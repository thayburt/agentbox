from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import shlex


@dataclass(frozen=True)
class DriverSettings:
    image_name: str
    base_image: str
    workspace_folder: str
    options: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class StateMount:
    source: Path
    target: str
    shared: bool = True
    required: bool = True


@dataclass(frozen=True)
class Driver:
    id: str
    display_name: str
    aliases: tuple[str, ...]
    containerfile_name: str
    default_image_name: str
    default_base_image: str
    default_workspace_folder: str

    def default_containerfile(self, settings: DriverSettings) -> str:
        if self.id == "codex":
            return default_codex_containerfile(settings.base_image)
        if self.id == "kilo":
            return default_kilo_containerfile(settings.base_image)
        raise RuntimeError(f"unknown driver: {self.id}")

    def state_mounts(
        self, settings: DriverSettings, host_env: dict[str, str]
    ) -> list[StateMount]:
        if self.id == "codex":
            return [StateMount(Path(settings.options["codex_home"]).expanduser(), "/codex-home")]
        if self.id == "kilo":
            return kilo_state_mounts(host_env)
        raise RuntimeError(f"unknown driver: {self.id}")

    def container_env(
        self, settings: DriverSettings, host_env: dict[str, str]
    ) -> dict[str, str]:
        del settings
        if self.id == "codex":
            return {"CODEX_HOME": "/codex-home"}
        if self.id == "kilo":
            return kilo_container_env(host_env)
        raise RuntimeError(f"unknown driver: {self.id}")

    def launch_command(self, workspace: str, prompt: str) -> str:
        if self.id == "codex":
            args = [
                "codex",
                "--cd",
                workspace,
                "--sandbox",
                "danger-full-access",
                "--ask-for-approval",
                "never",
            ]
        elif self.id == "kilo":
            args = [
                "kilo",
                "run",
                "--dir",
                workspace,
                "--interactive",
                "--dangerously-skip-permissions",
            ]
        else:
            raise RuntimeError(f"unknown driver: {self.id}")
        if prompt:
            args.append(prompt)
        return "exec " + shlex.join(args)

    def doctor_checks(
        self, settings: DriverSettings, host_env: dict[str, str]
    ) -> list[tuple[str, str, bool]]:
        if self.id == "codex":
            home = Path(settings.options["codex_home"]).expanduser()
            return [("codex_home", str(home), home.exists())]
        if self.id == "kilo":
            paths = [mount.source for mount in kilo_state_mounts(host_env) if mount.required]
            exists = any(path.exists() for path in paths)
            value = ", ".join(str(path) for path in paths)
            suffix = "" if exists else " (not found; first interactive setup may create it)"
            return [("kilo_state", value + suffix, True)]
        raise RuntimeError(f"unknown driver: {self.id}")


CODEX = Driver(
    id="codex",
    display_name="Codex",
    aliases=(),
    containerfile_name="codex.Containerfile",
    default_image_name="agentbox-codex",
    default_base_image="ubuntu:24.04",
    default_workspace_folder="/workspace",
)

KILO = Driver(
    id="kilo",
    display_name="Kilo Code",
    aliases=("kilocode",),
    containerfile_name="kilo.Containerfile",
    default_image_name="agentbox-kilo",
    default_base_image="ubuntu:24.04",
    default_workspace_folder="/workspace",
)

DRIVERS = (CODEX, KILO)
_BY_ID = {driver.id: driver for driver in DRIVERS}
_ALIASES = {alias: driver.id for driver in DRIVERS for alias in driver.aliases}


def get_driver(driver_id: str) -> Driver:
    canonical = canonical_driver_id(driver_id)
    try:
        return _BY_ID[canonical]
    except KeyError as exc:
        raise RuntimeError(f"unknown driver: {driver_id}") from exc


def canonical_driver_id(driver_id: str) -> str:
    return _ALIASES.get(driver_id, driver_id)


def all_drivers() -> tuple[Driver, ...]:
    return DRIVERS


def kilo_state_mounts(host_env: dict[str, str]) -> list[StateMount]:
    home = Path.home()
    mounts = [
        StateMount(_xdg_path(host_env, "XDG_CONFIG_HOME", home / ".config") / "kilo", "/kilo-home/.config/kilo"),
        StateMount(_xdg_path(host_env, "XDG_DATA_HOME", home / ".local" / "share") / "kilo", "/kilo-home/.local/share/kilo"),
        StateMount(_xdg_path(host_env, "XDG_STATE_HOME", home / ".local" / "state") / "kilo", "/kilo-home/.local/state/kilo"),
        StateMount(_xdg_path(host_env, "XDG_CACHE_HOME", home / ".cache") / "kilo", "/kilo-home/.cache/kilo"),
    ]
    legacy = home / ".kilo"
    if legacy.exists():
        mounts.append(StateMount(legacy, "/kilo-home/.kilo", required=False))
    legacy_kilocode = home / ".kilocode"
    if legacy_kilocode.exists():
        mounts.append(StateMount(legacy_kilocode, "/kilo-home/.kilocode", required=False))
    if host_env.get("KILO_CONFIG"):
        mounts.append(StateMount(Path(host_env["KILO_CONFIG"]).expanduser(), "/kilo-host/KILO_CONFIG"))
    if host_env.get("KILO_CONFIG_DIR"):
        mounts.append(StateMount(Path(host_env["KILO_CONFIG_DIR"]).expanduser(), "/kilo-host/KILO_CONFIG_DIR"))
    return mounts


def kilo_container_env(host_env: dict[str, str]) -> dict[str, str]:
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


def _xdg_path(host_env: dict[str, str], name: str, fallback: Path) -> Path:
    return Path(host_env.get(name, str(fallback))).expanduser()


def default_codex_containerfile(base_image: str) -> str:
    return f"""FROM {base_image}

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


def default_kilo_containerfile(base_image: str) -> str:
    return f"""FROM {base_image}

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
