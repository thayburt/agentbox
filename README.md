# agentbox

Run AI coding agents inside rootless Podman containers. Supported harnesses are
Codex and Kilo Code.

The default flow is intentionally interactive:

1. `agentbox` creates an ephemeral local clone under `.agentbox/runs/`.
2. The original checkout is not mounted into the container.
3. The selected harness runs interactively with full permissions inside the container.
4. When the harness exits, you decide whether to pull committed work back.

## Setup

```bash
uv run agentbox init
uv run agentbox doctor
uv run agentbox codex build
uv run agentbox kilo build
```

`agentbox init` creates these local files independently and never overwrites one
when it already exists:

- `agentbox.toml`
- `.agentbox/codex/Containerfile`
- `.agentbox/kilo/Containerfile`
- `.agentbox/kilo/kilo.jsonc`

Each Containerfile is the mutable local definition of its managed harness image.
Edit one when you need a custom base image or additional tools for that harness.
When a Containerfile is first generated, agentbox pins the configured
`base_image` by digest: the tag is pulled and the FROM line is recorded as
`ubuntu:24.04@sha256:<digest>` (the manifest-list digest, so the pin stays
usable across architectures). A `base_image` that already carries a digest is
used verbatim. If no registry digest can be resolved (for example offline, or
a locally built base image), the Containerfile is written unpinned with a
warning.

Run tests with:

```bash
uv run python -m unittest discover -s tests -v
uv run ruff check
```

`agentbox` expects rootless Podman. Each harness driver declares its own state
mounts and environment, while agentbox validates and renders Podman sandboxing,
workspace mounts, image management, and run lifecycle behavior centrally. Codex
uses `CODEX_HOME=/codex-home`, preferring the host `CODEX_HOME` environment
variable when set, otherwise `~/.codex`.

Containers run with `--cap-drop=ALL` and
`--security-opt=no-new-privileges`, and the managed images do not include
`sudo`. Network access remains unrestricted because harnesses require API and
package access. The isolation boundary is the unmounted host checkout together
with rootless `--userns=keep-id`, not network isolation.

## Run Harnesses

```bash
uv run agentbox codex run
uv run agentbox kilo run
```

By default, `agentbox` uses the current harness Containerfile contents to select
a managed image tag:

```text
agentbox-codex:<full-containerfile-sha256>
agentbox-kilo:<full-containerfile-sha256>
```

`agentbox <harness> build` skips the Podman build when that exact image already
exists locally. `agentbox <harness> run` and `agentbox <harness> shell`
automatically build the current managed image when it is missing.

Each run also snapshots the Containerfile used to build its image into
`.agentbox/runs/<run-id>/Containerfile`. This keeps runs reproducible: even
after you edit `.agentbox/<harness>/Containerfile` (which changes the managed
image tag), `agentbox runs enter` and `agentbox <harness> shell --run` can
rebuild the run's original image from its snapshot when it is no longer present
locally.

Editing the Containerfile produces a new content-addressed tag, so old images
accumulate over time. Manage them with:

```bash
uv run agentbox codex images          # list managed images (current/referenced)
uv run agentbox codex prune           # remove images no run still references
uv run agentbox codex prune --dry-run # show what prune would remove
uv run agentbox kilo images
uv run agentbox kilo prune
```

`prune` keeps the current managed image and any image referenced by a saved run.
Managed images pin the base image by digest (resolved when the Containerfile is
generated) and harness tools by version. The pin does not move when the
registry tag is updated; to refresh a dependency, intentionally edit its
version or digest — or delete the Containerfile to have it regenerated with a
fresh digest — and rebuild with:

```bash
uv run agentbox codex build --rebuild
uv run agentbox kilo build --rebuild
```

The build's `--pull=newer` behavior cannot refresh a digest-pinned base image;
the pinned digest must be updated first.

Pass `--image IMAGE` to bypass the managed Containerfile image entirely:

```bash
uv run agentbox codex run --image ubuntu:24.04
uv run agentbox codex shell --image localhost/custom-codex:dev
uv run agentbox kilo run --image localhost/custom-kilo:dev
```

The override is passed directly to Podman and recorded in run metadata as-is;
`agentbox` does not check, pull, or build it first.

If a dry-run would need to generate a Containerfile, digest resolution is
deferred and the printed managed-image tag contains `<containerfile-digest>`.
That placeholder describes the eventual content-addressed tag but cannot be
copied from dry-run output to pre-build the image.

If the checkout is dirty, the CLI prompts before copying dirty file contents
into the isolated clone. In non-interactive use, choose explicitly:

```bash
uv run agentbox codex run --dirty include
uv run agentbox codex run --dirty ignore
uv run agentbox kilo run --dirty ignore
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
uv run agentbox kilo run --git-user-name "Your Name" --git-user-email you@example.com
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
uv run agentbox kilo run --pull later
```

If the run finishes with uncommitted changes, interactive sessions first offer
to stage and commit all changes, select changes with Git's interactive staging
UI, commit the currently staged changes, or leave everything in the saved run.
Staging and committing run inside the sandbox container; only committed Git
objects are fetched back by the host.

Set the default action in `agentbox.toml`, or override it for one invocation:

```toml
[git]
uncommitted = "prompt" # prompt, commit-all, commit-staged, later, or abort
# commit_message_default = "Apply agent-generated changes"
```

```bash
uv run agentbox kilo run --uncommitted commit-all --commit-message "Apply Kilo changes"
uv run agentbox codex run --uncommitted later
```

For commit messages, `--commit-message` takes precedence. Otherwise interactive
sessions ask for a message and show `git.commit_message_default` as the fallback;
pressing Enter accepts it. If no configured fallback exists, Agentbox uses
`Apply Agentbox run <run-id> changes`. Non-interactive runs use the configured or
generated fallback directly. A non-interactive `prompt` action safely behaves as
`later`.

Codex launches as:

```bash
codex --cd <workspace> --sandbox danger-full-access --ask-for-approval never
```

Kilo launches as:

```bash
kilo [<prompt>]
```

Both managed images include uv so harness sessions can run Python project setup
and test commands; Kilo itself does not require uv.

These full-permission modes are safe only because they run against the isolated
clone, not the original checkout.

Kilo runs as the image's `ubuntu` user. Host Kilo XDG data is mounted read-write
at `/home/ubuntu/.local/share/kilo`, using the host `XDG_DATA_HOME` default or
override. This keeps authentication shared: Kilo stores `auth.json` in XDG data.
Agentbox creates this mutable host-backed directory when needed; Podman assigns
it to the user running Kilo; `doctor` reports its first-use absence as a warning.
Host XDG cache and state are not mounted.

Each saved run mounts `<run_store>/<run-id>/home` at `/home/ubuntu`. Everything
Kilo writes under its home persists when re-entering the same run, is isolated
from the host and other runs, and is removed by `agentbox runs prune`. Existing
host home contents remain untouched and are never copied into runs. New runs
initialize their home from `/home/ubuntu` in the selected image, then snapshot
an existing host `XDG_STATE_HOME/kilo/model.json` into their home state tree.
This optional model seed is non-fatal if missing or unreadable, does not
propagate later host changes, and is not applied retroactively to existing saved
runs.

Kilo global configuration is mounted read-only: `XDG_CONFIG_HOME/kilo` (or
`~/.config/kilo`), `~/.kilo`, `~/.kilocode`, and `KILO_CONFIG_DIR` when set.
No missing config directory is created. `agentbox init` also creates the
repository-owned `.agentbox/kilo/kilo.jsonc`, mounted read-only at
`/agentbox/config/kilo.jsonc` and set as `KILO_CONFIG`. This file is read from
the host repository root rather than the isolated clone, so a run cannot change
the config that started it.

When `.agentbox/kilo/kilo.jsonc` exists, it wins over a host `KILO_CONFIG` and
agentbox prints a warning identifying the ignored host path. If it does not
exist, a host `KILO_CONFIG` is mounted read-only and used normally. Project Kilo
configuration, commands, agents, and skills remain available from the isolated
run clone, including `kilo.json`, `.kilo/`, and legacy `.kilocode/` paths.

## Bring Work Back

The end-of-session prompt can import committed work into `agentbox/<run-id>`,
fast-forward the current branch when it is safe, or leave the run for later
review. Fast-forward requires a clean host worktree, the same branch the run was
created from, and no host-only commits outside the run history.

Pull handling intentionally runs even after a non-zero harness exit so
non-interactive pull modes can import work from a failed run.

List saved runs:

```bash
uv run agentbox runs list
```

Open a shell in a run:

```bash
uv run agentbox runs enter <run-id>
```

`runs enter` and `<harness> shell --run` accept `--image` to override the saved
image for that session without rewriting run metadata.

Import committed work from a run as a new local branch:

```bash
uv run agentbox runs import <run-id>
git switch agentbox/<run-id>
```

Uncommitted changes are never auto-committed. Enter the run and handle them
manually.
