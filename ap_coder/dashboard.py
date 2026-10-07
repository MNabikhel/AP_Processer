"""AP Coder review dashboard (Streamlit). Runs locally: ``python -m ap_coder dashboard``.

Pages
-----
* Review queue: welcome banner, today's numbers and clickable invoice cards; opening one switches to
  a focused review (keyboard shortcuts, invoice summary, spend breakdown, live checks, editable lines
  and taxes, GL posting preview) with a sticky approve bar. Approving teaches the memory.
* Process invoices: setup checklist, upload or pick up new files in private/invoices.
* GL accounts & tax: import / view / edit / delete GL accounts (cost codes) and cost centers with
  your own category column, map each sales tax to its GL, edit coding policy notes.
* Learning & accuracy: accuracy vs target, trend, recent lessons, vendors, corrections and the memory.

Each page lives in ``ap_coder/webapp/<page>.py``; this file is the app shell (theme, sidebar,
navigation). Visual building blocks live in ``ui.py`` and ``assets/style.css``. All data stays in
the local SQLite database in the data folder (see ``ap_coder/paths.py``).
"""

from __future__ import annotations

import streamlit as st

from ap_coder import ui
from ap_coder.demo import is_demo
from ap_coder.store import REVIEW
from ap_coder.webapp.accounts import page_accounts
from ap_coder.webapp.common import ASSETS, DB_PATH, PAGES, approved_today, get_store, reviewer, short_path
from ap_coder.webapp.learning import page_learning
from ap_coder.webapp.process import page_process
from ap_coder.webapp.review import page_review
from ap_coder.webapp.settings import page_settings

st.set_page_config(page_title="AP Coder", page_icon=str(ASSETS / "icon.svg"), layout="wide")
st.html(f"<style>{(ASSETS / 'style.css').read_text(encoding='utf-8')}</style>")

PAGES.update(
    {
        "review": st.Page(page_review, title="Review queue", icon=":material/inbox:", default=True),
        "process": st.Page(page_process, title="Process invoices", icon=":material/upload_file:"),
        "accounts": st.Page(page_accounts, title="GL accounts & tax", icon=":material/account_tree:"),
        "learning": st.Page(page_learning, title="Learning & accuracy", icon=":material/insights:"),
        "settings": st.Page(page_settings, title="Settings", icon=":material/settings:"),
    }
)

st.logo(str(ASSETS / "logo.svg"), size="large", icon_image=str(ASSETS / "icon.svg"))
with st.sidebar:
    _store = get_store()
    st.html(ui.sidebar_profile(reviewer(), approved_today(_store), len(_store.list_invoices(REVIEW))))
    if any(is_demo(i) for i in _store.list_invoices_full()):
        st.html(ui.pill("Demo invoices loaded", "violet", "science"))
    st.caption(f":material/lock: Runs on this computer only · `{short_path(DB_PATH)}`")

st.navigation(list(PAGES.values())).run()
