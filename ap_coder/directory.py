"""The invoice directory: every invoice in a table, filtered column by column, as JD Edwards' "query by example".

Each column has a filter box. What is typed there is read by the column's kind:

* text (vendor, invoice #, PO, GL account, people...): ``north`` contains "north"; ``north*`` starts with it,
  ``*0912`` ends with it (``*`` is any run of characters, ``?`` one character); ``=TELUS`` is exactly that;
  ``!credit`` does not contain it. Case and accents are ignored ("energir" finds "Énergir"): both sides are
  compared through the SQL function ``ap_fold`` that store.py gives each connection.
* amounts: ``1250``, ``>1000``, ``<=500``, ``100..250`` (a range, both ends included), ``!0``.
* dates: ``2026-09-14``, ``>2026-09-01``, ``2026-09`` (the month), ``2026`` (the year), ``2026-07..2026-09``.
* numbers (AP Coder #, export batch): like amounts; ``*`` alone is "has one", ``!*`` "has none".

The filters become SQL ``WHERE`` clauses with parameters (never text pasted into the SQL). Rows are sorted by any
column (empty values last) and paged. The PO, subtotal, tax and GL accounts come from the invoice's coding (the
reviewer's when approved, else AP Coder's), read with SQLite's JSON functions.

The chosen columns, filters and sort are a URL query (``query_string``), so a layout can be bookmarked, and saved
by name as a preset (Settings: ``directory_presets``, one of them the default).
"""

from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl, urlencode

from .safe import parse_amount
from .store import APPROVED, FAILED, PARKED, PENDING, REJECTED, REVIEW, fold_text

TEXT, MONEY, DATE, NUMBER, STATUS, WHERE = "text", "money", "date", "number", "status", "where"
STATUSES = (REVIEW, PARKED, PENDING, APPROVED, REJECTED, FAILED)
PAGE_SIZE = 100
EXPORT_LIMIT = 50_000
PRESETS_SETTING = "directory_presets"
DEFAULT_SETTING = "directory_default"
MAX_PRESETS = 30
MAX_PRESET_NAME = 60

# The coding an invoice is filtered on: the reviewer's once approved, else AP Coder's (never malformed JSON).
_DOC = "CASE WHEN json_valid(COALESCE(final_output, ai_output)) THEN COALESCE(final_output, ai_output) END"
_GL = (
    "(SELECT group_concat(g, ', ') FROM (SELECT DISTINCT json_extract(l.value, '$.predicted_gl_code') AS g "
    "FROM json_each(doc, '$.line_items') AS l WHERE g IS NOT NULL AND g NOT IN ('', 'UNASSIGNED') ORDER BY g))"
)
# The lowest GL account of an invoice's lines: what "GL accounts" sorts on (the list itself is slow to sort).
_FIRST_GL = "(SELECT MIN(json_extract(l.value, '$.predicted_gl_code')) FROM json_each(doc, '$.line_items') AS l " \
            "WHERE json_extract(l.value, '$.predicted_gl_code') NOT IN ('', 'UNASSIGNED'))"  # fmt: skip
# A line's GL account, for the GL filter: an invoice matches when one of its lines does.
_LINE_GL = "json_extract(l.value, '$.predicted_gl_code')"


@dataclass(frozen=True)
class Column:
    key: str
    label: str
    kind: str
    sql: str  # the expression over the ``rows`` view below (empty: not in SQL, e.g. "where it stands")
    right: bool = False  # numbers line up on the right
    sort_sql: str = ""  # what it is sorted on, when not ``sql``


COLUMNS: dict[str, Column] = {c.key: c for c in (
    Column("id", "AP Coder #", NUMBER, "id", right=True),
    Column("vendor", "Vendor", TEXT, "vendor_name"),
    Column("invoice_number", "Invoice #", TEXT, "invoice_number"),
    Column("invoice_date", "Invoice date", DATE, "invoice_date"),
    Column("due_date", "Due date", DATE, "due_date"),
    Column("po", "PO", TEXT, "po_number"),
    Column("currency", "Currency", TEXT, "currency"),
    Column("subtotal", "Subtotal", MONEY, "subtotal", right=True),
    Column("tax", "Tax", MONEY, "tax_total", right=True),
    Column("total", "Total", MONEY, "grand_total", right=True),
    Column("status", "Status", STATUS, "status"),
    Column("gl", "GL accounts", TEXT, "gl_codes", sort_sql=_FIRST_GL),
    Column("reviewer", "Approved by", TEXT, "reviewer"),
    Column("reviewed_at", "Approved on", DATE, "reviewed_at"),
    Column("second_reviewer", "Second approver", TEXT, "second_reviewer"),
    Column("batch", "Export batch", NUMBER, "export_batch", right=True),
    Column("received", "Received", DATE, "created_at"),
    Column("file", "File", TEXT, "file_name"),
    Column("where", "Where it stands", WHERE, ""),
)}  # fmt: skip
DEFAULT_COLUMNS = ("vendor", "invoice_number", "invoice_date", "due_date", "total", "currency", "status", "where")
DEFAULT_SORT = "received"

# Every invoice with the values the directory shows, filters and sorts on (one row per invoice).
ROWS_SQL = f"""
WITH coded AS (SELECT *, {_DOC} AS doc FROM invoices),
rows AS (
  SELECT id, status, file_name, vendor_name, invoice_number, invoice_date, due_date, currency, grand_total,
         created_at, reviewed_at, reviewer, second_reviewer, second_reviewed_at, export_batch, parked_reason,
         follow_up, error,
         NULLIF(TRIM(CAST(json_extract(doc, '$.po_number') AS TEXT)), '') AS po_number,
         CAST(json_extract(doc, '$.subtotal') AS REAL) AS subtotal,
         CAST(json_extract(doc, '$.tax_total') AS REAL) AS tax_total,
         {_GL} AS gl_codes, doc
  FROM coded)
"""


class FilterError(ValueError):
    """What is typed in a filter box can't be read (the message says what can)."""


@dataclass
class Query:
    columns: list[str] = field(default_factory=lambda: list(DEFAULT_COLUMNS))
    filters: dict[str, str] = field(default_factory=dict)  # column key -> what is typed
    sort: str = DEFAULT_SORT
    descending: bool = True
    page: int = 1
    q: str = ""  # the quick search (search.find), on top of the filters

    @classmethod
    def from_params(cls, params: Any) -> Query:
        """From the page's query string (a starlette ``QueryParams``, a dict or pairs): unknown keys are ignored."""
        if hasattr(params, "multi_items"):
            pairs = list(params.multi_items())
        elif isinstance(params, dict):
            pairs = list(params.items())
        else:
            pairs = list(params)
        cols = [v for k, v in pairs if k == "col" and v in COLUMNS]
        filters = {k[2:]: v.strip() for k, v in pairs if k.startswith("f_") and k[2:] in COLUMNS and v.strip()}
        values = {k: v for k, v in pairs}
        sort = values.get("sort", DEFAULT_SORT)
        try:
            page = max(1, int(values.get("page") or 1))
        except ValueError:
            page = 1
        return cls(
            columns=list(dict.fromkeys(cols)) or list(DEFAULT_COLUMNS),
            filters=filters,
            sort=sort if sort in COLUMNS and COLUMNS[sort].sql else DEFAULT_SORT,
            descending=values.get("dir", "desc" if sort == DEFAULT_SORT or sort not in COLUMNS else "asc") == "desc",
            page=page,
            q=str(values.get("q") or "").strip()[:200],
        )

    def params(self, **change: Any) -> list[tuple[str, str]]:
        """This query as URL pairs (``change`` overrides sort, dir, page or q)."""
        q = Query(**{**self.__dict__, **change}) if change else self
        pairs = [("col", c) for c in q.columns]
        pairs += [(f"f_{k}", v) for k, v in q.filters.items()]
        pairs += [("sort", q.sort), ("dir", "desc" if q.descending else "asc")]
        if q.q:
            pairs.append(("q", q.q))
        if q.page > 1:
            pairs.append(("page", str(q.page)))
        return pairs

    def query_string(self, **change: Any) -> str:
        return urlencode(self.params(**change))

    def sort_link(self, key: str) -> str:
        """The query sorted by ``key``: text A to Z first, amounts, dates and numbers largest or latest first; the other
        way when it is already sorted by it."""
        descending = not self.descending if key == self.sort else COLUMNS[key].kind in (MONEY, DATE, NUMBER)
        return self.query_string(sort=key, descending=descending, page=1)


# --- Reading a filter --------------------------------------------------------------------------------------------


_RANGE = re.compile(r"^(.+?)\s*\.\.\s*(.+)$")
_OP = re.compile(r"^(>=|<=|<>|!=|>|<|=|!)?\s*(.*)$")


def _like(pattern: str) -> str:
    """A filter's wildcards as a ``LIKE`` pattern (``\\`` escapes SQL's own % and _)."""
    escaped = pattern.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return escaped.replace("*", "%").replace("?", "_")


def _text(sql: str, typed: str) -> tuple[str, list[Any]]:
    op, value = _OP.match(typed).groups()  # type: ignore[union-attr]
    value = value.strip()
    if value in ("", "*"):
        has = f"COALESCE(CAST({sql} AS TEXT), '') != ''"
        return (f"NOT ({has})", []) if op in ("!", "!=", "<>") else (has, [])
    # The column through ap_fold, what is typed through the same fold_text: accents and case of any letter are
    # ignored, not only A to Z. Folded before the wildcards are read, so a fullwidth ％ never becomes SQL's %.
    value = fold_text(value)
    if op == "=" and not any(c in value for c in "*?"):
        return f"ap_fold(COALESCE(CAST({sql} AS TEXT), '')) = ?", [value]
    pattern = _like(value) if any(c in value for c in "*?") else f"%{_like(value)}%"
    clause = f"ap_fold(COALESCE(CAST({sql} AS TEXT), '')) LIKE ? ESCAPE '\\'"
    if op in ("!", "!=", "<>"):
        return f"NOT ({clause})", [pattern]
    if op in (">", "<", ">=", "<="):
        raise FilterError("Text filters take words and * (e.g. north*), = for exactly, ! for not.")
    return clause, [pattern]


def _amount(value: str) -> float:
    amount = parse_amount(value.strip())
    if amount is None:
        raise FilterError(f"“{value}” is not an amount: try 1250, >1000, 100..250.")
    return float(amount)


def _day_span(value: str) -> tuple[str, str]:
    """(first day, day after the last) of ``2026-09-14``, ``2026-09`` or ``2026``."""
    v = value.strip()
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
            d = dt.date.fromisoformat(v)
            return d.isoformat(), (d + dt.timedelta(days=1)).isoformat()
        if re.fullmatch(r"\d{4}-\d{2}", v):
            d = dt.date.fromisoformat(f"{v}-01")
            nxt = dt.date(d.year + d.month // 12, d.month % 12 + 1, 1)
            return d.isoformat(), nxt.isoformat()
        if re.fullmatch(r"\d{4}", v):
            return f"{v}-01-01", f"{int(v) + 1}-01-01"
    except ValueError:
        pass
    raise FilterError(f"“{value}” is not a date: try 2026-09-14, 2026-09, >2026-09-01, 2026-07..2026-09.")


def _compare(sql: str, typed: str, kind: str) -> tuple[str, list[Any]]:
    """An amount, date or number filter."""
    typed = typed.strip()
    if typed in ("*", "!*"):
        return (f"{sql} IS NOT NULL AND {sql} != ''", []) if typed == "*" else (f"({sql} IS NULL OR {sql} = '')", [])
    if kind == DATE:
        col = f"SUBSTR({sql}, 1, 10)"
        if m := _RANGE.match(typed):
            return f"{col} >= ? AND {col} < ?", [_day_span(m.group(1))[0], _day_span(m.group(2))[1]]
        op, value = _OP.match(typed).groups()  # type: ignore[union-attr]
        start, end = _day_span(value)
        clause = {">": (f"{col} >= ?", [end]), ">=": (f"{col} >= ?", [start]), "<": (f"{col} < ?", [start]),
                  "<=": (f"{col} < ?", [end])}.get(op or "")  # fmt: skip
        if clause:
            return clause
        inside = (f"{col} >= ? AND {col} < ?", [start, end])
        return (f"NOT ({inside[0]})", inside[1]) if op in ("!", "!=", "<>") else inside
    number = _amount if kind == MONEY else _whole
    if m := _RANGE.match(typed):
        low, high = sorted((number(m.group(1)), number(m.group(2))))
        return f"{sql} BETWEEN ? AND ?", [low - 0.005, high + 0.005]
    op, value = _OP.match(typed).groups()  # type: ignore[union-attr]
    n = number(value)
    if op in (">", ">=", "<", "<="):
        return f"{sql} {op} ?", [n]
    if op in ("!", "!=", "<>"):
        return f"NOT (COALESCE({sql}, 0) BETWEEN ? AND ?)", [n - 0.005, n + 0.005]
    return f"{sql} BETWEEN ? AND ?", [n - 0.005, n + 0.005]


def _whole(value: str) -> float:
    v = value.strip().lstrip("#")
    if not re.fullmatch(r"\d{1,12}", v):
        raise FilterError(f"“{value}” is not a number: try 42, >100, 10..20.")
    return float(v)


def condition(key: str, typed: str) -> tuple[str, list[Any]]:
    """One column's filter as (SQL condition, parameters). Raises FilterError."""
    col = COLUMNS[key]
    if col.kind == WHERE:
        raise FilterError("“Where it stands” can't be filtered: filter on Status instead.")
    if col.kind == STATUS:
        # Only real statuses, each once: the list comes from the address and could be any length (B-126).
        statuses = list(dict.fromkeys(s.strip().lower() for s in typed.split(",") if s.strip()))
        unknown = [s for s in statuses if s not in STATUSES]
        if unknown or not statuses:
            raise FilterError(f"“{(unknown or [typed])[0]}” is not a status: pick one from the list.")
        return f"status IN ({', '.join('?' * len(statuses))})", statuses  # at most len(STATUSES)
    if key == "gl":  # any one line's account ("6*": a line coded to a 6000 account)
        sql, params = _text(_LINE_GL, typed.lstrip("!"))
        each = f"EXISTS (SELECT 1 FROM json_each(doc, '$.line_items') AS l WHERE {sql})"
        return (f"NOT {each}", params) if typed.startswith("!") else (each, params)
    if col.kind == TEXT:
        return _text(col.sql, typed)
    return _compare(col.sql, typed, col.kind)


def where(query: Query, ids: list[int] | None = None) -> tuple[str, list[Any], dict[str, str]]:
    """The query's ``WHERE`` (``1`` when nothing is filtered), its parameters, and each filter that can't be read
    (column key -> what to type instead; those filters are left out)."""
    parts, params, problems = [], [], {}
    for key, typed in query.filters.items():
        try:
            sql, values = condition(key, typed)
        except FilterError as exc:
            problems[key] = str(exc)
            continue
        parts.append(f"({sql})")
        params += values
    if ids is not None:
        # The quick search's hits (up to EXPORT_LIMIT) as one JSON list: one '?' however many there are, as SQLite
        # takes only so many bound variables (32,766 on a stock build, 999 on older ones).
        parts.append("id IN (SELECT value FROM json_each(?))" if ids else "0")
        params += [json.dumps([int(i) for i in ids])] if ids else []
    return " AND ".join(parts) or "1", params, problems


def order_by(query: Query) -> str:
    col = COLUMNS[query.sort]
    sql = col.sort_sql or col.sql
    expr = f"{sql} COLLATE NOCASE" if col.kind in (TEXT, STATUS) else sql
    direction = "DESC" if query.descending else "ASC"
    return f"COALESCE({sql}, '') = '' ASC, {expr} {direction}, id DESC"


# What a row of the directory carries (not the coding itself: it stays in the database).
ROW_FIELDS = ("id, status, file_name, vendor_name, invoice_number, invoice_date, due_date, currency, grand_total, "
              "created_at, reviewed_at, reviewer, second_reviewer, second_reviewed_at, export_batch, parked_reason, "
              "follow_up, error, po_number, subtotal, tax_total, gl_codes")  # fmt: skip


# --- Results --------------------------------------------------------------------------------------------------------


@dataclass
class Result:
    rows: list[dict[str, Any]]
    total: int
    page: int
    pages: int
    problems: dict[str, str]
    erp: list[Any] = field(default_factory=list)  # quick-search hits in the ERP register (search.Hit)

    @property
    def first(self) -> int:
        return (self.page - 1) * PAGE_SIZE + 1 if self.total else 0

    @property
    def last(self) -> int:
        return min(self.page * PAGE_SIZE, self.total)


def run(store: Any, query: Query, *, page_size: int = PAGE_SIZE, everything: bool = False) -> Result:
    """The query's rows (one page, or ``everything`` up to EXPORT_LIMIT for an export)."""
    ids, erp = None, []
    if query.q:
        from .search import find

        hits = find(store, query.q, limit=EXPORT_LIMIT)
        ids = [h.row["id"] for h in hits if h.source != "ERP"]
        erp = [h for h in hits if h.source == "ERP"]
    sql_where, params, problems = where(query, ids)
    total = store.directory_count(ROWS_SQL, sql_where, params)
    if everything:
        limit, offset, page, pages = EXPORT_LIMIT, 0, 1, 1
    else:
        pages = max(1, -(-total // page_size))
        page = min(query.page, pages)
        limit, offset = page_size, (page - 1) * page_size
    rows = store.directory_rows(ROWS_SQL, ROW_FIELDS, sql_where, params, order_by(query), limit, offset)
    return Result(rows, total, page, pages, problems, erp)


def cell(row: dict[str, Any], key: str) -> Any:
    """A row's value for a column (the raw value: the page and the export format it)."""
    if key == "where":
        from .search import where as where_it_stands

        return where_it_stands(row)
    sql = COLUMNS[key].sql
    return row.get(sql)


# --- Presets ---------------------------------------------------------------------------------------------------------


def _clean(query_string: str) -> str:
    """A saved query's string rebuilt from the keys the directory knows (never anything else), without its page."""
    q = Query.from_params(parse_qsl(query_string or "", keep_blank_values=False))
    q.page = 1
    return q.query_string()


def presets(store: Any) -> list[dict[str, str]]:
    """The saved layouts: [{name, query}], in the order saved."""
    try:
        data = json.loads(store.get_setting(PRESETS_SETTING) or "[]")
    except ValueError:
        return []
    out = []
    for p in data if isinstance(data, list) else []:
        if isinstance(p, dict) and str(p.get("name") or "").strip():
            out.append({"name": str(p["name"]).strip()[:MAX_PRESET_NAME], "query": _clean(str(p.get("query") or ""))})
    return out


def default_preset(store: Any) -> dict[str, str] | None:
    name = store.get_setting(DEFAULT_SETTING) or ""
    return next((p for p in presets(store) if p["name"] == name), None)


def save_preset(store: Any, name: str, query_string: str, make_default: bool = False) -> str:
    """Save (or replace) a layout by name; returns the name kept. Raises ValueError with what is wrong."""
    name = " ".join(str(name or "").split())[:MAX_PRESET_NAME]
    if not name:
        raise ValueError("Give the layout a name.")
    kept = [p for p in presets(store) if p["name"].lower() != name.lower()]
    if len(kept) >= MAX_PRESETS:
        raise ValueError(f"At most {MAX_PRESETS} saved layouts: delete one first.")
    kept.append({"name": name, "query": _clean(query_string)})
    store.set_setting(PRESETS_SETTING, json.dumps(kept))
    if make_default:
        store.set_setting(DEFAULT_SETTING, name)
    return name


def delete_preset(store: Any, name: str) -> bool:
    before = presets(store)
    kept = [p for p in before if p["name"] != name]
    if len(kept) == len(before):
        return False
    store.set_setting(PRESETS_SETTING, json.dumps(kept))
    if (store.get_setting(DEFAULT_SETTING) or "") == name:
        store.set_setting(DEFAULT_SETTING, "")
    return True


def set_default(store: Any, name: str) -> bool:
    """Make a saved layout the one the directory opens with ("" for none)."""
    if name and not any(p["name"] == name for p in presets(store)):
        return False
    store.set_setting(DEFAULT_SETTING, name)
    return True
