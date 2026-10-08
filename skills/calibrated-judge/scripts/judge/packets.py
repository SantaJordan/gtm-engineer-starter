#!/usr/bin/env python3
"""`judge.packets` — a shim onto Crawford's packet builder.

The builder and its contract live in the sibling crawford skill:

    skills/crawford/scripts/lib/packets.py
    skills/crawford/references/evidence-packet.md

Crawford is the packet BUILDER; this skill is the packet's READER. Two copies
of a 101-field contract is how a field name drifts and a judge starts reading a
key nobody writes, so there is one module and this file points at it.

Path resolution: `CRAWFORD_SCRIPTS` if the environment sets it; else the first
of: a sibling `crawford/` folder next to this skill (the layout of this repo),
`~/.claude/skills/crawford`, `~/.agents/skills/crawford`.

A missing builder raises ImportError HERE, loudly. For the judge the packet is
the evidence, and a judge that silently reads no fields is a judge that says
`fits: true` about a row it never saw.

    from judge import packets
    packet = packets.build_company_packet(anchor, page_cache=cache)
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

#: Point this at another checkout's `crawford/scripts` to test against it.
ENV_CRAWFORD_SCRIPTS = "CRAWFORD_SCRIPTS"


def _candidate_scripts() -> list[Path]:
    """Every place Crawford's scripts/ can sit relative to THIS install, in order.

    The sibling folder comes first: in this repo `skills/calibrated-judge` sits
    beside `skills/crawford`. The two agent skill dirs cover a standalone install
    where both skills were copied into `~/.claude/skills/` or `~/.agents/skills/`.
    """
    here = Path(__file__).resolve()
    skill = here.parents[2] if len(here.parents) > 2 else here.parent   # …/calibrated-judge
    home = Path.home()
    return [
        skill.parent / "crawford" / "scripts",                   # sibling (this repo)
        home / ".claude" / "skills" / "crawford" / "scripts",
        home / ".agents" / "skills" / "crawford" / "scripts",
    ]


def _crawford_scripts() -> Path:
    override = os.environ.get(ENV_CRAWFORD_SCRIPTS)
    if override:
        return Path(override).expanduser()        # an explicit override is never second-guessed
    cands = _candidate_scripts()
    for c in cands:
        if (c / "lib" / "packets.py").is_file():
            return c
    # Nothing exists anywhere: hand back the first candidate so the loud
    # ImportError in _load() names the place this install expected Crawford.
    return cands[0]


def _load():
    """Import Crawford's `lib.packets`, or raise a plain ImportError.

    Loaded by file path under a private module name rather than by adding
    `crawford/scripts` to `sys.path` and importing `lib.packets`: `lib` is a
    common package name, and a judge run that quietly bound some other `lib`
    would be worse than not loading at all.
    """
    scripts = _crawford_scripts()
    path = scripts / "lib" / "packets.py"
    if not path.is_file():
        raise ImportError(
            f"the evidence-packet builder is not at {path} — it lives in the "
            "crawford skill (skills/crawford/scripts/lib/packets.py); "
            f"set {ENV_CRAWFORD_SCRIPTS} to that skill's scripts/ directory")
    name = "_crawford_lib_packets"
    existing = sys.modules.get(name)
    if existing is not None and getattr(existing, "__file__", "") == str(path):
        return existing
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"could not load the packet builder from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:                                   # noqa: BLE001
        sys.modules.pop(name, None)
        raise ImportError(
            f"the packet builder at {path} failed to import: "
            f"{type(exc).__name__}: {exc}") from exc
    return module


_impl = _load()

#: Re-export EVERY name the builder defines — public and private alike. The
#: fixture builders and the contract tests in this skill read private helpers
#: (`_clean`, `_put`, `_Absences`, …); an explicit allow-list here would make
#: the shim a second place the contract is written down, which is the exact
#: thing the move was for.
globals().update({k: v for k, v in vars(_impl).items()
                  if k not in ("__name__", "__file__", "__loader__",
                               "__spec__", "__package__", "__builtins__",
                               "__doc__", "__cached__")})

#: Where the module this shim re-exports actually lives — for an error message
#: or a test that wants to assert the seam, never for importing around it.
CRAWFORD_PACKETS_PATH = str(_crawford_scripts() / "lib" / "packets.py")

#: Crawford's skill folder and its references — for tests and docs that need
#: the contract text (`references/evidence-packet.md`) in every install layout.
CRAWFORD_DIR = _crawford_scripts().parent
CRAWFORD_REFERENCES = CRAWFORD_DIR / "references"


def crawford_dir() -> Path:
    return _crawford_scripts().parent
