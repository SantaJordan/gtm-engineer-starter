#!/usr/bin/env python3
"""Rich evidence packets — Crawford's builder for `references/evidence-packet.md`.

This module and its contract doc are CRAWFORD'S (moved here from the
calibrated-judge skill, where the shape was first written). Crawford is the
packet builder: `scripts/build_packets.py` (Job 8) compiles them from a list,
`lib/rich_packets.py` emits one beside every pack, and the judge skill reads
them through a shim at `judge/packets.py`. The doc travelled with the code —
one contract, one home, one place a field name can change.

One packet per entity, named fields, hard character caps, and a provenance plus
an observation date on every value. The packet is what a literal reader (Jev)
is shown; it is assembled here so the co-location rule is enforced in code
instead of in a prompt.

Three rules from the contract are structural in this file, not advisory:

  1. **A missing field is ABSENT, never an empty string.** A blank that reads
     as a value is how `fits: true` survives a failed fetch.
     Absences are recorded in `packet["absent"]` with one of
     `absent | fetch_failed | not_fetched | junk_page | stale` and a reason.
  2. **Caps are enforced in code.** Every capped string carries `full_chars`
     and flips to `status: "truncated"` at the boundary, so a clipped state is
     visible rather than silently short. Negations, self-descriptions and
     ownership disclosures are extracted from the FULL text before any cap
     is applied (CO-EVID-03).
  3. **A provider's claim travels under the provider's name.** The LinkedIn
     `is_current` flag is carried as `provider_current`, because the flag is
     true for any role with a blank end date — the "Currently retired" owner
     whose 2018 role never got an end date. The name is the bug.

Stdlib only, no network, no vendor client. The page cache, the Maps record and
the registry record all arrive as arguments, so every path here is testable
offline and the caller owns the spend.

    from lib.packets import build_company_packet, build_person_packet
    packet = build_company_packet(anchor, page_cache=cache, maps=rec, registry=nppes)
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

__all__ = [
    "PACKET_VERSION", "CAPS", "FIELD_STATUSES", "ABSENT_STATUSES",
    "MAX_SUBPAGE_FETCHES", "SUBPAGE_TIMEOUT_S", "SUBPAGE_LABEL_RE",
    "NPPES_URL", "NOT_FETCHED_ERRORS", "RICHNESS_TIERS",
    "RICHNESS_REQUIREMENTS", "field", "page_facts", "discover_subpages",
    "parse_nppes", "absence_status", "present_fields", "richness",
    "default_enricher", "build_company_packet", "build_person_packet",
    # the packet card — plain English, one screen, shown before any spend
    "TIER_MEANING", "FIELD_LABELS", "FIELD_ALIASES", "CARD_EXCERPT_CHARS",
    "CARD_ANSWER_EXCERPT_CHARS", "CONTACT_WITHHELD", "EXPERIENCE_FIELDS",
    "explain", "explain_rules",
]

PACKET_VERSION = "1.0"

#: A present field is one of these. `stale` is a present value whose age is
#: past the mission's freshness SLA — still shown, never silently trusted.
FIELD_STATUSES = ("present", "truncated", "stale")
#: An absent field is one of these, and always carries a reason.
#: `not_fetched` is NOT `fetch_failed`: a page an offline run never asked for
#: did not fail. Calling a miss a failure is how "we have no evidence" turns
#: into "the site is dead", which is the false exclude nobody ever complains
#: about because the row simply disappears.
ABSENT_STATUSES = ("absent", "fetch_failed", "not_fetched", "junk_page", "stale")

#: Cache errors that mean WE NEVER ASKED, rather than "the server refused".
#: `offline_cache_miss` is crawford's PageCache in replay; the budget one is
#: `rich_packets._ChainedCache` refusing to spend another fetch.
NOT_FETCHED_ERRORS = ("offline_cache_miss", "offline_miss", "cache_miss",
                      "not_in_cache", "fetch_budget_exhausted", "offline")

#: Character caps, enforced in code. `<=6,000 chars page text per field` is the
#: doctrine line; the narrower pages get narrower caps because a contact page
#: that needs 3,000 characters is a page of chrome.
CAPS = {
    "record.name": 200, "record.city": 120, "record.state": 40,
    "record.phone": 40, "record.domain": 253, "record.linkedin_url": 300,
    "record.title": 200,
    # The row's own claims about the person, capped at the contract's widths.
    "crm.title": 80, "crm.title_as_of": 40, "crm.persona_source": 40,
    "site.domain": 253, "site.final_url": 500, "site.redirect_terminus": 500,
    "site.homepage_text": 6000, "site.about_text": 3000,
    "site.services_text": 3000, "site.contact_text": 1000,
    "site.og_site_name": 200, "site.schema_org_name": 200,
    "site.legal_entity_footer": 600, "site.ownership_disclosure_text": 600,
    "maps.primary_category": 120, "maps.business_status": 40,
    "maps.business_status_as_of": 40, "maps.address": 300, "maps.phone": 40,
    "registry.source": 60, "registry.status": 40, "registry.entity_type": 40,
    "registry.taxonomy_code": 40, "registry.taxonomy_desc": 200,
    "registry.parent_org_lbn": 200, "registry.as_of": 40,
    "profile.headline": 400, "profile.about": 3000, "profile.prior_role": 300,
    "profile.location": 120, "profile.scrape_as_of": 40,
}
#: List-valued fields cap by element count and by per-element characters.
LIST_CAPS = {
    "site.nav_labels": (24, 80),
    "maps.secondary_categories": (12, 80),
    "profile.licenses": (12, 160),
    "profile.current_experiences": (8, 0),
}
#: Per-key caps inside a `current_experiences` entry.
EXPERIENCE_CAPS = {"title": 200, "company": 200, "company_url": 300,
                   "dates_text": 80}

MAX_SUBPAGE_FETCHES = 5
#: Polite, and the same ceiling crawford's PageCache uses.
SUBPAGE_TIMEOUT_S = 15

#: The nav/footer link labels worth a fetch, and nothing else. Matching on the
#: LABEL rather than the href is deliberate: `/p/2481` with the text "About Us"
#: is the about page, and `/about-the-cast` in a blog roll is not.
SUBPAGE_LABEL_RE = re.compile(
    r"about|team|our story|services|treatments|practice areas|contact",
    re.I)
_KIND_RULES = (
    ("contact", re.compile(r"contact", re.I)),
    ("about", re.compile(r"about|team|our story", re.I)),
    ("services", re.compile(r"services|treatments|practice areas", re.I)),
)
#: The order the three named fields are filled in — one page per kind first,
#: so a nav with six service links cannot starve the contact page.
_KIND_ORDER = ("about", "services", "contact")

NPPES_URL = "https://npiregistry.cms.hhs.gov/api/?version=2.1"

_OWNERSHIP_RE = re.compile(
    r"independently owned|owned and operated|family owned|privately (held|owned)"
    r"|affiliated with|not affiliated|a member of|part of the|subsidiary"
    r"|parent company|dental service organi[sz]ation|\bDSO\b|franchise",
    re.I)
_FOOTER_ENTITY_RE = re.compile(
    r"©|\(c\)|all rights reserved|\b(LLC|L\.L\.C\.|PLLC|Inc\.?|Incorporated"
    r"|Ltd\.?|LLP|P\.?C\.?|P\.?A\.?|Corp\.?|Company)\b")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_WS_RE = re.compile(r"[ \t\r\f\v]+")
_BLANKLINE_RE = re.compile(r"\n{3,}")

#: A 200 that renders 40 characters is a wall, a splash or a JS shell, not a
#: homepage. Below this the homepage is `junk_page`, never a short truth.
JUNK_PAGE_MIN_CHARS = 64

_SKIP_TAGS = {"script", "style", "noscript", "svg", "template", "head"}
_BLOCK_TAGS = {"p", "div", "section", "article", "li", "tr", "br", "h1", "h2",
               "h3", "h4", "h5", "h6", "footer", "header", "nav", "td"}


def now_iso() -> str:
    """UTC now, or the fixed time in $SOURCE_DATE_EPOCH (the reproducible-builds
    convention) so a replay builds byte-identical packets."""
    fixed = os.environ.get("SOURCE_DATE_EPOCH")
    if fixed:
        return datetime.fromtimestamp(int(fixed), timezone.utc).isoformat(timespec="seconds")
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------- field objects

def field(value, *, source, observed_at, status="present", cap=None, **extra):
    """One packet field: a value that knows where it came from and when.

    Strings are capped here — the cap is not a promise made in a document, it
    is arithmetic done before the value can travel.
    """
    if status not in FIELD_STATUSES:
        raise ValueError(f"field status must be one of {FIELD_STATUSES}")
    out = {"value": value, "source": str(source), "observed_at": str(observed_at),
           "status": status}
    if isinstance(value, str) and cap and len(value) > cap:
        out["value"] = _truncate_at_sentence(value, cap)
        out["full_chars"] = len(value)
        out["status"] = "truncated"
    out.update(extra)
    return out


def _truncate_at_sentence(text: str, cap: int) -> str:
    """Clip at a sentence boundary when one is near the cap.

    A half sentence is how "we do not place implants" becomes "we do …
    implants" — the clip must not be able to invert a negation.
    """
    if len(text) <= cap:
        return text
    head = text[:cap]
    for mark in (". ", "! ", "? ", ".\n", "\n"):
        idx = head.rfind(mark)
        if idx >= cap * 0.6:
            return head[:idx + 1].rstrip()
    return head.rstrip()


def _clean(text) -> str:
    if not isinstance(text, str):
        return ""
    text = _WS_RE.sub(" ", text.replace("\xa0", " "))
    text = "\n".join(line.strip() for line in text.split("\n"))
    return _BLANKLINE_RE.sub("\n\n", text).strip()


class _Absences(dict):
    """`{"site.about_text": {"status": ..., "reason": ...}}`."""

    def mark(self, key, status, reason):
        if status not in ABSENT_STATUSES:
            raise ValueError(f"absent status must be one of {ABSENT_STATUSES}")
        self[key] = {"status": status, "reason": str(reason)}


def _put(block, absent, key, value, *, source, observed_at, reason="not returned by the source"):
    """Set `block[name]` when the value is real; record the absence when it is not.

    A blank string, a blank list and None are absences. `0` and `False` are
    values — a business with zero reviews is a fact about the business.
    """
    name = key.split(".", 1)[1]
    if value is None or (isinstance(value, (str, list, tuple, dict)) and len(value) == 0):
        absent.mark(key, "absent", reason)
        return
    if isinstance(value, str):
        value = _clean(value)
        if not value:
            absent.mark(key, "absent", reason)
            return
        block[name] = field(value, source=source, observed_at=observed_at,
                            cap=CAPS.get(key))
        return
    if isinstance(value, (list, tuple)):
        max_items, per_item = LIST_CAPS.get(key, (24, 200))
        items = list(value)[:max_items]
        if per_item:
            items = [_clean(i)[:per_item] if isinstance(i, str) else i for i in items]
            items = [i for i in items if i != ""]
        if not items:
            absent.mark(key, "absent", reason)
            return
        extra = {"full_count": len(value)} if len(value) > len(items) else {}
        block[name] = field(items, source=source, observed_at=observed_at, **extra)
        return
    block[name] = field(value, source=source, observed_at=observed_at)


# ------------------------------------------------------------------ page facts

class _PageParser(HTMLParser):
    """Visible text, link inventory, og:site_name, JSON-LD name, footer text.

    One pass, stdlib only. Zone tracking (nav / footer / body) is what lets a
    footer disclosure be labelled a footer disclosure.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.links: list[dict] = []
        self.og_site_name = ""
        self.schema_names: list[str] = []
        self.footer_parts: list[str] = []
        self._skip = 0
        self._zone: list[str] = []
        self._ld = 0
        self._ld_buf: list[str] = []
        self._a: dict | None = None

    # -- helpers
    def _zone_now(self) -> str:
        return self._zone[-1] if self._zone else "body"

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "script" and (a.get("type") or "").lower() == "application/ld+json":
            self._ld += 1
            self._ld_buf = []
            return
        if tag in _SKIP_TAGS:
            self._skip += 1
            return
        if tag == "meta":
            prop = (a.get("property") or a.get("name") or "").lower()
            if prop in ("og:site_name", "og:sitename") and a.get("content"):
                self.og_site_name = a["content"]
            return
        if tag in ("nav", "footer", "header"):
            self._zone.append("nav" if tag in ("nav", "header") else "footer")
        if tag == "a":
            self._a = {"href": a.get("href") or "", "text": [],
                       "zone": self._zone_now()}
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag == "script" and self._ld:
            self._ld -= 1
            self._harvest_ld("".join(self._ld_buf))
            self._ld_buf = []
            return
        if tag in _SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
            return
        if tag == "a" and self._a is not None:
            self._a["text"] = _clean("".join(self._a["text"]))
            if self._a["href"]:
                self.links.append(self._a)
            self._a = None
        if tag in ("nav", "footer", "header") and self._zone:
            self._zone.pop()
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._ld:
            self._ld_buf.append(data)
            return
        if self._skip:
            return
        self.parts.append(data)
        if self._a is not None:
            self._a["text"].append(data)
        if self._zone_now() == "footer":
            self.footer_parts.append(data)

    def _harvest_ld(self, raw):
        try:
            obj = json.loads(raw)
        except ValueError:
            return
        stack = [obj]
        while stack:
            cur = stack.pop()
            if isinstance(cur, dict):
                name = cur.get("name")
                if isinstance(name, str) and name.strip():
                    self.schema_names.append(name.strip())
                stack.extend(v for v in cur.values() if isinstance(v, (dict, list)))
            elif isinstance(cur, list):
                stack.extend(cur)


def page_facts(html: str) -> dict:
    """Parse one page into the named facts a packet needs.

    Returns `{text, links[], nav_labels[], og_site_name, schema_org_name,
    footer_text, legal_entity_footer, ownership_sentences[]}`. Every value is
    a string or a list — never None — because this is an internal shape; the
    packet layer is where absence becomes a recorded status.
    """
    p = _PageParser()
    try:
        p.feed(html or "")
        p.close()
    except Exception:                                     # noqa: BLE001
        pass                                              # partial parse beats none
    text = _clean("".join(p.parts))
    footer_text = _clean(" ".join(p.footer_parts))
    legal = [ln for ln in footer_text.split("\n") if _FOOTER_ENTITY_RE.search(ln)]
    # Ownership disclosure is extracted from the FULL text, before any cap.
    sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(text.replace("\n", " "))]
    ownership = [s for s in sentences if s and _OWNERSHIP_RE.search(s)]
    nav_labels = [ln["text"] for ln in p.links
                  if ln["zone"] in ("nav", "footer") and ln["text"]]
    return {
        "text": text,
        "links": p.links,
        "nav_labels": nav_labels,
        "og_site_name": p.og_site_name.strip(),
        "schema_org_name": p.schema_names[0] if p.schema_names else "",
        "footer_text": footer_text,
        "legal_entity_footer": " ".join(legal).strip(),
        "ownership_sentences": ownership,
    }


# ------------------------------------------------------------ sub-page discovery

def _host(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


#: Known two-part suffixes. The list is a SHORTCUT, never the rule — a
#: 12-entry list of public suffixes is wrong for every country it forgot, and
#: the rules below are what actually decide.
_MULTI_TLDS = {"co.uk", "org.uk", "ac.uk", "gov.uk", "co.nz", "com.au",
               "co.za", "co.jp", "com.br", "co.in", "org.au", "net.au"}

#: Second-level labels that are a REGISTRY, not a registrant, under a
#: two-letter ccTLD: `example.com.au` and `example.co.nz` are one site each.
_CC_SECOND_LEVEL = frozenset((
    "co", "com", "net", "org", "gov", "edu", "ac", "sch", "asn", "id", "ne",
    "or", "go", "mil", "nom", "info", "biz", "plc", "ltd", "me", "govt",
    "nhs", "police", "res", "gen", "firm", "web"))

#: US locality-style third-level registries: `exampletown.k12.ca.us` is the
#: registrable name, and two districts under `k12.ca.us` are two different
#: sites. Without this, every school district in California reads as one.
_LOCALITY_SLDS = frozenset((
    "k12", "cc", "lib", "state", "ci", "town", "vil", "gen", "mus", "tec",
    "dst", "cog", "pvt", "chtr", "paroch"))


def _suffix_labels(labels) -> int:
    """How many trailing labels are PUBLIC SUFFIX rather than a name.

    Conservative on purpose. The only consumer is "same site or not", and the
    two errors are not equal: keeping one label too many skips a fetch, while
    keeping one too few glues two unrelated sites together and follows an
    off-site link as if it were an About page. So an unrecognised shape keeps
    more labels, never fewer.
    """
    last = labels[-1]
    pen = labels[-2] if len(labels) >= 2 else ""
    n = 1
    if ".".join(labels[-2:]) in _MULTI_TLDS:
        n = 2
    elif len(last) == 2 and (pen in _CC_SECOND_LEVEL or len(pen) == 2):
        # a two-letter ccTLD under a registry label (com.au) or a region code
        # (ca.us, qc.ca) — the pair is the suffix
        n = 2
    if (n == 2 and len(last) == 2 and len(pen) == 2 and len(labels) >= 4
            and labels[-3] in _LOCALITY_SLDS):
        n = 3
    return n


def registrable_domain(url_or_host: str) -> str:
    """The registrable name: one label in front of the public suffix.

    Rule-based rather than a hardcoded list, because a list is wrong for every
    country it forgot. `_suffix_labels` decides how much of the tail is
    suffix; everything unrecognised keeps two labels, the ordinary `.com` case.
    """
    host = _host(url_or_host) if "://" in url_or_host or "/" in url_or_host else \
        (url_or_host or "").lower().lstrip(".")
    if host.startswith("www."):
        host = host[4:]
    labels = [p for p in host.split(".") if p]
    if len(labels) <= 2:
        return ".".join(labels)
    keep = _suffix_labels(labels) + 1
    return ".".join(labels[-keep:])


def _kind_of(label: str) -> str | None:
    for kind, rx in _KIND_RULES:
        if rx.search(label or ""):
            return kind
    return None


def discover_subpages(html: str, base_url: str, *, limit=MAX_SUBPAGE_FETCHES) -> list[dict]:
    """Nav/footer links worth one fetch each: `[{url, label, kind}]`.

    Same registrable domain only, deduped, and capped at `limit`. The cap is
    spent one-page-per-kind first (about, then services, then contact) so a
    long services menu cannot crowd out the contact page.
    """
    facts = page_facts(html)
    base_reg = registrable_domain(base_url)
    by_kind: dict[str, list[dict]] = {k: [] for k in _KIND_ORDER}
    seen = set()
    for link in facts["links"]:
        label = link.get("text") or ""
        if not label or not SUBPAGE_LABEL_RE.search(label):
            continue
        href = (link.get("href") or "").strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        url = urljoin(base_url, href)
        if urlsplit(url).scheme not in ("http", "https"):
            continue
        if registrable_domain(url) != base_reg:
            continue
        url = url.split("#", 1)[0]
        if url.rstrip("/") == (base_url or "").rstrip("/") or url in seen:
            continue
        kind = _kind_of(label)
        if kind is None:
            continue
        seen.add(url)
        by_kind[kind].append({"url": url, "label": label, "kind": kind})
    out: list[dict] = []
    for kind in _KIND_ORDER:                      # one of each first
        if by_kind[kind]:
            out.append(by_kind[kind][0])
    for kind in _KIND_ORDER:                      # then fill in kind order
        out.extend(by_kind[kind][1:])
    return out[:limit]


# ------------------------------------------------------------------- registry

def parse_nppes(payload, *, observed_at=None, source_url=NPPES_URL) -> dict | None:
    """One NPPES `version=2.1` response -> the named registry record, or None.

    None means "the registry did not answer for this entity", which is an
    absence the caller records — never a blank record that reads as a miss.
    """
    if not isinstance(payload, dict):
        return None
    results = payload.get("results")
    if not isinstance(results, list) or not results:
        return None
    rec = results[0]
    if not isinstance(rec, dict):
        return None
    basic = rec.get("basic") if isinstance(rec.get("basic"), dict) else {}
    taxonomies = [t for t in (rec.get("taxonomies") or []) if isinstance(t, dict)]
    primary = next((t for t in taxonomies if t.get("primary")), None)
    if primary is None and taxonomies:
        primary = taxonomies[0]
    enum_type = (rec.get("enumeration_type") or "").upper()
    entity_type = {"NPI-1": "individual", "NPI-2": "organization"}.get(enum_type, "")
    out = {
        "source": "nppes",
        "source_url": source_url,
        "npi": str(rec.get("number") or ""),
        "status": basic.get("status") or "",
        "entity_type": entity_type,
        "taxonomy_code": (primary or {}).get("code") or "",
        "taxonomy_desc": (primary or {}).get("desc") or "",
        "parent_org_lbn": basic.get("parent_organization_legal_business_name") or "",
        "as_of": basic.get("last_updated") or "",
        "observed_at": observed_at or now_iso(),
    }
    return {k: v for k, v in out.items() if v not in ("", None)}


def _normalise_registry(registry, observed_at):
    """Accept a raw NPPES payload or an already-named registry record."""
    if not isinstance(registry, dict) or not registry:
        return None
    if "results" in registry or "result_count" in registry:
        return parse_nppes(registry, observed_at=observed_at)
    return dict(registry)


# ------------------------------------------------------------- company packet

_REGISTRY_FIELDS = ("source", "status", "entity_type", "taxonomy_code",
                    "taxonomy_desc", "parent_org_lbn", "as_of")
#: What a person packet inherits from its company — identity, never page text.
IDENTITY_SITE_FIELDS = ("domain", "final_url", "og_site_name",
                        "schema_org_name", "legal_entity_footer")


def _anchor_url(anchor: dict) -> str:
    dom = (anchor.get("domain") or anchor.get("site") or "").strip()
    if not dom:
        url = (anchor.get("url") or anchor.get("final_url") or "").strip()
        return url
    if "://" in dom:
        return dom
    return f"https://{dom.strip('/')}/"


def _fetch(page_cache, url):
    """`(rec, html, error)` from a duck-typed crawford PageCache."""
    try:
        rec, html = page_cache.get_or_fetch_page(url)
    except Exception as exc:                              # noqa: BLE001
        return {}, "", f"{type(exc).__name__}: {exc}"[:200]
    rec = rec if isinstance(rec, dict) else {}
    err = rec.get("error") or ""
    status = rec.get("status")
    if not err and isinstance(status, int) and status >= 400:
        err = f"http_{status}"
    return rec, (html or ""), err


def absence_status(err, page_cache=None) -> str:
    """`fetch_failed` or `not_fetched` — which kind of nothing this is.

    A request that was MADE and came back wrong is a failure. A request nobody
    made — an offline replay, an exhausted fetch budget — is not, and the two
    must not share a name: one is evidence about the site, the other is
    evidence about the run.
    """
    text = str(err or "").strip().lower()
    if any(marker in text for marker in NOT_FETCHED_ERRORS):
        return "not_fetched"
    if not text and getattr(page_cache, "offline", False):
        return "not_fetched"
    return "fetch_failed"


def build_company_packet(anchor, *, page_cache=None, maps=None, registry=None,
                         fetch_subpages=True,
                         max_subpage_fetches=MAX_SUBPAGE_FETCHES,
                         observed_at=None) -> dict:
    """The company packet: record anchor + site + maps + registry.

    `page_cache` is crawford's PageCache (or anything with
    `get_or_fetch_page(url) -> (record, html)`); passing an offline cache makes
    this a pure replay. `maps` is a gmaps_facts record, `registry` a raw NPPES
    payload or an already-named registry record. All three are arguments, so
    this function never spends a cent of its own.
    """
    anchor = dict(anchor or {})
    built_at = observed_at or now_iso()
    absent = _Absences()
    record: dict = {}
    site: dict = {}
    maps_block: dict = {}
    registry_block: dict = {}
    fetches: list[dict] = []

    src = anchor.get("record_source") or "record anchor"
    for name in ("name", "city", "state", "phone", "domain", "linkedin_url"):
        value = anchor.get(name)
        if value in (None, ""):
            continue
        _put(record, absent, f"record.{name}", value, source=src,
             observed_at=anchor.get("as_of") or built_at)
    if "name" not in record:
        absent.mark("record.name", "absent", "the row carries no entity name")

    url = _anchor_url(anchor)
    if not url:
        for key in ("site.domain", "site.homepage_text"):
            absent.mark(key, "absent", "the row carries no domain")
    elif page_cache is None:
        for key in ("site.domain", "site.homepage_text"):
            absent.mark(key, "absent", "no page cache was supplied")
    else:
        _build_site(site, absent, fetches, url, page_cache,
                    fetch_subpages=fetch_subpages,
                    max_subpage_fetches=max_subpage_fetches,
                    built_at=built_at)

    _build_maps(maps_block, absent, maps, built_at)
    _build_registry(registry_block, absent, registry, built_at)

    return {
        "packet_version": PACKET_VERSION,
        "kind": "company",
        "entity_key": str(anchor.get("entity_key") or anchor.get("row_id") or ""),
        "built_at": built_at,
        "record": record,
        "site": site,
        "maps": maps_block,
        "registry": registry_block,
        "fetches": fetches,
        "absent": dict(absent),
    }


def _build_site(site, absent, fetches, url, page_cache, *, fetch_subpages,
                max_subpage_fetches, built_at):
    rec, html, err = _fetch(page_cache, url)
    observed = rec.get("fetched_at") or built_at
    fetches.append({"url": url, "kind": "homepage", "status": rec.get("status"),
                    "error": err or None,
                    "from_cache": bool(rec.get("from_cache"))})
    _put(site, absent, "site.domain", registrable_domain(url) or _host(url),
         source=url, observed_at=observed)
    if err or not html:
        reason = err or "the cache returned no body for this domain"
        status = absence_status(err, page_cache)
        for key in ("site.homepage_text", "site.about_text", "site.services_text",
                    "site.contact_text", "site.nav_labels", "site.og_site_name",
                    "site.schema_org_name", "site.legal_entity_footer",
                    "site.ownership_disclosure_text"):
            absent.mark(key, status, reason)
        for key in ("site.final_url", "site.redirect_terminus"):
            absent.mark(key, status, reason)
        if rec.get("status") is not None:
            _put(site, absent, "site.http_status", rec.get("status"),
                 source=url, observed_at=observed)
        else:
            absent.mark("site.http_status", status, reason)
        return

    facts = page_facts(html)
    final_url = rec.get("final_url") or url
    _put(site, absent, "site.final_url", final_url, source=url, observed_at=observed)
    if rec.get("status") is not None:
        _put(site, absent, "site.http_status", rec.get("status"), source=url,
             observed_at=observed)
    chain = rec.get("redirect_chain") or []
    if chain:
        _put(site, absent, "site.redirect_terminus", chain[-1] or final_url,
             source=url, observed_at=observed)
    else:
        absent.mark("site.redirect_terminus", "absent", "the request did not redirect")

    text = facts["text"]
    if len(text) < JUNK_PAGE_MIN_CHARS:
        absent.mark("site.homepage_text", "junk_page",
                    f"the page rendered {len(text)} characters of visible text")
    else:
        _put(site, absent, "site.homepage_text", text, source=final_url,
             observed_at=observed)
    for key, value, reason in (
        ("site.nav_labels", facts["nav_labels"], "no nav or footer links were found"),
        ("site.og_site_name", facts["og_site_name"], "the page declares no og:site_name"),
        ("site.schema_org_name", facts["schema_org_name"], "the page carries no schema.org name"),
        ("site.legal_entity_footer", facts["legal_entity_footer"],
         "the footer names no legal entity"),
        ("site.ownership_disclosure_text", " ".join(facts["ownership_sentences"]),
         "the page makes no ownership statement"),
    ):
        _put(site, absent, key, value, source=final_url, observed_at=observed,
             reason=reason)

    kinds = {"about": "site.about_text", "services": "site.services_text",
             "contact": "site.contact_text"}
    if not fetch_subpages:
        for key in kinds.values():
            absent.mark(key, "absent", "sub-page discovery was disabled for this run")
        return
    found = discover_subpages(html, final_url, limit=max_subpage_fetches)
    filled = set()
    for page in found:
        key = kinds[page["kind"]]
        if key in filled:
            continue
        prec, phtml, perr = _fetch(page_cache, page["url"])
        fetches.append({"url": page["url"], "kind": page["kind"],
                        "status": prec.get("status"), "error": perr or None,
                        "from_cache": bool(prec.get("from_cache")),
                        "label": page["label"]})
        if perr or not phtml:
            continue
        ptext = page_facts(phtml)["text"]
        if not ptext:
            continue
        _put(site, absent, key, ptext,
             source=f"{page['url']} ({page['kind']} page, linked as {page['label']!r})",
             observed_at=prec.get("fetched_at") or built_at)
        filled.add(key)
    for kind, key in kinds.items():
        if key in filled or key in site:
            continue
        tried = [f for f in found if f["kind"] == kind]
        if tried:
            absent.mark(key, absence_status(_last_error(fetches, tried[0]["url"]),
                                            page_cache),
                        f"{tried[0]['url']} returned no usable text")
        else:
            absent.mark(key, "absent", f"no {kind} link was found in the nav or footer")


def _last_error(fetches, url) -> str:
    """The error this run recorded for `url`, or "" when it never tried."""
    for rec in reversed(fetches or ()):
        if rec.get("url") == url:
            return rec.get("error") or ""
    return ""


def _build_maps(block, absent, maps, built_at):
    keys = ("maps.primary_category", "maps.secondary_categories",
            "maps.business_status", "maps.business_status_as_of",
            "maps.address", "maps.phone", "maps.rating_count")
    if not isinstance(maps, dict) or not maps:
        for key in keys:
            absent.mark(key, "absent", "no Maps record was fetched for this row")
        return
    observed = maps.get("_observed_at") or maps.get("observed_at") or built_at
    source = maps.get("_source") or maps.get("_lane") or "google-maps"
    address = ", ".join(str(maps[k]) for k in ("street_address", "city", "state", "zip")
                        if maps.get(k))
    _put(block, absent, "maps.primary_category", maps.get("category"),
         source=source, observed_at=observed, reason="the listing declares no category")
    _put(block, absent, "maps.secondary_categories", maps.get("subtypes"),
         source=source, observed_at=observed, reason="the listing declares no subtypes")
    _put(block, absent, "maps.business_status", maps.get("business_status"),
         source=source, observed_at=observed, reason="the listing carries no status")
    # Our observation date, never Google's — the status is only as true as the read.
    if maps.get("business_status"):
        _put(block, absent, "maps.business_status_as_of", observed,
             source=source, observed_at=observed)
    else:
        absent.mark("maps.business_status_as_of", "absent",
                    "there is no status to date")
    _put(block, absent, "maps.address", address, source=source,
         observed_at=observed, reason="the listing carries no address")
    _put(block, absent, "maps.phone", maps.get("phone"), source=source,
         observed_at=observed, reason="the listing carries no phone")
    rating_count = maps.get("review_count")
    if rating_count is None:
        absent.mark("maps.rating_count", "absent",
                    "this lane does not return a reliable review count")
    else:
        _put(block, absent, "maps.rating_count", rating_count, source=source,
             observed_at=observed)


def _build_registry(block, absent, registry, built_at):
    rec = _normalise_registry(registry, built_at)
    if not rec:
        for name in _REGISTRY_FIELDS:
            absent.mark(f"registry.{name}", "absent",
                        "no registry record was found for this row")
        return
    source = rec.get("source_url") or rec.get("source") or "registry"
    observed = rec.get("observed_at") or rec.get("as_of") or built_at
    for name in _REGISTRY_FIELDS:
        _put(block, absent, f"registry.{name}", rec.get(name), source=source,
             observed_at=observed,
             reason=f"the {rec.get('source', 'registry')} record carries no {name}")


# -------------------------------------------------------------- person packet

_STATUS_WORDS = ("retired", "self-employed", "self employed", "unemployed",
                 "none", "n/a", "freelance", "consultant", "sabbatical")
_PERSON_FIELDS = ("headline", "about", "current_experiences", "prior_role",
                  "location", "licenses", "open_to_work", "scrape_as_of")


def _dates_text(exp: dict) -> str:
    start = (exp.get("start_date") or exp.get("start_date_text") or "").strip()
    end = exp.get("end_date") or exp.get("end_date_text")
    end = (end or "").strip() if isinstance(end, str) else ""
    if start and end:
        return f"{start} – {end}"
    if start:
        return f"{start} – present (no end date on the profile)"
    return end


def _experience(exp: dict) -> dict:
    out = {
        "title": _clean(exp.get("title") or exp.get("position") or "")[:EXPERIENCE_CAPS["title"]],
        "company": _clean(exp.get("company") or exp.get("company_name") or "")[:EXPERIENCE_CAPS["company"]],
        "company_url": (exp.get("company_linkedin_url") or exp.get("company_url")
                        or "")[:EXPERIENCE_CAPS["company_url"]],
        "dates_text": _dates_text(exp)[:EXPERIENCE_CAPS["dates_text"]],
        # The provider's flag, under the provider's name: it is TRUE for any
        # role with a blank end date, including the owner who retired in 2023.
        "provider_current": bool(exp.get("is_current")),
    }
    company_l = out["company"].lower().strip(" .")
    if company_l in _STATUS_WORDS:
        out["company_is_status_word"] = True
    return {k: v for k, v in out.items() if v not in ("",)}


def build_person_packet(anchor, *, profile=None, company_packet=None,
                        observed_at=None) -> dict:
    """The person packet: record anchor + profile + the company identity block.

    `profile` is a canonical profile dict, fetched by the caller — this module
    imports no vendor client, and the field-sourcing table in the contract doc
    says which provider fills which field. The company's identity
    travels so "Owner at <anchor>" can be read against the company the row is
    actually about; the company's page text does not, because a person state
    that carries a homepage is no longer a person state.
    """
    anchor = dict(anchor or {})
    built_at = observed_at or now_iso()
    absent = _Absences()
    record: dict = {}
    prof_block: dict = {}
    crm_block: dict = {}

    src = anchor.get("record_source") or "record anchor"
    for name in ("name", "city", "state", "phone", "domain", "linkedin_url",
                 "title"):
        value = anchor.get(name)
        if value in (None, ""):
            continue
        _put(record, absent, f"record.{name}", value, source=src,
             observed_at=anchor.get("as_of") or built_at)

    _build_crm(crm_block, absent, anchor, src, built_at)

    if not isinstance(profile, dict) or not profile:
        for name in _PERSON_FIELDS:
            absent.mark(f"profile.{name}", "absent",
                        "no profile was fetched for this row")
    else:
        _build_profile(prof_block, absent, profile, anchor, built_at)

    identity: dict = {}
    if isinstance(company_packet, dict):
        for name, fld in (company_packet.get("record") or {}).items():
            identity[name] = fld
        for name in IDENTITY_SITE_FIELDS:
            fld = (company_packet.get("site") or {}).get(name)
            if fld is not None and name not in identity:
                identity[name] = fld
    if not identity:
        absent.mark("company_identity", "absent",
                    "no company packet was built for this row")

    return {
        "packet_version": PACKET_VERSION,
        "kind": "person",
        "entity_key": str(anchor.get("entity_key") or ""),
        "built_at": built_at,
        "record": record,
        "crm": crm_block,
        "profile": prof_block,
        "company_identity": identity,
        "absent": dict(absent),
    }


#: What `crm.persona_source` is allowed to say (the contract, § CRM fields). A
#: title that arrives on the row was supplied by whoever built the file — that
#: is `vendor_field`, and it is not `read_from_profile`.
CRM_PERSONA_SOURCES = ("read_from_profile", "read_from_site", "vendor_field",
                       "inferred", "guessed")
DEFAULT_PERSONA_SOURCE = "vendor_field"


def _build_crm(block, absent, anchor, src, built_at):
    """The row's OWN claims about the person, filed under the row's name.

    A roster or CRM title is `crm.title`, never `profile.headline`: the row
    asserts it on nobody's authority, and `crm.persona_source` is the field
    that says so — `fit_persona_source_is_a_guess` and
    `fit_title_not_backed_by_current_experience` both read it. A title that is
    read out of the input file and then dropped is the worse failure: the
    packet then says neither "Owner" nor "we were told nothing".

    `crm.title_as_of` is recorded only when the row carries one. A blank date
    must read as UNKNOWN rather than as current — a CRM title is frequently
    the title at hire, years stale.
    """
    observed = anchor.get("as_of") or built_at
    title = anchor.get("title")
    _put(block, absent, "crm.title", title, source=src, observed_at=observed,
         reason="the input row carried no title, so the row claims nothing "
                "about this person's role")
    if not (isinstance(title, str) and title.strip()):
        absent.mark("crm.title_as_of", "absent",
                    "the row carried no title, so there is no date for one")
        absent.mark("crm.persona_source", "absent",
                    "the row asserted no title, so nothing determined a "
                    "persona for this person")
        return
    _put(block, absent, "crm.title_as_of", anchor.get("title_as_of"),
         source=src, observed_at=observed,
         reason="the row carries no date for its title — a blank reads as "
                "unknown, never as current")
    supplied = str(anchor.get("persona_source") or "").strip()
    persona = supplied if supplied in CRM_PERSONA_SOURCES \
        else DEFAULT_PERSONA_SOURCE
    _put(block, absent, "crm.persona_source", persona, source=src,
         observed_at=observed)


def _build_profile(block, absent, profile, anchor, built_at):
    # Provenance, best available first. The last resort names no provider on
    # purpose: a packet that cannot say who fetched a field must not invent a
    # name for it, because the name is what a reader trusts the value on.
    source = (profile.get("source") or profile.get("_source")
              or profile.get("profile_url") or anchor.get("linkedin_url")
              or "profile provider (unnamed)")
    # WHEN WE READ IT, best available first. `retrieved_at` is the honest
    # fallback for a vendor DATABASE read: the record's own age may be unknown
    # (see `person_profiles.to_profile`), and an unknown age must not quietly
    # become "observed today" on every field in the block.
    observed = (profile.get("scrape_as_of") or profile.get("observed_at")
                or profile.get("retrieved_at") or built_at)
    _put(block, absent, "profile.headline", profile.get("headline"),
         source=source, observed_at=observed,
         reason="the profile carries no headline")
    _put(block, absent, "profile.about",
         profile.get("about") or profile.get("about_text"),
         source=source, observed_at=observed,
         reason="the provider returned no About section")
    _put(block, absent, "profile.location", profile.get("location"),
         source=source, observed_at=observed,
         reason="the profile carries no location")
    _put(block, absent, "profile.licenses",
         profile.get("licenses") or profile.get("certifications"),
         source=source, observed_at=observed,
         reason="the provider returned no licences or certifications")

    exps = [e for e in (profile.get("experiences") or []) if isinstance(e, dict)]
    current = [_experience(e) for e in exps if e.get("is_current")]
    prior = [e for e in exps if not e.get("is_current")]
    if current:
        max_items, _ = LIST_CAPS["profile.current_experiences"]
        shown = current[:max_items]
        extra = {"full_count": len(current)} if len(current) > len(shown) else {}
        block["current_experiences"] = field(
            shown, source=source, observed_at=observed,
            current_role_count=len(current), **extra)
    else:
        absent.mark("profile.current_experiences", "absent",
                    "the profile lists no role the provider calls current")
    if prior:
        p = _experience(prior[0])
        text = " — ".join(x for x in (p.get("title"), p.get("company"),
                                      p.get("dates_text")) if x)
        _put(block, absent, "profile.prior_role", text, source=source,
             observed_at=observed)
    else:
        absent.mark("profile.prior_role", "absent",
                    "the profile lists no ended role")

    otw = profile.get("open_to_work")
    if otw is None:
        absent.mark("profile.open_to_work", "absent",
                    "the provider does not return the open-to-work flag")
    else:
        _put(block, absent, "profile.open_to_work", bool(otw), source=source,
             observed_at=observed)
    # HOW OLD THE RECORD IS — the provider's claim, never ours. A vendor that
    # does not date its own record leaves this ABSENT with the reason saying
    # so; stamping our read time here is what makes a two-year-old row read as
    # today's, which is the one thing a freshness SLA cannot survive.
    as_of = profile.get("scrape_as_of")
    if as_of:
        _put(block, absent, "profile.scrape_as_of", as_of, source=source,
             observed_at=observed)
    else:
        status = str(profile.get("as_of_status") or "vendor_record_age_unknown")
        absent.mark("profile.scrape_as_of", "absent",
                    f"{status}: the provider's record carries no last-updated "
                    "date, so how old this profile is cannot be said — it was "
                    f"retrieved at {profile.get('retrieved_at') or observed}")


# ------------------------------------------------------------------ richness
#: How much evidence a packet actually carries. Three words, one rule each,
#: computed from WHICH CONTRACT FIELDS ARE PRESENT and never from a character
#: count: 12,000 characters of navigation chrome is a thin packet.
RICHNESS_TIERS = ("rich", "partial", "thin")

#: Fields that are the ROW, not evidence about it. A packet that carries only
#: these knows the name of the thing it cannot see.
_ANCHOR_FIELDS = frozenset((
    "site.domain", "site.final_url", "site.http_status",
    "site.redirect_terminus", "site.domain_registrable", "site.page_path",
    "profile.scrape_as_of", "profile.open_to_work", "maps.business_status_as_of",
    # The ROW's word about the person, not evidence about the person. A title
    # supplied by whoever built the file cannot lift a packet off the thin
    # tier — nothing about that person has been read yet.
    "crm.title", "crm.title_as_of", "crm.persona_source",
    "maps.address", "maps.phone", "maps.rating_count",
    # Fetch bookkeeping and boolean flags: they describe the RUN, not the row.
    "site.evidence_available", "site.visible_text_truncated", "site.home_status",
    "site.about_status", "site.services_status", "site.pages_fetched"))

#: The recipe for `rich`, per kind: every group must have ONE member present.
#: Alternatives inside a group are alternatives on purpose — an About page and
#: a Services page answer the same question about what a company does.
RICHNESS_REQUIREMENTS = {
    "company": (
        # `site.home_text` is the fit-test builder's name for the same page text
        # (bakeoff/fit_packets.py); `visible_text_truncated` is a FLAG, not text,
        # and must never satisfy this group.
        ("site.homepage_text", "site.visible_text", "site.home_text",
         "site.self_description_sentences", "site.matched_sentences"),
        ("site.about_text", "site.services_text"),
        ("maps.primary_category", "maps.secondary_categories",
         "registry.specialty_label", "registry.entity_type", "registry.name",
         "registry.taxonomy_code", "registry.primary_taxonomy"),
    ),
    "person": (
        ("profile.headline",),
        ("profile.about", "profile.about_text"),
        ("profile.current_experiences",),
        ("profile.location",),
    ),
}

#: A person packet is only rich when a CURRENT role carries dates text. "VP
#: Sales at Northwind" with no dates is a claim, not a record.
_PERSON_DATED_EXPERIENCE = "profile.current_experiences"


def _is_field(value) -> bool:
    return isinstance(value, dict) and "value" in value and "source" in value


def _has_value(value) -> bool:
    if _is_field(value):
        if value.get("status") in ABSENT_STATUSES:
            return False
        value = value.get("value")
    if value is None or isinstance(value, bool):
        return value is not None and value is not False or value is True
    if isinstance(value, (str, list, tuple, dict, set)):
        return len(value) > 0
    return True


_RICHNESS_SKIP_BLOCKS = frozenset((
    "absent", "fetches", "crawford", "packet_version", "kind", "entity_key",
    "built_at", "boundary", "criteria", "code_facts", "company_identity",
    # The fit-test packet shape: the question, the criteria and the row's own
    # name are not evidence about the row.
    "instructions_to_evaluator", "icp", "persona", "company", "context"))


def present_fields(packet) -> list:
    """Every contract field this packet actually carries, dotted and sorted.

    Reads BOTH shapes in production: a built packet (blocks of field objects
    with an `absent` map beside them) and a flat dotted evidence dict, which is
    what the older hooks hand the judge.
    """
    if not isinstance(packet, dict):
        return []
    absent = packet.get("absent") if isinstance(packet.get("absent"), dict) else {}
    found = set()
    for block, value in packet.items():
        if block in _RICHNESS_SKIP_BLOCKS:
            continue
        if isinstance(value, dict) and not _is_field(value):
            for name, inner in value.items():
                if _has_value(inner):
                    found.add(f"{block}.{name}")
            continue
        if _has_value(value):
            found.add(block)
    return sorted(found - set(absent))


def _kind_of_packet(packet, present) -> str:
    kind = str((packet or {}).get("kind") or "").strip().lower()
    if kind in ("company", "person"):
        return kind
    return "person" if any(f.startswith("profile.") for f in present) else "company"


def _dated_current_experience(packet) -> bool:
    block = (packet or {}).get("profile")
    field_obj = block.get("current_experiences") if isinstance(block, dict) else None
    if field_obj is None:
        field_obj = (packet or {}).get("profile.current_experiences")
    value = field_obj.get("value") if _is_field(field_obj) else field_obj
    for exp in value or ():
        if isinstance(exp, dict) and str(exp.get("dates_text") or "").strip():
            return True
    return False


def richness(packet) -> dict:
    """-> `{tier, present_fields, missing_required, kind}`.

    `rich`    every requirement group for this kind has a member present
              (company: page text + About-or-Services + a category-or-registry
              record; person: headline + About + a dated current role + location).
    `thin`    nothing of the entity's own to read: the anchor and at most ONE
              evidence field, or — for a company — no page text at all (a Maps
              category or registry record standing alone is a third party's
              word, not the company's), or — for a person — neither a headline
              nor an About.
    `partial` everything in between: enough to ask, not enough to settle.

    Never raises. A caller that hands in None gets `thin`, because no packet is
    the thinnest packet there is.
    """
    present = present_fields(packet)
    kind = _kind_of_packet(packet if isinstance(packet, dict) else {}, present)
    groups = RICHNESS_REQUIREMENTS[kind]
    have = set(present)
    missing = []
    for group in groups:
        ok = any(f in have for f in group)
        if ok and group == (_PERSON_DATED_EXPERIENCE,):
            ok = _dated_current_experience(packet)
        if not ok:
            missing.append(" or ".join(group))
    evidence = [f for f in present
                if f not in _ANCHOR_FIELDS and not f.startswith("record.")]
    # The doctrine's thin tier is "no usable page text at all" — the entity's
    # own words are the first leg of every rich packet, and without them a
    # third-party record is not a packet a judge can settle.
    own_words_missing = (
        not any(f in have for f in groups[0]) if kind == "company"
        else not (any(f in have for f in groups[0]) or any(f in have for f in groups[1])))
    if len(evidence) <= 1 or own_words_missing:
        tier = "thin"
    elif missing:
        tier = "partial"
    else:
        tier = "rich"
    return {"tier": tier, "kind": kind, "present_fields": present,
            "missing_required": missing}


# ------------------------------------------------------------- the enricher
def default_enricher(*, page_cache=None, maps=None, registry=None, profile=None,
                     company_packet=None, fetch_subpages=True,
                     max_subpage_fetches=MAX_SUBPAGE_FETCHES):
    """A `judge(enrich=...)` callable that rebuilds the packet from the caches.

    Everything it needs arrives here as an argument — the page cache, the Maps
    record, the registry payload, the profile — so the enricher spends exactly
    what its caller already decided to spend and NOTHING on its own. Handed no
    cache at all it returns None, which the judge reads as "nothing more was
    found" rather than as an error.

        enrich = default_enricher(page_cache=cache, maps=rec)
        judge("icp_fit", evidence, icp=icp, enrich=enrich)
    """
    def enrich(evidence, decision=None):
        anchor = _anchor_from(evidence)
        if not anchor:
            return None
        if str(decision or "").strip() == "persona_fit" or profile is not None:
            company = company_packet
            if company is None and page_cache is not None:
                company = build_company_packet(
                    anchor, page_cache=page_cache, maps=maps, registry=registry,
                    fetch_subpages=fetch_subpages,
                    max_subpage_fetches=max_subpage_fetches)
            return build_person_packet(anchor, profile=profile,
                                       company_packet=company)
        if page_cache is None and maps is None and registry is None:
            return None
        return build_company_packet(anchor, page_cache=page_cache, maps=maps,
                                    registry=registry,
                                    fetch_subpages=fetch_subpages,
                                    max_subpage_fetches=max_subpage_fetches)
    return enrich


def _anchor_from(evidence) -> dict:
    """The row the evidence is about — whatever shape the caller used."""
    if not isinstance(evidence, dict):
        return {}
    anchor = evidence.get("anchor")
    if isinstance(anchor, dict) and anchor:
        return dict(anchor)
    out = {}
    record = evidence.get("record")
    if isinstance(record, dict):
        for name, value in record.items():
            out[name] = value.get("value") if _is_field(value) else value
    site = evidence.get("site")
    if isinstance(site, dict):
        for name in ("domain", "domain_registrable", "final_url"):
            value = site.get(name)
            value = value.get("value") if _is_field(value) else value
            if value and "domain" not in out:
                out["domain"] = value
    for dotted in ("record.name", "record.domain", "site.domain",
                   "site.domain_registrable"):
        value = evidence.get(dotted)
        if value:
            out.setdefault(dotted.split(".", 1)[1], value)
    if evidence.get("entity_key"):
        out["entity_key"] = evidence["entity_key"]
    return out


# ------------------------------------------------------------- the packet card
#: What each tier MEANS, in the words the richness report already uses. One
#: sentence, one place — the report and the card must not drift.
TIER_MEANING = {
    "rich": "every requirement group has a member — enough to settle",
    "partial": "enough to ask, not enough to settle",
    "thin": "nothing of the entity's own to read",
}

#: Plain English for a contract field name. The card prints the label and keeps
#: the raw name in brackets, because the operator reads the label and the agent
#: greps the name. A field with no entry here still gets a line — `_label()`
#: builds one from the name rather than hiding the field.
FIELD_LABELS = {
    # --- the row itself, as the operator handed it over
    "record.name": "Name on the row",
    "record.city": "City on the row",
    "record.state": "State on the row",
    "record.domain": "Website on the row",
    "record.linkedin_url": "LinkedIn URL on the row",
    "record.title": "Job title on the row",
    # --- what the row CLAIMS about a person (nobody's authority but the file's)
    "crm.title": "Title the file gave us",
    "crm.title_as_of": "Date on that title",
    "crm.persona_source": "Where that title came from",
    "crm.is_generic_inbox": "Is the address a shared inbox",
    "crm.person_name": "Name on the row",
    # --- the company's own site
    "site.domain": "Website (registrable domain)",
    "site.domain_registrable": "Website (registrable domain)",
    "site.final_url": "Page we actually landed on",
    "site.http_status": "What the site answered (HTTP status)",
    "site.redirect_terminus": "Where the redirects ended",
    "site.homepage_text": "Own description (homepage)",
    "site.visible_text": "Own description (homepage)",
    "site.home_text": "Own description (homepage)",
    "site.self_description_sentences": "Own description (homepage/about)",
    "site.matched_sentences": "Sentences that matched the operator's wording",
    "site.negation_sentences": "Sentences saying what they do NOT do",
    "site.about_text": "About page",
    "site.services_text": "Services page",
    "site.contact_text": "Contact page",
    "site.nav_labels": "Menu labels on the site",
    "site.og_site_name": "Site name the page declares",
    "site.schema_org_name": "Name in the page's own structured data",
    "site.legal_entity_footer": "Legal entity named in the footer",
    "site.ownership_disclosure_text": "What the site says about who owns it",
    "site.page_path": "Which page the quote came from",
    "site.location_count": "How many locations the site lists",
    "site.injection_flags": "Text aimed at an evaluator (flagged)",
    "site.evidence_available": "Was any page text available",
    "site.visible_text_truncated": "Was the page text cut at the cap",
    "site.provider_credentials": "Credentials the site lists",
    "site.provider_roster": "People the site lists",
    # --- Google Maps: labels the OWNER chose, never a verdict
    "maps.primary_category": "Google Maps main category (the owner chose it)",
    "maps.secondary_categories": "Google Maps other categories (owner-chosen)",
    "maps.business_status": "Google Maps open/closed status",
    "maps.business_status_as_of": "The date WE read that status",
    "maps.address": "Address on the Maps listing",
    "maps.rating_count": "How many Google reviews",
    # --- an independent record
    "registry.source": "Which registry the record came from",
    "registry.status": "Registry record status",
    "registry.entity_type": "Registry entity type (a person or an organisation)",
    "registry.taxonomy_code": "Registry specialty code",
    "registry.taxonomy_desc": "Registry specialty label",
    "registry.specialty_label": "Registry specialty label",
    "registry.specialty_code": "Registry specialty code",
    "registry.parent_org_lbn": "Parent organisation on the registry record",
    "registry.as_of": "Date on the registry record",
    "registry.name": "Name on the registry record",
    "registry.primary_taxonomy": "Registry specialty code",
    # --- the person
    "profile.person_name": "Name on the profile",
    "profile.headline": "Profile headline (their own words)",
    "profile.about": "About section (their own words)",
    "profile.about_text": "About section (their own words)",
    "profile.current_experiences": "Current roles, with dates",
    "profile.prior_role": "The most recent role that ended",
    "profile.location": "Location on the profile",
    "profile.licenses": "Licences and certifications",
    "profile.open_to_work": "Open-to-work flag",
    "profile.scrape_as_of": "How old the profile record is",
    "profile.negation_sentences": "Sentences saying what they do NOT do",
    "profile.injection_flags": "Text aimed at an evaluator (flagged)",
    "profile.current_experiences[].company_is_status_word":
        "Is the employer a status word (Retired, Stealth) rather than a company",
    "profile.current_experiences[].company_matches_anchor":
        "Is that role at the company on the row",
    "profile.current_experiences[].is_open_ended":
        "Does that role have no end date",
    "profile.current_experiences[].is_operating_role":
        "Is that role an operating one rather than advisory",
    "profile.current_experiences[].description": "What that role's entry says",
    # --- firmographics and entity type
    "firmo.entity_level": "Is this the parent or a branch",
    "firmo.industry_by_source": "Industry, per source",
    "firmo.headcount": "Headcount (vendor's number)",
    "firmo.parent_name": "Parent company (vendor's record)",
    "entity.type_candidates": "What kind of entity this looks like",
    "entity.type_evidence_line": "The line that says what kind of entity it is",
    "company.name": "Employer on the row",
    "company_identity": "The company this person is matched to",
    # --- the operator's rules, which travel INSIDE the packet
    "icp.inclusions": "What counts as in (the operator's list)",
    "icp.exclusions": "What counts as out (the operator's list)",
    "icp.territories": "Territories the operator named",
    "icp.competitor_names": "Competitors the operator named",
    "icp.persona_titles": "The titles the operator is looking for",
    "icp.criteria_measured": "ICP criteria this packet has evidence for",
    "icp.criteria_unmeasured": "ICP criteria this packet CANNOT see",
    "icp.as_of": "Date the rules were written",
}

#: Never printed, at any width, on any card. A packet card is pasted into
#: Slack, a ticket and a Sheet; a phone number or an email address on it is a
#: contact detail leaving the machine for no decision anybody is making.
_NEVER_PRINT = ("phone", "email", "mobile", "e_mail", "telephone")

#: What a scrubbed contact reads as. One string everywhere, so a reader who
#: meets it twice knows it is the scrubber and not a coincidence.
CONTACT_WITHHELD = "[contact withheld]"

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
#: A run of digits carrying phone punctuation. BOTH the digit count and the
#: punctuation are required: "any ten digits" eats an NPI, a licence number and
#: an ISO date, and a card that redacts its own observation date is a card
#: whose freshness nobody can check.
_PHONE_RE = re.compile(r"(?<![\w@.])\(?\+?\d[\d\u00a0 ().\-]{6,18}\d(?![\w@])")
_PHONE_PUNCT_RE = re.compile(r"[ ().\-\u00a0]")


def _looks_like_phone(text: str) -> bool:
    digits = re.sub(r"\D", "", str(text))
    if not 10 <= len(digits) <= 15:
        return False
    # A bare run of digits with no punctuation is an identifier far more often
    # than it is a phone number. A leading `+` is the one exception: nothing
    # but a phone number is written `+1 555 ...`.
    stripped = str(text).lstrip("(").lstrip()
    return stripped.startswith("+") or bool(_PHONE_PUNCT_RE.search(str(text)))


def _scrub_contacts(text) -> str:
    """Phone numbers and email addresses out of ANY string a card prints.

    The card already holds back the contact FIELDS by name. This is the second
    half of the same promise, for the contact somebody typed into an About
    section, a role description, a footer or a review reason — which is where
    they actually are. Every rendered string on a card goes through here.
    """
    out = _EMAIL_RE.sub(CONTACT_WITHHELD, str(text))
    return _PHONE_RE.sub(
        lambda m: CONTACT_WITHHELD if _looks_like_phone(m.group(0))
        else m.group(0), out)


#: The ONLY suffix matches a question's `requires` may be satisfied by. A bare
#: suffix rule reads `anchor.name` as `candidate.name` — evidence about one
#: entity counted as evidence about another, which is the whole failure the
#: packet contract exists to prevent. So the alternatives are named here, one
#: by one, and a match through this table is LABELLED on the card.
FIELD_ALIASES = {
    "site.about": ("site.about_text",),
    "site.about_text": ("site.about",),
    "site.home": ("site.homepage_text", "site.home_text", "site.visible_text"),
    "site.homepage": ("site.homepage_text", "site.home_text",
                      "site.visible_text"),
    "site.homepage_text": ("site.home_text", "site.visible_text"),
    "site.home_text": ("site.homepage_text", "site.visible_text"),
    "site.visible_text": ("site.homepage_text", "site.home_text"),
    "site.services": ("site.services_text",),
    "site.contact": ("site.contact_text",),
    "profile.about": ("profile.about_text",),
    "profile.about_text": ("profile.about",),
    "registry.taxonomy_desc": ("registry.specialty_label",),
    "registry.specialty_label": ("registry.taxonomy_desc",),
    "registry.taxonomy_code": ("registry.specialty_code",),
    "registry.specialty_code": ("registry.taxonomy_code",),
    "registry.primary_taxonomy": ("registry.taxonomy_code",
                                  "registry.specialty_code"),
}

#: The fields whose entries are EXPERIENCE records, and the only ones rendered
#: through `_experience_line`. Every other dict on a card serialises faithfully
#: — an inclusion object that reads `"(untitled)" at (no employer)` is an
#: employment record the operator never wrote.
EXPERIENCE_FIELDS = frozenset((
    "profile.current_experiences", "profile.past_experiences",
    "profile.experiences", "profile.prior_roles", "profile.roles",
    "current_experiences", "past_experiences", "experiences",
))

#: Fields that describe the RUN rather than the row. They are still listed —
#: an operator asking "what does the judge see" deserves the whole list — but
#: after the evidence, and without an excerpt.
_CARD_BOOKKEEPING = frozenset((
    "site.final_url", "site.http_status", "site.redirect_terminus",
    "site.evidence_available", "site.visible_text_truncated",
    "site.home_status", "site.about_status", "site.services_status",
    "site.pages_fetched", "maps.business_status_as_of", "profile.scrape_as_of",
    "registry.as_of", "registry.source", "registry.status"))

#: The order a READER needs, which is not the order a judge is given. The
#: contract orders a packet cheap-label-first so a literal reader meets the
#: untrustworthy category before the trustworthy sentence; a human scanning a
#: card needs the entity's OWN WORDS first, because that is what tells them in
#: two seconds whether the packet is about the right company at all. The cap
#: below cuts the tail, so the tail must be the part nobody reads twice.
_CARD_ORDER = (
    "record.name", "profile.person_name", "company.name",
    "profile.headline", "profile.about", "profile.about_text",
    "profile.current_experiences", "profile.prior_role", "profile.location",
    "site.homepage_text", "site.visible_text", "site.home_text",
    "site.self_description_sentences", "site.matched_sentences",
    "site.about_text", "site.services_text",
    "site.ownership_disclosure_text", "site.negation_sentences",
    "profile.negation_sentences",
    "maps.primary_category", "maps.secondary_categories",
    "maps.business_status",
    "registry.taxonomy_desc", "registry.specialty_label",
    "registry.entity_type", "registry.taxonomy_code",
    "registry.parent_org_lbn", "registry.name",
    "record.title", "crm.title", "crm.title_as_of", "crm.persona_source",
    "profile.licenses", "profile.open_to_work",
    "site.contact_text", "site.legal_entity_footer",
    "site.og_site_name", "site.schema_org_name", "site.nav_labels",
    "site.injection_flags", "profile.injection_flags",
    "entity.type_evidence_line", "entity.type_candidates",
    "firmo.entity_level", "firmo.industry_by_source",
    "record.city", "record.state", "record.domain", "record.linkedin_url",
    "site.domain", "site.domain_registrable", "maps.address",
    "maps.rating_count",
)

#: Caps, so a card stays one screen. Every cap that bites says how many it hid.
CARD_EXCERPT_CHARS = 160
CARD_ANSWER_EXCERPT_CHARS = 120
_MAX_CARD_FIELDS = 10
_MAX_CARD_MISSING = 8
_MAX_CARD_QUESTIONS = 8
_MAX_CARD_NOT_ASKED = 6
_MAX_EXPERIENCE_LINES = 3
#: A generic dict's keys, and a list-of-dicts' rows, both cap here.
_MAX_DICT_KEYS = 8


def _label(dotted) -> str:
    """The plain label for a field name — from the table, or built from it."""
    name = str(dotted or "")
    known = FIELD_LABELS.get(name)
    if known:
        return known
    tail = name.split(".")[-1].replace("[]", "").replace("_", " ").strip()
    block = name.split(".")[0] if "." in name else ""
    tail = tail[:1].upper() + tail[1:] if tail else name
    return f"{tail} ({block})" if block else tail


def _is_private(dotted) -> bool:
    low = str(dotted or "").lower()
    return any(marker in low for marker in _NEVER_PRINT)


def _unwrap(value):
    """`(value, source, observed_at, status)` from a field object or a raw one."""
    if _is_field(value):
        return (value.get("value"), str(value.get("source") or ""),
                str(value.get("observed_at") or ""),
                str(value.get("status") or "present"))
    return value, "", "", "present"


def _when(observed) -> str:
    """The DATE a value was observed. A full ISO timestamp on every line is
    noise; the day is what an operator checks a packet's freshness against."""
    text = str(observed or "").strip()
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:10]
    return text[:24]


def _short_source(source) -> str:
    """A lane name a human reads, not a 300-character URL."""
    text = str(source or "").strip()
    if not text:
        return ""
    if "://" in text:
        host = _host(text)
        rest = text.split(" ", 1)[1] if " " in text else ""
        return _scrub_contacts(f"{host} {rest}".strip() if rest else host)
    return _scrub_contacts(text[:60])


def _is_experience_field(dotted) -> bool:
    """Is this field an EXPERIENCE list — the only dicts read as employment?"""
    name = str(dotted or "")
    return name in EXPERIENCE_FIELDS or name.split(".")[-1] in EXPERIENCE_FIELDS


def _scalar(value) -> str:
    """One value, as itself. Never reshaped into a record it is not."""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, dict):
        return "{" + _pairs(value) + "}"
    if isinstance(value, (list, tuple)):
        return "; ".join(_scalar(v) for v in value)
    return str(value if value is not None else "")


def _pairs(obj, cap_keys=_MAX_DICT_KEYS) -> str:
    """A dict, faithfully: `key: value`, sorted for determinism, capped."""
    keys = sorted(str(k) for k in obj)
    text = ", ".join(f"{k}: {_scalar(obj[k])}" for k in keys[:cap_keys])
    if len(keys) > cap_keys:
        text += f", … and {len(keys) - cap_keys} more key(s)"
    return text


def _excerpt(value, cap=CARD_EXCERPT_CHARS, *, experience=False) -> str:
    """The first ~`cap` characters of a value, cut at a sentence boundary.

    `experience=True` is the ONLY way a dict is rendered as an employment
    record. Every other dict serialises its own keys and values, because an
    inclusion object printed as `"(untitled)" at (no employer)` is a job
    history the operator never wrote.
    """
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (list, tuple)):
        parts = []
        for item in value:
            if isinstance(item, dict):
                parts.append(_experience_line(item) if experience
                             else _pairs(item))
            else:
                parts.append(str(item))
        value = "; ".join(p for p in parts if p)
    if isinstance(value, dict):
        value = _experience_line(value) if experience else _pairs(value)
    text = _clean(str(value if value is not None else "")).replace("\n", " ")
    out = _truncate_at_sentence(text, cap)
    return _scrub_contacts(out + (" …" if len(text) > len(out) else ""))


def _quoted(text) -> str:
    """Quote an excerpt once. A value that already opens with a quotation mark
    (an experience line, a headline someone wrapped) is not re-quoted."""
    text = str(text)
    return text if text.startswith(('"', "'", "- ")) else '"' + text + '"'


def _experience_line(exp) -> str:
    if not isinstance(exp, dict):
        return str(exp)
    title = str(exp.get("title") or "(untitled)")
    company = str(exp.get("company") or exp.get("company_name") or "(no employer)")
    dates = str(exp.get("dates_text") or exp.get("start_date_text") or "")
    flags = []
    if exp.get("company_is_status_word"):
        flags.append("the employer is a status word, not a company")
    if exp.get("company_matches_anchor"):
        flags.append("at the company on the row")
    if exp.get("provider_current"):
        flags.append("the provider calls it current")
    line = f'"{title}" at {company}'
    if dates:
        line += f" — {dates}"
    if flags:
        line += " (" + "; ".join(flags) + ")"
    return _scrub_contacts(line)


def _wrap(text, width, indent, hang=None) -> list:
    """Deterministic wrapping. No textwrap heuristics, no locale, no surprises.

    `indent` prefixes the FIRST line; `hang` (default: `indent` plus two
    spaces) prefixes every continuation, so a wrapped label never reads as a
    new field.
    """
    words = str(text).split()
    if not words:
        return []
    hang = indent + "  " if hang is None else hang
    lines, cur, pad = [], indent, indent
    for word in words:
        candidate = cur + word if cur == pad else cur + " " + word
        if len(candidate) > max(width, len(pad) + 8) and cur != pad:
            lines.append(cur)
            pad = hang
            cur = pad + word
        else:
            cur = candidate
    lines.append(cur)
    return lines


def _res(result, key, default=None):
    """One field off a JudgeResult, its `as_dict()`, or a decision-log row."""
    if result is None:
        return default
    if isinstance(result, dict):
        value = result.get(key, default)
    else:
        value = getattr(result, key, default)
    return default if value is None else value


def _probability(answer):
    """`(label, p)` for one answered question, or None when it carries none."""
    if not isinstance(answer, dict):
        return None
    p = answer.get("p", answer.get("probability"))
    if isinstance(p, (int, float)) and not isinstance(p, bool):
        return "", float(p)
    probs = answer.get("probs") or answer.get("probabilities")
    if isinstance(probs, dict) and probs:
        pairs = [(str(k), float(v)) for k, v in probs.items()
                 if isinstance(v, (int, float)) and not isinstance(v, bool)]
        if pairs:
            label, value = max(pairs, key=lambda kv: (kv[1], kv[0]))
            return label, value
    return None


def _field_map(packet) -> dict:
    """`{dotted name: the field object or raw value}` for everything present."""
    out = {}
    if not isinstance(packet, dict):
        return out
    for block, value in packet.items():
        if block in _RICHNESS_SKIP_BLOCKS:
            continue
        if isinstance(value, dict) and not _is_field(value):
            for name, inner in value.items():
                if _has_value(inner):
                    out[f"{block}.{name}"] = inner
            continue
        if _has_value(value):
            out[block] = value
    # the two fit packet shapes put the evidence one level down
    inner = packet.get("evidence")
    if isinstance(inner, dict):
        for name, value in inner.items():
            if isinstance(value, dict) and not _is_field(value):
                for leaf, deep in value.items():
                    if _has_value(deep):
                        out.setdefault(f"{name}.{leaf}", deep)
            elif _has_value(value):
                out.setdefault(name, value)
    return out


def _read_labels(here, gone, unknown, aliased=None) -> list:
    """The plain labels a question reads, deduped and in order.

    Two contract names can share one label on purpose (`profile.about` and
    `profile.about_text` are the same section under two providers' names), and
    printing it twice reads as two pieces of evidence where there is one.

    A name satisfied through `FIELD_ALIASES` rather than by its own path says
    so — the operator is reading a claim about which field answered a question,
    and "near enough" is not a field name.
    """
    aliased = aliased or {}
    out, seen = [], set()
    for name in list(here or gone):
        text = _label(name)
        matched = aliased.get(name)
        if matched:
            text += f" (matched by alias: {matched})"
        if text not in seen:
            seen.add(text)
            out.append(text)
    for text in list(unknown):
        if text not in seen:
            seen.add(text)
            out.append(text)
    return out


def _resolve_requires(names, present) -> tuple:
    """`(present, absent, unknown, aliased)` for a question's `requires`.

    A `requires` token is either a packet FIELD name (it carries a dot) or a
    battery FACT key (`has_page_text`). A fact key cannot be checked against a
    packet, so it is reported as unknown rather than guessed absent — calling
    a question "not asked" when it was is worse than saying we cannot tell.

    The full path is matched FIRST and on its own. A bare suffix match reads
    `anchor.name` as `candidate.name` — one
    entity's evidence counted as another's, which is the exact failure the
    packet contract exists to prevent. The only other way a token can be
    satisfied is `FIELD_ALIASES`, which names every alternative explicitly and
    is reported in `aliased` so the card can label it.
    """
    here, gone, unknown, aliased = [], [], [], {}
    have = set(present)
    for name in names or ():
        text = str(name)
        if "." not in text:
            unknown.append(text)
            continue
        bare = text.replace("[]", "")
        if text in have or bare in have:
            here.append(text)
            continue
        matched = next((alt for alt in FIELD_ALIASES.get(bare, ())
                        if alt in have), "")
        if matched:
            here.append(text)
            aliased[text] = matched
        else:
            gone.append(text)
    return tuple(here), tuple(gone), tuple(unknown), aliased


def _rule_value_lines(prefix, label, key, value) -> list:
    """One rule, verbatim. A structured rule keeps its own keys and values.

    A list of objects gets one object per line — `_experience_line` is never
    reached from here, because an inclusion object is not a job.
    """
    if isinstance(value, dict):
        rows = [f"{k}: {_scalar(value[k])}" for k in sorted(str(x) for x in value)]
    elif (isinstance(value, (list, tuple)) and value
          and any(isinstance(v, dict) for v in value)):
        rows = [_pairs(v) if isinstance(v, dict) else _scalar(v) for v in value]
    else:
        return [f"{prefix}{label} [{key}]: {_excerpt(value, 300)}"]
    out = [f"{prefix}{label} [{key}]:"]
    for row in rows[:_MAX_DICT_KEYS]:
        out.append(f"{prefix}  - {_scrub_contacts(row)}")
    if len(rows) > _MAX_DICT_KEYS:
        out.append(f"{prefix}  - … and {len(rows) - _MAX_DICT_KEYS} more")
    return out


def _rule_lines(criteria, prefix="  ") -> list:
    """The operator's rules, verbatim and short. Never paraphrased."""
    lines = []
    if not isinstance(criteria, dict) or not criteria:
        return lines
    blocks = [criteria]
    for key in ("icp", "persona", "criteria"):
        inner = criteria.get(key)
        if isinstance(inner, dict):
            blocks.append(inner)
    seen = set()
    named = (("inclusions", "In (named by the operator)"),
             ("exclusions", "Out (named by the operator)"),
             ("territories", "Territories"),
             ("competitor_names", "Competitors named"),
             ("persona_titles", "Titles wanted"),
             ("what", "What this seat decides"),
             ("not_for", "The seats this is NOT for"),
             ("examples", "Example titles from the client's own customers"),
             ("weights", "What matters most, in order"),
             ("rubric", "The rubric, verbatim"))
    for key, label in named:
        for block in blocks:
            value = block.get(key)
            if key in seen or value in (None, "", [], {}):
                continue
            seen.add(key)
            lines += _rule_value_lines(prefix, label, key, value)
    for block in blocks:
        for key, value in sorted(block.items()):
            if key in seen or key.startswith("_") or value in (None, "", [], {}):
                continue
            if isinstance(value, dict):
                continue
            seen.add(key)
            lines += _rule_value_lines(prefix, _label(key), key, value)
    return lines


#: The two decisions whose COMPOSER requires an exclusion list. CO-RULE-01 is
#: theirs and nobody else's: a rubric-only rule block (title-sweep's band
#: preview) produces a numeric band, and telling it its fit can only be MAYBE
#: is a warning about a verdict that stage never reaches.
FIT_DECISIONS = ("icp_fit", "persona_fit")


def explain_rules(criteria, decision=None) -> str:
    """The rule block a judge is given, as the judge is given it.

    An exclusion that lives in a rep's head is unenforceable (CO-RULE-01), and
    an EMPTY exclusion list is not a clean screen — under the contract's
    composition rule it means fit can only ever come back MAYBE. So the empty
    case says that out loud rather than printing nothing.

    `decision` is the decision these rules will be composed under, and the
    CO-RULE-01 warning prints ONLY for `icp_fit` and `persona_fit`. The default, `None`, prints no warning: a caller
    that has not said which composer will read the block has not earned a
    claim about what that composer can return.
    """
    try:
        fit = str(decision or "") in FIT_DECISIONS
        lines = _rule_lines(criteria)
        if not lines:
            text = ("Rules the judge is given (as data)\n"
                    "  none supplied — with no inclusion or exclusion list the "
                    "judge is reading evidence against nothing")
            text += (", and under the contract's composition rule fit can only "
                     "be MAYBE." if fit else ".")
            return _scrub_contacts(text)
        out = ["Rules the judge is given (as data)"] + lines
        if fit:
            exclusions = None
            if isinstance(criteria, dict):
                exclusions = criteria.get("exclusions")
                if exclusions in (None, "", [], {}):
                    inner = criteria.get("icp")
                    exclusions = (inner.get("exclusions")
                                  if isinstance(inner, dict) else None)
            if exclusions in (None, "", [], {}):
                out.append("  no exclusion list was supplied — under the "
                           "contract's composition rule fit can only be MAYBE "
                           "(CO-RULE-01).")
        return _scrub_contacts("\n".join(out))
    except Exception as exc:                                # noqa: BLE001
        return f"Rules the judge is given (as data)\n  unreadable ({type(exc).__name__})"


def explain(packet, *, rules=None, questions=None, result=None, width=100,
            decision=None) -> str:
    """One screen of plain English: what the judge reads, and what it cannot.

    This is the PACKET CARD. It is shown to the operator BEFORE any paid pass
    (`build_packets.py --preview`) and beside every row a human is asked to
    settle, so nobody is ever asked to trust a verdict without seeing the
    evidence it was reached on — and so an empty packet looks empty instead of
    looking like a confident answer.

        print(explain(packet, rules=icp, questions=battery["questions"],
                      result=judge_result))

    `packet` may be a built packet, a flat evidence dict, or None — with None
    and a `result` the card still renders from the result's own richness block,
    because the decision log stores the hash of the evidence, not the evidence.
    `questions` are battery dicts (`{id, text, requires}`); `result` is a
    `JudgeResult`, its `as_dict()`, or a decision-log row. `rules=None` leaves
    the rule block off this card (the caller is printing it elsewhere, or does
    not have it); `rules={}` prints it and says it is empty, which is a finding.
    Never raises: a malformed packet renders a card that says so.
    """
    try:
        return _scrub_contacts(
            _explain(packet, rules=rules, questions=questions, result=result,
                     width=int(width or 100), decision=decision))
    except Exception as exc:                                # noqa: BLE001
        return ("PACKET CARD — this packet could not be read\n"
                f"  {type(exc).__name__}: {str(exc)[:200]}\n"
                "  Treat it as THIN: nothing here should be judged until the "
                "builder is fixed.")


def _explain(packet, *, rules, questions, result, width, decision=None) -> str:
    pkt = packet if isinstance(packet, dict) else {}
    tier = richness(pkt) if pkt else None
    if tier is None or (not pkt and result is not None):
        from_result = _res(result, "packet_richness", {})
        if isinstance(from_result, dict) and from_result.get("tier"):
            tier = dict(from_result)
    if not isinstance(tier, dict) or not tier.get("tier"):
        tier = richness(pkt)
    kind = str(tier.get("kind") or pkt.get("kind") or "company")
    fields = _field_map(pkt)
    present = sorted(fields)
    if not present:
        listed = _res(result, "packet_richness", {})
        present = list((listed or {}).get("present_fields") or ())

    name = ""
    for key in ("record.name", "profile.person_name", "company.name",
                "crm.person_name", "registry.name"):
        if key in fields:
            name = str(_unwrap(fields[key])[0] or "")
            break
    if not name:
        name = str(pkt.get("entity_key") or _res(result, "entity_key", "") or
                   "(this row carries no name)")
    name = _scrub_contacts(name)

    out = [f"PACKET CARD — {name} ({kind}) — {tier['tier'].upper()}",
           f"  {tier['tier'].upper()}: "
           f"{TIER_MEANING.get(tier['tier'], 'unknown tier')}."]

    # --- 2. what the judge will read ---------------------------------------
    out.append("")
    out.append("What the judge will read")
    if not present:
        out.append("  nothing — " + ("no packet was supplied at all"
                                     if not isinstance(packet, dict)
                                     else "this packet carries no readable field")
                   + ". A judge asked to answer from it is guessing.")
    elif not fields:
        out += _wrap("the packet itself is not on this row (the decision log "
                     "keeps the evidence hash, not the evidence). The fields "
                     "it carried were:", width, "  ")
        out += _wrap("; ".join(f"{_label(f)} [{f}]" for f in present
                               if not _is_private(f)), width, "    ")
    else:
        order = {name: i for i, name in enumerate(_CARD_ORDER)}
        shown = [f for f in present if not _is_private(f)]
        hidden_private = len(present) - len(shown)
        shown.sort(key=lambda f: (f in _CARD_BOOKKEEPING,
                                  order.get(f, len(_CARD_ORDER)), f))
        for dotted in shown[:_MAX_CARD_FIELDS]:
            out += _field_lines(dotted, fields[dotted], width)
        extra = shown[_MAX_CARD_FIELDS:]
        if extra:
            out += _wrap("also present, unquoted: " + ", ".join(extra),
                         width, "  ")
        if hidden_private:
            out.append(f"  ({hidden_private} contact field(s) held back — a card "
                       "never prints a phone number or an email address.)")

    # --- 3. missing, and why ------------------------------------------------
    out.append("")
    out += _missing_lines(pkt, tier, present, width, log_only=not pkt)

    # --- 4. the rules -------------------------------------------------------
    # `rules=None` means the caller is not showing rules on this card (the
    # review queue's log rows do not carry them, and a preview prints the block
    # once above the cards). `rules={}` means the caller HAS the rule block and
    # it is empty, which is a finding and says so. The two are not the same
    # sentence and must not print the same one.
    if rules is not None:
        out.append("")
        out.append(explain_rules(rules, decision))

    # --- 5. the questions ---------------------------------------------------
    if questions:
        out.append("")
        out += _question_lines(questions, present, result, width, kind=kind,
                               absent=pkt.get("absent")
                               if isinstance(pkt.get("absent"), dict) else None)

    # --- 6. the answer ------------------------------------------------------
    if result is not None:
        out.append("")
        out += _answer_lines(result, questions, fields, present, tier, width)
    return "\n".join(out)


def _field_lines(dotted, value, width) -> list:
    raw, source, observed, status = _unwrap(value)
    head = f"  {_label(dotted)} [{dotted}]"
    stamp = " · ".join(x for x in (_short_source(source), _when(observed)) if x)
    if stamp:
        head += f" · {stamp}"
    if status and status != "present":
        head += f" · {status}"
    lines = _wrap(head.strip(), width, "  ")
    if dotted in _CARD_BOOKKEEPING and not isinstance(raw, (list, tuple)):
        lines[-1] += f" = {_excerpt(raw, 60)}"
        return lines
    experience = _is_experience_field(dotted)
    if isinstance(raw, (list, tuple)) and raw and any(isinstance(v, dict)
                                                      for v in raw):
        # Only an EXPERIENCE field is read as employment. Any other list of
        # objects keeps its own keys and values, one object per line — an
        # inclusion rule rendered as `"(untitled)" at (no employer)` is a job
        # nobody worked.
        for item in list(raw)[:_MAX_EXPERIENCE_LINES]:
            body = (_experience_line(item) if experience and isinstance(item, dict)
                    else _pairs(item) if isinstance(item, dict)
                    else _scalar(item))
            lines += _wrap("- " + _scrub_contacts(body), width, "      ",
                           hang="        ")
        if len(raw) > _MAX_EXPERIENCE_LINES:
            lines.append(f"      - … and {len(raw) - _MAX_EXPERIENCE_LINES} more")
        return lines
    if isinstance(raw, dict) and not experience:
        keys = sorted(str(k) for k in raw)
        for key in keys[:_MAX_EXPERIENCE_LINES]:
            lines += _wrap(f"{key}: {_scrub_contacts(_scalar(raw[key]))}",
                           width, "      ", hang="        ")
        if len(keys) > _MAX_EXPERIENCE_LINES:
            lines.append(f"      … and {len(keys) - _MAX_EXPERIENCE_LINES} "
                         "more key(s)")
        return lines
    text = _excerpt(raw, experience=experience)
    if not text:
        return lines
    quote = _quoted(text)
    # A short value rides on its own label line. Two lines per field is how a
    # card for a person packet runs past one screen, and the operator scrolling
    # past the About text is the reader this whole thing exists for.
    if len(lines) == 1 and len(lines[0]) + 3 + len(quote) <= width:
        lines[0] += " — " + quote
        return lines
    lines += _wrap(quote, width, "      ", hang="      ")
    return lines


#: The fields the card reports an absence for: the contract's `required` and
#: `supporting` tiers. The optional/escalation tail is deliberately left out —
#: a card that lists forty things nobody asked for buries the two that matter.
_CARD_MISSING_FIELDS = {
    "company": (
        "site.homepage_text", "site.visible_text", "site.home_text",
        "site.self_description_sentences", "site.matched_sentences",
        "site.about_text", "site.services_text", "site.contact_text",
        "site.negation_sentences", "site.ownership_disclosure_text",
        "site.legal_entity_footer", "site.og_site_name",
        "site.schema_org_name", "site.nav_labels",
        "maps.primary_category", "maps.secondary_categories",
        "maps.business_status", "registry.entity_type",
        "registry.taxonomy_desc", "registry.specialty_label",
        "registry.status", "entity.type_evidence_line",
        "firmo.entity_level", "firmo.industry_by_source"),
    "person": (
        "profile.headline", "profile.about", "profile.about_text",
        "profile.current_experiences", "profile.prior_role",
        "profile.location", "profile.licenses", "profile.negation_sentences",
        "profile.open_to_work", "profile.scrape_as_of", "crm.title",
        "crm.title_as_of", "crm.persona_source", "company_identity"),
}


def _missing_lines(pkt, tier, present, width, log_only=False) -> list:
    """Every absent required/supporting field, with the packet's OWN status
    word and its OWN reason.

    A reason is NEVER invented here. An absence the packet recorded no reason
    for renders `reason unavailable` — "the packet recorded no reason; treat it
    as not looked for" was a fetch history nobody gathered. A card with no packet on it says the absence records are not
    available at all, rather than printing a status it does not have.
    """
    absent = pkt.get("absent") if isinstance(pkt.get("absent"), dict) else {}
    kind = str(tier.get("kind") or "company")
    contract = _CARD_MISSING_FIELDS.get(kind, ())
    wanted, seen = [], set()
    for group in tier.get("missing_required") or ():
        # the members of a group are ALTERNATIVES — an About page and a
        # Services page answer the same question — so the card reports the
        # group once, on the member the packet actually recorded an absence
        # for, rather than printing four lines that are one fact.
        members = [m for m in str(group).split(" or ") if m not in present]
        if not members:
            continue
        chosen = next((m for m in members if isinstance(absent.get(m), dict)),
                      members[0])
        seen.update(members)
        shown = (chosen if len(members) == 1 else
                 " or ".join(members) if len(members) == 2 else
                 f"{chosen}, or {len(members) - 1} alternatives")
        wanted.append((chosen, shown))
    for name in contract:
        if name in seen or name in present or _is_private(name):
            continue
        if name in absent:
            seen.add(name)
            wanted.append((name, name))
    other = [n for n in sorted(absent)
             if n not in seen and n not in present and not _is_private(n)]
    lines = ["Missing, and why"]
    if log_only:
        lines += _wrap("the packet is not on this card, so its absence records "
                       "— the status word and the reason for each missing "
                       "field — are not available here.", width, "  ")
    if not wanted:
        lines.append("  nothing the contract asks for is missing."
                     + (f" ({len(other)} optional field(s) are absent too.)"
                        if other else ""))
        return lines
    for name, shown in wanted[:_MAX_CARD_MISSING]:
        entry = absent.get(name)
        if isinstance(entry, dict):
            status = str(entry.get("status") or "").strip() or "status unavailable"
            reason = str(entry.get("reason") or "").strip()
        else:
            status, reason = "status unavailable", ""
        text = f"{_label(name)} [{shown}]: {status}"
        text += f" — {_scrub_contacts(reason)}" if reason else " — reason unavailable"
        lines += _wrap(text, width, "  ")
    if len(wanted) > _MAX_CARD_MISSING:
        lines.append(f"  … and {len(wanted) - _MAX_CARD_MISSING} more required "
                     "field(s) absent — the packet's `absent` block lists every one")
    if other:
        lines.append(f"  ({len(other)} optional field(s) are absent too.)")
    return lines


#: The blocks a packet of each kind can carry. A question whose evidence lives
#: entirely in blocks this kind does not have is NOT APPLICABLE to it — a
#: different state from "we looked and it was not there", and one the card must
#: not fold into the missing-evidence line.
_KIND_BLOCKS = {
    "company": frozenset(("record", "site", "maps", "registry", "entity",
                          "firmo", "icp", "company", "company_identity")),
    "person": frozenset(("record", "crm", "profile", "icp", "company",
                         "company_identity")),
}


def _question_text(q) -> str:
    return _scrub_contacts(str(q.get("text") or q.get("id") or ""))


def _needs_line(gone, aliased) -> str:
    labels = ", ".join(_read_labels((), gone, (), aliased))
    return "needs " + (labels or "evidence this packet lacks")


def _question_lines(questions, present, result, width, *, kind="company",
                    absent=None) -> list:
    """The battery, in the order it is asked, in FOUR honest states.

    answered · asked and no answer came back · not asked because the packet
    RECORDS the evidence absent · not applicable to a packet of this kind.
    The missing-evidence sentence is never printed for a question whose
    evidence was present: "its evidence is here
    and no answer came back" is an unavailable answer, not an unasked question,
    and a gate is claimed only where a recorded absence establishes one.
    """
    per_question = _res(result, "per_question", {}) or {}
    answered_ids = ({str(q) for q in per_question}
                    if isinstance(per_question, dict) else set())
    absent = absent if isinstance(absent, dict) else {}
    kind = str(kind or "company")
    kind_blocks = _KIND_BLOCKS.get(kind, frozenset())
    asked, no_answer, gated, not_applicable = [], [], [], []
    for q in questions or ():
        if not isinstance(q, dict):
            continue
        qid = str(q.get("id") or "")
        here, gone, unknown, aliased = _resolve_requires(q.get("requires"),
                                                         present)
        requires = tuple(str(n) for n in (q.get("requires") or ()))
        row = (q, here, gone, unknown, aliased)
        if any(a == qid or a.startswith(qid + "__") for a in answered_ids):
            # an answer on the row is the ground truth for "was it asked" —
            # it outranks any gate this card could compute afterwards
            asked.append(row)
            continue
        if here or unknown or not requires:
            # The evidence IS here. Only the run can say whether the question
            # was put, and it says so by carrying an answer; with other
            # answers on the row and none for this one, the answer is
            # UNAVAILABLE — never "we did not ask because nothing was there".
            (no_answer if answered_ids else asked).append(row)
            continue
        blocks = {n.split(".")[0] for n in requires if "." in n}
        if blocks and kind_blocks and not (blocks & kind_blocks):
            not_applicable.append(row + (
                f"not applicable to a {kind} packet — it reads "
                + ", ".join(sorted(blocks)) + " fields, which a "
                f"{kind} packet does not carry",))
            continue
        recorded = next((n for n in gone
                         if isinstance(absent.get(n.replace("[]", "")), dict)),
                        "")
        if recorded:
            entry = absent.get(recorded.replace("[]", "")) or {}
            status = str(entry.get("status") or "").strip() or "status unavailable"
            why = _scrub_contacts(str(entry.get("reason") or "").strip())
            gated.append(row + (_needs_line(gone, aliased)
                                + f" — the packet records it {status}"
                                + (f": {why}" if why else ", reason unavailable"),))
        else:
            gated.append(row + (_needs_line(gone, aliased)
                                + " — this packet does not carry it, and "
                                  "records no absence for it",))
    # With an answer on the card the three questions that MOVED it are printed
    # below anyway, so the battery list gets less room and says how much it hid.
    cap = 6 if answered_ids else _MAX_CARD_QUESTIONS
    cap_tail = 3 if answered_ids else _MAX_CARD_NOT_ASKED
    lines = ["Questions, in plain words"]
    if not asked and not no_answer:
        if gated and not not_applicable:
            lines.append("  none — every question in this battery needs "
                         "evidence this packet does not carry.")
        elif not_applicable and not gated:
            lines.append(f"  none — no question in this battery applies to a "
                         f"{kind} packet.")
        elif gated or not_applicable:
            lines.append("  none — every question in this battery either needs "
                         "evidence this packet does not carry or does not "
                         f"apply to a {kind} packet.")
        else:
            lines.append("  none — no question was supplied with this card.")
    for i, (q, here, gone, unknown, aliased) in enumerate(asked[:cap], 1):
        lines += _wrap(f"{i}. {_question_text(q)}", width, "  ")
        reads = _read_labels(here, gone, unknown, aliased)
        lines += _wrap("reads: " + (", ".join(reads) if reads
                                    else "the whole packet"), width, "     ",
                       hang="     ")
    if len(asked) > cap:
        lines.append(f"  … and {len(asked) - cap} more question(s) asked")
    if no_answer:
        lines.append("Asked, and no answer came back")
        for q, _h, _g, _u, _a in no_answer[:cap_tail]:
            lines += _wrap(f"- {_question_text(q)} (its evidence is here; this "
                           "run recorded no answer for it — unavailable, not "
                           "false)", width, "    ", hang="      ")
        if len(no_answer) > cap_tail:
            lines.append(f"  … and {len(no_answer) - cap_tail} more question(s) "
                         "with no answer on the row")
    tail = gated + not_applicable
    if tail:
        lines.append("Not asked, and why")
        for q, _h, _g, _u, _a, why in tail[:cap_tail]:
            lines += _wrap(f"- {_question_text(q)} ({why})",
                           width, "    ", hang="      ")
        if len(tail) > cap_tail:
            lines.append(f"  … and {len(tail) - cap_tail} more "
                         "question(s) that were not asked")
    return lines


def _answer_lines(result, questions, fields, present, tier, width) -> list:
    by_id = {str(q.get("id")): q for q in (questions or ())
             if isinstance(q, dict) and q.get("id")}
    action = str(_res(result, "action", "") or "")
    verdict = _res(result, "verdict", "")
    score = _res(result, "decision_score")
    rnd = _res(result, "round", 1)
    lines = ["Answer",
             f"  {action or '(no action)'} · verdict {verdict if verdict not in (None, '') else '(none reached)'}"
             f" · score {score if score is not None else '(none — the judge manufactured nothing)'}"
             f" · round {rnd}"]
    added = _res(result, "enrichment_added", []) or []
    if int(rnd or 1) > 1:
        lines += _wrap("enrichment added: "
                       + (", ".join(_label(f) for f in added) if added
                          else "nothing — a second pass found no more evidence"),
                       width, "  ")

    per_q = _res(result, "per_question", {}) or {}
    scored = []
    if isinstance(per_q, dict):
        for qid, answer in per_q.items():
            hit = _probability(answer)
            if hit is None:
                continue
            label, value = hit
            scored.append((value, str(qid), label))
    scored.sort(key=lambda t: (-t[0], t[1]))
    quoted = set()
    if scored:
        lines.append("Top answers")
        for value, qid, label in scored[:3]:
            base = by_id.get(qid) or by_id.get(qid.split("__")[0]) or {}
            text = base.get("text") or qid
            shown = f"{value:.2f}" + (f" ({label})" if label else "")
            lines += _wrap(f"- {shown} — {_scrub_contacts(text)}", width, "    ")
            here, gone, unknown, aliased = _resolve_requires(
                base.get("requires"), present)
            reads = _read_labels(here, gone, unknown, aliased)
            lines += _wrap("read: " + (", ".join(reads) if reads
                                       else "the whole packet"), width, "      ",
                           hang="      ")
            strongest = _strongest(here, fields)
            if strongest in quoted:
                strongest = ""
            if strongest:
                quoted.add(strongest)
                lines += _wrap(_quoted(strongest), width, "      ",
                               hang="      ")

    reasons = _res(result, "review_reasons", []) or []
    if action in ("review", "insufficient"):
        lines.append("Why a human is asked")
        if reasons:
            for reason in list(reasons)[:5]:
                lines += _wrap("- " + _reason_in_words(reason), width, "    ")
        else:
            lines.append("    - the judge reached no verdict and named no "
                         "reason; treat the row as unanswered")

    missing = tier.get("missing_required") or []
    if action == "accept":
        nxt = "accept it — the judge committed, on the evidence above"
    elif action == "reject":
        nxt = "drop it — the judge committed against, on the evidence above"
    elif tier.get("tier") in ("thin", "partial") and missing:
        nxt = ("fix the packet first — go and get "
               + "; ".join(str(m) for m in missing))
    else:
        nxt = "send it to review — a person decides this one"
    lines += _wrap("Next: " + nxt, width, "  ")
    return lines


def _strongest(names, fields) -> str:
    """The longest value among the fields a question reads — what fed it.

    A contact field is never eligible, whatever a question says it reads: the
    card's promise is that it prints no phone number and no email address, and
    quoting one here under "Top answers" breaks it exactly as printing the
    field would. The alternatives a name can
    resolve to are `FIELD_ALIASES` and nothing else — a bare suffix search
    reaches into another entity's block.
    """
    best = ""
    for name in names or ():
        if _is_private(name):
            continue
        bare = str(name).replace("[]", "")
        value = fields.get(name)
        if value is None:
            value = fields.get(bare)
        if value is None:
            for alt in FIELD_ALIASES.get(bare, ()):
                if _is_private(alt):
                    continue
                value = fields.get(alt)
                if value is not None:
                    break
        if value is None:
            continue
        text = _excerpt(_unwrap(value)[0], CARD_ANSWER_EXCERPT_CHARS,
                        experience=_is_experience_field(bare))
        if len(text) > len(best):
            best = text
    return best


#: Review reasons a composer emits, in words an operator reads. A reason with
#: no entry is printed verbatim — inventing a translation for a reason we do
#: not recognise is how a card starts lying.
_REASON_WORDS = {
    "judge_unavailable": "judge_unavailable — the judge could not be reached; "
                         "nothing was decided",
    "employer_is_status_word": "employer_is_status_word — the current employer "
                               "is a status (Retired, Stealth), not a company",
    "evidence_insufficient": "evidence_insufficient — the packet was too thin "
                             "to decide at all",
    "injection_flags": "injection_flags — the page text tries to instruct an "
                       "evaluator, so no answer from it is trusted",
    "guard_veto": "a guard fired against an accept",
}


def _reason_in_words(reason) -> str:
    text = _scrub_contacts(str(reason))
    if text in _REASON_WORDS:
        return _REASON_WORDS[text]
    if text.startswith("still_missing:"):
        return ("still_missing — after one enrichment pass these are still not "
                "there:" + text.split(":", 1)[1])
    return text
