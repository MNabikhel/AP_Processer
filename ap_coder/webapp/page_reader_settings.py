"""Settings → Page reader: the vision model that reads each page as a second, independent reader (OvisOCR2 in LM
Studio by default). It is linked by a test on an invoice whose answers are known: until the model in use has passed
it, the page reader reads nothing."""

from __future__ import annotations

import functools
from pathlib import Path

import pandas as pd
import streamlit as st

from ap_coder import page_reader, ui
from ap_coder.config import Settings, normalise_base_url
from ap_coder.page_worker import confirmed, figure_totals, saved_test, saved_tests, start_test, test_under_way
from ap_coder.safe import md
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
    "blind": ("Can't look at pictures: can't read pages", "err", "visibility_off"),
    "off": ("Off", "gray", "block"),
    "down": ("LM Studio isn't answering", "err", "error"),
}
TEST_POLL_SECONDS = 5  # the test's progress is looked up again this often while it runs
FIRST_WAIT = "about 10–20 minutes on a laptop without a graphics card"  # before this computer has read a page
SETUP_STEPS = (
    "Get OvisOCR2's two files from IT (the bartowski build at **Q8_0**: `ATH-MaaS_OvisOCR2-Q8_0.gguf` and "
    "`mmproj-ATH-MaaS_OvisOCR2-f16.gguf`, about 1 GB) and copy them into LM Studio's models folder, under "
    "`bartowski/ATH-MaaS_OvisOCR2-GGUF`. Nothing is downloaded on this computer.",
    "No need to load it: AP Coder has LM Studio load it when it reads pages, next to the chat model.",
    "Press **Test the page reader** below: it reads an invoice whose answers are known and shows what it got. "
    "When it reads it right, the model is linked and starts reading invoices.",
)


def _status_html(status: page_reader.ReaderStatus) -> str:
    label, tone, icon = STATE_PILL.get(status.state, ("Unknown", "gray", "help"))
    model = f" <span class='apc-muted'>{esc(status.model)}</span>" if status.model else ""
    kind = " <span class='apc-muted'>· a document reader</span>" if status.document_reader else ""
    return ui.pill(label, tone, icon) + model + kind


def _test_detail(test: dict) -> str:
    right, total = test.get("fields_right", 0), test.get("fields_total", 0)
    minutes = (test.get("seconds") or 0) / 60
    when = (test.get("when") or "")[:16].replace("T", " ")
    return f"{esc(test.get('model', ''))} read {right} of {total} fields right in {minutes:.1f} min · {esc(when)}"


def _test_html(test: dict | None, model: str, others: list[dict] | None = None) -> str:
    """The test that links the page reader: passed by the model in use, failed, or not run with it yet. Each model
    keeps its own test: ``others`` are the other models tested (one that passed stays linked)."""
    if not test:
        if others and model:
            return ui.pill(f"Not tested with {model}", "warn", "pending") + (
                " <span class='apc-muted'>Each model is tested once on this computer before it reads invoices: "
                "test this one to link it.</span>"
            )
        return ui.pill("Not tested yet", "gray", "pending") + (
            " <span class='apc-muted'>Test it once to confirm it reads invoices right on this computer; it reads "
            "nothing until then.</span>"
        )
    detail = _test_detail(test)
    if test.get("ok"):
        return ui.pill("Linked: passed its test", "ok", "verified") + f" <span class='apc-muted'>{detail}</span>"
    return ui.pill("Failed its test: not used", "err", "error") + f" <span class='apc-muted'>{detail}</span>"


def others_tested(tests: dict[str, dict], model: str) -> str:
    """The other models tested on this computer, each with its own result ("" when none)."""
    parts = []
    for name, test in sorted(tests.items(), key=lambda kv: str(kv[1].get("when") or ""), reverse=True):
        if name != model:
            right, total = test.get("fields_right", 0), test.get("fields_total", 0)
            verdict = "passed, linked" if test.get("ok") else "failed"
            parts.append(f"{md(name)} {verdict} ({right} of {total} fields right)")
    return ("Also tested here: " + "; ".join(parts) + ".") if parts else ""


def _text(value: object) -> str:
    """A value as the test table shows it: always text (a column of text and numbers trips Streamlit's table)."""
    return "" if value is None else str(value)


def _test_table(test: dict) -> None:
    rows = test.get("rows") or []
    if not rows:
        if test.get("problem"):
            st.caption(test["problem"])
        return
    frame = pd.DataFrame(
        [
            {
                "Field": _text(r.get("field", "")),
                "On the invoice": _text(r.get("expected")),
                "Page reader read": _text(r.get("read")),
                "Match": "✓ right" if r.get("match") else "✗ different",
            }
            for r in rows
        ]
    )
    st.dataframe(frame, hide_index=True, width="stretch")
    if test.get("problem"):
        st.caption(test["problem"])


@functools.cache
def _test_page_count() -> int:
    """The pages of the test invoice the page reader reads (both pages of the sample: the totals are on the second)."""
    try:
        return max(1, page_reader._page_count(page_reader.TEST_SAMPLE.read_bytes(), True))
    except Exception:  # noqa: BLE001 - the sample missing or unreadable: the test itself says so
        return 2


def test_wait(settings: Settings, model: str) -> str:
    """How long the test should take on this computer: the time ``model`` takes a page here, times the test
    invoice's pages ("about 3 minutes"), or what a laptop takes before this one has read a page."""
    estimate = page_reader.page_seconds_estimate(settings, model=model)
    if not estimate:
        return FIRST_WAIT
    return page_reader.duration(estimate * _test_page_count())


test_wait.__test__ = False  # type: ignore[attr-defined]  # not a pytest test


def setup_steps(settings: Settings, status: page_reader.ReaderStatus, store: Store) -> list[tuple[str, str, str]]:
    """(state, step, detail) for the page reader's set-up, in order: "ok" when done, "todo" when it is next."""
    running = status.reachable and status.lm_studio
    downloaded = status.document_reader or any(page_reader.document_reader(m) for m in status.candidates)
    linked = confirmed(store, status.model)
    mode = settings.page_reader.mode
    reading = {"auto": "in the background, after each invoice", "ask": "when you ask, from the invoice",
               "off": "turned off (How it is used, below)"}[mode]  # fmt: skip
    if mode != "off" and not linked:  # it reads nothing until a model has passed its test
        reading = "will read " + ("in the background" if mode == "auto" else "when you ask") + " once linked"
    if downloaded:
        have = "in LM Studio"
    elif not running:  # it cannot be asked: the model may well be there
        have = "LM Studio isn't answering: start it to check"
    else:
        have = "copy its two files into LM Studio's models folder (below)"
    return [
        ("ok" if running else "todo", "LM Studio is running",
         "its server answers" if running else "start LM Studio, then its server (Developer tab, Start server)"),
        ("ok" if downloaded else "todo", "OvisOCR2 is downloaded", have),
        ("ok" if linked else "todo", "Tested and linked",
         f"{status.model} passed its test" if linked else "Test the page reader below (10–20 minutes)"),
        ("ok" if mode != "off" and linked else "todo", "Reading invoices", reading),
    ]  # fmt: skip


def models_folder() -> Path:
    """Where LM Studio keeps its models (its default: .lmstudio/models in the user's folder)."""
    return Path.home() / ".lmstudio" / "models"


def _copy_in_note() -> None:
    """Offline: the model files come from IT (downloaded once elsewhere), never from the internet."""
    folder = models_folder() / "bartowski" / "ATH-MaaS_OvisOCR2-GGUF"
    st.info(
        "**Add OvisOCR2 without the internet.** Copy its two files, from your IT team's model share or a USB "
        f"stick, into this folder (make it if it isn't there):\n\n`{folder}`\n\n"
        "- `ATH-MaaS_OvisOCR2-Q8_0.gguf` (813 MB)\n- `mmproj-ATH-MaaS_OvisOCR2-f16.gguf` (205 MB)\n\n"
        "Then press **Check again**. LM Studio finds models in that folder on its own; nothing is downloaded.",
        icon=":material/usb:",
    )


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


@st.fragment(run_every=TEST_POLL_SECONDS)
def _test_progress(store: Store, wait: str) -> None:
    """While the page reader is tested (in the background): since when, and how many pages it has read; the tab is
    drawn again with the result once it is done."""
    testing = test_under_way(store)
    if not testing:
        st.rerun()
        return
    started = str(testing.get("started") or "")[11:16]
    done, pages = int(testing.get("done") or 0), int(testing.get("pages") or 0)
    progress = f" · {done} of {pages} pages read" if pages else ""
    st.caption(
        f":material/hourglass_top: Testing {md(testing.get('model', ''))}… started {started}{progress} ({wait}). It "
        "runs in the background: you can leave this page or close the browser, and its result is kept when it is done."
    )


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
        tests = saved_tests(store)
        test = tests.get(status.model) if status.model else saved_test(store)
        others = [t for name, t in tests.items() if name != status.model]
        st.html(f"<div style='margin:.25rem 0 .5rem'>{_test_html(test, status.model, others)}</div>")
        if others and status.model:
            st.caption(others_tested(tests, status.model))
        steps = setup_steps(settings, status, store)
        st.html("".join(ui.step(state, label, detail) for state, label, detail in steps))
        if steps[0][0] == "ok" and steps[1][0] != "ok":
            _copy_in_note()
        if not (status.reachable and status.lm_studio) or not status.document_reader:
            steps_md = "\n".join(f"{n}. {step}" for n, step in enumerate(SETUP_STEPS, start=1))
            with st.expander("Set it up by hand instead", expanded=False):
                st.markdown(steps_md)
        b1, b2, _ = st.columns([1, 1, 2])
        can_read = status.usable  # a model that can't look at pictures is neither loaded nor tested from here
        if b1.button("Load in LM Studio", icon=":material/download_for_offline:", key="pr_load",
                     disabled=not can_read or status.state == "loaded", width="stretch"):  # fmt: skip
            with st.spinner(f"LM Studio is loading {status.model}…"):
                problem = page_reader.load_reader(settings, status.model)
            if problem:
                st.error(problem)
            else:
                notify(f"{status.model} is loaded.", ":material/check_circle:")
                st.rerun()
        testing = test_under_way(store)
        wait = test_wait(settings, status.model) if status.model else FIRST_WAIT
        if b2.button("Test the page reader", icon=":material/fact_check:", key="pr_test", type="primary",
                     disabled=not can_read or bool(testing), width="stretch"):  # fmt: skip
            # In a thread of its own: its result is kept even if the browser is closed meanwhile.
            if start_test(settings, store, status.model):
                notify(f"Testing {status.model} in the background ({wait}).", ":material/fact_check:")
            else:
                notify("A test of the page reader is already under way: one at a time.", ":material/hourglass_top:")
            st.rerun()
        if testing:
            _test_progress(store, wait)
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
