"""Learning memory and the local dashboard database."""

import copy
import json

from ap_coder.inference import InvoiceCoder
from ap_coder.memory import (
    ACCEPTED,
    CORRECTED,
    compare_with_history,
    format_examples,
    select_examples,
    vendor_key,
)
from ap_coder.pipeline import InvoicePipeline, finalise_coding
from ap_coder.schema import InvoiceCoding
from ap_coder.store import APPROVED, REVIEW, Store

from .conftest import DATA, FakeOpenAI, make_completion


def _row(vendor, desc, gl, outcome=ACCEPTED, cc="CC400", suggested=None):
    return {
        "vendor_key": vendor_key(vendor),
        "vendor_name": vendor,
        "description": desc,
        "final_gl": gl,
        "final_cc": cc,
        "suggested_gl": suggested or gl,
        "outcome": outcome,
        "created_at": "2026-10-01T10:00:00",
    }


# --- Memory -----------------------------------------------------------------------------------


def test_vendor_key_ignores_legal_suffixes_and_punctuation():
    assert (
        vendor_key("Northwind IT Solutions Inc.")
        == vendor_key("NORTHWIND IT SOLUTIONS, INC")
        == "northwind it solutions"
    )
    assert vendor_key("Services Informatiques Laurentides Ltée") == "services informatiques laurentides"


def test_select_examples_prefers_same_vendor_then_similar_lines():
    rows = [
        _row("Northwind IT Solutions Inc.", "Dell Latitude laptop", "6010"),
        _row("Other Vendor Ltd", "Microsoft 365 E3 licences", "6020"),
        _row("Unrelated Co", "Catering lunch", "6320"),
    ]
    doc = "Northwind IT Solutions Inc.\n| Dell Latitude 7450 laptop | Microsoft 365 E3 licences - monthly |"
    examples = select_examples(rows, doc)
    assert [e.description for e in examples] == ["Dell Latitude laptop", "Microsoft 365 E3 licences"]
    text = format_examples(examples)
    assert "Approved coding history" in text and "| Northwind IT Solutions Inc. | Dell Latitude laptop | 6010 |" in text


def test_corrections_rank_first_and_are_labelled():
    rows = [
        _row("Northwind IT Solutions Inc.", "Delivery & handling", "6800"),
        _row("Northwind IT Solutions Inc.", "Delivery & handling", "6800"),
        _row("Northwind IT Solutions Inc.", "Server", "1500", outcome=CORRECTED, suggested="6010"),
    ]
    examples = select_examples(rows, "Northwind IT Solutions Inc. invoice")
    assert examples[0].gl_code == "1500" and examples[0].corrections == 1
    assert examples[1].approvals == 2


def test_compare_with_history_flags_conflicts_and_reinforces_matches(ground_truth):
    rows = [
        _row("Northwind IT Solutions Inc.", "Dell Latitude 7450 laptop, 32GB RAM", "6010"),
        _row("Northwind IT Solutions Inc.", "Dell Latitude 7450 laptop 32GB", "6010"),
        _row("Northwind IT Solutions Inc.", "Microsoft 365 E3 licences - monthly", "6020", outcome=CORRECTED),
    ]
    coding = copy.deepcopy(ground_truth)
    coding["line_items"][2]["predicted_gl_code"] = "6030"  # disagrees with the reviewer's correction
    result = {h["line_number"]: h for h in compare_with_history(InvoiceCoding.model_validate(coding), rows)}
    assert result[1]["status"] == "match" and result[1]["decisions"] == 2
    assert result[3]["status"] == "conflict" and result[3]["history_gl"] == "6020"
    assert 2 not in result, "no history for the server line"


def test_single_unconfirmed_approval_is_not_a_pattern(ground_truth):
    rows = [_row("Northwind IT Solutions Inc.", "Dell Latitude 7450 laptop, 32GB RAM", "6000")]
    assert compare_with_history(InvoiceCoding.model_validate(ground_truth), rows) == []


# --- Store ---------------------------------------------------------------------------------------


def _store(tmp_path):
    store = Store(tmp_path / "ap.db")
    rows = [
        {"Account": "6010", "Account Description": "IT hardware", "Group": "Opex"},
        {"Account": 6020.0, "Account Description": "Software", "Group": "Opex"},
        {"Account": "2310", "Account Description": "GST/HST recoverable", "Group": "Tax"},
        {"Account": "", "Account Description": "blank row", "Group": ""},
    ]
    assert store.import_accounts("gl_accounts", rows, "Account", "Account Description", "Group") == {
        "added": 3,
        "updated": 0,
        "skipped": 1,
    }
    store.set_tax_treatment("HST", "recoverable", "2310")
    return store


def test_import_view_delete_accounts(tmp_path):
    store = _store(tmp_path)
    assert [a["code"] for a in store.list_accounts("gl_accounts")] == ["2310", "6010", "6020"]
    assert store.list_accounts("gl_accounts")[2] == {"code": "6020", "description": "Software", "category": "Opex"}
    assert store.delete_accounts("gl_accounts", ["6020"]) == 1
    assert [a["code"] for a in store.list_accounts("gl_accounts")] == ["2310", "6010"]
    again = store.import_accounts("gl_accounts", [{"c": "6010", "d": "Hardware (renamed)"}], "c", "d")
    assert again["updated"] == 1


def test_reference_data_from_store(tmp_path):
    store = _store(tmp_path)
    store.set_setting("policy_notes", "# comment\n- Laptops under 2500 go to 6010")
    ref = store.reference_data()
    assert ref.chart_of_accounts.rows[0] == {"gl_code": "2310", "description": "GST/HST recoverable", "category": "Tax"}
    assert ref.cost_centers is None
    assert ref.tax.treatment("HST").gl_code == "2310"
    assert ref.notes == ["Laptops under 2500 go to 6010"]


def test_approve_records_accepted_and_corrected_lines(tmp_path, ground_truth):
    store = Store(tmp_path / "ap.db")
    inv_id = store.add_invoice(tmp_path / "x.pdf", ground_truth, {"requires_review": True})
    final = copy.deepcopy(ground_truth)
    final["line_items"][1]["predicted_gl_code"] = "6010"  # reviewer corrects the server line
    final["line_items"].append({**final["line_items"][0], "line_number": 6, "description": "Eco fee"})

    counts = store.approve_invoice(inv_id, final, reviewer="ap.clerk")
    assert counts == {ACCEPTED: 4, CORRECTED: 2}  # corrected line + added line
    inv = store.get_invoice(inv_id)
    assert inv["status"] == APPROVED and "line_coding" in inv["edits"] and "line_count" in inv["edits"]
    rows = store.feedback_rows()
    corrected = [r for r in rows if r["outcome"] == CORRECTED]
    assert {(r["suggested_gl"], r["final_gl"]) for r in corrected} == {("1500", "6010"), (None, "6010")}

    m = store.metrics()
    assert m["lines_reviewed"] == 6 and m["line_accuracy"] == round(4 / 6, 4)
    assert m["top_corrections"][0]["final_gl"] == "6010"


def test_duplicate_invoice_detection(tmp_path, ground_truth):
    store = Store(tmp_path / "ap.db")
    first = store.add_invoice(tmp_path / "a.pdf", ground_truth, {"requires_review": False})
    dupe = dict(ground_truth, vendor_name="NORTHWIND IT SOLUTIONS INC", invoice_number="NW 2026-0912")
    assert store.find_duplicates(dupe["vendor_name"], dupe["invoice_number"]) == [first]
    assert store.find_duplicates(dupe["vendor_name"], "NW-2026-0999") == []


def test_learning_applies_to_the_next_invoice(tmp_path, settings, reference, ground_truth, sample_markdown_path):
    """A reviewer correction is in the very next prompt, and a repeat of the old answer is flagged."""
    store = Store(tmp_path / "ap.db")
    body = json.dumps(ground_truth)
    client = FakeOpenAI(make_completion(body), make_completion(body))
    pipe = InvoicePipeline(settings, reference, coder=InvoiceCoder(settings, reference, client=client), store=store)

    first = pipe.process(sample_markdown_path)
    assert first.invoice_id and store.get_invoice(first.invoice_id)["status"] == REVIEW
    final = copy.deepcopy(ground_truth)
    final["line_items"][2]["predicted_gl_code"] = "6030"  # reviewer: M365 goes to cloud hosting here
    store.approve_invoice(first.invoice_id, final, reviewer="ap.clerk")

    second = pipe.process(sample_markdown_path)
    user_message = client.calls[1]["messages"][1]["content"]
    assert "Approved coding history from your AP team" in user_message
    assert "| Microsoft 365 E3 licences - monthly | 6030 | CC400 | 0 | 1 |" in user_message
    codes = {i.code for i in second.report.issues}
    assert "HISTORY_CONFLICT" in codes  # the AI repeated 6020 despite the correction
    assert "DUPLICATE_INVOICE" in codes  # same vendor + invoice number as the first one
    assert second.history_examples == 5


def test_finalise_coding_recomputes_distribution_after_edits(tmp_path, settings, reference, ground_truth):
    edited = copy.deepcopy(ground_truth)
    edited["line_items"][0]["predicted_gl_code"] = "6000"
    output, report = finalise_coding(InvoiceCoding.model_validate(edited), reference, settings)
    assert output["gl_distribution"][0]["gl_code"] == "6000"
    assert report.issues == []


def test_sample_reference_files_are_importable(tmp_path):
    import csv

    store = Store(tmp_path / "ap.db")
    with (DATA / "chart_of_accounts.csv").open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    result = store.import_accounts("gl_accounts", rows, "gl_code", "description", "category")
    assert result["added"] == len(rows) and result["skipped"] == 0


def test_share_report_from_dashboard_database_is_redacted(tmp_path, ground_truth):
    from ap_coder.share_report import build_share_report

    store = Store(tmp_path / "ap.db")
    for _ in range(2):  # the same file twice must still get two labels
        store.add_invoice(tmp_path / "northwind.pdf", ground_truth, {"requires_review": False, "issues": [],
                          "model_confidence": 0.9, "adjusted_confidence": 0.9, "review_threshold": 0.85})  # fmt: skip
    final = copy.deepcopy(ground_truth)
    final["line_items"][0]["predicted_gl_code"] = "6000"
    store.approve_invoice(1, final, reviewer="ap.clerk")

    text = build_share_report(tmp_path / "no-output", db_path=tmp_path / "ap.db")
    assert "| doc-01 |" in text and "| doc-02 |" in text
    assert "AI coding accepted as-is: 80.0%" in text and "corrections taught: 1" in text
    for secret in ("Northwind", "northwind", "NW-2026", "18017", "Latitude", "6010", "6000"):
        assert secret not in text, secret
    assert "6010 -> 6000 x1" in build_share_report(tmp_path / "x", include_codes=True, db_path=tmp_path / "ap.db")
