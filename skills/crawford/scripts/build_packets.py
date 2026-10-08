#!/usr/bin/env python3
"""Job 8 — evidence packets for a LIST, not just for a run.

One packet per row, in the contract shape (`lib/packets.py`, documented in
`references/evidence-packet.md`): named fields, hard char caps, a source and an
`observed_at` on every value, and an ABSENCE — with a status and a reason —
wherever a value is missing. A packet is the state a literal reader (a judge,
an agent, a human on a call) is shown, so the whole point is that it can never
answer "we did not look" the same way it answers "there is nothing there".

Job 8 differs from the pack sidecar (`lib/rich_packets.py`) in exactly one way:
the rows come from a CSV instead of a finished Crawford run, so this works for
anything — a client's account list, a call list, the rows of a TAM, the
companies a mission already settled. Point `--run-dir` at an existing Crawford
run and the lanes replay that run's own page cache for free.

  build_packets.py --run-dir <dir> [--companies companies.csv]
                   [--people people.csv] [--offline] [--limit N] [--resume]
                   [--maps-budget-usd 0] [--profiles] [--max-profiles N]
                   [--registry auto|always|never]
                   [--max-fetches-total 500] [--max-subpages 5] [--enrich]
                   [--preview N [--yes]] [--icp icp.json] [--persona persona.json]

BEFORE ANY PAID PASS: run `--preview`. It builds a small, stratified sample on
the SAME lanes and budgets the full run would use, prints a PACKET CARD for each
shown row (what the judge will read, what is missing and why, the operator's
rules as the judge is given them), the tier counts, and a per-lane cost estimate
for the whole list — then stops with exit code 0. Show the operator the cards,
say the cost, and wait for a yes. `--yes` continues into the full build; the
previewed rows resume from the index rather than being built a second time.

Input columns (only `name` is required; every other column is optional and a
missing one is recorded as an absence, never as a blank):
  companies.csv  name, domain|website, city, state, phone, maps_place_id, npi, row_id
  people.csv     name, linkedin_url, company, company_domain, title, city, state, row_id
                 (plus title_as_of, persona_source, as_of — the row's own date
                 and authority for the title it asserts; a blank title_as_of is
                 recorded as UNKNOWN, never as current)

Evidence lanes, in ladder order — free first, and every paid one refuses
without an explicit budget or flag:
  pages     FREE. The run's own page cache replayed read-only, then the
            homepage and up to --max-subpages About/Services/Contact pages
            fetched into <run_dir>/rich_packet_cache/ (a SEPARATE cache, so a
            packet build never moves a pack build's content-bound build_id).
  registry  FREE. NPPES, by NPI when the row carries one, else by organization
            name — gated on a healthcare-shaped row unless --registry always.
  maps      PAID, off by default. DataForSEO Google Maps (Live, $0.002 per
            request list price) ONLY under a positive --maps-budget-usd AND
            with DATAFORSEO_LOGIN + DATAFORSEO_PASSWORD set. Every call writes
            a cost_ledger.jsonl line.
  profiles  Vendor records, OFF unless --profiles, and capped by
            --max-profiles. Blitz /v2/enrichment/person via
            lib/person_profiles.py, for rows that carry a linkedin_url. A row
            without one is recorded absent with the reason no_profile_url —
            the vendor's name filter is a measured silent no-op, so there is no
            honest free name-to-profile lookup to fall back on. A URL the
            vendor has already answered for — found or not found — is never
            asked again, not even by --enrich: a not-found cost a record.
  Blitz is the ONLY vendor this path can call, and only under --profiles. No
  waterfall contact enricher, no model call, no other paid API, ever.

--enrich runs ONE more pass over the thin and partial packets with the caps
raised (the full sub-page budget, the redirect terminus, the registry and Maps
lanes if they were SKIPPED in round 1, the profile if --profiles), rebuilds and
re-tags them. Once, never twice: a second pass that keeps going is a crawl. No
lane is ever asked twice for one row: an empty answer is an answer, and the
paid Maps call that finds no listing is billed exactly like the one that finds
a record.

Layout under <run_dir>/:
  packets/companies/<key>.json   the packet, under a `packet` key, with the
  packets/people/<key>.json      run's own bookkeeping (richness, rounds, cost)
                                 beside it — judge(decision, doc["packet"])
  packets/index.jsonl            key, kind, path, tier, present, missing, cost
  packets/richness_report.md     counts by tier, every thin/partial row's
  packets/richness_report.json   missing fields + absence reason, spend by
                                 lane, what --enrich added and what it did not
  cost_ledger.jsonl              one line per paid (and per free-lane) call
  rich_packet_cache/             sub-page bodies — never the run's page_cache
  page_cache/                    read-only here; seed it from a Crawford run

Resumable: --resume skips every key already in packets/index.jsonl. Stdlib
only. Never prints a person's name — people keys are hashes for that reason.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shlex
import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parents[2] / "_shared"))
import project_env  # noqa: E402  (loads <project root>/.env; real env wins)
project_env.load()
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import packets as pk                               # noqa: E402
from lib import person_profiles as pp                       # noqa: E402
from lib import rich_packets as rpk                         # noqa: E402
from lib import gmaps_facts as gm                           # noqa: E402
from lib.fetch_cache import PageCache, normalize_domain     # noqa: E402
import cost_ledger as cl                                    # noqa: E402
from lib.portable import write_text_utf8, read_text_compat  # noqa: E402

PACKETS_DIRNAME = "packets"
CACHE_DIRNAME = rpk.CACHE_DIRNAME
INDEX_NAME = "index.jsonl"
#: Rungs, so a Job-8 line in the ledger is distinguishable from a pack's.
MAPS_RUNG = "job8-packets-maps"
PROFILE_RUNG = "job8-packets-profile"
#: DataForSEO Google Maps SERP API, Live mode, list price per request
#: (https://dataforseo.com/pricing/serp/google-maps-serp-api). The response's
#: own `cost` is logged instead whenever it is present.
MAPS_COST_USD = gm.DATAFORSEO_MAPS_COST_USD
#: The profile lane is metered against a monthly fair-usage pool rather than
#: billed per call, so the dollar figure is 0 and the RECORD count is what the
#: report carries. A $0 line is still a line: the call happened.
PROFILE_COST_USD = 0.0
#: Round 1 is deliberately cheap — one sub-page per kind is usually all a small
#: site has. --enrich raises it to the contract's ceiling.
ROUND1_SUBPAGES = 2
DEFAULT_MAX_FETCHES = rpk.DEFAULT_MAX_FETCHES
TIERS = ("rich", "partial", "thin")
ENV_MAPS_KEY = f"{gm.ENV_LOGIN} + {gm.ENV_PASSWORD}"


def now_iso() -> str:
    """UTC now, or the fixed time in $SOURCE_DATE_EPOCH (the reproducible-builds
    convention) so a replay builds byte-identical packets."""
    fixed = os.environ.get("SOURCE_DATE_EPOCH")
    if fixed:
        return datetime.fromtimestamp(int(fixed), timezone.utc).isoformat(timespec="seconds")
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ------------------------------------------------------------------- intake

def _clean(value) -> str:
    return str(value if value is not None else "").strip()


def _first(row: dict, *names) -> str:
    for name in names:
        value = _clean(row.get(name))
        if value:
            return value
    return ""


def sanitize_key(raw: str) -> str:
    """Mirrors pack_build.sanitize_key — the same key names the same file."""
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", str(raw)).strip("._-") or "row"
    return s[:80]


def _digest(*parts) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:12]


def read_companies(path) -> tuple[list[dict], list[dict]]:
    """`(anchors, bad_rows)` from a companies CSV. A bad row is COUNTED.

    A row with no name is skipped and reported, never dropped in silence and
    never packeted: the packet's first field is the entity's own name, and a
    packet about an unnamed row is a packet about nothing.

    The key is `c-<row_id>` when the row carries one, so a company row and a
    person row that share a row_id never share a key.
    """
    anchors, bad = [], []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for line_no, row in enumerate(csv.DictReader(fh), start=2):
            name = _first(row, "name", "company", "company_name", "entity")
            domain = normalize_domain(_first(row, "domain", "website", "url")) or ""
            if not name and not domain:
                bad.append({"line": line_no, "why": "no name and no domain"})
                continue
            row_id = _first(row, "row_id", "id")
            # The key carries its KIND. A companies CSV and a people CSV both
            # number their own rows 1, 2, 3..., so an un-namespaced row_id
            # files two different entities under one name — and --resume then
            # reads the company's line as proof the person was built.
            key = sanitize_key(f"c-{row_id}" if row_id
                               else domain or f"c-{_digest(name, line_no)}")
            anchors.append({
                "entity_key": key, "kind": "company", "name": name,
                "domain": domain,
                "city": _first(row, "city"), "state": _first(row, "state"),
                "phone": _first(row, "phone"),
                "maps_place_id": _first(row, "maps_place_id", "place_id"),
                "npi": _first(row, "npi"),
                "record_source": f"{Path(path).name}:{line_no}",
            })
    return anchors, bad


def read_people(path) -> tuple[list[dict], list[dict]]:
    """`(anchors, bad_rows)` from a people CSV.

    The key is `p-<row_id>`, or a HASH of the row — never the name or the
    profile slug: the key is printed in logs and is the file name on disk, and
    a person's name belongs in the packet, not in a terminal scrollback or a
    directory listing.
    """
    anchors, bad = [], []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for line_no, row in enumerate(csv.DictReader(fh), start=2):
            name = _first(row, "name", "full_name", "person_name")
            url = pp.normalize_linkedin_url(_first(row, "linkedin_url", "linkedin",
                                                   "profile"))
            if not name and not url:
                bad.append({"line": line_no, "why": "no name and no linkedin_url"})
                continue
            company = _first(row, "company", "entity", "employer")
            company_domain = normalize_domain(
                _first(row, "company_domain", "domain", "website")) or ""
            row_id = _first(row, "row_id", "id")
            key = sanitize_key(f"p-{row_id}" if row_id
                               else f"p-{_digest(url or name, company, line_no)}")
            anchors.append({
                "entity_key": key, "kind": "person", "name": name,
                "linkedin_url": url, "company": company,
                "company_domain": company_domain, "title": _first(row, "title"),
                # A CRM title is frequently the title at hire. The row's own
                # date for it travels when the file carries one, and a blank
                # is recorded as UNKNOWN rather than as current.
                "title_as_of": _first(row, "title_as_of", "title_date"),
                "as_of": _first(row, "as_of", "row_as_of", "exported_at"),
                "persona_source": _first(row, "persona_source"),
                "city": _first(row, "city"), "state": _first(row, "state"),
                "record_source": f"{Path(path).name}:{line_no}",
            })
    return anchors, bad


# --------------------------------------------------------------------- lanes

class Lanes:
    """The four evidence lanes, each one holding its own refusal.

    Every lane is injectable — `maps_free` / `maps_paid` / `registry_http` /
    `profile_client` — so the whole builder runs offline in a test with no
    network and no key. That is also how a caller with its own state registry
    or its own Maps account wires one in without editing this file.
    """

    def __init__(self, *, ledger, offline=False, maps_budget_usd=0.0,
                 maps_credentials=None, registry_mode="auto", profiles=False,
                 profile_client=None, max_profiles=0,
                 maps_paid=None, registry_http=None):
        if maps_budget_usd is None or float(maps_budget_usd) < 0:
            raise ValueError("--maps-budget-usd must be >= 0; a paid lane needs "
                             "an explicit budget and a negative one is a bug")
        self.ledger = ledger
        self.offline = bool(offline)
        self.maps_budget_usd = float(maps_budget_usd)
        self.maps_credentials = maps_credentials if maps_credentials is not None \
            else gm.credentials_from_env()
        self.registry_mode = registry_mode
        self.profiles = bool(profiles)
        self.profile_client = profile_client
        # The one metered lane with no dollar meter — the vendor bills against
        # a record pool — so its refusal is a COUNT, the way Maps' is a budget.
        # 0 means no ceiling, which is what --profiles alone has always meant.
        self.max_profiles = max(0, int(max_profiles or 0))
        self._maps_paid = maps_paid
        self._registry_http = registry_http
        self.spend = {"maps_usd": 0.0}
        self.counts = Counter()
        self.notes: list[str] = []

    # -- maps ---------------------------------------------------------------

    @property
    def maps_budget_left(self) -> float:
        return self.maps_budget_usd - self.spend["maps_usd"]

    def maps(self, anchor, state=None):
        """A Maps record, or None. Paid (DataForSEO), and only under budget.

        `state` is a mutable dict the caller keeps for THIS ROW, remembering
        whether the lane was already asked. It is what makes "asked and got
        nothing" different from "never asked": the enrichment round re-asks only
        what was SKIPPED. A no-result is billed exactly like a record, so asking
        twice buys the same silence twice.
        """
        state = state if state is not None else {}
        if self.offline:
            state["skipped"] = "offline"
            return None
        if self.maps_budget_usd <= 0:
            state["skipped"] = "no_budget"
            return None
        if state.get("paid_asked"):
            self.counts["maps_paid_already_answered"] += 1
            return None
        if self.maps_budget_left < MAPS_COST_USD - 1e-9:
            self.counts["maps_budget_exhausted"] += 1
            state["skipped"] = "budget_exhausted"
            return None
        if not self.maps_credentials:
            self.counts["maps_no_key"] += 1
            state["skipped"] = "no_key"
            return None
        state["paid_asked"] = True
        state.pop("skipped", None)
        return self._maps_paid_call(anchor)

    def _maps_paid_call(self, anchor):
        fn = self._maps_paid or (lambda name, city, state: gm.dataforseo_facts(
            name, city, state, credentials=self.maps_credentials))
        try:
            rec = fn(anchor.get("name") or "", anchor.get("city") or "",
                     anchor.get("state") or "")
        except Exception as exc:                            # noqa: BLE001
            self.counts["maps_paid_error"] += 1
            self.notes.append(f"maps: {type(exc).__name__}")
            return None
        cost, basis = MAPS_COST_USD, "imputed"
        if isinstance(rec, dict) and "_cost_usd" in rec:
            rec = dict(rec)
            cost = float(rec.pop("_cost_usd"))
            basis = rec.pop("_cost_basis", "measured")
        # Billed the moment the provider answers — logged before it is used.
        self.spend["maps_usd"] += cost
        self.counts["maps_paid_calls"] += 1
        self._log(anchor, "dataforseo", cost, "google-maps-live-advanced",
                  "ok" if rec else "no_result", basis, MAPS_RUNG)
        return _stamp(rec, "dataforseo")

    # -- registry -----------------------------------------------------------

    def registry(self, anchor, state=None):
        """An NPPES record, or None. Free, and gated unless --registry always.

        `state` remembers whether this row was ever ASKED. The registry is
        free, so the cost of a second ask is the registry's rate limit and the
        run's clock rather than money — but "NPPES has no such organization"
        is an answer, and asking it twice cannot change it. A round-1 SKIP is
        different: an enrichment round may have learned a Maps category that
        makes the row healthcare-shaped, and that row has never been asked.
        """
        state = state if state is not None else {}
        if state.get("asked"):
            self.counts["registry_already_answered"] += 1
            return None
        if self.offline or self.registry_mode == "never":
            state["skipped"] = "offline" if self.offline else "registry_off"
            return None
        if self.registry_mode != "always" and not (
                anchor.get("npi") or rpk.looks_like_healthcare(anchor)):
            state["skipped"] = "not_healthcare_shaped"
            return None
        state["asked"] = True
        state.pop("skipped", None)
        rec = _safe(rpk.nppes_lookup, anchor, http_json=self._registry_http)
        if rec:
            self.counts["registry_hits"] += 1
        return rec

    # -- profiles -----------------------------------------------------------

    def profile(self, anchor):
        """`(profile|None, reason|None)` — the reason is why there is none."""
        if not self.profiles:
            return None, "profile_lane_off"
        url = _clean(anchor.get("linkedin_url"))
        if not url:
            # Honest, not silent: the vendor's people search takes a name
            # filter that is a measured silent no-op, so there is no free
            # name-to-profile lookup that would not be a guess.
            self.counts["no_profile_url"] += 1
            return None, "no_profile_url"
        if self.offline and self.profile_client is None:
            return None, "offline"
        if self.max_profiles and self.counts["profile_calls"] >= self.max_profiles:
            self.counts["profile_budget_exhausted"] += 1
            return None, "profile_budget_exhausted"
        client = self.profile_client
        if client is None:
            try:
                client = pp.BlitzPersonClient()
                self.profile_client = client
            except Exception as exc:                        # noqa: BLE001
                self.notes.append(str(exc)[:160])
                return None, "no_api_key"
        record = _safe(client.fetch, url)
        # A record that never arrived and a record that says "no such person"
        # are different states and must not share a name: the first is worth
        # asking again, the second already cost a record to learn.
        failed = record is None or bool(record.get("error"))
        self.counts["profile_calls"] += 1
        self._log(anchor, "blitz", PROFILE_COST_USD, "enrichment-person",
                  "error" if failed else
                  ("ok" if record.get("found") else "no_result"),
                  "measured", PROFILE_RUNG)
        if failed:
            self.counts["profile_errors"] += 1
            return None, "profile_error"
        profile = pp.to_profile(record)
        if profile:
            self.counts["profiles"] += 1
            return profile, None
        return None, "profile_not_found"

    # -- the ledger ---------------------------------------------------------

    def _log(self, anchor, provider, usd, action, outcome, basis, rung):
        if self.ledger is None:
            return
        try:
            self.ledger.log(anchor.get("entity_key") or "row", provider=provider,
                            usd=usd, action=action, outcome=outcome, basis=basis,
                            rung=rung)
        except Exception as exc:                            # noqa: BLE001
            self.notes.append(f"ledger: {type(exc).__name__}")


def _stamp(rec, source):
    if not isinstance(rec, dict) or not rec:
        return None
    if "_observed_at" in rec:
        return rec
    return dict(rec, _observed_at=now_iso(), _source=rec.get("_lane") or source)


def _safe(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except Exception:                                       # noqa: BLE001
        return None


# ------------------------------------------------------------------ building

def _company_anchor(anchor) -> dict:
    """The anchor a company packet is built from — a person row's EMPLOYER."""
    if anchor.get("kind") != "person":
        return anchor
    return {"entity_key": anchor["entity_key"] + "-company",
            "name": anchor.get("company") or "",
            "domain": anchor.get("company_domain") or "",
            "city": anchor.get("city") or "", "state": anchor.get("state") or "",
            "record_source": anchor.get("record_source") or ""}


def build_one(anchor, *, cache, lanes, max_subpages, prior=None):
    """One packet plus the lane results that produced it.

    `prior` is round 1's lane results: round 2 re-asks only the lanes that were
    never ASKED, so an enrichment pass never re-spends on a lane that answered.
    "Answered" includes an empty answer — the paid Maps call that finds no
    listing is billed exactly like the one that finds a record, and the vendor
    spent a profile record to say it has no profile. Asking again buys the same
    sentence twice. A lane that was SKIPPED in round 1 (no budget yet, not yet
    healthcare-shaped, a cap not yet raised) has answered nothing and may be
    asked once the round-2 caps are up.
    """
    prior = prior or {}
    maps_state = dict(prior.get("maps_state") or {})
    registry_state = dict(prior.get("registry_state") or {})
    maps_rec = prior.get("maps")
    if maps_rec is None:
        maps_rec = lanes.maps(_company_anchor(anchor), state=maps_state)
    registry_rec = prior.get("registry")
    if registry_rec is None:
        reg_anchor = _company_anchor(anchor)
        if maps_rec and not reg_anchor.get("maps_category"):
            reg_anchor = dict(reg_anchor,
                              maps_category=maps_rec.get("category") or "")
        registry_rec = lanes.registry(reg_anchor, state=registry_state)

    company_anchor = _company_anchor(anchor)
    company = None
    if anchor.get("kind") != "person" or company_anchor.get("domain") \
            or company_anchor.get("name"):
        company = pk.build_company_packet(
            company_anchor, page_cache=cache, maps=maps_rec,
            registry=registry_rec, fetch_subpages=True,
            max_subpage_fetches=max_subpages)

    if anchor.get("kind") != "person":
        return company, {"maps": maps_rec, "registry": registry_rec,
                         "maps_state": maps_state,
                         "registry_state": registry_state,
                         "profile": prior.get("profile"),
                         "profile_reason": prior.get("profile_reason")}

    profile = prior.get("profile")
    reason = prior.get("profile_reason")
    if profile is None and reason not in SETTLED_PROFILE_REASONS:
        profile, reason = lanes.profile(anchor)
    packet = pk.build_person_packet(anchor, profile=profile,
                                    company_packet=company)
    if profile is None and reason:
        _restate_profile_absences(packet, reason)
    return packet, {"maps": maps_rec, "registry": registry_rec,
                    "maps_state": maps_state, "registry_state": registry_state,
                    "profile": profile, "profile_reason": reason}


#: Why a profile block is empty, said in the packet itself. The STATUS stays
#: `absent` (the contract's five are the only ones a reader parses); the reason
#: is where a run says which kind of nothing this is.
_PROFILE_REASONS = {
    "no_profile_url": "no_profile_url: the row carries no linkedin_url, and the "
                      "vendor's people search ignores a name filter (measured), "
                      "so no profile was asked for",
    "profile_lane_off": "profile_lane_off: this run was not given --profiles, so "
                        "no profile was asked for",
    "no_api_key": "no_api_key: the profile lane has no BLITZ_API_KEY, so no "
                  "profile was asked for",
    "profile_not_found": "profile_not_found: the vendor has no profile at this URL",
    "profile_error": "profile_error: the vendor could not be reached for this "
                     "URL — the lane failed, it did not answer",
    "profile_budget_exhausted": "profile_budget_exhausted: this run's "
                                "--max-profiles ceiling was reached before "
                                "this row, so no profile was asked for",
    "offline": "offline: this run replays caches only and asked no vendor",
}


#: The profile states a second pass cannot improve: the vendor answered, or
#: the lane was never allowed to ask. Everything else — only a transport
#: failure, in practice — is worth one more attempt under --enrich.
SETTLED_PROFILE_REASONS = frozenset((
    "profile_lane_off", "no_profile_url", "no_api_key", "offline",
    "profile_not_found", "profile_budget_exhausted"))


def _restate_profile_absences(packet, reason):
    text = _PROFILE_REASONS.get(reason, reason)
    for key, entry in (packet.get("absent") or {}).items():
        if key.startswith("profile.") and isinstance(entry, dict):
            entry["reason"] = text


def _evidence_present(packet) -> list:
    return pk.richness(packet).get("present_fields") or []


def build_row(anchor, *, cache, lanes, max_subpages, enrich_subpages,
              enrich=False, built_at=None):
    """The full per-row build: round 1, then one enrichment round if asked.

    Returns the document that lands on disk — the contract packet under
    `packet`, and this run's own bookkeeping beside it, so a judge can be
    handed `doc["packet"]` unchanged.
    """
    packet, lane_results = build_one(anchor, cache=cache, lanes=lanes,
                                     max_subpages=max_subpages)
    tier = pk.richness(packet)
    rounds = [{"round": 1, "tier": tier["tier"],
               "present_fields": len(tier["present_fields"]),
               "missing_required": list(tier["missing_required"])}]

    if enrich and tier["tier"] in ("thin", "partial"):
        before = set(tier["present_fields"])
        packet2, lane_results = build_one(
            anchor, cache=cache, lanes=lanes,
            max_subpages=enrich_subpages, prior=lane_results)
        tier2 = pk.richness(packet2)
        added = sorted(set(tier2["present_fields"]) - before)
        rounds.append({"round": 2, "tier": tier2["tier"],
                       "present_fields": len(tier2["present_fields"]),
                       "missing_required": list(tier2["missing_required"]),
                       "enrichment_added": added,
                       "enrichment_missing": list(tier2["missing_required"])})
        # A second pass can only ever ADD evidence; if it somehow tagged lower,
        # the richer of the two is the honest one to keep.
        if TIERS.index(tier2["tier"]) <= TIERS.index(tier["tier"]):
            packet, tier = packet2, tier2

    return {
        "job": "build_packets",
        "packet_version": pk.PACKET_VERSION,
        "key": anchor["entity_key"],
        "kind": anchor.get("kind") or packet.get("kind"),
        "built_at": built_at or now_iso(),
        "packet": packet,
        "richness": tier,
        "rounds": rounds,
        "lanes": {
            "maps": bool(lane_results.get("maps")),
            "registry": bool(lane_results.get("registry")),
            "profile": bool(lane_results.get("profile")),
            "profile_reason": lane_results.get("profile_reason"),
        },
        "record_source": anchor.get("record_source") or "",
    }


# -------------------------------------------------------------------- the run

def _load_index(path: Path) -> list[dict]:
    rows = []
    if not path.exists():
        return rows
    for line in read_text_compat(path, errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict):
            rows.append(obj)
    return rows


def rebuild_refusal(rows, index_path, *, maps_budget_usd=0.0, profiles=False,
                    registry_mode="auto") -> str:
    """Why a run into a directory that ALREADY holds built packets stops.

    A preview builds rows and leaves them in the index; the run that follows
    is supposed to skip them. A run without `--resume` rebuilds them instead,
    and every lane those rows already paid for is asked again — silently. So
    it refuses, says how many rows are there and names the lanes that would
    be re-run.
    """
    tiers = Counter(str(r.get("tier") or "?") for r in rows)
    lanes = ["pages — this run's cache replayed, then polite sub-page fetches "
             "(free, but re-fetched)",
             f"registry — NPPES (--registry {registry_mode}; free)"]
    lanes.append(
        f"maps — PAID lane, up to ${float(maps_budget_usd):.4f} of the "
        f"${len(rows) * MAPS_COST_USD:.4f} these rows would cost again at "
        f"${MAPS_COST_USD:.6f}/row"
        if float(maps_budget_usd or 0.0) > 0 else
        "maps — the PAID lane is off (--maps-budget-usd 0); nothing is called")
    lanes.append("profiles — one vendor record per row with a linkedin_url, "
                 "spent again" if profiles else
                 "profiles — lane off (no --profiles)")
    return (f"refusing to build into a run directory that already holds "
            f"{len(rows)} packet(s).\n"
            f"  index : {index_path}\n"
            f"  tiers : {dict(sorted(tiers.items()))}\n"
            "  re-running would rebuild all of them and re-run these lanes:\n"
            + "".join(f"    - {lane}\n" for lane in lanes)
            + "  Add --resume to KEEP those rows and build only what is left "
              "(nothing is paid for twice),\n"
              "  or --rebuild to build them again on purpose.")


def run(run_dir, *, companies=None, people=None, offline=False, limit=0,
        resume=False, rebuild=False, maps_budget_usd=0.0, profiles=False,
        max_profiles=0, registry_mode="auto",
        max_fetches_total=DEFAULT_MAX_FETCHES,
        max_subpages=pk.MAX_SUBPAGE_FETCHES, enrich=False, ledger=None,
        lanes=None, fetch_page=None, docs_out=None) -> dict:
    """Build every packet the inputs ask for. Returns the summary the CLI prints.

    `docs_out` is an optional list the built documents are appended to. The
    preview needs the packets themselves to print a card, and reading them
    back off disk would be a second source of truth for what this run built.
    """
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    offline = bool(offline) or rpk.env_offline()

    anchors, bad = [], []
    if companies:
        got, bad_rows = read_companies(companies)
        anchors += got
        bad += [dict(r, file=str(companies)) for r in bad_rows]
    if people:
        got, bad_rows = read_people(people)
        anchors += got
        bad += [dict(r, file=str(people)) for r in bad_rows]
    if not anchors:
        raise SystemExit("no usable rows — pass --companies and/or --people "
                         "with at least a name or a domain per row")

    out_dir = run_dir / PACKETS_DIRNAME
    index_path = out_dir / INDEX_NAME
    built_already = _load_index(index_path)
    if built_already and not resume and not rebuild:
        raise SystemExit(rebuild_refusal(
            built_already, index_path, maps_budget_usd=maps_budget_usd,
            profiles=profiles, registry_mode=registry_mode))
    # (kind, key), not key: the key already carries its kind, and pairing the
    # two means a resume can never read one kind's line as another's proof.
    done = {(r.get("kind"), r.get("key")) for r in built_already} \
        if resume else set()
    todo = [a for a in anchors
            if (a.get("kind"), a["entity_key"]) not in done]
    if limit:
        # A NEGATIVE limit means BUILD NOTHING. A repeated `--preview` already
        # has its sample on disk, and it must not quietly spend on ten more
        # rows just because the file has more rows in it.
        todo = todo[:int(limit)] if int(limit) > 0 else []

    if ledger is None:
        ledger = cl.Ledger(run_dir / "cost_ledger.jsonl")
    if lanes is None:
        lanes = Lanes(ledger=ledger, offline=offline,
                      maps_budget_usd=maps_budget_usd, profiles=profiles,
                      max_profiles=max_profiles, registry_mode=registry_mode)

    read_cache = PageCache(run_dir, offline=True)
    write_cache = None
    if not offline and fetch_page is None:
        write_cache = PageCache(run_dir / CACHE_DIRNAME,
                                timeout=rpk.SUBPAGE_TIMEOUT_S,
                                fetch_favicon=False, offline=False)
    cache = rpk.ChainedCache(read_cache, write_cache=write_cache,
                              fetch_page=fetch_page,
                              max_fetches=int(max_fetches_total))

    (out_dir / "companies").mkdir(parents=True, exist_ok=True)
    (out_dir / "people").mkdir(parents=True, exist_ok=True)
    index_rows = list(built_already) if resume else []
    built, errors = [], []
    built_at = now_iso()

    for anchor in todo:
        try:
            doc = build_row(anchor, cache=cache, lanes=lanes,
                            max_subpages=min(max_subpages, ROUND1_SUBPAGES),
                            enrich_subpages=max_subpages, enrich=enrich,
                            built_at=built_at)
        except Exception as exc:                            # noqa: BLE001
            errors.append(f"{anchor['entity_key']}: {type(exc).__name__}: {exc}"[:300])
            continue
        sub = "people" if doc["kind"] == "person" else "companies"
        path = out_dir / sub / f"{anchor['entity_key']}.json"
        text = json.dumps(doc, indent=1, sort_keys=True) + "\n"
        write_text_utf8(path, text)
        index_rows.append({
            "key": doc["key"], "kind": doc["kind"],
            "path": str(path.relative_to(run_dir)),
            "tier": doc["richness"]["tier"],
            "present_fields": len(doc["richness"]["present_fields"]),
            "missing_required": doc["richness"]["missing_required"],
            "rounds": len(doc["rounds"]),
            "cost_usd": 0.0,
            "sha256": hashlib.sha256(text.encode()).hexdigest(),
        })
        built.append(doc)

    # Spend is per-run, not per-row: a Maps call is charged to the row that
    # made it, and the ledger is where that lives. The index carries the share
    # so a reader of one line is not told a number the ledger disagrees with.
    share = (lanes.spend["maps_usd"] / len(built)) if built else 0.0
    for row in index_rows[-len(built):] if built else []:
        row["cost_usd"] = round(share, 8)

    write_text_utf8(index_path,
        "".join(json.dumps(r, sort_keys=True) + "\n" for r in index_rows))

    if docs_out is not None:
        docs_out.extend(built)

    summary = summarize(built, index_rows, lanes, bad=bad, errors=errors,
                        cache=cache, enrich=enrich, offline=offline,
                        run_dir=run_dir)
    write_text_utf8(out_dir / "richness_report.json",
        json.dumps(summary, indent=2, sort_keys=True) + "\n")
    write_text_utf8(out_dir / "richness_report.md", render_report(summary))
    return summary


def summarize(built, index_rows, lanes, *, bad, errors, cache, enrich, offline,
              run_dir) -> dict:
    tiers = Counter(r["tier"] for r in index_rows)
    kinds = Counter(r["kind"] for r in index_rows)
    weak = []
    for doc in built:
        if doc["richness"]["tier"] == "rich":
            continue
        absent = doc["packet"].get("absent") or {}
        missing = []
        for group in doc["richness"]["missing_required"]:
            # The group is "a or b or c"; name the reason for each member the
            # packet actually recorded, so the row says WHY, not just WHAT.
            reasons = {}
            for name in group.split(" or "):
                entry = absent.get(name)
                if isinstance(entry, dict):
                    reasons[name] = {"status": entry.get("status"),
                                     "reason": entry.get("reason")}
            missing.append({"group": group, "absences": reasons})
        rounds = doc["rounds"]
        weak.append({
            "key": doc["key"], "kind": doc["kind"],
            "tier": doc["richness"]["tier"],
            "present_fields": len(doc["richness"]["present_fields"]),
            "missing_required": missing,
            "enrichment_added": (rounds[-1].get("enrichment_added")
                                 if len(rounds) > 1 else None),
            "profile_reason": doc["lanes"].get("profile_reason"),
        })
    added = Counter()
    enriched = 0
    for doc in built:
        if len(doc["rounds"]) < 2:
            continue
        enriched += 1
        for name in doc["rounds"][-1].get("enrichment_added") or ():
            added[name] += 1
    return {
        "run_dir": str(run_dir), "built_at": now_iso(), "offline": bool(offline),
        "packets": len(index_rows), "built_this_run": len(built),
        "by_tier": {t: tiers.get(t, 0) for t in TIERS},
        "by_kind": dict(kinds),
        "bad_rows": bad, "bad_row_count": len(bad), "errors": errors,
        "fetches": getattr(cache, "fetches", 0),
        "fetch_budget_exhausted": bool(getattr(cache, "budget_exhausted", False)),
        "spend_by_lane_usd": {
            "pages": 0.0,
            "registry": 0.0,
            "maps": round(lanes.spend["maps_usd"], 6),
            "profiles": round(PROFILE_COST_USD * lanes.counts["profile_calls"], 6),
        },
        "maps_budget_usd": lanes.maps_budget_usd,
        "lane_counts": dict(lanes.counts),
        "lane_notes": lanes.notes,
        "enrich": bool(enrich), "enriched_rows": enriched,
        "enrichment_added_fields": dict(added),
        "weak_rows": weak,
    }


def render_report(s: dict) -> str:
    lines = ["# Evidence packets — richness report", "",
             f"Built {s['built_this_run']} packet(s) this run; "
             f"{s['packets']} in the index. Offline: {s['offline']}.", ""]
    lines += ["| tier | packets | what it means |", "|---|---|---|"]
    # One sentence per tier, from the builder — the report and the packet card
    # must never describe the same word two different ways.
    meaning = pk.TIER_MEANING
    for tier in TIERS:
        lines.append(f"| {tier} | {s['by_tier'].get(tier, 0)} | {meaning[tier]} |")
    lines += ["", f"By kind: {s['by_kind'] or '{}'}.",
              f"Sub-page fetches: {s['fetches']}"
              + (" (budget exhausted)" if s["fetch_budget_exhausted"] else ""),
              ""]

    lines += ["## Spend, by lane", "", "| lane | usd | note |", "|---|---|---|",
              "| pages | $0.000000 | the run's own cache, then polite fetches |",
              "| registry | $0.000000 | NPPES is free |",
              f"| maps | ${s['spend_by_lane_usd']['maps']:.6f} | "
              f"budget ${s['maps_budget_usd']:.4f}; DataForSEO Maps, "
              f"${MAPS_COST_USD:.4f}/request list price |",
              f"| profiles | ${s['spend_by_lane_usd']['profiles']:.6f} | "
              f"{s['lane_counts'].get('profile_calls', 0)} vendor record(s), "
              "metered against a fair-usage pool, not billed per call |", ""]
    if s["lane_counts"]:
        lines += [f"Lane counts: `{json.dumps(s['lane_counts'], sort_keys=True)}`", ""]
    if s["lane_notes"]:
        lines += ["Lane notes: " + "; ".join(s["lane_notes"]), ""]

    if s["enrich"]:
        lines += ["## What --enrich added", "",
                  f"{s['enriched_rows']} thin/partial row(s) got a second pass."]
        if s["enrichment_added_fields"]:
            lines += ["", "| field | rows it was added to |", "|---|---|"]
            for name, count in sorted(s["enrichment_added_fields"].items()):
                lines.append(f"| `{name}` | {count} |")
        else:
            lines.append("")
            lines.append("It added no field to any row — the evidence is not "
                         "there to be found by another pass.")
        lines.append("")

    weak = s["weak_rows"]
    lines += ["## Thin and partial rows", ""]
    if not weak:
        lines += ["None — every packet is rich.", ""]
    else:
        lines += [f"{len(weak)} row(s). The key is the packet's file name; a "
                  "person's name is deliberately not printed here.", ""]
        for row in weak:
            lines.append(f"### `{row['key']}` — {row['tier']} ({row['kind']}, "
                         f"{row['present_fields']} field(s) present)")
            for group in row["missing_required"]:
                lines.append(f"- missing **{group['group']}**")
                for name, why in sorted(group["absences"].items()):
                    lines.append(f"  - `{name}`: {why['status']} — {why['reason']}")
                if not group["absences"]:
                    lines.append("  - the packet recorded no absence for any "
                                 "member of this group")
            if row.get("profile_reason"):
                lines.append(f"- profile lane: {row['profile_reason']}")
            if row.get("enrichment_added"):
                lines.append("- `--enrich` added: "
                             + ", ".join(f"`{f}`" for f in row["enrichment_added"]))
            elif row.get("enrichment_added") == []:
                lines.append("- `--enrich` ran and found nothing more")
            lines.append("")

    if s["bad_rows"]:
        lines += ["## Rows that could not become a packet", "",
                  f"{s['bad_row_count']} row(s) were skipped and counted, never "
                  "packeted:", ""]
        for row in s["bad_rows"][:50]:
            lines.append(f"- `{row.get('file')}` line {row.get('line')}: "
                         f"{row.get('why')}")
        lines.append("")
    if s["errors"]:
        lines += ["## Errors", ""] + [f"- {e}" for e in s["errors"][:50]] + [""]
    return "\n".join(lines)


# ------------------------------------------------------------------ preview
#: How many rows a preview builds, whatever N it is asked to SHOW. A tier
#: distribution read off three rows is not a distribution; ten is still free on
#: the cache lane and still under a cent on the paid Maps lane.
PREVIEW_BUILD_MIN = 10
PREVIEW_FILE = "preview.md"
#: The line the operator (and the agent reading its stdout) must see before
#: anything is charged. It is asserted in the tests on purpose.
PREVIEW_STOP_LINE = (
    "Preview only — nothing beyond the sample was built. To build the rest, "
    "re-run the same command WITH --resume and WITHOUT --preview (or add "
    "--yes now). Without --resume the run refuses: the previewed rows are "
    "already in packets/index.jsonl and building them again pays for them "
    "twice.")


def continuation_command(argv=None) -> str:
    """The exact command that finishes this run without paying twice.

    Built from the arguments THIS invocation was given, minus `--preview` (and
    its N) and `--yes`, plus `--resume`. Printing "re-run without --preview"
    and leaving the operator to remember `--resume` is what let a previewed row
    be built a second time.
    """
    argv = list(argv if argv is not None else sys.argv[1:])
    out, skip = [], False
    for i, token in enumerate(argv):
        if skip:
            skip = False
            continue
        if token == "--preview":
            nxt = argv[i + 1] if i + 1 < len(argv) else ""
            skip = bool(nxt) and not nxt.startswith("-")
            continue
        if token.startswith("--preview=") or token in ("--yes", "--resume",
                                                       "--rebuild"):
            continue
        out.append(token)
    out.append("--resume")
    return "python3 build_packets.py " + " ".join(shlex.quote(t) for t in out)


def load_rules(path):
    """The rule block from a JSON or YAML file — or a refusal that says why.

    The contract's rule block (`references/evidence-packet.md` § The rule block
    travels inside the packet) is `icp.inclusions`, `icp.exclusions`,
    `icp.territories`, `icp.competitor_names`, and for a persona `what`,
    `not_for`, `examples`, `weights`. Nothing here validates the CONTENT — an
    empty exclusion list is a legitimate state, and the card says out loud what
    it costs.
    """
    if not path:
        return None
    text = read_text_compat(Path(path))
    try:
        return json.loads(text)
    except ValueError:
        pass
    try:
        import yaml                                         # noqa: PLC0415
    except ImportError as exc:                              # pragma: no cover
        raise SystemExit(f"{path} is not JSON and PyYAML is not installed, so "
                         "it cannot be read as YAML either") from exc
    loaded = yaml.safe_load(text)
    if not isinstance(loaded, dict):
        raise SystemExit(f"{path} must be a mapping of rule-block keys")
    return loaded


def pick_preview(docs, n):
    """Up to `n` packets to show: one of each tier first, then in order.

    An operator shown three rich packets approves a run whose thin rows they
    have never seen. The whole point of the card is that the empty ones look
    empty, so the sample is stratified before it is truncated.
    """
    n = max(1, int(n or 1))
    chosen, seen = [], set()
    for tier in TIERS:
        for doc in docs:
            key = doc.get("key")
            if key in seen or (doc.get("richness") or {}).get("tier") != tier:
                continue
            chosen.append(doc)
            seen.add(key)
            break
        if len(chosen) >= n:
            break
    for doc in docs:
        if len(chosen) >= n:
            break
        if doc.get("key") not in seen:
            chosen.append(doc)
            seen.add(doc.get("key"))
    order = {doc.get("key"): i for i, doc in enumerate(docs)}
    return sorted(chosen[:n], key=lambda d: order.get(d.get("key"), 0))


def ledger_rates(path) -> dict:
    """`{rung: (usd_per_call, basis)}` measured from THIS run's own ledger.

    The estimate below is arithmetic on a rate, and the rate should come from
    what the run actually recorded rather than from a constant in this file
    that a price change would leave stale. A rung with no priced line falls
    back to the constant, and the printed line says which it used.
    """
    rates = {}
    path = Path(path)
    if not path.exists():
        return rates
    totals = {}
    for line in read_text_compat(path, errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        rung = str(row.get("rung") or "")
        usd = row.get("usd")
        if not rung or not isinstance(usd, (int, float)):
            continue
        total, count, basis = totals.get(rung, (0.0, 0, row.get("basis")))
        totals[rung] = (total + float(usd), count + 1, basis)
    for rung, (total, count, basis) in totals.items():
        if count and total > 0:
            rates[rung] = (total / count, str(basis or "measured"))
    return rates


def estimate(companies, people, *, maps_budget_usd, profiles, rates=None,
             registry_mode="auto") -> dict:
    """What the FULL list would cost, per lane, as arithmetic anyone can check.

    An estimate, and it says so. Pages and the registry are free by
    construction; Maps has a per-row rate and a budget ceiling; the profile
    lane is metered in vendor RECORDS, not dollars, so its dollar column is
    honestly zero and its record count is the number that matters.
    """
    rates = rates or {}
    maps_rate, maps_basis = rates.get(MAPS_RUNG, (MAPS_COST_USD, "imputed"))
    rows = len(companies) + len(people)
    profile_rows = sum(1 for a in people if a.get("linkedin_url"))
    maps_would = rows * maps_rate
    maps_will = min(maps_would, float(maps_budget_usd or 0.0))
    lanes = [
        {"lane": "pages", "rows": rows, "rate": 0.0, "usd": 0.0,
         "note": "the run's own cache replayed, then polite sub-page fetches"},
        {"lane": "registry", "rows": rows, "rate": 0.0, "usd": 0.0,
         "note": f"NPPES is free (--registry {registry_mode})"},
        {"lane": "maps", "rows": rows, "rate": maps_rate, "usd": maps_will,
         "note": (f"PAID lane is OFF (--maps-budget-usd 0), so $0.00 is spent; "
                  f"turning it on would cost ${maps_would:.4f} ({maps_basis})"
                  if not maps_budget_usd else
                  f"budget ${float(maps_budget_usd):.4f} caps it "
                  f"({maps_basis} rate, DataForSEO)")},
        {"lane": "profiles", "rows": profile_rows, "rate": PROFILE_COST_USD,
         "usd": 0.0,
         "note": ("lane OFF (no --profiles)" if not profiles else
                  f"{profile_rows} vendor record(s), metered against a "
                  "fair-usage pool rather than billed per call")},
    ]
    return {"rows": rows, "company_rows": len(companies),
            "people_rows": len(people), "profile_rows": profile_rows,
            "lanes": lanes,
            "total_usd": round(sum(l["usd"] for l in lanes), 6),
            "maps_if_enabled_usd": round(maps_would, 6)}


def render_estimate(est) -> str:
    lines = ["Cost estimate for the FULL list — an ESTIMATE, not a bill",
             f"  {est['rows']} row(s): {est['company_rows']} company, "
             f"{est['people_rows']} people"]
    for lane in est["lanes"]:
        lines.append(f"  {lane['lane']:<9} {lane['rows']:>6} rows x "
                     f"${lane['rate']:.6f}/row = ${lane['rows'] * lane['rate']:.6f}"
                     f"  ->  ${lane['usd']:.6f} as configured")
        lines.append(f"            {lane['note']}")
    lines.append(f"  TOTAL as configured: ${est['total_usd']:.6f}")
    return "\n".join(lines)


def render_preview(docs, est, rules=None, *, run_dir=None, tiers=None,
                   decision=None) -> str:
    """The preview document: one card per shown row, the rules, the estimate."""
    tiers = tiers or Counter((d.get("richness") or {}).get("tier") for d in docs)
    lines = ["# Packet preview — what the judge would read", "",
             "Cards for a sample of this list, built on the same lanes and the "
             "same budgets the full run would use. Read them, read the rules, "
             "read the estimate, then decide.", ""]
    if run_dir:
        lines += [f"Run dir: `{run_dir}`", ""]
    lines += [f"Rows built for this preview, by tier: "
              f"{dict(sorted(tiers.items()))}", ""]
    # The rule block is printed ONCE, not once per card: it is the same
    # block for every row in the run, and repeating it is how the cards stop
    # being read.
    lines += ["```", _rules_text(rules, decision), "```", ""]
    for doc in docs:
        lines += [f"## `{doc.get('key')}` — {doc.get('kind')}", "", "```",
                  pk.explain(doc.get("packet")), "```", ""]
    lines += ["## The estimate", "", "```", render_estimate(est), "```", "",
              PREVIEW_STOP_LINE, ""]
    return "\n".join(lines)


def _rules_text(rules, decision=None) -> str:
    """The rule block, told which composer will read it.

    `explain_rules` prints the CO-RULE-01 warning — an empty exclusion list
    means fit can only come back MAYBE — for the two FIT decisions and nothing
    else, so the decision this list is being qualified under travels with it
   . An older Crawford has no decision
    parameter; it still renders, without the gate.
    """
    try:
        return pk.explain_rules(rules, decision)
    except TypeError:                                       # pragma: no cover
        return pk.explain_rules(rules)


def docs_from_index(run_dir, limit) -> list:
    """The first `limit` packets this run has already built, read off disk.

    A second `--preview` must not re-spend on rows the first one already paid
    for, so the preview always resumes — and a resumed preview builds nothing,
    which would leave it with no cards to show. It reads the packets back
    instead.
    """
    out = []
    run_dir = Path(run_dir)
    for row in _load_index(run_dir / PACKETS_DIRNAME / INDEX_NAME)[:int(limit)]:
        path = run_dir / str(row.get("path") or "")
        if not path.is_file():
            continue
        try:
            doc = json.loads(read_text_compat(path))
        except ValueError:
            continue
        if isinstance(doc, dict) and doc.get("packet"):
            out.append(doc)
    return out


def preview(run_dir, *, show=3, companies=None, people=None, icp=None,
            persona=None, **run_kw) -> dict:
    """Build a small, stratified sample, print its cards, and stop.

    Nothing here is a second code path: it calls `run()` with a `--limit`, so
    the lanes, the budgets, the caps and the ledger are the ones the real run
    uses. Every row it builds lands in `packets/index.jsonl`, so the full run
    that follows skips them under `--resume` and no row is ever paid for twice.
    """
    run_dir = Path(run_dir)
    show = max(1, int(show or 1))
    company_anchors = read_companies(companies)[0] if companies else []
    people_anchors = read_people(people)[0] if people else []
    rules = icp if isinstance(icp, dict) else load_rules(icp)
    persona_rules = persona if isinstance(persona, dict) else load_rules(persona)
    # Which composer will read this block, so the card's CO-RULE-01 warning
    # fires for a fit block and stays silent on anything else.
    decision = ("icp_fit" if rules else
                "persona_fit" if persona_rules else None)
    if rules and persona_rules:
        rules = {"icp": rules, "persona": persona_rules}
    elif persona_rules and not rules:
        rules = persona_rules

    docs = []
    run_kw.pop("limit", None)
    run_kw.pop("docs_out", None)
    run_kw.pop("resume", None)
    # A preview never rebuilds: its whole contract is that it costs nothing a
    # second time. `--rebuild` is for the FULL run the operator chooses.
    run_kw.pop("rebuild", None)
    # ALWAYS resume. A preview run twice must not buy the same Maps record
    # twice; the rows already in the index are read back off disk instead.
    built = max(show, PREVIEW_BUILD_MIN)
    already = docs_from_index(run_dir, built)
    remaining = built - len(already)
    summary = run(run_dir, companies=companies, people=people,
                  limit=remaining if remaining > 0 else -1,
                  resume=True, docs_out=docs, **run_kw)
    docs = (already + docs)[:built] if already else docs
    shown = pick_preview(docs, show)
    est = estimate(company_anchors, people_anchors,
                   maps_budget_usd=run_kw.get("maps_budget_usd", 0.0),
                   profiles=run_kw.get("profiles", False),
                   rates=ledger_rates(run_dir / "cost_ledger.jsonl"),
                   registry_mode=run_kw.get("registry_mode", "auto"))
    tiers = Counter((d.get("richness") or {}).get("tier") for d in docs)
    text = render_preview(shown, est, rules, run_dir=run_dir, tiers=tiers,
                          decision=decision)
    out = run_dir / PACKETS_DIRNAME / PREVIEW_FILE
    out.parent.mkdir(parents=True, exist_ok=True)
    write_text_utf8(out, text)
    return {"summary": summary, "shown": shown, "built": docs, "rules": rules,
            "estimate": est, "tiers": dict(tiers), "path": out, "text": text,
            "decision": decision}


def print_preview(result) -> None:
    """Everything the operator has to see before a yes. Cards, rules, money."""
    print()
    print(_rules_text(result["rules"], result.get("decision")))
    for doc in result["shown"]:
        print()
        print(pk.explain(doc.get("packet")))
    print()
    print(f"Rows built for this preview, by tier: "
          f"{dict(sorted(result['tiers'].items()))}")
    print()
    print(render_estimate(result["estimate"]))
    print()
    print(f"preview: {result['path']}")


# ------------------------------------------------------------------- the CLI

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-dir", required=True,
                    help="output directory (created); point it at an existing "
                         "Crawford run to replay that run's page cache for free")
    ap.add_argument("--companies", help="CSV: name[,domain|website,city,state,"
                                        "phone,maps_place_id,npi,row_id]")
    ap.add_argument("--people", help="CSV: name[,linkedin_url,company,"
                                     "company_domain,title,title_as_of,"
                                     "persona_source,city,state,row_id]")
    ap.add_argument("--offline", action="store_true",
                    help="cache replay only — no fetch, no vendor, no spend")
    ap.add_argument("--limit", type=int, default=0,
                    help="only the first N rows (0 = no limit)")
    ap.add_argument("--resume", action="store_true",
                    help="skip every key already in packets/index.jsonl. A run "
                         "into a directory that already holds packets REFUSES "
                         "without this (or --rebuild), because rebuilding them "
                         "pays for them twice")
    ap.add_argument("--rebuild", action="store_true",
                    help="build every row again even though the run directory "
                         "already holds packets — an explicit re-spend. Cannot "
                         "be combined with --resume or --preview")
    ap.add_argument("--maps-budget-usd", type=float, default=0.0,
                    help="0 (the default) means the PAID Maps lane (DataForSEO) "
                         "is off and nothing is called")
    ap.add_argument("--profiles", action="store_true",
                    help="fetch a profile for every people row with a "
                         "linkedin_url (spends vendor records; needs "
                         f"{pp.ENV_KEY_NAME}). Off by default, and the absence "
                         "is recorded honestly")
    ap.add_argument("--max-profiles", type=int, default=0,
                    help="ceiling on vendor profile records this run may spend "
                         "(0, the default, means no ceiling); past it the lane "
                         "refuses and the absence says profile_budget_exhausted")
    ap.add_argument("--registry", choices=["auto", "always", "never"],
                    default="auto",
                    help="auto (default) asks NPPES for healthcare-shaped rows "
                         "and any row with an npi; always asks for every row")
    ap.add_argument("--max-fetches-total", type=int, default=DEFAULT_MAX_FETCHES,
                    help="run-wide ceiling on network sub-page fetches")
    ap.add_argument("--max-subpages", type=int, default=pk.MAX_SUBPAGE_FETCHES,
                    help="sub-page fetches per entity in the enrichment round "
                         f"(default {pk.MAX_SUBPAGE_FETCHES}; round 1 uses "
                         f"{ROUND1_SUBPAGES})")
    ap.add_argument("--enrich", action="store_true",
                    help="one more pass over thin/partial packets with the caps "
                         "raised, then rebuild and re-tag. Once, never twice")
    ap.add_argument("--preview", nargs="?", type=int, const=3, default=None,
                    metavar="N",
                    help="build a small sample on the SAME lanes and budgets, "
                         "print N packet cards (default 3 — one rich, one "
                         "partial, one thin where they exist), the rule block, "
                         "the tier counts and a cost estimate for the full "
                         "list, then STOP. Nothing beyond the sample is built "
                         "until you add --yes")
    ap.add_argument("--yes", action="store_true",
                    help="with --preview: print the preview, then go on and "
                         "build the rest. The previewed rows are resumed, "
                         "never rebuilt, so nothing is paid for twice")
    ap.add_argument("--icp", help="JSON or YAML rule block for the card: "
                                  "inclusions, exclusions, territories, "
                                  "competitor_names")
    ap.add_argument("--persona", help="JSON or YAML persona block for the card: "
                                      "what, not_for, examples, weights")
    a = ap.parse_args(argv)

    if not a.companies and not a.people:
        ap.error("pass --companies and/or --people")
    if a.maps_budget_usd < 0:
        ap.error("--maps-budget-usd must be >= 0")
    if a.max_profiles < 0:
        ap.error("--max-profiles must be >= 0 (0 means no ceiling)")
    if a.rebuild and a.resume:
        ap.error("--rebuild and --resume are opposites: --resume keeps the "
                 "rows already built, --rebuild pays for them again")
    if a.rebuild and a.preview is not None:
        ap.error("--rebuild cannot be combined with --preview: a preview "
                 "always resumes, so it never pays for a row twice")
    if a.profiles and not os.environ.get(pp.ENV_KEY_NAME):
        ap.error(f"--profiles needs {pp.ENV_KEY_NAME} in the environment; "
                 "without it every profile block would be recorded absent, "
                 "which is a run you did not ask for")
    if a.maps_budget_usd > 0 and not gm.credentials_from_env():
        print(f"note: --maps-budget-usd is set but {ENV_MAPS_KEY} are not — the "
              "Maps lane cannot run and every Maps field will be recorded absent",
              file=sys.stderr)

    common = dict(offline=a.offline, maps_budget_usd=a.maps_budget_usd,
                  profiles=a.profiles, max_profiles=a.max_profiles,
                  registry_mode=a.registry,
                  max_fetches_total=a.max_fetches_total,
                  max_subpages=a.max_subpages, enrich=a.enrich)

    if a.preview is not None:
        if a.preview < 1:
            ap.error("--preview N must be at least 1")
        result = preview(a.run_dir, show=a.preview, companies=a.companies,
                         people=a.people, icp=a.icp, persona=a.persona,
                         resume=a.resume, **common)
        print_preview(result)
        if not a.yes:
            print()
            print(PREVIEW_STOP_LINE)
            print()
            print("  " + continuation_command(argv))
            return 0
        print()
        print("--yes: building the rest. The previewed rows are resumed from "
              "packets/index.jsonl, not rebuilt.")
        summary = run(a.run_dir, companies=a.companies, people=a.people,
                      limit=a.limit, resume=True, **common)
    else:
        summary = run(a.run_dir, companies=a.companies, people=a.people,
                      limit=a.limit, resume=a.resume, rebuild=a.rebuild,
                      **common)

    out = Path(a.run_dir) / PACKETS_DIRNAME
    print(f"packets: {summary['built_this_run']} built this run "
          f"({summary['packets']} in the index) -> {out}")
    print(f"tiers  : {summary['by_tier']}")
    print(f"fetches: {summary['fetches']}"
          + ("  (sub-page budget exhausted)"
             if summary["fetch_budget_exhausted"] else ""))
    print(f"spend  : {summary['spend_by_lane_usd']} "
          f"(ledger: {Path(a.run_dir) / 'cost_ledger.jsonl'})")
    if summary["bad_row_count"]:
        print(f"skipped: {summary['bad_row_count']} unusable input row(s) — "
              "see the report")
    if summary["errors"]:
        print(f"errors : {len(summary['errors'])} — see the report")
    print(f"report : {out / 'richness_report.md'}")
    return 0 if not summary["errors"] else 2


if __name__ == "__main__":
    from lib.portable import utf8_stdio
    utf8_stdio()  # Windows console: UTF-8 before any output
    sys.exit(main())
