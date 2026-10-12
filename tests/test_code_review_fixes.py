"""Bugs found by the code review of the dashboard and the launcher (docs/CODE_REVIEW_WEB_AND_LAUNCHER.md), each
reproduced here first."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from ap_coder import paths

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def launch(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("ap_script_launch_review", ROOT / "scripts" / "launch.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    venv = tmp_path / ".venv"
    venv.mkdir()
    monkeypatch.setattr(module, "VENV", venv)
    monkeypatch.setattr(module, "STATE", venv / "ap_coder_launcher.json")
    return module


# --- A damaged settings file or launcher state -----------------------------------------------------------------------


@pytest.mark.parametrize("text", ["[]", "null", '"C:\\\\APCoder"', "42"])
def test_a_settings_file_that_is_not_a_json_object_is_read_as_empty(text):
    """~/.ap_coder/settings.json holding valid JSON that is not an object (a sync tool or an editor left ``[]`` or
    ``null``): every page (the reviewer's name) and every start (the data folder) stopped on AttributeError."""
    paths.user_settings_path().write_text(text, encoding="utf-8")
    assert paths.read_user_settings() == {}
    assert paths.installs() == [] and paths.current_install() is None
    paths.write_user_settings(reviewer="Jordan Lee")  # and it can be written again
    assert paths.read_user_settings() == {"reviewer": "Jordan Lee"}


def test_a_damaged_install_record_is_skipped(tmp_path):
    """An install entry whose folder is not text, or a "current" that is a number, never stops a start."""
    good = str(tmp_path / "AP Coder 0.2")
    record = {"installs": [{"folder": 5}, {"folder": ["x"]}, {"folder": good, "version": "0.2.0"}], "current": 7}
    paths.user_settings_path().write_text(json.dumps(record), encoding="utf-8")
    assert [e["folder"] for e in paths.installs()] == [good]
    assert paths.current_install() is None
    paths.record_install({"folder": good, "version": "0.2.0", "build": ""})
    assert paths.current_install()["folder"] == good


@pytest.mark.parametrize("text", ["[]", "null", '"x"'])
def test_a_launcher_state_that_is_not_a_json_object_is_read_as_empty(launch, text):
    """.venv/ap_coder_launcher.json holding ``[]`` or ``null``: ``state.get(...)`` stopped the start with an
    unexpected AttributeError."""
    launch.STATE.write_text(text, encoding="utf-8")
    assert launch.load_state() == {}
    assert launch.candidate_ports([], launch.load_state())[0] == launch.DEFAULT_PORT


# --- The dashboard ---------------------------------------------------------------------------------------------------


@pytest.fixture
def demo(tmp_path, monkeypatch):
    """The dashboard on a database with the demo invoices loaded."""
    from ap_coder.config import Settings
    from ap_coder.demo import load_demo
    from ap_coder.store import Store
    from tests.webclient import client_for

    monkeypatch.setenv("AP_REVIEWER", "Jordan Lee")
    store = Store(tmp_path / "private" / "ap_coder.db")
    load_demo(store, Settings())
    return store, client_for(store)


def test_the_approved_pdf_of_a_vendor_named_beyond_latin_1_downloads(demo):
    """A vendor named "Bœuf Œuvre Ltée" (œ is not in Latin-1, which HTTP headers are written in): the stamped PDF's
    download answered 500."""
    from ap_coder.store import APPROVED

    store, client = demo
    inv = next(i for i in store.list_invoices() if i["status"] == APPROVED)
    final = {**store.get_invoice(inv["id"])["final_output"], "vendor_name": "Bœuf Œuvre Ltée"}
    with store._conn() as conn:
        conn.execute("UPDATE invoices SET final_output = ? WHERE id = ?", (json.dumps(final), inv["id"]))
    response = client.get(f"/review/{inv['id']}/approved.pdf")
    assert response.status_code == 200 and response.content.startswith(b"%PDF")
    assert "filename*=UTF-8''B%C5%93uf%20%C5%92uvre%20Lt%C3%A9e" in response.headers["content-disposition"]


def test_a_file_in_the_invoices_folder_that_cant_be_opened_never_breaks_process_invoices(demo, monkeypatch):
    """A scan still being written, a PDF open in another program, a OneDrive placeholder: reading it to see whether it
    was processed raised, and Process invoices (the page and "Process N files") answered 500."""
    from ap_coder.store import Store
    from ap_coder.web.core import invoice_dir
    from tests.webclient import PAGE

    store, client = demo
    folder = invoice_dir(store)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "still-scanning.pdf").write_bytes(b"%PDF-1.4 half")
    real = Store.find_by_hash

    def find_by_hash(self, path, include_failed=False):
        if Path(path).name == "still-scanning.pdf":
            raise PermissionError(13, "The process cannot access the file", str(path))
        return real(self, path, include_failed)

    monkeypatch.setattr(Store, "find_by_hash", find_by_hash)
    page = client.get("/process")
    assert page.status_code == 200
    started = client.post("/process/folder", headers=PAGE)
    assert started.status_code == 200 and started.json()["job"] == "process"
    job = client.app.state.jobs.get("process")
    assert job.wait(60)
    texts = [m["text"] for m in job.snapshot()["result"]["messages"]]
    assert any("1 file could not be opened" in t for t in texts), texts


@pytest.mark.parametrize(
    ("url", "field", "said"),
    [
        ("/settings/review", "threshold", "The confidence threshold must be a number"),
        ("/settings/azure-check", "daily_pages", "The daily cap must be a number"),
        ("/settings/azure-check", "spot_percent", "The spot-check share must be a number"),
    ],
)
@pytest.mark.parametrize("value", ["1e309", "inf", "nan"])
def test_a_number_too_large_or_not_a_number_is_refused_plainly(demo, url, field, said, value):
    """Posted directly (the form's number inputs never send it): "1e309" answered 500 (OverflowError) on the review
    threshold and the Azure check's daily cap, and "nan" said "cannot convert float NaN to integer"."""
    from tests.webclient import PAGE

    _, client = demo
    response = client.post(url, data={field: value}, headers=PAGE)
    assert response.status_code == 400, response.text
    assert said in response.json()["error"]


def test_correcting_an_exported_touchless_invoice_does_not_suspend_its_supplier(demo):
    """Reopen for correction on a stale page, for an invoice approved without review and exported meanwhile: the
    reopen is refused (exported), but the supplier had already been sent back to supervised review."""
    from ap_coder import exports
    from ap_coder.capture.workflow import AUTONOMOUS_REVIEWER
    from ap_coder.store import APPROVED
    from tests.webclient import PAGE

    store, client = demo
    inv = next(i for i in store.list_invoices() if i["status"] == APPROVED)
    with store._conn() as conn:
        conn.execute("UPDATE invoices SET reviewer = ? WHERE id = ?", (AUTONOMOUS_REVIEWER, inv["id"]))
    store.create_export_batch([inv["id"]], next(iter(exports.FORMATS)), actor="Jordan Lee")
    final = store.get_invoice(inv["id"])["final_output"]
    key = store.supplier_key_for(final.get("vendor_name") or "", final.get("gst_hst_registration_number"))
    if store.get_supplier_profile(key) is None:
        store.save_supplier_profile(key, display_name=final.get("vendor_name"))
    with store._conn() as conn:  # the supplier is touchless (how its invoice was approved without review)
        conn.execute("UPDATE supplier_profiles SET state = 'autonomous' WHERE key = ?", (key,))
    answer = client.post(f"/review/{inv['id']}/correct", data={"reason": "wrong GL"}, headers=PAGE).json()
    assert "changed meanwhile" in client.get(answer["redirect"]).text
    assert store.get_supplier_profile(key)["state"] == "autonomous"
    assert store.get_invoice(inv["id"])["status"] == APPROVED


def test_a_second_approval_by_the_first_approver_says_why_it_is_refused(demo):
    """The first approver's own second approval (a stale page, or the button posted directly) said "someone else
    changed this invoice meanwhile": nobody had."""
    from ap_coder.store import PENDING
    from tests.webclient import PAGE

    store, client = demo
    inv = next(i for i in store.list_invoices() if i["status"] == "review")
    with store._conn() as conn:
        conn.execute("UPDATE invoices SET status = ?, reviewer = 'Jordan Lee' WHERE id = ?", (PENDING, inv["id"]))
    answer = client.post(f"/review/{inv['id']}/final-approve", headers=PAGE).json()
    page = client.get(answer["redirect"]).text
    assert "someone else changed" not in page
    assert "the second approval must come from someone other than the first approver" in page
    assert store.get_invoice(inv["id"])["status"] == PENDING


# --- Starting the server by hand: python -m ap_coder serve ------------------------------------------------------------


def test_serve_without_a_port_never_picks_one_the_system_refuses(monkeypatch):
    """``python -m ap_coder serve`` chose its port by connecting (nothing answers = free), so a port Windows reserves
    for Hyper-V / WSL (connect refused, bind refused too) was chosen and the server failed to start; the launcher's
    bind test already passed over those. Both now use the same one (``offline.free_port``)."""
    import socket as real

    from ap_coder import cli
    from ap_coder.web import server

    refused = set(range(8501, 8561))  # (the 50 from 8501 and a few more)

    class Probe(real.socket):
        def bind(self, address):
            if address[1] in refused:
                raise OSError(10013, "An attempt was made to access a socket in a way forbidden by its permissions")
            return super().bind(address)

    monkeypatch.setattr(real, "socket", Probe)
    chosen = {}
    monkeypatch.setattr(server, "serve", lambda host, port, **kwargs: chosen.update(port=port) or 0)
    assert cli.cmd_serve(cli.build_parser().parse_args(["serve", "--no-browser"])) == 0
    assert chosen["port"] not in refused and chosen["port"] > 0


def test_serve_on_a_port_another_ap_coder_answers_on_leaves_its_server_file_alone(tmp_path, capsys):
    """``python -m ap_coder serve --port N`` while an AP Coder runs on N: it wrote its own server file over the
    running one's (losing the token the launcher stops it with), then deleted it when its own start failed; and it
    said "AP Coder is running at" about the other server. Now it says the port is in use and starts nothing."""
    import socket

    from ap_coder.web import server

    path = server.write_server_file(1, "the running one's token")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        port = s.getsockname()[1]
        assert server.serve("127.0.0.1", port, open_browser=False) == 1
    assert json.loads(path.read_text(encoding="utf-8"))["token"] == "the running one's token"
    assert f"Port {port} is in use" in capsys.readouterr().err


# --- A number larger than SQLite keeps -------------------------------------------------------------------------------

HUGE = "99999999999999999999"  # beyond 2**63: SQLite refuses it with OverflowError


@pytest.mark.parametrize(
    "url", [f"/review/{HUGE}", f"/review/{HUGE}/page/1.png", f"/review/{HUGE}/words", f"/search/{HUGE}/approved.pdf"]
)
def test_an_invoice_number_too_large_to_exist_is_not_found(demo, url):
    """An address typed or pasted with a long number (a document number instead of the invoice's): the page answered
    500 Internal Server Error instead of "not found"."""
    _, client = demo
    response = client.get(url, headers={"Accept": "text/html"})
    assert response.status_code == 404
    assert "Internal Server Error" not in response.text


@pytest.mark.parametrize("url", ["/review/bulk-approve", "/learning/memory/forget", f"/review/{HUGE}/delete"])
def test_a_posted_number_too_large_to_exist_is_refused_plainly(demo, url):
    from tests.webclient import post

    _, client = demo
    response = post(client, url, {"ids": HUGE})
    assert response.status_code == 404 and response.json()["ok"] is False
