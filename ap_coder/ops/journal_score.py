"""How AP Coder would have coded last year's vouchers (``python -m ap_coder pilot-score --journal [FILE]``).

JD Edwards' Transaction Journal (report R09321 saved as CSV: the file given, else the journal loaded in this data
folder) is cut in two by G/L date. The older part is loaded into a TEMPORARY database, which gets a copy of the chart
of accounts, the business units, the supplier list and the settings: the database in use, its learning memory and its
journal are only read. Then each coding line of the last months' vouchers is coded as a line of an invoice of its
supplier would be, its remark standing for the words on the invoice, and what comes out is compared with the account
string that was keyed.

There is no invoice here: a remark is in the department's own words, an invoice in the supplier's. So the figures are
an upper bound, and the report says so. ``pilot-score --proposed`` scores real invoices.

The measuring is ``bench coding --journal``'s own (``bench.coding_journal``: ``_scored``, ``line_stats``,
``sure_grid``): one scorer, here on a company's own journal. Writes ``journal_score.md`` (the report),
``journal_score_misses.csv`` (a row for each line whose first suggestion is not the keyed account) and
``journal_score.json`` (the figures, no names) into ``pilot_score`` in the data folder.
"""

from __future__ import annotations

import calendar
import csv
import datetime as dt
import io
import json
import tempfile
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from ..bench import coding_journal as measure
from ..bench.harness import _table
from ..core import chart
from ..core.safe import csv_row
from ..core.store.journal import supplier_key
from ..core.ui import plural
from ..erp import jde
from ..erp import journal as jr
from .pilot_score import FOLDER, ExportError

REPORT, MISSES, FIGURES = "journal_score.md", "journal_score_misses.csv", "journal_score.json"
HOLD_OUT_MONTHS = measure.HOLD_OUT_MONTHS
SUPPLIERS_SHOWN = 20  # the suppliers with the most misses
GRID_LINES = (3, 5)  # the bars of ``sure_grid`` shown
EXPENSE, BALANCE_SHEET = "expense", "balance sheet"
UNIT_CLASSES = {"IS": EXPENSE, "BS": BALANCE_SHEET}  # a business unit's type in the business unit master
UPPER_BOUND = ("This is an upper bound: there was no invoice to read, so each line was described by the remark typed "
               "in JD Edwards, which is in the department's own words. A supplier's invoice words the same purchase "
               "its own way; `pilot-score --proposed` scores real invoices.")  # fmt: skip
NO_JOURNAL = ("No journal is loaded in this data folder: give the file (pilot-score --journal R09321.csv), or load it "
              "first (python -m ap_coder journal import R09321.csv).")  # fmt: skip
MISS_COLUMNS = ["supplier_number", "supplier_name", "gl_date", "remark", "amount", "keyed_account",
                "suggested_account", "why", "coded_with_no_one_picking", "keyed_account_in_first_three"]  # fmt: skip


# --- The journal ----------------------------------------------------------------------------------------------------


def loaded_journal(store: Any) -> jr.Journal:
    """The journal this data folder holds (every file loaded, a voucher two files hold once), as the reader gives
    one."""
    vouchers = [
        jr.Voucher(
            row=v["id"], doc_type=v["doc_type"], company=v["company"], gl_date=v["gl_date"] or "",
            supplier_number=v["supplier_number"], supplier_name=v["supplier_name"],
            lines=tuple(jr.Line(row=t["row"], account=t["account"], business_unit=t["business_unit"],
                                object=t["object"], subsidiary=t["subsidiary"], description=t["description"],
                                amount=t["amount"], remark=t["remark"], kind=t["kind"]) for t in v["lines"]),
            gross=v["gross"], tax=v["tax"], affiliate=v["affiliate"], credit=v["credit"], balanced=v["balanced"],
            both_columns=v["both_columns"],
        )
        for v in store.journal_vouchers()
    ]  # fmt: skip
    if not vouchers:
        raise ExportError(NO_JOURNAL)
    return jr.journal_of(vouchers)


def journal_file(store: Any, path: str | Path) -> jr.Journal:
    """A journal file as ``journal import`` would read it (its payable and tax objects as saved, else worked out
    from the file); nothing is loaded."""
    from . import journal_cmd

    path = Path(path)
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ExportError(f"{path}: {exc}") from exc
    seen = journal_cmd.look(store, data, path.name)
    if seen.journal is None or not seen.journal.vouchers:
        raise ExportError(f"{path}: {seen.problem or 'no voucher could be read in this file.'}")
    return seen.journal


def the_journal(store: Any, path: str | Path | None) -> tuple[jr.Journal, str]:
    """(the journal to score with, what to call it): the file given, else the one loaded here."""
    if path:
        return journal_file(store, path), Path(path).name
    return loaded_journal(store), "the journal loaded in this data folder"


def hold_out_from(last: str, months: int) -> str:
    """The first G/L date held out: the first day of the last ``months`` months that end with ``last``'s."""
    day = dt.date.fromisoformat(last[:10])
    first = day.year * 12 + day.month - 1 - (months - 1)
    return dt.date(first // 12, first % 12 + 1, 1).isoformat()


def account_of(line: Any) -> str:
    """A journal line's account string as the suggestions write one."""
    return chart.join_account(line.business_unit, chart.code_of(line.object, line.subsidiary))


# --- The temporary database -----------------------------------------------------------------------------------------


def books_copy(live: Any, path: Path) -> Any:
    """A new database at ``path`` with what ``live`` knows of the company and nothing it learned: the business units,
    the chart of accounts and its settings, the supplier list, the tax setup, JD Edwards' settings and the journal's
    payable and tax objects. ``live`` is only read."""
    from ..core.store import Store

    store = Store(path)
    store.import_business_units(live.business_units())
    store.import_chart(live.chart_accounts())
    if not live.has_chart():  # the lists typed or imported as they are
        for table in ("gl_accounts", "cost_centers"):
            store.save_accounts(table, live.list_accounts(table))
    store.save_chart_settings(live.chart_settings())
    store.import_vendor_master([
        {"vendor_name": v["display_name"], "erp_id": str(v.get("erp_id") or ""), "status": v.get("status") or "active",
         "gst": v.get("expected_gst") or "", "terms": v.get("terms") or "", "default_gl": v.get("default_gl") or ""}
        for v in live.master_vendors()
    ], by_erp_id=True, log=False)  # fmt: skip
    for tax_type, treatment in live.tax_treatments().items():
        store.set_tax_treatment(tax_type, treatment.treatment, treatment.gl_code)
    jde.save(store, jde.load(live))
    store.save_journal_objects(**live.journal_objects())
    return store


# --- The hold-out ---------------------------------------------------------------------------------------------------


def simulate(live: Any, journal: jr.Journal, months: int = HOLD_OUT_MONTHS) -> tuple[list[dict[str, Any]], dict]:
    """(a row for each coding line of the held-out vouchers, with what was suggested and what was keyed; the period
    and the counts). The vouchers with no G/L date are in neither part; an affiliate's voucher and one that does not
    balance are not learned from and not scored, as when a journal is loaded."""
    from ..ai.offline_coder import code_from_capture
    from ..bench.coding import _capture

    dated = [v for v in journal.vouchers if v.gl_date]
    if not dated:
        raise ExportError("No voucher of the journal has a G/L date that could be read: nothing to hold out.")
    first, last = min(v.gl_date for v in dated), max(v.gl_date for v in dated)
    start = hold_out_from(last, max(1, months))
    older, held = [v for v in dated if v.gl_date < start], [v for v in dated if v.gl_date >= start]
    if not older:
        raise ExportError(f"The journal covers {first} to {last}: holding out its last {plural(months, 'month')} "
                          "leaves no older voucher to learn from. Give fewer months (--hold-out 1) or a journal of "
                          "a longer period.")  # fmt: skip
    scored = [v for v in held if not v.affiliate and v.balanced]
    counts = {
        "first_date": first, "last_date": last, "hold_out_months": months, "held_out_from": start,
        "learned_to": max(v.gl_date for v in older), "older_vouchers": len(older), "held_out_vouchers": len(held),
        "affiliates_left_out": sum(1 for v in held if v.affiliate),
        "unbalanced_left_out": sum(1 for v in held if not v.affiliate and not v.balanced),
        "undated_left_out": len(journal.vouchers) - len(dated),
    }  # fmt: skip
    known = {supplier_key(v.supplier_number) for v in older if not v.affiliate and v.balanced}
    rows: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="ap-coder-journal-score-", ignore_cleanup_errors=True) as tmp:
        store = books_copy(live, Path(tmp) / "score.db")
        store.import_journal(jr.journal_of(older), source_name="the older vouchers", source_hash="pilot-score")
        with store.session():
            reference = store.reference_data()
            names = {supplier_key(v["erp_id"]): v["display_name"] for v in store.master_vendors() if v.get("erp_id")}
            classes = {u.code: UNIT_CLASSES.get(u.type.strip().upper(), "") for u in store.business_units()}
            for voucher in scored:
                keyed = [line for line in voucher.lines if line.kind == jr.CODING]
                if not keyed:
                    continue
                number = supplier_key(voucher.supplier_number)
                vendor = names.get(number) or voucher.supplier_name
                printed = [SimpleNamespace(text=line.remark, quantity=1.0, unit_price=float(line.amount),
                                           amount=float(line.amount)) for line in keyed]  # fmt: skip
                feedback = store.learning_rows(vendor)
                captured = _capture(vendor, "", dt.date.fromisoformat(voucher.gl_date[:10]), printed)
                coded = code_from_capture(captured, reference, feedback, store, text="").coding.line_items
                for line, li in zip(keyed, coded, strict=True):
                    account = account_of(line)
                    top = measure._ranked(store, reference, vendor, li.description, feedback)
                    rows.append({
                        "supplier": number, "supplier_name": voucher.supplier_name, "gl_date": voucher.gl_date,
                        "voucher": voucher.row, "remark": line.remark, "amount": line.amount,
                        "seen_before": number in known, "one_line": len(keyed) == 1,
                        "unit": line.business_unit, "class": classes.get(line.business_unit, ""),
                        **measure._scored(SimpleNamespace(text=li.description), SimpleNamespace(account=account),
                                          li, top),
                    })  # fmt: skip
    counts["vouchers"] = len({r["voucher"] for r in rows})
    counts["lines"] = len(rows)
    return rows, counts


def _by(rows: list[dict[str, Any]], key: str, names: dict[Any, str]) -> dict[str, dict[str, Any]]:
    """``line_stats`` of the rows of each value of ``key`` that ``names`` names (a group with no row is left out)."""
    found = {}
    for value, name in names.items():
        part = [r for r in rows if r[key] == value]
        if part:
            found[name] = measure.line_stats(part)
    return found


def summarize(rows: list[dict[str, Any]], counts: dict[str, Any], unit_names: dict[str, str]) -> dict[str, Any]:
    """The report's figures: all the lines, then the same by supplier seen before or new, by one-line vouchers or
    more, by expense or balance-sheet business unit (when the business unit master says), by business unit, the
    suppliers with the most misses, and what the sure rule would do at other bars."""
    suppliers: dict[str, dict[str, Any]] = {}
    for r in rows:
        s = suppliers.setdefault(r["supplier"], {"number": r["supplier"], "name": r["supplier_name"], "lines": 0,
                                                 "right": 0, "misses": 0})  # fmt: skip
        s["lines"] += 1
        s["right"] += r["first_right"]
        s["misses"] += not r["first_right"]
    worst = sorted(suppliers.values(), key=lambda s: (-s["misses"], -s["lines"], s["number"]))
    units: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        units[r["unit"]].append(r)
    by_unit = [{"unit": unit, "name": unit_names.get(unit, ""), **measure.line_stats(part)}
               for unit, part in sorted(units.items(), key=lambda t: (-len(t[1]), t[0]))]  # fmt: skip
    all_ = measure.line_stats(rows)
    left = [r for r in rows if not r["coded"]]
    return {
        "counts": counts, "all": all_,
        "offered": sum(1 for r in left if r["in_top3"]), "left": sum(1 for r in left if not r["in_top3"]),
        "by_supplier_seen": _by(rows, "seen_before", {True: "supplier seen before", False: "supplier new"}),
        "by_voucher_lines": _by(rows, "one_line", {True: "one-line vouchers", False: "vouchers of two lines or more"}),
        "by_class": _by(rows, "class", {EXPENSE: "expense business units", BALANCE_SHEET: "balance-sheet ones"}),
        "by_unit": by_unit, "suppliers": len(suppliers),
        "worst_suppliers": [s for s in worst if s["misses"]][:SUPPLIERS_SHOWN],
        "sure_grid": [g for g in measure.sure_grid(rows) if g["lines"] in GRID_LINES],
    }  # fmt: skip


# --- The report -----------------------------------------------------------------------------------------------------


def months_said(start: str, end: str) -> str:
    """ "November and December 2024" for the months from ``start``'s to ``end``'s."""
    a, b = dt.date.fromisoformat(start[:10]), dt.date.fromisoformat(end[:10])
    months = [(n // 12, n % 12 + 1) for n in range(a.year * 12 + a.month - 1, b.year * 12 + b.month)]
    if len(months) > 3 or a.year != b.year:
        return f"{calendar.month_name[a.month]} {a.year} to {calendar.month_name[b.month]} {b.year}"
    names = [calendar.month_name[month] for _, month in months]
    return (", ".join(names[:-1]) + " and " + names[-1] if len(names) > 1 else names[0]) + f" {b.year}"


def _right_said(coded: int, right: int) -> str:
    wrong = coded - right
    return "all of them" if not wrong else "none of them" if not right else f"all but {wrong:,}"


def paragraph(report: dict[str, Any]) -> str:
    """The report's first paragraph: what a finance analyst reads out."""
    c, s = report["counts"], report["all"]
    keyed = months_said(c["held_out_from"], c["last_date"])
    alone = (f"AP Coder would have coded {s['coded']:,} by itself, {_right_said(s['coded'], s['coded_right'])} the "
             "way they were keyed" if s["coded"] else "AP Coder would have coded none by itself")  # fmt: skip
    return (f"Of {plural(s['lines'], 'line')} keyed in {keyed} ({plural(c['vouchers'], 'voucher')}), {alone}; for "
            f"another {report['offered']:,} the keyed account was among its first three suggestions; "
            f"{report['left']:,} it would have left to a person without the keyed account among them. It knew only the "
            f"vouchers keyed before, {c['first_date']} to {c['learned_to']}.")  # fmt: skip


def _pct(a: float, b: float) -> str:
    return "-" if not b else f"{100 * a / b:.0f}%"


def _stat_row(name: str, s: dict[str, Any]) -> list[Any]:
    share = measure._pct
    return [name, f"{s['lines']:,}", f"{s['coded']:,} ({share(s['coded_share'])})",
            f"{s['coded_right']:,} ({share(s['coded_right_share'])})" if s["coded"] else "-",
            share(s["first_right"]), share(s["in_top3"]), share(s["code_right"]), share(s["unit_right"])]  # fmt: skip


STAT_HEAD = ["", "lines", "coded with no one picking", "of those right", "first suggestion right",
             "right answer in the first three", "object.subsidiary right", "business unit right"]  # fmt: skip


def render_markdown(report: dict[str, Any]) -> str:
    c = report["counts"]
    grid = {(g["share"], g["lines"]): g for g in report["sure_grid"]}
    shares = sorted({share for share, _ in grid})
    groups = [(name, s) for part in ("by_supplier_seen", "by_voucher_lines", "by_class")
              for name, s in report[part].items()]  # fmt: skip
    lines = [
        "# How AP Coder would have coded last year's vouchers", "",
        paragraph(report), "", UPPER_BOUND, "",
        f"Made {report.get('made', '')} from {report.get('source', 'the journal')}.", "",
        "## What was scored", "",
        f"- The journal: G/L dates {c['first_date']} to {c['last_date']}. Learned from: the "
        f"{plural(c['older_vouchers'], 'voucher')} up to {c['learned_to']}. Held out: the "
        f"{plural(c['held_out_vouchers'], 'voucher')} from {c['held_out_from']} (the last "
        f"{plural(c['hold_out_months'], 'month')}).",
        f"- Scored: {plural(c['vouchers'], 'voucher')}, {plural(c['lines'], 'coding line')}, of "
        f"{plural(report['suppliers'], 'supplier')}. The payable and tax lines are JD Edwards' own, not coding.",
        f"- Left out: {plural(c['affiliates_left_out'], 'voucher')} of an affiliate, {c['unbalanced_left_out']:,} "
        f"that do not balance, {c['undated_left_out']:,} with no G/L date.",
        "", "## The score", "",
        "A line is right when both its object.subsidiary and its business unit are the ones keyed. \"Coded with no "
        "one picking\": AP Coder was sure enough to put the account in itself; the other lines wait for a person, "
        "with its suggestions beside them.", "",
        _table(STAT_HEAD, [_stat_row("all lines", report["all"]), *(_stat_row(name, s) for name, s in groups)]),
    ]  # fmt: skip
    if not report["by_class"]:
        lines += ["Expense or balance-sheet accounts are not told apart: the business unit master loaded here does "
                  "not say which business units are which (their type, IS or BS).", ""]  # fmt: skip
    lines += ["## By business unit", "",
              _table(["business unit", *STAT_HEAD[1:]],
                     [_stat_row(f"{u['unit']} {u['name']}".strip(), u) for u in report["by_unit"]])]  # fmt: skip
    lines += ["## The suppliers with the most misses", ""]
    if report["worst_suppliers"]:
        lines += [f"A miss: the first suggestion is not the keyed account. The {SUPPLIERS_SHOWN} suppliers with the "
                  f"most; every miss is a row of {MISSES}.", "",
                  _table(["supplier number", "name", "lines", "right", "misses"],
                         [[s["number"], s["name"], f"{s['lines']:,}",
                           f"{s['right']:,} ({_pct(s['right'], s['lines'])})", f"{s['misses']:,}"]
                          for s in report["worst_suppliers"]])]  # fmt: skip
    else:
        lines += ["None: the first suggestion of every line is the keyed account.", ""]
    lines += [
        "## What the sure rule would do at other bars", "",
        "AP Coder codes a line with no one picking when one account has a large enough share of the supplier's "
        "lines, of enough lines. Each cell: the lines that bar would have coded and, of those, the ones coded wrong. "
        "Counted on the lines whose first suggestion comes from the supplier's own coding.", "",
        _table(["one account's share of the supplier's lines", *(f"{n} lines or more" for n in GRID_LINES)],
               [[f"{100 * share:.0f}%" + (" or more" if share < 1 else ""),
                 *(f"{grid[(share, n)]['coded']:,} coded, {grid[(share, n)]['wrong']:,} wrong" for n in GRID_LINES)]
                for share in shares]),
    ]  # fmt: skip
    return "\n".join(lines).rstrip("\n") + "\n"


def misses_csv(rows: list[dict[str, Any]]) -> bytes:
    """A row for each line whose first suggestion is not the keyed account: both accounts and why."""
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\r\n")
    writer.writerow(MISS_COLUMNS)
    for r in rows:
        if r["first_right"]:
            continue
        writer.writerow(csv_row([
            r["supplier"], r["supplier_name"], r["gl_date"], r["remark"], f"{r['amount']:.2f}", r["truth"],
            (r["top"] or [""])[0], r["why"], "yes" if r["coded"] else "no", "yes" if r["in_top3"] else "no",
        ]))  # fmt: skip
    return buf.getvalue().encode("utf-8-sig")


def figures(report: dict[str, Any]) -> dict[str, Any]:
    """The figures with no supplier's name in them."""
    return {key: value for key, value in report.items() if key != "worst_suppliers"}


def run(store: Any, journal: str | Path | None = None, out: str | Path | None = None, *,
        hold_out: int = HOLD_OUT_MONTHS) -> tuple[dict[str, Any], Path]:  # fmt: skip
    """Score the last ``hold_out`` months of the journal (the file, else the one loaded in ``store``) and write the
    three files; returns (the report, the folder written to). ``ExportError`` says what to do when it cannot."""
    read, source = the_journal(store, journal)
    rows, counts = simulate(store, read, hold_out)
    if not rows:
        raise ExportError(f"The last {plural(hold_out, 'month')} of the journal (from {counts['held_out_from']}) "
                          "hold no voucher with a coding line to score.")  # fmt: skip
    report = summarize(rows, counts, {u.code: u.name for u in store.business_units()})
    report["made"] = dt.datetime.now().isoformat(timespec="minutes").replace("T", " ")
    report["source"] = source
    folder = Path(out) if out else Path(store.path).parent / FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    (folder / REPORT).write_text(render_markdown(report), encoding="utf-8")
    (folder / MISSES).write_bytes(misses_csv(rows))
    (folder / FIGURES).write_text(json.dumps(figures(report), indent=1) + "\n", encoding="utf-8")
    return report, folder
