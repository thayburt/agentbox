from __future__ import annotations

from pathlib import Path
import hashlib
import shlex
import subprocess
import tempfile

from .config import Config
from .devcontainer import Devcontainer


def run(args: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=check
    )


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


def harness_containerfile_path(config: Config, harness: str = "codex") -> Path:
    return config.repo_root / ".agentbox" / f"{harness}.Containerfile"


def harness_image_name(config: Config, digest: str) -> str:
    return f"{config.image_name}:{digest}"


def build_image(
    config: Config, devcontainer: Devcontainer | None, dry_run: bool = False
) -> list[str]:
    del devcontainer
    image = current_managed_image(config, dry_run=dry_run)
    containerfile = harness_containerfile_path(config)
    return build_tagged_image(config, containerfile, image, dry_run=dry_run)


def build_tagged_image(
    config: Config,
    containerfile: Path,
    image: str,
    *,
    dry_run: bool = False,
) -> list[str]:
    context = config.repo_root / ".agentbox"
    exists_cmd = ["podman", "image", "exists", image]
    cmd = managed_build_command(config, image, containerfile)
    if dry_run:
        print(shlex.join(exists_cmd))
        print(shlex.join(cmd))
        return cmd
    if image_exists(image):
        print(f"image {image} already exists; skipping build")
        return exists_cmd

    context.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", delete=False) as ignorefile:
        ignorefile.write("runs\n")
        ignorefile_path = ignorefile.name
    try:
        subprocess.run([*cmd[:2], "--ignorefile", ignorefile_path, *cmd[2:]], check=True)
    finally:
        Path(ignorefile_path).unlink(missing_ok=True)
    return cmd


def image_exists(image: str) -> bool:
    result = subprocess.run(
        ["podman", "image", "exists", image],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    return result.returncode == 0


def current_managed_image(config: Config, *, dry_run: bool = False) -> str:
    path = ensure_harness_containerfile(config, dry_run=dry_run)
    if dry_run and not path.exists():
        digest = default_containerfile_digest(config.base_image)
    else:
        digest = containerfile_digest(path)
    return harness_image_name(config, digest)


def ensure_managed_image(config: Config, *, dry_run: bool = False) -> str:
    image = current_managed_image(config, dry_run=dry_run)
    if dry_run:
        print(shlex.join(["podman", "image", "exists", image]))
        print(shlex.join(managed_build_command(config, image)))
    elif not image_exists(image):
        build_image(config, None)
    return image


def managed_build_command(
    config: Config, image: str, containerfile: Path | None = None
) -> list[str]:
    if containerfile is None:
        containerfile = harness_containerfile_path(config)
    context = config.repo_root / ".agentbox"
    return ["podman", "build", "-t", image, "-f", str(containerfile), str(context)]


def ensure_harness_containerfile(
    config: Config, harness: str = "codex", dry_run: bool = False
) -> Path:
    path = harness_containerfile_path(config, harness)
    if path.exists():
        return path
    if dry_run:
        print(f"would create {path}")
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(default_codex_containerfile(config.base_image))
    return path


def containerfile_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def default_containerfile_digest(base_image: str) -> str:
    return hashlib.sha256(default_codex_containerfile(base_image).encode()).hexdigest()


def render_run_command(
    *,
    config: Config,
    devcontainer: Devcontainer | None,
    image: str,
    run_repo: Path,
    command: str,
) -> list[str]:
    workspace = (
        devcontainer.workspace_folder if devcontainer and devcontainer.workspace_folder else None
    )
    workspace = workspace or config.workspace_folder
    # codex_home is a shared host directory: relabel with :z (shared) rather
    # than :Z (private) so podman does not strip the host's own access to it.
    codex_home_suffix = volume_suffix(config.selinux, shared=True)
    # The run clone is ephemeral and container-private, so :Z is appropriate.
    run_repo_suffix = volume_suffix(config.selinux, shared=False)

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
        f"{config.codex_home.resolve()}:/codex-home{codex_home_suffix}",
        "-v",
        f"{run_repo.resolve()}:{workspace}{run_repo_suffix}",
    ]
    if devcontainer:
        for key, value in devcontainer.env.items():
            args.extend(["-e", f"{key}={value}"])
        for mount in devcontainer.mounts:
            args.extend(["--mount", mount])
        args.extend(devcontainer.run_args)
    args.extend([image, "bash", "-lc", command])
    return args


def volume_suffix(mode: str, *, shared: bool = False) -> str:
    if mode == "disabled":
        return ""
    if mode in {"z", "Z"}:
        return f":{mode}"
    if mode == "auto" and Path("/sys/fs/selinux").exists():
        return ":z" if shared else ":Z"
    return ""


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
