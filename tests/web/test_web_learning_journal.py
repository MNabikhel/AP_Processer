"""Learning → Coding accuracy → Teach from past coding, with JD Edwards' Transaction Journal (R09321 saved as CSV):
the file is recognised, its summary shown before anything is written (the command's own lines), the objects in boxes,
"Load this journal", the journal listed, "Forget". A table of past lines goes the way it always did. The journal is a
made-up company's (``ap_coder.bench.jde_company``)."""

from __future__ import annotations

import csv
import html
import io
import re
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from ap_coder.bench import jde_company, jde_exports
from ap_coder.ops import journal_cmd
from ap_coder.web.app import create_app
from tests.webclient import PAGE, client_for, enrol, follow, post, sign_in, web_settings

AT = jde_exports.JOURNAL_POSITIONS


@pytest.fixture(scope="module")
def truth():
    return jde_company.build(1, "small")


@pytest.fixture(scope="module")
def data(truth):
    return jde_exports.journal_csv(truth)


def _upload(client, data, name="R09321.csv"):
    return client.post("/learning/history/upload", files={"file": (name, data, "text/csv")}, headers=PAGE)


def _token(answer):
    address = urlparse(answer.json()["redirect"])
    assert address.path == "/learning" and address.fragment == "history"
    return parse_qs(address.query)["journal"][0]


def _text(page):
    """A page's words, without its tags."""
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", page)))


def test_a_journal_is_summarised_loaded_listed_and_forgotten(store, data, truth, monkeypatch):
    monkeypatch.setenv("AP_REVIEWER", "Pat")
    client = client_for(store)
    answer = _upload(client, data)
    assert answer.status_code == 200, answer.text
    token = _token(answer)
    page = client.get(answer.json()["redirect"]).text
    # The summary is the command's, line for line, and nothing is written yet.
    seen = journal_cmd.look(store, data, "R09321.csv")
    words = _text(page)
    assert "R09321.csv · JD Edwards' Transaction Journal (report R09321). Nothing is loaded yet." in words
    assert len(seen.said) == 20 and all(line.strip() in words for line in seen.said)
    assert "Read: 120 vouchers with 405 lines, G/L dates 2024-01-15 to 2024-12-30." in words
    assert "Learned from: 154 coding lines on 103 vouchers." in words
    assert "Kept but not learned from: 16 affiliates' vouchers and 1 voucher that does not balance." in words
    assert store.journal_files() == [] and store.journal_stamp() == 0 and store.journal_objects()["trade"] == ()
    assert store.events(actions=["journal_imported", "settings_changed"]) == []
    # The suggested objects are in boxes, and go with "Load this journal" as they were shown.
    for name, value in (("trade", "2110"), ("intercompany", "2150"), ("tax", "2420")):
        assert f'<input type="text" name="{name}" value="{value}"' in page
        assert f'<input type="hidden" name="{name}" value="{value}">' in page
    assert "Load this journal" in page and 'action="/learning/journal/load"' in page and "disabled" not in page
    assert "Teach 4" not in page and "col_vendor_name" not in page  # not the table's column matching

    loaded = post(client, "/learning/journal/load", {"token": token, "trade": "2110", "intercompany": "2150",
                                                     "tax": "2420"})  # fmt: skip
    page = follow(client, loaded)
    assert "Loaded R09321.csv: 120 vouchers and 405 lines kept, 154 coding lines to learn from." in _text(page)
    assert len(store.journal_vouchers()) == len(truth.vouchers) == 120
    assert store.journal_objects() == {"trade": ("2110",), "intercompany": ("2150",), "tax": ("2420",)}
    (event,) = store.events(actions=["journal_imported"])
    assert event["actor"] == "Pat" and event["detail"]["vouchers"] == 120
    # Listed: the file, the period, the counts, who and when, with "Forget".
    words = _text(page)
    assert "1 JD Edwards journal loaded" in words and "JD Edwards journals loaded" in words
    (listed,) = store.journal_files()
    row = re.search(r"<tr><td>R09321\.csv</td>.*?</tr>", page, re.S).group(0)
    assert "2024-01-15 to 2024-12-30" in row and ">120<" in row and ">154<" in row and "Pat" in row
    assert listed["loaded_at"][:10] in row and 'action="/learning/journal/forget"' in row
    assert f'name="file_id" value="{listed["id"]}"' in row
    assert post(client, "/learning/journal/load", {"token": token}).status_code == 400  # the upload is used up

    forgotten = post(client, "/learning/journal/forget", {"file_id": listed["id"]})
    page = follow(client, forgotten)
    assert "Forgot R09321.csv: 120 vouchers removed." in _text(page) and "JD Edwards journals loaded" not in page
    assert store.journal_files() == [] and store.journal_vouchers() == []
    assert store.events(actions=["journal_forgotten"])[0]["actor"] == "Pat"
    again = post(client, "/learning/journal/forget", {"file_id": listed["id"]})
    assert again.status_code == 400 and again.json()["error"] == "That journal is not loaded any more."


def test_the_objects_can_be_changed_and_checked_again_before_loading(store, data):
    client = client_for(store)
    token = _token(_upload(client, data))
    page = client.get(f"/learning?journal={token}&trade=2110%2C+2150&intercompany=&tax=2420").text
    words = _text(page)
    assert "The trade payable objects: 2110, 2150 (as given)." in words
    assert "The intercompany payable objects: none (as given)." in words
    assert "Learned from: 194 coding lines on 119 vouchers." in words
    assert '<input type="text" name="trade" value="2110, 2150"' in page
    assert '<input type="hidden" name="intercompany" value="">' in page
    # An object no line has: said, and the button is off (the server refuses it too).
    page = client.get(f"/learning?journal={token}&trade=9999&intercompany=&tax=").text
    assert "This file cannot be loaded as it is" in page and "No line of this file has a trade payable object" in page
    assert re.search(r'<button class="btn primary" type="submit" disabled>', page)
    refused = post(client, "/learning/journal/load", {"token": token, "trade": "9999"})
    assert refused.status_code == 400 and refused.json()["error"].startswith("Not loaded: No line of this file")
    assert store.journal_files() == []
    # Loaded as the boxes said: no intercompany object, so no voucher is an affiliate's.
    loaded = post(client, "/learning/journal/load", {"token": token, "trade": "2110, 2150", "intercompany": "",
                                                     "tax": "2420"})  # fmt: skip
    assert loaded.status_code == 200
    (listed,) = store.journal_files()
    assert listed["affiliates"] == 0 and listed["objects"] == {"trade": ["2110", "2150"], "intercompany": [],
                                                               "tax": ["2420"]}  # fmt: skip


def test_the_same_journal_again_and_one_whose_total_differs(store, data, truth):
    client = client_for(store)
    assert post(client, "/learning/journal/load", {"token": _token(_upload(client, data))}).status_code == 200
    answer = _upload(client, data, "the same.csv")
    page = client.get(answer.json()["redirect"]).text
    assert "Loaded already" in page and "This file is loaded already" in page and "Load this journal" not in page
    same = post(client, "/learning/journal/load", {"token": _token(answer)})
    assert same.status_code == 200 and "This file is loaded already" in _text(follow(client, same))
    assert len(store.journal_files()) == 1 and len(store.events(actions=["journal_imported"])) == 1

    rows = jde_exports.journal_rows(truth)
    rows[-1][AT["credit"]] = "1.00"
    text = io.StringIO(newline="")
    csv.writer(text, lineterminator="\r\n").writerows(rows[:-9] + rows[-2:])  # rows missing, and another total
    cut = text.getvalue().encode("utf-8-sig")
    answer = _upload(client, cut, "cut.csv")
    page = client.get(answer.json()["redirect"]).text
    assert "The control total differs" in page and 'name="anyway" value="1" required' in page
    assert "Warning: the lines read do not add up to the report's Grand Total." in _text(page)
    token = _token(answer)
    refused = post(client, "/learning/journal/load", {"token": token})
    assert refused.status_code == 400 and "do not add up to the report's Grand Total" in refused.json()["error"]
    assert len(store.journal_files()) == 1
    loaded = post(client, "/learning/journal/load", {"token": token, "anyway": "1"})
    assert loaded.status_code == 200 and "already there from another file" in _text(follow(client, loaded))
    newest = store.journal_files()[0]
    assert (newest["name"], newest["control_total_differs"]) == ("cut.csv", True)
    # Every whole voucher of the cut file was there: none is counted twice. Its last one is cut short, so it is
    # another voucher than the whole one (which is why such a file is not loaded unless asked).
    cut_short = [v for v in store.journal_vouchers() if v["file_id"] == newest["id"]]
    assert newest["counted"] == len(cut_short) == 1 and len(store.journal_vouchers()) == 121
    assert cut_short[0]["balanced"] is False and cut_short[0]["gl_date"] == newest["last_date"]


def test_a_table_of_past_lines_still_goes_to_its_columns(store, data):
    client = client_for(store)
    table = "Supplier;Memo;GL Account;Dept\nAcme;Paper;6100;100\nAcme;Toner;6100;\n"
    answer = client.post("/learning/history/upload", files={"file": ("ap.csv", table.encode(), "text/csv")},
                         headers=PAGE)  # fmt: skip
    assert answer.status_code == 200 and "history=" in answer.json()["redirect"]
    page = client.get(answer.json()["redirect"]).text
    assert "Teach 2 lines" in page and '<option value="Supplier" selected>' in page and "Load this journal" not in page
    assert "the Transaction Journal (report R09321) saved as CSV is read as it comes" in _text(client.get(
        "/learning").text)  # fmt: skip
    gone = client.get("/learning?journal=not-a-token").text
    assert "That upload has expired" in gone
    assert post(client, "/learning/journal/load", {"token": "not-a-token"}).status_code == 400


def test_a_table_whose_header_looks_like_the_journals_can_still_be_read_as_a_table(store):
    client = client_for(store)
    table = "Vendor,Remark,Account Number,Dept\nAcme,Paper,6100,100\nAcme,Toner,6100,\n"
    answer = _upload(client, table.encode(), "lines.csv")
    token = _token(answer)  # (the header names an account number and a remark)
    page = client.get(answer.json()["redirect"]).text
    assert "This file cannot be loaded as it is" in page and "No voucher could be read from this file." in page
    assert "Not a Transaction Journal? Read it as a table of past lines" in page
    as_table = post(client, "/learning/journal/as-table", {"token": token})
    assert as_table.status_code == 200 and "history=" in as_table.json()["redirect"]
    page = client.get(as_table.json()["redirect"]).text
    assert "Teach 2 lines" in page and '<option value="Vendor" selected>' in page
    assert post(client, "/learning/journal/as-table", {"token": token}).status_code == 400  # used up
    assert store.journal_files() == []


def test_a_page_with_no_journal_says_nothing_of_one(store):
    page = client_for(store).get("/learning").text
    assert "JD Edwards journals loaded" not in page and "journal-summary" not in page


def test_only_an_admin_loads_or_forgets_a_journal_on_the_company_server(store, data):
    app = create_app(web_settings(), store, start_reader=False, server=True)
    clients = {}
    for role in ("clerk", "approver", "admin"):
        uid = store.add_user(f"the.{role}", "correct horse battery", role=role, display_name=f"The {role.title()}")
        with store._conn() as conn:
            conn.execute("UPDATE users SET must_change = 0 WHERE id = ?", (uid,))
        enrol(store, uid)  # its authenticator app set up (D-146)
        clients[role] = TestClient(app, base_url="https://localhost", client=("10.0.0.5", 50000),
                                   follow_redirects=False)  # fmt: skip
        assert sign_in(clients[role], f"the.{role}", "correct horse battery").status_code in (200, 303)
    for role in ("clerk", "approver"):
        client = clients[role]
        assert _upload(client, data).status_code == 403
        assert post(client, "/learning/journal/load", {"token": "x"}).status_code == 403
        assert post(client, "/learning/journal/forget", {"file_id": "1"}).status_code == 403
    admin = clients["admin"]
    answer = _upload(admin, data)
    assert answer.status_code == 200
    assert post(admin, "/learning/journal/load", {"token": _token(answer)}).status_code == 200
    (event,) = store.events(actions=["journal_imported"])
    assert event["actor"] == "The Admin"
    # A clerk sees the journal listed, without "Forget"; the Admin has it.
    page = clients["clerk"].get("/learning").text
    assert "JD Edwards journals loaded" in page and "/learning/journal/forget" not in page
    assert "/learning/journal/forget" in admin.get("/learning").text
