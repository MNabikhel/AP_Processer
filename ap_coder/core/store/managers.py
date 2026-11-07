"""A manager confirms an invoice's cost allocation before AP approves it (D-138).

AP decides, invoice by invoice, to send one to the manager of its cost centers. The invoice is then **parked** (out
of the review queue, still counted as it is today for POs, duplicates and the month end), waiting for that manager;
``manager_requests`` keeps the request: who it went to, the lines as AP coded them when it was sent (GL account, cost
center, amount), the note, and later the answer. The manager answers on screen (server mode: a Manager account,
``web/pages/manager.py``) or by email, which AP then records, the reply email kept in the data folder. Either answer
(confirmed, or sent back with what to change) puts the invoice back in the review queue for AP, who approves it as
before: the approval limits and the second approval are unchanged.

A request is ``waiting`` until answered (``confirmed`` / ``sent_back``) or ``withdrawn`` (AP brought the invoice back
before an answer, or deleted it). One invoice has at most one waiting request.
"""

from __future__ import annotations

import json
from typing import Any

from ._base import PARKED, REVIEW, _now
from .dialect import begin_write

WAITING, CONFIRMED, SENT_BACK, WITHDRAWN = "waiting", "confirmed", "sent_back", "withdrawn"
ANSWERS = (CONFIRMED, SENT_BACK)
SCREEN, EMAIL = "screen", "email"  # how the manager answered: on AP Coder's screen, or by email AP recorded
MAX_NOTE = 2000
MAX_NAME = 120


def parked_reason(manager: str) -> str:
    """What a parked invoice says it is waiting for while a manager has it."""
    return f"{manager or 'the manager'} to confirm the cost allocation"


def _code(value: Any) -> str:
    """A GL account or cost center as kept: "" for none (the coding's UNASSIGNED placeholder too)."""
    code = str(value or "").strip()
    return "" if code == "UNASSIGNED" else code


def coding_lines(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The lines as a manager confirms them: number, description, GL account, cost center and amount (the rest of a
    line is not theirs to confirm)."""
    out = []
    for n, li in enumerate(lines, 1):
        out.append({
            "line": int(li.get("line_number") or n),
            "description": str(li.get("description") or "")[:200],
            "gl_code": _code(li.get("gl_code") or li.get("predicted_gl_code")),  # (the coding's names: predicted_)
            "cost_center": _code(li.get("cost_center") or li.get("predicted_cost_center")),
            "amount": round(float(li.get("amount") or 0), 2),
        })  # fmt: skip
    return out


def same_coding(sent: list[dict[str, Any]], now: list[dict[str, Any]]) -> bool:
    """Whether the lines coded now are the ones the manager was sent (GL account, cost center and amount, line by
    line; a description retyped does not count)."""

    def key(lines: list[dict[str, Any]]) -> list[tuple[str, str, float]]:
        return sorted((li["gl_code"], li["cost_center"], round(li["amount"], 2)) for li in lines)

    return key(sent) == key(coding_lines(now) if now and "line" not in now[0] else now)


def _row(r: Any) -> dict[str, Any]:
    d = dict(r)
    for key in ("cost_centers", "lines"):
        try:
            d[key] = json.loads(d.get(key) or "[]")
        except ValueError:
            d[key] = []
    return d


class ManagersMixin:
    def send_to_manager(
        self,
        invoice_id: int,
        actor: str,
        *,
        manager_name: str,
        manager_email: str = "",
        manager_user: int | None = None,
        lines: list[dict[str, Any]],
        note: str = "",
    ) -> int:
        """Park the invoice (it must be in the review queue) waiting for the manager, and keep the request: the lines
        as coded now. Returns the request's id."""
        name = " ".join(manager_name.split())[:MAX_NAME]
        if not name:
            raise ValueError("Say who the manager is.")
        sent = coding_lines(lines)
        centers = sorted({li["cost_center"] for li in sent if li["cost_center"]})
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE invoices SET status = ?, parked_reason = ?, follow_up = NULL WHERE id = ? AND status = ?",
                (PARKED, parked_reason(name), invoice_id, REVIEW),
            )
            if cur.rowcount == 0:
                raise ValueError(f"invoice {invoice_id} is not in the review queue")
            cur = conn.execute(
                "INSERT INTO manager_requests (invoice_id, status, sent_at, sent_by, manager_name, manager_email, "
                "manager_user, cost_centers, lines, note) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (invoice_id, WAITING, _now(), actor, name, manager_email.strip()[:200], manager_user,
                 json.dumps(centers), json.dumps(sent), note.strip()[:MAX_NOTE]),
            )  # fmt: skip
            request_id = int(cur.lastrowid)
            self._log(conn, "sent_to_manager", invoice_id, actor,
                      {"request": request_id, "manager": name, "cost_centers": centers})  # fmt: skip
        return request_id

    def manager_request(self, request_id: int) -> dict[str, Any] | None:
        with self._conn() as conn:
            r = conn.execute("SELECT * FROM manager_requests WHERE id = ?", (request_id,)).fetchone()
        return _row(r) if r else None

    def manager_requests(self, invoice_id: int) -> list[dict[str, Any]]:
        """An invoice's requests, the newest first."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM manager_requests WHERE invoice_id = ? ORDER BY id DESC", (invoice_id,)
            ).fetchall()
        return [_row(r) for r in rows]

    def waiting_manager_request(self, invoice_id: int) -> dict[str, Any] | None:
        found = [r for r in self.manager_requests(invoice_id) if r["status"] == WAITING]
        return found[0] if found else None

    def last_manager_answer(self, invoice_id: int) -> dict[str, Any] | None:
        """The latest answer a manager gave on the invoice (confirmed or sent back), if any."""
        found = [r for r in self.manager_requests(invoice_id) if r["status"] in ANSWERS]
        return found[0] if found else None

    def waiting_manager_requests(self, manager_user: int | None = None) -> list[dict[str, Any]]:
        """The requests waiting for an answer (``manager_user``: only that Manager account's), the oldest first, with
        the invoice's vendor, number, date, total and currency."""
        sql = ("SELECT m.*, i.vendor_name, i.invoice_number, i.invoice_date, i.grand_total, i.currency "
               "FROM manager_requests m JOIN invoices i ON i.id = m.invoice_id WHERE m.status = ?")  # fmt: skip
        params: list[Any] = [WAITING]
        if manager_user is not None:
            sql += " AND m.manager_user = ?"
            params.append(manager_user)
        with self._conn() as conn:
            rows = conn.execute(sql + " ORDER BY m.id", params).fetchall()
        return [_row(r) for r in rows]

    def waiting_manager_map(self) -> dict[int, dict[str, Any]]:
        """invoice id -> its waiting request (name and when), for the lists that say who an invoice waits for."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT invoice_id, manager_name, sent_at FROM manager_requests WHERE status = ?", (WAITING,)
            ).fetchall()
        return {r["invoice_id"]: dict(r) for r in rows}

    def answer_manager_request(
        self,
        request_id: int,
        answer: str,
        *,
        by: str,
        actor: str,
        note: str = "",
        answered_at: str | None = None,
        reply_file: str = "",
        via: str = SCREEN,
    ) -> dict[str, Any]:
        """The manager's answer: ``confirmed`` or ``sent_back`` (``note``: what to change, needed then). The request
        must still be waiting; the invoice goes back to the review queue for AP (if it is still parked). ``by``: who
        answered (the manager); ``actor``: who recorded it (the manager on screen, or AP from the email)."""
        if answer not in ANSWERS:
            raise ValueError(f"unknown answer {answer!r}")
        if answer == SENT_BACK and not note.strip():
            raise ValueError("Say what the manager wants changed.")
        meanwhile = "This request was answered or withdrawn meanwhile."
        with self._conn() as conn:
            begin_write(conn)  # read and answered in one go: two answers at once keep one (B-1578)
            req = conn.execute("SELECT * FROM manager_requests WHERE id = ?", (request_id,)).fetchone()
            if req is None or req["status"] != WAITING:
                raise ValueError(meanwhile)
            at = answered_at or _now()
            cur = conn.execute(
                "UPDATE manager_requests SET status = ?, answered_at = ?, answered_by = ?, answer_note = ?, "
                "reply_file = ?, via = ? WHERE id = ? AND status = ?",
                (answer, at, " ".join(by.split())[:MAX_NAME] or req["manager_name"], note.strip()[:MAX_NOTE],
                 reply_file, via, request_id, WAITING),
            )  # fmt: skip
            if cur.rowcount == 0:
                raise ValueError(meanwhile)
            conn.execute(
                "UPDATE invoices SET status = ?, parked_reason = NULL, follow_up = NULL WHERE id = ? AND status = ?",
                (REVIEW, req["invoice_id"], PARKED),
            )
            action = "manager_confirmed" if answer == CONFIRMED else "manager_sent_back"
            self._log(conn, action, req["invoice_id"], actor,
                      {"request": request_id, "manager": by or req["manager_name"], "via": via,
                       "note": note.strip()[:MAX_NOTE], "answered_at": at})  # fmt: skip
        return self.manager_request(request_id) or {}

    def withdraw_manager_requests(self, invoice_id: int, actor: str, conn: Any = None) -> int:
        """Withdraw the invoice's waiting request (AP brought it back, or deleted it, before the manager answered).
        ``conn``: inside a transaction already open."""
        if conn is None:
            with self._conn() as own:
                return self.withdraw_manager_requests(invoice_id, actor, own)
        cur = conn.execute(
            "UPDATE manager_requests SET status = ?, answered_at = ?, answered_by = ? WHERE invoice_id = ? AND "
            "status = ?",
            (WITHDRAWN, _now(), actor, invoice_id, WAITING),
        )
        if cur.rowcount:
            self._log(conn, "manager_request_withdrawn", invoice_id, actor)
        return cur.rowcount

    def cost_center_managers(self) -> dict[str, dict[str, str]]:
        """cost center -> {"name", "email"} of its manager, for the ones that have one."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT code, manager_name, manager_email FROM cost_centers WHERE manager_name != ''"
            ).fetchall()
        return {r["code"]: {"name": r["manager_name"], "email": r["manager_email"]} for r in rows}

    def set_cost_center_manager(self, codes: list[str], name: str, email: str, actor: str) -> int:
        """Remember ``name`` (and ``email``) as the manager of these cost centers; returns how many it set."""
        name, email = " ".join(name.split())[:MAX_NAME], email.strip()[:200]
        with self._conn() as conn:
            n = 0
            for code in codes:
                n += conn.execute(
                    "UPDATE cost_centers SET manager_name = ?, manager_email = ? WHERE code = ?", (name, email, code)
                ).rowcount
            if n:
                self._log(conn, "cost_center_manager_set", None, actor, {"cost_centers": codes, "manager": name})
        return n
