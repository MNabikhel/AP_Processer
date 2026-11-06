"""The JD Edwards sync at a company's size (ap_coder/erp/jde_sync.py, D-181) against the stand-in AIS server: every
page read however many, only what changed the next night, a sync interrupted going on where it stopped, a page a
transaction, the POs read in a few queries with JDE's line numbers, voided vouchers out of the register, and the
register found by the supplier's number."""

from __future__ import annotations

import datetime as dt
import sqlite3

import pytest

from ap_coder.coding.vendors import vendor_findings
from ap_coder.core import store as store_mod
from ap_coder.core.store import Store
from ap_coder.erp import jde_ais, jde_sync
from tests.erp.test_jde_sync import _coding, connect, quick  # noqa: F401  (the fixture that pauses nothing)
from tests.fake_ais import FakeAis

TODAY = dt.date.today()
LONG_AGO = "2020-01-01"  # what a row's ``_changed`` says when it has not changed since the first sync


@pytest.fixture
def ais():
    with FakeAis() as fake:
        yield fake


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "ap_coder.db")


def _day(days_ago: int) -> str:
    return (TODAY - dt.timedelta(days=days_ago)).isoformat()


def _vouchers(ais, count: int, days_ago: int = 3, start: int = 0, **values) -> list[dict]:
    base = ais.data["vouchers"][0]
    made = [{**base, "key": f"00001-PV-{start + i:06d}-001", "document": str(500000 + start + i),
             "invoice_number": f"B-{start + i}", "invoice_date": _day(days_ago), "gross": 50.0 + i, **values}
            for i in range(count)]  # fmt: skip
    ais.data["vouchers"] += made
    return made


def _numbers(store) -> set[str]:
    return {r["invoice_number"] for r in store.erp_register()}


def test_more_than_two_hundred_pages_of_vouchers_are_all_read(store, ais):
    """B-1850: the sync read at most 200 pages of a part (100,000 rows at 500 a page) and then failed it: at 25,000
    invoices a month, 730 days of vouchers are about 600,000, so the voucher sync failed every night and the
    register the duplicate checks fall back on went stale (as would a chart of more than 100,000 accounts)."""
    connect(store, ais)
    ais.mapping["ais"]["page_size"] = 1
    _vouchers(ais, 205)
    report = jde_sync.sync(store, parts=("vouchers",), client=jde_ais.AisClient(ais.settings(), ais.mapping))
    assert report["ok"], report["errors"]
    assert len(_numbers(store)) == 206 and report["parts"]["vouchers"]["read"] == 206


def test_the_next_night_reads_only_what_changed(store, ais, monkeypatch):
    """B-1851: every part was read whole every night (no ChangedSince sent, though the suppliers' orchestration takes
    it; the vouchers of the last 730 days), so the nightly sync grew with JDE, not with what changed in a day. The
    next night asks for what changed since the last good read began (from a day before), and the vouchers of the
    last weeks whole; a week on, everything again."""
    for part in ("suppliers", "gl_accounts", "purchase_orders", "vouchers"):
        for row in ais.data[part]:
            row["_changed"] = LONG_AGO
    _vouchers(ais, 3, days_ago=200, _changed=LONG_AGO)
    connect(store, ais)
    first = jde_sync.sync(store)
    assert first["ok"] and first["parts"]["suppliers"]["read"] == 2
    ais.data["suppliers"][1].update(hold_code="", _changed=_day(0))  # released in JDE today
    _vouchers(ais, 1, days_ago=300, start=100, _changed=_day(0))  # an old bill entered today
    asked = len(ais.calls)
    report = jde_sync.sync(store)
    assert report["ok"], report["errors"]
    since = (TODAY - dt.timedelta(days=1)).isoformat()
    calls = {path.rsplit("/", 1)[-1]: inputs for path, inputs in ais.calls[asked:] if "/orchestrator/" in path}
    for name in ("APC_Suppliers_Read", "APC_Accounts_Read", "APC_OpenPOs_Read"):
        assert calls[name]["ChangedSince"] == since, name
    parts = report["parts"]
    assert parts["suppliers"]["read"] == 1 and parts["suppliers"]["changed"] == ["Northern Courier Inc. (hold)"]
    assert parts["gl_accounts"]["read"] == 0 and parts["purchase_orders"]["read"] == 0
    asked_vouchers = [i for p, i in ais.calls[asked:] if p.endswith("/APC_Vouchers_Read")]
    # what changed in the window, then every voucher of the last weeks
    assert [(i["ChangedSince"], i["FromInvoiceDate"]) for i in asked_vouchers] == [
        (since, _day(730)), ("", _day(jde_sync.RECENT_DAYS))]  # fmt: skip
    assert parts["vouchers"]["read"] == 1 and "B-100" in _numbers(store)
    assert store.get_vendor("northern courier")["erp_hold"] == ""
    lines = jde_sync.report_lines(report)
    assert lines[0].startswith(f"Suppliers (what changed since {since}): 0 added, 1 changed"), lines
    later = dt.datetime.now() + dt.timedelta(days=jde_sync.FULL_DAYS)
    monkeypatch.setattr(jde_sync, "_now", lambda: later)
    assert jde_sync.sync(store)["parts"]["suppliers"]["read"] == 2  # a week on: everything again


def test_an_interrupted_sync_goes_on_from_the_last_page_written(store, ais):
    """B-1852: a sync that stopped (JDE down at page 150, the computer restarted) kept nothing of where it was: the
    next one read every page from the first again, and one that never got through never finished. Each page is
    written with where the read is; the next sync goes on after the last row written."""
    connect(store, ais)
    ais.mapping["ais"]["page_size"] = 2
    made = _vouchers(ais, 9)
    ais.script("vouchers", None, None, 503, 503, 503)  # the third page: AIS down, retried, given up
    client = jde_ais.AisClient(ais.settings(), ais.mapping)
    report = jde_sync.sync(store, parts=("vouchers",), client=client)
    assert set(report["errors"]) == {"vouchers"} and len(_numbers(store)) == 4
    asked = len(ais.orchestration_calls("vouchers"))
    report = jde_sync.sync(store, parts=("vouchers",), client=client)
    assert report["ok"] and report["parts"]["vouchers"]["resumed"]
    assert ais.orchestration_calls("vouchers")[asked]["AfterKey"] == made[3]["key"]  # after the fourth voucher
    assert _numbers(store) == {"INV-7001", *(v["invoice_number"] for v in made)}
    assert store.get_json(jde_sync.PROGRESS_KEY, {}) == {}  # nothing left to go on with


def test_each_page_is_written_in_a_transaction_of_its_own(store, ais):
    """B-1853: each part was written in one transaction once every page had been read (30,000 suppliers: half a
    second of the write lock here, much more on a busy server; 600,000 vouchers: seconds), holding up every approval
    meanwhile. Each page is written and committed as it comes: another connection sees it before the next is asked."""
    connect(store, ais)
    ais.mapping["ais"]["page_size"] = 2
    base = ais.data["suppliers"][0]
    ais.data["suppliers"] = [{**base, "an8": str(5000 + i), "name": f"Supplier {i} Ltd.", "tax_id": ""}
                             for i in range(6)]  # fmt: skip
    seen = []

    def look() -> None:
        with sqlite3.connect(store.path) as conn:
            seen.append(conn.execute("SELECT COUNT(*) FROM vendors WHERE in_master = 1").fetchone()[0])

    ais.script("suppliers", None, look, look)
    jde_sync.sync(store, parts=("suppliers",), client=jde_ais.AisClient(ais.settings(), ais.mapping))
    assert seen == [2, 4] and len(store.master_vendors()) == 6


def test_the_po_sync_reads_the_pos_a_page_at_a_time_with_jdes_line_numbers(store, ais, monkeypatch):
    """B-1854: the PO sync read each PO's lines before and after its import and every synced open PO again: three or
    four queries a PO (40,000 POs, 160,000 queries a night). And a split line's number (JDE's 1.500) was cut to 1:
    taken as a second line 1, it and every line after it were numbered one more than in JDE (R9)."""
    connect(store, ais)
    base = ais.data["purchase_orders"][0]
    ais.data["purchase_orders"] = [{**base, "key": f"{6000 + n}-OP-00001-{line}", "order_number": str(6000 + n),
                                    "line_number": line} for n in range(40) for line in ("1.000", "2.000")]  # fmt: skip
    ais.data["purchase_orders"].append({**base, "key": "6000-OP-00001-1.5", "order_number": "6000",
                                        "line_number": "1.500", "description": "Split"})  # fmt: skip
    seen: list[str] = []
    real = sqlite3.connect

    def tracing(*args, **kwargs):
        conn = real(*args, **kwargs)
        conn.set_trace_callback(seen.append)
        return conn

    monkeypatch.setattr(store_mod.sqlite3, "connect", tracing)
    report = jde_sync.sync(store, parts=("purchase_orders",))
    monkeypatch.undo()
    assert report["ok"] and len(report["parts"]["purchase_orders"]["added"]) == 40
    reads = [s for s in seen if s.lstrip().startswith("SELECT") and ("purchase_orders" in s or "po_lines" in s)]
    assert len(reads) < 12, len(reads)
    assert [li["line_number"] for li in store.purchase_order("6000")["lines"]] == [1, 2, 1500]
    assert [li["line_number"] for li in store.purchase_order("6001")["lines"]] == [1, 2]


def test_a_po_whose_changed_line_alone_comes_back_keeps_its_other_lines(store, ais):
    """What changed of a PO (one line received) is merged into its lines, whether JDE answers that line alone or the
    whole PO; a PO is closed by its lines at 999, or by a whole read no longer listing it."""
    for row in ais.data["purchase_orders"]:
        row["_changed"] = LONG_AGO
    connect(store, ais)
    jde_sync.sync(store, parts=("purchase_orders",))
    ais.data["purchase_orders"][1].update(received=2, _changed=_day(0))  # the toner received today, line 2 alone
    part = jde_sync.sync(store, parts=("purchase_orders",))["parts"]["purchase_orders"]
    lines = store.purchase_order("4512")["lines"]
    assert part["read"] == 1 and part["changed"] == ["4512"]
    assert [(li["line_number"], li["received_quantity"]) for li in lines] == [(1, 10), (2, 2)]
    ais.data["purchase_orders"][1].update(next_status="999", _changed=_day(0))  # line 2 closed: the PO still open
    jde_sync.sync(store, parts=("purchase_orders",))
    assert store.purchase_order("4512")["status"] == "open"


def test_a_voucher_voided_in_jde_leaves_the_register(store, ais):
    """B-1855: the vouchers were only ever added to the register: one voided in JDE stayed for good, so the bill
    entered again (or AP Coder's own invoice of it) was flagged DUPLICATE_IN_ERP against a voucher JDE no longer has
    (R7). Each night a voucher of the last weeks no longer listed leaves; an older one at the week's whole read; most
    or all at once is refused, as a wrong answer."""
    connect(store, ais)
    recent, old = _vouchers(ais, 4), _vouchers(ais, 3, days_ago=90, start=10)
    jde_sync.sync(store)
    assert {"B-0", "B-10"} <= _numbers(store)
    ais.data["vouchers"].remove(recent[0])  # voided in JDE
    ais.data["vouchers"].remove(old[0])
    report = jde_sync.sync(store)
    assert report["parts"]["vouchers"]["removed"] == 1 and "B-0" not in _numbers(store) and "B-10" in _numbers(store)
    assert store.in_erp("Acme Office Supplies Ltd.", "B-0", 50.0) == []
    assert any("1 no longer in JD Edwards" in line for line in jde_sync.report_lines(report))
    jde_sync.sync(store, full=True)  # the week's whole read
    assert _numbers(store) == {"INV-7001", "B-1", "B-2", "B-3", "B-11", "B-12"}
    ais.data["vouchers"] = []  # a wrong answer (a filter, row security): nothing removed
    part = jde_sync.sync(store, full=True)["parts"]["vouchers"]
    assert "would remove 6 of the 6" in part["refused_remove"] and len(_numbers(store)) == 6


def test_a_bill_entered_twice_in_jde_counts_both_whatever_page_each_is_on(store, ais):
    """The register's copies of a bill (the duplicate audit's "entered twice in the ERP") are its vouchers' dates,
    across pages; one of them voided, the bill counts one."""
    connect(store, ais)
    ais.mapping["ais"]["page_size"] = 2
    twice = _vouchers(ais, 1, invoice_number="TWICE", gross=75.0)
    _vouchers(ais, 4, start=1)
    _vouchers(ais, 1, start=9, days_ago=5, invoice_number="TWICE", gross=75.0)
    client = jde_ais.AisClient(ais.settings(), ais.mapping)
    jde_sync.sync(store, parts=("vouchers",), client=client)
    assert store.erp_register_repeats() == 1
    ais.data["vouchers"].remove(twice[0])
    jde_sync.sync(store, parts=("vouchers",), client=client, full=True)
    assert store.erp_register_repeats() == 0 and "TWICE" in _numbers(store)


def test_a_supplier_renamed_in_jde_has_its_vouchers_once_found_by_its_number(store, ais):
    """B-1856: the register is keyed by the vendor's name and was only ever added to: a supplier renamed in JDE (the
    same AN8) had its vouchers in it twice for good, under the old name, which no invoice of it bears any more, and
    the new (R7). Each voucher is kept by its key and moves to the new name when it is read again (the week's whole
    read); meanwhile it is found by the supplier's number."""
    for part in ("suppliers", "vouchers"):
        for row in ais.data[part]:
            row["_changed"] = LONG_AGO
    connect(store, ais)
    jde_sync.sync(store)
    renamed = "Acme Business Supplies Inc."
    ais.data["suppliers"][0].update(name=renamed, _changed=_day(0))
    ais.data["vouchers"][0]["supplier_name"] = renamed  # (JDE's name of it now; the voucher itself unchanged)
    jde_sync.sync(store)
    assert len(store.erp_register()) == 1 and store.in_erp(renamed, "INV-7001", 113.0)
    credit = _coding(vendor_name=renamed, invoice_number="CR-1", grand_total=-20.0, subtotal=-17.7, tax_total=-2.3,
                     original_invoice_number="INV-7001")  # fmt: skip
    assert "CREDIT_NOTE_ORIGINAL_UNKNOWN" not in [c for _, c, _ in vendor_findings(credit, store)]
    jde_sync.sync(store, full=True)
    assert [r["vendor_name"] for r in store.erp_register()] == [renamed]


@pytest.mark.parametrize("how", ["clear", "replace"])
def test_a_register_cleared_or_replaced_is_read_whole_again_the_next_night(store, ais, monkeypatch, how):
    """B-1893: clearing the register (Exports) or replacing it with a file deleted JD Edwards' vouchers but not the
    sync's state, so the next night read only what changed and the last 31 days: every voucher older than that was
    out of the register (the duplicate checks' fallback) until the week's whole read, six nights at most."""
    for row in ais.data["vouchers"]:
        row["_changed"] = LONG_AGO
    _vouchers(ais, 3, days_ago=100, _changed=LONG_AGO)
    connect(store, ais)
    assert jde_sync.sync(store, parts=("vouchers",))["ok"] and "B-2" in _numbers(store)
    if how == "clear":
        store.clear_erp_register(actor="ap")
    else:
        store.import_erp_register([{"vendor_name": "Other Co", "invoice_number": "X-1", "total": 5.0,
                                    "invoice_date": _day(3)}], replace_all=True, actor="ap")  # fmt: skip
    night = dt.datetime.now() + dt.timedelta(days=1)
    monkeypatch.setattr(jde_sync, "_now", lambda: night)
    part = jde_sync.sync(store, parts=("vouchers",))["parts"]["vouchers"]
    assert part["mode"] == jde_sync.FULL and {"INV-7001", "B-0", "B-1", "B-2"} <= _numbers(store)


def test_the_dates_read_are_in_the_mappings_date_format_too(store, ais, monkeypatch):
    """B-1894: the mapping's ``date_format`` (a JDE user's, %m/%d/%Y) was used for the dates sent and not for those
    read: a voucher dated 10/07/2026 (7 October) was kept as that text, which sorts before every YYYY-MM-DD date, so
    no sweep counted it, and voided in JDE it stayed in the register for good, a possible duplicate payment (one dated
    after the 12th was read by its day)."""
    monkeypatch.setattr(FakeAis, "_listed", staticmethod(lambda key, r, given: True))  # (it compares dates as text)
    ais.mapping["ais"]["date_format"] = "%m/%d/%Y"
    connect(store, ais)
    _vouchers(ais, 4, invoice_date="10/20/2026")
    void = _vouchers(ais, 1, start=9, invoice_date="10/07/2026")[0]
    client = jde_ais.AisClient(ais.settings(), ais.mapping)
    jde_sync.sync(store, parts=("vouchers",), client=client)
    assert store.in_erp("Acme Office Supplies Ltd.", "B-9", 50.0)[0]["invoice_date"] == "2026-10-07"
    ais.data["vouchers"].remove(void)  # voided in JDE
    assert jde_sync.sync(store, parts=("vouchers",), client=client, full=True)["parts"]["vouchers"]["removed"] == 1
    assert "B-9" not in _numbers(store) and "B-0" in _numbers(store)


def test_a_voucher_without_a_date_voided_in_jde_leaves_at_the_whole_read(store, ais):
    """B-1895: a read's sweep counted the vouchers dated in its days, so one with no invoice date (or one that could
    not be read) never left: voided in JDE, it stayed in the register for good. The whole read of the window removes
    those it no longer lists too (the nightly read of the last weeks leaves them be)."""
    connect(store, ais)
    _vouchers(ais, 4)
    undated = _vouchers(ais, 1, start=9, invoice_date="")[0]
    jde_sync.sync(store, parts=("vouchers",))
    assert "B-9" in _numbers(store)
    ais.data["vouchers"].remove(undated)  # voided in JDE
    assert jde_sync.sync(store, parts=("vouchers",), full=True)["parts"]["vouchers"]["removed"] == 1
    assert "B-9" not in _numbers(store) and "B-0" in _numbers(store)


def test_a_po_whose_lines_jde_closes_on_different_days_closes_with_its_last(store, ais):
    """B-1896: a PO's lines here have no status of their own, and the lines a read of what changed kept took the PO's,
    open: a PO whose first line JDE closed one day and its last another stayed open here until the week's whole
    read. A line JDE closed (999) leaves its PO's lines while another is open, as a whole read lists the open lines
    alone; the last one closed closes the PO."""
    for row in ais.data["purchase_orders"]:
        row["_changed"] = LONG_AGO
    connect(store, ais)
    jde_sync.sync(store, parts=("purchase_orders",))
    first, last = ais.data["purchase_orders"]
    first.update(next_status=jde_sync.CLOSED_STATUS, _changed=_day(0))  # closed in JDE
    jde_sync.sync(store, parts=("purchase_orders",))
    po = store.purchase_order("4512")
    assert po["status"] == "open" and [li["line_number"] for li in po["lines"]] == [2]
    first["_changed"] = LONG_AGO  # (a later day: changed before the read's ChangedSince)
    last.update(next_status=jde_sync.CLOSED_STATUS, _changed=_day(0))
    assert jde_sync.sync(store, parts=("purchase_orders",))["parts"]["purchase_orders"]["mode"] == jde_sync.CHANGES
    assert store.purchase_order("4512")["status"] == "closed"


def test_a_sync_whose_lease_another_took_stops_there(store, ais):
    """B-1898: a sync that waited on JD Edwards longer than its lease (a page slower than 15 minutes: AIS's timeout up
    to 300 s, three tries) never noticed another had taken over (``renew_lease``'s answer ignored): both wrote, and
    the older one's stamp on the vouchers both listed let the newer one's sweep remove vouchers JDE still holds. A
    sync that can't renew its lease stops there, the page undone, the other's lease left be (and a voucher keeps the
    newest stamp it was listed with: ``tests/core/test_store_register.py``)."""
    connect(store, ais)
    ais.mapping["ais"]["page_size"] = 2
    _vouchers(ais, 5)

    def taken() -> None:  # meanwhile this one's lease ran out and another sync took it
        store.drop_lease(jde_sync.LEASE, None)
        assert store.take_lease(jde_sync.LEASE, "another", jde_sync.LEASE_SECONDS)

    ais.script("vouchers", None, taken)
    report = jde_sync.sync(store, parts=("vouchers",), client=jde_ais.AisClient(ais.settings(), ais.mapping))
    assert "another sync took over" in report["errors"]["vouchers"] and _numbers(store) == {"B-0", "B-1"}
    assert not store.take_lease(jde_sync.LEASE, "a third", 1)  # the other sync's lease kept


def test_an_orchestration_giving_the_same_page_again_never_switches_the_live_check_off(store, ais, monkeypatch,
                                                                                       caplog):  # fmt: skip
    """B-1899: the vouchers' orchestration giving the same page again (its After… input ignored) was taken by the
    live check as JDE down: a supplier with more than a page of vouchers got JDE_NOT_REACHED, and the check was off
    for every invoice for a minute. It is the live check's question too wide: asked by the invoice number instead,
    and said in the log."""
    connect(store, ais)
    jde_sync.sync(store, parts=("suppliers",))
    _vouchers(ais, int(ais.mapping["ais"]["page_size"]))  # Acme's: more than a page with INV-7001
    real = ais._after
    monkeypatch.setattr(ais, "_after", lambda key, after: real(key, ""))  # the first page, whatever is asked after
    found = jde_sync.live_findings(store, _coding())
    assert [c for _, c, _ in found] == ["DUPLICATE_IN_ERP"] and not jde_sync._DOWN
    assert "gave the same page again" in caplog.text


def test_an_ais_giving_fewer_rows_a_page_than_asked_is_read_to_the_end_first(store, ais, monkeypatch):
    """B-1902: a page shorter than MaxRows was taken as the last, and since D-181 that decides what leaves: an AIS
    whose own maximum rows is below the page size (Server Manager) ended the read at its first page, and the sweep
    removed the vouchers past it (fewer than half of them: not refused). A read that removes or closes what it no
    longer lists asks again after a page as long as the longest before it, to an empty or a shorter one."""
    connect(store, ais)
    _vouchers(ais, 7)
    jde_sync.sync(store, parts=("vouchers",))
    real = ais._inputs
    monkeypatch.setattr(ais, "_inputs", lambda key, body: {**real(key, body), "max_rows": 5})  # AIS's maximum: 5
    part = jde_sync.sync(store, parts=("vouchers",), full=True)["parts"]["vouchers"]
    assert part["read"] == 8 and part["removed"] == 0 and len(_numbers(store)) == 8
    base = ais.data["purchase_orders"][0]  # the POs a whole read no longer lists are closed: the same
    ais.data["purchase_orders"] += [{**base, "key": f"{6000 + n}-OP-00001-1", "order_number": str(6000 + n)}
                                    for n in range(5)]  # fmt: skip
    monkeypatch.setattr(ais, "_inputs", real)
    jde_sync.sync(store, parts=("purchase_orders",))
    monkeypatch.setattr(ais, "_inputs", lambda key, body: {**real(key, body), "max_rows": 4})
    part = jde_sync.sync(store, parts=("purchase_orders",), full=True)["parts"]["purchase_orders"]
    assert part["read"] == 7 and part["closed"] == [] and store.purchase_order("6004")["status"] == "open"


def test_a_full_page_ending_on_a_voucher_without_its_key_removes_nothing(store, ais, monkeypatch):
    """B-1903: a full page whose last voucher came without its Key ended the read there (no key to ask the next page
    after), taken as complete, so the sweep removed every voucher past it (fewer than half: not refused). Only a read
    that ended on an empty page or a shorter one than before removes anything; this one says why it removed none."""
    connect(store, ais)
    ais.mapping["ais"]["page_size"] = 2
    _vouchers(ais, 9)
    client = jde_ais.AisClient(ais.settings(), ais.mapping)
    jde_sync.sync(store, parts=("vouchers",), client=client)
    key, real = ais.mapping["orchestrations"]["vouchers"]["fields"]["key"], ais._out
    monkeypatch.setattr(ais, "_out", lambda part, row: {**real(part, row), **({key: ""} if row.get(
        "invoice_number") == "B-5" else {})})  # the third page's last voucher, without its key  # fmt: skip
    part = jde_sync.sync(store, parts=("vouchers",), client=client, full=True)["parts"]["vouchers"]
    assert part["removed"] == 0 and {"B-6", "B-8", "INV-7001"} <= _numbers(store)
    assert "none was removed" in part["refused_remove"]


def test_a_voucher_dated_on_a_reads_first_day_stays_whether_jde_lists_that_day_or_not(store, ais, monkeypatch):
    """B-1904: the sweep counted the vouchers dated from a read's FromInvoiceDate on, the day itself too, which is
    right only if the orchestration lists that day: one that takes it as "after" left out the vouchers of the last
    weeks' first day each night, and the sweep removed them (back at the week's whole read). That day is left
    alone now, whichever way JDE takes it."""
    real = FakeAis._listed

    def after_the_day(key, r, given):  # FromInvoiceDate taken as "after"
        on_it = key == "vouchers" and given.get("since") and str(r.get("invoice_date")) == str(given["since"])
        return real(key, r, given) and not on_it

    monkeypatch.setattr(FakeAis, "_listed", staticmethod(after_the_day))
    for row in ais.data["vouchers"]:
        row["_changed"] = LONG_AGO
    _vouchers(ais, 4, _changed=LONG_AGO)
    _vouchers(ais, 1, start=9, days_ago=jde_sync.RECENT_DAYS, _changed=LONG_AGO)
    connect(store, ais)
    jde_sync.sync(store, parts=("vouchers",))
    part = jde_sync.sync(store, parts=("vouchers",))["parts"]["vouchers"]  # the next night: the last weeks' read
    assert part["mode"] == jde_sync.CHANGES and part["removed"] == 0 and "B-9" in _numbers(store)
