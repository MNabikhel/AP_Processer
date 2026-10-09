"""JD Edwards E1 export: F0411Z1 / F0911Z1 rows, formats, checks, settings and the Exports page flow."""

import csv
import datetime as dt
import io
import json
import sys
import zipfile

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from ap_coder import jde
from ap_coder.memory import vendor_key
from ap_coder.schema import InvoiceCoding
from ap_coder.store import Store
from ap_coder.tax import RECOVERABLE, TaxRateTable, TaxSetup, TaxTreatment, build_gl_distribution, load_tax_mapping

from .conftest import DATA, ROOT, SAMPLES

ON, QC, BC, AB = (
    "northwind_ON_HST_NW-2026-0912",
    "laurentides_QC_TPS_TVQ_SIL-4471",
    "pacific_BC_GST_PST_PO-77120",
    "chinook_AB_GST_CCO-26-10418",
)
USD, CREDIT, SK = "cascade_US_SalesTax_INV-30981", "northwind_ON_HST_CN-2026-0047", "prairie_SK_GST_PST_PNS-104882"


def _setup(**treatments):
    mapping = load_tax_mapping(DATA / "tax_gl_mapping.csv")
    mapping.update(treatments)
    return TaxSetup(TaxRateTable.load(DATA / "canada_tax_rates.csv"), mapping)


def _invoice(stem, inv_id=1, setup=None, **changes):
    doc = {**json.loads((SAMPLES / "ground_truth" / f"{stem}.json").read_text()), **changes}
    doc["gl_distribution"] = build_gl_distribution(InvoiceCoding.model_validate(doc), setup or _setup())
    return {"id": inv_id, "final_output": doc}


def AN8(final):  # noqa: N802 - a lookup that knows every supplier
    return "10023"


def _csv(data):
    return list(csv.DictReader(io.StringIO(data.decode("utf-8"))))


def _files(invoices, settings=None, **kw):
    files = jde.export_batch(invoices, settings or jde.JdeSettings(), kw.pop("lookup", AN8), batch=7, **kw)
    return {name: _csv(data) for name, data in files.items()}


# --- Formats ------------------------------------------------------------------------------------------------


def test_julian_dates():
    assert jde.julian(dt.date(2026, 10, 9)) == 126282
    assert jde.julian("2026-01-01") == 126001
    assert jde.julian(dt.date(2024, 2, 29)) == 124060  # leap day
    assert jde.julian(dt.date(2024, 12, 31)) == 124366  # leap year: 366 days
    assert jde.julian(dt.date(2023, 12, 31)) == 123365
    assert jde.julian(dt.date(1999, 12, 31)) == 99365  # century digit 0 (099365)
    assert jde.julian(dt.date(2000, 1, 1)) == 100001  # century digit 1
    assert jde.julian(dt.datetime(2026, 10, 9, 23, 59)) == 126282
    with pytest.raises(ValueError):
        jde.julian(dt.date(1899, 12, 31))


def test_amounts_and_rounding():
    assert jde.jde_amount(5.50) == 550
    assert jde.jde_amount(5.5, implied=False) == "5.50"
    assert jde.jde_amount(2.675) == 268  # half away from zero, on the decimal value (not binary float)
    assert jde.jde_amount(-2.675) == -268
    assert jde.jde_amount(0.005) == 1
    assert jde.jde_amount(1234567.891) == 123456789
    assert jde.jde_amount(-2316.5, implied=False) == "-2316.50"
    assert jde.jde_amount(1234.5, decimals=0) == 1235  # e.g. JPY: no decimals
    assert jde.jde_amount("18017.85") == 1801785


# --- One invoice per province ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "stem, area, code, gross, distribution",
    [
        (ON, "ON-HST", "V", 1801785, 1594500),  # HST recoverable: E1 posts it
        (QC, "QC-GSTQST", "V", 275940, 240000),  # GST + QST recoverable
        (BC, "BC-GSTPST", "C", 305312, 291682),  # GST recoverable, the PST stays in the expense lines
        (AB, "AB-GST", "V", 183330, 174600),  # GST only
    ],
)
def test_a_full_invoice_per_province(stem, area, code, gross, distribution):
    inv = _invoice(stem)
    assert jde.validate([inv], jde.JdeSettings(), AN8) == {}
    files = _files([inv])
    (head,) = files[jde.HEADER_FILE]
    final = inv["final_output"]
    assert (head["VLTXA1"], head["VLEXR1"], head["VLAG"]) == (area, code, str(gross))
    assert head["VLATXA"] == head["VLSTAM"] == ""  # gross mode: never both
    assert (head["VLEDUS"], head["VLEDBT"], head["VLEDTN"], head["VLEDLN"]) == ("APCODER", "APC7", "APC1", "1")
    assert (head["VLEDSP"], head["VLEDTC"], head["VLEDTR"], head["VLDCT"], head["VLCO"]) == (
        "0",
        "A",
        "V",
        "PV",
        "00001",
    )
    assert head["VLVINV"] == final["invoice_number"] and head["VLAN8"] == "10023"
    assert head["VLDIVJ"] == head["VLDGJ"] == head["VLDSVJ"] == str(jde.julian(final["invoice_date"]))
    assert (head["VLCRRM"], head["VLCRCD"]) == ("D", "CAD")
    lines = files[jde.DIST_FILE]
    assert len(lines) == len(final["line_items"])
    assert sum(int(r["VNAA"]) for r in lines) == distribution
    assert [r["VNEDLN"] for r in lines] == [str(n) for n in range(1, len(lines) + 1)]
    first = final["line_items"][0]
    assert lines[0]["VNANI"] == f"{first['predicted_cost_center']}.{first['predicted_gl_code']}"
    assert {r["VNAM"] for r in lines} == {"2"} and {r["VNLT"] for r in lines} == {"AA"}
    assert {r["VNEDTN"] for r in lines} == {"APC1"} and {r["VNDCT"] for r in lines} == {"PV"}


def test_tax_amount_mode_writes_taxable_and_tax_instead_of_gross():
    settings = jde.JdeSettings(amount_mode=jde.AMOUNT_TAX)
    (bc,), (qc,) = _files([_invoice(BC)], settings)[jde.HEADER_FILE], _files([_invoice(QC)], settings)[jde.HEADER_FILE]
    assert bc["VLAG"] == "" and (bc["VLATXA"], bc["VLATXN"], bc["VLSTAM"]) == ("272600", "0", "32712")  # GST + PST
    assert (qc["VLATXA"], qc["VLATXN"], qc["VLSTAM"]) == ("240000", "0", "35940")


def test_decimal_point_mode():
    (head,) = _files([_invoice(ON)], jde.JdeSettings(decimals=jde.POINT))[jde.HEADER_FILE]
    assert head["VLAG"] == "18017.85"


def test_self_assessed_pst_and_exempt_codes():
    doc = json.loads((SAMPLES / "ground_truth" / f"{SK}.json").read_text())
    no_pst = [t for t in doc["tax_lines"] if t["tax_type"] != "PST"]
    inv = _invoice(SK, tax_lines=no_pst, tax_total=504.75, grand_total=10599.75)
    (head,) = _files([inv])[jde.HEADER_FILE]
    assert head["VLEXR1"] == "B" and head["VLTXA1"] == "SK-GSTPST"
    assert jde.validate([inv], jde.JdeSettings(), AN8) == {}  # distribution = gross - GST
    exempt = _invoice(AB, tax_lines=[], tax_total=0, grand_total=1746.0)
    (head,) = _files([exempt])[jde.HEADER_FILE]
    assert head["VLEXR1"] == "E" and jde.validate([exempt], jde.JdeSettings(), AN8) == {}


def test_a_usd_invoice_in_foreign_mode():
    inv = _invoice(USD)
    assert jde.validate([inv], jde.JdeSettings(), AN8) == {}
    files = _files([inv], fx_rates={"USD": 1.37})
    (head,) = files[jde.HEADER_FILE]
    assert (head["VLCRRM"], head["VLCRCD"], head["VLACR"], head["VLAG"], head["VLCRR"]) == (
        "F", "USD", "757001", "", "1.37",
    )  # fmt: skip
    assert (head["VLTXA1"], head["VLEXR1"]) == ("", "")  # outside Canada: no tax handling
    lines = files[jde.DIST_FILE]
    assert {r["VNAA"] for r in lines} == {""} and sum(int(r["VNACR"]) for r in lines) == 757001  # US tax in lines
    assert {r["VNCRCD"] for r in lines} == {"USD"}
    problems = jde.validate([inv], jde.JdeSettings(currencies=["CAD"]), AN8)
    assert "currency USD is not set up" in problems[1][0]


def test_a_po_matched_invoice():
    settings = jde.JdeSettings(po_matched=True, use_default_rule=False)  # no account needed: matched in E1
    inv = _invoice(ON)
    assert jde.validate([inv], settings, AN8) == {}
    raw = jde.export_batch([inv, _invoice(AB, 2)], settings, AN8, batch=7)
    files = {name: _csv(data) for name, data in raw.items()}
    assert raw[jde.HEADER_FILE].decode().splitlines()[0].split(",") == [*jde.HEADER_COLUMNS, "VLATFLG", "VLPSTE"]
    on, ab = files[jde.HEADER_FILE]
    assert (on["VLPO"], on["VLPDCT"], on["VLPKCO"], on["VLAG"]) == ("PO-88213", "OP", "00001", "1801785")
    assert ab["VLPO"] == ""
    assert "APC1" not in {r["VNEDTN"] for r in files[jde.DIST_FILE]}  # the PO invoice has no G/L lines
    matches = files[jde.MATCH_FILE]
    assert [m["LNID"] for m in matches] == ["1", "2", "3", "4", "5"]
    assert (matches[0]["DOCO"], matches[0]["UORG"], matches[0]["PRRC"], matches[0]["AEXP"]) == (
        "PO-88213", "3", "1450", "435000",
    )  # fmt: skip
    assert list(matches[0]) == jde.MATCH_COLUMNS
    # The AB invoice (no PO) still needs its G/L lines, so without a mapping it is not exportable.
    assert "has no JDE account" in jde.validate([_invoice(AB, 2)], settings, AN8)[2][0]


def test_a_credit_note_has_negative_amounts():
    inv = _invoice(CREDIT, 3)
    settings = jde.JdeSettings(credit_document_type="PD")
    assert jde.validate([inv], settings, AN8) == {}
    files = _files([inv], settings)
    (head,) = files[jde.HEADER_FILE]
    assert head["VLAG"] == "-231650" and head["VLDCT"] == "PD"
    assert head["VLDDJ"] == head["VLDIVJ"]  # a credit is not "due": the invoice date
    lines = files[jde.DIST_FILE]
    assert [r["VNAA"] for r in lines] == ["-145000", "-60000"] and {r["VNDCT"] for r in lines} == {"PD"}
    # A credit note does not duplicate an invoice with the same number.
    invoice = _invoice(ON, 4, invoice_number="CN-2026-0047")
    assert jde.duplicate_problems([inv, invoice], settings, AN8) == {}


def test_match_header_numbering_gives_one_pay_item_per_line():
    settings = jde.JdeSettings(line_numbering=jde.MATCH_HEADER, amount_mode=jde.AMOUNT_TAX)
    files = _files([_invoice(ON)], settings)
    heads, lines = files[jde.HEADER_FILE], files[jde.DIST_FILE]
    assert [h["VLEDLN"] for h in heads] == [r["VNEDLN"] for r in lines] == ["1", "2", "3", "4", "5"]
    gross = sum(int(h["VLATXA"]) + int(h["VLATXN"]) + int(h["VLSTAM"]) for h in heads)
    assert gross == 1801785 and sum(int(h["VLSTAM"]) for h in heads) == 207285
    for h, r in zip(heads, lines, strict=True):  # each pay item = its line + its share of the HST (code V)
        assert int(h["VLATXA"]) + int(h["VLATXN"]) == int(r["VNAA"])
    gross_mode = _files([_invoice(ON)], jde.JdeSettings(line_numbering=jde.MATCH_HEADER))[jde.HEADER_FILE]
    assert sum(int(h["VLAG"]) for h in gross_mode) == 1801785


def test_match_header_pay_items_carry_their_own_pst():
    """Manitoba RST is charged on the furniture and delivery, not on the consulting line (line 3). Each pay item's
    tax is its share of the GST plus the PST on that line, never a negative non-taxable amount."""
    inv = _invoice("redriver_MB_GST_RST_RRO-55821")
    settings = jde.JdeSettings(line_numbering=jde.MATCH_HEADER, amount_mode=jde.AMOUNT_TAX)
    assert jde.validate([inv], settings, AN8) == {}
    files = _files([inv], settings)
    heads, lines = files[jde.HEADER_FILE], files[jde.DIST_FILE]
    taxes = [(int(h["VLATXA"]), int(h["VLATXN"]), int(h["VLSTAM"])) for h in heads]
    # GST 5% on each line; RST 7% on lines 1, 2 and 4 (the PST in the expense line under code C)
    assert taxes == [(920000, 0, 46000 + 64400), (396000, 0, 19800 + 27720), (120000, 0, 6000), (28500, 0, 1425 + 1995)]
    assert sum(sum(t) for t in taxes) == 1631840 and sum(t[2] for t in taxes) == 167340  # gross and GST + RST
    for (atxa, atxn, stam), line in zip(taxes, lines, strict=True):  # pay item = its G/L line + its GST
        assert atxa + atxn + stam == int(line["VNAA"]) + atxa // 20


def test_account_columns_mode_and_mapping():
    settings = jde.JdeSettings(
        account_mode=jde.ACCOUNT_COLUMNS,
        gl_map=[jde.GlMap("6010", "", "100", "6010", "01"), jde.GlMap("1500", "CC400", "200", "1500")],
    )
    lines = _files([_invoice(ON)], settings)[jde.DIST_FILE]
    assert (lines[0]["VNMCU"], lines[0]["VNOBJ"], lines[0]["VNSUB"], lines[0]["VNANI"]) == ("100", "6010", "01", "")
    assert (lines[1]["VNMCU"], lines[1]["VNOBJ"]) == ("200", "1500")
    assert (lines[2]["VNMCU"], lines[2]["VNOBJ"]) == ("CC400", "6020")  # the default rule
    assert jde.ani(jde.account_for("6010", "", settings)) == "100.6010.01"
    no_cc = jde.JdeSettings(bu_from_cost_center=False, default_bu="1")
    assert jde.ani(jde.account_for("6010", "CC400", no_cc)) == "1.6010"


def test_csv_columns_in_order_and_formula_safe():
    inv = _invoice(ON)
    inv["final_output"]["gl_distribution"][0]["description"] = "=HYPERLINK(evil)"
    raw = jde.export_batch([inv], jde.JdeSettings(), AN8, batch=1)
    assert set(raw) == {jde.HEADER_FILE, jde.DIST_FILE}  # no match file outside PO-matched mode
    assert raw[jde.HEADER_FILE].decode().splitlines()[0].split(",") == jde.HEADER_COLUMNS
    assert raw[jde.DIST_FILE].decode().splitlines()[0].split(",") == jde.DIST_COLUMNS
    assert not raw[jde.HEADER_FILE].startswith(b"\xef\xbb\xbf")  # no BOM for database loaders
    assert _csv(raw[jde.DIST_FILE])[0]["VNEXA"] == "'=HYPERLINK(evil)"


# --- Checks -------------------------------------------------------------------------------------------------------


def test_validation_failures():
    settings = jde.JdeSettings()
    assert "no JDE supplier number" in jde.validate([_invoice(ON)], settings, lambda f: "")[1][0]
    assert "not a JDE address number" in jde.validate([_invoice(ON)], settings, lambda f: "V10023")[1][0]
    assert "longer than 25" in jde.validate([_invoice(ON, invoice_number="X" * 26)], settings, AN8)[1][0]
    assert "longer than 8" in jde.validate([_invoice(ON, po_number="PO-882139")], settings, AN8)[1][0]
    unmapped = jde.validate([_invoice(ON)], jde.JdeSettings(use_default_rule=False), AN8)[1]
    assert len(unmapped) == 5 and "GL 6010 / CC400 has no JDE account" in unmapped[0]
    no_bu = jde.validate([_invoice(ON)], jde.JdeSettings(bu_from_cost_center=False), AN8)[1]
    assert "has no JDE account" in no_bu[0]  # no cost center rule and no default BU
    # PST set up as recoverable in AP Coder, but BC's code C expenses it: E1 would not post it.
    pst_recoverable = _invoice(BC, setup=_setup(PST=TaxTreatment("PST", RECOVERABLE, "2330")))
    (msg,) = jde.validate([pst_recoverable], settings, AN8)[1]
    assert msg.startswith("unbalanced: the distribution is 2,726.00 but gross 3,053.12 minus recoverable tax 136.30")
    broken = _invoice(ON)
    broken["final_output"]["gl_distribution"][0]["amount"] += 1
    assert "adds up to" in jde.validate([broken], settings, AN8)[1][0]
    nowhere = _invoice(ON, supplier_province="", ship_to_province="")
    assert "province of supply unknown" in jde.validate([nowhere], settings, AN8)[1][0]
    assert jde.settings_problems(jde.JdeSettings(company="ABC", user="", batch_prefix="X" * 10)) and not (
        jde.settings_problems(jde.JdeSettings())
    )


def test_duplicates_in_the_batch_and_against_earlier_exports():
    settings = jde.JdeSettings()
    a, b = _invoice(ON, 1), _invoice(ON, 2, invoice_number="#NW-2026-0912")  # the same number, written differently
    assert jde.validate([a, b], settings, AN8) == {2: ["same supplier and invoice number as AP Coder #1 in this batch"]}
    prior = {
        "exported": [{"id": 9, "export_batch": 3, "vendor_name": "Northwind IT Solutions Inc.",
                      "invoice_number": "NW-2026-0912", "grand_total": 18017.85}],
        "register": [{"vendor_key": vendor_key("Other name Ltd"), "vendor_name": "Other name Ltd",
                      "invoice_number": "CCO-26-10418", "invoice_date": "2026-09-30", "total": 1833.3}],
    }  # fmt: skip
    problems = jde.validate([a, _invoice(AB, 5)], settings, AN8, prior)
    assert problems[1] == ["already exported: AP Coder #9 in batch 3"]
    assert problems[5] == ["already in the ERP (invoice CCO-26-10418, dated 2026-09-30)"]  # same AN8, another name
    assert jde.validate([_invoice(AB, 5)], settings, {}, prior)[5] == [
        "no JDE supplier number (AN8): set the vendor's ERP ID in the vendor master, or in Settings → JD Edwards"
    ]  # without an AN8 the register row (another vendor name) is not the same supplier


def test_an8_from_the_vendor_master_and_the_overrides():
    final = _invoice(ON)["final_output"]
    ids = {"northwind it solutions": "4410"}
    assert jde.resolve_an8(final, ids, jde.JdeSettings()) == "4410"
    by_gst = {"gst:123456789rt0001": "4411"}
    assert jde.resolve_an8(final, by_gst, jde.JdeSettings()) == "4411"
    override = jde.JdeSettings(an8_overrides={vendor_key(final["vendor_name"]): "777"})
    assert jde.resolve_an8(final, ids, override) == "777"


# --- Settings and files -------------------------------------------------------------------------------------------


def test_settings_round_trip(tmp_path):
    settings = jde.JdeSettings(
        company="42", user="JDOE", batch_prefix="AP", amount_mode=jde.AMOUNT_TAX, decimals=jde.POINT,
        account_mode=jde.ACCOUNT_COLUMNS, line_numbering=jde.MATCH_HEADER, po_matched=True,
        po_extra_columns={"VLATFLG": "1"}, gl_map=[jde.GlMap("6010", "CC400", "100", "6010", "01", "X", "A")],
        an8_overrides={"northwind it solutions": "4410"}, currencies=["CAD"],
    )  # fmt: skip
    settings.tax_map["ON"] = jde.TaxArea("ONT", "V")
    assert jde.JdeSettings.from_json(settings.to_json()) == settings
    assert settings.company_code == "00042"
    store = Store(tmp_path / "ap.db")
    assert jde.load(store) == jde.JdeSettings()  # nothing saved yet: the defaults
    assert jde.save(store, settings, actor="jane") and not jde.save(store, settings, actor="jane")
    assert jde.load(store) == settings
    assert store.events(actions=["settings_changed"])[0]["detail"] == {"keys": [jde.SETTING_KEY]}
    assert jde.JdeSettings.from_json("not json") == jde.JdeSettings()
    assert jde.JdeSettings.from_json('{"amount_mode": "both", "company": "7"}').amount_mode == jde.AMOUNT_GROSS


def test_default_tax_map():
    m = jde.default_tax_map()
    assert (m["ON"].tax_area, m["ON"].code, m["NS"].code) == ("ON-HST", "V", "V")
    assert (m["QC"].tax_area, m["QC"].code) == ("QC-GSTQST", "V")
    assert {m[p].code for p in ("BC", "SK", "MB")} == {"C"}
    assert {m[p].code for p in ("AB", "NT", "NU", "YT")} == {"V"}


def test_gl_map_import_from_a_spreadsheet():
    rows = jde.gl_map_from_records([
        {"GL Code": "6010", "Cost Center": "CC400", "Business Unit": "100", "Object": "6010", "Subsidiary": "01"},
        {"GL Code": "", "Cost Center": "", "Business Unit": "1", "Object": "", "Subsidiary": ""},
        {"GL Code": "6800", "Cost Center": None, "Business Unit": "nan", "Object": "6810", "Subsidiary": ""},
    ])  # fmt: skip
    assert rows == [jde.GlMap("6010", "CC400", "100", "6010", "01"), jde.GlMap("6800", "", "", "6810", "")]


def test_zip_has_the_files_and_a_readme():
    data, name, mime = jde.build_zip(
        [_invoice(ON)], jde.JdeSettings(), AN8, batch=12, left_out={2: ["no JDE supplier number (AN8)"]}
    )
    assert (name, mime) == ("ap_coder_jde_batch_12.zip", "application/zip")
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert sorted(zf.namelist()) == ["F0411Z1.csv", "F0911Z1.csv", "README.txt"]
        text = zf.read("README.txt").decode()
    assert "R04110ZA (or R04110Z) in PROOF mode" in text and "R04110ZA in FINAL mode" in text and "APC12" in text
    assert "F0911Z1.csv  5 row(s)" in text and "AP Coder #2: no JDE supplier number" in text


# --- Dashboard ------------------------------------------------------------------------------------------------------

TIMEOUT = 90


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "private" / "ap_coder.db"
    monkeypatch.setenv("AP_DB_PATH", str(path))
    for name in [m for m in sys.modules if m.startswith(("ap_coder.webapp", "ap_coder.dashboard"))]:
        monkeypatch.delitem(sys.modules, name)
    st.cache_resource.clear()
    st.cache_data.clear()
    return path


def _page(module, function):
    return AppTest.from_string(
        f"from ap_coder.webapp.{module} import {function}\n{function}()", default_timeout=TIMEOUT
    )


def _ok(at):
    assert not at.exception, [e.value for e in at.exception]
    return at


def test_exports_page_jde_flow(db):
    from ap_coder.demo import load_demo

    store = Store(db)
    load_demo(store)
    ready = {i["vendor_name"]: i["id"] for i in store.unexported_approved()}
    northwind = next(i for n, i in ready.items() if n.startswith("Northwind"))
    chinook = next(i for n, i in ready.items() if n.startswith("Chinook"))
    at = _ok(_page("exports", "page_exports").run())
    _ok(at.radio(key="export_format").set_value(jde.FORMAT).run())
    # The demo vendor master's ERP IDs (V10023, …) are not JDE address numbers: nothing can go out yet.
    assert at.button(key="export_create").proto.disabled
    assert any("cannot go to JD Edwards yet" in w.value for w in at.warning)
    settings = jde.load(store)
    settings.an8_overrides = {vendor_key(store.get_invoice(northwind)["vendor_name"]): "10023"}
    jde.save(store, settings)
    _ok(at.run())
    assert at.button(key="export_create").label == "Export 1 invoice"
    _ok(at.button(key="export_create").click().run())
    (batch,) = store.export_batches()
    assert batch["format"] == jde.FORMAT and store.batch_invoice_ids(batch["id"]) == [northwind]
    assert [i["id"] for i in store.unexported_approved()] == [chinook]  # left out, still ready
    from ap_coder.webapp.exports import _batch_file

    data, name, mime = _batch_file(store, batch["id"], jde.FORMAT)
    assert name == f"ap_coder_jde_batch_{batch['id']}.zip" and mime == "application/zip"
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        (head,) = _csv(zf.read(jde.HEADER_FILE))
        assert (head["VLEDBT"], head["VLAN8"], head["VLEDTN"]) == (f"APC{batch['id']}", "10023", f"APC{northwind}")
        assert len(_csv(zf.read(jde.DIST_FILE))) == 5
    # Exported once: the same invoice cannot be exported again until the batch is undone.
    assert (
        "already exported"
        in jde.validate([store.get_invoice(northwind)], jde.load(store), store.vendor_ids(), jde.prior_records(store))[
            northwind
        ][0]
    )
    store.undo_export_batch(batch["id"], actor="jane")
    assert jde.validate([store.get_invoice(northwind)], jde.load(store), store.vendor_ids(),
                        jde.prior_records(store)) == {}  # fmt: skip
    at = _ok(_page("exports", "page_exports").run())  # the undone batch is listed, nothing breaks


def test_settings_page_jde_tab(db):
    store = Store(db)
    at = _ok(_page("settings", "page_settings").run())
    company = next(t for t in at.text_input if t.label == "Company (VLCO)")
    company.input("42")
    submit = next(b for b in at.button if b.proto.is_form_submitter and b.proto.form_id.endswith("jde_form"))
    _ok(submit.click().run())
    assert jde.load(store).company_code == "00042"
    _ok(at.button(key="jde_tax_save").click().run())
    assert jde.load(store).tax_map["ON"] == jde.TaxArea("ON-HST", "V")
    assert any("Confirm with your JDE team" in m.value for m in at.markdown)


def test_docs_exist():
    text = (ROOT / "docs" / "JDE_E1.md").read_text()
    for column in [*jde.HEADER_COLUMNS, *jde.DIST_COLUMNS]:
        assert column in text, column
    assert "R04110ZA" in text and "JP040000" in text and "Orchestrator" in text
