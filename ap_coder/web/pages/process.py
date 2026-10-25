"""Import invoices: the setup checklist, uploading invoices, picking up new files in the invoices folder.

Reading and coding runs as the background job "process" (``ap_coder/web/jobs.py``): the page uploads the files,
then polls ``GET /process/status`` and shows each invoice as it is done. The demo invoices (load / remove) are here
too; the review queue's welcome card uses the same routes.
"""

from __future__ import annotations

import collections
import contextvars
import datetime as dt
import hashlib
import logging
import os
import re
import threading
import time
from collections.abc import Callable
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from ap_coder.ai import page_reader
from ap_coder.ai.extraction import SUPPORTED_EXTENSIONS, TEXT_EXTENSIONS
from ap_coder.ai.local_llm import provider_status
from ap_coder.ai.pipeline import InvoicePipeline, PipelineResult, invoice_files, other_files
from ap_coder.capture.layout import ocr_available
from ap_coder.coding.tax import PROVINCE_NAMES, TAX_TYPES
from ap_coder.core import paths, ui
from ap_coder.core.config import Settings
from ap_coder.core.reference_data import ReferenceData
from ap_coder.core.store import REVIEW, Store
from ap_coder.intake import inbox
from ap_coder.intake.mailbox import (
    EMAIL_EXTENSIONS,
    MAX_EMAIL_BYTES,
    Unpacked,
    emails_in,
    safe_name,
    unpack,
    unpack_folder,
)
from ap_coder.intake.pictures import looks_incomplete
from ap_coder.ops.help import SERVER_LM_STUDIO
from ap_coder.web import identity
from ap_coder.web.access import is_admin
from ap_coder.web.core import (
    cache_dir,
    can,
    done,
    flash_message,
    folder_on_the_server,
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
    server_mode,
    short_path,
)
from ap_coder.web.identity import person
from ap_coder.web.jobs import Job
from ap_coder.web.pages.review import attention

router = APIRouter()

JOB = "process"
STILL_SAVING_SECONDS = 60  # a file changed this recently whose end is missing is still being written
ACCEPT = sorted(SUPPORTED_EXTENSIONS | EMAIL_EXTENSIONS)  # what the upload takes: PDFs, scans, photos, saved emails
# The largest invoice uploaded (B-981): as a spreadsheet (web/files.py); a saved email up to what the mailbox reads.
MAX_UPLOAD_BYTES = 50 * 1024 * 1024


# --- Saving uploads -----------------------------------------------------------------------------------------------


def safe_file_name(name: str) -> str:
    """Just the file name of an upload (no folders, Windows or POSIX), never empty or a dot name, and short
    enough for Windows paths (80 characters, the extension kept)."""
    base = PureWindowsPath(PurePosixPath(name or "").name).name.strip().lstrip(".")
    return safe_name(base) if base else "invoice"


# An amount, as a text invoice prints one: with cents (12.50, 1 204,00), after a currency sign ($105), or after a word
# that names one (Total 1,250; GST: 5).
_AMOUNT = re.compile(
    r"\d[\d,. \u00a0]*[.,]\d{2}\b"
    r"|[$€£]\s*\d"
    r"|\b(?:total|sub-?total|amount|balance|due|tax|gst|hst|pst|qst|tps|tvq|tvh|montant)\b[^\n\d]{0,20}\d",
    re.IGNORECASE,
)


def text_of(content: bytes) -> str:
    """The text of an uploaded text file, decoded as ``extraction.result_from_text`` reads it: Notepad's "Unicode"
    (UTF-16, by its byte-order mark), UTF-8 (with or without a mark), else Windows' "ANSI" (cp1252)."""
    if content.startswith((b"\xff\xfe", b"\xfe\xff")):
        return content.decode("utf-16", errors="replace")
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError:
        return content.decode("cp1252", errors="replace")


def looks_like_an_invoice_text(content: bytes) -> bool:
    """An uploaded text file (.txt, .md) is read as an invoice only when it prints an amount: a notes file uploaded
    by mistake ("hello") became an invoice from "hello" (B-439). Upload only: a text file copied into the invoices
    folder is read as before (a person put it there as an invoice)."""
    return bool(_AMOUNT.search(text_of(content)))


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


def email_line(mail: Unpacked, already: list[Path] | None = None) -> tuple[str, str]:
    """(text, tone) about a saved email: what it was, where its attachments went, what was left out and why.
    ``already``: the files of this run skipped as already in AP Coder (not sent to the review queue)."""
    if mail.error:
        return (f"{mail.email} {mail.error}. It was moved to the emails/could not read subfolder; ask the sender to "
                "send the invoice again."), "err"  # fmt: skip
    t = mail.triage
    went = []
    seen = [p for p in mail.saved if p in (already or [])]
    if len(mail.saved) > len(seen):
        went.append(f"{ui.plural(len(mail.saved) - len(seen), 'invoice')} to the review queue")
    if seen:
        went.append(f"{ui.plural(len(seen), 'attachment')} already in AP Coder")
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


LM_STUDIO = "LM Studio running"
TAX_STEP = "Sales tax accounts"  # the GL page's tab of the same name (B-530)
START_LM_STUDIO = "not running: open LM Studio, Developer tab → Start server, then Check again"
LM_STUDIO_STEPS = (  # what to do while LM Studio isn't running (docs/QUICK_START.md, step 0)
    "Open LM Studio (IT installs it with OvisOCR2 and the chat model already in it).",
    "In LM Studio: Developer tab → Start server.",
    "Press Check again. Leave LM Studio open while you work.",
)
NO_OVISOCR2 = "running, but OvisOCR2 isn't in it: ask IT for its two files (Settings → Reading)"
NO_CHAT_MODEL = "running, OvisOCR2 ready, but no chat model loaded: ask IT to load it (Settings → Reading)"
# What the welcome page's first card says to do, for each way LM Studio isn't ready yet (B-529).
LM_STUDIO_HELP = {
    START_LM_STUDIO: ("Start LM Studio", "It runs OvisOCR2, which reads every page, and the chat model, which checks "
                      "each invoice and suggests GL accounts for new lines. Without it every invoice waits for you.",
                      LM_STUDIO_STEPS),
    NO_OVISOCR2: ("Add OvisOCR2 to LM Studio", "LM Studio runs, but OvisOCR2, which reads every page, isn't in it.",
                  ("Ask IT for OvisOCR2's two files and where they go (Settings → Reading shows the folder).",
                   "Press Check again: AP Coder finds it, and its self-test starts by itself.")),
    NO_CHAT_MODEL: ("Load the chat model in LM Studio", "LM Studio and OvisOCR2 run, but no chat model is loaded: it "
                    "checks each invoice and suggests GL accounts for new lines.",
                    ("In LM Studio, load the chat model IT put there (Gemma 4 12B or Qwen 3.5 9B), or ask IT.",
                     "Press Check again.")),
}  # fmt: skip


def lm_studio_step(store: Store, settings: Settings) -> tuple[str, str, str]:
    """The first step (the Quick start's step 0): LM Studio runs, with OvisOCR2 (it reads every page) and the chat
    model (the AI reviewer and GL suggestions for new lines) in it. Without it AP Coder still works, but every
    invoice waits for a person."""
    status = reading_status_or_none(settings, store)
    if status is None:
        return "todo", LM_STUDIO, "couldn't be checked just now: press Check again"
    if not status.lm_studio:
        return "todo", LM_STUDIO, START_LM_STUDIO
    if status.self_test in ("pending", "running"):
        return "wait", LM_STUDIO, "OvisOCR2 is testing itself (10 to 20 min the first time); use AP Coder meanwhile"
    if status.self_test == "failed":
        return "bad", LM_STUDIO, "running, but OvisOCR2 failed its self-test (Settings → Reading)"
    if not status.page_reader_ready:
        return "todo", LM_STUDIO, NO_OVISOCR2
    ai = provider_status(settings)
    if not ai.ready:  # the pilot runs with the chat model (the AI reviewer): not done without it (B-529)
        return "todo", LM_STUDIO, NO_CHAT_MODEL
    return "ok", LM_STUDIO, f"OvisOCR2 ready · chat model: {ai.label.split(' · ')[-1]}"


def lm_studio_help(step: tuple[str, str, str]) -> dict[str, Any] | None:
    """What to do about the LM Studio step as it is (None when it is done or only waiting for the self-test)."""
    state, _, detail = step
    if state not in ("todo", "bad"):
        return None
    title, text, steps = LM_STUDIO_HELP.get(detail, ("Check LM Studio", detail[:1].upper() + detail[1:] + ".",
                                                     ("Open Settings → Reading: it says what is missing.",
                                                      "Press Check again.")))  # fmt: skip
    return {
        "title": title,
        "icon": "memory",
        "href": STEP_LINKS[LM_STUDIO],
        "button": "Settings → Reading",
        "text": text,
        "steps": steps,
        "check": True,
    }


def setup_steps(store: Store, settings: Settings) -> list[tuple[str, str, str]]:
    """(state, label, detail) of what AP Coder needs before it reads invoices; state ok, todo, wait or bad. Every row
    counts: the checklist's counter is the rows done out of the rows shown (B-503)."""
    mapped = tax_types_mapped(store)
    gl_count = len(store.list_accounts("gl_accounts"))
    if ocr_available():
        reader = ("ok", "Reading invoices", "PDF text + OCR")
    else:
        reader = ("todo", "Reading invoices", "text PDFs only: scans need OCR (double-click APProcessor again)")
    return [  # in the order of docs/QUICK_START.md and Help's Getting started (B-514)
        lm_studio_step(store, settings),
        reader,
        name_step(),
        province_step(store),
        ("ok" if gl_count else "todo", "GL accounts", f"{gl_count} imported" if gl_count else "none yet: import them"),
        ("ok" if mapped == len(TAX_TYPES) else "todo", TAX_STEP, f"{mapped} of {len(TAX_TYPES)} set"),
        bank_step(store),
        *pilot_steps(store),
    ]


VENDOR_STEP, JDE_STEP, HISTORY_STEP = "Vendor master", "JD Edwards settings", "Past coding from your ERP"
HISTORY_WHY = "AP Coder learns your coding from what you posted before"


def pilot_steps(store: Store) -> list[tuple[str, str, str]]:
    """The vendor master, the JD Edwards settings (when JD Edwards is the export) and the ERP's past coding, in the
    order the shadow pilot needs them after the GL accounts (D-92): each supplier's address number (AN8) comes from
    the vendor master, the settings map the accounts, and the past coding warm-starts the coding of the first
    invoices."""
    from ap_coder.erp import jde
    from ap_coder.web.core import jde_shown

    shown = jde_shown(store)
    vendors = store.master_vendors()
    if not vendors:
        what = "the ERP's vendor list" + (", with each supplier's address number (AN8)" if shown else "")
        vendor = ("todo", VENDOR_STEP, f"on Vendors: import {what}")
    elif shown:
        # its ERP ID, else the number set in Settings → JD Edwards → Supplier address numbers (B-1002)
        overrides = jde.load(store).an8_overrides
        an8 = sum(1 for v in vendors if str(v.get("erp_id") or "").strip().isdigit()
                  or str(overrides.get(v.get("vendor_key") or "") or "").strip().isdigit())  # fmt: skip
        vendor = (
            ("ok" if an8 else "todo"),
            VENDOR_STEP,
            f"{ui.plural(len(vendors), 'vendor')}, {an8} with an address number (AN8)"
            if an8
            else f"{ui.plural(len(vendors), 'vendor')}, but no address number (AN8): import it as the ERP ID, or set "
            "it in Settings → JD Edwards → Supplier address numbers",
        )
    else:
        vendor = ("ok", VENDOR_STEP, ui.plural(len(vendors), "vendor"))
    steps = [vendor]
    if shown:
        saved = bool(store.get_setting(jde.SETTING_KEY))
        problems = jde.settings_problems(jde.load(store)) if saved else []
        if saved and not problems:
            settings = jde.load(store)
            steps.append(("ok", JDE_STEP, f"company {settings.company_code}, "
                          f"{ui.plural(len(settings.gl_map), 'account')} mapped"))  # fmt: skip
        else:
            steps.append(("todo", JDE_STEP, problems[0] if problems else
                          "company, document type, accounts and tax areas: Settings → JD Edwards E1"))  # fmt: skip
    taught = store.history_count()
    steps.append(("ok", HISTORY_STEP, ui.plural(taught, "past line") + " taught") if taught
                 else ("todo", HISTORY_STEP, HISTORY_WHY))  # fmt: skip
    return steps


BANK_STEP = "Your bank accounts (optional)"


def bank_step(store: Store) -> tuple[str, str, str]:
    """Our bank accounts (GL accounts & tax → Bank accounts, D-66): optional, so the review screen can say which
    account pays each invoice. Counted like every row (B-503)."""
    n = len(store.bank_accounts(active_only=True))
    if n:
        return "ok", BANK_STEP, ui.plural(n, "account")
    return "todo", BANK_STEP, "to choose which account pays each invoice"


def step_state(steps: list[tuple[str, str, str]], label: str) -> str:
    """The state of the step called ``label`` ("" when there is none)."""
    return next((s for s, name, _ in steps if name == label), "")


def province_step(store: Store) -> tuple[str, str, str]:
    """Where invoices are delivered to you (Settings → Review): the sales tax check's province when an invoice prints
    no ship-to. A step like the others (counted since B-503): without it such an invoice says "Province of supply
    unknown" and its tax goes unchecked."""
    code = store.our_province()
    if code:
        return "ok", "Your province", PROVINCE_NAMES.get(code, code)
    return "todo", "Your province", "Settings → Review: the sales tax check uses it when an invoice prints no ship-to"


def name_step() -> tuple[str, str, str]:
    """Whether the person approving gave their name: each approval records it, and without one it is the computer
    login ("root", "jsmith"). Given in Settings → Review (or AP_REVIEWER), or by a colleague on the "Who are you?"
    page."""
    colleague = identity.person()
    name = colleague.name if colleague is not None else ""
    name = name or str(paths.read_user_settings().get("reviewer") or "") or os.environ.get("AP_REVIEWER", "")
    if name.strip():
        return "ok", "Your name", name.strip()
    return "todo", "Your name", "Settings → Review: each approval is recorded under it"


def first_steps(store: Store, settings: Settings, has_invoices: bool) -> tuple[list[tuple[str, str, str]], int, int]:
    """(steps, done, needed) of getting started, the same on Import invoices' Setup card and the review queue's
    welcome page: ``setup_steps``, then processing the first invoices. Every row shown is counted (B-503)."""
    steps = setup_steps(store, settings)
    steps.append(("ok" if has_invoices else "todo", "Process your first invoices", "done" if has_invoices else "to do"))
    return steps, sum(1 for s, _, _ in steps if s == "ok"), len(steps)


# Where each getting-started step is done: its row on the welcome page and Import invoices links there until it is.
STEP_LINKS = {
    LM_STUDIO: "/settings/reading",
    "Reading invoices": "/settings/reading",
    "GL accounts": "/accounts",
    TAX_STEP: "/accounts?tab=tax",
    BANK_STEP: "/accounts?tab=bank",
    "Your name": "/settings/review",
    "Your province": "/settings/review",
    VENDOR_STEP: "/vendors?setup=1#import",
    JDE_STEP: "/settings/jde",
    HISTORY_STEP: "/learning?teach=1#history",
    "Process your first invoices": "/process",
}


def next_step(steps: list[tuple[str, str, str]]) -> dict[str, Any] | None:
    """The one thing to do next on a new laptop, as the welcome page and Import invoices show it: the name (each
    approval is recorded under it), the GL accounts (or the sample ones, to look around first), the sales tax
    accounts, then the first invoices. None once all four are done (LM Studio is the checklist's first row, with
    Check again, and has its own banner)."""
    state = {label: (s, detail) for s, label, detail in steps}
    lm = next((s for s in steps if s[1] == LM_STUDIO), None)
    if lm is not None and (help_ := lm_studio_help(lm)):  # step 0: LM Studio, with Check again (B-529)
        return help_
    if state["Your name"][0] != "ok":
        return {"title": "Add your name", "icon": "badge", "href": STEP_LINKS["Your name"], "button": "Add your name",
                "text": "Each approval is recorded under it. Until you add it, approvals carry this computer's "
                        "login."}  # fmt: skip
    if state["GL accounts"][0] != "ok":
        return {"title": "Import your GL accounts", "icon": "account_tree", "href": STEP_LINKS["GL accounts"],
                "button": "Import GL accounts", "sample": True,
                "text": "AP Coder codes each invoice line to one of your GL accounts. To look around first, use the "
                        "sample accounts and switch to yours later."}  # fmt: skip
    if state[TAX_STEP][0] != "ok":
        return {"title": "Set up the sales tax accounts", "icon": "percent", "button": "Sales tax accounts",
                "href": STEP_LINKS[TAX_STEP],
                "text": "GST, HST and QST each need a GL account (PST is added to each expense line, so it needs "
                        "none), so approved invoices can be posted. Then Save tax setup."}  # fmt: skip
    if JDE_STEP in state:  # the shadow pilot with JD Edwards: the vendor master, its settings, the past coding (D-92)
        if state[VENDOR_STEP][0] != "ok":
            return {"title": "Import your vendor master", "icon": "storefront", "href": STEP_LINKS[VENDOR_STEP],
                    "button": "Import the vendor master",
                    "text": "The ERP's vendor list, with each supplier's JD Edwards address number (AN8) as its "
                            "ERP ID: each voucher needs it, and an invoice from a vendor not in the list is "
                            "flagged."}  # fmt: skip
        if state[JDE_STEP][0] != "ok":
            return {"title": "Fill in the JD Edwards settings", "icon": "settings", "href": STEP_LINKS[JDE_STEP],
                    "button": "JD Edwards settings",
                    "text": "Company, document type, the GL account → BU.Object.Subsidiary mapping and the tax areas, "
                            "or download the worksheet for your JDE team and import their answers."}  # fmt: skip
        if state[HISTORY_STEP][0] != "ok":
            return {"title": "Teach AP Coder your past coding", "icon": "history_edu",
                    "href": STEP_LINKS[HISTORY_STEP], "button": "Teach from past coding",
                    "text": f"{HISTORY_WHY}: import last year's posted AP invoice lines from JD Edwards (vendor, "
                            "line description, GL account, cost center), so the first invoices are coded "
                            "already."}  # fmt: skip
    if state["Process your first invoices"][0] != "ok":
        return {"title": "Import your first invoices", "icon": "upload_file", "button": "Import invoices",
                "href": STEP_LINKS["Process your first invoices"],
                "text": "Upload a few PDFs, scans or photos: each is read, coded and checked, then waits for you in "
                        "the review queue."}  # fmt: skip
    return None


def shown_setup(request: Request, steps: list[tuple[str, str, str]], coming: dict[str, Any] | None) -> tuple[
        list[tuple[str, str, str]], dict[str, Any] | None, dict[str, str]]:  # fmt: skip
    """The checklist, its Next step and the steps' links as the person sees them: on a laptop as they are; on the
    company server (D-102) LM Studio is IT's or an Admin's (it runs on the server), a step whose page the role can't
    open has no link, and a Next step the role can't do says who does it."""
    links = {label: href for label, href in STEP_LINKS.items() if can(request, href, "GET")}
    if not server_mode(request):
        return steps, coming, links
    steps = [(state, label, f"not ready: {SERVER_LM_STUDIO}" if label == LM_STUDIO and state in ("todo", "bad")
              else detail) for state, label, detail in steps]  # fmt: skip
    if coming and coming.get("check"):  # the LM Studio card
        coming = {**coming, "title": "LM Studio isn't ready on the server", "steps": (),
                  "text": f"{SERVER_LM_STUDIO}. Until it is ready, every invoice waits for a person."}  # fmt: skip
    elif coming and not can(request, coming["href"], "GET"):
        coming = {**coming, "text": f"{coming['text']} An Admin does this step."}
    return steps, coming, links


def ready_to_read(steps: list[tuple[str, str, str]]) -> bool:
    """PDF text / OCR and GL accounts are enough: OvisOCR2 reads invoices once it runs, the chat model is optional."""
    return step_state(steps, "GL accounts") == "ok"


def queued_note(settings: Settings, store: Store, queued: int) -> str:
    """After processing: when OvisOCR2 double-checks the invoices just queued for it ("" when none were). They are
    ready to review now: nothing waits for it (F1: "they wait for it" read as if they could not be reviewed)."""
    if queued <= 0:
        return ""
    them = "it" if queued == 1 else "them"
    status = reading_status_or_none(settings, store)
    if status is not None and not status.page_reader_ready:
        test = getattr(status, "self_test", "")
        if getattr(status, "page_reader_model", "") and test in ("failed", "pending", "running"):
            why = "it failed its self-test" if test == "failed" else "its self-test hasn't passed yet"
            return (f"OvisOCR2 runs in LM Studio but isn't trusted yet ({why}): it will double-check {them} once it "
                    "passes (Settings → Reading).")  # fmt: skip
        if getattr(status, "lm_studio", False):
            return f"OvisOCR2 will double-check {them} once it is loaded in LM Studio (Settings → Reading)."
        return f"OvisOCR2 will double-check {them} when LM Studio runs (Settings → Reading)."
    eta = ""
    if status is not None and status.eta_minutes:
        eta = f" ({page_reader.duration(status.eta_minutes * 60)} for the whole queue)"
    return f"OvisOCR2 double-checks {them} in the background{eta}."


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

    actor = reviewer()  # who started it (the job's thread has no request to say)

    def work(job: Job) -> dict[str, Any]:
        todo, skipped, mails = list(paths), list(already or []), list(emails or [])
        if folder_emails:
            unpacked = unpack_folder(invoice_dir(store))
            mails += unpacked
            new, dup = split_new(store, [p for m in unpacked for p in m.saved])
            todo += [p for p in new if p not in todo]
            skipped += dup
        for mail in mails:
            text, tone = email_line(mail, skipped)
            job.log(text, tone, "gpp_maybe" if tone == "err" and mail.triage else "mail")
            inbox.log_sorted(store, mail)
        job.step(0, len(todo), "Starting")
        ok, errors = 0, []
        started, clock = dt.datetime.now().isoformat(timespec="seconds"), time.monotonic()
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
                errors.append(f"{path.name}: {result.error}")
        failed = len(todo) - ok
        if todo or skipped:  # the run in the audit trail (the finance hub's workflow_runs, D-103)
            store.record_import_run("dashboard", files=len(todo), ok=ok, failed=failed, skipped=len(skipped),
                                    started_at=started, seconds=time.monotonic() - clock, errors=errors,
                                    actor=actor)  # fmt: skip
        queued = max(0, store.page_reads_waiting() - waiting)
        messages = list(notes or [])
        if ok:
            note = queued_note(settings, store, queued)
            ready = "It's" if ok == 1 else "They're"  # (B-430)
            messages.append(flash_message(f"{ui.plural(ok, 'invoice')} read and coded. {ready} ready to review in the "
                                          f"review queue. {note}".strip(), "ok", icon="inbox"))  # fmt: skip
        if failed:
            messages.append(flash_message(f"{ui.plural(failed, 'file')} could not be processed. See Review queue → "
                                          "Failed / rejected.", "err"))  # fmt: skip
        if skipped:
            messages.append(flash_message(f"Skipped {ui.plural(len(skipped), 'file')} already in AP Coder.", "info",
                                          [p.name for p in skipped], "content_copy"))  # fmt: skip
        if not todo and not skipped and not mails:
            messages.append(flash_message("Nothing new to read.", "info"))
        label = f"Processed {ok} of {len(todo) + len(skipped)}" if todo else "Nothing new to read"
        if todo and skipped:  # every file of the run counted, the ones not read said (B-1184)
            label += f": {len(skipped)} already in AP Coder"
        return {"processed": len(todo), "ok": ok, "failed": failed, "queued": queued, "messages": messages,
                "label": label}  # fmt: skip

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
    steps, coming, links = shown_setup(request, steps, next_step(steps))
    demo_count = store.demo_count()
    return render(
        request, "process.html", "process",
        # The demo still loaded and none of your own invoices yet: asked to remove it first, so its reviewer, lessons
        # and vendors don't mix into your numbers (B-468).
        demo_first=bool(demo_count) and demo_count >= len(invoices),
        steps=steps,
        step_links=links,
        steps_done=steps_done,
        steps_needed=steps_needed,
        next=coming if coming and coming["href"] != "/process" else None,  # (this page's own step: choose files)
        first_invoices=bool(coming) and coming["href"] == "/process",
        ready=ready_to_read(steps),
        recent=recent,
        recent_waiting=any(i["status"] == REVIEW for i in recent),
        files=folder_files(store),
        not_read=not_read_here(invoice_dir(store)),
        set_aside=inbox.needs_person(store, invoice_dir(store)),
        folder=short_path(invoice_dir(store)) if is_admin(request) else "",
        accept=",".join(ACCEPT),
        accept_text=", ".join(e.lstrip(".").upper() for e in ACCEPT),
        job=jobs(request).get(JOB).snapshot(),
        waiting=import_queue(request).files(),
        waiting_text=waiting_text(import_queue(request).files()),
        demo_available=demo_available(),
        demo_count=demo_count,
        where="/process",
    )  # fmt: skip


# --- Imports queued behind the one running (D-99) -------------------------------------------------------------------
# One import reads at a time. An upload that comes while one runs (a colleague's, on the server) is saved to the
# invoices folder at once and waits here; when the running import finishes, the next one waiting starts, as the person
# who uploaded it (their context). In this process only: after a restart its files are in the folder, listed as new.


class ImportQueue:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.waiting: collections.deque[tuple[contextvars.Context, Callable[[], tuple[Any, int]], int]] = (
            collections.deque()
        )

    def files(self) -> int:
        """How many files wait behind the import in progress."""
        return sum(count for _, _, count in self.waiting)


_queue_lock = threading.Lock()


def import_queue(request: Request) -> ImportQueue:
    state = request.app.state
    with _queue_lock:
        if getattr(state, "import_queue", None) is None:
            state.import_queue = ImportQueue()
        return state.import_queue


def waiting_text(files: int) -> str:
    return f"Waiting: {ui.plural(files, 'file')} queued behind the import in progress."


def _next_import(queue: ImportQueue, job: Job) -> None:
    """After an import: start the next one waiting (which of its files are new is decided now, so one read
    meanwhile is skipped)."""
    with queue.lock:
        _start_next(queue, job)


def _start_next(queue: ImportQueue, job: Job) -> None:
    """Start the first import waiting, if the job is free (the caller holds ``queue.lock``)."""
    while queue.waiting and not job.running:
        context, build, _ = queue.waiting[0]
        try:
            started = context.run(_begin, queue, job, build)
        except Exception:  # noqa: BLE001 - one batch that cannot start never holds up the rest
            logging.getLogger(__name__).exception("A queued import could not start")
            queue.waiting.popleft()
            continue
        if started:
            queue.waiting.popleft()
        return  # (not started: another import began meanwhile; this one waits for it)


def _begin(queue: ImportQueue, job: Job, build: Callable[[], tuple[Any, int]]) -> bool:
    work, total = build()
    return job.start(work, total=total, then=lambda: _next_import(queue, job))


def start_import(request: Request, work: Any, total: int) -> bool:
    """Start reading (``work``) now, False while an import runs; the imports queued meanwhile follow it."""
    queue, job = import_queue(request), jobs(request).get(JOB)
    return job.start(work, total=total, then=lambda: _next_import(queue, job))


def _start(request: Request, work: Any, total: int) -> JSONResponse:
    if not start_import(request, work, total):
        return refuse("AP Coder is already reading invoices. Wait for it to finish, then try again.", 409)
    return JSONResponse({"ok": True, "job": JOB, "status": "/process/status"})


def _start_or_queue(request: Request, build: Callable[[], tuple[Any, int]], files: int) -> JSONResponse:
    """Start the import ``build`` makes (``(work, total)``), or queue it behind the one running."""
    queue, job = import_queue(request), jobs(request).get(JOB)
    started = {"ok": True, "job": JOB, "status": "/process/status"}
    with queue.lock:
        if not job.running and not queue.waiting:
            work, total = build()
            if job.start(work, total=total, then=lambda: _next_import(queue, job)):
                return JSONResponse(started)
        batch = (contextvars.copy_context(), build, files)
        queue.waiting.append(batch)
        _start_next(queue, job)  # (an import that ended without starting the next: never left waiting for nothing)
        if not any(b is batch for b in queue.waiting):
            return JSONResponse(started)
        waiting = queue.files()
    return JSONResponse({**started, "queued": waiting, "toast": flash_message(waiting_text(waiting), "info")})


@router.post("/process/upload")
async def process_upload(request: Request, files: list[UploadFile] = File(...)) -> JSONResponse:  # noqa: B008
    """Save the uploaded files to the invoices folder, then read and code the new ones in the background."""
    received = []
    for upload in files:
        name = upload.filename or "invoice"
        limit = MAX_EMAIL_BYTES if Path(name).suffix.lower() in EMAIL_EXTENSIONS else MAX_UPLOAD_BYTES
        content = await upload.read(limit + 1)  # never more than the limit read: the rest stays in the spool file
        received.append((name, content if len(content) <= limit else None))
    return await run_in_threadpool(_save_and_start, request, received)


def _save_and_start(request: Request, received: list[tuple[str, bytes | None]]) -> JSONResponse:
    store, settings = get_store(request), get_settings(request)
    if not ready_to_read(setup_steps(store, settings)):
        return refuse("Finish the setup first: import your GL accounts (GL accounts & tax).")
    folder = invoice_dir(store)
    saved, mails, notes = [], [], []
    for name, content in received:
        if content is None:  # over the limit (B-981): an invoice is never this large
            email = Path(name).suffix.lower() in EMAIL_EXTENSIONS
            notes.append(flash_message(f"{name} was not read: it is larger than {'100' if email else '50'} MB. An "
                                       "invoice is far smaller: scan it again at 300 dpi, or save it as a PDF.",
                                       "warn"))  # fmt: skip
            continue
        if Path(name).suffix.lower() not in ACCEPT:
            notes.append(flash_message(f"{name} was not read: AP Coder reads PDFs, pictures (JPEG, PNG, HEIC, TIFF, "
                                       "BMP, WEBP, GIF), saved emails (.eml, .msg) and invoices saved as text (.txt). "
                                       "Save it as PDF or JPEG to have it read.", "warn"))  # fmt: skip
            continue
        if Path(name).suffix.lower() in TEXT_EXTENSIONS and not looks_like_an_invoice_text(content):
            notes.append(flash_message(f"{name} was not read: a text file (.txt, .md) is read as an invoice only "
                                       "when it has amounts in it (like 125.00 or Total $105), and this one has none.",
                                       "warn"))  # fmt: skip
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

    def build() -> tuple[Any, int]:  # when it starts: a file read by the import before it is then skipped
        todo, already = split_new(store, saved)
        return process_files(store, settings, todo, already=already, emails=mails, notes=notes), len(todo)

    return _start_or_queue(request, build, len(saved))


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
    snap = {**jobs(request).get(JOB).snapshot(), "waiting": import_queue(request).files()}
    return JSONResponse(snap, headers={"Cache-Control": "no-store"})


@router.post("/process/open-folder")
def process_open_folder(request: Request) -> JSONResponse:
    folder = invoice_dir(get_store(request))
    if server_mode(request):
        return folder_on_the_server(request, folder)
    if person() is not None:  # a colleague's click must not open a window on the computer running AP Coder
        return message("The invoices folder is on the computer running AP Coder: open it there.", "info")
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
