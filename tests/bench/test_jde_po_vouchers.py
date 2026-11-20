"""The made-up PO-matched invoices for the pilot tester's voucher match test (``bench.jde_po_vouchers``, D-265): each
case keyed as expected under both setups, end to end from its PDF on the made-up company (read, matched to its PO,
the Exports page's keying sheet), and the command that writes the set."""

import io

import openpyxl
import pytest

from ap_coder.bench import jde_company, jde_exports, jde_po_vouchers
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


TYPED = ("PO {}", "PO-{}", "PO{}", "PO#{}", " #{} ", "PO# {}")  # how a PO is printed; the last over VLPO's 8


@pytest.mark.parametrize("setup", SETUPS)
def test_the_z_files_name_the_order_and_its_lines_as_the_keying_sheet_does_whatever_the_po_typed(tmp_path, setup):
    """B-2921 (D-284): the Z-files sent the PO number as typed in VLPO and F0411Z1T's DOCO ("PO-41101", "PO 41106"),
    while the keying sheet keys the order it is matched to (41101): DOCO is JD Edwards' order number, and "PO-41101"
    names none. Every case of the set, its PO typed with a prefix, a "#" or spaces, now goes with the matched order's
    number, type and company, each row on the PO line the sheet keys; one typed longer than VLPO's 8 characters
    ("PO# 41105") is held by both, to be typed as JD Edwards has it (JDE_PO_LONG)."""
    from ap_coder.erp import jde, jde_zfiles

    made = jde_po_vouchers.cases(TRUTH)
    store = Store(tmp_path / "ap.db")
    load_pos(store, jde_po_vouchers.po_files(made))
    js = settings_of(setup)
    tax = _setup(QST=TaxTreatment("QST", "expense_to_line")) if setup == PILOT else _setup()
    invoices = []
    for n, case in enumerate(made, 1):
        final = {**case.coding(), "po_number": TYPED[n % len(TYPED)].format(case.po)}
        final["gl_distribution"] = build_gl_distribution(InvoiceCoding.model_validate(final), tax)
        invoices.append({"id": n, "final_output": final})
    numbers = {case.coding()["vendor_name"]: str(case.supplier.number) for case in made}

    def lookup(final):
        return numbers[final["vendor_name"]]

    matches = jde_keying.po_matches(store, invoices, js.charge_account)
    ready, held = jde_keying.vouchers(invoices, js, lookup, po_matches=matches)
    left_out = jde.validate(invoices, js, lookup)
    long = [inv["id"] for inv in invoices if len(inv["final_output"]["po_number"].strip()) > 8]
    assert long and [h.invoice_id for h in held] == long == sorted(left_out)
    assert all(why[0].endswith("is longer than 8 characters (VLPO)") for why in left_out.values())
    files = jde_zfiles.export_batch([inv for inv in invoices if inv["id"] not in left_out], js, lookup,
                                    po_matches=matches)  # fmt: skip
    heads = {h["VLEDTN"].split("-")[0]: h for h in _csv(files[jde_zfiles.HEADER_FILE])}
    sent: dict[str, list[dict]] = {}
    for m in _csv(files[jde_zfiles.MATCH_FILE]):
        sent.setdefault(m["EDTN"].split("-")[0], []).append(m)
    assert len(ready) == len(heads) == len(made) - len(long)
    for v in ready:
        head, rows = heads[f"APC{v.invoice_id}"], sent[f"APC{v.invoice_id}"]
        (order,) = {(m.order_number, m.order_type, m.order_company) for m in v.matches if m.on_po}
        assert (head["VLPO"], head["VLPDCT"], head["VLPKCO"]) == order, v.invoice_number
        keyed_rows = [(m.order_number, m.order_type, m.order_company, m.line) for m in v.matches if m.on_po]
        assert [(m["DOCO"], m["DCTO"], m["KCOO"], m["LNID"]) for m in rows if m["LNID"]] == keyed_rows, v.invoice_number
        assert {(m["DOCO"], m["DCTO"], m["KCOO"]) for m in rows} == {order}, v.invoice_number


def _csv(data: bytes) -> list[dict]:
    import csv

    return list(csv.DictReader(io.StringIO(data.decode("utf-8"))))


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
    Edwards exports them, the rows expected under each setup and how to try them; and the progress billings beside
    them (D-273), their subcontracts' lines as report 6 gives them and their rows expected."""
    import json

    from ap_coder.bench.__main__ import main

    assert main(["po-vouchers", "--out", str(tmp_path)]) == 0
    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == sorted([*(f"{n}.pdf" for n in (*NAMES, *jde_po_vouchers.PROGRESS_NAMES)), "README.txt",
                            "expected.json", "open_po_lines.xlsx", "order_details.xlsx", "subcontract_lines.xlsx",
                            "progress_billings.json"])  # fmt: skip
    progress = json.loads((tmp_path / "progress_billings.json").read_text(encoding="utf-8"))
    assert list(progress) == list(jde_po_vouchers.PROGRESS_NAMES)
    assert progress["first_billing"]["pilot_whole"] == [{
        "order_number": "41201", "order_type": "OS", "order_company": "00210", "line": "1.000", "quantity": None,
        "amount": 45000.0, "retained": 4500.0, "account": "0021061022.7330.3207", "taxed": "Y", "tax_code": "V",
        "tax_area": "CDNGST"}]  # fmt: skip
    assert progress["gst_on_net"]["pilot_whole"] == [] and progress["gst_on_net"]["pilot_net"][0]["retained"] == 6000
    assert "Subcontract progress billings" in (tmp_path / "README.txt").read_text(encoding="utf-8")
    expected = json.loads((tmp_path / "expected.json").read_text(encoding="utf-8"))
    assert list(expected) == list(NAMES)
    assert expected["bc_gst_pst"][AREAS]["tax"] == 109.8 and expected["bc_gst_pst"][PILOT]["tax"] == 45.75
    assert expected["bc_gst_pst"][PILOT]["rows"][-1] == {
        "order_number": "", "order_type": "", "order_company": "", "line": "", "quantity": None, "uom": "",
        "amount": 64.05, "account": "0021061010.7410.4141", "taxed": "N", "tax_code": "E", "tax_area": "",
        "on_po": False}  # fmt: skip
    assert "python -m ap_coder demo --company" in (tmp_path / "README.txt").read_text(encoding="utf-8")


def _progress_rows(v: jde_keying.Voucher) -> list[jde_po_vouchers.ProgressRow]:
    return [jde_po_vouchers.ProgressRow(m.order_number, m.order_type, m.order_company, m.line, m.quantity, m.amount,
                                        m.retained, m.account, m.taxed, m.tax_code, m.tax_area)
            for m in v.matches]  # fmt: skip


def _progress_vouchers(store: Store, made, js):
    invoices = []
    for n, case in enumerate(made, 1):
        final = {**case.coding(), "progress_billing": case.progress()}
        final["gl_distribution"] = build_gl_distribution(InvoiceCoding.model_validate(final), _setup())
        invoices.append({"id": n, "final_output": final})
    numbers = {case.coding()["vendor_name"]: str(case.supplier.number) for case in made}
    return jde_keying.vouchers(invoices, js, lambda final: numbers[final["vendor_name"]],
                               po_matches=jde_keying.po_matches(store, invoices, js.charge_account))  # fmt: skip


@pytest.mark.parametrize("setup", SETUPS)
@pytest.mark.parametrize("tax_on", [jde_po_vouchers.WHOLE, jde_po_vouchers.NET])
def test_each_progress_billing_is_keyed_with_its_retained_amounts_or_held_with_why(tmp_path, setup, tax_on):
    """D-273: each made-up progress billing, as AP Coder should read it, on its subcontract (report 6's lines, order
    type OS, 10% retainage): each row's Retained Amount the invoice's holdback, its quantity matched left empty;
    amounts matched - retained + the tax on the rows = the amount due as printed. The one charged its tax the other way
    than Voucher Match works it out is held, so is the holdback release, with why; a holdback printed other than the
    subcontract's percent is warned; the work billed before is JD Edwards' vouchered, said nothing."""
    made = jde_po_vouchers.progress_cases(TRUTH)
    assert [c.name for c in made] == list(jde_po_vouchers.PROGRESS_NAMES)
    store = Store(tmp_path / "ap.db")
    lines = list({(pl.order, pl.line): pl for case in made for pl in case.po_lines}.values())
    load_pos(store, {jde_po_vouchers.SUBCONTRACT_FILE: jde_exports.po_lines_report_xlsx(lines)})
    js = jde_po_vouchers.subcontract_settings(None, tax_on, setup)
    ready, held = _progress_vouchers(store, made, js)
    keyed = [c for c in made if not c.held(tax_on)]
    assert [h.invoice_number for h in held] == [c.number for c in made if c.held(tax_on)]
    for h, case in zip(held, [c for c in made if c.held(tax_on)], strict=True):
        assert any(why.startswith(case.held(tax_on)) for why in h.why), (case.name, h.why)
    assert [v.invoice_number for v in ready] == [c.number for c in keyed]
    for v, case in zip(ready, keyed, strict=True):
        assert _progress_rows(v) == case.expected(setup), case.name
        assert (v.retained, v.amount_due, v.match_tax, v.gross) == (
            float(case.holdback),
            float(case.due),
            float(case.tax),
            float(case.this_period + case.tax),
        ), case.name
        assert v.balanced and jde_keying._check(v).endswith(f"= {case.due:,.2f}: balanced"), case.name
        assert not any("has no quantity" in n for n in v.notes), case.name
    by_name = {case.name: v for v, case in zip(ready, keyed, strict=True)}
    differs = [w for v in ready for w in v.warnings]
    if "holdback_differs" in by_name:
        assert differs == ["The invoice holds back 2,500.00 (5% printed), the subcontract 10% (5,000.00): the rows "
                           "retain the invoice's 2,500.00. If the subcontract's is to be held back, type it in "
                           "Retained Amount and pay 2,500.00 less than the invoice's amount due."]  # fmt: skip
    else:
        assert differs == []


def test_a_progress_billing_is_held_while_subcontract_billings_are_off(tmp_path):
    """D-273: subcontract progress billings off (the default, until the JD Edwards team confirms them): every billing
    on a subcontract is held for a person, said; the release too, as a release."""
    made = jde_po_vouchers.progress_cases(TRUTH)
    store = Store(tmp_path / "ap.db")
    lines = list({(pl.order, pl.line): pl for case in made for pl in case.po_lines}.values())
    load_pos(store, {jde_po_vouchers.SUBCONTRACT_FILE: jde_exports.po_lines_report_xlsx(lines)})
    js = jde_po_vouchers.pilot_settings()
    assert js.po_subcontracts is False
    ready, held = _progress_vouchers(store, made, js)
    assert ready == [] and len(held) == len(made)
    assert [h.why[0].split(":")[0] for h in held[:4]] == [
        f"a subcontract's progress billing (order {c.po} OS)" for c in made[:4]]  # fmt: skip
    assert "subcontract progress billings are off (Settings → JD Edwards E1 → Subcontract progress billings)" in \
        held[0].why[0]  # fmt: skip
    assert held[4].why[0].startswith("a holdback release")


def test_the_progress_billings_read_from_their_pdfs_are_keyed_on_the_exports_pages_sheet(chart_company, tmp_path):
    """D-273, end to end on the made-up company: the billings written as the command writes them, their subcontracts'
    lines loaded from report 6, each PDF read by the pipeline (no chat model, no page reader): its work this period
    the subtotal (an application's one line), its tax on its base, the total before the holdback, and what it prints
    of the billing kept beside the coding; approved as read, the Exports page's keying sheet has each one's Voucher
    Match rows with their Retained Amounts and its check line to the amount due; the release and the billing charged
    its tax the other way are on "Not ready". Voucher Match set to work the tax out on the amount less the retained,
    the billing charged so is keyed and the others held."""
    from ap_coder.ai.pipeline import InvoicePipeline
    from ap_coder.web.pages.exports import keying_file
    from tests.webclient import web_settings

    store = chart_company.store
    folder = tmp_path / "set"
    made = jde_po_vouchers.write_progress(folder, chart_company.truth)
    load_pos(store, {jde_po_vouchers.SUBCONTRACT_FILE: (folder / jde_po_vouchers.SUBCONTRACT_FILE).read_bytes()})
    jde_settings.save(store, jde_po_vouchers.subcontract_settings(jde_settings.load(store)))
    pipeline = InvoicePipeline(web_settings(), store.reference_data(), store=store)
    ids = []
    for case in made:
        result = pipeline.process(folder / f"{case.name}.pdf")
        read = store.get_invoice(result.invoice_id)["ai_output"]
        want = case.coding()
        for name in ("vendor_name", "invoice_number", "po_number", "ship_to_province", "subtotal", "tax_total",
                     "grand_total"):  # fmt: skip
            assert read[name] == want[name], (case.name, name, read[name])
        assert [(li["description"], li["amount"], li["taxes_applied"]) for li in read["line_items"]] == [
            (li["description"], li["amount"], li["taxes_applied"]) for li in want["line_items"]
        ], case.name
        assert [(t["tax_type"], t["taxable_amount"], t["tax_amount"]) for t in read["tax_lines"]] == [
            (t["tax_type"], t["taxable_amount"], t["tax_amount"]) for t in want["tax_lines"]
        ], case.name
        assert read["progress_billing"] == case.progress(), case.name
        store.approve_invoice(result.invoice_id, read, "Pat Tester")
        ids.append(result.invoice_id)
    batch = store.create_export_batch(ids, jde_keying.FORMAT, actor="Pat Tester")
    for tax_on in (jde_po_vouchers.WHOLE, jde_po_vouchers.NET):
        jde_settings.save(store, jde_po_vouchers.subcontract_settings(jde_settings.load(store), tax_on))
        data, _, _ = keying_file(store, batch)
        book = openpyxl.load_workbook(io.BytesIO(data))
        assert [r[3].value for r in book["Not ready"].iter_rows(min_row=2)] == [
            c.number for c in made if c.held(tax_on)], tax_on  # fmt: skip
        blocks = _retained_blocks([list(r) for r in book["Keying sheet"].iter_rows(values_only=True)])
        assert list(blocks) == [c.number for c in made if not c.held(tax_on)], tax_on
        for case in (c for c in made if not c.held(tax_on)):
            rows, check = blocks[case.number]
            assert rows == [_progress_cells(row) for row in case.expected(PILOT)], case.name
            assert check[1] == "Amounts matched - retained + tax on the rows = amount due", case.name
            assert check[3].endswith(f"= {case.due:,.2f}: balanced"), case.name


def _retained_blocks(rows: list[list]) -> dict[str, tuple[list[list], list]]:
    """{invoice number: (its voucher match rows' first twelve cells, its check line)} of a keying sheet's
    subcontract vouchers (a Retained Amount column after Amount Matched)."""
    head = [*jde_keying.MATCH_COLUMNS[:7], jde_keying.RETAINED, *jde_keying.MATCH_COLUMNS[7:]]
    out, number, rows_of = {}, "", None
    for row in rows:
        if row[2] == "Invoice number":
            number = row[3]
        elif row[: len(head)] == head:
            rows_of = []
        elif row[0] == "Check" and rows_of is not None:
            out[number], rows_of = (rows_of, row[:4]), None
        elif rows_of is not None:
            rows_of.append(row[:12])
    return out


def _progress_cells(row: jde_po_vouchers.ProgressRow) -> list:
    def cell(value):
        return None if value in ("", None) else int(value) if isinstance(value, float) and value.is_integer() else value

    return [
        cell(x)
        for x in (
            row.order_number,
            row.order_type,
            row.order_company,
            row.line,
            row.quantity,
            None,
            row.amount,
            row.retained,
            row.account,
            row.taxed,
            row.tax_code,
            row.tax_area,
        )
    ]


def test_a_po_matched_invoice_holding_back_part_of_its_total_is_never_keyed_at_its_total(chart_company, tmp_path):
    """B-2700: an invoice on a purchase order (OP) taking a holdback off under its total ("Total 825.09 | Less 10%
    holdback (78.58) | Balance due 746.51") was keyed in Voucher Match at its total: the amounts matched and the tax
    added up to it, so the holdback was paid with the rest. Held for a person while subcontract progress billings are
    off; on, each row retains its share of the holdback and the check line comes to the balance due."""
    from decimal import Decimal

    from ap_coder.ai.pipeline import InvoicePipeline
    from ap_coder.web.pages.exports import keying_file
    from tests.webclient import web_settings

    store = chart_company.store
    case = jde_po_vouchers.cases(chart_company.truth)[0]  # (Alberta, GST, two lines on PO 41101)
    load_pos(store, {jde_po_vouchers.OPEN_PO_FILE: jde_exports.open_po_lines_xlsx(case.po_lines)})
    jde_settings.save(store, settings_of(PILOT, jde_settings.load(store)))
    path = tmp_path / "held_back.pdf"
    path.write_bytes(jde_po_vouchers.pdf(case, chart_company.truth, holdback=Decimal(10)))
    result = InvoicePipeline(web_settings(), store.reference_data(), store=store).process(path)
    read = store.get_invoice(result.invoice_id)["ai_output"]
    assert (read["grand_total"], read["progress_billing"]["holdback"], read["progress_billing"]["amount_due"]) == (
        825.09, 78.58, 746.51)  # fmt: skip
    store.approve_invoice(result.invoice_id, read, "Pat Tester")
    batch = store.create_export_batch([result.invoice_id], jde_keying.FORMAT, actor="Pat Tester")
    book = openpyxl.load_workbook(io.BytesIO(keying_file(store, batch)[0]))
    why = [r[-1].value for r in book["Not ready"].iter_rows(min_row=2)]
    assert len(why) == 1 and why[0].startswith("a billing that holds back 78.58: subcontract progress billings are off")
    jde_settings.save(store, jde_po_vouchers.subcontract_settings(jde_settings.load(store)))
    book = openpyxl.load_workbook(io.BytesIO(keying_file(store, batch)[0]))
    assert [r[0].value for r in book["Not ready"].iter_rows(min_row=2)] == []
    blocks = _retained_blocks([list(r) for r in book["Keying sheet"].iter_rows(values_only=True)])
    rows, check = blocks[case.number]
    assert [(r[3], r[6], r[7]) for r in rows] == [("1.000", 628.8, 62.88), ("2.000", 157, 15.7)]
    assert check[3] == "785.80 - 78.58 + 39.29 = 746.51: balanced"
