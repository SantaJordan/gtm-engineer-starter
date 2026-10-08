#!/usr/bin/env python3
"""Shared page-cache fetch helper — the ONE fetch path for Job-1 and Job-2.

Contract (SKILL.md page-cache):
  <run_dir>/page_cache/<domain>.html   first 200KB of the FINAL response body
  <run_dir>/page_cache/meta.jsonl      one record per fetched domain:
    {"domain","fetched_at","status","final_url","redirect_chain":[],"title",
     "tls_names":[],"favicon_mmh3","content_len","from_cache"}

check_alive.py (Job 1) WRITES this cache; entity_match.py (Job 2) READS it and
fetches only the domains the cache is missing — through this same code path, so
both pipelines produce byte-identical records.

stdlib-only by design (the system python3 has no httpx/requests): http.client
gives us the redirect CHAIN and the raw TLS socket (for cert SANs), which
urllib's opener hides. GET, never HEAD (too many false 405s — ladder.md Rung 0).

Safety: every connect — including each redirect hop and the favicon probe —
passes the SSRF guard first (is_safe_url: http/https only; no loopback/private/
link-local/reserved/multicast/unspecified/metadata targets). Blocked rows
settle as a clean record with error="blocked_host:<reason>" — never a crash.
A per-registered-domain HostRateLimiter (~2 req/s/host default) spaces
concurrent fetches that land on one apex.

Notes on fields:
  status        int HTTP status of the final hop; None on network failure
                (an "error" field carries the reason — extra fields are fine).
  redirect_chain  URLs VISITED AFTER the initial request (empty = no redirect).
  tls_names     DNS subjectAltNames of the final host's cert ([] for http or
                when the cert could not be read).
  favicon_mmh3  Shodan-convention MurmurHash3-32 of the base64-encoded
                /favicon.ico body (signed int32); None when absent/unreachable.
  from_cache    False in the file; flipped to True on the copy returned from a
                cache hit so callers can tell replay from fresh fetch.
"""
from __future__ import annotations

import base64
import hashlib
import html as html_mod
import http.client
import ipaddress
import json
import os
import re
import socket
import ssl
import sys
import threading
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from .portable import write_text_utf8, read_text_compat

MAX_BYTES = 200 * 1024          # "first 200KB of final body" — the contract
FAVICON_MAX_BYTES = 100 * 1024
MAX_REDIRECTS = 8
DEFAULT_TIMEOUT = 15
#: Set CRAWFORD_USER_AGENT to identify yourself (a contact URL is polite).
USER_AGENT = os.environ.get("CRAWFORD_USER_AGENT",
                            "Mozilla/5.0 (compatible; gtm-engineer-starter/0.1)")
HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "close",
}

TITLE_RE = re.compile(r"(?is)<title[^>]*>(.*?)</title>")

# Optional IDNA2008 codec (pip `idna`). Not required — stdlib-only design holds;
# when absent we fall back to the stdlib IDNA2003 codec and FLAG the few
# characters where the two standards register DIFFERENT domains (ß->ss, Greek
# final sigma, ZWJ/ZWNJ) instead of silently answering about the wrong one.
try:
    import idna as _idna2008
except ImportError:
    _idna2008 = None

_IDNA_DIVERGENT_RE = re.compile("[\u00df\u03c2\u200c\u200d]")  # ss-sharp, final sigma, ZWNJ, ZWJ


def now_iso() -> str:
    """UTC now, or the fixed time in $SOURCE_DATE_EPOCH (the reproducible-builds
    convention) so a replay builds byte-identical packets."""
    fixed = os.environ.get("SOURCE_DATE_EPOCH")
    if fixed:
        return datetime.fromtimestamp(int(fixed), timezone.utc).isoformat(timespec="seconds")
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def idna_ascii(domain: str):
    """Unicode or ASCII domain -> (punycode/ASCII form, note|None).

    The punycode form is the canonical cache key: unicode and xn-- spellings of
    the same domain must collapse to one record. The note flags IDNA2003 vs
    IDNA2008 divergence so callers can surface it in reasoning/evidence."""
    s = domain or ""
    try:
        s.encode("ascii")
        return s, None                       # already ASCII (incl. xn-- forms)
    except UnicodeEncodeError:
        pass
    divergent = bool(_IDNA_DIVERGENT_RE.search(s))
    if _idna2008 is not None:
        try:
            out = _idna2008.encode(s, uts46=True).decode("ascii")
            return out, ("idna2008_divergent_char_handled" if divergent else None)
        except _idna2008.IDNAError:
            pass
    try:
        out = s.encode("idna").decode("ascii")   # stdlib codec == IDNA2003
    except UnicodeError:
        return s, "idna_encode_failed"
    return out, ("idna2003_fallback_on_divergent_char(verdict_may_describe_"
                 "a_different_registration)" if divergent else None)


def normalize_domain_ex(raw: str):
    """normalize_domain plus provenance notes -> (domain, [notes]).
    Notes: "port_stripped" when the input carried an explicit port, and any
    idna_ascii note (IDN inputs) — the guts of Job-1's domain_mapping.csv."""
    s = (raw or "").strip().lower()
    notes: list[str] = []
    if not s:
        return "", notes
    if "://" in s:
        s = urlsplit(s).netloc or ""
    else:
        s = s.split("/", 1)[0]
    s = s.split("@")[-1]
    host, sep, port = s.partition(":")
    if sep and port:
        notes.append("port_stripped")
    s = host.strip(".")
    if s.startswith("www."):
        s = s[4:]
    s, idna_note = idna_ascii(s)
    if idna_note:
        notes.append(idna_note)
    return s, notes


def normalize_domain(raw: str) -> str:
    """URL or bare domain -> cache key: lowercase PUNYCODE host, no
    scheme/path/port/www (unicode and xn-- duplicates collapse to one key)."""
    return normalize_domain_ex(raw)[0]


# Common two-part public suffixes only — a heuristic, deliberately small. The
# Public Suffix List (publicsuffix.org) is the fuller upstream; swap it in if
# ccTLD precision ever starts to matter for a job.
_MULTIPART_SUFFIXES = frozenset({
    "co.uk", "org.uk", "ac.uk", "gov.uk", "me.uk", "net.uk", "ltd.uk", "plc.uk",
    "com.au", "net.au", "org.au", "edu.au", "gov.au",
    "co.nz", "org.nz", "net.nz", "co.jp", "or.jp", "ne.jp", "ac.jp", "go.jp",
    "com.br", "net.br", "org.br", "com.mx", "org.mx", "com.ar",
    "co.in", "net.in", "org.in", "co.za", "org.za", "com.sg", "com.hk",
    "com.tw", "com.cn", "net.cn", "org.cn", "com.tr", "com.pl", "com.co",
    "co.kr", "or.kr", "com.my", "com.ph", "com.vn", "com.ua",
})

# US locality namespace (RFC 1480 / the PSL): <state>.us is a public suffix, so
# ci.exampleton.oh.us and co.samplecounty.oh.us are DIFFERENT registrations
# (exampleton.oh.us vs samplecounty.oh.us) — without these entries every .oh.us city,
# county, and district collapsed to one apex ('oh.us'), which cross-attributed
# people and 'domain-matched' emails across unrelated municipal orgs (the
# flagship gov/municipal vertical). k12/cc/lib.<state>.us are PSL public
# suffixes one level deeper.
_US_STATE_CODES = (
    "al", "ak", "az", "ar", "ca", "co", "ct", "dc", "de", "fl", "ga", "hi",
    "id", "il", "in", "ia", "ks", "ky", "la", "me", "md", "ma", "mi", "mn",
    "ms", "mo", "mt", "ne", "nv", "nh", "nj", "nm", "ny", "nc", "nd", "oh",
    "ok", "or", "pa", "pr", "ri", "sc", "sd", "tn", "tx", "ut", "vt", "va",
    "wa", "wv", "wi", "wy",
)
_MULTIPART_SUFFIXES = _MULTIPART_SUFFIXES | {f"{st}.us" for st in _US_STATE_CODES}
_THREE_PART_SUFFIXES = frozenset(
    f"{kind}.{st}.us" for kind in ("k12", "cc", "lib") for st in _US_STATE_CODES)


def registered_domain(domain: str) -> str:
    """Apex ("registrable") domain: sub.foo.co.uk -> foo.co.uk, a.b.foo.com ->
    foo.com, ci.exampleton.oh.us -> exampleton.oh.us (US locality namespace)."""
    parts = (domain or "").strip(".").split(".")
    if len(parts) <= 2:
        return domain or ""
    if len(parts) >= 4 and ".".join(parts[-3:]) in _THREE_PART_SUFFIXES:
        return ".".join(parts[-4:])
    if ".".join(parts[-2:]) in _MULTIPART_SUFFIXES:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def page_key(url: str) -> str:
    """Cache key for an exact HTTP resource, sharing ordinary homepages only.

    Paths are case-sensitive; queries, trailing slashes, scheme, hostname and
    non-default ports can select different records. Fragments never reach the
    server. Preserve those distinctions before hashing. The versioned namespace
    deliberately does not reuse old ``__p`` entries: their lowercased-path-only
    keys may already hold another record's evidence. Bare/default-port root URLs
    keep the existing domain cache shared by the liveness and match pipelines.
    """
    dom = normalize_domain(url)
    if not dom:
        return ""
    s = str(url or "").strip()
    sp = urlsplit(s if "://" in s else "https://" + s)
    scheme = sp.scheme.lower()
    host, _ = idna_ascii((sp.hostname or "").lower())
    port = sp.port
    if (scheme, port) in (("http", 80), ("https", 443)):
        port = None
    path = sp.path or "/"
    if scheme in ("http", "https") and path == "/" and not sp.query and port is None:
        return dom
    identity = json.dumps([scheme, host, port, path, sp.query],
                          ensure_ascii=True, separators=(",", ":"))
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
    return f"{dom}__p2_{digest}"


def murmur3_32(data: bytes, seed: int = 0) -> int:
    """Pure-python MurmurHash3 x86 32-bit (signed), matching mmh3.hash().
    Ported from Austin Appleby's public-domain reference implementation so the
    favicon hash matches the Shodan/mmh3 convention without a pip install."""
    c1, c2 = 0xCC9E2D51, 0x1B873593
    h = seed & 0xFFFFFFFF
    length = len(data)
    rounded = length & ~3
    for i in range(0, rounded, 4):
        k = int.from_bytes(data[i:i + 4], "little")
        k = (k * c1) & 0xFFFFFFFF
        k = ((k << 15) | (k >> 17)) & 0xFFFFFFFF
        k = (k * c2) & 0xFFFFFFFF
        h ^= k
        h = ((h << 13) | (h >> 19)) & 0xFFFFFFFF
        h = (h * 5 + 0xE6546B64) & 0xFFFFFFFF
    k = 0
    tail = data[rounded:]
    if len(tail) >= 3:
        k ^= tail[2] << 16
    if len(tail) >= 2:
        k ^= tail[1] << 8
    if len(tail) >= 1:
        k ^= tail[0]
        k = (k * c1) & 0xFFFFFFFF
        k = ((k << 15) | (k >> 17)) & 0xFFFFFFFF
        k = (k * c2) & 0xFFFFFFFF
        h ^= k
    h ^= length
    h ^= h >> 16
    h = (h * 0x85EBCA6B) & 0xFFFFFFFF
    h ^= h >> 13
    h = (h * 0xC2B2AE35) & 0xFFFFFFFF
    h ^= h >> 16
    return h - 0x100000000 if h >= 0x80000000 else h


def favicon_hash(body: bytes) -> int:
    """Shodan convention: mmh3 of the base64 (with newlines) of the raw bytes."""
    return murmur3_32(base64.encodebytes(body))


def _decode(body: bytes, ctype: str) -> str:
    m = re.search(r"charset=([\w\-]+)", ctype or "", re.I)
    for enc in ([m.group(1)] if m else []) + ["utf-8"]:
        try:
            return body.decode(enc)
        except (LookupError, UnicodeDecodeError):
            continue
    return body.decode("latin-1", "replace")


def decode_body(body: bytes, ctype: str = "") -> str:
    """Public alias for the charset-sniffing decoder (probe fetches use it too)."""
    return _decode(body, ctype)


def extract_title(text: str) -> str:
    m = TITLE_RE.search(text or "")
    if not m:
        return ""
    return html_mod.unescape(re.sub(r"\s+", " ", m.group(1))).strip()[:300]


class _TextStats(HTMLParser):
    """Visible-text extractor: skips script/style/head etc., counts links and
    anchor-text chars so callers can compute a text/link ratio (link-farm tell)."""
    _SKIP = {"script", "style", "noscript", "template", "svg", "head", "iframe"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.links = 0
        self.anchor_chars = 0
        self._skip = 0
        self._in_a = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip += 1
        elif tag == "a":
            self.links += 1
            self._in_a += 1

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip:
            self._skip -= 1
        elif tag == "a" and self._in_a:
            self._in_a -= 1

    def handle_data(self, data):
        if self._skip:
            return
        t = data.strip()
        if not t:
            return
        self.parts.append(t)
        if self._in_a:
            self.anchor_chars += len(t)


def visible_text_stats(html: str) -> dict:
    """{"text","text_len","links","anchor_chars","anchor_ratio"} for a page body.
    text is whitespace-collapsed visible text (script/style/head stripped)."""
    p = _TextStats()
    try:
        p.feed(html or "")
        p.close()
    except Exception:                    # malformed HTML: keep whatever parsed
        pass
    text = re.sub(r"\s+", " ", " ".join(p.parts)).strip()
    tl = len(text)
    return {"text": text, "text_len": tl, "links": p.links,
            "anchor_chars": p.anchor_chars,
            "anchor_ratio": (p.anchor_chars / tl) if tl else 0.0}


def _peer_dns_names(conn) -> list[str]:
    """DNS subjectAltNames from a live HTTPSConnection's socket ([] on any failure)."""
    try:
        cert = conn.sock.getpeercert()
        return [v for (k, v) in (cert or {}).get("subjectAltName", ()) if k == "DNS"]
    except Exception:
        return []


# -- SSRF guard ---------------------------------------------------------------
# Candidate URLs come from CSVs, search results, and Location headers — any of
# them could point at localhost, RFC-1918 space, or a cloud metadata endpoint.
# Every connect (and every redirect hop) is screened through is_safe_url first.

_METADATA_HOSTS = frozenset({
    "169.254.169.254",              # AWS/GCP/Azure/DO instance metadata IP
    "metadata.google.internal", "metadata.goog",   # GCP
    "instance-data", "metadata",                    # EC2 / generic shorthands
})
_CGNAT_NET = ipaddress.ip_network("100.64.0.0/10")  # shared space (Alibaba metadata lives here)
_BLOCKED_IP_FLAGS = ("is_loopback", "is_link_local", "is_site_local",
                     "is_multicast", "is_unspecified", "is_reserved",
                     "is_private")


class BlockedURL(OSError):
    """The SSRF guard refused this URL. Subclasses OSError so every existing
    `except (OSError, http.client.HTTPException)` fetch handler degrades it to
    a normal per-row fetch error instead of crashing the row."""

    def __init__(self, reason: str, url: str = ""):
        super().__init__(f"blocked_host:{reason}")
        self.reason = reason
        self.url = url


def _ip_blocked(ip) -> str | None:
    """Block-reason for an ipaddress object, or None if it looks public."""
    mapped = getattr(ip, "ipv4_mapped", None)   # ::ffff:10.0.0.1 hides an IPv4
    if mapped is not None:
        ip = mapped
    for flag in _BLOCKED_IP_FLAGS:
        if getattr(ip, flag, False):
            return f"{flag[3:]}_ip"             # e.g. "loopback_ip"
    if ip.version == 4 and ip in _CGNAT_NET:
        return "shared_cgnat_ip"
    return None


def is_safe_url(url: str, resolve: bool = True):
    """(ok, reason) SSRF pre-screen for a candidate URL or bare domain.

    Rejects non-http(s) schemes, cloud-metadata hostnames, and any host whose
    literal or DNS-resolved IPs are loopback/private/link-local/reserved/
    multicast/unspecified. A host that does not resolve returns ok=True — the
    connect fails with the normal gaierror path (no SSRF surface there).
    entity_match/find_people call this to pre-screen candidates; the fetch
    path re-checks before every connect and after every redirect hop."""
    s = str(url or "").strip()
    sp = urlsplit(s if "://" in s else "http://" + s)
    if sp.scheme not in ("http", "https"):
        return False, f"scheme_forbidden:{sp.scheme or 'none'}"
    host = (sp.hostname or "").strip(".").lower()
    if not host:
        return False, "no_host"
    if host in _METADATA_HOSTS:
        return False, f"metadata_host:{host}"
    if host == "localhost" or host.endswith(".localhost"):
        return False, f"local_host:{host}"
    try:
        ip = ipaddress.ip_address(host)         # literal IP in the URL
    except ValueError:
        ip = None
    if ip is not None:
        why = _ip_blocked(ip)
        return (False, f"{why}:{host}") if why else (True, None)
    if not resolve:
        return True, None
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except OSError:
        return True, None                       # unresolvable: gaierror path
    for info in infos:
        addr = str(info[4][0]).split("%")[0]    # strip IPv6 zone id
        try:
            resolved = ipaddress.ip_address(addr)
        except ValueError:
            continue
        why = _ip_blocked(resolved)
        if why:
            return False, f"{why}:{addr}"
    return True, None


def _one_hop(url: str, timeout: int, max_bytes: int):
    """One GET, no redirect following. Returns (status, location, ctype, body,
    tls_names). Raises BlockedURL (an OSError) when the SSRF guard refuses the
    URL; other OSError-family on network failure."""
    ok, reason = is_safe_url(url)
    if not ok:
        raise BlockedURL(reason, url)
    sp = urlsplit(url)
    host, port = sp.hostname, sp.port
    path = sp.path or "/"
    if sp.query:
        path += "?" + sp.query
    tls_names: list[str] = []
    if sp.scheme == "https":
        try:
            ctx = ssl.create_default_context()
            conn = http.client.HTTPSConnection(host, port or 443, timeout=timeout, context=ctx)
            conn.request("GET", path, headers=HEADERS)
            # Cert must be read BEFORE getresponse(): with Connection: close,
            # http.client detaches conn.sock once the response arrives.
            tls_names = _peer_dns_names(conn)
            resp = conn.getresponse()
        except ssl.SSLError:
            # Broken/self-signed certs are routine on small-org sites; the page
            # is still evidence. Unverified retry (SANs unavailable that way).
            ctx = ssl._create_unverified_context()
            conn = http.client.HTTPSConnection(host, port or 443, timeout=timeout, context=ctx)
            conn.request("GET", path, headers=HEADERS)
            resp = conn.getresponse()
    else:
        conn = http.client.HTTPConnection(host, port or 80, timeout=timeout)
        conn.request("GET", path, headers=HEADERS)
        resp = conn.getresponse()
    status = resp.status
    location = resp.getheader("Location")
    ctype = resp.getheader("Content-Type") or ""
    body = b""
    if not (300 <= status < 400 and location):
        try:
            body = resp.read(max_bytes)
        except http.client.IncompleteRead as e:   # server closed early: keep partial
            body = e.partial
        except OSError:                            # read-timeout mid-body: keep status
            pass
    conn.close()
    return status, location, ctype, body, tls_names


def fetch_url(url: str, timeout: int = DEFAULT_TIMEOUT, max_bytes: int = MAX_BYTES):
    """GET with manual redirect following (records the chain).
    Returns (status, final_url, redirect_chain, ctype, body, tls_names)."""
    chain: list[str] = []
    current = url
    for _ in range(MAX_REDIRECTS):
        status, location, ctype, body, tls = _one_hop(current, timeout, max_bytes)
        if 300 <= status < 400 and location:
            current = urljoin(current, location)
            ok, reason = is_safe_url(current)   # re-validate every hop: a public
            if not ok:                          # site must not bounce us inside
                raise BlockedURL(reason, current)
            chain.append(current)
            continue
        return status, current, chain, ctype, body, tls
    return status, current, chain, ctype, b"", tls  # redirect loop: give up


DEFAULT_HOST_MIN_INTERVAL = 0.5     # seconds between hits on one apex (~2 req/s)


class HostRateLimiter:
    """Per-registered-domain politeness throttle, thread-safe. 40 department
    pages on one county site come through a ThreadPoolExecutor at once; without
    spacing the pool hammers that single host. acquire() reserves the next send
    slot for the URL's apex (subdomains share their parent's budget) and sleeps
    until it — distinct apexes never wait on each other. One instance can be
    shared across a whole pool (find_people passes PageCache.rate_limiter)."""

    def __init__(self, min_interval: float = DEFAULT_HOST_MIN_INTERVAL):
        self.min_interval = float(min_interval)
        self._guard = threading.Lock()
        self._next_ok: dict[str, float] = {}    # apex -> earliest next-send time

    def acquire(self, url_or_domain: str) -> float:
        """Block until this caller may hit the host; returns seconds waited."""
        if self.min_interval <= 0:
            return 0.0
        apex = registered_domain(normalize_domain(url_or_domain))
        now = time.monotonic()
        with self._guard:
            slot = max(self._next_ok.get(apex, 0.0), now)
            self._next_ok[apex] = slot + self.min_interval
        wait = slot - now
        if wait > 0:
            time.sleep(wait)
        return wait


class PageCache:
    """Thread-safe page cache over <run_dir>/page_cache/. Safe for pipelines
    running fetches from a ThreadPoolExecutor (per-domain in-flight locks stop
    duplicate fetches; a meta lock serializes the JSONL appends; a shared
    HostRateLimiter spaces concurrent fetches that land on one apex)."""

    def __init__(self, run_dir, timeout: int = DEFAULT_TIMEOUT,
                 fetch_favicon: bool = True, offline: bool = False,
                 rate_limiter: HostRateLimiter | None = None):
        self.dir = Path(run_dir) / "page_cache"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.meta_path = self.dir / "meta.jsonl"
        self.timeout = timeout
        self.fetch_favicon = fetch_favicon
        self.offline = offline
        self.rate_limiter = rate_limiter or HostRateLimiter()
        self.meta: dict[str, dict] = {}
        if self.meta_path.exists():
            for line in read_text_compat(self.meta_path).splitlines():
                if line.strip():
                    try:
                        rec = json.loads(line)
                        self.meta[rec["domain"]] = rec
                    except (json.JSONDecodeError, KeyError):
                        continue
        self._meta_lock = threading.Lock()
        self._locks_guard = threading.Lock()
        self._domain_locks: dict[str, threading.Lock] = {}

    # -- public API ---------------------------------------------------------

    def get(self, domain: str):
        """Cache hit -> (meta dict with from_cache=True, html text); else None."""
        key = normalize_domain(domain)
        rec = self.meta.get(key)
        if rec is None:
            return None
        return dict(rec, from_cache=True), self._read_html(key)

    def get_or_fetch(self, domain: str):
        """The pipeline entrypoint: cached record, or fetch-and-cache it."""
        key = normalize_domain(domain)
        if not key:
            return self._error_record("", "empty_domain"), ""
        hit = self.get(key)
        if hit:
            return hit
        with self._lock_for(key):
            hit = self.get(key)  # another thread may have fetched meanwhile
            if hit:
                return hit
            if self.offline:
                return self._error_record(key, "offline_cache_miss", persist=False), ""
            return self.fetch(key)

    def get_or_fetch_page(self, url: str):
        """Like get_or_fetch, but a path, query or non-default port targets
        THAT resource and caches it under a URL-aware key (page_key) — deep-page
        candidates (city.gov/fire) get scored where the evidence actually
        lives, not on the homepage. Homepage-shaped URLs delegate unchanged."""
        key = page_key(url)
        dom = normalize_domain(url)
        if not dom:
            return self._error_record("", "empty_domain"), ""
        if key == dom:
            return self.get_or_fetch(dom)
        rec = self.meta.get(key)
        if rec is not None:
            return dict(rec, from_cache=True), self._read_html(key)
        with self._lock_for(key):
            rec = self.meta.get(key)
            if rec is not None:
                return dict(rec, from_cache=True), self._read_html(key)
            if self.offline:
                return self._error_record(key, "offline_cache_miss", persist=False), ""
            return self._fetch_page(url, key)

    def fetch(self, domain: str):
        """Unconditional network fetch, then cache. Attempt order: https, http,
        then https/http on www.<domain> when the input is a bare apex (some
        small-org sites only answer on www — false-dead guard)."""
        key = normalize_domain(domain)
        last_err = "unknown"
        attempts = [f"https://{key}/", f"http://{key}/"]
        if key and registered_domain(key) == key:
            attempts += [f"https://www.{key}/", f"http://www.{key}/"]
        for url in attempts:
            self.rate_limiter.acquire(key)
            try:
                status, final_url, chain, ctype, body, tls = fetch_url(url, self.timeout)
            except BlockedURL as e:
                # SSRF guard: same host on every attempt — settle the row now.
                return self._error_record(key, f"blocked_host:{e.reason}"), ""
            except (OSError, http.client.HTTPException) as e:
                last_err = f"{type(e).__name__}: {e}"[:200]
                continue
            text = _decode(body, ctype)
            fav = self._favicon(final_url, key) if (self.fetch_favicon and body) else None
            rec = {
                "domain": key, "fetched_at": now_iso(), "status": status,
                "final_url": final_url, "redirect_chain": chain,
                "title": extract_title(text), "tls_names": tls,
                "favicon_mmh3": fav, "content_len": len(body),
                "from_cache": False,
            }
            if body:
                write_text_utf8(self._html_path(key), text, errors="replace")
            self._persist(rec)
            return rec, text
        return self._error_record(key, last_err), ""

    # -- internals -----------------------------------------------------------

    def _fetch_page(self, url: str, key: str):
        """Network fetch of one specific deep-page URL, cached under `key`
        (a page_key). Same record shape as fetch(); no favicon probe (that
        identity signal belongs to the domain record, not a page record)."""
        s = str(url or "").strip()
        attempts = [s] if "://" in s else [f"https://{s}", f"http://{s}"]
        last_err = "unknown"
        for target in attempts:
            self.rate_limiter.acquire(target)
            try:
                status, final_url, chain, ctype, body, tls = fetch_url(target, self.timeout)
            except BlockedURL as e:
                return self._error_record(key, f"blocked_host:{e.reason}"), ""
            except (OSError, http.client.HTTPException) as e:
                last_err = f"{type(e).__name__}: {e}"[:200]
                continue
            text = _decode(body, ctype)
            rec = {
                "domain": key, "fetched_at": now_iso(), "status": status,
                "final_url": final_url, "redirect_chain": chain,
                "title": extract_title(text), "tls_names": tls,
                "favicon_mmh3": None, "content_len": len(body),
                "from_cache": False,
            }
            if body:
                write_text_utf8(self._html_path(key), text, errors="replace")
            self._persist(rec)
            return rec, text
        return self._error_record(key, last_err), ""

    def _favicon(self, final_url: str, key: str):
        try:
            fav_url = urljoin(final_url or f"https://{key}/", "/favicon.ico")
            self.rate_limiter.acquire(fav_url)
            status, _fu, _c, _ct, body, _t = fetch_url(
                fav_url, self.timeout, FAVICON_MAX_BYTES)
            if status == 200 and body and not body.lstrip()[:1] == b"<":
                # leading "<" = an HTML soft-404 favicon, not an icon — skip it
                return favicon_hash(body)
        except (OSError, http.client.HTTPException):
            pass
        return None

    def _error_record(self, key: str, err: str, persist: bool = True):
        rec = {"domain": key, "fetched_at": now_iso(), "status": None,
               "final_url": "", "redirect_chain": [], "title": "",
               "tls_names": [], "favicon_mmh3": None, "content_len": 0,
               "from_cache": False, "error": err}
        if persist and key:
            self._persist(rec)
        return rec

    def _persist(self, rec: dict):
        with self._meta_lock:
            self.meta[rec["domain"]] = rec
            with self.meta_path.open("a", encoding="utf-8", newline="\n") as f:
                f.write(json.dumps(rec) + "\n")

    def _html_path(self, key: str) -> Path:
        safe = re.sub(r"[^a-z0-9.\-_]", "_", key)
        return self.dir / f"{safe}.html"

    def _read_html(self, key: str) -> str:
        p = self._html_path(key)
        return read_text_compat(p, errors="replace") if p.exists() else ""

    def _lock_for(self, key: str) -> threading.Lock:
        with self._locks_guard:
            return self._domain_locks.setdefault(key, threading.Lock())


if __name__ == "__main__":
    # Debug CLI: fetch_cache.py <run_dir> <domain> [<domain> ...]
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    cache = PageCache(sys.argv[1])
    for d in sys.argv[2:]:
        meta, _text = cache.get_or_fetch(d)
        print(json.dumps(meta))
