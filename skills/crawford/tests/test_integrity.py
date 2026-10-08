"""lib/integrity.py — atomic writes, strict JSONL reads, check accounting."""
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from lib import integrity as it  # noqa: E402


# ---------------------------------------------------------------- atomic write

def test_atomic_write_roundtrip(tmp_path):
    p = tmp_path / "deep" / "a.json"
    it.atomic_write_json(p, {"b": 2, "a": 1})
    assert json.loads(p.read_text()) == {"a": 1, "b": 2}
    it.atomic_write_jsonl(p, [{"x": 1}, {"y": 2}])
    rows = [json.loads(l) for l in p.read_text().splitlines()]
    assert rows == [{"x": 1}, {"y": 2}]
    assert not list(tmp_path.rglob("*.tmp.*"))


def test_atomic_write_crash_leaves_original_intact(tmp_path, monkeypatch):
    p = tmp_path / "a.txt"
    it.atomic_write_text(p, "original")

    def boom(src, dst):
        raise OSError("simulated crash at replace")

    monkeypatch.setattr(it.os, "replace", boom)
    with pytest.raises(OSError, match="simulated crash"):
        it.atomic_write_text(p, "replacement")
    assert p.read_text() == "original"
    assert not list(tmp_path.glob("*.tmp.*")), "tmp file must be cleaned up"


# ---------------------------------------------------------------- JSONL reads

def test_lenient_reader_counts_bad_lines_with_numbers(tmp_path):
    p = tmp_path / "v.jsonl"
    p.write_text('{"ok": 1}\n\n{broken\n42\n{"ok": 2}\n')
    rows, bad = it.read_jsonl_lenient(p)
    assert [r["ok"] for r in rows] == [1, 2]
    assert [n for n, _ in bad] == [3, 4]          # broken json + non-object


def test_strict_reader_raises_with_line_numbers(tmp_path):
    p = tmp_path / "v.jsonl"
    p.write_text('{"ok": 1}\n{broken\n')
    with pytest.raises(it.MalformedRecordError, match="line 2"):
        it.read_jsonl_strict(p)


@pytest.mark.parametrize("payload", [
    '{"key":"first","key":"second"}\n',
    '{"cost":NaN}\n',
    '{"cost":Infinity}\n',
])
def test_strict_reader_rejects_ambiguous_or_nonfinite_json(tmp_path, payload):
    p = tmp_path / "v.jsonl"
    p.write_text(payload)
    rows, bad = it.read_jsonl_lenient(p)
    assert rows == [] and bad and bad[0][0] == 1
    with pytest.raises(it.MalformedRecordError, match="line 1"):
        it.read_jsonl_strict(p)


def test_atomic_json_refuses_nonfinite_numbers(tmp_path):
    with pytest.raises(ValueError, match="Out of range float"):
        it.atomic_write_json(tmp_path / "bad.json", {"value": float("nan")})
    with pytest.raises(ValueError, match="Out of range float"):
        it.atomic_write_jsonl(tmp_path / "bad.jsonl",
                              [{"value": float("inf")}])


def test_missing_file_reads_empty(tmp_path):
    assert it.read_jsonl_lenient(tmp_path / "nope.jsonl") == ([], [])
    assert it.read_jsonl_strict(tmp_path / "nope.jsonl") == []


# -------------------------------------------------------------------- hashing

def test_canonical_json_sha_is_key_order_independent():
    assert (it.canonical_json_sha({"a": 1, "b": [2, 3]})
            == it.canonical_json_sha({"b": [2, 3], "a": 1}))
    assert (it.canonical_json_sha({"a": 1})
            != it.canonical_json_sha({"a": 2}))


def test_sha256_file(tmp_path):
    p = tmp_path / "f.bin"
    p.write_bytes(b"abc")
    assert it.sha256_file(p) == (
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")


# ----------------------------------------------------------- check accounting

def test_record_check_upserts_and_validates(tmp_path):
    with pytest.raises(ValueError, match="invalid check status"):
        it.record_check(tmp_path, "x", "passed")
    it.record_check(tmp_path, "b_check", "ran")
    it.record_check(tmp_path, "a_check", "failed", "boom", critical=True)
    it.record_check(tmp_path, "b_check", "skipped", "n/a")     # upsert
    checks = it.read_check_manifest(tmp_path)
    assert [c["name"] for c in checks] == ["a_check", "b_check"]
    assert checks[0]["critical"] is True
    assert checks[1]["status"] == "skipped"


def test_check_manifest_rejects_duplicate_json_keys(tmp_path):
    (tmp_path / it.CHECK_MANIFEST).write_text(
        '{"checks":[],"checks":[{"name":"x","status":"ran"}]}')
    with pytest.raises(ValueError, match="duplicate JSON object key"):
        it.read_check_manifest(tmp_path)


def test_record_check_threaded_updates_do_not_clobber(tmp_path):
    """Atomic replace is not enough: the read/merge/write must be locked."""
    names = [f"thread_{i:03d}" for i in range(40)]
    with ThreadPoolExecutor(max_workers=12) as pool:
        list(pool.map(lambda name: it.record_check(tmp_path, name, "ran"),
                      names))
    assert {c["name"] for c in it.read_check_manifest(tmp_path)} == set(names)


def test_record_check_cross_process_updates_do_not_clobber(tmp_path):
    scripts = Path(__file__).resolve().parent.parent / "scripts"
    workers, per_worker = 6, 12
    env = dict(os.environ)
    env["PYTHONPATH"] = str(scripts)
    procs = []
    for worker in range(workers):
        code = (
            "from lib import integrity as i\n"
            f"run={str(tmp_path)!r}\n"
            f"worker={worker}\n"
            f"for n in range({per_worker}):\n"
            " i.record_check(run, f'proc_{worker}_{n:03d}', 'ran')\n"
        )
        procs.append(subprocess.Popen([sys.executable, "-c", code], env=env,
                                      cwd=str(scripts)))
    for proc in procs:
        assert proc.wait(timeout=60) == 0
    expected = {f"proc_{w}_{n:03d}" for w in range(workers)
                for n in range(per_worker)}
    assert {c["name"] for c in it.read_check_manifest(tmp_path)} == expected


def test_execute_check_isolates_outcomes(tmp_path):
    assert it.execute_check(tmp_path, "ok", lambda: 42) == 42
    assert it.execute_check(tmp_path, "boom",
                            lambda: 1 / 0, critical=True) is None

    def skipper():
        raise it.CheckSkipped("not applicable here")

    assert it.execute_check(tmp_path, "skip", skipper) is None
    by_name = {c["name"]: c for c in it.read_check_manifest(tmp_path)}
    assert by_name["ok"]["status"] == "ran"
    assert by_name["boom"]["status"] == "failed"
    assert by_name["skip"]["status"] == "skipped"
    assert "not applicable" in by_name["skip"]["reason"]


def test_stage_summary_fails_on_failure_or_missing(tmp_path):
    it.record_check(tmp_path, "c1", "ran")
    rec = it.record_stage_summary(tmp_path, "s", ["c1"])
    assert rec["status"] == "ran"
    rec = it.record_stage_summary(tmp_path, "s", ["c1", "never_ran"])
    assert rec["status"] == "failed" and "never recorded" in rec["reason"]
    it.record_check(tmp_path, "c2", "failed", "boom")
    rec = it.record_stage_summary(tmp_path, "s2", ["c1", "c2"])
    assert rec["status"] == "failed" and "boom" in rec["reason"]
