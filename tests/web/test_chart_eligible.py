"""The whole chart is kept, only the accounts AP may post to are ever offered or approved (D-272), on the made-up
company (``chart_company``): an account whose posting edit code is not one AP posts to (a code not decided yet among
them) is never suggested, never in the Account box, flagged when a person, a coding rule or the PO's coding puts it on
a line, and refused at the first approval, in bulk and at the second; a business unit missing from the business units
report is an error approved anyway only with a reason, kept with who and when; an invoice of a company outside the
pilot is held whoever approves it."""

from __future__ import annotations

import dataclasses
import json

from ap_coder.coding import companies
from ap_coder.coding.bulk import bulk_approve, needs_a_person
from ap_coder.coding.validation import account_problem
from ap_coder.core import chart
from ap_coder.core.store import APPROVED, PENDING, REVIEW
from tests.ai.test_supplier_prior import _account, _coded, _courier
from tests.review_page import ReviewPage
from tests.webclient import PAGE, follow, post, web_settings

SECOND = "Kim"  # who gives the second approval (the first: "Sam")


def _set_edit(store, account: str, edit: str) -> None:
    """The chart loaded again with ``account``'s posting edit code ``edit``, as a refreshed export would have it."""
    bu, code = chart.split_account(account)
    obj, sub = chart.split_code(code)
    rows = [dataclasses.replace(r, posting_edit=edit) if (r.bu, r.object, r.subsidiary) == (bu, obj, sub) else r
            for r in store.chart_accounts()]  # fmt: skip
    store.import_chart(rows)


def _one_line(chart_company, client):
    """The invoice of the supplier always coded to one account (the courier's), and its account."""
    invoice, invoice_id = chart_company.invoices["one_line"]
    return ReviewPage(client, invoice_id), invoice.parts[0].account


def _box(client, **asked) -> list[str]:
    """The account strings the Account box gets: the list whole, a search (``q``) or a unit's (``unit``)."""
    return [row[0] for row in client.get("/review/accounts.json", params=asked).json()["list"]]


def _two_companies(store) -> None:
    """JD Edwards' companies (report 9): the made-up company's and another."""
    store.set_setting(companies.COMPANIES_SETTING, json.dumps({
        "00210": {"name": "MAPLE RIDGE HOLDINGS LTD.", "currency": "CAD", "country": "CA"},
        "00310": {"name": "MAPLE RIDGE US INC.", "currency": "USD", "country": "US"}}))  # fmt: skip


def _second_approval(store, client, monkeypatch, page) -> str:
    """The second approval of ``page``'s invoice (approved first by Sam), asked by Kim on her own computer login: the
    page it leads to."""
    monkeypatch.setenv("AP_REVIEWER", SECOND)
    for name in ("LOGNAME", "USER", "LNAME", "USERNAME"):  # (``getpass.getuser``: the login recorded)
        monkeypatch.setenv(name, "kim")
    return follow(client, post(client, f"/review/{page.id}/final-approve",
                               {"approval": store.latest_approval_id(page.id)}))  # fmt: skip


def _first_approval(store, monkeypatch, page, anyway: str = "") -> None:
    """``page``'s invoice approved by Sam over the approval limit: it waits for a second approval."""
    store.set_setting("approval_limit", "1")
    monkeypatch.setenv("AP_REVIEWER", "Sam")
    assert page.approve(anyway=anyway).get("ok") is not False
    assert store.get_invoice(page.id)["status"] == PENDING


# --- An account AP does not post to -------------------------------------------------------------------------------


def test_an_account_no_longer_postable_is_never_suggested_nor_in_the_account_box(chart_company, chart_company_client):
    """The courier's one account, refreshed with a posting edit code AP Coder does not know (Z): not yet decided, so
    closed. Its lines are no longer coded to it, nor offered it; the box's list, search and business unit leave it
    out; the account is still in the chart, with its description."""
    store, client = chart_company.store, chart_company_client
    vendor, courier = _courier(chart_company)
    assert _account(_coded(chart_company, vendor, "Courier delivery").line_items[0]) == courier
    bu, code = chart.split_account(courier)
    assert courier in _box(client) and courier in _box(client, q=code.replace(".", " "), unit="")
    _set_edit(store, courier, "Z")
    line = _coded(chart_company, vendor, "Courier delivery").line_items[0]
    assert _account(line) != courier and courier not in [c["account"] for c in line.candidates or []]
    assert courier not in _box(client) and courier not in _box(client, q=code.replace(".", " "))
    assert courier not in _box(client, unit=bu)
    pairs = store.account_pairs()
    assert pairs.status(bu, code) == chart.NOT_POSTABLE and pairs.description(bu, code)
    assert pairs.settings.undecided(["Z"]) == ["Z"]


def test_a_person_a_coding_rule_or_the_pos_coding_putting_it_on_a_line_is_flagged_and_never_approved(
    chart_company, chart_company_client
):
    store, client = chart_company.store, chart_company_client
    page, courier = _one_line(chart_company, client)
    _set_edit(store, courier, "Z")
    bu, code = chart.split_account(courier)
    # A fixed coding rule may not code to it.
    problem = account_problem(store.reference_data(), code, bu)
    assert "cannot be posted to (its posting edit code Z is not decided yet" in problem
    # The PO's coding: the courier's open PO line on the account; used, the line is flagged, never quietly kept.
    invoice, _ = chart_company.invoices["one_line"]
    line = invoice.lines[0]
    store.import_purchase_orders([{
        "po_number": "45120", "vendor_name": "", "supplier_number": str(invoice.supplier.number), "order_type": "OP",
        "order_company": "00210", "line_number": 1, "erp_line": "1.000", "description": line.description,
        "quantity": float(line.quantity), "unit_price": float(line.unit_price), "amount": float(line.amount),
        "open_amount": None, "open_quantity": float(line.quantity), "uom": "EA", "received_quantity": None,
        "gl_code": code, "cost_center": bu, "status": "open", "tax_flag": "", "tax_code": "", "tax_area": "",
        "currency": "CAD"}])  # fmt: skip
    form = {**page.form, "po_number": "45120"}
    other = next(a for a in chart_company.truth.suggestable() if a.string != courier and a.business_unit == bu)
    form["line_account"] = [other.string] + form["line_account"][1:]
    answer = client.post(f"/review/{page.id}/po-coding", data=form, headers=PAGE).json()
    assert answer["ok"]
    page.open()
    assert page.lines[0].account == courier
    checked = page.check()
    assert not checked["allow"] and "ACCOUNT_NOT_POSTABLE" in page.findings
    # By hand, and with Approve anyway: refused (an error it never covers).
    page.set_account(1, courier)
    assert not page.check()["allow"] and list(page.findings) == ["ACCOUNT_NOT_POSTABLE"]
    assert page.approve(anyway="the courier says so")["ok"] is False
    assert store.get_invoice(page.id)["status"] == REVIEW


def test_an_approval_on_an_account_closed_since_is_refused_at_the_second_approval(
    chart_company, chart_company_client, monkeypatch
):
    """Approved first on the courier's account, which a chart refreshed since closes (its code now N): the second
    approval checks it again and refuses it (D-257)."""
    store, client = chart_company.store, chart_company_client
    page, courier = _one_line(chart_company, client)
    _first_approval(store, monkeypatch, page)
    _set_edit(store, courier, "N")
    shown = _second_approval(store, client, monkeypatch, page)
    assert "Not done: checked again now, this invoice has to be fixed first" in shown
    assert store.get_invoice(page.id)["status"] == PENDING


def test_bulk_approval_skips_an_invoice_coded_to_an_account_closed_since(chart_company, chart_company_client):
    store = chart_company.store
    page, courier = _one_line(chart_company, chart_company_client)
    _set_edit(store, courier, "N")
    result = bulk_approve(store, store.reference_data(), web_settings(), [page.id], "Sam")
    assert result["approved"] == [] and [i for i, _why in result["skipped"]] == [page.id]
    assert store.get_invoice(page.id)["status"] == REVIEW


# --- A business unit missing from the business units report -------------------------------------------------------


def _without_unit(chart_company, unit: str) -> None:
    """The business units report loaded again without ``unit`` (a job opened in JD Edwards since it was run)."""
    store = chart_company.store
    store.import_business_units([u for u in store.business_units() if u.code != unit], whole=True)


def test_a_line_on_a_business_unit_missing_from_report_2_is_approved_only_anyway_with_a_reason_kept(
    chart_company, chart_company_client, monkeypatch
):
    """Its accounts are never offered; a line coded to one is an error (not one Approve anyway never covers): approved
    with a reason by someone who may approve, the reason, who and when kept in the invoice's history and the activity
    log, and the second approval passes it as approved over."""
    store, client = chart_company.store, chart_company_client
    page, courier = _one_line(chart_company, client)
    bu, _code = chart.split_account(courier)
    _without_unit(chart_company, bu)
    assert not [a for a in _box(client) if a.startswith(f"{bu}.")] and _box(client, unit=bu) == []
    checked = page.check()
    assert not checked["allow"] and "BU_NOT_IN_UNITS_REPORT" in page.findings
    assert "is not in the business units report" in page.findings["BU_NOT_IN_UNITS_REPORT"]
    assert page.approve()["ok"] is False
    reason = "a job opened in JD Edwards this week: confirmed with the project manager"
    _first_approval(store, monkeypatch, page, anyway=reason)
    first = store.events(page.id, ["approved"])[0]
    over = [i for i in first["detail"]["open_issues"] if i["code"] == "BU_NOT_IN_UNITS_REPORT"]
    assert over and all(i["override_reason"] == reason for i in over) and first["actor"] == "Sam"
    assert first["created_at"]
    assert reason in client.get("/activity").text
    shown = _second_approval(store, client, monkeypatch, page)
    assert store.get_invoice(page.id)["status"] == APPROVED, shown[shown.find("Not done") :][:300]
    assert store.get_invoice(page.id)["second_reviewer"] == SECOND


# --- Companies in the pilot ---------------------------------------------------------------------------------------


def _pilot(store, client, companies_in: str) -> None:
    """Settings → JD Edwards E1 → Accounts AP posts to: the companies in the pilot saved, the rest as it is."""
    kept = store.chart_settings().saved()
    answer = client.post("/settings/jde/posting", data={"code": ["L", "U"], "other_codes": "", "never_offered":
                                                        kept["never_offered"], "pilot_companies": companies_in},
                         headers=PAGE)  # fmt: skip
    assert answer.status_code == 200, answer.text


def test_an_invoice_of_a_company_outside_the_pilot_is_held_whoever_approves_it(
    chart_company, chart_company_client, monkeypatch
):
    """With 00310 alone in the pilot, the courier's invoice (company 00210, its only company billed) is an error
    Approve anyway never covers: not approved by a person, in bulk, or at the second approval of one approved before
    the pilot was set; the change of setting is in the activity log. Its company added, it is approved."""
    store, client = chart_company.store, chart_company_client
    _two_companies(store)
    page, courier = _one_line(chart_company, client)
    waiting = ReviewPage(client, chart_company.invoices["quebec"][1])
    _first_approval(store, monkeypatch, waiting)
    _pilot(store, client, "310")
    assert store.chart_settings().pilot == ("00310",)
    changed = store.events(actions=["settings_changed"])[0]
    assert changed["detail"]["keys"] == ["pilot_companies"] and changed["actor"]
    assert not page.check()["allow"] and "COMPANY_NOT_IN_PILOT" in page.findings
    said = page.findings["COMPANY_NOT_IN_PILOT"]
    assert "Company 00210 MAPLE RIDGE HOLDINGS LTD. is not in the pilot" in said and "can't be approved here" in said
    assert page.approve(anyway="it is ours")["ok"] is False
    result = bulk_approve(store, store.reference_data(), web_settings(), [page.id], "Sam")
    assert result["approved"] == [] and store.get_invoice(page.id)["status"] == REVIEW
    shown = _second_approval(store, client, monkeypatch, waiting)
    assert "Not done: checked again now" in shown and store.get_invoice(waiting.id)["status"] == PENDING
    # The pilot's companies' accounts alone are offered: none of 00210's.
    assert _box(client, q="courier") == [] and courier not in _box(client, q=chart.split_account(courier)[1])
    _pilot(store, client, "00210, 00310")
    assert page.check()["allow"] and page.approve()["ok"] is not False  # (approved: a second approval asked, as the
    assert store.get_invoice(page.id)["status"] == PENDING  # approval limit was 1.00 since the invoice came in, B-970)


def test_an_invoice_outside_the_pilot_is_never_touchless_nor_left_out_of_a_persons_queue():
    from ap_coder.capture.supplier import touchless_gates

    found = [{"severity": "error", "code": "COMPANY_NOT_IN_PILOT"}]
    assert touchless_gates(found) == ["its company is not in the pilot"]
    assert needs_a_person("error", "COMPANY_NOT_IN_PILOT") and needs_a_person("error", "BU_NOT_IN_UNITS_REPORT")


def test_with_one_company_an_invoice_is_held_when_settings_company_is_not_in_the_pilot(
    chart_company, chart_company_client, monkeypatch
):
    """B-2683: without JD Edwards' companies (report 9 not loaded yet, or one company), every voucher is keyed under
    Settings → JD Edwards E1's company, and the pilot was never asked of it: with 00310 alone in the pilot, the
    made-up company's invoice (Settings' company 00210) was approved in one click over a warning, outside the pilot.
    It is held as with several companies, whoever approves it; the company added to the pilot, it is approved."""
    store, client = chart_company.store, chart_company_client
    page, _courier = _one_line(chart_company, client)
    _pilot(store, client, "310")
    assert not page.check()["allow"] and "COMPANY_NOT_IN_PILOT" in page.findings
    said = page.findings["COMPANY_NOT_IN_PILOT"]
    assert "Company 00210 is not in the pilot" in said and "Settings → JD Edwards E1" in said
    assert page.approve(anyway="it is ours")["ok"] is False and store.get_invoice(page.id)["status"] == REVIEW
    result = bulk_approve(store, store.reference_data(), web_settings(), [page.id], "Sam")
    assert result["approved"] == [] and store.get_invoice(page.id)["status"] == REVIEW
    # a company the coding has, kept from when several were known: its voucher is keyed under it, so it is asked
    _pilot(store, client, "210")
    kept = client.post(f"/review/{page.id}/check", data={**page.form, "company": "00410"}, headers=PAGE).json()
    assert not kept["allow"] and "Company 00410 is not in the pilot" in kept["sections"]["checks"]
    assert client.post(f"/review/{page.id}/check", data={**page.form, "company": ""}, headers=PAGE).json()["allow"]
    _pilot(store, client, "00210, 00310")
    assert page.check()["allow"] and page.approve()["ok"] is not False
    assert store.get_invoice(page.id)["status"] == APPROVED
