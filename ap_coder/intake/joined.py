"""Several files that are one voucher (D-216): the pages of one invoice scanned or photographed one by one, or the
receipts of one expense claim.

A person says so on the Add invoices page. The files are joined, in the order given, into one PDF in the invoices
folder (:func:`join`): a picture becomes its pages as a picture coming in alone does (``pictures.image_to_pdf``), a
PDF gives its pages as they are. That one document goes in line like any file; the files it was made of are kept in
``originals/`` as they came, and the database keeps which pages are whose (``Store.joined_files``), so the review
screen can say "page 3 is from IMG_0412.jpg".

* **Pages of one invoice** (``PAGES``): the document is read as any invoice is.
* **Receipts of one claim** (``CLAIM``): each receipt's pages are read on their own (:func:`pages_of`,
  ``InvoicePipeline``), then made one voucher (``coding/claim.py``).

A file that is already in AP Coder is never joined again: the person is told where it is. While the voucher is
still in review, :func:`separate` puts the files back in the invoices folder to be read one by one.
"""

from __future__ import annotations

import hashlib
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

from ..capture.layout import PDF_LOCK, CannotRead
from ..core.store.joined import CLAIM, PAGES
from .pictures import ORIGINALS_FOLDER, _free_name, as_its_kind, image_to_pdf, is_image

log = logging.getLogger(__name__)

KINDS = {PAGES: "Pages of one invoice", CLAIM: "Receipts of one claim"}  # as the Add invoices page offers them


class CannotJoin(Exception):
    """Why these files were not joined (or a voucher's files not separated), in words for the person."""


def pages_said(file: dict[str, Any]) -> str:
    """Which pages of the voucher's document a joined file is (``Store.joined_files``): "page 3", "pages 4 to 6"."""
    first, last = file["first_page"], file["first_page"] + file["pages"] - 1
    return f"page {first}" if last == first else f"pages {first} to {last}"


def _where(store: Any, path: Path, sha: str) -> str:
    """Where the file is already in AP Coder, as a sentence; "" when it is not."""
    part = store.joined_where(sha)
    if part is not None:
        return (f"{path.name} is already in AP Coder: it is {pages_said(part)} of invoice #{part['invoice_id']} "
                f"({part['file_name']}).")  # fmt: skip
    found = store.find_by_hash(path, sha=sha)
    return f"{path.name} is already in AP Coder: it is invoice #{found['id']}." if found is not None else ""


def join(store: Any, folder: Path, paths: list[Path], kind: str) -> Path:
    """Join the files at ``paths`` (PDFs and pictures in the invoices folder ``folder``), in that order, into one PDF
    there, named after the first ("IMG_0412 and 2 more.pdf"), and return it for the import queue. The files are moved
    to ``originals/`` and kept with the document's SHA-256 (``Store.add_joined_files``). Raises ``CannotJoin``, with
    nothing joined, for fewer than two files, a file that is neither a PDF nor a picture, one that can't be opened,
    the same file twice, or one already in AP Coder (it says where) or in line to be read on its own."""
    import pymupdf

    if kind not in KINDS:
        raise CannotJoin("Choose what the files are: the pages of one invoice, or the receipts of one claim.")
    if len(paths) < 2:
        raise CannotJoin("Tick at least two files to join: one file alone is read as it is.")
    files, seen = [], {}
    for path in paths:
        try:
            path = as_its_kind(path)
            raw = path.read_bytes()
        except CannotRead as exc:
            raise CannotJoin(str(exc)) from exc
        except OSError as exc:
            raise CannotJoin(f"{path.name} can't be opened just now ({exc.strerror or type(exc).__name__}).") from exc
        if not is_image(path) and path.suffix.lower() != ".pdf":
            raise CannotJoin(f"{path.name} is not a PDF or a picture: only those can be joined into one voucher.")
        sha = hashlib.sha256(raw).hexdigest()
        if sha in seen:
            raise CannotJoin(f"{path.name} is the same file as {seen[sha]}: leave one of them out.")
        seen[sha] = path.name
        already = _where(store, path, sha)
        if already:
            raise CannotJoin(f"{already} Nothing was joined: leave it out and join the others.")
        if store.in_line(sha):
            raise CannotJoin(f"{path.name} is being read as an invoice of its own right now. Nothing was joined.")
        files.append({"path": path, "raw": raw, "name": path.name, "sha256": sha})
    with PDF_LOCK:
        out = pymupdf.open()
    try:
        with tempfile.TemporaryDirectory() as work:
            for n, f in enumerate(files):
                f["first_page"], f["pages"] = out.page_count + 1, _add(out, f["path"], Path(work) / f"{n}.pdf")
        with PDF_LOCK:
            content = out.tobytes(garbage=3, deflate=True)
    finally:
        with PDF_LOCK:
            out.close()
    target = _free_name(folder, f"{files[0]['path'].stem} and {len(files) - 1} more.pdf")
    part = target.with_name(target.name + ".part")  # never seen half written by a pick-up of the folder
    part.write_bytes(content)
    os.replace(part, target)
    keep_dir = folder / ORIGINALS_FOLDER
    keep_dir.mkdir(parents=True, exist_ok=True)
    for f in files:
        f["kept"] = _keep(f["path"], f["raw"], keep_dir).relative_to(folder).as_posix()
    store.add_joined_files(hashlib.sha256(content).hexdigest(), kind, files)
    log.info("%s: joined from %s (kept in %s)", target.name, ", ".join(f["name"] for f in files), keep_dir)
    return target


def _add(out: Any, path: Path, work: Path) -> int:
    """Add the pages of the PDF or picture at ``path`` to the open PDF ``out`` (``work``: where a picture's own PDF is
    made on the way); how many. Raises ``CannotJoin`` when the file can't be opened."""
    import pymupdf

    from ..ai.pipeline import plain_error

    try:
        if is_image(path):
            image_to_pdf(path, work)
        with PDF_LOCK, pymupdf.open(work if is_image(path) else path) as doc:
            if doc.needs_pass:
                raise CannotJoin(f"{path.name} is protected by a password: save it without one, then join it.")
            if not doc.page_count:
                raise CannotJoin(f"{path.name} has no pages in it.")
            out.insert_pdf(doc)
            return int(doc.page_count)
    except CannotJoin:
        raise
    except Exception as exc:  # noqa: BLE001 - a damaged picture or PDF: said plainly, nothing joined
        raise CannotJoin(str(exc) if isinstance(exc, CannotRead) else plain_error(exc, path)) from exc


def _keep(path: Path, raw: bytes, keep_dir: Path) -> Path:
    """Move a joined file to ``originals/`` as a picture made into a PDF is kept (once, whatever its name; left where
    it is when another program holds it). Where it is now."""
    kept = _free_name(keep_dir, path.name, raw)
    try:
        if kept.exists():
            path.unlink()
        else:
            os.replace(path, kept)
    except OSError as exc:
        log.warning("%s: could not be moved to %s (%s)", path.name, keep_dir, exc)
        return path
    return kept


def pages_of(pdf: Path, first_page: int, pages: int, target: Path) -> Path:
    """Write ``pages`` pages of ``pdf`` from ``first_page`` (from 1) as the PDF ``target``: one receipt of a claim,
    to be read on its own."""
    import pymupdf

    with PDF_LOCK, pymupdf.open(pdf) as doc, pymupdf.open() as part:
        part.insert_pdf(doc, from_page=first_page - 1, to_page=first_page + pages - 2)
        part.save(target)
    return target


def separate(store: Any, invoice: dict[str, Any], actor: str | None = None) -> list[Path]:
    """Undo :func:`join` for the voucher ``invoice`` (its stored row): its files are moved back from ``originals/``
    to the invoices folder beside its document, the document and the invoice are removed, and the files are returned
    to be put in line one by one. Raises ``CannotJoin``, with nothing changed, when the invoice was not made of
    several files or one of them is no longer where it was kept (and ``ValueError`` when it was approved
    meanwhile: ``Store.delete_invoice``)."""
    parts = store.joined_files(invoice_id=invoice["id"])
    document = Path(invoice.get("source_path") or "")
    folder = document.parent
    if not parts:
        raise CannotJoin("This invoice was not made of several files.")
    gone = [p["name"] for p in parts if not (folder / p["kept"]).is_file()]
    if gone:
        raise CannotJoin(f"{', '.join(gone)} can no longer be found in the {ORIGINALS_FOLDER} folder, so the files "
                         "were left as one invoice.")  # fmt: skip
    store.delete_invoice(invoice["id"], actor=actor, keep_approved=True)
    store.forget_joined_files(parts[0]["joined"])
    back = []
    for part in parts:
        kept = folder / part["kept"]
        target = kept if kept.parent == folder else _free_name(folder, part["name"], kept.read_bytes())
        if target != kept:
            os.replace(kept, target)
        back.append(target)
    document.unlink(missing_ok=True)
    return back
