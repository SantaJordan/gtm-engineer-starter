# Phase 3 — Archetype Namer Prompt

You are naming customer archetypes after `scripts/segment.py` has clustered them on Phase 2 signal panels. The clusters are mathematical; your job is to make them legible.

## Why this matters

The lazy ICP definition is "B2B SaaS, 50–1000 employees." The whole point of this skill is to break that. Your archetype names must be operationally specific enough that a BDR can hear the name and immediately see what the customer looks like in the wild. Generic personas ("the SMB buyer", "the enterprise champion") are an automatic failure — kick those back and try again.

## Input per cluster

```
cluster_id: <int>
member_count: <int>
outcome_breakdown: {won: <n>, lost: <n>, healthy: <n>, churned: <n>}
canonical_signals: [
  {feature: <name>, mean: <val>, prevalence: <pct>}, ...
]
top_3_member_companies: [{name, domain, one_line_description}]
disambiguating_signals_vs_other_clusters: [list]
```

## Your output per cluster

```yaml
archetype_id: <int>
archetype_name: "<name>"          # 3–7 words. Operational. Specific.
one_liner: "<sentence>"            # 1 sentence. What you'd see if you walked in.
canonical_features:               # The 3–5 features that *most* distinguish this cluster
  - <feature 1>
  - <feature 2>
  - <feature 3>
why_this_cluster_is_different_from_others:
  - "Unlike Archetype N, members here <specific difference>"
spot_check_quote: "<one phrase a member of this cluster might literally say>"
```

## Naming style — what good looks like

**Good archetype names** (specific, observable):

- "The two-state legal-ops shop with no in-house engineer"
- "The post-SOC2 health-system compliance team"
- "The owner-operated multi-location restaurant group"
- "The municipal-contracts construction firm 5 years post-license"
- "The single-agency insurance broker absorbing a second book"

**Bad archetype names** (banned — these are what lazy ICP looks like):

- "Mid-market SMB" — meaningless
- "Growth-stage SaaS" — describes nothing operational
- "Enterprise customer" — size is not an archetype
- "B2B services company" — could be 100k companies
- "Healthcare vertical" — too broad to be useful
- Anything with "fast-growing", "innovative", "modern"

If you find yourself reaching for "growing", "modern", "tech-forward", or any descriptor that could apply to >10,000 companies, you're failing. Go back to the canonical signals and find the *operational* fact that distinguishes this cluster.

## Hard rule

Archetype names that don't pass the **"BDR could spot one in a list of 100 companies in 30 seconds"** test are rejected. If the cluster's canonical signals don't support an operationally specific name, the cluster might be noise — flag it as `archetype_quality: low`, list the cluster as a candidate-for-merge in the output, and let `compose_discriminator.py` decide whether to drop it.

## Output format

A single YAML block per cluster. Concatenate all clusters into `outputs/<run-id>/archetypes.md` with a markdown summary table at the top:

```
| ID | Name | Members | Won/Lost/Healthy/Churned | Quality |
|----|------|---------|--------------------------|---------|
```

## Tone

Direct. No "this archetype represents..." filler. Just say what they are.
