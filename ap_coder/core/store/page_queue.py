"""The page reader's queue (``page_reads``, ``ap_coder/ai/page_worker.py``): the invoices waiting for OvisOCR2, the one
each reader is reading, and where an invoice is in line.

* **Taken in order** (D-171): a person's "read now" first (``requested_by``), then a new invoice of a touchless
  supplier that waits only for the page reader (``TOUCHLESS_NEXT``), then the one due soonest, then the one waiting
  longest. Two readers never take the same invoice (one write transaction from the choice to the mark).
* **A heartbeat on a read under way:** its reader renews it every ``READING_RENEW_SECONDS`` (``renew_page_read``); one
  not renewed for ``READING_STALE_SECONDS`` (its process paused, or gone on another computer) is in line again,
  compared on the database's clock (``renewed``: a change of the clocks never makes a read look stale, B-1770). A
  reader whose process stopped on this computer is put back at once (``release_page_reads``); one whose read another
  took meanwhile ends nothing of it (``finish_page_read(reader=)``).
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from typing import Any

from ._base import (
    _IN_LINE,
    _STALE,
    PAGE_DONE,
    PAGE_FAILED,
    PAGE_READING,
    PAGE_SKIPPED,
    PAGE_WAITING,
    PARKED,
    PENDING,
    REVIEW,
    _now,
)

READING_RENEW_SECONDS = 60.0  # a reader renews the read (and the AI reviewer the review) it has under way this often
READING_STALE_SECONDS = 300.0  # a read or review not renewed this long is in line again (it was STALE_READING_HOURS)
# The reason a new invoice is put in line with when touchless processing waits only for the page reader to approve it
# (its supplier is touchless): read before the others but a person's.
TOUCHLESS_NEXT = "new invoice, touchless supplier"
# The order the queue is taken in, as SQL on the queue's row ``p`` and its invoice ``i``, each ascending: a person's
# first, then a touchless supplier's, then the earliest due (one without a due date last), then the oldest.
_ORDER = ("p.requested_by = ''", f"p.reason != '{TOUCHLESS_NEXT}'", "i.due_date IS NULL", "COALESCE(i.due_date, '')",
          "p.created_at", "p.invoice_id")  # fmt: skip
_LINE = f"(p.status = 'waiting' OR (p.status = 'reading' AND {_STALE.format(t='p.')}))"  # _IN_LINE on the row ``p``
_ROWS = "page_reads p LEFT JOIN invoices i ON i.id = p.invoice_id"


def _reading_stale_before() -> str:
    """A read (or review) under way last renewed before this local time, by a row renewed before the queue had the
    database's clock, is no longer under way: it is in line again."""
    return (dt.datetime.now() - dt.timedelta(seconds=READING_STALE_SECONDS)).isoformat(timespec="seconds")


def _stale() -> tuple[float, str]:
    """What ``_STALE`` (``_IN_LINE``, ``_LINE``, ``_VERIFY_IN_LINE``) is asked with: a read or a review under way not
    renewed for READING_STALE_SECONDS is in line again."""
    return float(READING_STALE_SECONDS), _reading_stale_before()


class PageQueueMixin:
    def queue_page_read(self, invoice_id: int, reason: str, requested_by: str = "") -> bool:
        """Put an invoice in line for the page reader. One already waiting keeps its place in line (a person
        asking for it is noted); one read before goes to the back of the line; a read under way is left to
        finish (False)."""
        now = _now()
        waiting = "page_reads.status IN ('waiting', 'reading')"  # past the WHERE below, a reading row is stale
        keep = f"{waiting} AND excluded.requested_by = ''"  # a background request never replaces a person's
        with self._conn() as conn:  # type: ignore[attr-defined]
            cur = conn.execute(
                f"""INSERT INTO page_reads (invoice_id, status, reason, requested_by, created_at, updated_at)
                    VALUES (?, 'waiting', ?, ?, ?, ?) ON CONFLICT(invoice_id) DO UPDATE SET status = 'waiting',
                    reason = CASE WHEN {keep} THEN page_reads.reason ELSE excluded.reason END,
                    requested_by = CASE WHEN {keep} THEN page_reads.requested_by ELSE excluded.requested_by END,
                    created_at = CASE WHEN {waiting} THEN page_reads.created_at ELSE excluded.created_at END,
                    error = '', tries = 0, reader = '', updated_at = excluded.updated_at
                    WHERE page_reads.status != 'reading' OR {_STALE.format(t="page_reads.")}""",
                (invoice_id, reason or "", requested_by or "", now, now, *_stale()),
            )
            return cur.rowcount > 0

    def next_page_read(self, invoice_id: int | None = None, reader: str = "") -> dict[str, Any] | None:
        """The invoice the page reader should read next (in the queue's order: a person's first, then a touchless
        supplier's, then the earliest due, then the one waiting longest), marked as being read (by ``reader``: the
        process reading it); None when nothing waits. With ``invoice_id``: that invoice, if it is in line (the one
        AP asked for, read first), else None. Two readers (the dashboard's and ``read-pages``) never take the same
        invoice."""
        now, stale = _now(), _stale()
        with self._conn() as conn:  # type: ignore[attr-defined]
            conn.execute("BEGIN IMMEDIATE")  # the write lock from the choice to the mark
            if invoice_id is None:
                row = conn.execute(
                    f"SELECT p.invoice_id FROM {_ROWS} WHERE {_LINE} ORDER BY {', '.join(_ORDER)} LIMIT 1", stale
                ).fetchone()
            else:
                row = conn.execute(
                    f"SELECT invoice_id FROM page_reads WHERE invoice_id = ? AND {_IN_LINE}", (invoice_id, *stale)
                ).fetchone()
            if row is None:
                return None
            conn.execute(
                "UPDATE page_reads SET status = 'reading', error = '', reader = ?, updated_at = ?, renewed = "
                "julianday('now') WHERE invoice_id = ?",
                (reader or "", now, row[0]),
            )
            taken = conn.execute("SELECT * FROM page_reads WHERE invoice_id = ?", (row[0],)).fetchone()
        return dict(taken)

    def renew_page_read(self, invoice_id: int, reader: str) -> bool:
        """The heartbeat of a read under way: still ``reader``'s, marked afresh (``READING_RENEW_SECONDS``). False once
        it is no longer that reader's (put back in line meanwhile, or taken by another)."""
        with self._conn() as conn:  # type: ignore[attr-defined]
            return conn.execute(
                "UPDATE page_reads SET updated_at = ?, renewed = julianday('now') WHERE invoice_id = ? AND status = "
                "'reading' AND reader = ?",
                (_now(), invoice_id, reader or ""),
            ).rowcount > 0  # fmt: skip

    def skip_page_read(self, invoice_id: int, reason: str, why: str) -> bool:
        """Record that the page reader leaves a new invoice out (``skipped``, ``why``), never put in line (D-111); an
        invoice already in line is left as it is. False then."""
        now = _now()
        with self._conn() as conn:  # type: ignore[attr-defined]
            cur = conn.execute(
                "INSERT INTO page_reads (invoice_id, status, reason, error, created_at, updated_at) VALUES "
                "(?, 'skipped', ?, ?, ?, ?) ON CONFLICT(invoice_id) DO NOTHING",
                (invoice_id, reason or "", why or "", now, now),
            )
            return cur.rowcount > 0

    def take_chat_deferred(self, in_line_too: bool = False) -> int | None:
        """The oldest invoice waiting in the review queue untouched whose arrival put off the chat model's accounts
        (``meta.chat_deferred``, D-168) and that no page read asks again for: none done nor in line (``in_line_too``:
        the page reader can't read now, those in line too). Its mark cleared as it is taken: it is asked again once,
        by one reader (B-1765). None when none waits."""
        with self._conn() as conn:  # type: ignore[attr-defined]
            row = conn.execute(
                "SELECT i.id FROM invoices i WHERE i.status = ? AND i.meta LIKE '%\"chat_deferred\": true%' AND CASE "
                "WHEN json_valid(i.meta) THEN json_extract(i.meta, '$.chat_deferred') END = 1 AND i.final_output IS "
                "NULL AND COALESCE(i.reviewer, '') = '' AND COALESCE(i.edits, 'null') IN ('null', '[]', '{}', '') AND "
                "NOT EXISTS (SELECT 1 FROM page_reads p WHERE p.invoice_id = i.id AND (p.status = 'done' OR (p.status "
                "IN ('waiting', 'reading') AND NOT ?))) ORDER BY i.id LIMIT 1", (REVIEW, int(in_line_too)),
            ).fetchone()  # fmt: skip
            if row is None:
                return None
            taken = conn.execute(
                "UPDATE invoices SET meta = json_set(meta, '$.chat_deferred', json('false')) WHERE id = ? AND "
                "json_extract(meta, '$.chat_deferred') = 1", (row[0],),
            ).rowcount  # fmt: skip
        return int(row[0]) if taken else None

    def finish_page_read(
        self, invoice_id: int, status: str, model: str = "", pages: int = 0, seconds: float = 0.0, error: str = "",
        *, tries: int | None = None, only_if_reading: bool = False, reader: str | None = None,
    ) -> bool:  # fmt: skip
        """How a page read ended: ``done``, ``failed`` (``error`` says why), ``skipped`` (nothing to read, or the
        invoice was dealt with meanwhile) or ``waiting`` (stopped part-way: it keeps its place in line). ``tries``:
        the reads cut off by the model server so far (None: as it was; ``done`` starts again from 0).
        ``only_if_reading``: only while it is still marked as being read; ``reader``: and by that reader (the one
        that took it, ``next_page_read``): a read another took since is left to that one (B-1770). False when
        nothing ended (the invoice no longer in line, deleted meanwhile)."""
        if status not in (PAGE_DONE, PAGE_FAILED, PAGE_SKIPPED, PAGE_WAITING):
            raise ValueError(f"unknown page read status {status!r}")
        if tries is None and status == PAGE_DONE:
            tries = 0
        with self._conn() as conn:  # type: ignore[attr-defined]
            cur = conn.execute(
                "UPDATE page_reads SET status = ?, model = ?, pages = ?, seconds = ?, error = ?, updated_at = ?, "
                "reader = '', tries = COALESCE(?, tries) WHERE invoice_id = ?"
                + (" AND status = 'reading'" if only_if_reading or reader is not None else "")
                + (" AND reader = ?" if reader is not None else ""),
                (status, model or "", int(pages or 0), round(float(seconds or 0), 2), (error or "")[:2000], _now(),
                 tries, invoice_id, *(() if reader is None else (reader,))),
            )  # fmt: skip
            return cur.rowcount > 0

    def release_page_reads(self, alive: Callable[[str], bool]) -> int:
        """Put back in line every invoice marked as being read by a reader that isn't reading any more
        (``alive(reader)`` is False: its process was closed or killed part way). How many were put back."""
        why = "interrupted (AP Coder was closed while it was read): it is read again"
        with self._conn() as conn:  # type: ignore[attr-defined]
            rows = conn.execute("SELECT invoice_id, reader FROM page_reads WHERE status = 'reading'").fetchall()
            gone = [(r["invoice_id"], r["reader"]) for r in rows if not alive(r["reader"] or "")]
            for invoice_id, reader in gone:
                conn.execute(
                    "UPDATE page_reads SET status = 'waiting', reader = '', error = ?, updated_at = ? "
                    "WHERE invoice_id = ? AND status = 'reading' AND reader = ?",
                    (why, _now(), invoice_id, reader),
                )
        return len(gone)

    def page_read(self, invoice_id: int) -> dict[str, Any] | None:
        """{invoice_id, status, model, pages, seconds, reason, error, requested_by, created_at, updated_at}, or
        None if the invoice was never put in line. A read whose reader stopped renewing it shows as waiting."""
        with self._conn() as conn:  # type: ignore[attr-defined]
            row = conn.execute(f"SELECT *, {_STALE.format(t='')} AS stale FROM page_reads WHERE invoice_id = ?",
                               (*_stale(), invoice_id)).fetchone()  # fmt: skip
        if row is None:
            return None
        read = dict(row)
        if read.pop("stale") and read["status"] == PAGE_READING:
            read["status"] = PAGE_WAITING
        return read

    def page_reads_waiting(self, still_open: bool = False) -> int:
        """How many invoices wait for the page reader (interrupted reads included). ``still_open``: only those not
        approved, rejected or exported yet, the ones a person waits for (the reader still reads the others, to score
        itself against what AP approved)."""
        sql = f"SELECT COUNT(*) FROM page_reads WHERE {_IN_LINE}"
        args: list[Any] = [*_stale()]
        if still_open:  # each one's invoice looked up, not every invoice in review listed first (B-1736)
            sql += (" AND EXISTS (SELECT 1 FROM invoices WHERE invoices.id = page_reads.invoice_id AND invoices.status "
                    "IN (?, ?, ?))")  # fmt: skip
            args += [REVIEW, PARKED, PENDING]
        with self._conn() as conn:  # type: ignore[attr-defined]
            row = conn.execute(sql, args).fetchone()
        return int(row[0])

    def page_read_pages_average(self, recent: int = 50) -> float | None:
        """The pages the page reader read per invoice, on average over its latest ``recent`` finished reads (None
        before the first): how many pages an invoice in its queue is likely to have."""
        with self._conn() as conn:  # type: ignore[attr-defined]
            row = conn.execute(
                "SELECT AVG(pages) FROM (SELECT pages FROM page_reads WHERE status = 'done' AND pages > 0 "
                "ORDER BY updated_at DESC LIMIT ?)",
                (int(recent),),
            ).fetchone()
        return round(float(row[0]), 2) if row and row[0] is not None else None

    def page_reads_ahead(self, invoice_id: int) -> int:
        """How many invoices the page reader reads before this one: those being read now, and those in line ahead of
        it in the order ``next_page_read`` takes them. 0 when it isn't waiting."""
        stale, keys = _stale(), ", ".join(_ORDER)
        with self._conn() as conn:  # type: ignore[attr-defined]
            me = conn.execute(f"SELECT {keys} FROM {_ROWS} WHERE p.invoice_id = ? AND {_LINE}",
                              (invoice_id, *stale)).fetchone()  # fmt: skip
            if me is None:
                return 0
            row = conn.execute(
                f"SELECT COUNT(*) FROM {_ROWS} WHERE p.invoice_id != ? AND ((p.status = 'reading' AND NOT "
                f"{_STALE.format(t='p.')}) OR ({_LINE} AND ({keys}) < ({', '.join('?' * len(_ORDER))})))",
                (invoice_id, *stale, *stale, *me),
            ).fetchone()
        return int(row[0])
