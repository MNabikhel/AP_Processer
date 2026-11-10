"""Last year's coding out of JD Edwards: report R09321 "Transaction Journal" saved as CSV, read as it comes.

The file is a printed report, not a table: five title rows, a header split over two rows, rows of 3 to 84 cells,
the page header again every page, total rows at the end and no document number. So:

* a data row is a row with a "." in its account cell; every other row is a title, a page header or a total
* a voucher is a row whose first cell is filled (the document type) plus the rows after it whose first three cells
  are blank; its only identity is the number of its first row
* a line is one account string with a debit or a credit: a coding line AP chose, or a payable or tax line JD Edwards
  made itself, told apart by the account's object. Those objects are a company's own account numbers, so the caller
  passes them
* the columns are found by the header's words, and are the report's standard positions when it has no header

Nothing is stored and nothing is refused here: ``Journal.notes`` says in plain words what was read and what was
left out, and ``Journal.totals.differ`` that the lines do not add up to the file's ``Grand Total`` row (the caller
decides what to do about it).

Entry points: ``read_journal`` (a ``Journal``), ``looks_like_journal`` and ``coding_lines``.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable
from dataclasses import dataclass, field

from ..core.chart import SEPARATOR, clean, join_account, split_account, split_code
from ..core.csvio import sniff_delimiter
from ..core.headers import header_key
from ..core.parsing import parse_amount, parse_date
from ..core.ui import plural

REPORT = "R09321"
CODING, PAYABLE, TAX = "coding", "payable", "tax"
# The report's standard columns, used when the file has no header row to find them by.
POSITIONS = {"doc_type": 0, "company": 1, "gl_date": 2, "account": 3, "description": 7, "debit": 9, "credit": 11,
             "remark": 13, "supplier": 15, "name": 16}  # fmt: skip
# The header's words, read with ``header_key``. "Document / Number" and "Address / Number" are split over two rows,
# so both columns are headed "Number" on the row that names the others: see ``_positions``.
HEADER_WORDS = {"company": "co", "gl_date": "gldate", "account": "accountnumber", "description": "description",
                "debit": "debitamount", "credit": "creditamount", "remark": "remark", "name": "name"}  # fmt: skip
_NUMBER_HEADERS = ("number", "documentnumber", "addressnumber")
_HEAD = ("doc_type", "company", "gl_date")  # filled on a voucher's first row, blank on the rows that continue it
HEADER_ROWS = 30  # the header is looked for in the first rows only
_HEAD_BYTES = 16384  # enough of a file to recognise it by
SHOWN = 5  # how many rows or vouchers a note names before "and N more"


@dataclass(frozen=True, slots=True)
class PayableObjects:
    """The objects of the payable lines JD Edwards adds to a voucher (a company's own account numbers): the trade
    payable ones, and the intercompany ones, which make a voucher an affiliate's."""

    trade: tuple[str, ...]
    intercompany: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Line:
    """One row of a voucher: an account string with a debit (positive) or a credit (negative)."""

    row: int  # its row in the file, from 1
    account: str  # "0880133142.7340.2210", the report's padding gone
    business_unit: str
    object: str
    subsidiary: str  # blank, digits or letters
    description: str  # the account's name as the report prints it (24 characters)
    amount: float  # debit - credit
    remark: str  # AP's words (30 characters)
    kind: str  # CODING, PAYABLE or TAX


@dataclass(frozen=True, slots=True)
class Voucher:
    """A voucher's first row and the rows that continue it."""

    row: int  # its first row in the file, from 1: its only identity (the report prints no document number)
    doc_type: str
    company: str
    gl_date: str  # YYYY-MM-DD; blank when it could not be read
    supplier_number: str
    supplier_name: str  # as the report prints it (26 characters)
    lines: tuple[Line, ...]
    gross: float  # what the supplier is owed: minus the payable lines
    tax: float  # the tax lines
    affiliate: bool  # a payable line is an intercompany one
    credit: bool  # the gross is negative
    balanced: bool  # debits = credits within a cent
    both_columns: bool  # a line has an amount in the debit and in the credit column


@dataclass(frozen=True, slots=True)
class Totals:
    """What was read, and the control total: the lines' two columns against the file's ``Grand Total`` row."""

    vouchers: int
    lines: int
    coding: int
    payable: int
    tax: int
    first_date: str  # the period covered: the first and last G/L date, YYYY-MM-DD
    last_date: str
    debit: float  # the debit column of the lines read
    credit: float
    file_debit: float | None  # the Grand Total row's; None when the file has none (cut short?)
    file_credit: float | None
    differ: bool  # the lines do not add up to the Grand Total row: rows are missing or misread


@dataclass(frozen=True, slots=True)
class Journal:
    """A journal file as read: plain data, ``dataclasses.asdict`` makes it JSON."""

    vouchers: tuple[Voucher, ...]
    notes: tuple[str, ...]  # for a person: what was read, what was left out and why
    totals: Totals


@dataclass(frozen=True, slots=True)
class CodingLine:
    """A line AP chose, with what its voucher says about it: what the coding is learned from."""

    line: Line
    voucher_row: int
    supplier_number: str
    supplier_name: str
    gl_date: str
    voucher_coding_lines: int  # 1: the whole voucher went to this account


@dataclass(slots=True)
class _Draft:
    """A voucher while its rows are being read."""

    row: int
    doc_type: str
    company: str
    gl_date: str
    lines: list[Line] = field(default_factory=list)
    supplier_number: str = ""
    supplier_name: str = ""
    net: int = 0  # debits - credits, in cents
    both_columns: bool = False


@dataclass(slots=True)
class _Seen:
    """What the rows held besides the vouchers' lines."""

    titles: int = 0
    total_rows: int = 0
    unreadable: list[int] = field(default_factory=list)
    debit: int = 0  # the two columns of the lines read, in cents
    credit: int = 0
    grand: tuple[int, int] | None = None  # the Grand Total row's two amounts, in cents
    no_header: bool = False


def _rows(data: bytes) -> list[list[str]]:
    """The file's rows as they are, each with its own number of cells: UTF-8 (with or without a mark) or Windows'
    cp1252, comma or semicolon, Excel's quoting (a name with a comma is in quotes)."""
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("cp1252", errors="replace")
    try:
        return list(csv.reader(io.StringIO(text, newline=""), delimiter=sniff_delimiter(text)))
    except csv.Error as exc:
        raise ValueError("This file could not be read as a CSV file. Save the report as CSV and try again.") from exc


def _positions(rows: list[list[str]]) -> tuple[dict[str, int], int | None]:
    """({field: its column}, the header row's index): the header is the first row naming the account number and the
    remark, and each column is where its words are. Two columns are headed "Number" ("Document" and "Address" are
    on the row above): the one before the account is the document type's, the one after it the supplier number's.
    With no header: the standard positions, and None."""
    for at, row in enumerate(rows[:HEADER_ROWS]):
        keys = [header_key(cell) for cell in row]
        if HEADER_WORDS["account"] not in keys or HEADER_WORDS["remark"] not in keys:
            continue
        found = {name: keys.index(word) for name, word in HEADER_WORDS.items() if word in keys}
        numbers = [i for i, key in enumerate(keys) if key in _NUMBER_HEADERS]
        before = [i for i in numbers if i < found["account"]]
        after = [i for i in numbers if i > found["account"]]
        if before:
            found["doc_type"] = before[0]
        if after:
            found["supplier"] = after[-1]
        return {**POSITIONS, **found}, at
    return dict(POSITIONS), None


def _cell(row: list[str], at: int) -> str:
    return row[at].strip() if at < len(row) else ""


def _cents(text: str) -> int | None:
    """An amount cell in cents (".26" is 26); 0 when blank, None when it is not an amount."""
    if not text:
        return 0
    amount = parse_amount(text)
    return None if amount is None else round(amount * 100)


def _line(n: int, row: list[str], pos: dict[str, int], kinds: dict[str, str]) -> tuple[Line, int, int] | None:
    """A data row as (line, debit, credit), the amounts in cents; None when its account or its amounts can't be
    read. An amount in both columns is kept: the line is the difference (zero when they are the same)."""
    business_unit, code = split_account(_cell(row, pos["account"]))
    obj, subsidiary = split_code(code)
    debit_text, credit_text = _cell(row, pos["debit"]), _cell(row, pos["credit"])
    debit, credit = _cents(debit_text), _cents(credit_text)
    if not (business_unit and obj and (debit_text or credit_text)) or debit is None or credit is None:
        return None
    description, remark, amount = _cell(row, pos["description"]), _cell(row, pos["remark"]), (debit - credit) / 100
    account, kind = join_account(business_unit, code), kinds.get(obj, CODING)
    return Line(n, account, business_unit, obj, subsidiary, description, amount, remark, kind), debit, credit


def _not_data(row: list[str], pos: dict[str, int], seen: _Seen) -> None:
    """Counts a row that is no voucher line: a total row (amounts under the two columns; the ``Grand Total`` one is
    kept for the control total), or a title or page header."""
    texts = [_cell(row, pos["debit"]), _cell(row, pos["credit"])]
    debit, credit = (_cents(text) for text in texts)
    if any(texts) and debit is not None and credit is not None:
        seen.total_rows += 1
        if any(header_key(cell) == "grandtotal" for cell in row):
            seen.grand = (debit, credit)
    elif any(cell.strip() for cell in row):
        seen.titles += 1


def _voucher(draft: _Draft, intercompany: frozenset[str]) -> Voucher:
    gross = -sum(round(line.amount * 100) for line in draft.lines if line.kind == PAYABLE)
    tax = sum(round(line.amount * 100) for line in draft.lines if line.kind == TAX)
    return Voucher(
        row=draft.row, doc_type=draft.doc_type, company=draft.company, gl_date=draft.gl_date,
        supplier_number=draft.supplier_number, supplier_name=draft.supplier_name, lines=tuple(draft.lines),
        gross=gross / 100, tax=tax / 100, credit=gross < 0, balanced=abs(draft.net) <= 1,
        affiliate=any(line.kind == PAYABLE and line.object in intercompany for line in draft.lines),
        both_columns=draft.both_columns,
    )  # fmt: skip


def read_journal(data: bytes, *, payable_objects: PayableObjects, tax_objects: Iterable[str]) -> Journal:
    """The vouchers of a Transaction Journal saved as CSV. ``payable_objects`` and ``tax_objects``: the objects of
    the lines AP did not choose (trade and intercompany payable; the GST clearing account). Every voucher is kept,
    also one that does not balance or pays an affiliate: it is marked, and ``coding_lines`` leaves it out.
    Raises ValueError when the file is not a CSV at all."""
    rows = _rows(data)
    pos, header = _positions(rows)
    start = 0 if header is None else header + 1
    intercompany = frozenset(clean(o) for o in payable_objects.intercompany)
    kinds = {clean(o): TAX for o in tax_objects}
    kinds |= {clean(o): PAYABLE for o in (*payable_objects.trade, *intercompany)}
    seen = _Seen(titles=sum(1 for row in rows[:start] if any(cell.strip() for cell in row)), no_header=header is None)
    drafts: list[_Draft] = []
    for n, row in enumerate(rows[start:], start=start + 1):
        if SEPARATOR not in _cell(row, pos["account"]):
            _not_data(row, pos, seen)
            continue
        doc_type, company, gl_date = (_cell(row, pos[name]) for name in _HEAD)
        if doc_type:
            date = parse_date(gl_date, day_first=False)
            drafts.append(_Draft(n, doc_type, company, date.isoformat() if date else ""))
        read = _line(n, row, pos, kinds)
        if read is None or not drafts or not doc_type and (company or gl_date):
            seen.unreadable.append(n)  # no amount, before the first voucher, or neither a first row nor a next one
            continue
        line, debit, credit = read
        draft = drafts[-1]
        draft.lines.append(line)
        draft.net += debit - credit
        draft.both_columns = draft.both_columns or bool(debit and credit)
        draft.supplier_number = draft.supplier_number or _cell(row, pos["supplier"])
        draft.supplier_name = draft.supplier_name or _cell(row, pos["name"])
        seen.debit += debit
        seen.credit += credit
    vouchers = tuple(_voucher(draft, intercompany) for draft in drafts if draft.lines)
    totals = _totals(vouchers, seen)
    return Journal(vouchers, tuple(_notes(vouchers, totals, seen)), totals)


def _totals(vouchers: tuple[Voucher, ...], seen: _Seen) -> Totals:
    kinds = [line.kind for voucher in vouchers for line in voucher.lines]
    dates = sorted(voucher.gl_date for voucher in vouchers if voucher.gl_date)
    grand = seen.grand
    return Totals(
        vouchers=len(vouchers), lines=len(kinds),
        coding=kinds.count(CODING), payable=kinds.count(PAYABLE), tax=kinds.count(TAX),
        first_date=dates[0] if dates else "", last_date=dates[-1] if dates else "",
        debit=seen.debit / 100, credit=seen.credit / 100,
        file_debit=grand[0] / 100 if grand else None, file_credit=grand[1] / 100 if grand else None,
        differ=grand is not None and grand != (seen.debit, seen.credit),
    )  # fmt: skip


def _first(items: list[str]) -> str:
    """ "8, 12 and 3 more": the first few of a long list."""
    more = len(items) - SHOWN
    return ", ".join(items[:SHOWN]) + (f" and {more:,} more" if more > 0 else "")


def _notes(vouchers: tuple[Voucher, ...], totals: Totals, seen: _Seen) -> list[str]:
    """What a person should know about the file before it is used, in plain words."""
    notes = []
    if seen.no_header:
        notes.append("No header row was found, so the report's standard column positions were used.")
    period = f", G/L dates {totals.first_date} to {totals.last_date}" if totals.first_date else ""
    notes.append(f"Read: {plural(totals.vouchers, 'voucher')} with {plural(totals.lines, 'line')}{period}.")
    notes.append(
        f"The lines by kind: {totals.coding:,} coding (what AP chose), {totals.payable:,} payable and "
        f"{totals.tax:,} tax (what JD Edwards added)."
    )
    if seen.titles or seen.total_rows:
        notes.append(
            f"Skipped: {plural(seen.titles, 'title or page header row')} and {plural(seen.total_rows, 'total row')}."
        )
    undated = [str(voucher.row) for voucher in vouchers if not voucher.gl_date]
    if undated:
        notes.append(f"{plural(len(undated), 'voucher')} with no G/L date that could be read: row {_first(undated)}.")
    affiliates = sum(voucher.affiliate for voucher in vouchers)
    if affiliates:
        notes.append(f"{plural(affiliates, 'voucher')} with an intercompany payable line (an affiliate's).")
    credits = sum(voucher.credit for voucher in vouchers)
    if credits:
        notes.append(f"{plural(credits, 'voucher')} with a negative amount (a credit).")
    unbalanced = [f"row {v.row} ({' '.join((v.supplier_number, v.supplier_name)).strip() or 'no supplier'})"
                  for v in vouchers if not v.balanced]  # fmt: skip
    if unbalanced:
        notes.append(
            f"{plural(len(unbalanced), 'voucher')} where the debits and the credits are not the same amount "
            f"(kept and marked): {_first(unbalanced)}."
        )
    both = [str(voucher.row) for voucher in vouchers if voucher.both_columns]
    if both:
        notes.append(
            f"{plural(len(both), 'voucher')} with a line that has an amount in both the debit and the credit column "
            f"(kept and marked): row {_first(both)}."
        )
    if seen.unreadable:
        rows = [str(n) for n in seen.unreadable]
        notes.append(
            f"{plural(len(rows), 'row')} left out, because the account, the amount or the voucher it belongs to "
            f"could not be read: row {_first(rows)}."
        )
    notes.append(_control_note(totals))
    return notes


def _control_note(totals: Totals) -> str:
    read = f"debits {totals.debit:,.2f} and credits {totals.credit:,.2f}"
    if totals.file_debit is None:
        return (
            f"The file has no Grand Total row, so the lines read ({read}) could not be checked against the report's "
            "own total. The file may be cut short."
        )
    if totals.differ:
        return (
            f"Warning: the lines read do not add up to the report's Grand Total. The lines read: {read}. The Grand "
            f"Total row: debits {totals.file_debit:,.2f} and credits {totals.file_credit:,.2f}. Rows are missing "
            "from this file or could not be read."
        )
    return f"The lines read add up to the report's Grand Total: {read}."


def looks_like_journal(data: bytes) -> bool:
    """True for a Transaction Journal saved as CSV: its first cell is the report's number, or its header row is
    there (only the start of the file is read)."""
    try:
        rows = _rows(data[:_HEAD_BYTES])
    except ValueError:
        return False
    named = any(_cell(row, 0).upper() == REPORT for row in rows[:1])
    return named or _positions(rows)[1] is not None


def coding_lines(
    journal: Journal, *, include_affiliates: bool = False, include_unbalanced: bool = False
) -> list[CodingLine]:
    """The lines to learn from: the coding lines of the vouchers, without those of an affiliate's voucher or of one
    that does not balance unless asked for. Each carries its voucher's supplier, G/L date and how many coding lines
    the voucher has; its ``line.remark`` is what AP wrote."""
    found: list[CodingLine] = []
    for voucher in journal.vouchers:
        if voucher.affiliate and not include_affiliates or not voucher.balanced and not include_unbalanced:
            continue
        coding = [line for line in voucher.lines if line.kind == CODING]
        found += [
            CodingLine(line, voucher.row, voucher.supplier_number, voucher.supplier_name, voucher.gl_date, len(coding))
            for line in coding
        ]
    return found
