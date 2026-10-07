"""Invoices that arrive by email: take the PDF and image attachments out of saved emails (.eml).

An email saved as .eml (Outlook on the web and new Outlook: *Download*; most mail programs: *Save as*)
dropped in the invoices folder, or uploaded, has its invoice attachments saved next to it as
"<email name> - <attachment name>"; the email itself moves to ``emails/`` so it is unpacked once.

Signature logos and other small inline pictures are left out: an image referenced from the email's own
text (a Content-ID) or smaller than ``MIN_IMAGE_BYTES`` is not an invoice.
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


def _safe(name: str) -> str:
    name = re.split(r"[\\/]", name)[-1]  # the file name only, whichever separator the sender's system used
    name = re.sub(r'[:*?"<>|\x00-\x1f]', "_", name).strip(" .")
    if len(name) > 80:  # short enough for Windows paths; the extension is kept
        suffix = Path(name).suffix[:10]
        name = name[: 80 - len(suffix)].rstrip(" .") + suffix
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
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    assert isinstance(msg, EmailMessage)
    found: list[tuple[str, bytes]] = []
    skipped: list[str] = []

    def walk(message: EmailMessage) -> None:
        for part in message.walk():  # also goes into emails forwarded as attachments (message/rfc822)
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
            safe = _safe(name or f"attachment{suffix}")
            if Path(safe).suffix.lower() != suffix:  # keep a type the invoices folder picks up
                safe = f"{safe if Path(safe).suffix.lower() not in DOCUMENT_EXTENSIONS else Path(safe).stem}{suffix}"
            found.append((safe, content))

    walk(msg)
    return str(msg.get("Subject") or ""), found, skipped


def unpack(path: Path, folder: Path | None = None) -> Unpacked:
    """Save the invoice attachments of the email at ``path`` into ``folder`` (default: its own folder),
    then move the email to ``<folder>/emails``. A file with the same name and content is not written twice."""
    folder = folder or path.parent
    subject, found, skipped = attachments(path.read_bytes())
    result = Unpacked(path.name, subject, skipped=skipped)
    stem = _safe(path.stem)[:40].rstrip(" .")
    for name, content in found:
        target = folder / f"{stem} - {name}"
        n = 1
        while target.exists() and target.read_bytes() != content:
            target = folder / f"{stem} - {Path(name).stem}_{n}{Path(name).suffix}"
            n += 1
        if not target.exists():
            target.write_bytes(content)
        result.saved.append(target)
    done = folder / "emails"
    done.mkdir(exist_ok=True)
    target = done / path.name
    n = 1
    while target.exists():
        target = done / f"{path.stem}_{n}{path.suffix}"
        n += 1
    try:
        path.replace(target)
    except OSError:
        pass  # left in place (e.g. open in another program): unpacking again writes nothing new
    return result


def emails_in(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in EMAIL_EXTENSIONS)


def unpack_folder(folder: Path, now: float | None = None) -> list[Unpacked]:
    """Unpack every saved email in ``folder`` that has finished copying (unchanged for ``SETTLE_SECONDS``)."""
    now = time.time() if now is None else now
    out = []
    for path in emails_in(folder):
        try:
            if now - path.stat().st_mtime < SETTLE_SECONDS:
                continue  # still being written: the next check picks it up
            out.append(unpack(path, folder))
        except OSError:
            continue  # locked or removed meanwhile: the next check picks it up
    return out
