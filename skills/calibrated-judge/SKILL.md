---
name: calibrated-judge
description: Score rows with Jev, TypeSafe's calibrated evaluation model, and get a probability plus one of four actions (accept, reject, review, insufficient) per row instead of a prose opinion. Use this whenever the user wants to score a list for ICP fit or persona fit, decide which rows a human must review, or check a yes/no claim about a company or person from an evidence packet. Reads the packets that skills/crawford builds. Writes scores.csv, a review queue, and an append-only decision log.
version: "1.0-starter"
author: "Jordan Crawford / Blueprint GTM"
requires-keys: [TYPESAFE_API_KEY or OPENROUTER_API_KEY]
---

# Calibrated judge (Jev)

**Jev** is an evaluation model from TypeSafe. You give it some text (the "state")
and typed questions about it. It answers with **calibrated probabilities**. A
calibrated 0.9 means that, across many answers, about 90% of the 0.9s are true.
That is what makes a rule like "accept only above 0.90" a sensible **starting
threshold**. It is not proof the rule is right for your ICP and your packets:
check it on your own rows (below, "Check the threshold on your rows").

This skill wraps Jev in a judge that turns those probabilities into one of four
actions per row:

| Action | Meaning |
|---|---|
| `accept` | confident the row fits (a support question cleared 0.90) |
| `reject` | confident it does not (a named disqualifier cleared 0.90) |
| `review` | unsure; a human should look. These rows go to `review_queue.csv` |
| `insufficient` | no answer: no key, judge switched off, or too little evidence. **Not a "no".** |

## The one rule

**`jev_client.ask()` is the only door to Jev.** Every call goes through
`scripts/jev_client.py`. Do not hand-roll HTTP calls to TypeSafe or OpenRouter
elsewhere. One client means one cache, one retry policy, one place keys are read,
and one place they are kept out of logs.

Jev is the scorer. Do not swap in a general chat model as a "fallback judge":
its numbers are not calibrated, so the 0.90 thresholds would mean nothing.

## Setup (keys)

Put **one** of these in `.env` at the repo root (see `.env.example`):

1. `TYPESAFE_API_KEY`: TypeSafe direct. Sign up at console.typesafe.ai. The
   client tries this first.
2. `OPENROUTER_API_KEY`: the same model through OpenRouter (`typesafe/jev-1.13`
   on OpenRouter's `/api/alpha/decisions` endpoint).

Check your key with one tiny call (a fraction of a cent):

```bash
python3 skills/calibrated-judge/scripts/jev_client.py probe --no-cache
```

It prints which backend answered, the model, the latency and the cost. With no
key, every backend is skipped and the judge returns `insufficient`; nothing is
spent.

Jev is priced per input token (TypeSafe lists $0.042 per million). One fit
question about a short packet costs well under a cent.

## Score a list

```bash
python3 skills/calibrated-judge/scripts/judge_packets.py \
    --packets out/my-run/packets \
    --icp icp.json \
    --out out/my-scores
```

- `--decision persona_fit --persona persona.json` scores people packets instead.
- `--max-usd 1.0` (default) stops the run once metered spend passes $1.
- `--mode off` makes no call at all (every row `insufficient`).

Outputs in `--out`:
- `scores.csv`: one line per row with action, verdict, probability, reasons, cost.
- `review_queue.csv` + `review_cards.md`: only the `review` rows, as a human worklist.
- `judge/decisions/YYYY-MM.jsonl`: the append-only decision log.
- `summary.json`: counts and spend.

## In Python

```python
import sys; sys.path.insert(0, "skills/calibrated-judge/scripts")
from judge import judge
result = judge("icp_fit", packet, icp={"inclusions": [...], "exclusions": [...]}, mode="shadow")
print(result.action, result.final_probability, result.review_reasons)
```

Decisions available: `icp_fit`, `persona_fit` (fit), and `real_website`,
`employment_now`, `same_identity`, `site_status`, `role_holder_now` (identity).

Modes (`CALIBRATED_JUDGE_MODE`): `off` (default for library calls: no call),
`shadow` (the judge answers and is logged, but changes nothing upstream),
`active` (the judge's accept/reject may replace an upstream verdict, only for
decisions listed in `CALIBRATED_JUDGE_ACTIVE_DECISIONS`). `judge_packets.py`
runs in `shadow` by default.

## Rank the accepted rows (quantify)

```bash
python3 skills/calibrated-judge/scripts/quantify.py --scores out/my-scores/scores.csv \
    --packets out/my-run/packets --metric-name offices --out out/my-scores/shortlist.csv
```

It reads one number per accepted row from a packet field (default
`site.homepage_text`, pattern: "N offices / locations / clinics / practices"; set
`--field` and `--pattern` for your own), quotes where it found it, and ranks rows.
A row with no number is kept, ranked last, and left blank: missing is not zero.

## Check the threshold on your rows (free)

```bash
python3 skills/calibrated-judge/scripts/check_precision.py sample \
    --scores out/my-scores/scores.csv --n 30 --out out/my-scores/to_label.csv
# fill your_answer (yes/no) for each row without looking at the judge's answer
python3 skills/calibrated-judge/scripts/check_precision.py measure \
    --scores out/my-scores/scores.csv --labels out/my-scores/to_label.csv
```

It prints accept precision (of the rows Jev accepted, how many you said fit), how
many rejected rows you said fit, and every disagreement. If precision is below
what you can live with, raise the threshold, fix the ICP rules or enrich the
packets, then measure again on new rows.

## Thin packets

If a packet is thin and the answer is `review` or `insufficient`, the fix is more
evidence, not a better prompt. Rebuild the packet with
`build_packets.py --enrich` and score again.

## Reviewing the top rows

Jev scores every row. Then, for a small number of top-ranked rows (say the best
10 to 20 before you send), have a stronger model read the packet a second time.
Do that with subagents inside the tool you already use (Claude Code or Codex
subagents), not by wiring up a separate cheap API model. Small models like Haiku
are not the best at this job.

If a step needs more intelligence than your default model, ask your agent, on the
day you run it, to look up the current best models by dollars per unit of
intelligence on public benchmarks. Do not hardcode a model: the answer changes
every few months.

## Self-hosted alternatives

If you cannot use a hosted model, open zero-shot classifiers exist, such as
GLiClass and DeBERTa-based NLI zero-shot models. They are not wired into this
repo, and their probabilities are not calibrated the way the thresholds here
assume. You would need your own labelled set to set thresholds.

## Files

- `scripts/jev_client.py`: the client (backends, cache, retries, key handling).
- `scripts/judge/`: the judge (`core.py`), fit questions (`fit.py`), cards, log, review queue.
- `scripts/bakeoff/`: question batteries and composers the judge loads.
- `scripts/judge_packets.py`: the list command.
- `scripts/quantify.py`: rank accepted rows by one number from the packets.
- `scripts/check_precision.py`: label ~30 rows by hand and measure the judge.
- `fixtures/packets/`: synthetic packets used by the tests.
- `tests/`: run `python3 -m pytest -q skills/calibrated-judge/tests`.

The bake-off harness, recipe-tuning loop and calibration reports behind the
thresholds are in the full version (see `NOT-INCLUDED.md`).
