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
_UNLISTED = ("readme", ".", "~$")  # README files, hidden files, Office lock files: not invoices, not mentioned


def folder_names(folder: Path) -> tuple[list[str], list[str], list[str]]:
    """The names of ``folder``'s invoice files, saved emails and files of a kind not read (not its subfolders), each
    sorted as their paths sort, from one listing by name: each entry's kind as the listing says it, no look at a file
    (a folder of 100,000 invoices, D-166). Raises OSError."""
    known = SUPPORTED_EXTENSIONS | EMAIL_EXTENSIONS | STILL_ARRIVING
    names, emails, others = [], [], []
    with os.scandir(folder) as entries:
        for entry in entries:
            name, lower = entry.name, entry.name.lower()
            suffix = os.path.splitext(lower)[1]  # (``Path.suffix`` of a name that doesn't start with a dot)
            shown = not lower.startswith(_UNLISTED)
            if suffix in SUPPORTED_EXTENSIONS and shown and entry.is_file():
                names.append(name)
            if suffix in EMAIL_EXTENSIONS and not lower.startswith(".") and entry.is_file():
                emails.append(name)
            if suffix not in known and shown and lower not in NOT_A_FILE_TO_MENTION and entry.is_file():
                others.append(name)
    names.sort(key=os.path.normcase)
    parts = [os.path.splitext(name) for name in names]
    documents = {stem.lower() for stem, suffix in parts if suffix.lower() not in TEXT_EXTENSIONS}
    names = [name for name, (stem, suffix) in zip(names, parts, strict=True)
             if suffix.lower() not in TEXT_EXTENSIONS or stem.lower() not in documents]  # fmt: skip
    return names, sorted(emails, key=os.path.normcase), sorted(others, key=os.path.normcase)
