#!/usr/bin/env python3
"""Where things live, and how to borrow the repo's existing helpers.

No hardcoded /Users path (security-policies §no-hardcoded-paths): the root is
found by walking up from this file, overridable with GTM_STARTER_ROOT.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve()


def repo_root() -> Path:
    env = os.environ.get("GTM_STARTER_ROOT")
    if env:
        return Path(env).expanduser()
    for parent in _HERE.parents:
        if (parent / "START-HERE.md").is_file():
            return parent
    return _HERE.parents[3]


ROOT = repo_root()
SKILL_DIR = _HERE.parents[1]                      # …/calibrated-judge
CRAWFORD_SCRIPTS = SKILL_DIR.parent / "crawford" / "scripts"   # sibling skill
OUT_DIR = ROOT / "outputs" / "jev-bakeoff"
FROZEN_DIR = OUT_DIR / "frozen"
REPORT_DIR = OUT_DIR / "reports"
RUN_DIR = OUT_DIR / "runs"


def add_path(p: Path) -> None:
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)


def try_import(module: str, path: Path):
    """Import `module` with `path` on sys.path; None when it cannot load.

    Every borrowed helper goes through here: the repo's pipelines carry heavy
    third-party imports (openai, rapidfuzz, tldextract) that the system python
    does not always have, and a missing one must degrade the arm, never crash
    the harness.
    """
    add_path(path)
    try:
        import importlib

        return importlib.import_module(module)
    except Exception:                       # ImportError, and anything a module
        return None                         # does at import time


def load_dotenv_quietly() -> None:
    """Populate os.environ from the repo .env without printing anything.

    python-dotenv when available, otherwise a minimal parser. Values are never
    logged, returned, or echoed — callers only ever ask `os.environ.get(...)`.
    """
    env_path = ROOT / ".env"
    try:
        from dotenv import load_dotenv           # type: ignore

        load_dotenv(env_path, override=False)
        return
    except Exception:
        pass
    if not env_path.exists():
        return
    try:
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v
    except OSError:
        pass
