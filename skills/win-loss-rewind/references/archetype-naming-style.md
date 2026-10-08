# Archetype Naming Style

Archetype names are read by humans who need to recognize a customer in the wild. They are read by BDRs scrolling through prospect lists. They appear at the top of `discriminator.md`. If the names are mush, the discriminator is mush.

## Five rules

1. **Operational over demographic.** What the company *does* day-to-day, not what's on its LinkedIn page.
2. **Specific over general.** Reach for the operational fact that distinguishes this cluster from the next, not the one that describes a thousand companies.
3. **Short over comprehensive.** 3–7 words. If it takes 10, the cluster isn't crisp.
4. **Concrete over abstract.** "Two-state legal-ops shop" beats "the SMB legal services buyer."
5. **No marketing adjectives.** No "growth-stage", "innovative", "modern", "tech-forward". Banned without exception.

## Templates that work

- "The [size descriptor that's operational] [vertical-specific entity type] [conditioning fact]"
  - "The two-state legal-ops shop with no in-house engineer"
  - "The single-location restaurant group post-100-reviews-per-month"
- "The [vertical] [role/team] [moment in lifecycle]"
  - "The post-SOC2 SaaS compliance team"
  - "The 5-years-post-license municipal-contracts construction firm"
- "The [operational pattern] [vertical entity]"
  - "The owner-operated multi-location restaurant group"
  - "The book-absorbing single-agency insurance broker"

## Templates that fail

- "The [size band] [vertical] customer" — too generic
- "The growth-stage [anything]" — banned
- "The [vertical] vertical" — meaningless
- "The [persona] buyer" — describes a person, not a cluster
- "Mid-market [anything]" — banned

## Bench test: the BDR walk

Read the archetype name aloud. Imagine handing it to a BDR with no context, alongside a list of 100 prospect company names + websites + LinkedIn pages. Could the BDR identify which 5 are members of this archetype in 30 seconds of looking at each one?

- "The two-state legal-ops shop with no in-house engineer" — yes. They'd check the LinkedIn page for ops/legal titles, scan website for engineering team, check state filings for two-state operations.
- "Mid-market SaaS" — no. Could be 50,000 companies.
- "The growth-stage compliance buyer" — no. "Growth-stage" is meaningless and "compliance buyer" describes anyone in compliance.

If the name fails the bench test, regenerate.

## What `one_liner` looks like

The `one_liner` in `archetype-namer.md` output is a single sentence describing what you'd see if you walked into the office. Examples:

- "Three legal admins, one part-time COO, paper compliance binders, no internal engineering function — they hire developers off Upwork when they need anything custom."
- "Compliance officer plus two ops leads, post-SOC2 audit fatigue, every meeting starts with 'we said we'd automate this last quarter and didn't get to it.'"
- "Owner runs four locations from his phone, GM at each location, no salaried marketing — they grow by reviews and word of mouth."

If the one_liner could describe any company, it failed. If it tells you *what these companies are like to work with*, it worked.

## When the cluster doesn't deserve a name

If `scripts/segment.py` produced a cluster that doesn't have a clean operational story, the agent's correct move is to mark `archetype_quality: low` and surface it as a candidate for merging or dropping — not to produce a fake-specific name. Honest is better than ornate.
