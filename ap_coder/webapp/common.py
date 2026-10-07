"""Helpers shared by every dashboard page: data access, formatting, toasts, cards, drafts."""

from __future__ import annotations

import datetime as dt
import getpass
import html
import io
import os
import sys
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from ap_coder.config import Settings
from ap_coder.reference_data import UNASSIGNED, ReferenceData
from ap_coder.store import APPROVED, Store, default_db_path

DB_PATH = Path(os.environ.get("AP_DB_PATH") or default_db_path())
INVOICE_DIR = DB_PATH.parent / "invoices"
CACHE_DIR = DB_PATH.parent / ".cache" / "extraction"
ASSETS = Path(__file__).resolve().parent.parent / "assets"
TARGET_ACCURACY = 0.90
SERIES_BLUE = "#2a78d6"  # categorical slot 1 (dataviz reference palette)
TARGET_GRAY = "#8a8985"

# Filled in by dashboard.py at start-up: pages link to each other with ``st.page_link(PAGES["process"])``.
PAGES: dict[str, Any] = {}


# --- Shared helpers -----------------------------------------------------------------------------


@st.cache_resource
def get_store() -> Store:
    return Store(DB_PATH)


def get_settings() -> Settings:
    return Settings.from_env(os.environ.get("AP_ENV_FILE"))


def reviewer() -> str:
    for name in (st.session_state.get("reviewer"), os.environ.get("AP_REVIEWER"), getpass.getuser()):
        if name and str(name).strip():
            return str(name).strip()
    return "Reviewer"


def first_name() -> str:
    return reviewer().split()[0]


def money(value: Any, currency: str = "") -> str:
    try:
        return f"{float(value):,.2f}{' ' + currency if currency else ''}"
    except (TypeError, ValueError):
        return "-"


def esc(value: Any) -> str:
    return html.escape(str(value or ""))


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


def reference_or_none(store: Store) -> ReferenceData | None:
    try:
        return store.reference_data()
    except ValueError:
        return None


def gl_label_map(reference: ReferenceData | None) -> dict[str, str]:
    labels = {UNASSIGNED: f"{UNASSIGNED} · needs a code", "": "(none)"}
    if reference is not None:
        for row in reference.chart_of_accounts.rows:
            name = row.get("description", "").split(" - ")[0]
            labels[row["gl_code"]] = f"{row['gl_code']} · {name[:40]}"
    return labels


def gl_name(reference: ReferenceData, code: str) -> str:
    row = reference.chart_of_accounts.get(code)
    return (row or {}).get("description", "").split(" - ")[0] if row else ""


def cc_label_map(reference: ReferenceData | None) -> dict[str, str]:
    labels = {UNASSIGNED: f"{UNASSIGNED} · needs a cost center", "": "(none)"}
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


def forget_drafts(prefix: str) -> None:
    """Drop the saved drafts of a finished invoice."""
    for k in [k for k in st.session_state if str(k).startswith((f"_base_{prefix}_", f"_draft_{prefix}_"))]:
        del st.session_state[k]


def card(name: str) -> Any:
    """A bordered white card (the key gives it a stable CSS class: st-key-card_<name>)."""
    return st.container(border=True, key=f"card_{name}")


def approved_today(store: Store) -> int:
    today = dt.date.today().isoformat()
    return sum(1 for i in store.list_invoices(APPROVED) if (i["reviewed_at"] or "").startswith(today))


def weekly_accuracy(metrics: dict[str, Any]) -> list[float]:
    return [w["accepted"] / (w["accepted"] + w["corrected"]) for w in metrics["weekly"]]


def demo_card(store: Store, where: str) -> None:
    """Load or remove the demo invoices (the bundled samples, coded as if by Azure; no Azure needed)."""
    from ap_coder.demo import demo_available, is_demo, load_demo, remove_demo

    if not demo_available():
        return
    demo_count = sum(1 for i in store.list_invoices_full() if is_demo(i))
    with card(f"demo_{where}"):
        st.markdown("#### :material/science: Demo invoices")
        if not demo_count:
            st.caption(
                "Look around before connecting Azure: ten sample invoices from across Canada (plus a US vendor "
                "and a credit note) are added as if the AI had read and coded them, including a few realistic "
                "mistakes to correct. Uses the sample GL accounts if you haven't imported yours."
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
                f"{demo_count} demo invoice(s) are loaded. Removing them also forgets what the AI learned from "
                "them; your own invoices and settings are untouched."
            )
            sure = st.checkbox("Yes, remove the demo invoices", key=f"demo_sure_{where}")
            if st.button(
                "Remove demo invoices", icon=":material/delete_sweep:", disabled=not sure, key=f"demo_rm_{where}"
            ):
                removed = remove_demo(store)
                notify(f"Removed {removed} demo invoice(s).", ":material/delete_sweep:")
                st.rerun()
