from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import json
import shlex
from typing import Any


UNSUPPORTED_FIELDS = {
    "dockerComposeFile",
    "dockerComposeFile",
    "composeFile",
    "service",
    "features",
}


@dataclass(frozen=True)
class Devcontainer:
    path: Path
    image: str | None = None
    build_context: Path | None = None
    build_dockerfile: Path | None = None
    workspace_folder: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    mounts: list[str] = field(default_factory=list)
    run_args: list[str] = field(default_factory=list)
    post_create: list[str] = field(default_factory=list)
    post_start: list[str] = field(default_factory=list)


def load_devcontainer(path: Path | None) -> Devcontainer | None:
    if path is None or not path.exists():
        return None

    raw = json.loads(strip_jsonc(path.read_text()))
    unsupported = sorted(field for field in UNSUPPORTED_FIELDS if field in raw)
    if unsupported:
        joined = ", ".join(unsupported)
        raise ValueError(f"Unsupported devcontainer field(s): {joined}")

    base_dir = path.parent
    build = raw.get("build")
    build_context = None
    build_dockerfile = None
    if isinstance(build, dict):
        context_raw = build.get("context", ".")
        dockerfile_raw = build.get("dockerfile", "Dockerfile")
        build_context = _resolve(base_dir, context_raw)
        build_dockerfile = _resolve(base_dir, dockerfile_raw)

    env = {}
    for key in ("containerEnv", "remoteEnv"):
        values = raw.get(key, {})
        if isinstance(values, dict):
            env.update({str(k): str(v) for k, v in values.items()})

    return Devcontainer(
        path=path,
        image=raw.get("image"),
        build_context=build_context,
        build_dockerfile=build_dockerfile,
        workspace_folder=raw.get("workspaceFolder"),
        env=env,
        mounts=[str(item) for item in _as_list(raw.get("mounts"))],
        run_args=[str(item) for item in _as_list(raw.get("runArgs"))],
        post_create=_command_list(raw.get("postCreateCommand")),
        post_start=_command_list(raw.get("postStartCommand")),
    )


def strip_jsonc(text: str) -> str:
    out: list[str] = []
    i = 0
    in_string = False
    quote = ""
    while i < len(text):
        ch = text[i]
        nxt = text[i + 1] if i + 1 < len(text) else ""
        if in_string:
            out.append(ch)
            if ch == "\\" and nxt:
                out.append(nxt)
                i += 2
                continue
            if ch == quote:
                in_string = False
            i += 1
            continue
        if ch in {"'", '"'}:
            in_string = True
            quote = ch
            out.append(ch)
            i += 1
            continue
        if ch == "/" and nxt == "/":
            i += 2
            while i < len(text) and text[i] not in "\r\n":
                i += 1
            continue
        if ch == "/" and nxt == "*":
            i += 2
            while i + 1 < len(text) and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def shell_join(commands: list[str]) -> str:
    return " && ".join(f"({cmd})" for cmd in commands if cmd.strip())


def _resolve(base_dir: Path, raw: str) -> Path:
    path = Path(str(raw)).expanduser()
    if path.is_absolute():
        return path
    return base_dir / path


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _command_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [shlex.join([str(part) for part in value])]
    if isinstance(value, dict):
        return [str(value[key]) for key in sorted(value)]
    raise ValueError(f"Unsupported command value: {value!r}")
