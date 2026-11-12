"""JD Edwards E1 export: F0411Z1 / F0911Z1 rows, formats, checks, settings and the Exports page flow."""

import csv
import datetime as dt
import io
import json
import re
import zipfile

import pytest

from ap_coder.coding.tax import (
    RECOVERABLE,
    TaxRateTable,
    TaxSetup,
    TaxTreatment,
    build_gl_distribution,
    load_tax_mapping,
    tax_shares,
)
from ap_coder.core.memory import vendor_key
from ap_coder.core.schema import InvoiceCoding
from ap_coder.core.store import Store
from ap_coder.erp import (
    jde,
    jde_accounts,
    jde_formats,
    jde_prototype,
    jde_remarks,
    jde_review,
    jde_settings,
    jde_voucher,
    jde_zfiles,
)
from tests.conftest import DATA, ROOT, SAMPLES, copy_database

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
    files = jde_zfiles.export_batch(
        invoices,
        settings or jde_settings.JdeSettings(amount_mode=jde_settings.AMOUNT_GROSS),
        kw.pop("lookup", AN8),
        batch=7,
        **kw,
    )
    return {name: _csv(data) for name, data in files.items()}


# --- Formats ------------------------------------------------------------------------------------------------


def test_julian_dates():
    assert jde_formats.julian(dt.date(2026, 10, 9)) == 126282
    assert jde_formats.julian("2026-01-01") == 126001
    assert jde_formats.julian(dt.date(2024, 2, 29)) == 124060  # leap day
    assert jde_formats.julian(dt.date(2024, 12, 31)) == 124366  # leap year: 366 days
    assert jde_formats.julian(dt.date(2023, 12, 31)) == 123365
    assert jde_formats.julian(dt.date(1999, 12, 31)) == 99365  # century digit 0 (099365)
    assert jde_formats.julian(dt.date(2000, 1, 1)) == 100001  # century digit 1
    assert jde_formats.julian(dt.datetime(2026, 10, 9, 23, 59)) == 126282
    with pytest.raises(ValueError):
        jde_formats.julian(dt.date(1899, 12, 31))


def test_amounts_and_rounding():
    assert jde_formats.jde_amount(5.50) == 550
    assert jde_formats.jde_amount(5.5, implied=False) == "5.50"
    assert jde_formats.jde_amount(2.675) == 268  # half away from zero, on the decimal value (not binary float)
    assert jde_formats.jde_amount(-2.675) == -268
    assert jde_formats.jde_amount(0.005) == 1
    assert jde_formats.jde_amount(1234567.891) == 123456789
    assert jde_formats.jde_amount(-2316.5, implied=False) == "-2316.50"
    assert jde_formats.jde_amount(1234.5, decimals=0) == 1235  # e.g. JPY: no decimals
    assert jde_formats.jde_amount("18017.85") == 1801785


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
    assert jde.validate([inv], jde_settings.JdeSettings(amount_mode=jde_settings.AMOUNT_GROSS), AN8) == {}
    files = _files([inv])
    (head,) = files[jde_zfiles.HEADER_FILE]
    final = inv["final_output"]
    assert (head["VLTXA1"], head["VLEXR1"], head["VLAG"]) == (area, code, str(gross))
    assert head["VLATXA"] == head["VLSTAM"] == ""  # gross mode: never both
    assert (head["VLEDUS"], head["VLEDBT"], head["VLEDTN"], head["VLEDLN"]) == (
        "APCODER",
        "APC7",
        f"APC1-{jde_zfiles.invoice_site(inv)}",
        "1",
    )  # (no batch code given: as before; D-182)
    assert (head["VLEDSP"], head["VLEDTC"], head["VLEDTR"], head["VLDCT"], head["VLCO"]) == (
        "0",
        "A",
        "V",
        "PV",
        "00001",
    )
    assert head["VLVINV"] == final["invoice_number"] and head["VLAN8"] == "10023"
    assert head["VLDIVJ"] == head["VLDGJ"] == head["VLDSVJ"] == str(jde_formats.julian(final["invoice_date"]))
    assert (head["VLCRRM"], head["VLCRCD"]) == ("D", "CAD")
    lines = files[jde_zfiles.DIST_FILE]
    assert len(lines) == len(final["line_items"])
    assert sum(int(r["VNAA"]) for r in lines) == distribution
    assert [r["VNEDLN"] for r in lines] == [str(n) for n in range(1, len(lines) + 1)]
    first = final["line_items"][0]
    assert lines[0]["VNANI"] == f"{first['predicted_cost_center']}.{first['predicted_gl_code']}"
    assert {r["VNAM"] for r in lines} == {"2"} and {r["VNLT"] for r in lines} == {"AA"}
    assert {r["VNEDTN"] for r in lines} == {head["VLEDTN"]} and {r["VNDCT"] for r in lines} == {"PV"}


def test_tax_amount_mode_writes_taxable_and_tax_instead_of_gross():
    settings = jde_settings.JdeSettings(amount_mode=jde_settings.AMOUNT_TAX)
    (bc,), (qc,) = (
        _files([_invoice(BC)], settings)[jde_zfiles.HEADER_FILE],
        _files([_invoice(QC)], settings)[jde_zfiles.HEADER_FILE],
    )
    assert bc["VLAG"] == "" and (bc["VLATXA"], bc["VLATXN"], bc["VLSTAM"]) == ("272600", "0", "32712")  # GST + PST
    assert (qc["VLATXA"], qc["VLATXN"], qc["VLSTAM"]) == ("240000", "0", "35940")


def test_decimal_point_mode():
    (head,) = _files(
        [_invoice(ON)], jde_settings.JdeSettings(amount_mode=jde_settings.AMOUNT_GROSS, decimals=jde_settings.POINT)
    )[jde_zfiles.HEADER_FILE]
    assert head["VLAG"] == "18017.85"


def test_self_assessed_pst_and_exempt_codes():
    doc = json.loads((SAMPLES / "ground_truth" / f"{SK}.json").read_text())
    no_pst = [t for t in doc["tax_lines"] if t["tax_type"] != "PST"]
    inv = _invoice(SK, tax_lines=no_pst, tax_total=504.75, grand_total=10599.75)
    (head,) = _files([inv])[jde_zfiles.HEADER_FILE]
    assert head["VLEXR1"] == "B" and head["VLTXA1"] == "SK-GSTPST"
    # distribution = gross - GST
    assert jde.validate([inv], jde_settings.JdeSettings(amount_mode=jde_settings.AMOUNT_GROSS), AN8) == {}
    exempt = _invoice(AB, tax_lines=[], tax_total=0, grand_total=1746.0)
    (head,) = _files([exempt])[jde_zfiles.HEADER_FILE]
    assert (
        head["VLEXR1"] == "E"
        and jde.validate([exempt], jde_settings.JdeSettings(amount_mode=jde_settings.AMOUNT_GROSS), AN8) == {}
    )


def test_a_usd_invoice_in_foreign_mode():
    inv = _invoice(USD)
    assert jde.validate([inv], jde_settings.JdeSettings(amount_mode=jde_settings.AMOUNT_GROSS), AN8) == {}
    files = _files([inv], fx_rates={"USD": 1.37})
    (head,) = files[jde_zfiles.HEADER_FILE]
    assert (head["VLCRRM"], head["VLCRCD"], head["VLACR"], head["VLAG"], head["VLCRR"]) == (
        "F", "USD", "757001", "", "1.37",
    )  # fmt: skip
    assert (head["VLTXA1"], head["VLEXR1"]) == ("", "")  # outside Canada: no tax handling
    lines = files[jde_zfiles.DIST_FILE]
    assert {r["VNAA"] for r in lines} == {""} and sum(int(r["VNACR"]) for r in lines) == 757001  # US tax in lines
    assert {r["VNCRCD"] for r in lines} == {"USD"}
    problems = jde.validate(
        [inv], jde_settings.JdeSettings(amount_mode=jde_settings.AMOUNT_GROSS, currencies=["CAD"]), AN8
    )
    assert "currency USD is not set up" in problems[1][0]


def test_a_po_matched_invoice():
    settings = jde_settings.JdeSettings(
        amount_mode=jde_settings.AMOUNT_GROSS, po_matched=True, use_default_rule=False
    )  # no account needed: matched in E1
    inv = _invoice(ON)
    assert jde.validate([inv], settings, AN8) == {}
    raw = jde_zfiles.export_batch([inv, _invoice(AB, 2)], settings, AN8, batch=7)
    files = {name: _csv(data) for name, data in raw.items()}
    assert raw[jde_zfiles.HEADER_FILE].decode().splitlines()[0].split(",") == [
        *jde_zfiles.HEADER_COLUMNS,
        "VLATFLG",
        "VLPSTE",
    ]
    on, ab = files[jde_zfiles.HEADER_FILE]
    assert (on["VLPO"], on["VLPDCT"], on["VLPKCO"], on["VLAG"]) == ("PO-88213", "OP", "00001", "1801785")
    assert ab["VLPO"] == ""
    assert on["VLEDTN"] not in {r["VNEDTN"] for r in files[jde_zfiles.DIST_FILE]}  # the PO invoice has no G/L lines
    matches = files[jde_zfiles.MATCH_FILE]
    assert [m["LNID"] for m in matches] == ["1", "2", "3", "4", "5"]
    assert (matches[0]["DOCO"], matches[0]["UORG"], matches[0]["PRRC"], matches[0]["AEXP"]) == (
        "PO-88213", "3", "1450", "435000",
    )  # fmt: skip
    assert list(matches[0]) == jde_zfiles.MATCH_COLUMNS
    # The AB invoice (no PO) still needs its G/L lines, so without a mapping it is not exportable.
    assert "has no JDE account" in jde.validate([_invoice(AB, 2)], settings, AN8)[2][0]


def test_a_credit_note_has_negative_amounts():
    inv = _invoice(CREDIT, 3)
    settings = jde_settings.JdeSettings(amount_mode=jde_settings.AMOUNT_GROSS, credit_document_type="PD")
    assert jde.validate([inv], settings, AN8) == {}
    files = _files([inv], settings)
    (head,) = files[jde_zfiles.HEADER_FILE]
    assert head["VLAG"] == "-231650" and head["VLDCT"] == "PD"
    assert head["VLDDJ"] == head["VLDIVJ"]  # a credit is not "due": the invoice date
    lines = files[jde_zfiles.DIST_FILE]
    assert [r["VNAA"] for r in lines] == ["-145000", "-60000"] and {r["VNDCT"] for r in lines} == {"PD"}
    # A credit note does not duplicate an invoice with the same number.
    invoice = _invoice(ON, 4, invoice_number="CN-2026-0047")
    assert jde.duplicate_problems([inv, invoice], settings, AN8) == {}


def test_match_header_numbering_gives_one_pay_item_per_line():
    settings = jde_settings.JdeSettings(line_numbering=jde_settings.MATCH_HEADER, amount_mode=jde_settings.AMOUNT_TAX)
    files = _files([_invoice(ON)], settings)
    heads, lines = files[jde_zfiles.HEADER_FILE], files[jde_zfiles.DIST_FILE]
    assert [h["VLEDLN"] for h in heads] == [r["VNEDLN"] for r in lines] == ["1", "2", "3", "4", "5"]
    gross = sum(int(h["VLATXA"]) + int(h["VLATXN"]) + int(h["VLSTAM"]) for h in heads)
    assert gross == 1801785 and sum(int(h["VLSTAM"]) for h in heads) == 207285
    for h, r in zip(heads, lines, strict=True):  # each pay item = its line + its share of the HST (code V)
        assert int(h["VLATXA"]) + int(h["VLATXN"]) == int(r["VNAA"])
    gross_mode = _files(
        [_invoice(ON)],
        jde_settings.JdeSettings(amount_mode=jde_settings.AMOUNT_GROSS, line_numbering=jde_settings.MATCH_HEADER),
    )[jde_zfiles.HEADER_FILE]
    assert sum(int(h["VLAG"]) for h in gross_mode) == 1801785


def test_match_header_pay_items_carry_their_own_pst():
    """Manitoba RST is charged on the furniture and delivery, not on the consulting line (line 3). Each pay item's
    tax is its share of the GST plus the PST on that line, never a negative non-taxable amount."""
    inv = _invoice("redriver_MB_GST_RST_RRO-55821")
    settings = jde_settings.JdeSettings(line_numbering=jde_settings.MATCH_HEADER, amount_mode=jde_settings.AMOUNT_TAX)
    assert jde.validate([inv], settings, AN8) == {}
    files = _files([inv], settings)
    heads, lines = files[jde_zfiles.HEADER_FILE], files[jde_zfiles.DIST_FILE]
    taxes = [(int(h["VLATXA"]), int(h["VLATXN"]), int(h["VLSTAM"])) for h in heads]
    # GST 5% on each line; RST 7% on lines 1, 2 and 4 (the PST in the expense line under code C)
    assert taxes == [(920000, 0, 46000 + 64400), (396000, 0, 19800 + 27720), (120000, 0, 6000), (28500, 0, 1425 + 1995)]
    assert sum(sum(t) for t in taxes) == 1631840 and sum(t[2] for t in taxes) == 167340  # gross and GST + RST
    for (atxa, atxn, stam), line in zip(taxes, lines, strict=True):  # pay item = its G/L line + its GST
        assert atxa + atxn + stam == int(line["VNAA"]) + atxa // 20


def test_match_header_gives_no_tax_to_an_exempt_line():
    """The HST is split over the lines that carry it: an exempt line's pay item has no tax and no taxable amount
    (it was weighted by net amount, so the exempt line got HST and VLATXN could go negative)."""
    doc = json.loads((SAMPLES / "ground_truth" / f"{ON}.json").read_text())
    doc["line_items"][-1]["taxes_applied"] = []
    exempt = doc["line_items"][-1]["amount"]
    taxable = round(doc["subtotal"] - exempt, 2)
    hst = round(taxable * 0.13, 2)
    doc["tax_lines"][0].update(taxable_amount=taxable, tax_amount=hst)
    doc.update(tax_total=hst, grand_total=round(doc["subtotal"] + hst, 2))
    inv = _invoice(ON, **doc)
    settings = jde_settings.JdeSettings(line_numbering=jde_settings.MATCH_HEADER, amount_mode=jde_settings.AMOUNT_TAX)
    assert jde.validate([inv], settings, AN8) == {}
    heads = _files([inv], settings)[jde_zfiles.HEADER_FILE]
    taxes = [(int(h["VLATXA"]), int(h["VLATXN"]), int(h["VLSTAM"])) for h in heads]
    assert taxes[-1] == (0, round(exempt * 100), 0)
    assert all(atxn == 0 for _, atxn, _ in taxes[:-1])  # the taxable lines: all taxable
    assert sum(t[0] for t in taxes) == round(taxable * 100) and sum(t[2] for t in taxes) == round(hst * 100)
    assert sum(sum(t) for t in taxes) == round(doc["grand_total"] * 100)


def test_match_header_pay_items_take_the_tax_shares_the_lines_have():
    """B-1889: with a pay item per line and the tax not on G/L lines, each pay item's share of the recoverable tax
    was truncated and the cent left over put on the largest, while the lines' shares (``tax.tax_shares``,
    ``money.split``) round each half up: two lines of 10.10 with GST 1.01 gave pay items 0.51 / 0.50 against the
    lines' 0.50 / 0.51. One rule now, ``money.split``'s, for the tax and the taxable amount, in whole yen too."""
    doc = json.loads((SAMPLES / "ground_truth" / f"{AB}.json").read_text())
    lines = doc["line_items"][:2]
    for li in lines:
        li.update(amount=10.10, quantity=1, unit_price=10.10)
    inv = _invoice(AB, line_items=lines, subtotal=20.20, tax_total=1.01, grand_total=21.21,
                   tax_lines=[{**doc["tax_lines"][0], "taxable_amount": 20.20, "tax_amount": 1.01}])  # fmt: skip
    settings = jde_settings.JdeSettings(line_numbering=jde_settings.MATCH_HEADER, amount_mode=jde_settings.AMOUNT_TAX)
    assert jde.validate([inv], settings, AN8) == {}
    heads = _files([inv], settings)[jde_zfiles.HEADER_FILE]
    coding = InvoiceCoding.model_validate({k: v for k, v in inv["final_output"].items() if k != "gl_distribution"})
    shares = [round(share * 100) for _, share in tax_shares(coding.line_items, coding.tax_lines[0])]
    assert [int(h["VLSTAM"]) for h in heads] == shares == [50, 51]
    assert sum(int(h["VLATXA"]) + int(h["VLSTAM"]) for h in heads) == 2121
    assert jde_formats._split(300, [1, 1], unit=100) == [100, 200]  # 3 yen: 1.5 each, half up, the rest on the first
    assert jde_formats._split(-101, [1010, 1010]) == [-50, -51]  # a credit as the invoice it reverses
    # The samples that changed (GST and QST each split over the lines; D-182 lists them): the lines' shares now
    for stem, was, now in (("laurentides_QC_TPS_TVQ_SIL-4471", [17971, 5091, 12878], [17969, 5092, 12879]),
                           ("montroyal_QC_TPS_TVQ_ACMR-2026-1187", [34143, 31447, 52412, 19767, 71133, 38186, 32945,
                            1018], [34143, 31448, 52413, 19767, 71131, 38186, 32945, 1018])):  # fmt: skip
        inv = _invoice(stem)
        coding = InvoiceCoding.model_validate({k: v for k, v in inv["final_output"].items() if k != "gl_distribution"})
        per_line = [0] * len(coding.line_items)
        for tax_line in coding.tax_lines:
            for i, share in tax_shares(coding.line_items, tax_line):
                per_line[i] += round(share * 100)
        heads = _files([inv], settings)[jde_zfiles.HEADER_FILE]
        assert [int(h["VLSTAM"]) for h in heads] == per_line == now and sum(was) == sum(now), stem


def test_match_header_an_exempt_or_zero_rated_line_is_its_own_pay_item_with_the_exempt_code():
    """D-64: a line marked exempt or zero-rated (no tax put on it) says so to E1, on its pay item and, with the
    distribution's tax columns, on its G/L line; the taxed lines keep the invoice's code. One pay item: the
    invoice's."""
    doc = json.loads((SAMPLES / "ground_truth" / f"{ON}.json").read_text())
    doc["line_items"][-1].update(taxes_applied=[], tax_status="exempt")
    doc["line_items"][-2].update(taxes_applied=[], tax_status="zero_rated")
    free = doc["line_items"][-1]["amount"] + doc["line_items"][-2]["amount"]
    taxable = round(doc["subtotal"] - free, 2)
    hst = round(taxable * 0.13, 2)
    doc["tax_lines"][0].update(taxable_amount=taxable, tax_amount=hst)
    doc.update(tax_total=hst, grand_total=round(doc["subtotal"] + hst, 2))
    inv = _invoice(ON, **doc)
    settings = jde_settings.JdeSettings(
        line_numbering=jde_settings.MATCH_HEADER, amount_mode=jde_settings.AMOUNT_TAX, dist_tax_columns=True
    )
    assert jde.validate([inv], settings, AN8) == {}
    files = _files([inv], settings)
    heads, lines = files[jde_zfiles.HEADER_FILE], files[jde_zfiles.DIST_FILE]
    assert [h["VLEXR1"] for h in heads] == ["V", "V", "V", "E", "E"]
    assert [x["VNEXR1"] for x in lines] == ["V", "V", "V", "E", "E"]
    assert all(int(h["VLSTAM"]) == 0 and int(h["VLATXA"]) == 0 for h in heads[-2:])
    assert sum(int(h["VLATXA"]) + int(h["VLATXN"]) + int(h["VLSTAM"]) for h in heads) == round(doc["grand_total"] * 100)
    one = _files([inv], jde_settings.JdeSettings(amount_mode=jde_settings.AMOUNT_TAX, dist_tax_columns=True))
    assert [h["VLEXR1"] for h in one[jde_zfiles.HEADER_FILE]] == ["V"]
    # A configured exempt code is used, not "E"
    custom = _files(
        [inv],
        jde_settings.JdeSettings(
            amount_mode=jde_settings.AMOUNT_GROSS, line_numbering=jde_settings.MATCH_HEADER, exempt_code="X"
        ),
    )[jde_zfiles.HEADER_FILE]
    assert [h["VLEXR1"] for h in custom][-2:] == ["X", "X"]


def _yen_invoice(old_distribution=False):
    """Three 10-yen lines with BC PST (2 yen), one 1,000-yen line without; GST 52 yen on everything. An old
    distribution was spread in cents (10.66 + 10.67 + 10.67): rounded line by line to the yen, that is 33, not 32."""
    doc = json.loads((SAMPLES / "ground_truth" / f"{BC}.json").read_text())
    item = doc["line_items"][0]
    doc["line_items"] = [
        {**item, "line_number": n, "quantity": 1, "unit_price": a, "amount": a, "taxes_applied": t}
        for n, (a, t) in enumerate([(10.0, ["GST", "PST"])] * 3 + [(1000.0, ["GST"])], 1)
    ]
    doc["tax_lines"] = [
        {"tax_type": "GST", "province": "", "rate": 0.05, "taxable_amount": 1030.0, "tax_amount": 52.0},
        {"tax_type": "PST", "province": "BC", "rate": 0.07, "taxable_amount": 30.0, "tax_amount": 2.0},
    ]
    doc.update(subtotal=1030.0, tax_total=54.0, grand_total=1084.0, currency="CAD" if old_distribution else "JPY")
    inv = _invoice(BC, **doc)
    inv["final_output"]["currency"] = "JPY"
    return inv


@pytest.mark.parametrize("old_distribution", [False, True])
@pytest.mark.parametrize("numbering", [jde_settings.SEQUENTIAL, jde_settings.MATCH_HEADER])
def test_a_yen_voucher_balances_in_whole_yen(old_distribution, numbering):
    inv = _yen_invoice(old_distribution)
    if old_distribution:
        assert any(e["amount"] != round(e["amount"]) for e in inv["final_output"]["gl_distribution"])
    gross = jde_settings.JdeSettings(
        currencies=["CAD", "JPY"], line_numbering=numbering, amount_mode=jde_settings.AMOUNT_GROSS
    )
    # PST on three lines only: from the gross alone E1 would tax every line (B-921)
    assert "works out 12% tax" in jde.validate([inv], gross, AN8)[1][0]
    for mode in (jde_settings.AMOUNT_TAX,):
        settings = jde_settings.JdeSettings(currencies=["CAD", "JPY"], line_numbering=numbering, amount_mode=mode)
        assert jde.validate([inv], settings, AN8) == {}
        files = _files([inv], settings)
        heads, lines = files[jde_zfiles.HEADER_FILE], files[jde_zfiles.DIST_FILE]
        if mode == jde_settings.AMOUNT_GROSS:
            gross = sum(int(h["VLACR"]) for h in heads)
        else:
            gross = sum(int(h["VLCTXA"]) + int(h["VLCTXN"]) + int(h["VLCTAM"]) for h in heads)
            assert sum(int(h["VLCTAM"]) for h in heads) == 54
        assert gross == 1084  # whole yen, implied decimals: none
        assert sum(int(r["VNACR"]) for r in lines) + 52 == 1084  # the G/L lines + the recoverable GST


def test_exchange_rate_keeps_every_decimal():
    """VLCRR carries 7 decimals in E1: the rate typed in Settings must not be cut to 6 significant digits."""
    for rate, text in ((1.3654321, "1.3654321"), (0.0000977, "0.0000977"), (1234567.0, "1234567"), (1.37, "1.37")):
        (head,) = _files([_invoice(USD)], fx_rates={"USD": rate})[jde_zfiles.HEADER_FILE]
        assert head["VLCRR"] == text


def test_the_exchange_rate_is_in_the_domestic_currency_when_it_is_not_cad():
    """AP Coder's rates are CAD per unit: with JDE's domestic currency USD, a CAD invoice's VLCRR is USD per CAD
    (1 / 1.37), never the CAD rate 1; without a rate for the domestic currency it is left blank (E1's table)."""
    usd_books = jde_settings.JdeSettings(
        amount_mode=jde_settings.AMOUNT_GROSS, domestic_currency="USD", currencies=["CAD", "USD", "EUR"]
    )
    rates = {"CAD": 1.0, "USD": 1.37, "EUR": 1.5}
    (head,) = _files([_invoice(ON)], usd_books, fx_rates=rates)[jde_zfiles.HEADER_FILE]
    assert (head["VLCRRM"], head["VLCRCD"], head["VLCRR"]) == ("F", "CAD", "0.729927")
    euro = _invoice(ON)
    euro["final_output"]["currency"] = "EUR"
    (head,) = _files([euro], usd_books, fx_rates=rates)[jde_zfiles.HEADER_FILE]
    assert head["VLCRR"] == "1.0948905"  # USD per EUR: 1.50 / 1.37
    (head,) = _files([_invoice(ON)], usd_books, fx_rates={"CAD": 1.0, "EUR": 1.5})[jde_zfiles.HEADER_FILE]
    assert head["VLCRR"] == ""  # no USD rate: E1 takes it from its exchange-rate table
    (usd,) = _files([_invoice(USD)], usd_books, fx_rates=rates)[jde_zfiles.HEADER_FILE]
    assert (usd["VLCRRM"], usd["VLCRR"]) == ("D", "")  # domestic: no rate


def test_account_columns_mode_and_mapping():
    settings = jde_settings.JdeSettings(
        amount_mode=jde_settings.AMOUNT_GROSS,
        account_mode=jde_settings.ACCOUNT_COLUMNS,
        gl_map=[
            jde_settings.GlMap("6010", "", "100", "6010", "01"),
            jde_settings.GlMap("1500", "CC400", "200", "1500"),
        ],
    )
    lines = _files([_invoice(ON)], settings)[jde_zfiles.DIST_FILE]
    assert (lines[0]["VNMCU"], lines[0]["VNOBJ"], lines[0]["VNSUB"], lines[0]["VNANI"]) == ("100", "6010", "01", "")
    assert (lines[1]["VNMCU"], lines[1]["VNOBJ"]) == ("200", "1500")
    assert (lines[2]["VNMCU"], lines[2]["VNOBJ"]) == ("CC400", "6020")  # the default rule
    assert jde_accounts.ani(jde_accounts.account_for("6010", "", settings)) == "100.6010.01"
    no_cc = jde_settings.JdeSettings(amount_mode=jde_settings.AMOUNT_GROSS, bu_from_cost_center=False, default_bu="1")
    assert jde_accounts.ani(jde_accounts.account_for("6010", "CC400", no_cc)) == "1.6010"


def test_the_account_of_each_line_is_found_without_reading_the_whole_mapping():
    """B-1863: ``account_for`` read the account mapping from the top for each line (twice when a line's cost center
    had no row of its own): with the 20,000 rows a JD Edwards sync adds, 5,000 lines (a 500-voucher batch) took 13
    seconds, at every check, dry run, go-ahead and download. The mapping is looked up by GL code and cost center, the
    first row of each as before, and again once the mapping is another or has grown."""
    import time

    rows = [
        jde_settings.GlMap(f"{6000 + i}", "CC1" if i % 2 else "", "B1", f"{6000 + i}", f"{i % 7}") for i in range(20000)
    ]
    rows += [jde_settings.GlMap("6002", "", "SECOND", "6002")]  # a second row of a code: the first one counts
    settings = jde_settings.JdeSettings(default_bu="CC9", gl_map=rows)
    asked = [(f"{6000 + i * 7 % 30000}", ("CC1", "CC2", "")[i % 3]) for i in range(5000)]

    def scanned(gl: str, cc: str) -> jde_settings.GlMap | None:  # the mapping read row by row, as before
        exact = next((m for m in rows if m.gl_code == gl and m.cost_center == cc), None)
        row = exact or next((m for m in rows if m.gl_code == gl and not m.cost_center), None)
        return (jde_settings.GlMap(gl, cc, row.bu or cc or "CC9", row.obj or gl, row.sub, row.sbl, row.sblt)
                if row else jde_settings.GlMap(gl, cc, cc or "CC9", gl))  # fmt: skip

    expected = [scanned(gl, cc) for gl, cc in asked[:300]]
    started = time.perf_counter()
    found = [jde_accounts.account_for(gl, cc, settings) for gl, cc in asked]
    assert time.perf_counter() - started < 1.0
    assert found[:300] == expected and jde_accounts.account_for("6002", "", settings).bu == "B1"
    settings.gl_map.append(jde_settings.GlMap("99999", "", "NEW", "7000"))  # a row added: found at once
    assert jde_accounts.account_for("99999", "", settings).obj == "7000"
    settings.gl_map = [jde_settings.GlMap("6002", "", "ONLY", "6002")]  # another mapping
    assert jde_accounts.account_for("6002", "", settings).bu == "ONLY"


def test_a_subledger_too_long_for_jd_edwards_holds_the_voucher_back_before_the_batch():
    """VNSBL holds 8 characters and VNSBLT one: a longer mapping (from a spreadsheet) failed only when JD Edwards
    loaded the batch, where the business unit, object and subsidiary are checked before it is made."""

    def settings(sbl: str, sblt: str) -> jde_settings.JdeSettings:
        return jde_settings.JdeSettings(
            amount_mode=jde_settings.AMOUNT_GROSS, account_mode=jde_settings.ACCOUNT_COLUMNS,
            gl_map=[jde_settings.GlMap("6010", "", "100", "6010", "01", sbl, sblt)])  # fmt: skip

    assert jde.validate([_invoice(ON)], settings("12345678", "A"), AN8) == {}
    for sbl, sblt in (("123456789", "A"), ("12345678", "AB")):
        (reasons,) = jde.validate([_invoice(ON)], settings(sbl, sblt), AN8).values()
        assert any("GL 6010" in r and "subledger" in r and "too long" in r for r in reasons), reasons
        codes = {f[1] for f in jde_review.voucher_findings(_invoice(ON)["final_output"], settings(sbl, sblt), "10023")}
        assert "JDE_NO_ACCOUNT" in codes


def test_csv_columns_in_order_and_text_as_it_is():
    inv = _invoice(ON)
    inv["final_output"]["gl_distribution"][0]["description"] = "=HYPERLINK(evil)"
    raw = jde_zfiles.export_batch([inv], jde_settings.JdeSettings(amount_mode=jde_settings.AMOUNT_GROSS), AN8, batch=1)
    assert set(raw) == {jde_zfiles.HEADER_FILE, jde_zfiles.DIST_FILE}  # no match file outside PO-matched mode
    assert raw[jde_zfiles.HEADER_FILE].decode().splitlines()[0].split(",") == jde_zfiles.HEADER_COLUMNS
    assert raw[jde_zfiles.DIST_FILE].decode().splitlines()[0].split(",") == jde_zfiles.DIST_COLUMNS
    assert not raw[jde_zfiles.HEADER_FILE].startswith(b"\xef\xbb\xbf")  # no BOM for database loaders
    assert _csv(raw[jde_zfiles.DIST_FILE])[0]["VNEXA"] == "=HYPERLINK(evil)"  # for a database loader, not Excel


def test_the_g_l_bank_account_follows_the_pay_from_account_when_it_is_mapped():
    """D-66: VLGLBA (the G/L bank account E1 pays from) is a mapped setting per Pay from account: with no mapping the
    files are as before (no column, E1 takes the AAI PB bank); mapped, each voucher carries its account's value."""
    paid = _invoice(ON, 1, pay_from_account="CAD-OP")
    other = _invoice(QC, 2, pay_from_account="CAD-PAY")
    none = _invoice(AB, 3)
    assert "VLGLBA" not in _files([paid])[jde_zfiles.HEADER_FILE][0]
    settings = jde_settings.JdeSettings(amount_mode=jde_settings.AMOUNT_GROSS, bank_glba={"CAD-OP": "00012345"})
    rows = {r["VLEDTN"].split("-")[0]: r for r in _files([paid, other, none], settings)[jde_zfiles.HEADER_FILE]}
    assert [rows[f"APC{i}"]["VLGLBA"] for i in (1, 2, 3)] == ["00012345", "", ""]
    assert jde_zfiles.header_columns(settings) == [*jde_zfiles.HEADER_COLUMNS, "VLGLBA"]
    assert jde_settings.JdeSettings.from_json(settings.to_json()).bank_glba == {"CAD-OP": "00012345"}
    assert jde_settings.JdeSettings.from_json('{"bank_glba": "nope"}').bank_glba == {}


def test_an_unmapped_pay_from_account_is_said_and_codes_match_whatever_their_case():
    """B-684: with only CAD-OP mapped, a USD invoice's VLGLBA was blank with no word of it (E1 then pays from its
    default bank, AAI PB); a key typed "cad-op" mapped nothing."""
    cad = _invoice(ON, 1, pay_from_account="CAD-OP")
    usd = _invoice(USD, 2, pay_from_account="USD-OP")
    settings = jde_settings.JdeSettings(amount_mode=jde_settings.AMOUNT_GROSS, bank_glba={"cad-op": "00012345"})
    rows = {r["VLEDTN"].split("-")[0]: r for r in _files([cad, usd], settings)[jde_zfiles.HEADER_FILE]}
    assert rows["APC1"]["VLGLBA"] == "00012345" and rows["APC2"]["VLGLBA"] == ""
    none = _invoice(AB, 3)
    notes = jde_zfiles.bank_notes([cad, usd, none], settings)
    assert notes == {  # B-689: whole sentences
        2: ["AP Coder #2 is paid from USD-OP, which has no G/L bank account (VLGLBA) mapped: E1 pays it from its "
            "default bank (AAI PB), which may not be that account."],
        3: ["AP Coder #3 has no Pay from account: E1 pays it from its default bank (AAI PB)."],
    }  # fmt: skip
    assert (
        jde_zfiles.bank_notes([cad, usd], jde_settings.JdeSettings(amount_mode=jde_settings.AMOUNT_GROSS)) == {}
    )  # no mapping in use: nothing to say
    report = jde.dry_run([cad, usd], settings, AN8)
    assert "USD-OP" in jde.dry_run_text(report)
    data, _, _ = jde.build_zip([cad, usd], settings, AN8, batch=3)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        readme = zf.read("README.txt").decode()
    assert "AP Coder #2 is paid from USD-OP" in readme and "AP Coder #2: AP Coder" not in readme


def test_z_files_hold_the_text_as_it_is_without_spreadsheet_quoting():
    number = "+" + "A" * 24  # 25 characters: VLVINV's width
    inv = _invoice(ON, invoice_number=number)
    inv["final_output"]["gl_distribution"][0]["description"] = "- Labour, 4 h on site"
    assert jde.validate([inv], jde_settings.JdeSettings(amount_mode=jde_settings.AMOUNT_GROSS), AN8) == {}
    files = _files([inv])
    # JDE's own supplier + invoice number check still matches
    assert files[jde_zfiles.HEADER_FILE][0]["VLVINV"] == number
    line = files[jde_zfiles.DIST_FILE][0]
    assert (line["VNEXR"], line["VNEXA"]) == (number, "- Labour, 4 h on site")


# --- Checks -------------------------------------------------------------------------------------------------------


def test_validation_failures():
    settings = jde_settings.JdeSettings(amount_mode=jde_settings.AMOUNT_GROSS)
    assert "no JDE supplier number" in jde.validate([_invoice(ON)], settings, lambda f: "")[1][0]
    assert "not a JDE address number" in jde.validate([_invoice(ON)], settings, lambda f: "V10023")[1][0]
    assert "longer than 25" in jde.validate([_invoice(ON, invoice_number="X" * 26)], settings, AN8)[1][0]
    assert (
        "longer than 8"
        in jde.validate(
            [_invoice(ON, po_number="PO-882139")],
            jde_settings.JdeSettings(amount_mode=jde_settings.AMOUNT_GROSS, po_matched=True),
            AN8,
        )[1][0]
    )
    unmapped = jde.validate(
        [_invoice(ON)], jde_settings.JdeSettings(amount_mode=jde_settings.AMOUNT_GROSS, use_default_rule=False), AN8
    )[1]
    assert len(unmapped) == 5 and "GL 6010 / CC400 has no JDE account" in unmapped[0]
    no_bu = jde.validate(
        [_invoice(ON)], jde_settings.JdeSettings(amount_mode=jde_settings.AMOUNT_GROSS, bu_from_cost_center=False), AN8
    )[1]
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
    assert jde_settings.settings_problems(
        jde_settings.JdeSettings(amount_mode=jde_settings.AMOUNT_GROSS, company="ABC", user="", batch_prefix="X" * 10)
    ) and not (jde_settings.settings_problems(jde_settings.JdeSettings(amount_mode=jde_settings.AMOUNT_GROSS)))


def test_duplicates_in_the_batch_and_against_earlier_exports():
    settings = jde_settings.JdeSettings(amount_mode=jde_settings.AMOUNT_GROSS)
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


def test_an_earlier_export_known_only_by_its_gst_number_is_a_duplicate(tmp_path):
    from ap_coder.coding.vendors import norm_tax_number

    settings = jde_settings.JdeSettings(amount_mode=jde_settings.AMOUNT_GROSS)
    first = _invoice(ON, 1)
    gst = first["final_output"]["gst_hst_registration_number"]
    ids = {f"gst:{norm_tax_number(gst)}": "10023"}  # the vendor master knows the supplier by its GST/HST number
    second = _invoice(ON, 2, vendor_name="Northwind I.T. Solns")  # the same bill, the name read differently
    assert jde.validate([first, second], settings, ids) == {
        2: ["same supplier and invoice number as AP Coder #1 in this batch"]
    }
    # The first went out in an earlier batch: the store's rows of what is out carry its GST/HST number.
    store = Store(tmp_path / "ap.db")
    first_id = store.add_invoice(tmp_path / "a.pdf", first["final_output"], {})
    store.approve_invoice(first_id, first["final_output"], "Jane")
    batch = store.create_export_batch([first_id], jde_settings.FORMAT)
    problems = jde.validate([second], settings, ids, jde.prior_records(store))
    assert problems == {2: [f"already exported: AP Coder #{first_id} in batch {batch}"]}


def test_a_voucher_in_jd_edwards_for_one_of_two_suppliers_of_a_name_is_found_by_its_supplier_number(tmp_path):
    """B-2342: JD Edwards' voucher list keeps each voucher's supplier number, but the batch's duplicate check read
    the register by name only, and a name two supplier numbers share gives neither: an invoice picked as "LAKESIDE
    CATERING LTD #2004" that JD Edwards already has under 2004 went out again. Where both sides have a supplier
    number it decides; the other supplier of the name with the same invoice number is no duplicate."""
    from ap_coder.coding.vendors import master_columns, master_rows
    from ap_coder.erp import registers

    store = Store(tmp_path / "ap.db")
    suppliers = [{"Address Number": "2003", "Alpha Name": "LAKESIDE CATERING LTD"},
                 {"Address Number": "2004", "Alpha Name": "LAKESIDE CATERING LTD"}]  # fmt: skip
    store.import_vendor_master(master_rows(suppliers, master_columns(list(suppliers[0]))))
    names = {v["erp_id"]: v["display_name"] for v in store.master_vendors()}

    def voucher(document, an8, number):
        return {"Document Number": document, "Doc Type": "PV", "Doc Co": "00210", "Pay Item": "001",
                "Supplier Number": an8, "Supplier Number Desc": "LAKESIDE CATERING LTD", "Invoice Number": number,
                "Gross Amount": "525.00", "Invoice Date": "2026-11-02", "G/L Date": "2026-11-02"}  # fmt: skip

    items = [voucher("40001", "2004", "LC-7781"), voucher("40002", "2003", "LC-9000")]
    store.import_erp_vouchers(registers.vouchers_from_pay_items(items, registers.voucher_columns(list(items[0])),
                                                                names)[0])  # fmt: skip
    invoices = [{"id": n, "final_output": {"vendor_name": "LAKESIDE CATERING LTD #2004", "invoice_number": number,
                                           "grand_total": 525.0, "invoice_date": "2026-11-02"}}
                for n, number in ((1, "LC-7781"), (2, "LC-9000"))]  # fmt: skip
    settings, ids = jde_settings.JdeSettings(), store.vendor_ids()
    assert jde.duplicate_problems(invoices, settings, ids, jde.prior_records(store, invoices=invoices)) == {
        1: ["already in the ERP (invoice LC-7781, dated 2026-11-02)"]
    }


def test_a_voucher_in_jd_edwards_is_found_when_the_supplier_number_was_typed_with_a_leading_zero():
    """B-2487: the duplicate check compared JD Edwards' supplier number with ours as text: an override typed
    "02004" missed the voucher list's "2004" (and the name is not asked when both sides have a number), so a voucher
    JD Edwards already has went out again. The hold check and the API's compare the numbers without leading zeros."""
    final = {"vendor_name": "LAKESIDE CATERING LTD", "invoice_number": "LC-7781", "grand_total": 525.0,
             "invoice_date": "2026-11-02"}  # fmt: skip
    register = [{"invoice_number": "LC-7781", "total": 525.0, "an8": "2004", "vendor_name": "LAKESIDE CATERING",
                 "invoice_date": "2026-11-02"}]  # fmt: skip
    for typed in ("2004", "02004"):
        settings = jde_settings.JdeSettings(an8_overrides={vendor_key(final["vendor_name"]): typed})
        assert jde.duplicate_problems([{"id": 1, "final_output": final}], settings, None, {"register": register}) == {
            1: ["already in the ERP (invoice LC-7781, dated 2026-11-02)"]
        }, typed


def test_an8_from_the_vendor_master_and_the_overrides():
    final = _invoice(ON)["final_output"]
    ids = {"northwind it solutions": "4410"}
    assert (
        jde_accounts.resolve_an8(final, ids, jde_settings.JdeSettings(amount_mode=jde_settings.AMOUNT_GROSS)) == "4410"
    )
    by_gst = {"gst:123456782rt0001": "4411"}
    assert (
        jde_accounts.resolve_an8(final, by_gst, jde_settings.JdeSettings(amount_mode=jde_settings.AMOUNT_GROSS))
        == "4411"
    )
    override = jde_settings.JdeSettings(
        amount_mode=jde_settings.AMOUNT_GROSS, an8_overrides={vendor_key(final["vendor_name"]): "777"}
    )
    assert jde_accounts.resolve_an8(final, ids, override) == "777"


# --- Settings and files -------------------------------------------------------------------------------------------


def test_settings_round_trip(tmp_path):
    settings = jde_settings.JdeSettings(
        company="42", user="JDOE", batch_prefix="AP", amount_mode=jde_settings.AMOUNT_TAX, decimals=jde_settings.POINT,
        account_mode=jde_settings.ACCOUNT_COLUMNS, line_numbering=jde_settings.MATCH_HEADER, po_matched=True,
        po_extra_columns={"VLATFLG": "1"}, gl_map=[jde_settings.GlMap("6010", "CC400", "100", "6010", "01", "X", "A")],
        an8_overrides={"northwind it solutions": "4410"}, currencies=["CAD"],
    )  # fmt: skip
    settings.tax_map["ON"] = jde_settings.TaxArea("ONT", "V")
    assert jde_settings.JdeSettings.from_json(settings.to_json()) == settings
    assert settings.company_code == "00042"
    store = Store(tmp_path / "ap.db")
    assert jde_settings.load(store) == jde_settings.JdeSettings()  # nothing saved yet: the defaults
    assert jde_settings.save(store, settings, actor="jane") and not jde_settings.save(store, settings, actor="jane")
    assert jde_settings.load(store) == settings
    assert store.events(actions=["settings_changed"])[0]["detail"] == {"keys": [jde_settings.SETTING_KEY]}
    assert jde_settings.JdeSettings.from_json("not json") == jde_settings.JdeSettings(
        amount_mode=jde_settings.AMOUNT_GROSS
    )
    assert (
        jde_settings.JdeSettings.from_json('{"amount_mode": "both", "company": "7"}').amount_mode
        == jde_settings.AMOUNT_GROSS
    )


def test_default_tax_map():
    m = jde_settings.default_tax_map()
    assert (m["ON"].tax_area, m["ON"].code, m["NS"].code) == ("ON-HST", "V", "V")
    assert (m["QC"].tax_area, m["QC"].code) == ("QC-GSTQST", "V")
    assert {m[p].code for p in ("BC", "SK", "MB")} == {"C"}
    assert {m[p].code for p in ("AB", "NT", "NU", "YT")} == {"V"}


def test_gl_map_import_from_a_spreadsheet():
    rows = jde_accounts.gl_map_from_records([
        {"GL Code": "6010", "Cost Center": "CC400", "Business Unit": "100", "Object": "6010", "Subsidiary": "01"},
        {"GL Code": "", "Cost Center": "", "Business Unit": "1", "Object": "", "Subsidiary": ""},
        {"GL Code": "6800", "Cost Center": None, "Business Unit": "nan", "Object": "6810", "Subsidiary": ""},
    ])  # fmt: skip
    assert rows == [
        jde_settings.GlMap("6010", "CC400", "100", "6010", "01"),
        jde_settings.GlMap("6800", "", "", "6810", ""),
    ]


def test_zip_has_the_files_and_a_readme():
    data, name, mime = jde.build_zip(
        [_invoice(ON)],
        jde_settings.JdeSettings(amount_mode=jde_settings.AMOUNT_GROSS),
        AN8,
        batch=12,
        left_out={2: ["no JDE supplier number (AN8)"]},
    )
    assert (name, mime) == ("ap_coder_jde_batch_12.zip", "application/zip")
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert sorted(zf.namelist()) == ["DRY_RUN.txt", "F0411Z1.csv", "F0911Z1.csv", "README.txt"]
        text = zf.read("README.txt").decode()
        dry = zf.read("DRY_RUN.txt").decode()
    assert "Will load: 1 voucher(s)" in dry and "HELD BACK  AP Coder #2" in dry and "no JDE supplier number" in dry
    assert "R04110ZA (or R04110Z) in PROOF mode" in text and "R04110ZA in FINAL mode" in text and "APC12" in text
    assert "F0911Z1.csv  5 row(s)" in text and "AP Coder #2: no JDE supplier number" in text


# --- Dashboard ------------------------------------------------------------------------------------------------------


@pytest.fixture
def db(tmp_path):
    return tmp_path / "private" / "ap_coder.db"


def test_exports_page_jde_flow(db, cost_centers_demo_template):
    from ap_coder.web.pages.exports import batch_file
    from tests.webclient import client_for, follow, post

    store = copy_database(
        cost_centers_demo_template, db
    )  # JD Edwards' business units come from cost centers (the demo has none: D-53)
    client = client_for(store)
    ready = {i["vendor_name"]: i["id"] for i in store.unexported_approved()}
    northwind = next(i for n, i in ready.items() if n.startswith("Northwind"))
    chinook = next(i for n, i in ready.items() if n.startswith("Chinook"))
    page = client.get("/exports", params={"fmt": jde_settings.FORMAT}).text
    # No business unit for the tax accounts yet: nothing can go out.
    assert re.search(r'<button[^>]*id="export-go"[^>]*disabled', page)
    assert "cannot go to JD Edwards yet" in page
    settings = jde_settings.load(store)
    settings.default_bu = "HQ"  # the tax accounts' business unit (the tax coded to G/L accounts: D-93)
    settings.an8_overrides = {vendor_key(store.get_invoice(chinook)["vendor_name"]): "C10041"}  # not an AN8
    jde_settings.save(store, settings)
    page = client.get("/exports", params={"fmt": jde_settings.FORMAT}).text
    assert "Export 1 invoice<" in page
    follow(client, post(client, "/exports/create", {"fmt": jde_settings.FORMAT, "ids": list(ready.values())}))
    (batch,) = store.export_batches()
    assert batch["format"] == jde_settings.FORMAT and store.batch_invoice_ids(batch["id"]) == [northwind]
    assert [i["id"] for i in store.unexported_approved()] == [chinook]  # left out, still ready
    data, name, mime = batch_file(store, batch["id"], jde_settings.FORMAT)
    assert name == f"ap_coder_jde_batch_{batch['id']}.zip" and mime == "application/zip"
    download = post(client, f"/exports/batches/{batch['id']}/download")  # by POST: recorded (D-135)
    assert download.status_code == 200 and download.content[:2] == b"PK"
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        (head,) = _csv(zf.read(jde_zfiles.HEADER_FILE))
        assert (head["VLEDBT"].split("-")[0], head["VLAN8"], head["VLEDTN"].split("-")[0]) == (
            f"APC{batch['id']}",
            "10023",
            f"APC{northwind}",
        )  # and a code of each (D-182)
        assert len(_csv(zf.read(jde_zfiles.DIST_FILE))) == 6  # five lines and the HST on its own (D-93)
    # Exported once: the same invoice cannot be exported again until the batch is undone.
    assert (
        "already exported"
        in jde.validate(
            [store.get_invoice(northwind)], jde_settings.load(store), store.vendor_ids(), jde.prior_records(store)
        )[northwind][0]
    )
    store.undo_export_batch(batch["id"], actor="jane")
    assert jde.validate([store.get_invoice(northwind)], jde_settings.load(store), store.vendor_ids(),
                        jde.prior_records(store)) == {}  # fmt: skip
    assert client.get("/exports").status_code == 200  # the undone batch is listed, nothing breaks


def test_a_jde_batch_downloads_again_with_the_same_vouchers(db, cost_centers_demo_template):
    from ap_coder.web.pages.exports import jde_file, jde_left_out
    from tests.webclient import client_for, follow, post

    store = copy_database(
        cost_centers_demo_template, db
    )  # JD Edwards' business units come from cost centers (the demo has none: D-53)
    client = client_for(store)
    northwind = next(i for i in store.unexported_approved() if i["vendor_name"].startswith("Northwind"))
    settings = jde_settings.load(store)
    settings.default_bu = "HQ"  # the tax accounts' business unit (the tax coded to G/L accounts: D-93)
    settings.an8_overrides = {vendor_key(northwind["vendor_name"]): "10023"}
    jde_settings.save(store, settings)
    follow(client, post(client, "/exports/create", {"fmt": jde_settings.FORMAT, "ids": [northwind["id"]]}))
    (batch,) = store.export_batches()

    def vouchers() -> dict[str, bytes]:
        with zipfile.ZipFile(io.BytesIO(jde_file(store, batch["id"])[0])) as zf:
            return {n: zf.read(n) for n in (jde_zfiles.HEADER_FILE, jde_zfiles.DIST_FILE)}

    first = vouchers()
    assert len(_csv(first[jde_zfiles.HEADER_FILE])) == 1
    # AP loads the batch into JD Edwards, then imports the ERP's invoice register: the voucher is now in it.
    final = store.get_invoice(northwind["id"])["final_output"]
    store.import_erp_register([{"vendor_name": final["vendor_name"], "invoice_number": final["invoice_number"],
                                "total": final["grand_total"]}])  # fmt: skip
    assert vouchers() == first
    assert jde_left_out(store, batch["id"])[1] == {}
    # The JD Edwards settings change after the batch was made: the batch still holds its invoice.
    settings.an8_overrides = {vendor_key(northwind["vendor_name"]): "N10023"}  # not an AN8
    jde_settings.save(store, settings)
    again = vouchers()
    assert [r["VLEDTN"].split("-")[0] for r in _csv(again[jde_zfiles.HEADER_FILE])] == [f"APC{northwind['id']}"]
    with zipfile.ZipFile(io.BytesIO(jde_file(store, batch["id"])[0])) as zf:
        text = zf.read("README.txt").decode()
        dry = zf.read("DRY_RUN.txt").decode()
    assert "check before loading" in text and f"AP Coder #{northwind['id']}: " in text
    assert "it IS in this batch's files" in dry
    assert "Not in this batch" not in text
    assert store.batch_invoice_ids(batch["id"]) == [northwind["id"]]


def _one_jde_batch(store, send_due_date=True, **final_changes):
    """A JD Edwards batch of the demo's Northwind invoice (its coding changed by ``final_changes`` first), on a copy of
    ``cost_centers_demo_template``: JD Edwards' business units come from cost centers (the demo has none: D-53)."""
    from tests.webclient import client_for, follow, post

    northwind = next(i for i in store.unexported_approved() if i["vendor_name"].startswith("Northwind"))
    if final_changes:
        with store._conn() as conn:
            final = json.loads(conn.execute("SELECT final_output FROM invoices WHERE id = ?",
                                            (northwind["id"],)).fetchone()[0])  # fmt: skip
            conn.execute("UPDATE invoices SET final_output = ? WHERE id = ?",
                         (json.dumps({**final, **final_changes}), northwind["id"]))  # fmt: skip
    settings = jde_settings.load(store)
    settings.default_bu = "HQ"  # the tax accounts' business unit (the tax coded to G/L accounts: D-93)
    settings.an8_overrides = {vendor_key(northwind["vendor_name"]): "10023"}
    settings.send_due_date = send_due_date
    jde_settings.save(store, settings)
    client = client_for(store)
    follow(client, post(client, "/exports/create", {"fmt": jde_settings.FORMAT, "ids": [northwind["id"]]}))
    (batch,) = store.export_batches()
    return client, settings, batch["id"], northwind["id"]


def test_a_due_date_jde_cannot_hold_leaves_vlddj_blank_in_a_batch_downloaded_again(db, cost_centers_demo_template):
    """The batch was made with "send due date" off; switched on later, a due date of 3026 must not break the file."""
    from tests.webclient import post

    store = copy_database(cost_centers_demo_template, db)
    client, settings, batch, inv_id = _one_jde_batch(store, send_due_date=False, due_date="3026-10-14")
    settings.send_due_date = True
    jde_settings.save(store, settings)
    answer = post(client, f"/exports/batches/{batch}/download")
    assert answer.status_code == 200
    with zipfile.ZipFile(io.BytesIO(answer.content)) as zf:
        (head,) = _csv(zf.read(jde_zfiles.HEADER_FILE))
        readme = zf.read("README.txt").decode()
    assert head["VLEDTN"].startswith(f"APC{inv_id}-") and head["VLDDJ"] == ""
    assert "check before loading" in readme and "due date 3026-10-14" in readme


def test_a_jde_batch_is_not_downloaded_while_a_setting_stops_every_export(db, cost_centers_demo_template):
    """A Z-file with no User ID cannot load, and its own dry run says so: the download says what to fix instead."""
    from tests.webclient import post

    store = copy_database(cost_centers_demo_template, db)
    client, settings, batch, _inv_id = _one_jde_batch(store)
    settings.user = ""
    jde_settings.save(store, settings)
    answer = post(client, f"/exports/batches/{batch}/download")
    assert answer.status_code == 409 and "User ID" in answer.text and "Settings" in answer.text
    assert client.get(f"/exports/batches/{batch}/download?fmt=xlsx").status_code == 200  # other formats still go


def test_settings_page_jde_tab(db):
    from tests.web.test_web_settings import general_form
    from tests.webclient import client_for, follow, post

    store = Store(db)
    client = client_for(store)
    follow(client, post(client, "/settings/jde", general_form(company="42")))
    assert jde_settings.load(store).company_code == "00042"
    tax_map = jde_settings.load(store).tax_map
    provinces = list(tax_map)
    table = {"province": provinces, "tax_area": [tax_map[p].tax_area for p in provinces],
             "code": [tax_map[p].code for p in provinces]}  # fmt: skip
    follow(client, post(client, "/settings/jde/tax-map", table))
    assert jde_settings.load(store).tax_map["ON"] == jde_settings.TaxArea("ON-HST", "V")
    assert "Confirm with your JDE team" in client.get("/settings/jde").text


def test_docs_exist():
    text = (ROOT / "docs" / "JDE_E1.md").read_text(encoding="utf-8")
    for column in [*jde_zfiles.HEADER_COLUMNS, *jde_zfiles.DIST_COLUMNS]:
        assert column in text, column
    assert "R04110ZA" in text and "JP040000" in text and "Orchestrator" in text


def test_no_balance_message_while_a_line_has_no_account_or_the_province_is_unknown():
    """B-812: an uncoded line, or a province of supply not read, has its own finding: "unbalanced … check the Tax
    setup" beside it misled the clerk. The voucher is still held back, and a real imbalance is still said."""
    settings = jde_settings.JdeSettings(amount_mode=jde_settings.AMOUNT_GROSS)
    uncoded = _invoice(ON)
    uncoded["final_output"]["gl_distribution"][0]["gl_code"] = "UNASSIGNED"
    nowhere = _invoice(ON, supplier_province="", ship_to_province="")
    for inv in (uncoded, nowhere):
        problems = jde.validate([inv], settings, AN8)[1]  # still refused
        assert problems and not any(p.startswith("unbalanced") for p in problems), problems
        codes = {c for _, c, _, _ in jde_review.voucher_findings(inv["final_output"], settings, "10023")}
        assert "JDE_UNBALANCED" not in codes
    pst_recoverable = _invoice(BC, setup=_setup(PST=TaxTreatment("PST", RECOVERABLE, "2330")))
    codes = {c for _, c, _, _ in jde_review.voucher_findings(pst_recoverable["final_output"], settings, "10023")}
    assert "JDE_UNBALANCED" in codes


def test_a_po_number_longer_than_jde_holds_is_kept_here_and_not_sent():
    """D-87 (B-813): vendors print POs like "4500078813" or "MNT-2026-028", longer than VLPO's 8 characters. The
    clerk is not made to change what is printed: the voucher goes out without a PO reference (VLPO, VLPDCT, VLPKCO
    blank), the PO kept in AP Coder and in the remark when it fits, with a warning. A PO-matched voucher needs its
    PO in E1's order number: still held back."""
    settings = jde_settings.JdeSettings(amount_mode=jde_settings.AMOUNT_GROSS)
    for po in ("4500078813", "MNT-2026-028"):
        inv = _invoice(ON, po_number=po)
        assert jde.validate([inv], settings, AN8) == {}
        (finding,) = [
            f for f in jde_review.voucher_findings(inv["final_output"], settings, "10023") if f[1] == "JDE_PO_LONG"
        ]
        assert finding[0] == "warning"
        assert finding[2] == ("JD Edwards' PO field holds 8 characters: this PO number is kept in AP Coder and not "
                              "sent")  # fmt: skip
        header = _files([inv])[jde_zfiles.HEADER_FILE][0]
        assert (header["VLPO"], header["VLPDCT"], header["VLPKCO"]) == ("", "", "")
        assert header["VLRMK"] == f"AP Coder #1 PO {po}"
    short = _files([_invoice(ON, po_number="PO77120")])[jde_zfiles.HEADER_FILE][0]
    assert (short["VLPO"], short["VLRMK"]) == ("PO77120", "AP Coder #1")
    too_long = _files([_invoice(ON, po_number="P" * 20)])[jde_zfiles.HEADER_FILE][0]
    assert too_long["VLRMK"] == "AP Coder #1"  # no room in the remark's 30 characters
    matched = jde_settings.JdeSettings(amount_mode=jde_settings.AMOUNT_GROSS, po_matched=True)
    inv = _invoice(ON, po_number="4500078813")
    assert "longer than 8" in jde.validate([inv], matched, AN8)[1][0]
    (finding,) = [
        f for f in jde_review.voucher_findings(inv["final_output"], matched, "10023") if f[1] == "JDE_PO_LONG"
    ]
    assert finding[0] == "error"


def test_a_line_with_no_gl_account_yet_has_no_e1_account():
    """An uncoded line (UNASSIGNED) is not an object account: the review screen showed "CC400.UNASSIGN" (B-830)."""
    settings = jde_settings.JdeSettings(amount_mode=jde_settings.AMOUNT_GROSS, default_bu="CC400")
    assert (
        jde_accounts.account_for("UNASSIGNED", "", settings) is None
        and jde_accounts.account_text("UNASSIGNED", "", settings) == ""
    )
    assert jde_accounts.account_text("6010", "", settings) == "CC400.6010"


def _recoded(stem, gl_code, cost_center, **kw):
    """A sample invoice with every line coded to one account string's two codes."""
    doc = json.loads((SAMPLES / "ground_truth" / f"{stem}.json").read_text())
    lines = [{**li, "predicted_gl_code": gl_code, "predicted_cost_center": cost_center} for li in doc["line_items"]]
    return _invoice(stem, line_items=lines, **kw)


def test_a_gl_code_with_a_dot_is_object_and_subsidiary_under_the_default_rule():
    """B-2085: with the default account rule and no mapping row, a GL code "object.subsidiary" (D-209) was taken
    whole as the object, so every such line was held as "too long". It is split at the dot; a mapping row wins."""
    settings = jde_settings.JdeSettings(amount_mode=jde_settings.AMOUNT_GROSS)
    account = jde_accounts.account_for("6420.2210", "0312005561", settings)
    assert (account.bu, account.obj, account.sub) == ("0312005561", "6420", "2210")
    assert (
        jde_accounts.ani(account)
        == jde_accounts.account_text("6420.2210", "0312005561", settings)
        == "0312005561.6420.2210"
    )
    plain = jde_accounts.account_for("3015", "0312005561", settings)
    assert (plain.obj, plain.sub) == ("3015", "") and jde_accounts.ani(
        plain
    ) == "0312005561.3015"  # no subsidiary: as before
    inv = _recoded(AB, "6420.2210", "0312005561")
    assert jde.validate([inv], settings, AN8) == {}
    assert {r["VNANI"] for r in _files([inv], settings)[jde_zfiles.DIST_FILE]} == {"0312005561.6420.2210"}
    columns = jde_settings.JdeSettings(amount_mode=jde_settings.AMOUNT_GROSS, account_mode=jde_settings.ACCOUNT_COLUMNS)
    assert jde.validate([inv], columns, AN8) == {}
    assert {(r["VNMCU"], r["VNOBJ"], r["VNSUB"]) for r in _files([inv], columns)[jde_zfiles.DIST_FILE]} == {
        ("0312005561", "6420", "2210")
    }
    # A mapping row still wins: its object and subsidiary as they are, or its business unit alone.
    mapped = jde_settings.JdeSettings(
        gl_map=[jde_settings.GlMap("6420.2210", "", "77", "7000", ""), jde_settings.GlMap("6430.01", bu="88")]
    )
    assert jde_accounts.ani(jde_accounts.account_for("6420.2210", "0312005561", mapped)) == "77.7000"
    assert jde_accounts.ani(jde_accounts.account_for("6430.01", "0312005561", mapped)) == "88.6430.01"
    # A subsidiary longer than JD Edwards holds is still said.
    reason = jde.validate([_recoded(AB, "6420.123456789", "0312005561")], settings, AN8)[1][0]
    assert "too long" in reason and "0312005561.6420.123456789" in reason


def _quebec_qst_in_the_expense(code="V", **settings):
    """A Quebec invoice coded with the GST recovered and the QST left in the expense, and settings that send tax
    amounts with Quebec on a GST-only tax area."""
    inv = _invoice(QC, setup=_setup(QST=TaxTreatment("QST", "expense_to_line")))
    js = jde_settings.JdeSettings(amount_mode=jde_settings.AMOUNT_TAX, **settings)
    js.tax_map["QC"] = jde_settings.TaxArea("QC-GST", code)
    return inv, js


def test_quebec_with_the_qst_left_in_the_expense_puts_only_the_gst_on_the_pay_item():
    """B-2086: GST recovered and QST left in the expense lines (the Tax setup's "add to the expense line"), tax
    explanation code V: the voucher was held as JDE_UNBALANCED, the code taken to recover the QST too. The pay item
    carries the recovered tax only (D-210): taxable = the base, tax = the GST, the QST in the non-taxable amount and
    inside the G/L lines, and the voucher balances."""
    inv, settings = _quebec_qst_in_the_expense()
    assert jde.validate([inv], settings, AN8) == {}
    assert "JDE_UNBALANCED" not in {f[1] for f in jde_review.voucher_findings(inv["final_output"], settings, "10023")}
    files = _files([inv], settings)
    (head,) = files[jde_zfiles.HEADER_FILE]
    assert (head["VLTXA1"], head["VLEXR1"]) == ("QC-GST", "V")
    assert (head["VLATXA"], head["VLSTAM"], head["VLATXN"]) == ("240000", "12000", "23940")
    lines = [int(r["VNAA"]) for r in files[jde_zfiles.DIST_FILE]]
    assert lines == [131969, 37392, 94579] and sum(lines) == 275940 - 12000  # each line with its QST inside
    assert jde_voucher.sent_tax(inv, settings, "10023") == 120.0
    # One pay item per G/L line: each with its share of the GST only, and its QST as non-taxable.
    each = _files([inv], _quebec_qst_in_the_expense(line_numbering=jde_settings.MATCH_HEADER)[1])[
        jde_zfiles.HEADER_FILE
    ]
    assert [(h["VLATXA"], h["VLSTAM"], h["VLATXN"]) for h in each] == [
        ("120000", "6000", "11969"), ("34000", "1700", "3392"), ("86000", "4300", "8579"),
    ]  # fmt: skip


def test_what_the_tax_setup_recovers_is_still_all_on_the_pay_item_and_a_real_mismatch_still_held():
    """D-210 changes nothing where the Tax setup recovers what the code posts, and still holds a voucher whose Tax
    setup recovers a tax the code leaves in the expense."""
    full = jde_settings.JdeSettings(amount_mode=jde_settings.AMOUNT_TAX)
    (qc,) = _files([_invoice(QC)], full)[jde_zfiles.HEADER_FILE]  # GST and QST both recovered: both E1's to post
    assert (qc["VLATXA"], qc["VLATXN"], qc["VLSTAM"]) == ("240000", "0", "35940")
    (bc,) = _files([_invoice(BC)], full)[jde_zfiles.HEADER_FILE]  # code C: the PST is the area's sales tax, as before
    assert (bc["VLATXA"], bc["VLATXN"], bc["VLSTAM"]) == ("272600", "0", "32712")
    recovered = _invoice(BC, setup=_setup(PST=TaxTreatment("PST", RECOVERABLE, "2330")))
    assert "unbalanced" in jde.validate([recovered], full, AN8)[1][0]  # PST recovered, code C expenses it
    # With the gross amount only, JD Edwards would work the GST out of an amount that holds the QST: held, and said.
    inv, settings = _quebec_qst_in_the_expense()
    settings.amount_mode = jde_settings.AMOUNT_GROSS
    (reason,) = jde.validate([inv], settings, AN8)[1]
    assert "QST" in reason and "gross amount only" in reason and "tax amounts" in reason


def test_the_remarks_stay_as_they_were_or_carry_what_a_person_types():
    """D-210: one setting for the two remarks. As before, the pay item's (VLRMK) is AP Coder's invoice number and each
    G/L line's (VNEXR) the invoice number; or both are the remarks a person types by hand (``jde_remarks.line_remark``,
    ``jde_remarks.voucher_remark``: the keying sheet's too), the invoice number being in VLVINV either way."""
    inv = _invoice(ON)
    final = inv["final_output"]
    described = [" ".join(li["description"].split())[:30].rstrip() for li in final["line_items"]]
    assert [jde_remarks.line_remark(li) for li in final["line_items"]] == described and jde_remarks.voucher_remark(
        inv
    ) == described[0]
    assert (
        jde_remarks.line_remark({"description": "  two\n lines  "}) == "two lines" and jde_remarks.line_remark({}) == ""
    )
    blank_first = {"final_output": {"line_items": [{"description": " "}, {"description": "Toner"}]}}
    assert jde_remarks.voucher_remark(blank_first) == "Toner" and jde_remarks.voucher_remark({}) == ""

    assert jde_settings.JdeSettings().remarks == jde_settings.REMARKS_REFERENCE
    before = _files([inv])
    assert before[jde_zfiles.HEADER_FILE][0]["VLRMK"] == "AP Coder #1"
    assert {r["VNEXR"] for r in before[jde_zfiles.DIST_FILE]} == {final["invoice_number"]}
    assert [r["VNEXA"] for r in before[jde_zfiles.DIST_FILE]] == described

    typed = jde_settings.JdeSettings(amount_mode=jde_settings.AMOUNT_GROSS, remarks=jde_settings.REMARKS_TYPED)
    files = _files([inv], typed)
    (head,) = files[jde_zfiles.HEADER_FILE]
    assert head["VLRMK"] == described[0] and head["VLVINV"] == final["invoice_number"]
    assert (
        [r["VNEXR"] for r in files[jde_zfiles.DIST_FILE]]
        == [r["VNEXA"] for r in files[jde_zfiles.DIST_FILE]]
        == described
    )
    # A PO number too long for VLPO is still added to the pay item's remark when it fits its 30 characters.
    short = _invoice(ON, po_number="4500078813", line_items=[{**final["line_items"][0], "description": "Laptops"}],
                     subtotal=final["line_items"][0]["amount"])  # fmt: skip
    assert jde_zfiles.voucher_rows(short, "10023", typed)[0][0]["VLRMK"] == "Laptops PO 4500078813"
    assert _files([_invoice(ON, po_number="4500078813")], typed)[jde_zfiles.HEADER_FILE][0]["VLRMK"] == described[0]

    # Saved settings from before the choice, or with one not known: as they were.
    assert (
        jde_settings.JdeSettings.from_json(json.dumps({"company": "00002"})).remarks == jde_settings.REMARKS_REFERENCE
    )
    assert jde_settings.JdeSettings.from_json(json.dumps({"remarks": "both"})).remarks == jde_settings.REMARKS_REFERENCE
    assert jde_settings.JdeSettings.from_json(typed.to_json()).remarks == jde_settings.REMARKS_TYPED


def test_code_v_with_the_gst_left_in_the_expense_is_said_before_it_goes_out():
    """B-2150: GST set to "not recoverable" by mistake under code V went out as "V, the tax area, no tax amounts"
    with nothing said: typed so, JD Edwards works the tax out itself; loaded, the input tax credit is never claimed.
    The review screen warns (JDE_TAX_NOT_RECOVERED), and a voucher with no tax left on its pay item goes with the
    exempt code, as one that charges no tax does."""
    settings = jde_settings.JdeSettings(amount_mode=jde_settings.AMOUNT_TAX)
    inv = _invoice(AB, setup=_setup(GST=TaxTreatment("GST", "expense_to_line")))
    assert jde.validate([inv], settings, AN8) == {}  # a warning, not a hold
    (found,) = [
        f for f in jde_review.voucher_findings(inv["final_output"], settings, "10023") if f[1].startswith("JDE_TAX")
    ]
    assert found[:2] == (jde_review.WARNING, "JDE_TAX_NOT_RECOVERED")
    assert "the GST (87.30) is not recovered" in found[2] and "GL accounts & tax" in found[2]
    files = _files([inv], settings)
    (head,) = files[jde_zfiles.HEADER_FILE]
    assert (head["VLEXR1"], head["VLTXA1"]) == ("E", "AB-GST")
    assert (head["VLATXA"], head["VLATXN"], head["VLSTAM"]) == ("0", "183330", "0")
    assert sum(int(r["VNAA"]) for r in files[jde_zfiles.DIST_FILE]) == 183330
    # Both Quebec taxes in the expense: the same, the federal one named.
    both = _invoice(QC, setup=_setup(GST=TaxTreatment("GST", "expense_to_line"),
                                     QST=TaxTreatment("QST", "expense_to_line")))  # fmt: skip
    (found,) = [
        f for f in jde_review.voucher_findings(both["final_output"], settings, "10023") if f[1].startswith("JDE_TAX")
    ]
    assert "the GST (120.00) is not recovered" in found[2]
    assert _files([both], settings)[jde_zfiles.HEADER_FILE][0]["VLEXR1"] == "E"
    # With the gross amount only and the exempt code JD Edwards works out no tax: it balances, nothing to hold.
    assert jde.validate([both], jde_settings.JdeSettings(amount_mode=jde_settings.AMOUNT_GROSS), AN8) == {}
    # The QST alone left in the expense is the Tax setup's choice (D-210): code V stays, and nothing is warned.
    qst, on_gst_area = _quebec_qst_in_the_expense()
    for fine, js in ((qst, on_gst_area), (_invoice(AB), settings)):
        assert "JDE_TAX_NOT_RECOVERED" not in {
            f[1] for f in jde_review.voucher_findings(fine["final_output"], js, "10023")
        }


def test_the_rule_for_a_tax_left_in_the_expense_is_code_vs_alone_and_counts_a_meals_cent_as_recovered():
    """D-210's edges, each a deliberate break no test noticed: code C still holds a GST the Tax setup expenses; a
    cent of GST all on a meal (nothing left to claim, so no tax line) is still a recovered tax; and the taxable
    amount is the recovered tax's base, not a wider one of the tax left in the expense."""
    tax_amounts = jde_settings.JdeSettings(amount_mode=jde_settings.AMOUNT_TAX)
    expensed = _invoice(BC, setup=_setup(GST=TaxTreatment("GST", "expense_to_line")))
    assert "unbalanced" in jde.validate([expensed], tax_amounts, AN8)[1][0]  # BC is code C: as before D-210
    # A meal of 0.10 with a cent of GST, among other lines: the cent is in the meal's line and no GST line is left.
    from tests.erp.test_jde_gl_mode import _setup as meals_setup
    from tests.erp.test_jde_gl_mode import coded, doc, line, tl

    meal = coded(doc([line(1, 0.10, "6300", ["GST"], desc="Coffee")], [tl("GST", "", 0.05, 0.10, 0.01)], prov="AB"),
                 meals_setup(meals=["6300"]))  # fmt: skip
    assert not [e for e in meal["final_output"]["gl_distribution"] if e["kind"] == "tax"]
    v = jde_voucher._prepare(
        meal, jde_settings.JdeSettings(amount_mode=jde_settings.AMOUNT_TAX, default_bu="HQ"), "10023"
    )
    assert v.expensed == frozenset() and v.recoverable_types >= {"GST"} and (v.stam, v.recoverable) == (0, 0)
    assert v.tax_code == "V" and not v.problems
    # The QST charged on more than the GST (a line the GST does not apply to): the taxable amount is the GST's base.
    lines = [line(1, 1000.0, "6010", ["GST", "QST"]), line(2, 500.0, "6010", ["QST"], exempt=["GST"])]
    wider = coded(doc(lines, [tl("GST", "", 0.05, 1000.0, 50.0), tl("QST", "QC", 0.09975, 1500.0, 149.63)], prov="QC"),
                  _setup(QST=TaxTreatment("QST", "expense_to_line")))  # fmt: skip
    _inv, on_gst_area = _quebec_qst_in_the_expense()
    assert jde.validate([wider], on_gst_area, AN8) == {}
    (head,) = _files([wider], on_gst_area)[jde_zfiles.HEADER_FILE]
    assert (head["VLATXA"], head["VLSTAM"], head["VLATXN"]) == ("100000", "5000", "64963")
    # One pay item per line: the taxable amount follows the GST's lines, not the QST's wider ones.
    on_gst_area.line_numbering = jde_settings.MATCH_HEADER
    each = _files([wider], on_gst_area)[jde_zfiles.HEADER_FILE]
    assert [(h["VLATXA"], h["VLSTAM"]) for h in each] == [("100000", "5000"), ("0", "0")]


def test_a_mapping_rows_own_subsidiary_wins_over_the_gl_codes_when_it_names_no_object():
    """B-2085's corner: a mapping row that gives a subsidiary and no object keeps its subsidiary; the object is the
    GL code's."""
    mapped = jde_settings.JdeSettings(
        gl_map=[jde_settings.GlMap("6420.2210", sub="9001"), jde_settings.GlMap("6431", sub="77")]
    )
    assert jde_accounts.ani(jde_accounts.account_for("6420.2210", "0312005561", mapped)) == "0312005561.6420.9001"
    assert jde_accounts.ani(jde_accounts.account_for("6431", "0312005561", mapped)) == "0312005561.6431.77"


def _two_provinces(taxes, lines=None, **kw):
    from tests.erp.test_jde_gl_mode import coded, doc, line

    lines = lines or [line(1, 2400.0, "6010", ["GST"]), line(2, 600.0, "6010", ["GST"]),
                      line(3, 1000.0, "6010", ["HST"])]  # fmt: skip
    return coded(doc(lines, taxes, prov="AB", **kw))


def test_two_federal_rates_on_one_invoice_are_two_pay_items_spelled_out_and_the_voucher_is_held():
    """B-2152: lines supplied in two provinces (GST on some, HST on others) went out as one pay item on one tax
    area, the two taxes added up ("GST of 9.33%"), with nothing holding it. A pay item has one tax area: the voucher
    is held (JDE_TAX_AREA) and the reason spells out the two pay items to key by hand."""
    from tests.erp.test_jde_gl_mode import line, tl

    tax_amounts = jde_settings.JdeSettings(amount_mode=jde_settings.AMOUNT_TAX)
    inv = _two_provinces([tl("GST", "", 0.05, 3000.0, 150.0), tl("HST", "ON", 0.13, 1000.0, 130.0)])
    (reason,) = jde.validate([inv], tax_amounts, AN8)[1]
    assert reason == (
        "GST 5% and HST 13% are both charged (lines supplied in two provinces): a pay item has one tax area, so this "
        "voucher is two pay items, to key by hand with the G/L lines as coded. Pay item 001: tax area AB-GST, code V, "
        "taxable amount 3,000.00, tax amount 150.00 (GST 5%: lines 1, 2). Pay item 002: tax area ON-HST, code V, "
        "taxable amount 1,000.00, tax amount 130.00 (HST 13%: line 3)"
    )
    (found,) = [
        f for f in jde_review.voucher_findings(inv["final_output"], tax_amounts, "10023") if f[0] == jde_review.ERROR
    ]
    assert found[1] == "JDE_TAX_AREA" and found[2].startswith("JD Edwards: GST 5% and HST 13% are both charged")
    assert jde.validate([inv], jde_settings.JdeSettings(amount_mode=jde_settings.AMOUNT_GROSS), AN8)[1][0] == reason
    from ap_coder.erp import jde_keying

    ready, held = jde_keying.vouchers([inv], tax_amounts, AN8)
    assert not ready and held[0].why == [reason]
    # HST at two rates (Ontario and Nova Scotia): the lines' tax codes do not say which lines are whose, their
    # amounts do (``taxmath.invoice_taxes``'s ``areas``, B-2227); two lines of one amount are not told apart.
    lines = [line(1, 1000.0, "6010", ["HST"]), line(2, 500.0, "6010", ["HST"])]
    two_hst = _two_provinces([tl("HST", "ON", 0.13, 1000.0, 130.0), tl("HST", "NS", 0.14, 500.0, 70.0)], lines,
                             ship_to_province="ON", supplier_province="ON")  # fmt: skip
    (reason,) = jde.validate([two_hst], tax_amounts, AN8)[1]
    assert reason.startswith("HST 13% and HST 14% are both charged (lines supplied in two provinces)")
    assert ("Pay item 001: tax area ON-HST, code V, taxable amount 1,000.00, tax amount 130.00 (HST 13%: line 1)."
            in reason)  # fmt: skip
    assert reason.endswith("Pay item 002: tax area NS-HST, code V, taxable amount 500.00, tax amount 70.00 (HST 14%: "
                           "line 2)")  # fmt: skip
    lines = [line(1, 500.0, "6010", ["HST"]), line(2, 500.0, "6010", ["HST"])]
    alike = _two_provinces([tl("HST", "ON", 0.13, 500.0, 65.0), tl("HST", "NS", 0.14, 500.0, 70.0)], lines,
                           ship_to_province="ON", supplier_province="ON")  # fmt: skip
    assert jde.validate([alike], tax_amounts, AN8)[1][0].endswith("taxable amount 500.00, tax amount 70.00 (HST 14%)")
    # One federal rate is one pay item: the same tax printed twice (goods, freight), a tax line with no rate read,
    # GST with a provincial tax; and with the tax coded to G/L accounts there is no tax area to split by.
    one = [line(1, 1000.0, "6010", ["GST"]), line(2, 100.0, "6010", ["GST"])]
    twice = _two_provinces([tl("GST", "", 0.05, 1000.0, 50.0), tl("GST", "", 0.05, 100.0, 5.0)], one)
    no_rate = _two_provinces([tl("HST", "ON", 0.13, 1000.0, 130.0), tl("HST", "", 0.0, 100.0, 13.0)],
                             [line(1, 1000.0, "6010", ["HST"]), line(2, 100.0, "6010", ["HST"])],
                             ship_to_province="ON", supplier_province="ON")  # fmt: skip
    for fine in (twice, no_rate, _invoice(QC), _invoice(BC)):
        assert jde.validate([fine], tax_amounts, AN8) == {}
    assert jde.validate([inv], jde_settings.JdeSettings(default_bu="HQ"), AN8) == {}


def test_the_voucher_view_says_what_is_keyed_on_the_pay_item_or_why_it_is_not_ready():
    """The review screen's JD Edwards block: beside its rows, the pay item's G/L date, tax explanation code, tax
    rate/area, taxable and tax amounts, and when the voucher is held, why it is not ready to key."""
    from tests.erp.test_jde_gl_mode import tl

    tax_amounts = jde_settings.JdeSettings(amount_mode=jde_settings.AMOUNT_TAX)
    final = _invoice(AB)["final_output"]
    view = jde_review.voucher_view(final, tax_amounts, "10023")
    assert {"rows", "settings_url", "terms_sent", "terms_code", "terms_note"} <= set(view)  # as before
    assert (view["gl_date"], view["tax_code"], view["tax_area"]) == (final["invoice_date"], "V", "AB-GST")
    assert (view["taxable"], view["tax"], view["not_ready"]) == (1746.0, 87.3, [])
    assert view["keyed"] == (f"Keyed on the pay item: G/L date {final['invoice_date']} · Tax Ex V · Tax Rate/Area "
                             "AB-GST · taxable amount 1,746.00 · tax amount 87.30")  # fmt: skip
    # The tax coded to G/L accounts: no tax on the pay item.
    on_gl = jde_review.voucher_view(final, jde_settings.JdeSettings(default_bu="HQ"), "10023")
    assert (on_gl["tax_code"], on_gl["tax_area"], on_gl["taxable"], on_gl["tax"]) == ("E", "", None, None)
    assert on_gl["keyed"] == (f"Keyed on the pay item: G/L date {final['invoice_date']} · Tax Ex E · no tax (it is "
                              "on its own G/L lines)")  # fmt: skip
    # A G/L date in a closed period, moved to the open one's first day (D-182).
    moved = jde_review.voucher_view(
        final, jde_settings.JdeSettings(amount_mode=jde_settings.AMOUNT_TAX, open_from="2027-01-01"), "10023"
    )
    assert moved["gl_date"] == "2027-01-01" and "G/L date 2027-01-01" in moved["keyed"]
    # Held back: every reason, and the first in the line.
    held = jde_review.voucher_view(final, tax_amounts, "")
    assert len(held["not_ready"]) == 1 and held["not_ready"][0].startswith("no JDE supplier number (AN8)")
    assert held["keyed"] == f"Not ready to key: {held['not_ready'][0]}"
    two = _two_provinces([tl("GST", "", 0.05, 3000.0, 150.0), tl("HST", "ON", 0.13, 1000.0, 130.0)])
    view = jde_review.voucher_view(two["final_output"], tax_amounts, "10023")
    assert view["keyed"].startswith("Not ready to key: GST 5% and HST 13% are both charged")


def test_the_review_screens_g_l_date_row_is_the_date_keyed_moved_into_the_open_period():
    """B-2491: the review screen's G/L date row ignored the open period: "2026-10-20 (the invoice date)" beside
    "Keyed on the pay item: G/L date 2026-11-01". It is the date keyed, and says it was moved and from what."""
    final = {**_invoice(AB)["final_output"], "invoice_date": "2026-10-20"}

    def gl_row(**kw):
        settings = jde_settings.JdeSettings(amount_mode=jde_settings.AMOUNT_TAX, **kw)
        view = jde_review.voucher_view(final, settings, "10023", on=dt.date(2026, 11, 12))
        return next(r for r in view["rows"] if r["column"] == "VLDGJ")["value"], view["gl_date"]

    assert gl_row() == ("2026-10-20 (the invoice date)", "2026-10-20")
    assert gl_row(open_from="2026-11-01") == (
        "2026-11-01 (moved into JD Edwards' open period from the invoice date, 2026-10-20, in a closed period)",
        "2026-11-01",
    )
    assert gl_row(gl_date=jde_settings.GL_DATE_EXPORT) == ("2026-11-12 (the day of the export)", "2026-11-12")


def test_the_prototype_settings_are_the_way_of_a_department_that_types_its_vouchers():
    """`JdeSettings()` keeps its defaults (existing installs); `prototype_settings()` is the tax on the pay item
    with code V, a tax rate/area a province, the remarks as typed and the identity account rule."""
    from ap_coder.bench import jde_company

    assert (jde_settings.JdeSettings().amount_mode, jde_settings.JdeSettings().remarks) == (
        jde_settings.AMOUNT_GL,
        jde_settings.REMARKS_REFERENCE,
    )
    js = jde_prototype.prototype_settings()
    assert (js.amount_mode, js.remarks) == (jde_settings.AMOUNT_TAX, jde_settings.REMARKS_TYPED)
    assert (js.use_default_rule, js.bu_from_cost_center, js.gl_map) == (True, True, [])
    assert {p: (a.tax_area, a.code) for p, a in js.tax_map.items()} == {
        **{p: (jde_company.TAX_AREA, "V") for p in ("AB", "BC", "MB", "SK", "QC", "NT", "NU", "YT")},
        **{p: (f"{p}HST", "V") for p in ("ON", "NB", "NL", "NS", "PE")},
        "OUTSIDE_CANADA": ("", ""),
    }
    assert jde_settings.settings_problems(js) == []
    # What a setup already saved (the company, a supplier's number) is kept.
    saved = jde_settings.JdeSettings(
        company="00042", an8_overrides={"acme": "77"}, amount_mode=jde_settings.AMOUNT_GROSS
    )
    kept = jde_prototype.prototype_settings(saved)
    assert (kept.company, kept.an8_overrides, kept.amount_mode) == ("00042", {"acme": "77"}, jde_settings.AMOUNT_TAX)
    assert saved.amount_mode == jde_settings.AMOUNT_GROSS  # not changed in place
    assert jde_settings.JdeSettings.from_json(js.to_json()) == js


def test_a_recovered_tax_on_the_pay_item_needs_no_sales_tax_account(tmp_path):
    """B-2259: a new laptop keys the recovered tax on the voucher's pay item (code V: JD Edwards makes the GST line
    itself), so no sales-tax GL account of ours is posted to and a missing one is not a finding. Where the account
    is posted to it is asked for as before: the Excel/CSV export, the tax coded to G/L lines, a code that recovers
    nothing."""
    from ap_coder.ai.pipeline import finalise_coding
    from ap_coder.bench import jde_exports
    from ap_coder.core.config import Settings
    from ap_coder.ops import chart_cmd
    from ap_coder.web.core import first_run
    from tests.erp.test_jde_gl_mode import doc, line, tl

    store = first_run(tmp_path / "private" / "ap_coder.db", server=False)
    books = tmp_path / "books"
    jde_exports.write_all(books)
    chart_cmd.import_files(store, books / jde_exports.CHART_FILE, books / jde_exports.UNITS_FILE)
    pairs = store.account_pairs()
    unit = pairs.business_units()[0]
    code = next(c for c in pairs.accounts_of(unit) if pairs.offered(unit, c))
    alberta = doc([line(1, 240.0, code, ["GST"], cc=unit)], [tl("GST", "", 0.05, 240.0, 12.0)], prov="AB")
    quebec = doc([line(1, 100.0, code, ["GST", "QST"], cc=unit)],
                 [tl("GST", "", 0.05, 100.0, 5.0), tl("QST", "QC", 0.09975, 100.0, 9.98)], prov="QC",
                 qst_registration_number="1234567890TQ0001")  # fmt: skip

    def said(d):
        report = finalise_coding(InvoiceCoding.model_validate(d), store.reference_data(), Settings(), store=store)[1]
        return [i.message for i in report.issues if i.code == "TAX_GL_UNMAPPED"]

    assert not store.tax_treatments().get("GST") or not store.tax_treatments()["GST"].gl_code  # none set yet
    assert said(alberta) == [] and said(quebec) == []  # the QST is in the expense line, the GST on the pay item
    saved = jde_settings.load(store)
    saved.tax_map["AB"] = jde_settings.TaxArea("", "E")  # exempt in JD Edwards: the GST is a G/L line of ours
    jde_settings.save(store, saved)
    assert said(alberta) == ["no GL account is set for GST: set it in GL accounts & tax"] and said(quebec) == []
    jde_settings.save(store, jde_settings.JdeSettings())  # the tax coded to G/L lines
    assert len(said(alberta)) == len(said(quebec)) == 1
    jde_prototype.use_prototype(store)
    jde_settings.show(store, False)  # the Excel/CSV export
    assert len(said(alberta)) == len(said(quebec)) == 1


def test_a_line_with_a_remark_of_its_own_is_sent_with_it_fitted_to_30_characters():
    """D-213: ``jde_remarks.line_remark`` is the line's remark when it has one (the whole words that fit JD Edwards' 30
    characters), else its description cut at 30 as before; the pay item's remark is the first line's."""
    assert (
        jde_remarks.line_remark({"description": "Courier", "remark": "KT May.parcels 3-1-204"})
        == "KT May.parcels 3-1-204"
    )
    assert jde_remarks.line_remark({"description": "Courier", "remark": " "}) == "Courier"
    assert (
        jde_remarks.line_remark({"remark": "coffee supplies for the 3rd floor kitchen"})
        == "coffee supplies for the 3rd"
    )
    inv = _invoice(ON)
    final = inv["final_output"]
    described = [" ".join(li["description"].split())[:30].rstrip() for li in final["line_items"]]
    final["line_items"][0].update(remark="May janitorial 410 Elm", remark_from="supplier")
    expected = ["May janitorial 410 Elm", *described[1:]]
    assert jde_remarks.voucher_remark(inv) == "May janitorial 410 Elm"

    files = _files([inv])  # the references as before: the remark is the line's explanation
    lines = [r for r in files[jde_zfiles.DIST_FILE]]
    assert [r["VNEXA"] for r in lines][: len(expected)] == expected
    assert files[jde_zfiles.HEADER_FILE][0]["VLRMK"] == "AP Coder #1"
    typed = _files(
        [inv], jde_settings.JdeSettings(amount_mode=jde_settings.AMOUNT_GROSS, remarks=jde_settings.REMARKS_TYPED)
    )
    assert [r["VNEXR"] for r in typed[jde_zfiles.DIST_FILE]][: len(expected)] == expected
    assert typed[jde_zfiles.HEADER_FILE][0]["VLRMK"] == "May janitorial 410 Elm"
