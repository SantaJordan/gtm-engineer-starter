---
name: crawford
description: Build evidence packets for a list of companies or people (Crawford "Job 8"). One packet per row, holding named fields with a source, a date and a character cap on every value, and an explicit "absent, and why" wherever a value is missing. Use this whenever the user wants to score, qualify, or check fit for a list of accounts, wants "the evidence behind each row", or is about to send a list to the calibrated judge (Jev). Free lanes first (the row's own website pages, the NPPES health registry); paid lanes (Google Maps facts, LinkedIn-style profiles) are off unless the user sets a budget or flag.
version: "1.0-starter"
author: "Jordan Crawford / Blueprint GTM"
requires-keys: []
optional-keys: [DATAFORSEO_LOGIN, DATAFORSEO_PASSWORD, BLITZ_API_KEY]
---

# Crawford: evidence packets for a list

This folder is the **evidence-packet cut** of Crawford, Jordan Crawford's research
agent. It does one job: turn a CSV of companies (or people) into one evidence
packet per row. A judge, an agent, or a person on a call reads the packet
instead of guessing.

The folder must stay named `crawford/` and sit next to `skills/calibrated-judge/`.
The judge finds the packet builder by that sibling path (or by `$CRAWFORD_SCRIPTS`).

## What a packet is

A packet is a JSON file. Every value in it has:
- a **source** (which page, file or registry it came from),
- an **observed_at** date,
- a **status**: `present`, or `absent` with a **reason**.

That last rule is the point. "We did not look" and "we looked and there is
nothing" must never look the same. The full contract is
`references/evidence-packet.md`.

## How to run it

```bash
# 1. Preview first: build a few packets, see the cards and the cost, then stop.
python3 skills/crawford/scripts/build_packets.py --run-dir out/my-run \
    --companies companies.csv --icp icp.json --preview 3

# 2. Build every packet (add --offline to use only cached pages; no network).
python3 skills/crawford/scripts/build_packets.py --run-dir out/my-run \
    --companies companies.csv --icp icp.json

# 3. Score them with Jev (see skills/calibrated-judge/SKILL.md).
python3 skills/calibrated-judge/scripts/judge_packets.py \
    --packets out/my-run/packets --icp icp.json --out out/my-scores
```

`companies.csv` columns: `name` (required), then any of `domain`, `city`,
`state`, `phone`, `npi`, `row_id`. A missing column is recorded
as an absence, never as a blank.

`icp.json` holds your rules as data: `inclusions`, `exclusions`, and optionally
`territories` and `competitor_names`. **Always give an exclusion list.** With no
exclusions, the fit decision can only ever come back "maybe".

## The rule before any paid pass

Run `--preview` first. Show the operator the packet cards and the cost estimate,
and wait for a yes. `--yes` continues into the full build.

## Evidence lanes (free first)

| Lane | Cost | When it runs |
|---|---|---|
| pages | free | always (homepage plus up to `--max-subpages` About/Services/Contact pages) |
| registry (NPPES) | free | healthcare-shaped rows, or `--registry always` |
| maps (Google Maps listing: category, rating, review count, address, phone) | DataForSEO, $0.002 per request (Live mode list price) | only with `--maps-budget-usd` above 0 and `DATAFORSEO_LOGIN` + `DATAFORSEO_PASSWORD` |
| profiles | Blitz, priced per record | only with `--profiles` and `BLITZ_API_KEY` |

Every call, free or paid, writes a line to `<run-dir>/cost_ledger.jsonl`.

The Maps lane uses DataForSEO's Google Maps SERP API, Live mode
(`POST /v3/serp/google/maps/live/advanced`, docs:
https://docs.dataforseo.com/v3/serp/google/maps/live/advanced/). DataForSEO lists
Live mode at $0.002 per request and the cheaper Standard queue at $0.0006, which
answers minutes later (https://dataforseo.com/pricing/serp/google-maps-serp-api,
checked 2026-10-06). The charge the response reports is what goes in the ledger.
The builder searches "name city state" and keeps the listing whose title shares
the most words with the row's name; no shared word means no match.

## Output layout

```
<run-dir>/packets/companies/<key>.json   one packet per row, under a "packet" key
<run-dir>/packets/index.jsonl            key, tier, present/missing counts, cost
<run-dir>/packets/richness_report.md     how many packets are rich / partial / thin, and why
<run-dir>/cost_ledger.jsonl              one line per lane call
```

## Files

- `scripts/build_packets.py`: the command (Job 8).
- `scripts/lib/packets.py`: the packet builder and the card renderer.
- `references/evidence-packet.md`: the contract (fields, caps, absences).
- `references/adjudication.md`, `references/delegation.md`, `references/judge-sidecar.md`:
  doctrine from the full Crawford. They mention tools not included here.
- `agents/`: blind-then-compare adjudicator prompts for a second human-style read.
- `tests/`: run `python3 -m pytest -q skills/crawford/tests`.

What the full Crawford adds (316 free sources, dataset indexes, find-people,
still-alive checks) is listed in `NOT-INCLUDED.md` at the repo root.
