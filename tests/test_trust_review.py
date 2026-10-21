"""Before testing: the review screen and coding (Sweep 4, B-463 on). A guess is never passed off as a learned code;
the review screen and the reports say plainly what is known and what still needs a person."""

from __future__ import annotations

import datetime as dt
from dataclasses import replace
from types import SimpleNamespace

from ap_coder.ai import inference
from ap_coder.ai.inference import MODEL_SUGGESTED
from ap_coder.ai.offline_coder import WEAK_MATCH, guessed
from ap_coder.ai.pipeline import InvoicePipeline
from ap_coder.core.config import Settings
from ap_coder.core.memory import ACCEPTED, CORRECTED
from ap_coder.core.reference_data import UNASSIGNED
from ap_coder.core.schema import InvoiceCoding
from ap_coder.core.store import Store, load_sample_setup
from ap_coder.erp import accruals

from .conftest import SAMPLES
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
    assert counts == {ACCEPTED: 1, CORRECTED: 2}  # only the learned code was "as suggested"
    assert store.metrics()["line_accuracy"] == round(1 / 3, 4)


def test_the_review_screen_marks_a_guess_and_does_not_call_it_as_suggested(tmp_path):
    store = Store(tmp_path / "ap.db")
    load_sample_setup(store)
    ai = _invoice([{"description": "Copy paper", "amount": 20.0, "predicted_gl_code": "6000",
                    "reasoning_justification": f"{MODEL_SUGGESTED}: office supplies"}])  # fmt: skip
    iid = store.add_invoice(tmp_path / "a.pdf", ai, {"requires_review": True})
    page = client_for(store).get(f"/review/{iid}").text
    assert "is-guess" in page and "A guess, not learned yet: check it" in page
    assert "all as suggested" not in page and "1 guessed account to check" in page


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


def test_month_end_lists_an_invoice_dated_over_a_year_back_instead_of_accruing_it(tmp_path):
    store = Store(tmp_path / "ap.db")
    load_sample_setup(store)
    line = {"description": "Audit", "amount": 500.0, "predicted_gl_code": "6100", "reasoning_justification": "x"}
    store.add_invoice(tmp_path / "a.pdf", _invoice([line], invoice_date="2012-12-01"), {"requires_review": True})
    end = dt.date(2026, 9, 30)
    assert not accruals.build(store, end)
    (old,) = accruals.old_dated(store, end)
    assert old.amount == 500.0 and "2012-12-01" in old.description
    page = client_for(store).get("/month-end?end=2026-09-30").text
    assert "Not accrued: dated more than a year before" in page and "already in the ERP" not in page


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
