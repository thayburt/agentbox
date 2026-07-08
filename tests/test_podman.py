from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from agentbox.config import Config
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
            config = Config(
                repo_root=root,
                run_store=root / "runs",
                devcontainer=None,
                image_name="agentbox-codex",
                base_image="ubuntu:24.04",
                codex_home=codex_home,
                workspace_folder="/workspace",
                selinux="disabled",
                git_user_name=None,
                git_user_email=None,
                sign_imports=False,
            )
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
            self.assertIn(str(Path(tmp) / ".agentbox" / "codex.Containerfile"), cmd)
            self.assertEqual(cmd[-1], str(Path(tmp) / ".agentbox"))

    def config(self, root: Path) -> Config:
        return Config(
            repo_root=root,
            run_store=root / "runs",
            devcontainer=None,
            image_name="agentbox-codex",
            base_image="ubuntu:24.04",
            codex_home=root / "codex-home",
            workspace_folder="/workspace",
            selinux="disabled",
            git_user_name=None,
            git_user_email=None,
            sign_imports=False,
        )


if __name__ == "__main__":
    unittest.main()
