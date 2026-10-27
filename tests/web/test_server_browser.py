"""Server mode in a browser, as each role (D-97, D-98): log in, what the menu and the pages offer, what is refused,
log out."""

from __future__ import annotations

import pytest

from ap_coder.core.store import Store, load_sample_setup
from ap_coder.web.app import create_app
from tests.browser import browser_on
from tests.conftest import SAMPLE_STEM
from tests.webclient import add_clean, known_vendors, web_settings

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
    return store


@pytest.fixture
def browser(chromium, store):
    with browser_on(chromium, create_app(web_settings(), store, start_reader=False, server=True)) as opened:
        yield opened


def _log_in(browser, username: str, password: str = GOOD, path: str = "/review", lands: str = ""):
    chromium, base = browser
    page = chromium.new_page(viewport=LAPTOP)
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(base + path)
    assert "/login" in page.url  # nothing shows before logging in
    page.fill("input[name=username]", username)
    page.fill("input[name=password]", password)
    page.click("button:has-text('Log in')")
    page.wait_for_url(f"**{lands or path}*")
    return page, errors


def _menu(page) -> set[str]:
    return {t.strip() for t in page.locator(".rail .nav-link .label").all_inner_texts()}


@pytest.mark.parametrize("username", list(ROLES))
def test_in_a_browser_each_role_logs_in_sees_what_it_may_and_logs_out(browser, store, username):
    role = ROLES[username]
    page, errors = _log_in(browser, username)
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


def test_in_a_browser_a_temporary_password_is_changed_first(browser, store):
    store.add_user("newbie", GOOD, role="clerk", display_name="New Bie")  # must choose their own
    page, errors = _log_in(browser, "newbie", lands="/account/password")
    assert "Choose your own password" in page.content()
    page.fill("input[name=current]", GOOD)
    page.fill("input[name=new]", "my very own password")
    page.fill("input[name=again]", "my very own password")
    page.click("button:has-text('Save the new password')")
    page.wait_for_url("**/review*")
    assert "Password changed" in page.content()
    assert errors == []


def test_in_a_browser_read_only_opens_an_invoice_without_a_refusal(browser, store):
    """B-1100: opening an invoice posted its check, which Read-only is refused (a 403 toast); B-1101: its fields
    could be typed in. Read-only sees the stored checks, posts nothing and changes nothing."""
    page, errors = _log_in(browser, "reader")
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
