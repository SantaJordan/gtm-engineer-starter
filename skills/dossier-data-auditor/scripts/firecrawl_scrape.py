#!/usr/bin/env python3
"""Firecrawl wrapper — any URL -> clean markdown.

$0.001/page. 77% content coverage per the Floqer GTM-stack PDF. Default JS-page
scraper for W2 domain audit.

Calls the Firecrawl API directly; this
script calls the API directly per the >50-row rule in global CLAUDE.md.

Usage:
    python firecrawl_scrape.py --url https://acme.example [--only-main-content]
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
from typing import Any

import requests

ENDPOINT = "https://api.firecrawl.dev/v1/scrape"


def scrape(
    url: str,
    only_main_content: bool = True,
    formats: list[str] = None,
    timeout_s: int = 30,
) -> dict[str, Any]:
    """Scrape a URL and return Firecrawl's response payload."""
    key = os.environ.get("FIRECRAWL_API_KEY")
    if not key:
        raise RuntimeError("FIRECRAWL_API_KEY not set. See ../references/tool-equipping.md.")

    formats = formats or ["markdown"]
    r = requests.post(
        ENDPOINT,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={
            "url": url,
            "formats": formats,
            "onlyMainContent": only_main_content,
        },
        timeout=timeout_s,
    )
    r.raise_for_status()
    return r.json()


def detect_parked(markdown: str) -> tuple[bool, str | None]:
    """Match the parked-page fingerprints from audit-checks.yaml against scraped content."""
    fingerprints = [
        "domain is for sale",
        "buy this domain",
        "sedo.com",
        "afternic",
        "godaddy auctions",
        "inquire about this domain",
        "make an offer",
    ]
    md_lower = markdown.lower()
    for fp in fingerprints:
        if fp in md_lower:
            return True, fp
    return False, None


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", required=True)
    ap.add_argument("--check-parked", action="store_true",
                    help="Print 'parked: <fingerprint>' or 'not parked'")
    ap.add_argument("--max-chars", type=int, default=4000)
    args = ap.parse_args()

    try:
        result = scrape(args.url)
    except Exception as e:
        print(f"ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)

    data = result.get("data", {})
    md = data.get("markdown", "")
    meta = data.get("metadata", {})

    if args.check_parked:
        is_parked, fp = detect_parked(md)
        out = {
            "url": meta.get("sourceURL"),
            "title": meta.get("title"),
            "is_parked": is_parked,
            "fingerprint": fp,
            "cost_usd": 0.001,
        }
        print(json.dumps(out, indent=2))
    else:
        out = {
            "url": meta.get("sourceURL"),
            "title": meta.get("title"),
            "markdown": md[: args.max_chars] + ("...[truncated]" if len(md) > args.max_chars else ""),
            "cost_usd": 0.001,
        }
        print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
