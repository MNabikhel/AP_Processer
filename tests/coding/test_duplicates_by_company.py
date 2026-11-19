"""D-267: the check against invoices already entered in JD Edwards is per company. The same supplier and invoice
number entered under the invoice's company is the duplicate error it always was; entered only under another company,
a warning that it may be keyed to the wrong company. A voucher listed by two overlapping loads counts once."""

from ap_coder.coding.vendors import vendor_findings
from ap_coder.core.schema import InvoiceCoding
from ap_coder.core.store import Store


def _voucher(key, number="LS-881", total=113.0, name="Lakeside Steel Ltd"):
    company, kind, document = key.split("-")
    return {"key": key, "vendor_name": name, "invoice_number": number, "total": total, "invoice_date": "2026-10-01",
            "an8": "4410", "document": document, "batch": "9001", "currency": "CAD"}  # fmt: skip


def _found(store, ground_truth, company="", number="LS-881", total=113.0):
    coding = InvoiceCoding.model_validate(
        {
            **ground_truth,
            "vendor_name": "Lakeside Steel Ltd",
            "invoice_number": number,
            "grand_total": total,
            "company": company,
        }
    )
    return [(severity, code, said) for severity, code, said in vendor_findings(coding, store)
            if code.startswith("DUPLICATE_")]  # fmt: skip


def test_an_invoice_entered_under_another_company_is_a_warning_and_under_its_own_the_error(tmp_path, ground_truth):
    store = Store(tmp_path / "ap.db")
    store.import_erp_vouchers([_voucher("00200-PV-700101")])
    store.import_erp_vouchers([_voucher("00200-PV-700101")])  # an overlapping load: the same voucher, once
    [(severity, code, said)] = _found(store, ground_truth, company="00100")
    assert (severity, code) == ("warning", "DUPLICATE_OTHER_COMPANY")
    assert said == ("invoice LS-881 from this vendor is already in the ERP under company 00200 (dated 2026-10-01), "
                    "total 113.00, not 00100: check it isn't keyed to the wrong company")  # fmt: skip
    assert [code for _, code, _ in _found(store, ground_truth, company="200")] == ["DUPLICATE_IN_ERP"]
    # no company decided (one company, or not known yet): the error, as before
    assert [code for _, code, _ in _found(store, ground_truth)] == ["DUPLICATE_IN_ERP"]
    # entered under both: under its own too, so the error
    store.import_erp_vouchers([_voucher("00100-PV-700150")])
    assert [code for _, code, _ in _found(store, ground_truth, company="00100")] == ["DUPLICATE_IN_ERP"]
    assert store.in_erp("Lakeside Steel Ltd", "LS-881", 113.0)[0]["companies"] == ["00100", "00200"]


def test_a_register_file_names_no_company_so_its_bill_is_the_error_for_every_company(tmp_path, ground_truth):
    store = Store(tmp_path / "ap.db")
    store.import_erp_register([{"vendor_name": "Lakeside Steel Ltd", "invoice_number": "LS-881", "total": 113.0}])
    assert [code for _, code, _ in _found(store, ground_truth, company="00100")] == ["DUPLICATE_IN_ERP"]


def test_another_companys_duplicate_is_never_approved_without_a_person():
    from ap_coder.capture.supplier import ALWAYS_A_PERSON
    from ap_coder.ops.help import CHECKS

    assert ALWAYS_A_PERSON["DUPLICATE_OTHER_COMPANY"] == "it may be a duplicate"
    assert CHECKS["DUPLICATE_OTHER_COMPANY"].title == "Already entered under another company"


def test_a_duplicate_among_ap_coders_own_invoices_is_per_company_too(tmp_path, ground_truth, reference):
    """D-270: two invoices of AP Coder's with the same vendor and invoice number are the duplicate error when they are
    for the same company (or either has none decided), as DUPLICATE_IN_ERP is; for two companies, a warning that one
    may be keyed to the wrong company, never approved without a person."""
    from ap_coder.ai.pipeline import finalise_coding
    from ap_coder.capture.supplier import ALWAYS_A_PERSON
    from ap_coder.coding.validation import needs_review
    from ap_coder.core.config import Settings
    from ap_coder.ops.help import CHECKS

    store = Store(tmp_path / "ap.db")
    first = InvoiceCoding.model_validate({**ground_truth, "company": "00200"})
    first_id = store.add_invoice(tmp_path / "first.pdf", first.to_output(), None)

    def found(company):
        coding = InvoiceCoding.model_validate({**ground_truth, "company": company})
        _, report = finalise_coding(coding, reference, Settings(), store=store)
        return report, [(i.severity, i.code, i.message) for i in report.issues if i.code.startswith("DUPLICATE_")]

    report, [(severity, code, said)] = found("00100")
    assert (severity, code) == ("warning", "DUPLICATE_INVOICE_OTHER_COMPANY")
    assert said == (f"same vendor and invoice number as invoice #{first_id} (received first, waiting for review), "
                    "which is for company 00200, not 00100: another company's bill with the same number, or one of "
                    "the two keyed to the wrong company. Check the company of each")  # fmt: skip
    assert needs_review(1.0, [(i.severity, i.code) for i in report.issues], 0.5)
    assert ALWAYS_A_PERSON["DUPLICATE_INVOICE_OTHER_COMPANY"] == "it may be a duplicate"
    assert CHECKS["DUPLICATE_INVOICE_OTHER_COMPANY"].title == "Same invoice number under another company"
    assert [code for _, code, _ in found("00200")[1]] == ["DUPLICATE_INVOICE"]
    assert [code for _, code, _ in found("")[1]] == ["DUPLICATE_INVOICE"]  # (none decided: as before)


def test_rejecting_one_of_two_duplicates_checks_the_third_again_per_company(tmp_path, ground_truth, reference):
    """D-270: an invoice whose duplicates are one of its company and one of another, once the first is rejected,
    keeps the other company's warning alone."""
    from ap_coder.ai.pipeline import finalise_coding
    from ap_coder.core.config import Settings

    store = Store(tmp_path / "ap.db")
    ids = []
    for company in ("00200", "00100", "00100"):
        coding = InvoiceCoding.model_validate({**ground_truth, "company": company})
        output, report = finalise_coding(coding, reference, Settings(), store=store)
        ids.append(store.add_invoice(tmp_path / f"{len(ids)}.pdf", output, report.to_dict()))
    codes = [i["code"] for i in store.get_invoice(ids[2])["validation"]["issues"] if i["code"].startswith("DUP")]
    assert codes == ["DUPLICATE_INVOICE", "DUPLICATE_INVOICE_OTHER_COMPANY"]
    store.reject_invoice(ids[1], "Sam Reviewer", "Sent twice")
    issues = [i for i in store.get_invoice(ids[2])["validation"]["issues"] if i["code"].startswith("DUP")]
    assert [i["code"] for i in issues] == ["DUPLICATE_INVOICE_OTHER_COMPANY"]
    assert (
        f"#{ids[0]} (received first" in issues[0]["message"] and "for company 00200, not 00100" in issues[0]["message"]
    )
