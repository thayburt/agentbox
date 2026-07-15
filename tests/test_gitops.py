from pathlib import Path
import tempfile
import unittest

from agentbox import gitops
from tests.helpers import configure_fake_signing, configure_user, git, git_output, init_repo


class GitOpsTests(unittest.TestCase):
    def test_read_git_identity_from_repo_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            git(root, "init")
            git(root, "config", "user.email", "local@example.com")
            git(root, "config", "user.name", "Local User")

            identity = gitops.read_git_identity(root)

            self.assertEqual(identity.user_name, "Local User")
            self.assertEqual(identity.user_email, "local@example.com")

    def test_apply_git_identity_writes_local_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            git(root, "init")

            gitops.apply_git_identity(
                root,
                gitops.GitIdentity(
                    user_name="Run User",
                    user_email="run@example.com",
                ),
            )

            self.assertEqual(
                git_output(root, "config", "--local", "--get", "user.name"),
                "Run User",
            )
            self.assertEqual(
                git_output(root, "config", "--local", "--get", "user.email"),
                "run@example.com",
            )

    def test_clone_and_import_branch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            git(root, "init")
            configure_user(root)
            (root / "file.txt").write_text("base\n")
            git(root, "add", "file.txt")
            git(root, "commit", "-m", "base")
            state = gitops.repo_state(root)

            run_repo = Path(tmp) / "run" / "repo"
            gitops.clone_repo(root, run_repo, include_dirty=False)
            configure_user(run_repo)
            (run_repo / "file.txt").write_text("base\nchange\n")
            git(run_repo, "add", "file.txt")
            git(run_repo, "commit", "-m", "change")

            self.assertEqual(gitops.count_commits_since(run_repo, state.head), 1)
            run_head = gitops.current_head(run_repo)
            gitops.import_branch(root, run_repo, "agentbox/test", force=False)
            self.assertTrue(gitops.branch_exists(root, "agentbox/test"))
            self.assertEqual(gitops.rev_parse(root, "agentbox/test"), run_head)

    def test_clone_include_dirty_handles_staged_rename(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            git(root, "init")
            configure_user(root)
            (root / "old.txt").write_text("hello\n")
            git(root, "add", "old.txt")
            git(root, "commit", "-m", "base")

            git(root, "mv", "old.txt", "new.txt")

            run_repo = Path(tmp) / "run" / "repo"
            gitops.clone_repo(root, run_repo, include_dirty=True)

            self.assertFalse((run_repo / "old.txt").exists())
            self.assertTrue((run_repo / "new.txt").exists())
            self.assertEqual((run_repo / "new.txt").read_text(), "hello\n")

    def test_clone_include_dirty_handles_modified_rename(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            git(root, "init")
            configure_user(root)
            (root / "old.txt").write_text("hello\n")
            git(root, "add", "old.txt")
            git(root, "commit", "-m", "base")

            git(root, "mv", "old.txt", "new.txt")
            (root / "new.txt").write_text("hello\nworld\n")

            run_repo = Path(tmp) / "run" / "repo"
            gitops.clone_repo(root, run_repo, include_dirty=True)

            self.assertFalse((run_repo / "old.txt").exists())
            self.assertEqual((run_repo / "new.txt").read_text(), "hello\nworld\n")

    def test_import_branch_signed_replays_with_signed_commits(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = init_repo(Path(tmp) / "repo")
            configure_fake_signing(root, Path(tmp))
            state = gitops.repo_state(root)

            run_repo = Path(tmp) / "run" / "repo"
            gitops.clone_repo(root, run_repo, include_dirty=False)
            configure_user(run_repo)
            (run_repo / "file.txt").write_text("base\nchange\n")
            git(run_repo, "add", "file.txt")
            git(run_repo, "commit", "-m", "change")
            run_head = gitops.current_head(run_repo)

            signed_head = gitops.import_branch_signed(
                root,
                run_repo,
                state.head,
                "agentbox/signed",
                force=False,
            )

            self.assertTrue(gitops.branch_exists(root, "agentbox/signed"))
            self.assertEqual(gitops.rev_parse(root, "agentbox/signed"), signed_head)
            self.assertNotEqual(signed_head, run_head)
            self.assertIn("gpgsig", git_output(root, "cat-file", "commit", signed_head))
            self.assertEqual(
                git_output(root, "show", "agentbox/signed:file.txt"),
                "base\nchange",
            )

    def test_import_branch_signed_rejects_merge_commits(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = init_repo(Path(tmp) / "repo")
            configure_fake_signing(root, Path(tmp))
            state = gitops.repo_state(root)

            run_repo = Path(tmp) / "run" / "repo"
            gitops.clone_repo(root, run_repo, include_dirty=False)
            configure_user(run_repo)
            (run_repo / "file.txt").write_text("base\nmain\n")
            git(run_repo, "add", "file.txt")
            git(run_repo, "commit", "-m", "main change")
            git(run_repo, "checkout", "-b", "feature", state.head)
            (run_repo / "feature.txt").write_text("feature\n")
            git(run_repo, "add", "feature.txt")
            git(run_repo, "commit", "-m", "feature change")
            git(run_repo, "checkout", state.branch)
            git(run_repo, "merge", "--no-ff", "feature", "-m", "merge feature")

            with self.assertRaisesRegex(RuntimeError, "merge commits"):
                gitops.import_branch_signed(root, run_repo, state.head, "agentbox/signed", False)

            self.assertFalse(gitops.branch_exists(root, "agentbox/signed"))

    def test_fetch_log_and_fast_forward(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = init_repo(Path(tmp) / "repo")
            state = gitops.repo_state(root)

            run_repo = Path(tmp) / "run" / "repo"
            gitops.clone_repo(root, run_repo, include_dirty=False)
            configure_user(run_repo)
            (run_repo / "file.txt").write_text("base\nchange\n")
            git(run_repo, "add", "file.txt")
            git(run_repo, "commit", "-m", "change")

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
            root = init_repo(Path(tmp) / "repo")
            state = gitops.repo_state(root)

            run_repo = Path(tmp) / "run" / "repo"
            gitops.clone_repo(root, run_repo, include_dirty=False)
            configure_user(run_repo)
            (run_repo / "file.txt").write_text("base\nrun\n")
            git(run_repo, "add", "file.txt")
            git(run_repo, "commit", "-m", "run change")

            (root / "file.txt").write_text("base\nhost\n")
            git(root, "add", "file.txt")
            git(root, "commit", "-m", "host change")

            gitops.fetch_head(root, run_repo)
            check = gitops.check_fast_forward(root, state.branch, "FETCH_HEAD")
            self.assertFalse(check.ok)
            self.assertEqual(check.reason, "current branch has diverged")

    def test_fast_forward_rejects_dirty_worktree(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = init_repo(Path(tmp) / "repo")
            state = gitops.repo_state(root)

            run_repo = Path(tmp) / "run" / "repo"
            gitops.clone_repo(root, run_repo, include_dirty=False)
            configure_user(run_repo)
            (run_repo / "file.txt").write_text("base\nrun\n")
            git(run_repo, "add", "file.txt")
            git(run_repo, "commit", "-m", "run change")

            (root / "dirty.txt").write_text("dirty\n")
            gitops.fetch_head(root, run_repo)
            check = gitops.check_fast_forward(root, state.branch, "FETCH_HEAD")
            self.assertFalse(check.ok)
            self.assertEqual(check.reason, "current worktree is dirty")
