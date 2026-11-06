"""Which files of a folder AP Coder reads, one rule for all (D-168): the invoices (PDFs, pictures, text invoices), the
saved emails whose attachments are read, and the other files, said to the person, never passed over in silence.
``ai/pipeline.py``'s ``invoice_files`` and ``other_files``, ``intake/mailbox.py``'s ``emails_in`` and the import page's
list (``web/pages/process.py``) are each a part of ``folder_names``.

Not listed at all: hidden files (a Mac's "._inv.pdf" beside "inv.pdf" on a shared or USB drive; never a saved email
either, B-1758), README files and Office lock files ("~$...") as invoices or other files, Windows' own files and
downloads still arriving. A text copy (.md, .txt) beside a PDF or a picture of its name is the same invoice (the bundled
samples): only the document is read.
"""

from __future__ import annotations

import os
from pathlib import Path

from ..ai.extraction import SUPPORTED_EXTENSIONS, TEXT_EXTENSIONS

EMAIL_EXTENSIONS = {".eml", ".msg"}
NOT_A_FILE_TO_MENTION = {"desktop.ini", "thumbs.db"}  # Windows' own, in any folder
STILL_ARRIVING = {".part", ".partial", ".crdownload", ".download", ".tmp"}  # a copy or a download not finished
_UNLISTED = ("readme", "~$")  # README files, Office lock files: not invoices, not mentioned (a saved email still)
_ORDER = os.path.normcase if os.name == "nt" else None  # as paths sort (``normcase`` changes nothing elsewhere)


def folder_names(folder: Path) -> tuple[list[str], list[str], list[str]]:
    """The names of ``folder``'s invoice files, saved emails and files of a kind not read (not its subfolders), each
    sorted as their paths sort, from one listing by name: each entry's kind as the listing says it, no look at a file
    (a folder of 100,000 invoices, D-166; 300,000 in 0.3 s, D-176). Raises OSError."""
    names, texts, emails, others = [], set(), [], []
    with os.scandir(folder) as entries:
        for entry in entries:
            name = entry.name
            lower = name.lower()
            if lower[:1] == ".":  # hidden: never listed, not even as a saved email (B-1758)
                continue
            dot = lower.rfind(".")
            suffix = lower[dot:] if dot > 0 else ""  # (``Path.suffix`` of a name that doesn't start with a dot)
            if suffix in EMAIL_EXTENSIONS:
                if entry.is_file():
                    emails.append(name)
            elif lower.startswith(_UNLISTED):
                continue
            elif suffix in SUPPORTED_EXTENSIONS:
                if entry.is_file():
                    names.append(name)
                    if suffix in TEXT_EXTENSIONS:
                        texts.add(name)
            elif suffix not in STILL_ARRIVING and lower not in NOT_A_FILE_TO_MENTION and entry.is_file():
                others.append(name)
    if texts:  # a text copy beside a document of its name: only the document is listed
        copies = {_stem(name) for name in texts}
        documents = {stem for name in names if name not in texts and (stem := _stem(name)) in copies}
        names = [name for name in names if name not in texts or _stem(name) not in documents]
    return sorted(names, key=_ORDER), sorted(emails, key=_ORDER), sorted(others, key=_ORDER)


def _stem(name: str) -> str:
    """A listed file's name without its suffix, in lower case, as ``os.path.splitext`` and ``str.lower`` give it."""
    return name[: name.rfind(".")].lower()
