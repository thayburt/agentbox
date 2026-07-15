"""Shared helpers for tests that operate on real git repositories."""

from pathlib import Path
import subprocess


def git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, stdout=subprocess.PIPE)


def git_output(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )
    return result.stdout.strip()


def configure_user(cwd: Path, name: str = "Test User", email: str = "test@example.com") -> None:
    git(cwd, "config", "user.email", email)
    git(cwd, "config", "user.name", name)


def init_repo(root: Path, name: str = "Test User", email: str = "test@example.com") -> Path:
    root.mkdir()
    git(root, "init")
    configure_user(root, name, email)
    (root / "file.txt").write_text("base\n")
    git(root, "add", "file.txt")
    git(root, "commit", "-m", "base")
    return root


def configure_fake_signing(cwd: Path, tmp: Path) -> None:
    fake_gpg = tmp / "fake-gpg"
    fake_gpg.write_text(
        """#!/bin/sh
cat >/dev/null
echo '[GNUPG:] SIG_CREATED D 1 1 00 0 0 0 0 FAKE' >&2
cat <<'SIG'
-----BEGIN PGP SIGNATURE-----

fake
-----END PGP SIGNATURE-----
SIG
exit 0
"""
    )
    fake_gpg.chmod(0o755)
    git(cwd, "config", "gpg.program", str(fake_gpg))
    git(cwd, "config", "user.signingkey", "fake")
