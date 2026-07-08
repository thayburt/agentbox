from pathlib import Path
import tempfile
import unittest

from agent_containers.config import Config
from agent_containers.podman import render_run_command, volume_suffix


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
                image_name="agentc-codex",
                base_image="ubuntu:24.04",
                codex_home=codex_home,
                workspace_folder="/workspace",
                selinux="disabled",
            )
            cmd = render_run_command(
                config=config,
                devcontainer=None,
                image="agentc-codex:test",
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


if __name__ == "__main__":
    unittest.main()
