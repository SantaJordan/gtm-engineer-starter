# Lift and Holdout — Statistical Method

Phase 6 lives or dies on getting this right. The whole point of the skill is to ship discriminators that hold up on data the system has never seen. If we overfit to the training set, the discriminator burns BDR cycles in production.

## Lift

For each hypothesis × archetype:

```
P(good_fit | signal)   = (good_fit AND signal) / signal
P(good_fit | no signal) = (good_fit AND no signal) / no signal

lift = P(good_fit | signal) / P(good_fit | no signal)
```

Where `good_fit` is `outcome ∈ {won, healthy, 10x}` and `not_good_fit` is `outcome ∈ {lost, churned}` (the verified outcome from `scripts/wlr_schema.py`). The 2x2 cells, as `compute_lift_with_ci()` names them:

```
                 signal   no signal
    good_fit       a          c
    not_good_fit   b          d

lift = (a / (a+b)) / (c / (c+d))
```

Lift is a probability ratio: P(good fit | signal) divided by P(good fit | no signal). It is NOT the odds ratio (a·d)/(b·c); the odds ratio is reported beside it and never replaces it.

Lift of 1.0 means the signal does nothing. Lift of 3.0 means a customer with this signal is 3× more likely to be a good fit than one without. We want lift ≥ 1.5 to bother shipping.

## Confidence interval

Bare lift values are misleading at small sample sizes. Compute a 95% CI using the log-lift Wald approximation:

```
log_lift = ln(lift)
se = sqrt(1/a - 1/(a+b) + 1/c - 1/(c+d))   # cells as in the table above
ci_lower = exp(log_lift - 1.96 * se)
ci_upper = exp(log_lift + 1.96 * se)
```

Reject any signal whose `ci_lower < 1.2` regardless of point estimate. Below 1.2 means the signal might just be noise.

When any cell is below 5 (or a margin is zero), the Wald interval is unreliable, so the table goes to **Fisher's exact test**. The effect measure stays the same probability-ratio lift; significance comes from Fisher instead of the CI:

- train: two-sided Fisher p ≤ 0.05
- holdout: one-sided Fisher p ≤ 0.10 (it replicates a direction fixed on train)

A split with fewer than 10 (train) or 5 (holdout) labelled, observed customers cannot pass at all: it reports "insufficient evidence". These constants live in `scripts/backcast_validate.py` (`ALPHA_TRAIN`, `ALPHA_HOLDOUT`, `MIN_TRAIN_N`, `MIN_HOLDOUT_N`).

## Holdout protocol

At Phase 3 (segmentation), hold out 20% of each archetype's members at random. Persist the holdout indicator in `archetypes.jsonl` so every downstream phase sees the same split.

In Phase 6:

1. Compute `train_lift` on the 80% training set per archetype.
2. Compute `holdout_lift` on the 20% holdout set per archetype.
3. **Holdout replication test:** the signal survives only if `holdout_lift >= 0.8 * train_lift` AND the holdout passes its significance check (CI lower ≥ 1.2 on a large table, one-sided Fisher p ≤ 0.10 on a small one).
4. If no signal survives for an archetype, the result is an explicit **"no reliable discriminator found"** with its causes counted (`insufficient_evidence`, `signal_not_observed`, `no_detectable_relationship`).
5. Signals that fail the holdout test are logged to `validated_signals.jsonl` with `survived: false` for traceability, but only `survived: true` signals appear in Phase 7.

## Why we don't do K-fold CV

K-fold gives better statistical power but undermines the "this is the holdout that we never touched" rhetorical strength when handing the discriminator to a CRO. Single 80/20 holdout is conservative but defensible. If a sophisticated customer pushes back on holdout size, document it — don't trade the rhetoric for a 2% lift improvement.

## Anti-overfitting checks

Signals that look strong on train are often artifacts of how the data was collected. Specific failure modes to check explicitly:

### Data artifact

Some "signals" only exist for some customers because the data was collected for them. Example: `salesforce.last_activity_date` is null for customers without an SDR assigned, who happen to be the smaller, churn-prone ones. The signal says "customer has SDR-logged activity → fit" — but it's not the activity, it's the SDR assignment that's the discriminator. Useless for cold prospects.

**Check:** for every signal, is there a population reason this signal could be missing differentially? If yes, flag and downgrade.

### Outcome leakage

A "predictive signal" that includes information generated *after* the deal closed. Common in healthcare data: a CMS rating in the dataset reflects the rating today, not the rating at the time of solution-search. Backcasting to `close_date - lead_time_days` doesn't help if the data field gets overwritten in place.

**Check (mechanized in Phase 6.5):** every source is classified in `references/source-temporal-classification.json` as point-in-time-record vs current-state-only; `outcome_leakage_check()` flags any current-state-only source whose hypothesis earned zero firm-dated evidence in the firm pass. This is no longer a judgment call — it's a computed flag.

## Two-pass firm lift (Phase 6.5 — the time-travel gate)

Lift as defined above is computed twice:

1. **Provisional pass** — signal presence read from the *current* Phase 2 panel. Cheap, runs for every hypothesis. This is a **filter only**: it decides which hypotheses earn expensive verification (top-K per archetype by provisional lift, default 8, plus any `--force-firm-check` ids). A provisional number never ships.
2. **Firm pass** — for each (hypothesis, customer) cell of the candidates, evidence is dated against the customer's **reference date** (`scripts/reference_dates.py` — close/signing date per outcome) and tagged:
   - **[ARCHIVED-BEFORE]** — the latest archive.org capture at-or-before the reference date, re-parsed by the same Phase 2 parser, passes the hypothesis's typed predicate. Firm. A capture that fails the predicate is a provable absence. Sources whose fields cannot be re-extracted from an archived page stay unknown; a text substring never certifies a signal.
   - **[RECORD-DATED-BEFORE]** — a record that ITSELF satisfies the signal's predicate has an intrinsic date (+ the source's publication-lag haircut) that precedes the reference date. Firm, with the record's URL, date and excerpt kept as evidence. An old record that does not satisfy the predicate proves nothing, and a source with no satisfying record, or only satisfying records dated after the reference, is unknown, not absent (a search result set cannot prove absence). A hypothesis with no provably absent cell is `unverifiable`: presence alone is not a contrast, so no firm lift exists.
   - **[RETRO-ASSUMED]** — only today's web observed. Never firm.
   Firm lift uses **only firm-tagged cells**; [RETRO-ASSUMED] and unchecked cells are *unknown* — excluded from the 2×2 table, never coerced to signal-absent (coercing inconclusive to negative would bias lift downward for exactly the hardest-to-archive sources). Train **and** holdout both re-earn their lift on firm evidence — a holdout that replicates only on retro-assumed cells has replicated nothing.
3. **Statistical floor:** if firm-present cells < 5 or firm coverage < 30% of checked cells, the signal's status is **`unverifiable`** — no firm lift number is computed at all. Unverifiable ≠ refuted: the report must always distinguish "killed for weak lift" from "killed for unprovability" (an archive-coverage statement, not an absence claim).

Statuses: `firm` (ships to Phase 7) · `killed_at_firm` · `unverifiable` · `provisional_only` (cleared the cheap bar, never firm-checked) · `killed_at_provisional`. Per-cell evidence lands in `wayback_evidence.jsonl`.

**Known bias, accepted:** a hypothesis whose *provisional* proxy is weak but whose *true historical* signal is strong never reaches the firm pass (the filter can't see the past either). Escape hatch: `--force-firm-check <hypothesis_id>`. Coverage bias is real too — small companies are barely archived — which is why coverage is reported per signal and unverifiability is labeled, not hidden.

### Selection effect on the customer set

If the customer roster is missing churned-on-day-7 customers (they never made it into the CRM), the discriminator will look better than it really is. Flag this in Phase 0 if the user describes their data shape that way.

**Check:** ask the user in Phase 0 whether very-short-tenure churns are represented. If no, document the limitation in `discriminator.md`.

### Holdout bias from clustering

If clustering used outcome-correlated features, the holdout split is contaminated. Mitigation: cluster only on signal panels (Phase 2 outputs), never on outcomes.

**Check (built into segment.py):** `assert no outcome columns in clustering features`.

## Reporting in `validated_signals.md`

Per surviving signal:

```markdown
### Signal: <description>

- Source: <source>
- Queryable as: `<API field / SQL>`
- Lead time: <N> days
- FIRM Train lift: <X.XX> (95% CI <a> – <b>, n=<n>)
- FIRM Holdout lift: <Y.YY> (95% CI <c> – <d>, n=<m>)
- Replication ratio: <holdout/train>
- Firm evidence: [ARCHIVED-BEFORE]+[RECORD-DATED-BEFORE] present=<n>, provably-absent=<n>, [RETRO-ASSUMED]/unknown=<n> (coverage <p>% of checked cells)
- Provisional lift (today's-web filter, for reference): <Z.ZZ>
- Anti-overfitting: data_artifact: <ok|flag>, outcome_leakage: <ok|FLAG + detail> (source class: <class>)
- Anti-matches: [list]
```
