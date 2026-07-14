import hashlib
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
                self.assertIn(chr(92) + chr(10), text)
                self.assertTrue(text.endswith("\n"))

    def test_default_templates_preserve_clean_environment_bytes(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            rendered = {
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

        expected_digests = {
            "agentbox.toml": "3e81d71013f81c585acacf212d4cea4d1202517961187ae6347cee9ab8d0c714",
            "codex/agentbox-section.toml": "7ae0c4db5b79c34b6a2b47750f473f26dd7a2e86562fa24640165d308349a838",
            "kilo/agentbox-section.toml": "84ab96201716b858219cec32a01ce7b117799a46edb166590a4d508e98214e21",
            "codex/Containerfile": "2d89cdc2be4633ddb4bda4f2586a05e06fa5381bf140e3cf784d0cbe55614b00",
            "kilo/Containerfile": "4470ab72e89c9e8ade4ea7e00dce51b22e005a0d07b2c1c0f4e763cff2bc5d43",
            "kilo/kilo.jsonc": "50de534d570283f9704e3205c086dbd2a7bb2392de26446f9bec1cd614711f1e",
        }
        self.assertEqual(
            {name: hashlib.sha256(text.encode()).hexdigest() for name, text in rendered.items()},
            expected_digests,
        )

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
        self.assertEqual(list(tomllib.loads(root)), ["runtime", "devcontainer", "codex", "kilo", "git"])

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
