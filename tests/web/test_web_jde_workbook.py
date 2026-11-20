"""All of JD Edwards' reports in one workbook (ap_coder/web/pages/jde_workbook.py, D-266): the made-up company's
reports queried as the pilot tester's build sheet says (``jde_exports.master_workbook_xlsx``), uploaded at the top of
the JD Edwards files page, each sheet shown with the report it is, then loaded with one click; and each card of the
reports the build sheet added (the G/L lines, the open PO lines, the payment terms, the tax areas)."""

from __future__ import annotations

import io
import re

import openpyxl
import pytest

from ap_coder.ai.supplier_prior import prior_for
from ap_coder.bench import jde_company
from ap_coder.bench import jde_exports as made
from ap_coder.coding import po as po_mod
from ap_coder.core.schema import InvoiceCoding
from ap_coder.erp import import_specs
from tests.webclient import PAGE, follow


@pytest.fixture(scope="module")
def truth():
    return jde_company.build(1, "small", 2024)


@pytest.fixture(scope="module")
def workbook(truth):
    return made.master_workbook_xlsx(truth)


def _token(answer, card: str) -> str:
    assert answer.status_code == 200, answer.text
    return re.search(rf"{card}=([^&#]+)", answer.json()["redirect"]).group(1)


def _upload(client, data: bytes, name: str = made.MASTER_FILE):
    answer = client.post("/jde-files/workbook/upload", files={"file": (name, data)}, headers=PAGE)
    token = _token(answer, "workbook")
    return token, client.get(f"/jde-files?workbook={token}").text


def _summary(page: str) -> dict[str, str]:
    """{sheet: its row's text} of the workbook's summary."""
    table = page.split('id="workbook-sheets"', 1)[1].split("</table>", 1)[0]
    rows = re.findall(r"<tr[^>]*><td><b>([^<]+)</b>(.*?)</tr>", table, re.S)
    return {title: re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", rest)).strip() for title, rest in rows}


def test_each_sheet_is_shown_with_its_report_before_anything_is_loaded(client, store, truth, workbook):
    token, page = _upload(client, workbook)
    shown = _summary(page)
    assert list(shown) == [title for title, _key in made.MASTER_SHEETS] + ["Notes"]
    for title, key in made.MASTER_SHEETS:
        assert import_specs.SPECS[key].title in shown[title] and "Loads" in shown[title], (title, shown[title])
    assert "Not recognised" in shown["Notes"] and "None of the reports" in shown["Notes"]
    assert "column names on row 2" in shown["Accounts"]  # (under the sheet's title row)
    assert f" {len(truth.accounts):,} " in f" {shown['Accounts']} " and f" {len(truth.suppliers):,} " in (
        f" {shown['Suppliers']} ")  # fmt: skip
    items = sum(len(v.pay_items) for v in truth.vouchers if v.gl_date.month <= 6)
    assert f" {items:,} " in f" {shown['Ledger Jan-Jun']} "
    assert "Load all: 10 sheets" in page and not store.chart_accounts() and not store.master_vendor_count()


def test_load_all_loads_every_report_in_order_and_po_matching_and_suggestions_work(client, store, truth, workbook):
    token, _page = _upload(client, workbook)
    page = follow(client, client.post("/jde-files/workbook/load", data={"token": token}, headers=PAGE))
    said = page.split('id="workbook-said"', 1)[1].split("</div></div>", 1)[0]
    assert f"Loaded 10 sheets of {made.MASTER_FILE}." in said, said
    assert store.chart_accounts() and len(store.business_units()) == len(truth.business_units)
    assert store.master_vendor_count() == len(truth.suppliers) and store.vendor_info_count() == len(truth.suppliers)
    held = next(s for s in truth.suppliers if s.hold_payment != "N")
    assert [v["erp_hold"] for v in store.master_vendors() if v["erp_id"] == str(held.number)] == [held.hold_payment]
    assert store.erp_register_count() == len({(v.supplier, v.invoice_number) for v in truth.vouchers if v.gross})
    files = store.journal_files()
    assert len(files) == 2 and len(store.journal_vouchers()) == len(truth.vouchers)
    assert set(store.jde_terms_table()) == {code for code, *_rest in made.TERMS_TABLE}
    lines = made.open_po_lines(truth)
    assert store.open_po_count() == len({li.order for li in lines})
    # PO matching: an invoice of one order's lines (nothing coded yet) is matched to it line by line.
    first = [li for li in lines if li.order == lines[0].order]
    supplier = next(s for s in truth.suppliers if s.number == first[0].supplier)
    invoice = InvoiceCoding.model_validate({
        "vendor_name": supplier.name, "invoice_number": "PO-TEST-1", "invoice_date": "2026-09-14",
        "po_number": str(first[0].order), "currency": "CAD", "confidence_score": 0.9,
        "subtotal": float(sum(li.ordered * li.unit_cost for li in first)), "tax_total": 0.0,
        "grand_total": float(sum(li.ordered * li.unit_cost for li in first)),
        "line_items": [{"line_number": n, "description": li.description, "quantity": float(li.ordered),
                        "unit_price": float(li.unit_cost), "amount": float(li.ordered * li.unit_cost),
                        "predicted_gl_code": ""}
                       for n, li in enumerate(first, 1)]})  # fmt: skip
    codes = [code for _level, code, _text, _line in po_mod.po_findings(invoice, store)]
    # (each invoice line paired with its PO line, whose account it is told; nothing unknown, no vendor mismatch)
    assert codes.count("PO_CODING_DIFFERS") == len(first), codes
    assert not {"PO_UNKNOWN", "PO_LINE_NOT_ON_PO", "PO_VENDOR_MISMATCH", "PO_PRICE_OVER"} & set(codes), codes
    # Suggestions: a supplier's past coding is learned from the G/L lines.
    busy = max(truth.suppliers, key=lambda s: sum(1 for v in truth.vouchers if v.supplier == s.number))
    vendor = next(v for v in store.master_vendors() if v["erp_id"] == str(busy.number))
    prior = prior_for(store, vendor)
    assert prior.journal and prior.found_by == "number" and prior.accounts
    assert prior.accounts[0].account in busy.accounts


def test_two_sheets_of_a_report_replaced_whole_are_left_out_and_said(client, store, workbook):
    """Two business units sheets in one workbook with the same company: neither is loaded (each replaces its
    companies' rows, so one would be lost: D-267); two ledgers add up."""
    book = openpyxl.load_workbook(io.BytesIO(workbook))
    book.copy_worksheet(book["BUs"]).title = "BUs 2"
    out = io.BytesIO()
    book.save(out)
    _token_, page = _upload(client, out.getvalue())
    shown = _summary(page)
    assert (
        "Company 00210 is in another sheet of" in shown["BUs"]
        and "neither is loaded" in shown["BUs"]
        and "Not loaded" in shown["BUs 2"]
    )
    assert "Loads" in shown["Ledger Jan-Jun"] and "Loads" in shown["Ledger Jul-Dec"]


def test_a_chart_split_across_two_sheets_is_one_load_its_company_whole(client, store, truth, workbook):
    """The chart in two sheets, company 00210 in both (D-272): both read and checked, then swapped in once; the page
    says what was read of each, an account in both rejected once as a repeat; the card keeps the summary."""
    book = openpyxl.load_workbook(io.BytesIO(workbook))
    rows = list(book["Accounts"].iter_rows(values_only=True))
    half = (len(rows) + 2) // 2
    second = book.create_sheet("Accounts 2", 1)
    for row in [rows[1], *rows[half - 1 :]]:  # (its column names, then the second half, from the first half's last row)
        second.append(row)
    for _ in range(len(rows) - half):
        book["Accounts"].delete_rows(half + 1)
    out = io.BytesIO()
    book.save(out)
    token, page = _upload(client, out.getvalue())
    shown = _summary(page)
    assert "Loads" in shown["Accounts"] and "Loads" in shown["Accounts 2"]
    page = follow(client, client.post("/jde-files/workbook/load", data={"token": token}, headers=PAGE))
    said = page.split('id="workbook-said"', 1)[1].split("</div></div>", 1)[0]
    assert "Accounts, Accounts 2 (Chart of accounts): Read" in said and " in 2 files (Accounts " in said, said
    assert "Rejected (rows repeating an account already read): 1" in said
    assert len(store.chart_accounts()) == len(truth.accounts)
    card = client.get("/jde-files").text.split('id="chart-read"', 1)[1].split("</ul>", 1)[0]
    assert "in 2 files (Accounts" in card


def test_a_workbook_that_is_none_and_other_files_are_said_in_plain_words(client):
    answer = client.post("/jde-files/workbook/upload", files={"file": ("reports.csv", b"a,b\n1,2\n")}, headers=PAGE)
    assert answer.status_code == 400 and "a sheet for each report" in answer.json()["error"]
    book = openpyxl.Workbook()
    book.active.append(["Vendor", "Amount"])
    book.active.append(["Acme", 12])
    data = io.BytesIO()
    book.save(data)
    _token_, page = _upload(client, data.getvalue(), "other.xlsx")
    assert "No sheet of this workbook can be loaded" in page and "Load all:" not in page


def _sheet_of(workbook: bytes, *titles: str) -> bytes:
    """The workbook with only these sheets (each card's own upload: one report a file)."""
    book = openpyxl.load_workbook(io.BytesIO(workbook))
    for sheet in list(book.worksheets):
        if sheet.title not in titles:
            book.remove(sheet)
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def _card(client, card: str, upload: str, load: str, data: bytes, name: str) -> tuple[str, str]:
    """A file sent to a card and shown, then loaded: (the page showing it, the page after its load)."""
    token = _token(client.post(f"/jde-files/{upload}", files={"file": (name, data)}, headers=PAGE), card)
    shown = client.get(f"/jde-files?{card}={token}").text
    return shown, follow(client, client.post(f"/jde-files/{load}", data={"token": token}, headers=PAGE))


def test_each_new_report_loads_on_its_own_card_too(client, store, truth, workbook):
    """D-266: the cards take the build sheet's reports one by one: the suppliers with their vendor information (card
    3), the G/L lines as a workbook (card 4, two files adding up), the open PO lines (card 6), the payment terms (7)
    and the tax areas (8)."""
    shown, page = _card(client, "suppliers", "suppliers/upload", "suppliers/load", _sheet_of(workbook, "Suppliers"),
                        "suppliers.xlsx")  # fmt: skip
    assert 'id="suppliers-info"' in shown and "Tax ID:" in shown
    assert store.master_vendor_count() == len(truth.suppliers) == store.vendor_info_count()
    assert "with their currencies, terms and addresses" in page
    for title in ("GL lines H1", "GL lines H2"):
        shown, page = _card(client, "journal", "journal/upload", "journal/load", _sheet_of(workbook, title),
                            f"{title}.xlsx")  # fmt: skip
        assert "Past invoice coding (G/L lines)" in shown and "This is the G/L lines of JD Edwards" in shown, shown
    assert len(store.journal_files()) == 2 and len(store.journal_vouchers()) == len(truth.vouchers)
    shown, page = _card(client, "orders", "orders/upload-table", "orders/load-table", _sheet_of(workbook, "Open POs"),
                        "open_pos.xlsx")  # fmt: skip
    lines = made.open_po_lines(truth)
    assert f"{len(lines)} open lines of {len({li.order for li in lines})} orders to load" in shown
    assert store.open_po_count() == len({li.order for li in lines}) and "Open purchase order lines:" in page
    shown, page = _card(client, "terms", "terms/upload-table", "terms/load-table", _sheet_of(workbook, "Terms"),
                        "terms.xlsx")  # fmt: skip
    assert "4 terms codes to load" in shown and set(store.jde_terms_table()) == {"N", "F", "T", "E"}
    assert 'id="terms-table"' in page and "A supplier&#39;s terms code now counts as its terms" in page
    shown, page = _card(client, "areas", "areas/upload-table", "areas/load-table", _sheet_of(workbook, "Tax areas"),
                        "areas.xlsx")  # fmt: skip
    assert "5 tax areas to load" in shown and 'id="areas-said"' in page
    assert "MB: the tax area MB-GSTPST in Settings is not one of JD Edwards" in page


def test_the_payment_terms_card_says_each_codes_rule_and_those_not_worked_out(client, store):
    """B-2834 (D-302): F0014's proximate date, fixed due date and split payments, titled as the build sheet says, are
    read; the card lists each code as it is worked out, and its load says which ones are not, never guessed."""
    from tests.coding.test_terms import F0014_RULES

    head = [title for _field, title in made.BUILD_SHEET["payment_terms"]]
    order = ["PNPTC", "PNPTD", "PNDCP", "PNDCD", "PNNDTP", "PNDDJ", "PNPXDM", "PNPXDD", "PNNSP", "PNDTPA"]
    rows = [dict(zip(order, (r[0], r[1], r[2], r[3], r[4], r[5], r[8], r[9], r[6], r[7]), strict=True))
            for r in F0014_RULES]  # fmt: skip
    book = openpyxl.Workbook()
    book.active.append(head)
    for row in rows:
        book.active.append([row[f] for f, _title in made.BUILD_SHEET["payment_terms"]])
    data = io.BytesIO()
    book.save(data)
    _shown, page = _card(client, "terms", "terms/upload-table", "terms/load-table", data.getvalue(), "terms.xlsx")
    assert set(store.jde_terms_table()) == {r[0] for r in F0014_RULES}
    assert (
        "A supplier&#39;s terms code now counts as its terms: E Net 31st prox; M Due end of month; P Net 15th "
        "prox; S 2/10 Net 30 in 3 payments 30 days apart; Y Due 2026-12-31."
    ) in page
    assert (
        "Not worked out as JD Edwards does, so an invoice of these is due by the days to pay (Settings → Review) "
        "and says to check its due date: D: its due date “126365” is not a date AP Coder reads; Q: proximate month "
        "3, day 15; R: a proximate date with a discount; V: 2 split payments with no days between them."
    ) in page
    listed = page.split('id="terms-table"', 1)[1].split("</details>", 1)[0]
    assert '<b>E</b> End of next month <span class="muted">· Net 31st prox</span>' in listed
    assert (
        '<b>Q</b> 15th, 3 months on <span class="muted">· rule not supported (proximate month 3, day 15)</span>'
        in listed
    )


def test_a_report_on_another_card_is_named_with_its_card(client, workbook):
    """The open PO lines dropped on the business units' card, or the payment terms on the tax areas', say which card
    they go on; nothing is loaded."""
    data = _sheet_of(workbook, "Terms")
    token = _token(
        client.post("/jde-files/areas/upload-table", files={"file": ("t.xlsx", data)}, headers=PAGE), "areas"
    )
    shown = client.get(f"/jde-files?areas={token}").text
    assert "load it under 7. Payment terms" in shown
    refused = client.post("/jde-files/areas/load-table", data={"token": token}, headers=PAGE)
    assert refused.status_code == 400 and "7. Payment terms" in refused.json()["error"]


def test_in_a_browser_the_workbook_is_dropped_shown_and_loaded_on_a_laptop(chromium, workbook, tmp_path):
    """At 1366 x 768 and 390 x 844: the workbook's box is the first thing on the page; after Check its summary and
    Load all are shown, the sheets' table within the page's width; Load all says what it loaded."""
    from ap_coder.core.store import Store
    from ap_coder.web.app import create_app
    from tests.browser import browser_on
    from tests.webclient import web_settings

    path = tmp_path / made.MASTER_FILE
    path.write_bytes(workbook)
    for width, height in ((1366, 768), (390, 844)):
        store = Store(tmp_path / f"{width}.db")
        with browser_on(chromium, create_app(web_settings(), store, start_reader=False)) as (browser, base):
            page = browser.new_page(viewport={"width": width, "height": height})
            page.goto(base + "/jde-files")
            first = page.locator("main section.card").first
            assert first.get_attribute("id") == "workbook"
            page.locator("#workbook input[type=file]").set_input_files(str(path))
            page.locator("#workbook .imp-upload button[type=submit]").click()
            page.wait_for_selector("#workbook-summary")
            page.wait_for_load_state("load")
            load = page.locator("#workbook-summary button[data-load]")
            assert load.inner_text().strip().endswith("Load all: 10 sheets")
            wide = page.evaluate("document.documentElement.scrollWidth")
            assert wide <= width + 1, (width, wide)
            load.click()
            page.wait_for_selector("#workbook-said", timeout=120_000)
            assert "Loaded 10 sheets" in page.locator("#workbook-said").inner_text()
            assert store.master_vendor_count() and store.journal_files()
            page.close()
