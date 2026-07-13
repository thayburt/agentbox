# Kilo Config Support Specification

## Goal
Implement the config layering proposed in `.tasks/kilo-support/proposal.md` for Kilo inside agentbox containers:

1. Read the user's host Kilo global configs inside the container.
2. Add an Agentbox-specific Kilo config at `.agentbox/kilo/kilo.jsonc`, mounted read-only and injected with `KILO_CONFIG`.
3. Preserve Kilo project/repo configs from the isolated run clone (`kilo.json`, `.kilo/`, legacy `.kilocode/`, etc.).
4. Generate the Agentbox-specific Kilo config from `agentbox init` without overwriting user edits.

This is an adjustment to the current branch, which already has a `KiloDriver`, Kilo commands, Kilo Containerfile creation, state mounts, and `KILO_CONFIG_CONTENT` permission overrides.

## Key Decisions
- Use Kilo's actual config precedence: global/home config first, `KILO_CONFIG` next, project/workspace configs after that, and existing `KILO_CONFIG_CONTENT` last.
- Keep the existing `KILO_CONFIG_CONTENT` merge for `sandbox=false`, `sandbox_restrict_network=false`, and `permission="allow"`. This intentionally remains a high-precedence safety/runtime override and may override those keys from global, Agentbox, or project config.
- `.agentbox/kilo/kilo.jsonc` owns `KILO_CONFIG` inside the container when it exists.
- If the host already has `KILO_CONFIG` set and `.agentbox/kilo/kilo.jsonc` exists, Agentbox must let the Agentbox config win and print a warning to stderr whenever it starts/renders a container command for Kilo so developers know the host `KILO_CONFIG` was not passed through.
- If `.agentbox/kilo/kilo.jsonc` does not exist, preserve the current host `KILO_CONFIG` behavior, but mount it read-only.
- Config mounts must be read-only. Kilo data/state/cache mounts may remain read-write and creatable.
- Mount Agentbox config from the host repository root, not from the isolated run clone. The sandbox may contain or edit its own `.agentbox/kilo/kilo.jsonc`, but the container must continue using the host copy mounted at `/agentbox/config/kilo.jsonc`.

## Current Code Boundaries
- Driver protocol and shared types: `src/agentbox/drivers/base.py`
- Kilo behavior: `src/agentbox/drivers/kilo.py`
- Codex no-op compatibility: `src/agentbox/drivers/codex.py`
- Driver registry exports: `src/agentbox/drivers/__init__.py`
- Init/doctor/run orchestration: `src/agentbox/cli.py`
- Podman command rendering, mount validation, state creation: `src/agentbox/podman.py`
- Tests to update/add: `tests/test_drivers.py`, `tests/test_podman.py`, `tests/test_config.py`, `tests/test_cli.py`
- Docs to update after behavior changes: `README.md`

## Design

### 1. Add driver-owned init/config hooks
Extend `src/agentbox/drivers/base.py` with a small driver-owned spec instead of branching on driver id in orchestration:

```python
@dataclass(frozen=True)
class InitFileSpec:
    relative_path: Path
    contents: str
    description: str = ""
```

Add protocol methods with empty defaults implemented by each concrete driver:

```python
def init_files(self, settings: object) -> list[InitFileSpec]: ...
def config_mounts(self, settings: object, host_env: Mapping[str, str], repo_root: Path) -> list[MountSpec]: ...
def config_env(self, settings: object, host_env: Mapping[str, str], repo_root: Path) -> dict[str, str]: ...
def runtime_warnings(self, settings: object, host_env: Mapping[str, str], repo_root: Path) -> list[str]: ...
```

Codex should return empty lists/dicts for these hooks.

### 2. Generate `.agentbox/kilo/kilo.jsonc` in `init`
In `KiloDriver.init_files`, return one file:

- Source: `.agentbox/kilo/kilo.jsonc`
- Default content:

```jsonc
{
  "$schema": "https://app.kilo.ai/config.json"
}
```

Update `cmd_init` to iterate `driver.init_files(config.driver_settings(driver.id))`, create parent directories, write missing files, print `created ...`, and never overwrite existing files.

### 3. Split Kilo config mounts from mutable state mounts
Refactor `KiloDriver.state_mounts` so it only returns mutable non-config state:

- `$XDG_DATA_HOME/kilo` or `~/.local/share/kilo` -> `/kilo-home/.local/share/kilo`, directory, `create=True`
- `$XDG_STATE_HOME/kilo` or `~/.local/state/kilo` -> `/kilo-home/.local/state/kilo`, directory, `create=True`
- `$XDG_CACHE_HOME/kilo` or `~/.cache/kilo` -> `/kilo-home/.cache/kilo`, directory, `create=True`

Move config-related paths into `KiloDriver.config_mounts`, all read-only:

- `$XDG_CONFIG_HOME/kilo` or `~/.config/kilo` -> `/kilo-home/.config/kilo`, optional directory, no create, read-only
- `~/.kilo` -> `/kilo-home/.kilo`, optional directory, read-only
- `~/.kilocode` -> `/kilo-home/.kilocode`, optional directory, read-only
- Host `KILO_CONFIG_DIR`, when set -> `/kilo-host/KILO_CONFIG_DIR`, required directory, no create, read-only
- Host `KILO_CONFIG`, only when set and no Agentbox config exists -> `/kilo-host/KILO_CONFIG`, required file, read-only
- Host repo `.agentbox/kilo/kilo.jsonc`, when it exists -> `/agentbox/config/kilo.jsonc`, required file, read-only

Do not create missing global config directories. Missing global config is normal and should not become a host mutation side effect.

### 4. Set Kilo environment in layers
Keep `KiloDriver.env` for stable Kilo runtime environment:

- `HOME=/kilo-home`
- `XDG_CONFIG_HOME=/kilo-home/.config`
- `XDG_DATA_HOME=/kilo-home/.local/share`
- `XDG_STATE_HOME=/kilo-home/.local/state`
- `XDG_CACHE_HOME=/kilo-home/.cache`
- `KILO_CONFIG_CONTENT=<merged safety override JSON>`

Move config-specific env to `KiloDriver.config_env`:

- If host `KILO_CONFIG_DIR` is set, set `KILO_CONFIG_DIR=/kilo-host/KILO_CONFIG_DIR`.
- If `.agentbox/kilo/kilo.jsonc` exists, set `KILO_CONFIG=/agentbox/config/kilo.jsonc`.
- Else if host `KILO_CONFIG` is set, set `KILO_CONFIG=/kilo-host/KILO_CONFIG`.
- Else omit `KILO_CONFIG`.

In `podman.render_run_command`, combine env dictionaries in this order so config env can override host passthrough:

1. `driver.env(...)`
2. `driver.config_env(...)`
3. devcontainer env, preserving current devcontainer behavior

### 5. Render and create mounts centrally
Update `podman.render_run_command` to render:

1. workspace/run clone mount
2. `driver.state_mounts(...)`
3. `driver.config_mounts(..., config.repo_root)`
4. devcontainer mounts/run args

Update `podman.ensure_state_mounts` to validate and create only mutable state mounts. It should also validate config mounts but never create them. A simple approach is:

- Validate/create `state_mounts` as today.
- Validate `config_mounts` with `validated_state_mounts(..., check_sources=True)` but do not create directories.
- Required host `KILO_CONFIG`/`KILO_CONFIG_DIR` should still fail clearly when they are the active config source and missing.

All config mounts must render with `:ro` plus the existing SELinux suffix when enabled, e.g. `:ro,z`.

### 6. Runtime warnings and diagnostics
Add `KiloDriver.runtime_warnings`:

- If host `KILO_CONFIG` is set and host repo `.agentbox/kilo/kilo.jsonc` exists, return a warning like:
  `agentbox: warning: host KILO_CONFIG=/path/to/config is ignored inside Kilo containers because .agentbox/kilo/kilo.jsonc is mounted as KILO_CONFIG`

Print these warnings to stderr in `cli.run_container` before rendering/running the Podman command. This should also appear during dry-run because dry-run renders the container command developers inspect.

Update `KiloDriver.diagnostics`:

- Report mutable Kilo data/state/cache paths as warning if none exist, preserving current first-run behavior.
- Report missing required host `KILO_CONFIG` only when no Agentbox config exists and host `KILO_CONFIG` would be used.
- Report host `KILO_CONFIG` conflict as a warning, not an error, when Agentbox config exists.
- Report missing host `KILO_CONFIG_DIR` as an error when the env var is set.
- Include the Agentbox config path in diagnostics as ok when present, warning when absent with message `run agentbox init to create it`.

### 7. Preserve project/repo configs automatically
No extra mount is required for project/repo Kilo configs. The run clone is already mounted as the workspace, and Kilo will discover workspace-local config such as:

- `kilo.json` / `kilo.jsonc`
- `.kilo/kilo.json` / `.kilo/kilo.jsonc`
- `.kilo/command/*.md`, `.kilo/agent/*.md`, skills, etc.
- legacy `.kilocode/` equivalents

This means project config remains editable by the agent in the isolated clone and naturally has higher precedence than the Agentbox-specific `KILO_CONFIG` layer.

## Implementation Steps
1. Add `InitFileSpec` and the new driver hook methods in `drivers/base.py`; update Codex and Kilo classes to satisfy the protocol.
2. Implement `KiloDriver.init_files`, `config_mounts`, `config_env`, and `runtime_warnings`.
3. Refactor Kilo mutable state mounts to exclude config directories and make config mounts read-only.
4. Update `cmd_init` to create driver init files without overwriting.
5. Update `podman.render_run_command` and `podman.ensure_state_mounts` to include/validate config mounts and merge config env.
6. Update Kilo diagnostics for Agentbox config, host `KILO_CONFIG` conflict, and read-only config path handling.
7. Update tests for init generation, readonly mount rendering, conflict behavior, diagnostics, and env precedence.
8. Update README Kilo config section to describe global read-only config mounts, `.agentbox/kilo/kilo.jsonc`, host `KILO_CONFIG` conflict behavior, and project config discovery from the isolated clone.

## Test Plan
Add or update tests to cover:

- `agentbox init` creates `.agentbox/kilo/kilo.jsonc` and does not overwrite existing contents.
- Kilo Agentbox config is mounted from `config.repo_root`, not from `run_repo`.
- Agentbox config mount renders read-only at `/agentbox/config/kilo.jsonc` and sets `KILO_CONFIG=/agentbox/config/kilo.jsonc`.
- Host global config directory mounts are read-only and optional.
- Kilo data/state/cache mounts remain read-write and creatable.
- Host `KILO_CONFIG` is mounted/read when no Agentbox config exists.
- Host `KILO_CONFIG` is ignored when Agentbox config exists, and warning is printed to stderr during dry-run and real run rendering.
- Missing host `KILO_CONFIG` errors only when it would be used.
- Missing host `KILO_CONFIG_DIR` errors when set.
- `KILO_CONFIG_CONTENT` merge behavior remains unchanged for invalid JSON and enforced safety keys.
- Existing Codex tests still pass unchanged.
- Run the full suite: `uv run python -m unittest discover -s tests -v`.

## Risks and Notes
- Kilo has only one `KILO_CONFIG` file slot. The chosen behavior is Agentbox config priority with an explicit warning for host `KILO_CONFIG` conflicts.
- Read-only global config mounts may prevent interactive Kilo login/setup inside the container from writing config files. That is intentional for this proposal; mutable Kilo runtime data/state/cache remain mounted separately.
- Existing `KILO_CONFIG_CONTENT` is higher precedence than project config. Keep this documented because it is required for Agentbox's full-permission isolated-clone execution model.
- If future Kilo versions change config search paths, update `KiloDriver.config_mounts` and diagnostics in one place.
