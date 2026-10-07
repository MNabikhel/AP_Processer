"""The public demo on the web: the normal dashboard with the made-up demo invoices (``streamlit_app.py``).

Everything here only runs when ``PUBLIC_DEMO`` is on (``AP_PUBLIC_DEMO=1``, set by ``streamlit_app.py``).
The database lives in a temporary folder, so a restart of the host simply starts the demo over.

* ``bootstrap``: on a visitor's first page view, the demo invoices are loaded (as if they had clicked
  *Load demo invoices*) when the app has just (re)started or the review queue is empty (a new database,
  or earlier visitors approved everything), so the first screen is always a review queue to work on.
* ``reset``: wipes the database and loads the demo again (the *Reset demo* button in the banner).
* ``banner``: the slim notice at the top of every page.

All visitors share one database, so a lock keeps two of them from loading the demo at the same time.
"""

from __future__ import annotations

import tempfile
import threading
from pathlib import Path

import streamlit as st

from ap_coder import ui
from ap_coder.demo import load_demo
from ap_coder.store import REVIEW, Store
from ap_coder.webapp.common import PAGES, get_settings, get_store, notify

_LOCK = threading.Lock()
_STARTED = "_public_demo_started"
_ROUND = "_public_demo_round"
_fresh_process = True  # the first visit after the app (re)starts gets a fresh demo


def reset(store: Store) -> dict[str, int]:
    """Start the demo over: a brand-new database with the demo invoices replaces the current one."""
    with _LOCK, tempfile.TemporaryDirectory(dir=store.path.parent) as tmp:
        fresh = Store(Path(tmp) / "fresh.db")
        result = load_demo(fresh, get_settings())
        fresh.backup_to(store.path)  # a consistent copy, safe while other visitors are reading
    return result


def bootstrap(store: Store) -> None:
    """Once per visit: make sure the review queue has invoices in it (see the module docstring)."""
    global _fresh_process
    if st.session_state.get(_STARTED):
        return
    st.session_state[_STARTED] = True
    with _LOCK:
        first, _fresh_process = _fresh_process, False
    if first or not store.list_invoices(REVIEW):
        reset(store)


def banner() -> None:
    """The notice at the top of every page, with the Reset demo button."""
    with st.container(key="public_demo_banner"):
        note, reset_box = st.columns([5, 1], vertical_alignment="center", gap="small")
        note.html(
            f"<div class='apc-demo-banner'>{ui.icon('science', '1.15em')}<b>Public demo</b>"
            "<span>Made-up invoices only · nothing you do here is sent anywhere · the demo may reset at any time"
            "</span></div>"
        )
        round_no = st.session_state.get(_ROUND, 0)  # a new key after a reset: the menu opens closed again
        with reset_box.popover(
            "Reset demo", icon=":material/restart_alt:", width="stretch", key=f"demo_menu_{round_no}"
        ):
            st.markdown(
                "Start the demo over with the original invoices? Everything changed since (approvals, edits, "
                "settings) is undone, also for anyone else trying the demo right now."
            )
            if st.button("Reset the demo", type="primary", icon=":material/restart_alt:", key="public_demo_reset"):
                result = reset(get_store())
                for key in list(st.session_state):  # open invoices, drafts and filters of the old data
                    del st.session_state[key]
                st.session_state[_STARTED] = True
                st.session_state[_ROUND] = round_no + 1
                notify(f"Demo reset: {result['to_review']} invoices to review.", ":material/restart_alt:")
                st.switch_page(PAGES["review"])
