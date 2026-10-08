"""Small cross-platform helpers: UTF-8 text files with LF line endings on
every platform (Python 3.9+), reading artifacts an older release wrote in the
Windows code page, the one place the machine-state path is built, a
Windows-safe atomic replace, and UTF-8 console output for CLI entry points on
Windows.

macOS and Linux behave exactly as they always have; every Windows-only branch
is gated on ``os.name == "nt"`` (``_IS_WINDOWS``), so on a Mac the only effect
of this module is that the state path is computed in one place.

Stdlib only.
"""
from __future__ import annotations

import codecs
import io
import os
import re
import socket
import sys
import time
from pathlib import Path

_IS_WINDOWS = os.name == "nt"

# PermissionError from os.replace on Windows means another process (a virus
# scanner, the indexer, a reader that has not closed yet) holds the target open
# for a moment. Retry briefly, then give up and raise the real error.
_REPLACE_DELAYS = (0.05, 0.1, 0.2, 0.4, 0.8)


def replace_with_retry(src, dst) -> None:
    """``os.replace(src, dst)``; on Windows, retry a transient PermissionError
    a few times with short backoff before re-raising it. On macOS/Linux this
    is exactly one ``os.replace`` call."""
    if not _IS_WINDOWS:
        os.replace(src, dst)
        return
    for delay in _REPLACE_DELAYS:
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            time.sleep(delay)
    os.replace(src, dst)


def write_text_utf8(path, text: str, errors: str | None = None) -> int:
    """``Path.write_text`` as UTF-8 with ``\\n`` line endings on every platform.

    ``Path.write_text(newline=...)`` only exists from Python 3.10, and macOS
    ships 3.9 as ``/usr/bin/python3``; without ``newline`` Windows would write
    ``\\r\\n``. Same bytes as ``write_text(text, encoding="utf-8")`` on macOS/Linux.
    """
    with Path(path).open("w", encoding="utf-8", errors=errors, newline="\n") as fh:
        return fh.write(text)


# ---------------------------------------------------------------- legacy reads
#
# Before 2026.09.28.2 many artifacts were written with the platform default
# encoding: UTF-8 on macOS/Linux (so nothing changes there), the ANSI code page
# (cp1252 on most machines) on Windows. On Windows those files must still read
# back exactly. Off Windows these helpers ARE open(encoding="utf-8"): strict,
# native speed. On Windows lines are streamed in bounded binary chunks, split on
# \r\n, \r and \n BEFORE choosing each line's decoder (an append-only JSONL may
# hold old ANSI lines followed by new UTF-8 ones), exactly as text-mode
# iteration splits them; each line is UTF-8 first, else the ANSI code page.

_LINE_BREAK = re.compile(rb"\r\n|\r|\n")
_CHUNK = 1 << 20                                  # bytes read per step on Windows


def _windows_acp():
    """The Windows ANSI code page number (GetACP), or None where unavailable."""
    try:
        import ctypes
        return int(ctypes.windll.kernel32.GetACP())
    except Exception:  # noqa: BLE001 -- not Windows, or no ctypes
        return None


def _legacy_encoding() -> str:
    """The encoding an older Crawford wrote on Windows when it named none: the
    ANSI code page. Asked of Windows directly, so UTF-8 mode (PYTHONUTF8=1,
    which makes the locale report UTF-8) does not hide it; cp1252 otherwise."""
    acp = _windows_acp()
    if acp:
        name = f"cp{acp}"
        try:
            codecs.lookup(name)
            return name
        except LookupError:
            pass
    return "cp1252"


def _is_utf8(name: str) -> bool:
    return name.lower().replace("-", "").replace("_", "") in ("utf8", "utf8sig", "cp65001")


def _decode_line(raw: bytes, legacy: str, errors: str) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as first:
        if not _is_utf8(legacy):
            try:
                return raw.decode(legacy)
            except (UnicodeDecodeError, LookupError):
                pass
        if errors != "strict":
            return raw.decode("utf-8", errors)
        raise first


def _iter_windows(path, errors: str):
    legacy = _legacy_encoding()
    buf = b""
    with open(path, "rb") as fh:
        while True:
            data = fh.read(_CHUNK)
            if not data:
                break
            buf += data
            # a trailing \r may be the first half of a \r\n split across reads
            end = len(buf) - 1 if buf.endswith(b"\r") else len(buf)
            pos = 0
            for m in _LINE_BREAK.finditer(buf, 0, end):
                yield _decode_line(buf[pos:m.start()], legacy, errors) + "\n"
                pos = m.end()
            buf = buf[pos:]
    pos = 0
    for m in _LINE_BREAK.finditer(buf):
        yield _decode_line(buf[pos:m.start()], legacy, errors) + "\n"
        pos = m.end()
    if buf[pos:]:
        yield _decode_line(buf[pos:], legacy, errors)


def iter_lines_compat(path, errors: str = "strict"):
    """The lines of a Crawford artifact, as ``open(path, encoding="utf-8")``
    yields them, streamed. Off Windows that is literally what runs (strict).
    On Windows a line that is not UTF-8 is decoded in the ANSI code page;
    nothing is replaced unless ``errors`` says so."""
    if not _IS_WINDOWS:
        with open(path, encoding="utf-8", errors=errors) as fh:
            yield from fh
        return
    yield from _iter_windows(path, errors)


def read_text_compat(path, errors: str = "strict") -> str:
    """``Path.read_text(encoding="utf-8")``, which it is off Windows. On
    Windows a file that is valid UTF-8 is decoded in one step; otherwise it is
    decoded line by line (see ``iter_lines_compat``)."""
    if not _IS_WINDOWS:
        return Path(path).read_text(encoding="utf-8", errors=errors)
    data = Path(path).read_bytes()
    try:
        return io.StringIO(data.decode("utf-8"), newline=None).read()
    except UnicodeDecodeError:
        return "".join(_iter_windows(path, errors))


# ------------------------------------------------------------------ state dir

def crawford_state_dir(env=None, platform: str | None = None,
                       home: Path | None = None, host: str | None = None) -> Path:
    """Machine-scoped state directory (quota ledger, reviews cooldown).

    Exactly the path Crawford has always used, on every platform:
    ``CRAWFORD_QUOTA_STATE_DIR`` if set and non-empty, else
    ``~/Library/Application Support/gtm-engineer-starter/<host>/crawford``. That path
    is valid (if unconventional) on Windows and Linux too; moving it would risk
    resetting period quota counts, so it deliberately does not move.
    ``platform`` is accepted for tests and ignored.

    The parameters exist for tests; callers pass nothing.
    """
    env = os.environ if env is None else env
    override = env.get("CRAWFORD_QUOTA_STATE_DIR")
    if override:                     # before Path.home()/gethostname(), as always
        return Path(override)
    home = Path.home() if home is None else Path(home)
    host = socket.gethostname() if host is None else host
    return home / "Library" / "Application Support" / "gtm-engineer-starter" / host / "crawford"


def utf8_stdio() -> None:
    """On Windows, make stdout/stderr UTF-8 with replacement so printing an
    entity name with an emoji or a non-Latin script can never crash a job
    (a piped Windows stdout defaults to the ANSI code page). No-op elsewhere."""
    if not _IS_WINDOWS:
        return
    for stream in (sys.stdout, sys.stderr):
        enc = (getattr(stream, "encoding", None) or "").lower().replace("-", "").replace("_", "")
        if enc == "utf8" or not hasattr(stream, "reconfigure"):
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass
