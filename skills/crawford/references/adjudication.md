# Adjudication, freshness, license facts, and the human queue

> **Note on this copy.** This reference was written for the full Crawford research agent. This repo ships only the evidence-packet builder (`scripts/build_packets.py`) and the judge that reads its packets (`skills/calibrated-judge`). Mentions of `pack_build`, `harness.py`, `judge_sidecar.py`, title-sweep, tam-qa or other house tools describe parts that are not included. See `NOT-INCLUDED.md` at the repo root.

The engine's honesty layer. Crawford uses TAM-QA's transferable trust invariants — exact
coordinates, strict artifacts, provenance, freshness, and fail-closed reporting — and emits
facts ready for downstream TAM-QA where required: value, source/structured record, quote,
method, lineage roots, and honest date status. Conflict adjudication is a first-class runtime
step, not a ship-gate afterthought.

## 1. The determination-specific evidence rule

- Default: a fact is terminal when two sources with **distinct lineage roots** agree. Two files,
  APIs, or access paths sharing one upstream collapse to one voice (Salesforce inherited from
  the TAM is NOT a second vote on it). A mission contract may name one authoritative system of
  record that suffices alone for a specific determination, or require more roots/fresher
  evidence where false-positive or false-negative cost warrants it. The machine-readable
  mission and brief contracts are authoritative; confidence alone never waives them.
- **Aggregator blocklist** (never corroborate, only seed): Blitz, ZoomInfo, Apollo, RocketReach,
  PDL-enrichment, Lusha, ContactOut, Clay-brokered vendor data, any reseller. Basis: blind
  calibration audits find a large share of aggregator contact names wrong.
- Crawford applies the governing determination policy at the exact entity+field coordinate;
  anything less stays unresolved or routes to the human queue. It does not import TAM-QA's
  master-file mutation or survivorship machinery. If a downstream TAM-QA run later auto-fixes
  a master, TAM-QA's own blast cap and reversible fix ledger govern that separate action.

## 2. Blind-then-compare (the anti-anchoring move)

Adjudicator agents get the evidence pack WITHOUT the recorded value, commit an answer, THEN see
the recorded value and reconcile. Never let the worker grade its own output — the verifier is a
fresh context seeing only output + criteria (Anthropic guidance), instructed to flag ONLY gaps
that affect correctness (a reviewer told to find gaps always finds some).

Inside isolated adjudication waves (delegation.md) this is enforced STRUCTURALLY, not behaviorally.
`chunk` creates exactly one self-contained entity assignment per invocation and dispatches the
hashed blind-agent definition with no tools. `one_assignment_per_invocation` is bound into the
chunk parameters, assignment manifest, agent manifest, and dispatch contract. The model receives
no neighboring entity, pack/sidecar path, filesystem capability, prior answer, confidence band,
or suspicion stratum. Full invocation context includes the repeated brief, machine contract,
schema, and one pack; that overhead is counted before the context ceiling is enforced. A unique
orchestrator receipt binds the exact one-key manifest, no-tools definition, invocation, and raw
response bytes; missing/hash-inconsistent proof cannot support READY. `close-blind` validates
the isolation contract, unique owners, exact assignment coverage, and immutable commitment
before code may read and join sidecars.

Comparison is a separate no-tools phase with one entity per new invocation and receipt. Its
plan, manifest, and dispatch contract bind the same isolation mode. It receives the committed
blind record plus code-validated priors and must echo `blind_answer` exactly. A changed answer
needs a provenance-bound `revision_reason`; duplicate, multi-entity, or silent overwrites are
errors. `--with-sidecars` is legacy honor-system recovery and caps the run below READY.

For stubborn ties: the council pattern — anonymized candidate records to 2-3 cheap models
(distinct lenses: skeptical-fraud-reviewer / company's-own-webmaster — the STORM persona trick),
a chairman picks. Confidence reflects the mission's calibrated evidence policy, never a model's
self-reported score alone. Parallel's calibrated Basis (low/med/high) is one acceptance input,
still logged as one root; it cannot manufacture provenance or override contradiction.

## 3. Freshness arbitration (dates are first-class)

1. **Source-native as-of first**: portal "rows updated", file dates, release ids (CC-MAIN-YYYY-WW,
   Overture release path, NPPES file month, GLEIF publish ts). Every fact carries `as_of`.
2. **Delta feeds beat snapshots**: FCC daily/weekly, Companies House stream, Oregon monthly
   new-biz, TTB weekly, NPPES weekly incrementals — prefer for currency.
3. **Conflict ranking**: (a) newer as-of wins, (b) primary beats aggregator, (c) faster cadence
   wins. A daily regulator census beats ANY packaged dump.
4. **Date the web evidence itself**: CT earliest cert, Wayback first/last capture, CC crawl
   date, sitemap lastmod, DNS SOA serial (signal-grade only — many hosts use opaque serials).
5. **Unknown freshness remains unknown** (the 2019-Kaggle-PDL trap). Preserve useful undated
   material as a lead with `date_status: unknown` and no invented `as_of`; never promote it to
   current evidence, a fresh answer key, or a terminal answer where the mission freshness SLA
   applies. Catalog rows carry `as_of` + `cadence` + `freshness_check`; stale/unknown evidence
   routes onward rather than being stamped with today's date.
6. **The fire-chief rule**: conflicting people rosters resolve toward the roster whose PAGE is
   newer, not whose site feels more authoritative. Archived-only people are `archived_only`,
   never blended as current.
7. **Suspicion pattern**: present in a historical dataset (PPP) but absent from every current
   POI/license/live-site check → probably defunct; flag, don't ship.

## 4. License facts are collected, not adjudicated

Crawford researches and collects; it does NOT judge whether a source is "legally usable" or
withhold it from a deliverable. A separate downstream process owns all usability/licensing
adjudication and gating. Crawford's only job here is to preserve the FACTS it collected so that
downstream step has them: the source's raw `license_notes` and any research note ride through
the catalog, surfaced by `catalog.py notes "<source>"`. Do not classify sources as
open/restricted/conditional and do not block a source on those grounds — pass the information
along and let the downstream process decide.

## 5. The human queue (tradeoffs, not chores)

File: `<run_dir>/review_queue.md`, impact-sorted. Every item is a DECISION with stakes:

```
## Q1 [impact: 1,200 rows | ~$20 provider spend | ~$30 expected fallback avoided]
Rung-4 left 1,200 rows without enough determination-specific evidence. Parallel Task lite is
expected to add ~800 correct terminals at ~$20 estimated spend; unresolved rows otherwise carry
the mission's expected fallback cost.
Options: (a) spend ~$20 (b) retain and price the unresolved residue (c) sample 50 first [recommended: c]
Evidence: cost_ledger.jsonl · confusion sample: qa/sample_20.csv

## Q2 [impact: one whole state segment] — which source wins systematically?
State roster (older) and own-site scrape (newer) disagree on chief names in about 60% of that
state's rows. Recommend: own-site wins (newer pages); demote that roster to seed-only for this job.
Approving updates the survivorship policy for the RUN, not row-by-row.
```

Rules: rank by expected additional correct terminals per additional total cost and
rows-affected × decision leverage. Show measured provider spend, imputed model/subscription
cost, retry/waste cost, wrong-terminal exposure, and the fallback price of unresolved rows.
Every item has a recommendation; answers become run-plan/policy updates (source priority,
evidence rule, calibration, budget) and rerun only the affected slice. Never ask the human to
hand-fix a row the ladder could close.
