"""JD Edwards' vendor information list (ap_coder/coding/vendor_info.py, D-219): the made-up company's list read as
it comes, its tax IDs kept as written and compared only when they are numbers, and the list and the supplier master
meeting on the supplier number whichever is loaded first."""

from __future__ import annotations

import io
import time

import openpyxl
import pytest

from ap_coder.bench import jde_exports as made
from ap_coder.bench.jde_company import build
from ap_coder.coding import vendor_info
from ap_coder.coding.vendors import master_columns, master_rows
from ap_coder.core.keys import same_tax_number
from ap_coder.web.files import read_sheet


@pytest.fixture(scope="module")
def truth():
    return build(1, "small", 2024)


@pytest.fixture(scope="module")
def listed(truth):
    return vendor_info.read(made.vendor_info_xlsx(truth), made.VENDOR_INFO_FILE)


def _load_master(store, truth):
    sheet, _ = read_sheet(made.SUPPLIERS_FILE, made.suppliers_xlsx(truth))
    return store.import_vendor_master(master_rows(sheet.to_dict("records"), master_columns(list(sheet.columns))),
                                      by_erp_id=True)  # fmt: skip


def _load_info(store, listed):
    return store.import_vendor_info(vendor_info.master_rows(listed["rows"]), listed["rows"], actor="AP")


def test_the_list_is_told_by_its_column_names_and_the_supplier_master_is_not():
    assert vendor_info.is_vendor_info(made.VENDOR_INFO_HEADER)
    assert not vendor_info.is_vendor_info(made.SUPPLIERS_HEADER)
    where = vendor_info.positions(made.VENDOR_INFO_HEADER)
    first = made.VENDOR_INFO_HEADER.index("Business Unit")
    assert (where["business_unit"], where["business_unit_name"]) == (first, first + 1)  # two columns of one name
    assert vendor_info.read(made.suppliers_xlsx(build(1, "small", 2024)), "grid.xlsx") is None
    assert vendor_info.read(b"not a workbook", "grid.xlsx") is None  # (the caller's own reader says why)


def test_every_supplier_is_read_and_the_totals_row_is_not_one(truth, listed):
    rows = {row["erp_id"]: row for row in listed["rows"]}
    assert len(rows) == len(truth.suppliers) + len(made.OTHER_SUPPLIERS)
    assert listed["left"] == {"totals": 1}
    assert set(rows) >= {str(s.number) for s in truth.suppliers}
    assert not listed["missing"] and ("Tax ID", "Tax ID") in listed["found"]
    one = rows["900001"]  # a name stored with a space before it, a business number written with spaces
    assert one["vendor_name"] == "HARBOURVIEW MARINE SUPPLY LTD" and one["tax_id"] == "70412 9930 RT0001"
    assert one["business_unit"] == made.OTHER_UNIT[0] and one["business_unit_name"] == made.OTHER_UNIT[1]
    assert (one["city"], one["state"], one["country"], one["postal_code"]) == ("Halifax", "NS", "CA", "B3J 1P3")
    assert {row["currency"] for row in rows.values()} == {"CAD", "USD", "AUD"}


@pytest.mark.parametrize(("written", "country", "compared", "gst"), [
    ("123456782RT0001", "CA", "123456782rt0001", "123456782RT0001"),
    ("12345 6782 RT0001", "CA", "123456782rt0001", "12345 6782 RT0001"),  # as written
    ("123456782 RC0001", "CA", "123456782rc0001", "123456782"),  # the same business' other account
    ("R123456782", "CA", "r123456782", "123456782"),
    (" 123456782", "", "123456782", "123456782"),
    ("123456782", "US", "123456782", ""),  # nine digits of a supplier abroad: an EIN
    ("12-3456782", "CA", "123456782", ""),
    ("123-45-6782", "CA", "123456782", ""),  # a person's number
    ("51 824 753 556", "AU", "51824753556", ""),
    ("GB 318 4402 71", "GB", "gb318440271", ""),
    ("CHARITABLE", "CA", "", ""),
    ("GOVERNMENT", "CA", "", ""),
    ("N/A", "CA", "", ""),
    ("", "CA", "", ""),
])  # fmt: skip
def test_a_tax_id_is_kept_as_written_and_only_a_business_number_is_a_gst_number(written, country, compared, gst):
    assert vendor_info.compared(written) == compared
    assert vendor_info.gst_number(written, country) == gst
    if gst:  # matched with or without its program account
        assert same_tax_number(gst, "123456782 RT0001") and same_tax_number(gst, "123456782")


def test_the_two_lists_meet_on_the_supplier_number_whichever_comes_first(tmp_path, truth, listed):
    from ap_coder.core.store import Store

    held = {str(s.number) for s in truth.suppliers if s.hold_payment in ("Y", "1", "2")}
    assert held
    seen = []
    for order in ("master first", "list first"):
        store = Store(tmp_path / order / "ap_coder.db")
        for load in (_load_master, _load_info) if order == "master first" else (_load_info, _load_master):
            load(store, truth if load is _load_master else listed)
        vendors = {v["erp_id"]: v for v in store.master_vendors()}
        assert len(vendors) == len(store.master_vendors()) == len(listed["rows"])  # one vendor a number
        assert {number for number, v in vendors.items() if v["erp_hold"]} == held  # the holds: the master's
        for row in listed["rows"]:  # the tax ID, terms and province: the list's
            vendor, info = vendors[row["erp_id"]], store.vendor_info(row["erp_id"])
            assert vendor["expected_gst"] == row["gst"] and vendor["terms"] == row["terms"]
            assert (info["tax_id"], info["tax_id_norm"], info["email"]) == (row["tax_id"], row["tax_id_norm"],
                                                                           row["email"])  # fmt: skip
        seen.append({number: (v["vendor_key"], v["expected_gst"], v["erp_hold"], v["status"])
                     for number, v in vendors.items()})  # fmt: skip
    assert seen[0] == seen[1]


def test_loading_the_list_again_changes_nothing_and_a_word_is_never_a_number(store, truth, listed):
    first = _load_info(store, listed)
    assert first["added"] == len(listed["rows"]) and first["updated"] == 0
    again = _load_info(store, listed)
    assert again["added"] == 0 and store.vendor_info_count() == len(listed["rows"])
    words = [row for row in listed["rows"] if row["tax_id"] in ("CHARITABLE", "GOVERNMENT", "N/A")]
    assert len(words) >= 4
    for row in words:
        info = store.vendor_info(row["erp_id"])
        assert info["tax_id"] == row["tax_id"] and info["tax_id_norm"] == ""
        assert next(v for v in store.master_vendors() if v["erp_id"] == row["erp_id"])["expected_gst"] == ""
    assert store.master_by_tax_number("CHARITABLE") == []
    retired = [v for v in store.master_vendors() if v["erp_id"] in ("900008", "900015", "900022")]
    assert [v["status"] for v in retired] == ["on_hold"] * 3  # (DNU), (DO NOT USE), - DO NOT USE: as the master's
    event = store.events(actions=["vendors_imported"], limit=1)[0]
    assert event["detail"]["list"] == "vendor information" and event["actor"] == "AP"


def test_a_business_number_finds_its_one_supplier_with_or_without_its_account(store, listed):
    _load_info(store, listed)
    assert [v["erp_id"] for v in store.master_by_tax_number("704129930")] == ["900001"]
    assert [v["erp_id"] for v in store.master_by_tax_number("70412 9930 RT 0001")] == ["900001"]
    assert [v["erp_id"] for v in store.master_by_tax_number("881042267RT0001")] == ["900036"]  # listed with RC0001
    assert store.master_by_tax_number("814402719") == []  # a US supplier's number is no GST/HST number


def test_the_summary_says_what_the_list_holds_before_anything_is_kept(truth, listed):
    known = {str(s.number) for s in truth.suppliers[:10]}
    said = vendor_info.said(listed, known, 3)
    assert said[0].startswith(f"{len(listed['rows'])} suppliers to load: 10 already in the supplier list")
    assert "Left out: 1 row of totals at the end." in said
    assert any(line.startswith("Tax ID: ") and "CHARITABLE" in line for line in said)
    assert any(line.startswith("Currency: CAD ") for line in said)
    assert any("3 names carry a retired supplier's marker" in line for line in said)


@pytest.mark.slow
def test_twenty_thousand_suppliers_are_read_and_loaded_in_seconds(store, truth):
    """The real list has about 20,000 suppliers of every company: read once at its upload (about 4 s), then loaded
    (about 1 s)."""
    import random

    rng = random.Random(7)
    rows = []
    for n in range(20_000):
        eight = f"7{rng.randrange(10**6, 10**7)}"
        tax = (eight + made._check_digit(eight) + "RT0001", "", "CHARITABLE", f"{eight[:2]}-{eight[2:]}1")[n % 4]
        rows.append(made._vendor_row(truth, rng, 100_000 + n, f"SUPPLIER {n} OF MANY LTD", [f"{n} Main St"],
                                     ("Calgary", "AB", "CA", "T2P 1J9"), tax, "CAD", made.OTHER_UNIT))  # fmt: skip
    totals = [None] * 15 + [20_000] + [None] * 6
    book = made._workbook(made.VENDOR_INFO_HEADER, [*rows, totals])
    started = time.monotonic()
    read = vendor_info.read(book, "list.xlsx")
    read_in = time.monotonic() - started
    assert len(read["rows"]) == 20_000 and read["left"] == {"totals": 1}
    started = time.monotonic()
    result = _load_info(store, read)
    loaded_in = time.monotonic() - started
    assert result["added"] == 20_000 and store.vendor_info_count() == 20_000
    assert read_in < 30 and loaded_in < 15, (read_in, loaded_in)  # (a slow shared runner takes several times longer)


def test_the_made_up_list_has_the_layout_of_the_export(truth):
    sheet = openpyxl.load_workbook(io.BytesIO(made.vendor_info_xlsx(truth)))["Sheet1"]
    rows = list(sheet.iter_rows())
    assert [cell.value for cell in rows[0]] == made.VENDOR_INFO_HEADER and len(made.VENDOR_INFO_HEADER) == 22
    assert made.VENDOR_INFO_HEADER.count("Business Unit") == 2
    number, count, date = (made.VENDOR_INFO_HEADER.index(name) for name in (
        "Internal Supplier Number", "Total Payment Count", "Check/ Itm Date"))  # fmt: skip
    for row in rows[1:-1]:
        assert isinstance(row[number].value, int) and isinstance(row[count].value, int)
        assert row[date].number_format == "mm-dd-yy" and row[date].is_date
        for at, cell in enumerate(row):  # every other cell: text, or truly empty
            if at not in (number, count, date) and cell.value is not None:
                assert isinstance(cell.value, str) and cell.number_format == "@"
    assert [cell.value is not None for cell in rows[-1]] == [at == count for at in range(22)]  # the totals row
    names = [row[1].value for row in rows[1:-1]]
    assert names == sorted(names) and names[0].startswith(" ")  # by name as stored: a space first
    assert any(name.startswith("(DNU)") for name in names) and any("DO NOT USE" in name for name in names)
    assert any(" O/A " in name for name in names) and any(name.endswith("     00417") for name in names)


def test_a_tax_id_changed_by_hand_is_kept_when_either_supplier_file_is_loaded_again(store, truth, listed):
    """D-221's promise holds for both exports: the vendor information list's load is the vendor master's import
    too, so the tax ID an Admin corrected by hand is put back after it, as after the supplier master's grid."""
    _load_master(store, truth)
    _load_info(store, listed)
    vendor = next(v for v in store.master_vendors() if v["erp_id"] == "900001")
    assert vendor["expected_gst"] == "70412 9930 RT0001"
    store.edit_supplier(vendor["vendor_key"], {"expected_gst": "881042267RT0001"}, actor="Pat")
    again = _load_info(store, listed)
    assert again["by_hand"] == ["900001 HARBOURVIEW MARINE SUPPLY LTD"]
    assert store.get_vendor(vendor["vendor_key"])["expected_gst"] == "881042267RT0001"
    _load_master(store, truth)
    assert store.get_vendor(vendor["vendor_key"])["expected_gst"] == "881042267RT0001"
    assert store.vendor_info("900001")["tax_id"] == "70412 9930 RT0001"  # what the list says, as written


def test_what_the_list_gives_is_seen_with_the_suppliers_loaded_under_readable_headings(store, listed):
    _load_info(store, listed)
    labels = {column.key: column.label for column in store.loaded_columns("suppliers")}
    assert labels["tax_id"] == "Tax ID (as written)" and labels["expected_gst"] == "GST/HST number"
    assert (labels["currency"], labels["terms"], labels["address1"], labels["city"], labels["state"]) == (
        "Currency", "Payment terms", "Address line 1", "City", "Province or state")  # fmt: skip
    assert (labels["country"], labels["postal_code"], labels["email"]) == ("Country", "Postal code", "E-mail")
    assert not any("bank" in key or "bank" in label.lower() for key, label in labels.items())
    page = store.loaded_page("suppliers", q="Halifax")  # searched by what the list gives too
    assert page["count"] == 1 and page["total"] == len(listed["rows"])
    (row,) = page["rows"]
    assert (row["erp_id"], row["tax_id"], row["currency"], row["postal_code"]) == (
        "900001", "70412 9930 RT0001", "CAD", "B3J 1P3")  # fmt: skip
    word = store.loaded_page("suppliers", q="CHARITABLE")["rows"]
    assert {r["tax_id"] for r in word} == {"CHARITABLE"} and {r["expected_gst"] for r in word} == {""}


def test_the_two_business_unit_columns_are_told_apart_by_what_they_hold_not_their_order(truth, listed):
    """B-2409: with the number's and the name's columns the other way round, the name ("*** DO NOT USE ***", "Head
    Office") was kept as the supplier's business unit."""
    book = openpyxl.load_workbook(io.BytesIO(made.vendor_info_xlsx(truth)))
    rows = [list(row) for row in book.active.iter_rows(values_only=True)]
    first = rows[0].index("Business Unit")
    for row in rows:
        row[first], row[first + 1] = row[first + 1], row[first]
    swapped = openpyxl.Workbook()
    for row in rows:
        swapped.active.append(row)
    out = io.BytesIO()
    swapped.save(out)
    read = vendor_info.read(out.getvalue(), "info.xlsx")
    by_number = {row["erp_id"]: row for row in listed["rows"]}
    assert [(r["business_unit"], r["business_unit_name"]) for r in read["rows"]] == [
        (by_number[r["erp_id"]]["business_unit"], by_number[r["erp_id"]]["business_unit_name"]) for r in read["rows"]
    ]
    assert read["rows"][1]["business_unit"] == made.OTHER_UNIT[0]
