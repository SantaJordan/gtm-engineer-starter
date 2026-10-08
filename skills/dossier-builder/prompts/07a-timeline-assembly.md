# Agent: timeline-assembly-agent (W4a)

## ROLE

Per account, assemble the sorted `timeline[]` from all source events that survived linking. Apply mode-specific rules. Pass-1 verbatim — no narrative, no inference.

## INPUTS

- `outputs/dossiers/<client_slug>/events/*.normalized.json`
- `outputs/dossiers/<client_slug>/linkage/link_evidence.json`
- `outputs/dossiers/<client_slug>/linkage/speaker_roster.json` (from W2a.5 — the per-account roster +
  `party_role_index[call_id][party_id] → role`). Load it before assembling any transcript-bearing event.
- `outputs/dossiers/<client_slug>/engagement.json` (mode + anchor_rules)

Read the inline **Speaker Attribution Protocol** (`references/speaker-attribution-protocol.md`) before
tagging any transcript line. This wave executes its Step 3 (per-line role tagging) and Step 4
(elicitation grading) on top of the roster W2a.5 already built (Steps 1–2).

## RULES

### 1. Filter by linking verdict

Drop events whose `link_evidence.verdict ∈ {AUTH_REJECT, EMAIL_REJECT}`. Keep `INDETERMINATE` only if the source is the canonical primary-id-system itself (CRM events about the account always belong to the account); for non-canonical sources, drop `INDETERMINATE`.

### 2. Dedupe SFDC ↔ Gong mirrors

Where `sfdc_task.fields.is_gong_mirror == true` AND a `gong_call` event exists with the same `call_id`: keep BOTH but mark the SFDC row `dedup_role = "metadata_sibling"` and the Gong row `dedup_role = "primary"`. Renderers display the Gong row and treat the SFDC row as a hover-extension (Outreach sequence attribution etc.).

### 2a. Tag every transcript line `is_customer_voice` + `elicitation` (Speaker Attribution Protocol Steps 3–4)

For every event that carries transcript text (`gong_call`, and any Claap/Zoom equivalent), split the
text into speaker turns on the verbatim `[party_id]:` prefixes preserved by W2a, then tag each turn by
joining on the W2a.5 roster:

1. **Role join (Protocol Step 3).** Look up `speaker_roster[account_local_id].party_role_index[call_id][party_id]`
   → `role ∈ {company, customer, unknown}`. Set `is_customer_voice = (role == "customer")`. A `company`
   or `unknown` turn is **NOT** customer voice (`is_customer_voice = false`). **Never** default an
   `unknown` speaker to customer. If a `party_id` is missing from the index, set `role = "unknown"`,
   `is_customer_voice = false`, and flag it — do not guess.

2. **Elicitation grade (Protocol Step 4).** For each turn where `is_customer_voice == true`, classify how
   the statement arose, reading the immediately preceding turns as context:
   - `unprompted` — the customer raised it on their own (volunteered a problem / number / consequence)
     without the company teeing it up in the preceding turn(s). **Highest trust.**
   - `led` — the customer said it only after the company framed or proposed it ("wouldn't it help if…?"
     → "yeah"). Agreement-after-framing is not independent demand. **Low trust — flagged.**
   - `seller_echo` — the customer line merely repeats the company's stock pitch, or restates a theme the
     company raises on essentially every call. **Not customer signal — context only.**
   When torn between `unprompted` and `led`, **choose `led`** (conservative: degrade to "kept it,
   flagged it," never delete a real signal). Company and unknown turns get `elicitation = null` (the
   tier only applies to customer voice).

Preserve the **verbatim** turn text and its original `[party_id]:` prefix. Add `role`,
`is_customer_voice`, and `elicitation` **alongside** — never rewrite or drop a line. Seller (company)
speech is retained in full as context; it is simply never counted as customer signal.

Persist the tagged turns on the event **under the envelope's `fields{}` object** (the envelope is
`additionalProperties: false` at its top level but `fields{}` is `additionalProperties: true`, so
`transcript_tagged` — and its per-turn `role` / `is_customer_voice` / `elicitation` — must live inside
`fields`, not at the envelope top level, or the event fails schema validation):

```json
"fields": {
  "transcript_tagged": [
    {"party_id": "1000001", "speaker_label": "[1000001]", "role": "company",  "is_customer_voice": false, "elicitation": null,        "text": "<verbatim>"},
    {"party_id": "1000002", "speaker_label": "[1000002]", "role": "customer", "is_customer_voice": true,  "elicitation": "unprompted", "text": "<verbatim>"}
  ]
}
```

### 3. Sort by `date`

Stable sort. Ties: `gong_call > opportunity_created/stage_transition > sfdc_task/event > email > billing_event > product_event` (most-load-bearing first within a tie).

### 4. Customer-mode output

The full sorted array becomes `dossier.timeline[]`. No anchor filtering. No pre/post split.

### 5. Prospect-mode output

Compute `t_anchor`:
```
if any gong_call exists for this account: t_anchor = MIN(gong_call.date)
else: t_anchor = MIN(opportunity_created.date)
else: t_anchor = MIN(any event date)  # fallback
```

Emit:
- `timeline[]` — the full sorted array (don't drop anything)
- `pre_first_call_evidence[]` — subset where `event.date < t_anchor`
- `post_first_call_evidence[]` — subset where `event.date >= t_anchor`

Every timeline event must appear in exactly one of pre/post (no overlap, no gap).

Write `t_anchor` into `dossier.structured_signals.t_anchor` with `rule` capturing which branch of the cascade applied.

### 6. Anti-leakage audit

Emit `leakage_audit.json`:
```json
{
  "n_events": ...,
  "n_pre_anchor": ...,
  "n_post_anchor": ...,
  "anchor_date": "...",
  "anchor_rule": "first_call_date|opp_created_date|fallback"
}
```

If any event lacks a date or `t_anchor` is null, fail loudly — anti-leakage cannot be confirmed, dossier cannot ship.

### 7. Customer-voice-only view (in addition to the verbatim full transcript)

For each account, emit a **`customer_voice[]`** view derived from the tagged turns above — the
analysis-ready surface the dossier and any Pass-2 reasoning should read by default. It carries ONLY
turns where `is_customer_voice == true`, each with its `elicitation` tier and a back-reference to the
full turn:

```json
"customer_voice": [
  {"call_id": "1000000000000000001", "date": "2025-06-01T12:00:00Z", "speaker_label": "[1000002]",
   "elicitation": "unprompted", "text": "<verbatim customer turn>"}
]
```

Rules for this view, straight from the Speaker Attribution Protocol:
- **Customer-only by default.** Company and unknown turns are excluded from `customer_voice[]` (they
  remain in the full verbatim transcript / `transcript_tagged` — nothing is deleted).
- **Unprompted weighted highest, led discounted, seller_echo excluded from demand.** Keep all three
  tiers in the view (so the reader can audit), but the dossier's headline / "what the customer wants"
  surfaces must draw on `unprompted` first; `led` is shown flagged and never as discovered pain; a
  `seller_echo` line never becomes a finding.
- **Keep the verbatim full transcript too.** The full `transcript_excerpt` / `transcript_tagged` stays
  on the event (inside `fields{}`, per the persistence note above) as the audit trail.
  `customer_voice[]` is an additive view, not a replacement.

Also emit per-account `speaker_attribution_summary`:
`{n_turns, n_company, n_customer, n_unknown, n_unprompted, n_led, n_seller_echo}`. If `n_customer == 0`
on an account that has calls, fail loudly to the panel — a call with zero customer voice usually means a
broken roster join, not a silent customer.

## OUTPUT

Embed `timeline[]`, `customer_voice[]`, and `speaker_attribution_summary` (and, prospect-mode, the
pre/post arrays) into the per-account dossier JSON. Write per-account file at
`outputs/dossiers/<client_slug>/dossiers/<account_id>.json`.

## SUCCESS CRITERIA

- All output schema-valid (against `dossier.{customer,prospect}.schema.json`).
- Timeline sorted ascending by date.
- Prospect mode: every event in exactly one of pre/post arrays.
- Forbidden-word grep returns empty.
- Every transcript turn tagged with `role` + `is_customer_voice` + (`elicitation` for customer turns);
  zero `unknown` speakers tagged `is_customer_voice = true`.
- `customer_voice[]` present and contains only `is_customer_voice == true` turns; full verbatim
  transcript retained alongside (nothing deleted).
- `speaker_attribution_summary` present; any calls-bearing account with `n_customer == 0` is surfaced as
  a loud failure, not silently shipped.
