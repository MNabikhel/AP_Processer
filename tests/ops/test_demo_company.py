"""The made-up company's demo (``ops.demo.load_company``, ``demo --company``): a new database made a company's
that codes each line to a JD Edwards account string, as the ``chart_company`` fixture of the tests is made."""

import pytest

from ap_coder.bench import jde_company, jde_exports, jde_invoices
from ap_coder.coding.tax import OUTSIDE_CANADA
from ap_coder.core import chart
from ap_coder.core.store import REVIEW, Store
from ap_coder.core.tax_setup import EXPENSE_TO_LINE, RECOVERABLE
from ap_coder.erp import jde_prototype, jde_settings
from ap_coder.ops import cli, demo
from ap_coder.web.core import simple_screens


def test_the_company_has_its_lists_loaded_by_the_importers(chart_company):
    store, truth = chart_company.store, chart_company.truth
    pairs = store.account_pairs()
    postable = [a for a in truth.accounts if a.postable]
    assert pairs.tally()[chart.OK] + pairs.tally()[chart.RESERVED] + pairs.tally()[chart.BU_INACTIVE] == len(postable)
    assert len(store.business_units()) == len(truth.business_units)
    assert {v["erp_id"] for v in store.master_vendors()} == {str(s.number) for s in truth.suppliers}
    (journal,) = store.journal_files()
    assert journal["vouchers"] == len(truth.vouchers)
    assert store.journal_objects() == {"trade": ("2110",), "intercompany": ("2150",), "tax": ("2420",)}
    assert store.erp_register_count() == len(truth.vouchers)
    files = {path.name for path in (chart_company.folder / "jde_exports").iterdir()}  # what a person would load
    assert {jde_exports.CHART_FILE, jde_exports.UNITS_FILE, jde_exports.SUPPLIERS_FILE, jde_exports.JOURNAL_FILE,
            jde_exports.HEADERS_FILE} <= files  # fmt: skip
    assert sorted(p.stem for p in (chart_company.folder / "invoices").glob("*.pdf")) == sorted(jde_invoices.NAMED)


def test_the_company_is_set_up_the_way_a_department_that_types_its_vouchers_keys_them(chart_company):
    store, company = chart_company.store, chart_company.truth.company
    settings = store.chart_settings()
    assert settings.never_offered == ("*.9710", "*.9720") and settings.home_business_unit == company.main_business_unit
    assert store.account_pairs().tally()[chart.RESERVED] == sum(1 for a in chart_company.truth.accounts if a.reserved)
    tax = {tax_type: (t.treatment, t.gl_code) for tax_type, t in store.tax_treatments().items()}
    assert tax["GST"] == tax["HST"] == (RECOVERABLE, company.gst_object)
    assert tax["PST"] == tax["QST"] == (EXPENSE_TO_LINE, "")
    js = jde_settings.load(store)
    # The department's way (``jde_prototype.prototype_settings``: the one definition) over the company's own two values;
    # the company's GST-only tax area is the one the department's placeholder is named after.
    own = jde_settings.JdeSettings(company=company.number, terms_codes={"Net 30": company.payment_terms})
    assert js == jde_prototype.prototype_settings(own) and jde_settings.in_use(store)
    assert (js.company, js.amount_mode, js.remarks) == ("00210", jde_settings.AMOUNT_TAX, jde_settings.REMARKS_TYPED)
    assert js.terms_codes == {"Net 30": "N30"}
    assert company.tax_area == jde_prototype.PROTOTYPE_GST_AREA == "CDNGST"
    assert {(area.tax_area, area.code) for p, area in js.tax_map.items() if p in ("AB", "BC", "QC", "SK", "MB")} == {
        ("CDNGST", "V")
    }
    assert (js.tax_map["ON"].tax_area, js.tax_map["ON"].code) == ("ONHST", "V")
    assert js.tax_map[OUTSIDE_CANADA] == jde_settings.TaxArea("", "")


def test_the_seven_invoices_are_in_the_review_queue_read_from_their_pdfs(chart_company):
    store = chart_company.store
    assert list(chart_company.invoices) == list(jde_invoices.NAMED)
    # The queue lists the ones to look at first: the five with a line left for a person in the order read, then the
    # two that arrived coded from last year's journal (D-212), the surer last.
    ids = {name: invoice_id for name, (_, invoice_id) in chart_company.invoices.items()}
    coded = [ids["one_line"], ids["quebec"]]
    assert [i["id"] for i in store.list_invoices(REVIEW)] == [i for i in ids.values() if i not in coded] + coded
    for name, (invoice, invoice_id) in chart_company.invoices.items():
        read = store.get_invoice(invoice_id)["ai_output"]
        assert (read["vendor_name"], read["invoice_number"], read["invoice_date"], read["currency"]) == (
            invoice.supplier_name, invoice.number, invoice.date.isoformat(), invoice.currency), name  # fmt: skip
        assert (read["subtotal"], read["grand_total"]) == (float(invoice.subtotal), float(invoice.total)), name
        assert {t["tax_type"]: t["tax_amount"] for t in read["tax_lines"]} == {
            tax.code: float(tax.amount) for tax in invoice.taxes}, name  # fmt: skip
        assert [(li["description"], li["amount"]) for li in read["line_items"]] == [
            (line.description, float(line.amount)) for line in invoice.lines], name  # fmt: skip


def test_the_company_is_loaded_into_a_new_database_only(store, ground_truth, tmp_path):
    store.add_invoice(tmp_path / "real.pdf", ground_truth, {"requires_review": True})
    with pytest.raises(ValueError, match="new database only"):
        demo.load_company(store)
    assert store.account_pairs() is None and not store.has_vendor_master() and len(store.list_invoices()) == 1
    assert not (store.path.parent / demo.COMPANY_FOLDER).exists()


@pytest.mark.slow
def test_demo_company_command(tmp_path, capsys):
    db = tmp_path / "private" / "ap_coder.db"
    assert cli.main(["--db", str(db), "demo", "--company"]) == 0
    said = capsys.readouterr().err
    assert "MAPLE RIDGE HOLDINGS LTD., 7 invoices to review" in said and str(db.parent / "demo_company") in said
    store = Store(db)
    assert len(store.list_invoices(REVIEW)) == 7 and store.account_pairs() is not None
    assert store.chart_settings().home_business_unit == jde_company.MAIN_UNIT
    assert simple_screens(store)  # as a new laptop installation starts: the short menu
    assert store.get_setting("sign_in") == "off"  # made-up data: no sign-in to try it (D-243)
    assert "without signing in" in said
    assert cli.main(["--db", str(db), "demo", "--company"]) == 1
    assert "Not loaded: The made-up company is loaded into a new database only" in capsys.readouterr().err
    assert len(Store(db).list_invoices()) == 7
