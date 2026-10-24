"""Money fixes of the fourth bug sweep: "Approve anyway" over the account findings (B-926), the PO price tolerance
(B-927), the vendor master's address numbers (B-929, B-930)."""

from ap_coder.coding import po
from ap_coder.coding.validation import Issue
from ap_coder.coding.vendors import master_columns, master_rows, master_vendor_id
from ap_coder.core.schema import InvoiceCoding
from ap_coder.core.store import Store
from ap_coder.erp import jde
from ap_coder.web.invoice_view import is_hard

from .test_jde_gl_mode import coded, doc, line, tl


def test_approve_anyway_never_covers_a_wrong_or_missing_account():
    for code in ("GL_IS_TAX_ACCOUNT", "GL_UNKNOWN", "CC_UNKNOWN", "TAX_GL_UNMAPPED", "TAX_GL_UNKNOWN"):
        assert is_hard(Issue("error", code, "x")), code
    assert not is_hard(Issue("warning", "GL_UNKNOWN", "x"))


def _po_store(tmp_path, recs):
    store = Store(tmp_path / "t.db")
    rows, _ = po.rows_from_records(recs, po.map_columns(list(recs[0])))
    store.import_purchase_orders(rows)
    return store


def _po_line(**kw):
    base = dict(po_number="PO-500", vendor_name="Acme Ltd", line_number="1", description="M6 hex bolts zinc",
                quantity="20000", unit_price="0.10", amount="2000.00", received_quantity="20000", gl_code="6010",
                cost_center="", status="open")  # fmt: skip
    return {**base, **kw}


def _findings(store, qty, price, amount=None, desc="M6 hex bolts zinc"):
    li = line(1, amount if amount is not None else round(qty * price, 2), taxes=["HST"], desc=desc)
    li.update(quantity=qty, unit_price=price)
    d = doc([li], [tl("HST", "ON", 0.13, li["amount"], round(li["amount"] * 0.13, 2))], po_number="PO-500")
    return [f[1] for f in po.po_findings(InvoiceCoding.model_validate(d), store)]


def test_a_cheap_unit_price_five_times_the_po_is_flagged(tmp_path):
    store = _po_store(tmp_path, [_po_line()])
    assert "PO_PRICE_OVER" in _findings(store, 20000, 0.55)  # 11,000.00 billed against 2,000.00 ordered
    assert "PO_PRICE_OVER" not in _findings(store, 20000, 0.10)
    assert "PO_PRICE_OVER" not in _findings(store, 20000, 0.101, 2020.0)  # within 2% and a dollar


def test_a_line_amount_above_its_quantity_at_the_po_price_is_flagged(tmp_path):
    store = _po_store(tmp_path, [_po_line(unit_price="100", quantity="10", amount="1000", received_quantity="10")])
    # the unit price printed as the PO's, the amount not: 10 x 100 = 1,000 but 1,500 billed
    assert "PO_PRICE_OVER" in _findings(store, 10, 100.0, amount=1500.0)
    assert "PO_PRICE_OVER" not in _findings(store, 10, 100.0, amount=1000.50)
    assert "PO_PRICE_OVER" not in _findings(store, 10, 100.40)  # 40 cents a unit on $100: within the 50 cents


def test_jde_address_numbers_are_read_as_the_erp_id():
    assert master_columns(["Address Number", "Alpha Name"]) == {"erp_id": "Address Number", "vendor_name": "Alpha Name"}
    assert master_columns(["AN8", "Name"])["erp_id"] == "AN8"


def test_two_suppliers_with_different_address_numbers_stay_two_vendors(tmp_path):
    store = Store(tmp_path / "t.db")
    records = [
        {"Name": "Great Lakes Mechanical Inc.", "AN8": "40212", "GST/HST Number": "123456782RT0001"},
        {"Name": "Great Lakes Mechanical Limited", "AN8": "40219", "GST/HST Number": "897654321RT0001"},
        {"Name": "Northwind IT Solutions", "AN8": "40001", "GST/HST Number": ""},
        {"Name": "Northwind IT Solutions Inc.", "AN8": "", "GST/HST Number": ""},  # set up twice: still merged
    ]
    result = store.import_vendor_master(master_rows(records, master_columns(list(records[0]))))
    assert result["duplicates"] == [["Northwind IT Solutions", "Northwind IT Solutions Inc."]]
    ids = store.vendor_ids()
    assert sorted(v for k, v in ids.items() if not k.startswith(("gst:", "bn:", "bnx:"))) == ["40001", "40212", "40219"]
    names = {v["display_name"] for v in store.master_vendors()}
    assert {"Great Lakes Mechanical Inc.", "Great Lakes Mechanical Limited"} <= names
    # An invoice is matched by its GST/HST number first: the Limited's invoices go to the Limited's AN8
    assert master_vendor_id(ids, "Great Lakes Mechanical Limited", "897654321RT0001") == "40219"
    assert master_vendor_id(ids, "Great Lakes Mechanical Inc.", "123456782 RT0001") == "40212"
    assert master_vendor_id(ids, "Great Lakes Mechanical Inc.", "") == "40212"  # by name
    inv = coded(
        doc(
            [line(1, 100.0, taxes=["HST"])],
            [tl("HST", "ON", 0.13, 100.0, 13.0)],
            vendor_name="Great Lakes Mechanical Limited",
            gst_hst_registration_number="897654321RT0001",
        )
    )
    assert jde.resolve_an8(inv["final_output"], ids, jde.JdeSettings()) == "40219"
    # imported again: still two, nothing doubled
    store.import_vendor_master(master_rows(records, master_columns(list(records[0]))))
    assert len([v for v in store.master_vendors() if v["display_name"].startswith("Great Lakes")]) == 2
