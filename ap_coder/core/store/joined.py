"""Several files that are one voucher (D-216): the files a person joined into one document on the Add invoices page
(``intake/joined.py``), in the order they were joined, by the SHA-256 of the document made of them (``joined``).

An invoice made from that document is found by it: its ``file_sha256``, or its ``original_sha256`` when its pages
were set upright afterwards (the copy as it was joined is then the one kept). Nothing here is written on an invoice's
own row: an invoice read again or failed leaves the files as they are, and a file counts as in AP Coder
(``PART_KNOWN``, ``PART_SEEN``, ``PART_STORED``: the import queue's and the folder's pick-up) from the moment its
document is in line (``_in_line``: waiting or being read, B-2283) and while an invoice made from it is there.
"""

from __future__ import annotations

from typing import Any

from .dialect import json_get

PAGES, CLAIM = "pages", "claim"  # the pages of one invoice; the receipts of one expense claim


def _holders(what: str, sha: str, more: str = "") -> str:
    """The invoices (``i``) made from a document one of whose files (``v``) has the content ``sha``: ``what`` of
    each, by the document's SHA-256 as the invoice's file or as the copy kept of it."""
    return " UNION ALL ".join(f"SELECT {what} FROM joined_files v JOIN invoices i ON i.{column} = v.joined WHERE "
                              f"v.sha256 = {sha}{more}" for column in ("file_sha256", "original_sha256"))  # fmt: skip


def _in_line(what: str, sha: str) -> str:
    """The same of a voucher no invoice was made from yet: its document (``q``) waits in line or is being read."""
    return (f"SELECT {what} FROM joined_files v JOIN import_files q ON q.sha256 = v.joined WHERE q.status IN "
            f"('waiting', 'reading') AND v.sha256 = {sha}")  # fmt: skip


_LIVE = " AND (? OR i.status != 'failed')"
# A file with the content ``value`` is one of a voucher's files: asked with (failed reads too) twice.
PART_KNOWN = f"EXISTS ({_holders('1', 'value', _LIVE)} UNION ALL {_in_line('1', 'value')})"
# The same of the folder's file ``f``, a failed read counting: more arms of ``folder._KNOWN``.
PART_SEEN = f"{_holders('1', 'f.sha256')} UNION ALL {_in_line('1', 'f.sha256')}"
# The latest invoice whose voucher has a file with this content: asked with (the content, failed reads too) twice.
PART_STORED = f"{_holders('i.id', '?', _LIVE)} ORDER BY 1 DESC LIMIT 1"
# Where a file with this content is: asked with the content three times (invoice 0: its voucher is still in line).
_WHERE = (_holders("i.id AS invoice_id, i.file_name, v.kind, v.name, v.first_page, v.pages", "?",
                   " AND i.status != 'failed'")
          + f" UNION ALL {_in_line('0, q.name, v.kind, v.name, v.first_page, v.pages', '?')} ORDER BY 1 DESC LIMIT 1"
          )  # fmt: skip
# The event of the joining of the document an invoice was made from (``joined`` in its detail), the latest: asked
# with the invoice twice.
_JOINING = (f"SELECT MAX(id) FROM events WHERE action = 'joined' AND {json_get('detail', '$.joined')} IN (SELECT "
            "file_sha256 FROM invoices WHERE id = ? UNION ALL SELECT original_sha256 FROM invoices WHERE id = ?)"
            )  # fmt: skip
_OF_INVOICE = ("SELECT * FROM joined_files WHERE joined IN (SELECT file_sha256 FROM invoices WHERE id = ? UNION ALL "
               "SELECT original_sha256 FROM invoices WHERE id = ?) ORDER BY position")  # fmt: skip


class JoinedMixin:
    def add_joined_files(self, joined: str, kind: str, files: list[dict[str, Any]]) -> None:
        """Keep the files joined into the document ``joined`` (its SHA-256), in order: each {"name": as it came,
        "sha256", "kept": where it is kept, from the document's folder, "first_page": its first page in the document
        (from 1), "pages"}. ``kind``: ``PAGES`` or ``CLAIM``. The same files joined again replace what was kept."""
        with self._conn() as conn:  # type: ignore[attr-defined]
            conn.execute("DELETE FROM joined_files WHERE joined = ?", (joined,))
            conn.executemany(
                "INSERT INTO joined_files (joined, position, kind, name, sha256, kept, first_page, pages) VALUES (?, "
                "?, ?, ?, ?, ?, ?, ?)",
                [(joined, n, kind, f["name"], f["sha256"], f["kept"], int(f["first_page"]), int(f["pages"]))
                 for n, f in enumerate(files, start=1)],
            )  # fmt: skip

    def joined_files(self, joined: str | None = None, *, invoice_id: int | None = None) -> list[dict[str, Any]]:
        """The files joined into the document ``joined``, or into the one invoice ``invoice_id`` was made from, in
        order ({"joined", "position", "kind", "name", "sha256", "kept", "first_page", "pages", "note", "tax"}); [] for
        any other document or invoice."""
        with self._conn() as conn:  # type: ignore[attr-defined]
            if invoice_id is not None:
                rows = conn.execute(_OF_INVOICE, (invoice_id, invoice_id)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM joined_files WHERE joined = ? ORDER BY position",
                                    (joined,)).fetchall()  # fmt: skip
        return [dict(r) for r in rows]

    def joined_where(self, sha256: str) -> dict[str, Any] | None:
        """Where a file with this content is already one of a voucher's files: {"invoice_id", "file_name" (the
        voucher's document), "kind", "name" (the file there), "first_page", "pages"} of the latest invoice not failed,
        or of the voucher still in line to be read ("invoice_id" 0: a file is part of its voucher from the moment it
        is joined, B-2283), or None."""
        with self._conn() as conn:  # type: ignore[attr-defined]
            row = conn.execute(_WHERE, (sha256, sha256, sha256)).fetchone()
        return dict(row) if row else None

    def note_joined_files(self, joined: str, notes: list[str], taxes: list[str]) -> None:
        """What reading said of each file of the document ``joined``, in order: why its amounts are not in the
        voucher's total ("" when they are), shown as a finding while it is so, and the tax area its receipt is of
        ("GST", "Ontario HST"; "": it charges neither. ``coding/claim.py``)."""
        with self._conn() as conn:  # type: ignore[attr-defined]
            conn.executemany("UPDATE joined_files SET note = ?, tax = ? WHERE joined = ? AND position = ?",
                             [(note, tax, joined, n) for n, (note, tax) in
                              enumerate(zip(notes, taxes, strict=True), start=1)])  # fmt: skip

    def joined_read(self, invoice_id: int) -> None:
        """The invoice made from a joined document is ``invoice_id``: the event of its joining (who joined which
        files, logged before any invoice was there) is this invoice's from now on, in its history (B-2284)."""
        with self._conn() as conn:  # type: ignore[attr-defined]
            if conn.execute(_OF_INVOICE + " LIMIT 1", (invoice_id, invoice_id)).fetchone() is not None:
                conn.execute(f"UPDATE events SET invoice_id = ? WHERE id = ({_JOINING})", (invoice_id,) * 3)

    def forget_joined_files(self, joined: str) -> None:
        """The files of the document ``joined`` are separate files again ("Make these separate invoices again")."""
        with self._conn() as conn:  # type: ignore[attr-defined]
            conn.execute("DELETE FROM joined_files WHERE joined = ?", (joined,))
