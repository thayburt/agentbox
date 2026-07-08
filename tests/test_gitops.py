from pathlib import Path
import subprocess
import tempfile
import unittest

from agent_containers import gitops


class GitOpsTests(unittest.TestCase):
    def test_read_git_identity_from_repo_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            self.git(root, "init")
            self.git(root, "config", "user.email", "local@example.com")
            self.git(root, "config", "user.name", "Local User")

            identity = gitops.read_git_identity(root)

            self.assertEqual(identity.user_name, "Local User")
            self.assertEqual(identity.user_email, "local@example.com")

    def test_apply_git_identity_writes_local_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            self.git(root, "init")

            gitops.apply_git_identity(
                root,
                gitops.GitIdentity(
                    user_name="Run User",
                    user_email="run@example.com",
                ),
            )

            self.assertEqual(
                self.git_output(root, "config", "--local", "--get", "user.name"),
                "Run User",
            )
            self.assertEqual(
                self.git_output(root, "config", "--local", "--get", "user.email"),
                "run@example.com",
            )

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
            run_head = gitops.current_head(run_repo)
            gitops.import_branch(root, run_repo, "agentc/test", force=False)
            self.assertTrue(gitops.branch_exists(root, "agentc/test"))
            self.assertEqual(gitops.rev_parse(root, "agentc/test"), run_head)

    def test_import_branch_signed_replays_with_signed_commits(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.init_repo(Path(tmp) / "repo")
            self.configure_fake_signing(root, Path(tmp))
            state = gitops.repo_state(root)

            run_repo = Path(tmp) / "run" / "repo"
            gitops.clone_repo(root, run_repo, include_dirty=False)
            self.configure_user(run_repo)
            (run_repo / "file.txt").write_text("base\nchange\n")
            self.git(run_repo, "add", "file.txt")
            self.git(run_repo, "commit", "-m", "change")
            run_head = gitops.current_head(run_repo)

            signed_head = gitops.import_branch_signed(
                root,
                run_repo,
                state.head,
                "agentc/signed",
                force=False,
            )

            self.assertTrue(gitops.branch_exists(root, "agentc/signed"))
            self.assertEqual(gitops.rev_parse(root, "agentc/signed"), signed_head)
            self.assertNotEqual(signed_head, run_head)
            self.assertIn("gpgsig", self.git_output(root, "cat-file", "commit", signed_head))
            self.assertEqual(
                self.git_output(root, "show", "agentc/signed:file.txt"),
                "base\nchange",
            )

    def test_import_branch_signed_rejects_merge_commits(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.init_repo(Path(tmp) / "repo")
            self.configure_fake_signing(root, Path(tmp))
            state = gitops.repo_state(root)

            run_repo = Path(tmp) / "run" / "repo"
            gitops.clone_repo(root, run_repo, include_dirty=False)
            self.configure_user(run_repo)
            (run_repo / "file.txt").write_text("base\nmain\n")
            self.git(run_repo, "add", "file.txt")
            self.git(run_repo, "commit", "-m", "main change")
            self.git(run_repo, "checkout", "-b", "feature", state.head)
            (run_repo / "feature.txt").write_text("feature\n")
            self.git(run_repo, "add", "feature.txt")
            self.git(run_repo, "commit", "-m", "feature change")
            self.git(run_repo, "checkout", "master")
            self.git(run_repo, "merge", "--no-ff", "feature", "-m", "merge feature")

            with self.assertRaisesRegex(RuntimeError, "merge commits"):
                gitops.import_branch_signed(root, run_repo, state.head, "agentc/signed", False)

            self.assertFalse(gitops.branch_exists(root, "agentc/signed"))

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

    def configure_fake_signing(self, cwd: Path, tmp: Path) -> None:
        fake_gpg = tmp / "fake-gpg"
        fake_gpg.write_text(
            """#!/bin/sh
cat >/dev/null
echo '[GNUPG:] SIG_CREATED D 1 1 00 0 0 0 0 FAKE' >&2
cat <<'SIG'
-----BEGIN PGP SIGNATURE-----

fake
-----END PGP SIGNATURE-----
SIG
exit 0
"""
        )
        fake_gpg.chmod(0o755)
        self.git(cwd, "config", "gpg.program", str(fake_gpg))
        self.git(cwd, "config", "user.signingkey", "fake")

    def git(self, cwd: Path, *args: str) -> None:
        subprocess.run(["git", *args], cwd=cwd, check=True, stdout=subprocess.PIPE)

    def git_output(self, cwd: Path, *args: str) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        )
        return result.stdout.strip()


if __name__ == "__main__":
    unittest.main()
