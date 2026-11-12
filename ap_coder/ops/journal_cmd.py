"""``python -m ap_coder journal import FILE``, ``journal list`` and ``journal forget ID|all``: last year's coding
loaded from JD Edwards' Transaction Journal (report R09321 saved as CSV), from the console.

A journal is looked at first and loaded second. ``look`` reads a file's bytes and says, in plain words and writing
nothing: the period, the vouchers, the coding lines, the payable and tax lines left out (which objects were taken
for them, and why), the affiliates' vouchers and those that do not balance (kept, not learned from), the suppliers
the vendor master does not have, the account strings the chart of accounts does not have or does not let be posted
to, and the control total. ``load`` does the same and then keeps the journal (``Store.import_journal``), unless
something stands in the way: the lines read not adding up to the report's own total stops it unless it is asked to
go on anyway. The Learning page's "Teach from past coding" calls the same two with an upload's bytes.

The payable and tax objects are a company's own account numbers. They are, in this order: the ones given (the
command's options, the page's boxes); the ones saved when the last journal was loaded; the ones worked out from the
file itself (``erp/journal.suggest_objects``).
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..core import chart
from ..core.store import Store
from ..core.store.journal import supplier_key
from ..core.tax_setup import RECOVERABLE
from ..core.ui import plural
from ..erp import journal as jr

ACTOR = "the console (journal import)"
SHOWN = 5  # the suppliers or accounts a line names before "and N more"
# What a look at a file finds (``Look.state``) and what a load did (``load``'s first value).
READY, DIFFERS, SAME_FILE, REFUSED, DONE = "ready", "differs", "same_file", "refused", "done"
NAMES = {"trade": "trade payable", "intercompany": "intercompany payable", "tax": "tax"}
ORIGINS = {"given": "as given", "saved": "as saved with the last journal loaded",
           "guessed": "worked out from this file"}  # fmt: skip
NOT_A_JOURNAL = ("This file is not a Transaction Journal (report R09321) saved as CSV: its first cell is not R09321 "
                 "and it has no header row with Account Number and Remark.")  # fmt: skip
STATUS_WORDS = {chart.NOT_IN_CHART: "not in the chart", chart.NOT_POSTABLE: "may not be posted to",
                chart.BU_INACTIVE: "on a business unit that is not active",
                chart.RESERVED: "never offered"}  # fmt: skip


@dataclass(frozen=True)
class Look:
    """A journal file as it would be loaded, nothing written. ``state``: ``READY``; ``DIFFERS`` (the lines read do
    not add up to the report's total: loaded only when asked to anyway); ``SAME_FILE`` (this very file is loaded
    already); ``REFUSED``. ``problem`` says why it is not ``READY``, in plain words."""

    name: str
    hash: str  # the SHA-256 of the file's bytes
    journal: jr.Journal | None  # None: the file could not be read as a journal
    objects: dict[str, tuple[str, ...]]  # {"trade", "intercompany", "tax"}: the objects it was read with
    said: tuple[str, ...]  # the summary, a line each (none when the file could not be read: ``problem`` says it)
    state: str
    problem: str = ""
    brief: tuple[str, ...] = ()  # the summary in three lines: what was read, what is learned, what to check


def _first(items: list[str]) -> str:
    more = len(items) - SHOWN
    return ", ".join(items[:SHOWN]) + (f" and {more:,} more" if more > 0 else "")


def objects_of(value: Any) -> tuple[str, ...]:
    """Object accounts as given, a list or a text ("2110, 2115": commas, semicolons or spaces between): each once,
    in order."""
    parts = [value] if isinstance(value, str) else list(value)
    return tuple(dict.fromkeys(o for part in parts for o in str(part).replace(",", " ").replace(";", " ").split()))


def _objects(store: Store, data: bytes, given: dict[str, Any],
             saved: dict[str, tuple[str, ...]]) -> tuple[dict[str, tuple[str, ...]], list[str]]:  # fmt: skip
    """The payable and tax objects to read the file with, and what to say of where each came from: given, saved
    with the last journal loaded (``saved``; empty: none is), or worked out from the file (then with why)."""
    found: dict[str, tuple[str, ...]] = {}
    origin = {}
    for name in NAMES:
        if given.get(name) is not None:
            found[name], origin[name] = objects_of(given[name]), "given"
        elif saved["trade"]:
            found[name], origin[name] = saved[name], "saved"
    why: tuple[str, ...] = ()
    if len(found) < len(NAMES):
        mapped = [chart.split_code(t.gl_code)[0] for t in store.tax_treatments().values()
                  if t.gl_code and t.treatment == RECOVERABLE]  # fmt: skip
        payable, tax, why = jr.suggest_objects(data, mapped)
        guessed = {"trade": payable.trade, "intercompany": payable.intercompany, "tax": tax}
        for name in NAMES:
            if name not in found:
                found[name], origin[name] = guessed[name], "guessed"
    said = ["The payable and tax lines are JD Edwards' own, not AP's coding. They are told by their object:"]
    for name, words in NAMES.items():
        objects = ", ".join(found[name]) or "none"
        said.append(f"  The {words} {'object' if len(found[name]) == 1 else 'objects'}: {objects} "
                    f"({ORIGINS[origin[name]]}).")  # fmt: skip
        if origin[name] == "guessed":  # ("Trade payable: object 2110 closes..." without its first words)
            reasons = [line.partition(": ")[2] for line in why if line.lower().startswith(words)]
            said += [f"    {reason[:1].upper()}{reason[1:]}" for reason in reasons]
    return found, said


def _read(store: Store, data: bytes, given: dict[str, Any]) -> tuple[jr.Journal, dict[str, tuple[str, ...]], list[str]]:
    """The journal read with its objects (``_objects``). Saved objects none of which is on a line of this file (the
    last journal was another company's) are passed over for those worked out from the file."""
    saved = store.journal_objects()
    for use in (saved, dict.fromkeys(NAMES, ())):
        objects, taken = _objects(store, data, given, use)
        journal = jr.read_journal(data, payable_objects=jr.PayableObjects(objects["trade"], objects["intercompany"]),
                                  tax_objects=objects["tax"])  # fmt: skip
        if _trade_lines(journal, objects) or given.get("trade") is not None or not use["trade"]:
            break
    return journal, objects, taken


def _trade_lines(journal: jr.Journal, objects: dict[str, tuple[str, ...]]) -> int:
    """How many lines of the journal are a trade payable object's."""
    return sum(1 for voucher in journal.vouchers for line in voucher.lines
               if line.kind == jr.PAYABLE and line.object in objects["trade"])  # fmt: skip


def _suppliers_said(store: Store, lines: list[jr.CodingLine]) -> str:
    names = {supplier_key(line.supplier_number): f"{line.supplier_number} {line.supplier_name}".strip()
             for line in lines}  # fmt: skip
    unnumbered = names.pop("", None)
    said = f"Suppliers: {len(names):,} with coding lines that are learned from"
    said += " (and vouchers with no supplier number)." if unnumbered is not None else "."
    known = {supplier_key(erp_id) for _, erp_id in store.master_vendor_ids() if supplier_key(erp_id)}
    if not known:
        return (f"{said} The vendor master has no supplier number (ERP ID) yet, so they could not be looked up in "
                "it: import JD Edwards' supplier master as well.")  # fmt: skip
    missing = [names[key] for key in sorted(names) if key not in known]
    if not missing:
        return f"{said} All are in the vendor master, by supplier number (its ERP ID)."
    return (f"{said} {len(missing):,} of them {'is' if len(missing) == 1 else 'are'} not in the vendor master, by "
            f"supplier number (its ERP ID): {_first(missing)}. Their lines are kept under the number.")  # fmt: skip


def _accounts_said(store: Store, lines: list[jr.CodingLine]) -> list[str]:
    accounts = sorted({line.line.account for line in lines})
    said = f"Accounts: {plural(len(accounts), 'account string')} on the coding lines that are learned from."
    pairs = store.account_pairs()
    if pairs is None:
        return [f"{said} No chart of accounts is imported, so they were not checked against one."]
    by_status: dict[str, list[str]] = {}
    for account in accounts:
        status = pairs.status(*chart.split_account(account))
        if status != chart.OK:
            by_status.setdefault(status, []).append(account)
    if not by_status:
        return [f"{said} All are in the chart of accounts and may be posted to."]
    return [said] + [f"  {len(found):,} {STATUS_WORDS[status]}: {_first(found)}."
                     for status, found in by_status.items()]  # fmt: skip


def _kept_said(store: Store, journal: jr.Journal, learned: list[jr.CodingLine], same_file: bool) -> list[str]:
    vouchers = {line.voucher_row for line in learned}
    affiliates = sum(1 for v in journal.vouchers if v.affiliate)
    unbalanced = sum(1 for v in journal.vouchers if not v.balanced and not v.affiliate)
    said = [f"Learned from: {plural(len(learned), 'coding line')} on {plural(len(vouchers), 'voucher')}."]
    parts = [plural(affiliates, "affiliate's voucher", "affiliates' vouchers")] if affiliates else []
    if unbalanced:
        parts.append(f"{plural(unbalanced, 'voucher')} that {'does' if unbalanced == 1 else 'do'} not balance")
    if parts:
        said.append(f"Kept but not learned from: {' and '.join(parts)}.")
    already = 0 if same_file else store.journal_already(journal)
    if already:
        said.append(f"{plural(already, 'voucher')} of this file {'is' if already == 1 else 'are'} there already "
                    "from a file loaded before: kept with this file too, never counted twice.")  # fmt: skip
    return said


def _brief(store: Store, journal: jr.Journal, learned: list[jr.CodingLine]) -> tuple[str, ...]:
    """The summary in three lines, for a page to show above the rest: what was read (and whether it matches the
    report's own total), what will be learned from, and what to check (``said`` has each in full)."""
    totals = journal.totals
    read = (f"Read {plural(totals.vouchers, 'voucher')} with {plural(totals.lines, 'line')}, G/L dates "
            f"{totals.first_date} to {totals.last_date}")  # fmt: skip
    if totals.file_debit is None:
        read += ": the file has no Grand Total row to check them against."
    elif totals.differ:
        read += ": they do not add up to the report's Grand Total."
    else:
        read += ", matching the report's Grand Total."
    suppliers = {supplier_key(line.supplier_number) for line in learned} - {""}
    learn = (f"To learn from: {plural(len(learned), 'coding line')} on "
             f"{plural(len({line.voucher_row for line in learned}), 'voucher')}, of "
             f"{plural(len(suppliers), 'supplier')}." if learned
             else "Nothing to learn from: no line of AP's coding was read.")  # fmt: skip
    check = []
    unbalanced = sum(1 for v in journal.vouchers if not v.balanced and not v.affiliate)
    if unbalanced:
        check.append(f"{plural(unbalanced, 'voucher')} that {'does' if unbalanced == 1 else 'do'} not balance "
                     "(kept, not learned from)")  # fmt: skip
    known = {supplier_key(erp_id) for _, erp_id in store.master_vendor_ids() if supplier_key(erp_id)}
    if learned and not known:
        check.append("no supplier master is loaded to look the suppliers up in")
    elif missing := len(suppliers - known):
        check.append(f"{plural(missing, 'supplier')} not in the vendor master")
    pairs = store.account_pairs()
    if learned and pairs is None:
        check.append("no chart of accounts is loaded to check the accounts against")
    elif pairs is not None:
        wrong = sum(1 for account in {line.line.account for line in learned}
                    if pairs.status(*chart.split_account(account)) != chart.OK)  # fmt: skip
        if wrong:
            check.append(f"{plural(wrong, 'account')} not in the chart, or not offered for coding")
    said = (f"To check: {'; '.join(check)}." if check
            else "Nothing to check: every supplier and every account is known.")  # fmt: skip
    return read, learn, said


def _state(store: Store, digest: str, journal: jr.Journal, objects: dict[str, tuple[str, ...]]) -> tuple[str, str]:
    """(``Look.state``, ``Look.problem``) of a journal as read."""
    before = next((f for f in store.journal_files() if f["hash"] == digest), None)
    if before is not None:
        who = f" by {before['loaded_by']}" if before["loaded_by"] else ""
        return SAME_FILE, (f"This file is loaded already ({before['loaded_at'][:10]}{who}): there is nothing to "
                           "add. To load it again, with other objects for instance, forget it first.")  # fmt: skip
    if not journal.vouchers:
        return REFUSED, "No voucher could be read from this file."
    pairs = store.account_pairs()
    units, codes = (pairs.business_units(), pairs.offered_codes()) if pairs is not None else ((), ())
    cut = jr.cut_by_excel(journal, units, {chart.split_code(code)[0] for code in codes})
    if cut:  # saved again through Excel, which reads "0021061010.2110" as a number (B-2346)
        return REFUSED, ("This file was saved through Excel and lost its leading zeros: export it again from JD "
                         "Edwards and load that file without opening it in Excel. "
                         f"{plural(len(cut), 'account')} cut short: {_first(cut)}.")  # fmt: skip
    if not _trade_lines(journal, objects):
        named = f" ({', '.join(objects['trade'])})" if objects["trade"] else ""
        return REFUSED, (f"No line of this file has a trade payable object{named}, so JD Edwards' own lines would be "
                         "taken for AP's coding. Name the trade payable object.")  # fmt: skip
    if journal.totals.differ:
        return DIFFERS, ("The lines read do not add up to the report's Grand Total, so rows are missing or were "
                         "misread. Pull the report again; or load it anyway, knowing it is not whole.")  # fmt: skip
    return READY, ""


def look(store: Store, data: bytes, name: str, *, trade: Any = None, intercompany: Any = None,
         tax: Any = None) -> Look:  # fmt: skip
    """What loading the journal file ``data`` (its bytes; ``name``: the file's name) would do, nothing written.
    ``trade``, ``intercompany``, ``tax``: the objects of the payable and tax lines, each a list (empty: none) or
    None to take the saved ones or, with none saved, those worked out from the file."""
    digest = hashlib.sha256(data).hexdigest()
    none: dict[str, tuple[str, ...]] = dict.fromkeys(NAMES, ())
    if not jr.looks_like_journal(data):
        return Look(name, digest, None, none, (), REFUSED, NOT_A_JOURNAL)
    try:
        journal, objects, taken = _read(store, data, {"trade": trade, "intercompany": intercompany, "tax": tax})
    except ValueError as exc:
        return Look(name, digest, None, none, (), REFUSED, str(exc))
    learned = jr.coding_lines(journal)
    state, problem = _state(store, digest, journal, objects)
    said = [f"{name}: JD Edwards' Transaction Journal (R09321).", *journal.notes, *taken,
            *_kept_said(store, journal, learned, state == SAME_FILE)]  # fmt: skip
    if learned:
        said += [_suppliers_said(store, learned), *_accounts_said(store, learned)]
    return Look(name, digest, journal, objects, tuple(said), state, problem, _brief(store, journal, learned))


def load(store: Store, data: bytes, name: str, *, trade: Any = None, intercompany: Any = None, tax: Any = None,
         anyway: bool = False, actor: str | None = None) -> tuple[str, list[str]]:  # fmt: skip
    """Look at the journal file ``data`` (``look``) and keep it. Returns (what was done, what to say, a line each:
    the summary, then what was done or why nothing was). What was done: ``DONE`` (loaded); ``SAME_FILE`` (this very
    file is loaded already: nothing to do); ``DIFFERS`` (not loaded: the lines read do not add up to the report's
    total, and ``anyway`` was not given); ``REFUSED`` (not loaded, for the reason said). The objects a journal was
    loaded with are saved for the next one."""
    seen = look(store, data, name, trade=trade, intercompany=intercompany, tax=tax)
    said = list(seen.said)
    if seen.state == SAME_FILE:
        return SAME_FILE, [*said, seen.problem]
    if seen.journal is None or seen.state == REFUSED or (seen.state == DIFFERS and not anyway):
        return seen.state, [*said, f"Not loaded: {seen.problem}"]
    try:
        result = store.import_journal(seen.journal, source_name=name, source_hash=seen.hash, actor=actor,
                                      objects=seen.objects)  # fmt: skip
    except ValueError as exc:
        return REFUSED, [*said, f"Not loaded: {exc}"]
    store.save_journal_objects(**seen.objects, actor=actor)
    return DONE, [*said, loaded_line(result)]


def loaded_line(result: dict[str, Any]) -> str:
    """What a load did, in one sentence (``Store.import_journal``'s result)."""
    said = (f"Loaded {result['name']}: {plural(result['vouchers'], 'voucher')} and "
            f"{plural(result['lines'], 'line')} kept, {plural(result['learned_lines'], 'coding line')} to learn "
            "from")  # fmt: skip
    if result["already"]:
        said += (f"; {plural(result['already'], 'voucher')} already there from another file "
                 f"({'it is' if result['already'] == 1 else 'they are'} not counted twice)")  # fmt: skip
    return said + "."


def listed(store: Store) -> list[str]:
    """The journals loaded, a line each."""
    files = store.journal_files()
    if not files:
        return ["No journal is loaded."]
    said = [f"{plural(len(files), 'journal')} loaded:"]
    for f in files:
        counted = "" if f["counted"] == f["vouchers"] else f" ({f['counted']:,} of them in no other file)"
        said.append(f"  {f['id']}: {f['name']}, G/L dates {f['first_date']} to {f['last_date']}, "
                    f"{plural(f['vouchers'], 'voucher')}{counted}, {plural(f['learned_lines'], 'coding line')} "
                    f"to learn from, loaded {f['loaded_at'].replace('T', ' ')[:16]}"
                    f"{' by ' + f['loaded_by'] if f['loaded_by'] else ''}.")  # fmt: skip
    return said


def forget(store: Store, which: str, actor: str | None = None) -> tuple[bool, str]:
    """Forget the journal file numbered ``which`` (``listed``), or every one for "all": (done or not, what to say)."""
    if which.strip().lower() == "all":
        removed = store.forget_journals(actor=actor)
        return True, f"Forgot every journal: {plural(removed, 'voucher')} removed."
    found = next((f for f in store.journal_files() if str(f["id"]) == which.strip()), None)
    if found is None:
        return False, f"There is no journal numbered {which}. `journal list` shows the ones loaded."
    removed = store.forget_journal(found["id"], actor=actor)
    return True, f"Forgot {found['name']}: {plural(removed, 'voucher')} removed."


def add_parser(sub: Any) -> None:
    p = sub.add_parser("journal", help="Last year's coding from JD Edwards' Transaction Journal (R09321 saved as "
                       "CSV): import, list, forget")  # fmt: skip
    p.add_argument("action", choices=["import", "list", "forget"])
    p.add_argument("target", nargs="?", default=None, metavar="FILE|ID|all",
                   help="import: the journal's CSV file; forget: a journal's number (journal list), or "
                   "all")  # fmt: skip
    for name, words in NAMES.items():
        p.add_argument(f"--{name}", nargs="*", default=None, metavar="OBJ",
                       help=f"import: the {words} object accounts (their lines are not AP's coding). Left out: the "
                       "ones saved with the last journal, else worked out from the file")  # fmt: skip
    p.add_argument("--anyway", action="store_true",
                   help="import: load the file though its lines do not add up to the report's Grand Total")  # fmt: skip
    p.add_argument("--dry-run", action="store_true", help="import: say what would be loaded, and load nothing")


def _import(args: argparse.Namespace, store: Store) -> int:
    if not args.target:
        print("Name the journal's file: journal import FILE.", file=sys.stderr)
        return 2
    path = Path(args.target)
    if not path.is_file():
        print(f"There is no file {path}. Nothing was loaded.", file=sys.stderr)
        return 1
    given = {name: getattr(args, name) for name in NAMES}
    if args.dry_run:
        seen = look(store, path.read_bytes(), path.name, **given)
        state, said = seen.state, [*seen.said, *([seen.problem] if seen.problem else [])]
        said.append("Nothing was loaded (--dry-run).")
    else:
        state, said = load(store, path.read_bytes(), path.name, **given, anyway=args.anyway, actor=ACTOR)
        if state == DIFFERS:
            said.append("To load it anyway, add --anyway.")
    for line in said:
        print(line)
    return 1 if state == REFUSED or (state == DIFFERS and not args.dry_run) else 0


def main(args: argparse.Namespace) -> int:
    store = Store(args.db)
    if args.action == "import":
        return _import(args, store)
    if args.action == "list":
        for line in listed(store):
            print(line)
        return 0
    if not args.target:
        print("Name the journal to forget: its number (journal list), or all.", file=sys.stderr)
        return 2
    done, said = forget(store, args.target, actor=ACTOR)
    print(said, file=sys.stdout if done else sys.stderr)
    return 0 if done else 1
