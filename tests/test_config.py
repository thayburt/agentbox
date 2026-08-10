import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agentbox.config import load_config


class ConfigTests(unittest.TestCase):
    def test_defaults_use_agentbox_run_store(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = load_config(Path(tmp))
            self.assertEqual(config.run_store, Path(tmp) / ".agentbox" / "runs")

    def test_codex_home_prefers_environment(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ, {"CODEX_HOME": "/tmp/codex-home"}
        ):
            config = load_config(Path(tmp))
            self.assertEqual(
                config.driver_settings("codex").codex_home,
                Path("/tmp/codex-home"),
            )

    def test_kilo_defaults_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = load_config(Path(tmp))
            settings = config.driver_settings("kilo")

            self.assertEqual(settings.image_name, "agentbox-kilo")
            self.assertEqual(settings.base_image, "ubuntu:24.04")
            self.assertEqual(settings.workspace_folder, "/workspace")

    def test_run_store_rejects_filesystem_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "agentbox.toml").write_text('[runtime]\nrun_store = "/"\n')

            with self.assertRaisesRegex(RuntimeError, "filesystem root"):
                load_config(root)

    def test_git_identity_defaults_to_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = load_config(Path(tmp))
            self.assertIsNone(config.git_user_name)
            self.assertIsNone(config.git_user_email)
            self.assertFalse(config.sign_imports)

    def test_git_identity_loads_from_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "agentbox.toml").write_text(
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

    def test_rejects_invalid_section_shapes_and_unknown_sections(self):
        cases = (
            ("runtime = 'bad'", "runtime must be a table"),
            ("[unknown]", "unknown is not a valid section"),
            ("[kilocode]", "kilocode is not a valid section"),
        )
        for contents, message in cases:
            with self.subTest(contents=contents), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / "agentbox.toml").write_text(contents)
                with self.assertRaisesRegex(ValueError, message):
                    load_config(root)

    def test_rejects_unknown_and_wrongly_typed_values(self):
        cases = (
            ("[runtime]\nunknown = 'x'", "runtime.unknown is not a valid setting"),
            ("[runtime]\nrun_store = false", "runtime.run_store must be a string"),
            ("[runtime]\nselinux = 'invalid'", "runtime.selinux must be one of"),
            ("[git]\nsign_imports = 'false'", "git.sign_imports must be a boolean"),
            ("[codex]\nimage_name = 1", "codex.image_name must be a string"),
            ("[kilo]\nunknown = 'x'", "kilo.unknown is not a valid setting"),
        )
        for contents, message in cases:
            with self.subTest(contents=contents), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / "agentbox.toml").write_text(contents)
                with self.assertRaisesRegex(ValueError, message):
                    load_config(root)


if __name__ == "__main__":
    unittest.main()
