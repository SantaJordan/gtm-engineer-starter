"""The PACKET CARD — `lib.packets.explain` / `explain_rules`.

The card is what a non-engineer sees before any money is spent and beside every
row a human is asked to settle. So what is guarded here is not formatting, it
is the four ways a card could lie:

  1. **An empty packet must LOOK empty.** The tier is printed in capitals with
     the report's own sentence beside it, and a thin packet says so on line one.
  2. **A missing field says WHY, in the packet's own words.** The status word
     and the reason come off the packet's `absent` block; the card never
     invents a reason and never turns "we did not look" into "there is nothing
     there".
  3. **A contact detail never reaches a card.** Cards are pasted into Slack, a
     ticket and a Sheet. Phone and email are held back, and the card says how
     many it held.
  4. **It cannot throw.** A malformed packet, a string, None — each renders a
     card that says so, because a review row that arrives with no card is a row
     nobody answers.
  5. **It never states a fact it does not have** (the 2026-09-21 blind review).
     An absence with no recorded reason says `reason unavailable`, never "treat
     it as not looked for". A question whose evidence IS here and whose answer
     never came back is UNAVAILABLE, not unasked. A `requires` token is matched
     on its full path or through the explicit alias map, so `anchor.name` can
     never answer for `candidate.name`. A structured rule keeps its own keys
     instead of being rewritten as an employment record. And the CO-RULE-01
     warning belongs to the two fit composers, not to a rubric.

Every company, person and address below is invented.
"""
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
from lib import packets as pk                               # noqa: E402
from lib.fetch_cache import PageCache, page_key             # noqa: E402

HOME = (
    "<html><head>"
    "<meta property='og:site_name' content='Northgate Dental Care'>"
    "</head><body>"
    "<nav><a href='/about'>About Us</a><a href='/services'>Our Services</a></nav>"
    + "<p>Northgate Dental Care is an independently owned family practice. "
      "We have cleaned teeth on this corner since 1998. We do not place "
      "implants.</p>" * 6
    + "<footer><p>&copy; 2026 Northgate Dental Care PLLC.</p></footer></body></html>")
ABOUT = "<html><body><p>" + "Our team has six hygienists. " * 20 + "</p></body></html>"
SERVICES = ("<html><body><p>" + "Cleanings, crowns and whitening. " * 20
            + "</p></body></html>")

MAPS = {"name": "Northgate Dental Care", "category": "Dentist",
        "subtypes": ["Dental clinic"], "business_status": "OPEN",
        "street_address": "10 Northgate Rd", "city": "Springfield",
        "state": "IL", "phone": "+15550100", "_lane": "dataforseo"}
NPPES = {"result_count": 1, "results": [{
    "number": "0000000000", "enumeration_type": "NPI-2",
    "basic": {"status": "A", "last_updated": "2025-02-01"},
    "taxonomies": [{"primary": True, "code": "1223G0001X",
                    "desc": "Dentist - General Practice"}]}]}

PROFILE = {"headline": "Owner at Northgate Dental Care",
           "about": "I have run a two-chair practice for twenty years.",
           "location": "Springfield, IL", "scrape_as_of": "2026-09-01",
           "experiences": [
               {"title": "Owner", "company": "Northgate Dental Care",
                "start_date": "2004-06", "end_date": None, "is_current": True},
               {"title": "Associate", "company": "Elm Dental",
                "start_date": "1999-01", "end_date": "2004-05",
                "is_current": False}]}

ICP = {"inclusions": ["general dentistry"],
       "exclusions": ["orthodontics", "oral surgery"], "territories": ["IL"]}

QUESTIONS = [
    {"id": "self_description_matches_icp",
     "text": "Does the company's OWN description claim the included service?",
     "requires": ("site.homepage_text", "site.about_text")},
    {"id": "negation_scope_unclear",
     "text": "Is a 'we do not' sentence present whose SCOPE cannot be read?",
     "requires": ("site.negation_sentences",)},
    {"id": "category_matches_icp",
     "text": "Does the primary category name an included class?",
     "requires": ("maps.primary_category",)},
]


class FakeResult:
    """Duck-types `judge.core.JudgeResult` — the card reads attributes."""

    action = "review"
    verdict = None
    decision_score = 0.42
    round = 1
    enrichment_added = []
    review_reasons = ["still_missing: site.about_text"]
    packet_richness = {}
    per_question = {"self_description_matches_icp": {"type": "noul", "p": 0.93},
                    "category_matches_icp": {"type": "noul", "p": 0.71}}


def flat(card: str) -> str:
    """The card with its wrapping removed. The card WRAPS on purpose (a line
    that runs off a terminal is a line nobody reads), so an assertion about a
    sentence must not also be an assertion about where it broke."""
    return " ".join(card.split())


def seed(tmp_path, *, subpages=True) -> Path:
    pc = tmp_path / "page_cache"
    pc.mkdir(parents=True, exist_ok=True)
    meta, bodies = [], {}
    meta.append({"domain": "northgate-dental.example",
                 "fetched_at": "2026-09-19T00:00:00+00:00", "status": 200,
                 "final_url": "https://northgate-dental.example/",
                 "redirect_chain": [], "title": "", "tls_names": [],
                 "favicon_mmh3": None, "content_len": len(HOME),
                 "from_cache": False})
    bodies["northgate-dental.example"] = HOME
    if subpages:
        for path, body in (("/about", ABOUT), ("/services", SERVICES)):
            url = "https://northgate-dental.example" + path
            key = page_key(url)
            meta.append({"domain": key, "fetched_at": "2026-09-19T00:00:00+00:00",
                         "status": 200, "final_url": url, "redirect_chain": [],
                         "title": "", "tls_names": [], "favicon_mmh3": None,
                         "content_len": len(body), "from_cache": False})
            bodies[key] = body
    (pc / "meta.jsonl").write_text("".join(__import__("json").dumps(m) + "\n"
                                           for m in meta))
    for key, body in bodies.items():
        (pc / f"{key}.html").write_text(body)
    return tmp_path


def rich_company(tmp_path):
    seed(tmp_path)
    cache = PageCache(tmp_path, offline=True)
    return pk.build_company_packet(
        {"entity_key": "c-1", "name": "Northgate Dental Care",
         "domain": "northgate-dental.example", "city": "Springfield",
         "state": "IL", "phone": "555-0100"},
        page_cache=cache, maps=MAPS, registry=NPPES)


# ------------------------------------------------------------------ the rich

def test_a_rich_company_card_names_the_tier_and_quotes_the_evidence(tmp_path):
    packet = rich_company(tmp_path)
    assert pk.richness(packet)["tier"] == "rich"
    card = pk.explain(packet, rules=ICP)

    head = card.splitlines()[0]
    assert head.startswith("PACKET CARD — Northgate Dental Care (company) — RICH")
    # the tier's meaning is the richness report's own sentence, not a second one
    assert pk.TIER_MEANING["rich"] in card
    # a plain label, the raw field name beside it, and the value quoted
    assert "Own description (homepage) [site.homepage_text]" in flat(card)
    assert "independently owned family practice" in flat(card)
    # provenance and the date the value was observed
    assert "northgate-dental.example" in card and "2026-09-19" in card


def test_the_card_holds_back_every_contact_field(tmp_path):
    """A card is pasted into Slack, a ticket and a Sheet. A phone number on it
    is a contact detail leaving the machine for no decision anybody is making."""
    packet = rich_company(tmp_path)
    card = pk.explain(packet, rules=ICP)
    assert "555-0100" not in card and "+15550100" not in card
    assert "record.phone" not in card and "maps.phone" not in card
    assert "contact field(s) held back" in card


def test_the_card_stays_about_one_screen(tmp_path):
    packet = rich_company(tmp_path)
    card = pk.explain(packet, rules=ICP, questions=QUESTIONS)
    assert len(card.splitlines()) <= 75
    # and it wraps: no line runs past the width it was given
    assert max(len(line) for line in card.splitlines()) <= 100


def test_the_card_is_deterministic(tmp_path):
    packet = rich_company(tmp_path)
    assert pk.explain(packet, rules=ICP) == pk.explain(packet, rules=ICP)


# ------------------------------------------------------------------ the thin

def test_a_thin_card_says_thin_and_names_what_is_missing_and_why():
    packet = pk.build_company_packet(
        {"entity_key": "c-2", "name": "Cedar Ridge Landscaping",
         "city": "Springfield", "state": "IL"})
    assert pk.richness(packet)["tier"] == "thin"
    card = pk.explain(packet, rules={"inclusions": ["landscaping"]})
    assert "— THIN" in card
    assert pk.TIER_MEANING["thin"] in card
    assert "Missing, and why" in card
    # the packet's OWN reason, verbatim — never a reason the card made up
    assert "the row carries no domain" in flat(card)


def test_an_absence_keeps_its_own_status_word(tmp_path):
    """`not_fetched` is not `fetch_failed`. A page an offline run never asked
    for did not fail, and a card that says it did is evidence about the site
    that nobody gathered."""
    cache = PageCache(tmp_path, offline=True)
    packet = pk.build_company_packet(
        {"entity_key": "c-3", "name": "Maple Street Dental",
         "domain": "maple-street.example"}, page_cache=cache)
    card = pk.explain(packet)
    assert "not_fetched" in card
    assert "fetch_failed" not in card


def test_an_empty_exclusion_list_says_what_it_costs():
    text = pk.explain_rules({"inclusions": ["general dentistry"]}, "icp_fit")
    assert "no exclusion list was supplied" in text
    assert "MAYBE" in text


def test_the_exclusion_warning_belongs_to_the_fit_composers_and_nobody_else():
    """CO-RULE-01 is the two FIT composers' rule. A rubric-only block produces
    a numeric band, and warning it that its fit can only be MAYBE is a claim
    about a verdict that stage never reaches."""
    rules = {"inclusions": ["general dentistry"]}
    for decision in ("icp_fit", "persona_fit"):
        assert "CO-RULE-01" in pk.explain_rules(rules, decision)
    # the default says nothing about a composer nobody named
    assert "CO-RULE-01" not in pk.explain_rules(rules)
    assert "MAYBE" not in pk.explain_rules(rules)
    assert "MAYBE" not in pk.explain_rules(rules, "title_band")
    # and the band preview's rubric-only block is never warned
    rubric = {"rubric": "Band 5: the buyer.\nBand 2: everyone else."}
    assert "MAYBE" not in pk.explain_rules(rubric)
    assert "CO-RULE-01" not in pk.explain_rules(rubric)
    # an empty block says it is empty either way, and only names MAYBE for fit
    assert "MAYBE" not in pk.explain_rules({})
    assert "MAYBE" in pk.explain_rules({}, "icp_fit")


def test_a_structured_rule_keeps_its_own_keys_instead_of_becoming_a_job():
    """An inclusion OBJECT is not an employment record. Rendering it through
    the experience formatter printed `"(untitled)" at (no employer)` — an
    invented job history."""
    text = pk.explain_rules({"inclusions": [
        {"kind": "contract manufacturer", "naics": "333120", "min_lines": 2},
        {"kind": "job shop", "naics": "332710"}]})
    assert "kind: contract manufacturer" in text
    assert "naics: 333120" in text and "min_lines: 2" in text
    assert "kind: job shop" in text          # one object per line, not merged
    assert "(untitled)" not in text and "(no employer)" not in text


def test_a_non_experience_list_of_objects_on_a_card_keeps_its_own_keys():
    packet = {"kind": "company", "entity_key": "c-8", "absent": {},
              "firmo": {"industry_by_source": [
                  {"source": "vendor A", "label": "contract manufacturing"},
                  {"source": "vendor B", "label": "machine shops"}]}}
    card = pk.explain(packet)
    assert "source: vendor A" in card and "label: machine shops" in card
    assert "(untitled)" not in card and "(no employer)" not in card


def test_no_rules_at_all_is_its_own_sentence():
    assert "none supplied" in pk.explain_rules({})
    assert "none supplied" in pk.explain_rules(None)


def test_rules_are_printed_verbatim():
    text = pk.explain_rules(ICP)
    for term in ("general dentistry", "orthodontics", "oral surgery", "IL"):
        assert term in text


def test_a_card_with_rules_none_leaves_the_rule_block_off(tmp_path):
    """`rules=None` is "not shown here"; `rules={}` is "there are none". The
    two are different findings and must not print the same sentence."""
    packet = rich_company(tmp_path)
    assert "Rules the judge is given" not in pk.explain(packet)
    assert "none supplied" in pk.explain(packet, rules={})


# ---------------------------------------------------------------- the person

def test_a_person_card_lists_each_current_role_on_its_own_line():
    packet = pk.build_person_packet(
        {"entity_key": "p-1", "name": "Dana Reyes", "title": "Owner"},
        profile=PROFILE)
    card = pk.explain(packet, rules={"examples": ["Owner", "Managing Partner"]})
    assert "(person)" in card
    assert '"Owner" at Northgate Dental Care' in flat(card)
    assert "no end date on the profile" in flat(card)  # the flag is named, not trusted
    assert "Example titles from the client's own customers" in flat(card)


# -------------------------------------------------------------- the questions

def test_questions_are_numbered_and_name_the_evidence_each_one_reads(tmp_path):
    packet = rich_company(tmp_path)
    card = pk.explain(packet, rules=ICP, questions=QUESTIONS)
    assert "Questions, in plain words" in card
    assert "1. Does the company's OWN description" in flat(card)
    assert "reads: Own description (homepage), About page" in flat(card)


def test_a_question_whose_evidence_is_absent_is_listed_as_not_asked(tmp_path):
    """The gate the batteries have had since v2: a missing prerequisite makes a
    question INAPPLICABLE, never FALSE. The card has to show which."""
    packet = rich_company(tmp_path)
    card = pk.explain(packet, rules=ICP, questions=QUESTIONS)
    assert "Not asked, and why" in card
    assert "we do not" in card               # the negation question, unasked
    assert "needs Sentences saying what they do NOT do" in flat(card)


# ----------------------------------------------------------------- the answer

def test_the_answer_block_carries_the_action_the_top_answers_and_a_next_step(tmp_path):
    packet = rich_company(tmp_path)
    card = pk.explain(packet, rules=ICP, questions=QUESTIONS, result=FakeResult())
    assert "Answer" in card and "review" in card and "0.42" in card
    assert "Top answers" in card
    assert "0.93" in card                                   # the judge's own number
    assert "read: Own description (homepage), About page" in flat(card)
    assert "Why a human is asked" in card
    assert "Next: " in card


def test_a_result_as_a_dict_reads_the_same_as_the_object(tmp_path):
    packet = rich_company(tmp_path)
    as_dict = {"action": FakeResult.action, "verdict": None,
               "decision_score": 0.42, "round": 1, "enrichment_added": [],
               "review_reasons": list(FakeResult.review_reasons),
               "per_question": dict(FakeResult.per_question),
               "packet_richness": {}}
    assert (pk.explain(packet, rules=ICP, questions=QUESTIONS, result=as_dict)
            == pk.explain(packet, rules=ICP, questions=QUESTIONS,
                          result=FakeResult()))


def test_a_second_round_names_what_the_enrichment_added(tmp_path):
    packet = rich_company(tmp_path)
    card = pk.explain(packet, rules=ICP, questions=QUESTIONS, result={
        "action": "accept", "verdict": "IN_ICP", "decision_score": 0.96,
        "round": 2, "enrichment_added": ["site.about_text"],
        "per_question": {}, "review_reasons": [], "packet_richness": {}})
    assert "round 2" in card
    assert "enrichment added: About page" in flat(card)
    assert "Next: accept it" in card


def test_a_card_with_no_packet_renders_from_the_results_own_richness():
    """The decision log keeps the evidence HASH, not the evidence. The review
    queue still has to put a card in front of a human."""
    card = pk.explain(None, result={
        "action": "review", "verdict": None, "decision_score": None,
        "round": 1, "per_question": {}, "review_reasons": ["evidence_insufficient"],
        "packet_richness": {"tier": "partial", "kind": "person",
                            "present_fields": ["profile.headline"],
                            "missing_required": ["profile.about"]}})
    assert "— PARTIAL" in card
    assert "the packet itself is not on this row" in flat(card)
    assert "Profile headline (their own words) [profile.headline]" in flat(card)


# ------------------------------------------------------------- it never throws

# ------------------------------------------------ contacts, wherever they hide

#: A profile with a phone number inside a ROLE TITLE and an email inside the
#: About text. Both are values the card quotes, and neither lives in a field
#: whose NAME says "phone" or "email" — which is exactly how a contact reached
#: a card that held the contact fields back.
CONTACT_PROFILE = {
    "headline": "Owner at Northgate Dental Care",
    "about": ("I have run a two-chair practice for twenty years. Reach me on "
              "(555) 010-0199 or dana@northgate-dental.example."),
    "location": "Springfield, IL", "scrape_as_of": "2026-09-01",
    "experiences": [
        {"title": "Owner — direct line +1 555 010 0177",
         "company": "Northgate Dental Care", "start_date": "2004-06",
         "end_date": None, "is_current": True}]}

CONTACT_QUESTIONS = [
    {"id": "title_matches_persona",
     "text": "Does a CURRENT role's title match the operator's persona?",
     "requires": ("profile.current_experiences",)},
    {"id": "self_declared_not_working",
     "text": "Does the person say they are not working?",
     "requires": ("profile.about",)},
]


def test_a_contact_inside_a_role_line_or_an_answer_excerpt_is_scrubbed():
    """The card's promise is that it never prints a phone number or an email
    address. Holding back the contact FIELDS is only half of it: the numbers
    people actually leave behind are inside a role title, an About section and
    a footer, and those are quoted on the card."""
    packet = pk.build_person_packet(
        {"entity_key": "p-2", "name": "Dana Reyes", "title": "Owner"},
        profile=CONTACT_PROFILE)
    card = pk.explain(packet, rules={"examples": ["Owner"]},
                      questions=CONTACT_QUESTIONS,
                      result={"action": "review", "verdict": None,
                              "decision_score": 0.5, "round": 1,
                              "enrichment_added": [], "review_reasons": [],
                              "packet_richness": {},
                              "per_question": {
                                  "title_matches_persona": {"p": 0.94},
                                  "self_declared_not_working": {"p": 0.11}}})
    # the role line the judge reads
    assert "555 010 0177" not in card
    # the About text, quoted under the field AND under Top answers
    assert "010-0199" not in card
    assert "dana@northgate-dental.example" not in card
    assert "@" not in card.split("Rules the judge is given")[0]
    assert pk.CONTACT_WITHHELD in card
    # and the rest of the value still travels — a scrub is not a deletion
    assert "two-chair practice" in flat(card)
    assert '"Owner' in flat(card) and "Northgate Dental Care" in card


def test_a_contact_field_can_never_be_the_strongest_field_on_a_card():
    """`Top answers` quotes the LONGEST field a question reads. A question
    that names a contact field must not be able to quote it."""
    fields = {"record.phone": "+1 555 010 0199 extension 4321",
              "record.name": "Dana"}
    assert pk._strongest(["record.phone", "record.name"], fields) == "Dana"
    assert pk._strongest(["record.phone"], fields) == ""


def test_an_iso_date_and_a_registry_code_survive_the_scrub(tmp_path):
    """A scrubber that eats any ten digits redacts the observation date, the
    NPI and the licence number — and a card whose dates are gone is a card
    whose freshness nobody can check."""
    assert pk._scrub_contacts("2026-09-19") == "2026-09-19"
    assert pk._scrub_contacts("1223G0001X") == "1223G0001X"
    assert pk._scrub_contacts("2021-03 – present") == "2021-03 – present"
    assert pk.CONTACT_WITHHELD in pk._scrub_contacts("call (555) 010-0199")
    assert pk.CONTACT_WITHHELD in pk._scrub_contacts("a@b.example")
    card = pk.explain(rich_company(tmp_path), rules=ICP)
    assert "2026-09-19" in card


# ----------------------------------------------------- what a question reads

def test_a_field_suffix_alone_never_satisfies_a_requirement():
    """`anchor.name` answering for `candidate.name` is one entity's evidence
    counted as another's — the exact failure the packet contract exists to
    prevent."""
    here, gone, unknown, aliased = pk._resolve_requires(
        ["candidate.name"], ["anchor.name"])
    assert here == () and gone == ("candidate.name",)
    assert unknown == () and aliased == {}
    # the full path still matches on its own
    assert pk._resolve_requires(["anchor.name"], ["anchor.name"])[0] == \
        ("anchor.name",)


def test_an_alias_match_is_allowed_through_the_table_and_is_labelled():
    here, gone, unknown, aliased = pk._resolve_requires(
        ["profile.about"], ["profile.about_text"])
    assert here == ("profile.about",)
    assert aliased == {"profile.about": "profile.about_text"}
    labels = pk._read_labels(here, gone, unknown, aliased)
    assert any("matched by alias: profile.about_text" in x for x in labels)
    # and every alias is a NAMED alternative, never a suffix rule
    assert "candidate.name" not in pk.FIELD_ALIASES
    assert all("." in name for name in pk.FIELD_ALIASES)


def test_the_card_labels_an_alias_match_where_the_operator_reads_it(tmp_path):
    packet = rich_company(tmp_path)
    card = pk.explain(packet, rules=ICP, questions=[
        {"id": "about_says_so", "text": "Does the About page say so?",
         "requires": ("site.about",)}])
    assert "matched by alias: site.about_text" in flat(card)


# -------------------------------------------- the four states of a question

def test_a_question_whose_evidence_is_present_is_never_called_unasked(tmp_path):
    """Evidence here + no answer on the row = UNAVAILABLE, not unasked. The
    old card filed it under "Not asked, and why" and could print the
    missing-evidence line beside it."""
    packet = rich_company(tmp_path)
    card = pk.explain(packet, rules=ICP, questions=QUESTIONS, result={
        "action": "review", "verdict": None, "decision_score": 0.4, "round": 1,
        "enrichment_added": [], "review_reasons": [], "packet_richness": {},
        "per_question": {"category_matches_icp": {"type": "noul", "p": 0.7}}})
    body = flat(card)
    assert "Asked, and no answer came back" in card
    assert "unavailable, not false" in body
    # the homepage and the About page are BOTH here; nothing may say otherwise
    assert "needs Own description (homepage)" not in body
    assert "none — every question in this battery needs evidence" not in body
    # and the question that genuinely has no evidence is still gated
    assert "needs Sentences saying what they do NOT do" in body


def test_a_gate_is_claimed_only_where_the_packet_records_the_absence():
    packet = {"kind": "company", "entity_key": "c-7",
              "site": {"homepage_text": {"value": "We fabricate steel.",
                                         "source": "site", "observed_at":
                                         "2026-09-19", "status": "present"}},
              "absent": {"site.about_text": {"status": "fetch_failed",
                                             "reason": "the About page 404s"}}}
    card = pk.explain(packet, questions=[
        {"id": "about_q", "text": "What does the About page say?",
         "requires": ("site.about_text",)},
        {"id": "services_q", "text": "What does the Services page say?",
         "requires": ("site.services_text",)}])
    body = flat(card)
    # recorded: the packet's own status word and reason travel
    assert "the packet records it fetch_failed: the About page 404s" in body
    # not recorded: no fetch history is invented for it
    assert "records no absence for it" in body


def test_a_question_that_reads_another_kinds_blocks_is_not_applicable():
    packet = {"kind": "company", "entity_key": "c-6", "absent": {},
              "site": {"homepage_text": {"value": "We fabricate steel.",
                                         "source": "site",
                                         "observed_at": "2026-09-19",
                                         "status": "present"}}}
    card = pk.explain(packet, questions=[
        {"id": "role_is_operating", "text": "Is that role an operating one?",
         "requires": ("profile.current_experiences",)}])
    assert "not applicable to a company packet" in flat(card)
    assert "needs Current roles, with dates" not in flat(card)


# ------------------------------------------ an absence with no recorded reason

def test_an_absence_with_no_recorded_reason_says_so_and_invents_nothing():
    """"The packet recorded no reason; treat it as not looked for" is a fetch
    history nobody gathered."""
    packet = pk.build_company_packet(
        {"entity_key": "c-5", "name": "Cedar Ridge Landscaping"})
    for entry in (packet.get("absent") or {}).values():
        entry.pop("reason", None)
    card = pk.explain(packet)
    assert "reason unavailable" in card
    assert "not looked for" not in card
    # and a status the packet DID record still travels verbatim
    packet["absent"]["site.homepage_text"] = {"status": "junk_page"}
    assert "junk_page" in pk.explain(packet)


def test_a_log_only_card_says_the_packets_absence_records_are_not_here():
    card = pk.explain(None, result={
        "action": "review", "verdict": None, "decision_score": None,
        "round": 1, "per_question": {}, "review_reasons": [],
        "packet_richness": {"tier": "partial", "kind": "person",
                            "present_fields": ["profile.headline"],
                            "missing_required": ["profile.about"]}})
    assert "its absence records" in flat(card)
    assert "are not available here" in flat(card)
    assert "not looked for" not in card


# ------------------------------------------------------------- it never throws

def test_a_malformed_packet_renders_a_card_instead_of_raising():
    for junk in (None, "garbage", 7, [], {"kind": "company", "site": "not a block",
                                          "absent": 7}):
        card = pk.explain(junk)
        assert card.startswith("PACKET CARD")
        assert "THIN" in card


def test_explain_rules_never_raises():
    for junk in (None, "", 7, [], {"inclusions": object()}):
        assert "Rules the judge is given" in pk.explain_rules(junk)
