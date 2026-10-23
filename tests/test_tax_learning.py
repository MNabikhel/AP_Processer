"""Line tax codes learned from what AP approved (D-70): each approval records the line's tax code beside its GL account
(``feedback.final_tax_code``, schema 17); a code the vendor's history agrees on (similar line, at least two approvals,
never contradicted) is used before the subset-sum guess, codes printed on the invoice still win, and the Tax check
still flags what does not add up."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from ap_coder.coding import line_tax
from ap_coder.coding.line_tax import LEARNED_NOTE, learned_codes, prefill, tax_code_of, tax_learned
from ap_coder.coding.taxmath import math_check
from ap_coder.core.schema import InvoiceCoding, LineItem, TaxLine
from ap_coder.core.store import SCHEMA_VERSION, Store

VENDOR = "Coastal Electrical Ltd."


def _line(n: int, amount: float, description: str, taxes=(), exempt=None) -> LineItem:
    return LineItem(line_number=n, description=description, quantity=1, unit_price=amount, amount=amount,
                    predicted_gl_code="6220", taxes_applied=list(taxes), tax_exempt=exempt)  # fmt: skip


def _tax(kind: str, rate: float, taxable: float, province: str = "BC") -> TaxLine:
    return TaxLine(tax_type=kind, province=province if kind != "GST" else "", rate=rate, taxable_amount=taxable,
                   tax_amount=round(taxable * rate, 2))  # fmt: skip


def _row(invoice_id: int, description: str, code: str, vendor: str = VENDOR) -> dict:
    from ap_coder.core.memory import vendor_key

    return {"id": invoice_id, "invoice_id": invoice_id, "vendor_key": vendor_key(vendor), "vendor_name": vendor,
            "description": description, "final_gl": "6220", "final_cc": "", "outcome": "accepted",
            "final_tax_code": code, "created_at": f"2026-01-{invoice_id:02d}"}  # fmt: skip


def test_two_agreeing_approvals_teach_a_lines_code_one_does_not_and_a_contradiction_stops_it():
    lines = [_line(1, 400.0, "Electrician - labour, 4 h"), _line(2, 120.0, "LED panel 2x4 ft")]
    one = [_row(1, "Electrician - labour", "GST~PST")]
    assert learned_codes(lines, one, VENDOR) == ["", ""]
    two = [*one, _row(2, "ELECTRICIAN - LABOUR 2 h", "GST~PST")]
    assert learned_codes(lines, two, VENDOR) == ["GST~PST", ""]
    assert learned_codes(lines, two, "Another Vendor Inc.") == ["", ""]  # another vendor's history says nothing
    contradicted = [*two, _row(3, "Electrician labour", "GST+PST")]
    assert learned_codes(lines, contradicted, VENDOR) == ["", ""]
    # The same invoice approved twice (reopened, approved again) is one approval, not two.
    assert learned_codes(lines, [one[0], {**one[0], "id": 9}], VENDOR) == ["", ""]


def test_a_learned_code_comes_before_the_subset_sum_guess_and_is_marked():
    """Labour is exempt from BC PST: the solver alone makes it out from the PST charged, but marks the exemption as
    inferred for a person to confirm; learned from two approvals it is taken as AP approved it, marked as learned."""
    lines = [_line(1, 400.0, "Electrician - labour"), _line(2, 120.0, "LED panel 2x4 ft")]
    taxes = [_tax("GST", 0.05, 520.0), _tax("PST", 0.07, 120.0)]
    guessed = prefill(lines, taxes)
    assert guessed.how == ["solver", "solver"] and line_tax.exemption_to_confirm(guessed.lines[0]) == "PST"
    learned = prefill(lines, taxes, learned=["GST~PST", ""])
    assert learned.how[0] == "learned" and learned.how[1] in ("solver", "all")
    assert tax_code_of(learned.lines[0]) == "GST~PST" and tax_code_of(learned.lines[1]) == "GST+PST"
    assert tax_learned(learned.lines[0]) and LEARNED_NOTE in learned.lines[0].reasoning_justification
    assert not line_tax.exemption_to_confirm(learned.lines[0])  # AP approved it twice: nothing left to confirm
    assert not tax_learned(learned.lines[1])


def test_a_services_only_invoice_learns_the_pst_exemption_it_cannot_see():
    """No PST charged anywhere: the line alone reads as PST due (to self-assess); the vendor's history says exempt."""
    lines = [_line(1, 400.0, "Electrician - labour")]
    taxes = [_tax("GST", 0.05, 400.0)]
    assert tax_code_of(prefill(lines, taxes).lines[0]) == "GST"
    assert tax_code_of(prefill(lines, taxes, learned=["GST~PST"]).lines[0]) == "GST~PST"


def test_codes_printed_on_the_invoice_still_win():
    lines = [_line(1, 400.0, "Electrician - labour")]
    taxes = [_tax("GST", 0.05, 400.0), _tax("PST", 0.07, 400.0)]
    done = prefill(lines, taxes, printed=["G P"], learned=["GST~PST"])
    assert done.how == ["printed"] and tax_code_of(done.lines[0]) == "GST+PST" and not tax_learned(done.lines[0])
    # A printed "G" says only what is charged: the history may add that the PST not charged is exempt, not due.
    alone = prefill(lines, [_tax("GST", 0.05, 400.0)], printed=["G"], learned=["GST~PST"])
    assert alone.how == ["printed"] and tax_code_of(alone.lines[0]) == "GST~PST"
    # A learned code for a tax this invoice does not charge is no help: the usual guess.
    other = prefill(lines, [_tax("GST", 0.05, 400.0)], learned=["GST+PST"])
    assert other.how[0] != "learned" and tax_code_of(other.lines[0]) == "GST"


def test_a_learned_code_never_hides_a_tax_mismatch():
    """The history says labour is PST exempt, but this vendor charged PST on it this time: the learned codes are
    dropped and the lines worked out from the tax charged, marked for a person to check (B-761); a learned code that
    does not add up all the same is flagged by the Tax check, which names the line."""
    lines = [_line(1, 400.0, "Electrician - labour"), _line(2, 120.0, "LED panel 2x4 ft")]
    taxes = [_tax("GST", 0.05, 520.0), _tax("PST", 0.07, 520.0)]
    dropped = prefill(lines, taxes, learned=["GST~PST", "GST+PST"])
    assert [tax_code_of(li) for li in dropped.lines] == ["GST+PST", "GST+PST"]
    assert not tax_learned(dropped.lines[0]) and line_tax.tax_guessed(dropped.lines[0])
    taxes = [_tax("GST", 0.05, 520.0), _tax("PST", 0.07, 300.0)]  # no set of the lines makes it, learned or not
    done = prefill(lines, taxes, learned=["GST~PST", "GST+PST"])
    assert done.how == ["learned", "learned"]
    coding = InvoiceCoding(vendor_name=VENDOR, invoice_number="1", invoice_date="2026-08-21", currency="CAD",
                           supplier_province="BC", ship_to_province="BC", subtotal=520.0, tax_lines=taxes,
                           tax_total=round(sum(t.tax_amount for t in taxes), 2),
                           grand_total=round(520.0 + sum(t.tax_amount for t in taxes), 2), confidence_score=1.0,
                           line_items=done.lines)  # fmt: skip
    check = math_check(coding)
    pst = next(r for r in check["rows"] if r.get("tax_type") == "PST")
    assert not pst["ok"]


def _approved(store: Store, path: Path, description: str, code: str) -> int:
    parsed = line_tax.parse_code(code)
    assert parsed is not None
    line = {"line_number": 1, "description": description, "quantity": 1, "unit_price": 400.0, "amount": 400.0,
            "predicted_gl_code": "6220", "predicted_cost_center": "", "taxes_applied": parsed[0],
            "tax_exempt": parsed[1], "tax_status": parsed[2], "reasoning_justification": ""}  # fmt: skip
    output = {"vendor_name": VENDOR, "invoice_number": path.stem, "invoice_date": "2026-08-21", "currency": "CAD",
              "subtotal": 400.0, "tax_lines": [{"tax_type": "GST", "province": "", "rate": 0.05,
                                                "taxable_amount": 400.0, "tax_amount": 20.0}],
              "tax_total": 20.0, "grand_total": 420.0, "line_items": [line]}  # fmt: skip
    invoice_id = store.add_invoice(path, output, None)
    store.approve_invoice(invoice_id, json.loads(json.dumps(output)), "Clerk")
    return invoice_id


def test_an_approval_records_each_lines_tax_code_and_the_coder_learns_it(tmp_path, reference):
    from ap_coder.ai.offline_coder import code_from_capture
    from ap_coder.capture.types import VERIFIED, CaptureResult, FieldResult, LineReading

    store = Store(tmp_path / "s.db")
    for n in (1, 2):
        _approved(store, tmp_path / f"INV-{n}.pdf", "Electrician - labour", "GST~PST")
    assert [r["final_tax_code"] for r in store.feedback_rows()] == ["GST~PST", "GST~PST"]
    fields = {name: FieldResult(name, value, 0.99, VERIFIED) for name, value in (
        ("vendor_name", VENDOR), ("invoice_number", "INV-3"), ("invoice_date", "2026-09-01"), ("currency", "CAD"),
        ("gst_amount", 20.0), ("grand_total", 420.0), ("pst_registration_number", "PST-1234-5678"))}  # fmt: skip
    capture = CaptureResult(fields=fields, line_items=[LineReading("Electrician - labour", 1.0, 400.0, 400.0)])
    text = "Bill to: Bench Company Ltd.\n100 Main Street\nVancouver BC V6B 1A1"
    coding = code_from_capture(capture, reference, store.feedback_rows(), store, text=text).coding
    assert tax_code_of(coding.line_items[0]) == "GST~PST" and tax_learned(coding.line_items[0])


def test_schema_17_adds_the_tax_code_and_fills_it_from_approved_invoices(tmp_path):
    path = tmp_path / "old.db"
    store = Store(path)
    invoice_id = _approved(store, tmp_path / "INV-1.pdf", "Electrician - labour", "GST~PST")
    with sqlite3.connect(path) as conn:  # what a version-16 database looks like
        conn.execute("ALTER TABLE feedback DROP COLUMN final_tax_code")
        conn.execute("UPDATE settings SET value = '16' WHERE key = 'schema_version'")
    upgraded = Store(path)
    assert SCHEMA_VERSION >= 17 and upgraded.get_setting("schema_version") == str(SCHEMA_VERSION)
    rows = upgraded.feedback_rows()
    assert [(r["invoice_id"], r["final_tax_code"]) for r in rows] == [(invoice_id, "GST~PST")]


def test_picking_a_code_on_screen_makes_it_the_clerks_not_the_historys():
    reason = f"From this vendor's lines. {LEARNED_NOTE}"
    assert line_tax.confirm_line(reason, "GST+PST") == "From this vendor's lines."
    assert "LEARNED_NOTE" in Path("ap_coder/web/static/js/invoice.js").read_text(encoding="utf-8")
    assert LEARNED_NOTE in Path("ap_coder/web/static/js/invoice.js").read_text(encoding="utf-8")
