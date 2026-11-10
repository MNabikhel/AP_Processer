"""What is loaded of JD Edwards' files, read a page at a time (D-221): the chart of accounts, the business units, the
suppliers, last year's coding (the journal's lines) and the vouchers already in JD Edwards (the register's bills).

A chart is 15,000 rows, a supplier list 20,000, a year's journal 25,000 lines and a register up to 600,000 bills, so
nothing here reads a table whole: the search, the filters, the count and the page are SQLite's (``loaded_page``:
``LIMIT`` and ``OFFSET``), and the download reads on from the last row it gave, a batch at a time
(``loaded_batches``: each view is in the order of a key of its own). A code is given as the text the table keeps, its
leading zeros with it. A row of the three lists a person may change carries what was changed by hand on it
(``by_hand.held``).
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any

from ..chart import SEPARATOR as DOT
from . import by_hand, dialect
from ._base import _in_list
from .chart import _settings

CHART, UNITS, SUPPLIERS, JOURNAL, VOUCHERS = "chart", "units", "suppliers", "journal", "vouchers"
WHATS = (CHART, UNITS, SUPPLIERS, JOURNAL, VOUCHERS)
PAGE = 100  # rows a page shows
BATCH = 2000  # rows the download reads at a time
WORDS_MOST = 6  # words of a search that are looked for (each must be found in the row)
CHOICES_MOST = 60  # a filter with more values than this is typed, not picked from a list
BLANK = "(blank)"  # a filter's value for rows whose column is empty (an empty value is no filter)
BY_HAND = "by_hand"  # the filter of the rows a person changed by hand
TEXT, CODE, AMOUNT, WHOLE, FLAG = "text", "code", "amount", "whole", "flag"


@dataclass(frozen=True)
class Column:
    """One column of a view: ``kind`` says how it is shown (a code stays text; an amount and a whole number are
    numbers; a flag is 1 or 0)."""

    key: str
    label: str
    sql: str
    kind: str = TEXT


@dataclass(frozen=True)
class Filter:
    """A column a view is filtered by, on one of its values (``named``: what a value is called)."""

    key: str
    label: str
    sql: str
    named: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class View:
    """One of the five lists: ``source`` and ``where`` (the rows it is of), its columns, the columns a search looks
    in, ``key`` (the columns it is ordered by, unique together) and, for a list a person may change, ``table`` and
    ``row_key`` (its name and a row's key in ``by_hand.held``)."""

    what: str
    noun: str
    source: str
    where: str
    columns: tuple[Column, ...]
    search: tuple[str, ...]
    key: tuple[str, ...]
    filters: tuple[Filter, ...] = ()
    table: str = ""
    row_key: str = "''"


_CHART_KEY = "c.bu || char(9) || c.object || char(9) || c.subsidiary"  # (``by_hand.chart_key``, in SQL)
_GL_CODE = f"CASE WHEN c.subsidiary != '' THEN c.object || '{DOT}' || c.subsidiary ELSE c.object END"
_DOCUMENTS = (f"(SELECT {dialect.group_concat('x.document', chr(39) + ', ' + chr(39))} FROM erp_vouchers x WHERE "
              "x.vendor_key = e.vendor_key AND x.number_key = e.number_key AND x.total = e.total AND "
              "x.document != '')")  # fmt: skip


def _view(conn: sqlite3.Connection, what: str) -> View:
    """The view called ``what`` (``ValueError`` for another name), as this database has it: the chart's three parts
    under the names the chart's settings give them, the suppliers with whichever columns their table has."""
    if what == CHART:
        bu, obj, subsidiary = _settings(conn).account_labels
        return View(
            CHART, "row", f"chart_accounts c LEFT JOIN gl_accounts g ON g.code = {_GL_CODE}", "1 = 1",
            (Column("bu", bu, "c.bu", CODE), Column("object", obj, "c.object", CODE),
             Column("subsidiary", subsidiary, "c.subsidiary", CODE),
             Column("description", "Description", "c.description"),
             Column("lod", "Level of detail", "c.lod", WHOLE),
             Column("posting_edit", "Posting edit code", "c.posting_edit", CODE),
             Column("category", "Under (title account)", "c.category"),
             Column("listed", "In the GL accounts", "g.code IS NOT NULL", FLAG),
             Column("meals", "Meals account", "COALESCE(g.meals, 0)", FLAG)),
            ("c.bu", "c.object", "c.subsidiary", "c.description", f"c.bu || '{DOT}' || {_GL_CODE}"),
            ("c.bu", "c.object", "c.subsidiary"),
            (Filter("bu", bu, "c.bu"),
             Filter("posting_edit", "Posting edit code", "c.posting_edit", ((BLANK, "blank: may be posted to"),))),
            by_hand.CHART, _CHART_KEY,
        )  # fmt: skip
    if what == UNITS:
        return View(
            UNITS, "business unit", "business_units u", "1 = 1",
            (Column("code", "Business unit", "u.code", CODE), Column("name", "Name", "u.name"),
             Column("company", "Company", "u.company", CODE), Column("type", "Type", "u.type", CODE),
             Column("status", "Status", "u.status", CODE)),
            ("u.code", "u.name", "u.company"), ("u.code",),
            (Filter("status", "Status", "u.status"), Filter("company", "Company", "u.company"),
             Filter("type", "Type", "u.type")),
            by_hand.UNITS, "u.code",
        )  # fmt: skip
    if what == SUPPLIERS:
        fields = by_hand.supplier_fields(conn)
        kinds = dialect.column_kinds(conn, by_hand.SUPPLIERS)
        return View(
            SUPPLIERS, "supplier", "vendors v", "v.in_master = 1",
            tuple(Column(f.name, f.label, f"v.{f.name}", CODE if kinds[f.name] == "TEXT" else WHOLE) for f in fields),
            tuple(f"v.{f.name}" for f in fields if kinds[f.name] == "TEXT"), ("v.vendor_key",),
            (Filter("status", "On hold (AP's own)", "v.status", ((by_hand.ON_HOLD, "on hold"),
                                                                 (by_hand.IN_USE, "not on hold"))),
             Filter("erp_hold", "JD Edwards' hold payment code", "v.erp_hold", ((BLANK, "blank: no hold"),))),
            by_hand.SUPPLIERS, "v.vendor_key",
        )  # fmt: skip
    if what == JOURNAL:
        return View(
            JOURNAL, "line", "journal_lines l JOIN journal_vouchers v ON v.id = l.voucher_id "
            "JOIN journal_files f ON f.id = v.file_id", "v.counted = 1",
            (Column("gl_date", "G/L date", "v.gl_date", CODE), Column("doc_type", "Type", "v.doc_type", CODE),
             Column("company", "Company", "v.company", CODE),
             Column("supplier_number", "Supplier number", "v.supplier_number", CODE),
             Column("supplier_name", "Supplier", "v.supplier_name"), Column("account", "Account", "l.account", CODE),
             Column("description", "Account's name", "l.description"), Column("amount", "Amount", "l.amount", AMOUNT),
             Column("remark", "Remark", "l.remark"), Column("kind", "Kind of line", "l.kind"),
             Column("file", "File", "f.name")),
            ("v.supplier_name", "v.supplier_number", "l.account", "l.description", "l.remark", "v.gl_date"),
            ("v.gl_date", "v.id", "l.n"),
            (Filter("kind", "Kind of line", "l.kind"), Filter("business_unit", "Business unit", "l.business_unit"),
             Filter("file", "File", "f.name")),
        )  # fmt: skip
    if what == VOUCHERS:
        return View(
            VOUCHERS, "bill", "erp_invoices e", "1 = 1",
            (Column("an8", "Supplier number", "e.an8", CODE), Column("vendor_name", "Supplier", "e.vendor_name"),
             Column("invoice_number", "Invoice number", "e.invoice_number", CODE),
             Column("invoice_date", "Invoice date", "e.invoice_date", CODE),
             Column("total", "Gross", "e.total", AMOUNT), Column("copies", "Times listed", "e.copies", WHOLE),
             Column("documents", "JD Edwards document", _DOCUMENTS, CODE)),
            ("e.vendor_name", "e.invoice_number", "e.an8", "e.invoice_date"), (f"e.{dialect.ROWID}",),
        )  # fmt: skip
    raise ValueError(f"unknown list {what!r}")


def _where(view: View, q: Any, filters: Mapping[str, Any], kept: Mapping[str, Any]) -> tuple[str, list[Any]]:
    """The SQL condition (and its values) of the rows of ``view`` a search for ``q`` and ``filters`` keep: each
    word of the search somewhere in the row's searched columns (A to Z in any case), each filter's column its value
    exactly (``BLANK``: empty), and, for ``BY_HAND``, the rows ``kept`` names."""
    parts, args = [view.where], []
    for word in str(q or "").split()[:WORDS_MOST]:
        found = "%" + word.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        parts.append("(" + " OR ".join(f"{dialect.like(column)} ESCAPE '\\'" for column in view.search) + ")")
        args += [found] * len(view.search)
    for column in view.filters:
        value = str(filters.get(column.key) or "")
        if value:
            parts.append(f"{column.sql} = ?")
            args.append("" if value == BLANK else value)
    if view.table and filters.get(BY_HAND):
        parts.append(_in_list(view.row_key))
        args.append(json.dumps(list(kept)))
    return " AND ".join(parts), args


def _select(view: View) -> str:
    """``SELECT`` the view's columns, each under its key, a row's key in ``by_hand.held`` (``row_key``) and the
    columns the view is ordered by (``k0``, ``k1``...)."""
    columns = [f"{column.sql} AS {column.key}" for column in view.columns]
    order = [f"{column} AS k{n}" for n, column in enumerate(view.key)]
    return f"SELECT {', '.join(columns)}, {view.row_key} AS row_key, {', '.join(order)} FROM {view.source}"


def _rows(view: View, found: list[sqlite3.Row], kept: Mapping[str, Any], postable: tuple[str, ...]) -> list[dict]:
    """The rows as the page and the download take them: each column's value under its key, ``row_key``, what was
    changed by hand on it (``by_hand``: {field: {"value", "file", "by", "at"}}) and, for the chart, ``postable``:
    whether its posting edit code is one AP may post to."""
    rows = []
    for row in found:
        made = {column.key: row[column.key] for column in view.columns}
        made["row_key"], made[BY_HAND] = row["row_key"], kept.get(row["row_key"], {})
        if view.what == CHART:
            made["postable"] = bool(made["object"]) and made["posting_edit"] in postable
        rows.append(made)
    return rows


class LoadedMixin:
    def loaded_page(self, what: str, q: str = "", filters: Mapping[str, Any] | None = None, page: int = 1,
                    size: int = PAGE) -> dict[str, Any]:  # fmt: skip
        """One page of the list ``what`` (one of ``WHATS``): {"columns" (``Column``), "rows" (``size`` at most, each
        as ``_rows`` gives it), "count" (the rows the search ``q`` and ``filters`` keep), "total" (the rows of the
        list), "page" (the page given: the last one when ``page`` is past it), "pages", "filters": [{"key",
        "label", "value", "choices": [(value, words)] or None for one to type}], "by_hand" (how many rows keep a
        change made by hand, None for a list nobody changes), "noun"}. ``filters``: {a filter's key: the value its
        column has (``BLANK``: empty)}, and ``BY_HAND``: only the rows changed by hand."""
        filters = filters or {}
        with self._conn() as conn:
            view = _view(conn, what)
            kept = by_hand.held(conn)[view.table] if view.table else {}
            where, args = _where(view, q, filters, kept)
            total = conn.execute(f"SELECT COUNT(*) FROM {view.source} WHERE {view.where}").fetchone()[0]
            count = total if where == view.where else conn.execute(
                f"SELECT COUNT(*) FROM {view.source} WHERE {where}", args).fetchone()[0]  # fmt: skip
            pages = max(1, -(-count // size))
            page = min(max(1, int(page)), pages)
            found = conn.execute(f"{_select(view)} WHERE {where} ORDER BY {', '.join(view.key)} LIMIT ? OFFSET ?",
                                 (*args, size, (page - 1) * size)).fetchall()  # fmt: skip
            postable = _settings(conn).postable_codes if what == CHART else ()
            offered = [{"key": f.key, "label": f.label, "value": str(filters.get(f.key) or ""),
                        "choices": self._loaded_choices(conn, view, f)} for f in view.filters]  # fmt: skip
        return {"columns": view.columns, "rows": _rows(view, found, kept, postable), "count": count, "total": total,
                "page": page, "pages": pages, "filters": offered, "by_hand": len(kept) if view.table else None,
                "noun": view.noun}  # fmt: skip

    @staticmethod
    def _loaded_choices(conn: sqlite3.Connection, view: View, column: Filter) -> list[tuple[str, str]] | None:
        """The values a filter's column has in the list, each with what it is called; None when there are more than
        ``CHOICES_MOST`` (the business units of a whole chart: typed)."""
        values = conn.execute(f"SELECT DISTINCT {column.sql} FROM {view.source} WHERE {view.where} ORDER BY 1 LIMIT ?",
                              (CHOICES_MOST + 1,))  # fmt: skip
        found = [str(r[0] or "") for r in values]
        if len(found) > CHOICES_MOST:
            return None
        named = dict(column.named)
        return [(value or BLANK, named.get(value or BLANK, value)) for value in found]

    def loaded_batches(self, what: str, q: str = "", filters: Mapping[str, Any] | None = None,
                       batch: int = BATCH) -> Iterator[list[dict[str, Any]]]:  # fmt: skip
        """Every row of the list ``what`` that ``q`` and ``filters`` keep (as ``loaded_page`` reads them), in the
        list's order, ``batch`` rows at a time: each batch is a read of its own, on from the last row given, so a
        list of any length is never held whole."""
        last: list[Any] | None = None
        while True:
            with self._conn() as conn:
                view = _view(conn, what)
                kept = by_hand.held(conn)[view.table] if view.table else {}
                where, args = _where(view, q, filters or {}, kept)
                if last is not None:
                    where += f" AND ({', '.join(view.key)}) > ({', '.join('?' for _ in view.key)})"
                    args += last
                found = conn.execute(f"{_select(view)} WHERE {where} ORDER BY {', '.join(view.key)} LIMIT ?",
                                     (*args, batch)).fetchall()  # fmt: skip
                postable = _settings(conn).postable_codes if what == CHART else ()
            if found:
                yield _rows(view, found, kept, postable)
            if len(found) < batch:
                return
            last = [found[-1][f"k{n}"] for n in range(len(view.key))]

    def loaded_columns(self, what: str) -> tuple[Column, ...]:
        """The columns of the list ``what``, as ``loaded_page`` gives them (``ValueError``: no such list)."""
        with self._conn() as conn:
            return _view(conn, what).columns

    def loaded_row(self, what: str, row_key: str) -> dict[str, Any] | None:
        """The row of the list ``what`` (one a person may change) whose key in ``by_hand.held`` is ``row_key``, as
        ``loaded_page`` gives a row; None when the list has no such row."""
        with self._conn() as conn:
            view = _view(conn, what)
            if not view.table:
                return None
            found = conn.execute(f"{_select(view)} WHERE {view.where} AND {view.row_key} = ?", (row_key,)).fetchall()
            postable = _settings(conn).postable_codes if what == CHART else ()
            rows = _rows(view, found, by_hand.held(conn)[view.table], postable)
        return rows[0] if rows else None
