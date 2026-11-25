"""Offline regression coverage for missing render evidence."""
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/domain_state_classify.py"
spec = importlib.util.spec_from_file_location("domain_state_classify", SCRIPT)
audit = importlib.util.module_from_spec(spec)
with patch.dict(sys.modules, {"project_env": SimpleNamespace(load=lambda: None)}):
    spec.loader.exec_module(audit)


class DomainStateTest(unittest.TestCase):
    def test_failure_modes_and_success(self):
        domain = "example.test"
        cases = [
            (410, {}, None, "http_410", "not_checked"),
            (429, {}, None, "http_429", "not_checked"),
            (200, {"success": False}, None, "unsuccessful_render", "not_checked"),
            (200, {"success": True, "data": {"markdown": "  "}}, None, "empty_render", "not_checked"),
            (200, None, audit.requests.Timeout(), "error:Timeout", "not_checked"),
            (200, {"success": True, "data": {"markdown": "  ", "html": "<p>Working site</p>"}}, None, "ok", "verified"),
            (200, {"success": True, "data": {"markdown": "this domain is for sale"}}, None, "ok", "parked"),
        ]
        for code, payload, error, expected_status, expected_state in cases:
            with self.subTest(expected_status=expected_status, expected_state=expected_state):
                response = Mock(status_code=code)
                response.json.return_value = payload
                with patch.object(audit.requests, "post", return_value=response, side_effect=error) as post:
                    evidence = audit.firecrawl_render(domain, "fixture-only")
                self.assertEqual(post.call_args.args[0], "https://api.firecrawl.dev/v2/scrape")
                self.assertEqual(evidence["status"], expected_status)
                self.assertEqual(audit.classify(domain, True, evidence), expected_state)
        with patch.object(audit.requests, "post") as post:
            self.assertEqual(audit.classify(domain, True, audit.firecrawl_render(domain, None)), "not_checked")
            post.assert_not_called()

    def test_cli_does_not_upgrade_dns_only_to_verified(self):
        import json

        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            (output / "canonical_domain_picks.json").write_text(json.dumps([
                {"canonical_domain": "example.test"}, {"canonical_domain": "example.test"},
            ]))
            with patch.dict(audit.os.environ, clear=True), patch.object(audit, "check_dns", return_value=True) as dns, patch.object(audit.requests, "post") as post, patch.object(sys, "argv", ["audit", "--audit-slug", "fixture", "--out", folder]):
                audit.main()
            dns.assert_called_once()
            post.assert_not_called()
            rows = json.loads((output / "domain_state_findings.json").read_text())
            self.assertEqual([row["state"] for row in rows], ["not_checked", "not_checked"])


if __name__ == "__main__":
    unittest.main()
