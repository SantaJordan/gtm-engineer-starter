#!/usr/bin/env python3
"""Canonicalize domain values: strip protocols, www, paths, query params, normalize case.

Used by W2 before any verification call. Standardizes domains so downstream
matching is unambiguous.

Usage as library:
    from domain_canonicalize import canonicalize, resolve_dns_batch

    canonicalize("HTTPS://www.Acme.example/about?utm=x")  # -> "acme.example"
    canonicalize("acme.co.uk/page")                    # -> "acme.co.uk"
    canonicalize("yourcompany.com")                    # -> "yourcompany.com", WARN: generic placeholder

CLI:
    python domain_canonicalize.py acme.example https://www.example.com/path
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse

try:
    import dns.resolver
    DNS_AVAILABLE = True
except ImportError:
    DNS_AVAILABLE = False


# Heuristic regex: valid domain shape. Allows multi-level TLDs (acme.co.uk).
DOMAIN_RE = re.compile(r"^[a-z0-9-]+(\.[a-z0-9-]+)+$")

GENERIC_PLACEHOLDERS = {
    "example.com", "example.org", "example.net",
    "yourcompany.com", "yourdomain.com",
    "company.com", "domain.com",
    "test.com", "demo.com",
}

URL_SHORTENERS = {
    "bit.ly", "tinyurl.com", "t.co", "goo.gl", "buff.ly",
    "ow.ly", "is.gd", "fb.me", "lnkd.in",
}


def canonicalize(raw: str) -> dict:
    """Return {canonical, warnings: [...], blocks: [...]}.

    canonical is None if the input is unrecoverable (IP, mailto, malformed).
    """
    if not raw or not isinstance(raw, str):
        return {"canonical": None, "warnings": [], "blocks": ["empty_or_non_string"]}

    s = raw.strip().lower()
    warnings = []
    blocks = []

    # mailto: prefix
    if s.startswith("mailto:"):
        warnings.append("input_was_mailto")
        s = s[7:]
        if "@" in s:
            s = s.split("@", 1)[1]   # keep just the domain

    # Email format leakage
    if "@" in s:
        warnings.append("contains_at_sign_email_leak")
        s = s.split("@", 1)[1]

    # URL form
    if s.startswith(("http://", "https://")):
        parsed = urlparse(s)
        s = parsed.netloc
        if parsed.path and parsed.path != "/":
            warnings.append("input_had_path")

    # Strip www.
    if s.startswith("www."):
        s = s[4:]

    # Strip port
    if ":" in s and not _is_ipv6(s):
        s = s.split(":", 1)[0]

    # Strip trailing slash
    s = s.rstrip("/")

    # Strip remaining path/query if any
    if "/" in s:
        s = s.split("/", 1)[0]
        warnings.append("had_residual_path_after_normalization")
    if "?" in s:
        s = s.split("?", 1)[0]

    # IP address?
    if _is_ipv4(s) or _is_ipv6(s):
        blocks.append("ip_address_not_domain")
        return {"canonical": None, "warnings": warnings, "blocks": blocks}

    # Localhost / invalid
    if s in {"localhost", ""} or "." not in s:
        blocks.append("invalid_or_localhost")
        return {"canonical": None, "warnings": warnings, "blocks": blocks}

    # Format check
    if not DOMAIN_RE.match(s):
        blocks.append("invalid_format")
        return {"canonical": s, "warnings": warnings, "blocks": blocks}

    # Generic placeholder
    if s in GENERIC_PLACEHOLDERS:
        warnings.append("generic_placeholder")

    # URL shortener
    if s in URL_SHORTENERS:
        warnings.append("url_shortener_not_company_domain")

    return {"canonical": s, "warnings": warnings, "blocks": blocks}


def _is_ipv4(s: str) -> bool:
    parts = s.split(".")
    if len(parts) != 4:
        return False
    try:
        return all(0 <= int(p) <= 255 for p in parts)
    except ValueError:
        return False


def _is_ipv6(s: str) -> bool:
    return ":" in s and len([c for c in s if c == ":"]) >= 2


def resolve_dns(domain: str, timeout_s: int = 5) -> dict:
    """DNS A-record lookup. Returns {domain, resolved: bool, error: str | None}."""
    if not DNS_AVAILABLE:
        return {"domain": domain, "resolved": False, "error": "dnspython not installed"}
    try:
        resolver = dns.resolver.Resolver()
        resolver.timeout = timeout_s
        resolver.lifetime = timeout_s
        resolver.resolve(domain, "A")
        return {"domain": domain, "resolved": True, "error": None}
    except dns.resolver.NXDOMAIN:
        return {"domain": domain, "resolved": False, "error": "nxdomain"}
    except dns.resolver.NoAnswer:
        return {"domain": domain, "resolved": False, "error": "no_answer"}
    except dns.resolver.Timeout:
        return {"domain": domain, "resolved": False, "error": "timeout"}
    except Exception as e:
        return {"domain": domain, "resolved": False, "error": f"{type(e).__name__}"}


def resolve_dns_batch(domains: list[str], max_workers: int = 50) -> list[dict]:
    """Parallel DNS lookup. Returns list in input order."""
    results = [None] * len(domains)
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(resolve_dns, d): i for i, d in enumerate(domains)}
        for f in as_completed(futures):
            i = futures[f]
            results[i] = f.result()
    return results


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("domains", nargs="+", help="One or more raw domain strings")
    ap.add_argument("--dns", action="store_true", help="Also DNS-resolve")
    args = ap.parse_args()

    out = []
    for raw in args.domains:
        result = canonicalize(raw)
        result["input"] = raw
        if args.dns and result.get("canonical"):
            result["dns"] = resolve_dns(result["canonical"])
        out.append(result)
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
