"""JD Edwards files (ap_coder/web/pages/jde_files.py): the made-up company's five exports loaded through the page,
each summary shown and the data really kept; a wrong file in a box answers with the importer's plain words."""

from __future__ import annotations

import csv
import io
import random
import re

import openpyxl
import pytest
from markupsafe import escape

from ap_coder.bench import jde_exports as made
from ap_coder.core.reference_data import reads_like_meals
from ap_coder.erp import import_specs, jde_settings
from ap_coder.ops import chart_cmd, journal_cmd
from ap_coder.web import files, roles
from ap_coder.web.app import create_app
from ap_coder.web.core import SHOW_JDE
from ap_coder.web.pages import jde_files
from tests import chart_files as cf
from tests.webclient import PAGE, follow, web_settings

ROUTES = ["/jde-files/chart/upload", "/jde-files/chart/load", "/jde-files/units/upload", "/jde-files/units/load",
          "/jde-files/chart/settings", "/jde-files/suppliers/upload",
          "/jde-files/suppliers/load", "/jde-files/journal/upload", "/jde-files/journal/load",
          "/jde-files/journal/forget", "/jde-files/vouchers/upload", "/jde-files/vouchers/load",
          "/jde-files/meals", "/jde-files/company"]  # fmt: skip


@pytest.fixture(scope="module")
def exports(tmp_path_factory):
    folder = tmp_path_factory.mktemp("jde")
    made.write_all(folder, size="small")
    return folder


def _send(client, path: str, folder, name: str, as_name: str = ""):
    return client.post(path, files={"file": (as_name or name, (folder / name).read_bytes())}, headers=PAGE)


def _token(answer) -> str:
    assert answer.status_code == 200, answer.text
    return re.search(r"(?:chart|units|suppliers|journal|vouchers)=([^&#]+)", answer.json()["redirect"]).group(1)


def _accounts(store) -> int:
    return len([row for row in store.chart_accounts() if not row.is_header])


def _upload_and_load(client, card: str, name: str, data: bytes):
    """A chart or business unit file sent, shown, then loaded: the page after the load."""
    token = _token(client.post(f"/jde-files/{card}/upload", files={"file": (name, data)}, headers=PAGE))
    return follow(client, client.post(f"/jde-files/{card}/load", data={"token": token}, headers=PAGE))


def _load_chart(client, exports) -> None:
    for card, name in (("chart", made.CHART_FILE), ("units", made.UNITS_FILE)):
        _upload_and_load(client, card, name, (exports / name).read_bytes())


def test_the_page_starts_with_nothing_loaded_and_says_the_order(client):
    page = client.get("/jde-files").text
    assert page.count("Not yet") == 10 and "Loaded</span>" not in page  # the checklist's five lines, and each card
    assert str(escape(jde_files.ORDER)) in page
    for card in ("chart", "units", "suppliers", "journal", "vouchers"):
        assert f'<section class="card stack" id="{card}">' in page and f'href="#{card}"' in page
    assert page.count('type="file"') == 5


def test_the_chart_and_the_business_units_load_and_say_what_was_read(client, store, exports):
    page = _upload_and_load(client, "chart", made.CHART_FILE, (exports / made.CHART_FILE).read_bytes())
    said = page.split('id="chart-said"', 1)[1].split("</div></div>", 1)[0]
    assert "Chart of accounts" in said and "Accounts a line may be coded to" in said
    assert _accounts(store) > 0 and f"{_accounts(store):,} accounts</b>" in page and "last loaded" in page
    assert 'id="chart-tally"' in page and "A newer export replaces the chart whole" in page
    page = _upload_and_load(client, "units", made.UNITS_FILE, (exports / made.UNITS_FILE).read_bytes())
    assert "Business units" in page.split('id="units-said"', 1)[1].split("</div></div>", 1)[0]
    assert store.business_units() and f"{len(store.business_units()):,} business units</b>" in page
    assert page.count("Not yet") == 6  # three files still to load


def test_the_charts_settings_are_saved_from_the_page(client, store, exports):
    _load_chart(client, exports)
    home = store.business_units()[0].code
    form = {**store.chart_settings().saved(), "never_offered": "*.7701", "home_business_unit": home}
    page = follow(client, client.post("/jde-files/chart/settings", data=form, headers=PAGE))
    assert store.chart_settings().never_offered == ("*.7701",) and store.chart_settings().home_business_unit == home
    assert "settings were saved." in page and 'value="*.7701"' in page
    refused = client.post("/jde-files/chart/settings", data={**form, "home_business_unit": "NOPE"}, headers=PAGE)
    assert refused.status_code == 400 and "There is no business unit NOPE" in refused.text
    assert store.chart_settings().home_business_unit == home


@pytest.mark.parametrize("column, field", [("P E", "posting edit code"), ("Subsidiary", "subsidiary")])
def test_a_chart_without_a_column_it_needs_is_shown_and_never_loaded_and_one_loaded_so_elsewhere_says_so(
        column, field, client, store, tmp_path):  # fmt: skip
    """A grid exported without its posting edit column loaded with every account postable, one without its
    Subsidiary column with most accounts left out, and neither with a word about the column (B-2271, B-2272). The
    page has no "Load it anyway" (D-234): the file is shown, the column named, and nothing loads it; the command
    line's --anyway still can, and the page then says so every time."""
    grid = cf.write_chart(tmp_path / "grid.xlsx", without=(column,)).read_bytes()
    page = client.get("/jde-files").text
    assert 'name="anyway"' not in page.split('id="journal"', 1)[0] and "Load it anyway" not in page
    token = _token(client.post("/jde-files/chart/upload", files={"file": ("grid.xlsx", grid)}, headers=PAGE))
    summary = client.get(f"/jde-files?chart={token}").text.split('id="chart-summary"', 1)[1].split("</section>")[0]
    why = f"Not loaded: this file has no “{column}” ({field}) column, which JD Edwards&#39; account master export"
    assert "grid.xlsx" in summary and why in summary and f"missing: “{column}”" in summary
    assert "It looks like" not in summary  # (a chart missing a column, not the business units)
    assert "/jde-files/chart/load" in summary and "accounts</button>" not in summary  # Cancel only
    refused = client.post("/jde-files/chart/load", data={"token": token}, headers=PAGE)
    assert refused.status_code == 400 and str(escape(refused.json()["error"])) in summary
    assert store.chart_accounts() == [] and "Loaded</span>" not in client.get("/jde-files").text
    chart_cmd.import_files(store, chart_source=grid, anyway=True)  # (the command line's --anyway)
    page = client.get("/jde-files").text
    assert "Loaded without" in page.split('id="chart-tally"', 1)[1].split("</ul>", 1)[0]
    assert "Loaded without a column it needs" in page.split('id="checklist"', 1)[1].split("</section>", 1)[0]
    whole = cf.write_chart(tmp_path / "chart.xlsx").read_bytes()
    page = _upload_and_load(client, "chart", "chart.xlsx", whole)
    assert "Loaded without" not in page and store.chart_without() == ()


def _meals(store) -> set[str]:
    return {row["code"] for row in store.list_accounts("gl_accounts") if row["meals"]}


def test_the_meals_accounts_are_named_and_ticked_with_one_click_and_a_newer_chart_keeps_them(client, store, exports):
    """With a chart loaded no account was a meals account and nothing said to tick one, so a lunch coded to "Meals -
    Travel" put the whole GST on the pay item (B-2277)."""
    assert 'id="meals-accounts"' not in client.get("/jde-files").text  # (no chart: no row)
    _load_chart(client, exports)
    names = {row["code"]: row["description"] for row in store.list_accounts("gl_accounts")}
    like = sorted(code for code, name in names.items() if reads_like_meals(name))
    assert {names[code] for code in like} >= {"Meals - Travel", "Working Lunches", "Client Hospitality",
                                              "Catering - Meetings"}  # fmt: skip
    others = ("Airfare", "Hotels & Lodging", "Meeting Room Rental", "Recovery-Meals & Hospi", "Meals billed to clients")
    assert not any(reads_like_meals(name) for name in others)
    row = client.get("/jde-files").text.split('id="meals-accounts"', 1)[1].split("</dd>", 1)[0]
    assert "None is ticked" in row and all(f"{code} {escape(names[code])}" in row for code in like)
    assert f"Mark these {len(like)} as meals accounts (half their GST/HST is claimable)" in row
    assert 'href="/accounts?tab=gl"' in row and _meals(store) == set()  # nothing is ticked without the click
    page = follow(client, client.post("/jde-files/meals", data={"code": like}, headers=PAGE))
    assert _meals(store) == set(like) and store.reference_data().tax.meals_gl_codes == frozenset(like)
    assert store.events(actions=["accounts_edited"])[0]["detail"]["codes"] == like
    row = page.split('id="meals-accounts"', 1)[1].split("</dd>", 1)[0]
    assert f"{len(like)} accounts ticked" in row and "Mark these" not in row and 'href="/accounts?tab=gl"' in row
    _load_chart(client, exports)  # a newer export of the chart keeps the ticks (B-2090)
    assert _meals(store) == set(like)
    refused = client.post("/jde-files/meals", data={"code": ["NOPE"]}, headers=PAGE)
    assert refused.status_code == 400 and _meals(store) == set(like)


def test_the_one_company_of_the_business_units_is_offered_as_jd_edwards_company(client, store, exports, tmp_path):
    """JD Edwards' company number stayed 00001 until set by hand in Settings, and the keying sheet printed it
    (B-2278): offered with one click when every business unit loaded belongs to one company, never to several."""
    assert jde_settings.load(store).company == "00001" and "/jde-files/company" not in client.get("/jde-files").text
    several = [(code, name, "00002" if n else company, kind, status)
               for n, (code, name, company, kind, status) in enumerate(cf.UNITS)]  # fmt: skip
    units = cf.write_business_units(tmp_path / "bu.xlsx", several).read_bytes()
    page = _upload_and_load(client, "units", "bu.xlsx", units)
    assert "/jde-files/company" not in page
    refused = client.post("/jde-files/company", headers=PAGE)
    assert refused.status_code == 400 and jde_settings.load(store).company == "00001"
    _load_chart(client, exports)
    (company,) = {unit.company for unit in store.business_units()}
    page = client.get("/jde-files").text
    assert company != "00001" and f"Use company {company} (the one your business units belong to)" in page
    assert jde_settings.load(store).company == "00001"  # not without the click
    page = follow(client, client.post("/jde-files/company", headers=PAGE))
    assert jde_settings.load(store).company == company and "/jde-files/company" not in page
    assert f"company number is now {company}" in page.split('id="units-said"', 1)[1].split("</div></div>", 1)[0]


def test_the_suppliers_are_shown_before_they_are_loaded(client, store, exports):
    token = _token(_send(client, "/jde-files/suppliers/upload", exports, made.SUPPLIERS_FILE))
    page = client.get(f"/jde-files?suppliers={token}").text
    summary = page.split('id="suppliers-summary"', 1)[1].split("</section>", 1)[0]
    assert made.SUPPLIERS_FILE in summary and "nothing is loaded yet" in summary and "as</span> “Alpha Name”" in summary
    assert (
        re.search(r"Load [\d,]+ suppliers?</button>", summary) and "/vendors?file=" not in summary
    )  # (no picker for it: B-2406)
    assert store.master_vendors() == []
    page = follow(client, client.post("/jde-files/suppliers/load", data={"token": token}, headers=PAGE))
    count = len(store.master_vendors())
    assert count > 0 and f"Suppliers: {count:,} added, 0 updated." in page
    assert f"{count:,} suppliers</b>" in page and 'href="/vendors"' in page


def test_suppliers_without_their_number_column_are_refused_with_the_columns_name(client, store, exports):
    """A supplier master exported without "Address Number" said "40 added" with no supplier number read: the number
    is the supplier's identity, so the list is not loaded without it (B-2275)."""
    sheet = openpyxl.load_workbook(exports / made.SUPPLIERS_FILE).active
    sheet.delete_cols(made.SUPPLIERS_HEADER.index("Address Number") + 1)
    grid = io.BytesIO()
    sheet.parent.save(grid)
    answer = client.post("/jde-files/suppliers/upload", files={"file": ("grid.xlsx", grid.getvalue())}, headers=PAGE)
    token = _token(answer)
    summary = client.get(f"/jde-files?suppliers={token}").text.split('id="suppliers-summary"', 1)[1]
    summary = summary.split("</section>", 1)[0]
    assert "no “Address Number” (supplier number) column, which JD Edwards&#39; supplier master" in summary
    assert "suppliers</button>" not in summary and "Add the column to the grid in JD Edwards" in summary
    refused = client.post("/jde-files/suppliers/load", data={"token": token}, headers=PAGE)
    assert refused.status_code == 400 and refused.json()["error"].startswith("Not loaded: this file has no “Address")
    assert store.master_vendors() == []


def _summary(client, token: str) -> str:
    page = client.get(f"/jde-files?suppliers={token}").text
    return page.split('id="suppliers-summary"', 1)[1].split("</section>", 1)[0]


def test_the_vendor_information_list_is_told_by_its_columns_and_shown_before_it_is_loaded(client, store, exports):
    """The same box takes the second supplier export (D-219): its summary first, nothing kept; then its tax IDs,
    terms and addresses, each on the supplier of its number."""
    token = _token(_send(client, "/jde-files/suppliers/upload", exports, made.VENDOR_INFO_FILE))
    summary = _summary(client, token)
    assert made.VENDOR_INFO_FILE in summary and "nothing is loaded yet" in summary
    assert "55 suppliers to load: 0 already in the supplier list by their number" in summary
    assert "Left out: 1 row of totals at the end." in summary and "Tax ID: " in summary and "Currency: CAD" in summary
    assert '<b>Tax ID</b> <span class="muted">as</span> “Tax ID”' in summary
    assert '<b>Business unit</b> <span class="muted">as</span> “Business Unit”' in summary
    assert (
        re.search(r"Load [\d,]+ suppliers?</button>", summary) and "/vendors?file=" not in summary
    )  # its columns are not picked
    assert store.master_vendors() == [] and store.vendor_info_count() == 0
    page = follow(client, client.post("/jde-files/suppliers/load", data={"token": token}, headers=PAGE))
    assert "Suppliers: 55 added, 0 updated, from the vendor information list." in page
    assert len(store.master_vendors()) == store.vendor_info_count() == 55
    assert "The vendor information list is loaded for 55 suppliers" in page
    info = store.vendor_info("900071")
    assert info["tax_id"] == "#814402719" and info["country"] == "US" and info["currency"] == "USD"
    gone = client.post("/jde-files/suppliers/load", data={"token": token}, headers=PAGE)
    assert gone.status_code == 400 and gone.json()["error"] == jde_files.LOADED  # loaded once


def test_each_supplier_file_keeps_what_only_the_other_gives(client, store, exports):
    for name in (made.SUPPLIERS_FILE, made.VENDOR_INFO_FILE, made.SUPPLIERS_FILE):
        token = _token(_send(client, "/jde-files/suppliers/upload", exports, name))
        if name == made.VENDOR_INFO_FILE:  # the master's 40 are known by their number
            assert "55 suppliers to load: 40 already in the supplier list by their number" in _summary(client, token)
        page = follow(client, client.post("/jde-files/suppliers/load", data={"token": token}, headers=PAGE))
    assert "Suppliers: 0 added, 40 updated." in page
    vendors = store.master_vendors()
    assert len(vendors) == len({v["erp_id"] for v in vendors}) == 55
    assert sum(1 for v in vendors if v["erp_hold"]) > 0 and sum(1 for v in vendors if v["expected_gst"]) > 20
    assert store.vendor_info_count() == 55


def test_the_long_list_goes_to_the_reader_of_long_exports_and_the_supplier_master_keeps_its_limit(
        client, store, exports, monkeypatch):  # fmt: skip
    """20,000 suppliers' sheet unzips to more than the 20 MB a sheet of any other upload may: the list is read a row
    at a time, as the chart is; a damaged one is refused in the reader's plain words."""
    monkeypatch.setattr(files.triage, "sheet_unzips_too_big", lambda content: True)
    token = _token(_send(client, "/jde-files/suppliers/upload", exports, made.VENDOR_INFO_FILE))
    assert "55 suppliers to load" in _summary(client, token)
    refused = _send(client, "/jde-files/suppliers/upload", exports, made.SUPPLIERS_FILE)
    assert refused.status_code == 400 and refused.json()["error"] == files.TOO_BIG_UNZIPPED


def test_two_suppliers_of_one_name_keep_their_own_numbers_when_a_list_comes_in_another_order(client, store):
    """Loaded sorted by number, then again sorted by name: the vendor under the name took the other supplier's
    number, with the hold and everything AP had set on it, and a third vendor was made (B-2298)."""
    from tests.jde_exports import supplier, supplier_master

    rows = [supplier(410001, "PRAIRIE LIFT SERVICES LTD"), supplier(410077, "PRAIRIE LIFT SERVICES LTD"),
            supplier(410090, "QUARRY ROAD SIGNS INC")]  # fmt: skip
    for order in (rows, rows[::-1]):
        answer = client.post("/jde-files/suppliers/upload", files={"file": ("grid.xlsx", supplier_master(order))},
                             headers=PAGE)  # fmt: skip
        follow(client, client.post("/jde-files/suppliers/load", data={"token": _token(answer)}, headers=PAGE))
        vendors = {v["vendor_key"]: v["erp_id"] for v in store.master_vendors()}
        assert vendors == {"prairie lift services": "410001", "prairie lift services #410077": "410077",
                           "quarry road signs": "410090"}  # fmt: skip


def test_a_journal_saved_as_unicode_text_is_read_as_the_csv_is(client, store, exports):
    """Excel's "Unicode Text" (UTF-16 with its mark, tabs between the cells) said "No voucher could be read from this
    file" with no reason (B-2276)."""
    _load_chart(client, exports)
    saved = (exports / made.JOURNAL_FILE).read_bytes()
    text = io.StringIO()
    csv.writer(text, delimiter="\t", lineterminator="\r\n").writerows(csv.reader(io.StringIO(saved.decode("utf-8"))))
    for name, data in (("tabs.txt", text.getvalue().encode("utf-16")), ("commas.csv", saved.decode().encode("utf-16"))):
        seen = journal_cmd.look(store, data, name)
        assert seen.state == journal_cmd.READY, (name, seen.problem)
        assert seen.journal.vouchers == journal_cmd.look(store, saved, "saved.csv").journal.vouchers, name
    assert 'accept=".csv,.txt"' in client.get("/jde-files").text
    upload = client.post("/jde-files/journal/upload", files={"file": ("tabs.txt", data)}, headers=PAGE)
    page = follow(client, client.post("/jde-files/journal/load", data={"token": _token(upload)}, headers=PAGE))
    (loaded,) = store.journal_files()
    assert loaded["vouchers"] == len(seen.journal.vouchers) > 0 and "Loaded commas.csv" not in page


def test_the_journal_is_shown_then_loaded_and_can_be_forgotten(client, store, exports):
    _load_chart(client, exports)
    token = _token(_send(client, "/jde-files/journal/upload", exports, made.JOURNAL_FILE))
    page = client.get(f"/jde-files?journal={token}").text
    summary = page.split('id="journal-summary"', 1)[1].split("</section>", 1)[0]
    assert "JD Edwards&#39; Transaction Journal (R09321)" in summary and "Load this journal" in summary
    assert store.journal_files() == []
    page = follow(client, client.post("/jde-files/journal/load", data={"token": token}, headers=PAGE))
    (loaded,) = store.journal_files()
    assert loaded["vouchers"] > 0 and f"Loaded {made.JOURNAL_FILE}: " in page.split('id="journal-said"', 1)[1]
    assert "1 journal</b>" in page and "/jde-files/journal/forget" in page
    page = follow(client, client.post("/jde-files/journal/forget", data={"file_id": loaded["id"]}, headers=PAGE))
    assert store.journal_files() == [] and f"Forgot {made.JOURNAL_FILE}" in page


def test_the_voucher_list_is_shown_then_loaded(client, store, exports):
    token = _token(_send(client, "/jde-files/vouchers/upload", exports, made.HEADERS_FILE))
    page = client.get(f"/jde-files?vouchers={token}").text
    summary = page.split('id="vouchers-summary"', 1)[1].split("</section>", 1)[0]
    assert "JD Edwards&#39; voucher list: " in summary and re.search(r"Load [\d,]+ vouchers?</button>", summary)
    assert store.erp_register_count() == 0
    page = follow(client, client.post("/jde-files/vouchers/load", data={"token": token}, headers=PAGE))
    count = store.erp_register_count()
    assert count > 0 and f"{count:,} vouchers of JD Edwards known now." in page
    assert f"{count:,} vouchers</b>" in page and 'href="/exports#register"' in page


def test_a_wrong_file_in_a_box_is_refused_in_plain_words(client, store, exports):
    for card in ("chart", "units"):
        path = f"/jde-files/{card}/upload"
        token = _token(_send(client, path, exports, made.SUPPLIERS_FILE))  # shown, with the card it goes on
        summary = client.get(f"/jde-files?{card}={token}").text.split(f'id="{card}-summary"', 1)[1]
        assert "It looks like JD Edwards&#39; supplier master export: load it under 3. Suppliers." in summary, card
        wrong = client.post(f"/jde-files/{card}/load", data={"token": token}, headers=PAGE)
        assert wrong.status_code == 400 and wrong.json()["error"].startswith("Not loaded: this file has no "), card
        other = _send(client, path, exports, made.JOURNAL_FILE)  # (named with its card: B-2412)
        assert other.status_code == 400 and other.json()["error"] == import_specs.elsewhere("journal"), path
        other = client.post(path, files={"file": ("chart.csv", b"Business Unit,Object\n1,2\n")}, headers=PAGE)
        assert other.status_code == 400 and other.json()["error"] == jde_files.NOT_XLSX, path
        broken = client.post(path, files={"file": ("chart.xlsx", b"not a workbook")}, headers=PAGE)
        assert broken.status_code == 400 and "is not an Excel workbook" in broken.json()["error"], path
    token = _token(_send(client, "/jde-files/units/upload", exports, made.CHART_FILE))  # every column of the units
    summary = client.get(f"/jde-files?units={token}").text.split('id="units-summary"', 1)[1].split("</section>")[0]
    assert (
        "this file is JD Edwards&#39; account master export (the chart of accounts), not JD Edwards&#39; business "
        "unit master export: load it under 1. Chart of accounts."
        in summary
        and "Load these" not in summary
    )
    gone = client.post("/jde-files/chart/load", data={"token": "nothing"}, headers=PAGE)
    assert gone.status_code == 400 and gone.json()["error"] == jde_files.GONE
    assert store.chart_accounts() == [] and store.business_units() == []

    token = _token(_send(client, "/jde-files/suppliers/upload", exports, made.UNITS_FILE))
    page = client.get(f"/jde-files?suppliers={token}").text
    assert "It looks like JD Edwards&#39; business unit master export: load it under 2. Business units." in page
    wrong = client.post("/jde-files/suppliers/load", data={"token": token}, headers=PAGE)
    assert wrong.status_code == 400 and "load it under 2. Business units." in wrong.json()["error"]
    gone = client.post("/jde-files/suppliers/load", data={"token": "nothing"}, headers=PAGE)
    assert gone.status_code == 400 and gone.json()["error"] == files.GONE
    assert store.master_vendors() == []

    wrong = _send(client, "/jde-files/journal/upload", exports, made.SUPPLIERS_FILE)
    assert wrong.status_code == 400 and wrong.json()["error"] == journal_cmd.NOT_A_JOURNAL + (
        " It looks like JD Edwards' supplier master export: load it under 3. Suppliers."
    )
    gone = client.post("/jde-files/journal/load", data={"token": "nothing"}, headers=PAGE)
    assert gone.status_code == 400 and gone.json()["error"] == files.GONE  # (one message for it: B-2416)

    token = _token(_send(client, "/jde-files/vouchers/upload", exports, made.SUPPLIERS_FILE))
    page = client.get(f"/jde-files?vouchers={token}").text
    assert "voucher list (the supplier ledger inquiry" in page and "load it under 3. Suppliers." in page
    assert "vouchers</button>" not in page
    wrong = client.post("/jde-files/vouchers/load", data={"token": token}, headers=PAGE)
    assert wrong.status_code == 400 and "It looks like JD Edwards' supplier master" in wrong.json()["error"]
    other = client.post("/jde-files/vouchers/upload", files={"file": ("list.csv", b"Vendor,Invoice,Total\nA,1,2\n")},
                        headers=PAGE)  # fmt: skip
    page = client.get(f"/jde-files?vouchers={_token(other)}").text  # (no picker for it on Exports: B-2406)
    assert "this file has no “Document Number” (document number)" in page and "/exports?register=" not in page
    broken = client.post("/jde-files/vouchers/upload", files={"file": ("list.xlsx", b"not a workbook")}, headers=PAGE)
    assert broken.status_code == 400 and store.erp_register_count() == 0


def test_the_chart_is_bound_by_its_readers_own_limit_and_no_other_upload_is_loosened(client, store, exports,
                                                                                      monkeypatch):  # fmt: skip
    """A chart's sheet unzips to about 104 MB, over the 20 MB a sheet of any other upload may (``files.read_excel``):
    the chart and the business units go to their reader as they come; the other boxes keep the limit."""
    monkeypatch.setattr(files.triage, "sheet_unzips_too_big", lambda content: True)
    _load_chart(client, exports)
    assert _accounts(store) > 0 and store.business_units()
    for path, name in (("/jde-files/suppliers/upload", made.SUPPLIERS_FILE),
                       ("/jde-files/vouchers/upload", made.HEADERS_FILE)):  # fmt: skip
        refused = _send(client, path, exports, name)
        assert refused.status_code == 400 and refused.json()["error"] == files.TOO_BIG_UNZIPPED, path


def test_on_the_server_anyone_from_a_clerk_sees_the_page_and_only_an_admin_loads(store):
    app = create_app(web_settings(), store, start_reader=False, server=True)
    assert roles.can(app, "clerk", "GET", "/jde-files") and not roles.can(app, "readonly", "GET", "/jde-files")
    for path in ROUTES:
        assert roles.can(app, "admin", "POST", path), path
        assert not any(roles.can(app, role, "POST", path) for role in ("readonly", "clerk", "approver")), path


# --- Look first: each card's report, its columns and template, the file shown before it is loaded (D-234) -----------


def _shuffled(data: bytes, drop: str = "") -> bytes:
    """The export with its columns in another order (and without the column headed ``drop``)."""
    sheet = openpyxl.load_workbook(io.BytesIO(data)).active
    rows = [list(row) for row in sheet.iter_rows(values_only=True)]
    order = [i for i in range(len(rows[0])) if rows[0][i] != drop]
    random.Random(3).shuffle(order)
    book = openpyxl.Workbook()
    for row in rows:
        book.active.append([row[i] if i < len(row) else None for i in order])
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def test_each_card_says_its_report_its_columns_and_gives_a_blank_template(client):
    page = client.get("/jde-files").text
    for key in import_specs.JDE_REPORTS:
        spec = import_specs.SPECS[key]
        card = page.split(f'id="cols-{key}"', 1)[1].split("</details>", 1)[0]
        assert all(f'<span class="imp-chip need" title="{escape(c.label)}">{escape(c.header)}</span>' in card
                   for c in spec.required), key  # fmt: skip
        assert all(f">{escape(c.header)}</span>" in card or f">{escape(c.header)} (" in card for c in spec.optional), (
            key
        )
        assert str(escape(spec.source)) in page and f'href="/jde-files/template/{key}" download' in page, key
        answer = client.get(f"/jde-files/template/{key}")
        assert answer.status_code == 200 and "attachment" in answer.headers["content-disposition"], key
        if key == "journal":
            assert answer.headers["content-type"].startswith("text/csv") and b"Account Number" in answer.content
        else:
            header = next(openpyxl.load_workbook(io.BytesIO(answer.content)).active.iter_rows(values_only=True))
            assert list(header) == [c.header for c in spec.columns], key
    for other in ("purchase_orders", "jde_worksheet", "nothing"):
        assert client.get(f"/jde-files/template/{other}").status_code == 404, other


def test_the_chart_is_shown_first_its_columns_in_any_order_and_loaded_only_on_load(client, store, exports):
    whole = (exports / made.CHART_FILE).read_bytes()
    token = _token(client.post("/jde-files/chart/upload", files={"file": ("mixed.xlsx", _shuffled(whole))},
                               headers=PAGE))  # fmt: skip
    page = client.get(f"/jde-files?chart={token}").text
    summary = page.split('id="chart-summary"', 1)[1].split("</section>", 1)[0]
    rows = len(list(openpyxl.load_workbook(io.BytesIO(whole)).active.iter_rows())) - 1
    assert f"<b>mixed.xlsx</b> · {rows:,} rows · nothing is loaded yet." in summary
    assert "every column it needs is here." in summary and 'role="alert"' not in summary
    for header in ("Business Unit", "Object Account", "Subsidiary", "P E", "Account Description", "L D"):
        assert f'<span class="muted">as</span> “{header}”' in summary, header
    assert "104 other columns of the file are not used:" in summary
    offered = re.search(r"Load ([\d,]+) accounts</button>", summary)
    assert summary.count("<tr>") == 1 + 5 and offered
    assert store.chart_accounts() == []
    assert "Choose the file" not in page.split('id="chart"', 1)[1].split("</section>")[0]
    page = follow(client, client.post("/jde-files/chart/load", data={"token": token}, headers=PAGE))
    assert _accounts(store) > 0 and 'id="chart-said"' in page and 'id="chart-summary"' not in page
    assert offered.group(1) == f"{_accounts(store):,}"  # the accounts it loads, not its rows
    gone = client.post("/jde-files/chart/load", data={"token": token}, headers=PAGE)
    assert gone.status_code == 400 and gone.json()["error"] == jde_files.LOADED  # loaded once


def test_a_file_without_a_column_is_named_and_another_report_is_sent_to_its_card(client, store, exports):
    """No "load it anyway": a file is the report with every column it needs, or it is not loaded; dropped on the
    wrong card, it says which card it goes on."""
    whole = (exports / made.CHART_FILE).read_bytes()
    cut = _shuffled(whole, "Object Account")
    token = _token(client.post("/jde-files/chart/upload", files={"file": ("cut.xlsx", cut)}, headers=PAGE))
    summary = client.get(f"/jde-files?chart={token}").text.split('id="chart-summary"', 1)[1].split("</section>")[0]
    assert "Not loaded: this file has no “Object Account” (object account) column" in summary
    assert 'role="alert"' in summary and "missing: “Object Account”" in summary
    assert "accounts</button>" not in summary
    checklist = client.get(f"/jde-files?chart={token}").text.split('id="checklist"', 1)[1].split("</section>")[0]
    assert "The file chosen cannot be loaded" in checklist
    token = _token(_send(client, "/jde-files/chart/upload", exports, made.HEADERS_FILE))
    summary = client.get(f"/jde-files?chart={token}").text.split('id="chart-summary"', 1)[1].split("</section>")[0]
    assert (
        "It looks like JD Edwards&#39; voucher list (the supplier ledger inquiry&#39;s grid): load it under 5. "
        "Invoices already in JD Edwards."
    ) in summary
    refused = client.post("/jde-files/chart/load", data={"token": token}, headers=PAGE)
    assert refused.status_code == 400 and "load it under 5. Invoices already in JD Edwards." in refused.json()["error"]
    assert store.chart_accounts() == []


def test_the_supplier_master_and_the_voucher_list_show_their_columns_and_first_rows(client, exports):
    token = _token(_send(client, "/jde-files/suppliers/upload", exports, made.SUPPLIERS_FILE))
    summary = client.get(f"/jde-files?suppliers={token}").text.split('id="suppliers-summary"', 1)[1]
    assert "JD Edwards&#39; supplier master export: every column it needs is here." in summary
    assert "“Address Number”" in summary and "not in this file (optional)" in summary
    assert "Individual Tax ID" in summary.split("not used:", 1)[1].split("</p>", 1)[0]
    token = _token(_send(client, "/jde-files/vouchers/upload", exports, made.HEADERS_FILE))
    summary = client.get(f"/jde-files?vouchers={token}").text.split('id="vouchers-summary"', 1)[1]
    assert '<th scope="col">Gross Amount</th>' in summary and re.search(r"Load [\d,]+ vouchers?</button>", summary)


def test_in_a_browser_a_shuffled_chart_is_shown_then_loaded_and_one_without_a_column_is_refused(
    chromium, store, exports, tmp_path
):
    from tests.browser import browser_on

    whole = (exports / made.CHART_FILE).read_bytes()
    (tmp_path / "mixed.xlsx").write_bytes(_shuffled(whole))
    (tmp_path / "no_pe.xlsx").write_bytes(_shuffled(whole, "P E"))
    with browser_on(chromium, create_app(web_settings(), store, start_reader=False)) as (browser, base):
        errors: list[str] = []
        for width in (1366, 683, 390):
            page = browser.new_page(viewport={"width": width, "height": 800})
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(base + "/jde-files")
            box = page.locator("#chart input[type=file]")
            box.set_input_files(str(tmp_path / "no_pe.xlsx"))
            assert "no_pe.xlsx" in page.locator("#chart [data-imp-chosen]").inner_text()
            page.locator("#chart .imp-upload button[type=submit]").click()
            page.wait_for_selector("#chart-summary")
            verdict = page.locator("#chart-summary [role=alert]").inner_text()
            assert "this file has no “P E” (posting edit code) column" in verdict, width
            assert page.locator("#chart-summary button.primary").count() == 0
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), width
            page.locator("#chart-summary a", has_text="Choose another file").click()
            page.wait_for_selector("#chart input[type=file]", state="attached")
            page.locator("#chart input[type=file]").set_input_files(str(tmp_path / "mixed.xlsx"))
            page.locator("#chart .imp-upload button[type=submit]").click()
            page.wait_for_selector("#chart-summary")
            assert "every column it needs is here." in page.locator("#chart-summary").inner_text()
            assert page.locator("#chart-summary .imp-check li.ok").count() == 6
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), width
            if width == 390:
                assert store.chart_accounts() == []
                page.locator("#chart-summary button.primary").click()
                page.wait_for_selector("#chart-said")
                said = page.locator("#chart-said").inner_text()  # what was done, then the next file to load
                assert "accounts on" in said and "Next: 2. Business units" in said
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.close()
        assert errors == [] and _accounts(store) > 0


def test_a_column_name_a_report_has_twice_says_which_and_dates_show_as_dates(client, store, exports):
    """B-2426: the journal's columns read "Number … Number" and the vendor information list's "Business Unit,
    Business Unit", its preview "Document type as “Number”" and "Supplier number as “Number”"; the voucher list's
    first rows showed its dates as "2024-01-11 00:00:00", and the vendor information list labelled its address lines
    "Address2", "Address3"."""
    page = client.get("/jde-files").text
    journal = page.split('id="cols-journal"', 1)[1].split("</details>", 1)[0]
    assert ">Number (document type)</span>" in journal and ">Number (supplier number)</span>" in journal
    info = page.split('id="cols-vendor_info"', 1)[1].split("</details>", 1)[0]
    assert (
        ">Business Unit (the 1st of that name)</span>" in info and ">Business Unit (business unit name)</span>" in info
    )
    assert "Address2" not in info and "<b>Address line 2</b>" in info
    token = _token(_send(client, "/jde-files/journal/upload", exports, made.JOURNAL_FILE))
    summary = client.get(f"/jde-files?journal={token}").text.split('id="journal-summary"', 1)[1]
    assert (
        '<b>Document type</b> <span class="muted">as</span> “Number” <span class="muted">(the 1st of 2 by that name)'
        in summary
    )
    assert (
        '<b>Supplier number</b> <span class="muted">as</span> “Number” <span class="muted">(the 2nd of 2 by that name)'
        in summary
    )
    token = _token(_send(client, "/jde-files/vouchers/upload", exports, made.HEADERS_FILE))
    summary = client.get(f"/jde-files?vouchers={token}").text.split('id="vouchers-summary"', 1)[1]
    assert " 00:00:00" not in summary and re.search(r"<td>2024-\d\d-\d\d</td>", summary)


# --- The right report with every column it needs, or nothing (the pilot tester's rule) ------------------------------


def _grid(data: bytes) -> list[list]:
    return [list(row) for row in openpyxl.load_workbook(io.BytesIO(data)).active.iter_rows(values_only=True)]


def _book(*sheets: tuple[str, list[list]]) -> bytes:
    book = openpyxl.Workbook()
    book.remove(book.active)
    for title, rows in sheets:
        sheet = book.create_sheet(title)
        for row in rows:
            sheet.append(row)
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def _without(rows: list[list], header: str) -> list[list]:
    at = rows[0].index(header)
    return [[cell for i, cell in enumerate(row) if i != at] for row in rows]


def _look_of(client, card: str, name: str, data: bytes) -> tuple[str, str]:
    """(the token, the card's summary as the page shows it) of a file uploaded on ``card``."""
    token = _token(client.post(f"/jde-files/{card}/upload", files={"file": (name, data)}, headers=PAGE))
    page = client.get(f"/jde-files?{card}={token}").text
    return token, page.split(f'id="{card}-summary"', 1)[1].split("</section>", 1)[0]


def test_a_journal_without_its_remark_column_is_refused_naming_it_not_read_by_position(client, store, exports):
    """B-2407: with the Remark column removed the card said "The file has no header row", offered Load and read the
    file by the report's positions: supplier numbers became names, no remark was kept."""
    rows = list(csv.reader(io.StringIO((exports / made.JOURNAL_FILE).read_text("utf-8-sig"))))
    at = next(i for i, row in enumerate(rows) if "Remark" in [cell.strip() for cell in row])
    remark = [cell.strip() for cell in rows[at]].index("Remark")
    cut = [[cell for i, cell in enumerate(row) if i != remark] for row in rows]
    text = io.StringIO()
    csv.writer(text, lineterminator="\r\n").writerows(cut)
    token = _token(_send_bytes(client, "/jde-files/journal/upload", "journal.csv", text.getvalue().encode()))
    summary = client.get(f"/jde-files?journal={token}").text.split('id="journal-summary"', 1)[1]
    summary = summary.split("</section>", 1)[0]
    assert "no header row" not in summary and "“Remark”" in summary and 'role="alert"' in summary
    refused = client.post("/jde-files/journal/load", data={"token": token}, headers=PAGE)
    assert refused.status_code == 400 and "no “Remark” column" in refused.json()["error"]
    assert store.journal_files() == []


def _send_bytes(client, path: str, name: str, data: bytes):
    return client.post(path, files={"file": (name, data)}, headers=PAGE)


def test_a_needed_column_named_twice_is_refused_naming_it(client, store, exports):
    """B-2408: a junk column named as a needed one, before it, was the one read: the chart was replaced by its 179
    rows on business unit "ZZZ", and every supplier was named "ZZZ"."""
    rows = _grid((exports / made.CHART_FILE).read_bytes())
    doubled = [["Business Unit" if i == 0 else "ZZZ", *row] for i, row in enumerate(rows)]
    token, summary = _look_of(client, "chart", "chart.xlsx", _book(("Sheet1", doubled)))
    assert "more than one column named “Business Unit”" in summary and 'role="alert"' in summary
    refused = client.post("/jde-files/chart/load", data={"token": token}, headers=PAGE)
    assert refused.status_code == 400 and "“Business Unit”" in refused.json()["error"]
    assert store.chart_accounts() == []
    rows = _grid((exports / made.SUPPLIERS_FILE).read_bytes())
    doubled = [["Alpha Name" if i == 0 else "ZZZ", *row] for i, row in enumerate(rows)]
    token, summary = _look_of(client, "suppliers", "suppliers.xlsx", _book(("Sheet1", doubled)))
    assert "more than one column named “Alpha Name”" in summary
    refused = client.post("/jde-files/suppliers/load", data={"token": token}, headers=PAGE)
    assert refused.status_code == 400 and "“Alpha Name”" in refused.json()["error"]
    assert store.master_vendors() == []
    token, summary = _look_of(
        client, "suppliers", made.VENDOR_INFO_FILE, (exports / made.VENDOR_INFO_FILE).read_bytes()
    )
    assert 'role="alert"' not in summary  # (the list's own two "Business Unit" columns: read both)


def test_the_vendor_information_list_without_a_column_is_judged_as_itself(client, store, exports):
    """B-2410: the vendor information list without its Tax ID column was judged as the supplier master: the message
    named Alpha Name and Address Number, columns the list never has."""
    rows = _without(_grid((exports / made.VENDOR_INFO_FILE).read_bytes()), "Tax ID")
    token, summary = _look_of(client, "suppliers", "info.xlsx", _book(("Sheet1", rows)))
    assert "“Tax ID”" in summary and "vendor information list" in summary
    assert "Alpha Name" not in summary.split('role="alert"', 1)[1].split("</div>", 1)[0]
    refused = client.post("/jde-files/suppliers/load", data={"token": token}, headers=PAGE)
    assert refused.status_code == 400 and "“Tax ID”" in refused.json()["error"]
    assert store.master_vendors() == []


def test_title_rows_with_a_print_date_above_the_column_names_are_read_on_every_card(client, store, exports):
    """B-2411: two cells on a title row (the report's title and its print date) above the column names: cards 1 and
    2 read the file, cards 3 and 5 refused it as missing every column."""
    titled = [["MAPLE RIDGE HOLDINGS LTD."], ["Supplier Master", None, None, "11/12/2026"], []]
    for card, name, then in (("suppliers", made.SUPPLIERS_FILE, lambda: len(store.master_vendors()) == 40),
                             ("suppliers", made.VENDOR_INFO_FILE, lambda: store.vendor_info_count() == 55),
                             ("vouchers", made.HEADERS_FILE, lambda: store.erp_register_count() > 0)):  # fmt: skip
        rows = [*titled, *_grid((exports / name).read_bytes())]
        token, summary = _look_of(client, card, name, _book(("Sheet1", rows)))
        assert 'role="alert"' not in summary, name
        follow(client, client.post(f"/jde-files/{card}/load", data={"token": token}, headers=PAGE))
        assert then(), name


def test_the_journal_dropped_on_another_card_is_named_with_its_card(client, store, exports):
    """B-2412: the Transaction Journal (CSV) dropped on cards 1, 2, 3 and 5 said "upload .xlsx" or "could not be read
    as a spreadsheet"."""
    for card in ("chart", "units", "suppliers", "vouchers"):
        answer = _send(client, f"/jde-files/{card}/upload", exports, made.JOURNAL_FILE)
        assert answer.status_code == 400, card
        said = answer.json()["error"]
        assert "Transaction Journal" in said and "under 4. Last year's coding" in said, card


def test_the_journal_saved_as_a_workbook_is_told_to_be_saved_as_csv(client, store, exports):
    """B-2413: the journal saved as .xlsx on its own card was "not a Transaction Journal ... It looks like JD
    Edwards' Transaction Journal ...: load it under 4"."""
    rows = list(csv.reader(io.StringIO((exports / made.JOURNAL_FILE).read_text("utf-8-sig"))))
    answer = _send_bytes(client, "/jde-files/journal/upload", "journal.xlsx", _book(("Sheet1", rows)))
    assert answer.status_code == 400 and answer.json()["error"] == jde_files.JOURNAL_XLSX
    assert "not a Transaction Journal" not in jde_files.JOURNAL_XLSX and "CSV" in jde_files.JOURNAL_XLSX


def test_a_report_on_a_workbooks_second_sheet_is_read_there_or_the_sheet_is_named(client, store, exports):
    """B-2414: the report on the workbook's second sheet (a notes sheet first) was refused as missing its columns,
    with no word of sheets."""
    notes = ("Notes", [["Exported by", "me"]])
    rows = _grid((exports / made.CHART_FILE).read_bytes())
    token, summary = _look_of(client, "chart", "chart.xlsx", _book(notes, ("Data", rows)))
    assert "first sheet (“Notes”)" in summary and "sheet “Data”" in summary
    assert store.chart_accounts() == []
    rows = _grid((exports / made.VENDOR_INFO_FILE).read_bytes())
    answer = _send_bytes(client, "/jde-files/suppliers/upload", "info.xlsx", _book(notes, ("Data", rows)))
    summary = client.get(f"/jde-files?suppliers={_token(answer)}").text.split('id="suppliers-summary"', 1)[1]
    assert "sheet “Data”" in summary.split("</section>", 1)[0]
    rows = _grid((exports / made.HEADERS_FILE).read_bytes())
    token, summary = _look_of(client, "vouchers", "vouchers.xlsx", _book(notes, ("Data", rows)))
    assert 'role="alert"' not in summary
    follow(client, client.post("/jde-files/vouchers/load", data={"token": token}, headers=PAGE))
    assert store.erp_register_count() > 0


def test_a_vouchers_own_grid_says_what_it_is_and_the_grid_the_card_wants(client, store, exports):
    """B-2415: a voucher's G/L distribution, pay items or payment history on cards 3 and 5 was told to "add the
    columns to the grid"."""
    for grid in ("gl_distribution", "pay_items", "payment_history"):
        name = f"voucher_split_{grid}.xlsx"
        for card in ("suppliers", "vouchers"):
            token, summary = _look_of(client, card, name, (exports / name).read_bytes())
            said = summary.split('role="alert"', 1)[1].split("</div>", 1)[0]
            assert str(escape(import_specs.VOUCHER_GRIDS[grid])) in said and "Supplier Ledger Inquiry" in said, name
            assert "Add the column" not in said, name
            refused = client.post(f"/jde-files/{card}/load", data={"token": token}, headers=PAGE)
            assert refused.status_code == 400 and "Supplier Ledger Inquiry" in refused.json()["error"], name
    assert store.master_vendors() == [] and store.erp_register_count() == 0


def test_an_upload_gone_says_so_in_one_way_and_a_second_load_says_it_was_loaded(client, store, exports):
    """B-2416: "That upload has expired" here and "That upload is no longer here" there; a second Load after one
    that worked said it had expired."""
    for card in ("chart", "suppliers", "journal", "vouchers"):
        gone = client.post(f"/jde-files/{card}/load", data={"token": "nothing"}, headers=PAGE)
        assert gone.status_code == 400 and gone.json()["error"] == files.GONE, card
        page = client.get(f"/jde-files?{card}=nothing").text
        assert "That upload is no longer here" in page and "expired" not in page, card
    _load_chart(client, exports)
    for card, name in (("suppliers", made.SUPPLIERS_FILE), ("journal", made.JOURNAL_FILE),
                       ("vouchers", made.HEADERS_FILE), ("chart", made.CHART_FILE)):  # fmt: skip
        token = _token(_send(client, f"/jde-files/{card}/upload", exports, name))
        follow(client, client.post(f"/jde-files/{card}/load", data={"token": token}, headers=PAGE))
        again = client.post(f"/jde-files/{card}/load", data={"token": token}, headers=PAGE)
        assert again.status_code == 400 and again.json()["error"] == jde_files.LOADED, card


def test_an_empty_file_is_said_to_be_empty(client, store):
    """B-2417: a 0-byte .xlsx was "not an Excel workbook", an empty workbook "add the columns to the grid"."""
    empty_book = _book(("Sheet1", []))
    for card in ("chart", "units", "suppliers", "vouchers"):
        nothing = _send_bytes(client, f"/jde-files/{card}/upload", "export.xlsx", b"")
        assert nothing.status_code == 400 and nothing.json()["error"] == files.EMPTY, card
        answer = _send_bytes(client, f"/jde-files/{card}/upload", "export.xlsx", empty_book)
        said = answer.json()["error"] if answer.status_code == 400 else client.get(
            f"/jde-files?{card}={_token(answer)}").text  # fmt: skip
        assert str(escape(files.EMPTY)) in said or files.EMPTY in said, card
        assert "Add the column" not in said, card
    nothing = _send_bytes(client, "/jde-files/journal/upload", "journal.csv", b"")
    assert nothing.status_code == 400 and nothing.json()["error"] == files.EMPTY


def test_a_supplier_master_with_no_rows_or_no_number_column_has_no_way_round_to_vendors(client, store, exports):
    """B-2406: the supplier master without its Address Number column (or the voucher list without Gross Amount) was
    offered "Pick the columns on Vendors" ("Exports"), where it went in: 40 vendors with no number. A header-only
    supplier master added "Another ERP's supplier list is loaded on Vendors"."""
    rows = _grid((exports / made.SUPPLIERS_FILE).read_bytes())
    for data in (_book(("Sheet1", _without(rows, "Address Number"))), _book(("Sheet1", rows[:1]))):
        _token_, summary = _look_of(client, "suppliers", "suppliers.xlsx", data)
        assert "/vendors?file=" not in summary and "Another ERP" not in summary
    rows = _grid((exports / made.HEADERS_FILE).read_bytes())
    _token_, summary = _look_of(client, "vouchers", "vouchers.xlsx", _book(("Sheet1", _without(rows, "Gross Amount"))))
    assert "/exports?register=" not in summary and "“Gross Amount”" in summary


def _card_of(client, card: str, token: str) -> str:
    return client.get(f"/jde-files?{card}={token}").text.split(f'id="{card}-summary"', 1)[1].split("</section>")[0]


def test_each_load_button_says_what_it_loads_and_what_that_replaces_or_adds_to(client, store, exports):
    """B-2421: with a chart loaded, "Load 250 rows as the chart" (its 250 rows were 230 accounts, and that it
    replaces the chart whole was under a closed "What the chart holds"); "Load these business units" with no count;
    a supplier master loaded again, "40 suppliers to load", not saying they are updated, none added or removed."""
    _load_chart(client, exports)
    accounts, units = _accounts(store), len(store.business_units())
    card = _card_of(client, "chart", _token(_send(client, "/jde-files/chart/upload", exports, made.CHART_FILE)))
    assert f"Load {accounts:,} accounts</button>" in card
    assert f"Replaces the {accounts:,} accounts loaded now: the chart is taken whole from this file." in card
    card = _card_of(client, "units", _token(_send(client, "/jde-files/units/upload", exports, made.UNITS_FILE)))
    assert f"Load {units:,} business units</button>" in card and f"Replaces the {units:,} business units loaded" in card
    token = _token(_send(client, "/jde-files/suppliers/upload", exports, made.SUPPLIERS_FILE))
    assert "Updates the" not in _card_of(client, "suppliers", token)  # (nothing loaded yet: nothing to update)
    follow(client, client.post("/jde-files/suppliers/load", data={"token": token}, headers=PAGE))
    suppliers = len(store.master_vendors())
    card = _card_of(client, "suppliers", _token(_send(client, "/jde-files/suppliers/upload", exports,
                                                      made.SUPPLIERS_FILE)))  # fmt: skip
    assert f"Load {suppliers:,} suppliers</button>" in card
    assert f"Updates the {suppliers:,} suppliers loaded already and adds 0: no supplier is removed." in card
    token = _token(_send(client, "/jde-files/vouchers/upload", exports, made.HEADERS_FILE))
    follow(client, client.post("/jde-files/vouchers/load", data={"token": token}, headers=PAGE))
    card = _card_of(client, "vouchers", _token(_send(client, "/jde-files/vouchers/upload", exports, made.HEADERS_FILE)))
    assert f"Replace the list loaded now, {store.erp_register_count():,} vouchers (otherwise" in card


def test_a_load_says_what_it_did_in_one_line_then_the_next_file_and_the_meals_accounts_on_its_card(
        client, store, exports):  # fmt: skip
    """B-2424: a chart's load said "230 accounts" and "250 added" in one list of a dozen lines, the business units'
    repeated "GL accounts: 117 now (0 added…)", and nothing said what to load next; the meals accounts to tick were
    at the top of the page, off screen once the page went to the card."""
    page = _upload_and_load(client, "chart", made.CHART_FILE, (exports / made.CHART_FILE).read_bytes())
    said = page.split('id="chart-said"', 1)[1].split("</div></div>", 1)[0]
    head = said.split('class="imp-done-head"><b>', 1)[1].split("</b>", 1)[0]
    assert head.startswith("Read ") and f"{_accounts(store):,} accounts" in head
    assert 'Next: <a href="#units">2. Business units</a>' in said and "<details" in said and " rows added" in said
    card = page.split('id="chart"', 1)[1].split("</section>", 1)[0]
    gl, cc = len(store.list_accounts("gl_accounts")), len(store.list_accounts("cost_centers"))
    assert 'id="meals-accounts"' in card and f"lists {gl:,} GL accounts" in card and f"{cc:,} cost centers" in card
    assert 'id="meals-accounts"' not in page.split('id="checklist"', 1)[1].split("</section>", 1)[0]
    assert "No meals account ticked yet" in page.split('id="checklist"', 1)[1].split("</section>", 1)[0]
    page = _upload_and_load(client, "units", made.UNITS_FILE, (exports / made.UNITS_FILE).read_bytes())
    said = page.split('id="units-said"', 1)[1].split("</div></div>", 1)[0]
    assert 'Next: <a href="#suppliers">3. Suppliers</a>' in said
    assert "GL accounts:" not in said.split("<details", 1)[0]  # (folded with the rest)


def test_the_journal_is_summed_up_in_three_lines_with_the_rest_folded_and_its_count_said(client, store, exports):
    """B-2422 and B-2427: the journal's preview was some twenty lines ("Object 2110 closes 103 of 119 vouchers that
    balance…") with Load far below them and no count of what was read; "add up to the Grand Total: debits 383,307.80
    and credits 383,423.04" read as a contradiction; and a journal loaded already said "nothing is loaded yet" beside
    "This file is loaded already"."""
    _load_chart(client, exports)
    token = _token(_send(client, "/jde-files/journal/upload", exports, made.JOURNAL_FILE))
    summary = client.get(f"/jde-files?journal={token}").text.split('id="journal-summary"', 1)[1].split("</section>")[0]
    seen = journal_cmd.look(store, (exports / made.JOURNAL_FILE).read_bytes(), made.JOURNAL_FILE)
    lines, vouchers = seen.journal.totals.lines, seen.journal.totals.vouchers
    assert f"<b>{made.JOURNAL_FILE}</b> · {lines:,} lines on {vouchers:,} vouchers · nothing is loaded yet." in summary
    brief = summary.split('id="journal-brief">', 1)[1].split("</ul>", 1)[0]
    assert brief.count("<li>") == 3 and "matching the report&#39;s Grand Total" in brief and "To learn from: " in brief
    folded = summary.split("<details", 1)[1].split("</details>", 1)[0]
    assert "Object 2110 closes" in folded and "Object 2110 closes" not in summary.split("<details", 1)[0]
    assert "add up to the report" not in summary and "No row is missing" in folded
    assert summary.index("Load this journal") < summary.index("<details")
    follow(client, client.post("/jde-files/journal/load", data={"token": token}, headers=PAGE))
    token = _token(_send(client, "/jde-files/journal/upload", exports, made.JOURNAL_FILE))
    summary = client.get(f"/jde-files?journal={token}").text.split('id="journal-summary"', 1)[1].split("</section>")[0]
    assert "This file is loaded already" in summary and "nothing is loaded yet" not in summary
    assert f"{lines:,} lines on {vouchers:,} vouchers · loaded already." in summary


def test_each_reports_card_is_named_as_the_page_numbers_it():
    """(A message sends a file to its card by ``ImportSpec.card``: the page's own number and title.)"""
    for n, (card, (title, keys)) in enumerate(jde_files.CARDS.items(), start=1):
        for key in keys:
            assert import_specs.SPECS[key].card == f"{n}. {title}", (card, key)


def test_one_name_for_the_voucher_list_and_one_word_for_the_last_button_on_every_import(client, store):
    """B-2425: the voucher list was "Vouchers already in JD Edwards" (Load, "Replace the list loaded now") on JD
    Edwards files, "Invoices already in the ERP" (Import, "Replace the invoices already imported") on Exports and
    "bills" on the page of what is loaded; the last button was Load, Import, Apply or Teach by page. JD Edwards' words
    on both pages: "Invoices already in JD Edwards" ("your ERP" on Exports for another system's list)."""
    store.set_setting(SHOW_JDE, "0")  # (another system's list on Exports)
    assert "Invoices already in your ERP" in client.get("/exports").text
    store.set_setting(SHOW_JDE, "1")
    assert "5. Invoices already in JD Edwards" in client.get("/jde-files").text
    assert "Invoices already in JD Edwards" in client.get("/exports").text
    loaded = client.get("/jde-files/loaded/vouchers").text
    assert "Invoices already in JD Edwards" in loaded and "Vouchers already" not in loaded and " bills" not in loaded
    for path in ("/jde-files", "/accounts?tab=gl", "/accounts?tab=bank", "/vendors", "/purchase-orders", "/exports",
                 "/learning?teach=1", "/settings/jde"):  # fmt: skip
        page = client.get(path).text
        assert "click Load." in page and not re.search(r"click (Import|Teach|Apply)\b", page), path
