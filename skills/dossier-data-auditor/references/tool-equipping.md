# Tool equipping

Read at W0 before spawning any sub-agent.

## The source of truth

The matrix lives in `config/tool-equipping.yaml`. This file explains the *why* and the runtime semantics.

## What gets equipped, by sub-agent

### `scan-agent` (W1)

- `GROQ_API_KEY` for cheap structured extraction when a column's header is ambiguous (e.g., a column called `value` with numeric data — Groq can tell us it's currency by looking at 5 sample values).
- No web APIs. No spot-check budget. Pure local I/O.

### `domain-audit-agent` (W2)

The big one. Multi-provider parallel verification means this agent needs the full search/extraction stack:

| Env var | Used for | Cost per call |
|---|---|---|
| `SERPER_API_KEY` | Provider 1 in fan-out (Google KG + organic) | $0.001 |
| `EXA_API_KEY` | Provider 2 in fan-out (semantic) | $0.005 |
| `LINKUP_API_KEY` | Provider 4 in fan-out (alt search) | $0.005 |
| `FIRECRAWL_API_KEY` | Provider 3 in fan-out (scrape page title); also primary JS-page scraper | $0.001/page |
| `OPENWEBNINJA_API_KEY` | Final fallback for domain→company | $0.002 |
| `ZENROWS_API_KEY` | Anti-bot scraping when Firecrawl + agent-browser fail | $0.01/page |
| `BLITZ_API_KEY` | `domain-to-linkedin` for multi-collapse detection | ~1 credit |
| `DISCOLIKE_API_KEY` | Firmographics (industry, keywords) for sanity input | $0.005 |
| `PARALLEL_API_KEY` | 4th-opinion tiebreaker when fan-out is 2-2 | $0.004 |
| `GROQ_API_KEY` | Extract structured fields from scraped HTML | $0.00005 |

Plus the `agent-browser` CLI on disk for JS-page fallback.

Default spot-check budget: **$5 per audit run** (50 domains × ~$0.01 per multi-provider call).

### `join-inference-agent` (W3)

No env vars. Pure local reasoning on Opus. Cost-guard trips at $100 because at large N (>10K rows) the LLM verdict step can exceed that.

### `revenue-agent` (W4)

`GROQ_API_KEY` optional — for extracting revenue values from free-text columns like a description field that mentions "$1.2M ARR." If the data is already numeric, skip Groq.

### `fill-rate-agent` (W5)

No env vars. Pure pandas.

### `sanity-agent` (W6)

The second big one:

| Env var | Used for | Cost |
|---|---|---|
| `PARALLEL_API_KEY` | Primary — Task Groups for ICP cohort sample (~$8/200 rows) AND Task Core for paradox resolution ($0.05/row) | varies |
| `PERPLEXITY_API_KEY` | Second opinion (Sonar Pro) on low-confidence Parallel results | $0.018 |
| `SERPER_API_KEY` | Multi-provider for industry/NPI/CMS lookups | $0.001 |
| `EXA_API_KEY` | Semantic search for paradox resolution | $0.005 |
| `OPENWEBNINJA_API_KEY` | NPI registry lookup for healthcare paradoxes | $0.002 |
| `FIRECRAWL_API_KEY` | Scrape company sites for context | $0.001 |
| `GROQ_API_KEY` | Extract structure from Sonar prose | $0.00005 |

Plus the `agent-browser` CLI on disk.

Hard caps:
- Parallel Task Group: $8 typical, $15 max.
- Paradox resolution: 50 records / $15 max.

### `scorecard-agent` (W7)

No env vars. Jinja2 templating only.

## When keys are missing — graceful degradation

The skill warns once at W0 and degrades, never blocks (except for keys the user can't graceful-degrade — there are none currently).

| Missing key | Degradation |
|---|---|
| `GROQ_API_KEY` | Fall back to Opus for structured extraction. 5× cost; same accuracy. |
| `PARALLEL_API_KEY` | Fall back to the legacy `web_sanity_sample.py` (Serper + Exa + Opus-judge). Slower, less cited. |
| `PERPLEXITY_API_KEY` | Skip second-opinion step on low-confidence rows. Lower confidence in final verdicts. |
| `LINKUP_API_KEY` | Multi-provider fan-out becomes 3-provider (Serper + Exa + Firecrawl). Still works; less triangulation. |
| `FIRECRAWL_API_KEY` | Fall back to ZenRows ($0.01/page) as default scraper. 10× cost. |
| `agent-browser` CLI not installed | ZenRows-only for JS-page fallback. |
| `BLITZ_API_KEY` | Skip multi-domain-to-one-company collapse. Surface multi-domain rows as separate. |
| `DISCOLIKE_API_KEY` | Skip industry-median input. Industry-revenue paradox check becomes less informed. |

If multiple keys are missing, the warnings stack but the audit still runs. Final scorecard notes which providers were unavailable.

## How to add a missing key

The user should set the env var in `.env` at the monorepo root. The auditor reads it via the standard `os.environ` mechanism (no special loading). After updating `.env`, restart the Claude Code session so the new env loads.

Add missing keys to the repo `.env` (see `.env.example` at the repo root) or your shell profile.

## Anti-pattern: don't use MCP for batch

Per global CLAUDE.md and this skill's `mcp_allowed: false` rule: any operation touching >50 rows MUST use Python `requests` directly. Never the MCP wrappers.

The provider scripts under `scripts/{firecrawl_scrape,groq_extract,parallel_verify,perplexity_second_opinion,multi_provider_verify}.py` all use `requests` directly. If you find yourself reaching for an MCP tool inside a sub-agent for batch work — stop, use the Python wrapper instead.

## Anti-pattern: don't default to Opus for extraction

Per global CLAUDE.md Rule 3: never default to Opus. The skill uses Opus for reasoning (verdicts, judgments) but Groq for extraction (parsing structured fields out of unstructured text). Mixing the two is the win: $0.00005/extraction call × hundreds of rows is essentially free; the Opus calls are reserved for the few high-stakes decisions per wave.
