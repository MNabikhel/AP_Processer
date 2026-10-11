"""Text from invoices, file names and emails reaches the dashboard as plain text: no Markdown image or link to an
outside site (it would load or open it), no upload or saved email that stops the page, and the dashboard refuses
to be driven through another site's name (DNS rebinding)."""

import os
import re
from types import SimpleNamespace

from streamlit.testing.v1 import AppTest

from ap_coder.store import Store, load_sample_setup

from .test_dashboard_pages import APP, TIMEOUT, _ok
from .test_dashboard_pages import db as db  # the fixture (a fresh database per test)
from .test_webapp_dashboard_fixes import _gt

EVIL = "Acme ![x](http:evil.example/a.png) [y](https://evil.example)"
# An image or link Markdown would render: "](" not escaped, pointing outside (escaped text reads "\](").
UNESCAPED = re.compile(r"(?<!\\)\]\(\s*<?(?:https?:)?/*evil\.example")
# The Process page on its own, with the links to other pages it shows.
PROCESS_PAGE = """
import streamlit as st
from ap_coder.webapp.common import PAGES
for name in ("review", "accounts", "process"):
    PAGES.setdefault(name, st.Page(lambda: None, title=name, url_path=name))
from ap_coder.webapp.process import page_process
page_process()
"""
PLAIN_TEXT = {"Html", "Code", "Dataframe", "Arrow", "TextInput", "TextArea", "Selectbox", "MultiSelect", "Text"}


def _rendered(at) -> list[tuple[str, str]]:
    """(element type, text) of everything on the page that Streamlit draws as Markdown (labels, help, toasts)."""
    out: list[tuple[str, str]] = []

    def walk(node):
        proto = getattr(node, "proto", None)
        if proto is not None and type(proto).__name__ not in PLAIN_TEXT:
            out.append((type(proto).__name__, str(proto)))
        children = getattr(node, "children", None)
        if isinstance(children, dict):
            for child in children.values():
                walk(child)

    walk(at._tree)
    return out + [("Toast", t.value) for t in at.toast]


def _no_outside_markdown(at) -> None:
    bad = [(kind, text[:200]) for kind, text in _rendered(at) if UNESCAPED.search(text)]
    assert not bad, bad


def test_queue_shows_an_invoices_vendor_name_as_text(db):
    """The "Review invoice from …" button rendered ![x](…) as an image: loaded from outside on every visit."""
    store = Store(db)
    load_sample_setup(store)
    store.add_invoice(db.parent / "a.pdf", {**_gt(), "vendor_name": EVIL}, {"requires_review": True})
    at = _ok(AppTest.from_file(APP, default_timeout=TIMEOUT).run())
    labels = [b.label for b in at.button if (b.key or "").startswith("qopen_")]
    assert labels and all("evil" in label and "\\]\\(" in label for label in labels)
    _no_outside_markdown(at)


def _process_page(monkeypatch, vendor=EVIL):
    import ap_coder.webapp.process as process

    class Pipeline:
        def __init__(self, *args, **kwargs):
            pass

        def process(self, path):
            return SimpleNamespace(ok=True, output={"vendor_name": vendor}, report=None, error=None)

    monkeypatch.setattr(process, "InvoicePipeline", Pipeline)
    return process


def test_process_page_shows_file_and_vendor_names_as_text(db, monkeypatch):
    store = Store(db)
    load_sample_setup(store)
    process = _process_page(monkeypatch)
    process.INVOICE_DIR.mkdir(parents=True, exist_ok=True)
    named = process.INVOICE_DIR / "![t](evil.example)x.pdf"  # no ":" (Windows makes it a file stream)
    named.write_bytes(b"%PDF-1.4 one")
    # The progress lines while processing ("Reading and coding …", "<vendor>: ready").
    at = _ok(AppTest.from_string(f"from pathlib import Path\nfrom ap_coder.webapp.common import get_store\n"
                                 f"from ap_coder.webapp.process import run_pipeline\n"
                                 f"run_pipeline(get_store(), [Path({str(named)!r})])",
                                 default_timeout=TIMEOUT).run())  # fmt: skip
    assert sum("evil" in m.value for m in at.markdown) == 2
    _no_outside_markdown(at)
    # The same file uploaded again: "Skipped … already in AP Coder" names it, as text.
    store.add_invoice(named, {**_gt(), "vendor_name": EVIL}, {"requires_review": True})
    at = _ok(AppTest.from_string(PROCESS_PAGE, default_timeout=TIMEOUT).run())  # fmt: skip
    at.file_uploader[0].upload(named.name, b"%PDF-1.4 one", "application/pdf")
    _ok(at.run())
    _ok(next(b for b in at.button if b.label.startswith("Process 1 uploaded")).click().run())
    assert any("Skipped" in t.value and "evil" in t.value for t in at.toast)
    _no_outside_markdown(at)


def test_one_upload_that_cannot_be_saved_does_not_stop_the_others(db, monkeypatch):
    """A long name (300 characters, or 90 Chinese ones) is shortened; a file Windows still refuses is said and
    skipped, and the other files of the upload are processed."""
    store = Store(db)
    load_sample_setup(store)
    process = _process_page(monkeypatch, vendor="Northwind")
    real_save = process.save_upload

    def save(name, content):
        if name.startswith("locked"):
            raise PermissionError(13, "Permission denied")
        return real_save(name, content)

    monkeypatch.setattr(process, "save_upload", save)
    at = _ok(AppTest.from_string(PROCESS_PAGE, default_timeout=TIMEOUT).run())  # fmt: skip
    at.file_uploader[0].set_value([
        ("a" * 300 + ".pdf", b"%PDF-1.4 long", "application/pdf"),
        ("发票" * 45 + ".pdf", b"%PDF-1.4 cjk", "application/pdf"),
        ("locked.pdf", b"%PDF-1.4 locked", "application/pdf"),
    ])  # fmt: skip
    _ok(at.run())
    _ok(next(b for b in at.button if b.label.startswith("Process 3 uploaded")).click().run())
    saved = sorted(p.name for p in process.INVOICE_DIR.iterdir() if p.is_file())
    assert len(saved) == 2 and all(len(n) <= 80 and len(n.encode()) <= 200 and n.endswith(".pdf") for n in saved)
    assert any("locked.pdf could not be saved" in t.value for t in at.toast)
    assert any("2 invoices read and coded" in t.value for t in at.toast)


def test_safe_file_name_is_short_enough_for_windows():
    from ap_coder.webapp.process import safe_file_name

    assert safe_file_name("C:\\Users\\x\\" + "b" * 300 + ".PDF") == "b" * 76 + ".PDF"
    assert safe_file_name("../.hidden.pdf") == "hidden.pdf"
    assert safe_file_name("") == "invoice"
    cjk = safe_file_name("发票" * 45 + ".pdf")
    assert cjk.endswith(".pdf") and len(cjk.encode()) <= 200


def _nested_email(depth: int) -> bytes:
    msg = 'Content-Type: application/pdf\nContent-Disposition: attachment; filename="inv.pdf"\n\n%PDF-1.4 x'
    for n in range(depth):
        msg = f"Content-Type: message/rfc822\n\nSubject: Fwd {n}\nMIME-Version: 1.0\n" + msg
    return ("From: a@b.example\nSubject: deep\nMIME-Version: 1.0\n" + msg).encode()


def test_a_crafted_email_in_the_folder_does_not_break_the_process_page(db):
    """1000 emails forwarded inside each other made the page fail on every visit, the email never moved."""
    store = Store(db)
    load_sample_setup(store)
    from ap_coder.webapp.common import INVOICE_DIR

    INVOICE_DIR.mkdir(parents=True, exist_ok=True)
    (INVOICE_DIR / "deep.eml").write_bytes(_nested_email(1000))
    os.utime(INVOICE_DIR / "deep.eml", (1_700_000_000, 1_700_000_000))
    at = _ok(AppTest.from_string(PROCESS_PAGE, default_timeout=TIMEOUT).run())
    assert any("deep.eml" in c.value and "could not be read" in c.value for c in at.caption)
    assert not (INVOICE_DIR / "deep.eml").exists()
    assert (INVOICE_DIR / "emails" / "could not read" / "deep.eml").exists()
    _ok(AppTest.from_string(PROCESS_PAGE, default_timeout=TIMEOUT).run())  # and the next visit is fine too


# --- DNS rebinding -------------------------------------------------------------------------------------------------


def test_host_allowed_only_for_this_computer():
    from ap_coder.webapp.common import host_allowed

    this_computer = ("localhost:8501", "127.0.0.1:8501", "[::1]:8501", "LOCALHOST", "127.0.0.2:8501", "localhost.")
    for host in (*this_computer, "[::ffff:7f00:1]:8501"):
        assert host_allowed(host, "127.0.0.1"), host
    for host in ("evil.example:8501", "localhost.evil.example", "127.0.0.1.nip.io:8501", "", "[::ffff:7f00:1"):
        assert not host_allowed(host, "127.0.0.1"), host
    assert host_allowed(None, "127.0.0.1")  # no Host header: not a browser
    assert host_allowed("ap-laptop:8501", "127.0.0.1", ("ap-laptop",))  # the configured browser address
    # Shared on the office network (0.0.0.0): an IP address or this computer's name, never another site's name.
    import socket

    assert host_allowed("192.168.1.20:8501", "0.0.0.0")
    assert host_allowed(f"{socket.gethostname()}:8501", "0.0.0.0")
    assert not host_allowed("evil.example:8501", "0.0.0.0")
    assert host_allowed("10.0.0.5:8501", "10.0.0.5") and not host_allowed("10.0.0.6:8501", "10.0.0.5")


def test_dashboard_opened_through_another_sites_name_shows_nothing(db, monkeypatch):
    store = Store(db)
    load_sample_setup(store)
    store.add_invoice(db.parent / "a.pdf", _gt(), {"requires_review": True})
    import ap_coder.webapp.common as common

    monkeypatch.setattr(common, "request_host", lambda: "rebind.evil.example:8501")
    at = AppTest.from_file(APP, default_timeout=TIMEOUT).run()
    assert not at.exception
    assert [e.value for e in at.error] and "rebind.evil.example" in at.error[0].value
    assert not at.button and not at.markdown and not at.get("html")  # nothing else drawn, nothing to click
    monkeypatch.setattr(common, "request_host", lambda: "localhost:8501")
    at = _ok(AppTest.from_file(APP, default_timeout=TIMEOUT).run())
    assert not at.error and any((b.key or "").startswith("qopen_") for b in at.button)


def test_the_public_demo_is_open_to_any_host(monkeypatch):
    import ap_coder.webapp.common as common

    monkeypatch.setattr(common, "PUBLIC_DEMO", True)
    monkeypatch.setattr(common, "request_host", lambda: "ap-coder.streamlit.app")
    common.refuse_foreign_host()  # returns: nothing stopped
