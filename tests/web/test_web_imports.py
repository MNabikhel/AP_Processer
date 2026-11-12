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
