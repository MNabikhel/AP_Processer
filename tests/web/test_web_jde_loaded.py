"""What is loaded of JD Edwards' files, seen and changed (ap_coder/web/pages/jde_loaded.py, core/store/loaded.py,
D-221): each of the five lists of the made-up company opens from "What is loaded" as a table read a page at a time,
with a search, filters, a count and a CSV whose codes keep their leading zeros; a row of the chart, the business
units or the suppliers is changed by hand and Activity says who changed what; the journal and the vouchers are only
looked at."""

from __future__ import annotations

import csv
import html
import io
from urllib.parse import quote

import pytest

from ap_coder.core.store import by_hand, loaded
from ap_coder.web import roles
from ap_coder.web.app import create_app
from tests.webclient import PAGE, follow, web_settings

HERE = "/jde-files/loaded"


def _csv(client, address: str) -> list[list[str]]:
    answer = client.get(address)
    assert answer.status_code == 200 and answer.headers["content-type"].startswith("text/csv"), address
    return list(csv.reader(io.StringIO(answer.content.decode("utf-8-sig"))))


def _count(page: str) -> str:
    return page.split('id="loaded-count">', 1)[1].split("</span>", 1)[0]


def test_the_search_and_its_download_ignore_accents_and_the_case_of_any_letter(store):
    store.import_vendor_master([
        {"vendor_name": "HYDRO-QUÉBEC", "erp_id": "0410010", "gst": "100000000RT0001", "terms": "N30"},
        {"vendor_name": "Énergir inc.", "erp_id": "0410011", "gst": "", "terms": "N30"},
        {"vendor_name": "Cafe Brulerie", "erp_id": "0410012", "gst": "", "terms": "N30"},
    ])  # fmt: skip
    for typed, names in [("quebec", ["HYDRO-QUÉBEC"]), ("québec", ["HYDRO-QUÉBEC"]), ("QUEBEC", ["HYDRO-QUÉBEC"]),
                         ("énergir", ["Énergir inc."]), ("ENERGIR", ["Énergir inc."]), ("café", ["Cafe Brulerie"]),
                         ("hydro 0410010", ["HYDRO-QUÉBEC"]), ("québec énergir", [])]:  # fmt: skip
        found = store.loaded_page("suppliers", q=typed)
        assert [row["display_name"] for row in found["rows"]] == names, typed
        assert [row["display_name"] for batch in store.loaded_batches("suppliers", q=typed) for row in batch] == names
    assert store.loaded_page("suppliers", q="qu_bec")["count"] == 0  # (_ is still looked for as it is)


def test_a_search_word_of_nothing_but_accent_marks_is_left_out_not_looked_for_as_nothing(store):
    store.import_vendor_master([
        {"vendor_name": "HYDRO-QUÉBEC", "erp_id": "0410010", "gst": "", "terms": "N30"},
        {"vendor_name": "Cafe Brulerie", "erp_id": "0410012", "gst": "", "terms": "N30"},
    ])  # fmt: skip
    # A lone combining acute folds to "", a spacing acute or diaeresis to " ": the word was looked for as "%%"
    # (every row) or "% %" (only rows with a space), so "quebec ´" found nothing of HYDRO-QUÉBEC
    for typed in ("\u0301", "\u00b4", "\u00a8", "quebec \u00b4"):
        names = [row["display_name"] for row in store.loaded_page("suppliers", q=typed)["rows"]]
        alone = [row["display_name"] for row in store.loaded_page("suppliers", q=typed.replace("quebec", ""))["rows"]]
        assert alone == [row["display_name"] for row in store.loaded_page("suppliers")["rows"]], typed
        assert names == (["HYDRO-QUÉBEC"] if "quebec" in typed else alone), typed
    assert store.loaded_page("suppliers", q="cafe \u00b4")["count"] == 1  # (still a search of "cafe")


def test_each_of_the_five_lists_opens_from_what_is_loaded(chart_company_client, chart_company):
    client, store = chart_company_client, chart_company.store
    home = client.get("/jde-files").text
    for what in loaded.WHATS:
        assert f'<a href="{HERE}/{what}" data-see="{what}">' in home, what
        answer = client.get(f"{HERE}/{what}")
        found = store.loaded_page(what)
        assert answer.status_code == 200 and found["total"] > 0, what
        assert f"{found['total']:,} {found['noun']}" in _count(answer.text), what
        for column in found["columns"]:
            assert f">{html.escape(column.label, quote=False)}</th>" in answer.text.replace("&#39;", "'"), column
        assert answer.text.count("<tr") == 1 + len(found["rows"]) + (
            len(store.journal_files()) + 1 if what == loaded.JOURNAL else 0), what  # fmt: skip
    assert client.get(f"{HERE}/nothing").status_code == 404 and client.get(f"{HERE}/nothing.csv").status_code == 404


@pytest.mark.parametrize("what", loaded.WHATS)
def test_a_list_is_read_a_page_at_a_time_by_sqlite_never_whole(chart_company, what):
    store = chart_company.store
    total = store.loaded_page(what)["total"]
    size = max(2, total // 4)
    first, second = store.loaded_page(what, size=size), store.loaded_page(what, page=2, size=size)
    assert first["pages"] == -(-total // size) and len(first["rows"]) == size and first["page"] == 1
    assert second["rows"] and second["rows"][0] != first["rows"][0]
    assert store.loaded_page(what, page=10**6, size=size)["page"] == first["pages"]  # past the end: the last page
    paged = [row for n in range(1, first["pages"] + 1) for row in store.loaded_page(what, page=n, size=size)["rows"]]
    batches = list(store.loaded_batches(what, batch=size))
    assert all(len(batch) <= size for batch in batches) and [row for batch in batches for row in batch] == paged
    seen: list[str] = []
    with store.session(), store._conn() as conn:  # every read of rows names how many it takes
        conn.set_trace_callback(seen.append)
        store.loaded_page(what, q="a")
        list(store.loaded_batches(what, q="a", batch=size))
        conn.set_trace_callback(None)
    reads = [sql for sql in seen if sql.startswith("SELECT") and "COUNT(*)" not in sql and "DISTINCT" not in sql
             and "FROM settings" not in sql]  # fmt: skip
    assert len(reads) >= 2 and all(" LIMIT " in sql for sql in reads), reads


def test_the_search_the_filters_the_count_and_the_csv_keep_codes_as_text(chart_company_client, chart_company):
    client, store = chart_company_client, chart_company.store
    rows = [r for r in store.chart_accounts() if r.object]
    row = next(r for r in rows if r.bu.startswith("0") and r.description)
    word = row.description.split()[0]
    page = client.get(f"{HERE}/chart?q={quote(f'{row.bu}.{row.object} {word.upper()}')}").text
    assert f"<td>{row.bu}</td>" in page and "of " in _count(page) and "Show all" in page
    assert store.loaded_page("chart", q=f"{row.bu}.{row.object} {word}")["count"] >= 1
    assert store.loaded_page("chart", q="no-such-words-at-all")["count"] == 0
    assert "No row matches." in client.get(f"{HERE}/chart?q=no-such-words-at-all").text
    assert store.loaded_page("chart", q="100%_")["count"] == 0  # (% and _ are looked for as they are)
    inactive = sum(1 for r in store.chart_accounts() if r.posting_edit == "I")
    blank = sum(1 for r in store.chart_accounts() if r.posting_edit == "")
    assert store.loaded_page("chart", filters={"posting_edit": "I"})["count"] == inactive > 0
    assert store.loaded_page("chart", filters={"posting_edit": loaded.BLANK})["count"] == blank
    assert f"{inactive:,} of " in _count(client.get(f"{HERE}/chart?posting_edit=I").text)
    of_unit = [r for r in store.chart_accounts() if r.bu == row.bu]
    sheet = _csv(client, f"{HERE}/chart.csv?bu={row.bu}")
    assert sheet[0][:4] == ["Business unit", "Object", "Subsidiary", "Description"] and "Can be posted to" in sheet[0]
    assert [line[0] for line in sheet[1:]] == [row.bu] * len(of_unit)  # the code as text, its leading zeros kept
    assert len(_csv(client, f"{HERE}/chart.csv")) == 1 + len(store.chart_accounts())
    numbers = {v["erp_id"] for v in store.master_vendors()}
    suppliers = _csv(client, f"{HERE}/suppliers.csv")
    assert {line[0] for line in suppliers[1:]} == numbers and suppliers[0][0] == "Supplier number"
    assert not any("bank" in label.lower() for label in suppliers[0])
    coding = store.loaded_page("journal", filters={"kind": "coding"})
    assert 0 < coding["count"] < coding["total"] and {r["kind"] for r in coding["rows"]} == {"coding"}
    bill = store.loaded_page("vouchers")["rows"][0]
    found = store.loaded_page("vouchers", q=f"{bill['vendor_name'].split()[0]} {bill['invoice_number']}")
    assert bill in found["rows"] and found["count"] < found["total"]
    assert len(_csv(client, f"{HERE}/vouchers.csv?q={quote(bill['invoice_number'])}")) >= 2


def test_an_admin_changes_a_row_by_hand_and_the_page_and_activity_say_so(chart_company_client, chart_company):
    client, store = chart_company_client, chart_company.store
    listed = {r["code"] for r in store.list_accounts("gl_accounts")}
    row = next(r for r in store.chart_accounts() if r.object and r.posting_edit == "I" and r.code not in listed)
    key = by_hand.chart_key(row.bu, row.object, row.subsidiary)
    back = f"{HERE}/chart?q={row.bu}.{row.object}"
    page = client.get(back).text
    assert f'href="{html.escape(back)}&amp;edit={quote(key, safe="")}#edit"' in page and 'id="edit"' not in page
    form = client.get(f"{back}&edit={quote(key)}").text.split('id="edit"', 1)[1].split("</form>", 1)[0]
    assert f'name="description" value="{html.escape(row.description)}"' in form and 'name="postable" value="1">' in form
    assert 'name="meals" value="1" disabled' in form and "kept when a newer file is loaded" in form
    sent = {"key": key, "back": back, "description": "Changed by Pat", "postable": "1"}
    page = follow(client, client.post(f"{HERE}/chart/edit", data=sent, headers=PAGE))
    assert "Saved: 2 changes made by hand" in page and "Changed by Pat" in page and "data-by-hand" in page
    assert f'title="The file says: {html.escape(row.description)}"' in page and 'title="The file says: I"' in page
    assert row.code in {r["code"] for r in store.list_accounts("gl_accounts")}
    assert "Nothing changed." in follow(client, client.post(f"{HERE}/chart/edit", data=sent, headers=PAGE))
    assert "1 changed by hand, kept when a newer file is loaded" in client.get("/jde-files").text
    only = client.get(f"{HERE}/chart?by_hand=1").text
    assert _count(only).startswith("1 of ") and only.count("data-by-hand") == 1
    activity = client.get("/activity?view=table").text
    assert "Changed by hand" in activity and f"description {html.escape(row.description)} → Changed by Pat" in activity
    form = client.get(f"{back}&edit={quote(key)}").text.split('id="edit"', 1)[1].split("</form>", 1)[0]
    assert "Changed by hand" in form and f"the file says: {html.escape(row.description)}" in form
    refused = client.post(f"{HERE}/chart/edit", data={"key": "no\tsuch\trow", "description": "x"}, headers=PAGE)
    assert refused.status_code == 400 and "not in the chart of accounts any more" in refused.json()["error"]

    unit = store.business_units()[0]
    page = follow(client, client.post(f"{HERE}/units/edit", data={"key": unit.code, "name": "Renamed", "status":
                                                                  unit.status}, headers=PAGE))  # fmt: skip
    assert "Saved: 1 change made by hand" in page and store.business_units()[0].name == "Renamed"

    vendor = store.master_vendors()[0]
    form = client.get(f"{HERE}/suppliers?edit={quote(vendor['vendor_key'])}").text.split('id="edit"', 1)[1]
    form = form.split("</form>", 1)[0]
    assert 'name="erp_id"' not in form and "(from JD Edwards)" in form and 'name="status" value="1"' in form
    sent = {"key": vendor["vendor_key"], "display_name": vendor["display_name"], "expected_gst": "987654321RT0001",
            "status": "1", "erp_id": "999"}  # fmt: skip
    page = follow(client, client.post(f"{HERE}/suppliers/edit", data=sent, headers=PAGE))
    after = store.get_vendor(vendor["vendor_key"])
    assert "Saved: 2 changes made by hand" in page and after["erp_id"] == vendor["erp_id"]
    assert (after["expected_gst"], after["status"]) == ("987654321RT0001", "on_hold")
    assert "status not on hold → on hold" in client.get("/activity?view=table").text


def test_last_years_coding_and_the_vouchers_are_looked_at_not_changed(chart_company_client, chart_company):
    client, store = chart_company_client, chart_company.store
    before = (store.loaded_page("journal")["rows"], store.loaded_page("vouchers")["rows"])
    for what, forget in (("journal", "/jde-files/journal/forget"), ("vouchers", "/exports/register/clear")):
        page = client.get(f"{HERE}/{what}").text
        assert ">Change</a>" not in page and "/edit" not in page and f'action="{forget}"' in page, what
        assert "it is not changed here" in page, what
        refused = client.post(f"{HERE}/{what}/edit", data={"key": "1", "remark": "x"}, headers=PAGE)
        assert refused.status_code == 400 and "not changed here" in refused.json()["error"], what
    assert (store.loaded_page("journal")["rows"], store.loaded_page("vouchers")["rows"]) == before
    assert ">Change</a>" in client.get(f"{HERE}/chart").text


def test_on_the_server_a_clerk_and_above_see_the_lists_and_only_an_admin_changes_a_row(store):
    app = create_app(web_settings(), store, start_reader=False, server=True)
    for what in loaded.WHATS:
        for path in (f"{HERE}/{what}", f"{HERE}/{what}.csv"):
            assert all(roles.can(app, role, "GET", path) for role in ("clerk", "approver", "admin")), path
            assert not roles.can(app, "readonly", "GET", path) and not roles.can(app, "manager", "GET", path), path
        assert roles.can(app, "admin", "POST", f"{HERE}/{what}/edit")
        others = ("readonly", "clerk", "approver", "manager")
        assert not any(roles.can(app, role, "POST", f"{HERE}/{what}/edit") for role in others)
