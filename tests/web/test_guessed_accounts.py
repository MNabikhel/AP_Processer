"""Approve is not open in one click while every account of the invoice is a guess (B-2218): the approve route
answers "N accounts are still guesses: look at them", and "Approve anyway" with its reason (the form's ``override``
and ``override_reason``), or a person changing an account, passes it. Server side: the screen's markup is not read
beyond the form (``tests/review_page.py``)."""

import json

from ap_coder.core import audit
from ap_coder.core.store import APPROVED, REVIEW
from tests.review_page import ANYWAY, ANYWAY_REASON, ReviewPage

GUESSES = ("{n} accounts are still guesses: look at them. Nothing AP approved, keyed last year or set as a rule backs "
           "any of them; change the wrong ones, or tick “Approve anyway” and say why to keep them")  # fmt: skip


def _guessed(chart_company, name: str) -> int:
    """The invoice ``name`` of the made-up company as if the chat model had chosen every account."""
    invoice, invoice_id = chart_company.invoices[name]
    store = chart_company.store
    output = store.get_invoice(invoice_id)["ai_output"]
    for line, part in zip(output["line_items"], invoice.parts, strict=True):
        unit, _, code = part.account.partition(".")
        line.update(predicted_cost_center=unit, predicted_gl_code=code, decided_by={"source": "model"},
                    candidates=[])  # fmt: skip
    with store._conn() as conn:
        conn.execute("UPDATE invoices SET ai_output = ?, final_output = NULL WHERE id = ?",
                     (json.dumps(output), invoice_id))  # fmt: skip
    return invoice_id


def test_approve_asks_for_a_look_while_every_account_is_a_guess(chart_company, chart_company_client):
    invoice_id = _guessed(chart_company, "quebec")
    page = ReviewPage(chart_company_client, invoice_id)
    assert page.findings["ACCOUNTS_ALL_GUESSED"].endswith(GUESSES.format(n=2))
    answer = page.approve()
    assert answer["ok"] is False and chart_company.store.get_invoice(invoice_id)["status"] == REVIEW
    assert answer["error"] == "Tick “Approve anyway” and say why to approve despite 1 error."
    # The deliberate second approval: the box ticked for these errors (``override``) with its reason.
    form = {**page.form, ANYWAY: page._anyway}
    refused = chart_company_client.post(f"/review/{invoice_id}/approve", data=form, headers={"X-AP-Coder": "1"})
    assert refused.json()["ok"] is False  # ticked without a reason: not yet
    assert page._anyway and ANYWAY_REASON == "override_reason"
    assert page.approve(anyway="looked at both: software licences")["ok"]
    store = chart_company.store
    assert store.get_invoice(invoice_id)["status"] == APPROVED
    event = store.events(invoice_id, ["approved"], limit=1)[0]  # the reason is kept with what it went over (D-75)
    assert [(i["title"], i["reason"]) for i in audit.approved_over(event["detail"])] == [
        ("Every account is a guess", "looked at both: software licences")]  # fmt: skip


def test_an_account_a_person_changed_is_no_longer_a_guess(chart_company, chart_company_client):
    invoice_id = _guessed(chart_company, "quebec")
    page = ReviewPage(chart_company_client, invoice_id)
    other = next(a.string for a in chart_company.truth.suggestable() if a.string != page.lines[0].account)
    page.set_account(1, other)
    assert page.check()["allow"] and "ACCOUNTS_ALL_GUESSED" not in page.findings
    assert page.approve()["ok"]
