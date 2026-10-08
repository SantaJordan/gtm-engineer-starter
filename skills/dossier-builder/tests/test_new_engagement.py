"""new-engagement.sh creates customer work INSIDE the project (and its .gitignore)."""
import shutil
import subprocess
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
AUDITOR = SKILL.parent / "dossier-data-auditor"


def _project(tmp_path):
    """A minimal project: START-HERE.md at the root, the skills copied below it."""
    root = tmp_path / "project"
    (root / "skills").mkdir(parents=True)
    (root / "START-HERE.md").write_text("# start\n")
    shutil.copytree(SKILL / "scripts", root / "skills" / "dossier-builder" / "scripts")
    shutil.copytree(AUDITOR / "scripts", root / "skills" / "dossier-data-auditor" / "scripts")
    return root


def run(args, cwd):
    return subprocess.run(["bash", *map(str, args)], cwd=cwd, capture_output=True, text=True)


def test_scaffold_lands_under_the_project_root_from_any_cwd(tmp_path):
    root = _project(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    p = run([root / "skills/dossier-builder/scripts/new-engagement.sh", "demo"], cwd=elsewhere)
    assert p.returncode == 0, p.stderr
    assert (root / "outputs/dossiers/demo/engagement.json").is_file()
    assert not (tmp_path / "outputs").exists() and not (elsewhere / "outputs").exists()


def test_explicit_project_root_wins(tmp_path):
    root = _project(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    p = run([root / "skills/dossier-builder/scripts/new-engagement.sh", "demo",
             "--project-root", other], cwd=root)
    assert p.returncode == 0, p.stderr
    assert (other / "outputs/dossiers/demo/engagement.json").is_file()


def test_no_project_root_is_a_clear_error(tmp_path):
    loose = tmp_path / "loose"
    shutil.copytree(SKILL / "scripts", loose)
    p = run([loose / "new-engagement.sh", "demo"], cwd=tmp_path)
    assert p.returncode == 3 and "project root" in p.stderr


def test_audit_scaffold_also_lands_under_the_project(tmp_path):
    root = _project(tmp_path)
    p = run([root / "skills/dossier-data-auditor/scripts/new-audit.sh", "a1", "wiki/raw",
             "example.com"], cwd=tmp_path)
    assert p.returncode == 0, p.stderr
    assert (root / "outputs/audits/a1/audit_context.json").is_file()
