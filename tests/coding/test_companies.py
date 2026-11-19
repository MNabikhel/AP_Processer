"""D-267: which of JD Edwards' companies an invoice is for (report 9, F0010), the first rule that gives one answer,
never a guess between two; and which companies get the Canadian tax checks (CA and CAD only)."""

import copy
import json

import pytest

from ap_coder.ai.pipeline import finalise_coding
from ap_coder.bench import jde_group
from ap_coder.capture.workflow import our_name_keys
from ap_coder.coding import companies
from ap_coder.core.chart import BuRow, ChartRow
from ap_coder.core.config import Settings
from ap_coder.core.headers import match_columns
from ap_coder.core.schema import InvoiceCoding
from ap_coder.core.store import Store
from ap_coder.erp import import_specs, jde_settings


def _load_group(store, rows=None):
    table, _left = companies.company_table(
        [dict(zip(jde_group.TITLES["build"], r, strict=True)) for r in (rows or jde_group.companies_rows())[1:]],
        match_columns(list(jde_group.TITLES["build"]), companies.COMPANY_ALIASES),
    )
    store.set_setting(companies.COMPANIES_SETTING, json.dumps(table))


@pytest.fixture
def group(tmp_path):
    store = Store(tmp_path / "ap.db")
    _load_group(store)
    return store


def _coding(ground_truth, **changes):
    doc = copy.deepcopy(ground_truth)
    doc.update(changes)
    return InvoiceCoding.model_validate(doc)


def _check(store, reference, coding, text=""):
    from ap_coder.ai.extraction import ExtractionResult

    extraction = ExtractionResult(source="test", model_id="", content=text) if text else None
    output, report = finalise_coding(coding, reference, Settings(), extraction, store=store)
    return output, {i.code: i for i in report.issues}, report


# --- Report 9 ------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("titles", ["build", "reportsnow", "fields"])
def test_report_9_is_read_by_the_build_sheets_titles_reportsnows_and_the_field_names(titles):
    rows = jde_group.companies_rows(titles=titles)
    assert import_specs.recognise(rows[0]) == "companies" == import_specs.workbook_report(rows[0])
    table, left = companies.company_table([dict(zip(rows[0], r, strict=True)) for r in rows[1:]],
                                          match_columns(rows[0], companies.COMPANY_ALIASES))  # fmt: skip
    assert left == 1  # JD Edwards' default company, 00000: no company's books
    assert table["00310"] == {"name": "Maple Ridge Builders Inc.", "currency": "USD", "country": "US"}
    assert len(table) == 6


def test_a_company_and_a_name_alone_are_never_report_9_nor_is_another_report_taken_for_it():
    assert import_specs.recognise(["Co", "Name"]) is None
    assert import_specs.recognise(["Company", "Name", "Phone"]) is None
    assert import_specs.recognise(["Job Number", "Job Name", "Co", "BU Ty", "Job Sts"]) == "units"
    chart = ["Company", "Business Unit", "Object Account", "Subsidiary", "Account Description", "L D", "P E",
             "Account ID", "Currency Code"]  # fmt: skip
    assert import_specs.recognise(chart) == "chart"
    assert import_specs.recognise(["Address Number", "Alpha Name", "Hold Payment Code", "Name", "Co"]) == (
        "supplier_master")  # fmt: skip


def test_only_a_company_of_ca_and_cad_gets_the_canadian_tax_checks_and_settings_win(group):
    said = {code: companies.canadian(group, code) for code, *_ in jde_group.GROUP}
    assert said == {"00210": (True, "CA and CAD"), "00220": (True, "CA and CAD"), "00230": (True, "CA and CAD"),
                    "00310": (False, "currency USD"), "00320": (False, "currency USD"),
                    "00410": (False, "no country")}  # fmt: skip
    assert companies.canadian(group, "00999") == (False, "not in JD Edwards' companies")
    settings = jde_settings.load(group)
    settings.company_tax = {"00320": "on", "00210": "off"}
    jde_settings.save(group, settings)
    assert companies.canadian(group, "320") == (True, "turned on in Settings")
    assert companies.canadian(group, "00210") == (False, "turned off in Settings")


def test_one_company_decides_nothing_and_keeps_the_invoice_as_before(tmp_path, reference, ground_truth):
    store = Store(tmp_path / "ap.db")
    _load_group(store, jde_group.companies_rows(jde_group.GROUP[:1]))
    assert not companies.several(store) and companies.canadian(store, "00310") == (True, "one company")
    output, codes, report = _check(store, reference, _coding(ground_truth))
    assert "company" not in output and "COMPANY_UNKNOWN" not in codes and "company" not in report.checks


# --- Which company -----------------------------------------------------------------------------------------------


def test_nothing_decides_so_the_invoice_is_held_to_pick_the_company(group, reference, ground_truth):
    output, codes, report = _check(group, reference, _coding(ground_truth))
    assert "company" not in output and codes["COMPANY_UNKNOWN"].severity == "error"
    assert codes["COMPANY_UNKNOWN"].message == "Pick the company this invoice is for: nothing on it decides one"
    output, codes, report = _check(group, reference, _coding(ground_truth, company="220"))  # a person's pick
    assert output["company"] == "00220" and "COMPANY_UNKNOWN" not in codes
    assert report.checks["company"]["how"] == "picked"


def test_its_po_with_its_supplier_decides_the_company(group, reference, ground_truth):
    line = {"po_number": "PO-88213", "vendor_name": "Northwind IT Solutions Inc.", "order_type": "OP",
            "order_company": "00310", "line_number": 1, "description": "Dell Latitude 7450 laptop", "quantity": 3,
            "unit_price": 1450.0, "amount": 4350.0, "status": "open"}  # fmt: skip
    group.import_purchase_orders([line, {**line, "order_company": "00220", "vendor_name": "Someone Else Ltd"}])
    output, codes, report = _check(group, reference, _coding(ground_truth))
    assert output["company"] == "00310" and report.checks["company"]["said"] == "the company of PO PO-88213"
    assert "COMPANY_UNKNOWN" not in codes


def test_a_companys_name_on_the_page_decides_but_never_the_suppliers_own(group, reference, ground_truth):
    page = "Northwind IT Solutions Inc.\nBill to: MAPLE RIDGE CONSTRUCTORS LTD, 400 Pine St, Calgary AB\n"
    output, _codes, report = _check(group, reference, _coding(ground_truth), page)
    assert output["company"] == "00220" and report.checks["company"]["how"] == "name"
    # a company of the group billing another: its own name is the supplier's, the other one decides
    biller = _coding(ground_truth, vendor_name="Maple Ridge Builders Inc.")
    output, _codes, _report = _check(group, reference, biller, f"Maple Ridge Builders Inc.\n{page}")
    assert output["company"] == "00220"
    # two of the group named: no one answer
    output, codes, _report = _check(group, reference, _coding(ground_truth), page + "Fraser Bend Civil Ltd.\n")
    assert "company" not in output and "00220 Maple Ridge Constructors Ltd." in codes["COMPANY_UNKNOWN"].message
    # a trade name of Settings → Review, written "name = company"
    group.set_setting("our_names", "Maple Ridge Holdings Ltd.\nCascade Line = 320")
    output, _codes, _report = _check(group, reference, _coding(ground_truth), "Ship to: CASCADE LINE, Yard 4")
    assert output["company"] == "00320"
    assert {"maple ridge holdings", "cascade line", "maple ridge builders", "fraser bend civil"} <= our_name_keys(group)


def test_a_companys_name_inside_the_suppliers_own_never_decides(group, reference, ground_truth):
    """B-2680: a company's short name (Settings → Review, "Fraser Bend = 230") printed only inside the supplier's own
    name ("Fraser Bend Aggregates Ltd.", a supplier of its own) decided the invoice was 00230's, a Canadian company,
    whatever it was billed to: never held to pick the company, and approved in bulk or touchless under it."""
    group.set_setting("our_names", "Fraser Bend = 230")
    supplier = "Fraser Bend Aggregates Ltd."
    coding = _coding(ground_truth, vendor_name=supplier)
    page = f"{supplier.upper()}\n12 Quarry Rd, Abbotsford BC\nBill to: MRB Inc., Spokane WA\n"
    output, codes, _report = _check(group, reference, coding, page)
    assert "company" not in output and "COMPANY_UNKNOWN" in codes
    # the company it is billed to decides, as for any supplier
    page = f"{supplier}\nBill to: MAPLE RIDGE CONSTRUCTORS LTD, 400 Pine St\n"
    output, _codes, report = _check(group, reference, coding, page)
    assert output["company"] == "00220" and report.checks["company"]["how"] == "name"
    # report 9's own name inside a supplier's likewise; printed elsewhere on the page too, it decides
    coding = _coding(ground_truth, vendor_name="Fraser Bend Civil Testing Inc.")
    output, codes, _report = _check(group, reference, coding, "Fraser Bend Civil Testing Inc.\nBill to: MRB Inc.\n")
    assert "company" not in output and "COMPANY_UNKNOWN" in codes
    page = "Fraser Bend Civil Testing Inc.\nBill to: Fraser Bend Civil Ltd., 9 River Rd\n"
    output, _codes, _report = _check(group, reference, coding, page)
    assert output["company"] == "00230"


def test_the_suppliers_history_decides_when_it_only_ever_billed_one_company(group, reference, ground_truth):
    voucher = {"key": "00230-PV-700101-001", "vendor_name": "Northwind IT Solutions Inc.", "invoice_number": "N-1",
               "total": 10.0, "invoice_date": "2026-01-02", "an8": "", "document": "700101"}  # fmt: skip
    group.import_erp_vouchers([voucher, {**voucher, "key": "00230-PV-700102-001", "invoice_number": "N-2"}])
    output, _codes, report = _check(group, reference, _coding(ground_truth))
    assert output["company"] == "00230" and report.checks["company"]["how"] == "history"
    group.import_erp_vouchers([{**voucher, "key": "00210-PV-700103-001", "invoice_number": "N-3"}])
    output, codes, _report = _check(group, reference, _coding(ground_truth))
    assert "company" not in output and "it could be 00210" in codes["COMPANY_UNKNOWN"].message


def test_the_business_units_of_its_lines_decide_last(group, reference, ground_truth):
    group.import_business_units([BuRow("CC400", "Head office IT", "00210"), BuRow("CC500", "Yard", "00310")])
    group.import_chart([ChartRow("CC400", "6010", "", "Computers"), ChartRow("CC500", "6010", "", "Computers")])
    output, _codes, report = _check(group, reference, _coding(ground_truth))
    assert output["company"] == "00210" and report.checks["company"]["how"] == "business unit"
    split = _coding(ground_truth)
    split.line_items[1].predicted_cost_center = "CC500"
    output, codes, _report = _check(group, reference, split)
    assert "company" not in output and "COMPANY_UNKNOWN" in codes


# --- A US company ------------------------------------------------------------------------------------------------


def test_a_us_company_has_every_check_but_the_canadian_tax_ones_and_always_goes_to_a_person(group, reference,
                                                                                            ground_truth):  # fmt: skip
    from ap_coder.capture.supplier import ALWAYS_A_PERSON

    wrong_rate = _coding(ground_truth, gst_hst_registration_number="")
    wrong_rate.tax_lines[0].rate = 0.15
    _output, canadian, _report = _check(group, reference, wrong_rate.model_copy(update={"company": "00220"}))
    assert {"TAX_RATE_NONSTANDARD", "GST_HST_NUMBER_MISSING"} & set(canadian)
    _output, us, _report = _check(group, reference, wrong_rate.model_copy(update={"company": "00320"}))
    assert not {"TAX_RATE_NONSTANDARD", "GST_HST_NUMBER_MISSING", "ITC_AT_RISK"} & set(us)
    assert us["US_TAX_NOT_CHECKED"].severity == "warning"
    assert us["US_TAX_NOT_CHECKED"].message == ("Company 00320 is not checked for Canadian tax (currency USD): its tax "
                                                "is not checked here, confirm it is right")  # fmt: skip
    assert "US_TAX_NOT_CHECKED" in ALWAYS_A_PERSON and "COMPANY_UNKNOWN" in ALWAYS_A_PERSON
