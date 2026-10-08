#!/usr/bin/env python3
"""Offline smoke test: each tool runs with no API keys. (make check runs this AND the replay.)

    python3 examples/check.py

It runs the Northwind demo end to end in a temporary folder, with every API key
removed from the environment and the repo's own .env ignored, and checks each
step's output:

  1. dossiers      build 7 demo dossiers, validate them against the schema,
                   prove a broken one FAILS, check forbidden words, render HTML
  2. data audit    scan the corpus and test its joins (gate must be PASS)
  3. won/lost/10X  read customers.csv through the win-loss-rewind CSV adapter
  4. packets       build 6 evidence packets offline from site snapshots ($0)
  5. Jev scoring   run judge_packets.py with no key: every row must come back
                   `insufficient`, nothing spent, scores.csv + queue written
  6. PVP draft     the sample email exists and cites a scored prospect

Nothing here calls the network. Exit code 0 means every step passed.
"""
from __future__ import annotations

import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "examples" / "northwind-demo"
PY = sys.executable

KEY_HINTS = ("_KEY", "_TOKEN", "_SECRET")


def clean_env(tmp: Path) -> dict:
    env = {k: v for k, v in os.environ.items() if not any(h in k.upper() for h in KEY_HINTS)}
    env.pop("BLUEPRINT_ROOT", None)
    env["GTM_STARTER_ROOT"] = str(tmp / "no-env-root")      # so no .env is ever read
    env["JEV_CACHE_PATH"] = str(tmp / "jev-cache.jsonl")
    env["CALIBRATED_JUDGE_OUT"] = str(tmp / "judge-log")
    env["CRAWFORD_QUOTA_STATE_DIR"] = str(tmp / "crawford-state")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["GTM_NO_DOTENV"] = "1"                              # never load a real .env
    return env


def sh(args, env, expect_ok=True) -> subprocess.CompletedProcess:
    p = subprocess.run([PY, *map(str, args)], cwd=ROOT, env=env, capture_output=True, text=True)
    if expect_ok and p.returncode != 0:
        raise AssertionError(f"command failed ({p.returncode}): {' '.join(map(str, args))}\n"
                             f"{p.stdout[-1500:]}\n{p.stderr[-1500:]}")
    return p


def step_dossiers(tmp, env):
    dz = tmp / "dossiers"
    sh([DEMO / "build_demo_dossiers.py", dz], env)
    files = sorted(dz.glob("*.json"))
    assert len(files) == 7, f"expected 7 dossiers, got {len(files)}"
    v = ROOT / "skills" / "dossier-builder" / "scripts" / "validate_schema.py"
    sh([v, "--dossier-dir", dz, "--mode", "customer"], env)
    bad = tmp / "bad-dossier"
    bad.mkdir()
    doc = json.loads(files[0].read_text())
    doc.pop("headline")
    (bad / files[0].name).write_text(json.dumps(doc))
    p = sh([v, "--dossier-dir", bad, "--mode", "customer"], env, expect_ok=False)
    assert p.returncode != 0, "a dossier with no headline should fail the schema"
    sh([v, "--forbidden-words", ROOT / "skills/dossier-builder/config/forbidden-words.yaml",
        "--dossier-dir", dz], env)
    sh([ROOT / "skills/dossier-builder/scripts/render_html.py", "--dossier-dir", dz,
        "--out-dir", tmp / "dossiers-html"], env)
    assert len(list((tmp / "dossiers-html").glob("*.html"))) == 7
    sh([v, "--dossier-dir", dz / "prospects", "--mode", "prospect"], env)
    return "7 customer + 1 lost-deal dossiers valid, a broken one rejected, 7 HTML pages rendered"


def step_audit(tmp, env):
    base = tmp / "audits"
    scripts = ROOT / "skills" / "dossier-data-auditor" / "scripts"
    sh([scripts / "scan.py", "--data-dir", DEMO / "corpus", "--audit-slug", "northwind-demo",
        "--out", base / "northwind-demo"], env)
    sh([scripts / "join_test.py", "--audit-slug", "northwind-demo", "--tenant-domain",
        "northwind.example", "--output-base", base, "--data-dir", DEMO / "corpus"], env)
    f = json.loads((base / "northwind-demo" / "audit" / "join_findings.json").read_text())
    assert f["n_candidates_tested"] >= 3, f
    assert f["overall_gate"] == "PASS", f
    return f"{f['n_candidates_tested']} joins tested, gate {f['overall_gate']}"


def step_labels(tmp, env):
    out = tmp / "wlr" / "raw_dataset.json"
    out.parent.mkdir()
    sh([ROOT / "skills/win-loss-rewind/scripts/adapters/csv.py", DEMO / "customers.csv", "--out", out], env)
    d = json.loads(out.read_text())
    assert d["row_count"] == 8, d["row_count"]
    labels = sorted({r["label"] for r in d["customers"]})
    assert labels == ["10X", "lost", "won"], labels
    return f"{d['row_count']} labelled accounts ({', '.join(labels)})"


def step_packets(tmp, env):
    run = tmp / "northwind-run"
    sh([DEMO / "seed_page_cache.py", run], env)
    sh([ROOT / "skills/crawford/scripts/build_packets.py", "--run-dir", run,
        "--companies", DEMO / "companies.csv", "--icp", DEMO / "icp.json", "--offline"], env)
    packets = sorted((run / "packets" / "companies").glob("*.json"))
    assert len(packets) == 6, f"expected 6 packets, got {len(packets)}"
    with_text = 0
    for p in packets:
        site = json.loads(p.read_text())["packet"].get("site") or {}
        if (site.get("homepage_text") or {}).get("status") == "present":
            with_text += 1
    assert with_text == 6, f"only {with_text}/6 packets carry homepage text"
    ledger = run / "cost_ledger.jsonl"
    spent = 0.0
    if ledger.exists():
        for line in ledger.read_text().splitlines():
            if line.strip():
                spent += float(json.loads(line).get("usd") or 0)
    assert spent == 0.0, spent
    return "6 packets built offline, all with homepage text, $0 spent"


def step_judge(tmp, env):
    out = tmp / "scores"
    sh([ROOT / "skills/calibrated-judge/scripts/judge_packets.py", "--packets",
        tmp / "northwind-run" / "packets", "--icp", DEMO / "icp.json", "--out", out], env)
    s = json.loads((out / "summary.json").read_text())
    assert s["rows"] == 6 and s["actions"] == {"insufficient": 6}, s
    assert s["judge_unavailable"] == 6 and s["spent_usd"] == 0, s
    with (out / "scores.csv").open(newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 6 and all(r["error"] == "judge_unavailable" for r in rows)
    assert (out / "review_queue.csv").is_file() and (out / "review_cards.md").is_file()
    return "6 rows scored with no key: all `insufficient`, $0, files written"


def step_pvp(tmp, env):
    draft = (DEMO / "pvp-email-draft.md").read_text()
    with (DEMO / "companies.csv").open(newline="") as fh:
        domains = {r["domain"] for r in csv.DictReader(fh)}
    cited = [d for d in domains if d in draft]
    assert cited, "the PVP draft should cite a prospect from companies.csv"
    return f"draft cites {', '.join(sorted(cited))}"


STEPS = [("1 dossiers", step_dossiers), ("2 data audit", step_audit),
         ("3 won/lost/10X", step_labels), ("4 packets", step_packets),
         ("5 Jev scoring", step_judge), ("6 PVP draft", step_pvp)]


def main() -> int:
    keep = "--keep" in sys.argv
    tmp = Path(tempfile.mkdtemp(prefix="gtm-starter-check-"))
    env = clean_env(tmp)
    failed = 0
    print(f"Offline smoke test (no API keys). Work folder: {tmp}\n")
    for name, fn in STEPS:
        try:
            print(f"PASS  {name:<15} {fn(tmp, env)}")
        except Exception as exc:          # noqa: BLE001 — report every step
            failed += 1
            print(f"FAIL  {name:<15} {exc}")
    print(f"\n{len(STEPS) - failed}/{len(STEPS)} steps passed.")
    if keep:
        print(f"Outputs kept in {tmp}")
    else:
        shutil.rmtree(tmp, ignore_errors=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
