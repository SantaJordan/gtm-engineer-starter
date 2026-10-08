#!/usr/bin/env python3
"""
Phase 6 — Backcasting Validation.

Test every Phase 5 hypothesis against historical signals on the customers
in its archetype. Compute lift + Wald 95% CI on train and holdout cohorts.
Apply survival rules per `references/lift-and-holdout.md`. Surface anti-
overfitting warnings.

Usage:
    python backcast_validate.py --run-id <id>
    python backcast_validate.py --run-id <id> --archetype-id 2
    python backcast_validate.py --run-id <id> --lift-min 1.5 --ci-lower-min 1.2

Inputs:
    outputs/<run-id>/hypotheses.jsonl
    outputs/<run-id>/archetypes.jsonl
    outputs/<run-id>/customer_panels.jsonl

Outputs:
    outputs/<run-id>/validated_signals.jsonl   (every hypothesis, with `survived: bool`)
    outputs/<run-id>/validated_signals.md      (markdown report; survivors first)

Phase 6.5 — TWO-PASS time-travel verification (implemented):
    Pass 1 (provisional): the cheap check against the CURRENT Phase 2 panel.
        This is a FILTER, never a ship decision.
    Pass 2 (firm): hypotheses clearing the provisional bar (top-K per
        archetype by provisional lift) get per-customer archive verification
        anchored at each customer's reference_date (see reference_dates.py):
          - point_in_time_record sources: the record's own intrinsic date
            (+ publication-lag haircut) → RECORD_DATED_BEFORE if it lands
            at-or-before the reference date.
          - wayback-friendly sources: wayback_timetravel.bracket() proves the
            signal text was visible in a capture at-or-before the reference
            date → ARCHIVED_BEFORE, with a bracket-dated knowable-by window.
          - everything else: RETRO_ASSUMED — never firm.
        FIRM lift is recomputed from firm-tagged cells only; RETRO_ASSUMED
        and unchecked cells count as UNKNOWN (excluded), never as absent.
        Only status=="firm" signals enter Phase 7. Thin firm samples are
        reported "unverifiable" — shown, labeled, excluded, never promoted.
    Evidence per checked cell → outputs/<run-id>/wayback_evidence.jsonl.

    Flags: --skip-firm-pass (legacy single-pass), --firm-check-top-k (8),
    --firm-min-coverage (0.30), --firm-min-cell (5),
    --force-firm-check <hypothesis_id> (escape hatch past the top-K filter).
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
from dotenv import load_dotenv
from scipy.stats import fisher_exact

import reference_dates as refd
import signal_presence as sp
import panel_parsers as pp
import wayback_timetravel as wtt
import wlr_schema

load_dotenv()

SKILL_ROOT = Path(__file__).resolve().parent.parent
OUTPUTS_ROOT = SKILL_ROOT / "outputs"
REFERENCES_ROOT = SKILL_ROOT / "references"

DEFAULT_LIFT_MIN = 1.5
DEFAULT_CI_LOWER_MIN = 1.2
SMALL_CELL_THRESHOLD = 5         # if any cell n<5, switch to Fisher's exact
SMALL_ARCHETYPE_THRESHOLD = 20   # if archetype < 20 members, prefer Fisher's

DEFAULT_FIRM_TOP_K = 8           # firm-verify only top-K provisional survivors/archetype
DEFAULT_FIRM_MIN_COVERAGE = 0.30 # min n_firm/n_checked or the signal is unverifiable
DEFAULT_FIRM_MIN_CELL = 5        # min firm-present cells or unverifiable


GOOD_FIT_OUTCOMES = wlr_schema.GOOD_FIT_OUTCOMES
NOT_GOOD_FIT_OUTCOMES = wlr_schema.NOT_GOOD_FIT_OUTCOMES


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        raise SystemExit(f"Error: required file missing — {path}")
    rows = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


# ──────────────────────────────────────────────────────────────
# Signal presence — shared with Phases 7 and 8 (scripts/signal_presence.py).
# ──────────────────────────────────────────────────────────────


# `signal_presence.signal_state()` is the one presence brain; Phase 6 keeps the
# tri-state reading — None means the source was never observed (counted in the
# null-rate diagnostic, never coerced to absent).
signal_present_in_panel = sp.signal_present_tristate


def classify_outcome(panel: dict) -> str:
    """The VERIFIED outcome (wlr_schema): label > churned health > CRM outcome."""
    return wlr_schema.verified_outcome(panel)


# ──────────────────────────────────────────────────────────────
# Lift + CI math
# ──────────────────────────────────────────────────────────────


def compute_lift_with_ci(a: int, b: int, c: int, d: int) -> dict:
    """
    2x2 table:
                  signal=1   signal=0
        good_fit    a          c
        not_fit     b          d

    lift = (a / (a+b)) / (c / (c+d))
    Wald CI on log-lift.
    """
    n_signal = a + b
    n_no_signal = c + d
    n_good_signal = a
    n_good_no_signal = c

    total = a + b + c + d
    use_fisher = (
        min(a, b, c, d) < SMALL_CELL_THRESHOLD
        or n_signal == 0
        or n_no_signal == 0
        or n_good_signal == 0
        or n_good_no_signal == 0
    )

    out: dict = {
        "n_total": total,
        "cells": {"a_good_signal": a, "b_notfit_signal": b,
                  "c_good_nosignal": c, "d_notfit_nosignal": d},
        "method": "wald_log_lift",
        "lift": None,
        "ci_lower": None,
        "ci_upper": None,
        "p_good_given_signal": (a / n_signal) if n_signal else None,
        "p_good_given_no_signal": (c / n_no_signal) if n_no_signal else None,
        "fisher_p_value": None,
        "fisher_odds_ratio": None,
    }

    if use_fisher:
        # Small cells: report the SAME effect measure as the large-sample
        # branch (lift = probability ratio), plus Fisher's exact p-values. The
        # odds ratio is reported separately and never stands in for lift.
        out["method"] = "fisher_exact"
        try:
            odds, p = fisher_exact([[a, b], [c, d]], alternative="two-sided")
            _, p_greater = fisher_exact([[a, b], [c, d]], alternative="greater")
            out["fisher_odds_ratio"] = float(odds) if math.isfinite(odds) else None
            out["fisher_p_value"] = float(p)
            out["fisher_p_value_greater"] = float(p_greater)
        except Exception as e:
            out["error"] = f"fisher_failed: {e}"
        if n_signal and n_no_signal:
            p_s, p_n = a / n_signal, c / n_no_signal
            out["lift"] = (p_s / p_n) if p_n > 0 else float("inf")
        else:
            out["lift"] = float("nan")
        return out

    p_signal = a / n_signal
    p_no_signal = c / n_no_signal
    if p_no_signal == 0:
        out["lift"] = float("inf")
        return out

    lift = p_signal / p_no_signal
    log_lift = math.log(lift)
    se = math.sqrt(1.0 / a - 1.0 / n_signal + 1.0 / c - 1.0 / n_no_signal)
    ci_lower = math.exp(log_lift - 1.96 * se)
    ci_upper = math.exp(log_lift + 1.96 * se)

    out["lift"] = lift
    out["ci_lower"] = ci_lower
    out["ci_upper"] = ci_upper
    return out


# ──────────────────────────────────────────────────────────────
# Anti-overfitting checks
# ──────────────────────────────────────────────────────────────


def data_artifact_check(
    hypothesis: dict,
    panels_in_archetype: list[dict],
) -> dict:
    """
    Warn if the signal correlates with whether the source data is present
    at all — i.e., null-rate differs >30% between good_fit and not_good_fit.
    """
    source = (hypothesis.get("observability_source") or "").strip()
    null_good = 0; tot_good = 0
    null_bad = 0; tot_bad = 0
    for p in panels_in_archetype:
        outcome = classify_outcome(p)
        signals = p.get("signal_panel") or p.get("signals") or {}
        is_null = source not in signals or signals.get(source) in (None, {}, [])
        if outcome in GOOD_FIT_OUTCOMES:
            tot_good += 1
            if is_null:
                null_good += 1
        elif outcome in NOT_GOOD_FIT_OUTCOMES:
            tot_bad += 1
            if is_null:
                null_bad += 1
    rate_good = (null_good / tot_good) if tot_good else 0.0
    rate_bad = (null_bad / tot_bad) if tot_bad else 0.0
    diff = abs(rate_good - rate_bad)
    return {
        "null_rate_good_fit": rate_good,
        "null_rate_not_good_fit": rate_bad,
        "diff": diff,
        "flag": diff > 0.30,
    }


_MANIFEST_CACHE: dict | None = None


def load_source_manifest() -> dict:
    """references/source-temporal-classification.json — the temporal manifest."""
    global _MANIFEST_CACHE
    if _MANIFEST_CACHE is None:
        path = REFERENCES_ROOT / "source-temporal-classification.json"
        try:
            _MANIFEST_CACHE = json.loads(path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            _MANIFEST_CACHE = {"sources": {}, "default_unclassified": {
                "class": "current_state_only", "wayback_friendly": "unknown"}}
    return _MANIFEST_CACHE


def classify_source(source: str) -> dict:
    m = load_source_manifest()
    return m["sources"].get(source, m.get("default_unclassified", {}))


def merge_vertical_sources(run_dir: Path) -> int:
    """Fold Phase 4.5 scout sources (vertical_sources.jsonl) into the manifest
    cache so firm verification routes them like built-ins. Returns count."""
    path = run_dir / "vertical_sources.jsonl"
    if not path.exists():
        return 0
    m = load_source_manifest()
    n = 0
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            key = (r.get("source_key") or "").strip()
            if not key:
                continue
            m["sources"][key] = {
                "class": r.get("class", "current_state_only"),
                "wayback_friendly": r.get("wayback_friendly", "unknown"),
                "publication_lag_days": r.get("publication_lag_days"),
                "record_date_field": r.get("record_date_field"),
                "opportunistic_url_field": r.get("opportunistic_url_field"),
            }
            n += 1
    return n


def outcome_leakage_check(hypothesis: dict, firm_stats: dict | None = None) -> dict:
    """
    Real implementation (was a stub): a current-state-only source whose
    hypothesis earned NO firm evidence in Pass 2 is leaking today's state
    into a claim about the past — flag it.
    """
    source = (hypothesis.get("observability_source") or "").strip()
    cls = classify_source(source)
    current_state = cls.get("class") == "current_state_only"
    firm_count = (firm_stats or {}).get("n_firm_present", 0)
    flag = bool(current_state and firm_count == 0)
    return {
        "checked": True,
        "flag": flag,
        "source_class": cls.get("class", "unclassified"),
        "wayback_friendly": cls.get("wayback_friendly", "unknown"),
        "firm_present_cells": firm_count,
        "detail": ("current-state-only source with zero firm-dated evidence — "
                   "the 'historical' claim is today's web wearing a costume"
                   if flag else "ok"),
    }


# ──────────────────────────────────────────────────────────────
# Pass 2 — firm verification per (hypothesis, customer) cell
# ──────────────────────────────────────────────────────────────


def _dotted_get(blob, path: str):
    cur = blob
    for part in path.split("."):
        part = part.strip()
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list):
            vals = [x.get(part) for x in cur if isinstance(x, dict) and part in x]
            cur = vals or None
        else:
            return None
    return cur


def _record_dates_from_panel(hypothesis: dict, panel: dict, cls: dict) -> list:
    """Intrinsic dates for a point_in_time_record source, from the panel blob."""
    source = (hypothesis.get("observability_source") or "").strip()
    signals = panel.get("signal_panel") or panel.get("signals") or {}
    blob = (signals.get(source) or {}).get("data") if isinstance(signals.get(source), dict) \
        else signals.get(source)
    if blob is None:
        return []
    date_field = hypothesis.get("record_date_field") or cls.get("record_date_field") or ""
    # Strip the source prefix if the manifest wrote e.g. "results.published_date".
    raw = _dotted_get(blob, date_field) if date_field else None
    vals = raw if isinstance(raw, list) else [raw]
    out = []
    for v in vals:
        d = refd.parse_date(v)
        if d:
            out.append(d)
    return out


def _signal_pattern(hypothesis: dict) -> str:
    """
    Text pattern bracket() searches for in captures. Explicit
    `archive_search_pattern` (Phase 5 emits it) beats a heuristic fallback
    from the signal description.
    """
    # No heuristic fallback: words lifted from the signal's description can
    # match an unrelated page and manufacture "firm" evidence. Without an
    # explicit pattern the archive route returns unknown.
    return (hypothesis.get("archive_search_pattern") or "").strip()


#: Sources whose Phase 2 fields can be re-extracted from an archived page:
#: source -> function(archived page text) -> panel blob (the live parser).
ARCHIVE_PARSERS = {
    "firecrawl_scrape": lambda text: pp.parse_firecrawl_scrape({"data": {"markdown": text}}),
}


def _archive_cell(cell: dict, hypothesis: dict, source: str, url: str, ref_date) -> dict:
    """Evaluate the typed predicate on the latest capture at-or-before the reference."""
    day = ref_date.strftime("%Y%m%d")
    caps = []
    for u in wtt.url_variants(url):
        try:
            mom = wtt.cdx_moments(u, yto=day[:4])
        except Exception as exc:                 # noqa: BLE001 — archive outage = unknown
            cell["evidence_detail"] = f"unknown: archive lookup failed ({exc.__class__.__name__})"
            return cell
        caps = [c for c in mom.get("captures") or [] if str(c.get("ts", ""))[:8] <= day]
        if caps:
            url = u
            break
    if not caps:
        cell["evidence_detail"] = "unknown: no archived capture at-or-before the reference date"
        return cell
    cap = caps[-1]
    try:
        text = wtt.fetch_capture(url, cap["ts"])
    except Exception as exc:                     # noqa: BLE001
        cell["evidence_detail"] = f"unknown: archived capture could not be read ({exc.__class__.__name__})"
        return cell
    blob = ARCHIVE_PARSERS[source](text or "")
    ev = sp.evaluate(hypothesis, {"signal_panel": {source: blob}})
    field = sp.field_type(hypothesis)
    value = ev.get("value")
    detail = {"route": "archived_page", "url": url, "capture": cap.get("day") or str(cap["ts"])[:8],
              "field": hypothesis.get("queryable_field"), "extracted_value": value,
              "predicate": hypothesis.get("predicate"), "excerpt": (text or "")[:300]}
    if ev["state"] == sp.PRESENT:
        kb = refd.parse_date(detail["capture"])
        cell.update({"visibility_tag": refd.ARCHIVED_BEFORE,
                     "knowable_by_window": {"after": None, "before_or_on": detail["capture"]},
                     "strike_window_days": refd.strike_window_days(ref_date, kb),
                     "firm_present": True, "evidence_detail": detail})
    elif ev["state"] == sp.ABSENT:
        # The archived page WAS observed on that day and the predicate fails on it.
        cell.update({"firm_present": False, "evidence_detail": dict(
            detail, note="predicate false on the latest capture at-or-before the reference")})
    else:
        cell["evidence_detail"] = dict(detail, note=f"unknown: {ev['reason']} on the archived page")
    del field
    return cell


def verify_cell_firm(hypothesis: dict, panel: dict, provisional_present: bool | None) -> dict:
    """
    One (hypothesis, customer) cell → evidence record with a visibility tag.
    Returns firm_present ∈ {True, False, None}: True = firm-tagged present,
    False = provably absent at reference, None = unknown (never coerced).
    """
    ref_date, ref_note = refd.resolve_reference_date(panel)
    cell = {
        "customer_id": panel.get("customer_id"),
        "hypothesis_id": hypothesis.get("hypothesis_id"),
        "reference_date": str(ref_date) if ref_date else None,
        "reference_date_provenance": ref_note,
        "visibility_tag": refd.RETRO_ASSUMED,
        "knowable_by_window": None,
        "strike_window_days": None,
        "firm_present": None,
        "evidence_detail": None,
    }
    if ref_date is None:
        cell["evidence_detail"] = "unanchorable reference date"
        return cell

    source = (hypothesis.get("observability_source") or "").strip()
    cls = classify_source(source)

    # Route 1 — dated record. Only a record that ITSELF satisfies the signal's
    # predicate can date the signal: an old, unrelated record proves nothing.
    # The satisfying record's URL, date and excerpt are kept as evidence.
    if cls.get("class") == "point_in_time_record" and source != "wayback":
        lag = cls.get("publication_lag_days") or 0
        ev = sp.evaluate(hypothesis, panel)
        kind = sp.field_type(hypothesis)[0]
        if kind != "record":
            cell["evidence_detail"] = (
                "unknown: a point-in-time source can only date a RECORD-level signal "
                f"(e.g. items.snippet); this signal reads a field ({ev['reason']})")
            return cell
        if ev["state"] != sp.PRESENT:
            cell["evidence_detail"] = (
                f"unknown: no record satisfies the signal ({ev['reason']}); "
                "a search result set cannot prove absence")
            return cell
        dfield = (hypothesis.get("record_date_field") or sp.record_date_field(hypothesis) or "").split(".")[-1]
        dated = []
        for rec in ev["matches"]:
            d = refd.parse_date(rec.get(dfield)) if dfield else None
            if d:
                dated.append((refd.knowable_by(d, lag), d, rec))
        if not dated:
            cell["evidence_detail"] = "unknown: the satisfying record(s) carry no date"
            return cell
        dated.sort(key=lambda t: t[0])
        k, d, rec = dated[0]
        _, _, rkey, rfield = sp.field_type(hypothesis)
        evidence = {"route": "dated_record", "record_date": str(d),
                    "url": rec.get("link") or rec.get("url"),
                    "excerpt": str(rec.get(rfield) or "")[:300],
                    "publication_lag_days": lag}
        if k <= ref_date:
            cell.update({
                "visibility_tag": refd.RECORD_DATED_BEFORE,
                "knowable_by_window": {"after": None, "before_or_on": str(k)},
                "strike_window_days": refd.strike_window_days(ref_date, k),
                "firm_present": True,
                "evidence_detail": evidence,
            })
            return cell
        # A matching record dated AFTER the reference does not prove that no
        # earlier matching record existed: a search result set is not a census.
        cell["evidence_detail"] = dict(evidence, note=(
            f"unknown: the earliest satisfying record found was knowable {k}, after the "
            f"reference {ref_date}; that cannot prove the signal was absent then"))
        return cell

    # Route 2 — archived page. The SAME typed predicate is evaluated on fields
    # re-extracted from the archived page by the same Phase 2 parser; a text
    # substring never certifies a signal. Sources whose fields cannot be
    # re-extracted from an archived page stay unknown.
    if cls.get("wayback_friendly") in (True, "opportunistic"):
        if source not in ARCHIVE_PARSERS:
            cell["evidence_detail"] = (
                f"unknown: {source}'s fields cannot be re-extracted from an archived page "
                f"(re-extractable sources: {sorted(ARCHIVE_PARSERS)})")
            return cell
        domain = (panel.get("domain") or "").strip()
        if not domain:
            cell["evidence_detail"] = "unknown: no domain to look up in the archive"
            return cell
        return _archive_cell(cell, hypothesis, source, f"https://{domain}", ref_date)

    # Route 3 — unverifiable source (e.g., LinkedIn): capped at RETRO_ASSUMED.
    cell["evidence_detail"] = (
        f"source '{source}' is {cls.get('class','unclassified')} and not "
        f"wayback-friendly — evidence capped at RETRO_ASSUMED"
        + ("" if provisional_present is None
           else f" (today's panel says present={provisional_present})"))
    return cell


def firm_contingency(hypothesis: dict, panels: list[dict],
                     evidence_out: list[dict]) -> tuple[int, int, int, int, dict]:
    """
    2x2 table from FIRM evidence only. RETRO_ASSUMED / unknown cells are
    excluded (unknown), never coerced to absent. Appends every checked cell
    to evidence_out.
    """
    a = b = c = d = 0
    n_firm_present = n_firm_absent = n_retro = n_unknown = 0
    for p in panels:
        outcome = classify_outcome(p)
        if outcome in GOOD_FIT_OUTCOMES:
            good = True
        elif outcome in NOT_GOOD_FIT_OUTCOMES:
            good = False
        else:
            continue
        prov = signal_present_in_panel(hypothesis, p)
        cell = verify_cell_firm(hypothesis, p, prov)
        cell["outcome"] = outcome
        evidence_out.append(cell)
        fp = cell["firm_present"]
        if fp is True:
            n_firm_present += 1
            if good:
                a += 1
            else:
                b += 1
        elif fp is False:
            n_firm_absent += 1
            if good:
                c += 1
            else:
                d += 1
        else:
            if cell["visibility_tag"] == refd.RETRO_ASSUMED:
                n_retro += 1
            n_unknown += 1
    counts = {
        "n_firm_present": n_firm_present,
        "n_firm_absent": n_firm_absent,
        "n_retro_assumed_or_unknown": n_unknown,
        "n_retro_assumed": n_retro,
        "n_checked": n_firm_present + n_firm_absent + n_unknown,
    }
    return a, b, c, d, counts


# ──────────────────────────────────────────────────────────────
# Cohort assembly
# ──────────────────────────────────────────────────────────────


#: Significance on each split when the small-sample (Fisher) branch is used.
#: Train: two-sided p <= 0.05. Holdout is a REPLICATION of a direction fixed
#: on train, so it uses the one-sided test at p <= 0.10.
ALPHA_TRAIN = 0.05
ALPHA_HOLDOUT = 0.10
#: Below these many labelled, observed customers a split cannot support a claim.
MIN_TRAIN_N = 10
MIN_HOLDOUT_N = 5


def apply_survival_rules(train: dict, holdout: dict,
                         lift_min: float, ci_lower_min: float,
                         alpha_train: float = ALPHA_TRAIN,
                         alpha_holdout: float = ALPHA_HOLDOUT,
                         min_train_n: int = MIN_TRAIN_N,
                         min_holdout_n: int = MIN_HOLDOUT_N) -> tuple[bool, list[str]]:
    """The lift-and-holdout.md survival rules, shared by both passes.

    A signal survives only if, on BOTH splits, the effect is big enough
    (lift) AND unlikely to be chance (Wald CI lower bound on large tables;
    Fisher's exact p-value on small ones). A split too small to say anything
    fails with an "insufficient evidence" reason instead of passing.
    """
    reasons: list[str] = []
    train_lift = train.get("lift")
    hold_lift = holdout.get("lift")

    if train_lift is None or not math.isfinite(train_lift):
        return False, ["train lift undefined"]
    n_tr, n_ho = train.get("n_total"), holdout.get("n_total")
    if n_tr is not None and n_tr < min_train_n:
        return False, [f"insufficient evidence: train has {n_tr} labelled customers < {min_train_n}"]
    if train_lift < lift_min:
        return False, [f"train lift {train_lift:.2f} < {lift_min}"]
    if train.get("method") == "wald_log_lift":
        lo = train.get("ci_lower")
        if lo is None or lo < ci_lower_min:
            return False, [f"train CI lower {lo} < {ci_lower_min}"]
    else:
        p = train.get("fisher_p_value")
        if p is None or p > alpha_train:
            return False, [f"train Fisher p={p} > {alpha_train}"]

    if hold_lift is None or not math.isfinite(hold_lift):
        return False, ["holdout lift undefined"]
    if n_ho is not None and n_ho < min_holdout_n:
        return False, [f"insufficient evidence: holdout has {n_ho} labelled customers < {min_holdout_n}"]
    if hold_lift < 0.8 * train_lift:
        return False, [f"holdout lift {hold_lift:.2f} < 0.8 × train ({0.8*train_lift:.2f})"]
    if holdout.get("method") == "wald_log_lift":
        lo = holdout.get("ci_lower")
        if lo is None or lo < ci_lower_min:
            return False, [f"holdout CI lower {lo} < {ci_lower_min}"]
    else:
        p = holdout.get("fisher_p_value_greater", holdout.get("fisher_p_value"))
        if p is None or p > alpha_holdout:
            return False, [f"holdout one-sided Fisher p={p} > {alpha_holdout}"]
    return True, reasons


def archetype_member_ids(archetype: dict) -> list:
    members = archetype.get("members") or []
    out = []
    for m in members:
        if isinstance(m, (str, int)):
            out.append(m)
        elif isinstance(m, dict):
            out.append(m.get("customer_id") or m.get("id"))
    return [m for m in out if m is not None]


def split_train_holdout(archetype: dict, panels_by_id: dict) -> tuple[list[dict], list[dict]]:
    """Train and holdout panels from the archetype's membership records (wlr_schema)."""
    train_ids, hold_ids = wlr_schema.split_ids(archetype)
    train = [panels_by_id[c] for c in train_ids if c in panels_by_id]
    hold = [panels_by_id[c] for c in hold_ids if c in panels_by_id]
    return train, hold


def build_contingency(
    hypothesis: dict, panels: list[dict]
) -> tuple[int, int, int, int, int]:
    """
    Returns (a, b, c, d, n_unknown). a/b/c/d follow compute_lift_with_ci docstring.
    """
    a = b = c = d = unknown = 0
    for p in panels:
        outcome = classify_outcome(p)
        if outcome in GOOD_FIT_OUTCOMES:
            good = True
        elif outcome in NOT_GOOD_FIT_OUTCOMES:
            good = False
        else:
            unknown += 1
            continue
        present = signal_present_in_panel(hypothesis, p)
        if present is None:
            unknown += 1
            continue
        if present and good:
            a += 1
        elif present and not good:
            b += 1
        elif not present and good:
            c += 1
        elif not present and not good:
            d += 1
    return a, b, c, d, unknown


# ──────────────────────────────────────────────────────────────
# Markdown rendering
# ──────────────────────────────────────────────────────────────


def render_markdown(survivors: list[dict], non_survivors: list[dict]) -> str:
    lines: list[str] = []
    lines.append("# Phase 6 — Validated Signals\n")
    lines.append("> **Evidence discipline:** every pre-close claim below carries a visibility tag —")
    lines.append("> **[ARCHIVED-BEFORE]** (an archive.org capture at-or-before the customer's")
    lines.append("> reference date shows the signal) or **[RECORD-DATED-BEFORE]** (the record's own")
    lines.append("> date, after a publication-lag haircut, precedes the reference date) count as")
    lines.append("> FIRM; **[RETRO-ASSUMED]** (only today's web observed) never does. FIRM lift is")
    lines.append("> computed from firm-tagged cells only; only FIRM signals ship to Phase 7.")
    lines.append("> Signals killed for UNPROVABILITY are labeled as such — that is a coverage")
    lines.append("> statement about the archive, never a claim the signal was absent.\n")
    n_unver = sum(1 for s in non_survivors if s.get("status") == "unverifiable")
    n_prov = sum(1 for s in non_survivors if s.get("status") == "provisional_only")
    lines.append(f"**FIRM survivors:** {len(survivors)}")
    lines.append(f"**Unverifiable (archive too thin to prove — not weak-lift kills):** {n_unver}")
    lines.append(f"**Provisional-only (cleared the cheap bar, never firm-checked):** {n_prov}")
    lines.append(f"**All non-survivors:** {len(non_survivors)}\n")
    lines.append("---\n")
    lines.append("## FIRM survivors\n")
    for s in survivors:
        lines.append(_render_signal_block(s, survived=True))
        lines.append("")
    lines.append("---\n")
    lines.append("## Non-survivors (logged for traceability — grouped by WHY they died)\n")
    for status, label in (("killed_at_firm", "Killed on firm evidence (weak firm lift)"),
                          ("unverifiable", "Unverifiable (archive coverage too thin — NOT evidence of absence)"),
                          ("provisional_only", "Provisional-only (never firm-checked)"),
                          ("killed_at_provisional", "Killed at the provisional filter"),
                          (None, "Other")):
        group = [s for s in non_survivors
                 if (s.get("status") == status if status else s.get("status") is None)]
        if not group:
            continue
        lines.append(f"### {label}\n")
        for s in group:
            why = s.get("survival_reason", "")
            lines.append(f"- `{s.get('hypothesis_id')}` archetype={s.get('archetype_id')} — "
                         f"{s.get('signal','')[:90]}  ⟶  {why}")
        lines.append("")
    return "\n".join(lines)


def _render_signal_block(s: dict, survived: bool) -> str:
    train = s.get("train") or {}
    hold = s.get("holdout") or {}
    art = s.get("anti_overfitting") or {}
    out = []
    out.append(f"### Signal: {s.get('signal','(unspecified)')}\n")
    out.append(f"- Hypothesis ID: `{s.get('hypothesis_id')}`")
    out.append(f"- Archetype: {s.get('archetype_id')}")
    out.append(f"- Source: {s.get('observability_source','')}")
    out.append(f"- Queryable as: `{s.get('queryable_field','')}`")
    out.append(f"- Lead time: {s.get('hypothesized_lead_time_days','?')} days")
    firm = s.get("firm") or {}
    label_prefix = "FIRM " if firm else ""
    out.append(_fmt_lift_line(f"{label_prefix}Train", train))
    out.append(_fmt_lift_line(f"{label_prefix}Holdout", hold))
    if train.get("lift") and hold.get("lift"):
        try:
            ratio = (hold["lift"] or 0) / (train["lift"] or 1)
        except ZeroDivisionError:
            ratio = float("nan")
        out.append(f"- Replication ratio (holdout/train): {ratio:.2f}")
    if firm:
        counts = firm.get("counts") or {}
        out.append(
            f"- Firm evidence: [ARCHIVED-BEFORE]+[RECORD-DATED-BEFORE] present="
            f"{counts.get('n_firm_present', 0)}, provably-absent="
            f"{counts.get('n_firm_absent', 0)}, [RETRO-ASSUMED]/unknown="
            f"{counts.get('n_retro_assumed_or_unknown', 0)} "
            f"(coverage {firm.get('coverage', 0):.0%} of checked cells)")
        prov = (s.get("provisional") or {}).get("train") or {}
        if prov.get("lift") is not None:
            out.append(f"- Provisional lift (today's-web filter, for reference): "
                       f"{prov['lift']:.2f}")
    da = art.get("data_artifact") or {}
    ol = art.get("outcome_leakage") or {}
    out.append(
        f"- Anti-overfitting: data_artifact={'flag' if da.get('flag') else 'ok'}, "
        f"outcome_leakage={'FLAG — ' + str(ol.get('detail','')) if ol.get('flag') else 'ok'} "
        f"(source class: {ol.get('source_class', 'unclassified')})"
    )
    if survived:
        out.append("- Survives all gates: ✅")
    else:
        out.append(f"- Survived: ❌ — {s.get('survival_reason','')}")
    ams = s.get("anti_match_examples") or []
    if ams:
        out.append("- Anti-matches:")
        for a in ams:
            out.append(f"  - {a}")
    return "\n".join(out)


def _fmt_lift_line(label: str, stats: dict) -> str:
    if not stats:
        return f"- {label}: (no data)"
    lift = stats.get("lift")
    lo = stats.get("ci_lower")
    hi = stats.get("ci_upper")
    n = stats.get("n_total")
    method = stats.get("method", "")
    if lift is None:
        return f"- {label}: insufficient data (n={n}, method={method})"
    if lo is not None and hi is not None:
        return f"- {label} lift: {lift:.2f} (95% CI {lo:.2f} – {hi:.2f}, n={n}, {method})"
    if method == "fisher_exact":
        p = stats.get("fisher_p_value")
        pg = stats.get("fisher_p_value_greater")
        odds = stats.get("fisher_odds_ratio")
        ptxt = "n/a" if p is None else f"{p:.3g}"
        pgtxt = "" if pg is None else f", one-sided p={pg:.3g}"
        otxt = "" if odds is None else f"; odds ratio {odds:.2f}"
        lift_txt = "inf" if lift == float("inf") else f"{lift:.2f}"
        return f"- {label} lift: {lift_txt} (Fisher two-sided p={ptxt}{pgtxt}, n={n}{otxt})"
    return f"- {label} lift: {lift:.2f} (n={n}, {method})"


# ──────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--archetype-id", type=int, default=None)
    ap.add_argument("--lift-min", type=float, default=DEFAULT_LIFT_MIN)
    ap.add_argument("--ci-lower-min", type=float, default=DEFAULT_CI_LOWER_MIN)
    ap.add_argument("--skip-firm-pass", action="store_true",
                    help="legacy single-pass behavior (provisional only — "
                         "nothing earns FIRM status)")
    ap.add_argument("--firm-check-top-k", type=int, default=DEFAULT_FIRM_TOP_K,
                    help="firm-verify only top-K provisional survivors per archetype")
    ap.add_argument("--firm-min-coverage", type=float, default=DEFAULT_FIRM_MIN_COVERAGE,
                    help="min firm-evidence coverage (n_firm/n_checked) else unverifiable")
    ap.add_argument("--firm-min-cell", type=int, default=DEFAULT_FIRM_MIN_CELL,
                    help="min firm-present cells else unverifiable")
    ap.add_argument("--force-firm-check", action="append", default=[],
                    help="hypothesis_id to firm-check even outside top-K (repeatable)")
    args = ap.parse_args()

    run_dir = OUTPUTS_ROOT / args.run_id
    if not run_dir.exists():
        raise SystemExit(f"Error: run dir {run_dir} does not exist")

    n_vs = merge_vertical_sources(run_dir)
    if n_vs:
        print(f"[phase6] vertical scout sources merged into manifest: {n_vs}",
              file=sys.stderr)

    hypotheses = load_jsonl(run_dir / "hypotheses.jsonl")
    archetypes = wlr_schema.load_archetypes(run_dir / "archetypes.jsonl")
    panels = load_jsonl(run_dir / "customer_panels.jsonl")
    panels_by_id = {str(p.get("customer_id")): p for p in panels}

    if args.archetype_id is not None:
        archetypes = [a for a in archetypes if a.get("id") == args.archetype_id]
        hypotheses = [h for h in hypotheses if h.get("archetype_id") == args.archetype_id]
        if not archetypes:
            raise SystemExit(f"Error: archetype id {args.archetype_id} not found")

    # Pre-split per archetype.
    arc_by_id = {a.get("id"): a for a in archetypes}
    splits = {
        aid: split_train_holdout(a, panels_by_id) for aid, a in arc_by_id.items()
    }

    survivors_jsonl: list[dict] = []
    non_survivors_jsonl: list[dict] = []
    archetypes_with_zero_survivors: set = set(arc_by_id.keys())
    evidence_rows: list[dict] = []
    forced_ids = set(args.force_firm_check)

    # ── Pass 1: provisional lift from the CURRENT panel — a FILTER, never
    #    a ship decision.
    records: list[dict] = []
    for h in hypotheses:
        aid = h.get("archetype_id")
        if aid not in arc_by_id:
            non_survivors_jsonl.append(_kill(h, f"archetype {aid} not in run"))
            continue
        train_panels, hold_panels = splits[aid]
        a1, b1, c1, d1, _ = build_contingency(h, train_panels)
        a2, b2, c2, d2, _ = build_contingency(h, hold_panels)
        train = compute_lift_with_ci(a1, b1, c1, d1)
        holdout = compute_lift_with_ci(a2, b2, c2, d2)
        cleared, reasons = apply_survival_rules(train, holdout,
                                               args.lift_min, args.ci_lower_min)
        record = dict(h)
        record["provisional"] = {"train": train, "holdout": holdout}
        record["provisional_cleared_bar"] = cleared
        record["provisional_reasons"] = reasons or ["passed"]
        records.append(record)

    # ── Pick Pass-2 candidates: top-K per archetype by provisional train
    #    lift among bar-clearers, plus any --force-firm-check ids.
    by_arc: dict = defaultdict(list)
    for r in records:
        if r["provisional_cleared_bar"]:
            by_arc[r.get("archetype_id")].append(r)
    firm_candidates: set = set(forced_ids)
    for aid, rs in by_arc.items():
        rs.sort(key=lambda r: r["provisional"]["train"].get("lift") or 0, reverse=True)
        for r in rs[: args.firm_check_top_k]:
            firm_candidates.add(r.get("hypothesis_id"))
        dropped = rs[args.firm_check_top_k:]
        if dropped:
            print(f"[archetype {aid}] {len(dropped)} bar-clearing hypotheses "
                  f"NOT firm-checked (outside top-{args.firm_check_top_k}; "
                  f"use --force-firm-check <id> to include)", file=sys.stderr)

    # ── Pass 2: firm verification per cell, firm lift from firm cells only.
    for record in records:
        aid = record.get("archetype_id")
        hid = record.get("hypothesis_id")
        train_panels, hold_panels = splits[aid]
        all_panels_in_arc = train_panels + hold_panels

        artifact = data_artifact_check(record, all_panels_in_arc)
        firm_counts_total: dict = {}

        if not record["provisional_cleared_bar"] and hid not in forced_ids:
            record["status"] = "killed_at_provisional"
            record["survival_reason"] = "; ".join(record["provisional_reasons"])
        elif args.skip_firm_pass or hid not in firm_candidates:
            record["status"] = "provisional_only"
            record["survival_reason"] = (
                "cleared provisional bar but not firm-verified "
                + ("(--skip-firm-pass)" if args.skip_firm_pass
                   else f"(outside top-{args.firm_check_top_k})"))
        else:
            fa1, fb1, fc1, fd1, tc = firm_contingency(record, train_panels, evidence_rows)
            fa2, fb2, fc2, fd2, hc = firm_contingency(record, hold_panels, evidence_rows)
            firm_train = compute_lift_with_ci(fa1, fb1, fc1, fd1)
            firm_hold = compute_lift_with_ci(fa2, fb2, fc2, fd2)
            firm_counts_total = {
                k: tc.get(k, 0) + hc.get(k, 0)
                for k in set(tc) | set(hc)
            }
            n_checked = firm_counts_total.get("n_checked", 0)
            n_firm = (firm_counts_total.get("n_firm_present", 0)
                      + firm_counts_total.get("n_firm_absent", 0))
            coverage = (n_firm / n_checked) if n_checked else 0.0
            firm_train["firm_coverage"] = round((tc["n_firm_present"] + tc["n_firm_absent"])
                                                / tc["n_checked"], 3) if tc.get("n_checked") else 0.0
            firm_hold["firm_coverage"] = round((hc["n_firm_present"] + hc["n_firm_absent"])
                                               / hc["n_checked"], 3) if hc.get("n_checked") else 0.0
            record["firm"] = {"train": firm_train, "holdout": firm_hold,
                              "coverage": round(coverage, 3),
                              "counts": firm_counts_total}

            if firm_counts_total.get("n_firm_absent", 0) == 0:
                record["status"] = "unverifiable"
                record["survival_reason"] = (
                    "insufficient evidence: no cell could be proven ABSENT at its reference "
                    "date, so a firm lift cannot be computed (presence alone is not a contrast)")
            elif (firm_counts_total.get("n_firm_present", 0) < args.firm_min_cell
                    or coverage < args.firm_min_coverage):
                record["status"] = "unverifiable"
                record["survival_reason"] = (
                    f"firm evidence too thin to compute a firm lift "
                    f"(firm-present cells={firm_counts_total.get('n_firm_present',0)} "
                    f"< {args.firm_min_cell} or coverage={coverage:.0%} "
                    f"< {args.firm_min_coverage:.0%}) — killed for UNPROVABILITY, "
                    f"not for weak lift")
            else:
                firm_ok, firm_reasons = apply_survival_rules(
                    firm_train, firm_hold, args.lift_min, args.ci_lower_min)
                if firm_ok:
                    record["status"] = "firm"
                    record["survival_reason"] = "passed on FIRM evidence"
                else:
                    record["status"] = "killed_at_firm"
                    record["survival_reason"] = ("firm lift failed: "
                                                 + "; ".join(firm_reasons))

        leakage = outcome_leakage_check(record, firm_counts_total)
        record["anti_overfitting"] = {
            "data_artifact": artifact,
            "outcome_leakage": leakage,
        }
        # Backward compat: `survived` now means FIRM (or bar-cleared when the
        # firm pass was explicitly skipped).
        record["survived"] = (record["status"] == "firm"
                              or (args.skip_firm_pass
                                  and record["status"] == "provisional_only"))
        # Legacy readers expect train/holdout at top level — point them at the
        # authoritative stats (firm when present, else provisional).
        authoritative = record.get("firm") or record["provisional"]
        record["train"] = authoritative["train"]
        record["holdout"] = authoritative["holdout"]

        if record["survived"]:
            survivors_jsonl.append(record)
            archetypes_with_zero_survivors.discard(aid)
        else:
            non_survivors_jsonl.append(record)

    # ── Evidence ledger: one row per checked (hypothesis, customer) cell.
    evidence_path = run_dir / "wayback_evidence.jsonl"
    with open(evidence_path, "w") as f:
        for row in evidence_rows:
            f.write(json.dumps(row, default=str) + "\n")
    print(f"→ {evidence_path} ({len(evidence_rows)} checked cells)", file=sys.stderr)

    # Flag archetypes with no surviving signals.
    no_disc_flags = []
    for aid in sorted(archetypes_with_zero_survivors):
        no_disc_flags.append(no_discriminator_flag(aid, [
            r for r in non_survivors_jsonl if r.get("archetype_id") == aid]))

    out_jsonl = run_dir / "validated_signals.jsonl"
    with open(out_jsonl, "w") as f:
        for r in survivors_jsonl + non_survivors_jsonl:
            f.write(json.dumps(r, default=str) + "\n")
        for flag in no_disc_flags:
            f.write(json.dumps(flag) + "\n")

    out_md = run_dir / "validated_signals.md"
    md = render_markdown(survivors_jsonl, non_survivors_jsonl)
    if no_disc_flags:
        md += "\n\n## Archetypes with no discriminator\n\n"
        for flag in no_disc_flags:
            md += f"- archetype {flag['archetype_id']}: {flag['reason']}\n"
    out_md.write_text(md)

    print(f"survivors={len(survivors_jsonl)} non_survivors={len(non_survivors_jsonl)} "
          f"no_disc_archetypes={len(no_disc_flags)}", file=sys.stderr)
    print(f"→ {out_jsonl}", file=sys.stderr)
    print(f"→ {out_md}", file=sys.stderr)
    return 0


def no_discriminator_flag(archetype_id, killed: list[dict]) -> dict:
    """An explicit "no reliable discriminator found" result, with the why.

    Zero survivors is a legitimate answer, not a data problem to be fixed
    until something passes. The causes are counted separately because they
    call for different next steps.
    """
    def cause(r):
        if r.get("status") == "provisional_only":
            return "not_firm_checked"
        why = " ".join(str(x) for x in (r.get("provisional_reasons") or [r.get("survival_reason") or ""]))
        if "insufficient evidence" in why or r.get("status") == "unverifiable":
            return "insufficient_evidence"
        if "undefined" in why:
            return "signal_not_observed"
        return "no_detectable_relationship"
    counts: dict = {}
    for r in killed:
        counts[cause(r)] = counts.get(cause(r), 0) + 1
    if not killed:
        counts["no_hypotheses"] = 1
    return {"archetype_id": archetype_id, "no_discriminator_found": True,
            "result": "no reliable discriminator found",
            "causes": counts,
            "reason": ("no reliable discriminator found: " + ", ".join(
                f"{k}={v}" for k, v in sorted(counts.items())))}


def _kill(h: dict, why: str) -> dict:
    r = dict(h)
    r["survived"] = False
    r["survival_reason"] = why
    return r


if __name__ == "__main__":
    sys.exit(main())
