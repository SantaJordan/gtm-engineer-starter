# Hard Gate Validator

**Purpose:** Five mandatory gates that AUTO-REJECT invalid patterns. Apply to every segment and PVP before proceeding.

**Rule:** Fail ANY gate = AUTO-DESTROY. No exceptions. No "but in this case..." rationalizations.

**CRITICAL DEPENDENCY:** Gate 5 requires Product Value Analysis output from Wave 0.5 (valid/invalid pain domains)

---

## The Five Hard Gates

```
INPUT: Segment concept OR PVP message + Product Value Analysis from Wave 0.5

→ GATE 1: Horizontal Disqualification
   "Is the ICP operationally specific?"
   FAIL → AUTO-DESTROY

→ GATE 2: Causal Link Constraint
   "Does the signal PROVE the pain?"
   FAIL → AUTO-DESTROY

→ GATE 3: No Aggregates Ban
   "Are statistics company-specific?"
   FAIL → AUTO-DESTROY

→ GATE 4: Technical Feasibility Audit
   "Can we actually detect this data?"
   FAIL → AUTO-DESTROY

→ GATE 5: Product Connection (NEW - CRITICAL)
   "Does the product solve this pain?"
   FAIL → AUTO-DESTROY (fundamental flaw, no revision)

→ ALL PASS → VALIDATED ✓ (proceed to next phase)
```

---

## Gate 1: Horizontal Disqualification

**Question:** Is the ICP operationally specific, or could it be "any B2B company"?

### FAIL Conditions (Auto-Reject if ANY are true)

The segment/message FAILS if the ICP can be described as:
- "Any B2B company"
- "SMB" or "Enterprise" (without operational specificity)
- "Growing companies"
- "Funded companies"
- "Tech companies" (generic)
- "SaaS companies" (generic)
- "Sales teams" (without licensing/regulatory context)
- "Companies with [department]" (e.g., "companies with HR teams")

### PASS Conditions

The segment/message PASSES if ICP includes:
- Specific industry with regulatory oversight
- Operational context (processes, workflows, compliance requirements)
- Specific pain tied to observable data

### Examples

| ICP Description | Gate 1 Result | Rationale |
|-----------------|---------------|-----------|
| "B2B SaaS companies growing fast" | ❌ FAIL | No operational specificity |
| "Companies that raised Series B" | ❌ FAIL | Funding ≠ operational specificity |
| "Sales teams using CRM" | ❌ FAIL | Could be any company |
| "Multi-state insurance agencies" | ✅ PASS | Regulated, specific compliance context |
| "Restaurants with >100 Google reviews/month in NYC" | ✅ PASS | Operational + geographic specificity |
| "Nursing homes with <3 CMS stars" | ✅ PASS | Regulatory + specific threshold |

### Gate 1 Validation Format

```markdown
**Gate 1: Horizontal Disqualification**

ICP Description: [From segment/message]

Check:
- [ ] NOT "any B2B company"
- [ ] NOT generic funding/growth descriptor
- [ ] HAS operational/regulatory specificity
- [ ] HAS observable pain indicator

**Result:** ✅ PASS / ❌ FAIL
**Rationale:** [Why it passes or fails]
```

---

## Gate 2: Causal Link Constraint

**Question:** Does the signal DIRECTLY PROVE the specific pain, or is it just a proxy for growth/activity?

### The Causal Link Test

Ask: "Could a company have this signal but NOT have the pain?"

- If YES → Weak causal link → ❌ FAIL
- If NO → Strong causal link → ✅ PASS

### Banned Signals (Always FAIL Gate 2)

These signals NEVER prove specific pain:

| Banned Signal | Why It Fails |
|---------------|--------------|
| Recently raised funding | Thousands of companies raise, no specific pain implied |
| Hiring for [role type] | All scaling companies hire |
| Growing headcount | Growth activity, not pain |
| Expanding to new markets | Strategic move, not operational pain |
| M&A activity | Business development, not pain |
| Job postings | Normal business operation |
| Tech stack includes [tool] | Using tool ≠ having problem with it |
| Company is [size] | Size alone doesn't prove pain |
| Industry is [sector] | Being in sector ≠ experiencing pain |

### Strong Signals (Can PASS Gate 2)

| Strong Signal | Why It Works |
|---------------|--------------|
| Open EPA violation | Violation IS the compliance pressure |
| CMS <3 star rating | Rating IS the quality mandate |
| FMCSA Conditional rating | Rating IS the safety requirement |
| Health dept grade drop | Grade IS the operational failure |
| License expiration in 30 days | Deadline IS the compliance pain |
| Permit filed requiring specific equipment | Filing IS the need signal |
| Menu price 28% higher on DoorDash | Price difference IS the commission exposure |

### Gate 2 Validation Format

```markdown
**Gate 2: Causal Link Constraint**

Signal Used: [From segment/message]
Claimed Pain: [What pain is this supposed to indicate?]

Causal Test: "Could they have this signal but NOT have the pain?"
- Answer: YES / NO

Check:
- [ ] Signal is NOT on banned list
- [ ] Signal DIRECTLY proves pain (not just activity)
- [ ] Pain connection is necessary, not coincidental

**Result:** ✅ PASS / ❌ FAIL
**Rationale:** [Why the causal link is strong or weak]
```

---

## Gate 3: No Aggregates Ban

**Question:** Are statistics company-specific, or are they industry-wide aggregates presented as insights?

### FAIL Conditions

The segment/message FAILS if it contains:
- Industry averages without their specific data
- Statistics that apply to thousands of companies
- Research findings presented as if about them specifically
- Benchmarks without their data point for comparison

### Banned Patterns

| Pattern | Example | Why It Fails |
|---------|---------|--------------|
| "Industry average is X%" | "The average restaurant pays 28% in fees" | Applies to ALL restaurants |
| "Companies like yours typically..." | "Companies like yours see 20-30% inefficiency" | Not about THEM |
| "Research shows..." | "Research shows 73% of sales teams..." | Generic stat |
| "[N]% of [industry]..." | "63% of manufacturers face..." | Could be them or anyone |
| "The typical [role]..." | "The typical ops manager spends 10 hours..." | Generic persona |

### PASS Conditions

Aggregates are ALLOWED only when:
1. Their SPECIFIC data point is provided
2. Aggregate is used for COMPARISON, not as the insight
3. Format: "Your [specific metric] vs [aggregate benchmark]"

### Examples

| Statement | Gate 3 Result | Rationale |
|-----------|---------------|-----------|
| "23% capture rate is the industry norm" | ❌ FAIL | Industry stat, not their data |
| "Companies in your space face compliance challenges" | ❌ FAIL | Generic, applies to thousands |
| "Your 18.4 visits per 1000 residents vs state avg of 28.1" | ✅ PASS | Their specific data + comparison |
| "Your facility's 14 violations vs county median of 8" | ✅ PASS | Their data + benchmark |
| "Top 10% use 2.3 lbs chlorine vs your 3.8 lbs" | ✅ PASS | Their data + benchmark |

### Gate 3 Validation Format

```markdown
**Gate 3: No Aggregates Ban**

Statistics Used: [List any statistics/numbers in segment/message]

Check for each statistic:
- [ ] Is this THEIR specific data point?
- [ ] If aggregate, is it comparison (not insight)?
- [ ] Format: "Your [X] vs [benchmark]"?

**Result:** ✅ PASS / ❌ FAIL
**Rationale:** [Which stats pass/fail and why]
```

---

## Gate 4: Technical Feasibility Audit

**Question:** Can we actually detect/obtain this data? What is the MECHANICAL detection method?

### The Mechanical Test

For EVERY data claim, you MUST be able to answer:

1. **What is the data source?** (Name of API, database, or scraping target)
2. **What is the specific field/attribute?** (Field name, CSS selector, API response key)
3. **How do we access it?** (API call, web scrape, manual lookup)
4. **What's the detection footprint?** (What exactly makes this detectable?)

If you cannot answer ALL FOUR → ❌ FAIL

### Field Reality Check (NEW)

Additionally, for each data claim:

5. **Do the underlying fields actually exist?**
   - If using raw field → Cite the field name from official docs/schema
   - If using calculated field → Cite the source fields used for calculation
   - If field doesn't exist → ❌ FAIL (fabricated claim)

6. **Is this reasonable derivation or wild speculation?**
   - ✅ OK: "Duration = final_date - file_date" (math on real fields)
   - ✅ OK: "Growth rate = (Q4 - Q1) / Q1" (comparison of real snapshots)
   - ❌ FAIL: "Runway = revenue / burn rate" when neither field is available
   - ❌ FAIL: "Construction start date" when only permit_file_date exists
   - ❌ FAIL: Industry knowledge presented as database fields (e.g., "RSC filing timelines")

**Key principle:** Calculated fields are fine. Made-up fields are not.

### Undetectable Data Claims (Always FAIL)

| Claimed Data | Why Undetectable |
|--------------|------------------|
| Digital business card usage | Apps live on phones, not corporate websites |
| CRM data quality | Internal system, not externally visible |
| Sales cycle length | Internal metric |
| Employee satisfaction | Glassdoor is anecdotal, not systematic |
| Internal process efficiency | Operational, requires inside access |
| Tool adoption rates | Can't see login/usage data |
| Budget constraints | Not publicly visible |
| "BuiltWith for [non-web technology]" | Only detects web-facing tech |

### Gate 4b: Temporal Provability (win-loss-rewind addendum)

Gate 4 asks whether the field EXISTS. Gate 4b asks whether its value **at a
past date** is provable — because this skill's claims are about what was
visible BEFORE an outcome, and today's web silently impersonating last year's
web is the exact hindsight leak this addendum kills.

For every data claim that depends on WHEN a fact became true:

7. **Which visibility tag could this claim earn at the reference date?**
   - **[ARCHIVED-BEFORE]** — an archive.org capture at-or-before the date shows it
     (requires a `wayback_friendly` source + an `archive_search_pattern`).
   - **[RECORD-DATED-BEFORE]** — the record carries its own date (+ publication-lag
     haircut) preceding the reference date (requires a `record_date_field`).
   - **[RETRO-ASSUMED]** — only today's state is observable. Allowed to exist, but
     it can NEVER count as firm evidence, and the hypothesis must say so.

8. **Is the claimed tag consistent with the source's classification**
   (`references/source-temporal-classification.json`)? A current-state-only,
   non-archivable source (LinkedIn employee counts) claiming [ARCHIVED-BEFORE]
   is fabricating provability → ❌ FAIL.

**Quick check:** "Can you name the mechanism that proves this was visible at
reference_date − lead_time? If not even [RETRO-ASSUMED] with a stated
assumption — FAIL."

### Detectable Data Claims (Can PASS)

| Data Claim | Detection Mechanism |
|------------|---------------------|
| EPA violation | EPA ECHO API → `violation_id`, `penalty_amount` |
| CMS star rating | CMS Care Compare API → `overall_rating` field |
| Restaurant health grade | Local health dept API → `inspection_score` |
| FMCSA safety rating | FMCSA SAFER API → `safety_rating` field |
| Google review velocity | Google Maps API → `reviews[].time` timestamps |
| Menu price arbitrage | Web scrape: Restaurant site vs DoorDash/UberEats |
| Tech stack (web-facing) | BuiltWith API → specific technology categories |

### Gate 4 Validation Format

```markdown
**Gate 4: Technical Feasibility Audit**

Data Claims in Segment/Message:
1. [Data claim 1]
2. [Data claim 2]

For EACH claim:

**Claim 1: [Data claim]**
- Data Source: [API/database name]
- Specific Field: [field name or selector]
- Access Method: [API call / scrape / manual]
- Detection Footprint: [What makes this visible externally?]
- Field Reality: ✅ Field exists / ❌ Fabricated / ⚠️ Calculated from [source fields]
- If Calculated: Source fields = [field1, field2, ...]
- Reasonable Derivation: ✅ Data scientist would do this / ❌ Wild speculation
- Feasibility: ✅ Detectable / ❌ Not detectable

[Repeat for each claim]

**Result:** ✅ PASS (all claims detectable AND real) / ❌ FAIL (any claim undetectable OR fabricated)
**Rationale:** [Which claims pass/fail and why]
```

---

## Gate 5: Product Connection (NEW - CRITICAL)

**Question:** Does resolving this pain require/benefit from our product? Would they NEED this product to solve this problem?

**CRITICAL:** Gate 5 failure is a FUNDAMENTAL FLAW. No revision is possible - the pain domain is wrong for this product. The segment must be DESTROYED.

### The Product Connection Test

Ask: "If this prospect resolves this pain, would they NEED this product to do it?"

- If YES → ✅ PASS (product is the solution)
- If MAYBE/PARTIALLY → ⚠️ Weak fit, may need revision
- If NO → ❌ FAIL (product doesn't solve this pain)

### Referencing Wave 0.5 Product Value Analysis

From Wave 0.5, retrieve:
- **Valid Pain Domains:** What the product CAN solve
- **Invalid Pain Domains:** What the product CANNOT solve
- **Core Problem:** The fundamental problem the product addresses

### FAIL Conditions (Auto-Reject if ANY are true)

The segment/message FAILS Gate 5 if:
- Pain can be resolved without the product
- Product is only tangentially related to pain domain
- Any competitor could solve this pain with their (different) product
- Pain falls into an INVALID domain from Wave 0.5 Product Value Analysis
- The connection between pain and product requires >2 logical jumps

### PASS Conditions

The segment/message PASSES Gate 5 if:
- Product is the PRIMARY solution to this pain
- Buying the product materially addresses the identified situation
- Pain domain matches the product's core value proposition
- Pain is in a VALID domain from Wave 0.5

### Examples

| Pain Identified | Product | Gate 5 Result | Rationale |
|-----------------|---------|---------------|-----------|
| "CE deadline in 45 days" | Digital business cards | ❌ FAIL | Cards don't help with CE compliance |
| "No time to print cards before conference" | Digital business cards | ✅ PASS | Product is direct solution |
| "EPA violation deadline" | Compliance software | ✅ PASS | Product tracks/remediates violations |
| "EPA violation deadline" | Digital business cards | ❌ FAIL | Wrong product domain |
| "Hiring 15 people/month, can't provision cards fast enough" | Digital business cards | ✅ PASS | Product scales instantly |
| "CMS <3 star rating" | Compliance training software | ✅ PASS | Training addresses quality issues |
| "CMS <3 star rating" | Sales engagement platform | ❌ FAIL | Sales tools don't fix care quality |

### Gate 5 Validation Format

```markdown
**Gate 5: Product Connection**

Pain Identified: [From segment/message]
Product: [What the company sells]
Product Value Analysis Reference: [From Wave 0.5]
- Valid Domains: [List]
- Invalid Domains: [List]

Domain Check:
- Pain Domain: [What domain is this pain in?]
- Domain Status: VALID / INVALID

Validation Question:
"If this prospect resolves [pain], would they NEED [product] to do it?"
- Answer: YES / MAYBE / NO
- Explanation: [Why]

Check:
- [ ] Pain is in VALID domain (from Wave 0.5)
- [ ] Product is PRIMARY solution (not tangential)
- [ ] Buying product resolves the pain
- [ ] Connection requires ≤2 logical jumps

**Result:** ✅ PASS / ❌ FAIL
**Rationale:** [Why the product does/doesn't connect to this pain]

[If FAIL:]
**Revision Possible?** NO - Gate 5 failures are fundamental flaws
**Action:** AUTO-DESTROY, consider situation-based fallback
```

---

## Complete Validation Process

### For Segments (Synthesis Phase)

```markdown
## Hard Gate Validation: [Segment Name]

### Gate 1: Horizontal Disqualification
[Validation format from above]

### Gate 2: Causal Link Constraint
[Validation format from above]

### Gate 3: No Aggregates Ban
[Validation format from above]

### Gate 4: Technical Feasibility Audit
[Validation format from above]

### Gate 5: Product Connection
[Validation format from above]

---

## FINAL VERDICT

| Gate | Result |
|------|--------|
| Gate 1: Horizontal | ✅/❌ |
| Gate 2: Causal Link | ✅/❌ |
| Gate 3: Aggregates | ✅/❌ |
| Gate 4: Feasibility | ✅/❌ |
| Gate 5: Product Connection | ✅/❌ |

**VERDICT:** ✅ VALIDATED - Proceed / ❌ AUTO-DESTROY

[If any gate failed:]
**Failed Gate(s):** [List]
**Failure Reason:** [Specific issue]
**Revision Possible?**
- Gate 3 or 4 only: YES (one attempt)
- Gate 1, 2, or 5: NO (fundamental flaw)
```

### For PVP Messages (Evaluation Phase)

Apply same format, but check the actual MESSAGE text against all five gates.

---

## Revision Protocol

### When Revision IS Possible

If segment/message fails due to:
- Gate 3 (aggregate used incorrectly) → Can add their specific data
- Gate 4 (one data source undetectable) → Can substitute different source

**Revision Steps:**
1. Identify specific failure point
2. Propose one fix
3. Re-validate through all 5 gates
4. If still fails → DESTROY (no second revision)

### When Revision is NOT Possible

If segment/message fails due to:
- Gate 1 (fundamental ICP issue) → Need different vertical
- Gate 2 (no strong signal exists) → Concept is invalid
- Gate 4 (core premise is undetectable) → Concept impossible
- **Gate 5 (product doesn't solve this pain) → Pain domain mismatch, need different approach**

**Action:** DESTROY immediately, don't attempt revision

**Special Note on Gate 5 Failures:**
Gate 5 failures indicate the system found valid pain in a data-rich niche, but the product doesn't solve that pain. This is a FUNDAMENTAL mismatch. Options:
1. Try situation-based fallback (if product is horizontal)
2. Trigger no-fit response
3. Suggest vertical pivot where product-fit is stronger

---

## Quick Reference Card

**Gate 1 Quick Check:**
> "Could this ICP description apply to 10,000+ companies?" If YES → FAIL

**Gate 2 Quick Check:**
> "Could they have this signal but NOT have the pain?" If YES → FAIL

**Gate 3 Quick Check:**
> "Is there a specific number that is THEIR data (not industry)?" If NO → FAIL

**Gate 4 Quick Check:**
> "Can I name the API field or scraping selector for this data?" If NO → FAIL
> "Does this field actually exist, or is it fabricated/industry knowledge?" If fabricated → FAIL
> "If calculated, do the underlying fields exist?" If NO → FAIL

**Gate 5 Quick Check:**
> "Would they NEED this product to resolve this pain?" If NO → FAIL
> "Is this pain in a VALID domain from Wave 0.5?" If NO → FAIL

---

## Integration Points

### Use During Synthesis
- After generating segment concept, before proceeding
- Reject segments that fail any gate
- One revision attempt allowed

### Use During Drafting
- After drafting message, before evaluation
- Catch issues before wasting evaluation time
- No revision at this stage (draft differently)

### Use During Evaluation
- First step before scoring
- Fail any gate → AUTO-DESTROY (don't score)
- Hard gates come BEFORE soft evaluation

---

**Version:** 2.0.0 (November 2025) - Added Gate 5: Product Connection

**Principle:** These gates are non-negotiable. If a message is good enough to pass, it passes. If not, destroy it. There is no "almost passes" or "close enough."

**Critical Addition (v2.0):** Gate 5 ensures that excellent data and specific pain are not wasted on product-irrelevant messages. Data quality without product-fit = useless content.
