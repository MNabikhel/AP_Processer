"""The emails triage set aside: what a person still has to look at, and the actions on them.

``mailbox.unpack`` sorts each email and records it in ``emails/triage.jsonl`` (``mailbox.triage_log``). This
module turns that log into what the dashboard shows: the emails that need a person (bank-detail changes first),
the statements received by email, and the three things a person can do: open a file, send an attachment to the
review queue after all, or mark the email done (an audit event, ``email_triage_done``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import triage
from .mailbox import safe_name, triage_log

DONE_ACTION = "email_triage_done"
SORTED_ACTION = "email_sorted"
FRAUD_ACTION = "email_bank_change"


def done_ids(store: Any) -> set[str]:
    return {str((e.get("detail") or {}).get("id")) for e in store.events(actions=[DONE_ACTION], limit=20_000)}


def _file(folder: Path, rel: str) -> Path | None:
    """The file ``rel`` (as the triage log has it) inside ``folder``, or None when it is not there or not inside."""
    if not rel:
        return None
    root = folder.resolve()
    try:
        path = (folder / rel).resolve()
        path.relative_to(root)
    except (OSError, ValueError):
        return None
    return path if path.is_file() else None


def needs_person(store: Any, folder: Path, limit: int = 100) -> list[dict[str, Any]]:
    """The emails a person still has to look at, possible fraud first, newest first. The whole log is searched, and
    the ``limit`` applies after possible fraud is put first: an email nobody marked done stays here however many
    emails were sorted after it."""
    finished = done_ids(store)
    rows = []
    for entry in triage_log(folder, 1_000_000, keep=lambda e: bool(e.get("needs_person")) and e["id"] not in finished):
        docs = []
        for n, d in enumerate(entry.get("documents") or []):
            if d.get("kind") in (*triage.QUEUE_KINDS, triage.STATEMENT):
                continue
            docs.append({**d, "n": n, "label": triage.LABELS.get(d.get("kind", ""), d.get("kind", "")),
                         "there": _file(folder, d.get("file", "")) is not None})  # fmt: skip
        rows.append({**entry, "docs": docs, "email_there": _file(folder, entry.get("email", "")) is not None})
    return sorted(rows, key=lambda r: not r.get("possible_fraud"))[:limit]


def statements(store: Any, folder: Path, limit: int = 50) -> list[dict[str, Any]]:
    """The statements of account received by email, newest first (those marked done left out)."""
    finished = done_ids(store)
    out = []
    for entry in triage_log(folder):
        if entry["id"] in finished:
            continue
        for n, d in enumerate(entry.get("documents") or []):
            if d.get("kind") == triage.STATEMENT and _file(folder, d.get("file", "")) is not None:
                out.append({"id": entry["id"], "n": n, "name": d["name"], "at": entry.get("at", ""),
                            "sender": entry.get("sender", ""), "subject": entry.get("subject", ""),
                            "sheet": Path(d["name"]).suffix.lower() in triage.SHEET_EXTENSIONS})  # fmt: skip
        if len(out) >= limit:
            break
    return out


def entry(folder: Path, entry_id: str) -> dict[str, Any] | None:
    """Email ``entry_id`` of the triage log, however old (an email still shown as open can always be acted on)."""
    return next(iter(triage_log(folder, limit=1, keep=lambda e: e.get("id") == entry_id)), None)


def file_of(folder: Path, entry_id: str, n: int) -> Path | None:
    """Attachment ``n`` of email ``entry_id`` (-1: the email itself), if it is still there."""
    e = entry(folder, entry_id)
    if e is None:
        return None
    if n == -1:
        return _file(folder, e.get("email", ""))
    docs = e.get("documents") or []
    return _file(folder, docs[n].get("file", "")) if 0 <= n < len(docs) else None


def to_queue(store: Any, folder: Path, entry_id: str, n: int, actor: str | None = None) -> Path | None:
    """Put attachment ``n`` of an email set aside into the invoices folder, so the next pick-up reads it into the
    review queue (a person decided it is an invoice after all). Its new path, or None if it is not there."""
    source = file_of(folder, entry_id, n)
    if source is None or source.parent == folder.resolve():
        return None
    target = folder / safe_name(source.name)
    stem, k = target.stem, 1
    while target.exists():
        target = folder / f"{stem}_{k}{target.suffix}"
        k += 1
    source.replace(target)
    store.log_event("email_attachment_to_queue", actor=actor, detail={"id": entry_id, "file": target.name})
    return target


def mark_done(store: Any, folder: Path, entry_id: str, actor: str | None = None) -> bool:
    e = entry(folder, entry_id)
    if e is None:
        return False
    store.log_event(DONE_ACTION, actor=actor, detail={"id": entry_id, "subject": e.get("subject", ""),
                                                      "kind": e.get("kind", "")})  # fmt: skip
    return True


def log_sorted(store: Any, mail: Any) -> None:
    """Record in the activity log that an email was sorted (and, for a bank-detail change, that it may be fraud)."""
    t = getattr(mail, "triage", None)
    if t is None or getattr(mail, "left_in_place", False):  # not moved: sorted again at the next check, logged then
        return
    detail = {"email": mail.email, "subject": mail.subject[:200], "kind": t.kind, "label": t.label,
              "to_queue": len(mail.saved), "statements": len(mail.statements), "set_aside": len(mail.held),
              "reasons": t.reasons[:5]}  # fmt: skip
    store.log_event(FRAUD_ACTION if t.possible_fraud else SORTED_ACTION, detail=detail)
