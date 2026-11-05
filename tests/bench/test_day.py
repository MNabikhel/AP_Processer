"""``python -m ap_coder.bench day``: a morning's drop through a real company server against the stand-in model server
(ap_coder/bench/day.py), and its drills on the model machine."""

import argparse
import json

import pytest

from ap_coder.bench import day
from ap_coder.bench.__main__ import main
from ap_coder.bench.fake_model import FakeModel
from ap_coder.capture import layout


def _drill(kind, **kw):
    return argparse.Namespace(drill=kind, drill_at=kw.get("at", 2), drill_for=kw.get("length", 10.0),
                              slow_factor=kw.get("factor", 4.0))  # fmt: skip


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
    log = (out / "server.log").read_text(encoding="utf-8", errors="replace") if (out / "server.log").is_file() else ""
    # On a failure, the server's own words (its last lines) say why, on a CI machine nobody can log in to.
    assert code == 0 and not report["timed_out"] and not report["problem"], (report["problem"], log[-4000:])
    assert r["arrived"] == 4 and r["reads_open_at_end"] == 0 and r["reviews_open_at_end"] == 0
    # On a failure, say what went wrong (the failed imports, reads and reviews, with their errors), not only counts.
    assert r["reads_done"] + r["reads_skipped"] == r["reads_queued"] == 4 and r["reads_failed"] == 0, report["failures"]
    assert r["reviews_done"] >= 1 and r["failures"] == 0 and r["probes"] >= 1, report["failures"]
    assert report["self_test"]["ok"] and report["model_server"]["served"] >= 4
    assert not (out / "data").exists()  # the data folder is removed afterwards
    assert "| invoices in the review queue | 4 of 4 |" in (out / "report.md").read_text(encoding="utf-8")
    assert "invoices in the review queue in" in capsys.readouterr().out
