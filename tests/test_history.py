"""Teaching the AI from the ERP's past AP coding."""

from ap_coder.ai import history
from ap_coder.core.memory import compare_with_history
from ap_coder.core.schema import InvoiceCoding
from ap_coder.core.store import Store


def _rows():
    records = [
        {"Supplier": "Acme Ltd.", "Memo": "Janitorial cleaning, head office", "Account": "6230.0", "Dept": "CC700",
         "Net Amount": "1,200.00", "Posting Date": "2025-11-30 00:00:00"},
        {"Supplier": "Acme Ltd.", "Memo": "Janitorial cleaning, head office", "Account": "6230", "Dept": "CC700",
         "Net Amount": "1,200.00", "Posting Date": "2025-12-31"},
        {"Supplier": "", "Memo": "orphan", "Account": "6000", "Dept": "", "Net Amount": "1", "Posting Date": ""},
    ]  # fmt: skip
    cols = history.map_columns(list(records[0]))
    assert cols == {"vendor_name": "Supplier", "description": "Memo", "gl_code": "Account", "cost_center": "Dept",
                    "amount": "Net Amount", "date": "Posting Date"}  # fmt: skip
    return history.rows_from_records(records, cols)


def test_history_is_memory_but_not_accuracy(tmp_path, ground_truth):
    store = Store(tmp_path / "a.db")
    rows, skipped = _rows()
    assert skipped == 1 and rows[0]["gl_code"] == "6230" and rows[0]["date"] == "2025-11-30"
    assert store.import_history(rows) == {"added": 2, "skipped": 0, "unknown_gl": 0}
    assert store.import_history(rows) == {"added": 0, "skipped": 2, "unknown_gl": 0}  # importing again: nothing
    assert store.history_count() == 2
    m = store.metrics()
    assert m["lines_reviewed"] == 0 and m["line_accuracy"] is None  # not reviewer decisions

    doc = {**ground_truth, "vendor_name": "ACME Ltd"}
    doc["line_items"] = [{**ground_truth["line_items"][0], "description": "Janitorial cleaning, head office - October",
                          "predicted_gl_code": "6000"}]  # fmt: skip
    found = compare_with_history(InvoiceCoding.model_validate(doc), store.feedback_rows(vendor_name="Acme"))
    assert found and found[0]["status"] == "conflict" and found[0]["history_gl"] == "6230"

    assert store.forget_history() == 2 and store.history_count() == 0


def test_history_dates_are_read_not_cut_at_ten_characters(tmp_path):
    """A date written "1/25/2026 0:00" is 2026-01-25, so the same line from an export with ISO dates is a
    duplicate; a date that can't be read (or could be read either way) keeps its date part, never "1/5/2026 0"."""
    cols = {"vendor_name": "V", "description": "D", "gl_code": "G", "date": "When"}
    line = {"V": "Acme", "D": "Paper", "G": "6000"}
    rows, _ = history.rows_from_records(
        [{**line, "When": "1/25/2026 0:00"}, {**line, "When": "2026-01-25T00:00:00"}, {**line, "When": "soon"}], cols
    )
    assert [r["date"] for r in rows] == ["2026-01-25", "2026-01-25", "soon"]
    store = Store(tmp_path / "a.db")
    assert store.import_history(rows) == {"added": 2, "skipped": 1, "unknown_gl": 0}
    rows, _ = history.rows_from_records([{**line, "When": "1/5/2026 0:00"}], cols)
    assert rows[0]["date"] == "1/5/2026"
