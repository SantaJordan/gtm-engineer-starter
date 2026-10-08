# GTM Engineer Starter

A starter kit for go-to-market (GTM) engineering with Claude Code or Codex: find
out why your best customers bought, turn that into rules, and score a prospect
list against those rules with evidence you can show.

It is the free, open-source version of the method Jordan Crawford teaches at
[Blueprint](https://blueprintgtm.com). It ships six skills, a project template,
and one fictional worked example that runs end to end with no API keys.

**New here? Open [START-HERE.md](START-HERE.md).** It is the method in order, for
people and for AI agents.

## What you can do with it

1. **Build account dossiers.** Join your CRM, calls and billing into one timeline
   per account, with the source of every fact.
2. **Label Won / Lost / 10X.** Work backward from which customers stayed, which
   left, and which brought in at least 10 times the median customer's revenue, to
   what was true before they bought.
3. **Write your ICP as rules.** Inclusions and exclusions, each traced to a real
   account. ICP means ideal customer profile: the kind of company that buys and stays.
4. **Score a list with evidence.** One evidence packet per prospect, judged by
   Jev, a calibrated model that answers with probabilities. Unsure rows go to a
   human review queue instead of being guessed.
5. **Write a PVP message.** A message so specific the buyer would pay to read it,
   with every claim tied to a source.

## Quick start

```bash
git clone https://github.com/SantaJordan/gtm-engineer-starter ~/gtm
cd ~/gtm
python3 -m pip install -r requirements.txt
make check
```

`make check` runs every test suite, an offline smoke test, and an offline replay
of the whole method on a fictional company. It needs no API keys and spends
nothing. You should see `6/6 steps passed.` and `13/13 replay steps passed.`

## What is in the box

| Folder | What it is |
|---|---|
| `START-HERE.md` | The method, step by step, with commands and decision rules |
| `foundation/` | The wiki pattern, how to connect your data, how to keep it in a private GitHub repo, and a project template |
| `skills/dossier-data-auditor/` | Checks your data before a dossier build: joins, domains, revenue columns |
| `skills/dossier-builder/` | Builds per-account dossiers across every system |
| `skills/win-loss-rewind/` | Won / Lost / 10X: finds the signals that separate good customers from bad |
| `skills/crawford/` | Builds evidence packets for a list (the packet builder from the Crawford research agent) |
| `skills/calibrated-judge/` | Scores packets with Jev; writes scores and a human review queue |
| `skills/wiki-lint/` | Weekly health check for your wiki |
| `examples/` | A fictional company, Northwind, run end to end, plus the acceptance check |
| `TOOLS.md` | Tools and vendors, with links |
| `NOT-INCLUDED.md` | What the paid version adds |
| `llms.txt` | A file index for AI agents |

## Using the skills

**Claude Code:** each folder in `skills/` has a `SKILL.md` with a name and
description. From your project folder run `make install-skills`, which links every
folder into the project's `.claude/skills/` (or copy them there, or into
`~/.claude/skills/` for every project). It prints six `SKILL.md` paths when it
worked. Then ask for a skill by name, for example `/dossier-data-auditor`. Keep
`crawford/` and `calibrated-judge/` side by side: the judge finds the packet
builder there. START-HERE.md walks through this just before Step 3.

**Codex or another agent:** nothing to install. Point it at `START-HERE.md` and
`llms.txt`, and start a step with "Read `skills/<name>/SKILL.md` and follow it".
Every `SKILL.md` is plain markdown, and every script runs from the command line.

## Keys

Nothing is needed for `make check`. For real work, copy `.env.example` to `.env`
and fill in only what you use. The two that matter most:
- a **Jev key** for scoring: `TYPESAFE_API_KEY` (preferred) or `OPENROUTER_API_KEY`;
- `ANTHROPIC_API_KEY` for Won / Lost / 10X.

`.env` is gitignored. Do not commit keys.

## Your data stays yours

This repo holds only fictional data. When you add your own, keep the copy in a
**private** repository. `.gitignore` already keeps `.env`, raw exports
(`wiki/raw/`, `private/`), customer CSVs at the root and generated runs out of
git. GitHub, your AI assistant's provider and any API you configure still receive
what you send them. See `foundation/push-to-github.md`.

## License

MIT. Copyright 2026 Jordan Crawford / Blueprint. See `LICENSE`.

The wiki pattern builds on Andrej Karpathy's
[LLM Wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)
and Jacob Dietl's
[gtm-context-os-quickstart](https://github.com/jacob-dietle/gtm-context-os-quickstart).
Jev is a model from TypeSafe, used through its API; no TypeSafe code is included.
