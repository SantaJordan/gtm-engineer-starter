"""Job 8 `--preview` — the stop before the spend.

The rule this file exists to enforce: before any paid
pass, the operator sees the packet CARDS and the rules exactly as the judge
reads them, hears the cost, and says yes. In a hosted install that is a hard stop,
not a printed summary an agent walks past. So what is guarded here is:

  1. **It stops.** Exit code 0, the stop line on stdout, and NOTHING beyond the
     preview's own small sample built — a preview that quietly builds the list
     is not a preview.
  2. **The sample is stratified.** One rich, one partial, one thin where they
     exist. An operator shown three rich packets has approved a run whose thin
     rows they never saw.
  3. **The estimate is arithmetic anyone can check** — a per-row rate times a
     row count, per lane, with the paid lane's OFF state stated rather than
     silently costed at zero.
  4. **Nothing is paid for twice.** The rows the preview built are in the index,
     so `--yes` (and any later `--resume` run) skips them — and since the
     2026-09-21 blind review, a run into a directory that already holds built
     rows REFUSES without `--resume` or `--rebuild` rather than silently paying
     for them again, while the stop line prints the continuation command with
     `--resume` already in it.
  5. **It is offline here.** Every test in this file replays a cache; none of
     them may reach the network.

Every company below is invented.
"""
import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
import build_packets as bp                                  # noqa: E402
import cost_ledger as cl                                    # noqa: E402
from lib.fetch_cache import page_key                        # noqa: E402


def _miss(url):
    """A fetch seam that answers every URL with a cache miss, never the wire."""
    return {"error": "offline_cache_miss", "status": None}, ""


HOME = (
    "<html><head><meta property='og:site_name' content='Northgate Dental Care'>"
    "</head><body><nav><a href='/about'>About Us</a></nav>"
    + "<p>Northgate Dental Care is an independently owned family practice. "
      "We have cleaned teeth on this corner since 1998.</p>" * 6
    + "</body></html>")
ABOUT = "<html><body><p>" + "Our team has six hygienists. " * 20 + "</p></body></html>"
MAPS = {"name": "Northgate Dental Care", "category": "Dentist",
        "subtypes": ["Dental clinic"], "business_status": "OPEN",
        "_lane": "dataforseo"}


def seed_cache(run_dir: Path) -> None:
    pc = run_dir / "page_cache"
    pc.mkdir(parents=True, exist_ok=True)
    meta, bodies = [], {}
    meta.append({"domain": "northgate-dental.example",
                 "fetched_at": "2026-09-19T00:00:00+00:00", "status": 200,
                 "final_url": "https://northgate-dental.example/",
                 "redirect_chain": [], "title": "", "tls_names": [],
                 "favicon_mmh3": None, "content_len": len(HOME),
                 "from_cache": False})
    bodies["northgate-dental.example"] = HOME
    url = "https://northgate-dental.example/about"
    key = page_key(url)
    meta.append({"domain": key, "fetched_at": "2026-09-19T00:00:00+00:00",
                 "status": 200, "final_url": url, "redirect_chain": [],
                 "title": "", "tls_names": [], "favicon_mmh3": None,
                 "content_len": len(ABOUT), "from_cache": False})
    bodies[key] = ABOUT
    (pc / "meta.jsonl").write_text("".join(json.dumps(m) + "\n" for m in meta))
    for name, body in bodies.items():
        (pc / f"{name}.html").write_text(body)


def companies_csv(tmp_path: Path, n=20) -> Path:
    rows = ["Northgate Dental Care,northgate-dental.example,Springfield,IL"]
    rows += [f"Practice {i},,Springfield,IL" for i in range(1, n)]
    path = tmp_path / "companies.csv"
    path.write_text("name,domain,city,state\n" + "\n".join(rows) + "\n")
    return path


def icp_json(tmp_path: Path) -> Path:
    path = tmp_path / "icp.json"
    path.write_text(json.dumps({
        "inclusions": ["general dentistry"],
        "exclusions": ["orthodontics", "oral surgery"],
        "territories": ["IL"]}))
    return path


def run_dir(tmp_path: Path) -> Path:
    out = tmp_path / "run"
    out.mkdir()
    seed_cache(out)
    return out


def index_keys(out: Path) -> list:
    path = out / bp.PACKETS_DIRNAME / bp.INDEX_NAME
    if not path.exists():
        return []
    return [json.loads(line)["key"]
            for line in path.read_text().splitlines() if line.strip()]


# ------------------------------------------------------------------- the stop

def test_preview_stops_at_exit_zero_with_the_stop_line(tmp_path, capsys):
    out = run_dir(tmp_path)
    code = bp.main(["--run-dir", str(out), "--companies",
                    str(companies_csv(tmp_path)), "--icp", str(icp_json(tmp_path)),
                    "--offline", "--preview", "3"])
    printed = capsys.readouterr().out
    assert code == 0
    assert bp.PREVIEW_STOP_LINE in printed


def test_preview_builds_the_sample_and_nothing_beyond_it(tmp_path, capsys):
    """`--preview 3` may build ten rows — a tier distribution read off three is
    not a distribution — and may never build the twenty in the file."""
    out = run_dir(tmp_path)
    bp.main(["--run-dir", str(out), "--companies", str(companies_csv(tmp_path, 20)),
             "--offline", "--preview", "3"])
    capsys.readouterr()
    keys = index_keys(out)
    assert len(keys) == bp.PREVIEW_BUILD_MIN
    assert len(keys) < 20


def test_preview_writes_the_cards_to_a_file(tmp_path, capsys):
    out = run_dir(tmp_path)
    bp.main(["--run-dir", str(out), "--companies", str(companies_csv(tmp_path)),
             "--icp", str(icp_json(tmp_path)), "--offline", "--preview", "2"])
    capsys.readouterr()
    path = out / bp.PACKETS_DIRNAME / bp.PREVIEW_FILE
    assert path.exists()
    text = path.read_text()
    assert "PACKET CARD" in text
    assert "orthodontics" in text                  # the rules, verbatim
    assert bp.PREVIEW_STOP_LINE in text


def test_the_printed_preview_carries_cards_rules_tiers_and_the_estimate(tmp_path, capsys):
    out = run_dir(tmp_path)
    bp.main(["--run-dir", str(out), "--companies", str(companies_csv(tmp_path)),
             "--icp", str(icp_json(tmp_path)), "--offline", "--preview", "3"])
    printed = capsys.readouterr().out
    assert "PACKET CARD" in printed
    assert "Rules the judge is given (as data)" in printed
    assert "orthodontics" in printed
    assert "Rows built for this preview, by tier:" in printed
    assert "Cost estimate for the FULL list — an ESTIMATE, not a bill" in printed
    # the arithmetic is shown, per lane
    assert "rows x $" in printed and "/row = $" in printed


# ------------------------------------------------------------- the sample

def test_the_sample_is_stratified_by_tier(tmp_path):
    docs = [{"key": "a", "richness": {"tier": "rich"}},
            {"key": "b", "richness": {"tier": "rich"}},
            {"key": "c", "richness": {"tier": "partial"}},
            {"key": "d", "richness": {"tier": "thin"}}]
    chosen = bp.pick_preview(docs, 3)
    assert {d["richness"]["tier"] for d in chosen} == {"rich", "partial", "thin"}
    # and it is stable: the same docs give the same three, in input order
    assert [d["key"] for d in chosen] == [d["key"] for d in bp.pick_preview(docs, 3)]


def test_the_sample_falls_back_to_order_when_a_tier_is_missing(tmp_path):
    docs = [{"key": k, "richness": {"tier": "thin"}} for k in "abcd"]
    assert [d["key"] for d in bp.pick_preview(docs, 3)] == ["a", "b", "c"]


def test_the_preview_shows_a_rich_row_when_one_exists(tmp_path):
    out = run_dir(tmp_path)
    result = bp.preview(out, show=3, companies=str(companies_csv(tmp_path)),
                        icp=str(icp_json(tmp_path)), offline=True)
    tiers = [(d["richness"]["tier"]) for d in result["shown"]]
    assert "rich" in tiers or "partial" in tiers
    assert "thin" in tiers


# ------------------------------------------------------------- the estimate

def test_the_estimate_prices_the_whole_list_not_the_sample(tmp_path):
    out = run_dir(tmp_path)
    result = bp.preview(out, show=2, companies=str(companies_csv(tmp_path, 20)),
                        offline=True)
    est = result["estimate"]
    assert est["rows"] == 20                        # the file, not the sample
    assert est["total_usd"] == 0.0                  # the paid lane is off
    assert est["maps_if_enabled_usd"] > 0           # and what it would cost is said


def test_an_off_paid_lane_is_stated_rather_than_silently_zero(tmp_path):
    est = bp.estimate([{"name": "a"}] * 5, [], maps_budget_usd=0.0, profiles=False)
    maps = next(l for l in est["lanes"] if l["lane"] == "maps")
    assert maps["usd"] == 0.0
    assert "PAID lane is OFF" in maps["note"]
    assert "would cost" in maps["note"]
    text = bp.render_estimate(est)
    assert "5 rows x $0.002000/row = $0.010000" in text


def test_a_budget_caps_the_estimate(tmp_path):
    est = bp.estimate([{"name": "a"}] * 100, [], maps_budget_usd=0.02,
                      profiles=False)
    maps = next(l for l in est["lanes"] if l["lane"] == "maps")
    assert maps["usd"] == 0.02                      # the budget, not 100 x rate
    assert est["maps_if_enabled_usd"] == pytest.approx(100 * bp.MAPS_COST_USD)


def test_the_profile_lane_is_counted_in_rows_that_could_be_asked(tmp_path):
    people = [{"linkedin_url": "https://www.linkedin.com/in/a"}, {"linkedin_url": ""}]
    est = bp.estimate([], people, maps_budget_usd=0.0, profiles=True)
    assert est["profile_rows"] == 1                 # the row with no URL is not one


# --------------------------------------------------------------- nothing twice

def test_the_full_run_resumes_the_previewed_rows(tmp_path, capsys):
    out = run_dir(tmp_path)
    csv_path = companies_csv(tmp_path, 20)
    bp.main(["--run-dir", str(out), "--companies", str(csv_path), "--offline",
             "--preview", "2"])
    capsys.readouterr()
    first = index_keys(out)
    assert len(first) == bp.PREVIEW_BUILD_MIN

    summary = bp.run(out, companies=str(csv_path), offline=True, resume=True)
    assert summary["built_this_run"] == 20 - bp.PREVIEW_BUILD_MIN
    keys = index_keys(out)
    assert len(keys) == 20
    assert len(set(keys)) == 20                     # no row built twice


def test_yes_goes_on_to_build_the_rest_without_rebuilding(tmp_path, capsys):
    out = run_dir(tmp_path)
    code = bp.main(["--run-dir", str(out), "--companies",
                    str(companies_csv(tmp_path, 14)), "--offline",
                    "--preview", "2", "--yes"])
    printed = capsys.readouterr().out
    assert code == 0
    assert bp.PREVIEW_STOP_LINE not in printed      # --yes does not stop
    keys = index_keys(out)
    assert len(keys) == 14 and len(set(keys)) == 14


# ------------------------------------------------------------------ the rules

def test_a_rule_file_can_be_json_or_yaml(tmp_path):
    path = tmp_path / "icp.yaml"
    path.write_text("inclusions:\n  - general dentistry\nexclusions:\n"
                    "  - orthodontics\n")
    loaded = bp.load_rules(path)
    assert loaded["exclusions"] == ["orthodontics"]
    assert bp.load_rules(icp_json(tmp_path))["territories"] == ["IL"]
    assert bp.load_rules(None) is None


def test_the_preview_never_reaches_the_network(tmp_path, monkeypatch):
    """`--offline` means offline. A preview that fetches is a preview that
    spends, which is the one thing this mode exists to prevent."""
    def explode(*a, **kw):                                  # pragma: no cover
        raise AssertionError("the preview tried to open a socket")
    import socket
    monkeypatch.setattr(socket, "socket", explode)
    monkeypatch.setattr(socket, "create_connection", explode)
    out = run_dir(tmp_path)
    result = bp.preview(out, show=2, companies=str(companies_csv(tmp_path)),
                        offline=True)
    assert result["summary"]["built_this_run"] == bp.PREVIEW_BUILD_MIN


def test_a_second_preview_costs_nothing_and_still_shows_cards(tmp_path, capsys):
    """A preview run twice must not buy the same Maps record twice. It always
    resumes, and reads the packets it already built back off disk so there is
    still something to show."""
    out = run_dir(tmp_path)
    csv_path = companies_csv(tmp_path, 20)
    bp.main(["--run-dir", str(out), "--companies", str(csv_path), "--offline",
             "--preview", "3"])
    capsys.readouterr()
    first = index_keys(out)

    result = bp.preview(out, show=3, companies=str(csv_path), offline=True)
    assert result["summary"]["built_this_run"] == 0      # nothing was rebuilt
    assert index_keys(out) == first                      # and nothing was added
    assert len(result["shown"]) == 3                     # but the cards are there
    assert all(d.get("packet") for d in result["shown"])


# ------------------------------------------- the continuation, and the refusal

def test_the_stop_line_and_the_printed_continuation_both_carry_resume(tmp_path, capsys):
    """"Re-run without --preview" defaults `resume` to False, so the operator
    who follows the instruction literally rebuilds every row the preview just
    built and re-runs its paid lanes."""
    out = run_dir(tmp_path)
    argv = ["--run-dir", str(out), "--companies", str(companies_csv(tmp_path)),
            "--icp", str(icp_json(tmp_path)), "--offline", "--preview", "3"]
    code = bp.main(list(argv))
    printed = capsys.readouterr().out
    assert code == 0
    assert "--resume" in bp.PREVIEW_STOP_LINE
    command = bp.continuation_command(argv)
    assert command in printed
    assert command.endswith("--resume")
    assert "--preview" not in command and " 3" not in command
    # every other argument the operator typed is still in it
    for token in ("--run-dir", "--companies", "--icp", "--offline"):
        assert token in command


def test_a_run_into_a_directory_that_already_holds_packets_refuses(tmp_path, capsys):
    out = run_dir(tmp_path)
    csv_path = companies_csv(tmp_path, 20)
    bp.main(["--run-dir", str(out), "--companies", str(csv_path), "--offline",
             "--preview", "2"])
    capsys.readouterr()
    before = index_keys(out)
    assert len(before) == bp.PREVIEW_BUILD_MIN

    with pytest.raises(SystemExit) as exc:
        bp.main(["--run-dir", str(out), "--companies", str(csv_path),
                 "--offline"])
    message = str(exc.value)
    # how many rows are already there ...
    assert f"{bp.PREVIEW_BUILD_MIN} packet(s)" in message
    assert "tiers" in message
    # ... which lanes a rebuild would re-run ...
    for lane in ("pages", "registry", "maps", "profiles"):
        assert lane in message
    # ... and both ways out
    assert "--resume" in message and "--rebuild" in message
    # and it refused BEFORE building anything
    assert index_keys(out) == before


def test_resume_and_rebuild_are_the_two_ways_past_the_refusal(tmp_path, capsys):
    out = run_dir(tmp_path)
    csv_path = companies_csv(tmp_path, 14)
    bp.main(["--run-dir", str(out), "--companies", str(csv_path), "--offline",
             "--preview", "2"])
    capsys.readouterr()

    assert bp.main(["--run-dir", str(out), "--companies", str(csv_path),
                    "--offline", "--resume"]) == 0
    capsys.readouterr()
    assert len(index_keys(out)) == 14 and len(set(index_keys(out))) == 14

    # --rebuild is the explicit re-spend: it builds the whole list again
    assert bp.main(["--run-dir", str(out), "--companies", str(csv_path),
                    "--offline", "--rebuild"]) == 0
    printed = capsys.readouterr().out
    assert "14 built this run" in printed
    assert len(index_keys(out)) == 14 and len(set(index_keys(out))) == 14


def test_rebuild_and_resume_cannot_be_asked_for_together(tmp_path):
    out = run_dir(tmp_path)
    args = ["--run-dir", str(out), "--companies", str(companies_csv(tmp_path)),
            "--offline"]
    with pytest.raises(SystemExit):
        bp.main(args + ["--resume", "--rebuild"])
    with pytest.raises(SystemExit):
        bp.main(args + ["--preview", "2", "--rebuild"])


def test_a_preview_then_a_full_run_never_buys_a_row_twice(tmp_path):
    """The whole point, priced. The paid Maps lane is armed behind a fake
    provider and the page lane answers every URL with a cache miss, so this
    runs offline and still proves the spend: twenty rows, twenty purchases."""
    out = run_dir(tmp_path)
    csv_path = companies_csv(tmp_path, 20)
    ledger_path = out / "cost_ledger.jsonl"
    asked = []

    def paid(name, city, state):
        asked.append(name)
        return {"name": name, "category": "Dentist", "business_status": "OPEN"}

    lane = bp.Lanes(ledger=cl.Ledger(ledger_path), offline=False,
                    maps_budget_usd=1.0, maps_credentials="test-only-not-a-key",
                    maps_paid=paid, registry_mode="never",
                    registry_http=lambda url: None)

    bp.preview(out, show=2, companies=str(csv_path), lanes=lane,
               fetch_page=_miss)
    after_preview = list(asked)
    assert len(after_preview) == bp.PREVIEW_BUILD_MIN

    bp.run(out, companies=str(csv_path), resume=True, lanes=lane,
           fetch_page=_miss)
    assert len(asked) == 20
    assert len(set(asked)) == 20                 # no company bought twice
    assert asked[:bp.PREVIEW_BUILD_MIN] == after_preview
    assert lane.spend["maps_usd"] == pytest.approx(20 * bp.MAPS_COST_USD)

    rows = [json.loads(line) for line in ledger_path.read_text().splitlines()
            if line.strip()]
    bought = [r for r in rows if r["provider"] == "dataforseo"]
    assert len(bought) == 20
    assert len({r["row_id"] for r in bought}) == 20
    keys = index_keys(out)
    assert len(keys) == 20 and len(set(keys)) == 20
