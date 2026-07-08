from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Mapping

from .base import CommonDriverSettings, Diagnostic, MountSpec


@dataclass(frozen=True)
class KiloSettings(CommonDriverSettings):
    pass


class KiloDriver:
    id = "kilo"
    display_name = "Kilo Code"
    aliases = ("kilocode",)
    containerfile_name = "kilo.Containerfile"

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

WORKDIR /workspace
"""

    def state_mounts(self, settings: object, host_env: Mapping[str, str]) -> list[MountSpec]:
        _settings(settings)
        home = _home(host_env)
        mounts = [
            MountSpec(_xdg_path(host_env, "XDG_CONFIG_HOME", home / ".config") / "kilo", "/kilo-home/.config/kilo", "directory", create=True, description="Kilo XDG state"),
            MountSpec(_xdg_path(host_env, "XDG_DATA_HOME", home / ".local" / "share") / "kilo", "/kilo-home/.local/share/kilo", "directory", create=True, description="Kilo XDG state"),
            MountSpec(_xdg_path(host_env, "XDG_STATE_HOME", home / ".local" / "state") / "kilo", "/kilo-home/.local/state/kilo", "directory", create=True, description="Kilo XDG state"),
            MountSpec(_xdg_path(host_env, "XDG_CACHE_HOME", home / ".cache") / "kilo", "/kilo-home/.cache/kilo", "directory", create=True, description="Kilo XDG state"),
        ]
        legacy = home / ".kilo"
        if legacy.exists():
            mounts.append(MountSpec(legacy, "/kilo-home/.kilo", "directory", optional=True))
        legacy_kilocode = home / ".kilocode"
        if legacy_kilocode.exists():
            mounts.append(MountSpec(legacy_kilocode, "/kilo-home/.kilocode", "directory", optional=True))
        if host_env.get("KILO_CONFIG"):
            mounts.append(MountSpec(Path(host_env["KILO_CONFIG"]).expanduser(), "/kilo-host/KILO_CONFIG", "file", description="KILO_CONFIG file"))
        if host_env.get("KILO_CONFIG_DIR"):
            mounts.append(MountSpec(Path(host_env["KILO_CONFIG_DIR"]).expanduser(), "/kilo-host/KILO_CONFIG_DIR", "directory", create=True, description="KILO_CONFIG_DIR directory"))
        return mounts

    def env(self, settings: object, host_env: Mapping[str, str]) -> dict[str, str]:
        _settings(settings)
        env = {
            "HOME": "/kilo-home",
            "XDG_CONFIG_HOME": "/kilo-home/.config",
            "XDG_DATA_HOME": "/kilo-home/.local/share",
            "XDG_STATE_HOME": "/kilo-home/.local/state",
            "XDG_CACHE_HOME": "/kilo-home/.cache",
            "KILO_CONFIG_CONTENT": merged_kilo_config_content(host_env.get("KILO_CONFIG_CONTENT")),
        }
        if host_env.get("KILO_CONFIG"):
            env["KILO_CONFIG"] = "/kilo-host/KILO_CONFIG"
        if host_env.get("KILO_CONFIG_DIR"):
            env["KILO_CONFIG_DIR"] = "/kilo-host/KILO_CONFIG_DIR"
        return env

    def launch_argv(self, workspace: str, prompt: str) -> list[str]:
        args = [
            "kilo",
            "run",
            "--dir",
            workspace,
            "--interactive",
            "--auto",
        ]
        if prompt:
            args.append(prompt)
        return args

    def diagnostics(self, settings: object, host_env: Mapping[str, str]) -> list[Diagnostic]:
        _settings(settings)
        mounts = self.state_mounts(settings, host_env)
        normal_paths = [mount.source for mount in mounts if mount.description == "Kilo XDG state"]
        exists = any(path.exists() for path in normal_paths)
        severity = "ok" if exists else "warning"
        message = None if exists else "not found; first interactive setup may create it"
        diagnostics = [Diagnostic("kilo_state", ", ".join(str(path) for path in normal_paths), severity, message)]
        for mount in mounts:
            if mount.description == "Kilo XDG state":
                continue
            source = mount.source.expanduser()
            if mount.optional or source.exists() or (mount.kind == "directory" and mount.create):
                continue
            diagnostics.append(
                Diagnostic(
                    mount.description or mount.target,
                    str(source),
                    "error",
                    f"required {mount.kind} does not exist",
                )
            )
        return diagnostics


def merged_kilo_config_content(raw: str | None) -> str:
    if raw:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"KILO_CONFIG_CONTENT is invalid JSON: {exc.msg}") from exc
        if not isinstance(data, dict):
            raise RuntimeError("KILO_CONFIG_CONTENT must be a JSON object")
    else:
        data = {}
    data.update(
        {
            "sandbox": False,
            "sandbox_restrict_network": False,
            "permission": "allow",
        }
    )
    return json.dumps(data, separators=(",", ":"), sort_keys=True)


def _home(host_env: Mapping[str, str]) -> Path:
    return Path(host_env.get("HOME", str(Path.home()))).expanduser()


def _xdg_path(host_env: Mapping[str, str], name: str, fallback: Path) -> Path:
    return Path(host_env.get(name, str(fallback))).expanduser()


def _settings(settings: object) -> KiloSettings:
    if not isinstance(settings, KiloSettings):
        raise TypeError("KiloDriver requires KiloSettings")
    return settings
