# Join audit (W3)

Read at the start of W3. The single most important wave — get this wrong and the entire downstream dossier is wrong.

## Source rubric

This wave's logic is borrowed verbatim from `../../dossier-builder/references/linking-trust-rubric.md`. Read that file directly — every rule in it applies here unchanged. Summary of the rubric reproduced below for context; the canonical version is the dossier-builder file.

## Six verdicts (mutually exclusive)

For every record proposing a link from a source file to a canonical primary-id-system account:

| Verdict | When | Action |
|---|---|---|
| `AUTH_KEEP` | Source's own FK matches a known account AND email-domain check (where applicable) doesn't contradict | Trust the link. Highest confidence. |
| `AUTH_REJECT` | Source's FK points to an account ID that doesn't exist | Drop the record. |
| `AUTH_EMAIL_CONFLICT` | Source's FK matches a known account BUT the participant/contact email domains don't overlap that account's contact list | Drop the record — source's own integration is wrong. (This is the ~40%-wrong derived-FK case.) |
| `EMAIL_KEEP` | No FK, but external email domain (after tenant-domain strip) overlaps exactly one account's contact-domain set, AND three-test guard passes | Fallback link. Medium confidence. |
| `EMAIL_REJECT` | No FK, and email-domain match fails or matches multiple accounts ambiguously | Drop. |
| `INDETERMINATE` | No FK, no useful email evidence (phone-only call, e.g.) | Keep record, don't link. Surfaces in dossier as unlinked context. |

A seventh sentinel `NOT_IN_SOURCE` is emitted when a record was expected (stale cache) but the source returns 404. Silently dropped — they're retention deletions, not link failures.

## Three-test guard

When a join is proposed by exactly one signal (just an ID, just a domain, just a phone), **require ≥2 of 3 to agree** before promoting verdict to `AUTH_KEEP` or `EMAIL_KEEP`:

1. **ID match** — exact match on a stable primary key.
2. **Domain match** — at least one external (non-tenant-domain) email or website domain in the record matches a domain in the candidate account's contact list.
3. **Name fuzzy ≥ 0.92** — Jaccard similarity (token-stripped, corporate stopwords `inc|llc|llp|pc|pa|pllc|ltd|lp|corp|company|co|the` removed) of source-side name vs account name.

If only 1 of 3 agrees: downgrade to `INDETERMINATE`. Surface in the W3 panel.

## Self-domain strip (mandatory)

Before any email-domain match runs:

```python
tenant_domain = audit_context.tenant_domain  # captured at W0
external_domains = [d for d in record.external_email_domains if d != tenant_domain]
if not external_domains:
    return INDETERMINATE  # nothing to match on
```

This eliminates the self-domain false-match class. The `tenant_domain` field in `audit_context.json` is the only place the customer's own domain lives — every join check must consult it.

If `tenant_domain` is not set at W0, the audit BLOCKS at W3 with: "Set tenant_domain at W0 before running join inference. Required to prevent self-domain-trap false matches."

## Key cardinality (mandatory)

Coverage alone can pass a join that doubles revenue. `scripts/join_test.py` also
checks whether the key repeats:

- **A table's own ID repeats** (for example `accounts.csv` lists an `account_id`
  twice): every row on the other side joins to two rows, so amounts are summed
  twice. Verdict `DUPLICATE_KEYS_MULTIPLY_ROWS`, `linking_trust = low`, gate BLOCK.
  Fix the duplicate rows (or pick the authoritative one) before building.
- **Two sibling tables share a parent's key** (`contacts.account_id` and
  `invoices.account_id`, both repeating): not a direct join. Verdict
  `SIBLING_TABLES_SHARE_A_KEY`, `linking_trust = not_a_direct_join`, a warning;
  join each one through the parent table instead.
- Each finding carries `joined_row_count` and rows-per-row on each side, so the
  multiplication is visible.

## Aggregate `linking_trust` per account

| Aggregate | Rule |
|---|---|
| `high` | ≤25% of records have suspicious verdicts (`AUTH_EMAIL_CONFLICT`, `AUTH_REJECT`, `EMAIL_REJECT`) |
| `medium` | 25–50% suspicious |
| `low` | ≥50% suspicious |
| `none` | account has no link-eligible records |
| `indeterminate` | account has records but all are INDETERMINATE |

Every audited account carries this. The recommended_engagement_overrides.json includes per-account trust grades for dossier-builder W0 ingestion.

## Random spot-check (mandatory)

Before completing W3, render a 100-record random sample to the user in the four-block panel. The user either confirms verdicts wholesale or flags individual records.

This step is non-skippable. The audit is a best-effort algorithmic verdict; the human review is the warrant.

## Targets

- `fuzzy_join_fraction ≤ 0.20` — i.e., ≥80% of joins are ID-based, not fuzzy/email/name. If fuzzy fraction > 20%, the linking is fragile and dossiers built downstream ship with `linking_trust ≤ medium` even if individual records look OK.
- `tenant_domain_strip.false_matches_avoided > 0` — informational. Interesting to log.

## Hard refusal: blocked joins require written user override

If any join the user wants to keep failed the three-test guard, the audit BLOCKS. The user must answer the W3 question with an explicit override + written reason:

```
Q1. Joining customers.csv to calls.parquet on sf_account_id — 40% verdict=AUTH_EMAIL_CONFLICT (invented example).
    Reject this join (recommended) or override?

User: override
Audit: A written reason is required for this override. The override + reason will
       persist into notes_from_user[] as the audit trail for why we proceeded
       despite the failed guard.

User: We're testing whether a new CRM sync fixed the FK problem. If it
       didn't, we'll catch it in W5 spot-check.
Audit: Recorded. Override applied. Proceeding to W4 with `linking_trust=low`
       on the affected accounts.
```

Silent overrides are impossible. The audit trail is the load-bearing artifact for "how did we decide this?" months later.

## What goes into the recommended_engagement_overrides

```json
{
  "primary_id_system": "sfdc",            // single canonical join key system
  "do_not_use_joins": [
    {
      "left": "calls.parquet",
      "right": "customers.csv",
      "on": "sf_account_id",
      "reason": "40% AUTH_EMAIL_CONFLICT"
    }
  ],
  "fuzzy_join_fraction": 0.18,            // for dossier-builder's W3a context
  "tenant_domain_strip": {
    "applied": true,
    "false_matches_avoided": 250
  },
  "per_account_linking_trust": {
    "001XX00000Td..": "high",
    "001XX00000Ue..": "medium",
    ...
  }
}
```

## What this is NOT

- NOT a fix for the underlying source-system FK problem. The audit detects and refuses bad joins; it can't repair them.
- NOT a replacement for `dossier-builder`'s W2b identity-linking. The auditor's W3 is a pre-flight that catches the broad classes; the builder's W2b runs the full per-account identity-linking with the human in the loop.
