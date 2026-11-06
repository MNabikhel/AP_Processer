"""Which files of a folder AP Coder reads (``intake/files.py``, one rule for all, D-168): the names whatever their case,
dots and letters. The rule's other cases, and that each of its users is a part of it: ``tests/web/test_web_process.py``
``test_the_invoice_files_the_saved_emails_and_the_others_are_one_rule``."""

from __future__ import annotations

import os

from ap_coder.intake.files import folder_names


def test_the_rule_reads_names_whatever_their_case_dots_and_letters(tmp_path):
    """``folder_names`` lists 300,000 files in 0.3 s, not 0.8 s (D-176): a name's suffix and stem are taken without
    ``os.path.splitext``, as it takes them. A text copy goes with a document of its stem in any case and any script;
    stems that only begin alike are two invoices; a saved email named like a README or a lock file is still one."""
    names = ["Ünïcode.PDF", "ΑΣ.pdf", "ΑΣ.txt", "a.b.c.png", "a.b.c.md", "a.png", "a.b.md", "inv.PDF", "INV.Md",
             "notes.TXT", "README.eml", "~$x.MSG", "~$lock.pdf", "Readme.docx", "x.PART", "photo.Jpeg", "plan.dwg",
             ".DS_Store", "._a.png"]  # fmt: skip
    for name in names:
        (tmp_path / name).write_bytes(b"x")
    documents, emails, others = folder_names(tmp_path)
    expected = ["Ünïcode.PDF", "ΑΣ.pdf", "a.b.c.png", "a.png", "a.b.md", "inv.PDF", "notes.TXT", "photo.Jpeg"]
    assert documents == sorted(expected, key=os.path.normcase)
    assert emails == sorted(["README.eml", "~$x.MSG"], key=os.path.normcase) and others == ["plan.dwg"]
