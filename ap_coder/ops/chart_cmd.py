"""``python -m ap_coder chart import --chart <file> --business-units <file>``: the chart of accounts and the business
unit master loaded from JD Edwards' exports as they come (D-209), from the console.

Either file alone or both (the business units first, so the chart knows which are active). Each replaces what was
imported before; the GL accounts and the cost centers are worked out again from the two. The same file again changes
nothing. What was read, what may be posted to and what was left out is said in plain words (``import_files``, which a
page can call with an upload's bytes).
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from ..core import chart, chart_file
from ..core.store import Store
from ..core.ui import plural

ACTOR = "the console (chart import)"
SHOWN = 10  # the business units held back for their name that are listed one by one


def add_parser(sub: Any) -> None:
    p = sub.add_parser("chart", help="The chart of accounts and the business units from JD Edwards' exports: import")
    p.add_argument("action", choices=["import"])
    p.add_argument("--chart", default=None, metavar="FILE",
                   help="the account master's export (.xlsx, as JD Edwards saves it): a business unit, object and "
                   "subsidiary a row")  # fmt: skip
    p.add_argument("--business-units", default=None, metavar="FILE",
                   help="the business unit master's export (.xlsx): which business units are active")  # fmt: skip


def _left_out(skipped: dict[str, int]) -> list[str]:
    return [f"  Left out ({why}): {n:,}." for why, n in skipped.items()]


def _changed(name: str, result: dict[str, Any]) -> str:
    if not (result["added"] or result["updated"] or result["removed"]):
        return f"{name}: the same as already imported, nothing changed."
    return f"{name}: {result['added']:,} added, {result['updated']:,} changed, {result['removed']:,} removed."


def _units_read(read: chart.Rows) -> list[str]:
    closed = [u.code for u in read.rows if u.status not in chart.ACTIVE]
    said = [f"Read {plural(len(read.rows), 'business unit')}: {len(read.rows) - len(closed):,} active, "
            f"{len(closed):,} not active."]  # fmt: skip
    return said + _left_out(read.skipped)


def _chart_read(read: chart.Rows, settings: chart.ChartSettings) -> list[str]:
    accounts = [r for r in read.rows if not r.is_header]
    edits = Counter(r.posting_edit for r in accounts if r.posting_edit not in settings.postable_codes)
    said = [f"Read {plural(len(read.rows), 'row')} of the chart: {plural(len(accounts), 'account')} on "
            f"{plural(len({r.bu for r in read.rows}), 'business unit')}; "
            f"{len(accounts) - sum(edits.values()):,} may be posted to."]  # fmt: skip
    if edits:
        said.append("  Not for posting, by posting edit code: "
                    + ", ".join(f"{n:,} with {code}" for code, n in sorted(edits.items())) + ".")  # fmt: skip
    return said + _left_out(read.skipped)


def _lists(store: Store, results: list[dict[str, Any]]) -> list[str]:
    """What the two lists and the pairs are after the imports, and what is held back and why."""
    said = []
    for table, name in (("gl_accounts", "GL accounts"), ("cost_centers", "Cost centers")):
        added, updated, removed = (sum(r[table][what] for r in results) for what in ("added", "updated", "removed"))
        said.append(f"{name}: {results[-1][table]['now']:,} now ({added:,} added, {updated:,} changed, "
                    f"{removed:,} removed).")  # fmt: skip
    pairs = store.account_pairs()
    if pairs is None:
        return [*said, "No chart of accounts is imported yet: the accounts and business units are not checked "
                "together until it is."]  # fmt: skip
    tally = pairs.tally()
    said.append(f"Accounts a line may be coded to: {tally[chart.OK]:,} (a business unit and its account).")
    if tally[chart.BU_INACTIVE]:
        closed = pairs.closed_units()
        said.append(f"  Not used: {plural(tally[chart.BU_INACTIVE], 'account')} on a business unit that is not "
                    f"active ({', '.join(closed[:5])}{' …' if len(closed) > 5 else ''}).")  # fmt: skip
    named = list(pairs.skipped_units().items())
    for bu, word in named[:SHOWN]:
        said.append(f"  Never offered: business unit {bu}, whose name says {word}.")
    if len(named) > SHOWN:
        said.append(f"  Never offered: {len(named) - SHOWN:,} more business units, for their name.")
    if pairs.settings.never_offered:
        said.append("  Never offered: the accounts on the never-offered list "
                    f"({', '.join(pairs.settings.never_offered)}).")  # fmt: skip
    if tally[chart.RESERVED]:
        said.append(f"  In all, {plural(tally[chart.RESERVED], 'account')} may be posted to but "
                    f"{'is' if tally[chart.RESERVED] == 1 else 'are'} never offered: a line coded to one by hand is "
                    "flagged for a person to confirm.")  # fmt: skip
    return said


def import_files(store: Store, chart_source: str | Path | bytes | None = None,
                 units_source: str | Path | bytes | None = None, actor: str | None = None) -> list[str]:  # fmt: skip
    """Import the business unit master and the chart of accounts (either or both; a path, or a file's bytes) and say
    what was done, a line each. ``chart.ChartError`` for a file that cannot be read and ``ValueError`` for one that
    would leave no account to code to: nothing is changed then, of either file."""
    units = chart_file.read_business_units(units_source) if units_source is not None else None
    rows = chart_file.read_chart(chart_source) if chart_source is not None else None
    said: list[str] = []
    results: list[dict[str, Any]] = []
    with store.transaction():
        if units is not None:
            results.append(store.import_business_units(units.rows, actor=actor))
            said += [*_units_read(units), _changed("Business units", results[-1])]
        if rows is not None:
            results.append(store.import_chart(rows.rows, actor=actor))
            said += [*_chart_read(rows, store.chart_settings()), _changed("Chart of accounts", results[-1])]
    return said + _lists(store, results)


def main(args: argparse.Namespace) -> int:
    if not args.chart and not args.business_units:
        print("Name the file to import: --chart <file> for the chart of accounts, --business-units <file> for the "
              "business units, or both.", file=sys.stderr)  # fmt: skip
        return 2
    for path in (args.chart, args.business_units):
        if path and not Path(path).is_file():
            print(f"There is no file {path}. Nothing was changed.", file=sys.stderr)
            return 1
    if args.chart:
        print("Reading the chart of accounts (a chart of 15,000 rows takes 10 to 20 seconds)...", flush=True)
    try:
        said = import_files(Store(args.db), args.chart, args.business_units, actor=ACTOR)
    except ValueError as exc:  # (a ChartError too: the file cannot be read as what it should be)
        print(f"Not imported: {exc}", file=sys.stderr)
        return 1
    for line in said:
        print(line)
    return 0
