# Agentbox Audit Fix — Commit-by-Commit Implementation Plan

Fixes for `AGENT_AUDIT.md`. Each numbered step below is **one commit**,
independently implementable by a separate agent, with explicit file edits,
tests, and a commit message. Baseline verified before planning: 94 tests pass
(`uv run python -m unittest discover -s tests`), `uv run ruff check` green.

## Rules for every step

- After finishing a step, run `uv run python -m unittest discover -s tests`
  (must stay green) and, from step 8 onward, also `uv run ruff check`.
- Do not refactor anything outside your step's file list.
- One commit per step, using the suggested message.

## Dependency map (for parallel agents)

- Steps 1, 2, 5, 6 are fully independent of each other.
- Step 3 must come after step 2 (both edit the same four Containerfiles).
- Step 4 must come after step 3 (both edit `podman.py`).
- Step 7 is independent of 1-6 but edits `cli.py`; step 9 also edits `cli.py`
  and must come after 7.
- Step 8 after steps 3, 4, 7 (reformats lines in `podman.py`/`cli.py`).
- Step 10 after steps 7, 8, 9 (moves final code).
- Step 11 after steps 2, 3, 7. Step 12 last.

## Confirmed decisions (already made with the user — do not revisit)

1. Pin harness versions **and** the base-image digest, in both the packaged
   templates (`src/agentbox/templates/*/Containerfile`) and the repo-local
   `.agentbox/*/Containerfile`.
2. Podman hardening: `--cap-drop=ALL` + `--security-opt=no-new-privileges`;
   remove `sudo` from both images; document the unrestricted-network rationale.
3. Saved-run recovery: add `--image` to `runs enter` and honor `--image` in
   `shell --run` as a per-invocation override (`run.json` is never rewritten).
4. Make `driver_id` a required keyword argument across `podman.py` and cli
   helpers; split `cli.py` into `cli.py` + `lifecycle.py` + `seed.py`.
5. No CI workflow. Enforce lint locally via E501 + documented commands.

---

## Step 1 — Symlink-safe dirty-file copying (audit finding: Medium security)

**Depends on:** nothing.

**Files:** `src/agentbox/gitops.py`, `tests/test_gitops.py`.

**Problem:** `_copy_or_remove` (gitops.py:240-251) uses `Path.exists()` (false
for broken symlinks → treated as deletion), `Path.is_dir()` (follows links),
`shutil.copy2` (follows links), and `shutil.copytree` without `symlinks=True`.
A worktree symlink like `credentials -> ~/.aws/credentials` would copy host
file contents into the run clone. Copying a regular file onto an existing dest
symlink also writes through to the link target (host file corruption).

**Change:** replace `_copy_or_remove` with:

```python
def _copy_or_remove(src: Path, dest: Path) -> None:
    if not os.path.lexists(src):
        _remove_dest(dest)
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    if src.is_symlink():
        _remove_dest(dest)
        os.symlink(os.readlink(src), dest)
    elif src.is_dir():
        if dest.is_symlink() or dest.is_file():
            dest.unlink()
        shutil.copytree(
            src,
            dest,
            symlinks=True,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns(".git"),
        )
    else:
        if dest.is_symlink() or dest.is_dir():
            _remove_dest(dest)
        shutil.copy2(src, dest)


def _remove_dest(dest: Path) -> None:
    if dest.is_symlink() or dest.is_file():
        dest.unlink()
    elif dest.is_dir():
        shutil.rmtree(dest)
```

`os` is already imported in gitops.py. Do not touch `copy_dirty_paths` — its
rename handling calls `_copy_or_remove` and benefits automatically.

**Tests to add (in `GitOpsTests`, follow existing tempfile+git patterns):**

1. `test_clone_include_dirty_preserves_file_symlink`: init repo, commit a base
   file, create untracked symlink `link.txt` -> a sentinel file outside the
   repo. Clone with `include_dirty=True`. Assert clone's `link.txt`
   `is_symlink()`, `os.readlink` matches, and the sentinel content was NOT
   copied as file contents.
2. `test_clone_include_dirty_preserves_directory_symlink`: untracked dir
   symlink -> a directory containing a file. Assert clone path is a symlink,
   not a copied tree.
3. `test_clone_include_dirty_preserves_broken_symlink`: untracked
   `broken.txt` -> nonexistent target. Assert clone has a broken symlink
   (`is_symlink()` true, `exists()` false). Add a second variant: tracked file
   deleted and replaced by a broken symlink → clone path becomes the broken
   symlink (old code deleted it).
4. `test_copy_dirty_paths_does_not_write_through_dest_symlink`: commit a
   symlink `probe` -> host sentinel file; then replace it in the worktree with
   a regular file (git porcelain typechange). Clone with `include_dirty=True`.
   Assert clone's `probe` is a regular file with the new content AND the host
   sentinel file content is unchanged.

**Commit message:** `Preserve symlinks when copying dirty files into run clones`

---

## Step 2 — Pin managed image dependencies (audit finding: Medium security)

**Depends on:** nothing (step 3 must follow it).

**Files:** `src/agentbox/templates/codex/Containerfile`,
`src/agentbox/templates/kilo/Containerfile`, `.agentbox/codex/Containerfile`,
`.agentbox/kilo/Containerfile`, `src/agentbox/drivers/codex.py`,
`src/agentbox/drivers/kilo.py`, `agentbox.toml`, `tests/test_config.py`,
`tests/test_templates.py`, `README.md`.

**First, resolve and write down (in the commit message) the current pins:**

- Ubuntu digest (multi-arch manifest list): `skopeo inspect --raw
  docker://docker.io/library/ubuntu:24.04 | sha256sum`, or
  `podman pull ubuntu:24.04` then `podman image inspect --format '{{.Digest}}'
  ubuntu:24.04`. Use the manifest-LIST digest so x86_64 and aarch64 both work.
- Codex version: `curl -fsSL https://api.github.com/repos/openai/codex/releases/latest`
  → tag `rust-v<X.Y.Z>` → pin `<X.Y.Z>`.
- Kilo version: `npm view @kilocode/cli version`.
- uv version: latest `astral-sh/uv` release tag.

**Template edits** (both templates keep the `FROM @@BASE_IMAGE@@` token; the
digest comes from the new driver defaults below):

- codex template: add `python-is-python3` to the apt list; add after the apt
  block `RUN curl -LsSf https://astral.sh/uv/<UV_VERSION>/install.sh | env
  UV_UNMANAGED_INSTALL=/usr/local/bin sh`; add `ENV CODEX_RELEASE=<CODEX_VERSION>`
  before the codex install RUN (the installer verifies SHA-256 of release
  assets internally, so the pinned `curl | sh` form is acceptable). Do NOT
  remove `sudo` here — that is step 3.
- kilo template: add `python-is-python3`; add the same pinned uv line; change
  to `npm install -g @kilocode/cli@<KILO_VERSION>`. Keep `USER ubuntu`.
- Repo-local `.agentbox/codex/Containerfile` and `.agentbox/kilo/Containerfile`:
  apply the identical rendered content (they already have the uv line and the
  kilo `@7.4.11` pin — bump the kilo pin if a newer version was chosen; the
  codex one gets `CODEX_RELEASE`, `python-is-python3`, and the pinned uv URL).

**Driver defaults:** in `CodexDriver.default_settings` (codex.py:21-27) and
`KiloDriver.default_settings` (kilo.py:25-31), change
`base_image="ubuntu:24.04"` to `base_image="ubuntu:24.04@sha256:<DIGEST>"`.

**Repo config:** update `[codex] base_image` in the root `agentbox.toml` to the
digest-pinned ref. (No `[kilo]` section exists there; the new default covers
it.)

**README:** add a short paragraph near the image-management section: managed
images pin the base image by digest and harness tools by version; refreshing a
dependency means intentionally editing the version/digest and running
`agentbox <harness> build --rebuild`; note `--pull=newer` cannot refresh a
digest-pinned base.

**Tests:**

- Update `tests/test_config.py:28`: expected `settings.base_image` becomes the
  new digest-pinned default.
- Add `test_default_containerfiles_pin_dependencies` to
  `tests/test_templates.py`: render both drivers' `default_containerfile` with
  `default_settings({})` and assert: `FROM ubuntu:24.04@sha256:` first line;
  codex render matches `CODEX_RELEASE=\d+\.\d+\.\d+`; kilo render matches
  `@kilocode/cli@\d+\.\d+\.\d+`; both match
  `astral\.sh/uv/\d+\.\d+\.\d+/install\.sh`.

**Commit message:** `Pin base image digest and harness dependency versions`

---

## Step 3 — Podman hardening (audit finding: Low security)

**Depends on:** step 2 (same four Containerfiles).

**Files:** `src/agentbox/podman.py`, the four Containerfiles from step 2,
`tests/test_podman.py`, `README.md`.

**Changes:**

- In `render_run_command` (podman.py:237-247), extend the fixed argv prefix to:

```python
    args = [
        "podman",
        "run",
        "--rm",
        "-it",
        "--userns=keep-id",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--workdir",
        workspace,
        "-v",
        f"{run_repo.resolve()}:{workspace}{run_repo_suffix}",
    ]
```

- Remove the `sudo \` apt line from all four Containerfiles.
- README: document both flags, the sudo removal, and why the network stays
  unrestricted (harnesses need API + package access; the isolation boundary is
  the unmounted host checkout plus rootless keep-id, not the network).

**Test:** add `test_render_run_command_hardens_container` to
`tests/test_podman.py`: assert `--cap-drop=ALL` and
`--security-opt=no-new-privileges` are in the rendered command.

**Commit message:** `Harden podman run with dropped capabilities and no-new-privileges`

---

## Step 4 — Mount validation: colons and normalization (audit finding: Low security)

**Depends on:** step 3 (same file).

**Files:** `src/agentbox/podman.py`, `tests/test_podman.py`.

**Change:** add `import posixpath` at the top of podman.py and rewrite
`validate_mount` (podman.py:311-325) as:

```python
def validate_mount(mount: MountSpec, workspace: str, targets: set[str]) -> None:
    if ":" in mount.target:
        raise RuntimeError(f"mount target must not contain ':': {mount.target}")
    source = mount.source.expanduser()
    if ":" in str(source):
        raise RuntimeError(f"mount source must not contain ':': {source}")
    if not mount.target.startswith("/"):
        raise RuntimeError(f"mount target must be absolute: {mount.target}")
    normalized_target = posixpath.normpath(mount.target)
    normalized_workspace = posixpath.normpath(workspace)
    if normalized_target in {"/", normalized_workspace} or normalized_target.startswith(
        normalized_workspace + "/"
    ):
        raise RuntimeError(f"mount target interferes with workspace: {mount.target}")
    if normalized_target in targets:
        raise RuntimeError(f"duplicate mount target: {mount.target}")
    if source.resolve() == Path("/"):
        raise RuntimeError(f"mount source must not be root: {source}")
    targets.add(normalized_target)
```

Behavior preserved for all built-in driver mounts (their targets are already
clean absolute paths). `render_mount` is unchanged — hostile values are
rejected before rendering.

**Tests to add:**

- `test_mount_target_with_colon_is_rejected`: target `/sta:e` →
  "must not contain".
- `test_mount_source_with_colon_is_rejected`: source `Path(tmp)/"co:lon"` →
  "must not contain".
- `test_mount_target_dotdot_evasion_is_rejected`: target `/other/../workspace`
  → "interferes with workspace" (evades today's rstrip-based check).
- `test_duplicate_targets_rejected_after_normalization`: targets `/state` and
  `/state/./` → "duplicate mount target".

**Commit message:** `Reject colons and normalize targets in mount validation`

---

## Step 5 — run_store filesystem-root guard (audit finding: Informational)

**Depends on:** nothing.

**Files:** `src/agentbox/config.py`, `tests/test_config.py`.

**Change:** in `load_config`, immediately after
`run_store = _resolve_repo_path(repo_root, run_store_raw)` (config.py:96), add:

```python
    if run_store.resolve() == Path(run_store.resolve().anchor):
        raise RuntimeError("run_store must not be the filesystem root")
```

**Test:** `test_run_store_rejects_filesystem_root` — write
`[runtime]\nrun_store = "/"` to `agentbox.toml`, assert
`assertRaisesRegex(RuntimeError, "filesystem root")` on `load_config`.

**Commit message:** `Reject filesystem root as run_store`

---

## Step 6 — Resilient run metadata + symlinked-store regression test (audit: maintainability)

**Depends on:** nothing.

**Files:** `src/agentbox/runs.py`, `tests/test_runs.py`, `tests/test_cli.py`.

**Change:** add `import sys` to runs.py and make `list_runs` skip bad entries:

```python
def list_runs(run_store: Path) -> list[RunMetadata]:
    if not run_store.exists():
        return []
    found: list[RunMetadata] = []
    for path in sorted(run_store.iterdir()):
        if not path.is_dir() or not (path / METADATA_FILE).exists():
            continue
        try:
            found.append(read_metadata(path))
        except (OSError, json.JSONDecodeError, TypeError, KeyError, ValueError) as exc:
            print(
                f"agentbox: warning: skipping invalid run metadata in {path}: {exc}",
                file=sys.stderr,
            )
    return found
```

Keep `read_metadata` strict — direct single-run operations (`load_run`) must
still fail loudly.

**Tests:**

- `test_list_runs_skips_corrupt_metadata` (test_runs.py): one valid run plus a
  dir whose `run.json` is `not json{`. Assert only the valid run is returned
  and stderr (via `contextlib.redirect_stderr(io.StringIO())`) contains
  "skipping invalid run metadata".
- `test_list_runs_skips_forward_incompatible_metadata`: `run.json` with an
  extra `"future_field": 1` (TypeError from `RunMetadata(**data)`) → skipped
  with warning.
- `test_runs_prune_rejects_symlinked_run_store_entry` (test_cli.py): create a
  real run dir OUTSIDE the store with a `run.json`; create a symlink
  `<run_store>/linked` pointing to it. Run `cmd_runs_prune` with
  `run_id=["linked"]`. Assert status 2, stderr contains
  "invalid run id: linked", and the outside directory still exists.
  (`resolve_run_dir` already rejects this — the test is a regression guard.)

**Commit message:** `Skip invalid run metadata with a warning instead of failing`

---

## Step 7 — `--image` support for saved-run entry (audit finding: Low UX/security guidance)

**Depends on:** nothing (step 9 must follow it).

**Files:** `src/agentbox/cli.py`, `tests/test_cli.py`, `README.md`.

**Changes in cli.py:**

1. In `build_parser`, after `runs_enter.add_argument("--dry-run", ...)` add:
   `runs_enter.add_argument("--image", default=None)`.
2. `cmd_runs_enter` becomes:

```python
def cmd_runs_enter(args: argparse.Namespace) -> int:
    config = context(args)
    metadata = load_run(config, args.run_id)
    image = args.image or metadata.image
    if args.image is None:
        ensure_saved_run_image(config, metadata, args.dry_run)
    command = "exec bash"
    return run_container(
        config, image, Path(metadata.run_repo), command, args.dry_run, metadata.driver
    )
```

3. In `cmd_harness_shell`, the `if args.run_id:` branch: after the
   driver-mismatch check, compute `image = args.image or metadata.image`, call
   `ensure_saved_run_image` only when `args.image is None`, and pass `image`
   (not `metadata.image`) to `run_container`.
4. Update the `ensure_saved_run_image` RuntimeError text to:

```python
    raise RuntimeError(
        f"image {image} for run {metadata.id} is missing and has no Containerfile "
        "snapshot to rebuild from; rebuild it manually or enter with "
        f"`agentbox runs enter {metadata.id} --image <image>`"
    )
```

(Keep the substring "no Containerfile snapshot" — an existing test matches it.)

**README:** one sentence in "Bring Work Back": `runs enter` and
`<harness> shell --run` accept `--image` to override the saved image for that
session without rewriting run metadata.

**Tests:**

- Parser: `parse_args(["runs", "enter", "abc", "--image", "ubuntu:24.04"])`
  sets `args.image`.
- `test_runs_enter_image_override_skips_image_check`: metadata with no
  containerfile; `mock.patch("agentbox.cli.podman.image_exists",
  return_value=False)`; call `cmd_runs_enter(args(repo=root, run_id=...,
  dry_run=True, image="override:tag"))` → status 0, stdout contains
  "override:tag", no exception.
- `test_shell_run_image_override_is_honored`: same via `cmd_harness_shell`
  with `run_id`, `driver_id="codex"`, `image="override:tag"`, `dry_run=True`
  → dry-run output contains "override:tag".

**Commit message:** `Support --image override when entering saved runs`

---

## Step 8 — Enforce line length locally (audit finding: Low; no CI per decision)

**Depends on:** steps 3, 4, 7.

**Files:** `pyproject.toml`, `src/agentbox/cli.py`, `src/agentbox/podman.py`,
`README.md`.

**Changes:**

- Add to pyproject.toml:

```toml
[tool.ruff.lint]
extend-select = ["E501"]
```

- Run `uv run ruff check`; wrap every reported line. Currently: cli.py lines
  314, 353, 378, 659, 671 and podman.py line 329 (numbers may have shifted
  after earlier steps — trust ruff's output, not this list). Rewrap by splitting
  f-strings into adjacent literals; do not change behavior.
- README: in the test-command area, add `uv run ruff check` as the lint
  command alongside the unittest command.

**Commit message:** `Enable ruff E501 and wrap long lines`

---

## Step 9 — Dead code removal, required driver_id, small maintainability items

**Depends on:** step 7.

**Files:** `src/agentbox/cli.py`, `src/agentbox/config.py`,
`src/agentbox/podman.py`, `src/agentbox/runs.py`,
`src/agentbox/drivers/base.py`, `src/agentbox/drivers/kilo.py`,
`tests/test_cli.py`, `tests/test_config.py`, `tests/test_podman.py`,
`tests/test_runs.py`, `tests/helpers.py`, `README.md`.

**Changes (in this order):**

1. Delete `referenced_image_tags` (cli.py:262-263) — confirmed unused.
2. Delete the five `cmd_codex_*` aliases (cli.py:827-831). Update the three
   test call sites `cli.cmd_codex_run(args)` in test_cli.py (~lines 398, 422,
   468) to `cli.cmd_harness_run(args)`.
3. Delete `Config.image_name`, `Config.base_image`, `Config.codex_home`,
   `Config.workspace_folder`, and `Config._codex_settings` (config.py:39-59).
   Update: test_config.py:20 → `config.driver_settings("codex").codex_home`;
   test_podman.py:51 and :288 → same expression. Also drop the now-unused
   `CodexSettings` import from config.py if nothing else uses it.
4. Make `driver_id` a required keyword argument (remove `= "codex"` defaults)
   in podman.py: `harness_containerfile_path`, `harness_image_name`,
   `build_image`, `build_tagged_image`, `current_managed_image`,
   `ensure_managed_image`, `managed_build_command`, `list_managed_images`,
   `ensure_harness_containerfile`, `default_containerfile_digest`,
   `render_run_command`; and in cli.py: `referenced_image_refs`,
   `current_managed_image_or_none`, `prepare_run`, `resolve_run_image`,
   `run_container`. Make `runs.create_metadata`'s `driver` required too.
   KEEP `RunMetadata.driver = "codex"` and the `data.setdefault("driver",
   "codex")` in `read_metadata` — they provide backward compatibility for old
   run dirs. Update every call site: production callers already pass
   `driver_id`; test callers that omit it must add `driver_id="codex"`
   (test_podman.py calls to `ensure_harness_containerfile(config)`,
   `current_managed_image(config)`, `build_image(config)`,
   `list_managed_images(config)`, `render_run_command(...)` in codex tests;
   test_cli.py line ~367 `cli.podman.ensure_harness_containerfile(config)`;
   any `create_metadata(...)` without `driver=` — grep for them).
5. kilo.py: add module constant `KILO_DATA_MOUNT_DESCRIPTION = "Kilo XDG
   data"`; use it in `state_mounts`' MountSpec and in the `diagnostics` filter
   instead of the inline string.
6. `build_tagged_image` and `build_image`: change return annotation to
   `-> None`, delete the `return cmd` / `return exists_cmd` statements and the
   `return build_tagged_image(...)` in `build_image` (just call it). All
   production callers ignore the return value — verify with grep before
   committing.
7. cli.py: change `from agentbox.template import render_template` to
   `from .template import render_template`; in `register_driver_commands`
   remove the private `argparse._SubParsersAction` parameter annotation (leave
   the parameter unannotated — argparse exposes no public type).
8. Add a comment above the `complete_run(...)` calls in `cmd_harness_run` and
   `cmd_harness_shell`: pull handling intentionally runs even after a non-zero
   harness exit so non-interactive pull modes can import work from a failed
   run. Add the same sentence to the README "Bring Work Back" section.
9. OPTIONAL (only if trivial): extract a small helper in drivers/base.py for
   the three common `str(section.get(key, defaults.key))` lines shared by both
   drivers' `load_settings`.

**Commit message:** `Remove dead code and require explicit driver identifiers`

---

## Step 10 — Split cli.py into cli / lifecycle / seed modules

**Depends on:** steps 7, 8, 9.

**Files:** `src/agentbox/cli.py`, new `src/agentbox/lifecycle.py`, new
`src/agentbox/seed.py`, `tests/test_cli.py`.

**Move to `seed.py`** (with their imports: os, shutil, stat, tempfile, Path,
and `from .drivers import RunSeedFileSpec, get_driver`):

- `copy_seed_file`, `seed_run_files`, `warn_seed_failure`,
  `snapshot_containerfile`.

**Move to `lifecycle.py`** (imports: Path, shlex, subprocess, sys, os,
`from . import gitops, podman, runs`, `from .config import Config`,
`from .drivers import get_driver`, `from .seed import seed_run_files,
snapshot_containerfile`):

- `prepare_run`, `resolve_run_inputs`, `resolve_dirty_mode`, `load_run`,
  `resolve_run_dir`, `resolve_run_image`, `ensure_saved_run_image`,
  `complete_run`, `resolve_pull_mode`, `resolve_sign_imports`,
  `print_commit_preview`, `print_later_message`, `run_container`,
  `referenced_image_refs`, `current_managed_image_or_none`.
- Also move the `LOG_PREVIEW_LIMIT` constant.

**Keep in cli.py:** `main`, `build_parser`, `register_driver_commands`, all
`cmd_*` handlers, `context`, `selected_driver_id`, `repo_root`,
`add_sign_import_args`, `PULL_CHOICES`. cli.py imports the moved names from
`.lifecycle` / `.seed` as needed. Do NOT leave compatibility aliases — update
all references.

**Update `tests/test_cli.py`:**

- `from agentbox import cli, gitops, runs` → add `from agentbox import
  lifecycle, podman, seed`.
- `cli.prepare_run` → `lifecycle.prepare_run` (≈10 sites).
- `cli.ensure_saved_run_image` → `lifecycle.ensure_saved_run_image` (2 sites).
- `cli.complete_run` → `lifecycle.complete_run` (1 site).
- `cli.run_container` → `lifecycle.run_container` (1 site).
- `cli.referenced_image_refs` → `lifecycle.referenced_image_refs` (1 site).
- `cli.podman.ensure_harness_containerfile(config, ...)` →
  `podman.ensure_harness_containerfile(config, ...)`.
- Mock targets: `agentbox.cli.podman.ensure_managed_image` →
  `agentbox.lifecycle.podman.ensure_managed_image` (≈4 sites);
  `agentbox.cli.podman.image_exists` → `agentbox.lifecycle.podman.image_exists`;
  `agentbox.cli.shutil.copyfileobj` → `agentbox.seed.shutil.copyfileobj`.

**Sanity check:** `src/agentbox/cli.py` should shrink to roughly 400-450
lines; public behavior unchanged.

**Commit message:** `Split cli.py into cli, lifecycle, and seed modules`

---

## Step 11 — Document security invariants (audit finding: Informational)

**Depends on:** steps 2, 3, 7.

**Files:** `src/agentbox/gitops.py`, `src/agentbox/runs.py`,
`src/agentbox/config.py`, `README.md`.

**Changes:**

- `gitops.fetch_head`: add a docstring/comment — fetching from the
  agent-controlled run clone is safe only because it is a local-path fetch,
  which executes no repository hooks; keep this restricted to local paths and
  do not add remote-URL fetching without a new trust review.
- `runs.py` near `METADATA_FILE`: comment that `run.json` is trusted input
  because run directories are never mounted into containers; if that ever
  changes, metadata parsing must become defensive.
- `config.py` at the step-5 guard: brief comment explaining it protects
  `runs prune --all` from deleting arbitrary directories.
- README: verify the refresh-model note (step 2), hardening/network rationale
  (step 3), `--image` override note (step 7), and post-failure pull note
  (step 9.8) are all present; add any that are missing.

**Commit message:** `Document host-side trust-boundary invariants`

---

## Step 12 — Final validation (no commit; report results)

**Depends on:** all previous steps.

1. `uv run python -m unittest discover -s tests` — all green.
2. `uv run ruff check` — green (E501 enabled).
3. `uv run agentbox --help` and `uv run agentbox doctor` work.
4. In a scratch git repo with an `agentbox.toml`: `uv run agentbox codex run
   --dry-run --dirty ignore --pull later` — inspect the printed podman argv:
   contains `--cap-drop=ALL`, `--security-opt=no-new-privileges`, no host
   checkout mount.
5. Confirm rendered templates and repo-local `.agentbox/*/Containerfile`
   contain the pins (covered by the step-2 test).
6. Optional, only if podman is available: `agentbox kilo build` and a real
   `agentbox kilo shell --dry-run=false` smoke run to confirm the harness
   works under the dropped capabilities.

---

## Risks / notes for all agents

- Pinned versions/digests go stale by design; record the exact pins chosen in
  the step-2 commit message. The README refresh-model note is the mitigation.
- Pin/sudo edits change Containerfile contents → new content-addressed image
  tags; old images linger until `agentbox <harness> prune`. Expected.
- Existing saved runs keep working: their snapshot Containerfiles (old unpinned
  recipes) still rebuild via `ensure_saved_run_image`.
- `tests/test_config.py:28` and any test asserting `ubuntu:24.04` as the
  default base image must be updated in step 2.
- When two steps list the same file, the dependency map already sequences them;
  do not batch steps into one commit.

## Out of scope

- CI/CD workflow files of any kind (user decision).
- Changing the unrestricted-network default.
- Re-architecting the driver registry beyond the listed items.
