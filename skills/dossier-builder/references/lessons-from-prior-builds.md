# Lessons from prior builds

A catalog of common dossier failure patterns. Read at W1b before running the raw-extract DQ pre-flight. Each lesson is a category, not a one-off: these recur because the underlying data systems behave the same way at most B2B SaaS companies. Numbers below are rounded illustrations of scale.

## A. Cross-system joins are the #1 silent killer

A derived foreign-key field in a downstream warehouse — for example, an account-id column on a calls table populated by a name-stem fuzzy match upstream — can be wrong on **roughly 40% of records**. The dossier still looks plausible because every account has call activity; in reality much of that activity belongs to a different company.

**Pattern**: an LLM-readable field name doesn't make the field correct. If you didn't compute the join yourself with auditable rules, treat it as a fallback signal, not a primary key. Always keep the source's own native foreign key (e.g., the field the call recorder itself populates from its CRM integration) as the authority and demote the warehouse-derived join to "use only when authority is missing".

## B. Self-domain trap

Joining accounts to calls by external-participant email domain can falsely match **hundreds of accounts** to a single shared domain: your own. When your staff appear as "external participants" in call data, an email-domain join matches them to every account that has a contact at your domain.

**Pattern**: never email-domain-join without first stripping your own tenant domain. This single rule eliminates a class of bug that is invisible at small scale and catastrophic at large scale. `engagement.tenant_domain` is in the engagement file specifically to make this rule enforceable globally.

## C. Single-key joins collide at scale

A numeric internal customer ID used to join CRM accounts to a customer-success platform works at small scale and collides at full scale: two unrelated companies in different states can share one legacy ID. A three-test guard (require ≥2 of {ID match, domain match, name fuzzy ≥0.92}) catches this.

**Pattern**: any single-key join that's older than the deduping discipline of its source system will collide. Apply the 3-test guard.

## D. Derived/labeled fields lie

Three persistent shapes:

1. **"Do Not Use" fields used anyway**. A CRM field labeled "Do Not Use" in governance docs (a deal-value `Amount`, say) gets consumed by a revenue formula anyway, and revenue is silently overstated. Fix: maintain `do_not_use_fields[]` in `engagement.json`, refuse to read those fields anywhere in the pipeline.
2. **Same field name on multiple objects**. A custom segment field on Account can hold different values than the field with the same name on a related subscription object. Without object-path qualification, queries silently pick whichever was scanned last.
3. **Stale enrichment fields**. A lead-source value from a retired enrichment vendor can linger on a large share of records years after the switch. Fix: every potentially-stale field gets a `*_trust` sibling in the schema (e.g. `lead_source_trust ∈ {trusted, junk_triangulated, suspect_vendor, unknown}`).

## E. ICP cohort sanity is non-trivial

A meaningful share of a cohort (a fifth is not unusual) may not be in the stated ICP at all: businesses from adjacent or unrelated trades that were entered under the core vertical. The dossier has no way to detect this without web research per account, and cohort-level outputs (churn rates, expansion fits) are poisoned by the misclassification.

**Pattern**: build the W3c 200-record web sample into every engagement. The check is "does the public web confirm this account is in the stated ICP?" — domain resolves, name on website matches, vertical signals present.

## F. Bulk imports cluster tenure

A one-day bulk import can give **about a thousand accounts the same created date**, and so identical tenure. Tenure derivation that prefers `account.created_date` will silently treat them all as if they signed on the same day. Fix: tenure-source cascade preferring `opportunity_won.close_date` first; tag accounts with `tenure_source='bulk_import_uncertain'` when only `account.created_date` is available; histogram-flag any cohort where >5% share identical values.

## G. Test/fake/demo accounts pollute everything

A single fake account can inflate reported ARR by **seven figures**. Names containing `Fake`, `Test CPQ`, `Demo`, `Sandbox`, or matching company-specific test patterns must be excluded at the pull stage, not the analysis stage. Pulling them and filtering downstream creates a long tail of forgotten queries that include them.

## H. Silent failures kill trust

A web handler that returns HTTP 200 OK on uncaught exceptions can produce empty dossiers for days while every cron looks green. Fix: every job exits non-zero on any unhandled error; completeness checks (e.g. ≥95% of accounts had at least one timeline event added) hard-fail the pipeline rather than allowing partial data through.

## I. MRR shown twice with two different values

The same dossier can display MRR in two places — one derived from ARR/12, one from the CRM — with different values. Whichever the reader looks at first sets their mental model. Fix: pick exactly one canonical source per field (W3a's job), explicitly list `rejected_sources` with reasons, and never display two un-reconciled numbers for the same concept.

## J. Paradox flags catch what stats miss

A high-MRR customer with zero calls in a year is either (a) a deal nobody talks about, (b) a call-linking failure, or (c) a billing-vs-CRM truth divergence. None are good. The PARADOX flag exists for cross-source contradictions — `mrr_pos_no_calls_180d`, `customer_but_cancelled_in_billing`, `cross_source_mrr_mismatch`, `name_test_fake_demo_regex`. Every dossier carries `flags[]`; downstream consumers can scope by absence/presence.

## What this means for the DQ triage agent

Every check in `config/dq-checks.yaml` traces back to one of these lessons. When the RevOps person on the call says "we don't have that problem here" — politely run the check anyway. The pattern that they're sure they don't have is most often the one they have at scale.

## Lesson: correct critics, unreadable corpus

A large dossier build can pass the critique trio — every aggregate reproduced exactly — and still be unreadable. Typical causes: most timeline events are near-identical $0 rows (a billing system that rewrites contract lines on every change), event subjects are internal codes, calls are filed under source "other" with empty-voicemail sentinel strings as display text, and contact details are marked present but never emitted. A blind-retell survey by a few independent readers surfaces these; correctness critics do not.

Encoded response: the W5 `critique-reader` (08d) — blind retell BEFORE critique, noise-share
measurement, unanswerable-question count, render-fidelity check, and a re-run-on-same-sample
before/after gate. The general law: **verifying the data is right is not verifying the artifact
is legible; every corpus needs at least one critic who reads it the way its consumer will.**
