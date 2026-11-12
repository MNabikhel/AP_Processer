"""Every import outside JD Edwards files, as its page shows it (D-234's specs, ``erp/import_specs``): the page says the
file it wants and its columns with a blank template, shows an uploaded file's header check (each column found as which
header, or missing) above its first rows, starts each column choice from the headers, keeps the import closed while a
needed field has no column, and the server refuses the same, naming it. Columns in another order import the same."""

from __future__ import annotations

import io
import re
from urllib.parse import parse_qs, urlsplit

import openpyxl
import pytest
from markupsafe import escape

from ap_coder.core.store import load_sample_setup
from ap_coder.erp import import_specs
from ap_coder.erp.import_specs import SPECS
from tests.webclient import PAGE, client_for, post


def _xlsx(rows: list[list[str]], sheet: str = "Sheet1") -> bytes:
    book = openpyxl.Workbook()
    book.active.title = sheet
    for row in rows:
        book.active.append(row)
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


# Each page's import: (where its file is uploaded, the import, the upload's query name and the import's token field,
# where the page is, a file of its columns as their spec writes them, what the store holds after it).
PAGES = {
    "gl_accounts": ("/accounts/gl_accounts/upload", "/accounts/gl_accounts/import", "file", "file", "/accounts",
                    [["Code", "Description", "Category", "Meals"], ["6010", "IT Hardware", "Opex", ""],
                     ["6400", "Meals", "Opex", "yes"]],
                    lambda s: sorted((a["code"], a["description"], a["category"], bool(a["meals"]))
                                     for a in s.list_accounts("gl_accounts"))),
    "cost_centers": ("/accounts/cost_centers/upload", "/accounts/cost_centers/import", "file", "file",
                     "/accounts?tab=cc", [["Code", "Description", "Category"], ["100", "Admin", "Overhead"]],
                     lambda s: sorted((a["code"], a["description"]) for a in s.list_accounts("cost_centers"))),
    "bank_accounts": ("/accounts/bank/upload", "/accounts/bank/import", "file", "file", "/accounts?tab=bank",
                      [["Code", "Name", "Currency"], ["CAD-OP", "Operating", "CAD"], ["USD-OP", "US", "USD"]],
                      lambda s: sorted((a["code"], a["name"], a["currency"]) for a in s.bank_accounts())),
    "vendor_master": ("/vendors/master/upload", "/vendors/master/import", "file", "file", "/vendors",
                      [["Vendor Name", "Vendor ID", "Payment Terms"], ["Acme Ltd", "V1", "Net 30"]],
                      lambda s: sorted((v["display_name"], v["erp_id"], v["terms"]) for v in s.master_vendors())),
    "purchase_orders": ("/purchase-orders/upload", "/purchase-orders/import", "file", "file", "/purchase-orders",
                        [["PO Number", "Description", "Amount", "Vendor"], ["PO-1", "Laptops", "2000", "Acme Ltd"]],
                        lambda s: sorted((p["po_number"], p["vendor_name"], p["total"])
                                         for p in s.purchase_orders())),
    "erp_register": ("/exports/register/upload", "/exports/register/import", "register", "file", "/exports",
                     [["Vendor", "Invoice Number", "Total", "Invoice Date"], ["Acme Ltd", "A-1", "100.00",
                                                                             "2026-01-05"]],
                     lambda s: sorted((r["vendor_name"], r["invoice_number"], r["total"]) for r in s.erp_register())),
    "past_coding": ("/learning/history/upload", "/learning/history/teach", "history", "token", "/learning",
                    [["Vendor", "Description", "GL Code", "Cost Center"], ["Acme Ltd", "Paper", "6010", ""]],
                    lambda s: s.history_count()),
}  # fmt: skip


@pytest.fixture
def client(store):
    load_sample_setup(store)  # (GL accounts: a bank account's cash GL and the past coding's accounts are checked)
    return client_for(store)


def _upload(client, key: str, rows: list[list[str]]):
    """The page an uploaded file of ``rows`` is shown on, and its token."""
    url, _import, name, _field, _page, _rows, _held = PAGES[key]
    answer = client.post(url, files={"file": ("list.xlsx", _xlsx(rows))}, headers=PAGE)
    assert answer.status_code == 200, answer.text
    redirect = answer.json()["redirect"]
    return client.get(redirect).text, parse_qs(urlsplit(redirect).query)[name][0]


def _offered(html: str) -> dict[str, str]:
    """{field: the column the page offers already chosen} of the column choices ("" for none)."""
    chosen = {}
    for field, options in re.findall(r'<select name="col_(\w+)">(.*?)</select>', html, re.S):
        picked = re.search(r'<option value="([^"]*)" selected>', options)
        chosen[field] = picked.group(1) if picked else ""
    return chosen


def _import(client, key: str, token: str, html: str):
    _url, url, _name, field, _page, _rows, _held = PAGES[key]
    picked = {f"col_{f}": c for f, c in _offered(html).items() if c}
    return post(client, url, {field: token, **picked})


def _check(html: str, key: str) -> str:
    """The header check of the file shown (``_import.html``'s preview)."""
    return html.split(f'aria-label="The columns of {escape(SPECS[key].title)} in this file"', 1)[1].split("</ul>", 1)[0]


@pytest.mark.parametrize("key", list(PAGES))
def test_the_page_says_what_file_it_wants_its_columns_and_gives_a_template(client, key):
    spec, page = SPECS[key], client.get(PAGES[key][4]).text
    cols = page.split(f'id="cols-{key}"', 1)[1].split("</details>", 1)[0]
    assert all(f'<span class="imp-chip need" title="{c.label}">' in cols for c in spec.required), key
    assert "How to get it:" in page and "data-imp-drop" in page and "data-imp-chosen" in page
    assert f'href="/imports/template/{key}" download' in page
    answer = client.get(f"/imports/template/{key}")
    assert answer.status_code == 200 and "attachment" in answer.headers["content-disposition"]
    header = next(openpyxl.load_workbook(io.BytesIO(answer.content)).active.iter_rows(values_only=True))
    assert list(header) == [c.header for c in spec.columns] and import_specs.check(spec, header).ok


@pytest.mark.parametrize("key", list(PAGES))
def test_an_uploaded_file_shows_each_column_found_or_missing_and_is_refused_without_a_needed_one(client, store, key):
    spec, rows = SPECS[key], PAGES[key][5]
    html, _token = _upload(client, key, rows)
    shown = _check(html, key)
    for c in spec.columns:
        if (header := c.header) in rows[0]:
            assert f'<b>{escape(c.label)}</b> <span class="muted">as</span> “{header}”' in shown, (key, c.field)
    assert "missing:" not in shown and "Every column" in html and " data-imp-go disabled" not in html
    assert f"<td>{rows[1][0]}</td>" in html  # its first rows, under the check
    needed = spec.required[0]
    without = [[cell for cell, h in zip(row, rows[0], strict=True) if h != needed.header] for row in rows]
    html, token = _upload(client, key, without)
    assert f"missing: “{needed.header}”" in _check(html, key)
    assert f"Not found by its name: {needed.label}." in html and " data-imp-go disabled" in html
    assert re.search(rf"Pick the column for: {re.escape(needed.label)}\.", html)
    before = PAGES[key][6](store)
    refused = _import(client, key, token, html)
    assert refused.status_code == 400 and refused.json()["error"] == (f"Pick the column for: {needed.label}. Nothing "
                                                                      "was imported.")  # fmt: skip
    assert PAGES[key][6](store) == before


@pytest.mark.parametrize("key", list(PAGES))
def test_columns_in_another_order_are_offered_and_imported_the_same(client, store, tmp_path, key):
    rows = PAGES[key][5]
    held = []
    for order in (list(range(len(rows[0]))), list(range(len(rows[0])))[::-1]):
        html, token = _upload(client, key, [[row[i] for i in order] for row in rows])
        offered = {f: c for f, c in _offered(html).items() if c}
        answer = _import(client, key, token, html)
        assert answer.status_code == 200 and answer.json()["ok"], answer.text
        held.append((offered, PAGES[key][6](store)))
    assert held[0] == held[1] and held[0][1]


def test_the_template_route_serves_every_import_that_has_one_and_jde_files_keeps_its_own(client):
    for key, spec in SPECS.items():
        assert client.get(f"/imports/template/{key}").status_code == (404 if spec.no_template else 200), key
    assert client.get("/imports/template/nothing").status_code == 404
    assert client.get("/jde-files/template/chart").status_code == 200
    assert client.get("/jde-files/template/gl_accounts").status_code == 404  # (not a JD Edwards export)


def test_the_jd_edwards_voucher_list_on_exports_is_checked_as_jd_edwards_files_checks_it(client, store):
    from tests.jde_exports import sample_pay_items, voucher_headers

    answer = client.post("/exports/register/upload", files={"file": ("v.xlsx", voucher_headers(sample_pay_items()))},
                         headers=PAGE)  # fmt: skip
    html = client.get(answer.json()["redirect"]).text
    assert "every column it needs is here." in html and 'name="col_' not in html
    assert all(f"<b>{c.label}</b>" in _check(html, "vouchers") for c in SPECS["vouchers"].required)


def test_a_statement_shows_its_columns_and_is_compared_only_with_an_amount_column(store, setup_demo_template):
    from tests.conftest import fill_from
    from tests.web.test_web_finance import _statement

    client = client_for(fill_from(store, setup_demo_template))
    key, content = _statement(store)
    page = client.get("/statements").text
    assert 'id="cols-statements"' in page and "/imports/template/statements" not in page  # (the supplier's own)
    answer = client.post("/statements/upload", data={"vendor": key}, files={"file": ("s.csv", content)}, headers=PAGE)
    redirect = answer.json()["redirect"]
    html = client.get(redirect).text
    assert '<b>Invoice number</b> <span class="muted">as</span> “Invoice No”' in _check(html, "statements")
    assert "Matched" in html
    token = parse_qs(urlsplit(redirect).query)["file"][0]
    html = client.get("/statements", params={"vendor": key, "file": token, "col_number": "Invoice No",
                                             "col_amount": ""}).text  # fmt: skip
    assert "Pick the column for: one of Amount, Credit or Open balance." in html and " data-imp-go disabled" in html
    assert "Matched" not in html


# --- Settings → JD Edwards: the GL map's file and the JDE team's worksheet, shown before they are imported -----------


def test_the_gl_map_is_shown_then_imported_and_one_without_its_gl_code_column_is_refused(client, store):
    from ap_coder.erp import jde_settings

    page, before = client.get("/settings/jde").text, jde_settings.load(store).gl_map
    assert 'id="cols-gl_map"' in page and 'href="/imports/template/gl_map" download' in page
    gl_map = before
    for order in ([0, 1, 2, 3], [3, 2, 1, 0]):
        rows = [["GL Code", "Cost Center", "BU", "Object"], ["6010", "", "100", "6010"], ["6400", "OPS", "200", ""]]
        answer = client.post("/settings/jde/gl-map/upload",
                             files={"file": ("m.xlsx", _xlsx([[r[i] for i in order] for r in rows]))},
                             headers=PAGE)  # fmt: skip
        html = client.get(answer.json()["redirect"]).text
        assert '<b>GL code</b> <span class="muted">as</span> “GL Code”' in _check(html, "gl_map")
        assert "2 mapping rows to import." in html and jde_settings.load(store).gl_map in (before, gl_map)
        token = parse_qs(urlsplit(answer.json()["redirect"]).query)["gl_map"][0]
        done = post(client, "/settings/jde/gl-map/import", {"token": token, "replace": "1"})
        assert done.status_code == 200, done.text
        gl_map = jde_settings.load(store).gl_map
        assert [(m.gl_code, m.cost_center, m.bu, m.obj) for m in gl_map] == [("6010", "", "100", "6010"),
                                                                            ("6400", "OPS", "200", "")]  # fmt: skip
    answer = client.post("/settings/jde/gl-map/upload", files={"file": ("m.xlsx", _xlsx([["BU", "Object"],
                                                                                         ["100", "6010"]]))},
                         headers=PAGE)  # fmt: skip
    html = client.get(answer.json()["redirect"]).text
    assert "missing: “GL Code”" in _check(html, "gl_map") and "this file has no “GL Code” (GL code) column" in html
    assert "Import 0" not in html and 'name="replace"' not in html.split('id="gl-map-import"', 1)[1]
    token = parse_qs(urlsplit(answer.json()["redirect"]).query)["gl_map"][0]
    refused = post(client, "/settings/jde/gl-map/import", {"token": token})
    assert refused.status_code == 400 and "“GL Code”" in refused.json()["error"]


def _worksheet(client, answers: dict[str, str], drop: str = "") -> bytes:
    book = openpyxl.load_workbook(io.BytesIO(client.get("/settings/jde/worksheet.xlsx").content))
    sheet = book["Questions"]
    header = [c.value for c in sheet[1]]
    for row in sheet.iter_rows(min_row=2):
        if row[0].value in answers:
            row[header.index("Your answer")].value = answers[row[0].value]
    if drop:
        sheet.delete_cols(header.index(drop) + 1)
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def test_the_worksheet_shows_what_it_would_apply_then_applies_it_and_one_without_its_answers_is_refused(client, store):
    from ap_coder.erp import jde_settings

    sheet = _worksheet(client, {"company": "00042"})
    answer = client.post("/settings/jde/worksheet/upload", files={"file": ("w.xlsx", sheet)}, headers=PAGE)
    html = client.get(answer.json()["redirect"]).text
    assert '<b>Your answer</b> <span class="muted">as</span> “Your answer”' in _check(html, "jde_worksheet")
    assert "1 answer to apply:" in html and "00042" in html and jde_settings.load(store).company != "00042"
    token = parse_qs(urlsplit(answer.json()["redirect"]).query)["worksheet"][0]
    assert post(client, "/settings/jde/worksheet", {"token": token}).status_code == 200
    assert jde_settings.load(store).company == "00042"
    answer = client.post("/settings/jde/worksheet/upload", headers=PAGE, files={"file": ("w.xlsx", _worksheet(
        client, {"company": "00043"}, drop="Your answer"))})  # fmt: skip
    html = client.get(answer.json()["redirect"]).text
    assert "missing: “Your answer”" in _check(html, "jde_worksheet") and "Apply" not in html.split('id="worksheet"')[1]
    token = parse_qs(urlsplit(answer.json()["redirect"]).query)["worksheet"][0]
    refused = post(client, "/settings/jde/worksheet", {"token": token})
    assert refused.status_code == 400 and "“Your answer”" in refused.json()["error"]
    assert jde_settings.load(store).company == "00042"
    not_one = client.post("/settings/jde/worksheet/upload", files={"file": ("w.xlsx", _xlsx([["x"]]))}, headers=PAGE)
    assert not_one.status_code == 400 and "no Questions sheet" in not_one.json()["error"]


# --- In a browser: the file box, the header check and the column picker, on a laptop and a phone --------------------


def _picked_in_a_browser(chromium, store, tmp_path, start: str, box: str, file: str, rows: list[list[str]],
                         field: str, column: str) -> list[str]:  # fmt: skip
    """Upload ``rows`` (a file without ``field``'s header) through the file box at ``start``, see the import closed
    with the line naming the field, pick ``column`` for it, then import; at 1366 and 390 wide, nothing wider than the
    window. The page's errors."""
    from ap_coder.web.app import create_app
    from tests.browser import browser_on
    from tests.webclient import web_settings

    (tmp_path / file).write_bytes(_xlsx(rows))
    errors: list[str] = []
    with browser_on(chromium, create_app(web_settings(), store, start_reader=False)) as (browser, base):
        for width in (1366, 390):
            page = browser.new_page(viewport={"width": width, "height": 800})
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(base + start)
            if page.locator(f"details{box}:not([open])").count():
                page.locator(f"{box} > summary").click()
            page.locator(f"{box} input[type=file]").set_input_files(str(tmp_path / file))
            assert file in page.locator(f"{box} [data-imp-chosen]").inner_text()
            page.locator(f"{box} .imp-upload button[type=submit]").click()
            page.wait_for_selector("[data-imp-picker]")
            label = page.locator(f'[data-imp-picker] label:has(select[name="col_{field}"])').evaluate(
                "e => e.firstChild.textContent.trim()"
            )
            go = page.locator("button[data-imp-go]")
            wait = page.locator("[data-imp-wait]")
            assert go.is_disabled() and wait.is_visible(), width
            assert f"Pick the column for: {label}." in wait.inner_text(), width
            assert "missing:" in page.locator(".imp-check").inner_text()
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), width
            page.select_option(f'select[name="col_{field}"]', column)
            assert go.is_enabled() and not wait.is_visible(), width
            page.select_option(f'select[name="col_{field}"]', "")  # (back to none: closed again)
            assert go.is_disabled() and wait.is_visible(), width
            page.select_option(f'select[name="col_{field}"]', column)
            if width == 390:
                go.click()
                page.wait_for_selector("[data-imp-picker]", state="detached")
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.close()
    return errors


def test_in_a_browser_gl_accounts_are_shown_their_code_picked_and_imported(chromium, store, tmp_path):
    rows = [["Number", "Account Description", "Group"], ["6010", "IT Hardware", "Opex"], ["6400", "Meals", "Opex"]]
    errors = _picked_in_a_browser(chromium, store, tmp_path, "/accounts", "#import", "chart.xlsx", rows, "code",
                                  "Number")  # fmt: skip
    assert errors == []
    assert sorted(a["code"] for a in store.list_accounts("gl_accounts")) == ["6010", "6400"]


def test_in_a_browser_the_erp_register_is_shown_its_total_picked_and_imported(chromium, store, tmp_path):
    rows = [["Vendor", "Invoice Number", "Invoice Date", "Gross"], ["Acme Ltd", "A-1", "2026-01-05", "100.00"]]
    errors = _picked_in_a_browser(chromium, store, tmp_path, "/exports#register", "#register", "erp.xlsx", rows,
                                  "total", "Gross")  # fmt: skip
    assert errors == [] and store.erp_register_count() == 1


# --- JD Edwards' own reports: on JD Edwards files only (the pilot tester's rule) -------------------------------------


@pytest.fixture(scope="module")
def jde(tmp_path_factory):
    from ap_coder.bench import jde_exports as made

    folder = tmp_path_factory.mktemp("jde")
    made.write_all(folder, size="small")
    return folder


def _grid(data: bytes) -> list[list]:
    return [list(row) for row in openpyxl.load_workbook(io.BytesIO(data)).active.iter_rows(values_only=True)]


def _without(rows: list[list], header: str) -> list[list]:
    at = rows[0].index(header)
    return [[cell for i, cell in enumerate(row) if i != at] for row in rows]


# Each page of a column picker, where its file is uploaded.
PICKERS = {"gl_accounts": "/accounts/gl_accounts/upload", "cost_centers": "/accounts/cost_centers/upload",
           "bank_accounts": "/accounts/bank/upload", "vendor_master": "/vendors/master/upload",
           "purchase_orders": "/purchase-orders/upload", "erp_register": "/exports/register/upload",
           "gl_map": "/settings/jde/gl-map/upload"}  # fmt: skip


def test_a_jd_edwards_report_is_refused_on_every_column_picker_naming_its_card(client, store, jde):
    """B-2404: GL accounts & tax took any of JD Edwards' exports: the vendor information list's Postal Code was
    picked as the code ("found by its name") and its postal codes imported as GL accounts, the chart's Account ID too.
    Each is refused, naming the card of JD Edwards files it is loaded on."""
    from ap_coder.bench import jde_exports as made

    files = {made.CHART_FILE: "1. Chart of accounts", made.UNITS_FILE: "2. Business units",
             made.VENDOR_INFO_FILE: "3. Suppliers", made.JOURNAL_FILE: "4. Last year's coding",
             made.SUPPLIERS_FILE: "3. Suppliers", made.HEADERS_FILE: SPECS["vouchers"].card,
             "voucher_split_pay_items.xlsx": SPECS["vouchers"].card}  # fmt: skip
    before = sorted(a["code"] for a in store.list_accounts("gl_accounts"))
    for key, url in PICKERS.items():
        for name, card in files.items():
            if (key, name) in (("vendor_master", made.SUPPLIERS_FILE), ("erp_register", made.HEADERS_FILE)):
                continue  # (read there whole: B-2406)
            answer = client.post(url, files={"file": (name, (jde / name).read_bytes())}, headers=PAGE)
            assert answer.status_code == 400, (key, name)
            said = answer.json()["error"]
            assert said.startswith("Not imported: this file is ") and f"under {card}" in said, (key, name, said)
    assert sorted(a["code"] for a in store.list_accounts("gl_accounts")) == before
    assert store.master_vendors() == [] and store.erp_register_count() == 0
    # Chosen on the page from a workbook's other sheet, at the import too.
    book = openpyxl.Workbook()
    book.active.append(["Code", "Description"])
    book.active.append(["6010", "IT Hardware"])
    chart = book.create_sheet("Chart")
    for row in _grid((jde / made.CHART_FILE).read_bytes()):
        chart.append(row)
    out = io.BytesIO()
    book.save(out)
    answer = client.post(PICKERS["gl_accounts"], files={"file": ("two.xlsx", out.getvalue())}, headers=PAGE)
    token = parse_qs(urlsplit(answer.json()["redirect"]).query)["file"][0]
    assert "1. Chart of accounts" in client.get(f"/accounts?tab=gl&file={token}&sheet=Chart").text
    refused = post(client, "/accounts/gl_accounts/import", {"file": token, "sheet": "Chart", "col_code": "Account ID"})
    assert refused.status_code == 400 and "under 1. Chart of accounts" in refused.json()["error"]
    assert sorted(a["code"] for a in store.list_accounts("gl_accounts")) == before


def test_purchase_orders_and_the_gl_map_refuse_jd_edwards_report_at_the_import_too(client, store, jde):
    """B-2463: the purchase order and GL map imports checked for JD Edwards' reports only at the upload: the chart,
    picked as the workbook's other sheet, went in as purchase orders, and a supplier master kept on JD Edwards
    files went in as the GL map when its token was posted. Each import refuses it, naming its card."""
    from ap_coder.bench import jde_exports as made

    book = openpyxl.Workbook()
    book.active.append(["PO Number", "Description", "Line Amount"])
    book.active.append(["PO-1", "Paper", "10.00"])
    chart = book.create_sheet("Chart")
    for row in _grid((jde / made.CHART_FILE).read_bytes()):
        chart.append(row)
    out = io.BytesIO()
    book.save(out)
    answer = client.post(PICKERS["purchase_orders"], files={"file": ("two.xlsx", out.getvalue())}, headers=PAGE)
    token = parse_qs(urlsplit(answer.json()["redirect"]).query)["file"][0]
    refused = post(client, "/purchase-orders/import", {"file": token, "sheet": "Chart", "col_po_number": "Account ID",
                                                       "col_description": "Account Description",
                                                       "col_amount": "Company"})  # fmt: skip
    assert refused.status_code == 400 and "under 1. Chart of accounts" in refused.json()["error"]
    assert not store.has_purchase_orders()
    rows = _grid((jde / made.SUPPLIERS_FILE).read_bytes())
    rows = [[*row, "GL Code" if i == 0 else "6000"] for i, row in enumerate(rows)]
    answer = client.post("/jde-files/suppliers/upload", files={"file": ("s.xlsx", _xlsx(rows))}, headers=PAGE)
    token = parse_qs(urlsplit(answer.json()["redirect"]).query)["suppliers"][0]
    refused = post(client, "/settings/jde/gl-map/import", {"token": token})
    assert refused.status_code == 400 and "under 3. Suppliers" in refused.json()["error"]
    page = client.get(f"/settings/jde?gl_map={token}").text
    assert "under 3. Suppliers" in page


def test_a_list_with_an_account_number_column_is_not_taken_for_the_transaction_journal(client):
    """B-2461: a CSV naming "Account Number" and two other of the journal's header words ("Co", "Description",
    "Name") was taken for the Transaction Journal and refused on every column picker: a GL account list, a GL map, a
    purchase order list, past coding. Only a file naming both Account Number and Remark, or the report's number in
    its first cell, is the journal; each of these reaches its column picker."""
    lists = [b"Account Number,Description,Name\r\n6000,Office,Office supplies\r\n",
             b"Co,Account Number,Description,Category\r\n00001,6000,Office,Opex\r\n",
             b"PO Number,Description,Amount,Name,Account Number\r\nPO-1,Paper,10.00,Acme,6000\r\n"]  # fmt: skip
    for url in (PICKERS["gl_accounts"], PICKERS["gl_map"], PICKERS["purchase_orders"], "/learning/history/upload"):
        for body in lists:
            answer = client.post(url, files={"file": ("list.csv", body)}, headers=PAGE)
            assert answer.status_code == 200 and "redirect" in answer.json(), (url, body, answer.text)


def test_a_column_guessed_by_a_word_in_its_header_is_shown_as_guessed_not_found_by_its_name(client):
    """B-2405: "Postal Code" picked as the GL account's code was shown in green, "found by its name"."""
    html, _token = _upload(client, "gl_accounts", [["Postal Code", "Company Name"], ["T5H 1L2", "Bracken"]])
    shown = _check(html, "gl_accounts")
    assert '“Postal Code” <span class="imp-tag">guessed: check it</span>' in shown
    assert "Every column" not in html and "Guessed by a word in the header" in html
    html, _token = _upload(client, "gl_accounts", [["Code", "Description"], ["6010", "IT Hardware"]])
    assert "guessed: check it" not in _check(html, "gl_accounts") and "Every column" in html


def test_vendors_and_exports_refuse_jd_edwards_report_without_a_needed_column_naming_it(client, store, jde):
    """B-2406: the supplier master without Address Number went in on Vendors (40 vendors with no number), the
    voucher list without Gross Amount on Exports (its total picked from Open Amount). Whole, each is read there."""
    from ap_coder.bench import jde_exports as made

    rows = _without(_grid((jde / made.SUPPLIERS_FILE).read_bytes()), "Address Number")
    answer = client.post("/vendors/master/upload", files={"file": ("s.xlsx", _xlsx(rows))}, headers=PAGE)
    assert answer.status_code == 400 and "“Address Number” (supplier number)" in answer.json()["error"]
    rows = _without(_grid((jde / made.HEADERS_FILE).read_bytes()), "Gross Amount")
    answer = client.post("/exports/register/upload", files={"file": ("v.xlsx", _xlsx(rows))}, headers=PAGE)
    assert answer.status_code == 400 and "“Gross Amount” (gross amount)" in answer.json()["error"]
    assert store.master_vendors() == [] and store.erp_register_count() == 0
    whole = client.post("/vendors/master/upload", files={"file": (made.SUPPLIERS_FILE,
                        (jde / made.SUPPLIERS_FILE).read_bytes())}, headers=PAGE)  # fmt: skip
    assert whole.status_code == 200, whole.text


def test_vendors_refuses_the_vendor_information_list_naming_its_card(client, store, jde):
    """B-2419: Vendors took the vendor information list for a vendor list: "Vendor name missing", its Tax ID
    offered as the GST/HST number."""
    from ap_coder.bench import jde_exports as made

    answer = client.post("/vendors/master/upload", files={"file": (made.VENDOR_INFO_FILE,
                         (jde / made.VENDOR_INFO_FILE).read_bytes())}, headers=PAGE)  # fmt: skip
    assert answer.status_code == 400
    assert answer.json()["error"] == import_specs.elsewhere("vendor_info", verb="imported")
    assert "under 3. Suppliers" in answer.json()["error"] and store.master_vendors() == []


def test_an_empty_file_and_a_worksheet_that_is_no_workbook_say_so_in_plain_words(client):
    """B-2417, B-2418: a 0-byte file "could not be read as a spreadsheet", an empty workbook asked to add the columns;
    the worksheet upload said "(BadZipFile)"."""
    from ap_coder.web import files

    for url in PICKERS.values():
        for data in (b"", _xlsx([])):
            answer = client.post(url, files={"file": ("list.xlsx", data)}, headers=PAGE)
            assert answer.status_code == 400 and answer.json()["error"] == files.EMPTY, url
    for data in (b"not a workbook", b""):
        answer = client.post("/settings/jde/worksheet/upload", files={"file": ("w.xlsx", data)}, headers=PAGE)
        said = answer.json()["error"]
        assert answer.status_code == 400 and "BadZipFile" not in said and "Error" not in said, said
        assert ("empty" in said) == (not data), said


# --- Review passes 3 and 4: what is said, where, and where the focus goes --------------------------------------------


def _app(store):
    from ap_coder.web.app import create_app
    from tests.webclient import web_settings

    return create_app(web_settings(), store, start_reader=False)


# Each column picker's file box, where its page opens it (a folded box is opened first).
BOXES = [("/vendors", "#import"), ("/purchase-orders", "#import"), ("/accounts", "#import"),
         ("/exports#register", "#register"), ("/learning?teach=1#history", "#history"),
         ("/jde-files#vouchers", "#vouchers")]  # fmt: skip
FOCUSED = "() => { const e = document.activeElement; return [e.tagName, e.className, e.id, e.innerText || ''] }"


def test_in_a_browser_a_refused_upload_is_said_in_its_form_the_box_emptied_and_focused(chromium, store, tmp_path):
    """B-2445: a refused upload on Vendors, purchase orders, GL accounts, Exports or Learning (and JD Edwards files'
    card 5) showed only in a message in the corner for 9 s, over the file box at 683×400; the focus dropped to the
    page and the box still said "chosen. Click Check the file". It is said in the box's form as an alert, and stays;
    the box is emptied and has the focus."""
    from tests.browser import browser_on

    load_sample_setup(store)
    (tmp_path / "list.xlsx").write_bytes(b"not a workbook at all")
    errors: list[str] = []
    with browser_on(chromium, _app(store)) as (browser, base):
        for start, box in BOXES:
            page = browser.new_page(viewport={"width": 683, "height": 400})
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(base + start)
            if page.locator(f"details{box}:not([open])").count():
                page.locator(f"{box} > summary").click()
            page.locator(f"{box} input[type=file]").set_input_files(str(tmp_path / "list.xlsx"))
            page.locator(f"{box} .imp-upload button[type=submit]").click()
            alert = page.locator(f"{box} form.imp-upload [role=alert]")
            alert.wait_for()
            assert "could not be read as a spreadsheet" in alert.inner_text(), start
            assert page.locator("#toasts .flash").count() == 0, start
            assert "Choose the file" in page.locator(f"{box} [data-imp-chosen]").inner_text(), start
            assert page.evaluate("document.activeElement.type") == "file", start
            assert page.locator(f"{box} input[type=file]").evaluate("e => e.files.length") == 0, start
            page.wait_for_timeout(200)
            assert alert.is_visible(), start  # (kept: no message that goes)
            page.close()
    assert errors == []


def test_in_a_browser_after_check_and_load_the_focus_is_on_what_the_page_says(chromium, store, tmp_path, jde):
    """B-2446: after Check the file or Load, the page came back with the focus on the page itself, and the verdict
    (a status already in the page) was never read out. The focus is on the verdict after Check, and on what the load
    did after Load: the message on Vendors, the line of what was done on JD Edwards files."""
    from ap_coder.bench import jde_exports as made
    from tests.browser import browser_on

    load_sample_setup(store)
    (tmp_path / "vendors.xlsx").write_bytes(_xlsx([["Vendor Name", "Vendor ID"], ["Acme Ltd", "V1"]]))
    with browser_on(chromium, _app(store)) as (browser, base):
        page = browser.new_page(viewport={"width": 683, "height": 400})
        page.goto(base + "/vendors")
        if page.locator("details#import:not([open])").count():
            page.locator("#import > summary").click()
        page.locator("#import input[type=file]").set_input_files(str(tmp_path / "vendors.xlsx"))
        page.locator("#import .imp-upload button[type=submit]").click()
        page.wait_for_function("document.activeElement.classList.contains('imp-verdict')")
        assert "Every column a vendor list needs was found" in page.evaluate(FOCUSED)[3]
        page.locator("button[data-imp-go]").click()
        page.wait_for_selector(".imp-preview", state="detached")
        page.wait_for_function("document.activeElement.classList.contains('flash')")
        assert "Vendor master: 1 added" in page.evaluate(FOCUSED)[3]
        page.goto(base + "/jde-files")
        page.locator("#units input[type=file]").set_input_files(str(jde / made.UNITS_FILE))
        page.locator("#units .imp-upload button[type=submit]").click()
        page.wait_for_function("document.activeElement.classList.contains('imp-verdict')")
        assert "every column it needs is here" in page.evaluate(FOCUSED)[3]
        page.locator("#units-summary button.primary").click()
        page.wait_for_function("document.activeElement.id === 'units-said'")
        page.close()


def test_in_a_browser_reconcile_says_what_was_compared_and_takes_the_person_to_it(
    chromium, store, setup_demo_template, tmp_path
):
    """B-2449: after Reconcile the statement's box still said "nothing is compared yet" and "Check them, then
    reconcile", the comparison was below the fold and the focus at the top of the page. It says what was compared
    with, and Reconcile goes to the comparison, the focus on it."""
    from tests.browser import browser_on
    from tests.conftest import fill_from
    from tests.web.test_web_finance import _statement

    store = fill_from(store, setup_demo_template)
    key, content = _statement(store)
    client = client_for(store)
    redirect = client.post("/statements/upload", data={"vendor": key}, files={"file": ("s.csv", content)},
                           headers=PAGE).json()["redirect"]  # fmt: skip
    html = client.get(redirect).text
    shown = html.split('id="statements-summary"', 1)[1].split("</p>", 1)[0]
    assert "compared with AP Coder." in shown and "not compared yet" not in html and "nothing is" not in shown
    assert "Check them, then reconcile again." in html and 'id="statements-compared"' in html
    assert re.search(r"Compared with .*&#39;s invoices in AP Coder: \d+ matched, \d+ amount differs", html)
    assert 'action="/statements#result"' in html and 'id="result" tabindex="-1" data-imp-result' in html
    with browser_on(chromium, _app(store)) as (browser, base):
        page = browser.new_page(viewport={"width": 683, "height": 400})
        page.goto(base + redirect)
        page.locator("#pick-statements button.primary").click()
        page.wait_for_function("document.activeElement.id === 'result'")
        assert page.url.endswith("#result")
        assert page.locator("#result").evaluate("e => e.getBoundingClientRect().top") < 400
        page.close()


def test_in_a_browser_past_coding_loaded_says_so_in_its_card_opened(chromium, store, tmp_path):
    """B-2450: Learning's Load of past coding came back to /learning#history with the card closed and its message at
    the top of the page, off screen ("Taught 0 past lines; 1 with a GL account that is not in your list"). The
    message is in the card, opened, and has the focus."""
    from tests.browser import browser_on

    load_sample_setup(store)
    (tmp_path / "past.xlsx").write_bytes(_xlsx([["Vendor", "Description", "GL Code"], ["Acme Ltd", "Paper", "9999"]]))
    with browser_on(chromium, _app(store)) as (browser, base):
        page = browser.new_page(viewport={"width": 683, "height": 400})
        page.goto(base + "/learning?teach=1#history")
        page.locator("#history input[type=file]").set_input_files(str(tmp_path / "past.xlsx"))
        page.locator("#history .imp-upload button[type=submit]").click()
        page.wait_for_selector("#pick-past_coding")
        page.locator("#pick-past_coding button.primary").click()
        page.wait_for_selector("#pick-past_coding", state="detached")
        page.wait_for_function("document.activeElement.classList.contains('flash')")
        said = page.locator("#history > .flash")
        assert "1 with a GL account that is not in your list" in said.inner_text()
        assert page.evaluate("document.getElementById('history').open")
        assert said.evaluate("e => e === document.activeElement")
        page.close()


def test_past_coding_whose_gl_accounts_are_not_listed_is_warned_of_before_it_is_loaded(client):
    """B-2451: past coding whose lines' GL accounts are not in the GL accounts list gave no warning before Load,
    then taught none of them. The file shown counts them, naming the column read."""
    rows = [["Vendor", "Description", "GL Code"], ["Acme Ltd", "Paper", "9999"], ["Acme Ltd", "Toner", "9998"],
            ["Acme Ltd", "Pens", "6100"]]  # fmt: skip
    html, _token = _upload(client, "past_coding", rows)
    warned = html.split('id="past_coding-unknown-gl"', 1)[1].split("</div></div>", 1)[0]
    assert "2 lines have a GL account in “GL Code” that is not in your GL accounts" in warned
    html, _token = _upload(client, "past_coding", rows[:1] + rows[3:])
    assert 'id="past_coding-unknown-gl"' not in html


def _contrast(fore: str, back: str) -> float:
    def lum(hex_colour: str) -> float:
        channels = [int(hex_colour.lstrip("#")[i : i + 2], 16) / 255 for i in (0, 2, 4)]
        r, g, b = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    light, dark = sorted((lum(fore), lum(back)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


def test_the_other_names_read_of_a_column_are_readable():
    """B-2452: "Also read: …" under "What each column is" was #8a93a3 on #fafbfc (2.99:1) at 10.8 px."""
    from pathlib import Path

    static = Path(__file__).parents[2] / "ap_coder" / "web" / "static"
    tokens = dict(re.findall(r"--(lg-[\w-]+):\s*(#[0-9a-fA-F]{6})", (static / "app.css").read_text()))
    rule = re.search(r"\.imp-also \{([^}]*)\}", (static / "imports.css").read_text()).group(1)
    colour = tokens[re.search(r"color: var\(--([\w-]+)\)", rule).group(1)]
    assert _contrast(colour, tokens["lg-head"]) >= 4.5
    assert float(re.search(r"font-size: ([\d.]+)px", rule).group(1)) >= 12


def test_an_upload_no_longer_kept_on_learning_says_so_as_everywhere(client):
    """B-2453: Learning still said "That upload has expired" (Load, the journal's Load, Read it as a table, and the
    page), where every other import says it is no longer here."""
    from ap_coder.web import files

    for url, field in (("/learning/history/teach", "token"), ("/learning/journal/load", "token"),
                       ("/learning/journal/as-table", "token")):  # fmt: skip
        answer = post(client, url, {field: "gone"})
        assert answer.status_code == 400 and answer.json()["error"] == files.GONE, url
    for query in ("history=gone", "journal=gone"):
        page = client.get(f"/learning?{query}").text
        assert "expired" not in page and "That upload is no longer here" in page, query


def test_the_words_beside_a_load_button_say_load(client):
    """B-2454: Learning's message said "Check the columns below, then teach" beside "Load 1 line", and a GL accounts
    file with a guessed column "Check each below before you import" beside "Load GL accounts"."""
    past = _xlsx([["Vendor", "Description", "GL Code"], ["A", "B", "6100"]])
    answer = client.post("/learning/history/upload", headers=PAGE, files={"file": ("past.xlsx", past)})
    page = client.get(answer.json()["redirect"]).text
    assert "Check them, then load." in page and "then teach" not in page
    html, _token = _upload(client, "gl_accounts", [["Postal Code", "Company Name"], ["T5H 1L2", "Bracken"]])
    assert "Check each below before you load." in html and "before you import" not in html


def test_the_invoices_already_in_the_erp_are_named_as_their_list_is_when_loaded(client, store):
    """B-2455: Exports' message after a load said "2 ERP invoices known", for the list named "Invoices already in
    JD Edwards" (or "your ERP")."""
    html, token = _upload(client, "erp_register", PAGES["erp_register"][5])
    page = client.get(_import(client, "erp_register", token, html).json()["redirect"]).text
    assert "Invoices already in your ERP: 1 invoice on the list now." in page and "known." not in page


def test_a_needed_column_named_twice_is_picked_by_the_person_and_that_one_is_loaded(client, store):
    """B-2469: GL accounts with "Code" twice (a junk column first) said in green "found by its name" and loaded the
    junk column; so did a vendor list with "Vendor Name" twice; the choices offered pandas' "Code.1". The page says
    which name is there twice, picks none of them, offers each as the file names it and which ("Code (2nd)"), and the
    one picked is the one loaded."""
    for key, header, junk, real in (("gl_accounts", "Code", "ZZZ", "6010"),
                                    ("vendor_master", "Vendor Name", "ZZZ", "Acme Ltd")):  # fmt: skip
        field = SPECS[key].required[0].field
        html, token = _upload(client, key, [[header, "Description", header], [junk, "IT Hardware", real]])
        assert "Every column" not in html and f"Named twice in this file: “{header}”" in html, key
        assert _offered(html)[field] == "" and " data-imp-go disabled" in html, key
        picker = re.search(rf'<select name="col_{field}">(.*?)</select>', html, re.S).group(1)
        options = {label: value for value, label in re.findall(r'<option value="([^"]*)">([^<]*)</option>', picker)}
        assert {f"{header} (1st)", f"{header} (2nd)"} <= set(options) and f"{header}.1" not in options, options
        _url, url, _name, token_field, _page, _rows, _held = PAGES[key]
        refused = post(client, url, {token_field: token, f"col_{field}": ""})
        assert refused.status_code == 400, key
        loaded = post(client, url, {token_field: token, f"col_{field}": options[f"{header} (2nd)"]})
        assert loaded.status_code == 200 and loaded.json()["ok"], loaded.text
    assert [a["code"] for a in store.list_accounts("gl_accounts") if a["code"] in ("ZZZ", "6010")] == ["6010"]
    assert [v["display_name"] for v in store.master_vendors()] == ["Acme Ltd"]


def test_a_gl_account_list_with_an_ordinary_code_header_is_not_said_guessed(client):
    """B-2470: GL accounts headed "GL Code" (Past coding's and the GL map's own template header), "Account Number" or
    "GL Account" said "Guessed by a word in the header… check each"; they are names of the code, as "Code" is."""
    for header in ("GL Code", "Account Number", "GL Account"):
        html, _token = _upload(client, "gl_accounts", [[header, "Description"], ["6010", "IT Hardware"]])
        assert "Guessed by a word" not in html and "Every column" in html, header
        assert _offered(html)["code"] == header
    html, _token = _upload(client, "gl_accounts", [["Postal Code", "Company Name"], ["T5H 1L2", "Bracken"]])
    assert "Guessed by a word" in html


def test_past_coding_shows_one_verdict_and_its_way_back_says_choose_another_file(client):
    """B-2471: Learning showed a green "Read past_miss.xlsx: 1 line. Check the columns below, then load." above the
    amber "Not found by its name: GL account", and its button back said Cancel, where every other import shows one
    verdict and says "Choose another file"."""
    html, _token = _upload(client, "past_coding", [["Vendor", "Description"], ["Acme Ltd", "Paper"]])
    assert "Not found by its name: GL account." in html
    assert "Read list.xlsx" not in html and len(re.findall(r'class="flash (?:ok|warn|err)\b', html)) == 1
    assert "Choose another file</a>" in html and ">Cancel</a>" not in html
