"""Custom CSV layouts for an ERP's import."""

import json

from ap_coder.erp import export_layout as el
from ap_coder.erp import exports
from tests.conftest import SAMPLE_STEM, SAMPLES


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
    # no apostrophe: the file is for the ERP's loader, not Excel (E2-2)
    assert lines[1] == "FAB01;V10023;NW-2026-0912;14/09/2026;14/10/2026;6010;4350,00;=Laptops"
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
    assert text.splitlines()[0].startswith(
        ",Northwind IT Solutions Inc.,NW-2026-0912,2026-09-14,2026-10-14,CAD,6010,CC400"
    )


def test_yen_amounts_have_no_decimals():
    """The yen has no cents: an ERP import of 1000.00 JPY reads a value the invoice never had."""
    inv = _invoice()
    inv["final_output"]["currency"] = "JPY"
    layout = el.Layout([el.Column("Amount", "amount"), el.Column("Currency", "currency")])
    data, _, _ = exports.build("custom", [inv], {}, 3, {}, layout)
    rows = data.decode("utf-8-sig").splitlines()[1:]
    assert rows[0].startswith("4350,") and ".00" not in "\n".join(rows)


def test_whole_yen_lines_add_up_to_the_invoice_total():
    """A distribution spread in cents (10.66 + 10.67 + 10.67), each line rounded to the yen, is 33, not 32: the
    lines round half up and the largest line takes the difference, as in the JD Edwards files."""
    from tests.erp.test_jde import _yen_invoice

    inv = _yen_invoice(old_distribution=True)
    assert [e["amount"] for e in inv["final_output"]["gl_distribution"]] == [10.66, 10.67, 10.67, 1000.0, 52.0]
    layout = el.Layout([el.Column("Amount", "amount"), el.Column("Total", "grand_total")])
    rows = [line.split(",") for line in exports.build("custom", [inv], {}, 1, {}, layout)[0].decode("utf-8-sig")
            .splitlines()[1:]]  # fmt: skip
    assert [r[0] for r in rows] == ["11", "11", "11", "999", "52"]
    assert sum(int(r[0]) for r in rows) == 1084 and {r[1] for r in rows} == {"1084"}
    half = el.Layout([el.Column("Net", "net_amount")])
    one = {**inv["final_output"], "gl_distribution": [dict(inv["final_output"]["gl_distribution"][3], net_amount=2.5)]}
    assert exports.build("custom", [{**inv, "final_output": one}], {}, 1, {}, half)[0].decode("utf-8-sig") == (
        "Net\r\n3\r\n"
    )  # half up, as an amount is rounded everywhere else (not 2, the even yen)


def test_custom_csv_keeps_text_exactly_as_typed():
    """The custom CSV is for an ERP loader (E2-2): no spreadsheet apostrophe, so the ERP sees the invoice
    number the supplier printed."""
    inv = _invoice()
    lines = inv["final_output"]["gl_distribution"]
    inv["final_output"]["invoice_number"] = "+A-100"
    lines[1]["description"] = "- Labour"
    lines.append(dict(lines[0], line_number=2, description="=1+1"))
    layout = el.Layout([el.Column("Doc", "invoice_number"), el.Column("Text", "description")])
    text = exports.build("custom", [inv], {}, 3, {}, layout)[0].decode("utf-8-sig")
    assert sorted(text.splitlines()[1:]) == ["+A-100,- Labour", "+A-100,=1+1", "+A-100,=Laptops"]


def test_the_custom_csv_can_leave_out_the_byte_order_mark_for_a_database_loader():
    """B-224: the custom CSV (for an ERP's loader) always began with a UTF-8 byte-order mark, which a loader that
    reads plain UTF-8 (SQL*Loader, a bulk insert) takes as part of the first header ("\\ufeffVendor"). It stays on by
    default (Excel shows accents correctly), and a layout can now leave it out."""
    import codecs

    columns = [el.Column("Vendor", "vendor_name"), el.Column("Amount", "amount")]
    data = exports.build("custom", [_invoice()], {}, 3, {}, el.Layout(columns))[0]
    assert data.startswith(codecs.BOM_UTF8)  # as before
    plain = el.Layout(columns, byte_order_mark=False)
    data = exports.build("custom", [_invoice()], {}, 3, {}, plain)[0]
    assert not data.startswith(codecs.BOM_UTF8) and data.decode("utf-8").startswith("Vendor,Amount\r\n")
    assert el.Layout.from_json(plain.to_json()).byte_order_mark is False
    assert el.Layout.from_json('{"delimiter": ";"}').byte_order_mark is True  # a layout saved before: as it was
