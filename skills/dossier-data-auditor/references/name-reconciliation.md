# Name reconciliation — brand vs legal vs DBA (W1b)

> Loaded by `scripts/name_cluster.py` and used at W1b. The job: detect when the same company appears under multiple name variants in the data, decide which variant is canonical for display vs which is canonical for legal context, and surface ambiguous clusters for human resolution.

## The problem

Real B2B data has the same company under variants like:

```
Apple Inc., a California corporation        →  legal_entity
Apple Inc.                                   →  legal_short
Apple Computer Inc.                          →  historical_legal (pre-2007)
Apple                                        →  brand
Apple, Inc.                                  →  legal_with_comma
APPLE INC                                    →  all_caps_variant
```

These are usually 5 separate rows in SFDC (or 3 rows with bad merges that lost their lineage). The dossier-builder would otherwise produce 5 dossiers labeled "Apple variants" and the live call collapses.

## Clustering algorithm

1. **Normalize names**:
   - Lowercase.
   - Strip corporate-suffix stopwords: `inc`, `llc`, `llp`, `pc`, `pa`, `pllc`, `ltd`, `lp`, `corp`, `company`, `co`, `the`, `gmbh`, `s.a.`, `s.a.s`, `pty`, `pte`, `bv`, `nv`, `kk`, `ag`, `oy`, `ab`, `as`.
   - Strip punctuation (commas, periods, quotes).
   - Collapse whitespace.

2. **Block-cluster** within each first-3-token block (e.g., all `apple-*` go in one block; doesn't scale to N² across the corpus).

3. **Pairwise within block**: token-set Jaccard ≥ 0.85 → candidate match.

4. **Cross-reference against domain**: if all members of the candidate cluster have website fields pointing to the same registered-domain root (eTLD+1), the cluster is real. If they disagree by registered root, surface the disagreement — they might not be the same entity.

5. **Optional API confirmation** for ambiguous clusters (low Jaccard or domain disagreement): Blitz companies search per variant + OpenWebNinja AI Mode cross-check. If both APIs resolve each variant to the same LinkedIn company URL → same entity.

## Classification within a cluster

After clustering, classify each variant:

| Class | Pattern |
|---|---|
| `legal_entity` | Contains "Inc.", "LLC", "Corp.", or "a [State] corporation" |
| `legal_short` | Contains "Inc." or "LLC" with no state qualifier |
| `historical_legal` | Legal form known to be deprecated (e.g., "Apple Computer Inc." after 2007) |
| `brand` | No corporate suffix, no all-caps, no punctuation |
| `dba` | Pattern "X dba Y" or "X doing business as Y" |
| `all_caps_variant` | All-caps version (SFDC import artifact) |
| `legal_with_comma` | "Apple, Inc." pattern |

The recommended `display_name` is the `brand` form (if present, otherwise the shortest legal form). The recommended `legal_name` is the `legal_entity` form (if present, otherwise the most complete legal_short).

## Conglomerate-style ambiguity

Some "clusters" are NOT the same entity. An invented example:

- "Globex" (the group)
- "Globex Pharma" (a national subsidiary)
- "Globex Health" (a separate subsidiary)
- "Globex Holdings AG" (the parent)
- "Globex Agri" (yet another subsidiary)

These will pass naïve Jaccard clustering but are separate billing/legal/contract entities. The auditor must:

1. Test whether the domain roots disagree (e.g., `globex.example` vs `globex-pharma.example` vs `agri.globex.example`).
2. If domains disagree → surface as `AMBIGUOUS_CLUSTER` with each variant treated separately.
3. Optionally call Blitz subsidiaries / DiscoLike `/subsidiaries` to detect parent/child relationships explicitly — that's a W2c (multi-domain consolidation) signal, not a W1b cluster collapse.

## Surface to the human

W1b pause when ≥1 cluster has multiple candidate canonical names OR domain disagreement within cluster:

> "5 accounts look like 'Apple' variants:
>  - Apple Inc., a California corporation
>  - Apple Inc.
>  - Apple Computer Inc.
>  - Apple
>  - APPLE INC
>
> All resolve to apple.com / linkedin.com/company/apple. Treat as one entity?
> [yes — merge / no — keep separate / show me the 5 records first]"

## Output

`name_clusters.json`:

```json
{
  "clusters": [
    {
      "cluster_id": "cl_0042",
      "members": [{"account_id": "001A...", "variant": "Apple Inc., a California corporation", "class": "legal_entity"}, ...],
      "recommended_display_name": "Apple",
      "recommended_legal_name": "Apple Inc.",
      "evidence": ["jaccard_0.91", "shared_domain_apple.com", "blitz_same_linkedin_apple"],
      "verdict": "MERGE_CANDIDATE | AMBIGUOUS | NO_MERGE",
      "human_decision": null
    }
  ]
}
```

This file feeds `recommended_engagement_overrides.json:account_name_resolutions` and `name_clusters` at W7. The dossier-builder uses `display_name` for dossier titles and `legal_name` for billing-context display.
