---
name: dossier-data-auditor
description: Pre-flight partner to the dossier-builder skill — audit customer/prospect data quality before a dossier build (or after, or standalone). Point it at any folder of customer data (CSV/XLSX/SQLite/Parquet/JSONL); it classifies columns, verifies join keys and company domains, reconciles conflicting revenue columns, and writes an overrides file dossier-builder auto-detects. Launch it whenever dossier-builder is about to run, and whenever the user says "audit my customer data," "check the joins on this," "is this data fit for a dossier," or wants a data scorecard. Refuses to greenlight bad joins without a written user override.
category: blueprint
version: 1.0
tier: team
lifecycle: validated
author: "Jordan Crawford / Blueprint GTM"
contributors: []
status: active
valid_from: 2026-05-25
invalid_at: null
last-verified: 2026-06-18
requires-keys: [BLITZ_API_KEY, EXA_API_KEY, FIRECRAWL_API_KEY, GROQ_API_KEY, LINKUP_API_KEY, OPENWEBNINJA_API_KEY, PARALLEL_API_KEY, PERPLEXITY_API_KEY]
---

# Dossier Data Auditor

A client-agnostic skill that audits the data going INTO (or OUT of) a customer-dossier build. Point at any folder; the skill auto-discovers files, classifies columns, and audits in user-priority order — **domains first, joins second, revenue third, sanity last** — using the full multi-provider stack for ground-truth verification, with deterministic arithmetic ahead of every LLM reasoning step so verdicts are falsifiable, not vibes.

## When this skill fires

- "Audit my dossier data."
- "Check the joins on this."
- "Is this data fit for a dossier?"
- "Tell me what's wrong with my customer data."
- "Scan this folder before we run the pipeline."
- "Are these columns trustworthy?"
- "Give me a data scorecard before I burn a day on this."
- "Run the pre-flight on our [client] data."
- "What's going to break if we build dossiers from this?"

If the user is asking about data quality, joins, fill rates, duplicate columns, or "is this fit for X," this skill is the right tool.

## Three run modes

Bidirectional plus standalone. None of the three is the "real" one — they're equal-weight entry points serving different moments.

| Mode | Trigger | Output |
|---|---|---|
| `pre-build` (default) | Audit BEFORE running `dossier-builder`. The recommended workflow. | `audit_findings.json` + scorecard + `recommended_engagement_overrides.json` that `dossier-builder`'s W0 ingests automatically (see its `SKILL.md:73-75` and `../dossier-builder/prompts/00-mode-select.md`). |
| `post-build` | Audit AFTER `dossier-builder` produced `outputs/dossiers/<slug>/`. | Cross-checks `linking_trust` distribution, MRR canonical-pick coverage, ICP web-sample fraction, the `flags[]` array. |
| `standalone` | Any folder, anywhere, regardless of whether `dossier-builder` is involved. | Same scorecard + findings — but no `recommended_engagement_overrides.json` (no downstream consumer). |

## Core ethos (every sub-agent inherits these)

1. **Falsifiable verdicts only.** Every claim names the metric, the threshold, the sample size, and the citation URL where applicable. "Fill rate" alone is not signal — pair it with cardinality, modal-value spike, NULL encoding diversity, and last-write recency. The audit trail is the artifact; the conclusion is downstream.
2. **Deterministic math before LLM reasoning.** Orphan rate, fan-out cardinality, modal spike, Spearman correlation, schema coercion failure — these run as Python before any agent reasons. Agents reason over numbers, not raw rows.
3. **Skeptical by default.** Never say "looks good" or "everything checks out." Lead with what's broken. If nothing flagged, list what was checked and that nothing flagged. Reassurance through specificity, not optimism.
4. **Hard refusal on bad joins without written override.** If W3's three-test guard fails, the audit fails — unless `notes_from_user[]` contains an explicit override with a written reason. Silent override is impossible.
5. **No emojis, no chart-junk, no hedging.** Fixed-width tables, severity tags in caps, every line earns its space. Banned phrases (`looks good`, `everything checks out`, `seems fine`, `nothing major`) get instructed-against in every sub-agent prompt rather than blocked at the renderer.

## Wave-by-wave execution

Stateful. Each wave writes to `outputs/audits/<audit_slug>/`. Every gated wave ends with the four-block panel template at `../dossier-builder/references/interactive-pause-script.md`.

### W0 — Audit context + stated expectations (gated)

Capture (mirroring `dossier-builder/prompts/00-mode-select.md` so the handshake is shape-compatible):

- `audit_slug` (e.g., `acme-2026-05`)
- `data_dir` (absolute path; validate readable)
- `expected_icp` (free-text)
- `tenant_domain` (string or array; load-bearing for self-domain strip at W3)
- `priority_order` (default `[domain, joins, revenue, fill_rate, paradox]`; user can reorder)
- `claimed_primary_id_system` (W2.5 will prove or disprove)
- `do_not_use_fields_from_revops` (array of `{field_path, reason}`, same shape as builder's `engagement.json`)
- `recent_migrations[]` (list of "CRM swap <quarter>", "enrichment refresh <month>", etc. — captures expected anomalies)
- `user_stated_expectations` (`expected_customer_count`, `expected_prospect_count`, `expected_mrr_shape ∈ {log_normal, bimodal, uniform, unknown}`, `expected_icp_fit_pct`)
- `data_truth_keeper_name` (the person who knows where the bodies are buried)

Read `references/multi-provider-verification.md`, `references/per-system-field-landmines.md`, `references/dance-script.md` once at this wave. Write `outputs/audits/<audit_slug>/audit_context.json`.

### W1 — Scan + auto-discovery + typed schema validation (silent unless empty)

Spawn `scan-agent` (prompt: inline orchestration; no dedicated sub-agent needed). Runs `scripts/scan.py --data-dir <dir>` and `scripts/schema_coerce_audit.py`. Walks the folder recursively for CSV/XLSX/SQLite/Parquet/JSONL. For every file:

- Parse schema (columns, dtypes, row count, file size, mtime).
- Classify every column against `config/column-dictionary.yaml` (12 classes including `domain_like`, `linkedin_like`, `revenue_like`, `id_like`, `name_like`, `email_like`, `phone_like`, `geo_like`, `date_like`, `free_text`, `numeric`, `unknown`).
- Sub-classify URL-like columns (`primary_website`, `domain_only`, `email_inferred_domain`, `linkedin_url`, `enrichment_domain`, `secondary_domain`, `platform_subdomain`, `free_text_url`).
- Test every candidate join pair for inner-join fraction.
- Run typed coercion audit per declared semantic class: SF 15/18-char IDs, email regex, phone E.164, currency strip-and-coerce, date format detection.

Outputs: `scan/files_inventory.json`, `scan/column_inventory.json`, `scan/join_candidates.json`, `scan/column_coercion_audit.json`.

Pause only if zero files matched or zero join candidates surfaced.

### W1b — Name primitives audit (gated if clusters found)

Run `scripts/name_cluster.py`. Per account-bearing table:
- Normalize names: lowercase, strip corporate-suffix stopwords (`inc`, `llc`, `ltd`, `corp`, `gmbh`, `ag`, `sa`, etc.).
- Block-cluster within first-3-token blocks; compute pairwise token-set Jaccard ≥ 0.85.
- Within each cluster, classify variants: `legal_entity`, `legal_short`, `historical_legal`, `brand`, `dba`, `all_caps_variant`.
- Cross-reference against domain candidates (do all members of the cluster share a registered-domain root?).
- Per ambiguous cluster, call Blitz companies search per variant + OpenWebNinja AI Mode as cross-check.

Output: `name_clusters.json`. Pause if any cluster has > 1 candidate canonical name and the human needs to pick.

### W2a — URL column harvest + canonical-domain-pick (no pause, feeds W2b)

Run `scripts/url_harvest.py` then `scripts/canonical_domain_pick.py`. For every account row:

1. Extract every URL-like value across all URL-class columns (including regex-extracted URLs from free-text fields).
2. Canonicalize: lowercase, strip scheme, strip `www.`, strip path, strip query, strip trailing slash, IDN-decode.
3. Exclude tenant_domain, personal-email-domains (the list shipped at `config/personal-domains.txt`, extendable with `--personal-domains <file>`; the picker stops if it is missing), parking-page domains, platform_subdomain class.
4. Score each candidate via the `+3/+2/-3` rubric in `config/domain-rubric.yaml` (see `references/domain-canonicalization.md`).
5. Output: top score is canonical pick + runner-up + confidence ∈ `{high, medium, low, zero}`.

Outputs: `domain_inventory.json`, `domain_candidates_per_account.json`.

### W2b — Multi-provider verification on disagreements + low-confidence (gated)

Spawn `domain-canonicalizer-agent` (prompt: `prompts/02b-domain-canonicalizer.md`). For every account with confidence ∈ `{low, zero}` OR cross-column conflicts:

1. Blitz `domain↔linkedin` round-trip (unlimited; primary).
2. OpenWebNinja AI Mode cross-check (independent grounded second opinion).
3. Firecrawl page render → `domain_state_classify.py` emits one of `{verified, parked, dead, acquired_or_repointed, tenant_subdomain, shortener}`.
4. Exa SERP only when Blitz + OWN disagree (Serper retired).
5. DiscoLike `/match` last-resort.

Sample stratified across: free-mail domains, short domains (≤5 chars), country-TLD mismatches, domains containing digits, cross-column conflicts, cross-source conflicts, plus random control. Budget: ~200 verifications.

Output: `audit/domain_audit_findings.json`. Render W2b panel with trust distribution + provider-agreement table + worst offenders + 3 questions for the human.

### W2c — Multi-domain consolidation (gated if clusters found)

Run `scripts/multi_domain_cluster.py`. Pairwise across all canonical domains: Blitz companies search (do two domains resolve to the same LinkedIn company URL?) + OpenWebNinja AI Mode parent/child inference + WHOIS owner match + shared GTM ID + redirect-graph terminus. ≥2 signals → cluster. ≥1 strong signal (Blitz same LinkedIn) → cluster. ≥1 weak signal alone → flag for human.

Output: `multi_domain_clusters.json` with `{cluster_id, parent_domain, child_domains[], evidence[], rollup_recommendation}`.

### W2.5 — ID system reconciliation (HARD GATE if disagreement at high confidence)

Spawn `id-system-reconciler` (prompt: `prompts/02_5-id-reconciliation.md`). Reads `references/id-system-archaeology.md`. Inputs: `audit_context.json` (with claim), `column_inventory.json` (every ID-shaped field), `domain_audit_findings.json`.

Job: prove or disprove the user's claimed primary ID system, per object class. Builds the bipartite link graph (system A's column → system B's column at fill_rate × resolution_rate), detects ghost IDs (SFDC 15-char checksum violations, HubSpot IDs mistyped as SFDC), detects ID-on-wrong-object (integration-tool misconfiguration patterns), detects dual-canonical zones (SFDC canonical for accounts, Stripe canonical for paying customers).

Output: `id_system_audit.json` with `per_object_canonical_id`, `id_chain_health[]`, `dual_canonical_zones[]`. Hard-gate: if recommendation ≠ claim at high confidence, the user must explicitly accept the recommendation or write an override before W3 proceeds.

### W3 — Join inference (HARD REFUSAL until written override)

Four-hat fan-out in parallel. Each reads the same `join_attempt_metrics.json` produced by `scripts/referential_metrics.py` (deterministic arithmetic — NOT LLM):

1. **`referential-integrity-auditor`** (prompt: `prompts/03a-referential-integrity.md`) — orphan rate, fan-out cardinality, surrogate-key collisions, scientific-notation in IDs, SF 15-vs-18-char overlap, transitive chain validation. Summarizes the deterministic numbers; never overrides them.
2. **`join-statistical-anomaly-auditor`** (prompt: `prompts/03b-join-statistical-anomaly.md`) — row-count inflation after join, distribution-shift comparison pre vs post-join, brand-name-at-low-revenue (Apple-at-$20), cardinality-of-result-vs-expectation.
3. **`self-domain-guard`** (prompt: `prompts/03c-self-domain-guard.md`) — applies tenant_domain strip and counts `false_matches_avoided`; identifies multi-tenant-domain join risks (accenture.com → many accounts).
4. **`join-semantic-reviewer`** (prompt: `prompts/03d-join-semantic-reviewer.md`) — reads the three prior verdicts + `../dossier-builder/references/linking-trust-rubric.md`. Applies six-verdict rubric + three-test guard. Final decision.

`scripts/reconcile_join_verdicts.py` merges into one `join_verdicts.json`. Any join with `verdict ∈ {FAIL_HARD, FAIL_STRUCTURAL, REJECT}` blocks until human writes an explicit override into `notes_from_user[]`. Cost-guard wraps the LLM step at $100 per the global threshold.

### W4 — Revenue reconciliation (gated)

Run `scripts/revenue_reconcile.py`. Reads `references/revenue-audit.md` and `config/revenue-rubric.yaml`.

- Cluster all `revenue_like` columns into concept groups: `probable_mrr`, `probable_arr`, `probable_deal_amount`, `probable_historical_total`, `probable_payment_volume`, `probable_count_proxy`.
- Per cluster apply canonical-pick rubric: fill_rate ⊕ source_authority ⊕ recency ⊕ round-cluster-demotion ⊕ do_not_use blocking.
- Apple-at-$20 watchlist across 15 brand names.
- Benford's-law χ² test on each picked revenue column.

Output: `audit/revenue_findings.json` with canonical pick per cluster + rejected sources with reasons + conflict log.

### W5 — Column fitness pentad (silent unless any column unfit/dead)

Three sub-agents in parallel reading `scripts/column_metrics_pentad.py`'s output:

1. **`column-distribution-statistician`** (prompt: `prompts/05a-column-distribution-statistician.md`) — distribution shape vs declared semantics (uniform MRR is suspicious; log-normal is real; 50/50 boolean in a customer-only set is suspicious).
2. **`column-recency-historian`** (prompt: `prompts/05b-column-recency-historian.md`) — `LastModifiedDate` distributions per column; detect dead columns (bimodal at one <bulk-load date> + scattered current) and bulk-import fingerprints.
3. **`column-encoding-detective`** (prompt: `prompts/05c-column-encoding-detective.md`) — NULL/sentinel variety (`NULL` vs `''` vs `'N/A'` vs `0` vs `noreply@tenant_domain` in same column); case-inconsistent enums.

Output: `column_fitness_verdicts.json` with per-column verdict `{fit, marginal, unfit, dead}` + the dimension that caused the verdict.

### W5b — Contact-fill audit (gated if poor)

Run `scripts/contact_fill_audit.py`. Grade contact-level quality per the 9-metric rubric in `references/per-system-field-landmines.md` (accounts-with-≥1-contact, accounts-with-verifiable-email contact, accounts-with-LinkedIn-URL contact, contacts-per-account p50/p90, contact-name fill, contact-title fill, bulk-import-tenure cluster, same-email dedup fraction).

For "verifiable email": regex + DNS MX (free) on full set, MillionVerifier on 500-row sample (~$0.45) for the cohort grade. For LinkedIn URL validity: invoke `enrichment/linkedin-validator` skill on 100-row sample (uses RapidAPI LinkedIn underneath).

Output: `contact_fill_audit.json`.

### W5.5 — Account hierarchy resolution (gated if hierarchy fields present)

Spawn `hierarchy-resolver-agent` (prompt: `prompts/05_5-hierarchy-resolver.md`). Walks `ParentId` / `Ultimate_Parent__c` / equivalent chain. Detects self-parent anomaly, orphan-children-with-revenue (split-signal pattern), DBA collision among siblings, inactive-parent-active-children, hierarchy-depth tails > 5 (circular or junk).

Output: `account_hierarchy.json` with `parent_map`, `hierarchy_findings`, `rollup_directive ∈ {rollup_to_parent, keep_separate, both}` (ask the human; don't assume).

### W6 — Cross-field paradox + ICP cohort sanity + intra-source dedup (gated)

Spawn `sanity-agent` (prompt: `prompts/06-paradox-sanity.md`). Three activities:

1. **5 cross-field paradox checks** — `employee_range_revenue_implausible` (the "11-50 emp + $1M rev" case), `industry_revenue_implausible`, `geo_phone_mismatch`, `healthcare_npi_revenue_paradox`, `id_format_inconsistent_within_column`.
2. **200-row ICP cohort sample** — **Parallel.ai Task Groups** primary (calibrated Basis confidence per row, ~$5-8). **Exa Agent (beta)** secondary on `confidence < 0.6` rows — independent semantic-index second opinion. Header pin `Exa-Beta: agent-2026-05-07`. Perplexity Sonar Pro tertiary if both still uncertain.
3. **Per-record paradox resolution** — Parallel Task Core ($25/1K) for each paradox flagged in step 1; cap 50 rows / $15. Exa Agent on disputed rows.
4. **Intra-source dedup-likelihood** — within one source, find pairs of rows with (name fuzzy ≥ 0.92 AND domain match AND non-overlapping non-null fields). Apple-as-47-records detection.
5. **Cohort-count-vs-expectation** — actual loaded count vs `user_stated_expectations.expected_customer_count` from W0.

Output: `audit/paradox_findings.json`.

### W7 — Scorecard + handshake (terminal)

Run `scripts/render_scorecard.py`. Produces:

- `audit/scorecard.txt` — 1-page plaintext (fixed-width tables).
- `audit/scorecard.html` — same content, web-rendered.
- `audit/audit_findings.json` — full machine-readable record.
- `audit/cost_summary.json` — total $ spent, per-provider breakdown.
- **`audit/recommended_engagement_overrides.json`** — drop-in for `dossier-builder` W0. See § Handshake schema below.

Final panel: "Audit complete. Pre-build go/no-go: <verdict>. Next: run `dossier-builder` with the recommended overrides."

## Handshake schema (the load-bearing contract)

`outputs/audits/<audit_slug>/audit/recommended_engagement_overrides.json` consumed by `dossier-builder/SKILL.md:73-75` at its W0 Step 0. Old keys preserved for back-compat; new keys are additive (the builder's W0 ingests what it knows and ignores the rest; a coordinated W0 update will teach it the new keys).

```json
{
  "do_not_use_fields": [{"field_path": "sfdc.Account.Revenue_Rollup__c", "reason": "always zero (stale integration)"}],
  "tenant_domain": ["contoso.example"],
  "primary_id_system": "sfdc",
  "rejected_joins": [{"from": "gong.call.account_id", "to": "sfdc.Account.Id", "reason": "orphan_rate≈0.4", "verdict": "FAIL_HARD"}],
  "canonical_revenue_picks": {"probable_mrr": "sfdc.SBQQ__Subscription__r.Amount__c"},
  "per_account_linking_trust": {"<account_id>": "high|medium|low"},

  "per_object_canonical_id": {"accounts": "sfdc.Account.Id", "subscriptions": "stripe.Customer.id"},
  "dual_canonical_zones": [{"object": "paying_customers", "primary": "stripe", "secondary": "sfdc", "coverage_gap": "850 stripe-only customers"}],
  "account_hierarchy": {"parent_map": {"<child_id>": "<parent_id>"}, "rollup_directive": "rollup_to_parent"},
  "name_clusters": [{"cluster_id": "cl_0042", "account_ids": [], "display_name": "Apple", "legal_name": "Apple Inc."}],
  "canonical_domain_per_account": {"<account_id>": {"domain": "globex.example", "state": "verified", "confidence": "high"}},
  "domain_state_blocklist": ["parked-example.com"],
  "multi_domain_clusters": [{"cluster_id": "cl_0017", "parent_domain": "globex.example", "child_domains": ["globexmail.example", "globexbooks.example"], "rollup_recommendation": "rollup_to_parent"}],
  "multi_tenant_join_blocklist": ["accenture.com", "deloitte.com"],
  "column_fitness_verdicts": {"sfdc.Account.Industry": "dead"},
  "column_coercion_rules": {"sfdc.Contact.MRR__c": "strip_$_,coerce_decimal"},
  "null_sentinels_per_column": {"sfdc.Contact.Email": ["noreply@contoso.example", ""]},
  "treat_as_suspect_fields": [{"field_path": "sfdc.Lead.LeadSource", "reason": "75% written in a single old import"}],
  "contact_fill_audit": {"accounts_with_zero_contacts": [], "recommend_contact_finder_run": true, "linkedin_url_fill_pct": 0.34},
  "user_stated_expectations": {"expected_customer_count": 12000, "actual_loaded_count": 12500, "expected_mrr_shape": "log_normal"}
}
```

## Sub-agent fan-out cheat sheet

Every sub-agent runs on Opus. Groq Llama (via `enrichment/blitz` or direct) used inside agents for cheap structured extraction only.

| Agent | Wave | Prompt | Reads | Env keys |
|---|---|---|---|---|
| `domain-canonicalizer-agent` | W2b | `prompts/02b-domain-canonicalizer.md` | `domain_candidates_per_account.json`, provider responses | BLITZ_API_KEY, OPENWEBNINJA_API_KEY, FIRECRAWL_API_KEY, EXA_API_KEY |
| `id-system-reconciler` | W2.5 | `prompts/02_5-id-reconciliation.md` | `audit_context.json`, `column_inventory.json`, `domain_audit_findings.json` | (none — local Opus + Python) |
| `referential-integrity-auditor` | W3 Hat 1 | `prompts/03a-referential-integrity.md` | `referential_metrics.json` (from `scripts/referential_metrics.py`) | (none) |
| `join-statistical-anomaly-auditor` | W3 Hat 2 | `prompts/03b-join-statistical-anomaly.md` | `join_anomaly_metrics.json` | (none) |
| `self-domain-guard` | W3 Hat 3 | `prompts/03c-self-domain-guard.md` | `audit_context.json`, `referential_metrics.json` | (none) |
| `join-semantic-reviewer` | W3 Hat 4 | `prompts/03d-join-semantic-reviewer.md` | Hat 1/2/3 verdicts + linking-trust-rubric | (none) |
| `column-distribution-statistician` | W5 | `prompts/05a-column-distribution-statistician.md` | `column_metrics_pentad.json` | (none) |
| `column-recency-historian` | W5 | `prompts/05b-column-recency-historian.md` | `column_metrics_pentad.json` | (none) |
| `column-encoding-detective` | W5 | `prompts/05c-column-encoding-detective.md` | `column_metrics_pentad.json` | (none) |
| `hierarchy-resolver-agent` | W5.5 | `prompts/05_5-hierarchy-resolver.md` | `hierarchy_resolve.json` | (none) |
| `sanity-agent` | W6 | `prompts/06-paradox-sanity.md` | scan + audit outputs | PARALLEL_API_KEY, EXA_API_KEY, PERPLEXITY_API_KEY |

Full equipping matrix at `config/provider-routing.yaml`. Graceful-degradation rules in `references/multi-provider-verification.md`.

## The dance — what each pause asks, and what NOT to ask

Read `references/dance-script.md` once at W0. Summary: 8-11 potential pauses, most silent unless interesting. Map:

| Wave | When pause fires | What earns trust | What's insulting |
|---|---|---|---|
| W0 | Always | "Walk me through every field you've told the team NOT to use, and why" / "Are there migrations in the last 24 months I should know about?" / "Who's the person who knows where the bodies are buried?" | "What's your CRM?" / "What's a CSV?" |
| W1 | If scan finds surprises | "I see data from a marketing-automation tool you no longer mention — is it still active or from before a migration?" | (no insult risk) |
| W1b | If name clusters found | "5 accounts look like Apple variants — same entity or 5 distinct?" | (no insult risk) |
| W2b | Always | Show worst 5 provider-disagreement domains; surface freemail-as-account-domain count | "Which is better, Serper or Exa?" — they don't care |
| W2.5 | Always | Surface the recommended primary ID system + evidence; ask for confirmation or override | (no insult risk) |
| W3 | Always (HARD GATE) | Show top 10 join concerns in a fixed-width table; specific tiebreaker questions | (no insult risk) |
| W4 | Always | Render revenue-disagreement table; ask which column the CFO uses in the board deck | (no insult risk) |
| W5 | If unfit/dead columns | Show specific columns with the metric that flagged them | (no insult risk) |
| W5b | If contact fill weak | "1,800 accounts have zero contacts — auto-run contact-finder, or ask per account?" | (no insult risk) |
| W5.5 | If hierarchy clusters | Surface parent-child rollup candidates; ask the rollup direction | "Should we use ParentId?" — show them the choice, don't ask abstractly |
| W6 | Always | ICP-misses, paradox resolutions, cohort-count surprise | (no insult risk) |
| W7 | Sign-off | Show every override about to be written; user types "approve" or makes edits | (no insult risk) |

## Privacy and client-agnostic guardrails

Read at W0; verify at W7.

- The skill itself contains zero client names, domains, vertical-specific field names. Everything client-specific lives in `outputs/audits/<audit_slug>/`.
- `audit_context.tenant_domain` is the only place the customer's own domain lives. Every sub-agent strips it from email-domain matches before testing.
- Truncation rules: domains > 24 chars truncated, IDs > 12 chars truncated, names > 18 chars redacted to `<first-token>..`. Full values in JSON only.
- `scripts/render_scorecard.py` runs a final grep at W7 to verify no client-specific values leaked into the skill's own files.

## Multi-provider verification policy

Hard policy in W2 and W6. The Floqer GTM-stack PDF's load-bearing claim:

> "No single provider sees the whole web — the play is running multiple in parallel and merging results."

Provider routing in `config/provider-routing.yaml`. Merge rule: ≥2 of N agree → `verified`; disagree → `mismatch` (surface to user); all fail → `unverifiable` (no claim, don't fabricate).

**Routing summary** (full table in `references/multi-provider-verification.md`):
- Domain canonicalization (per-record full corpus): **Blitz** primary (unlimited) → OpenWebNinja AI Mode secondary → Firecrawl + DiscoLike last-resort.
- ICP cohort sanity 200-row (W6): **Parallel.ai Task Groups** primary (calibrated Basis confidence) → **Exa Agent (beta)** secondary on `<0.6` rows (independent semantic-index lens; pin `Exa-Beta: agent-2026-05-07`) → Perplexity Sonar Pro tertiary.
- Per-record paradox (W6): **Parallel.ai Task Core** primary ($25/1K, cap $15) → Exa Agent on disputed rows.
- Multi-domain consolidation (W2c): **Blitz** companies search → OpenWebNinja AI Mode → DiscoLike `/subsidiaries` last-resort.
- LinkedIn validation (W5b): `linkedin-validator` skill (uses RapidAPI LinkedIn underneath).

## Validation

Earlier versions were checked by replaying frozen exports (private, not shipped). The
cases they must catch: a derived foreign key that is about 40% wrong (W3 Hat 1 reports
`orphan_rate≈0.4`, verdict `FAIL_HARD`), a revenue field that is always zero (W4
`do_not_use`), and the self-domain trap before joins (W3 Hat 3: `false_matches_avoided > 0`).
To try the skill on data you can see, point it at `examples/northwind-demo/corpus/` at the repo root.

## Deliberate non-goals

- NOT a replacement for `dossier-builder`. Audits inputs; doesn't produce dossiers.
- NOT a pass-2 analysis tool (no scoring, churn-cause attribution).
- NOT a continuous DQ cron monitor (one-shot per engagement).
- NOT a CRM writeback (output: local JSON + HTML + overrides file).
- NOT a column-mapping/ETL tool (read-only inspection).
- NOT a BI dashboard (1-page scorecard, not interactive UI).
- NOT an AI-fix tool (surfaces problems; doesn't auto-correct).
- NOT a TAM-research / entity-discovery tool (Parallel.ai FindAll's domain — explicitly different skill).

If the user asks for any of those, finish the audit first, then suggest the right next skill.

## Integration with other skills

- **`dossier-builder`** — sibling. The auditor is its recommended pre-flight. The auditor's `recommended_engagement_overrides.json` is auto-detected and ingested by `dossier-builder`'s W0 (see its `SKILL.md:73-75` and `../dossier-builder/prompts/00-mode-select.md`). When new additive keys appear in the overrides file but the builder hasn't been updated to read them yet, the builder ignores them gracefully and falls back to its existing W0 interactive capture.
- **`enrichment/blitz`** — unlimited; the workhorse for domain↔linkedin round-trip (W2b), brand-name reconciliation (W1b), multi-domain detection (W2c).
- **`enrichment/openweb-ninja`** — AI Mode for independent grounded second opinion when Blitz returns low confidence.
- **`enrichment/linkedin-validator`** — 3-tier contact validation on a 100-row sample at W5b (uses RapidAPI LinkedIn underneath).
- **`enrichment/domain-linkedin-finder`** — import the canonical-domain helpers + parking-page detector (not included). The personal-domain blocklist ships with this skill at `config/personal-domains.txt`.
- **`enrichment/firecrawl`** — page render for W2b state classification.
- **`enrichment/exa`** — SERP tie-breaker in W2b when first-pass providers disagree (`enrichment/serper` retired).
- **`enrichment/discolike`** — last-resort only for `/subsidiaries` and `/redirects`.
- **The $100 rule** — before the W3 LLM verdict step, estimate cost; above $100, stop and get an explicit yes.

## References (read on demand)

| Reference | Read at | Purpose |
|---|---|---|
| `references/multi-provider-verification.md` | W0, W2b, W6 | Floqer doctrine + per-use-case routing + merge algorithm |
| `references/domain-canonicalization.md` | W2a, W2b | The +3/+2/-3 rubric in prose; per-state treatment |
| `references/name-reconciliation.md` | W1b | Brand vs legal vs DBA; cluster strategy |
| `references/multi-domain-consolidation.md` | W2c | One-parent-many-brands detection logic |
| `references/id-system-archaeology.md` | W2.5 | Per-system canonical ID guidance; chain integrity model |
| `references/per-system-field-landmines.md` | W0, W5 | SFDC/HubSpot/Stripe/Gong/Product/Support/MA field cheat sheet |
| `references/statistical-anomaly-catalog.md` | W4, W5 | 18 patterns explained with thresholds |
| `references/revenue-audit.md` | W4 | Six-revenue-columns + canonical-pick + Apple-at-$20 |
| `references/dance-script.md` | W0 | What each pause asks, what NOT to ask |
| `../dossier-builder/config/dq-checks.yaml` | W1, W4, W6 | 25-check catalog the auditor inherits |
| `../dossier-builder/references/linking-trust-rubric.md` | W3 Hat 4 | Six-verdict + three-test guard (verbatim) |
| `../dossier-builder/references/lessons-from-prior-builds.md` | W0 | Catalog of common failure patterns |
| `../dossier-builder/references/interactive-pause-script.md` | every gated wave | Four-block panel template |
