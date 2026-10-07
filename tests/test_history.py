"""Teaching the AI from the ERP's past AP coding."""

from ap_coder import history
from ap_coder.memory import compare_with_history
from ap_coder.schema import InvoiceCoding
from ap_coder.store import Store


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
    assert store.import_history(rows) == {"added": 2, "skipped": 0}
    assert store.import_history(rows) == {"added": 0, "skipped": 2}  # importing again adds nothing
    assert store.history_count() == 2
    m = store.metrics()
    assert m["lines_reviewed"] == 0 and m["line_accuracy"] is None  # not reviewer decisions

    doc = {**ground_truth, "vendor_name": "ACME Ltd"}
    doc["line_items"] = [{**ground_truth["line_items"][0], "description": "Janitorial cleaning, head office - October",
                          "predicted_gl_code": "6000"}]  # fmt: skip
    found = compare_with_history(InvoiceCoding.model_validate(doc), store.feedback_rows(vendor_name="Acme"))
    assert found and found[0]["status"] == "conflict" and found[0]["history_gl"] == "6230"

    assert store.forget_history() == 2 and store.history_count() == 0
