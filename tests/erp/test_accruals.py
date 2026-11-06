"""Month-end accruals: received not invoiced, invoices not in the ERP, expected recurring invoices."""

import datetime as dt
import json

import pytest

from ap_coder.ai.pipeline import finalise_coding
from ap_coder.core.config import Settings
from ap_coder.core.schema import InvoiceCoding
from ap_coder.core.store import Store, load_sample_purchase_orders, load_sample_setup
from ap_coder.erp import accruals
from tests.conftest import SAMPLES, copy_database

END = dt.date(2026, 9, 30)


def _coded(store, stem, **changes):
    doc = {**json.loads((SAMPLES / "ground_truth" / f"{stem}.json").read_text()), **changes}
    output, _ = finalise_coding(InvoiceCoding.model_validate(doc), store.reference_data(), Settings(), store=store)
    return output


def test_demo_accruals(tmp_path, demo_template):
    store = copy_database(demo_template, tmp_path / "a.db")
    items = accruals.build(store, END)
    received = [a for a in items if a.source == accruals.RECEIVED]
    # Only the mount kits: the returned laptop and the freight billed without the PO number are not accrued.
    assert [(a.reference, a.amount, a.gl_code) for a in received] == [("PO-89904 line 5", 210.0, "6010")]
    not_in_erp = {a.reference.split(" (")[0] for a in items if a.source == accruals.NOT_IN_ERP}
    assert "Invoice NW-2026-0912" in not_in_erp and "Invoice ACMR-2026-1187" in not_in_erp
    assert "Invoice INV-30981" not in not_in_erp  # Cascade is dated October 1, after the period end
    totals = accruals.summary(items)
    assert totals[accruals.RECEIVED] == (1, {"CAD": 210.0})
    by_gl = [t for _, totals in accruals.by_gl_columns(items)[1] for t in totals.values()]
    assert sum(by_gl) == pytest.approx(round(sum(a.amount for a in items), 2))
    assert "PO-89904 line 5" in accruals.to_csv(items, END).decode("utf-8-sig")


def test_exported_rejected_and_closed_are_left_out(tmp_path):
    store = Store(tmp_path / "a.db")
    load_sample_setup(store)
    load_sample_purchase_orders(store)
    nw = _coded(store, "northwind_ON_HST_NW-2026-0912")
    a = store.add_invoice(tmp_path / "a.pdf", nw, {})
    store.approve_invoice(a, nw, "Jane")
    b = store.add_invoice(tmp_path / "b.pdf", {**nw, "invoice_number": "X-1"}, {})
    store.reject_invoice(b, "Jane", "duplicate")
    refs = {x.reference for x in accruals.build(store, END)}
    assert any("NW-2026-0912" in r for r in refs) and not any("X-1" in r for r in refs)
    store.create_export_batch([a], "csv")  # today: in the ERP at today's period end (D-133)
    assert not any("NW-2026-0912" in x.reference for x in accruals.build(store, dt.date.today()))
    assert any(x.reference == "PO-89904 line 1" for x in accruals.build(store, END))  # nothing billed yet
    store.set_po_status("89904", "closed")
    assert not any(x.reference.startswith("PO-89904") for x in accruals.build(store, END))


def test_an_invoice_exported_after_the_period_end_is_accrued_for_that_period(tmp_path):
    # Dated 14 September, exported on 2 October with the export day as its G/L date: JD Edwards has it in October,
    # so at 30 September it is accrued, and says why (D-133). Exported by 30 September, it was in the ERP then.
    from ap_coder.erp import jde
    from tests.webclient import client_for

    store = Store(tmp_path / "a.db")
    load_sample_setup(store)
    settings = jde.load(store)
    settings.gl_date = jde.GL_DATE_EXPORT
    jde.save(store, settings)
    nw = _coded(store, "northwind_ON_HST_NW-2026-0912")
    a = store.add_invoice(tmp_path / "a.pdf", nw, {})
    store.approve_invoice(a, nw, "Jane")
    batch = store.create_export_batch([a], "csv")

    def made_on(when):
        with store._conn() as conn:
            conn.execute("UPDATE export_batches SET created_at = ? WHERE id = ?", (when, batch))
        return [x for x in accruals.build(store, END) if f"(#{a})" in x.reference]

    late = made_on("2026-10-02T09:15:00")
    assert late and {x.note for x in late} == {"exported after the month end"}
    assert round(sum(x.amount for x in late), 2) == nw["subtotal"]  # its cost; the recoverable HST is not
    assert "exported after the month end" in accruals.to_csv(late, END).decode("utf-8-sig")
    assert "Exported after the month end" in client_for(store).get(f"/month-end?end={END}").text
    assert made_on("2026-09-30T16:40:00") == []
    assert made_on("2026-10-02T09:15:00") and not [x for x in accruals.build(store, dt.date(2026, 10, 31))
                                                   if f"(#{a})" in x.reference]  # fmt: skip


def test_with_the_invoice_date_as_g_l_date_an_invoice_exported_after_the_period_end_is_in_its_month(tmp_path):
    # JD Edwards' G/L date is the invoice date (the default): dated 14 September and exported on 2 October, the
    # voucher is in September already, so it is not accrued for 30 September as well (D-133).
    store = Store(tmp_path / "a.db")
    load_sample_setup(store)
    nw = _coded(store, "northwind_ON_HST_NW-2026-0912")
    a = store.add_invoice(tmp_path / "a.pdf", nw, {})
    store.approve_invoice(a, nw, "Jane")
    batch = store.create_export_batch([a], "csv")
    with store._conn() as conn:
        conn.execute("UPDATE export_batches SET created_at = ? WHERE id = ?", ("2026-10-02T09:15:00", batch))
    assert not [x for x in accruals.build(store, END) if f"(#{a})" in x.reference]


def test_a_resent_copy_of_a_bill_already_counted_is_not_accrued_again(tmp_path):
    store = Store(tmp_path / "a.db")
    load_sample_setup(store)
    nw = _coded(store, "northwind_ON_HST_NW-2026-0912")
    a = store.add_invoice(tmp_path / "a.pdf", nw, {})
    store.approve_invoice(a, nw, "Jane")
    store.create_export_batch([a], "csv")
    flagged = {"issues": [{"severity": "error", "code": "DUPLICATE_INVOICE", "message": f"same as invoice #{a}"}]}
    copy = store.add_invoice(tmp_path / "b.pdf", nw, flagged)  # the vendor sent it again: in review
    other = store.add_invoice(tmp_path / "c.pdf", {**nw, "invoice_number": "NW-2026-0999"}, flagged)
    accrued = {x.reference for x in accruals.build(store, END) if x.source == accruals.NOT_IN_ERP}
    assert not any(f"(#{copy})" in r for r in accrued)  # the bill is in the ERP already
    assert any(f"(#{other})" in r for r in accrued)  # flagged, but no bill with its number is counted
    in_erp = {"issues": [{"severity": "error", "code": "DUPLICATE_IN_ERP", "message": "in the ERP"}]}
    keyed = store.add_invoice(tmp_path / "d.pdf", {**nw, "invoice_number": "NW-2026-1000"}, in_erp)
    assert not any(f"(#{keyed})" in x.reference for x in accruals.build(store, END))


def test_expected_recurring_invoice(tmp_path):
    store = Store(tmp_path / "a.db")
    load_sample_setup(store)
    doc = _coded(store, "chinook_AB_GST_CCO-26-10418")
    for i, day in enumerate(("2026-05-31", "2026-06-30", "2026-07-31")):
        inv = {**doc, "invoice_number": f"C-{i}", "invoice_date": day}
        invoice_id = store.add_invoice(tmp_path / f"{i}.pdf", inv, {})
        store.approve_invoice(invoice_id, inv, "Jane")
    expected = [x for x in accruals.build(store, END) if x.source == accruals.RECURRING]
    assert len(expected) == 1 and expected[0].amount == doc["grand_total"] and expected[0].gl_code
    assert not [x for x in accruals.build(store, dt.date(2026, 8, 15)) if x.source == accruals.RECURRING]


def test_an_expected_recurring_invoice_is_not_accrued_beside_its_vendors_received_po_lines(tmp_path):
    # Chinook bills monthly, and its freight on PO-90155 was received, not invoiced, by 30 September: its next invoice
    # bills that freight. Accrued once, on the PO line; the page says why the expected invoice is left out.
    from tests.webclient import client_for

    store = Store(tmp_path / "a.db")
    load_sample_setup(store)
    load_sample_purchase_orders(store)
    stem = "chinook_AB_GST_CCO-26-10418"
    lines = json.loads((SAMPLES / "ground_truth" / f"{stem}.json").read_text())["line_items"]
    doc = _coded(store, stem, line_items=[li for li in lines if "PO-90155" not in li["description"]])
    for i, day in enumerate(("2026-05-31", "2026-06-30", "2026-07-31")):
        inv = {**doc, "invoice_number": f"C-{i}", "invoice_date": day}
        store.approve_invoice(store.add_invoice(tmp_path / f"{i}.pdf", inv, {}), inv, "Jane")
    items = accruals.build(store, END)
    chinook = [(a.source, a.reference, a.amount) for a in items
               if a.source != accruals.NOT_IN_ERP and a.vendor == doc["vendor_name"]]  # fmt: skip
    assert chinook == [(accruals.RECEIVED, "PO-90155 line 1", 385.0)]
    left_out: list[accruals.Accrual] = []
    assert accruals.build(store, END, left_out=left_out) == items
    why = (doc["vendor_name"], doc["grand_total"], accruals.ON_PO)
    assert [(a.vendor, a.amount, a.note) for a in left_out] == [why]
    text = client_for(store).get(f"/month-end?end={END}").text
    assert "Not accrued as expected recurring invoices" in text and "Chinook Courier" in text.split("Not accrued as")[1]


def test_received_not_invoiced_gives_what_matching_each_po_in_turn_gave(tmp_path):
    """Each invoice's lines are read once (only its date and lines of its coding, by SQLite) and each description and
    price matched to a PO's lines once: the same accruals as reading each PO and its vendor's invoices in turn and
    matching every line again (``reports_reference``), with several POs a vendor, lines alike at other prices, credits,
    lines without a description or price, invoices dated after the period end or only in their coding, on no PO,
    rejected, coded in review only, or with a coding Python reads and SQLite doesn't (NaN)."""
    from tests import reports_reference as ref

    store = Store(tmp_path / "a.db")
    load_sample_setup(store)
    load_sample_purchase_orders(store)
    items = [("Toner cartridge", 80.0), ("Toner cartridge", 95.0), ("Paper A4 case", 42.5),
             ("Delivery", 25.0), ("Printer service visit", 150.0)]  # fmt: skip
    rows = []
    for p in range(6):
        for n, (what, price) in enumerate(items[p % 3 : p % 3 + 3], 1):
            rows.append({"po_number": f"PO-7{p}", "vendor_name": "Ridge Office Supply" if p < 4 else "Lakeview Print",
                         "line_number": n, "description": what, "quantity": 10.0 + p, "unit_price": price + p % 2,
                         "amount": round((10.0 + p) * (price + p % 2), 2), "received_quantity": None if p == 5 else
                         4.0 + n, "gl_code": "6010", "cost_center": "", "status": "closed" if p == 3 else "open",
                         "amount_only": False, "open_amount": None})  # fmt: skip
    store.import_purchase_orders(rows)
    line_sets = [
        [{"description": "Toner cartridge", "quantity": 2, "unit_price": 80.0, "amount": 160.0},
         {"description": "Toner cartridge", "quantity": 1, "unit_price": 95.0, "amount": 95.0}],
        [{"description": "toner cartridge", "quantity": 1, "unit_price": 96.0, "amount": 96.0},
         {"description": "Paper A4 case", "quantity": 3, "unit_price": 42.5, "amount": 127.5}],
        [{"description": "Toner cartridge", "quantity": 1, "unit_price": 95.0, "amount": -95.0}],  # a credit
        [{"quantity": 2, "unit_price": None, "amount": 50.0}, {"description": None, "quantity": 1, "amount": 25.0}],
        [{"description": "Delivery", "quantity": "1", "unit_price": "25.00", "amount": 25.0}],
        [{"description": "Printer service visit", "quantity": 0.5, "unit_price": 150.0, "amount": 75.0}],
    ]  # fmt: skip
    for n in range(40):
        po = ["PO-70", "PO-71", "PO-72", "PO-74", "", ""][n % 6]
        vendor = "Lakeview Print" if po == "PO-74" or n % 5 == 0 else "Ridge Office Supply"
        doc = {"vendor_name": vendor, "invoice_number": f"R-{n}", "po_number": po, "currency": "CAD",
               "invoice_date": ["2026-09-12", "2026-10-03", ""][n % 3], "line_items": line_sets[n % len(line_sets)],
               "subtotal": 100.0, "grand_total": 105.0}  # fmt: skip
        lines = [{**li, "line_number": k, "predicted_gl_code": "6010"} for k, li in enumerate(doc["line_items"], 1)]
        invoice = store.add_invoice(tmp_path / f"{n}.pdf", {**doc, "line_items": lines}, {})
        if n % 4 == 1:  # approved as AP Coder read it, then its lines as they are (odd ones too) and maybe its date
            approved = {**doc, "line_items": [{**li, "description": li.get("description") or ""} for li in lines]}
            store.approve_invoice(invoice, approved, "Jane")
            with store._conn() as conn:
                conn.execute("UPDATE invoices SET final_output = ? WHERE id = ?",
                             (json.dumps({**approved, "line_items": lines,
                                          "invoice_date": "2026-09-30" if n % 8 == 1 else doc["invoice_date"]}),
                              invoice))  # fmt: skip
        elif n % 9 == 2:
            store.reject_invoice(invoice, "Jane", "not ours")
        if n == 7:  # a total Python reads (NaN) and SQLite does not
            with store._conn() as conn:
                conn.execute("UPDATE invoices SET ai_output = replace(ai_output, '105.0', 'NaN') WHERE id = ?",
                             (invoice,))  # fmt: skip
        if n == 9:  # no date in the invoice's own column: its coding's
            with store._conn() as conn:
                conn.execute("UPDATE invoices SET invoice_date = NULL WHERE id = ?", (invoice,))
    for end in (dt.date(2026, 9, 30), dt.date(2026, 10, 31), dt.date(2026, 8, 31)):
        assert accruals._received_not_invoiced(store, end) == ref.received_not_invoiced(store, end), end
    found = accruals._received_not_invoiced(store, dt.date(2026, 10, 31))
    assert {a.reference.split(" line")[0] for a in found} >= {"PO-70", "PO-71", "PO-72", "PO-74"}
    assert not any(a.reference.startswith(("PO-73 ", "PO-75 ")) for a in found)  # closed, nothing received


def test_received_not_invoiced_works_out_again_only_the_pos_whose_billing_changed(tmp_path, monkeypatch):
    """D-166: each received PO's accruals are kept in each process (``accruals._PARTS``) and worked out again, its
    invoices' codings decoded, only once its lines, its billing invoices (which, their dates and PO) or those
    invoices' vendors' invoices changed, whoever changed them (another Store, plain SQL); never past a restore or a
    write under way, which may yet be undone. Each time, what working every PO out afresh gives (6.5 s of decoding at
    100,000 invoices, at every change before)."""
    from tests.core.test_kept_answers import _plain, _po_line

    store, other = Store(tmp_path / "ap.db"), Store(tmp_path / "ap.db")
    other.import_purchase_orders([_po_line(1, 4.0), _po_line(2, 1.0),
                                  {**_po_line(1, 5.0), "po_number": "PO-2", "vendor_name": "Bolt Works"}])  # fmt: skip
    acme, bolt = store.received_po_keys()
    end = dt.date(2026, 10, 31)
    decoded: list[int] = []
    real = Store.billing_codings
    monkeypatch.setattr(Store, "billing_codings", lambda self, ids: decoded.extend(ids) or real(self, ids))

    def bill(vendor: str, po: str, quantity: float, day: str = "2026-10-03") -> dict:
        line = {"line_number": 1, "description": "Bolts size 1", "quantity": quantity, "unit_price": 2.0,
                "amount": 2.0 * quantity, "predicted_gl_code": "6010"}  # fmt: skip
        return {"vendor_name": vendor, "invoice_number": f"{vendor}-{quantity}", "po_number": po,
                "invoice_date": day, "currency": "CAD", "line_items": [line], "subtotal": 2.0 * quantity,
                "grand_total": 2.1 * quantity}  # fmt: skip

    def received() -> tuple[list[tuple], list[int]]:
        decoded.clear()
        found = accruals._received_not_invoiced(store, end)
        read, kept = sorted(decoded), dict(accruals._PARTS)
        accruals._PARTS.clear()
        assert found == accruals._received_not_invoiced(store, end)  # what working every PO out afresh gives
        accruals._PARTS.update(kept)
        return [(a.reference, a.amount) for a in found], read

    a = other.add_invoice(tmp_path / "a.pdf", bill("Acme Supply", "PO-1", 4), {})
    b = other.add_invoice(tmp_path / "b.pdf", bill("Bolt Works", "PO-2", 2), {})
    assert received() == ([("PO-1 line 2", 2.0), ("PO-2 line 1", 6.0)], [a, b])
    assert received() == ([("PO-1 line 2", 2.0), ("PO-2 line 1", 6.0)], [])  # (kept)
    other.approve_invoice(b, bill("Bolt Works", "PO-2", 3), "Jordan Lee")
    assert received() == ([("PO-1 line 2", 2.0), ("PO-2 line 1", 4.0)], [b])  # Acme's PO kept
    _plain(store, "UPDATE invoices SET ai_output = ? WHERE id = ?", json.dumps(bill("Acme Supply", "PO-1", 1)), a)
    assert received() == ([("PO-1 line 1", 6.0), ("PO-1 line 2", 2.0), ("PO-2 line 1", 4.0)], [a])
    _plain(store, "UPDATE invoices SET po_key = ? WHERE id = ?", bolt, a)  # (its PO alone: no count moves)
    assert received() == ([("PO-1 line 1", 8.0), ("PO-1 line 2", 2.0), ("PO-2 line 1", 2.0)], [a, b])
    _plain(store, "UPDATE invoices SET invoice_date = '2026-11-02' WHERE id = ?", b)  # after the period end
    assert received() == ([("PO-1 line 1", 8.0), ("PO-1 line 2", 2.0), ("PO-2 line 1", 8.0)], [a, b])
    backup = store.backup_now("manual")
    _plain(store, "UPDATE invoices SET invoice_date = '2026-10-02' WHERE id = ?", b)
    assert received()[0][-1] == ("PO-2 line 1", 2.0)
    store.restore_from(backup)
    assert received() == ([("PO-1 line 1", 8.0), ("PO-1 line 2", 2.0), ("PO-2 line 1", 8.0)], [a, b])  # (afresh)
    with pytest.raises(RuntimeError), store.session(), store._conn() as conn:  # a write under way: never kept
        conn.execute("UPDATE invoices SET invoice_date = '2026-10-02' WHERE id = ?", (b,))
        assert accruals._received_not_invoiced(store, end)[-1].amount == 2.0
        raise RuntimeError("undone")
    assert received() == ([("PO-1 line 1", 8.0), ("PO-1 line 2", 2.0), ("PO-2 line 1", 8.0)], [])
    assert acme in accruals._PARTS[(str(store.path), end.isoformat())]


def test_the_month_end_page_settles_after_an_invoice_changes_but_never_after_a_po_or_the_setup_does(
    tmp_path, monkeypatch
):
    """D-166: the invoices not in the ERP yet are kept until an invoice, the tax setup, JD Edwards' G/L date or the
    batches made after the period end change (0.9 s at 100,000 invoices, at every visit before). What the page shows
    (``build(settle=)``): once an invoice changed elsewhere, worked out again at most every
    ``accruals.SETTLE_SECONDS``; a PO imported, the tax setup or the G/L date changed: another question, at once. The
    CSV (``build`` without ``settle``) always follows."""
    from ap_coder.coding.tax import EXPENSE_TO_LINE
    from ap_coder.core.store import summary
    from ap_coder.erp import jde
    from tests.core.test_kept_answers import _plain, _po_line

    clock = [1000.0]
    monkeypatch.setattr(summary, "monotonic", lambda: clock[0])
    worked: list[int] = []
    real = accruals._not_in_erp
    monkeypatch.setattr(accruals, "_not_in_erp", lambda *a: worked.append(1) or real(*a))
    store = Store(tmp_path / "ap.db")
    load_sample_setup(store)
    other = Store(store.path)
    nw = _coded(store, "northwind_ON_HST_NW-2026-0912")
    invoice = other.add_invoice(tmp_path / "a.pdf", nw, {})

    def shown(settle: float = accruals.SETTLE_SECONDS) -> tuple[set, set, int]:
        items = accruals.build(store, END, settle=settle)
        return {a.source for a in items}, {a.note for a in items if a.source == accruals.NOT_IN_ERP}, len(worked)

    review, approved = f"in review, {accruals.PROPOSED}", "approved, not exported"
    assert shown() == ({accruals.NOT_IN_ERP}, {review}, 1) == shown()
    other.approve_invoice(invoice, nw, "Jane")
    clock[0] += accruals.SETTLE_SECONDS - 1
    assert shown() == ({accruals.NOT_IN_ERP}, {review}, 1)  # changed under 30 s ago: settled
    clock[0] += 1
    assert shown() == ({accruals.NOT_IN_ERP}, {approved}, 2)
    other.set_tax_treatment("GST", EXPENSE_TO_LINE)
    assert shown()[2] == 3  # (the tax setup: at once)
    settings = jde.load(store)
    settings.gl_date = jde.GL_DATE_EXPORT
    jde.save(other, settings)
    assert shown()[2] == 4  # (the G/L date: at once)
    other.import_purchase_orders([_po_line(1, 4.0)])
    assert shown()[0] == {accruals.RECEIVED, accruals.NOT_IN_ERP}  # (a PO: at once)
    _plain(store, "UPDATE invoices SET status = 'rejected' WHERE id = ?", invoice)
    assert shown()[1] == {approved}  # settled
    assert shown(settle=0)[1] == set()  # the CSV: always what reading again gives


def test_default_period_end():
    assert accruals.default_period_end(dt.date(2026, 10, 3)) == dt.date(2026, 9, 30)
    assert accruals.default_period_end(dt.date(2026, 10, 20)) == dt.date(2026, 10, 31)
    assert accruals.default_period_end(dt.date(2026, 12, 15)) == dt.date(2026, 12, 31)
