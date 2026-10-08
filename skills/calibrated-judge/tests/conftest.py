"""Put scripts/ on sys.path so `from bakeoff import ...` works under pytest."""
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

# --- calibrated-judge mode isolation ------------------------------------------
# The machine's repo .env sets CALIBRATED_JUDGE_MODE and, since 2026-09-18,
# CALIBRATED_JUDGE_ACTIVE_DECISIONS (the per-decision activation list). Tests must
# not depend on that file: this autouse fixture makes every loaded mode reader see
# "no .env" for BOTH settings, so "off unless the env says otherwise" stays true;
# a test that wants the fallback patches it back explicitly.
import sys as _sys
import pytest as _pytest

_MODE_READER_FILES = ("shadow_judge.py", "judge_hook.py", "judge_sidecar.py",
                      "icp_fit_judge.py", "config.py")


@_pytest.fixture(autouse=True)
def _no_env_file_mode_fallback(monkeypatch):
    monkeypatch.delenv("CALIBRATED_JUDGE_MODE", raising=False)
    monkeypatch.delenv("CALIBRATED_JUDGE_ACTIVE_DECISIONS", raising=False)
    for mod in list(_sys.modules.values()):
        f = getattr(mod, "__file__", "") or ""
        if f.endswith(_MODE_READER_FILES) and hasattr(mod, "_mode_from_env_file"):
            monkeypatch.setattr(mod, "_mode_from_env_file", lambda start=None: "", raising=False)
            monkeypatch.setattr(mod, "_active_decisions_from_env_file",
                                lambda start=None: "", raising=False)
    yield
