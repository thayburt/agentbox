from dataclasses import replace
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from agentbox.config import Config
from agentbox.drivers import CodexSettings, MountSpec, get_driver
from agentbox import podman
from agentbox.podman import render_run_command, volume_suffix


class PodmanTests(unittest.TestCase):
    def test_render_run_command_mounts_clone_and_codex_home(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            codex_home = root / "codex-home"
            run_repo = root / "run" / "repo"
            codex_home.mkdir()
            run_repo.mkdir(parents=True)
            config = self.config(root, codex_home=codex_home)
            cmd = render_run_command(
                config=config,
                devcontainer=None,
                image="agentbox-codex:test",
                run_repo=run_repo,
                command="exec bash",
            )
            self.assertIn("--userns=keep-id", cmd)
            self.assertIn(f"{codex_home.resolve()}:/codex-home", cmd)
            self.assertIn(f"{run_repo.resolve()}:/workspace", cmd)
            self.assertNotIn(str(root) + ":/workspace", cmd)

    def test_volume_suffix(self):
        self.assertEqual(volume_suffix("disabled"), "")
        self.assertEqual(volume_suffix("z"), ":z")
        self.assertEqual(volume_suffix("Z"), ":Z")

    def test_volume_suffix_auto_shared_vs_private(self):
        with mock.patch("agentbox.podman.Path") as path_cls:
            path_cls.return_value.exists.return_value = True
            self.assertEqual(volume_suffix("auto", shared=True), ":z")
            self.assertEqual(volume_suffix("auto", shared=False), ":Z")
        with mock.patch("agentbox.podman.Path") as path_cls:
            path_cls.return_value.exists.return_value = False
            self.assertEqual(volume_suffix("auto", shared=True), "")
            self.assertEqual(volume_suffix("auto", shared=False), "")

    def test_render_run_command_uses_shared_label_for_codex_home(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = replace(self.config(root), selinux="auto")
            codex_home = config.codex_home
            codex_home.mkdir(parents=True, exist_ok=True)
            run_repo = root / "run" / "repo"
            run_repo.mkdir(parents=True)
            # config/run paths are passed in as real Path objects; only
            # volume_suffix's /sys/fs/selinux check goes through podman.Path.
            with mock.patch("agentbox.podman.Path") as path_cls:
                path_cls.return_value.exists.return_value = True
                cmd = render_run_command(
                    config=config,
                    devcontainer=None,
                    image="agentbox-codex:test",
                    run_repo=run_repo,
                    command="exec bash",
                )
            self.assertIn(f"{codex_home.resolve()}:/codex-home:z", cmd)
            self.assertIn(f"{run_repo.resolve()}:/workspace:Z", cmd)

    def test_ensure_harness_containerfile_writes_default_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self.config(Path(tmp))
            path = podman.ensure_harness_containerfile(config)
            original = path.read_text()

            path.write_text("custom\n")
            podman.ensure_harness_containerfile(config)

            self.assertIn("FROM ubuntu:24.04", original)
            self.assertEqual(path.read_text(), "custom\n")

    def test_ensure_kilo_containerfile_writes_default_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self.config(Path(tmp))
            path = podman.ensure_harness_containerfile(config, driver_id="kilo")
            original = path.read_text()

            path.write_text("custom\n")
            podman.ensure_harness_containerfile(config, driver_id="kilo")

            self.assertEqual(path, Path(tmp) / ".agentbox" / "kilo" / "Containerfile")
            self.assertIn("npm install -g @kilocode/cli", original)
            self.assertIn("kilo --version", original)
            self.assertIn("USER ubuntu", original)
            self.assertEqual(path.read_text(), "custom\n")

    def test_content_changes_produce_different_managed_image_tags(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self.config(Path(tmp))
            path = podman.ensure_harness_containerfile(config)
            first = podman.current_managed_image(config)

            path.write_text(path.read_text() + "\nRUN true\n")
            second = podman.current_managed_image(config)

            self.assertNotEqual(first, second)
            self.assertTrue(first.startswith("agentbox-codex:"))

    def test_build_image_skips_existing_managed_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self.config(Path(tmp))
            podman.ensure_harness_containerfile(config)
            with mock.patch("agentbox.podman.image_exists", return_value=True), mock.patch(
                "agentbox.podman.subprocess.run"
            ) as run:
                podman.build_image(config, None)

            run.assert_not_called()

    def test_build_image_uses_agentbox_containerfile_and_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self.config(Path(tmp))
            podman.ensure_harness_containerfile(config)
            with mock.patch("agentbox.podman.image_exists", return_value=False), mock.patch(
                "agentbox.podman.subprocess.run"
            ) as run:
                podman.build_image(config, None)

            cmd = run.call_args.args[0]
            self.assertIn("podman", cmd)
            self.assertIn("build", cmd)
            self.assertIn(str(Path(tmp) / ".agentbox" / "codex" / "Containerfile"), cmd)
            self.assertEqual(cmd[-1], str(Path(tmp) / ".agentbox"))
            containerignore = Path(tmp) / ".agentbox" / ".containerignore"
            self.assertIn("runs", containerignore.read_text().split())

    def test_harness_containerfile_path_canonicalizes_kilocode_alias(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self.config(Path(tmp))

            path = podman.harness_containerfile_path(config, "kilocode")

            self.assertEqual(path, Path(tmp) / ".agentbox" / "kilo" / "Containerfile")

    def test_build_image_force_rebuilds_existing_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self.config(Path(tmp))
            podman.ensure_harness_containerfile(config)
            with mock.patch("agentbox.podman.image_exists", return_value=True), mock.patch(
                "agentbox.podman.subprocess.run"
            ) as run:
                podman.build_image(config, None, force=True)

            cmd = run.call_args.args[0]
            self.assertIn("--pull=newer", cmd)

    def test_list_managed_images_filters_by_image_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self.config(Path(tmp))
            completed = subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout=(
                    "agentbox-codex:aaa\n"
                    "ubuntu:24.04\n"
                    "localhost/agentbox-codex:bbb\n"
                    "localhost/other:ccc\n"
                ),
                stderr="",
            )
            with mock.patch("agentbox.podman.run", return_value=completed):
                images = podman.list_managed_images(config)

            self.assertEqual(images, ["agentbox-codex:aaa", "localhost/agentbox-codex:bbb"])

    def test_list_managed_images_filters_by_driver_image_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self.config(Path(tmp))
            completed = subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout=(
                    "agentbox-codex:same\n"
                    "localhost/agentbox-kilo:same\n"
                    "agentbox-kilo:other\n"
                ),
                stderr="",
            )
            with mock.patch("agentbox.podman.run", return_value=completed):
                images = podman.list_managed_images(config, driver_id="kilo")

            self.assertEqual(images, ["agentbox-kilo:other", "localhost/agentbox-kilo:same"])

    def test_render_run_command_sets_kilo_home_env_and_mounts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_repo = root / "run" / "repo"
            run_repo.mkdir(parents=True)
            config_home = root / "xdg-config"
            data_home = root / "xdg-data"
            state_home = root / "xdg-state"
            cache_home = root / "xdg-cache"
            host_env = {
                "XDG_CONFIG_HOME": str(config_home),
                "XDG_DATA_HOME": str(data_home),
                "XDG_STATE_HOME": str(state_home),
                "XDG_CACHE_HOME": str(cache_home),
            }

            cmd = render_run_command(
                config=self.config(root),
                devcontainer=None,
                image="agentbox-kilo:test",
                run_repo=run_repo,
                command="exec kilo status",
                driver_id="kilo",
                host_env=host_env,
            )

            self.assertIn("HOME=/home/ubuntu", cmd)
            self.assertNotIn(f"{config_home / 'kilo'}:/home/ubuntu/.config/kilo", cmd)
            self.assertIn(f"{data_home / 'kilo'}:/home/ubuntu/.local/share/kilo:U", cmd)
            self.assertIn(f"{state_home / 'kilo'}:/home/ubuntu/.local/state/kilo:U", cmd)
            self.assertIn(f"{cache_home / 'kilo'}:/home/ubuntu/.cache/kilo:U", cmd)
            self.assertIn(
                f"{run_repo.parent / 'state' / 'kilo-sandbox-policy'}:"
                "/home/ubuntu/.local/state/kilo-sandbox-policy:U",
                cmd,
            )
            self.assertFalse(any(item.startswith("KILO_CONFIG_CONTENT=") for item in cmd))
            self.assertEqual(cmd[-1], "exec kilo status")

    def test_kilo_agentbox_config_mounts_from_repo_root_readonly(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agentbox_config = root / ".agentbox" / "kilo" / "kilo.jsonc"
            agentbox_config.parent.mkdir(parents=True)
            agentbox_config.write_text("{}\n")
            run_repo = root / "run" / "repo"
            run_repo.mkdir(parents=True)

            cmd = render_run_command(
                config=self.config(root),
                devcontainer=None,
                image="agentbox-kilo:test",
                run_repo=run_repo,
                command="exec kilo",
                driver_id="kilo",
                host_env={"KILO_CONFIG": str(root / "host.json")},
            )

            self.assertIn(f"{agentbox_config.resolve()}:/agentbox/config/kilo.jsonc:ro", cmd)
            self.assertIn("KILO_CONFIG=/agentbox/config/kilo.jsonc", cmd)
            self.assertNotIn("/kilo-host/KILO_CONFIG", cmd)

    def test_kilo_global_config_mounts_are_optional_and_readonly(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "home"
            config_dir = home / ".config" / "kilo"
            config_dir.mkdir(parents=True)
            driver = get_driver("kilo")
            mounts = driver.config_mounts(driver.default_settings({}), {"HOME": str(home)}, root)
            global_mount = next(mount for mount in mounts if mount.target == "/home/ubuntu/.config/kilo")

            self.assertTrue(global_mount.optional)
            self.assertTrue(global_mount.readonly)
            self.assertFalse(global_mount.create)

    def test_ensure_state_mounts_does_not_create_kilo_config_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "home"
            config = self.config(root)

            run_repo = root / "runs" / "run" / "repo"
            podman.ensure_state_mounts(config, "kilo", {"HOME": str(home)}, run_repo)

            self.assertFalse((home / ".config" / "kilo").exists())
            self.assertTrue((home / ".local" / "share" / "kilo").is_dir())
            self.assertTrue((run_repo.parent / "state" / "kilo-sandbox-policy").is_dir())

    def test_ensure_state_mounts_creates_required_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = self.config(root)

            podman.ensure_state_mounts(config, "codex", {}, root / "runs" / "run" / "repo")

            self.assertTrue(config.codex_home.is_dir())

    def test_kilo_run_state_mount_renders_before_its_source_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_repo = root / "runs" / "run" / "repo"

            cmd = render_run_command(
                config=self.config(root),
                devcontainer=None,
                image="agentbox-kilo:test",
                run_repo=run_repo,
                command="exec kilo",
                driver_id="kilo",
            )

            source = run_repo.parent / "state" / "kilo-sandbox-policy"
            self.assertFalse(source.exists())
            self.assertIn(
                f"{source.resolve()}:/home/ubuntu/.local/state/kilo-sandbox-policy:U", cmd
            )

    def test_kilo_run_state_mount_includes_selinux_suffix(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_repo = root / "runs" / "run" / "repo"
            config = replace(self.config(root), selinux="auto")

            with mock.patch("agentbox.podman.Path") as path_cls:
                path_cls.return_value.exists.return_value = True
                cmd = render_run_command(
                    config=config,
                    devcontainer=None,
                    image="agentbox-kilo:test",
                    run_repo=run_repo,
                    command="exec kilo",
                    driver_id="kilo",
                )

            self.assertIn(
                f"{run_repo.parent / 'state' / 'kilo-sandbox-policy'}:"
                "/home/ubuntu/.local/state/kilo-sandbox-policy:U,z",
                cmd,
            )

    def test_optional_missing_mounts_are_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mount = MountSpec(root / "missing", "/state", "directory", optional=True)

            self.assertEqual(podman.validated_state_mounts([mount], "/workspace"), [])

    def test_required_missing_file_mount_errors_generically(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mount = MountSpec(root / "missing.json", "/state/config.json", "file")

            with self.assertRaisesRegex(RuntimeError, "required file mount source is missing"):
                podman.validated_state_mounts([mount], "/workspace")

    def test_readonly_mount_renders_with_ro_and_selinux_suffix(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "config.json"
            source.write_text("{}\n")
            mount = MountSpec(source, "/state/config.json", "file", readonly=True)

            self.assertEqual(
                podman.render_mount(mount, "z"),
                f"{source.resolve()}:/state/config.json:ro,z",
            )

    def test_mounts_targeting_workspace_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "state"
            source.mkdir()

            for target in ("/workspace", "/workspace/state"):
                with self.subTest(target=target):
                    mount = MountSpec(source, target, "directory")
                    with self.assertRaisesRegex(RuntimeError, "interferes with workspace"):
                        podman.validated_state_mounts([mount], "/workspace")

    def test_duplicate_mount_targets_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "state"
            source.mkdir()
            mounts = [
                MountSpec(source, "/state", "directory"),
                MountSpec(source, "/state", "directory"),
            ]

            with self.assertRaisesRegex(RuntimeError, "duplicate mount target"):
                podman.validated_state_mounts(mounts, "/workspace")

    def test_host_source_root_is_rejected(self):
        mount = MountSpec(Path("/"), "/state", "directory")

        with self.assertRaisesRegex(RuntimeError, "must not be root"):
            podman.validated_state_mounts([mount], "/workspace")

    def test_kilo_config_is_required_file_mount_when_set_without_agentbox_config(self):
        driver = get_driver("kilo")
        settings = driver.default_settings({})
        with tempfile.TemporaryDirectory() as tmp:
            mounts = driver.config_mounts(settings, {"KILO_CONFIG": "/tmp/kilo.json"}, Path(tmp))
        config_mount = next(mount for mount in mounts if mount.target == "/kilo-host/KILO_CONFIG")

        self.assertEqual(config_mount.kind, "file")
        self.assertFalse(config_mount.optional)
        self.assertTrue(config_mount.readonly)

    def test_render_run_command_allows_missing_required_file_for_dry_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_repo = root / "run" / "repo"
            run_repo.mkdir(parents=True)
            cmd = render_run_command(
                config=self.config(root),
                devcontainer=None,
                image="agentbox-kilo:test",
                run_repo=run_repo,
                command="exec kilo",
                driver_id="kilo",
                host_env={"KILO_CONFIG": str(root / "missing.json")},
            )

            self.assertIn(f"{root / 'missing.json'}:/kilo-host/KILO_CONFIG:ro", cmd)

    def config(self, root: Path, codex_home: Path | None = None) -> Config:
        codex_settings = CodexSettings(
            image_name="agentbox-codex",
            base_image="ubuntu:24.04",
            codex_home=codex_home or root / "codex-home",
            workspace_folder="/workspace",
        )
        return Config(
            repo_root=root,
            run_store=root / "runs",
            devcontainer=None,
            selinux="disabled",
            git_user_name=None,
            git_user_email=None,
            sign_imports=False,
            harnesses={
                "codex": codex_settings,
                "kilo": get_driver("kilo").default_settings({}),
            },
        )


if __name__ == "__main__":
    unittest.main()
