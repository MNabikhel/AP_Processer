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


def test_the_queue_search_and_vendor_order_read_accents_as_people_do():
    """Typing "quebec" in the review queue's search found no "Hydro-Québec", and "Vendor A–Z" put "Énergir" after
    "Zenith" (é sorts after z by its code)."""
    from ap_coder.web.pages.review import filter_queue, sort_queue

    rows = [{"vendor_name": "Zenith Supply"}, {"vendor_name": "Hydro-Québec"}, {"vendor_name": "Énergir"}]
    assert [r["vendor_name"] for r in filter_queue(rows, "quebec")] == ["Hydro-Québec"]
    assert [r["vendor_name"] for r in filter_queue(rows, "ENERGIR")] == ["Énergir"]
    assert [r["vendor_name"] for r in sort_queue(rows, "vendor")] == ["Énergir", "Hydro-Québec", "Zenith Supply"]
