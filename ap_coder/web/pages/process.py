"""Import invoices: the setup checklist, uploading invoices, picking up new files in the invoices folder.

Reading and coding runs as the background job "process" (``ap_coder/web/jobs.py``): the page uploads the files,
then polls ``GET /process/status`` and shows each invoice as it is done. The demo invoices (load / remove) are here
too; the review queue's welcome card uses the same routes.
"""

from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from ap_coder.ai import page_reader
from ap_coder.ai.extraction import SUPPORTED_EXTENSIONS
from ap_coder.ai.local_llm import provider_status
from ap_coder.ai.pipeline import InvoicePipeline, PipelineResult, invoice_files, other_files
from ap_coder.capture.layout import ocr_available
from ap_coder.coding.tax import TAX_TYPES
from ap_coder.core import paths, ui
from ap_coder.core.config import Settings
from ap_coder.core.reference_data import ReferenceData
from ap_coder.core.store import REVIEW, Store
from ap_coder.intake import inbox
from ap_coder.intake.mailbox import EMAIL_EXTENSIONS, Unpacked, emails_in, safe_name, unpack, unpack_folder
from ap_coder.intake.pictures import looks_incomplete
from ap_coder.web import identity
from ap_coder.web.core import (
    cache_dir,
    done,
    flash_message,
    get_settings,
    get_store,
    invoice_dir,
    jobs,
    local_url,
    message,
    open_folder,
    reading_status_or_none,
    reference_or_none,
    refuse,
    render,
    reviewer,
    short_path,
)
from ap_coder.web.jobs import Job
from ap_coder.web.pages.review import attention

router = APIRouter()

JOB = "process"
STILL_SAVING_SECONDS = 60  # a file changed this recently whose end is missing is still being written
ACCEPT = sorted(SUPPORTED_EXTENSIONS | EMAIL_EXTENSIONS)  # what the upload takes: PDFs, scans, photos, saved emails


# --- Saving uploads -----------------------------------------------------------------------------------------------


def safe_file_name(name: str) -> str:
    """Just the file name of an upload (no folders, Windows or POSIX), never empty or a dot name, and short
    enough for Windows paths (80 characters, the extension kept)."""
    base = PureWindowsPath(PurePosixPath(name or "").name).name.strip().lstrip(".")
    return safe_name(base) if base else "invoice"


def save_upload(folder: Path, name: str, content: bytes) -> Path:
    """Save an uploaded file to ``folder`` under a safe name (a different file with the same name gets ``_1``,
    ``_2``...). Raises OSError when it cannot be written."""
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / safe_file_name(name)
    stem, n = target.stem, 1
    while target.exists() and target.read_bytes() != content:
        target = folder / f"{stem}_{n}{target.suffix}"
        n += 1
    target.write_bytes(content)
    return target


def split_new(store: Store, files: list[Path]) -> tuple[list[Path], list[Path]]:
    """(new files, files already in AP Coder): a file seen before, or a repeat within ``files``, is not read again."""
    todo, already, seen = [], [], set()
    for path in files:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest in seen or store.find_by_hash(path) is not None:
            already.append(path)
        else:
            todo.append(path)
        seen.add(digest)
    return todo, already


def email_line(mail: Unpacked) -> tuple[str, str]:
    """(text, tone) about a saved email: what it was, where its attachments went, what was left out and why."""
    if mail.error:
        return (f"{mail.email} {mail.error}. It was moved to the emails/could not read subfolder; ask the sender to "
                "send the invoice again."), "err"  # fmt: skip
    t = mail.triage
    went = []
    if mail.saved:
        went.append(f"{ui.plural(len(mail.saved), 'invoice')} to the review queue")
    if mail.statements:
        went.append(f"{ui.plural(len(mail.statements), 'statement')} to Statements")
    if mail.held:
        went.append(f"{ui.plural(len(mail.held), 'attachment')} set aside for a person")
    kind = t.label if t else "Email"
    text = f"{mail.email}: {kind}" + (f"; {', '.join(went)}" if went else "")
    if t and t.needs_person:
        text += f". Needs a person: {'; '.join(t.reasons[:2])}"
    if mail.skipped:
        text += f". Left out: {'; '.join(mail.skipped)}"
    return text, "err" if t and t.possible_fraud else "warn" if t and t.needs_person else "info"


# --- Setup ---------------------------------------------------------------------------------------------------------


def tax_types_mapped(store: Store) -> int:
    """Tax types ready to post: added to expense lines, or mapped to a GL account that still exists."""
    codes = {a["code"] for a in store.list_accounts("gl_accounts")}
    return sum(1 for t in store.tax_treatments().values() if not t.needs_gl or t.gl_code in codes)


def setup_steps(store: Store, settings: Settings) -> list[tuple[str, str, str]]:
    """(state, label, detail) of what AP Coder needs before it reads invoices; state ok, todo, bad or opt."""
    mapped = tax_types_mapped(store)
    gl_count = len(store.list_accounts("gl_accounts"))
    ai = provider_status(settings)  # the chat model in LM Studio that suggests GL accounts (optional)
    if ocr_available():
        reader = ("ok", "Reading invoices", "PDF text + OCR")
    else:
        reader = ("todo", "Reading invoices", "text PDFs only: scans need OCR (double-click APProcessor again)")
    status = reading_status_or_none(settings, store)
    if status is None:
        second = ("todo", "OvisOCR2", "see Settings → Reading")
    elif status.page_reader_ready:
        second = ("ok", "OvisOCR2", "reads every invoice in the background")
    elif status.self_test in ("pending", "running"):
        second = ("wait", "OvisOCR2", "testing itself (10 to 20 min the first time); use AP Coder meanwhile")
    elif status.self_test == "failed":
        second = ("bad", "OvisOCR2", "failed its self-test (Settings → Reading)")
    else:
        second = ("todo", "OvisOCR2", "not running in LM Studio: invoices wait for it (Settings → Reading)")
    return [  # the review queue's getting-started card reads the chat model at index 1; process.html 2 to 5
        reader,
        ("ok" if ai.ready else "opt", "Chat model for GL suggestions", ai.label if ai.ready else "optional"),
        ("ok" if gl_count else "todo", "GL accounts", f"{gl_count} imported" if gl_count else "import them"),
        ("ok" if mapped == len(TAX_TYPES) else "todo", "Sales tax GL mapping", f"{mapped} of {len(TAX_TYPES)} set"),
        second,
        name_step(),
    ]


def name_step() -> tuple[str, str, str]:
    """Whether the person approving gave their name: each approval records it, and without one it is the computer
    login ("root", "jsmith"). Given in Settings → Review (or AP_REVIEWER), or by a colleague on the "Who are you?"
    page."""
    colleague = identity.person()
    name = colleague.name if colleague is not None else ""
    name = name or str(paths.read_user_settings().get("reviewer") or "") or os.environ.get("AP_REVIEWER", "")
    if name.strip():
        return "ok", "Your name", name.strip()
    return "todo", "Your name", "Settings → Review: each approval is recorded in it"


def first_steps(store: Store, settings: Settings, has_invoices: bool) -> tuple[list[tuple[str, str, str]], int, int]:
    """(steps, done, needed) of getting started, the same on Import invoices' Setup card and the review queue's
    welcome page: ``setup_steps``, then processing the first invoices; the optional chat model is not counted."""
    steps = setup_steps(store, settings)
    steps.append(("ok" if has_invoices else "todo", "Process your first invoices", "done" if has_invoices else "to do"))
    needed = [s for s, _, _ in steps if s != "opt"]
    return steps, sum(1 for s in needed if s == "ok"), len(needed)


def ready_to_read(steps: list[tuple[str, str, str]]) -> bool:
    """PDF text / OCR and GL accounts are enough: OvisOCR2 reads invoices once it runs, the chat model is optional."""
    return steps[0][0] != "bad" and steps[2][0] == "ok"


def queued_note(settings: Settings, store: Store, queued: int) -> str:
    """After processing: the invoices now waiting for OvisOCR2, and when it reads them ("" when none were queued)."""
    if queued <= 0:
        return ""
    them = "it" if queued == 1 else "them"
    status = reading_status_or_none(settings, store)
    if status is not None and not status.page_reader_ready:
        wait = "it waits" if queued == 1 else "they wait"
        test = getattr(status, "self_test", "")
        if getattr(status, "page_reader_model", "") and test in ("failed", "pending", "running"):
            why = "it failed its self-test" if test == "failed" else "its self-test hasn't passed yet"
            return (f"{ui.plural(queued, 'invoice')} queued for OvisOCR2, which runs in LM Studio but isn't trusted "
                    f"yet ({why}): {wait} for it (Settings → Reading).")  # fmt: skip
        return (f"{ui.plural(queued, 'invoice')} queued for OvisOCR2, which isn't running: {wait} for it "
                "(Settings → Reading).")  # fmt: skip
    eta = ""
    if status is not None and status.eta_minutes:
        eta = f", {page_reader.duration(status.eta_minutes * 60)} for the whole queue"
    return f"{ui.plural(queued, 'invoice')} queued for OvisOCR2: it reads {them} in the background{eta}."


# --- Reading and coding (the background job) ------------------------------------------------------------------------


def needs_attention(store: Store, reference: ReferenceData | None, result: PipelineResult) -> bool:
    """Whether the import list says "needs attention" of a processed invoice: as the review queue says it
    (``review.attention``: its Approve isn't open as it stands, B-197) for one waiting there; the checks' flag for one
    that isn't (approved on its own, or not saved)."""
    invoice_id = getattr(result, "invoice_id", None)
    inv = store.get_invoice(invoice_id) if invoice_id is not None else None
    if inv is None or inv["status"] != REVIEW:
        return bool(result.report and result.report.requires_review)
    return inv["id"] in attention(store, reference, [inv])


def process_files(store: Store, settings: Settings, paths: list[Path], *, already: list[Path] | None = None,
                  emails: list[Unpacked] | None = None, notes: list[dict[str, Any]] | None = None,
                  folder_emails: bool = False) -> Any:  # fmt: skip
    """The job that reads and codes ``paths`` and sends them to the review queue. ``already``: files skipped as seen
    before (said in the summary); ``emails``: saved emails unpacked for this run; ``notes``: messages from saving the
    upload; ``folder_emails``: first unpack the saved emails waiting in the invoices folder (the folder pick-up)."""

    def work(job: Job) -> dict[str, Any]:
        todo, skipped, mails = list(paths), list(already or []), list(emails or [])
        if folder_emails:
            unpacked = unpack_folder(invoice_dir(store))
            mails += unpacked
            new, dup = split_new(store, [p for m in unpacked for p in m.saved])
            todo += [p for p in new if p not in todo]
            skipped += dup
        for mail in mails:
            text, tone = email_line(mail)
            job.log(text, tone, "gpp_maybe" if tone == "err" and mail.triage else "mail")
            inbox.log_sorted(store, mail)
        job.step(0, len(todo), "Starting")
        ok = 0
        waiting = store.page_reads_waiting()
        pipeline = reference = None
        if todo:
            reference = store.reference_data()
            pipeline = InvoicePipeline(settings, reference, cache_dir=cache_dir(store), store=store)
        for n, path in enumerate(todo, start=1):
            job.step(n - 1, len(todo), f"Reading and coding {path.name} ({n}/{len(todo)})", stage="reading")
            result = pipeline.process(path)
            if result.ok:
                ok += 1
                attention = needs_attention(store, reference, result)
                name = result.output.get("vendor_name") or path.name
                job.log(f"{name}: {'needs attention' if attention else 'ready'}", "warn" if attention else "ok",
                        "flag" if attention else "check_circle")  # fmt: skip
            else:
                job.log(f"{path.name}: {result.error}", "err", "error")
        failed = len(todo) - ok
        queued = max(0, store.page_reads_waiting() - waiting)
        messages = list(notes or [])
        if ok:
            note = queued_note(settings, store, queued)
            messages.append(flash_message(f"{ui.plural(ok, 'invoice')} read and coded. They're waiting in the review "
                                          f"queue. {note}".strip(), "ok", icon="inbox"))  # fmt: skip
        if failed:
            messages.append(flash_message(f"{ui.plural(failed, 'file')} could not be processed. See Review queue → "
                                          "Failed / rejected.", "err"))  # fmt: skip
        if skipped:
            messages.append(flash_message(f"Skipped {ui.plural(len(skipped), 'file')} already in AP Coder.", "info",
                                          [p.name for p in skipped], "content_copy"))  # fmt: skip
        if not todo and not skipped and not mails:
            messages.append(flash_message("Nothing new to read.", "info"))
        return {"processed": len(todo), "ok": ok, "failed": failed, "queued": queued, "messages": messages,
                "label": f"Processed {ok} of {len(todo)}" if todo else "Nothing new to read"}  # fmt: skip

    return work


# --- The page ------------------------------------------------------------------------------------------------------


def not_processed(store: Store, files: list[Path]) -> tuple[list[Path], list[Path]]:
    """(the files never processed, the files that can't be read now): a file still being written by a scanner, open
    in another program or a cloud placeholder not downloaded is left for the next pick-up, never a broken page."""
    new, unreadable = [], []
    for path in files:
        try:
            if store.find_by_hash(path, include_failed=True) is None:
                new.append(path)
        except OSError:
            unreadable.append(path)
    return new, unreadable


def not_read_here(folder: Path) -> list[str]:
    """The names of the files in the invoices folder of a kind AP Coder does not read (B-268)."""
    try:
        return [p.name for p in other_files(folder, EMAIL_EXTENSIONS)] if folder.exists() else []
    except OSError:
        return []


def still_being_saved(path: Path, now: float | None = None) -> bool:
    """Changed in the last minute and its end missing (``intake.looks_incomplete``): a scanner or a download is still
    writing it. Read now, half an invoice would be stored (and the whole one read again later as another). A file
    that is complete is read at once, however new."""
    try:
        recent = abs((time.time() if now is None else now) - path.stat().st_mtime) < STILL_SAVING_SECONDS
        return recent and looks_incomplete(path)
    except OSError:  # gone or locked meanwhile: next time
        return True


def folder_files(store: Store) -> list[dict[str, Any]]:
    """The files in the invoices folder not processed yet (saved emails too: their attachments are read)."""
    folder = invoice_dir(store)
    if not folder.exists():
        return []
    files, _ = not_processed(store, invoice_files(folder))
    rows = []
    for p in [*files, *emails_in(folder)]:
        try:
            size = p.stat().st_size
        except OSError:
            continue
        email = p.suffix.lower() in EMAIL_EXTENSIONS
        icon = "mail" if email else "picture_as_pdf" if p.suffix.lower() == ".pdf" else "image"
        rows.append({"name": p.name, "kb": f"{size / 1024:,.0f} KB", "icon": icon, "email": email})
    return rows


@router.get("/process", response_class=HTMLResponse)
def process_page(request: Request) -> HTMLResponse:
    from ap_coder.ops.demo import demo_available

    store, settings = get_store(request), get_settings(request)
    invoices = store.list_invoices()
    steps, steps_done, steps_needed = first_steps(store, settings, bool(invoices))
    recent = sorted(invoices, key=lambda i: (i["created_at"] or "", i["id"]), reverse=True)[:6]
    waiting = [i for i in recent if i["status"] == REVIEW]
    reasons = attention(store, reference_or_none(store), waiting) if waiting else {}
    recent = [{**i, "needs_attention": i["id"] in reasons} for i in recent]  # its pill says what the queue says
    return render(
        request, "process.html", "process",
        steps=steps,
        steps_done=steps_done,
        steps_needed=steps_needed,
        ready=ready_to_read(steps),
        recent=recent,
        recent_waiting=any(i["status"] == REVIEW for i in recent),
        files=folder_files(store),
        not_read=not_read_here(invoice_dir(store)),
        set_aside=inbox.needs_person(store, invoice_dir(store)),
        folder=short_path(invoice_dir(store)),
        accept=",".join(ACCEPT),
        accept_text=", ".join(e.lstrip(".").upper() for e in ACCEPT),
        job=jobs(request).get(JOB).snapshot(),
        demo_available=demo_available(),
        demo_count=store.demo_count(),
        where="/process",
    )  # fmt: skip


def _start(request: Request, work: Any, total: int) -> JSONResponse:
    if not jobs(request).get(JOB).start(work, total=total):
        return refuse("AP Coder is already reading invoices. Wait for it to finish, then try again.", 409)
    return JSONResponse({"ok": True, "job": JOB, "status": "/process/status"})


@router.post("/process/upload")
async def process_upload(request: Request, files: list[UploadFile] = File(...)) -> JSONResponse:  # noqa: B008
    """Save the uploaded files to the invoices folder, then read and code the new ones in the background."""
    received = [(upload.filename or "invoice", await upload.read()) for upload in files]
    return await run_in_threadpool(_save_and_start, request, received)


def _save_and_start(request: Request, received: list[tuple[str, bytes]]) -> JSONResponse:
    store, settings = get_store(request), get_settings(request)
    if jobs(request).get(JOB).running:
        return refuse("AP Coder is already reading invoices. Wait for it to finish, then try again.", 409)
    if not ready_to_read(setup_steps(store, settings)):
        return refuse("Finish the setup first: import your GL accounts (GL accounts & tax).")
    folder = invoice_dir(store)
    saved, mails, notes = [], [], []
    for name, content in received:
        if Path(name).suffix.lower() not in ACCEPT:
            notes.append(flash_message(f"{name} was not read: AP Coder reads PDFs, pictures (JPEG, PNG, HEIC, TIFF, "
                                       "BMP, WEBP, GIF) and saved emails (.eml, .msg). Save it as PDF or JPEG to have "
                                       "it read.", "warn"))  # fmt: skip
            continue
        try:  # one file that cannot be saved (a name Windows refuses, disk full) is said and skipped
            target = save_upload(folder, name, content)
            if target.suffix.lower() in EMAIL_EXTENSIONS:  # its invoice attachments, not the email
                mail = unpack(target, folder)
                mails.append(mail)
                saved.extend(mail.saved)
                continue
        except OSError as exc:
            notes.append(flash_message(f"{name} could not be saved to the invoices folder "
                                       f"({exc.strerror or type(exc).__name__}). Rename it or save it there yourself.",
                                       "err"))  # fmt: skip
            continue
        saved.append(target)
    todo, already = split_new(store, saved)
    work = process_files(store, settings, todo, already=already, emails=mails, notes=notes)
    return _start(request, work, len(todo))


@router.post("/process/folder")
def process_folder(request: Request) -> JSONResponse:
    """Read and code the new files in the invoices folder (and the invoices attached to saved emails there)."""
    store, settings = get_store(request), get_settings(request)
    if not ready_to_read(setup_steps(store, settings)):
        return refuse("Finish the setup first: import your GL accounts (GL accounts & tax).")
    folder = invoice_dir(store)
    files, unreadable = not_processed(store, invoice_files(folder) if folder.exists() else [])
    saving = [p for p in files if still_being_saved(p)]  # a scanner or a download still writing it (B-265)
    files, unreadable = [p for p in files if p not in saving], unreadable + saving
    todo, already = split_new(store, files)
    notes = []
    if unreadable:
        notes.append(flash_message(f"{ui.plural(len(unreadable), 'file')} could not be opened (still being saved, or "
                                   "open in another program): picked up next time.", "warn",
                                   [p.name for p in unreadable]))  # fmt: skip
    others = not_read_here(folder)
    if others:
        are = "are" if len(others) > 1 else "is"
        notes.append(flash_message(f"{ui.plural(len(others), 'file')} in the invoices folder {are} not a kind AP Coder "
                                   "reads (PDFs, scans, photos and saved emails): save them as PDF (or JPEG) to have "
                                   "them read.", "warn", others))  # fmt: skip
    work = process_files(store, settings, todo, already=already, folder_emails=True, notes=notes)
    return _start(request, work, len(todo))


@router.get("/process/status")
def process_status(request: Request) -> JSONResponse:
    """How far reading and coding got (the page polls this)."""
    return JSONResponse(jobs(request).get(JOB).snapshot(), headers={"Cache-Control": "no-store"})


@router.post("/process/open-folder")
def process_open_folder(request: Request) -> JSONResponse:
    folder = invoice_dir(get_store(request))
    if open_folder(folder):
        return message("Opened the invoices folder.", "info")
    return message(f"Open this folder yourself: {folder}", "info")


# --- Emails set aside by triage ------------------------------------------------------------------------------------


@router.get("/process/inbox/{entry_id}/file/{n}")
def inbox_file(request: Request, entry_id: str, n: int) -> FileResponse:
    """An attachment of an email set aside (``n`` = -1: the email itself), to open."""
    from fastapi import HTTPException

    path = inbox.file_of(invoice_dir(get_store(request)), entry_id, n)
    if path is None:
        raise HTTPException(status_code=404, detail="That file is no longer in the invoices folder.")
    return FileResponse(path, filename=path.name)


@router.post("/process/inbox/{entry_id}/to-queue/{n}")
def inbox_to_queue(request: Request, entry_id: str, n: int) -> JSONResponse:
    store = get_store(request)
    try:
        moved = inbox.to_queue(store, invoice_dir(store), entry_id, n, reviewer())
    except ValueError as exc:  # a spreadsheet: never read from the invoices folder
        return refuse(str(exc))
    if moved is None:
        return refuse("That file is no longer where AP Coder put it.")
    text = f"{moved.name} is in the invoices folder: process the folder to read it into the review queue."
    return done(request, "/process", flash_message(text, "ok", icon="move_to_inbox"))


@router.post("/process/inbox/{entry_id}/done")
def inbox_done(request: Request, entry_id: str) -> JSONResponse:
    store = get_store(request)
    if not inbox.mark_done(store, invoice_dir(store), entry_id, reviewer()):
        return refuse("That email is no longer in the list.")
    return done(request, "/process", flash_message("Marked as dealt with.", "ok", icon="done_all"))


# --- Demo invoices -------------------------------------------------------------------------------------------------


@router.post("/demo/load")
def demo_load(request: Request, back: str = Form("/process")) -> JSONResponse:
    from ap_coder.ops.demo import demo_available, load_demo

    if not demo_available():
        return refuse("The demo invoices are not in this copy of AP Coder.")
    result = load_demo(get_store(request), get_settings(request))
    return done(request, local_url(back, "/process"),
                flash_message(f"Demo ready: {result['to_review']} invoices to review, {result['approved']} already "
                              "approved.", "ok", icon="science"))  # fmt: skip


@router.post("/demo/remove")
def demo_remove(request: Request, back: str = Form("/process"), sure: str = Form("")) -> JSONResponse:
    from ap_coder.ops.demo import remove_demo

    if not sure:
        return refuse("Tick “Yes, remove the demo invoices” first.")
    removed = remove_demo(get_store(request))
    return done(request, local_url(back, "/process"),
                flash_message(f"Removed {ui.plural(removed, 'demo invoice')}.", "ok", icon="delete_sweep"))  # fmt: skip
