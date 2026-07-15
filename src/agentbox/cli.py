from __future__ import annotations

import argparse
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

from agentbox.template import render_template

from .config import CONFIG_FILE, Config, default_toml, load_config
from .devcontainer import Devcontainer, load_devcontainer, shell_join
from . import gitops
from . import podman
from . import runs
from .drivers import Diagnostic, all_drivers, canonical_driver_id, get_driver


PULL_CHOICES = ("prompt", "branch", "ff-only", "later")
LOG_PREVIEW_LIMIT = 20


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


def register_driver_commands(
    subparsers: argparse._SubParsersAction, command_name: str, display_name: str
) -> None:
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
    shell.add_argument("--dirty", choices=["prompt", "include", "ignore", "abort"], default="prompt")
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
        containerfile = podman.harness_containerfile_path(config, driver.id)
        if containerfile.exists():
            print(f"{containerfile} already exists")
        else:
            podman.ensure_harness_containerfile(config, driver_id=driver.id)
            print(f"created {containerfile}")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    config, devcontainer = context(args)
    ok = True
    version = podman.podman_version()
    rootless = podman.podman_rootless()
    checks = [
        Diagnostic("repo", str(config.repo_root), "ok"),
        Diagnostic("podman", version or "not found", "ok" if version else "error"),
        Diagnostic("rootless", str(rootless), "ok" if rootless is True else "error"),
        Diagnostic("devcontainer", str(devcontainer.path) if devcontainer else "none", "ok"),
    ]
    for driver in all_drivers():
        checks.extend(
            driver.diagnostics(config.driver_settings(driver.id), dict(os.environ), config.repo_root)
        )
    for diagnostic in checks:
        ok = ok and diagnostic.severity != "error"
        mark = {"ok": "ok", "warning": "warn", "error": "fail"}[diagnostic.severity]
        message = f" ({diagnostic.message})" if diagnostic.message else ""
        print(f"{mark:4} {diagnostic.name}: {diagnostic.value}{message}")
    return 0 if ok else 1


def cmd_harness_build(args: argparse.Namespace) -> int:
    config, devcontainer = context(args)
    driver_id = selected_driver_id(args)
    podman.build_image(
        config, devcontainer, dry_run=args.dry_run, force=args.rebuild, driver_id=driver_id
    )
    return 0


def cmd_harness_images(args: argparse.Namespace) -> int:
    config, _ = context(args)
    driver_id = selected_driver_id(args)
    referenced = referenced_image_refs(config, driver_id)
    current = current_managed_image_or_none(config, driver_id)
    current_ref = podman.normalized_image_ref(current) if current else None
    images = podman.list_managed_images(config, driver_id)
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
    config, _ = context(args)
    driver_id = selected_driver_id(args)
    keep = referenced_image_refs(config, driver_id)
    current = current_managed_image_or_none(config, driver_id)
    if current:
        keep.add(podman.normalized_image_ref(current))
    removed = 0
    for image in podman.list_managed_images(config, driver_id):
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


def referenced_image_refs(config: Config, driver_id: str = "codex") -> set[str]:
    """Normalized managed image refs referenced by saved runs.

    Shared by image listing and pruning so the two commands cannot disagree on
    which images are still referenced. Full refs avoid cross-talk when different
    repositories share the same digest-like tag.
    """
    image_name = config.driver_settings(driver_id).image_name
    refs = set()
    for metadata in runs.list_runs(config.run_store):
        if metadata.driver != driver_id:
            continue
        ref = podman.normalized_image_ref(metadata.image)
        repo, _, _tag = ref.rpartition(":")
        if repo == image_name:
            refs.add(ref)
    return refs


def referenced_image_tags(config: Config, driver_id: str = "codex") -> set[str]:
    return {podman.image_tag(ref) for ref in referenced_image_refs(config, driver_id)}


def current_managed_image_or_none(config: Config, driver_id: str = "codex") -> str | None:
    if not podman.harness_containerfile_path(config, driver_id).exists():
        return None
    return podman.current_managed_image(config, driver_id=driver_id)


def cmd_harness_run(args: argparse.Namespace) -> int:
    config, devcontainer = context(args)
    driver_id = selected_driver_id(args)
    preflight = resolve_run_inputs(
        config,
        args.dirty,
        git_user_name=args.git_user_name,
        git_user_email=args.git_user_email,
    )
    image, managed_containerfile = resolve_run_image(config, args.image, args.dry_run, driver_id)
    _, metadata = prepare_run(
        config,
        devcontainer,
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
    argv = driver.launch_argv(workspace_path(config, devcontainer, driver_id), prompt)
    command = prelude(devcontainer) + "exec " + shlex.join(argv)
    status = run_container(
        config, devcontainer, metadata.image, Path(metadata.run_repo), command, args.dry_run, driver_id
    )
    if args.dry_run:
        return status
    pull_status = complete_run(config, metadata, args.pull, args.sign_imports)
    return status if status else pull_status


def cmd_harness_shell(args: argparse.Namespace) -> int:
    config, devcontainer = context(args)
    driver_id = selected_driver_id(args)
    should_complete = False
    if args.run_id:
        metadata = load_run(config, args.run_id)
        if metadata.driver != driver_id:
            raise RuntimeError(
                f"run {metadata.id} uses driver {metadata.driver}; use `agentbox runs enter {metadata.id}`"
            )
        ensure_saved_run_image(config, metadata, args.dry_run)
    else:
        preflight = resolve_run_inputs(
            config,
            args.dirty,
            git_user_name=args.git_user_name,
            git_user_email=args.git_user_email,
        )
        image, managed_containerfile = resolve_run_image(config, args.image, args.dry_run, driver_id)
        _, metadata = prepare_run(
            config,
            devcontainer,
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
    command = prelude(devcontainer) + "exec bash"
    status = run_container(
        config, devcontainer, metadata.image, Path(metadata.run_repo), command, args.dry_run, driver_id
    )
    if args.dry_run or not should_complete:
        return status
    pull_status = complete_run(config, metadata, args.pull, args.sign_imports)
    return status if status else pull_status


def cmd_runs_list(args: argparse.Namespace) -> int:
    config, _ = context(args)
    for metadata in runs.list_runs(config.run_store):
        print(
            f"{metadata.id}\t{metadata.driver}\t{metadata.base_branch}\t{metadata.created_at}\t{metadata.run_repo}"
        )
    return 0


def cmd_runs_enter(args: argparse.Namespace) -> int:
    config, devcontainer = context(args)
    metadata = load_run(config, args.run_id)
    ensure_saved_run_image(config, metadata, args.dry_run)
    command = prelude(devcontainer) + "exec bash"
    return run_container(
        config, devcontainer, metadata.image, Path(metadata.run_repo), command, args.dry_run, metadata.driver
    )


def cmd_runs_import(args: argparse.Namespace) -> int:
    config, _ = context(args)
    metadata = load_run(config, args.run_id)
    run_repo = Path(metadata.run_repo)
    branch = f"agentbox/{metadata.id}"

    commit_count = gitops.count_commits_since(run_repo, metadata.base_head)
    if commit_count == 0:
        if gitops.has_uncommitted_changes(run_repo):
            print(
                f"run {metadata.id} has uncommitted changes; use `agentbox runs enter {metadata.id}`"
            )
            return 2
        print(f"run {metadata.id} has no commits to import")
        return 0

    if gitops.branch_exists(config.repo_root, branch) and not args.force:
        print(f"branch {branch} already exists; use --force to replace it", file=sys.stderr)
        return 2

    sign_imports = resolve_sign_imports(config, args.sign_imports)
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
    config, _ = context(args)
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
            target = resolve_run_dir(config, run_id)
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


def context(args: argparse.Namespace) -> tuple[Config, Devcontainer | None]:
    root = repo_root(args)
    config = load_config(root)
    devcontainer = load_devcontainer(config.devcontainer)
    return config, devcontainer


def selected_driver_id(args: argparse.Namespace) -> str:
    return getattr(args, "driver_id", "codex")


def repo_root(args: argparse.Namespace) -> Path:
    if args.repo:
        return args.repo.resolve()
    result = gitops.run_git(["rev-parse", "--show-toplevel"], Path.cwd())
    return Path(result.stdout.strip()).resolve()


def prepare_run(
    config: Config,
    devcontainer: Devcontainer | None,
    dirty_mode: str,
    image: str,
    dry_run: bool = False,
    git_user_name: str | None = None,
    git_user_email: str | None = None,
    preflight: tuple[gitops.RepoState, bool, gitops.GitIdentity] | None = None,
    containerfile: Path | None = None,
    driver_id: str = "codex",
) -> tuple[Path, runs.RunMetadata]:
    if preflight is None:
        preflight = resolve_run_inputs(
            config,
            dirty_mode,
            git_user_name=git_user_name,
            git_user_email=git_user_email,
        )
    state, include_dirty, resolved_identity = preflight

    run_id = runs.new_run_id()
    run_dir = config.run_store / run_id
    run_repo = run_dir / "repo"
    if dry_run:
        metadata = runs.create_metadata(
            run_id, config.repo_root, run_repo, state.branch, state.head, image, driver=driver_id
        )
        return run_dir, metadata
    gitops.clone_repo(config.repo_root, run_repo, include_dirty=include_dirty)
    gitops.apply_git_identity(run_repo, resolved_identity)
    snapshot = snapshot_containerfile(run_dir, containerfile)
    metadata = runs.create_metadata(
        run_id,
        config.repo_root,
        run_repo,
        state.branch,
        state.head,
        image,
        driver=driver_id,
        containerfile=snapshot,
    )
    runs.write_metadata(run_dir, metadata)
    return run_dir, metadata


def snapshot_containerfile(run_dir: Path, containerfile: Path | None) -> str | None:
    """Copy the Containerfile used for a managed image into the run directory.

    This makes the run self-contained: the exact build recipe survives later
    edits to the shared harness Containerfile, so the run can be rebuilt and
    re-entered even after its content-addressed image tag has changed.
    """
    if containerfile is None or not containerfile.exists():
        return None
    run_dir.mkdir(parents=True, exist_ok=True)
    snapshot = run_dir / "Containerfile"
    snapshot.write_text(containerfile.read_text())
    return str(snapshot)


def resolve_run_inputs(
    config: Config,
    dirty_mode: str,
    git_user_name: str | None = None,
    git_user_email: str | None = None,
) -> tuple[gitops.RepoState, bool, gitops.GitIdentity]:
    state = gitops.repo_state(config.repo_root)
    include_dirty = False
    if state.dirty:
        include_dirty = resolve_dirty_mode(dirty_mode)
    resolved_identity = gitops.resolve_git_identity(
        config.repo_root,
        user_name=git_user_name if git_user_name is not None else config.git_user_name,
        user_email=git_user_email if git_user_email is not None else config.git_user_email,
    )
    if not resolved_identity.user_name or not resolved_identity.user_email:
        print(
            "agentbox: warning: git user.name/user.email is not set; commits inside the "
            "container may fail. Set [git] user_name/user_email in agentbox.toml or pass "
            "--git-user-name/--git-user-email.",
            file=sys.stderr,
        )
    return state, include_dirty, resolved_identity


def resolve_dirty_mode(mode: str) -> bool:
    if mode == "include":
        return True
    if mode == "ignore":
        return False
    if mode == "abort":
        raise RuntimeError("working tree is dirty")
    if not sys.stdin.isatty():
        raise RuntimeError("working tree is dirty; rerun with --dirty include or --dirty ignore")
    answer = input("Working tree is dirty. Include dirty file contents in the run clone? [y/N] ")
    return answer.lower() in {"y", "yes"}


def load_run(config: Config, run_id: str) -> runs.RunMetadata:
    run_dir = resolve_run_dir(config, run_id)
    if not run_dir.exists():
        raise RuntimeError(f"unknown run id: {run_id}")
    return runs.read_metadata(run_dir)


def resolve_run_dir(config: Config, run_id: str) -> Path:
    """Resolve a run id to its directory, rejecting ids that escape the store."""
    run_store = config.run_store.resolve()
    candidate = (config.run_store / run_id).resolve()
    if candidate.parent != run_store:
        raise RuntimeError(f"invalid run id: {run_id}")
    return candidate


def resolve_run_image(
    config: Config, image_override: str | None, dry_run: bool, driver_id: str = "codex"
) -> tuple[str, Path | None]:
    """Return the image to run and, for managed images, its Containerfile.

    An explicit --image override is used verbatim with no snapshot, since its
    build recipe is not owned by agentbox.
    """
    if image_override:
        return image_override, None
    image = podman.ensure_managed_image(config, dry_run=dry_run, driver_id=driver_id)
    return image, podman.harness_containerfile_path(config, driver_id)


def ensure_saved_run_image(config: Config, metadata: runs.RunMetadata, dry_run: bool) -> None:
    image = metadata.image
    snapshot = Path(metadata.containerfile) if metadata.containerfile else None
    if dry_run:
        print(shlex.join(["podman", "image", "exists", image]))
        if snapshot:
            print(shlex.join(podman.managed_build_command(config, image, snapshot, driver_id=metadata.driver)))
        return
    if podman.image_exists(image):
        return
    if snapshot and snapshot.exists():
        podman.build_tagged_image(config, snapshot, image, driver_id=metadata.driver)
        return
    raise RuntimeError(
        f"image {image} for run {metadata.id} is missing and has no Containerfile "
        f"snapshot to rebuild from; rebuild it manually or rerun with --image"
    )


def complete_run(
    config: Config,
    metadata: runs.RunMetadata,
    pull_mode: str,
    sign_imports_override: bool | None = None,
) -> int:
    run_repo = Path(metadata.run_repo)
    branch = f"agentbox/{metadata.id}"
    target_head = gitops.fetch_head(config.repo_root, run_repo)
    state = gitops.repo_state(config.repo_root)
    run_only_count = gitops.count_commits_between(config.repo_root, "HEAD", target_head)
    has_uncommitted = gitops.has_uncommitted_changes(run_repo)

    if run_only_count == 0:
        if has_uncommitted:
            print(
                f"run {metadata.id} has uncommitted changes; use `agentbox runs enter {metadata.id}`"
            )
        else:
            print(f"run {metadata.id} has no commits to pull")
        return 0

    print(f"Run {metadata.id} finished with {run_only_count} commit(s).")
    print()
    print_commit_preview(config.repo_root, state.branch, target_head)
    if has_uncommitted:
        print()
        print(
            f"run {metadata.id} also has uncommitted changes; use `agentbox runs enter {metadata.id}`"
        )

    fast_forward = gitops.check_fast_forward(config.repo_root, metadata.base_branch, target_head)
    action = resolve_pull_mode(pull_mode, config, metadata, branch, fast_forward, target_head)
    sign_imports = resolve_sign_imports(config, sign_imports_override)
    if action == "later":
        print_later_message(metadata, run_only_count)
        return 0
    if action == "branch":
        if gitops.branch_exists(config.repo_root, branch):
            print(
                f"branch {branch} already exists; use `agentbox runs import {metadata.id} --force`",
                file=sys.stderr,
            )
            return 2
        if sign_imports:
            gitops.import_branch_signed(
                config.repo_root,
                run_repo,
                metadata.base_head,
                branch,
                force=False,
            )
            print(f"imported {run_only_count} signed commit(s) to local branch {branch}")
        else:
            gitops.import_branch(config.repo_root, run_repo, branch, force=False)
            print(f"imported {run_only_count} commit(s) to local branch {branch}")
        return 0
    if action == "ff-only":
        if sign_imports:
            print(
                "signed import rewrites commits; use --pull branch or --no-sign-imports",
                file=sys.stderr,
            )
            return 2
        if not fast_forward.ok:
            print(f"fast-forward unavailable: {fast_forward.reason}", file=sys.stderr)
            return 2
        gitops.fast_forward(config.repo_root, target_head)
        print(f"fast-forwarded {fast_forward.current_branch} to {target_head[:7]}")
        return 0
    raise RuntimeError(f"unknown pull mode: {action}")


def add_sign_import_args(parser: argparse.ArgumentParser) -> None:
    parser.set_defaults(sign_imports=None)
    parser.add_argument("--sign-imports", dest="sign_imports", action="store_true")
    parser.add_argument("--no-sign-imports", dest="sign_imports", action="store_false")


def resolve_sign_imports(config: Config, override: bool | None) -> bool:
    if override is not None:
        return override
    return config.sign_imports


def print_commit_preview(repo: Path, branch: str, target: str) -> None:
    run_only_count = gitops.count_commits_between(repo, "HEAD", target)
    print(f"Commits in run not on {branch}:")
    for line in gitops.one_line_log(repo, "HEAD", target, limit=LOG_PREVIEW_LIMIT):
        print(f"  {line}")
    if run_only_count > LOG_PREVIEW_LIMIT:
        remaining = run_only_count - LOG_PREVIEW_LIMIT
        print(f"  ... {remaining} more commit(s)")

    host_only_count = gitops.count_commits_between(repo, target, "HEAD")
    if host_only_count == 0:
        return
    print()
    print(f"Commits on {branch} not in run:")
    for line in gitops.one_line_log(repo, target, "HEAD", limit=LOG_PREVIEW_LIMIT):
        print(f"  {line}")
    if host_only_count > LOG_PREVIEW_LIMIT:
        remaining = host_only_count - LOG_PREVIEW_LIMIT
        print(f"  ... {remaining} more commit(s)")


def resolve_pull_mode(
    pull_mode: str,
    config: Config,
    metadata: runs.RunMetadata,
    branch: str,
    fast_forward: gitops.FastForwardCheck,
    target_head: str,
) -> str:
    if pull_mode != "prompt":
        return pull_mode
    if not sys.stdin.isatty():
        return "later"

    print()
    print(f"Pull changes back to {config.repo_root}?")
    print(f"  [b] Import to branch {branch}")
    if fast_forward.ok:
        print(f"  [f] Fast-forward {fast_forward.current_branch} to {target_head[:7]}")
    else:
        print(f"  [f] Fast-forward {metadata.base_branch} unavailable: {fast_forward.reason}")
    print("  [l] Leave in run for later review (default)")
    print()

    while True:
        answer = input("Choice [b/f/l]: ").strip().lower()
        if answer in {"", "l", "later"}:
            return "later"
        if answer in {"b", "branch"}:
            return "branch"
        if answer in {"f", "ff", "ff-only"}:
            if fast_forward.ok:
                return "ff-only"
            print(f"fast-forward unavailable: {fast_forward.reason}")
            continue
        print("choose b, f, or l")


def print_later_message(metadata: runs.RunMetadata, commit_count: int) -> None:
    print()
    print(f"Run {metadata.id} has {commit_count} commit(s) left for later review.")
    print(f"Review:  agentbox runs enter {metadata.id}")
    print(f"Import:  agentbox runs import {metadata.id}")


def run_container(
    config: Config,
    devcontainer: Devcontainer | None,
    image: str,
    run_repo: Path,
    command: str,
    dry_run: bool,
    driver_id: str = "codex",
) -> int:
    host_env = dict(os.environ)
    driver = get_driver(driver_id)
    settings = config.driver_settings(driver.id)
    for warning in driver.runtime_warnings(settings, host_env, config.repo_root):
        print(warning, file=sys.stderr)
    args = podman.render_run_command(
        config=config,
        devcontainer=devcontainer,
        image=image,
        run_repo=run_repo,
        command=command,
        driver_id=driver_id,
        host_env=host_env,
    )
    if dry_run:
        print(shlex.join(args))
        return 0
    podman.ensure_state_mounts(
        config,
        driver_id,
        host_env,
        run_repo,
        workspace_path(config, devcontainer, driver_id),
    )
    return subprocess.run(args).returncode


def workspace_path(config: Config, devcontainer: Devcontainer | None, driver_id: str = "codex") -> str:
    return (
        devcontainer.workspace_folder if devcontainer and devcontainer.workspace_folder else None
    ) or (config.driver_settings(driver_id).workspace_folder)


cmd_codex_build = cmd_harness_build
cmd_codex_images = cmd_harness_images
cmd_codex_prune = cmd_harness_prune
cmd_codex_run = cmd_harness_run
cmd_codex_shell = cmd_harness_shell


def prelude(devcontainer: Devcontainer | None) -> str:
    if not devcontainer:
        return ""
    commands = shell_join([*devcontainer.post_create, *devcontainer.post_start])
    if not commands:
        return ""
    return f"set -e; {commands}; "


if __name__ == "__main__":
    raise SystemExit(main())
