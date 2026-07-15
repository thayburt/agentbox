import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from agentbox.config import default_toml, load_config
from agentbox.drivers import CodexSettings, KiloSettings, canonical_driver_id, get_driver


class DriverContractTests(unittest.TestCase):
    def test_registry_resolves_driver_ids_and_aliases(self):
        self.assertEqual(get_driver("codex").id, "codex")
        self.assertEqual(get_driver("kilo").id, "kilo")
        self.assertEqual(get_driver("kilocode").id, "kilo")
        self.assertEqual(canonical_driver_id("kilocode"), "kilo")

    def test_default_toml_uses_driver_sections(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            text = default_toml()

        self.assertIn("[codex]", text)
        self.assertIn("[kilo]", text)
        self.assertIn('codex_home = "~/.codex"', text)

    def test_config_loads_typed_driver_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "agentbox.toml").write_text(
                """
[codex]
codex_home = "/tmp/codex-home"
"""
            )

            config = load_config(root)

            self.assertIsInstance(config.driver_settings("codex"), CodexSettings)
            self.assertIsInstance(config.driver_settings("kilo"), KiloSettings)
            self.assertEqual(config.driver_settings("codex").codex_home, Path("/tmp/codex-home"))

    def test_codex_launch_argv_returns_argv(self):
        argv = get_driver("codex").launch_argv("/workspace", "status with spaces")

        self.assertEqual(argv[0], "codex")
        self.assertIn("status with spaces", argv)

    def test_kilo_launch_argv_returns_argv(self):
        argv = get_driver("kilo").launch_argv("/workspace", "status with spaces")

        self.assertEqual(argv, ["kilo", "status with spaces"])

    def test_run_state_mounts_are_driver_specific(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            kilo = get_driver("kilo")
            mounts = kilo.run_state_mounts(kilo.default_settings({}), {}, run_dir)

            self.assertEqual(len(mounts), 2)
            cache, policy = mounts
            self.assertEqual(cache.source, run_dir / "cache")
            self.assertEqual(cache.target, "/home/ubuntu/.cache")
            self.assertEqual(cache.kind, "directory")
            self.assertTrue(cache.create)
            self.assertTrue(cache.chown)
            self.assertFalse(cache.readonly)
            self.assertEqual(cache.relabel, "private")
            self.assertEqual(policy.source, run_dir / "state" / "kilo-sandbox-policy")
            self.assertEqual(policy.target, "/home/ubuntu/.local/state/kilo-sandbox-policy")

            codex = get_driver("codex")
            self.assertEqual(codex.run_state_mounts(codex.default_settings({}), {}, run_dir), [])

    def test_kilo_missing_state_is_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            host_env = {"HOME": str(Path(tmp) / "home")}
            driver = get_driver("kilo")
            diagnostics = driver.diagnostics(driver.default_settings(host_env), host_env, Path(tmp))

            self.assertEqual(diagnostics[0].severity, "warning")

    def test_kilo_state_mounts_and_diagnostics_exclude_host_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache_home = root / "cache"
            (cache_home / "kilo").mkdir(parents=True)
            host_env = {"HOME": str(root / "home"), "XDG_CACHE_HOME": str(cache_home)}
            driver = get_driver("kilo")

            mounts = driver.state_mounts(driver.default_settings(host_env), host_env)
            diagnostics = driver.diagnostics(driver.default_settings(host_env), host_env, root)

            self.assertFalse(any(mount.target.startswith("/home/ubuntu/.cache") for mount in mounts))
            self.assertFalse(any(mount.source == cache_home / "kilo" for mount in mounts))
            self.assertEqual(diagnostics[0].severity, "warning")
            self.assertNotIn(str(cache_home), diagnostics[0].value)

    def test_kilo_missing_required_config_file_is_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            host_env = {
                "HOME": str(Path(tmp) / "home"),
                "KILO_CONFIG": str(Path(tmp) / "missing.json"),
            }
            driver = get_driver("kilo")
            diagnostics = driver.diagnostics(driver.default_settings(host_env), host_env, Path(tmp))

            self.assertEqual(diagnostics[-1].name, "KILO_CONFIG file")
            self.assertEqual(diagnostics[-1].severity, "error")

    def test_codex_home_environment_default_is_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"CODEX_HOME": "/tmp/codex-env"}):
                config = load_config(Path(tmp))

        self.assertEqual(config.driver_settings("codex").codex_home, Path("/tmp/codex-env"))


if __name__ == "__main__":
    unittest.main()
