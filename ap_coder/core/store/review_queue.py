"""The review queue's lists and facts, kept in each process and brought up to date from the queue's change log (D-193).

``/review`` lists the queue (``list_invoices`` of review, parked and pending) and reads what it shows of each invoice in
review (``bulk.queue_checks``): about 2,700 invoices at 300,000. Kept whole on the summary's topics (D-159), each
check, approval or park moved them, and every web worker read them all again at its next ``/review`` (a quarter of
their CPU under 100 people). Now the summary logs each invoice whose row in the queue changed (``queue_changes``,
written by ``summary._summarize`` in the same transaction as the change, however it was made: through a Store, by
another process, by plain SQL worked into the summary by the next read): its row in the lists (``LISTED_COLUMNS``, the
confidence a check writes included), its facts in review (``QUEUE_READ``; one the summary could not work out, at each
change), or its coming into the queues or leaving them (``summary.queue_logged``). A process keeps the rows by invoice
with the last change it read (``_KEPT``) and reads again only the invoices logged since.

They are read whole when the database is another (``summary_id``: a restore, a summary built again), when the log no
longer holds every change since (``QUEUE_CHANGES_KEPT``), or when the summary was worked out without the log since (an
older AP Coder on the same database: the summary's generation moved by more than its refreshes that logged,
``summary.LOGGED``); and afresh, neither taken nor kept, inside a write under way or a read transaction: it may yet be
undone, and the next write would take its place in the log (D-159's rule).
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from ._base import APPROVED, REVIEW, _in_list
from .summary import LISTED_COLUMNS, LOGGED, QUEUE_ORDER, QUEUE_READ, QUEUES, computed, fetch_rows

# (database, what) -> ((its summary_id, the summary's refreshes made without the log, the last change read), {id: row})
_KEPT: dict[tuple[str, str], tuple[tuple[Any, ...], dict[int, Any]]] = {}
_KEPT_LOCK = threading.Lock()
KEPT_MOST = 16  # databases and what of each, kept in a process (the oldest let go first)
_FOUND_TEXTS = ("q_found", "r_found")  # of the queue's facts, the findings texts: each read once (``queue_rows``)
# The stamp and the invoices logged after the last change read (``seq > ?``; NULL: none), in one statement.
_STAMP = ("SELECT (SELECT value FROM settings WHERE key = 'summary_id'), "
          "COALESCE((SELECT CAST(value AS INTEGER) FROM settings WHERE key = 'summary_generation/all'), 0) - "
          f"COALESCE((SELECT CAST(value AS INTEGER) FROM settings WHERE key = '{LOGGED}'), 0), "
          "(SELECT MIN(seq) FROM queue_changes), (SELECT MAX(seq) FROM queue_changes), "
          "(SELECT json_group_array(DISTINCT id) FROM queue_changes WHERE seq > ?)")  # fmt: skip


def _keep(key: tuple[str, str], stamp: tuple[Any, ...], rows: dict[int, Any]) -> None:
    """Keep ``rows`` as of ``stamp``, unless another thread kept them as of a later change of the same database."""
    with _KEPT_LOCK:
        kept = _KEPT.pop(key, None)
        _KEPT[key] = kept if kept is not None and kept[0][:2] == stamp[:2] and kept[0][2] > stamp[2] else (stamp, rows)
        while len(_KEPT) > KEPT_MOST:
            _KEPT.pop(next(iter(_KEPT)))


class ReviewQueueMixin:
    # --- The review queue's lists and facts (D-155, D-193) -----------------------------------------------------------

    @contextmanager
    def _kept(self, what: str, read: Callable[[list[int] | None], dict[int, Any]]) -> Iterator[dict[int, Any]]:
        """{id: row} of the queue's invoices ``read`` gives (``read(None)``: all of them; ``read(ids)``: those of
        ``ids`` it still gives), kept for ``what`` and brought up to date from the queue's log (above), read with the
        log in one moment: the caller's reads inside are of that moment too. Shared: read it, don't change it."""
        key = (str(self.path), what)
        with _KEPT_LOCK:
            hit = _KEPT.get(key)
        with self.session(), self._conn() as conn:
            if conn.in_transaction:  # a write under way, or the caller's read: afresh, not kept
                self._summary_fresh(conn)
                yield read(None)
                return
            with self.summary_snapshot():
                summary_id, unlogged, first, last, changed = conn.execute(_STAMP, (hit[0][2] if hit else None,)
                                                                          ).fetchone()  # fmt: skip
                stamp = (summary_id, unlogged, last or 0)
                # every change since still in the log (it never empties: one kept at ``last`` has nothing to read)
                if hit is not None and hit[0][:2] == stamp[:2] and (first or 1) - 1 <= hit[0][2] <= stamp[2]:
                    rows = hit[1]
                    if ids := json.loads(changed):
                        again = set(ids)
                        rows = {i: r for i, r in rows.items() if i not in again}
                        rows.update(read(ids))
                else:
                    rows = read(None)
                _keep(key, stamp, rows)
                yield rows

    def queue_listed(self, statuses: tuple[str, ...]) -> list[dict[str, Any]]:
        """``list_invoices`` of the queue's lists (``statuses``, of ``QUEUES``), the ones to review first: their rows
        kept by invoice and followed by the queue's log (``_kept``), in the queue's order read in the same moment off
        its index (``summary_queue_order``, the ids alone). Each caller gets rows of its own."""
        order = (f"SELECT json_group_array(id) FROM (SELECT id FROM invoice_summary WHERE {_in_list('status')} "
                 f"ORDER BY {QUEUE_ORDER})")  # fmt: skip
        with self._kept("listed", self._listed) as rows, self._conn() as conn:
            ids = json.loads(conn.execute(order, (json.dumps(list(statuses)),)).fetchone()[0])
        return [dict(rows[i]) for i in ids]

    def _listed(self, ids: list[int] | None) -> dict[int, dict[str, Any]]:
        where, params = _in_list("status"), [json.dumps(list(QUEUES))]
        if ids is not None:
            where += f" AND {_in_list('id')}"
            params.append(json.dumps(ids))
        with self._conn() as conn:
            rows = fetch_rows(conn, f"SELECT {', '.join(LISTED_COLUMNS)} FROM invoice_summary WHERE {where}", params)
        return {r["id"]: r for r in rows}

    def recently_approved(self, limit: int) -> list[dict[str, Any]]:
        """The ``limit`` invoices approved last (``list_invoices(APPROVED)`` by approval time, latest first): the
        queue's Approved tab, read off the summary's index in that order (``summary_recent``, D-193)."""
        with self._conn() as conn:
            self._summary_fresh(conn)
            return fetch_rows(conn,
                "SELECT id, file_name, status, vendor_name, invoice_number, invoice_date, due_date, currency, "
                "grand_total, model_confidence, adjusted_confidence, requires_review, error, created_at, reviewed_at, "
                "reviewer, second_reviewer FROM invoice_summary WHERE status = ? ORDER BY COALESCE(reviewed_at, '') "
                "DESC, requires_review DESC, adjusted_confidence ASC, id ASC LIMIT ?", (APPROVED, limit))  # fmt: skip

    def queue_facts(self) -> dict[int, dict[str, Any]]:
        """{id: what the queue reads of it} of every invoice in review (``queue_rows``; one the summary could not work
        out, read again from the invoice: ``computed``), kept and followed by the queue's log as its lists are: the
        queue's checks take those they are asked (``bulk.queue_checks``). Shared: read it, don't change it."""
        with self._kept("facts", lambda ids: {r["id"]: computed(self, r, "queue") for r in self.queue_rows(ids)}
                        ) as rows:  # fmt: skip
            return rows

    def stale_checks(self, stamp: str, ids: list[int] | None = None) -> list[int]:
        """The invoices in review (only ``ids``' when given) whose stored checks and queue re-check were both made
        against other settings than ``stamp`` (``Store.checks_stamp``), in the queue's order: what
        ``coding.bulk.recheck_queue`` checks again. From the invoices' summary."""
        where, params = "status = ? AND q_stamp != ? AND r_stamp != ?", [REVIEW, stamp, stamp]
        if ids is not None:
            where += f" AND {_in_list('id')}"
            params.append(json.dumps([int(i) for i in ids]))
        rows = self.summary_rows(where, params, "id", "requires_review DESC, adjusted_confidence ASC, id ASC")
        return [r["id"] for r in rows]

    def queue_rows(self, ids: list[int] | None) -> list[dict[str, Any]]:
        """The queue's facts (``id`` and ``QUEUE_READ``) of the invoices of ``ids`` in review (None: of all of them), by
        id: the rows ``summary_rows`` gives, but each findings text (``q_found``, ``r_found``) read once for all the
        invoices that have it (the queue's share a few: 190 texts, 2.1 MB in its rows at 100,000 invoices, B-1741), in
        the same read transaction as the rest."""
        where, params = "status = ?", (REVIEW,)
        if ids is not None:
            where, params = f"{where} AND {_in_list('id')}", (REVIEW, json.dumps([int(i) for i in ids]))
        columns = ", ".join(f"NULL AS {c}" if c in _FOUND_TEXTS else c for c in ("id", *QUEUE_READ))
        texts = " UNION ALL ".join(f"SELECT '{c}' AS c, {c} AS text, json_group_array(id) AS ids FROM invoice_summary "
                                   f"WHERE {where} AND {c} IS NOT NULL GROUP BY {c}" for c in _FOUND_TEXTS)  # fmt: skip
        with self.summary_snapshot(), self._conn() as conn:
            rows = fetch_rows(conn, f"SELECT {columns} FROM invoice_summary WHERE {where} ORDER BY id", params)
            found = fetch_rows(conn, texts, params * len(_FOUND_TEXTS))
        by_id = {r["id"]: r for r in rows}
        for t in found:
            for invoice_id in json.loads(t["ids"]):
                by_id[invoice_id][t["c"]] = t["text"]
        return rows
