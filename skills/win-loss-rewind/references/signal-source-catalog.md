# Signal Source Catalog

Every Phase 2 enrichment source × the signals it can produce × concrete query patterns. Phase 5 hypothesis generators consume this when proposing where to look.

If a source's API key isn't set, signals from that source are skipped (not fabricated).

## Firecrawl

`FIRECRAWL_API_KEY` required. Docs: firecrawl.dev.

| Signal | Method | What to extract |
|--------|--------|-----------------|
| Total page count of company site | `/map` | URL count |
| Has careers page | `/map` | URL filter `careers` or `jobs` |
| Has pricing page | `/map` | URL filter `pricing` |
| Has investor page | `/map` | URL filter `investors` |
| Blog cadence | `/crawl` careers/blog | post timestamps |
| Specific role being hired | `/scrape` careers page + `/extract` schema | titles, departments |
| Pricing tier listed | `/scrape` pricing page + `/extract` | tiers, prices |
| Compliance/regulatory page exists | `/map` | URL filter `compliance|hipaa|soc2|gdpr` |
| Press release on partnership X | `/search` site-restricted to domain | match keyword |
| Site age (rough) | `/scrape` homepage | copyright year, footer |

## EXA

`EXA_API_KEY`. Docs: exa.ai.

| Signal | Method | Example query |
|--------|--------|---------------|
| Press appearance about company X | `web_search` neural | `"<company>" announcement` |
| Recent partnership / launch | `web_search` neural + 6mo filter | `"<company>" partnership 2026` |
| Industry-specific evidence | `deep-reasoning` with outputSchema | "what does this company do — give me evidence of $X scale" |
| Peer customer published case study | `findSimilar` from a known customer URL | filter `category=company` |
| Research paper or whitepaper authored by company | `web_search` | `"<company>" whitepaper OR "research paper"` |
| Substack / blog mentions | domain wildcards | `*.substack.com` + name filter |

## OpenWebNinja

`OPENWEBNINJA_API_KEY`. Direct API at `api.openwebninja.com`. Docs: openwebninja.com.

| Signal | Endpoint | Output |
|--------|----------|--------|
| Google Maps panel for company | `local-business` | Hours, location, phone, photos count, rating |
| Real owner identity (vs registered name) | `business-owners` | Name, role |
| AI-mode "how big is this company really" | `ai-mode` | Free-text estimate |
| Website contacts | `website-contacts` | Emails, phones, socials |

Cost: $0.002/query.

## Apify

`APIFY_TOKEN`. Direct REST at `https://api.apify.com/v2`. Docs: docs.apify.com.

| Signal | Actor | Output |
|--------|-------|--------|
| LinkedIn employee count | `anchor/linkedin-company-detail` | `employeesCount`, `employeesAtRange` |
| LinkedIn employee role mix | `curious_coder/linkedin-company-employees` | array of profiles with titles |
| Recent leadership hire | same | filter by tenure < 6 mo |
| G2 review velocity | `omkar-cloud/g2-product-scraper` | review timestamps |
| Instagram cadence | `apify/instagram-scraper` | post frequency |
| Full site content for entity-specific extraction | `apify/website-content-crawler` | structured pages |

**Important:** Apify *server-side* scrapers (which run in Apify's cloud) are fine. Do NOT use any tool that scrapes LinkedIn via your local browser session/cookies — that violates LinkedIn's TOS and is fragile. Stick to actors that use Apify's own infrastructure.

## Serper — ⚠️ DEPRECATED (use OpenWebNinja)

> Serper.dev is not used here. Use the `openweb-ninja` skill (Search-Light / AI Mode) for these signals. Endpoints below kept for legacy reference.

`SERPER_API_KEY`. SKILL: `skills/_archive/serper/` (archived — use `openweb-ninja` instead). Cost: $0.001/query.

| Signal | Endpoint | Output |
|--------|----------|--------|
| Recent news mention | `/news` with date filter | headline, date, URL |
| Autocomplete intent | `/autocomplete` with company name | what people search alongside |
| PAA (people also ask) | `/search` PAA section | exact questions |
| Industry/topic demand | `/autocomplete` 26 alphabetical | search demand patterns |

## Optional: vertical-specific data warehouses

The skill does not bundle any specific public-records data warehouse — you wire your own if you have one. Common patterns for two verticals:

### Construction / contractors / building permits

If you subscribe to a permits-and-contractors data warehouse, the kinds of signals that tend to discriminate:

| Signal | Typical table / field |
|--------|------------------------|
| Permit filed by entity | `permits.entity_id`, `permits.file_date` |
| Permit type | `permits.type_code` (filter solar, plumbing, foundation, etc.) |
| Permit value | `permits.estimated_value` |
| Contractor license status | `contractors.license_status` (active/expired/suspended) |
| New contractor at entity | recent `contractors.licensed_at` |

Wire as a Snowflake / Databricks / Postgres connection in `enrich_panel.py`. Only loaded if the operator's environment has the connection configured.

### Healthcare (CMS / NPI / HRSA)

If you have access to public-records healthcare warehouses:

| Signal | Typical source |
|--------|----------------|
| CMS overall star rating at a date | CMS Care Compare snapshot |
| NPI provider count at entity over time | NPI registry historical |
| HRSA grant funding | HRSA grants database |

Same pattern: wire your own connection if you have one. None are required.

### How to add a new source

In `scripts/enrich_panel.py`, add a function `_pull_<source>(customer)` that returns `{ok: bool, data: dict | None, error: str | None}`. Register it in the source dispatch dict. Document the queryable fields in this catalog so Phase 5 hypothesis generators know they're available.

## State open data portals

Per-state. No universal API. Use Firecrawl for portals + OpenWebNinja (Search-Light) to discover them.

Common signals:
- Business registry filings (state Secretary of State portals)
- Professional license actions (state licensing board sites)
- Court filings (state PACER-equivalents)
- Open government grant data (state data.gov mirrors)

## Cross-source patterns

### Contradiction detection
```
{
  "linkedin_employee_count": 50,
  "openweb_ninja_ai_mode_size": "1-10 employees",
  "contradiction_signal": true
}
```
Contradictions are themselves signals — usually pointing to org-chart messiness or stealth growth.

### Temporal absence
```
{
  "firecrawl_careers_page_present": true,
  "firecrawl_jobs_posted_last_18mo": 0,
  "absence_signal": "company has career page infrastructure but no postings in 18 months"
}
```

### Combination via shared timestamp window
```
{
  "vertical_warehouse_event_2024_q3": true,
  "linkedin_role_added_2024_q4": "operations manager",
  "combined_signal": "warehouse event + ops hire within 2 quarters"
}
```
