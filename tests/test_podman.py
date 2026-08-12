import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from agentbox import podman, seed
from agentbox.config import Config
from agentbox.drivers import CodexSettings, MountSpec, RunSeedDirectorySpec, get_driver
from agentbox.podman import render_run_command, volume_suffix


class PodmanTests(unittest.TestCase):
    def setUp(self):
        # Tests in this class materialize Containerfiles; keep real digest
        # resolution (and podman itself) out of them by treating every base
        # image reference as already resolved.
        patcher = mock.patch(
            "agentbox.podman.resolve_pinned_base_image", side_effect=lambda ref: ref
        )
        patcher.start()
        self.addCleanup(patcher.stop)

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
                image="agentbox-codex:test",
                run_repo=run_repo,
                command="exec bash",
                driver_id="codex",
            )
            self.assertIn("--userns=keep-id", cmd)
            self.assertIn(f"{codex_home.resolve()}:/codex-home", cmd)
            self.assertIn(f"{run_repo.resolve()}:/workspace", cmd)
            self.assertNotIn(str(root) + ":/workspace", cmd)

    def test_render_run_command_hardens_container(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_repo = root / "run" / "repo"
            run_repo.mkdir(parents=True)

            cmd = render_run_command(
                config=self.config(root),
                image="agentbox-codex:test",
                run_repo=run_repo,
                command="exec bash",
                driver_id="codex",
            )

            self.assertIn("--cap-drop=ALL", cmd)
            self.assertEqual(cmd.count("--security-opt=no-new-privileges"), 1)
            self.assertEqual(
                cmd[:9],
                [
                    "podman",
                    "run",
                    "--rm",
                    "-it",
                    "--userns=keep-id",
                    "--cap-drop=ALL",
                    "--security-opt=no-new-privileges",
                    "--workdir",
                    "/workspace",
                ],
            )
            self.assertFalse(any(arg.startswith("--cap-add=") for arg in cmd))

    def test_render_run_command_adds_configured_security_options_in_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_repo = root / "run" / "repo"
            run_repo.mkdir(parents=True)
            config = replace(
                self.config(root),
                security_options=("unmask=ALL", "seccomp=/path with spaces/profile.json"),
            )

            cmd = render_run_command(
                config=config,
                image="agentbox-codex:test",
                run_repo=run_repo,
                command="exec bash",
                driver_id="codex",
            )

            self.assertEqual(
                [arg for arg in cmd if arg.startswith("--security-opt=")],
                [
                    "--security-opt=no-new-privileges",
                    "--security-opt=unmask=ALL",
                    "--security-opt=seccomp=/path with spaces/profile.json",
                ],
            )
            self.assertLess(
                cmd.index("--security-opt=seccomp=/path with spaces/profile.json"),
                cmd.index("-e"),
            )

    def test_render_run_command_deduplicates_all_security_options(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_repo = root / "run" / "repo"
            run_repo.mkdir(parents=True)
            config = replace(
                self.config(root),
                security_options=(
                    "no-new-privileges",
                    "label=disable",
                    "label=disable",
                    "unmask=ALL",
                ),
            )

            cmd = render_run_command(
                config=config,
                image="agentbox-codex:test",
                run_repo=run_repo,
                command="exec bash",
                driver_id="codex",
            )

            self.assertEqual(
                [arg for arg in cmd if arg.startswith("--security-opt=")],
                [
                    "--security-opt=no-new-privileges",
                    "--security-opt=label=disable",
                    "--security-opt=unmask=ALL",
                ],
            )

    def test_render_run_command_rejects_disabling_no_new_privileges(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_repo = root / "run" / "repo"
            run_repo.mkdir(parents=True)

            for separator in ("=", ":"):
                for value in ("0", "f", "F", "false", "False", "FALSE"):
                    option = f"no-new-privileges{separator}{value}"
                    config = replace(self.config(root), security_options=(option,))
                    with self.subTest(option=option), self.assertRaisesRegex(
                        ValueError, "cannot disable mandatory no-new-privileges"
                    ):
                        render_run_command(
                            config=config,
                            image="agentbox-codex:test",
                            run_repo=run_repo,
                            command="exec bash",
                            driver_id="codex",
                        )

    def test_render_run_command_adds_configured_capabilities(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_repo = root / "run" / "repo"
            run_repo.mkdir(parents=True)
            config = replace(
                self.config(root), capabilities=("SYS_ADMIN", "SYS_CHROOT")
            )

            cmd = render_run_command(
                config=config,
                image="agentbox-codex:test",
                run_repo=run_repo,
                command="exec bash",
                driver_id="codex",
            )

            self.assertIn("--cap-drop=ALL", cmd)
            self.assertIn("--cap-add=SYS_ADMIN", cmd)
            self.assertIn("--cap-add=SYS_CHROOT", cmd)

    def test_volume_suffix(self):
        self.assertEqual(volume_suffix("disabled"), "")
        self.assertEqual(volume_suffix("z"), ":z")
        self.assertEqual(volume_suffix("Z"), ":Z")

    def test_volume_suffix_auto_shared_vs_private(self):
        with mock.patch("agentbox.podman._selinux_enabled", return_value=True):
            self.assertEqual(volume_suffix("auto", shared=True), ":z")
            self.assertEqual(volume_suffix("auto", shared=False), ":Z")
        with mock.patch("agentbox.podman._selinux_enabled", return_value=False):
            self.assertEqual(volume_suffix("auto", shared=True), "")
            self.assertEqual(volume_suffix("auto", shared=False), "")

    def test_render_run_command_uses_shared_label_for_codex_home(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = replace(self.config(root), selinux="auto")
            codex_home = config.driver_settings("codex").codex_home
            codex_home.mkdir(parents=True, exist_ok=True)
            run_repo = root / "run" / "repo"
            run_repo.mkdir(parents=True)
            with mock.patch("agentbox.podman._selinux_enabled", return_value=True):
                cmd = render_run_command(
                    config=config,
                    image="agentbox-codex:test",
                    run_repo=run_repo,
                    command="exec bash",
                    driver_id="codex",
                )
            self.assertIn(f"{codex_home.resolve()}:/codex-home:z", cmd)
            self.assertIn(f"{run_repo.resolve()}:/workspace:Z", cmd)

    def test_ensure_harness_containerfile_writes_default_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self.config(Path(tmp))
            path = podman.ensure_harness_containerfile(config, driver_id="codex")
            original = path.read_text()

            path.write_text("custom\n")
            podman.ensure_harness_containerfile(config, driver_id="codex")

            codex = get_driver("codex")
            self.assertEqual(original, codex.default_containerfile(config.driver_settings("codex")))
            self.assertEqual(path.read_text(), "custom\n")

    def test_ensure_kilo_containerfile_writes_default_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self.config(Path(tmp))
            path = podman.ensure_harness_containerfile(config, driver_id="kilo")
            original = path.read_text()

            path.write_text("custom\n")
            podman.ensure_harness_containerfile(config, driver_id="kilo")

            self.assertEqual(path, Path(tmp) / ".agentbox" / "kilo" / "Containerfile")
            kilo = get_driver("kilo")
            self.assertEqual(original, kilo.default_containerfile(config.driver_settings("kilo")))
            self.assertEqual(path.read_text(), "custom\n")

    def test_content_changes_produce_different_managed_image_tags(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self.config(Path(tmp))
            path = podman.ensure_harness_containerfile(config, driver_id="codex")
            first = podman.current_managed_image(config, driver_id="codex")

            path.write_text(path.read_text() + "\nRUN true\n")
            second = podman.current_managed_image(config, driver_id="codex")

            self.assertNotEqual(first, second)
            self.assertTrue(first.startswith("agentbox-codex:"))

    def test_build_image_skips_existing_managed_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self.config(Path(tmp))
            podman.ensure_harness_containerfile(config, driver_id="codex")
            with (
                mock.patch("agentbox.podman.image_exists", return_value=True),
                mock.patch("agentbox.podman.subprocess.run") as run,
            ):
                podman.build_image(config, driver_id="codex")

            run.assert_not_called()

    def test_build_image_uses_agentbox_containerfile_and_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self.config(Path(tmp))
            podman.ensure_harness_containerfile(config, driver_id="codex")
            with (
                mock.patch("agentbox.podman.image_exists", return_value=False),
                mock.patch("agentbox.podman.subprocess.run") as run,
            ):
                podman.build_image(config, driver_id="codex")

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

            path = podman.harness_containerfile_path(config, driver_id="kilocode")

            self.assertEqual(path, Path(tmp) / ".agentbox" / "kilo" / "Containerfile")

    def test_build_image_force_rebuilds_existing_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self.config(Path(tmp))
            podman.ensure_harness_containerfile(config, driver_id="codex")
            with (
                mock.patch("agentbox.podman.image_exists", return_value=True),
                mock.patch("agentbox.podman.subprocess.run") as run,
            ):
                podman.build_image(config, force=True, driver_id="codex")

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
                images = podman.list_managed_images(config, driver_id="codex")

            self.assertEqual(images, ["agentbox-codex:aaa", "localhost/agentbox-codex:bbb"])

    def test_list_managed_images_filters_by_driver_image_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self.config(Path(tmp))
            completed = subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout=("agentbox-codex:same\nlocalhost/agentbox-kilo:same\nagentbox-kilo:other\n"),
                stderr="",
            )
            with mock.patch("agentbox.podman.run", return_value=completed):
                images = podman.list_managed_images(config, driver_id="kilo")

            self.assertEqual(images, ["agentbox-kilo:other", "localhost/agentbox-kilo:same"])

    def test_copy_image_directory_uses_temporary_container_and_atomic_destination(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "run" / "home"
            spec = RunSeedDirectorySpec("/home/ubuntu", destination, "Kilo home")

            def fake_run(args, check=True):
                if args[1] == "create":
                    return subprocess.CompletedProcess(args, 0, stdout="container-id\n", stderr="")
                if args[1] == "cp":
                    Path(args[-1], ".profile").write_text("image profile\n")
                return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

            with mock.patch("agentbox.seed.podman.run", side_effect=fake_run) as run_mock:
                seed.copy_image_directory("agentbox-kilo:test", spec)

            self.assertEqual((destination / ".profile").read_text(), "image profile\n")
            calls = [call.args[0] for call in run_mock.call_args_list]
            self.assertEqual(
                calls[0],
                [
                    "podman",
                    "create",
                    "--userns=keep-id",
                    "--image-volume=ignore",
                    "agentbox-kilo:test",
                    "true",
                ],
            )
            self.assertEqual(
                calls[1][0:4],
                ["podman", "cp", "--archive=false", "container-id:/home/ubuntu/."],
            )
            self.assertEqual(calls[2], ["podman", "rm", "container-id"])
            self.assertEqual(list(destination.parent.glob(".home-*")), [])

    def test_copy_image_directory_cleans_up_after_copy_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "run" / "home"
            spec = RunSeedDirectorySpec("/home/ubuntu", destination, "Kilo home")

            def fake_run(args, check=True):
                if args[1] == "create":
                    return subprocess.CompletedProcess(args, 0, stdout="container-id\n", stderr="")
                if args[1] == "cp":
                    raise subprocess.CalledProcessError(1, args)
                return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

            with mock.patch("agentbox.seed.podman.run", side_effect=fake_run) as run_mock, (
                self.assertRaises(subprocess.CalledProcessError)
            ):
                seed.copy_image_directory("agentbox-kilo:test", spec)

            self.assertFalse(destination.exists())
            self.assertEqual(list(destination.parent.glob(".home-*")), [])
            self.assertEqual(
                run_mock.call_args_list[-1].args[0],
                ["podman", "rm", "--force", "container-id"],
            )

    def test_copy_image_directory_forces_cleanup_after_remove_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "run" / "home"
            spec = RunSeedDirectorySpec("/home/ubuntu", destination, "Kilo home")

            def fake_run(args, check=True):
                if args[1] == "create":
                    return subprocess.CompletedProcess(args, 0, stdout="container-id\n", stderr="")
                returncode = 1 if args == ["podman", "rm", "container-id"] else 0
                return subprocess.CompletedProcess(args, returncode, stdout="", stderr="")

            with mock.patch("agentbox.seed.podman.run", side_effect=fake_run) as run_mock, (
                self.assertRaisesRegex(RuntimeError, "could not remove temporary container")
            ):
                seed.copy_image_directory("agentbox-kilo:test", spec)

            self.assertFalse(destination.exists())
            self.assertEqual(
                run_mock.call_args_list[-1].args[0],
                ["podman", "rm", "--force", "container-id"],
            )

    def test_copy_image_directory_preserves_existing_destination(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "run" / "home"
            destination.mkdir(parents=True)
            marker = destination / "state"
            marker.write_text("existing\n")
            spec = RunSeedDirectorySpec("/home/ubuntu", destination, "Kilo home")

            with mock.patch("agentbox.seed.podman.run") as run_mock:
                seed.copy_image_directory("agentbox-kilo:test", spec)

            run_mock.assert_not_called()
            self.assertEqual(marker.read_text(), "existing\n")

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
                image="agentbox-kilo:test",
                run_repo=run_repo,
                command="exec kilo status",
                driver_id="kilo",
                host_env=host_env,
            )

            self.assertIn("HOME=/home/ubuntu", cmd)
            self.assertNotIn(f"{config_home / 'kilo'}:/home/ubuntu/.config/kilo", cmd)
            self.assertIn(f"{data_home / 'kilo'}:/home/ubuntu/.local/share/kilo:U", cmd)
            self.assertNotIn(str(state_home), cmd)
            self.assertIn(f"{run_repo.parent / 'home'}:/home/ubuntu:U", cmd)
            self.assertFalse(any(str(cache_home) in item for item in cmd))
            self.assertIn("XDG_CACHE_HOME=/home/ubuntu/.cache", cmd)
            self.assertLess(
                cmd.index(f"{run_repo.parent / 'home'}:/home/ubuntu:U"),
                cmd.index(f"{data_home / 'kilo'}:/home/ubuntu/.local/share/kilo:U"),
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
            global_mount = next(
                mount for mount in mounts if mount.target == "/home/ubuntu/.config/kilo"
            )

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
            self.assertFalse((home / ".local" / "state" / "kilo").exists())
            self.assertFalse((home / ".cache" / "kilo").exists())
            self.assertTrue((run_repo.parent / "home").is_dir())
            self.assertTrue((run_repo.parent / "home" / ".local" / "share").is_dir())
            self.assertFalse((run_repo.parent / "home" / ".local" / "state").exists())

    def test_ensure_state_mounts_creates_nested_target_parent_in_closest_mount(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outer = MountSpec(root / "home", "/home/ubuntu", "directory", create=True)
            middle = MountSpec(
                root / "share", "/home/ubuntu/.local/share", "directory", create=True
            )
            inner = MountSpec(root / "data", "/home/ubuntu/.local/share/kilo/data", "directory")

            podman.ensure_nested_mount_parents([outer, middle, inner])

            self.assertTrue((outer.source / ".local").is_dir())
            self.assertTrue((middle.source / "kilo").is_dir())
            self.assertFalse((outer.source / ".local" / "share" / "kilo").exists())

    def test_ensure_state_mounts_creates_required_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = self.config(root)

            podman.ensure_state_mounts(config, "codex", {}, root / "runs" / "run" / "repo")

            self.assertTrue(config.driver_settings("codex").codex_home.is_dir())

    def test_dry_run_reports_base_image_tag_and_defers_managed_image_tag(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = self.config(root)
            stdout = io.StringIO()

            with contextlib.redirect_stdout(stdout):
                image = podman.current_managed_image(config, dry_run=True, driver_id="codex")

            self.assertEqual(image, "agentbox-codex:<containerfile-digest>")
            self.assertIn("base image 'ubuntu:24.04'", stdout.getvalue())
            self.assertFalse(podman.harness_containerfile_path(config, driver_id="codex").exists())

    def test_kilo_run_state_mount_renders_before_its_source_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_repo = root / "runs" / "run" / "repo"

            cmd = render_run_command(
                config=self.config(root),
                image="agentbox-kilo:test",
                run_repo=run_repo,
                command="exec kilo",
                driver_id="kilo",
            )

            home = run_repo.parent / "home"
            self.assertFalse(home.exists())
            self.assertIn(f"{home.resolve()}:/home/ubuntu:U", cmd)

    def test_kilo_run_state_mount_includes_selinux_suffix(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_repo = root / "runs" / "run" / "repo"
            config = replace(self.config(root), selinux="auto")

            with mock.patch("agentbox.podman._selinux_enabled", return_value=True):
                cmd = render_run_command(
                    config=config,
                    image="agentbox-kilo:test",
                    run_repo=run_repo,
                    command="exec kilo",
                    driver_id="kilo",
                )

            self.assertIn(
                f"{run_repo.parent / 'home'}:/home/ubuntu:U,Z",
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

    def test_mount_target_with_colon_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            mount = MountSpec(Path(tmp), "/sta:e", "directory")

            with self.assertRaisesRegex(RuntimeError, "must not contain"):
                podman.validated_state_mounts([mount], "/workspace")

    def test_mount_source_with_colon_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            mount = MountSpec(Path(tmp) / "co:lon", "/state", "directory")

            with self.assertRaisesRegex(RuntimeError, "must not contain"):
                podman.validated_state_mounts([mount], "/workspace")

    def test_mount_target_dotdot_evasion_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            mount = MountSpec(Path(tmp), "/other/../workspace", "directory")

            with self.assertRaisesRegex(RuntimeError, "interferes with workspace"):
                podman.validated_state_mounts([mount], "/workspace")

    def test_duplicate_targets_rejected_after_normalization(self):
        with tempfile.TemporaryDirectory() as tmp:
            mounts = [
                MountSpec(Path(tmp), "/state", "directory"),
                MountSpec(Path(tmp), "/state/./", "directory"),
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
            selinux="disabled",
            git_user_name=None,
            git_user_email=None,
            sign_imports=False,
            harnesses={
                "codex": codex_settings,
                "kilo": get_driver("kilo").default_settings({}),
            },
        )


class BaseImagePinningTests(unittest.TestCase):
    INSTANCE_DIGEST = "sha256:" + "1" * 64
    LIST_DIGEST = "sha256:" + "2" * 64
    OTHER_LIST_DIGEST = "sha256:" + "3" * 64

    def test_prepinned_references_are_used_verbatim(self):
        references = (
            f"ubuntu:24.04@{self.LIST_DIGEST}",
            f"registry.example.com/library/ubuntu@{self.LIST_DIGEST}",
        )
        with mock.patch("agentbox.podman.run") as run_mock:
            for reference in references:
                with self.subTest(reference=reference):
                    self.assertEqual(podman.resolve_pinned_base_image(reference), reference)
        run_mock.assert_not_called()

    def test_prefers_manifest_list_digest_over_instance_digest(self):
        payload = [
            {
                "Digest": self.INSTANCE_DIGEST,
                "RepoDigests": [
                    f"docker.io/library/ubuntu@{self.LIST_DIGEST}",
                    f"docker.io/library/ubuntu@{self.INSTANCE_DIGEST}",
                ],
            }
        ]
        with mock.patch("agentbox.podman.run", side_effect=self.fake_run(payload)) as run_mock:
            resolved = self.resolve("ubuntu:24.04")

        self.assertEqual(resolved, f"ubuntu:24.04@{self.LIST_DIGEST}")
        self.assertEqual(run_mock.call_args_list[0].args[0], ["podman", "pull", "ubuntu:24.04"])

    def test_prefers_digest_from_requested_repository(self):
        payload = [
            {
                "Digest": self.INSTANCE_DIGEST,
                "RepoDigests": [
                    f"mirror.example.com/ubuntu@{self.OTHER_LIST_DIGEST}",
                    f"docker.io/library/ubuntu@{self.LIST_DIGEST}",
                    f"docker.io/library/ubuntu@{self.INSTANCE_DIGEST}",
                ],
            }
        ]
        with mock.patch("agentbox.podman.run", side_effect=self.fake_run(payload)):
            resolved = self.resolve("ubuntu:24.04")

        self.assertEqual(resolved, f"ubuntu:24.04@{self.LIST_DIGEST}")

    def test_single_arch_image_uses_its_manifest_digest(self):
        payload = [
            {
                "Digest": self.INSTANCE_DIGEST,
                "RepoDigests": [f"docker.io/library/ubuntu@{self.INSTANCE_DIGEST}"],
            }
        ]
        with mock.patch("agentbox.podman.run", side_effect=self.fake_run(payload)):
            resolved = self.resolve("ubuntu:24.04")

        self.assertEqual(resolved, f"ubuntu:24.04@{self.INSTANCE_DIGEST}")

    def test_failed_pull_still_pins_from_local_image(self):
        payload = [
            {
                "Digest": self.INSTANCE_DIGEST,
                "RepoDigests": [f"docker.io/library/ubuntu@{self.INSTANCE_DIGEST}"],
            }
        ]
        stderr = io.StringIO()
        with (
            mock.patch("agentbox.podman.run", side_effect=self.fake_run(payload, pull_rc=1)),
            contextlib.redirect_stderr(stderr),
        ):
            resolved = podman.resolve_pinned_base_image("ubuntu:24.04")

        self.assertEqual(resolved, f"ubuntu:24.04@{self.INSTANCE_DIGEST}")
        self.assertIn("using a cached registry digest", stderr.getvalue())

    def test_missing_podman_yields_no_pin(self):
        with mock.patch("agentbox.podman.run", side_effect=FileNotFoundError("podman")):
            self.assertIsNone(self.resolve("ubuntu:24.04"))

    def test_locally_built_image_yields_no_pin(self):
        payload = [{"Digest": self.INSTANCE_DIGEST, "RepoDigests": []}]
        with mock.patch("agentbox.podman.run", side_effect=self.fake_run(payload, pull_rc=1)):
            self.assertIsNone(self.resolve("localhost/custom-base:dev"))

    def test_uninspectable_image_yields_no_pin(self):
        with mock.patch("agentbox.podman.run", side_effect=self.fake_run([], inspect_rc=1)):
            self.assertIsNone(self.resolve("example.com/missing:latest"))

    def test_ambiguous_inspect_result_yields_no_pin(self):
        payload = [
            {"RepoDigests": [f"docker.io/library/ubuntu@{self.LIST_DIGEST}"]},
            {"RepoDigests": [f"docker.io/library/ubuntu@{self.INSTANCE_DIGEST}"]},
        ]
        with mock.patch("agentbox.podman.run", side_effect=self.fake_run(payload)):
            self.assertIsNone(self.resolve("ubuntu"))

    def test_malformed_digest_yields_no_pin(self):
        payload = [{"Digest": "<missing>", "RepoDigests": ["docker.io/library/ubuntu@<missing>"]}]
        with mock.patch("agentbox.podman.run", side_effect=self.fake_run(payload)):
            self.assertIsNone(self.resolve("ubuntu:24.04"))

    def test_malformed_inspection_inner_fields_yield_no_pin(self):
        payloads = (
            [{"Digest": 1, "RepoDigests": []}],
            [{"Digest": self.INSTANCE_DIGEST, "RepoDigests": "not-a-list"}],
            [{"Digest": self.INSTANCE_DIGEST, "RepoDigests": [1]}],
        )
        for payload in payloads:
            with self.subTest(payload=payload), mock.patch(
                "agentbox.podman.run", side_effect=self.fake_run(payload)
            ):
                self.assertIsNone(self.resolve("ubuntu:24.04"))

    def test_materialized_containerfile_contains_pinned_from_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self.config(Path(tmp))
            with mock.patch(
                "agentbox.podman.resolve_pinned_base_image",
                return_value=f"ubuntu:24.04@{self.LIST_DIGEST}",
            ):
                path = podman.ensure_harness_containerfile(config, driver_id="codex")

            self.assertEqual(
                path.read_text().splitlines()[0],
                f"FROM ubuntu:24.04@{self.LIST_DIGEST}",
            )

    def test_materialized_containerfile_falls_back_unpinned_with_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self.config(Path(tmp))
            stderr = io.StringIO()
            with (
                mock.patch("agentbox.podman.resolve_pinned_base_image", return_value=None),
                contextlib.redirect_stderr(stderr),
            ):
                path = podman.ensure_harness_containerfile(config, driver_id="codex")

            self.assertEqual(path.read_text().splitlines()[0], "FROM ubuntu:24.04")
            self.assertIn("ubuntu:24.04", stderr.getvalue())
            self.assertIn("unpinned", stderr.getvalue())

    @staticmethod
    def fake_run(inspect_payload, *, inspect_rc=0, pull_rc=0):
        def fake(args, check=True):
            if args[:3] == ["podman", "image", "inspect"]:
                stdout = json.dumps(inspect_payload) if inspect_rc == 0 else ""
                return subprocess.CompletedProcess(args, inspect_rc, stdout=stdout, stderr="")
            return subprocess.CompletedProcess(args, pull_rc, stdout="", stderr="")

        return fake

    @staticmethod
    def resolve(base_image: str) -> str | None:
        with contextlib.redirect_stderr(io.StringIO()):
            return podman.resolve_pinned_base_image(base_image)

    def config(self, root: Path) -> Config:
        return Config(
            repo_root=root,
            run_store=root / "runs",
            selinux="disabled",
            git_user_name=None,
            git_user_email=None,
            sign_imports=False,
            harnesses={
                "codex": CodexSettings(
                    image_name="agentbox-codex",
                    base_image="ubuntu:24.04",
                    codex_home=root / "codex-home",
                    workspace_folder="/workspace",
                ),
            },
        )


if __name__ == "__main__":
    unittest.main()
