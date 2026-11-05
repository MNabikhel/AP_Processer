"""Server mode in a browser, as each role (D-97, D-98): log in, what the menu and the pages offer, what is refused,
log out."""

from __future__ import annotations

import time

import pytest

from ap_coder.core import authenticator
from ap_coder.core.store import Store, load_sample_setup
from ap_coder.web.app import create_app
from tests.browser import browser_on
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
    _again(page, store, "april")
    page.wait_for_url(f"**{path}")
    page.wait_for_load_state("networkidle")
    assert page.locator('[form="inv"][name="invoice_number"]').input_value() == number
    assert page.evaluate("Object.keys(sessionStorage).filter(k => k.startsWith('apc-unsaved'))") == []
    assert errors == []
