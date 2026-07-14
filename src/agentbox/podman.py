from __future__ import annotations

from pathlib import Path
import hashlib
import shlex
import subprocess

from .config import Config
from .devcontainer import Devcontainer
from .drivers import MountSpec, get_driver


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
    return config.repo_root / ".agentbox" / driver.id / "Containerfile"


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
    for key, value in driver.env(settings, host_env).items():
        args.extend(["-e", f"{key}={value}"])
    for key, value in driver.config_env(settings, host_env, config.repo_root).items():
        args.extend(["-e", f"{key}={value}"])
    mounts = validated_state_mounts(
        [
            *driver.state_mounts(settings, host_env),
            *driver.run_state_mounts(settings, host_env, run_repo.parent),
            *driver.config_mounts(settings, host_env, config.repo_root),
        ],
        workspace,
        check_sources=False,
    )
    for mount in mounts:
        args.extend(["-v", render_mount(mount, config.selinux)])
    if devcontainer:
        for key, value in devcontainer.env.items():
            args.extend(["-e", f"{key}={value}"])
        for mount in devcontainer.mounts:
            args.extend(["--mount", mount])
        args.extend(devcontainer.run_args)
    args.extend([image, "bash", "-lc", command])
    return args


def ensure_state_mounts(
    config: Config,
    driver_id: str,
    host_env: dict[str, str],
    run_repo: Path,
    workspace: str | None = None,
) -> None:
    driver = get_driver(driver_id)
    settings = config.driver_settings(driver.id)
    workspace = workspace or settings.workspace_folder
    mounts = validated_state_mounts(
        [
            *driver.state_mounts(settings, host_env),
            *driver.run_state_mounts(settings, host_env, run_repo.parent),
            *driver.config_mounts(settings, host_env, config.repo_root),
        ],
        workspace,
    )
    for mount in mounts:
        if mount.kind == "directory" and mount.create:
            mount.source.expanduser().mkdir(parents=True, exist_ok=True)


def validated_state_mounts(
    mounts: list[MountSpec], workspace: str, *, check_sources: bool = True
) -> list[MountSpec]:
    rendered: list[MountSpec] = []
    targets = {workspace}
    for mount in mounts:
        validate_mount(mount, workspace, targets)
        source = mount.source.expanduser()
        if mount.optional and not source.exists():
            continue
        if not check_sources:
            rendered.append(mount)
            continue
        if mount.kind == "file" and not source.exists():
            raise RuntimeError(f"required file mount source is missing: {source}")
        if mount.kind == "directory" and not source.exists() and not mount.create:
            raise RuntimeError(f"required directory mount source is missing: {source}")
        rendered.append(mount)
    return rendered


def validate_mount(mount: MountSpec, workspace: str, targets: set[str]) -> None:
    if not mount.target.startswith("/"):
        raise RuntimeError(f"mount target must be absolute: {mount.target}")
    normalized_target = mount.target.rstrip("/") or "/"
    normalized_workspace = workspace.rstrip("/") or "/"
    if normalized_target in {"/", normalized_workspace} or normalized_target.startswith(
        normalized_workspace + "/"
    ):
        raise RuntimeError(f"mount target interferes with workspace: {mount.target}")
    if normalized_target in targets:
        raise RuntimeError(f"duplicate mount target: {mount.target}")
    source = mount.source.expanduser()
    if source.resolve() == Path("/"):
        raise RuntimeError(f"mount source must not be root: {source}")
    targets.add(normalized_target)


def render_mount(mount: MountSpec, selinux: str) -> str:
    return f"{mount.source.expanduser().resolve()}:{mount.target}{volume_suffix_for_mount(selinux, mount)}"


def volume_suffix_for_mount(mode: str, mount: MountSpec) -> str:
    options: list[str] = []
    if mount.readonly:
        options.append("ro")
    if mount.chown:
        options.append("U")
    if mount.relabel != "none":
        suffix = volume_suffix(mode, shared=mount.relabel == "shared")
        if suffix:
            options.append(suffix.removeprefix(":"))
    return ":" + ",".join(options) if options else ""


def volume_suffix(mode: str, *, shared: bool = False) -> str:
    if mode == "disabled":
        return ""
    if mode in {"z", "Z"}:
        return f":{mode}"
    if mode == "auto" and Path("/sys/fs/selinux").exists():
        return ":z" if shared else ":Z"
    return ""
