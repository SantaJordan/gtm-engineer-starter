"""One cross-platform advisory lock on an open lock-file handle.

POSIX uses ``fcntl.flock`` exactly as Crawford always has (exclusive or shared,
blocking). Windows has no ``fcntl``; there the lock is ``msvcrt.locking`` on
one byte at offset 0 of the lock file, retried non-blocking until granted, so
it blocks like ``flock`` without msvcrt's 10-second ``LK_LOCK`` give-up. Only
lock contention (EACCES / EDEADLOCK) is retried; any other error is raised
with the lock file's name, so a permanent failure can never hang the caller.
Windows byte-range locks are exclusive only, so a shared request takes the
exclusive lock there: safe, just less concurrent. Locking a byte past EOF is
allowed on Windows, so an empty lock file needs no padding.

Callers keep their fail-closed behavior for the one case that is still
unsafe: a host with neither primitive (``available()`` is False).

Stdlib only.
"""
from __future__ import annotations

import errno
import os
import time

try:
    import fcntl
except ImportError:
    fcntl = None

if fcntl is None:
    try:
        import msvcrt
    except ImportError:
        msvcrt = None
else:
    msvcrt = None

_POLL_SECONDS = 0.05

# The errnos msvcrt.locking raises when the byte is merely held by someone else
# (LK_NBLCK -> EACCES; EDEADLOCK/EDEADLK is the CRT's "gave up" code). Only these
# are retried; anything else (EINVAL, EBADF, ...) is permanent and is raised.
_CONTENTION_ERRNOS = frozenset(
    code for code in (getattr(errno, name, None)
                      for name in ("EACCES", "EDEADLK", "EDEADLOCK"))
    if code is not None)


class LockUnavailableError(RuntimeError):
    """This host has no cross-process file-locking primitive."""


def available() -> bool:
    return fcntl is not None or msvcrt is not None


def acquire(handle, exclusive: bool = True) -> None:
    """Block until the lock on ``handle`` (an open lock file) is held."""
    if fcntl is not None:
        fcntl.flock(handle.fileno(),
                    fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        return
    if msvcrt is not None:
        fd = handle.fileno()
        while True:
            os.lseek(fd, 0, os.SEEK_SET)
            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                return
            except OSError as e:
                if e.errno not in _CONTENTION_ERRNOS:
                    name = getattr(handle, "name", fd)
                    raise OSError(
                        e.errno, f"cannot lock {name}: {e.strerror or e}") from e
            time.sleep(_POLL_SECONDS)
    raise LockUnavailableError(
        "no cross-process file lock (fcntl/msvcrt) on this platform")


def release(handle) -> None:
    if fcntl is not None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return
    if msvcrt is not None:
        fd = handle.fileno()
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        return
    raise LockUnavailableError(
        "no cross-process file lock (fcntl/msvcrt) on this platform")
