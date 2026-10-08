# Examples

Two offline checks run in `make check`. Neither needs an API key or the network.

## The offline replay (the whole method)

```bash
python3 examples/northwind-demo/replay.py          # run and check all 13 steps
python3 examples/northwind-demo/replay.py --keep   # keep the outputs to read
```

It runs the method on the fictional company Northwind through the real phase
code: dossiers (customers and a lost deal) -> `customers.csv` built from the
dossiers (labels, 10X, dates) -> phase 0 normalization -> panels built by the LIVE
parsers from recorded raw vendor responses -> segmentation -> hypotheses
(training accounts only; typed signals, untyped ones rejected) -> holdout test and
the firm pass on dated records -> rubric and prospect scoring with one shared
scorer -> ICP derivation with provenance -> evidence packets -> Jev scoring ->
quantify -> the PVP draft.

Where a live run calls a paid API, the replay reads a recorded response: raw
Firecrawl / OpenWebNinja / Apify / news responses, themes and hypotheses for a
300-account cohort (`northwind-demo/cohort/`), and Jev's answers
(`northwind-demo/recorded_jev_cache.jsonl`, recorded 2026-10-06 for under one cent).

The firm pass proves a signal was visible before the account's close date. For
dated records (news items) it runs offline here: only a record that itself
matches the signal can date it, and its URL and excerpt are kept. An old,
unrelated article leaves the cell unknown, and so does a matching article dated
AFTER the close date: a later article cannot prove there was no earlier one.
The archive route (Wayback captures of a homepage) needs the network, so that
hypothesis stops at "provisional only" in the replay.

**What the replay finds: no reliable discriminator.** The cohort is synthetic,
with planted signals: a homepage saying 2+ offices (current state), a dated news
item about opening another office, and a coin-flip "book online" line. The news
signal is PROVEN present for 123 accounts. But search results can never prove a
signal was absent, so the firm table has no "absent" side, no lift can be
computed, and the honest result is "no reliable discriminator found" (cause:
insufficient evidence). The homepage signal clears today's-web filter in one
archetype but cannot be firm-checked offline (cause: not firm-checked). The coin
flip dies. The replay asserts exactly this. The ICP then rests on the labelled
accounts (step 7), and the rest of the method runs as before.

## The offline smoke test

```bash
python3 examples/check.py          # or: python3 examples/check.py --keep
```

It runs each tool once with every API key removed: dossiers, the data audit, the
label reader, offline packets, the judge with no key (every row `insufficient`,
$0) and the PVP draft.

## Northwind demo

`northwind-demo/` is a fictional company, **Northwind Scheduling**
(`northwind.example`), that sells appointment-scheduling software to dental
practices. Every company, person, ID and domain in it is invented. All domains
end in `.example`, which is reserved and never resolves on the internet.

See `northwind-demo/README.md` for the walk-through.

## Won / Lost / 10X walkthrough

`skills/win-loss-rewind/examples/walkthrough-fictional-legalops/` is a second
fictional data set (20 customers of a compliance-software company) with planted
signals the full win-loss-rewind pipeline should find, and a planted fake signal
it should kill at holdout. It needs `ANTHROPIC_API_KEY` and a web-evidence key, ships no recorded
enrichment, and at 20 customers its holdout (2 per cluster) is too small to
confirm anything: expect "no reliable discriminator found" (see its README).

## Which steps need which key

| Step | Offline (no key) | With keys | Expected cost with keys |
|---|---|---|---|
| Dossiers | demo build, schema check, HTML render | the full `/dossier-builder` skill in Claude Code; optional web sample (Exa or OpenWebNinja) | web sample: per the vendor's pricing, 200 records max |
| Data audit | column scan, join test, revenue checks | domain verification: any one of Exa, Firecrawl, Parallel, OpenWebNinja | per the vendor's pricing; the skill asks above $100 |
| Won / Lost / 10X | building `customers.csv` from dossiers; the replay's phases 3-8 on recorded responses | `ANTHROPIC_API_KEY` + one web-evidence key | themes phase: at most $0.01 output per customer on the Batch API (`claude-sonnet-5-5`); the script prints its estimate first |
| Evidence packets | demo packets from saved pages | live page fetches are free; Maps (DataForSEO, $0.002/request, `DATAFORSEO_LOGIN` + `DATAFORSEO_PASSWORD` + `--maps-budget-usd`) and profiles (`BLITZ_API_KEY` + `--profiles`) are opt-in | $0 on default lanes |
| Jev scoring | recorded answers (replay) or every row `insufficient` (smoke test), $0 | `TYPESAFE_API_KEY` or `OPENROUTER_API_KEY` | a fraction of a cent per row; one probe call measured $0.0000118 on 2026-10-06 |
| PVP draft | the sample draft | the assistant writes one per accepted row | your coding agent's usage |
