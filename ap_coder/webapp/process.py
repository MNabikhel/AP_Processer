"""Process invoices: setup checklist, upload, folder pick-up, recently processed."""

from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath, PureWindowsPath

import streamlit as st

from ap_coder import page_reader, ui
from ap_coder.capture.layout import ocr_available
from ap_coder.config import Settings
from ap_coder.extraction import SUPPORTED_EXTENSIONS
from ap_coder.local_llm import provider_status
from ap_coder.mailbox import EMAIL_EXTENSIONS, Unpacked, safe_name, unpack, unpack_folder
from ap_coder.pipeline import InvoicePipeline, invoice_files
from ap_coder.safe import md
from ap_coder.store import REVIEW, Store
from ap_coder.tax import TAX_TYPES
from ap_coder.webapp.common import (
    CACHE_DIR,
    INVOICE_DIR,
    PAGES,
    PUBLIC_DEMO,
    card,
    demo_card,
    esc,
    get_settings,
    get_store,
    not_in_public_demo,
    notify,
    open_folder,
    page_head,
    reading_status_or_none,
    short_path,
    show_toast,
)


def safe_file_name(name: str) -> str:
    """Just the file name of an upload (no folders, Windows or POSIX), never empty or a dot name, and short
    enough for Windows paths (80 characters, the extension kept)."""
    base = PureWindowsPath(PurePosixPath(name or "").name).name.strip().lstrip(".")
    return safe_name(base) if base else "invoice"


def save_upload(name: str, content: bytes) -> Path:
    """Save an uploaded file to the invoices folder under a safe name (a different file with the same name
    gets ``_1``, ``_2``...). Raises OSError when it cannot be written."""
    INVOICE_DIR.mkdir(parents=True, exist_ok=True)
    target = INVOICE_DIR / safe_file_name(name)
    stem, n = target.stem, 1
    while target.exists() and target.read_bytes() != content:
        target = INVOICE_DIR / f"{stem}_{n}{target.suffix}"
        n += 1
    target.write_bytes(content)
    return target


# --- Process invoices --------------------------------------------------------------------------------------


def _email_note(mail: Unpacked) -> None:
    """What was taken out of a saved email, and what was left out and why."""
    if mail.error:
        st.caption(f":material/mail: **{md(mail.email)}** {md(mail.error)}. It was moved to the "
                   "`emails/could not read` subfolder; ask the sender to send the invoice again.")  # fmt: skip
        return
    took = f"{ui.plural(len(mail.saved), 'attachment')} taken out" if mail.saved else "no invoice attached"
    st.caption(f":material/mail: **{md(mail.email)}**: {took}; the email is in the `emails` subfolder.")
    if mail.skipped:
        st.caption("Left out: " + md("; ".join(mail.skipped)))


def queued_note(queued: int) -> str:
    """After processing: the invoices now waiting for OvisOCR2, and when it reads them ("" when none were queued)."""
    if queued <= 0:
        return ""
    them = "it" if queued == 1 else "them"
    status = reading_status_or_none()
    if status is not None and not status.page_reader_ready:
        return (f"{ui.plural(queued, 'invoice')} queued for OvisOCR2, which isn't running: {them} wait for it "
                "(Settings → Reading).")  # fmt: skip
    eta = ""
    if status is not None and status.eta_minutes:
        eta = f", {page_reader.duration(status.eta_minutes * 60)} for the whole queue"
    return f"{ui.plural(queued, 'invoice')} queued for OvisOCR2: it reads {them} in the background{eta}."


def run_pipeline(store: Store, paths: list[Path]) -> None:
    if PUBLIC_DEMO:  # the public demo reads nothing new: its invoices were read ahead of time
        not_in_public_demo("Processing new invoices")
        return
    reference = store.reference_data()
    settings = get_settings()
    pipeline = InvoicePipeline(settings, reference, cache_dir=CACHE_DIR, store=store)
    ok = 0
    waiting = store.page_reads_waiting()
    with st.status(f"Processing {ui.plural(len(paths), 'invoice')}…", expanded=True) as status:
        for n, path in enumerate(paths, start=1):
            st.write(f":material/document_scanner: Reading and coding **{md(path.name)}** ({n}/{len(paths)})")
            result = pipeline.process(path)
            if result.ok:
                ok += 1
                flag = "needs attention" if result.report and result.report.requires_review else "ready"
                st.write(f":material/check_circle: {md(result.output.get('vendor_name') or path.name)}: {flag}")
            else:
                st.error(f"{md(path.name)}: {md(result.error)}", icon=":material/error:")
        status.update(label=f"Processed {ok} of {len(paths)}", state="complete" if ok == len(paths) else "error")
    failed = len(paths) - ok
    queued = max(0, store.page_reads_waiting() - waiting)
    if ok:
        notify(f"{ui.plural(ok, 'invoice')} read and coded. They're waiting in the review queue. {queued_note(queued)}",
               ":material/inbox:")  # fmt: skip
    if failed:
        notify(
            f"{ui.plural(failed, 'file')} could not be processed. See Review queue → Failed / rejected.",
            ":material/error:",
        )


def tax_types_mapped(store: Store) -> int:
    """Tax types ready to post: added to expense lines, or mapped to a GL account that still exists."""
    codes = {a["code"] for a in store.list_accounts("gl_accounts")}
    return sum(1 for t in store.tax_treatments().values() if not t.needs_gl or t.gl_code in codes)


def setup_steps(store: Store, settings: Settings) -> list[tuple[str, str, str]]:
    mapped = tax_types_mapped(store)
    gl_count = len(store.list_accounts("gl_accounts"))
    ai = provider_status(settings)  # the chat model in LM Studio that suggests GL accounts (optional)
    if ocr_available():
        reader = ("ok", "Reading invoices", "PDF text + OCR")
    else:
        reader = ("todo", "Reading invoices", "text PDFs only: scans need OCR (run the launcher again)")
    status = reading_status_or_none(settings, store)
    if status is None:
        second = ("todo", "OvisOCR2", "see Settings → Reading")
    elif status.page_reader_ready:
        second = ("ok", "OvisOCR2", "reads every invoice in the background")
    else:
        second = ("todo", "OvisOCR2", "not running yet: invoices wait for it (Settings → Reading)")
    return [  # the review page's getting-started card reads the chat model at index 1
        reader,
        ("ok" if ai.ready else "opt", "Chat model for GL suggestions", ai.label if ai.ready else "optional"),
        ("ok" if gl_count else "todo", "GL accounts", f"{gl_count} imported" if gl_count else "import them"),
        ("ok" if mapped == len(TAX_TYPES) else "todo", "Sales tax GL mapping", f"{mapped} of {len(TAX_TYPES)} set"),
        second,
    ]


def page_process() -> None:
    store = get_store()
    show_toast()
    settings = get_settings()
    steps = setup_steps(store, settings)
    # PDF text / OCR and GL accounts. OvisOCR2 reads them once it runs (they wait for it); the chat model is optional.
    ready = steps[0][0] != "bad" and steps[2][0] == "ok"
    page_head(
        "process",
        "Process invoices",
        "Read new invoices and send them to the review queue.",
        aside=ui.status("Ready to read invoices", "ok") if ready else ui.status("Setup not finished", "warn"),
    )

    left, right = st.columns([3, 2], gap="medium")
    with right, card("setup_steps"):
        needed = [s for s, _, _ in steps if s != "opt"]
        done = sum(1 for s in needed if s == "ok")
        head, gauge = st.columns([3, 1], vertical_alignment="top")
        head.markdown("#### Setup")
        head.caption("What AP Coder needs before it reads invoices.")
        progress = ui.pill(f"{done} of {len(needed)} done", "ok" if done == len(needed) else "gray")
        gauge.html(f"<div style='text-align:right'>{progress}</div>")
        st.html("".join(ui.step(s, label, state) for s, label, state in steps))
        if any(s != "ok" for s, _, _ in steps[2:4]):
            st.page_link(PAGES["accounts"], label="Finish setup", icon=":material/arrow_forward:")
        if steps[4][0] != "ok" and "settings" in PAGES:
            st.page_link(PAGES["settings"], label="Settings → Reading", icon=":material/arrow_forward:")

    recent = sorted(store.list_invoices(), key=lambda i: (i["created_at"] or "", i["id"]), reverse=True)[:6]
    if recent:
        with right, card("recent"):
            st.markdown("#### Recently processed")
            st.html("".join(ui.recent_row(i) for i in recent))
            if any(i["status"] == REVIEW for i in recent):
                st.page_link(PAGES["review"], label="Go to the review queue", icon=":material/arrow_forward:")

    with right:
        demo_card(store, "process")

    if PUBLIC_DEMO:
        with left, card("public_demo"):
            st.markdown("#### Processing new invoices")
            not_in_public_demo("Reading and coding new invoices")
            st.caption(
                "In your own copy, AP Coder reads each invoice the same way, whether a digital PDF, a scan or a phone "
                "photo, all on your computer: the PDF's own text or OCR, OvisOCR2 reading every page as a second "
                "reader, the supplier's template and the business checks. It then codes every line and sends the "
                "invoice to the review queue. The demo invoices went through the same checks, read and coded ahead "
                "of time, so you can review, correct and approve them."
            )
            st.page_link(PAGES["review"], label="Go to the review queue", icon=":material/arrow_forward:")
        return

    with left:
        with card("upload"):
            st.markdown("#### Upload invoices")
            st.caption("Each file is saved to the invoices folder on this computer, then read, coded and checked.")
            uploaded = st.file_uploader(
                "PDFs, scans or photos (TIFF, PNG, JPG), or saved emails (.eml) with invoices attached",
                type=sorted(e.lstrip(".") for e in SUPPORTED_EXTENSIONS | EMAIL_EXTENSIONS),
                accept_multiple_files=True,
                key=f"upload_{st.session_state.get('upload_round', 0)}",  # new key = empty uploader after a run
            )
            if uploaded and st.button(
                f"Process {ui.plural(len(uploaded), 'uploaded invoice')}", type="primary", icon=":material/play_arrow:",
                disabled=not ready,
            ):  # fmt: skip
                INVOICE_DIR.mkdir(parents=True, exist_ok=True)
                paths = []
                for f in uploaded:
                    try:  # one file that cannot be saved (a name Windows refuses, disk full) is said and skipped
                        target = save_upload(f.name, f.getvalue())
                        if target.suffix.lower() in EMAIL_EXTENSIONS:  # its invoice attachments, not the email
                            mail = unpack(target, INVOICE_DIR)
                            paths.extend(mail.saved)
                            st.session_state.setdefault("unpacked_emails", []).append(mail)
                            continue
                    except OSError as exc:
                        notify(f"{md(f.name)} could not be saved to the invoices folder "
                               f"({md(exc.strerror or type(exc).__name__)}). Rename it or save it there yourself.",
                               ":material/error:")  # fmt: skip
                        continue
                    paths.append(target)
                already, todo, hashes = [], [], set()
                for p in paths:  # skip files seen before, and repeats within this upload
                    digest = hashlib.sha256(p.read_bytes()).hexdigest()
                    if digest in hashes or store.find_by_hash(p) is not None:
                        already.append(p)
                    else:
                        todo.append(p)
                    hashes.add(digest)
                if todo:
                    run_pipeline(store, todo)
                if already:
                    notify(
                        f"Skipped {ui.plural(len(already), 'file')} already in AP Coder: "
                        + md(", ".join(p.name for p in already)),
                        ":material/content_copy:",
                    )
                st.session_state["upload_round"] = st.session_state.get("upload_round", 0) + 1
                st.rerun()

        with card("folder"):
            st.markdown("#### Invoices folder")
            hint, button = st.columns([2.3, 1], vertical_alignment="center")
            hint.caption(
                "Copy invoices into this folder, or have a scanner or mail rule save them there. New files are "
                "listed here, ready to process."
            )
            if button.button(
                "Open folder", icon=":material/folder_open:", key="open_invoices", width="stretch",
                help=f"{short_path(INVOICE_DIR)}  \n\nTo process new files automatically (e.g. overnight), whoever "
                "runs AP Coder can start the folder watcher: `python -m ap_coder watch`.",
            ):  # fmt: skip
                if not open_folder(INVOICE_DIR):
                    st.info(f"Open this folder yourself: {INVOICE_DIR}")
            unpacked = unpack_folder(INVOICE_DIR)  # saved emails dropped in the folder: their attachments
            if unpacked:
                st.session_state["unpacked_emails"] = [*st.session_state.get("unpacked_emails", []), *unpacked][-5:]
            for mail in st.session_state.get("unpacked_emails") or []:  # the last few, for this session
                _email_note(mail)
            files = invoice_files(INVOICE_DIR) if INVOICE_DIR.exists() else []
            new_files = [p for p in files if store.find_by_hash(p, include_failed=True) is None]
            if not new_files:
                st.html(ui.empty_note("No new files", "Everything in the folder has been processed.", "done_all"))
            else:
                rows = [
                    [f"{ui.icon('picture_as_pdf' if p.suffix.lower() == '.pdf' else 'image', '1.1em', '#c53030')} "
                     f"{esc(p.name)}", f"{p.stat().st_size / 1024:,.0f} KB"]
                    for p in new_files
                ]  # fmt: skip
                st.html(ui.table(["File", "Size"], rows, right=[1]))
                if st.button(
                    f"Process {ui.plural(len(new_files), 'file')}", type="primary", icon=":material/play_arrow:",
                    disabled=not ready,
                ):  # fmt: skip
                    run_pipeline(store, new_files)
                    st.rerun()
