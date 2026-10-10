"""Invoices that arrive by email: take the PDF and image attachments out of saved emails (.eml).

An email saved as .eml (Outlook on the web and new Outlook: *Download*; most mail programs: *Save as*)
dropped in the invoices folder, or uploaded, has its invoice attachments saved next to it as
"<email name> - <attachment name>"; the email itself moves to ``emails/`` so it is unpacked once.

Signature logos and other small inline pictures are left out: an image referenced from the email's own
text (a Content-ID) or smaller than ``MIN_IMAGE_BYTES`` is not an invoice.

An email that cannot be read (damaged, far too large, or emails forwarded inside each other hundreds of
times) moves to ``emails/could not read`` with the reason, so it never stops the other files.
"""

from __future__ import annotations

import email
import email.policy
import re
import time
from dataclasses import dataclass, field
from email.message import EmailMessage
from pathlib import Path

from .extraction import DOCUMENT_EXTENSIONS

EMAIL_EXTENSIONS = {".eml"}
MIN_IMAGE_BYTES = 15_000
MAX_LOGO_BYTES = 150_000  # an inline picture larger than this is a photo of an invoice, not a signature logo
SETTLE_SECONDS = 5  # an email changed more recently may still be being copied
MAX_EMAIL_BYTES = 100_000_000  # larger than any mail server sends: not read
MAX_FORWARD_DEPTH = 20  # emails forwarded inside forwarded emails: deeper ones are not opened
MAX_PARTS = 500  # attachments and text parts read from one email
DONE_FOLDER = "emails"
UNREADABLE_FOLDER = "could not read"  # inside DONE_FOLDER
_MAGIC = {b"%PDF": ".pdf", b"\xff\xd8\xff": ".jpg", b"\x89PNG": ".png", b"II*\x00": ".tif", b"MM\x00*": ".tif",
          b"BM": ".bmp"}  # fmt: skip
_TYPES = {"application/pdf": ".pdf", "image/jpeg": ".jpg", "image/png": ".png", "image/tiff": ".tif",
          "image/bmp": ".bmp", "image/heic": ".heic", "image/heif": ".heif"}  # fmt: skip


@dataclass
class Unpacked:
    email: str
    subject: str
    saved: list[Path] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)  # attachments that are not invoices (and why)
    error: str = ""  # why the email could not be read at all (it was moved to emails/could not read)


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


def attachments(raw: bytes) -> tuple[str, list[tuple[str, bytes]], list[str]]:
    """(subject, [(file name, content)] of the invoice attachments, [skipped attachments]) of an email,
    including the attachments of emails forwarded as attachments."""
    if len(raw) > MAX_EMAIL_BYTES:
        raise UnreadableEmail(f"the email is larger than {MAX_EMAIL_BYTES // 1_000_000} MB")
    try:
        msg = email.message_from_bytes(raw, policy=email.policy.default)
    except RecursionError:
        raise UnreadableEmail("it holds too many emails forwarded inside each other") from None
    assert isinstance(msg, EmailMessage)
    found: list[tuple[str, bytes]] = []
    skipped: list[str] = []

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
            if part.is_multipart() or part.get_content_maintype() in ("text", "message", "multipart"):
                continue
            name = part.get_filename() or ""
            label = name or "an unnamed attachment"
            try:
                content = part.get_payload(decode=True) or b""
            except Exception:  # a damaged attachment
                skipped.append(f"{label} (could not be read)")
                continue
            suffix = _kind(name, part.get_content_type(), content)
            if not suffix:
                skipped.append(f"{label} (not a PDF or image)")
                continue
            if suffix == ".pdf" and not content.lstrip()[:1024].startswith(b"%PDF") and b"%PDF" not in content[:1024]:
                skipped.append(f"{label} (named as a PDF but is not one)")
                continue
            if suffix != ".pdf":
                inline = part.get("Content-ID") and part.get_content_disposition() != "attachment"
                if len(content) < MIN_IMAGE_BYTES or (inline and len(content) < MAX_LOGO_BYTES):
                    skipped.append(f"{label} (a small or inline picture, e.g. a logo)")
                    continue
            safe = safe_name(name or f"attachment{suffix}")
            if Path(safe).suffix.lower() != suffix:  # keep a type the invoices folder picks up
                safe = f"{safe if Path(safe).suffix.lower() not in DOCUMENT_EXTENSIONS else Path(safe).stem}{suffix}"
            found.append((safe, content))

    walk(msg)
    return str(msg.get("Subject") or ""), found, skipped


def _move_to(path: Path, done: Path) -> None:
    """Move the email into ``done`` under a name not taken yet; left in place if it cannot be moved."""
    done.mkdir(parents=True, exist_ok=True)
    target = done / path.name
    n = 1
    while target.exists():
        target = done / f"{path.stem}_{n}{path.suffix}"
        n += 1
    try:
        path.replace(target)
    except OSError:
        pass  # left in place (e.g. open in another program): unpacking again writes nothing new


def _unreadable(path: Path, folder: Path, exc: Exception) -> Unpacked:
    """File away an email that cannot be read, so it is not tried again on every check, and say why."""
    why = str(exc) if isinstance(exc, UnreadableEmail) else f"it is damaged ({type(exc).__name__})"
    _move_to(path, folder / DONE_FOLDER / UNREADABLE_FOLDER)
    return Unpacked(path.name, "", error=f"could not be read: {why}")


def unpack(path: Path, folder: Path | None = None) -> Unpacked:
    """Save the invoice attachments of the email at ``path`` into ``folder`` (default: its own folder),
    then move the email to ``<folder>/emails``. A file with the same name and content is not written twice.
    An email that cannot be read moves to ``<folder>/emails/could not read`` and ``error`` says why;
    an OSError (the file locked or removed meanwhile) is raised, so the next check tries again."""
    folder = folder or path.parent
    raw = path.read_bytes()
    try:
        subject, found, skipped = attachments(raw)
    except Exception as exc:  # noqa: BLE001 - one bad email must never stop the others
        return _unreadable(path, folder, exc)
    result = Unpacked(path.name, subject, skipped=skipped)
    stem = safe_name(path.stem)[:40].rstrip(" .")
    for name, content in found:
        target = folder / f"{stem} - {name}"
        n = 1
        while target.exists() and target.read_bytes() != content:
            target = folder / f"{stem} - {Path(name).stem}_{n}{Path(name).suffix}"
            n += 1
        if not target.exists():
            target.write_bytes(content)
        result.saved.append(target)
    _move_to(path, folder / DONE_FOLDER)
    return result


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
