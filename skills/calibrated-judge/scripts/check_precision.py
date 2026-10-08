#!/usr/bin/env python3
"""Check the judge on YOUR rows before you trust its threshold. Free, by hand.

The 0.90 thresholds are a starting point, not a guarantee for your ICP and your
packets. Label about 30 rows yourself, then measure.

Step 1: make a sheet to label: up to 30 ACCEPTED rows (the precision
sample) plus up to 10 rejected/review rows (a separate diagnostic):

    python3 skills/calibrated-judge/scripts/check_precision.py sample \
        --scores out/run1-scores/scores.csv --n 30 --out out/run1-scores/to_label.csv

  Open to_label.csv, read each row's packet, and fill `your_answer` with yes
  (fits) or no (does not). Do it without looking at the judge's answer.

Step 2: measure:

    python3 skills/calibrated-judge/scripts/check_precision.py measure \
        --scores out/run1-scores/scores.csv --labels out/run1-scores/to_label.csv

  It prints accept precision as k/n with a 95% interval, says INSUFFICIENT
  EVIDENCE when fewer than 10 accepted rows were labelled, counts rejected rows
  you said fit (the diagnostic), and lists every disagreement.

Decision rule: if accept precision is below what you can live with (for
example 90% for a cold send), raise the threshold, fix the ICP rules, or enrich
the packets, then measure again on new rows.
"""
from __future__ import annotations

import argparse
import csv
import random
import sys
from pathlib import Path

YES = {"yes", "y", "1", "true", "fit"}
NO = {"no", "n", "0", "false", "not"}


def _read(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


#: Below this many labelled ACCEPTED rows, a precision number means little.
MIN_ACCEPTS = 10


def wilson(k: int, n: int, z: float = 1.96):
    """95% Wilson interval for k successes in n trials."""
    if n == 0:
        return None, None
    p = k / n
    den = 1 + z * z / n
    mid = (p + z * z / (2 * n)) / den
    half = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / den
    return max(0.0, mid - half), min(1.0, mid + half)


def sample(scores, n=30, n_other=10, seed=0) -> list[dict]:
    """Up to `n` ACCEPTED rows (the precision sample) plus up to `n_other`
    rejected / review rows (a separate diagnostic). Each row says which."""
    rng = random.Random(seed)
    accepts = [r for r in scores if r.get("action") == "accept"]
    others = [r for r in scores if r.get("action") in ("reject", "review")]
    picked = [(r, "precision") for r in rng.sample(accepts, min(n, len(accepts)))]
    picked += [(r, "diagnostic") for r in rng.sample(others, min(n_other, len(others)))]
    rng.shuffle(picked)
    return [{"entity_key": r["entity_key"], "name": r.get("name", ""), "domain": r.get("domain", ""),
             "sample": kind, "your_answer": ""} for r, kind in picked]


def measure(scores, labels, min_accepts=MIN_ACCEPTS) -> dict:
    by_key = {r["entity_key"]: r for r in scores}
    tp = fp = fn = tn = 0
    disagreements, unlabeled = [], 0
    for lab in labels:
        ans = (lab.get("your_answer") or "").strip().lower()
        if ans not in YES | NO:
            unlabeled += 1
            continue
        fits = ans in YES
        action = (by_key.get(lab["entity_key"]) or {}).get("action")
        if action == "accept":
            tp += fits
            fp += not fits
        elif action == "reject":
            fn += fits
            tn += not fits
        if (action == "accept" and not fits) or (action == "reject" and fits):
            disagreements.append({"entity_key": lab["entity_key"], "name": lab.get("name", ""),
                                  "judge": action, "you": "fits" if fits else "does not fit"})
    n_acc = tp + fp
    lo, hi = wilson(tp, n_acc)
    n_accepts_total = sum(1 for r in scores if r.get("action") == "accept")
    return {"accept_precision": (tp / n_acc) if n_acc else None,
            "accept_precision_ci95": (lo, hi), "accepts_checked": n_acc, "accepts_correct": tp,
            "accepts_in_scores": n_accepts_total,
            "insufficient_evidence": n_acc < min_accepts,
            "rejected_but_fit": fn, "rejects_checked": fn + tn,
            "unlabeled": unlabeled, "disagreements": disagreements}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sample")
    s.add_argument("--scores", type=Path, required=True)
    s.add_argument("--n", type=int, default=30, help="accepted rows to label (precision sample)")
    s.add_argument("--n-other", type=int, default=10, help="rejected/review rows (diagnostic)")
    s.add_argument("--out", type=Path, required=True)
    m = sub.add_parser("measure")
    m.add_argument("--scores", type=Path, required=True)
    m.add_argument("--labels", type=Path, required=True)
    a = ap.parse_args(argv)
    scores = _read(a.scores)
    if a.cmd == "sample":
        rows = sample(scores, a.n, a.n_other)
        with a.out.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=["entity_key", "name", "domain", "sample", "your_answer"])
            w.writeheader()
            w.writerows(rows)
        print(f"{len(rows)} rows to label -> {a.out}")
        return 0
    res = measure(scores, _read(a.labels))
    p, (lo, hi) = res["accept_precision"], res["accept_precision_ci95"]
    if p is None:
        print("accept precision: no accepted row was labelled")
    else:
        print(f"accept precision: {res['accepts_correct']}/{res['accepts_checked']} = {p:.0%} "
              f"(95% interval {lo:.0%} to {hi:.0%}); {res['accepts_in_scores']} accepted rows in scores")
    if res["insufficient_evidence"]:
        print(f"INSUFFICIENT EVIDENCE: fewer than {MIN_ACCEPTS} accepted rows labelled. "
              "Label more accepted rows (or score more rows) before trusting the threshold.")
    print(f"rejected but you said fit: {res['rejected_but_fit']} of {res['rejects_checked']} rejected rows you labelled")
    if res["unlabeled"]:
        print(f"unlabelled rows skipped: {res['unlabeled']}")
    for d in res["disagreements"]:
        print(f"  disagree: {d['name'] or d['entity_key']}: judge {d['judge']}, you: {d['you']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
