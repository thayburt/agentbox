import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from agentbox import runs


class RunsTests(unittest.TestCase):
    def test_list_runs_skips_corrupt_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_store = Path(tmp) / "runs"
            valid_dir = run_store / "valid"
            invalid_dir = run_store / "invalid"
            metadata = runs.create_metadata(
                "valid",
                Path(tmp) / "repo",
                valid_dir / "repo",
                "main",
                "0" * 40,
                "agentbox-codex:test",
                driver="codex",
            )
            runs.write_metadata(valid_dir, metadata)
            invalid_dir.mkdir()
            (invalid_dir / runs.METADATA_FILE).write_text("not json{")
            errors = io.StringIO()

            with contextlib.redirect_stderr(errors):
                found = runs.list_runs(run_store)

            self.assertEqual(found, [metadata])
            self.assertIn("skipping invalid run metadata", errors.getvalue())

    def test_list_runs_skips_forward_incompatible_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_store = Path(tmp) / "runs"
            run_dir = run_store / "future"
            metadata = runs.create_metadata(
                "future",
                Path(tmp) / "repo",
                run_dir / "repo",
                "main",
                "0" * 40,
                "agentbox-codex:test",
                driver="codex",
            )
            runs.write_metadata(run_dir, metadata)
            data = json.loads((run_dir / runs.METADATA_FILE).read_text())
            data["future_field"] = 1
            (run_dir / runs.METADATA_FILE).write_text(json.dumps(data))
            errors = io.StringIO()

            with contextlib.redirect_stderr(errors):
                found = runs.list_runs(run_store)

            self.assertEqual(found, [])
            self.assertIn("skipping invalid run metadata", errors.getvalue())

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

    def test_old_metadata_defaults_containerfile_to_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run-a"
            run_dir.mkdir()
            (run_dir / runs.METADATA_FILE).write_text(
                json.dumps(
                    {
                        "id": "run-a", "created_at": "now", "original_repo": "repo",
                        "run_repo": "run-repo", "base_branch": "main", "base_head": "a" * 40,
                        "image": "agentbox-codex:test",
                    }
                )
            )
            self.assertIsNone(runs.read_metadata(run_dir).containerfile)

    def test_metadata_rejects_invalid_shapes_and_unknown_drivers(self):
        valid = {
            "id": "run-a", "created_at": "now", "original_repo": "repo", "run_repo": "run-repo",
            "base_branch": "main", "base_head": "a" * 40, "image": "agentbox-codex:test",
        }
        cases = (
            ([], "must be an object"),
            ({}, "missing required field"),
            ({**valid, "id": 1}, "field id must be a string"),
            ({**valid, "containerfile": 1}, "containerfile must be a string or null"),
            ({**valid, "driver": "unknown"}, "unknown driver"),
            ({**valid, "extra": True}, "unknown field"),
        )
        for data, message in cases:
            with self.subTest(data=data), tempfile.TemporaryDirectory() as tmp:
                run_dir = Path(tmp)
                (run_dir / runs.METADATA_FILE).write_text(json.dumps(data))
                with self.assertRaisesRegex((ValueError, RuntimeError), message):
                    runs.read_metadata(run_dir)

    def test_metadata_canonicalizes_driver_alias(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / runs.METADATA_FILE).write_text(
                json.dumps(
                    {
                        "id": "run-a", "created_at": "now", "original_repo": "repo",
                        "run_repo": "run-repo", "base_branch": "main", "base_head": "a" * 40,
                        "image": "agentbox-kilo:test", "driver": "kilocode",
                    }
                )
            )
            self.assertEqual(runs.read_metadata(run_dir).driver, "kilo")


if __name__ == "__main__":
    unittest.main()
