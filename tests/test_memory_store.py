"""Learning memory and the local dashboard database."""

import copy
import json

from ap_coder.ai.inference import InvoiceCoder
from ap_coder.ai.pipeline import InvoicePipeline, finalise_coding
from ap_coder.core.memory import (
    ACCEPTED,
    CODED,
    CORRECTED,
    compare_with_history,
    format_examples,
    select_examples,
    vendor_key,
)
from ap_coder.core.schema import InvoiceCoding
from ap_coder.core.store import APPROVED, REVIEW, Store

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


def test_vendor_key_ignores_french_legal_forms():
    assert vendor_key("Les Entreprises ABC Limitée") == vendor_key("Les Entreprises ABC") == "les entreprises abc"
    assert vendor_key("Construction XYZ Incorporée") == "construction xyz"
    assert vendor_key("Énergir, s.e.c.") == vendor_key("Energir S.E.C.") == "energir"


def test_vendor_keys_stored_by_an_older_version_are_recomputed(tmp_path):
    """Keys stored before a change to vendor_key (here: French legal forms) still match once the database opens."""
    import sqlite3

    path = tmp_path / "t.db"
    Store(path)
    old, now = "energir sec", "2026-10-01T10:00:00"
    conn = sqlite3.connect(path)
    with conn:
        conn.execute("INSERT INTO invoices (source_path, file_name, status, vendor_name, vendor_key, created_at) "
                     "VALUES ('a.pdf', 'a.pdf', 'approved', 'Énergir, s.e.c.', ?, ?)", (old, now))  # fmt: skip
        conn.execute("INSERT INTO feedback (vendor_key, vendor_name, description, final_gl, outcome, created_at) "
                     "VALUES (?, 'Énergir, s.e.c.', 'Gas', '6100', 'accepted', ?)", (old, now))  # fmt: skip
        conn.execute("INSERT INTO vendors (vendor_key, display_name, terms, updated_at) VALUES (?, 'Énergir, s.e.c.', "
                     "'Net 15', ?)", (old, now))  # fmt: skip
        conn.execute("INSERT INTO purchase_orders (po_key, po_number, vendor_name, vendor_key, updated_at) "
                     "VALUES ('PO1', 'PO1', 'Énergir, s.e.c.', ?, ?)", (old, now))  # fmt: skip
        conn.execute("INSERT INTO erp_invoices (vendor_key, number_key, total, vendor_name, invoice_number, "
                     "imported_at) VALUES (?, 'A1', 10, 'Énergir, s.e.c.', 'A1', ?)", (old, now))  # fmt: skip
        conn.execute("INSERT INTO lessons (situation, vendor_key, vendor_name, field, created_at, updated_at) "
                     "VALUES (?, ?, 'Énergir, s.e.c.', 'gl', ?, ?)", (f"{old}|invoice|gl|", old, now, now))  # fmt: skip
        conn.execute("INSERT INTO supplier_profiles (key, updated_at) VALUES (?, ?)", (f"name:{old}", now))
        conn.execute("INSERT INTO supplier_outcomes (supplier_key, field, correct, at) VALUES (?, 'total', 1, ?)",
                     (f"name:{old}", now))  # fmt: skip
        conn.execute("DELETE FROM settings WHERE key = 'vendor_key_rules'")  # as an older version left it
    conn.close()
    store = Store(path)
    conn = sqlite3.connect(path)
    for table in ("invoices", "feedback", "vendors", "purchase_orders", "erp_invoices", "lessons"):
        assert conn.execute(f"SELECT vendor_key FROM {table}").fetchall() == [("energir",)], table
    assert conn.execute("SELECT situation FROM lessons").fetchone() == ("energir|invoice|gl|",)
    assert conn.execute("SELECT key FROM supplier_profiles").fetchone() == ("name:energir",)
    assert conn.execute("SELECT supplier_key FROM supplier_outcomes").fetchone() == ("name:energir",)
    conn.close()
    assert store.vendor_terms(vendor_key("Énergir")) == "Net 15"
    from ap_coder.erp.jde import JdeSettings  # JDE address numbers saved by vendor key in the settings

    assert JdeSettings.from_json('{"an8_overrides": {"energir sec": "77"}}').an8_overrides == {"energir": "77"}


def test_rekeyed_vendor_rows_merge_and_on_hold_wins(tmp_path):
    """Two vendor rows that become one vendor under today's rules are merged: an on-hold row is never left behind
    under a key no invoice gives any more, its notes are kept and blank fields are filled."""
    import sqlite3

    path = tmp_path / "t.db"
    store = Store(path)
    store.save_vendor("acme", "Acme", status="active", expected_gst="123456789RT0001")
    store.save_vendor("acme ltee", "Acme Ltée", status="on_hold", notes="bank change pending", province="QC")
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE settings SET value = '1' WHERE key = 'vendor_key_rules'")  # as an older version left it
    store = Store(path)
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT vendor_key FROM vendors").fetchall() == [("acme",)]
    vendor = store.get_vendor(vendor_key("Acme Ltée"))
    assert vendor["status"] == "on_hold"
    assert vendor["notes"] == "bank change pending"
    assert vendor["expected_gst"] == "123456789RT0001" and vendor["province"] == "QC"


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


LAPTOP = "Dell Latitude 7450 laptop, 32GB RAM"  # line 1 of the ground truth


def _laptop_coded(ground_truth, gl):
    coding = copy.deepcopy(ground_truth)
    coding["line_items"][0]["predicted_gl_code"] = gl
    return InvoiceCoding.model_validate(coding)


def _laptop(rows, ground_truth, gl):
    return next(h for h in compare_with_history(_laptop_coded(ground_truth, gl), rows) if h["line_number"] == 1)


def test_no_history_conflict_once_ap_has_confirmed_a_recode(ground_truth):
    """AP moved the laptops to fixed assets after twelve approvals to 6010, and confirmed it on the next invoice:
    1500 is no conflict any more (the twelve older votes are superseded, B-601), and the old account is one."""
    rows = [_row("Northwind IT Solutions Inc.", LAPTOP, "1500"),
            _row("Northwind IT Solutions Inc.", LAPTOP, "1500", outcome=CORRECTED, suggested="6010")]  # fmt: skip
    rows += [_row("Northwind IT Solutions Inc.", LAPTOP, "6010") for _ in range(12)]
    assert _laptop(rows, ground_truth, "1500")["status"] == "match"
    old = _laptop(rows, ground_truth, "6010")
    assert old["status"] == "conflict" and old["history_gl"] == "1500"


def test_a_single_correction_or_a_kept_guess_still_warns(ground_truth):
    """A single correction against twelve approvals may be a slip (it looks the same as a change of mind until the
    next decision), and a guess kept on another account changes nothing: the old account still stands."""
    old = [_row("Northwind IT Solutions Inc.", LAPTOP, "6010") for _ in range(12)]
    for latest in (_row("Northwind IT Solutions Inc.", LAPTOP, "6000", outcome=CORRECTED, suggested="6010"),
                   _row("Northwind IT Solutions Inc.", LAPTOP, "6000", outcome=CODED)):  # fmt: skip
        slip = _laptop([latest, *old], ground_truth, "6000")
        assert slip["status"] == "conflict" and slip["history_gl"] == "6010"
    fixed = [_row("Northwind IT Solutions Inc.", LAPTOP, "6010", outcome=CORRECTED, suggested="6000"),
             _row("Northwind IT Solutions Inc.", LAPTOP, "6000", outcome=CORRECTED, suggested="6010")]  # fmt: skip
    fixed += old
    assert _laptop(fixed, ground_truth, "6000")["status"] == "conflict"  # the slip, put right
    assert _laptop(fixed, ground_truth, "6010")["status"] == "match"


def test_a_recode_confirmed_on_one_line_leaves_similar_lines_their_own_history(ground_truth):
    """The laptops moved to 1500 and confirmed; the laptop batteries, twelve times 6010, were never recoded: their
    6010 is still a match and 1500 a conflict (B-602). Each line's recode counts for that line only."""
    laptop, battery = "Dell Latitude 7450 laptop", "Dell Latitude 7450 laptop battery"
    rows = [_row("Northwind IT Solutions Inc.", laptop, "1500"),
            _row("Northwind IT Solutions Inc.", laptop, "1500", outcome=CORRECTED, suggested="6010")]  # fmt: skip
    rows += [_row("Northwind IT Solutions Inc.", battery, "6010") for _ in range(12)]
    rows += [_row("Northwind IT Solutions Inc.", laptop, "6010") for _ in range(12)]

    def check(gl):
        coding = copy.deepcopy(ground_truth)
        coding["line_items"] = coding["line_items"][:1]
        coding["line_items"][0].update(description=battery, predicted_gl_code=gl)
        return compare_with_history(InvoiceCoding.model_validate(coding), rows)[0]

    assert check("6010")["status"] == "match"
    wrong = check("1500")
    assert wrong["status"] == "conflict" and wrong["history_gl"] == "6010"


def test_a_recode_confirmed_by_filling_in_the_blank_counts(ground_truth):
    """After a reversal is left blank, the clerk who fills it in with the new account is recorded as a correction
    (a blank was proposed): that confirms the recode as well as an approval would (B-605)."""
    rows = [_row("Northwind IT Solutions Inc.", LAPTOP, "1500", outcome=CORRECTED, suggested="UNASSIGNED"),
            _row("Northwind IT Solutions Inc.", LAPTOP, "1500", outcome=CORRECTED, suggested="6010")]  # fmt: skip
    rows += [_row("Northwind IT Solutions Inc.", LAPTOP, "6010") for _ in range(12)]
    assert _laptop(rows, ground_truth, "1500")["status"] == "match"
    examples = select_examples(rows, "Northwind IT Solutions Inc. invoice")
    assert [(e.gl_code, e.decisions) for e in examples] == [("1500", 2)]


def test_examples_leave_out_coding_ap_has_moved_away_from():
    """The chat model is shown the laptops' new account once AP confirmed it, not the approvals it replaced."""
    rows = [_row("Northwind IT Solutions Inc.", LAPTOP, "1500"),
            _row("Northwind IT Solutions Inc.", LAPTOP, "1500", outcome=CORRECTED, suggested="6010")]  # fmt: skip
    rows += [_row("Northwind IT Solutions Inc.", LAPTOP, "6010") for _ in range(5)]
    examples = select_examples(rows, "Northwind IT Solutions Inc. invoice")
    assert [(e.gl_code, e.decisions) for e in examples] == [("1500", 2)]
    once = select_examples(rows[1:], "Northwind IT Solutions Inc. invoice")  # not confirmed yet: both shown
    assert sorted(e.gl_code for e in once) == ["1500", "6010"]


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
    assert store.list_accounts("gl_accounts")[2] == {
        "code": "6020",
        "description": "Software",
        "category": "Opex",
        "meals": False,
    }  # D-90
    store.save_accounts("gl_accounts", [a for a in store.list_accounts("gl_accounts") if a["code"] != "6020"])
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
    assert counts == {ACCEPTED: 4, CORRECTED: 2, CODED: 0}  # corrected line + added line
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
    # The sample invoice fell due on 2026-10-14: from then on it is (rightly) noted as overdue, which is about today's
    # date, not about the coding this test checks.
    assert [i for i in report.issues if i.code != "PAYMENT_OVERDUE"] == []


def test_sample_reference_files_are_importable(tmp_path):
    import csv

    store = Store(tmp_path / "ap.db")
    with (DATA / "chart_of_accounts.csv").open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    result = store.import_accounts("gl_accounts", rows, "gl_code", "description", "category")
    assert result["added"] == len(rows) and result["skipped"] == 0


def test_share_report_from_dashboard_database_is_redacted(tmp_path, ground_truth):
    from ap_coder.ops.share_report import build_share_report

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
