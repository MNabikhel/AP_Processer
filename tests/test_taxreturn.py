"""Sales tax to claim back: ITCs and ITRs by period, credit notes, claims to check."""

import datetime as dt
import json

from ap_coder import taxreturn
from ap_coder.config import Settings
from ap_coder.demo import load_demo
from ap_coder.pipeline import finalise_coding
from ap_coder.schema import InvoiceCoding
from ap_coder.store import Store, load_sample_setup
from ap_coder.tax import EXPENSE_TO_LINE

from .conftest import SAMPLES

Q3 = (dt.date(2026, 7, 1), dt.date(2026, 9, 30))
YEAR = (dt.date(2020, 1, 1), dt.date(2030, 12, 31))


def _approved(store, tmp_path, stem, export=True, **changes):
    doc = {**json.loads((SAMPLES / "ground_truth" / f"{stem}.json").read_text()), **changes}
    output, _ = finalise_coding(InvoiceCoding.model_validate(doc), store.reference_data(), Settings(), store=store)
    invoice_id = store.add_invoice(tmp_path / f"{stem}{len(changes)}.pdf", output, {})
    store.approve_invoice(invoice_id, output, "Jane")
    if export:
        store.create_export_batch([invoice_id], "csv")
    return invoice_id, output


def test_hst_gst_and_qst_with_a_credit_note(tmp_path):
    store = Store(tmp_path / "t.db")
    load_sample_setup(store)
    _, nw = _approved(store, tmp_path, "northwind_ON_HST_NW-2026-0912")
    _, cn = _approved(store, tmp_path, "northwind_ON_HST_CN-2026-0047")
    _, qc = _approved(store, tmp_path, "montroyal_QC_TPS_TVQ_ACMR-2026-1187")
    _, bc = _approved(store, tmp_path, "pacific_BC_GST_PST_PO-77120")
    report = taxreturn.build(store, *YEAR)
    tax = {(c.invoice_number, c.tax_type): c.amount for c in report.claims}
    assert tax[(nw["invoice_number"], "HST")] > 0 and tax[(cn["invoice_number"], "HST")] < 0  # the credit reduces it
    assert (bc["invoice_number"], "PST") not in tax  # PST is not recoverable
    gst_hst = sum(t["tax_amount"] for d in (nw, cn, bc, qc) for t in d["tax_lines"] if t["tax_type"] in ("GST", "HST"))
    qst = sum(t["tax_amount"] for t in qc["tax_lines"] if t["tax_type"] == "QST")
    totals = report.totals()
    assert totals[taxreturn.ITC] == {"CAD": round(gst_hst, 2)}
    assert totals[taxreturn.ITR] == {"CAD": round(qst, 2)} and qst > 0
    assert report.at_risk() == []  # every sample shows valid registration numbers
    assert "ACMR-2026-1187" in taxreturn.to_csv(report).decode("utf-8-sig")


def test_period_status_and_treatment(tmp_path):
    store = Store(tmp_path / "t.db")
    load_sample_setup(store)
    _approved(store, tmp_path, "northwind_ON_HST_NW-2026-0912", invoice_date="2026-10-02")
    doc = json.loads((SAMPLES / "ground_truth" / "chinook_AB_GST_CCO-26-10418.json").read_text())
    store.add_invoice(tmp_path / "r.pdf", doc, {})  # still in review
    report = taxreturn.build(store, *Q3)
    assert report.claims == [] and report.not_approved == 1  # the approved one is dated after the quarter
    assert len(taxreturn.build(store, dt.date(2026, 10, 1), dt.date(2026, 12, 31)).claims) == 1
    store.set_tax_treatment("HST", EXPENSE_TO_LINE)
    report = taxreturn.build(store, dt.date(2026, 10, 1), dt.date(2026, 12, 31))
    assert report.claims == [] and "HST" in report.not_recoverable


def test_claims_to_check(tmp_path):
    store = Store(tmp_path / "t.db")
    load_sample_setup(store)
    a, _ = _approved(store, tmp_path, "chinook_AB_GST_CCO-26-10418", gst_hst_registration_number="")
    b, _ = _approved(store, tmp_path, "montroyal_QC_TPS_TVQ_ACMR-2026-1187", export=False, qst_registration_number="12")
    report = taxreturn.build(store, *YEAR)
    issues = {(c.invoice_id, c.tax_type): c.issues for c in report.claims}
    assert issues[(a, "GST")] == [taxreturn.NO_GST_NUMBER]
    assert issues[(b, "QST")] == [taxreturn.NO_QST_NUMBER, taxreturn.NOT_EXPORTED]
    assert issues[(b, "GST")] == [taxreturn.NOT_EXPORTED]  # not exported alone is not a risk
    assert {(c.invoice_id, c.tax_type) for c in report.at_risk()} == {(a, "GST"), (b, "QST")}


def test_small_invoices_need_no_number(tmp_path):
    store = Store(tmp_path / "t.db")
    load_sample_setup(store)
    doc = json.loads((SAMPLES / "ground_truth" / "chinook_AB_GST_CCO-26-10418.json").read_text())
    doc.update(gst_hst_registration_number="", subtotal=20.0, tax_total=1.0, grand_total=21.0,
               tax_lines=[{"tax_type": "GST", "province": "", "rate": 0.05, "taxable_amount": 20.0, "tax_amount": 1.0}],
               line_items=[{**doc["line_items"][0], "quantity": 1, "unit_price": 20.0, "amount": 20.0}])  # fmt: skip
    invoice_id = store.add_invoice(tmp_path / "s.pdf", doc, {})
    store.approve_invoice(invoice_id, doc, "Jane")
    store.create_export_batch([invoice_id], "csv")
    (claim,) = taxreturn.build(store, *YEAR).claims
    assert claim.amount == 1.0 and claim.issues == []


def test_demo_and_default_period(tmp_path):
    store = Store(tmp_path / "t.db")
    load_demo(store)
    report = taxreturn.build(store, *Q3)
    assert report.totals()[taxreturn.ITC]["CAD"] > 0 and report.not_approved > 0
    assert taxreturn.default_period(dt.date(2026, 10, 7)) == Q3
    assert taxreturn.default_period(dt.date(2026, 1, 15)) == (dt.date(2025, 10, 1), dt.date(2025, 12, 31))
