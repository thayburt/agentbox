from __future__ import annotations

from pathlib import Path
import hashlib
import shlex
import subprocess

from .config import Config
from .devcontainer import Devcontainer
from .drivers import default_codex_containerfile, get_driver


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


def harness_containerfile_path(config: Config, driver_id: str = "codex") -> Path:
    driver = get_driver(driver_id)
    return config.repo_root / ".agentbox" / driver.containerfile_name


def harness_image_name(config: Config, digest: str, driver_id: str = "codex") -> str:
    settings = config.driver_settings(driver_id)
    return f"{settings.image_name}:{digest}"


def build_image(
    config: Config,
    devcontainer: Devcontainer | None,
    dry_run: bool = False,
    *,
    force: bool = False,
    driver_id: str = "codex",
) -> list[str]:
    del devcontainer
    image = current_managed_image(config, dry_run=dry_run, driver_id=driver_id)
    containerfile = harness_containerfile_path(config, driver_id)
    # A forced rebuild also refreshes the base image, since the content-addressed
    # tag cannot detect upstream base-image or install-script changes on its own.
    return build_tagged_image(
        config, containerfile, image, dry_run=dry_run, force=force, pull_newer=force, driver_id=driver_id
    )


def build_tagged_image(
    config: Config,
    containerfile: Path,
    image: str,
    *,
    dry_run: bool = False,
    force: bool = False,
    pull_newer: bool = False,
    driver_id: str = "codex",
) -> list[str]:
    context = config.repo_root / ".agentbox"
    exists_cmd = ["podman", "image", "exists", image]
    cmd = managed_build_command(config, image, containerfile, pull_newer=pull_newer, driver_id=driver_id)
    if dry_run:
        print(shlex.join(exists_cmd))
        print(shlex.join(cmd))
        return cmd
    if not force and image_exists(image):
        print(f"image {image} already exists; skipping build")
        return exists_cmd

    context.mkdir(parents=True, exist_ok=True)
    ensure_containerignore(context)
    subprocess.run(cmd, check=True)
    return cmd


def ensure_containerignore(context: Path) -> None:
    """Ensure the build context ignores the run store.

    The context is ``.agentbox``, which also holds per-run clones under
    ``runs/``. Those must stay out of the build context or every build would
    copy every saved run. A persistent ``.containerignore`` keeps the dry-run
    and real build commands identical (podman reads it automatically).
    """
    path = context / ".containerignore"
    lines = path.read_text().splitlines() if path.exists() else []
    if "runs" not in {line.strip() for line in lines}:
        lines.append("runs")
        path.write_text("\n".join(lines) + "\n")


def image_exists(image: str) -> bool:
    result = subprocess.run(
        ["podman", "image", "exists", image],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    return result.returncode == 0


def current_managed_image(config: Config, *, dry_run: bool = False, driver_id: str = "codex") -> str:
    path = ensure_harness_containerfile(config, driver_id=driver_id, dry_run=dry_run)
    if dry_run and not path.exists():
        digest = default_containerfile_digest(config, driver_id)
    else:
        digest = containerfile_digest(path)
    return harness_image_name(config, digest, driver_id)


def ensure_managed_image(config: Config, *, dry_run: bool = False, driver_id: str = "codex") -> str:
    image = current_managed_image(config, dry_run=dry_run, driver_id=driver_id)
    if dry_run:
        print(shlex.join(["podman", "image", "exists", image]))
        print(shlex.join(managed_build_command(config, image, driver_id=driver_id)))
    elif not image_exists(image):
        build_image(config, None, driver_id=driver_id)
    return image


def managed_build_command(
    config: Config,
    image: str,
    containerfile: Path | None = None,
    *,
    pull_newer: bool = False,
    driver_id: str = "codex",
) -> list[str]:
    if containerfile is None:
        containerfile = harness_containerfile_path(config, driver_id)
    context = config.repo_root / ".agentbox"
    cmd = ["podman", "build", "-t", image, "-f", str(containerfile)]
    if pull_newer:
        cmd.append("--pull=newer")
    cmd.append(str(context))
    return cmd


def list_managed_images(config: Config, driver_id: str = "codex") -> list[str]:
    result = run(["podman", "images", "--format", "{{.Repository}}:{{.Tag}}"], check=False)
    if result.returncode != 0:
        return []
    images: set[str] = set()
    for raw in result.stdout.splitlines():
        line = raw.strip()
        if not line or ":" not in line:
            continue
        repo, _, _tag = normalized_image_ref(line).rpartition(":")
        if repo == config.driver_settings(driver_id).image_name:
            images.add(line)
    return sorted(images)


def image_tag(image: str) -> str:
    return image.rsplit(":", 1)[-1]


def normalized_image_ref(image: str) -> str:
    if image.startswith("localhost/"):
        return image.removeprefix("localhost/")
    return image


def remove_image(image: str) -> None:
    subprocess.run(["podman", "rmi", image], check=True)


def ensure_harness_containerfile(
    config: Config, driver_id: str = "codex", dry_run: bool = False
) -> Path:
    driver = get_driver(driver_id)
    path = harness_containerfile_path(config, driver.id)
    if path.exists():
        return path
    if dry_run:
        print(f"would create {path}")
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(driver.default_containerfile(config.driver_settings(driver.id)))
    return path


def containerfile_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def default_containerfile_digest(config: Config, driver_id: str = "codex") -> str:
    driver = get_driver(driver_id)
    return hashlib.sha256(
        driver.default_containerfile(config.driver_settings(driver.id)).encode()
    ).hexdigest()


def render_run_command(
    *,
    config: Config,
    devcontainer: Devcontainer | None,
    image: str,
    run_repo: Path,
    command: str,
    driver_id: str = "codex",
    host_env: dict[str, str] | None = None,
) -> list[str]:
    driver = get_driver(driver_id)
    settings = config.driver_settings(driver.id)
    host_env = host_env or {}
    workspace = (
        devcontainer.workspace_folder if devcontainer and devcontainer.workspace_folder else None
    )
    workspace = workspace or settings.workspace_folder
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
        "-v",
        f"{run_repo.resolve()}:{workspace}{run_repo_suffix}",
    ]
    for key, value in driver.container_env(settings, host_env).items():
        args.extend(["-e", f"{key}={value}"])
    for mount in driver.state_mounts(settings, host_env):
        suffix = volume_suffix(config.selinux, shared=mount.shared)
        args.extend(["-v", f"{mount.source.expanduser().resolve()}:{mount.target}{suffix}"])
    if devcontainer:
        for key, value in devcontainer.env.items():
            args.extend(["-e", f"{key}={value}"])
        for mount in devcontainer.mounts:
            args.extend(["--mount", mount])
        args.extend(devcontainer.run_args)
    args.extend([image, "bash", "-lc", command])
    return args


def ensure_state_mounts(config: Config, driver_id: str, host_env: dict[str, str]) -> None:
    driver = get_driver(driver_id)
    settings = config.driver_settings(driver.id)
    for mount in driver.state_mounts(settings, host_env):
        source = mount.source.expanduser()
        if source.exists():
            continue
        if not mount.required:
            continue
        if mount.target == "/kilo-host/KILO_CONFIG":
            raise RuntimeError(f"KILO_CONFIG points to missing file: {source}")
        source.mkdir(parents=True, exist_ok=True)


def volume_suffix(mode: str, *, shared: bool = False) -> str:
    if mode == "disabled":
        return ""
    if mode in {"z", "Z"}:
        return f":{mode}"
    if mode == "auto" and Path("/sys/fs/selinux").exists():
        return ":z" if shared else ":Z"
    return ""
