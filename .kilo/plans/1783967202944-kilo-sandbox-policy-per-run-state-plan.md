# Kilo Sandbox-Policy Per-Run State Mount

## Problem
Kilo CLI creates `~/.local/state/kilo-sandbox-policy` at startup (even with `sandbox=false` forced via `KILO_CONFIG_CONTENT`). Inside agentbox containers, only `~/.local/state/kilo` is bind-mounted; podman creates the missing parent `/home/ubuntu/.local/state` root-owned, so under `--userns=keep-id` the runtime user gets EACCES creating the sibling `kilo-sandbox-policy` directory, and Kilo breaks.

## Decision
Give each run a private, writable `kilo-sandbox-policy` directory stored **inside the existing run directory**:

- Host: `<run_store>/<run_id>/state/kilo-sandbox-policy` (i.e. `.agentbox/runs/<run_id>/state/kilo-sandbox-policy`)
- Container: `/home/ubuntu/.local/state/kilo-sandbox-policy`, read-write, `create=True`, `chown=True` (renders `:U` plus SELinux suffix like other mutable state mounts)

Rationale (agreed with user):
- Reuses run lifecycle for free: `runs prune` deletes the whole run dir, so the state is cleaned up automatically.
- `.containerignore` already excludes `runs` from the build context.
- `resolve_run_dir` path validation already covers the run store.
- Sandbox policy state stays per-run (not shared across runs, never written to host XDG state), preserving isolation.
- Rejected alternatives: shared host mount of `~/.local/state/kilo-sandbox-policy` (leaks policy state across runs/host), tmpfs (lost on `runs enter` re-entry, keep-id ownership quirks), new `.agentbox/state/<run_id>` tree (duplicates prune/ignore/validation machinery).

## Design

### 1. New driver hook: `run_state_mounts`
Add to `HarnessDriver` protocol in `src/agentbox/drivers/base.py`:

```python
def run_state_mounts(
    self, settings: object, host_env: Mapping[str, str], run_dir: Path
) -> list[MountSpec]: ...
```

- `CodexDriver` (`src/agentbox/drivers/codex.py`): return `[]`.
- `KiloDriver` (`src/agentbox/drivers/kilo.py`): return one mount:
  - `MountSpec(run_dir / "state" / "kilo-sandbox-policy", f"{KILO_HOME}/.local/state/kilo-sandbox-policy", "directory", create=True, chown=True, description="Kilo sandbox policy state")`

Keep it a generic hook (not hardcoded in podman.py) so future per-run-writable paths for any driver land in one place. Do NOT change the existing `state_mounts` signature — diagnostics/doctor call it without run context and stay unchanged.

### 2. Plumb `run_dir` through podman
`run_dir` is always `run_repo.parent` (`run_repo` is `<run_store>/<run_id>/repo` in all call paths: run, shell, shell --run, runs enter).

- `podman.render_run_command`: already receives `run_repo`. After rendering `driver.state_mounts(...)`, also render `driver.run_state_mounts(settings, host_env, run_repo.parent)` through `validated_state_mounts(..., check_sources=False)`, before `config_mounts`. Note `render_mount` uses `Path.resolve()` which tolerates a not-yet-existing source.
- `podman.ensure_state_mounts`: add a `run_repo: Path` parameter (or `run_dir`). Validate + `mkdir(parents=True)` the run-state mounts alongside existing mutable state mounts (same `create=True` branch). Update the call site in `cli.run_container` (cli.py ~line 760), which already has `run_repo` in scope.
- Duplicate-target validation: `/home/ubuntu/.local/state/kilo-sandbox-policy` does not collide with existing targets; the shared `targets` set in `validated_state_mounts` guards regressions when validating state + run-state mounts together.

### 3. Diagnostics
No change required: `doctor` has no run context, and a missing per-run dir is created on demand. Do not add run-state paths to `KiloDriver.diagnostics`.

## Implementation Steps
1. Add `run_state_mounts` to the protocol in `drivers/base.py`; implement empty in `CodexDriver`, real mount in `KiloDriver`.
2. Update `podman.render_run_command` to include run-state mounts (workspace/run clone → state → run-state → config → devcontainer order).
3. Update `podman.ensure_state_mounts` to accept the run dir (via `run_repo`), validate, and create run-state mount sources; update the `cli.run_container` call site.
4. Tests:
   - `tests/test_drivers.py`: `KiloDriver.run_state_mounts` returns the sandbox-policy mount rooted at `run_dir / "state"`, targeting `/home/ubuntu/.local/state/kilo-sandbox-policy`, rw, create+chown; Codex returns `[]`.
   - `tests/test_podman.py`: rendered command includes `<run_dir>/state/kilo-sandbox-policy:/home/ubuntu/.local/state/kilo-sandbox-policy:U` (plus SELinux suffix when enabled); `ensure_state_mounts` creates the directory under the run dir; dry-run rendering works when the dir does not exist yet.
   - Confirm existing Codex tests pass unchanged.
5. Update README Kilo section: note that Kilo sandbox-policy state is per-run under `.agentbox/runs/<run_id>/state/` and removed by `runs prune`.

## Validation
- `uv run python -m unittest discover -s tests -v`
- Manual: `agentbox --driver kilo shell` (or `run`) and verify Kilo starts without EACCES; `ls .agentbox/runs/<id>/state/kilo-sandbox-policy` on host; `agentbox runs prune <id>` removes it.

## Risks / Notes
- If future Kilo versions write additional siblings under `~/.local/state` (or other home paths), add them to `KiloDriver.run_state_mounts` — the mechanism is now in place.
- Per-run sandbox-policy state persists across `runs enter` re-entries of the same run (intended) but is never shared between runs or with the host.
- `ensure_state_mounts` signature change is internal; keep the parameter required to avoid silently skipping run-state creation.
