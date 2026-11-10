"""A day on the made-up company (the ``chart_company`` fixture: a chart of accounts, last year's journal, JD Edwards
set up for vouchers typed by hand): the queue, each of the seven invoices on the review screen, the chart's checks,
approving, and the keying sheet and batch files made from what was approved.

These are the first end-to-end tests of an install with a chart loaded, on today's code. Where a part is not built
yet the test of it is ``xfail(strict=True)`` with what is missing: it fails the run the day it starts passing, so the
build that brings it takes the mark off. What today's code does instead is asserted beside it, with a comment."""

import csv
import datetime as dt
import html
import io
import zipfile
from decimal import Decimal

import openpyxl
import pytest

from ap_coder.bench import jde_invoices
from ap_coder.core.store import APPROVED, REVIEW
from ap_coder.erp import jde, jde_keying
from tests.erp.test_jde_keying import blocks, field
from tests.review_page import ReviewPage
from tests.webclient import PAGE, follow, post

NEW_NUMBER = "99001"  # the new supplier's JD Edwards address number, once it is set up
TAX_WORDS = {"quebec": "GST + QST", "usd_new_supplier": "No tax charged"}  # each line's tax code as shown; else GST


def _coded(client, chart_company, name: str) -> ReviewPage:
    """The invoice's review screen with every line coded by hand as AP would key it (``Invoice.parts``): a line of
    two parts split first, from the last line up so the lines before keep their place."""
    invoice, invoice_id = chart_company.invoices[name]
    page = ReviewPage(client, invoice_id)
    for n, line in reversed(list(enumerate(invoice.lines, start=1))):
        if len(line.parts) > 1:
            whole = sum(part.amount for part in line.parts)
            page.split(n, {part.account: float(part.amount / whole * 100) for part in line.parts})
    for n, part in enumerate(invoice.parts, start=1):
        page.set_account(n, part.account)
    return page


def _approve_the_ready_ones(client, chart_company) -> list[int]:
    """Every invoice but the new supplier's coded by hand and approved: their ids."""
    done = []
    for name in jde_invoices.NAMED:
        if name != "usd_new_supplier":
            page = _coded(client, chart_company, name)
            assert page.check()["allow"], (name, page.findings)
            assert page.approve()["ok"], name
            done.append(page.id)
    return done


def _sheet(client, store, ids: list[int]):
    """A keying-sheet batch of ``ids`` made on the Exports page: (the batch's number, its workbook)."""
    follow(client, post(client, "/exports/create", {"fmt": jde_keying.FORMAT, "ids": ids}))
    batch = store.export_batches()[0]["id"]
    answer = client.get(f"/exports/batches/{batch}/download?fmt={jde_keying.FORMAT}")
    assert answer.status_code == 200
    return batch, openpyxl.load_workbook(io.BytesIO(answer.content))


def _vouchers(workbook) -> dict[str, list]:
    """The keying sheet's blocks by invoice number."""
    return {field(block, "Invoice number").value: block for block in blocks(workbook[jde_keying.SHEET])}


def _cents(value) -> Decimal | None:
    """A money cell's number to the cent (None: the cell is empty)."""
    return None if value is None else Decimal(str(value)).quantize(Decimal("0.01"))


def _money(block, label: str) -> Decimal | None:
    return _cents(field(block, label).value)


def _lines(block) -> list[tuple[str, Decimal]]:
    """A voucher's G/L lines: (the account string, the amount)."""
    return [(row[0].value, _cents(row[2].value)) for row in block[9:-1]]


# --- the queue and the screen -------------------------------------------------------------------------------------


def test_the_queue_lists_the_seven_invoices(chart_company, chart_company_client):
    text = chart_company_client.get("/review").text
    assert "All · 7" in text
    for invoice, invoice_id in chart_company.invoices.values():
        assert f'href="/review/{invoice_id}"' in text
        assert html.escape(invoice.supplier_name) in text and invoice.number in text


def test_each_invoice_opens_with_its_lines_as_printed(chart_company, chart_company_client):
    for name, (invoice, invoice_id) in chart_company.invoices.items():
        page = ReviewPage(chart_company_client, invoice_id)
        assert page.status == 200, name
        assert [(row.description, row.amount) for row in page.lines] == [
            (line.description, f"{line.amount:.2f}") for line in invoice.lines], name  # fmt: skip
        assert {row.tax for row in page.lines} == {TAX_WORDS.get(name, "GST")}, name
        assert all(row.reason for row in page.lines), name


def test_today_every_line_arrives_without_an_account_and_says_why(chart_company, chart_company_client):
    """Today nothing is suggested from last year's journal: each line is left for a person with a reason, and
    Approve is closed until every line has its account. The suggestions build changes this for the lines the journal
    settles (the two tests below); a line it cannot settle must still arrive like this."""
    for name, (_, invoice_id) in chart_company.invoices.items():
        page = ReviewPage(chart_company_client, invoice_id)
        assert [row.account for row in page.lines] == [""] * len(page.lines), name
        assert all(row.reason for row in page.lines), name
        assert {"GL_UNASSIGNED", "CC_UNASSIGNED"} <= set(page.findings), name
        assert page.not_ready.startswith("Pick a GL account for") or name == "usd_new_supplier", name
    one = ReviewPage(chart_company_client, chart_company.invoices["one_line"][1])
    assert one.not_ready == "Pick a GL account for line 1 and a cost center for line 1 to approve"
    assert one.approve() == {"ok": False, "error": one.not_ready + "."}


@pytest.mark.xfail(strict=True, reason="Not built yet (the suggestions build): nothing is suggested from last year's "
                   "journal, where this supplier's vouchers all went to one account string")  # fmt: skip
def test_the_one_line_invoice_arrives_coded_to_its_suppliers_one_account(chart_company, chart_company_client):
    invoice, invoice_id = chart_company.invoices["one_line"]
    page = ReviewPage(chart_company_client, invoice_id)
    assert [row.account for row in page.lines] == [invoice.lines[0].parts[0].account]
    assert page.lines[0].reason and not page.not_ready


@pytest.mark.xfail(strict=True, reason="Not built yet (the suggestions build): the lines that name whose move it is "
                   "should get that person's account from the journal, and the line that says nothing of what was "
                   "bought should be left for a person, with why")  # fmt: skip
def test_the_four_line_invoice_arrives_with_three_lines_coded_and_the_vague_one_left(
    chart_company, chart_company_client
):
    invoice, invoice_id = chart_company.invoices["four_lines"]
    page = ReviewPage(chart_company_client, invoice_id)
    wanted = ["" if line.for_a_person else line.parts[0].account for line in invoice.lines]
    assert [row.account for row in page.lines] == wanted
    assert page.lines[3].reason and page.not_ready


# --- the chart's checks -------------------------------------------------------------------------------------------


def _other_accounts(truth) -> dict[str, str]:
    """An account string of each kind a line must not, or should not, be coded to."""
    main = truth.company.main_business_unit
    moved = next(a for a in truth.accounts if a.object == "1450" and a.subsidiary)  # on another business unit only
    title = next(a for a in truth.accounts if a.business_unit == main and a.object and a.posting_edit)
    closed = next(u.number for u in truth.business_units if u.status == "INA" and len(u.number) == 10)
    used = next(a for a in truth.suggestable() if a.business_unit == main)
    return {
        "ACCOUNT_NOT_IN_CHART": f"{main}.{moved.object}.{moved.subsidiary}",
        "ACCOUNT_NOT_POSTABLE": title.string,
        "BU_INACTIVE": f"{closed}.{used.object}.{used.subsidiary}",
        "ACCOUNT_RESERVED": next(a for a in truth.accounts if a.reserved).string,
    }


@pytest.mark.parametrize("code", ["ACCOUNT_NOT_IN_CHART", "ACCOUNT_NOT_POSTABLE", "BU_INACTIVE"])
def test_an_account_the_chart_does_not_take_keeps_approve_closed(chart_company, chart_company_client, code):
    account = _other_accounts(chart_company.truth)[code]
    page = ReviewPage(chart_company_client, chart_company.invoices["one_line"][1])
    page.set_account(1, account)
    answer = page.check()
    assert not answer["allow"] and list(page.findings) == [code]
    assert page.lines[0].account == account  # kept as typed: the screen shows what is wrong, not a blank
    assert page.findings[code].startswith("Line 1: ") and page.not_ready == page.findings[code]
    assert page.approve() == {"ok": False, "error": f"Not ready: {page.not_ready}."}
    assert chart_company.store.get_invoice(page.id)["status"] == REVIEW


def test_a_reserved_subsidiary_typed_by_hand_is_a_warning_and_may_be_approved(chart_company, chart_company_client):
    account = _other_accounts(chart_company.truth)["ACCOUNT_RESERVED"]
    page = ReviewPage(chart_company_client, chart_company.invoices["one_line"][1])
    page.set_account(1, account)
    assert page.check()["allow"] and list(page.findings) == ["ACCOUNT_RESERVED"]
    assert "is not offered for invoices" in page.findings["ACCOUNT_RESERVED"] and not page.not_ready
    assert page.approve()["ok"]
    assert chart_company.store.get_invoice(page.id)["status"] == APPROVED


# --- approving, the keying sheet and the batch files ---------------------------------------------------------------


def test_the_ready_invoices_are_approved_and_the_keying_sheet_is_what_ap_keys(chart_company, chart_company_client):
    """Six of the seven, coded by hand and approved, on the keying sheet: each voucher's pay item (the supplier's
    number, the invoice, the gross, the terms code, tax explanation code V with the company's tax area, the taxable
    amount and the tax recovered), its G/L lines (the account string a text cell, the amount as keyed) and a check
    line that balances. Nothing is on "Not ready"."""
    store, company = chart_company.store, chart_company.truth.company
    ids = _approve_the_ready_ones(chart_company_client, chart_company)
    assert [i["id"] for i in store.list_invoices(REVIEW)] == [chart_company.invoices["usd_new_supplier"][1]]
    _, workbook = _sheet(chart_company_client, store, ids)
    found = _vouchers(workbook)
    assert list(found) == [chart_company.invoices[n][0].number for n in jde_invoices.NAMED if n != "usd_new_supplier"]
    for name, (invoice, _) in chart_company.invoices.items():
        if name == "usd_new_supplier":
            continue
        block = found[invoice.number]
        number = field(block, "Supplier number")
        assert (number.value, number.data_type, number.number_format) == (str(invoice.supplier.number), "s", "@"), name
        assert field(block, "Supplier name").value == invoice.supplier_name, name
        day = dt.datetime.combine(invoice.date, dt.time())
        assert (field(block, "Invoice date").value, field(block, "G/L date").value) == (day, day), name
        assert (_money(block, "Gross amount"), field(block, "Currency").value) == (invoice.total, "CAD"), name
        assert field(block, "Payment terms").value == (None if name == "credit_note" else company.payment_terms), name
        assert (field(block, "Tax explanation code").value, field(block, "Tax rate/area").value) == (
            "V", company.tax_area), name  # fmt: skip
        assert (_money(block, "Taxable amount"), _money(block, "Tax amount")) == (
            invoice.subtotal, invoice.recovered), name  # fmt: skip
        lines, check = block[9:-1], block[-1]
        assert _lines(block) == [(part.account, part.amount) for part in invoice.parts], name
        assert {(a.data_type, a.number_format) for a, _, _, _ in lines} == {("s", "@")}, name  # text: zeros kept
        assert all(description.value for _, description, _, _ in lines), name  # the account's name from the chart
        # The remark is the start of the line as printed, cut at 30 characters (D-210). The remark build (a remark
        # of the line's own: the department's words, ``Part.remark``) changes this column.
        printed = [line.description for line in invoice.lines for _ in line.parts]
        assert all(0 < len(remark.value) <= 30 and text.startswith(remark.value)
                   for (_, _, _, remark), text in zip(lines, printed, strict=True)), name  # fmt: skip
        assert field(block, "Remark").value == lines[0][3].value, name
        assert check[0].value == "Check" and _cents(check[2].value) == invoice.total - invoice.recovered, name
        assert check[3].value.endswith(": balanced"), name
    assert list(workbook[jde_keying.NOT_READY].iter_rows(min_row=2, values_only=True)) == []


def test_the_quebec_voucher_carries_only_the_tax_recovered_and_the_credit_note_is_negative(
    chart_company, chart_company_client
):
    store = chart_company.store
    _, workbook = _sheet(chart_company_client, store, _approve_the_ready_ones(chart_company_client, chart_company))
    found = _vouchers(workbook)
    quebec = chart_company.invoices["quebec"][0]
    taxes = {tax.code: tax.amount for tax in quebec.taxes}
    block = found[quebec.number]
    assert (_money(block, "Taxable amount"), _money(block, "Tax amount")) == (quebec.subtotal, taxes["GST"])
    assert _money(block, "Gross amount") == quebec.subtotal + taxes["GST"] + taxes["QST"]
    assert sum(amount for _, amount in _lines(block)) == quebec.subtotal + taxes["QST"]  # the QST is in the expense
    assert block[-1][3].value == "1,363.69 + 62.00 = 1,425.69: balanced"
    credit = chart_company.invoices["credit_note"][0]
    block = found[credit.number]
    amounts = [_money(block, label) for label in ("Gross amount", "Taxable amount", "Tax amount")]
    assert amounts == [credit.total, credit.subtotal, credit.recovered] and all(amount < 0 for amount in amounts)
    assert _lines(block) == [(part.account, part.amount) for part in credit.parts]
    assert block[-1][3].value == "-578.00 + -28.90 = -606.90: balanced"


def test_the_line_split_across_two_business_units_is_two_lines_of_the_voucher(chart_company, chart_company_client):
    invoice, invoice_id = chart_company.invoices["split_line"]
    page = _coded(chart_company_client, chart_company, "split_line")
    assert [(row.account, row.amount) for row in page.lines] == [
        (part.account, f"{part.amount:.2f}") for part in invoice.parts]  # fmt: skip
    assert page.check()["allow"] and page.approve()["ok"]
    _, workbook = _sheet(chart_company_client, chart_company.store, [invoice_id])
    (block,) = blocks(workbook[jde_keying.SHEET])
    assert _lines(block) == [(part.account, part.amount) for part in invoice.parts]


def test_the_batch_files_of_the_same_invoices_agree_with_the_sheet(chart_company, chart_company_client):
    store = chart_company.store
    batch, workbook = _sheet(chart_company_client, store, _approve_the_ready_ones(chart_company_client, chart_company))
    answer = chart_company_client.post(f"/exports/batches/{batch}/download?fmt={jde.FORMAT}", headers=PAGE)
    assert answer.status_code == 200
    with zipfile.ZipFile(io.BytesIO(answer.content)) as files:
        heads = list(csv.DictReader(io.StringIO(files.read(jde.HEADER_FILE).decode())))
        lines = list(csv.DictReader(io.StringIO(files.read(jde.DIST_FILE).decode())))
    sheet = blocks(workbook[jde_keying.SHEET])
    assert len(heads) == len(sheet) == 6

    def cents(value) -> str:
        return str(round(value * 100))

    for head, block in zip(heads, sheet, strict=True):
        assert (head["VLAN8"], head["VLVINV"], head["VLEXR1"], head["VLTXA1"], head["VLCO"]) == (
            field(block, "Supplier number").value, field(block, "Invoice number").value,
            field(block, "Tax explanation code").value, field(block, "Tax rate/area").value, "00210")  # fmt: skip
        assert (head["VLATXA"], head["VLSTAM"]) == (
            cents(field(block, "Taxable amount").value), cents(field(block, "Tax amount").value))  # fmt: skip
        gross = field(block, "Gross amount").value  # not sent: taxable + not taxable + tax add up to it
        assert int(head["VLATXA"]) + int(head["VLATXN"]) + int(head["VLSTAM"]) == round(gross * 100)
        assert head["VLDGJ"] == str(jde.julian(field(block, "G/L date").value))
        assert head["VLRMK"] == field(block, "Remark").value  # the remarks as typed (the settings' choice)
    typed = [(row[0].value, cents(row[2].value), row[3].value) for block in sheet for row in block[9:-1]]
    assert typed == [(line["VNANI"], line["VNAA"], line["VNEXA"]) for line in lines]


# --- the new supplier, in USD -------------------------------------------------------------------------------------


def test_the_new_suppliers_invoice_cannot_be_approved_until_the_supplier_has_its_number(
    chart_company, chart_company_client
):
    """A supplier that is not in the supplier master has no JD Edwards number: its invoice is held on the review
    screen, which says so, and so never reaches a keying sheet (not even its "Not ready" sheet, which lists approved
    invoices only)."""
    invoice, invoice_id = chart_company.invoices["usd_new_supplier"]
    page = _coded(chart_company_client, chart_company, "usd_new_supplier")
    assert not page.check()["allow"]
    assert {"VENDOR_NOT_IN_MASTER", "JDE_NO_AN8"} <= set(page.findings)
    assert page.not_ready == "JD Edwards: this vendor has no supplier number (AN8)"
    assert page.approve() == {"ok": False, "error": f"Not ready: {page.not_ready}."}
    refused = post(chart_company_client, "/exports/create", {"fmt": jde_keying.FORMAT, "ids": [invoice_id]})
    assert "no longer approved" in follow(chart_company_client, refused)
    assert chart_company.store.export_batches() == []


def test_once_the_new_supplier_has_its_number_its_usd_voucher_is_right_on_the_sheet(
    chart_company, chart_company_client
):
    """Added on the review screen with its JD Edwards address number, the supplier's invoice is approved with
    "Approve anyway" (it is still to be set up in JD Edwards) and its voucher is on the keying sheet in USD, with no
    tax amounts on the pay item. Its number taken away again, the approved invoice is on "Not ready", with why."""
    client, store = chart_company_client, chart_company.store
    invoice, invoice_id = chart_company.invoices["usd_new_supplier"]
    added = post(client, f"/review/{invoice_id}/vendor", {"name": invoice.supplier_name, "an8": NEW_NUMBER,
                                                          "province": jde.OUTSIDE_CANADA})  # fmt: skip
    assert added.json()["ok"]
    page = _coded(client, chart_company, "usd_new_supplier")
    assert not page.check()["allow"] and "VENDOR_ADDED_HERE" in page.findings and not page.not_ready
    assert page.approve()["error"].startswith("Tick “Approve anyway” and say why")
    assert page.approve(anyway="Being set up in JD Edwards this week")["ok"]
    _, workbook = _sheet(client, store, [invoice_id])
    (block,) = blocks(workbook[jde_keying.SHEET])
    assert (field(block, "Supplier number").value, field(block, "Currency").value) == (NEW_NUMBER, "USD")
    assert _money(block, "Gross amount") == invoice.total
    assert (_money(block, "Taxable amount"), _money(block, "Tax amount")) == (None, None)
    assert _lines(block) == [(part.account, part.amount) for part in invoice.parts]
    assert block[-1][3].value == "2,400.00 + 0.00 = 2,400.00: balanced"
    settings = jde.load(store)
    settings.an8_overrides = {}
    jde.save(store, settings)
    batch = store.export_batches()[0]["id"]
    again = client.get(f"/exports/batches/{batch}/download?fmt={jde_keying.FORMAT}")
    held = openpyxl.load_workbook(io.BytesIO(again.content))
    assert blocks(held[jde_keying.SHEET]) == []
    ((number, _, name, *_, why),) = held[jde_keying.NOT_READY].iter_rows(min_row=2, values_only=True)
    assert (number, name) == (invoice_id, invoice.supplier_name) and jde.NO_AN8 in why


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="To settle with the JD Edwards settings: a voucher "
                   "with no tax shows tax explanation code E and the tax area of where it was supplied "
                   "(jde._tax_area_code keeps the area, and no setting leaves both blank), where the made-up "
                   "company's books key neither on a voucher without tax (jde_company.PayItem)")  # fmt: skip
def test_a_voucher_with_no_tax_has_no_tax_code_and_no_tax_area_on_the_sheet(chart_company, chart_company_client):
    client = chart_company_client
    invoice, invoice_id = chart_company.invoices["usd_new_supplier"]
    post(client, f"/review/{invoice_id}/vendor", {"name": invoice.supplier_name, "an8": NEW_NUMBER})
    page = _coded(client, chart_company, "usd_new_supplier")
    page.check()
    if not page.approve(anyway="Being set up in JD Edwards")["ok"]:  # (not the subject: never an expected failure)
        raise RuntimeError("the new supplier's invoice was not approved")
    _, workbook = _sheet(client, chart_company.store, [invoice_id])
    (block,) = blocks(workbook[jde_keying.SHEET])
    assert (field(block, "Tax explanation code").value, field(block, "Tax rate/area").value) == (None, None)
