from __future__ import annotations

import os
import shutil
import stat
import sys
import tempfile
from pathlib import Path

from .config import Config
from .drivers import RunSeedFileSpec, get_driver


def seed_run_files(config: Config, driver_id: str, run_dir: Path) -> None:
    driver = get_driver(driver_id)
    settings = config.driver_settings(driver.id)
    for seed in driver.run_seed_files(settings, dict(os.environ), run_dir):
        try:
            copy_seed_file(seed)
        except FileNotFoundError:
            continue
        except OSError as exc:
            warn_seed_failure(seed, exc)


def copy_seed_file(seed: RunSeedFileSpec) -> None:
    source_fd = os.open(seed.source, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        source_stat = os.fstat(source_fd)
        if not stat.S_ISREG(source_stat.st_mode) or os.path.lexists(seed.destination):
            return
        seed.destination.parent.mkdir(parents=True, exist_ok=True)
        temp_fd, temp_name = tempfile.mkstemp(dir=seed.destination.parent)
        temp_path = Path(temp_name)
        try:
            with os.fdopen(source_fd, "rb", closefd=False) as source_file, os.fdopen(
                temp_fd, "wb"
            ) as temp_file:
                shutil.copyfileobj(source_file, temp_file)
                temp_file.flush()
                os.fchmod(temp_file.fileno(), stat.S_IMODE(source_stat.st_mode))
                os.utime(
                    temp_file.fileno(),
                    ns=(source_stat.st_atime_ns, source_stat.st_mtime_ns),
                )
            os.link(temp_path, seed.destination)
        finally:
            temp_path.unlink(missing_ok=True)
    finally:
        os.close(source_fd)


def warn_seed_failure(seed: RunSeedFileSpec, exc: OSError) -> None:
    print(
        f"agentbox: warning: could not seed {seed.description or 'run file'} "
        f"from {seed.source} to {seed.destination}: {exc}",
        file=sys.stderr,
    )


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
