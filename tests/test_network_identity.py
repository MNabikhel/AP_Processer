"""W2-02 (docs/BUGS.md N-57): the dashboard shared on the office network (``AP_DASHBOARD_ADDRESS=0.0.0.0``) knows
who each person is. Each browser on another computer gives its name once ("Who are you?"); the name is kept in a
cookie the server signs, so it cannot be made up. Approvals, the audit trail and the review screen's unsaved edits
are then that person's, and this computer's own browser works as before."""

from __future__ import annotations

import shutil

import pytest
from fastapi.testclient import TestClient

from ap_coder.core.config import Settings
from ap_coder.core.store import APPROVED, PENDING, REVIEW, Store
from ap_coder.ops.demo import load_demo
from ap_coder.web import identity
from ap_coder.web.app import create_app

from .webclient import PAGE, web_settings


@pytest.fixture(scope="module")
def demo_db(tmp_path_factory):
    path = tmp_path_factory.mktemp("demo") / "private" / "ap_coder.db"
    load_demo(Store(path), Settings())
    return path


@pytest.fixture
def store(demo_db, tmp_path):
    target = tmp_path / "private" / "ap_coder.db"
    target.parent.mkdir(parents=True)
    shutil.copy(demo_db, target)
    return Store(target)


@pytest.fixture
def app(store, monkeypatch):
    monkeypatch.setenv("AP_REVIEWER", "Server Owner")  # the name of the person on the computer running AP Coder
    return create_app(web_settings(), store, address="0.0.0.0", start_reader=False)


def colleague(app, address: str, name: str | None = None) -> TestClient:
    """A browser on another computer of the office network; with ``name``, it has said who it is."""
    client = TestClient(app, base_url="http://localhost", client=(address, 50000), follow_redirects=False)
    if name:
        answer = client.post("/who", data={"name": name, "next": "/review"}, headers=PAGE)
        assert answer.status_code == 200 and answer.json()["ok"], answer.text
    return client


def this_computer(app) -> TestClient:
    return TestClient(app, base_url="http://localhost", client=("127.0.0.1", 50000), follow_redirects=False)


def test_a_browser_on_another_computer_is_asked_who_it_is_first(app):
    client = colleague(app, "10.0.0.5")
    first = client.get("/review")
    assert first.status_code == 303 and first.headers["location"].startswith("/who")
    assert "Who are you" in client.get(first.headers["location"]).text
    refused = client.post("/review/bulk-approve", data={}, headers=PAGE)
    assert refused.status_code == 403 and "who you are" in refused.json()["error"]
    assert client.get("/healthz").status_code == 200  # the launcher's check and the page's styles need no name
    assert client.get("/static/css/app.css").status_code in (200, 404)
    answer = client.post("/who", data={"name": "  Ana Silva ", "next": "/review"}, headers=PAGE)
    assert answer.json()["ok"] and answer.json()["redirect"].startswith("/review")
    page = client.get("/review")
    assert page.status_code == 200 and "Ana Silva" in page.text and "Server Owner" not in page.text


def test_a_name_is_needed_and_the_next_page_stays_on_this_server(app):
    client = colleague(app, "10.0.0.5")
    assert not client.post("/who", data={"name": "  "}, headers=PAGE).json()["ok"]
    answer = client.post("/who", data={"name": "Ana", "next": "https://example.com/x"}, headers=PAGE).json()
    assert answer["ok"] and not answer["redirect"].startswith("http")


def test_a_made_up_or_changed_name_cookie_is_refused(app):
    client = colleague(app, "10.0.0.5", "Ana")
    real = client.cookies.get(identity.COOKIE)
    assert real and "Ana" not in real.split(".")[-1]  # the signature, not the name, ends it
    forged = colleague(app, "10.0.0.6")
    forged.cookies.set(identity.COOKIE, real.replace(identity.encode_name("Ana"), identity.encode_name("Boss")))
    assert forged.get("/review").status_code == 303
    forged.cookies.set(identity.COOKIE, "Qm9zcw.not-a-signature")
    assert forged.get("/review").status_code == 303
    other_server = create_app(web_settings(), Store(app.state.store.path), address="0.0.0.0", start_reader=False)
    stranger = colleague(other_server, "10.0.0.5")
    stranger.cookies.set(identity.COOKIE, real)  # signed by another start of AP Coder
    assert stranger.get("/review").status_code == 303


def test_two_colleagues_give_the_two_approvals_and_the_audit_trail_names_each(app, store):
    store.set_setting("approval_limit", "1")
    ana, ben = colleague(app, "10.0.0.5", "Ana"), colleague(app, "10.0.0.6", "Ben")
    invoice_id = store.list_invoices(REVIEW)[0]["id"]
    inv = store.get_invoice(invoice_id)
    store.approve_invoice(invoice_id, inv["ai_output"], "Ana", login=identity.network_login("10.0.0.5"))
    assert store.get_invoice(invoice_id)["status"] == PENDING
    # Ana may not give the second approval, even under another name from the same computer.
    assert "Not done" in ana.get(ana.post(f"/review/{invoice_id}/final-approve", headers=PAGE).json()["redirect"]).text
    ana2 = colleague(app, "10.0.0.5", "Someone Else")
    ana2.post(f"/review/{invoice_id}/final-approve", headers=PAGE)
    assert store.get_invoice(invoice_id)["status"] == PENDING
    answer = ben.post(f"/review/{invoice_id}/final-approve", headers=PAGE).json()
    assert answer["ok"], answer
    done = store.get_invoice(invoice_id)
    assert done["status"] == APPROVED and done["reviewer"] == "Ana" and done["second_reviewer"] == "Ben"
    final = next(e for e in store.events(invoice_id) if e["action"] == "final_approved")
    assert final["actor"] == "Ben" and final["detail"]["address"] == "10.0.0.6"


def test_an_action_from_the_network_records_the_person_and_their_address(app, store):
    ana = colleague(app, "10.0.0.5", "Ana")
    invoice_id = store.list_invoices(REVIEW)[0]["id"]
    answer = ana.post(f"/review/{invoice_id}/reject", data={"reason": "not ours"}, headers=PAGE).json()
    assert answer["ok"], answer
    event = next(e for e in store.events(invoice_id) if e["action"] == "rejected")
    assert event["actor"] == "Ana" and event["detail"]["address"] == "10.0.0.5"


def test_each_person_keeps_their_own_unsaved_edits(app, store):
    ana, ben = colleague(app, "10.0.0.5", "Ana"), colleague(app, "10.0.0.6", "Ben")
    invoice_id = store.list_invoices(REVIEW)[0]["id"]
    vendor = store.get_invoice(invoice_id)["ai_output"]["vendor_name"]
    assert ana.post(f"/review/{invoice_id}/check", data={"vendor_name": "Ana's Vendor"}, headers=PAGE).json()
    assert "Ana&#39;s Vendor" in ana.get(f"/review/{invoice_id}").text
    ben_page = ben.get(f"/review/{invoice_id}").text
    assert "Ana&#39;s Vendor" not in ben_page and vendor.split()[0] in ben_page
    ana.post(f"/review/{invoice_id}/start-over", headers=PAGE)
    assert "Ana&#39;s Vendor" not in ana.get(f"/review/{invoice_id}").text


def test_this_computers_browser_is_not_asked_and_keeps_its_name(app, store):
    me = this_computer(app)
    page = me.get("/review")
    assert page.status_code == 200 and "Server Owner" in page.text
    invoice_id = store.list_invoices(REVIEW)[0]["id"]
    me.post(f"/review/{invoice_id}/reject", data={"reason": "dup"}, headers=PAGE)
    event = next(e for e in store.events(invoice_id) if e["action"] == "rejected")
    assert event["actor"] == "Server Owner" and "address" not in event["detail"]


def test_on_this_computer_only_nobody_is_asked(store):
    """Listening on 127.0.0.1 (the default), no other computer can reach AP Coder: no name is asked."""
    app = create_app(web_settings(), store, start_reader=False)
    client = TestClient(app, base_url="http://localhost", follow_redirects=False)  # the test client's own address
    assert client.get("/review").status_code == 200


def test_a_colleague_changes_their_name_on_the_who_page_not_the_owners_setting(app):
    from ap_coder.core import paths

    ana = colleague(app, "10.0.0.5", "Ana")
    answer = ana.post("/settings/review", data={"reviewer_name": "Not Ana", "threshold": "80"}, headers=PAGE)
    assert not answer.json()["ok"] and "/who" in answer.json()["error"]
    assert paths.read_user_settings().get("reviewer", "") == ""


def test_a_background_job_started_by_a_colleague_works_in_their_name():
    from ap_coder.web import core
    from ap_coder.web.jobs import Job

    token = identity.PERSON.set(identity.Person("Ana", "10.0.0.5"))
    try:
        job = Job("process")
        assert job.start(lambda job: {"who": core.reviewer()})
    finally:
        identity.PERSON.reset(token)
    assert job.wait(5) and job.result == {"who": "Ana"}


def test_a_colleagues_address_never_replaces_what_an_event_itself_records(store):
    """B-129: the address was written as ``detail["from"]``, which some events already use for the old value (the
    touchless limit, a supplier's state, a moved file), so a change made from the network lost what it changed from."""
    from ap_coder.core.store import EVENT_ORIGIN

    token = EVENT_ORIGIN.set("192.168.1.20")
    try:
        before = store.touchless_limit()
        store.set_touchless_limit(1234.0, "Ana")
    finally:
        EVENT_ORIGIN.reset(token)
    event = next(e for e in store.events() if e["action"] == "settings_changed")
    assert event["detail"]["from"] == before and event["detail"]["to"] == 1234.0
    assert event["detail"]["address"] == "192.168.1.20"


def test_invoices_read_in_parallel_keep_the_colleague_who_started_them(monkeypatch):
    """B-130: a colleague's "Process" job reads files in a thread pool, and pool threads did not carry the person or
    their address, so those invoices' events were recorded as nobody's."""
    from ap_coder.ai.pipeline import InvoicePipeline
    from ap_coder.core.store import EVENT_ORIGIN

    seen = []
    pipeline = InvoicePipeline.__new__(InvoicePipeline)
    monkeypatch.setattr(InvoicePipeline, "process", lambda self, p: seen.append(EVENT_ORIGIN.get()) or p, raising=False)
    token = EVENT_ORIGIN.set("10.0.0.5")
    try:
        pipeline.process_many(["a.pdf", "b.pdf", "c.pdf"], workers=3)
    finally:
        EVENT_ORIGIN.reset(token)
    assert seen == ["10.0.0.5"] * 3


def test_this_computers_browser_at_its_own_network_address_cannot_give_the_second_approval(app, store):
    """B-190: opened at this computer's own network address (http://192.0.2.2:8501, not localhost), the server PC's
    browser came from that address, not a loopback one, so it was taken for a colleague: the first approver could say
    another name on /who and give the second approval from the same computer (D-34 refuses that)."""
    from ap_coder.web import core

    store.set_setting("approval_limit", "1")
    invoice_id = store.list_invoices(REVIEW)[0]["id"]
    store.approve_invoice(invoice_id, store.get_invoice(invoice_id)["ai_output"], core.reviewer(), login=core.login())
    assert store.get_invoice(invoice_id)["status"] == PENDING
    same = TestClient(app, base_url="http://192.0.2.2:8501", client=("192.0.2.2", 50001), follow_redirects=False)
    assert same.get("/review").status_code == 200  # this computer: no "Who are you?"
    assert not same.post("/who", data={"name": "S. Owner"}, headers=PAGE).json()["ok"]
    same.post(f"/review/{invoice_id}/final-approve", headers=PAGE)
    assert store.get_invoice(invoice_id)["status"] == PENDING
    assert colleague(app, "10.0.0.5").get("/review").status_code == 303  # another computer is still asked


def test_two_colleagues_who_give_one_name_on_two_computers_keep_their_own_edits(app, store):
    """B-192: the drafts were kept by name only, so "Mike" on one computer and "mike" on another saw each other's
    unsaved edits on the review screen."""
    invoice_id = store.list_invoices(REVIEW)[0]["id"]
    mike_a, mike_b = colleague(app, "10.0.0.5", "Mike"), colleague(app, "10.0.0.9", "mike")
    mike_a.post(f"/review/{invoice_id}/check", data={"po_number": "PO-TYPED-BY-A"}, headers=PAGE)
    assert "PO-TYPED-BY-A" in mike_a.get(f"/review/{invoice_id}").text
    assert "PO-TYPED-BY-A" not in mike_b.get(f"/review/{invoice_id}").text


def test_a_colleague_who_changes_their_name_keeps_their_edits_and_can_start_over(app, store):
    """B-192: after a new name on /who, the edits left under the old one were no one's: not on the screen, not
    dropped by Start over, and bulk approval skipped the invoice until AP Coder restarted."""
    invoice_id = store.list_invoices(REVIEW)[0]["id"]
    ana = colleague(app, "10.0.0.6", "Ana")
    ana.post(f"/review/{invoice_id}/check", data={"po_number": "PO-ANA-1"}, headers=PAGE)
    assert ana.post("/who", data={"name": "Ana Silva"}, headers=PAGE).json()["ok"]
    assert "PO-ANA-1" in ana.get(f"/review/{invoice_id}").text
    ana.post(f"/review/{invoice_id}/start-over", headers=PAGE)
    assert not any(invoice_id in drafts for drafts in app.state.person_drafts.values())


def test_a_colleague_whose_computer_gets_a_new_address_keeps_their_edits_and_can_start_over(app, store):
    """B-221: the drafts were kept by (name, address), so after the office network gave a colleague's computer a new
    address (Wi-Fi to cable, a lease renewed) their edits were no one's: not on their screen, not dropped by Start
    over, and bulk approval skipped the invoice until AP Coder restarted. The name cookie now names the browser."""
    invoice_id = store.list_invoices(REVIEW)[0]["id"]
    ana = colleague(app, "10.0.0.6", "Ana")
    ana.post(f"/review/{invoice_id}/check", data={"po_number": "PO-ANA-2"}, headers=PAGE)
    moved = TestClient(app, base_url="http://localhost", client=("10.0.0.16", 50000), follow_redirects=False)
    moved.cookies = ana.cookies
    assert "PO-ANA-2" in moved.get(f"/review/{invoice_id}").text
    moved.post(f"/review/{invoice_id}/start-over", headers=PAGE)
    assert not any(invoice_id in drafts for drafts in app.state.person_drafts.values())
    # Two browsers that give one name still keep their own edits (B-192), on one address or two.
    mike_a, mike_b = colleague(app, "10.0.0.5", "Mike"), colleague(app, "10.0.0.5", "Mike")
    mike_a.post(f"/review/{invoice_id}/check", data={"po_number": "PO-TYPED-BY-A"}, headers=PAGE)
    assert "PO-TYPED-BY-A" not in mike_b.get(f"/review/{invoice_id}").text
    # A new name on /who keeps the browser, and its edits.
    assert mike_a.post("/who", data={"name": "Mike Roy"}, headers=PAGE).json()["ok"]
    assert "PO-TYPED-BY-A" in mike_a.get(f"/review/{invoice_id}").text


def test_a_link_from_another_site_keeps_who_the_colleague_is(app):
    """B-193: the name cookie was SameSite=Strict, so a link to the dashboard from another site (the intranet, a
    webmail) came without it and asked "Who are you?" again. Lax sends it on such a link (a GET, which never changes
    anything); a POST from another site is still refused by the header and origin checks (web/security.py)."""
    client = colleague(app, "10.0.0.5")
    answer = client.post("/who", data={"name": "Ana"}, headers=PAGE)
    cookie = answer.headers["set-cookie"].lower()
    assert "samesite=lax" in cookie and "httponly" in cookie
    refused = client.post(
        "/review/bulk-approve",
        data={},
        headers={**PAGE, "Origin": "http://example.com", "Sec-Fetch-Site": "cross-site"},
    )
    assert refused.status_code == 403


def test_an_ipv4_mapped_loopback_address_is_this_computer():
    assert identity.is_this_computer("::ffff:127.0.0.1")
    assert not identity.is_this_computer("::ffff:10.0.0.5")
