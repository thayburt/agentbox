from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .base import CommonDriverSettings, Diagnostic, InitFileSpec, MountSpec


@dataclass(frozen=True)
class CodexSettings(CommonDriverSettings):
    codex_home: Path


class CodexDriver:
    id = "codex"
    display_name = "Codex"
    aliases: tuple[str, ...] = ()

    def default_settings(self, host_env: Mapping[str, str]) -> CodexSettings:
        return CodexSettings(
            image_name="agentbox-codex",
            base_image="ubuntu:24.04",
            workspace_folder="/workspace",
            codex_home=Path(host_env.get("CODEX_HOME", "~/.codex")).expanduser(),
        )

    def load_settings(self, section: Mapping[str, object], host_env: Mapping[str, str]) -> CodexSettings:
        defaults = self.default_settings(host_env)
        codex_home = Path(str(section.get("codex_home", defaults.codex_home))).expanduser()
        return CodexSettings(
            image_name=str(section.get("image_name", defaults.image_name)),
            base_image=str(section.get("base_image", defaults.base_image)),
            workspace_folder=str(section.get("workspace_folder", defaults.workspace_folder)),
            codex_home=codex_home,
        )

    def default_toml_section(self, host_env: Mapping[str, str]) -> str:
        defaults = self.default_settings(host_env)
        codex_home = host_env.get("CODEX_HOME", "~/.codex")
        return f"""[codex]
image_name = \"{defaults.image_name}\"
base_image = \"{defaults.base_image}\"
workspace_folder = \"{defaults.workspace_folder}\"
codex_home = \"{codex_home}\"
"""

    def default_containerfile(self, settings: object) -> str:
        typed = _settings(settings)
        return f"""FROM {typed.base_image}

ENV DEBIAN_FRONTEND=noninteractive
ENV CODEX_NON_INTERACTIVE=1

RUN apt-get update \\
    && apt-get install -y --no-install-recommends \\
        bash \\
        ca-certificates \\
        curl \\
        git \\
        jq \\
        less \\
        openssh-client \\
        python3 \\
        ripgrep \\
        sudo \\
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /opt/codex-install \\
    && curl -fsSL https://chatgpt.com/codex/install.sh | CODEX_HOME=/opt/codex-install CODEX_NON_INTERACTIVE=1 CODEX_INSTALL_DIR=/usr/local/bin sh

ENV CODEX_HOME=/codex-home

WORKDIR /workspace
"""

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
