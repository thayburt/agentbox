from __future__ import annotations

from pathlib import Path
import hashlib
import shlex
import subprocess

from .config import Config
from .devcontainer import Devcontainer


def run(args: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=check)


def podman_version() -> str | None:
    result = run(["podman", "--version"], check=False)
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def podman_rootless() -> bool | None:
    result = run(
        ["podman", "info", "--format", "{{.Host.Security.Rootless}}"],
        check=False,
    )
    if result.returncode != 0:
        return None
    value = result.stdout.strip().lower()
    if value == "true":
        return True
    if value == "false":
        return False
    return None


def harness_image_name(config: Config, devcontainer: Devcontainer | None) -> str:
    seed = str(config.base_image)
    if devcontainer:
        seed += str(devcontainer.image or "")
        seed += str(devcontainer.build_context or "")
        seed += str(devcontainer.build_dockerfile or "")
    digest = hashlib.sha256(seed.encode()).hexdigest()[:12]
    return f"{config.image_name}:{digest}"


def build_image(config: Config, devcontainer: Devcontainer | None, dry_run: bool = False) -> list[str]:
    state_dir = config.repo_root / ".agentc" / "images" / "codex"

    base = config.base_image
    if devcontainer and devcontainer.build_context and devcontainer.build_dockerfile:
        base = f"{config.image_name}-base:{_hash_path(devcontainer.build_context)}"
        base_cmd = [
            "podman",
            "build",
            "-t",
            base,
            "-f",
            str(devcontainer.build_dockerfile),
            str(devcontainer.build_context),
        ]
        if dry_run:
            print(shlex.join(base_cmd))
        else:
            subprocess.run(base_cmd, check=True)
    elif devcontainer and devcontainer.image:
        base = devcontainer.image

    image = harness_image_name(config, devcontainer)
    containerfile = state_dir / "Containerfile"
    cmd = ["podman", "build", "-t", image, "-f", str(containerfile), str(state_dir)]
    if dry_run:
        print(shlex.join(cmd))
    else:
        state_dir.mkdir(parents=True, exist_ok=True)
        containerfile.write_text(_containerfile(base))
        subprocess.run(cmd, check=True)
    return cmd


def render_run_command(
    *,
    config: Config,
    devcontainer: Devcontainer | None,
    image: str,
    run_repo: Path,
    command: str,
) -> list[str]:
    workspace = devcontainer.workspace_folder if devcontainer and devcontainer.workspace_folder else None
    workspace = workspace or config.workspace_folder
    suffix = volume_suffix(config.selinux)

    args = [
        "podman",
        "run",
        "--rm",
        "-it",
        "--userns=keep-id",
        "--workdir",
        workspace,
        "-e",
        "CODEX_HOME=/codex-home",
        "-v",
        f"{config.codex_home.resolve()}:/codex-home{suffix}",
        "-v",
        f"{run_repo.resolve()}:{workspace}{suffix}",
    ]
    if devcontainer:
        for key, value in devcontainer.env.items():
            args.extend(["-e", f"{key}={value}"])
        for mount in devcontainer.mounts:
            args.extend(["--mount", mount])
        args.extend(devcontainer.run_args)
    args.extend([image, "bash", "-lc", command])
    return args


def volume_suffix(mode: str) -> str:
    if mode == "disabled":
        return ""
    if mode in {"z", "Z"}:
        return f":{mode}"
    if mode == "auto" and Path("/sys/fs/selinux").exists():
        return ":Z"
    return ""


def _containerfile(base: str) -> str:
    return f"""FROM {base}

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


def _hash_path(path: Path) -> str:
    return hashlib.sha256(str(path.resolve()).encode()).hexdigest()[:12]
