from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .base import CommonDriverSettings, Diagnostic, InitFileSpec, MountSpec


AGENTBOX_CONFIG_RELATIVE_PATH = Path(".agentbox/kilo/kilo.jsonc")
AGENTBOX_CONFIG_CONTENTS = '''{
  "$schema": "https://app.kilo.ai/config.json"
}
'''
KILO_HOME = "/home/ubuntu"


@dataclass(frozen=True)
class KiloSettings(CommonDriverSettings):
    pass


class KiloDriver:
    id = "kilo"
    display_name = "Kilo Code"
    aliases = ("kilocode",)

    def default_settings(self, host_env: Mapping[str, str]) -> KiloSettings:
        del host_env
        return KiloSettings(
            image_name="agentbox-kilo",
            base_image="ubuntu:24.04",
            workspace_folder="/workspace",
        )

    def load_settings(self, section: Mapping[str, object], host_env: Mapping[str, str]) -> KiloSettings:
        defaults = self.default_settings(host_env)
        return KiloSettings(
            image_name=str(section.get("image_name", defaults.image_name)),
            base_image=str(section.get("base_image", defaults.base_image)),
            workspace_folder=str(section.get("workspace_folder", defaults.workspace_folder)),
        )

    def default_toml_section(self, host_env: Mapping[str, str]) -> str:
        defaults = self.default_settings(host_env)
        return f"""[kilo]
image_name = \"{defaults.image_name}\"
base_image = \"{defaults.base_image}\"
workspace_folder = \"{defaults.workspace_folder}\"
"""

    def default_containerfile(self, settings: object) -> str:
        typed = _settings(settings)
        return f"""FROM {typed.base_image}

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update \\
    && apt-get install -y --no-install-recommends \\
        bash \\
        ca-certificates \\
        curl \\
        git \\
        jq \\
        less \\
        nodejs \\
        npm \\
        openssh-client \\
        python3 \\
        ripgrep \\
        sudo \\
    && rm -rf /var/lib/apt/lists/*

RUN npm install -g @kilocode/cli \\
    && kilo --version

USER ubuntu

WORKDIR /workspace
"""

    def state_mounts(self, settings: object, host_env: Mapping[str, str]) -> list[MountSpec]:
        _settings(settings)
        home = _home(host_env)
        return [
            MountSpec(_xdg_path(host_env, "XDG_DATA_HOME", home / ".local" / "share") / "kilo", f"{KILO_HOME}/.local/share/kilo", "directory", create=True, chown=True, description="Kilo XDG state"),
            MountSpec(_xdg_path(host_env, "XDG_STATE_HOME", home / ".local" / "state") / "kilo", f"{KILO_HOME}/.local/state/kilo", "directory", create=True, chown=True, description="Kilo XDG state"),
            MountSpec(_xdg_path(host_env, "XDG_CACHE_HOME", home / ".cache") / "kilo", f"{KILO_HOME}/.cache/kilo", "directory", create=True, chown=True, description="Kilo XDG state"),
        ]

    def run_state_mounts(
        self, settings: object, host_env: Mapping[str, str], run_dir: Path
    ) -> list[MountSpec]:
        _settings(settings)
        del host_env
        return [
            MountSpec(
                run_dir / "state" / "kilo-sandbox-policy",
                f"{KILO_HOME}/.local/state/kilo-sandbox-policy",
                "directory",
                create=True,
                chown=True,
                description="Kilo sandbox policy state",
            )
        ]

    def init_files(self, settings: object) -> list[InitFileSpec]:
        _settings(settings)
        return [InitFileSpec(AGENTBOX_CONFIG_RELATIVE_PATH, AGENTBOX_CONFIG_CONTENTS, "Kilo config")]

    def config_mounts(
        self, settings: object, host_env: Mapping[str, str], repo_root: Path
    ) -> list[MountSpec]:
        _settings(settings)
        home = _home(host_env)
        agentbox_config = repo_root / AGENTBOX_CONFIG_RELATIVE_PATH
        mounts = [
            MountSpec(_xdg_path(host_env, "XDG_CONFIG_HOME", home / ".config") / "kilo", f"{KILO_HOME}/.config/kilo", "directory", optional=True, readonly=True, relabel="none", description="Kilo XDG config"),
            MountSpec(home / ".kilo", f"{KILO_HOME}/.kilo", "directory", optional=True, readonly=True, relabel="none", description="Kilo home config"),
            MountSpec(home / ".kilocode", f"{KILO_HOME}/.kilocode", "directory", optional=True, readonly=True, relabel="none", description="Kilo legacy home config"),
        ]
        if host_env.get("KILO_CONFIG_DIR"):
            mounts.append(MountSpec(Path(host_env["KILO_CONFIG_DIR"]).expanduser(), "/kilo-host/KILO_CONFIG_DIR", "directory", readonly=True, relabel="none", description="KILO_CONFIG_DIR directory"))
        if agentbox_config.exists():
            mounts.append(MountSpec(agentbox_config, "/agentbox/config/kilo.jsonc", "file", readonly=True, description="Agentbox Kilo config"))
        elif host_env.get("KILO_CONFIG"):
            mounts.append(MountSpec(Path(host_env["KILO_CONFIG"]).expanduser(), "/kilo-host/KILO_CONFIG", "file", readonly=True, relabel="none", description="KILO_CONFIG file"))
        return mounts

    def env(self, settings: object, host_env: Mapping[str, str]) -> dict[str, str]:
        _settings(settings)
        del host_env
        return {
            "HOME": KILO_HOME,
            "XDG_CONFIG_HOME": f"{KILO_HOME}/.config",
            "XDG_DATA_HOME": f"{KILO_HOME}/.local/share",
            "XDG_STATE_HOME": f"{KILO_HOME}/.local/state",
            "XDG_CACHE_HOME": f"{KILO_HOME}/.cache",
        }

    def config_env(
        self, settings: object, host_env: Mapping[str, str], repo_root: Path
    ) -> dict[str, str]:
        _settings(settings)
        env = {}
        if host_env.get("KILO_CONFIG_DIR"):
            env["KILO_CONFIG_DIR"] = "/kilo-host/KILO_CONFIG_DIR"
        if (repo_root / AGENTBOX_CONFIG_RELATIVE_PATH).exists():
            env["KILO_CONFIG"] = "/agentbox/config/kilo.jsonc"
        elif host_env.get("KILO_CONFIG"):
            env["KILO_CONFIG"] = "/kilo-host/KILO_CONFIG"
        return env

    def runtime_warnings(
        self, settings: object, host_env: Mapping[str, str], repo_root: Path
    ) -> list[str]:
        _settings(settings)
        if host_env.get("KILO_CONFIG") and (repo_root / AGENTBOX_CONFIG_RELATIVE_PATH).exists():
            return [
                "agentbox: warning: host "
                f"KILO_CONFIG={host_env['KILO_CONFIG']} is ignored inside Kilo containers because "
                ".agentbox/kilo/kilo.jsonc is mounted as KILO_CONFIG"
            ]
        return []

    def launch_argv(self, workspace: str, prompt: str) -> list[str]:
        args = [
            "kilo",
        ]
        if prompt:
            args.append(prompt)
        return args

    def diagnostics(
        self, settings: object, host_env: Mapping[str, str], repo_root: Path
    ) -> list[Diagnostic]:
        _settings(settings)
        mounts = self.state_mounts(settings, host_env)
        normal_paths = [mount.source for mount in mounts if mount.description == "Kilo XDG state"]
        exists = any(path.exists() for path in normal_paths)
        severity = "ok" if exists else "warning"
        message = None if exists else "not found; first interactive setup may create it"
        diagnostics = [Diagnostic("kilo_state", ", ".join(str(path) for path in normal_paths), severity, message)]
        agentbox_config = repo_root / AGENTBOX_CONFIG_RELATIVE_PATH
        diagnostics.append(Diagnostic("Agentbox Kilo config", str(agentbox_config), "ok" if agentbox_config.exists() else "warning", None if agentbox_config.exists() else "run agentbox init to create it"))
        if host_env.get("KILO_CONFIG_DIR"):
            source = Path(host_env["KILO_CONFIG_DIR"]).expanduser()
            if not source.is_dir():
                diagnostics.append(Diagnostic("KILO_CONFIG_DIR directory", str(source), "error", "required directory does not exist"))
        if host_env.get("KILO_CONFIG"):
            source = Path(host_env["KILO_CONFIG"]).expanduser()
            if agentbox_config.exists():
                diagnostics.append(Diagnostic("KILO_CONFIG file", str(source), "warning", "ignored because .agentbox/kilo/kilo.jsonc is active"))
            elif not source.is_file():
                diagnostics.append(Diagnostic("KILO_CONFIG file", str(source), "error", "required file does not exist"))
        return diagnostics


def _home(host_env: Mapping[str, str]) -> Path:
    return Path(host_env.get("HOME", str(Path.home()))).expanduser()


def _xdg_path(host_env: Mapping[str, str], name: str, fallback: Path) -> Path:
    return Path(host_env.get(name, str(fallback))).expanduser()


def _settings(settings: object) -> KiloSettings:
    if not isinstance(settings, KiloSettings):
        raise TypeError("KiloDriver requires KiloSettings")
    return settings
