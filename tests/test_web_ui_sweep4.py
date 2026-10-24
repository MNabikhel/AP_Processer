"""The dashboard's fourth UI sweep (B-990 on): the second approver sees why it is needed and what was approved over,
the screen's shortcuts work from the box the page focused, drafts in the queue, plain words for codes and settings,
and the small things."""

from __future__ import annotations

import re

from ap_coder.core.store import PENDING, REVIEW

from .test_web_invoice import PAGE, check, clean_invoice, form_of
from .test_web_review_signoff import _id, _open, _review, browser, client, demo_db, me, store

__all__ = ["browser", "client", "demo_db", "me", "store"]  # the fixtures, shared


def _waiting_for_second(store, client, currency: str = "USD") -> int:
    """An invoice approved over two errors (each with its reason), waiting for a second approval because there is no
    CAD rate for its currency."""
    store.set_setting("approval_limit", "15000")
    invoice_id = clean_invoice(client, store)
    inv = store.get_invoice(invoice_id)
    output = dict(inv["final_output"] or inv["ai_output"], currency=currency)
    issues = [
        {"code": "VENDOR_NOT_IN_MASTER", "severity": "error", "line": None, "override_reason": "US vendor, set up next week"},  # noqa: E501
        {"code": "TAX_CALC_MISMATCH", "severity": "error", "line": None, "override_reason": "Credit promised by email"},
        {"code": "TAX_NON_CANADIAN", "severity": "warning", "line": None},
    ]  # fmt: skip
    store.approve_invoice(invoice_id, output, "Dana Clerk", open_issues=issues, login="dclerk")
    assert store.get_invoice(invoice_id)["status"] == PENDING
    return invoice_id


# --- 1. The second approver sees why, and what was approved over --------------------------------------------------


def test_the_second_approval_says_the_stored_reason_and_each_error_approved_over(client, store):
    """B-990: a USD invoice with no CAD rate said "Invoices over 15,000.00 CAD need a second approver", and the
    reasons typed for "Approve anyway" were shown nowhere."""
    invoice_id = _waiting_for_second(store, client)
    for path in (f"/review/{invoice_id}", "/review?tab=second"):
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", client.get(path).text))
        assert "No CAD exchange rate for USD" in text, path
        assert "Invoices over 15,000.00 CAD need" not in text, path
        assert "Not in the vendor master" in text and "US vendor, set up next week" in text, path
        assert "Tax miscalculated" in text and "Credit promised by email" in text, path
        assert "VENDOR_NOT_IN_MASTER" not in text and "TAX_CALC_MISMATCH" not in text, path


def test_activity_says_the_second_approval_reason_and_the_errors_approved_over_in_plain_words(client, store):
    """B-991: Activity said "approved despite TAX_CALC_MISMATCH" and "over the approval limit" for a missing rate."""
    _waiting_for_second(store, client)
    text = client.get("/activity").text
    assert "approved despite TAX" not in text and "TAX_CALC_MISMATCH" not in text and "VENDOR_NOT_IN_MASTER" not in text
    assert "Approved over: Not in the vendor master (US vendor, set up next week)" in text
    assert "no CAD exchange rate for USD" in text and "over the approval limit" not in text


# --- 2. The screen's shortcuts from the box the page focused ---------------------------------------------------------


def _autofocused(page) -> str:
    page.wait_for_function("['INPUT', 'SELECT', 'TEXTAREA'].includes(document.activeElement?.tagName)")
    return page.evaluate("document.activeElement.name || document.activeElement.className")


def test_in_a_browser_ctrl_z_undoes_the_approval_from_the_box_the_next_invoice_focused(browser, store):
    """B-992: after an approval the next invoice focused its Vendor box, and from there Ctrl+Z did nothing though the
    banner said "Undo Ctrl+Z"; the box sat half under the approve bar at 1366."""
    invoice_id = _id(store, "Pacific")
    page = _review(browser, store, "Pacific")
    page.keyboard.press("Control+Enter")
    page.wait_for_selector(".flash form[action$='/undo-approve']")
    page.wait_for_load_state("networkidle")
    _autofocused(page)
    page.wait_for_timeout(300)
    at = page.evaluate("""() => [document.activeElement.getBoundingClientRect().top,
      document.activeElement.getBoundingClientRect().bottom,
      document.querySelector('.inv-actionbar').getBoundingClientRect().top]""")
    assert at[1] <= at[2] + 1 or at[0] >= at[2], at  # in view above the bar, or wholly below it (B-792)
    page.keyboard.press("Control+z")
    page.wait_for_url(re.compile(rf"/review/{invoice_id}\b"), timeout=10000)
    page.wait_for_selector(".flash")
    assert store.get_invoice(invoice_id)["status"] == REVIEW
    assert page.errors == []


def test_in_a_browser_ctrl_z_in_a_box_typed_in_stays_the_boxs_own(browser, store):
    """B-992: once something is typed in the box, Ctrl+Z is the box's own undo, never the approval's."""
    page = _review(browser, store, "Pacific")
    page.keyboard.press("Control+Enter")
    page.wait_for_selector(".flash form[action$='/undo-approve']")
    page.wait_for_load_state("networkidle")
    _autofocused(page)
    url = page.url
    page.keyboard.type("x")
    page.keyboard.press("Control+z")
    page.wait_for_timeout(800)
    assert page.url == url and page.locator(".flash form[action$='/undo-approve']").count()
    assert page.errors == []


def test_in_a_browser_alt_arrows_work_from_the_box_the_page_focused(browser, store):
    """B-993: Alt+← / Alt+→ / Alt+↑ did nothing from a text box, where the page puts the focus on opening."""
    page = _review(browser, store, "Pacific")
    _autofocused(page)
    page.keyboard.press("Alt+ArrowUp")
    page.wait_for_url(re.compile(r"/review(\?.*)?$"), timeout=10000)
    page.close()
    page = _review(browser, store, "Pacific")
    _autofocused(page)
    before = page.url
    page.keyboard.press("Alt+ArrowRight")
    page.wait_for_function(f"location.href !== {before!r}", timeout=10000)
    assert re.search(r"/review/\d+", page.url)
    assert page.errors == []


# --- 3. The meals note, the remark count ----------------------------------------------------------------------------


def _set_line_gl(store, invoice_id: int, line: int, gl: str) -> None:
    import json

    inv = store.get_invoice(invoice_id)
    doc = inv["ai_output"]
    doc["line_items"][line]["predicted_gl_code"] = gl
    with store._conn() as conn:
        conn.execute("UPDATE invoices SET ai_output = ? WHERE id = ?", (json.dumps(doc), invoice_id))


def test_in_a_browser_the_meals_note_wraps_at_1366(browser, store):
    """B-994: "Meals: 50% of the GST/HST is claimable. Billed to a client or a staff event? Use…" was cut off."""
    invoice_id = _id(store, "Harbourview")
    _set_line_gl(store, invoice_id, 3, "6320")
    page = _review(browser, store, "Harbourview")
    found = page.evaluate("""() => { const n = [...document.querySelectorAll('[data-meals-note]')]
        .find((x) => !x.hidden); const td = n.closest('td').getBoundingClientRect();
        const r = n.getBoundingClientRect();
        return {clipped: n.scrollWidth > n.clientWidth + 1, out: r.right > td.right + 1 || r.left < td.left - 1,
                text: n.textContent.trim()}; }""")
    assert found["text"].endswith("Use the other account.")
    assert not found["clipped"] and not found["out"], found
    assert page.errors == []


def test_in_a_browser_the_remark_count_shows_only_in_the_box_typed_in_and_says_what_jde_keeps(browser, store):
    """B-995: a bare "53/30" sat under most descriptions, over the box's corner, even when nobody typed."""
    from .test_web_review_signoff import _jde_on

    _jde_on(store)
    page = _review(browser, store, "Harbourview")
    page.evaluate("document.activeElement.blur()")
    page.wait_for_timeout(300)
    shown = page.evaluate("[...document.querySelectorAll('[data-remark]')].filter((n) => !n.hidden).length")
    assert shown == 0
    long = page.evaluate("""() => [...document.querySelectorAll('textarea[name=line_description]')]
        .findIndex((x) => x.value.trim().length > 30)""")
    box = page.locator("textarea[name=line_description]").nth(long)
    assert "JD Edwards keeps the first 30 characters" in (box.get_attribute("title") or "")
    box.focus()
    n = len(box.input_value().strip())
    said = page.evaluate("document.activeElement.closest('td').querySelector('[data-remark]').textContent")
    assert said.startswith(f"JD Edwards keeps 30 of {n} characters"), said
    assert page.errors == []


# --- 3. Drafts: the queue, and when they were made ------------------------------------------------------------------


def _queue_row(client, invoice_id: int) -> str:
    text = client.get("/review").text
    at = text.index(f'href="/review/{invoice_id}"')
    row = text[at : text.index("</li>", at)]
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", row))


def test_the_queue_row_says_the_drafts_own_blocker(client, store):
    """B-996: the queue still said "Pick a GL account for line 4" after line 4 was coded in a saved draft."""
    invoice_id = _id(store, "Harbourview")
    assert "Pick a GL account for line 4" in _queue_row(client, invoice_id)

    def code(rows):
        rows[3]["predicted_gl_code"] = "6800"
        return rows

    answer = check(client, invoice_id, form_of(store, invoice_id, lines=code))
    assert answer["draft"]
    row = _queue_row(client, invoice_id)
    assert "Pick a GL account for line 4" not in row
    assert "Edits not approved yet" in row, row


def test_a_draft_keeps_the_time_it_was_edited_and_says_it_plainly(client, store):
    """B-997: "Unsaved edits from 0:37" was when the invoice was last opened (the opening check saved the draft
    again), on a 24-hour clock."""
    invoice_id = _id(store, "Pacific")
    form = form_of(store, invoice_id, payment_terms="Net 45 EOM")
    check(client, invoice_id, form)
    kept = client.app.state.invoice_drafts[invoice_id]
    kept["saved_at"] = "2026-01-05T00:37"
    check(client, invoice_id, form)  # opened again: the same edits
    assert client.app.state.invoice_drafts[invoice_id]["saved_at"] == "2026-01-05T00:37"
    page = re.sub(r"\s+", " ", client.get(f"/review/{invoice_id}").text)
    assert "Unsaved edits made Jan 5 at 12:37 a.m." in page
    check(client, invoice_id, form_of(store, invoice_id, payment_terms="Net 60"))  # edited again: now
    assert client.app.state.invoice_drafts[invoice_id]["saved_at"] != "2026-01-05T00:37"
    assert re.search(r"Unsaved edits made today at \d{1,2}:\d\d [ap]\.m\.", client.get(f"/review/{invoice_id}").text)


# --- 3. The end of the list ------------------------------------------------------------------------------------------


def test_after_the_last_one_in_the_list_the_first_opens_as_the_message_says(client, store):
    """B-998: "That was the last one in the list… back to the first of them" opened the one before it ("Reviewing 8
    of 8")."""
    from ap_coder.web.pages.invoice import navigation

    ids = navigation(store, store.list_invoices(REVIEW)[0]["id"], {})["ids"]
    last = next(i for i in reversed(ids) if check(client, i)["allow"])
    rest = [i for i in ids if i != last]
    answer = client.post(f"/review/{last}/approve", data=form_of(store, last), headers=PAGE).json()
    assert answer["ok"], answer
    page = client.get(answer["redirect"]).text
    assert last == ids[-1]  # (the demo's last invoice can be approved as it is)
    assert "That was the last one in the list" in page and "back to the first" in page
    assert answer["redirect"].startswith(f"/review/{rest[0]}?")
    assert 'Reviewing <b class="n">1</b> of' in page


# --- 3. Help at 1366 ----------------------------------------------------------------------------------------------


def test_in_a_browser_help_does_not_scroll_sideways_at_1366(browser, store):
    """B-999: the Shortcuts table ran 68 px past the page at 1366: Help scrolled sideways."""
    page = _open(browser, "/help", wait_viewer=False)
    found = page.evaluate("[document.documentElement.scrollWidth, innerWidth]")
    assert found[0] <= found[1], found
    assert page.errors == []


# --- 3. Plain words for settings and provinces ---------------------------------------------------------------------


def test_activity_names_the_settings_changed_in_plain_words(client, store):
    """B-1000: Activity said "Settings changed · jde_e1 / our_province / approval_limit"."""
    for key in ("jde_e1", "our_province", "approval_limit", "show_jde_settings", "AP_REVIEW_THRESHOLD"):
        store.log_event("settings_changed", actor="Dana Clerk", detail={"keys": [key]})
    text = client.get("/activity").text
    for code in ("jde_e1", "our_province", "approval_limit", "show_jde_settings", "AP_REVIEW_THRESHOLD"):
        assert code not in text, code
    for words in ("JD Edwards E1 settings", "Our province", "Second approval limit", "Review threshold"):
        assert words in text, words


def test_the_jd_edwards_tax_areas_say_outside_canada_not_its_code(client, store):
    """B-1000: the JD Edwards tax areas listed "OUTSIDE_CANADA" as a province."""
    from .test_web_review_signoff import _jde_on

    _jde_on(store)
    text = client.get("/settings/jde").text
    shown = re.sub(r'value="[^"]*"', "", text)  # the form's own value may keep the code
    assert "OUTSIDE_CANADA" not in shown and "Outside Canada" in shown
