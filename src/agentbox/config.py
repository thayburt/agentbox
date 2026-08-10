from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .domain import DriverId, SelinuxMode, SELINUX_MODES
from .drivers import (
    CommonDriverSettings,
    all_drivers,
    canonical_driver_id,
    get_driver,
)
from .template import render_template

CONFIG_FILE = "agentbox.toml"


@dataclass(frozen=True)
class Config:
    repo_root: Path
    run_store: Path
    selinux: SelinuxMode
    git_user_name: str | None
    git_user_email: str | None
    sign_imports: bool
    harnesses: dict[DriverId, CommonDriverSettings] = field(default_factory=dict)

    def driver_settings(self, driver_id: str | DriverId) -> CommonDriverSettings:
        canonical = canonical_driver_id(driver_id)
        try:
            return self.harnesses[canonical]
        except KeyError as exc:
            get_driver(canonical)
            raise RuntimeError(f"missing settings for driver: {canonical}") from exc


def default_toml() -> str:
    driver_sections = "\n".join(
        driver.default_toml_section(os.environ).rstrip() for driver in all_drivers()
    )
    return render_template("agentbox.toml", {"DRIVER_SECTIONS": driver_sections})


def load_config(repo_root: Path) -> Config:
    repo_root = repo_root.resolve()
    path = repo_root / CONFIG_FILE
    data: dict[str, object] = {}
    if path.exists():
        data = tomllib.loads(path.read_text())

    allowed_sections = {"runtime", "git", *(str(driver.id) for driver in all_drivers())}
    unknown_sections = sorted(set(data) - allowed_sections)
    if unknown_sections:
        raise ValueError(f"agentbox.toml: {unknown_sections[0]} is not a valid section")

    runtime = _table(data, "runtime")
    git = _table(data, "git")
    _reject_unknown(runtime, {"run_store", "selinux"}, "runtime")
    _reject_unknown(git, {"user_name", "user_email", "sign_imports"}, "git")
    run_store_raw = _string(runtime, "run_store", ".agentbox/runs", "runtime")
    selinux_raw = _string(runtime, "selinux", "auto", "runtime")
    if selinux_raw not in SELINUX_MODES:
        values = ", ".join(SELINUX_MODES)
        raise ValueError(f"agentbox.toml: runtime.selinux must be one of {values}")
    harnesses: dict[DriverId, CommonDriverSettings] = {}
    for driver in all_drivers():
        section = _table(data, driver.id)
        settings = driver.load_settings(section, os.environ)
        if not isinstance(settings, CommonDriverSettings):
            raise TypeError(f"driver {driver.id} returned invalid settings")
        harnesses[driver.id] = settings

    run_store = _resolve_repo_path(repo_root, run_store_raw)
    # Prevent `runs prune --all` from deleting arbitrary filesystem contents.
    if run_store.resolve() == Path(run_store.resolve().anchor):
        raise RuntimeError("run_store must not be the filesystem root")

    return Config(
        repo_root=repo_root,
        run_store=run_store,
        selinux=selinux_raw,
        git_user_name=_optional_string(git, "user_name", "git"),
        git_user_email=_optional_string(git, "user_email", "git"),
        sign_imports=_boolean(git, "sign_imports", False, "git"),
        harnesses=harnesses,
    )


def _resolve_repo_path(repo_root: Path, value: str) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    return repo_root / path


def _table(data: dict[str, object], key: str | DriverId) -> dict[str, object]:
    value = data.get(key, {})
    if not isinstance(value, dict):
        raise ValueError(f"agentbox.toml: {key} must be a table")
    if not all(isinstance(item, str) for item in value):
        raise ValueError(f"agentbox.toml: {key} contains a non-string key")
    return value


def _reject_unknown(table: dict[str, object], allowed: set[str], path: str) -> None:
    unknown = sorted(set(table) - allowed)
    if unknown:
        raise ValueError(f"agentbox.toml: {path}.{unknown[0]} is not a valid setting")


def _string(table: dict[str, object], key: str, default: str, path: str) -> str:
    value = table.get(key, default)
    if not isinstance(value, str):
        raise ValueError(f"agentbox.toml: {path}.{key} must be a string")
    return value


def _optional_string(table: dict[str, object], key: str, path: str) -> str | None:
    if key not in table:
        return None
    value = table[key]
    if not isinstance(value, str):
        raise ValueError(f"agentbox.toml: {path}.{key} must be a string")
    return value or None


def _boolean(table: dict[str, object], key: str, default: bool, path: str) -> bool:
    value = table.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"agentbox.toml: {path}.{key} must be a boolean")
    return value
