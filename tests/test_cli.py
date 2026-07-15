from pathlib import Path
import contextlib
import io
import tempfile
import unittest
from unittest import mock

from agentbox import cli, gitops, runs
from agentbox.config import load_config
from agentbox.drivers import get_driver
from agentbox.template import read_template
from tests import helpers
from tests.helpers import configure_fake_signing, git, git_output


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

    def test_parser_accepts_kilo_and_kilocode_alias(self):
        parser = cli.build_parser()

        kilo = parser.parse_args(["kilo", "run", "status"])
        alias = parser.parse_args(["kilocode", "shell", "--dry-run"])

        self.assertEqual(kilo.driver_id, "kilo")
        self.assertEqual(alias.driver_id, "kilo")

    def test_init_creates_config_and_containerfile_without_overwriting(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.init_repo(Path(tmp) / "repo")
            args = self.args(repo=root)

            with self.quiet_output():
                status = cli.cmd_init(args)

            containerfile = root / ".agentbox" / "codex" / "Containerfile"
            kilo_containerfile = root / ".agentbox" / "kilo" / "Containerfile"
            kilo_config = root / ".agentbox" / "kilo" / "kilo.jsonc"
            self.assertEqual(status, 0)
            self.assertTrue((root / "agentbox.toml").exists())
            config = load_config(root)
            self.assertEqual(
                containerfile.read_text(),
                get_driver("codex").default_containerfile(config.driver_settings("codex")),
            )
            self.assertEqual(
                kilo_containerfile.read_text(),
                get_driver("kilo").default_containerfile(config.driver_settings("kilo")),
            )
            self.assertEqual(kilo_config.read_text(), read_template("kilo/kilo.jsonc"))

            containerfile.write_text("custom\n")
            kilo_containerfile.write_text("custom kilo\n")
            kilo_config.write_text("custom\n")
            with self.quiet_output():
                cli.cmd_init(args)

            self.assertEqual(containerfile.read_text(), "custom\n")
            self.assertEqual(kilo_containerfile.read_text(), "custom kilo\n")
            self.assertEqual(kilo_config.read_text(), "custom\n")

    def test_kilo_config_conflict_warns_during_dry_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.init_repo(Path(tmp) / "repo")
            agentbox_config = root / ".agentbox" / "kilo" / "kilo.jsonc"
            agentbox_config.parent.mkdir(parents=True)
            agentbox_config.write_text("{}\n")
            host_config = Path(tmp) / "host-kilo.json"
            host_config.write_text("{}\n")
            config = load_config(root)
            output = io.StringIO()
            errors = io.StringIO()

            with mock.patch.dict("os.environ", {"KILO_CONFIG": str(host_config)}, clear=True):
                with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                    status = cli.run_container(
                        config,
                        None,
                        "agentbox-kilo:test",
                        root / ".agentbox" / "runs" / "dry" / "repo",
                        "exec kilo",
                        True,
                        "kilo",
                    )

            self.assertEqual(status, 0)
            self.assertIn("host KILO_CONFIG=", errors.getvalue())
            self.assertIn(f"{agentbox_config.resolve()}:/agentbox/config/kilo.jsonc:ro", output.getvalue())
            self.assertNotIn("/kilo-host/KILO_CONFIG", output.getvalue())

    def test_kilo_saved_run_enter_dry_run_uses_original_run_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.init_repo(Path(tmp) / "repo")
            config = load_config(root)
            run_dir = config.run_store / "kilo-run"
            run_repo = run_dir / "repo"
            run_repo.mkdir(parents=True)
            runs.write_metadata(
                run_dir,
                runs.create_metadata(
                    "kilo-run",
                    root,
                    run_repo,
                    "main",
                    "0" * 40,
                    "agentbox-kilo:test",
                    driver="kilo",
                ),
            )
            output = io.StringIO()

            with contextlib.redirect_stdout(output):
                status = cli.cmd_runs_enter(self.args(repo=root, run_id="kilo-run", dry_run=True))

            self.assertEqual(status, 0)
            self.assertIn(f"{run_dir / 'cache'}:/home/ubuntu/.cache:U", output.getvalue())
            self.assertFalse((run_dir / "cache").exists())

    def test_prepare_run_applies_identity_from_host_git_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.init_repo(Path(tmp) / "repo")
            config = load_config(root)

            _, metadata = cli.prepare_run(config, None, "ignore", "agentbox-codex:test")

            run_repo = Path(metadata.run_repo)
            self.assertEqual(
                git_output(run_repo, "config", "--local", "--get", "user.name"),
                "Host User",
            )
            self.assertEqual(
                git_output(run_repo, "config", "--local", "--get", "user.email"),
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
            git(root, "add", "agentbox.toml")
            git(root, "commit", "-m", "add config")
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
                git_output(run_repo, "config", "--local", "--get", "user.name"),
                "CLI User",
            )
            self.assertEqual(
                git_output(run_repo, "config", "--local", "--get", "user.email"),
                "cli@example.com",
            )

    def test_prepare_run_stores_resolved_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.init_repo(Path(tmp) / "repo")
            config = load_config(root)

            _, metadata = cli.prepare_run(config, None, "ignore", "custom/image:tag")

            self.assertEqual(metadata.image, "custom/image:tag")
            self.assertEqual(metadata.driver, "codex")
            self.assertIsNone(metadata.containerfile)

    def test_prepare_run_stores_selected_driver(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.init_repo(Path(tmp) / "repo")
            config = load_config(root)

            _, metadata = cli.prepare_run(
                config, None, "ignore", "agentbox-kilo:test", driver_id="kilo"
            )

            self.assertEqual(metadata.driver, "kilo")

    def test_prepare_kilo_run_snapshots_host_model_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.init_repo(Path(tmp) / "repo")
            config = load_config(root)
            state_home = Path(tmp) / "host-state"
            source = state_home / "kilo" / "model.json"
            source.parent.mkdir(parents=True)
            source.write_text('{"model":"first"}\n')

            with mock.patch.dict("os.environ", {"XDG_STATE_HOME": str(state_home)}, clear=True):
                run_dir, _ = cli.prepare_run(
                    config, None, "ignore", "agentbox-kilo:test", driver_id="kilo"
                )

            destination = run_dir / "state" / "kilo" / "model.json"
            self.assertEqual(destination.read_text(), '{"model":"first"}\n')
            source.write_text('{"model":"changed"}\n')
            self.assertEqual(destination.read_text(), '{"model":"first"}\n')

    def test_prepare_kilo_run_skips_missing_model_and_dry_run_has_no_seed_effects(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.init_repo(Path(tmp) / "repo")
            config = load_config(root)
            state_home = Path(tmp) / "host-state"

            with mock.patch.dict("os.environ", {"XDG_STATE_HOME": str(state_home)}, clear=True):
                run_dir, _ = cli.prepare_run(
                    config, None, "ignore", "agentbox-kilo:test", driver_id="kilo"
                )
                dry_run_dir, _ = cli.prepare_run(
                    config, None, "ignore", "agentbox-kilo:test", dry_run=True, driver_id="kilo"
                )

            self.assertFalse((run_dir / "state").exists())
            self.assertFalse(dry_run_dir.exists())

    def test_prepare_kilo_run_warns_and_continues_when_model_copy_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.init_repo(Path(tmp) / "repo")
            config = load_config(root)
            source = Path(tmp) / "host-state" / "kilo" / "model.json"
            source.parent.mkdir(parents=True)
            source.write_text("model\n")
            errors = io.StringIO()

            with mock.patch.dict("os.environ", {"XDG_STATE_HOME": str(Path(tmp) / "host-state")}, clear=True):
                with mock.patch("agentbox.cli.shutil.copyfileobj", side_effect=OSError("denied")):
                    with contextlib.redirect_stderr(errors):
                        run_dir, metadata = cli.prepare_run(
                            config, None, "ignore", "agentbox-kilo:test", driver_id="kilo"
                        )

            self.assertTrue(Path(metadata.run_repo).is_dir())
            self.assertFalse((run_dir / "state" / "kilo" / "model.json").exists())
            self.assertIn("agentbox: warning: could not seed Kilo model selection", errors.getvalue())
            self.assertIn(str(source), errors.getvalue())

    def test_prepare_kilo_run_rejects_symlinked_host_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.init_repo(Path(tmp) / "repo")
            config = load_config(root)
            secret = Path(tmp) / "secret"
            secret.write_text("host secret\n")
            source = Path(tmp) / "host-state" / "kilo" / "model.json"
            source.parent.mkdir(parents=True)
            source.symlink_to(secret)
            errors = io.StringIO()

            with mock.patch.dict("os.environ", {"XDG_STATE_HOME": str(Path(tmp) / "host-state")}, clear=True):
                with contextlib.redirect_stderr(errors):
                    run_dir, _ = cli.prepare_run(
                        config, None, "ignore", "agentbox-kilo:test", driver_id="kilo"
                    )

            self.assertFalse((run_dir / "state" / "kilo" / "model.json").exists())
            self.assertIn("agentbox: warning: could not seed Kilo model selection", errors.getvalue())

    def test_codex_run_does_not_seed_kilo_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.init_repo(Path(tmp) / "repo")
            config = load_config(root)
            source = Path(tmp) / "host-state" / "kilo" / "model.json"
            source.parent.mkdir(parents=True)
            source.write_text("model\n")

            with mock.patch.dict("os.environ", {"XDG_STATE_HOME": str(Path(tmp) / "host-state")}, clear=True):
                run_dir, _ = cli.prepare_run(config, None, "ignore", "agentbox-codex:test")

            self.assertFalse((run_dir / "state").exists())

    def test_saved_kilo_run_entry_does_not_reseed_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.init_repo(Path(tmp) / "repo")
            config = load_config(root)
            run_dir = config.run_store / "kilo-run"
            run_repo = run_dir / "repo"
            run_repo.mkdir(parents=True)
            destination = run_dir / "state" / "kilo" / "model.json"
            destination.parent.mkdir(parents=True)
            destination.write_text("run model\n")
            source = Path(tmp) / "host-state" / "kilo" / "model.json"
            source.parent.mkdir(parents=True)
            source.write_text("host model\n")
            runs.write_metadata(
                run_dir,
                runs.create_metadata("kilo-run", root, run_repo, "main", "0" * 40, "agentbox-kilo:test", driver="kilo"),
            )

            with mock.patch.dict("os.environ", {"XDG_STATE_HOME": str(Path(tmp) / "host-state")}, clear=True):
                with self.quiet_output():
                    status = cli.cmd_runs_enter(self.args(repo=root, run_id="kilo-run", dry_run=True))

            self.assertEqual(status, 0)
            self.assertEqual(destination.read_text(), "run model\n")
            destination.unlink()
            with mock.patch.dict("os.environ", {"XDG_STATE_HOME": str(Path(tmp) / "host-state")}, clear=True):
                with self.quiet_output():
                    status = cli.cmd_runs_enter(self.args(repo=root, run_id="kilo-run", dry_run=True))

            self.assertEqual(status, 0)
            self.assertFalse(destination.exists())

    def test_prepare_run_snapshots_managed_containerfile(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.init_repo(Path(tmp) / "repo")
            config = load_config(root)
            containerfile = cli.podman.ensure_harness_containerfile(config)

            run_dir, metadata = cli.prepare_run(
                config,
                None,
                "ignore",
                "agentbox-codex:test",
                containerfile=containerfile,
            )

            self.assertIsNotNone(metadata.containerfile)
            snapshot = Path(metadata.containerfile)
            self.assertEqual(snapshot, run_dir / "Containerfile")
            self.assertEqual(snapshot.read_text(), containerfile.read_text())

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

    def test_kilo_run_image_override_bypasses_managed_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.init_repo(Path(tmp) / "repo")
            args = self.args(
                repo=root,
                image="ubuntu:24.04",
                dirty="ignore",
                dry_run=True,
                git_user_name=None,
                git_user_email=None,
                prompt=["status"],
                pull="later",
                sign_imports=None,
                driver_id="kilo",
            )

            with mock.patch("agentbox.cli.podman.ensure_managed_image") as ensure:
                with self.quiet_output():
                    status = cli.cmd_harness_run(args)

            self.assertEqual(status, 0)
            ensure.assert_not_called()

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

    def test_saved_run_dry_run_reports_rebuild_from_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.init_repo(Path(tmp) / "repo")
            config = load_config(root)
            snapshot = config.run_store / "run-a" / "Containerfile"
            snapshot.parent.mkdir(parents=True, exist_ok=True)
            snapshot.write_text("FROM ubuntu:24.04\n")
            metadata = runs.create_metadata(
                "run-a",
                root,
                config.run_store / "run-a" / "repo",
                "main",
                "0" * 40,
                "agentbox-codex:test",
                containerfile=str(snapshot),
            )

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                cli.ensure_saved_run_image(config, metadata, dry_run=True)

            text = output.getvalue()
            self.assertIn("podman image exists", text)
            self.assertIn("podman build", text)
            self.assertIn(str(snapshot), text)

    def test_referenced_images_use_driver_full_refs_not_tags_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.init_repo(Path(tmp) / "repo")
            config = load_config(root)
            codex_dir = config.run_store / "codex-run"
            kilo_dir = config.run_store / "kilo-run"
            custom_dir = config.run_store / "custom-run"
            runs.write_metadata(
                codex_dir,
                runs.create_metadata(
                    "codex-run",
                    root,
                    codex_dir / "repo",
                    "main",
                    "0" * 40,
                    "agentbox-codex:same",
                    driver="codex",
                ),
            )
            runs.write_metadata(
                kilo_dir,
                runs.create_metadata(
                    "kilo-run",
                    root,
                    kilo_dir / "repo",
                    "main",
                    "0" * 40,
                    "agentbox-kilo:same",
                    driver="kilo",
                ),
            )
            runs.write_metadata(
                custom_dir,
                runs.create_metadata(
                    "custom-run",
                    root,
                    custom_dir / "repo",
                    "main",
                    "0" * 40,
                    "custom/image:same",
                    driver="kilo",
                ),
            )

            self.assertEqual(cli.referenced_image_refs(config, "kilo"), {"agentbox-kilo:same"})

    def test_saved_run_without_snapshot_errors_when_image_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.init_repo(Path(tmp) / "repo")
            config = load_config(root)
            metadata = runs.create_metadata(
                "run-b",
                root,
                config.run_store / "run-b" / "repo",
                "main",
                "0" * 40,
                "agentbox-codex:gone",
            )

            with mock.patch("agentbox.cli.podman.image_exists", return_value=False):
                with self.assertRaisesRegex(RuntimeError, "no Containerfile snapshot"):
                    cli.ensure_saved_run_image(config, metadata, dry_run=False)

    def test_driver_specific_shell_rejects_mismatched_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.init_repo(Path(tmp) / "repo")
            config = load_config(root)
            run_dir = config.run_store / "codex-run"
            run_dir.mkdir(parents=True)
            metadata = runs.create_metadata(
                "codex-run",
                root,
                run_dir / "repo",
                "main",
                "0" * 40,
                "agentbox-codex:test",
                driver="codex",
            )
            runs.write_metadata(run_dir, metadata)

            args = self.args(repo=root, run_id="codex-run", dry_run=True, driver_id="kilo")

            with self.assertRaisesRegex(RuntimeError, "uses driver codex"):
                cli.cmd_harness_shell(args)

    def test_runs_prune_reports_unknown_and_rejects_escaping_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.init_repo(Path(tmp) / "repo")
            config = load_config(root)
            (config.run_store / "keep").mkdir(parents=True)
            (config.run_store / "keep" / "cache" / "kilo").mkdir(parents=True)
            (config.run_store / "keep" / "cache" / "kilo" / "probe").write_text("cached\n")
            (config.run_store / "keep" / "state" / "kilo").mkdir(parents=True)
            (config.run_store / "keep" / "state" / "kilo" / "model.json").write_text("model\n")
            (config.run_store / "keep" / "state" / "kilo-sandbox-policy").mkdir()

            errors = io.StringIO()
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(errors):
                status = cli.cmd_runs_prune(
                    self.args(repo=root, all=False, run_id=["missing", "keep", "../escape"])
                )

            text = errors.getvalue()
            self.assertEqual(status, 2)
            self.assertIn("no such run: missing", text)
            self.assertIn("invalid run id: ../escape", text)
            self.assertFalse((config.run_store / "keep").exists())

    def test_runs_import_uses_sign_imports_from_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.init_repo(Path(tmp) / "repo")
            configure_fake_signing(root, Path(tmp))
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
            self.assertIn("gpgsig", git_output(root, "cat-file", "commit", imported_head))

    def test_runs_import_cli_override_disables_config_signing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.init_repo(Path(tmp) / "repo")
            configure_fake_signing(root, Path(tmp))
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
        return helpers.init_repo(root, name="Host User", email="host@example.com")

    def create_committed_run(
        self, root: Path, run_id: str
    ) -> tuple[Path, runs.RunMetadata]:
        config = load_config(root)
        state = gitops.repo_state(root)
        run_dir = config.run_store / run_id
        run_repo = run_dir / "repo"
        gitops.clone_repo(root, run_repo, include_dirty=False)
        git(run_repo, "config", "user.email", "run@example.com")
        git(run_repo, "config", "user.name", "Run User")
        (run_repo / "file.txt").write_text("base\nchange\n")
        git(run_repo, "add", "file.txt")
        git(run_repo, "commit", "-m", "change")
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

    def args(self, **kwargs):
        return type("Args", (), kwargs)()

    @contextlib.contextmanager
    def quiet_output(self):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            yield
