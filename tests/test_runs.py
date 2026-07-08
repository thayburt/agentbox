from pathlib import Path
import json
import tempfile
import unittest

from agentbox import runs


class RunsTests(unittest.TestCase):
    def test_new_metadata_includes_driver(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            metadata = runs.create_metadata(
                "run-a",
                root,
                root / "runs" / "run-a" / "repo",
                "main",
                "0" * 40,
                "agentbox-kilo:test",
                driver="kilo",
            )

            self.assertEqual(metadata.driver, "kilo")

    def test_old_metadata_defaults_to_codex(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run-a"
            run_dir.mkdir()
            (run_dir / runs.METADATA_FILE).write_text(
                json.dumps(
                    {
                        "id": "run-a",
                        "created_at": "2026-01-01T00:00:00+00:00",
                        "original_repo": str(Path(tmp) / "repo"),
                        "run_repo": str(run_dir / "repo"),
                        "base_branch": "main",
                        "base_head": "0" * 40,
                        "image": "agentbox-codex:test",
                        "containerfile": None,
                    }
                )
            )

            metadata = runs.read_metadata(run_dir)

            self.assertEqual(metadata.driver, "codex")


if __name__ == "__main__":
    unittest.main()
