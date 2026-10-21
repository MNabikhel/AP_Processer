"""Sweep 4, the pilot path (backend): import, review, approval, learning, exports and the store, as the pilot tester
uses them with real English vendor PDFs (docs/BUGS.md, "Pilot path (backend)")."""

from __future__ import annotations

import sys
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from ap_coder.core.config import Settings
from ap_coder.core.store import Store
from ap_coder.web.app import create_app


def _web(store: Store) -> TestClient:
    s = Settings()
    app = create_app(replace(s, llm=replace(s.llm, provider="off")), store, start_reader=False)
    return TestClient(app, base_url="http://localhost")


@pytest.mark.skipif(sys.platform == "win32", reason="the file size limit (a full disk) is set with resource")
def test_a_backup_that_cannot_be_written_never_stops_the_dashboard_nor_counts_as_made(tmp_path):
    import resource

    store = Store(tmp_path / "private" / "ap_coder.db")
    store.set_setting("filler", "x" * 400_000)  # a database bigger than the disk has room for
    soft, hard = resource.getrlimit(resource.RLIMIT_FSIZE)
    resource.setrlimit(resource.RLIMIT_FSIZE, (100_000, hard))  # as a full disk: no file grows past 100 kB
    try:
        with _web(store) as client:  # the daily backup runs as the dashboard starts
            started = client.get("/review").status_code
        with pytest.raises(OSError):
            store.backup_now("manual")  # the Settings page says it could not be made (it catches OSError)
    finally:
        resource.setrlimit(resource.RLIMIT_FSIZE, (soft, hard))
    assert started == 200
    assert store.list_backups() == []  # no half-written copy offered to restore, nor taken for today's backup
    assert store.auto_backup() is not None  # with room again, today's backup is made


def test_a_part_backup_left_by_a_crash_is_removed_when_backups_run(tmp_path):
    import os
    import time

    store = Store(tmp_path / "private" / "ap_coder.db")
    folder = store.backup_dir()
    folder.mkdir(parents=True)
    old, recent = folder / "ap_coder-20260101-080000.db.part", folder / "ap_coder-20990101-080000.db.part"
    for leftover in (old, recent):
        leftover.write_bytes(b"half")
    two_days_ago = time.time() - 2 * 86400
    os.utime(old, (two_days_ago, two_days_ago))
    store.backup_now("manual")
    assert not old.exists() and recent.exists()  # a copy still being written (another process) is left alone


PAGE = {"X-AP-Coder": "1"}


def _form(store: Store, invoice_id: int) -> dict:
    """The review form as the page posts it (tests/test_web_invoice.py ``form_of``)."""
    from ap_coder.web import invoice_view
    from ap_coder.web.invoice_view import Draft

    inv = store.get_invoice(invoice_id)
    draft = Draft.stored(inv["final_output"] or inv["ai_output"])
    rows = [dict(r) for r in draft.lines]
    data = {**draft.header, "grids": "1", "line_key": [f"r{i}" for i in range(len(rows))]}
    for column, name in invoice_view.LINE_INPUTS.items():
        data[name] = ["" if r.get(column) is None else str(r.get(column)) for r in rows]
    for i, r in enumerate(rows):
        data[f"line_tax_r{i}"] = list(r.get("taxes_applied") or [])
    for column, name in invoice_view.TAX_INPUTS.items():
        data[name] = ["" if t.get(column) is None else str(t.get(column)) for t in draft.taxes]
    return data


def _many_lines(ground_truth: dict, n: int) -> dict:
    """The sample invoice (Ontario, HST) with ``n`` lines."""
    lines = [{"line_number": i, "description": f"Part {i}", "quantity": 1.0, "unit_price": 1.0 + i / 10,
              "amount": round(1.0 + i / 10, 2), "predicted_gl_code": "6100", "predicted_cost_center": "CC100",
              "taxes_applied": ["HST"], "reasoning_justification": ""} for i in range(1, n + 1)]  # fmt: skip
    subtotal = round(sum(li["amount"] for li in lines), 2)
    hst = round(subtotal * 0.13, 2)
    tax = {**ground_truth["tax_lines"][0], "taxable_amount": subtotal, "tax_amount": hst}
    return {**ground_truth, "line_items": lines, "subtotal": subtotal, "tax_total": hst,
            "grand_total": round(subtotal + hst, 2), "tax_lines": [tax]}  # fmt: skip


def test_an_invoice_of_a_hundred_and_fifty_lines_can_be_checked_and_approved(tmp_path, ground_truth):
    """A long invoice (a distributor's, a phone bill) posts some ten fields per line: past about 90 lines the review
    screen answered every check, line action and Approve with "Too many fields. Maximum number of fields is 1000."."""
    from ap_coder.core.store import APPROVED, load_sample_setup

    from .conftest import SAMPLE_STEM, SAMPLES

    store = Store(tmp_path / "private" / "ap_coder.db")
    load_sample_setup(store)
    inv_id = store.add_invoice(SAMPLES / f"{SAMPLE_STEM}.pdf", _many_lines(ground_truth, 150), {})
    client = _web(store)
    data = _form(store, inv_id)
    checked = client.post(f"/review/{inv_id}/check", data=data, headers=PAGE).json()
    assert checked.get("valid") and checked.get("allow"), checked
    approved = client.post(f"/review/{inv_id}/approve", data=data, headers=PAGE).json()
    assert approved.get("ok"), approved
    inv = store.get_invoice(inv_id)
    assert inv["status"] == APPROVED and len(inv["final_output"]["line_items"]) == 150
    ticked = [inv_id, *range(10_000, 11_500)]  # the Exports page with 1,500 invoices ticked
    made = client.post("/exports/create", data={"fmt": "xlsx", "ids": ticked}, headers=PAGE).json()
    assert made.get("ok") and store.get_invoice(inv_id)["export_batch"], made


def _pdf(path, lines, size: float = 10):
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    for x, y, text in lines:
        page.insert_text((x, y), text, fontsize=size)
    doc.save(path)
    return path


def test_an_invoice_approved_again_teaches_the_template_once(tmp_path, ground_truth):
    """Approved, reopened for correction (or sent back by the second approver) and approved again: the second approval
    of the same page counted as a second invoice, so a spot learned unsettled (the date printed once for the invoice
    date and the due date) was vouched for by that one page and read the invoice date as the due date afterwards."""
    from ap_coder.capture import build_layout
    from ap_coder.capture.supplier import apply_template
    from ap_coder.capture.types import UNSETTLED
    from ap_coder.capture.workflow import learn_from_approval

    store = Store(tmp_path / "private" / "ap_coder.db")
    dated = [(40, 50, "Au bon moulin"), (330, 90, "Date de facture :"), (450, 90, "05/11/2017")]
    first = _pdf(tmp_path / "a.pdf", dated)
    coding = {**ground_truth, "vendor_name": "Au bon moulin", "invoice_date": "2017-11-05", "due_date": "2017-11-05",
              "payment_terms": ""}  # fmt: skip
    inv_id = store.add_invoice(first, coding, {})
    store.approve_invoice(inv_id, coding, "Jordan Lee")
    learn_from_approval(store, inv_id, coding, actor="Jordan Lee")
    store.reopen(inv_id, "Jordan Lee", "wrong cost center")
    store.approve_invoice(inv_id, coding, "Jordan Lee")
    learned = learn_from_approval(store, inv_id, coding, actor="Jordan Lee")
    assert learned["template"]
    template = store.get_supplier_profile(learned["supplier"])["template"]
    assert template["invoices"] == 1
    later = _pdf(tmp_path / "b.pdf", [*dated, (330, 110, "Date d'échéance :"), (450, 110, "05/12/2017")])
    got = apply_template(template, build_layout(later, ocr=False))
    assert got["due_date"] and all(UNSETTLED in r.method for r in got["due_date"])


def test_a_value_corrected_on_an_invoice_approved_again_is_still_learned(tmp_path):
    """What is learned once per page is each value as approved: a value corrected before the second approval is
    learned (and the spot of the value approved first is a miss)."""
    from ap_coder.capture import build_layout
    from ap_coder.capture.supplier import Template, learn

    page = _pdf(tmp_path / "a.pdf", [(40, 50, "Contoso"), (330, 90, "Order Date:"), (450, 90, "2026-03-02"),
                                     (40, 300, "Invoice Date:"), (150, 300, "2026-03-05")])  # fmt: skip
    layout = build_layout(page, ocr=False)
    template = learn(None, layout, {"invoice_date": ("2026-03-02", None)}, document="abc")  # the order date, wrongly
    template = learn(template, layout, {"invoice_date": ("2026-03-02", None)}, document="abc")  # approved again
    spots = Template.from_dict(template.to_dict()).fields["invoice_date"]
    assert [(v.count, v.settled) for v in spots] == [(1, 1)]
    template = learn(template, layout, {"invoice_date": ("2026-03-05", None)}, document="abc")  # corrected
    found = sorted((v.count, v.settled, v.missed) for v in template.fields["invoice_date"])
    assert found == [(1, 0, 1), (1, 1, 0)]
    assert template.invoices == 1


def test_what_capture_found_about_the_page_is_on_the_review_screen(tmp_path, ground_truth):
    """B-267 said "only 20 of its 300 pages were read: check the other pages yourself before approving" on the review
    screen; it was kept with the invoice (it flagged it in the queue) but the review screen, which checks the coding
    again as it is edited, never showed it, nor any other finding about the page (a currency not printed, totals that
    don't add up as printed, fields to check)."""
    from ap_coder.core.store import load_sample_setup

    from .conftest import SAMPLE_STEM, SAMPLES

    store = Store(tmp_path / "private" / "ap_coder.db")
    load_sample_setup(store)
    inv_id = store.add_invoice(SAMPLES / f"{SAMPLE_STEM}.pdf", ground_truth, {})
    store.save_capture(inv_id, {"fields": {}, "page_count": 300, "pages_left_out": 280})
    client = _web(store)
    said = "only 20 of its 300 pages were read"
    assert said in client.get(f"/review/{inv_id}").text.lower()  # the screen starts a check's message with a capital
    checked = client.post(f"/review/{inv_id}/check", data=_form(store, inv_id), headers=PAGE).json()
    assert said in str(checked["sections"]).lower() and checked["allow"]  # a warning: Approve stays open


def _undated(path):
    return _pdf(path, [(40, 50, "Contoso Supplies Ltd."), (40, 64, "12 Main Street, Toronto ON M5V 2T6"),
                       (360, 50, "INVOICE"), (360, 70, "Invoice # INV-5521"),
                       (40, 160, "Description"), (300, 160, "Qty"), (360, 160, "Price"), (460, 160, "Amount"),
                       (40, 180, "Widgets"), (300, 180, "2"), (360, 180, "50.00"), (460, 180, "100.00"),
                       (360, 240, "Subtotal"), (460, 240, "100.00"), (360, 256, "HST 13%"), (460, 256, "13.00"),
                       (360, 272, "Total"), (460, 272, "113.00")])  # fmt: skip


def test_an_invoice_with_no_date_found_says_the_date_was_assumed(tmp_path):
    """An invoice printing no date the readers found was given the day it was read as its invoice date (so its due
    date, its G/L period and its tax period), shown on the review screen like a date read from the page, and nothing
    said so: no check, no flag in the queue (ca_ubc_google: Ready, 2026-10-21)."""
    import dataclasses as dc
    import datetime as dt

    from ap_coder.ai.pipeline import InvoicePipeline
    from ap_coder.core.store import load_sample_setup

    store = Store(tmp_path / "private" / "ap_coder.db")
    load_sample_setup(store)
    settings = Settings()
    settings = dc.replace(settings, llm=dc.replace(settings.llm, provider="off"))
    result = InvoicePipeline(settings, store.reference_data(), store=store).process(_undated(tmp_path / "a.pdf"))
    assert result.ok, result.error
    today = dt.date.today().isoformat()
    assert result.output["invoice_date"] == today
    found = [i for i in result.report.issues if i.code == "INVOICE_DATE_NOT_FOUND"]
    assert [i.severity for i in found] == ["warning"] and today in found[0].message
    stored = store.get_invoice(result.invoice_id)
    assert "INVOICE_DATE_NOT_FOUND" in {i["code"] for i in stored["validation"]["issues"]}
    from ap_coder.coding.bulk import sort_for_bulk

    assert result.invoice_id not in {i["id"] for i in sort_for_bulk(store)[0]}  # never approved in bulk
    client = _web(store)
    data = _form(store, result.invoice_id)
    assert "INVOICE_DATE_NOT_FOUND" in str(client.post(f"/review/{result.invoice_id}/check", data=data,
                                                       headers=PAGE).json()["sections"])  # fmt: skip
    data["invoice_date"] = "2026-09-30"  # the reviewer puts in the date (from the email, say): no longer assumed
    sections = str(client.post(f"/review/{result.invoice_id}/check", data=data, headers=PAGE).json()["sections"])
    assert "INVOICE_DATE_NOT_FOUND" not in sections
    # The reviewer approves it with the assumed date (it is the right one), then reopens it: the date was confirmed.
    store.approve_invoice(result.invoice_id, {**result.output, "invoice_date": today}, "Jordan Lee")
    store.reopen(result.invoice_id, "Jordan Lee", "cost center")
    sections = str(client.post(f"/review/{result.invoice_id}/check", data=_form(store, result.invoice_id),
                               headers=PAGE).json()["sections"])  # fmt: skip
    assert "INVOICE_DATE_NOT_FOUND" not in sections


def test_the_review_screen_leaves_the_fields_to_check_to_their_highlight(tmp_path, ground_truth):
    """ "check on the page: …" is on nearly every invoice and the fields are highlighted in amber already: on the
    review screen's checks it was noise (B-458's review)."""
    from ap_coder.core.store import load_sample_setup

    from .conftest import SAMPLE_STEM, SAMPLES

    store = Store(tmp_path / "private" / "ap_coder.db")
    load_sample_setup(store)
    inv_id = store.add_invoice(SAMPLES / f"{SAMPLE_STEM}.pdf", ground_truth, {})
    field = {"field": "po_number", "value": "PO-88213", "confidence": 0.6, "status": "check"}
    store.save_capture(inv_id, {"fields": {"po_number": field}, "page_count": 1})
    checked = _web(store).post(f"/review/{inv_id}/check", data=_form(store, inv_id), headers=PAGE).json()
    assert "check on the page" not in str(checked["sections"])


def test_long_lists_are_saved_on_every_page_that_posts_them(tmp_path):
    """B-455 raised the form limit for the review screen and Exports only: a GL list of 400 accounts could not be
    saved on GL accounts & tax (three fields an account), nor 1,200 invoices approved in bulk or lessons forgotten
    (B-461)."""
    from ap_coder.core.store import load_sample_setup

    store = Store(tmp_path / "private" / "ap_coder.db")
    load_sample_setup(store)
    client = _web(store)
    n = 400
    data = {"code": [str(6000 + i) for i in range(n)], "description": [f"Account {i}" for i in range(n)],
            "category": ["Expense"] * n}  # fmt: skip
    saved = client.post("/accounts/gl_accounts/save", data=data, headers=PAGE)
    assert saved.status_code == 200 and saved.json().get("ok"), saved.text[:200]
    assert len(store.list_accounts("gl_accounts")) == n
    bulk = client.post("/review/bulk-approve", data={"ids": list(range(1, 1201))}, headers=PAGE)
    assert bulk.status_code == 200, bulk.text[:200]
    forget = client.post("/learning/memory/forget", data={"ids": list(range(1, 1201))}, headers=PAGE)
    assert forget.status_code == 200, forget.text[:200]


def test_the_po_company_goes_out_only_with_the_po_number():
    """With "Send the PO number" off, the pay item had no PO number (VLPO) nor PO type (VLPDCT) but still the PO
    company (VLPKCO), where docs/JDE_E1.md says all three go out together when there is a PO to send."""
    from ap_coder.erp import jde

    from .test_jde import ON, _files, _invoice

    off = _files([_invoice(ON)], jde.JdeSettings(send_po_reference=False))[jde.HEADER_FILE][0]
    assert (off["VLPO"], off["VLPDCT"], off["VLPKCO"]) == ("", "", "")
    on = _files([_invoice(ON)], jde.JdeSettings())[jde.HEADER_FILE][0]
    assert (on["VLPO"], on["VLPDCT"], on["VLPKCO"]) == ("PO-88213", "OP", "00001")


def test_a_value_corrected_onto_another_fields_words_is_not_settled_there(tmp_path):
    """B-454's first fix looked for the corrected values only: a due date corrected to the day printed for the invoice
    date was taken as printed alone there and its spot settled, so the next invoice's due date was read, vouched for,
    as its invoice date (B-460)."""
    from ap_coder.capture import build_layout
    from ap_coder.capture.supplier import apply_template, learn
    from ap_coder.capture.types import UNSETTLED

    head = [(40, 50, "Contoso Supplies"), (330, 110, "Invoice #")]
    page = build_layout(_pdf(tmp_path / "a.pdf", [*head, (330, 90, "Date:"), (450, 90, "05/11/2017"),
                                                  (450, 110, "INV-100")]), ocr=False)  # fmt: skip
    first = {"invoice_date": ("2017-11-05", None), "due_date": ("2017-12-05", None),
             "invoice_number": ("INV-100", None)}  # fmt: skip
    template = learn(None, page, first, document="a")
    template = learn(template, page, {**first, "due_date": ("2017-11-05", None)}, document="a")  # corrected
    later = build_layout(_pdf(tmp_path / "b.pdf", [*head, (330, 90, "Date:"), (450, 90, "06/01/2018"),
                                                   (450, 110, "INV-200"), (330, 130, "Due date:"),
                                                   (450, 130, "06/02/2018")]), ocr=False)  # fmt: skip
    got = apply_template(template.to_dict(), later).get("due_date") or []
    assert all(UNSETTLED in r.method for r in got if r.value == "2018-01-06")


def test_a_page_approved_with_a_value_then_another_then_the_first_again_counts_it_once(tmp_path):
    from ap_coder.capture import build_layout
    from ap_coder.capture.supplier import learn

    page = build_layout(_pdf(tmp_path / "a.pdf", [(40, 50, "Contoso"), (330, 90, "Invoice Date:"),
                                                  (450, 90, "2026-03-05"), (40, 300, "Ship Date:"),
                                                  (150, 300, "2026-03-07")]), ocr=False)  # fmt: skip
    template = None
    for value in ("2026-03-05", "2026-03-07", "2026-03-05"):
        template = learn(template, page, {"invoice_date": (value, None)}, document="sha")
    assert template.invoices == 1
    assert max(v.count for v in template.fields["invoice_date"]) == 1


def test_a_value_put_back_on_a_page_approved_again_still_misses_the_spot_corrected_away(tmp_path):
    """Invoice date approved as the printed 2026-03-05, corrected to the ship date 2026-03-07, then put back: the
    put-back value was skipped as learned from this page already, so the ship date's spot, settled at the correction,
    missed nothing and the next invoice's ship date was read as its invoice date, vouched for (B-462)."""
    from ap_coder.capture import build_layout
    from ap_coder.capture.supplier import apply_template, learn
    from ap_coder.capture.types import UNSETTLED

    def page(name, invoice, order, ship):
        lines = [(40, 50, "Contoso"), (330, 90, "Invoice Date:"), (450, 90, invoice), (330, 110, "Order Date:"),
                 (450, 110, order), (40, 300, "Ship Date:"), (150, 300, ship)]  # fmt: skip
        return build_layout(_pdf(tmp_path / name, lines), ocr=False)

    first = page("a.pdf", "2026-03-05", "2026-03-05", "2026-03-07")
    template = None
    for value in ("2026-03-05", "2026-03-07", "2026-03-05"):
        template = learn(template, first, {"invoice_date": (value, None)}, document="sha")
    got = apply_template(template.to_dict(), page("b.pdf", "2026-04-09", "2026-04-01", "2026-04-11"))
    assert all(UNSETTLED in r.method for r in got.get("invoice_date", []) if r.value == "2026-04-11")


def test_the_queue_search_and_vendor_order_read_accents_as_people_do():
    """Typing "quebec" in the review queue's search found no "Hydro-Québec", and "Vendor A–Z" put "Énergir" after
    "Zenith" (é sorts after z by its code)."""
    from ap_coder.web.pages.review import filter_queue, sort_queue

    rows = [{"vendor_name": "Zenith Supply"}, {"vendor_name": "Hydro-Québec"}, {"vendor_name": "Énergir"}]
    assert [r["vendor_name"] for r in filter_queue(rows, "quebec")] == ["Hydro-Québec"]
    assert [r["vendor_name"] for r in filter_queue(rows, "ENERGIR")] == ["Énergir"]
    assert [r["vendor_name"] for r in sort_queue(rows, "vendor")] == ["Énergir", "Hydro-Québec", "Zenith Supply"]
