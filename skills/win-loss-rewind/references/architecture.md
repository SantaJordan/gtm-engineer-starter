# Architecture — The 8-Phase Contract

This document defines the artifact contract between phases. Every phase reads named files from `outputs/<run-id>/` and writes named files to the same directory. No globals, no implicit state, every phase resumable.

## Run directory layout

```
outputs/<run-id>/
├── run.json                       # run metadata, mode, args, started_at, completed_at
├── data_confidence.md             # Phase 0 output, requires user approval
├── matched_calls.jsonl            # Phase 1 output
├── audit_report.md                # Phase 1 audit summary
├── calls_with_speaker_attribution.jsonl  # Phase 1.4 output (roster + role-tagged turns + elicitation)
├── customer_panels.jsonl          # Phase 2 output
├── archetypes.md                  # Phase 3 output
├── archetypes.jsonl               # Phase 3 machine-readable
├── themes.jsonl                   # Phase 4 output
├── hypotheses.jsonl               # Phase 5 output (survivors only)
├── hypotheses-rejected.jsonl      # Phase 5 dropped hypotheses, with gate-failure reason
├── validated_signals.md           # Phase 6 output
├── validated_signals.jsonl        # Phase 6 machine-readable
├── discriminator.md               # Phase 7 output — THE DELIVERABLE
├── discriminator.jsonl            # Phase 7 machine-readable (consumed by score mode)
└── cost_summary.json              # cost tracking across all phases
```

## Phase contracts

### Phase 0 — Data Archaeology

| | |
|---|---|
| Reads | User-provided `<csv-path>` (or live SFDC connection, or Gong-only) |
| Writes | `data_confidence.md`, `run.json` |
| Gate | User must approve `data_confidence.md`. Skill halts otherwise. |
| Resumable | Yes — re-run `init` to redo Phase 0. |
| Cost | ~$0.20 (5 high-ARR rows × Firecrawl + Apify probes; one Sonnet inference) |

### Phase 1 — Gong Match & Verify

| | |
|---|---|
| Reads | `data_confidence.md`, customer roster from Phase 0 adapter |
| Writes | `matched_calls.jsonl`, `audit_report.md` |
| Gate | Audit precision ≥ 90%. If lower, halt with diagnostic. |
| Resumable | Yes — `--resume-from 1`. |
| Cost | ~$0.05 per Opus-audited match (adaptive thinking, batch-discounted). 200 matches ≈ $10. |

### Phase 1.4 — Speaker Attribution

| | |
|---|---|
| Reads | `matched_calls.jsonl` (verified pairs) + `gong_calls_raw.jsonl` (raw `parties[]` + transcript) |
| Writes | `calls_with_speaker_attribution.jsonl` — per (call, customer): `roster`, role-tagged `turns` (`company\|customer\|unknown`), elicitation-graded `customer_statements` (`unprompted\|led\|seller_echo`) |
| Gate | None. An `unknown` speaker is never promoted to customer; seller speech is kept as context. |
| Resumable | Yes — re-run `label_speakers.py --run-id <id>`. Free (Gong `parties[].affiliation` metadata). |
| Cost | $0 — no LLM, no paid enrichment for Gong-sourced calls. |
| Protocol | `references/speaker-attribution-protocol.md` (inline copy of the canonical Speaker Attribution Protocol). |

### Phase 2 — External Reality Enrichment

| | |
|---|---|
| Reads | `matched_calls.jsonl` (for the customer roster — calls themselves are unused here) |
| Writes | `customer_panels.jsonl` |
| Gate | None (all enrichment failures are logged inline as `null` fields with error reason). |
| Resumable | Yes — `--resume-from 2`. Per-customer cache prevents re-pulling identical signals. |
| Cost | $0.05–$0.20 per customer depending on which sources are wired. 200 customers ≈ $10–$40. |

### Phase 3 — Auto-Segmentation

| | |
|---|---|
| Reads | `customer_panels.jsonl` |
| Writes | `archetypes.md`, `archetypes.jsonl` |
| Gate | Each surviving archetype must have ≥ 5 members across ≥ 2 outcome classes. |
| Resumable | Yes — `--resume-from 3`. Re-runs clustering + naming. |
| Cost | ~$0.50 (clustering is local; Sonnet does the naming). |

### Phase 4 — Theme Extraction (Batch API)

| | |
|---|---|
| Reads | `matched_calls.jsonl`, `calls_with_speaker_attribution.jsonl` (Phase 1.4 — roster + role-tagged turns + elicitation), `customer_panels.jsonl`, `archetypes.jsonl` |
| Writes | `themes.jsonl` — each `pain_language[]` item carries `speaker_employment` + `elicitation`; pain mined only from `customer` turns, `seller_echo` excluded, confidence lowered when pain is mostly `led`/`seller_echo`. |
| Gate | None — low-confidence themes flagged but not blocking. |
| Resumable | Yes — Batch API persists batch ID; `--resume-from 4` re-uses an in-flight batch. |
| Cost | ~$0.005 per customer (Sonnet batch). 500 customers ≈ $2.50. |

### Phase 5 — Hypothesis Generation

| | |
|---|---|
| Reads | `themes.jsonl`, `archetypes.jsonl`, available_external_sources |
| Writes | `hypotheses.jsonl`, `hypotheses-rejected.jsonl` |
| Gate | At least one hypothesis must survive per archetype, OR archetype is flagged "no hypotheses" and excluded from Phase 6. |
| Resumable | Yes — `--resume-from 5`. |
| Cost | ~$1–$3 per archetype (Opus + Sequential Thinking). 8 archetypes ≈ $8–$24. |

### Phase 6 — Backcasting Validation

| | |
|---|---|
| Reads | `hypotheses.jsonl`, `customer_panels.jsonl` (for retrospective signal pulls) |
| Writes | `validated_signals.md`, `validated_signals.jsonl` |
| Gate | If zero signals survive an archetype's holdout, archetype is flagged "no discriminator found" rather than producing noise. |
| Resumable | Yes — `--resume-from 6`. |
| Cost | $0.10–$0.50 per hypothesis tested (depending on how many historical signal pulls are needed). 100 hypotheses ≈ $10–$50. |

### Phase 7 — Discriminator + Cynical Buyer

| | |
|---|---|
| Reads | `validated_signals.jsonl`, `archetypes.jsonl` |
| Writes | `discriminator.md`, `discriminator.jsonl` |
| Gate | Cynical Buyer 8.0+ ships, 7.0–7.9 one revision, <7.0 destroyed. Per archetype. |
| Resumable | Yes — `--resume-from 7`. |
| Cost | ~$0.50 per archetype (Opus). 8 archetypes ≈ $4. |

**Thresholds from an older version need a re-run, and the call sites changed with
them.** Phases 6-8 now share one presence brain (`scripts/signal_presence.py`), which reads a
signal as present / absent / unknown. The thresholds already in `discriminator.jsonl` were
calibrated against the old permissive fallback, which scored a missing field as present, so
they do not transfer. What moved at the call sites: Phase 7 `score_panel()` now divides by the
OBSERVED weight (a signal whose source never ran is dropped from both the numerator and the
denominator instead of counting as absent) and records each signal's `state` in the
`signal_diff`; Phase 8 `score_against_archetype()` reports the same `state` plus an
`n_unknown` count; Phase 6 rejects a non-finite lift (the nan an empty 2x2 produces) up front
instead of letting it pass every threshold comparison. Open gap: Phase 8 still sums raw
weights without the observed-weight denominator, so a prospect with unenriched sources scores
low against a threshold calibrated with it — read `n_unknown` before trusting a near-miss.

### Phase 8 — `score` mode

| | |
|---|---|
| Reads | Latest `discriminator.jsonl` from any prior run |
| Writes | stdout (or `outputs/<run-id>/scores/<domain>.json`) |
| Gate | None. |
| Resumable | N/A (single-shot). |
| Cost | $0.05–$0.20 per prospect (Phase 2-style enrichment for one company). |

## Total run cost

For a run on 200 customers, ~5 archetypes, ~50 hypotheses generated and ~25 validated:

- Phase 0: $0.20
- Phase 1: $10
- Phase 2: $10–$40
- Phase 3: $0.50
- Phase 4: $1
- Phase 5: $5–$15
- Phase 6: $5–$25
- Phase 7: $2.50
- **Total: ~$35–$95** for a full discriminator generation run.

`score` mode runs after that for $0.05–$0.20 per prospect.

## Resume semantics

`--resume-from N` skips phases 0…N-1 and re-enters at phase N. The skill validates that all required artifacts from prior phases exist before resuming. Missing artifacts → halt with diagnostic.

If the user wants to re-do a single phase in place (e.g., re-cluster archetypes after tweaking the panel), they pass `--rerun-phase N`. This deletes downstream artifacts and re-enters at phase N.

## What the deliverable looks like

`discriminator.md` is the file a CRO actually reads. Per archetype:

```markdown
## Archetype: <name>

**One-liner:** <sentence>

**Members:** <N> customers (won: A / lost: B / healthy: C / churned: D)

**Discriminator (passed Cynical Buyer score X.X):**

To identify a good-fit prospect like this archetype, score on:

| Signal | Source | Field | Threshold | Weight | Lift (train / holdout) |
|--------|--------|-------|-----------|--------|------------------------|
| ...    | ...    | ...   | ...       | ...    | ...                    |

**Combination scoring formula:**

`fit_score = (w1 * I(signal_1 met) + w2 * I(signal_2 met) + ...) / sum(w_i over observed signals)`

Signals whose source never ran (`state: unknown`) leave both sums.

Threshold for "looks-like-archetype": `fit_score >= T`

**Worked example (held-out customer):**

<concrete example showing each signal value + final score + actual outcome>

**Anti-matches to watch for:**

- <company type that has the signals but is not a fit, with reason>

**How to query this on a cold prospect:**

```python
# concrete API code
```
```
