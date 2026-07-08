from pathlib import Path
import tempfile
import unittest

from agent_containers.devcontainer import load_devcontainer, strip_jsonc


class DevcontainerTests(unittest.TestCase):
    def test_strip_jsonc_preserves_urls(self):
        text = '{"url": "https://example.com", // comment\n "x": 1}'
        self.assertIn("https://example.com", strip_jsonc(text))

    def test_load_subset(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "devcontainer.json"
            path.write_text(
                """
                {
                  // comment
                  "image": "ubuntu:24.04",
                  "workspaceFolder": "/work",
                  "containerEnv": {"A": "B"},
                  "remoteEnv": {"C": "D"},
                  "runArgs": ["--network=host"],
                  "postCreateCommand": "true"
                }
                """
            )
            dev = load_devcontainer(path)
            assert dev is not None
            self.assertEqual(dev.image, "ubuntu:24.04")
            self.assertEqual(dev.workspace_folder, "/work")
            self.assertEqual(dev.env, {"A": "B", "C": "D"})
            self.assertEqual(dev.run_args, ["--network=host"])
            self.assertEqual(dev.post_create, ["true"])

    def test_unsupported_fields_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "devcontainer.json"
            path.write_text('{"dockerComposeFile": "compose.yml"}')
            with self.assertRaisesRegex(ValueError, "dockerComposeFile"):
                load_devcontainer(path)


if __name__ == "__main__":
    unittest.main()
