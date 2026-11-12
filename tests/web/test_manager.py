"""A manager confirms an invoice's cost allocation (D-138), on the screens: AP sends it from the review screen; on the
laptop AP downloads the request email and records the answer; in server mode a Manager account answers on screen,
sees only its own requests, and nothing else of AP Coder."""

from __future__ import annotations

import io
import re
from email import message_from_bytes
from email.policy import default

import pytest
from fastapi.testclient import TestClient

from ap_coder.core.store import PARKED, REVIEW
from ap_coder.core.store.managers import CONFIRMED, SENT_BACK, WAITING
from ap_coder.web.app import create_app
from ap_coder.web.identity import SESSION_COOKIE
from tests.stores import cost_centers_demo_store  # noqa: F401  (the tests' store)
from tests.web.test_web_invoice import form_of
from tests.webclient import PAGE, client_for, enrol, web_settings

GOOD = "correct horse battery"


def _coded_form(store, iid, center="CC-200"):
    """The review form with every line coded: a GL account and the cost center ``center``."""
    gl = next(a["code"] for a in store.list_accounts("gl_accounts"))
    with store._conn() as conn:  # the sample cost center list, with this one in it
        conn.execute("INSERT OR IGNORE INTO cost_centers (code, description, created_at) VALUES (?, '', '')", (center,))

    def coded(rows):
        return [{**r, "predicted_gl_code": gl, "predicted_cost_center": center} for r in rows]

    return form_of(store, iid, coded)


def _first(store):
    return store.list_invoices(REVIEW)[0]["id"]


def test_the_laptop_flow_send_the_email_record_the_answer_back_in_the_queue(store, tmp_path):
    client = client_for(store)
    iid = _first(store)
    form = {**_coded_form(store, iid), "manager_name": "Bob Manager", "manager_email": "bob@example.com",
            "manager_note": "New site", "remember_manager": "1"}  # fmt: skip
    answer = client.post(f"/review/{iid}/manager", data=form, headers=PAGE).json()
    assert answer["ok"], answer
    req = store.waiting_manager_request(iid)
    assert answer["redirect"].startswith(f"/manager/{req['id']}")
    assert store.get_invoice(iid)["status"] == PARKED and req["cost_centers"] == ["CC-200"]
    assert store.cost_center_managers()["CC-200"] == {"name": "Bob Manager", "email": "bob@example.com"}
    page = client.get(f"/review/{iid}").text  # parked, waiting for Bob, with the way to the request
    assert "Bob Manager to confirm the cost allocation" in page and f"/manager/{req['id']}" in page
    # B-1546: the coding shown while it waits is the one sent, not the coding as first read
    sent = re.search(r'<section class="card inv-posting">.*?</section>', page, re.S).group(0)
    assert "Coding sent to Bob Manager" in sent and req["lines"][0]["gl_code"] in sent and "CC-200" in sent
    assert "Bob Manager" in client.get("/manager").text  # in the list of requests waiting
    email = client.get(f"/manager/{req['id']}/email.eml")
    msg = message_from_bytes(email.content, policy=default)
    assert msg["To"] == "bob@example.com" and msg["X-Unsent"] == "1" and list(msg.iter_attachments())
    reply = ("RE confirm.eml", io.BytesIO(b"Subject: RE\n\nCONFIRMED, thanks"), "message/rfc822")
    answer = client.post(f"/manager/{req['id']}/record", headers=PAGE, files={"reply": reply},
                         data={"decision": CONFIRMED, "by": "Bob Manager"}).json()  # fmt: skip
    assert answer["ok"], answer
    done_req = store.manager_request(req["id"])
    assert done_req["status"] == CONFIRMED and done_req["via"] == "email" and done_req["reply_file"]
    assert (tmp_path / "private" / "emails" / "manager").is_dir()
    assert store.get_invoice(iid)["status"] == REVIEW
    assert "Bob Manager confirmed the coding" in client.get(f"/review/{iid}").text


def _recorded_reply(store, client) -> int:
    """A request sent and answered by email, the reply kept: its id."""
    iid = _first(store)
    form = {**_coded_form(store, iid), "manager_name": "Bob Manager", "manager_email": "bob@example.com"}
    assert client.post(f"/review/{iid}/manager", data=form, headers=PAGE).json()["ok"]
    rid = store.waiting_manager_request(iid)["id"]
    reply = ("RE confirm.eml", io.BytesIO(b"Subject: RE\n\nCONFIRMED, thanks"), "message/rfc822")
    answer = client.post(f"/manager/{rid}/record", headers=PAGE, files={"reply": reply},
                         data={"decision": CONFIRMED, "by": "Bob Manager"}).json()  # fmt: skip
    assert answer["ok"], answer
    return rid


def test_the_reply_kept_opens_after_a_restore_onto_a_data_folder_elsewhere(store, tmp_path):
    """The reply's path was kept whole (``C:\\Users\\ann\\APCoder\\emails\\manager\\...``): restored onto a new computer
    whose data folder is elsewhere, the reply was nowhere to be found (B-1552)."""
    import shutil

    from ap_coder.core.store import Store

    client = client_for(store)
    rid = _recorded_reply(store, client)
    assert store.manager_request(rid)["reply_file"].startswith("emails/manager/AP-")  # kept relative to the data folder
    assert f'href="/manager/{rid}/reply"' in client.get(f"/manager/{rid}").text
    assert client.get(f"/manager/{rid}/reply").content == b"Subject: RE\n\nCONFIRMED, thanks"
    backup = store.backup_now("manual")
    new = Store(tmp_path / "new computer" / "ap_coder.db")
    new.restore_state(backup)
    shutil.rmtree(tmp_path / "private")  # the old computer is gone
    opened = client_for(new).get(f"/manager/{rid}/reply")
    assert opened.status_code == 200 and opened.content == b"Subject: RE\n\nCONFIRMED, thanks"


@pytest.mark.parametrize(
    "old",
    ["D:\\APCoderData\\emails\\manager\\{name}", "/Users/ann/APCoder/emails/manager/{name}"],
    ids=["windows", "mac"],
)
def test_a_reply_kept_under_an_old_whole_path_is_found_in_the_data_folder(store, old):
    client = client_for(store)
    rid = _recorded_reply(store, client)
    name = store.manager_request(rid)["reply_file"].rsplit("/", 1)[1]
    with store._conn() as conn:  # as an AP Coder before B-1552 kept it, on another computer
        conn.execute("UPDATE manager_requests SET reply_file = ? WHERE id = ?", (old.format(name=name), rid))
    assert client.get(f"/manager/{rid}/reply").content == b"Subject: RE\n\nCONFIRMED, thanks"
    with store._conn() as conn:  # never a file outside the data folder's own places
        conn.execute("UPDATE manager_requests SET reply_file = ? WHERE id = ?", (str(store.path), rid))
    assert client.get(f"/manager/{rid}/reply").status_code == 404


def test_sending_needs_every_line_coded_and_a_manager(store):
    client = client_for(store)
    iid = _first(store)
    uncoded = form_of(store, iid, lambda rows: [{**r, "predicted_gl_code": ""} for r in rows])
    answer = client.post(f"/review/{iid}/manager", data={**uncoded, "manager_name": "Bob"}, headers=PAGE)
    assert answer.status_code == 400 and "GL account" in answer.json()["error"]
    answer = client.post(f"/review/{iid}/manager", data=_coded_form(store, iid), headers=PAGE)
    assert answer.status_code == 400 and "who the manager is" in answer.json()["error"]
    assert store.get_invoice(iid)["status"] == REVIEW


def test_sent_back_shows_what_to_change_and_a_changed_coding_is_said(store):
    client = client_for(store)
    iid = _first(store)
    client.post(f"/review/{iid}/manager", data={**_coded_form(store, iid), "manager_name": "Bob"}, headers=PAGE)
    rid = store.waiting_manager_request(iid)["id"]
    sent_back = {"decision": SENT_BACK, "by": "Bob", "note": "Line 1 is CC-300"}
    assert client.post(f"/manager/{rid}/record", data=sent_back, headers=PAGE).json()["ok"]
    assert "Line 1 is CC-300" in client.get(f"/review/{iid}").text
    # Sent again, confirmed, then the coding changed on screen: the screen says the lines are not the ones confirmed.
    client.post(f"/review/{iid}/manager", data={**_coded_form(store, iid), "manager_name": "Bob"}, headers=PAGE)
    rid = store.waiting_manager_request(iid)["id"]
    store.answer_manager_request(rid, CONFIRMED, by="Bob", actor="Bob")
    client.post(f"/review/{iid}/check", data=_coded_form(store, iid, center="CC-999"), headers=PAGE)
    assert "The coding has changed since" in client.get(f"/review/{iid}").text


def test_withdrawing_brings_it_back(store):
    client = client_for(store)
    iid = _first(store)
    client.post(f"/review/{iid}/manager", data={**_coded_form(store, iid), "manager_name": "Bob"}, headers=PAGE)
    rid = store.waiting_manager_request(iid)["id"]
    assert client.post(f"/manager/{rid}/withdraw", headers=PAGE).json()["ok"]
    assert store.get_invoice(iid)["status"] == REVIEW and store.manager_request(rid)["status"] == "withdrawn"


# --- Server mode ----------------------------------------------------------------------------------------------------


def _account(store, username, role, centers=""):
    uid = store.add_user(username, GOOD, role=role, display_name=username.title())
    with store._conn() as conn:
        conn.execute("UPDATE users SET must_change = 0, cost_centers = ? WHERE id = ?", (centers, uid))
    enrol(store, uid)  # its authenticator app set up (D-146)
    return uid, store.start_session(uid)


@pytest.fixture
def server(store):
    app = create_app(web_settings(), store, start_reader=False, server=True)
    client = TestClient(app, base_url="http://localhost", client=("10.0.0.5", 50000), follow_redirects=False)
    _admin, admin = _account(store, "boss", "admin")
    clerk_id, clerk = _account(store, "ann", "clerk")
    bob_id, bob = _account(store, "bob", "manager", "CC-200")
    _sue_id, sue = _account(store, "sue", "manager", "CC-300")
    return client, {"admin": admin, "clerk": clerk, "bob": bob, "sue": sue}, bob_id


def _as(token):
    return {**PAGE, "Cookie": f"{SESSION_COOKIE}={token}"}


def test_a_manager_answers_on_screen_and_sees_only_its_own(server, store):
    client, tokens, bob_id = server
    iid = _first(store)
    form = {**_coded_form(store, iid), "manager_user": str(bob_id)}
    assert client.post(f"/review/{iid}/manager", data=form, headers=_as(tokens["clerk"])).json()["ok"]
    rid = store.waiting_manager_request(iid)["id"]
    assert store.manager_request(rid)["manager_user"] == bob_id and store.manager_request(rid)["manager_name"] == "Bob"
    # Bob sees it; Sue (another manager) sees neither it nor its pages; neither sees the review queue.
    assert "Bob" in client.get("/manager", headers=_as(tokens["bob"])).text
    assert client.get(f"/manager/{rid}", headers=_as(tokens["bob"])).status_code == 200
    assert client.get(f"/manager/{rid}", headers=_as(tokens["sue"])).status_code == 404
    assert client.get(f"/manager/{rid}/page/1.png", headers=_as(tokens["sue"])).status_code == 404
    assert client.post(f"/manager/{rid}/answer", data={"decision": CONFIRMED},
                       headers=_as(tokens["sue"])).status_code == 404  # fmt: skip
    assert client.get("/review", headers=_as(tokens["bob"])).status_code == 403
    assert client.get(f"/review/{iid}", headers=_as(tokens["bob"])).status_code == 403
    assert client.get(f"/manager/{rid}/email.eml", headers=_as(tokens["bob"])).status_code == 403
    assert client.post(f"/manager/{rid}/record", data={"decision": CONFIRMED},
                       headers=_as(tokens["bob"])).status_code == 403  # fmt: skip
    # A clerk can't answer for the manager on screen.
    assert client.post(f"/manager/{rid}/answer", data={"decision": CONFIRMED},
                       headers=_as(tokens["clerk"])).status_code == 403  # fmt: skip
    answer = client.post(f"/manager/{rid}/answer", data={"decision": CONFIRMED}, headers=_as(tokens["bob"])).json()
    assert answer["ok"] and store.manager_request(rid)["status"] == CONFIRMED
    assert store.manager_request(rid)["via"] == "screen" and store.get_invoice(iid)["status"] == REVIEW


def test_a_manager_lands_on_its_own_page(server, store):
    client, tokens, _ = server
    assert client.get("/", headers=_as(tokens["bob"])).headers["location"] == "/manager"
    assert client.get("/", headers=_as(tokens["clerk"])).headers["location"] == "/review"


def test_only_an_active_manager_account_can_be_picked(server, store):
    client, tokens, _ = server
    iid = _first(store)
    clerk = next(u["id"] for u in store.list_users() if u["username"] == "ann")
    form = {**_coded_form(store, iid), "manager_user": str(clerk)}
    answer = client.post(f"/review/{iid}/manager", data=form, headers=_as(tokens["clerk"]))
    assert answer.status_code == 400 and store.waiting_manager_request(iid) is None


def test_an_admin_sets_a_managers_cost_centers(server, store):
    client, tokens, bob_id = server
    answer = client.post(f"/admin/users/{bob_id}/cost-centers", data={"cost_centers": "CC-200; CC-410, CC-200"},
                         headers=_as(tokens["admin"])).json()  # fmt: skip
    assert answer["ok"] and next(u for u in store.list_users() if u["id"] == bob_id)["cost_centers"] == "CC-200, CC-410"
    assert store.waiting_manager_requests() == [] and WAITING == "waiting"


def test_in_a_browser_send_to_manager_sends_the_coding_on_screen(chromium, store):
    """The pop-up's fields belong to the invoice's form and its button posts it to /manager instead of /approve
    (``formaction``, app.js): in a real browser the invoice is parked and the request page opens."""
    import json as _json
    from dataclasses import replace as _replace

    from ap_coder.core.config import Settings
    from tests.browser import browser_on, wait_until

    iid = _first(store)
    gl = next(a["code"] for a in store.list_accounts("gl_accounts"))
    inv = store.get_invoice(iid)
    coding = inv["ai_output"]
    coding["line_items"] = [{**li, "predicted_gl_code": gl} for li in coding["line_items"]]
    with store._conn() as conn:
        conn.execute("UPDATE invoices SET ai_output = ? WHERE id = ?", (_json.dumps(coding), iid))
    s = Settings()
    app = create_app(_replace(s, llm=_replace(s.llm, provider="off")), store, start_reader=False)
    with browser_on(chromium, app) as (browser, base):
        page = browser.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{base}/review/{iid}")
        page.wait_for_selector(".iv-sheet:not(.loading)")
        page.click("[data-park] summary")
        page.fill("[data-manager] [name=manager_name]", "Bob Manager")
        page.fill("[data-manager] [name=manager_email]", "bob@example.com")
        page.click("[data-manager] button[formaction]")
        wait_until(page, lambda: "/manager/" in page.url, timeout=15)
        main = page.inner_text("main") if page.query_selector("main") else page.inner_text("body")
        assert "Bob Manager" in main and errors == [], main[:1500]
    req = store.waiting_manager_request(iid)
    assert req and req["manager_email"] == "bob@example.com" and store.get_invoice(iid)["status"] == PARKED
    assert all(li["gl_code"] == gl for li in req["lines"])


def test_in_a_browser_park_and_send_to_manager_share_one_panel(chromium, store):
    """B-1545: Send to manager was a second pop-up panel inside Park, placed at the same spot as Park's own form, so
    it covered it: the reason box could not be clicked. One panel holds both now, Park's form first."""
    from dataclasses import replace as _replace

    from ap_coder.core.config import Settings
    from tests.browser import browser_on

    iid = _first(store)
    s = Settings()
    app = create_app(_replace(s, llm=_replace(s.llm, provider="off")), store, start_reader=False)
    with browser_on(chromium, app) as (browser, base):
        page = browser.new_page(viewport={"width": 1366, "height": 900})
        page.goto(f"{base}/review/{iid}")
        page.wait_for_selector(".iv-sheet:not(.loading)")
        page.click("[data-park] summary")
        assert page.locator("[data-park] > .pop-body").count() == 1
        on_top = page.evaluate(
            "() => { const el = document.querySelector('[data-park] input[name=reason]');"
            " const r = el.getBoundingClientRect(); const top = document.elementFromPoint(r.x + r.width / 2,"
            " r.y + r.height / 2); return top === el || el.contains(top); }"
        )
        assert on_top
        page.fill("[data-park] input[name=reason]", "buyer to confirm")
        assert page.input_value("[data-park] input[name=reason]") == "buyer to confirm"


def test_read_only_sees_the_coding_sent_but_no_link_it_cannot_open(server, store):
    """B-1546: a Read-only account opening a parked invoice sees the coding sent to the manager, without the link to
    the request (which only AP and the manager can open)."""
    client, tokens, bob_id = server
    _ro_id, ro = _account(store, "rita", "readonly")
    iid = _first(store)
    form = {**_coded_form(store, iid), "manager_user": str(bob_id)}
    assert client.post(f"/review/{iid}/manager", data=form, headers=_as(tokens["clerk"])).json()["ok"]
    rid = store.waiting_manager_request(iid)["id"]
    page = client.get(f"/review/{iid}", headers=_as(ro))
    assert page.status_code == 200 and "Coding sent to Bob" in page.text and f"/manager/{rid}" not in page.text
    assert client.get(f"/manager/{rid}", headers=_as(ro)).status_code == 403
    assert f"/manager/{rid}" in client.get(f"/review/{iid}", headers=_as(tokens["clerk"])).text


def _split_form(store):
    """An invoice of two lines or more coded to CC-200 (its first line) and CC-300 (the others), and its form."""

    def lines(i):
        inv = store.get_invoice(i["id"])
        return (inv["final_output"] or inv["ai_output"] or {}).get("line_items") or []

    iid = next(i["id"] for i in store.list_invoices(REVIEW) if len(lines(i)) >= 2)
    gl = next(a["code"] for a in store.list_accounts("gl_accounts"))
    with store._conn() as conn:
        for code in ("CC-200", "CC-300"):
            conn.execute(
                "INSERT OR IGNORE INTO cost_centers (code, description, created_at) VALUES (?, '', '')", (code,)
            )

    def coded(rows):
        return [{**r, "predicted_gl_code": gl, "predicted_cost_center": "CC-200" if n == 0 else "CC-300"}
                for n, r in enumerate(rows)]  # fmt: skip

    return iid, form_of(store, iid, coded)


def test_remembering_the_manager_does_not_take_another_managers_cost_center(store):
    """B-2530: "Remember them as the manager of these cost centers" made the person picked the manager of every cost
    center on the invoice, another manager's too, without a word."""
    client = client_for(store)
    iid, form = _split_form(store)
    store.set_cost_center_manager(["CC-200"], "Ann Site", "ann@example.com", "AP")
    store.set_cost_center_manager(["CC-300"], "Bob Plant", "bob@example.com", "AP")
    form = {**form, "manager_name": "Ann Site", "manager_email": "ann.site@example.com", "remember_manager": "1"}
    answer = client.post(f"/review/{iid}/manager", data=form, headers=PAGE).json()
    assert answer["ok"], answer
    assert store.waiting_manager_request(iid)["cost_centers"] == ["CC-200", "CC-300"]
    known = store.cost_center_managers()
    assert known["CC-300"] == {"name": "Bob Plant", "email": "bob@example.com"}, known
    assert known["CC-200"] == {"name": "Ann Site", "email": "ann.site@example.com"}  # the same manager: kept up to date
    assert "CC-300 keeps its manager, Bob Plant" in client.get(answer["redirect"]).text


def test_the_manager_sees_only_their_own_cost_centers_marked(server, store):
    """B-2534: a Manager answering for CC-200 saw the CC-300 lines of the same invoice marked as theirs too."""
    client, tokens, bob_id = server
    iid, form = _split_form(store)
    sent = client.post(f"/review/{iid}/manager", data={**form, "manager_user": str(bob_id)},
                       headers=_as(tokens["clerk"]))  # fmt: skip
    assert sent.json()["ok"], sent.text
    rid = store.waiting_manager_request(iid)["id"]
    page = client.get(f"/manager/{rid}", headers=_as(tokens["bob"])).text
    marked = re.findall(r'<tr class="mine">.*?</tr>', page, re.S)
    assert marked and all("CC-200" in row and "CC-300" not in row for row in marked), marked


def test_the_request_email_marks_only_the_cost_centers_remembered_for_that_manager(store):
    """B-2534: on the laptop, the email marked every cost center of the invoice as the manager's."""
    client = client_for(store)
    iid, form = _split_form(store)
    store.set_cost_center_manager(["CC-300"], "Bob Plant", "bob@example.com", "AP")
    form = {**form, "manager_name": "Ann Site", "manager_email": "ann@example.com", "remember_manager": "1"}
    assert client.post(f"/review/{iid}/manager", data=form, headers=PAGE).json()["ok"]
    rid = store.waiting_manager_request(iid)["id"]
    msg = message_from_bytes(client.get(f"/manager/{rid}/email.eml").content, policy=default)
    marked = re.findall(r'<tr style="background:#fff4e0">.*?</tr>', msg.get_body(("html",)).get_content(), re.S)
    assert marked and all("CC-200" in row and "CC-300" not in row for row in marked), marked
