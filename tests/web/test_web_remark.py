"""A line's remark on the review screen's form (D-213), on the made-up company (``chart_company``: a chart, last
year's journal, JD Edwards keyed by hand with the remarks as typed): the form's ``line_remark`` box is taken when it
is posted and each line keeps its remark when it is not; a suggestion follows the line's account, a typed remark is
a person's and stays, learned on approval; one longer than JD Edwards keeps holds the approval back while the remarks
go there as typed."""

from dataclasses import replace

from ap_coder.ai.supplier_prior import prior_for
from ap_coder.coding.remark import suggest_remark, suggested
from ap_coder.core.memory import vendor_key
from ap_coder.core.store import APPROVED
from ap_coder.erp import jde
from ap_coder.web.invoice_view import LINE_REMARK
from tests.review_page import ReviewPage
from tests.webclient import PAGE


def _open(client, chart_company, name="one_line"):
    """The invoice's review screen with its line on the account AP keys it to; the line as it was proposed; and the
    remark suggested for it on that account (the supplier's usual one, from last year's journal)."""
    store = chart_company.store
    invoice, invoice_id = chart_company.invoices[name]
    page = ReviewPage(client, invoice_id)
    (account,) = [part.account for part in invoice.parts]
    page.set_account(1, account)
    proposed = store.get_invoice(invoice_id)["ai_output"]
    prior = prior_for(store, proposed["vendor_name"], always=True)
    usual = suggested(proposed["line_items"][0], proposed, prior, account, store.reference_data())
    assert usual.source == "supplier" and 0 < len(usual.text) <= 30
    return page, proposed["line_items"][0], usual


def _post(page, action, remarks):
    """The screen's form posted with a remark box a line, as the page will post it."""
    answer = page.client.post(f"/review/{page.id}/{action}", data={**page.form, LINE_REMARK: remarks}, headers=PAGE)
    assert answer.status_code in (200, 400, 403, 409), answer.text
    page.open()
    return answer.json()


def _line(store, invoice_id):
    stored = store.get_invoice(invoice_id)
    assert stored["status"] == APPROVED
    (li,) = stored["final_output"]["line_items"]
    return li["remark"], li.get("remark_from")


def test_an_invoice_read_has_a_remark_a_line_and_the_suggestion_follows_the_account(
        chart_company, chart_company_client):  # fmt: skip
    store = chart_company.store
    for _, invoice_id in chart_company.invoices.values():
        for li in store.get_invoice(invoice_id)["ai_output"]["line_items"]:
            assert 0 < len(li["remark"]) <= 30 and li["remark_from"] in ("supplier", "invoice", "account")
    page, was, usual = _open(chart_company_client, chart_company)
    assert page.form[LINE_REMARK] == [""]  # the box shows the suggestion and posts none: it follows the account
    assert page.check()["allow"], page.findings
    assert page.approve()["ok"]
    assert _line(store, page.id) == (usual.text, "supplier")


def test_the_box_left_as_suggested_is_still_a_suggestion(chart_company, chart_company_client):
    """Posted as the line had it, or as the suggestion for the account it has now: not a person's remark."""
    store = chart_company.store
    page, was, usual = _open(chart_company_client, chart_company)
    assert _post(page, "check", [was["remark"]])["allow"], page.findings
    assert _post(page, "approve", [usual.text])["ok"]
    assert _line(store, page.id) == (usual.text, "supplier")


def test_a_typed_remark_is_a_persons_kept_by_the_draft_learned_on_approval(chart_company, chart_company_client):
    store = chart_company.store
    page, was, usual = _open(chart_company_client, chart_company)
    typed = "KT parcels 3-1-204"
    assert typed not in (was["remark"], usual.text)
    assert _post(page, "check", [f"  {typed} "])["allow"], page.findings
    assert page.approve()["ok"]  # (the page posts what the draft kept: the remark typed, on the account changed)
    assert _line(store, page.id) == (typed, "person")
    # Learned with the line: the supplier's next invoice on that account is suggested it.
    vendor = store.get_invoice(page.id)["final_output"]["vendor_name"]
    learned = store.approved_coding(vendor_key(vendor))["remarks"]
    assert [(r["final_remark"], r["remark_from"]) for r in learned] == [(typed, "person")]
    account = chart_company.invoices["one_line"][0].parts[0].account
    text, why = suggest_remark({"description": "Another one"}, {"invoice_date": "2026-06-02"},
                               prior_for(store, vendor, always=True), account)  # fmt: skip
    assert text == typed and why.startswith("the remark AP typed for this supplier's account here")


def test_a_remark_over_30_characters_keeps_approve_closed_while_remarks_go_to_jd_edwards_as_typed(
        chart_company, chart_company_client):  # fmt: skip
    store = chart_company.store
    assert jde.load(store).remarks == jde.REMARKS_TYPED
    page, _, _ = _open(chart_company_client, chart_company)
    long = "coffee supplies for the 3rd floor kitchen"
    answer = _post(page, "check", [long])
    said = page.findings.get("REMARK_LONG", "")
    assert not answer["allow"] and "41 characters" in said and "coffee supplies for the 3rd" in said
    assert not page.approve().get("ok")
    # Where the remarks sent are AP Coder's references it is a warning: approved, kept whole, keyed fitted.
    jde.save(store, replace(jde.load(store), remarks=jde.REMARKS_REFERENCE))
    assert _post(page, "check", [long])["allow"], page.findings
    assert page.approve()["ok"]
    assert _line(store, page.id) == (long, "person")
    assert jde.line_remark(store.get_invoice(page.id)["final_output"]["line_items"][0]) == "coffee supplies for the 3rd"
