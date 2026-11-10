"""A day on the made-up company (the ``chart_company`` fixture: a chart of accounts, last year's journal, JD Edwards
set up for vouchers typed by hand): the queue, each of the seven invoices on the review screen, how each line arrives
(coded from last year's journal, or left with why and the accounts offered: D-212), the chart's checks, approving,
and the keying sheet and batch files made from what was approved (D-210).

These are the end-to-end tests of an install with a chart loaded, on today's code. A part that is not built yet has
its test ``xfail(strict=True)`` with what is missing: it fails the run the day it starts passing, so the build that
brings it takes the mark off."""

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
from tests.erp.test_jde_keying import LINES, blocks, field, said
from tests.review_page import ReviewPage
from tests.webclient import PAGE, follow, post

NEW_NUMBER = "99001"  # the new supplier's JD Edwards address number, once it is set up
TAX_WORDS = {"quebec": "GST + QST", "usd_new_supplier": "No tax charged"}  # each line's tax code as shown; else GST
# Why a line is left for a person (``suggest.why_left``, D-212), as the line says it.
SEVERAL = "This supplier's {lines} lines last year went to {accounts} accounts: {shares}: pick the account."
TOO_FEW = "Only {counted} last year went to {account}: too few to code it alone: pick the account."
NEW_SUPPLIER = "A new supplier: not in last year's journal: pick the account."
PICK = "Pick a GL account for {lines} and a cost center for {lines} to approve"  # why Approve is closed


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


def _check(block):
    """A voucher's check line: under its G/L lines, above its warnings and notes."""
    return next(row for row in block[LINES:] if row[0].value == "Check")


def _rows(block) -> list:
    """A voucher's G/L lines as rows of cells: under the row "G/L distribution", above the check line."""
    return block[LINES : block.index(_check(block))]


def _lines(block) -> list[tuple[str, Decimal]]:
    """A voucher's G/L lines: (the account string, the amount)."""
    return [(row[0].value, _cents(row[2].value)) for row in _rows(block)]


def _shown(invoice) -> list[str]:
    """The description of each G/L line as the review screen has it: as printed, a share of a split line with its
    percentage."""
    shown = []
    for line in invoice.lines:
        whole = sum(part.amount for part in line.parts)
        shown += [line.description if len(line.parts) == 1 else f"{line.description} ({part.amount * 100 / whole:.0f}%)"
                  for part in line.parts]  # fmt: skip
    return shown


def _left(invoice_lines: list[dict], reason: str, first: list[str]) -> None:
    """Each of these stored lines was left for a person: no account, nothing recorded as deciding it, ``reason``,
    and accounts offered of which the first is ``first``'s (in the lines' order)."""
    assert [line["predicted_gl_code"] for line in invoice_lines] == ["UNASSIGNED"] * len(first)
    assert [line["reasoning_justification"] for line in invoice_lines] == [reason] * len(first)
    assert not any("decided_by" in line for line in invoice_lines)  # the key itself is left out
    assert [line["candidates"][0]["account"] for line in invoice_lines] == first


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


# How each line arrives (D-212): coded with no one picking when one account has 90% of three or more of the supplier's
# lines in last year's journal, else left for a person with why and the accounts offered. The supplier is found by its
# JD Edwards number (the supplier master's), in the journal loaded before the invoices were read.


def _arrived(chart_company, client, name: str):
    """(the invoice with what AP should key, its review screen, its lines as the pipeline stored them)."""
    invoice, invoice_id = chart_company.invoices[name]
    stored = chart_company.store.get_invoice(invoice_id)["ai_output"]["line_items"]
    return invoice, ReviewPage(client, invoice_id), stored


def _last_year(truth, supplier) -> list[str]:
    """The accounts of the supplier's coding lines in last year's journal (a voucher that does not balance is not in
    it), the most used first."""
    used = [line.account for voucher in truth.vouchers if voucher.supplier == supplier.number
            and voucher.oddity != "unbalanced" for line in voucher.lines]  # fmt: skip
    return sorted(used, key=lambda account: (-used.count(account), used.index(account)))


@pytest.mark.parametrize("name, lines", [("one_line", 8), ("quebec", 6)])
def test_an_invoice_of_a_supplier_always_coded_to_one_account_arrives_coded_and_is_approved_as_it_is(
    chart_company, chart_company_client, name, lines
):
    invoice, page, stored = _arrived(chart_company, chart_company_client, name)
    assert _last_year(chart_company.truth, invoice.supplier) == [invoice.parts[0].account] * lines
    assert [row.account for row in page.lines] == [line.parts[0].account for line in invoice.lines]
    counted = f"{lines} of this supplier's {lines} lines last year"
    assert all(row.reason == counted or row.reason.startswith(f"{counted}; ") for row in page.lines)
    by = {"source": "supplier", "lines": lines, "of": lines, "share": 1.0}
    assert [line["decided_by"] for line in stored] == [by] * len(invoice.lines)
    assert not any("candidates" in line for line in stored)  # the key itself is left out
    assert (page.findings, page.not_ready) == ({}, "")
    assert page.check()["allow"] and page.approve()["ok"]  # nothing picked, nothing typed
    assert chart_company.store.get_invoice(page.id)["status"] == APPROVED


def test_the_four_line_invoice_is_left_with_the_account_of_each_line_offered_first(chart_company, chart_company_client):
    """Its supplier's three lines last year went to three accounts, one each: no account has 90%, so no line is coded
    with no one picking, the lines that name whose move it is no more than the one that says nothing of what was
    bought. Each says why, and the account offered first is the one AP keys: the named person's, and for the vague
    line the supplier's latest (the billback), with the supplier's other two next."""
    invoice, page, stored = _arrived(chart_company, chart_company_client, "four_lines")
    his, hers, billback = invoice.supplier.accounts
    assert sorted(_last_year(chart_company.truth, invoice.supplier)) == sorted([his, hers, billback])
    reason = SEVERAL.format(lines=3, accounts=3, shares="33% / 33% / 33%")
    assert [(row.account, row.reason) for row in page.lines] == [("", reason)] * 4
    _left(stored, reason, [his, his, hers, billback])
    assert [line.parts[0].account for line in invoice.lines] == [his, his, hers, billback]
    assert all({c["account"] for c in line["candidates"][:3]} == {his, hers, billback} for line in stored)
    assert all(c["source"] == "supplier" and (c["lines"], c["of"]) == (1, 3)
               for line in stored for c in line["candidates"][:3])  # fmt: skip
    assert list(page.findings) == ["GL_UNASSIGNED", "CC_UNASSIGNED"]
    assert page.not_ready == PICK.format(lines="4 lines") + " (or use Apply to all lines…)"


def test_the_fifteen_line_invoice_is_left_with_the_suppliers_most_used_business_units_offered(
    chart_company, chart_company_client
):
    """One object.subsidiary on thirteen business units (who ordered decides): no line is coded with no one picking.
    Each keeps five of the supplier's accounts, the most used first whichever business unit the line names: the
    right account is first on one line of the fifteen, among the five on seven, and not offered on eight."""
    invoice, page, stored = _arrived(chart_company, chart_company_client, "fifteen_lines")
    history = _last_year(chart_company.truth, invoice.supplier)
    assert (len(history), len(set(history))) == (23, 13)
    reason = SEVERAL.format(lines=23, accounts=13, shares="26% / 13% / 9% / 9% / ...")
    assert [(row.account, row.reason) for row in page.lines] == [("", reason)] * 15
    _left(stored, reason, [history[0]] * 15)
    offered = [[c["account"] for c in line["candidates"]] for line in stored]
    assert all(len(found) == 5 and set(found) <= set(invoice.supplier.accounts) for found in offered)
    right = [line.parts[0].account for line in invoice.lines]
    place = [found.index(account) + 1 if account in found else 0 for found, account in zip(offered, right, strict=True)]
    assert place == [2, 4, 1, 0, 0, 0, 0, 0, 3, 0, 5, 0, 0, 2, 4]  # 0: not among the five kept
    assert list(page.findings) == ["GL_UNASSIGNED", "CC_UNASSIGNED"]
    assert page.not_ready == PICK.format(lines="15 lines") + " (or use Apply to all lines…)"


@pytest.mark.parametrize(
    "name, counted, to_pick",
    [
        ("split_line", "1 of this supplier's 1 line", "lines 1, 2"),
        ("credit_note", "2 of this supplier's 2 lines", "line 1"),
    ],
)
def test_an_invoice_of_a_supplier_with_under_three_lines_last_year_is_left_with_its_account_offered(
    chart_company, chart_company_client, name, counted, to_pick
):
    """One account last year, on fewer lines than code a line with no one picking (``supplier_prior.SURE_LINES``):
    each line says so and offers that account first. It is right, for the split line as its larger part."""
    invoice, page, stored = _arrived(chart_company, chart_company_client, name)
    account = invoice.parts[0].account
    assert set(_last_year(chart_company.truth, invoice.supplier)) == {account}
    reason = TOO_FEW.format(counted=counted, account=account)
    assert [(row.account, row.reason) for row in page.lines] == [("", reason)] * len(invoice.lines)
    _left(stored, reason, [account] * len(invoice.lines))
    assert [line.parts[0].account for line in invoice.lines] == [account] * len(invoice.lines)
    assert list(page.findings) == ["GL_UNASSIGNED", "CC_UNASSIGNED"]
    assert page.not_ready == PICK.format(lines=to_pick)
    assert page.approve() == {"ok": False, "error": page.not_ready + "."}


def test_the_new_suppliers_line_is_left_with_no_account_offered(chart_company, chart_company_client):
    """A supplier that is in neither the supplier master nor last year's journal: its line says so, and the words it
    prints ("SaaS seats x 3 - annual") are near no account's name, so nothing is offered for it."""
    invoice, page, stored = _arrived(chart_company, chart_company_client, "usd_new_supplier")
    assert [(row.account, row.reason) for row in page.lines] == [("", NEW_SUPPLIER)]
    assert [line["predicted_gl_code"] for line in stored] == ["UNASSIGNED"]
    assert not any("candidates" in line or "decided_by" in line for line in stored)
    assert list(page.findings) == ["GL_UNASSIGNED", "CC_UNASSIGNED", "VENDOR_NOT_IN_MASTER", "JDE_NO_AN8",
                                   "NO_TAX_CHARGED"]  # fmt: skip
    assert page.not_ready == "JD Edwards: this vendor has no supplier number (AN8)"


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
    number, the company, the invoice, the gross, the terms code and the due date, tax explanation code V with the
    company's tax area, the taxable amount and the tax recovered), then under "G/L distribution" its G/L lines (the
    account string a text cell, the amount as keyed) and a check line that balances, with a note only where a tax
    stays in the expense. Nothing is on "Not ready"."""
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
        credit = name == "credit_note"  # no terms: due the day it is dated
        assert field(block, "Payment terms").value == (None if credit else company.payment_terms), name
        assert field(block, "Due date").value == day + dt.timedelta(days=0 if credit else jde_invoices.TERMS.days), name
        of = field(block, "Company")
        assert (of.value, of.data_type, of.number_format) == (company.number, "s", "@"), name
        assert (field(block, "Tax explanation code").value, field(block, "Tax rate/area").value) == (
            "V", company.tax_area), name  # fmt: skip
        assert (_money(block, "Taxable amount"), _money(block, "Tax amount")) == (
            invoice.subtotal, invoice.recovered), name  # fmt: skip
        assert [cell.value for cell in block[LINES - 1]] == ["G/L distribution", None, None, None], name
        lines, check = _rows(block), _check(block)
        assert _lines(block) == [(part.account, part.amount) for part in invoice.parts], name
        assert {(a.data_type, a.number_format) for a, _, _, _ in lines} == {("s", "@")}, name  # text: zeros kept
        assert all(description.value for _, description, _, _ in lines), name  # the account's name from the chart
        # The remark is the line's own (D-213): the one approved with it (the supplier's usual remark for the
        # account, else the invoice line's words), 30 characters at most.
        approved = store.get_invoice(chart_company.invoices[name][1])["final_output"]["line_items"]
        assert all(0 < len(li["remark"]) <= 30 for li in approved), name
        assert [remark.value for _, _, _, remark in lines] == [li["remark"] for li in approved], name
        assert field(block, "Remark").value == lines[0][3].value, name
        assert check[0].value == "Check" and _cents(check[2].value) == invoice.total - invoice.recovered, name
        assert check[3].value.endswith(": balanced"), name
        kept = [f"The QST ({tax.amount:,.2f}) is not recovered: it is inside the G/L lines, not in the tax or taxable "
                "amount." for tax in invoice.taxes if tax.code == "QST"]  # fmt: skip
        assert (said(block, "Warning"), said(block, "Note")) == ([], kept), name
        assert kept or block[-1] is check, name  # nothing under the check line but that note
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
    assert _check(block)[3].value == "1,363.69 + 62.00 = 1,425.69: balanced"
    assert said(block, "Note") == ["The QST (123.69) is not recovered: it is inside the G/L lines, not in the tax "
                                   "or taxable amount."]  # fmt: skip
    credit = chart_company.invoices["credit_note"][0]
    block = found[credit.number]
    amounts = [_money(block, label) for label in ("Gross amount", "Taxable amount", "Tax amount")]
    assert amounts == [credit.total, credit.subtotal, credit.recovered] and all(amount < 0 for amount in amounts)
    assert _lines(block) == [(part.account, part.amount) for part in credit.parts]
    assert _check(block)[3].value == "-578.00 + -28.90 = -606.90: balanced" and block[-1] is _check(block)


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
            field(block, "Tax explanation code").value, field(block, "Tax rate/area").value,
            field(block, "Company").value)  # fmt: skip
        assert (head["VLATXA"], head["VLSTAM"]) == (
            cents(field(block, "Taxable amount").value), cents(field(block, "Tax amount").value))  # fmt: skip
        gross = field(block, "Gross amount").value  # not sent: taxable + not taxable + tax add up to it
        assert int(head["VLATXA"]) + int(head["VLATXN"]) + int(head["VLSTAM"]) == round(gross * 100)
        assert head["VLDGJ"] == str(jde.julian(field(block, "G/L date").value))
        assert head["VLRMK"] == field(block, "Remark").value  # the remarks as typed (the settings' choice)
    typed = [(row[0].value, cents(row[2].value), row[3].value) for block in sheet for row in _rows(block)]
    assert typed == [(line["VNANI"], line["VNAA"], line["VNEXA"]) for line in lines]


# --- the new supplier, in USD -------------------------------------------------------------------------------------


def test_the_new_suppliers_invoice_cannot_be_approved_until_the_supplier_has_its_number(
    chart_company, chart_company_client
):
    """A supplier that is not in the supplier master has no JD Edwards number: its invoice is held on the review
    screen, which says so, and so never reaches a keying sheet: a keying batch takes only invoices that pass the JD
    Edwards checks, and a batch of none is refused (D-210)."""
    invoice, invoice_id = chart_company.invoices["usd_new_supplier"]
    page = _coded(chart_company_client, chart_company, "usd_new_supplier")
    assert not page.check()["allow"]
    assert {"VENDOR_NOT_IN_MASTER", "JDE_NO_AN8"} <= set(page.findings)
    assert page.not_ready == "JD Edwards: this vendor has no supplier number (AN8)"
    assert page.approve() == {"ok": False, "error": f"Not ready: {page.not_ready}."}
    refused = post(chart_company_client, "/exports/create", {"fmt": jde_keying.FORMAT, "ids": [invoice_id]})
    assert (refused.status_code, refused.json()) == (400, {
        "ok": False,
        "error": "None of the ticked invoices can go to JD Edwards yet: fix them first (see the list)."})  # fmt: skip
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
    assert _check(block)[3].value == "2,400.00 + 0.00 = 2,400.00: balanced"
    assert (said(block, "Warning"), said(block, "Note")) == ([], [
        "USD voucher: type the amounts as shown, in USD (foreign entry), not in CAD. JD Edwards takes the exchange "
        "rate from its own table."])  # fmt: skip
    settings = jde.load(store)
    settings.an8_overrides = {}
    jde.save(store, settings)
    batch = store.export_batches()[0]["id"]
    again = client.get(f"/exports/batches/{batch}/download?fmt={jde_keying.FORMAT}")
    held = openpyxl.load_workbook(io.BytesIO(again.content))
    ((row,),) = blocks(held[jde_keying.SHEET])  # no voucher: one row that says where it went
    assert [cell.value for cell in row] == ["No voucher to type: see the sheet “Not ready”.", None, None, None]
    ((number, _, name, *_, why),) = held[jde_keying.NOT_READY].iter_rows(min_row=2, values_only=True)
    assert (number, name) == (invoice_id, invoice.supplier_name) and jde.NO_AN8 in why


def test_a_voucher_with_no_tax_goes_with_the_exempt_code_on_the_tax_area_of_where_it_was_supplied(
    chart_company, chart_company_client
):
    """A voucher with no tax on its pay item goes with the exempt code (D-210), E, and keeps the tax rate/area of the
    province it was supplied to (``jde._tax_area_code``): the US supplier's invoice billed to head office shows E and
    the company's GST area, with no taxable and no tax amount. Still to ask the JDE team: the made-up company's own
    books key neither a code nor an area on a voucher without tax (``jde_company.PayItem``, ``Invoice.coding``), and
    no setting leaves both blank."""
    client, company = chart_company_client, chart_company.truth.company
    invoice, invoice_id = chart_company.invoices["usd_new_supplier"]
    post(client, f"/review/{invoice_id}/vendor", {"name": invoice.supplier_name, "an8": NEW_NUMBER})
    page = _coded(client, chart_company, "usd_new_supplier")
    page.check()
    assert page.approve(anyway="Being set up in JD Edwards")["ok"]
    _, workbook = _sheet(client, chart_company.store, [invoice_id])
    (block,) = blocks(workbook[jde_keying.SHEET])
    assert not invoice.taxes and invoice.coding()["tax_code"] == invoice.coding()["tax_area"] == ""  # the books' way
    assert (field(block, "Tax explanation code").value, field(block, "Tax rate/area").value) == (
        jde.JdeSettings().exempt_code, company.tax_area) == ("E", "CDNGST")  # fmt: skip
    assert (field(block, "Taxable amount").value, field(block, "Tax amount").value) == (None, None)
