from pathlib import Path
import os
import tempfile
import unittest
from unittest import mock

from agent_containers.config import default_toml, load_config


class ConfigTests(unittest.TestCase):
    def test_defaults_use_agentc_run_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = load_config(Path(tmp))
            self.assertEqual(config.run_store, Path(tmp) / ".agentc" / "runs")

    def test_codex_home_prefers_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"CODEX_HOME": "/tmp/codex-home"}):
                config = load_config(Path(tmp))
                self.assertEqual(config.codex_home, Path("/tmp/codex-home"))
                self.assertIn('codex_home = "/tmp/codex-home"', default_toml())

    def test_git_identity_defaults_to_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = load_config(Path(tmp))
            self.assertIsNone(config.git_user_name)
            self.assertIsNone(config.git_user_email)
            self.assertFalse(config.sign_imports)

    def test_git_identity_loads_from_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "agent-containers.toml").write_text(
                """
[git]
user_name = "Agent User"
user_email = "agent@example.com"
sign_imports = true
"""
            )
            config = load_config(root)
            self.assertEqual(config.git_user_name, "Agent User")
            self.assertEqual(config.git_user_email, "agent@example.com")
            self.assertTrue(config.sign_imports)


if __name__ == "__main__":
    unittest.main()
