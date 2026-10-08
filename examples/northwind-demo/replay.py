#!/usr/bin/env python3
"""Offline replay: the whole method on Northwind, through the real phase code. No keys.

    python3 examples/northwind-demo/replay.py           # run and check every step
    python3 examples/northwind-demo/replay.py --keep    # keep the outputs to read
    python3 examples/northwind-demo/replay.py --write   # refresh the shipped icp.json + PVP draft

Every step calls the same functions a live run calls. Where a live run would
call a paid API, the replay reads a RECORDED response instead:

  step                         code it runs                              recorded input
  1  dossiers (incl. lost deal) build_demo_dossiers + validate_schema     -
  2  customers.csv + labels     customers_from_dossiers.py (10X rule)     -
  3  phase 0: normalize         adapters/csv.py + wlr_schema              cohort/customers.csv
  4  phase 2: panels            panel_parsers (the live parsers)          cohort/recorded_raw.jsonl
  5  phase 3: segment           segment.segment_rows + write_archetypes   -
  6  phase 5: hypotheses        generate_hypotheses.training_inputs       cohort/recorded_hypotheses.json
                                + run_gates                               cohort/recorded_themes.jsonl
  7  phase 6: holdout + firm    backcast_validate: provisional pass, then the
                                firm pass on DATED records (verify_cell_firm)
  8  phase 7-8: scoring         compose_discriminator + score_prospect     -
                                (one shared scorer, coverage reported)
  9  ICP derivation             derive_icp() below                        -
  10 packets                    crawford/build_packets.py --offline       site-snapshots/
  11 Jev scoring                calibrated-judge/judge_packets.py         recorded_jev_cache.jsonl
  12 quantify                   calibrated-judge/scripts/quantify.py      -
  13 PVP draft                  write_pvp() below                         -

Phase 6's firm pass has two routes. Dated records (news items) are verified
offline here, record by record: only a record that satisfies the signal's
predicate can date it, and its URL and excerpt are kept. The archive route
(Wayback Machine captures of a homepage) needs the network, so hypotheses that
rely on it stop at "provisional only" in the replay and say so.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
COHORT = HERE / "cohort"
WLR = ROOT / "skills" / "win-loss-rewind" / "scripts"
PY = sys.executable
sys.path.insert(0, str(WLR))

import backcast_validate as bv          # noqa: E402
import enrich_panel                     # noqa: E402
import generate_hypotheses as gh        # noqa: E402
import segment                          # noqa: E402
import wlr_schema                       # noqa: E402
import compose_discriminator as cd      # noqa: E402
import panel_parsers                    # noqa: E402
import score_prospect as sp_            # noqa: E402

TEN_X_MULTIPLE = 10
#: Rules that turn a labelled segment into ICP text. Each is traced to accounts.
INCLUDE_TEXT = {
    "general dentistry group": "general or family dentistry practice with two or more offices",
    "pediatric dentistry": "pediatric dentistry practice",
}
EXCLUDE_TEXT = {
    "orthodontics": "orthodontics-only practice",
    "general dentistry solo": "single-office solo practice",
    "oral surgery": "oral surgery-only practice",
}
#: Exclusions from what Northwind sells, not learned from a lost account.
PRODUCT_SCOPE_EXCLUSIONS = ["veterinary clinic or any non-dental business"]


def sh(args, env, cwd=ROOT):
    p = subprocess.run([PY, *map(str, args)], cwd=cwd, env=env, capture_output=True, text=True)
    if p.returncode != 0:
        raise AssertionError(f"failed: {' '.join(map(str, args))}\n{p.stdout[-1200:]}\n{p.stderr[-1200:]}")
    return p.stdout


def read_csv(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


# ------------------------------------------------------------------ steps

def step_dossiers(work, env):
    out = work / "dossiers"
    sh([HERE / "build_demo_dossiers.py", out], env)
    v = ROOT / "skills/dossier-builder/scripts/validate_schema.py"
    sh([v, "--dossier-dir", out, "--mode", "customer"], env)
    sh([v, "--dossier-dir", out / "prospects", "--mode", "prospect"], env)
    lost = list((out / "prospects").glob("*.json"))
    assert len(lost) == 1, "the closed-lost deal must get a dossier"
    return out, f"7 customer + {len(lost)} lost-deal dossier(s), both schemas valid"


def step_labels(work, dossier_dir, env):
    """Build the customer CSV from the dossiers (the real converter) and compare
    it with the shipped customers.csv: labels, 10X rule and date anchors."""
    out = work / "customers.csv"
    sh([WLR / "customers_from_dossiers.py", "--dossiers", dossier_dir, "--out", out], env)
    derived = {r["customer_id"]: r for r in read_csv(out)}
    shipped = {r["customer_id"]: r for r in read_csv(HERE / "customers.csv")}
    assert set(derived) == set(shipped), "customers.csv and the dossiers list different accounts"
    for aid, d in derived.items():
        for k in ("label", "outcome", "health", "close_date", "first_call_date", "churn_date"):
            assert shipped[aid][k] == d[k], f"{aid}: customers.csv {k}={shipped[aid][k]!r}, dossiers say {d[k]!r}"
    undated = [a for a, r in shipped.items() if not (r["close_date"] or r["first_call_date"])]
    assert not undated, f"rows with no date anchor: {undated}"
    # Every quote in customers.csv must be verbatim in a dossier, with the
    # elicitation the evidence column claims.
    voice = {}
    for f in list(dossier_dir.glob("*.json")) + list((dossier_dir / "prospects").glob("*.json")):
        d = json.loads(f.read_text())
        voice[d["account_id"]] = d.get("customer_voice") or []
    n_quotes = 0
    for aid, r in shipped.items():
        for claim, quote in re.findall(r"(unprompted|led): '([^']+)'", r["label_evidence"], re.I):
            hits = [v for v in voice.get(aid, []) if quote in v["text"]]
            assert hits, f"{aid}: quote not found in its dossier: {quote[:50]}"
            assert hits[0]["elicitation"] == claim.lower(), f"{aid}: quote is {hits[0]['elicitation']}, customers.csv says {claim}"
            n_quotes += 1
    n10 = sum(1 for v in derived.values() if v["label"] == "10X")
    return shipped, (f"customers.csv matches the dossiers ({n10} x 10X at >= {TEN_X_MULTIPLE}x the median "
                     f"lifetime revenue; every row dated); {n_quotes} quotes verified with their elicitation")


def step_phase0(work, env):
    raw = work / "wlr" / "raw_dataset.json"
    raw.parent.mkdir(parents=True)
    sh([WLR / "adapters" / "csv.py", COHORT / "customers.csv", "--out", raw], env)
    customers = [wlr_schema.normalize_customer(c) for c in json.loads(raw.read_text())["customers"]]
    assert all(c["company"] for c in customers), "company names must survive phase 0"
    src = {r["customer_id"]: r for r in read_csv(COHORT / "customers.csv")}
    for c in customers:
        s = src[c["customer_id"]]
        if s["health"] == "churned":
            assert c["outcome"] in wlr_schema.NOT_GOOD_FIT_OUTCOMES, \
                f"{c['customer_id']}: churned account read as {c['outcome']} (CRM says {s['outcome']})"
        if s["label"] == "10X":
            assert c["outcome"] == "10x"
    counts = {o: sum(1 for c in customers if c["outcome"] == o) for o in wlr_schema.OUTCOMES}
    return customers, f"{len(customers)} customers; outcomes {{{', '.join(f'{k}: {v}' for k, v in counts.items() if v)}}}"


def step_phase2(customers):
    """Panels through the LIVE parsers (panel_parsers.py) from recorded raw responses."""
    raw = {r["customer_id"]: r["raw"] for r in wlr_schema.load_jsonl(COHORT / "recorded_raw.jsonl")}
    panels = []
    for c in customers:
        panel = enrich_panel.build_panel_record(c, panel_parsers.panel_from_raw(raw[c["customer_id"]]))
        panel["enriched_at"] = "2026-10-01T00:00:00+00:00"     # fixed, so the replay is reproducible
        panels.append(panel)
    counts = [p["signal_panel"]["firecrawl_scrape"]["data"]["location_count"] for p in panels]
    assert all(isinstance(n, int) for n in counts), "the scrape parser must extract an office count"
    n_multi = sum(1 for n in counts if n >= 2)
    return panels, (f"{len(panels)} panels parsed from recorded raw responses; "
                    f"office count extracted for all ({n_multi} with 2+)")


def _namer(summary):
    top = (summary.get("canonical_features") or [{}])[0].get("feature", "")
    big = any(f.get("feature") in ("fc_page_count", "fc_log_page_count", "li_employees",
                                   "li_log_employees", "own_reviews", "own_log_reviews",
                                   "fc_has_careers")
              and f.get("gap", 0) > 0 for f in summary.get("canonical_features") or [])
    name = "Large group websites" if big else "Small single-site practices"
    return {"archetype_name": name, "one_liner": f"clustered on {top}"}


def step_phase3(work, panels):
    rdir = work / "wlr"
    archetypes, labels, holdout, sil, k = segment.segment_rows(panels, seed=42, namer=_namer)
    segment.write_archetypes(rdir, archetypes, panels, labels, holdout, sil, k)
    arcs = wlr_schema.load_archetypes(rdir / "archetypes.jsonl")
    assert arcs and all(a["members"] for a in arcs), "every archetype must carry members"
    for a in arcs:
        n, n_hold = len(a["members"]), sum(1 for m in a["members"] if m["holdout"])
        assert n_hold == max(1, round(n * 0.2)), f"archetype {a['id']}: {n_hold} holdout of {n}"
    desc = "; ".join(f"{a['name']} ({len(a['members'])} members, "
                     f"{sum(1 for m in a['members'] if m['holdout'])} holdout)" for a in arcs)
    return arcs, f"k={k}: {desc}"


def step_phase5(arcs, panels):
    themes_by = {}
    for t in wlr_schema.load_jsonl(COHORT / "recorded_themes.jsonl"):
        themes_by.setdefault(str(t["customer_id"]), []).append(t)
    panels_by = {str(p["customer_id"]): p for p in panels}
    recorded = json.loads((COHORT / "recorded_hypotheses.json").read_text())["hypotheses"]
    survivors, rejected = [], []
    for a in arcs:
        view, themes, sample = gh.training_inputs(a, themes_by, panels_by)
        _, hold_ids = wlr_schema.split_ids(a)
        seen = json.dumps([view, themes, sample])
        leaked = [h for h in hold_ids if f'"{h}"' in seen]
        assert not leaked, f"archetype {a['id']}: holdout ids reached hypothesis generation: {leaked[:3]}"
        for h in recorded:
            h = dict(h, archetype_id=a["id"])
            ok, gate, reason = gh.run_gates(h, themes, gh.load_source_manifest())
            (survivors if ok else rejected).append(h if ok else dict(h, failed_gate=gate, failure_reason=reason))
    gates = {(r["hypothesis_id"], r["failed_gate"]) for r in rejected}
    assert gates == {("H-growing", "gate1"), ("H-headcount", "gate4")}, gates
    return survivors, (f"{len(survivors)} hypotheses passed the gates, {len(rejected)} rejected "
                       f"(generic 'growing practices' at Gate 1; untyped numeric headcount at Gate 4); "
                       f"no holdout account reached the generator")


def _firm(h, train, hold):
    ev = []
    t = bv.compute_lift_with_ci(*bv.firm_contingency(h, train, ev)[:4])
    o = bv.compute_lift_with_ci(*bv.firm_contingency(h, hold, ev)[:4])
    ok, reasons = bv.apply_survival_rules(t, o, bv.DEFAULT_LIFT_MIN, bv.DEFAULT_CI_LOWER_MIN)
    present = sum(1 for c in ev if c["firm_present"] is True)
    coverage = sum(1 for c in ev if c["firm_present"] is not None) / max(1, len(ev))
    if not any(c["firm_present"] is False for c in ev):
        ok, reasons = False, ["insufficient evidence: no cell could be proven absent, so a firm "
                              "lift cannot be computed (the same rule as backcast_validate.main)"]
    elif present < bv.DEFAULT_FIRM_MIN_CELL or coverage < bv.DEFAULT_FIRM_MIN_COVERAGE:
        ok, reasons = False, [f"unverifiable: {present} firm-present cells, coverage {coverage:.0%}"]
    return t, o, ok, reasons, ev


def step_phase6(arcs, panels, hypotheses):
    """Provisional pass for every hypothesis; FIRM pass (dated records) where the
    source carries dates. Archive (Wayback) checks need the network, so archive-
    route hypotheses stop at provisional here and say so."""
    panels_by = {str(p["customer_id"]): p for p in panels}
    results, evidence = [], []
    for h in hypotheses:
        a = next(x for x in arcs if x["id"] == h["archetype_id"])
        train, hold = bv.split_train_holdout(a, panels_by)
        t = bv.compute_lift_with_ci(*bv.build_contingency(h, train)[:4])
        o = bv.compute_lift_with_ci(*bv.build_contingency(h, hold)[:4])
        prov_ok, reasons = bv.apply_survival_rules(t, o, bv.DEFAULT_LIFT_MIN, bv.DEFAULT_CI_LOWER_MIN)
        rec = {"hypothesis_id": h["hypothesis_id"], "archetype_id": a["id"], "archetype": a["name"],
               "signal": h["signal"], "observability_source": h["observability_source"],
               "queryable_field": h["queryable_field"], "predicate": h.get("predicate"),
               "provisional_ok": prov_ok, "reasons": reasons, "train": t, "holdout": o}
        if h["temporal_provability"]["best_case_tag"] == "RECORD_DATED_BEFORE":
            ft, fo, ok, freasons, ev = _firm(h, train, hold)
            evidence += ev
            rec.update(status="firm" if (prov_ok and ok) else ("killed_at_firm" if prov_ok else "killed_at_provisional"),
                       survived=prov_ok and ok, firm={"train": ft, "holdout": fo},
                       reasons=reasons if not prov_ok else freasons)
            rec["train"], rec["holdout"] = ft, fo
        else:
            rec.update(status="provisional_only" if prov_ok else "killed_at_provisional", survived=False)
        results.append(rec)

    # The review's negative fixture, through the same verifier: an old, unrelated
    # record must not date a signal the panel does not have.
    h = next(x for x in hypotheses if x["hypothesis_id"] == "H-new-office-news")
    unrelated = next(p for p in panels if "5K" in json.dumps(p["signal_panel"]["serper_news"]))
    cell = bv.verify_cell_firm(h, unrelated, bv.signal_present_in_panel(h, unrelated))
    assert cell["firm_present"] is None, f"unrelated old article was counted as firm evidence: {cell}"
    firm_cells = [c for c in evidence if c["firm_present"] is True]
    assert firm_cells and all(c["evidence_detail"]["url"] and "opened its" in c["evidence_detail"]["excerpt"]
                              for c in firm_cells), "firm cells must keep the satisfying record's URL + excerpt"

    lines = []
    for name in dict.fromkeys(r["archetype"] for r in results):
        rs = [r for r in results if r["archetype"] == name]
        firm = [r["hypothesis_id"] for r in rs if r["survived"]]
        prov = [r["hypothesis_id"] for r in rs if r["status"] == "provisional_only"]
        if firm:
            lines.append(f"{name}: FIRM {', '.join(firm)}" + (f"; provisional only {', '.join(prov)}" if prov else ""))
        else:
            flag = bv.no_discriminator_flag(rs[0]["archetype_id"], [{"provisional_reasons": r["reasons"], "status": r["status"]} for r in rs])
            lines.append(f"{name}: {flag['reason']}")
    assert not any(r["status"] in ("firm", "provisional_only") for r in results
                   if r["hypothesis_id"] == "H-online-booking"), "the coin-flip signal must die"
    # What the shipped cohort actually shows: the dated news signal is PROVEN
    # present for most good-fit accounts, but nothing can be proven ABSENT (a
    # later article, or no article, is never proof that none existed before), so
    # the firm table has no "absent" cells and no lift can be computed. The
    # honest result is "no reliable discriminator found", not a survivor.
    post_ref = [c for c in evidence if c["firm_present"] is None
                and "after the reference" in json.dumps(c.get("evidence_detail"))]
    assert post_ref, "post-reference-only matches must be recorded as unknown"
    assert not any(c["firm_present"] is False for c in evidence), "a search result must never prove absence"
    news = [r for r in results if r["hypothesis_id"] == "H-new-office-news"]
    assert news and not any(r["survived"] for r in news), \
        "with no provable absence, the dated signal cannot pass the firm test"
    return results, ("; ".join(lines) + f". {len(firm_cells)} firm-present cells, each with the dated "
                     f"record's URL and excerpt; {len(post_ref)} later-only matches left unknown; no cell "
                     "can be proven absent from search results, so no firm lift exists; the "
                     "unrelated-old-article fixture stayed unknown")


def step_phase7(work, arcs, panels, results):
    """Rubric + threshold from firm survivors, then score the holdout with the
    Phase 8 scorer: one shared scoring function, coverage reported."""
    panels_by = {str(p["customer_id"]): p for p in panels}
    out = []
    for a in arcs:
        surv = [r for r in results if r["archetype_id"] == a["id"] and r["survived"]]
        if not surv:
            flag = bv.no_discriminator_flag(a["id"], [{"provisional_reasons": r["reasons"], "status": r["status"]}
                                                      for r in results if r["archetype_id"] == a["id"]])
            md, rec = cd.no_discriminator_section(a, flag)
            assert "no reliable discriminator found" in md and rec["causes"]
            out.append(f"{a['name']}: no discriminator shipped ({flag['reason']})")
            continue
        rubric = cd.compose_rubric_for_archetype(surv)
        assert all(i["predicate"] for i in rubric["signals"]), "the typed test must travel into the rubric"
        train, hold = cd.split_train_holdout(a, panels_by)
        thr = cd.calibrate_threshold(rubric, train)
        arc = {"archetype_id": a["id"], "archetype_name": a["name"], "rubric": rubric, "threshold": thr}
        scored = [sp_.score_against_archetype(arc, p) for p in hold]
        for p, s in zip(hold, scored):
            assert abs(s["fit_score"] - cd.score_panel(rubric, p)[0]) < 1e-9, "Phase 7 and 8 disagree"
        good = [s for p, s in zip(hold, scored) if wlr_schema.verified_outcome(p) in wlr_schema.GOOD_FIT_OUTCOMES]
        bad = [s for p, s in zip(hold, scored) if wlr_schema.verified_outcome(p) in wlr_schema.NOT_GOOD_FIT_OUTCOMES]
        cov = sum(s["coverage"] for s in scored) / max(1, len(scored))
        out.append(f"{a['name']}: threshold {thr:.2f}; holdout looks-like {sum(s['looks_like'] for s in good)}/{len(good)} "
                   f"good vs {sum(s['looks_like'] for s in bad)}/{len(bad)} lost; coverage {cov:.0%}")
    probe = next(r for r in results if r["hypothesis_id"] == "H-new-office-news")
    empty = sp_.score_against_archetype({"archetype_id": 0, "archetype_name": "x", "threshold": 0.5,
                                          "rubric": {"signals": [dict(probe, weight=1.0)]}},
                                         {"signal_panel": {}})
    assert empty["status"] == "insufficient_evidence", "an unobserved panel must be insufficient, not a no"
    return None, "; ".join(out) + "; an unobserved panel scores insufficient_evidence"


#: How a surviving hypothesis becomes ICP text the judge can read in a packet.
SIGNAL_TEXT = {
    "H-new-office-news": "a dental practice that has opened a second (or later) office",
    "H-multi-office": "a website that says the practice runs two or more offices",
}


def derive_icp(results, labels):
    """ICP rules, each traced to accounts or to a surviving signal."""
    segs = {a["account_id"]: a["segment"].lower() for a in read_csv(HERE / "corpus/accounts.csv")}
    inclusions, exclusions, prov = [], [], {}
    for seg, text in INCLUDE_TEXT.items():
        ids = [aid for aid, r in labels.items() if segs.get(aid) == seg and r["label"] in ("won", "10X")]
        if ids:
            inclusions.append(text)
            prov[text] = {"source": "customers.csv won/10X accounts", "accounts": sorted(ids)}
    for r in results:
        if not r["survived"]:
            continue
        text = SIGNAL_TEXT[r["hypothesis_id"]]
        if text not in inclusions:
            inclusions.append(text)
            prov[text] = {"source": "win-loss-rewind phase 6 (firm, dated records)",
                          "hypotheses": [], "archetypes": []}
        prov[text]["hypotheses"].append(r["hypothesis_id"])
        prov[text]["archetypes"].append(
            f"{r['archetype']}: lift {r['train']['lift']:.1f} train / {r['holdout']['lift']:.1f} holdout")
    for seg, text in EXCLUDE_TEXT.items():
        ids = [aid for aid, r in labels.items() if segs.get(aid) == seg and r["label"] == "lost"]
        if ids:
            exclusions.append(text)
            prov[text] = {"source": "customers.csv lost accounts", "accounts": sorted(ids)}
    for text in PRODUCT_SCOPE_EXCLUSIONS:
        exclusions.append(text)
        prov[text] = {"source": "product scope: Northwind sells dental scheduling (not learned from a loss)"}
    return {"_why": "Derived by examples/northwind-demo/replay.py. Each rule's evidence is in _provenance.",
            "inclusions": inclusions, "exclusions": exclusions, "territories": ["United States"],
            "_provenance": prov}


def step_packets(work, env, icp_path):
    run = work / "run"
    sh([HERE / "seed_page_cache.py", run], env)
    sh([ROOT / "skills/crawford/scripts/build_packets.py", "--run-dir", run, "--companies",
        HERE / "companies.csv", "--icp", icp_path, "--offline"], env)
    n = len(list((run / "packets" / "companies").glob("*.json")))
    assert n == 6
    return run / "packets", f"{n} packets built offline, $0"


def step_judge(work, env, packets, icp_path, record=False):
    cache = HERE / "recorded_jev_cache.jsonl"
    run_cache = work / "jev-cache.jsonl"
    if not record:
        assert cache.exists(), "recorded_jev_cache.jsonl is missing (run with --record-jev once)"
        shutil.copy(cache, run_cache)
    jenv = dict(env, JEV_CACHE_PATH=str(run_cache if not record else cache))
    out = work / "scores"
    sh([ROOT / "skills/calibrated-judge/scripts/judge_packets.py", "--packets", packets,
        "--icp", icp_path, "--out", out], jenv)
    s = json.loads((out / "summary.json").read_text())
    rows = read_csv(out / "scores.csv")
    if not record:
        assert s["judge_unavailable"] == 0, "a recorded answer is missing: the packets or ICP changed"
        assert s["spent_usd"] == 0, "the replay must not spend"
    return out, f"{len(rows)} rows from recorded Jev answers: {s['actions']}"


def step_quantify(work, packets, scores_dir, env):
    out = scores_dir / "shortlist.csv"
    sh([ROOT / "skills/calibrated-judge/scripts/quantify.py", "--scores", scores_dir / "scores.csv",
        "--packets", packets, "--metric-name", "offices", "--out", out], env)
    ranked = read_csv(out)
    desc = ", ".join(f"{r['name']} ({r['offices'] or 'unknown'})" for r in ranked) or "none accepted"
    return ranked, f"accepted rows ranked by office count from the packets: {desc}"


CHECKLIST = [
    "List every office's chairs and providers in one sheet, with each office's hours.",
    "Pick one rule for same-day cancellations: who may offer the open chair, and to which patients.",
    "Move one office first. Run the shared calendar there for two weeks before adding the next.",
    "Before each new office goes live, check when its old scheduling contract renews and give notice in time.",
]


def write_pvp(ranked, labels, packets) -> str:
    if not ranked:
        return "# PVP draft\n\nNo prospect was accepted by the judge, so there is no message to send.\n"
    top = ranked[0]
    doc = json.loads((packets / "companies" / f"{top['entity_key']}.json").read_text())
    home = doc["packet"]["site"]["homepage_text"]["value"]
    sentence = next((s.strip() for s in re.split(r"(?<=[.!?])\s+", home)
                     if re.search(r"offices|locations", s)), "")
    tenx = [r for r in labels.values() if r["label"] == "10X"]
    quote_m = re.search(r"unprompted: '([^']+)'", tenx[0]["label_evidence"])
    quote = quote_m.group(1) if quote_m else ""
    lines = [
        f"# Sample PVP email: {top['name']}",
        "",
        "Generated by `examples/northwind-demo/replay.py` from the replay's results. A **PVP**",
        "(Permissionless Value Proposition) is a message so specific and useful that the buyer",
        "would pay to receive it. Every claim below maps to a row in the fact table.",
        "",
        "## Where each fact came from",
        "",
        "| Fact | Source |",
        "|---|---|",
        f"| \"{sentence}\" | packet `site.homepage_text`, {top['domain']} |",
        f"| The judge accepted this row | `scores.csv`, recorded Jev answer |",
        f"| It ranks first by office count ({top['offices']}) among accepted rows | `shortlist.csv` (quantify step) |",
        f"| A multi-office customer said, unprompted: \"{quote}\" | `corpus/calls/2024-02-20-rivermark-discovery.md` |",
        f"| Customers like this became 10X accounts (lifetime revenue at least 10x the median) | `customers.csv`, {len(tenx)} 10X rows |",
        "| The checklist below | written for this email; not from a customer |",
        "",
        "## The draft",
        "",
        f"**Subject:** {top['name']}: one calendar before the next office opens",
        "",
        "Hi {first name},",
        "",
        f"Your site says: \"{sentence}\"",
        "",
        "When a practice runs several offices on separate calendars, the gap shows up the day",
        "someone calls out sick. One operations director put it to us this way: \"" + quote + "\"",
        "",
        "Here is the checklist we would use to put every office on one calendar:",
        "",
    ]
    lines += [f"{i}. {c}" for i, c in enumerate(CHECKLIST, 1)]
    lines += ["", "No reply needed. If you want help with step 1, I can send a blank sheet.", "",
              "{your name}", ""]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--write", action="store_true", help="refresh the shipped icp.json and pvp-email-draft.md")
    ap.add_argument("--record-jev", action="store_true",
                    help="LIVE: ask Jev for real and save the answers as the recording (a fraction of a cent)")
    a = ap.parse_args()

    work = Path(tempfile.mkdtemp(prefix="northwind-replay-"))
    env = {k: v for k, v in os.environ.items() if not any(h in k.upper() for h in ("_KEY", "_TOKEN", "_SECRET", "PASSWORD", "LOGIN"))}
    if a.record_jev:
        env = dict(os.environ)
    env.pop("BLUEPRINT_ROOT", None)
    env.update(PYTHONDONTWRITEBYTECODE="1", CALIBRATED_JUDGE_OUT=str(work / "judge-log"),
               SOURCE_DATE_EPOCH="1790812800",      # 2026-10-01: packets are byte-identical every run
               CRAWFORD_QUOTA_STATE_DIR=str(work / "crawford-state"))
    if not a.record_jev:
        env["GTM_STARTER_ROOT"] = str(work / "no-env-root")
        env["GTM_NO_DOTENV"] = "1"                         # never load a real .env
    failed = 0
    print(f"Northwind offline replay. Work folder: {work}\n")

    def run(name, fn):
        nonlocal failed
        try:
            value, msg = fn()
            print(f"PASS  {name:<24} {msg}")
            return value
        except Exception as exc:          # noqa: BLE001
            failed += 1
            print(f"FAIL  {name:<24} {exc}")
            raise SystemExit(1)

    dz = run("1 dossiers", lambda: step_dossiers(work, env))
    labels = run("2 customers.csv", lambda: step_labels(work, dz, env))
    customers = run("3 phase 0 normalize", lambda: step_phase0(work, env))
    panels = run("4 phase 2 panels", lambda: step_phase2(customers))
    arcs = run("5 phase 3 segment", lambda: step_phase3(work, panels))
    hyps = run("6 phase 5 hypotheses", lambda: step_phase5(arcs, panels))
    results = run("7 phase 6 holdout+firm", lambda: step_phase6(arcs, panels, hyps))
    run("8 phase 7-8 scoring", lambda: step_phase7(work, arcs, panels, results))

    def icp_step():
        icp = derive_icp(results, labels)
        path = work / "icp.json"
        path.write_text(json.dumps(icp, indent=2) + "\n")
        shipped = json.loads((HERE / "icp.json").read_text())
        if a.write:
            (HERE / "icp.json").write_text(json.dumps(icp, indent=2) + "\n")
        else:
            assert {k: shipped.get(k) for k in ("inclusions", "exclusions")} == \
                   {k: icp[k] for k in ("inclusions", "exclusions")}, \
                   "examples/northwind-demo/icp.json differs from what the replay derives (run --write)"
        return path, f"{len(icp['inclusions'])} inclusions, {len(icp['exclusions'])} exclusions, each with provenance"
    icp_path = run("9 ICP derivation", icp_step)
    packets = run("10 packets", lambda: step_packets(work, env, icp_path))
    scores_dir = run("11 Jev scoring", lambda: step_judge(work, env, packets, icp_path, a.record_jev))
    ranked = run("12 quantify", lambda: step_quantify(work, packets, scores_dir, env))

    def pvp_step():
        text = write_pvp(ranked, labels, packets)
        (work / "pvp-email-draft.md").write_text(text)
        if a.write:
            (HERE / "pvp-email-draft.md").write_text(text)
        else:
            assert (HERE / "pvp-email-draft.md").read_text() == text, \
                "pvp-email-draft.md differs from what the replay writes (run --write)"
        return None, f"draft for {ranked[0]['name'] if ranked else 'nobody'} matches the shipped sample"
    run("13 PVP draft", pvp_step)

    print("\n13/13 replay steps passed.")
    if a.keep or a.write or a.record_jev:
        print(f"Outputs kept in {work}")
    else:
        shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
