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
from dataclasses import dataclass, field
from email.message import EmailMessage
from pathlib import Path

from .extraction import DOCUMENT_EXTENSIONS

EMAIL_EXTENSIONS = {".eml"}
MIN_IMAGE_BYTES = 15_000
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
    return name[:120] or "attachment"


def attachments(raw: bytes) -> tuple[str, list[tuple[str, bytes]], list[str]]:
    """(subject, [(file name, content)] of the invoice attachments, [skipped attachments]) of an email,
    including the attachments of emails forwarded as attachments."""
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    assert isinstance(msg, EmailMessage)
    found: list[tuple[str, bytes]] = []
    skipped: list[str] = []

    def walk(message: EmailMessage) -> None:
        for part in message.walk():  # also goes into emails forwarded as attachments (message/rfc822)
            if part.is_multipart():
                continue
            name = part.get_filename() or ""
            suffix = Path(name).suffix.lower() or _TYPES.get(part.get_content_type(), "")
            if suffix not in DOCUMENT_EXTENSIONS:
                if name:
                    skipped.append(f"{name} (not a PDF or image)")
                continue
            try:
                content = part.get_payload(decode=True) or b""
            except Exception:  # a damaged attachment
                skipped.append(f"{name or 'attachment'} (could not be read)")
                continue
            if suffix != ".pdf":
                inline = part.get("Content-ID") and part.get_content_disposition() != "attachment"
                if inline or len(content) < MIN_IMAGE_BYTES:
                    if name:
                        skipped.append(f"{name} (a small or inline picture, e.g. a logo)")
                    continue
            safe = _safe(name or f"attachment{suffix}")
            if Path(safe).suffix.lower() != suffix:  # keep a type the invoices folder picks up
                safe = f"{Path(safe).stem or 'attachment'}{suffix}"
            found.append((safe, content))

    walk(msg)
    return str(msg.get("Subject") or ""), found, skipped


def unpack(path: Path, folder: Path | None = None) -> Unpacked:
    """Save the invoice attachments of the email at ``path`` into ``folder`` (default: its own folder),
    then move the email to ``<folder>/emails``. A file with the same name and content is not written twice."""
    folder = folder or path.parent
    subject, found, skipped = attachments(path.read_bytes())
    result = Unpacked(path.name, subject, skipped=skipped)
    stem = _safe(path.stem)[:60]
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


def unpack_folder(folder: Path) -> list[Unpacked]:
    """Unpack every saved email in ``folder``."""
    out = []
    for path in emails_in(folder):
        try:
            out.append(unpack(path, folder))
        except OSError:
            continue  # being copied or locked: the next check picks it up
    return out
