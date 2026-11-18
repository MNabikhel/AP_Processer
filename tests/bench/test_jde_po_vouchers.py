"""The made-up PO-matched invoices for the pilot tester's voucher match test (``bench.jde_po_vouchers``, D-265): each
case keyed as expected under both setups, end to end from its PDF on the made-up company (read, matched to its PO,
the Exports page's keying sheet), and the command that writes the set."""

import io

import openpyxl
import pytest

from ap_coder.bench import jde_company, jde_po_vouchers
from ap_coder.bench.jde_po_vouchers import AREAS, NAMES, PILOT, SETUPS, Row
from ap_coder.coding import po
from ap_coder.coding.tax import TaxTreatment, build_gl_distribution
from ap_coder.core.schema import InvoiceCoding
from ap_coder.core.store import Store
from ap_coder.erp import jde_keying, jde_settings
from ap_coder.erp.import_specs import PURCHASE_ORDERS, check
from ap_coder.erp.jde_voucher import EXEMPT_STATUSES
from ap_coder.web.files import read_sheet
from tests.erp.test_jde import _setup

TRUTH = jde_company.build(1, "small", 2025)


def load_pos(store: Store, files: dict[str, bytes]) -> None:
    """The set's PO files loaded as the Purchase orders page reads them: every column by its own name, none picked by
    hand."""
    for name, data in files.items():
        sheet, _ = read_sheet(name, data)
        found = check(PURCHASE_ORDERS, list(sheet.columns))
        assert found.ok and not found.guessed and not found.twice, (name, found)
        rows, skipped = po.rows_from_records(sheet.to_dict("records"), found.found)
        assert skipped == 0
        store.import_purchase_orders(rows)


def keyed(v: jde_keying.Voucher) -> list[Row]:
    return [Row(m.order_number, m.order_type, m.order_company, m.line, m.quantity, m.uom, m.amount, m.account,
                m.taxed, m.tax_code, m.tax_area, m.on_po) for m in v.matches]  # fmt: skip


def settings_of(setup: str, base: jde_settings.JdeSettings | None = None) -> jde_settings.JdeSettings:
    return (jde_po_vouchers.pilot_settings if setup == PILOT else jde_po_vouchers.area_settings)(base)


@pytest.mark.parametrize("setup", SETUPS)
def test_each_case_is_keyed_with_the_rows_expected_and_adds_up_to_its_gross(tmp_path, setup):
    """D-265: every case of the set, as AP Coder should read it, gets the voucher match rows expected under the
    pilot's setup (the prototype's GST-only area, the PST and QST kept in the expense on a line of their own) and under
    a tax area for each province (BC, SK and MB on code C, the QST recovered): amounts matched + the tax JD Edwards
    works out on the rows = the gross, for each."""
    made = jde_po_vouchers.cases(TRUTH)
    assert [c.name for c in made] == list(NAMES)
    store = Store(tmp_path / "ap.db")
    load_pos(store, jde_po_vouchers.po_files(made))
    js = settings_of(setup)
    tax = _setup(QST=TaxTreatment("QST", "expense_to_line")) if setup == PILOT else _setup()
    invoices = []
    for n, case in enumerate(made, 1):
        final = case.coding()
        final["gl_distribution"] = build_gl_distribution(InvoiceCoding.model_validate(final), tax)
        invoices.append({"id": n, "final_output": final})
    numbers = {case.coding()["vendor_name"]: str(case.supplier.number) for case in made}
    ready, held = jde_keying.vouchers(invoices, js, lambda final: numbers[final["vendor_name"]],
                                      po_matches=jde_keying.po_matches(store, invoices, js.charge_account))  # fmt: skip
    assert held == [] and len(ready) == len(made)
    for v, case in zip(ready, made, strict=True):
        assert keyed(v) == case.expected(setup), case.name
        assert v.balanced and v.match_tax == case.tax(setup) and v.gross == float(case.total), case.name
        assert jde_keying._check(v).endswith(": balanced")
    by_name = {case.name: v for v, case in zip(ready, made, strict=True)}
    if setup == PILOT:  # the PST on a line of its own, said; the partial match and the quantity-less line, said
        assert any(n.startswith("The PST (64.05) is not recovered and tax area CDNGST does not hold it")
                   for n in by_name["bc_gst_pst"].notes)  # fmt: skip
        assert "Order 41112 line 1.000: 60 of the 100 open matched; the line stays open for 40 (do not close it in " \
               "Voucher Match)." in by_name["ab_partial"].notes  # fmt: skip
        assert any("has no quantity in the purchase orders loaded" in n for n in by_name["ab_no_quantity"].notes)
        assert any("Self-assessment report" in n for n in by_name["bc_self_assessed"].notes)
    else:
        assert any("JD Edwards accrues the PST not charged" in n for n in by_name["bc_self_assessed"].notes)
    assert not any(w for v in ready for w in v.warnings), [(v.invoice_number, v.warnings) for v in ready]


def test_the_set_read_from_its_pdfs_is_keyed_on_the_exports_pages_sheet_as_expected(chart_company, tmp_path):
    """D-265, end to end on the made-up company (its supplier list, chart and JD Edwards set up as the prototype's):
    the set written as the command writes it, its PO files loaded, each PDF read by the pipeline (no chat model, no
    page reader), approved as read, and the Exports page's keying sheet made for the batch: each voucher's Voucher
    Match rows are the ones expected under the pilot's setup, and its check line adds up to the gross."""
    from ap_coder.ai.pipeline import InvoicePipeline
    from ap_coder.web.pages.exports import keying_file
    from tests.webclient import web_settings

    store = chart_company.store
    folder = tmp_path / "set"
    made = jde_po_vouchers.write(folder, chart_company.truth)
    load_pos(store, {name: (folder / name).read_bytes()
                     for name in (jde_po_vouchers.OPEN_PO_FILE, jde_po_vouchers.ORDER_DETAILS_FILE)})  # fmt: skip
    jde_settings.save(store, settings_of(PILOT, jde_settings.load(store)))
    pipeline = InvoicePipeline(web_settings(), store.reference_data(), store=store)
    ids = []
    for case in made:
        result = pipeline.process(folder / f"{case.name}.pdf")
        read = store.get_invoice(result.invoice_id)["ai_output"]
        want = case.coding()
        for name in ("vendor_name", "invoice_number", "po_number", "ship_to_province", "subtotal", "grand_total"):
            assert read[name] == want[name], (case.name, name)
        assert [
            (li["description"], li["quantity"], li["amount"], li["taxes_applied"]) for li in read["line_items"]
        ] == [(li["description"], li["quantity"], li["amount"], li["taxes_applied"]) for li in want["line_items"]]
        assert [li["tax_status"] in EXEMPT_STATUSES for li in read["line_items"]] == [
            bool(li["tax_status"]) for li in want["line_items"]
        ]  # (zero-rated, printed so, is read as exempt)
        store.approve_invoice(result.invoice_id, read, "Pat Tester")
        ids.append(result.invoice_id)
    batch = store.create_export_batch(ids, jde_keying.FORMAT, actor="Pat Tester")
    data, _, _ = keying_file(store, batch)
    book = openpyxl.load_workbook(io.BytesIO(data))
    assert [r[0].value for r in book["Not ready"].iter_rows(min_row=2)] == []
    blocks = _match_blocks([list(r) for r in book["Keying sheet"].iter_rows(values_only=True)])
    for case in made:
        rows, check = blocks[case.number]
        assert rows == [_cells(row) for row in case.expected(PILOT)], case.name
        assert check[1] == "Amounts matched + tax on the rows = gross" and check[3].endswith(": balanced"), case.name
        assert check[2] == pytest.approx(float(case.total) - case.tax(PILOT)), case.name


def _match_blocks(rows: list[list]) -> dict[str, tuple[list[list], list]]:
    """{invoice number: (its voucher match rows' first eleven cells, its check line)} of a keying sheet."""
    out, number, rows_of = {}, "", None
    for row in rows:
        if row[2] == "Invoice number":
            number = row[3]
        elif row[: len(jde_keying.MATCH_COLUMNS)] == jde_keying.MATCH_COLUMNS:
            rows_of = []
        elif row[0] == "Check" and rows_of is not None:
            out[number], rows_of = (rows_of, row[:4]), None
        elif rows_of is not None:
            rows_of.append(row[:11])
    return out


def _cells(row: Row) -> list:
    """An expected row as the keying sheet's cells hold it (an empty cell None, a whole number an int)."""

    def cell(value):
        return None if value in ("", None) else int(value) if isinstance(value, float) and value.is_integer() else value

    first = row.order_number if row.on_po else jde_keying.NOT_ON_PO
    return [cell(x) for x in (first, row.order_type, row.order_company, row.line, row.quantity, row.uom, row.amount,
                              row.account, row.taxed, row.tax_code, row.tax_area)]  # fmt: skip


def test_the_command_writes_the_set_with_its_po_files_and_the_rows_expected(tmp_path):
    """D-265: ``python -m ap_coder.bench po-vouchers --out DIR`` writes a PDF a case, the open PO lines as JD
    Edwards exports them, the rows expected under each setup and how to try them."""
    import json

    from ap_coder.bench.__main__ import main

    assert main(["po-vouchers", "--out", str(tmp_path)]) == 0
    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == sorted([*(f"{n}.pdf" for n in NAMES), "README.txt", "expected.json", "open_po_lines.xlsx",
                            "order_details.xlsx"])  # fmt: skip
    expected = json.loads((tmp_path / "expected.json").read_text(encoding="utf-8"))
    assert list(expected) == list(NAMES)
    assert expected["bc_gst_pst"][AREAS]["tax"] == 109.8 and expected["bc_gst_pst"][PILOT]["tax"] == 45.75
    assert expected["bc_gst_pst"][PILOT]["rows"][-1] == {
        "order_number": "", "order_type": "", "order_company": "", "line": "", "quantity": None, "uom": "",
        "amount": 64.05, "account": "0021061010.7410.4141", "taxed": "N", "tax_code": "E", "tax_area": "",
        "on_po": False}  # fmt: skip
    assert "python -m ap_coder demo --company" in (tmp_path / "README.txt").read_text(encoding="utf-8")
