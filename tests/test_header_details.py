"""The review screen's invoice details (the header): the vendor list's tax numbers filled in where none is printed
(D-65), the details a check is about highlighted with a note, and "Pay from", our bank account (D-66)."""

from __future__ import annotations

import html
import json

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
        # Ctrl+Z puts the number back as it was, the vendor stays
        page.keyboard.press("Control+z")
        page.wait_for_function(f"document.querySelector('[name={GST}]').value === '555666775 RT0001'")
        assert "Prairie" in vendor.input_value()
        page.wait_for_timeout(900)  # checked again: the server does not fill it again
        assert gst.input_value() == "555666775 RT0001"
        # a printed number that differs from the vendor list: amber, with the list's number under it
        gst.fill("123456782 RT0001")
        page.wait_for_selector(f'[data-field="{GST}"].is-flagged')
        flag = page.locator(f'[data-field="{GST}"] [data-flag]')
        assert flag.is_visible() and "Vendor list: 609274188 RT0001" in flag.inner_text()
        assert not page.locator(f'[data-field="{GST}"] [data-from-vendor-note]').is_visible()
        assert errors == []
