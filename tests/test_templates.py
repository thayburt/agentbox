import os
import tomllib
import unittest
from unittest import mock

from agentbox.config import default_toml
from agentbox.drivers import CodexSettings, KiloSettings, get_driver
from agentbox.template import read_template, render_template


class TemplateTests(unittest.TestCase):
    def test_containerfiles_render_non_default_base_images_without_tokens(self):
        cases = (
            ("codex", CodexSettings("custom-codex", "example/codex:latest", "/work", "/codex")),
            ("kilo", KiloSettings("custom-kilo", "example/kilo:latest", "/work")),
        )

        for driver_id, settings in cases:
            with self.subTest(driver=driver_id):
                text = get_driver(driver_id).default_containerfile(settings)

                self.assertEqual(text.splitlines()[0], f"FROM {settings.base_image}")
                self.assertNotIn("@@", text)
                self.assertTrue(text.endswith("\n"))

    def test_default_templates_are_independent_of_unrelated_environment(self):
        def render_all():
            return {
                "agentbox.toml": default_toml(),
                "codex/agentbox-section.toml": get_driver("codex").default_toml_section({}),
                "kilo/agentbox-section.toml": get_driver("kilo").default_toml_section({}),
                "codex/Containerfile": get_driver("codex").default_containerfile(
                    get_driver("codex").default_settings({})
                ),
                "kilo/Containerfile": get_driver("kilo").default_containerfile(
                    get_driver("kilo").default_settings({})
                ),
                "kilo/kilo.jsonc": read_template("kilo/kilo.jsonc"),
            }

        # CODEX_HOME legitimately affects rendering, so it is deliberately
        # excluded from the polluted environment below.
        polluted_env = {
            "HOME": "/tmp/somewhere",
            "USER": "someone",
            "RANDOM_VAR": "unrelated",
        }
        with mock.patch.dict(os.environ, {}, clear=True):
            clean = render_all()
        with mock.patch.dict(os.environ, polluted_env, clear=True):
            polluted = render_all()

        self.assertEqual(clean, polluted)

    def test_driver_sections_and_root_toml_preserve_values_and_order(self):
        codex = get_driver("codex")
        kilo = get_driver("kilo")
        codex_section = codex.default_toml_section(
            {
                "CODEX_HOME": "/custom/codex",
            }
        )
        kilo_section = kilo.default_toml_section({})
        with mock.patch.dict(os.environ, {"CODEX_HOME": "/custom/codex"}, clear=True):
            root = default_toml()

        self.assertEqual(tomllib.loads(codex_section)["codex"]["codex_home"], "/custom/codex")
        self.assertEqual(tomllib.loads(kilo_section)["kilo"]["image_name"], "agentbox-kilo")
        self.assertEqual(list(tomllib.loads(root)), ["runtime", "codex", "kilo", "git"])

    def test_kilo_templates_preserve_load_bearing_contracts(self):
        # The kilo container must not run as root, and the kilo config must
        # keep its schema reference. These are user-facing contracts, not
        # incidental template content.
        containerfile = read_template("kilo/Containerfile")
        kilo_config = read_template("kilo/kilo.jsonc")

        self.assertIn("USER ubuntu", containerfile.splitlines())
        self.assertIn('"$schema": "https://app.kilo.ai/config.json"', kilo_config)

    def test_kilo_init_file_is_exact_packaged_resource(self):
        driver = get_driver("kilo")
        init_file = driver.init_files(driver.default_settings({}))[0]

        self.assertEqual(init_file.relative_path.as_posix(), ".agentbox/kilo/kilo.jsonc")
        self.assertEqual(init_file.contents, read_template("kilo/kilo.jsonc"))

    def test_renderer_rejects_mismatched_replacements(self):
        with mock.patch("agentbox.template.read_template", return_value="x @@ONE@@ y"):
            with self.assertRaisesRegex(ValueError, "missing replacements: ONE"):
                render_template("test", {})
            with self.assertRaisesRegex(ValueError, "unexpected replacements: TWO"):
                render_template("test", {"ONE": "one", "TWO": "two"})

    def test_renderer_does_not_reprocess_replacement_tokens(self):
        with mock.patch("agentbox.template.read_template", return_value="@@ONE@@"):
            self.assertEqual(render_template("test", {"ONE": "@@TWO@@"}), "@@TWO@@")


if __name__ == "__main__":
    unittest.main()
