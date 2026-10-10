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

``streamlit_app.py`` runs this same app as the public web demo (made-up invoices, no Azure, a temporary
database): see ``webapp/public_demo.py``.
"""

from __future__ import annotations

import streamlit as st

from ap_coder import ui
from ap_coder.store import REVIEW, Store
from ap_coder.webapp.accounts import page_accounts
from ap_coder.webapp.activity import page_activity
from ap_coder.webapp.common import (
    ASSETS,
    CACHE_DIR,
    DB_PATH,
    NAV_SECTIONS,
    PAGES,
    PUBLIC_DEMO,
    approved_today,
    get_settings,
    get_store,
    reviewer,
    short_path,
)
from ap_coder.webapp.exports import page_exports
from ap_coder.webapp.help import page_help
from ap_coder.webapp.insights import page_insights
from ap_coder.webapp.learning import page_learning
from ap_coder.webapp.month_end import page_month_end
from ap_coder.webapp.process import page_process
from ap_coder.webapp.purchase_orders import page_purchase_orders
from ap_coder.webapp.review import page_review
from ap_coder.webapp.sales_tax import page_sales_tax
from ap_coder.webapp.search import page_search
from ap_coder.webapp.settings import page_settings
from ap_coder.webapp.spend import page_spend
from ap_coder.webapp.statements import page_statements
from ap_coder.webapp.vendors import page_vendors

st.set_page_config(page_title="AP Coder", page_icon=str(ASSETS / "icon.svg"), layout="wide")


@st.cache_resource
def _page_reader_thread():
    """The page reader's background thread, once per dashboard process (Settings → Page reader)."""
    from ap_coder.page_worker import start_background

    return start_background(get_settings, lambda: Store(DB_PATH), cache_dir=CACHE_DIR)


st.html(f"<style>{(ASSETS / 'style.css').read_text(encoding='utf-8')}</style>")

PAGES.update(
    {
        "review": st.Page(page_review, title="Review queue", icon=":material/inbox:", default=True),
        "search": st.Page(page_search, title="Find an invoice", icon=":material/search:"),
        "process": st.Page(page_process, title="Process invoices", icon=":material/upload_file:"),
        "exports": st.Page(page_exports, title="Exports", icon=":material/ios_share:"),
        "month_end": st.Page(page_month_end, title="Month-end", icon=":material/event_available:"),
        "sales_tax": st.Page(page_sales_tax, title="Sales tax", icon=":material/percent:"),
        "statements": st.Page(page_statements, title="Vendor statements", icon=":material/fact_check:"),
        "purchase_orders": st.Page(page_purchase_orders, title="Purchase orders", icon=":material/shopping_cart:"),
        "accounts": st.Page(page_accounts, title="GL accounts & tax", icon=":material/account_tree:"),
        "learning": st.Page(page_learning, title="Learning & accuracy", icon=":material/insights:"),
        "spend": st.Page(page_spend, title="Spend", icon=":material/donut_small:"),
        "insights": st.Page(page_insights, title="Insights", icon=":material/query_stats:"),
        "vendors": st.Page(page_vendors, title="Vendors", icon=":material/storefront:"),
        "activity": st.Page(page_activity, title="Activity", icon=":material/history:"),
        "settings": st.Page(page_settings, title="Settings", icon=":material/settings:"),
        "help": st.Page(page_help, title="Help", icon=":material/help:"),
    }
)

st.logo(str(ASSETS / "logo.svg"), size="large", icon_image=str(ASSETS / "icon.svg"))
_store = get_store()
if PUBLIC_DEMO:  # the public demo on the web (streamlit_app.py): made-up invoices, see webapp/public_demo.py
    from ap_coder.webapp import public_demo

    public_demo.bootstrap(_store)
    public_demo.banner()
else:
    _page_reader_thread()
_waiting = len(_store.list_invoices(REVIEW))
with st.sidebar:
    if PUBLIC_DEMO:
        _env, _tone, _where, _tip = "Public demo", "violet", "made-up invoices · no Azure", ""
    else:  # the folder itself is a tooltip: a clerk needs to know data stays here, not the path
        _env, _tone, _where, _tip = "Offline", "ok", "Data stays on this computer", short_path(DB_PATH.parent)
    if _store.demo_count():
        st.html(ui.pill("Demo invoices loaded", "gray", "science"))
    st.html(ui.sidebar_profile(reviewer(), approved_today(_store), _waiting, _env, _tone, _where, _tip))
# Count badges on the navigation (see style.css): invoices waiting for review (accent, the one queue to work
# through), approved ones not exported yet (neutral).
_badges = {"": _waiting, "page_exports": len(_store.unexported_approved())}
_rules = "".join(
    f'[data-testid="stSidebarNavLink"][href$="/{path}"]::after {{content: "{count}";'
    + ("background: var(--lg-accent-weak); color: var(--lg-accent);" if not path else "")
    + "}"
    for path, count in _badges.items()
    if count
)
if _rules:
    st.html(f"<style>{_rules}</style>")

st.navigation({section: [PAGES[k] for k in keys] for section, keys in NAV_SECTIONS.items()}, expanded=True).run()
