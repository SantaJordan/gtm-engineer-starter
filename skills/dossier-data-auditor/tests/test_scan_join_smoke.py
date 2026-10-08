"""Smoke test: scan.py + join_test.py on the fictional Northwind corpus. No network."""
import json
import subprocess
import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
ROOT = SKILL.parents[1]
CORPUS = ROOT / "examples" / "northwind-demo" / "corpus"


def run(*args):
    p = subprocess.run([sys.executable, *map(str, args)], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr[-2000:]
    return p


def test_scan_then_join(tmp_path):
    run(SKILL / "scripts" / "scan.py", "--data-dir", CORPUS, "--audit-slug", "demo",
        "--out", tmp_path / "demo")
    cands = json.loads((tmp_path / "demo" / "scan" / "join_candidates.json").read_text())
    assert any(c["left"]["column"] == "account_id" for c in cands)
    run(SKILL / "scripts" / "join_test.py", "--audit-slug", "demo", "--tenant-domain",
        "northwind.example", "--output-base", tmp_path, "--data-dir", CORPUS)
    f = json.loads((tmp_path / "demo" / "audit" / "join_findings.json").read_text())
    assert f["overall_gate"] == "PASS"
    assert f["n_candidates_tested"] == len(cands)


def test_join_test_accepts_legacy_string_candidates(tmp_path):
    scan = tmp_path / "demo" / "scan"
    scan.mkdir(parents=True)
    a, c = CORPUS / "accounts.csv", CORPUS / "contacts.csv"
    (scan / "join_candidates.json").write_text(json.dumps(
        {"candidates": [{"left": f"{a}.account_id", "right": f"{c}.account_id"}]}))
    run(SKILL / "scripts" / "join_test.py", "--audit-slug", "demo", "--tenant-domain",
        "northwind.example", "--output-base", tmp_path)
    f = json.loads((tmp_path / "demo" / "audit" / "join_findings.json").read_text())
    assert f["n_candidates_tested"] == 1


# ------------------------------------------------------------- domain picker

def _picker_input(tmp_path, buckets):
    out = tmp_path / "pick"
    out.mkdir()
    (out / "domain_candidates_per_account.json").write_text(json.dumps(
        [{"account_id": "A1", "url_field_values": buckets}]))
    return out


def test_picker_never_picks_a_personal_email_domain(tmp_path):
    """Regression: gmail.com in website/domain/email candidates must not become canonical."""
    out = _picker_input(tmp_path, {"primary_website": ["gmail.com"], "domain_only": ["gmail.com"],
                                   "email_inferred_domain": ["gmail.com", "gmail.com"]})
    run(SKILL / "scripts" / "canonical_domain_pick.py", "--audit-slug", "x", "--out", out)
    pick = json.loads((out / "canonical_domain_picks.json").read_text())[0]
    assert pick["canonical_domain"] is None and pick["verdict"] == "NO_USABLE_DOMAIN"


def test_picker_keeps_the_company_domain_beside_a_personal_one(tmp_path):
    out = _picker_input(tmp_path, {"primary_website": ["northwind.example"],
                                   "email_inferred_domain": ["outlook.com", "northwind.example"]})
    run(SKILL / "scripts" / "canonical_domain_pick.py", "--audit-slug", "x", "--out", out)
    pick = json.loads((out / "canonical_domain_picks.json").read_text())[0]
    assert pick["canonical_domain"] == "northwind.example"
    assert all(c["domain"] != "outlook.com" for c in pick["all_candidates"])


def test_picker_fails_loudly_without_the_personal_domain_list(tmp_path):
    out = _picker_input(tmp_path, {"primary_website": ["gmail.com"]})
    fake_root = tmp_path / "skill"
    (fake_root / "config").mkdir(parents=True)
    (fake_root / "config" / "domain-rubric.yaml").write_text(
        (SKILL / "config" / "domain-rubric.yaml").read_text())
    p = subprocess.run([sys.executable, str(SKILL / "scripts" / "canonical_domain_pick.py"),
                        "--audit-slug", "x", "--out", str(out), "--skill-root", str(fake_root)],
                       capture_output=True, text=True)
    assert p.returncode != 0 and "personal-domains list not found" in p.stderr
    assert not (out / "canonical_domain_picks.json").exists()


# ------------------------------------------------------------- join cardinality

def test_duplicate_account_ids_block_the_join_gate(tmp_path):
    """Regression: accounts.csv lists every account twice, so 5 invoices join to
    10 rows ($500 becomes $1,000). The gate must not PASS or call it high trust."""
    data = tmp_path / "data"
    data.mkdir()
    (data / "accounts.csv").write_text("account_id,company_name\n" + "".join(
        f"ACC000{i},Fictional {i} {j}\n" for i in range(1, 6) for j in range(2)))
    (data / "invoices.csv").write_text("account_id,amount\n" + "".join(
        f"ACC000{i},100\n" for i in range(1, 6)))
    run(SKILL / "scripts" / "scan.py", "--data-dir", data, "--audit-slug", "dup",
        "--out", tmp_path / "audits" / "dup")
    run(SKILL / "scripts" / "join_test.py", "--audit-slug", "dup", "--tenant-domain",
        "seller.example", "--output-base", tmp_path / "audits", "--data-dir", data)
    f = json.loads((tmp_path / "audits" / "dup" / "audit" / "join_findings.json").read_text())
    assert f["overall_gate"] == "BLOCK"
    hit = [x for x in f["findings"] if "cardinality" in x]
    assert hit and all(x["linking_trust"] != "high" for x in hit)
    card = hit[0]["cardinality"]
    assert card["verdict"] == "DUPLICATE_KEYS_MULTIPLY_ROWS" and card["joined_row_count"] == 10


def test_a_normal_many_to_one_join_still_passes(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "accounts.csv").write_text("account_id,company_name\n" + "".join(
        f"ACC000{i},Fictional {i}\n" for i in range(1, 6)))
    (data / "invoices.csv").write_text("account_id,amount\n" + "".join(
        f"ACC000{i},100\n" for i in range(1, 6) for _ in range(2)))
    run(SKILL / "scripts" / "scan.py", "--data-dir", data, "--audit-slug", "ok",
        "--out", tmp_path / "audits" / "ok")
    run(SKILL / "scripts" / "join_test.py", "--audit-slug", "ok", "--tenant-domain",
        "seller.example", "--output-base", tmp_path / "audits", "--data-dir", data)
    f = json.loads((tmp_path / "audits" / "ok" / "audit" / "join_findings.json").read_text())
    assert f["overall_gate"] == "PASS"
    assert all(x["cardinality"]["verdict"] == "OK" for x in f["findings"])


# ------------------------------------------------------------- project .env

def _run_py(code, env):
    import os
    full = {k: v for k, v in os.environ.items()
            if not k.endswith(("_KEY", "_TOKEN", "_PASSWORD", "_SECRET"))}
    full.pop("GTM_NO_DOTENV", None)        # make test sets it; these tests need the loader on
    full.update(env)
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=full)


def test_firecrawl_wrapper_reads_the_project_env_file(tmp_path):
    """Regression: the key is only in <project>/.env, not exported. It must be used."""
    (tmp_path / ".env").write_text("# keys\nexport FIRECRAWL_API_KEY='fc-test-0000'\n")
    code = (
        "import sys; sys.path.insert(0, %r)\n"
        "import firecrawl_scrape as fs\n"
        "seen = {}\n"
        "class R:\n"
        "    def raise_for_status(self): pass\n"
        "    def json(self): return {'success': True}\n"
        "def post(url, headers=None, **k):\n"
        "    seen['auth'] = headers['Authorization']; return R()\n"
        "fs.requests.post = post\n"
        "fs.scrape('https://northwind.example')\n"
        "print('KEY_USED' if seen['auth'] == 'Bearer fc-test-0000' else 'WRONG')\n"
    ) % str(SKILL / "scripts")
    p = _run_py(code, {"GTM_STARTER_ROOT": str(tmp_path)})
    assert p.returncode == 0, p.stderr[-1500:]
    assert p.stdout.strip() == "KEY_USED"
    assert "fc-test-0000" not in p.stderr


def test_real_environment_wins_over_the_env_file(tmp_path):
    (tmp_path / ".env").write_text("FIRECRAWL_API_KEY=from-file\n")
    code = ("import sys, os; sys.path.insert(0, %r)\nimport firecrawl_scrape\n"
            "print(os.environ['FIRECRAWL_API_KEY'])\n") % str(SKILL / "scripts")
    p = _run_py(code, {"GTM_STARTER_ROOT": str(tmp_path), "FIRECRAWL_API_KEY": "from-shell"})
    assert p.stdout.strip() == "from-shell", p.stderr[-800:]


def test_every_provider_wrapper_loads_the_project_env():
    import re
    roots = [SKILL / "scripts", SKILL.parent / "dossier-builder" / "scripts",
             SKILL.parent / "crawford" / "scripts"]
    missing = []
    for root in roots:
        for f in root.glob("*.py"):
            s = f.read_text()
            if re.search(r'os\.environ\.get\("[A-Z_]*(API_KEY|TOKEN|PASSWORD|LOGIN)"', s) \
                    or f.name == "build_packets.py":
                if "project_env.load()" not in s:
                    missing.append(str(f))
    assert not missing, missing


def test_env_file_in_the_working_directory_is_used(tmp_path):
    """Regression: running from a project folder whose .env holds the key."""
    import os
    (tmp_path / ".env").write_text("FIRECRAWL_API_KEY=fc-cwd-0000\n")
    env = {k: v for k, v in os.environ.items() if k not in ("FIRECRAWL_API_KEY", "GTM_STARTER_ROOT", "GTM_NO_DOTENV")}
    code = ("import sys, os; sys.path.insert(0, %r)\nimport firecrawl_scrape\n"
            "print(os.environ.get('FIRECRAWL_API_KEY'))\n") % str(SKILL / "scripts")
    p = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, env=env,
                       capture_output=True, text=True)
    assert p.stdout.strip() == "fc-cwd-0000", p.stderr[-800:]


def test_gtm_no_dotenv_turns_the_loader_off(tmp_path):
    import os
    (tmp_path / ".env").write_text("FIRECRAWL_API_KEY=should-not-load\n")
    env = {k: v for k, v in os.environ.items() if k not in ("FIRECRAWL_API_KEY", "GTM_STARTER_ROOT")}
    env["GTM_NO_DOTENV"] = "1"
    code = ("import sys, os; sys.path.insert(0, %r)\nimport firecrawl_scrape\n"
            "print(os.environ.get('FIRECRAWL_API_KEY'))\n") % str(SKILL / "scripts")
    p = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, env=env,
                       capture_output=True, text=True)
    assert p.stdout.strip() == "None", p.stderr[-800:]
