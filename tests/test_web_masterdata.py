"""The web dashboard's master-data pages: Vendors, Purchase orders and GL accounts & tax.

Each page draws on an empty and on a busy database; imports go upload → check the columns → import; downloads are
the files the earlier dashboard gave; destructive actions ask first; text from files and invoices is escaped."""

from __future__ import annotations

import io
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlencode

import openpyxl
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from ap_coder.coding import po as po_mod
from ap_coder.coding.rules import Rule
from ap_coder.coding.vendors import ON_HOLD
from ap_coder.core.config import Settings
from ap_coder.core.safe import csv_cell
from ap_coder.core.store import Store, load_sample_setup
from ap_coder.ops.demo import load_demo
from ap_coder.web.app import create_app

PAGE = {"X-AP-Coder": "1"}
MASTER_PAGES = [
    "/vendors",
    "/vendors?view=hold",
    "/vendors?view=new&sort=name&q=a",
    "/purchase-orders",
    "/purchase-orders?status=all",
    "/accounts",
    "/accounts?tab=cc",
    "/accounts?tab=tax",
    "/accounts?tab=policy",
    "/accounts?tab=rules",
    "/accounts?tab=bank",
]


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "private" / "ap_coder.db")


@pytest.fixture
def client(store):
    s = Settings()
    app = create_app(replace(s, llm=replace(s.llm, provider="off")), store, start_reader=False)
    return TestClient(app, base_url="http://localhost")


@pytest.fixture
def busy(store):
    load_sample_setup(store)
    load_demo(store, Settings())
    return store


def follow(client, response):
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"], body
    return client.get(body["redirect"])


def upload(client, url, name, content, mime="text/csv"):
    """Upload a spreadsheet; returns the page showing its columns."""
    return follow(client, client.post(url, files={"file": (name, content, mime)}, headers=PAGE))


def xlsx(rows: list[list[str]]) -> bytes:
    book = openpyxl.Workbook()
    for row in rows:
        book.active.append(row)
    raw = io.BytesIO()
    book.save(raw)
    return raw.getvalue()


# --- Every page draws ------------------------------------------------------------------------------------------


@pytest.mark.parametrize("url", MASTER_PAGES)
def test_pages_draw_on_an_empty_database(client, url):
    response = client.get(url)
    assert response.status_code == 200 and "is on its way" not in response.text


@pytest.mark.parametrize("url", MASTER_PAGES)
def test_pages_draw_on_a_busy_database(client, busy, url):
    response = client.get(url)
    assert response.status_code == 200 and "is on its way" not in response.text


def test_empty_pages_say_so(client):
    assert "No vendors yet" in client.get("/vendors").text
    assert "No purchase orders yet" in client.get("/purchase-orders").text
    text = client.get("/accounts").text
    assert "No GL accounts yet" in text and "Use the sample GL accounts" in text


# --- Vendors ----------------------------------------------------------------------------------------------------


def test_the_vendor_list_filters_and_opens_a_vendor(client, busy):
    vendors = busy.vendor_summaries()
    text = client.get("/vendors").text
    for v in vendors:
        assert v["vendor_name"].replace("&", "&amp;") in text
    assert "Recurring invoices" in text
    one = vendors[0]
    found = client.get("/vendors", params={"q": one["vendor_name"][:8]}).text
    assert one["vendor_name"].replace("&", "&amp;") in found
    assert "No vendors match" in client.get("/vendors?q=zzzz-nobody").text
    detail = client.get("/vendors", params={"open": one["vendor_key"]}).text
    for heading in (
        "GL accounts reviewers used",
        "Recent invoices",
        "Bank account and GST/HST number history",
        "Controls",
        "Ask the vendor",
    ):
        assert heading in detail
    assert f'name="key" value="{one["vendor_key"]}"' in detail
    if one["terms"]:
        assert f"terms {one['terms']}" in detail


def test_a_vendor_is_put_on_hold_and_given_its_gst_number(client, busy):
    v = busy.vendor_summaries()[0]
    assert client.post("/vendors/save", data={"key": v["vendor_key"], "status": ON_HOLD}).status_code == 403
    page = follow(
        client,
        client.post(
            "/vendors/save",
            data={
                "key": v["vendor_key"],
                "status": ON_HOLD,
                "expected_gst": "123456789RT0001",
                "notes": "<i>call first</i>",
            },
            headers=PAGE,
        ),
    )
    saved = busy.get_vendor(v["vendor_key"])
    assert saved["status"] == ON_HOLD and saved["expected_gst"] == "123456789RT0001"
    assert "Saved " in page.text and "&lt;i&gt;call first&lt;/i&gt;" in page.text
    assert "On hold" in client.get("/vendors?view=hold").text
    assert client.post("/vendors/save", data={"key": v["vendor_key"], "status": "x"}, headers=PAGE).status_code == 400
    assert client.post("/vendors/save", data={"key": "nobody"}, headers=PAGE).status_code == 404


def test_a_vendor_is_given_its_qst_and_pst_numbers_and_province(client, busy):
    v = busy.vendor_summaries()[0]
    data = {"key": v["vendor_key"], "status": "active", "qst_number": "1234567890TQ0001", "pst_number": "PST-1234-5678",
            "province": "BC"}  # fmt: skip
    follow(client, client.post("/vendors/save", data=data, headers=PAGE))
    saved = busy.get_vendor(v["vendor_key"])
    assert (saved["qst_number"], saved["pst_number"], saved["province"]) == ("1234567890TQ0001", "PST-1234-5678", "BC")
    detail = client.get("/vendors?" + urlencode({"open": v["vendor_key"]})).text
    assert 'value="PST-1234-5678"' in detail and '<option value="BC" selected>' in detail
    bad = client.post("/vendors/save", data={**data, "province": "XX"}, headers=PAGE)
    assert bad.status_code == 400


def test_a_vendor_list_number_that_fails_the_cra_check_is_warned_about(client, busy):
    """D-65: the review screen fills the vendor list's numbers in, so a wrong one there is said on the Vendors page."""
    from ap_coder.coding.vendors import vendor_number_problems

    v = busy.vendor_summaries()[0]
    data = {"key": v["vendor_key"], "status": "active", "expected_gst": "123456789 RT0001", "qst_number": "12345TQ"}
    follow(client, client.post("/vendors/save", data=data, headers=PAGE))
    detail = client.get("/vendors?" + urlencode({"open": v["vendor_key"]})).text
    assert "GST/HST number 123456789 RT0001 fails the CRA check digit" in detail
    assert "QST number 12345TQ is not in the format 1234567890 TQ0001" in detail
    assert "Tax number to check" in client.get("/vendors").text
    assert vendor_number_problems({"expected_gst": "123456782"}) == []  # the Business Number alone is fine
    assert vendor_number_problems({"expected_gst": "12345"}) == [
        "GST/HST number 12345 is not in the format 123456789 RT0001"
    ]
    assert vendor_number_problems({"expected_gst": "123456782 RT0001", "qst_number": "1234567890 TQ0001"}) == []


def test_bank_and_tax_number_history_is_masked(client, store, tmp_path):
    for n, (bank, gst) in enumerate(
        [("Account 004-55555-9994567", "123456789RT0001"), ("Account 004-12345-7654321", "987654321RT0001")]
    ):
        pdf = tmp_path / f"{n}.pdf"
        pdf.write_bytes(b"%PDF-1.4 " + bytes([n]))
        store.add_invoice(
            pdf,
            {
                "vendor_name": "Acme Ltd",
                "invoice_number": f"A-{n}",
                "invoice_date": f"2026-0{n + 1}-01",
                "grand_total": 10,
                "remit_bank_account": bank,
                "gst_hst_registration_number": gst,
            },
            {"issues": []},
        )
    key = store.vendor_summaries()[0]["vendor_key"]
    text = client.get("/vendors", params={"open": key}).text
    assert "…4567" in text and "…4321" in text and "9994567" not in text
    assert "987654321RT0001" in text and "Changed" in text


def test_a_new_bank_account_ending_in_the_same_four_digits_shows_as_changed(client, store, tmp_path):
    """B-191: the history grouped accounts by their last 4 digits, so another account at another bank that ends in
    the same 4 digits counted as the old one: one row, no "Changed", while the review screen warned."""
    old = "Transit 00012, Institution 003, Account 5011234567"
    new = "Transit 47210, Institution 815, Account 9907754567"
    for n, bank in enumerate([old, new]):
        pdf = tmp_path / f"{n}.pdf"
        pdf.write_bytes(b"%PDF-1.4 " + bytes([n]))
        store.add_invoice(
            pdf,
            {"vendor_name": "Acme Ltd", "invoice_number": f"A-{n}", "invoice_date": f"2026-0{n + 1}-01",
             "grand_total": 10, "remit_bank_account": bank},
            {"issues": []},
        )  # fmt: skip
    key = store.vendor_summaries()[0]["vendor_key"]
    text = client.get("/vendors", params={"open": key}).text
    assert "Changed" in text
    assert "00012 003" in text and "47210 815" in text  # the bank and branch tell the two apart
    assert "5011234567" not in text and "9907754567" not in text


def test_ask_the_vendor_drafts_an_email_for_its_invoices_in_the_queue(client, store, tmp_path):
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    store.add_invoice(
        pdf,
        {"vendor_name": "Acme <b>Ltd</b>", "invoice_number": "", "grand_total": 50, "invoice_date": "2026-09-01"},
        {"issues": [{"code": "MISSING_INVOICE_NUMBER", "severity": "error", "message": "x"}]},
    )
    v = store.vendor_summaries()[0]
    text = client.get("/vendors", params={"open": v["vendor_key"]}).text
    assert 'href="mailto:' in text and "Open in email" in text and "Acme <b>Ltd</b>" not in text
    assert "Acme &lt;b&gt;Ltd&lt;/b&gt;" in text
    french = client.get("/vendors", params={"open": v["vendor_key"], "lang": "fr"}).text
    assert "Bonjour" in french


def test_the_vendor_master_is_uploaded_checked_and_imported(client, busy):
    rows = [
        ["Vendor Name", "Vendor ID", "GST", "Terms", "Status"],
        ["Acme <script>x</script>", "V1", "", "Net 30", "Active"],
        ["Beta Supply", "V2", "", "Net 45", "Hold"],
        ["BETA SUPPLY.", "V3", "", "", ""],
    ]
    bad = client.post(
        "/vendors/master/upload", files={"file": ("v.xlsx", b"not a workbook", "application/zip")}, headers=PAGE
    )
    assert bad.status_code == 400 and "could not be read" in bad.json()["error"]
    page = upload(client, "/vendors/master/upload", "vendors.xlsx", xlsx(rows))
    assert "vendors.xlsx" in page.text and "Acme &lt;script&gt;" in page.text and "<script>x" not in page.text
    assert '<option value="Vendor Name" selected>' in page.text  # the column is guessed
    token = page.url.params["file"]
    missing = client.post("/vendors/master/import", data={"file": token}, headers=PAGE)
    assert missing.status_code == 400 and "vendor name" in missing.json()["error"]
    page = follow(
        client,
        client.post(
            "/vendors/master/import",
            data={
                "file": token,
                "col_vendor_name": "Vendor Name",
                "col_erp_id": "Vendor ID",
                "col_terms": "Terms",
                "col_status": "Status",
            },
            headers=PAGE,
        ),
    )
    assert "Vendor master: 2 added" in page.text and "more than once" in page.text and "BETA SUPPLY." in page.text
    assert any(v["erp_id"] == "V1" for v in busy.master_vendors())


# --- Purchase orders ----------------------------------------------------------------------------------------------


def test_the_template_is_the_po_modules(client):
    response = client.get("/purchase-orders/template.csv")
    assert response.headers["content-type"].startswith("text/csv")
    assert "purchase_orders_template.csv" in response.headers["content-disposition"]
    assert response.content == po_mod.template_csv()


def test_sample_pos_then_close_reopen_and_delete_with_a_confirmation(client, store):
    page = follow(client, client.post("/purchase-orders/sample", headers=PAGE))
    pos = store.purchase_orders()
    assert pos and f"Loaded {len(pos)} sample purchase orders" in page.text
    assert client.post("/purchase-orders/sample", headers=PAGE).json()["toast"]  # only for an empty list
    p = pos[0]
    text = client.get("/purchase-orders", params={"status": "all", "po": p["po_key"]}).text
    assert "Ordered" in text and "Received" in text and "Billed" in text
    assert f'action="/purchase-orders/delete" data-confirm="Delete {p["po_number"]} from the list?' in text
    follow(client, client.post("/purchase-orders/status", data={"key": p["po_key"]}, headers=PAGE))
    assert store.purchase_order(p["po_key"])["status"] == po_mod.CLOSED
    follow(client, client.post("/purchase-orders/status", data={"key": p["po_key"]}, headers=PAGE))
    assert store.purchase_order(p["po_key"])["status"] == po_mod.OPEN
    page = follow(client, client.post("/purchase-orders/delete", data={"key": p["po_key"]}, headers=PAGE))
    assert store.purchase_order(p["po_key"]) is None and "deleted" in page.text
    assert client.post("/purchase-orders/delete", data={"key": "nope"}, headers=PAGE).status_code == 404


def test_pos_are_uploaded_checked_and_imported(client, busy):
    sheet = (
        "PO Number,Supplier,Line,Item,Qty,Price,Received\n"
        "PO-9001,Acme Ltd,1,Laptops,2,1000,2\nPO-9001,Acme Ltd,2,Docks,2,150,0\nPO-9002,Beta,1,Toner,10,30,\n"
    )
    page = upload(client, "/purchase-orders/upload", "pos.csv", sheet.encode())
    assert "pos.csv" in page.text and "Laptops" in page.text
    token = page.url.params["file"]
    missing = client.post("/purchase-orders/import", data={"file": token, "col_po_number": "PO Number"}, headers=PAGE)
    assert missing.status_code == 400 and "Description" in missing.json()["error"]
    page = follow(
        client,
        client.post(
            "/purchase-orders/import",
            data={
                "file": token,
                "col_po_number": "PO Number",
                "col_vendor_name": "Supplier",
                "col_line_number": "Line",
                "col_description": "Item",
                "col_quantity": "Qty",
                "col_unit_price": "Price",
                "col_received_quantity": "Received",
            },
            headers=PAGE,
        ),
    )
    assert "Imported 2 POs with 3 lines" in page.text
    assert store_po(busy, "PO-9001")["total"] == 2300
    text = client.get("/purchase-orders?q=9001").text
    assert "PO-9001" in text and "PO-9002" not in text


def store_po(store: Store, number: str) -> dict:
    return store.purchase_order(po_mod.po_key(number))


# --- GL accounts & tax ------------------------------------------------------------------------------------------------


def test_the_sample_gl_accounts(client, store):
    page = follow(client, client.post("/accounts/sample", headers=PAGE))
    assert "Sample setup loaded" in page.text and store.list_accounts("gl_accounts")
    assert "Use the sample GL accounts" not in page.text
    assert client.post("/accounts/sample", headers=PAGE).status_code == 400


def test_gl_accounts_are_imported_from_excel_edited_and_downloaded(client, store):
    rows = [
        ["GL Code", "Account Name", "Group"],
        ["6010", "IT Hardware", "Opex"],
        ["6020", "Software <b>", "Opex"],
        ["1500", "Computer Equipment", "Capex"],
    ]
    page = upload(client, "/accounts/gl_accounts/upload", "coa.xlsx", xlsx(rows))
    assert '<option value="GL Code" selected>' in page.text and '<option value="Account Name" selected>' in page.text
    token = page.url.params["file"]
    page = follow(
        client,
        client.post(
            "/accounts/gl_accounts/import",
            data={
                "file": token,
                "col_code": "GL Code",
                "col_description": "Account Name",
                "col_category": "Group",
            },
            headers=PAGE,
        ),
    )
    assert "Imported: 3 added, 0 updated, 0 skipped" in page.text
    assert "Software &lt;b&gt;" in page.text and "Opex · 2" in page.text
    # download: the CSV the earlier dashboard gave
    data = client.get("/accounts/gl_accounts.csv")
    assert (
        data.headers["content-type"].startswith("text/csv") and "gl_accounts.csv" in data.headers["content-disposition"]
    )
    expected = pd.DataFrame(store.list_accounts("gl_accounts")).map(csv_cell).to_csv(index=False).encode("utf-8-sig")
    assert data.content == expected
    # edit: rename one, delete one (ticked), add one in a blank row
    current = store.list_accounts("gl_accounts")
    codes = [a["code"] for a in current] + ["7000"]
    descriptions = [a["description"] for a in current] + ["Travel"]
    descriptions[0] = "Equipment"
    form = {
        "code": codes,
        "description": descriptions,
        "category": [a["category"] for a in current] + [""],
        "delete": ["1"],
    }
    page = follow(client, client.post("/accounts/gl_accounts/save", data=form, headers=PAGE))
    saved = {a["code"]: a["description"] for a in store.list_accounts("gl_accounts")}
    assert saved == {"1500": "Equipment", "6020": "Software <b>", "7000": "Travel"}
    assert "removed 1" in page.text
    dupes = client.post("/accounts/gl_accounts/save", data={"code": ["1", "1"]}, headers=PAGE)
    assert dupes.status_code == 400 and "more than once" in dupes.json()["error"]
    assert 'id="accounts-form"' in client.get("/accounts").text  # deleting ticked rows asks first (accounts.js)
    assert client.get("/accounts/other.csv").status_code == 404


def test_bank_accounts_are_imported_edited_and_never_shown_in_full(client, store):
    """D-66: our bank accounts, imported with the columns picked like the other lists, edited in a grid (a default
    per currency, In use), and only the last 4 digits of a number ever shown."""
    load_sample_setup(store)
    sample = (Path(__file__).resolve().parent.parent / "data" / "bank_accounts.csv").read_bytes()
    page = upload(client, "/accounts/bank/upload", "bank_accounts.csv", sample)
    for field, column in (("code", "code"), ("currency", "currency"), ("account_number", "account_number"),
                          ("is_default", "default")):  # fmt: skip
        assert f'<select name="col_{field}">' in page.text and f'<option value="{column}" selected>' in page.text
    token = page.url.params["file"]
    picked = {f"col_{f}": c for f, c in (("code", "code"), ("name", "name"), ("currency", "currency"),
              ("institution", "institution"), ("transit", "transit"), ("account_number", "account_number"),
              ("gl_account", "gl_account"), ("is_default", "default"), ("active", "active"))}  # fmt: skip
    page = follow(client, client.post("/accounts/bank/import", data={"file": token, **picked}, headers=PAGE))
    assert "Imported: 2 added, 0 updated." in page.text
    accounts = {a["code"]: a for a in store.bank_accounts()}
    assert accounts["CAD-OP"]["account_number"] == "0000001234" and accounts["CAD-OP"]["is_default"]
    assert "0000001234" not in page.text and "••••1234" in page.text  # the last 4 digits only
    assert "Sample Bank CAD operating" in page.text
    # a GL account that is not in the chart is refused, nothing imported
    bad = upload(client, "/accounts/bank/upload", "b.csv", b"Code,Currency,Cash GL\nX-1,CAD,9999\n")
    answer = client.post(
        "/accounts/bank/import",
        headers=PAGE,
        data={
            "file": bad.url.params["file"],
            "col_code": "Code",
            "col_currency": "Currency",
            "col_gl_account": "Cash GL",
        },
    )
    assert answer.status_code == 400 and "9999 is not in your GL accounts" in answer.json()["error"]
    # the grid: rename, a new USD default, CAD-OP no longer in use; a blank number keeps the stored one
    page = client.get("/accounts?tab=bank")
    version = page.text.split('name="version" value="', 2)[-1].split('"')[0]
    shown = store.bank_accounts()  # CAD-OP, USD-OP
    form = {
        "version": version,
        "code": [a["code"] for a in shown] + ["USD-2"],
        "name": ["Operating", shown[1]["name"], "Second USD"],
        "currency": ["CAD", "USD", "usd"],
        "institution": ["", "", "3"],
        "transit": ["", "", "12"],
        "account_number": ["", "", "987654321"],
        "gl_account": ["", "", "2310"],
        "is_default": ["2"],
        "active": ["1"],  # CAD-OP unticked
    }
    follow(client, client.post("/accounts/bank/save", data=form, headers=PAGE))
    accounts = {a["code"]: a for a in store.bank_accounts()}
    assert accounts["CAD-OP"]["name"] == "Operating" and not accounts["CAD-OP"]["active"]
    assert accounts["CAD-OP"]["account_number"] == "0000001234"  # left blank: kept
    assert accounts["USD-2"]["is_default"] and not accounts["USD-OP"]["is_default"]
    assert (accounts["USD-2"]["institution"], accounts["USD-2"]["transit"]) == ("003", "00012")
    stale = client.post("/accounts/bank/save", data=form, headers=PAGE)  # the page drawn before that save
    assert stale.status_code == 409
    template = client.get("/accounts/bank/template.csv")
    assert template.status_code == 200 and b"account_number" in template.content
    assert any(e["detail"].get("table") == "bank_accounts" for e in store.events(actions=["accounts_imported"]))


def test_cost_centers_import_with_the_columns_picked(client, store):
    page = upload(client, "/accounts/cost_centers/upload", "cc.csv", b"Dept;Name\n100;Admin\n200;Sales\n")
    token = page.url.params["file"]
    assert client.post("/accounts/cost_centers/import", data={"file": token}, headers=PAGE).status_code == 400
    follow(
        client,
        client.post(
            "/accounts/cost_centers/import",
            data={"file": token, "col_code": "Dept", "col_description": "Name"},
            headers=PAGE,
        ),
    )
    assert [a["code"] for a in store.list_accounts("cost_centers")] == ["100", "200"]


def test_tax_setup_policy_and_rules(client, busy):
    gl = busy.list_accounts("gl_accounts")[0]["code"]
    form = {f"treatment_{t}": "recoverable" for t in ("GST", "HST", "QST", "PST", "OTHER")}
    form |= {f"gl_{t}": gl for t in ("GST", "HST", "QST", "PST", "OTHER")}
    form["treatment_PST"] = "expense_to_line"
    follow(client, client.post("/accounts/tax", data=form, headers=PAGE))
    treatments = busy.tax_treatments()
    assert treatments["GST"].gl_code == gl and treatments["PST"].treatment == "expense_to_line"
    assert treatments["PST"].gl_code == ""
    bad = client.post("/accounts/tax", data={**form, "gl_GST": "nope"}, headers=PAGE)
    assert bad.status_code == 400
    text = client.get("/accounts?tab=tax").text
    assert "Rates in force today" in text and "Posted with each expense line" in text

    follow(client, client.post("/accounts/policy", data={"notes": "Laptops go to 6010"}, headers=PAGE))
    assert busy.get_setting("policy_notes") == "Laptops go to 6010"
    assert "Laptops go to 6010" in client.get("/accounts?tab=policy").text

    page = follow(
        client,
        client.post(
            "/accounts/rules",
            data={
                "vendor": ["Purolator", "", "", ""],
                "contains": ["", "freight", "", ""],
                "gl_code": [gl, gl, gl, ""],
                "cost_center": ["", "", "", ""],
            },
            headers=PAGE,
        ),
    )
    assert [(r.vendor, r.contains) for r in busy.coding_rules()] == [("Purolator", ""), ("", "freight")]
    assert "2 coding rules saved; 1 incomplete row left out" in page.text
    follow(client, client.post("/accounts/rules/add", data={"vendor": "Acme", "gl_code": gl}, headers=PAGE))
    assert Rule("Acme", "", gl, "") in [
        Rule(r.vendor, r.contains, r.gl_code, r.cost_center) for r in busy.coding_rules()
    ]
    assert client.post("/accounts/rules/add", data={"vendor": "Acme"}, headers=PAGE).status_code == 400
    assert 'value="Purolator"' in client.get("/accounts?tab=rules").text


def test_rules_need_gl_accounts_first(client, store):
    assert "Import your GL accounts first" in client.get("/accounts?tab=rules").text
    assert client.post("/accounts/rules", data={"vendor": ["x"], "gl_code": ["1"]}, headers=PAGE).status_code == 400


def test_an_excel_cell_reading_na_is_kept_as_typed():
    """W2-04: the Excel readers dropped cells that read "NA", "NULL", "None", "N/A" (a cost center "NA" for North
    America vanished), while the same file as CSV kept them: every reader keeps a cell as it was typed."""
    from ap_coder.web.files import read_sheet
    from ap_coder.web.pages import learning, settings_jde

    book = openpyxl.Workbook()
    book.active.append(["Cost center", "Name"])
    for code in ("EU", "NA", "NULL", "N/A", "None"):
        book.active.append([code, "x"])
    raw = io.BytesIO()
    book.save(raw)
    codes = ["EU", "NA", "NULL", "N/A", "None"]
    df, _names = read_sheet("cc.xlsx", raw.getvalue())
    assert list(df["Cost center"]) == codes
    assert [r["Cost center"] for r in settings_jde.read_sheet("cc.xlsx", raw.getvalue())] == codes
    ((_columns, rows),) = learning.read_sheets("cc.xlsx", raw.getvalue()).values()
    assert [r["Cost center"] for r in rows] == codes
