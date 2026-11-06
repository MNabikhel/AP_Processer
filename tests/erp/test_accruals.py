"""Month-end accruals: received not invoiced, invoices not in the ERP, expected recurring invoices."""

import datetime as dt
import json
import sqlite3

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


def test_an_invoice_whose_g_l_date_is_moved_past_the_period_end_is_accrued_in_its_month(tmp_path):
    """B-1883: with September closed in JD Edwards (the open period from 1 October, closed periods moved, D-182), an
    invoice dated 14 September exported on 2 October goes with the G/L date 1 October, yet month end took it as in
    September's ledger ("with the invoice date as its G/L date a voucher exported later lands in its own month"): in
    neither September's ledger nor its accruals. A batch made after the period end is late then, as with the export
    day as the G/L date; one made by the period end, or with the open period from within it, is not."""
    from ap_coder.erp import jde, jde_post

    store = Store(tmp_path / "a.db")
    load_sample_setup(store)
    jde.save(store, jde.JdeSettings(default_bu="CC100", bu_from_cost_center=False, open_from="2026-10-01",
                                    closed_period=jde.CLOSED_MOVE))  # fmt: skip
    nw = _coded(store, "northwind_ON_HST_NW-2026-0912")
    store.import_vendor_master([{"vendor_name": nw["vendor_name"], "erp_id": "4001"}])
    a = store.add_invoice(tmp_path / "a.pdf", nw, {})
    store.approve_invoice(a, nw, "Jane")
    batch = store.create_export_batch([a], jde.FORMAT)

    def made_on(when):
        with store._conn() as conn:
            conn.execute("UPDATE export_batches SET created_at = ? WHERE id = ?", (when, batch))
        return [x for x in accruals.build(store, END) if f"(#{a})" in x.reference]

    late = made_on("2026-10-02T09:15:00")
    assert jde.from_julian(jde_post.payloads(store, batch)[0][a]["pay_items"][0]["VLDGJ"]) == dt.date(2026, 10, 1)
    assert late and {x.note for x in late} == {accruals.EXPORTED_LATE}
    assert made_on("2026-09-30T16:40:00") == []  # made in September, while it was open: in September's ledger
    made_on("2026-10-02T09:15:00")
    jde.save(store, jde.JdeSettings(default_bu="CC100", bu_from_cost_center=False, open_from="2026-09-01"))
    assert not [x for x in accruals.build(store, END) if f"(#{a})" in x.reference]  # September open: not moved


def test_an_exported_invoice_whose_jde_voucher_is_not_posted_yet_is_not_in_the_erp(tmp_path, monkeypatch):
    """B-1862 (R4): an invoice in an export batch counted as in the ERP at month end even when its voucher, sent
    through the JD Edwards API, was waiting in the queue (JD Edwards down), sent with its answer lost, or refused: its
    cost was in neither JD Edwards nor the accruals. Until JD Edwards has it, it is accrued, saying why; a batch loaded
    from its files is in the ERP as before."""
    from ap_coder.erp import jde_ais, jde_post
    from tests.erp.test_jde import QC
    from tests.erp.test_jde_post import batch_of
    from tests.fake_ais import PASSWORD, FakeAis

    monkeypatch.setattr(jde_ais, "RETRY_PAUSE", 0)
    monkeypatch.setenv(jde_ais.PASSWORD_ENV, PASSWORD)

    def accrued() -> dict[int, set[str]]:
        found: dict[int, set[str]] = {}
        for x in accruals.build(store, dt.date(2026, 10, 31)):
            if x.source == accruals.NOT_IN_ERP:
                found.setdefault(int(x.reference.split("(#")[1].rstrip(")")), set()).add(x.note)
        return found

    with FakeAis() as ais:
        store, batch = batch_of(tmp_path, ais, "northwind_ON_HST_NW-2026-0912", QC)  # 14 September, 1 October
        assert accrued() == {}  # exported: in the ERP, as a batch loaded from its files
        jde_post.dry_run(store, batch)
        assert accrued() == {}  # a dry run sends nothing
        ais.script("voucher_create", 503, 503, 503)  # JD Edwards busy: both wait in the queue
        jde_post.post(store, batch)
        assert accrued() == {1: {"exported; its JD Edwards voucher: Waiting: JD Edwards not reached"},
                             2: {"exported; its JD Edwards voucher: Waiting: JD Edwards not reached"}}  # fmt: skip
        ais.data["suppliers"] = [s for s in ais.data["suppliers"] if s["an8"] != "4001"]  # refused from here on
        jde_post.retry_queue(store)
        assert accrued() == {1: {"exported; its JD Edwards voucher: Refused by JD Edwards"},
                             2: {"exported; its JD Edwards voucher: Refused by JD Edwards"}}  # fmt: skip
        jde_post.file_downloaded(store, batch, "sam")  # the refused ones loaded from the batch's files instead
        assert accrued() == {}


def _api_accrued(store) -> set[int]:
    """The invoices accrued at 31 October as not in the ERP."""
    return {int(x.reference.split("(#")[1].rstrip(")")) for x in accruals.build(store, dt.date(2026, 10, 31))
            if x.source == accruals.NOT_IN_ERP}  # fmt: skip


@pytest.fixture
def _quick_ais(monkeypatch):
    from ap_coder.erp import jde_ais
    from tests.fake_ais import PASSWORD

    monkeypatch.setattr(jde_ais, "RETRY_PAUSE", 0)
    monkeypatch.setenv(jde_ais.PASSWORD_ENV, PASSWORD)


def test_a_voucher_held_back_then_loaded_from_the_batch_files_is_not_accrued(tmp_path, _quick_ais):
    """B-1882: JD Edwards refused one voucher as in a closed period, so the other of the same month was held back for
    a new dry run, its go-ahead kept; the batch's files were downloaded (it is in them: neither was locked) and
    loaded, yet month end still counted the held-back one as not in the ERP: in JD Edwards and accrued, twice. A
    download takes the go-ahead off every voucher the files hold."""
    from ap_coder.erp import jde_post
    from tests.erp.test_jde import BC, QC
    from tests.erp.test_jde_post import batch_of
    from tests.fake_ais import FakeAis

    with FakeAis() as ais:
        store, batch = batch_of(tmp_path, ais, QC, BC)  # 1 and 5 October
        jde_post.dry_run(store, batch)
        ais.script("voucher_create", ("refuse", "0005", "Error: G/L date is in a closed period (PBCO)"))
        jde_post.post(store, batch)
        assert [p["status"] for p in jde_post.postings(store, batch)] == [jde_post.FAILED, jde_post.CHECKED]
        assert _api_accrued(store) == {1, 2}  # neither in JD Edwards
        jde_post.file_downloaded(store, batch, "sam")  # both in the files, loaded into JD Edwards
        assert _api_accrued(store) == set()
        assert jde_post.left_out_of_files(store, batch) == {}
        jde_post.dry_run(store, batch)  # and a dry run afterwards changes nothing of that
        assert _api_accrued(store) == set()


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
    """D-166: each received PO's accruals are kept in each process (``accruals._PARTS``) and worked out again only
    once its lines, its billing invoices (which, their dates and PO) or those invoices' vendors' invoices changed,
    whoever changed them (another Store, plain SQL); never past a restore or a write under way, which may yet be
    undone. Of its billing invoices only those whose coding or date changed since are decoded again (D-177: an invoice
    only moved to another PO is not). Each time, what working every PO out afresh gives (6.5 s of decoding at 100,000
    invoices, at every change before)."""
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
        read = sorted(decoded)
        accruals._PARTS.clear()
        _plain(store, "DELETE FROM report_answers")  # (nothing kept for any process either)
        assert found == accruals._received_not_invoiced(store, end)  # what working every PO out afresh gives
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
    assert received() == ([("PO-1 line 1", 8.0), ("PO-1 line 2", 2.0), ("PO-2 line 1", 2.0)], [])  # (its lines kept)
    _plain(store, "UPDATE invoices SET invoice_date = '2026-11-02' WHERE id = ?", b)  # after the period end
    assert received() == ([("PO-1 line 1", 8.0), ("PO-1 line 2", 2.0), ("PO-2 line 1", 8.0)], [b])
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


def test_received_not_invoiced_are_of_one_moment_though_another_worker_approves_while_they_are_read(
    tmp_path, monkeypatch
):
    """B-1818: the invoices billing each received PO were read (the summary) a moment before their codings, so another
    worker's approval between the two that moved an invoice to another PO with other lines gave accruals of no one
    moment: its new lines on its old PO, its new PO billed by nothing. The POs, the invoices billing them and their
    codings are read in one moment."""
    import threading

    from tests.core.test_kept_answers import _po_line

    store, other = Store(tmp_path / "ap.db"), Store(tmp_path / "ap.db")
    nuts = {**_po_line(1, 5.0), "po_number": "PO-2", "description": "Hex nuts", "unit_price": 1.0, "amount": 10.0}
    other.import_purchase_orders([_po_line(1, 4.0), nuts])
    end = dt.date(2026, 10, 31)

    def bill(po: str, what: str, quantity: float, price: float) -> dict:
        line = {"line_number": 1, "description": what, "quantity": quantity, "unit_price": price,
                "amount": quantity * price, "predicted_gl_code": "6010"}  # fmt: skip
        total = quantity * price
        return {"vendor_name": "Acme Supply", "invoice_number": "A-1", "po_number": po, "invoice_date": "2026-10-03",
                "currency": "CAD", "line_items": [line], "subtotal": total, "grand_total": total}  # fmt: skip

    invoice = other.add_invoice(tmp_path / "a.pdf", bill("PO-1", "Bolts size 1", 4, 2.0), {})

    def received() -> list[tuple]:
        return [(a.reference, a.amount) for a in accruals._received_not_invoiced(store, end)]

    before = received()
    assert before == [("PO-2 line 1", 5.0)]
    with sqlite3.connect(store.path) as conn:
        conn.execute("DELETE FROM report_answers")  # (nothing kept: every invoice read again)
    accruals._PARTS.clear()
    real = Store.billing_codings

    def racing(self, ids):  # another worker's approval, just before the codings are read
        if self is store and not raced:
            raced.append(1)
            worker = threading.Thread(target=other.approve_invoice,
                                      args=(invoice, bill("PO-2", "Hex nuts", 5, 1.0), "Jordan Lee"))  # fmt: skip
            worker.start()
            worker.join()
        return real(self, ids)

    raced: list[int] = []
    monkeypatch.setattr(Store, "billing_codings", racing)
    assert received() == before and raced  # (as it was when the read began)
    assert received() == [("PO-1 line 1", 8.0)]  # (and after the approval)


def test_after_one_invoice_of_a_big_vendor_changes_another_process_decodes_that_invoice_alone(tmp_path, monkeypatch):
    """D-177: any change to one invoice of a vendor had month-end decode again every invoice billing its POs (those
    on no PO: 23,000 for the biggest vendor at 300,000 invoices), in whichever process worked it out. What month-end
    reads of each is kept for every process by vendor: another process, with nothing of its own kept, decodes only the
    invoice changed; the vendor's row is written again once enough were decoded since (``BILLING_REWRITE``)."""
    from ap_coder.core.store import purchasing
    from tests.core.test_kept_answers import _plain, _po_line, _Processes

    monkeypatch.setattr(purchasing, "BILLING_REWRITE", 4)
    process = _Processes(monkeypatch)
    store, other = Store(tmp_path / "ap.db"), Store(tmp_path / "ap.db")
    other.import_purchase_orders([{**_po_line(1, 500.0), "quantity": 500.0}])
    end = dt.date(2026, 10, 31)
    line = {"line_number": 1, "description": "Bolts size 1", "quantity": 1, "unit_price": 2.0, "amount": 2.0}
    doc = {"vendor_name": "Acme Supply", "po_number": "", "invoice_date": "2026-10-03", "currency": "CAD",
           "line_items": [line], "subtotal": 2.0, "grand_total": 2.1}  # fmt: skip
    bills = [other.add_invoice(tmp_path / f"{n}.pdf", {**doc, "invoice_number": f"A-{n}"}, {}) for n in range(12)]
    decoded: list[int] = []
    real = Store.billing_codings
    monkeypatch.setattr(Store, "billing_codings", lambda self, ids: decoded.extend(ids) or real(self, ids))

    def received(name: str) -> list[tuple]:
        process(name)
        decoded.clear()
        return [(a.reference, a.amount) for a in accruals._received_not_invoiced(store, end)]

    def billing_two(invoice: int) -> None:
        _plain(store, "UPDATE invoices SET ai_output = json_set(ai_output, '$.line_items[0].quantity', 2) WHERE id = ?",
               invoice)  # fmt: skip

    assert received("worker 1") == [("PO-1 line 1", 976.0)] and sorted(decoded) == bills  # (488 left at 2.00)
    for n, invoice in enumerate(bills[: purchasing.BILLING_REWRITE]):
        billing_two(invoice)
        assert received(f"worker {n + 2}") == [("PO-1 line 1", 976.0 - 2 * (n + 1))]
        assert decoded == bills[: n + 1]  # (those changed since the vendor's row was written: one, then two, ...)
    billing_two(bills[-1])  # (its row written again with the last of them)
    assert received("one more worker") == [("PO-1 line 1", 976.0 - 2 * (purchasing.BILLING_REWRITE + 1))]
    assert decoded == [bills[-1]]


def test_the_month_end_page_settles_after_an_invoice_changes_but_never_after_a_po_or_the_setup_does(
    tmp_path, monkeypatch
):
    """D-166: the invoices not in the ERP yet are kept until an invoice, the tax setup, JD Edwards' G/L date or the
    batches made after the period end change (0.9 s at 100,000 invoices, at every visit before). What the page shows
    (``build(settle=)``): once an invoice changed elsewhere, worked out again at most every
    ``accruals.SETTLE_SECONDS``; a PO imported, the tax setup or the G/L date changed: another question, at once. The
    CSV (``build`` without ``settle``) always follows."""
    from ap_coder.coding.tax import EXPENSE_TO_LINE
    from ap_coder.core.store import reports, summary
    from ap_coder.erp import jde
    from tests.core.test_kept_answers import _plain, _po_line

    clock = [1000.0]
    monkeypatch.setattr(summary, "monotonic", lambda: clock[0])
    monkeypatch.setattr(reports, "wall", lambda: clock[0])  # (worked out once for every process, D-169)
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


_VENDORS = ("Acme Supply", "Bolt Works", "Cog Ltd")
_ITEMS = (("Bolts size 1", 2.0), ("Bolts size 2", 2.5), ("Washers", 0.1), ("Hex nuts", 1.0), ("Delivery", 25.0))
_ENDS = (dt.date(2026, 9, 30), dt.date(2026, 10, 31))
_CHANGES = ("add", "approve", "lines", "date", "coding date", "vendor", "no vendor", "po", "delete", "export",
            "undo export", "park", "reject", "reopen", "pos", "older", "undone", "restore")  # fmt: skip


def _orders(rng) -> list[dict]:
    """Four POs of three vendors (two of Acme's), three lines each, some received."""
    rows = []
    for number, vendor in (("PO-1", "Acme Supply"), ("PO-2", "Acme Supply"), ("PO-3", "Bolt Works"),
                           ("PO-4", "Cog Ltd")):  # fmt: skip
        for n, (what, price) in enumerate(rng.sample(_ITEMS, 3), 1):
            rows.append({"po_number": number, "vendor_name": vendor, "line_number": n, "description": what,
                         "quantity": 20.0, "unit_price": price, "amount": 20 * price,
                         "received_quantity": rng.choice((None, 4.0, 12.0, 20.0)), "gl_code": "6010",
                         "cost_center": "", "status": "open", "amount_only": False, "open_amount": None})  # fmt: skip
    return rows


def _month_bill(rng, n: int, vendor: str | None = None) -> dict:
    """An invoice of one of the vendors, on one of the POs or none, its lines some of the POs' (as they are, alike,
    at another price, a credit), dated in either month, after both or not at all."""
    lines = []
    for k, (what, price) in enumerate(rng.sample(_ITEMS, rng.randint(1, 3)), 1):
        quantity, price = rng.choice((1.0, 2.0, 3.5, 0.1, -1.0)), price * rng.choice((1.0, 1.01, 1.5))
        lines.append({"line_number": k, "description": rng.choice((what, what.lower(), f"{what} checked")),
                      "quantity": quantity, "unit_price": price, "amount": round(quantity * price, 2),
                      "predicted_gl_code": "6010"})  # fmt: skip
    return {"vendor_name": vendor or rng.choice(_VENDORS), "invoice_number": f"M-{n}",
            "po_number": rng.choice(("PO-1", "PO-2", "PO-3", "", "")), "currency": "CAD", "line_items": lines,
            "invoice_date": rng.choice(("2026-09-12", "2026-10-03", "2026-11-02", "")), "subtotal": 10.0,
            "grand_total": 10.5}  # fmt: skip


def _month_change(rng, writer: Store, tmp_path, n: int, backup, keep: int) -> str:
    """One change, picked at random among the ways what month-end reads of an invoice changes: through a Store (either
    worker's) or plain SQL, an older AP Coder's (no log), a write undone, a restore."""
    import re

    from ap_coder.coding.po import po_key
    from ap_coder.core.memory import vendor_key
    from ap_coder.core.store.summary import SUMMARY_TRIGGERS
    from tests.core.test_vendor_changes import _LOGGING, _short_log

    path = writer.path

    def ids(*statuses: str) -> list[int]:
        with sqlite3.connect(path) as conn:
            return [r[0] for r in conn.execute("SELECT id FROM invoices WHERE status IN (SELECT value FROM "
                                               "json_each(?)) ORDER BY id", (json.dumps(statuses),))]  # fmt: skip

    def plain(sql: str, *args) -> None:
        with sqlite3.connect(path) as conn:
            conn.execute(sql, args)

    # A coding's value changed, the approved one if there is one, else AP Coder's.
    coding = ("UPDATE invoices SET final_output = CASE WHEN json_valid(final_output) THEN json_set(final_output, "
              "{0}) ELSE final_output END, ai_output = CASE WHEN json_valid(final_output) THEN ai_output ELSE "
              "json_set(ai_output, {0}) END WHERE id = ?2")  # fmt: skip
    review, active = ids("review"), ids("review", "parked", "pending_approval", "approved")
    action = rng.choices(_CHANGES, [0.3 if a in ("restore", "older") else 1 for a in _CHANGES])[0]
    if action == "add" or not review:
        writer.add_invoice(tmp_path / f"m-{n}.pdf", _month_bill(rng, n), {})
        return "add"
    invoice, any_one = rng.choice(review), rng.choice(active or review)
    if action == "approve":
        writer.approve_invoice(invoice, _month_bill(rng, n, writer.get_invoice(invoice)["vendor_name"]), "Jordan Lee")
    elif action == "lines":
        plain(coding.format("'$.line_items[0].quantity', ?1"), rng.choice((0.5, 4.0, -2.0)), any_one)
    elif action == "date":  # its own date, or none: its coding's then
        plain("UPDATE invoices SET invoice_date = ? WHERE id = ?", rng.choice(("2026-09-30", "2026-11-01", None)),
              any_one)  # fmt: skip
    elif action == "coding date":
        plain(coding.format("'$.invoice_date', ?1"), rng.choice(("2026-09-01", "2026-10-31", "2026-12-01")), any_one)
    elif action == "vendor":  # another vendor's now
        name = rng.choice(_VENDORS)
        plain("UPDATE invoices SET vendor_name = ?, vendor_key = ? WHERE id = ?", name, vendor_key(name), any_one)
    elif action == "no vendor":  # (whose changes no log follows)
        plain("UPDATE invoices SET vendor_key = NULL WHERE id = ?", any_one)
    elif action == "po":  # on another PO, or none (its PO alone: not logged)
        plain("UPDATE invoices SET po_key = ? WHERE id = ?", po_key(rng.choice(("PO-1", "PO-2", "PO-3", ""))),
              any_one)  # fmt: skip
    elif action == "delete":
        writer.delete_invoice(invoice, actor="Ann")
    elif action == "export":
        with sqlite3.connect(path) as conn:
            ready = [r[0] for r in conn.execute("SELECT id FROM invoices WHERE status = 'approved' AND export_batch "
                                                "IS NULL")]  # fmt: skip
        if ready:
            writer.create_export_batch(rng.sample(ready, min(2, len(ready))), "csv", "Ann")
    elif action == "undo export":
        with sqlite3.connect(path) as conn:
            batches = [r[0] for r in conn.execute("SELECT DISTINCT export_batch FROM invoices WHERE export_batch > 0")]
        if batches:
            writer.undo_export_batch(rng.choice(batches), "Ann")
    elif action == "park":
        if ids("parked") and rng.random() < 0.5:
            writer.unpark_invoice(rng.choice(ids("parked")), "Ann")
        else:
            writer.park_invoice(invoice, "Ann", "waiting for the PO")
    elif action == "reject":
        writer.reject_invoice(invoice, "Ann", "a copy")
    elif action == "reopen" and ids("rejected"):
        writer.reopen(rng.choice(ids("rejected")), "Ann", "again")
    elif action == "pos":  # the POs imported again: other lines, other quantities received
        writer.import_purchase_orders(_orders(rng))
    elif action == "older":  # an older AP Coder's triggers, counting only (B-1811), then this one's again
        with sqlite3.connect(path) as conn:
            for name in _LOGGING:
                conn.execute(f"DROP TRIGGER {name}")
                conn.execute(re.sub(r"; INSERT INTO vendor_changes .*? - \d+", "", SUMMARY_TRIGGERS[name]))
        plain(coding.format("'$.line_items[0].quantity', ?1"), rng.choice((0.5, 4.0)), any_one)
        _short_log(path, keep)
    elif action == "undone":  # month-end asked inside a write under way, then the write undone
        with pytest.raises(RuntimeError), writer.session(), writer._conn() as conn:
            conn.execute(coding.format("'$.line_items[0].quantity', ?1"), (7.0, any_one))
            conn.execute("UPDATE invoices SET status = 'rejected' WHERE id = ?", (invoice,))
            for end in _ENDS:
                accruals.build(writer, end)
            raise RuntimeError("undone")
    elif action == "restore":
        writer.restore_from(backup)
        _short_log(path, keep)  # (the migration made the log's triggers as they are)
    return action


@pytest.mark.parametrize("seed, keep", [(1, 1000), (2, 8), (3, 1000), (4, 8)])
def test_month_ends_page_and_csv_are_what_working_them_out_afresh_gives_after_any_changes_in_two_processes(
    tmp_path, monkeypatch, seed, keep
):
    """D-177: what month-end reads of each invoice billing a received PO is kept for every process by vendor and
    brought up to date from the vendors' change log. Random changes by two workers and plain SQL (an invoice added,
    approved with other lines, its lines, date or coding's date changed, moved to another vendor, to none or to another
    PO, deleted, exported and its batch undone, parked, rejected and reopened; the POs imported again; an older AP
    Coder's change, logged nowhere; a write undone; a restore), each worker asking at either period end between them,
    with a log of ``keep`` changes (8: pruned before a worker asks): its accruals CSV, and now and then its page, byte
    for byte what working them out afresh gives (nothing kept in any process or the database), and most invoices'
    lines taken as they were kept."""
    import difflib
    import itertools
    import random

    from ap_coder.core.store import purchasing, reports
    from ap_coder.core.store import summary as summary_module
    from tests.core.test_kept_answers import _Processes
    from tests.core.test_vendor_changes import _short_log
    from tests.webclient import client_for

    clock = itertools.count(1_000_000, 100)  # (never settled: each page as it is now, its counts too)
    monkeypatch.setattr(reports, "wall", lambda: float(next(clock)))
    monkeypatch.setattr(summary_module, "monotonic", lambda: float(next(clock)))
    monkeypatch.setattr(purchasing, "BILLING_REWRITE", 3)  # (a vendor's row written again, or read as it is, often)
    rng, process = random.Random(seed), _Processes(monkeypatch)
    store, other = Store(tmp_path / "ap.db"), Store(tmp_path / "ap.db")
    other.import_purchase_orders(_orders(rng))
    for n in range(12):
        other.add_invoice(tmp_path / f"m-{n}.pdf", _month_bill(rng, n), {})
    backup = store.backup_now("manual")
    _short_log(store.path, keep)
    client, fresh, decoded, needed = client_for(store), itertools.count(), [], []
    real = Store.billing_codings
    monkeypatch.setattr(Store, "billing_codings", lambda self, ids: decoded.extend(ids) or real(self, ids))
    lines = Store.billing_lines
    monkeypatch.setattr(
        Store, "billing_lines", lambda self, rows, moment: needed.extend(rows) or lines(self, rows, moment)
    )

    def shown(end: dt.date, page: bool) -> tuple[bytes, str]:
        csv = accruals.to_csv(accruals.build(store, end), end)
        return csv, client.get(f"/month-end?end={end}").text if page else ""

    def afresh(end: dt.date, page: bool) -> tuple[bytes, str]:
        with sqlite3.connect(store.path) as conn:
            kept = conn.execute("SELECT * FROM report_answers").fetchall()
            conn.execute("DELETE FROM report_answers")
        process(f"afresh {next(fresh)}")
        found = shown(end, page)
        with sqlite3.connect(store.path) as conn:
            conn.execute("DELETE FROM report_answers")
            conn.executemany("INSERT INTO report_answers VALUES (?, ?, ?, ?, ?, ?)", kept)
        return found

    done, asked, read = [], 0, 0
    for n in range(12, 72):
        done.append(_month_change(rng, rng.choice((store, other)), tmp_path, n, backup, keep))
        for worker in rng.sample(("worker 1", "worker 2"), rng.randint(1, 2)):
            for end in rng.sample(_ENDS, rng.randint(1, 2)):
                page = rng.random() < 0.15
                process(worker)
                needed.clear(), decoded.clear()
                mine = shown(end, page)
                asked, read = asked + len(needed), read + len(decoded)
                theirs = afresh(end, page)
                changed = list(difflib.unified_diff(mine[1].splitlines(), theirs[1].splitlines(), lineterm="", n=0))
                assert mine == theirs, (seed, n, done[-3:], worker, end, changed[:12])
                assert mine[0].count(b"Received, not invoiced") or not page
    assert {"approve", "lines", "vendor", "po", "delete", "older", "restore"} <= set(done) or seed != 1
    assert read < asked / 2  # (most invoices' lines as they were kept)


def test_matching_a_line_by_its_description_alone_when_no_price_can_change_it_bills_what_each_line_matched_gave():
    """D-177: ``billed_on`` takes a line's PO line from its description alone when no price can change it (none
    resembles it enough even at the same price, or one more at another price than any other at the same), and works
    out otherwise which of the PO lines whose price may change it have the same price. Over random POs and lines
    (descriptions the same, alike or not, prices at either edge of a PO line's band; then with how alike they are
    drawn from values that tie exactly at the same price), the same quantities, to the last bit, as matching each line
    in turn (``billed_by_line``); both ways taken."""
    import random

    from ap_coder.coding import po

    rng = random.Random(7)
    words = ["Toner cartridge", "toner cartridge black", "Paper A4 case", "Paper A4", "Delivery", "Delivery charge",
             "Printer service visit", "Service visit", "Bolts size 1", "Bolts size 2", "", None]  # fmt: skip
    taken = {"description": 0, "price": 0}
    real = po._price_decides

    def watched(description, lines):
        found = real(description, lines)
        taken["description" if found[0] is not None else "price"] += 1
        return found

    for round_ in range(600):
        prices = [rng.choice((0.0, 0.1, 2.0, 25.0, 80.0, 95.0, 150.0)) for _ in range(rng.randint(1, 5))]
        order = {"lines": [{"line_number": n, "description": rng.choice(words[:-2]), "unit_price": p}
                           for n, p in enumerate(prices, 1)]}  # fmt: skip
        lines = []
        for _ in range(rng.randint(1, 30)):
            at, room = po._price_band(rng.choice(prices))
            price = rng.choice((at, at + room, at - room, at + room * 1.0001, at - room * 1.0001, at * 3, -at))
            lines.append({"description": rng.choice(words), "unit_price": price,
                          "quantity": rng.choice((1, 2, 0.1, 0.7, 3.3, -1)),
                          "amount": rng.choice((10.0, -5.0))})  # fmt: skip
        invoices = [{"coding": {"line_items": lines}}]
        with pytest.MonkeyPatch.context() as patch:
            if round_ >= 300:  # (0.25, 0.3 and 0.45 at the same price tie with 0.45, 0.5 and 0.65 exactly)
                alike: dict = {}
                patch.setattr(po, "similarity", lambda a, b, alike=alike: alike.setdefault(
                    (a, b), rng.choice((0.0, 0.25, 0.3, 0.45, 0.5, 0.65))))  # fmt: skip
            expected = po.billed_by_line(order, invoices, positive_only=True)
            patch.setattr(po, "_price_decides", watched)
            assert po.billed_on(order, po.positive_lines(invoices)) == expected
    assert taken["description"] > 200 and taken["price"] > 200
    # A tie: line 1 at the same price (0.3 + 0.2) resembles it exactly as much as line 2 at another: the first wins.
    order = {"lines": [{"line_number": 1, "description": "one", "unit_price": 10.0},
                       {"line_number": 2, "description": "two", "unit_price": 50.0}]}  # fmt: skip
    invoices = [{"coding": {"line_items": [{"description": "it", "unit_price": price, "quantity": 1.0, "amount": 1.0}
                                           for price in (10.0, 50.0)]}}]  # fmt: skip
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(po, "similarity", lambda a, b: {"one": 0.3, "two": 0.5}.get(b, 0.0))
        assert po.billed_on(order, po.positive_lines(invoices)) == {1: 1.0, 2: 1.0}
        assert po.billed_by_line(order, invoices, positive_only=True) == {1: 1.0, 2: 1.0}


def test_default_period_end():
    assert accruals.default_period_end(dt.date(2026, 10, 3)) == dt.date(2026, 9, 30)
    assert accruals.default_period_end(dt.date(2026, 10, 20)) == dt.date(2026, 10, 31)
    assert accruals.default_period_end(dt.date(2026, 12, 15)) == dt.date(2026, 12, 31)
