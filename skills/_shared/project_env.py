"""Load the project's `.env` into os.environ. Shared by the auditor, builder and
Crawford scripts so every one of them reads `<project root>/.env` the same way.

Rules:
  * The `.env` used: $GTM_STARTER_ROOT/.env if that is set; else the nearest
    `.env` at or above the current directory (commands run from the project
    root); else the one in the starter copy this file lives in.
  * A variable already set in the real environment always wins.
  * Values are never printed or returned in logs; callers read os.environ.
  * `KEY=value`, `export KEY=value`, quotes and `#` comment lines are handled.
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT_MARKER = "START-HERE.md"


def project_root(start: Path | None = None) -> Path | None:
    """Where `.env` is read from: $GTM_STARTER_ROOT; else the nearest folder at
    or above the current directory that holds a `.env` (you run commands from
    the project root); else the starter copy this file lives in (the nearest
    folder above it holding START-HERE.md)."""
    env = os.environ.get("GTM_STARTER_ROOT")
    if env:
        return Path(env).expanduser()
    here = (start or Path.cwd()).resolve()
    for parent in (here, *here.parents):
        if (parent / ".env").is_file():
            return parent
    for parent in Path(__file__).resolve().parents:
        if (parent / ROOT_MARKER).is_file():
            return parent
    return None


def parse_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return out
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        if key:
            out[key] = value
    return out


def load(start: Path | None = None) -> Path | None:
    """Fill os.environ from <project root>/.env (real environment wins).
    Returns the .env path used, or None when there is none. GTM_NO_DOTENV=1
    turns it off (the offline test suites set it, so your real keys are never
    loaded into a test run)."""
    if os.environ.get("GTM_NO_DOTENV") == "1":
        return None
    root = project_root(start)
    if root is None:
        return None
    path = root / ".env"
    if not path.is_file():
        return None
    for k, v in parse_env_file(path).items():
        if k not in os.environ:
            os.environ[k] = v
    return path
