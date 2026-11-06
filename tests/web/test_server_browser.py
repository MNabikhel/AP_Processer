"""Server mode in a browser, as each role (D-97, D-98): log in, what the menu and the pages offer, what is refused,
log out."""

from __future__ import annotations

import json
import threading
import time

import pytest

from ap_coder.core import authenticator
from ap_coder.core.store import Store, load_sample_setup
from ap_coder.web.app import create_app
from tests.browser import browser_on, wait_until
from tests.conftest import SAMPLE_STEM
from tests.webclient import add_clean, authenticator_code, enrol, known_vendors, web_settings

GOOD = "correct horse battery"
QC = "laurentides_QC_TPS_TVQ_SIL-4471"
LAPTOP = {"width": 1366, "height": 768}
ROLES = {"reader": "readonly", "clara": "clerk", "april": "approver", "boss": "admin"}


@pytest.fixture
def store(tmp_path):
    store = Store(tmp_path / "private" / "ap_coder.db")
    load_sample_setup(store)
    known_vendors(store, SAMPLE_STEM, QC)
    add_clean(store)  # two clean invoices: bulk approval offers them
    add_clean(store, QC)
    for username, role in ROLES.items():
        uid = store.add_user(username, GOOD, role=role, display_name=username.title())
        with store._conn() as conn:
            conn.execute("UPDATE users SET must_change = 0 WHERE id = ?", (uid,))
        enrol(store, uid)  # their authenticator app set up (D-146)
    return store


@pytest.fixture
def browser(chromium, store):
    with browser_on(chromium, create_app(web_settings(), store, start_reader=False, server=True)) as opened:
        yield opened


def _log_in(browser, store: Store, username: str, password: str = GOOD, path: str = "/review", lands: str = ""):
    chromium, base = browser
    page = chromium.new_page(viewport=LAPTOP)
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(base + path)
    assert "/login" in page.url  # nothing shows before logging in
    page.fill("input[name=username]", username)
    page.fill("input[name=password]", password)
    page.click("button:has-text('Log in')")
    page.wait_for_url("**/login/code*")  # the second step (D-146)
    page.fill("input[name=code]", authenticator_code(store, username))
    page.click("button:has-text('Log in')")
    page.wait_for_url(f"**{lands or path}*")
    return page, errors


def _menu(page) -> set[str]:
    return {t.strip() for t in page.locator(".rail .nav-link .label").all_inner_texts()}


@pytest.mark.parametrize("username", list(ROLES))
def test_in_a_browser_each_role_logs_in_sees_what_it_may_and_logs_out(browser, store, username):
    role = ROLES[username]
    page, errors = _log_in(browser, store, username)
    _, base = browser
    foot = page.locator(".rail-foot").inner_text()
    assert username.title() in foot and {"readonly": "Read-only"}.get(role, role.title()) in foot
    menu = _menu(page)
    assert {"Review queue", "Invoice directory", "Exports", "Help"} <= menu
    assert ("Settings" in menu) == (role == "admin")
    assert ("Import invoices" in menu) == (role != "readonly")
    assert ("GL accounts & tax" in menu) == (role != "readonly")
    bulk = page.locator("summary:has-text('Approve 2')")
    assert bulk.count() == (0 if role == "readonly" else 1)
    invoice = store.list_invoices("review")[0]["id"]
    page.goto(f"{base}/review/{invoice}")
    page.wait_for_selector(".inv-actionbar")
    assert page.locator("button[data-approve]").count() == (0 if role == "readonly" else 1)
    page.goto(f"{base}/settings")
    if role == "admin":
        assert page.locator("h1").inner_text().strip() == "Settings"
        page.goto(f"{base}/admin/users")
        page.fill("input[name=username]", "newcomer")
        page.fill("input[name=name]", "New Comer")
        page.click("button:has-text('Add')")
        page.wait_for_selector("#temporary")
        assert "newcomer" in page.locator("#temporary").inner_text()
    else:
        assert "Not allowed" in page.locator("h1").inner_text()
        assert "Your role" in page.content()
    page.click(".rail-foot button:has-text('Log out')")  # at once, no page to confirm it (B-1114)
    page.wait_for_url("**/login*")
    assert "You are logged out" in page.content()
    page.goto(f"{base}/review")
    assert "/login" in page.url
    assert errors == []


def test_in_a_browser_the_first_sign_in_sets_a_password_then_the_authenticator_app(browser, store):
    """A new clerk (D-146): the temporary password the Admin gave, their own password, the QR code to scan (the
    code here worked out from the secret shown beside it, as a phone would), then the review queue."""
    chromium, base = browser
    store.add_user("newbie", "abcd-efgh-jkmn-pqrs", role="clerk", display_name="New Bie")
    page = chromium.new_page(viewport=LAPTOP)
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(base + "/review")
    page.fill("input[name=username]", "newbie")
    page.fill("input[name=password]", "abcd-efgh-jkmn-pqrs")
    page.click("button:has-text('Log in')")
    page.wait_for_url("**/login/new-password*")
    assert "Choose your own password" in page.content()
    page.fill("input[name=current]", "abcd-efgh-jkmn-pqrs")
    page.fill("input[name=new]", "my very own password")
    page.fill("input[name=again]", "my very own password")
    page.click("button:has-text('Save the new password')")
    page.wait_for_url("**/login/authenticator*")
    assert page.locator("svg.apc-qr").is_visible()
    assert "Microsoft Authenticator" in page.content()
    secret = page.locator("[data-secret]").get_attribute("data-secret").replace(" ", "")
    page.fill("input[name=code]", authenticator.code(secret, time.time()))
    page.click("button:has-text('Confirm')")
    page.wait_for_url("**/review*")
    assert "New Bie" in page.locator(".rail-foot").inner_text()
    assert errors == []


def test_in_a_browser_read_only_opens_an_invoice_without_a_refusal(browser, store):
    """B-1100: opening an invoice posted its check, which Read-only is refused (a 403 toast); B-1101: its fields
    could be typed in. Read-only sees the stored checks, posts nothing and changes nothing."""
    page, errors = _log_in(browser, store, "reader")
    _, base = browser
    posts: list[str] = []
    page.on("request", lambda r: posts.append(r.url) if r.method == "POST" else None)
    invoice = store.list_invoices("review")[0]["id"]
    page.goto(f"{base}/review/{invoice}")
    page.wait_for_selector("[data-live=checks] .rvw-status")
    page.wait_for_timeout(600)
    assert posts == []
    assert page.locator("#toasts > *").count() == 0
    box = page.locator('[form="inv"][name="invoice_number"]')
    assert box.evaluate("el => el.readOnly")
    box.click()
    page.keyboard.type("X")
    assert "X" not in box.input_value()
    assert page.locator('[data-grid="lines"] .pk-input').first.evaluate("el => el.readOnly")
    assert page.locator("[data-add-row], [data-vendor-add], [data-note-form]").count() == 0
    assert errors == []


def _again(page, store: Store, username: str) -> None:
    """Log in on the login page this page was sent to (its code step too)."""
    page.wait_for_url("**/login?next=*")
    page.fill("input[name=username]", username)
    page.fill("input[name=password]", GOOD)
    page.click("button:has-text('Log in')")
    page.wait_for_url("**/login/code*")
    page.fill("input[name=code]", authenticator_code(store, username))
    page.click("button:has-text('Log in')")


def _session_ends(store: Store, username: str) -> None:
    store.end_sessions(store.find_user(username)["id"])


@pytest.mark.parametrize("by", ["check", "approve"])
def test_in_a_browser_edits_made_after_the_session_ended_come_back_after_logging_in_again(browser, store, by):
    """B-1594: an edit made after the session had ended was lost (its check refused, the edit kept nowhere), and
    Approve sent the person to /login with no way back: logging in again opened the queue, not the invoice. The
    edits are kept in the tab, the login comes back to the invoice, and they are its draft again."""
    invoice = store.list_invoices("review")[0]["id"]
    path = f"/review/{invoice}?sort=amount"
    page, errors = _log_in(browser, store, "clara", path=path)
    page.wait_for_selector("[data-approve]:not([disabled])")
    _session_ends(store, "clara")
    box = page.locator('[form="inv"][name="invoice_number"]')
    box.fill("AFTER-THE-END")
    if by == "approve":
        page.click("[data-approve]")  # at once: the check of the edit is not sent first
    _again(page, store, "clara")
    page.wait_for_url(f"**{path}")
    page.wait_for_function("() => document.querySelector('[form=inv][name=invoice_number]').value === 'AFTER-THE-END'")
    page.wait_for_load_state("networkidle")
    assert store.get_invoice(invoice)["status"] == "review"  # nothing approved behind the login
    page.reload()  # kept on the server as the draft, not only in the tab
    assert page.locator('[form="inv"][name="invoice_number"]').input_value() == "AFTER-THE-END"
    assert errors == []


def test_in_a_browser_edits_kept_when_the_session_ended_are_never_shown_to_someone_else(browser, store):
    """B-1594: the edits kept in the tab when the session ended are only ever put back for the person who made them;
    someone else logging in on that tab never sees them, nor does the first person afterwards."""
    invoice = store.list_invoices("review")[0]["id"]
    path = f"/review/{invoice}"
    page, errors = _log_in(browser, store, "clara", path=path)
    page.wait_for_selector("[data-approve]:not([disabled])")
    number = page.locator('[form="inv"][name="invoice_number"]').input_value()
    _session_ends(store, "clara")
    page.locator('[form="inv"][name="invoice_number"]').fill("CLARAS-EDIT")
    page.wait_for_url("**/login?next=*")
    kept = json.loads(page.evaluate(f"sessionStorage.getItem('apc-unsaved-{invoice}')") or "null")
    assert kept and kept["account"] == "clara" and ["invoice_number", "CLARAS-EDIT"] in kept["fields"]  # kept: B-1678
    _again(page, store, "april")
    page.wait_for_url(f"**{path}")
    page.wait_for_load_state("networkidle")
    assert page.locator('[form="inv"][name="invoice_number"]').input_value() == number
    assert page.evaluate("Object.keys(sessionStorage).filter(k => k.startsWith('apc-unsaved'))") == []
    assert errors == []


def _unanswered(page, base: str, invoice: int) -> str:
    """What the review screen says when AP Coder doesn't answer an edit's check."""
    page.goto(f"{base}/review/{invoice}")
    page.wait_for_selector("[data-approve]:not([disabled])")
    page.route("**/check", lambda route: route.abort())
    page.locator('[form="inv"][name="invoice_number"]').fill("NO-ANSWER")
    page.wait_for_selector("#toasts > *")
    return page.locator("#toasts").inner_text()


def test_in_a_browser_no_answer_is_said_in_the_servers_words_on_the_server(browser, chromium, store):
    """B-1595: on the server, a request that got no answer told the person to "start it again with the AP Coder
    shortcut", which only a laptop has. The server's people are told the server didn't answer, and to tell IT if it
    keeps happening; a laptop keeps its own words."""
    invoice = store.list_invoices("review")[0]["id"]
    page, errors = _log_in(browser, store, "clara", path=f"/review/{invoice}")
    said = _unanswered(page, browser[1], invoice)
    assert "The AP Coder server didn't answer" in said and "tell IT" in said and "shortcut" not in said, said
    with browser_on(chromium, create_app(web_settings(), store, start_reader=False)) as (laptop, base):
        page = laptop.new_page(viewport=LAPTOP)
        said = _unanswered(page, base, invoice)
        assert "Start it again with the AP Coder shortcut" in said and "IT" not in said.split(), said
    assert errors == []


def _job_running(app) -> threading.Event:
    """An import under way in ``app`` until the event returned is set (the Import invoices page follows it): a file of
    the import queue being read (D-168)."""
    from ap_coder.core.store.folder import read_file

    store, release = app.state.store, threading.Event()
    path = store.path.parent / "in-line.pdf"
    path.write_bytes(b"%PDF-1.4 in line")
    store.queue_imports([read_file(path)], source="upload", person=True)
    row = store.claim_import("a reader", 600)

    def read() -> None:
        release.wait(60)
        store.finish_import(row["id"], "a reader", "done")

    threading.Thread(target=read, daemon=True).start()
    return release


def test_in_a_browser_a_job_page_stops_asking_once_the_session_ended(chromium, store):
    """B-1712: after the session ended, a page following a job (Import invoices) kept asking for its progress every
    0.8 s, each answered by the login page, for as long as the tab stayed open. Its look sent on to the login stops
    it: the page goes to the login, and comes back after it."""
    app = create_app(web_settings(), store, start_reader=False, server=True)
    release = _job_running(app)
    try:
        with browser_on(chromium, app) as opened:
            page, errors = _log_in(opened, store, "boss", path="/process")
            looks: list[float] = []
            page.on("request", lambda r: looks.append(time.monotonic()) if "/process/status" in r.url else None)
            wait_until(page, lambda: looks)
            _session_ends(store, "boss")
            ended = time.monotonic()
            wait_until(page, lambda: "/login" in page.url, timeout=6)
            assert "/login?next=%2Fprocess" in page.url
            page.wait_for_timeout(2500)
            assert len([t for t in looks if t > ended]) <= 1, looks
            _again(page, store, "boss")
            page.wait_for_url("**/process")
            assert errors == []
    finally:
        release.set()


def test_in_a_browser_the_review_screen_stops_following_the_reading_once_the_session_ended(browser, store):
    """While OvisOCR2 has the invoice in its queue, the review screen looks at its progress every 20 seconds: after the
    session ended it kept looking, each answered by the login page. It stops (B-1712); the next edit's check goes to
    the login and keeps the edits (B-1594)."""
    invoice = store.list_invoices("review")[0]["id"]
    store.queue_page_read(invoice, "not read yet", requested_by="Boss")
    page, errors = _log_in(browser, store, "boss", path="/review")
    looks: list[str] = []
    page.on("requestfinished", lambda r: looks.append(r.url) if f"/review/{invoice}/reader" in r.url else None)
    page.clock.install()
    page.goto(f"{browser[1]}/review/{invoice}")
    page.wait_for_selector("[data-reader][data-poll]", state="attached")
    page.clock.run_for(21_000)
    wait_until(page, lambda: len(looks) == 1)
    _session_ends(store, "boss")
    page.clock.run_for(21_000)
    wait_until(page, lambda: any("/login?next=" in u for u in looks))  # answered by the login (once the time moves on
    page.wait_for_timeout(300)  # in jumps, a look under way must end first, or its 30 s deadline would cut it)
    for _ in range(6):  # six minutes
        page.clock.run_for(60_000)
        page.wait_for_timeout(50)
    assert len([u for u in looks if u.split("?", 1)[0].endswith("/reader")]) == 2, looks  # (?seen=: D-160)
    assert "/review/" in page.url  # the screen stays as it is, with the edits
    assert errors == []
