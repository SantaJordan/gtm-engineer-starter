#!/usr/bin/env python3
"""Generic N-provider parallel verification + confidence-weighted merge.

The core of the multi-provider doctrine described in ../references/multi-provider-verification.md:
    "No single provider sees the whole web. Run multiple in parallel, merge."

Usage as a library:
    from multi_provider_verify import verify_domain_company

    result = verify_domain_company(
        domain="acme.example",
        claimed_company="Acme Corp",
        providers=["openwebninja", "exa", "firecrawl", "linkup"],
        tiebreaker="parallel_search",
        final_fallback="openwebninja_website_contacts",
    )
    # result = {
    #     verdict: 'verified' | 'mismatch' | 'unverifiable',
    #     confidence: 'high' | 'low' | 'none',
    #     agree_count: 3,
    #     n_providers: 4,
    #     per_provider: {openwebninja: 'Acme Corp', exa: 'Acme Corp', firecrawl: 'Acme Corp', linkup: 'Acme Group LLC'},
    #     evidence: [{provider, url, snippet, score}],
    #     cost_usd: 0.012,
    # }

Per CLAUDE.md: this script uses requests directly (not MCP) for batch work.

Serper (formerly the "serper" provider here) is a dropped vendor repo-wide as of
replaced by "openwebninja" (OpenWebNinja Search-Light), same
{company_name, url, snippet, cost_usd} return contract.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parents[2] / "_shared"))
import project_env  # noqa: E402  (loads <project root>/.env; real env wins)
project_env.load()
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Callable

import requests

# Provider stubs — each returns {company_name, url, snippet, cost_usd, error?}
# For brevity these are minimal; expand per the API references in
# ../config/provider-routing.yaml and each provider's public API docs.


def _fuzzy_match(a: str, b: str) -> float:
    """Token-set Jaccard with corporate-stopword stripping."""
    stopwords = {"inc", "llc", "llp", "pc", "pa", "pllc", "ltd", "lp", "corp", "company", "co", "the"}
    def normalize(s: str) -> set[str]:
        return {t.lower().strip(".,") for t in s.split() if t.lower().strip(".,") not in stopwords}
    sa, sb = normalize(a), normalize(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / max(len(sa | sb), 1)


def _call_openwebninja(domain: str, claimed_company: str, timeout_s: int = 10) -> dict:
    """OpenWebNinja Search-Light — replaces the dropped Serper `/search` call. No
    Knowledge-Graph field on this endpoint, so we just take the top organic result
    (same fallback path `_call_serper` used when its own KG was empty)."""
    key = os.environ.get("OPENWEBNINJA_API_KEY")
    if not key:
        return {"error": "OPENWEBNINJA_API_KEY not set", "cost_usd": 0.0}
    try:
        r = requests.get(
            "https://api.openwebninja.com/realtime-web-search/search-light",
            headers={"x-api-key": key},
            params={"q": f"{claimed_company} {domain}", "limit": 3},
            timeout=timeout_s,
        )
        r.raise_for_status()
        data = r.json()
        rows = data.get("data") or data.get("results") or []
        top = rows[0] if rows else {}
        return {
            "company_name": (top.get("title") or "").split(" - ")[0],
            "url": top.get("url"),
            "snippet": (top.get("snippet") or "")[:200],
            "cost_usd": 0.005,
        }
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}", "cost_usd": 0.005}


def _call_exa(domain: str, claimed_company: str, timeout_s: int = 15) -> dict:
    key = os.environ.get("EXA_API_KEY")
    if not key:
        return {"error": "EXA_API_KEY not set", "cost_usd": 0.0}
    try:
        r = requests.post(
            "https://api.exa.ai/search",
            headers={"x-api-key": key, "Content-Type": "application/json"},
            json={
                "query": f"{claimed_company} {domain}",
                "category": "company",
                "numResults": 3,
                "text": True,
            },
            timeout=timeout_s,
        )
        r.raise_for_status()
        data = r.json()
        results = data.get("results", [])
        top = results[0] if results else {}
        return {
            "company_name": top.get("title", ""),
            "url": top.get("url"),
            "snippet": (top.get("text") or "")[:200],
            "cost_usd": 0.005,
        }
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}", "cost_usd": 0.005}


def _call_firecrawl(domain: str, claimed_company: str, timeout_s: int = 20) -> dict:
    """Scrape the domain itself; extract <title> + JSON-LD org markup."""
    key = os.environ.get("FIRECRAWL_API_KEY")
    if not key:
        return {"error": "FIRECRAWL_API_KEY not set", "cost_usd": 0.0}
    try:
        r = requests.post(
            "https://api.firecrawl.dev/v1/scrape",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={
                "url": f"https://{domain}",
                "formats": ["markdown"],
                "onlyMainContent": True,
            },
            timeout=timeout_s,
        )
        r.raise_for_status()
        data = r.json()
        page = data.get("data", {})
        meta = page.get("metadata", {})
        title = meta.get("title", "") or meta.get("ogTitle", "")
        return {
            "company_name": title.split(" - ")[0].split(" | ")[0],   # strip suffixes
            "url": meta.get("sourceURL"),
            "snippet": (page.get("markdown") or "")[:200],
            "cost_usd": 0.001,
        }
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}", "cost_usd": 0.001}


def _call_linkup(domain: str, claimed_company: str, timeout_s: int = 10) -> dict:
    key = os.environ.get("LINKUP_API_KEY")
    if not key:
        return {"error": "LINKUP_API_KEY not set", "cost_usd": 0.0}
    try:
        r = requests.post(
            "https://api.linkup.so/v1/search",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"q": f"{claimed_company} {domain}", "depth": "standard"},
            timeout=timeout_s,
        )
        r.raise_for_status()
        data = r.json()
        results = data.get("results", [])
        top = results[0] if results else {}
        return {
            "company_name": top.get("name") or top.get("title", "").split(" - ")[0],
            "url": top.get("url"),
            "snippet": top.get("snippet", "")[:200],
            "cost_usd": 0.005,
        }
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}", "cost_usd": 0.005}


def _call_parallel_search(domain: str, claimed_company: str, timeout_s: int = 10) -> dict:
    """4th-opinion tiebreaker via Parallel.ai Search (Base tier)."""
    key = os.environ.get("PARALLEL_API_KEY")
    if not key:
        return {"error": "PARALLEL_API_KEY not set", "cost_usd": 0.0}
    try:
        r = requests.post(
            "https://api.parallel.ai/v1beta/search",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={
                "query": f"Does {domain} belong to {claimed_company}?",
                "processor": "base",
            },
            timeout=timeout_s,
        )
        r.raise_for_status()
        data = r.json()
        results = data.get("results", [{}])
        return {
            "company_name": results[0].get("title", "").split(" - ")[0],
            "url": results[0].get("url"),
            "snippet": results[0].get("excerpts", [""])[0][:200],
            "cost_usd": 0.004,
        }
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}", "cost_usd": 0.004}


PROVIDER_CALLERS: dict[str, Callable[[str, str], dict]] = {
    "openwebninja": _call_openwebninja,
    "exa": _call_exa,
    "firecrawl": _call_firecrawl,
    "linkup": _call_linkup,
    "parallel_search": _call_parallel_search,
}


@dataclass
class VerificationResult:
    verdict: str   # 'verified' | 'mismatch' | 'unverifiable'
    confidence: str   # 'high' | 'low' | 'none'
    agree_count: int
    n_providers: int
    per_provider: dict[str, str | None]
    evidence: list[dict] = field(default_factory=list)
    cost_usd: float = 0.0


def verify_domain_company(
    domain: str,
    claimed_company: str,
    providers: list[str] = None,
    tiebreaker: str | None = "parallel_search",
    min_agreement: int = 2,
    fuzzy_threshold: float = 0.85,
    timeout_s: int = 25,
) -> VerificationResult:
    """Run providers in parallel, merge with confidence rule.

    Returns VerificationResult — see dataclass.
    """
    providers = providers or ["openwebninja", "exa", "firecrawl", "linkup"]

    per_provider: dict[str, str | None] = {}
    evidence: list[dict] = []
    total_cost = 0.0

    with ThreadPoolExecutor(max_workers=len(providers)) as ex:
        futures = {
            ex.submit(PROVIDER_CALLERS[p], domain, claimed_company): p
            for p in providers if p in PROVIDER_CALLERS
        }
        for f in as_completed(futures, timeout=timeout_s):
            p = futures[f]
            try:
                result = f.result()
            except Exception as e:
                result = {"error": f"{type(e).__name__}: {e}", "cost_usd": 0.0}
            total_cost += result.get("cost_usd", 0.0)
            name = result.get("company_name") if not result.get("error") else None
            per_provider[p] = name
            if name:
                evidence.append({
                    "provider": p,
                    "company_name": name,
                    "url": result.get("url"),
                    "snippet": result.get("snippet", ""),
                    "fuzzy_score_vs_claim": _fuzzy_match(name, claimed_company),
                })

    # Count agreements vs the claimed company
    agree_count = sum(
        1 for e in evidence if e["fuzzy_score_vs_claim"] >= fuzzy_threshold
    )
    n_providers = len([p for p in providers if p in PROVIDER_CALLERS])

    # Tiebreaker: if split, call the tiebreaker provider
    if tiebreaker and tiebreaker in PROVIDER_CALLERS and 0 < agree_count < n_providers:
        tb_result = PROVIDER_CALLERS[tiebreaker](domain, claimed_company)
        total_cost += tb_result.get("cost_usd", 0.0)
        tb_name = tb_result.get("company_name") if not tb_result.get("error") else None
        per_provider[tiebreaker] = tb_name
        if tb_name:
            tb_score = _fuzzy_match(tb_name, claimed_company)
            evidence.append({
                "provider": tiebreaker,
                "company_name": tb_name,
                "url": tb_result.get("url"),
                "snippet": tb_result.get("snippet", ""),
                "fuzzy_score_vs_claim": tb_score,
            })
            if tb_score >= fuzzy_threshold:
                agree_count += 1
            n_providers += 1

    # Verdict
    if agree_count >= min_agreement:
        verdict, confidence = "verified", "high"
    elif agree_count == 0:
        verdict, confidence = "unverifiable", "none"
    else:
        verdict, confidence = "mismatch", "low"

    return VerificationResult(
        verdict=verdict,
        confidence=confidence,
        agree_count=agree_count,
        n_providers=n_providers,
        per_provider=per_provider,
        evidence=evidence,
        cost_usd=round(total_cost, 4),
    )


def main():
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--domain", required=True)
    ap.add_argument("--claimed-company", required=True)
    ap.add_argument("--providers", nargs="+", default=["openwebninja", "exa", "firecrawl", "linkup"])
    ap.add_argument("--tiebreaker", default="parallel_search")
    ap.add_argument("--min-agreement", type=int, default=2)
    args = ap.parse_args()

    result = verify_domain_company(
        domain=args.domain,
        claimed_company=args.claimed_company,
        providers=args.providers,
        tiebreaker=args.tiebreaker,
        min_agreement=args.min_agreement,
    )
    # Print as JSON for sub-agent consumption
    print(json.dumps({
        "verdict": result.verdict,
        "confidence": result.confidence,
        "agree_count": result.agree_count,
        "n_providers": result.n_providers,
        "per_provider": result.per_provider,
        "evidence": result.evidence,
        "cost_usd": result.cost_usd,
    }, indent=2))


if __name__ == "__main__":
    main()
