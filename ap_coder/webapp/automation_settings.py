"""Settings → Automation: when an invoice may be approved without a person (touchless processing)."""

from __future__ import annotations

import streamlit as st


def automation_tab(store) -> None:
    """The touchless switch, the largest amount approved without a person, and the fixed bar each vendor must meet."""
    st.caption("Touchless processing is off: every invoice is reviewed by a person.")
