#!/usr/bin/env python3
"""
web_sanity_sample.py — 200-record cohort web sanity sample.

Per-account web cross-check that verifies:
- domain resolves
- public website's name matches account_name (loosely)
- public website's vertical signals match the cohort's stated ICP

Uses OpenWebNinja for search and Exa for company-page lookup. Both required to be in env:
  OPENWEBNINJA_API_KEY, EXA_API_KEY

(Serper is no longer used — OpenWebNinja Search-Light replaces it
here; same organic-results contract, different vendor.)

Output: a per-record web_evidence row + cohort-level icp_adherence_rate.

Usage:
  python web_sanity_sample.py \\
    --dossier-dir outputs/dossiers/acme/dossiers/ \\
    --sample-size 200 \\
    --icp "dental practice" \\
    --out outputs/dossiers/acme/dq/web_sanity.json
"""

from __future__ import annotations

import argparse
import json
import os
import random
import socket
import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parents[2] / "_shared"))
import project_env  # noqa: E402  (loads <project root>/.env; real env wins)
project_env.load()
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


OPENWEBNINJA_URL = "https://api.openwebninja.com/realtime-web-search/search-light"
EXA_URL = "https://api.exa.ai/contents"


def http_post_json(url: str, headers: dict[str, str], payload: dict[str, Any], timeout: int = 15) -> dict[str, Any]:
    body = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=body, headers={**headers, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def http_get_json(url: str, headers: dict[str, str], params: dict[str, Any], timeout: int = 15) -> dict[str, Any]:
    qs = urllib.parse.urlencode(params)
    req = urllib.request.Request(f"{url}?{qs}", headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def domain_resolves(domain: str) -> bool:
    if not domain:
        return False
    try:
        socket.gethostbyname(domain)
        return True
    except socket.gaierror:
        return False


def openwebninja_search(api_key: str, query: str) -> list[dict[str, Any]]:
    """OpenWebNinja Search-Light (Serper's replacement). Normalizes results back to the
    {link, title, snippet} shape the Serper `organic` contract used, so downstream code
    (which reads `r["link"]`) doesn't need to change."""
    try:
        result = http_get_json(OPENWEBNINJA_URL, {"x-api-key": api_key}, {"q": query, "limit": 5})
        rows = result.get("data") or result.get("results") or []
        if not isinstance(rows, list):
            return []
        return [
            {"link": r.get("url"), "title": r.get("title"), "snippet": r.get("snippet")}
            for r in rows if isinstance(r, dict) and r.get("url")
        ]
    except Exception:
        return []


def exa_contents(api_key: str, urls: list[str]) -> list[dict[str, Any]]:
    if not urls:
        return []
    try:
        result = http_post_json(EXA_URL, {"x-api-key": api_key}, {"urls": urls, "text": True}, timeout=30)
        return result.get("results", [])
    except Exception:
        return []


def name_overlap(account_name: str, page_text: str) -> float:
    if not account_name or not page_text:
        return 0.0
    name_tokens = {t.lower() for t in account_name.split() if len(t) > 2}
    text_tokens = {t.lower() for t in page_text.split() if len(t) > 2}
    if not name_tokens:
        return 0.0
    return len(name_tokens & text_tokens) / len(name_tokens)


def evaluate_account(
    own_key: str,
    exa_key: str,
    account_name: str,
    domain: str | None,
    icp_keywords: list[str],
) -> dict[str, Any]:
    evidence: dict[str, Any] = {
        "account_name": account_name,
        "domain": domain,
        "domain_resolves": False,
        "name_match_score": 0.0,
        "icp_signals_found": [],
        "verdict": "unknown",
    }

    # 1. Domain resolution
    if domain:
        evidence["domain_resolves"] = domain_resolves(domain)

    # 2. Search OpenWebNinja for "{account_name} {ICP-keyword[0]}"
    icp_kw = icp_keywords[0] if icp_keywords else ""
    query = f"{account_name} {icp_kw}".strip()
    organic = openwebninja_search(own_key, query)

    # 3. Pull text from top 2 results via Exa
    urls = [r["link"] for r in organic[:2] if "link" in r]
    contents = exa_contents(exa_key, urls)

    text_blob = " ".join((c.get("text") or "")[:2000] for c in contents)
    evidence["name_match_score"] = round(name_overlap(account_name, text_blob), 2)
    evidence["icp_signals_found"] = [kw for kw in icp_keywords if kw.lower() in text_blob.lower()]

    # 4. Verdict
    if evidence["name_match_score"] >= 0.5 and evidence["icp_signals_found"]:
        evidence["verdict"] = "fit_confirmed"
    elif evidence["name_match_score"] >= 0.5:
        evidence["verdict"] = "name_matches_but_no_icp_signal"
    elif not evidence["domain_resolves"] and not organic:
        evidence["verdict"] = "no_web_presence"
    else:
        evidence["verdict"] = "uncertain"

    return evidence


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dossier-dir", type=Path, required=True)
    ap.add_argument("--sample-size", type=int, default=200)
    ap.add_argument("--icp", action="append", default=[], help="ICP keyword(s); repeatable")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    own_key = os.environ.get("OPENWEBNINJA_API_KEY")
    exa_key = os.environ.get("EXA_API_KEY")
    if not own_key or not exa_key:
        print("ERROR: OPENWEBNINJA_API_KEY and EXA_API_KEY must be set in env", file=sys.stderr)
        return 2

    if not args.icp:
        print("ERROR: at least one --icp keyword required (e.g. 'dental practice')", file=sys.stderr)
        return 2

    files = sorted(args.dossier_dir.glob("*.json"))
    if not files:
        print(f"ERROR: no dossiers in {args.dossier_dir}", file=sys.stderr)
        return 2

    rng = random.Random(args.seed)
    sample = rng.sample(files, min(args.sample_size, len(files)))

    findings = []
    print(f"Sampling {len(sample)} accounts for ICP web sanity (ICP keywords: {args.icp})")
    for i, f in enumerate(sample, 1):
        try:
            doc = json.loads(f.read_text())
        except Exception:
            continue
        name = doc.get("account_name", "")
        # Best-effort domain extraction from provenance_index or fields
        domain = None
        prov = doc.get("provenance_index", {})
        for path, p in prov.items():
            if "website" in path.lower() and isinstance(p.get("value"), str):
                domain = p["value"]
                break
        ev = evaluate_account(own_key, exa_key, name, domain, args.icp)
        ev["account_id"] = doc.get("account_id")
        findings.append(ev)
        if i % 25 == 0:
            print(f"  ... {i}/{len(sample)}")

    confirmed = sum(1 for e in findings if e["verdict"] == "fit_confirmed")
    no_presence = sum(1 for e in findings if e["verdict"] == "no_web_presence")
    icp_rate = confirmed / len(findings) if findings else 0.0

    output = {
        "sampled": len(findings),
        "icp_keywords": args.icp,
        "icp_adherence_rate": round(icp_rate, 3),
        "verdict_counts": {
            "fit_confirmed": confirmed,
            "name_matches_but_no_icp_signal": sum(1 for e in findings if e["verdict"] == "name_matches_but_no_icp_signal"),
            "no_web_presence": no_presence,
            "uncertain": sum(1 for e in findings if e["verdict"] == "uncertain"),
        },
        "evidence": findings,
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(output, indent=2))
    print(f"Wrote {args.out}: ICP adherence {icp_rate:.1%} on {len(findings)} sampled")
    return 0


if __name__ == "__main__":
    sys.exit(main())
