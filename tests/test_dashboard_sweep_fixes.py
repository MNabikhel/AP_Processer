"""Bugs found driving the dashboard in a browser as a clerk and an AP lead would (third sweep), each reproduced here
through the page (ap_coder/web) or the helper it came from, so they are not made again."""

import json
import re
from html.parser import HTMLParser
from types import SimpleNamespace

import pytest

from ap_coder.core.store import Store, load_sample_setup
from ap_coder.web.core import STATIC_DIR

from .conftest import SAMPLE_STEM, SAMPLES
from .test_web_invoice import check, form_of
from .webclient import add_clean, client_for, follow, gt, known_vendors, post, web_settings


@pytest.fixture
def store(tmp_path):
    store = Store(tmp_path / "private" / "ap_coder.db")
    load_sample_setup(store)
    return store


@pytest.fixture
def client(store):
    return client_for(store)


def _flagged(store, doc=None, **changes):
    doc = {**(doc or gt()), **changes}
    return store.add_invoice(SAMPLES / f"{SAMPLE_STEM}.pdf", doc, {"requires_review": True})


def test_the_queued_note_reads_as_a_sentence(store, monkeypatch):
    """The toast after processing said "2 invoices queued for OvisOCR2, which isn't running: them wait for it"."""
    from ap_coder.web.pages import process

    monkeypatch.setattr(process, "reading_status_or_none", lambda *a: SimpleNamespace(page_reader_ready=False))
    settings = web_settings()
    # Ready to review now: OvisOCR2 only double-checks them later (it said "they wait for it", F1)
    assert process.queued_note(settings, store, 2) == (
        "OvisOCR2 will double-check them when LM Studio runs (Settings → Reading)."
    )
    assert process.queued_note(settings, store, 1) == (
        "OvisOCR2 will double-check it when LM Studio runs (Settings → Reading)."
    )
    assert process.queued_note(settings, store, 0) == ""
    running = SimpleNamespace(page_reader_ready=False, lm_studio=True)
    monkeypatch.setattr(process, "reading_status_or_none", lambda *a: running)
    assert process.queued_note(settings, store, 2) == (
        "OvisOCR2 will double-check them once it is loaded in LM Studio (Settings → Reading)."
    )


def test_the_queued_note_does_not_call_a_running_ovisocr2_not_running(store, monkeypatch):
    """The pilot tester's LM Studio runs OvisOCR2, but it has not passed its self-test yet (or failed it): the toast
    said "OvisOCR2, which isn't running", and he would look for a problem in LM Studio that isn't there."""
    from ap_coder.web.pages import process

    settings = web_settings()
    for state, why in (("failed", "it failed its self-test"), ("pending", "its self-test hasn't passed yet"),
                       ("running", "its self-test hasn't passed yet")):  # fmt: skip
        status = SimpleNamespace(page_reader_ready=False, page_reader_model="ath-maas_ovisocr2", self_test=state)
        monkeypatch.setattr(process, "reading_status_or_none", lambda *a, s=status: s)
        assert process.queued_note(settings, store, 2) == (
            f"OvisOCR2 runs in LM Studio but isn't trusted yet ({why}): it will double-check them once it passes "
            "(Settings → Reading)."
        )


def test_the_split_menu_names_cost_centers_as_the_line_grid_does(client, store):
    """The "Split a line…" menu showed a line without a cost center as the stored code UNASSIGNED (and CC400 without
    its name), where the line grid beside it says "Needs a cost center"."""
    doc = gt()
    doc["line_items"][0]["predicted_cost_center"] = "UNASSIGNED"
    invoice_id = _flagged(store, doc)
    page = client.get(f"/review/{invoice_id}").text
    # the split's cost centers are picked from the page's list (#cc-options, D-56), as a line's are
    parts = page.split('<template id="part-template">', 1)[1].split("</template>", 1)[0]
    assert 'name="part_cc"' in parts and 'data-options="cc-options"' in parts
    options = dict(json.loads(re.search(r'id="cc-options">(.*?)</script>', page, re.S).group(1)))
    assert options["UNASSIGNED"] == "Needs a cost center"
    assert options["CC400"].startswith("CC400 · ")


def test_the_approve_bar_counts_changes_as_the_approval_does(client, store):
    """Picking the cost centers the AI left blank read "you changed 4 of its codes" on the approve bar, and the
    approval then said "3 of 4 lines coded as suggested": filling a blank corrects nothing."""
    doc = gt()
    for li in doc["line_items"]:
        li["predicted_cost_center"] = "UNASSIGNED"
    invoice_id = _flagged(store, doc)
    n = len(doc["line_items"])

    def fill(rows):
        for row in rows:
            row["predicted_cost_center"] = "CC400"
        rows[0]["predicted_gl_code"] = "6000" if rows[0]["predicted_gl_code"] != "6000" else "6010"  # one change
        return rows

    data = form_of(store, invoice_id, fill)
    bar = check(client, invoice_id, data)["sections"]["bar"]
    assert (
        re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", bar.split("Teaches from", 1)[1]))
        .strip()
        .startswith(f"{n} lines: 1 you changed, {n - 1} as suggested")
    )
    page = follow(client, post(client, f"/review/{invoice_id}/approve", data))
    assert f"{n - 1} of {n} lines coded as suggested" in page


def test_a_restore_asks_for_restore_again_before_the_next_one(client, store):
    """After a restore the menu stayed open with RESTORE still typed and the button enabled: one more click (or
    picking another backup, then one click) replaced the database again without a new confirmation."""
    older = store.backup_now("manual")
    newer = store.backup_now("manual")
    assert older != newer
    page = follow(client, post(client, "/settings/data/restore", {"name": older.name, "confirm": "RESTORE"}))
    assert f"Restored {older.name}" in page
    form = page.split('action="/settings/data/restore"', 1)[1].split("</form>", 1)[0]
    confirm = re.search(r'<input[^>]*name="confirm"[^>]*>', form).group(0)
    assert "value=" not in confirm  # typed afresh for the next restore
    assert re.search(r'<button[^>]*type="submit"[^>]*disabled', form)  # and the button waits for it
    assert (
        "<details" in page.split('id="restore"', 1)[0].rsplit("\n", 1)[-1]
        and " open" not in (page.split('id="restore"', 1)[0].rsplit("<details", 1)[1])
    )  # the menu is closed
    response = post(client, "/settings/data/restore", {"name": newer.name})  # no RESTORE typed: nothing happens
    assert response.status_code == 400 and "Type RESTORE" in response.json()["error"]


def test_the_reading_tab_does_not_say_ovisocr2_is_missing_while_lm_studio_is_down(client):
    """With LM Studio not running, Settings → Reading said "Not in LM Studio" and "No self-test: OvisOCR2 isn't in
    LM Studio", while the steps below it (rightly) said "start LM Studio first to check": nobody knows yet whether
    OvisOCR2 is there, and IT went looking for model files that were in place."""
    import html

    page = client.get("/settings/reading").text
    text = html.unescape(page)
    assert "start LM Studio first to check" in text  # LM Studio is not running here
    assert "Not in LM Studio" not in text and "OvisOCR2 isn't in LM Studio" not in text
    assert "No self-test: LM Studio isn't running" in text
    button = page.split('action="/settings/reading/self-test"', 1)[1].split("</button>", 1)[0]
    assert " disabled" in button


def test_the_vendor_master_duplicates_warning_is_about_the_last_import(client):
    """The "appear more than once in the file" warning stayed after the corrected file was imported, naming vendors
    the new file no longer has twice."""

    def import_file(name, text):
        response = post(client, "/vendors/master/upload", files={"file": (name, text.encode(), "text/csv")})
        token = client.get(response.json()["redirect"]).url.params["file"]
        columns = {"file": token, "col_vendor_name": "vendor_name", "col_erp_id": "vendor_id",
                   "col_terms": "payment_terms"}  # fmt: skip
        return follow(client, post(client, "/vendors/master/import", columns))

    head = "vendor_id,vendor_name,payment_terms\n"
    twice = head + "V1,Northwind IT Solutions Inc.,Net 30\nV2,Northwind IT Solutions Inc,Net 30\nV3,Chinook,Net 15\n"
    assert "1 vendor appears more than once" in import_file("vendors.csv", twice)
    once = head + "V1,Northwind IT Solutions Inc.,Net 30\nV3,Chinook,Net 15\n"
    page = import_file("vendors_fixed.csv", once)
    assert "Vendor master:" in page and "more than once" not in page


class _Tables(HTMLParser):
    """The class lists of the elements around each table of a page."""

    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}

    def __init__(self):
        super().__init__()
        self.stack, self.tables = [], []

    def handle_starttag(self, tag, attrs):
        classes = (dict(attrs).get("class") or "").split()
        if tag == "table":
            self.tables.append((classes, [c for _, cls in self.stack for c in cls]))
        if tag not in self.VOID:
            self.stack.append((tag, classes))

    def handle_endtag(self, tag):
        while self.stack:
            if self.stack.pop()[0] == tag:
                break


def test_tables_scroll_in_their_card_at_a_narrow_width(client, store):
    """At a phone's or a narrow window's width the GL posting preview (a table inside <div class='rvw-gl'>) spilled
    out of its card and the whole review screen scrolled sideways. Every table of the review screen is in a wrapper
    that scrolls on its own (or in a pop-up no wider than the window). The wrapper is positioned, so a label hidden
    for screen readers inside a table (absolutely positioned) scrolls with it: the line grid's "Delete" heading made
    the web screen scroll sideways at 390 px (checked in a browser)."""
    invoice_id = _flagged(store)
    page = client.get(f"/review/{invoice_id}").text
    sections = check(client, invoice_id)["sections"]
    css = "".join(p.read_text(encoding="utf-8") for p in STATIC_DIR.glob("*.css"))
    wrap = re.search(r"(?m)^\.table-wrap\s*\{([^}]*)\}", css).group(1)  # the wrapper's own rule
    assert re.search(r"overflow-x:\s*auto", wrap) and re.search(r"position:\s*relative", wrap)
    assert re.search(r"max-width:\s*100%", wrap)
    assert re.search(r"\.pop-body\s*\{[^}]*width:\s*min\([^)]*vw\)", css)
    assert "rvw-gl table-wrap" in sections["posting"]
    found = 0
    for html in (page, *sections.values()):
        parser = _Tables()
        parser.feed(html)
        for classes, around in parser.tables:
            if "apc-table" in classes:
                found += 1
                assert {"table-wrap", "pop-body"} & set(around), (classes, around)
    assert found >= 5


def test_insights_charge_no_cloud_cost_for_invoices_read_and_coded_on_this_computer(client, store, monkeypatch):
    """The offline build sends nothing to Azure, yet Insights priced the tokens LM Studio (or the demo) recorded at
    Azure's rates: "Cloud cost / invoice $0.040 · Azure, measured on 10 invoices", and a cloud cost taken off the
    business case's net benefit."""
    from ap_coder.erp.insights import compute
    from ap_coder.ops.demo import load_demo

    load_demo(store)
    assert compute(store)["cost_per_invoice_cad"] is None
    assert compute(store)["projection"]["azure_cost"] == 0
    page = client.get("/insights").text  # (no cloud cost shown at all offline: B-515)
    assert "Cloud cost" not in page and "Azure, measured" not in page
    monkeypatch.setenv("AP_ALLOW_INTERNET", "1")  # a developer build that may use Azure: its cost is measured
    assert compute(store)["cost_per_invoice_cad"] > 0


def test_counts_of_one_read_as_sentences(client, store):
    """Seen in the browser: "1 wait for a second approval" after a bulk approval, "Pick a cost center for line 1, 2,
    3, 4" on the approve bar, "1 invoice cannot go to JD Edwards yet and are left out" on Exports."""
    store.set_setting("approval_limit", "5000.00")
    known_vendors(store, SAMPLE_STEM, "laurentides_QC_TPS_TVQ_SIL-4471")
    ids = [add_clean(store), add_clean(store, "laurentides_QC_TPS_TVQ_SIL-4471")]  # 18,017.85 CAD is over the limit
    page = follow(client, post(client, "/review/bulk-approve", {"ids": ids}))
    assert "Approved 2 invoices; 1 waits for a second approval." in page

    doc = gt()
    for li in doc["line_items"]:
        li["predicted_cost_center"] = "UNASSIGNED"
    invoice_id = _flagged(store, doc, invoice_number="NW-2")
    n = len(doc["line_items"])  # 4 or more are counted, with the way to fill them all (B-667)
    assert (
        f"Pick a cost center for {n} lines to approve (or use Apply to all lines…)"
        in client.get(f"/review/{invoice_id}").text
    )

    warned = re.sub(r"\s+", " ", client.get("/exports?fmt=jde").text)
    assert "1 invoice cannot go to JD Edwards yet and is left out of the batch" in warned


def test_an_activity_search_that_finds_nothing_says_so(client, store):
    """Searching the Activity log for something it doesn't hold (or picking a filter with no events, e.g. Backups
    before any backup) crashed the page: an empty timeline drew nothing at all."""
    add_clean(store)
    for params in ({"q": "nothing like this"}, {"q": "nothing like this", "view": "table"}, {"group": "Backups"}):
        response = client.get("/activity", params=params)
        assert response.status_code == 200, params
    assert "No events match" in client.get("/activity", params={"q": "nothing like this"}).text
    assert "No events match" in client.get("/activity", params={"group": "Backups"}).text


def test_getting_started_counts_only_the_steps_that_are_needed(tmp_path, monkeypatch):
    """The welcome screen's ring counted the optional chat model, so it read 1/6 where the Process page said 1 of 4
    (plus processing the first invoices), and could never be complete without that optional model. OCR is taken as
    installed (CI's test jobs run without it)."""
    from ap_coder.web.pages import process

    monkeypatch.setattr(process, "ocr_available", lambda: True)
    store = Store(tmp_path / "private" / "ap_coder.db")
    client = client_for(store)
    page = client.get("/review").text
    # Every row shown is counted (B-503): reading invoices done; LM Studio, GL, tax mapping, your name, your province
    # and the first invoices to do, your bank accounts (optional, D-66), the vendor master and the past coding (D-92).
    # (The chat model is part of the LM Studio step.)
    assert "Chat model for GL suggestions" not in page
    assert "aria-label='1/10'" in page, page
    load_sample_setup(store)
    assert "aria-label='3/10'" in client.get("/review").text
