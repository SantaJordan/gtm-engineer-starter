"""Test configuration."""

# --- calibrated-judge mode isolation ------------------------------------------
# The machine's repo .env sets CALIBRATED_JUDGE_MODE and, since 2026-09-18,
# CALIBRATED_JUDGE_ACTIVE_DECISIONS (the per-decision activation list). Tests must
# not depend on that file: this autouse fixture makes every loaded mode reader see
# "no .env" for BOTH settings, so "off unless the env says otherwise" stays true;
# a test that wants the fallback patches it back explicitly.
import sys as _sys
import pytest as _pytest

_MODE_READER_FILES = ("shadow_judge.py", "judge_hook.py", "judge_sidecar.py",
                      "icp_fit_judge.py", "config.py")


@_pytest.fixture(autouse=True)
def _no_env_file_mode_fallback(monkeypatch):
    monkeypatch.delenv("CALIBRATED_JUDGE_MODE", raising=False)
    monkeypatch.delenv("CALIBRATED_JUDGE_ACTIVE_DECISIONS", raising=False)
    for mod in list(_sys.modules.values()):
        f = getattr(mod, "__file__", "") or ""
        if f.endswith(_MODE_READER_FILES) and hasattr(mod, "_mode_from_env_file"):
            monkeypatch.setattr(mod, "_mode_from_env_file", lambda start=None: "", raising=False)
            monkeypatch.setattr(mod, "_active_decisions_from_env_file",
                                lambda start=None: "", raising=False)
    yield


# --- rich packets: cache replay only, always ----------------------------------
# pack_build emits rich evidence packets by DEFAULT, and a packet may fetch up
# to five sub-pages per entity. A unit test must never put the network in that
# path, so the whole suite runs the packet builder in pure replay. A test that
# wants a fetch injects its own `fetch_page`.
@_pytest.fixture(autouse=True)
def _rich_packets_offline(monkeypatch):
    monkeypatch.setenv("CRAWFORD_RICH_PACKETS_OFFLINE", "1")
    yield


# --- the network marker -------------------------------------------------------
# A unit test that resolves a hostname is not a unit test. `probe_templates`
# pre-screens every candidate through `fetch_cache.is_safe_url`, whose SSRF
# check RESOLVES the host (socket.getaddrinfo) before anything else runs — so
# the one test that exercises the real probe path reaches DNS even though its
# pages come from a FakeCache. It is worth keeping and worth not running by
# default: on a plane, on a locked-down runner or behind a captive portal it
# fails for a reason that has nothing to do with the code.
#
#     pytest                      -> network tests are DESELECTED
#     pytest -m network           -> only those
#     pytest --network            -> everything
NETWORK_MARKER = "network"


def pytest_addoption(parser):
    parser.addoption("--network", action="store_true", default=False,
                     help="also run tests marked `network` (they resolve DNS)")


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        f"{NETWORK_MARKER}: touches DNS or the network; deselected by default")


def pytest_collection_modifyitems(config, items):
    if config.getoption("--network"):
        return
    if NETWORK_MARKER in (config.getoption("-m") or ""):
        return
    kept, deselected = [], []
    for item in items:
        (deselected if item.get_closest_marker(NETWORK_MARKER) else kept).append(item)
    if deselected:
        config.hook.pytest_deselected(items=deselected)
        items[:] = kept
