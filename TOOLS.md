# Tools

Everything this repo can use, what it is for, and where to get it. Only the first
section is needed to run the demo. Everything else is optional: bring the one or
two you already pay for.

## Needed to run the demo

| Tool | What it is for | Link |
|---|---|---|
| Python 3.10+ | runs every script | https://www.python.org/downloads/ |
| Claude Code | the AI coding agent the skills are written for | https://claude.com/claude-code |
| or Codex | another coding agent; reads `START-HERE.md` and `llms.txt` | https://openai.com/codex |
| Git + GitHub CLI (`gh`) | private backup of your project | https://cli.github.com |

## Scoring (step 8)

| Tool | What it is for | Key | Link |
|---|---|---|---|
| TypeSafe (Jev) | the calibrated judge; preferred route | `TYPESAFE_API_KEY` | https://console.typesafe.ai |
| OpenRouter | the same Jev model through another provider | `OPENROUTER_API_KEY` | https://openrouter.ai |

Self-hosted alternatives exist if you cannot use a hosted model: zero-shot
classifiers such as GLiClass (https://github.com/Knowledgator/GLiClass) and
DeBERTa-based NLI zero-shot models on Hugging Face (https://huggingface.co/models?pipeline_tag=zero-shot-classification).
They are not wired into this repo, and you would need your own labelled set to
set thresholds.

## Claude models (step 6)

| Tool | What it is for | Key | Link |
|---|---|---|---|
| Anthropic API | Won / Lost / 10X: themes, segments, hypotheses | `ANTHROPIC_API_KEY` | https://console.anthropic.com |

Model IDs live in `skills/win-loss-rewind/scripts/wlr_config.py`
(defaults `claude-sonnet-5-5` and `claude-opus-5-5`).

## Web evidence (bring any one)

| Tool | What it is for | Key | Link |
|---|---|---|---|
| Firecrawl | render and read web pages | `FIRECRAWL_API_KEY` | https://firecrawl.dev |
| Exa | web search built for AI | `EXA_API_KEY` | https://exa.ai |
| OpenWebNinja | Google search data (data auditor, Won/Lost/10X enrichment) | `OPENWEBNINJA_API_KEY` | https://www.openwebninja.com |
| DataForSEO | Google Maps listings for the packet builder's paid Maps lane ($0.002/request, Live) | `DATAFORSEO_LOGIN`, `DATAFORSEO_PASSWORD` | https://dataforseo.com |
| Parallel | web research API | `PARALLEL_API_KEY` | https://parallel.ai |
| Apify | ready-made scrapers | `APIFY_TOKEN` | https://apify.com |

## Optional enrichment (data auditor, packet builder)

| Tool | What it is for | Key | Link |
|---|---|---|---|
| Blitz | person and company profiles (`--profiles` lane) | `BLITZ_API_KEY` | https://blitz-api.ai |
| Groq | fast, cheap structured extraction with open models | `GROQ_API_KEY` | https://groq.com |
| Perplexity | a second opinion on ambiguous domains | `PERPLEXITY_API_KEY` | https://www.perplexity.ai |
| Linkup | web search API | `LINKUP_API_KEY` | https://www.linkup.so |
| DiscoLike | company domain matching | `DISCOLIKE_API_KEY` | https://discolike.com |
| MillionVerifier | email verification | `MILLIONVERIFIER_API_KEY` | https://www.millionverifier.com |

## Free public sources the code uses

| Source | What for | Link |
|---|---|---|
| NPPES NPI Registry | US healthcare provider records (packet registry lane) | https://npiregistry.cms.hhs.gov |
| Internet Archive Wayback Machine | what a company's site said in the past (Won / Lost / 10X) | https://web.archive.org |

## Your own systems

| Tool | What for | Keys |
|---|---|---|
| Gong (or any call recorder with exports) | call transcripts | `GONG_ACCESS_KEY`, `GONG_SECRET_KEY`, `GONG_BASE_URL` |
| Salesforce (or any CRM with exports) | accounts, deals | `SF_USERNAME`, `SF_PASSWORD`, `SF_SECURITY_TOKEN`, `SF_DOMAIN` |

A CSV export works everywhere: every skill has a CSV path.

## Nice to have

| Tool | What for | Link |
|---|---|---|
| Obsidian | browse the wiki and its `[[links]]` as a graph | https://obsidian.md |
