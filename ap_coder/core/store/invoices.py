"""Invoices: saving, reading, approving, the ERP's register, the second approval."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import time
from pathlib import Path
from typing import Any

from ...coding.line_tax import invoice_uncollected, supply_province, tax_vote
from ...coding.po import po_key
from ...coding.terms import DEFAULT_TERMS_DAYS
from ..audit import diff_coding
from ..memory import ACCEPTED, CODED, CORRECTED, pair_lines, vendor_key
from ..paths import PROJECT_DIR, default_db_path, find_moved_file, private_dir  # noqa: F401  (re-exported)
from ..reference_data import UNASSIGNED
from ..schema import as_read
from ._base import (
    _FOUND,
    _HEADER_FIELDS,
    _LOOK_AGAIN_SECONDS,
    _NOT_FOUND,
    APPROVED,
    CHECKS_STAMP_KEY,
    FAILED,
    PARKED,
    PENDING,
    REJECTED,
    REVIEW,
    ApprovedAgain,
    _due,
    _in_list,
    _norm_number,
    _now,
    _store_class,
    _tax_signature,
    line_guessed,
    nothing_proposed,
    parse_fx_rates,
    suggestion_kept,
)


class InvoicesMixin:
    # --- Invoices --------------------------------------------------------------------------------------

    def add_invoice(
        self,
        source_path: str | Path,
        output: dict[str, Any] | None,
        validation: dict[str, Any] | None,
        extraction_md: str = "",
        meta: dict[str, Any] | None = None,
        error: str | None = None,
        original_sha256: str | None = None,
    ) -> int:
        """``original_sha256``: the SHA-256 of the picture this PDF was made from (``intake.py``), so the same photo
        sent again is found as this invoice (``find_by_hash``)."""
        source_path = Path(source_path)
        sha = hashlib.sha256(source_path.read_bytes()).hexdigest() if source_path.exists() else None
        out = output or {}
        if validation:  # what it was checked against: the queue checks it again once that changes (B-815)
            validation = {CHECKS_STAMP_KEY: self.checks_stamp(), **validation}
        val = validation or {}
        with self._conn() as conn:
            if sha:  # a new attempt replaces earlier failed attempts at the same file
                conn.execute("DELETE FROM invoices WHERE file_sha256 = ? AND status = ?", (sha, FAILED))
            cur = conn.execute(
                """INSERT INTO invoices (source_path, file_name, file_sha256, status, vendor_name, vendor_key,
                   invoice_number, invoice_date, currency, grand_total, model_confidence, adjusted_confidence,
                   requires_review, ai_output, validation, extraction_md, meta, error, created_at, po_key,
                   due_date) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(source_path.resolve()),  # absolute: the dashboard may run from another folder
                    source_path.name,
                    sha,
                    FAILED if error else REVIEW,
                    out.get("vendor_name"),
                    vendor_key(out.get("vendor_name", "")),
                    out.get("invoice_number"),
                    out.get("invoice_date"),
                    out.get("currency"),
                    out.get("grand_total"),
                    val.get("model_confidence"),
                    val.get("adjusted_confidence"),
                    int(bool(val.get("requires_review", True))),
                    json.dumps(output) if output else None,
                    json.dumps(validation) if validation else None,
                    extraction_md,
                    json.dumps(meta or {}, default=str),
                    error,
                    _now(),
                    po_key(out.get("po_number") or ""),
                    _due(out, self.default_terms_days(), self.vendor_terms(vendor_key(out.get("vendor_name", ""))))
                    if out
                    else None,
                ),
            )
            invoice_id = int(cur.lastrowid)
            if original_sha256:
                # Earlier failed attempts at the same picture (before or after it was made a PDF) are replaced too.
                conn.execute("DELETE FROM invoices WHERE (original_sha256 = ? OR file_sha256 = ?) AND status = ? "
                             "AND id != ?", (original_sha256, original_sha256, FAILED, invoice_id))  # fmt: skip
                conn.execute("UPDATE invoices SET original_sha256 = ? WHERE id = ?", (original_sha256, invoice_id))
            if error:
                self._log(conn, "failed", invoice_id, detail={"file": source_path.name, "error": error})
            else:
                self._log(conn, "processed", invoice_id, detail={
                    "file": source_path.name, "vendor": out.get("vendor_name"),
                    "invoice_number": out.get("invoice_number"),
                    "total": out.get("grand_total"), "confidence": val.get("adjusted_confidence"),
                    "requires_review": bool(val.get("requires_review", True)),
                    "issues": len(val.get("issues") or []), "demo": bool((meta or {}).get("demo")),
                })  # fmt: skip
            return invoice_id

    def find_by_hash(self, path: str | Path, include_failed: bool = False) -> dict[str, Any] | None:
        """The latest invoice made from this exact file, or from this exact picture made into a PDF (failed attempts
        only if ``include_failed``)."""
        sha = hashlib.sha256(Path(path).read_bytes()).hexdigest()
        sql = "SELECT id, status FROM invoices WHERE (file_sha256 = ? OR original_sha256 = ?)"
        if not include_failed:
            sql += f" AND status != '{FAILED}'"
        with self._conn() as conn:
            row = conn.execute(sql + " ORDER BY id DESC", (sha, sha)).fetchone()
        return dict(row) if row else None

    def find_duplicates(
        self,
        vendor_name: str,
        invoice_number: str,
        exclude_id: int | None = None,
        grand_total: float | None = None,
    ) -> list[int]:
        """Other invoices with the same vendor and invoice number (possible duplicate payment).

        With ``grand_total``, a credit note (negative) is not a duplicate of the invoice it reverses.
        """
        key, number = vendor_key(vendor_name), _norm_number(invoice_number)
        if not key or not number:
            return []
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, invoice_number, grand_total FROM invoices "
                "WHERE vendor_key = ? AND status != ? AND status != ?",
                (key, FAILED, REJECTED),
            ).fetchall()

        def same_sign(other: float | None) -> bool:
            return grand_total is None or other is None or (grand_total < 0) == (other < 0)

        return [
            r["id"]
            for r in rows
            if _norm_number(r["invoice_number"]) == number and r["id"] != exclude_id and same_sign(r["grand_total"])
        ]

    def default_terms_days(self) -> int:
        """Days to pay when an invoice prints neither a due date nor terms (Settings → Review)."""
        try:
            return int(self.get_setting("default_terms_days") or DEFAULT_TERMS_DAYS)
        except ValueError:
            return DEFAULT_TERMS_DAYS

    # --- The ERP's invoice register (invoices entered before or outside AP Coder) ---------------------

    def import_erp_register(
        self, rows: list[dict[str, Any]], replace_all: bool = False, actor: str | None = None
    ) -> int:
        """Invoices already in the ERP (``registers.rows_from_records`` output). Returns how many are stored. A bill
        listed more than once in the file with another date (the same vendor, number and amount: entered twice in the
        ERP) is one row that counts its ``copies``; rows alike in their date too are one bill (a register listing a
        bill once per GL line, or JD Edwards pay items), and a register imported again, overlapping the last, does not
        add to them."""
        now = _now()
        keyed = [
            ((vendor_key(r["vendor_name"]), _norm_number(r["invoice_number"]), round(float(r["total"]), 2)), r)
            for r in rows
            if vendor_key(r["vendor_name"]) and _norm_number(r["invoice_number"]) and r.get("total") is not None
        ]
        dates: dict[tuple[str, str, float], set[str]] = {}
        for key, r in keyed:
            dates.setdefault(key, set()).add(r.get("invoice_date") or "")
        copies = {key: len(found - {""}) or 1 for key, found in dates.items()}
        with self._conn() as conn:
            if replace_all:
                conn.execute("DELETE FROM erp_invoices")
            conn.executemany(
                """INSERT INTO erp_invoices (vendor_key, number_key, total, vendor_name, invoice_number, invoice_date,
                   imported_at, copies) VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(vendor_key, number_key, total)
                   DO UPDATE SET invoice_date = excluded.invoice_date, imported_at = excluded.imported_at,
                   copies = MAX(erp_invoices.copies, excluded.copies)""",
                [(*key, r["vendor_name"], r["invoice_number"], r.get("invoice_date") or "", now, copies[key])
                 for key, r in keyed],
            )  # fmt: skip
            count = int(conn.execute("SELECT COUNT(*) FROM erp_invoices").fetchone()[0])
            detail: dict[str, Any] = {"rows": len(rows), "total": count}
            if repeats := sum(1 for n in copies.values() if n > 1):
                detail["repeated"] = repeats  # bills the file lists more than once
            self._log(conn, "erp_register_imported", actor=actor, detail=detail)
        return count

    def erp_register_repeats(self) -> int:
        """How many bills the ERP register lists more than once (entered twice in the ERP: the duplicate audit)."""
        with self._conn() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM erp_invoices WHERE copies > 1").fetchone()[0])

    def erp_register_count(self) -> int:
        with self._conn() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM erp_invoices").fetchone()[0])

    def clear_erp_register(self, actor: str | None = None) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM erp_invoices")
            self._log(conn, "erp_register_imported", actor=actor, detail={"rows": 0, "total": 0, "cleared": True})

    def search_rows(self) -> list[dict[str, Any]]:
        """Every invoice's identifying fields and where it stands (for the invoice directory's quick search), with
        the date of its export batch and its GST/HST number (the JD Edwards duplicate check finds a supplier by
        it)."""
        with self._conn() as conn:
            batches = {r["id"]: r["created_at"] for r in conn.execute("SELECT id, created_at FROM export_batches")}
            rows = [
                dict(r)
                for r in conn.execute(
                    """SELECT id, status, file_name, vendor_name, invoice_number, invoice_date, due_date, currency,
                              grand_total, po_key, created_at, reviewed_at, reviewer, second_reviewer,
                              second_reviewed_at, export_batch, parked_reason, follow_up, error,
                              json_extract(final_output, '$.gst_hst_registration_number') AS gst_hst_registration_number
                       FROM invoices ORDER BY id DESC"""
                )
            ]
        for r in rows:
            r["exported_at"] = batches.get(r["export_batch"]) if r["export_batch"] else None
        return rows

    def directory_count(self, rows_sql: str, where: str, params: list[Any]) -> int:
        """How many invoices the invoice directory's filters keep (``directory.py`` builds the SQL, with parameters)."""
        with self._conn() as conn:
            return int(conn.execute(f"{rows_sql} SELECT COUNT(*) FROM rows WHERE {where}", params).fetchone()[0])

    def directory_rows(
        self, rows_sql: str, fields: str, where: str, params: list[Any], order: str, limit: int, offset: int
    ) -> list[dict[str, Any]]:
        """One page of the invoice directory, with the date each one's export batch was made. The page's ids come
        first (SQLite then reads only what the filters and the sort need), then their rows: reading every invoice's
        coding to sort the whole table would take most of a second with tens of thousands of invoices."""
        with self._conn() as conn:
            ids = [
                r[0]
                for r in conn.execute(
                    f"{rows_sql} SELECT id FROM rows WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?",
                    [*params, limit, offset],
                )
            ]
            found: dict[int, dict[str, Any]] = {}
            for start in range(0, len(ids), 500):
                chunk = ids[start : start + 500]
                marks = ", ".join("?" * len(chunk))  # at most 500, under SQLite's variable limit
                for r in conn.execute(f"{rows_sql} SELECT {fields} FROM rows WHERE id IN ({marks})", chunk):
                    found[r["id"]] = dict(r)
            rows = [found[i] for i in ids if i in found]
            wanted = sorted({r["export_batch"] for r in rows if r["export_batch"]})
            batches = {}
            for start in range(0, len(wanted), 500):
                chunk = wanted[start : start + 500]
                marks = ", ".join("?" * len(chunk))  # at most 500, under SQLite's variable limit
                batches.update(
                    {
                        b["id"]: b["created_at"]
                        for b in conn.execute(f"SELECT id, created_at FROM export_batches WHERE id IN ({marks})", chunk)
                    }
                )
        for r in rows:
            r["exported_at"] = batches.get(r["export_batch"]) if r["export_batch"] else None
        return rows

    def erp_register(self) -> list[dict[str, Any]]:
        """Every invoice in the imported ERP register."""
        with self._conn() as conn:
            return [
                dict(r)
                for r in conn.execute(
                    "SELECT vendor_key, vendor_name, invoice_number, number_key, invoice_date, total, copies "
                    "FROM erp_invoices"
                )
            ]

    def in_erp(self, vendor_name: str, invoice_number: str, grand_total: float | None) -> list[dict[str, Any]]:
        """This vendor's invoices with the same number already in the ERP (a credit note is not a duplicate
        of the invoice it reverses)."""
        key, number = vendor_key(vendor_name), _norm_number(invoice_number)
        if not key or not number:
            return []
        with self._conn() as conn:
            rows = [
                dict(r)
                for r in conn.execute(
                    "SELECT invoice_number, invoice_date, total FROM erp_invoices "
                    "WHERE vendor_key = ? AND number_key = ?",
                    (key, number),
                )
            ]
        if grand_total is None:
            return rows
        return [r for r in rows if (r["total"] < 0) == (grand_total < 0)]

    def duplicates_elsewhere(
        self, vendor_name: str, invoice_number: str, grand_total: float, exclude_id: int | None = None
    ) -> list[dict[str, Any]]:
        """Invoices from ANOTHER vendor with the same invoice number and total: the same bill entered under a
        second vendor record (a common cause of duplicate payments)."""
        key, number = vendor_key(vendor_name), _norm_number(invoice_number)
        if not number or not grand_total:
            return []
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, vendor_name, vendor_key, invoice_number FROM invoices "
                "WHERE grand_total BETWEEN ? AND ? AND status NOT IN (?, ?) AND vendor_key != ?",  # by its index
                (grand_total - 0.010001, grand_total + 0.010001, FAILED, REJECTED, key),  # a cent, as floats
            ).fetchall()
        return [dict(r) for r in rows if r["id"] != exclude_id and _norm_number(r["invoice_number"]) == number]

    def list_invoices(self, status: str | None = None) -> list[dict[str, Any]]:
        sql = (
            "SELECT id, file_name, status, vendor_name, invoice_number, invoice_date, due_date, currency, grand_total, "
            "model_confidence, adjusted_confidence, requires_review, error, created_at, reviewed_at, reviewer, "
            "second_reviewer "
            "FROM invoices"
        )
        args: tuple[Any, ...] = ()
        if status:
            sql += " WHERE status = ?"
            args = (status,)
        sql += " ORDER BY requires_review DESC, adjusted_confidence ASC, id ASC"
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(sql, args)]

    def has_invoices(self) -> bool:
        """Whether there is any invoice at all (of any status)."""
        with self._conn() as conn:
            return conn.execute("SELECT 1 FROM invoices LIMIT 1").fetchone() is not None

    def get_invoice(self, invoice_id: int) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
        return None if row is None else self._full_invoice(row)

    def get_invoices(self, invoice_ids: list[int]) -> list[dict[str, Any]]:
        """``get_invoice`` of each of ``invoice_ids`` in one query, in that order (ones that no longer exist are left
        out): a page listing many invoices does not open the database once per invoice."""
        ids = [int(i) for i in invoice_ids]
        if not ids:
            return []
        unique = list(dict.fromkeys(ids))
        with self._conn() as conn:
            found = {
                r["id"]: r
                for r in conn.execute(f"SELECT * FROM invoices WHERE {_in_list('id')}", (json.dumps(unique),))
            }
        return [self._full_invoice(found[i], listed=True) for i in ids if i in found]

    def _full_invoice(self, row: sqlite3.Row, listed: bool = False) -> dict[str, Any]:
        """The invoice of ``row``, its file looked for where it was recorded (found again elsewhere when it moved).
        ``listed``: one of a list (``get_invoices``), whose file found a moment ago is not looked for again."""
        inv = dict(row)
        for key in ("ai_output", "final_output", "validation", "meta", "edits"):
            inv[key] = json.loads(inv[key]) if inv[key] else None
        stored = inv["source_path"]
        recent = listed and time.monotonic() - _FOUND.get(stored or "", -_LOOK_AGAIN_SECONDS) < _LOOK_AGAIN_SECONDS
        if stored and not recent:
            if Path(stored).exists():
                _FOUND[stored] = time.monotonic()
                if len(_FOUND) > 50_000:
                    _FOUND.clear()
            else:
                self.invoice_file(inv)  # moved (an update): found again, and ``inv`` points there
        return inv

    def invoice_file(self, inv: dict[str, Any]) -> Path | None:
        """The file of invoice ``inv`` (a row with ``id``, ``source_path``...), or None when it is nowhere. A file no
        longer where it was recorded (an update moved the data folder or deleted the old install's folder) is looked
        for by its name and SHA-256 in the data folder (``invoices/``, data an update kept aside in ``backups/``) and
        this install's ``samples/`` (a demo invoice); found, the invoice points there from then on (``inv`` too)."""
        stored = str(inv.get("source_path") or "")
        if not stored:
            return None
        if Path(stored).exists():
            return Path(stored)
        key = (str(self.path), int(inv.get("id") or 0), stored)
        if time.monotonic() - _NOT_FOUND.get(key, -_LOOK_AGAIN_SECONDS) < _LOOK_AGAIN_SECONDS:
            return None  # looked for a moment ago: not again on every page picture
        data = self.path.parent
        sha = inv.get("file_sha256")
        if sha is None and "file_sha256" not in inv:
            with self._conn() as conn:
                row = conn.execute("SELECT file_sha256 FROM invoices WHERE id = ?", (inv.get("id"),)).fetchone()
            sha = row["file_sha256"] if row else None
        backups = data / "backups"
        aside = sorted((p for p in backups.glob("from-*") if p.is_dir()), reverse=True) if backups.is_dir() else []
        meta = inv.get("meta")
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except ValueError:
                meta = {}
        demo = isinstance(meta, dict) and bool(meta.get("demo"))  # only a demo invoice's file is one of the samples
        places = [data / "invoices", *aside, *([PROJECT_DIR / "samples"] if demo else [])]
        found = find_moved_file(stored, sha, places, name=str(inv.get("file_name") or ""), data=data)
        if found is None:
            _NOT_FOUND[key] = time.monotonic()
            return None
        new = str(found.resolve())
        with self._conn() as conn:
            row = conn.execute("SELECT meta FROM invoices WHERE id = ? AND source_path = ?",
                               (inv.get("id"), stored)).fetchone()  # fmt: skip
            if row is not None:
                meta = json.loads(row["meta"]) if row["meta"] else {}
                if isinstance(meta, dict) and meta.get("source") == stored:
                    meta["source"] = new
                conn.execute("UPDATE invoices SET source_path = ?, meta = ? WHERE id = ?",
                             (new, json.dumps(meta, default=str) if meta else row["meta"], inv.get("id")))  # fmt: skip
                self._log(conn, "file_found", inv.get("id"), detail={"from": stored, "to": new})
        inv["source_path"] = new
        if isinstance(inv.get("meta"), dict) and inv["meta"].get("source") == stored:
            inv["meta"]["source"] = new
        return found

    def park_invoice(self, invoice_id: int, actor: str, reason: str, follow_up: str | None = None) -> None:
        """Take an invoice out of the queue while waiting for information; ``follow_up``: YYYY-MM-DD."""
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE invoices SET status = ?, parked_reason = ?, follow_up = ? WHERE id = ? AND status = ?",
                (PARKED, reason.strip(), follow_up or None, invoice_id, REVIEW),
            )
            if cur.rowcount == 0:
                raise ValueError(f"invoice {invoice_id} is not in the review queue")
            self._log(conn, "parked", invoice_id, actor, {"reason": reason.strip(), "follow_up": follow_up or ""})

    def unpark_invoice(self, invoice_id: int, actor: str) -> None:
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE invoices SET status = ?, parked_reason = NULL, follow_up = NULL WHERE id = ? AND status = ?",
                (REVIEW, invoice_id, PARKED),
            )
            if cur.rowcount == 0:
                raise ValueError(f"invoice {invoice_id} is not parked")
            self._log(conn, "unparked", invoice_id, actor)

    def parked(self) -> list[dict[str, Any]]:
        """Parked invoices, the ones to follow up first."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, vendor_name, invoice_number, invoice_date, currency, grand_total, parked_reason, "
                "follow_up FROM invoices WHERE status = ? ORDER BY follow_up IS NULL, follow_up, id",
                (PARKED,),
            ).fetchall()
        return [dict(r) for r in rows]

    def add_note(self, invoice_id: int, actor: str, text: str) -> None:
        """A note on an invoice for the team (kept in its history)."""
        if text.strip():
            self.log_event("note", invoice_id=invoice_id, actor=actor, detail={"text": text.strip()[:2000]})

    def reject_invoice(self, invoice_id: int, reviewer: str, reason: str = "") -> None:
        """Reject an invoice in the review queue, or a parked one. An approved invoice cannot be: a Reject clicked
        on a screen opened before someone approved it would wipe a finished approval (reopen it first). Anything
        learned from the invoice is withdrawn, as when it is reopened."""
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE invoices SET status = ?, reviewer = ?, reviewed_at = ?, second_reviewer = NULL, "
                "second_reviewed_at = NULL, parked_reason = NULL, follow_up = NULL, error = ? "
                "WHERE id = ? AND status IN (?, ?) AND export_batch IS NULL",
                (REJECTED, reviewer, _now(), reason or None, invoice_id, REVIEW, PARKED),
            )
            if cur.rowcount == 0:
                raise ValueError(
                    f"invoice {invoice_id} cannot be rejected (approved, rejected or deleted meanwhile: only an "
                    "invoice in the review queue or parked can be)"
                )
            layout = self._forget_learning(conn, invoice_id)
            self._log(conn, "rejected", invoice_id, reviewer, {"reason": reason, **layout})

    @staticmethod
    def _forget_learning(conn: sqlite3.Connection, invoice_id: int) -> dict[str, Any]:
        """Withdraw what an invoice's approval taught: its coding memory, its lessons (what the AI is told), its
        supplier outcomes (from a review or an audit sample alike, so none keeps counting towards the supplier's
        autonomy), its reader scores and what it taught the supplier's template (``_withdraw_template``). Returns
        what became of the template, for the event's detail ({} when the approval taught it nothing)."""
        for table in ("feedback", "supplier_outcomes", "reader_outcomes"):
            conn.execute(f"DELETE FROM {table} WHERE invoice_id = ?", (invoice_id,))
        _store_class()._withdraw_lessons(conn, invoice_id)
        return _store_class()._withdraw_template(conn, invoice_id)

    @staticmethod
    def _withdraw_template(conn: sqlite3.Connection, invoice_id: int) -> dict[str, Any]:
        """The supplier's template as it was before ``invoice_id``'s approval taught it (``template_snapshots``),
        put back, unless a later approval changed it since: that one is kept, and said (D-71)."""
        snap = conn.execute("SELECT * FROM template_snapshots WHERE invoice_id = ?", (invoice_id,)).fetchone()
        if snap is None:
            return {}
        conn.execute("DELETE FROM template_snapshots WHERE invoice_id = ?", (invoice_id,))
        now = conn.execute("SELECT template_json FROM supplier_profiles WHERE key = ?",
                           (snap["supplier_key"],)).fetchone()  # fmt: skip
        if now is None:
            return {}
        if now["template_json"] != snap["after_json"]:
            # The later approval's snapshot started from what this one taught: it now starts from what was there
            # before this one, so withdrawing it too takes back both (B-781).
            conn.execute("UPDATE template_snapshots SET before_json = ? WHERE supplier_key = ? AND before_json IS ?",
                         (snap["before_json"], snap["supplier_key"], snap["after_json"]))  # fmt: skip
            return {"layout": "kept: a later approval changed the vendor's layout since, so it was not put back"}
        conn.execute("UPDATE supplier_profiles SET template_json = ?, updated_at = ? WHERE key = ?",
                     (snap["before_json"], _now(), snap["supplier_key"]))  # fmt: skip
        # A later snapshot of this supplier started from what this approval taught: it now starts from what is back.
        conn.execute("UPDATE template_snapshots SET before_json = ? WHERE supplier_key = ? AND before_json IS ?",
                     (snap["before_json"], snap["supplier_key"], snap["after_json"]))  # fmt: skip
        return {"layout": "put back as it was before this approval"}

    def delete_invoice(
        self, invoice_id: int, forget_lessons: bool = False, actor: str | None = None, *, keep_approved: bool = False
    ) -> None:
        """Delete an invoice; with ``forget_lessons`` also what was learned when it was approved. ``keep_approved``:
        refuse (ValueError, nothing deleted) an invoice approved or waiting for its second approval, e.g. approved
        and exported by someone else since a Delete button was shown for it (reopen it first)."""
        with self._conn() as conn:
            if keep_approved:
                conn.execute("BEGIN IMMEDIATE")  # checked and deleted in one go: an approval cannot slip in between
            row = conn.execute(
                "SELECT file_name, vendor_name, invoice_number, status FROM invoices WHERE id = ?", (invoice_id,)
            ).fetchone()
            if keep_approved and row is not None and row["status"] in (APPROVED, PENDING):
                raise ValueError(f"invoice {invoice_id} was approved meanwhile: reopen it before deleting it")
            conn.execute("DELETE FROM invoices WHERE id = ?", (invoice_id,))
            # What capture read, and the supplier accuracy measured on it, go with the invoice: a deleted
            # invoice (a duplicate upload, a demo) must not count towards a supplier's autonomy, a reader's
            # record or the confidence calibration, and is no longer waiting for the page reader.
            for table in (
                "invoice_capture",
                "supplier_outcomes",
                "reader_outcomes",
                "page_reads",
                "azure_checks",
                "verifications",
                "invoice_drafts",
            ):
                conn.execute(f"DELETE FROM {table} WHERE invoice_id = ?", (invoice_id,))
            if row is not None:
                self._log(conn, "deleted", invoice_id, actor, {
                    "file": row["file_name"], "vendor": row["vendor_name"], "invoice_number": row["invoice_number"],
                    "status": row["status"], "lessons_forgotten": forget_lessons,
                })  # fmt: skip
            if forget_lessons:
                conn.execute("DELETE FROM feedback WHERE invoice_id = ?", (invoice_id,))
                self._withdraw_lessons(conn, invoice_id)
                self._withdraw_template(conn, invoice_id)
            else:
                conn.execute("DELETE FROM template_snapshots WHERE invoice_id = ?", (invoice_id,))

    # --- The review screen's drafts: edits not approved yet (D-86) ---------------------------------------------------
    # One row per invoice and person ("" this computer, else a colleague's key on the office network: web/pages/
    # invoice.py), so the edits outlive a restart, as the screen says. Dropped on approval, rejection, Start over.

    def save_invoice_draft(self, invoice_id: int, person: str, draft: dict[str, Any]) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO invoice_drafts (invoice_id, person, draft, updated_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(invoice_id, person) DO UPDATE SET draft = excluded.draft, "
                "updated_at = excluded.updated_at",
                (invoice_id, person, json.dumps(draft), _now()),
            )

    def invoice_drafts(self) -> list[tuple[int, str, dict[str, Any]]]:
        """(invoice id, person, draft) of every invoice still in the review queue; a draft that can't be read is left
        out."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT d.invoice_id, d.person, d.draft FROM invoice_drafts d JOIN invoices i ON i.id = d.invoice_id "
                "WHERE i.status = ? ORDER BY d.updated_at",
                (REVIEW,),
            ).fetchall()
        out = []
        for r in rows:
            try:
                draft = json.loads(r["draft"])
            except ValueError:
                continue
            if isinstance(draft, dict):
                out.append((r["invoice_id"], r["person"], draft))
        return out

    def drop_invoice_drafts(self, invoice_id: int | None = None, person: str | None = None) -> None:
        """Forget drafts: an invoice's (one person's, or everyone's), or every one (``invoice_id`` None)."""
        where, params = [], []
        if invoice_id is not None:
            where.append("invoice_id = ?")
            params.append(invoice_id)
        if person is not None:
            where.append("person = ?")
            params.append(person)
        with self._conn() as conn:
            conn.execute("DELETE FROM invoice_drafts" + (" WHERE " + " AND ".join(where) if where else ""), params)

    def list_invoices_full(self) -> list[dict[str, Any]]:
        """Every invoice with its file path, status and metadata (no AI output: kept light)."""
        with self._conn() as conn:
            rows = [dict(r) for r in conn.execute("SELECT id, source_path, status, meta FROM invoices ORDER BY id")]
        for r in rows:
            r["meta"] = json.loads(r["meta"]) if r["meta"] else {}
        return rows

    _JSON_COLUMNS = ("ai_output", "final_output", "validation", "meta", "edits")
    _LIGHT_COLUMNS = (
        "id", "status", "requires_review", "created_at", "reviewed_at", "reviewer", "second_reviewer",
        "second_reviewed_at", "invoice_date", "due_date", "export_batch", "source_path", "file_name", *_JSON_COLUMNS
    )  # fmt: skip

    def check_stamps(self, ids: list[int]) -> dict[int, tuple[str, str]]:
        """{id: (the stamp its stored checks were made against, the stamp of its queue re-check)} ("" for none),
        read by SQLite without decoding each validation (``Store.checks_stamp``, B-815)."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, json_extract(validation, '$.checks_stamp'), json_extract(validation, '$.recheck.stamp') "
                f"FROM invoices WHERE validation IS NOT NULL AND json_valid(validation) AND {_in_list('id')}",
                (json.dumps([int(i) for i in ids]),),
            ).fetchall()
        return {int(r[0]): (str(r[1] or ""), str(r[2] or "")) for r in rows}

    def save_recheck(self, invoice_id: int, stamp: str, issues: list[dict[str, Any]], seen: str | None) -> bool:
        """Keep the queue's re-check of an invoice in review (``coding.bulk.issues_today``) with its stored checks,
        beside them (``recheck``: the stamp and the findings): they stay as they were found. Only while the stored
        checks are still ``seen`` (their text as read): True when kept."""
        recheck = json.dumps({"stamp": stamp, "issues": issues}, default=str)
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE invoices SET validation = json_set(validation, '$.recheck', json(?)) "
                "WHERE id = ? AND status = ? AND validation = ?",
                (recheck, int(invoice_id), REVIEW, seen),
            )
        return cur.rowcount > 0

    def invoice_columns(
        self, columns: tuple[str, ...], status: str | None = None, ids: list[int] | None = None, decode: bool = True
    ) -> list[dict[str, Any]]:
        """Just these columns of every invoice, or of ``ids`` (JSON ones decoded, unless ``decode`` is False: their
        text as stored): cheaper than ``get_invoice`` in a loop."""
        unknown = set(columns) - set(self._LIGHT_COLUMNS)
        if unknown:
            raise ValueError(f"unknown columns {sorted(unknown)}")
        where, args = [], []
        if status:
            where.append("status = ?")
            args.append(status)
        if ids is not None:
            where.append(_in_list("id"))
            args.append(json.dumps([int(i) for i in ids]))
        sql = f"SELECT {', '.join(columns)} FROM invoices" + (" WHERE " + " AND ".join(where) if where else "")
        with self._conn() as conn:
            rows = [dict(r) for r in conn.execute(sql + " ORDER BY id", args)]
        for r in rows if decode else []:
            for key in set(columns) & set(self._JSON_COLUMNS):
                r[key] = json.loads(r[key]) if r[key] else None
        return rows

    def demo_count(self) -> int:
        """How many demo invoices are loaded (see ``demo.py``), without reading every invoice."""
        with self._conn() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM invoices WHERE meta LIKE '%\"demo\": true%'").fetchone()[0])

    def approved_since(self, since_iso: str, reviewer: str | None = None) -> int:
        """Invoices approved since ``since_iso`` (and still approved, or waiting for a second approver). With
        ``reviewer``: only those this person approved, first or second approval (names compared without case)."""
        with self._conn() as conn:
            if reviewer is None:
                sql = "SELECT COUNT(*) FROM invoices WHERE status IN (?, ?) AND reviewed_at >= ?"
                return int(conn.execute(sql, (APPROVED, PENDING, since_iso)).fetchone()[0])
            rows = conn.execute(
                "SELECT reviewer, reviewed_at >= ?, second_reviewer, second_reviewed_at >= ? FROM invoices "
                "WHERE status IN (?, ?) AND (reviewed_at >= ? OR second_reviewed_at >= ?)",
                (since_iso, since_iso, APPROVED, PENDING, since_iso, since_iso),
            ).fetchall()
        # Compared here, not in SQL: SQLite's lower() leaves accented letters alone ("Élise" never matched).
        me = reviewer.strip().casefold()
        return sum(
            1
            for first, first_since, second, second_since in rows
            if (first_since and (first or "").strip().casefold() == me)
            or (second_since and (second or "").strip().casefold() == me)
        )

    def approve_invoice(
        self,
        invoice_id: int,
        final_output: dict[str, Any],
        reviewer: str,
        bulk: bool = False,
        open_issues: list[dict[str, Any]] | None = None,
        login: str = "",
    ) -> dict[str, int]:
        """Store the reviewer's final version and record one feedback row per line. ``open_issues``: the
        errors and warnings still showing when the reviewer approved (kept in the audit trail). Only an invoice
        in the review queue can be approved. An approval by AP Coder on its own (``AUTONOMOUS_REVIEWER``)
        records no feedback: nobody checked its coding, so it is no lesson and no measure of accuracy."""
        from ...capture.workflow import AUTONOMOUS_REVIEWER

        inv = self.get_invoice(invoice_id)
        if inv is None:
            raise KeyError(invoice_id)
        if inv["status"] in (APPROVED, PENDING):
            raise ValueError(f"invoice {invoice_id} is already approved")
        if inv["status"] == REJECTED:  # e.g. rejected by someone else meanwhile: reopen it first
            raise ValueError(f"invoice {invoice_id} was rejected: reopen it before approving it")
        if inv["status"] == PARKED:  # parked by someone else meanwhile: waiting for information
            raise ValueError(f"invoice {invoice_id} is parked: bring it back to the queue before approving it")
        if inv["status"] != REVIEW:  # a failed read has nothing checked to approve
            raise ValueError(f"invoice {invoice_id} is not in the review queue ({inv['status']})")
        ai = inv["ai_output"] or {"line_items": []}
        second_reason = self.second_approval_reason(final_output)
        needs_second = bool(second_reason)
        vendor_name = final_output.get("vendor_name", "")
        key = vendor_key(vendor_name)
        now = _now()
        counts = {ACCEPTED: 0, CORRECTED: 0, CODED: 0}
        reviewer_changed = reviewer_coded = 0
        # Lines a fixed coding rule set: the AI's own answer is what its accuracy is measured on.
        by_rule = {c["line_number"]: c for c in (inv.get("meta") or {}).get("rules_applied") or []}
        feedback_rows = []

        kept = suggestion_kept
        # Without a cost center list the review screen has none to show or keep: a cost center the AI copied from
        # coding memory is not part of what it proposed (B-562).
        cost_centers = bool(self.list_accounts("cost_centers"))

        final_lines = final_output.get("line_items", [])
        # Where it is supplied, for the tax code each line is remembered with (D-80).
        province = supply_province(final_output, self.our_province())
        # And the provincial taxes the vendor did not collect, as the coder saw the invoice (what it read).
        missing = "+".join(invoice_uncollected(ai if ai.get("line_items") else final_output, province))
        for suggestion, li in pair_lines(ai.get("line_items", []), final_lines):
            s_gl = suggestion.get("predicted_gl_code") if suggestion else None
            s_cc = (suggestion.get("predicted_cost_center", "") if cost_centers else "") if suggestion else None
            f_gl, f_cc = li["predicted_gl_code"], li.get("predicted_cost_center", "")
            if f_gl == UNASSIGNED:
                continue  # not a coding decision: nothing to learn from it
            rule = by_rule.get(suggestion.get("line_number")) if suggestion else None
            guess = line_guessed(suggestion, rule)
            kept_rule = rule is not None and rule.get("gl_to") == f_gl and (rule.get("cc_to") or "") == (f_cc or "")
            # A kept guess is no edit; another account in its place is (B-479), as any change to what was proposed.
            blank = nothing_proposed(s_gl, s_cc)
            replaced = (
                (f_gl != s_gl or (f_cc or "") != (s_cc or ""))
                if guess
                else not blank and not kept(s_gl, s_cc, f_gl, f_cc)
            )
            if not kept_rule and replaced:
                reviewer_changed += 1
            if not kept_rule and blank and not guess:  # coded by hand where the AI left the line blank (B-670)
                reviewer_coded += 1
            if rule is not None:  # the AI's own answer, as first recorded (not a rule's)
                s_gl, s_cc = rule.get("gl_from"), rule.get("cc_from", "")
            # Accuracy measures what the AI proposed: the blanks it left are not counted against it. A line whose
            # account it only guessed, or left blank, is the reviewer's own coding: neither confirmed nor corrected
            # (B-475, B-650). (The rule's own answer decides: a rule that filled a blank leaves the line coded.)
            outcome = (
                CODED if guess or nothing_proposed(s_gl, s_cc) else ACCEPTED if kept(s_gl, s_cc, f_gl, f_cc)
                else CORRECTED
            )  # fmt: skip
            counts[outcome] += 1
            feedback_rows.append(
                (
                    invoice_id,
                    li["line_number"],
                    key,
                    vendor_name,
                    li["description"],
                    li.get("amount"),
                    s_gl,
                    f_gl,
                    s_cc,
                    f_cc,
                    outcome,
                    reviewer,
                    now,
                    *tax_vote(
                        suggestion,
                        li,
                        ai.get("line_items", []),
                        ai.get("tax_lines") or [],
                        final_lines,
                        final_output.get("tax_lines") or [],
                        bulk=bulk,
                    ),
                    province,
                    missing,
                )  # fmt: skip
            )
        read = as_read(final_output, ai)  # a tax number filled from the vendor list is no edit (B-681)
        edits = [f for f in _HEADER_FIELDS if str(ai.get(f, "")) != str(read.get(f, ""))]
        if _tax_signature(ai) != _tax_signature(final_output):
            edits.append("tax_lines")
        if reviewer_changed:  # what the reviewer changed (a rule's coding kept is not a change)
            edits.append("line_coding")
        if reviewer_coded:  # no correction, but not the AI's coding either: never "approved without any edit"
            edits.append("line_coded")
        if len(ai.get("line_items", [])) != len(final_output.get("line_items", [])):
            edits.append("line_count")
        with self._conn() as conn:
            # Claim the invoice first, in the same transaction as the feedback: if two approvals race,
            # the second finds it already approved and records nothing.
            cur = conn.execute(
                """UPDATE invoices SET status = ?, final_output = ?, edits = ?, reviewer = ?, reviewed_at = ?,
                   vendor_name = ?, vendor_key = ?, invoice_number = ?, invoice_date = ?, grand_total = ?,
                   currency = ?, po_key = ?, due_date = ?, second_reviewer = NULL, second_reviewed_at = NULL
                   WHERE id = ? AND status = ?""",
                (
                    PENDING if needs_second else APPROVED,
                    json.dumps(final_output),
                    json.dumps(edits),
                    reviewer,
                    now,
                    vendor_name,
                    key,
                    final_output.get("invoice_number"),
                    final_output.get("invoice_date"),
                    final_output.get("grand_total"),
                    str(final_output.get("currency") or "").strip().upper() or None,
                    po_key(final_output.get("po_number") or ""),
                    _due(final_output, self.default_terms_days(), self.vendor_terms(key)),
                    invoice_id,
                    REVIEW,
                ),  # fmt: skip
            )
            if cur.rowcount == 0:
                raise ValueError(f"invoice {invoice_id} left the review queue (approved, parked or rejected)")
            if reviewer == AUTONOMOUS_REVIEWER:
                feedback_rows = []  # nobody checked it: nothing to learn from, nothing to measure accuracy on
            self._log(conn, "approved", invoice_id, reviewer, {
                "lines": len(final_output.get("line_items", [])), "corrected": counts[CORRECTED],
                **({"coded": counts[CODED]} if counts[CODED] else {}),
                "total": final_output.get("grand_total"), "changes": diff_coding(ai, final_output),
                **({"bulk": True} if bulk else {}),
                **({"needs_second": True, "second_reason": second_reason} if needs_second else {}),
                **({"open_issues": open_issues} if open_issues else {}), **({"login": login} if login else {}),
            })  # fmt: skip
            conn.executemany(
                """INSERT INTO feedback (invoice_id, line_number, vendor_key, vendor_name, description, amount,
                   suggested_gl, final_gl, suggested_cc, final_cc, outcome, reviewer, created_at, final_tax_code,
                   tax_source, tax_province, tax_uncollected)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                feedback_rows,
            )
        return counts

    # --- Second approval -----------------------------------------------------------------------------

    def fx_rates(self) -> dict[str, float]:
        """{currency: CAD per unit}, as AP set them in Settings (e.g. "USD=1.37, EUR 1.50")."""
        return parse_fx_rates(self.get_setting("fx_rates"))

    def approval_limit(self) -> float:
        """Invoices above this amount need a second approver (0: no limit). Settings → Review."""
        try:
            return max(float(self.get_setting("approval_limit") or 0), 0.0)
        except ValueError:
            return 0.0

    def second_approval_reason(self, coding: dict[str, Any], limit: float | None = None, *,
                               unknown_rate: bool = True) -> str:  # fmt: skip
        """Why this invoice needs a second approver ("" when it doesn't). The limit is in CAD: a foreign-currency total
        is converted at the rate set in Settings. Without a rate it is never taken as CAD (9,000 EUR is about 13,000
        CAD): it needs the second approver, whatever its total (``unknown_rate``; False: left out, for a caller that
        deals with a missing rate itself, like the touchless gates)."""
        limit = self.approval_limit() if limit is None else limit
        if not limit:
            return ""
        currency = str(coding.get("currency") or "CAD").strip().upper()
        rate = self.fx_rates().get(currency)
        if rate is None:
            return f"no CAD rate for {currency}: a second approval is needed" if unknown_rate else ""
        total = abs(float(coding.get("grand_total") or 0))
        if not math.isfinite(total) or total * rate > limit:  # a NaN total is never "over" anything: refused here
            return f"over the approval limit of {limit:,.2f} CAD"
        return ""

    def over_approval_limit(self, coding: dict[str, Any], limit: float | None = None, *,
                            unknown_rate: bool = True) -> bool:  # fmt: skip
        """Does this invoice need a second approver (``second_approval_reason``)?"""
        return bool(self.second_approval_reason(coding, limit, unknown_rate=unknown_rate))

    def final_approve(self, invoice_id: int, approver: str, login: str = "", shown: str | None = None) -> None:
        """The second approval: by someone other than the first approver (another name and, when the
        dashboard records it, another computer login). The invoice can then be exported. ``shown``: the first
        approval the page showed (its ``approved`` event's id, as the page posts it): approved again since, it is
        refused (``ApprovedAgain``), as the version now waiting is not the one the approver looked at (B-654)."""
        inv = self.get_invoice(invoice_id)
        if inv is None or inv["status"] != PENDING:
            raise ValueError(f"invoice {invoice_id} is not waiting for a second approval")
        if (approver or "").strip().casefold() == (inv["reviewer"] or "").strip().casefold():
            raise PermissionError("the second approval must come from someone other than the first approver")
        first = next((e for e in self.events(invoice_id) if e["action"] == "approved"), None)
        first_login = ((first or {}).get("detail") or {}).get("login") or ""
        if login and first_login and login.casefold() == first_login.casefold():
            raise PermissionError("the second approval must come from another computer login than the first")
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")  # checked and approved in one go: no new first approval in between
            latest = conn.execute(
                "SELECT MAX(id) FROM events WHERE invoice_id = ? AND action = 'approved'", (invoice_id,)
            ).fetchone()[0]
            if shown is not None and str(shown).strip() != str(latest or ""):
                raise ApprovedAgain(f"invoice {invoice_id} was approved again since the page was opened")
            cur = conn.execute(
                # Same first approval as checked above: it may have been sent back and re-approved meanwhile.
                "UPDATE invoices SET status = ?, second_reviewer = ?, second_reviewed_at = ? "
                "WHERE id = ? AND status = ? AND reviewer IS ? AND reviewed_at IS ?",
                (APPROVED, approver, _now(), invoice_id, PENDING, inv["reviewer"], inv["reviewed_at"]),
            )
            if cur.rowcount == 0:
                raise ValueError(f"invoice {invoice_id} is not waiting for a second approval")
            self._log(conn, "final_approved", invoice_id, approver, {
                "first_approver": inv["reviewer"], "total": (inv["final_output"] or {}).get("grand_total"),
                **({"login": login} if login else {}),
            })  # fmt: skip

    def send_back(self, invoice_id: int, actor: str, reason: str = "") -> None:
        """Return an invoice waiting for a second approval to the review queue. What was learned from the
        first approval is withdrawn: it is learned again when the invoice is approved again."""
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE invoices SET status = ?, reviewer = NULL, reviewed_at = NULL WHERE id = ? AND status = ?",
                (REVIEW, invoice_id, PENDING),
            )
            if cur.rowcount == 0:
                raise ValueError(f"invoice {invoice_id} is not waiting for a second approval")
            layout = self._forget_learning(conn, invoice_id)
            self._log(conn, "sent_back", invoice_id, actor, {"reason": reason, **layout})

    def refresh_confidence(self, invoice_id: int, adjusted: float, requires_review: bool) -> bool:
        """The confidence the review screen just worked out (with the reviewer's edits and today's checks), so
        the queue card shows the same number. Only for an invoice still in review; True when it changed."""
        with self._conn() as conn:
            cur = conn.execute(
                """UPDATE invoices SET adjusted_confidence = ?, requires_review = ?
                   WHERE id = ? AND status = ? AND (adjusted_confidence IS NULL OR ABS(adjusted_confidence - ?) > 0.0005
                   OR requires_review != ?)""",
                (adjusted, int(requires_review), invoice_id, REVIEW, adjusted, int(requires_review)),
            )
            return cur.rowcount > 0

    def reopen(self, invoice_id: int, actor: str, reason: str = "", *, only_rejected: bool = False) -> None:
        """Put an approved invoice that is not exported yet, or a rejected one, back in the review queue to be
        corrected. What its approval taught is withdrawn (learned again at the next approval); the reviewer's
        coding is kept as the starting point. An exported invoice is reopened by undoing its batch first.
        ``only_rejected``: a rejected invoice only (a Reopen button shown on a rejected invoice that someone has
        reopened and approved since must not reopen that approval)."""
        approved = "" if only_rejected else f"status IN ('{APPROVED}', '{PENDING}') AND export_batch IS NULL OR "
        with self._conn() as conn:
            cur = conn.execute(
                f"""UPDATE invoices SET status = ?, reviewer = NULL, reviewed_at = NULL, second_reviewer = NULL,
                   second_reviewed_at = NULL, error = NULL WHERE id = ? AND ({approved}status = ?)""",
                (REVIEW, invoice_id, REJECTED),
            )
            if cur.rowcount == 0:
                raise ValueError(f"invoice {invoice_id} cannot be reopened (exported, or not approved or rejected)")
            layout = self._forget_learning(conn, invoice_id)
            self._log(conn, "reopened", invoice_id, actor, {"reason": reason, **layout})
