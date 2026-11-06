"""Learning → Coding accuracy → Corrections: each correction AP made (a field, a line's GL account, cost center or tax
code), what AP Coder proposed and what AP approved, and whether AP Coder had it right the next time (D-118)."""

from __future__ import annotations

import copy
import re

from ap_coder.ai import lessons
from ap_coder.core.store import Store
from tests.webclient import client_for


def _approve(store: Store, tmp_path, ground_truth, n: int, proposed_gl: str, final_gl: str, *,
             number_read: str = "", tax_exempt: bool = False) -> int:  # fmt: skip
    proposed = copy.deepcopy(ground_truth)
    proposed["invoice_number"] = number_read or f"NW-{n}"
    proposed["line_items"][0]["predicted_gl_code"] = proposed_gl
    final = copy.deepcopy(proposed)
    final["invoice_number"] = f"NW-{n}"
    final["line_items"][0]["predicted_gl_code"] = final_gl
    if tax_exempt:
        final["line_items"][4].update(taxes_applied=[], tax_exempt=["HST"])
    iid = store.add_invoice(tmp_path / f"{n}.pdf", proposed, None)
    store.approve_invoice(iid, final, "Jane")
    lessons.record(store, iid, proposed, final, "Jane")  # as the dashboard's approval does
    return iid


def test_each_correction_and_whether_it_was_right_the_next_time(tmp_path, ground_truth):
    store = Store(tmp_path / "ap.db")
    first = _approve(store, tmp_path, ground_truth, 1, "6010", "6999", number_read="ACCT 1", tax_exempt=True)
    second = _approve(store, tmp_path, ground_truth, 2, "6999", "6999", number_read="ACCT 2")
    found = store.corrections()
    by_field = {c["field"]: c for c in found}
    assert set(by_field) == {lessons.LINE_GL, "invoice_number", "tax_code"}
    gl = by_field[lessons.LINE_GL]
    assert (gl["invoice_id"], gl["proposed"], gl["value"], gl["next"]) == (first, "6010", "6999", "right")
    assert gl["next_invoice_id"] == second and gl["pattern"] == lessons.line_pattern(
        "Dell Latitude 7450 laptop, 32GB RAM"
    )
    number = by_field["invoice_number"]
    assert (number["proposed"], number["value"], number["next"]) == ("ACCT 1", "NW-1", "again")
    tax = by_field["tax_code"]
    assert tax["invoice_id"] == first and tax["proposed"] and tax["value"] != tax["proposed"]
    assert tax["next"] == "right" and tax["pattern"] == "Delivery & handling"

    page = client_for(store).get("/learning").text
    assert page.index("Corrections") < page.index("Teach from past coding")  # the first thing on the page
    assert "GL account of lines like" in page and "Right the next time" in page and "Corrected again" in page
    assert "Tax code of “Delivery &amp; handling”" in page
    # The page never calls the pilot tester a "manager" (the menu's Manager confirmations, a cost center's manager
    # confirming the coding, D-138, is another person).
    assert "manager" not in re.sub(r"(?s)<nav.*?</nav>", "", page).lower()


def test_no_corrections_section_before_any_correction(tmp_path, ground_truth):
    store = Store(tmp_path / "ap.db")
    _approve(store, tmp_path, ground_truth, 1, "6010", "6010")
    assert store.corrections() == []
    assert 'id="corrections"' not in client_for(store).get("/learning").text


def test_forgetting_the_line_takes_its_correction_off_the_list(tmp_path, ground_truth):
    store = Store(tmp_path / "ap.db")
    _approve(store, tmp_path, ground_truth, 1, "6010", "6999")
    assert [c["field"] for c in store.corrections()] == [lessons.LINE_GL]
    ids = [r["id"] for r in store.feedback_rows() if r["description"].startswith("Dell Latitude")]
    store.delete_feedback(ids)
    assert store.corrections() == []


def test_a_line_description_corrected_on_its_own_shows_on_the_learning_page(tmp_path, monkeypatch):
    """B-1839: the Corrections list, settled and shared (D-179), followed the lessons and the invoices but not the
    line descriptions AP corrected (``line_texts``), which an approval keeps in a write of their own after its
    lessons: a page asked between the two kept a list without the correction, and with nothing else changing it
    never came, a day later still."""
    from pathlib import Path

    from ap_coder.core.store import Store, reports
    from ap_coder.web.pages import learning

    clock = [1000.0]
    monkeypatch.setattr(reports, "wall", lambda: clock[0])
    store = Store(tmp_path / "ap.db")
    invoice = store.add_invoice(Path(tmp_path / "a.pdf"), {"vendor_name": "Acme", "invoice_number": "A-1",
                                                          "line_items": []}, {})  # fmt: skip

    def shown() -> list[tuple[str, str]]:
        return [(r["field"], r["value"]) for r in learning.corrections_context(store)["fixes"]]

    assert shown() == []
    store.remember_line_texts("name:acme", [{"read_key": "k1", "printed": "Bolts sz 1", "description": "Bolts size 1",
                                             "corrected": True}], invoice_id=invoice)  # fmt: skip
    clock[0] += 86400
    assert shown() == [("line_description", "Bolts size 1")]
