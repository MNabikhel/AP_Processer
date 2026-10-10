"""Each capture reader scored against what AP approved: the reader_outcomes table, the scorecards, the evidence
counts behind the local calibration, and the rows every approval records (review screen and bulk approval)."""

import copy
import sqlite3

from ap_coder.capture.types import Box, CaptureResult, FieldResult
from ap_coder.capture.workflow import learn_from_approval, reader_outcome_rows
from ap_coder.store import PENDING, REVIEW, SCHEMA_VERSION, Store

from .conftest import SAMPLE_STEM, SAMPLES

PDF = SAMPLES / f"{SAMPLE_STEM}.pdf"
BOX = [Box(1, 0.6, 0.2, 0.8, 0.22)]
SPEC_COLUMNS = {
    "reader_outcomes": {"id", "invoice_id", "reader", "field", "read_value", "final_value", "correct", "evidence",
                        "layout_source", "at"},
    "page_reads": {"invoice_id", "status", "model", "pages", "seconds", "reason", "error", "requested_by",
                   "created_at", "updated_at"},
}  # fmt: skip


def _columns(path, table):
    with sqlite3.connect(path) as conn:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def _tables(path):
    with sqlite3.connect(path) as conn:
        return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'index')")}


def _capture(layout_source="ocr"):
    """The sample invoice as the readers might have read a scan of it."""
    fields = {
        "vendor_name": FieldResult(
            "vendor_name", "Northwind IT Solutions Inc.", 0.99, "verified", BOX,
            {"rules": "Northwind IT Solutions Inc.", "vlm": "NORTHWIND IT SOLUTIONS INC"},
            evidence="vendor_name|rules+vlm|top-of-page|scan|",
        ),
        # The page reader read another number, which lost: it is scored on its own value.
        "invoice_number": FieldResult(
            "invoice_number", "NW-2026-0912", 0.99, "verified", BOX,
            {"ocr2": "NW 2026 0912", "rules": "NW-2026-0912", "other:NW-2026-0812": "vlm"},
            evidence="invoice_number|ocr2+rules|label-right|scan|",
        ),
        # Printed 09/14/2026; the readers' value is the date, whatever the raw text looks like.
        "invoice_date": FieldResult(
            "invoice_date", "2026-09-14", 0.97, "likely", BOX, {"rules": "09/14/2026", "ocr2": "Sept 14, 2026"},
            evidence="invoice_date|ocr2+rules|label-right|scan|",
        ),
        # The readers agreed on a total AP corrected; the template had it right but lost.
        "grand_total": FieldResult(
            "grand_total", 18071.85, 0.9, "check", BOX,
            {"rules": "$18,071.85", "vlm": "18071.85", "other:18017.85": "ocr2, template"},
            evidence="grand_total|rules+vlm|label-right|scan|contested",
        ),
        "hst_amount": FieldResult(
            "hst_amount", 2072.85, 1.0, "verified", BOX, {"rules": "$2,072.85", "vlm": "2 072,85"},
            evidence="hst_amount|rules+vlm|label-right|scan|adds-up,confirmed",
        ),
        "tax_total": FieldResult("tax_total", 2072.85, 1.0, "verified", [], {"computed": "sum of the taxes"}),
        "qst_registration_number": FieldResult("qst_registration_number", "1234567890TQ0001", 0.7, "check", BOX,
                                               {"vlm": "1234567890 TQ 0001"}, evidence="qst|vlm|label-right|scan|"),
        "po_number": FieldResult("po_number", None, 0.0, "missing", [], {}),
    }  # fmt: skip
    return CaptureResult(fields, layout_source=layout_source, page_count=1)


def _by(rows):
    return {(r["reader"], r["field"]): r for r in rows}


def test_fresh_database_has_the_reader_tables(tmp_path):
    store = Store(tmp_path / "fresh.db")
    assert SCHEMA_VERSION == 15 and store.get_setting("schema_version") == "15"
    for table, columns in SPEC_COLUMNS.items():
        assert columns <= _columns(store.path, table)
    assert {"reader_outcomes_invoice", "reader_outcomes_reader", "page_reads_status"} <= _tables(store.path)
    assert store.reader_scorecard() == [] and store.evidence_counts() == {} and store.page_reads_waiting() == 0


def test_migration_from_schema_14(tmp_path, ground_truth):
    path = tmp_path / "old.db"
    store = Store(path)
    invoice_id = store.add_invoice(PDF, ground_truth, {"requires_review": False})
    with sqlite3.connect(path) as conn:  # what a version-14 database looks like
        for table in ("reader_outcomes", "page_reads"):
            conn.execute(f"DROP TABLE {table}")
        conn.execute("UPDATE settings SET value = '14' WHERE key = 'schema_version'")
    assert not {"reader_outcomes", "page_reads"} & _tables(path)
    upgraded = Store(path)
    assert upgraded.get_setting("schema_version") == str(SCHEMA_VERSION)
    assert {"reader_outcomes", "page_reads", "reader_outcomes_invoice", "page_reads_status"} <= _tables(path)
    assert upgraded.get_invoice(invoice_id)["vendor_name"] == ground_truth["vendor_name"]  # data kept
    upgraded.record_reader_outcomes(invoice_id, [{"reader": "rules", "field": "grand_total", "correct": True}])
    assert upgraded.queue_page_read(invoice_id, "scan") and upgraded.page_reads_waiting() == 1


def test_scorecards_and_evidence_counts(tmp_path, ground_truth):
    store = Store(tmp_path / "s.db")
    a, b = (store.add_invoice(PDF, ground_truth, {"requires_review": False}) for _ in range(2))
    fused = {"reader": "fused", "evidence": "grand_total|rules|label-right|scan|", "layout_source": "ocr"}
    store.record_reader_outcomes(a, [
        {"reader": "rules", "field": "grand_total", "read_value": "$1.00", "final_value": 1.0, "correct": True},
        {"reader": "vlm", "field": "grand_total", "read_value": "7.00", "final_value": 1.0, "correct": False},
        {**fused, "field": "grand_total", "read_value": 1.0, "final_value": 1.0, "correct": True, "status": "verified"},
        {"reader": "", "field": "grand_total", "correct": True},  # incomplete rows are dropped
    ])  # fmt: skip
    # Recording an invoice again replaces its rows (approved again after a reopen).
    rows_b = [
        {"reader": "rules", "field": "invoice_number", "read_value": "A-1", "final_value": "A-1", "correct": True},
        {"reader": "vlm", "field": "invoice_number", "read_value": "A-7", "final_value": "A-1", "correct": False},
    ]
    store.record_reader_outcomes(b, [*rows_b, {**rows_b[0], "reader": "ocr2"}])
    assert store.record_reader_outcomes(b, rows_b + [{**fused, "field": "invoice_number", "evidence": "",
                                                      "correct": False, "status": "likely"}]) == 3  # fmt: skip
    card = {r["reader"]: r for r in store.reader_scorecard()}
    assert set(card) == {"fused", "rules", "vlm"}  # the ocr2 row was replaced
    assert card["rules"] == {"reader": "rules", "fields": 2, "agreed": 2, "rate": 1.0, "invoices": 2}
    assert card["vlm"]["agreed"] == 0 and card["vlm"]["rate"] == 0.0
    fields = store.reader_field_scorecard("vlm")
    assert [f["field"] for f in fields] == ["grand_total", "invoice_number"]
    assert (fields[1]["last_read"], fields[1]["last_final"]) == ("A-7", "A-1")
    assert store.reader_field_scorecard("rules")[0]["last_read"] is None  # never disagreed
    assert store.reader_field_scorecard("nobody") == []
    # Only the values AP saw count for the calibration, and only with an evidence key.
    assert store.evidence_counts() == {"grand_total|rules|label-right|scan|": (1, 1)}
    statuses = store.fused_status_scorecard()
    assert [s["status"] for s in statuses] == ["verified", "likely"] and statuses[1]["rate"] == 0.0


def test_reader_rows_go_with_the_approval_and_the_invoice(tmp_path, ground_truth):
    store = Store(tmp_path / "s.db")
    invoice_id = store.add_invoice(PDF, ground_truth, {"requires_review": False})
    row = [{"reader": "rules", "field": "grand_total", "correct": True}]

    def recorded():
        return sum(r["fields"] for r in store.reader_scorecard())

    store.approve_invoice(invoice_id, ground_truth, "Ann")
    store.record_reader_outcomes(invoice_id, row)
    store.reopen(invoice_id, "Ann", "wrong total")  # what the approval taught is withdrawn
    assert recorded() == 0
    store.record_reader_outcomes(invoice_id, row)
    store.reject_invoice(invoice_id, "Ann", "not ours")
    assert recorded() == 0
    store.set_setting("approval_limit", "100")  # over the limit: waits for a second approver
    other = store.add_invoice(PDF, ground_truth, {"requires_review": False})
    store.approve_invoice(other, ground_truth, "Ann")
    assert store.get_invoice(other)["status"] == PENDING
    store.record_reader_outcomes(other, row)
    store.send_back(other, "Bob", "check the PO")
    assert recorded() == 0
    store.record_reader_outcomes(other, row)
    store.queue_page_read(other, "scan")
    store.delete_invoice(other)
    assert recorded() == 0 and store.page_read(other) is None and store.page_reads_waiting() == 0


def test_reader_outcome_rows_scores_every_reader(ground_truth):
    final = copy.deepcopy(ground_truth)
    final["qst_registration_number"] = ""  # AP left it blank: not scored (not printed, or not needed)
    rows = reader_outcome_rows(_capture().to_dict(), final)
    by = _by(rows)
    assert by[("rules", "invoice_number")]["correct"] and by[("ocr2", "invoice_number")]["correct"]
    assert by[("ocr2", "invoice_number")]["read_value"] == "NW 2026 0912"  # what it read, as printed
    vlm = by[("vlm", "invoice_number")]  # the value it read lost to the others: scored on its own value
    assert (vlm["read_value"], vlm["final_value"], vlm["correct"]) == ("NW-2026-0812", "NW-2026-0912", False)
    assert by[("rules", "invoice_date")]["correct"]  # 09/14/2026: the readers' value, not the raw text
    assert by[("vlm", "vendor_name")]["correct"]  # the same supplier, printed in capitals
    assert not by[("rules", "grand_total")]["correct"] and not by[("vlm", "grand_total")]["correct"]
    assert by[("ocr2", "grand_total")]["correct"] and by[("template", "grand_total")]["correct"]
    assert by[("vlm", "hst_amount")]["correct"]  # 2 072,85 read the French way
    fused = by[("fused", "grand_total")]
    assert (fused["read_value"], fused["correct"], fused["status"]) == (18071.85, False, "check")
    assert fused["evidence"] == "grand_total|rules+vlm|label-right|scan|contested" and fused["layout_source"] == "ocr"
    # The tax total was worked out, not read: only the value AP saw is scored.
    assert [r["reader"] for r in rows if r["field"] == "tax_total"] == ["fused"]
    assert not any(r["field"] in ("qst_registration_number", "po_number") for r in rows)
    assert all(r["evidence"] for r in rows if r["reader"] == "fused" and r["field"] != "tax_total")
    assert len({(r["reader"], r["field"]) for r in rows}) == len(rows)  # one row per reader and field
    assert reader_outcome_rows(None, final) == [] and reader_outcome_rows({}, final) == []


def test_a_name_from_the_vendor_master_scores_the_readers_on_the_printed_name():
    capture = CaptureResult({
        "vendor_name": FieldResult(
            "vendor_name", "Northwind IT Solutions Incorporated", 0.99, "verified", BOX,
            {"vendor master": "Northwind IT Solutions Incorporated", "rules": "NORTHWIND IT SOLUTIONS INC.",
             "other:Northwind Freight": "vlm"},
        ),
    }, layout_source="ocr")  # fmt: skip
    by = _by(reader_outcome_rows(capture, {"vendor_name": "Northwind IT Solutions Inc."}))
    assert set(by) == {("rules", "vendor_name"), ("vlm", "vendor_name"), ("fused", "vendor_name")}
    assert by[("rules", "vendor_name")]["correct"] and by[("fused", "vendor_name")]["correct"]
    assert not by[("vlm", "vendor_name")]["correct"]


def test_learn_from_approval_records_the_readers(tmp_path, ground_truth, monkeypatch):
    store = Store(tmp_path / "s.db")
    invoice_id = store.add_invoice(PDF, ground_truth, {"requires_review": False})
    store.save_capture(invoice_id, _capture().to_dict())
    store.approve_invoice(invoice_id, ground_truth, "Ann")
    learn_from_approval(store, invoice_id, ground_truth, actor="Ann")
    card = {r["reader"]: r for r in store.reader_scorecard()}
    assert {"fused", "rules", "ocr2", "vlm", "template"} == set(card)
    assert card["fused"]["fields"] == 6 and card["fused"]["agreed"] == 5  # all but the total
    assert card["template"] == {"reader": "template", "fields": 1, "agreed": 1, "rate": 1.0, "invoices": 1}
    counts = store.evidence_counts()
    assert counts["grand_total|rules+vlm|label-right|scan|contested"] == (1, 0)
    assert counts["invoice_number|ocr2+rules|label-right|scan|"] == (1, 1)
    supplier = store.supplier_stats(store.supplier_key_for(ground_truth["vendor_name"], "123456782RT0001"))
    assert supplier.invoices == 1  # the supplier learning still happens

    # Learning never blocks an approval: a failure is logged, and the rest still learns.
    other = store.add_invoice(PDF, ground_truth, {"requires_review": False})
    store.save_capture(other, _capture().to_dict())
    store.approve_invoice(other, ground_truth, "Ann")

    def broken(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(store, "record_reader_outcomes", broken)
    learn_from_approval(store, other, ground_truth, actor="Ann")
    assert supplier.invoices == 1 and store.supplier_stats(supplier.key).invoices == 2

    # An invoice without a capture (a text file) records nothing.
    monkeypatch.undo()
    text_only = store.add_invoice(SAMPLES / f"{SAMPLE_STEM}.md", ground_truth, {"requires_review": False})
    store.approve_invoice(text_only, ground_truth, "Ann")
    learn_from_approval(store, text_only, ground_truth, actor="Ann")
    assert {r["invoices"] for r in store.reader_scorecard()} == {1}


def test_bulk_approval_scores_the_readers_too(tmp_path):
    from ap_coder.bulk import bulk_approve, clean_candidates
    from ap_coder.config import Settings
    from ap_coder.demo import load_demo

    store = Store(tmp_path / "ap.db")
    load_demo(store)  # the demo's pre-approved invoices record no reader rows: nobody approved them here
    assert store.reader_scorecard() == []
    candidates = [c["id"] for c in clean_candidates(store)]
    result = bulk_approve(store, store.reference_data(), Settings(), candidates, "jane")
    assert result["approved"]
    card = {r["reader"]: r for r in store.reader_scorecard()}
    assert card["fused"]["invoices"] == len(result["approved"]) and card["rules"]["fields"]
    assert all(store.get_invoice(i)["status"] != REVIEW for i in result["approved"])
