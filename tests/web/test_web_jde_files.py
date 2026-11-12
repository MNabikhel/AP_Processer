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
from ap_coder.erp import import_specs, jde_prototype, jde_settings
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
    assert (
        made.SUPPLIERS_FILE in summary
        and "this file is not loaded yet" in summary
        and "as</span> “Alpha Name”" in summary
    )
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
    assert made.VENDOR_INFO_FILE in summary and "this file is not loaded yet" in summary
    assert "55 suppliers to load: 0 already in the supplier list by their number" in summary
    assert "Left out: 1 row of totals at the end." in summary and "Tax ID: " in summary and "Currency: CAD" in summary
    assert '<b>Tax ID</b> <span class="muted">as</span> “Tax ID”' in summary
    assert '<b>Business unit number</b> <span class="muted">as</span> “Business Unit”' in summary  # (B-2480)
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
    the chart and the business units go to their reader as they come; the other boxes keep the limit (but for the
    voucher list, read within the chart reader's own: B-2433)."""
    monkeypatch.setattr(files.triage, "sheet_unzips_too_big", lambda content: True)
    _load_chart(client, exports)
    assert _accounts(store) > 0 and store.business_units()
    refused = _send(client, "/jde-files/suppliers/upload", exports, made.SUPPLIERS_FILE)
    assert refused.status_code == 400 and refused.json()["error"] == files.TOO_BIG_UNZIPPED


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
    assert f"<b>mixed.xlsx</b> · {rows:,} rows · this file is not loaded yet." in summary
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
        ">Business Unit (business unit number)</span>" in info and ">Business Unit (business unit name)</span>" in info
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


def test_a_vendor_information_list_with_a_needed_column_named_twice_is_not_loaded(client, store, exports):
    """B-2462: the vendor information list with a junk "Company Name" column before its own was refused on its card,
    but its Load checked only that rows were read: a POST of the token loaded every supplier as "ZZZ"."""
    rows = _grid((exports / made.VENDOR_INFO_FILE).read_bytes())
    at = next(i for i, row in enumerate(rows) if "Company Name" in [str(cell).strip() for cell in row])
    doubled = [["Company Name" if i == at else "ZZZ" if i > at else "", *row] for i, row in enumerate(rows)]
    token, summary = _look_of(client, "suppliers", "info.xlsx", _book(("Sheet1", doubled)))
    assert "more than one column named “Company Name”" in summary and 'role="alert"' in summary
    refused = client.post("/jde-files/suppliers/load", data={"token": token}, headers=PAGE)
    assert refused.status_code == 400 and "“Company Name”" in refused.json()["error"]
    assert store.master_vendors() == []


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
    assert (
        f"<b>{made.JOURNAL_FILE}</b> · {lines:,} lines on {vouchers:,} vouchers · this file is not loaded yet."
        in summary
    )
    brief = summary.split('id="journal-brief">', 1)[1].split("</ul>", 1)[0]
    assert brief.count("<li>") == 3 and "matching the report&#39;s Grand Total" in brief and "To learn from: " in brief
    folded = summary.split("<details", 1)[1].split("</details>", 1)[0]
    assert "Object 2110 closes" in folded and "Object 2110 closes" not in summary.split("<details", 1)[0]
    assert "add up to the report" not in summary and "No row is missing" in folded
    assert summary.index("Load this journal") < summary.index("<details")
    follow(client, client.post("/jde-files/journal/load", data={"token": token}, headers=PAGE))
    token = _token(_send(client, "/jde-files/journal/upload", exports, made.JOURNAL_FILE))
    summary = client.get(f"/jde-files?journal={token}").text.split('id="journal-summary"', 1)[1].split("</section>")[0]
    assert "This file is loaded already" in summary and "not loaded yet" not in summary
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


# --- The voucher list: read once, as the chart's reader reads a long export, loaded as on Exports -------------------


def _vouchers_page(client, items, name: str = "vouchers.xlsx", cut: bool = False) -> str:
    """The token of a voucher list of these pay items uploaded on card 5 (its totals row left off: ``cut``)."""
    from tests.jde_exports import voucher_headers

    data = voucher_headers(items)
    if cut:
        data = _book(("Sheet1", _grid(data)[:-1]))
    return _token(_send_bytes(client, "/jde-files/vouchers/upload", name, data))


def test_a_years_voucher_list_too_large_for_a_spreadsheet_is_read_as_the_charts_reader_reads_it(
        client, store, exports, monkeypatch):  # fmt: skip
    """B-2433: a year's voucher list as .xlsx (22,137 pay items: 46.7 MB unzipped) was refused on card 5 and on
    Exports by the 20 MB a sheet of any other upload may unzip to, as "it may be damaged"; as CSV it loaded in 3 s.
    It is read a row at a time within the chart reader's own bounds (``core/chart_file``); a file over any bound is
    "too large", told to be saved as CSV, never "damaged"."""
    monkeypatch.setattr(files.triage, "sheet_unzips_too_big", lambda content: True)
    token = _token(_send(client, "/jde-files/vouchers/upload", exports, made.HEADERS_FILE))
    assert "JD Edwards&#39; voucher list: " in _card_of(client, "vouchers", token)
    page = follow(client, client.post("/jde-files/vouchers/load", data={"token": token}, headers=PAGE))
    assert store.erp_register_count() > 0 and "vouchers of JD Edwards known now." in page
    sent = _send(client, "/exports/register/upload", exports, made.HEADERS_FILE)
    assert sent.status_code == 200 and "JD Edwards&#39; voucher list: " in client.get(sent.json()["redirect"]).text
    assert "too large" in files.TOO_BIG_UNZIPPED and "CSV" in files.TOO_BIG_UNZIPPED
    assert "damaged" not in files.TOO_BIG_UNZIPPED + files.TOO_BIG
    refused = _send(client, "/jde-files/suppliers/upload", exports, made.SUPPLIERS_FILE)  # (no long export: 20 MB)
    assert refused.status_code == 400 and refused.json()["error"] == files.TOO_BIG_UNZIPPED
    monkeypatch.setattr(jde_files.chart_file, "SHEET_MOST", 1000)  # over the chart reader's bound too
    refused = _send(client, "/jde-files/vouchers/upload", exports, made.HEADERS_FILE)
    assert refused.status_code == 400 and refused.json()["error"] == files.TOO_BIG_UNZIPPED


def test_replace_is_refused_for_a_voucher_list_that_may_be_cut_short(client, store):
    """B-2434: Replace ticked on card 5 with a list whose totals row is missing (or not the sum of its rows) took the
    list loaded from 20,000 vouchers to 18,193, while its words said a voucher it does not show is never taken off.
    Replace is refused for such a list, saying why, on card 5 and on Exports; loaded without Replace, it adds."""
    from tests.jde_exports import sample_pay_items, voucher_headers

    page = follow(client, client.post("/jde-files/vouchers/load", data={"token": _vouchers_page(
        client, sample_pay_items())}, headers=PAGE))  # fmt: skip
    before = store.erp_register_count()
    assert before == 5, page
    token = _vouchers_page(client, sample_pay_items()[:2], cut=True)
    refused = client.post("/jde-files/vouchers/load", data={"token": token, "replace": "1"}, headers=PAGE)
    assert refused.status_code == 400 and "cut short" in refused.json()["error"] and store.erp_register_count() == 5
    cut = _book(("Sheet1", _grid(voucher_headers(sample_pay_items()[:2]))[:-1]))
    sent = client.post("/exports/register/upload", files={"file": ("v.xlsx", cut)}, headers=PAGE)
    register = re.search(r"register=([^&#]+)", sent.json()["redirect"]).group(1)
    refused = client.post("/exports/register/import", data={"file": register, "replace": "1"}, headers=PAGE)
    assert refused.status_code == 400 and "cut short" in refused.json()["error"] and store.erp_register_count() == 5
    follow(client, client.post("/jde-files/vouchers/load", data={"token": token}, headers=PAGE))
    assert store.erp_register_count() == 5  # (its two vouchers were there already)


def test_card_5_takes_off_a_voided_voucher_as_exports_does(client, store):
    """B-2435: card 5 never took off the list a voucher a later list shows at 0.00 (voided in JD Edwards); Exports
    did with the same file, and also offered to take off what a whole, unfiltered list no longer shows. Both pages
    load a voucher list by the same function, with the same words."""
    from tests.jde_exports import pay_item, sample_pay_items

    follow(client, client.post("/jde-files/vouchers/load", data={"token": _vouchers_page(client, sample_pay_items())},
                               headers=PAGE))  # fmt: skip
    assert store.in_erp("Maple Leaf Coffee Services Inc.", "INV-0042", 214.2)
    void = pay_item(700101, "001", 0.00, 410001, "MAPLE LEAF COFFEE SERVICES INC.", "INV-0042")
    later = [void, *sample_pay_items()[1:4], *sample_pay_items()[5:7]]
    page = follow(client, client.post("/jde-files/vouchers/load", data={"token": _vouchers_page(client, later)},
                                      headers=PAGE))  # fmt: skip
    assert store.in_erp("Maple Leaf Coffee Services Inc.", "INV-0042", 214.2) == []
    assert "1 voucher an earlier list gave is voided or no longer in JD Edwards by this one: taken off" in page
    assert store.in_erp("Maple Leaf Coffee Services Inc.", "ML-3318", 642.90)  # not shown: still there
    whole = {"token": _vouchers_page(client, later), "whole": "1"}
    follow(client, client.post("/jde-files/vouchers/load", data=whole, headers=PAGE))
    assert store.in_erp("Maple Leaf Coffee Services Inc.", "ML-3318", 642.90) == []


def test_card_5_reads_its_file_once_at_the_upload(client, store, exports, monkeypatch):
    """B-2441: card 5's summary read the uploaded voucher list again at every view of the page (1.2 to 3 s a view;
    a year's list as .xlsx, 8 s). What was read is kept with the upload, as the other cards keep theirs, and the
    load reads nothing again."""
    token = _token(_send(client, "/jde-files/vouchers/upload", exports, made.HEADERS_FILE))
    first = _card_of(client, "vouchers", token)

    def never(*_args, **_kwargs):
        raise AssertionError("the file was read again")

    monkeypatch.setattr(files, "read_sheet", never)
    assert _card_of(client, "vouchers", token) == first and "JD Edwards&#39; voucher list: " in first
    follow(client, client.post("/jde-files/vouchers/load", data={"token": token}, headers=PAGE))
    assert store.erp_register_count() > 0


def test_card_5_offers_whole_list_as_exports_does(client, store, exports):
    """B-2464: card 5's load takes off the vouchers of the period a whole list no longer shows when told the grid was
    not filtered (``whole``), as Exports' does, but the card had no box to say so: the list could only be added."""
    token = _token(_send(client, "/jde-files/vouchers/upload", exports, made.HEADERS_FILE))
    card = _card_of(client, "vouchers", token)
    sent = _send(client, "/exports/register/upload", exports, made.HEADERS_FILE)
    register = client.get(sent.json()["redirect"]).text
    label = re.search(r'<input type="checkbox" name="whole" value="1">([^<]+)</label>', register).group(1)
    assert label.strip().startswith("This list is every voucher of the period it covers")
    assert f'<input type="checkbox" name="whole" value="1">{label}</label>' in card


def test_a_file_being_loaded_shows_no_load_button(client, store, exports, monkeypatch):
    """B-2465: while a chart or business unit file was being loaded (``looks.<card>.loading``, B-2437) its card
    showed it as before, with its Load button; Load then said only that it was being loaded. The card says the file
    is being loaded and offers no Load."""
    monkeypatch.setattr(jde_files, "_being_loaded", lambda token: True)
    for card, name in (("chart", made.CHART_FILE), ("units", made.UNITS_FILE)):
        shown = _card_of(client, card, _token(_send(client, f"/jde-files/{card}/upload", exports, name)))
        assert "is being loaded now" in shown and f'data-load="{card}"' not in shown, card


def test_an_upload_gone_says_how_long_and_how_many_are_kept(client):
    """B-2466: JD Edwards files and Learning said an upload gone was gone "(uploads are kept for an hour)", where
    every other page says an upload is kept for an hour and only the person's last 24 (``files.GONE``)."""
    for url in ("/jde-files?chart=gone", "/learning?history=gone"):
        page = client.get(url).text
        assert "(uploads are kept for an hour)" not in page and "only your last 24" in page, url


def test_a_worksheet_too_large_is_called_too_large_not_damaged(client, monkeypatch):
    """B-2467: the JDE team's worksheet that unzips to more than any workbook may was refused as "may be damaged",
    on its upload and by its reader. It is called too large, and told to be downloaded again."""
    from ap_coder.erp import jde_worksheet

    monkeypatch.setattr(files.triage, "sheet_unzips_too_big", lambda content: True)
    refused = client.post("/settings/jde/worksheet/upload", files={"file": ("w.xlsx", b"PK")}, headers=PAGE)
    said = [refused.json()["error"], *jde_worksheet.read(b"PK", jde_settings.JdeSettings()).problems]
    assert refused.status_code == 400 and said[0] == said[1], said
    assert "too large" in said[0] and "damaged" not in said[0]


def test_exports_reads_a_voucher_list_once_at_the_upload(client, store, exports, monkeypatch):
    """B-2468: Exports read an uploaded voucher list again at every view of the page and at its import (a year's
    list as .xlsx, 8 s each), where card 5 keeps what was read with the upload (B-2441). Exports keeps it too."""
    sent = _send(client, "/exports/register/upload", exports, made.HEADERS_FILE)
    first = client.get(sent.json()["redirect"]).text.split('id="register"', 1)[1]

    def never(*_args, **_kwargs):
        raise AssertionError("the file was read again")

    monkeypatch.setattr(files, "read_sheet", never)
    again = client.get(sent.json()["redirect"]).text.split('id="register"', 1)[1]
    assert again == first and "JD Edwards&#39; voucher list: " in first
    token = re.search(r"register=([^&#]+)", sent.json()["redirect"]).group(1)
    follow(client, client.post("/exports/register/import", data={"file": token}, headers=PAGE))
    assert store.erp_register_count() > 0


# --- Review passes 3 and 4: headings, names and what a box says while it works --------------------------------------


def _levels(html: str) -> list[int]:
    """The levels of the headings of the page's content, in order."""
    return [int(n) for n in re.findall(r"<h([1-6])[ >]", html.split('id="main"', 1)[1])]


def test_the_headings_go_down_one_level_at_a_time(client, store, exports):
    """B-2447: the page's headings went from its title (h1) to the cards' (h4) and the file shown's "Columns" and
    "Its first rows" (h5). The cards are h2, the parts under them h3."""
    for card, name in (("chart", made.CHART_FILE), ("suppliers", made.SUPPLIERS_FILE)):
        page = client.get(f"/jde-files?{card}={_token(_send(client, f'/jde-files/{card}/upload', exports, name))}").text
        levels = _levels(page)
        assert levels[0] == 1 and all(b <= a + 1 for a, b in zip(levels, levels[1:], strict=False)), levels
        assert '<h2 id="chart-title">1. Chart of accounts</h2>' in page and '<h3 class="imp-sub">Columns</h3>' in page


def test_each_more_is_named_after_what_it_is_about(client):
    """B-2448: five toggles on the page were each called "More": a screen reader's list of them said "More" five
    times. Each names its report."""
    names = re.findall(r'<details class="imp-more"><summary>([^<]*)</summary>', client.get("/jde-files").text)
    more = [name for name in names if name.startswith("More")]
    assert "More" not in names and len(more) == 5 and len(set(more)) == 5, names
    assert "More about chart of accounts" in more and "More about invoices already in JD Edwards" in more


def test_a_file_shown_says_this_file_is_not_loaded_yet_beside_what_is_loaded_now(client, store, exports):
    """B-2457: the chart shown said "14,000 rows · nothing is loaded yet." above "Replaces the 13,870 accounts loaded
    now": it says this file is not loaded yet."""
    _load_chart(client, exports)
    card = _card_of(client, "chart", _token(_send(client, "/jde-files/chart/upload", exports, made.CHART_FILE)))
    assert "rows · this file is not loaded yet." in card and "nothing is loaded yet" not in card
    assert "Replaces the" in card


def test_the_vendor_information_list_says_what_its_load_does(client, store, exports):
    """B-2459: the vendor information list's preview had no line saying what Load would do, where the supplier
    master's says it updates the suppliers loaded and removes none."""
    token = _token(_send(client, "/jde-files/suppliers/upload", exports, made.SUPPLIERS_FILE))
    follow(client, client.post("/jde-files/suppliers/load", data={"token": token}, headers=PAGE))
    card = _card_of(client, "suppliers", _token(_send(client, "/jde-files/suppliers/upload", exports,
                                                      made.VENDOR_INFO_FILE)))  # fmt: skip
    then = card.split('id="suppliers-then"', 1)[1].split("</p>", 1)[0]
    assert re.search(r"Updates the [\d,]+ suppliers? loaded already and adds [\d,]+: no supplier is removed\.", then)
    assert "Each keeps its payment hold; its tax ID, currency, terms and address are taken from this list." in then


def test_in_a_browser_the_chart_box_says_it_is_reading_and_how_long_it_takes(chromium, store, exports):
    """B-2460: a real-size chart takes 15 to 19 s to read, and the box showed only "Reading the chart…" on its
    button. The button says how long it takes, and the box what file it reads, the seconds gone by and how long a
    real-size chart takes; it is said once to a screen reader, not each second."""
    from tests.browser import browser_on

    with browser_on(chromium, create_app(web_settings(), store, start_reader=False)) as (browser, base):
        page = browser.new_page(viewport={"width": 683, "height": 400})
        held = []
        page.route("**/jde-files/chart/upload", lambda route: held.append(route))
        page.goto(base + "/jde-files")
        page.locator("#chart input[type=file]").set_input_files(str(exports / made.CHART_FILE))
        page.locator("#chart .imp-upload button[type=submit]").click()
        page.wait_for_selector("#chart .imp-drop.working")
        said = page.locator("#chart [data-imp-chosen]").inner_text()
        assert f"Reading {made.CHART_FILE}… A chart of some 14,000 accounts takes 10 to 20 seconds" in said, said
        assert "10 to 20 seconds" in page.locator("#chart .imp-upload button[type=submit]").inner_text()
        assert page.locator("#chart form.imp-upload[aria-busy]").count() == 1
        page.wait_for_function("/\\d+ s$/.test(document.querySelector('#chart .imp-seconds').textContent)")
        assert page.locator("#chart .imp-seconds").get_attribute("aria-hidden") == "true"
        held[0].continue_()
        page.wait_for_selector("#chart-summary")
        page.close()


def test_the_page_counts_the_chart_and_never_reads_its_rows(client, store, exports, monkeypatch):
    """B-2436: opening JD Edwards files while a chart was being loaded took 11 to 13 s: the page read the chart's
    20,000 rows one at a time only to count them, and after each row it waited for the load, reading its workbook on
    another thread, to let go of Python's lock (5 ms at a time: not the database, which the load writes to only
    once it has read the file). The page counts them in one query, and the dashboard's threads take turns every
    half millisecond (0.3 s for the page during a load)."""
    import sys

    from ap_coder.web import app as web_app

    assert sys.getswitchinterval() == web_app.SWITCH_INTERVAL <= 0.0005  # (set by create_app)
    _load_chart(client, exports)
    accounts, units = _accounts(store), len({row.bu for row in store.chart_accounts()})
    vendors = len(store.master_vendors())

    def never(*_args):
        raise AssertionError("every row read")

    monkeypatch.setattr(type(store), "chart_accounts", never)
    monkeypatch.setattr(type(store), "master_vendors", never)
    page = client.get("/jde-files").text
    assert f"{accounts:,} accounts</b>" in page and f"On {units:,} business units" in page and vendors == 0


def test_a_file_being_loaded_says_so_and_is_not_loaded_twice(client, store, exports, monkeypatch):
    """B-2437: the page opened again during a chart's Load showed the file as before, Load offered; Load then said
    the file was loaded already, with the counts from before. While a file is loaded its card says so, and a second
    Load of it is refused."""
    import threading

    from fastapi.testclient import TestClient

    from ap_coder.web.core import using_store

    _load_chart(client, exports)
    token = _token(_send(client, "/jde-files/chart/upload", exports, made.CHART_FILE))
    reading, go_on = threading.Event(), threading.Event()
    real = chart_cmd.import_files

    def importing(*args, **kwargs):
        reading.set()
        assert go_on.wait(30)
        return real(*args, **kwargs)

    monkeypatch.setattr(jde_files.chart_cmd, "import_files", importing)
    first = {}
    loading = threading.Thread(target=lambda: first.setdefault("answer", client.post(
        "/jde-files/chart/load", data={"token": token}, headers=PAGE)))  # fmt: skip
    loading.start()
    try:
        assert reading.wait(30)
        other = TestClient(client.app, base_url="http://localhost")
        with using_store(store):  # (the card's words for it are the page's: ``looks.chart.loading``)
            look = jde_files.page_context(store, {"chart": token})["looks"]["chart"]
        again = other.post("/jde-files/chart/load", data={"token": token}, headers=PAGE)
    finally:
        go_on.set()
        loading.join(30)
    assert first["answer"].status_code == 200 and again.status_code == 400
    assert again.json()["error"] == jde_files.BEING_LOADED and look["loading"]
    again = client.post("/jde-files/chart/load", data={"token": token}, headers=PAGE)
    assert again.json()["error"] == jde_files.LOADED  # (done: loaded already)


def test_the_charts_load_says_how_many_gl_accounts_it_removes_or_changes(client, store, exports):
    """B-2438: a chart loaded after a GL list was imported on GL accounts & tax took off the GL accounts only that
    list had (9990, 9991) and put the chart's description back over one typed there, said only under "8 more
    lines". The chart's preview counts the GL accounts its load removes and changes (``gl_replaced``)."""
    from ap_coder.web.core import using_store

    _load_chart(client, exports)
    first = store.list_accounts("gl_accounts")[0]
    store.import_accounts("gl_accounts", [{"c": "9990", "d": "Suspense"}, {"c": "9991", "d": "Clearing"},
                                          {"c": first["code"], "d": "Typed here"}], "c", "d")  # fmt: skip
    token = _token(_send(client, "/jde-files/chart/upload", exports, made.CHART_FILE))
    with using_store(store):
        replaced = jde_files.page_context(store, {"chart": token})["gl_replaced"]
        assert jde_files.page_context(store, {})["gl_replaced"] is None
    assert (replaced["removed"], replaced["changed"]) == (2, 1)
    follow(client, client.post("/jde-files/chart/load", data={"token": token}, headers=PAGE))
    listed = {r["code"]: r["description"] for r in store.list_accounts("gl_accounts")}
    assert "9990" not in listed and "9991" not in listed and listed[first["code"]] == first["description"]


def test_loading_the_same_chart_or_units_again_is_recorded(client, store, exports):
    """B-2439: the chart or the business units loaded again from the same file recorded nothing: "last loaded" kept
    the first time and Activity showed no load. A load is recorded even when it changes nothing (and nothing else
    is worked out again)."""
    _load_chart(client, exports)
    version = store.chart_version()
    _load_chart(client, exports)
    events = store.events(actions=["accounts_imported"])
    assert [e["detail"]["table"] for e in events] == ["business_units", "chart_accounts"] * 2
    assert events[0]["detail"]["unchanged"] and "unchanged" not in events[3]["detail"]
    assert store.chart_version() == version
    from ap_coder.core import audit
    from ap_coder.web.core import using_store

    assert "loaded again, the same as before: nothing changed" in audit.describe(events[1])
    with using_store(store):
        last = jde_files.page_context(store, {})["last"]["chart"]["when"]
    assert last == str(events[1]["created_at"]).replace("T", " ")[:16]


def test_getting_started_is_not_ticked_while_the_business_units_offer_another_company(client, store, exports):
    """B-2440: Getting started ticked "JD Edwards settings ✓ company 00001" while card 2 offered "Use company
    00210", the one company of the business units loaded: every voucher would have gone out under 00001. The step
    is to do until that company is used (or another set)."""
    from ap_coder.web.core import SHOW_JDE, using_store
    from ap_coder.web.pages.process import JDE_STEP, pilot_steps

    store.set_setting(SHOW_JDE, "1")
    jde_settings.save(store, jde_settings.JdeSettings(use_default_rule=True, default_bu="1"), actor="pat")
    _load_chart(client, exports)
    (company,) = {unit.company for unit in store.business_units()}

    def step():
        with using_store(store):
            return next(s for s in pilot_steps(store) if s[1] == JDE_STEP)

    state, text = step()[0], step()[2]
    assert state == "todo" and company in text and "JD Edwards files" in text, text
    follow(client, client.post("/jde-files/company", headers=PAGE))
    assert step()[0] == "ok"


def test_vendors_says_its_suppliers_came_from_jd_edwards_files_and_warns_before_a_list_on_top(client, store, exports):
    """B-2476: after the suppliers were loaded on JD Edwards files, Vendors said "55 vendors in the vendor master ·
    Imported from the ERP" with no link there, and its own import added another list on top without a word, where GL
    accounts & tax warns first."""
    token = _token(_send(client, "/jde-files/suppliers/upload", exports, made.SUPPLIERS_FILE))
    follow(client, client.post("/jde-files/suppliers/load", data={"token": token}, headers=PAGE))
    count = store.master_vendor_count()
    page = re.sub(r"\s+", " ", client.get("/vendors").text)
    said = page.split('id="from-jde"', 1)[1].split("</p>", 1)[0]
    assert f"The {count:,} vendors in the vendor master were loaded on" in said
    assert '<a href="/jde-files#suppliers">JD Edwards files</a>' in said
    assert "Imported from the ERP" not in page
    grid = io.BytesIO()
    book = openpyxl.Workbook()
    book.active.append(["Vendor Name", "Vendor ID"])
    book.active.append(["Another Supply Ltd", "99001"])
    book.save(grid)
    answer = client.post("/vendors/master/upload", files={"file": ("vendors.xlsx", grid.getvalue())}, headers=PAGE)
    form = client.get(answer.json()["redirect"]).text.split('id="pick-vendor_master"', 1)[1].split("</form>", 1)[0]
    form = re.sub(r"\s+", " ", form)
    assert f"The suppliers loaded on JD Edwards files give the {count:,} vendors in the vendor master now" in form
    assert "a list loaded here is added on top of them" in form


def test_getting_started_never_ticks_the_company_a_new_installation_starts_with(tmp_path, exports):
    """B-2478: on a fresh install Getting started ticked "JD Edwards settings ✓ company 00001, 0 accounts mapped",
    then turned it to to-do once the business units were loaded (another company): a step went backwards. The
    company a new installation starts with is to do until a person saves settings of their own or the business units
    loaded are that company."""
    from ap_coder.core.chart import BuRow
    from ap_coder.web.core import first_run, using_store
    from ap_coder.web.pages.process import JDE_STEP, pilot_steps
    from tests.webclient import client_for

    store = first_run(tmp_path / "private" / "ap_coder.db", server=False)

    def step():
        with using_store(store):
            return next(s for s in pilot_steps(store) if s[1] == JDE_STEP)

    state, _, text = step()
    assert state == "todo" and "company 00001" in text and "JD Edwards files" in text, text
    client = client_for(store)
    _load_chart(client, exports)
    assert step()[0] == "todo"
    follow(client, client.post("/jde-files/company", headers=PAGE))  # the company of the business units, used
    assert step()[0] == "ok"
    with using_store(store):  # (a company really numbered 00001: its business units say so)
        store.import_business_units([BuRow(unit.code, unit.name, "00001", unit.type, unit.status)
                                     for unit in store.business_units()])  # fmt: skip
        jde_settings.save(store, jde_prototype.prototype_settings())
    assert step()[0] == "ok"


# Each card's file of the made-up company, in the order they are loaded.
EVERY_FILE = (
    ("chart", made.CHART_FILE),
    ("units", made.UNITS_FILE),
    ("suppliers", made.SUPPLIERS_FILE),
    ("suppliers", made.VENDOR_INFO_FILE),
    ("journal", made.JOURNAL_FILE),
    ("vouchers", made.HEADERS_FILE),
)


def test_each_cards_check_lands_on_the_file_shown_so_its_load_is_in_view(client, exports):
    """B-2479: after Check, the page landed on the card's top, so on a laptop (1366 x 768, 911 x 600) the vendor
    information list's Load (card 3) and the voucher list's (card 5) were below the fold. Every card's Check lands on
    the file it shows (``#<card>-summary``): its verdict and its Load."""
    for card, name in EVERY_FILE:
        answer = _send(client, f"/jde-files/{card}/upload", exports, name)
        assert answer.status_code == 200, answer.text
        address = answer.json()["redirect"]
        assert address.endswith(f"#{card}-summary"), (name, address)
        assert f'id="{card}-summary"' in client.get(address).text, name
        follow(client, client.post(f"/jde-files/{card}/load", data={"token": _token(answer)}, headers=PAGE))


def test_in_a_browser_each_cards_load_is_in_view_after_check_on_a_laptop(chromium, store, exports, tmp_path):
    """B-2479, in a browser: at 1366 x 768 and 911 x 600, after each card's Check the Load button is on screen."""
    from ap_coder.core.store import Store
    from tests.browser import browser_on

    for width, height in ((1366, 768), (911, 600)):  # (each on a database of its own: nothing loaded yet)
        empty = Store(tmp_path / f"{width}.db")
        with browser_on(chromium, create_app(web_settings(), empty, start_reader=False)) as (browser, base):
            page = browser.new_page(viewport={"width": width, "height": height})
            page.goto(base + "/jde-files")
            for card, name in EVERY_FILE:
                page.locator(f"#{card} input[type=file]").set_input_files(str(exports / name))
                page.locator(f"#{card} .imp-upload button[type=submit]").click()
                page.wait_for_selector(f"#{card}-summary")
                page.wait_for_load_state("load")
                page.wait_for_timeout(300)  # (the page takes the focus to what it shows: imports.js)
                load = page.locator(f"#{card}-summary button[data-load]")
                assert load.count() == 1, page.locator(f"#{card}-summary").inner_text()
                box = load.bounding_box()
                assert box and 0 <= box["y"] and box["y"] + box["height"] <= height, (width, name, box)
                load.click()
                page.wait_for_selector(f"#{card}-said")
            page.close()


def test_the_vendor_information_lists_two_business_unit_columns_say_number_and_name(client, exports):
    """B-2480: the vendor information list's two "Business Unit" chips read "Business Unit (the 1st of that name)"
    and "Business Unit (business unit name)": the first is the business unit's number, and says so. The columns read
    are the same: the first by that name the number, the second the name."""
    from ap_coder.coding import vendor_info

    info = client.get("/jde-files").text.split('id="cols-vendor_info"', 1)[1].split("</details>", 1)[0]
    assert ">Business Unit (business unit number)</span>" in info and "(the 1st of that name)" not in info
    assert ">Business Unit (business unit name)</span>" in info
    assert vendor_info.positions(["Internal Supplier Number", "Business Unit", "Tax ID", "Business Unit"]) == {
        "erp_id": 0, "business_unit": 1, "tax_id": 2, "business_unit_name": 3}  # fmt: skip
    token = _token(_send(client, "/jde-files/suppliers/upload", exports, made.VENDOR_INFO_FILE))
    summary = _summary(client, token)
    assert '<b>Business unit number</b> <span class="muted">as</span> “Business Unit”' in summary
