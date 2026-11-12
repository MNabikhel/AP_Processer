"""``python -m ap_coder journal import FILE``, ``journal list`` and ``journal forget ID|all``: JD Edwards' Transaction
Journal loaded from the console, what was read and left out said in plain words first. The journals are a made-up
company's (``ap_coder.bench.jde_company``)."""

from __future__ import annotations

import csv
import hashlib
import io
import time
from dataclasses import replace

import pytest

from ap_coder.bench import jde_company, jde_exports
from ap_coder.core.store import Store
from ap_coder.ops import chart_cmd, cli, journal_cmd

AT = jde_exports.JOURNAL_POSITIONS


@pytest.fixture(scope="module")
def truth():
    return jde_company.build(1, "small")


def _file(tmp_path, truth, name="R09321.csv"):
    path = tmp_path / name
    path.write_bytes(jde_exports.journal_csv(truth))
    return path


def _run(tmp_path, capsys, *args):
    code = cli.main(["--db", str(tmp_path / "ap_coder.db"), "journal", *(str(a) for a in args)])
    said = capsys.readouterr()
    return code, said.out, said.err


def _store(tmp_path):
    return Store(tmp_path / "ap_coder.db")


def test_the_small_journal_is_loaded_and_what_was_read_is_said(tmp_path, capsys, truth):
    code, out, err = _run(tmp_path, capsys, "import", _file(tmp_path, truth))
    assert code == 0 and err == ""
    assert out.splitlines() == [
        "R09321.csv: JD Edwards' Transaction Journal (R09321).",
        "Read: 120 vouchers with 405 lines, G/L dates 2024-01-15 to 2024-12-30.",
        "The lines by kind: 195 coding (what AP chose), 129 payable and 81 tax (what JD Edwards added).",
        "Skipped: 7 title or page header rows and 2 total rows.",
        "Dates that read two ways were read month first (2/1/2024: 1 February 2024), as 1/15/2024 in the file says.",
        "16 vouchers with an intercompany payable line (an affiliate's).",
        "1 voucher with a negative amount (a credit).",
        "1 voucher where the debits and the credits are not the same amount (kept and marked): row 12 (740075 "
        "WINDROW WIRELESS CORP.).",
        "1 voucher with a line that has an amount in both the debit and the credit column (kept and marked): row 12.",
        "No row is missing: the lines read match the report's Grand Total row (debits 383,307.80 and credits "
        "383,423.04, as the report prints them).",
        "The payable and tax lines are JD Edwards' own, not AP's coding. They are told by their object:",
        "  The trade payable object: 2110 (worked out from this file).",
        "    Object 2110 closes 103 of 119 vouchers that balance (its lines alone are the other side of the coding "
        "lines).",
        "  The intercompany payable object: 2150 (worked out from this file).",
        "    Object 2150 closes 16 of the 16 vouchers that 2110 is not on, for 2 suppliers with no voucher that 2110 "
        "closes. An affiliate's vouchers are kept but not learned from; if this is a second trade payable object, "
        "move it.",
        "  The tax object: 2420 (worked out from this file).",
        "    Object 2420 is on 71 vouchers and is 5% of the rest of the voucher on 71 of them.",
        "Learned from: 154 coding lines on 103 vouchers.",
        "Kept but not learned from: 16 affiliates' vouchers and 1 voucher that does not balance.",
        "Suppliers: 35 with coding lines that are learned from. The vendor master has no supplier number (ERP ID) "
        "yet, so they could not be looked up in it: import JD Edwards' supplier master as well.",
        "Accounts: 75 account strings on the coding lines that are learned from. No chart of accounts is imported, "
        "so they were not checked against one.",
        "Loaded R09321.csv: 120 vouchers and 405 lines kept, 154 coding lines to learn from.",
    ]
    store = _store(tmp_path)
    (listed,) = store.journal_files()
    assert (listed["name"], listed["vouchers"], listed["counted"], listed["loaded_by"]) == (
        "R09321.csv", 120, 120, journal_cmd.ACTOR)  # fmt: skip
    assert listed["objects"] == {"trade": ["2110"], "intercompany": ["2150"], "tax": ["2420"]}
    # Exactly what the truth holds, and the objects kept for the next journal.
    assert len(store.journal_vouchers()) == len(truth.vouchers)
    assert store.journal_objects() == {"trade": ("2110",), "intercompany": ("2150",), "tax": ("2420",)}
    assert {e["actor"] for e in store.events(actions=["journal_imported"])} == {journal_cmd.ACTOR}


def test_the_same_file_again_adds_nothing_and_says_so(tmp_path, capsys, truth):
    path = _file(tmp_path, truth)
    assert _run(tmp_path, capsys, "import", path)[0] == 0
    store = _store(tmp_path)
    before = (store.journal_stamp(), store.events_stamp(), store.journal_files())
    code, out, _ = _run(tmp_path, capsys, "import", path)
    assert code == 0 and "This file is loaded already (" in out and "there is nothing to add" in out
    assert "by the console (journal import)" in out and "To load it again" in out and "Loaded R09321" not in out
    assert "The trade payable object: 2110 (as saved with the last journal loaded)." in out
    assert "there already from a file loaded before" not in out
    assert (store.journal_stamp(), store.events_stamp(), store.journal_files()) == before


def test_a_file_loaded_when_it_read_otherwise_says_so_and_reads_as_now_once_forgotten(tmp_path, truth):
    """B-2631: a journal printed day first, loaded before its dates were read so, is the same file by its bytes;
    loading it again says it reads otherwise now (it was "nothing to add"), and how to keep it as it reads."""
    data, store = jde_exports.journal_csv(truth), _store(tmp_path)
    now = journal_cmd.look(store, data, "R09321.csv").journal
    first = replace(now.vouchers[0], gl_date="2024-03-04")  # as an older reader read 03/04/2024
    store.import_journal(replace(now, vouchers=(first, *now.vouchers[1:])), source_name="R09321.csv",
                         source_hash=hashlib.sha256(data).hexdigest())  # fmt: skip
    state, said = journal_cmd.load(store, data, "R09321.csv")
    assert state == journal_cmd.SAME_FILE and said[-1].startswith("This file is loaded already (")
    assert said[-1].endswith(", but 1 voucher of it reads otherwise now: AP Coder reads such files better than "
                             "when it was loaded. Forget it and load it again to learn from it as it reads "
                             "now.")  # fmt: skip
    store.forget_journal(store.journal_files()[0]["id"])
    assert journal_cmd.load(store, data, "R09321.csv")[0] == journal_cmd.DONE
    assert min(v["gl_date"] for v in store.journal_vouchers()) == "2024-01-15"
    assert journal_cmd.load(store, data, "R09321.csv")[1][-1].endswith("forget it first.")  # as it reads: nothing new


def test_two_files_that_overlap_say_how_many_vouchers_were_there(tmp_path, capsys, truth):
    first = _file(tmp_path, replace(truth, vouchers=truth.vouchers[:80]), "to August.csv")
    second = _file(tmp_path, replace(truth, vouchers=truth.vouchers[40:]), "from May.csv")
    assert _run(tmp_path, capsys, "import", first)[0] == 0
    code, out, _ = _run(tmp_path, capsys, "import", second)
    assert code == 0
    assert ("40 vouchers of this file are there already from a file loaded before: kept with this file too, never "
            "counted twice.") in out  # fmt: skip
    assert out.splitlines()[-1].endswith("; 40 vouchers already there from another file (they are not counted twice).")
    assert len(_store(tmp_path).journal_vouchers()) == 120
    code, out, _ = _run(tmp_path, capsys, "list")
    lines = out.splitlines()
    assert code == 0 and lines[0] == "2 journals loaded:"
    may, august = truth.vouchers[40].gl_date.isoformat(), truth.vouchers[79].gl_date.isoformat()
    assert lines[1].startswith(f"  2: from May.csv, G/L dates {may} to 2024-12-30, 80 vouchers (40 of them in no "
                               "other file), ")  # fmt: skip
    assert lines[2].startswith(f"  1: to August.csv, G/L dates 2024-01-15 to {august}, 80 vouchers, ")
    assert lines[2].endswith(" by the console (journal import).") and " coding lines to learn from, loaded 20" in out


def test_forget_one_journal_or_all(tmp_path, capsys, truth):
    assert _run(tmp_path, capsys, "list") == (0, "No journal is loaded.\n", "")
    _run(tmp_path, capsys, "import", _file(tmp_path, replace(truth, vouchers=truth.vouchers[:80]), "a.csv"))
    _run(tmp_path, capsys, "import", _file(tmp_path, replace(truth, vouchers=truth.vouchers[40:]), "b.csv"))
    code, out, err = _run(tmp_path, capsys, "forget", "7")
    assert (code, out) == (1, "") and "There is no journal numbered 7" in err
    assert _run(tmp_path, capsys, "forget")[0] == 2
    assert _run(tmp_path, capsys, "forget", "1") == (0, "Forgot a.csv: 80 vouchers removed.\n", "")
    store = _store(tmp_path)
    assert len(store.journal_vouchers()) == 80  # b.csv's own: nothing of it went with a.csv
    assert {e["actor"] for e in store.events(actions=["journal_forgotten"])} == {journal_cmd.ACTOR}
    assert _run(tmp_path, capsys, "forget", "all") == (0, "Forgot every journal: 80 vouchers removed.\n", "")
    assert store.journal_files() == [] and store.journal_vouchers() == []


def test_a_control_total_that_differs_stops_the_load_unless_asked_to_go_on(tmp_path, capsys, truth):
    rows = jde_exports.journal_rows(truth)
    rows[-1][AT["debit"]] = "383307.90"
    text = io.StringIO(newline="")
    csv.writer(text, lineterminator="\r\n").writerows(rows)
    path = tmp_path / "cut.csv"
    path.write_bytes(text.getvalue().encode("utf-8-sig"))
    code, out, _ = _run(tmp_path, capsys, "import", path)
    assert code == 1
    assert "Warning: the lines read do not add up to the report's Grand Total." in out
    assert out.splitlines()[-2] == (
        "Not loaded: The lines read do not add up to the report's Grand Total, so rows are missing or were misread. "
        "Pull the report again; or load it anyway, knowing it is not whole.")  # fmt: skip
    assert out.splitlines()[-1] == "To load it anyway, add --anyway."
    store = _store(tmp_path)
    assert store.journal_files() == [] and store.journal_objects()["trade"] == () and store.events() == []
    code, out, _ = _run(tmp_path, capsys, "import", path, "--dry-run")
    assert code == 0 and out.splitlines()[-1] == "Nothing was loaded (--dry-run)." and store.journal_files() == []
    code, out, _ = _run(tmp_path, capsys, "import", path, "--anyway")
    assert code == 0 and out.splitlines()[-1].startswith("Loaded cut.csv: 120 vouchers")
    assert store.journal_files()[0]["control_total_differs"] is True


def test_the_objects_given_are_the_ones_used(tmp_path, capsys, truth):
    path = _file(tmp_path, truth)
    code, out, _ = _run(tmp_path, capsys, "import", path, "--trade", "2110,2150", "--intercompany", "--dry-run")
    assert code == 0 and "  The trade payable objects: 2110, 2150 (as given)." in out
    assert "  The intercompany payable objects: none (as given)." in out
    assert "  The tax object: 2420 (worked out from this file)." in out
    assert "Learned from: 194 coding lines on 119 vouchers." in out and "affiliate" not in out
    assert _store(tmp_path).journal_files() == []  # (--dry-run)
    # An object no line has: JD Edwards' own lines would be learned as AP's coding, so nothing is loaded.
    code, out, _ = _run(tmp_path, capsys, "import", path, "--trade", "9999")
    assert code == 1 and out.splitlines()[-1] == (
        "Not loaded: No line of this file has a trade payable object (9999), so JD Edwards' own lines would be "
        "taken for AP's coding. Name the trade payable object.")  # fmt: skip
    assert _store(tmp_path).journal_files() == []
    code, out, _ = _run(tmp_path, capsys, "import", path, "--trade", "2110", "--intercompany", "2150", "--tax")
    assert code == 0 and "  The tax objects: none (as given)." in out
    store = _store(tmp_path)
    assert store.journal_objects() == {"trade": ("2110",), "intercompany": ("2150",), "tax": ()}
    assert store.journal_files()[0]["tax_lines"] == 0 and store.journal_files()[0]["coding_lines"] == 195 + 81


def test_saved_objects_of_another_company_are_passed_over_for_the_files_own(tmp_path, capsys, truth):
    assert _run(tmp_path, capsys, "import", _file(tmp_path, truth))[0] == 0
    other = jde_exports.journal_csv(truth).replace(b".2110,", b".2188,").replace(b"00210", b"00530")
    path = tmp_path / "other company.csv"
    path.write_bytes(other)
    code, out, _ = _run(tmp_path, capsys, "import", path)
    assert code == 0 and "  The trade payable object: 2188 (worked out from this file)." in out
    assert _store(tmp_path).journal_objects()["trade"] == ("2188",)


def test_the_tax_setups_recoverable_account_is_the_tax_object(tmp_path, capsys, truth):
    store = _store(tmp_path)
    store.set_tax_treatment("GST", "recoverable", "2420.10")
    store.set_tax_treatment("PST", "expense_separate", "7510")  # an expense account of the journal: not a tax line
    code, out, _ = _run(tmp_path, capsys, "import", _file(tmp_path, truth), "--dry-run")
    assert code == 0 and "  The tax object: 2420 (worked out from this file)." in out
    assert "    Object 2420 is a GL account of the tax setup; it is on 81 lines." in out
    assert "7510" not in out


def test_suppliers_and_accounts_are_looked_up_in_the_vendor_master_and_the_chart(tmp_path, capsys, truth):
    used = sorted({line.account for v in truth.vouchers for line in v.lines})
    gone, held = used[5], used[9]
    accounts = [replace(a, posting_edit="I") if a.string == held else a for a in truth.accounts if a.string != gone]
    changed = replace(truth, accounts=accounts)
    store = _store(tmp_path)
    chart_cmd.import_files(store, jde_exports.chart_xlsx(changed), jde_exports.business_units_xlsx(changed))
    learned = [v for v in truth.vouchers if truth.supplier(v.supplier).kind != "affiliate"
               and v.oddity != "unbalanced"]  # fmt: skip
    numbers = sorted({str(v.supplier) for v in learned})
    missing = numbers[:7]
    store.import_vendor_master([{"vendor_name": truth.supplier(int(n)).name, "erp_id": "00" + n}
                                for n in numbers[7:]])  # fmt: skip
    code, out, _ = _run(tmp_path, capsys, "import", _file(tmp_path, truth))
    assert code == 0
    names = [f"{n} {truth.supplier(int(n)).name[:26].strip()}" for n in missing]
    assert (f"Suppliers: {len(numbers)} with coding lines that are learned from. 7 of them are not in the vendor "
            f"master, by supplier number (its ERP ID): {', '.join(names[:5])} and 2 more. Their lines are kept "
            "under the number.") in out  # fmt: skip
    assert "Accounts: 75 account strings on the coding lines that are learned from." in out
    assert f"  1 not in the chart: {gone}." in out and f"  1 may not be posted to: {held}." in out
    # With every supplier in the vendor master and the chart whole, both say so.
    store.import_vendor_master([{"vendor_name": truth.supplier(int(n)).name, "erp_id": n} for n in missing])
    chart_cmd.import_files(store, jde_exports.chart_xlsx(truth), jde_exports.business_units_xlsx(truth))
    _run(tmp_path, capsys, "forget", "all")
    code, out, _ = _run(tmp_path, capsys, "import", _file(tmp_path, truth))
    assert code == 0 and "All are in the vendor master, by supplier number (its ERP ID)." in out
    assert "All are in the chart of accounts and may be posted to." in out


def test_a_file_that_is_no_journal_is_refused_and_nothing_is_written(tmp_path, capsys):
    table = tmp_path / "lines.csv"
    table.write_text("Vendor,Description,GL\nAcme,Paper,6000\n", encoding="utf-8")
    code, out, _ = _run(tmp_path, capsys, "import", table)
    assert code == 1 and out == f"Not loaded: {journal_cmd.NOT_A_JOURNAL}\n"
    code, _, err = _run(tmp_path, capsys, "import", tmp_path / "missing.csv")
    assert code == 1 and "There is no file" in err and "Nothing was loaded." in err
    code, _, err = _run(tmp_path, capsys, "import")
    assert code == 2 and "journal import FILE" in err
    headed = tmp_path / "headed.csv"  # the report's header and totals, not one voucher
    headed.write_bytes(jde_exports.journal_csv(replace(jde_company.build(1, "small"), vouchers=[])))
    code, out, _ = _run(tmp_path, capsys, "import", headed, "--trade", "2110")
    assert code == 1 and out.splitlines()[-1] == "Not loaded: No voucher could be read from this file."
    assert _store(tmp_path).journal_files() == [] and _store(tmp_path).events() == []


def test_a_year_of_vouchers_from_the_console(tmp_path, capsys):
    truth = jde_company.build(1, "year")
    path = _file(tmp_path, truth)
    started = time.perf_counter()
    code, out, _ = _run(tmp_path, capsys, "import", path)
    took = time.perf_counter() - started
    print(
        f"\na year's journal ({len(truth.vouchers):,} vouchers) looked at and loaded from the console in {took:.2f} s"
    )
    assert code == 0 and took < 30
    assert "Read: 5,500 vouchers with 18,707 lines, G/L dates 2024-01-01 to 2024-12-31." in out
    assert "  The trade payable object: 2110 (worked out from this file)." in out
    assert "  The intercompany payable object: 2150 (worked out from this file)." in out
    assert "  The tax object: 2420 (worked out from this file)." in out
    assert out.splitlines()[-1] == ("Loaded R09321.csv: 5,500 vouchers and 18,707 lines kept, 7,332 coding lines to "
                                    "learn from.")  # fmt: skip
    store = _store(tmp_path)
    assert len(store.journal_suppliers()) == len({v.supplier for v in truth.vouchers})


def test_a_journal_saved_again_through_excel_is_refused_in_plain_words(tmp_path, capsys, truth):
    """B-2346: opened and saved in Excel, an account cell with one dot reads as a number, so "0021061010.2110" becomes
    "21061010.211": the trade payable object was worked out as 211 and the vouchers loaded as if whole. A business
    unit or object cut short of the chart's codes, or of the file's own, refuses the file; the file as JD Edwards
    wrote it loads."""
    rows = jde_exports.journal_rows(truth)
    cut = 0
    for row in rows:
        cell = row[AT["account"]] if len(row) > AT["account"] else ""
        if cell.count(".") == 1 and cell.replace(".", "").strip().isdigit():
            row[AT["account"]], cut = repr(float(cell)).removesuffix(".0"), cut + 1
    assert cut
    text = io.StringIO(newline="")
    csv.writer(text, lineterminator="\r\n").writerows(rows)
    path = tmp_path / "saved.csv"
    path.write_bytes(text.getvalue().encode("utf-8-sig"))
    words = "this file was saved through Excel and lost its leading zeros: export it again from JD Edwards"
    for chart in (False, True):
        if chart:
            chart_cmd.import_files(_store(tmp_path), jde_exports.chart_xlsx(truth),
                                   jde_exports.business_units_xlsx(truth))  # fmt: skip
        code, out, _ = _run(tmp_path, capsys, "import", path)
        assert code == 1 and out.splitlines()[-1].startswith("Not loaded: This file was saved through Excel"), out
        assert words.lower() in out.lower() and _store(tmp_path).journal_files() == []
    code, out, _ = _run(tmp_path, capsys, "import", _file(tmp_path, truth))
    assert code == 0 and out.splitlines()[-1].startswith("Loaded R09321.csv")
