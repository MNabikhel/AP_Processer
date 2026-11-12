"""Import invoices: the setup checklist, uploading invoices, picking up new files in the invoices folder.

Each upload and each pick-up puts its files in the import queue (``core/store/imports.py``, D-168), which keeps them
over a restart; they are read in turn, fairly between the people who brought them. On the company server its capture
processes read them (``web/server.py``), never a web worker; on a laptop the dashboard's own job "process"
(``ap_coder/web/jobs.py``) does, as before. The page polls ``GET /process/status`` (the queue's latest run) and shows
each invoice as it is done. The demo invoices (load / remove) are here too; the review queue's welcome card uses the
same routes.

The new files in the invoices folder: on a laptop each view lists the folder (``folder_files``); on the company server
the folder is listed once for every process and kept in the database, each view showing that (``shared_files``,
D-176).
"""

from __future__ import annotations

import collections
import contextlib
import datetime as dt
import json
import logging
import os
import re
import secrets
import sqlite3
import threading
import time
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from fastapi import File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from ap_coder.ai import page_reader
from ap_coder.ai.extraction import SUPPORTED_EXTENSIONS, TEXT_EXTENSIONS
from ap_coder.ai.local_llm import provider_status
from ap_coder.ai.pipeline import PipelineResult
from ap_coder.capture.layout import ocr_available
from ap_coder.coding.tax import PROVINCE_NAMES, TAX_TYPES
from ap_coder.core import paths, ui
from ap_coder.core.config import Settings
from ap_coder.core.reference_data import ReferenceData
from ap_coder.core.store import EVENT_ACTOR, EVENT_ORIGIN, REVIEW, Store
from ap_coder.core.store.folder import read_file
from ap_coder.intake import inbox, joined
from ap_coder.intake.imports import NO_ROOM, RESTORING, Reader, has_room, list_folder, noted, owner_id, pick_up
from ap_coder.intake.mailbox import (
    EMAIL_EXTENSIONS,
    MAX_EMAIL_BYTES,
    Unpacked,
    safe_name,
    unpack,
    unpack_folder,
    wait_for_pickup,
)
from ap_coder.intake.pictures import looks_incomplete
from ap_coder.ops.help import SERVER_LM_STUDIO
from ap_coder.web import identity
from ap_coder.web.access import is_admin
from ap_coder.web.core import (
    cache_dir,
    can,
    data_dir,
    done,
    flash_message,
    get_settings,
    get_store,
    invoice_dir,
    local_url,
    open_folder_here,
    page_router,
    reading_status_or_none,
    reference_or_none,
    refuse,
    render,
    reviewer,
    server_mode,
    short_path,
)
from ap_coder.web.identity import person
from ap_coder.web.jobs import MAX_LINES, Job
from ap_coder.web.pages.review import attention

router = page_router()  # each plain handler: one connection a request

JOB = "process"
STILL_SAVING_SECONDS = 60  # a file changed this recently whose end is missing is still being written
ACCEPT = sorted(SUPPORTED_EXTENSIONS | EMAIL_EXTENSIONS)  # what the upload takes: PDFs, scans, photos, saved emails
# The largest invoice uploaded (B-981): as a spreadsheet (web/files.py); a saved email up to what the mailbox reads.
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
# On the server, one upload (D-109): at most this many files and bytes, and each account at most this much an hour
# (a person drops a day's invoices at once; a script filling the server's disk is stopped).
MAX_REQUEST_FILES = 200
MAX_REQUEST_BYTES = 250 * 1024 * 1024
UPLOAD_WINDOW = 3600.0
MAX_BYTES_PER_WINDOW = 2 * 1024 * 1024 * 1024
MAX_UPLOADS_PER_WINDOW = 120
_uploads: dict[Any, collections.deque] = collections.defaultdict(collections.deque)  # account → (time, bytes)
_uploads_lock = threading.Lock()


def upload_refusal(user: Any, count: int, size: int, now: float | None = None,
                   store: Store | None = None) -> tuple[str, int]:  # fmt: skip
    """Why the server takes no more uploads from ``user`` now, with the status (("", 0) = it does), counting this one
    when taken. ``store``: counted in the database, as every web worker sees it (D-111); else in this process."""
    if count > MAX_REQUEST_FILES:
        return (f"That is {count} files at once: AP Coder on the server takes up to {MAX_REQUEST_FILES} per upload. "
                "Send them in a few goes.", 413)  # fmt: skip
    if size > MAX_REQUEST_BYTES:
        return (f"Those files are {size / 1e6:,.0f} MB together: AP Coder on the server takes up to "
                f"{MAX_REQUEST_BYTES // (1024 * 1024)} MB per upload. Send them in a few goes.", 413)  # fmt: skip
    busy = ("You have uploaded a lot in the last hour: AP Coder on the server takes more in a while. If this is a real "
            "batch, ask an Admin to put the files in the invoices folder.", 429)  # fmt: skip
    if store is not None:
        most = (MAX_UPLOADS_PER_WINDOW, MAX_BYTES_PER_WINDOW)
        return ("", 0) if store.take_upload_allowance(str(user), size, UPLOAD_WINDOW, *most) else busy
    now = time.monotonic() if now is None else now
    with _uploads_lock:
        recent = _uploads[user]
        while recent and recent[0][0] <= now - UPLOAD_WINDOW:
            recent.popleft()
        if len(recent) >= MAX_UPLOADS_PER_WINDOW or sum(b for _, b in recent) + size > MAX_BYTES_PER_WINDOW:
            return busy
        recent.append((now, size))
    return "", 0


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


def email_line(mail: Unpacked, already: int = 0, repeated: int = 0) -> tuple[str, str]:
    """(text, tone) about a saved email: what it was, where its attachments went, what was left out and why.
    ``already``: how many of its files were skipped as already in AP Coder, ``repeated``: as the same as another file
    of this run (neither sent to the review queue)."""
    if mail.error:
        return (f"{mail.email} {mail.error}. It was moved to the emails/could not read subfolder; ask the sender to "
                "send the invoice again."), "err"  # fmt: skip
    t = mail.triage
    went = []
    if len(mail.saved) > already + repeated:
        went.append(f"{ui.plural(len(mail.saved) - already - repeated, 'invoice')} to the review queue")
    if repeated:
        went.append(f"{repeated} repeated")
    if already:
        went.append(f"{ui.plural(already, 'attachment')} already in AP Coder")
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
        # (before any GL account none is set: the taxes that need no account are not "2 of 5 set", B-2103)
        ("ok" if mapped == len(TAX_TYPES) else "todo", TAX_STEP, f"{mapped} of {len(TAX_TYPES)} set")
        if gl_count
        else ("todo", TAX_STEP, "after the GL accounts: each tax is posted to one of them"),
        bank_step(store),
        *pilot_steps(store),
    ]


VENDOR_STEP, JDE_STEP, HISTORY_STEP = "Vendor master", "JD Edwards settings", "Past coding from your ERP"
HISTORY_WHY = "AP Coder learns your coding from what you posted before"


def pilot_steps(store: Store) -> list[tuple[str, str, str]]:
    """The vendor master, the JD Edwards settings (when JD Edwards is the export) and the ERP's past coding, in the
    order the pilot needs them after the GL accounts (D-92): each supplier's address number (AN8) comes from
    the vendor master, the settings map the accounts, and the past coding warm-starts the coding of the first
    invoices."""
    from ap_coder.erp import jde_accounts, jde_settings
    from ap_coder.web.core import jde_shown

    shown = jde_shown(store)
    # its ERP ID, else the number set in Settings → JD Edwards → Supplier address numbers (B-1002)
    overrides = dict(jde_settings.load(store).an8_overrides) if shown else {}

    def counted() -> tuple[int, int]:  # (the vendor master's vendors, those with an address number)
        vendors = store.master_vendor_ids()
        return len(vendors), sum(1 for key, erp_id in vendors if str(erp_id or "").strip().isdigit()
                                 or str(overrides.get(key or "") or "").strip().isdigit())  # fmt: skip

    # Kept until the vendor list or the numbers set change: 20,000 vendors read at every view took 0.23 s (D-166).
    vendors, an8 = store.summary_memo("master_vendors_counted", counted, topic="", changes="vendors", asked=overrides)
    if not vendors:
        what = "the ERP's vendor list" + (", with each supplier's address number (AN8)" if shown else "")
        vendor = ("todo", VENDOR_STEP, f"on Vendors: import {what}")
    elif shown:
        vendor = (
            ("ok" if an8 else "todo"),
            VENDOR_STEP,
            f"{ui.plural(vendors, 'vendor')}, {an8} with an address number (AN8)"
            if an8
            else f"{ui.plural(vendors, 'vendor')}, but no address number (AN8): import it as the ERP ID, or set "
            "it in Settings → JD Edwards → Supplier address numbers",
        )
    else:
        vendor = ("ok", VENDOR_STEP, ui.plural(vendors, "vendor"))
    steps = [vendor]
    if shown:
        saved = bool(store.get_setting(jde_settings.SETTING_KEY))
        problems = jde_settings.settings_problems(jde_settings.load(store)) if saved else []
        settings = jde_settings.load(store)
        missing = jde_accounts.accounts_without_bu(
            settings, [a["code"] for a in store.list_accounts("gl_accounts")],
            [t.gl_code for t in store.tax_treatments().values() if t.needs_gl and t.gl_code],
            bool(store.list_accounts("cost_centers")),
        ) if saved and not problems else []  # fmt: skip
        if missing:  # the export would hold back every invoice posting to them (B-1300)
            shown = ", ".join(missing[:4]) + (f" and {len(missing) - 4} more" if len(missing) > 4 else "")
            verb = "has" if len(missing) == 1 else "have"
            steps.append(("todo", JDE_STEP, f"company {settings.company_code} saved, but "
                          f"{ui.plural(len(missing), 'account')} ({shown}) {verb} no JD Edwards business unit: set a "
                          "Default BU, or map them, in Settings → JD Edwards E1"))  # fmt: skip
        elif saved and not problems:
            mapped = f"Default BU {settings.default_bu.strip()}" if settings.use_default_rule and \
                settings.default_bu.strip() else ui.plural(len(settings.gl_map), "account") + " mapped"  # fmt: skip
            steps.append(("ok", JDE_STEP, f"company {settings.company_code}, {mapped}"))
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
    if JDE_STEP in state:  # the pilot with JD Edwards: the vendor master, its settings, the past coding (D-92)
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


def run_summary(ok: int, read: int, skipped: list[str], emails: int, to_person: int, note: str,
                notes: list[dict[str, Any]] | None = None) -> tuple[list[dict[str, Any]], str]:  # fmt: skip
    """What a run of imports says when it ends: (its messages, its one-line label). ``ok``: the files of the ``read``
    ones read and coded; ``skipped``: the names of those already in AP Coder; ``emails``: the saved emails unpacked,
    ``to_person`` of them sorted to a person; ``note``: OvisOCR2's queue (``queued_note``); ``notes``: messages from
    saving the uploads, said first."""
    failed = read - ok
    messages = list(notes or [])
    if ok:
        ready = "It's" if ok == 1 else "They're"  # (B-430)
        messages.append(flash_message(f"{ui.plural(ok, 'invoice')} read and coded. {ready} ready to review in the "
                                      f"review queue. {note}".strip(), "ok", icon="inbox"))  # fmt: skip
    if failed:
        messages.append(flash_message(f"{ui.plural(failed, 'file')} could not be processed. See Review queue → "
                                      "Failed / rejected.", "err"))  # fmt: skip
    if skipped:
        messages.append(flash_message(f"Skipped {ui.plural(len(skipped), 'file')} already in AP Coder.", "info",
                                      list(skipped), "content_copy"))  # fmt: skip
    if not read and not skipped and not emails:
        messages.append(flash_message("Nothing new to read.", "info"))
    # The files of an upload left out before the reading (too large, a kind not read, a text with no amounts, not
    # saved: ``_left_out``) are files picked too: counted, as the button that sent them counted them (B-2100).
    left = sum(1 for m in notes or [] if m.get("left_out"))
    label = f"Processed {ok} of {read + len(skipped) + left}" if read else "Nothing new to read"
    aside = [f"{len(skipped)} already in AP Coder"] if read and skipped else []  # (each said: B-1184)
    aside += [f"{left} left out"] if left else []
    if aside:
        label += f": {', '.join(aside)}"
    if to_person:  # every email counted, the ones sorted to a person too (B-1303)
        files = read + len(skipped) + left
        parts = [f"{ui.plural(ok, 'invoice')} processed" if ok == files
                 else f"{ok} of {ui.plural(files, 'file')} processed"] if files else []  # fmt: skip
        parts += [f"{len(skipped)} already in AP Coder"] if skipped else []
        parts += [f"{left} left out"] if left else []
        parts.append(f"{to_person} sent to Emails that need a person")
        label = f"{ui.plural(emails, 'email')}: {', '.join(parts)}"
    return messages, label


def import_status(store: Store, settings: Settings | None = None) -> dict[str, Any]:
    """The Process page's card, as a job's snapshot (``Job.snapshot``): the import queue's latest run (D-168), the
    same in every web worker and after a restart. Running while any of its files is in line; then its summary."""
    snap: dict[str, Any] = {"name": JOB, "state": "idle", "stage": "", "done": 0, "total": 0, "note": "",
                            "lines": [], "result": None, "error": "", "started_at": "", "finished_at": "",
                            "about": {}}  # fmt: skip
    run = store.import_run(MAX_LINES)
    if run is None:
        return snap
    counts, batches = run["counts"], run["batches"]
    ok, read = counts.get("done", 0), counts.get("done", 0) + counts.get("failed", 0)
    in_line = counts.get("waiting", 0) + counts.get("reading", 0)
    lines = [line for b in batches for line in json.loads(b["lines"] or "[]")] + run["lines"]
    snap.update(state="running" if in_line else "done", stage="reading", done=read, total=read + in_line,
                lines=lines[-MAX_LINES:], started_at=batches[0]["created_at"])  # fmt: skip
    if in_line:
        reading = run["reading"]
        if not has_room(store.path.parent):
            snap["note"] = NO_ROOM
        elif store.restoring():
            snap["note"] = RESTORING
        elif reading:
            shown = ", ".join(reading[:3]) + (f" and {len(reading) - 3} more" if len(reading) > 3 else "")
            snap["note"] = f"Reading and coding {shown} ({read + 1}/{read + in_line})"
        else:
            snap["note"] = f"{ui.plural(in_line, 'file')} in line to be read"
            if run["held"]:  # what stopped the last try (the GL accounts open in Excel), said, not a spinner alone
                snap["note"] += f". It could not be read just now: {run['held']}. AP Coder tries again every few " \
                                "seconds."  # fmt: skip
        return snap
    skipped = [name for b in batches for name in json.loads(b["skipped"] or "[]")] + run["skipped"]
    notes = [m for b in batches for m in json.loads(b["notes"] or "[]")]
    queued = run["page_reads"]
    note = queued_note(settings, store, queued) if ok and settings is not None else ""
    messages, label = run_summary(ok, read, skipped, sum(b["emails"] for b in batches),
                                  sum(b["to_person"] for b in batches), note, notes)  # fmt: skip
    snap.update(finished_at=run["finished_at"] or batches[-1]["finished_at"] or "",
                result={"processed": read, "ok": ok, "failed": read - ok, "queued": queued, "messages": messages,
                        "label": label})  # fmt: skip
    return snap


# --- The page ------------------------------------------------------------------------------------------------------


def still_being_saved(path: Path, now: float | None = None) -> bool:
    """Changed in the last minute and its end missing (``intake.looks_incomplete``): a scanner or a download is still
    writing it. Read now, half an invoice would be stored (and the whole one read again later as another). A file
    that is complete is read at once, however new."""
    try:
        recent = abs((time.time() if now is None else now) - path.stat().st_mtime) < STILL_SAVING_SECONDS
        return recent and looks_incomplete(path)
    except OSError:  # gone or locked meanwhile: next time
        return True


NOT_LISTED = "The invoices folder could not be listed just now ({why}): its new files show here once it can be."
BEING_LISTED = "The invoices folder is being listed: its new files show here in a moment."
LISTED_AT = "As the invoices folder was {when}: files saved since show once it is listed again."  # ui.day_and_time
# The folder's new files and saved emails (None: not listed), the names of its files not read, what to say of it.
Shown = tuple[list[dict[str, Any]] | None, list[str], str]

# On a laptop, ``folder_files`` by folder: (stamp, what it gives, when worked out); kept LISTED_SECONDS at most (a file
# written over in place changes no stamp)
_LISTED: dict[str, tuple[tuple[Any, ...], tuple[list[dict[str, Any]], list[str]], float]] = {}
LISTED_SECONDS = 60.0
# On the company server, the folder as listed for every process (``shared_files``, D-176): listed again once the
# folder changed and the listing is SHARED_REFRESH seconds old, or whatever changed once it is SHARED_MOST old (each
# counted from its end: a slow folder is not listed back to back); the view that started a listing waits SHARED_WAIT
# at most for it when the one kept is older than that. A file saved in the folder shows within a minute; a listing
# shown older than SHARED_MOST says when it was made.
SHARED_REFRESH = 20.0
SHARED_MOST = 45.0
SHARED_WAIT = 5.0
SHARED_POLL = 0.1
LISTING_LEASE = 60.0  # seconds the process listing the folder holds it for the others, renewed as it goes
_OWNER = f"{os.getpid()}:{secrets.token_hex(4)}"  # this process, as the listings' lease names it
_LISTING: set[str] = set()  # the folders a thread of this process lists now
_LISTING_LOCK = threading.Lock()


def folder_files(store: Store) -> Shown:
    """(the files in the invoices folder not processed yet, saved emails too: their attachments are read, None when
    it could not be listed; the names of its files of a kind not read; what to say of the listing, "" when nothing)
    on a laptop, as the pick-up last read them: a page view reads none, it looks at their size and time
    (``list_folder``, D-155). The invoices folder keeps the processed files: reading every one on each view took 7 s
    at 100,000 invoices, and looking at each one's size and time still takes a second at 300,000, so the list is
    worked out again only once the folder (a file added, removed or renamed), what a pick-up read or the invoices
    changed, or a minute on (a scan written over another of its name). Listed at the folder's resolved place, as the
    pick-up notes them (``read_file``)."""
    folder = invoice_dir(store).resolve()
    try:
        stamp = (folder.stat().st_mtime_ns, store.files_stamp())
    except OSError:  # no invoices folder yet
        return [], [], ""
    known = _LISTED.get(str(folder))
    if known is not None and known[0] == stamp and time.monotonic() - known[2] < LISTED_SECONDS:
        return *known[1], ""
    try:
        listing = list_folder(store, folder)
    except OSError as exc:  # there, but not listable just now (B-1804)
        return None, [], NOT_LISTED.format(why=exc.strerror or type(exc).__name__)
    rows = _rows([(os.path.basename(place), size) for place, size, _ in listing.new], listing.emails)
    if len(_LISTED) > 8:  # (a test's many data folders)
        _LISTED.clear()
    if time.time_ns() - stamp[0] > 2e9:  # a folder changed just now may change again within its time's precision
        _LISTED[str(folder)] = (stamp, (rows, listing.others), time.monotonic())
    return rows, listing.others, ""


def shared_files(store: Store) -> Shown:
    """``folder_files`` on the company server, where no view lists the folder (D-176): the latest listing kept for
    every process (``Store.kept_listing``), the files a pick-up put in line or read since left out at once (one
    query on those files only). When it is due (``SHARED_REFRESH``, ``SHARED_MOST``) a thread of this process lists
    the folder again (``list_for_all``) while the view shows the one kept, unless that is too old: the view that
    started the listing (or with none to show) waits a few seconds for it; the others say when the one they show was
    made (a stopped process's lease, a slow folder: none waits on another's listing)."""
    place = invoice_dir(store).resolve()
    try:
        mtime_ns = os.stat(place).st_mtime_ns
    except OSError:  # no invoices folder yet
        return [], [], ""
    kept = store.kept_listing(str(place))
    age = time.time() - kept[0] - kept[2].get("took", 0) if kept is not None else -1.0  # (since it ended)
    current = kept is not None and 0 <= age < SHARED_MOST
    if not current or (kept[1] != mtime_ns and age >= SHARED_REFRESH):  # type: ignore[index]
        started = list_for_all(store, place)
        if not current and (started or kept is None):
            kept = _next_listing(store, str(place), kept)
    if kept is None:
        return None, [], BEING_LISTED
    began, _, found = kept
    if found.get("error"):
        return None, [], NOT_LISTED.format(why=found["error"])
    listed = [(os.path.join(place, name), size, at) for name, size, at in found["files"]]
    new = store.unseen_files(listed, since=int(began * 1e9)) if listed else set()  # (one written again since, B-1814)
    rows = _rows([(name, size) for (name, size, _), f in zip(found["files"], listed, strict=True) if f[0] in new],
                 found["emails"])  # fmt: skip
    if 0 <= time.time() - began < SHARED_MOST:
        return rows, found["others"], ""
    return rows, found["others"], LISTED_AT.format(when=ui.day_and_time(dt.datetime.fromtimestamp(began)))  # B-1815


def list_for_all(store: Store, place: Path) -> bool:
    """The invoices folder at ``place`` listed again for every process (``list_folder``, kept: ``Listing.keep``) by a
    thread of this one, unless another thread or process lists it now (its lease, renewed as it lists: a slow network
    folder, minutes at 300,000 files, is listed once); whether it was started. A folder that can't be listed is kept
    as such, with why."""
    lease = f"folder-listing:{place}"
    with _LISTING_LOCK:
        if str(place) in _LISTING:
            return False
        _LISTING.add(str(place))
    try:
        with store.briefly():  # (a busy database: the next view)
            taken = store.take_lease(lease, _OWNER, LISTING_LEASE)
    except sqlite3.Error:
        taken = False
    if taken:
        try:
            threading.Thread(target=_list_for_all, args=(store, place, lease), name="ap-coder-folder-listing",
                             daemon=True).start()  # fmt: skip
            return True
        except RuntimeError:  # no thread to be had just now (B-1816): let go of, for the next view that finds it due
            logging.getLogger(__name__).warning("the invoices folder %s was not listed: no thread to list it", place,
                                                exc_info=True)  # fmt: skip
            with contextlib.suppress(sqlite3.Error), store.briefly():  # (else it runs out within LISTING_LEASE)
                store.drop_lease(lease, _OWNER)
    with _LISTING_LOCK:
        _LISTING.discard(str(place))
    return False


def _list_for_all(store: Store, place: Path, lease: str) -> None:
    done = threading.Event()
    beat = threading.Thread(target=_renewing, args=(store, lease, done), name="ap-coder-folder-listing-lease",
                            daemon=True)  # fmt: skip
    try:
        beat.start()
        with store.session():
            try:
                list_folder(store, place).keep(store)
            except OSError as exc:  # kept as such: each process's page says why
                why = exc.strerror or type(exc).__name__
                store.keep_listing(str(place), time.time(), 0, {"files": [], "emails": [], "others": [], "error": why})
    except Exception:  # noqa: BLE001 - a busy database, the folder gone: the next view that finds it due lists it
        logging.getLogger(__name__).warning("the invoices folder %s could not be listed for the import page", place,
                                            exc_info=True)  # fmt: skip
    finally:
        done.set()
        with contextlib.suppress(Exception):
            store.drop_lease(lease, _OWNER)
        with _LISTING_LOCK:
            _LISTING.discard(str(place))


def _renewing(store: Store, lease: str, done: threading.Event) -> None:
    """The listing's lease renewed every LISTING_LEASE/4 until ``done``, whatever the listing is doing (the folder
    read by name, each file looked at, what is new worked out: B-1817). A renewal the database refuses (busy past its
    wait) is tried again at the next: the listing goes on."""
    while not done.wait(LISTING_LEASE / 4):
        try:
            store.renew_lease(lease, _OWNER, LISTING_LEASE)
        except Exception:  # noqa: BLE001 - a busy database: tried again at the next, well within the lease
            logging.getLogger(__name__).warning("the lease on listing the invoices folder was not renewed just now",
                                                exc_info=True)  # fmt: skip


def _next_listing(store: Store, place: str, kept: Any) -> Any:
    """The listing kept of ``place`` once another than ``kept`` is (``SHARED_WAIT`` at most; else ``kept``)."""
    ends = time.monotonic() + SHARED_WAIT
    while time.monotonic() < ends:
        time.sleep(SHARED_POLL)
        found = store.kept_listing(place)
        if found is not None and (kept is None or found[0] != kept[0]):
            return found
    return kept


def _rows(files: list[tuple[str, int]], emails: list[tuple[str, int]]) -> list[dict[str, Any]]:
    """The page's rows of the new files and saved emails, (name, size) each."""
    found = [(name, size, False) for name, size in files] + [(name, size, True) for name, size in emails]
    return [{"name": name, "kb": f"{size / 1024:,.0f} KB", "email": email,
             "icon": "mail" if email else "picture_as_pdf" if name.lower().endswith(".pdf") else "image"}
            for name, size, email in found]  # fmt: skip


RECENT = 6  # the newest invoices the page lists
NEWEST = "created_at DESC, id DESC"
RECENT_COLUMNS = ("id, file_name, status, vendor_name, invoice_number, invoice_date, due_date, currency, grand_total, "
                  "model_confidence, adjusted_confidence, requires_review, error, created_at, reviewed_at, reviewer, "
                  "second_reviewer")  # (as ``Store.list_invoices`` has them)  # fmt: skip


@router.get("/process", response_class=HTMLResponse)
def process_page(request: Request) -> HTMLResponse:
    store, settings = get_store(request), get_settings(request)
    total = sum(store.status_counts().values())  # counted, not read: a year of invoices is not read (D-110)
    steps, steps_done, steps_needed = first_steps(store, settings, bool(total))
    recent = store.summary_rows(f"id IN (SELECT id FROM invoice_summary ORDER BY {NEWEST} LIMIT ?)", (RECENT,),
                                RECENT_COLUMNS, NEWEST)  # fmt: skip
    waiting = [i for i in recent if i["status"] == REVIEW]
    reasons = attention(store, reference_or_none(store), waiting) if waiting else {}
    recent = [{**i, "needs_attention": i["id"] in reasons} for i in recent]  # its pill says what the queue says
    steps, coming, links = shown_setup(request, steps, next_step(steps))
    files, not_read, folder_note = (shared_files if server_mode(request) else folder_files)(store)
    return render(
        request, "process.html", "process",
        # (Demo invoices loaded are said on the page from every page's ``demo_count``: a notice, never asked to be
        # removed, with your own invoices there or not; removed in Settings → Data & backups, D-221.)
        steps=steps,
        step_links=links,
        steps_done=steps_done,
        steps_needed=steps_needed,
        next=coming if coming and coming["href"] != "/process" else None,  # (this page's own step: choose files)
        first_invoices=bool(coming) and coming["href"] == "/process",
        ready=ready_to_read(steps),
        recent=recent,
        recent_waiting=any(i["status"] == REVIEW for i in recent),
        files=files or [],
        not_read=not_read,
        folder_note=folder_note,
        folder_listed=files is not None,
        set_aside=inbox.needs_person(store, invoice_dir(store)),
        folder=short_path(invoice_dir(store)) if is_admin(request) else "",
        accept=",".join(ACCEPT),
        voucher_kinds=joined.KINDS,
        job=import_status(store, settings),
    )  # fmt: skip


# --- The import queue (D-168) ---------------------------------------------------------------------------------------
# An upload or a pick-up puts its files in line and answers at once. On a laptop (and a server of one web worker) the
# job "process" of this process reads the queue until it is empty, whoever brought the files; on the company server
# its capture processes read it, never a web worker.

READER = owner_id("dashboard")  # this process's own reader, as the queue's rows name it
FOLLOW_SECONDS = 1.0  # while another reader holds the last files, the job looks again this often
# Paused (the disk nearly full, a backup being restored) or stopped by an error (the GL accounts open in Excel), the job
# looks again this often while files wait, as the server's capture processes do (B-1764).
LOOK_AGAIN_SECONDS = 5.0
PICKUP_PATIENCE = 3.0  # the Process button waits this long at most for another pick-up of the folder (B-1771)
BUSY_PICKUP = ("Another pick-up of invoices is under way (a scheduled watch, or ap_coder process): press Process "
               "again in a moment.")  # fmt: skip


def capture_elsewhere(app: Any) -> bool:
    """Whether the files are read by the company server's capture processes (several web workers, D-111), not here."""
    return getattr(app.state, "processes", 1) > 1


def queue_files(request: Request, read: list[Any], *, source: str, include_failed: bool = False,
                notes: list[dict[str, Any]] | None = None, lines: list[dict[str, str]] | None = None,
                skipped: list[str] | None = None, emails: int = 0, to_person: int = 0) -> dict[str, Any]:  # fmt: skip
    """Put the files ``read`` (``read_file``'s tuples) in the import queue as one batch brought by the person asking,
    then see that they are read (``start_reading``). What ``Store.queue_imports`` returns."""
    who = person()
    queued = get_store(request).queue_imports(
        read, source=source, uploader=str(who.user_id) if who is not None and who.user_id else reviewer(),
        actor=reviewer(), account=EVENT_ACTOR.get() or "", origin=EVENT_ORIGIN.get() or "", person=True,
        include_failed=include_failed, notes=notes or [], lines=lines or [], skipped=skipped or [], emails=emails,
        to_person=to_person,
    )  # fmt: skip
    start_reading(request.app)
    return queued


def start_reading(app: Any) -> bool:
    """See that the queue is read: here, the job "process" started unless it runs (True when it started); on the
    company server, nothing (its capture processes look every second)."""
    if capture_elsewhere(app) or app.state.import_stop.is_set():
        return False
    job = app.state.jobs.get(JOB)
    store = app.state.store
    return job.start(_read_here(store, app.state.settings_factory, app.state.import_stop),
                     then=lambda: _again(app, job))  # fmt: skip


def _again(app: Any, job: Job) -> None:
    """After the job: files put in line as it ended are read too."""
    store = app.state.store
    if job.error or app.state.import_stop.is_set() or not store.imports_open(READER)["waiting"]:
        return  # (a job that failed is not started again in a loop: the next import or start does)
    start_reading(app)


def _read_here(store: Store, settings: Any, stop: threading.Event) -> Any:
    def work(job: Job) -> dict[str, Any]:
        reader = Reader(store, settings, READER, attention=needs_attention, cache_dir=cache_dir(store))
        said = ""
        while not stop.is_set():
            try:
                reader.read_all(stop)
                found = store.imports_open(READER)
            except Exception as exc:  # noqa: BLE001 - the GL accounts open in Excel, a busy database: the files wait
                if str(exc) != said:  # (logged once, not every few seconds; the Process page says why)
                    logging.getLogger(__name__).warning("the import queue could not be read just now", exc_info=True)
                said = str(exc)
                stop.wait(LOOK_AGAIN_SECONDS)
                continue
            said = ""
            if not found["waiting"] and not found["reading"]:
                return {}
            # Paused (no room, a restore) or another reader holds the last ones (a watch): until they are done.
            stop.wait(LOOK_AGAIN_SECONDS if reader.paused else FOLLOW_SECONDS)
        return {}

    return work


def stop_reading(app: Any) -> None:
    """As the dashboard stops (a laptop's): its import takes no more files, and the one under way goes back in line,
    its try not counted, as a capture process's stop does (B-1766)."""
    app.state.import_stop.set()
    try:
        app.state.store.release_imports(READER)
    except Exception:  # noqa: BLE001 - a busy database: its lease runs out, and it is read again
        logging.getLogger(__name__).warning("the file under way was not put back in line", exc_info=True)


def resume(app: Any) -> None:
    """At the start: files left in line by the last run (a restart, a power cut) are read again (a laptop's)."""
    try:
        if any(app.state.store.imports_open(READER).values()):
            start_reading(app)
    except Exception:  # noqa: BLE001 - a busy database: the next import reads them
        logging.getLogger(__name__).warning("the import queue was not looked at as AP Coder started", exc_info=True)


def _started(queued: dict[str, Any]) -> JSONResponse:
    """The answer to an upload or a pick-up: the page follows the queue's run; told when others' files were in line."""
    answer: dict[str, Any] = {"ok": True, "job": JOB, "status": "/process/status"}
    if queued["queued"] and queued["ahead"]:
        answer["queued"] = queued["queued"]
        answer["toast"] = flash_message(
            f"{ui.plural(queued['queued'], 'file')} in line with {ui.plural(queued['ahead'], 'file')} that came in "
            "before: they're read in turn, so yours don't wait for all of them.", "info")  # fmt: skip
    return JSONResponse(answer)


def _email_lines(store: Store, mails: list[Unpacked], read: list[Any], include_failed: bool) -> list[dict[str, str]]:
    """Each email's line on the card (``email_line``), its attachments already in AP Coder said (``read``: what was
    read of them); each logged as sorted."""
    shas = {r[0]: r[3] for r in read}
    saved = [shas.get(str(p.parent.resolve() / p.name)) for m in mails for p in m.saved]
    known = store.known_files([sha for sha in saved if sha], include_failed) if saved else set()
    seen: set[str | None] = set()
    lines = []
    for mail in mails:
        already = repeated = 0
        for path in mail.saved:
            sha = shas.get(str(path.parent.resolve() / path.name))
            already += sha in known
            repeated += sha not in known and sha in seen
            seen.add(sha)
        text, tone = email_line(mail, already, repeated)
        lines.append({"text": text, "tone": tone, "icon": "gpp_maybe" if tone == "err" and mail.triage else "mail"})
        inbox.log_sorted(store, mail)
    return lines


def _read_saved(mails: list[Unpacked], unreadable: list[Path]) -> list[Any]:
    """``read_file`` of each attachment the emails saved (one that can't be read now goes to ``unreadable``)."""
    read = []
    for path in [p for m in mails for p in m.saved]:
        try:
            read.append(read_file(path))
        except OSError:
            unreadable.append(path)
    return read


@router.post("/process/upload")
def process_upload(request: Request, files: list[UploadFile] = File(...),  # noqa: B008
                   voucher: str = Form("")) -> JSONResponse:  # fmt: skip
    """Save the uploaded files to the invoices folder, then read and code the new ones in the background. Each file
    is read from its spool file (on disk past 1 MB) only when it is saved, one at a time: never all in memory. (A
    plain function: FastAPI takes the upload in, then runs it on a threadpool thread, D-158.) ``voucher``: the files
    are one voucher of that kind (``joined.KINDS``, D-216), joined in the order sent and read as one."""
    if server_mode(request):
        who = person()
        refused, status = upload_refusal(who.user_id if who else None, len(files), sum(u.size or 0 for u in files),
                                         store=get_store(request))  # fmt: skip
        if refused:
            return refuse(refused, status=status)
    return _save_and_start(request, files, voucher)


def _read_upload(upload: UploadFile) -> tuple[str, bytes | None]:
    """An upload's name and bytes (None when over its limit: never more than the limit read)."""
    name = upload.filename or "invoice"
    limit = MAX_EMAIL_BYTES if Path(name).suffix.lower() in EMAIL_EXTENSIONS else MAX_UPLOAD_BYTES
    content = upload.file.read(limit + 1)
    return name, content if len(content) <= limit else None


def _left_out(name: str, why: str, text: str, tone: str = "warn") -> tuple[dict[str, Any], dict[str, str]]:
    """A file of an upload left out before the reading: (its message, marked so the run counts it among the files
    picked; its line on the card, said as the run starts), B-2100."""
    line = {"text": f"{name}: left out, {why}", "tone": tone, "icon": "block"}
    return {**flash_message(text, tone), "left_out": True}, line


def _save_and_start(request: Request, files: list[UploadFile], voucher: str = "") -> JSONResponse:
    store, settings = get_store(request), get_settings(request)
    if not ready_to_read(setup_steps(store, settings)):
        return refuse("Finish the setup first: import your GL accounts (GL accounts & tax).")
    folder = invoice_dir(store)
    saved, mails, notes, left = [], [], [], []
    for upload in files:
        name, content = _read_upload(upload)
        if content is None:  # over the limit (B-981): an invoice is never this large
            most = "100" if Path(name).suffix.lower() in EMAIL_EXTENSIONS else "50"
            left.append(_left_out(name, f"larger than {most} MB",
                                  f"{name} was not read: it is larger than {most} MB. An invoice is far smaller: "
                                  "scan it again at 300 dpi, or save it as a PDF."))  # fmt: skip
            continue
        if Path(name).suffix.lower() not in ACCEPT:
            left.append(_left_out(name, "not a kind of file AP Coder reads",
                                  f"{name} was not read: AP Coder reads PDFs, pictures (JPEG, PNG, HEIC, TIFF, BMP, "
                                  "WEBP, GIF), saved emails (.eml, .msg) and invoices saved as text (.txt). Save it "
                                  "as PDF or JPEG to have it read."))  # fmt: skip
            continue
        if Path(name).suffix.lower() in TEXT_EXTENSIONS and not looks_like_an_invoice_text(content):
            left.append(_left_out(name, "a text file with no amounts in it",
                                  f"{name} was not read: a text file (.txt, .md) is read as an invoice only when it "
                                  "has amounts in it (like 125.00 or Total $105), and this one has none."))  # fmt: skip
            continue
        try:  # one file that cannot be saved (a name Windows refuses, disk full) is said and skipped
            target = save_upload(folder, name, content)
            if target.suffix.lower() in EMAIL_EXTENSIONS:  # its invoice attachments, not the email
                mails.append(unpack(target, folder))
                continue
            saved.append(noted(target, content))  # hashed once, from what was uploaded (D-168)
        except OSError as exc:
            left.append(_left_out(name, "it could not be saved to the invoices folder",
                                  f"{name} could not be saved to the invoices folder "
                                  f"({exc.strerror or type(exc).__name__}). Rename it or save it there yourself.",
                                  "err"))  # fmt: skip
            continue
    if voucher:  # these files are one voucher (D-216): joined in the order chosen, then read as one
        try:
            if mails or left:
                raise joined.CannotJoin(left[0][0]["text"] if left else "A saved email can't be one of a voucher's "
                                        "files: choose its attachments, as PDFs or pictures.")  # fmt: skip
            return _started(join_files(request, folder, [Path(place) for place, *_ in saved], voucher, "upload"))
        except joined.CannotJoin as exc:
            if len(store.known_files([sha for *_, sha in saved])) == len(saved):  # all in AP Coder: none is listed
                return refuse(str(exc))
            return refuse(f"{exc} The files you chose are in the invoices folder now, listed on this page.")
    unreadable: list[Path] = []
    attached = _read_saved(mails, unreadable)
    saved += attached
    if unreadable:  # an email's attachment saved, then locked meanwhile (an antivirus): the folder's pick-up reads it
        notes.append(flash_message(f"{ui.plural(len(unreadable), 'file')} could not be opened just now: picked up "
                                   "next time.", "warn", [p.name for p in unreadable]))  # fmt: skip
    notes = [note for note, _ in left] + notes
    lines = [line for _, line in left] + _email_lines(store, mails, attached, include_failed=False)
    queued = queue_files(request, saved, source="upload", notes=notes, lines=lines, emails=len(mails),
                         to_person=_to_person(mails))  # fmt: skip
    return _started(queued)


def join_files(request: Request, folder: Path, paths: list[Path], kind: str, source: str,
               include_failed: bool = False) -> dict[str, Any]:  # fmt: skip
    """The files at ``paths`` made one document (``joined.join``, D-216) and that document put in line (what
    ``queue_files`` returns), both while no pick-up of the folder runs: a scheduled watch would put the same files in
    line one by one, and a second person's join of one of them is refused, its voucher being in line by then
    (B-2283). Raises ``joined.CannotJoin``."""
    store = get_store(request)
    lock = wait_for_pickup(data_dir(store), lambda: None, most=PICKUP_PATIENCE)
    if lock is None:
        raise joined.CannotJoin(BUSY_PICKUP)
    try:
        made = read_file(joined.join(store, folder, paths, kind, reviewer()))
        return queue_files(request, [made], source=source, include_failed=include_failed)
    finally:
        lock.close()


def _to_person(mails: list[Unpacked]) -> int:
    return sum(1 for m in mails if m.error or (m.triage and m.triage.needs_person))


@router.post("/process/folder")
def process_folder(request: Request) -> JSONResponse:
    """Put the new files in the invoices folder (and the invoices attached to saved emails there) in line to be read
    and coded. Only the files the pick-up hasn't read since they changed are read, each once (D-168)."""
    store, settings = get_store(request), get_settings(request)
    if not ready_to_read(setup_steps(store, settings)):
        return refuse("Finish the setup first: import your GL accounts (GL accounts & tax).")
    folder = invoice_dir(store)
    # One pick-up at a time (a scheduled watch): each only lists, unpacks the emails and puts files in line (B-1587).
    # This request waits a few seconds for another at most, then says so: a big folder takes a minute (B-1771).
    lock = wait_for_pickup(data_dir(store), lambda: None, most=PICKUP_PATIENCE)
    if lock is None:
        return refuse(BUSY_PICKUP, 409)
    try:  # (the folder listed before the emails are unpacked: their attachments are read once, as theirs)
        try:
            listing = list_folder(store, folder) if folder.exists() else None
        except OSError as exc:  # there, but not listable just now (B-1806)
            return refuse(NOT_LISTED.format(why=exc.strerror or type(exc).__name__), 409)
        read, unreadable, saving = ([], [], []) if listing is None else pick_up(store, folder, still_being_saved,
                                                                                listing)  # fmt: skip
        mails = [] if listing is None else unpack_folder(folder)
        if mails:  # the emails unpacked have left the folder: the import page no longer lists them (D-176)
            listing.emails = [e for e in listing.emails if os.path.exists(os.path.join(listing.place, e[0]))]
            listing.keep(store)
        attached = _read_saved(mails, unreadable)
        read += attached
        notes = []
        unreadable += saving  # a scanner or a download still writing it (B-265)
        if unreadable:
            notes.append(flash_message(f"{ui.plural(len(unreadable), 'file')} could not be opened (still being saved, "
                                       "or open in another program): picked up next time.", "warn",
                                       [p.name for p in unreadable]))  # fmt: skip
        others = [] if listing is None else listing.others
        if others:
            are = "are" if len(others) > 1 else "is"
            notes.append(flash_message(f"{ui.plural(len(others), 'file')} in the invoices folder {are} not a kind AP "
                                       "Coder reads (PDFs, scans, photos and saved emails): save them as PDF (or JPEG) "
                                       "to have them read.", "warn", others))  # fmt: skip
        lines = _email_lines(store, mails, attached, include_failed=True)
        queued = queue_files(request, read, source="folder", include_failed=True, notes=notes, lines=lines,
                             emails=len(mails), to_person=_to_person(mails))  # fmt: skip
    finally:
        lock.close()
    return _started(queued)


@router.post("/process/voucher")
def process_voucher(request: Request, names: list[str] = Form([]), kind: str = Form("")) -> JSONResponse:  # noqa: B008
    """The files ticked in the invoices folder's list are one voucher (D-216): joined in the order shown
    (``intake.joined.join``: the pages of one invoice, or the receipts of one claim) and put in line as one file."""
    store, settings = get_store(request), get_settings(request)
    if not ready_to_read(setup_steps(store, settings)):
        return refuse("Finish the setup first: import your GL accounts (GL accounts & tax).")
    folder = invoice_dir(store)
    paths = [folder / name for name in names]
    gone = [name for name, path in zip(names, paths, strict=True) if path.parent != folder or not path.is_file()]
    if gone:
        return refuse(f"{', '.join(gone)} is no longer in the invoices folder: reload the page.", 409)
    try:
        queued = join_files(request, folder, paths, kind, "folder", include_failed=True)
    except joined.CannotJoin as exc:
        return refuse(str(exc))
    if server_mode(request):  # the list kept for every process no longer has the files joined (D-176)
        with contextlib.suppress(OSError):
            list_folder(store, folder).keep(store)
    return _started(queued)


@router.get("/process/status")
def process_status(request: Request) -> JSONResponse:
    """How far reading and coding got (the page polls this): the import queue's latest run."""
    snap = import_status(get_store(request), get_settings(request))
    return JSONResponse(snap, headers={"Cache-Control": "no-store"})


@router.post("/process/open-folder")
def process_open_folder(request: Request) -> JSONResponse:
    return open_folder_here(request, invoice_dir(get_store(request)), "invoices folder")


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


# --- Demo invoices (offered in Settings → Data & backups only: settings.data_context, D-221) -------------------------


@router.post("/demo/load")
def demo_load(request: Request, back: str = Form("/process")) -> JSONResponse:
    from ap_coder.ops.demo import demo_available, load_demo

    if not demo_available():
        return refuse("The demo invoices are not in this copy of AP Coder.")
    store = get_store(request)
    if sum(store.status_counts().values()) > store.demo_count():  # a page drawn before your own came in (B-1529)
        return refuse("Not loaded: your own invoices are in AP Coder now, and the demo would mix ten made-up invoices "
                      "with them (a second PO-77120 would read as a duplicate payment). Reload the page.")  # fmt: skip
    result = load_demo(store, get_settings(request))
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
