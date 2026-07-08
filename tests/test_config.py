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


if __name__ == "__main__":
    unittest.main()

