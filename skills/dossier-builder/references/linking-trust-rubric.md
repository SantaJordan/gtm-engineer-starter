# Linking trust rubric

The audit logic the identity-linking agent runs at W2b. Read every time before spawning that agent.

## Six verdicts (mutually exclusive)

For every record proposing a link from a source system to the canonical primary-id-system account, the agent emits exactly one verdict:

| Verdict | When | What it means |
|---|---|---|
| `AUTH_KEEP` | source's own native foreign key matches a known account, AND email-domain check (where applicable) doesn't contradict | Trust this link. Highest confidence. |
| `AUTH_REJECT` | source's own native foreign key points to an account ID that doesn't exist in our primary-id-system | The link is broken at the source. Drop the record. |
| `AUTH_EMAIL_CONFLICT` | source's foreign key matches a known account, BUT the participant/contact email domains don't overlap that account's contact list | The source's own integration is wrong. Drop the record. |
| `EMAIL_KEEP` | no source foreign key, but external email domain (after tenant-domain strip) overlaps exactly one account's contact-domain set, AND 3-test guard passes | Fallback link. Medium confidence. |
| `EMAIL_REJECT` | no source foreign key, and email-domain match either fails or matches multiple accounts ambiguously | No reliable basis. Drop. |
| `INDETERMINATE` | no source foreign key AND no useful email evidence (e.g., phone-only call) | Keep the record but don't link it. Surfaces in the dossier as unlinked context. |

A seventh sentinel `NOT_IN_SOURCE` is emitted when a record was expected (e.g., listed in a stale cache) but the source system returns 404. These are dropped silently — they're not link failures, they're retention deletions.

## Three-test guard for every single-key join

When a join is proposed by exactly one signal (just an ID, just a domain, just a phone), require **at least 2 of 3** of the following to agree before promoting verdict to `AUTH_KEEP` or `EMAIL_KEEP`:

1. **ID match** — exact match on a stable primary key
2. **Domain match** — at least one external (non-tenant-domain) email or website domain in the record matches a domain in the candidate account's contact list
3. **Name fuzzy ≥ 0.92** — Jaccard similarity (token-stripped, with corporate stopwords `inc|llc|llp|pc|pa|pllc|ltd|lp|corp|company|co|the` removed) of the source-side name vs the account name

If only 1 of 3 agrees, downgrade to `INDETERMINATE` and surface in the four-block panel.

## Self-domain strip

Before any email-domain match runs:

```
tenant_domain = engagement.tenant_domain  # e.g. "contoso.example"
external_domains = [d for d in record.external_email_domains if d != tenant_domain]
if not external_domains:
    return INDETERMINATE  # nothing to match on
```

This is the single most important defensive rule in the agent. It eliminates self-domain-false-match-class bugs.

## Aggregate `linking_trust`

Computed at the per-account level from the verdict distribution:

| Aggregate | Rule |
|---|---|
| `high` | ≤ 25% of records have suspicious verdicts (`AUTH_EMAIL_CONFLICT`, `AUTH_REJECT`, `EMAIL_REJECT`) |
| `medium` | 25-50% suspicious |
| `low` | ≥ 50% suspicious |
| `none` | account has no link-eligible records |
| `indeterminate` | account has records but all are `INDETERMINATE` |

Every dossier carries this. Downstream consumers should scope by it — e.g., a churn analysis should exclude `low` and `indeterminate` accounts or caveat conclusions.

## Random spot-check (mandatory)

Before completing W2b, the agent renders a 100-record random sample to the human in the four-block panel. The human eyeballs them and either:

- confirms the verdicts wholesale (one-shot tiebreaker)
- flags individual records that look wrong (per-record tiebreakers, persisted to `notes_from_revops[]`)

This step is non-skippable. The audit is not the same as human verification — it's a defensible best-effort. The human review is the warrant.

## Targets

- `fuzzy_join_fraction ≤ 0.20` (i.e., ≥80% of joins are ID-based, not fuzzy/email/name). If fuzzy fraction > 20%, the linking is fragile and the dossier should ship with `linking_trust ≤ medium` even if individual records look OK.
- `tenant_domain_strip.false_matches_avoided > 0` is informational (interesting to log) but not required.

## What to do when the rubric blocks ALL records for an account

If every record on an account is `AUTH_REJECT` / `EMAIL_REJECT` / `INDETERMINATE`, the account ships with `linking_trust = "none"` and an empty timeline. Don't fabricate. Surface in the four-block panel — the human may know that this account legitimately has no engagement (a closed-lost prospect, a paid-but-never-onboarded customer) and can confirm.
