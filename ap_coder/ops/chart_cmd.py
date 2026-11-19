"""``python -m ap_coder chart import|settings|show|rebuild``: the chart of accounts and the business unit master
loaded from JD Edwards' exports as they come (D-209), from the console.

* ``import --chart <file> [--chart <file> ...] --business-units <file> [--anyway]``: either alone or both (the
  business units first, so the chart knows which are active). A chart as JD Edwards exports it (.xlsx) or as a query
  saves it (.csv, or tab-delimited .txt/.tsv, UTF-8 or UTF-16: streamed from disk, D-272); several --chart files are
  one import, read and checked in full before one swap (a company split across files is loaded with all of them),
  and the summary says the rows read, accepted and rejected (each reason with its first rows) and each posting edit
  code. A load that looks cut short (a company with less than half its accounts, or in fewer files than last time)
  is refused unless --anyway, the chart left as it was. A chart exported without its posting edit or subsidiary
  column is imported only with ``--anyway``, and says so each time it is shown. Each replaces what was imported
  before (a file whose rows name their company, the accounts or business units of those companies alone, the others
  kept: a chart split by company, D-267; ``--whole``: all of it, to start over); the GL accounts and the cost centers
  are worked out again from the two, even when the file is the one already imported. What was read, what may be
  posted to, what was left out, what left the lists and what a person had typed that was set aside is said in plain
  words (``import_files``, which a page can call with an upload's bytes).
* ``settings [--postable …] [--never-offered …] [--skip-words …] [--home-business-unit …] [--labels …]
  [--closed-codes …] [--pilot-companies …]``: the chart's settings, shown, and changed for the ones named (an empty
  value clears one).
* ``show``: the chart imported, its settings, and how many accounts a line may be coded to.
* ``rebuild``: the GL accounts and cost centers worked out again from the chart as it is (a list changed by hand).
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from ..core import chart, chart_file
from ..core.store import Store
from ..core.store.chart import ChartRefused
from ..core.ui import plural

ACTOR = "the console (chart import)"
PROGRESS_EVERY = 250_000  # a chart CSV streamed from disk: how many rows were read, said after each that many
SHOWN = 10  # the codes of a kind, or the business units held back for their name, that are named one by one
# ``chart settings``' options, each with the setting it writes.
OPTIONS = {"postable": "postable_codes", "never_offered": "never_offered", "skip_words": "skip_words",
           "home_business_unit": "home_business_unit", "labels": "account_labels", "closed_codes": "closed_codes",
           "pilot_companies": "pilot_companies"}  # fmt: skip


def add_parser(sub: Any) -> None:
    p = sub.add_parser("chart", help="The chart of accounts and the business units from JD Edwards' exports: import, "
                       "settings, show, rebuild")  # fmt: skip
    p.add_argument("action", choices=["import", "settings", "show", "rebuild"])
    options = (
        ("--chart", "FILE", "import: the account master's export (.xlsx as JD Edwards saves it, or the query's text: "
         ".csv, or tab-delimited .txt/.tsv, UTF-8 or UTF-16): a business unit, object and subsidiary a row. Give it "
         "once a file: a company split across files is loaded with all of them in one import"),
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
        ("--closed-codes", "CODES", "settings: posting edit codes found in the chart and decided never to post to "
         "(a code AP Coder does not know is closed until decided either way)"),
        ("--pilot-companies", "COMPANIES", 'settings: the companies in the pilot, with commas between ("": every '
         "company): an invoice of another company is held, and only their accounts are offered"),
    )  # fmt: skip
    for option, value, about in options:
        p.add_argument(option, default=None, metavar=value, help=about,
                       **({"action": "append"} if option == "--chart" else {}))  # fmt: skip
    p.add_argument("--whole", action="store_true",
                   help="import: replace the whole chart or all the business units with the file (start over), not "
                        "only the companies it names")  # fmt: skip
    p.add_argument("--anyway", action="store_true",
                   help="import: import a chart exported without its posting edit or subsidiary column (without "
                        "the posting edit column every account is offered), or one that looks cut short: a company "
                        "with less than half the accounts it has now, or in fewer files than last time")  # fmt: skip


def _left_out(skipped: dict[str, int]) -> list[str]:
    return [f"  Left out ({why}): {n:,}." for why, n in skipped.items()]


def _changed(name: str, result: dict[str, Any], rows: str = "") -> str:
    """What an import changed; ``rows``: what it counts, when its rows are not all the thing named (the chart's are
    its accounts and each business unit's title row)."""
    named = result.get("companies") or []
    whose = (f" (company {', '.join(named)}'s; the other companies' kept)" if len(named) == 1 else
             f" (companies {', '.join(named)}; the other companies' kept)") if named else ""  # fmt: skip
    if not (result["added"] or result["updated"] or result["removed"]):
        return f"{name}: the same as already imported{whose}."
    return (f"{name}{whose}: {result['added']:,} {rows}added, {result['updated']:,} changed, {result['removed']:,} "
            "removed.")  # fmt: skip


def _units_read(read: chart.Rows) -> list[str]:
    closed = [u.code for u in read.rows if u.status not in chart.ACTIVE]
    said = [f"Read {plural(len(read.rows), 'business unit')}: {len(read.rows) - len(closed):,} active, "
            f"{len(closed):,} not active."]  # fmt: skip
    return said + _left_out(read.skipped)


def _rows_of(numbers: list[int]) -> str:
    return f"row{'s' if len(numbers) > 1 else ''} {', '.join(f'{n:,}' for n in numbers)}"


def _rejected(reads: list[tuple[str, chart.Rows]], staged: Any) -> list[str]:
    """The rows left out, a line for each reason: how many, why, and the first of them (by file, with several)."""
    reasons: dict[str, int] = {}
    shown: dict[str, list[str]] = {}
    for name, read in reads:
        for why, n in read.skipped.items():
            reasons[why] = reasons.get(why, 0) + n
            if read.examples.get(why):
                rows = _rows_of(read.examples[why])
                shown.setdefault(why, []).append(f"{rows} of {name}" if len(reads) > 1 else rows)
    if staged.repeats:
        reasons[chart.REPEATED] = reasons.get(chart.REPEATED, 0) + staged.repeats
        if staged.repeat_rows:
            shown.setdefault(chart.REPEATED, []).append(_rows_of(staged.repeat_rows))
    return [f"  Rejected ({why}): {n:,}" + (f" ({'; '.join(shown[why])}{' …' if n > chart.EXAMPLES else ''})"
            if shown.get(why) else "") + "." for why, n in reasons.items()]  # fmt: skip


def edits_line(edits: Counter[str], settings: chart.ChartSettings) -> str:
    """Each posting edit code of the chart with its accounts and whether AP posts to it (D-272): a code no setting
    decides and JD Edwards' list does not have is "not yet decided", closed until a person decides."""
    undecided = set(settings.undecided(edits))
    said = []
    for code, n in sorted(edits.items()):
        if code in settings.postable_codes:
            what = "posted to"
        elif code in undecided:
            what = "not yet decided: not posted to until decided on Settings → JD Edwards E1 → Accounts AP posts to"
        else:
            what = f"not posted to: {chart.KNOWN_EDITS.get(code, 'decided on Settings')}"
        said.append(f"{code or 'blank'} {n:,} ({what})")
    return "  Posting edit codes: " + "; ".join(said) + "."


def read_lines(reads: list[tuple[str, chart.Rows]], staged: Any, settings: chart.ChartSettings) -> list[str]:
    """What an import read of its files (D-272): the rows under the column names, accepted and rejected (each reason
    with the first of its rows), the accounts, business units and companies, and each posting edit code with its
    accounts and whether AP posts to it. Said at the end of a load, kept with its event for the page."""
    parsed = sum(read.parsed for _name, read in reads)
    rejected = sum(sum(read.skipped.values()) for _name, read in reads) + staged.repeats
    files = (f" in {len(reads)} files ({', '.join(f'{name} {read.parsed:,}' for name, read in reads)})"
             if len(reads) > 1 else "")  # fmt: skip
    postable = sum(n for code, n in staged.edits.items() if code in settings.postable_codes)
    said = [
        f"Read {plural(parsed, 'row')} of the chart{files}: {staged.kept:,} accepted "
        f"({plural(staged.accounts, 'account')} on {plural(staged.units, 'business unit')}), {rejected:,} rejected.",
        f"  {postable:,} of the accounts may be posted to; the others are kept, for their descriptions and to say why "
        "a line may not use one.",
    ]
    said += _rejected(reads, staged)
    if staged.edits:
        said.append(edits_line(staged.edits, settings))
    named = sorted((co, n) for co, n in staged.by_company.items() if co)
    if named:
        said.append(f"  By company: {', '.join(f'{co} {n:,}' for co, n in named[:SHOWN])}"
                    + (f" and {len(named) - SHOWN:,} more" if len(named) > SHOWN else "") + ".")  # fmt: skip
    return said


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
    by_hand = [name for r in results for name in r.get("by_hand", ())]
    if by_hand:  # (D-221: a row changed on the JD Edwards files page keeps its change across a load)
        said.append(f"  Kept as changed by hand here, where the file says otherwise: {_some(by_hand)}.")
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
    if tally[chart.BU_UNKNOWN]:  # (D-272)
        unlisted = pairs.unlisted_units(6)
        said.append(f"  Not offered: {plural(tally[chart.BU_UNKNOWN], 'account')} on a business unit that is not in "
                    f"the business units report ({', '.join(unlisted[:5])}{' …' if len(unlisted) > 5 else ''}): a "
                    "line coded to one is an error to approve anyway, saying why.")  # fmt: skip
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
        f"Posting edit codes decided never to post to: {', '.join(settings.closed_codes) or 'none'}.",
        f"Companies in the pilot: {', '.join(settings.pilot) or 'every company'}.",
    ]


def _chart_parts(sources: list[tuple[Any, str, str]],
                 anyway: bool) -> tuple[list[tuple[str, chart.Rows]], list[tuple[str, Iterable[Any]]]]:  # fmt: skip
    """Each chart file opened (its column names checked before a row is staged): (its name and what it read, as it is
    read; its name and its rows). A text file from disk (``chart_file.TEXT_FILES``: CSV, or tab-delimited) is streamed,
    never held (D-267, D-272)."""
    reads, parts = [], []
    for source, sheet, name in sources:
        if isinstance(source, (str, Path)) and str(source).lower().endswith(chart_file.TEXT_FILES):
            read, rows = chart_file.stream_chart_text(source, anyway)
        else:
            read = chart_file.read_chart(source, anyway, sheet)
            rows = read.rows
        reads.append((name, read))
        parts.append((name, rows))
    return reads, parts


def import_files(store: Store, chart_source: str | Path | bytes | None = None,
                 units_source: str | Path | bytes | None = None, actor: str | None = None,
                 anyway: bool = False, chart_sheet: str = "", units_sheet: str = "",
                 whole: bool = False, every: int = 0,
                 charts: list[tuple[Any, str, str]] | None = None) -> list[str]:  # fmt: skip
    """Import the business unit master and the chart of accounts (either or both; a path, or a file's bytes; each
    from its sheet of that name, ``chart_sheet`` and ``units_sheet``, else its first) and say what was done, a line
    each. ``charts``: several chart files loaded as one ((source, sheet, name) each: a company split across them is
    loaded whole, D-272). ``chart.ChartError`` for a file that cannot be read (``chart.ColumnsMissing`` for a chart
    without its posting edit or subsidiary column, unless ``anyway``) and ``ValueError`` for one that would leave no
    account to code to, or looks cut short (``ChartRefused``, unless ``anyway``; its ``said``: what was read): nothing
    is changed then, of either file. ``whole``: each file replaces all of its table, not only the companies it names
    (D-267). The chart's files are read into a table of their own first, the live chart untouched and the write lock
    not taken (``Store.stage_chart``), then swapped in in one transaction with the business units. A chart saved as
    text (a path ending .csv, .txt or .tsv) is streamed from disk as it is loaded, up to ``chart.STREAMED_MOST`` rows
    a file, never held; ``every``: say how many rows were read after each that many."""
    units = chart_file.read_business_units(units_source, units_sheet) if units_source is not None else None
    if charts is None and chart_source is not None:
        name = chart.FILE if isinstance(chart_source, bytes) else Path(chart_source).name
        charts = [(chart_source, chart_sheet, name)]
    said: list[str] = []
    results: list[dict[str, Any]] = []
    seen = [0]

    def counted(rows: Iterable[Any]) -> Iterable[Any]:  # (how many rows were read, said after each ``every``)
        for row in rows:
            seen[0] += 1
            if seen[0] % every == 0:
                print(f"  {seen[0]:,} rows read...", flush=True)
            yield row

    with store.session():
        staged, read_said, reads = None, [], []
        if charts:
            reads, parts = _chart_parts(charts, anyway)
            staged = store.stage_chart([(name, counted(rows)) for name, rows in parts] if every else parts)
            read_said = read_lines(reads, staged, store.chart_settings())
        try:
            with store.transaction():
                if units is not None:
                    results.append(store.import_business_units(units.rows, actor=actor, whole=whole))
                    said += [*_units_read(units), _changed("Business units", results[-1])]
                if staged is not None:
                    without = tuple(dict.fromkeys(f for _name, read in reads for f in read.without))
                    results.append(store.swap_chart(staged, actor=actor, without=without, whole=whole,
                                                    anyway=anyway, summary=read_said))  # fmt: skip
                    said += [*read_said, _changed("Chart of accounts", results[-1], "rows ")]
        except ChartRefused as exc:
            exc.said = read_said
            raise
    return said + lists_lines(results) + tally_lines(store)


def _import(args: argparse.Namespace) -> int:
    if not args.chart and not args.business_units:
        print("Name the file to import: --chart <file> for the chart of accounts, --business-units <file> for the "
              "business units, or both.", file=sys.stderr)  # fmt: skip
        return 2
    for path in (*(args.chart or ()), args.business_units):
        if path and not Path(path).is_file():
            print(f"There is no file {path}. Nothing was changed.", file=sys.stderr)
            return 1
    if args.chart:
        print("Reading the chart of accounts (15,000 rows take seconds, 3,500,000 a few minutes)...", flush=True)
    charts = [(path, "", Path(path).name) for path in args.chart or ()]
    try:
        said = import_files(Store(args.db), None, args.business_units, actor=ACTOR, anyway=args.anyway,
                            whole=args.whole, every=PROGRESS_EVERY, charts=charts)  # fmt: skip
    except ChartRefused as exc:
        for line in exc.said:
            print(line)
        print(str(exc), file=sys.stderr)
        return 1
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
    if "postable_codes" in typed and not typed["postable_codes"].strip():  # ("": blank only, as the help says)
        typed["postable_codes"] = chart.BLANK_ONLY
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
    (accounts, on_units), units = store.chart_counts(), store.business_units()
    if accounts:
        print(f"Chart of accounts: {plural(accounts, 'account')} on {plural(on_units, 'business unit')}; "
              f"{plural(len(units), 'business unit')} in the master.")  # fmt: skip
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
