# Delegation & context — when to spawn, what to compile, how to batch

> **Note on this copy.** This reference was written for the full Crawford research agent. This repo ships only the evidence-packet builder (`scripts/build_packets.py`) and the judge that reads its packets (`skills/calibrated-judge`). Mentions of `pack_build`, `harness.py`, `judge_sidecar.py`, title-sweep, tam-qa or other house tools describe parts that are not included. See `NOT-INCLUDED.md` at the repo root.

THE single normative home for how Crawford uses sub-agents and compiles their context.
SKILL.md §4 and prompt-patterns.md §6 point here; if any doc disagrees with this one, this
one wins. Doctrine in one line:

> **Gather with scripts, distill to facts, compile packs per determination, judge each entity
> in its own no-tools context, dispatch those contexts in controlled waves, reduce
> deterministically, escalate the residue warm — and never let raw material or prior verdicts
> touch a blind reasoning context.**

## 1. The router question

Never ask "API or subagent?". Ask: **what context does this decision need, and what kind of
choice is it?** Then route:

| Choice shape | Route | Why |
|---|---|---|
| Deterministic (a rule settles it) | script, $0 | never send an agent where a script settles it |
| Mechanical at volume (extract/classify/distill; context already compiled; no tools needed) | batched cheap API (`bulk_extractor`/`gate` roles; prompt-cached rubric + batch discount) | hammer-through: faster wall-clock AND cheaper than agents |
| Judgment over compiled context (weigh evidence, apply a rubric, decide OR say what's missing) | **isolated subscription adjudication wave** (§6) | subscription tokens, including repeated brief/schema overhead, are imputed; each no-tools invocation sees exactly one entity and can answer, escalate, or defer |
| Judgment but the context is MISSING | gatherer first (scripts; §5) — never a reasoning agent doing fetch work | fetch work poisons a reasoning context and burns its call budget |
| Open-ended investigation (unknown sources, multi-hop) | deep diver, 1-5 entities, warm-started (§8) | the expensive shape; residue only |

Bias note: sessions historically over-reach for the API. When a choice is
judgment-shaped and the context is compiled, the default is the subscription adjudicator —
but its model/subscription use is still imputed. Route on expected correct terminals per total
cost, with context-fit governing correctness, wall-clock, and spend.

**Effort floor** (Anthropic scaling rule): < ~30 residue rows → skip the multi-agent chunk/
sentinel factory, but still compile the compact per-row evidence pack and exact missing-evidence
request. Dispatch those warm handoffs directly; don't cold-start a deep diver or build a factory
for a dozen rows.

### Economic routing rule

At a fixed correctness requirement, choose the route with the lowest **total economic cost per
correct terminal answer**. At a fixed budget, choose the route expected to produce the most
correct terminal answers. Total cost includes measured provider spend, imputed model/
subscription use, retries, crashes, wasted calls, mission-priced wrong terminals, and the
fallback cost of every unresolved row. Provider-only cost and cost per attempted row are
diagnostics.

Run a rung only while its measured marginal value — additional correct terminal answers divided
by additional total cost — justifies it. Stop immediately when the determination's evidence and
freshness contract is satisfied; do not buy a redundant second root when one declared
authoritative system is sufficient. Conversely, cheap confidence cannot terminate a
contradictory or provenance-invalid row. Quota exhaustion creates one explicit routed event and
preserves its spend/attempt identity; it never drops or double-counts the row.

## 2. The Evidence Pack primitive

A pack is the per-entity answer to: *what does a model need to make THIS determination?*
It is a **derived, rebuildable artifact** — `pack_build.py build` from run-dir state is the
truth; "append-only" applies only within one build. Every build stamps a `build_id` into every
pack head and chunk manifest; a chunk manifest against a stale build_id is refused. The build
identity binds source rows, cached page bytes, mission intent (including absence), build
parameters, catalog, relevant policy docs, trust helpers, and agent definitions. Adjudication
and stage identities add the exact brief/contract, assignment, configuration, and artifact
bytes. A changed same-day input invalidates only its dependent stages; resume cannot mix them.

Layout: `<run_dir>/packs/<job-or-mission>/<sanitized_key>.md`, with `packs/index.jsonl`
mapping raw key → path and byte hash (case-fold collision check — two keys differing only in
case silently overwrite on macOS, so mixed-case IDs such as Salesforce's collide). Job 1 keys
by normalized domain; Job 2/3 by row_id. Blank, duplicate, or unstable coordinates are
refused. Positional `row{i}` coordinates require explicit `--disposable` and can never support
an unqualified READY delivery.

**YAML head** (identity only — NEVER prior verdicts, NEVER a prior confidence; those anchor
the judge and live in the sidecar, §4): `determination`, entity keys (name/city/state/domain),
`schema_version`, `build_id`, `token_count`, `as_of`.

**Body**, in budget-priority order — evict from the bottom when over budget (default ~2k
tokens/pack):

1. **Atomic facts** — each `{fact, category, value, source_url|structured_record, quote, as_of,
   date_status, lineage_roots, method, decision_relevance, applies_to, origin_pointer}`. The
   canonical entity name appears
   literally in every fact (retrieval collapses without lexical overlap — NoLiMa). Required and
   supporting facts sort ahead of leads; facts explicitly irrelevant to this determination are
   dropped with a restorable pointer. Unknown freshness remains `as_of: null`/unknown; it is
   never replaced with today's date. `method: cheap_llm` facts are distiller output and must
   carry a grounding quote.
2. **Negative evidence** (`sources_tried`) — structured records of what was checked and NOT
   found, including source, method, date status, lineage, and pointer ("Wikidata P856: no
   claim; 30 discovery templates: 0 survivors"). It prevents repeated work but is never proof
   of nonexistence: absence in a corpus routes onward; it does not manufacture a negative
   answer.
3. **Upstream decisions** — per-entity rulings already made ("phone hit ruled roster-grade per
   the harnett trap; municipal host allowed for this market class"). Actions carry implicit
   decisions; an uninformed judge silently re-litigates them (Cognition).
4. **Open questions** — what the compiling rung couldn't settle.
5. **Page slices** — distilled markdown, last in, first evicted.

**Restorable compression** (Manus): anything dropped leaves a pointer — keep the URL/cache
path, drop the page. The full HTML stays in `page_cache/`. A no-tools adjudicator cannot
restore it and must escalate with a precise request when the missing material is load-bearing;
a warm deep diver may restore only the named pointer under its own budget.

## 3. `facts.jsonl` — the canonical input interface

Each input row normalizes to `{entity_id, fact, category, value,
source_url|structured_record, quote, as_of, date_status, lineage_roots, method,
decision_relevance, applies_to, origin_pointer}` (`date_status` is derived honestly from
`as_of`) with
`category ∈ identity | firmographic | people | signal | tech | footprint | client_prior`.

Job outputs are **adapters** that emit facts.jsonl (pack_build ships adapters for Jobs 1-3).
Any mission — fit scoring, tiering, notability, win-loss discriminators — feeds the same
interface: compile the client's rubric into the brief (§6), the rubric-relevant evidence into
facts, and the module becomes a generic rubric-scoring engine. This is the promotion path to
`_shared` once validated. `category: client_prior` is never rendered as blind evidence; it is
routed to the private compare sidecar with provenance.

## 4. The verdict sidecar (blind-then-compare, enforced BY CODE)

Prior rung verdicts + confidences live in `<sanitized_key>.verdicts.json` NEXT TO the stored
pack, never inside it. That filesystem layout is not the blind security boundary. Each blind
invocation receives one inline copy of one entity's exact pack bytes in a self-contained
manifest and is dispatched from the hashed `crawford-blind-adjudicator` definition with
**zero tools exposed**. The two phases are code-enforced:

1. `chunk` issues one exact BLIND assignment per invocation:
   `{key, assignment_id, pack_sha256, pack_text}`. `isolation_mode` is
   `one_assignment_per_invocation` in the chunk parameters, assignment manifest, each agent
   manifest, and dispatch contract. `assignment_manifest.json` must partition the complete pack
   index between adjudication and explicit deep-diver handoffs. Duplicate owners, owners with
   more than one expected key, outcome-bearing filenames, sidecars, suspicion strata,
   neighboring rows, and prior answers are refused or absent.
2. The orchestrator records `blind_dispatch_receipts.jsonl`. Each unique receipt binds the
   owner, invocation, exact manifest hash, exact no-tools agent-definition hash, empty exposed
   tool list, and raw response hash. The agent cannot author this receipt because it has no
   tools. Missing or hash-inconsistent proof may leave recovery artifacts, but it records a
   critical failure and cannot support READY. This is local provenance, not a cryptographically
   signed platform attestation: the dispatcher must verify that `tools: []` really means zero
   exposed tools; if the platform treats it as inheritance, stop the wave and block the run.
3. `close-blind` validates strict JSONL, schema, owner/assignment identity, exact coverage,
   duplicates, unknown coordinates, and forbidden prior-referencing fields, then commits the
   blind answers into immutable `packs/blind_committed.jsonl`. Byte drift after the close
   refuses downstream work.
4. `chunk-compare` — only runnable after a fresh close — validates every sidecar itself and
   writes an exact compare plan. Every target gets its own invocation containing the inline
   pack, explicit priors, committed blind record, and a new compare assignment ID; every
   non-target has an explicit disposition (`no_prior`, `prior_agrees`, or blocking
   `compare_context_oversized`). The compare plan,
   manifests, and dispatch contract bind the same one-assignment isolation mode. Compare also
   counts the complete brief/schema/pack/prior context; an oversized comparison becomes an
   explicit blocking warm-escalation disposition instead of silently truncating. Compare uses
   a hashed no-tools agent and exact dispatch receipts.
5. A compare verdict is accepted at `reduce` only if its `blind_answer` echoes the committed
   answer exactly (proof it saw the commitment) and any answer change carries a
   `revision_reason`. No last-write-wins anywhere.

Structure beats prompt discipline — a value the model cannot access cannot anchor it, and an
ordering the code enforces cannot be skipped. `chunk --with-sidecars` restores the old
honor-system exposure only for legacy recovery; it is recorded and caps the run below READY.
Residual leak, accepted and documented: verdicts on OTHER fields correlate with the audited
field; for harness/audit runs, redact the sidecar aggressively (`build --blind-field <f>`
drops correlated fields too).

## 5. Stage-typed roles (agents by stage, then per-entity only at the residue)

| Role | Form | v1 tooling |
|---|---|---|
| Gatherer | scripts fetch to `page_cache/` / corpora / registries; an agent only PLANS what to fetch | doctrine only — no dedicated script yet |
| Distiller | `visible_text_stats` (script) → `bulk_extractor` role (cheap API) for judgment-y extraction; output = quote-grounded facts | doctrine only — routes to existing rung-4 machinery |
| Assembler | deterministic | `pack_build.py` |
| Isolated adjudicator wave | one no-tools subscription invocation per entity | §6 contract + prompt-patterns §8 |
| Deep diver | subscription subagent, 1-5 entities, warm-started | §8 |
| Verifier / key agent | unchanged (harness.md) — may START from a pack, still verifies blind from primary sources | harness.py |

## 6. The isolated adjudication-wave contract

**Before any chunking**, the orchestrating session writes `<run_dir>/packs/brief.md` and the
machine-readable `<run_dir>/packs/brief_contract.json`. The prose brief helps the judge; the
JSON contract is the policy authority and binds the determination, answer schema, rubric,
boundaries, Hard Limits, and evidence policy. It must agree with the build and mission intent.
A missing or contradictory contract records a critical condition and cannot support READY.

**Per invocation**, the BLIND agent (one entity, no tools, response text only):
1. Re-reads the inline brief and machine contract (Manus recitation as an internal attention
   step; the response remains strict JSONL with no prefatory prose).
2. Reads the manifest's single inline assignment and **blind-judges only that entity**. No
   neighboring pack exists in its context, and it has no filesystem, search, or fetch
   capability. Verdicts must not reference priors in any field; `close-blind` refuses
   `prior`/`sidecar`/`blind_answer`-class leakage.
3. Returns exactly one JSONL row, echoing `key`, `build_id`, and `assignment_id`. It does not
   write files. The orchestrator saves the raw response under the unique assigned owner and
   records the dispatch receipt.
4. Resume is against the exact content-bound assignment and response receipt. A stray row with
   the same local key never suppresses work, and duplicate verdicts are errors unless the
   compare stage expresses an explicit revision.

**The session then runs `close-blind`** (commit) and `chunk-compare` (join priors). Each no-tools
COMPARE invocation receives one self-contained `compare-agent-<k>.json` assignment with one
entity's pack, priors, and committed blind record. It returns one row that echoes
`assignment_id` and `blind_answer` verbatim and sets `answer` + `revision_reason` when the priors
change the answer, or re-asserts the answer. The orchestrator stores raw response bytes and
compare receipts; `reduce` applies provenance-bound revisions (`revised_from`) and refuses
everything else.

**Verdict record** = the SKILL.md §4 full envelope + `{key, build_id, assignment_id, triage,
research_request?, defer_reason?, unlock?}` with `triage ∈ answer | escalate | defer`.
Every evidence item declares a source (`url` or `structured_record`), quote, method,
`lineage_roots`, and `date_status`; known dates carry a real `as_of`, while unknown stays
unknown. `cost_usd` is measured provider spend and `imputed_cost_usd` prices model/subscription
work; either may be explicit `null` when genuinely unknown, never silently zero.

**Escalate** carries a `research_request`: the exact missing evidence and source to try. At
report time `warm_handoffs.jsonl` combines that request with settled reasoning/evidence,
lineage roots, the exact pack hash/path, and a negative-evidence pointer. It points to cached/raw
material instead of dumping it, so a deep diver starts warm and does not repeat settled work.
**`rubric_gap`** (as a
research_request type): "the rubric doesn't cover this shape" — routes to `review_queue.md` as
a PLAN update (fix causes, not rows; adjudication.md §5), never silently improvised by one
agent while its siblings improvise differently.

**Drift sentinel**: the deterministic schedule seeds known-answer coordinates across the wave,
but every sentinel receives its own isolated invocation and is never labeled to the judge. Any
sentinel miss fails reduction; there is no reused adjudicator context to retire.

## 7. Isolation and throughput controls

| Dial | Setting | Governs |
|---|---|---|
| **Entities per model invocation** | **Exactly 1**, enforced in manifests, assignments, dispatch contracts, receipts, compare plans, and reduction | structural cross-entity isolation |
| **Complete invocation tokens** | brief + machine contract + schema + one pack; repeated overhead is measured before testing the context ceiling | no hidden over-budget context |
| **Oversized route** | effective pack cap = the smaller configured context ceiling minus complete dispatch overhead; anything larger becomes an explicit warm handoff | token eviction cannot silently remove load-bearing context |
| **Wave concurrency** | launch isolated invocations in bounded resumable waves; historical 6-9 concurrency is a starting point, not a quality contract | wall-clock and provider throttling |

Legacy-named `per_judgment` and `chunks_per_agent` inputs only shape the deterministic
schedule/sentinel spacing. They never place multiple entities into a model context.

One entity per agent context is a correctness feature with a real imputed cost: uncorrelated
judge contexts prevent cross-entity anchoring, while repeated brief/schema tokens are counted
rather than called free overhead.
The residue that escalates past the adjudicator lane is typically 5-20% of what enters it
(cascade literature: 14-35% to the expensive path preserves ~95% of top-model quality; our
Job-2 measured tail was smaller — treat per-job).

**Numbers behind the controls — transferability caveat.** These external measurements explain
why Crawford chose isolation; they are not operating settings. The harness replaces them with
measured Crawford correctness and economics per job.

| Figure | Value | Source |
|---|---|---|
| Focused vs full context | ~300 relevant tokens beats the same info in ~113k, all 18 models tested | Chroma context-rot |
| Reasoning under length | most models < 50% of short-context baseline at 32k when lexical overlap is removed | NoLiMa |
| Effective vs advertised context | ¼-½ | RULER |
| Batch classification | −3.2% absolute across batch 5-100; **cross-entity selection −20-32%**; reasoning degrades past ~batch 8; easy labels OK to ~50 | batch-prompting studies |
| Multi-agent token cost | ~15x chat; only for tasks that pay for it | Anthropic |
| Sub-agent return size | 1-2k token distilled summary | Anthropic |
| HTML → markdown | 5-20x token compression | Firecrawl/Cloudflare |

## 8. Triage terminal semantics + the deferred lane

Every row always ends schema-valid (SKILL.md §3 invariant). Triage states:

- **answer** — terminal.
- **escalate** — `packs/escalations.jsonl`. Deep-diver dispatch: the orchestrator groups 1-5
  entities per agent; each prompt uses `warm_handoffs.jsonl`: exact pack hash/pointer, settled
  evidence and reasoning, negative-evidence pointer, open question/research request, §4
  envelope, and Hard Limits. Never a cold start.
- **defer** — `packs/deferred.jsonl` with `defer_reason` + `unlock`. Defer is a **within-run
  state, never a hidden cross-run backlog**. It remains visible and priced as unresolved;
  `report` conditions the run until it is resolved or explicitly converted to an escalation.
  Deferrals roll up as ONE review-queue decision ("N rows deferred pending X — resolve now, or
  retain as priced residue?"). Mechanical unlocks re-enter via `pack_build chunk
  --drain-deferred`.

**Reduce is mandatory** after any multi-agent wave: `pack_build reduce` revalidates its own
inputs, byte-valid stage dependencies, build/assignment identity, strict schemas, exact compare
coverage, one-assignment isolation/unique owners, receipts, and sentinels before writing output.
Duplicate verdicts are errors; there is
no last-write-wins. It reports per-agent distribution skew (>2σ vs overall on comparable
strata), explicit escalations, deferrals, oversized handoffs, unadjudicated coordinates, and the
total unresolved residue. A sentinel or coverage failure marks the stage failed and writes no
publishable reduction. `merge` independently requires a fresh successful reduction and the
exact source-base fingerprint. `report` requires every named stage and safety check to be
present as `ran`, `skipped`, or `failed`; a missing/failed/critical-skipped check cannot become
READY. It renders the residue and warm-handoff artifacts and exits nonzero when the run's
worst-of component verdict is BLOCKED.

## 9. Concurrency + wall-clock

6-9 concurrent agents, wave launches, resume via manifests. Basis: much more than that in
parallel tends to trip server-side throttling, while waves of around nine with
resume-from-cache complete cleanly.

The earlier 10-30-entities-per-context wall-clock envelope is retired: it does not apply to
one-entity invocations. Quote turnaround only from a measured isolated-wave pilot, including
repeated context overhead, throttling, retries, and residue—not from old chunk arithmetic.

## 10. Caching, honestly stated

- **Blind/compare lane**: every entity is a fresh conversation. There is no prior entity context
  to compact and no cross-agent KV reuse guaranteed by a file on disk. Repeated brief, contract,
  schema, and agent-definition overhead is included in full-context accounting and imputed cost.
- **API tier only**: explicit `cache_control` breakpoints (shared rubric first: 1.25x write,
  0.1x reads) + 50% batch discount. This is where cache economics are real dollars.

## 11. When NOT to delegate

- A single fact or a handful of rows → answer inline (1 agent, 3-10 tool calls, or none).
- Outputs that must cohere (one document, one ranked list, one plan) → ONE context writes it;
  parallelize the reading, never the writing.
- Anything a script settles → no agent, ever.
- A worker never re-delegates; compiled context in, typed verdict out.

Provenance: designed from 3 research streams (Crawford internals; repo pattern
recon; external corpus — Anthropic multi-agent/context-engineering, Karpathy, Cherny, Manus,
Cognition, Chroma/NoLiMa/RULER, batch-prompting + FrugalGPT/RouteLLM cascades) + 3 adversarial
critiques (engineering / AI-qualification / GTM). Numbers table carries its own caveats; the
harness replaces them with measured values per job.
