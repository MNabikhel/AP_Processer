"""Helpers shared by every dashboard page: data access, formatting, toasts, cards, drafts."""

from __future__ import annotations

import datetime as dt
import getpass
import html
import io
import ipaddress
import os
import re
import socket
import sys
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from ap_coder import paths, ui
from ap_coder.config import Settings
from ap_coder.reference_data import UNASSIGNED, ReferenceData, short_name
from ap_coder.safe import md
from ap_coder.store import Store, default_db_path

DB_PATH = Path(os.environ.get("AP_DB_PATH") or default_db_path())
# The public demo on the web (``streamlit_app.py`` sets it): made-up invoices only, no Azure, no folders.
PUBLIC_DEMO = os.environ.get("AP_PUBLIC_DEMO", "").strip().lower() in {"1", "true", "yes", "on"}
INVOICE_DIR = DB_PATH.parent / "invoices"
CACHE_DIR = DB_PATH.parent / ".cache" / "extraction"
ASSETS = Path(__file__).resolve().parent.parent / "assets"
TARGET_ACCURACY = 0.90
SERIES_BLUE = "#2a78d6"  # categorical slot 1 (dataviz reference palette)
TARGET_GRAY = "#8a8985"

# Filled in by dashboard.py at start-up: pages link to each other with ``st.page_link(PAGES["process"])``.
PAGES: dict[str, Any] = {}

# The sidebar's sections (PAGES keys, in order). Page headers show the section as a breadcrumb.
NAV_SECTIONS: dict[str, list[str]] = {
    "Work": ["review", "process", "search"],
    "Close & compliance": ["exports", "month_end", "sales_tax", "statements"],
    "Master data": ["vendors", "purchase_orders", "accounts"],
    "Analytics": ["learning", "spend", "insights", "activity"],
    "System": ["settings", "help"],
}
SECTION_OF = {key: section for section, keys in NAV_SECTIONS.items() for key in keys}


def page_head(page: str, title: str, subtitle: str = "", aside: str = "", action: bool = False) -> Any:
    """The standard page header: breadcrumb (AP Coder / section), title, one-line description.

    ``aside``: HTML on the right (pills, a status). ``action=True`` returns a right-aligned container for
    the page's primary action (a button or page link), e.g. ``with page_head(...): st.page_link(...)``.
    """
    crumbs = ("AP Coder", SECTION_OF.get(page, ""))
    html_head = ui.page_header("", title, subtitle, aside, crumbs=[c for c in crumbs if c])
    if not action:
        st.html(html_head)
        return None
    with st.container(key=f"pagehead_{page}"):
        left, right = st.columns([3, 1], vertical_alignment="bottom")
        left.html(html_head)
    return right


# --- Shared helpers -----------------------------------------------------------------------------


@st.cache_resource
def get_store() -> Store:
    store = Store(DB_PATH)
    try:
        store.auto_backup()  # once a day, when the dashboard starts; the newest 14 are kept
    except OSError:
        pass  # a backup problem must never stop the dashboard from opening
    return store


def get_settings() -> Settings:
    return Settings.from_env(os.environ.get("AP_ENV_FILE"))


def login() -> str:
    """The Windows / computer account running the dashboard (recorded with approvals)."""
    try:
        return getpass.getuser()
    except Exception:  # no login name available (unusual service accounts)
        return ""


def reviewer() -> str:
    """The person approving: the name this Windows user saved in Settings (kept per user, so two people
    sharing one AP Coder keep their own names), else AP_REVIEWER from the .env, else the computer login."""
    saved = str(paths.read_user_settings().get("reviewer") or "")
    for name in (saved, os.environ.get("AP_REVIEWER"), getpass.getuser()):
        if name and str(name).strip():
            return str(name).strip()
    return "Reviewer"


def first_name() -> str:
    return reviewer().split()[0]


def money(value: Any, currency: str = "") -> str:
    try:
        float(value)
    except (TypeError, ValueError):
        return "-"
    return ui.money(value) + (f" {currency}" if currency else "")


def esc(value: Any) -> str:
    return html.escape(str(value or ""))


# --- Only this computer's addresses (DNS rebinding) ---------------------------------------------------------------
# A web page on the internet whose name is made to point at 127.0.0.1 could otherwise open the dashboard in the
# clerk's browser and use it. Browsers send the name they used in the Host header, so the dashboard checks it.

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
WILDCARD_ADDRESSES = {"", "0.0.0.0", "::"}  # listening on every network: colleagues use an IP or this PC's name


def host_name(host: str) -> str:
    """The name part of a Host header ("localhost:8501" → "localhost", "[::1]:8501" → "::1")."""
    host = host.strip().lower()
    if host.startswith("["):
        host = host[1 : host.find("]")] if "]" in host else ""  # "[::1" without "]" is not an address
    elif host.count(":") == 1:
        host = host.rsplit(":", 1)[0]
    return host.rstrip(".")


def host_allowed(host: str | None, address: str | None, extra: tuple[str | None, ...] = ()) -> bool:
    """Whether a request whose Host header is ``host`` may use the dashboard listening on ``address``: this
    computer (localhost, 127.0.0.1, ::1) or the configured address. When it listens on every network
    (``0.0.0.0``), also an IP address or this computer's own name: a rebinding attack needs a domain name.
    No Host header at all is not a browser (e.g. tests), so it is allowed."""
    if host is None:
        return True
    name = host_name(host)
    if not name:
        return False
    allowed = LOCAL_HOSTS | {host_name(a) for a in (address, *extra) if a}
    if name in allowed:
        return True
    try:
        ip = ipaddress.ip_address(name)
    except ValueError:
        ip = None
    if ip is not None and (ip.is_loopback or (getattr(ip, "ipv4_mapped", None) or ip).is_loopback):
        return True  # also ::ffff:127.0.0.1, as newer Pythons already say
    if host_name(address or "") in WILDCARD_ADDRESSES:
        if ip is not None:
            return True
        names = {socket.gethostname(), socket.getfqdn()}
        return name in {n.lower().rstrip(".") for n in names if n}
    return False


def request_host() -> str | None:
    """The Host header of the browser's connection, or None when there is none (tests, bare mode)."""
    try:
        return st.context.headers.get("Host")
    except Exception:  # noqa: BLE001 - no browser connection
        return None


def refuse_foreign_host() -> None:
    """Stop this run, showing only a short message, when the dashboard was opened through an address that is
    not this computer's (see ``host_allowed``). The public demo on the web is meant to be opened by anyone."""
    if PUBLIC_DEMO:
        return
    host = request_host()
    if host_allowed(host, st.get_option("server.address"), (st.get_option("browser.serverAddress"),)):
        return
    st.error(
        f"AP Coder was opened through **{md(host)}**, which is not this computer's address, so nothing is shown. "
        f"Open it at http://localhost:{st.get_option('server.port')} (or with the AP Coder shortcut).",
        icon=":material/gpp_bad:",
    )
    st.stop()


def short_path(path: Path) -> str:
    """A path as short as possible: relative to the working folder, else ``~`` for the home folder."""
    path = path.resolve()
    for base, prefix in ((Path.cwd(), ""), (Path.home(), "~")):
        try:
            rel = path.relative_to(base)
        except ValueError:
            continue
        return str(Path(prefix) / rel) if prefix else str(rel)
    return str(path)


def open_folder(path: Path) -> bool:
    """Open a folder in Explorer / Finder. The dashboard runs on this computer, so this is the user's."""
    import subprocess

    path.mkdir(parents=True, exist_ok=True)
    try:
        if sys.platform.startswith("win"):
            os.startfile(path)  # type: ignore[attr-defined]  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])  # noqa: S603, S607
        else:
            subprocess.Popen(["xdg-open", str(path)])  # noqa: S603, S607
        return True
    except OSError:
        return False


def by_currency(invoices: list[dict[str, Any]]) -> str:
    """ "27,864.06 CAD · 7,570.01 USD": totals per currency, never added together."""
    totals: dict[str, float] = {}
    for i in invoices:
        cur = i.get("currency") or "CAD"
        totals[cur] = totals.get(cur, 0.0) + float(i.get("grand_total") or 0)
    return " · ".join(f"{money(t)} {c}" for c, t in sorted(totals.items(), key=lambda x: (x[0] != "CAD", x[0])))


def reference_or_none(store: Store) -> ReferenceData | None:
    try:
        return store.reference_data()
    except ValueError:
        return None


# What a clerk sees instead of the stored UNASSIGNED code (the stored value never changes).
NEEDS_GL = "Needs an account"
NEEDS_CC = "Needs a cost center"


def gl_display(code: str | None) -> str:
    """A GL code for display: UNASSIGNED reads as "Needs an account", a blank as an em dash."""
    return NEEDS_GL if code == UNASSIGNED else (code or "—")


def cc_display(code: str | None) -> str:
    return NEEDS_CC if code == UNASSIGNED else (code or "")


def gl_label_map(reference: ReferenceData | None) -> dict[str, str]:
    labels = {UNASSIGNED: NEEDS_GL, "": "(none)"}
    if reference is not None:
        for row in reference.chart_of_accounts.rows:
            name = short_name(row.get("description", ""))
            labels[row["gl_code"]] = f"{row['gl_code']} · {name[:44]}"
    return labels


def gl_name(reference: ReferenceData, code: str) -> str:
    row = reference.chart_of_accounts.get(code)
    return short_name((row or {}).get("description", "")) if row else ""


def cc_label_map(reference: ReferenceData | None) -> dict[str, str]:
    labels = {UNASSIGNED: NEEDS_CC, "": "(none)"}
    if reference is not None and reference.cost_centers is not None:
        for row in reference.cost_centers.rows:
            labels[row["cost_center"]] = f"{row['cost_center']} · {row.get('description', '')[:30]}"
    return labels


@st.cache_data(show_spinner=False)
def render_pages(path: str, mtime: float) -> list[bytes]:
    """PNG bytes per page (PDF via PyMuPDF, images via Pillow)."""
    suffix = Path(path).suffix.lower()
    if suffix == ".pdf":
        import pymupdf

        with pymupdf.open(path) as doc:
            return [page.get_pixmap(dpi=120).tobytes("png") for page in doc]
    if suffix in {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}:
        from PIL import Image, ImageSequence

        pages = []
        with Image.open(path) as img:
            for frame in ImageSequence.Iterator(img):
                buf = io.BytesIO()
                frame.convert("RGB").save(buf, format="PNG")
                pages.append(buf.getvalue())
        return pages
    return []


def not_in_public_demo(what: str) -> None:
    """The friendly note shown instead of something the public demo can't do (Azure, folders on a computer)."""
    st.info(
        f"{what} is not available in the public demo. It runs on made-up invoices only, with no Azure "
        "connection and no folders of its own; install AP Coder on your computer to use it.",
        icon=":material/science:",
    )


def notify(message: str, icon: str = ":material/check_circle:") -> None:
    """Show a toast after the next rerun."""
    st.session_state.setdefault("toasts", []).append((message, icon))


def scroll_to_top_if_asked() -> None:
    """After approve / reject / next, start the newly opened page at the top, not where the button was.

    The element is drawn on every run (only its content changes) so the page's layout stays the same;
    adding and removing it confused Streamlit's clean-up of the previous page.
    """
    nonce = st.session_state.get("_scroll_nonce", 0)
    script = ""
    if st.session_state.pop("scroll_top", False):
        nonce += 1
        st.session_state["_scroll_nonce"] = nonce
        script = (
            "<script>for (const el of window.parent.document.querySelectorAll("
            '\'[data-testid="stMain"], [data-testid="stAppViewContainer"], section.main\')) el.scrollTo(0, 0);'
            "window.scrollTo(0, 0);</script>"
        )
    st.html(f"<span data-scroll='{nonce}' hidden></span>{script}", unsafe_allow_javascript=True)


def show_toast() -> None:
    scroll_to_top_if_asked()
    for message, icon in st.session_state.pop("toasts", []):
        st.toast(message, icon=icon)


def persistent_editor(data: pd.DataFrame, key: str, **kwargs: Any) -> pd.DataFrame:
    """``st.data_editor`` whose edits survive moving to another invoice or page and back.

    Streamlit forgets a widget's edits once it is not drawn. The last edited table is kept
    as a draft and becomes the starting point the next time the editor is created.
    """
    base_key, draft_key = f"_base_{key}", f"_draft_{key}"
    if key not in st.session_state:
        st.session_state[base_key] = st.session_state.get(draft_key, data)
    edited = st.data_editor(st.session_state[base_key], key=key, **kwargs)
    st.session_state[draft_key] = edited
    return edited


def replace_editor(key: str, data: pd.DataFrame) -> None:
    """Make a ``persistent_editor`` start over from ``data`` on the next run (e.g. after a bulk change)."""
    st.session_state[f"_draft_{key}"] = data
    st.session_state.pop(f"_base_{key}", None)
    st.session_state.pop(key, None)


def forget_drafts(prefix: str) -> None:
    """Drop the saved drafts of a finished invoice: its grids and the header fields kept for the session."""
    for k in [
        k for k in st.session_state if str(k).startswith((f"_base_{prefix}_", f"_draft_{prefix}_", f"{prefix}_"))
    ]:
        del st.session_state[k]


_INVOICE_STATE = re.compile(r"^(?:_base_|_draft_)?inv\d+_")


def forget_all_drafts() -> None:
    """Drop every invoice's drafts (after a restore, invoice numbers are given out again to new invoices), and
    the fixed-rules grid's draft (the restored rules are shown, not the ones edited before)."""
    for k in [k for k in st.session_state if _INVOICE_STATE.match(str(k))]:
        del st.session_state[k]
    st.session_state.pop("unsaved_edits", None)
    for k in ("rules_grid", "_base_rules_grid", "_draft_rules_grid", "_rules_grid_from"):
        st.session_state.pop(k, None)


def card(name: str) -> Any:
    """A bordered white card (the key gives it a stable CSS class: st-key-card_<name>)."""
    return st.container(border=True, key=f"card_{name}")


def approved_today(store: Store) -> int:
    """How many invoices this reviewer approved today (shown under their name: not the whole team's)."""
    return store.approved_since(dt.date.today().isoformat(), reviewer())


def weekly_accuracy(metrics: dict[str, Any]) -> list[float]:
    return [w["accepted"] / (w["accepted"] + w["corrected"]) for w in metrics["weekly"]]


def demo_card(store: Store, where: str) -> None:
    """Load or remove the demo invoices (the bundled samples, already read and coded; nothing to connect)."""
    from ap_coder.demo import demo_available, load_demo, remove_demo

    if not demo_available():
        return
    demo_count = store.demo_count()
    with card(f"demo_{where}"):
        st.markdown("#### Demo invoices")
        if not demo_count:
            st.caption(
                "Look around first: ten sample invoices from across Canada (including a US vendor "
                "and a credit note) are added already read and coded, including a few realistic "
                "mistakes to correct, plus sample purchase orders and a sample vendor list. Uses the sample GL "
                "accounts if you haven't imported yours."
            )
            if st.button("Load demo invoices", icon=":material/play_circle:", type="primary", key=f"demo_load_{where}"):
                result = load_demo(store, get_settings())
                notify(
                    f"Demo ready: {result['to_review']} invoices to review, {result['approved']} already approved.",
                    ":material/science:",
                )
                st.rerun()
        else:
            st.caption(
                f"{ui.plural(demo_count, 'demo invoice')} loaded. Removing them also forgets what the AI "
                "learned from them, and removes the sample purchase orders and vendor list loaded with them; your own "
                "invoices, POs, vendors and settings are untouched."
            )
            sure = st.checkbox("Yes, remove the demo invoices", key=f"demo_sure_{where}")
            if st.button(
                "Remove demo invoices", icon=":material/delete_sweep:", disabled=not sure, key=f"demo_rm_{where}"
            ):
                removed = remove_demo(store)
                notify(f"Removed {ui.plural(removed, 'demo invoice')}.", ":material/delete_sweep:")
                st.rerun()


def invoice_label(event: dict[str, Any]) -> str:
    """ "Northwind IT Solutions Inc. · NW-2026-0912" for an event about an invoice (blank otherwise)."""
    if not event.get("invoice_id"):
        return ""
    parts = [event.get("invoice_vendor") or "", event.get("invoice_number") or ""]
    return " · ".join(p for p in parts if p) or f"#{event['invoice_id']} (deleted)"


def history_html(events: list[dict[str, Any]], with_invoice: bool = False) -> str:
    """An invoice's (or, ``with_invoice``, the app's) audit events as a timeline, newest first."""
    from ap_coder.audit import ACTIONS, describe

    items = []
    for e in events:
        icon_name, tone, label = ACTIONS.get(e["action"], ("circle", "gray", e["action"]))
        about = invoice_label(e) if with_invoice else ""
        title = f"{label} · {about}" if about else label
        items.append(
            {"icon": icon_name, "tone": tone, "title": title, "text": describe(e), "who": e.get("actor") or "",
             "when": f"{ui.time_ago(e['created_at'])} · {e['created_at'].replace('T', ' ')[:16]}"}
        )  # fmt: skip
    return ui.timeline(items)
