from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import shutil
import subprocess
import tempfile


@dataclass(frozen=True)
class RepoState:
    root: Path
    branch: str
    head: str
    dirty: bool


@dataclass(frozen=True)
class FastForwardCheck:
    ok: bool
    reason: str | None
    current_branch: str
    current_head: str
    target_head: str


@dataclass(frozen=True)
class GitIdentity:
    user_name: str | None = None
    user_email: str | None = None


def run_git(args: list[str], cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=check,
    )


def repo_state(repo_root: Path) -> RepoState:
    branch = run_git(["rev-parse", "--abbrev-ref", "HEAD"], repo_root).stdout.strip()
    head = run_git(["rev-parse", "--verify", "HEAD"], repo_root).stdout.strip()
    dirty = bool(run_git(["status", "--porcelain"], repo_root).stdout.strip())
    return RepoState(root=repo_root, branch=branch, head=head, dirty=dirty)


def clone_repo(source: Path, dest: Path, include_dirty: bool) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    run_git(["clone", "--no-hardlinks", str(source), str(dest)], source)
    if include_dirty:
        copy_dirty_paths(source, dest)


def read_git_identity(repo: Path) -> GitIdentity:
    return GitIdentity(
        user_name=_git_config_get(repo, "user.name"),
        user_email=_git_config_get(repo, "user.email"),
    )


def resolve_git_identity(
    repo: Path, user_name: str | None = None, user_email: str | None = None
) -> GitIdentity:
    host_identity = read_git_identity(repo)
    return GitIdentity(
        user_name=user_name if user_name is not None else host_identity.user_name,
        user_email=user_email if user_email is not None else host_identity.user_email,
    )


def apply_git_identity(repo: Path, identity: GitIdentity) -> None:
    if identity.user_name:
        run_git(["config", "user.name", identity.user_name], repo)
    if identity.user_email:
        run_git(["config", "user.email", identity.user_email], repo)


def copy_dirty_paths(source: Path, dest: Path) -> None:
    result = run_git(["status", "--porcelain", "-z"], source)
    records = result.stdout.encode().split(b"\0")
    i = 0
    while i < len(records):
        if not records[i]:
            i += 1
            continue
        entry = os.fsdecode(records[i])
        status = entry[:2]
        path = entry[3:]
        i += 1
        if "R" in status or "C" in status:
            if i >= len(records):
                break
            path = os.fsdecode(records[i])
            i += 1
        _copy_or_remove(source / path, dest / path)


def count_commits_since(repo: Path, base: str) -> int:
    result = run_git(["rev-list", "--count", f"{base}..HEAD"], repo)
    return int(result.stdout.strip() or "0")


def count_commits_between(repo: Path, base: str, head: str) -> int:
    result = run_git(["rev-list", "--count", f"{base}..{head}"], repo)
    return int(result.stdout.strip() or "0")


def has_uncommitted_changes(repo: Path) -> bool:
    return bool(run_git(["status", "--porcelain"], repo).stdout.strip())


def current_head(repo: Path) -> str:
    return run_git(["rev-parse", "--verify", "HEAD"], repo).stdout.strip()


def rev_parse(repo: Path, ref: str) -> str:
    return run_git(["rev-parse", "--verify", ref], repo).stdout.strip()


def branch_exists(repo: Path, branch: str) -> bool:
    result = run_git(["show-ref", "--verify", f"refs/heads/{branch}"], repo, check=False)
    return result.returncode == 0


def fetch_head(original_repo: Path, run_repo: Path) -> str:
    run_git(["fetch", "--quiet", str(run_repo), "HEAD"], original_repo)
    return rev_parse(original_repo, "FETCH_HEAD")


def one_line_log(repo: Path, base: str, head: str, limit: int = 20) -> list[str]:
    result = run_git(["log", "--oneline", f"--max-count={limit}", f"{base}..{head}"], repo)
    if not result.stdout.strip():
        return []
    return result.stdout.strip().splitlines()


def is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    result = run_git(["merge-base", "--is-ancestor", ancestor, descendant], repo, check=False)
    return result.returncode == 0


def import_branch(original_repo: Path, run_repo: Path, branch: str, force: bool) -> None:
    refspec = f"HEAD:refs/heads/{branch}"
    if force:
        refspec = f"+{refspec}"
    run_git(["fetch", str(run_repo), refspec], original_repo)


def import_branch_signed(
    original_repo: Path,
    run_repo: Path,
    base_head: str,
    branch: str,
    force: bool,
) -> str:
    fetch_head(original_repo, run_repo)
    if branch_exists(original_repo, branch) and not force:
        raise RuntimeError(f"branch {branch} already exists")

    merge_commits = _rev_list(original_repo, [f"{base_head}..FETCH_HEAD", "--merges"])
    if merge_commits:
        raise RuntimeError(
            "signed import does not support merge commits; rerun with --no-sign-imports"
        )

    commits = _rev_list(original_repo, [f"{base_head}..FETCH_HEAD", "--reverse"])
    if not commits:
        return base_head

    with tempfile.TemporaryDirectory(prefix="agentc-sign-import-") as tmp:
        worktree = Path(tmp) / "worktree"
        try:
            run_git(["worktree", "add", "--detach", str(worktree), base_head], original_repo)
            for commit in commits:
                run_git(["cherry-pick", "-S", commit], worktree)
            signed_head = current_head(worktree)
            run_git(["update-ref", f"refs/heads/{branch}", signed_head], original_repo)
            return signed_head
        finally:
            if worktree.exists():
                run_git(
                    ["worktree", "remove", "--force", str(worktree)],
                    original_repo,
                    check=False,
                )


def check_fast_forward(
    original_repo: Path, expected_branch: str, target_ref: str
) -> FastForwardCheck:
    state = repo_state(original_repo)
    target_head = rev_parse(original_repo, target_ref)
    if state.dirty:
        return FastForwardCheck(
            ok=False,
            reason="current worktree is dirty",
            current_branch=state.branch,
            current_head=state.head,
            target_head=target_head,
        )
    if state.branch != expected_branch:
        return FastForwardCheck(
            ok=False,
            reason=f"current branch is {state.branch}, expected {expected_branch}",
            current_branch=state.branch,
            current_head=state.head,
            target_head=target_head,
        )
    if not is_ancestor(original_repo, state.head, target_head):
        return FastForwardCheck(
            ok=False,
            reason="current branch has diverged",
            current_branch=state.branch,
            current_head=state.head,
            target_head=target_head,
        )
    return FastForwardCheck(
        ok=True,
        reason=None,
        current_branch=state.branch,
        current_head=state.head,
        target_head=target_head,
    )


def fast_forward(original_repo: Path, target_ref: str) -> None:
    run_git(["merge", "--ff-only", target_ref], original_repo)


def _copy_or_remove(src: Path, dest: Path) -> None:
    if not src.exists():
        if dest.is_dir():
            shutil.rmtree(dest)
        elif dest.exists():
            dest.unlink()
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        shutil.copytree(src, dest, dirs_exist_ok=True, ignore=shutil.ignore_patterns(".git"))
    else:
        shutil.copy2(src, dest)


def _git_config_get(repo: Path, key: str) -> str | None:
    result = run_git(["config", "--get", key], repo, check=False)
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value or None


def _rev_list(repo: Path, args: list[str]) -> list[str]:
    result = run_git(["rev-list", *args], repo)
    if not result.stdout.strip():
        return []
    return result.stdout.strip().splitlines()
