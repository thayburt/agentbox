# Harness-specific Containerfile directories

## Goal and decisions

Move each managed harness Containerfile to `.agentbox/<canonical-harness-id>/Containerfile` so harness-specific assets share one directory. This is an intentional clean break: do not detect, migrate, or fall back to legacy `.agentbox/<harness>.Containerfile` files. Derive the path from the canonical driver ID rather than retaining a configurable per-driver filename.

The Podman build context remains `.agentbox`; `.agentbox/runs/<run-id>/Containerfile` snapshots and `.agentbox/kilo/kilo.jsonc` are unchanged.

## Implementation steps

1. **Move the checked-in Containerfiles without regenerating them.**
   - Move `.agentbox/codex.Containerfile` to `.agentbox/codex/Containerfile`.
   - Move `.agentbox/kilo.Containerfile` to `.agentbox/kilo/Containerfile`, alongside `kilo.jsonc`.
   - Preserve file contents byte-for-byte so the existing extra packages, `uv` install, and content-derived image tags do not change.

2. **Enforce the new path convention in the driver/runtime model.**
   - In `src/agentbox/podman.py`, make `harness_containerfile_path()` canonicalize through `get_driver()` and return `repo_root / ".agentbox" / driver.id / "Containerfile"`.
   - Keep Containerfile creation behavior unchanged apart from location; `ensure_harness_containerfile()` must create the harness directory and must not overwrite an existing nested Containerfile.
   - Remove `containerfile_name` from `HarnessDriver` in `src/agentbox/drivers/base.py` and from `CodexDriver` and `KiloDriver`, since all drivers now follow the same convention.
   - Do not alter the `.agentbox` build context, `.containerignore`, run snapshot paths, Kilo config paths/mounts, or devcontainer Dockerfile handling.

3. **Update and strengthen path-focused tests.**
   - Update `tests/test_podman.py` to expect `.agentbox/codex/Containerfile` in generated build commands while still asserting that the final build context is `.agentbox`.
   - Update Kilo creation assertions to verify the complete `.agentbox/kilo/Containerfile` location rather than the old filename.
   - Cover canonical alias resolution so `kilocode` uses `.agentbox/kilo/Containerfile`.
   - Update `tests/test_cli.py` so `agentbox init` is verified to create both nested Containerfiles, preserve customized files on rerun, and coexist with `.agentbox/kilo/kilo.jsonc`.
   - No legacy-path compatibility tests should be added; old paths are deliberately unsupported.

4. **Update user-facing documentation.**
   - Replace old paths in `README.md` setup output, run reproducibility explanation, and customization instructions with `.agentbox/codex/Containerfile`, `.agentbox/kilo/Containerfile`, or `.agentbox/<harness>/Containerfile` as appropriate.
   - Keep `.agentbox/runs/<run-id>/Containerfile` documentation unchanged.

## Validation

1. Run the full suite: `uv run python -m unittest discover -s tests -v`.
2. Run dry-run builds for both canonical harnesses and the Kilo alias; confirm each `podman build -f` path is nested while the final context argument remains `.agentbox`:
   - `uv run agentbox codex build --dry-run`
   - `uv run agentbox kilo build --dry-run`
   - `uv run agentbox kilocode build --dry-run`
3. Search tracked source, tests, and README content for stale `codex.Containerfile`, `kilo.Containerfile`, `.agentbox/<harness>.Containerfile`, and `containerfile_name` references; none should remain outside historical artifacts not part of the change.
4. Confirm the final tree contains `.agentbox/codex/Containerfile`, `.agentbox/kilo/Containerfile`, and `.agentbox/kilo/kilo.jsonc`, with no top-level harness Containerfiles.

## Risk note

Existing external checkouts with customized legacy Containerfiles will need to move those files manually. The implementation must not silently generate compatibility behavior that could obscure this clean-break decision.
