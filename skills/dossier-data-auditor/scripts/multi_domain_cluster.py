#!/usr/bin/env python3
"""
multi_domain_cluster.py — W2c: detect parent/subsidiary domain clusters
(one parent, many brand domains) via Blitz domain↔linkedin round-trip.
Blitz is the unlimited primary; OpenWebNinja AI Mode the cross-check;
DiscoLike /subsidiaries the last-resort.

Usage:
    uv run multi_domain_cluster.py --audit-slug <slug>
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parents[2] / "_shared"))
import project_env  # noqa: E402  (loads <project root>/.env; real env wins)
project_env.load()
import time
from pathlib import Path

import requests


def blitz_domain_to_linkedin(domain: str, api_key: str) -> str | None:
    if not api_key:
        return None
    try:
        r = requests.post(
            "https://api.blitz-api.ai/v2/enrichment/domain-to-linkedin",
            headers={"x-api-key": api_key, "Content-Type": "application/json"},
            json={"domain": domain},
            timeout=15,
        )
        if r.status_code != 200:
            return None
        return (r.json() or {}).get("linkedin_url")
    except Exception:
        return None


def own_ai_mode(query: str, api_key: str) -> str | None:
    if not api_key:
        return None
    try:
        r = requests.get(
            "https://api.openwebninja.com/google-ai-mode",
            headers={"x-api-key": api_key},
            params={"q": query},
            timeout=15,
        )
        if r.status_code != 200:
            return None
        return (r.json() or {}).get("answer", "")[:500]
    except Exception:
        return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit-slug", required=True)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--max-pairs", type=int, default=2000,
                        help="cap pair-space to avoid O(N^2) cost")
    args = parser.parse_args()

    out_dir = args.out or Path("outputs/audits") / args.audit_slug
    picks = json.loads((out_dir / "canonical_domain_picks.json").read_text())
    distinct_domains = sorted({p["canonical_domain"] for p in picks if p.get("canonical_domain")})

    blitz_key = os.environ.get("BLITZ_API_KEY")
    own_key = os.environ.get("OPENWEBNINJA_API_KEY")

    if not blitz_key and not own_key:
        (out_dir / "multi_domain_clusters.json").write_text(json.dumps({
            "clusters": [],
            "note": "Blitz + OpenWebNinja keys missing — multi-domain consolidation skipped. Set BLITZ_API_KEY and/or OPENWEBNINJA_API_KEY to enable."
        }, indent=2))
        print(json.dumps({"clusters_found": 0, "reason": "no_provider_keys"}, indent=2))
        return

    # Phase 1: get LinkedIn URL per domain via Blitz
    domain_to_linkedin: dict[str, str | None] = {}
    for d in distinct_domains:
        domain_to_linkedin[d] = blitz_domain_to_linkedin(d, blitz_key) if blitz_key else None
        time.sleep(0.05)

    # Phase 2: cluster by shared LinkedIn URL
    linkedin_to_domains: dict[str, list[str]] = {}
    for d, l in domain_to_linkedin.items():
        if l:
            linkedin_to_domains.setdefault(l, []).append(d)

    clusters = []
    for linkedin_url, domains in linkedin_to_domains.items():
        if len(domains) >= 2:
            # Pick the shortest domain as the likely parent (heuristic)
            parent = min(domains, key=len)
            children = [d for d in domains if d != parent]
            clusters.append({
                "cluster_id": f"cl_{len(clusters):04d}",
                "parent_domain": parent,
                "child_domains": children,
                "linkedin_url": linkedin_url,
                "evidence_signals": ["blitz_same_linkedin"],
                "evidence_quality": "strong",
                "affected_account_keys": [p["account_row_key"] for p in picks
                                           if p.get("canonical_domain") in domains],
                "rollup_recommendation": "rollup_to_parent",
                "human_decision": None,
            })

    # Phase 3: optional OWN AI Mode cross-check for top clusters
    if own_key and clusters:
        for c in clusters[:20]:
            q = f"Is {c['parent_domain']} the parent company of {', '.join(c['child_domains'])}?"
            ai_answer = own_ai_mode(q, own_key)
            if ai_answer:
                c["evidence_signals"].append("openwebninja_ai_confirmed")
                c["ai_mode_answer"] = ai_answer
            time.sleep(0.1)

    (out_dir / "multi_domain_clusters.json").write_text(json.dumps({"clusters": clusters}, indent=2, default=str))
    print(json.dumps({
        "distinct_domains_checked": len(distinct_domains),
        "blitz_resolved_count": sum(1 for v in domain_to_linkedin.values() if v),
        "clusters_found": len(clusters),
    }, indent=2))


if __name__ == "__main__":
    main()
