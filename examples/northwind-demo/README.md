# Northwind demo

A fictional company run through the whole method. Northwind Scheduling
(`northwind.example`) sells scheduling software to dental practices. Everything
here is invented. Domains end in `.example`, which never resolves online.

## Run it

From the repo root:

```bash
python3 examples/northwind-demo/replay.py --keep
```

Thirteen steps, each through the real code, no keys, no network. The work folder
it prints holds every output: dossiers, archetypes, the derived `icp.json`,
packets, `scores/scores.csv`, `scores/shortlist.csv` and `pvp-email-draft.md`.

## The files

| File | Step | What it is |
|---|---|---|
| `corpus/accounts.csv` | 2 | 8 accounts: 5 customers, 2 churned, 1 closed-lost deal |
| `corpus/contacts.csv` | 2 | people at each account, plus Northwind's own rep |
| `corpus/invoices.csv` | 2 | monthly invoices with billed location counts |
| `corpus/calls/*.md` | 2 | 5 call transcripts with a participant roster (one per lost deal too) |
| `build_demo_dossiers.py` | 3 | deterministic stand-in for the dossier-builder skill (customers + lost deals) |
| `customers.csv` | 5-6 | THE customer file: labels, 10X, dates, evidence; the replay rebuilds it from the dossiers |
| `cohort/` | 5-6 | 300 fictional accounts: raw vendor responses, themes and typed hypotheses |
| `icp.json` | 7 | ICP rules with provenance, derived by the replay |
| `companies.csv` | 7 | 6 fictional prospects to score |
| `site-snapshots/*.html` | 8 | saved homepages for the 6 prospects |
| `seed_page_cache.py` | 8 | puts the snapshots where the packet builder reads first |
| `recorded_jev_cache.jsonl` | 8 | Jev's real answers for the 6 packets, recorded 2026-10-06 |
| `replay.py` | all | runs steps 3 to 10 and checks each result |
| `pvp-email-draft.md` | 10 | the PVP email the replay writes from its results |

## What to look for

- **Lost deals are evidence.** `Elmstead Oral Surgery` never became a customer, so
  it gets a prospect-mode dossier. Its unprompted line ("Most of our cases are
  booked weeks out by referring dentists") is where the oral-surgery exclusion comes from.
- **Led versus unprompted.** In the Rivermark discovery call, "One calendar across
  every location" is a "Yes." to the rep's question, so it is graded `led`. The
  PVP quotes the unprompted line instead ("When a hygienist calls out sick...").
- **10X is measured.** Rivermark and Copperline billed 12.3x and 17.4x the median
  paying customer's lifetime revenue ($6,840). The replay recomputes this from
  `invoices.csv` and fails if `customers.csv` disagrees.
- **Churn is not a win.** In the cohort, churned accounts still say "won" in the
  CRM column; phase 0 reads `health` and studies them as churned.
- **The holdout is held out.** The replay checks that no holdout account's ID,
  theme or panel reaches hypothesis generation.
- **Signals are typed.** "Practice headcount" with no threshold is rejected at Gate 4;
  a generic "growing practices" is rejected at Gate 1.
- **Firm means dated and matching, and absence must be provable.** The news signal
  is proven present for 123 accounts, each cell keeping the matching article's URL
  and excerpt. An old article about a 5K run never counts, and a matching article
  dated after the close date stays unknown. Because no search result can prove a
  signal was ABSENT, no firm lift exists and the replay reports "no reliable
  discriminator found" (insufficient evidence). That is the correct answer for this
  data, not a failure. The coin-flip "book online" signal dies.
- **Every ICP rule has provenance.** Each exclusion names the lost accounts it came
  from; "veterinary clinic" is marked as product scope, not learned from a loss.
- **Jev's real answers.** Jev accepted Maplecrest and Northshore. It returned
  `insufficient` (not a "no") for the other four, including the orthodontist, the
  solo practice and the vet the ICP excludes: one-page packets did not give it
  enough to confirm an exclusion. In a live run you would enrich those packets
  (`--enrich`) or review them by hand.
- **Quantify.** Accepted rows are ranked by the office count read from each
  homepage (Maplecrest 5, Northshore 3), with the quote that gave the number.

## What is a stand-in

`build_demo_dossiers.py` is a plain join, written so the demo runs without keys.
The real `dossier-builder` skill runs inside Claude Code, asks you questions
between waves, and grades transcript lines with the Speaker Attribution Protocol.
The output shape and the schemas are the same.
