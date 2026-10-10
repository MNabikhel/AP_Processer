"""Settings → Page reader: the vision model that reads each page as a second, independent reader (OvisOCR2 in LM
Studio by default). It is linked by a test on an invoice whose answers are known: until the model in use has passed
it, the page reader reads nothing."""

from __future__ import annotations

import dataclasses
import datetime as dt
import json

import pandas as pd
import streamlit as st

from ap_coder import page_reader, ui
from ap_coder.config import Settings, normalise_base_url
from ap_coder.page_worker import TEST_KEY, confirmed, figure_totals, saved_test
from ap_coder.store import Store
from ap_coder.webapp.common import PUBLIC_DEMO, card, esc, get_settings, not_in_public_demo, notify

MODES = {
    "auto": "In the background, after each invoice is processed (recommended)",
    "ask": "Only when I ask, from the invoice",
    "off": "Off: OCR only",
}
SCOPES = {
    "scans": "Scans and photos",
    "all": "Every invoice, digital PDFs too (more fields verified; slower)",
}
STATE_PILL = {
    "loaded": ("Ready: loaded in LM Studio", "ok", "check_circle"),
    "downloaded": ("Ready: LM Studio loads it when a page is read", "ok", "check_circle"),
    "missing": ("Not downloaded", "warn", "warning"),
    "off": ("Off", "gray", "block"),
    "down": ("LM Studio isn't answering", "err", "error"),
}
SETUP_STEPS = (
    "In LM Studio, search **OvisOCR2** and download the bartowski build at **Q8_0** (about 1 GB).",
    "No need to load it: AP Coder has LM Studio load it when it reads pages, next to the chat model.",
    "Press **Test the page reader** below: it reads an invoice whose answers are known and shows what it got. "
    "When it reads it right, the model is linked and starts reading invoices.",
)


def _status_html(status: page_reader.ReaderStatus) -> str:
    label, tone, icon = STATE_PILL.get(status.state, ("Unknown", "gray", "help"))
    model = f" <span class='apc-muted'>{esc(status.model)}</span>" if status.model else ""
    kind = " <span class='apc-muted'>· a document reader</span>" if status.document_reader else ""
    return ui.pill(label, tone, icon) + model + kind


def _test_html(test: dict | None, model: str) -> str:
    """The test that links the page reader: passed by the model in use, failed, or not run with it yet."""
    if not test:
        return ui.pill("Not tested yet", "gray", "pending") + (
            " <span class='apc-muted'>Test it once to confirm it reads invoices right on this computer; it reads "
            "nothing until then.</span>"
        )
    right, total = test.get("fields_right", 0), test.get("fields_total", 0)
    minutes = (test.get("seconds") or 0) / 60
    when = (test.get("when") or "")[:16].replace("T", " ")
    detail = f"{esc(test.get('model', ''))} read {right} of {total} fields right in {minutes:.1f} min · {esc(when)}"
    if model and test.get("model") != model:
        return ui.pill(f"Not tested with {model}", "warn", "pending") + (
            f" <span class='apc-muted'>The last test was another model ({detail}). Test this one to link it.</span>"
        )
    if test.get("ok"):
        return ui.pill("Linked: passed its test", "ok", "verified") + f" <span class='apc-muted'>{detail}</span>"
    return ui.pill("Failed its test: not used", "err", "error") + f" <span class='apc-muted'>{detail}</span>"


def _test_table(test: dict) -> None:
    rows = test.get("rows") or []
    if not rows:
        if test.get("problem"):
            st.caption(test["problem"])
        return
    frame = pd.DataFrame(
        [
            {
                "Field": r.get("field", ""),
                "On the invoice": r.get("expected", ""),
                "Page reader read": r.get("read", ""),
                "Match": "✓ right" if r.get("match") else "✗ different",
            }
            for r in rows
        ]
    )
    st.dataframe(frame, hide_index=True, width="stretch")
    if test.get("problem"):
        st.caption(test["problem"])


DOWNLOAD_KEY = "pr_download_job"  # the download LM Studio is doing for this session


def setup_steps(settings: Settings, status: page_reader.ReaderStatus, store: Store) -> list[tuple[str, str, str]]:
    """(state, step, detail) for the page reader's set-up, in order: "ok" when done, "todo" when it is next."""
    running = status.reachable and status.lm_studio
    downloaded = status.document_reader or any(page_reader.document_reader(m) for m in status.candidates)
    linked = confirmed(store, status.model)
    mode = settings.page_reader.mode
    reading = {"auto": "in the background, after each invoice", "ask": "when you ask, from the invoice",
               "off": "turned off (How it is used, below)"}[mode]  # fmt: skip
    return [
        ("ok" if running else "todo", "LM Studio is running",
         "its server answers" if running else "start LM Studio, then its server (Developer tab, Start server)"),
        ("ok" if downloaded else "todo", "OvisOCR2 is downloaded",
         "in LM Studio" if downloaded else "Download OvisOCR2 below (about 1 GB), or in LM Studio"),
        ("ok" if linked else "todo", "Tested and linked",
         f"{status.model} passed its test" if linked else "Test the page reader below (a few minutes)"),
        ("ok" if mode != "off" else "todo", "Reading invoices", reading),
    ]  # fmt: skip


def _download_button(settings: Settings) -> None:
    job = st.session_state.get(DOWNLOAD_KEY)
    if job:
        _download_progress(job)
        return
    if st.button("Download OvisOCR2", icon=":material/download:", key="pr_download", type="primary",
                 help="LM Studio downloads the bartowski build at Q8_0 (about 1 GB) from Hugging Face."):  # fmt: skip
        job, problem = page_reader.download_reader(settings)
        if problem:
            st.error(problem)
            return
        if job:
            st.session_state[DOWNLOAD_KEY] = job
        else:
            notify("OvisOCR2 is downloaded.", ":material/check_circle:")
        st.rerun()


@st.fragment(run_every=5)
def _download_progress(job: str) -> None:
    """LM Studio's download, looked up again every few seconds; the page is drawn again when it is done."""
    progress = page_reader.download_progress(get_settings(), job)
    if progress["status"] == "completed":
        st.session_state.pop(DOWNLOAD_KEY, None)
        notify("OvisOCR2 is downloaded. Next: Test the page reader.", ":material/check_circle:")
        st.rerun(scope="app")
    if progress["status"] == "failed":
        st.session_state.pop(DOWNLOAD_KEY, None)
        st.error("LM Studio couldn't finish the download: try again, or download OvisOCR2 in LM Studio itself.")
        return
    done, total = progress["done"], progress["total"]
    left = progress["seconds_left"]
    text = f"Downloading OvisOCR2: {done / 1e6:,.0f} of {total / 1e6:,.0f} MB" if total else "Downloading OvisOCR2…"
    if left:
        text += f", about {max(left / 60, 1):.0f} min left"
    st.progress(min(done / total, 1.0) if total else 0.0, text=text)


def lm_studio_models_card(settings: Settings, key: str) -> None:
    """Every model LM Studio has, whether it is loaded (and with what context) and what AP Coder uses it for: so the
    set-up can be checked at a glance."""
    with card(f"lm_models_{key}"):
        head, check = st.columns([3, 1], vertical_alignment="center")
        head.markdown("#### Models in LM Studio")
        head.caption("What LM Studio has downloaded, which are loaded now, and what AP Coder uses each one for.")
        fresh = check.button("Check again", icon=":material/refresh:", key=f"lm_models_check_{key}", width="stretch")
        rows = page_reader.lm_studio_models(settings, use_cache=not fresh)
        if rows is None:
            st.caption(
                ":material/error: LM Studio isn't answering: start it, then its server (Developer tab, Start server). "
                "Without it, invoices are still read by OCR and coded from what AP approved."
            )
            return
        if not rows:
            st.caption("LM Studio has no chat or vision model downloaded yet.")
            return
        frame = pd.DataFrame([{
            "Model": r["model"],
            "Loaded": (f"yes, {r['context']:,} tokens" if r["context"] else "yes") if r["loaded"] else "no",
            "Reads pages": "document reader" if r["document_reader"] else ("can see" if r["vision"] else "no"),
            "Used for": r["used_for"] or "—",
        } for r in rows])  # fmt: skip
        st.dataframe(frame, hide_index=True, width="stretch")
        # The AI model tab offers chat models first, the page reader's tab document readers first.
        unloaded = [r["model"] for r in sorted(rows, key=lambda r: r["document_reader"] == (key == "ai"))
                    if not r["loaded"]]  # fmt: skip
        if unloaded:
            pick, go = st.columns([3, 1], vertical_alignment="bottom")
            model = pick.selectbox("Load a model now", unloaded, key=f"lm_models_pick_{key}",
                                   help="A chat model with an 8,192-token context; the page reader with the context "
                                   "a page needs.")  # fmt: skip
            if go.button("Load", icon=":material/play_arrow:", key=f"lm_models_load_{key}", width="stretch"):
                with st.spinner(f"LM Studio is loading {model}…"):
                    problem = page_reader.load_model(settings, model)
                if problem:
                    st.error(problem)
                else:
                    notify(f"{model} is loaded.", ":material/check_circle:")
                    st.rerun()
        st.caption("A model that isn't loaded is loaded by LM Studio when AP Coder first asks it (the page reader with "
                   "the context a page needs).")  # fmt: skip


def page_reader_tab(store: Store) -> None:
    if PUBLIC_DEMO:
        with card("page_reader"):
            st.markdown("#### Page reader")
            not_in_public_demo("Connecting a page reader")
        return
    settings = get_settings()
    reader = settings.page_reader
    status = page_reader.reader_status(settings)

    with card("page_reader"):
        head, check = st.columns([3, 1], vertical_alignment="center")
        head.markdown("#### Page reader")
        head.caption(
            "A vision model in LM Studio that reads each page as a second, independent reader. Where it and OCR "
            "agree, a field can be verified; where they differ, it is marked Check. Nothing leaves this computer."
        )
        if check.button("Check again", icon=":material/refresh:", key="pr_check", width="stretch"):
            status = page_reader.reader_status(settings, use_cache=False)
        st.html(f"<div style='margin:.25rem 0'>{_status_html(status)}</div>")
        if status.note:
            st.caption(status.note)
        st.html(f"<div style='margin:.25rem 0 .5rem'>{_test_html(saved_test(store), status.model)}</div>")
        steps = setup_steps(settings, status, store)
        st.html("".join(ui.step(state, label, detail) for state, label, detail in steps))
        if steps[0][0] == "ok" and steps[1][0] != "ok":
            _download_button(settings)
        if not (status.reachable and status.lm_studio) or not status.document_reader:
            steps_md = "\n".join(f"{n}. {step}" for n, step in enumerate(SETUP_STEPS, start=1))
            with st.expander("Set it up by hand instead", expanded=False):
                st.markdown(steps_md)
        b1, b2, _ = st.columns([1, 1, 2])
        can_read = bool(status.model) and status.state in ("loaded", "downloaded")
        if b1.button("Load in LM Studio", icon=":material/download_for_offline:", key="pr_load",
                     disabled=not can_read or status.state == "loaded", width="stretch"):  # fmt: skip
            with st.spinner(f"LM Studio is loading {status.model}…"):
                problem = page_reader.load_reader(settings, status.model)
            if problem:
                st.error(problem)
            else:
                notify(f"{status.model} is loaded.", ":material/check_circle:")
                st.rerun()
        if b2.button("Test the page reader", icon=":material/fact_check:", key="pr_test", type="primary",
                     disabled=not can_read, width="stretch"):  # fmt: skip
            estimate = page_reader.page_seconds_estimate(settings)
            wait = f"about {estimate / 60:.0f} min" if estimate else "a few minutes on a laptop without a graphics card"
            with st.spinner(f"Reading the test invoice with {status.model}: {wait}…"):
                result = page_reader.test_reader(settings, model=status.model)
            record = dataclasses.asdict(result)
            record["when"] = record.get("when") or dt.datetime.now().isoformat(timespec="minutes")
            store.set_setting(TEST_KEY, json.dumps(record))
            store.log_event("page_reader_tested", detail={k: record.get(k) for k in
                            ("model", "ok", "fields_right", "fields_total", "seconds")})  # fmt: skip
            st.rerun()
        test = saved_test(store)
        if test:
            with st.expander("What it read on the test invoice", expanded=not test.get("ok")):
                _test_table(test)

    lm_studio_models_card(settings, "reader")

    with card("page_reader_settings"), st.form("page_reader_form", border=False):
        st.markdown("#### How it is used")
        models = ["", *status.candidates]
        if reader.model and reader.model not in models:
            models.append(reader.model)
        model = st.selectbox(
            "Model that reads pages", models, index=models.index(reader.model), key="pr_model",
            format_func=lambda m: m or "Automatic: a document reader (OvisOCR2) when downloaded, else the chat model "
            "if it can see",
            help="Vision models LM Studio has downloaded. Press Check again after downloading one.",
        )  # fmt: skip
        modes = list(MODES)
        mode = st.radio("When it reads", modes, index=modes.index(reader.mode), format_func=MODES.get, key="pr_mode")
        scopes = list(SCOPES)
        scope = st.radio("Which invoices", scopes, index=scopes.index(reader.scope), format_func=SCOPES.get,
                         key="pr_scope", horizontal=True)  # fmt: skip
        base_url = st.text_input(
            "Server address", reader.base_url, key="pr_base_url", placeholder="Same as the AI model",
            help="Leave empty to use the same LM Studio as the AI model.",
        )  # fmt: skip
        if st.form_submit_button("Save page reader settings", type="primary", icon=":material/save:"):
            from ap_coder.webapp.settings import save_settings

            updates = {}
            if model != reader.model:
                updates["AP_PAGE_READER_MODEL"] = model
            if mode != reader.mode:
                updates["AP_PAGE_READER"] = mode
            if scope != reader.scope:
                updates["AP_PAGE_READER_SCOPE"] = scope
            cleaned = normalise_base_url(base_url) if base_url.strip() else ""
            if cleaned != reader.base_url:
                updates["AP_PAGE_READER_BASE_URL"] = cleaned
            changed = save_settings(updates)
            page_reader.forget_reader_failures()
            notify(f"Saved {ui.plural(len(changed), 'change')}." if changed else "Nothing changed.", ":material/save:")
            st.rerun()

    with card("page_reader_models"):
        st.markdown("#### Models AP Coder uses")
        rows = page_reader.models_in_use(settings)
        st.dataframe(
            pd.DataFrame([{"Job": r["role"], "Model": r.get("model") or "—", "Status": r.get("status", "")}
                          for r in rows]),  # fmt: skip
            hide_index=True, width="stretch",
        )  # fmt: skip
        notes = [r["note"] for r in rows if r.get("note")]
        for note in notes:
            st.caption(note)

    with card("page_reader_queue"):
        st.markdown("#### Queue and speed")
        waiting = store.page_reads_waiting()
        estimate = page_reader.page_seconds_estimate(settings)
        speed = f"about {estimate / 60:.1f} min a page on this computer" if estimate else "not measured yet"
        st.markdown(
            f"**{ui.plural(waiting, 'invoice')} waiting** for the page reader · {speed}. It reads in the background "
            "while AP Coder is open; invoices stay in the review queue meanwhile, read by OCR."
        )
        if waiting and not confirmed(store, status.model):
            st.caption(":material/pause_circle: Waiting for a linked model: test the page reader above to start.")
        st.caption(
            "To read the queue overnight instead (Task Scheduler): `python -m ap_coder read-pages --minutes 240`."
        )
        totals = figure_totals(store)
        same, seen = totals.get("digital", (0, 0))
        if seen:
            st.markdown(
                f"**Checked on every digital PDF it reads:** it read {same:,} of {seen:,} figures the same as the "
                f"PDF's own text, which is exact ({same / seen:.1%})."
            )
        same, seen = totals.get("scans", (0, 0))
        if seen:
            st.markdown(
                f"**On scans and photos** it and OCR read {same:,} of {seen:,} figures the same ({same / seen:.1%}); "
                "where they differ on a field, the field is marked Check."
            )
        scorecard = {row["reader"]: row for row in store.reader_scorecard()}
        vlm = scorecard.get("vlm")
        if vlm and vlm.get("fields"):
            st.markdown(
                f"**How much to trust it so far:** it agreed with what AP approved on {vlm['agreed']} of "
                f"{vlm['fields']} fields ({vlm['rate']:.1%}), on {ui.plural(vlm.get('invoices', 0), 'invoice')}. "
                "Learning & accuracy → Readers compares every reader."
            )
        else:
            st.caption("Its record against AP's approvals shows here (and on Learning & accuracy → Readers) once "
                       "invoices it read are approved.")  # fmt: skip
