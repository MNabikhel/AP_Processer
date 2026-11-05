"""``python -m ap_coder.bench load``: people working on the real server (server mode) at once, each as a browser; the
checks of the database after the run; the report and its gate."""

import json

from ap_coder.bench import load
from ap_coder.bench.__main__ import main


def test_three_people_work_on_the_real_server_and_the_report_says_how_it_went(tmp_path):
    out = tmp_path / "load"
    code = main(["load", "--users", "3", "--duration", "15", "--ramp", "2", "--workers", "1", "--think", "1.5",
                 "--seed-invoices", "120", "--out", str(out)])  # fmt: skip
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert report["counts"]["logins"] == 3 and not report["problems"]  # password, then the authenticator's code
    r = report["requests"]
    assert r["total"] > 20 and r["errors_5xx"] == r["no_answer"] == r["over_30s"] == 0
    routes = report["routes"]
    assert routes["GET /review"]["n"] and routes["GET /static/{file}"]["n"] and routes["POST /login/code"]["n"] == 3
    assert report["counts"]["static from the cache"]  # the fonts kept, as a browser keeps them
    audit = report["audit"]
    assert not audit["approved_twice"] and not audit["exported_twice"] and not audit["approved_without_event"]
    assert audit["summary_checked"] >= 100 and not audit["summary_rows_differing"] and not audit["logins_paused"]
    assert report["harness"]["process_cores"] < 0.5 and report["config"]["stopped_in"] < 30
    assert code == (0 if report["gate"]["passed"] else 1)
    assert not (out / "data").exists()  # the folder seeded for the run is gone with it
    assert "## Routes" in (out / "report.md").read_text(encoding="utf-8")


def test_the_gate_fails_on_errors_and_on_a_route_slower_than_its_baseline():
    def report(p95: float, n: int = 50, errors: int = 0) -> dict:
        return {"routes": {"GET /review": {"n": n, "p95": p95}}, "counts": {}, "audit": {}, "logs": {},
                "requests": {"errors_5xx": errors, "no_answer": 0, "over_30s": 0}}  # fmt: skip

    baseline = report(0.40)
    assert load.gate(report(0.55), baseline, 1.5) == []
    assert "GET /review: p95 0.700 s" in load.gate(report(0.70), baseline, 1.5)[0]
    assert load.gate(report(0.04), report(0.01), 1.5) == []  # within the floor: noise on a quick route
    assert load.gate(report(9.0, n=3), baseline, 1.5) == []  # too few requests to compare
    assert load.gate(report(0.40, errors=1), baseline, 1.5)[0].startswith("errors: 1 5xx")


def test_a_browser_keeps_static_files_as_their_headers_say():
    assert load.freshness({"cache-control": "no-store"}) == (False, False, 0.0)
    assert load.freshness({"cache-control": "no-cache"})[:2] == (True, True)  # kept, asked again each time
    assert load.freshness({"cache-control": "public, max-age=31536000, immutable"}) == (True, False, 31536000.0)
    heuristic = {"last-modified": "Mon, 01 Jan 2024 00:00:00 GMT", "date": "Thu, 11 Jan 2024 00:00:00 GMT"}
    assert load.freshness(heuristic) == (True, False, 86400.0)  # a tenth of its age
    assert load.children_of("/static/js/invoice.js?v=1", 'import { a } from "./app.js";\nimport "../x/y.js";') == [
        "/static/js/app.js", "/static/x/y.js"]  # fmt: skip
    assert load.children_of("/static/app.css", 'src: url("/fonts/Inter.woff2") format("woff2")') == [
        "/fonts/Inter.woff2"]  # fmt: skip


def test_a_screen_sends_its_form_as_the_page_does_and_takes_the_sections_a_check_draws_again():
    page = """<form id="inv" method="post" action="/review/7/approve"><input type="hidden" name="grids" value="1">
      </form><input form="inv" name="vendor_name" value="Acme &amp; Co">
      <textarea form="inv" name="line_description">
Paper</textarea><select form="inv" name="tax"><option value="GST">GST<option value="HST" selected>HST</select>
      <input form="inv" type="checkbox" name="unticked" value="1"><input form="inv" name="off" value="x" disabled>
      <div data-live="bar"><input type="checkbox" name="override" value="k1" form="inv"></div>
      <form method="post" action="/review/7/park"><input name="reason"><button type="submit">Park</button></form>"""
    screen = load.Screen(7, page)
    assert screen.editable and screen.form_ending("/park") == "form1"
    assert screen.data("inv") == [("grids", "1"), ("vendor_name", "Acme & Co"), ("line_description", "Paper"),
                                  ("tax", "HST")]  # fmt: skip
    assert screen.data("form1", reason="Waiting") == [("reason", "Waiting")]
    screen.apply({"allow": False, "sections": {"bar": '<input type="checkbox" name="override" value="k2" form="inv" '
                                                      "checked>"}})  # fmt: skip
    assert screen.allow is False and screen.data("inv")[-1] == ("override", "k2")


def _run(samples=(), timeout=120.0, ramp=0.0, duration=60.0):
    run = load.Run("http://127.0.0.1:1", "127.0.0.1", 3, duration, ramp, 1.0, timeout, 1)
    run.samples = list(samples)
    return run


def _sample(route, seconds, kind="page", error="", end=1.0):
    return (route, kind, 0 if error else 200, seconds, seconds, end, 100, error)


def test_a_request_without_an_answer_counts_at_least_the_timeout_in_every_percentile():
    # 90 pages at 1 s, 10 timed out at 121 s: the p95 is the timeouts', not 1 s; a connection dropped after 10 ms is
    # no quick answer either.
    samples = [_sample("GET /review", 1.0) for _ in range(90)] + [_sample("GET /review", 121.0, error="timeout")
                                                                  for _ in range(10)]  # fmt: skip
    samples += [_sample("POST /review/{id}/approve", 0.2, "action") for _ in range(9)]
    samples.append(_sample("POST /review/{id}/approve", 0.01, "action", error="ConnectError"))
    report = load.build_report(_run(samples), {}, {})
    route = report["routes"]["GET /review"]
    assert route["p95"] == 121.0 and route["p50"] == 1.0 and route["n"] == route["count"] == 100
    assert route["no_answer"] == {"timeout": 10} and report["requests"]["no_answer"] == 11  # still said apart
    assert report["pages"]["p95"] == 121.0 and not report["targets"]["pages p95 <= 1.5 s"]
    assert report["actions"]["p95"] == 120.0 and not report["targets"]["actions p95 <= 1.0 s"]
    assert report["routes"]["POST /review/{id}/approve"]["max"] == 120.0
    assert report["timeline"][0]["p95"] == 121.0
    baseline = {"routes": {"GET /review": {"n": 100, "p95": 1.0}}}
    assert any(p.startswith("GET /review: p95 121.000 s") for p in load.gate(report, baseline, 1.5))
