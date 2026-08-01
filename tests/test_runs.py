from pathlib import Path
import contextlib
import io
import json
import tempfile
import unittest

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


if __name__ == "__main__":
    unittest.main()
