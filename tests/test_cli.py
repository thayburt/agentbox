from pathlib import Path
import contextlib
import io
import subprocess
import tempfile
import unittest
from unittest import mock

from agentbox import cli, gitops, runs
from agentbox.config import load_config


class CliRunPreparationTests(unittest.TestCase):
    def test_sign_import_parser_defaults_to_none(self):
        parser = cli.build_parser()

        args = parser.parse_args(["codex", "run"])

        self.assertIsNone(args.sign_imports)

    def test_sign_import_parser_accepts_enable_and_disable(self):
        parser = cli.build_parser()

        enabled = parser.parse_args(["codex", "run", "--sign-imports"])
        disabled = parser.parse_args(["runs", "import", "abc", "--no-sign-imports"])

        self.assertTrue(enabled.sign_imports)
        self.assertFalse(disabled.sign_imports)

    def test_parser_accepts_image_for_run_and_shell(self):
        parser = cli.build_parser()

        run = parser.parse_args(["codex", "run", "--image", "ubuntu:24.04"])
        shell = parser.parse_args(["codex", "shell", "--image", "localhost/custom:dev"])

        self.assertEqual(run.image, "ubuntu:24.04")
        self.assertEqual(shell.image, "localhost/custom:dev")

    def test_init_creates_config_and_containerfile_without_overwriting(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.init_repo(Path(tmp) / "repo")
            args = self.args(repo=root)

            with self.quiet_output():
                status = cli.cmd_init(args)

            containerfile = root / ".agentbox" / "codex.Containerfile"
            self.assertEqual(status, 0)
            self.assertTrue((root / "agentbox.toml").exists())
            self.assertIn("FROM ubuntu:24.04", containerfile.read_text())

            containerfile.write_text("custom\n")
            with self.quiet_output():
                cli.cmd_init(args)

            self.assertEqual(containerfile.read_text(), "custom\n")

    def test_prepare_run_applies_identity_from_host_git_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.init_repo(Path(tmp) / "repo")
            config = load_config(root)

            _, metadata = cli.prepare_run(config, None, "ignore", "agentbox-codex:test")

            run_repo = Path(metadata.run_repo)
            self.assertEqual(
                self.git_output(run_repo, "config", "--local", "--get", "user.name"),
                "Host User",
            )
            self.assertEqual(
                self.git_output(run_repo, "config", "--local", "--get", "user.email"),
                "host@example.com",
            )

    def test_prepare_run_cli_identity_overrides_config_and_host_git_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.init_repo(Path(tmp) / "repo")
            (root / "agentbox.toml").write_text(
                """
[runtime]
run_store = "../runs"

[git]
user_name = "Config User"
user_email = "config@example.com"
"""
            )
            self.git(root, "add", "agentbox.toml")
            self.git(root, "commit", "-m", "add config")
            config = load_config(root)

            _, metadata = cli.prepare_run(
                config,
                None,
                "ignore",
                "agentbox-codex:test",
                git_user_name="CLI User",
                git_user_email="cli@example.com",
            )

            run_repo = Path(metadata.run_repo)
            self.assertEqual(
                self.git_output(run_repo, "config", "--local", "--get", "user.name"),
                "CLI User",
            )
            self.assertEqual(
                self.git_output(run_repo, "config", "--local", "--get", "user.email"),
                "cli@example.com",
            )

    def test_prepare_run_stores_resolved_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.init_repo(Path(tmp) / "repo")
            config = load_config(root)

            _, metadata = cli.prepare_run(config, None, "ignore", "custom/image:tag")

            self.assertEqual(metadata.image, "custom/image:tag")

    def test_codex_run_image_override_bypasses_managed_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.init_repo(Path(tmp) / "repo")
            args = self.args(
                repo=root,
                image="ubuntu:24.04",
                dirty="ignore",
                dry_run=True,
                git_user_name=None,
                git_user_email=None,
                prompt=[],
                pull="later",
                sign_imports=None,
            )

            with mock.patch("agentbox.cli.podman.ensure_managed_image") as ensure:
                with self.quiet_output():
                    status = cli.cmd_codex_run(args)

            self.assertEqual(status, 0)
            ensure.assert_not_called()

    def test_default_codex_run_auto_builds_managed_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.init_repo(Path(tmp) / "repo")
            args = self.args(
                repo=root,
                image=None,
                dirty="ignore",
                dry_run=True,
                git_user_name=None,
                git_user_email=None,
                prompt=[],
                pull="later",
                sign_imports=None,
            )

            with mock.patch(
                "agentbox.cli.podman.ensure_managed_image", return_value="agentbox-codex:test"
            ) as ensure:
                with self.quiet_output():
                    status = cli.cmd_codex_run(args)

            self.assertEqual(status, 0)
            ensure.assert_called_once()

    def test_dirty_abort_happens_before_managed_image_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.init_repo(Path(tmp) / "repo")
            (root / "file.txt").write_text("dirty\n")
            args = self.args(
                repo=root,
                image=None,
                dirty="abort",
                dry_run=False,
                git_user_name=None,
                git_user_email=None,
                prompt=[],
                pull="later",
                sign_imports=None,
            )

            with mock.patch("agentbox.cli.podman.ensure_managed_image") as ensure:
                with self.assertRaisesRegex(RuntimeError, "working tree is dirty"):
                    cli.cmd_codex_run(args)

            ensure.assert_not_called()

    def test_saved_run_dry_run_reports_default_image_creation_and_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.init_repo(Path(tmp) / "repo")
            config = load_config(root)
            image = cli.podman.harness_image_name(
                config, cli.podman.default_containerfile_digest(config.base_image)
            )

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                cli.ensure_saved_run_image(config, image, dry_run=True)

            text = output.getvalue()
            self.assertIn("would create", text)
            self.assertIn("podman image exists", text)
            self.assertIn("podman build", text)

    def test_runs_import_uses_sign_imports_from_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.init_repo(Path(tmp) / "repo")
            self.configure_fake_signing(root, Path(tmp))
            (root / "agentbox.toml").write_text(
                """
[runtime]
run_store = "../runs"

[git]
sign_imports = true
"""
            )
            run_repo, metadata = self.create_committed_run(root, "test")
            run_head = gitops.current_head(run_repo)

            with self.quiet_output():
                status = cli.cmd_runs_import(
                    self.args(repo=root, run_id=metadata.id, force=False, sign_imports=None)
                )

            imported_head = gitops.rev_parse(root, f"agentbox/{metadata.id}")
            self.assertEqual(status, 0)
            self.assertNotEqual(imported_head, run_head)
            self.assertIn("gpgsig", self.git_output(root, "cat-file", "commit", imported_head))

    def test_runs_import_cli_override_disables_config_signing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.init_repo(Path(tmp) / "repo")
            self.configure_fake_signing(root, Path(tmp))
            (root / "agentbox.toml").write_text(
                """
[runtime]
run_store = "../runs"

[git]
sign_imports = true
"""
            )
            run_repo, metadata = self.create_committed_run(root, "test")
            run_head = gitops.current_head(run_repo)

            with self.quiet_output():
                status = cli.cmd_runs_import(
                    self.args(repo=root, run_id=metadata.id, force=False, sign_imports=False)
                )

            self.assertEqual(status, 0)
            self.assertEqual(gitops.rev_parse(root, f"agentbox/{metadata.id}"), run_head)

    def test_complete_run_rejects_ff_only_when_signing_is_enabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.init_repo(Path(tmp) / "repo")
            (root / "agentbox.toml").write_text(
                """
[git]
sign_imports = true
"""
            )
            config = load_config(root)
            run_repo, metadata = self.create_committed_run(root, "test")
            original_head = gitops.current_head(root)

            with self.quiet_output():
                status = cli.complete_run(config, metadata, "ff-only")

            self.assertEqual(status, 2)
            self.assertEqual(gitops.current_head(root), original_head)
            self.assertNotEqual(gitops.current_head(root), gitops.current_head(run_repo))

    def init_repo(self, root: Path) -> Path:
        root.mkdir()
        self.git(root, "init")
        self.git(root, "config", "user.email", "host@example.com")
        self.git(root, "config", "user.name", "Host User")
        (root / "file.txt").write_text("base\n")
        self.git(root, "add", "file.txt")
        self.git(root, "commit", "-m", "base")
        return root

    def create_committed_run(
        self, root: Path, run_id: str
    ) -> tuple[Path, runs.RunMetadata]:
        config = load_config(root)
        state = gitops.repo_state(root)
        run_dir = config.run_store / run_id
        run_repo = run_dir / "repo"
        gitops.clone_repo(root, run_repo, include_dirty=False)
        self.git(run_repo, "config", "user.email", "run@example.com")
        self.git(run_repo, "config", "user.name", "Run User")
        (run_repo / "file.txt").write_text("base\nchange\n")
        self.git(run_repo, "add", "file.txt")
        self.git(run_repo, "commit", "-m", "change")
        metadata = runs.create_metadata(
            run_id,
            root,
            run_repo,
            state.branch,
            state.head,
            "agentbox-codex:test",
        )
        runs.write_metadata(run_dir, metadata)
        return run_repo, metadata

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

    def args(self, **kwargs):
        return type("Args", (), kwargs)()

    @contextlib.contextmanager
    def quiet_output(self):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            yield

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
