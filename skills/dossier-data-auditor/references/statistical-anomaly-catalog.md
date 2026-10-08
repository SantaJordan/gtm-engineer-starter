# Statistical anomaly catalog (W4 + W5)

> Loaded at W4 (revenue reconciliation needs Benford + round-cluster + Apple-at-$20) and W5 (column fitness pentad needs the rest). The 18 patterns defined here are also encoded as machine-readable thresholds in `config/audit-checks.yaml`.

## The principle

Fill rate alone is not signal. A column at 95% fill rate looks healthy until you discover 1,000 of those values are an identical backfilled placeholder. Cardinality, distribution shape, modal-value spike, write-recency clustering, and NULL-encoding diversity all matter. This catalog formalizes those into testable patterns.

Every pattern below is a Python computation (deterministic), surfaced to an Opus sub-agent only for verdict interpretation when triggered.

---

## 1. `benfords_law_revenue` (W4)

**Pattern**: first-digit distribution of revenue values deviates from Benford's law.

**Test**: χ² goodness-of-fit on the observed first-digit frequencies (1-9) against Benford expected (`log10(1 + 1/d)`).

**Threshold**: χ² p < 0.01 with min 500 rows.

**What it catches**: synthetic / placeholder / hand-typed revenue. Real revenue from real businesses follows Benford; placeholder values like `$10000`, `$50000` clustered together don't.

---

## 2. `modal_value_spike_any_column` (W5)

**Pattern**: top-1 value frequency > 5% of populated rows AND > 50 rows share it.

**Test**: `top1_count / populated_count`.

**What it catches**: the 1,000-identical-tenure pattern in any column. Bulk-import fingerprint.

---

## 3. `bimodal_distribution_test` (W5)

**Pattern**: numeric column is bimodal.

**Test**: Hartigan dip test, p < 0.05.

**What it catches**: accidentally-merged source files with different scale conventions (one file in cents, another in dollars; one in millions, another in thousands).

---

## 4. `column_pair_perfect_correlation` (W5)

**Pattern**: Spearman ρ > 0.99 between any two numeric columns in same file (excluding ID-pairs).

**What it catches**: derived columns silently encoding the same thing. One of them is "the data" and the other is "a stale copy" — usually the former gets updated, the latter doesn't.

---

## 5. `column_pair_perfect_anticorrelation` (W5)

**Pattern**: Spearman ρ < −0.99 between any two numeric/boolean columns.

**What it catches**: mirror columns like `is_active` and `is_churned` where downstream code only reads one and the other goes stale.

---

## 6. `cardinality_vs_uniqueness_mismatch` (W5)

**Pattern**: column declared as `id_*` has cardinality / row-count < 0.5 OR column declared as enum-like has > 50 unique values.

**What it catches**: schema-vs-reality drift on column semantics. An "ID" column with cardinality 0.3 isn't an ID. An "Industry" enum with 47 distinct values is free-text.

---

## 7. `value_length_distribution_truncation` (W5)

**Pattern**: > 1% of values in a `VARCHAR(N)` column have length exactly N.

**What it catches**: silent Excel/DB truncation. The destination column was set to varchar(100) and 50 values are exactly 100 chars — those were originally longer and got chopped.

---

## 8. `scientific_notation_in_id_columns` (W5)

**Pattern**: any value matching `\d+E[+-]\d+` in a column where other values look like IDs.

**What it catches**: Excel-roundtrip-destroyed IDs. A 17-digit Salesforce ID opened in Excel and saved gets cast to `1.234567890123456E+19`, losing the last 4 digits. This single pattern catches the most common cross-system join failure that goes silent forever.

**Severity**: `block` — single-most-load-bearing detection.

---

## 9. `mixed_date_format_in_column` (W5)

**Pattern**: same column has values parseable as ISO-8601 AND values parseable as MM/DD/YYYY but not ISO.

**What it catches**: DD-vs-MM ambiguity. Mismaps half your dates silently. `01/02/2024` could be Jan 2 or Feb 1.

---

## 10. `epoch_or_sentinel_dates` (W5)

**Pattern**: date column has > 0.1% values at `1970-01-01`, `9999-12-31`, `2099-12-31`, `1900-01-01`.

**What it catches**: sentinel-as-real-data. Code that does `if date_field: ...` will treat these as real timestamps. Cohort by these and you get the "all our customers were created on the same day" failure.

---

## 11. `currency_mixing` (W5)

**Pattern**: numeric column has both `1234.56`-style and `1,234.56`-style and `$1234.56`-style and `€...` and `£...` values.

**What it catches**: multi-locale CSV concatenation. Code that does `float(x)` will throw on some rows and silently truncate on others.

---

## 12. `null_encoding_diversity` (W5)

**Pattern**: same column has > 1 of `NULL` / `''` / `'N/A'` / `'Unknown'` / `0` / `-` / `'TBD'` / `n/a` / `NaN`.

**What it catches**: NULL-semantic ambiguity. Canonical-pick rubric picks the "fuller" column but it's actually full of `'N/A'`-string placeholders.

---

## 13. `bulk_write_signature_per_column` (W5)

**Pattern**: any column where > 5% of populated rows share identical `LastModifiedDate` to the second.

**What it catches**: backfilled-and-dead column. Last meaningful change was a one-time backfill years ago; the column hasn't been written to since. Fill rate alone says it's healthy at 95%; this metric says it's dead.

**Severity**: `warn` — verdict in `column_fitness_verdicts.json` goes to `dead` regardless of fill rate.

---

## 14. `intra_source_dedup_likelihood` (W6)

**Pattern**: within one source, any pair of rows where (name fuzzy ≥ 0.92 AND domain match AND non-overlapping non-null fields).

**What it catches**: Apple-as-47-records within one system. Same conceptual entity duplicated under variants like "Apple" and "Apple Inc.", each with different fields populated (one has MRR, another has phone, another has industry).

---

## 15. `cohort_count_vs_user_expectation` (W6)

**Pattern**: actual loaded count differs from user's stated expectation at W0 by > 10%.

**What it catches**: "we said 12,000 customers but we loaded 47,000" — usually means the cohort is contaminated with prospects, lost-opps, or test data.

---

## 16. `domain_field_pairwise_agreement` (W2)

**Pattern**: across URL-like columns (`Website`, `homepage_url`, `domain`, `email-inferred-domain`, `linkedin_url`, `additional_domains`), ≥ 5% of accounts have ≥ 2 different eTLD+1 canonical roots.

**What it catches**: the W2 raw-primitive problem. Drives multi-provider verification scope (W2b only runs on disagreement records).

---

## 17. `multi_tenant_domain_overuse` (W3 Hat 3 + W2)

**Pattern**: any "company domain" appearing on > 5 distinct accounts in the customer base.

**What it catches**: consulting firms (accenture.com, deloitte.com, capgemini.com), platform-tenant domains, freemail-as-canonical patterns. Domains that fail this check cannot be used as join keys — flag in `multi_tenant_join_blocklist`.

---

## 18. `hierarchy_self_parent` (W5.5)

**Pattern**: any row where `ParentId = Id`.

**What it catches**: botched data loads. 5-15% of accounts in a typical SFDC org have this from a bad migration. Breaks recursive rollup queries.

---

## How the catalog is used

1. **W4 — revenue reconciliation** runs patterns 1, 5 (against revenue clusters), and Apple-at-$20 (in `revenue-rubric.yaml`).
2. **W5 — column fitness pentad** runs patterns 2-13 over every column the W1 scan classified.
3. **W6 — sanity wave** runs patterns 14, 15 plus the 5 cross-field paradox families from `audit-checks.yaml`.
4. **W2 — domain audit** runs pattern 16 to drive W2b scope.
5. **W3 Hat 3 self-domain-guard** runs pattern 17 against the join proposal set.
6. **W5.5 — hierarchy resolver** runs pattern 18.

Every triggered pattern → entry in `audit_findings.json` with `pattern_id`, threshold, observed value, sample evidence rows, severity. The sub-agent reading the metrics interprets the verdict for the panel; the metrics themselves are not negotiable.
