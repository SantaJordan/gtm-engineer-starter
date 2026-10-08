"""Which Crawford does `judge.packets` bind to?

The judge reads packets Crawford builds; if the shim binds to the wrong copy the
judge reads a 101-field contract nobody is writing any more.

The order these tests pin:

  1. `CRAWFORD_SCRIPTS`: an explicit override, never second-guessed;
  2. the sibling folder `<skill>/../crawford/scripts` (this repo's layout);
  3. `~/.claude/skills/crawford/scripts`, then `~/.agents/skills/crawford/scripts`.

Every layout is a tempdir and `__file__` is monkeypatched onto it, so nothing
here touches the real checkout and nothing touches the network.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from judge import packets

JUDGE_REL = Path("skills") / "calibrated-judge" / "scripts" / "judge"


def _same(a, b) -> bool:
    return Path(a).resolve() == Path(b).resolve()


def _crawford_at(scripts: Path) -> Path:
    lib = scripts / "lib"
    lib.mkdir(parents=True, exist_ok=True)
    (lib / "packets.py").write_text("# stand-in packet builder\n")
    return scripts


def _judge_file(scripts_judge_dir: Path) -> Path:
    scripts_judge_dir.mkdir(parents=True, exist_ok=True)
    f = scripts_judge_dir / "packets.py"
    f.write_text("# stand-in shim\n")
    return f


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path):
    monkeypatch.delenv("CRAWFORD_SCRIPTS", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    yield


def _repo(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "starter"
    here = _judge_file(root / JUDGE_REL)
    skill = here.parents[2]
    return here, skill


def test_sibling_crawford_resolves(tmp_path, monkeypatch):
    here, skill = _repo(tmp_path)
    sib = _crawford_at(skill.parent / "crawford" / "scripts")
    monkeypatch.setattr(packets, "__file__", str(here))
    assert _same(packets._crawford_scripts(), sib)


def test_override_beats_everything(tmp_path, monkeypatch):
    here, skill = _repo(tmp_path)
    _crawford_at(skill.parent / "crawford" / "scripts")
    other = _crawford_at(tmp_path / "elsewhere" / "scripts")
    monkeypatch.setattr(packets, "__file__", str(here))
    monkeypatch.setenv("CRAWFORD_SCRIPTS", str(other))
    assert _same(packets._crawford_scripts(), other)


def test_home_skill_dir_used_when_no_sibling(tmp_path, monkeypatch):
    here, _ = _repo(tmp_path)
    home_copy = _crawford_at(tmp_path / "home" / ".claude" / "skills" / "crawford" / "scripts")
    monkeypatch.setattr(packets, "__file__", str(here))
    assert _same(packets._crawford_scripts(), home_copy)


def test_sibling_beats_home_copy(tmp_path, monkeypatch):
    here, skill = _repo(tmp_path)
    sib = _crawford_at(skill.parent / "crawford" / "scripts")
    _crawford_at(tmp_path / "home" / ".claude" / "skills" / "crawford" / "scripts")
    monkeypatch.setattr(packets, "__file__", str(here))
    assert _same(packets._crawford_scripts(), sib)


def test_nothing_found_raises_importerror_naming_the_sibling(tmp_path, monkeypatch):
    here, skill = _repo(tmp_path)
    monkeypatch.setattr(packets, "__file__", str(here))
    assert _same(packets._crawford_scripts(), skill.parent / "crawford" / "scripts")
    with pytest.raises(ImportError):
        packets._load()


def test_no_monorepo_or_desktop_fallback(tmp_path, monkeypatch):
    """A Desktop checkout of some other repo must never be picked up."""
    here, _ = _repo(tmp_path)
    _crawford_at(tmp_path / "home" / "Desktop" / "Blueprint-GTM-Skills" / "skills"
                 / "blueprint" / "crawford" / "scripts")
    monkeypatch.setattr(packets, "__file__", str(here))
    for cand in packets._candidate_scripts():
        assert "Desktop" not in str(cand)
