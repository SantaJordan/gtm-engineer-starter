# Start here

This is the method, in order. It is written for two readers at once: a person
working through it, and an AI coding agent (Claude Code or Codex) asked to run it.
Each step says what it is, what to run, what goes in, what comes out, and how to
know you are ready for the next step.

**Agents:** read this whole file first, then `llms.txt` for the file index. Run
steps in order. Stop at every "Decision rule" and show the human the evidence
before moving on. Never spend money without stating the cost first.

## Your project layout

Your project IS a copy of this repo. You add your context to it; the skills and
scripts stay where they are, so every command below works as written.

```
~/gtm/                     your project (a clone of this repo, pushed to a PRIVATE GitHub repo)
  START-HERE.md  skills/  foundation/  examples/      from the starter
  CLAUDE.md                project instructions (copied from foundation/template/)
  wiki-pattern.md          how the wiki works (copied from foundation/)
  wiki/                    pages the AI writes from your data (committed to your private repo)
    raw/                   your raw exports: CRM, calls, billing (NEVER committed, see .gitignore)
  private/                 anything else customer-identifying (NEVER committed)
  private/customers.csv    THE customer file (labels + dates); every step uses this one
  companies.csv icp.json   your prospect list and ICP rules (NEVER committed)
  out/                     generated runs: packets, scores (NEVER committed)
  .claude/skills/          links to skills/*, made by `make install-skills` (before Step 3)
  .env                     your API keys (NEVER committed)
```

**Every command runs from the project root (`~/gtm`).** Every script reads keys
from `~/gtm/.env` (a key already exported in your shell wins); nothing needs exporting. The Won / Lost / 10X skill writes its own run folders under
`skills/win-loss-rewind/outputs/<run-id>/` (also ignored by git).

## The steps at a glance

| # | Step | Skill or file | Needs a key? |
|---|---|---|---|
| 0 | Set up and prove it runs | `make check` | no |
| 1 | Foundation | `foundation/template/`, `foundation/wiki-pattern.md` | no |
| 2 | Connect your context | `foundation/connect-your-context.md` | your own systems |
| — | Install the skills (before step 3) | `make install-skills` | no |
| 3 | Account dossiers | `skills/dossier-data-auditor/`, `skills/dossier-builder/` | optional |
| 4 | Compound with GitHub | `foundation/push-to-github.md` | GitHub account |
| 5 | Segment | `skills/win-loss-rewind/` (phase 3) | `ANTHROPIC_API_KEY` |
| 6 | Won / Lost / 10X | `skills/win-loss-rewind/` | `ANTHROPIC_API_KEY` |
| 7 | Qualify, then quantify | `icp.json` + `skills/calibrated-judge/scripts/quantify.py` | no |
| 8 | Score with evidence packets + Jev | `skills/crawford/`, `skills/calibrated-judge/` | Jev key |
| 9 | Ship and iterate | review queue, `check_precision.py`, `wiki/log.md` | no |
| 10 | Write a PVP message | `examples/northwind-demo/pvp-email-draft.md` | no |

The fictional Northwind example runs steps 3 to 10 with no keys, through the
same phase code: `python3 examples/northwind-demo/replay.py` (see `examples/README.md`).

---

## Step 0. Set up and prove it runs

**What it is:** copy the starter into your own project folder, install the
Python packages, and run the offline checks.

**Run:**
```bash
git clone https://github.com/SantaJordan/gtm-engineer-starter ~/gtm
cd ~/gtm
python3 -m pip install -r requirements.txt
make check
```

**Inputs:** Python 3.10 or newer. No API keys.

**Outputs:** five test suites, the offline smoke test (`6/6 steps passed.`) and
the offline replay of the whole method (`13/13 replay steps passed.`).

**Decision rule:** do not go on until `make check` passes. A failure names the
command that failed.

---

## Step 1. Foundation

**What it is:** a short `CLAUDE.md` and a wiki the AI keeps up to date, so every
session starts from what you already know.

**Read:** `foundation/wiki-pattern.md` (5 minutes). It builds on Andrej
Karpathy's LLM Wiki idea.

**Run (from `~/gtm`):**
```bash
cp foundation/template/CLAUDE.md CLAUDE.md
cp foundation/wiki-pattern.md wiki-pattern.md
cp -R foundation/template/wiki wiki
mkdir -p private
```
Then ask the assistant:
> Open CLAUDE.md and interview me to fill in every {{placeholder}}. Ask one
> question at a time. Fill in the systems table last.

**Inputs:** you, for 15 minutes.

**Outputs:** a filled-in `CLAUDE.md`; `wiki/` with `index.md`, `log.md` and an empty `raw/`.

**Decision rule:** move on when the systems table lists every system you will
pull data from, with a join key and a "do not trust" note for each.

---

## Step 2. Connect your context

**What it is:** get your real data (calls, CRM, billing) into `wiki/raw/` so the
assistant can read it. `wiki/raw/` is ignored by git: raw exports never reach
GitHub. The wiki pages the AI writes from them do go to your private repo.

**Read:** `foundation/connect-your-context.md`.

**Run:** export from each system (prefer a CLI or an API script for anything over
about 50 records), save into `wiki/raw/<system>/`, then ask:
> Read everything new in wiki/raw/ and update the wiki following wiki-pattern.md:
> one entity page per customer account, a concept page for any pattern seen in
> three or more accounts, and one line in wiki/log.md per change.

**Inputs:** a CRM export (accounts, deals), call transcripts, invoices or
subscriptions. Even one of the three is a start.

**Outputs:** files in `wiki/raw/`; pages in `wiki/entities/`; an updated `wiki/index.md`.

**Decision rule:** move on when, for every account you care about, you can point
to its revenue, its calls and its outcome in a file, not from memory.

---

## Before Step 3. Install the skills

Steps 3 onward call skills by name (`/dossier-data-auditor`, `/dossier-builder`,
...). Claude Code only finds a skill that sits in a `.claude/skills/` folder, so
link them in once.

**Claude Code (from `~/gtm`):**
```bash
make install-skills
```
This links each folder in `skills/` into `~/gtm/.claude/skills/` (nothing is
copied, so a `git pull` updates them). Prefer copies? `mkdir -p .claude/skills && cp -R skills/* .claude/skills/`.

**Check it worked:** the command prints six `SKILL.md` paths, one per skill. Then
start Claude Code in `~/gtm` (restart it if it was already open), type `/dossier`
and both `/dossier-data-auditor` and `/dossier-builder` should be offered.

**Codex or another agent:** there is nothing to install. Start each step's prompt
with "Read `skills/<name>/SKILL.md` and follow it", for example
"Read `skills/dossier-data-auditor/SKILL.md` and follow it." Check: `ls skills/*/SKILL.md` lists six files.

---

## Step 3. Account dossiers

**What it is:** one file per account (customers AND deals you lost) that joins
every system into a single timeline, with the source of every fact. First audit
the data, then build.

**Run (in Claude Code, from `~/gtm`):**
> /dossier-data-auditor Audit the customer data in wiki/raw/ before a dossier
> build. Our own email domain is <yourdomain.com>.

Then, once the audit gate says PASS:
> /dossier-builder Let's build dossiers for <your company>, customers and lost deals.

The scripted pieces of the audit can also be run directly (from `~/gtm`):
```bash
python3 skills/dossier-data-auditor/scripts/scan.py --data-dir wiki/raw/crm \
    --audit-slug acme-2026-10 --out out/audits/acme-2026-10
python3 skills/dossier-data-auditor/scripts/join_test.py --audit-slug acme-2026-10 \
    --tenant-domain yourdomain.com --output-base out/audits --data-dir wiki/raw/crm
python3 skills/dossier-builder/scripts/validate_schema.py --dossier-dir <dossiers folder> --mode customer
python3 skills/dossier-builder/scripts/validate_schema.py --dossier-dir <lost-deal folder> --mode prospect
```

**Inputs:** the exports from step 2; your own email domain (so your staff are
never matched as customers).

**Outputs:** audit files under `out/audits/<slug>/`; one JSON and one HTML page
per account.

**Keys:** none for scan, joins and schema checks. Domain checks and the web
sample use one web provider (Exa, Firecrawl, OpenWebNinja or Parallel); the skill
asks before using one.

**Decision rule:** move on when (a) the join gate is PASS, or you wrote down why
you override it, (b) both schema checks pass, and (c) you opened three dossiers
and checked each against the source system by hand.

---

## Step 4. Compound with GitHub

**What it is:** put the project in a **private** GitHub repo so the work is backed
up and builds week over week.

**Read and follow:** `foundation/push-to-github.md`. It shows how to review
exactly what will be uploaded before anything leaves your machine.

**Run (from `~/gtm`):**
```bash
git remote rename origin starter          # keep the starter as a second remote
gh repo create <your-company>-gtm --private --source=. --remote=origin
git status                                # read it: no .env, wiki/raw/, private/, CSVs
git add CLAUDE.md wiki-pattern.md wiki/index.md wiki/log.md
git diff --cached --stat                  # exactly what will upload
git commit -m "Project setup"
git push -u origin main
```

**Inputs:** a GitHub account and the `gh` CLI.

**Outputs:** a private repo with your instructions and wiki pages, and none of
your raw data.

**Decision rule:** move on when `git check-ignore -v .env wiki/raw/x private/x`
prints a matching rule for all three, and the repo shows as Private on GitHub.

---

## Step 5. Segment

**What it is:** group your customers into a few **archetypes** (kinds of customer
that bought for the same reason) from the data, without assuming industry or
size up front. It is phase 3 of the Won / Lost / 10X skill.

**First, make THE customer file** (from `~/gtm`). One file, `private/customers.csv`,
feeds steps 5, 6, 7 and 10. Build it from your dossiers (step 3), so the labels and
dates come from billing and calls, not memory:
```bash
python3 skills/win-loss-rewind/scripts/customers_from_dossiers.py \
    --dossiers outputs/dossiers/<slug>/dossiers --out private/customers.csv
```
Columns: `customer_id, company_name, domain, outcome` (CRM: won / lost), `health`
(healthy / churned), `label` (won / lost / 10X, computed), `close_date`,
`first_call_date`, `churn_date`, `lifetime_revenue`, `label_evidence`. The dates
matter: Phase 6 tests each signal against what was knowable on the account's
`close_date` (or `first_call_date` for a lost deal); a row with neither is
"unanchorable" and cannot be tested. If you have no dossiers, ask the assistant:
> Build private/customers.csv with exactly these columns from our CRM and billing
> exports in wiki/raw/. Compute label from billing (10X = lifetime revenue at least
> 10x the median paying account; lost = churned or never closed). Leave a cell
> blank when you do not know it; never guess a date.

See `examples/northwind-demo/customers.csv` for the shape.

**Run phases 0 to 3, then stop** (the skill writes to `skills/win-loss-rewind/outputs/<run-id>/`):
```bash
python3 skills/win-loss-rewind/scripts/run_init.py private/customers.csv --adapter csv --run-id acme-1
# read and approve skills/win-loss-rewind/outputs/acme-1/data_confidence.md
```
Calls, if you are not on Gong: put one markdown file per call in `wiki/raw/calls/`
(format in `skills/win-loss-rewind/scripts/adapters/transcripts_md.py`) and import them.
The import grades each customer line `led` or `unprompted` **provisionally**: read
a line in context before you quote it as unprompted.
```bash
python3 skills/win-loss-rewind/scripts/adapters/transcripts_md.py wiki/raw/calls \
    --run-id acme-1 --seller-domain yourdomain.com
python3 skills/win-loss-rewind/scripts/orchestrate.py --run-id acme-1 --resume-from 2 --stop-after 3
```
With Gong, use `--resume-from 1 --stop-after 3`. Nothing after phase 3 runs until
you approve the archetypes (below).

**Inputs:** `private/customers.csv`; call transcripts.

**Outputs:** `skills/win-loss-rewind/outputs/acme-1/archetypes.md` and
`archetypes.jsonl` (each archetype with its members and which are held out).

**Decision rule:** keep an archetype only if it has at least five customers and
you can say in one sentence why its members bought. To merge or drop, edit
`archetypes.jsonl`: move member records between archetypes, or delete an
archetype (its members are then left out). Keep each member's `holdout` flag as
it is, so the 20% you will test on stays untouched. Then approve and continue:
```bash
python3 skills/win-loss-rewind/scripts/orchestrate.py --run-id acme-1 --resume-from 4 --approve-archetypes
```
Editing `archetypes.jsonl` after approving requires approving again.

---

## Step 6. Won / Lost / 10X

**What it is:** label every customer, then work backward to what was true about
each group *before* they bought.

- **won**: bought and stayed.
- **lost**: churned, or never closed.
- **10X**: exceptional value, measured: lifetime billed revenue at least **10 times
  the median** lifetime revenue of all paying customers in the same data set.
  Compute it from your billing data; do not label by feel.

The skill distrusts your labels until the data backs them. It holds back 20% of
each archetype (the **holdout**), writes hypotheses from the other 80% only, and
keeps a signal only if it also holds on the holdout.

**Run:** the approval command at the end of step 5 runs phases 4 to 7. Then score one prospect:
```bash
python3 skills/win-loss-rewind/scripts/score_prospect.py <prospect-domain> --run-id acme-1
```

**Inputs:** `private/customers.csv`, call transcripts, `ANTHROPIC_API_KEY`, and
one web-evidence key (Firecrawl, Exa, OpenWebNinja or Apify) for enrichment.

**Signals are typed.** Each hypothesis names a field the enrichment parser really
emits and a test for it: "homepage `location_count` ≥ 2", or "a news item whose
snippet matches 'opened its second office'". A numeric field always needs a
threshold, an unknown field is rejected, and a field the parser could not read
is "unknown", never "absent". A **firm** signal is one proven visible before the
account's close date: a dated record that itself matches the test (its URL and
excerpt are kept), or an archived page whose fields, re-extracted by the same
parser, pass the same test. A firm lift also needs accounts where the signal is
provably ABSENT at their close date (an archived page that fails the test). A
search result never proves absence, so a signal seen only in search results ends
as "insufficient evidence", which is what the Northwind replay shows.

**Outputs:** `skills/win-loss-rewind/outputs/acme-1/discriminator.md` and
`.jsonl`: per archetype, the signals that separated good fits from lost ones,
with **lift** on both splits. Lift is how many times more likely an account WITH
the signal is to be a good fit than an account WITHOUT it:
P(good fit | signal) ÷ P(good fit | no signal). Good fit means won, healthy or 10X.
An archetype with no survivor says "no reliable discriminator found", with the
cause and the next action, in the same files.

**How a signal survives:** lift at least 1.5 on the 80%; the holdout lift at least
0.8 times that; and on both splits a check that it is not chance (a confidence
interval on large tables; Fisher's exact test on small ones, p ≤ 0.05 on the 80%
and one-sided p ≤ 0.10 on the holdout). A split with fewer than 10 (80%) or 5
(holdout) labelled customers cannot pass; it reports "insufficient evidence".

**Cost:** the themes phase uses the Anthropic Batch API and prints its estimate
before it submits. At `claude-sonnet-5-5` batch prices ($1 in / $5 out per
million tokens), output costs at most $0.01 per customer.

**Decision rule:** if a signal survived, go to step 7. If the result is **"no
reliable discriminator found"**, that is an answer, not a failure. Read its causes:
`insufficient_evidence` (too few customers per archetype, or no provable absence:
add history, merge archetypes, or find a source that shows the past state),
`not_firm_checked` (passed today's-web filter but never proven historical: run the
archive check), `signal_not_observed` (the enrichment never saw the field: fix the
source), `no_detectable_relationship` (the hypotheses did not separate winners:
go to step 7 with the qualitative evidence from your dossiers, and say so).

---

## Step 7. Qualify, then quantify

**What it is:** turn what you learned into rules, in two passes.
**Qualify** first: is this company the right *kind* of company at all (yes or no)?
**Quantify** second: among qualified ones, which are biggest or most urgent?
Never rank a list you have not qualified.

**Run:** ask the assistant:
> Using private/customers.csv, the lost-deal dossiers and discriminator.md, write icp.json
> with "inclusions" and "exclusions". Under "_provenance", list for every rule the
> account IDs or the surviving signal it came from. Mark exclusions that come from
> what we sell (product scope) separately from exclusions learned from lost accounts.
> Then pick ONE number we can read from each prospect's packet to rank qualified
> rows, and give me the three quantify settings: the packet field, a regular
> expression whose first group captures the number, and a metric name.

See `examples/northwind-demo/icp.json` for the shape (the replay derives it).

**Inputs:** step 6 outputs; your prospect list as `companies.csv` (columns
`name, domain`, optional `city, state, row_id`).

**Outputs:** `icp.json`; the three quantify settings (field, pattern, metric name);
`companies.csv`. Two examples of quantify settings:

| Metric | `--field` | `--pattern` | `--metric-name` |
|---|---|---|---|
| office count | `site.homepage_text` | `(\d+\|one\|two\|three\|four\|five\|six)\s+(?:\w+\s+){0,3}?(?:offices\|locations)` | `offices` |
| provider count | `site.homepage_text` | `(\d+)\s+(?:dentists\|doctors\|providers\|clinicians)` | `providers` |

**Decision rule:** move on when `icp.json` has a non-empty `exclusions` list and
every rule has provenance. With no exclusions, the judge can only answer "maybe".

---

## Step 8. Score with evidence packets + Jev

**What it is:** build one **evidence packet** per prospect (the facts, each with
its source, and an explicit note wherever a fact is missing), then ask **Jev**, a
calibrated judge model, whether each packet fits your ICP. Then rank the accepted
rows with your quantify number.

**Run (from `~/gtm`):**
```bash
# preview: a few packets, their cards and the cost. Show these to the human.
python3 skills/crawford/scripts/build_packets.py --run-dir out/run1 \
    --companies companies.csv --icp icp.json --preview 3
# build every packet (free lanes only by default)
python3 skills/crawford/scripts/build_packets.py --run-dir out/run1 \
    --companies companies.csv --icp icp.json
# score
python3 skills/calibrated-judge/scripts/judge_packets.py \
    --packets out/run1/packets --icp icp.json --out out/run1-scores
# quantify: rank accepted rows by the number you chose in step 7 (pass all three)
python3 skills/calibrated-judge/scripts/quantify.py --scores out/run1-scores/scores.csv \
    --packets out/run1/packets --field site.homepage_text \
    --pattern '(\d+)\s+(?:dentists|doctors|providers|clinicians)' \
    --metric-name providers --out out/run1-scores/shortlist.csv
```
`quantify.py` prints how many accepted rows had no value. If more than a third are
missing, the field or pattern is wrong for your prospects: fix it before ranking.

**Inputs:** `companies.csv`, `icp.json`, and one Jev key in `.env`:

| Setup | Key | Status |
|---|---|---|
| TypeSafe direct (preferred) | `TYPESAFE_API_KEY` from console.typesafe.ai | Supported and tried first. |
| OpenRouter (alternative) | `OPENROUTER_API_KEY` | Same model (`typesafe/jev-1.13`). Verified with live calls on 2026-10-06. |

Check your key: `python3 skills/calibrated-judge/scripts/jev_client.py probe --no-cache`.

**Outputs:** `out/run1/packets/`; `out/run1-scores/scores.csv` (accept / reject /
review / insufficient per row, with reasons); `review_queue.csv` and
`review_cards.md`; `shortlist.csv` (accepted rows ranked, the number's source
quoted, missing values last, never zero).

**Cost:** packets are free on the default lanes. Jev is billed per input token
(TypeSafe lists $0.042 per million); a row costs a fraction of a cent. The scorer
prints a ceiling first and stops at `--max-usd` (default $1).

**Review the top rows:** Jev scores everything. For the small number of
top-ranked rows you are about to send, have your agent review each packet with
subagents inside the tool you already use (Claude Code or Codex), not a separate
cheap API model. Small models like Haiku are not the best at this job. If a step
needs more intelligence, ask your agent to look up, on the day you run it, the
current best models by dollars per unit of intelligence on public benchmarks,
rather than hardcoding one.

**Decision rule:** `insufficient` is not a "no". If more than a third of rows are
`insufficient` or `review` because packets are thin, rebuild with `--enrich`
before you trust the scores. In the Northwind replay, Jev accepted 2 of 6 and
returned `insufficient` for the other 4, including the three that the ICP
excludes: thin one-page packets did not let it confirm an exclusion.

---

## Step 9. Ship and iterate

**What it is:** check the judge on your own rows, send to a small batch, and feed
the results back.

**Run (from `~/gtm`):**
1. **Check the threshold on your rows (free, 30 minutes).** The 0.90 bands are a
   starting point, not a guarantee for your ICP:
   ```bash
   python3 skills/calibrated-judge/scripts/check_precision.py sample \
       --scores out/run1-scores/scores.csv --n 30 --out out/run1-scores/to_label.csv
   # 30 ACCEPTED rows for precision, plus 10 rejected/review rows as a diagnostic
   # fill your_answer (yes / no) for each row, without looking at the judge
   python3 skills/calibrated-judge/scripts/check_precision.py measure \
       --scores out/run1-scores/scores.csv --labels out/run1-scores/to_label.csv
   ```
2. Take the top 20 to 50 rows of `shortlist.csv`. Decide every `review` row yourself.
3. Send (step 10). Record replies and meetings next to each row.
4. Ask the assistant:
   > Add this week's results to the wiki: update each account's entity page, add
   > a synthesis page for what worked and what did not, and log every change.
   > Then tell me which ICP rule, if any, the results contradict.
5. Change `icp.json` if the results say so, and re-run step 8.

**Decision rule:** send only if accept precision on your labelled rows (shown as
k/n with a 95% interval) is at a level you can live with, for example 90% for a
cold send. If it says INSUFFICIENT EVIDENCE (fewer than 10 accepted rows
labelled), score and label more before you trust the threshold. Change one rule at a
time, and only when at least 20 sends point the same way.

---

## Step 10. Write a PVP message

**What it is:** a **PVP** (Permissionless Value Proposition) is a message so
specific and useful that the buyer would pay to receive it. It delivers the value
in the message itself, and every claim points at a fact you can show.

**Run:** for the top row of `shortlist.csv`, ask:
> Write a PVP email for <company>. Use only facts from its packet in
> out/run1/packets/companies/<key>.json and the 10X evidence in private/customers.csv. Quote
> customers only from lines graded `unprompted`. Put a table of every fact and its
> source above the draft. Give the useful thing (a checklist, a number, a finding)
> in the email itself. Under 150 words.

**Inputs:** one packet, your 10X evidence.

**Outputs:** a draft like `examples/northwind-demo/pvp-email-draft.md` (the
replay generates it from its own results).

**Decision rule:** send only if every sentence maps to a row in the fact table,
every customer quote is `unprompted`, and the facts are still true today.

---

## Where to go next

- `TOOLS.md`: the tools and vendors this repo can use, with links.
- `NOT-INCLUDED.md`: what the full version adds.
- Each skill's `SKILL.md`: the details.
