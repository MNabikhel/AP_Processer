"""The dashboard's fourth UI sweep (B-990 on): the second approver sees why it is needed and what was approved over,
the screen's shortcuts work from the box the page focused, drafts in the queue, plain words for codes and settings,
and the small things."""

from __future__ import annotations

import re

from ap_coder.core.store import PENDING, REVIEW

from .test_web_invoice import PAGE, check, clean_invoice, form_of
from .test_web_review_signoff import _id, _open, _review, browser, demo_db, me, store

__all__ = ["browser", "demo_db", "me", "store"]  # the fixtures, shared


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
        assert "No CAD exchange rate for USD is set" in text, path
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
    from ap_coder.web.pages.review import draft_reasons

    class Asked:  # what draft_reasons reads of the request: this person's drafts
        app = client.app

    client.app.state.invoice_drafts[invoice_id]["check"] = {"why": "JD Edwards: the supplier has no AN8"}
    with_drafts = draft_reasons(Asked(), store.list_invoices(REVIEW))
    assert with_drafts[invoice_id] == "Edits not approved yet: JD Edwards: the supplier has no AN8"


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


# --- 3. No phantom changes -------------------------------------------------------------------------------------------


def test_an_untouched_invoice_records_no_cost_center_change_when_no_list_is_in_use(client, store):
    """B-1001: "4 change(s): line 1 cost center CC700 → (blank)…" on an invoice nobody changed: with no cost center
    list, a cost center the AI copied is not shown and its blank is no edit (D-86)."""
    import json

    with store._conn() as conn:
        conn.execute("DELETE FROM cost_centers")
    invoice_id = clean_invoice(client, store)
    inv = store.get_invoice(invoice_id)
    doc = inv["ai_output"]
    for line in doc["line_items"]:
        line["predicted_cost_center"] = "CC700"
    with store._conn() as conn:
        conn.execute("UPDATE invoices SET ai_output = ? WHERE id = ?", (json.dumps(doc), invoice_id))
    final = json.loads(json.dumps(doc))
    for line in final["line_items"]:
        line["predicted_cost_center"] = ""
    store.approve_invoice(invoice_id, final, "Dana Clerk")
    approved = next(e for e in store.events(invoice_id) if e["action"] == "approved")
    assert approved["detail"]["changes"] == []
    assert "all as the AI suggested" in client.get("/activity").text


# --- 4. The small things ---------------------------------------------------------------------------------------------


def _plain(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


def test_the_vendor_master_step_counts_the_supplier_numbers_set_in_jd_edwards_settings(client, store):
    """B-1002: "no address number (AN8)" while every vendor had one in Settings → JD Edwards → Supplier address
    numbers."""
    from ap_coder.erp import jde
    from ap_coder.web.pages.process import VENDOR_STEP, pilot_steps

    from .test_web_review_signoff import _jde_on

    _jde_on(store)
    vendors = store.master_vendors()
    assert vendors
    with store._conn() as conn:
        conn.execute("UPDATE vendors SET erp_id = '' WHERE in_master = 1")
    settings = jde.load(store)
    settings.an8_overrides = {v["vendor_key"]: str(1000 + n) for n, v in enumerate(vendors)}
    jde.save(store, settings)
    state, _, text = next(s for s in pilot_steps(store) if s[1] == VENDOR_STEP)
    assert state == "ok" and f"{len(vendors)} with an address number (AN8)" in text, text


def test_settings_about_says_what_is_shown_as_the_boxes_are(client, store):
    """B-1003: Settings → About said the JD Edwards settings "are hidden" with "Show JD Edwards settings" ticked."""
    from .test_web_review_signoff import _jde_on

    hidden = _plain(client.get("/settings/about").text)
    assert "JD Edwards E1 settings and export" in hidden and "are hidden" in hidden
    _jde_on(store)
    shown = _plain(client.get("/settings/about").text)
    assert "JD Edwards E1 settings and export are hidden" not in shown
    assert "The JD Edwards E1 settings and export are shown" in shown


def test_the_terms_table_says_the_review_screen_notes_terms_not_in_it(client, store):
    """B-1004: it said "the review screen then warns": it is a note since B-896."""
    from .test_web_review_signoff import _jde_on

    _jde_on(store)
    text = _plain(client.get("/settings/jde").text)
    assert "then warns" not in text and "the review screen then notes it when an invoice's terms" in text


def test_the_failed_and_rejected_tab_shows_the_invoice_number_and_amount(client, store):
    """B-1005: the Failed / rejected tab showed no invoice number or amount."""
    invoice_id = _id(store, "Prairie")
    store.reject_invoice(invoice_id, "Dana Clerk", "not ours")
    inv = store.get_invoice(invoice_id)
    text = _plain(client.get("/review?tab=other").text)
    assert inv["invoice_number"] in text and f"{inv['grand_total']:,.2f}" in text


def test_approved_today_says_which_are_fully_approved_as_the_approved_tab_lists_them(client, store, me):
    """B-1006: "Approved today" counted first approvals still waiting for a second while the Approved tab listed
    fewer, and its line was cut off."""
    store.set_setting("approval_limit", "1")
    invoice_id = clean_invoice(client, store)
    client.post(f"/review/{invoice_id}/approve", data=form_of(store, invoice_id), headers=PAGE)
    assert store.get_invoice(invoice_id)["status"] == PENDING
    text = _plain(client.get("/review?tab=approved").text)
    tile = text[text.index("Approved today") :][:200]
    assert "1 waits for a second approval" in tile and "0 fully approved" in tile, tile
    assert "1 more approved, waiting for a second approval" in text
    page = client.get("/review").text
    kpi = page[page.index("Approved today") :][:600]
    assert 'class="s wrap"' in kpi  # wraps rather than being cut off


def test_a_parked_invoice_offers_un_park_not_back_to_the_queue(client, store):
    """B-1007: "Back to the queue" on a parked invoice un-parked it."""
    invoice_id = _id(store, "Pacific")
    store.park_invoice(invoice_id, "Dana Clerk", "waiting for the PO")
    for path in (f"/review/{invoice_id}", "/review?tab=parked"):
        text = _plain(client.get(path).text)
        assert "Un-park" in text, path
        assert "play_circle Back to the queue" not in text, path


def test_vendors_says_the_vendor_master_is_imported_before_any_invoice(tmp_path):
    """B-1008: Vendors said "No vendors yet" after a vendor master import."""
    from ap_coder.core.store import Store

    from .webclient import client_for

    store = Store(tmp_path / "private" / "ap_coder.db")
    store.import_vendor_master([{"vendor_name": "Acme Supply Ltd.", "erp_id": "10001"},
                                {"vendor_name": "Beta Services Inc.", "erp_id": "10002"}])  # fmt: skip
    text = _plain(client_for(store).get("/vendors").text)
    assert "No vendors yet" not in text
    assert "2 vendors in the vendor master" in text


def test_activity_people_lists_everyone_it_counts(client, store):
    """B-1009: "People 4" listed 3 names."""
    for name in ("Ann", "Bob", "Cy", "Dee"):
        store.log_event("note", actor=name, detail={})
    text = _plain(client.get("/activity").text)
    tile = text[text.index("People") :][:200]
    count = int(re.match(r"People (\d+)", tile).group(1))
    names = tile.split(str(count), 1)[1].split("Deletions", 1)[0].strip()
    assert count >= 4 and (names.count(",") + 1 == count or f"and {count - 3} more" in names), tile


def test_a_place_of_supply_change_is_no_correction_of_the_coding():
    """B-1010: Insights and Activity counted a place-of-supply change as a correction."""
    from ap_coder.erp.insights import corrected

    moved = {"detail": {"changes": [{"what": "place of supply", "before": "ON", "after": "MB"}]}}
    assert not corrected(moved)
    filled = {"detail": {"changes": [{"what": "line 1 cost center", "before": "(blank)", "after": "CC100"}]}}
    assert not corrected(filled)  # filling a blank completes the coding (D-39)
    assert corrected({"detail": {"changes": [{"what": "line 1 GL", "before": "6000", "after": "6400"}]}})


def test_in_a_browser_enter_in_the_why_box_approves_anyway(browser, store):
    """B-1011: Enter in the "Why approve anyway?" box did nothing; Ctrl+Enter approved."""
    from .test_web_review_signoff import _gst_off

    invoice_id = _id(store, "Red River")
    _gst_off(store, invoice_id)
    page = _review(browser, store, "Red River")
    page.locator("[data-live=bar] [name=override]").check()
    page.wait_for_selector("[data-override-reason]:not([hidden])")
    page.locator("[data-override-reason]").fill("Credit promised by email")
    page.wait_for_timeout(900)
    page.locator("[data-override-reason]").press("Enter")
    page.wait_for_url(lambda url: not url.endswith(f"/review/{invoice_id}"), timeout=10000)
    assert store.get_invoice(invoice_id)["status"] in ("approved", PENDING)
    assert page.errors == []


def test_pilot_score_counts_fully_approved_invoices_only(client, store):
    """B-1012: the Insights pilot card counted invoices still waiting for a second approval."""
    from ap_coder.ops.pilot_score import our_invoices

    store.set_setting("approval_limit", "1")
    invoice_id = clean_invoice(client, store)
    client.post(f"/review/{invoice_id}/approve", data=form_of(store, invoice_id), headers=PAGE)
    assert store.get_invoice(invoice_id)["status"] == PENDING
    assert invoice_id not in [o.id for o in our_invoices(store)]


def test_the_insights_pilot_card_shows_the_command_only_as_a_hint_for_the_pilot_tester(client, store, monkeypatch):
    """B-1012: the card showed "python -m ap_coder pilot-score" to the clerk."""
    monkeypatch.setattr("ap_coder.ops.pilot_score.last_figures", lambda s: {"matched": 4, "ap_coder": 5,
                                                                            "made": "2026-10-24 00:39"})  # fmt: skip
    text = client.get("/insights").text
    card = text[text.index("pilot-title") : text.index("</section>", text.index("pilot-title"))]
    lead = card.split("<table", 1)[0] + card.split("</table>", 1)[1].split("pilot-hint", 1)[0]
    assert "python -m ap_coder" not in lead
    assert 'class="muted small pilot-hint"' in card and "For the pilot tester" in card


def test_the_document_cards_reading_button_is_reading_tools(client, store):
    """B-1013: "Reading" was both the confidence in the header and the Document card's button."""
    text = client.get(f"/review/{_id(store, 'Pacific')}").text
    summary = text[text.index('title="How this invoice was read') :][:400]
    assert "Reading tools" in summary
