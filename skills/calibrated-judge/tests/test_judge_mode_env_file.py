"""CALIBRATED_JUDGE_MODE: the process variable wins; unset falls back to the nearest
repo .env; nothing anywhere (or garbage) means off. judge module"""
import sys
from pathlib import Path
from judge import config as M  # conftest puts scripts/ on sys.path

# captured at import, before the conftest isolation fixture patches the attribute
_REAL_MODE_FROM_ENV_FILE = M._mode_from_env_file


def test_env_file_fallback_reads_the_line(tmp_path):
    (tmp_path / ".env").write_text("OTHER=1\nCALIBRATED_JUDGE_MODE=shadow\n")
    start = tmp_path / "pkg" / "x.py"; start.parent.mkdir()
    assert _REAL_MODE_FROM_ENV_FILE(start=start) == "shadow"


def test_env_file_without_the_line_is_empty(tmp_path):
    (tmp_path / ".env").write_text("OTHER=1\n")
    start = tmp_path / "pkg" / "x.py"; start.parent.mkdir()
    assert _REAL_MODE_FROM_ENV_FILE(start=start) == ""


def test_process_variable_wins_over_env_file(monkeypatch):
    monkeypatch.setenv("CALIBRATED_JUDGE_MODE", "off")
    monkeypatch.setattr(M, "_mode_from_env_file", lambda start=None: "shadow")
    assert M.mode() == "off"


def test_env_file_used_when_process_variable_unset(monkeypatch):
    monkeypatch.delenv("CALIBRATED_JUDGE_MODE", raising=False)
    monkeypatch.setattr(M, "_mode_from_env_file", lambda start=None: "shadow")
    assert M.mode() == "shadow"


def test_garbage_in_env_file_is_off(monkeypatch):
    monkeypatch.delenv("CALIBRATED_JUDGE_MODE", raising=False)
    monkeypatch.setattr(M, "_mode_from_env_file", lambda start=None: "shadoww")
    assert M.mode() == "off"
