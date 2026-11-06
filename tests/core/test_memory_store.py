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
from tests.conftest import DATA, FakeOpenAI, make_completion


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


def test_a_single_correction_against_many_approvals_still_warns(ground_truth):
    """A single correction against twelve approvals may be a slip (it looks the same as a change of mind until the
    next decision): the old account still stands, as ``suggest_gl`` leaves the line for AP. So do two slips in a row
    (they reverse eleven decisions between them). A guess kept on another account is learned like any approval
    (D-58), and the line is coded with it next time: the history check agrees (D-118)."""
    old = [_row("Northwind IT Solutions Inc.", LAPTOP, "6010") for _ in range(12)]
    vendor = "Northwind IT Solutions Inc."
    for latest in ([_row(vendor, LAPTOP, "6000", outcome=CORRECTED, suggested="6010")],
                   [_row(vendor, LAPTOP, "6000", outcome=CORRECTED, suggested="6020"),
                    _row(vendor, LAPTOP, "6020", outcome=CORRECTED, suggested="6010")]):  # fmt: skip
        slip = _laptop([*latest, *old], ground_truth, "6000")
        assert slip["status"] == "conflict" and slip["history_gl"] == "6010"
    kept = [_row("Northwind IT Solutions Inc.", LAPTOP, "6000", outcome=CODED), *old]
    assert all(h["status"] == "match" for h in compare_with_history(_laptop_coded(ground_truth, "6000"), kept)
               if h["line_number"] == 1)  # fmt: skip
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


def test_a_json_setting_unset_unreadable_or_of_another_kind_is_the_default(tmp_path):
    store = Store(tmp_path / "ap.db")
    assert store.get_json("k", {}) == {} and store.get_json("k") is None
    store.set_setting("k", '{"a": 1}')
    assert store.get_json("k", {}) == {"a": 1} and store.get_json("k") == {"a": 1}
    assert store.get_json("k", []) == []  # a dict where a list is kept
    store.set_setting("k", "{not json")
    assert store.get_json("k", {"b": 2}) == {"b": 2} and store.get_json("k") is None
    store.set_setting("k", "[1, 2]")
    assert store.get_json("k", []) == [1, 2] and store.get_json("k", {}) == {}


def test_no_false_alarm_on_the_line_ap_just_recoded(ground_truth):
    """Four approvals of 6010, then AP corrects the laptop to 1500 once: the next invoice is coded 1500
    (``suggest_gl``: the latest decision wins), and the history check agrees with it instead of warning that
    reviewers coded it to 6010 (B-1420). One rule for both (``memory.latest_decision``, D-118)."""
    rows = [_row("Northwind IT Solutions Inc.", LAPTOP, "1500", outcome=CORRECTED, suggested="6010")]
    rows += [_row("Northwind IT Solutions Inc.", LAPTOP, "6010") for _ in range(4)]
    assert _laptop(rows, ground_truth, "1500")["status"] == "match"
    assert _laptop(rows, ground_truth, "6010")["status"] == "conflict"


def test_suggest_and_the_history_check_follow_one_rule(ground_truth, reference):
    """For any run of approvals before a single correction, the line is coded with the new account exactly when the
    history check takes it as the line's account: no warning on a line coded as AP just taught."""
    from ap_coder.ai.suggest import suggest_gl
    from ap_coder.core.memory import UNCONFIRMED_REVERSAL

    for before in range(1, UNCONFIRMED_REVERSAL + 3):
        rows = [_row("Northwind IT Solutions Inc.", LAPTOP, "6020", outcome=CORRECTED, suggested="6010")]
        rows += [_row("Northwind IT Solutions Inc.", LAPTOP, "6010") for _ in range(before)]
        top = suggest_gl(LAPTOP, "Northwind IT Solutions Inc.", rows, reference)[0]
        coded = top.auto and top.gl_code == "6020"
        warned = _laptop(rows, ground_truth, "6020")["status"] == "conflict"
        assert coded == (before < UNCONFIRMED_REVERSAL) and warned == (not coded), before


# --- The whole memory kept: refreshed with only the lessons added, read once at a time ---------------------------


def _full_read(store, limit):
    from ap_coder.core.store.summary import fetch_rows

    with store._conn() as conn:
        return fetch_rows(conn, "SELECT * FROM feedback ORDER BY outcome = 'history', id DESC LIMIT ?", (limit,))


def test_the_memory_refreshed_with_the_lessons_added_is_the_memory_read_again(tmp_path, ground_truth, monkeypatch):
    # The memory kept is refreshed with only the lessons added since (id > the newest kept): it must be what reading
    # it all again gives, after approvals, history imports, a line forgotten, history forgotten and lessons changed
    # by an upgrade, with the read cap reached (reviewer decisions first, then history) and without.
    from ap_coder.core.store import _base, learning

    store = Store(tmp_path / "ap.db")
    reads = []
    real = learning.fetch_rows

    def spy(conn, sql, params=()):
        reads.append(sql)
        return real(conn, sql, params)

    monkeypatch.setattr(learning, "fetch_rows", spy)

    def history(n, start):
        store.import_history([{"vendor_name": f"Freight {i}", "description": f"Freight lot {i}", "gl_code": "5100",
                               "amount": 10.0, "date": "2025-06-01"} for i in range(start, start + n)])  # fmt: skip

    def approve():
        inv = store.add_invoice(tmp_path / "x.pdf", ground_truth, {"requires_review": True})
        store.approve_invoice(inv, copy.deepcopy(ground_truth), reviewer="ap.clerk")
        return inv

    def same(how):
        del reads[:]
        for limit in (None, 7, 12):
            got = store.feedback_rows(limit=limit)
            assert got == _full_read(store, limit or learning.FEEDBACK_READ), (how, limit)
        return list(reads)

    history(4, 0)
    first = approve()
    same("first read")
    history(1, 10)
    approve()
    read = same("history and an approval added")
    assert len(read) == 3 and all(" WHERE id > ?" in sql for sql in read)  # only the new ones read
    approve()
    history(9, 20)  # more history than the smaller caps hold
    same("added past the cap")
    store.delete_feedback([store.feedback_rows()[3]["id"]])
    assert not any(" WHERE id > ?" in sql for sql in same("a line forgotten"))  # read all again
    approve()
    store.delete_invoice(first, forget_lessons=True)
    history(2, 40)
    same("an invoice's lessons forgotten, then more added")
    store.forget_history()
    same("history forgotten")
    history(5, 50)
    with store._conn() as conn:  # an upgrade changes lessons in place, and drops what is kept
        conn.execute("UPDATE feedback SET final_gl = '6999' WHERE outcome = 'history'")
    _base._forget_learning(store.path)
    same("changed by an upgrade")
    approve()
    assert same("added once more") and store.feedback_rows() == _full_read(store, 50_000)


def test_the_memory_is_read_by_one_page_at_a_time(tmp_path, monkeypatch):
    # Under load every page that found the memory out of date read its own copy at once (50,000 lessons, 130 MB
    # each): one reads it, the others wait for it and share it.
    import threading
    import time

    from ap_coder.core.store import learning

    store = Store(tmp_path / "ap.db")
    store.import_history([{"vendor_name": "Freight", "description": f"Freight lot {i}", "gl_code": "5100",
                           "amount": 10.0, "date": "2025-06-01"} for i in range(5)])  # fmt: skip
    reads = []
    real = learning.fetch_rows

    def slow(conn, sql, params=()):
        reads.append(sql)
        time.sleep(0.3)
        return real(conn, sql, params)

    monkeypatch.setattr(learning, "fetch_rows", slow)
    got = []
    threads = [threading.Thread(target=lambda: got.append(store.feedback_rows())) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(reads) == 1 and len(got) == 4 and all(g is got[0] for g in got)


def test_a_vendors_lessons_are_worked_out_once_until_one_of_them_changes(tmp_path, ground_truth, monkeypatch):
    # The review screen and every check of its edits read the vendor's lessons (the history check, "Used for this
    # vendor", the usual cost center): up to 50,000 for the biggest vendor. Worked out once and kept until one of this
    # vendor's lessons is added or removed, they say what its lessons read again say.
    from ap_coder.core.memory import _aggregate
    from ap_coder.core.store import learning

    store = Store(tmp_path / "ap.db")
    vendor = ground_truth["vendor_name"]
    reads = []
    real = learning.fetch_rows

    def spy(conn, sql, params=()):
        if sql.startswith("SELECT " + learning._VENDOR_COLUMNS):  # (not the feedback_rows below)
            reads.append(sql)
        return real(conn, sql, params)

    def expected():
        rows = store.feedback_rows(vendor_name=vendor)
        counts = {}
        for r in rows:
            if r["final_gl"] and r["final_gl"] != "UNASSIGNED":
                counts[r["final_gl"]] = counts.get(r["final_gl"], 0) + 1
        return ([vars(p) for p in _aggregate(rows)], sorted(counts, key=lambda c: -counts[c]),
                {r["final_cc"] or "" for r in rows})  # fmt: skip

    def same():
        got = store.vendor_lessons(vendor)
        assert ([vars(p) for p in got.patterns], got.accounts, set(got.cost_centers)) == expected()
        return got

    monkeypatch.setattr(learning, "fetch_rows", spy)
    assert store.vendor_lessons(vendor).patterns == [] and not reads  # no lesson: nothing read
    inv = store.add_invoice(tmp_path / "x.pdf", ground_truth, {"requires_review": True})
    final = copy.deepcopy(ground_truth)
    final["line_items"][1]["predicted_gl_code"] = "6010"
    store.approve_invoice(inv, final, reviewer="ap.clerk")
    first = same()
    del reads[:]
    assert store.vendor_lessons(vendor) is first and not reads  # kept: nothing read again
    store.import_history([{"vendor_name": "Someone Else", "description": "Freight", "gl_code": "5100",
                           "amount": 1.0, "date": "2025-06-01"}])  # fmt: skip
    assert store.vendor_lessons(vendor) is first and not reads  # another vendor's lesson changes nothing here
    store.import_history([{"vendor_name": vendor, "description": "Toner cartridges", "gl_code": "6300",
                           "amount": 1.0, "date": "2025-06-01"}])  # fmt: skip
    assert same() is not first and len(reads) == 1  # one of its own: worked out again
    store.delete_feedback([store.feedback_rows(vendor_name=vendor)[0]["id"]])
    same()
    store.delete_invoice(inv, forget_lessons=True)
    assert same().accounts == ["6300"]


def test_a_vendors_lessons_past_the_memory_are_read_once_until_they_or_the_memory_change(tmp_path, monkeypatch):
    # Past the read cap, the review screen's GL suggestions read the whole memory plus the vendor's lessons it does not
    # hold (``learning_rows``): all of the vendor's were read on every page and check (50,000 for the biggest), and
    # their new copies made the suggestions' index tell the rows apart by what they say (half a second). Only the ones
    # past the memory are read now, once until the vendor's lessons or the memory change, as reading them all gives.
    from ap_coder.core.store import learning

    store = Store(tmp_path / "ap.db")
    monkeypatch.setattr(learning, "FEEDBACK_READ", 12)
    reads = []
    real = learning.fetch_rows

    def spy(conn, sql, params=()):
        reads.append(sql)
        return real(conn, sql, params)

    def history(vendor, n, start, outcome_date="2025-06-01"):
        store.import_history([{"vendor_name": vendor, "description": f"Freight lot {i}", "gl_code": "5100",
                               "amount": 10.0, "date": outcome_date} for i in range(start, start + n)])  # fmt: skip

    def expected(vendor):  # what learning_rows gave reading all of the vendor's lessons
        rows = store.feedback_rows()
        have = {r["id"] for r in rows if r["vendor_key"] == vendor_key(vendor)}
        missing = [r for r in store.feedback_rows(vendor_name=vendor) if r["id"] not in have]
        return rows + missing

    history("Northwind IT", 9, 0)  # imported first: pushed out of the memory by the others
    history("Zenith Freight", 10, 100)
    monkeypatch.setattr(learning, "fetch_rows", spy)
    first = store.learning_rows("Northwind IT")
    assert first == expected("Northwind IT") and len(first) > 12
    del reads[:]
    again = store.learning_rows("Northwind IT")
    assert again == first and all(a is b for a, b in zip(again, first, strict=True)) and not reads  # nothing read
    history("Zenith Freight", 3, 200)  # the memory moves on: more of Northwind's fall out of it
    assert store.learning_rows("Northwind IT") == expected("Northwind IT")
    history("Northwind IT", 2, 300)  # its own newest are in the memory again
    assert store.learning_rows("Northwind IT") == expected("Northwind IT")
    assert store.learning_rows("Zenith Freight") == expected("Zenith Freight")
    store.forget_history()
    assert store.learning_rows("Northwind IT") == store.feedback_rows() == []


def _lesson(conn, vendor, description="Part 1", outcome=ACCEPTED, invoice=None, gl="6000"):
    """A lesson put straight into the database (an approval's, or imported history), its id."""
    return conn.execute(
        "INSERT INTO feedback (invoice_id, line_number, vendor_key, vendor_name, description, final_gl, outcome, "
        "created_at) VALUES (?, 1, ?, ?, ?, ?, ?, '2026-01-01')", (invoice, vendor, vendor, description, gl, outcome)
    ).lastrowid  # fmt: skip


def _lines(lines):
    """What ``PastLines`` says, however it was made (read whole or carried forward): its lessons' lines, newest first,
    and each line's runs, each numbered by its place."""
    return (
        [lines.names[i] for i in lines.order],
        {line: [(*run[:2], run[2] - lines.head, *run[3:]) for run in runs] for line, runs in lines.lines.items()},
    )


def _past_read(store, key, last, cap):
    """What ``learning_rows`` adds for this vendor, worked out from all its lessons: its newest ``cap`` in the memory's
    order (decisions first, then history, each newest first), those after the memory's ``last``."""
    place = lambda r: (r["outcome"] == "history", -r["id"])  # noqa: E731
    with store._conn() as conn:
        rows = sorted((dict(r) for r in conn.execute("SELECT * FROM feedback WHERE vendor_key = ?", (key,))), key=place)
    return [r for r in rows[:cap] if place(r) > place(last)]


def test_a_vendors_older_lessons_carried_forward_are_what_reading_them_again_gives(tmp_path, monkeypatch):
    # D-173: past the memory's cap each approval moves its last lesson, and every vendor past it read all its older
    # lessons again, in every process (up to 50,000 rows for the biggest). They are carried forward now (the vendor's
    # lessons the memory just let go put in front, the oldest past its own cap dropped) and read whole after anything
    # else. Random approvals, history imports, re-approvals, lessons forgotten, moved to another vendor, changed by an
    # upgrade, more than the memory holds at once and a database restored, vendors asked now and then: each time
    # what reading them all gives.
    import random
    import sqlite3

    from ap_coder.core.memory import PastLines
    from ap_coder.core.store import _base, learning

    cap = 30
    monkeypatch.setattr(learning, "FEEDBACK_READ", cap)
    store = Store(tmp_path / "ap.db")
    rng = random.Random(173)
    vendors = [vendor_key(v) for v in ("Acme Supply", "Beta Ltd", "Gamma Inc.", "Delta", "Echo Freight", "Fox")]
    reads = {"carried": 0, "whole": 0}
    real = learning.fetch_rows

    def spy(conn, sql, params=()):
        if "LIMIT ?) WHERE" in sql:
            reads["whole"] += 1
        elif sql.startswith("SELECT * FROM feedback WHERE vendor_key = ? AND ("):
            reads["carried"] += 1
        return real(conn, sql, params)

    monkeypatch.setattr(learning, "fetch_rows", spy)
    invoices = [0]

    def add(conn, n, history=False, vendor=None):
        invoices[0] += 1
        for _ in range(n):
            outcome = "history" if history else rng.choice([ACCEPTED, CORRECTED, CODED, "bulk"])
            _lesson(conn, vendor or rng.choices(vendors, weights=[12, 4, 2, 1, 1, 0.3])[0], f"Part {rng.randint(1, 9)}",
                    outcome, None if history else invoices[0], rng.choice(["5000", "6000", "6100"]))  # fmt: skip

    def any_lesson(conn, where="1"):
        ids = [r[0] for r in conn.execute(f"SELECT id FROM feedback WHERE {where} ORDER BY id")]
        return rng.choice(ids) if ids else None

    with store._conn() as conn:
        add(conn, 20, history=True)
        add(conn, 15)
    saved, ops = None, []
    for step in range(300):
        op = rng.choices(["approve", "history", "reapprove", "forget", "move", "upgrade", "many", "save", "restore"],
                         weights=[60, 8, 5, 3, 2, 1, 1, 2, 1])[0]  # fmt: skip
        ops.append(op)
        with store._conn() as conn:
            if op == "approve":
                add(conn, rng.randint(1, 6))
            elif op == "history":
                add(conn, rng.randint(1, 8), history=True)
            elif op == "reapprove" and (lesson := any_lesson(conn, "invoice_id IS NOT NULL")) is not None:
                invoice, vendor = conn.execute("SELECT invoice_id, vendor_key FROM feedback WHERE id = ?",
                                               (lesson,)).fetchone()  # fmt: skip
                conn.execute("DELETE FROM feedback WHERE invoice_id = ?", (invoice,))
                add(conn, rng.randint(1, 4), vendor=vendor)
            elif op == "forget" and (lesson := any_lesson(conn)) is not None:
                conn.execute("DELETE FROM feedback WHERE id = ?", (lesson,))
            elif op == "move" and (lesson := any_lesson(conn)) is not None:  # a vendor merged into another
                conn.execute("UPDATE feedback SET vendor_key = ? WHERE id = ?", (rng.choice(vendors), lesson))
            elif op == "upgrade" and (lesson := any_lesson(conn)) is not None:  # changed in place: what is kept dropped
                conn.execute("UPDATE feedback SET final_gl = '6999' WHERE id = ?", (lesson,))
                conn.execute("DELETE FROM past_lines")  # (as the upgrades that change lessons do, D-178)
            elif op == "many":
                add(conn, cap + 5)
        if op == "upgrade":
            _base._forget_learning(store.path)
        if op == "save":
            saved = sqlite3.connect(":memory:")
            with sqlite3.connect(store.path) as live:
                live.backup(saved)
        elif op == "restore" and saved is not None:
            with sqlite3.connect(store.path) as live:
                saved.backup(live)
        with store.session():
            rows = store.feedback_rows()
            for key in rng.sample(vendors, rng.randint(1, len(vendors))):  # some vendors asked, not all, each time
                got = store.learning_rows(key, key)
                past = _past_read(store, key, rows[-1], cap) if len(rows) >= cap else []
                assert list(got) == rows + past, (step, op, key)
                if past:
                    assert got.past is store.learning_rows(key, key).past  # kept until something changes
                    assert _lines(got.older) == _lines(PastLines.of(past)), (step, op, key)  # and by line (D-178)
    assert reads["carried"] > 200 and reads["whole"] > 20, reads  # both ways, many times
    assert {"approve", "reapprove", "forget", "move", "upgrade", "many", "restore"} <= set(ops)


def test_a_vendors_older_lessons_are_read_again_when_more_than_the_memorys_last_moved(tmp_path, monkeypatch):
    # D-173's carry forward at its edges: the memory's last moved from the history to the decisions (the vendor's
    # history lesson it was), history of the vendor imported past the memory (read again), a database restored whose
    # next lessons took the ids of those it lost (read again), and more decisions at once than the memory holds, the
    # vendor's among those it doesn't. Both the rows (read when they are) and the lines (always: D-178).
    import sqlite3

    from ap_coder.core.memory import PastLines
    from ap_coder.core.store import learning

    monkeypatch.setattr(learning, "FEEDBACK_READ", 5)
    store = Store(tmp_path / "ap.db")
    whole, real = [], learning.fetch_rows
    monkeypatch.setattr(learning, "fetch_rows", lambda conn, sql, params=(): whole.append(
        ("lines" if learning._LINE_COLUMNS in sql else "rows") if "LIMIT ?) WHERE" in sql else "") or real(
        conn, sql, params))  # fmt: skip

    def same(how, rows_how=None):
        del whole[:]
        rows = store.feedback_rows()
        got, past = store.learning_rows("x", "x"), _past_read(store, "x", rows[-1], 5)
        assert list(got) == rows + past
        assert _lines(got.older if past else PastLines.of([])) == _lines(PastLines.of(past))
        assert ("read again" if "lines" in whole else "carried") == how
        assert ("read again" if "rows" in whole else "carried") == (rows_how or how) or not past

    with store._conn() as conn:
        for vendor in "yxyyyy":
            _lesson(conn, vendor, outcome="history")  # 1 to 6: the memory's last is x's (2)
    same("read again")
    with store._conn() as conn:
        for _ in range(5):
            _lesson(conn, "z")  # 7 to 11: the memory holds only decisions now, x's 2 is past its last (7)
    same("carried", "read again")  # (its rows: none kept, the first time there are some)
    with store._conn() as conn:
        _lesson(conn, "x", outcome="history")  # 12: history, past the memory's last
    same("read again")
    saved = sqlite3.connect(":memory:")
    with sqlite3.connect(store.path) as live:
        live.backup(saved)
    with store._conn() as conn:
        _lesson(conn, "x", "Part before the restore")  # 13
        for _ in range(6):
            _lesson(conn, "z")  # 14 to 19
    same("carried")
    with sqlite3.connect(store.path) as live:
        saved.backup(live)
    with store._conn() as conn:
        assert _lesson(conn, "x", "Part after the restore") == 13  # the id of the one lost, another lesson
        for _ in range(7):
            _lesson(conn, "z")  # 14 to 20: the memory's last moves on (16)
    same("carried", "read again")  # (the lines from the database's own copy, restored with it)
    with store._conn() as conn:
        _lesson(conn, "z")  # 21
        _lesson(conn, "x", "Part 2")  # 22: past the memory's last once the next five are in (23)
        for _ in range(5):
            _lesson(conn, "z")  # 23 to 27
    same("carried")


def test_a_vendors_lessons_are_counted_once_a_page_while_nothing_changes(tmp_path, monkeypatch):
    # A check asked for the stamp of the vendor's lessons three or four times (the history check, the accounts and
    # cost centers used for it, its older lessons past the memory), each counting all of them: 8 ms each for the
    # biggest vendor at 300,000 invoices. Once a page now while the database does not change; a change meanwhile, by
    # another process or the page itself, counts them again.
    import sqlite3

    from ap_coder.core.store import learning

    monkeypatch.setattr(learning, "FEEDBACK_READ", 5)
    store = Store(tmp_path / "ap.db")
    with store._conn() as conn:
        for n in range(12):
            _lesson(conn, "acme" if n % 2 else "beta", f"Part {n}")
    counted, real = [], learning._vendor_stamp
    monkeypatch.setattr(learning, "_vendor_stamp", lambda conn, key: counted.append(key) or real(conn, key))

    def parts():
        return {p.description for p in store.vendor_lessons("Acme").patterns}

    store.learning_rows("Acme")  # (its older lessons' first read is kept for every process: a change, D-178)
    del counted[:]
    with store.session():
        assert len(parts()) == 5 and store.learning_rows("Acme").past and parts()  # (its newest FEEDBACK_READ)
        assert counted == ["acme"]
        with sqlite3.connect(store.path) as other:  # another process's approval meanwhile
            _lesson(other, "acme", "Freight")
        assert "Freight" in parts() and store.learning_rows("Acme").past
        assert counted == ["acme"] * 2
        with store._conn() as conn:  # the page's own
            _lesson(conn, "acme", "Toner")
        assert "Toner" in parts() and counted == ["acme"] * 3
    parts()
    parts()  # outside a page: each time
    assert counted == ["acme"] * 5


def test_a_vendors_older_lessons_kept_from_a_race_with_an_approval_are_not_carried_forward(tmp_path, monkeypatch):
    # B-1809: the whole memory and the vendor's own lessons are read a moment apart (``learning_rows``); an approval
    # of the vendor between the two, past the memory's cap, had its older lessons kept for a memory they were not read
    # with, and D-173's carry forward then gave it lessons too many at every approval after (the GL suggestions'
    # counts with them) until something made them be read whole. What is read across such a moment is used once and
    # not kept: the next ask reads them again from one moment.
    import sqlite3

    from ap_coder.core.memory import PastLines
    from ap_coder.core.store import learning

    monkeypatch.setattr(learning, "FEEDBACK_READ", 5)
    store = Store(tmp_path / "ap.db")
    sql = ("INSERT INTO feedback (vendor_key, vendor_name, description, final_gl, outcome, created_at) "
           "VALUES ('acme', 'acme', 'line', '6100', 'corrected', datetime('now'))")  # fmt: skip

    def add() -> None:
        with sqlite3.connect(store.path) as conn:
            conn.execute(sql)

    def fresh() -> list[int]:
        with sqlite3.connect(store.path) as conn:
            memory = [
                r[0] for r in conn.execute("SELECT id FROM feedback ORDER BY outcome = 'history', id DESC LIMIT 5")
            ]
            own = [r[0] for r in conn.execute("SELECT id FROM feedback WHERE vendor_key = 'acme' ORDER BY "
                                              "outcome = 'history', id DESC LIMIT 5")]  # fmt: skip
        return memory + [i for i in own if i < memory[-1]]

    for _ in range(6):
        add()
    real, raced = store.feedback_rows, []

    def racing(*a, **k):
        rows = real(*a, **k)
        if not raced:  # an approval of the vendor lands just after the whole memory was read
            raced.append(1)
            add()
        return rows

    monkeypatch.setattr(store, "feedback_rows", racing)
    store.learning_rows("acme", key="acme")
    for _ in range(4):
        got = store.learning_rows("acme", key="acme")
        assert [r["id"] for r in got] == fresh()
        assert _lines(got.older if got is not store.feedback_rows() else PastLines.of([])) == _lines(
            PastLines.of(getattr(got, "past", []))
        )  # (and by line, D-178)
        add()


def test_a_vendors_lessons_read_inside_a_write_that_is_undone_are_not_answered_after_it(tmp_path):
    # B-1810: the vendor's lessons' stamp, read once a page while the database does not change (D-173), was also
    # kept from inside a write: one undone moves neither of the session's counts, so the rest of the page was told
    # of the lesson that was undone. Inside a write it is read afresh.
    import sqlite3

    store = Store(tmp_path / "ap.db")
    sql = ("INSERT INTO feedback (vendor_key, vendor_name, description, final_gl, outcome, created_at) "
           "VALUES ('acme', 'acme', ?, ?, 'corrected', datetime('now'))")  # fmt: skip
    with sqlite3.connect(store.path) as conn:
        conn.execute(sql, ("gloves", "6100"))
    with store.session():
        assert store.vendor_lessons("acme", "acme").accounts == ["6100"]
        try:
            with store._conn() as conn:
                conn.execute(sql, ("toner", "7200"))
                assert "7200" in store.vendor_lessons("acme", "acme").accounts
                raise RuntimeError("undone")
        except RuntimeError:
            pass
        assert store.vendor_lessons("acme", "acme").accounts == ["6100"]


def test_a_vendors_older_lessons_by_line_kept_for_every_process_are_what_reading_them_again_gives(
    tmp_path, monkeypatch, reference
):
    # D-178: at 300,000 invoices most vendors have thousands of lessons past the memory's cap, and each process kept
    # 32 vendors' while the review queue's invoices came from a thousand: nearly every check read them whole (about
    # 115 ms of CPU). What the GL suggestions read of them, their lines, is kept in the database for every process and
    # carried forward as the memory moves on; the rows themselves are read only when they are. Two processes on one
    # database, random approvals (some vendors past the cap of their own), invoices approved again, lessons forgotten
    # (by the Learning page and by plain SQL), history imported, an upgrade, more than the memory holds at once and a
    # database restored, vendors asked in either process: each time the lines are what reading the lessons whole
    # gives, the rows too when they are read, and the GL suggestions are those of a memory built of all the rows.
    import random
    import sqlite3

    from ap_coder.ai import suggest
    from ap_coder.ai.line_match import keys
    from ap_coder.core import memory
    from ap_coder.core.memory import PastLines
    from ap_coder.core.reference_data import UNASSIGNED
    from ap_coder.core.store import _base, learning

    cap = 30
    monkeypatch.setattr(learning, "FEEDBACK_READ", cap)
    monkeypatch.setattr(learning, "PAST_WRITE_AFTER", 4)
    for name in ("_PAST", "_OLDER"):
        monkeypatch.setattr(suggest, name, {})
    monkeypatch.setattr(suggest, "_MEMORY", suggest.RowsCache())
    store = Store(tmp_path / "ap.db")
    tax_gls = reference.tax.tax_gl_codes()
    valid = frozenset(c for c in reference.chart_of_accounts.codes if c not in tax_gls and c != UNASSIGNED)
    accounts = [*sorted(valid)[:4], "9999"]  # (9999: not in the chart)
    rng = random.Random(178)
    vendors = [vendor_key(v) for v in ("Acme Supply", "Beta Ltd", "Gamma Inc.", "Delta", "Echo Freight", "Fox")]
    items = ["Nitrile gloves", "Copy paper", "Toner cartridge", "Freight charge", "Hydraulic hose", "the item"]

    def caches():  # what each process keeps for itself
        return [_base._FEEDBACK, _base._METRICS, _base._VENDOR_LESSONS, _base._PAST_MEMORY, _base._PAST_LINES,
                suggest._PAST, suggest._OLDER]  # fmt: skip

    processes = [[{} for _ in caches()] + [()] for _ in range(2)]
    here = [0]

    def in_process(n):
        if n != here[0]:
            processes[here[0]] = [dict(kept) for kept in caches()] + [suggest._MEMORY._slots]
            for kept, theirs in zip(caches(), processes[n], strict=False):
                kept.clear()
                kept.update(theirs)
            suggest._MEMORY._slots = processes[n][-1]
            here[0] = n

    seen = {"from the copy": 0, "written": 0}
    stored, keep = learning.LearningMixin._stored_past_lines, learning.LearningMixin._keep_past_lines

    def from_copy(self, conn, key):
        found = stored(self, conn, key)
        seen["from the copy"] += found is not None
        return found

    def written(self, key, stamp, lines):
        done = keep(self, key, stamp, lines)
        seen["written"] += done
        return done

    monkeypatch.setattr(learning.LearningMixin, "_stored_past_lines", from_copy)
    monkeypatch.setattr(learning.LearningMixin, "_keep_past_lines", written)
    invoices = [0]

    def add(conn, n, history=False, vendor=None):
        invoices[0] += 1
        for _ in range(n):
            v = vendor or rng.choices(vendors, weights=[12, 4, 2, 1, 1, 0.3])[0]
            gl = rng.choice(accounts)
            conn.execute(
                "INSERT INTO feedback (invoice_id, line_number, vendor_key, vendor_name, description, final_gl, "
                "final_cc, suggested_gl, outcome, created_at) VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?, '2026-01-01')",
                (None if history else invoices[0], v, v, f"{rng.choice(items)}{rng.choice(['', ' XL', ' 2'])}", gl,
                 rng.choice(["", "CC10"]), rng.choice([gl, "", "6000"]),
                 "history" if history else rng.choice([ACCEPTED, ACCEPTED, CORRECTED, CODED, "bulk"])),
            )  # fmt: skip

    def any_lesson(conn, where="1"):
        ids = [r[0] for r in conn.execute(f"SELECT id FROM feedback WHERE {where} ORDER BY id")]
        return rng.choice(ids) if ids else None

    with store._conn() as conn:
        add(conn, 20, history=True)
        add(conn, 15)
    saved, ops, asked = None, [], 0
    for step in range(250):
        op = rng.choices(["approve", "history", "reapprove", "forget", "unlearn", "upgrade", "many", "save",
                          "restore"], weights=[60, 6, 5, 3, 3, 2, 1, 2, 1])[0]  # fmt: skip
        ops.append(op)
        with store._conn() as conn:
            if op == "approve":
                add(conn, rng.randint(1, 6))
            elif op == "history":
                add(conn, rng.randint(1, 8), history=True)
            elif op == "reapprove" and (lesson := any_lesson(conn, "invoice_id IS NOT NULL")) is not None:
                invoice, vendor = conn.execute("SELECT invoice_id, vendor_key FROM feedback WHERE id = ?",
                                               (lesson,)).fetchone()  # fmt: skip
                conn.execute("DELETE FROM feedback WHERE invoice_id = ?", (invoice,))  # (as an invoice reopened)
                add(conn, rng.randint(1, 4), vendor=vendor)
            elif op == "unlearn" and (lesson := any_lesson(conn)) is not None:
                conn.execute("DELETE FROM feedback WHERE id = ?", (lesson,))
            elif op == "upgrade" and (lesson := any_lesson(conn)) is not None:  # changed in place by an upgrade
                conn.execute("UPDATE feedback SET final_gl = ? WHERE id = ?", (rng.choice(accounts), lesson))
                conn.execute("DELETE FROM past_lines")
            elif op == "many":
                add(conn, cap + 5)
            forgotten = any_lesson(conn) if op == "forget" else None
        if forgotten is not None:
            store.delete_feedback([forgotten])  # (the Learning page: what is kept of it goes with it)
        elif op == "upgrade":
            for n in range(2):
                in_process(n)
                _base._forget_learning(store.path)
        elif op == "save":
            saved = sqlite3.connect(":memory:")
            with sqlite3.connect(store.path) as live:
                live.backup(saved)
        elif op == "restore" and saved is not None:
            with sqlite3.connect(store.path) as live:
                saved.backup(live)
        in_process(rng.randint(0, 1))
        rows = store.feedback_rows()
        for key in rng.sample(vendors, rng.randint(1, 3)):  # some vendors asked, in one process or the other
            got = store.learning_rows(key, key)
            past = _past_read(store, key, rows[-1], cap) if len(rows) >= cap else []
            if not past:
                assert got is rows, (step, op, key)
                continue
            asked += 1
            assert _lines(got.older) == _lines(PastLines.of(past)), (step, op, key)
            built = suggest._build_memory(rows + past, valid)
            for item in rng.sample(items, 2):
                line = f"{item} XL"
                assert suggest._suggested(line, keys(line), key, suggest._memory(got, valid), reference, valid, 3,
                                          "") == suggest._suggested(line, keys(line), key, built, reference, valid,
                                                                    3, ""), (step, op, key, item)  # fmt: skip
            if rng.random() < 0.3:  # the rows, now and then (an invoice read: its examples, its history check)
                assert list(got) == rows + past and isinstance(got, memory.WithPast), (step, op, key)
    assert asked > 300 and seen["from the copy"] > 50 and seen["written"] > 50, (asked, seen)
    assert {"approve", "reapprove", "forget", "unlearn", "upgrade", "many", "restore"} <= set(ops)


def test_the_lines_kept_for_every_process_are_taken_only_as_this_version_made_them(tmp_path, monkeypatch):
    # D-178: the database's copy of a vendor's older lessons by line is JSON with the stamp it was worked out under.
    # One another version made, or for another size of the memory, one that can't be read or doesn't add up, or none
    # at all (a backup of an older version restored, before its upgrade made the table) is worked out again.
    import sqlite3

    from ap_coder.core.memory import PastLines
    from ap_coder.core.store import _base, learning

    monkeypatch.setattr(learning, "FEEDBACK_READ", 5)
    store = Store(tmp_path / "ap.db")
    with store._conn() as conn:
        for n in range(12):
            _lesson(conn, "acme" if n < 6 else "beta", f"Part {n % 3}", gl=f"6{n % 2}00")

    def asked():  # in a process that keeps nothing of its own yet
        _base._PAST_LINES.clear()
        rows = store.feedback_rows()
        got = store.learning_rows("acme", "acme")
        assert _lines(got.older) == _lines(PastLines.of(_past_read(store, "acme", rows[-1], 5)))
        return got.older

    right = asked()
    with sqlite3.connect(store.path) as conn:
        stamp, body = conn.execute("SELECT stamp, body FROM past_lines WHERE vendor_key = 'acme'").fetchone()
    wrong = json.dumps(PastLines.of([]).encoded())
    first, cap = json.loads(stamp)[:2]
    for stamped, kept in [
        (json.dumps(["0.1/1", cap, *json.loads(stamp)[2:]]), wrong),  # another version's
        (json.dumps([first, cap + 1, *json.loads(stamp)[2:]]), wrong),  # for another size of the memory
        (stamp, body.replace('"n":5', '"n":4')),  # doesn't add up
        (stamp, body[:-3]),  # can't be read
        (stamp, json.dumps({**json.loads(body), "order": "!"})),
    ]:  # fmt: skip
        with sqlite3.connect(store.path) as conn:
            conn.execute("UPDATE past_lines SET stamp = ?, body = ?", (stamped, kept))
        assert asked().n == right.n == 5, stamped
    with sqlite3.connect(store.path) as conn:
        conn.execute("DROP TABLE past_lines")
    assert asked().n == 5
