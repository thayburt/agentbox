# agentbox

Run AI coding agents inside rootless Podman containers. The first supported agent is
Codex.

The default flow is intentionally interactive:

1. `agentbox` creates an ephemeral local clone under `.agentbox/runs/`.
2. The original checkout is not mounted into the container.
3. Codex runs interactively with full permissions inside the container.
4. When Codex exits, you decide whether to pull committed work back.

## Setup

```bash
uv run agentbox init
uv run agentbox doctor
uv run agentbox codex build
```

`agentbox init` creates two local files independently and never overwrites either
one when it already exists:

- `agentbox.toml`
- `.agentbox/codex.Containerfile`

The Containerfile is the mutable local definition of the managed Codex harness
image. Edit it when you need a custom base image or additional tools.

Run tests with:

```bash
uv run python -m unittest discover -s tests -v
```

`agentbox` expects rootless Podman and mounts your host Codex state into the
container as `CODEX_HOME=/codex-home`. By default it uses the host `CODEX_HOME`
environment variable when set, otherwise `~/.codex`.

## Run Codex

```bash
uv run agentbox codex run
```

By default, `agentbox` uses the current `.agentbox/codex.Containerfile` contents
to select a managed image tag:

```text
agentbox-codex:<full-containerfile-sha256>
```

`agentbox codex build` skips the Podman build when that exact image already
exists locally. `agentbox codex run` and `agentbox codex shell` automatically
build the current managed image when it is missing.

Pass `--image IMAGE` to bypass the managed Containerfile image entirely:

```bash
uv run agentbox codex run --image ubuntu:24.04
uv run agentbox codex shell --image localhost/custom-codex:dev
```

The override is passed directly to Podman and recorded in run metadata as-is;
`agentbox` does not check, pull, or build it first.

If the checkout is dirty, the CLI prompts before copying dirty file contents
into the isolated clone. In non-interactive use, choose explicitly:

```bash
uv run agentbox codex run --dirty include
uv run agentbox codex run --dirty ignore
```

New run clones inherit only Git commit identity from the host checkout. `agentbox`
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
uv run agentbox codex run --git-user-name "Your Name" --git-user-email you@example.com
uv run agentbox codex shell --git-user-name "Your Name" --git-user-email you@example.com
```

Set `sign_imports = true` or pass `--sign-imports` to rewrite imported run
commits on the host with `git cherry-pick -S`. This keeps signing keys out of
the sandbox. `--no-sign-imports` disables that behavior for a command. Signed
imports create/update the `agentbox/<run-id>` branch; `ff-only` remains an
exact-history operation and is not available while signed imports are enabled.

When the run finishes, `agentbox` shows a compact `git log --oneline` preview of
commits in the run that are not on the host branch, then prompts:

```text
[b] Import to branch agentbox/<run-id>
[f] Fast-forward <branch> to <commit>
[l] Leave in run for later review (default)
```

In non-interactive use, choose explicitly:

```bash
uv run agentbox codex run --pull branch
uv run agentbox codex run --pull ff-only
uv run agentbox codex run --pull later
```

Codex launches as:

```bash
codex --sandbox danger-full-access --ask-for-approval never
```

That is safe only because it runs against the isolated clone, not the original
checkout.

## Bring Work Back

The end-of-session prompt can import committed work into `agentbox/<run-id>`,
fast-forward the current branch when it is safe, or leave the run for later
review. Fast-forward requires a clean host worktree, the same branch the run was
created from, and no host-only commits outside the run history.

List saved runs:

```bash
uv run agentbox runs list
```

Open a shell in a run:

```bash
uv run agentbox runs enter <run-id>
```

Import committed work from a run as a new local branch:

```bash
uv run agentbox runs import <run-id>
git switch agentbox/<run-id>
```

Uncommitted changes are never auto-committed. Enter the run and handle them
manually.

## Devcontainer Subset

If `.devcontainer/devcontainer.json` exists, `agentbox` supports this subset:

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

Devcontainer `image` and `build` fields do not change the Codex harness base
image. Workspace, environment, mounts, run arguments, and post commands remain
supported. To change the harness base, edit `.agentbox/codex.Containerfile` or
pass `--image IMAGE` for a specific run or shell.
