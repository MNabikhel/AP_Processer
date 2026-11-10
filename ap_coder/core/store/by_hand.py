"""What a person changed by hand in what JD Edwards' files loaded (D-221): a chart account's description, whether it
may be posted to and its meals tick; a business unit's name and status; a supplier's own fields.

A newer chart or business unit master replaces its table whole, and a newer supplier list replaces what its file
gives, so a change made by hand would be gone at the next load. Each one is kept instead, beside what the file said
(``hand_edits``, a setting: {table: {key: {field: {"value", "file", "by", "at"}}}}), the way ``chart_set_aside`` keeps
what was typed on a code that left the lists (B-2090):

* the chart's and the business units' imports put it back on the rows they read, before they compare and write
  (``_hand_edits_put_back``): the same file loaded again changes nothing; a row the file no longer has keeps its
  change set aside, for when a file has it again;
* the supplier master's import is followed by ``_supplier_edits_put_back`` (``Store.import_vendor_master``): a field
  the import replaced gets the person's value back. One changed elsewhere since (the Vendors page, a sync) is no
  longer the change made here, and is let go;
* a change is let go when the file says the same (the chart, the business units), or when the person types the
  file's value back.

The meals tick is the GL account's (``gl_accounts.meals``, on every business unit): the lists' rebuild keeps it
(B-2090), nothing more is kept here. Every change is an event (``EDITED_ACTION``: who, which row, each field before and
after); what an import put back is in the import's own event (the chart, the business units) or one of its own (the
suppliers).
"""

from __future__ import annotations

import dataclasses
import json
import sqlite3
from collections.abc import Mapping
from typing import Any

from ..chart import TEXT_MOST, account_lists, clean, code_of, join_account
from . import dialect
from ._base import _in_list, _now
from .accounts import EVENT_ACTOR, count_masters_change
from .chart import NAMED, _chart, _settings, _units

EDITS_KEY = "hand_edits"  # (settings, JSON)
CHART, UNITS, SUPPLIERS = "chart_accounts", "business_units", "vendors"
TABLES = (CHART, UNITS, SUPPLIERS)
EDITED_ACTION = "changed_by_hand"  # the event of a change made by hand, or of the changes an import put back
# The posting edit code written on an account a person says may not be posted to, when the file's own code let it
# be: JD Edwards' "inactive, no entries".
NOT_POSTED = "I"
ON_HOLD, IN_USE = "on_hold", "active"  # AP's own hold on a supplier (``vendors.status``)
NO_ACCOUNT_ROW = "That account is not in the chart of accounts any more. Nothing was changed."
NO_UNIT = "That business unit is not in the business unit master any more. Nothing was changed."
NO_SUPPLIER = "That supplier is not in the supplier list any more. Nothing was changed."
NO_MEALS = ("The meals tick is kept on the GL accounts a line may be coded to, and this account is not one of them "
            "(it may not be posted to on any active business unit). Nothing was changed.")  # fmt: skip
NAME_NEEDED = "A supplier needs a name. Nothing was changed."
SEPARATOR = "\t"  # between the business unit, the object and the subsidiary of a chart row's key


def held(conn: sqlite3.Connection) -> dict[str, dict[str, dict[str, dict[str, str]]]]:
    """What is kept: {table: {key: {field: {"value" (as changed), "file" (what the file said, or the row held,
    before), "by", "at"}}}}; a chart row's key is ``chart_key``, a business unit's its code, a supplier's its
    vendor key."""
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (EDITS_KEY,)).fetchone()
    try:
        kept = json.loads(row["value"]) if row else {}
    except ValueError:
        kept = {}
    return {table: dict(kept.get(table) or {}) if isinstance(kept, dict) else {} for table in TABLES}


def _keep(conn: sqlite3.Connection, kept: dict[str, Any]) -> None:
    conn.execute(dialect.upsert("settings", ("key", "value"), ("key",)), (EDITS_KEY, json.dumps(kept, sort_keys=True)))


def _note(kept: dict[str, Any], table: str, key: str, changes: Mapping[str, tuple[str, str]],
          actor: str | None) -> None:  # fmt: skip
    """``changes`` ({field: (before, after)}) made by hand on a row, kept: a field changed for the first time keeps
    what it was (the file's); one changed back to that is not kept any more."""
    fields = kept[table].setdefault(key, {})
    for field, (before, after) in changes.items():
        said = fields[field]["file"] if field in fields else before
        if after == said:
            fields.pop(field, None)
        else:
            fields[field] = {"value": after, "file": said, "by": actor or EVENT_ACTOR.get() or "", "at": _now()}
    if not fields:
        del kept[table][key]


def chart_key(bu: Any, obj: Any, subsidiary: Any = "") -> str:
    """A chart row's key in ``hand_edits`` (and in the address of its form)."""
    return SEPARATOR.join((clean(bu), clean(obj), clean(subsidiary)))


def chart_parts(key: Any) -> tuple[str, str, str]:
    """A chart row's (business unit, object, subsidiary) from its key."""
    bu, obj, subsidiary = (str(key or "").split(SEPARATOR) + ["", ""])[:3]
    return bu, obj, subsidiary


def row_name(table: str, key: str) -> str:
    """A row as an event and a load's summary name it: a chart row's account string, a business unit's code."""
    if table != CHART:
        return key
    bu, obj, subsidiary = chart_parts(key)
    return join_account(bu, code_of(obj, subsidiary))


def _said(changes: Mapping[str, tuple[Any, Any]]) -> dict[str, dict[str, Any]]:
    return {field: {"from": before, "to": after} for field, (before, after) in changes.items()}


# --- A supplier's fields -------------------------------------------------------------------------------------------

SUPPLIER_LABELS = {
    "erp_id": "Supplier number", "display_name": "Name", "status": "On hold (AP's own)", "notes": "Note",
    "expected_gst": "GST/HST number", "qst_number": "QST number", "pst_number": "PST number",
    "province": "Province", "terms": "Payment terms", "default_gl": "Default GL account",
    "erp_hold": "JD Edwards' hold payment code",
}  # fmt: skip
SUPPLIER_FIRST = ("erp_id", "display_name", "status", "erp_hold", "notes", "expected_gst")  # then the table's order
SUPPLIER_NOT_SHOWN = ("vendor_key", "updated_at", "in_master")  # AP Coder's own bookkeeping of the row
# JD Edwards' own: the number is which supplier it is (B-2161), the hold payment code is JD Edwards' to give and take
# (AP's own hold is the ``status``). Shown, never changed here.
SUPPLIER_FIXED = ("erp_id", "erp_hold")
SUPPLIER_TEXT_MOST = 200


@dataclasses.dataclass(frozen=True)
class Field:
    """One column of a supplier's row as the page shows it; ``editable``: an Admin may change it by hand."""

    name: str
    label: str
    editable: bool


def supplier_fields(conn: sqlite3.Connection) -> list[Field]:
    """The supplier list's columns a person sees, whichever the table has (a version that keeps a currency or an
    address shows them too, under their column's name in words): the number, the name and the holds first. A bank
    account is never among them (D-160); a column that is not text is shown, not changed."""
    kinds = dialect.column_kinds(conn, SUPPLIERS)
    names = [name for name in dict.fromkeys((*SUPPLIER_FIRST, *kinds))
             if name in kinds and name not in SUPPLIER_NOT_SHOWN and "bank" not in name]  # fmt: skip
    return [Field(name, SUPPLIER_LABELS.get(name) or name.replace("_", " ").capitalize(),
                  kinds[name] == "TEXT" and name not in SUPPLIER_FIXED) for name in names]  # fmt: skip


def _supplier_text(field: str, value: Any) -> str:
    text = " ".join(str(value or "").split())[:SUPPLIER_TEXT_MOST]
    if field == "status":
        return ON_HOLD if text.lower() in (ON_HOLD, "1", "on", "yes", "true") else IN_USE
    return text.upper() if field == "province" else text


class ByHandMixin:
    def hand_edits(self) -> dict[str, dict[str, dict[str, dict[str, str]]]]:
        """What a person changed by hand in the chart, the business units and the suppliers, kept for the next load
        (``held``)."""
        with self._conn() as conn:
            return held(conn)

    # --- The chart of accounts and the business units -----------------------------------------------------------

    def _hand_edits_put_back(self, conn: sqlite3.Connection, table: str, rows: dict[Any, Any]) -> list[str]:
        """An import's ``rows`` of the chart or the business units ({key: ``ChartRow`` or ``BuRow``}, changed in
        place) with what a person changed by hand put back, before they are compared and written. A field the file
        now gives the person's value is let go; another keeps what the file says beside it. Returns the rows that
        keep a change, as ``row_name`` names them."""
        kept = held(conn)
        if not kept[table]:
            return []
        before = json.dumps(kept, sort_keys=True)
        by_text = {SEPARATOR.join(key) if isinstance(key, tuple) else key: key for key in rows}
        put = []
        for text, fields in list(kept[table].items()):
            if text not in by_text:
                continue  # not in this file: set aside, for a file that has the row again
            row = rows[by_text[text]]
            for field, edit in list(fields.items()):
                said = getattr(row, field)
                if said == edit["value"]:
                    del fields[field]
                else:
                    edit["file"] = said
                    row = dataclasses.replace(row, **{field: edit["value"]})
            rows[by_text[text]] = row
            if fields:
                put.append(row_name(table, text))
            else:
                del kept[table][text]
        if json.dumps(kept, sort_keys=True) != before:
            _keep(conn, kept)
        return put

    def edit_chart_account(self, bu: str, obj: str, subsidiary: str = "", *, description: str | None = None,
                           postable: bool | None = None, meals: bool | None = None,
                           actor: str | None = None) -> dict[str, dict[str, Any]]:  # fmt: skip
        """Change one row of the chart by hand: its ``description``, whether it may be posted to (``postable``: its
        posting edit code made blank, or the file's own code again, ``NOT_POSTED`` when that one let it be) and the
        meals tick of its GL account (``meals``); None leaves each as it is. The GL accounts and the cost centers
        follow at once, and the change is kept for the next load of the chart (``held``). Returns what changed,
        {field: {"from", "to"}}, recorded as one event; {} when nothing did. ``ValueError``, nothing changed: the
        row is not in the chart, no account would be left to code to, or a meals tick is asked for an account that
        is not a GL account."""
        with self._conn() as conn:
            dialect.begin_write(conn)
            where, key = "bu = ? AND object = ? AND subsidiary = ?", (clean(bu), clean(obj), clean(subsidiary))
            row = conn.execute(f"SELECT description, posting_edit FROM chart_accounts WHERE {where}", key).fetchone()
            if row is None:
                raise ValueError(NO_ACCOUNT_ROW)
            settings, kept, text = _settings(conn), held(conn), chart_key(*key)
            code = code_of(key[1], key[2])
            ticked = conn.execute("SELECT meals FROM gl_accounts WHERE code = ?", (code,)).fetchone()
            wanted: dict[str, str] = {}
            if description is not None:
                wanted["description"] = " ".join(str(description).split())[:TEXT_MOST]
            if postable is not None and key[1] and postable != (row["posting_edit"] in settings.postable_codes):
                said = kept[CHART].get(text, {}).get("posting_edit", {}).get("file", row["posting_edit"])
                wanted["posting_edit"] = "" if postable else (NOT_POSTED if said in settings.postable_codes else said)
            changes = {field: (row[field], value) for field, value in wanted.items() if row[field] != value}
            if changes:
                was = account_lists(_chart(conn), _units(conn), settings)[0]
                conn.execute(f"UPDATE chart_accounts SET {', '.join(f'{field} = ?' for field in changes)} "
                             f"WHERE {where}", (*(after for _, after in changes.values()), *key))  # fmt: skip
                _note(kept, CHART, text, changes, actor)
                _keep(conn, kept)
                self._rebuild_account_lists(conn, was)
                self._chart_stamped(conn)
            said = _said(changes)
            if meals is not None and key[1]:  # (a business unit's own header row is no account)
                listed = conn.execute("SELECT meals FROM gl_accounts WHERE code = ?", (code,)).fetchone()
                if listed is None and meals and not (ticked and ticked["meals"]):
                    raise ValueError(NO_MEALS)
                if listed is not None and bool(listed["meals"]) != meals:
                    conn.execute("UPDATE gl_accounts SET meals = ? WHERE code = ?", (int(meals), code))
                    said["meals"] = {"from": bool(listed["meals"]), "to": meals}
            if said:
                detail = {"table": CHART, "row": row_name(CHART, text), "changes": said}
                self._log(conn, EDITED_ACTION, actor=actor, detail=detail)
        return said

    def edit_business_unit(self, code: str, *, name: str | None = None, status: str | None = None,
                           actor: str | None = None) -> dict[str, dict[str, Any]]:  # fmt: skip
        """Change one business unit of the business unit master by hand: its ``name``, its ``status`` (``ACT`` or
        blank takes postings; any other does not); None leaves each as it is. The GL accounts and the cost centers
        follow at once, and the change is kept for the next load. Returns what changed, {field: {"from", "to"}},
        recorded as one event; {} when nothing did. ``ValueError``, nothing changed: the business unit is not in
        the master, or no account would be left to code to."""
        with self._conn() as conn:
            dialect.begin_write(conn)
            code = clean(code)
            row = conn.execute("SELECT name, status FROM business_units WHERE code = ?", (code,)).fetchone()
            if row is None:
                raise ValueError(NO_UNIT)
            wanted: dict[str, str] = {}
            if name is not None:
                wanted["name"] = " ".join(str(name).split())[:TEXT_MOST]
            if status is not None:
                wanted["status"] = clean(status).upper()[:TEXT_MOST]
            changes = {field: (row[field], value) for field, value in wanted.items() if row[field] != value}
            if changes:
                was = account_lists(_chart(conn), _units(conn), _settings(conn))[0]
                conn.execute(f"UPDATE business_units SET {', '.join(f'{field} = ?' for field in changes)} "
                             "WHERE code = ?", (*(after for _, after in changes.values()), code))  # fmt: skip
                kept = held(conn)
                _note(kept, UNITS, code, changes, actor)
                _keep(conn, kept)
                self._rebuild_account_lists(conn, was)
                self._chart_stamped(conn)
                self._log(
                    conn, EDITED_ACTION, actor=actor, detail={"table": UNITS, "row": code, "changes": _said(changes)}
                )
        return _said(changes)

    # --- The suppliers ------------------------------------------------------------------------------------------

    def supplier_master_fields(self) -> list[Field]:
        """The supplier list's columns as a person sees them (``supplier_fields``)."""
        with self._conn() as conn:
            return supplier_fields(conn)

    def edit_supplier(self, key: str, changes: Mapping[str, Any],
                      actor: str | None = None) -> dict[str, dict[str, Any]]:  # fmt: skip
        """Change a supplier of the supplier list by hand: ``changes`` {column: its new text}, each a column an Admin
        may change (``supplier_fields``; ``status``: "on_hold" or "active", AP's own hold). Kept for the next load of
        the supplier list (``held``). Returns what changed, {field: {"from", "to"}}, recorded as one event; {} when
        nothing did. ``ValueError``, nothing changed: no such supplier, a column that is not changed here, or no
        name."""
        with self._conn() as conn:
            dialect.begin_write(conn)
            editable = {field.name for field in supplier_fields(conn) if field.editable}
            refused = sorted(set(changes) - editable)
            if refused:
                raise ValueError(f"{', '.join(refused)} cannot be changed here. Nothing was changed.")
            row = conn.execute("SELECT * FROM vendors WHERE vendor_key = ? AND in_master = 1", (key,)).fetchone()
            if row is None:
                raise ValueError(NO_SUPPLIER)
            wanted = {field: _supplier_text(field, value) for field, value in changes.items()}
            if "display_name" in wanted and not wanted["display_name"]:
                raise ValueError(NAME_NEEDED)
            moved = {field: (row[field] or "", value) for field, value in wanted.items() if (row[field] or "") != value}
            if moved:
                values = [after for _, after in moved.values()]
                conn.execute(f"UPDATE vendors SET {', '.join(f'{field} = ?' for field in moved)}, updated_at = ? "
                             "WHERE vendor_key = ?", (*values, _now(), key))  # fmt: skip
                kept = held(conn)
                _note(kept, SUPPLIERS, key, moved, actor)
                _keep(conn, kept)
                count_masters_change(conn)  # the checks read the supplier list (B-842)
                name = f"{row['erp_id']} {wanted.get('display_name') or row['display_name']}".strip()
                detail = {"table": SUPPLIERS, "row": name, "changes": _said(moved)}
                self._log(conn, EDITED_ACTION, actor=actor, detail=detail)
        return _said(moved)

    def _supplier_edits_held(self) -> dict[str, dict[str, Any]]:
        """Before an import of the supplier list: each supplier with a change made by hand, its row as it is now
        ({vendor key: row}; {} when no supplier has one)."""
        with self._conn() as conn:
            keys = list(held(conn)[SUPPLIERS])
            if not keys:
                return {}
            rows = conn.execute(f"SELECT * FROM vendors WHERE {_in_list('vendor_key')}", (json.dumps(keys),))
            return {row["vendor_key"]: dict(row) for row in rows}

    def _supplier_edits_put_back(self, before: dict[str, dict[str, Any]], actor: str | None = None) -> list[str]:
        """After an import of the supplier list: each field a person changed by hand that the import replaced gets
        the person's value back, what the file says kept beside it. ``before``: ``_supplier_edits_held`` as it was
        before the import; a field that was not the person's value any more then (changed elsewhere since) is let
        go. Returns the suppliers put back, each "<number> <name>", recorded as one event."""
        with self._conn() as conn:
            dialect.begin_write(conn)
            kept = held(conn)
            was = json.dumps(kept, sort_keys=True)
            keys = [key for key in kept[SUPPLIERS] if key in before]
            rows = conn.execute(f"SELECT * FROM vendors WHERE {_in_list('vendor_key')}", (json.dumps(keys),))
            put = []
            for row in [dict(found) for found in rows]:
                key, back = row["vendor_key"], {}
                fields = kept[SUPPLIERS][key]
                for field, edit in list(fields.items()):
                    if field not in row:
                        continue  # a column this version does not keep: nothing to put back
                    if (before[key].get(field) or "") != edit["value"]:
                        del fields[field]
                    elif (row[field] or "") != edit["value"]:
                        edit["file"], back[field] = row[field] or "", edit["value"]
                if back:
                    conn.execute(f"UPDATE vendors SET {', '.join(f'{field} = ?' for field in back)} "
                                 "WHERE vendor_key = ?", (*back.values(), key))  # fmt: skip
                    put.append(f"{row['erp_id']} {back.get('display_name') or row['display_name']}".strip())
                if not fields:
                    del kept[SUPPLIERS][key]
            if json.dumps(kept, sort_keys=True) != was:
                _keep(conn, kept)
            if put:
                count_masters_change(conn)
                detail = {"table": SUPPLIERS, "kept": put[:NAMED], "count": len(put)}
                self._log(conn, EDITED_ACTION, actor=actor, detail=detail)
        return put
