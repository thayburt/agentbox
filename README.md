# agent-containers

Run AI coding agents inside rootless Podman containers. The first supported agent is
Codex.

The default flow is intentionally interactive:

1. `agentc` creates an ephemeral local clone under `.agentc/runs/`.
2. The original checkout is not mounted into the container.
3. Codex runs interactively with full permissions inside the container.
4. When Codex exits, you decide whether to pull committed work back.

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

New run clones inherit only Git commit identity from the host checkout. `agentc`
resolves `user.name` and `user.email` from CLI flags, then `[git]` config, then
`git config --get` in the original repo, and writes resolved values into the run
clone's local `.git/config`:

```toml
[git]
user_name = "Your Name"
user_email = "you@example.com"
sign_imports = false
```

```bash
uv run agentc codex run --git-user-name "Your Name" --git-user-email you@example.com
uv run agentc codex shell --git-user-name "Your Name" --git-user-email you@example.com
```

Set `sign_imports = true` or pass `--sign-imports` to rewrite imported run
commits on the host with `git cherry-pick -S`. This keeps signing keys out of
the sandbox. `--no-sign-imports` disables that behavior for a command. Signed
imports create/update the `agentc/<run-id>` branch; `ff-only` remains an
exact-history operation and is not available while signed imports are enabled.

When the run finishes, `agentc` shows a compact `git log --oneline` preview of
commits in the run that are not on the host branch, then prompts:

```text
[b] Import to branch agentc/<run-id>
[f] Fast-forward <branch> to <commit>
[l] Leave in run for later review (default)
```

In non-interactive use, choose explicitly:

```bash
uv run agentc codex run --pull branch
uv run agentc codex run --pull ff-only
uv run agentc codex run --pull later
```

Codex launches as:

```bash
codex --sandbox danger-full-access --ask-for-approval never
```

That is safe only because it runs against the isolated clone, not the original
checkout.

## Bring Work Back

The end-of-session prompt can import committed work into `agentc/<run-id>`,
fast-forward the current branch when it is safe, or leave the run for later
review. Fast-forward requires a clean host worktree, the same branch the run was
created from, and no host-only commits outside the run history.

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
