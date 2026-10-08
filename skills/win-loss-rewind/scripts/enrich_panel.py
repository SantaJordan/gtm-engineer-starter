#!/usr/bin/env python3
"""
Phase 2 — External Reality Enrichment.

For every customer in `outputs/<run-id>/raw_dataset.json` (or `matched_calls.jsonl`
for the company roster), pull a parallel signal panel from Firecrawl, EXA,
OpenWebNinja (local business + organic domain search + news), and Apify (LinkedIn
detail). Reconcile against user-supplied labels. Write
`outputs/<run-id>/customer_panels.jsonl`.

This script calls REST APIs directly (no MCP tools — MCP has too much overhead
for batch operations). Apify *server-side* scrapers are fine because they run
in Apify's cloud, not your local browser. Do NOT add any tool that accesses
LinkedIn via local browser cookies — that violates LinkedIn's TOS.

Serper is a dropped vendor repo-wide (2026-08) — its domain-resolution-fallback and
news roles here now both run through OpenWebNinja (Search-Light + News Search) under
the `openweb_ninja_news` source flag. The `serper_news` PANEL KEY is kept as-is
on purpose: scripts/segment.py, scripts/generate_hypotheses.py, and
prompts/hypothesis-generator.md all read that exact key downstream — only the
vendor call underneath it changed.

Usage:
    python enrich_panel.py --run-id 2026-05-05-acme \\
        [--customer-cap 200] \\
        [--sources firecrawl,exa,openweb_ninja,apify,openweb_ninja_news,wayback] \\
        [--cache] \\
        [--workers 8]

Wayback timeline (default ON, free, keyless): each customer gets a change-
history of homepage + up to 2 key pages via archive.org CDX (max 3 index
calls, no page fetches). The 3s global throttle serializes live archive.org
hits across ALL workers, so ~200 customers add up to ~30 min wall-clock on a
cold cache. Drop `wayback` from --sources to skip.

Outputs:
    outputs/<run-id>/customer_panels.jsonl   one row per customer

Environment:
    FIRECRAWL_API_KEY, EXA_API_KEY, OPENWEBNINJA_API_KEY,
    APIFY_TOKEN  (any subset; missing keys skip those sources)

Optional vertical-specific data warehouses (NOT implemented in scaffold):
    The skill ships with hooks for the operator to wire in their own paid
    public-records data sources — e.g., a permits-and-contractors warehouse
    for construction verticals, or a CMS / NPI / HRSA warehouse for healthcare.
    See references/signal-source-catalog.md § "Optional: vertical-specific
    data warehouses" for the integration shape. None are required.
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import sys
import time
import traceback
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import requests
from dotenv import load_dotenv

# ------------------------------------------------------------------ paths

from wlr_config import repo_root  # noqa: E402
import wlr_schema  # noqa: E402
import panel_parsers as pp  # noqa: E402
REPO_ROOT = repo_root()
SKILL_ROOT = Path(__file__).resolve().parents[1]


def outputs_dir(run_id: str) -> Path:
    return SKILL_ROOT / "outputs" / run_id


# ------------------------------------------------------------------ env

load_dotenv(REPO_ROOT / ".env")
load_dotenv(SKILL_ROOT / ".env")  # secondary fallback

FIRECRAWL_API_KEY = os.environ.get("FIRECRAWL_API_KEY")
EXA_API_KEY = os.environ.get("EXA_API_KEY")
OPENWEBNINJA_API_KEY = os.environ.get("OPENWEBNINJA_API_KEY")
APIFY_TOKEN = os.environ.get("APIFY_TOKEN")


# ------------------------------------------------------------------ cost model
#
# Per-call cost in USD. Pulled from references/signal-source-catalog.md
# and the vendors' public docs. Conservative — undercounts is worse than over.

COST_PER_CALL = {
    "firecrawl_map": 0.001,        # /map is cheap
    "firecrawl_scrape": 0.002,     # single-URL scrape
    "exa_search": 0.005,           # neural search w/ 3 results
    "openweb_ninja": 0.002,        # per references/signal-source-catalog.md
    "apify_linkedin": 0.012,       # anchor/linkedin-company-detail
    "openweb_ninja_news": 0.002,   # /news-search/search ~$0.002/req (per SKILL.md)
    "openweb_ninja_domain_search": 0.005,  # /realtime-web-search/search-light $0.005/q (Pay As You Go)
}


# ------------------------------------------------------------------ source: OpenWebNinja
# (domain-resolution fallback + news). Serper — the prior vendor for both roles — is
# no longer used; see OpenWebNinja's API docs.

def openweb_ninja_search_company_domain(company: str, timeout: int = 20) -> Optional[str]:
    """Best-effort: ask OpenWebNinja Search-Light for the company's website. Returns a bare
    domain. Same organic-result + aggregator-filter logic as the dropped Serper call;
    different vendor and response shape underneath (`data` list of {title,url,snippet})."""
    if not OPENWEBNINJA_API_KEY:
        return None
    try:
        r = requests.get(
            "https://api.openwebninja.com/realtime-web-search/search-light",
            headers={"x-api-key": OPENWEBNINJA_API_KEY},
            params={"q": f"{company} official website", "limit": 5},
            timeout=timeout,
        )
        r.raise_for_status()
        data = r.json()
        rows = data.get("data") or data.get("results") or []
        for hit in rows:
            link = hit.get("url") or hit.get("link") or ""
            if not link:
                continue
            host = urllib.parse.urlparse(link).netloc.lower()
            host = host.removeprefix("www.")
            # filter out aggregators
            if not host:
                continue
            if any(bad in host for bad in (
                "linkedin.com", "facebook.com", "twitter.com", "x.com",
                "youtube.com", "wikipedia.org", "crunchbase.com", "bloomberg.com",
                "yelp.com", "google.com", "reddit.com",
            )):
                continue
            return host
        return None
    except Exception:
        return None


def openweb_ninja_news(company: str, timeout: int = 20) -> dict:
    """OpenWebNinja News Search — replaces the dropped Serper /news call. The PANEL KEY
    this feeds (`serper_news`, see enrich_one_customer below) is intentionally left
    unchanged for downstream compatibility; only this implementation is new."""
    if not OPENWEBNINJA_API_KEY:
        return {"source": "openweb_ninja_news", "success": False, "data": None,
                "error": "OPENWEBNINJA_API_KEY not set"}
    try:
        r = requests.get(
            "https://api.openwebninja.com/news-search/search",
            headers={"x-api-key": OPENWEBNINJA_API_KEY},
            params={"q": company, "limit": 5},
            timeout=timeout,
        )
        r.raise_for_status()
        return pp.parse_own_news(r.json())
    except Exception as e:
        return {"source": "openweb_ninja_news", "success": False, "data": None, "error": str(e)}


# ------------------------------------------------------------------ source: Firecrawl

from panel_parsers import _CAREERS_RX, _PRICING_RX  # noqa: E402,F401


def firecrawl_map(domain: str, timeout: int = 60) -> dict:
    if not FIRECRAWL_API_KEY:
        return {"source": "firecrawl_map", "success": False, "data": None,
                "error": "FIRECRAWL_API_KEY not set"}
    url = f"https://{domain}" if not domain.startswith("http") else domain
    try:
        r = requests.post(
            "https://api.firecrawl.dev/v1/map",
            headers={"Authorization": f"Bearer {FIRECRAWL_API_KEY}",
                     "Content-Type": "application/json"},
            json={"url": url},
            timeout=timeout,
        )
        r.raise_for_status()
        return pp.parse_firecrawl_map(r.json())
    except Exception as e:
        return {"source": "firecrawl_map", "success": False, "data": None, "error": str(e)}


def firecrawl_scrape_homepage(domain: str, timeout: int = 60) -> dict:
    if not FIRECRAWL_API_KEY:
        return {"source": "firecrawl_scrape", "success": False, "data": None,
                "error": "FIRECRAWL_API_KEY not set"}
    url = f"https://{domain}" if not domain.startswith("http") else domain
    try:
        r = requests.post(
            "https://api.firecrawl.dev/v1/scrape",
            headers={"Authorization": f"Bearer {FIRECRAWL_API_KEY}",
                     "Content-Type": "application/json"},
            json={"url": url, "formats": ["markdown", "html"]},
            timeout=timeout,
        )
        r.raise_for_status()
        return pp.parse_firecrawl_scrape(r.json())
    except Exception as e:
        return {"source": "firecrawl_scrape", "success": False, "data": None, "error": str(e)}


# ------------------------------------------------------------------ source: EXA

def exa_search(company: str, timeout: int = 30) -> dict:
    if not EXA_API_KEY:
        return {"source": "exa", "success": False, "data": None,
                "error": "EXA_API_KEY not set"}
    try:
        r = requests.post(
            "https://api.exa.ai/search",
            headers={"x-api-key": EXA_API_KEY, "Content-Type": "application/json"},
            json={
                "query": f"What is {company} and what does it do",
                "type": "neural",
                "numResults": 3,
                "contents": {"text": {"maxCharacters": 1200}},
            },
            timeout=timeout,
        )
        r.raise_for_status()
        return pp.parse_exa(r.json())
    except Exception as e:
        return {"source": "exa", "success": False, "data": None, "error": str(e)}


# ------------------------------------------------------------------ source: OpenWebNinja

def openweb_ninja_local_business(company: str, location: Optional[str], timeout: int = 30) -> dict:
    if not OPENWEBNINJA_API_KEY:
        return {"source": "openweb_ninja", "success": False, "data": None,
                "error": "OPENWEBNINJA_API_KEY not set"}
    if not location:
        return {"source": "openweb_ninja", "success": False, "data": None,
                "error": "no location available; OWN local-business needs a city/region"}
    try:
        params = {"query": f"{company} {location}".strip(), "limit": 1}
        r = requests.get(
            # Endpoint per OpenWebNinja's public API reference.
            "https://api.openwebninja.com/local-business-data/search",
            headers={"x-api-key": OPENWEBNINJA_API_KEY},
            params=params,
            timeout=timeout,
        )
        r.raise_for_status()
        return pp.parse_own_local_business(r.json())
    except Exception as e:
        return {"source": "openweb_ninja", "success": False, "data": None, "error": str(e)}


# ------------------------------------------------------------------ source: Apify (LinkedIn company detail)

def apify_linkedin_company(linkedin_url: str, timeout: int = 120) -> dict:
    if not APIFY_TOKEN:
        return {"source": "apify_linkedin", "success": False, "data": None,
                "error": "APIFY_TOKEN not set"}
    if not linkedin_url:
        return {"source": "apify_linkedin", "success": False, "data": None,
                "error": "customer has no linkedin_url"}
    try:
        r = requests.post(
            f"https://api.apify.com/v2/acts/anchor~linkedin-company-detail/run-sync-get-dataset-items?token={APIFY_TOKEN}",
            headers={"Content-Type": "application/json"},
            json={"linkedinUrl": linkedin_url},
            timeout=timeout,
        )
        r.raise_for_status()
        return pp.parse_apify_linkedin(r.json() or [], linkedin_url)
    except Exception as e:
        return {"source": "apify_linkedin", "success": False, "data": None, "error": str(e)}


# ------------------------------------------------------------------ source: Wayback timeline (free, keyless)

def wayback_timeline(domain: str, key_pages: list[str] | None = None) -> dict:
    """
    Free change-history pre-warm for Phase 6's time-travel verification:
    `moments` (CDX digest-collapse) on the homepage + up to 2 key pages.
    Index calls only — NO page-text fetches here (Phase 6's bracket() does
    those, against the same shared cache). Max 3 CDX requests per customer,
    globally throttled at 3s via wayback_timetravel's flock stamp, so an
    8-worker pool cannot stampede archive.org.
    """
    try:
        import wayback_timetravel as wtt
    except ImportError:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        try:
            import wayback_timetravel as wtt
        except ImportError:
            return {"source": "wayback", "success": False, "data": None,
                    "error": "wayback_timetravel.py not importable"}
    try:
        pages: dict[str, Any] = {}
        targets = [f"https://{domain}"] + (key_pages or [])[:2]
        for url in targets:
            mom = wtt.cdx_moments(url)
            if not mom["captures"]:
                # www/non-www are distinct hosts in some CDX index states.
                for variant in wtt.url_variants(url)[1:]:
                    mom = wtt.cdx_moments(variant)
                    if mom["captures"]:
                        url = variant
                        break
            caps = mom["captures"]
            pages[url] = {
                "n_content_versions": len(caps),
                "first_capture": caps[0]["day"] if caps else None,
                "last_capture": caps[-1]["day"] if caps else None,
                "change_days": [c["day"] for c in caps],
                "truncated": mom["truncated"],
            }
        archived = any(p["n_content_versions"] for p in pages.values())
        return {
            "source": "wayback",
            "success": True,
            "data": {
                "pages": pages,
                "any_archive_coverage": archived,
                "note": None if archived else (
                    "no captures — this customer's web history is unprovable "
                    "via archive.org; Phase 6 evidence caps at RETRO_ASSUMED"),
            },
            "error": None,
        }
    except Exception as e:
        return {"source": "wayback", "success": False, "data": None, "error": str(e)}


# ------------------------------------------------------------------ panel orchestration

ALL_SOURCES = ["firecrawl", "exa", "openweb_ninja", "apify", "openweb_ninja_news", "wayback"]


def reconciliation_flags(panel: dict, customer: dict) -> list[str]:
    """Surface contradictions between user labels and external reality."""
    flags: list[str] = []
    fc = panel.get("firecrawl_map", {}).get("data") or {}
    li = panel.get("apify_linkedin", {}).get("data") or {}

    # User-claimed ARR vs site size
    arr = customer.get("arr") or customer.get("annual_revenue")
    if arr:
        try:
            arr_num = float(str(arr).replace("$", "").replace(",", ""))
            page_count = fc.get("page_count") or 0
            emp = li.get("employees_count") or 0
            if arr_num >= 50_000 and page_count and page_count <= 3:
                flags.append(
                    f"high_arr_thin_site: arr={arr_num} but firecrawl_page_count={page_count}"
                )
            if arr_num >= 50_000 and emp and emp <= 1:
                flags.append(
                    f"high_arr_solo_company: arr={arr_num} but linkedin_employees={emp}"
                )
        except (ValueError, TypeError):
            pass

    # Domain failed to resolve
    if not customer.get("_resolved_domain"):
        flags.append("no_domain_resolved")

    # All sources failed
    sources_run = [k for k in panel.keys() if not k.startswith("_")]
    failed = [k for k in sources_run if panel[k].get("success") is False]
    if sources_run and len(failed) == len(sources_run):
        flags.append("all_enrichment_sources_failed")

    return flags


def enrich_one_customer(customer: dict, sources: list[str]) -> dict:
    """Call every enabled source for one customer. Returns full panel dict."""
    panel: dict[str, Any] = {}
    cost: float = 0.0
    customer = wlr_schema.normalize_customer(customer)
    company = customer["company"]
    domain = customer["domain"]
    location = customer.get("location") or customer.get("city") or customer.get("hq_city")
    linkedin_url = customer.get("linkedin_url") or customer.get("linkedin")

    # Domain resolution fallback (was Serper; OpenWebNinja Search-Light now — dropped
    # vendor, see module docstring)
    if not domain and company and "openweb_ninja_news" in sources:
        domain = openweb_ninja_search_company_domain(company) or ""
        if domain:
            cost += COST_PER_CALL["openweb_ninja_domain_search"]
    customer["_resolved_domain"] = domain

    # Each task is (key, callable). Dispatch in parallel via thread pool.
    tasks: list[tuple[str, Any]] = []
    if "firecrawl" in sources and domain:
        tasks.append(("firecrawl_map", lambda d=domain: firecrawl_map(d)))
        tasks.append(("firecrawl_scrape", lambda d=domain: firecrawl_scrape_homepage(d)))
    if "exa" in sources and company:
        tasks.append(("exa", lambda c=company: exa_search(c)))
    if "openweb_ninja" in sources:
        tasks.append(("openweb_ninja",
                      lambda c=company, l=location: openweb_ninja_local_business(c, l)))
    if "apify" in sources:
        tasks.append(("apify_linkedin",
                      lambda u=linkedin_url: apify_linkedin_company(u)))
    if "openweb_ninja_news" in sources and company:
        # Panel key stays "serper_news" on purpose — segment.py, generate_hypotheses.py,
        # and prompts/hypothesis-generator.md all read this exact key downstream. Only
        # the call underneath changed (Serper dropped 2026-08 -> OpenWebNinja News Search).
        tasks.append(("serper_news", lambda c=company: openweb_ninja_news(c)))

    cost_keys = {
        "firecrawl_map": "firecrawl_map",
        "firecrawl_scrape": "firecrawl_scrape",
        "exa": "exa_search",
        "openweb_ninja": "openweb_ninja",
        "apify_linkedin": "apify_linkedin",
        "serper_news": "openweb_ninja_news",
    }

    if tasks:
        with cf.ThreadPoolExecutor(max_workers=max(1, len(tasks))) as pool:
            futures = {pool.submit(fn): key for key, fn in tasks}
            for fut in cf.as_completed(futures):
                key = futures[fut]
                try:
                    panel[key] = fut.result()
                except Exception as e:
                    panel[key] = {"source": key, "success": False, "data": None,
                                  "error": f"unhandled: {e}"}
                cost += COST_PER_CALL.get(cost_keys.get(key, ""), 0.0)

    # Wayback timeline runs AFTER the pool: it reuses firecrawl_map's
    # discovered careers/pricing URLs as key pages, costs $0, and pre-warms
    # the shared cache Phase 6's bracket() verification reads from.
    timeline = None
    if "wayback" in sources and domain:
        key_pages: list[str] = []
        fc = (panel.get("firecrawl_map") or {}).get("data") or {}
        for u in fc.get("sample_urls") or []:
            if any(p in u for p in _CAREERS_RX + _PRICING_RX):
                key_pages.append(u)
            if len(key_pages) >= 2:
                break
        timeline = wayback_timeline(domain, key_pages)

    return build_panel_record(customer, panel, timeline, cost)


def build_panel_record(customer: dict, panel: dict, timeline=None, cost: float = 0.0) -> dict:
    """The Phase 2 panel row. Shared by live enrichment and recorded replays."""
    customer = wlr_schema.normalize_customer(customer)
    domain = customer.get("_resolved_domain") or customer["domain"]
    out = {
        "customer_id": customer["customer_id"],
        "company": customer["company"],
        "domain": domain,
        "linkedin_url": customer.get("linkedin_url") or customer.get("linkedin"),
        # `outcome` is the VERIFIED outcome every later phase studies; the CRM
        # stage, health and label it came from travel beside it.
        "outcome": customer["outcome"],
        "crm_outcome": customer.get("crm_outcome"),
        "health": customer.get("health"),
        "label": customer.get("label"),
        "close_date": customer.get("close_date"),
        "churn_date": customer.get("churn_date") or customer.get("cancellation_date"),
        "renewal_date": customer.get("renewal_date") or customer.get("last_renewal_date"),
        "first_call_date": customer.get("first_call_date"),
        "signal_panel": panel,
        "wayback_timeline": timeline,
        "reconciliation_flags": reconciliation_flags(panel, customer),
        "enriched_at": datetime.now(timezone.utc).isoformat(),
        "_cost_estimate_usd": round(cost, 4),
    }
    return out


# ------------------------------------------------------------------ I/O

def load_roster(run_id: str) -> list[dict]:
    """Load customer roster from the run dir.

    Tries `raw_dataset.json` first (Phase 0 adapter output), then falls back to
    deduping company rows out of `matched_calls.jsonl`.
    """
    rdir = outputs_dir(run_id)
    raw = rdir / "raw_dataset.json"
    if raw.exists():
        with raw.open() as f:
            data = json.load(f)
        if isinstance(data, dict):
            for key in ("customers", "rows", "data"):
                if key in data and isinstance(data[key], list):
                    return data[key]
        if isinstance(data, list):
            return data
    matched = rdir / "matched_calls.jsonl"
    if matched.exists():
        seen: dict[str, dict] = {}
        with matched.open() as f:
            for line in f:
                row = json.loads(line)
                cid = row.get("customer_id") or row.get("company") or row.get("account_name")
                if not cid or cid in seen:
                    continue
                seen[cid] = {
                    "id": cid,
                    "company": row.get("company") or row.get("account_name"),
                    "domain": row.get("domain"),
                    "outcome": row.get("outcome"),
                    "close_date": row.get("close_date"),
                    "linkedin_url": row.get("linkedin_url"),
                    "location": row.get("location"),
                }
        return list(seen.values())
    raise FileNotFoundError(
        f"No roster found in {rdir}. Expected raw_dataset.json or matched_calls.jsonl."
    )


def load_existing_ids(out_path: Path) -> set[str]:
    if not out_path.exists():
        return set()
    seen: set[str] = set()
    with out_path.open() as f:
        for line in f:
            try:
                row = json.loads(line)
                if "customer_id" in row:
                    seen.add(str(row["customer_id"]))
            except Exception:
                continue
    return seen


# ------------------------------------------------------------------ CLI

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Phase 2 — pull external signal panels for the customer roster.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--run-id", required=True, help="run id under outputs/")
    p.add_argument("--customer-cap", type=int, default=None,
                   help="cap on number of customers (debug)")
    p.add_argument("--sources", default=",".join(ALL_SOURCES),
                   help="comma-separated subset of: " + ",".join(ALL_SOURCES))
    p.add_argument("--cache", action="store_true",
                   help="skip customers already present in customer_panels.jsonl")
    p.add_argument("--workers", type=int, default=8,
                   help="parallel customers (each customer also fans out internally)")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    rdir = outputs_dir(args.run_id)
    rdir.mkdir(parents=True, exist_ok=True)
    out_path = rdir / "customer_panels.jsonl"

    requested_sources = [s.strip() for s in args.sources.split(",") if s.strip()]
    invalid = [s for s in requested_sources if s not in ALL_SOURCES]
    if invalid:
        print(f"ERROR: unknown sources: {invalid}. Valid: {ALL_SOURCES}", file=sys.stderr)
        return 2

    # Warn about missing keys per requested source
    key_for = {
        "firecrawl": FIRECRAWL_API_KEY, "exa": EXA_API_KEY,
        "openweb_ninja": OPENWEBNINJA_API_KEY, "apify": APIFY_TOKEN,
        "openweb_ninja_news": OPENWEBNINJA_API_KEY,
        "wayback": True,  # keyless + free (archive.org)
    }
    skipped = [s for s in requested_sources if not key_for.get(s)]
    if skipped:
        print(f"WARN: missing API key(s); these sources will return failure rows: {skipped}",
              file=sys.stderr)

    roster = load_roster(args.run_id)
    if args.customer_cap:
        roster = roster[: args.customer_cap]

    existing = load_existing_ids(out_path) if args.cache else set()
    work = [c for c in roster
            if str(c.get("id") or c.get("customer_id") or c.get("company")) not in existing]

    print(f"[enrich_panel] run_id={args.run_id} roster={len(roster)} "
          f"to_enrich={len(work)} cached_skip={len(roster) - len(work)} "
          f"sources={requested_sources}")

    started = time.time()
    total_cost = 0.0
    n_done = 0
    n_err = 0

    mode = "a" if args.cache else "w"
    with out_path.open(mode) as fout:
        # Outer parallelism: many customers in flight at once. Each customer
        # also fans out internally to its own thread pool. Keep workers modest
        # so we don't flood APIs.
        with cf.ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
            futures = {pool.submit(enrich_one_customer, c, requested_sources): c
                       for c in work}
            for fut in cf.as_completed(futures):
                c = futures[fut]
                try:
                    row = fut.result()
                except Exception as e:
                    n_err += 1
                    row = {
                        "customer_id": c.get("id") or c.get("company"),
                        "company": c.get("company"),
                        "domain": c.get("domain"),
                        "outcome": c.get("outcome"),
                        "signal_panel": {},
                        "reconciliation_flags": [f"orchestrator_error: {e}"],
                        "enriched_at": datetime.now(timezone.utc).isoformat(),
                        "_cost_estimate_usd": 0.0,
                        "_traceback": traceback.format_exc()[:2000],
                    }
                fout.write(json.dumps(row) + "\n")
                fout.flush()
                total_cost += row.get("_cost_estimate_usd") or 0.0
                n_done += 1
                if n_done % 25 == 0 or n_done == len(work):
                    elapsed = time.time() - started
                    rate = n_done / elapsed if elapsed else 0
                    print(f"  [{n_done}/{len(work)}] cost~${total_cost:.2f} "
                          f"err={n_err} rate={rate:.2f}/s")

    elapsed = time.time() - started
    print(f"[enrich_panel] done. wrote {n_done} rows to {out_path}")
    print(f"  total cost (est): ${total_cost:.2f}")
    print(f"  elapsed: {elapsed:.1f}s   errors: {n_err}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
