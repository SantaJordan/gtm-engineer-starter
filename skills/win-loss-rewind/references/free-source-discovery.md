# Finding free, dated sources for a signal (no paid atlas needed)

Phases 4.5 and 6 need sources that show a signal **as it was before the
customer bought**. This is the free way to find them. The full Crawford
research agent ships a ready-made atlas of such sources; that is optional
acceleration, not a requirement.

## The procedure

1. **Reason first.** For the archetype, write the chain in one line each:
   pain → what the company does about it → what public trace that leaves.
   Example: "too many offices on separate calendars" → "opens more offices" →
   "new organization records in a licensing or provider registry".
2. **List candidate source types** for that trace: state or federal registries,
   regulator lists, licensing boards, permit and inspection records, court
   dockets, association member directories, conference speaker lists, job
   boards, the company's own site through the Wayback Machine.
3. **Verify one real record.** For each candidate, pull one record for one of
   your member companies (or a company like them). Write down what you saw.
   A source you have not pulled a record from does not go in the list.
4. **Date its depth.** Find the field that dates each record, and the earliest
   record you can find. A source that starts in 2024 cannot show 2022.
5. **Classify it** in `references/source-temporal-classification.json`:
   `point_in_time_record` (each record carries its own date) or
   `current_state_only` (shows today only; usable for the past only through an
   archived copy).

## Worked example (verified 2026-10-06)

**NPPES NPI Registry**, the US registry of healthcare providers. Free, no key.

- Query: `https://npiregistry.cms.hhs.gov/api/?version=2.1&taxonomy_description=Dentist&state=OH&city=Columbus&enumeration_type=NPI-2&limit=2`
  (`NPI-2` means organizations, not individual dentists.)
- Each result carries `basic.enumeration_date` (when the organization got its
  number) and `basic.last_updated`, plus `addresses` and `practiceLocations`.
- So it is a `point_in_time_record` source: an organization's enumeration date
  says whether a new practice location existed before your reference date.
- Signal it can test: "the group registered a new practice organization in the
  12 months before the first call".

## The Wayback Machine for current-state sources

A company website shows only today. `scripts/wayback_timetravel.py` checks
whether a page (for example `/locations`) had a given text in a capture dated
before the reference date. That is how a `current_state_only` source can still
prove the past. It is free; it is slow, because it is rate-limited on purpose.
