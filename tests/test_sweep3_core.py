"""The third sweep's fixes in ap_coder/core: lessons withdrawn with an approval, imports that would wipe the GL list,
semicolon CSVs, backups dated in the future, re-keyed supplier profiles and lessons, amounts as people write them,
an old database's safety copy, Excel's trailing blank rows and places abroad."""

from __future__ import annotations

import copy

from ap_coder.ai import lessons
from ap_coder.capture.workflow import learn_from_approval
from ap_coder.core.store import Store, load_sample_setup

PAGE = {"X-AP-Coder": "1"}

# --- B-158: a withdrawn approval stops teaching ---------------------------------------------------------------------


def _approve(store, invoice_id, coding):
    store.approve_invoice(invoice_id, copy.deepcopy(coding), "Jane")
    learn_from_approval(store, invoice_id, copy.deepcopy(coding), actor="Jane")


def _laptop_lesson(store):
    return next(r for r in store.lessons_for("northwind it solutions") if r["pattern"].startswith("dell latitude"))


def test_a_withdrawn_approval_no_longer_teaches_its_lessons(tmp_path, ground_truth):
    store = Store(tmp_path / "a.db")
    load_sample_setup(store)
    pdf = tmp_path / "inv.pdf"
    pdf.write_bytes(b"%PDF-1.4 x")
    iid = store.add_invoice(pdf, copy.deepcopy(ground_truth), {"requires_review": True})
    wrong = copy.deepcopy(ground_truth)
    wrong["line_items"][0]["predicted_gl_code"] = "6999"  # a slip, approved
    _approve(store, iid, wrong)
    assert any("GL 6999" in line for line in lessons.prompt_lines(store, wrong["vendor_name"], lessons.INVOICE))
    store.reopen(iid, "Jane", "wrong GL")  # withdrawn: the AI is no longer told 6999
    assert lessons.prompt_lines(store, wrong["vendor_name"], lessons.INVOICE) == []
    _approve(store, iid, ground_truth)  # approved as read: nothing corrected
    assert lessons.prompt_lines(store, wrong["vendor_name"], lessons.INVOICE) == []
    lesson = _laptop_lesson(store)
    assert (lesson["seen"], lesson["corrected"], lesson["since_why"]) == (1, 0, 0)
    store.reopen(iid, "Jane")
    store.reject_invoice(iid, "Jane", "duplicate")
    assert all(r["seen"] == 0 for r in store.lessons_for("northwind it solutions"))


def test_a_reopened_and_reapproved_correction_counts_once(tmp_path, ground_truth):
    store = Store(tmp_path / "a.db")
    load_sample_setup(store)
    pdf = tmp_path / "inv.pdf"
    pdf.write_bytes(b"%PDF-1.4 y")
    iid = store.add_invoice(pdf, copy.deepcopy(ground_truth), {"requires_review": True})
    second = tmp_path / "other.pdf"
    second.write_bytes(b"%PDF-1.4 other")
    other = store.add_invoice(second, copy.deepcopy(ground_truth), {"requires_review": True})
    earlier = copy.deepcopy(ground_truth)
    earlier["line_items"][0]["predicted_gl_code"] = "6050"
    _approve(store, other, earlier)  # another invoice, corrected another way
    fixed = copy.deepcopy(ground_truth)
    fixed["line_items"][0]["predicted_gl_code"] = "6999"
    _approve(store, iid, fixed)
    store.reopen(iid, "Jane", "date")  # reopened to fix the date, approved again
    assert _laptop_lesson(store)["value"] == "6050"  # the other invoice's correction is the one left
    _approve(store, iid, fixed)
    lesson = _laptop_lesson(store)
    assert (lesson["seen"], lesson["corrected"], lesson["since_why"], lesson["value"]) == (2, 2, 2, "6999")


def test_an_invoice_deleted_with_its_lessons_forgets_them(tmp_path, ground_truth):
    store = Store(tmp_path / "a.db")
    load_sample_setup(store)
    pdf = tmp_path / "inv.pdf"
    pdf.write_bytes(b"%PDF-1.4 z")
    iid = store.add_invoice(pdf, copy.deepcopy(ground_truth), {"requires_review": True})
    wrong = copy.deepcopy(ground_truth)
    wrong["line_items"][0]["predicted_gl_code"] = "6999"
    _approve(store, iid, wrong)
    store.delete_invoice(iid, forget_lessons=True)
    assert lessons.prompt_lines(store, wrong["vendor_name"], lessons.INVOICE) == []
