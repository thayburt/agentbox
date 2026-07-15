# Per-Run Kilo XDG Cache

## Goal and accepted decisions

Replace the Kilo driver's host-backed cache mount with a private cache owned by each saved Agentbox run.

- Remove `${XDG_CACHE_HOME:-~/.cache}/kilo` as a mount source entirely.
- Mount `<run_store>/<run-id>/cache` over the complete container cache root `/home/ubuntu/.cache`, so Kilo and other tools can create cache entries without encountering a root-owned parent.
- Keep `XDG_CACHE_HOME=/home/ubuntu/.cache` inside the container.
- Persist the cache across `kilo shell --run` and `runs enter` for the same saved run; never share it with another run.
- Delete it with the existing `agentbox runs prune` lifecycle.
- Make a clean break: do not read, seed, migrate, modify, or delete the old host cache. Existing saved Kilo runs receive an empty per-run cache on their first real re-entry after upgrading.
- Limit the change to cache handling. Preserve the existing host-backed Kilo XDG data and state mounts, configuration mounts, and sandbox-policy per-run state.
- Add no configuration option and no run-metadata field; the cache path is deterministically derived from `run_repo.parent`.

## Implementation steps

1. **Move cache ownership into `KiloDriver.run_state_mounts()`** (`src/agentbox/drivers/kilo.py`).
   - Remove the cache `MountSpec` from `state_mounts()` so that method returns only the existing host-backed XDG data and state mounts.
   - Do not resolve or use host `XDG_CACHE_HOME` for runtime mounts or directory creation.
   - Add this mount to `run_state_mounts()` alongside the existing sandbox-policy mount:
     - source: `run_dir / "cache"`
     - target: `f"{KILO_HOME}/.cache"`
     - kind: `"directory"`
     - read-write, `create=True`, `chown=True`
     - description identifying it as the per-run Kilo XDG cache
     - private relabeling so `selinux="auto"` renders `:Z`, consistent with the run clone's per-run isolation; explicit `z`, `Z`, or disabled runtime settings continue to control the final suffix.
   - Retain `XDG_CACHE_HOME=f"{KILO_HOME}/.cache"` in `env()`.
   - Mount the whole cache root, not only `.cache/kilo`; Kilo will naturally store its own entries below `<run-dir>/cache/kilo`.

2. **Reuse the existing generic run-mount lifecycle without new plumbing.**
   - `podman.render_run_command()` already combines `state_mounts()`, `run_state_mounts(..., run_repo.parent)`, and config mounts. The new mount must render as `<run-dir>/cache:/home/ubuntu/.cache:U` plus the configured SELinux suffix.
   - `podman.ensure_state_mounts()` already validates and creates creatable run-state sources before Podman starts; it must create `<run-dir>/cache` for real invocations.
   - Dry-run rendering must continue to show the deterministic cache mount without creating the source directory.
   - Saved-run entry paths already reuse `metadata.run_repo`, so they must resolve to the original run's cache automatically.
   - `cmd_runs_prune()` already removes the complete run directory, so no dedicated cache cleanup or failure rollback is needed.
   - No functional changes are expected in `drivers/base.py`, `podman.py`, `cli.py`, `runs.py`, or `config.py` unless tests reveal a missing generic invariant.

3. **Keep diagnostics host-focused and remove cache from their output.**
   - `KiloDriver.diagnostics()` has no run context and must not warn that a per-run cache is absent; it is created on demand.
   - Update the shared description/filter used by the host-backed data and state mounts if needed so it no longer implies that host cache is part of persistent Kilo storage.
   - Preserve the existing diagnostic severity behavior for host XDG data/state and existing Kilo config diagnostics.
   - A host `XDG_CACHE_HOME` path must not be reported, created, or used to decide diagnostic status.

4. **Update focused regression and lifecycle tests.**
   - `tests/test_drivers.py`:
     - Assert Kilo's persistent `state_mounts()` contain host data/state but no cache source or `/home/ubuntu/.cache*` target, even when host `XDG_CACHE_HOME` is set.
     - Update the run-state contract test to expect both `<run-dir>/cache -> /home/ubuntu/.cache` and the existing sandbox-policy mount, with the intended create/chown/read-write/relabel properties.
     - Keep Codex returning no run-state mounts.
     - Verify diagnostics exclude host cache paths and do not treat an existing host cache as persistent-state readiness.
   - `tests/test_podman.py`:
     - Replace the host cache assertion in the Kilo command-rendering test with the per-run whole-cache mount; assert the supplied host cache path is absent while `XDG_CACHE_HOME=/home/ubuntu/.cache` remains present.
     - Verify `ensure_state_mounts()` creates `<run-dir>/cache` and does not create `${XDG_CACHE_HOME}/kilo` or `~/.cache/kilo`.
     - Cover rendering before the cache source exists and confirm dry rendering has no filesystem side effects.
     - Under mocked SELinux support with `selinux="auto"`, expect the cache's private `:U,Z` suffix; preserve existing sandbox-policy expectations unless deliberately changed separately.
   - `tests/test_cli.py`:
     - Add or extend a Kilo saved-run dry-run test to prove `runs enter`/`kilo shell --run` derives the cache from the original run directory, without adding metadata.
     - Extend run-prune coverage with a cache subtree and verify deleting the run removes it.
   - Do not add a migration test: the required behavior is a cold cache and zero access to the old host cache.

5. **Update the Kilo runtime documentation** (`README.md`).
   - State that host XDG data and state remain mounted read-write, while host XDG cache is not mounted.
   - Document `<run_store>/<run-id>/cache -> /home/ubuntu/.cache`.
   - Explain that the cache survives entry into the same saved run, is isolated from other runs and the host, and is removed by `agentbox runs prune`.
   - Note that old host cache contents remain untouched and are not copied into new or existing runs.
   - Keep the existing sandbox-policy state documentation intact.

## Compatibility, failure modes, and boundaries

- Existing saved-run metadata remains valid. The cache directory is lazily created on first non-dry re-entry.
- A failed or interrupted container invocation may leave cache files in the saved run; this is intentional and they remain subject to normal run pruning.
- The per-run cache increases run-store disk usage. No independent cache quota or cache-only prune command is introduced.
- Mounting `/home/ubuntu/.cache` intentionally hides any cache content baked into that path in a custom image.
- Continue using Podman's `:U` ownership adjustment, but only against Agentbox-owned disposable run storage rather than the user's host cache.
- Explicit devcontainer environment, mounts, and run arguments retain their current precedence and remain capable of overriding Agentbox defaults; policing user-supplied devcontainer behavior is outside this change.
- Do not broaden this work to isolate Kilo XDG data/state, harden edited `run.json` paths, change sandbox-policy relabeling, or add generic parent/child mount-target validation.

## Validation

1. Run the complete suite:
   - `uv run python -m unittest discover -s tests -v`
2. Run a Kilo dry run with `XDG_CACHE_HOME` pointing at a sentinel host directory and verify:
   - the Podman command contains `<run-dir>/cache:/home/ubuntu/.cache`;
   - it does not contain the sentinel path;
   - the sentinel and dry-run cache source are not created or modified.
3. Start a real Kilo shell, create a probe below `$XDG_CACHE_HOME`, exit, and re-enter that saved run; verify the probe persists and `~/.cache` is writable by the `ubuntu` user.
4. Start a second run and verify the first run's probe is absent.
5. Prune the first run and verify its `cache/` subtree is removed with the rest of the run.
6. On an SELinux-enabled host, confirm the cache mount uses the private label and remains writable.
