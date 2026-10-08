"""Custom CSV layouts for an ERP's import."""

import json

from ap_coder import export_layout as el
from ap_coder import exports

from .conftest import SAMPLE_STEM, SAMPLES


def _invoice():
    gt = json.loads((SAMPLES / "ground_truth" / f"{SAMPLE_STEM}.json").read_text())
    gt["gl_distribution"] = [
        {"kind": "expense", "line_number": 1, "gl_code": "6010", "cost_center": "CC400", "net_amount": 4350.0,
         "non_recoverable_tax": 0.0, "amount": 4350.0, "description": "=Laptops"},
        {"kind": "tax", "line_number": None, "gl_code": "2310", "cost_center": "", "net_amount": 0.0,
         "non_recoverable_tax": 0.0, "amount": 565.5, "description": "HST 13% (recoverable)"},
    ]  # fmt: skip
    return {"id": 7, "final_output": gt, "reviewer": "Jane", "reviewed_at": "2026-10-01T10:00:00",
            "file_name": "a.pdf", "due_date": "2026-10-14"}  # fmt: skip


def test_custom_layout_formats_dates_decimals_and_fixed_text():
    layout = el.Layout(
        [el.Column("Company", el.FIXED, "FAB01"), el.Column("Vendor", "vendor_id"), el.Column("Doc", "invoice_number"),
         el.Column("Date", "invoice_date"), el.Column("Due", "due_date"), el.Column("Account", "gl_code"),
         el.Column("Amount", "amount"), el.Column("Text", "description")],
        date_format="DD/MM/YYYY", delimiter=";", decimal_comma=True,
    )  # fmt: skip
    data, name, mime = exports.build("custom", [_invoice()], {}, 3, {"northwind it solutions": "V10023"}, layout)
    lines = data.decode("utf-8-sig").splitlines()
    assert name == "ap_coder_export_3_custom.csv" and mime == "text/csv"
    assert lines[0] == "Company;Vendor;Doc;Date;Due;Account;Amount;Text"
    assert lines[1] == "FAB01;V10023;NW-2026-0912;14/09/2026;14/10/2026;6010;4350,00;'=Laptops"
    assert lines[2].endswith(";2310;565,50;HST 13% (recoverable)")


def test_layout_round_trip_and_bad_settings():
    layout = el.Layout([el.Column("A", "gl_code")], "MM/DD/YYYY", "\t", False, False)
    again = el.Layout.from_json(layout.to_json())
    assert again == layout
    junk = el.Layout.from_json('{"columns": [{"header": "X", "field": "no_such_field"}], "date_format": "?"}')
    assert junk.columns == el.default_columns() and junk.date_format == "YYYY-MM-DD"
    assert el.Layout.from_json("not json").columns == el.default_columns()


def test_default_layout_without_header():
    layout = el.Layout(el.default_columns(), header_row=False)
    text = exports.build("custom", [_invoice()], {}, 1, {}, layout)[0].decode("utf-8-sig")
    assert text.splitlines()[0].startswith(",NW-2026-0912,2026-09-14,2026-10-14,CAD,6010,CC400")
