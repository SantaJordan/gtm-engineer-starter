# Phase 5 — Hypothesis Generator Prompt (THE CREATIVE CORE)

You are generating "what would have preceded this?" hypotheses for one archetype. This is the differentiator of the entire skill. Most win/loss tooling tells you why deals closed. This tells you **what was visible about the company 3–12 months before they were even in market** — and how to find more like them.

You are running with Sequential Thinking enabled. Use it. Do not skip the ideation step.

## What "preceded this?" means

For every customer in this archetype, you've been given:

- The pain language they used (verbatim quotes from calls), **each tagged with `speaker_employment` and `elicitation`** (Speaker Attribution Protocol — inline copy at `references/speaker-attribution-protocol.md`).
- The temporal marker for when they started searching for a solution.
- The company-state observations from Phase 4 (what was happening at the company at pain emergence).
- The full Phase 2 signal panel (everything observable from outside).

**Weight the evidence by attribution — this is non-negotiable:**

- **Only `speaker_employment: customer` phrases are demand.** A `company` phrase is the seller talking; a `unknown` phrase is an unplaced speaker. Never build a hypothesis whose pain rationale rests on a `company` or `unknown` phrase.
- **`elicitation: unprompted` is the gold signal — weight it highest.** A pain the customer raised on their own is real demand and the strongest precursor evidence. Prefer hypotheses grounded in unprompted customer pain when estimating `hypothesized_lift` and when ranking which hypotheses to ship.
- **`elicitation: led` is weak evidence — discount it.** The customer said it only after the seller framed it. A hypothesis grounded *only* in led pain across the archetype is suspect; lower its `hypothesized_lift` and prefer to find an unprompted phrase before shipping it.
- **`elicitation: seller_echo` is NOT customer demand — ignore it entirely.** It is the seller's stock pitch parroted back. A hypothesis may not cite a `seller_echo` phrase in `rationale_from_phase_4_themes`. If the only support for a pain is seller_echo, the pain is the seller's, not the market's — do not generate the hypothesis.

Your job: imagine yourself standing 6 months *before* the customer started searching. What was already true about the company that — if you'd known to look — would have told you they'd be in market for what we sell?

The answer is **not**:

- "They had pain X" (we already know that — it's what they bought to solve).
- "They were a B2B SaaS company in vertical Y, 50–1000 employees" (the lazy ICP we're replacing).
- "They were growing fast" (banned by Gate 2 — describes thousands of companies).

The answer **is**:

- A specific public-data observation that, in retrospect, was the precursor — a license filed, a permit issued, a state registration changed, a content cadence shift, a niche role posted, a regulator action published, a peer's case study published.
- Something queryable on a *cold* prospect where we have no relationship and no Gong call.
- Something where the lead-time matters and is named.

## Inputs (per archetype)

```
archetype:
  id: <int>
  name: <string>
  one_liner: <string>
  canonical_features: [list]
  member_count: <int>
  outcome_breakdown: {won, lost, healthy, churned}

themes_from_phase_4:           # array of customer themes
  - customer_id: <id>
    pain_language:             # each phrase carries speaker + elicitation (Phase 1.4)
      - phrase: <verbatim>
        speaker_employment: company | customer | unknown   # only `customer` is real demand
        elicitation: unprompted | led | seller_echo        # only `unprompted` is the gold signal
    underlying_pain: <string>
    temporal_markers: {...}
    company_state_at_pain_emergence: [observations]
    extraction_confidence: <float>   # lowered upstream when pain was mostly led/echo
    outcome: <string>

phase_2_signal_panels:         # full panel per customer
  - customer_id: <id>
    signals: {...}

available_external_sources:    # what we can actually query
  - exa | firecrawl | openweb_ninja | apify_linkedin | serper_news
  - wayback (archive.org time-travel prover — scripts/wayback_timetravel.py)
  - any_optional_vertical_data_warehouse (only if the operator's panel includes it — never assume)
  - state_open_data_portals (varies by state)

source_temporal_classification:  # references/source-temporal-classification.json
  # per source: class (point_in_time_record | current_state_only),
  # wayback_friendly (true | false | varies), publication_lag_days
```

## Your output

For this archetype, generate **10–30 candidate hypotheses**. JSONL, one per line. Then run each through the gates (next section) and emit only survivors to `outputs/<run-id>/hypotheses.jsonl`.

Each hypothesis:

```json
{
  "hypothesis_id": "<archetype_id>-h<n>",
  "archetype_id": <int>,
  "signal": "<concrete observable fact>",
  "observability_source": "<source name from available_external_sources>",
  "queryable_field": "<a field listed in testable_fields for that source, e.g. location_count, or a record field like items.snippet>",
  "predicate": {"op": "gte | gt | lte | lt | eq | ne | truthy | falsy | contains | regex", "value": <threshold or text>},
  "hypothesized_lead_time_days": <int>,
  "hypothesized_lift": <float>,
  "anti_match_examples": [
    "<companies that have this signal but aren't a fit and why>"
  ],
  "rationale_from_phase_4_themes": [
    {
      "customer_id": "<id>",
      "phase_4_phrase": "<verbatim quote>",
      "connection": "<why this phrase implies this signal would have been visible>"
    }
  ],
  "weirdness_score": 1-5,
  "data_combination_required": "<single | combination of N signals>",
  "temporal_provability": {
    "best_case_tag": "ARCHIVED_BEFORE | RECORD_DATED_BEFORE | RETRO_ASSUMED",
    "how": "<one sentence: the mechanism that would prove this signal was visible at reference_date - lead_time>"
  },
  "archive_search_pattern": "<short literal text Phase 6 greps for in archived page captures — only for wayback-friendly signals>",
  "record_date_field": "<dotted path to the record's intrinsic date field — only for point_in_time_record sources>"
}
```

**Typed signals (Gate 4 enforces this).** `queryable_field` must be one of the
fields in `testable_fields` for the chosen `observability_source`: those are the
only fields the Phase 2 parsers emit. The `predicate` must fit the field's type:
a numeric field (int/float) needs a threshold (`{"op": "gte", "value": 2}`), a
text field uses `contains` or `regex`, a boolean field may omit the predicate
(it means truthy). For a source that returns dated records (news `items`, search
`results`), test a record field (`items.snippet`) with `regex` or `contains`; the
firm pass will then date the specific record that matches. Anything else is
rejected before any statistics.

### Temporal provability — declare it honestly (Phase 6 enforces it)

Phase 6 no longer takes "would have been visible" on faith. Every claim about
the past earns one of three visibility tags, and only the first two count as
firm evidence in lift computation:

- **ARCHIVED_BEFORE** — an archive.org capture at-or-before the customer's
  reference date shows the signal. Provable for public *pages* (`wayback_friendly:
  true` sources). Provide `archive_search_pattern`: the literal text the prover
  greps for in captures (e.g., `"SOC 2"`, `"compliance officer"`). Choose text
  that is distinctive and stable — not a full sentence that a copy edit breaks.
- **RECORD_DATED_BEFORE** — the record carries its own date (filing date,
  permit date, publish date) that — after the source's publication-lag
  haircut — precedes the reference date. Provide `record_date_field`.
- **RETRO_ASSUMED** — only today's web can be observed. This is the CEILING for
  `wayback_friendly: false` sources (LinkedIn employee counts, dynamic API
  panels). Never claim a better tag than the source classification allows —
  a LinkedIn-based hypothesis stating `best_case_tag: ARCHIVED_BEFORE` is lying
  and dies at Gate 4.

A `best_case_tag: RETRO_ASSUMED` hypothesis is still allowed to exist — but say
so, and know that it can never ship as a firm signal on its own. Prefer to pair
it with a provable co-signal (combination) or find the same fact in a dated
record (a press release, a filing) that upgrades it to RECORD_DATED_BEFORE.

## Generation guidance — go weird

The whole point of this skill is finding non-obvious signal *combinations* that the lazy ICP misses. Boring hypotheses ("the company has a website" — no kidding) waste cycles in Phase 6. Bias toward weird:

- **Combinations beat singletons.** "Company filed a state-level multi-entity registration AND has a careers page mentioning 'compliance' AND has fewer than 10 LinkedIn employees" is more discriminating than any of those alone. Generate combinations explicitly when no single signal has obvious lift.
- **Use temporal absence.** "Company has NOT posted a job for [role] in 18 months despite [growth signal]" can be a stronger signal than presence.
- **Use public records the lazy ICP ignores.** State open data portals, county permits, regulator actions, court filings, association membership rosters, conference speaker lists, podcast guest appearances.
- **Use content cadence shifts.** Blog frequency drop, sudden uptick in technical hiring, new careers page section, new pricing tier published.
- **Use cross-source contradictions.** "LinkedIn says 50 employees but Apollo says 8" — that *itself* is a signal of either understaffing in compliance or org-chart messiness, both of which create pain.
- **Use peer evidence.** "A direct competitor of this company published a case study with one of our existing customers in the past 90 days" — high signal, low noise.

`weirdness_score` of 1 = boring (will probably be obvious lift), 5 = creative (less obvious but more defensible if it survives). Aim for distribution skewed toward 3+.

## Hard gates — APPLY EVERY ONE

After generating hypotheses, run each through `prompts/hard-gates.md`. Reject any that fail any gate. Specifically:

- **Gate 1 (Horizontal Disqualification):** A hypothesis like "company has > 50 employees" applies to thousands of companies. KILL.
- **Gate 2 (Causal Link):** Does the signal directly prove the pain in `rationale_from_phase_4_themes`? Banned signals (auto-kill): generic funding rounds, generic hiring, M&A activity, headcount growth, "tech stack adoption". Allowed: signals that ARE the pain or its proximate cause (license expiration, regulatory violation, deadline filing, etc.).
- **Gate 3 (No Aggregates Ban):** Hypothesis must be checkable per-company, not as an industry average. KILL "the average company in this archetype has X" hypotheses.
- **Gate 4 (Technical Feasibility):** You MUST name a real API field, SQL column, or scraping selector. "We could probably get this from somewhere" is auto-kill. If you don't know the source, don't ship the hypothesis. **Gate 4b (Temporal Provability):** you MUST declare `temporal_provability` with a valid `best_case_tag` consistent with the source's classification — a `wayback_friendly: false` source can never claim better than `RETRO_ASSUMED`; a wayback-friendly claim must include `archive_search_pattern`; a dated-record claim must include `record_date_field`. Missing or inconsistent = auto-kill.
- **Gate 5 (Pain Connection):** The hypothesis must trace back to a verbatim phrase in `rationale_from_phase_4_themes`. If no Phase 4 customer in this archetype actually articulated this pain, the hypothesis is invented and KILL. **The cited phrase must be `speaker_employment: customer` AND `elicitation` of `unprompted` or (weakly) `led` — never `seller_echo`, never `company`/`unknown`.** A hypothesis whose only pain support is a seller_echo or a non-customer line fails Gate 5 and is KILLed: it traces to the seller's mouth, not the buyer's.

When a hypothesis fails a gate, log the failure to `outputs/<run-id>/hypotheses-rejected.jsonl` for traceability — the operator reads these to spot prompt-engineering issues. Don't silently drop them.

## What you must not do

- Don't invent pain phrases. Every `phase_4_phrase` in `rationale_from_phase_4_themes` must be a real verbatim from Phase 4 — and must be an `unprompted` (or weakly `led`) **customer** phrase. A `seller_echo`, `company`, or `unknown` phrase is not eligible as rationale.
- Don't hypothesize signals from sources the operator doesn't have. The `available_external_sources` list is authoritative. If a vertical-specific data warehouse isn't listed, don't generate hypotheses that depend on it — generate ones that only need the built-in sources.
- Don't repeat hypotheses. If two hypotheses reduce to the same observable fact + same source + same lead time, they're the same hypothesis.
- Don't ship hypotheses with `hypothesized_lift < 1.5`. If your best estimate is below 1.5x, the signal isn't worth Phase 6's expensive backcast.

## Tone

Cold and concrete. No "I think this could be interesting" — say what it is, where it lives, why it would have been visible. The output is read by `scripts/backcast_validate.py`, which doesn't care about your prose.
