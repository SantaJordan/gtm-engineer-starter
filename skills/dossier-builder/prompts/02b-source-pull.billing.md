# Source-pull overlay: Billing (Stripe / Recurly / Zuora / Chargebee) (W2a)

Use on top of `02a-source-pull.template.md`. Billing is the highest-trust revenue source — pull aggressively.

## Billing-specific rules

### 1. Aggregate to per-account current state

For each account, emit:
- Current `mrr` from active subscriptions (most recent invoice with sub_status='active')
- All historical state changes — `subscription_created`, `plan_changed`, `cancellation_scheduled`, `cancelled`, `reactivated`
- All payments — `payment_succeeded`, `payment_failed`, `refund_issued`

Each as separate timeline events with `source = billing_event` and verbatim `type` from the billing system.

### 2. Customer-ID join

The billing system has its own primary key (Stripe `customer_id`, Recurly `account_code`). Join to canonical `primary_id_system` via the customer-id field that the customer-success/CRM integration populates (`stripe.customer_id ↔ sfdc.Account.Stripe_Customer_ID__c` or equivalent).

If no such field exists in the CRM, fall back to email-domain match with the W2b agent applying tenant-domain strip + 3-test guard. Flag in `provenance.rule = "billing_email_domain_fallback"`.

### 3. Currency normalization

Convert all amounts to USD using the rate at `as_of` time, recorded in `fields.original_currency` + `fields.original_amount`. Don't lose the source values.

### 4. MRR computation

Per `references/methodology.md`: MRR is the sum of active recurring subscription `unit_amount * quantity / interval_in_months` at a point in time. Pull this verbatim from the billing system's reports if available; otherwise compute and document the computation in `provenance.rule`.

### 5. Cancellation semantics

Distinguish:
- `cancellation_scheduled` — customer requested cancellation but is still paying through period_end. Subscription still active. **MRR is still positive** until period_end passes.
- `cancelled` — period_end passed, subscription is fully terminated. MRR = 0.

This nuance is load-bearing for `outcome` derivation. Customer-mode dossiers with active scheduled-cancellation should land `outcome = active` until period_end, then flip to `cancelled`.

### 6. Failed-payment grace period

If `payment_failed` is followed by `payment_retry_succeeded` within N days (per billing system's grace period config), emit both events but DON'T emit a `cancellation_grace_period_started`. The customer didn't actually churn.

## Event row shapes

```json
{"source": "billing_event", "type": "subscription_created", ...}
{"source": "billing_event", "type": "plan_changed", "fields": {"from_plan": "...", "to_plan": "...", "from_mrr": ..., "to_mrr": ...}, ...}
{"source": "billing_event", "type": "payment_succeeded", "fields": {"amount": ..., "currency_original": "USD"}, ...}
{"source": "billing_event", "type": "cancellation_scheduled", "fields": {"period_end": "2026-04-01"}, ...}
{"source": "billing_event", "type": "cancelled", "fields": {"reason": "..."}, ...}
{"source": "subscription", "type": "active", "fields": {"current_mrr": ..., "plan": ..., "started_at": ..., "period_end": ...}, ...}
```
