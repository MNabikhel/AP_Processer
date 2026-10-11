"""The public web demo (``streamlit_app.py``): made-up invoices on first visit, a banner with Reset demo, no
Azure, no folders, and nothing changed for the normal dashboard."""

import os
import sys

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from ap_coder.store import APPROVED, REVIEW, Store

from .conftest import ROOT, SAMPLE_STEM, SAMPLES
from .test_dashboard_pages import PAGES, TIMEOUT

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10: the toml package Streamlit installs
    import toml as tomllib

DEMO_APP = str(ROOT / "streamlit_app.py")
WEBAPP = ("ap_coder.webapp", "ap_coder.dashboard")


@pytest.fixture
def demo_dir(tmp_path, monkeypatch):
    """Run the demo app against a temporary folder; afterwards the environment and the page modules (which
    read ``AP_PUBLIC_DEMO`` when imported) are put back, so later tests see the normal dashboard."""
    saved_env = dict(os.environ)
    saved_modules = {name: module for name, module in sys.modules.items() if name.startswith(WEBAPP)}
    for name in saved_modules:
        del sys.modules[name]
    st.cache_resource.clear()
    st.cache_data.clear()
    for name in [n for n in os.environ if n.startswith("AZURE_")]:
        monkeypatch.delenv(name)
    folder = tmp_path / "demo"
    monkeypatch.setenv("AP_DEMO_DIR", str(folder))
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.invalid/")  # set on the host: must be ignored
    yield folder
    for name in [n for n in sys.modules if n.startswith(WEBAPP)]:
        del sys.modules[name]
    sys.modules.update(saved_modules)
    os.environ.clear()
    os.environ.update(saved_env)
    st.cache_resource.clear()
    st.cache_data.clear()


def _ok(at):
    assert not at.exception, [e.value for e in at.exception]
    return at


def _run_demo():
    return _ok(AppTest.from_file(DEMO_APP, default_timeout=TIMEOUT).run())


def _queue(at):
    return [b for b in at.button if (b.key or "").startswith("qopen_")]


def _has_banner(at):
    return any("<div class='apc-demo-banner'>" in e.proto.body for e in at.get("html"))  # not the CSS rule


def _texts(at):
    return " ".join([*(e.value for e in at.info), *(c.value for c in at.caption)])


def _page(module, function):
    return AppTest.from_string(
        f"from ap_coder.webapp.{module} import {function}\n{function}()", default_timeout=TIMEOUT
    )


def test_first_visit_shows_a_review_queue_of_demo_invoices(demo_dir, tmp_path):
    at = _run_demo()
    assert _has_banner(at)
    assert at.button(key="public_demo_reset")
    assert len(_queue(at)) == 8  # the demo loaded by itself: 10 invoices, 2 already approved
    store = Store(demo_dir / "ap_coder.db")
    assert store.demo_count() == 10
    assert not (tmp_path / "private").exists()  # nothing in the normal data folder
    assert os.environ["AP_PUBLIC_DEMO"] == "1" and "AZURE_OPENAI_ENDPOINT" not in os.environ
    assert len(_queue(_run_demo())) == 8  # a second visit loads nothing more


def test_reset_demo_starts_over(demo_dir):
    at = _run_demo()
    store = Store(demo_dir / "ap_coder.db")
    first = store.list_invoices(REVIEW)[0]
    store.approve_invoice(first["id"], store.get_invoice(first["id"])["ai_output"], "Visitor")
    store.set_setting("fx_rates", "USD=9.99")
    assert len(store.list_invoices(REVIEW)) == 7

    _ok(at.button(key="public_demo_reset").click().run())
    assert len(store.list_invoices(REVIEW)) == 8
    assert len(store.list_invoices(APPROVED)) == 2
    assert store.get_setting("fx_rates") == ""  # everything a visitor changed is undone
    assert any("Demo reset" in t.proto.body for t in at.toast)


def test_a_new_visitor_never_sees_an_emptied_queue(demo_dir):
    _run_demo()
    store = Store(demo_dir / "ap_coder.db")
    for inv in store.list_invoices(REVIEW):
        store.approve_invoice(inv["id"], store.get_invoice(inv["id"])["ai_output"], "Earlier visitor")
    assert not store.list_invoices(REVIEW)
    assert len(_queue(_run_demo())) == 8


def test_a_restarted_app_starts_over(demo_dir):
    _run_demo()
    store = Store(demo_dir / "ap_coder.db")
    store.set_setting("approval_limit", "1.00")
    assert store.get_setting("approval_limit") == "1.00"
    _run_demo()  # same app process: what visitors did is kept
    assert store.get_setting("approval_limit") == "1.00"

    from ap_coder.webapp import public_demo

    public_demo._fresh_process = True  # as after a reboot, when the temporary folder survived
    _run_demo()
    assert store.get_setting("approval_limit") == ""


def test_every_page_runs_in_the_demo(demo_dir):
    _run_demo()
    for module, function in PAGES:
        _ok(_page(module, function).run())


def test_processing_says_it_is_not_available(demo_dir):
    _run_demo()
    at = _ok(_page("process", "page_process").run())
    assert not at.get("file_uploader")
    assert "not available in the public demo" in _texts(at)

    store = Store(demo_dir / "ap_coder.db")
    before = len(store.list_invoices())
    at = _ok(
        AppTest.from_string(
            "from pathlib import Path\n"
            "from ap_coder.webapp.common import get_store\n"
            "from ap_coder.webapp.process import run_pipeline\n"
            f"run_pipeline(get_store(), [Path({str(SAMPLES / (SAMPLE_STEM + '.pdf'))!r})])",
            default_timeout=TIMEOUT,
        ).run()
    )
    assert "not available in the public demo" in _texts(at)
    assert len(store.list_invoices()) == before  # nothing was sent anywhere


def test_settings_need_no_azure_and_write_no_folders(demo_dir):
    _run_demo()
    at = _ok(_page("settings", "page_settings").run())
    assert "Azure" not in _texts(at) + " ".join(m.value for m in at.markdown)  # no cloud services in Settings
    assert "The demo invoices were read this way ahead of time" in _texts(at)
    assert not [t for t in at.text_input if "copy each backup" in t.label]
    assert at.button(key="open_data").disabled
    assert not [t for t in at.text_input if "endpoint" in t.label.lower()]


def test_the_normal_dashboard_has_no_demo_banner(tmp_path, monkeypatch):
    monkeypatch.setenv("AP_DB_PATH", str(tmp_path / "private" / "ap_coder.db"))
    monkeypatch.delenv("AP_PUBLIC_DEMO", raising=False)
    for name in [m for m in sys.modules if m.startswith(WEBAPP)]:
        monkeypatch.delitem(sys.modules, name)
    st.cache_resource.clear()
    at = _ok(AppTest.from_file(str(ROOT / "ap_coder" / "dashboard.py"), default_timeout=TIMEOUT).run())
    assert not _has_banner(at)
    assert not _queue(at)  # no demo loaded by itself
    assert not Store(tmp_path / "private" / "ap_coder.db").list_invoices()


def test_streamlit_config_matches_the_dashboard_theme():
    """``.streamlit/config.toml`` (the public demo) repeats theme.toml; static/ repeats the font."""
    config = tomllib.loads((ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8"))
    theme = tomllib.loads((ROOT / "ap_coder" / "assets" / "theme.toml").read_text(encoding="utf-8"))
    assert config["theme"] == theme["theme"]
    assert config["server"]["enableStaticServing"] is True
    assert "address" not in config["server"]  # the host decides (localhost only for the normal dashboard)
    for name in ("InterVariable.woff2", "Inter-OFL.txt"):
        assert (ROOT / "static" / name).read_bytes() == (ROOT / "ap_coder" / "static" / name).read_bytes()
