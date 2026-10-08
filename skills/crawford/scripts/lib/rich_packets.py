#!/usr/bin/env python3
"""Rich evidence packets, emitted beside every Crawford pack.

One packet per entity, at `<run_dir>/packs/<mission>/packets/<entity_key>.json`,
built from the rungs' own cached evidence plus at most five polite sub-page
fetches. The packet SHAPE is not defined here — it is `lib/packets.py`, which
owns the contract in `references/evidence-packet.md`. This module is the
pack-sidecar side: which rows become anchors, which caches may be read, which
sources may be paid for, and where the file lands. Job 8
(`scripts/build_packets.py`) is the other caller — same builder, a CSV instead
of a run directory.

**A packet is a sidecar artifact and changes nothing about a build.**

  * `build_id` is computed over `page_cache/*.html`, so a sub-page fetch
    landing there would move the id of the NEXT build. Sub-pages are therefore
    cached in a SEPARATE PageCache root (`<run_dir>/rich_packet_cache/`), and
    the run's own page cache is opened read-only/offline. Nothing this module
    writes is a pack_build input.
  * No pack, manifest, assignment or dispatch contract references a packet, and
    nothing reads one during blind adjudication. Packets carry evidence and
    identity only — never a prior answer, never a verdict.
  * Every failure degrades to a counted skip. This module cannot fail a run.

Spend: free by default. Maps (DataForSEO, ~$0.002/row) is not called at all
unless the caller passes a positive `maps_budget_usd` AND a `maps_lookup`; the
budget is a refusal, and every paid call writes a `cost_ledger.jsonl` line at
rung `rich-packets-maps`. NPPES is free. Profiles are off unless the caller
passes a `profile_lookup`, because that lane spends vendor records.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from . import fetch_cache
from . import packets
from . import person_profiles
from .fetch_cache import PageCache, normalize_domain, page_key
from .portable import write_text_utf8, read_text_compat

__all__ = [
    "DEFAULT_ENABLED", "CONFIG_FILE", "PACKETS_DIRNAME", "CACHE_DIRNAME",
    "MAPS_RUNG", "MAPS_COST_ESTIMATE_USD", "DEFAULT_MAX_FETCHES",
    "ENV_OFFLINE", "env_offline", "load_config", "enabled",
    "anchors_from_run", "looks_like_healthcare",
    "nppes_lookup", "default_registry_lookup", "profile_lookup",
    "ChainedCache",
    "emit_packets",
]

DEFAULT_ENABLED = True
#: Set to 1/true to force every packet build into pure cache replay. The test
#: suite sets it, so a default-ON feature can never put the network in a unit
#: test; an explicit `offline=` argument always wins over it.
ENV_OFFLINE = "CRAWFORD_RICH_PACKETS_OFFLINE"
#: Per-run config. mission_intent.json rejects unknown keys by design, so the
#: rich-packet switch lives in its own file rather than bending that contract.
CONFIG_FILE = "rich_packets.json"
PACKETS_DIRNAME = "packets"
#: The separate fetch cache — see the module docstring for why it is separate.
CACHE_DIRNAME = "rich_packet_cache"
MAPS_RUNG = "rich-packets-maps"
#: DataForSEO Google Maps SERP API, Live mode, list price per request. IMPUTED.
MAPS_COST_ESTIMATE_USD = 0.002
#: Run-wide ceiling on network sub-page fetches, so a 50,000-row run cannot
#: turn a default-on feature into an overnight crawl.
DEFAULT_MAX_FETCHES = 500
SUBPAGE_TIMEOUT_S = 15

NPPES_API = "https://npiregistry.cms.hhs.gov/api/"
NPPES_VERSION = "2.1"

_HEALTHCARE_RE = re.compile(
    r"dental|dentist|orthodon|endodon|periodon|oral surgery|medical|health"
    r"|clinic|physician|pediatric|chiropract|physical therapy|podiatr"
    r"|dermatolog|ophthalmolog|optometr|veterinar|pharmac|surgery|surgical"
    r"|urgent care|primary care|behavioral|counseling|hospice|home health"
    r"|nursing|radiolog|cardiolog|oncolog|psychiatr|psycholog", re.I)


# ------------------------------------------------------------------- the gate

def env_offline() -> bool:
    """Whether the environment has pinned packet building to cache replay."""
    return (os.environ.get(ENV_OFFLINE) or "").strip().lower() in (
        "1", "true", "yes", "on")


def load_config(run_dir) -> dict:
    """`<run_dir>/rich_packets.json`, or {} — a broken file is never fatal."""
    path = Path(run_dir) / CONFIG_FILE
    if not path.exists():
        return {}
    try:
        obj = json.loads(read_text_compat(path))
    except (OSError, ValueError):
        return {}
    return obj if isinstance(obj, dict) else {}


def enabled(run_dir, cli=None) -> bool:
    """CLI flag, then the config key `rich_packets`, then ON."""
    if cli is not None:
        return bool(cli)
    value = load_config(run_dir).get("rich_packets")
    if isinstance(value, bool):
        return value
    return DEFAULT_ENABLED


# -------------------------------------------------------------------- ledger

def _ledger(run_dir):
    scripts = str(Path(__file__).resolve().parents[1])
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    import cost_ledger as cl                               # noqa: PLC0415
    return cl.Ledger(Path(run_dir) / "cost_ledger.jsonl")


# -------------------------------------------------------------------- anchors

def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in read_text_compat(path, errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue                  # a malformed line is a skipped row here,
        if isinstance(obj, dict):     # never a failed emission
            rows.append(obj)
    return rows


def detect_jobs(run_dir) -> list[str]:
    """Mirrors pack_build.detect_jobs so packets and packs see one run."""
    run_dir = Path(run_dir)
    jobs = []
    if (run_dir / "entity_match_results.jsonl").exists():
        jobs.append("match")
    if (run_dir / "people.jsonl").exists():
        jobs.append("people")
    if not jobs and (run_dir / "results.jsonl").exists():
        jobs.append("liveness")
    return jobs


def _candidate_domain(row: dict):
    """(domain, provenance) from the row's candidate/evidence EVIDENCE.

    `matched_domain` is preferred when the rungs settled one, and the
    provenance always says which — a packet that cannot say where its domain
    came from is a packet about an unknown site.
    """
    matched = (row.get("matched_domain") or "").strip()
    if matched:
        return normalize_domain(matched) or matched, "matched_domain"
    for cand in row.get("candidates") or []:
        if isinstance(cand, dict):
            host = cand.get("host") or cand.get("domain") or ""
            if host:
                return normalize_domain(host) or host, "candidates[0]"
    for ev in row.get("evidence") or []:
        if isinstance(ev, dict) and ev.get("url"):
            dom = normalize_domain(ev["url"])
            if dom:
                return dom, "evidence[0].url"
    return "", "none"


def anchors_from_run(run_dir, jobs=None) -> dict:
    """`{entity_key: anchor}` for every Job-1/Job-2/Job-3 row in the run.

    The key is the one pack_build uses, so a packet sits beside its pack.
    """
    run_dir = Path(run_dir)
    jobs = list(jobs or detect_jobs(run_dir))
    anchors: dict[str, dict] = {}

    if "match" in jobs:
        for row in _read_jsonl(run_dir / "entity_match_results.jsonl"):
            key = str(row.get("row_id") or "")
            if not key:
                continue
            domain, why = _candidate_domain(row)
            anchors[key] = {
                "entity_key": key, "job": "match",
                "name": row.get("name") or "",
                "city": row.get("city") or "", "state": row.get("state") or "",
                "phone": row.get("phone") or "", "domain": domain,
                "domain_source": f"entity_match_results.jsonl:{key}.{why}",
                "record_source": f"entity_match_results.jsonl:{key}",
            }

    if "liveness" in jobs:
        for row in _read_jsonl(run_dir / "results.jsonl"):
            dom = row.get("domain") or ""
            if not dom:
                continue
            key = normalize_domain(dom) or dom
            anchors.setdefault(key, {
                "entity_key": key, "job": "liveness",
                "name": row.get("name") or dom, "domain": dom,
                "city": row.get("city") or "", "state": row.get("state") or "",
                "phone": row.get("phone") or "",
                "domain_source": f"results.jsonl:{key}.domain",
                "record_source": f"results.jsonl:{key}",
            })

    if "people" in jobs:
        for row in _read_jsonl(run_dir / "people.jsonl"):
            org = row.get("org") or row.get("domain") or row.get("org_domain") or ""
            person = row.get("person_name") or row.get("name") or ""
            if not (org or person):
                continue
            key = f"{org}|{person}" if person else org
            anchors[key] = {
                "entity_key": key, "job": "people",
                "name": person or org, "org": org,
                "domain": normalize_domain(org) if org else "",
                "city": row.get("city") or "", "state": row.get("state") or "",
                "phone": row.get("phone") or "",
                "linkedin_url": row.get("linkedin") or row.get("linkedin_url") or "",
                "title": row.get("title") or "",
                "domain_source": f"people.jsonl:{key}.org",
                "record_source": f"people.jsonl:{key}",
            }
    return anchors


# ------------------------------------------------------------------- registry

def looks_like_healthcare(anchor) -> bool:
    """Whether NPPES is the right registry for this row.

    Name first, then the Maps category when one has already been fetched. A
    false negative costs nothing (the registry block records its absence); a
    false positive costs one free API call.
    """
    blob = " ".join(str(anchor.get(k) or "") for k in
                    ("name", "maps_category", "category", "industry"))
    return bool(_HEALTHCARE_RE.search(blob))


def _nppes_http(url: str, timeout: int = SUBPAGE_TIMEOUT_S) -> dict:
    req = Request(url, headers={
        "User-Agent": os.environ.get("CRAWFORD_USER_AGENT", "gtm-engineer-starter/0.1 (crawford)"),
        "Accept": "application/json"})
    with urlopen(req, timeout=timeout) as resp:            # noqa: S310 — fixed host
        return json.loads(resp.read().decode("utf-8", "replace"))


def nppes_lookup(anchor, *, http_json=None, timeout=SUBPAGE_TIMEOUT_S):
    """One free NPPES query -> the named registry record, or None.

    An `npi` on the anchor is queried BY NUMBER, which is an identifier and
    therefore an exact answer; everything else is a name search, which is a
    guess the packet has to carry as a guess. `http_json(url) -> dict` is
    injectable so tests never touch the network.
    """
    params = {"version": NPPES_VERSION, "limit": 1}
    npi = str(anchor.get("npi") or "").strip()
    if npi:
        params["number"] = npi
    else:
        name = (anchor.get("name") or "").strip()
        if not name:
            return None
        params["organization_name"] = name
        if anchor.get("state"):
            params["state"] = anchor["state"]
    url = NPPES_API + "?" + urlencode(params)
    try:
        payload = (http_json or _nppes_http)(url)
    except Exception:                                      # noqa: BLE001
        return None
    return packets.parse_nppes(payload, source_url=url)


def default_registry_lookup(anchor, *, offline=False, http_json=None):
    """NPPES for healthcare rows; a named seam for everything else.

    The state-registry lane is deliberately NOT built here: fifty scrapers
    behind one function is how a "registry" field starts meaning fifty
    different things. A caller with a state source passes its own
    `registry_lookup`.
    """
    if offline:
        return None
    # An NPI on the row IS the healthcare signal — a registry number nobody
    # carries by accident — so it does not wait on the name heuristic.
    if anchor.get("npi") or looks_like_healthcare(anchor):
        return nppes_lookup(anchor, http_json=http_json)
    return None                       # seam: state registry, caller-supplied


# -------------------------------------------------------------------- profiles

def profile_lookup(anchor, *, client=None, api_key=None):
    """One public profile for a row that carries a LinkedIn URL, or None.

    The client is Crawford's own — `lib/person_profiles.BlitzPersonClient`,
    one record per call against Blitz `/v2/enrichment/person`. Previously
    it was another skill's async scraper; that client is not in the subscriber
    bundle, so half of this lane shipped missing. Pass `client=` to inject a
    fake; tests never touch the network.

    **This lane is OFF unless a caller wires it in**, because it spends vendor
    records and needs `BLITZ_API_KEY`. A row without it — or a row with no
    LinkedIn URL at all — gets a packet whose profile fields are recorded
    ABSENT, which is the honest state, not a person who has no headline.
    """
    url = (anchor.get("linkedin_url") or anchor.get("linkedin") or "").strip()
    if not url:
        return None
    if client is None:
        try:
            client = person_profiles.BlitzPersonClient(api_key)
        except Exception:                                  # noqa: BLE001
            return None                        # no key, no client, no field
    try:
        record = client.fetch(url)
    except Exception:                                      # noqa: BLE001
        return None
    return person_profiles.to_profile(record)


# --------------------------------------------------------------------- caches

class _ChainedCache:
    """Read the run's own page cache first; fetch only what it is missing.

    The run cache is opened OFFLINE, so a homepage always replays the rungs'
    evidence and is never re-fetched. Sub-pages the rungs never saw go to the
    second cache, which lives outside the fingerprinted directory.
    """

    def __init__(self, read_cache, write_cache=None, fetch_page=None,
                 max_fetches=DEFAULT_MAX_FETCHES):
        self.read = read_cache
        self.write = write_cache
        self.fetch_page = fetch_page
        self.max_fetches = max_fetches
        self.fetches = 0
        self.budget_exhausted = False

    @staticmethod
    def _hit(rec, html) -> bool:
        return bool(html) and not (rec or {}).get("error")

    def get_or_fetch_page(self, url):
        try:
            rec, html = self.read.get_or_fetch_page(url)
        except Exception as exc:                           # noqa: BLE001
            rec, html = {"error": f"{type(exc).__name__}: {exc}"[:200]}, ""
        if self._hit(rec, html):
            return dict(rec, from_cache=True), html
        if self.fetch_page is None and self.write is None:
            return rec, html
        if self.fetches >= self.max_fetches:
            self.budget_exhausted = True
            return {"error": "fetch_budget_exhausted", "status": None}, ""
        self.fetches += 1
        try:
            if self.fetch_page is not None:
                rec2, html2 = self.fetch_page(url)
            else:
                rec2, html2 = self.write.get_or_fetch_page(url)
        except Exception as exc:                           # noqa: BLE001
            return {"error": f"{type(exc).__name__}: {exc}"[:200], "status": None}, ""
        return (rec2 if isinstance(rec2, dict) else {}), (html2 or "")


#: The public name. Job 8 (`scripts/build_packets.py`) builds packets from a
#: CSV rather than from a run, and needs exactly these semantics: replay the
#: run's own cache, spend a bounded number of fetches on what it is missing,
#: and call an exhausted budget `fetch_budget_exhausted` rather than an error
#: about the site.
ChainedCache = _ChainedCache


def _skey_map(run_dir, mission) -> dict:
    """key -> pack basename, from the build index when there is one."""
    index = Path(run_dir) / "packs" / "index.jsonl"
    out = {}
    for row in _read_jsonl(index):
        if row.get("mission") in (None, mission) and row.get("key") and row.get("skey"):
            out[str(row["key"])] = str(row["skey"])
    return out


def _sanitize_key(raw: str) -> str:
    """Mirrors pack_build.sanitize_key — the same key names the same file."""
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", str(raw)).strip("._-") or "row"
    return s[:80]


def _short_hash(raw: str) -> str:
    return hashlib.sha1(str(raw).encode()).hexdigest()[:6]


# -------------------------------------------------------------------- emission

def emit_packets(run_dir, *, mission="mission", jobs=None, build_id=None,
                 offline=False, dry_run=False, fetch_subpages=True,
                 max_subpage_fetches=None, max_fetches_total=DEFAULT_MAX_FETCHES,
                 maps_budget_usd=0.0, maps_lookup=None,
                 maps_cost_estimate_usd=MAPS_COST_ESTIMATE_USD,
                 registry_lookup=None, profile_lookup=None, ledger=None,
                 fetch_page=None, limit=None) -> dict:
    """Write one rich packet per entity. Returns a summary; raises only on a
    caller error (a negative budget), never on a data or network failure."""
    run_dir = Path(run_dir)
    if maps_budget_usd is None or float(maps_budget_usd) < 0:
        raise ValueError("maps_budget_usd must be >= 0 — a paid source needs "
                         "an explicit budget, and a negative one is a bug")
    maps_budget_usd = float(maps_budget_usd)
    summary = {
        "emitted": 0, "planned": 0, "dry_run": bool(dry_run),
        "dir": str(run_dir / "packs" / mission / PACKETS_DIRNAME),
        "fetches": 0, "fetch_budget_exhausted": False,
        "maps_calls": 0, "maps_usd": 0.0, "maps_budget_usd": maps_budget_usd,
        "maps_budget_exhausted": False, "registry_hits": 0, "profiles": 0,
        "errors": [], "offline": bool(offline),
    }
    anchors = anchors_from_run(run_dir, jobs=jobs)
    keys = sorted(anchors)
    if not keys:
        # a facts-only build has no Job-1/2/3 row to describe; leave no
        # empty directory behind to be mistaken for an empty answer
        summary["errors"].append("no Job-1/Job-2/Job-3 rows in this run")
        return summary
    if limit:
        keys = keys[:int(limit)]
    summary["planned"] = len(keys)
    if max_subpage_fetches is None:
        max_subpage_fetches = packets.MAX_SUBPAGE_FETCHES
    if dry_run:
        summary["would_fetch_max"] = len(keys) * (1 + int(max_subpage_fetches))
        summary["would_spend_maps_usd"] = round(
            min(maps_budget_usd, len(keys) * maps_cost_estimate_usd), 6
        ) if maps_lookup else 0.0
        return summary

    read_cache = PageCache(run_dir, offline=True)
    write_cache = None
    if not offline and fetch_page is None:
        write_cache = PageCache(run_dir / CACHE_DIRNAME, timeout=SUBPAGE_TIMEOUT_S,
                                fetch_favicon=False, offline=False)
    cache = _ChainedCache(read_cache, write_cache=write_cache,
                          fetch_page=fetch_page, max_fetches=max_fetches_total)

    out_dir = run_dir / "packs" / mission / PACKETS_DIRNAME
    out_dir.mkdir(parents=True, exist_ok=True)
    skeys = _skey_map(run_dir, mission)
    used: dict[str, str] = {}
    index_rows = []
    led = ledger
    if led is None and maps_lookup is not None and maps_budget_usd > 0:
        led = _ledger(run_dir)

    for key in keys:
        anchor = anchors[key]
        try:
            maps_rec = None
            if maps_lookup is not None and maps_budget_usd > 0:
                if summary["maps_usd"] + maps_cost_estimate_usd <= maps_budget_usd + 1e-9:
                    maps_rec, cost = _call_maps(anchor, maps_lookup, led, key,
                                                build_id, summary)
                else:
                    summary["maps_budget_exhausted"] = True
            if maps_rec and anchor.get("maps_category") is None:
                anchor = dict(anchor, maps_category=maps_rec.get("category") or "")

            registry_rec = None
            if registry_lookup is not None:
                registry_rec = _safe(registry_lookup, anchor)
            elif not offline:
                registry_rec = _safe(default_registry_lookup, anchor, offline=offline)
            if registry_rec:
                summary["registry_hits"] += 1

            packet = packets.build_company_packet(
                anchor, page_cache=cache, maps=maps_rec, registry=registry_rec,
                fetch_subpages=fetch_subpages,
                max_subpage_fetches=max_subpage_fetches)

            if anchor.get("job") == "people":
                profile = _safe(profile_lookup, anchor) if profile_lookup else None
                if profile:
                    summary["profiles"] += 1
                packet = packets.build_person_packet(
                    anchor, profile=profile, company_packet=packet)

            packet["crawford"] = {
                "run_dir": str(run_dir), "mission": mission,
                "job": anchor.get("job"), "entity_key": key,
                "build_id": build_id, "domain_source": anchor.get("domain_source"),
                "offline": bool(offline),
            }
            skey = skeys.get(key) or _sanitize_key(key)
            if used.get(skey) not in (None, key):
                skey = f"{skey}-{_short_hash(key)}"
            used[skey] = key
            path = out_dir / f"{skey}.json"
            text = json.dumps(packet, indent=1, sort_keys=True) + "\n"
            write_text_utf8(path, text)
            index_rows.append({
                "key": key, "skey": skey, "mission": mission,
                "job": anchor.get("job"), "packet": str(path),
                "sha256": hashlib.sha256(text.encode()).hexdigest(),
                "absent_fields": len(packet.get("absent") or {}),
            })
            summary["emitted"] += 1
        except Exception as exc:                           # noqa: BLE001
            summary["errors"].append(f"{key}: {type(exc).__name__}: {exc}"[:300])

    summary["fetches"] = cache.fetches
    summary["fetch_budget_exhausted"] = cache.budget_exhausted
    summary["maps_usd"] = round(summary["maps_usd"], 6)
    write_text_utf8(out_dir / "index.jsonl",
        "".join(json.dumps(r, sort_keys=True) + "\n" for r in index_rows))
    return summary


def _safe(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except Exception:                                      # noqa: BLE001
        return None


def _call_maps(anchor, maps_lookup, led, key, build_id, summary):
    """One paid Maps call, logged the moment the provider answers."""
    try:
        got = maps_lookup(anchor)
    except Exception as exc:                               # noqa: BLE001
        summary["errors"].append(f"{key}: maps: {type(exc).__name__}: {exc}"[:200])
        return None, 0.0
    rec, cost = got if isinstance(got, tuple) else (got, MAPS_COST_ESTIMATE_USD)
    cost = float(cost or 0.0)
    summary["maps_calls"] += 1
    summary["maps_usd"] += cost
    if led is not None:
        try:
            led.log(key, provider="dataforseo", usd=cost,
                    action="google-maps-live-advanced", basis="imputed",
                    outcome="ok" if rec else "no_result", rung=MAPS_RUNG,
                    build_id=build_id)
        except Exception as exc:                           # noqa: BLE001
            summary["errors"].append(f"{key}: ledger: {type(exc).__name__}"[:120])
    if rec and "_observed_at" not in rec:
        rec = dict(rec, _observed_at=fetch_cache.now_iso(), _source="dataforseo")
    return (rec or None), cost
