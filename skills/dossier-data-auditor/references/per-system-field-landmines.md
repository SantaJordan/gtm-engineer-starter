# Per-system field landmines (W0 + W5)

> Loaded at W0 (so the auditor knows what to look for during scan) and W5 (so the column fitness pentad can flag system-specific landmines). For each common system the customer might have, this catalog lists the 5-10 fields RevOps cares about plus the load-bearing landmines that commonly corrupt an audit.

## Salesforce (SFDC)

| Field | Watchlist |
|---|---|
| `Account.Id` | Canonical join key. **Landmine**: 15-vs-18-char inconsistency, format violations from Excel roundtrip (`1.23E+19`). |
| `Account.Name` | What AEs typed. **Landmine**: test/demo regex hits, person-name patterns. |
| `Account.Website` | The primitive. **Landmine**: protocol present/absent, trailing path, freemail domains as canonical. |
| `Account.ParentId` / `Account.Ultimate_Parent__c` | **Massive landmine** — hierarchy logic depends on this. Often partially populated, often self-referential, often disagrees between `ParentId` and the custom `Ultimate_Parent__c`. |
| `Account.Type` | `Customer` / `Prospect` / `Partner` / `Former Customer`. **Landmine**: values that don't appear in current picklist (deprecated). |
| `Account.RecordTypeId` | Controls which fields are visible/required per record type. **Landmine**: explains "why are 40% of accounts missing field X" — different record type. |
| `Account.OwnerId` | **Landmine**: assignments to deactivated users (`User.IsActive = false`). |
| `Opportunity.Amount` / `Opportunity.SBQQ__Subscription__r.Amount__c` | **Massive landmine**. The "do not use" classic. Deal value ≠ MRR. SBQQ subscription amount is the recurring source. |
| `Opportunity.StageName` / `Opportunity.ForecastCategoryName` | Pipeline truth. **Landmine**: deprecated stages still on old records. |
| `Contact.Email` | For email-domain joins. **Landmine**: tenant-domain pollution, ex-employee emails, role-based emails (`info@`, `sales@`). |

## HubSpot

| Field | Watchlist |
|---|---|
| `company.hs_object_id` (or `companyId`) | Canonical. |
| `company.domain` | Auto-set from form-fill email domain. **Landmine**: freemail addresses become `gmail.com` companies. |
| `company.name` | Auto-set. **Landmine**: frequently becomes "Acme.example" when computed from domain. |
| `company.industry` | Picklist that's been migrated multiple times. |
| `company.hs_lead_status` | Pipeline analog. |
| `company.hs_analytics_source_data_1` / `_2` | First-touch attribution. **Landmine**: changes across HubSpot version migrations. |
| `company.hubspot_owner_id` | Owner. Same deactivation issue as SFDC. |
| `contact.email` | For joins. |
| `deal.amount` + `deal.dealstage` | Deal economics. **Landmine**: stage migrations + `is_closed_won` boolean drift. |
| `merge_audits` table (if exported) | Deduplication ghosts. **Landmine**: the IDs you're joining on may have been merged into a different ID. |

## Stripe / billing

| Field | Watchlist |
|---|---|
| `customer.id` | Canonical for billing universe. |
| `customer.email` | For cross-system join. **Landmine**: who controls this email matters — the person who signed up, not the account exec. |
| `customer.name` | **Landmine**: frequently a person's name, not company. Cannot be used as company-name primitive. |
| `customer.metadata.sfdc_id` | The linking field. **Landmine**: manually set, often only on customers who closed before a certain date. |
| `subscription.id` + `subscription.status` | Subscription state. **Landmine**: `incomplete`, `past_due`, `paused` ≠ `active` but often treated as such. |
| `subscription.items[].plan.amount` | True MRR computation source. Beats `Opportunity.Amount` every time. |
| `subscription.created` vs `customer.created` | For tenure — use subscription, not customer. |
| `invoice.status_transitions.paid_at` | Actual revenue recognition timestamp. |
| `charge.refunded` / `disputes` | Friction signals usually invisible to CRM. |
| `subscription.cancel_at_period_end` | Pending-churn signal. **Landmine**: distinct from `status='canceled'`. |

## Gong (or any call recording)

| Field | Watchlist |
|---|---|
| `call.id` | Canonical. |
| `call.account_id` | Gong's link to its CRM integration. **MASSIVE landmine** — this is the ~40%-wrong field. |
| `call.scheduled_start_time` vs `call.started_at` | For activity-recency. Use `started_at`. |
| `call.duration` | Watchlist: < 30 seconds (cancelled), > 4 hours (left-on-recorder). |
| `call.participants[].email_address` | For email-domain fallback joins. **Landmine**: tenant-domain pollution (self-domain trap). |
| `call.participants[].affiliation` (internal / external) | Gong's own classification. Often wrong on tenant-domain pollution. |
| `call.title` | For fake/test detection. |
| `call.tracker_occurrences[]` | Keyword trackers. **Landmine**: trackers renamed → broken historical data. |
| `call.deal_id` / `call.opportunity_id` | Secondary join path. Cross-check against `account_id`. |
| `call.crm_account_id` | Sometimes a *different* field than `account_id`. Pick one and document; disagreement is a landmine. |

## Product analytics (Mixpanel / Amplitude / Heap / Pendo / etc.)

| Field | Watchlist |
|---|---|
| `tenant.id` / `organization.id` | Canonical for product universe. Naming varies wildly per vendor. |
| `tenant.created_at` | True product-side activation date. Often differs from CRM by days-to-months. |
| `user.id` + `user.email` + `user.tenant_id` | The user→org link. **Landmine**: consultants/agency users with ambiguous current-tenant. |
| `user.is_admin` / `user.role` | Important for who-decides analysis. |
| `event.timestamp` | For activity-recency. **Landmine**: events with epoch-zero timestamps (ingest errors). |
| `event.tenant_id` vs `event.user.tenant_id` | Often disagree on user-switched-orgs records. |
| `tenant.plan_tier` / `tenant.subscription_status` | Duplicated from billing; often stale. |
| `tenant.metadata.crm_account_id` | The link to SFDC. **Landmine**: set at provisioning, never updated. |
| First/last event timestamps per tenant | Bookends for engagement window. |

## Support (Zendesk / Intercom / Freshdesk)

| Field | Watchlist |
|---|---|
| `organization.id` / `org.id` | Canonical for support universe. |
| `organization.external_id` | Link to CRM. **Landmine**: often unset for self-serve customers. |
| `ticket.id` + `ticket.organization_id` | The tickets→org link. |
| `ticket.requester.email` | For orphan tickets (no org). **Landmine**: tenant-domain pollution again. |
| `ticket.created_at` + `ticket.resolved_at` | For SLA. **Landmine**: `resolved_at < created_at` corruption. |
| `ticket.status` | Pipeline analog. |
| `ticket.tags[]` | Institutional classification. **Landmine**: tag drift over time. |
| `ticket.satisfaction_rating` / CSAT | Often very sparse. |
| `ticket.priority` / `ticket.severity` | **Landmine**: most orgs have severity-inflation problems. |
| `organization.domain_names[]` | Zendesk's own domain associations — cross-check with CRM website. |

## Marketing automation (Marketo / Eloqua / HubSpot Marketing / Pardot)

| Field | Watchlist |
|---|---|
| `lead.email` | Universal join key. **Landmine**: invalid format, tenant-domain pollution, role-based. |
| `lead.lead_source` / `person.original_source` | First-touch attribution. **MASSIVE landmine** — ZoomInfo stamps this and never refreshes. |
| `lead.mql_date` / `lead.mql_status` | Funnel position. **Landmine**: MQL date AFTER opportunity created date. |
| `lead.score` / `lead.behavior_score` | Engagement score. **Landmine**: score-drift over scoring-rule changes. |
| `lead.lifecycle_stage` | Analog of Account.Type. **Landmine**: deprecated stages. |
| `lead.utm_*` + `lead.first_touch_url` | Channel attribution. **Landmine**: bot traffic, employee internal traffic. |
| `lead.account_id_lookup` | Link to CRM. Often manually-mapped, partially populated. |
| `activity.activity_type` + `activity.activity_date` | Engagement timeline. **Landmine**: activity types that don't appear in current setup. |
| `form.submission_date` + `form.fields_submitted` | Direct-disclosure signal. **Landmine**: form-fill bots. |
| `email.unsubscribe_date` + `email.bounce_date` | Must-honor lists. **Landmine**: unsubscribed-but-still-in-active-sequence cases. |

## How the auditor uses this

For each system detected in W1 scan:

1. Match the discovered columns against this cheat sheet (by header pattern + value pattern).
2. Default the system-specific landmine checks to ON.
3. Report fill rate + the landmine-specific verdict per field.
4. Surface the **bold** items in the W1b/W2/W5 panels.
5. The highest-suspicion fields land in `recommended_engagement_overrides.json` as `do_not_use_fields[]` (when known bad) or `treat_as_suspect_fields[]` (when historical data suggests low trust without being declared bad).
