# Agentbox Audit

## Overall Assessment

Agentbox has a thoughtful security model: the host checkout is never mounted,
configuration is read from the host rather than the run clone, run-id traversal
is guarded, seed-file copying uses `O_NOFOLLOW` plus atomic hardlinking, and
commit signing stays on the host. The test suite is above average for a project
of this size, using real Git repositories, a fake GPG harness, and negative
tests for mount validation and symlinked seed files.

The primary weaknesses are a symlink-handling issue in dirty-file copying,
unpinned image dependencies that undermine reproducibility, and maintainability
drift in the CLI and driver implementations.

## Security Architecture

### Strengths

- The host checkout is not mounted into containers. Project configuration is
  read from the host checkout, preventing an agent from rewriting the policy
  that started it.
- Prompt construction is injection-safe: `cli.py` uses `shlex.join` before
  invoking `bash -lc`, and Podman arguments are passed as argv elements.
- `resolve_run_dir` in `src/agentbox/cli.py:593-599` rejects path traversal and
  symlink escapes before destructive operations.
- `copy_seed_file` in `src/agentbox/cli.py:499-522` safely avoids following
  source symlinks and atomically avoids overwriting an existing destination.
- Only explicitly selected environment variables are passed into containers.

### Findings

#### Medium: Dirty-file copying dereferences symlinks

`src/agentbox/gitops.py:240-251` uses `shutil.copy2` and `shutil.copytree` in a
way that follows symlinks. When `--dirty include` is selected, a worktree
symlink such as `credentials -> ~/.aws/credentials` can copy the contents of a
host file into the run clone. The full-permission agent can then read or commit
that material. A broken symlink is also treated as a deletion because
`Path.exists()` returns false.

Preserve symlinks with `os.symlink(os.readlink(source), destination)` and
`copytree(..., symlinks=True)`, or reject non-regular files with a warning.
Add regression tests for file symlinks, directory symlinks, and broken links.

#### Medium: Managed image tags are not reproducible

`src/agentbox/templates/codex/Containerfile:23-24` installs Codex with an
unpinned `curl | sh` script. `src/agentbox/templates/kilo/Containerfile:26-27`
uses an unpinned global npm installation. Both images use `ubuntu:24.04` by
mutable tag.

An image tag derived only from the Containerfile hash does not identify stable
image contents: the same recipe rebuilt later may contain different base image,
installer, or package bits. This also affects rebuilding a saved run from its
Containerfile snapshot.

Pin base images by digest and harness dependencies by version and, where
available, checksum or signature. Document that dependency refreshes require an
intentional rebuild/update.

#### Low: Podman execution lacks defense-in-depth hardening

`src/agentbox/podman.py:227-253` uses `--rm`, `-it`, and
`--userns=keep-id`, but does not drop capabilities or prevent privilege gain.
The harnesses intentionally have broad permissions inside the container, making
the Podman boundary especially important.

Evaluate `--cap-drop=ALL`, adding back only required capabilities, and
`--security-opt=no-new-privileges`. Document the rationale for unrestricted
network access. Both image templates currently install `sudo`, which should be
removed if it is not required.

#### Low: Mount validation does not normalize or fully encode volume paths

`src/agentbox/podman.py:301-319` renders a mount as `source:target:options`.
A colon in a configured source or target can alter Podman's parsing. The
workspace protection compares raw targets, so a path such as
`/other/../workspace` can evade the check while resolving to `/workspace`.

Reject colons in mount components and normalize targets with `posixpath.normpath`
before checking target overlap.

#### Low: Saved-run image recovery guidance is inaccurate

`src/agentbox/cli.py:629-632` tells users to rerun with `--image`, but
`agentbox runs enter` has no such flag. In addition, `shell --run` ignores its
`--image` value. Either support this override in these paths or correct the
message.

#### Informational: Host-side Git trust boundary should be documented

`src/agentbox/gitops.py:135-137` and `152-156` fetch from a repository the
agent controlled. Local Git fetch does not execute repository hooks, so this is
reasonable, but the invariant should be documented to protect against unsafe
future changes. Likewise, `run.json` metadata is trusted because it is not
mounted into containers; documenting that invariant would make the design safer
to maintain.

#### Informational: Absolute run stores need guardrails

`src/agentbox/config.py:103-107` allows any absolute `run_store`. With a value
of `/`, `runs prune --all` could delete any direct child directory containing a
`run.json`. Rejecting the filesystem root is a low-cost safety check.

## Code Quality And Maintainability

### `cli.py` is overly broad

At 827 lines, `src/agentbox/cli.py` contains parser construction, command
handlers, run lifecycle orchestration, image resolution, pull/import handling,
and seed-file implementation. Split it into a thin CLI layer plus lifecycle and
seed modules. This reduces coupling and makes behavior easier to test directly.

### Dead code and abstraction leakage

- `referenced_image_tags` in `src/agentbox/cli.py:260-261` is unused.
- The `cmd_codex_*` aliases in `src/agentbox/cli.py:819-823` are unused.
- Codex-only convenience properties and `_codex_settings` in
  `src/agentbox/config.py:33-53` are unused outside that module and weaken the
  otherwise generic driver registry abstraction.
- Default `driver_id="codex"` parameters appear throughout `cli.py` and
  `podman.py`. Make the driver identifier required where practical so missing
  propagation cannot silently select Codex.

### Linting is configured but not enforced

`pyproject.toml` sets Ruff's line length to 100, but more than 30 source lines
exceed it. `src/agentbox/drivers/kilo.py` has lines exceeding 200 characters.
Ruff is not declared as a development dependency and no CI workflow runs tests
or lint.

Add Ruff to a development dependency group, add a CI job for unit tests and
`ruff check`, then reformat the existing long lines.

### Run metadata is not resilient to corruption

`src/agentbox/runs.py:59-72` will fail all listing, image-management, and prune
operations if one `run.json` is invalid or contains a newer unsupported field.
Skip invalid metadata with a warning so one damaged saved run does not block all
other runs.

### Smaller maintainability observations

- `kilo.py` diagnostics identify the data mount by the display string
  `"Kilo XDG data"`; use a named helper instead of coupling logic to text.
- `build_tagged_image` returns either the build command or image-exists command
  depending on the branch, so its return value has inconsistent meaning.
- `cli.py` mixes absolute and relative imports and annotates with the private
  `argparse._SubParsersAction` type.
- `codex.py` and `kilo.py` duplicate most of their settings-loading and TOML
  rendering patterns.
- `complete_run` continues into pull/import handling after a non-zero harness
  exit. This may be intentional, but it should be documented because
  non-interactive pull modes can import work after a failed harness run.

## Test Coverage

The existing suite is strong: 94 tests passed during this audit, including real
Git workflows, signed import simulation, mount-target validation, and safe seed
file behavior.

Add tests for:

- Dirty-copy behavior for regular, directory, and broken symlinks.
- Run-id traversal and symlinked run-store entries.
- Corrupt and forward-incompatible `run.json` files.
- Colon-containing mount components and normalized workspace targets.
- Saved-run image recovery behavior and its error message.

## Priority Actions

1. Fix dirty-file symlink dereferencing and add regression coverage.
2. Pin base image digests and harness dependency versions/checksums.
3. Add Podman capability and privilege hardening.
4. Split `cli.py` and remove obsolete Codex-specific/dead compatibility code.
5. Add Ruff and CI, then address the configured line-length violations.
6. Normalize and validate mount components; require driver IDs where possible.
7. Make corrupt run metadata non-fatal and correct saved-run recovery guidance.
