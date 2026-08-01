from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
import json
import secrets
import sys


# run.json is trusted host input because run directories are never mounted into
# containers. If that boundary changes, metadata parsing must become defensive.
METADATA_FILE = "run.json"


@dataclass(frozen=True)
class RunMetadata:
    id: str
    created_at: str
    original_repo: str
    run_repo: str
    base_branch: str
    base_head: str
    image: str
    driver: str = "codex"
    containerfile: str | None = None


def new_run_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{secrets.token_hex(3)}"


def create_metadata(
    run_id: str,
    original_repo: Path,
    run_repo: Path,
    base_branch: str,
    base_head: str,
    image: str,
    *,
    driver: str,
    containerfile: str | None = None,
) -> RunMetadata:
    return RunMetadata(
        id=run_id,
        created_at=datetime.now(timezone.utc).isoformat(),
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
    data.setdefault("driver", "codex")
    return RunMetadata(**data)


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
