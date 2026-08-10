from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path

from . import gitops, podman, runs
from .config import Config
from .drivers import get_driver
from .seed import seed_run_files, snapshot_containerfile

LOG_PREVIEW_LIMIT = 20


def referenced_image_refs(config: Config, *, driver_id: str) -> set[str]:
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


def current_managed_image_or_none(config: Config, *, driver_id: str) -> str | None:
    if not podman.harness_containerfile_path(config, driver_id=driver_id).exists():
        return None
    return podman.current_managed_image(config, driver_id=driver_id)


def prepare_run(
    config: Config,
    dirty_mode: str,
    image: str,
    *,
    dry_run: bool = False,
    git_user_name: str | None = None,
    git_user_email: str | None = None,
    preflight: tuple[gitops.RepoState, bool, gitops.GitIdentity] | None = None,
    containerfile: Path | None = None,
    driver_id: str,
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
    seed_run_files(config, driver_id, run_dir)
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
    config: Config, image_override: str | None, dry_run: bool, *, driver_id: str
) -> tuple[str, Path | None]:
    """Return the image to run and, for managed images, its Containerfile.

    An explicit --image override is used verbatim with no snapshot, since its
    build recipe is not owned by agentbox.
    """
    if image_override:
        return image_override, None
    image = podman.ensure_managed_image(config, dry_run=dry_run, driver_id=driver_id)
    return image, podman.harness_containerfile_path(config, driver_id=driver_id)


def ensure_saved_run_image(config: Config, metadata: runs.RunMetadata, dry_run: bool) -> None:
    image = metadata.image
    snapshot = Path(metadata.containerfile) if metadata.containerfile else None
    if dry_run:
        print(shlex.join(["podman", "image", "exists", image]))
        if snapshot:
            print(
                shlex.join(
                    podman.managed_build_command(config, image, snapshot, driver_id=metadata.driver)
                )
            )
        return
    if podman.image_exists(image):
        return
    if snapshot and snapshot.exists():
        podman.build_tagged_image(config, snapshot, image, driver_id=metadata.driver)
        return
    raise RuntimeError(
        f"image {image} for run {metadata.id} is missing and has no Containerfile "
        "snapshot to rebuild from; rebuild it manually or enter with "
        f"`agentbox runs enter {metadata.id} --image <image>`"
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
                f"run {metadata.id} has uncommitted changes; use "
                f"`agentbox runs enter {metadata.id}`"
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
            f"run {metadata.id} also has uncommitted changes; use "
            f"`agentbox runs enter {metadata.id}`"
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
    image: str,
    run_repo: Path,
    command: str,
    dry_run: bool,
    *,
    driver_id: str,
) -> int:
    host_env = dict(os.environ)
    driver = get_driver(driver_id)
    settings = config.driver_settings(driver.id)
    for warning in driver.runtime_warnings(settings, host_env, config.repo_root):
        print(warning, file=sys.stderr)
    args = podman.render_run_command(
        config=config,
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
        settings.workspace_folder,
    )
    return subprocess.run(args, check=False).returncode
