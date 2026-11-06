"""``python -m ap_coder.bench day``: a morning's drop through a real company server against the stand-in model server
(ap_coder/bench/day.py), and its drills on the model machine."""

import argparse
import datetime as dt
import json
from types import SimpleNamespace

import pytest

from ap_coder.bench import day
from ap_coder.bench.__main__ import main
from ap_coder.bench.fake_model import FakeModel
from ap_coder.capture import layout


def _drill(kind, **kw):
    return argparse.Namespace(drill=kind, drill_at=kw.get("at", 2), drill_for=kw.get("length", 10.0),
                              slow_factor=kw.get("factor", 4.0))  # fmt: skip


def _report(pages, slots, parallel, chats=(), capture=1, reviewed=()):
    """The report of a drop of 4 invoices, at time x0.5, in the review queue 2 s after it, whose page reads ended 10,
    20, 30 and 40 s after it; ``pages``: the stand-in's (wait, seconds) of each read; ``chats``: the seconds of each
    arrival's chat answer, ``capture`` processes waiting for them; ``reviewed``: when each invoice's review ended."""
    wall0 = 1_800_000_000.0

    def stamp(t):
        return dt.datetime.fromtimestamp(wall0 + t).isoformat()

    invoices = [{"created_at": stamp(2.0), "file_name": f"{n}.pdf", "error": ""} for n in range(1, 5)]
    reads = [{"invoice_id": n, "status": "done", "created_at": stamp(2.0), "updated_at": stamp(10.0 * n),
              "seconds": 10.0, "pages": 1, "error": ""} for n in range(1, 5)]  # fmt: skip
    reviews = [{"invoice_id": n, "status": "done", "created_at": stamp(2.0), "updated_at": stamp(t),
                "outcome": "agreed", "error": ""} for n, t in enumerate(reviewed, start=1)]  # fmt: skip
    history = [{"t": 1.0, "kind": "page", "model": day.READER, "image": "", "wait": wait, "seconds": seconds,
                "outcome": "served"} for wait, seconds in pages]  # fmt: skip
    history += [{"t": 1.0, "kind": "suggest", "model": day.CHAT_MODEL, "image": "", "wait": 0.0, "seconds": seconds,
                 "outcome": "served"} for seconds in chats]  # fmt: skip
    processes = {"processes": {"capture": {"processes": capture}}}
    a = argparse.Namespace(n=4, seed=1, scanned=0, workers=1, deliver="folder", batch=10, slots=slots, time_scale=0.5,
                           fail_rate=0.0, reviewer="all", drill="none", drill_at=0, drill_for=0, slow_factor=1,
                           probe_every=1, llm_parallel=parallel)  # fmt: skip
    run = SimpleNamespace(
        a=a, wall0=wall0, fake_t0=0.0, cases=[], probes=[], samples=[], deliveries=[], timed_out=False, problem="",
        cpu_before={}, since=lambda: 45.0,
        cpu=SimpleNamespace(sample=lambda: None, report=lambda before, wall: processes),
        db=SimpleNamespace(invoices=lambda: invoices, page_reads=lambda: reads, reviews=lambda: reviews,
                           self_test=lambda: None),
        fake=SimpleNamespace(history=lambda: history, stats=dict, page_answers=dict),
        server=SimpleNamespace(log_counts=lambda: {"database is locked": 0, "Traceback": 0}, settings={}),
    )  # fmt: skip
    return day.Run.report(run, [])


def _full_latency(pages, slots, parallel, **kw):
    return _report(pages, slots, parallel, **kw)["at_full_latency"]


def test_the_drain_at_full_latency_stretches_the_model_s_answering_not_its_line():
    # Each page answered in 10 s at x0.5 (20 s at full latency), one at a time: the 40 s drain is 80 s at full
    # latency. On one slot, the 4 readers' waits in line are the other pages' answering, counted once already...
    assert _full_latency([(0.0, 10.0), (10.0, 20.0), (20.0, 30.0), (30.0, 40.0)], 1, 4)["drain_seconds"] == 80.0
    # ...and with one reader, the 4 slots don't read its pages any faster.
    full = _full_latency([(0.0, 10.0)] * 4, 4, 1)
    assert full["drain_seconds"] == 80.0 and full["model_seconds_per_invoice"] == 20.0


def test_the_arrival_at_full_latency_waits_for_its_chat_answers_side_by_side_at_most_the_arrival_limit():
    """Each capture process waits for its own arrival's chat answer, at most ``pipeline.ARRIVAL_SECONDS`` (20 s): 4
    answers of 6 s at x0.5 are 12 s each at full latency, 6 s more each; 15 s ones would be 30 s, cut at 20 s."""
    assert _full_latency([], 1, 1, chats=[6.0] * 4, capture=1)["arrival_seconds"] == 2.0 + 4 * 6.0
    assert _full_latency([], 1, 1, chats=[6.0] * 4, capture=2)["arrival_seconds"] == 2.0 + 4 * 6.0 / 2
    assert _full_latency([], 1, 1, chats=[15.0] * 4, capture=2)["arrival_seconds"] == 2.0 + 4 * 5.0 / 2


def test_the_report_says_whether_the_reviewer_kept_up_with_the_page_reader():
    """Reviews ended while the page reader still had invoices in line (the reviewer not starved), and each invoice's
    wait for its review once read: here the first three were reviewed while the fourth was read."""
    results = _report([(0.0, 10.0)] * 4, 2, 2, reviewed=[12.0, 22.0, 32.0, 60.0])["results"]
    assert results["reviews_while_reads_waited"] == 3
    assert results["review_wait_after_read"] == {"n": 4, "median": 2.0, "mean": 6.5, "max": 20.0}


def test_each_drill_makes_the_model_machine_misbehave_then_puts_it_back():
    with FakeModel(latency="1-2", models=[day.CHAT_MODEL, day.READER]) as fake:
        kill = day.Drill(_drill("kill"), fake)
        kill.check(5.0, 1)
        assert kill.started is None and not fake.settings()["down"]  # not before 2 pages were read
        kill.check(6.0, 2)
        assert kill.started == 6.0 and fake.settings()["down"] and not kill.over()
        kill.check(16.0, 2)
        assert kill.ended == 16.0 and not fake.settings()["down"] and kill.over()

        slow = day.Drill(_drill("slow"), fake)
        slow.check(0.0, 9)
        assert fake.settings()["page_latency"] == (4.0, 8.0) and fake.settings()["chat_latency"] == (4.0, 8.0)
        slow.check(10.0, 9)
        assert fake.settings()["page_latency"] == (1.0, 2.0)

        hang = day.Drill(_drill("hang"), fake)
        hang.check(0.0, 9)
        assert fake.settings()["hang_rate"] == 1.0
        hang.check(10.0, 9)
        assert fake.settings()["hang_rate"] == 0.0

        swap = day.Drill(_drill("swap"), fake)
        swap.check(3.0, 9)
        assert fake.settings()["models"] == [day.SWAP_CHAT, day.READER] and swap.over()
        reader = day.Drill(_drill("swap-reader"), fake)
        reader.check(4.0, 9)
        assert fake.settings()["models"] == [day.CHAT_MODEL, day.SWAP_READER] and reader.reader == day.SWAP_READER
    never = day.Drill(_drill("kill", at=99), None).report([], 0.0, [], {}, set(), {})
    assert never["started"] is None and never["failover"].startswith("not supported")


@pytest.mark.slow
def test_a_tiny_server_day_reads_every_invoice_and_reports_it(tmp_path, capsys):
    """4 invoices through ``serve --server`` (2 workers) and its background process, the model 100 times faster."""
    out = tmp_path / "day"
    # Without the OCR engine (pip install -e .[ocr]) a scan is refused on arrival, as it should be: digital ones only.
    scanned = "0.25" if layout.ocr_available() else "0"
    code = main(["day", "--n", "4", "--scanned", scanned, "--time-scale", "0.01", "--workers", "2", "--gen-workers",
                 "1", "--out", str(out), "--timeout", "8", "--self-test-minutes", "3"])  # fmt: skip
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    r = report["results"]
    # On a failure, the server's own words say why, on a CI machine nobody can log in to: printed whole (pytest shows
    # a failed test's output in full, while it shortens an assertion's message).
    logs = [out / "server.log", *sorted((out / "server-logs").glob("*.log"))]
    for path in (p for p in logs if p.is_file()):
        print(f"--- {path.name}\n{path.read_text(encoding='utf-8', errors='replace')[-6000:]}")
    assert code == 0 and not report["timed_out"] and not report["problem"], report["problem"]
    assert r["arrived"] == 4 and r["reads_open_at_end"] == 0 and r["reviews_open_at_end"] == 0
    # On a failure, say what went wrong (the failed imports, reads and reviews, with their errors), not only counts.
    assert r["reads_done"] + r["reads_skipped"] == r["reads_queued"] == 4 and r["reads_failed"] == 0, report["failures"]
    assert r["reviews_done"] >= 1 and r["failures"] == 0 and r["probes"] >= 1, report["failures"]
    assert report["self_test"]["ok"] and report["model_server"]["served"] >= 4
    assert not (out / "data").exists()  # the data folder is removed afterwards
    assert "| invoices in the review queue | 4 of 4 |" in (out / "report.md").read_text(encoding="utf-8")
    assert "invoices in the review queue in" in capsys.readouterr().out


def test_the_reader_of_the_server_s_database_lets_go_of_it(tmp_path):
    """Windows CI: the data folder stayed behind after a server day, as the bench's own read-only connection to the
    server's database was still open (Windows deletes no open file; ``ignore_errors`` said nothing)."""
    import sqlite3

    db = tmp_path / "ap_coder.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE invoices (id INTEGER)")
    conn.close()
    reader = day.Reader(db)
    assert reader.counts()["invoices"] == 0 and reader._conn is not None
    reader.close()
    assert reader._conn is None
    reader.close()  # twice: nothing to do
    assert reader.counts()["invoices"] == 0  # reads again when asked
    reader.close()
