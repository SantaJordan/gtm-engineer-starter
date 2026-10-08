"""gmaps_facts.py — Google Maps listing facts through DataForSEO (paid, off by default).

One lane: DataForSEO's Google Maps SERP API, Live mode.

    POST https://api.dataforseo.com/v3/serp/google/maps/live/advanced
    Docs:    https://docs.dataforseo.com/v3/serp/google/maps/live/advanced/
    Pricing: https://dataforseo.com/pricing/serp/google-maps-serp-api
             Live mode is listed at $0.002 per SERP page (up to 100 results);
             Standard queue is $0.0006 (normal) / $0.0012 (high priority) but
             answers minutes later. Re-check before a big run.

Auth is HTTP Basic with DATAFORSEO_LOGIN / DATAFORSEO_PASSWORD (environment or
the repo .env). The packet builder calls this ONLY under a positive
`--maps-budget-usd`; with no budget, or no credentials, nothing is called and the
packet records the Maps fields as absent with the reason.

The search is "<name> <city> <state>". The listing whose title best matches the
row's name is kept; when no title shares a word with the name, there is no
match and nothing is returned (a nearby competitor is not this business).

Every network function takes an injectable transport (`http_json`) so tests never
touch the network.
"""
from __future__ import annotations

import base64
import json
import os
import re
from urllib.request import Request, urlopen

__all__ = [
    "GmapsFactsShapeError", "TARGET_FIELDS", "DATAFORSEO_MAPS_COST_USD",
    "DATAFORSEO_URL", "ENV_LOGIN", "ENV_PASSWORD",
    "credentials_from_env", "dataforseo_facts", "parse_dataforseo", "enrich_business",
]

DATAFORSEO_URL = "https://api.dataforseo.com/v3/serp/google/maps/live/advanced"
#: Live-mode list price per request (one SERP page of up to 100 results). The
#: response carries the real charge in `tasks[0].cost`; that wins when present.
DATAFORSEO_MAPS_COST_USD = 0.002
ENV_LOGIN = "DATAFORSEO_LOGIN"
ENV_PASSWORD = "DATAFORSEO_PASSWORD"
#: How many listings to ask for. Price is per 100, so 20 costs the same as 1.
DEPTH = 20
OK_STATUS = 20000

# Canonical record — every caller reads these keys, whatever the provider.
TARGET_FIELDS = (
    "name", "street_address", "city", "state", "zip", "phone", "website",
    "category", "subtypes", "rating", "review_count", "lat", "lng",
    "business_status", "place_id", "fid", "cid", "photo_count", "is_claimed",
)

_UA = os.environ.get("CRAWFORD_USER_AGENT", "gtm-engineer-starter/0.1 (crawford)")


class GmapsFactsShapeError(RuntimeError):
    """The response no longer matches the documented shape. Loud, never swallowed."""


def credentials_from_env(env=None):
    """(login, password) when both are set, else None. Values are never printed."""
    env = os.environ if env is None else env
    login, password = env.get(ENV_LOGIN, ""), env.get(ENV_PASSWORD, "")
    return (login, password) if login and password else None


def _http_json(body: list, credentials, timeout: int = 60) -> dict:
    login, password = credentials
    token = base64.b64encode(f"{login}:{password}".encode()).decode()
    req = Request(DATAFORSEO_URL, data=json.dumps(body).encode(), method="POST",
                  headers={"Authorization": f"Basic {token}",
                           "Content-Type": "application/json", "User-Agent": _UA})
    with urlopen(req, timeout=timeout) as r:                 # noqa: S310 — fixed host
        return json.load(r)


def _words(text) -> set:
    return {w for w in re.findall(r"[a-z0-9]+", str(text or "").lower())
            if len(w) > 2 and w not in {"the", "and", "llc", "inc", "pllc", "group"}}


def _best_item(items: list, name: str):
    """The listing whose title shares the most words with `name`; None if none share any."""
    want = _words(name)
    best, best_score = None, 0
    for it in items:
        if not isinstance(it, dict) or it.get("type") != "maps_search":
            continue
        score = len(want & _words(it.get("title")))
        if score > best_score:
            best, best_score = it, score
    return best


def parse_dataforseo(payload: dict, name: str) -> tuple[dict | None, float | None]:
    """DataForSEO response -> (canonical record or None, charged cost or None)."""
    if not isinstance(payload, dict) or payload.get("status_code") != OK_STATUS:
        raise GmapsFactsShapeError(
            f"DataForSEO status_code={payload.get('status_code') if isinstance(payload, dict) else '?'}")
    tasks = payload.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise GmapsFactsShapeError("DataForSEO response has no tasks")
    task = tasks[0]
    if task.get("status_code") != OK_STATUS:
        raise GmapsFactsShapeError(f"DataForSEO task status_code={task.get('status_code')}: "
                                   f"{task.get('status_message')}")
    cost = task.get("cost")
    cost = float(cost) if isinstance(cost, (int, float)) else None
    results = task.get("result") or []
    items = (results[0].get("items") if results and isinstance(results[0], dict) else None) or []
    if not isinstance(items, list):
        raise GmapsFactsShapeError("DataForSEO items is not a list")
    it = _best_item(items, name)
    if it is None:
        return None, cost
    rating = it.get("rating") if isinstance(it.get("rating"), dict) else {}
    addr = it.get("address_info") if isinstance(it.get("address_info"), dict) else {}
    rec = {k: None for k in TARGET_FIELDS}
    rec.update({
        "name": it.get("title"),
        "street_address": addr.get("address"),
        "city": addr.get("city"), "state": addr.get("region"), "zip": addr.get("zip"),
        "phone": it.get("phone"), "website": it.get("url") or it.get("domain"),
        "category": it.get("category"), "subtypes": it.get("additional_categories"),
        "rating": rating.get("value"), "review_count": rating.get("votes_count"),
        "lat": it.get("latitude"), "lng": it.get("longitude"),
        # DataForSEO's work_hours.current_status is open/closed RIGHT NOW, not
        # whether the business still exists, so no business_status is claimed.
        "business_status": None,
        "place_id": it.get("place_id"), "fid": it.get("feature_id"), "cid": it.get("cid"),
        "photo_count": it.get("total_photos"), "is_claimed": it.get("is_claimed"),
    })
    rec["_lane"] = "dataforseo"
    return rec, cost


def dataforseo_facts(name: str, city: str = "", state: str = "", *, credentials,
                     http_json=None, location_name: str = "United States",
                     language_code: str = "en") -> dict | None:
    """One DataForSEO Maps call -> canonical record, or None when no listing matches.

    The record carries `_cost_usd`: the charge DataForSEO reported, else the list price.
    """
    keyword = " ".join(p for p in (name, city, state) if p)
    body = [{"keyword": keyword, "location_name": location_name,
             "language_code": language_code, "depth": DEPTH}]
    payload = (http_json or _http_json)(body, credentials)
    rec, cost = parse_dataforseo(payload, name)
    charged = DATAFORSEO_MAPS_COST_USD if cost is None else cost
    if rec is None:
        return None
    rec["_cost_usd"] = charged
    rec["_cost_basis"] = "imputed" if cost is None else "measured"
    return rec


def enrich_business(biz: dict, *, credentials=None, ledger=None, row_id: str = None,
                    http_json=None) -> tuple[dict | None, float, str]:
    """Look one business up. Returns (record_or_None, cost_usd, "dataforseo").

    No credentials -> nothing is called and nothing is billed.
    """
    if not credentials:
        return None, 0.0, "dataforseo"
    rec = dataforseo_facts(biz.get("name", ""), biz.get("city", ""), biz.get("state", ""),
                           credentials=credentials, http_json=http_json)
    cost = rec.pop("_cost_usd", DATAFORSEO_MAPS_COST_USD) if rec else DATAFORSEO_MAPS_COST_USD
    basis = rec.pop("_cost_basis", "imputed") if rec else "imputed"
    if ledger is not None:
        ledger.log(row_id or biz.get("name") or "gmaps", provider="dataforseo", usd=cost,
                   action="google-maps-live-advanced", outcome="ok" if rec else "no_result",
                   basis=basis, rung="gmaps-facts")
    return rec, cost, "dataforseo"
