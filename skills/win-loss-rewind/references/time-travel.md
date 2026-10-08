# Time Travel — Proving What Was Visible, and When

The skill's core question is "what could we have known, and WHEN." Before this
capability existed, backcasting silently counted **today's** web as if it had
been visible years ago — an independent grader caught exactly this hindsight
leak in live use. This document is the doctrine that closes it.

## The three visibility tags

Every pre-close claim carries exactly one tag. Only the first two are FIRM.

| Tag | Meaning | Proven by |
|-----|---------|-----------|
| **[ARCHIVED-BEFORE]** | An archive.org capture at-or-before the customer's reference date shows the signal | `scripts/wayback_timetravel.py bracket` |
| **[RECORD-DATED-BEFORE]** | The record's own intrinsic date (+ publication-lag haircut) precedes the reference date | the record's date field, per `source-temporal-classification.json` |
| **[RETRO-ASSUMED]** | Only today's web was observed; visibility at the reference date is an assumption | nothing — and it says so |

Firm lift is computed from firm-tagged cells only ([RETRO-ASSUMED] and
unchecked cells are *unknown*, never coerced to absent), and only FIRM signals
enter the Phase 7 discriminator. Full mechanics: `lift-and-holdout.md`
§ "Two-pass firm lift".

## The precision doctrine

A finding dated only to a vague multi-year range is near-worthless — the
strike window IS the deliverable. Every firm claim must carry:

- **Knowable-by window** — the CDX change-bracket: signal absent in capture A,
  present in capture B → it became visible in **(A, B]**. Produced directly by
  `bracket()`; no page-diffing heuristics, just archive.org's own digest
  collapse.
- **Strike window** — `reference_date − knowable_by date`, in days: how long
  the signal sat in plain sight before the deal. `discriminator.md` reports
  the median and range per signal across firm cells.

**Publication delay:** the date ON a record is not the date the WORLD could
see it. A permit filed in March may not surface in the portal until June —
knowable-by = record date + the source's `publication_lag_days` haircut
(`source-temporal-classification.json`; default conservative, tightened per
source when an archive capture of the portal itself proves earlier
visibility). Never compute a strike window from the raw record date of a
laggy source.

**Causation over correlation:** every shipped signal needs a stateable causal
chain — pain → visible exhaust → detection, with the delay at each hop. The
existing statistics (lift, CI floor, holdout replication) guard against
correlation-chasing; the knowability panel (below) guards against mechanisms
nobody can state. Both anti-patterns are named in `anti-patterns.md`.

## The prover: `scripts/wayback_timetravel.py`

Built for this skill and hardened for agent swarms. Three commands, all importable as
functions:

- `moments <url>` — every date a page's CONTENT changed (CDX
  `collapse=digest` — the server drops identical-digest captures, so each
  remaining row is a distinct version). Index-only; no page downloads.
- `get <url> <YYYYMMDD>` — readable text of the capture nearest that date.
- `bracket <url> <YYYYMMDD> <pattern>` — was this text visible by that date,
  and in which change-bracket did it first appear? Binary-searches the
  versions at-or-before the reference date: ≤9 page fetches even against a
  300-version history, all cached.

Operational contract (verified live during the build):

- **Cache** — `~/Library/Caches/gtm-engineer-starter/<host>/win-loss-rewind/wayback`
  (`$WLR_WAYBACK_CACHE_DIR` overrides; `~/.cache/...` on non-macOS). No TTL:
  captures are immutable, so a cached response is correct forever. Cache keys
  are per-fetch (url+timestamp), so many hypotheses probing the same page
  share downloads.
- **Throttle** — 3s minimum spacing between live hits, enforced by an
  fcntl-flock'd stamp file: concurrent processes/threads queue on the lock
  instead of racing past a naive read-sleep-write stamp.
- **Circuit breaker** — web.archive.org's replay edge hard-refuses TCP from
  IPs that burst page fetches (observed live: blocks lasting tens of minutes,
  while the CDX/availability hosts stay up). Any refusal/429 puts EVERY
  process sharing the cache into exponential cooldown (60s → 120s → … 15 min
  cap); short cooldowns are waited out, long ones fail fast so batch runs
  degrade to *unknown* honestly. Completed fetches are cached, so a re-run
  after the block clears resumes where it left off.
- **Honesty invariant** — "no captures" is NEVER proof of absence:
  robots-excluded and never-crawled pages look identical to pages that didn't
  exist. `bracket()` returns `no_captures` and Phase 6 maps it to
  unverifiable, not to signal-absent.
- Known limits: CDX row cap (300/query, `truncated` flag set when hit);
  www/non-www are distinct hosts in some index states (`url_variants()` +
  automatic fallback); JS-rendered SPAs yield near-empty text in old
  captures; gzip'd original bodies are sniffed and decompressed.

## Common Crawl — the second archive (taught, not wired)

archive.org is the automated prover. **Common Crawl** is the fallback archive
agents should seek out by hand when Wayback has no coverage: dated public web
crawls since ~2008, with a queryable index at `index.commoncrawl.org` (query
by URL → dated WARC records; each monthly crawl is a separate index). It
sometimes holds captures of pages the Wayback crawler never visited —
especially deep pages on mid-size sites. A dated Common Crawl record counts as
[ARCHIVED-BEFORE] evidence exactly like a Wayback capture; cite the crawl id
and record timestamp. No automation is wired here on purpose — coverage gaps
are rare enough that a knowability agent following the index docs beats
maintaining a second client.

## Where time travel enters the pipeline

| Phase | What happens |
|-------|--------------|
| **0** | Data archaeology asks which per-outcome date columns exist (churn date, renewal date) — `scripts/reference_dates.py` documents the anchor rule + fallbacks |
| **2** | `enrich_panel.py` records a free wayback timeline per customer (homepage + up to 2 key pages, index calls only) under `wayback_timeline` — a coverage map and a cache pre-warm for Phase 6 |
| **4.5** | Vertical Data Scout — one agent per archetype maps the vertical's real data landscape (registries, portals, rosters — verified + dated) BEFORE hypotheses exist; atlas in `vertical_sources.jsonl`, panel backfill for members, auto-merged into Phase 5's citable sources and Phase 6's manifest |
| **5** | Every hypothesis must declare `temporal_provability` (its best-case tag + mechanism), consistent with the source manifest — Gate 4b kills liars mechanically in `generate_hypotheses.py` |
| **6** | Two-pass validation: provisional filter → firm verification per cell → firm lift. Evidence ledger in `wayback_evidence.jsonl` |
| **6.7** | Knowability panel (below) |
| **7** | `compose_discriminator.py` admits only `status=="firm"` signals (`--allow-provisional` is the loud escape valve); the Cynical Buyer scores a dedicated temporal-firmness axis |
| **8** | **Untouched on purpose.** Scoring a cold prospect with today's data is legitimate — there's no close date to backcast from on an open prospect, and the rubric already earned its lead-time claims at build time. Time travel validates the rubric; it doesn't gate applying it. |

## Phase 6.7 — the knowability panel

After Phase 6 picks the signals that matter, one dedicated research agent per
FIRM signal asks the question statistics can't: **"how could we have
determined this ahead of time, in THIS vertical?"** Prompt:
`prompts/knowability-agent.md`. Verdicts: `cant_know` / `proxy` /
`direct_evidence`, each with a causal chain (delays per hop), a detection
recipe for a cold prospect, and a dated worked example (tagged like all other
evidence).

There is deliberately **no universal dataset catalog** — what was knowable is
unique to each vertical. Agents reason first, then search independently,
starting from the Phase 4.5 scout's atlas and the Crawford research skill's
free-first sources, and must verify a dataset's historical depth before citing
it (a database founded in 2024 proves nothing about 2022). An honest
`cant_know` is a good output. Knowability verdicts feed back into the scout
atlas — the map compounds across runs.

## Opportunistic archive checks (LinkedIn and friends)

Some sources auth-wall crawlers, so archive captures are rare but not zero
(a company's LinkedIn page can have no 200-OK captures for years). These are classified `wayback_friendly:
"opportunistic"` with an `opportunistic_url_field` naming the panel field
holding the page URL. The rule is **asymmetric by design**: a found capture
containing the signal earns [ARCHIVED-BEFORE]; a missing or junk capture is
ALWAYS unknown — a login-wall snapshot is indistinguishable from true absence,
so the opportunistic route can prove presence and can never prove absence.

Orchestration: the skill (running in Claude Code) spawns one subagent per firm
signal with the prompt + briefing, collects JSON verdicts into
`outputs/<run-id>/knowability.jsonl`; `compose_discriminator.py` picks that
file up automatically and renders verdicts into the deliverable. Default the
subagents to Sonnet per cost-controls.

## Reference dates per outcome

`scripts/reference_dates.py` — the single anchor-rule implementation:

| Outcome | Anchor | Fallback |
|---------|--------|----------|
| won | close_date | — |
| lost | close_date | first_call_date (flagged low-confidence) |
| churned | close_date (signing — "could we have seen the bad fit before we signed") | churn_date, flagged |
| healthy | close_date (signing — healthy is functionally still-won) | renewal_date, flagged |

Unanchorable rows (no usable date) are *unknown* in every contingency table —
never guessed.
