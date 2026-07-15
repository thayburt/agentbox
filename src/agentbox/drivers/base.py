from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Mapping, Protocol


@dataclass(frozen=True)
class CommonDriverSettings:
    image_name: str
    base_image: str
    workspace_folder: str


@dataclass(frozen=True)
class MountSpec:
    source: Path
    target: str
    kind: Literal["file", "directory"]
    create: bool = False
    optional: bool = False
    readonly: bool = False
    chown: bool = False
    relabel: Literal["shared", "private", "none"] = "shared"
    description: str = ""


@dataclass(frozen=True)
class InitFileSpec:
    relative_path: Path
    contents: str
    description: str = ""


@dataclass(frozen=True)
class RunSeedFileSpec:
    source: Path
    destination: Path
    description: str = ""


@dataclass(frozen=True)
class Diagnostic:
    name: str
    value: str
    severity: Literal["ok", "warning", "error"]
    message: str | None = None


class HarnessDriver(Protocol):
    id: str
    display_name: str
    aliases: tuple[str, ...]

    def default_settings(self, host_env: Mapping[str, str]) -> object: ...

    def load_settings(self, section: Mapping[str, object], host_env: Mapping[str, str]) -> object: ...

    def default_toml_section(self, host_env: Mapping[str, str]) -> str: ...

    def default_containerfile(self, settings: object) -> str: ...

    def state_mounts(self, settings: object, host_env: Mapping[str, str]) -> list[MountSpec]: ...

    def run_state_mounts(
        self, settings: object, host_env: Mapping[str, str], run_dir: Path
    ) -> list[MountSpec]: ...

    def run_seed_files(
        self, settings: object, host_env: Mapping[str, str], run_dir: Path
    ) -> list[RunSeedFileSpec]: ...

    def init_files(self, settings: object) -> list[InitFileSpec]: ...

    def config_mounts(
        self, settings: object, host_env: Mapping[str, str], repo_root: Path
    ) -> list[MountSpec]: ...

    def env(self, settings: object, host_env: Mapping[str, str]) -> dict[str, str]: ...

    def config_env(
        self, settings: object, host_env: Mapping[str, str], repo_root: Path
    ) -> dict[str, str]: ...

    def runtime_warnings(
        self, settings: object, host_env: Mapping[str, str], repo_root: Path
    ) -> list[str]: ...

    def launch_argv(self, workspace: str, prompt: str) -> list[str]: ...

    def diagnostics(
        self, settings: object, host_env: Mapping[str, str], repo_root: Path
    ) -> list[Diagnostic]: ...
