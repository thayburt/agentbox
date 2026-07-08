from pathlib import Path
import subprocess
import tempfile
import unittest

from agent_containers import gitops


class GitOpsTests(unittest.TestCase):
    def test_clone_and_import_branch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            self.git(root, "init")
            self.git(root, "config", "user.email", "test@example.com")
            self.git(root, "config", "user.name", "Test User")
            (root / "file.txt").write_text("base\n")
            self.git(root, "add", "file.txt")
            self.git(root, "commit", "-m", "base")
            state = gitops.repo_state(root)

            run_repo = Path(tmp) / "run" / "repo"
            gitops.clone_repo(root, run_repo, include_dirty=False)
            self.git(run_repo, "config", "user.email", "test@example.com")
            self.git(run_repo, "config", "user.name", "Test User")
            (run_repo / "file.txt").write_text("base\nchange\n")
            self.git(run_repo, "add", "file.txt")
            self.git(run_repo, "commit", "-m", "change")

            self.assertEqual(gitops.count_commits_since(run_repo, state.head), 1)
            gitops.import_branch(root, run_repo, "agentc/test", force=False)
            self.assertTrue(gitops.branch_exists(root, "agentc/test"))

    def git(self, cwd: Path, *args: str) -> None:
        subprocess.run(["git", *args], cwd=cwd, check=True, stdout=subprocess.PIPE)


if __name__ == "__main__":
    unittest.main()

