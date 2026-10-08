#!/usr/bin/env python3
"""One person's public profile, for the profile block of a person packet.

Vendor: Blitz `POST /v2/enrichment/person`, one record per call. The parameter
name is `person_linkedin_url` — `linkedin_url` returns 422. The key is
`BLITZ_API_KEY` in the environment; nothing here reads a file, and nothing here
writes one. Stdlib only, same as every other shipped Crawford script.

Written here rather than imported from another skill on purpose. The pattern
is the roster-profile puller in the title-sweep tool (not included) (token bucket, retry
set, resumable, never prints a name) and the shape it returns is the one
`lib/packets.build_person_packet` reads; both are small enough that a copy with
a cited source beats a cross-skill import that the subscriber bundle would ship
without its other half. Crawford's bundle is a mirror of this folder and
nothing else, so the two Apify/Goma-backed profile clients that live in other
skills are unavailable to a subscriber and are not used here. Neither is any
waterfall contact-enrichment vendor: this lane is one endpoint, one record.

**A row with no LinkedIn URL gets no profile, and says so.** Blitz's people
search takes a `people.name` filter that is a measured SILENT NO-OP — it
returns the whole index rather than the person — so there is no honest free
name-to-profile lookup to fall back on. The caller records the profile block
absent with the reason `no_profile_url`, which is the true state; guessing a
profile from a name is how one person's record ends up under another's name.

    from lib.person_profiles import BlitzPersonClient, to_profile
    client = BlitzPersonClient()                      # BLITZ_API_KEY from env
    rec = client.fetch(url)                           # {url, found, person, ...}
    profile = to_profile(rec)                         # or None

Rate: the vendor caps this key at 50 req/s; the default here is 5/s because a
packet run is not a bulk pull and politeness costs nothing at this size.
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request

from .fetch_cache import now_iso

__all__ = ["BLITZ_BASE", "ENDPOINT", "ENV_KEY_NAME", "DEFAULT_RATE",
           "RETRY_STATUS", "VENDOR_AS_OF_FIELDS", "TokenBucket",
           "BlitzPersonClient", "to_profile", "normalize_linkedin_url"]

BLITZ_BASE = "https://api.blitz-api.ai"
ENDPOINT = f"{BLITZ_BASE}/v2/enrichment/person"
#: The NAME of the environment variable the key is read from — never a value.
#: The constant is not itself called `*_API_KEY`, because the subscriber
#: bundle's secret scanner refuses any `SOMETHING_API_KEY = "..."` line on
#: sight and is right to: a name and a value look identical to a regex.
ENV_KEY_NAME = "BLITZ_API_KEY"
#: Requests per second. Well under the key's 50/s ceiling — see the docstring.
DEFAULT_RATE = 5.0
DEFAULT_TIMEOUT = 60
DEFAULT_TRIES = 4
#: Transient on the vendor's side. A 404/422 is an answer ("no such person"),
#: not a failure, and is never retried.
RETRY_STATUS = frozenset((429, 500, 502, 503, 504))
#: Bytes a packet never needs, dropped before the record is kept.
DROP_FIELDS = ("profile_picture_url",)
#: Where a response may carry the age of ITS OWN record. The endpoint is a
#: database read, so this is the only date that can honestly be called
#: `scrape_as_of`; the vendor does not document one, and the shape is checked
#: at runtime rather than assumed. None present -> the age is unknown, and the
#: profile says so instead of borrowing the build time.
VENDOR_AS_OF_FIELDS = ("last_updated", "last_updated_at", "last_updated_date",
                       "updated_at", "profile_last_updated",
                       "data_last_updated", "last_refreshed_at",
                       "record_last_updated", "as_of")


def normalize_linkedin_url(url: str) -> str:
    """`https://www.linkedin.com/in/<slug>` — lowercased, no query, no trail.

    The cache key and the dedupe key. Anything that is not a `/in/` profile
    comes back "" so a company page can never be sent to a person endpoint.
    """
    s = str(url or "").strip()
    if not s:
        return ""
    s = s.split("?", 1)[0].split("#", 1)[0].rstrip("/")
    low = s.lower()
    # `linkedin.example` is the reserved test host fixtures use; it normalizes
    # the same way but keeps its host, so a fixture never names a live profile.
    for marker, host in (("linkedin.com/in/", "www.linkedin.com"),
                         ("linkedin.example/in/", "linkedin.example")):
        idx = low.find(marker)
        if idx >= 0:
            slug = s[idx + len(marker):].strip("/").split("/", 1)[0]
            return f"https://{host}/in/{slug.lower()}" if slug else ""
    return ""


class TokenBucket:
    """`rate` requests per second, shared by every caller of one client."""

    def __init__(self, rate: float):
        self.interval = 1.0 / float(rate) if rate and rate > 0 else 0.0
        self._lock = threading.Lock()
        self._next = time.monotonic()

    def acquire(self):
        if self.interval <= 0:
            return
        with self._lock:
            now = time.monotonic()
            if self._next > now:
                time.sleep(self._next - now)
                now = time.monotonic()
            self._next = max(self._next, now) + self.interval


class BlitzPersonClient:
    """One profile per call, rate-limited and retried. Never logs a name.

    `http_post(url, body_bytes, headers, timeout) -> (status, body_bytes)` is
    injectable so tests never touch the network — the same convention
    `gmaps_facts` and `discover` use. Without an API key the client refuses at
    construction: a profile lane that silently returns nothing looks exactly
    like a person with no profile, and those two states must not share a name.
    """

    def __init__(self, api_key=None, *, rate=DEFAULT_RATE,
                 timeout=DEFAULT_TIMEOUT, tries=DEFAULT_TRIES, http_post=None):
        key = api_key if api_key is not None else os.environ.get(ENV_KEY_NAME, "")
        key = (key or "").strip()
        if not key and http_post is None:
            raise RuntimeError(
                f"{ENV_KEY_NAME} is not set — the profile lane needs it; export "
                "it, or run without --profiles and every profile block is "
                "recorded absent")
        self.api_key = key
        self.timeout = int(timeout)
        self.tries = max(1, int(tries))
        self.bucket = TokenBucket(rate)
        self.http_post = http_post
        self.calls = 0

    # -- transport ----------------------------------------------------------

    def _post(self, body: bytes):
        if self.http_post is not None:
            status, raw = self.http_post(ENDPOINT, body,
                                         {"Content-Type": "application/json",
                                          "x-api-key": self.api_key},
                                         self.timeout)
            code = int(status or 0)
            if code >= 400:
                # An injected transport reports a refusal the way `urlopen`
                # does — by raising — so the ONE branch below decides what a
                # status means. Parsing a 503 gateway page as a body is how
                # "the vendor is down" becomes "this person does not exist",
                # which is an answer nobody paid for and nobody can appeal.
                raise urllib.error.HTTPError(ENDPOINT, code, f"HTTP {code}",
                                             None, None)
            return code or 200, raw
        req = urllib.request.Request(
            ENDPOINT, data=body, method="POST",
            headers={"Content-Type": "application/json",
                     "x-api-key": self.api_key})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310
            return resp.status, resp.read()

    # -- public API ---------------------------------------------------------

    def fetch(self, url: str) -> dict:
        """`{url, found, person, status, error?}` for one profile URL.

        Never raises: a person packet is built either way, and a transport
        failure has to travel as a recorded state rather than as an exception
        that takes the whole row out.
        """
        clean = normalize_linkedin_url(url)
        if not clean:
            return {"url": str(url or ""), "found": False, "person": {},
                    "status": 0, "error": "not_a_profile_url"}
        body = json.dumps({"person_linkedin_url": clean}).encode()
        last = None
        for attempt in range(self.tries):
            self.bucket.acquire()
            self.calls += 1
            try:
                status, raw = self._post(body)
                data = json.loads(raw.decode("utf-8", "replace")
                                  if isinstance(raw, (bytes, bytearray)) else raw)
                person = dict(data.get("person") or {})
                for name in DROP_FIELDS:
                    person.pop(name, None)
                return {"url": clean, "found": bool(data.get("found")),
                        "person": person, "status": status or 200,
                        "records_used": (data.get("fair_usage") or {}).get(
                            "records_used")}
            except urllib.error.HTTPError as exc:
                last = f"HTTP {exc.code}"
                if exc.code in (404, 422):       # an answer, not a failure
                    return {"url": clean, "found": False, "person": {},
                            "status": exc.code}
                if exc.code not in RETRY_STATUS:
                    return {"url": clean, "found": False, "person": {},
                            "status": exc.code, "error": last}
            except Exception as exc:             # noqa: BLE001 — timeouts, resets
                last = type(exc).__name__
            # No back-off after the LAST attempt: the run is about to give up
            # on this URL, and sleeping first buys nothing but seconds per
            # dead row.
            if attempt < self.tries - 1:
                time.sleep(1.5 * (attempt + 1))
        return {"url": clean, "found": False, "person": {}, "status": -1,
                "error": last}


# ------------------------------------------------------------ the packet shape

def _location_text(loc) -> str:
    if isinstance(loc, dict):
        parts = [loc.get("city") or "", loc.get("state_code") or loc.get("state") or ""]
        return " ".join(p for p in parts if p).strip()
    return str(loc or "").strip()


def _vendor_as_of(record: dict, person: dict) -> str:
    """The date the VENDOR says its own record was last refreshed, or "".

    `/v2/enrichment/person` is a database read, not a live scrape: the record
    may have been collected today or two years ago, and only the vendor can
    say which. It does not always say. When it does not, the honest answer is
    that the record's age is unknown — never the time this process ran.
    """
    for source in (person, record):
        if not isinstance(source, dict):
            continue
        for name in VENDOR_AS_OF_FIELDS:
            value = str(source.get(name) or "").strip()
            if value:
                return value
    return ""


def to_profile(record, *, observed_at=None) -> dict | None:
    """A vendor record -> the profile dict `build_person_packet` reads, or None.

    None means the vendor had no profile for this URL — an absence the caller
    records, never a blank profile that reads as "a person with no headline".
    The vendor's own field names (`job_title`, `job_is_current`, …) are mapped
    here and nowhere else, so a rename upstream is a one-file change.

    Two dates, and they are not the same date. `retrieved_at` is when WE read
    the record and is ours to assert; `scrape_as_of` is how old the record
    itself is and is the VENDOR's to assert — it is `None`, with
    `as_of_status: "vendor_record_age_unknown"`, whenever the response carries
    no last-updated field. `observed_at` overrides the retrieval stamp only.
    """
    if not isinstance(record, dict):
        return None
    person = record.get("person")
    if not record.get("found") or not isinstance(person, dict) or not person:
        return None
    exps = []
    for exp in person.get("experiences") or []:
        if not isinstance(exp, dict):
            continue
        exps.append({
            "title": exp.get("job_title") or exp.get("title") or "",
            "company": exp.get("company_name") or exp.get("company") or "",
            "company_linkedin_url": exp.get("company_linkedin_url") or "",
            "company_domain": exp.get("company_domain") or "",
            "start_date": exp.get("job_start_date") or exp.get("start_date") or "",
            "end_date": exp.get("job_end_date") or exp.get("end_date") or "",
            # The vendor's flag, carried under the vendor's meaning: it is true
            # for any role with a blank end date. `packets._experience` renames
            # it `provider_current` for exactly that reason.
            "is_current": bool(exp.get("job_is_current")
                               if exp.get("job_is_current") is not None
                               else exp.get("is_current")),
        })
    out = {
        "source": record.get("url") or "blitz:/v2/enrichment/person",
        "profile_url": record.get("url") or "",
        "headline": person.get("headline") or "",
        "about": person.get("about_me") or person.get("about") or "",
        "location": _location_text(person.get("location")),
        "licenses": person.get("certifications") or person.get("licenses") or [],
        "experiences": exps,
        "retrieved_at": observed_at or now_iso(),
        "scrape_as_of": _vendor_as_of(record, person) or None,
    }
    out["as_of_status"] = ("vendor_record_last_updated" if out["scrape_as_of"]
                           else "vendor_record_age_unknown")
    # The vendor does not return an open-to-work flag. Leaving the key OUT is
    # what makes the packet record it absent; a False here would be a claim.
    if person.get("open_to_work") is not None:
        out["open_to_work"] = bool(person["open_to_work"])
    return out
