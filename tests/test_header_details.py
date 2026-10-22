"""The review screen's invoice details (the header): the vendor list's tax numbers filled in where none is printed
(D-65), the details a check is about highlighted with a note, and "Pay from", our bank account (D-66)."""

# ruff: noqa: F811  (the fixtures are tests/test_web_invoice.py's, imported)
from __future__ import annotations

import html
import json

from ap_coder.coding import banks
from ap_coder.coding.validation import Issue
from ap_coder.core.schema import FROM_VENDOR_LIST
from ap_coder.core.store import APPROVED, REVIEW, Store
from ap_coder.web import invoice_view as view

from .test_web_invoice import PAGE, _open_review, browser, check, client, demo_db, form_of, me, store  # noqa: F401
from .test_web_pilot_walkthrough import LAPTOP

GST, QST, PST = "gst_hst_registration_number", "qst_registration_number", "pst_registration_number"
ACME = {"expected_gst": "123456782 RT0001", "qst_number": "1234567890 TQ0001", "pst_number": ""}
OTHER = {"expected_gst": "741852966 RT0001", "qst_number": "", "pst_number": "PST-1234-5678"}


def _header(vendor: str = "Acme", **values: str) -> dict[str, str]:
    return {"vendor_name": vendor, GST: "", QST: "", PST: "", **values}


# --- The fill rules -------------------------------------------------------------------------------------------------


def test_a_vendor_fills_the_blank_tax_numbers_only():
    header = _header(**{PST: "PST-9999-0000"})  # the PST number printed
    state, changed = view.fill_from_vendor(header, {}, [], ACME)
    assert changed == {GST: "123456782 RT0001", QST: "1234567890 TQ0001"}
    assert header[GST] == "123456782 RT0001" and header[PST] == "PST-9999-0000"  # printed: never touched
    assert state == {"vendor": "acme", "fields": {GST: "123456782 RT0001", QST: "1234567890 TQ0001"}}
    # the same vendor again: nothing to do
    assert view.fill_from_vendor(header, state, [], ACME) == (state, {})


def test_a_number_filled_from_the_vendor_follows_the_vendor():
    header = _header()
    state, _ = view.fill_from_vendor(header, {}, [], ACME)
    header["vendor_name"] = "Other Co"
    state, changed = view.fill_from_vendor(header, state, [], OTHER)
    assert changed == {GST: "741852966 RT0001", QST: "", PST: "PST-1234-5678"}  # replaced, cleared, filled
    assert state["fields"] == {GST: "741852966 RT0001", PST: "PST-1234-5678"}
    header["vendor_name"] = "Not In The List"
    state, changed = view.fill_from_vendor(header, state, [], None)
    assert changed == {GST: "", PST: ""} and state == {"vendor": "not in the list", "fields": {}}


def test_a_number_typed_or_cleared_is_the_reviewers():
    header = _header()
    state, _ = view.fill_from_vendor(header, {}, [], ACME)
    header[GST] = "999999999 RT0001"  # typed over the filled number
    header[QST] = ""  # the filled one cleared
    state, changed = view.fill_from_vendor(header, state, [], ACME)
    assert changed == {} and state["fields"] == {}  # the same vendor: not filled again
    header["vendor_name"] = "Other Co"
    state, changed = view.fill_from_vendor(header, state, [], OTHER)
    assert header[GST] == "999999999 RT0001"  # typed: never overwritten, even when the vendor changes
    assert changed == {PST: "PST-1234-5678"} and header[QST] == ""  # (OTHER has no QST number)


def test_a_field_marked_not_on_the_invoice_is_not_filled():
    header = _header()
    state, changed = view.fill_from_vendor(header, {}, [GST], ACME)
    assert GST not in changed and header[GST] == "" and changed == {QST: "1234567890 TQ0001"}


def test_the_draft_keeps_what_was_filled_and_the_stored_coding_remembers_it():
    form = {"from_vendor": json.dumps({"vendor": "acme", "fields": {GST: "1", "remit_bank_account": "x"}})}
    base = view.Draft.stored({"vendor_name": "Acme"})
    assert view.Draft.from_form(form, base).from_vendor == {"vendor": "acme", "fields": {GST: "1"}}
    assert view.Draft.from_form({"from_vendor": "not json"}, base).from_vendor == {}
    stored = view.Draft.stored({"vendor_name": "Acme", GST: "123456782 RT0001", FROM_VENDOR_LIST: [GST]})
    assert stored.from_vendor == {"vendor": "acme", "fields": {GST: "123456782 RT0001"}}


# --- Which detail a check is about ----------------------------------------------------------------------------------


def test_the_checks_about_one_detail_highlight_it(tmp_path):
    store = Store(tmp_path / "ap.db")
    store.save_vendor("acme", "Acme", expected_gst="123456782 RT0001", qst_number="1234567890 TQ0001")
    coding = type("C", (), {"vendor_name": "Acme"})()
    issues = [Issue("warning", code, "") for code in (
        "VENDOR_TAX_NUMBER_CHANGED", "GST_NUMBER_CHECK_DIGIT", "VENDOR_QST_CHANGED", "VENDOR_BANK_CHANGED",
        "VENDOR_PST_CHANGED", "AMOUNT_UNUSUAL")]  # fmt: skip
    flags = view.field_flags(issues, store, coding)
    assert flags[GST] == "Vendor list: 123456782 RT0001 · Fails the CRA check digit"
    assert flags[QST] == "Vendor list: 1234567890 TQ0001"
    assert flags["remit_bank_account"].startswith("Not the account on this vendor's approved invoices")
    assert flags[PST] == "Differs from this vendor's"  # no PST number in the list, no history: the check says more
    assert "AMOUNT_UNUSUAL" not in str(flags) and len(flags) == 4
    assert set(view.FIELD_FLAGS) >= {"GST_HST_NUMBER_FORMAT", "QST_NUMBER_FORMAT", "PAY_FROM_CURRENCY"}


def test_the_input_tax_credit_note_needs_the_tax_charged_and_100_dollars():
    from ap_coder.core.schema import InvoiceCoding

    def coding(total: float, taxes: list[str]) -> InvoiceCoding:
        return InvoiceCoding(vendor_name="Acme", invoice_number="1", invoice_date="2026-10-01", currency="CAD",
                             subtotal=total, tax_total=0, grand_total=total, confidence_score=0.9, line_items=[],
                             tax_lines=[{"tax_type": t, "province": "QC" if t == "QST" else "", "rate": 0.05,
                                         "taxable_amount": total, "tax_amount": 5} for t in taxes])  # fmt: skip

    both = [GST, QST]
    codes = [i.code for i in view.not_printed(coding(105, ["GST", "QST"]), both)]
    assert codes == ["GST_HST_NUMBER_NOT_PRINTED", "QST_NUMBER_NOT_PRINTED"]
    note = view.not_printed(coding(100, ["GST"]), both)[0]
    assert note.severity == "info" and note.message == (
        "The GST/HST number isn't printed on this invoice: CRA needs it on the invoice to claim the input tax credit "
        "for purchases of $100 or more"
    )
    assert view.not_printed(coding(99.99, ["GST"]), both) == []  # under $100: no number needed
    assert view.not_printed(coding(500, []), both) == []  # no tax charged: no credit to claim
    assert view.not_printed(coding(500, ["GST"]), [QST]) == []  # the GST/HST number was printed


# --- On the review screen -------------------------------------------------------------------------------------------


def _red_river(store: Store, printed: bool = True) -> int:
    """Red River's invoice; ``printed=False``: as if no GST/HST number had been read on it."""
    import sqlite3

    invoice_id = next(i["id"] for i in store.list_invoices(REVIEW) if i["vendor_name"].startswith("Red River"))
    if not printed:
        with sqlite3.connect(store.path) as conn:
            conn.execute("UPDATE invoices SET ai_output = json_set(ai_output, '$.gst_hst_registration_number', '') "
                         "WHERE id = ?", (invoice_id,))  # fmt: skip
    return invoice_id


def test_a_blank_gst_number_is_filled_from_the_vendor_list_and_said_not_printed(client, store):
    invoice_id = _red_river(store, printed=False)
    first = client.get(f"/review/{invoice_id}").text  # opened: filled at once, with the note under it
    field = first.split(f'data-field="{GST}"', 1)[1].split("</label>", 1)[0]
    assert 'value="741852966 RT0001"' in field and "From the vendor list — not printed on the invoice" in field
    assert "data-from-vendor-note hidden" not in field
    answer = check(client, invoice_id, form_of(store, invoice_id))
    assert answer["header"]["values"] == {GST: "741852966 RT0001"}
    assert answer["header"]["from_vendor"]["fields"] == {GST: "741852966 RT0001"}
    checks = html.unescape(answer["sections"]["checks"])
    assert "The GST/HST number isn't printed on this invoice: CRA needs it" in checks
    assert "supplier GST/HST registration number not found" not in checks and GST not in answer["header"]["flags"]
    # approved: the coding remembers which number came from the vendor list
    data = form_of(store, invoice_id, **{GST: "741852966 RT0001"})
    data["from_vendor"] = json.dumps(answer["header"]["from_vendor"])
    data["override"] = check(client, invoice_id, data)["sections"]["bar"].split('name="override" value="', 1)[-1].split(
        '"')[0]  # fmt: skip
    client.post(f"/review/{invoice_id}/approve", data=data, headers=PAGE)
    inv = store.get_invoice(invoice_id)
    assert inv["status"] == APPROVED and inv["final_output"][FROM_VENDOR_LIST] == [GST]


def test_a_printed_number_that_differs_from_the_vendor_list_is_highlighted(client, store):
    invoice_id = _red_river(store)
    answer = check(client, invoice_id, form_of(store, invoice_id, **{GST: "123456782 RT0001"}))
    assert answer["header"]["values"] == {}  # printed: never replaced
    assert answer["header"]["flags"][GST] == "Vendor list: 741852966 RT0001"
    assert "VENDOR_TAX_NUMBER_CHANGED" in answer["sections"]["checks"] or "differs from the vendor list" in (
        html.unescape(answer["sections"]["checks"])
    )  # the checks keep listing it
    page = client.get(f"/review/{invoice_id}").text
    field = page.split(f'data-field="{GST}"', 1)[0].rsplit("<label", 1)[1]
    assert "is-flagged" in field


# --- In a browser ---------------------------------------------------------------------------------------------------


OPTION = ".pk-layer:not([hidden]) [role=option]"


def _pick_vendor(page, typed: str, name: str) -> None:
    page.locator("#vendor-name").fill(typed)
    page.locator(OPTION, has_text=name).first.click()


def test_in_a_browser_picking_a_vendor_fills_its_numbers_and_follows_it(browser, store):  # noqa: F811
    _red_river(store, printed=False)  # no GST/HST number read: the vendor list fills it (B-688)
    page, errors = _open_review(browser, store, "Red River", viewport=LAPTOP)
    if True:
        gst, qst = page.locator(f"[name={GST}]"), page.locator(f"[name={QST}]")
        gst.fill("")
        qst.fill("1111111111 TQ0001")  # typed by the clerk: never overwritten
        page.wait_for_timeout(600)
        vendor = page.locator("#vendor-name")
        _pick_vendor(page, "Pacific Office", "Pacific Office Supply")
        page.wait_for_function(f"document.querySelector('[name={GST}]').value === '555666775 RT0001'")
        note = page.locator(f'[data-field="{GST}"] [data-from-vendor-note]')
        assert note.is_visible() and "not printed on the invoice" in note.inner_text()
        assert qst.input_value() == "1111111111 TQ0001"
        assert page.locator("#toasts", has_text="filled from the vendor list").count()
        # another vendor: the filled number follows it
        _pick_vendor(page, "Prairie Network", "Prairie Network Supply")
        page.wait_for_function(f"document.querySelector('[name={GST}]').value === '609274188 RT0001'")
        assert qst.input_value() == "1111111111 TQ0001"
        # Ctrl+Z puts back the vendor with its numbers (B-682)
        page.keyboard.press("Control+z")
        page.wait_for_function(f"document.querySelector('[name={GST}]').value === '555666775 RT0001'")
        assert "Pacific" in vendor.input_value()
        page.wait_for_timeout(900)  # checked again: the server does not fill it again
        assert gst.input_value() == "555666775 RT0001"
        _pick_vendor(page, "Prairie Network", "Prairie Network Supply")
        page.wait_for_function(f"document.querySelector('[name={GST}]').value === '609274188 RT0001'")
        # a printed number that differs from the vendor list: amber, with the list's number under it
        gst.fill("123456782 RT0001")
        page.wait_for_selector(f'[data-field="{GST}"].is-flagged')
        flag = page.locator(f'[data-field="{GST}"] [data-flag]')
        assert flag.is_visible() and "Vendor list: 609274188 RT0001" in flag.inner_text()
        assert not page.locator(f'[data-field="{GST}"] [data-from-vendor-note]').is_visible()
        assert errors == []


# --- Pay from: our bank account (D-66) -------------------------------------------------------------------------------

BANKS = [
    {"code": "CAD-OP", "name": "Sample Bank CAD operating", "currency": "CAD", "account_number": "0000001234",
     "is_default": "yes"},
    {"code": "CAD-PAY", "name": "Sample Bank CAD payables", "currency": "CAD", "account_number": "0000004321"},
    {"code": "USD-OP", "name": "Sample Bank USD", "currency": "USD", "account_number": "0000005678"},
]  # fmt: skip


def _banks(store: Store) -> None:
    rows, problems = banks.clean_rows(BANKS)
    assert not problems
    store.save_bank_accounts(rows)


def _approvable(client, invoice_id: int, data: dict) -> dict:
    """``data`` with "Approve anyway" ticked for the errors the check finds (the demo's are not this test's)."""
    bar = check(client, invoice_id, data)["sections"]["bar"]
    if 'name="override" value="' in bar:
        data = {**data, "override": bar.split('name="override" value="', 1)[1].split('"')[0]}
    return data


def test_without_bank_accounts_nothing_asks_for_one(client, store):
    invoice_id = _red_river(store)
    answer = check(client, invoice_id, form_of(store, invoice_id))
    checks = html.unescape(answer["sections"]["checks"])
    assert "Add your bank accounts on GL accounts & tax to choose which account pays" in checks
    page = client.get(f"/review/{invoice_id}").text
    assert 'name="pay_from_account"' not in page and 'href="/accounts?tab=bank"' in page
    assert "Pay into" not in page


def test_pay_from_defaults_to_the_currencys_account_and_is_stored(client, store):
    _banks(store)
    invoice_id = _red_river(store)
    page = client.get(f"/review/{invoice_id}").text
    select = page.split('name="pay_from_account"', 1)[1].split("</select>", 1)[0]
    option = '<option value="CAD-OP" data-currency="CAD" selected>Sample Bank CAD operating · CAD · ••••1234</option>'
    assert option in select
    assert "0000001234" not in page  # never the whole number
    assert "Add your bank accounts" not in check(client, invoice_id, form_of(store, invoice_id))["sections"]["checks"]
    # the currency changes: the default follows it (never an account in another currency kept unsaid)
    usd = check(client, invoice_id, form_of(store, invoice_id, currency="USD", pay_from_account="CAD-OP"))
    assert usd["header"]["values"] == {"pay_from_account": "USD-OP"}
    # picked by the reviewer: kept while the currency stays, with a warning when it is in another currency
    picked = check(client, invoice_id, form_of(store, invoice_id, pay_from_account="USD-OP", pay_from_for="CAD"))
    assert picked["header"]["values"] == {} and picked["header"]["flags"]["pay_from_account"]
    assert "A CAD invoice paid from Sample Bank USD, a USD account" in html.unescape(picked["sections"]["checks"])
    data = _approvable(client, invoice_id, form_of(store, invoice_id, pay_from_account="CAD-PAY", pay_from_for="CAD"))
    assert client.post(f"/review/{invoice_id}/approve", data=data, headers=PAGE).json()["ok"]
    assert store.get_invoice(invoice_id)["final_output"]["pay_from_account"] == "CAD-PAY"
    assert store.last_pay_from(store.get_invoice(invoice_id)["vendor_key"]) == "CAD-PAY"  # this vendor's next one


def test_an_invoice_with_no_account_in_its_currency_is_not_approved_without_one(client, store):
    _banks(store)
    invoice_id = _red_river(store)
    data = form_of(store, invoice_id, currency="EUR")
    answer = check(client, invoice_id, data)
    assert answer["header"]["values"].get("pay_from_account", "") == "" and not answer["allow"]
    assert "Pick the bank account to pay this invoice from" in html.unescape(answer["sections"]["bar"])
    refused = client.post(f"/review/{invoice_id}/approve", data=_approvable(client, invoice_id, data), headers=PAGE)
    assert refused.status_code == 400 and "Pick the bank account to pay this invoice from" in refused.json()["error"]
    assert store.get_invoice(invoice_id)["status"] == REVIEW


def test_the_vendors_printed_bank_details_are_shown_masked_and_not_edited(client, store):
    import sqlite3

    invoice_id = _red_river(store)
    with sqlite3.connect(store.path) as conn:
        conn.execute("UPDATE invoices SET ai_output = json_set(ai_output, '$.remit_bank_account', "
                     "'Transit 12345, Institution 004, Account 7654321') WHERE id = ?", (invoice_id,))  # fmt: skip
    page = client.get(f"/review/{invoice_id}").text
    shown = page.split('data-field="remit_bank_account"', 1)[1].split("</div>", 1)[0]
    assert "Bank details printed on the invoice" in shown and "…4321" in shown and "7654321" not in page
    assert 'name="remit_bank_account"' not in page  # read-only: not a box
    data = form_of(store, invoice_id)
    data.pop("remit_bank_account")  # the page posts none
    answer = check(client, invoice_id, data)
    assert answer["ok"]
    data = _approvable(client, invoice_id, data)
    client.post(f"/review/{invoice_id}/approve", data=data, headers=PAGE)
    final = store.get_invoice(invoice_id)["final_output"]
    assert final["remit_bank_account"] == "Transit 12345, Institution 004, Account 7654321"  # kept for the fraud check


def test_in_a_browser_pay_from_is_a_list_that_follows_the_currency(browser, store):
    _banks(store)
    page, errors = _open_review(browser, store, "Red River", viewport=LAPTOP)
    select = page.locator("select[name=pay_from_account]")
    assert select.input_value() == "CAD-OP"
    labels = select.locator("option").all_inner_texts()
    assert labels[1:] == ["Sample Bank CAD operating · CAD · ••••1234", "Sample Bank CAD payables · CAD · ••••4321",
                          "Sample Bank USD · USD · ••••5678"]  # fmt: skip
    currency = page.locator("[form=inv][name=currency]")
    currency.fill("USD")
    currency.press("Tab")
    page.wait_for_function("document.querySelector('select[name=pay_from_account]').value === 'USD-OP'")
    select.select_option("CAD-PAY")  # picked: a CAD account for a USD invoice is kept, and said
    page.wait_for_selector('[data-field="pay_from_account"].is-flagged')
    assert "Not an account in the invoice's currency" in page.inner_text('[data-field="pay_from_account"]')
    page.wait_for_timeout(600)
    assert select.input_value() == "CAD-PAY"
    assert errors == []


# --- Review findings (B-681 to B-687) --------------------------------------------------------------------------------


def _approval_message(client, invoice_id: int, data: dict) -> str:
    """The message shown after Approve & teach (on the page it opens next)."""
    answer = client.post(f"/review/{invoice_id}/approve", data=data, headers=PAGE).json()
    assert answer.get("ok"), answer
    import re

    page = html.unescape(client.get(answer["redirect"]).text)
    return re.search(r"<div><div>(Approved [^<]*)</div>", page).group(1)


def _blank_reading(store: Store, invoice_id: int, *fields: str) -> None:
    """As if ``fields`` had not been read on the invoice (its coding and its capture)."""
    import sqlite3

    path = ", ".join(f"'$.{f}', ''" for f in fields)
    with sqlite3.connect(store.path) as conn:
        conn.execute(f"UPDATE invoices SET ai_output = json_set(ai_output, {path}) WHERE id = ?", (invoice_id,))
        row = conn.execute("SELECT capture_json FROM invoice_capture WHERE invoice_id = ?", (invoice_id,)).fetchone()
        if row:
            capture = json.loads(row[0])
            for f in fields:
                (capture.get("fields") or {}).pop(f, None)
            conn.execute("UPDATE invoice_capture SET capture_json = ? WHERE invoice_id = ?",
                         (json.dumps(capture), invoice_id))  # fmt: skip


def test_numbers_filled_from_the_vendor_list_are_not_corrections(client, store):
    """B-681: approving an invoice whose GST/HST and PST numbers the vendor list filled in said "Learned from your 2
    corrections (GST/HST number, pst registration number)", counted them against the supplier and its readers, and
    as the reviewer's edits."""
    invoice_id = _red_river(store)
    store.save_vendor(store.get_invoice(invoice_id)["vendor_key"], "Red River Office Interiors",
                      expected_gst="741852966 RT0001", pst_number="160482-7")  # fmt: skip
    _blank_reading(store, invoice_id, GST, PST)
    page = client.get(f"/review/{invoice_id}").text
    state = json.loads(html.unescape(page.split('name="from_vendor" value="', 1)[1].split('"', 1)[0]))
    assert set(state["fields"]) == {GST, PST}
    data = _approvable(client, invoice_id, form_of(store, invoice_id, **{GST: state["fields"][GST],
                                                                         PST: state["fields"][PST]}))  # fmt: skip
    data["from_vendor"] = json.dumps(state)
    said = _approval_message(client, invoice_id, data)
    assert "Approved" in said and "correction" not in said and "registration number" not in said, said
    inv = store.get_invoice(invoice_id)
    assert inv["status"] == APPROVED and inv["final_output"][GST] == "741852966 RT0001"
    assert GST not in inv["edits"] and PST not in inv["edits"]
    outcomes = {r["field"]: r["correct"] for r in store.supplier_outcomes(store.supplier_key_for(
        inv["final_output"]["vendor_name"], inv["final_output"][GST])) if r["invoice_id"] == invoice_id}  # fmt: skip
    assert outcomes.get(GST, 1) == 1 and outcomes.get(PST, 1) == 1
    import sqlite3

    with sqlite3.connect(store.path) as conn:
        scored = conn.execute("SELECT COUNT(*) FROM reader_outcomes WHERE invoice_id = ? AND field IN (?, ?)",
                              (invoice_id, GST, PST)).fetchone()[0]  # fmt: skip
        lessons = conn.execute("SELECT COUNT(*) FROM lessons WHERE field IN (?, ?) AND corrected > 0",
                               (GST, PST)).fetchone()[0]  # fmt: skip
    assert scored == 0 and lessons == 0


def test_every_header_field_has_a_word_for_the_learning_message():
    from ap_coder.capture.workflow import FIELD_WORDS
    from ap_coder.core.schema import InvoiceCoding

    headers = [f for f in InvoiceCoding.model_fields if f not in ("line_items", "tax_lines", "confidence_score")]
    missing = [f for f in headers if f not in FIELD_WORDS]
    assert missing == [] and FIELD_WORDS[PST] == "PST number"


def test_numbers_marked_from_the_vendor_list_are_always_this_vendors():
    """B-682 (the server's backstop): a number marked as filled from the vendor list that is not the vendor's own
    (an Undo that kept the vendor but put back another one's numbers) follows the vendor now in the field."""
    header = _header("Other Co", **{GST: "123456782 RT0001", QST: "1234567890 TQ0001"})
    state = {"vendor": "other", "fields": {GST: "123456782 RT0001", QST: "1234567890 TQ0001"}}
    state, changed = view.fill_from_vendor(header, state, [], OTHER)
    assert changed == {GST: "741852966 RT0001", QST: ""}
    assert state == {"vendor": "other", "fields": {GST: "741852966 RT0001"}}


def test_in_a_browser_undo_after_a_vendor_change_puts_back_the_vendor_with_its_numbers(browser, store):
    """B-682: Ctrl+Z after picking a second vendor kept that vendor but put back the first one's numbers, still
    marked as from the vendor list."""
    _red_river(store, printed=False)
    page, errors = _open_review(browser, store, "Red River", viewport=LAPTOP)
    gst = page.locator(f"[name={GST}]")
    gst.fill("")
    page.wait_for_timeout(600)
    _pick_vendor(page, "Pacific Office", "Pacific Office Supply")
    page.wait_for_function(f"document.querySelector('[name={GST}]').value === '555666775 RT0001'")
    _pick_vendor(page, "Prairie Network", "Prairie Network Supply")
    page.wait_for_function(f"document.querySelector('[name={GST}]').value === '609274188 RT0001'")
    page.keyboard.press("Control+z")
    page.wait_for_function(f"document.querySelector('[name={GST}]').value === '555666775 RT0001'")
    assert "Pacific" in page.locator("#vendor-name").input_value()  # the vendor and its numbers, together
    page.wait_for_timeout(900)  # checked again: nothing moves
    assert "Pacific" in page.locator("#vendor-name").input_value() and gst.input_value() == "555666775 RT0001"
    state = json.loads(page.locator("input[data-from-vendor]").input_value())
    assert state["vendor"] == "pacific office supply" and state["fields"][GST] == "555666775 RT0001"
    assert errors == []


def test_pay_from_an_unknown_or_closed_account_is_never_approved(client, store):
    """B-683: PAY_FROM_UNKNOWN was an error "Approve anyway" let through: a made-up code was stored, and an account
    set "not in use" after it was chosen could still pay."""
    _banks(store)
    invoice_id = _red_river(store)
    bogus = form_of(store, invoice_id, pay_from_account="BOGUS", pay_from_for="CAD")
    answer = check(client, invoice_id, bogus)
    assert not answer["allow"] and "BOGUS is not one of your bank accounts in use" in html.unescape(
        answer["sections"]["bar"])  # fmt: skip
    refused = client.post(f"/review/{invoice_id}/approve", data=_approvable(client, invoice_id, bogus), headers=PAGE)
    assert refused.status_code == 400 and store.get_invoice(invoice_id)["status"] == REVIEW
    # chosen, then closed: shown as such, and blocks
    rows, _ = banks.clean_rows([dict(BANKS[1], active="no")])
    store.save_bank_accounts(rows)
    closed = form_of(store, invoice_id, pay_from_account="CAD-PAY", pay_from_for="CAD")
    assert not check(client, invoice_id, closed)["allow"]
    refused = client.post(f"/review/{invoice_id}/approve", data=_approvable(client, invoice_id, closed), headers=PAGE)
    assert refused.status_code == 400 and store.get_invoice(invoice_id)["status"] == REVIEW
    page = client.get(f"/review/{invoice_id}").text
    assert "(not in use — pick another)" in page


def test_the_queue_is_ready_only_when_the_pay_from_rule_lets_approve(client, store):
    """B-685: the queue's Ready ignored Pay from: with bank accounts set up after an invoice was read, Ready showed
    for an invoice whose review screen would not open Approve (no account in its currency)."""
    from ap_coder.coding.bulk import bulk_approve
    from ap_coder.web.pages.review import attention

    pending = store.list_invoices(REVIEW)
    ready_before = {i["id"] for i in pending} - set(attention(store, None, pending))
    assert ready_before  # the demo has invoices Ready
    rows, _ = banks.clean_rows([{"code": "USD-OP", "name": "US", "currency": "USD", "is_default": "yes"}])
    store.save_bank_accounts(rows)
    pending = store.list_invoices(REVIEW)
    why = attention(store, None, pending)
    cad = [i for i in pending if i["id"] in ready_before and (i["currency"] or "CAD") == "CAD"]
    assert cad and all("Pick the bank account to pay this invoice from" in why[i["id"]] for i in cad)
    for inv in cad:  # the review screen agrees: Approve is not open
        assert not check(client, inv["id"], form_of(store, inv["id"]))["allow"]
    # bulk approval: no CAD account to pay from, so it is left, and says why
    from ap_coder.core.config import Settings

    result = bulk_approve(store, store.reference_data(), Settings(), [cad[0]["id"]], "Pat")
    assert result["approved"] == [] and "Pick the bank account to pay this invoice from" in str(result["skipped"])
    # a CAD default: Ready again, and bulk approval pays from it
    rows, _ = banks.clean_rows([{"code": "CAD-OP", "name": "Operating", "currency": "CAD", "is_default": "yes"}])
    store.save_bank_accounts(rows)
    for inv in cad:  # (the screen's checks above flagged them in the queue: opened again, they are clean)
        assert check(client, inv["id"], form_of(store, inv["id"]))["allow"]
    assert not {i["id"] for i in cad} & set(attention(store, None, store.list_invoices(REVIEW)))


def test_a_posted_vendor_bank_detail_is_ignored(client, store):
    """B-686: the vendor's printed bank details are read-only on the screen, but a crafted post still changed them
    (and with them what the fraud check compares)."""
    import sqlite3

    invoice_id = _red_river(store)
    printed = "Transit 12345, Institution 004, Account 7654321"
    with sqlite3.connect(store.path) as conn:
        conn.execute("UPDATE invoices SET ai_output = json_set(ai_output, '$.remit_bank_account', ?) WHERE id = ?",
                     (printed, invoice_id))  # fmt: skip
    base = view.Draft.stored(store.get_invoice(invoice_id)["ai_output"])
    assert view.Draft.from_form({"remit_bank_account": "Account 999"}, base).header["remit_bank_account"] == printed
    data = _approvable(client, invoice_id, form_of(store, invoice_id, remit_bank_account="Account 999"))
    client.post(f"/review/{invoice_id}/approve", data=data, headers=PAGE)
    assert store.get_invoice(invoice_id)["final_output"]["remit_bank_account"] == printed


# --- Re-review (B-688 to B-690) --------------------------------------------------------------------------------------


def test_only_a_number_not_read_is_filled_from_the_vendor_list_and_not_a_correction(client, store):
    """B-688: a GST/HST number read wrong (999999998 RT0001), cleared by the clerk, then filled from the vendor list
    was taken back to the wrong reading for learning: "You changed no header field", and the reading scored right."""
    from ap_coder.core.schema import as_read

    # the rules: only a field the reading left blank is filled and marked; the clerk's clearing is the clerk's edit
    header = _header()
    state, changed = view.fill_from_vendor(header, {}, [], ACME, unread={QST, PST})
    assert GST not in changed and header[GST] == "" and state["fields"] == {QST: "1234567890 TQ0001"}
    final = {GST: "123456782 RT0001", QST: "1234567890 TQ0001", FROM_VENDOR_LIST: [GST, QST]}
    assert as_read(final, {GST: "999999998 RT0001", QST: ""}) == {**final, QST: ""}  # a reading kept is no fill
    # on the screen
    invoice_id = next(i["id"] for i in store.list_invoices(REVIEW) if i["vendor_name"].startswith("Agence"))
    import sqlite3

    with sqlite3.connect(store.path) as conn:
        conn.execute("UPDATE invoices SET ai_output = json_set(ai_output, '$.gst_hst_registration_number', "
                     "'999999998 RT0001') WHERE id = ?", (invoice_id,))  # fmt: skip
    data = form_of(store, invoice_id, **{GST: ""})
    data["from_vendor"] = json.dumps({"vendor": "prairie network supply", "fields": {}})  # another vendor picked
    answer = check(client, invoice_id, data)
    assert GST not in answer["header"]["values"] and GST not in answer["header"]["from_vendor"]["fields"]
    data = _approvable(client, invoice_id, {**data, GST: "812345676 RT0001",
                                            "from_vendor": json.dumps(answer["header"]["from_vendor"])})  # fmt: skip
    said = _approval_message(client, invoice_id, data)
    assert "correction (GST/HST number)" in said, said
    inv = store.get_invoice(invoice_id)
    assert GST in inv["edits"] and FROM_VENDOR_LIST not in inv["final_output"]


def test_in_a_browser_a_number_read_and_cleared_is_not_filled_from_the_vendor_list(browser, store):
    """B-688: the clerk cleared a GST/HST number that was read: picking a vendor leaves it for the clerk to type."""
    page, errors = _open_review(browser, store, "Red River", viewport=LAPTOP)
    gst = page.locator(f"[name={GST}]")
    gst.fill("")
    page.wait_for_timeout(600)
    _pick_vendor(page, "Pacific Office", "Pacific Office Supply")
    page.wait_for_timeout(1200)
    assert gst.input_value() == ""
    assert errors == []


def test_the_queue_does_not_decode_every_invoice_again_for_pay_from(store, monkeypatch):
    """B-690: since B-685 the queue decoded every waiting invoice's coding on each load once bank accounts existed
    (0.17 s instead of 0.05 s at 5,000 invoices). The answer is remembered until the invoice, the bank accounts or the
    approvals change."""
    from ap_coder.web.pages import review

    rows, _ = banks.clean_rows([{"code": "USD-OP", "name": "US", "currency": "USD", "is_default": "yes"}])
    store.save_bank_accounts(rows)
    pending = store.list_invoices(REVIEW)
    first = review.attention(store, None, pending)
    calls = []
    real = review.json.loads
    monkeypatch.setattr(review.json, "loads", lambda *a, **k: calls.append(1) or real(*a, **k))
    assert review.attention(store, None, pending) == first and calls == []
    rows, _ = banks.clean_rows([{"code": "CAD-OP", "name": "Operating", "currency": "CAD", "is_default": "yes"}])
    store.save_bank_accounts(rows)  # the bank accounts changed: asked again
    again = review.attention(store, None, pending)
    assert calls and again != first
