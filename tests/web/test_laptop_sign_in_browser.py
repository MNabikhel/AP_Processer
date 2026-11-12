"""The laptop's first sign-in in a browser (D-243), as its owner does it tomorrow: admin / admin, their own password,
the authenticator app from the key shown, then the review queue; logging out closes it again."""

from __future__ import annotations

from ap_coder.core import authenticator
from ap_coder.core.store import users
from ap_coder.core.store.sign_in import LAPTOP, SIGN_IN
from ap_coder.web.app import create_app
from tests.browser import browser_on
from tests.webclient import web_settings

CHOSEN = "correct horse battery"


def test_in_a_browser_the_laptops_first_sign_in_leads_into_ap_coder(chromium, store, monkeypatch):
    monkeypatch.setenv("AP_REVIEWER", "Pat Lee")
    store.set_setting(SIGN_IN, LAPTOP)
    with browser_on(chromium, create_app(web_settings(), store, start_reader=False)) as (browser, base):
        page = browser.new_page(viewport={"width": 1366, "height": 768})
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(base + "/review")
        assert "/login" in page.url and "password admin" in page.locator("main").inner_text()
        page.fill("input[name=username]", "admin")
        page.fill("input[name=password]", "admin")
        page.click("button:has-text('Log in')")
        page.wait_for_url("**/login/first*")
        assert page.input_value("input[name=name]") == "Pat Lee"  # the name on the laptop's approvals so far
        page.fill("input[name=new]", CHOSEN)
        page.fill("input[name=again]", CHOSEN)
        page.click("button:has-text('Save the password')")
        page.wait_for_url("**/login/authenticator*")
        assert page.locator("svg.apc-qr").count() == 1 and "users reset-2fa admin" in page.content()
        secret = page.locator("code[data-secret]").get_attribute("data-secret").replace(" ", "")
        page.fill("input[name=code]", authenticator.code(secret, users._clock()))
        page.click("button:has-text('Confirm')")
        page.wait_for_url("**/review*")
        foot = page.locator(".rail-foot").inner_text()
        assert "Pat Lee" in foot and "Log out" in foot and "Users" not in foot
        page.click("button:has-text('Log out')")
        page.wait_for_url("**/login*")
        page.goto(base + "/review")
        assert "/login" in page.url
        assert errors == []
