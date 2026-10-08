# The probability sidecar (shadow — and, for three decisions, an OFFER)

> **Note on this copy.** This reference was written for the full Crawford research agent. This repo ships only the evidence-packet builder (`scripts/build_packets.py`) and the judge that reads its packets (`skills/calibrated-judge`). Mentions of `pack_build`, `harness.py`, `judge_sidecar.py`, title-sweep, tam-qa or other house tools describe parts that are not included. See `NOT-INCLUDED.md` at the repo root.

A vendor-neutral **observer** on the rung-6 adjudication lane. It answers one
question — *what would a calibrated judge have said about this pack?* — and
answers it where no part of Crawford can hear.

Script: `scripts/judge_sidecar.py`. Judge: `skills/calibrated-judge`.

```bash
CALIBRATED_JUDGE_MODE=shadow python3 scripts/judge_sidecar.py --run-dir <run>
```

Run it **after `pack_build chunk`**, and after `close-blind` when you want the
incumbent comparison. It is off unless the environment says otherwise.

## What it does not touch

| Thing | Why it is safe |
|---|---|
| The verdict record schema | unchanged; the sidecar writes its own file |
| `build_id` / the hash-bound build contract | `judge_sidecar.py` is NOT in `BUILD_CONTRACT_FILES`, and the output lives at the run root, outside every fingerprinted input |
| Blind adjudication | no manifest, assignment, dispatch contract or pack references the sidecar; nothing reads `judge_sidecar.jsonl`; `close-blind`'s leakage validation is unaffected |
| Crawford's answer | the blind agents decide. In `shadow` the judge cannot override; in `active` it OFFERS (below) and the verdict is still Crawford's until a human adopts the offer |

## What the judge is shown

**The pack's `## Facts` section, and nothing else** — carried as the anchor's
`context`, beside the entity's own name/org/city/state from the pack head.

Never the `.verdicts.json` sidecar. Never a prior answer. Never the committed
blind record. Never a neighbouring entity. An incumbent verdict, when one
exists, is written to the sidecar row for later scoring and is never put in
front of the model.

No `candidate` page block and no `profile` block is synthesised from that
prose. The judge's code-computed signals (`page_token_match`, `phone_match`,
`transport_class`) are therefore absent rather than fabricated from text that
is not a page. **Consequence, measured live:** a `real_website` row
composes to `EVIDENCE_INSUFFICIENT` — *"no candidate to judge"* — because the
v2 battery gates its questions on a structured candidate record Crawford's
prose packs do not carry. The lane is wired and honest; making it informative
needs a determination-aware adapter that reads the run's source rows
(`results.jsonl`, `entity_match_results.jsonl`, `people.jsonl`) into the
judge's record shape. Until that exists, read the rows as plumbing, not signal.

## `active` = an offer, never an installation

```bash
CALIBRATED_JUDGE_MODE=active \
CALIBRATED_JUDGE_ACTIVE_DECISIONS=real_website \
  python3 scripts/judge_sidecar.py --run-dir <run>
# list any of the offerable decisions (real_website, site_status, same_identity), comma-separated
```

Two switches: the global mode, and this decision named in
`CALIBRATED_JUDGE_ACTIVE_DECISIONS`. Anything else observes exactly as before.

When both are true and the judge actually **decided** (`accept` or `reject`,
with a verdict, no error), the sidecar appends one row to
**`<run_dir>/judge_offers.jsonl`** — the run ROOT, never `packs/`. Each row is
shaped like a verdict envelope (`key`, `build_id`, `answer`, `confidence`,
`evidence`, `reasoning`, `method`, `source_roots`, `cost_usd`,
`imputed_cost_usd`, `budget_exhausted`, `triage: answer`) plus the provenance to
audit it: `decision`, `action`, `decision_score`, `per_question`, `model`,
`battery_version`, `composer_version`, `pack_sha256`, `evidence_sha256`,
`incumbent_verdict`, `offered_at`.

**Why an offer and not a verdict.** `delegation.md` §4 makes the blind lane's
inputs code-enforced and lets `reduce` accept a changed answer only through a
provenance-bound revision. A judge writing into `packs/` would break that on the
first row. So nothing reads `judge_offers.jsonl`: adopting an offer is an
explicit step by a human or the orchestrating session, and it is recorded like
any other answer with `method: calibrated_judge`.

**And the honesty rule that travels with it:** the judge read the SAME pack the
blind agent read, so it is **not a second lineage root** on those facts. Every
offer carries `source_roots: ["calibrated_judge"]` and
`independent_lineage_root: false`; `adjudication.md` §1 still governs what makes
a fact terminal.

Only the three identity decisions may be offered from this lane — `real_website`,
`site_status`, `same_identity`. The people lanes have no determination-aware
adapter here (see "What the judge is shown"), so listing one buys an observation
and nothing more; the row says so in `offer_withheld`. Every sidecar row carries
`offer_withheld`: `""` when it WAS offered, otherwise the reason
(`mode_is_not_active_for_this_decision`, `decision_not_offerable_from_this_lane`,
`judge_did_not_decide`, `judge_named_no_answer`, `judge_unavailable`). A lane
that silently declines to offer is a lane nobody can audit.

## Determination → decision

Explicit `--decision` wins; then the determination text; then the job. An
ambiguous determination with an ambiguous job **refuses** rather than guesses.

| Determination shape | Job | Decision |
|---|---|---|
| website liveness / is it live / reachable | `liveness` | `site_status` |
| which website belongs to this entity | `match` | `real_website` |
| still employed / current employer | — | `employment_now` |
| who holds the role | `people` | `role_holder_now` |

## The row

`<run_dir>/judge_sidecar.jsonl`, append-only, one row per `entity_key` +
`decision`: raw per-question probabilities, composed `action` and `verdict`,
`decision_score`, `model`, `backend`, `battery_version`, `composer_version`,
`pack_sha256`, `evidence_sha256`, `evidence_fields`, `latency_ms`, `cost_usd`,
`incumbent_verdict` + `incumbent_source`, `mode`, and `error`.

Evidence size is reported honestly: `facts_chars` is what was sent,
`facts_chars_full` is what the pack held, and `facts_truncated` says whether
the 20k-character send cap bound. A pack is ~2k tokens by contract so the cap
should never bind — but a calibration curve drawn over silently clipped
evidence is not a calibration curve, so the clip is logged rather than hidden.

Re-running judges nothing it has already judged at the same `pack_sha256`
(`--refresh` overrides, `--limit N` caps the pass).

## Failure is a row, never an outage

No key, 429, timeout, bad schema, a judge module that will not import, pack
bytes that drifted from the index, a pack with no compiled facts — each becomes
a row with an `error` and the run proceeds untouched. This script cannot fail
a Crawford run.


---

# Rich evidence packets (a second sidecar, ON by default)

The probability sidecar above is an observer on the adjudication lane. This is
the other half: the **evidence** a calibrated judge would need in order to be
worth asking. `pack_build build` now writes one rich packet per entity —

```
<run_dir>/packs/<mission>/packets/<entity_key>.json
<run_dir>/packs/<mission>/packets/index.jsonl
```

— for every Job-1, Job-2 and Job-3 row in the run. Shape and caps belong to
`scripts/lib/packets.py` (Crawford's own), which
implements `references/evidence-packet.md`. This module owns only which rows
become anchors, which caches may be read, and what may be paid for
(`scripts/lib/rich_packets.py`).

## What is in one

`record` (the row's own anchor) · `site` (domain, final_url, http_status,
redirect_terminus, homepage_text <=6,000 chars, about_text <=3,000,
services_text <=3,000, contact_text <=1,000, nav_labels, og_site_name,
schema_org_name, legal_entity_footer, ownership_disclosure_text) · `maps`
(primary_category, secondary_categories, business_status,
business_status_as_of, address, phone, rating_count) · `registry` (source,
status, entity_type, taxonomy_code, taxonomy_desc, parent_org_lbn, as_of) ·
and for a people row, `profile` (headline, about, current_experiences[] kept
UNFLATTENED, prior_role, location, licenses, open_to_work, scrape_as_of) over
the company's identity block.

Every present value carries `source`, `observed_at` and a `status`
(`present | truncated | stale`). **A missing field is never an empty string**:
it is listed in `absent` with a status (`absent | fetch_failed | junk_page |
stale`) and a reason. That asymmetry is the point — a blank that reads as a
value is how a failed fetch ships as a clean row.

## Why it cannot move a build

| Thing | Why it is safe |
|---|---|
| `build_id` | packets are written AFTER `finish_stage`, are not stage outputs, and `lib/rich_packets.py` is not in `BUILD_CONTRACT_FILES` |
| `page_cache/*.html` (a fingerprinted input) | sub-page fetches are cached in a SEPARATE PageCache root, `<run_dir>/rich_packet_cache/`. The run's own cache is opened OFFLINE and only ever read |
| Blind adjudication | no pack, manifest, assignment or dispatch contract references a packet; nothing reads one. Packets carry evidence and identity, never a prior answer or a verdict |
| The run | every failure is a counted skip; a packet cannot fail a build |

**The one caveat that matters.** On the match job the packet's `site.domain`
may be the pipeline's own answer (`matched_domain`) rather than an independent
observation. `crawford.domain_source` says which, on every packet. A packet is
therefore admissible as evidence for a LATER question and inadmissible for
re-judging the decision that produced it.

## Spend

Free by default. Sub-page fetches are free, NPPES is free, and the **paid Maps
lane is never called without `--rich-packets-maps-budget-usd`** — the budget is
a refusal, not a warning, and each call writes a `cost_ledger.jsonl` line at
rung `rich-packets-maps` (`basis: imputed`, ~$0.001/row). LinkedIn profiles are
off unless a caller passes its own `profile_lookup`, because that lane spends
credits. The registry seam is NPPES for healthcare anchors and an explicit
caller-supplied hook for everything else — fifty state scrapers behind one
field is how "registry" starts meaning fifty different things.

## Switches

| Switch | Effect |
|---|---|
| `--no-rich-packets` | off for this build |
| `rich_packets: false` in `<run_dir>/rich_packets.json` | off for this run (the CLI flag wins) |
| `--rich-packets-offline` | pure replay of the rungs' page cache, no network |
| `CRAWFORD_RICH_PACKETS_OFFLINE=1` | the same, from the environment (the test suite sets it) |
| `--rich-packets-dry-run` | prints the plan and the ceiling on spend; writes nothing |
| `--rich-packets-max-fetches N` | run-wide ceiling on sub-page fetches (default 500) |
| `--rich-packets-profiles` | fetch a LinkedIn profile for every people row that carries a URL (spends provider credits; OFF by default) |
| `--rich-packets-maps-budget-usd X` | the only way the PAID Maps lane runs at all |

`mission_intent.json` rejects unknown keys by design, so the config switch
lives in its own file rather than bending that contract.
