"""Sales tax to claim back: ITCs and ITRs by period, credit notes, claims to check."""

import datetime as dt
import json

from ap_coder.ai.pipeline import finalise_coding
from ap_coder.coding import taxreturn
from ap_coder.coding.tax import EXPENSE_TO_LINE
from ap_coder.core.config import Settings
from ap_coder.core.schema import InvoiceCoding
from ap_coder.core.store import Store, load_sample_setup
from ap_coder.ops.demo import load_demo

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
    gst_hst -= 63.75  # half the GST and QST on the Montreal client cocktail (6320, meals) is not claimable (D-90)
    qst -= 127.18
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
    # (its client cocktail is coded to meals: half its tax is not claimed, a reason, not a risk: D-90)
    assert issues[(b, "QST")] == [taxreturn.NO_QST_NUMBER, taxreturn.MEALS, taxreturn.NOT_EXPORTED]
    assert issues[(b, "GST")] == [taxreturn.MEALS, taxreturn.NOT_EXPORTED]  # not exported alone is not a risk
    assert {(c.invoice_id, c.tax_type) for c in report.at_risk()} == {(a, "GST"), (b, "QST")}


def test_a_number_from_the_vendor_list_is_a_claim_at_risk(tmp_path):
    """D-65: a GST/HST or QST number the review screen filled from the vendor list is not printed on the invoice, and
    the claim needs it printed there (from $100): the claim is to check, and says why."""
    from ap_coder.core.schema import FROM_VENDOR_LIST

    store = Store(tmp_path / "t.db")
    load_sample_setup(store)
    doc = json.loads((SAMPLES / "ground_truth" / "montroyal_QC_TPS_TVQ_ACMR-2026-1187.json").read_text())
    output, _ = finalise_coding(InvoiceCoding.model_validate(doc), store.reference_data(), Settings(), store=store)
    output[FROM_VENDOR_LIST] = ["gst_hst_registration_number", "qst_registration_number"]
    invoice_id = store.add_invoice(tmp_path / "m.pdf", output, {})
    store.approve_invoice(invoice_id, output, "Jane")
    store.create_export_batch([invoice_id], "csv")
    issues = {c.tax_type: c.issues for c in taxreturn.build(store, *YEAR).claims}
    assert issues == {"GST": [taxreturn.GST_NOT_PRINTED, taxreturn.MEALS],
                      "QST": [taxreturn.QST_NOT_PRINTED, taxreturn.MEALS]}  # fmt: skip
    assert len(taxreturn.build(store, *YEAR).at_risk()) == 2
    assert InvoiceCoding.model_validate({k: v for k, v in output.items() if k != "gl_distribution"})  # still a coding


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


def test_registration_number_needed_from_100_dollars_since_2021(tmp_path):
    """Since April 21, 2021 (Budget 2021's amendment of the ITC information rules), the supplier's registration number
    is needed on an invoice of $100 or more (it was $30)."""
    store = Store(tmp_path / "t.db")
    load_sample_setup(store)
    doc = json.loads((SAMPLES / "ground_truth" / "chinook_AB_GST_CCO-26-10418.json").read_text())
    doc.update(gst_hst_registration_number="", subtotal=80.0, tax_total=4.0, grand_total=84.0,
               tax_lines=[{"tax_type": "GST", "province": "", "rate": 0.05, "taxable_amount": 80.0, "tax_amount": 4.0}],
               line_items=[{**doc["line_items"][0], "quantity": 1, "unit_price": 80.0, "amount": 80.0}])  # fmt: skip
    for n, date in enumerate(("2026-03-02", "2020-12-31")):
        d = {**doc, "invoice_number": f"S-{n}", "invoice_date": date}
        invoice_id = store.add_invoice(tmp_path / f"s{n}.pdf", d, {})
        store.approve_invoice(invoice_id, d, "Jane")
        store.create_export_batch([invoice_id], "csv")
    issues = {c.invoice_number: c.issues for c in taxreturn.build(store, *YEAR).claims}
    assert issues == {"S-0": [], "S-1": [taxreturn.NO_GST_NUMBER]}  # $84: needs no number since 2021, did before
    assert taxreturn.document_threshold("2021-04-21") == 100.0 and taxreturn.document_threshold("2021-04-20") == 30.0


def test_demo_and_default_period(tmp_path):
    store = Store(tmp_path / "t.db")
    load_demo(store)
    report = taxreturn.build(store, *Q3)
    assert report.totals()[taxreturn.ITC]["CAD"] > 0 and report.not_approved > 0
    assert taxreturn.default_period(dt.date(2026, 10, 7)) == Q3
    assert taxreturn.default_period(dt.date(2026, 1, 15)) == (dt.date(2025, 10, 1), dt.date(2025, 12, 31))


def test_pst_and_qst_to_self_assess(tmp_path):
    store = Store(tmp_path / "t.db")
    load_sample_setup(store)
    doc = json.loads((SAMPLES / "ground_truth" / "prairie_SK_GST_PST_PNS-104882.json").read_text())
    no_pst = {**doc, "invoice_number": "X-1", "tax_lines": [t for t in doc["tax_lines"] if t["tax_type"] != "PST"]}
    no_pst["tax_total"] = round(sum(t["tax_amount"] for t in no_pst["tax_lines"]), 2)
    no_pst["grand_total"] = round(no_pst["subtotal"] + no_pst["tax_total"], 2)
    for n, d in enumerate((doc, no_pst)):
        invoice_id = store.add_invoice(tmp_path / f"{n}.pdf", d, {})
        store.approve_invoice(invoice_id, d, "Jane")
    _approved(store, tmp_path, "northwind_ON_HST_NW-2026-0912")  # Ontario: HST only, nothing to self-assess
    (item,) = taxreturn.self_assessment(store, *YEAR)
    assert (item.invoice_number, item.province, item.tax_type, item.rate) == ("X-1", "SK", "PST", 0.06)
    assert item.estimate == round(doc["subtotal"] * 0.06, 2)
    text = taxreturn.self_assessment_csv([item], *YEAR).decode("utf-8-sig")
    assert "X-1" in text and "6%" in text


def test_pst_printed_as_zero_is_not_charged(tmp_path):
    """A British Columbia invoice printing "PST 0.00" charged no PST: it is on the self-assessment list (as
    the tax checks see it). One from BC's HST years (2010-07 to 2013-03) owes no PST."""
    store = Store(tmp_path / "t.db")
    load_sample_setup(store)
    doc = json.loads((SAMPLES / "ground_truth" / "pacific_BC_GST_PST_PO-77120.json").read_text())
    zero = {**doc, "invoice_number": "Z-1", "tax_lines": [dict(t) for t in doc["tax_lines"]]}
    zero["tax_lines"][1]["tax_amount"] = 0.0
    zero["tax_total"] = zero["tax_lines"][0]["tax_amount"]
    zero["grand_total"] = round(zero["subtotal"] + zero["tax_total"], 2)
    hst = {"tax_type": "HST", "province": "BC", "rate": 0.12, "taxable_amount": 2726.0, "tax_amount": 327.12}
    hst_years = {**doc, "invoice_number": "H-1", "invoice_date": "2012-05-01", "tax_total": 327.12, "tax_lines": [hst]}
    for n, d in enumerate((doc, zero, hst_years)):
        invoice_id = store.add_invoice(tmp_path / f"{n}.pdf", d, {})
        store.approve_invoice(invoice_id, d, "Jane")
    (item,) = taxreturn.self_assessment(store, dt.date(2010, 1, 1), dt.date(2030, 12, 31))
    assert (item.invoice_number, item.province, item.tax_type, item.rate) == ("Z-1", "BC", "PST", 0.07)


def test_gst_is_one_line_whatever_the_province(tmp_path):
    store = Store(tmp_path / "t.db")
    load_sample_setup(store)
    _approved(store, tmp_path, "chinook_AB_GST_CCO-26-10418")
    _approved(store, tmp_path, "montroyal_QC_TPS_TVQ_ACMR-2026-1187")
    gst = [row for row in taxreturn.build(store, *YEAR).by_type() if row[0] == "GST"]
    assert len(gst) == 1 and gst[0][1] == ""  # federal: not split by province


def test_self_assessment_uses_the_place_of_supply_of_the_tax_checks(tmp_path):
    """An invoice stored without a ship-to (approved before our province was set) is taxed where it is delivered
    to us (tax.supply_source), never the vendor's province: a BC vendor delivering to our Alberta site owes no PST,
    a US vendor delivering to our BC site may owe BC PST."""
    store = Store(tmp_path / "t.db")
    load_sample_setup(store)
    store.set_setting("our_province", "BC")
    store.set_setting("site_provinces", "CCAB=AB")
    doc = json.loads((SAMPLES / "ground_truth" / "chinook_AB_GST_CCO-26-10418.json").read_text())
    base = {**doc, "ship_to_province": ""}
    bc_vendor = {**base, "invoice_number": "B-1", "supplier_province": "BC",
                 "line_items": [{**li, "predicted_cost_center": "CCAB"} for li in doc["line_items"]]}  # fmt: skip
    us_vendor = {**base, "invoice_number": "U-1", "supplier_province": "OUTSIDE_CANADA"}
    for n, d in enumerate((bc_vendor, us_vendor)):
        invoice_id = store.add_invoice(tmp_path / f"{n}.pdf", d, {})
        store.approve_invoice(invoice_id, d, "Jane")
    items = taxreturn.self_assessment(store, *YEAR)
    assert [(s.invoice_number, s.province, s.tax_type) for s in items] == [("U-1", "BC", "PST")]
    hst = {"tax_type": "HST", "province": "", "rate": 0.13, "taxable_amount": 100.0, "tax_amount": 13.0}
    assert taxreturn.province("HST", hst, {"supplier_province": "ON"}, taxreturn.supply_setup(store)) == "BC"
