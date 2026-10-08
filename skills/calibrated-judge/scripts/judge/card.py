#!/usr/bin/env python3
"""The PACKET CARD, judge-side — what this decision read, in plain English.

    from judge import card
    print(card.card(result, packet=evidence, criteria=icp))

The card itself is Crawford's (`skills/crawford/scripts/lib/packets.py`
-> `explain`), because the packet contract is Crawford's and a second renderer
would be a second place a field name can drift. This module is the judge's half
of it: it resolves the decision's BATTERY, so every question is printed in the
words it was actually asked in and beside the evidence fields it reads, and then
hands the whole lot to `packets.explain`.

Why it exists at all. The operator this system is being built for is a founder
or a sales leader — not an engineer — and the judge's answer is a float and four
review reasons. A number with no evidence under it is indistinguishable from a
guess, and the measured failure mode of this whole stack is a packet bug that
"reports that the evidence did not help, when the evidence was never read"
(the calibrated-judge doctrine (summarized in `skills/calibrated-judge/SKILL.md`)). A card makes an
empty packet LOOK empty, before anyone is asked to trust a verdict built on it.

Two shapes come in here and both work:

  * a live `JudgeResult` plus the evidence it was judged on — the full card,
    with values, dates and provenance;
  * a decision-log row on its own — the log keeps the evidence HASH, not the
    evidence, so the card renders from the row's `packet_richness` and says in
    one line that the values are not on the row. That is the review-queue case.

Nothing here raises: a card that cannot be built says so, because a review row
that arrives with no card must still arrive.
"""
from __future__ import annotations

from . import config, packets

#: Which fit decision a verdict belongs to, when nobody said. The verdict
#: strings are the batteries' own (`judge/fit.py`).
_VERDICT_DECISION = {
    "in_icp": "icp_fit", "out_of_icp": "icp_fit",
    "persona_match": "persona_fit", "not_persona": "persona_fit",
}


def _get(result, key, default=None):
    """One field off a JudgeResult, its `as_dict()`, or a decision-log row."""
    if result is None:
        return default
    if isinstance(result, dict):
        value = result.get(key, default)
    else:
        value = getattr(result, key, default)
    return default if value is None else value


def resolve_decision(result=None, packet=None, decision=None):
    """The decision this answer is about — named, or read off the row.

    Guessing is ordered cheapest-and-surest first: what the caller said, what
    the log row recorded, what the verdict can only have come from, and only
    then the packet's kind. A wrong guess costs the card its question texts,
    never a verdict, so the last rung is allowed to be a guess.
    """
    for candidate in (decision, _get(result, "decision"),
                      (_get(result, "raw", {}) or {}).get("decision")):
        if candidate and str(candidate) in config.DECISIONS:
            return str(candidate)
    verdict = str(_get(result, "verdict", "") or "").strip().lower()
    if verdict in _VERDICT_DECISION:
        return _VERDICT_DECISION[verdict]
    kind = str((packet or {}).get("kind") or "") if isinstance(packet, dict) else ""
    if kind == "person":
        return "persona_fit"
    if kind == "company":
        return "icp_fit"
    return None


def resolve_version(result=None, version=None):
    """The battery version this answer was RECORDED under, if the row says.

    Every decision-log row carries `battery_version` (`judge/log.py`), and it
    is the only honest source for which wording a historical answer was given
    against. Nothing is guessed: a row that does not carry one gets None, and
    the card then shows the battery this harness ships today.
    """
    for candidate in (version, _get(result, "battery_version"),
                      (_get(result, "raw", {}) or {}).get("battery_version")):
        if candidate:
            return str(candidate)
    return None


def resolve_questions(decision, version=None):
    """`(questions, note)` for a decision, at the version it was asked under.

    The whole battery, not the applicable subset: a question that was NOT
    asked because its evidence is absent is exactly what the operator needs to
    see, and `packets.explain` files it under "Not asked, and why".

    When the row records a `battery_version` this harness cannot resolve, the
    card gets NO questions and a note saying the historical wording is
    unavailable. Substituting today's wording would print an operator a
    question that row was never asked, which is
    a fabricated record of what a judge read.
    """
    if not decision:
        return [], ""
    try:
        from . import core                                  # noqa: PLC0415
        battery, got = core.resolve_battery(decision, version or None)
    except Exception:                                       # noqa: BLE001
        battery, got = None, None
    if not battery:
        if version:
            return [], (f"The battery this row was judged under ({version}) is "
                        "not one this harness ships, so the historical question "
                        f"wording is unavailable for battery {version}. The "
                        "current wording is NOT substituted.")
        return [], ""
    if version and str(got or "") != str(version):
        return [], (f"Historical question wording unavailable for battery "
                    f"{version} — this harness resolves "
                    f"{got or 'no matching battery'} for {decision}, and the "
                    "current wording is NOT substituted for the wording this "
                    "row was actually asked.")
    questions = list(battery.get("questions") or [])
    insufficient = battery.get("evidence_insufficient")
    if insufficient:
        questions = questions + [insufficient]
    return questions, ""


def battery_questions(decision, version=None):
    """Every question in this decision's battery, with its `requires`.

    Empty when the recorded `version` is one this harness no longer ships —
    see `resolve_questions`, which also carries the note explaining that.
    """
    return resolve_questions(decision, version)[0]


def card(result, packet=None, criteria=None, *, decision=None, width=100) -> str:
    """One screen of plain English about ONE judged row. Never raises."""
    if not hasattr(packets, "explain"):
        # The card renderer lives in Crawford's packet library (2026.09.21.1+).
        # An older sibling Crawford still builds packets; it just cannot draw
        # the card — say so, instead of a bare AttributeError.
        return ("PACKET CARD — needs Crawford 2026.09.21.1 or later (the card "
                "renderer lives in Crawford's packet library). Update Crawford "
                "(the sibling skills/crawford folder) and retry; the row "
                "still needs a human and the decision log carries its evidence.")
    try:
        decision = resolve_decision(result, packet, decision)
        version = resolve_version(result)
        questions, note = resolve_questions(decision, version)
        head = ""
        if decision:
            head = f"Decision: {decision}"
            head += f" · battery {version}" if version else ""
            head += "\n"
        if note:
            head += note + "\n"
        kwargs = {}
        if "decision" in getattr(packets.explain, "__code__",
                                 type("_", (), {"co_varnames": ()})).co_varnames:
            kwargs["decision"] = decision
        return head + packets.explain(packet, rules=criteria,
                                      questions=questions, result=result,
                                      width=width, **kwargs)
    except Exception as exc:                                # noqa: BLE001
        return ("PACKET CARD — this card could not be built "
                f"({type(exc).__name__}: {str(exc)[:160]}). The row still "
                "needs a human; the decision log carries its evidence hash.")


#: How much of "what the judge read" fits in one spreadsheet cell a reviewer
#: scans on a phone. The full card is one file away.
READ_CAP = 300
READ_TOP_N = 3


def what_the_judge_read(row, *, limit=READ_TOP_N, cap=READ_CAP) -> str:
    """The judge's strongest `limit` questions AND the fields each one reads.

    The review queue already showed a reviewer three probabilities. Three
    probabilities with no evidence names beside them tell a human which
    question moved, never what it was looking at — and "the model was unsure"
    is not a question anybody can answer. This is that missing half, in one
    cell: `question <- field, field`.
    """
    try:
        per_question = _get(row, "per_question", {}) or {}
        if not isinstance(per_question, dict) or not per_question:
            return ""
        decision = resolve_decision(row)
        by_id = {str(q.get("id")): q
                 for q in battery_questions(decision, resolve_version(row))
                 if isinstance(q, dict) and q.get("id")}
        present = list((_get(row, "packet_richness", {}) or {})
                       .get("present_fields") or ())
        scored = []
        for qid, answer in per_question.items():
            hit = packets._probability(answer)              # noqa: SLF001
            if hit is None:
                continue
            scored.append((float(hit[1]), str(qid)))
        scored.sort(key=lambda kv: (-kv[0], kv[1]))
        parts = []
        for value, qid in scored[:limit]:
            base = by_id.get(qid) or by_id.get(qid.split("__")[0]) or {}
            resolved = packets._resolve_requires(              # noqa: SLF001
                base.get("requires"), present)
            # 2026.09.21.1 returns a fourth member (the alias map); an older
            # Crawford returns three, and this cell must render on both.
            here, gone, unknown = resolved[0], resolved[1], resolved[2]
            reads = packets._read_labels(                       # noqa: SLF001
                here, gone, unknown, *resolved[3:])
            parts.append(f"{qid}={value:.2f} <- "
                         + (", ".join(reads) if reads else "the whole packet"))
        text = "; ".join(parts)
        return text[:cap - 1] + "…" if len(text) > cap else text
    except Exception:                                       # noqa: BLE001
        return ""


def cards_markdown(rows, *, run_id=None) -> str:
    """One file, one card per review row — what the human is being asked about.

    The decision log keeps the evidence HASH rather than the evidence, so these
    cards carry the fields a packet held and what was missing, not the values.
    That is still the difference between "the model was unsure" and "the About
    page 404s and there is no registry record", which is the whole reason the
    review queue exists.
    """
    rows = list(rows or ())
    out = [f"# What the judge read — {run_id or 'all runs'}", "",
           f"{len(rows)} row(s) the judge could not settle. One card each: "
           "what it read, what was missing and why, the questions it asked, "
           "and the answer it reached.", ""]
    if not rows:
        out += ["Nothing to review.", ""]
    for row in rows:
        entity = str(_get(row, "entity_key", "") or "(no entity key)")
        decision = resolve_decision(row) or "(decision not recorded)"
        out += [f"## `{entity}` — {decision}", "", "```",
                card(row, packet=None, criteria=None), "```", ""]
    return "\n".join(out)
