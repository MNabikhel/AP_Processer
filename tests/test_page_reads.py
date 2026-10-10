"""The page reader's queue (page_reads) and replacing the proposal of an invoice nobody has worked on yet."""

import copy
import datetime as dt
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from ap_coder.capture.types import Box, CaptureResult, FieldResult
from ap_coder.po import po_key
from ap_coder.store import PENDING, REVIEW, Store

from .conftest import SAMPLE_STEM, SAMPLES

PDF = SAMPLES / f"{SAMPLE_STEM}.pdf"
MODEL = "ath-maas_ovisocr2"


def _ago(hours):
    return (dt.datetime.now() - dt.timedelta(hours=hours)).isoformat(timespec="seconds")


def _set(store, invoice_id, **columns):
    """Move a queued invoice in time (when it was asked for, when its read started)."""
    with sqlite3.connect(store.path) as conn:
        sets = ", ".join(f"{column} = ?" for column in columns)
        conn.execute(f"UPDATE page_reads SET {sets} WHERE invoice_id = ?", (*columns.values(), invoice_id))


def _report(confidence, review=False):
    return {"model_confidence": confidence, "adjusted_confidence": confidence, "requires_review": review,
            "issues": []}  # fmt: skip


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "ap.db")


def _invoices(store, ground_truth, n):
    return [store.add_invoice(PDF, ground_truth, _report(0.6, True)) for _ in range(n)]


# --- The queue ---------------------------------------------------------------------------------------------------


def test_the_page_reader_takes_the_invoice_waiting_longest(store, ground_truth):
    a, b, c = _invoices(store, ground_truth, 3)
    for invoice_id, hours in ((c, 0.5), (a, 0.3), (b, 0.1)):
        assert store.queue_page_read(invoice_id, "scan")
        _set(store, invoice_id, created_at=_ago(hours))
    assert store.page_reads_waiting() == 3 and store.page_read(a)["status"] == "waiting"
    first = store.next_page_read()
    assert (first["invoice_id"], first["status"], first["reason"]) == (c, "reading", "scan")
    assert store.page_read(c)["status"] == "reading" and store.page_reads_waiting() == 2
    assert [store.next_page_read()["invoice_id"] for _ in range(2)] == [a, b]
    assert store.next_page_read() is None and store.page_reads_waiting() == 0


def test_the_invoice_ap_asked_for_is_read_first(store, ground_truth):
    a, b, c = _invoices(store, ground_truth, 3)
    store.queue_page_read(a, "scan")
    _set(store, a, created_at=_ago(1))  # waiting longer
    store.queue_page_read(b, "asked", requested_by="Ann")
    taken = store.next_page_read(b)
    assert (taken["invoice_id"], taken["status"], taken["requested_by"]) == (b, "reading", "Ann")
    assert store.page_read(a)["status"] == "waiting"  # the others keep waiting
    assert store.next_page_read(b) is None  # being read already
    assert store.next_page_read(c) is None and store.page_read(c) is None  # never put in line
    assert store.finish_page_read(b, "done", MODEL, 1, 181.5)
    assert store.next_page_read(b) is None  # read already
    _set(store, a, status="reading", updated_at=_ago(3))  # its read was interrupted long ago: in line again
    assert store.next_page_read(a)["invoice_id"] == a
    assert store.next_page_read() is None


def test_asking_again(store, ground_truth):
    a, b = _invoices(store, ground_truth, 2)
    store.queue_page_read(a, "scan")
    _set(store, a, created_at=_ago(1))
    asked_at = store.page_read(a)["created_at"]
    assert store.queue_page_read(a, "reprocessed")  # already waiting: it keeps its place in line
    assert (store.page_read(a)["created_at"], store.page_read(a)["reason"]) == (asked_at, "scan")
    assert store.queue_page_read(a, "asked", requested_by="Ann")  # a person asking for it is noted
    assert (store.page_read(a)["reason"], store.page_read(a)["requested_by"]) == ("asked", "Ann")
    assert store.queue_page_read(a, "scan")  # a background request never replaces a person's
    assert (store.page_read(a)["requested_by"], store.page_read(a)["created_at"]) == ("Ann", asked_at)
    assert store.next_page_read()["invoice_id"] == a
    assert not store.queue_page_read(a, "asked", requested_by="Bob")  # being read: left to finish
    assert (store.page_read(a)["status"], store.page_read(a)["requested_by"]) == ("reading", "Ann")
    assert store.finish_page_read(a, "failed", MODEL, 0, 12.5, error="not enough memory")
    failed = store.page_read(a)
    assert (failed["status"], failed["error"], failed["model"], failed["seconds"]) == (
        "failed", "not enough memory", MODEL, 12.5,
    )  # fmt: skip
    store.queue_page_read(b, "scan")
    _set(store, b, created_at=_ago(0.5))
    assert store.queue_page_read(a, "asked", requested_by="Bob")  # read before: to the back of the line
    again = store.page_read(a)
    assert (again["status"], again["error"], again["requested_by"]) == ("waiting", "", "Bob")
    assert again["created_at"] > asked_at
    assert store.next_page_read()["invoice_id"] == b  # b was waiting before a was asked for again


def test_finishing_a_read(store, ground_truth):
    (a,) = _invoices(store, ground_truth, 1)
    with pytest.raises(ValueError):
        store.finish_page_read(a, "reading")
    assert not store.finish_page_read(a, "done")  # never put in line
    store.queue_page_read(a, "scan")
    store.next_page_read()
    assert store.finish_page_read(a, "waiting")  # stopped part-way: it waits again
    assert store.page_read(a)["status"] == "waiting" and store.page_reads_waiting() == 1
    store.next_page_read()
    assert store.finish_page_read(a, "skipped", error="approved before it was read")
    skipped = store.page_read(a)
    assert (skipped["status"], skipped["error"]) == ("skipped", "approved before it was read")
    assert store.page_reads_waiting() == 0 and store.next_page_read() is None


def test_a_read_interrupted_long_ago_waits_again(store, ground_truth):
    a, b = _invoices(store, ground_truth, 2)
    asked_at = _ago(5)
    store.queue_page_read(a, "scan")
    _set(store, a, created_at=asked_at)
    store.queue_page_read(b, "scan")
    _set(store, b, created_at=_ago(4))
    assert store.next_page_read()["invoice_id"] == a
    _set(store, a, updated_at=_ago(1.5))  # within the time a long read may take
    assert store.page_read(a)["status"] == "reading" and store.page_reads_waiting() == 1
    assert not store.queue_page_read(a, "asked", requested_by="Ann")
    _set(store, a, updated_at=_ago(2.5))  # the computer slept, or the app was closed
    assert store.page_read(a)["status"] == "waiting" and store.page_reads_waiting() == 2
    assert store.next_page_read()["invoice_id"] == a  # it kept its place in line
    assert store.page_read(a)["status"] == "reading"
    _set(store, a, updated_at=_ago(3))
    assert store.queue_page_read(a, "asked", requested_by="Ann")  # asking again puts it back, in its place
    assert (store.page_read(a)["status"], store.page_read(a)["created_at"]) == ("waiting", asked_at)
    assert store.next_page_read()["invoice_id"] == a


def test_two_readers_never_take_the_same_invoice(store, ground_truth):
    ids = _invoices(store, ground_truth, 24)
    for invoice_id in ids:
        store.queue_page_read(invoice_id, "scan")

    def drain():
        taken = []
        while (row := store.next_page_read()) is not None:
            taken.append(row["invoice_id"])
        return taken

    with ThreadPoolExecutor(4) as pool:
        batches = [f.result() for f in [pool.submit(drain) for _ in range(4)]]
    taken = [i for batch in batches for i in batch]
    assert sorted(taken) == sorted(ids)  # each one exactly once


# --- Replacing the proposal --------------------------------------------------------------------------------------


def test_replace_proposal_updates_an_invoice_nobody_has_worked_on(store, ground_truth):
    invoice_id = store.add_invoice(PDF, ground_truth, _report(0.62, True),
                                   meta={"source": "scan.jpg", "capture": {"layout": "ocr"}})  # fmt: skip
    store.save_capture(invoice_id, {"fields": {}, "layout_source": "ocr"})
    better = copy.deepcopy(ground_truth) | {"invoice_number": "NW-2026-0913", "grand_total": 18018.85,
                                            "currency": "USD", "po_number": "PO-1"}  # fmt: skip
    number = FieldResult("invoice_number", "NW-2026-0913", 0.99, "verified", [Box(1, 0.6, 0.1, 0.8, 0.12)],
                         {"rules": "NW-2026-0913", "vlm": "NW-2026-0913"})  # fmt: skip
    capture = CaptureResult({"invoice_number": number}, layout_source="ocr", page_count=1)
    meta = {"page_reader": {"model": MODEL, "pages": 1}, "capture": {"layout": "ocr", "status_counts": {"verified": 1}}}
    report = SimpleNamespace(to_dict=lambda: _report(0.97))  # a ValidationReport, or its dict
    assert store.replace_proposal(invoice_id, better, report, capture, meta)
    inv = store.get_invoice(invoice_id)
    assert inv["status"] == REVIEW and inv["ai_output"] == better and inv["validation"] == _report(0.97)
    assert (inv["model_confidence"], inv["adjusted_confidence"], inv["requires_review"]) == (0.97, 0.97, 0)
    assert (inv["invoice_number"], inv["grand_total"], inv["currency"]) == ("NW-2026-0913", 18018.85, "USD")
    assert (inv["po_key"], inv["due_date"]) == (po_key("PO-1"), "2026-10-14")
    assert inv["vendor_name"] == ground_truth["vendor_name"]
    assert inv["meta"]["source"] == "scan.jpg" and inv["meta"]["page_reader"] == {"model": MODEL, "pages": 1}
    assert inv["meta"]["capture"]["status_counts"] == {"verified": 1}  # merged: new notes replace old ones
    assert store.get_capture(invoice_id) == capture.to_dict()
    event = store.events(invoice_id, actions=["proposal_updated"])[0]
    assert (event["actor"], event["detail"]["by"], event["detail"]["confidence"]) == ("AP Coder", "page reader", 0.97)
    assert {"what": "invoice #", "before": "NW-2026-0912", "after": "NW-2026-0913"} in event["detail"]["changes"]
    assert store.list_invoices(REVIEW)[0]["adjusted_confidence"] == 0.97  # the queue shows the new figure
    # The capture can stay as it is (None), and the report be a dict.
    assert store.replace_proposal(invoice_id, ground_truth, _report(0.9, True), None)
    assert store.get_capture(invoice_id) == capture.to_dict() and store.get_invoice(invoice_id)["requires_review"]


@pytest.mark.parametrize("state", ["approved", "pending", "reopened", "edited", "parked", "rejected", "deleted"])
def test_replace_proposal_leaves_an_invoice_someone_worked_on_alone(store, ground_truth, state):
    invoice_id = store.add_invoice(PDF, ground_truth, _report(0.6, True))
    store.save_capture(invoice_id, {"fields": {}, "layout_source": "ocr"})
    if state == "pending":
        store.set_setting("approval_limit", "100")
    if state in ("approved", "pending", "reopened"):
        store.approve_invoice(invoice_id, ground_truth, "Ann")
        assert store.get_invoice(invoice_id)["status"] == (PENDING if state == "pending" else "approved")
    if state == "reopened":  # back in review, but the reviewer's coding is the starting point
        store.reopen(invoice_id, "Ann", "check the total")
    if state == "edited":
        with sqlite3.connect(store.path) as conn:
            conn.execute("UPDATE invoices SET edits = ? WHERE id = ?", ('["grand_total"]', invoice_id))
    if state == "parked":
        store.park_invoice(invoice_id, "Ann", "waiting for the buyer")
    if state == "rejected":
        store.reject_invoice(invoice_id, "Ann", "not ours")
    if state == "deleted":
        store.delete_invoice(invoice_id)
    before = (store.get_invoice(invoice_id), store.get_capture(invoice_id), len(store.events()))
    changed = copy.deepcopy(ground_truth) | {"grand_total": 1.0}
    assert not store.replace_proposal(invoice_id, changed, _report(0.99), {"fields": {}, "layout_source": "vlm"},
                                      {"page_reader": {"model": MODEL}})  # fmt: skip
    assert (store.get_invoice(invoice_id), store.get_capture(invoice_id), len(store.events())) == before


def test_replace_proposal_needs_a_proposal(store, ground_truth):
    (invoice_id,) = _invoices(store, ground_truth, 1)
    assert not store.replace_proposal(invoice_id, {}, _report(0.9), None)
    assert store.get_invoice(invoice_id)["ai_output"] == ground_truth
