from __future__ import annotations

import json
import secrets
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from .domain import DriverId, GitBranch, GitCommit, ImageRef, RunId
from .drivers import canonical_driver_id

METADATA_FILE = "run.json"


@dataclass(frozen=True)
class RunMetadata:
    id: RunId
    created_at: str
    original_repo: str
    run_repo: str
    base_branch: GitBranch
    base_head: GitCommit
    image: ImageRef
    driver: DriverId = DriverId("codex")
    containerfile: str | None = None


def new_run_id() -> RunId:
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return RunId(f"{stamp}-{secrets.token_hex(3)}")


def create_metadata(
    run_id: RunId,
    original_repo: Path,
    run_repo: Path,
    base_branch: GitBranch,
    base_head: GitCommit,
    image: ImageRef,
    *,
    driver: DriverId,
    containerfile: str | None = None,
) -> RunMetadata:
    return RunMetadata(
        id=run_id,
        created_at=datetime.now(UTC).isoformat(),
        original_repo=str(original_repo),
        run_repo=str(run_repo),
        base_branch=base_branch,
        base_head=base_head,
        image=image,
        driver=driver,
        containerfile=containerfile,
    )


def write_metadata(run_dir: Path, metadata: RunMetadata) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / METADATA_FILE).write_text(json.dumps(asdict(metadata), indent=2) + "\n")


def read_metadata(run_dir: Path) -> RunMetadata:
    data = json.loads((run_dir / METADATA_FILE).read_text())
    if not isinstance(data, dict) or not all(isinstance(key, str) for key in data):
        raise ValueError("run.json must be an object with string keys")
    allowed = {
        "id", "created_at", "original_repo", "run_repo", "base_branch", "base_head", "image",
        "driver", "containerfile",
    }
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ValueError(f"run.json has unknown field: {unknown[0]}")
    required = {
        "id",
        "created_at",
        "original_repo",
        "run_repo",
        "base_branch",
        "base_head",
        "image",
    }
    missing = sorted(required - set(data))
    if missing:
        raise ValueError(f"run.json is missing required field: {missing[0]}")
    for key in required | {"driver"}:
        if key in data and not isinstance(data[key], str):
            raise ValueError(f"run.json field {key} must be a string")
    containerfile = data.get("containerfile")
    if containerfile is not None and not isinstance(containerfile, str):
        raise ValueError("run.json field containerfile must be a string or null")
    return RunMetadata(
        id=RunId(data["id"]),
        created_at=data["created_at"],
        original_repo=data["original_repo"],
        run_repo=data["run_repo"],
        base_branch=GitBranch(data["base_branch"]),
        base_head=GitCommit(data["base_head"]),
        image=ImageRef(data["image"]),
        driver=canonical_driver_id(data.get("driver", "codex")),
        containerfile=containerfile,
    )


def list_runs(run_store: Path) -> list[RunMetadata]:
    if not run_store.exists():
        return []
    found: list[RunMetadata] = []
    for path in sorted(run_store.iterdir()):
        if not path.is_dir() or not (path / METADATA_FILE).exists():
            continue
        try:
            found.append(read_metadata(path))
        except (OSError, json.JSONDecodeError, TypeError, KeyError, ValueError) as exc:
            print(
                f"agentbox: warning: skipping invalid run metadata in {path}: {exc}",
                file=sys.stderr,
            )
    return found
