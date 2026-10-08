#!/usr/bin/env python3
"""Jina Reader wrapper — free, zero-key markdown extraction.

URL form: https://r.jina.ai/<target-url>
Returns clean markdown of the page. No API key. Free.

Useful as a zero-cost fallback for sanity-checking a single URL without burning budget.

Usage:
    python jina_reader.py https://acme.example
"""
from __future__ import annotations

import argparse
import sys
from urllib.parse import quote

import requests


def fetch_markdown(url: str, timeout_s: int = 20) -> str:
    """Fetch a URL via Jina Reader. Returns markdown text, raises on HTTP error."""
    target = quote(url, safe=":/?=&%")
    r = requests.get(f"https://r.jina.ai/{target}", timeout=timeout_s)
    r.raise_for_status()
    return r.text


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("url")
    ap.add_argument("--max-chars", type=int, default=4000, help="Truncate output to N chars")
    args = ap.parse_args()

    try:
        md = fetch_markdown(args.url)
    except Exception as e:
        print(f"ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)

    if len(md) > args.max_chars:
        md = md[: args.max_chars] + f"\n\n[... truncated at {args.max_chars} chars]"
    print(md)


if __name__ == "__main__":
    main()
