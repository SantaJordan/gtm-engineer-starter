#!/usr/bin/env python3
"""
Wayback time-travel prover — archive.org helper for Phase 2 / Phase 6 / agents.

Turns "sometime in the last two years" into bracket-dated key moments, and
turns "we could have known this" into a provable [ARCHIVED-BEFORE] claim.

CLI:
  moments <url> [--from 2008] [--to YYYY] [--json]
      List content-CHANGE brackets for a page: every capture where the page's
      digest differs from the previous capture (CDX collapse=digest).

  get <url> <YYYYMMDD> [--json]
      Fetch the capture nearest that date, print readable text.

  bracket <url> <YYYYMMDD> <pattern> [--json]
      Answer: "was <pattern> visible on this page at-or-before <date>, and in
      which change-bracket did it FIRST appear?" Returns a knowable-by window
      (absent in capture A, present in capture B → appeared in (A, B]).
      This is the primitive Phase 6 uses to tag evidence [ARCHIVED-BEFORE].

Library use (what backcast_validate.py imports):
  cdx_moments(url, yfrom, yto)   -> {"captures": [...], "truncated": bool}
  get_capture_text(url, day)    -> {"found": bool, "capture_ts": ..., "text": ...}
  bracket(url, day, pattern)    -> {"status": ..., "first_seen_bracket": {...}}

Politeness contract (non-negotiable — agent swarms share this module):
  - 3s minimum spacing between LIVE archive.org hits, enforced with an
    fcntl-flock'd stamp file so CONCURRENT PROCESSES cannot race past it
    (the naive read-sleep-write stamp has a TOCTOU race under swarms).
  - Cache hits never touch the network and never take the lock.
  - Captures are immutable once made, so the cache has NO TTL — a cached
    response is correct forever. Delete the cache dir to force refetch.

Cache location (no hardcoded user paths):
  $WLR_WAYBACK_CACHE_DIR if set, else
  ~/Library/Caches/gtm-engineer-starter/<hostname>/win-loss-rewind/wayback  (macOS)
  ~/.cache/gtm-engineer-starter/<hostname>/win-loss-rewind/wayback          (elsewhere)

Honesty notes:
  - "no captures" is NEVER proof of absence: robots-excluded and never-crawled
    pages look identical to pages that didn't exist. Callers must map
    no-capture to RETRO-ASSUMED / unverifiable, not to signal-absent.
  - JS-rendered SPAs yield mostly-empty text in old captures. Known limit.
  - CDX rows are capped (default 300/query); `truncated: True` flags it.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import html.parser
import json
import os
import re
import socket
import sys
import threading
import time
import zlib
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from pathlib import Path

try:
    import fcntl  # POSIX only — matches house precedent (crawford quota_ledger)
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore

MIN_SPACING_S = 3.0
CDX_ROW_LIMIT = 300
MAX_BRACKET_FETCHES = 12          # binary search over ≤300 versions needs ≤9
COOLDOWN_BASE_S = 60              # first strike: 60s shared cooldown
COOLDOWN_MAX_S = 900              # cap at 15 min
COOLDOWN_INLINE_WAIT_S = 120      # wait inline if remaining ≤ this, else fail fast
UA = {"User-Agent": "winloss-rewind/1.0 (research; contact: "
      + os.environ.get("WLR_CONTACT_EMAIL", "set-WLR_CONTACT_EMAIL@example.invalid") + ")"}


class TimeTravelError(RuntimeError):
    """archive.org unreachable / bad response after retries."""


# ------------------------------------------------------------------ cache dir

def cache_root() -> Path:
    env = os.environ.get("WLR_WAYBACK_CACHE_DIR")
    if env:
        return Path(env)
    host = socket.gethostname().split(".")[0]
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches" / "gtm-engineer-starter"
    else:
        base = Path.home() / ".cache" / "gtm-engineer-starter"
    return base / host / "win-loss-rewind" / "wayback"


def _lock_file() -> Path:
    return cache_root() / ".throttle.lock"


def _stamp_file() -> Path:
    return cache_root() / ".last_hit"


def _cooldown_file() -> Path:
    return cache_root() / ".cooldown.json"


def _read_cooldown() -> dict:
    try:
        return json.loads(_cooldown_file().read_text())
    except (FileNotFoundError, json.JSONDecodeError, ValueError):
        return {"until": 0.0, "strikes": 0}


def _trip_cooldown() -> float:
    """
    Shared circuit breaker: archive.org's replay edge (web.archive.org)
    hard-refuses TCP from IPs that burst page fetches — observed live during
    this build. One strike puts EVERY process sharing this cache into
    exponential cooldown (60s, 120s, … capped 15 min) so a swarm backs off
    as one organism instead of each agent re-tripping the block.
    """
    state = _read_cooldown()
    strikes = state.get("strikes", 0) + 1
    wait = min(COOLDOWN_BASE_S * (2 ** (strikes - 1)), COOLDOWN_MAX_S)
    state = {"until": time.time() + wait, "strikes": strikes}
    _cooldown_file().write_text(json.dumps(state))
    return wait


def _clear_cooldown() -> None:
    state = _read_cooldown()
    if state.get("strikes"):
        _cooldown_file().write_text(json.dumps({"until": 0.0, "strikes": 0}))


# ------------------------------------------------------------------ throttle

_THREAD_LOCK = threading.RLock()
_WARNED_NO_FCNTL = False


@contextmanager
def _throttle_slot():
    """
    Exclusive slot for one live archive.org hit. Sleeps INSIDE the lock so no
    other thread or process can fire within MIN_SPACING_S of the last hit.
    Degrades (with one warning) to best-effort spacing if fcntl is missing —
    a throttle miss risks a 429 that retry/backoff absorbs, so unlike the
    quota ledger we do not hard-fail without flock.
    """
    global _WARNED_NO_FCNTL
    root = cache_root()
    root.mkdir(parents=True, exist_ok=True)
    with _THREAD_LOCK:
        lf = None
        if fcntl is not None:
            lf = _lock_file().open("a+")
            fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
        elif not _WARNED_NO_FCNTL:
            _WARNED_NO_FCNTL = True
            print("[wayback_timetravel] WARN: fcntl unavailable — throttle is "
                  "best-effort only across processes", file=sys.stderr)
        try:
            # Circuit breaker first: a tripped cooldown either waits (short)
            # or fails fast (long) so batch runs degrade to 'unknown' honestly.
            remaining = _read_cooldown().get("until", 0.0) - time.time()
            if remaining > COOLDOWN_INLINE_WAIT_S:
                raise TimeTravelError(
                    f"archive.org cooldown active for {remaining:.0f}s more "
                    "(shared circuit breaker after connection refusals) — "
                    "re-run later; completed fetches are cached")
            if remaining > 0:
                time.sleep(remaining)
            try:
                last = float(_stamp_file().read_text())
            except (FileNotFoundError, ValueError):
                last = 0.0
            wait = MIN_SPACING_S - (time.time() - last)
            if wait > 0:
                time.sleep(wait)
            yield
        finally:
            try:
                _stamp_file().write_text(str(time.time()))
            finally:
                if lf is not None:
                    fcntl.flock(lf.fileno(), fcntl.LOCK_UN)
                    lf.close()


def _decompress(data: bytes) -> bytes:
    """
    Archived captures store the ORIGINAL response body — if the origin server
    sent it gzip/deflate-compressed at capture time, that's what id_ returns.
    Sniff and decompress so callers always get plain bytes.
    """
    if data[:2] == b"\x1f\x8b":
        try:
            return gzip.decompress(data)
        except OSError:
            return data
    if data[:1] == b"\x78":  # zlib/deflate
        try:
            return zlib.decompress(data)
        except zlib.error:
            return data
    return data


def _is_block_signal(err: Exception) -> bool:
    """Refused/reset TCP or HTTP 429 = archive.org telling this IP to back off."""
    if isinstance(err, urllib.error.HTTPError):
        return err.code == 429
    seen = {err, getattr(err, "reason", None), getattr(err, "__cause__", None)}
    return any(isinstance(e, (ConnectionRefusedError, ConnectionResetError))
               for e in seen if e is not None)


def _hit(url: str) -> bytes:
    """
    One live fetch, throttled, 3 attempts. Refusals/429 trip the SHARED
    cooldown breaker (see _trip_cooldown) — the retry then waits it out if
    short, or the whole call fails fast if the block is long.
    """
    last_err: Exception | None = None
    for attempt in (1, 2, 3):
        with _throttle_slot():
            try:
                req = urllib.request.Request(url, headers=UA)
                with urllib.request.urlopen(req, timeout=45) as r:
                    data = _decompress(r.read())
                _clear_cooldown()
                return data
            except Exception as e:
                last_err = e
                if _is_block_signal(e):
                    wait = _trip_cooldown()
                    print(f"[wayback_timetravel] archive.org block signal "
                          f"({type(e).__name__}) — shared cooldown {wait:.0f}s",
                          file=sys.stderr)
        if attempt < 3 and not _is_block_signal(last_err):
            time.sleep(8 * attempt)
    raise TimeTravelError(f"archive.org unreachable after retries: {last_err}")


def _cached(key: str, fetch) -> bytes:
    """Disk cache keyed by SHA-1(key). Hits skip network AND throttle."""
    root = cache_root()
    root.mkdir(parents=True, exist_ok=True)
    path = root / (hashlib.sha1(key.encode()).hexdigest() + ".cache")
    if path.exists():
        return path.read_bytes()
    data = fetch()
    path.write_bytes(data)
    # Sidecar metadata: lets tests verify live-hit spacing, humans debug keys.
    path.with_suffix(".meta.json").write_text(json.dumps(
        {"key": key, "fetched_at": time.time(), "bytes": len(data)}))
    return data


# ------------------------------------------------------------------ text extraction

class _Text(html.parser.HTMLParser):
    SKIP = {"script", "style", "noscript"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self._skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip and data.strip():
            self.out.append(data.strip())


def _html_to_text(body: bytes) -> str:
    p = _Text()
    try:
        p.feed(body.decode("utf-8", errors="replace"))
    except Exception:
        pass
    return "\n".join(p.out)


def _fmt_day(ts: str) -> str:
    return f"{ts[:4]}-{ts[4:6]}-{ts[6:8]}"


def _digits(day: str) -> str:
    return re.sub(r"\D", "", str(day))[:8]


# ------------------------------------------------------------------ core API

def cdx_moments(url: str, yfrom: str = "2008", yto: str | None = None,
                limit: int = CDX_ROW_LIMIT) -> dict:
    """
    Distinct content versions of a page (CDX collapse=digest — archive.org
    itself drops consecutive identical-digest captures, so each row IS a
    content change). Returns capture timestamps oldest-first.
    """
    yto = yto or time.strftime("%Y")
    q = urllib.parse.urlencode({
        "url": url, "output": "json", "fl": "timestamp,statuscode,digest",
        "filter": "statuscode:200", "collapse": "digest",
        "from": yfrom, "to": yto, "limit": str(limit),
    })
    raw = _cached(f"cdx:{url}:{yfrom}:{yto}:{limit}",
                  lambda: _hit(f"https://web.archive.org/cdx/search/cdx?{q}"))
    try:
        rows = json.loads(raw or b"[]")
    except json.JSONDecodeError:
        rows = []
    captures = [{"ts": ts, "day": _fmt_day(ts), "digest": dg}
                for ts, _, dg in rows[1:]] if len(rows) >= 2 else []
    return {
        "url": url,
        "from": yfrom,
        "to": yto,
        "captures": captures,
        "truncated": len(captures) >= limit,   # hit row cap — under-reporting risk
    }


def availability(url: str, day: str | None = None) -> dict:
    """Wayback Availability API — archive.org's own 'closest capture' resolution."""
    ts = _digits(day) if day else ""
    api = f"https://archive.org/wayback/available?url={urllib.parse.quote(url)}"
    if ts:
        api += f"&timestamp={ts}"
    raw = _cached(f"avail:{url}:{ts}", lambda: _hit(api))
    try:
        meta = json.loads(raw or b"{}")
    except json.JSONDecodeError:
        meta = {}
    snap = (meta.get("archived_snapshots") or {}).get("closest") or {}
    return {"url": url, "requested_ts": ts, "closest": snap}


def fetch_capture(url: str, capture_ts: str) -> str:
    """Raw text of one specific capture (id_ modifier = no Wayback toolbar)."""
    fetch_url = f"https://web.archive.org/web/{capture_ts}id_/{url}"
    body = _cached(f"page:{url}:{capture_ts}", lambda: _hit(fetch_url))
    return _html_to_text(body)


def get_capture_text(url: str, day: str, max_chars: int = 6000) -> dict:
    """Nearest capture to `day` → readable text (truncated for display only)."""
    ts = _digits(day)
    snap = availability(url, ts)["closest"]
    if not snap.get("url"):
        return {"found": False, "url": url, "day": day, "text": None,
                "capture_ts": None}
    when = snap["timestamp"]
    text = fetch_capture(url, when)
    return {
        "found": True,
        "url": url,
        "day": day,
        "capture_ts": when,
        "capture_day": _fmt_day(when),
        "chars_total": len(text),
        "text": text[:max_chars] if max_chars else text,
    }


def _pattern_in(text: str, pattern: str) -> bool:
    """Case-insensitive, whitespace-normalized substring match on FULL text."""
    norm = re.sub(r"\s+", " ", text).lower()
    return re.sub(r"\s+", " ", pattern).lower() in norm


def bracket(url: str, day: str, pattern: str,
            yfrom: str = "2008", max_fetches: int = MAX_BRACKET_FETCHES) -> dict:
    """
    The Phase-6 primitive: was `pattern` visible on `url` at-or-before `day`,
    and in which change-bracket did it FIRST appear?

    Binary-searches the distinct content versions at-or-before the reference
    date (each probe = one cached page fetch), so a 300-version history costs
    ≤9 live fetches — and repeat questions against the same page are free.

    Returns status ∈ {present_at_reference, absent_at_reference, no_captures,
    error} with a knowable-by window {"after": A, "before_or_on": B} meaning
    the pattern appeared in (A, B]. "no_captures" is NOT proof of absence.
    """
    ref_ts = _digits(day).ljust(8, "9")  # end-of-day so same-day captures count
    try:
        yto = str(min(int(_digits(day)[:4]), int(time.strftime("%Y"))))
        mom = cdx_moments(url, yfrom=yfrom, yto=yto)
        versions = [c for c in mom["captures"] if c["ts"][:8] <= ref_ts]
        if not versions:
            return {"status": "no_captures", "url": url, "reference_day": day,
                    "note": "no captures at-or-before reference — cannot prove "
                            "presence OR absence (robots-excluded pages look "
                            "identical to nonexistent ones)",
                    "truncated": mom["truncated"]}

        fetches = 0

        def present(idx: int) -> bool:
            nonlocal fetches
            fetches += 1
            return _pattern_in(fetch_capture(url, versions[idx]["ts"]), pattern)

        # 1. Check the latest version at-or-before the reference date.
        last_idx = len(versions) - 1
        if not present(last_idx):
            return {"status": "absent_at_reference", "url": url,
                    "reference_day": day, "pattern": pattern,
                    "checked_capture": versions[last_idx]["day"],
                    "versions_at_or_before": len(versions),
                    "truncated": mom["truncated"], "fetches": fetches}

        # 2. Binary search for the FIRST version where the pattern is present.
        #    Invariant: present at `hi`; unknown below `lo`.
        lo, hi = 0, last_idx
        if present(0):
            first_idx = 0
        else:
            first_idx = hi
            lo = 0
            while hi - lo > 1 and fetches < max_fetches:
                mid = (lo + hi) // 2
                if present(mid):
                    hi = mid
                    first_idx = mid
                else:
                    lo = mid
            first_idx = hi

        first = versions[first_idx]
        prev = versions[first_idx - 1] if first_idx > 0 else None
        return {
            "status": "present_at_reference",
            "url": url,
            "reference_day": day,
            "pattern": pattern,
            "first_seen_bracket": {
                "after": prev["day"] if prev else None,   # null = present since first capture
                "before_or_on": first["day"],
            },
            "present_since_first_capture": prev is None,
            "versions_at_or_before": len(versions),
            "truncated": mom["truncated"],
            "fetches": fetches,
            "budget_exhausted": fetches >= max_fetches,
        }
    except TimeTravelError as e:
        return {"status": "error", "url": url, "reference_day": day,
                "error": str(e)}


def url_variants(url: str) -> list[str]:
    """
    The URL as given plus its www/non-www flip — CDX host matching treats them
    as distinct in some index states; callers should fall back to the variant
    when the primary form has no captures.
    """
    m = re.match(r"^(https?://)?(www\.)?(.+)$", url.strip(), re.I)
    if not m:
        return [url]
    scheme = m.group(1) or "https://"
    rest = m.group(3)
    return [f"{scheme}{rest}", f"{scheme}www.{rest}"] if not m.group(2) \
        else [f"{scheme}www.{rest}", f"{scheme}{rest}"]


# ------------------------------------------------------------------ CLI

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    m = sub.add_parser("moments", help="distinct content versions of a page")
    m.add_argument("url")
    m.add_argument("--from", dest="yfrom", default="2008")
    m.add_argument("--to", dest="yto", default=None)
    m.add_argument("--json", action="store_true")

    g = sub.add_parser("get", help="readable text of capture nearest a date")
    g.add_argument("url")
    g.add_argument("day")
    g.add_argument("--json", action="store_true")

    b = sub.add_parser("bracket",
                       help="was <pattern> visible by <day>, and when did it first appear?")
    b.add_argument("url")
    b.add_argument("day")
    b.add_argument("pattern")
    b.add_argument("--from", dest="yfrom", default="2008")
    b.add_argument("--json", action="store_true")

    a = ap.parse_args()
    try:
        if a.cmd == "moments":
            out = cdx_moments(a.url, a.yfrom, a.yto)
            if a.json:
                print(json.dumps(out, indent=2))
            elif not out["captures"]:
                print(f"no captures found for {a.url} in {out['from']}-{out['to']}")
            else:
                caps = out["captures"]
                print(f"{len(caps)} distinct content versions of {a.url} "
                      f"({out['from']}–{out['to']})"
                      f"{' [TRUNCATED at row cap]' if out['truncated'] else ''}:")
                prev = None
                for c in caps:
                    if prev is None:
                        print(f"  first capture {c['day']}")
                    else:
                        print(f"  CONTENT CHANGED by {c['day']} "
                              f"(previous version last seen {prev})")
                    prev = c["day"]
        elif a.cmd == "get":
            out = get_capture_text(a.url, a.day)
            if a.json:
                print(json.dumps(out, indent=2))
            elif not out["found"]:
                print(f"no capture at-or-near {a.day} for {a.url}")
            else:
                print(f"=== {a.url} as captured {out['capture_day']} ===")
                print(out["text"])
        else:
            out = bracket(a.url, a.day, a.pattern, yfrom=a.yfrom)
            if a.json:
                print(json.dumps(out, indent=2))
            else:
                st = out["status"]
                if st == "present_at_reference":
                    w = out["first_seen_bracket"]
                    since = "present since first capture" if out["present_since_first_capture"] \
                        else f"first appeared after {w['after']}"
                    print(f"PRESENT at {out['reference_day']}: {a.pattern!r} on {a.url}")
                    print(f"  knowable-by window: ({w['after']}, {w['before_or_on']}]  ({since})")
                    print(f"  versions checked: {out['fetches']} of "
                          f"{out['versions_at_or_before']} at-or-before reference")
                elif st == "absent_at_reference":
                    print(f"ABSENT at {out['reference_day']}: {a.pattern!r} not in the "
                          f"{out['checked_capture']} capture of {a.url}")
                elif st == "no_captures":
                    print(f"NO CAPTURES of {a.url} at-or-before {out['reference_day']} — "
                          "cannot prove presence or absence")
                else:
                    print(f"ERROR: {out.get('error')}")
        return 0
    except TimeTravelError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
