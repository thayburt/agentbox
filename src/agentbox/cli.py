from __future__ import annotations

import argparse
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

from .template import render_template

from .config import CONFIG_FILE, Config, default_toml, load_config
from . import gitops
from . import lifecycle
from . import podman
from . import runs
from .drivers import Diagnostic, all_drivers, canonical_driver_id, get_driver


PULL_CHOICES = ("prompt", "branch", "ff-only", "later")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130
    except subprocess.CalledProcessError as exc:
        if exc.stderr:
            print(exc.stderr, file=sys.stderr, end="")
        return exc.returncode
    except Exception as exc:
        print(f"agentbox: {exc}", file=sys.stderr)
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agentbox")
    parser.add_argument(
        "--repo", type=Path, default=None, help="Repository root, default: git root"
    )
    sub = parser.add_subparsers(required=True)

    init = sub.add_parser("init", help="Create agentbox.toml")
    init.set_defaults(func=cmd_init)

    doctor = sub.add_parser("doctor", help="Check host prerequisites")
    doctor.set_defaults(func=cmd_doctor)

    for driver in all_drivers():
        register_driver_commands(sub, driver.id, driver.display_name)
        for alias in driver.aliases:
            register_driver_commands(sub, alias, driver.display_name)

    runs_parser = sub.add_parser("runs", help="Manage saved run directories")
    runs_sub = runs_parser.add_subparsers(required=True)
    runs_list = runs_sub.add_parser("list", help="List runs")
    runs_list.set_defaults(func=cmd_runs_list)
    runs_enter = runs_sub.add_parser("enter", help="Open a shell in a saved run")
    runs_enter.add_argument("run_id")
    runs_enter.add_argument("--dry-run", action="store_true")
    runs_enter.add_argument("--image", default=None)
    runs_enter.set_defaults(func=cmd_runs_enter)
    runs_import = runs_sub.add_parser("import", help="Import run commits as a local branch")
    runs_import.add_argument("run_id")
    runs_import.add_argument("--force", action="store_true")
    add_sign_import_args(runs_import)
    runs_import.set_defaults(func=cmd_runs_import)
    runs_prune = runs_sub.add_parser("prune", help="Delete saved run directories")
    runs_prune.add_argument("run_id", nargs="*")
    runs_prune.add_argument("--all", action="store_true")
    runs_prune.set_defaults(func=cmd_runs_prune)

    return parser


def register_driver_commands(subparsers, command_name: str, display_name: str) -> None:
    driver_id = canonical_driver_id(command_name)
    parser = subparsers.add_parser(command_name, help=f"{display_name} container commands")
    harness_sub = parser.add_subparsers(required=True)

    build = harness_sub.add_parser("build", help=f"Build the {display_name} harness image")
    build.add_argument("--dry-run", action="store_true")
    build.add_argument(
        "--rebuild",
        action="store_true",
        help="Rebuild even if the image exists, refreshing the base image",
    )
    build.set_defaults(func=cmd_harness_build, driver_id=driver_id)

    images = harness_sub.add_parser("images", help="List managed harness images")
    images.set_defaults(func=cmd_harness_images, driver_id=driver_id)

    prune = harness_sub.add_parser(
        "prune", help="Remove managed harness images not referenced by any run"
    )
    prune.add_argument("--dry-run", action="store_true")
    prune.set_defaults(func=cmd_harness_prune, driver_id=driver_id)

    run = harness_sub.add_parser("run", help=f"Run interactive {display_name} in an isolated clone")
    run.add_argument("prompt", nargs=argparse.REMAINDER)
    run.add_argument("--dry-run", action="store_true")
    run.add_argument("--dirty", choices=["prompt", "include", "ignore", "abort"], default="prompt")
    run.add_argument("--pull", choices=PULL_CHOICES, default="prompt")
    run.add_argument("--image", default=None)
    run.add_argument("--git-user-name", default=None)
    run.add_argument("--git-user-email", default=None)
    add_sign_import_args(run)
    run.set_defaults(func=cmd_harness_run, driver_id=driver_id)

    shell = harness_sub.add_parser("shell", help="Open a shell in an isolated run")
    shell.add_argument("--run", dest="run_id")
    shell.add_argument("--dry-run", action="store_true")
    shell.add_argument(
        "--dirty", choices=["prompt", "include", "ignore", "abort"], default="prompt"
    )
    shell.add_argument("--pull", choices=PULL_CHOICES, default="prompt")
    shell.add_argument("--image", default=None)
    shell.add_argument("--git-user-name", default=None)
    shell.add_argument("--git-user-email", default=None)
    add_sign_import_args(shell)
    shell.set_defaults(func=cmd_harness_shell, driver_id=driver_id)


def cmd_init(args: argparse.Namespace) -> int:
    root = repo_root(args)
    path = root / CONFIG_FILE
    if path.exists():
        print(f"{path} already exists")
    else:
        path.write_text(default_toml())
        print(f"created {path}")
    agentbox_dir = root / ".agentbox"
    agentbox_dir.mkdir(exist_ok=True)
    gitignore_path = agentbox_dir / ".gitignore"
    if gitignore_path.exists():
        print(f"{gitignore_path} already exists")
    else:
        gitignore_path.write_text(render_template("gitignore", {}))
        print(f"created {gitignore_path}")
    config = load_config(root)
    for driver in all_drivers():
        settings = config.driver_settings(driver.id)
        for init_file in driver.init_files(settings):
            path = root / init_file.relative_path
            if path.exists():
                print(f"{path} already exists")
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(init_file.contents)
                print(f"created {path}")
        containerfile = podman.harness_containerfile_path(config, driver_id=driver.id)
        if containerfile.exists():
            print(f"{containerfile} already exists")
        else:
            podman.ensure_harness_containerfile(config, driver_id=driver.id)
            print(f"created {containerfile}")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    config = context(args)
    ok = True
    version = podman.podman_version()
    rootless = podman.podman_rootless()
    checks = [
        Diagnostic("repo", str(config.repo_root), "ok"),
        Diagnostic("podman", version or "not found", "ok" if version else "error"),
        Diagnostic("rootless", str(rootless), "ok" if rootless is True else "error"),
    ]
    for driver in all_drivers():
        checks.extend(
            driver.diagnostics(
                config.driver_settings(driver.id), dict(os.environ), config.repo_root
            )
        )
    for diagnostic in checks:
        ok = ok and diagnostic.severity != "error"
        mark = {"ok": "ok", "warning": "warn", "error": "fail"}[diagnostic.severity]
        message = f" ({diagnostic.message})" if diagnostic.message else ""
        print(f"{mark:4} {diagnostic.name}: {diagnostic.value}{message}")
    return 0 if ok else 1


def cmd_harness_build(args: argparse.Namespace) -> int:
    config = context(args)
    driver_id = selected_driver_id(args)
    podman.build_image(config, dry_run=args.dry_run, force=args.rebuild, driver_id=driver_id)
    return 0


def cmd_harness_images(args: argparse.Namespace) -> int:
    config = context(args)
    driver_id = selected_driver_id(args)
    referenced = lifecycle.referenced_image_refs(config, driver_id=driver_id)
    current = lifecycle.current_managed_image_or_none(config, driver_id=driver_id)
    current_ref = podman.normalized_image_ref(current) if current else None
    images = podman.list_managed_images(config, driver_id=driver_id)
    if not images:
        print("no managed images")
        return 0
    for image in images:
        image_ref = podman.normalized_image_ref(image)
        labels = []
        if image_ref == current_ref:
            labels.append("current")
        if image_ref in referenced:
            labels.append("referenced")
        suffix = f"  [{', '.join(labels)}]" if labels else ""
        print(f"{image}{suffix}")
    return 0


def cmd_harness_prune(args: argparse.Namespace) -> int:
    config = context(args)
    driver_id = selected_driver_id(args)
    keep = lifecycle.referenced_image_refs(config, driver_id=driver_id)
    current = lifecycle.current_managed_image_or_none(config, driver_id=driver_id)
    if current:
        keep.add(podman.normalized_image_ref(current))
    removed = 0
    for image in podman.list_managed_images(config, driver_id=driver_id):
        if podman.normalized_image_ref(image) in keep:
            continue
        if args.dry_run:
            print(shlex.join(["podman", "rmi", image]))
        else:
            podman.remove_image(image)
            print(f"removed {image}")
        removed += 1
    if removed == 0:
        print("no unreferenced managed images to prune")
    return 0


def cmd_harness_run(args: argparse.Namespace) -> int:
    config = context(args)
    driver_id = selected_driver_id(args)
    preflight = lifecycle.resolve_run_inputs(
        config,
        args.dirty,
        git_user_name=args.git_user_name,
        git_user_email=args.git_user_email,
    )
    image, managed_containerfile = lifecycle.resolve_run_image(
        config, args.image, args.dry_run, driver_id=driver_id
    )
    _, metadata = lifecycle.prepare_run(
        config,
        args.dirty,
        image,
        dry_run=args.dry_run,
        git_user_name=args.git_user_name,
        git_user_email=args.git_user_email,
        preflight=preflight,
        containerfile=managed_containerfile,
        driver_id=driver_id,
    )
    prompt = " ".join(args.prompt).strip()
    driver = get_driver(driver_id)
    argv = driver.launch_argv(config.driver_settings(driver_id).workspace_folder, prompt)
    command = "exec " + shlex.join(argv)
    status = lifecycle.run_container(
        config,
        metadata.image,
        Path(metadata.run_repo),
        command,
        args.dry_run,
        driver_id=driver_id,
    )
    if args.dry_run:
        return status
    # Pull handling intentionally runs after a non-zero harness exit so
    # non-interactive pull modes can still import work from a failed run.
    pull_status = lifecycle.complete_run(config, metadata, args.pull, args.sign_imports)
    return status if status else pull_status


def cmd_harness_shell(args: argparse.Namespace) -> int:
    config = context(args)
    driver_id = selected_driver_id(args)
    should_complete = False
    if args.run_id:
        metadata = lifecycle.load_run(config, args.run_id)
        if metadata.driver != driver_id:
            raise RuntimeError(
                f"run {metadata.id} uses driver {metadata.driver}; use "
                f"`agentbox runs enter {metadata.id}`"
            )
        image = args.image or metadata.image
        if args.image is None:
            lifecycle.ensure_saved_run_image(config, metadata, args.dry_run)
    else:
        preflight = lifecycle.resolve_run_inputs(
            config,
            args.dirty,
            git_user_name=args.git_user_name,
            git_user_email=args.git_user_email,
        )
        image, managed_containerfile = lifecycle.resolve_run_image(
            config, args.image, args.dry_run, driver_id=driver_id
        )
        _, metadata = lifecycle.prepare_run(
            config,
            args.dirty,
            image,
            dry_run=args.dry_run,
            git_user_name=args.git_user_name,
            git_user_email=args.git_user_email,
            preflight=preflight,
            containerfile=managed_containerfile,
            driver_id=driver_id,
        )
        should_complete = True
    command = "exec bash"
    status = lifecycle.run_container(
        config,
        image,
        Path(metadata.run_repo),
        command,
        args.dry_run,
        driver_id=driver_id,
    )
    if args.dry_run or not should_complete:
        return status
    # Pull handling intentionally runs after a non-zero harness exit so
    # non-interactive pull modes can still import work from a failed run.
    pull_status = lifecycle.complete_run(config, metadata, args.pull, args.sign_imports)
    return status if status else pull_status


def cmd_runs_list(args: argparse.Namespace) -> int:
    config = context(args)
    for metadata in runs.list_runs(config.run_store):
        print(
            f"{metadata.id}\t{metadata.driver}\t{metadata.base_branch}\t{metadata.created_at}\t{metadata.run_repo}"
        )
    return 0


def cmd_runs_enter(args: argparse.Namespace) -> int:
    config = context(args)
    metadata = lifecycle.load_run(config, args.run_id)
    image = args.image or metadata.image
    if args.image is None:
        lifecycle.ensure_saved_run_image(config, metadata, args.dry_run)
    command = "exec bash"
    return lifecycle.run_container(
        config,
        image,
        Path(metadata.run_repo),
        command,
        args.dry_run,
        driver_id=metadata.driver,
    )


def cmd_runs_import(args: argparse.Namespace) -> int:
    config = context(args)
    metadata = lifecycle.load_run(config, args.run_id)
    run_repo = Path(metadata.run_repo)
    branch = f"agentbox/{metadata.id}"

    commit_count = gitops.count_commits_since(run_repo, metadata.base_head)
    if commit_count == 0:
        if gitops.has_uncommitted_changes(run_repo):
            print(
                f"run {metadata.id} has uncommitted changes; use "
                f"`agentbox runs enter {metadata.id}`"
            )
            return 2
        print(f"run {metadata.id} has no commits to import")
        return 0

    if gitops.branch_exists(config.repo_root, branch) and not args.force:
        print(f"branch {branch} already exists; use --force to replace it", file=sys.stderr)
        return 2

    sign_imports = lifecycle.resolve_sign_imports(config, args.sign_imports)
    if sign_imports:
        gitops.import_branch_signed(
            config.repo_root,
            run_repo,
            metadata.base_head,
            branch,
            force=args.force,
        )
        print(f"imported {commit_count} signed commit(s) to local branch {branch}")
    else:
        gitops.import_branch(config.repo_root, run_repo, branch, force=args.force)
        print(f"imported {commit_count} commit(s) to local branch {branch}")
    return 0


def cmd_runs_prune(args: argparse.Namespace) -> int:
    config = context(args)
    if not args.all and not args.run_id:
        print("provide run ids or --all", file=sys.stderr)
        return 2
    if args.all:
        run_ids = [item.id for item in runs.list_runs(config.run_store)]
    else:
        run_ids = args.run_id
    status = 0
    for run_id in run_ids:
        try:
            target = lifecycle.resolve_run_dir(config, run_id)
        except RuntimeError as exc:
            print(f"agentbox: {exc}", file=sys.stderr)
            status = 2
            continue
        if target.exists():
            shutil.rmtree(target)
            print(f"deleted {target}")
        else:
            print(f"no such run: {run_id}", file=sys.stderr)
            status = 2
    return status


def context(args: argparse.Namespace) -> Config:
    return load_config(repo_root(args))


def selected_driver_id(args: argparse.Namespace) -> str:
    return getattr(args, "driver_id", "codex")


def repo_root(args: argparse.Namespace) -> Path:
    if args.repo:
        return args.repo.resolve()
    result = gitops.run_git(["rev-parse", "--show-toplevel"], Path.cwd())
    return Path(result.stdout.strip()).resolve()


def add_sign_import_args(parser: argparse.ArgumentParser) -> None:
    parser.set_defaults(sign_imports=None)
    parser.add_argument("--sign-imports", dest="sign_imports", action="store_true")
    parser.add_argument("--no-sign-imports", dest="sign_imports", action="store_false")


if __name__ == "__main__":
    raise SystemExit(main())
