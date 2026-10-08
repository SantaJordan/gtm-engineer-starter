"""The dossier schema gate: demo dossiers pass, a broken one fails. No network."""
import json
import subprocess
import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
ROOT = SKILL.parents[1]
BUILD = ROOT / "examples" / "northwind-demo" / "build_demo_dossiers.py"
VALIDATE = SKILL / "scripts" / "validate_schema.py"


def py(*args):
    return subprocess.run([sys.executable, *map(str, args)], capture_output=True, text=True)


def test_demo_dossiers_validate(tmp_path):
    assert py(BUILD, tmp_path).returncode == 0
    p = py(VALIDATE, "--dossier-dir", tmp_path, "--mode", "customer")
    assert p.returncode == 0, p.stderr
    assert "7 dossiers schema-valid" in p.stdout


def test_missing_required_field_fails(tmp_path):
    assert py(BUILD, tmp_path / "ok").returncode == 0
    f = sorted((tmp_path / "ok").glob("*.json"))[0]
    doc = json.loads(f.read_text())
    doc["outcome"] = "not-an-outcome"
    (tmp_path / "bad").mkdir()
    (tmp_path / "bad" / f.name).write_text(json.dumps(doc))
    p = py(VALIDATE, "--dossier-dir", tmp_path / "bad", "--mode", "customer")
    assert p.returncode == 1


def test_forbidden_words_pass_on_demo(tmp_path):
    assert py(BUILD, tmp_path).returncode == 0
    p = py(VALIDATE, "--forbidden-words", SKILL / "config" / "forbidden-words.yaml",
           "--dossier-dir", tmp_path)
    assert p.returncode == 0, p.stderr
