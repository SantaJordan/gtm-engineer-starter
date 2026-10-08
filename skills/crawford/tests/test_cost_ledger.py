"""Strict, concurrent, idempotent cost-ledger accounting."""
from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import cost_ledger as cl  # noqa: E402


class TestCostLedger(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.path = Path(self._td.name) / "cost_ledger.jsonl"

    def _price_table(self, models):
        references = Path(self._td.name) / "references"
        references.mkdir(exist_ok=True)
        (references / "catalog.json").write_text(json.dumps({"models": models}))
        return patch.object(cl, "SKILL", Path(self._td.name))

    def test_model_price_requires_exact_id_or_provider_qualified_id(self):
        models = [
            {"id": "flash-fast", "provider": "vendor", "in": 0.1, "out": 0.2},
            {"id": "flash", "provider": "vendor", "in": 1.0, "out": 2.0},
        ]
        with self._price_table(models):
            # Exact identity wins even when another id contains the same text.
            self.assertEqual(cl.llm_cost("flash", 1_000_000, 1_000_000), 3.0)
            self.assertEqual(cl.llm_cost("vendor/flash", 1_000_000, 1_000_000), 3.0)
            for alias in ("fast", "fla", "", "other/flash"):
                with self.subTest(alias=alias), self.assertRaises(SystemExit):
                    cl.llm_cost(alias, 1_000_000, 1_000_000)

    def test_duplicate_price_identity_and_invalid_rates_are_refused(self):
        model = {"id": "flash", "provider": "vendor", "in": 1, "out": 2}
        with self._price_table([model, dict(model, out=20)]):
            with self.assertRaisesRegex(SystemExit, "ambiguous"):
                cl.llm_cost("flash", 1_000_000, 1_000_000)
        for bad in (-0.1, True, None):
            with self.subTest(rate=bad), self._price_table([dict(model, **{"in": bad})]):
                with self.assertRaises(ValueError):
                    cl.llm_cost("flash", 1_000_000, 1_000_000)

    def test_rejects_nonfinite_negative_costs_and_nonpositive_units(self):
        led = cl.Ledger(self.path)
        for bad in (-0.01, math.nan, math.inf, -math.inf):
            with self.subTest(usd=bad), self.assertRaises(ValueError):
                led.log("R1", "vendor", bad)
        for bad in (0, -1, 1.5, True):
            with self.subTest(units=bad), self.assertRaises(ValueError):
                led.log("R1", "vendor", 0.01, units=bad)

    def test_explicit_unknown_cost_is_not_zero(self):
        led = cl.Ledger(self.path)
        entry = led.log(
            "R1", "vendor", None, basis="unknown",
            event_id="evt-unknown", attempt_id="attempt-unknown",
            outcome="submitted",
        )
        self.assertIsNone(entry["usd"])
        summary = cl.audit(self.path)
        self.assertEqual(summary["unknown_cost_entries"], 1)
        self.assertIsNone(summary["total_usd"])
        self.assertEqual(summary["known_subtotal_usd"], 0.0)

    def test_exact_duplicate_is_idempotent_and_conflict_refuses(self):
        led = cl.Ledger(self.path)
        kw = dict(
            action="search", units=1, basis="measured",
            event_id="evt-stable", attempt_id="attempt-stable",
            outcome="completed", rung="4", build_id="build-1",
            intent_sha="intent-1",
        )
        first = led.log("R1", "vendor", 0.01, **kw)
        again = led.log("R1", "vendor", 0.01, **kw)
        self.assertEqual(first, again)
        self.assertEqual(len(self.path.read_text().splitlines()), 1)
        with self.assertRaises(cl.LedgerConflictError):
            led.log("R1", "vendor", 0.02, **kw)
        self.assertEqual(len(self.path.read_text().splitlines()), 1)

    def test_metadata_and_retry_lineage_roundtrip(self):
        led = cl.Ledger(self.path)
        led.log(
            "R1", "vendor", 0.01, event_id="evt-1",
            attempt_id="attempt-1", outcome="timeout", rung="4",
            build_id="build-1", intent_sha="intent-1",
        )
        led.log(
            "R1", "vendor", 0.02, basis="imputed", event_id="evt-2",
            attempt_id="attempt-2", retry_of="attempt-1",
            outcome="completed", rung="5", build_id="build-1",
            intent_sha="intent-1", provider_run_id="run-2",
        )
        entries = cl.read_entries(self.path)
        self.assertEqual(entries[1]["retry_of"], "attempt-1")
        self.assertEqual(entries[1]["basis"], "imputed")
        self.assertEqual(entries[1]["meta"]["provider_run_id"], "run-2")

    def test_strict_reader_refuses_malformed_or_missing_fields(self):
        self.path.write_text(
            '{"usd": 1.0}\n'
            '{broken\n'
        )
        with self.assertRaises(cl.LedgerAuditError) as ctx:
            cl.read_entries(self.path)
        message = str(ctx.exception)
        self.assertIn("line 1", message)
        self.assertIn("line 2", message)

    def test_strict_reader_refuses_string_cost(self):
        entry = cl.Ledger(self.path).log("R1", "vendor", 1.0)
        entry["usd"] = "1.0"
        self.path.write_text(json.dumps(entry) + "\n")
        with self.assertRaises(cl.LedgerAuditError) as ctx:
            cl.read_entries(self.path)
        self.assertIn("usd must be a JSON number", str(ctx.exception))

    def test_strict_reader_refuses_duplicate_keys_and_invalid_timestamp(self):
        entry = cl.Ledger(self.path).log("R1", "vendor", 1.0)
        duplicate = json.dumps(entry).replace(
            '"usd": 1.0', '"usd": 0.0, "usd": 1.0')
        self.path.write_text(duplicate + "\n")
        with self.assertRaises(cl.LedgerAuditError) as ctx:
            cl.read_entries(self.path)
        self.assertIn("duplicate JSON object key", str(ctx.exception))

        entry["ts"] = "today"
        self.path.write_text(json.dumps(entry) + "\n")
        with self.assertRaises(cl.LedgerAuditError) as ctx:
            cl.read_entries(self.path)
        self.assertIn("ISO-8601 timestamp", str(ctx.exception))

    def test_threaded_writes_are_complete(self):
        led = cl.Ledger(self.path)

        def write(i):
            led.log(
                f"R{i}", "vendor", 0.001,
                event_id=f"evt-{i}", attempt_id=f"attempt-{i}",
            )

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(write, range(80)))
        summary = cl.audit(self.path)
        self.assertEqual(summary["entries"], 80)
        self.assertAlmostEqual(summary["total_usd"], 0.08)

    def test_cross_process_writes_are_complete(self):
        code = (
            "import cost_ledger as c, os\n"
            "p = os.environ['LEDGER']\n"
            "worker = os.environ['WORKER']\n"
            "led = c.Ledger(p)\n"
            "for i in range(20):\n"
            "  led.log(worker + '-' + str(i), 'vendor', 0.001, "
            "event_id='evt-' + worker + '-' + str(i), "
            "attempt_id='attempt-' + worker + '-' + str(i))\n"
        )
        procs = []
        for worker in range(4):
            env = dict(os.environ, LEDGER=str(self.path), WORKER=str(worker),
                       PYTHONPATH=str(SCRIPTS))
            procs.append(subprocess.Popen(
                [sys.executable, "-c", code], cwd=str(SCRIPTS), env=env,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            ))
        for proc in procs:
            _out, err = proc.communicate(timeout=180)
            self.assertEqual(proc.returncode, 0, err)
        summary = cl.audit(self.path)
        self.assertEqual(summary["entries"], 80)
        self.assertAlmostEqual(summary["total_usd"], 0.08)


if __name__ == "__main__":
    unittest.main()
