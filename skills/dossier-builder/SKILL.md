---
name: dossier-builder
description: Build per-account customer or prospect dossiers from every system a company has — Gong/calls, CRM, billing, product, support, email — joined into a single sorted timeline with provenance and linking_trust. Use this whenever the user mentions building a dossier, rebuilding an account, running a timeline pipeline, "we're on a Zoom with RevOps and need to walk through their data," consolidating customer interactions, or capturing prospect signals across systems. Designed to run live on a call with the customer's RevOps person — interactive, fans out one Opus agent per data source / identity-linking / MRR-ARR / key field / DQ-triage / final critique trio, pauses between waves to surface outliers, asks tiebreaker questions, and educates the human on methodology at every step. Always trigger this when the user says "let's build dossiers," "give me an account view across all our systems," "we need to understand this customer end-to-end," or anything that implies unifying customer/prospect interactions across more than one source of truth.
category: blueprint
version: 1.1
tier: team
lifecycle: validated
author: "Jordan Crawford / Blueprint GTM"
contributors: []
status: active
valid_from: 2026-05-24
invalid_at: null
last-verified: 2026-07-17
requires-keys: [EXA_API_KEY, OPENWEBNINJA_API_KEY]
---

# Dossier Builder

A client-agnostic, interactively-run skill that builds canonical per-account dossiers — prospect or customer — by orchestrating a fan-out of specialist agents over the customer's data systems. Every wave pauses to walk the human through what was done, what was surprising, and what tiebreakers are needed before continuing.

## When this skill fires

- "Help me build a customer/prospect dossier."
- "We're on a call with RevOps — let's pull this account together across systems."
- "Rebuild dossiers for this cohort."
- "I need to see what every system says about this account on one timeline."
- "Run the timeline pipeline."
- "Consolidate Gong + CRM + billing + product into one view per account."

If the user is asking about a single account across multiple systems, or a cohort spanning Gong/CRM/billing/product/support/email, this skill is the right tool.

## Recommended pre-flight

Before invoking this skill, run `dossier-data-auditor` over the same `data_dir`:

    /skill dossier-data-auditor <path/to/data_dir>

The auditor catches the failure classes that kill dossier builds — the ~40%-wrong-FK,
the self-domain trap, the six-revenue-columns disagreement, the bulk-import tenure
cluster, the ICP-fit drift. It writes `outputs/audits/<slug>/recommended_engagement_overrides.json`,
which this skill's W0 ingests automatically (see Step 0 below) — pre-populating
`do_not_use_fields[]`, `tenant_domain`, `primary_id_system`, and any joins the auditor
already rejected.

Skipping the pre-flight is allowed but explicitly worse: every hour of live RevOps call
spent on a poisoned join is an hour you don't get back. The auditor takes minutes.

## Core ethos

Three rules govern every agent in this skill. They are non-negotiable because they encode the most common ways customer data corrupts a dossier. When agents conflict with these, the rules win.

1. **Trust the customer's actions first, their words second, what's in the CRM third.** Product events and Gong transcripts beat sales-stage notes. CRM is supplementary context, not ground truth.
2. **Every key field that matters gets its own agent.** Identity-linking, MRR/ARR, churn reason, plan tier — each gets its own context window. Never let one agent silently arbitrate two systems' versions of MRR. Surface conflicts; let the human pick.
3. **Conservative flagging. Educate the reader.** After every milestone print the four-block panel: *how I did it / outliers I'm flagging / questions I need you to answer / what I'll do next*. Better to ask than to silently drop. The dossier itself carries `notes_from_revops[]` as audit trail.

Read `references/methodology.md` once on first invocation. It expands these rules with concrete examples and the trust-hierarchy prompt fragments every fan-out agent should prepend.

A fourth rule governs every transcript this skill touches: **never confuse seller voice for customer voice.** Before any conclusion is drawn from a call, the **Speaker Attribution Protocol** (inline copy at `references/speaker-attribution-protocol.md`) builds a per-account roster, labels each speaker `company | customer | unknown` (unknown is **never** silently promoted to customer), and grades each customer statement `unprompted | led | seller_echo`. W2a.5 does the roster + roles; W4a tags every line and emits a customer-voice-only view. Seller speech is kept as context but never counted as customer signal.

## Two modes

Pick one at W0. The mode determines the schema, the data sources to ask about, and the anti-leakage rules.

| Mode | Universe | Outcome enum | Distinctive signals |
|---|---|---|---|
| `customer` | post-sale accounts | `active \| paying_silent \| cancelled \| prospect \| unknown` | mrr_snapshots, tenure_days, cases, subscriptions, churn_signals, csm |
| `prospect` | pre-customer accounts (`Type='Prospect' AND has any opp`) | `open \| lost \| won_recently_flipped` | lead_attribution, deal_shape, funnel_position, icp_fit, pre_first_call_evidence, post_first_call_evidence |

Schemas are in `schemas/dossier.customer.schema.json` and `schemas/dossier.prospect.schema.json`. Read the relevant one before W4.

## Wave-by-wave execution

This skill is stateful. Each wave reads files from the previous wave and writes its own outputs to `outputs/dossiers/<client_slug>/`. Every gated wave ends with the four-block panel — see `references/interactive-pause-script.md` for the template.

### W0 — Mode + engagement context (interactive, gated)

**Step 0 — pre-flight audit detection**. Look for `outputs/audits/<inferred_slug>/recommended_engagement_overrides.json` (produced by sibling skill `dossier-data-auditor`).
- If present: load it. Pre-fill `engagement.json` from these keys (load every one that's present; missing keys fall back to interactive capture):
  - **Core (always consume)**: `do_not_use_fields`, `tenant_domain`, `primary_id_system`, `rejected_joins`, `canonical_revenue_picks`, `per_account_linking_trust`.
  - **Structured ID system** (when present, supersedes flat `primary_id_system`): `per_object_canonical_id`, `dual_canonical_zones`. The flat `primary_id_system` is retained for back-compat and matches `per_object_canonical_id.accounts` when both are set.
  - **Hierarchy + naming** (read at W2b identity-linking + W4 timeline assembly): `account_hierarchy` (with `parent_map` + `rollup_directive`), `name_clusters`, `account_name_resolutions`.
  - **Domain primitives** (read by every source-pull agent that does email-domain joins, then by W2b): `canonical_domain_per_account`, `domain_state_blocklist`, `multi_domain_clusters`, `multi_tenant_join_blocklist`.
  - **Column quality** (read at W3a MRR canonical-pick and W4 timeline): `column_fitness_verdicts` (treat `dead`/`unfit` columns as do-not-cohort-by), `column_coercion_rules` (apply on read), `null_sentinels_per_column` (treat as null), `treat_as_suspect_fields`.
  - **Contact + expectations** (read at W2a contact pulls and W5 critique): `contact_fill_audit` (when `accounts_with_zero_contacts` is non-empty and `recommend_contact_finder_run` is true, surface to user at W2a; when LinkedIn-URL fill is < 30%, flag for re-enrichment), `user_stated_expectations` (cross-check at W3c cohort sanity).

  Surface in the W0 panel as: "Pre-flight audit from <date> loaded. Imported: N core overrides + M additive keys. Continue or re-audit?"
- If absent: print a one-line nudge: "No pre-flight audit found. Consider running `/skill dossier-data-auditor <data_dir>` first — it catches the join/domain/revenue failure classes before they reach this pipeline." The nudge is informational, not blocking; skipping audit is the user's call.

Then proceed with the original W0 capture:

Capture: `mode`, `client_slug`, `tenant_domain` (the customer's own email domain — load-bearing for the self-domain-trap strip in W2b), `anchor_rules` (for prospect mode: `t_anchor = first_call.date OR opp.created_date`), privacy flags.

Write `outputs/dossiers/<client_slug>/engagement.json`. Validate with `scripts/validate_schema.py --no-client-leakage` before proceeding — this greps the skill's own files for the new client_slug/tenant_domain to make sure no client-specific values leaked into the skill. Hits block.

### W1a — System discovery (interactive, gated)

Spawn `system-discovery-agent` (prompt: `prompts/01-system-discovery.md`). It walks RevOps through every category in `config/source-registry.yaml` — calls / CRM / marketing-automation / billing / product / support / email / ticketing / CSM / data-warehouse / Slack — and captures vendor, access-method, record-count, primary-key, and tenant-domain-issue per category.

Output: `systems_inventory.json` validated against `schemas/systems_inventory.schema.json`. Must flag exactly one `primary-id-system` (the system whose ID is treated as the canonical join key — usually SFDC).

### W1b — Raw-extract DQ pre-flight (gated)

Spawn `dq-triage-agent` (prompt: `prompts/06-dq-triage.md`) with stage=`raw`. Runs the catalog from `config/dq-checks.yaml` over raw exports, before any join. Catches the obvious blockers — fake/test account names, future close dates, suspicious distributions, fill-rate issues — before we waste cycles pulling data that's known-bad.

Output: `dq/dq_findings_raw.json`. Surface in the four-block panel; gate on `must_fix` items with severity=`block`.

### W2a — Per-source pulls (fan-out, no pause)

For every source in `systems_inventory.json`, spawn one `source-pull-agent` in parallel using prompts from `prompts/02b-source-pull.<source>.md` if available; otherwise fall back to the generic template `prompts/02a-source-pull.template.md`.

Each agent emits an array of `source_event_envelope` rows: `{account_local_id, date, source, type, subject, raw_ref, fields, provenance}`. **Pass-1 verbatim only**. No reasoning, no inference. Every event has a `raw_ref` that round-trips to the source.

Output: `events/<source>.normalized.json` per source.

The Gong source-pull overlay (`prompts/02b-source-pull.gong.md`) additionally emits a per-call
**party roster** in `fields.parties[]` — one row per Gong party `{party_id, name, email_domain,
gong_affiliation, speaker_label}` — and preserves the verbatim `[party_id]:` prefixes inside every
`transcript_excerpt`. This is the free raw material the next wave (W2a.5) turns into speaker roles. No
inference here; it's verbatim party metadata carried through.

### W2a.5 — Speaker Roster Enrichment (fan-out, no pause)

Spawn `speaker-roster-agent` (prompt: `prompts/02c-speaker-roster.md`). This wave runs **after the W2a
source pulls and before / alongside W2b identity-linking**. It is the dossier-builder implementation of
**Steps 1–2 of the Speaker Attribution Protocol** (inline copy at
`references/speaker-attribution-protocol.md`. Read it before this wave.

The seller is on every call, so a transcript read as one undifferentiated block credits the rep's pitch
to the customer, treats led answers as discovered demand, and treats stock seller lines as signal. The
unit of trust is the **unprompted customer statement**; everything else is context. Before we can isolate
customer voice we must know, per speaker, who is **company** (the seller running this dossier build) vs.
**customer** (the buyer) vs. **unknown** — and we must never silently promote an unknown to customer.

**It does this for free, reusing what W2a already pulled** — no enrichment spend:

- **Roster (Protocol Step 1).** Dedupe every Gong party across **all** of an account's calls into one
  roster (same human under one entry even when `party_id` drifts call-to-call), reusing `fields.parties[]`,
  `n_internal_parties` / `n_external_parties`, and `internal_email_domains` / `external_email_domains`.
- **Role labels (Protocol Step 2, cheapest-first).** Label each party `company | customer | unknown`:
  Gong `gong_affiliation` (`Internal` → company, `External` → customer) first; then email-domain compare
  against the seller's own internal domains vs. `engagement.tenant_domain`; otherwise **unknown** (never
  defaulted to customer). Each label carries `source ∈ {metadata|domain|heuristic}` + `confidence`.
- **Join key preserved.** Output keys the result back to the `[party_id]` prefixes already in the
  transcript excerpts, so W4a can tag each line. Paid name→employer enrichment (Protocol Step 2.3) is
  **out of scope here**; only run it as a separate cost-guarded pass if the human asks to resolve
  remaining `unknown`s.

Output: `linkage/speaker_roster.json` — per account, the deduped `roster[]` plus a
`party_role_index[call_id][party_id] → role` join table (exhaustive: every observed `party_id` has an
entry, defaulting to `unknown`). Surface per-account `counts` in the four-block panel, flagging any
account with `unknown > 0` or a self-domain-trap signature so the human can decide whether to authorize a
paid resolve pass. This wave only **labels** — it drops no turns and reweights nothing; seller speech is
retained as context for W4a.

### W2b — Identity linking (interactive, gated, load-bearing)

Spawn `identity-linking-agent` (prompt: `prompts/03-identity-linking.md`). This is the single most important wave — get this wrong and every downstream artifact is wrong.

It runs the six-verdict audit (`AUTH_KEEP/AUTH_REJECT/AUTH_EMAIL_CONFLICT/EMAIL_KEEP/EMAIL_REJECT/INDETERMINATE`) per record, applies the three-test guard for any single-key join (≥2 of {ID match, domain match, name fuzzy ≥0.92}), and **hard-fails any join whose only basis is `email_domain == engagement.tenant_domain`** (the self-domain trap: your own staff matched to every account they touched).

Output: `linkage/link_evidence.json` with per-record verdict + aggregate `linking_trust ∈ {high|medium|low|none|indeterminate}`. Surface a 100-record random spot-check to the human in the four-block panel; tiebreakers persist into `notes_from_revops[]`.

### W3a — MRR/ARR canonical pick (gated if conflicts)

Spawn `mrr-arr-agent` (prompt: `prompts/04-mrr-arr.md`). It picks the canonical revenue source per account from every system that exposes revenue (billing, CRM, CSM, product), honors the `do-not-use` list (e.g. a CRM `Opportunity.Amount` that governance docs mark Do-Not-Use), runs the Apple-at-$20 outlier check + p99/median ratio + round-number-cluster check, and **never silently picks** when sources disagree.

If two sources disagree by >10% on >5% of records, blocks and pauses. Output: `signals/revenue.canonical.json`.

### W3b — Per-key-field probes (fan-out, gated if conflicts)

For every key field surfaced by W1a + W2a beyond MRR (CSM, plan_tier, segment, churn_reason, health_score, etc.), spawn one `key-field-agent` (prompt: `prompts/05-key-field.template.md`). Each gets its own context window. Same pattern as MRR: canonical pick + conflict log + 3-record spot-check.

Output: `signals/<field>.json` per field.

### W3c — DQ triage post-link + 200-record web sample (gated)

Re-spawn `dq-triage-agent` with stage=`linked`. Now that joins are done, run the cohort-level checks: paradox detection (`mrr>0 ∧ no_calls_180d`), bulk-import-tenure clustering, cross-source MRR mismatch, ICP cohort-sanity (200-record web sample via `scripts/web_sanity_sample.py`).

The 200-record sample answers: "what fraction of 'customers' are actually in the ICP?" — the case where a fifth of a cohort is not in the ICP at all. Web sample uses OpenWebNinja + Exa to verify domain, name, vertical match.

Output: `dq/dq_findings_linked.json`.

### W4a — Timeline assembly (no pause)

Spawn `timeline-assembly-agent` (prompt: `prompts/07a-timeline-assembly.md`). Drops events with `link_evidence.verdict ∈ {AUTH_REJECT, EMAIL_REJECT}`. For prospect mode, applies anti-leakage: every event filtered against `t_anchor`; emits `pre_first_call_evidence` and `post_first_call_evidence` blocks.

This wave also executes **Steps 3–4 of the Speaker Attribution Protocol** on every transcript-bearing
event, joining on the W2a.5 roster (`linkage/speaker_roster.json`):

- **Per-line role tag (Step 3).** Each `[party_id]:` turn gets `role ∈ {company, customer, unknown}` and
  `is_customer_voice` (bool, `true` only when `role == customer` — `unknown` is **never** customer voice).
  Verbatim text + original prefix preserved; tags added alongside, never rewriting a line.
- **Elicitation grade (Step 4).** Each customer turn is graded `unprompted | led | seller_echo` from the
  preceding turns (when torn between `unprompted` and `led`, choose `led`). Company/unknown turns get
  `elicitation = null`.
- **Customer-voice-only view.** Emits an additive `customer_voice[]` (customer turns only, each with its
  `elicitation` tier) as the analysis-ready default surface — **customer-only by default, unprompted
  weighted highest, led discounted, seller_echo excluded from demand** — while keeping the full verbatim
  transcript on the event as the audit trail. Emits `speaker_attribution_summary` per account; a
  calls-bearing account with zero customer voice fails loudly (broken roster join, never a silent
  customer).

### W4b — Structured signals (no pause)

Spawn `signals-assembly-agent`. Composes mode-specific structured_signals from W3 outputs.

### W4c — Headline + flags + completeness (no pause)

Spawn `headline-agent` (`prompts/07b-headline-builder.md`) and `flags-agent` (`prompts/07c-flags-and-completeness.md`). Headline is deterministic — typed fields only, missing fields say so verbatim ("MRR unknown") rather than omitting. Flags include `mrr_pos_no_calls_180d`, `customer_but_cancelled`, `cross_source_mrr_mismatch`, `bulk_import_tenure_cluster`, `name_test_fake_demo_regex`, `paradox`.

### W5 — Critique quartet (parallel, gated)

Spawn four Opus agents in parallel — `critique-statistician`, `critique-revops`, `critique-outlier-hunter`, `critique-reader`. Prompts in `prompts/08{a,b,c,d}-critique-*.md`. They produce four independent perspective reports plus a unified `must_fix_before_ship.json`. Render is blocked until human resolves each `must_fix` item via the four-block panel.

The reader critic is the blind-retell traversability gate (added v1.1): it receives NO build context, retells each sampled account's story BEFORE critiquing, and fails the corpus when stories are unrecoverable or noise-drowned. It exists because the other three verify the data is *right* while nobody verifies it is *legible* — a corpus can pass three critics that reproduce every aggregate exactly on a corpus whose timelines were unreadable walls of near-identical cancellation rows; the human caught it in a five-minute skim. After fixes, re-run the reader on the same sample ids and report before/after — improvement is measured, never asserted.

This is the equivalent of Claude Code's plan-mode multi-perspective auto-review. See `references/critique-trio-rubric.md` for what each perspective is allowed to flag.

### W6 — Render + persistence + handoff (terminal)

Run `scripts/render_html.py` per dossier to produce v6-style HTML with provenance chips, four-state uncertainty grammar (`verified | inferred | fuzzy | missing`), and AI-briefing fallback to verbatim "unavailable" when confidence < 0.4.

Write `manifest.json` listing every artifact + SHA + agent prompt version. Write `methodology-notes.md` summarizing methodology used + top 5 surprises + top 3 RevOps tiebreakers — anonymized so the file is publish-shape. Append final tiebreakers to `tiebreakers.log.jsonl`.

## Agent fan-out cheat sheet

Each agent gets its own context window. Top model (Opus) for everything in this skill — these are high-stakes reasoning tasks where fabrication or bad joins poison the entire dossier.

| Agent | Wave | Prompt | Output |
|---|---|---|---|
| system-discovery-agent | W1a | 01-system-discovery.md | systems_inventory.json |
| dq-triage-agent | W1b/W3c | 06-dq-triage.md | dq_findings_{raw,linked}.json |
| source-pull-agent (× N sources) | W2a | 02a + 02b overlays | events/<source>.normalized.json |
| speaker-roster-agent | W2a.5 | 02c-speaker-roster.md | linkage/speaker_roster.json |
| identity-linking-agent | W2b | 03-identity-linking.md | linkage/link_evidence.json |
| mrr-arr-agent | W3a | 04-mrr-arr.md | signals/revenue.canonical.json |
| key-field-agent (× M fields) | W3b | 05-key-field.template.md | signals/<field>.json |
| timeline-assembly-agent | W4a | 07a-timeline-assembly.md | timeline embedded in dossier |
| signals-assembly-agent | W4b | (inline orchestrator) | structured_signals embedded |
| headline-agent | W4c | 07b-headline-builder.md | headline string |
| flags-agent | W4c | 07c-flags-and-completeness.md | flags[] |
| critique-statistician | W5 | 08a-critique-statistician.md | critique/statistician.json |
| critique-revops | W5 | 08b-critique-revops.md | critique/revops.json |
| critique-outlier-hunter | W5 | 08c-critique-outlier-hunter.md | critique/outlier-hunter.json |
| critique-reader | W5 | 08d-critique-reader.md | critique/reader.json |

## Privacy and client-agnostic guardrails

Read these once at W0 and check on every wave that emits an artifact:

- The skill itself contains zero client names, vertical-specific field names, or domains. Everything client-specific lives in `outputs/dossiers/<client_slug>/` or in `engagement.json`.
- `engagement.tenant_domain` is the only place the customer's own domain lives. Source-pull agents and the identity-linking agent must read it and strip it from email-domain joins before matching.
- Forbidden words for pass-1 (per `config/forbidden-words.yaml`): `predict, suggest, recommend, score, potential, fit, ICP, likely, indicate`. These are reasoning words; pass-1 describes verbatim. Pass-2 (a separate downstream skill) is allowed reasoning.
- AI-briefing narrative confidence < 0.4 → render "unavailable" rather than fabricating.
- `validate_schema.py --no-client-leakage` runs at W0 and W6.

## Validation

Earlier versions were checked by byte-identical replay against frozen exports, which are
private and not in this repo. The failure classes they exercise are
described in `references/lessons-from-prior-builds.md`: a ~40%-wrong derived foreign key,
an auth-email conflict, multi-source MRR blocking, the self-domain trap (hundreds of false matches)
and a product-ID collision.

To try the skill end to end, use the fictional corpus in `examples/northwind-demo/` at the
repo root. See `examples/README.md`.

## Deliberate non-goals

Do not let the skill drift into these — they are separate skills.

- **NOT Pass-2 analysis** — scoring, recommendations, churn-cause attribution, expansion-fit. Pass-1 dossiers are the durable artifact; reasoning is downstream.
- **NOT a daily-refresh cron** — v1 is one-shot per engagement, run live with RevOps.
- **NOT a CRM writeback** — output is local JSON + HTML.
- **NOT a BI dashboard** — HTML is per-account, not cohort.

If the user asks for any of those, finish the dossier first, then suggest the right next skill.

## Integration with other skills

- **`dossier-data-auditor`** (recommended pre-flight + optional post-build review). Same skill family; audits the data quality going INTO this pipeline (`pre-build` mode) or coming OUT (`post-build` mode). The auditor writes `outputs/audits/<slug>/recommended_engagement_overrides.json`, which W0 above auto-detects and imports. See § Recommended pre-flight at top.

## References (read on demand)

- `references/speaker-attribution-protocol.md` — inline copy of the canonical Speaker Attribution Protocol (roster, `company|customer|unknown` roles, `unprompted|led|seller_echo` elicitation tiers, blind-first analysis). Read at W2a.5 and W4a, before tagging any transcript.
- `references/methodology.md` — trust hierarchy, said-vs-did framing, anti-leakage anchoring (read at W0).
- `references/lessons-from-prior-builds.md` — catalog of common failure patterns (read at W1b).
- `references/canonical-dossier-schema.md` — the 26-key shape, both modes (read at W4).
- `references/linking-trust-rubric.md` — six-verdict audit + three-test guard rules (read at W2b).
- `references/verbatim-discipline.md` — forbidden-word list, pass-1/pass-2 split (read on every source-pull agent spawn).
- `references/trust-hierarchy-prompt-fragments.md` — reusable "TRUST THESE / use cautiously" stanzas to prepend on every agent prompt.
- `references/critique-trio-rubric.md` — what each of the four perspectives is allowed to flag (read at W5; filename kept for link stability after the reader critic joined in v1.1).
- `references/interactive-pause-script.md` — the four-block panel template (read every time you pause for a human).
