"""The CPA's final check of the line tax codes (D-64): B-719 to B-723."""

from __future__ import annotations

import datetime as dt

from ap_coder.ai.offline_coder import tax_codes
from ap_coder.coding.line_tax import confirm_line, exemption_to_confirm, inferred_note, tax_code_of
from ap_coder.coding.tax import check_taxes
from ap_coder.coding.taxmath import math_check
from ap_coder.core.schema import LineItem, TaxLine

from .test_line_tax_review import _invoice


def _bc():
    lines = [LineItem(line_number=i + 1, description=d, amount=a, quantity=1, unit_price=a, predicted_gl_code="6000")
             for i, (d, a) in enumerate([("Laptop", 1000.0), ("Bookkeeping setup", 300.0)])]  # fmt: skip
    taxes = [TaxLine(tax_type="GST", rate=0.05, taxable_amount=1300.0, tax_amount=65.0),
             TaxLine(tax_type="PST", province="BC", rate=0.07, taxable_amount=1000.0, tax_amount=70.0)]  # fmt: skip
    return lines, taxes


# --- B-719: no PST number printed, but PST charged: the vendor collects it ------------------------------------------


def test_a_missing_pst_number_does_not_make_a_line_due_when_pst_is_charged_b719():
    lines, taxes = _bc()
    got = tax_codes(lines, taxes, [], "", "BC", "2026-06-01", supplier_province="BC", pst_number="")
    assert [tax_code_of(li) for li in got] == ["GST+PST", "GST~PST"]
    assert exemption_to_confirm(got[1]) == "PST"  # asked, as B-715 asks it


# --- B-720: QST not charged is not "self-assess" in the Tax check either -------------------------------------------


def test_the_tax_check_says_qst_not_charged_without_self_assess_b720(reference):
    coding = _invoice("QC", [("Software", 1000.0, "GST")], [("GST", "", 0.05, 1000.0, 50.0)], prefilled=False)
    row = next(r for r in math_check(coding, reference.tax)["rows"] if r["kind"] == "selfassess")
    assert row["pill"] == "QST not charged" and "not charged: self-assess" not in row["detail"]
    assert "generally owes nothing net" in row["detail"]  # the B-718 note
    pst = _invoice("BC", [("Laptop", 1000.0, "GST")], [("GST", "", 0.05, 1000.0, 50.0)], prefilled=False)
    assert next(r for r in math_check(pst, reference.tax)["rows"] if r["kind"] == "selfassess")["pill"] == "self-assess"


# --- B-721: HST not charged: s.165, not s.218.1 ------------------------------------------------------------------


def test_hst_not_charged_cites_the_recipients_liability_under_s165_b721(reference):
    on = _invoice("ON", [("Software", 1000.0)], [("GST", "", 0.05, 1000.0, 50.0)], supplier="AB")
    message = next(f.message for f in check_taxes(on, reference.tax) if f.code == "HST_NOT_CHARGED")
    assert "you remain liable for the provincial part (ETA s.165): ask for a corrected invoice" in message
    assert "218.1" not in message and "the GST is claimable" in message
    from ap_coder.ops.help import CHECKS

    assert "218.1" not in CHECKS["HST_NOT_CHARGED"].action and "s.165" in CHECKS["HST_NOT_CHARGED"].action


# --- B-722: the Self-assessment report says what JD Edwards accrues itself -------------------------------------


def test_the_self_assessment_report_marks_invoices_jd_edwards_accrues_with_code_b_b722(tmp_path):
    from ap_coder.coding import taxreturn
    from ap_coder.core.store import Store

    from .test_jde import SK
    from .test_jde import _invoice as jde_invoice

    store = Store(tmp_path / "private" / "ap_coder.db")
    final = jde_invoice(SK)["final_output"]
    final["tax_lines"] = [t for t in final["tax_lines"] if t["tax_type"] != "PST"]
    final["tax_total"] = sum(t["tax_amount"] for t in final["tax_lines"])
    final["grand_total"] = round(final["subtotal"] + final["tax_total"], 2)
    for li in final["line_items"]:
        li["taxes_applied"], li["tax_exempt"] = ["GST"], []
    from .conftest import SAMPLES

    ids = []
    for _ in range(2):
        iid = store.add_invoice(SAMPLES / f"{SK}.pdf", final, {"requires_review": True})
        store.approve_invoice(iid, final, "Pat Tester")
        ids.append(iid)
    store.create_export_batch([ids[0]], "jde", actor="Pat Tester")
    day = dt.date.fromisoformat(final["invoice_date"])
    items = {s.invoice_id: s for s in taxreturn.self_assessment(store, day, day)}
    assert "JD Edwards accrues this one (code B): don't book it again from this report" == items[ids[0]].note
    assert items[ids[1]].note == ""
    assert b"JD Edwards accrues this one" in taxreturn.self_assessment_csv(list(items.values()), day, day)


# --- B-723: an inferred exemption picked again is confirmed --------------------------------------------------------


def test_picking_the_inferred_exemption_again_records_it_as_confirmed_b723():
    reason = f"Bookkeeping. {inferred_note('PST')}"
    away = confirm_line(reason, "GST")  # changed to "not charged (self-assess)"
    back = confirm_line(away, "GST~PST", inferred=reason)  # and back to "PST exempt": an explicit pick
    assert "PST exemption confirmed on review" in back and "inferred" not in back
    assert "confirmed" not in confirm_line(away, "GST", inferred=reason)
