"""Spend analysis from approved invoices, and a full data download for Excel / Power BI.

Spend is the cost to the company: each expense line's amount plus any sales tax that is not recovered
(recoverable GST/HST/QST is left out, as it is claimed back). Credit notes reduce it. Amounts stay in
each invoice's currency; currencies are never added together.

Entry points: ``parts`` (the Spend page's lines, from the invoices' summary), ``total_by``,
``by_month_and_category``, ``currencies``, ``in_cad``, and ``workbook`` (the full data download).
"""

from __future__ import annotations

import datetime as dt
import io
import json
import warnings
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..coding.tax import PLACE_OF_SUPPLY
from ..core.reference_data import ReferenceData
from ..core.safe import FORMULA_START, xlsx_row
from ..core.store import APPROVED, FAILED, Store
from ..core.store.summary import broken, whole
from .exports import distribution


@dataclass
class SpendLine:
    invoice_id: int
    month: str  # YYYY-MM of the invoice date
    vendor: str
    gl_code: str
    cost_center: str
    amount: float
    currency: str
    # A row of ``parts`` sums one invoice's lines on one GL account and cost center (in the order of the first of
    # them): ``sizes`` sums the sizes of their amounts (``currencies``), ``cents`` their amounts each rounded to the
    # cent and ``amounts`` lists them, for ``in_cad`` (None: a single line, ``amount``).
    sizes: float | None = None
    cents: float | None = None
    amounts: tuple[float, ...] | None = None


def spend_lines(doc: dict[str, Any], treatments: dict[str, Any]) -> list[tuple[str, str, float]]:
    """(GL account, cost center, amount) of each cost line of an approved invoice's coding ``doc``: its expense
    lines, and non-recoverable tax posted to its own GL account (a cost too)."""
    out = []
    for e in distribution(doc, treatments):  # an invoice approved by an older version: built again
        if e.get("kind") != "expense" and not e.get("non_recoverable_tax"):
            continue
        out.append((str(e.get("gl_code") or ""), str(e.get("cost_center") or ""), float(e.get("amount") or 0)))
    return out


def spend_parts(doc: dict[str, Any], treatments: dict[str, Any]) -> list[tuple[Any, ...]]:
    """``spend_lines`` by GL account and cost center, in the order of the first line of each, as the summary keeps
    them (``summary_spend``): (GL account, cost center, amount, sizes, cents, amounts as JSON or None). The amounts
    are summed line by line as the report sums them; each line's amount is listed for an invoice not in CAD, which
    ``in_cad`` converts line by line."""
    parts: dict[tuple[str, str], list[float]] = {}
    for gl, cc, amount in spend_lines(doc, treatments):
        parts.setdefault((gl, cc), []).append(amount)
    listed = str(doc.get("currency") or "CAD") != "CAD"
    out = []
    for (gl, cc), amounts in parts.items():
        total = sizes = cents = 0.0
        for a in amounts:
            total += a
            sizes += abs(a)
            cents += round(a, 2)
        out.append((gl, cc, total, sizes, cents, json.dumps(amounts) if listed else None))
    return out


def parts(store: Store, start: dt.date, end: dt.date) -> list[SpendLine]:
    """The cost lines of approved invoices dated from ``start`` to ``end`` (inclusive), by invoice, GL account and
    cost center (``spend_parts``), from the invoices' summary (D-110): what the Spend page totals. ``total_by``,
    ``by_month_and_category``, ``currencies`` and ``in_cad`` give what they give for ``lines``."""
    first, last = start.isoformat(), end.isoformat()
    rows = store.summary_fetch(
        "SELECT s.id, substr(s.f_date, 1, 7) month, s.p_vendor, s.p_currency, p.gl_code, p.cost_center, p.amount, "
        "p.sizes, p.cents, p.amounts FROM invoice_summary s JOIN summary_spend p ON p.invoice_id = s.id "
        f"WHERE s.f_date BETWEEN ? AND ? AND +s.status = ? AND s.{whole('spend')} ORDER BY s.id, p.part",
        (first, last, APPROVED),
    )
    out = [
        SpendLine(r["id"], r["month"], r["p_vendor"], r["gl_code"], r["cost_center"], r["amount"], r["p_currency"],
                  r["sizes"], r["cents"], tuple(json.loads(r["amounts"])) if r["amounts"] else None)
        for r in rows
    ]  # fmt: skip
    out += [line for line in _broken_lines(store, first, last)]
    return out


def _broken_lines(store: Store, first: str, last: str) -> list[SpendLine]:
    """``lines`` of the approved invoices the summary could not read (their JSON): read again from the invoice."""
    ids = [r["id"] for r in store.summary_rows(f"{broken('spend')} AND +status = ?", (APPROVED,), "id")]
    return [line for line in _lines_of(store.invoice_columns(("id", "final_output"), ids=ids), store, first, last)
            ] if ids else []  # fmt: skip


def lines(store: Store, start: dt.date, end: dt.date) -> list[SpendLine]:
    """Expense postings of approved invoices dated from ``start`` to ``end`` (inclusive)."""
    return _lines_of(store.invoice_columns(("id", "final_output"), APPROVED), store, start.isoformat(),
                     end.isoformat())  # fmt: skip


def _lines_of(rows: list[dict[str, Any]], store: Store, first: str, last: str) -> list[SpendLine]:
    treatments = store.tax_treatments()
    out = []
    for r in rows:
        doc = r["final_output"] or {}
        date = str(doc.get("invoice_date") or "")
        if not (first <= date <= last):
            continue
        vendor, currency = str(doc.get("vendor_name") or ""), str(doc.get("currency") or "CAD")
        out += [SpendLine(r["id"], date[:7], vendor, gl, cc, amount, currency)
                for gl, cc, amount in spend_lines(doc, treatments)]  # fmt: skip
    return out


def currencies(rows: list[SpendLine]) -> list[str]:
    """Currencies by total spend, largest first."""
    totals: dict[str, float] = defaultdict(float)
    for r in rows:
        totals[r.currency] += abs(r.amount) if r.sizes is None else r.sizes
    return sorted(totals, key=lambda c: -totals[c])


def in_cad(rows: list[SpendLine], rates: dict[str, float]) -> list[SpendLine]:
    """The lines in CAD at ``rates`` (CAD per unit); lines in a currency without a rate are left out."""
    return [
        SpendLine(r.invoice_id, r.month, r.vendor, r.gl_code, r.cost_center, _in_cad(r, rates[r.currency]), "CAD")
        for r in rows
        if r.currency in rates
    ]


def _in_cad(r: SpendLine, rate: float) -> float:
    """``r``'s amount in CAD, line by line rounded to the cent (a row of ``parts``: each of its lines)."""
    if r.amounts is not None:
        return sum(round(a * rate, 2) for a in r.amounts)
    if r.cents is not None and rate == 1.0:  # a * 1.0 is a: each line rounded to the cent, summed
        return r.cents
    return round(r.amount * rate, 2)


def total_by(rows: list[SpendLine], key: str, currency: str) -> list[tuple[str, float, int]]:
    """(value of ``key``, total, invoices) for one currency, largest total first."""
    totals: dict[str, float] = defaultdict(float)
    invoices: dict[str, set[int]] = defaultdict(set)
    for r in rows:
        if r.currency == currency:
            value = getattr(r, key)
            totals[value] += r.amount
            invoices[value].add(r.invoice_id)
    return sorted(((k, round(v, 2), len(invoices[k])) for k, v in totals.items()), key=lambda x: -x[1])


def by_month_and_category(rows: list[SpendLine], currency: str, category: dict[str, str]) -> list[dict[str, Any]]:
    """[{month, category, amount}] for a stacked chart."""
    totals: dict[tuple[str, str], float] = defaultdict(float)
    for r in rows:
        if r.currency == currency:
            totals[(r.month, category.get(r.gl_code) or "Uncategorised")] += r.amount
    return [{"month": m, "category": c, "amount": round(v, 2)} for (m, c), v in sorted(totals.items())]


# --- The Spend page's figures, summed by SQLite --------------------------------------------------------------------
# What the page shows of ``parts`` (``currencies``, then ``total_by``, ``by_month_and_category``...), summed by SQLite
# by GL account, vendor, cost center and month rather than every cost line of the period made a Python object (308,000
# at 100,000 invoices: +500 MB a view). The sums are SQLite's, the same to the cent (every posting line is rounded to
# the cent when coded); what is rounded, ordered or converted is as before. Lines SQLite has no CAD amount for (an
# invoice in another currency, "All, in CAD": converted line by line as ``in_cad`` does) and those of an invoice the
# summary could not read are summed here as before.

_PARTS = ("FROM invoice_summary s JOIN summary_spend p ON p.invoice_id = s.id WHERE s.f_date BETWEEN ? AND ? AND "
          f"+s.status = '{APPROVED}' AND s.{whole('spend')}")  # fmt: skip
_AT = 65536  # a cost line's place in the period's (``parts``' order): invoice * _AT + its part
_LAST = 1 << 62  # the places after every line of the summary (the lines ``_broken_lines`` reads)
BY = {"vendor": "s.p_vendor", "gl_code": "p.gl_code", "cost_center": "p.cost_center"}  # ``total_by``'s keys
# Under a stream of approvals the Spend page's figures and currencies (1.9 s and 0.4 s at 100,000 invoices) are worked
# out again at most this often in each process: a page that only shows them (D-164, D-166).
SETTLE_SECONDS = 30.0


class _Sums:
    """``total_by`` of one key from sums: by value, the amount, the invoices and the place it first came."""

    def __init__(self) -> None:
        self.amount: dict[Any, float] = {}
        self.invoices: dict[Any, int] = {}
        self.first: dict[Any, int] = {}

    def add(self, value: Any, amount: float, invoices: int, first: int) -> None:
        self.amount[value] = self.amount.get(value, 0.0) + amount
        self.invoices[value] = self.invoices.get(value, 0) + invoices
        self.first[value] = min(self.first.get(value, first), first)

    def rows(self) -> list[tuple[Any, float, int]]:
        found = sorted(self.amount, key=self.first.__getitem__)  # in the order they first came, as ``total_by``
        return sorted(((k, round(self.amount[k], 2), self.invoices[k]) for k in found), key=lambda x: -x[1])


def spend_currencies(store: Store, start: dt.date, end: dt.date) -> list[str]:
    """``currencies(parts(store, start, end))``, kept until the invoices' summary changes (D-162), and under a stream
    of changes worked out again at most every ``SETTLE_SECONDS`` (D-166)."""
    return list(store.summary_memo("spend_currencies", lambda: _spend_currencies(store, start, end),
                                   asked=(start, end), settle=SETTLE_SECONDS))  # fmt: skip


def _spend_currencies(store: Store, start: dt.date, end: dt.date) -> list[str]:
    first, last = start.isoformat(), end.isoformat()
    totals = _Sums()
    for r in store.summary_fetch(f"SELECT s.p_currency c, SUM(p.sizes) n, MIN(s.id) at {_PARTS} GROUP BY s.p_currency",
                                 (first, last)):  # fmt: skip
        totals.add(r["c"], r["n"], 0, r["at"])
    for n, line in enumerate(_broken_lines(store, first, last)):
        totals.add(line.currency, abs(line.amount), 0, _LAST + n)
    return sorted(sorted(totals.amount, key=totals.first.__getitem__), key=lambda c: -totals.amount[c])


def figures(store: Store, start: dt.date, end: dt.date, currency: str, category: dict[str, str],
            rates: dict[str, float] | None = None) -> dict[str, Any]:  # fmt: skip
    """What the Spend page shows of ``parts(store, start, end)`` in ``currency``, or with ``rates`` of every line of a
    currency with a rate in CAD (``in_cad``; ``currency`` is then "CAD"): ``lines`` (whether there are any), ``total``
    (rounded to the cent), ``invoices``, ``months``, ``by_month`` (``by_month_and_category`` with ``category``) and
    ``by``: for each key of ``BY``, its ``total_by``. Kept until the invoices' summary changes (D-162), and under a
    stream of changes worked out again at most every ``SETTLE_SECONDS`` (D-166), for these dates, currency,
    categories and rates: read it, don't change it."""
    asked = (start, end, currency, dict(category), None if rates is None else dict(rates))  # (copies)
    return store.summary_memo("spend_figures", lambda: read_figures(store, start, end, currency, category, rates),
                              asked=asked, settle=SETTLE_SECONDS)  # fmt: skip


def read_figures(store: Store, start: dt.date, end: dt.date, currency: str, category: dict[str, str],
                 rates: dict[str, float] | None = None) -> dict[str, Any]:  # fmt: skip
    """``figures`` read now, in one read transaction: an approval meanwhile is in every figure or none."""
    with store.summary_snapshot():
        return _figures(store, start, end, currency, category, rates)


def _figures(store: Store, start: dt.date, end: dt.date, currency: str, category: dict[str, str],
             rates: dict[str, float] | None) -> dict[str, Any]:  # fmt: skip
    first, last = start.isoformat(), end.isoformat()
    # SQLite sums the lines in ``currency``; converted, those in CAD (each line rounded to the cent: ``_in_cad``).
    if rates is None:
        value, where, args = "p.amount", "s.p_currency = ?", (currency,)
    else:
        value, where, args = "p.cents", "s.p_currency = 'CAD' AND p.amounts IS NULL", ()
    sums = {key: _Sums() for key in BY}
    for key, column in BY.items():
        for r in store.summary_fetch(f"SELECT {column} k, SUM({value}) n, COUNT(DISTINCT s.id) i, "
                                     f"MIN(s.id * {_AT} + p.part) at {_PARTS} AND {where} GROUP BY {column}",
                                     (first, last, *args)):  # fmt: skip
            sums[key].add(r["k"], r["n"], r["i"], r["at"])
    by_vendor = sums["vendor"]  # an invoice has one vendor: its sums and invoices are the period's
    total, invoices = sum(by_vendor.amount.values()), sum(by_vendor.invoices.values())
    months: dict[tuple[str, str], float] = defaultdict(float)
    for r in store.summary_fetch(f"SELECT substr(s.f_date, 1, 7) m, p.gl_code g, SUM({value}) n {_PARTS} AND {where} "
                                 "GROUP BY m, g", (first, last, *args)):  # fmt: skip
        months[(r["m"], category.get(r["g"]) or "Uncategorised")] += r["n"]
    # The rest, line by line as before: (its place, its invoice, the line).
    rest = [] if rates is None else [
        (r["id"] * _AT + r["part"], r["id"], SpendLine(r["id"], r["month"], r["p_vendor"], r["gl_code"],
         r["cost_center"], r["amount"], r["p_currency"], r["sizes"], r["cents"],
         tuple(json.loads(r["amounts"])) if r["amounts"] else None))
        for r in store.summary_fetch(
            "SELECT s.id, p.part, substr(s.f_date, 1, 7) month, s.p_vendor, s.p_currency, p.gl_code, p.cost_center, "
            f"p.amount, p.sizes, p.cents, p.amounts {_PARTS} AND NOT ({where}) AND s.p_currency IN "
            "(SELECT value FROM json_each(?)) ORDER BY s.id, p.part", (first, last, json.dumps(sorted(rates))))
    ]  # fmt: skip
    rest += [(_LAST + n, r.invoice_id, r) for n, r in enumerate(_broken_lines(store, first, last))]
    more: set[int] = set()
    counted: set[tuple[str, Any, int]] = set()  # (key, value, invoice) already counted
    for at, invoice, line in rest:
        if rates is None and line.currency != currency or rates is not None and line.currency not in rates:
            continue
        amount = line.amount if rates is None else _in_cad(line, rates[line.currency])
        total += amount
        more.add(invoice)
        for key, s in sums.items():
            v = getattr(line, key)
            s.add(v, amount, (key, v, invoice) not in counted, at)
            counted.add((key, v, invoice))
        months[(line.month, category.get(line.gl_code) or "Uncategorised")] += amount
    return {
        "lines": bool(invoices or more), "total": round(total, 2), "invoices": invoices + len(more),
        "months": sorted({m for m, _ in months}), "by": {key: s.rows() for key, s in sums.items()},
        "by_month": [{"month": m, "category": c, "amount": round(v, 2)} for (m, c), v in sorted(months.items())],
    }  # fmt: skip


def default_period(today: dt.date | None = None) -> tuple[dt.date, dt.date]:
    """The last 12 months, this one included."""
    today = today or dt.date.today()
    start = dt.date(today.year - 1, today.month, 1)
    start = dt.date(start.year + (start.month == 12), start.month % 12 + 1, 1)
    return start, today


def accounts_by_code(reference: ReferenceData | None, cost_centers: bool = False) -> dict[str, dict[str, str]]:
    table = (reference.cost_centers if cost_centers else reference.chart_of_accounts) if reference else None
    return {row[table.key_column]: row for row in table.rows} if table else {}


# --- Full data download -------------------------------------------------------------------------------

INVOICE_COLUMNS = [
    ("id", "AP Coder #"), ("status", "Status"), ("vendor_name", "Vendor"), ("invoice_number", "Invoice #"),
    ("invoice_date", "Invoice date"), ("due_date", "Due date"), ("po_number", "PO #"),
    ("payment_terms", "Terms"), ("currency", "Currency"), ("subtotal", "Subtotal"), ("tax_total", "Tax"),
    ("grand_total", "Total"), ("supplier_province", "Supplier province"), ("ship_to_province", "Place of supply"),
    ("gst_hst_registration_number", "GST/HST #"), ("qst_registration_number", "QST #"),
    ("reviewer", "Approved by"), ("reviewed_at", "Approved at"), ("second_reviewer", "Second approval by"),
    ("second_reviewed_at", "Second approval at"), ("export_batch", "Export batch"), ("created_at", "Processed at"),
]  # fmt: skip


WORKBOOK_BATCH = 500  # the invoices the data workbook reads at a time
WIDTH_ROWS = 200  # the rows of a sheet (its header too) the widths of its columns are worked out from


def workbook(store: Store, reference: ReferenceData | None) -> bytes:
    """Every invoice (not the unreadable ones), its lines and its posting, as an Excel workbook with three
    tables that join on "AP Coder #" (``write_workbook``)."""
    out = io.BytesIO()
    write_workbook(store, reference, out)
    return out.getvalue()


class _Sheet:
    """A sheet of the data workbook in openpyxl's write-only mode, its rows written as they come (a year of invoices'
    cells are never held at once): its first ``WIDTH_ROWS`` held until its columns' widths are known (set before any
    row is written); text a spreadsheet would run as a formula stored as text."""

    def __init__(self, wb: Any, name: str, header: list[str]) -> None:
        self.ws, self.name, self.header, self.columns, self.rows = wb.create_sheet(name), name, header, len(header), 0
        self.held: list[list[Any]] | None = []
        self.append(header)

    def append(self, row: list[Any]) -> None:
        self.rows += 1
        if self.held is None:
            self.ws.append(self._cells(row))
            return
        self.held.append(row)
        if len(self.held) == WIDTH_ROWS:
            self._start()

    def _cells(self, row: list[Any]) -> list[Any]:
        from openpyxl.cell import WriteOnlyCell

        cells = []
        for value in row:
            if isinstance(value, str) and value.startswith(FORMULA_START):  # as ``neutralise_sheet`` stores it
                value = WriteOnlyCell(self.ws, value=value)
                value.data_type, value.quotePrefix = "s", True
            cells.append(value)
        return cells

    def _start(self) -> None:
        from openpyxl.utils import get_column_letter

        held, self.held = self.held or [], None
        for n in range(self.columns):
            width = max(len(str((row[n] if n < len(row) else None) or "")) for row in held)
            self.ws.column_dimensions[get_column_letter(n + 1)].width = min(max(width + 2, 9), 48)
        self.ws.freeze_panes = "A2"
        for row in held:
            self.ws.append(self._cells(row))

    def close(self) -> None:
        from openpyxl.utils import get_column_letter
        from openpyxl.worksheet.filters import AutoFilter
        from openpyxl.worksheet.table import Table, TableColumn, TableStyleInfo

        if self.held is not None:
            self._start()
        if self.rows > 1:  # an Excel table (filters, and a named source for Power BI)
            ref = f"A1:{get_column_letter(self.columns)}{self.rows}"
            # Its columns named by the header, as openpyxl names them from the cells of a sheet it holds.
            columns = [TableColumn(id=n, name=str(h)) for n, h in enumerate(self.header, 1)]
            table = Table(displayName=f"{self.name}Table", ref=ref, autoFilter=AutoFilter(ref=ref),
                          tableColumns=columns)  # fmt: skip
            table.tableStyleInfo = TableStyleInfo(name="TableStyleMedium2", showRowStripes=True)
            with warnings.catch_warnings():  # "you must add table columns manually": they are, above
                warnings.simplefilter("ignore", UserWarning)
                self.ws.add_table(table)


def write_workbook(store: Store, reference: ReferenceData | None, out: Any,
                   step: Callable[[int, int], None] | None = None) -> None:  # fmt: skip
    """``workbook`` written to ``out`` (a path or a binary file), the invoices read ``WORKBOOK_BATCH`` at a time and
    the sheets in openpyxl's write-only mode (at 100,000 invoices: tens of GB of cells otherwise, D-156); ``step(done,
    total)``: after each batch of invoices."""
    from openpyxl import Workbook

    accounts = accounts_by_code(reference)

    def gl_name(code: str) -> str:
        return accounts.get(code, {}).get("description", "")

    def category(code: str) -> str:
        return accounts.get(code, {}).get("category", "")

    columns = ("id", "status", "reviewer", "reviewed_at", "second_reviewer", "second_reviewed_at", "export_batch",
               "created_at", "due_date", "ai_output", "final_output")  # fmt: skip
    ids = [r["id"] for r in store.summary_rows("status != ?", (FAILED,), "id")]
    treatments = store.tax_treatments()
    wb = Workbook(write_only=True)
    sheets = {
        "Invoices": [label for _, label in INVOICE_COLUMNS],
        "Lines": ["AP Coder #", "Vendor", "Invoice #", "Invoice date", "Line", "Description", "Quantity",
                  "Unit price", "Amount", "GL account", "GL name", "GL category", "Cost center", "Currency"],
        "Posting": ["AP Coder #", "Vendor", "Invoice #", "Invoice date", "Kind", "GL account", "GL name",
                    "Cost center", "Description", "Net", "Non-recoverable tax", "Amount", "Currency"],
    }  # fmt: skip
    ws_by_name = {name: _Sheet(wb, name, header) for name, header in sheets.items()}
    for start in range(0, len(ids), WORKBOOK_BATCH):
        for r in store.invoice_columns(columns, ids=ids[start : start + WORKBOOK_BATCH]):
            if r["status"] == FAILED:  # failed since the list was read
                continue
            doc = r["final_output"] or r["ai_output"] or {}
            merged = {**doc, **{k: r[k] for k in r if k not in ("ai_output", "final_output")}}
            # the ship-to printed, else the place of supply worked out when it was coded (pipeline)
            merged["ship_to_province"] = doc.get("ship_to_province") or doc.get(PLACE_OF_SUPPLY) or ""
            ws_by_name["Invoices"].append(xlsx_row([merged.get(k) for k, _ in INVOICE_COLUMNS]))
            head = [r["id"], doc.get("vendor_name"), doc.get("invoice_number"), doc.get("invoice_date")]
            for li in doc.get("line_items") or []:
                gl = li.get("predicted_gl_code") or ""
                ws_by_name["Lines"].append(
                    xlsx_row(
                        [
                            *head,
                            li.get("line_number"),
                            li.get("description"),
                            li.get("quantity"),
                            li.get("unit_price"),
                            li.get("amount"),
                            gl,
                            gl_name(gl),
                            category(gl),
                            li.get("predicted_cost_center"),
                            doc.get("currency"),
                        ]
                    )
                )
            for e in distribution(doc, treatments):
                gl = e.get("gl_code") or ""
                ws_by_name["Posting"].append(
                    xlsx_row(
                        [
                            *head,
                            e.get("kind"),
                            gl,
                            gl_name(gl),
                            e.get("cost_center"),
                            e.get("description"),
                            e.get("net_amount"),
                            e.get("non_recoverable_tax"),
                            e.get("amount"),
                            doc.get("currency"),
                        ]
                    )
                )
        if step is not None:
            step(min(start + WORKBOOK_BATCH, len(ids)), len(ids))
    for sheet in ws_by_name.values():
        sheet.close()
    wb.save(out)
