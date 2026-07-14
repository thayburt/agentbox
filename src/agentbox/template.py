from __future__ import annotations

from importlib.resources import files
import re
from typing import Mapping


_TOKEN = re.compile(r"@@([A-Z][A-Z0-9_]*)@@")


def read_template(resource_name: str) -> str:
    return files("agentbox").joinpath("templates", *resource_name.split("/")).read_text(
        encoding="utf-8"
    )


def render_template(resource_name: str, replacements: Mapping[str, str]) -> str:
    template = read_template(resource_name)
    expected = {match.group(1) for match in _TOKEN.finditer(template)}
    actual = set(replacements)
    missing = expected - actual
    unexpected = actual - expected
    if missing or unexpected:
        details = []
        if missing:
            details.append(f"missing replacements: {', '.join(sorted(missing))}")
        if unexpected:
            details.append(f"unexpected replacements: {', '.join(sorted(unexpected))}")
        raise ValueError(f"template {resource_name}: {'; '.join(details)}")
    return _TOKEN.sub(lambda match: replacements[match.group(1)], template)
