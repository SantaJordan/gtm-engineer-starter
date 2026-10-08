"""Billing normalization, outcome mapping, provider failures, provisional grades."""
import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "adapters"))

import batch_themes as bt  # noqa: E402
import billing  # noqa: E402
import customers_from_dossiers as cfd  # noqa: E402
import panel_parsers as pp  # noqa: E402
import signal_presence as sp  # noqa: E402
import wlr_schema as ws  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]


def ev(t, fields, date="2024-01-01T00:00:00Z"):
    """A billing event in the dossier-builder's documented format."""
    return {"account_local_id": "a", "date": date, "source": "billing_event", "type": t,
            "subject": t, "raw_ref": {"system": "billing", "id": "x"}, "fields": fields,
            "provenance": {"rule": "r", "source": "s"}}


# ------------------------------------------------------------- billing

def test_documented_payment_succeeded_amount_counts():
    s = billing.summarize([ev("payment_succeeded", {"amount": 20000, "currency_original": "USD"})])
    assert s["revenue"] == 20000 and s["n_paid"] == 1


def test_unpaid_and_failed_events_add_nothing():
    s = billing.summarize([ev("invoice_unpaid", {"amount_usd": 20000}),
                           ev("payment_failed", {"amount": 500}),
                           ev("invoice_paid", {"amount_usd": "$1,200.00"})])
    assert s["revenue"] == 1200 and s["skipped_types"] == {"invoice_unpaid": 1, "payment_failed": 1}


def test_refunds_subtract_cents_convert_and_foreign_amounts_are_not_summed():
    s = billing.summarize([ev("payment_succeeded", {"amount_cents": 150000}),
                           ev("refund_issued", {"amount": 200}),
                           ev("payment_succeeded", {"amount": 900, "currency": "EUR"})])
    assert s["revenue"] == 1300 and s["non_usd"] == 1


def _customer(state, events=()):
    return {"account_id": "001", "account_name": "Co", "mode": "customer", "outcome": state,
            "timeline": list(events), "structured_signals": {}}


def _prospect(state):
    return {"account_id": "002", "account_name": "Pr", "mode": "prospect", "outcome": state,
            "timeline": [], "structured_signals": {"t_anchor": {"date": "2024-05-01T00:00:00Z",
                                                                "rule": "first_call_date"}}}


def _build(tmp_path, docs):
    for i, d in enumerate(docs):
        (tmp_path / f"{i}.json").write_text(json.dumps(dict(d, account_id=f"A{i}")))
    return {r["customer_id"]: r for r in cfd.build([tmp_path])}


def test_outcome_states_are_mapped_and_unresolved_ones_preserved(tmp_path):
    paid = [ev("payment_succeeded", {"amount": 1000})]
    rows = _build(tmp_path, [_customer("active", paid), _customer("cancelled", paid),
                             _customer("unknown"), _prospect("open"), _prospect("lost"),
                             _customer("active", [ev("invoice_unpaid", {"amount_usd": 20000})])])
    assert (rows["A0"]["outcome"], rows["A0"]["health"], rows["A0"]["label"]) == ("won", "healthy", "won")
    assert rows["A1"]["label"] == "lost" and rows["A1"]["health"] == "churned"
    assert (rows["A2"]["outcome"], rows["A2"]["label"]) == ("unknown", "")
    assert (rows["A3"]["outcome"], rows["A3"]["label"]) == ("open", "")
    assert rows["A4"]["label"] == "lost"
    assert rows["A5"]["lifetime_revenue"] == 0 and "invoice_unpaid" in rows["A5"]["label_evidence"]
    assert ws.normalize_customer(rows["A3"])["outcome"] == "open"
    assert "open" not in ws.GOOD_FIT_OUTCOMES | ws.NOT_GOOD_FIT_OUTCOMES


def test_unresolved_rows_do_not_move_the_median(tmp_path):
    docs = [_customer("active", [ev("payment_succeeded", {"amount": a})]) for a in (1000, 1000, 1000)]
    docs.append(_customer("unknown", [ev("payment_succeeded", {"amount": 1})]))
    docs.append(_customer("active", [ev("payment_succeeded", {"amount": 50000})]))
    rows = _build(tmp_path, docs)
    assert rows["A4"]["label"] == "10X"           # 50x the median of the RESOLVED payers
    assert rows["A3"]["label"] == ""


def test_documented_retry_success_counts_and_can_make_a_10x(tmp_path):
    """Regression: a dated $20,000 payment_retry_succeeded must count as revenue."""
    docs = [_customer("active", [ev("payment_succeeded", {"amount": 1000})]) for _ in range(3)]
    docs.append(_customer("active", [ev("payment_failed", {"amount": 20000}, "2024-03-01T00:00:00Z"),
                                     ev("payment_retry_succeeded", {"amount": 20000},
                                        "2024-03-04T00:00:00Z")]))
    rows = _build(tmp_path, docs)
    assert rows["A3"]["lifetime_revenue"] == 20000
    assert rows["A3"]["label"] == "10X"
    assert rows["A3"]["close_date"].startswith("2024-03-04")


def test_every_success_type_the_builder_prompt_emits_is_counted():
    prompt = (ROOT / "skills" / "dossier-builder" / "prompts" / "02b-source-pull.billing.md").read_text()
    import re
    emitted = {t for t in re.findall(r"`([a-z_]+)`", prompt) + re.findall(r'"type": "([a-z_]+)"', prompt)
               if "succeeded" in t or t in {"payment_received", "invoice_paid"}}
    assert {"payment_succeeded", "payment_retry_succeeded"} <= emitted
    assert emitted <= billing.SUCCESS_TYPES


def test_explicit_amount_usd_wins_over_a_foreign_amount():
    """Regression: {amount 100 EUR, amount_usd 110} is $110, not skipped."""
    s = billing.summarize([ev("payment_succeeded", {"amount": 100, "currency": "EUR", "amount_usd": 110})])
    assert s["revenue"] == 110 and s["non_usd"] == 0 and s["n_paid"] == 1


# ------------------------------------------------------------- provider failures

def test_provider_failures_and_empty_payloads_are_unknown_not_negative():
    sig = {"observability_source": "firecrawl_scrape", "queryable_field": "mentions_online_booking"}
    careers = {"observability_source": "firecrawl_map", "queryable_field": "has_careers_page"}
    for bad in ({"success": False, "error": "scrape failed"}, {}, {"data": {}}, "oops"):
        blob = pp.parse_firecrawl_scrape(bad)
        assert blob["success"] is False
        assert sp.signal_state(sig, {"signal_panel": {"firecrawl_scrape": blob}}) == "unknown"
    for bad in ({"success": False, "error": "x"}, {}):
        blob = pp.parse_firecrawl_map(bad)
        assert sp.signal_state(careers, {"signal_panel": {"firecrawl_map": blob}}) == "unknown"
    assert pp.parse_own_news({"status": "ERROR", "data": []})["success"] is False
    assert pp.parse_own_local_business({"status": "OK", "data": []})["success"] is False
    assert pp.parse_apify_linkedin([])["success"] is False
    assert pp.parse_exa({"error": "rate limited"})["success"] is False


def test_a_404_target_page_is_unknown_not_absent(monkeypatch):
    """Regression, through the live wrapper: Firecrawl succeeds but the page was a 404."""
    import enrich_panel as ep
    from types import SimpleNamespace as NS
    body = {"success": True, "data": {"markdown": "404 Page not found",
                                      "metadata": {"statusCode": 404, "title": "Not Found"}}}
    monkeypatch.setattr(ep, "FIRECRAWL_API_KEY", "test-key")
    monkeypatch.setattr(ep.requests, "post",
                        lambda *a, **k: NS(raise_for_status=lambda: None, json=lambda: body))
    blob = ep.firecrawl_scrape_homepage("northwind.example")
    assert blob["success"] is False and "HTTP 404" in blob["error"]
    sig = {"observability_source": "firecrawl_scrape", "queryable_field": "mentions_online_booking"}
    assert sp.signal_state(sig, {"signal_panel": {"firecrawl_scrape": blob}}) == "unknown"


def test_non_2xx_statuses_fail_every_provider_and_2xx_passes():
    for code in (403, 500, 503, "404"):
        page = {"data": {"markdown": "Book online today", "metadata": {"statusCode": code}}}
        assert pp.parse_firecrawl_scrape(page)["success"] is False
    ok = pp.parse_firecrawl_scrape({"data": {"markdown": "Book online", "metadata": {"statusCode": 200}}})
    assert ok["success"] is True and ok["data"]["mentions_online_booking"] is True
    assert pp.parse_firecrawl_scrape({"data": {"markdown": "x", "metadata": {"error": "blocked"}}})[
        "success"] is False
    assert pp.parse_firecrawl_map({"statusCode": 502, "links": []})["success"] is False
    assert pp.parse_own_news({"status": 429, "data": []})["success"] is False
    assert pp.parse_own_local_business({"status": "OK", "status_code": 404, "data": [{"name": "x"}]})[
        "success"] is False
    assert pp.parse_apify_linkedin([{"statusCode": 999, "employeesCount": 5}])["success"] is False
    exa = pp.parse_exa({"results": [{"id": "u1", "url": "u1", "text": "t"}],
                        "statuses": [{"id": "u1", "status": "error",
                                      "error": {"tag": "CRAWL_NOT_FOUND", "httpStatusCode": 404}}]})
    assert exa["success"] is False


def _mock_get(monkeypatch, body):
    import enrich_panel as ep
    from types import SimpleNamespace as NS
    monkeypatch.setattr(ep, "OPENWEBNINJA_API_KEY", "test-key")
    monkeypatch.setattr(ep.requests, "get",
                        lambda *a, **k: NS(raise_for_status=lambda: None, json=lambda: body))
    return ep


def test_a_503_news_record_is_unknown_never_firm(monkeypatch):
    """Regression: a news record carrying statusCode 503 must not date a signal."""
    import backcast_validate as bv
    ep = _mock_get(monkeypatch, {"status": "OK", "data": [
        {"title": "t", "link": "https://news.example/h", "date": "2024-01-15", "statusCode": 503,
         "snippet": "Acme is hiring a compliance officer", "source": "News"}]})
    blob = ep.openweb_ninja_news("Acme")
    assert blob["success"] is False
    hyp = {"hypothesis_id": "H", "signal": "s", "observability_source": "serper_news",
           "queryable_field": "items.snippet",
           "predicate": {"op": "regex", "value": r"hiring an? compliance officer"},
           "temporal_provability": {"best_case_tag": "RECORD_DATED_BEFORE", "how": "news date"},
           "record_date_field": "items.date"}
    panel = {"customer_id": "c1", "outcome": "won", "close_date": "2024-06-01",
             "domain": "c1.example", "signal_panel": {"serper_news": blob}}
    cell = bv.verify_cell_firm(hyp, panel, provisional_present=True)
    assert cell["firm_present"] is None and cell["visibility_tag"] != "RECORD_DATED_BEFORE"


def test_record_success_false_and_object_error_are_unknown_never_firm(monkeypatch):
    """Regression: a record with success:false, and a body whose `error` is an
    object, must not date a signal through the real wrapper + firm pass."""
    import backcast_validate as bv
    news = {"title": "t", "link": "https://news.example/h", "date": "2024-01-15",
            "snippet": "Acme is hiring a compliance officer", "source": "News"}
    hyp = {"hypothesis_id": "H", "signal": "s", "observability_source": "serper_news",
           "queryable_field": "items.snippet",
           "predicate": {"op": "regex", "value": r"hiring an? compliance officer"},
           "temporal_provability": {"best_case_tag": "RECORD_DATED_BEFORE", "how": "news date"},
           "record_date_field": "items.date"}
    for body in ({"status": "OK", "data": [dict(news, success=False)]},
                 {"error": {"message": "provider failed"}, "data": [news]}):
        ep = _mock_get(monkeypatch, body)
        blob = ep.openweb_ninja_news("Acme")
        assert blob["success"] is False, body
        panel = {"customer_id": "c1", "outcome": "won", "close_date": "2024-06-01",
                 "domain": "c1.example", "signal_panel": {"serper_news": blob}}
        assert bv.verify_cell_firm(hyp, panel, provisional_present=True)["firm_present"] is None


def test_one_failure_helper_covers_body_and_record():
    for bad in ({"success": False}, {"success": "false"}, {"ok": False}, {"error": {"code": 1}},
                {"error": "x"}, {"errors": ["x"]}, {"status": "ERROR"}, {"statusCode": 503},
                {"status": 404}, "text"):
        assert pp.failure_reason(bad) is not None, bad
        assert pp._record_failed(bad) and pp._provider_error(bad) is not None, bad
    for good in ({}, {"status": "OK"}, {"statusCode": 200}, {"error": None}, {"error": ""},
                 {"success": True}):
        assert pp.failure_reason(good) is None, good


def test_a_failed_news_record_is_dropped_and_good_ones_kept():
    blob = pp.parse_own_news({"status": "OK", "data": [
        {"title": "bad", "link": "x", "date": "2024-01-01", "error": "fetch failed"},
        {"title": "good", "link": "y", "date": "2024-02-01", "snippet": "ok"}]})
    assert blob["success"] is True and [i["title"] for i in blob["data"]["items"]] == ["good"]


def test_404_and_503_business_records_are_unknown_not_absent(monkeypatch):
    """Regression: failed business records must not produce a definite absent."""
    for code in (404, 503):
        ep = _mock_get(monkeypatch, {"status": "OK", "data": [
            {"name": "Acme", "statusCode": code, "rating": None, "phone_number": None}]})
        blob = ep.openweb_ninja_local_business("Acme", "Springfield")
        assert blob["success"] is False, code
        for field in ("phone", "rating", "website"):
            sig = {"observability_source": "openweb_ninja", "queryable_field": field}
            assert sp.signal_state(sig, {"signal_panel": {"openweb_ninja": blob}}) == "unknown"
    ok = pp.parse_own_local_business({"status": "OK", "data": [
        {"name": "Bad", "status_code": 503}, {"name": "Acme", "phone_number": "555"}]})
    assert ok["success"] is True and ok["data"]["name"] == "Acme"


def test_failed_apify_items_are_dropped():
    assert pp.parse_apify_linkedin([{"statusCode": 404}])["success"] is False
    ok = pp.parse_apify_linkedin([{"error": "blocked"}, {"employeesCount": 12}])
    assert ok["success"] is True and ok["data"]["employees_count"] == 12


def test_observed_empty_lists_are_still_observed():
    assert pp.parse_firecrawl_map({"links": []})["success"] is True
    assert pp.parse_own_news({"status": "OK", "data": []})["success"] is True


# ------------------------------------------------------------- provisional grades

def test_provisional_grades_reach_the_model_input_marked_and_discounted():
    import transcripts_md as tm
    calls_dir = ROOT / "examples" / "northwind-demo" / "corpus" / "calls"
    matched, attributed = tm.import_dir(calls_dir, {"northwind.example"})
    by_call = {a["call_id"]: a for a in attributed}
    river = [c for c in matched if c["customer_id"] == "001XX0000000101AAA"]
    msg = bt.build_user_message({"customer_id": "001XX0000000101AAA", "company": "Rivermark"},
                                {"signal_panel": {}}, None, river, by_call)
    assert "[customer · unprompted · provisional]" in msg
    assert "[customer · unprompted]" not in msg            # nothing is presented as confirmed
    assert "NOT confirmed" in msg and "gold signal" not in msg.lower()
    counts = bt.grade_counts(river, by_call)
    assert counts["customer_lines_confirmed"] == 0 and counts["customer_lines_provisional"] > 0


def test_a_confirmed_grade_is_rendered_as_confirmed():
    attr = {"call_id": "c1", "roster": [], "turns": [{"role": "customer", "text": "Our phones ring all day."}],
            "customer_statements": [{"text": "Our phones ring all day.", "elicitation": "unprompted",
                                     "confirmed": True}]}
    _, block = bt._render_attributed_call(attr)
    assert block == "[customer · unprompted] Our phones ring all day."


def test_parsed_themes_carry_the_provisional_flag(tmp_path, monkeypatch):
    P = bt.paths("t")
    monkeypatch.setattr(bt, "paths", lambda run_id: {k: tmp_path / v.name for k, v in P.items()})
    Q = bt.paths("t")
    Q["lookup"].write_text(json.dumps({"c1": {"customer_id": "X", "customer_lines_provisional": 3,
                                              "customer_lines_confirmed": 0}}))
    Q["results"].write_text(json.dumps({"custom_id": "c1", "result": {"message": {"content": [
        {"text": json.dumps({"pain_themes": [], "extraction_confidence": 0.5})}]}}}) + "\n")
    assert bt.cmd_parse("t") == 0
    theme = json.loads(Q["themes"].read_text().splitlines()[0])
    assert theme["grades_provisional"] and theme["requires_confirmation"]
    assert theme["unprompted_evidence_verified"] is False


def _sdk_message(*blocks):
    from types import SimpleNamespace as NS
    try:
        from anthropic.types import TextBlock, ThinkingBlock
        made = [ThinkingBlock(type="thinking", thinking=b[1], signature="sig") if b[0] == "thinking"
                else TextBlock(type="text", text=b[1]) for b in blocks]
    except Exception:  # SDK not installed: same attribute shape
        made = [NS(type=b[0], **({"thinking": b[1]} if b[0] == "thinking" else {"text": b[1]}))
                for b in blocks]
    return NS(content=made)


def test_thinking_then_text_response_keeps_the_answer():
    """Regression: a ThinkingBlock before the TextBlock must not empty the answer."""
    from types import SimpleNamespace as NS
    answer = json.dumps({"pain_themes": [], "extraction_confidence": 0.7})
    res = NS(custom_id="c1", result=NS(type="succeeded",
             message=_sdk_message(("thinking", "let me think"), ("text", answer))))
    row = bt.result_row(res)
    assert row["status"] == "ok" and bt.message_text(row["result"]["message"]) == answer


def test_download_and_parse_gate_on_failed_or_textless_results(tmp_path, monkeypatch):
    from types import SimpleNamespace as NS
    P = bt.paths("t")
    monkeypatch.setattr(bt, "paths", lambda run_id: {k: tmp_path / v.name for k, v in P.items()})
    Q = bt.paths("t")
    Q["info"].write_text(json.dumps({"batch_id": "b1"}))
    Q["lookup"].write_text(json.dumps({c: {"customer_id": c} for c in ("c1", "c2", "c3")}))
    good = json.dumps({"pain_themes": [], "extraction_confidence": 0.7})
    results = [
        NS(custom_id="c1", result=NS(type="succeeded",
           message=_sdk_message(("thinking", "hmm"), ("text", good)))),
        NS(custom_id="c2", result=NS(type="succeeded", message=_sdk_message(("thinking", "only")))),
        NS(custom_id="c3", result=NS(type="errored", message=None, error="overloaded")),
    ]
    batches = NS(retrieve=lambda _id: NS(processing_status="ended"), results=lambda _id: iter(results))
    monkeypatch.setattr(bt, "_client", lambda: NS(messages=NS(batches=batches)))
    assert bt.cmd_download("t") == 0
    saved = [json.loads(line) for line in Q["results"].read_text().splitlines()]
    assert [r["status"] for r in saved] == ["ok", "failed", "failed"]
    assert bt.cmd_parse("t") == 1                     # the run is gated, not silently thin
    themes = [json.loads(line) for line in Q["themes"].read_text().splitlines()]
    assert [t["_custom_id"] for t in themes] == ["c1"]
    errs = {json.loads(line)["custom_id"] for line in Q["errors"].read_text().splitlines()}
    assert errs == {"c2", "c3"}


def test_no_discriminator_causes_include_not_firm_checked_and_no_absence():
    import backcast_validate as bv
    flag = bv.no_discriminator_flag(1, [
        {"status": "provisional_only", "provisional_reasons": []},
        {"status": "unverifiable", "survival_reason": "insufficient evidence: no cell could be proven ABSENT"}])
    assert flag["causes"] == {"not_firm_checked": 1, "insufficient_evidence": 1}
