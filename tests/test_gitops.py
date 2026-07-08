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

    def test_fetch_log_and_fast_forward(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.init_repo(Path(tmp) / "repo")
            state = gitops.repo_state(root)

            run_repo = Path(tmp) / "run" / "repo"
            gitops.clone_repo(root, run_repo, include_dirty=False)
            self.configure_user(run_repo)
            (run_repo / "file.txt").write_text("base\nchange\n")
            self.git(run_repo, "add", "file.txt")
            self.git(run_repo, "commit", "-m", "change")

            target = gitops.fetch_head(root, run_repo)
            self.assertEqual(gitops.count_commits_between(root, "HEAD", "FETCH_HEAD"), 1)
            self.assertIn("change", "\n".join(gitops.one_line_log(root, "HEAD", "FETCH_HEAD")))

            check = gitops.check_fast_forward(root, state.branch, "FETCH_HEAD")
            self.assertTrue(check.ok, check.reason)
            self.assertEqual(check.target_head, target)

            gitops.fast_forward(root, "FETCH_HEAD")
            self.assertEqual(gitops.current_head(root), target)
            self.assertEqual((root / "file.txt").read_text(), "base\nchange\n")

    def test_fast_forward_rejects_diverged_branch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.init_repo(Path(tmp) / "repo")
            state = gitops.repo_state(root)

            run_repo = Path(tmp) / "run" / "repo"
            gitops.clone_repo(root, run_repo, include_dirty=False)
            self.configure_user(run_repo)
            (run_repo / "file.txt").write_text("base\nrun\n")
            self.git(run_repo, "add", "file.txt")
            self.git(run_repo, "commit", "-m", "run change")

            (root / "file.txt").write_text("base\nhost\n")
            self.git(root, "add", "file.txt")
            self.git(root, "commit", "-m", "host change")

            gitops.fetch_head(root, run_repo)
            check = gitops.check_fast_forward(root, state.branch, "FETCH_HEAD")
            self.assertFalse(check.ok)
            self.assertEqual(check.reason, "current branch has diverged")

    def test_fast_forward_rejects_dirty_worktree(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.init_repo(Path(tmp) / "repo")
            state = gitops.repo_state(root)

            run_repo = Path(tmp) / "run" / "repo"
            gitops.clone_repo(root, run_repo, include_dirty=False)
            self.configure_user(run_repo)
            (run_repo / "file.txt").write_text("base\nrun\n")
            self.git(run_repo, "add", "file.txt")
            self.git(run_repo, "commit", "-m", "run change")

            (root / "dirty.txt").write_text("dirty\n")
            gitops.fetch_head(root, run_repo)
            check = gitops.check_fast_forward(root, state.branch, "FETCH_HEAD")
            self.assertFalse(check.ok)
            self.assertEqual(check.reason, "current worktree is dirty")

    def init_repo(self, root: Path) -> Path:
        root.mkdir()
        self.git(root, "init")
        self.configure_user(root)
        (root / "file.txt").write_text("base\n")
        self.git(root, "add", "file.txt")
        self.git(root, "commit", "-m", "base")
        return root

    def configure_user(self, cwd: Path) -> None:
        self.git(cwd, "config", "user.email", "test@example.com")
        self.git(cwd, "config", "user.name", "Test User")

    def git(self, cwd: Path, *args: str) -> None:
        subprocess.run(["git", *args], cwd=cwd, check=True, stdout=subprocess.PIPE)


if __name__ == "__main__":
    unittest.main()
