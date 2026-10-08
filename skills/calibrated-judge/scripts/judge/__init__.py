"""calibrated-judge — the shared judge module.

    from judge import judge
    result = judge("real_website", evidence, mode="shadow")

The pieces:
    judge.core                the judge itself and `JudgeResult`
    judge.packets             a shim onto crawford's packet builder (no network)
    judge.shadow.observe      the ONLY seam a pipeline hook should call
    judge.config              modes, bands, backend order, paths
    judge.log                 the append-only JSONL decision log
    judge.actuals             the human write-back store (consulted FIRST)
    judge.ratify_from_sheet   a reviewer's Sheet -> actuals (not shipped here)
    judge.report              the weekly calibration scorer

`CALIBRATED_JUDGE_MODE` is `off` unless someone sets it. Off means no call.
"""
from .core import JudgeResult, judge          # noqa: F401
from . import config, compose, core, fit, packets   # noqa: F401

__all__ = ["judge", "JudgeResult", "config", "core", "compose", "fit",
           "packets"]
