"""Read and write `.env` without destroying the comments in it.

`dotenv.set_key` would rewrite the whole file once per key and requote every
value, so the hand-written comments and formatting are preserved here instead.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv.main import parse_stream

# Internal spaces are safe unquoted (dotenv reads to end of line). Quotes are
# only needed for leading/trailing whitespace, comment markers, or quote chars.
_NEEDS_QUOTING = set("\n\r\"'#")


def read_env(path: str | os.PathLike[str]) -> dict[str, str]:
    """Parse a .env file into a plain dict (missing file -> empty dict)."""
    p = Path(path)
    if not p.exists():
        return {}
    with p.open("r", encoding="utf-8") as fh:
        return {m.key: (m.value or "") for m in parse_stream(fh) if m.key}


def format_value(value: str) -> str:
    if value == "":
        return ""  # bare `KEY=` -- quoting it would churn untouched lines
    if value != value.strip() or any(ch in _NEEDS_QUOTING for ch in value):
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return value


def write_env(path: str | os.PathLike[str], updates: dict[str, str]) -> None:
    """Rewrite `path`, replacing only the keys in `updates`.

    Comments, blank lines, ordering and unmanaged keys survive untouched. Keys
    not already present are appended. The write is atomic: a sibling temp file
    is swapped in with os.replace, so a crash mid-save cannot truncate .env.
    """
    p = Path(path)
    lines: list[str] = []
    seen: set[str] = set()

    if p.exists():
        with p.open("r", encoding="utf-8") as fh:
            original = fh.read()
        with p.open("r", encoding="utf-8") as fh:
            bindings = list(parse_stream(fh))

        # parse_stream yields every line, including comments and blanks, with
        # `original.string` holding the untouched source text.
        for binding in bindings:
            raw = binding.original.string
            if binding.key and binding.key in updates:
                lines.append(f"{binding.key}={format_value(updates[binding.key])}\n")
                seen.add(binding.key)
            else:
                lines.append(raw if raw.endswith("\n") else raw + "\n")

        if not original.endswith("\n") and lines:
            lines[-1] = lines[-1].rstrip("\n")

    missing = [k for k in updates if k not in seen]
    if missing:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        for key in missing:
            lines.append(f"{key}={format_value(updates[key])}\n")

    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text("".join(lines), encoding="utf-8")
    os.replace(tmp, p)
