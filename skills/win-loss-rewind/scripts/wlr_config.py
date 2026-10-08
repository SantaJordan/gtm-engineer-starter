"""Shared settings for win-loss-rewind scripts: model IDs and where .env lives.

Model IDs live here once, so a model upgrade is a one-line change. Each can be
overridden with an environment variable.

    WLR_SONNET_MODEL  default claude-sonnet-5-5  (themes, segmentation, init, hypotheses)
    WLR_OPUS_MODEL    default claude-opus-5-5    (the Opus audit of fuzzy matches)
"""
from __future__ import annotations

import os
from pathlib import Path

SONNET_MODEL = os.environ.get("WLR_SONNET_MODEL", "claude-sonnet-5-5")
OPUS_MODEL = os.environ.get("WLR_OPUS_MODEL", "claude-opus-5-5")

_MARKERS = ("START-HERE.md", ".git")


def repo_root() -> Path:
    """The starter repo root: the nearest parent holding START-HERE.md or .git.

    Falls back to the current working directory. It never walks into some
    unrelated parent folder's .env.
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        if any((parent / m).exists() for m in _MARKERS):
            return parent
    return Path.cwd()
