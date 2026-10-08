# Failure smell library

Read at W1 before running the raw-extract scan.

The audit catalog in `config/audit-checks.yaml` exists because of these failure classes. They are common patterns in B2B customer data; numbers are rounded illustrations of scale. Each maps to the audit check (and the wave) that catches it. When the user says "we don't have that problem here" — politely run the check anyway. The pattern they're sure they don't have is most often the one they have at scale.

Failure modes lifted from `../../dossier-builder/references/lessons-from-prior-builds.md` (lessons A-J) plus three new ones unique to the auditor's scope (K-M).

---

## A. Cross-system joins are the #1 silent killer

**The case**: A derived foreign-key field in a warehouse — an account-id column on a calls table populated by name-stem fuzzy match upstream — can be wrong on roughly 40% of records. The dossier looks plausible because every account has call activity; in reality much of it belongs to a different company.

**Smell**: a foreign-key field on a downstream warehouse table whose name suggests authority (`crm_account_id`) but whose population logic is opaque.

**Audit catches via**: `cross_system_join` family (inherited from dossier-builder) + the W3 six-verdict rubric. If you didn't compute the join yourself, treat the FK as a fallback signal, not a primary key.

---

## B. Self-domain trap

**The case**: Joining accounts to calls by external-participant email domain can falsely match hundreds of accounts to a single shared domain: your own. Your staff appear as "external participants" in call data with your company's email domain.

**Smell**: an email-domain join that doesn't first strip the tenant's own domain.

**Audit catches via**: `self_domain_trap` family (inherited) + `tenant_domain_in_customer_column` check at W2. The `tenant_domain` captured at W0 is the single guard.

---

## C. Single-key joins collide at scale

**The case**: A numeric internal customer ID used to join CRM accounts to a customer-success platform works at small scale and collides at full scale: two unrelated companies can share one legacy ID.

**Smell**: any single-key join, especially on a short numeric ID, that hasn't been deduped at the source.

**Audit catches via**: the three-test guard (W3) — require ≥2 of {ID match, domain match, name fuzzy ≥0.92}. Single-key joins that fail the guard get downgraded to INDETERMINATE.

---

## D. Derived / labeled fields lie

Three sub-shapes:

### D.1 "Do Not Use" fields used anyway

**The case**: A CRM field labeled "Do Not Use" in governance docs (a deal-value `Amount`, say) gets consumed by a revenue formula anyway, and revenue is silently overstated.

**Smell**: a field in `engagement.do_not_use_fields[]` that appears in any signal output.

**Audit catches via**: `do_not_use_demotion` rule in `config/revenue-rubric.yaml` (W4). Refuses to read flagged fields anywhere.

### D.2 Same field name on multiple objects

**The case**: A custom segment field on Account can hold different values than the field with the same name on a related subscription object. Without object-path qualification, queries silently pick whichever was scanned last.

**Smell**: a column name that appears across multiple source files / tables with different values.

**Audit catches via**: `same_field_two_objects` check (inherited paradox family). W4 column-dedupe also surfaces these as candidate concept-clusters needing canonical pick.

### D.3 Stale enrichment fields

**The case**: A lead-source value from a retired enrichment vendor can linger on a large share of records years after the switch.

**Smell**: an enrichment field whose timestamps cluster long before the data export date.

**Audit catches via**: `stale_vs_fresh_columns` check (column_dedupe family, W4). The audit recommends a `*_trust` sibling field pattern in the engagement.

---

## E. ICP cohort sanity is non-trivial

**The case**: A meaningful share of a cohort (a fifth is not unusual) may not be in the stated ICP: businesses from adjacent or unrelated trades entered under the core vertical. Cohort-level outputs (churn rates, expansion fits) are poisoned.

**Smell**: a cohort labeled by a vertical name where domain spot-checks reveal a meaningful fraction don't fit.

**Audit catches via**: `icp_cohort_multi_provider_sanity` (W6) — the Parallel Task Group of 200 records with structured ICP schema. Replaces the legacy `web_sanity_sample.py` Serper-only sample.

---

## F. Bulk imports cluster tenure

**The case**: A one-day bulk import can give about a thousand accounts the same created date, and so identical tenure. Tenure derivation that prefers `account.created_date` silently treats them all as if they signed on the same day.

**Smell**: >1% of cohort sharing identical CreatedDate.

**Audit catches via**: `bulk_import_date_cluster` (temporal family, inherited). At W1 raw scan.

---

## G. Test / fake / demo accounts pollute everything

**The case**: A single fake account can inflate reported ARR by seven figures.

**Smell**: account names containing `Fake`, `Test`, `Demo`, `Sandbox`, `QA`, `[Internal]`, or company-specific test patterns.

**Audit catches via**: `name_test_fake_demo_regex` (identity family, inherited). Block severity at W1 raw scan.

---

## H. Silent failures kill trust

**The case**: A web handler that returns HTTP 200 OK on uncaught exceptions can produce empty dossiers for days while every cron looks green.

**Smell**: a job that completes without error but produces near-empty output.

**Audit catches via**: completeness checks at W7. Hard-fail the audit if fewer than 95% of accounts have at least one timeline-relevant signal.

(Relevant to the audit only at scorecard rendering time — the audit itself doesn't suppress errors.)

---

## I. MRR shown twice with two different values

**The case**: The same dossier can display MRR in two places — one derived from ARR/12, one from the CRM — with different values.

**Smell**: multiple columns measuring the same concept appearing in the same record without explicit canonical pick.

**Audit catches via**: `multiple_columns_same_concept` (column_dedupe family, W4). Picks one canonical, lists rejected with reasons. The unit-mismatch check fires if values differ by ~1000×.

---

## J. Paradox flags catch what stats miss

**The case**: A high-MRR customer with zero calls in a year is either (a) a deal nobody talks about, (b) a Gong-linking failure, or (c) a billing-vs-CRM truth divergence. None are good.

**Smell**: any pair of fields whose values together imply a contradiction that single-field checks don't see.

**Audit catches via**: `cross_field_paradox` family (W6, new). Includes:
- `employee_range_revenue_implausible` (the user's "11-50 emp + $1M rev" example)
- `industry_revenue_implausible` (single-clinic FQHC reporting $500M)
- `geo_phone_mismatch` (HQ in CA but all phones are 212)
- `healthcare_npi_revenue_paradox` (Medicare payments but no NPI)

Per-record paradox resolution at W6 uses Parallel Task Core for cited verdicts.

---

## K. (Auditor-specific) Multi-domain pollution

**The pattern**: a dozen rows have "Globex" as the account name with 5 different domains (`globex.example`, `globex-health.example`, `myglobex.example`, `globexhealth.example`, `globex-net.example`). Some are subsidiaries; some are typos; some are vanity-redirected. Without resolution, a customer success workflow treats them as a dozen distinct customers.

**Smell**: ≥3 distinct domains mapping to the same canonical company.

**Audit catches via**: `multiple_domains_resolve_to_same_company` (W2). Uses Blitz `domain-to-linkedin` to detect.

---

## L. (Auditor-specific) Provider single-source bias

**The pattern**: A vendor CSV classifies every account as "SaaS." A spot-check of 50 via a few web sources shows a sizeable minority are consulting or e-commerce firms. The data was provider-poisoned.

**Smell**: 100% confidence on industry/vertical/ICP classification from a single source.

**Audit catches via**: `domain_company_provider_disagreement` (W2, new multi_provider family). Triangulation, not single-source.

---

## M. (Auditor-specific) Free-text revenue parsing

**The pattern**: A free-text column called `notes` contains entries like "$1.2M ARR, signed 2024." A revenue audit that ignores this column undercounts canonical revenue for accounts where the subscription system is empty.

**Smell**: a `free_text` column whose values mention dollar amounts or recurring-revenue language.

**Audit catches via**: `revenue-agent` (W4) optionally invokes Groq to extract structured `{amount, unit, frequency}` from any free_text column. Surfaces as a candidate revenue source for the canonical-pick rubric.

---

## What this means for the scan-agent

W1 doesn't run any of these checks itself. It just inventories files + classifies columns + surfaces join candidates. But the smells listed above tell scan-agent what to *look for* in its classification:

- Long-tail unclassified columns → flag for triage (could hide D.2 or M).
- Files with very recent mtime + identical row counts → possible bulk export (could hide F).
- Account-name columns where the same value appears with multiple different IDs → flag (could hide K).
- Free-text columns mentioning currency tokens → flag for revenue-agent's free-text pass (M).

The scan output `scan/files_inventory.json` includes these flags so downstream waves know where to look.
