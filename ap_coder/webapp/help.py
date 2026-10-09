"""Help: quick start, what every check means and what to do about it, FAQ, shortcuts."""

from __future__ import annotations

import re

import streamlit as st

from ap_coder import ui
from ap_coder.help import AREAS, CHECKS, FAQ, QUICK_START, ROUTINE
from ap_coder.paths import PROJECT_DIR
from ap_coder.webapp.common import DB_PATH, PAGES, card, esc, page_head, short_path, show_toast

STEP_PAGES = ["accounts", "purchase_orders", "process", "review", "exports"]
SHORTCUTS = [
    (("Ctrl", "Enter"), "Approve & teach the open invoice"),
    (("Alt", "→"), "Next invoice"),
    (("Alt", "←"), "Previous invoice"),
    (("Alt", "↑"), "Back to the queue"),
]


def checks_table(query: str = "", area: str | None = None) -> tuple[str, int]:
    """The check catalog as an HTML table, filtered by a search text and an area. Returns (html, count)."""
    pattern = re.compile(re.escape(query.strip()), re.IGNORECASE) if query.strip() else None
    rows = []
    for code, about in CHECKS.items():
        if area and about.area != area:
            continue
        if pattern and not pattern.search(" ".join((code, about.title, about.meaning, about.action))):
            continue
        rows.append(
            [
                f"<b>{esc(about.title)}</b><div class='apc-muted' style='font-family:ui-monospace,Consolas,"
                f"monospace;font-size:.72rem'>{esc(code)}</div>",
                esc(about.meaning),
                esc(about.action),
            ]
        )
    if not rows:
        return "", 0
    return ui.table(["Check", "What it means", "What to do"], rows, wrap=[0, 1, 2]), len(rows)


def page_help() -> None:
    show_toast()
    action = page_head(
        "help", "Help", "How AP Coder works, what each check means and what to do about it.", action=True
    )
    if "review" in PAGES:
        action.page_link(PAGES["review"], label="Go to the review queue", icon=":material/inbox:")

    news = PROJECT_DIR / "docs" / "WHATS_NEW.md"
    if news.exists():
        with st.expander("What's new in AP Coder", icon=":material/new_releases:"):
            text = news.read_text(encoding="utf-8")
            text = text.split("\n", 1)[1] if text.startswith("# ") else text  # without its own title
            st.markdown(re.sub(r"\[([^\]]+)\]\([^)]*\.md[^)]*\)", r"\1", text))  # links to other docs: plain text

    with card("help_start"):
        st.markdown("#### :material/flag: Getting started")
        for i, ((title, text), page) in enumerate(zip(QUICK_START, STEP_PAGES, strict=True), start=1):
            c1, c2 = st.columns([4, 1.3], vertical_alignment="center")
            c1.html(
                f"<div style='display:flex;gap:.7rem;align-items:flex-start'><span class='apc-step-no' "
                f"style='flex:none;width:1.6rem;height:1.6rem;border-radius:50%;background:var(--apc-brand-50);color:var(--apc-brand);"
                f"display:grid;place-items:center;font-weight:700;font-size:.85rem'>{i}</span><div><b>{esc(title)}"
                f"</b><div class='apc-muted'>{esc(text)}</div></div></div>"
            )
            if page in PAGES:
                c2.page_link(PAGES[page], label="Open", icon=":material/arrow_forward:")

    with card("help_routine"):
        st.markdown("#### :material/checklist: Your AP routine")
        columns = st.columns(len(ROUTINE))
        for col, (when, tasks) in zip(columns, ROUTINE, strict=True):
            col.markdown(f"**{when}**")
            for task, page in tasks:
                if page in PAGES:
                    col.page_link(PAGES[page], label=task, icon=":material/arrow_right:")
                else:
                    col.markdown(f"- {task}")

    with card("help_checks"):
        st.markdown("#### :material/fact_check: What the checks mean")
        st.caption(
            "Every invoice is checked before you see it. Errors must be fixed (or overridden), warnings deserve "
            "a look, and 'good to know' notes never block anything."
        )
        query = st.text_input("Search the checks", placeholder="Search, e.g. duplicate, QST, PO", key="help_search",
                              label_visibility="collapsed")  # fmt: skip
        area = st.pills("Area", AREAS, key="help_area", label_visibility="collapsed")
        html, count = checks_table(query, area)
        if count:
            st.html(html)
        else:
            st.caption("No check matches. Try another word.")

    left, right = st.columns([3, 2], gap="medium")
    with left, card("help_faq"):
        st.markdown("#### :material/help: Questions")
        for question, answer in FAQ:
            with st.expander(question):
                st.markdown(answer)
    with right:
        with card("help_keys"):
            st.markdown("#### :material/keyboard: Shortcuts")
            st.html(
                ui.table(
                    ["", ""],
                    [[" + ".join(ui.kbd(k) for k in keys), esc(what)] for keys, what in SHORTCUTS],
                    wrap=[1],
                )
            )
        with card("help_data"):
            st.markdown("#### :material/lock: Where your data is")
            st.html(
                ui.table(
                    ["", ""],
                    [
                        ["Database", f"<code>{esc(short_path(DB_PATH))}</code>"],
                        ["Backups", f"<code>{esc(short_path(DB_PATH.parent / 'backups'))}</code>"],
                        ["Azure keys", "Settings → Azure (kept in <code>.env</code> in the data folder)"],
                    ],
                    wrap=[1],
                )
            )
            st.caption("Nothing is sent anywhere except your own Azure resources.")
