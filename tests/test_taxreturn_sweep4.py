"""The sales-tax reports of the fourth bug sweep: tax charged in error flagged on the claim (B-925), the
self-assessment tile without what JD Edwards accrues (B-928)."""

import datetime as dt

from ap_coder.ai.pipeline import finalise_coding
from ap_coder.coding import taxreturn
from ap_coder.core.config import Settings
from ap_coder.core.schema import InvoiceCoding
from ap_coder.core.store import Store, load_sample_setup
from ap_coder.erp import jde

from .test_jde_gl_mode import doc, line, tl


def _store(tmp_path):
    store = Store(tmp_path / "t.db")
    load_sample_setup(store)
    return store


def _approve(store, tmp_path, d, fmt="csv", open_issues=None):
    out, rep = finalise_coding(InvoiceCoding.model_validate(d), store.reference_data(), Settings(), store=store)
    i = store.add_invoice(tmp_path / f"{d['invoice_number']}.pdf", out, rep.to_dict())
    store.approve_invoice(i, out, "Jane", open_issues=open_issues)
    if fmt:
        store.create_export_batch([i], fmt)
    return i, rep


def test_tax_charged_in_error_is_a_claim_to_check(tmp_path):
    store = _store(tmp_path)
    ns = doc(
        [line(1, 1000.0, taxes=["HST"], cc="CC400")],
        [tl("HST", "NS", 0.15, 1000.0, 150.0)],
        prov="NS",
        invoice_date="2025-06-10",
        invoice_number="NS-1",
    )  # fmt: skip  (14% then)
    ab = doc(
        [line(1, 1000.0, taxes=["HST"], cc="CC400")],
        [tl("HST", "ON", 0.13, 1000.0, 130.0)],
        prov="AB",
        invoice_date="2025-06-11",
        invoice_number="AB-1",
    )  # fmt: skip  (HST where GST applies)
    ok = doc([line(1, 1000.0, taxes=["HST"], cc="CC400")], [tl("HST", "ON", 0.13, 1000.0, 130.0)],
             invoice_date="2025-06-12", invoice_number="ON-1")  # fmt: skip
    _approve(store, tmp_path, ns)
    _approve(store, tmp_path, ab)
    _approve(store, tmp_path, ok)
    report = taxreturn.build(store, dt.date(2025, 4, 1), dt.date(2025, 6, 30))
    issues = {c.invoice_number: c.issues for c in report.claims}
    assert taxreturn.WRONG_RATE in issues["NS-1"]
    assert taxreturn.NOT_PAYABLE in issues["AB-1"]
    assert not issues["ON-1"]
    assert {c.invoice_number for c in report.at_risk()} == {"NS-1", "AB-1"}
    assert "ETA s.232" in taxreturn.WRONG_RATE and "recover the excess" in taxreturn.WRONG_RATE
    assert b"wrong rate" in taxreturn.to_csv(report)


def test_a_finding_fixed_before_approval_is_no_risk(tmp_path):
    # Read at 15% (the finding stored when it was read), the reviewer corrected the rate to 14% and approved with
    # nothing left open: the approval's open issues are what counts.
    store = _store(tmp_path)
    d = doc([line(1, 1000.0, taxes=["HST"], cc="CC400")], [tl("HST", "NS", 0.15, 1000.0, 150.0)], prov="NS",
            invoice_date="2025-06-10", invoice_number="NS-2")  # fmt: skip
    out, rep = finalise_coding(InvoiceCoding.model_validate(d), store.reference_data(), Settings(), store=store)
    i = store.add_invoice(tmp_path / "ns2.pdf", out, rep.to_dict())
    fixed = {**out, "tax_lines": [{**out["tax_lines"][0], "rate": 0.14, "tax_amount": 140.0}], "tax_total": 140.0,
             "grand_total": 1140.0}  # fmt: skip
    store.approve_invoice(i, fixed, "Jane")
    report = taxreturn.build(store, dt.date(2025, 4, 1), dt.date(2025, 6, 30))
    assert report.at_risk() == []
    # Approved with the finding still open: flagged
    j, _ = _approve(store, tmp_path, {**d, "invoice_number": "NS-3"},
                    open_issues=[{"code": "TAX_RATE_NONSTANDARD", "severity": "warning", "line": None}])  # fmt: skip
    report = taxreturn.build(store, dt.date(2025, 4, 1), dt.date(2025, 6, 30))
    assert [c.invoice_id for c in report.at_risk()] == [j]


def test_the_self_assessment_tile_leaves_out_what_jd_edwards_accrues(tmp_path):
    from ap_coder.web.pages.sales_tax import self_tiles

    store = _store(tmp_path)
    jde.save(store, jde.JdeSettings(amount_mode=jde.AMOUNT_TAX))  # code B: E1 accrues the PST
    bc = dict(prov="BC", supplier_province="AB", invoice_date="2026-09-10")
    _approve(store, tmp_path, doc([line(1, 1000.0, taxes=["GST"], cc="CC400")], [tl("GST", "", 0.05, 1000.0, 50.0)],
                                  invoice_number="B-1", **bc), fmt="jde")  # fmt: skip
    _approve(
        store,
        tmp_path,
        doc(
            [line(1, 500.0, taxes=["GST"], cc="CC400")], [tl("GST", "", 0.05, 500.0, 25.0)], invoice_number="B-2", **bc
        ),
        fmt=None,
    )  # fmt: skip  (approved, not exported yet)
    items = taxreturn.self_assessment(store, dt.date(2026, 7, 1), dt.date(2026, 9, 30))
    notes = {s.invoice_number: s.note for s in items}
    assert notes["B-1"] == taxreturn.JDE_ACCRUES
    assert notes["B-2"] == taxreturn.JDE_WILL_ACCRUE  # JD Edwards in use, the mode accrues it: said before export
    assert self_tiles(items) == []  # neither is to book from the report
    # The tax coded to G/L accounts: E1 will accrue nothing, the report has the one not exported yet to book
    jde.save(store, jde.JdeSettings(amount_mode=jde.AMOUNT_GL))
    items = taxreturn.self_assessment(store, dt.date(2026, 7, 1), dt.date(2026, 9, 30))
    assert {s.invoice_number: s.note for s in items}["B-2"] == ""  # not exported yet: to book from the report
