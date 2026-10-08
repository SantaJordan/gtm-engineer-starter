---
name: win-loss-rewind
description: Won/Lost/10X — builds a customer-fit discriminator by working backward from outcome (which customers won, which were lost, and which are 10X, meaning lifetime revenue at least 10 times the median customer's). Takes Gong calls + a list of customers labeled won/lost/healthy/churned, distrusts the user's own labels until verified, auto-segments customers without assuming industry, generates "what would have preceded this?" hypotheses per archetype, and validates them with holdout lift. Outputs a per-archetype scoring rubric of non-obvious public-data signals — the recipe for identifying good-fit prospects without lazy headcount-and-industry ICP. Three modes: `init <csv>` (data archaeology), `run` (full pipeline), `score <domain>` (apply discriminator to a new prospect).
category: blueprint
version: 1.1
tier: team
lifecycle: validated
author: "Jordan Crawford / Blueprint GTM"
contributors: []
status: active
valid_from: 2026-05-24
invalid_at: null
last-verified: 2026-07-18
requires-keys: [ANTHROPIC_API_KEY, APIFY_TOKEN, EXA_API_KEY, FIRECRAWL_API_KEY, GONG_ACCESS_KEY, GONG_SECRET_KEY, OPENWEBNINJA_API_KEY, SF_SECURITY_TOKEN]
engagement-keys: [GONG_ACCESS_KEY, GONG_SECRET_KEY, SF_SECURITY_TOKEN]
---

# Win-Loss Rewind

**Mission:** Replace lazy "50–1000 employees in vertical X" ICP definitions with a discriminator built backward from real outcomes — won, lost, churned, thriving — using non-obvious public-data signal combinations that hold up on a holdout cohort.

**Time:** 30–90 min for a full run on 50–500 customers (Batch API does the heavy lifting offline; mostly waiting).

**Output:** `outputs/<run-id>/discriminator.md` — per archetype, a scored signal recipe with concrete API queries, lift, holdout replication, and anti-match catalog. A CRO can hand this to a BDR team the day it ships.

---

## Why this exists

Most win/loss tooling does one of two things:

1. **Score every deal, find correlations.** This finds noise dressed as insight. Generic SaaS findings ("they stopped using the product before they churned") are tautologies — not actionable signals.
2. **Build a forward hypothesis from a hypothesis.** A message-writing workflow does this *given* a defined ICP. If the ICP is wrong, every PVP downstream is poisoned.

Win-Loss Rewind is the third thing. It takes outcomes as ground truth, distrusts the user's labels, finds the segments empirically, and ships a discriminator that's *queryable on a cold prospect tomorrow.* The deliverable is not an article, not a PVP, not a TAM map — it's a scoring spec.

**Four non-negotiables that shape every phase:**

1. **The operator does not fully understand their own data.** Before any signal is built on a label, verify the label against external reality. If a CSV says "great customer paying $50K" and Firecrawl says one-page WordPress site / LinkedIn says one employee, that row is flagged before downstream signals are computed.
2. **Hypotheses must die at holdout or they don't ship.** A large share of unaudited LLM entity matches can be false positives; planted spurious correlations routinely look strong on train and collapse on holdout. Every surviving signal in `discriminator.md` must survive a 20% holdout test.

3. **Never confuse today's web for last year's web.** The skill's core question is "what could we have known, and WHEN" — so every pre-close claim must prove its date or admit it can't. Phase 6 runs a two-pass time-travel verification (`references/time-travel.md`): claims are tagged **[ARCHIVED-BEFORE]** (archive.org capture proves it), **[RECORD-DATED-BEFORE]** (the record's own date + publication-lag haircut proves it), or **[RETRO-ASSUMED]** (today's web assumed backward — never counts as firm). Firm lift is computed from firm-tagged evidence only; only firm signals ship. "No archive coverage" is reported as *unverifiable*, never as absence.

4. **Never confuse the seller's voice for the customer's.** The seller talks on every call. If pain language is mined from an undifferentiated transcript, the rep's pitch gets credited to the buyer, led answers get treated as discovered demand, and stock lines the rep repeats everywhere get mistaken for signal. This skill runs the **Speaker Attribution Protocol** (inline copy at `references/speaker-attribution-protocol.md`) as **Phase 1.4** before any theme extraction: every transcript turn is tagged `company | customer | unknown`, and every customer statement is graded `unprompted | led | seller_echo`. The unit of trust downstream is the **unprompted customer statement**; seller speech is kept as context but never counted as demand, and an `unknown` speaker is never silently promoted to customer.

---

## Modes

The skill is invoked via three modes. Each mode is resumable — Phase artifacts are stable on disk, so you can re-run a single phase without re-doing the rest.

### Mode 1: `init <path-to-customer-csv>`

**Phase 0 only.** Data archaeology and user calibration. Outputs `data_confidence.md` and **gates** the rest of the pipeline behind user approval.

```
/win-loss-rewind init ~/Downloads/customers-2026-q1.csv
```

The skill examines the CSV column-by-column, asks the user to confirm semantics (what counts as "customer", what counts as "won", what's the health field, what's the close-date field), then runs cheap external probes (Firecrawl + LinkedIn employee count) on a sample to surface contradictions. User must approve before Phase 1 runs.

### Mode 2: `run [--resume-from N] [--archetype-cap 8]`

**Phases 1–7.** Full pipeline. Resumable from any phase boundary. Default cap of 8 archetypes prevents Phase 4 from blowing up Batch API cost on noise clusters. Run `--stop-after 3` first; phases 4-7 need `--approve-archetypes` (see "Human gate" below).

```
/win-loss-rewind run
/win-loss-rewind run --resume-from 4    # Skip ahead if Phase 1-3 already completed
```

### Mode 3: `score <prospect-domain>`

**Phase 8.** Reads the latest `discriminator.md`, pulls the same signal panel for the prospect, scores against each archetype.

```
/win-loss-rewind score acmelegal.example
```

Output: looks-like-archetype-X confidence Y%, expected fit Z, signal panel diff vs archetype canonical features.

---

## The phases

Every phase writes a stable artifact under `outputs/<run-id>/`. Next phase reads it. Phases gate on each other — a failed gate halts the run and surfaces the failure to the user. (Phases 0 → 1 → 1.4 → 2 → 3 → 4 → 5 → 6 → 7, plus `score` as Phase 8.)

### Phase 0 — Data Archaeology & Calibration

**Goal:** Understand the user's data before trusting any of it.

1. Detect data shape via adapter (`scripts/adapters/{csv,salesforce,gong_only}.py`).
2. Run `scripts/adapters/csv.py` (or live equivalent) to infer schema, sample 10 rows, generate column-meaning interrogation.
3. Use `prompts/data-archaeology.md` to drive an LLM-led interrogation: agent reads schema + samples, asks user to confirm the meaning of each column (especially: stage labels, customer/prospect boundary, ARR/MRR field, health/churn marker, close date).
4. Sanity-check sample of high-ARR rows: cheap Firecrawl + LinkedIn Apify probe. Flag ARR↔reality divergences >2 orders of magnitude.
5. Write `outputs/<run-id>/data_confidence.md` — what we trust, what we don't, what we'll verify in Phase 2.

Every later phase reads customers through one contract (`scripts/wlr_schema.py`):
`company` (from `company`, `company_name` or `account_name`), and a VERIFIED
`outcome` from `label` (won / lost / churned / 10X) first, then `health` (a
churned account is churned even if the CRM says won), then the CRM stage. The CRM
stage is kept beside it as `crm_outcome`. **10X** means lifetime billed revenue
at least 10 times the median paying customer's in the same data set.

**Gate:** user must approve `data_confidence.md` before Phase 1 runs.

### Phase 1 — Gong Match & Verify

**Goal:** Bind every customer in the CSV to their Gong call history with audited match confidence.

1. `scripts/pull_gong.py` — pull all Gong calls in the relevant date range. Basic auth, `/v2/calls` paginated, `/v2/calls/transcript` batched at 100.
2. `scripts/match_gong.py` — fuzzy match calls to companies. Title parse → suffix normalize → token Jaccard.
3. **Mandatory Opus audit pass.** `scripts/opus_audit_matches.py` runs every fuzzy match through Claude Opus + adaptive thinking via Batch API. Mismatches flagged for human review or fuzzy re-match. (Audit is non-negotiable: a large share of unaudited matches can be false positives.)
4. Output: `outputs/<run-id>/matched_calls.jsonl` — call_id, transcript, company, deal stage, deal outcome, audit_confidence.

**Gate:** if audit precision <90%, halt with diagnostic. Don't poison downstream phases with bad matches.

**No Gong?** Import markdown transcripts instead of running Phase 1:
`python3 scripts/adapters/transcripts_md.py <dir> --run-id <id> --seller-domain <yours>`
writes `matched_calls.jsonl` and `calls_with_speaker_attribution.jsonl`; then
`orchestrate.py --run-id <id> --resume-from 2`. Phase 4 skips customers with no
matched call (set `WLR_INCLUDE_NO_CALL_CUSTOMERS=1` to send them anyway).

### Phase 1.4 — Speaker Attribution

**Goal:** Before any pain language is mined, separate what the **customer** said from what the **seller** said, and grade how each customer statement arose. Implements the **Speaker Attribution Protocol** (inline copy at `references/speaker-attribution-protocol.md`).

This stage is **free** — Gong call objects already tag every party internal vs. external (`parties[].affiliation`), so no paid enrichment is needed for Gong-sourced calls. (The protocol's paid enrichment fallback only applies to raw transcripts with no party metadata, which is out of scope for this Gong pipeline.)

1. `scripts/label_speakers.py` reads `matched_calls.jsonl` (the verified call→customer pairs from Phase 1) and joins each back to `gong_calls_raw.jsonl` to recover the raw `parties[]` + `transcript` (the matched file itself carries neither).
2. **Roster + role (Steps 1–2).** For each customer, dedupe parties to people and label each `company | customer | unknown`, cheapest-first: Gong affiliation metadata → email-domain compare (vs. `SELLER_DOMAINS`/`--seller-domain`) → `unknown`. An unknown speaker is **never** defaulted to customer.
3. **Turn tagging (Step 3).** Every transcript turn is tagged `company | customer | unknown` by joining its `speakerId` to the roster. Verbatim text + original speaker label are preserved; seller turns are kept as context, never deleted.
4. **Elicitation (Step 4).** Every **customer** statement is graded:
   - `led` — the immediately preceding **seller** turn introduced the same topic or asked a leading/closed question (agreement-after-framing is not independent demand).
   - `seller_echo` — the line is spoken by an internal party, or it matches a high-frequency seller phrase seen across many calls in the run (a constant the seller injects discriminates nothing).
   - `unprompted` — neither of the above; the customer raised it on their own. **This is the gold signal.** (When in doubt between `unprompted` and `led`, the script chooses `led`.)
5. Output: `outputs/<run-id>/calls_with_speaker_attribution.jsonl` — one row per verified (call, customer) pair with `roster`, role-tagged `turns`, and elicitation-graded `customer_statements`.

**Downstream consumes these tags:** Phase 4 (`batch_themes.py`) passes the roster + role-tagged turns + elicitation tiers into the theme extractor, which mines pain **only from customer turns**, weights `unprompted` highest, discounts `led`, and excludes `seller_echo` from demand. Phase 5's hypothesis scoring weights unprompted customer pain higher and refuses to ground a hypothesis in a `seller_echo` or non-customer line (Gate 5).

```bash
python scripts/label_speakers.py --run-id <id>
# optional: provide the seller's own domain(s) as a fallback when affiliation is missing
python scripts/label_speakers.py --run-id <id> --seller-domain yourco.com
```

### Phase 2 — External Reality Enrichment

**Goal:** For every customer (won/lost/healthy/churned), pull a parallel signal panel from external sources. Reconcile with user labels.

For each customer, fan out via parallel sub-agents (TAM-research-style):

- **Firecrawl** — site map, page count, blog cadence, careers page, pricing page, content depth.
- **EXA deep-reasoning** — "what does this company actually do? what evidence of $X scale?"
- **OpenWebNinja** — Google Maps panel, AI-mode "how big is this company really?"
- **Apify** — LinkedIn employee count, role mix, leadership history.
- **Vertical-specific data warehouses** (optional, only if the operator has subscriptions) — public-records data warehouses can surface permits, licenses, NPI, regulatory filings. The skill ships with stubs; wire your own data sources in `scripts/enrich_panel.py` if you have them. None are required for the skill to run.
- **OpenWebNinja (Search-Light / AI Mode)** — recent press, partnerships, executive changes.
- **Wayback timeline** (default on, free, keyless) — change-history of the homepage + up to 2 key pages via archive.org CDX (index calls only, shared 3s throttle). Lands under `wayback_timeline` per customer: a coverage map for Phase 6's firm verification and a cache pre-warm. Drop `wayback` from `--sources` to skip; on a cold cache ~200 customers add up to ~30 min wall-clock (throttle-bound, not worker-bound).

Reconcile against Phase 0 labels. Surface contradictions explicitly in the panel.

`scripts/enrich_panel.py` orchestrates. Output: `outputs/<run-id>/customer_panels.jsonl` — one row per customer with all observable signals + reconciliation flags.

### Phase 3 — Auto-Segmentation (no industry assumed)

**Goal:** Cluster customers on the *signal panel*, not on user-supplied industry tags. Different verticals (legal vs. healthcare vs. construction) develop different precursor signals; they must not be blended.

1. `scripts/segment.py` — embed customers on numeric signal vector + clusters via HDBSCAN (or k-means with silhouette tuning). Cap at `--archetype-cap` (default 8).
2. `prompts/archetype-namer.md` — LLM names each cluster with an operationally-specific archetype + canonical features ("the small multi-entity legal-ops shop", "the regional health-system compliance team").
3. Output: `outputs/<run-id>/archetypes.md` — N segments, each with member list + canonical feature description + outcome breakdown (won/lost/healthy/churned counts).

**Gate:** archetype must have ≥5 members across at least 2 outcome classes to proceed. Singleton clusters surfaced as outliers, not as archetypes.

### Phase 4 — Theme Extraction (Anthropic Batch API)

**Goal:** For every customer, extract what they *literally said* + what was happening at their company at the moment of pain.

1. `scripts/batch_themes.py` — a 6-step CLI (`create / submit / status / download / parse`). Model from `scripts/wlr_config.py` (default `claude-sonnet-5-5`), max_tokens 2000.
2. Input per customer: **Phase 1.4 attributed transcripts** (`calls_with_speaker_attribution.jsonl` — roster + role-tagged turns + elicitation tiers) + Phase 2 signal panel + deal outcome. `build_user_message()` passes the roster and the role-tagged turns, not raw transcript; if Phase 1.4 has not run it falls back to raw text and warns the extractor to treat speakers as `unknown`/`led`.
3. Use `prompts/theme-extractor.md`: extract pain language **only from `customer` turns**, problem framing, time horizon (when did pain emerge vs when did they search), what was happening at the company at that moment. Each `pain_language[]` item carries `speaker_employment` (`company | customer | unknown`) and `elicitation` (`unprompted | led | seller_echo`); `seller_echo` is excluded from demand and `extraction_confidence` is lowered when the pain is mostly `led`/`seller_echo`.
4. Output: `outputs/<run-id>/themes.jsonl` — per-customer pain narrative + temporal markers, each pain phrase attributed + elicitation-graded.
   `download` keeps every text block of each answer (thinking blocks are skipped) and marks a request that errored or carried no text as `failed`. `parse` lists those in `themes_parse_errors.jsonl` and exits non-zero, so Phase 5 never runs on a silently thin theme set.

**Cost note:** `batch_themes.py create` prints an estimate before anything is submitted. At Batch API prices for `claude-sonnet-5-5` ($1 in / $5 out per million tokens) and 2,000 max output tokens, output costs at most $0.01 per customer (about $0.006 at the 60% use the script assumes); input depends on how many calls each customer has.

### Phase 4.5 — Vertical Data Scout (recon at the BEGINNING, not the end)

**Goal:** map what each archetype's vertical data actually looks like BEFORE any hypothesis is written. The generator can only cite sources it knows exist; without this step it knows five generic APIs — with it, it knows the vertical's license boards, permit portals, regulator feeds, and rosters. (The Phase 6.7 knowability panel closes this loop at the end; the scout opens it at the start. Example: themes reveal customers got licensed before buying — the license record itself may be unobtainable, but the scout already found the state board's monthly roster and dated its archive captures, so Phase 5 can hypothesize "appeared on the roster between capture A and capture B.")

1. Per archetype, spawn a scout subagent (Sonnet, per cost-controls) with `prompts/vertical-data-scout.md` + the archetype brief (canonical features, member companies, Phase 4 themes when available). Reason about the vertical's exhaust first, then search independently with the free procedure in `references/free-source-discovery.md` (the full Crawford atlas is optional acceleration, not required). Every listed source is verified with one real record and dated for historical depth; classification uses the same temporal axes as `references/source-temporal-classification.json`.
2. Output: `outputs/<run-id>/vertical_sources.jsonl` — one source per line with `source_key`, query recipe, verified example, historical depth, and temporal class.
3. **Panel backfill (required for scout sources to count):** for each verified scout source, query it for the archetype's member companies and merge results into `customer_panels.jsonl` under `signal_panel.<source_key>` (agent-driven; the scripts read any source key generically). Without backfill, a scout-sourced hypothesis has no presence data and dies at the provisional filter.
4. Phase 5 auto-merges the atlas into `available_external_sources` + the Gate 4b manifest; Phase 6 auto-merges the temporal classifications for firm-verification routing.

### Phase 5 — Hypothesis Generation: "What Would Have Preceded This?"

**The creative core: forward prediction, rather than retrospective churn root-cause analysis.**

1. Per archetype: spawn a hypothesis agent (Claude Opus, Sequential Thinking enabled) with `prompts/hypothesis-generator.md`. Generate 10–30 candidate "pre-pain signals" — observable, public, queryable behaviors that should have been visible 3–12 months before the customer entered solution-seeking. **Scoring weights `unprompted` customer pain highest, discounts `led`, and ignores `seller_echo`**; Gate 5 KILLs any hypothesis whose pain rationale rests on a `seller_echo` or non-customer (`company`/`unknown`) phrase.
2. Each hypothesis structured:
   ```json
   {
     "signal": "company files Form X with state Y",
     "observability_source": "state Y open data portal",
     "queryable_field": "filings.type WHERE filings.entity_id = ?",
     "hypothesized_lead_time_days": 180,
     "hypothesized_lift": 3.5,
     "anti_match_examples": ["companies that file but have no pain because..."],
     "rationale_from_phase_4_themes": "[link to specific transcript phrases]"
   }
   ```
3. **Hard-gated** against `prompts/hard-gates.md`:
   - Gate 1: must be ICP-operationally specific (no "any B2B").
   - Gate 2: must directly prove pain — banned signals: generic funding, hiring, M&A, headcount.
   - Gate 4: must name a real API field, scraping selector, or data source.
   - Gate 5: pain must connect to a problem the customer literally articulated in Phase 4 themes.
4. Output: `outputs/<run-id>/hypotheses.jsonl` — survivors only, with traceability back to Phase 4 transcript phrases.

### Phase 6 — Backcasting Validation (two-pass time travel)

**Goal:** Test every hypothesis against *provable* history. Compute lift twice — a cheap filter, then a firm pass on dated evidence. Hold out 20% to catch overfitting.

1. `scripts/backcast_validate.py`:
   - **Pass 1 (provisional filter):** signal presence from the current Phase 2 panel; lift + CI + holdout per `references/lift-and-holdout.md`. Decides which hypotheses earn verification (top-K per archetype, default 8) — never a ship decision.
   - **Pass 2 (firm):** per (hypothesis, customer) cell, anchored at the customer's reference date (`scripts/reference_dates.py`): dated records → **[RECORD-DATED-BEFORE]** (with publication-lag haircut); wayback-friendly pages → `scripts/wayback_timetravel.py bracket` → **[ARCHIVED-BEFORE]** with a bracket-dated knowable-by window; everything else caps at **[RETRO-ASSUMED]**. Firm lift from firm cells only; train AND holdout re-earn their lift on firm evidence. Thin firm samples → `unverifiable` (shown, labeled, excluded — never promoted, never hidden).
   - `outcome_leakage_check()` is real now: current-state-only sources (per `references/source-temporal-classification.json`) with zero firm evidence are flagged.
   - Flags: `--skip-firm-pass`, `--firm-check-top-k`, `--firm-min-coverage`, `--firm-min-cell`, `--force-firm-check <id>`.
2. Anti-pattern scrubbing per `references/anti-patterns.md`: generic-SaaS findings, data artifacts, RETRO-ASSUMED-only findings, vague-window findings, holdout collapses.
3. Output: `outputs/<run-id>/validated_signals.{md,jsonl}` (statuses: firm / killed_at_firm / unverifiable / provisional_only / killed_at_provisional — "killed for weak lift" always distinguished from "killed for unprovability") + `outputs/<run-id>/wayback_evidence.jsonl` (one row per checked cell: tag, knowable-by window, strike window).

**Typed signals:** every hypothesis names a field the Phase 2 parsers emit (`scripts/panel_parsers.py` `FIELD_TYPES` / `RECORD_FIELDS`) and a predicate that fits its type (`scripts/signal_presence.py`). Gate 4 rejects unsupported selectors and numeric fields without a threshold. A field the parser could not extract is `unknown`, never `absent`. Live fetchers and the offline replay share the same parsers.

**Firm pass, dated-record route:** only a record that itself satisfies the predicate can date the signal; its URL, date and excerpt are kept. A source with no satisfying record, or only satisfying records dated after the reference, is unknown: search results cannot prove absence.

**Firm pass, archive route:** the latest Wayback capture at-or-before the reference is re-parsed by the same Phase 2 parser and the same typed predicate is evaluated on it (a substring never certifies a signal). Only sources whose fields can be re-extracted from an archived page qualify (`ARCHIVE_PARSERS` in `backcast_validate.py`: the homepage scrape); others are unknown. A predicate that fails on a real capture is a provable absence.

**No provable absence, no firm lift:** a hypothesis with zero firm-absent cells is `unverifiable` ("insufficient evidence"), never killed for weak lift and never shipped.

**Scoring:** Phases 7 and 8 share one function (`scripts/rubric_score.py`). Unknown signals leave the denominator, `coverage` is reported, and a panel with nothing observed is `insufficient_evidence`.

**Human gate:** `orchestrate.py --stop-after 3` stops after segmentation. Phases 4-7 refuse to run until `--approve-archetypes` records the current `archetypes.jsonl` (an edit after approval needs re-approval). No signal surviving is carried into `discriminator.md/.jsonl` as "no reliable discriminator found", with causes and next actions.

**Survival rules** (`apply_survival_rules`): train lift ≥ 1.5; holdout lift ≥ 0.8 × train; and significance on BOTH splits — Wald CI lower bound ≥ 1.2 on large tables, Fisher's exact on small ones (train two-sided p ≤ 0.05, holdout one-sided p ≤ 0.10). Lift is always the probability ratio; the odds ratio is reported separately. A split with fewer than 10 (train) or 5 (holdout) labelled, observed customers fails as "insufficient evidence".

**Holdout isolation:** archetypes carry their members with a `holdout` flag (`wlr_schema.load_archetypes`). Phase 5 sees training members, their themes and their panels only (`generate_hypotheses.training_inputs`).

**Gate:** if zero signals survive for an archetype, the result is an explicit **"no reliable discriminator found"**, with its causes counted separately (`insufficient_evidence`, `signal_not_observed`, `no_detectable_relationship`). That is a valid answer, not a prompt to keep cleaning data until something passes.

### Phase 6.7 — Knowability Panel (dedicated agent per firm signal)

**Goal:** the statistics picked the signals; now one dedicated research agent per firm signal asks "how could we have determined this ahead of time, in THIS vertical?" Sometimes the honest answer is you can't; sometimes there's a proxy; sometimes direct evidence sits in a dataset nobody checked. There is deliberately no universal dataset catalog — knowability is vertical-specific.

1. For each `status=="firm"` signal, spawn a subagent (Sonnet, per cost-controls) with `prompts/knowability-agent.md` + a briefing: the signal's firm lift + strike windows, archetype context, per-customer timelines from `wayback_evidence.jsonl` + `wayback_timeline`, and the source manifest.
2. The agent reasons first (causal chain: pain → visible exhaust → detection, with delay per hop), then searches independently — using the free procedure in `references/free-source-discovery.md` — for datasets that existed *at that moment*, proving any claim with a dated worked example (archive.org via the prover; Common Crawl when Wayback has a gap — see `references/time-travel.md`).
3. Verdicts (`cant_know` / `proxy` / `direct_evidence`) collect into `outputs/<run-id>/knowability.jsonl`; Phase 7 renders them into the deliverable automatically.

### Phase 7 — Discriminator Spec + Cynical Buyer Pass

**Goal:** Compose FIRM signals into a per-archetype scoring rubric. Score from a tired CRO's perspective — would they believe this?

1. `scripts/compose_discriminator.py` — combine `status=="firm"` signals per archetype into a weighted scoring rubric (`--allow-provisional` is the loud escape valve). Produces concrete API queries, thresholds, a worked example on a held-out customer, and per-signal evidence status, firm coverage, median strike window, and knowability verdict.
2. `prompts/cynical-buyer.md` — role-play exhausted CRO/CEO. Score each finalist on 8 axes (1–10; the 8th is temporal evidence firmness — firm coverage, bracket-dated strike windows, knowability) plus 3 mandatory litmus tests ("So what?", "Wikipedia test", "Competitor test"). Mirrors blueprint-pvp-deep.
   - 8.0+ = ship
   - 7.0–7.9 = one revision attempt
   - <7.0 = destroy
3. Output: `outputs/<run-id>/discriminator.md` — the deliverable.

### Phase 8 — `score` mode

**Goal:** Apply the discriminator to a single new prospect domain.

1. `scripts/score_prospect.py <domain>`:
   - Read latest `discriminator.md`.
   - Pull Phase-2-style signal panel for `<domain>`.
   - Score against each archetype's rubric.
   - Return: looks-like-archetype-X confidence Y%, expected fit Z, signal-by-signal diff.

This is the daily-driver mode for a BDR team after the run completes.

---

## Architectural patterns

Every phase is built on a published GTM pattern. The patterns are general — no client-specific code is bundled.

| Need | Pattern |
|------|---------|
| Gong calls + transcripts | Basic-auth `/v2/calls` paginated + `/v2/calls/transcript` batched at 100 |
| Gong↔CRM fuzzy match | Title parse → suffix normalize → token Jaccard |
| Speaker attribution (who said it, how elicited) | Roster from `parties[].affiliation` → tag turns `company\|customer\|unknown` → grade customer statements `unprompted\|led\|seller_echo` (`references/speaker-attribution-protocol.md`) |
| Batch API CLI (Sonnet, JSONL, 6-step) | `create / submit / status / download / parse / run-all` |
| Batch API w/ Opus + adaptive thinking | Adaptive-thinking audit on every flagged record |
| Goal-backward verification | Verify against ground truth at every transition |
| Holdout + lift | 20% withhold per cluster; signal must replicate before shipping |
| Hard gates | Five-gate validator (see `prompts/hard-gates.md`) |
| Cynical Buyer + 3 litmus tests | Adversarial evaluation before final ship |
| 5-parallel-subagent source discovery | TAM-research-style fan-out for enrichment |
| Enrichment APIs | EXA, Firecrawl, OpenWebNinja, Apify (built-in); optional vertical-specific data warehouses |
| Time-travel proof (what was visible, when) | archive.org CDX digest-collapse + capture bracket-search (`scripts/wayback_timetravel.py`); Common Crawl as the taught fallback archive (`references/time-travel.md`) |
| Evidence-visibility tagging | [ARCHIVED-BEFORE] / [RECORD-DATED-BEFORE] / [RETRO-ASSUMED]; firm-only lift (`references/lift-and-holdout.md`) |
| Per-signal knowability agents | one Sonnet subagent per firm signal; verdicts cant_know / proxy / direct_evidence (`prompts/knowability-agent.md`) |

---

## Environment requirements

```bash
# Required
ANTHROPIC_API_KEY              # Batch API + agent calls
GONG_BASE_URL                  # REQUIRED, your own host: https://us-XXXXX.api.gong.io
GONG_ACCESS_KEY
GONG_SECRET_KEY

# Optional — Phase 1.4 speaker attribution
SELLER_DOMAINS                 # comma-separated, the analyzing company's own email domain(s);
                               # fallback only — Gong parties[].affiliation is preferred when present

# Required for Phase 2 enrichment (any subset; missing sources skip)
EXA_API_KEY
FIRECRAWL_API_KEY
OPENWEBNINJA_API_KEY
APIFY_TOKEN

# Optional vertical-specific data warehouses (only if you have access)
# Wire your own connections in scripts/enrich_panel.py — none are required.
```

---

## What this is NOT

- Not an article generator. Output is a discriminator spec, not Substack copy.
- Not a PVP/message generator. Phase 5 produces signals, not messages. (Step 10 of START-HERE turns validated signals into a message.)
- Not a churn root-cause analyzer — this skill is forward-predictive.
- Not a TAM-sizing tool — `tam-research` discovers sources for a known segment; this skill discovers the segment itself.

---

## Quick reference

```bash
# 1. Calibrate user data
/win-loss-rewind init ~/Downloads/customers.csv

# 2. After approving data_confidence.md, run pipeline
/win-loss-rewind run

# 3. Apply to a cold prospect
/win-loss-rewind score acmelegal.example
```

Resumable: every phase artifact is stable. `--resume-from N` to re-enter mid-pipeline.

---

**Version:** 1.1 (July 2026) — time travel: archive.org prover (`wayback_timetravel.py` — moments/get/bracket, flock throttle, shared circuit breaker), two-pass firm lift with evidence-visibility tags, per-outcome reference dates, source temporal manifest, Gate 4b temporal provability, knowability panel (Phase 6.7), Cynical Buyer 8th axis, Common Crawl taught as fallback archive. Also fixed the signal_panel/signals key mismatch and score-mode enrichment. (1.0, May 2026 — initial build.)
