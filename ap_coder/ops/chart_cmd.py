"""``python -m ap_coder chart import|settings|show|rebuild``: the chart of accounts and the business unit master
loaded from JD Edwards' exports as they come (D-209), from the console.

* ``import --chart <file> --business-units <file> [--anyway]``: either file alone or both (the business units
  first, so the chart knows which are active). A chart exported without its posting edit or subsidiary column is
  imported only with ``--anyway``, and says so each time it is shown. Each replaces what was imported before; the
  GL accounts and the cost centers are worked out again from the two, even when the file is the one already
  imported. What was read, what may be posted to, what was left out, what left the lists and what a person had
  typed that was set aside is said in plain words (``import_files``, which a page can call with an upload's
  bytes).
* ``settings [--postable …] [--never-offered …] [--skip-words …] [--home-business-unit …] [--labels …]``: the chart's
  settings, shown, and changed for the ones named (an empty value clears one).
* ``show``: the chart imported, its settings, and how many accounts a line may be coded to.
* ``rebuild``: the GL accounts and cost centers worked out again from the chart as it is (a list changed by hand).
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
SHOWN = 10  # the codes of a kind, or the business units held back for their name, that are named one by one
# ``chart settings``' options, each with the setting it writes.
OPTIONS = {"postable": "postable_codes", "never_offered": "never_offered", "skip_words": "skip_words",
           "home_business_unit": "home_business_unit", "labels": "account_labels"}  # fmt: skip


def add_parser(sub: Any) -> None:
    p = sub.add_parser("chart", help="The chart of accounts and the business units from JD Edwards' exports: import, "
                       "settings, show, rebuild")  # fmt: skip
    p.add_argument("action", choices=["import", "settings", "show", "rebuild"])
    options = (
        ("--chart", "FILE", "import: the account master's export (.xlsx, as JD Edwards saves it): a business unit, "
         "object and subsidiary a row"),
        ("--business-units", "FILE", "import: the business unit master's export (.xlsx): which business units are "
         "active"),
        ("--postable", "CODES", 'settings: the posting edit codes AP may post to besides blank, e.g. "M" ("": blank '
         "only)"),
        ("--never-offered", "PATTERNS", 'settings: accounts never suggested or listed, with commas between: "*.7701" '
         '(that subsidiary of any object), "4110" (that object), "0880133142.*.*" (a whole business unit)'),
        ("--skip-words", "WORDS", "settings: a business unit whose name carries one is never offered (DO NOT USE, "
         "DNU)"),
        ("--home-business-unit", "BUSINESS_UNIT", "settings: where a line is suggested to go when nothing says "
         "otherwise"),
        ("--labels", "NAMES", "settings: what the three parts are called (Business unit, Object, Subsidiary)"),
    )  # fmt: skip
    for option, value, about in options:
        p.add_argument(option, default=None, metavar=value, help=about)
    p.add_argument("--anyway", action="store_true",
                   help="import: import a chart exported without its posting edit or subsidiary column (without "
                        "the posting edit column every account is offered)")  # fmt: skip


def _left_out(skipped: dict[str, int]) -> list[str]:
    return [f"  Left out ({why}): {n:,}." for why, n in skipped.items()]


def _changed(name: str, result: dict[str, Any]) -> str:
    if not (result["added"] or result["updated"] or result["removed"]):
        return f"{name}: the same as already imported."
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


def _some(codes: list[str], total: int | None = None) -> str:
    """The first ``SHOWN`` codes, and how many more there are."""
    more = (len(codes) if total is None else total) - min(len(codes), SHOWN)
    return ", ".join(codes[:SHOWN]) + (f" and {more:,} more" if more > 0 else "")


def _kept(kinds: dict[str, list[str]]) -> str:
    """What was set aside or put back, in words: "the meals tick of 5450; the manager of 0731066135"."""
    words = (("meals", "the meals tick of"), ("categories", "the category typed for"), ("managers", "the manager of"))
    return "; ".join(f"{said} {_some(kinds[kind])}" for kind, said in words if kinds.get(kind))


def lists_lines(results: list[dict[str, Any]]) -> list[str]:
    """What happened to the GL accounts and the cost centers: how many each holds now, what left them, what a person
    had typed that was set aside (and is put back when the chart has the code again), and what still points at an
    account that is gone."""
    said = []
    for table, name in (("gl_accounts", "GL accounts"), ("cost_centers", "Cost centers")):
        added, updated, removed = (sum(r[table][what] for r in results) for what in ("added", "updated", "removed"))
        said.append(f"{name}: {results[-1][table]['now']:,} now ({added:,} added, {updated:,} changed, "
                    f"{removed:,} removed).")  # fmt: skip
    for table, name in (("gl_accounts", "GL accounts"), ("cost_centers", "cost centers")):
        left = sorted({code for r in results for code in r["left"][table]})
        if left:
            said.append(f"  Left the {name}: {_some(left, sum(r[table]['removed'] for r in results))}.")
    kept = (("set_aside", "Set aside, and put back when the chart has them again"), ("brought_back", "Put back"))
    for key, words in kept:
        kinds = {kind: [code for r in results for code in r[key][kind]] for kind in results[-1][key]}
        if any(kinds.values()):
            said.append(f"  {words}: {_kept(kinds)}.")
    for what in results[-1]["unmapped"]:
        said.append(f"  To set right: {what}, which is no longer in the list.")
    return said


def tally_lines(store: Store) -> list[str]:
    """How many accounts a line may be coded to, and what is held back and why."""
    pairs = store.account_pairs()
    if pairs is None:
        return ["No chart of accounts is imported yet: the accounts and business units are not checked together "
                "until it is."]  # fmt: skip
    tally = pairs.tally()
    said = [f"Accounts a line may be coded to: {tally[chart.OK]:,} (a business unit and its account)."]
    if tally[chart.BU_INACTIVE]:
        closed = pairs.closed_units()
        said.append(f"  Not used: {plural(tally[chart.BU_INACTIVE], 'account')} on a business unit that is not "
                    f"active ({', '.join(closed[:5])}{' …' if len(closed) > 5 else ''}).")  # fmt: skip
    named = [(bu, word) for bu, word in pairs.skipped_units().items() if pairs.has_accounts(bu)]
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
    return said + chart.without_lines(store.chart_without())  # a column the export did not have: said every time


def settings_lines(store: Store) -> list[str]:
    """The chart's settings, a line each."""
    settings, pairs = store.chart_settings(), store.account_pairs()
    more = [code for code in settings.postable_codes if code]
    home = settings.home_business_unit
    name = pairs.name_of(home) if pairs is not None and home else ""
    return [
        f"Posting edit codes AP may post to: blank {'and ' + ', '.join(more) if more else 'only'}.",
        f"Never offered: {', '.join(settings.never_offered) or 'none'}.",
        f"Skip words: {', '.join(settings.skip_words)}.",
        f"Home business unit: {home + (f' ({name})' if name else '') if home else 'not set'}.",
        f"The three parts of an account are called: {', '.join(settings.account_labels)}.",
    ]


def import_files(store: Store, chart_source: str | Path | bytes | None = None,
                 units_source: str | Path | bytes | None = None, actor: str | None = None,
                 anyway: bool = False) -> list[str]:  # fmt: skip
    """Import the business unit master and the chart of accounts (either or both; a path, or a file's bytes) and say
    what was done, a line each. ``chart.ChartError`` for a file that cannot be read (``chart.ColumnsMissing`` for a
    chart without its posting edit or subsidiary column, unless ``anyway``) and ``ValueError`` for one that would
    leave no account to code to: nothing is changed then, of either file."""
    units = chart_file.read_business_units(units_source) if units_source is not None else None
    rows = chart_file.read_chart(chart_source, anyway) if chart_source is not None else None
    said: list[str] = []
    results: list[dict[str, Any]] = []
    with store.transaction():
        if units is not None:
            results.append(store.import_business_units(units.rows, actor=actor))
            said += [*_units_read(units), _changed("Business units", results[-1])]
        if rows is not None:
            results.append(store.import_chart(rows.rows, actor=actor, without=rows.without))
            said += [*_chart_read(rows, store.chart_settings()), _changed("Chart of accounts", results[-1])]
    return said + lists_lines(results) + tally_lines(store)


def _import(args: argparse.Namespace) -> int:
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
        said = import_files(Store(args.db), args.chart, args.business_units, actor=ACTOR, anyway=args.anyway)
    except chart.ColumnsMissing as exc:
        offered = ": every account will be offered" if "posting_edit" in exc.fields else ""
        print(f"Not imported: {exc} To import it anyway, add --anyway{offered}.", file=sys.stderr)
        return 1
    except ValueError as exc:  # (a ChartError too: the file cannot be read as what it should be)
        print(f"Not imported: {exc}", file=sys.stderr)
        return 1
    for line in said:
        print(line)
    return 0


def _settings(args: argparse.Namespace, store: Store) -> int:
    typed = {setting: getattr(args, option) for option, setting in OPTIONS.items() if getattr(args, option) is not None}
    if typed:
        wanted = chart.ChartSettings.read({**store.chart_settings().saved(), **typed})
        pairs, home = store.account_pairs(), wanted.home_business_unit
        if "home_business_unit" in typed and home and pairs is not None and not pairs.has_business_unit(home):
            print(f"There is no business unit {home} in the chart of accounts or the business unit master. Nothing "
                  "was changed.", file=sys.stderr)  # fmt: skip
            return 1
        last = store.events(actions=["accounts_imported"], limit=1)
        try:
            store.save_chart_settings(wanted, actor=ACTOR)
        except ValueError as exc:
            print(f"Not saved: {exc}", file=sys.stderr)
            return 1
        followed = store.events(actions=["accounts_imported"], limit=1)
    for line in settings_lines(store):
        print(line)
    if typed and followed and followed != last:  # the lists followed the settings: their event says how
        for line in lists_lines([followed[0]["detail"]]):
            print(line)
    return 0


def _show(store: Store) -> int:
    rows, units = store.chart_accounts(), store.business_units()
    if rows:
        accounts = [r for r in rows if not r.is_header]
        print(f"Chart of accounts: {plural(len(accounts), 'account')} on "
              f"{plural(len({r.bu for r in rows}), 'business unit')}; {plural(len(units), 'business unit')} in the "
              "master.")  # fmt: skip
    for line in settings_lines(store):
        print(line)
    for table, name in (("gl_accounts", "GL accounts"), ("cost_centers", "Cost centers")):
        print(f"{name}: {len(store.list_accounts(table)):,}.")
    for line in tally_lines(store):
        print(line)
    return 0


def _rebuild(store: Store) -> int:
    try:
        result = store.rebuild_account_lists(actor=ACTOR)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if not any(result[table][what] for table in ("gl_accounts", "cost_centers") for what in ("added", "updated",
                                                                                              "removed")):  # fmt: skip
        print("The GL accounts and cost centers already are what the chart gives: nothing changed.")
        return 0
    for line in lists_lines([result]):
        print(line)
    return 0


def main(args: argparse.Namespace) -> int:
    if args.action == "import":
        return _import(args)
    store = Store(args.db)
    if args.action == "settings":
        return _settings(args, store)
    return _show(store) if args.action == "show" else _rebuild(store)
