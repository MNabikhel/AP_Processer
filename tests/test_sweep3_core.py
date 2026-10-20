"""The third sweep's fixes in ap_coder/core: lessons withdrawn with an approval, imports that would wipe the GL list,
semicolon CSVs, backups dated in the future, re-keyed supplier profiles and lessons, amounts as people write them,
an old database's safety copy, Excel's trailing blank rows and places abroad."""

from __future__ import annotations

import copy
import os
import sqlite3
import time
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from ap_coder.ai import lessons
from ap_coder.capture.workflow import learn_from_approval
from ap_coder.core.config import Settings
from ap_coder.core.csvio import read_csv_rows
from ap_coder.core.reference_data import load_table
from ap_coder.core.safe import parse_amount
from ap_coder.core.schema import OUTSIDE_CANADA, province_code
from ap_coder.core.store import Store, load_sample_setup
from ap_coder.web.app import create_app
from ap_coder.web.files import read_sheet

PAGE = {"X-AP-Coder": "1"}

# --- B-158: a withdrawn approval stops teaching ---------------------------------------------------------------------


def _approve(store, invoice_id, coding):
    store.approve_invoice(invoice_id, copy.deepcopy(coding), "Jane")
    learn_from_approval(store, invoice_id, copy.deepcopy(coding), actor="Jane")


def _laptop_lesson(store):
    return next(r for r in store.lessons_for("northwind it solutions") if r["pattern"].startswith("dell latitude"))


def test_a_withdrawn_approval_no_longer_teaches_its_lessons(tmp_path, ground_truth):
    store = Store(tmp_path / "a.db")
    load_sample_setup(store)
    pdf = tmp_path / "inv.pdf"
    pdf.write_bytes(b"%PDF-1.4 x")
    iid = store.add_invoice(pdf, copy.deepcopy(ground_truth), {"requires_review": True})
    wrong = copy.deepcopy(ground_truth)
    wrong["line_items"][0]["predicted_gl_code"] = "6999"  # a slip, approved
    _approve(store, iid, wrong)
    assert any("GL 6999" in line for line in lessons.prompt_lines(store, wrong["vendor_name"], lessons.INVOICE))
    store.reopen(iid, "Jane", "wrong GL")  # withdrawn: the AI is no longer told 6999
    assert lessons.prompt_lines(store, wrong["vendor_name"], lessons.INVOICE) == []
    _approve(store, iid, ground_truth)  # approved as read: nothing corrected
    assert lessons.prompt_lines(store, wrong["vendor_name"], lessons.INVOICE) == []
    lesson = _laptop_lesson(store)
    assert (lesson["seen"], lesson["corrected"], lesson["since_why"]) == (1, 0, 0)
    store.reopen(iid, "Jane")
    store.reject_invoice(iid, "Jane", "duplicate")
    assert all(r["seen"] == 0 for r in store.lessons_for("northwind it solutions"))


def test_a_reopened_and_reapproved_correction_counts_once(tmp_path, ground_truth):
    store = Store(tmp_path / "a.db")
    load_sample_setup(store)
    pdf = tmp_path / "inv.pdf"
    pdf.write_bytes(b"%PDF-1.4 y")
    iid = store.add_invoice(pdf, copy.deepcopy(ground_truth), {"requires_review": True})
    second = tmp_path / "other.pdf"
    second.write_bytes(b"%PDF-1.4 other")
    other = store.add_invoice(second, copy.deepcopy(ground_truth), {"requires_review": True})
    earlier = copy.deepcopy(ground_truth)
    earlier["line_items"][0]["predicted_gl_code"] = "6050"
    _approve(store, other, earlier)  # another invoice, corrected another way
    fixed = copy.deepcopy(ground_truth)
    fixed["line_items"][0]["predicted_gl_code"] = "6999"
    _approve(store, iid, fixed)
    store.reopen(iid, "Jane", "date")  # reopened to fix the date, approved again
    assert _laptop_lesson(store)["value"] == "6050"  # the other invoice's correction is the one left
    _approve(store, iid, fixed)
    lesson = _laptop_lesson(store)
    assert (lesson["seen"], lesson["corrected"], lesson["since_why"], lesson["value"]) == (2, 2, 2, "6999")


def test_an_invoice_deleted_with_its_lessons_forgets_them(tmp_path, ground_truth):
    store = Store(tmp_path / "a.db")
    load_sample_setup(store)
    pdf = tmp_path / "inv.pdf"
    pdf.write_bytes(b"%PDF-1.4 z")
    iid = store.add_invoice(pdf, copy.deepcopy(ground_truth), {"requires_review": True})
    wrong = copy.deepcopy(ground_truth)
    wrong["line_items"][0]["predicted_gl_code"] = "6999"
    _approve(store, iid, wrong)
    store.delete_invoice(iid, forget_lessons=True)
    assert lessons.prompt_lines(store, wrong["vendor_name"], lessons.INVOICE) == []


# --- B-159: an import with no usable code never empties the GL list ------------------------------------------------


def test_an_import_with_no_usable_code_never_replaces_the_gl_accounts(tmp_path):
    store = Store(tmp_path / "a.db")
    load_sample_setup(store)
    before = store.list_accounts("gl_accounts")
    rows = [{"GL": "", "Description": "Office"}, {"GL": " ", "Description": "Travel"}]  # the wrong column picked
    with pytest.raises(ValueError, match="nothing was replaced"):
        store.import_accounts("gl_accounts", rows, "GL", "Description", replace_all=True)
    assert store.list_accounts("gl_accounts") == before
    store.reference_data()  # coding still works


def test_the_accounts_page_says_why_an_import_with_no_codes_is_refused(tmp_path):
    store = Store(tmp_path / "private" / "ap_coder.db")
    load_sample_setup(store)
    s = Settings()
    client = TestClient(create_app(replace(s, llm=replace(s.llm, provider="off")), store, start_reader=False),
                        base_url="http://localhost")  # fmt: skip
    sent = client.post("/accounts/gl_accounts/upload", headers=PAGE,
                       files={"file": ("gl.csv", b"GL,Description\r\n,Office\r\n,Travel\r\n", "text/csv")})  # fmt: skip
    token = client.get(sent.json()["redirect"]).url.params["file"]
    form = {"file": token, "col_code": "GL", "col_description": "Description", "replace": "1"}
    answer = client.post("/accounts/gl_accounts/import", headers=PAGE, data=form)
    assert answer.status_code == 400 and "nothing was replaced" in answer.json()["error"]
    assert len(store.list_accounts("gl_accounts")) > 20


# --- B-160: a semicolon CSV with a comma in its header ------------------------------------------------------------

FRENCH = "Compte GL;Description;Catégorie (dépenses, revenus)\r\n6000;Fournitures de bureau;Dépenses\r\n"


def test_a_semicolon_csv_with_a_comma_in_its_header_is_split_on_semicolons(tmp_path):
    path = tmp_path / "gl.csv"
    path.write_text(FRENCH + "6100;Déplacements;Dépenses\r\n", encoding="utf-8")
    rows = read_csv_rows(path)
    assert rows[0] == {"Compte GL": "6000", "Description": "Fournitures de bureau",
                       "Catégorie (dépenses, revenus)": "Dépenses"}  # fmt: skip
    df, _names = read_sheet("gl.csv", FRENCH.encode("utf-8"))
    assert list(df.columns) == ["Compte GL", "Description", "Catégorie (dépenses, revenus)"]
    assert df.iloc[0].tolist() == ["6000", "Fournitures de bureau", "Dépenses"]


def test_a_plain_comma_csv_is_still_read_on_commas(tmp_path):
    path = tmp_path / "gl.csv"
    path.write_text('GL Code,Description\r\n6000,"Office; supplies"\r\n6100,Travel\r\n', encoding="utf-8")
    assert read_csv_rows(path)[0] == {"GL Code": "6000", "Description": "Office; supplies"}
    df, _names = read_sheet("gl.csv", path.read_bytes())
    assert list(df.columns) == ["GL Code", "Description"] and df.iloc[0].tolist() == ["6000", "Office; supplies"]


# --- B-161: a backup dated in the future -------------------------------------------------------------------------


def test_a_backup_dated_in_the_future_does_not_stop_daily_backups(tmp_path):
    store = Store(tmp_path / "a.db")
    store.backup_dir().mkdir(parents=True)
    future = store.backup_dir() / "ap_coder-20271019-090000.db"  # made the day the laptop's clock was a year ahead
    store.backup_to(future)
    ahead = time.time() + 365 * 86400
    os.utime(future, (ahead, ahead))
    made = store.auto_backup()
    assert made is not None and made != future
    assert store.auto_backup() is None  # still once a day


# --- B-162: re-keyed supplier profiles and lessons ---------------------------------------------------------------


def _rules_changed(path):
    conn = sqlite3.connect(path)
    conn.execute("UPDATE settings SET value = '1' WHERE key = 'vendor_key_rules'")
    conn.commit()
    conn.close()
    return Store(path)


def test_a_held_supplier_stays_held_when_its_key_merges_under_new_rules(tmp_path):
    store = Store(tmp_path / "a.db")
    store.save_supplier_profile("name:energir sec", "Énergir s.e.c.", vendor_id="V-77")  # the older rules' key
    store.save_supplier_profile("name:energir", "Energir")
    store.set_supplier_state("name:energir sec", "held", "Lead", "keep an eye on it")
    store = _rules_changed(store.path)
    profiles = store.list_supplier_profiles()
    assert [(p["key"], p["state"], p["vendor_id"]) for p in profiles] == [("name:energir", "held", "V-77")]


def test_two_lessons_that_become_one_vendor_add_up(tmp_path):
    store = Store(tmp_path / "a.db")
    for key, name, invoices in (("energir sec", "Énergir s.e.c.", (1, 2)), ("energir", "Energir", (3,))):
        row = {"key": f"{key}|invoice|invoice_number|", "vendor": name, "kind": "invoice",
               "field": "invoice_number", "value": "A-1", "proposed": "1", "corrected": True}  # fmt: skip
        for n in invoices:
            store.record_lesson(row, invoice_id=n)
    conn = sqlite3.connect(store.path)  # as the older rules keyed the first one
    conn.execute("UPDATE lessons SET vendor_key = 'energir sec' WHERE vendor_name = 'Énergir s.e.c.'")
    conn.commit()
    conn.close()
    store = _rules_changed(store.path)
    rows = store.lessons_for("energir")
    assert [(r["situation"], r["seen"], r["corrected"], r["since_why"]) for r in rows] == [
        ("energir|invoice|invoice_number|", 3, 3, 3)
    ]
    assert store.lessons_for("energir sec") == []


# --- B-163: amounts as registers and statements write them ----------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "number"),
    [("CA$1,234.50", 1234.5), ("C$5", 5.0), ("US$99.00", 99.0), ("250.00 DR", 250.0), ("−150.00", -150.0),
     ("CAD 1 234,56", 1234.56), ("99.00 CR", -99.0), ("$ −12.00", -12.0)],
)  # fmt: skip
def test_parse_amount_reads_currency_prefixes_debits_and_the_minus_sign(text, number):
    assert parse_amount(text) == number


# --- B-164: the update's safety copy of a schema-1 database ------------------------------------------------------

SCHEMA_1 = """
CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE invoices (id INTEGER PRIMARY KEY AUTOINCREMENT, source_path TEXT NOT NULL, file_name TEXT NOT NULL,
    file_sha256 TEXT, status TEXT NOT NULL, vendor_name TEXT, vendor_key TEXT, invoice_number TEXT, invoice_date TEXT,
    currency TEXT, grand_total REAL, model_confidence REAL, adjusted_confidence REAL, requires_review INTEGER,
    ai_output TEXT, final_output TEXT, validation TEXT, extraction_md TEXT, meta TEXT, edits TEXT, error TEXT,
    created_at TEXT NOT NULL, reviewed_at TEXT, reviewer TEXT);
CREATE TABLE feedback (id INTEGER PRIMARY KEY AUTOINCREMENT, invoice_id INTEGER, line_number INTEGER,
    vendor_key TEXT NOT NULL, vendor_name TEXT NOT NULL, description TEXT NOT NULL, amount REAL, suggested_gl TEXT,
    final_gl TEXT NOT NULL, suggested_cc TEXT, final_cc TEXT, outcome TEXT NOT NULL, reviewer TEXT,
    created_at TEXT NOT NULL);
"""


def test_the_safety_copy_of_a_schema_1_database_can_be_restored(tmp_path):
    db = tmp_path / "ap_coder.db"
    conn = sqlite3.connect(db)
    conn.executescript(SCHEMA_1)  # no schema_version row: older than the setting
    conn.close()
    old = tmp_path / "old.db"
    old.write_bytes(db.read_bytes())
    Store.check_backup(old)
    store = Store(db)  # the update: its safety copy first
    safety = [p for p in store.backup_dir().glob("*.db") if "before-update" in p.name]
    assert safety
    Store.check_backup(safety[0])


def test_a_database_without_ap_coders_tables_is_still_refused(tmp_path):
    other = tmp_path / "other.db"
    conn = sqlite3.connect(other)
    conn.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.close()
    with pytest.raises(ValueError, match="not an AP Coder database"):
        Store.check_backup(other)


# --- B-165: Excel's trailing blank rows ---------------------------------------------------------------------------


def test_a_gl_csv_with_excels_trailing_blank_rows_loads(tmp_path):
    path = tmp_path / "gl.csv"
    path.write_text("GL Code,Description\r\n6000,Office\r\n6100,Travel\r\n,\r\n,\r\n", encoding="utf-8")
    table = load_table(path, "GL account", "gl_code")
    assert [r["gl_code"] for r in table.rows] == ["6000", "6100"]
    path.write_text("GL Code,Description\r\n6000,Office\r\n,Travel\r\n", encoding="utf-8")
    with pytest.raises(ValueError, match="row 2 has an empty 'gl_code'"):  # a code really missing is still refused
        load_table(path, "GL account", "gl_code")


# --- B-166: a place abroad --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "place", ["London, UK", "Paris, France", "Berlin, Germany", "Shenzhen, China", "Dublin, Ireland",
              "Manchester, United Kingdom", "Mexico City, Mexico"],
)  # fmt: skip
def test_a_place_abroad_is_outside_canada(place):
    assert province_code(place) == OUTSIDE_CANADA


def test_canadian_places_named_like_places_abroad_stay_canadian():
    assert province_code("London, ON") == "ON" and province_code("Paris, Ontario") == "ON"
