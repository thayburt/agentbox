# agent-containers

Run AI coding agents inside rootless Podman containers. The first supported agent is
Codex.

The default flow is intentionally interactive:

1. `agentc` creates an ephemeral local clone under `.agentc/runs/`.
2. The original checkout is not mounted into the container.
3. Codex runs interactively with full permissions inside the container.
4. You decide whether to import finished commits back as a new local branch.

## Setup

```bash
uv run agentc init
uv run agentc doctor
uv run agentc codex build
```

Run tests with:

```bash
uv run python -m unittest discover -s tests -v
```

`agentc` expects rootless Podman and mounts your host Codex state into the
container as `CODEX_HOME=/codex-home`. By default it uses the host `CODEX_HOME`
environment variable when set, otherwise `~/.codex`.

## Run Codex

```bash
uv run agentc codex run
```

If the checkout is dirty, the CLI prompts before copying dirty file contents
into the isolated clone. In non-interactive use, choose explicitly:

```bash
uv run agentc codex run --dirty include
uv run agentc codex run --dirty ignore
```

Codex launches as:

```bash
codex --sandbox danger-full-access --ask-for-approval never
```

That is safe only because it runs against the isolated clone, not the original
checkout.

## Bring Work Back

List saved runs:

```bash
uv run agentc runs list
```

Open a shell in a run:

```bash
uv run agentc runs enter <run-id>
```

Import committed work from a run as a new local branch:

```bash
uv run agentc runs import <run-id>
git switch agentc/<run-id>
```

Uncommitted changes are never auto-committed. Enter the run and handle them
manually.

## Devcontainer Subset

If `.devcontainer/devcontainer.json` exists, `agentc` supports this subset:

- `image`
- `build.context`
- `build.dockerfile`
- `workspaceFolder`
- `containerEnv`
- `remoteEnv`
- `mounts`
- `runArgs`
- `postCreateCommand`
- `postStartCommand`

High-impact unsupported fields such as `dockerComposeFile`, `service`, and
`features` fail fast.
