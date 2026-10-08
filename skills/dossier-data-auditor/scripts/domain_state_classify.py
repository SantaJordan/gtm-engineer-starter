#!/usr/bin/env python3
"""
domain_state_classify.py — W2b: classify each canonical-picked domain's state.
DNS resolution + Firecrawl page render → one of {verified, parked, dead,
acquired_or_repointed, tenant_subdomain, shortener}.

Usage:
    uv run domain_state_classify.py --audit-slug <slug> [--max-checks 100]

Reads: outputs/audits/<slug>/canonical_domain_picks.json
Emits: outputs/audits/<slug>/domain_state_findings.json
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parents[2] / "_shared"))
import project_env  # noqa: E402  (loads <project root>/.env; real env wins)
project_env.load()
import time
from pathlib import Path
from typing import Any

import requests

PARKING_FINGERPRINTS = [
    "this domain is for sale", "buy this domain", "make an offer",
    "domain is for sale", "sedo.com", "afternic", "godaddy auctions",
    "hugedomains", "bodis", "dan.com", "inquire about this domain",
]
PLATFORM_SUBDOMAIN_SUFFIXES = (
    ".salesforce.com", ".lightning.force.com", ".hubspot.com",
    ".myshopify.com", ".shopify.com", ".zendesk.com", ".freshdesk.com",
    ".atlassian.net",
)
LINK_SHORTENERS = {"bit.ly", "lnkd.in", "t.co", "ow.ly", "tinyurl.com", "buff.ly"}


def check_dns(domain: str) -> bool:
    try:
        socket.gethostbyname(domain)
        return True
    except (socket.gaierror, socket.timeout):
        return False


def firecrawl_render(domain: str, api_key: str | None) -> dict[str, Any]:
    """Best-effort page-render via Firecrawl. Falls back gracefully if no API key."""
    if not api_key:
        return {"status": "no_api_key", "fingerprints_matched": [], "redirect_chain": []}
    try:
        r = requests.post(
            "https://api.firecrawl.dev/v1/scrape",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"url": f"https://{domain}", "formats": ["markdown", "html"]},
            timeout=20,
        )
        if r.status_code != 200:
            return {"status": f"http_{r.status_code}", "fingerprints_matched": [], "redirect_chain": []}
        data = r.json().get("data", {})
        content = (data.get("markdown") or data.get("html") or "").lower()
        matched = [fp for fp in PARKING_FINGERPRINTS if fp in content]
        meta = data.get("metadata", {}) or {}
        return {
            "status": "ok",
            "fingerprints_matched": matched,
            "redirect_chain": meta.get("sourceURL", []),
            "title": meta.get("title"),
            "final_url": meta.get("url"),
        }
    except Exception as e:
        return {"status": f"error:{type(e).__name__}", "fingerprints_matched": [], "redirect_chain": []}


def classify(domain: str, dns_ok: bool, firecrawl: dict[str, Any]) -> str:
    if domain in LINK_SHORTENERS:
        return "shortener"
    if any(domain.endswith(suffix) for suffix in PLATFORM_SUBDOMAIN_SUFFIXES):
        return "tenant_subdomain"
    if not dns_ok:
        return "dead"
    if firecrawl.get("fingerprints_matched"):
        return "parked"
    final_url = firecrawl.get("final_url", "")
    if final_url and not final_url.startswith(f"https://{domain}") and not final_url.startswith(f"http://{domain}"):
        return "acquired_or_repointed"
    return "verified"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit-slug", required=True)
    parser.add_argument("--max-checks", type=int, default=100)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    out_dir = args.out or Path("outputs/audits") / args.audit_slug
    picks_path = out_dir / "canonical_domain_picks.json"
    if not picks_path.exists():
        print(f"ERROR {picks_path} missing — run canonical_domain_pick.py first", file=sys.stderr)
        sys.exit(2)

    picks = json.loads(picks_path.read_text())
    fc_key = os.environ.get("FIRECRAWL_API_KEY")

    seen_domains: dict[str, dict] = {}
    findings = []
    checked = 0
    for p in picks:
        domain = p.get("canonical_domain")
        if not domain:
            continue
        if domain in seen_domains:
            findings.append({**p, "state": seen_domains[domain]["state"], "evidence": seen_domains[domain]["evidence"]})
            continue
        if checked >= args.max_checks:
            findings.append({**p, "state": "not_checked", "evidence": "max_checks_reached"})
            continue
        dns_ok = check_dns(domain)
        fc = firecrawl_render(domain, fc_key) if fc_key else {"status": "no_api_key", "fingerprints_matched": []}
        state = classify(domain, dns_ok, fc)
        seen_domains[domain] = {"state": state, "evidence": {"dns_ok": dns_ok, "firecrawl": fc}}
        findings.append({**p, "state": state, "evidence": seen_domains[domain]["evidence"]})
        checked += 1
        time.sleep(0.05)  # be polite

    (out_dir / "domain_state_findings.json").write_text(json.dumps(findings, indent=2, default=str))
    summary = {}
    for f in findings:
        summary[f.get("state", "unknown")] = summary.get(f.get("state", "unknown"), 0) + 1
    print(json.dumps({"domains_checked": checked, "by_state": summary, "firecrawl_available": bool(fc_key)}, indent=2))


if __name__ == "__main__":
    main()
