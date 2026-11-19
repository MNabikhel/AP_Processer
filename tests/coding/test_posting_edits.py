"""JD Edwards' posting edit codes L (a subledger and its type required) and U (units required) on an invoice line
(D-268): filled from the PO line, the supplier's past coding or the line's quantity, else a finding; sent with the
voucher. Every name and number is made up."""

from __future__ import annotations

import hashlib

from ap_coder.ai.pipeline import finalise_coding
from ap_coder.coding import posting_edits
from ap_coder.core.chart import BuRow, ChartRow
from ap_coder.core.config import Settings
from ap_coder.core.schema import InvoiceCoding
from ap_coder.erp import journal as jr
from ap_coder.web.invoice_findings import is_hard

MAIN = "0042061010"
SUPPLIER = "Northern Gravel Ltd."


def _chart(store):
    """A business unit with an ordinary account (5410), a job cost account taking a subledger (5480, L), a fuel account
    taking units (5490, U), and the GST account."""
    store.import_business_units([BuRow(MAIN, "Yard Operations", "00420", "AD", "ACT")])
    store.import_chart([ChartRow(MAIN, "", "", "Yard Operations", 3, "N"),
                        ChartRow(MAIN, "5410", "", "Stationery", 6, ""),
                        ChartRow(MAIN, "5480", "", "Job Costs", 6, "L"),
                        ChartRow(MAIN, "5490", "", "Fuel by the Litre", 6, "U"),
                        ChartRow(MAIN, "2310", "", "GST Paid", 6, "")])  # fmt: skip
    store.set_tax_treatment("GST", "recoverable", "2310")
    return store.reference_data()


def _coding(*lines, po=""):
    """An invoice of ``lines`` (GL code, amount, quantity) on ``MAIN``, GST at 5%."""
    items = [{"line_number": n, "description": f"Line {n}", "quantity": qty, "unit_price": amount / (qty or 1),
              "amount": amount, "predicted_gl_code": gl, "predicted_cost_center": MAIN, "taxes_applied": ["GST"]}
             for n, (gl, amount, qty) in enumerate(lines, 1)]  # fmt: skip
    subtotal = round(sum(amount for _, amount, _ in lines), 2)
    tax = round(subtotal * 0.05, 2)
    return InvoiceCoding.model_validate({
        "vendor_name": SUPPLIER, "invoice_number": "G-1001", "invoice_date": "2026-03-02", "currency": "CAD",
        "po_number": po, "subtotal": subtotal, "tax_total": tax, "grand_total": round(subtotal + tax, 2),
        "confidence_score": 0.95, "ship_to_province": "AB", "line_items": items,
        "tax_lines": [{"tax_type": "GST", "province": "", "rate": 0.05, "taxable_amount": subtotal,
                       "tax_amount": tax}],
    })  # fmt: skip


def _journal(store, *lines):
    """A past voucher of the supplier: each line (GL code, amount, subledger, its type, units) on ``MAIN``."""
    coded = [jr.Line(n, f"{MAIN}.{gl}", MAIN, gl, "", "", amount, "", jr.CODING, sbl, sblt, units)
             for n, (gl, amount, sbl, sblt, units) in enumerate(lines, 8)]  # fmt: skip
    gross = round(sum(line.amount for line in coded), 2)
    payable = jr.Line(99, f"{MAIN}.2110", MAIN, "2110", "", "", -gross, "", jr.PAYABLE)
    voucher = jr.Voucher(8, "PV", "00420", "2026-01-15", "51001", SUPPLIER.upper(), (*coded, payable), gross, 0.0,
                         False, False, True, False)  # fmt: skip
    totals = jr.Totals(1, len(coded) + 1, len(coded), 1, 0, "2026-01-15", "2026-01-15", 0.0, 0.0, None, None, False)
    digest = hashlib.sha256(repr(lines).encode()).hexdigest()
    store.import_journal(jr.Journal((voucher,), (), totals), source_name="gl.csv", source_hash=digest)


def _found(report):
    return [(i.line_number, i.code) for i in report.issues if i.code.startswith("JDE_S") or i.code.startswith("JDE_U")]


def test_a_line_on_an_account_that_takes_a_subledger_needs_one_before_approval(store):
    reference = _chart(store)
    coding = _coding(("5410", 100.0, 1), ("5480", 250.0, 1))
    output, report = finalise_coding(coding, reference, Settings(), store=store)
    assert _found(report) == [(2, "JDE_SUBLEDGER_NEEDED")]
    issue = next(i for i in report.issues if i.code == "JDE_SUBLEDGER_NEEDED")
    assert issue.severity == "error" and is_hard(issue) and report.requires_review
    assert "posting edit code is L" in issue.message
    coding.line_items[1].subledger, coding.line_items[1].subledger_type = "4500123", "W"
    output, report = finalise_coding(coding, reference, Settings(), store=store)
    assert _found(report) == []
    assert output["line_items"][1]["subledger"] == "4500123" and output["line_items"][1]["subledger_type"] == "W"
    assert "subledger" not in output["line_items"][0]  # an account without L keeps none


def test_the_subledger_comes_from_the_suppliers_past_coding_of_the_account(store):
    reference = _chart(store)
    _journal(
        store,
        ("5480", 300.0, "4500777", "W", None),
        ("5480", 120.0, "4500777", "W", None),
        ("5480", 80.0, "4500111", "W", None),
        ("5410", 40.0, "", "", None),
    )
    output, report = finalise_coding(_coding(("5480", 250.0, 1)), reference, Settings(), store=store)
    assert _found(report) == []
    assert (output["line_items"][0]["subledger"], output["line_items"][0]["subledger_type"]) == ("4500777", "W")


def test_the_subledger_comes_from_the_po_line_first(store):
    reference = _chart(store)
    _journal(store, ("5480", 300.0, "4500777", "W", None))
    store.import_purchase_orders([
        {"po_number": "PO-88", "vendor_name": SUPPLIER, "line_number": 1, "description": "Line 1", "quantity": 1,
         "unit_price": 250.0, "amount": 250.0, "gl_code": "5480", "cost_center": MAIN, "subledger": "4500999",
         "subledger_type": "W"}])  # fmt: skip
    output, _ = finalise_coding(_coding(("5480", 250.0, 1), po="PO-88"), reference, Settings(), store=store)
    assert (output["line_items"][0]["subledger"], output["line_items"][0]["subledger_type"]) == ("4500999", "W")


def test_a_line_on_an_account_that_takes_units_takes_its_quantity_else_needs_them(store):
    reference = _chart(store)
    output, report = finalise_coding(_coding(("5490", 180.0, 120)), reference, Settings(), store=store)
    assert _found(report) == [] and output["line_items"][0]["units"] == 120
    coding = _coding(("5490", 180.0, 0))
    _, report = finalise_coding(coding, reference, Settings(), store=store)
    assert _found(report) == [(1, "JDE_UNITS_NEEDED")]


def test_with_l_no_longer_postable_the_account_is_refused_not_asked_for_a_subledger(store):
    reference = _chart(store)
    from dataclasses import replace

    store.save_chart_settings(replace(store.chart_settings(), postable_codes=("", "U")))
    reference = store.reference_data()
    assert posting_edits.needed(reference, {"predicted_gl_code": "5480", "predicted_cost_center": MAIN}) == ""
    _, report = finalise_coding(_coding(("5480", 250.0, 1)), reference, Settings(), store=store)
    assert [i.code for i in report.issues if i.line_number == 1] == ["ACCOUNT_NOT_POSTABLE"]


def _approved(*extra):
    """An approved invoice: a stationery line, a job cost line with its subledger, a fuel line with its units."""
    from tests.erp.test_jde_gl_mode import coded, doc, line, tl

    lines = [
        line(1, 100.0, "5410", ["GST"], cc=MAIN),
        {**line(2, 250.0, "5480", ["GST"], cc=MAIN), "subledger": "4500777", "subledger_type": "W"},
        {**line(3, 180.0, "5490", ["GST"], cc=MAIN), "quantity": 120, "unit_price": 1.5, "units": 120.0},
    ]
    return coded(doc(lines, [tl("GST", "", 0.05, 530.0, 26.5)], prov="AB", vendor_name=SUPPLIER,
                     invoice_number="G-1001", invoice_date="2026-03-02"))  # fmt: skip


def test_the_keying_sheet_and_the_batch_files_carry_each_lines_subledger_and_units():
    import io

    import openpyxl

    from ap_coder.erp import jde_keying, jde_settings, jde_zfiles

    js = jde_settings.JdeSettings(amount_mode=jde_settings.AMOUNT_TAX)
    data, _, _ = jde_keying.build([_approved()], js, lambda final: "51001", batch=1)
    ws = openpyxl.load_workbook(io.BytesIO(data))[jde_keying.SHEET]
    assert [c.value for c in ws[1]] == [*jde_keying.LINE_COLUMNS, *jde_keying.LEDGER_COLUMNS]
    keyed = [[c.value for c in row][:7] for row in ws.iter_rows(min_row=2) if row[0].value
             and str(row[0].value).startswith(MAIN)]  # fmt: skip
    assert [row[4:] for row in keyed] == [[None, None, None], ["4500777", "W", None], [None, None, 120]]
    _, dist, _ = jde_zfiles.voucher_rows(_approved(), "51001", js)
    assert [(r["VNSBL"], r["VNSBLT"], r["VNU"]) for r in dist] == [("", "", ""), ("4500777", "W", ""),
                                                                 ("", "", "120")]  # fmt: skip
    assert "VNU" in jde_zfiles.DIST_COLUMNS


def test_a_keying_sheet_with_no_subledger_or_units_keeps_its_four_columns():
    import io

    import openpyxl

    from ap_coder.erp import jde_keying, jde_settings
    from tests.erp.test_jde_gl_mode import coded, doc, line, tl

    plain = coded(doc([line(1, 100.0, "5410", ["GST"], cc=MAIN)], [tl("GST", "", 0.05, 100.0, 5.0)], prov="AB"))
    data, _, _ = jde_keying.build([plain], jde_settings.JdeSettings(), lambda final: "51001", batch=1)
    ws = openpyxl.load_workbook(io.BytesIO(data))[jde_keying.SHEET]
    assert [c.value for c in ws[1]] == jde_keying.LINE_COLUMNS
