"""``pilot-score --journal``: how AP Coder would have coded the last months of last year's journal, knowing only the
months before. On the made-up company of ``bench.jde_company`` (no company's data): the numbers are those of
``bench coding --journal``'s measuring code on the same split, the database in use is only read, and the report says
it in a paragraph a person can read out."""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import sqlite3
import time
from types import SimpleNamespace

import pytest

from ap_coder.bench import coding_journal as measure
from ap_coder.bench import jde_company, jde_exports
from ap_coder.core.store import Store
from ap_coder.core.store.journal import supplier_key
from ap_coder.erp import journal as jr
from ap_coder.ops import journal_score, pilot_score


def _company(tmp, size: str = "small") -> SimpleNamespace:
    """The made-up company as a department has it on the laptop: its lists and the whole year's journal loaded, and
    the journal as a file."""
    truth = jde_company.build(1, size)
    store = Store(tmp / "private" / "ap_coder.db")
    measure.load_books(store, truth, truth.vouchers)
    file = tmp / "R09321.csv"
    file.write_bytes(jde_exports.journal_csv(truth))
    return SimpleNamespace(truth=truth, store=store, file=file)


@pytest.fixture(scope="module")
def company(tmp_path_factory) -> SimpleNamespace:
    made = _company(tmp_path_factory.mktemp("journal-score"))
    made.report, made.folder = journal_score.run(made.store, made.file)
    return made


def _dump(store: Store) -> list[str]:
    with sqlite3.connect(store.path) as conn:
        return list(conn.iterdump())


def _measured_as_the_bench_does(truth: jde_company.Truth, tmp) -> list[dict]:
    """The held-out lines scored by the coding bench's own code, on a database the bench loads itself with the
    older vouchers (``load_books``): the remark stands for the invoice line's words."""
    from ap_coder.ai.offline_coder import code_from_capture
    from ap_coder.bench.coding import _capture

    start = measure.hold_out_from(truth)
    store = Store(tmp / "bench.db")
    measure.load_books(store, truth, [v for v in truth.vouchers if v.gl_date < start])
    company = truth.company
    journal = jr.read_journal(jde_exports.journal_csv(truth), tax_objects=(company.gst_object,),
                              payable_objects=jr.PayableObjects((company.payable_object,),
                                                                (company.intercompany_object,)))  # fmt: skip
    names = {supplier_key(v["erp_id"]): v["display_name"] for v in store.master_vendors()}
    rows = []
    with store.session():
        reference = store.reference_data()
        for voucher in journal.vouchers:
            if voucher.gl_date < start.isoformat() or voucher.affiliate or not voucher.balanced:
                continue
            vendor = names.get(supplier_key(voucher.supplier_number)) or voucher.supplier_name
            keyed = [line for line in voucher.lines if line.kind == jr.CODING]
            lines = [SimpleNamespace(text=line.remark, quantity=1.0, unit_price=line.amount, amount=line.amount)
                     for line in keyed]  # fmt: skip
            feedback = store.learning_rows(vendor)
            captured = _capture(vendor, "", dt.date.fromisoformat(voucher.gl_date), lines)
            coded = code_from_capture(captured, reference, feedback, store, text="").coding.line_items
            for line, li in zip(keyed, coded, strict=True):
                top = measure._ranked(store, reference, vendor, li.description, feedback)
                posted = SimpleNamespace(account=journal_score.account_of(line))
                rows.append({"supplier": supplier_key(voucher.supplier_number),
                             **measure._scored(SimpleNamespace(text=li.description), posted, li, top)})  # fmt: skip
    return rows


def test_the_numbers_are_the_coding_benchs_measure_on_the_same_split(company, tmp_path):
    rows = _measured_as_the_bench_does(company.truth, tmp_path)
    report = company.report
    assert rows and report["all"] == measure.line_stats(rows)
    # the pilot's gate, in its words: the same lines, counted by the helper ``pilot-score --proposed`` uses too
    gate, s = report["gate"], report["all"]
    assert gate == journal_score.gate(rows) and gate["left_out"] == {}
    assert (gate["lines"], gate["coded_alone"], gate["right_when_alone"]) == (s["lines"], s["coded"], s["coded_right"])
    assert gate["first_three"] == round(s["in_top3"] * s["lines"])
    assert gate["first"] == round(s["first_right"] * s["lines"])
    assert gate["left_first_three"] == report["offered"]
    assert gate["left_to_a_person"] == report["offered"] + report["left"]
    assert report["sure_grid"] == [g for g in measure.sure_grid(rows) if g["lines"] in journal_score.GRID_LINES]
    assert {(g["share"], g["lines"]) for g in report["sure_grid"]} == {
        (share, n) for share in (0.8, 0.9, 0.95, 1.0) for n in (3, 5)}  # fmt: skip
    counts = report["counts"]
    assert counts["held_out_from"] == measure.hold_out_from(company.truth).isoformat()
    assert counts["lines"] == len(rows) and counts["affiliates_left_out"] >= 1


def test_the_database_in_use_is_only_read(tmp_path):
    made = _company(tmp_path)
    before, files = _dump(made.store), made.store.journal_files()
    journal_score.run(made.store, made.file)  # from the file: the journal loaded here is not added to
    journal_score.run(made.store)  # from the journal loaded here
    assert _dump(made.store) == before and made.store.journal_files() == files
    assert sorted(p.name for p in made.store.path.parent.iterdir() if p.is_dir()) == [pilot_score.FOLDER]


def test_the_journal_loaded_here_gives_the_score_of_its_file(company, tmp_path):
    report, _ = journal_score.run(company.store, None, tmp_path / "loaded")
    assert report["all"] == company.report["all"] and report["counts"] == company.report["counts"]


def test_the_paragraph_can_be_read_out_and_says_it_is_an_upper_bound(company):
    report, s = company.report, company.report["all"]
    said = journal_score.paragraph(report)
    year = company.truth.company.year
    assert said.startswith(f"Of {s['lines']} lines keyed in November and December {year} "
                           f"({report['counts']['vouchers']} vouchers), AP Coder would have coded {s['coded']} by "
                           "itself, ")  # fmt: skip
    assert s["coded"] + report["offered"] + report["left"] == s["lines"]
    assert f"for another {report['offered']} the keyed account was among its first three suggestions" in said
    assert f"{report['left']} it would have left to a person" in said
    text = (company.folder / journal_score.REPORT).read_text(encoding="utf-8")
    assert said in text and journal_score.UPPER_BOUND in text and "upper bound" in journal_score.UPPER_BOUND
    gate = report["gate"]
    assert (f"| Coded alone (no one picking the account) | {gate['coded_alone']} of {gate['lines']} " in text
            and "| Right when coded alone (the account string keyed) |" in text
            and "| Your account among the first three |" in text)  # fmt: skip
    assert text.index("| Coded alone") < text.index("## What was scored")  # at the top
    for heading in ("## The score", "## By business unit", "## The suppliers with the most misses",
                    "## What the sure rule would do at other bars"):  # fmt: skip
        assert heading in text
    for group in ("supplier seen before", "supplier new", "one-line vouchers", "vouchers of two lines or more",
                  "expense business units", "| 80% or more |", "| 100% |"):  # fmt: skip
        assert group in text


def test_the_three_files_and_every_miss_a_row_with_both_accounts(company):
    assert company.folder == company.store.path.parent / pilot_score.FOLDER
    missed = list(csv.DictReader(io.StringIO((company.folder / journal_score.MISSES).read_text("utf-8-sig"))))
    s = company.report["all"]
    assert len(missed) == s["lines"] - round(s["first_right"] * s["lines"]) > 0
    assert list(missed[0]) == journal_score.MISS_COLUMNS
    for row in missed:
        assert row["keyed_account"] and row["keyed_account"] != row["suggested_account"] and row["why"]
        assert row["supplier_number"] and row["coded_with_no_one_picking"] in ("yes", "no")
    worst = company.report["worst_suppliers"]
    assert worst == sorted(worst, key=lambda w: -w["misses"]) and len(worst) <= journal_score.SUPPLIERS_SHOWN
    assert sum(w["misses"] for w in worst) == len(missed)  # (the small company: fewer suppliers than are shown)
    figures = json.loads((company.folder / journal_score.FIGURES).read_text(encoding="utf-8"))
    assert figures["all"] == s and figures["counts"] == company.report["counts"]
    assert figures["gate"] == company.report["gate"]
    names = {supplier.name[:26] for supplier in company.truth.suppliers}
    assert not any(name in json.dumps(figures) for name in names)


def test_the_command_says_the_paragraph_and_where_the_report_is(company, tmp_path, capsys):
    from ap_coder.ops import cli

    out = tmp_path / "cli"
    code = cli.main(["--db", str(company.store.path), "pilot-score", "--journal", str(company.file), "--hold-out",
                     "2", "--out", str(out)])  # fmt: skip
    said = capsys.readouterr().out
    assert code == 0 and journal_score.paragraph(company.report) in said and journal_score.UPPER_BOUND in said
    assert str(out / journal_score.REPORT) in said and (out / journal_score.MISSES).exists()
    assert "First suggestion right: " in said and "business unit right: " in said
    assert journal_score.gate_said(company.report["gate"]) in said and "Lines coded alone: " in said
    assert cli.main(["--db", str(company.store.path), "pilot-score", "--journal", "--out", str(out)]) == 0


def test_more_months_held_out_and_what_cannot_be_scored_is_said(company, tmp_path, capsys):
    from ap_coder.ops import cli

    report, _ = journal_score.run(company.store, company.file, tmp_path / "three", hold_out=3)
    year = company.truth.company.year
    assert report["counts"]["held_out_from"] == f"{year}-10-01"
    assert f"October, November and December {year}" in journal_score.paragraph(report)
    with pytest.raises(pilot_score.ExportError, match="no older voucher to learn from"):
        journal_score.run(company.store, company.file, tmp_path / "all", hold_out=24)
    empty = Store(tmp_path / "empty" / "ap_coder.db")
    assert cli.main(["--db", str(empty.path), "pilot-score", "--journal"]) == 1
    assert "No journal is loaded in this data folder" in capsys.readouterr().err
    not_one = tmp_path / "list.csv"
    not_one.write_text("a,b\n1,2\n", encoding="utf-8")
    with pytest.raises(pilot_score.ExportError, match="not a Transaction Journal"):
        journal_score.run(company.store, not_one, tmp_path / "refused")


def test_a_data_folder_with_no_chart_of_accounts_is_scored_too(company, tmp_path):
    """GL accounts and cost centers typed in, no chart imported: the temporary database gets the two lists."""
    plain = Store(tmp_path / "plain" / "ap_coder.db")
    plain.save_accounts("gl_accounts", [{"code": "7340.2210", "description": "Courier", "category": ""}])
    plain.save_accounts("cost_centers", [{"code": "0021061010", "description": "General Administration",
                                          "category": ""}])  # fmt: skip
    before = _dump(plain)
    report, _ = journal_score.run(plain, company.file)
    assert report["all"]["lines"] == company.report["all"]["lines"] and _dump(plain) == before


def test_a_year_of_vouchers_is_scored_in_seconds(tmp_path):
    made = _company(tmp_path, "year")
    began = time.perf_counter()
    report, folder = journal_score.run(made.store, made.file)
    took = time.perf_counter() - began
    s = report["all"]
    print(f"\npilot-score --journal on the made-up year: {took:.1f} s\n{journal_score.paragraph(report)}\n"
          f"{journal_score.gate_said(report['gate'])}")  # fmt: skip
    assert s["lines"] > 1000 and s["coded"] + report["offered"] + report["left"] == s["lines"]
    assert len(report["worst_suppliers"]) == journal_score.SUPPLIERS_SHOWN
    assert took < 120, f"{took:.0f} s"
