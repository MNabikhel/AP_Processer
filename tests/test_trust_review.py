"""Before testing: the review screen and coding (Sweep 4, B-463 on). A guess is never passed off as a learned code;
the review screen and the reports say plainly what is known and what still needs a person."""

from __future__ import annotations

import datetime as dt
import html
from dataclasses import replace
from types import SimpleNamespace

from ap_coder.ai import inference
from ap_coder.ai.inference import MODEL_SUGGESTED
from ap_coder.ai.offline_coder import WEAK_MATCH, guessed
from ap_coder.ai.pipeline import InvoicePipeline
from ap_coder.core.config import Settings
from ap_coder.core.memory import ACCEPTED, CODED, CORRECTED
from ap_coder.core.reference_data import UNASSIGNED
from ap_coder.core.schema import InvoiceCoding
from ap_coder.core.store import Store, load_sample_setup
from ap_coder.erp import accruals

from .conftest import SAMPLES, copy_database
from .webclient import client_for

SAMPLE_PDF = SAMPLES / "harbourview_NS_HST_HPS-2026-0347.pdf"


def _invoice(lines, invoice_date="2026-09-10", **header):
    return {"vendor_name": "Acme Supply", "invoice_number": "A-1", "invoice_date": invoice_date, "currency": "CAD",
            "subtotal": sum(li["amount"] for li in lines), "tax_lines": [], "tax_total": 0.0,
            "grand_total": sum(li["amount"] for li in lines), "confidence_score": 0.9,
            "line_items": [{"line_number": n, "quantity": 1.0, "unit_price": li["amount"], "taxes_applied": [],
                            "predicted_cost_center": "", **li} for n, li in enumerate(lines, 1)], **header}  # fmt: skip


# --- B-463: a weak match is never put in a line ---------------------------------------------------------------------


def test_with_lm_studio_chosen_but_not_answering_a_weak_match_is_left_blank(tmp_path, reference):
    """The pilot tester's set-up: LM Studio is the chat model but isn't running. The account whose name shares a word
    with the line (Google -> Meals, AWS -> Consulting) was put in the line, looking like a learned code."""
    store = Store(tmp_path / "ap.db")
    load_sample_setup(store)
    s = Settings()
    settings = replace(s, llm=replace(s.llm, provider="local", base_url="http://127.0.0.1:9/v1"))
    result = InvoicePipeline(settings, store.reference_data(), store=store).process(SAMPLE_PDF)
    assert result.ok, result.error
    lines = result.output["line_items"]
    assert lines and not [li for li in lines if guessed(li["reasoning_justification"])]
    weak = [li for li in lines if li["reasoning_justification"].startswith("Not sure enough")]
    assert weak and {li["predicted_gl_code"] for li in weak} == {UNASSIGNED}  # suggested on the screen instead


def _coding(*descriptions):
    return InvoiceCoding.model_validate(
        _invoice(
            [
                {
                    "description": d,
                    "amount": 10.0,
                    "predicted_gl_code": UNASSIGNED,
                    "reasoning_justification": "Not sure",
                }
                for d in descriptions
            ]
        )
    )


def test_the_chat_models_answer_is_a_guess_and_says_how_it_compares_with_the_weak_match(monkeypatch):
    coding = _coding("Copy paper", "Toner", "Desk")
    monkeypatch.setattr(inference, "suggest_accounts", lambda coder, vendor, lines, history="": {
        1: ("6000", "", "office supplies"), 2: ("6010", "", "printer")})  # fmt: skip
    result = SimpleNamespace(coding=coding, model="", hints={1: "6000", 2: "6400", 3: "6000"})
    InvoicePipeline._local_accounts(SimpleNamespace(coder=SimpleNamespace(_local_model="m")), result, "")
    paper, toner, desk = coding.line_items
    assert paper.predicted_gl_code == "6000" and "name matches the line too" in paper.reasoning_justification
    assert toner.predicted_gl_code == "6010" and "pointed to 6400 instead" in toner.reasoning_justification
    assert desk.predicted_gl_code == UNASSIGNED  # no answer for it: the weak match is not put in on its own
    assert guessed(paper.reasoning_justification) and guessed(toner.reasoning_justification)


def test_a_kept_guess_is_the_reviewers_coding_not_coded_as_suggested(tmp_path):
    store = Store(tmp_path / "ap.db")
    load_sample_setup(store)
    ai = _invoice([{"description": "Google Workspace", "amount": 10.0, "predicted_gl_code": "6320",
                    "reasoning_justification": f"{WEAK_MATCH}: account description mentions business"},
                   {"description": "Copy paper", "amount": 20.0, "predicted_gl_code": "6000",
                    "reasoning_justification": f"{MODEL_SUGGESTED}: office supplies"},
                   {"description": "Toner", "amount": 30.0, "predicted_gl_code": "6000",
                    "reasoning_justification": "Approved 3 times for this vendor"}])  # fmt: skip
    iid = store.add_invoice(tmp_path / "a.pdf", ai, {"requires_review": True})
    counts = store.approve_invoice(iid, ai, "Pat")
    # B-475: a kept guess is the reviewer's own coding: neither confirmed nor corrected, out of the accuracy
    assert counts == {ACCEPTED: 1, CORRECTED: 0, CODED: 2}
    m = store.metrics()
    assert m["line_accuracy"] == 1.0 and m["lines_measured"] == 1 and m["top_corrections"] == []
    from ap_coder.core.audit import _approved_text

    (event,) = [e for e in store.events() if e["action"] == "approved"]
    assert _approved_text(event["detail"]) == (
        "3 line(s): 2 coded by the reviewer (the AI only guessed), the others as the AI suggested"
    )
    from ap_coder.ai.lessons import outcomes

    changed = {**ai, "line_items": [dict(li, predicted_gl_code="6400") for li in ai["line_items"]]}
    assert [r["proposed"] for r in outcomes(ai, changed) if r["field"] == "line_gl"] == ["6000"]  # the learned one only


def test_the_review_screen_marks_a_guess_and_does_not_call_it_as_suggested(tmp_path):
    store = Store(tmp_path / "ap.db")
    load_sample_setup(store)
    ai = _invoice([{"description": "Copy paper", "amount": 20.0, "predicted_gl_code": "6000",
                    "predicted_cost_center": "CC200",
                    "reasoning_justification": f"{MODEL_SUGGESTED}: office supplies"}])  # fmt: skip
    iid = store.add_invoice(tmp_path / "a.pdf", ai, {"requires_review": True})
    page = client_for(store).get(f"/review/{iid}").text
    assert "is-guess" in page and "A guess, not learned yet: check it" in page
    assert "all as suggested" not in page and "Teaches from 1 line: 1 guessed account to check" in page


# --- B-466: Month-end -----------------------------------------------------------------------------------------------


def test_month_end_accrues_an_unapproved_invoice_as_proposed_and_never_on_a_guess(tmp_path):
    store = Store(tmp_path / "ap.db")
    load_sample_setup(store)
    ai = _invoice([{"description": "Google Workspace", "amount": 10.0, "predicted_gl_code": "6320",
                    "reasoning_justification": f"{WEAK_MATCH}: account description mentions business"},
                   {"description": "Toner", "amount": 30.0, "predicted_gl_code": "6000",
                    "reasoning_justification": "Approved 3 times for this vendor"}])  # fmt: skip
    store.add_invoice(tmp_path / "a.pdf", ai, {"requires_review": True})
    items = [a for a in accruals.build(store, dt.date(2026, 9, 30)) if a.source == accruals.NOT_IN_ERP]
    assert {(a.gl_code, a.amount) for a in items} == {(UNASSIGNED, 10.0), ("6000", 30.0)}
    assert all(accruals.PROPOSED in a.note for a in items)
    page = client_for(store).get("/month-end?end=2026-09-30").text
    assert "Proposed" in page and "Needs an account" in page


def test_month_end_flags_an_invoice_dated_over_a_year_back_and_lists_one_with_no_date(tmp_path):
    """B-466 left out an invoice dated more than a year back (AP decides: it is accrued, flagged), and B-474: an invoice
    with no date was listed as "invoice dated None"."""
    store = Store(tmp_path / "ap.db")
    load_sample_setup(store)
    line = {"description": "Audit", "amount": 500.0, "predicted_gl_code": "6100", "reasoning_justification": "x"}
    store.add_invoice(tmp_path / "a.pdf", _invoice([line], invoice_date="2012-12-01"), {"requires_review": True})
    store.add_invoice(tmp_path / "b.pdf", _invoice([line], invoice_date="", invoice_number="B-2"), {})
    end = dt.date(2026, 9, 30)
    (old,) = accruals.build(store, end)
    assert old.old and old.amount == 500.0 and accruals.OLD_NOTE in old.note
    assert accruals.OLD_NOTE in accruals.to_csv([old], end).decode("utf-8-sig")
    no_date: list = []
    accruals.build(store, end, no_date)
    (undated,) = no_date
    assert "B-2" in undated.reference and "None" not in undated.description
    page = html.unescape(client_for(store).get("/month-end?end=2026-09-30").text)
    assert "1 invoice dated more than a year before 2026-09-30: 500.00 CAD included" in page and "Check date" in page
    assert "No invoice date: can't be accrued until it has one" in page and "dated None" not in page


def test_month_end_groups_an_account_in_two_currencies_on_one_row(tmp_path):
    store = Store(tmp_path / "ap.db")
    load_sample_setup(store)
    line = {"description": "Hosting", "amount": 100.0, "predicted_gl_code": "6400", "reasoning_justification": "x"}
    store.add_invoice(tmp_path / "a.pdf", _invoice([line]), {"requires_review": True})
    store.add_invoice(tmp_path / "b.pdf", _invoice([line], currency="USD", invoice_number="B-2"), {})
    currencies, rows = accruals.by_gl_columns(accruals.build(store, dt.date(2026, 9, 30)))
    assert currencies == ["CAD", "USD"] and rows == [("6400", {"CAD": 100.0, "USD": 100.0})]
    page = client_for(store).get("/month-end?end=2026-09-30").text
    assert "Accrue CAD" in page and "Accrue USD" in page


def test_month_end_with_nothing_imported_says_so(tmp_path):
    page = client_for(Store(tmp_path / "ap.db")).get("/month-end").text
    assert "already in the ERP" not in page and "No invoices or purchase orders yet" in page


# --- B-464: plain English where the reviewer reads ------------------------------------------------------------------


def test_the_checks_say_what_is_missing_in_plain_words_and_keep_their_codes_for_a_hover(tmp_path):
    from ap_coder.coding.validation import validate_coding
    from ap_coder.core import ui

    store = Store(tmp_path / "ap.db")
    load_sample_setup(store)
    doc = _invoice([{"description": "Audit", "amount": 50.0, "predicted_gl_code": "9999",
                     "reasoning_justification": "x"}], vendor_name="", invoice_number="")  # fmt: skip
    doc["subtotal"] = 60.0
    messages = {i.code: i.message for i in validate_coding(InvoiceCoding.model_validate(doc), store.reference_data())
                .issues}  # fmt: skip
    assert messages["MISSING_VENDOR"] == "No vendor name: type it from the page"
    assert messages["MISSING_INVOICE_NUMBER"] == "No invoice number: type it from the page"
    assert messages["GL_UNKNOWN"] == "GL account 9999 is not in your GL accounts: pick one from the list"
    assert messages["SUBTOTAL_MISMATCH"] == "the lines add up to 50.00 but the subtotal is 60.00"
    assert not [m for m in messages.values() if "_" in m or "'" in m]  # no code names, no quoted codes
    shown = ui.check("info", "Good to know", "First invoice from this vendor", "VENDOR_NEW")
    assert "title='Check VENDOR_NEW'" in shown and ">VENDOR_NEW<" not in shown


def test_a_lesson_on_a_blank_account_reads_left_blank_not_unassigned(tmp_path):
    from ap_coder.ai.lessons import sentence
    from ap_coder.core.audit import _approved_text

    text = sentence({"vendor_name": "Acme", "field": "invoice_number", "value": "A-1", "proposed": UNASSIGNED})
    assert UNASSIGNED not in text
    change = _approved_text({"changes": [{"what": "line 1 GL", "before": UNASSIGNED, "after": "6110"}]})
    assert change == "1 change(s): line 1 GL: blank → 6110"


# --- B-465: what keeps Approve closed is grouped first ----------------------------------------------------------------


def test_what_keeps_approve_closed_is_its_own_group_before_the_errors_and_warnings():
    from ap_coder.coding.validation import ERROR, WARNING, Issue
    from ap_coder.web.invoice_view import checks_details, checks_summary

    errors = [Issue(ERROR, "TOTAL_MISMATCH", "subtotal + tax = 10.00 but the total is 12.00")]
    warnings = [Issue(WARNING, "GL_UNASSIGNED", "no GL account yet: pick one", 1),
                Issue(WARNING, "INVOICE_DATE_NOT_FOUND", "no invoice date found: check it")]  # fmt: skip
    report = SimpleNamespace(requires_review=True, adjusted_confidence=0.5, review_threshold=0.85,
                             issues=errors + warnings, checks={})  # fmt: skip
    c = checks_summary(report, errors, warnings)
    assert [g["title"] for g in c["groups"]] == ["Before you can approve (1)", "Must be fixed (1)",
                                                 "Check before approving (1)"]  # fmt: skip
    assert c["groups"][0]["items"][0]["goto"] == {"box": "line_gl", "line": 1} and "G" in c["sub"]
    titles = {d["code"]: d["title"] for d in checks_details(report)}
    assert titles["GL_UNASSIGNED"] == "Before you can approve" and titles["INVOICE_DATE_NOT_FOUND"] == (
        "Check before approving"
    )


# --- B-468: the demo before the first real import ---------------------------------------------------------------------


def test_the_import_page_asks_to_remove_the_demo_before_the_first_real_invoice(tmp_path, demo_template):

    store = copy_database(demo_template, tmp_path / "private" / "ap.db")
    client = client_for(store)
    assert "Remove the demo invoices before you import your own" in client.get("/process").text
    line = {"description": "Audit", "amount": 50.0, "predicted_gl_code": "6100", "reasoning_justification": "x"}
    store.add_invoice(tmp_path / "real.pdf", _invoice([line]), {"requires_review": True})
    assert "Remove the demo invoices before you import your own" not in client.get("/process").text


# --- B-469: long overdue, and an invoice dated more than a year back --------------------------------------------------


def test_a_long_overdue_bill_says_since_when_and_an_old_invoice_date_is_flagged():
    from ap_coder.coding.terms import describe, payment, payment_findings

    old = {"invoice_date": "2012-11-01", "due_date": "2012-12-01", "grand_total": 100.0}
    today = dt.date(2026, 10, 21)
    assert describe(payment(old), today) == "Overdue since Dec 2012"
    found = {code: (severity, message) for severity, code, message in payment_findings(old, today=today)}
    assert found["PAYMENT_OVERDUE"][1] == "due 2012-12-01 (overdue since Dec 2012): late fees may apply"
    assert found["OLD_INVOICE_DATE"][0] == "info"  # B-476: information only, it holds nothing back
    from ap_coder.web.pages.review import due_note

    assert due_note(old, 30) == ("Overdue since Dec 2012", "err")  # the queue's row too
    recent = {"invoice_date": "2026-10-01", "due_date": "2026-10-11", "grand_total": 100.0}
    assert describe(payment(recent), today) == "Overdue by 10 days (Oct 11)"
    assert "OLD_INVOICE_DATE" not in {c for _, c, _ in payment_findings(recent, today=today)}


# --- B-470: the Excel export's dates are dates ------------------------------------------------------------------------


def test_the_excel_export_writes_real_dates():
    import io

    from openpyxl import load_workbook

    from ap_coder.erp.exports import build_xlsx

    final = {**_invoice([{"description": "Audit", "amount": 50.0, "predicted_gl_code": "6100",
                          "reasoning_justification": "x"}]), "due_date": "2026-10-10"}  # fmt: skip
    inv = {"id": 1, "final_output": final, "reviewer": "Pat", "reviewed_at": "2026-10-02T14:05:09",
           "file_name": "a.pdf"}  # fmt: skip
    wb = load_workbook(io.BytesIO(build_xlsx([inv])))
    head = [c.value for c in wb["Invoices"][1]]
    row = {h: c for h, c in zip(head, wb["Invoices"][2], strict=True)}
    assert row["Invoice date"].value == dt.datetime(2026, 9, 10) and row["Invoice date"].number_format == "yyyy-mm-dd"
    assert row["Due date"].value == dt.datetime(2026, 10, 10)
    assert row["Approved at"].value == dt.datetime(2026, 10, 2, 14, 5, 9)
    lines = wb["GL lines"]
    head = [c.value for c in lines[1]]
    assert lines[2][head.index("Invoice date")].value == dt.datetime(2026, 9, 10)


# --- B-471: the queue never shows the file's name as the vendor -------------------------------------------------------


def test_the_queue_says_no_vendor_name_and_marks_a_name_to_check(tmp_path):
    store = Store(tmp_path / "ap.db")
    load_sample_setup(store)
    line = {"description": "Audit", "amount": 50.0, "predicted_gl_code": "6100", "reasoning_justification": "x"}
    store.add_invoice(tmp_path / "scan_0042.pdf", _invoice([line], vendor_name=""), {"requires_review": True})
    unsure = store.add_invoice(tmp_path / "b.pdf", _invoice([line], vendor_name="Inc."), {"requires_review": True})
    store.save_capture(unsure, {"fields": {"vendor_name": {"field": "vendor_name", "value": "Inc.", "status": "check",
                                                            "confidence": 0.3}}})  # fmt: skip
    page = client_for(store).get("/review").text
    assert "No vendor name" in page and '<div class="name" title="scan_0042.pdf' not in page
    assert '<span class="rq-unsure">Inc.?</span>' in page and "Vendor name: check it" in page


# --- B-473: the approve bar says what approving teaches --------------------------------------------------------------


def test_the_approve_bar_says_lines_to_code_coded_by_you_or_kept_as_suggested(tmp_path):
    """BDO's only line had no GL account yet and the bar said "Teaches from 1 line · all as suggested"."""
    from ap_coder.web.invoice_view import learn_text

    store = Store(tmp_path / "ap.db")
    load_sample_setup(store)
    blank = {"description": "Disbursements", "amount": 50.0, "predicted_gl_code": UNASSIGNED,
             "reasoning_justification": "No earlier approval to learn from: pick the account."}  # fmt: skip
    iid = store.add_invoice(tmp_path / "a.pdf", _invoice([blank]), {"requires_review": True})
    page = client_for(store).get(f"/review/{iid}").text
    assert "1 line to code" in page and "all as suggested" not in page and "Teaches from" not in page

    learned = {"description": "Toner", "amount": 30.0, "predicted_gl_code": "6000",
               "reasoning_justification": "Approved 3 times for this vendor"}  # fmt: skip
    guess = {"description": "Paper", "amount": 20.0, "predicted_gl_code": "6000",
             "reasoning_justification": f"{MODEL_SUGGESTED}: office supplies"}  # fmt: skip
    ai = _invoice([blank, learned, dict(learned, description="Ink"), guess])

    def bar(*gls):
        doc = _invoice([dict(li, predicted_gl_code=gl) for li, gl in zip(ai["line_items"], gls, strict=True)])
        return learn_text(InvoiceCoding.model_validate(doc), ai)

    assert bar(UNASSIGNED, "6000", "6000", "6000") == (
        "1 line to code · Teaches from 3 lines: 2 as suggested, 1 guessed account to check"
    )
    assert bar("6100", "6000", "6400", "6010") == "Teaches from 4 lines: 2 you coded, 1 you changed, 1 as suggested"


# --- B-475: a rule's or the reviewer's account is no guess; a guess is out of the accuracy ----------------------------


def test_a_coding_rule_over_a_guess_is_the_rules_coding_not_a_guess_and_not_a_correction(tmp_path):
    """The review: a fixed coding rule that replaced the chat model's guess kept the model's reasoning: the line was
    marked a guess, the bar said "1 guessed account to check", and the approval recorded the model's guess as the
    AI's proposal, corrected ("6010" -> "6000")."""
    from ap_coder.coding import rules
    from ap_coder.web.invoice_view import learn_text

    store = Store(tmp_path / "ap.db")
    load_sample_setup(store)
    ai = _invoice([{"description": "Copy paper", "amount": 20.0, "predicted_gl_code": "6010",
                    "reasoning_justification": f"{MODEL_SUGGESTED}: office"}])  # fmt: skip
    rule = rules.Rule(vendor="Acme Supply", contains="paper", gl_code="6000")
    coded, changes = rules.apply(InvoiceCoding.model_validate(ai), [rule])
    (line,) = coded.line_items
    assert line.predicted_gl_code == "6000" and not guessed(line.reasoning_justification)
    assert changes[0]["from_guess"] is True
    out = coded.to_output()
    iid = store.add_invoice(tmp_path / "a.pdf", out, {"requires_review": True}, meta={"rules_applied": changes})
    assert learn_text(coded, out, rules_applied=changes) == "Teaches from 1 line: 1 by a coding rule"
    assert store.approve_invoice(iid, out, "Pat") == {ACCEPTED: 0, CORRECTED: 0, CODED: 1}
    assert store.metrics()["top_corrections"] == []


def test_an_account_the_reviewer_picked_over_a_guess_is_never_marked_again(tmp_path):
    """The review: the hidden reasoning kept the model's text, so the mark came back on the reviewer's own account
    after the server drew the grid again (an added line, a split, a page reload)."""
    from ap_coder.web import invoice_view

    store = Store(tmp_path / "ap.db")
    load_sample_setup(store)
    ai = _invoice([{"description": "Copy paper", "amount": 20.0, "predicted_gl_code": "6000",
                    "predicted_cost_center": "CC200",
                    "reasoning_justification": f"{MODEL_SUGGESTED}: office"}])  # fmt: skip
    draft = SimpleNamespace(lines=[dict(ai["line_items"][0])], taxes=[])
    reference = store.reference_data()
    assert invoice_view.grid(reference, draft, ai)["lines"][0]["guessed"]
    draft.lines[0]["predicted_gl_code"] = "6010"  # picked by the reviewer; the hidden reasoning is still the model's
    assert not invoice_view.grid(reference, draft, ai)["lines"][0]["guessed"]


def test_the_approval_message_says_you_coded_for_a_kept_guess(tmp_path):
    store = Store(tmp_path / "ap.db")
    load_sample_setup(store)
    ai = _invoice([{"description": "Copy paper", "amount": 20.0, "predicted_gl_code": "6000",
                    "predicted_cost_center": "CC200",
                    "reasoning_justification": f"{MODEL_SUGGESTED}: office"}])  # fmt: skip
    iid = store.add_invoice(tmp_path / "a.pdf", ai, {"requires_review": True})
    client = client_for(store)
    from .test_web_invoice import form_of
    from .webclient import follow, post

    page = html.unescape(follow(client, post(client, f"/review/{iid}/approve", form_of(store, iid, lambda rows: rows))))
    assert "1 line you coded." in page or "Learned from 1 line: 1 you coded." in page
    assert "coded as suggested" not in page.replace("1 line you coded.", "")


# --- B-476: an old invoice date is a note, not a hold -----------------------------------------------------------------


def test_an_old_invoice_date_is_a_note_measured_from_the_day_it_came_in_and_not_for_a_credit_note():
    from ap_coder.coding.terms import payment_findings

    doc = {"invoice_date": "2025-06-01", "due_date": "2025-07-01", "grand_total": 100.0}
    today = dt.date(2026, 10, 21)
    codes = {c: s for s, c, _ in payment_findings(doc, today=today)}
    assert codes["OLD_INVOICE_DATE"] == "info"
    assert "OLD_INVOICE_DATE" not in {c for _, c, _ in payment_findings(doc, today=today, received=dt.date(2026, 1, 5))}
    credit = {**doc, "grand_total": -100.0}
    assert "OLD_INVOICE_DATE" not in {c for _, c, _ in payment_findings(credit, today=today)}


# --- B-477: bulk approval's reasons in plain words --------------------------------------------------------------------


def test_bulk_approval_says_why_an_invoice_was_left_without_the_check_code(tmp_path):
    from ap_coder.coding.bulk import bulk_approve

    store = Store(tmp_path / "ap.db")
    load_sample_setup(store)
    line = {"description": "Audit", "amount": 50.0, "predicted_gl_code": "6100", "reasoning_justification": "x"}
    iid = store.add_invoice(tmp_path / "a.pdf", _invoice([line]), {"requires_review": False, "issues": [
        {"severity": "warning", "code": "VENDOR_BANK_CHANGED",
         "message": "the vendor's bank details changed"}]})  # fmt: skip
    result = bulk_approve(store, store.reference_data(), Settings(), [iid], "Pat")
    ((_, why),) = result["skipped"]
    assert why == "The vendor's bank details changed" and "VENDOR_BANK_CHANGED" not in why


# --- B-479: a changed guess is an edit; the lines AP reviewed include the ones it coded ------------------------------


def test_replacing_a_guessed_account_is_an_edit_and_coded_lines_count_as_reviewed(tmp_path):
    """The re-review: a reviewer who replaced the model's guess was recorded as no edit ("no changes to the suggested
    coding", counted as approved without edits) while the audit showed "line 1 GL: 6000 → 1500"; and "Lessons
    learned" / "Lines reviewed" left out the lines AP coded over a guess, though AP Coder learns from them."""
    store = Store(tmp_path / "ap.db")
    load_sample_setup(store)
    guess = {"description": "Copy paper", "amount": 20.0, "predicted_gl_code": "6000",
             "reasoning_justification": f"{MODEL_SUGGESTED}: office"}  # fmt: skip
    learned = {"description": "Toner", "amount": 30.0, "predicted_gl_code": "6000",
               "reasoning_justification": "Approved 3 times for this vendor"}  # fmt: skip
    kept = store.add_invoice(tmp_path / "a.pdf", _invoice([guess, learned]), {"requires_review": True})
    store.approve_invoice(kept, _invoice([guess, learned]), "Pat")
    assert store.get_invoice(kept)["edits"] == []  # a kept guess is no edit
    ai = _invoice([guess, learned], invoice_number="B-2")
    changed = store.add_invoice(tmp_path / "b.pdf", ai, {"requires_review": True})
    final = _invoice([dict(guess, predicted_gl_code="1500"), learned], invoice_number="B-2")
    store.approve_invoice(changed, final, "Pat")
    assert "line_coding" in store.get_invoice(changed)["edits"]
    m = store.metrics()
    assert m["invoices_approved_without_edits"] == 1
    assert m["lines_reviewed"] == 4 and m["lines_coded"] == 2  # every line AP reviewed, the coded ones too
    assert m["line_accuracy"] == 1.0  # the accuracy still leaves the guesses out


def test_queue_accuracy_note_counts_measured_lines_not_guesses(tmp_path, monkeypatch):
    """The queue's coding accuracy is held against the target only after 50 measured lines: kept guesses are out of
    the accuracy, so they don't count towards those 50 (as on the Learning page)."""
    from ap_coder.web.pages import review

    store = Store(tmp_path / "ap.db")
    real = store.line_metrics  # the queue's counts (D-110)
    monkeypatch.setattr(store, "line_metrics", lambda: {**real(), "line_accuracy": 0.95, "lines_reviewed": 400,
                                                   "lines_measured": 10})  # fmt: skip
    s = Settings()
    accuracy = review.kpis(store, [], [], settings=replace(s, llm=replace(s.llm, provider="off")))[2]
    assert accuracy["delta"] == "" and "improve as you approve" in accuracy["sub"]


def test_without_a_cost_center_list_a_cost_center_from_memory_is_no_change(tmp_path):
    """B-562: with no cost center list the page shows (and posts) no cost center. A line the AI coded with one from
    coding memory (ERP history, approvals before the list was removed), approved untouched, is kept as suggested:
    not "1 you changed", not a correction, not an edit."""
    from ap_coder.web.invoice_view import ai_changes, learn_text

    store = Store(tmp_path / "ap.db")
    load_sample_setup(store, cost_centers=False)
    ai = _invoice([{"description": "Copy paper", "amount": 20.0, "predicted_gl_code": "6000",
                    "predicted_cost_center": "CC200",
                    "reasoning_justification": "Approved 3 times for this vendor"}])  # fmt: skip
    final = {**ai, "line_items": [{**ai["line_items"][0], "predicted_cost_center": ""}]}  # as the page posts it
    coding = InvoiceCoding.model_validate(final)
    assert ai_changes(coding, ai, cost_centers=False) == 0
    assert learn_text(coding, ai, cost_centers=False) == "Teaches from 1 line: 1 as suggested"
    iid = store.add_invoice(tmp_path / "a.pdf", ai, {"requires_review": True})
    assert store.approve_invoice(iid, final, "Pat") == {ACCEPTED: 1, CORRECTED: 0, CODED: 0}
    assert "line_coding" not in (store.get_invoice(iid)["edits"] or [])
    assert store.metrics()["line_accuracy"] == 1.0


# --- B-811: an invoice dated in the future ----------------------------------------------------------------------------


def test_an_invoice_dated_after_today_is_a_warning_that_needs_a_person():
    """Dated tomorrow: the date was misread, or the bill was sent ahead; JD Edwards would get a G/L date in the
    future. A warning, and never approved without a person. Today's date is no warning."""
    from ap_coder.capture.supplier import touchless_gates
    from ap_coder.coding.terms import payment_findings
    from ap_coder.ops.help import CHECKS

    today = dt.date(2026, 10, 23)
    ahead = {"invoice_date": "2026-10-24", "due_date": "2026-11-23", "grand_total": 100.0}
    found = {c: (s, m) for s, c, m in payment_findings(ahead, today=today)}
    assert found["FUTURE_INVOICE_DATE"][0] == "warning"
    assert "2026-10-24" in found["FUTURE_INVOICE_DATE"][1]
    assert touchless_gates([{"code": "FUTURE_INVOICE_DATE", "severity": "warning"}])
    assert "FUTURE_INVOICE_DATE" in CHECKS
    for date in ("2026-10-23", "2026-10-01"):
        doc = {**ahead, "invoice_date": date}
        assert "FUTURE_INVOICE_DATE" not in {c for _, c, _ in payment_findings(doc, today=today)}
    # Read on its day, approved later: measured from the day it is checked, no longer in the future then.
    assert "FUTURE_INVOICE_DATE" not in {c for _, c, _ in payment_findings(ahead, today=dt.date(2026, 10, 24))}
