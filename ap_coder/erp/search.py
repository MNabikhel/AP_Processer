"""The invoice directory's quick search: answer a vendor's "did you get it, and when will it be paid?".

One search box: words of the vendor name, an invoice or PO number (however it is written), or an amount.
Each match says where the invoice stands; the ERP's invoice register (Exports page) is searched too.

Entry points: ``find`` (the hits, best first), ``condition`` (the invoices it finds, as SQL: the invoice directory
counts and pages them), ``erp_hits`` (the ERP register's) and ``where`` (where an invoice stands, in a sentence).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from ..coding.po import po_key
from ..coding.rules import _plain
from ..coding.vendors import norm_invoice_number
from ..core.memory import vendor_key
from ..core.safe import parse_amount
from ..core.store import APPROVED, FAILED, PARKED, PENDING, REJECTED, REVIEW, Store
from ..core.store.summary import fts_available

LIMIT = 50


@dataclass
class Hit:
    score: int
    row: dict[str, Any]
    source: str = "AP Coder"  # or "ERP"


def _day(iso: str | None) -> str:
    return (iso or "")[:10]


def where(row: dict[str, Any]) -> str:
    """Where an invoice stands, in a sentence."""
    status = row["status"]
    approved = f"approved by {row['reviewer'] or '?'} on {_day(row['reviewed_at'])}"
    if row.get("second_reviewer"):
        approved += f", second approval by {row['second_reviewer']} on {_day(row['second_reviewed_at'])}"
    if status == REVIEW:
        return f"In the review queue since {_day(row['created_at'])}"
    if status == PARKED:
        follow = f"; follow up {row['follow_up']}" if row.get("follow_up") else ""
        return f"Parked, waiting for: {row.get('parked_reason') or '?'}{follow}"
    if status == PENDING:
        return f"Waiting for a second approval ({approved})"
    if status == APPROVED and row.get("export_batch"):
        return f"In the ERP: {approved}; exported in batch {row['export_batch']} on {_day(row.get('exported_at'))}"
    if status == APPROVED:
        return f"{approved[0].upper()}{approved[1:]}; not exported to the ERP yet"
    if status == REJECTED:
        return f"Rejected by {row['reviewer'] or '?'} on {_day(row['reviewed_at'])}: {row.get('error') or 'no reason'}"
    if status == FAILED:
        return f"Could not be read ({row.get('error') or 'error'}): process it again or enter it by hand"
    return status


MANAGER_WAIT = " to confirm the cost allocation"  # the end of a parked reason while a manager has it (D-138)


def next_to_act(row: dict[str, Any]) -> str:
    """Who has to act on the invoice next, and what (D-140): AP, a manager, a second approver, or no one."""
    status = row["status"]
    if status == REVIEW:
        return "AP: review it and approve it"
    if status == PARKED:
        reason = str(row.get("parked_reason") or "")
        if reason.endswith(MANAGER_WAIT):
            return f"{reason[: -len(MANAGER_WAIT)] or 'The manager'}: confirm the cost allocation"
        follow = f" (follow up {row['follow_up']})" if row.get("follow_up") else ""
        return f"AP, once it has: {reason or 'what it waits for'}{follow}"
    if status == PENDING:
        return f"A second approver (not {row.get('reviewer') or 'the first'}): approve it or send it back"
    if status == APPROVED and row.get("export_batch"):
        return f"No one: in the ERP (batch {row['export_batch']})"
    if status == APPROVED:
        return "AP: export it to the ERP"
    if status == REJECTED:
        return "No one: rejected"
    if status == FAILED:
        return "AP: process it again or enter it by hand"
    return "AP Coder: reading it"


def _raw(text: str) -> str:
    """Letters and digits only, leading zeros kept: "CN-2026-0047" -> "cn20260047"."""
    return re.sub(r"[^0-9a-z]", "", text.lower())


def search_keys(row: dict[str, Any]) -> tuple[str, str, str]:
    """(the vendor and file names, the invoice number as it compares, its letters and digits) of an invoice, as
    ``_score`` reads them: what the invoices' summary keeps for the quick search (D-110)."""
    number = row.get("invoice_number") or ""
    return _plain(f"{row.get('vendor_name') or ''} {row.get('file_name') or ''}"), norm_invoice_number(number), \
        _raw(number)  # fmt: skip


def register_keys(vendor_name: str, invoice_number: str) -> tuple[str, str]:
    """(the vendor name, the invoice number's letters and digits) of an ERP register row as ``_score`` reads them:
    what the register keeps for the quick search (``plain_name``, ``raw_number``, D-157)."""
    keys = search_keys({"vendor_name": vendor_name, "invoice_number": invoice_number})
    return keys[0], keys[2]


def _score(row: dict[str, Any], words: list[str], numbers: list[str]) -> int:
    """0 unless every part of the query matches: the words in the vendor (or file) name, and each number as
    the invoice number, the PO, the amount, or part of the name ("3m")."""
    score = 0
    name = _plain(f"{row.get('vendor_name') or ''} {row.get('file_name') or ''}")
    if words:
        if not all(w in name for w in words):
            return 0
        score += 2 + len(words)
    invoice_number = norm_invoice_number(row.get("invoice_number") or "")
    raw_number = _raw(row.get("invoice_number") or "")
    total = row.get("grand_total")
    for token in numbers:
        number, amount = norm_invoice_number(token), parse_amount(token)
        found = 0
        if number and invoice_number == number:
            found = 6
        elif len(_raw(token)) >= 3 and _raw(token) in raw_number:
            found = 3
        if number and row.get("po_key") and po_key(token) == row["po_key"]:
            found = max(found, 4)
        if amount is not None and total is not None and abs(abs(total) - abs(amount)) < 0.005:
            found = max(found, 5)
        if not found and _plain(token) in name.split():
            found = 1  # a vendor name with a digit in it
        if not found:
            return 0
        score += found
    return score


def _same_vendor(a: str, b: str) -> bool:
    """Two vendor keys of one vendor: the same, or one the start of the other (the ERP's short name for it:
    "chinook courier" for "chinook courier and office services")."""
    short, long_ = sorted((a.split(), b.split()), key=len)
    return bool(short) and long_[: len(short)] == short


def _tokens(query: str) -> list[str]:
    """The query's parts; "18 017,85" (an amount written the French way) stays one part."""
    parts = [p.strip("\"'?!;:") for p in query.split()]
    out: list[str] = []
    for part in (p for p in parts if p):
        if out and re.fullmatch(r"[-+(]?\$?\d{1,3}", out[-1]) and re.fullmatch(r"\d{3}(?:[.,]\d+)?\)?\$?", part):
            out[-1] += part
        else:
            out.append(part)
    return out


def parts(query: str) -> tuple[list[str], list[str]]:
    """(the words, the numbers) of a quick search, as ``_score`` reads them; each once (every one must match)."""
    tokens = [t.lower() for t in _tokens(query)]
    numbers = [w for w in tokens if any(c.isdigit() for c in w)]
    words = [_plain(w.strip(",.")) for w in tokens if w not in numbers and len(w.strip(",.")) > 1]
    return list(dict.fromkeys(words)), list(dict.fromkeys(numbers))


def _contains(column: str, text: str, fts: bool) -> tuple[str, list[Any]]:
    """The summary's ``column`` (``plain_name`` or ``raw_number``) contains ``text``, as Python's ``in`` says it:
    narrowed first by the FTS5 trigram index when there is one and ``text`` is three characters or more (D-110)."""
    exact = f"instr({column}, ?) > 0"
    if not fts or len(text) < 3:
        return exact, [text]
    phrase = '"' + text.replace('"', '""') + '"'
    return f"id IN (SELECT rowid FROM invoice_search WHERE invoice_search MATCH ?) AND {exact}", [
        f"{column} : {phrase}", text]  # fmt: skip


def _amount(column: str, amount: float) -> tuple[str, list[Any]]:
    """``column`` is ``amount`` (or its credit), to the half cent, as ``_score`` compares them (by the column's index
    first)."""
    size = abs(amount)
    return (f"({column} BETWEEN ? AND ? OR {column} BETWEEN ? AND ?) AND abs(abs({column}) - ?) < 0.005",
            [size - 0.0051, size + 0.0051, -size - 0.0051, -size + 0.0051, size])  # fmt: skip


def condition(words: list[str], numbers: list[str], fts: bool) -> tuple[str, list[Any]]:
    """The invoices ``_score`` matches, every one and no other, as a SQL condition over the invoices' summary (its
    ``plain_name``, ``number_key``, ``raw_number``, ``po_key`` and ``grand_total``, D-110): each word in the vendor or
    file name, and each number as the invoice number (normalised or a part of it), the PO, the amount or a word of
    the name. The directory counts and pages the hits in SQL with it (D-157)."""
    if not words and not numbers:
        return "0", []
    where, params = [], []
    for w in words:
        sql, values = _contains("plain_name", w, fts)
        where.append(f"({sql})")
        params += values
    for token in numbers:
        number, amount, raw, plain = norm_invoice_number(token), parse_amount(token), _raw(token), _plain(token)
        either, values = [], []
        if number:
            either.append("number_key = ?")
            values.append(number)
            if po_key(token):
                either.append("po_key = ?")
                values.append(po_key(token))
        if len(raw) >= 3:
            sql, more = _contains("raw_number", raw, fts)
            either.append(sql)
            values += more
        if amount is not None:
            sql, more = _amount("grand_total", amount)
            either.append(sql)
            values += more
        if plain and " " not in plain:  # one of the name's words: a vendor name with a digit in it ("3m")
            sql, more = _contains("plain_name", plain, fts)
            either.append(f"{sql} AND instr(' ' || plain_name || ' ', ?) > 0")
            values += [*more, f" {plain} "]
        if not either:
            return "0", []
        where.append("(" + " OR ".join(f"({e})" for e in either) + ")")
        params += values
    return " AND ".join(where), params


def _in_register(words: list[str], numbers: list[str]) -> tuple[str, list[Any]]:
    """The ERP register's rows ``_score`` may match (every one it matches among them), as a SQL condition over its
    vendor name and invoice number as the quick search compares them (``plain_name``, ``raw_number``): a number
    normalised is always a part of its letters and digits ("INV-0042": "42" in "inv0042")."""
    if not words and not numbers:
        return "0", []
    where, params = [], []
    for w in words:
        where.append("instr(plain_name, ?) > 0")
        params.append(w)
    for token in numbers:
        number, amount, raw, plain = norm_invoice_number(token), parse_amount(token), _raw(token), _plain(token)
        either = [("instr(raw_number, ?) > 0", [text]) for text in dict.fromkeys(
            t for t in (number, raw if len(raw) >= 3 else "") if t)]  # fmt: skip
        if amount is not None:
            either.append(_amount("total", amount))
        if plain and " " not in plain:
            either.append(("instr(' ' || plain_name || ' ', ?) > 0", [f" {plain} "]))
        if not either:
            return "0", []
        where.append("(" + " OR ".join(f"({sql})" for sql, _ in either) + ")")
        params += [v for _, values in either for v in values]
    return " AND ".join(where), params


def _ours(erp: dict[str, Any], exported: list[dict[str, Any]]) -> bool:
    """Whether an ERP register row is an exported invoice's own (the same number and amount, the same vendor), among
    the exported invoices with its number."""
    total = round(erp["total"] or 0, 2)
    return any(
        round(r["grand_total"] or 0, 2) == total and _same_vendor(erp["vendor_key"], vendor_key(r["vendor_name"] or ""))
        for r in exported
    )


def erp_hits(store: Store, words: list[str], numbers: list[str]) -> list[Hit]:
    """The quick search's hits in the ERP register, best first (in the register's order among equals). An exported
    invoice's own row there is the same bill: shown once, as the AP Coder invoice. Its own row: the same number and
    amount (with its sign: a credit is another document), and the same vendor."""
    where, params = _in_register(words, numbers)
    scored = []
    for r in store.erp_register_rows(where, params):
        row = {"vendor_name": r["vendor_name"], "invoice_number": r["invoice_number"], "grand_total": r["total"],
               "invoice_date": r["invoice_date"], "po_key": ""}  # fmt: skip
        if s := _score(row, words, numbers):
            scored.append((s, r, row))
    keys = sorted({r["number_key"] for _, r, _ in scored})
    exported: dict[str, list[dict[str, Any]]] = {}
    if keys:  # the exported invoices with these numbers, in one read
        for x in store.summary_rows("export_batch AND number_key IN (SELECT value FROM json_each(?))",
                                    (json.dumps(keys),), "number_key, vendor_name, grand_total"):  # fmt: skip
            exported.setdefault(x["number_key"], []).append(x)
    hits = [Hit(s, row, "ERP") for s, r, row in scored if not _ours(r, exported.get(r["number_key"], []))]
    hits.sort(key=lambda h: -h.score)
    return hits


def find(store: Store, query: str, limit: int = LIMIT) -> list[Hit]:
    """The quick search's hits, best first: the invoices in AP Coder (the latest first among equals), then the ERP
    register's."""
    words, numbers = parts(query)
    if not words and not numbers:
        return []
    sql, params = condition(words, numbers, fts_available())
    hits = [Hit(s, r) for r in store.search_rows(where=sql, params=params) if (s := _score(r, words, numbers))]
    hits += erp_hits(store, words, numbers)
    hits.sort(key=lambda h: (-h.score, h.source != "AP Coder", -(h.row.get("id") or 0)))
    return hits[:limit]
