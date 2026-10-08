"""Reproduces a cache slowdown on a large shared cache file.

`JsonlCache.get()` re-read the WHOLE shared cache file on every lookup, and `ask()`
builds a fresh `JsonlCache()` per call. With a shared cache of hundreds of MB a scoring
run crawled; pointing JEV_CACHE_PATH at an empty file made it many times faster.

These tests pin the fix: one read of the file per process (shared across instances),
later appends picked up incrementally, and every existing behaviour kept — last write
wins, a torn trailing line is tolerated, a disabled cache never touches the disk.
"""
import builtins
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))

import jev_client as jc  # noqa: E402


def _seed(path, n):
    with path.open("w", encoding="utf-8") as fh:
        for i in range(n):
            fh.write(json.dumps({"key": f"k{i}", "value": {"v": i}}) + "\n")


class _ByteCounter:
    """Counts bytes read from one file, across every open() of it."""

    def __init__(self, monkeypatch, path):
        self.bytes = 0
        target = str(path)
        real_open = builtins.open
        counter = self

        class _Wrap:
            def __init__(self, fh):
                self._fh = fh

            def __iter__(self):
                for line in self._fh:
                    counter.bytes += len(line)
                    yield line

            def read(self, *a):
                data = self._fh.read(*a)
                counter.bytes += len(data)
                return data

            def readline(self, *a):
                data = self._fh.readline(*a)
                counter.bytes += len(data)
                return data

            def __getattr__(self, name):
                return getattr(self._fh, name)

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                self._fh.close()
                return False

        def fake_open(file, mode="r", *a, **kw):
            fh = real_open(file, mode, *a, **kw)
            if str(file) == target and "r" in mode and "+" not in mode:
                return _Wrap(fh)
            return fh

        monkeypatch.setattr(builtins, "open", fake_open)
        real_path_open = pathlib.Path.open

        def fake_path_open(self, mode="r", *a, **kw):
            fh = real_path_open(self, mode, *a, **kw)
            if str(self) == target and "r" in mode and "+" not in mode:
                return _Wrap(fh)
            return fh

        monkeypatch.setattr(pathlib.Path, "open", fake_path_open)


def test_lookups_do_not_rescan_the_whole_file(tmp_path, monkeypatch):
    path = tmp_path / "c.jsonl"
    _seed(path, 3000)
    size = path.stat().st_size
    counter = _ByteCounter(monkeypatch, path)
    # ask() builds a NEW JsonlCache per call, so the index must survive across instances.
    for i in range(0, 3000, 60):
        assert jc.JsonlCache(path).get(f"k{i}") == {"v": i}
    assert jc.JsonlCache(path).get("missing") is None
    # 51 lookups. Rescanning would read ~51x the file; an index reads it about once.
    assert counter.bytes <= 2 * size, f"read {counter.bytes} bytes for a {size}-byte file"


def test_appends_from_another_writer_are_seen(tmp_path):
    path = tmp_path / "c.jsonl"
    _seed(path, 10)
    a = jc.JsonlCache(path)
    assert a.get("k3") == {"v": 3}
    with path.open("a", encoding="utf-8") as fh:            # a different process appends
        fh.write(json.dumps({"key": "new", "value": {"v": "fresh"}}) + "\n")
    assert a.get("new") == {"v": "fresh"}
    assert jc.JsonlCache(path).get("new") == {"v": "fresh"}


def test_put_then_get_and_last_write_wins_with_index(tmp_path):
    path = tmp_path / "c.jsonl"
    _seed(path, 5)
    c = jc.JsonlCache(path)
    assert c.get("k1") == {"v": 1}                          # index built
    c.put("k1", {"v": "over"})
    assert c.get("k1") == {"v": "over"}
    assert jc.JsonlCache(path).get("k1") == {"v": "over"}


def test_torn_tail_completed_later_is_read(tmp_path):
    path = tmp_path / "c.jsonl"
    line = json.dumps({"key": "late", "value": {"v": 9}}) + "\n"
    path.write_text(json.dumps({"key": "k", "value": {"v": 1}}) + "\n" + line[:10], encoding="utf-8")
    c = jc.JsonlCache(path)
    assert c.get("k") == {"v": 1}
    assert c.get("late") is None                            # half-written: not there yet
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line[10:])                                 # writer finishes the line
    assert c.get("late") == {"v": 9}


def test_file_replaced_or_truncated_is_reread(tmp_path):
    path = tmp_path / "c.jsonl"
    _seed(path, 50)
    assert jc.JsonlCache(path).get("k40") == {"v": 40}
    path.write_text(json.dumps({"key": "only", "value": {"v": 1}}) + "\n", encoding="utf-8")
    c = jc.JsonlCache(path)
    assert c.get("only") == {"v": 1}
    assert c.get("k40") is None
