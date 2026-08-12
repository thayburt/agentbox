from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ..domain import DiagnosticSeverity, DriverId, ImageName, ImageRef, MountKind, MountRelabel


@dataclass(frozen=True)
class CommonDriverSettings:
    image_name: ImageName
    base_image: ImageRef
    workspace_folder: str


@dataclass(frozen=True)
class MountSpec:
    source: Path
    target: str
    kind: MountKind
    create: bool = False
    optional: bool = False
    readonly: bool = False
    chown: bool = False
    relabel: MountRelabel = "shared"
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
class RunSeedDirectorySpec:
    source: str
    destination: Path
    description: str = ""


@dataclass(frozen=True)
class Diagnostic:
    name: str
    value: str
    severity: DiagnosticSeverity
    message: str | None = None


class HarnessDriver(Protocol):
    id: DriverId
    display_name: str
    aliases: tuple[str, ...]

    def default_settings(self, host_env: Mapping[str, str]) -> object: ...

    def load_settings(
        self, section: Mapping[str, object], host_env: Mapping[str, str]
    ) -> object: ...

    def default_toml_section(self, host_env: Mapping[str, str]) -> str: ...

    def default_containerfile(self, settings: object) -> str: ...

    def state_mounts(self, settings: object, host_env: Mapping[str, str]) -> list[MountSpec]: ...

    def run_state_mounts(
        self, settings: object, host_env: Mapping[str, str], run_dir: Path
    ) -> list[MountSpec]: ...

    def run_seed_files(
        self, settings: object, host_env: Mapping[str, str], run_dir: Path
    ) -> list[RunSeedFileSpec]: ...

    def run_seed_directories(
        self, settings: object, host_env: Mapping[str, str], run_dir: Path
    ) -> list[RunSeedDirectorySpec]: ...

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


def reject_unknown_settings(section: Mapping[str, object], allowed: set[str], path: str) -> None:
    unknown = sorted(set(section) - allowed)
    if unknown:
        raise ValueError(f"agentbox.toml: {path}.{unknown[0]} is not a valid setting")


def required_string(section: Mapping[str, object], key: str, default: str, path: str) -> str:
    value = section.get(key, default)
    if not isinstance(value, str):
        raise ValueError(f"agentbox.toml: {path}.{key} must be a string")
    return value
