from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from ..domain import DriverId, ImageName, ImageRef
from ..template import render_template
from .base import (
    CommonDriverSettings,
    Diagnostic,
    InitFileSpec,
    MountSpec,
    RunSeedDirectorySpec,
    RunSeedFileSpec,
    reject_unknown_settings,
    required_string,
)


@dataclass(frozen=True)
class CodexSettings(CommonDriverSettings):
    codex_home: Path


class CodexDriver:
    id = DriverId("codex")
    display_name = "Codex"
    aliases: tuple[str, ...] = ()

    def default_settings(self, host_env: Mapping[str, str]) -> CodexSettings:
        return CodexSettings(
            image_name=ImageName("agentbox-codex"),
            base_image=ImageRef("ubuntu:24.04"),
            workspace_folder="/workspace",
            codex_home=Path(host_env.get("CODEX_HOME", "~/.codex")).expanduser(),
        )

    def load_settings(
        self, section: Mapping[str, object], host_env: Mapping[str, str]
    ) -> CodexSettings:
        defaults = self.default_settings(host_env)
        reject_unknown_settings(
            section, {"image_name", "base_image", "workspace_folder", "codex_home"}, "codex"
        )
        codex_home = Path(
            required_string(section, "codex_home", str(defaults.codex_home), "codex")
        ).expanduser()
        return CodexSettings(
            image_name=ImageName(
                required_string(section, "image_name", defaults.image_name, "codex")
            ),
            base_image=ImageRef(
                required_string(section, "base_image", defaults.base_image, "codex")
            ),
            workspace_folder=required_string(
                section, "workspace_folder", defaults.workspace_folder, "codex"
            ),
            codex_home=codex_home,
        )

    def default_toml_section(self, host_env: Mapping[str, str]) -> str:
        defaults = self.default_settings(host_env)
        codex_home = host_env.get("CODEX_HOME", "~/.codex")
        return render_template(
            "codex/agentbox-section.toml",
            {
                "IMAGE_NAME": defaults.image_name,
                "BASE_IMAGE": defaults.base_image,
                "WORKSPACE_FOLDER": defaults.workspace_folder,
                "CODEX_HOME": codex_home,
            },
        )

    def default_containerfile(self, settings: object) -> str:
        typed = _settings(settings)
        return render_template("codex/Containerfile", {"BASE_IMAGE": typed.base_image})

    def state_mounts(self, settings: object, host_env: Mapping[str, str]) -> list[MountSpec]:
        del host_env
        typed = _settings(settings)
        return [
            MountSpec(
                typed.codex_home,
                "/codex-home",
                "directory",
                create=True,
                relabel="shared",
                description="Codex home directory",
            )
        ]

    def run_state_mounts(
        self, settings: object, host_env: Mapping[str, str], run_dir: Path
    ) -> list[MountSpec]:
        del settings, host_env, run_dir
        return []

    def run_seed_files(
        self, settings: object, host_env: Mapping[str, str], run_dir: Path
    ) -> list[RunSeedFileSpec]:
        del settings, host_env, run_dir
        return []

    def run_seed_directories(
        self, settings: object, host_env: Mapping[str, str], run_dir: Path
    ) -> list[RunSeedDirectorySpec]:
        del settings, host_env, run_dir
        return []

    def init_files(self, settings: object) -> list[InitFileSpec]:
        del settings
        return []

    def config_mounts(
        self, settings: object, host_env: Mapping[str, str], repo_root: Path
    ) -> list[MountSpec]:
        del settings, host_env, repo_root
        return []

    def env(self, settings: object, host_env: Mapping[str, str]) -> dict[str, str]:
        del settings, host_env
        return {"CODEX_HOME": "/codex-home"}

    def config_env(
        self, settings: object, host_env: Mapping[str, str], repo_root: Path
    ) -> dict[str, str]:
        del settings, host_env, repo_root
        return {}

    def runtime_warnings(
        self, settings: object, host_env: Mapping[str, str], repo_root: Path
    ) -> list[str]:
        del settings, host_env, repo_root
        return []

    def launch_argv(self, workspace: str, prompt: str) -> list[str]:
        args = [
            "codex",
            "--cd",
            workspace,
            "--sandbox",
            "danger-full-access",
            "--ask-for-approval",
            "never",
        ]
        if prompt:
            args.append(prompt)
        return args

    def diagnostics(
        self, settings: object, host_env: Mapping[str, str], repo_root: Path
    ) -> list[Diagnostic]:
        del host_env, repo_root
        home = _settings(settings).codex_home.expanduser()
        return [
            Diagnostic(
                "codex_home",
                str(home),
                "ok" if home.exists() else "error",
            )
        ]


def _settings(settings: object) -> CodexSettings:
    if not isinstance(settings, CodexSettings):
        raise TypeError("CodexDriver requires CodexSettings")
    return settings
