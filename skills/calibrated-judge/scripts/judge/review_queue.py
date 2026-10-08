#!/usr/bin/env python3
"""The review queue — every "I am not sure" the judge said, in one place a
human can answer.

    python3 -m judge.review_queue --run-id <run_id> [--sheet]

`active` is only honest if the maybes actually reach somebody. The judge's
three outcomes are accept, reject and REVIEW; the third one is not a verdict,
it is a request. This module turns every `review` row a run logged into one CSV
at `outputs/calibrated-judge/review/<run_id>.csv` — and, with `--sheet`, into a
Google Sheet a reviewer can answer from a phone.

WHAT A REVIEWER GETS, PER ROW
    entity · decision · judge action · review reasons · top question
    probabilities · WHAT THE JUDGE READ · incumbent verdict · evidence link ·
    YOUR DECISION · note

`what the judge read` names, for the three questions that moved most, the
evidence fields each one was looking at — because three probabilities with no
field names beside them tell a reviewer which question wobbled and never what
it was wobbling about. Beside the CSV this module also writes
`<run_id>-cards.md`: the full PACKET CARD for every row, so "the model was
unsure" arrives as "the About page 404s and there is no registry record".

The first eight are the judge's and the incumbent's own words and numbers —
nothing rounded, nothing invented, and no verdict implied where the judge
declined to reach one. The last two are blank: they are the human's.

Answers written back into `YOUR DECISION` become ground truth through
`judge/actuals.py` (a settled actual ends that question for every later run —
see `core.judge`, step 2). Nothing in this file writes an answer itself.

THE SHEET IS OPT-IN. Without `--sheet` nothing touches Google, which is what
makes this importable from a test, a cron job, or a machine with no credential.
"""
from __future__ import annotations

import argparse
import csv
import datetime as _dt
import re
import sys
from pathlib import Path

from . import card as card_mod
from . import config, log

#: Exactly the columns a reviewer was promised, in order. The last two are the
#: human's; everything before them is evidence.
HEADERS = ("entity", "decision", "judge action", "review reasons",
           "top question probabilities", "what the judge read",
           "incumbent verdict", "evidence link", "YOUR DECISION", "note")

#: The action this queue exists for. `insufficient` is the judge failing to
#: read its evidence, not asking a question, and it belongs in the error report.
REVIEW_ACTION = "review"

#: How many question probabilities a row shows. A reviewer scanning a Sheet on
#: a phone reads three; the rest are one grep away in the decision log.
TOP_N = 3

#: Sheets' own limits, so a 40k-row queue fails loudly here rather than halfway
#: through a Google write.
SHEET_MAX_ROWS = 20000


# ------------------------------------------------------------------- reading

def review_rows(run_id=None, rows=None):
    """Every `review` row for `run_id` (all runs when it is None).

    A row whose judge errored is NOT a review: `judge_unavailable` is an
    outage, and putting it in front of a human as a question wastes the one
    resource this whole system is trying to spend carefully.
    """
    for row in (log.read_all() if rows is None else rows):
        if not isinstance(row, dict):
            continue
        if row.get("action") != REVIEW_ACTION or row.get("error"):
            continue
        if run_id is not None and row.get("run_id") != run_id:
            continue
        yield row


# ------------------------------------------------------------------ shaping

def _probability(answer):
    """(label, p) for one question's answer, or None when it carries no number.
    A question the judge never answered is absent, never a zero."""
    if not isinstance(answer, dict):
        return None
    p = answer.get("p", answer.get("probability"))
    if isinstance(p, (int, float)) and not isinstance(p, bool):
        return "", float(p)
    probs = answer.get("probs") or answer.get("probabilities")
    if isinstance(probs, dict) and probs:
        pairs = [(str(k), float(v)) for k, v in probs.items()
                 if isinstance(v, (int, float)) and not isinstance(v, bool)]
        if pairs:
            label, value = max(pairs, key=lambda kv: kv[1])
            return label, value
    return None


def top_probabilities(per_question, limit=TOP_N) -> str:
    """The judge's strongest `limit` questions, as `qid=0.91; qid=0.55`.

    The numbers are the judge's own, printed to two places and never rounded
    into a claim it did not make.
    """
    if not isinstance(per_question, dict):
        return ""
    scored = []
    for qid, answer in per_question.items():
        hit = _probability(answer)
        if hit is None:
            continue
        label, value = hit
        name = f"{qid}:{label}" if label else str(qid)
        scored.append((value, f"{name}={value:.2f}"))
    scored.sort(key=lambda kv: kv[0], reverse=True)
    return "; ".join(text for _, text in scored[:limit])


def evidence_link(row) -> str:
    """Where the full evidence for this row lives: the decision-log file, and
    the evidence hash inside it. Not a URL — the evidence never leaves the
    machine, and a link to somewhere it is not would be worse than none."""
    ts = str(row.get("ts") or "")
    try:
        when = _dt.datetime.fromisoformat(ts)
    except ValueError:
        when = None
    path = log.path_for(when)
    sha = str(row.get("evidence_sha256") or "")
    return f"{path}#{sha}" if sha else str(path)


def to_row(row) -> list:
    """One logged decision -> one reviewer-facing line."""
    reasons = row.get("review_reasons") or []
    return [
        str(row.get("entity_key") or ""),
        str(row.get("decision") or ""),
        str(row.get("action") or ""),
        "; ".join(str(r) for r in reasons),
        top_probabilities(row.get("per_question")),
        card_mod.what_the_judge_read(row),
        "" if row.get("incumbent_verdict") is None
        else str(row.get("incumbent_verdict")),
        evidence_link(row),
        "",                      # YOUR DECISION — the human's
        "",                      # note — the human's
    ]


# ------------------------------------------------------------------- writing

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def safe_name(run_id) -> str:
    """A filename that cannot leave the review directory, whatever a caller
    called its run."""
    name = _SAFE.sub("-", str(run_id or "all")).strip("-.") or "all"
    return name[:120]


def review_dir() -> Path:
    return config.out_dir() / "review"


def csv_path(run_id) -> Path:
    return review_dir() / f"{safe_name(run_id)}.csv"


def cards_path(run_id, csv_at=None) -> Path:
    """Where the full cards land: beside the CSV, always.

    A reviewer given a spreadsheet and told the evidence is "in the decision
    log" does not go and read the decision log. The cards sit next to the file
    they are already open.
    """
    if csv_at is not None:
        csv_at = Path(csv_at)
        return csv_at.with_name(csv_at.stem + "-cards.md")
    return review_dir() / f"{safe_name(run_id)}-cards.md"


def write_cards(run_id=None, rows=None, path=None) -> Path:
    """Write one packet card per review row and return the file's path.

    An EMPTY queue still writes the file, for the same reason the CSV does:
    "nothing to review" has to be something a human can see.
    """
    out = Path(path) if path else cards_path(run_id)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(card_mod.cards_markdown(list(review_rows(run_id, rows)),
                                           run_id=run_id), encoding="utf-8")
    return out


def write_csv(run_id=None, rows=None, path=None) -> Path:
    """Write the queue and return its path.

    An EMPTY queue still writes the header. "Nothing to review" is a result a
    reviewer needs to be able to see; a missing file is indistinguishable from
    a writer that never ran.
    """
    out = Path(path) if path else csv_path(run_id)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(HEADERS)
        for row in review_rows(run_id, rows):
            writer.writerow(to_row(row))
    return out


# -------------------------------------------------------------- the Sheet

def sheet_title(run_id, when=None) -> str:
    day = when or _dt.date.today()
    return f"Judge review — {run_id or 'all runs'} — {day:%Y-%m-%d}"


def _sheets_service():
    """The house Google client, or None. Never a crash: a machine without the
    credential still gets its CSV."""
    try:
        sys.path.insert(0, str(config.blueprint_root() / "tools" / "google-auth"))
        from google_auth import get_service          # noqa: PLC0415
        return get_service("sheets", "v4")
    except Exception:
        return None


def create_sheet(run_id, rows=None, service=None, when=None):
    """Create the review Sheet and fill it. Returns its URL, or None when
    Google is not reachable from here."""
    service = service or _sheets_service()
    if service is None:
        return None
    values = [list(HEADERS)] + [to_row(r) for r in review_rows(run_id, rows)]
    if len(values) - 1 > SHEET_MAX_ROWS:
        raise ValueError(f"{len(values) - 1} review rows is more than a Sheet "
                         f"should carry ({SHEET_MAX_ROWS}); read the CSV")
    title = sheet_title(run_id, when)
    doc = service.spreadsheets().create(
        body={"properties": {"title": title}}).execute()
    sheet_id = doc["spreadsheetId"]
    service.spreadsheets().values().update(
        spreadsheetId=sheet_id, range="A1",
        valueInputOption="RAW", body={"values": values}).execute()
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit"


# ----------------------------------------------------------------------- CLI

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="collect the calibrated judge's review rows for one run")
    ap.add_argument("--run-id", default=None,
                    help="the run to collect (default: every logged run)")
    ap.add_argument("--out", default=None, help="write the CSV here instead")
    ap.add_argument("--sheet", action="store_true",
                    help="also create a Google Sheet (off by default; needs "
                         "no Google helper in this repo; CSV only)")
    args = ap.parse_args(argv)

    rows = list(review_rows(args.run_id))
    path = write_csv(args.run_id, rows, path=args.out)
    cards = write_cards(args.run_id, rows, path=cards_path(args.run_id, args.out))
    print(f"{len(rows)} row(s) for review -> {path}")
    print(f"what the judge read, per row -> {cards}")
    if not args.sheet:
        return 0
    try:
        url = create_sheet(args.run_id, rows)
    except Exception as exc:
        print(f"the Sheet was not created ({type(exc).__name__}: {exc}); "
              f"the CSV above is complete")
        return 0
    if url is None:
        print("Google auth is not available here (no Google helper in this repo) — "
              "the CSV above is complete")
        return 0
    print(url)
    return 0


if __name__ == "__main__":
    sys.exit(main())
