# Code Review: `8f34bf` - Pin base images and harness dependencies

**Scope reviewed:** the diff of `8f34bf` against its parent, evaluated in the
context of the tree at that commit. The full test suite passed at that commit:
110 tests in 8.5 seconds.

## Summary

The commit pins base images by digest when a Containerfile is generated, pins
uv by its versioned installer URL, and pins the Codex and Kilo CLI versions.
The core design is sound, and the README accurately describes the intended
behavior. The Codex installer does honor `CODEX_RELEASE`, and the versioned uv
installer URL is valid.

One entry-point regression and several smaller concerns follow.

## Findings

### 1. Medium - `agentbox init` now fails when podman is not installed

`src/agentbox/podman.py:240-241` invokes podman unconditionally while
generating a Containerfile:

```python
run(["podman", "pull", base_image], check=False)
result = run(["podman", "image", "inspect", base_image], check=False)
```

`check=False` handles nonzero exit codes, not a missing executable. If podman
is absent, `subprocess.run` raises `FileNotFoundError`, which propagates from
`ensure_harness_containerfile` through `cmd_init`. This was reproduced with
podman removed from `PATH`: `agentbox.toml` and `.agentbox/.gitignore` were
created, but no Containerfile was written.

Before this commit, `agentbox init` did not require podman. This also bypasses
the intended graceful fallback to an unpinned Containerfile.

Catch `OSError` around the pull and inspect operations and return `None`, so
the existing unpinned-Containerfile warning path handles a missing podman.
Add a test with `run` raising `FileNotFoundError`.

### 2. Low - Digest selection ignores the repository of `RepoDigests` entries

`src/agentbox/podman.py:258-261` picks the first non-instance digest:

```python
candidates = [digest for digest in repo_digests if digest != instance_digest] or repo_digests
for candidate in candidates:
    if _BASE_IMAGE_DIGEST.match(candidate):
        return f"{base_image}@{candidate}"
```

`RepoDigests` can contain entries for other tags or registries referring to the
same locally stored image. Since ordering is not guaranteed, resolving
`ubuntu:24.04` could select a manifest-list digest acquired through another tag
or mirror registry, then attribute it to `ubuntu:24.04` in the generated
`FROM` line.

Prefer a `RepoDigests` entry whose repository matches the requested base-image
repository, with the current heuristic only as a fallback. Add a test with two
distinct list digests from different repositories.

### 3. Low - Failed pulls can silently produce stale pins

When `podman pull` fails but a locally cached image can be inspected, the code
pins the cached digest. The failed pull's stderr is captured and discarded, so
the user receives no indication that the resulting pin may be stale. The
best-effort behavior is reasonable; emit a short warning when pull fails and a
cached image is used.

### 4. Low - Dry-run no longer shows a usable managed-image tag

`src/agentbox/podman.py:18,137-139` now renders
`agentbox-codex:<containerfile-digest>` for an unmaterialized dry-run.

The placeholder is safe: it is shell-quoted in printed commands and is never
persisted in dry-run metadata. However, unlike the previous deterministic
Containerfile digest, it cannot be copied from dry-run output to pre-build the
image. This is an understandable trade-off because the final content depends
on digest resolution, but it should be documented near dry-run behavior. The
`else` after the returning branch can also be removed.

### 5. Low - `init` pulls the same default base image twice

`cmd_init` materializes both default drivers, which each resolve
`ubuntu:24.04`. This performs two `podman pull` calls in a row. Cache digest
resolution by base-image reference for the duration of the process to avoid a
second registry request.

### 6. Informational - Installer scripts remain unverified `curl | sh` inputs

The Codex installer remains an unpinned `curl | sh` download. The release is
pinned and the current installer verifies downloaded payload checksums, which
is a significant improvement, but the installer script itself is not verified.
The uv URL is version-pinned but likewise has no checksum verification.
Consider downloading and verifying published installer checksums before
execution.

### 7. Informational - Dogfood Containerfiles differ from the templates

Both packaged templates now install `python-is-python3`, but neither
`.agentbox/codex/Containerfile` nor `.agentbox/kilo/Containerfile` does. Those
files are documented as mutable local definitions, so divergence is allowed;
however, if this package is required for the harnesses, the repository's own
managed images lack it. The commit also does not explain why Kilo now needs uv.

## What Was Done Well

- The pinned reference is applied only to the materialized Containerfile via
  `dataclasses.replace`; the user-facing config remains readable and deleting
  the Containerfile re-resolves the pin.
- The resolver validates inspect output, rejects malformed and ambiguous data,
  avoids podman calls for already-pinned references, and correctly prefers a
  manifest-list digest over a host-specific instance digest in normal podman
  output.
- The Codex release pin is real: the current installer reads `CODEX_RELEASE`.
- The README describes the pinning model and digest-refresh behavior accurately.
- The new resolver tests cover pre-pinned references, malformed data, failed
  pulls with a local fallback, ambiguous inspect results, and materialization.

## Verdict

Address Finding 1 before merging. Findings 2 and 3 are recommended follow-up
hardening work.
