"""The review screen's second UI sign-off (B-790 on): the approve bar with "Approve anyway" at 1366, the tax lines
beside the page, the page kept at its top on opening, the approved view, the quiet remark counter, the shortcuts list,
one note under the vendor, the bar's first blocker in screen order, the menus on every part of the screen, confidence
without setup, the lines grid at 1920, the small things, and drafts that are real edits only and outlive a restart."""

from __future__ import annotations

import re
from types import SimpleNamespace

from ap_coder.coding import banks
from ap_coder.core.store import APPROVED, REJECTED, REVIEW
from ap_coder.erp import jde
from ap_coder.web import invoice_view

from . import test_web_invoice as screen
from .test_web_invoice import PAGE, check, form_of
from .webclient import client_for, web_settings

demo_db, store, client, me, browser = screen.demo_db, screen.store, screen.client, screen.me, screen.browser


def _id(store, vendor: str, status: str = REVIEW) -> int:
    return next(i["id"] for i in store.list_invoices(status) if vendor in (i["vendor_name"] or ""))


def _jde_on(store) -> None:
    store.set_setting(jde.SHOW_SETTING, "1")


def _open(browser, path: str, width=1366, height=768, wait_viewer=True):
    chromium, base = browser
    page = chromium.new_page(viewport={"width": width, "height": height})
    page.errors = []
    page.on("pageerror", lambda e: page.errors.append(str(e)))
    page.goto(f"{base}{path}")
    if wait_viewer:
        page.wait_for_selector(".iv-sheet:not(.loading)")
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(300)
    return page


def _review(browser, store, vendor, width=1366, height=768):
    return _open(browser, f"/review/{_id(store, vendor)}", width, height)


def _overlap(page, a: str, b: str) -> bool:
    return page.evaluate(
        """([a, b]) => { const x = document.querySelector(a).getBoundingClientRect();
        const y = document.querySelector(b).getBoundingClientRect();
        return x.left < y.right - 1 && y.left < x.right - 1 && x.top < y.bottom - 1 && y.top < x.bottom - 1; }""",
        [a, b],
    )


def _gst_off(store, invoice_id: int) -> None:
    """The GST charged 12.40 more than 5% of the lines (the sign-off's #14): an error "Approve anyway" covers, and the
    approve bar's longest Math pill ("1 difference: GST")."""
    import json

    inv = store.get_invoice(invoice_id)
    doc = inv["ai_output"]
    gst = next(t for t in doc["tax_lines"] if t["tax_type"] == "GST")
    gst["tax_amount"] = round(gst["tax_amount"] + 12.40, 2)
    doc["tax_total"] = round(doc["tax_total"] + 12.40, 2)
    doc["grand_total"] = round(doc["grand_total"] + 12.40, 2)
    with store._conn() as conn:
        conn.execute("UPDATE invoices SET ai_output = ?, grand_total = ? WHERE id = ?",
                     (json.dumps(doc), doc["grand_total"], invoice_id))  # fmt: skip


# --- 1. The approve bar with "Approve anyway" ticked at 1366 ----------------------------------------------------------


def test_in_a_browser_approve_anyway_at_1366_overlaps_nothing_and_asks_for_the_reason(browser, store):
    """B-790: with "Approve anyway" ticked at 1366 its label sat over the Math pill, "Teaches from…" and the reason box
    were cut off, and Ctrl+Enter without a reason said to tick the box that was ticked."""
    _gst_off(store, _id(store, "Red River"))
    page = _review(browser, store, "Red River")
    box = page.locator("[data-live=bar] [name=override]")
    box.check()
    page.wait_for_selector("[data-override-reason]:not([hidden])")
    page.locator("[data-override-reason]").fill("")
    page.locator("[form=inv][name=invoice_number]").focus()
    page.wait_for_timeout(700)
    assert not _overlap(page, ".inv-override", ".rvw-math")
    assert not _overlap(page, ".inv-override", ".rvw-post-sub")
    clipped = page.evaluate("""() => [...document.querySelectorAll(
        '.rvw-post-sub, .rvw-post-amount, .inv-override, [data-override-reason]')].filter((e) => {
        const r = e.getBoundingClientRect();
        const bar = document.querySelector('.inv-actionbar-left').getBoundingClientRect();
        return e.scrollWidth > e.clientWidth + 1 || r.right > bar.right + 1 || r.width < 120; })
        .map((e) => e.className)""")
    assert clipped == []
    page.keyboard.press("Control+Enter")
    page.wait_for_selector("#toasts .flash")
    said = page.inner_text("#toasts")
    assert "Say why you approve anyway" in said and "Tick" not in said
    assert page.evaluate("document.activeElement.matches('[data-override-reason]')")
    assert page.errors == []


# --- 2. Tax lines beside the page at 1366 -----------------------------------------------------------------------------


def test_in_a_browser_the_tax_lines_fit_their_card_at_1366_with_the_names_above_the_boxes(browser, store):
    """B-791: the tax line's amount box ran past the card, and "Taxable" / "Amount" sat beside the boxes."""
    page = _review(browser, store, "Pacific")
    found = page.evaluate("""() => { const card = document.querySelector('.inv-tax').getBoundingClientRect();
      const out = [];
      for (const tr of document.querySelectorAll('[data-grid=taxes] > tr'))
      for (const td of tr.querySelectorAll('td')) {
        const box = td.querySelector('input, select'); if (!box) continue; const r = box.getBoundingClientRect();
        if (r.right > card.right - 8 || r.width < 60)
          out.push(box.name + ' ' + Math.round(r.right) + '/' + Math.round(card.right));
        const label = getComputedStyle(td, '::before').content;
        if (label && label !== 'none' && label !== 'normal' && getComputedStyle(td, '::before').display !== 'block')
          out.push(box.name + ' label beside');
      } return out; }""")
    assert found == []
    assert page.errors == []


# --- 3. Opening an invoice keeps the page at its top ------------------------------------------------------------------


def test_in_a_browser_opening_an_invoice_keeps_the_header_and_the_checks_in_view(browser, store):
    """B-792: the focus on the first field to check scrolled the page past the header and "Must be fixed"."""
    page = _review(browser, store, "Prairie")
    page.wait_for_function("document.activeElement && document.activeElement.name")
    assert page.evaluate("document.activeElement.name") == "pst_registration_number"  # focused, as before (B-742)
    assert page.evaluate("scrollY") == 0
    top = page.evaluate("document.querySelector('.inv-checks').getBoundingClientRect().top")
    assert 0 <= top < 400
    page.keyboard.type("1")  # typing in it brings it into view, above the approve bar
    page.wait_for_timeout(900)
    at = page.evaluate("""() => [document.activeElement.getBoundingClientRect().bottom,
      document.querySelector('.inv-actionbar').getBoundingClientRect().top]""")
    assert at[0] <= at[1] + 1
    assert page.errors == []


def test_in_a_browser_the_approved_banner_shows_on_the_next_invoice(browser, store, client):
    page = _review(browser, store, "Pacific")
    page.keyboard.press("Control+Enter")
    page.wait_for_url(re.compile(r"/review/\d+"), timeout=10000)
    page.wait_for_selector(".flash")
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(600)
    box = page.evaluate("document.querySelector('.flash').getBoundingClientRect().top")
    assert page.evaluate("scrollY") == 0 and 0 <= box < 300
    assert page.errors == []


# --- 4. The approved view --------------------------------------------------------------------------------------------


def test_in_a_browser_the_approved_posting_fits_its_column_at_1366(browser, store):
    """B-793: the GL posting of an approved invoice was 753 px wide in a 424 px column (Net, tax and Amount behind a
    hidden scroll)."""
    invoice_id = _id(store, "Northwind", APPROVED)
    page = _open(browser, f"/review/{invoice_id}")
    wide = page.evaluate(
        """() => { const w = document.querySelector('.rvw-gl'); return [w.scrollWidth, w.clientWidth]; }"""
    )
    assert wide[0] <= wide[1] + 1, wide
    assert page.locator(".rvw-gl th", has_text="Amount").is_visible()
    assert page.errors == []


def test_the_approved_view_leaves_out_the_lm_studio_banner(client, store, monkeypatch):
    off = SimpleNamespace(banner="LM Studio isn't running: invoices are read by OCR only and wait for a person.",
                          self_test="", page_reader_ready=False, lm_studio=False, page_reader=None)  # fmt: skip
    monkeypatch.setattr("ap_coder.web.core.reading_status_or_none", lambda *a: off)
    monkeypatch.setattr("ap_coder.web.pages.invoice.reading_status_or_none", lambda *a: off, raising=False)
    approved = client.get(f"/review/{_id(store, 'Northwind', APPROVED)}").text
    assert "LM Studio isn" not in approved and "visibility_off" not in approved
    review = client.get(f"/review/{_id(store, 'Pacific')}").text
    assert "LM Studio isn" in review  # the review screen keeps its one-line note


# --- 5. The JD Edwards remark counter --------------------------------------------------------------------------------


def test_in_a_browser_a_long_remark_is_counted_quietly_until_its_box_is_used(browser, store):
    """B-794: an orange border and "44/30" on nearly every line watered down the warning colour."""
    _jde_on(store)
    page = _review(browser, store, "Harbourview", 1920, 1080)
    page.evaluate("document.activeElement.blur()")
    page.wait_for_timeout(300)
    quiet = page.evaluate("""() => { const t = [...document.querySelectorAll('textarea[name=line_description]')]
        .find((x) => x.value.trim().length > 30); const note = t.closest('td').querySelector('[data-remark]');
        const warn = getComputedStyle(document.documentElement).getPropertyValue('--lg-warning').trim();
        return {shown: !note.hidden, color: getComputedStyle(note).color, border: getComputedStyle(t).borderColor,
                title: note.title, warn}; }""")
    assert quiet["shown"] and "JD Edwards keeps the first 30 characters" in quiet["title"]
    probe = page.evaluate(
        """(c) => { const d = document.createElement('i'); d.style.color = c; document.body.append(d);
        const v = getComputedStyle(d).color; d.remove(); return v; }""",
        quiet["warn"],
    )
    assert quiet["color"] != probe and quiet["border"] != probe  # grey, not the warning colour
    long = page.locator("textarea[name=line_description]").first
    long.focus()
    note = page.evaluate("""() => { const t = document.activeElement;
        const n = t.closest('td').querySelector('[data-remark]');
        return getComputedStyle(n).color; }""")
    assert note == probe  # being typed in: the warning colour
    assert page.errors == []


# --- 6. The shortcuts list at 1366 -----------------------------------------------------------------------------------


def test_in_a_browser_the_shortcuts_list_fits_its_panel_at_1366(browser, store):
    """B-795: its text ran past its panel."""
    page = _review(browser, store, "Pacific")
    page.locator(".rvw-nav details.pop > summary").click()
    out = page.evaluate("""() => { const body = document.querySelector('.rvw-nav details.pop > .pop-body');
        const b = body.getBoundingClientRect();
        return [...body.querySelectorAll('td')]
          .filter((td) => td.getBoundingClientRect().right > b.right + 1).length; }""")
    assert out == 0
    assert page.errors == []


# --- 7. One note under the vendor, red when it is an error ----------------------------------------------------------


def test_in_a_browser_not_in_the_vendor_list_is_said_once_and_red_as_an_error(browser, store):
    """B-796: "Not in the vendor list" was said twice under the vendor, and orange there while red at the top."""
    page = _review(browser, store, "Cascade")
    page.wait_for_selector(".inv-details [data-field=vendor_name] [data-flag]:not([hidden])")
    said = page.inner_text(".inv-details [data-field=vendor_name]").lower()
    assert said.count("vendor list") == 1, said
    colors = page.evaluate("""() => [
        getComputedStyle(document.querySelector(".inv-details [data-field=vendor_name] [data-flag]")).color,
        getComputedStyle(document.querySelector('.rvw-status-group.err .rvw-status-title')).color]""")
    assert colors[0] == colors[1]
    assert page.errors == []


# --- 8. The bar's first blocker in screen order --------------------------------------------------------------------


def test_the_bar_says_the_first_blocker_in_screen_order(client, store):
    """B-797: with the currency XYZ the bar said to pick a bank account (which follows the currency)."""
    clean, _ = banks.clean_rows([{"code": "CAD-OP", "name": "Operating", "currency": "CAD",
                                  "account_number": "0000001234", "is_default": True, "active": True}])  # fmt: skip
    store.save_bank_accounts(clean)
    invoice_id = _id(store, "Pacific")
    answer = check(client, invoice_id, form_of(store, invoice_id, currency="XYZ"))
    bar = answer["sections"]["bar"]
    reason = re.search(r'data-not-ready="([^"]*)"', bar).group(1)
    assert "XYZ" in reason and "bank account" not in reason and reason.endswith("CAD, USD or EUR")
    assert "Not ready: “XYZ”" in bar.replace("&#34;", '"') or "Not ready: &ldquo;XYZ" in bar or "XYZ" in bar
    refused = client.post(f"/review/{invoice_id}/approve", data=form_of(store, invoice_id, currency="XYZ"),
                          headers=PAGE).json()  # fmt: skip
    assert "XYZ" in refused["error"]


# --- 9. Menus everywhere ---------------------------------------------------------------------------------------------


def _menu_ids(page) -> list[str]:
    page.wait_for_selector(".cm[role=menu]")
    items = page.locator(".cm[role=menu]:not(.cm-sub) > .cm-item")
    return [items.nth(i).get_attribute("data-cm") for i in range(items.count())]


def test_in_a_browser_posting_rows_tax_check_rows_and_checks_have_a_menu_and_esc_gives_the_focus_back(browser, store):
    """B-798: a right-click on the posting, the Tax check or a check had no menu, and Esc left the focus on the page."""
    _gst_off(store, _id(store, "Red River"))  # a difference in the Tax check, an error among the checks
    page = _review(browser, store, "Red River")
    number = page.locator("[form=inv][name=invoice_number]")
    for target, wanted in (
        ("[data-live=posting] tbody tr >> nth=0", {"goto", "copy-amount"}),
        ("[data-tax-row] >> nth=0", {"copy-diff"}),
        (".rvw-status li >> nth=0", {"help", "copy-text"}),
    ):
        number.focus()
        page.locator(target).scroll_into_view_if_needed()
        page.locator(target).click(button="right", position={"x": 4, "y": 4})
        ids = set(_menu_ids(page))
        assert wanted <= ids, (target, ids)
        assert page.locator(".cm [aria-disabled=true]").count() == 0
        page.keyboard.press("Escape")
        assert page.evaluate("document.activeElement.name") == "invoice_number", target
    # a posting row's Go to the line focuses that line's GL account
    page.locator("[data-live=posting] tbody tr >> nth=0").click(button="right", position={"x": 4, "y": 4})
    page.locator(".cm-item[data-cm=goto]").click()
    assert page.evaluate("document.activeElement.closest('[data-grid=lines] > tr') !== null")
    assert page.errors == []


def test_in_a_browser_no_greyed_item_is_left_in_any_menu_and_no_label_is_cut(browser, store):
    page = _review(browser, store, "Pacific")
    page.locator(".inv-details [data-field=invoice_number] .lbl").click(button="right")
    assert "absent" not in _menu_ids(page)  # AP Coder needs it: not offered, rather than greyed
    page.keyboard.press("Escape")
    page.locator(".iv-stage").click(button="right", position={"x": 5, "y": 5})  # not on a word
    assert "line" not in _menu_ids(page)
    page.keyboard.press("Escape")
    page.locator("[data-grid=lines] > tr >> nth=1").locator("[name=line_number]").click(button="right")
    _menu_ids(page)
    cut = page.evaluate("""() => [...document.querySelectorAll('.cm .cm-label')]
        .filter((l) => l.scrollWidth > l.clientWidth + 1)
        .map((l) => l.textContent)""")
    assert cut == []
    assert page.locator(".cm [aria-disabled=true]").count() == 0
    assert page.errors == []


# --- 10. Confidence is the reading's, not the setup's ----------------------------------------------------------------


def test_confidence_is_not_lowered_by_jd_edwards_not_set_up(client, store):
    """B-799: #3 showed 81% with JD Edwards set up and 29% without: setup blockers are blockers, not doubt."""
    invoice_id = _id(store, "Pacific")
    before = check(client, invoice_id, form_of(store, invoice_id))["sections"]["summary"]
    _jde_on(store)  # in use, not set up: every voucher is held back, said as a blocker
    answer = check(client, invoice_id, form_of(store, invoice_id))
    assert "Not ready: JD Edwards" in answer["sections"]["bar"]
    conf = re.compile(r"rq-conf[^>]*>.*?<b>([^<]*)</b>", re.S)
    assert conf.search(answer["sections"]["summary"]).group(1) == conf.search(before).group(1)


# --- 11. The lines grid at 1920 ---------------------------------------------------------------------------------


def test_in_a_browser_the_gl_account_and_tax_code_have_room_at_1920(browser, store):
    """B-800: at 1920 the GL account (148 px) and the tax code ("GST + Q…") were cut off while Amount was wide."""
    with store._conn() as conn:  # no cost centers, as in the pilot (D-53)
        conn.execute("DELETE FROM cost_centers")
    page = _review(browser, store, "Services Informatiques", 1920, 1080)
    page.locator("[data-show-qty]").check()
    sizes = page.evaluate("""() => { const tr = document.querySelector('[data-grid=lines] > tr');
        const w = (s) => tr.querySelector(s).getBoundingClientRect().width;
        const tax = tr.querySelector('select[data-tax-code]');
        return {gl: w('.c-gl'), amt: w('.c-amt'), tax: w('.c-tax'), title: tax.title,
                text: tax.selectedOptions[0].textContent}; }""")
    assert sizes["gl"] >= 130 and sizes["gl"] > 1.5 * sizes["amt"], sizes  # the description is wider (B-824)
    assert sizes["tax"] >= 110 and sizes["title"] == sizes["text"], sizes  # "GST + QST" in full
    assert page.errors == []


# --- 12. The small things --------------------------------------------------------------------------------------------


def test_in_a_browser_draft_saved_stays_off_the_document_card(browser, store):
    page = _review(browser, store, "Pacific")
    page.locator("[form=inv][name=payment_terms]").fill("Net 45")
    page.wait_for_function("document.querySelector('[data-checking]').textContent.startsWith('Draft saved')")
    inside = page.evaluate("""() => { const c = document.querySelector('[data-checking]').getBoundingClientRect();
        const bar = document.querySelector('.inv-actionbar').getBoundingClientRect();
        return c.top >= bar.top && c.bottom <= bar.bottom && c.left >= bar.left && c.right <= bar.right; }""")
    assert inside  # in the bar, not a tag floating over the document card's corner
    assert page.errors == []


def test_in_a_browser_reopen_focuses_its_reason_and_the_clear_queue_its_action(browser, store):
    page = _open(browser, f"/review/{_id(store, 'Northwind', APPROVED)}")
    page.locator("summary", has_text="Reopen for correction").click()
    page.wait_for_timeout(200)  # the toggle event comes after the click
    assert page.evaluate("document.activeElement.name") == "reason"
    for inv in store.list_invoices(REVIEW):
        store.approve_invoice(inv["id"], store.get_invoice(inv["id"])["ai_output"], "Jordan Lee")
    queue = _open(browser, "/review", wait_viewer=False)
    assert queue.evaluate("document.activeElement.textContent").strip().startswith("Process new invoices")
    assert page.errors == [] and queue.errors == []


def test_a_credit_notes_tax_check_uses_a_minus_sign_and_a_difference_says_which_way(client, store):
    credit = next(i["id"] for i in store.list_invoices(REVIEW) if (i["grand_total"] or 0) < 0)
    taxcheck = check(client, credit, form_of(store, credit))["sections"]["taxcheck"]
    assert not re.search(r"\(\d[\d,]*\.\d\d\)", taxcheck) and "-2,050.00" in taxcheck
    invoice_id = _id(store, "Pacific")
    off = check(client, invoice_id, form_of(store, invoice_id, grand_total="3063.12"))
    said = off["sections"]["posting"] + off["sections"]["checks"]
    assert "10.00 more than the lines" in said and "off by -10.00" not in said.lower()


def test_the_approved_banner_is_one_line_with_what_it_learned_folded(client, store):
    invoice_id = _id(store, "Pacific")
    answer = client.post(f"/review/{invoice_id}/approve", data={**form_of(store, invoice_id), "why_done": "1"},
                         headers=PAGE).json()  # fmt: skip
    page = client.get(answer["redirect"]).text
    flash = page.split('<div class="flash ok"', 1)[1].split('class="flash-close"', 1)[0]
    first = re.search(r"<div class=\"flash-text\">(.*?)</div>", flash, re.S).group(1)
    assert first.strip().startswith("Approved Pacific Office Supply Ltd") and "coded as suggested" not in first
    assert "What it learned" in flash and "coded as suggested" in flash  # behind the toggle
    assert f'action="/review/{invoice_id}/undo-approve"' in flash


def test_the_jd_edwards_blocks_settings_link_comes_last(client, store):
    _jde_on(store)
    invoice_id = _id(store, "Pacific")
    block = check(client, invoice_id, form_of(store, invoice_id))["sections"]["jde"]
    assert block.index("Change the settings") > block.index("</dl>")


# --- 13. Drafts: real edits only, kept over a restart; Undo's real reason ---------------------------------------------


def test_opening_an_invoice_keeps_no_draft_when_nothing_was_changed(client, store):
    """B-801: the screen's opening check filled the default Pay from account (stored blank) and posted "UNASSIGNED"
    for a cost center stored blank: a phantom draft, and bulk approval skipped the invoice "with edits open on its
    review screen"."""
    clean, _ = banks.clean_rows([{"code": "CAD-OP", "name": "Operating", "currency": "CAD",
                                  "account_number": "0000001234", "is_default": True, "active": True}])  # fmt: skip
    store.save_bank_accounts(clean)  # the screen fills the default Pay from account the invoice stored has not
    invoice_id = _id(store, "Pacific")
    data = form_of(store, invoice_id)
    data["line_cc"] = ["UNASSIGNED" if not v else v for v in data["line_cc"]]  # as the browser posts it
    check(client, invoice_id, data)
    assert invoice_id not in client.app.state.invoice_drafts
    check(client, invoice_id, {**data, "payment_terms": "Net 45"})
    assert invoice_id in client.app.state.invoice_drafts  # a real edit is kept


def test_a_draft_outlives_a_restart_and_goes_with_the_approval(client, store):
    """B-802: drafts were kept in memory only, while the screen said "leave and come back, they are still here"."""
    invoice_id = _id(store, "Pacific")
    check(client, invoice_id, form_of(store, invoice_id, payment_terms="Net 45 EOM"))
    again = client_for(store, web_settings())  # AP Coder started again
    assert 'value="Net 45 EOM"' in again.get(f"/review/{invoice_id}").text
    answer = again.post("/review/bulk-approve", data={"ids": [invoice_id]}, headers=PAGE).json()
    assert store.get_invoice(invoice_id)["status"] == REVIEW and answer
    again.post(f"/review/{invoice_id}/reject", data={"reason": "not ours"}, headers=PAGE)
    assert store.invoice_drafts() == []  # rejected: its draft is gone with it


def test_undo_of_a_rejected_invoice_gives_the_real_reason(client, store):
    invoice_id = _id(store, "Pacific")
    client.post(f"/review/{invoice_id}/reject", data={"reason": "not ours"}, headers=PAGE)
    assert store.get_invoice(invoice_id)["status"] == REJECTED
    refused = client.post(f"/review/{invoice_id}/undo-approve", headers=PAGE).json()
    assert "rejected" in refused["error"] and "exported" not in refused["error"]


def test_the_differs_check_treats_every_blank_account_alike():
    from ap_coder.core.schema import InvoiceCoding

    base = {"vendor_name": "A", "invoice_number": "1", "invoice_date": "2026-01-01", "subtotal": 10, "tax_total": 0,
            "grand_total": 10, "currency": "CAD", "confidence_score": 0.9,
            "line_items": [{"line_number": 1, "description": "x", "amount": 10, "quantity": 1, "unit_price": 10,
                            "predicted_gl_code": "6000", "predicted_cost_center": ""}]}  # fmt: skip
    shown = InvoiceCoding.model_validate(
        {**base, "line_items": [{**base["line_items"][0], "predicted_cost_center": "UNASSIGNED"}]}
    )
    assert not invoice_view.differs_from_stored(shown, base)
