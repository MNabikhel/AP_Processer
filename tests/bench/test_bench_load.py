"""``python -m ap_coder.bench load``: people working on the real server (server mode) at once, each as a browser; the
checks of the database after the run; the report and its gate."""

import asyncio
import json
import time

import httpx

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
                "requests": {"errors_5xx": errors, "no_answer": 0, "cut_off": 0, "over_30s": 0}}  # fmt: skip

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


def test_a_person_keeps_a_connection_unused_as_long_as_keep_alive_says(monkeypatch):
    """httpx's 5 s by default, so earlier runs compare; a browser keeps its own for minutes (``--keep-alive 300``),
    which kept people on the web worker that took them (D-161)."""
    import argparse

    parser = argparse.ArgumentParser()
    load.add_parser(parser.add_subparsers(dest="cmd"))
    assert parser.parse_args(["load"]).keep_alive == load.KEEP_ALIVE == 5.0
    assert parser.parse_args(["load", "--keep-alive", "300"]).keep_alive == 300.0
    limits, made = [], httpx.Limits
    monkeypatch.setattr(httpx, "Limits", lambda **kw: limits.append(kw) or made(**kw))

    async def person() -> None:
        run = _run()
        run.keep_alive = 300.0
        await load.Person(run, load.Account("pat", "pw", "secret", "clerk"), 0, 0.0).close()

    asyncio.run(person())
    assert limits == [{"max_connections": 6, "max_keepalive_connections": 6, "keepalive_expiry": 300.0}]


def test_a_304_keeps_what_the_browser_stored_so_a_font_stays_fresh_after_it_is_checked_again():
    # Starlette's StaticFiles (the fonts, the logo): no Cache-Control, so a tenth of the file's age; its 304 carries
    # the ETag and the Date but no Last-Modified. The stored headers are updated with the 304's, the rest kept.
    seen = []

    def server(request):
        seen.append(request.headers.get("if-none-match"))
        date = {"date": "Thu, 11 Jan 2024 00:00:00 GMT", "etag": '"abc"'}
        if request.headers.get("if-none-match") == '"abc"':
            return httpx.Response(304, headers=date)
        return httpx.Response(200, headers={**date, "last-modified": "Mon, 01 Jan 2024 00:00:00 GMT"}, content=b"f")

    async def browse():
        me = load.Person(_run(), load.Account("pat", "pw", "secret", "clerk"), 0, 0.0)
        await me.client.aclose()
        me.client = httpx.AsyncClient(base_url=me.run.base, transport=httpx.MockTransport(server))
        try:
            await me.static("/fonts/Inter.woff2")
            me.cache["/fonts/Inter.woff2"].fresh_until = 0.0  # a day later: stale, asked again
            await me.static("/fonts/Inter.woff2")
            await me.static("/fonts/Inter.woff2")  # fresh again for a day: from the cache
        finally:
            await me.close()
        return me

    me = asyncio.run(browse())
    assert seen == [None, '"abc"'] and me.run.counts["static from the cache"] == 1


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


def test_a_screen_fetches_the_vendor_list_its_page_names_and_its_reader_poll_sends_the_panels_tag():
    """D-160: the vendor list is fetched from the address in #vendor-seed (none for an empty list), and the reader
    poll sends what its panel shows (``data-tag``)."""
    page = """<form id="inv" method="post" action="/review/7/approve"></form>
      <script type="application/json" id="vendor-seed">{"url": "/review/vendors.json?v=ab12", "count": 3}</script>
      <div data-reader data-state="waiting" data-poll="20" data-tag="c0ffee">waiting</div>"""
    screen = load.Screen(7, page)
    assert screen.editable and screen.vendors == "/review/vendors.json?v=ab12"
    assert (screen.poll, screen.tag) == (20, "c0ffee")
    assert load.Screen(7, page.replace('"count": 3', '"count": 0')).vendors == ""
    assert load.route_of("GET", "/review/7/reader?seen=c0ffee") == "GET /review/{id}/reader"
    assert load.route_of("GET", "/review/vendors.json?v=ab12") == "GET /review/vendors.json"


def test_a_screen_leaves_out_the_rows_of_its_templates_as_the_page_does():
    """B-1622: the review screen's new-row templates (``<template id="line-template">``, a new tax row) were taken for
    rows of the form: every check the load test sent had an empty line and a GST row more than the browser's, so each
    kept a draft and each approval added a GST line of 0.00."""
    page = """<form id="inv" method="post" action="/review/7/approve"></form>
      <input form="inv" name="line_description" value="Paper">
      <template id="line-template"><tr><td><input form="inv" name="line_description" value="">
        <select form="inv" name="tax_type"><option value="GST" selected>GST</option></select><br/>
        <textarea form="inv" name="line_reason"></textarea></td></tr></template>
      <div data-live="bar"><input form="inv" name="override" value="k1"></div>"""
    screen = load.Screen(7, page)
    assert screen.data("inv") == [("line_description", "Paper"), ("override", "k1")]
    assert screen.field("override").section == "bar"


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


def test_a_request_still_under_way_when_the_run_stops_its_people_is_recorded_as_cut_off(monkeypatch):
    async def never(request):
        await asyncio.Event().wait()

    async def stuck(run, account, index, start):  # a person whose page never comes
        me = load.Person(run, account, index, start)
        await me.client.aclose()
        me.client = httpx.AsyncClient(base_url=run.base, transport=httpx.MockTransport(never))
        try:
            await me.page("/review", due=start)
        finally:
            await me.close()

    monkeypatch.setattr(load, "person", stuck)
    monkeypatch.setattr(load, "STOP_GRACE", 0.0)  # stopped as soon as every timeout is past
    run = _run(timeout=0.15, duration=0.1)
    asyncio.run(load.drive(run, [load.Account("pat", "pw", "secret", "clerk")]))
    (sample,) = run.samples
    assert sample[0] == "GET /review" and sample[7] == "cut off" and 0.24 <= sample[3] < 5
    assert run.counts["people stopped at the end, a request still under way"] == 1
    report = load.build_report(run, {}, {})
    r = report["requests"]
    assert r["cut_off"] == 1 and r["no_answer"] == 0 and report["routes"]["GET /review"]["no_answer"] == {"cut off": 1}
    assert report["pages"]["n"] == 1 and report["pages"]["p95"] >= 0.24
    assert load.gate(report, None, 1.5)[0].startswith("errors: 0 5xx, 0 without an answer, 1 cut off at the end")


def test_the_access_logs_read_after_a_run_keep_the_lines_of_a_log_rotated_meanwhile(tmp_path):
    data, out = tmp_path / "data", tmp_path / "out"
    logs = data / "logs"
    logs.mkdir(parents=True)
    out.mkdir()
    old, new = (f"2026-11-05 09:00:00,000 10.0.0.1 pat GET /review 200 900 {ms}\n" for ms in (5, 40))
    (logs / "access-1.log").write_text(old * 3, encoding="utf-8")  # before the run
    (logs / "access-1.log.1").write_text(old * 4, encoding="utf-8")  # rotated before it
    before = load.Server(data, out, 2, "http://127.0.0.1:1/v1", "127.0.0.1", 1).logs_before
    with (logs / "access-1.log").open("a", encoding="utf-8") as f:
        f.write(new * 2)  # the run's first lines, then the log rotates (RotatingFileHandler)...
    (logs / "access-1.log.1").rename(logs / "access-1.log.2")
    (logs / "access-1.log").rename(logs / "access-1.log.1")
    (logs / "access-1.log").write_text(new * 4, encoding="utf-8")  # ...and goes on, past where the old one was
    (logs / "access-2.log").write_text(new * 2, encoding="utf-8")  # the second worker's, made in the run
    found = load.read_logs(data, out, before)
    assert found["access_lines"] == 8 and found["server_seconds"]["GET /review"]["p50"] == 0.04


def test_the_requests_a_second_of_each_minute_are_over_the_seconds_it_covers():
    # A 90 s run: minute 0 has 30 requests in two of its six 10 s spans (0.5 a second, not 1.5); minute 1 is 30 s
    # long, with 15 requests in one span (0.5 a second again).
    samples = [_sample("GET /review", 1.0, end=t) for t in [5.0] * 15 + [15.0] * 15 + [75.0] * 15]
    cfg = {"users": 3, "duration": 90.0, "workers": 1, "ramp": 0.0, "think": 1.0, "invoices": 10, "data": "a folder",
           "model": "a stand-in", "host": "127.0.0.1", "keep_alive": 5.0}  # fmt: skip
    text = load.markdown(load.build_report(_run(samples, duration=90.0), cfg, {"started": "2026-11-05T09:00:00"}))
    assert "| 0 min | 0.5 | 0 | 1.00 s |" in text and "| 1 min | 0.5 | 0 | 1.00 s |" in text


def test_a_search_export_is_asked_for_followed_and_downloaded_as_the_page_does(monkeypatch):
    """On the server search to Excel and CSV are jobs of the background process (D-169): the page's link asks with its
    header, follows the job (``app.js`` ``pollJob``: at once, then 0.5 s, half as long again while nothing changes,
    5 s at most) and downloads the file once made. The bench asked for the plain address instead, whose web worker
    waits for the file holding no thread: not what a browser does. A person with too many files being made already
    is refused, and that is counted."""
    seen, refusing = [], []
    snaps = iter([("running", 0), ("running", 0), ("running", 0), ("running", 5), ("done", 9)])

    def server(request):
        path, query = request.url.path, request.url.query.decode()
        seen.append((path, query, request.headers.get("x-ap-coder"), request.headers.get("accept")))
        if path == "/search":
            return httpx.Response(200, html="<html><body>Search</body></html>")
        if path == "/downloads/7":
            state, done = next(snaps)
            return httpx.Response(200, json={"state": state, "done": done, "total": 9, "note": "", "error": ""})
        if query == "job=7":
            return httpx.Response(200, content=b"PK the workbook")
        if refusing:
            return httpx.Response(429, json={"ok": False, "error": "You have 3 files being made already."})
        return httpx.Response(200, json={"ok": True, "job": 7, "status": "/downloads/7", "file": f"{path}?job=7"})

    waits, real_sleep = [], asyncio.sleep

    async def kept(seconds):  # (no waiting in a test: each wait's length kept)
        waits.append(seconds)
        await real_sleep(0)

    monkeypatch.setattr(load.asyncio, "sleep", kept)

    async def export():
        run = _run()
        run.deadline = time.monotonic() + 60
        me = load.Person(run, load.Account("pat", "pw", "secret", "clerk"), 0, time.monotonic())
        await me.client.aclose()
        me.client = httpx.AsyncClient(base_url=run.base, transport=httpx.MockTransport(server))
        me.think = lambda share=1.0: 0.0
        try:
            await me.export_search()
            refusing.append(True)
            await me.export_search()
        finally:
            await me.close()
        return run

    run = asyncio.run(export())
    kind = seen[1][0].rsplit(".", 1)[1]
    assert seen[1][2:] == ("1", "application/json")  # the page's header: asked for its job
    assert [s[0] for s in seen[:8]] == ["/search", f"/search/export.{kind}", *["/downloads/7"] * 5,
                                        f"/search/export.{kind}"] and seen[7][1] == "job=7"  # fmt: skip
    assert waits == [0.5, 0.75, 1.125, 0.5]  # the first look at once; after an answer that changed, 0.5 s again
    assert [s[0] for s in run.samples[1:8]] == [f"GET /search/export.{kind}", *["GET /downloads/{id}"] * 5,
                                                f"GET /search/export.{kind}?job={{id}}"]  # fmt: skip
    assert run.counts["search exports downloaded"] == 1 and run.counts["search exports refused (too many at once)"] == 1


def test_a_search_export_whose_job_failed_or_whose_file_is_gone_fails_the_gate(monkeypatch):
    """A job the background process could not make answers 200 ``{"state": "error"}`` at its status: the bench counted
    a search export "not made" and the gate passed, where the plain address it asked before answered a 500 and the
    gate failed. A file no longer kept (404) was counted downloaded. Both are now an export not made, which fails the
    gate; one still being made when the person stops waiting for it (the run's end) does not."""
    monkeypatch.setattr(load, "JOB_MOST", 0.0)  # (the person stops waiting after the first look)

    def server(state: str, file_status: int):
        def answer(request):
            path, query = request.url.path, request.url.query.decode()
            if path == "/search":
                return httpx.Response(200, html="<html><body>Search</body></html>")
            if path == "/downloads/7":
                return httpx.Response(200, json={"state": state, "done": 0, "total": 9, "note": "",
                                                 "error": "The file could not be made (it stopped)."})  # fmt: skip
            if query == "job=7":
                return httpx.Response(file_status, content=b"PK the workbook")
            return httpx.Response(200, json={"ok": True, "job": 7, "status": "/downloads/7", "file": f"{path}?job=7"})

        return answer

    async def export(server):
        run = _run()
        run.deadline = time.monotonic() + 60
        me = load.Person(run, load.Account("pat", "pw", "secret", "clerk"), 0, time.monotonic())
        await me.client.aclose()
        me.client = httpx.AsyncClient(base_url=run.base, transport=httpx.MockTransport(server))
        me.think = lambda share=1.0: 0.0
        try:
            await me.export_search()
        finally:
            await me.close()
        return load.build_report(run, {}, {})

    for state, file_status in (("error", 200), ("done", 404)):
        report = asyncio.run(export(server(state, file_status)))
        assert report["counts"] == {"search exports not made": 1}, (state, file_status)
        assert load.gate(report, None, 1.5) == ["search exports not made: 1"]
    report = asyncio.run(export(server("running", 200)))
    assert report["counts"] == {"search exports still being made when left": 1} and load.gate(report, None, 1.5) == []
    report = asyncio.run(export(server("done", 200)))
    assert report["counts"] == {"search exports downloaded": 1} and load.gate(report, None, 1.5) == []


def test_each_server_processs_memory_slope_is_reported_and_a_limit_on_it_fails_the_run():
    """Phase 8's hour-long run is judged on how fast each worker's memory grows once its caches are full (D-195): the
    slope of its RSS (MB a 10 minutes, least squares), over the run and over its second half. A worker that fills its
    caches in the first minutes and then stays flat has a second-half slope of 0; one that keeps growing doesn't, and
    ``--max-slope`` fails the run then."""
    sampler = load.Sampler({}, 0.0, every=10.0)
    flat = [(t, t / 100, 300.0 + min(t, 600) / 2) for t in range(0, 3610, 10)]  # +300 MB in 10 minutes, then flat
    growing = [(t, t / 100, 300.0 + t / 60) for t in range(0, 3610, 10)]  # 10 MB a 10 minutes, all along
    sampler.series = {11: {"label": "worker", "points": flat}, 12: {"label": "worker", "points": growing},
                      13: {"label": "model", "points": [(0, 0, 20.0), (30, 0, 25.0)]}}  # fmt: skip
    one, two, model = sampler.summary()
    assert one["process"] == "worker 1" and one["rss_slope"] > 20 and one["rss_slope_late"] == 0.0
    assert two["rss_slope"] == two["rss_slope_late"] == 10.0
    assert model["rss_slope"] is None  # under a minute of samples: no slope
    assert load.rss_slope([(0, 0, 100.0), (60, 0, 101.0)]) is None  # two points: none either
    report = {"routes": {}, "counts": {}, "audit": {}, "logs": {}, "processes": [one, two, model],
              "requests": {"errors_5xx": 0, "no_answer": 0, "cut_off": 0, "over_30s": 0}}  # fmt: skip
    assert load.gate(report, None, 1.5) == []  # reported, not judged, without --max-slope
    report["config"] = {"max_slope": 5.0}
    assert load.gate(report, None, 1.5) == ["worker 2's memory grew 10.0 MB a 10 minutes over the run's second half "
                                            "(more than 5)"]  # fmt: skip
    short = {**one, "rss_slope": 40.0, "rss_slope_late": None}  # (a 90 s run: no second half to judge, B-2038)
    report["processes"] = [short, model]
    said = "the memory slope can't be judged: the run's second half has under a minute of samples"
    assert load.gate(report, None, 1.5) == [f"{said} (--max-slope wants a run of 2 minutes or more)"]
    report["processes"] = [one, short, model]  # (a worker started afresh late, beside one judged: as before)
    assert load.gate(report, None, 1.5) == []
    cfg = {"users": 3, "duration": 3600.0, "workers": 2, "ramp": 0.0, "think": 1.0, "invoices": 10, "data": "a folder",
           "model": "a stand-in", "host": "127.0.0.1", "keep_alive": 5.0}  # fmt: skip
    text = load.markdown(load.build_report(_run(), cfg, {"started": "2026-11-07T09:00:00", "processes": [one, two]}))
    assert "| worker 2 |" in text and "| +10.0, +10.0 |" in text and "RSS MB a 10 min: whole run, second half" in text


def test_a_process_that_has_ended_gives_no_memory_sample(tmp_path):
    """B-2049: the sample taken as the run stops found the web workers ended but not yet waited for (zombies, an RSS of
    0 in /proc), and kept "0 MB" as their last memory: the hour at 300,000 invoices said the workers ended at 0.0 MB
    and their second-half slope was −10 to −23 MB a 10 minutes, where it was +2 to +14. An ended process gives no
    sample; one still running does."""

    def stat(pid: int, state: str, ppid: int, ticks: int, pages: int) -> None:
        rest = [state, str(ppid)] + ["0"] * 9 + [str(ticks), "0"] + ["0"] * 8 + [str(pages)]
        (tmp_path / str(pid)).mkdir(exist_ok=True)
        (tmp_path / str(pid) / "stat").write_text(f"{pid} (python) {' '.join(rest)}", encoding="utf-8")
        (tmp_path / str(pid) / "cmdline").write_bytes(b"python\0-c\0from multiprocessing.spawn import spawn_main")

    sampler = load.Sampler({"server": 100}, 0.0)
    sampler.proc, sampler.psutil, sampler.tick, sampler.page = tmp_path, False, 100, 1_000_000
    stat(100, "S", 1, 100, 60)
    stat(101, "S", 100, 200, 700)
    sampler.sample()
    stat(101, "Z", 100, 300, 0)  # (ended, not yet waited for)
    sampler.sample()
    server, worker = sampler.summary()
    assert server["process"] == "server" and len(sampler.series[100]["points"]) == 2
    assert worker["process"] == "worker 1" and worker["rss_mb_last"] == worker["rss_mb_peak"] == 700.0
    assert len(sampler.series[101]["points"]) == 1
