"""The web dashboard's master-data pages: Vendors, Purchase orders and GL accounts & tax.

Each page draws on an empty and on a busy database; imports go upload → check the columns → import; downloads are
the files the earlier dashboard gave; destructive actions ask first; text from files and invoices is escaped."""

from __future__ import annotations

import io
import re
from pathlib import Path
from urllib.parse import urlencode

import openpyxl
import pandas as pd
import pytest

from ap_coder.coding import po as po_mod
from ap_coder.coding.rules import Rule
from ap_coder.coding.vendors import ON_HOLD
from ap_coder.core.safe import csv_cell
from ap_coder.core.store import Store, load_sample_setup
from tests.conftest import fill_from
from tests.webclient import PAGE, add_clean, client_for

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
    "/rules",
    "/accounts?tab=bank",
]


@pytest.fixture
def busy(store, setup_demo_template):
    return fill_from(store, setup_demo_template)  # the sample setup, then the demo


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


def test_the_vendor_search_and_name_sort_ignore_accents(store):
    """A search for "quebec" finds Hydro-Québec, as the queue's search does, and Éclair sorts before Zed."""
    from ap_coder.web.pages.vendors import sort_vendors

    add_clean(store, vendor_name="Hydro-Québec")
    client = client_for(store)
    for q in ("quebec", "QUEBEC", "hydro-quebec"):
        assert "Hydro-Québec" in client.get("/vendors", params={"q": q}).text.split("<main", 1)[-1]
    rows = [{"vendor_name": n} for n in ("Zed Inc", "Éclair Ltée", "Alpha", "Énergir")]
    assert [r["vendor_name"] for r in sort_vendors(rows, "name")] == ["Alpha", "Éclair Ltée", "Énergir", "Zed Inc"]


def test_the_vendor_search_and_name_sort_read_a_ligature_as_its_two_letters(store):
    """Œ and Æ, one letter each that NFKD leaves whole, are OE and AE: "oeuvre" finds Œuvres Ltée, which sorts
    among the O names, not after Zed."""
    from ap_coder.web.pages.vendors import sort_vendors

    add_clean(store, vendor_name="Œuvres Ltée")
    client = client_for(store)
    for q in ("oeuvre", "OEUVRES"):
        assert "Œuvres Ltée" in client.get("/vendors", params={"q": q}).text.split("<main", 1)[-1]
    rows = [{"vendor_name": n} for n in ("Zed Inc", "Æther", "Œuvres Ltée", "Alpha", "Omega")]
    assert [r["vendor_name"] for r in sort_vendors(rows, "name")] == ["Æther", "Alpha", "Œuvres Ltée", "Omega",
                                                                      "Zed Inc"]  # fmt: skip


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


@pytest.fixture
def held(tmp_path):
    """A server with a vendor on hold, one invoice from it, and a Clerk and an Approver logged in."""
    from fastapi.testclient import TestClient

    from ap_coder.core.store.users import APPROVER, CLERK
    from ap_coder.web.app import create_app
    from tests.webclient import add_clean, enrol, sign_in, web_settings

    store = Store(tmp_path / "private" / "ap_coder.db")
    add_clean(store)
    v = store.vendor_summaries()[0]
    store.save_vendor(v["vendor_key"], v["vendor_name"], ON_HOLD, notes="bank details changed")
    app = create_app(web_settings(), store, start_reader=False, server=True)
    clients = {}
    for role in (CLERK, APPROVER):
        uid = store.add_user(f"the.{role}", "correct horse battery", role=role, display_name=f"The {role.title()}")
        with store._conn() as conn:
            conn.execute("UPDATE users SET must_change = 0 WHERE id = ?", (uid,))
        enrol(store, uid)  # its authenticator app set up (D-146)
        client = TestClient(app, base_url="https://localhost", client=("10.0.0.5", 50000), follow_redirects=False)
        assert sign_in(client, f"the.{role}", "correct horse battery").status_code == 200
        clients[role] = client
    return store, v["vendor_key"], clients


def test_a_clerk_cannot_take_a_vendor_off_hold(held):
    """D-142: a Clerk lifting a hold, then approving under the limit, would get around D-141's hold."""
    store, key, clients = held
    answer = clients["clerk"].post("/vendors/save", data={"key": key, "status": "active", "notes": "ok now"},
                                   headers=PAGE)  # fmt: skip
    assert answer.status_code == 403 and "Only an Approver can take a vendor off hold." in answer.text
    saved = store.get_vendor(key)
    assert saved["status"] == ON_HOLD and saved["notes"] == "bank details changed"
    detail = clients["clerk"].get("/vendors?" + urlencode({"open": key})).text
    assert re.search(r'<input type="radio" name="status" value="active"[^>]* disabled', detail)
    assert "Only an Approver can take a vendor off hold." in detail


def test_a_clerk_may_still_edit_a_held_vendors_notes_and_put_a_vendor_on_hold(held):
    store, key, clients = held
    data = {"key": key, "status": ON_HOLD, "notes": "called them", "province": "BC"}
    follow(clients["clerk"], clients["clerk"].post("/vendors/save", data=data, headers=PAGE))
    saved = store.get_vendor(key)
    assert (saved["status"], saved["notes"], saved["province"]) == (ON_HOLD, "called them", "BC")
    store.save_vendor(key, saved["display_name"], "active")
    follow(clients["clerk"], clients["clerk"].post("/vendors/save", data={"key": key, "status": ON_HOLD}, headers=PAGE))
    assert store.get_vendor(key)["status"] == ON_HOLD


@pytest.mark.parametrize("field", ["expected_gst", "qst_number", "pst_number"])
def test_a_clerk_cannot_change_a_held_vendors_tax_numbers(held, field):
    """B-1554: a Clerk cleared a held vendor's GST/HST number (keeping the hold), so an invoice showing it no longer
    tied to the held vendor and "Approve anyway" went through. The numbers the hold is matched by are an Approver's
    to change while the vendor is on hold, as the hold itself is (D-142)."""
    store, key, clients = held
    numbers = {"expected_gst": "123456782RT0001", "qst_number": "1234567890TQ0001", "pst_number": "PST-1234-5678"}
    store.save_vendor(key, store.get_vendor(key)["display_name"], ON_HOLD, numbers["expected_gst"],
                      qst_number=numbers["qst_number"], pst_number=numbers["pst_number"])  # fmt: skip
    form = {"key": key, "status": ON_HOLD, **numbers}
    refused = clients["clerk"].post("/vendors/save", data={**form, field: ""}, headers=PAGE)
    assert refused.status_code == 403 and "Only an Approver can change" in refused.text
    assert store.get_vendor(key)[field] == numbers[field]
    detail = clients["clerk"].get("/vendors?" + urlencode({"open": key})).text
    assert re.search(rf'name="{field}" value="[^"]*"[^>]* readonly', detail)
    follow(clients["approver"], clients["approver"].post("/vendors/save", data={**form, field: ""}, headers=PAGE))
    assert store.get_vendor(key)[field] == ""


def test_an_approver_takes_a_vendor_off_hold_and_it_is_in_the_audit_trail(held):
    store, key, clients = held
    detail = clients["approver"].get("/vendors?" + urlencode({"open": key})).text
    assert not re.search(r'<input type="radio" name="status" value="active"[^>]* disabled', detail)
    follow(clients["approver"], clients["approver"].post("/vendors/save", data={"key": key, "status": "active"},
                                                         headers=PAGE))  # fmt: skip
    assert store.get_vendor(key)["status"] == "active"
    lifted = [e for e in store.events(None, ["vendor_updated"]) if e["detail"].get("status") == "active"]
    assert lifted and lifted[0]["actor"] == "The Approver"


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
        ["Gamma Signs", "", "", "", ""],
        ["GAMMA SIGNS INC", "", "", "", ""],
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
    assert missing.status_code == 400 and "Vendor name" in missing.json()["error"]
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
    # two supplier numbers under one name are two vendors, named with their numbers (B-2161); two rows of one name
    # with no number to tell them apart are one, as before
    assert "Loaded: 4 added" in page.text and "1 name is shared by more than one supplier number" in page.text
    assert "V2 Beta Supply / V3 BETA SUPPLY." in page.text
    assert "more than once" in page.text and "Gamma Signs / GAMMA SIGNS INC" in page.text
    assert {v["erp_id"] for v in busy.master_vendors()} >= {"V1", "V2", "V3"}
    assert busy.get_vendor("beta supply")["status"] == ON_HOLD  # (V2, as its Status column says)
    assert busy.get_vendor("beta supply #V3")["status"] == "active"


def guessed(html: str) -> dict[str, str]:
    """{field: the column the page offers already chosen} of an import's column choices ("" for none)."""
    chosen = {}
    for field, options in re.findall(r'<select name="col_(\w+)">(.*?)</select>', html, re.S):
        picked = re.search(r'<option value="([^"]*)" selected>', options)
        chosen[field] = picked.group(1) if picked else ""
    return chosen


def test_jd_edwards_supplier_master_is_imported_as_the_page_offers_it(client, store):
    """B-2082: the supplier master's export uploaded and imported with the columns the page guesses, none picked: the
    hold is ``Hold Payment Code`` (Y, 1 and 2 hold; N, JD Edwards' usual value, does not), the free-text tax ID is not
    the GST/HST number, and a code the JDE team confirms later (0) is one setting away, on the same form."""
    from tests.jde_exports import supplier, supplier_master

    listed = [supplier(410001, "MAPLE LEAF COFFEE SERVICES INC."), supplier(410002, "QUICKSHIP COURIER CANADA LTD"),
              supplier(410005, "HARBOUR TELECOM", "2", tax="123456789RT0001"),
              supplier(410006, "PRAIRIE OFFICE FURNITURE", "0", tax="VERIFIED BY PHONE 2019"),
              supplier(410010, "CEDAR RIDGE TOWING", "Y"), supplier(410011, "SUMMIT PEST CONTROL", "1")]  # fmt: skip

    page_after: list[str] = []  # the page the last import led to, with what it said

    def imported(**typed):
        page = upload(client, "/vendors/master/upload", "suppliers.xlsx", supplier_master(listed))
        offered = guessed(page.text)
        data = {"file": page.url.params["file"], **{f"col_{f}": c for f, c in offered.items()}, **typed}
        page_after[:] = [follow(client, client.post("/vendors/master/import", data=data, headers=PAGE)).text]
        return page, offered

    page, offered = imported()
    assert offered == {"vendor_name": "Alpha Name", "erp_id": "Address Number", "gst": "", "qst": "", "pst": "",
                       "province": "", "terms": "", "status": "Status", "hold": "Hold Payment Code",
                       "default_gl": ""}  # fmt: skip
    assert 'name="hold_codes" value="Y, 1, 2"' in page.text
    vendors = {v["erp_id"]: v for v in store.master_vendors()}
    assert {i: v["erp_hold"] for i, v in vendors.items()} == {"410001": "", "410002": "", "410005": "2", "410006": "",
                                                              "410010": "Y", "410011": "1"}  # fmt: skip
    assert {(v["status"], v["expected_gst"]) for v in vendors.values()} == {("active", "")}
    # 2 holds the payments only: no hold on the invoices (B-2157)
    assert sorted(v["display_name"] for v in store.held_vendors()) == ["CEDAR RIDGE TOWING", "SUMMIT PEST CONTROL"]
    # a code the list does not name is said, not dropped in silence (B-2159); and what a change reaches is on the form
    said = page_after[0]
    assert "1 supplier with hold payment code 0: not treated as a hold." in said
    assert (
        "Hold payment codes that put a supplier on hold" in said
        and "reaches the suppliers of this file now" in page.text
    )
    page, _ = imported(hold_codes="y,1 , 2, 0")
    assert "not treated as a hold" not in page_after[0]
    assert store.get_vendor("prairie office furniture")["erp_hold"] == "0"
    assert store.hold_codes() == ["Y", "1", "2", "0"]
    assert (
        'name="hold_codes" value="Y, 1, 2, 0"'
        in upload(client, "/vendors/master/upload", "suppliers.xlsx", supplier_master(listed)).text
    )


def test_a_supplier_marked_do_not_use_in_the_export_is_on_hold_and_the_words_are_on_the_form(client, store):
    """B-2083: imported through the page, a supplier the master marks as retired is found by its real name and on
    hold; the words that mark one are the import form's, kept as a setting."""
    from tests.jde_exports import supplier, supplier_master

    listed = [supplier(410003, "NORTHERN PAPER CO  DO NOT USE"), supplier(410012, "CEDAR RIDGE TOWING - CLOSED"),
              supplier(410001, "MAPLE LEAF COFFEE SERVICES INC.")]  # fmt: skip
    page = upload(client, "/vendors/master/upload", "suppliers.xlsx", supplier_master(listed))
    assert 'name="skip_words" value="DO NOT USE, DNU, *"' in page.text
    data = {"file": page.url.params["file"], **{f"col_{f}": c for f, c in guessed(page.text).items()}}
    follow(client, client.post("/vendors/master/import", data={**data, "skip_words": "DO NOT USE, DNU, closed"},
                               headers=PAGE))  # fmt: skip
    assert store.skip_words() == ["DO NOT USE", "DNU", "closed"]
    held = {v["display_name"]: v["notes"] for v in store.held_vendors()}
    assert held == {"NORTHERN PAPER CO  DO NOT USE": "marked DO NOT USE in the supplier master",
                    "CEDAR RIDGE TOWING - CLOSED": "marked CLOSED in the supplier master"}  # fmt: skip
    assert store.find_vendor("Northern Paper Co")["erp_id"] == "410003"
    assert store.find_vendor("Cedar Ridge Towing")["erp_id"] == "410012"
    # the star is a word of the list: taken off it, a star marks nothing (B-2160)
    starred = [supplier(410020, "*** MISC ONE-TIME VENDOR ***")]
    page = upload(client, "/vendors/master/upload", "suppliers.xlsx", supplier_master(starred))
    data = {"file": page.url.params["file"], **{f"col_{f}": c for f, c in guessed(page.text).items()}}
    follow(client, client.post("/vendors/master/import", data=data, headers=PAGE))
    assert store.find_vendor("Misc One-Time Vendor")["status"] == "active"
    page = upload(client, "/vendors/master/upload", "suppliers.xlsx", supplier_master(starred))
    follow(client, client.post("/vendors/master/import", data={**data, "file": page.url.params["file"],
                                                              "skip_words": "DNU, *"}, headers=PAGE))  # fmt: skip
    assert store.skip_words() == ["DNU", "*"]
    assert store.find_vendor("Misc One-Time Vendor")["notes"] == "marked * in the supplier master"
    assert store.chart_settings().skip_words == ("DNU", "*")  # one setting: the chart finds no word in the star
    # left blank: no word at all, for the chart of accounts too, which reads the same setting
    page = upload(client, "/vendors/master/upload", "suppliers.xlsx", supplier_master(starred))
    follow(client, client.post("/vendors/master/import", data={**data, "file": page.url.params["file"],
                                                              "skip_words": " "}, headers=PAGE))  # fmt: skip
    assert store.skip_words() == [] and store.find_vendor("Misc One-Time Vendor")["status"] == "active"
    assert store.chart_settings().skip_words == ()


def test_a_vendor_lists_qst_and_pst_numbers_and_province_are_imported_by_the_page(client, store):
    """B-2400: the vendor master's reader reads a QST number, a PST number and a province, but the Vendors page offered
    no column choice for them: imported with the columns it offered, a list's QST and PST numbers and provinces were
    dropped."""
    rows = [["Vendor Name", "Vendor ID", "QST Number", "PST Number", "Province"],
            ["Erable Fournitures", "V9", "1234567890TQ0001", "PST-1234-5678", "QC"]]  # fmt: skip
    page = upload(client, "/vendors/master/upload", "vendors.xlsx", xlsx(rows))
    offered = guessed(page.text)
    assert {offered.get(f) for f in ("qst", "pst", "province")} == {"QST Number", "PST Number", "Province"}
    data = {"file": page.url.params["file"], **{f"col_{f}": c for f, c in offered.items()}}
    follow(client, client.post("/vendors/master/import", data=data, headers=PAGE))
    (vendor,) = store.master_vendors()
    held = (vendor["qst_number"], vendor["pst_number"], vendor["province"])
    assert held == ("1234567890TQ0001", "PST-1234-5678", "QC")


def test_the_bank_accounts_import_needs_only_the_code_as_its_reader_does(client, store):
    """B-2402: the Bank accounts import marked its currency column as needed (*), though a blank currency is CAD and
    a file without the column is imported: only the code is needed."""
    page = upload(client, "/accounts/bank/upload", "b.csv", b"Code,Name\nCAD-2,Second operating\n")
    marked = r'<label class="field">{}[^<]*(?:\*|<span class="imp-tag">needed)'
    assert re.search(marked.format("Code"), page.text) and not re.search(marked.format("Currency"), page.text)
    data = {"file": page.url.params["file"], **{f"col_{f}": c for f, c in guessed(page.text).items()}}
    follow(client, client.post("/accounts/bank/import", data=data, headers=PAGE))
    assert [(a["code"], a["currency"]) for a in store.bank_accounts()] == [("CAD-2", "CAD")]


def test_a_bank_code_typed_in_another_case_is_the_same_account(client, store):
    """As the import's own check and JD Edwards' pay-from match codes whatever their case, "cad-op" in a file is
    the account CAD-OP: changed in place, its spelling, default and unpicked columns kept, not a second account."""

    def load(content: bytes) -> None:
        page = upload(client, "/accounts/bank/upload", "b.csv", content)
        data = {"file": page.url.params["file"], **{f"col_{f}": c for f, c in guessed(page.text).items()}}
        follow(client, client.post("/accounts/bank/import", data=data, headers=PAGE))

    load(b"Code,Name,Account number,Default\nCAD-OP,Operating,1234567,yes\n")
    load(b"Code,Name\ncad-op,Operating again\n")
    accounts = [(a["code"], a["name"], a["is_default"], a["account_number"]) for a in store.bank_accounts()]
    assert accounts == [("CAD-OP", "Operating again", True, "1234567")]


def test_an_exact_bank_code_is_its_own_account_when_a_database_holds_both_spellings(client, store):
    """A database from before B-2551 can hold CAD-OP and cad-op: the code typed exactly is that account, in the
    Store, the import (its unpicked columns) and the grid (a blank account number), whichever sorts last."""
    from ap_coder.coding import banks

    rows, _ = banks.clean_rows([{"code": "CAD-OP", "name": "A upper", "currency": "CAD", "account_number": "1111111"}])
    store.save_bank_accounts([*rows, {**rows[0], "code": "cad-op", "name": "B lower", "account_number": "2222222"}])

    def kept() -> list[tuple[str, str, str]]:
        return sorted((a["code"], a["name"], a["account_number"]) for a in store.bank_accounts())

    assert store.save_bank_accounts([{**rows[0], "name": "Changed"}]) == {"added": 0, "updated": 1}
    assert kept() == [("CAD-OP", "Changed", "1111111"), ("cad-op", "B lower", "2222222")]
    page = upload(client, "/accounts/bank/upload", "b.csv", b"Code,Name\nCAD-OP,Imported\n")
    data = {"file": page.url.params["file"], **{f"col_{f}": c for f, c in guessed(page.text).items()}}
    follow(client, client.post("/accounts/bank/import", data=data, headers=PAGE))
    assert kept() == [("CAD-OP", "Imported", "1111111"), ("cad-op", "B lower", "2222222")]
    form = {"code": ["CAD-OP"], "name": ["Typed"], "currency": ["CAD"], "institution": [""], "transit": [""],
            "account_number": [""], "gl_account": [""], "active": ["0"]}  # fmt: skip
    follow(client, client.post("/accounts/bank/save", data=form, headers=PAGE))
    assert kept() == [("CAD-OP", "Typed", "1111111"), ("cad-op", "B lower", "2222222")]


def test_the_sample_vendor_master_is_imported_by_the_page_as_the_sample_loader_does(client, store, tmp_path):
    """The repo's ``data/vendor_master.csv`` through the Vendors page, with the columns it offers: the same vendors,
    field for field, as ``load_sample_vendor_master`` makes, none on hold in JD Edwards."""
    from ap_coder.core.store import load_sample_vendor_master
    from ap_coder.core.store.samples import SAMPLE_DATA_DIR

    page = upload(client, "/vendors/master/upload", "vendor_master.csv",
                  (SAMPLE_DATA_DIR / "vendor_master.csv").read_bytes())  # fmt: skip
    offered = guessed(page.text)
    assert offered == {"vendor_name": "vendor_name", "erp_id": "vendor_id", "gst": "gst_hst_number", "hold": "",
                       "qst": "", "pst": "", "province": "", "terms": "payment_terms", "status": "status",
                       "default_gl": "default_gl"}  # fmt: skip
    data = {"file": page.url.params["file"], **{f"col_{f}": c for f, c in offered.items()}}
    follow(client, client.post("/vendors/master/import", data=data, headers=PAGE))
    loaded = Store(tmp_path / "loaded.db")
    load_sample_vendor_master(loaded)

    def rows(of):
        return sorted(tuple((k, v) for k, v in r.items() if k != "updated_at") for r in of.master_vendors())

    assert rows(store) == rows(loaded) and len(rows(store)) == 8
    assert {v["erp_hold"] for v in store.master_vendors()} == {""}


# --- Long lists, a part at a time (D-162) -------------------------------------------------------------------------


def test_how_many_rows_a_long_list_shows():
    """``shown_count``: the step, or more in whole steps of it, a million at most; the step for junk or too few."""
    from ap_coder.web.core import shown_count

    asked = ("", None, "abc", "1.5", "-3", "0", "1", "50", "51", "100", "149", 400)
    assert [shown_count(value, 50) for value in asked] == [50, 50, 50, 50, 50, 50, 50, 50, 100, 100, 150, 400]
    assert shown_count("99999999", 200) == shown_count(10**9, 500) == 1_000_000


def test_the_vendor_list_shows_a_part_at_a_time_and_keeps_its_length_when_one_is_opened(client, busy, monkeypatch):
    """Vendors, ``VENDORS_SHOWN`` at a time ("Show more"): the tiles count every one, search finds any, one beyond
    the part shown opens, and its links keep the list's length; under the step, the page is as it was."""
    from markupsafe import escape

    from ap_coder.web.pages import vendors as page

    vendors = page.sort_vendors(busy.vendor_summaries(), "spend")
    whole = client.get("/vendors").text
    assert len(vendors) > 4 and "Show more" not in whole and 'id="more-vendors"' not in whole
    monkeypatch.setattr(page, "VENDORS_SHOWN", 2)

    def listed(text: str) -> list[str]:
        return re.findall(r'class="vendor-link" href="/vendors\?([^"]*)"', text)

    first = client.get("/vendors").text
    assert len(listed(first)) == 2 and f"2 of {len(vendors)} vendors shown" in first
    assert '<a class="btn sm" href="/vendors?view=all&amp;q=&amp;sort=spend&amp;shown=4#more-vendors">' in first
    assert f"{sum(v['invoices'] for v in vendors)} invoices" in first  # (the tiles: every vendor)
    more = client.get("/vendors?shown=3").text  # (whole steps)
    assert len(listed(more)) == 4 and more.count('id="more-vendors"') == 1 and "shown=6#more-vendors" in more
    assert all(link.startswith("view=all&amp;q=&amp;sort=spend&amp;shown=4&open=") for link in listed(more))
    everyone = client.get("/vendors?shown=1000000").text
    assert len(listed(everyone)) == len(vendors) and "Show more" not in everyone
    last = vendors[-1]
    assert str(escape(last["vendor_name"])) in client.get("/vendors", params={"q": last["vendor_name"]}).text
    opened = client.get("/vendors", params={"open": last["vendor_key"], "shown": "4"}).text
    assert f'name="key" value="{escape(last["vendor_key"])}"' in opened and len(listed(opened)) == 4
    assert 'href="/vendors?view=all&amp;q=&amp;sort=spend&amp;shown=4#list"' in opened  # (Close keeps it too)


def test_the_po_list_shows_a_part_at_a_time(client, busy, monkeypatch):
    """Purchase orders, ``POS_SHOWN`` at a time ("Show more"): the tiles count every PO, search finds any, and one
    beyond the part shown opens."""
    from ap_coder.web.pages import purchase_orders as page

    pos = [p for p in busy.purchase_orders() if p["status"] == po_mod.OPEN]
    assert len(pos) > 2 and "Show more" not in client.get("/purchase-orders").text
    monkeypatch.setattr(page, "POS_SHOWN", 1)

    def listed(text: str) -> list[str]:
        return re.findall(r'<td><a href="/purchase-orders\?([^"]*)#po"><b>', text)

    first = client.get("/purchase-orders").text
    assert len(listed(first)) == 1 and f"1 of {len(pos)} purchase orders shown" in first
    assert '<a class="btn sm" href="/purchase-orders?q=&amp;status=open&amp;shown=2#more-pos">' in first
    assert f"{len(busy.purchase_orders())} in total" in first  # (the tiles: every PO)
    more = client.get("/purchase-orders?shown=2").text
    assert len(listed(more)) == 2 and more.count('id="more-pos"') == 1
    assert all(link.startswith("q=&amp;status=open&amp;shown=2&po=") for link in listed(more))
    last = pos[-1]
    found = client.get("/purchase-orders", params={"q": last["po_number"]}).text
    assert len(listed(found)) == 1 and f"<b>{last['po_number']}</b>" in found
    opened = client.get("/purchase-orders", params={"po": last["po_key"]}).text
    assert len(listed(opened)) == 1 and f"<div><b>{last['po_number']}</b>" in opened  # (its lines, below)


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
    assert "(2 POs with 3 lines)" in page.text
    assert store_po(busy, "PO-9001")["total"] == 2300
    text = client.get("/purchase-orders?q=9001").text
    assert "PO-9001" in text and "PO-9002" not in text


def test_the_po_import_takes_an_optional_open_amount_column(client, store):
    sheet = "PO,Item,Amount,AOPN\nPO-9101,Laptops,2000,1500\nPO-9101,Docks,300,\n"
    page = upload(client, "/purchase-orders/upload", "pos.csv", sheet.encode())
    assert '<label class="field">Open amount' in page.text  # the column picker offers it, AOPN recognised
    assert '<option value="AOPN" selected>' in page.text.split('name="col_open_amount"')[1].split("</select>")[0]
    form = {"file": page.url.params["file"], "col_po_number": "PO", "col_description": "Item", "col_amount": "Amount",
            "col_open_amount": "AOPN"}  # fmt: skip
    follow(client, client.post("/purchase-orders/import", data=form, headers=PAGE))
    assert [li["open_amount"] for li in store_po(store, "PO-9101")["lines"]] == [1500.0, None]


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
    assert "Loaded: 3 added, 0 updated, 0 skipped" in page.text
    assert "Software &lt;b&gt;" in page.text and "Opex · 2" in page.text
    # download: the CSV the earlier dashboard gave
    data = client.get("/accounts/gl_accounts.csv")
    assert (
        data.headers["content-type"].startswith("text/csv") and "gl_accounts.csv" in data.headers["content-disposition"]
    )
    rows = [{**a, "meals": "yes" if a["meals"] else ""} for a in store.list_accounts("gl_accounts")]  # (D-90)
    expected = pd.DataFrame(rows).map(csv_cell).to_csv(index=False).encode("utf-8-sig")
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
    sample = (Path(__file__).resolve().parents[2] / "data" / "bank_accounts.csv").read_bytes()
    page = upload(client, "/accounts/bank/upload", "bank_accounts.csv", sample)
    for field, column in (("code", "code"), ("currency", "currency"), ("account_number", "account_number"),
                          ("is_default", "default")):  # fmt: skip
        assert f'<select name="col_{field}">' in page.text and f'<option value="{column}" selected>' in page.text
    token = page.url.params["file"]
    picked = {f"col_{f}": c for f, c in (("code", "code"), ("name", "name"), ("currency", "currency"),
              ("institution", "institution"), ("transit", "transit"), ("account_number", "account_number"),
              ("gl_account", "gl_account"), ("is_default", "default"), ("active", "active"))}  # fmt: skip
    page = follow(client, client.post("/accounts/bank/import", data={"file": token, **picked}, headers=PAGE))
    assert "Loaded: 2 added, 0 updated." in page.text
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
            "/rules",
            data={
                "vendor": ["Purolator", "", "", ""],
                "contains": ["", "freight", "", ""],
                "gl_code": [gl, gl, gl, ""],
                "cost_center": ["", "", "", ""],
                "version": str(busy.rules_seen()),
            },
            headers=PAGE,
        ),
    )
    assert [(r.vendor, r.contains) for r in busy.coding_rules()] == [("Purolator", ""), ("", "freight")]
    assert "2 coding rules saved; 1 incomplete row left out" in page.text
    follow(client, client.post("/rules/add", data={"vendor": "Acme", "gl_code": gl}, headers=PAGE))
    assert Rule("Acme", "", gl, "") in [
        Rule(r.vendor, r.contains, r.gl_code, r.cost_center) for r in busy.coding_rules()
    ]
    assert client.post("/rules/add", data={"vendor": "Acme"}, headers=PAGE).status_code == 400
    assert 'value="Purolator"' in client.get("/rules").text


def test_rules_need_gl_accounts_first(client, store):
    assert "Import your GL accounts first" in client.get("/rules").text
    assert client.post("/rules", data={"vendor": ["x"], "gl_code": ["1"]}, headers=PAGE).status_code == 400


def test_an_excel_cell_reading_na_is_kept_as_typed():
    """W2-04: the Excel readers dropped cells that read "NA", "NULL", "None", "N/A" (a cost center "NA" for North
    America vanished), while the same file as CSV kept them: every reader keeps a cell as it was typed."""
    from ap_coder.web.files import read_sheet
    from ap_coder.web.pages import learning

    book = openpyxl.Workbook()
    book.active.append(["Cost center", "Name"])
    for code in ("EU", "NA", "NULL", "N/A", "None"):
        book.active.append([code, "x"])
    raw = io.BytesIO()
    book.save(raw)
    codes = ["EU", "NA", "NULL", "N/A", "None"]
    df, _names = read_sheet("cc.xlsx", raw.getvalue())
    assert list(df["Cost center"]) == codes  # (Settings → JD Edwards' GL map is read by it too)
    ((_columns, rows),) = learning.read_sheets("cc.xlsx", raw.getvalue()).values()
    assert [r["Cost center"] for r in rows] == codes


def test_the_bank_accounts_version_stamp_holds_no_account_number(client, store):
    """B-687: the page's version stamp was a hash of the rows with their full account numbers: a short number could
    be found again from the page by trying every one. It is a hash of what the page shows (the last 4 digits) and when
    each account last changed."""
    from ap_coder.coding import banks
    from ap_coder.web.core import page_version

    account = {"code": "CAD-OP", "name": "Operating", "currency": "CAD", "account_number": "1234567"}
    rows, _ = banks.clean_rows([account])
    store.save_bank_accounts(rows)
    page = client.get("/accounts?tab=bank").text
    stamp = page.split('name="version" value="', 1)[1].split('"', 1)[0]
    full = store.bank_accounts()
    assert stamp != page_version(full)  # keyed with this database's own secret: nothing to try numbers against
    store.save_bank_accounts([{**rows[0], "account_number": "7654567"}])  # another number, the same last 4 digits
    page = client.get("/accounts?tab=bank").text
    assert page.split('name="version" value="', 1)[1].split('"', 1)[0] != stamp  # still a new page version
    stale = client.post("/accounts/bank/save", data={"version": stamp, "code": ["CAD-OP"]}, headers=PAGE)
    assert stale.status_code == 409


# --- A title above the column names (B-1513) -----------------------------------------------------------------------

TITLED = [
    ["Chart of accounts export - Fabrikam Canada"],
    [],
    ["Object Account", "Account Description", "Account Type"],
    ["6010", "IT Hardware", "Opex"],
    ["1500", "Computer Equipment", "Capex"],
]


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("coa.xlsx", xlsx(TITLED)),
        ("coa.csv", "\n".join(",".join(r) for r in TITLED).encode()),
        ("coa.csv", "\n".join(",".join(r + [""] * (3 - len(r))) for r in TITLED).encode()),  # Excel's "CSV": commas
    ],
)
def test_a_report_title_above_the_column_names_is_skipped(name, content):
    """B-1513: an ERP export opens with its report's title (and a blank line) above the column names: the title was
    taken for the column names (the choices read "Chart of accounts export… / Unnamed: 1 / Unnamed: 2") and the real
    ones became a row of data."""
    from ap_coder.web.files import read_sheet

    df, _ = read_sheet(name, content)
    assert list(df.columns) == ["Object Account", "Account Description", "Account Type"]
    assert df.values.tolist() == [["6010", "IT Hardware", "Opex"], ["1500", "Computer Equipment", "Capex"]]


def test_gl_accounts_under_a_report_title_are_guessed_and_imported(client, store):
    page = upload(client, "/accounts/gl_accounts/upload", "coa.xlsx", xlsx(TITLED))
    assert '<option value="Object Account" selected>' in page.text
    assert '<option value="Account Description" selected>' in page.text
    token = page.url.params["file"]
    data = {"file": token, "col_code": "Object Account", "col_description": "Account Description"}
    page = follow(client, client.post("/accounts/gl_accounts/import", data=data, headers=PAGE))
    assert "Loaded: 2 added, 0 updated, 0 skipped" in page.text
    assert sorted(a["code"] for a in store.list_accounts("gl_accounts")) == ["1500", "6010"]


def test_a_sheet_whose_first_row_is_its_column_names_is_read_as_before():
    from ap_coder.web.files import read_sheet

    rows = [["Code", "Description", "", ""], ["CC100", "Executive", "", ""], ["CC200", "Finance", "", ""]]
    df, _ = read_sheet("cc.xlsx", xlsx(rows))
    assert list(df.columns)[:2] == ["Code", "Description"] and df["Code"].tolist() == ["CC100", "CC200"]


def test_a_csv_whose_rows_end_with_a_delimiter_its_header_lacks_keeps_its_columns():
    """B-2545: every data row ended with a comma the header line has not: pandas took the first column for the rows'
    index and dropped it, so "Invoice" held the dates and "Amount" nothing, under names that still matched."""
    from ap_coder.web.files import read_sheet

    content = b"Invoice,Date,Amount\nINV-1,2026-09-01,100.00,\nINV-2,2026-09-05,50.00,\n"
    df, _ = read_sheet("statement.csv", content)
    assert list(df.columns) == ["Invoice", "Date", "Amount"]
    assert df.values.tolist() == [["INV-1", "2026-09-01", "100.00"], ["INV-2", "2026-09-05", "50.00"]]


@pytest.mark.parametrize(
    ("url", "preface", "header", "row"),
    [
        ("/statements/upload", ["Statement date", "2026-09-30"], ["Invoice number", "Date", "Amount"],
         ["INV-1", "2026-09-01", "100.00"]),
        ("/purchase-orders/upload", ["Vendor", "Acme Ltd"], ["PO Number", "Description", "Amount"],
         ["PO-9001", "Laptops", "2000"]),
        ("/exports/register/upload", ["Company", "Fabrikam Canada"], ["Vendor", "Invoice number", "Date", "Total"],
         ["Acme Ltd", "A-1", "2026-01-05", "100.00"]),
    ],
)  # fmt: skip
def test_a_two_cell_line_above_the_column_names_is_not_taken_for_them(client, busy, url, preface, header, row):
    """B-2546: a statement's "Statement date | 2026-09-30" (or "Vendor | Acme Ltd") under its title was taken for the
    column names, the real ones a row of data: nothing was guessed and no column could be picked."""
    data = {"vendor": busy.vendor_summaries()[0]["vendor_key"]} if url.startswith("/statements") else {}
    content = xlsx([["Statement of account"], preface, [], header, row])
    mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    page = follow(client, client.post(url, data=data, files={"file": ("s.xlsx", content, mime)}, headers=PAGE))
    assert all(f'<option value="{name}" selected>' in page.text for name in header)
    assert f'value="{preface[0]}"' not in page.text


def test_an_old_excel_file_is_refused_saying_so(client, busy):
    """B-2547: the import pages said a .xls was read, but an old Excel file (no reader for it is installed) was
    refused as "empty, damaged or another format"; it is named, and the pages ask for .csv or .xlsx."""
    from ap_coder.web.files import SheetError, read_sheet
    from ap_coder.web.pages import learning

    old = bytes.fromhex("D0CF11E0A1B11AE1") + b"\0" * 504
    with pytest.raises(SheetError, match=r"older Excel file \(\.xls\)"):
        read_sheet("statement.xls", old)
    with pytest.raises(ValueError, match=r"older Excel file \(\.xls\)"):
        learning.read_sheets("history.xls", old)
    answer = client.post("/statements/upload", files={"file": ("s.xls", old, "application/vnd.ms-excel")}, headers=PAGE)
    assert answer.status_code == 400 and "save it as Excel Workbook (.xlsx)" in answer.json()["error"]
    for page in ("/statements", "/purchase-orders", "/vendors", "/accounts", "/exports", "/learning"):
        assert "xlsx or .xls" not in client.get(page).text, page


@pytest.mark.parametrize("sep", [",", "\t"])
def test_a_utf16_csv_is_read_as_its_text(sep):
    """B-2548: a CSV saved as UTF-16 (PowerShell's Out-File, Excel's "Unicode Text") was read as Windows-1252: its
    byte-order mark and a NUL between every letter in each column name and cell, so no column matched."""
    from ap_coder.web.files import read_sheet

    text = "\r\n".join(sep.join(r) for r in [["Invoice", "Date", "Amount"], ["INV-1", "2026-09-01", "100.00"]])
    for encoding in ("utf-16", "utf-16-be"):
        content = (b"\xfe\xff" if encoding == "utf-16-be" else b"") + text.encode(encoding)
        df, _ = read_sheet("statement.csv", content)
        assert list(df.columns) == ["Invoice", "Date", "Amount"], encoding
        assert df.values.tolist() == [["INV-1", "2026-09-01", "100.00"]], encoding


def test_names_typed_in_are_cleaned_as_the_who_page_cleans_them(client, store):
    """B-1531: Settings → Review's "Your name" and the review screen's "Add new vendor" kept any length and control or
    format characters (a bell, a right-to-left override that shows an approver's name reversed in the audit trail);
    the Who are you? page already cleans a name (``identity.clean_name``)."""
    from ap_coder.core import paths
    from tests.webclient import add_clean

    answer = client.post("/settings/review", data={"reviewer_name": "Dana\x07\u202eClerk " + "x" * 5000}, headers=PAGE)
    assert answer.status_code == 200
    saved = paths.read_user_settings().get("reviewer") or ""
    assert saved.startswith("Dana") and "\x07" not in saved and "\u202e" not in saved and len(saved) <= 60
    invoice_id = add_clean(store, vendor_name="Brand New Supply")
    client.post(f"/review/{invoice_id}/vendor", data={"name": "A\x00B\u202e Supply " + "y" * 20000}, headers=PAGE)
    with store._conn() as conn:
        names = [r[0] for r in conn.execute("SELECT display_name FROM vendors WHERE display_name LIKE 'A%'")]
    assert names and all("\x00" not in n and "\u202e" not in n and len(n) <= 120 for n in names)


def test_with_a_chart_loaded_gl_accounts_say_where_they_come_from_and_replace_warns_first(chart_company_client,
                                                                                            chart_company):  # fmt: skip
    """B-2423: GL accounts & tax said nothing of the chart of accounts loaded on JD Edwards files (230 accounts there,
    117 GL accounts here, 20 business units there and 14 cost centers here, unexplained), and a two-row list imported
    with "Replace my current list" took the GL accounts from 117 to 2 without a word of warning."""
    client, store = chart_company_client, chart_company.store
    gl, cc = len(store.list_accounts("gl_accounts")), len(store.list_accounts("cost_centers"))
    accounts = sum(1 for row in store.chart_accounts() if not row.is_header)
    page = re.sub(r"\s+", " ", client.get("/accounts?tab=gl").text)
    assert f"These {gl:,} GL accounts come from the chart of accounts loaded on" in page
    assert f"({accounts:,} accounts on {len(store.business_units()):,} business units)" in page
    assert f"These {cc:,} cost centers come from the" in re.sub(r"\s+", " ", client.get("/accounts?tab=cc").text)
    two = xlsx([["GL Code", "Account Name"], ["6010", "Paper"], ["6020", "Ink"]])
    page = upload(client, "/accounts/gl_accounts/upload", "two.xlsx", two)
    form = page.text.split('id="pick-gl_accounts"', 1)[1].split("</form>", 1)[0]
    assert f"data-replace-confirm=\"Replace the {gl:,} GL accounts listed now with this file's?" in form
    assert f"The chart of accounts loaded on JD Edwards files gives the {gl:,} GL accounts listed now" in form
    assert f"Replace my current list: of the {gl:,} GL accounts listed now, those not in this file are removed" in form
    assert "Load GL accounts</button>" in form


def test_no_bank_accounts_yet_says_what_this_person_can_do(store):
    """B-2458: with no bank account, the Bank accounts tab said "Import them above, or type them in the grid below"
    to a Clerk, who can do neither (and to an Approver, who has no import). Each role is told what it can do."""
    from tests.web.test_server_web import account, log_in, server_client

    said = {}
    for role in ("clerk", "approver", "admin"):
        account(store, role, role)
        client = server_client(store)
        log_in(client, role)
        page = client.get("/accounts?tab=bank").text
        said[role] = page.split("No bank accounts yet", 1)[1].split("</div>", 1)[0]
    assert "An Approver or an Admin adds them" in said["clerk"] and "grid" not in said["clerk"]
    assert "Type them in the grid below." in said["approver"] and "Import" not in said["approver"]
    assert "Import them above, or type them in the grid below." in said["admin"]


def test_with_a_chart_loaded_and_a_list_imported_too_the_page_counts_what_came_from_where(chart_company_client,
                                                                                         chart_company):  # fmt: skip
    """B-2475: with the chart loaded and then two GL accounts (and two cost centers) imported here on top, GL accounts
    & tax still said "These 119 GL accounts come from the chart of accounts" and "These 16 cost centers come from the
    20 business units": two of each came from the file imported here."""
    client, store = chart_company_client, chart_company.store
    gl, cc = len(store.list_accounts("gl_accounts")), len(store.list_accounts("cost_centers"))
    for table, rows in (("gl_accounts", [["Code", "Name"], ["9991", "Paper"], ["9992", "Ink"]]),
                        ("cost_centers", [["Code", "Name"], ["CC991", "Lab"], ["CC992", "Shop"]])):  # fmt: skip
        token = upload(client, f"/accounts/{table}/upload", "two.xlsx", xlsx(rows)).url.params["file"]
        data = {"file": token, "col_code": "Code", "col_description": "Name"}
        page = follow(client, client.post(f"/accounts/{table}/import", data=data, headers=PAGE))
        assert "Loaded: 2 added" in page.text
    for tab, noun, before in (("gl", "GL accounts", gl), ("cc", "cost centers", cc)):
        said = re.sub(r"\s+", " ", client.get(f"/accounts?tab={tab}").text).split('id="from-chart"', 1)[1][:400]
        assert f"{before:,} of these {before + 2:,} {noun} come from the" in said, said
        assert "the other 2 were added on this page" in said and "These " not in said
    page = upload(client, "/accounts/gl_accounts/upload", "more.xlsx", xlsx([["Code", "Name"], ["9993", "Toner"]]))
    form = page.text.split('id="pick-gl_accounts"', 1)[1].split("</form>", 1)[0]
    assert f"Those not in it are removed, though the chart of accounts loaded on JD Edwards files gives {gl:,}." in form
    assert f"files gives {gl:,} of the {gl + 2:,} GL accounts listed now" in form


@pytest.mark.parametrize(
    ("spec", "rows"),
    [
        ("STATEMENTS", [["Txn No.", "Posted On", "Txn Type", "Charge"], ["88231", "2026-09-01", "Invoice", "100.00"],
                        ["88232", "2026-09-04", "Credit", "-20.00"]]),
        ("STATEMENTS", [["N° pièce", "Émis le", "Nature", "Somme"], ["88231", "2026-09-01", "Facture", "100.00"],
                        ["88232", "2026-09-04", "Facture", "20.00"]]),
        ("PURCHASE_ORDERS", [["Ref", "Item", "Value", "Stage"], ["PO-9001", "Service", "2000.00", "Received"],
                             ["PO-9002", "Laptops", "900.00", "Open"]]),
        ("ERP_REGISTER", [["Entry", "Posted", "Kind", "Gross"], ["E1", "2026-01-05", "Invoice", "100.00"],
                          ["E2", "2026-01-06", "Invoice", "50.00"]]),
    ],
)  # fmt: skip
def test_a_data_cell_that_is_a_column_name_does_not_take_the_header(spec, rows):
    """B-2546: with a page's spec given, a header none of whose names were known lost to the first data row when a
    cell of it was a column's name ("Invoice" in a Type column): the real names and the first line were gone."""
    from ap_coder.erp import import_specs
    from ap_coder.web.files import read_sheet

    for name, content in (("s.csv", "\n".join(",".join(r) for r in rows).encode()), ("s.xlsx", xlsx(rows))):
        df, _ = read_sheet(name, content, specs=[getattr(import_specs, spec)])
        assert list(df.columns) == rows[0] and df.values.tolist() == rows[1:], name


def test_a_statement_whose_column_names_are_not_known_keeps_them_to_pick_from(client, busy):
    """B-2546, on the page: the column picker offers the statement's own names and the preview shows both lines."""
    key = busy.vendor_summaries()[0]["vendor_key"]
    content = b"Txn No.,Posted On,Txn Type,Charge\n88231,2026-09-01,Invoice,100.00\n88232,2026-09-04,Credit,-20.00\n"
    page = follow(client, client.post("/statements/upload", data={"vendor": key},
                                      files={"file": ("s.csv", content, "text/csv")}, headers=PAGE))  # fmt: skip
    assert 'value="Txn No."' in page.text and 'value="Txn Type"' in page.text
    assert 'value="88231"' not in page.text


def test_a_row_with_more_cells_than_the_header_is_refused_not_cut():
    """B-2545: an unquoted comma in a cell ("Acme, Inc.") gave the row a cell more than the header; with the extra
    cell dropped, its amount was lost silently. Only an empty extra cell (a trailing delimiter) is dropped."""
    from ap_coder.web.files import SheetError, read_sheet

    content = b"Invoice,Vendor,Amount\n1001,Acme, Inc.,250.00\n1002,Acme,100.00\n"
    with pytest.raises(SheetError, match="more cells than"):
        read_sheet("statement.csv", content)


@pytest.mark.parametrize("encoding", ["utf-16-le", "utf-16-be"])
def test_a_utf16_csv_without_its_mark_is_read_as_its_text(encoding):
    """B-2548: UTF-16 with no byte-order mark decoded as UTF-8, a NUL between every letter, and no column matched."""
    from ap_coder.web.files import read_sheet

    content = "Invoice,Date,Amount\r\nINV-1,2026-09-01,100.00\r\n".encode(encoding)
    df, _ = read_sheet("statement.csv", content)
    assert list(df.columns) == ["Invoice", "Date", "Amount"]
    assert df.values.tolist() == [["INV-1", "2026-09-01", "100.00"]]
