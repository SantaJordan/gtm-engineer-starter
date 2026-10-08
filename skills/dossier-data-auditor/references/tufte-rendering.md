# Tufte terminal rendering

Read at every panel render. Read once at W0 to internalize.

Every company, file, number and date in the example panels below is invented for illustration.

## Principle

Edward Tufte's data-ink rule: every element of a display must earn its place by carrying information. No decoration. No "visual breathing room" that isn't load-bearing.

For the terminal: fixed-width tables, right-aligned numbers with units, group separators with `---`, no Unicode box-drawing, no emojis. The exemplar is a plain cost-estimate block: one line per item, right-aligned dollars, a total. Mimic that shape.

## The pattern

```
=== W{N}: {Wave name} complete ===

HOW I DID IT
  • {Bullet, terse, specific, with numbers.}
  • {3-7 bullets max. Cite verbatim — "stripped 250 false matches", not "many false matches".}

DOMAIN TRUST DISTRIBUTION                  (or whatever section title is relevant)
  Verdict                  Count    Pct      Cost
  verified                46,000   92.0%      —
  unresolved (DNS fail)    2,800    5.6%      —
  parked / for sale          100    0.2%      —
  mismatched (providers)     100    0.2%    $0.30
  multi-collapse merged    1,000    2.0%    $2.10
  ---
                          50,000  100.0%    $2.40 total

OUTLIERS I'M FLAGGING
  Row ID             Domain                       Reason
  001XX000003abc..   roofing-deals.example            parked: 'this domain is for sale'
  001XX000003def..   yourcompany.com              generic placeholder — never legit
  001XX000003ghi..   bit.ly/abc123                URL shortener — not a domain
  ... +19 more, see audit/domain_findings.json

QUESTIONS I NEED YOU TO ANSWER
  Q1. {Specific question, options offered, recommendation marked.}
  Q2. {...}

WHAT I'LL DO NEXT
  {1-2 sentences. Next wave + what gates on the user's answers.}
```

## Rules

### Fixed-width

Always use spaces, not tabs. Choose a width per column that fits the widest expected value. Right-align numeric columns. Left-align text columns. No center-alignment.

### Truncation

- Domains > 24 chars: show `<first-22-chars>..`
- Row IDs > 12 chars: show `<first-10>..` (Salesforce IDs become `001XX00000..`)
- Account names > 18 chars: show `<first-token>..` (e.g., `Globex` instead of `Globex Health Group Inc`)
- The truncation `..` (two dots) is the standard ellipsis. Never use `…` (Unicode).
- Full values always available in the JSON output for inspection.

### Grouping

Section separators inside a table: a row of `---` matching the table width. Section separators between panel blocks: blank lines, not `===`.

The `===` framing line goes at the top and bottom of the entire panel only.

### Currency

`$1,200` not `1200.00`. `$48K` not `48000`. `$1.2M` for figures > $1M. Always right-aligned. Always include the `$` per cell, not just in a column header — easier to scan.

### Percentages

`92.0%` not `0.92`. Always 1 decimal place unless the value is exactly 0% or 100%.

### Counts

Comma-separated when >999: `50,000` not `50000`.

### No emojis

The renderer scans every line and rejects any character outside the printable ASCII range plus `…`-class punctuation (we ban `…` specifically; use `..`). No `✓`, no `✗`, no `→`, no `🔴`. Replace with words: `pass`, `fail`, `→` becomes `->`, severity gets caps: `BLOCK`, `WARN`, `NOTE`.

### Severity tags

When a row needs severity: leftmost column, capital letters, fixed-width 5 chars:

```
BLOCK   001XX000003abc..   roofing-deals.example       parked: 'for sale'
WARN    001XX000003def..   yourcompany.com         generic placeholder
NOTE    001XX000003ghi..   bit.ly/abc123           URL shortener
```

## The four canonical panels

### Panel 1: W1 scan-result

```
=== W1: Auto-discovery complete ===

HOW I DID IT
  • Walked ~/data/acme/00_raw/
  • Classified 47 files across 9 subdirectories.
  • Sniffed 300 columns; 90 unclassified.
  • Tested 1,400 candidate join pairs; 23 had inner-join ≥0.50.

FILES INVENTORY
  Path                                            Rows      Cols    MTime
  00_raw/crm/accounts_export.csv                12,000    34      <export date>
  00_raw/billing/invoices_2025.xlsx               4,000     28      <export date>
  00_raw/support/tickets.csv                      9,000     22      <export date>
  00_raw/contracts/contracts_index.xlsx           1,000     19      <export date>
  ... +43 more, see scan/files_inventory.json

COLUMN CLASSIFICATION
  Class              Count   Example header               Example value
  domain_like         11     Website                      acme.example
  company_id_like      8     Account ID                   001XX00000Td..
  revenue_like        24     Total Revenue / MRR / ARR    1,200,000
  company_name_like   16     Account Name                 Acme Roofing..
  employee_like        4     Employee Range               11-50
  industry_like        9     Industry / Vertical          Home Services
  email_like          18     Email / Contact Email        contact@acme..
  unclassified        89     (varies)                     (varies)

TOP JOIN CANDIDATES
  Left file                       Right file                On column           Inner %
  accounts_export.csv             invoices_2025.xlsx        account_id          94.0%
  tickets.csv                     accounts_export.csv       account_name(fuzzy) 71.0%
  contracts_index.xlsx            accounts_export.csv       customer_name       58.0%
  ... +20 more, see scan/join_candidates.json

OUTLIERS I'M FLAGGING
  • 89 unclassified columns. ~30 are date/time fields with non-standard names, ~50
    are free-text notes, 9 are genuinely ambiguous — need user input.
  • 4 files use 'Account ID' but values are 8-char numeric — not SFDC format.
    Possibly NetSuite. Confirm at W3.

QUESTIONS I NEED YOU TO ANSWER
  Q1. Skip the 89 unclassified columns or pause to triage them?
      Recommend: skip; triage is W3 territory.
  Q2. The 8-char numeric IDs in 4 files — what system?

WHAT I'LL DO NEXT
  Continue to W2 (domain audit) on the 11 domain_like columns,
  gated on Q2 only.
```

### Panel 2: W2 domain-audit

```
=== W2: Domain audit complete ===

HOW I DID IT
  • Canonicalized 50,000 domains across 11 columns.
  • DNS-resolved all (free).
  • Multi-provider spot-check on 50 random: Serper + Exa + Firecrawl + Linkup
    in parallel; Parallel Search as 4th-opinion tiebreaker on 7 disputes.
  • Detected parked-page fingerprints on 23 domains via Firecrawl HTML scan.
  • Clustered multi-domain-to-one-company via Blitz domain-to-LinkedIn.

DOMAIN TRUST DISTRIBUTION
  Verdict                    Count    Pct      Cost spent
  verified                  46,000   92.0%      —
  unresolved (DNS fail)      2,800    5.6%      —
  parked / for sale            100    0.2%      —
  mismatched (providers)       100    0.2%    $0.30
  multi-collapse merged      1,000    2.0%    $2.10
  ---
                            50,000  100.0%    $2.40 total

PROVIDER AGREEMENT ON SPOT-CHECK (50 random)
  All 4 agree                           41    82.0%
  3 of 4 agree                           7    14.0%
  Split decision (tiebreaker fired)      2     4.0%
  ---
                                         50   100.0%

WORST OFFENDERS
  Row ID             Domain                     Reason
  001XX000003abc..   roofing-deals.example          parked: 'this domain is for sale'
  001XX000003def..   yourcompany.com            generic placeholder — never legit
  001XX000003ghi..   bit.ly/abc123              URL shortener — not a company domain
  001XX000003jkl..   globex.example                  collapses w/ 18 other rows to Globex
  ... +19 more, see audit/domain_findings.json

QUESTIONS I NEED YOU TO ANSWER
  Q1. 2,800 unresolved domains (5.6%). Drop, enrich via Blitz (~$2.80),
      or keep with linking_trust capped at low?
      Recommend: enrich.
  Q2. 100 parked domains. Block from the build (recommended) or override?
  Q3. 18 'Globex' subsidiaries collapse to globex.example — collapse or keep separate?

WHAT I'LL DO NEXT
  Continue to W3 (join inference), gated on Q1-Q3.
```

### Panel 3: W4 revenue-reconciliation

```
=== W4: Revenue reconciliation complete ===

HOW I DID IT
  • Clustered 24 revenue_like columns into 3 concept groups: MRR, ARR, deal_amount.
  • Per cluster: fill, p99/median, round-cluster %, recency, source reputation.
  • Applied canonical-pick rubric from config/revenue-rubric.yaml.

CLUSTER: probable_MRR  (6 columns)
  Column                                Fill%   Median   p99     Round%   Recency       Pick
  SBQQ.Subscription.Amount__c           98%     $1,200   $50K     3%     <this month>  canonical
  Account.MRR__c                        85%     $1,200   $45K     8%     <this month>  reject (lower fill)
  monthly_revenue (CSV export)          70%     $1,250   $50K    25%     <6 mo ago>    reject (round cluster, stale)
  mrr_v2 (CSV export)                   40%     $1,000   $50K    20%     <6 mo ago>    reject (low fill, stale)
  csm_mrr                               70%     $1,200   $45K     5%     <last month>  reject (CSM < billing)
  legacy_mrr (do_not_use list)          90%       $850   $40K     2%     <2 yrs ago>   reject (do_not_use)

  Conflicts beyond 10% threshold:
  • 400 records (5% of cohort) where SBQQ and Account.MRR__c disagree by >10%.
    SBQQ is canonical. Account.MRR__c values rejected w/ reason="canonical_disagreement".
  • 2,000 records (20%) where SBQQ and legacy_mrr disagree.
    legacy_mrr already on do_not_use — rejected by policy.

OUTLIERS I'M FLAGGING
  • Initech Inc shows $20/mo in monthly_revenue and $45,000/mo in SBQQ.
    Classic Apple-at-$20 join error in the CSV export — drop that column.

QUESTIONS I NEED YOU TO ANSWER
  Q1. Confirm SBQQ.Subscription.Amount__c as canonical MRR?  Recommend: yes.
  Q2. monthly_revenue and mrr_v2 came from a CSV export. Discard or retain as
      historical signals only?

WHAT I'LL DO NEXT
  Continue to W5 (fill-rate cohort fitness), gated on Q1.
```

### Panel 4: W7 final scorecard

```
=== AUDIT SCORECARD: acme-2026-05 ===

DATA DIR    ~/data/acme/00_raw/
FILES       47          ROWS      300,000        COLUMNS     300        AUDIT TIME    7m
COST        $5.00       BUDGET    $20.00         REMAINING   $15.00

DIMENSION                                                    GRADE        BLOCKING?
W2  Domain audit                                             B+           no
W3  Join inference (3 joins tested, 2 high, 1 blocked)       D            YES (1 blocked)
W4  Revenue reconciliation (3 clusters, canonical picked)    A-           no
W5  Fill-rate cohort fitness (8 columns below 80%)           B            no
W6  Cross-field paradox (12 rows flagged)                    A            no

OVERALL                                                      C+           BLOCKED at W3

GO / NO-GO FOR dossier-builder PRE-BUILD HANDOFF:    NO-GO

Reason: The proposed join client_sales.xlsx <-> calls.parquet on sf_account_id
failed the three-test guard (40% AUTH_EMAIL_CONFLICT). Override required to
proceed.

NEXT STEPS
  1. Resolve W3-Q1 (override the bad join, or remove calls.parquet from sources).
  2. Re-run the audit from W3 (ask the skill: "resume the acme-2026-05 audit from W3")
  3. When go/no-go is GO: feed audit/recommended_engagement_overrides.json
     into dossier-builder's W0.

ARTIFACTS
  audit/scorecard.txt                              this file
  audit/scorecard.html                             same content, HTML
  audit/audit_findings.json                        full machine-readable record
  audit/recommended_engagement_overrides.json     drop into dossier-builder W0
```

## When NO outliers / NO questions

Print the panel anyway with empty sections. Reassuring through specificity, not through silence.

```
=== W2: Domain audit complete ===

HOW I DID IT
  • Canonicalized 4,500 domains. All resolved. No parked. No multi-collapse.
  • 50-row spot-check: all 4 providers agreed on all 50.

DOMAIN TRUST DISTRIBUTION
  Verdict       Count    Pct
  verified      4,500  100.0%

OUTLIERS I'M FLAGGING
  None.

QUESTIONS I NEED YOU TO ANSWER
  None.

WHAT I'LL DO NEXT
  Continue to W3 (join inference). No gate.
```
