"""Invoices that arrive by email: take the attachments out of saved emails and sort them (``triage``).

An email saved as .eml (Outlook on the web and new Outlook: *Download*; most mail programs: *Save as*) or as .msg
(classic Outlook: drag the email to a folder, or *Save As*) dropped in the invoices folder, or uploaded, is sorted
by ``triage.classify_email``; each attachment goes where it belongs, named "<email name> - <attachment name>":

* invoices and credit notes: next to the email, so they are read into the review queue;
* statements of account: ``statements/`` (the Statements page lists them);
* anything else (a quote, a reminder with nothing attached, a bank-detail change letter ...):
  ``emails/needs a person/``, with the email itself.

The email moves to ``emails/`` (or ``emails/needs a person/``) so it is unpacked once, and each decision is
added to ``emails/triage.jsonl`` (``triage_log``): what the email was, and why.

Signature logos and other small inline pictures are left out: an image referenced from the email's own
text (a Content-ID) or smaller than ``MIN_IMAGE_BYTES`` is not an invoice.

An email that cannot be read (damaged, far too large, or emails forwarded inside each other hundreds of
times) moves to ``emails/could not read`` with the reason, so it never stops the other files.
"""

from __future__ import annotations

import email
import email.policy
import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from email.message import EmailMessage
from pathlib import Path

from . import triage
from .extraction import DOCUMENT_EXTENSIONS
from .msgfile import MsgError, html_to_text, read_msg

EMAIL_EXTENSIONS = {".eml", ".msg"}
MIN_IMAGE_BYTES = 15_000
MAX_LOGO_BYTES = 150_000  # an inline picture larger than this is a photo of an invoice, not a signature logo
SETTLE_SECONDS = 5  # an email changed more recently may still be being copied
MAX_EMAIL_BYTES = 100_000_000  # larger than any mail server sends: not read
MAX_FORWARD_DEPTH = 20  # emails forwarded inside forwarded emails: deeper ones are not opened
MAX_PARTS = 500  # attachments and text parts read from one email
DONE_FOLDER = "emails"
UNREADABLE_FOLDER = "could not read"  # inside DONE_FOLDER
PERSON_FOLDER = "needs a person"  # inside DONE_FOLDER: emails and attachments a person must look at
STATEMENTS_FOLDER = "statements"  # in the invoices folder: statements of account taken out of emails
TRIAGE_LOG = "triage.jsonl"  # inside DONE_FOLDER: one line per email sorted
_MAGIC = {b"%PDF": ".pdf", b"\xff\xd8\xff": ".jpg", b"\x89PNG": ".png", b"II*\x00": ".tif", b"MM\x00*": ".tif",
          b"BM": ".bmp"}  # fmt: skip
_TYPES = {"application/pdf": ".pdf", "image/jpeg": ".jpg", "image/png": ".png", "image/tiff": ".tif",
          "image/bmp": ".bmp", "image/heic": ".heic", "image/heif": ".heif"}  # fmt: skip


@dataclass
class Unpacked:
    email: str
    subject: str
    saved: list[Path] = field(default_factory=list)  # invoices and credit notes: to the review queue
    skipped: list[str] = field(default_factory=list)  # attachments that are not invoices (and why)
    error: str = ""  # why the email could not be read at all (it was moved to emails/could not read)
    statements: list[Path] = field(default_factory=list)  # statements of account (statements/)
    held: list[Path] = field(default_factory=list)  # attachments a person must look at (emails/needs a person/)
    triage: triage.EmailTriage | None = None


@dataclass
class Mail:
    """What AP Coder takes from one email: its words and the attachments it may keep."""

    subject: str = ""
    sender: str = ""
    body: str = ""
    found: list[tuple[str, bytes]] = field(default_factory=list)  # PDFs and pictures
    sheets: list[tuple[str, bytes]] = field(default_factory=list)  # CSV and Excel (a statement, maybe)
    skipped: list[str] = field(default_factory=list)


class UnreadableEmail(ValueError):
    """An email AP Coder will not open; the message says why in plain words."""


def safe_name(name: str) -> str:
    """A file name safe to save on Windows: no folders or reserved characters, at most 80 characters
    (and 200 bytes, for names in Chinese, Japanese or emoji), the extension kept."""
    name = re.split(r"[\\/]", name)[-1]  # the file name only, whichever separator the sender's system used
    name = re.sub(r'[:*?"<>|\x00-\x1f]', "_", name).strip(" .")
    if len(name) > 80 or len(name.encode()) > 200:  # short enough for Windows paths; the extension is kept
        suffix = Path(name).suffix[:10]
        stem = name[: 80 - len(suffix)]
        while len((stem + suffix).encode()) > 200:
            stem = stem[:-1]
        name = stem.rstrip(" .") + suffix
    return name or "attachment"


def _kind(name: str, content_type: str, content: bytes) -> str:
    """The file type of an attachment: its name's extension when it is a known one, else its declared type,
    else what its first bytes say ("Invoice No. 12345" sent as application/pdf is still a PDF)."""
    suffix = Path(name).suffix.lower()
    if suffix in DOCUMENT_EXTENSIONS:
        return ".jpg" if suffix == ".jpeg" else suffix
    if content_type in _TYPES:
        return _TYPES[content_type]
    head = content.lstrip()[:8]
    return next((kind for magic, kind in _MAGIC.items() if head.startswith(magic)), "")


def _keep(mail: Mail, name: str, content_type: str, content: bytes, inline: bool) -> None:
    """Add one attachment to ``mail``: a PDF or a picture to ``found``, a spreadsheet to ``sheets``, the rest (and
    signature logos) to ``skipped`` with the reason."""
    label = name or "an unnamed attachment"
    if Path(name).suffix.lower() in triage.SHEET_EXTENSIONS:
        mail.sheets.append((safe_name(name), content))
        return
    suffix = _kind(name, content_type, content)
    if not suffix:
        mail.skipped.append(f"{label} (not a PDF or image)")
        return
    if suffix == ".pdf" and not content.lstrip()[:1024].startswith(b"%PDF") and b"%PDF" not in content[:1024]:
        mail.skipped.append(f"{label} (named as a PDF but is not one)")
        return
    if suffix != ".pdf" and (len(content) < MIN_IMAGE_BYTES or (inline and len(content) < MAX_LOGO_BYTES)):
        mail.skipped.append(f"{label} (a small or inline picture, e.g. a logo)")
        return
    safe = safe_name(name or f"attachment{suffix}")
    if Path(safe).suffix.lower() != suffix:  # keep a type the invoices folder picks up
        safe = f"{safe if Path(safe).suffix.lower() not in DOCUMENT_EXTENSIONS else Path(safe).stem}{suffix}"
    mail.found.append((safe, content))


def attachments(raw: bytes) -> tuple[str, list[tuple[str, bytes]], list[str]]:
    """(subject, [(file name, content)] of the invoice attachments, [skipped attachments]) of an email,
    including the attachments of emails forwarded as attachments."""
    mail = read_eml(raw)
    return mail.subject, mail.found, mail.skipped


def read_msg_mail(raw: bytes) -> Mail:
    """The subject, sender, text and attachments of an Outlook .msg email (emails attached to it opened too)."""
    if len(raw) > MAX_EMAIL_BYTES:
        raise UnreadableEmail(f"the email is larger than {MAX_EMAIL_BYTES // 1_000_000} MB")
    try:
        msg = read_msg(raw)
    except MsgError as exc:
        raise UnreadableEmail(str(exc)) from None
    mail = Mail(msg.subject, msg.sender, msg.body, skipped=list(msg.skipped))
    for a in msg.attachments:
        _keep(mail, a.name, a.content_type, a.data, inline=bool(a.content_id))
    return mail


def read_eml(raw: bytes) -> Mail:
    """The subject, sender, text and attachments of an .eml email (emails forwarded as attachments opened too)."""
    if len(raw) > MAX_EMAIL_BYTES:
        raise UnreadableEmail(f"the email is larger than {MAX_EMAIL_BYTES // 1_000_000} MB")
    try:
        msg = email.message_from_bytes(raw, policy=email.policy.default)
    except RecursionError:
        raise UnreadableEmail("it holds too many emails forwarded inside each other") from None
    assert isinstance(msg, EmailMessage)
    mail = Mail(str(msg.get("Subject") or ""), str(msg.get("From") or ""))
    skipped = mail.skipped
    texts: list[str] = []

    def parts(message: EmailMessage) -> list[EmailMessage]:
        """Every part of the email, also inside emails forwarded as attachments (message/rfc822), without
        going deeper than ``MAX_FORWARD_DEPTH`` or past ``MAX_PARTS`` (a loop, not recursion)."""
        out: list[EmailMessage] = []
        stack: list[tuple[EmailMessage, int]] = [(message, 0)]
        while stack and len(out) < MAX_PARTS:
            part, depth = stack.pop()
            out.append(part)
            if not part.is_multipart():
                continue
            if part.get_content_type() == "message/rfc822":
                if depth >= MAX_FORWARD_DEPTH:
                    skipped.append(f"emails forwarded more than {MAX_FORWARD_DEPTH} levels deep (not opened)")
                    continue
                depth += 1
            children = part.get_payload()
            stack.extend((child, depth) for child in reversed(children if isinstance(children, list) else []))
        if stack:
            skipped.append(f"everything after the first {MAX_PARTS} parts of the email (not opened)")
        return out

    def walk(message: EmailMessage) -> None:
        for part in parts(message):
            if part.is_multipart() or part.get_content_maintype() in ("message", "multipart"):
                continue
            name = part.get_filename() or ""
            if part.get_content_maintype() == "text" and not name:  # the email's own words (for triage)
                if sum(len(t) for t in texts) < 200_000:
                    try:
                        text = part.get_content()
                    except Exception:  # noqa: BLE001 - an unknown charset: the words are not needed
                        continue
                    texts.append(html_to_text(text) if part.get_content_subtype() == "html" else str(text))
                continue
            try:
                content = part.get_payload(decode=True) or b""
            except Exception:  # a damaged attachment
                skipped.append(f"{name or 'an unnamed attachment'} (could not be read)")
                continue
            inline = bool(part.get("Content-ID")) and part.get_content_disposition() != "attachment"
            _keep(mail, name, part.get_content_type(), content, inline)

    walk(msg)
    mail.body = "\n\n".join(t for t in texts if t.strip())[:200_000]
    return mail


def _move_to(path: Path, done: Path) -> Path:
    """Move the email into ``done`` under a name not taken yet (its new path); left in place if it cannot be
    moved."""
    done.mkdir(parents=True, exist_ok=True)
    target = done / path.name
    n = 1
    while target.exists():
        target = done / f"{path.stem}_{n}{path.suffix}"
        n += 1
    try:
        path.replace(target)
    except OSError:
        return path  # left in place (e.g. open in another program): unpacking again writes nothing new
    return target


def _unreadable(path: Path, folder: Path, exc: Exception) -> Unpacked:
    """File away an email that cannot be read, so it is not tried again on every check, and say why."""
    why = str(exc) if isinstance(exc, UnreadableEmail) else f"it is damaged ({type(exc).__name__})"
    _move_to(path, folder / DONE_FOLDER / UNREADABLE_FOLDER)
    return Unpacked(path.name, "", error=f"could not be read: {why}")


def read_mail(path: Path, raw: bytes) -> Mail:
    return read_msg_mail(raw) if path.suffix.lower() == ".msg" else read_eml(raw)


def _save(folder: Path, stem: str, name: str, content: bytes) -> Path:
    """Write an attachment as "<email name> - <name>" in ``folder``; the same file already there is not written
    twice, a different one under that name gets a number."""
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{stem} - {name}"
    n = 1
    while target.exists() and target.read_bytes() != content:
        target = folder / f"{stem} - {Path(name).stem}_{n}{Path(name).suffix}"
        n += 1
    if not target.exists():
        target.write_bytes(content)
    return target


def _record(folder: Path, mail: Mail, email_file: Path, result: Unpacked, files: list[Path]) -> None:
    """Add this email's triage to ``emails/triage.jsonl`` (paths relative to the invoices folder)."""
    t = result.triage
    assert t is not None
    rel = lambda p: p.relative_to(folder).as_posix() if p.is_relative_to(folder) else p.name  # noqa: E731
    entry = {
        "id": hashlib.sha1(f"{email_file.name}|{time.time_ns()}".encode()).hexdigest()[:12],
        "at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "email": rel(email_file),
        "subject": mail.subject[:300],
        "sender": mail.sender[:200],
        "kind": t.kind,
        "label": t.label,
        "possible_fraud": t.possible_fraud,
        "needs_person": t.needs_person,
        "reasons": t.reasons[:20],
        "documents": [
            {"name": doc.name, "kind": dec.kind, "reason": dec.reason, "file": rel(f)}
            for (doc, dec), f in zip(t.documents, files, strict=True)
        ],  # fmt: skip
        "skipped": result.skipped[:50],
    }
    log = folder / DONE_FOLDER / TRIAGE_LOG
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def unpack(path: Path, folder: Path | None = None) -> Unpacked:
    """Sort the email at ``path`` (``triage``) and save each attachment where it belongs in ``folder`` (default:
    its own folder): invoices and credit notes next to it, statements in ``statements/``, the rest with the email in
    ``emails/needs a person/``. The email then moves to ``emails/`` (or ``emails/needs a person/``) and the decision
    is added to ``emails/triage.jsonl``. A file with the same name and content is not written twice. An email that
    cannot be read moves to ``emails/could not read`` and ``error`` says why; an OSError (the file locked or removed
    meanwhile) is raised, so the next check tries again."""
    folder = folder or path.parent
    raw = path.read_bytes()
    try:
        mail = read_mail(path, raw)
        docs = [triage.document_for(name, content) for name, content in [*mail.found, *mail.sheets]]
        sorted_mail = triage.classify_email(mail.subject, mail.body, docs)
    except Exception as exc:  # noqa: BLE001 - one bad email must never stop the others
        return _unreadable(path, folder, exc)
    result = Unpacked(path.name, mail.subject, skipped=mail.skipped, triage=sorted_mail)
    stem = safe_name(path.stem)[:40].rstrip(" .")
    person = folder / DONE_FOLDER / PERSON_FOLDER
    placed: list[tuple[str, Path]] = []
    for (name, content), (_doc, dec) in zip([*mail.found, *mail.sheets], sorted_mail.documents, strict=True):
        if dec.kind in triage.QUEUE_KINDS:
            placed.append(("saved", _save(folder, stem, name, content)))
        elif dec.kind == triage.STATEMENT:
            placed.append(("statements", _save(folder / STATEMENTS_FOLDER, stem, name, content)))
        else:
            placed.append(("held", _save(person, stem, name, content)))
    for where, p in placed:
        getattr(result, where).append(p)
    done = person if sorted_mail.needs_person else folder / DONE_FOLDER
    email_file = _move_to(path, done)
    try:
        _record(folder, mail, email_file, result, [p for _, p in placed])
    except OSError:
        pass  # the log is a convenience: the files are where they belong either way
    return result


def triage_log(folder: Path, limit: int = 500) -> list[dict]:
    """The emails sorted in ``folder`` (newest first), from ``emails/triage.jsonl``; a damaged line is skipped."""
    log = folder / DONE_FOLDER / TRIAGE_LOG
    try:
        with log.open("rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - 4_000_000))
            lines = fh.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return []
    out = []
    for line in reversed(lines):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict) and entry.get("id"):
            out.append(entry)
        if len(out) >= limit:
            break
    return out


def emails_in(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in EMAIL_EXTENSIONS)


def unpack_folder(folder: Path, now: float | None = None) -> list[Unpacked]:
    """Unpack every saved email in ``folder`` that has finished copying (unchanged for ``SETTLE_SECONDS``).
    One email that cannot be read is filed away with the reason; it never stops the others."""
    now = time.time() if now is None else now
    out = []
    for path in emails_in(folder):
        try:
            if now - path.stat().st_mtime < SETTLE_SECONDS:
                continue  # still being written: the next check picks it up
            out.append(unpack(path, folder))
        except OSError:
            continue  # locked or removed meanwhile: the next check picks it up
        except Exception as exc:  # noqa: BLE001 - anything else about this one email: file it away, go on
            out.append(_unreadable(path, folder, exc))
    return out
