from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import tomllib

from .drivers import CodexSettings, CommonDriverSettings, all_drivers, canonical_driver_id, get_driver
from .template import render_template


CONFIG_FILE = "agentbox.toml"


@dataclass(frozen=True)
class Config:
    repo_root: Path
    run_store: Path
    devcontainer: Path | None
    selinux: str
    git_user_name: str | None
    git_user_email: str | None
    sign_imports: bool
    harnesses: dict[str, CommonDriverSettings] = field(default_factory=dict)

    def driver_settings(self, driver_id: str) -> CommonDriverSettings:
        canonical = canonical_driver_id(driver_id)
        try:
            return self.harnesses[canonical]
        except KeyError as exc:
            get_driver(canonical)
            raise RuntimeError(f"missing settings for driver: {canonical}") from exc

    @property
    def image_name(self) -> str:
        return self._codex_settings().image_name

    @property
    def base_image(self) -> str:
        return self._codex_settings().base_image

    @property
    def codex_home(self) -> Path:
        return self._codex_settings().codex_home

    @property
    def workspace_folder(self) -> str:
        return self._codex_settings().workspace_folder

    def _codex_settings(self) -> CodexSettings:
        settings = self.driver_settings("codex")
        if not isinstance(settings, CodexSettings):
            raise RuntimeError("codex driver returned invalid settings")
        return settings


def default_toml() -> str:
    driver_sections = "\n".join(
        driver.default_toml_section(os.environ).rstrip() for driver in all_drivers()
    )
    return render_template("agentbox.toml", {"DRIVER_SECTIONS": driver_sections})


def _get(table: dict, dotted: str, default=None):
    current = table
    for part in dotted.split("."):
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]
    return current


def load_config(repo_root: Path) -> Config:
    repo_root = repo_root.resolve()
    path = repo_root / CONFIG_FILE
    data: dict = {}
    if path.exists():
        data = tomllib.loads(path.read_text())

    run_store_raw = _get(data, "runtime.run_store", ".agentbox/runs")
    devcontainer_raw = _get(data, "devcontainer.path", ".devcontainer/devcontainer.json")
    harnesses = {}
    for driver in all_drivers():
        section = data.get(driver.id, {})
        if not isinstance(section, dict):
            section = {}
        settings = driver.load_settings(section, os.environ)
        if not isinstance(settings, CommonDriverSettings):
            raise RuntimeError(f"driver {driver.id} returned invalid settings")
        harnesses[driver.id] = settings

    run_store = _resolve_repo_path(repo_root, run_store_raw)
    devcontainer = _resolve_repo_path(repo_root, devcontainer_raw) if devcontainer_raw else None

    return Config(
        repo_root=repo_root,
        run_store=run_store,
        devcontainer=devcontainer,
        selinux=str(_get(data, "runtime.selinux", "auto")),
        git_user_name=_optional_str(_get(data, "git.user_name")),
        git_user_email=_optional_str(_get(data, "git.user_email")),
        sign_imports=bool(_get(data, "git.sign_imports", False)),
        harnesses=harnesses,
    )


def _resolve_repo_path(repo_root: Path, value: str) -> Path:
    path = Path(str(value)).expanduser()
    if path.is_absolute():
        return path
    return repo_root / path


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None
