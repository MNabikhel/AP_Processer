"""Process invoices: setup checklist, upload, folder pick-up, recently processed."""

from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

import streamlit as st

from ap_coder import ui
from ap_coder.config import Settings
from ap_coder.extraction import SUPPORTED_EXTENSIONS
from ap_coder.mailbox import EMAIL_EXTENSIONS, unpack, unpack_folder
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
    short_path,
    show_toast,
)


def safe_file_name(name: str) -> str:
    """Just the file name of an upload (no folders, Windows or POSIX), never empty or a dot name."""
    base = PureWindowsPath(PurePosixPath(name or "").name).name.strip().lstrip(".")
    return base or "invoice"


# --- Process invoices --------------------------------------------------------------------------------------


def _email_note(mail: Any) -> None:
    """What was taken out of a saved email, and what was left out and why."""
    took = f"{len(mail.saved)} attachment(s) taken out" if mail.saved else "no invoice attached"
    st.caption(f":material/mail: **{md(mail.email)}**: {took}; the email is in the `emails` subfolder.")
    if mail.skipped:
        st.caption("Left out: " + md("; ".join(mail.skipped)))


def run_pipeline(store: Store, paths: list[Path]) -> None:
    if PUBLIC_DEMO:  # no Azure in the public demo
        not_in_public_demo("Reading invoices with Azure")
        return
    reference = store.reference_data()
    settings = get_settings()
    pipeline = InvoicePipeline(settings, reference, cache_dir=CACHE_DIR, store=store)
    ok = 0
    with st.status(f"Processing {len(paths)} invoice(s)…", expanded=True) as status:
        for n, path in enumerate(paths, start=1):
            st.write(f":material/document_scanner: Reading and coding **{path.name}** ({n}/{len(paths)})")
            result = pipeline.process(path)
            if result.ok:
                ok += 1
                flag = "needs attention" if result.report and result.report.requires_review else "ready"
                st.write(f":material/check_circle: {result.output.get('vendor_name', path.name)}: {flag}")
            else:
                st.error(f"{md(path.name)}: {md(result.error)}", icon=":material/error:")
        status.update(label=f"Processed {ok} of {len(paths)}", state="complete" if ok == len(paths) else "error")
    failed = len(paths) - ok
    if ok:
        notify(f"{ok} invoice(s) read and coded. They're waiting in the review queue.", ":material/inbox:")
    if failed:
        notify(f"{failed} file(s) could not be processed. See Review queue → Failed / rejected.", ":material/error:")


def tax_types_mapped(store: Store) -> int:
    """Tax types ready to post: added to expense lines, or mapped to a GL account that still exists."""
    codes = {a["code"] for a in store.list_accounts("gl_accounts")}
    return sum(1 for t in store.tax_treatments().values() if not t.needs_gl or t.gl_code in codes)


def setup_steps(store: Store, settings: Settings) -> list[tuple[str, str, str]]:
    mapped = tax_types_mapped(store)
    gl_count = len(store.list_accounts("gl_accounts"))
    return [
        (
            "ok" if settings.document_intelligence.endpoint else "bad",
            "Azure Document Intelligence",
            "connected" if settings.document_intelligence.endpoint else "set it up in Settings → Azure",
        ),
        (
            "ok" if settings.openai.endpoint else "bad",
            "Azure OpenAI",
            f"{settings.openai.deployment}" if settings.openai.endpoint else "set it up in Settings → Azure",
        ),
        ("ok" if gl_count else "todo", "GL accounts", f"{gl_count} imported" if gl_count else "import them"),
        ("ok" if mapped == len(TAX_TYPES) else "todo", "Sales tax GL mapping", f"{mapped} of {len(TAX_TYPES)} set"),
    ]


def page_process() -> None:
    store = get_store()
    show_toast()
    st.html(
        ui.page_header("Inbox", "Process invoices", "Read new invoices with Azure and send them to the review queue.")
    )
    settings = get_settings()
    steps = setup_steps(store, settings)
    ready = all(s == "ok" for s, _, _ in steps[:3])

    left, right = st.columns([3, 2], gap="medium")
    with right, card("setup_steps"):
        done = sum(1 for s, _, _ in steps if s == "ok")
        head, gauge = st.columns([3, 1], vertical_alignment="center")
        head.markdown("#### :material/checklist: Setup")
        head.caption("Everything the engine needs before it can read invoices.")
        gauge.html(ui.ring(done / len(steps), size=56, stroke=6, label=f"{done}/{len(steps)}"))
        st.html("".join(ui.step(s, label, state) for s, label, state in steps))
        if any(s != "ok" for s, _, _ in steps[2:]):
            st.page_link(PAGES["accounts"], label="Finish setup", icon=":material/arrow_forward:")

    recent = sorted(store.list_invoices(), key=lambda i: (i["created_at"] or "", i["id"]), reverse=True)[:6]
    if recent:
        with right, card("recent"):
            st.markdown("#### :material/history: Recently processed")
            st.html("".join(ui.recent_row(i) for i in recent))
            if any(i["status"] == REVIEW for i in recent):
                st.page_link(PAGES["review"], label="Go to the review queue", icon=":material/arrow_forward:")

    with right:
        demo_card(store, "process")

    if PUBLIC_DEMO:
        with left, card("public_demo"):
            st.markdown("#### :material/cloud_off: Processing new invoices")
            not_in_public_demo("Reading and coding new invoices with Azure")
            st.caption(
                "In your own copy, AP Coder reads each PDF or scan with Azure Document Intelligence, codes every "
                "line with Azure OpenAI and sends it to the review queue. The demo invoices went through the same "
                "checks, coded ahead of time, so you can review, correct and approve them."
            )
            st.page_link(PAGES["review"], label="Go to the review queue", icon=":material/arrow_forward:")
        return

    with left:
        with card("upload"):
            st.markdown("#### :material/upload_file: Upload invoices")
            uploaded = st.file_uploader(
                "Drop PDFs, TIFFs, PNGs or JPGs here, or saved emails (.eml) with invoices attached. They are saved "
                "to your private invoices folder on this computer.",
                type=sorted(e.lstrip(".") for e in SUPPORTED_EXTENSIONS | EMAIL_EXTENSIONS),
                accept_multiple_files=True,
                key=f"upload_{st.session_state.get('upload_round', 0)}",  # new key = empty uploader after a run
            )
            if uploaded and st.button(
                f"Process {len(uploaded)} uploaded invoice(s)", type="primary", icon=":material/play_arrow:",
                disabled=not ready,
            ):  # fmt: skip
                INVOICE_DIR.mkdir(parents=True, exist_ok=True)
                paths = []
                for f in uploaded:
                    target = INVOICE_DIR / safe_file_name(f.name)
                    stem, n = target.stem, 1
                    while target.exists() and target.read_bytes() != f.getvalue():
                        target = INVOICE_DIR / f"{stem}_{n}{target.suffix}"
                        n += 1
                    target.write_bytes(f.getvalue())
                    if target.suffix.lower() in EMAIL_EXTENSIONS:  # its invoice attachments, not the email
                        mail = unpack(target, INVOICE_DIR)
                        paths.extend(mail.saved)
                        st.session_state.setdefault("unpacked_emails", []).append(mail)
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
                        f"Skipped {len(already)} file(s) already in AP Coder: {', '.join(p.name for p in already)}",
                        ":material/content_copy:",
                    )
                st.session_state["upload_round"] = st.session_state.get("upload_round", 0) + 1
                st.rerun()

        with card("folder"):
            st.markdown("#### :material/folder_open: Invoices folder")
            hint, button = st.columns([2.6, 1.4], vertical_alignment="center")
            hint.caption(
                f"Copy files into `{short_path(INVOICE_DIR)}` and they appear here. To process them automatically "
                "(e.g. overnight, or from a scanner or mail rule saving into this folder), run "
                "`python -m ap_coder watch` in the AP Coder terminal."
            )
            if button.button("Open folder", icon=":material/folder_open:", key="open_invoices"):
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
                st.html(ui.pill("No new files", "gray", "done_all"))
            else:
                rows = [
                    [f"{ui.icon('picture_as_pdf' if p.suffix.lower() == '.pdf' else 'image', '1.1em', '#c53030')} "
                     f"{esc(p.name)}", f"{p.stat().st_size / 1024:,.0f} KB"]
                    for p in new_files
                ]  # fmt: skip
                st.html(ui.table(["File", "Size"], rows, right=[1]))
                if st.button(
                    f"Process {len(new_files)} file(s)", type="primary", icon=":material/play_arrow:",
                    disabled=not ready,
                ):  # fmt: skip
                    run_pipeline(store, new_files)
                    st.rerun()
