"""quantify.py ranks accepted rows by a packet number; check_precision.py measures the judge. No network."""
import csv
import json

import check_precision as cp
import quantify as q


def _scores(path, rows):
    cols = ["entity_key", "name", "domain", "decision", "action", "final_probability"]
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})


def _packet(dirpath, key, text):
    d = dirpath / "companies"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{key}.json").write_text(json.dumps({"packet": {"site": {"homepage_text": {
        "value": text, "source": f"https://{key}.example/", "status": "present"}}}}))


def test_extract_digits_and_words():
    assert q.extract("We run five offices.", q.DEFAULT_PATTERN)[0] == 5
    assert q.extract("partner with 12 general dentistry practices", q.DEFAULT_PATTERN)[0] == 12
    assert q.extract("our single office", q.DEFAULT_PATTERN) == (None, None)


def test_rank_orders_accepts_and_keeps_missing_last(tmp_path):
    pk = tmp_path / "packets"
    _packet(pk, "a", "three offices in town")
    _packet(pk, "b", "We partner with 12 general dentistry practices.")
    _packet(pk, "c", "A friendly practice.")
    _packet(pk, "d", "40 locations")
    s = tmp_path / "scores.csv"
    _scores(s, [{"entity_key": k, "name": k.upper(), "action": act, "decision": "icp_fit"}
                for k, act in (("a", "accept"), ("b", "accept"), ("c", "accept"), ("d", "reject"))])
    rows = q.rank(s, pk, metric="offices")
    assert [r["entity_key"] for r in rows] == ["b", "a", "c"]      # d was rejected
    assert rows[-1]["offices"] == ""                               # missing is not zero
    assert rows[0]["offices_quote"].startswith("12")


def test_precision_measure():
    scores = [{"entity_key": k, "action": a} for k, a in
              (("1", "accept"), ("2", "accept"), ("3", "reject"), ("4", "review"))]
    labels = [{"entity_key": "1", "your_answer": "yes"}, {"entity_key": "2", "your_answer": "no"},
              {"entity_key": "3", "your_answer": "yes"}, {"entity_key": "4", "your_answer": ""}]
    res = cp.measure(scores, labels)
    assert res["accept_precision"] == 0.5
    assert res["rejected_but_fit"] == 1 and res["unlabeled"] == 1
    assert len(res["disagreements"]) == 2
    assert res["insufficient_evidence"] is True


def test_one_accept_cannot_validate_precision():
    """1 accept + 99 insufficient used to report 100% precision from one row."""
    scores = [{"entity_key": "a", "action": "accept"}] + \
             [{"entity_key": str(i), "action": "insufficient"} for i in range(99)]
    rows = cp.sample(scores, n=30)
    assert [r["entity_key"] for r in rows if r["sample"] == "precision"] == ["a"]
    res = cp.measure(scores, [{"entity_key": "a", "your_answer": "yes"}])
    assert res["accept_precision"] == 1.0 and res["insufficient_evidence"]
    lo, hi = res["accept_precision_ci95"]
    assert lo < 0.3 and hi == 1.0


def test_accepts_are_sampled_separately_from_diagnostics():
    scores = [{"entity_key": str(i), "action": "accept" if i < 40 else ("reject" if i < 90 else "review")}
              for i in range(100)]
    rows = cp.sample(scores, n=30, n_other=10)
    prec = [r for r in rows if r["sample"] == "precision"]
    diag = [r for r in rows if r["sample"] == "diagnostic"]
    assert len(prec) == 30 and len(diag) == 10
    acts = {s["entity_key"]: s["action"] for s in scores}
    assert all(acts[r["entity_key"]] == "accept" for r in prec)
    assert all(acts[r["entity_key"]] != "accept" for r in diag)
    labels = [{"entity_key": r["entity_key"], "your_answer": "yes"} for r in prec]
    res = cp.measure(scores, labels)
    assert res["accepts_checked"] == 30 and not res["insufficient_evidence"]
