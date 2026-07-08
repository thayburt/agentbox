from __future__ import annotations

import argparse
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

from .config import CONFIG_FILE, Config, default_toml, load_config
from .devcontainer import Devcontainer, load_devcontainer, shell_join
from . import gitops
from . import podman
from . import runs


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

    codex = sub.add_parser("codex", help="Codex container commands")
    codex_sub = codex.add_subparsers(required=True)
    codex_build = codex_sub.add_parser("build", help="Build the Codex harness image")
    codex_build.add_argument("--dry-run", action="store_true")
    codex_build.set_defaults(func=cmd_codex_build)

    codex_run = codex_sub.add_parser("run", help="Run interactive Codex in an isolated clone")
    codex_run.add_argument("prompt", nargs=argparse.REMAINDER)
    codex_run.add_argument("--dry-run", action="store_true")
    codex_run.add_argument(
        "--dirty", choices=["prompt", "include", "ignore", "abort"], default="prompt"
    )
    codex_run.add_argument("--pull", choices=PULL_CHOICES, default="prompt")
    codex_run.add_argument("--git-user-name", default=None)
    codex_run.add_argument("--git-user-email", default=None)
    add_sign_import_args(codex_run)
    codex_run.set_defaults(func=cmd_codex_run)

    codex_shell = codex_sub.add_parser("shell", help="Open a shell in an isolated run")
    codex_shell.add_argument("--run", dest="run_id")
    codex_shell.add_argument("--dry-run", action="store_true")
    codex_shell.add_argument(
        "--dirty", choices=["prompt", "include", "ignore", "abort"], default="prompt"
    )
    codex_shell.add_argument("--pull", choices=PULL_CHOICES, default="prompt")
    codex_shell.add_argument("--git-user-name", default=None)
    codex_shell.add_argument("--git-user-email", default=None)
    add_sign_import_args(codex_shell)
    codex_shell.set_defaults(func=cmd_codex_shell)

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


def cmd_init(args: argparse.Namespace) -> int:
    root = repo_root(args)
    path = root / CONFIG_FILE
    if path.exists():
        print(f"{path} already exists")
        return 0
    path.write_text(default_toml())
    print(f"created {path}")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    config, devcontainer = context(args)
    ok = True
    version = podman.podman_version()
    rootless = podman.podman_rootless()
    checks = [
        ("repo", str(config.repo_root), True),
        ("podman", version or "not found", bool(version)),
        ("rootless", str(rootless), rootless is True),
        ("codex_home", str(config.codex_home), config.codex_home.exists()),
        ("devcontainer", str(devcontainer.path) if devcontainer else "none", True),
    ]
    for name, value, passed in checks:
        ok = ok and passed
        mark = "ok" if passed else "fail"
        print(f"{mark:4} {name}: {value}")
    return 0 if ok else 1


def cmd_codex_build(args: argparse.Namespace) -> int:
    config, devcontainer = context(args)
    podman.build_image(config, devcontainer, dry_run=args.dry_run)
    return 0


def cmd_codex_run(args: argparse.Namespace) -> int:
    config, devcontainer = context(args)
    _, metadata = prepare_run(
        config,
        devcontainer,
        args.dirty,
        dry_run=args.dry_run,
        git_user_name=args.git_user_name,
        git_user_email=args.git_user_email,
    )
    prompt = " ".join(args.prompt).strip()
    codex_args = [
        "codex",
        "--cd",
        shlex.quote(workspace_path(config, devcontainer)),
        "--sandbox",
        "danger-full-access",
        "--ask-for-approval",
        "never",
    ]
    if prompt:
        codex_args.append(shlex.quote(prompt))
    command = prelude(devcontainer) + "exec " + " ".join(codex_args)
    status = run_container(
        config, devcontainer, metadata.image, Path(metadata.run_repo), command, args.dry_run
    )
    if args.dry_run:
        return status
    pull_status = complete_run(config, metadata, args.pull, args.sign_imports)
    return status if status else pull_status


def cmd_codex_shell(args: argparse.Namespace) -> int:
    config, devcontainer = context(args)
    should_complete = False
    if args.run_id:
        metadata = load_run(config, args.run_id)
    else:
        _, metadata = prepare_run(
            config,
            devcontainer,
            args.dirty,
            dry_run=args.dry_run,
            git_user_name=args.git_user_name,
            git_user_email=args.git_user_email,
        )
        should_complete = True
    command = prelude(devcontainer) + "exec bash"
    status = run_container(
        config, devcontainer, metadata.image, Path(metadata.run_repo), command, args.dry_run
    )
    if args.dry_run or not should_complete:
        return status
    pull_status = complete_run(config, metadata, args.pull, args.sign_imports)
    return status if status else pull_status


def cmd_runs_list(args: argparse.Namespace) -> int:
    config, _ = context(args)
    for metadata in runs.list_runs(config.run_store):
        print(f"{metadata.id}\t{metadata.base_branch}\t{metadata.created_at}\t{metadata.run_repo}")
    return 0


def cmd_runs_enter(args: argparse.Namespace) -> int:
    config, devcontainer = context(args)
    metadata = load_run(config, args.run_id)
    command = prelude(devcontainer) + "exec bash"
    return run_container(
        config, devcontainer, metadata.image, Path(metadata.run_repo), command, args.dry_run
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
    targets = [config.run_store / run_id for run_id in args.run_id]
    if args.all:
        targets = [config.run_store / item.id for item in runs.list_runs(config.run_store)]
    for target in targets:
        if target.exists():
            shutil.rmtree(target)
            print(f"deleted {target}")
    return 0


def context(args: argparse.Namespace) -> tuple[Config, Devcontainer | None]:
    root = repo_root(args)
    config = load_config(root)
    devcontainer = load_devcontainer(config.devcontainer)
    return config, devcontainer


def repo_root(args: argparse.Namespace) -> Path:
    if args.repo:
        return args.repo.resolve()
    result = gitops.run_git(["rev-parse", "--show-toplevel"], Path.cwd())
    return Path(result.stdout.strip()).resolve()


def prepare_run(
    config: Config,
    devcontainer: Devcontainer | None,
    dirty_mode: str,
    dry_run: bool = False,
    git_user_name: str | None = None,
    git_user_email: str | None = None,
) -> tuple[Path, runs.RunMetadata]:
    state = gitops.repo_state(config.repo_root)
    include_dirty = False
    if state.dirty:
        include_dirty = resolve_dirty_mode(dirty_mode)
    resolved_identity = gitops.resolve_git_identity(
        config.repo_root,
        user_name=git_user_name if git_user_name is not None else config.git_user_name,
        user_email=git_user_email if git_user_email is not None else config.git_user_email,
    )

    image = podman.harness_image_name(config, devcontainer)
    run_id = runs.new_run_id()
    run_dir = config.run_store / run_id
    run_repo = run_dir / "repo"
    if dry_run:
        metadata = runs.create_metadata(
            run_id, config.repo_root, run_repo, state.branch, state.head, image
        )
        return run_dir, metadata
    gitops.clone_repo(config.repo_root, run_repo, include_dirty=include_dirty)
    gitops.apply_git_identity(run_repo, resolved_identity)
    metadata = runs.create_metadata(
        run_id, config.repo_root, run_repo, state.branch, state.head, image
    )
    runs.write_metadata(run_dir, metadata)
    return run_dir, metadata


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
    run_dir = config.run_store / run_id
    if not run_dir.exists():
        raise RuntimeError(f"unknown run id: {run_id}")
    return runs.read_metadata(run_dir)


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
    run_only_count = gitops.count_commits_between(config.repo_root, "HEAD", "FETCH_HEAD")
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
    print_commit_preview(config.repo_root, state.branch)
    if has_uncommitted:
        print()
        print(
            f"run {metadata.id} also has uncommitted changes; use `agentbox runs enter {metadata.id}`"
        )

    fast_forward = gitops.check_fast_forward(config.repo_root, metadata.base_branch, "FETCH_HEAD")
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
        gitops.fast_forward(config.repo_root, "FETCH_HEAD")
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


def print_commit_preview(repo: Path, branch: str) -> None:
    run_only_count = gitops.count_commits_between(repo, "HEAD", "FETCH_HEAD")
    print(f"Commits in run not on {branch}:")
    for line in gitops.one_line_log(repo, "HEAD", "FETCH_HEAD", limit=LOG_PREVIEW_LIMIT):
        print(f"  {line}")
    if run_only_count > LOG_PREVIEW_LIMIT:
        remaining = run_only_count - LOG_PREVIEW_LIMIT
        print(f"  ... {remaining} more commit(s)")

    host_only_count = gitops.count_commits_between(repo, "FETCH_HEAD", "HEAD")
    if host_only_count == 0:
        return
    print()
    print(f"Commits on {branch} not in run:")
    for line in gitops.one_line_log(repo, "FETCH_HEAD", "HEAD", limit=LOG_PREVIEW_LIMIT):
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
) -> int:
    args = podman.render_run_command(
        config=config,
        devcontainer=devcontainer,
        image=image,
        run_repo=run_repo,
        command=command,
    )
    if dry_run:
        print(shlex.join(args))
        return 0
    config.codex_home.mkdir(parents=True, exist_ok=True)
    return subprocess.run(args).returncode


def workspace_path(config: Config, devcontainer: Devcontainer | None) -> str:
    return (
        devcontainer.workspace_folder if devcontainer and devcontainer.workspace_folder else None
    ) or (config.workspace_folder)


def prelude(devcontainer: Devcontainer | None) -> str:
    if not devcontainer:
        return ""
    commands = shell_join([*devcontainer.post_create, *devcontainer.post_start])
    if not commands:
        return ""
    return f"set -e; {commands}; "


if __name__ == "__main__":
    raise SystemExit(main())
