"""JD Edwards files (ap_coder/web/pages/jde_files.py): the made-up company's five exports loaded through the page,
each summary shown and the data really kept; a wrong file in a box answers with the importer's plain words."""

from __future__ import annotations

import re

import pytest
from markupsafe import escape

from ap_coder.bench import jde_exports as made
from ap_coder.ops import journal_cmd
from ap_coder.web import files, roles
from ap_coder.web.app import create_app
from ap_coder.web.pages import jde_files
from tests.webclient import PAGE, follow, web_settings

ROUTES = ["/jde-files/chart", "/jde-files/units", "/jde-files/chart/settings", "/jde-files/suppliers/upload",
          "/jde-files/suppliers/load", "/jde-files/journal/upload", "/jde-files/journal/load",
          "/jde-files/journal/forget", "/jde-files/vouchers/upload", "/jde-files/vouchers/load"]  # fmt: skip


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
