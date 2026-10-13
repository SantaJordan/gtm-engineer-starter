"""Phase 2 parsers: one RAW provider response in, one panel blob out. No network.

The live fetchers in `enrich_panel.py` call these on the vendor's JSON; the
offline replay calls the SAME functions on recorded raw responses. So whatever
the replay can find in a panel, a live run can find too.

Each parser returns the panel envelope {source, success, data, error}. A
provider failure (`success: false`, an `error`, a non-OK `status`), a target
page that answered with a non-2xx HTTP status (Firecrawl's
`metadata.statusCode`, or a `statusCode` / `status_code` / numeric `status`
on any provider's body), or a payload with no usable content returns
`success: False`, so every signal on that source reads UNKNOWN. A 404 page
that says nothing about online booking is not evidence the business has none.
Per-record failures (a business, news, search or dataset item carrying a
non-2xx status or an error) are dropped before any field is read; if no valid
record remains, the panel is failed, never an observed absence. False and zero are reserved for data actually observed. The
fields each one can emit, with their types, are declared in FIELD_TYPES; a
hypothesis may only test a declared field (see `signal_presence.validate_signal`).
"""
from __future__ import annotations

import re
from typing import Any

PAGE_TEXT_CAP = 20000

NUMBER_WORDS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen "
    "fourteen fifteen sixteen seventeen eighteen nineteen twenty".split())}
_COUNT_RX = re.compile(r"\b(\d+|" + "|".join(NUMBER_WORDS) + r")\s+(?:[a-z-]+\s+){0,3}?"
                       r"(?:offices|locations|clinics|practices|branches|sites)\b", re.I)
_SINGLE_RX = re.compile(r"\b(?:our|one|a single|the)\s+(?:[a-z-]+\s+){0,2}?office\b", re.I)
_BOOKING_RX = re.compile(r"\b(book|schedule)\s+(an?\s+)?(appointment|visit)?\s*online\b|"
                         r"\bonline\s+(booking|scheduling)\b", re.I)

_CAREERS_RX = ("careers", "/jobs", "join-us", "join_us", "work-with-us", "open-roles")
_PRICING_RX = ("/pricing", "/plans")
_INVESTOR_RX = ("/investor", "investors", "ir.")
_COMPLIANCE_RX = ("/compliance", "/hipaa", "/soc2", "/soc-2", "/gdpr", "/security/trust",
                  "/trust", "/privacy")

#: source -> field -> type. "records" fields hold a list of dicts typed in RECORD_FIELDS.
FIELD_TYPES: dict[str, dict[str, str]] = {
    "firecrawl_map": {"page_count": "int", "has_careers_page": "bool", "has_pricing_page": "bool",
                      "has_investor_page": "bool", "has_compliance_page": "bool",
                      "sample_urls": "list", "failed_link_count": "int"},
    "firecrawl_scrape": {"title": "str", "meta_description": "str", "markdown_excerpt": "str",
                         "page_text": "str", "copyright_year": "int", "location_count": "int",
                         "mentions_online_booking": "bool"},
    "exa": {"count": "int", "results": "records"},
    "openweb_ninja": {"name": "str", "rating": "float", "reviews_count": "int", "phone": "str",
                      "address": "str", "website": "str"},
    "apify_linkedin": {"employees_count": "int", "employees_range": "str", "headquarters": "str",
                       "founded": "str", "industry": "str", "specialties": "list"},
    "serper_news": {"count": "int", "items": "records"},
}
#: (source, records field) -> record field -> type. "date" fields date the record.
RECORD_FIELDS: dict[tuple[str, str], dict[str, str]] = {
    ("exa", "results"): {"title": "str", "url": "str", "published_date": "date",
                         "text_excerpt": "str"},
    ("serper_news", "items"): {"title": "str", "link": "str", "date": "date",
                               "snippet": "str", "source": "str"},
}


def _ok(source: str, data: dict) -> dict:
    return {"source": source, "success": True, "data": data, "error": None}


def _fail(source: str, why: str) -> dict:
    """A provider failure or unreadable payload. Every signal on it is UNKNOWN,
    never an observed false or zero."""
    return {"source": source, "success": False, "data": None, "error": why}


_STATUS_KEYS = ("statusCode", "status_code", "httpStatusCode", "http_status", "status")


def _http_status_error(obj, what: str = "response") -> str | None:
    """A non-2xx HTTP status recorded on `obj` (a body, page metadata or record),
    or None. Strings like "200" count; string words like "OK" are left to
    _provider_error."""
    if not isinstance(obj, dict):
        return None
    for key in _STATUS_KEYS:
        v = obj.get(key)
        if isinstance(v, bool) or v is None:
            continue
        if isinstance(v, str) and v.strip().isdigit():
            v = int(v.strip())
        if isinstance(v, int) and not 200 <= v < 300:
            return f"{what} returned HTTP {v}"
    return None


_OK_WORDS = ("OK", "SUCCESS", "SUCCEEDED", "COMPLETED", "OPERATIONAL", "PUBLISHED")


def failure_reason(obj, what: str = "response") -> str | None:
    """THE failure check, used for a provider's whole body and for each record it
    returns. Returns why `obj` is a failure, or None when it is usable:
      * not a JSON object;
      * `success` / `ok` is false (bool, or the string "false");
      * a non-empty `error` (string, object or list) or `errors` list;
      * a status word that is not OK-like ("ERROR", "failed", ...);
      * a non-2xx HTTP status (statusCode / status_code / numeric status ...).
    """
    if not isinstance(obj, dict):
        return f"{what} is {type(obj).__name__}, not a JSON object"
    for key in ("success", "ok"):
        v = obj.get(key)
        if v is False or (isinstance(v, str) and v.strip().lower() == "false"):
            return f"{what} reported failure: {str(obj.get('error') or 'no detail')[:200]}"
    err = obj.get("error")
    if err not in (None, "", {}, [], False) and not (isinstance(err, str) and not err.strip()):
        return f"{what} error: {str(err)[:200]}"
    errs = obj.get("errors")
    if isinstance(errs, (list, dict)) and errs:
        return f"{what} errors: {str(errs)[:200]}"
    status = obj.get("status")
    if (isinstance(status, str) and status.strip() and not status.strip().isdigit()
            and status.strip().upper() not in _OK_WORDS):
        return f"{what} status {status!r}"
    return _http_status_error(obj, what)


def _provider_error(body) -> str | None:
    """The provider's own failure report, or a structural one. None if usable.
    A bare list (an Apify dataset) is checked record by record by its parser."""
    if isinstance(body, list):
        return None
    if not isinstance(body, dict):
        return f"payload is {type(body).__name__}, not JSON"
    return failure_reason(body, "provider")


def _record_failed(rec) -> bool:
    """One returned record that is itself a failure (same rules as a provider
    body, via failure_reason). Such a record is dropped; it is never read as
    evidence, present or absent."""
    return failure_reason(rec, "record") is not None


def location_count(text: str):
    """Offices a page says the business runs: "five offices" -> 5. None if not stated."""
    t = text or ""
    m = _COUNT_RX.search(t)
    if m:
        w = m.group(1).lower()
        return int(w) if w.isdigit() else NUMBER_WORDS[w]
    if _SINGLE_RX.search(t):
        return 1
    return None


def parse_firecrawl_scrape(body: dict) -> dict:
    err = _provider_error(body)
    if err:
        return _fail("firecrawl_scrape", err)
    page = body.get("data") if isinstance(body.get("data"), dict) else body
    meta = page.get("metadata") or {}
    bad = (failure_reason(meta, "target page") if isinstance(meta, dict) else None) \
        or failure_reason(page, "target page")
    if bad:
        return _fail("firecrawl_scrape", bad)
    if not (page.get("markdown") or page.get("html")):
        return _fail("firecrawl_scrape", "no page content in the response")
    md = page.get("markdown") or ""
    html = page.get("html") or ""
    full = md + " " + html
    years = re.findall(r"©\s*(\d{4})|copyright[^\d]{0,20}(\d{4})", full, re.I)
    flat = [int(y) for tup in years for y in tup if y and y.isdigit()]
    text = md or re.sub(r"<[^>]+>", " ", html)
    return _ok("firecrawl_scrape", {
        "title": meta.get("title") or meta.get("ogTitle"),
        "meta_description": meta.get("description") or meta.get("ogDescription"),
        "markdown_excerpt": md[:500],
        "page_text": text[:PAGE_TEXT_CAP],
        "copyright_year": max(flat) if flat else None,
        "location_count": location_count(text),
        "mentions_online_booking": bool(_BOOKING_RX.search(text)),
    })


def parse_firecrawl_map(body: dict) -> dict:
    err = _provider_error(body)
    if err:
        return _fail("firecrawl_map", err)
    if not isinstance(body.get("links", body.get("urls")), list):
        return _fail("firecrawl_map", "no link list in the response")
    raw_links = body.get("links") or body.get("urls") or []
    # A link object can carry its own failure (success:false, an error, a
    # non-2xx status). It is dropped before any page is looked for, through
    # the same failure_reason() as every other record.
    good, n_failed = [], 0
    for x in raw_links:
        if isinstance(x, dict):
            if failure_reason(x, "link") is not None:
                n_failed += 1
                continue
            x = x.get("url", "")
        if isinstance(x, str) and x:
            good.append(x)
    if raw_links and not good:
        return _fail("firecrawl_map", "every link in the response failed")
    lc = [l.lower() for l in good]

    def has(pats):
        # Found among the good links: True. Not found, but some links failed:
        # the missing page may be one of them, so UNKNOWN (None), never False.
        if any(any(p in l for p in pats) for l in lc):
            return True
        return None if n_failed else False

    return _ok("firecrawl_map", {
        "page_count": len(lc), "has_careers_page": has(_CAREERS_RX),
        "has_pricing_page": has(_PRICING_RX), "has_investor_page": has(_INVESTOR_RX),
        "has_compliance_page": has(_COMPLIANCE_RX), "sample_urls": lc[:25],
        "failed_link_count": n_failed})


def parse_exa(body: dict) -> dict:
    err = _provider_error(body)
    if err:
        return _fail("exa", err)
    if not isinstance(body.get("results"), list):
        return _fail("exa", "no results list in the response")
    # Exa reports each page fetch in a separate `statuses` array. Every status
    # record goes through failure_reason() (status "error", success:false, an
    # object error, a non-2xx code); a failed one drops the matching result.
    failed_ids = {st.get("id") for st in (body.get("statuses") or [])
                  if isinstance(st, dict) and failure_reason(st, "status record") is not None}
    results = [x for x in (body.get("results") or [])
               if not _record_failed(x) and x.get("id", object()) not in failed_ids]
    if body.get("results") and not results:
        return _fail("exa", "every result's page fetch failed")
    return _ok("exa", {"results": [
        {"title": x.get("title"), "url": x.get("url"), "published_date": x.get("publishedDate"),
         "text_excerpt": (x.get("text") or "")[:1000], "score": x.get("score")}
        for x in results[:3]], "count": len(results)})


def parse_own_local_business(body: dict) -> dict:
    err = _provider_error(body)
    if err:
        return _fail("openweb_ninja", err)
    data = body.get("data", body.get("results"))
    records = data if isinstance(data, list) else ([data] if isinstance(data, dict) and data else [])
    valid = [r for r in records if not _record_failed(r)]
    if records and not valid:
        return _fail("openweb_ninja", "every business record in the response failed")
    primary: dict[str, Any] = valid[0] if valid else {}
    if not primary:
        return _fail("openweb_ninja", "no business record in the response")
    return _ok("openweb_ninja", {
        "name": primary.get("name") or primary.get("business_name"),
        "rating": primary.get("rating"),
        "reviews_count": primary.get("review_count") or primary.get("reviews"),
        "phone": primary.get("phone_number") or primary.get("phone"),
        "address": primary.get("address") or primary.get("full_address"),
        "website": primary.get("website"), "raw_keys": list(primary.keys())[:20]})


def parse_own_news(body: dict) -> dict:
    err = _provider_error(body)
    if err:
        return _fail("serper_news", err)
    if not isinstance(body.get("data"), list):
        return _fail("serper_news", "no item list in the response")
    raw_items = body["data"]
    valid = [it for it in raw_items if not _record_failed(it)]
    if raw_items and not valid:
        return _fail("serper_news", "every news record in the response failed")
    items = valid[:5]
    return _ok("serper_news", {"items": [
        {"title": it.get("title"), "link": it.get("link"), "date": it.get("date"),
         "snippet": it.get("snippet"), "source": it.get("source")} for it in items],
        "count": len(items)})


def parse_apify_linkedin(items: list, linkedin_url: str = "") -> dict:
    if isinstance(items, dict):
        err = _provider_error(items) or "actor returned an object, not a dataset"
        return _fail("apify_linkedin", err)
    if not items:
        return _fail("apify_linkedin", "actor returned an empty dataset")
    valid = [it for it in items if not _record_failed(it)]
    if not valid:
        return _fail("apify_linkedin", "every item in the actor's dataset failed")
    item = valid[0]
    return _ok("apify_linkedin", {
        "employees_count": item.get("employeesCount") or item.get("employees_count"),
        "employees_range": item.get("employeesAtRange") or item.get("employees_in_linkedin"),
        "headquarters": item.get("headquarters"),
        "founded": item.get("foundedOn") or item.get("founded"),
        "industry": item.get("industry"), "specialties": item.get("specialties"),
        "linkedin_url": linkedin_url})


#: panel key -> parser, for replaying recorded raw responses.
PARSERS = {
    "firecrawl_scrape": parse_firecrawl_scrape,
    "firecrawl_map": parse_firecrawl_map,
    "exa": parse_exa,
    "openweb_ninja": parse_own_local_business,
    "serper_news": parse_own_news,
    "apify_linkedin": parse_apify_linkedin,
}


def panel_from_raw(raw: dict) -> dict:
    """{panel_key: raw vendor response} -> signal_panel, through the live parsers."""
    return {key: PARSERS[key](body) for key, body in raw.items() if key in PARSERS}
