"""The review screen's third UI sign-off (B-820 on): the approve bar never overlaps at 1280 and up, the posting preview
fits its card, Alt+Enter in a description, Ctrl+Z undoes the approval just made, the lines' Description the widest
column, "Draft saved" only for a draft, a draft kept from before says so, one colour per finding, one message after
an Undo, and the page viewer's and the queue's small things."""

from __future__ import annotations

import re

from ap_coder.core.store import APPROVED, REVIEW
from ap_coder.erp import jde

from .browser import wait_until
from .test_web_invoice import PAGE, check, form_of
from .test_web_review_signoff import _id, _jde_on, _open, _overlap, _review, browser, demo_db, me, store

__all__ = ["browser", "demo_db", "me", "store"]  # the fixtures, shared


def _boxes_apart(page, *selectors: str) -> list[str]:
    """The pairs of the visible elements of ``selectors`` that overlap."""
    shown = [s for s in selectors if page.locator(s).count() and page.locator(s).first.is_visible()]
    return [f"{a} / {b}" for i, a in enumerate(shown) for b in shown[i + 1 :] if _overlap(page, a, b)]


# --- 1. The approve bar at 1366 and 1280 ----------------------------------------------------------------------------


def test_in_a_browser_not_ready_and_draft_saved_never_overlap_and_the_reason_has_room(browser, store):
    """B-820: with "Not ready" and "Draft saved" together the pill shrank to "Not ready…" under "Draft saved", and at
    1280 it ran into Park."""
    for vendor in ("Pacific", "Northwind"):  # an invoice, a credit note (its Approve button is longer)
        for width in (1366, 1280):
            page = _review(browser, store, vendor, width, 768)
            total = page.locator("[form=inv][name=grand_total]")
            total.fill(str(float(total.input_value()) + 10))
            page.wait_for_selector("[data-live=bar] .inv-hard")
            page.wait_for_function("document.querySelector('[data-checking]').textContent.startsWith('Draft saved')")
            pairs = _boxes_apart(page, "[data-live=bar] .inv-hard", "[data-checking]", "[data-park] > summary",
                                 ".rvw-math", ".rvw-post-sub")  # fmt: skip
            assert pairs == [], (vendor, width, pairs)
            whole = page.evaluate("""() => { const t = document.querySelector('[data-live=bar] .inv-hard .t');
                return t.scrollWidth <= t.clientWidth + 1; }""")
            assert whole, (vendor, width)  # the reason is said in full
            assert page.errors == []
            page.close()


# --- 2. The posting preview fits its card -----------------------------------------------------------------------------


def test_in_a_browser_the_posting_preview_shows_amount_and_total_without_a_scroll(browser, store):
    """B-821: Amount, Non-rec. tax and the Total sat behind a horizontal scroll at 1920 (and 1366, long names)."""
    _jde_on(store)
    for vendor, width, height in (("Pacific", 1920, 1080), ("Red River", 1366, 768), ("Services", 1366, 768)):
        page = _review(browser, store, vendor, width, height)
        found = page.evaluate("""() => { const w = document.querySelector('[data-live=posting] .rvw-gl');
            const r = w.getBoundingClientRect(); const amount = [...w.querySelectorAll('thead th')].pop();
            const total = w.querySelector('tfoot td:last-child').getBoundingClientRect();
            return {scroll: w.scrollWidth - w.clientWidth, amount: amount.textContent.trim(),
                    amountIn: amount.getBoundingClientRect().right <= r.right + 1,
                    totalIn: total.right <= r.right + 1}; }""")
        assert found == {"scroll": 0, "amount": "Amount", "amountIn": True, "totalIn": True}, (vendor, width, found)
        assert page.errors == []
        page.close()


# --- 3. Alt+Enter in a description ---------------------------------------------------------------------------------


def test_in_a_browser_alt_enter_in_a_description_inserts_a_line(browser, store):
    """B-822: the Enter blocker of the description box cancelled Alt+Enter before the line's keys saw it."""
    page = _review(browser, store, "Pacific")
    rows = page.locator("[data-grid=lines] > tr")
    before = rows.count()
    rows.first.locator("[name=line_description]").focus()
    page.keyboard.press("Alt+Enter")
    assert rows.count() == before + 1
    assert page.errors == []


# --- 4. Ctrl+Z right after approving ---------------------------------------------------------------------------------


def test_in_a_browser_ctrl_z_after_approving_undoes_the_approval(browser, store):
    """B-823: Ctrl+Z right after approving said "Nothing to undo." while the banner offered Undo."""
    invoice_id = _id(store, "Pacific")
    page = _review(browser, store, "Pacific")
    page.keyboard.press("Control+Enter")
    page.wait_for_selector(".flash form[action$='/undo-approve']")
    page.wait_for_load_state("networkidle")
    assert store.get_invoice(invoice_id)["status"] in (APPROVED, "pending")
    undo = page.locator(".flash form[action$='/undo-approve'] button")
    assert "Ctrl+Z" in (undo.get_attribute("title") or "") and undo.get_attribute("aria-keyshortcuts") == "Control+Z"
    page.evaluate("document.activeElement.blur()")
    page.keyboard.press("Control+z")
    page.wait_for_url(re.compile(rf"/review/{invoice_id}\b"), timeout=10000)
    page.wait_for_selector(".flash")
    assert store.get_invoice(invoice_id)["status"] == REVIEW
    assert "Approval undone" in page.inner_text("#flashes")
    assert page.errors == []


# --- 5. The lines' Description at 1920 --------------------------------------------------------------------------------


def test_in_a_browser_the_description_is_the_widest_column_at_1920(browser, store):
    """B-824: at 1920 the lines' Description was 110 px wide: four lines a description, cut off."""
    with store._conn() as conn:  # no cost centers, as in the pilot (D-53)
        conn.execute("DELETE FROM cost_centers")
    for qty in (False, True):
        page = _review(browser, store, "Harbourview", 1920, 1080)
        if qty:
            page.locator("[data-show-qty]").check()
        widths = page.evaluate("""() => { const tr = document.querySelector('[data-grid=lines] > tr');
            return [...tr.children].filter((td) => td.getClientRects().length)
              .map((td) => [td.className.split(' ')[0], td.getBoundingClientRect().width]); }""")
        desc = dict(widths)["c-desc"]
        assert all(desc >= w for _, w in widths), widths
        assert desc >= 170 and dict(widths)["c-gl"] >= 130, widths
        page.close()


# --- 6. and 7. Drafts: said only when kept; a draft from before says so --------------------------------------------


def test_in_a_browser_ticking_approve_anyway_says_no_draft_saved(browser, store):
    """B-825: ticking "Approve anyway" said "Draft saved" though nothing was kept."""
    page = _review(browser, store, "Cascade")
    page.locator("[data-live=bar] [name=override]").check()
    page.wait_for_timeout(1200)
    assert "Draft saved" not in page.inner_text("[data-checking]")
    assert page.errors == []


def test_a_draft_kept_from_before_says_so_with_discard(client, store):
    """B-826: a draft restored after a restart or on coming back looked like the invoice as read."""
    invoice_id = _id(store, "Pacific")
    assert "Unsaved edits made" not in client.get(f"/review/{invoice_id}").text
    check(client, invoice_id, form_of(store, invoice_id, payment_terms="Net 45 EOM"))
    page = client.get(f"/review/{invoice_id}").text
    assert re.search(r"Unsaved edits made today at \d{1,2}:\d\d [ap]\.m\.", page)
    assert f'action="/review/{invoice_id}/start-over"' in page.split("Unsaved edits made", 1)[1][:600]


# --- 8. One colour per finding ----------------------------------------------------------------------------------------


def test_in_a_browser_notes_under_the_vendor_take_their_findings_colour(browser, store):
    """B-827: "The GST/HST number printed is …'s" was orange under the vendor but "Good to know" in the checks; with
    JD Edwards not set up a green "In the vendor list · V10057, not an AN8" sat above a red error."""
    page = _review(browser, store, "Pacific")
    page.locator("#vendor-name").fill("Brand New Vendor Ltd")  # the GST/HST number printed is Pacific's
    page.wait_for_selector("[data-vendor-match] button")
    assert "warn" not in page.get_attribute("[data-vendor-match]", "class")
    page.close()
    _jde_on(store)
    vendor = store.get_invoice(_id(store, "Prairie"))["ai_output"]["vendor_name"]
    jde.save(store, jde.JdeSettings(an8_overrides={jde.vendor_key(vendor): "V10104"}))  # a supplier number no AN8
    page = _review(browser, store, "Prairie")
    page.wait_for_selector(".inv-details [data-field=vendor_name] [data-flag]:not([hidden])")
    assert "ok" not in page.get_attribute("[data-vendor-match]", "class").split()
    assert page.errors == []


# --- 9. One message after an Undo ----------------------------------------------------------------------------------


def test_undoing_an_approval_says_so_once(client, store):
    """B-828: after the banner's Undo, a green "Approval undone" and an orange "Reopened by…" both showed."""
    invoice_id = _id(store, "Pacific")
    client.post(f"/review/{invoice_id}/approve", data={**form_of(store, invoice_id), "why_done": "1"}, headers=PAGE)
    undone = client.post(f"/review/{invoice_id}/undo-approve", headers=PAGE).json()
    page = client.get(undone["redirect"]).text
    assert "Approval undone" in page and "Reopened by" not in page


# --- 10. The small things ---------------------------------------------------------------------------------------------


def test_in_a_browser_zoom_out_from_fit_page_never_zooms_in(browser, store):
    """B-829: at 1920 Fit page was 45% while the zoom's floor was 50%: "−" zoomed in."""
    page = _review(browser, store, "Pacific", 1920, 1080)
    if "fields-open" not in (page.locator("#iv").get_attribute("class") or ""):
        page.locator(".iv-fold").click()  # the Fields list open above the page, as at 1920 by default
    page.locator(".iv-stage").click(position={"x": 5, "y": 5})
    page.keyboard.press("9")
    page.wait_for_timeout(300)
    zoom = lambda: int(page.inner_text(".iv-zoom, [data-zoom-label]").strip().rstrip("%"))  # noqa: E731
    fit = zoom()
    assert fit < 50, fit  # the whole page shows only below the 50% floor of before
    page.keyboard.press("-")
    page.wait_for_timeout(300)
    assert zoom() <= fit
    box = page.locator(".iv-stage").bounding_box()
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + min(box["height"] / 2, 200))
    page.keyboard.down("Control")
    page.mouse.wheel(0, 300)
    page.keyboard.up("Control")
    page.wait_for_timeout(300)
    assert zoom() <= fit
    assert page.errors == []


def test_in_a_browser_esc_on_the_page_menu_leaves_a_visible_focus(browser, store):
    page = _review(browser, store, "Pacific")
    page.locator(".iv-stage").focus()
    page.keyboard.press("Shift+F10")
    page.wait_for_selector(".cm[role=menu]")
    page.keyboard.press("Escape")
    ring = page.evaluate("""() => { const a = document.activeElement; const cs = getComputedStyle(a);
        return [a.classList.contains('iv-stage'), cs.outlineStyle !== 'none' && cs.outlineWidth !== '0px']; }""")
    assert ring == [True, True]
    assert page.errors == []


def test_in_a_browser_the_approved_view_has_the_page_menu(browser, store):
    invoice_id = next(i["id"] for i in store.list_invoices(APPROVED))
    page = _open(browser, f"/review/{invoice_id}")
    page.locator(".iv-stage").click(button="right", position={"x": 5, "y": 5})
    page.wait_for_selector(".cm[role=menu]")
    assert page.locator(".cm-item[data-cm=zoom-in]").count() == 1
    assert page.locator(".cm-item[data-cm=teach]").count() == 0  # nothing to teach on an approved invoice
    assert page.errors == []


def test_in_a_browser_alt_up_to_the_queue_focuses_the_invoice_left(browser, store):
    invoice_id = _id(store, "Pacific")
    page = _review(browser, store, "Pacific")
    page.evaluate("document.activeElement.blur()")
    page.keyboard.press("Alt+ArrowUp")
    page.wait_for_url(re.compile(r"/review(\?|$)"))
    page.wait_for_load_state("networkidle")
    wait_until(page, lambda: page.evaluate("document.activeElement.getAttribute('href')") == f"/review/{invoice_id}")
    assert page.evaluate("document.activeElement.getAttribute('href')") == f"/review/{invoice_id}"
    assert page.errors == []
    store.approve_invoice(invoice_id, store.get_invoice(invoice_id)["ai_output"], "Jordan Lee")
    page.goto(page.url.split("/review")[0] + f"/review/{invoice_id}")  # approved since: its view, then the queue
    page.wait_for_load_state("networkidle")
    page.keyboard.press("Alt+ArrowUp")
    page.wait_for_url(re.compile(r"/review(\?|$)"))
    page.wait_for_load_state("networkidle")
    wait_until(page, lambda: page.evaluate("document.activeElement.matches('.rq-list a.rq-row')"))
    assert page.evaluate("document.activeElement.matches('.rq-list a.rq-row')")  # no longer listed: the first row


def test_a_bad_dates_example_never_breaks_across_lines(client, store):
    invoice_id = _id(store, "Pacific")
    answer = check(client, invoice_id, form_of(store, invoice_id, invoice_date="31/31/2026"))
    note = answer["header"]["flags"]["invoice_date"]
    assert "2026‑10‑05" in note and "2026-10-05" not in note
