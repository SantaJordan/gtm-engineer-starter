#!/usr/bin/env python3
"""Regression tests for lib/fetch_cache.py — SSRF guard (F2) + HostRateLimiter
(F5). No network: literal-IP cases never resolve; hostname cases patch
socket.getaddrinfo; fetch-path cases patch fc._one_hop / fc.fetch_url.

Run: python3 tests/test_fetch_cache.py
"""
import socket
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

from lib import fetch_cache as fc  # noqa: E402

META_KEYS = {"domain", "fetched_at", "status", "final_url", "redirect_chain",
             "title", "tls_names", "favicon_mmh3", "content_len", "from_cache"}


def _addrinfo(ip):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))]


class TestIsSafeUrl(unittest.TestCase):
    """F2: the public pre-screen helper entity_match/find_people call."""

    def test_forbidden_schemes(self):
        for url in ("file:///etc/passwd", "gopher://x/", "ftp://x/pub",
                    "dict://x:11/", "ldap://x/"):
            ok, reason = fc.is_safe_url(url)
            self.assertFalse(ok, url)
            self.assertTrue(reason.startswith("scheme_forbidden:"), reason)

    def test_literal_blocked_ips(self):
        for host in ("127.0.0.1", "127.8.8.8", "10.0.0.8", "172.16.0.1",
                     "192.168.1.1", "169.254.169.254", "169.254.170.2",
                     "0.0.0.0", "224.0.0.1", "240.0.0.1", "100.100.100.200"):
            ok, reason = fc.is_safe_url(f"http://{host}/")
            self.assertFalse(ok, host)
            self.assertTrue(reason, host)
        self.assertEqual(fc.is_safe_url("http://127.0.0.1/")[1],
                         "loopback_ip:127.0.0.1")
        self.assertEqual(fc.is_safe_url("http://10.0.0.8/x")[1],
                         "private_ip:10.0.0.8")

    def test_metadata_endpoints_blocked_without_dns(self):
        # Cloud metadata: the literal IP AND the well-known hostnames.
        for host in ("169.254.169.254", "metadata.google.internal",
                     "metadata.goog", "instance-data", "metadata"):
            ok, reason = fc.is_safe_url(f"http://{host}/latest/meta-data/")
            self.assertFalse(ok, host)
        self.assertEqual(fc.is_safe_url("http://169.254.169.254/")[1],
                         "metadata_host:169.254.169.254")

    def test_localhost_names_blocked(self):
        for host in ("localhost", "foo.localhost", "LOCALHOST"):
            self.assertFalse(fc.is_safe_url(f"https://{host}:8080/")[0], host)

    def test_ipv6_blocked(self):
        for url in ("http://[::1]/", "http://[fe80::1]/", "http://[::]/",
                    "http://[fd00::1]/", "http://[::ffff:10.0.0.1]/"):
            ok, _reason = fc.is_safe_url(url)
            self.assertFalse(ok, url)

    def test_public_literal_ips_ok(self):
        for host in ("93.184.216.34", "8.8.8.8", "1.1.1.1"):
            self.assertEqual(fc.is_safe_url(f"https://{host}/"), (True, None))

    def test_scheme_less_candidate_is_screened(self):
        # entity_match candidates arrive as bare host/path strings.
        self.assertFalse(fc.is_safe_url("192.168.0.10/admin")[0])
        self.assertEqual(fc.is_safe_url("example.com/fire", resolve=False),
                         (True, None))

    def test_no_host(self):
        self.assertEqual(fc.is_safe_url("http:///path"), (False, "no_host"))

    def test_hostname_resolving_private_blocked(self):
        with mock.patch("socket.getaddrinfo", return_value=_addrinfo("127.0.0.1")):
            ok, reason = fc.is_safe_url("http://internal.corp/")
        self.assertFalse(ok)
        self.assertEqual(reason, "loopback_ip:127.0.0.1")

    def test_hostname_resolving_public_ok(self):
        with mock.patch("socket.getaddrinfo", return_value=_addrinfo("93.184.216.34")):
            self.assertEqual(fc.is_safe_url("http://example.com/"), (True, None))

    def test_any_private_answer_among_many_blocks(self):
        infos = _addrinfo("93.184.216.34") + _addrinfo("10.1.2.3")
        with mock.patch("socket.getaddrinfo", return_value=infos):
            ok, reason = fc.is_safe_url("http://rebind.example/")
        self.assertFalse(ok)
        self.assertEqual(reason, "private_ip:10.1.2.3")

    def test_unresolvable_host_left_to_gaierror_path(self):
        # No SSRF surface if nothing resolves; the fetch must still produce the
        # normal gaierror error record (Stage-B dead classification depends on it).
        with mock.patch("socket.getaddrinfo", side_effect=socket.gaierror("boom")):
            self.assertEqual(fc.is_safe_url("http://nx.example/"), (True, None))


class TestSSRFFetchPath(unittest.TestCase):
    """F2: the guard on the actual fetch path — before every connect and after
    every redirect hop."""

    def test_one_hop_blocks_before_connect(self):
        with self.assertRaises(fc.BlockedURL) as cm:
            fc._one_hop("http://127.0.0.1/", 5, 1024)
        self.assertEqual(cm.exception.reason, "loopback_ip:127.0.0.1")
        self.assertIsInstance(cm.exception, OSError)  # existing handlers catch it

    def test_one_hop_blocks_file_scheme(self):
        with self.assertRaises(fc.BlockedURL):
            fc._one_hop("file:///etc/passwd", 5, 1024)

    def test_redirect_hop_to_metadata_blocked(self):
        hop = mock.Mock(return_value=(
            302, "http://169.254.169.254/latest/meta-data/", "", b"", []))
        with mock.patch.object(fc, "_one_hop", hop):
            with self.assertRaises(fc.BlockedURL) as cm:
                fc.fetch_url("http://public.example/")
        self.assertIn("metadata_host", cm.exception.reason)
        self.assertEqual(hop.call_count, 1)           # never followed the hop

    def test_redirect_hop_to_file_scheme_blocked(self):
        hop = mock.Mock(return_value=(301, "file:///etc/passwd", "", b"", []))
        with mock.patch.object(fc, "_one_hop", hop):
            with self.assertRaises(fc.BlockedURL) as cm:
                fc.fetch_url("http://public.example/")
        self.assertTrue(cm.exception.reason.startswith("scheme_forbidden:"))

    def test_blocked_domain_settles_clean_row(self):
        # Never crash the row: get_or_fetch on a blocked host returns the full
        # meta-record shape with a blocked_host reason, and persists it.
        with tempfile.TemporaryDirectory() as td:
            cache = fc.PageCache(td, fetch_favicon=False)
            rec, html = cache.get_or_fetch("127.0.0.1")
            self.assertIsNone(rec["status"])
            self.assertTrue(rec["error"].startswith("blocked_host:loopback_ip"))
            self.assertEqual(html, "")
            self.assertTrue(META_KEYS.issubset(rec.keys()))
            rec2, _ = cache.get_or_fetch("127.0.0.1")  # persisted -> cache hit
            self.assertTrue(rec2["from_cache"])

    def test_blocked_redirect_settles_clean_row(self):
        hop = mock.Mock(return_value=(
            302, "http://169.254.169.254/latest/meta-data/", "", b"", []))
        with tempfile.TemporaryDirectory() as td:
            cache = fc.PageCache(td, fetch_favicon=False)
            with mock.patch.object(fc, "_one_hop", hop):
                rec, html = cache.get_or_fetch("example.com")
        self.assertIsNone(rec["status"])
        self.assertTrue(rec["error"].startswith("blocked_host:metadata_host"))
        self.assertEqual(hop.call_count, 1)  # blocked once = row settled, no retries

    def test_get_or_fetch_page_blocked(self):
        with tempfile.TemporaryDirectory() as td:
            cache = fc.PageCache(td, fetch_favicon=False)
            rec, html = cache.get_or_fetch_page("http://192.168.0.10/admin")
        self.assertIsNone(rec["status"])
        self.assertTrue(rec["error"].startswith("blocked_host:private_ip"))
        self.assertEqual(html, "")


class TestHostRateLimiter(unittest.TestCase):
    """F5: per-registered-domain spacing shared across a thread pool."""

    def test_same_apex_spaced_subdomains_included(self):
        rl = fc.HostRateLimiter(min_interval=0.05)
        t0 = time.monotonic()
        self.assertEqual(rl.acquire("example.com"), 0.0)
        w2 = rl.acquire("www.example.com")            # same apex
        w3 = rl.acquire("https://fire.example.com/dept")
        elapsed = time.monotonic() - t0
        self.assertGreater(w2, 0.0)
        self.assertGreater(w3, 0.0)
        self.assertGreaterEqual(elapsed, 0.08)        # ~2 waits of 0.05s

    def test_distinct_apexes_do_not_wait(self):
        rl = fc.HostRateLimiter(min_interval=0.5)
        rl.acquire("a.com")
        t0 = time.monotonic()
        self.assertEqual(rl.acquire("b.org"), 0.0)
        self.assertLess(time.monotonic() - t0, 0.2)

    def test_zero_interval_disables(self):
        rl = fc.HostRateLimiter(min_interval=0)
        self.assertEqual(rl.acquire("example.com"), 0.0)
        self.assertEqual(rl.acquire("example.com"), 0.0)

    def test_default_is_about_2_per_second(self):
        self.assertEqual(fc.DEFAULT_HOST_MIN_INTERVAL, 0.5)
        self.assertEqual(fc.HostRateLimiter().min_interval, 0.5)

    def test_thread_pool_on_one_county_site_spaces_out(self):
        # The F5 scenario: N department pages on ONE county apex, concurrent.
        rl = fc.HostRateLimiter(min_interval=0.03)
        urls = [f"https://samplecountyohio.gov/dept{i}" for i in range(4)]
        t0 = time.monotonic()
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(rl.acquire, urls))
        self.assertGreaterEqual(time.monotonic() - t0, 0.085)  # >= 3 intervals


class _RecordingLimiter:
    def __init__(self):
        self.calls = []

    def acquire(self, key):
        self.calls.append(key)
        return 0.0


def _fake_fetch_url(url, timeout=15, max_bytes=fc.MAX_BYTES):
    return (200, "https://example.com/", [], "text/html",
            b"<html><title>X</title><body>hi</body></html>", [])


class TestPageCacheRateLimiterIntegration(unittest.TestCase):
    """F5: get_or_fetch / get_or_fetch_page acquire the limiter around each
    network fetch — and never on a cache hit."""

    def test_get_or_fetch_acquires_once_then_cache_hit(self):
        rl = _RecordingLimiter()
        with tempfile.TemporaryDirectory() as td:
            cache = fc.PageCache(td, fetch_favicon=False, rate_limiter=rl)
            with mock.patch.object(fc, "fetch_url", _fake_fetch_url):
                rec, _ = cache.get_or_fetch("example.com")
                self.assertEqual(rec["status"], 200)
                self.assertEqual(rl.calls, ["example.com"])
                cache.get_or_fetch("example.com")     # hit: no new acquire
                self.assertEqual(rl.calls, ["example.com"])

    def test_get_or_fetch_page_acquires(self):
        rl = _RecordingLimiter()
        with tempfile.TemporaryDirectory() as td:
            cache = fc.PageCache(td, fetch_favicon=False, rate_limiter=rl)
            with mock.patch.object(fc, "fetch_url", _fake_fetch_url):
                cache.get_or_fetch_page("https://example.com/fire")
                self.assertEqual(rl.calls, ["https://example.com/fire"])
                cache.get_or_fetch_page("https://example.com/fire")
                self.assertEqual(len(rl.calls), 1)    # hit: no new acquire

    def test_limiter_exposed_and_shareable(self):
        # find_people shares ONE limiter across its thread pool via this attr.
        with tempfile.TemporaryDirectory() as td:
            cache = fc.PageCache(td)
            self.assertIsInstance(cache.rate_limiter, fc.HostRateLimiter)
            rl = fc.HostRateLimiter(min_interval=0)
            cache2 = fc.PageCache(td, rate_limiter=rl)
            self.assertIs(cache2.rate_limiter, rl)

    def test_signature_backward_compatible(self):
        with tempfile.TemporaryDirectory() as td:
            cache = fc.PageCache(td, timeout=5, fetch_favicon=False, offline=True)
            rec, html = cache.get_or_fetch("example.com")
            self.assertEqual(rec["error"], "offline_cache_miss")


class TestPageResourceIdentity(unittest.TestCase):
    """Different public directory records must never reuse each other's bytes."""

    @staticmethod
    def _resource_fetch(url, timeout=15, max_bytes=fc.MAX_BYTES):
        return (200, url, [], "text/html", url.encode("utf-8"), [])

    def test_query_case_slash_host_scheme_and_port_stay_distinct(self):
        urls = [
            "https://directory.example/Staff?id=1",
            "https://directory.example/Staff?id=2",
            "https://directory.example/staff?id=1",
            "https://directory.example/Staff/?id=1",
            "http://directory.example/Staff?id=1",
            "https://www.directory.example/Staff?id=1",
            "https://directory.example:8443/Staff?id=1",
            "https://directory.example/Staff?id=1&id=2",
            "https://directory.example/Staff?id=2&id=1",
        ]
        with tempfile.TemporaryDirectory() as td:
            cache = fc.PageCache(td, fetch_favicon=False,
                                 rate_limiter=_RecordingLimiter())
            with mock.patch.object(fc, "fetch_url", side_effect=self._resource_fetch) as fetch:
                for url in urls:
                    with self.subTest(url=url):
                        rec, body = cache.get_or_fetch_page(url)
                        self.assertEqual(body, url)
                        self.assertEqual(rec["final_url"], url)
                self.assertEqual(fetch.call_count, len(urls))
            # Exact evidence survives a process restart and needs no new fetch.
            restored = fc.PageCache(td, offline=True)
            for url in urls:
                rec, body = restored.get_or_fetch_page(url)
                self.assertTrue(rec["from_cache"])
                self.assertEqual(body, url)

    def test_root_query_fetches_requested_record_after_homepage_cached(self):
        with tempfile.TemporaryDirectory() as td:
            cache = fc.PageCache(td, fetch_favicon=False,
                                 rate_limiter=_RecordingLimiter())
            with mock.patch.object(fc, "fetch_url", side_effect=self._resource_fetch) as fetch:
                cache.get_or_fetch("directory.example")
                url = "https://directory.example/?record=123"
                rec, body = cache.get_or_fetch_page(url)
                self.assertEqual(body, url)
                self.assertEqual(rec["final_url"], url)
                self.assertEqual(fetch.call_args.args[0], url)
                self.assertEqual(fetch.call_count, 2)

    def test_plain_homepages_still_share_job_one_cache(self):
        with tempfile.TemporaryDirectory() as td:
            cache = fc.PageCache(td, fetch_favicon=False,
                                 rate_limiter=_RecordingLimiter())
            with mock.patch.object(fc, "fetch_url", side_effect=self._resource_fetch) as fetch:
                cache.get_or_fetch("directory.example")
                for url in ("directory.example", "https://directory.example/",
                            "http://www.directory.example/#contact",
                            "https://directory.example:443/"):
                    rec, _ = cache.get_or_fetch_page(url)
                    self.assertTrue(rec["from_cache"])
                self.assertEqual(fetch.call_count, 1)

    def test_nondefault_port_root_is_not_homepage_cache(self):
        url = "https://directory.example:8443/"
        with tempfile.TemporaryDirectory() as td:
            cache = fc.PageCache(td, fetch_favicon=False,
                                 rate_limiter=_RecordingLimiter())
            with mock.patch.object(fc, "fetch_url", side_effect=self._resource_fetch):
                cache.get_or_fetch("directory.example")
                self.assertEqual(cache.get_or_fetch_page(url)[1], url)

    def test_fragments_and_default_ports_reuse_same_deep_resource(self):
        url = "https://directory.example/Staff?id=1"
        with tempfile.TemporaryDirectory() as td:
            cache = fc.PageCache(td, fetch_favicon=False,
                                 rate_limiter=_RecordingLimiter())
            with mock.patch.object(fc, "fetch_url", side_effect=self._resource_fetch) as fetch:
                cache.get_or_fetch_page(url)
                for alias in (url + "#bio", url.replace(".example/", ".example:443/")):
                    rec, body = cache.get_or_fetch_page(alias)
                    self.assertTrue(rec["from_cache"])
                    self.assertEqual(body, url)
                self.assertEqual(fetch.call_count, 1)

    def test_legacy_path_cache_is_not_trusted_even_offline(self):
        url = "https://directory.example/Staff?id=2"
        legacy = "directory.example__p%08x" % (
            fc.murmur3_32(b"staff") & 0xFFFFFFFF)
        with tempfile.TemporaryDirectory() as td:
            old = fc.PageCache(td, fetch_favicon=False)
            old._persist({"domain": legacy, "final_url": url.replace("id=2", "id=1"),
                          "from_cache": False})
            old._html_path(legacy).write_text("Wrong person's evidence")
            offline = fc.PageCache(td, offline=True)
            rec, body = offline.get_or_fetch_page(url)
            self.assertEqual(rec["error"], "offline_cache_miss")
            self.assertEqual(body, "")
            live = fc.PageCache(td, fetch_favicon=False,
                                rate_limiter=_RecordingLimiter())
            with mock.patch.object(fc, "fetch_url", side_effect=self._resource_fetch):
                self.assertEqual(live.get_or_fetch_page(url)[1], url)

    def test_redirect_does_not_alias_another_query_record(self):
        def redirect(url, timeout=15, max_bytes=fc.MAX_BYTES):
            final = url.replace("/lookup?", "/Staff?")
            return (200, final, [final], "text/html", final.encode(), [])

        with tempfile.TemporaryDirectory() as td:
            cache = fc.PageCache(td, fetch_favicon=False,
                                 rate_limiter=_RecordingLimiter())
            with mock.patch.object(fc, "fetch_url", side_effect=redirect) as fetch:
                for record in (1, 2):
                    url = f"https://directory.example/lookup?id={record}"
                    rec, body = cache.get_or_fetch_page(url)
                    self.assertEqual(body, f"https://directory.example/Staff?id={record}")
                    self.assertEqual(rec["redirect_chain"], [rec["final_url"]])
                self.assertEqual(fetch.call_count, 2)

    def test_non_http_root_does_not_bypass_scheme_guard(self):
        with tempfile.TemporaryDirectory() as td:
            cache = fc.PageCache(td, fetch_favicon=False,
                                 rate_limiter=_RecordingLimiter())
            rec, body = cache.get_or_fetch_page("ftp://directory.example/")
            self.assertTrue(rec["error"].startswith("blocked_host:scheme_forbidden:ftp"))
            self.assertEqual(body, "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
