"""Settings → Reading: how AP Coder reads every invoice, the same way each time, and how each reader is doing.

There is nothing to choose here. Every invoice, a digital PDF, a scan or a phone photo, goes through the same
readers (``ap_coder.reading``); this tab shows each one's state, the page reader's (OvisOCR2 in LM Studio) self-test,
queue and record, and the models LM Studio has, for IT. Cloud services appear only in a developer build that may use
the internet (``AP_ALLOW_INTERNET=1``)."""

from __future__ import annotations

import functools
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from ap_coder import page_reader, ui
from ap_coder.config import Settings
from ap_coder.offline import internet_allowed
from ap_coder.page_worker import figure_totals, saved_test, start_test, test_under_way
from ap_coder.safe import md
from ap_coder.store import Store
from ap_coder.webapp.common import PUBLIC_DEMO, card, esc, get_settings, notify, reading_status_or_none

# The same steps for every invoice: nothing to configure.
HOW_IT_READS = (
    "**The page's own words.** A digital PDF's text layer is read as it is; a scan or a photo is read by OCR on "
    "this computer, with two engines.",
    "**OvisOCR2 reads every page too**, digital PDFs included, as a second reader that works on its own. It runs "
    "in LM Studio on this computer, in the background, and is trusted only after its self-test.",
    "**The rule reader, the supplier's template and the business checks** (totals, tax, GST/HST number, vendor "
    "master, purchase order, duplicates, bank account) compare what the readers found. A field they agree on is "
    "verified; one they read differently is marked Check.",
    "**A person decides** whenever a reader or a check is unsure. No invoice is approved without a person before "
    "OvisOCR2 has read it.",
)
READER_PILL = {
    "ok": ("Working", "ok", "check_circle"),
    "warn": ("Needs a look", "warn", "warning"),
    "off": ("Not working", "err", "error"),
}
SELF_TEST_PILL = {
    "passed": ("Self-test passed", "ok", "verified"),
    "failed": ("Self-test failed: not trusted", "err", "error"),
    "running": ("Self-test running", "info", "hourglass_top"),
    "pending": ("Self-test not run yet", "gray", "pending"),
    "no model": ("No self-test: OvisOCR2 isn't in LM Studio", "gray", "block"),
}
TEST_POLL_SECONDS = 5  # the self-test's progress is looked up again this often while it runs
FIRST_WAIT = "about 10–20 minutes on a laptop without a graphics card"  # before this computer has read a page


def readers_table(status: Any) -> str:
    """The readers of ``reading_status`` as a table: name, state pill, plain-English detail."""
    rows = []
    for line in status.readers:
        label, tone, icon = READER_PILL.get(line.state, ("Unknown", "gray", "help"))
        rows.append([f"<b>{esc(line.name)}</b>", ui.pill(label, tone, icon), esc(line.detail)])
    return ui.table(["Reader", "State", "What it does"], rows, wrap=[2])


def self_test_html(status: Any) -> str:
    label, tone, icon = SELF_TEST_PILL.get(status.self_test, (status.self_test or "Unknown", "gray", "help"))
    return ui.pill(label, tone, icon)


def queue_line(status: Any) -> str:
    """ "3 invoices waiting for OvisOCR2 · about 12 minutes", or that nothing waits."""
    if not status.queue:
        return "Nothing waiting for OvisOCR2: it has read every invoice processed so far."
    eta = f" · {page_reader.duration(status.eta_minutes * 60)} to read them" if status.eta_minutes else ""
    return f"**{ui.plural(status.queue, 'invoice')} waiting** for OvisOCR2{eta}."


def figures_line(status: Any) -> str:
    """On digital PDFs: how often OvisOCR2 read a figure as the PDF's own text has it ("" before it has read one)."""
    if status.figures_agree is None or not status.figures_count:
        return ""
    return (
        f"On digital PDFs, OvisOCR2 read **{status.figures_agree:.0%}** of figures the same as the PDF's own text "
        f"({status.figures_count:,} figures). The text layer is exact, so this is OvisOCR2's own accuracy."
    )


def _test_detail(test: dict) -> str:
    right, total = test.get("fields_right", 0), test.get("fields_total", 0)
    minutes = (test.get("seconds") or 0) / 60
    when = (test.get("when") or "")[:16].replace("T", " ")
    return f"Read {right} of {total} fields right in {minutes:.1f} min · {when}"


def _text(value: object) -> str:
    """A value as the test table shows it: always text (a column of text and numbers trips Streamlit's table)."""
    return "" if value is None else str(value)


def _test_table(test: dict) -> None:
    rows = test.get("rows") or []
    if not rows:
        if test.get("problem"):
            st.caption(md(test["problem"]))
        return
    frame = pd.DataFrame(
        [
            {
                "Field": _text(r.get("field", "")),
                "On the invoice": _text(r.get("expected")),
                "OvisOCR2 read": _text(r.get("read")),
                "Match": "✓ right" if r.get("match") else "✗ different",
            }
            for r in rows
        ]
    )
    st.dataframe(frame, hide_index=True, width="stretch")
    if test.get("problem"):
        st.caption(md(test["problem"]))


@functools.cache
def _test_page_count() -> int:
    """The pages of the test invoice the page reader reads (both pages of the sample: the totals are on the second)."""
    try:
        return max(1, page_reader._page_count(page_reader.TEST_SAMPLE.read_bytes(), True))
    except Exception:  # noqa: BLE001 - the sample missing or unreadable: the test itself says so
        return 2


def test_wait(settings: Settings, model: str) -> str:
    """How long the self-test should take on this computer: the time ``model`` takes a page here, times the test
    invoice's pages ("about 3 minutes"), or what a laptop takes before this one has read a page."""
    estimate = page_reader.page_seconds_estimate(settings, model=model)
    if not estimate:
        return FIRST_WAIT
    return page_reader.duration(estimate * _test_page_count())


test_wait.__test__ = False  # type: ignore[attr-defined]  # not a pytest test


def lm_studio_running(settings: Settings) -> bool:
    """LM Studio's server answers (cached briefly). False when it cannot be asked."""
    try:
        status = page_reader.reader_status(settings)
    except Exception:  # noqa: BLE001 - a status that can't be read is shown as not running
        return False
    return bool(status.reachable and status.lm_studio)


def chat_model(settings: Settings) -> tuple[bool, str]:
    """(ready, label) of the optional chat model that suggests GL accounts."""
    from ap_coder.local_llm import provider_status

    try:
        status = provider_status(settings)
    except Exception:  # noqa: BLE001 - shown as not loaded
        return False, ""
    return status.ready, status.label if status.ready else ""


def setup_steps(status: Any, running: bool, chat: tuple[bool, str]) -> list[tuple[str, str, str]]:
    """(state, step, detail) for a new computer, in order: "ok" when done, "todo" when it is next, "opt" optional."""
    found = bool(status.page_reader_model)
    if found:
        have = f"found: {status.page_reader_model}"
    elif not running:
        have = "start LM Studio first to check"
    else:
        have = "copy its two files into LM Studio's models folder (below)"
    test = {
        "passed": ("ok", "passed: OvisOCR2 is trusted"),
        "running": ("todo", "running now, by itself"),
        "failed": ("bad", "failed: OvisOCR2 isn't trusted until it passes (Run the self-test again)"),
    }.get(status.self_test, ("todo", "runs by itself once OvisOCR2 is found"))
    ready, label = chat
    return [
        ("ok" if running else "todo", "LM Studio is running",
         "its server answers" if running else "start LM Studio, then its server (Developer tab, Start server)"),
        ("ok" if found else "todo", "OvisOCR2 is in LM Studio", have),
        (test[0], "Self-test passed (automatic)", test[1]),
        ("ok" if ready else "opt", "Chat model for GL suggestions (optional)",
         label or "without one, lines are coded from what AP approved before"),
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
        "Then press **Check again**. LM Studio finds models in that folder on its own, AP Coder finds OvisOCR2 in "
        "LM Studio and its self-test starts by itself; nothing is downloaded.",
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
                "Without it, invoices are still read by the PDF's text and OCR, and coded from what AP approved."
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
        unloaded = [r["model"] for r in sorted(rows, key=lambda r: not r["document_reader"]) if not r["loaded"]]
        if unloaded:
            pick, go = st.columns([3, 1], vertical_alignment="bottom")
            model = pick.selectbox("Load a model now", unloaded, key=f"lm_models_pick_{key}",
                                   help="A chat model with an 8,192-token context; OvisOCR2 with the context a page "
                                   "needs.")  # fmt: skip
            if go.button("Load", icon=":material/play_arrow:", key=f"lm_models_load_{key}", width="stretch"):
                with st.spinner(f"LM Studio is loading {model}…"):
                    problem = page_reader.load_model(settings, model)
                if problem:
                    st.error(md(problem))
                else:
                    notify(f"{md(model)} is loaded.", ":material/check_circle:")
                    st.rerun()
        st.caption("A model that isn't loaded is loaded by LM Studio when AP Coder first asks it (OvisOCR2 with the "
                   "context a page needs).")  # fmt: skip


@st.fragment(run_every=TEST_POLL_SECONDS)
def _test_progress(store: Store, wait: str) -> None:
    """While the self-test runs (in the background): since when, and how many pages it has read; the tab is drawn
    again with the result once it is done."""
    testing = test_under_way(store)
    if not testing:
        st.rerun()
        return
    started = str(testing.get("started") or "")[11:16]
    done, pages = int(testing.get("done") or 0), int(testing.get("pages") or 0)
    progress = f" · {done} of {pages} pages read" if pages else ""
    st.caption(
        f":material/hourglass_top: Self-test of {md(testing.get('model', ''))}… started {started}{progress} ({wait}). "
        "It runs in the background: you can leave this page or close the browser, and its result is kept."
    )


def _status(settings: Settings, store: Store, fresh: bool) -> Any:
    """``reading_status``, or None (said on the page) when it can't be worked out just now."""
    if fresh:
        page_reader.forget_status()
        from ap_coder.local_llm import forget_status

        forget_status()
    status = reading_status_or_none(settings, store, use_cache=not fresh)
    if status is None:
        st.warning("AP Coder couldn't check the readers just now. Press **Check again** in a moment.",
                   icon=":material/sync_problem:")  # fmt: skip
    return status


def how_it_reads_card(settings: Settings, store: Store) -> Any:
    """The fixed explanation, then each reader's state now. Returns the reading status (None when unknown)."""
    with card("reading_how"):
        head, check = st.columns([3, 1], vertical_alignment="center")
        head.markdown("#### How AP Coder reads every invoice")
        head.caption("The same steps for a digital PDF, a scan or a phone photo. Nothing to set up or choose, and "
                     "nothing leaves this computer.")  # fmt: skip
        fresh = check.button("Check again", icon=":material/refresh:", key="rd_check", width="stretch")
        st.markdown("\n".join(f"{n}. {step}" for n, step in enumerate(HOW_IT_READS, start=1)))
        status = _status(settings, store, fresh)
        if status is not None and status.readers:
            st.html(readers_table(status))
    return status


def page_reader_card(settings: Settings, store: Store, status: Any) -> None:
    """OvisOCR2: found in LM Studio or not, its self-test (with Run the self-test again), its queue and record."""
    from ap_coder.local_llm import LM_STUDIO_STEPS

    running = lm_studio_running(settings)
    model = status.page_reader_model
    with card("reading_page_reader"):
        st.markdown("#### Page reader (OvisOCR2)")
        found = (
            ui.pill("Found in LM Studio", "ok", "check_circle") + f" <span class='apc-muted'>{esc(model)}</span>"
            if model
            else ui.pill("Not in LM Studio", "warn", "warning")
        )
        st.html(f"<div style='margin:.25rem 0'>{found}</div>")
        line, button = st.columns([3, 1], vertical_alignment="center")
        detail = f" <span class='apc-muted'>{esc(status.self_test_detail)}</span>" if status.self_test_detail else ""
        line.html(f"<div style='margin:.25rem 0'>{self_test_html(status)}{detail}</div>")
        testing = test_under_way(store)
        wait = test_wait(settings, model) if model else FIRST_WAIT
        if button.button("Run the self-test again", icon=":material/fact_check:", key="rd_selftest",
                         disabled=not model or bool(testing) or status.self_test == "running",
                         help="It reads an invoice whose answers are known; OvisOCR2 is trusted only when it reads "
                         "it right. It also runs by itself whenever OvisOCR2 is found or changes.",
                         width="stretch"):  # fmt: skip
            if start_test(settings, store, model):
                notify(f"Self-test of {md(model)} started in the background ({wait}).", ":material/fact_check:")
            else:
                notify("A self-test is already running: one at a time.", ":material/hourglass_top:")
            st.rerun()
        if testing:
            _test_progress(store, wait)
        test = saved_test(store, model) if model else None
        if test:
            with st.expander(f"What it read on the test invoice ({_test_detail(test)})", expanded=not test.get("ok")):
                _test_table(test)
        if not model and running:
            _copy_in_note()
        elif not running:
            steps = "\n".join(f"{n}. {step}" for n, step in enumerate(LM_STUDIO_STEPS, start=1))
            with st.container(key="note_lmstudio"):
                st.markdown(f"**To start LM Studio on this computer**\n\n{steps}\n\nThen press **Check again**.")

        st.markdown(queue_line(status))
        st.caption("It reads in the background while AP Coder is open; invoices wait in the review queue meanwhile. "
                   "To read the queue overnight instead (Task Scheduler): "
                   "`python -m ap_coder read-pages --minutes 240`.")  # fmt: skip
        figures = figures_line(status)
        if figures:
            st.markdown(figures)
        same, seen = figure_totals(store).get("scans", (0, 0))
        if seen:
            st.markdown(
                f"On scans and photos, OvisOCR2 and OCR read {same:,} of {seen:,} figures the same "
                f"({same / seen:.0%}); where they differ on a field, the field is marked Check."
            )
        vlm = {row["reader"]: row for row in store.reader_scorecard()}.get("vlm")
        if vlm and vlm.get("fields"):
            st.markdown(
                f"Against what AP approved, OvisOCR2 agreed on {vlm['agreed']} of {vlm['fields']} fields "
                f"({vlm['rate']:.1%}), on {ui.plural(vlm.get('invoices', 0), 'invoice')}. Learning & accuracy → "
                "Readers compares every reader."
            )


def setup_card(settings: Settings, status: Any) -> None:
    with card("reading_setup"):
        st.markdown("#### On a new computer")
        st.caption("What AP Coder needs on this computer. Everything after the first two steps happens by itself.")
        steps = setup_steps(status, lm_studio_running(settings), chat_model(settings))
        st.html("".join(ui.step(state, label, detail) for state, label, detail in steps))


def reading_tab(store: Store) -> None:
    settings = get_settings()
    if PUBLIC_DEMO:
        with card("reading_how"):
            st.markdown("#### How AP Coder reads every invoice")
            st.caption("The same steps for a digital PDF, a scan or a phone photo.")
            st.markdown("\n".join(f"{n}. {step}" for n, step in enumerate(HOW_IT_READS, start=1)))
            st.caption(":material/science: The demo invoices were read this way ahead of time: the public demo runs "
                       "no readers of its own.")  # fmt: skip
        return
    status = how_it_reads_card(settings, store)
    if status is not None:
        page_reader_card(settings, store, status)
        setup_card(settings, status)
    lm_studio_models_card(settings, "reading")
    if internet_allowed():
        from ap_coder.webapp.settings import azure_tab

        with st.expander("Developer: cloud services", icon=":material/cloud:"):
            st.caption("Used only in a developer build that may use the internet (AP_ALLOW_INTERNET=1). The offline "
                       "build ignores these settings.")  # fmt: skip
            azure_tab(store)
