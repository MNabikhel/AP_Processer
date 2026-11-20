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
    picked = _coding(ground_truth, company="220", company_how="picked")  # a person's pick
    output, codes, report = _check(group, reference, picked)
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


# An intercompany invoice: Maple Ridge Constructors Ltd. (00220, a supplier too) bills Maple Ridge Holdings Ltd. (00210)
INTERCOMPANY = (
    "Maple Ridge Constructors Ltd.\n1180 Marine Drive, Kamloops, BC V2C 1A4\nInvoice #: IC-2611\nBill To:\n"
    "Maple Ridge Holdings Ltd.\n410 Elm Street, Edmonton, AB T5J 0N3\nDescription Qty Amount\n"
    "Crane time for Fraser Bend Civil Ltd., job 2403 1 2,000.00\n"
)
SENDER = ("Maple Ridge Constructors Ltd.", "MAPLE RIDGE CONSTRUCTORS LTD")  # (report 9's name, the vendor list's)


def _suppliers_of_the_group(store):
    suppliers = (("MAPLE RIDGE CONSTRUCTORS LTD", "4410900"), ("MAPLE RIDGE HOLDINGS LTD", "4410901"),
                 ("KELSO READY MIX LTD", "4410101"))  # fmt: skip
    store.import_vendor_master([{"vendor_name": n, "erp_id": e} for n, e in suppliers], by_erp_id=True)


def test_a_company_of_the_group_printed_as_the_sender_is_offered_to_pick_never_the_one_billed(group, ground_truth):
    """B-2790 (D-298): report 9's names are never read as the supplier, so an intercompany invoice (Maple Ridge
    Constructors Ltd., 00220, a supplier too, billing Maple Ridge Holdings Ltd.) was left with no supplier and no
    company, nothing said of who sent it. A company of the group printed outside the page's bill-to and ship-to that
    is a supplier in the vendor master is offered for a person to pick: never the company billed, the one decided, the
    supplier chosen, or a company that is no supplier."""
    _suppliers_of_the_group(group)
    unread = _coding(ground_truth, vendor_name="")
    assert companies.group_suppliers(group, INTERCOMPANY, unread) == [SENDER]
    for coding in (_coding(ground_truth, vendor_name="", company="00220"),  # the company it is for
                   _coding(ground_truth, vendor_name="MAPLE RIDGE CONSTRUCTORS LTD")):  # fmt: skip
        assert companies.group_suppliers(group, INTERCOMPANY, coding) == []
    # an outside supplier's invoice to a company of the group (a supplier too) and shipped to another
    page = (
        "Kelso Ready Mix Ltd.\n88 Industrial Way, Calgary, AB T2E 7H1\nBill to: Maple Ridge Constructors Ltd.\n"
        "1180 Marine Drive, Kamloops, BC V2C 1A4\nShip To:\nMaple Ridge Holdings Ltd., Kelso Bridge yard\n"
    )
    assert companies.group_suppliers(group, page, unread) == []
    group.set_setting(companies.COMPANIES_SETTING, '{"00220": {"name": "Maple Ridge Constructors Ltd."}}')
    assert companies.group_suppliers(group, "Maple Ridge Constructors Ltd.\nBill to: Someone\n", unread) == []


def test_a_company_billed_under_a_label_written_otherwise_or_a_supplier_in_the_box_is_never_offered(
    group, ground_truth
):
    """B-2792: an outside supplier's invoice billed to one company of the group and shipped to another, its blocks
    labelled "Bill-To:" and "Ship-To:" (or "BILL-TO", "SOLD-TO:", "Invoiced To:", "Customer Name:", "Billing
    Address:"), offered both companies as its supplier, the outside supplier read in the vendor box all the same: the
    labels took their words apart by spaces only, and a supplier in the box never stopped the offer. Offered only with
    no supplier in the box, or one of the company's own names (the company billed, typed or picked by mistake)."""
    _suppliers_of_the_group(group)
    unread = _coding(ground_truth, vendor_name="")
    for bill, ship in (("Bill-To:", "Ship-To:"), ("BILL-TO", "SHIP-TO"), ("SOLD-TO:", "Delivery Address:"),
                       ("Invoiced To:", "Shipping Address:"), ("Customer Name:", "Ship - To"),
                       ("Billing Address:", "Deliver-To:")):  # fmt: skip
        page = (
            f"Kelso Ready Mix Ltd.\n88 Industrial Way, Calgary, AB T2E 7H1\n{bill}\nMaple Ridge Holdings Ltd.\n"
            f"410 Elm Street, Edmonton, AB T5J 0N3\n{ship}\nMaple Ridge Constructors Ltd., Kelso Bridge yard\n"
        )
        assert companies.group_suppliers(group, page, unread) == [], bill
    kelso = _coding(ground_truth, vendor_name="Kelso Ready Mix Ltd.")
    assert companies.group_suppliers(group, INTERCOMPANY, kelso) == []
    billed = _coding(ground_truth, vendor_name="Maple Ridge Holdings Ltd.")  # the customer, in the box by mistake
    assert companies.group_suppliers(group, INTERCOMPANY, billed) == [SENDER]


def test_the_sender_is_offered_whatever_its_remit_to_says_to_the_attention_of(group, ground_truth):
    """B-2793: an intercompany invoice whose remit-to reads "Please remit to: / Attn: Accounts Receivable / Maple Ridge
    Constructors Ltd." offered nothing: the "Attn:" line opened a customer block inside the supplier's remit-to, and
    the sender's name in it was taken for the customer's. An "Attn:" line opens one only outside a remit-to."""
    _suppliers_of_the_group(group)
    unread = _coding(ground_truth, vendor_name="")
    remit = "Please remit to:\nAttn: Accounts Receivable\nMaple Ridge Constructors Ltd.\nPO Box 400, Kamloops, BC\n"
    assert companies.group_suppliers(group, INTERCOMPANY + remit, unread) == [SENDER]
    # the customer's own "Attn:" line, its name under it and no bill-to label: still the customer's
    page = INTERCOMPANY.replace("Bill To:\n", "ATTN: Accounts Payable\n")
    assert "Bill To" not in page and companies.group_suppliers(group, page, unread) == [SENDER]


def test_a_company_of_the_group_picked_with_its_supplier_number_decides_the_company_billed(group, reference,
                                                                                          ground_truth):  # fmt: skip
    """B-2794: a company of the group with two supplier numbers under its name is picked (or named by its GST/HST
    number) as "MAPLE RIDGE CONSTRUCTORS LTD #4410900": its own name was compared with the number on it, so rule (b)
    did not leave it out, and an intercompany invoice naming it and the company billed was held to pick the company.
    The supplier's name is compared without its number, as without one."""
    _suppliers_of_the_group(group)
    group.import_vendor_master([{"vendor_name": "MAPLE RIDGE CONSTRUCTORS LTD", "erp_id": "4410950"}], by_erp_id=True)
    page = INTERCOMPANY.split("Description")[0]  # the letterhead and the bill-to
    for picked in ("MAPLE RIDGE CONSTRUCTORS LTD #4410900", "MAPLE RIDGE CONSTRUCTORS LTD #4410950"):
        output, codes, report = _check(group, reference, _coding(ground_truth, vendor_name=picked), page)
        assert output["company"] == "00210" and report.checks["company"]["how"] == "name", picked
        assert "COMPANY_UNKNOWN" not in codes


def test_a_short_name_decides_only_printed_as_a_word_of_its_own_in_capitals(group, reference, ground_truth):
    """B-2690: a name of three letters or fewer in Settings → Review ("Art = 230", "MRB = 310") was read in any word
    of the page, whatever its case: "Art supplies" or "art" in a description decided the invoice was 00230's. Such a
    name now decides only printed as a word of its own in capitals ("MRB"), never inside another or run into one."""
    group.set_setting("our_names", "Art = 230\nMRB = 310")
    for page in ("Northwind IT Solutions Inc.\nArt supplies for the lobby\n", "Ship to: the art room, 2nd floor\n",
                 "Ship to: Mrb yard\n", "Bill to: MRBInc, Spokane WA\n", "Cartage 4 hrs\n"):  # fmt: skip
        output, codes, _report = _check(group, reference, _coding(ground_truth), page)
        assert "company" not in output and "COMPANY_UNKNOWN" in codes, page
    output, _codes, report = _check(group, reference, _coding(ground_truth), "Bill to: MRB Inc., Spokane WA\n")
    assert output["company"] == "00310" and report.checks["company"]["how"] == "name"
    output, _codes, _report = _check(group, reference, _coding(ground_truth), "ATTN: ART DEPT.\n")
    assert output["company"] == "00230"


def test_a_companys_name_run_together_on_a_scan_decides_as_printed_with_its_spaces(group, reference, ground_truth):
    """B-2691: a scan's text runs words together ("MapleRidge ConstructorsLtd."), and the names on the page were
    compared word by word: none decided, so an invoice of a supplier billing two companies was held to pick one, and
    a bill already in JD Edwards under the other company was said as the duplicate error, not as another company's.
    The names are now compared without their spaces and punctuation too, a word's start and end kept (a capital after
    a small letter starts one); a name only inside the supplier's own still never decides."""
    voucher = {"key": "00210-PV-700101-001", "vendor_name": "Northwind IT Solutions Inc.",
               "invoice_number": ground_truth["invoice_number"], "total": ground_truth["grand_total"],
               "invoice_date": "2026-01-02", "an8": "", "document": "700101"}  # fmt: skip
    group.import_erp_vouchers([voucher, {**voucher, "key": "00220-PV-700102-001", "invoice_number": "N-2"}])
    page = "NORTHWINDIT SOLUTIONSINC.\nBillTo: MapleRidge ConstructorsLtd.\n400PineSt, Calgary AB\n"
    output, codes, report = _check(group, reference, _coding(ground_truth), page)
    assert output["company"] == "00220" and report.checks["company"]["how"] == "name"
    assert "DUPLICATE_OTHER_COMPANY" in codes and "DUPLICATE_IN_ERP" not in codes
    for page in ("Bill to: MAPLERIDGE CONSTRUCTORS LTD.\n", "Billto:MAPLERIDGECONSTRUCTORS\n"):
        assert _check(group, reference, _coding(ground_truth), page)[0]["company"] == "00220", page
    # a word that only begins with a name is not the name; nor is a name inside the supplier's own, run together
    group.set_setting("our_names", "Cascade = 320\nFraser Bend = 230")
    output, codes, _report = _check(group, reference, _coding(ground_truth), "Yard: 1 Cascadeview Ave\n")
    assert output.get("company") != "00320"
    coding = _coding(ground_truth, vendor_name="Fraser Bend Aggregates Ltd.")
    output, codes, _report = _check(group, reference, coding, "FraserBend AggregatesLtd.\nBill to: Mrb Inc.\n")
    assert output.get("company") != "00230"


def test_report_9_loaded_after_an_invoice_was_read_its_checks_again_read_the_names_on_its_page(tmp_path):
    """B-2681: every check made again (the review screen, the queue's, bulk approval, touchless processing's, the
    second approval's) decided the company without the page: its names were read only as it was first read. JD
    Edwards' companies loaded after (the invoices already in the queue), an invoice billed to Maple Ridge Builders
    Inc. (00310, US) by a supplier that had billed only 00220 was given 00220, a Canadian company, by that history:
    no warning that its tax is not checked, Ready on the queue and approved in bulk."""
    from ap_coder.coding import bulk
    from ap_coder.core.store import REVIEW, load_sample_setup
    from tests.review_page import ReviewPage
    from tests.webclient import SAMPLE_STEM, SAMPLES, client_for, gt, web_settings

    store = Store(tmp_path / "private" / "ap_coder.db")
    load_sample_setup(store)
    supplier = "Kelso Formwork Ltd."
    store.import_vendor_master([{"vendor_name": supplier, "erp_id": "4410"}])
    page = f"{supplier}\nInvoice KF-118\nBill to: Maple Ridge Builders Inc., 18 Main St, Spokane WA\n"
    iid = store.add_invoice(SAMPLES / f"{SAMPLE_STEM}.pdf", gt(vendor_name=supplier, invoice_number="KF-118"),
                            {"requires_review": False, "adjusted_confidence": 0.97}, extraction_md=page)  # fmt: skip
    _load_group(store)
    voucher = {"total": 10.0, "invoice_date": "2026-01-02", "an8": "", "document": "700101"}
    store.import_erp_vouchers([{**voucher, "key": "00220-PV-700101-001", "vendor_name": supplier,
                                "invoice_number": "KF-90"}])  # fmt: skip
    assert "US_TAX_NOT_CHECKED" in {i["code"] for i in bulk.issues_today(store, [iid])[iid]}
    result = bulk.bulk_approve(store, store.reference_data(), web_settings(), [iid], "Sam")
    assert result["approved"] == [] and store.get_invoice(iid)["status"] == REVIEW
    assert "not checked for Canadian tax" in result["skipped"][0][1]
    screen = ReviewPage(client_for(store), iid)
    assert screen.form["company"] == ["00310"] and "Maple Ridge Builders Inc. is named on the invoice" in screen.text
    stamp = store.checks_stamp()  # a name of Settings → Review decides a company too: the queue checks again
    store.set_setting("our_names", "Kelso Yard = 230")
    assert store.checks_stamp() != stamp


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


def test_how_the_company_was_decided_is_kept_and_ap_coders_own_is_decided_again_as_the_facts_change(
        group, reference, ground_truth):  # fmt: skip
    """B-2692: a company decided once stayed the coding's whatever the rules said later (D-285's "left as it is"):
    decided by a name on the page, then its PO loaded naming another company, it kept the first, said as "picked" by
    a person nobody was. The coding now keeps how its company was decided: a person's pick is kept, AP Coder's own
    decided again at each check; one kept before (no source) is AP Coder's."""

    def again(output, **changes):
        return InvoiceCoding.model_validate({**{k: v for k, v in output.items() if k != "gl_distribution"},
                                             **changes})  # fmt: skip

    page = "Bill to: MAPLE RIDGE CONSTRUCTORS LTD, 400 Pine St\n"
    first, _codes, _report = _check(group, reference, _coding(ground_truth, vendor_name="Lakeside Steel Ltd"), page)
    assert (first["company"], first["company_how"]) == ("00220", "name")
    line = {"po_number": "PO-88213", "vendor_name": "Lakeside Steel Ltd", "order_type": "OP", "order_company": "00310",
            "line_number": 1, "description": "Rebar", "quantity": 3, "unit_price": 1450.0, "amount": 4350.0,
            "status": "open"}  # fmt: skip
    group.import_purchase_orders([line])
    output, _codes, report = _check(group, reference, again(first), page)
    assert (output["company"], output["company_how"]) == ("00310", "po")
    assert report.checks["company"]["said"] == "the company of PO PO-88213"
    # a person's pick is kept, whatever the rules say
    output, codes, report = _check(group, reference, again(first, company="00230", company_how="picked"), page)
    assert (output["company"], output["company_how"], report.checks["company"]["how"]) == ("00230", "picked", "picked")
    assert "PO_COMPANY_DIFFERS" in codes
    # a company kept before its source was: AP Coder's, decided again
    kept = {k: v for k, v in first.items() if k != "company_how"}
    assert _check(group, reference, again(kept, company="00230"), page)[0]["company_how"] == "po"
    # nothing decides it any more: held for a person again, not kept on a rule that no longer gives it
    output, codes, _report = _check(group, reference, again(first, po_number=""), "")
    assert "company" not in output and "company_how" not in output and "COMPANY_UNKNOWN" in codes


def test_a_company_not_in_report_9_is_an_error_approved_only_anyway_unless_the_pilot_names_it(
        group, reference, ground_truth):  # fmt: skip
    """B-2693: a company the coding names that report 9 does not list (a PO's company, a business unit's, one kept from
    an earlier load) was only the warning that its tax is not checked: approved by a person who saw it, its voucher
    went to JD Edwards under a company JD Edwards may not have. It is an error that only Approve anyway with a reason
    passes, unless the pilot names that company (D-272)."""
    import dataclasses

    from ap_coder.coding.validation import Issue
    from ap_coder.web.invoice_findings import is_hard

    picked = _coding(ground_truth, company="00999", company_how="picked")
    output, codes, _report = _check(group, reference, picked)
    assert output["company"] == "00999" and codes["COMPANY_NOT_IN_REPORT"].severity == "error"
    assert codes["COMPANY_NOT_IN_REPORT"].message == (
        "Company 00999 isn't in the companies report (JD Edwards files, card 9): pick the company this invoice is "
        "for, or approve anyway saying why")  # fmt: skip
    assert not is_hard(Issue("error", "COMPANY_NOT_IN_REPORT", ""))
    line = {"po_number": "PO-88213", "vendor_name": "Northwind IT Solutions Inc.", "order_type": "OP",
            "order_company": "00999", "line_number": 1, "description": "Laptop", "quantity": 3, "unit_price": 1450.0,
            "amount": 4350.0, "status": "open"}  # fmt: skip
    group.import_purchase_orders([line])
    output, codes, _report = _check(group, reference, _coding(ground_truth))
    assert output["company_how"] == "po" and "COMPANY_NOT_IN_REPORT" in codes
    # named in the pilot: allowed (D-272); a pilot without it holds it as it always did, said once
    chart = group.chart_settings()
    group.save_chart_settings(dataclasses.replace(chart, pilot=("00210", "00999")))
    _output, codes, _report = _check(group, reference, _coding(ground_truth))
    assert "COMPANY_NOT_IN_REPORT" not in codes and "COMPANY_NOT_IN_PILOT" not in codes
    group.save_chart_settings(dataclasses.replace(chart, pilot=("00210",)))
    _output, codes, _report = _check(group, reference, _coding(ground_truth))
    assert "COMPANY_NOT_IN_PILOT" in codes and "COMPANY_NOT_IN_REPORT" not in codes


# --- A US company ------------------------------------------------------------------------------------------------


def test_a_us_company_has_every_check_but_the_canadian_tax_ones_and_always_goes_to_a_person(group, reference,
                                                                                            ground_truth):  # fmt: skip
    from ap_coder.capture.supplier import ALWAYS_A_PERSON

    wrong_rate = _coding(ground_truth, gst_hst_registration_number="")
    wrong_rate.tax_lines[0].rate = 0.15
    picked = {"company_how": "picked"}
    _output, canadian, _report = _check(group, reference, wrong_rate.model_copy(update={"company": "00220", **picked}))
    assert {"TAX_RATE_NONSTANDARD", "GST_HST_NUMBER_MISSING"} & set(canadian)
    _output, us, _report = _check(group, reference, wrong_rate.model_copy(update={"company": "00320", **picked}))
    assert not {"TAX_RATE_NONSTANDARD", "GST_HST_NUMBER_MISSING", "ITC_AT_RISK"} & set(us)
    assert us["US_TAX_NOT_CHECKED"].severity == "warning"
    assert us["US_TAX_NOT_CHECKED"].message == ("Company 00320 is not checked for Canadian tax (currency USD): its tax "
                                                "is not checked here, confirm it is right")  # fmt: skip
    assert "US_TAX_NOT_CHECKED" in ALWAYS_A_PERSON and "COMPANY_UNKNOWN" in ALWAYS_A_PERSON


def test_report_9s_country_and_currency_are_read_however_they_are_written(tmp_path):
    """B-2689: report 9's country was taken as its first three letters: "CAN" and "Canada" were no "CA", so a
    Canadian company in CAD lost its Canadian tax checks (and "United States" read as "UNI"); its currency likewise
    with a space inside. Now CA, CAN, Canada (any case, spaces) are CA; US, USA, United States, U.S. are US."""
    store = Store(tmp_path / "ap.db")
    rows = [list(jde_group.TITLES["build"]), ["00210", "Maple Ridge Holdings Ltd.", " cad ", "CAN"],
            ["00220", "Maple Ridge Constructors Ltd.", "C AD", "canada "], ["00230", "Fraser Bend Civil Ltd.", "CAD",
            " ca "], ["00310", "Maple Ridge Builders Inc.", "usd", "USA"], ["00320", "Cascade Line Contractors Inc.",
            "USD", "United States"], ["00330", "Cascade Line West Inc.", "USD", "U.S."],
            ["00410", "Maple Ridge Caribbean Ltd.", "USD", "Barbados"]]  # fmt: skip
    _load_group(store, rows)
    table = companies.companies(store)
    assert {code: (c.currency, c.country) for code, c in table.items()} == {
        "00210": ("CAD", "CA"), "00220": ("CAD", "CA"), "00230": ("CAD", "CA"), "00310": ("USD", "US"),
        "00320": ("USD", "US"), "00330": ("USD", "US"), "00410": ("USD", "BAR")}  # fmt: skip
    assert [companies.canadian(store, code)[0] for code in table] == [True, True, True, False, False, False, False]
    # report 9 as an older load kept it: read the same way
    store.set_setting(companies.COMPANIES_SETTING, json.dumps({"00210": {"name": "A", "currency": "cad ",
                                                                         "country": "CAN"},
                                                               "00310": {"name": "B", "currency": "USD",
                                                                         "country": "us"}}))  # fmt: skip
    assert companies.canadian(store, "00210") == (True, "CA and CAD")
    assert companies.companies(store)["00310"].country == "US"


def test_a_us_companys_invoice_is_given_no_canadian_place_of_supply(group, reference, ground_truth):
    """B-2871: a US company's invoice with no ship-to printed was kept with Settings → Review's province as where it
    is delivered (``PLACE_OF_SUPPLY``): its stored header and the reports said Alberta for it."""
    import dataclasses

    from ap_coder.core.schema import PLACE_OF_SUPPLY

    reference = dataclasses.replace(reference, tax=dataclasses.replace(reference.tax, our_province="AB"))
    picked = {"ship_to_province": "", "company_how": "picked"}
    us, _issues, _report = _check(group, reference, _coding(ground_truth, company="00310", **picked))
    canadian, _issues, _report = _check(group, reference, _coding(ground_truth, company="00210", **picked))
    assert PLACE_OF_SUPPLY not in us and canadian[PLACE_OF_SUPPLY] == "AB"


def test_a_us_companys_tax_keyed_as_settings_say_is_said_in_place_of_not_checked(group, reference):
    """D-312: with Settings → JD Edwards E1's US sales and use tax keying a US company's tax, its invoice no longer
    says its tax is not checked (held for a person): the sales tax charged is said keyed (code S, its supplier's tax
    area), and one charging none that use tax may be due (code U), its amount worked out only at a rate Settings give
    the tax area. No tax area from the PO line or the supplier holds the voucher, said."""
    from ap_coder.core.schema import COMPANY_TAX
    from tests.webclient import gt

    jde_settings.show(group)
    jde_settings.save(group, jde_settings.JdeSettings(amount_mode=jde_settings.AMOUNT_TAX, us_tax="keyed"))
    us = {"company": "00310", "company_how": "picked"}
    taxed = InvoiceCoding.model_validate(gt("cascade_US_SalesTax_INV-30981", **us))
    lines = [{**li, "taxes_applied": []} for li in gt("cascade_US_SalesTax_INV-30981")["line_items"]]
    untaxed = InvoiceCoding.model_validate(gt("cascade_US_SalesTax_INV-30981", tax_lines=[], tax_total=0.0,
                                              grand_total=6860.0, line_items=lines, **us))  # fmt: skip
    _output, issues, _report = _check(group, reference, taxed)
    assert "US_TAX_NOT_CHECKED" not in issues and issues["JDE_TAX_AREA"].severity == "error"  # (no area: held)
    group.import_vendor_info([{"vendor_name": "Cascade Cloudworks, Inc.", "erp_id": "4420299"}],
                             [{"erp_id": "4420299", "tax_area": "WA-SPOKANE"}])  # fmt: skip
    output, issues, _report = _check(group, reference, taxed)
    assert output[COMPANY_TAX] == {"tax_area": "WA-SPOKANE", "from": "the supplier's default"}
    assert "US_TAX_NOT_CHECKED" not in issues and "JDE_TAX_AREA" not in issues
    assert (issues["US_SALES_TAX_KEYED"].severity, issues["US_SALES_TAX_KEYED"].message) == ("info", (
        "The sales tax charged (710.01) is keyed as the voucher's tax: code S, tax area WA-SPOKANE (the supplier's "
        "default); its rate is not checked here"))  # fmt: skip
    _output, issues, _report = _check(group, reference, untaxed)
    said = ("No sales tax charged: use tax may be due, at the rate of where it is delivered (the ship-to's). The "
            "voucher is keyed with code U on tax area WA-SPOKANE (the supplier's default), for JD Edwards to work it "
            "out and accrue it (no rate for tax area WA-SPOKANE in Settings → JD Edwards E1: no amount is worked out "
            "here)")  # fmt: skip
    assert (issues["US_USE_TAX"].severity, issues["US_USE_TAX"].message) == ("warning", said)
    jde_settings.save(group, jde_settings.JdeSettings(amount_mode=jde_settings.AMOUNT_TAX, us_tax="keyed",
                                                      use_tax_rates={"WA-SPOKANE": 9.1}))  # fmt: skip
    _output, issues, _report = _check(group, reference, untaxed)
    assert issues["US_USE_TAX"].message.endswith(": 624.26 at 9.1%, its rate in Settings → JD Edwards E1")
    jde_settings.save(group, jde_settings.JdeSettings(amount_mode=jde_settings.AMOUNT_TAX))  # (held: as before)
    _output, issues, _report = _check(group, reference, untaxed)
    assert "US_TAX_NOT_CHECKED" in issues and not {"US_USE_TAX", "US_SALES_TAX_KEYED"} & set(issues)
