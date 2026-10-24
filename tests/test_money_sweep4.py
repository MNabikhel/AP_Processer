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


BISTRO = """Maple Leaf Bistro Inc.
455 King Street West, Toronto ON M5V 1K4
HST Reg. No: 55807 3029 RT 0001
INVOICE
Invoice No: MLB-20931
Bill To: Bench Company Ltd., 100 Main Street, Toronto ON M5H 2N2
Description
Amount
Client dinner - food (8 guests)
640.00
Wine and beverages
180.00
Subtotal
820.00
HST 13%
106.60
Gratuity
120.00
Total Due
CAD 1,046.60
"""


def test_a_gratuity_printed_after_the_tax_is_an_untaxed_line(reference):
    from ap_coder.ai.offline_coder import code_from_capture
    from ap_coder.capture.types import CaptureResult, FieldResult, LineReading
    from ap_coder.coding.validation import validate_coding

    values = {"vendor_name": "Maple Leaf Bistro Inc.", "invoice_number": "MLB-20931", "invoice_date": "2026-10-15",
              "currency": "CAD", "subtotal": 820.0, "hst_amount": 106.6, "tax_total": 106.6, "grand_total": 1046.6,
              "gst_hst_registration_number": "558073029RT0001"}  # fmt: skip
    capture = CaptureResult(
        fields={k: FieldResult(k, v, 0.9, "likely") for k, v in values.items()},
        line_items=[LineReading("Client dinner - food (8 guests)", None, None, 640.0),
                    LineReading("Wine and beverages", None, None, 180.0)],
    )  # fmt: skip
    coding = code_from_capture(capture, reference, [], text=BISTRO).coding
    tip = coding.line_items[-1]
    assert (tip.description, tip.amount, tip.taxes_applied, tip.tax_status) == ("Gratuity", 120.0, [], "out_of_scope")
    assert coding.subtotal == 940.0 and coding.tax_total == 106.6 and coding.grand_total == 1046.6
    codes = {i.code for i in validate_coding(coding, reference).issues}
    assert not codes & {"TOTAL_MISMATCH", "SUBTOTAL_MISMATCH", "TAX_CALC_MISMATCH"}, codes
    # No gap: nothing added (a gratuity already in the lines, or none)
    plain = code_from_capture(capture, reference, [], text=BISTRO.replace("Gratuity\n120.00\n", "")).coding
    assert len(plain.line_items) == 2


def test_a_meal_is_never_steered_to_the_100_percent_account_on_a_weak_match(tmp_path):
    from ap_coder.ai.suggest import suggest_gl
    from ap_coder.core.reference_data import load_reference_data

    from .conftest import DATA

    chart = tmp_path / "chart.csv"
    chart.write_text("gl_code,description,category,meals\n6010,Office supplies,Office,\n"
                     "6320,Meals & Entertainment,Travel,yes\n6325,Staff events & meals billed to clients,Travel,\n"
                     "2310,GST/HST receivable,Tax,\n", encoding="utf-8")  # fmt: skip
    reference = load_reference_data(chart, None, DATA / "tax_gl_mapping.csv")
    (top, *_rest) = suggest_gl("Client dinner – food (8 guests)", "Maple Leaf Bistro Inc.", [], reference)
    assert top.gl_code == "6320" and not top.auto
    assert any("6325" in r for r in top.reasons)  # the other account said, for a client's or a staff event
    # Coded to the 100% account by AP for this vendor before: strong evidence, kept
    feedback = [
        {
            "vendor_key": "maple leaf bistro",
            "vendor_name": "Maple Leaf Bistro Inc.",
            "description": "Client dinner – food (8 guests)",
            "final_gl": "6325",
            "final_cc": "",
            "outcome": "accepted",
            "created_at": f"2026-0{m}-01",
            "suggested_gl": "6325",
        }
        for m in (1, 2, 3)
    ]
    assert (
        suggest_gl("Client dinner – food (8 guests)", "Maple Leaf Bistro Inc.", feedback, reference)[0].gl_code
        == "6325"
    )
