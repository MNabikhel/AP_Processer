"""JD Edwards files (ap_coder/web/pages/jde_files.py): the made-up company's five exports loaded through the page,
each summary shown and the data really kept; a wrong file in a box answers with the importer's plain words."""

from __future__ import annotations

import csv
import io
import re

import openpyxl
import pytest
from markupsafe import escape

from ap_coder.bench import jde_exports as made
from ap_coder.core.reference_data import reads_like_meals
from ap_coder.erp import jde
from ap_coder.ops import journal_cmd
from ap_coder.web import files, roles
from ap_coder.web.app import create_app
from ap_coder.web.pages import jde_files
from tests import chart_files as cf
from tests.webclient import PAGE, follow, web_settings

ROUTES = ["/jde-files/chart", "/jde-files/units", "/jde-files/chart/settings", "/jde-files/suppliers/upload",
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
    return re.search(r"(?:suppliers|journal|vouchers)=([^&#]+)", answer.json()["redirect"]).group(1)


def _accounts(store) -> int:
    return len([row for row in store.chart_accounts() if not row.is_header])


def _load_chart(client, exports) -> None:
    for path, name in (("/jde-files/chart", made.CHART_FILE), ("/jde-files/units", made.UNITS_FILE)):
        assert _send(client, path, exports, name).status_code == 200


def test_the_page_starts_with_nothing_loaded_and_says_the_order(client):
    page = client.get("/jde-files").text
    assert page.count("Not yet") == 10 and "Loaded</span>" not in page  # the checklist's five lines, and each card
    assert str(escape(jde_files.ORDER)) in page
    for card in ("chart", "units", "suppliers", "journal", "vouchers"):
        assert f'<section class="card stack" id="{card}">' in page and f'href="#{card}"' in page
    assert page.count('type="file"') == 5


def test_the_chart_and_the_business_units_load_and_say_what_was_read(client, store, exports):
    page = follow(client, _send(client, "/jde-files/chart", exports, made.CHART_FILE))
    said = page.split('id="chart-said"', 1)[1].split("</div></div>", 1)[0]
    assert "Chart of accounts" in said and "Accounts a line may be coded to" in said
    assert _accounts(store) > 0 and f"{_accounts(store):,} accounts</b>" in page and "last loaded" in page
    assert 'id="chart-tally"' in page and "A newer export replaces the chart whole" in page
    page = follow(client, _send(client, "/jde-files/units", exports, made.UNITS_FILE))
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


@pytest.mark.parametrize("column, said", [
    ("P E", "Loaded without the posting edit column (“P E”): every account is offered"),
    ("Subsidiary", "Loaded without the “Subsidiary” column: the accounts of one object that differ only by"),
])  # fmt: skip
def test_a_chart_without_a_column_it_needs_is_loaded_only_when_ticked_and_the_page_keeps_saying_so(
        column, said, client, store, tmp_path):  # fmt: skip
    """A grid exported without its posting edit column loaded with every account postable, one without its
    Subsidiary column with most accounts left out, and neither with a word about the column (B-2271, B-2272)."""
    grid = cf.write_chart(tmp_path / "grid.xlsx", without=(column,)).read_bytes()
    page = client.get("/jde-files").text
    assert 'name="anyway"' in page and "Load it anyway: every account will be offered" in page
    refused = client.post("/jde-files/chart", files={"file": ("grid.xlsx", grid)}, headers=PAGE)
    assert refused.status_code == 400 and store.chart_accounts() == []
    why = refused.json()["error"]
    assert why.startswith("Not loaded: This file has no ") and column in why and "export again" in why
    assert why.endswith("Or tick “Load it anyway” under the file and load it again.")
    ticked = client.post("/jde-files/chart", files={"file": ("grid.xlsx", grid)}, data={"anyway": "1"}, headers=PAGE)
    assert said in follow(client, ticked).split('id="chart-said"', 1)[1].split("</div></div>", 1)[0]
    assert _accounts(store) > 0
    assert said in client.get("/jde-files").text.split('id="chart-tally"', 1)[1].split("</ul>", 1)[0]  # every time
    whole = cf.write_chart(tmp_path / "chart.xlsx").read_bytes()
    page = follow(client, client.post("/jde-files/chart", files={"file": ("chart.xlsx", whole)}, headers=PAGE))
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
    assert jde.load(store).company == "00001" and "/jde-files/company" not in client.get("/jde-files").text
    several = [(code, name, "00002" if n else company, kind, status)
               for n, (code, name, company, kind, status) in enumerate(cf.UNITS)]  # fmt: skip
    units = cf.write_business_units(tmp_path / "bu.xlsx", several).read_bytes()
    page = follow(client, client.post("/jde-files/units", files={"file": ("bu.xlsx", units)}, headers=PAGE))
    assert "/jde-files/company" not in page
    refused = client.post("/jde-files/company", headers=PAGE)
    assert refused.status_code == 400 and jde.load(store).company == "00001"
    _load_chart(client, exports)
    (company,) = {unit.company for unit in store.business_units()}
    page = client.get("/jde-files").text
    assert company != "00001" and f"Use company {company} (the one your business units belong to)" in page
    assert jde.load(store).company == "00001"  # not without the click
    page = follow(client, client.post("/jde-files/company", headers=PAGE))
    assert jde.load(store).company == company and "/jde-files/company" not in page
    assert f"company number is now {company}" in page.split('id="units-said"', 1)[1].split("</div></div>", 1)[0]


def test_the_suppliers_are_shown_before_they_are_loaded(client, store, exports):
    token = _token(_send(client, "/jde-files/suppliers/upload", exports, made.SUPPLIERS_FILE))
    page = client.get(f"/jde-files?suppliers={token}").text
    summary = page.split('id="suppliers-summary"', 1)[1].split("</section>", 1)[0]
    assert made.SUPPLIERS_FILE in summary and "nothing is loaded yet" in summary and "Columns found: " in summary
    assert "Load these suppliers" in summary and f'href="/vendors?file={token}#import"' in summary
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
    assert "no supplier number column (“Address Number”" in summary and "Load these suppliers" not in summary
    assert "Add the column to the grid in JD Edwards and export again" in summary
    refused = client.post("/jde-files/suppliers/load", data={"token": token}, headers=PAGE)
    assert refused.status_code == 400 and refused.json()["error"] == jde_files.NO_NUMBER_COLUMN
    assert store.master_vendors() == []


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
    assert "JD Edwards&#39; voucher list: " in summary and "Load these vouchers" in summary
    assert store.erp_register_count() == 0
    page = follow(client, client.post("/jde-files/vouchers/load", data={"token": token}, headers=PAGE))
    count = store.erp_register_count()
    assert count > 0 and f"{count:,} vouchers of JD Edwards known now." in page
    assert f"{count:,} vouchers</b>" in page and 'href="/exports#register"' in page


def test_a_wrong_file_in_a_box_is_refused_in_plain_words(client, store, exports):
    for path in ("/jde-files/chart", "/jde-files/units"):
        wrong = _send(client, path, exports, made.SUPPLIERS_FILE)
        assert wrong.status_code == 400 and "Not loaded: " in wrong.json()["error"], path
        other = _send(client, path, exports, made.JOURNAL_FILE)
        assert other.status_code == 400 and other.json()["error"] == jde_files.NOT_XLSX, path
        broken = client.post(path, files={"file": ("chart.xlsx", b"not a workbook")}, headers=PAGE)
        assert broken.status_code == 400 and "is not an Excel workbook" in broken.json()["error"], path
    assert store.chart_accounts() == [] and store.business_units() == []

    token = _token(_send(client, "/jde-files/suppliers/upload", exports, made.UNITS_FILE))
    assert jde_files.NO_NAME_COLUMN in client.get(f"/jde-files?suppliers={token}").text
    wrong = client.post("/jde-files/suppliers/load", data={"token": token}, headers=PAGE)
    assert wrong.status_code == 400 and wrong.json()["error"] == jde_files.NO_NAME_COLUMN
    gone = client.post("/jde-files/suppliers/load", data={"token": "nothing"}, headers=PAGE)
    assert gone.status_code == 400 and gone.json()["error"] == files.GONE
    assert store.master_vendors() == []

    wrong = _send(client, "/jde-files/journal/upload", exports, made.SUPPLIERS_FILE)
    assert wrong.status_code == 400 and wrong.json()["error"] == journal_cmd.NOT_A_JOURNAL
    gone = client.post("/jde-files/journal/load", data={"token": "nothing"}, headers=PAGE)
    assert gone.status_code == 400 and "expired" in gone.json()["error"]

    token = _token(_send(client, "/jde-files/vouchers/upload", exports, made.SUPPLIERS_FILE))
    page = client.get(f"/jde-files?vouchers={token}").text
    assert "voucher list (the supplier ledger inquiry" in page and f'href="/exports?register={token}#register"' in page
    assert "Load these vouchers" not in page
    wrong = client.post("/jde-files/vouchers/load", data={"token": token}, headers=PAGE)
    assert wrong.status_code == 400 and wrong.json()["error"] == jde_files.NOT_VOUCHERS
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
