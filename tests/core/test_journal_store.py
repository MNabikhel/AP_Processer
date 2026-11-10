"""Last year's coding kept by the store (``core/store/journal.py``): JD Edwards' Transaction Journal as the reader
read it, every voucher and line in tables of their own, a file at a time. The journals are a made-up company's
(``ap_coder.bench.jde_company``), checked against the truth they were written from."""

from __future__ import annotations

import csv
import hashlib
import io
import time
from dataclasses import replace

import pytest

from ap_coder.bench import jde_company, jde_exports
from ap_coder.core.store import Store
from ap_coder.core.store import journal as part
from ap_coder.erp import journal as jr

AT = jde_exports.JOURNAL_POSITIONS


@pytest.fixture(scope="module")
def truth():
    return jde_company.build(1, "small")


def _read(truth, data=None):
    """(the journal as read with the company's own objects, its file's bytes)."""
    company = truth.company
    data = jde_exports.journal_csv(truth) if data is None else data
    objects = jr.PayableObjects((company.payable_object,), (company.intercompany_object,))
    return jr.read_journal(data, payable_objects=objects, tax_objects=(company.gst_object,)), data


def _load(store, truth, name="journal.csv", data=None, actor="pat"):
    journal, data = _read(truth, data)
    return store.import_journal(journal, source_name=name, source_hash=hashlib.sha256(data).hexdigest(), actor=actor)


def _csv(rows):
    text = io.StringIO(newline="")
    csv.writer(text, lineterminator="\r\n").writerows(rows)
    return text.getvalue().encode("utf-8-sig")


def _said(vouchers):
    """Vouchers as the store gives them, without what tells one load from another."""
    return [{k: v for k, v in voucher.items() if k not in ("id", "file_id", "row")}
            | {"lines": [{k: v for k, v in line.items() if k != "row"} for line in voucher["lines"]]}
            for voucher in vouchers]  # fmt: skip


def test_a_database_with_no_journal_has_the_tables_empty(store):
    assert store.journal_files() == [] and store.journal_vouchers() == [] and store.journal_suppliers() == []
    assert store.journal_lines_for("740075") == [] and store.journal_stamp() == 0
    assert store.journal_objects() == {"trade": (), "intercompany": (), "tax": ()}
    assert store.forget_journal(1) == 0 and store.forget_journals() == 0
    assert store.events(actions=["journal_imported", "journal_forgotten"]) == [] and store.journal_stamp() == 0


def test_the_kinds_are_the_readers(store):
    assert (part.CODING, part.PAYABLE, part.TAX) == (jr.CODING, jr.PAYABLE, jr.TAX)


def test_the_small_journal_is_kept_as_the_truth_has_it(store, truth):
    kinds = {number: truth.supplier(number).kind for number in {v.supplier for v in truth.vouchers}}
    affiliates = [v for v in truth.vouchers if kinds[v.supplier] == "affiliate"]
    odd = [v for v in truth.vouchers if v.oddity == "unbalanced"]
    learned = [v for v in truth.vouchers if v not in affiliates and v not in odd]
    result = _load(store, truth)
    assert result == {
        "file_id": 1, "name": "journal.csv", "loaded": True, "first_date": "2024-01-15", "last_date": "2024-12-30",
        "vouchers": len(truth.vouchers), "lines": 405, "coding_lines": sum(len(v.lines) for v in truth.vouchers),
        "payable_lines": sum(len(v.pay_items) for v in truth.vouchers),
        "tax_lines": sum(1 for v in truth.vouchers for item in v.pay_items if item.tax),
        "affiliates": len(affiliates), "unbalanced": len(odd),
        "credits": sum(1 for v in truth.vouchers if v.oddity == "negative"),
        "learned_vouchers": len(learned), "learned_lines": sum(len(v.lines) for v in learned),
        "added": len(truth.vouchers), "already": 0, "skipped": {},
    }  # fmt: skip
    assert (result["vouchers"], result["coding_lines"], result["affiliates"], result["unbalanced"]) == (120, 195, 16, 1)
    kept = store.journal_vouchers()
    assert len(kept) == len(truth.vouchers)
    for got, voucher in zip(kept, truth.vouchers, strict=True):  # the file's order: by G/L date
        supplier = truth.supplier(voucher.supplier)
        assert (got["doc_type"], got["company"], got["gl_date"]) == ("PV", "00210", voucher.gl_date.isoformat())
        assert (got["supplier_number"], got["supplier_name"]) == (str(supplier.number), supplier.name[:26].strip())
        assert got["affiliate"] is (supplier.kind == "affiliate")
        assert got["balanced"] is (voucher.oddity != "unbalanced") and got["both_columns"] is not got["balanced"]
        assert got["credit"] is (voucher.oddity == "negative")
        coding = [line for line in got["lines"] if line["kind"] == "coding"]
        sign = -1 if voucher.oddity == "unbalanced" else 1  # (printed as credits on the voucher that does not balance)
        assert [(line["account"], line["amount"], line["remark"]) for line in coding] == [
            (line.account, sign * float(line.amount), line.remark[:30]) for line in voucher.lines]  # fmt: skip
        assert got["coding_lines"] == len(voucher.lines) and [line["n"] for line in got["lines"]] == list(
            range(1, len(got["lines"]) + 1))  # fmt: skip
        if got["balanced"]:
            assert got["gross"] == float(voucher.gross) and got["tax"] == float(voucher.tax)
        own = [line for line in got["lines"] if line["kind"] != "coding"]
        payable = "2150" if got["affiliate"] else "2110"
        assert {line["object"] for line in own} <= {payable, "2420"}
        assert [line["kind"] for line in own if line["object"] == payable] == ["payable"] * len(voucher.pay_items)
        assert sum(line["kind"] == "tax" for line in own) == sum(1 for item in voucher.pay_items if item.tax)
    line = kept[0]["lines"][0]
    assert line["account"] == f"{line['business_unit']}.{line['object']}" + (
        f".{line['subsidiary']}" if line["subsidiary"] else "")  # fmt: skip
    assert line["description"] == truth.account(line["account"]).description[:24]


def test_the_file_is_listed_with_who_when_and_what_it_holds(store, truth):
    journal, data = _read(truth)
    digest = hashlib.sha256(data).hexdigest()
    objects = {"trade": ["2110"], "intercompany": ["2150"], "tax": ["2420"]}
    store.import_journal(journal, source_name="R09321 2024.csv", source_hash=digest, actor="pat", objects=objects)
    (listed,) = store.journal_files()
    assert listed["loaded_at"][:4].isdigit() and len(listed["loaded_at"]) == 19
    assert {k: v for k, v in listed.items() if k != "loaded_at"} == {
        "id": 1, "name": "R09321 2024.csv", "hash": digest, "loaded_by": "pat", "first_date": "2024-01-15",
        "last_date": "2024-12-30", "vouchers": 120, "counted": 120, "lines": 405, "coding_lines": 195,
        "payable_lines": 129, "tax_lines": 81, "affiliates": 16, "unbalanced": 1, "credits": 1,
        "learned_vouchers": 103, "learned_lines": 154, "objects": objects, "control_total_differs": False,
    }  # fmt: skip
    (event,) = store.events(actions=["journal_imported"])
    assert event["actor"] == "pat" and event["detail"] == {
        "file": "R09321 2024.csv", "file_id": 1, "first_date": "2024-01-15", "last_date": "2024-12-30",
        "vouchers": 120, "added": 120, "already": 0, "coding_lines": 195}  # fmt: skip
    assert store.journal_stamp() == 1


def test_the_same_file_again_adds_nothing(store, truth):
    first = _load(store, truth)
    before = (store.journal_stamp(), store.events_stamp(), store.journal_vouchers(), store.journal_files())
    again = _load(store, truth, name="the same, renamed.csv", actor="sam")
    assert again == {**first, "name": "the same, renamed.csv", "loaded": False, "added": 0, "already": 120,
                     "skipped": {part.SAME_FILE: 120}}  # fmt: skip
    assert (store.journal_stamp(), store.events_stamp(), store.journal_vouchers(), store.journal_files()) == before


@pytest.fixture(scope="module")
def whole(truth, tmp_path_factory):
    """The whole journal's vouchers as a store that loaded it in one file gives them."""
    store = Store(tmp_path_factory.mktemp("whole") / "ap_coder.db")
    _load(store, truth)
    return _said(store.journal_vouchers())


def test_two_files_that_overlap_count_a_voucher_once(store, truth, whole):
    first = _load(store, replace(truth, vouchers=truth.vouchers[:80]), "first.csv")
    assert (first["vouchers"], first["added"], first["already"], first["skipped"]) == (80, 80, 0, {})
    journal, _ = _read(replace(truth, vouchers=truth.vouchers[40:]))
    assert store.journal_already(journal) == 40  # known before it is loaded, nothing written
    second = _load(store, replace(truth, vouchers=truth.vouchers[40:]), "second.csv")
    assert (second["vouchers"], second["added"], second["already"]) == (80, 40, 40)
    assert second["skipped"] == {part.IN_ANOTHER_FILE: 40}
    assert _said(store.journal_vouchers()) == whole  # the year, each voucher once
    assert [(f["name"], f["vouchers"], f["counted"]) for f in store.journal_files()] == [
        ("second.csv", 80, 40), ("first.csv", 80, 80)]  # fmt: skip
    assert store.events(actions=["journal_imported"])[0]["detail"]["already"] == 40
    supplier = str(truth.vouchers[50].supplier)  # in both files
    lines = store.journal_lines_for(supplier, kinds=("coding", "payable", "tax"), include_affiliates=True,
                                    include_unbalanced=True)  # fmt: skip
    assert len(lines) == sum(len(v["lines"]) for v in whole if v["supplier_number"] == supplier)
    # The first file forgotten: what the second also holds is counted from the second, nothing of it is lost.
    stamp = store.journal_stamp()
    assert store.forget_journal(first["file_id"], actor="pat") == 80
    assert _said(store.journal_vouchers()) == whole[40:] and store.journal_stamp() == stamp + 1
    assert [(f["name"], f["counted"]) for f in store.journal_files()] == [("second.csv", 80)]
    (event,) = store.events(actions=["journal_forgotten"])
    assert event["actor"] == "pat" and event["detail"] == {"files": ["first.csv"], "vouchers": 80}
    assert store.forget_journal(first["file_id"]) == 0 and store.forget_journal(second["file_id"]) == 80
    assert store.journal_vouchers() == [] and store.journal_files() == [] and store.journal_suppliers() == []


def test_two_vouchers_alike_in_one_file_are_two_vouchers(store, truth):
    twin = truth.vouchers[3]
    twice = replace(truth, vouchers=[*truth.vouchers[:4], twin, *truth.vouchers[4:10]])
    result = _load(store, twice, "twice.csv")
    assert (result["vouchers"], result["added"], result["already"]) == (11, 11, 0)
    once = _load(store, replace(truth, vouchers=truth.vouchers[2:6]), "once.csv")
    assert (once["vouchers"], once["added"], once["already"]) == (4, 0, 4)  # the file with both keeps counting
    assert len(store.journal_vouchers()) == 11
    store.forget_journal(result["file_id"])
    assert len(store.journal_vouchers()) == 4


def test_forgetting_every_journal_empties_the_tables(store, truth):
    _load(store, replace(truth, vouchers=truth.vouchers[:30]), "a.csv")
    _load(store, replace(truth, vouchers=truth.vouchers[30:60]), "b.csv")
    assert store.forget_journals(actor="pat") == 60
    assert store.journal_files() == [] and store.journal_vouchers() == []
    assert store.journal_lines_for(str(truth.vouchers[0].supplier)) == []
    assert store.events(actions=["journal_forgotten"])[0]["detail"] == {"files": ["a.csv", "b.csv"], "vouchers": 60}
    assert store.journal_stamp() == 3 and store.forget_journals() == 0 and store.journal_stamp() == 3


def test_a_supplier_number_keeps_its_leading_zeros_and_is_found_with_or_without(store, truth):
    number = str(truth.vouchers[0].supplier)
    rows = jde_exports.journal_rows(truth)
    for row in rows:
        if len(row) > AT["supplier_number"] and row[AT["supplier_number"]] == number and "." in row[AT["account"]]:
            row[AT["supplier_number"]] = "00" + number
    _load(store, truth, data=_csv(rows))
    kept = store.journal_vouchers(supplier_number=number)
    assert kept and {v["supplier_number"] for v in kept} == {"00" + number}
    assert store.journal_vouchers(supplier_number="00" + number) == kept
    lines = store.journal_lines_for(number)
    assert lines and lines == store.journal_lines_for(f" 0000{number} ")
    assert {line["supplier_number"] for line in lines} == {"00" + number}
    assert "00" + number in {s["supplier_number"] for s in store.journal_suppliers()}
    assert part.supplier_key("00740075") == part.supplier_key(" 740075 ") == "740075"
    assert (part.supplier_key("000"), part.supplier_key(""), part.supplier_key(None)) == ("0", "", "")


def test_a_suppliers_lines_are_its_coding_by_date_with_what_the_voucher_says(store, truth):
    _load(store, truth)
    busiest = max({v.supplier for v in truth.vouchers}, key=lambda n: sum(v.supplier == n for v in truth.vouchers))
    own = [v for v in truth.vouchers if v.supplier == busiest]
    good = [v for v in own if v.oddity != "unbalanced"]
    lines = store.journal_lines_for(str(busiest))
    assert [(line["gl_date"], line["account"], line["amount"], line["remark"]) for line in lines] == [
        (v.gl_date.isoformat(), line.account, float(line.amount), line.remark[:30]) for v in good for line in v.lines
    ]
    assert {line["kind"] for line in lines} == {"coding"} and len(good) < len(own)  # one of its vouchers is left out
    by_voucher: dict[int, int] = {}
    for line in lines:
        by_voucher[line["voucher_id"]] = by_voucher.get(line["voucher_id"], 0) + 1
        assert (line["affiliate"], line["balanced"], line["company"], line["doc_type"]) == (False, True, "00210", "PV")
        assert set(line) == {"voucher_id", "n", "gl_date", "company", "doc_type", "supplier_number", "supplier_name",
                             "account", "business_unit", "object", "subsidiary", "description", "amount", "remark",
                             "kind", "voucher_coding_lines", "affiliate", "credit", "balanced"}  # fmt: skip
    assert all(line["voucher_coding_lines"] == by_voucher[line["voucher_id"]] for line in lines)
    everything = store.journal_lines_for(str(busiest), include_unbalanced=True)
    assert len(everything) == sum(len(v.lines) for v in own)
    own_lines = store.journal_lines_for(str(busiest), kinds=("payable", "tax"))
    assert {line["kind"] for line in own_lines} == {"payable", "tax"} and {line["object"] for line in own_lines} == {
        "2110", "2420"}  # fmt: skip
    # An affiliate's coding is there to be asked for, not given otherwise.
    affiliate = next(v.supplier for v in truth.vouchers if truth.supplier(v.supplier).kind == "affiliate")
    assert store.journal_lines_for(str(affiliate)) == []
    assert all(line["affiliate"] for line in store.journal_lines_for(str(affiliate), include_affiliates=True))
    assert store.journal_lines_for("999999999") == []


def test_the_suppliers_and_the_vouchers_of_a_period(store, truth):
    _load(store, truth)
    suppliers = store.journal_suppliers()
    numbers = {v.supplier for v in truth.vouchers}
    assert {s["supplier_number"] for s in suppliers} == {str(n) for n in numbers}
    assert [s["vouchers"] for s in suppliers] == sorted((s["vouchers"] for s in suppliers), reverse=True)
    for s in suppliers:
        own = [v for v in truth.vouchers if str(v.supplier) == s["supplier_number"]]
        supplier = truth.supplier(own[0].supplier)
        learned = [] if supplier.kind == "affiliate" else [v for v in own if v.oddity != "unbalanced"]
        assert (s["supplier_name"], s["vouchers"]) == (supplier.name[:26].strip(), len(own))
        assert s["affiliate_vouchers"] == (len(own) if supplier.kind == "affiliate" else 0)
        assert s["coding_lines"] == sum(len(v.lines) for v in learned)
        assert s["accounts"] == len({line.account for v in learned for line in v.lines})
        assert (s["first_date"], s["last_date"]) == (own[0].gl_date.isoformat(), own[-1].gl_date.isoformat())
    march = store.journal_vouchers("2024-03-01", "2024-03-31")
    assert len(march) == sum(1 for v in truth.vouchers if v.gl_date.month == 3) > 0
    assert [v["gl_date"] for v in march] == sorted(v["gl_date"] for v in march)
    assert len(store.journal_vouchers(start="2024-07-01")) == sum(1 for v in truth.vouchers if v.gl_date.month >= 7)
    one = store.journal_vouchers("2024-01-01", "2024-06-30", supplier_number=suppliers[0]["supplier_number"])
    assert one and {v["supplier_number"] for v in one} == {suppliers[0]["supplier_number"]}
    assert all(v["gl_date"] <= "2024-06-30" for v in one)


def test_a_journal_whose_control_total_differs_is_kept_and_marked_so(store, truth):
    rows = jde_exports.journal_rows(truth)
    rows[-1][AT["debit"]] = "1.00"
    journal, data = _read(truth, _csv(rows))
    assert journal.totals.differ
    store.import_journal(journal, source_name="cut.csv", source_hash=hashlib.sha256(data).hexdigest())
    assert store.journal_files()[0]["control_total_differs"] is True  # (the command is what refuses it)


def test_a_journal_with_no_voucher_is_refused_and_nothing_is_written(store, truth):
    journal, data = _read(replace(truth, vouchers=[]))
    with pytest.raises(ValueError, match="no voucher"):
        store.import_journal(journal, source_name="empty.csv", source_hash=hashlib.sha256(data).hexdigest())
    assert store.journal_files() == [] and store.journal_stamp() == 0 and store.events() == []


def test_a_load_that_stopped_half_way_is_never_read_and_the_file_loads_again(store, truth, monkeypatch):
    monkeypatch.setattr(part, "VOUCHERS_A_WRITE", 50)
    wrote = part.JournalMixin._write_vouchers
    calls = []

    def stops(conn, file_id, vouchers):
        calls.append(len(vouchers))
        if len(calls) == 2:
            raise OSError("the disk is full")
        wrote(conn, file_id, vouchers)

    monkeypatch.setattr(part.JournalMixin, "_write_vouchers", staticmethod(stops))
    with pytest.raises(OSError):
        _load(store, truth)
    # The first 50 vouchers were written, in a transaction of their own: none is counted, listed or read.
    assert calls == [50, 50] and store.journal_files() == [] and store.journal_vouchers() == []
    assert store.journal_suppliers() == [] and store.journal_stamp() == 0 and _rows(store) == (1, 50)
    monkeypatch.setattr(part.JournalMixin, "_write_vouchers", staticmethod(wrote))
    # Another file loaded soon after leaves it be (it may still be under way); the file itself replaces it.
    other = _load(store, replace(truth, vouchers=truth.vouchers[:10]), "other.csv")
    assert other["added"] == 10 and _rows(store) == (2, 60)
    result = _load(store, truth)
    assert (result["loaded"], result["added"], result["already"]) == (True, 110, 10)
    assert len(store.journal_vouchers()) == 120 and len(store.journal_files()) == 2 and _rows(store) == (2, 130)


def _rows(store):
    """(the files' rows, the vouchers' rows) in the tables, counted or not."""
    with store._conn() as conn:
        return tuple(conn.execute(f"SELECT COUNT(*) FROM journal_{table}").fetchone()[0]
                     for table in ("files", "vouchers"))  # fmt: skip


def test_what_an_old_stopped_load_left_is_removed_at_the_next_load(store, truth, monkeypatch):
    wrote = part.JournalMixin._write_vouchers

    def stops(conn, file_id, vouchers):
        wrote(conn, file_id, vouchers[:5])
        conn.commit()
        raise OSError("the computer stopped")

    monkeypatch.setattr(part.JournalMixin, "_write_vouchers", staticmethod(stops))
    with pytest.raises(OSError):
        _load(store, replace(truth, vouchers=truth.vouchers[:20]), "stopped.csv")
    monkeypatch.setattr(part.JournalMixin, "_write_vouchers", staticmethod(wrote))
    assert _rows(store) == (1, 5) and store.journal_vouchers() == []
    monkeypatch.setattr(part, "STOPPED_AFTER", -part.STOPPED_AFTER)  # (as if it had begun over an hour ago)
    assert _load(store, truth)["added"] == 120
    assert _rows(store) == (1, 120) and len(store.journal_lines_for(str(truth.vouchers[0].supplier))) > 0
    with store._conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM journal_lines WHERE voucher_id NOT IN (SELECT id FROM "
                            "journal_vouchers)").fetchone()[0] == 0  # fmt: skip


def test_the_same_file_loaded_twice_at_the_same_moment_is_kept_once(store, truth, monkeypatch):
    monkeypatch.setattr(part, "VOUCHERS_A_WRITE", 50)
    wrote = part.JournalMixin._write_vouchers
    calls = []

    def meanwhile(conn, file_id, vouchers):
        calls.append(file_id)
        if len(calls) == 2:  # between two of the first load's writes, the same file is loaded whole
            assert _load(store, truth, actor="sam")["added"] == 120
        wrote(conn, file_id, vouchers)

    monkeypatch.setattr(part.JournalMixin, "_write_vouchers", staticmethod(meanwhile))
    with pytest.raises(ValueError, match="loaded a second time at the same moment"):
        _load(store, truth, actor="pat")
    (kept,) = store.journal_files()
    assert (kept["loaded_by"], kept["vouchers"], kept["counted"]) == ("sam", 120, 120) and _rows(store) == (1, 120)
    assert len(store.journal_vouchers()) == 120 and len(store.events(actions=["journal_imported"])) == 1


def test_a_caller_may_keep_the_whole_load_in_one_transaction(store, truth):
    with pytest.raises(RuntimeError), store.transaction():
        _load(store, truth)
        raise RuntimeError("the caller changes its mind")
    assert store.journal_files() == [] and store.journal_vouchers() == [] and store.journal_stamp() == 0


def test_the_objects_are_plain_settings(store):
    assert store.save_journal_objects(["2110", " 2115 "], ["2150"], ["2420"], actor="pat") == [
        "journal_payable_trade", "journal_payable_intercompany", "journal_tax_objects"]  # fmt: skip
    assert store.journal_objects() == {"trade": ("2110", "2115"), "intercompany": ("2150",), "tax": ("2420",)}
    assert store.get_setting("journal_payable_trade") == "2110, 2115"
    assert store.get_setting("journal_payable_intercompany") == "2150"
    assert store.get_setting("journal_tax_objects") == "2420"
    assert store.events(actions=["settings_changed"])[0]["detail"]["keys"] == [
        "journal_payable_trade", "journal_payable_intercompany", "journal_tax_objects"]  # fmt: skip
    before = store.events_stamp()
    assert store.save_journal_objects(["2110", "2115"], ["2150"], ["2420"]) == [] and store.events_stamp() == before
    assert store.save_journal_objects(["2110"], [], ["2420"]) == ["journal_payable_trade",
                                                                    "journal_payable_intercompany"]  # fmt: skip
    assert store.journal_objects() == {"trade": ("2110",), "intercompany": (), "tax": ("2420",)}
    assert store.journal_stamp() == 0  # (the journals loaded keep the kinds they were read with)


def test_the_same_voucher_is_known_whatever_objects_it_was_read_with(truth):
    journal, data = _read(truth)
    unmarked = jr.read_journal(data, payable_objects=jr.PayableObjects(()), tax_objects=())
    assert [part.voucher_identity(v) for v in journal.vouchers] == [
        part.voucher_identity(v) for v in unmarked.vouchers]  # fmt: skip
    assert len({part.voucher_identity(v) for v in journal.vouchers}) == len(journal.vouchers)


def test_a_year_of_vouchers_is_loaded_in_short_writes_and_read_fast(store):
    truth = jde_company.build(1, "year")
    journal, data = _read(truth)
    digest = hashlib.sha256(data).hexdigest()
    started = time.perf_counter()
    result = store.import_journal(journal, source_name="year.csv", source_hash=digest, actor="pat")
    took = time.perf_counter() - started
    print(f"\na year's journal ({result['vouchers']:,} vouchers, {result['lines']:,} lines) loaded in {took:.2f} s")
    assert (result["vouchers"], result["added"], result["already"]) == (5500, 5500, 0)
    assert result["lines"] == sum(len(v.lines) + len(v.pay_items) + sum(1 for i in v.pay_items if i.tax)
                                  for v in truth.vouchers)  # fmt: skip
    assert result["coding_lines"] == sum(len(v.lines) for v in truth.vouchers)
    assert took < 10  # (about 0.3 s on a developer's machine)
    started = time.perf_counter()
    suppliers = store.journal_suppliers()
    lines = store.journal_lines_for(suppliers[0]["supplier_number"])
    read = time.perf_counter() - started
    assert len(suppliers) == len({v.supplier for v in truth.vouchers}) and lines and read < 2
    started = time.perf_counter()
    assert store.import_journal(journal, source_name="year.csv", source_hash=digest)["loaded"] is False
    assert time.perf_counter() - started < 1
    assert store.forget_journal(result["file_id"]) == 5500 and store.journal_vouchers() == []
