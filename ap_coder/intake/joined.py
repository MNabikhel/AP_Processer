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

A file that is part of a voucher (from the moment it is joined, read or not) or of an invoice no longer in review is
never joined again: the person is told where it is. One read as an invoice of its own and still in review is taken
into the voucher, its own invoice removed: what :func:`separate` did is undone the same way. While the voucher is
still in review, :func:`separate` puts the files back in the invoices folder to be read one by one; a voucher deleted
takes its files with it (:func:`set_aside`). Who joined and who separated which files are events (``joined``,
``separated``: ``core/audit.py``).
"""

from __future__ import annotations

import hashlib
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

from ..capture.layout import PDF_LOCK, CannotRead
from ..core.store import REVIEW
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


def _where(store: Any, path: Path, sha: str) -> tuple[str, dict[str, Any] | None]:
    """Where the file is already in AP Coder, as a sentence ("" when it is not, or when it is an invoice of its own
    still in review: that invoice, to be taken into the voucher)."""
    part = store.joined_where(sha)
    if part is not None and not part["invoice_id"]:  # joined, its voucher not read yet (B-2283)
        return (f"{path.name} is already in AP Coder: it is {pages_said(part)} of {part['file_name']}, a voucher "
                "waiting to be read."), None  # fmt: skip
    if part is not None:
        return (f"{path.name} is already in AP Coder: it is {pages_said(part)} of invoice #{part['invoice_id']} "
                f"({part['file_name']})."), None  # fmt: skip
    found = store.find_by_hash(path, sha=sha)
    if found is None or found["status"] == REVIEW:
        return "", found
    return f"{path.name} is already in AP Coder: it is invoice #{found['id']}.", None


def _one_tax_area(store: Any, files: list[dict[str, Any]]) -> None:
    """Raise ``CannotJoin`` when the receipts already read on their own show two tax areas and JD Edwards is the
    export in use (a pay item has one tax area: the voucher would be held, B-2288). Receipts not read yet say nothing
    here: the voucher's own finding does (``claim.two_areas``)."""
    from ..coding.claim import tax_area, two_areas
    from ..erp import jde

    if not jde.in_use(store) or jde.load(store).tax_on_gl:
        return
    read = []
    for f in files:
        invoice = store.get_invoice(f["taken"]["id"]) if f["taken"] else None
        coded = (invoice or {}).get("final_output") or (invoice or {}).get("ai_output")
        if coded:
            read.append({"name": f["name"], "tax": tax_area(coded.get("tax_lines") or [])})
    if said := two_areas(read):
        raise CannotJoin(f"{said}. Nothing was joined.")


def join(store: Any, folder: Path, paths: list[Path], kind: str, actor: str | None = None) -> Path:
    """Join the files at ``paths`` (PDFs and pictures in the invoices folder ``folder``), in that order, into one PDF
    there, named after the first ("IMG_0412 and 2 more.pdf"), and return it for the import queue. The files are moved
    to ``originals/`` and kept with the document's SHA-256 (``Store.add_joined_files``); a file read as an invoice of
    its own and still in review becomes part of the voucher (that invoice is removed, its document set aside); the
    event ``joined`` says who (``actor``) joined which files, as what. Raises ``CannotJoin``, with nothing joined, for
    fewer than two files, a file that is neither a PDF nor a picture, one that can't be opened, the same file twice,
    one already part of a voucher or of an invoice no longer in review (it says where), one in line to be read on its
    own, or receipts of one claim already read that are of two tax areas where JD Edwards takes one."""
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
        already, taken = _where(store, path, sha)
        if already:
            raise CannotJoin(f"{already} Nothing was joined: leave it out and join the others.")
        if store.in_line(sha):
            raise CannotJoin(f"{path.name} is being read as an invoice of its own right now. Nothing was joined: "
                             "join it once it is read.")  # fmt: skip
        files.append({"path": path, "raw": raw, "name": path.name, "sha256": sha, "taken": taken})
    if kind == CLAIM:
        _one_tax_area(store, files)
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
    joined = hashlib.sha256(content).hexdigest()
    store.add_joined_files(joined, kind, files)
    store.log_event("joined", None, actor, {
        "file": target.name, "kind": kind, "files": [f["name"] for f in files], "joined": joined,
        "took": _take(store, folder, files, actor),
    })  # fmt: skip
    log.info("%s: joined from %s (kept in %s)", target.name, ", ".join(f["name"] for f in files), keep_dir)
    return target


def _take(store: Any, folder: Path, files: list[dict[str, Any]], actor: str | None) -> list[int]:
    """The files read as invoices of their own and still in review (``taken``) are part of the voucher now: each
    such invoice is removed and its document set aside (the file as it came is kept with the voucher's). Their
    numbers. One approved meanwhile is left as it is."""
    took = []
    for f in files:
        if not f["taken"]:
            continue
        invoice = store.get_invoice(f["taken"]["id"]) or {}
        try:
            store.delete_invoice(f["taken"]["id"], actor=actor, keep_approved=True)
        except ValueError as exc:
            log.warning("%s: its own invoice was left (%s)", f["name"], exc)
            continue
        took.append(f["taken"]["id"])
        document = Path(invoice.get("source_path") or "")
        if document.is_file() and document.parent.resolve() == folder.resolve():
            set_aside(folder, [document])
    return took


def set_aside(folder: Path, files: list[Path]) -> None:
    """Move ``files`` to the invoices folder's ``deleted``, where a deleted invoice's file goes: out of the pick-up's
    way, never lost. One that can't be moved (open in another program) stays where it is."""
    aside = folder / "deleted"
    for path in files:
        try:
            aside.mkdir(exist_ok=True)
            os.replace(path, _free_name(aside, path.name))
        except OSError as exc:
            log.warning("%s: could not be moved to %s (%s)", path.name, aside, exc)


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


def _as_joined(invoice: dict[str, Any], parts: list[dict[str, Any]], folder: Path) -> Path | None:
    """The copy kept in ``originals/`` of the voucher's document as it was joined, when its pages were set upright
    since (``pictures.turn_upright``): AP Coder's own, its pages all in the voucher's files. None otherwise."""
    original = (invoice.get("meta") or {}).get("original") or {}
    kept = folder / str(original.get("kept") or "")
    return kept if original.get("sha256") == parts[0]["joined"] and kept.is_file() else None


def removed(store: Any, invoice: dict[str, Any], parts: list[dict[str, Any]]) -> None:
    """The voucher ``invoice`` (its stored row, made of ``parts``: ``Store.joined_files``) was deleted: its files
    go where a deleted invoice's file goes (:func:`set_aside`), the copy of its document as joined is removed, and
    they are no longer a voucher's files, so nothing of it is left in ``originals/`` (B-2289)."""
    folder = Path(invoice.get("source_path") or "").parent
    if copy := _as_joined(invoice, parts, folder):
        copy.unlink(missing_ok=True)
    set_aside(folder, [folder / p["kept"] for p in parts if (folder / p["kept"]).is_file()])
    store.forget_joined_files(parts[0]["joined"])


def separate(store: Any, invoice: dict[str, Any], actor: str | None = None) -> list[Path]:
    """Undo :func:`join` for the voucher ``invoice`` (its stored row): its files are moved back from ``originals/``
    to the invoices folder beside its document, the document (and the copy kept of it as joined, when its pages were
    set upright) and the invoice are removed, and the files are returned to be put in line one by one; the event
    ``separated`` says who (``actor``) did. Raises ``CannotJoin``, with nothing changed, when the invoice was not
    made of several files or one of them is no longer where it was kept (and ``ValueError`` when it was approved
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
    copy = _as_joined(invoice, parts, folder)
    store.delete_invoice(invoice["id"], actor=actor, keep_approved=True)
    store.forget_joined_files(parts[0]["joined"])
    store.log_event("separated", invoice["id"], actor, {
        "file": invoice.get("file_name") or document.name, "kind": parts[0]["kind"],
        "files": [p["name"] for p in parts],
    })  # fmt: skip
    back = []
    for part in parts:
        kept = folder / part["kept"]
        target = kept if kept.parent == folder else _free_name(folder, part["name"], kept.read_bytes())
        if target != kept:
            os.replace(kept, target)
        back.append(target)
    document.unlink(missing_ok=True)
    if copy is not None:
        copy.unlink(missing_ok=True)
    return back
