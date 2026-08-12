from __future__ import annotations

from typing import Literal, NewType, TypeAlias

RunId = NewType("RunId", str)
DriverId = NewType("DriverId", str)
ImageName = NewType("ImageName", str)
ImageRef = NewType("ImageRef", str)
GitCommit = NewType("GitCommit", str)
GitBranch = NewType("GitBranch", str)

DirtyMode: TypeAlias = Literal["prompt", "include", "ignore", "abort"]
UncommittedMode: TypeAlias = Literal["prompt", "commit-all", "commit-staged", "later", "abort"]
PullMode: TypeAlias = Literal["prompt", "branch", "ff-only", "later"]
PullAction: TypeAlias = Literal["branch", "ff-only", "later"]
SelinuxMode: TypeAlias = Literal["auto", "z", "Z", "disabled"]
MountKind: TypeAlias = Literal["file", "directory"]
MountRelabel: TypeAlias = Literal["shared", "private", "none"]
DiagnosticSeverity: TypeAlias = Literal["ok", "warning", "error"]

DIRTY_MODES: tuple[DirtyMode, ...] = ("prompt", "include", "ignore", "abort")
UNCOMMITTED_MODES: tuple[UncommittedMode, ...] = (
    "prompt",
    "commit-all",
    "commit-staged",
    "later",
    "abort",
)
PULL_MODES: tuple[PullMode, ...] = ("prompt", "branch", "ff-only", "later")
SELINUX_MODES: tuple[SelinuxMode, ...] = ("auto", "z", "Z", "disabled")
