from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import shutil
import subprocess


@dataclass(frozen=True)
class RepoState:
    root: Path
    branch: str
    head: str
    dirty: bool


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


def has_uncommitted_changes(repo: Path) -> bool:
    return bool(run_git(["status", "--porcelain"], repo).stdout.strip())


def current_head(repo: Path) -> str:
    return run_git(["rev-parse", "--verify", "HEAD"], repo).stdout.strip()


def branch_exists(repo: Path, branch: str) -> bool:
    result = run_git(["show-ref", "--verify", f"refs/heads/{branch}"], repo, check=False)
    return result.returncode == 0


def import_branch(original_repo: Path, run_repo: Path, branch: str, force: bool) -> None:
    refspec = f"HEAD:refs/heads/{branch}"
    if force:
        refspec = f"+{refspec}"
    run_git(["fetch", str(run_repo), refspec], original_repo)


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

