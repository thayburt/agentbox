from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import tomllib


CONFIG_FILE = "agentbox.toml"


@dataclass(frozen=True)
class Config:
    repo_root: Path
    run_store: Path
    devcontainer: Path | None
    image_name: str
    base_image: str
    codex_home: Path
    workspace_folder: str
    selinux: str
    git_user_name: str | None
    git_user_email: str | None
    sign_imports: bool


def default_toml() -> str:
    codex_home = os.environ.get("CODEX_HOME", "~/.codex")
    return f"""# agentbox project configuration

[runtime]
run_store = ".agentbox/runs"
selinux = "auto" # auto, z, Z, or disabled

[devcontainer]
path = ".devcontainer/devcontainer.json"

[codex]
image_name = "agentbox-codex"
base_image = "ubuntu:24.04"
workspace_folder = "/workspace"
codex_home = "{codex_home}"

[git]
# user_name = "Your Name"
# user_email = "you@example.com"
sign_imports = false
"""


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
    codex_home_raw = _get(data, "codex.codex_home", os.environ.get("CODEX_HOME", "~/.codex"))

    run_store = _resolve_repo_path(repo_root, run_store_raw)
    devcontainer = _resolve_repo_path(repo_root, devcontainer_raw) if devcontainer_raw else None

    return Config(
        repo_root=repo_root,
        run_store=run_store,
        devcontainer=devcontainer,
        image_name=str(_get(data, "codex.image_name", "agentbox-codex")),
        base_image=str(_get(data, "codex.base_image", "ubuntu:24.04")),
        codex_home=Path(str(codex_home_raw)).expanduser(),
        workspace_folder=str(_get(data, "codex.workspace_folder", "/workspace")),
        selinux=str(_get(data, "runtime.selinux", "auto")),
        git_user_name=_optional_str(_get(data, "git.user_name")),
        git_user_email=_optional_str(_get(data, "git.user_email")),
        sign_imports=bool(_get(data, "git.sign_imports", False)),
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
