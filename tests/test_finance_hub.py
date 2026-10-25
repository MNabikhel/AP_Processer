"""The finance hub feed (ap_coder/erp/hub, D-103): its six tables, the files as Fabric Open Mirroring wants them, the
SQLite copy for the gateway, the push to OneLake (against a stand-in on this computer only), the command line, the
schedule and Settings → Finance hub."""

from __future__ import annotations

import csv
import io
import json
import re
import sqlite3
import threading
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from ap_coder.core.store import Store
from ap_coder.erp.hub import landing, onelake, run, tables
from ap_coder.erp.hub.landing import CSV, PARQUET, HubError, MemoryDestination
from ap_coder.erp.hub.run import HubConfig, export
from ap_coder.erp.hub.tables import HUB_SCHEMA_VERSION, TABLES

from .conftest import ROOT, SAMPLES
from .webclient import client_for, follow, gt, post

# The columns of HUB_SCHEMA_VERSION 1, in order: changing one is a new version (and a new line in FINANCE_HUB.md).
COLUMNS_V1 = {
    "invoices": "invoice_id status coding_source vendor_name vendor_key vendor_erp_id invoice_number invoice_date "
    "due_date po_number payment_terms currency subtotal gst_amount hst_amount pst_amount qst_amount "
    "other_tax_amount tax_total grand_total is_credit_note original_invoice_number supplier_province "
    "ship_to_province line_count confidence needs_review source_file source_channel received_at first_approved_by "
    "first_approved_at second_approved_by second_approved_at touchless parked_reason export_batch exported_at "
    "jde_voucher jde_doc_type jde_company jde_posted_at invoice_url",
    "invoice_lines": "invoice_id line_position line_number description quantity unit_price amount gl_account "
    "cost_center tax_code taxes_applied coding_source",
    "approvals": "approval_id invoice_id step approver_name approver_account approver_role approved_at amount "
    "currency needs_second second_reason bulk touchless withdrawn withdrawn_at",
    "activity": "event_id invoice_id action actor occurred_at detail_json",
    "accuracy": "accuracy_id invoice_id vendor_key line_number field proposed approved correct source recorded_at",
    "workflow_runs": "run_id kind source actor started_at finished_at seconds items succeeded failed skipped "
    "total_amount status errors",
}


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "data" / "ap_coder.db")
    s.set_setting("approval_limit", "10000")
    return s


def _approved(store: Store, tmp_path: Path, name: str = "a.pdf", **changes) -> int:
    doc = {**gt(), **changes}
    invoice_id = store.add_invoice(tmp_path / name, doc, {"requires_review": False, "adjusted_confidence": 0.9})
    final = json.loads(json.dumps(doc))
    final["line_items"][0]["predicted_gl_code"] = "6999"  # one correction
    store.approve_invoice(invoice_id, final, "Jane")
    return invoice_id


def _rows(dest: MemoryDestination, table: str) -> list[dict]:
    """Every row of a table's CSV files, in file order."""
    out = []
    for name in sorted(n for n in dest.list(table) if n.endswith(".csv")):
        out += list(csv.DictReader(io.StringIO(dest.files[f"{table}/{name}"].decode("utf-8"))))
    return out


def csv_config(**kw) -> HubConfig:
    return HubConfig(enabled=True, format=CSV, **kw)


# --- The tables -----------------------------------------------------------------------------------------------------


def test_the_six_tables_keep_their_names_and_columns():
    assert HUB_SCHEMA_VERSION == 1
    assert {n: " ".join(t.names) for n, t in TABLES.items()} == COLUMNS_V1
    for table in TABLES.values():
        assert set(table.key) <= set(table.names)
        assert all(c.type in tables.TYPES and c.meaning for c in table.columns)


def test_the_data_dictionary_describes_every_table_and_column():
    text = (ROOT / "docs" / "FINANCE_HUB.md").read_text(encoding="utf-8")
    assert f"HUB_SCHEMA_VERSION = {HUB_SCHEMA_VERSION}" in text
    for table in TABLES.values():
        assert f"### `{table.name}`" in text, table.name
        section = text.split(f"### `{table.name}`", 1)[1].split("\n### ", 1)[0]
        for c in table.columns:
            assert re.search(rf"^\| `{c.name}` \| {re.escape(c.type)} \|", section, re.M), (table.name, c.name)


def test_each_table_is_built_from_the_store(store, tmp_path):
    first = _approved(store, tmp_path)  # 18,017.85: over the limit, waits for a second approval
    store.final_approve(first, "Sam")
    small = store.add_invoice(tmp_path / "b.pdf", {**gt(), "invoice_number": "X-2", "grand_total": 50.0}, {})
    store.park_invoice(small, "Jane", "waiting for the buyer")
    store.create_export_batch([first], "csv", actor="Jane")
    store.record_import_run("dashboard", files=3, ok=2, failed=1, errors=["bad.pdf: unreadable"], actor="Jane")
    ctx = tables.Context(link_base="https://ap.example.test/")

    inv = {r["invoice_id"]: r for r in tables.build(store, "invoices", ctx)}
    one = inv[first]
    assert one["status"] == "approved" and one["coding_source"] == "approved"
    assert one["hst_amount"] == 2072.85 and one["gst_amount"] is None and one["grand_total"] == 18017.85
    assert one["first_approved_by"] == "Jane" and one["second_approved_by"] == "Sam" and one["touchless"] is False
    assert one["export_batch"] == 1 and one["exported_at"] and one["invoice_date"] == "2026-09-14"
    assert one["invoice_url"] == f"https://ap.example.test/review/{first}" and one["source_file"] == "a.pdf"
    assert inv[small]["status"] == "parked" and inv[small]["parked_reason"] == "waiting for the buyer"
    assert inv[small]["coding_source"] == "proposed"

    lines = [r for r in tables.build(store, "invoice_lines", ctx) if r["invoice_id"] == first]
    assert [r["line_position"] for r in lines] == list(range(1, len(gt()["line_items"]) + 1))
    assert lines[0]["gl_account"] == "6999" and lines[0]["tax_code"] == "HST" and lines[0]["amount"] == 4350.0

    steps = [(a["step"], a["approver_name"], a["withdrawn"]) for a in tables.build(store, "approvals", ctx)]
    assert steps == [("first", "Jane", False), ("second", "Sam", False)]

    acc = [r for r in tables.build(store, "accuracy", ctx) if r["field"] == "gl_account"]
    assert sum(1 for r in acc if r["correct"] is False) == 1 and all(r["source"] == "lines" for r in acc)
    header = [r for r in tables.build(store, "accuracy", ctx) if r["line_number"] is None]
    assert header and all(r["source"] == "computed" for r in header)  # approved without a supplier record

    runs = {r["kind"]: r for r in tables.build(store, "workflow_runs", ctx)}
    assert runs["import"]["items"] == 3 and runs["import"]["failed"] == 1 and runs["import"]["status"] == "failed"
    assert runs["erp_export"]["items"] == 1 and runs["erp_export"]["total_amount"] == 18017.85

    actions = [r["action"] for r in tables.build(store, "activity", ctx)]
    assert "approved" in actions and "import_run" in actions


def test_a_reopened_approval_is_marked_withdrawn(store, tmp_path):
    invoice_id = _approved(store, tmp_path, grand_total=500.0)
    store.reopen(invoice_id, "Jane", "wrong account")
    (approval,) = tables.build(store, "approvals", tables.Context())
    assert approval["withdrawn"] is True and approval["withdrawn_at"]


def test_sign_in_events_and_bank_details_stay_out_of_activity(store):
    store.log_event("login_failed", actor="someone", detail={"username": "x"})
    store.log_event(
        "approved",
        1,
        "Jane",
        {
            "changes": [{"what": "Remit bank account", "before": "****1234", "after": "****9876"}],
            "bank_account": "12345",
        },
    )
    rows = tables.build(store, "activity", tables.Context())
    assert "login_failed" not in [r["action"] for r in rows]
    (approved,) = [r for r in rows if r["action"] == "approved"]
    assert "1234" not in approved["detail_json"] and "12345" not in approved["detail_json"]


# --- Runs to a folder ---------------------------------------------------------------------------------------------


def test_a_run_lays_the_files_out_as_open_mirroring_wants(store, tmp_path):
    _approved(store, tmp_path)
    target = tmp_path / "share"
    result = export(store, csv_config(folder=str(target)), actor="Jane")
    assert result.ok, result.error
    for name, table in TABLES.items():
        folder = target / name
        assert json.loads((folder / "_metadata.json").read_text(encoding="utf-8"))["keyColumns"] == list(table.key)
        schema = json.loads((folder / "_schema.json").read_text(encoding="utf-8"))
        assert [c["name"] for c in schema["columns"]] == [*table.names, "__rowMarker__"]
        if not result.tables[name]["upserts"]:
            assert not list(folder.glob("0*.csv"))  # the folder and its metadata, waiting for rows
            continue
        assert (folder / "00000000000000000001.csv").is_file()
        header = (folder / "00000000000000000001.csv").read_text(encoding="utf-8").splitlines()[0]
        assert header.split(",")[-1] == "__rowMarker__"
    hub = json.loads((target / "_hub.json").read_text(encoding="utf-8"))
    assert hub["schema_version"] == HUB_SCHEMA_VERSION and hub["format"] == CSV
    assert not list((target / "_staging").iterdir())  # every file renamed into place
    assert not list(target.rglob("*.pdf"))  # never the invoices themselves


def test_only_what_changed_goes_in_the_next_file_and_a_deleted_invoice_is_a_delete(store, tmp_path):
    dest = MemoryDestination()
    first = _approved(store, tmp_path, grand_total=500.0)
    other = store.add_invoice(tmp_path / "b.pdf", {**gt(), "invoice_number": "B-1"}, {})
    assert export(store, csv_config(), destination=dest).ok
    assert len(_rows(dest, "invoices")) == 2

    nothing = export(store, csv_config(), destination=dest)  # only the record of the last run is new
    assert (
        nothing.ok
        and nothing.rows == 0
        and dest.list("invoices") == ["00000000000000000001.csv", "_metadata.json", "_schema.json"]
    )
    store.reject_invoice(other, "Jane", "a copy")
    store.delete_invoice(other, actor="Jane")
    changed = export(store, csv_config(), destination=dest)
    assert changed.ok and "invoices/00000000000000000002.csv" in changed.files
    second = list(csv.DictReader(io.StringIO(dest.files["invoices/00000000000000000002.csv"].decode())))
    assert [(r["invoice_id"], r["__rowMarker__"]) for r in second] == [(str(other), "2")]
    activity = _rows(dest, "activity")
    assert len({r["event_id"] for r in activity}) == len(activity)  # each event sent once
    assert first


def test_full_rebuild_sends_every_row_again(store, tmp_path):
    dest = MemoryDestination()
    _approved(store, tmp_path, grand_total=500.0)
    export(store, csv_config(), destination=dest)
    again = export(store, csv_config(), full=True, destination=dest)
    assert again.ok and again.full and again.tables["invoices"]["upserts"] == 1
    assert again.tables["activity"]["upserts"] == len(tables.build(store, "activity", tables.Context())) - 1


def test_a_dry_run_writes_and_records_nothing(store, tmp_path):
    _approved(store, tmp_path, grand_total=500.0)
    dest = MemoryDestination()
    events = len(store.events())
    result = export(store, csv_config(), dry_run=True, destination=dest)
    assert result.ok and result.rows > 0 and "would send" in result.summary()
    assert dest.files == {} and len(store.events()) == events and run.last_run(store) is None


def test_a_database_put_back_from_a_backup_is_sent_in_full(store, tmp_path):
    dest = MemoryDestination()
    _approved(store, tmp_path, grand_total=500.0)
    backup = store.backup_now()
    export(store, csv_config(), destination=dest)
    store.log_event("note", 1, "Jane", {"text": "after the backup"})
    export(store, csv_config(), destination=dest)
    store.restore_from(backup)
    result = export(store, csv_config(), destination=dest)
    assert result.ok and result.full and result.tables["activity"]["deletes"] >= 1


def test_parquet_when_pyarrow_is_there(store, tmp_path):
    pq = pytest.importorskip("pyarrow.parquet")
    _approved(store, tmp_path, grand_total=500.0)
    target = tmp_path / "share"
    result = export(store, HubConfig(enabled=True, folder=str(target)))
    assert result.ok and result.format == PARQUET
    table = pq.read_table(target / "invoices" / "00000000000000000001.parquet")
    assert table.column_names[-1] == "__rowMarker__"
    assert str(table.schema.field("grand_total").type) == "decimal128(18, 2)"
    assert str(table.schema.field("invoice_date").type) == "date32[day]"


def test_csv_with_a_schema_when_pyarrow_is_not(store, tmp_path, monkeypatch):
    monkeypatch.setattr(landing, "parquet_available", lambda: False)
    assert landing.choose_format("auto") == CSV
    with pytest.raises(HubError, match="pyarrow"):
        landing.choose_format(PARQUET)
    _approved(store, tmp_path, grand_total=500.0)
    target = tmp_path / "share"
    assert export(store, HubConfig(enabled=True, folder=str(target))).format == CSV
    meta = json.loads((target / "invoices" / "_metadata.json").read_text(encoding="utf-8"))
    assert meta["fileFormat"] == "DelimitedText" and meta["firstRowAsHeader"] is True


def test_the_gateway_gets_a_read_only_sqlite_copy_with_views(store, tmp_path):
    first = _approved(store, tmp_path, grand_total=500.0)
    target = tmp_path / "share"
    for _ in range(2):  # the second run replaces the read-only file
        store.log_event("note", first, "Jane", {"text": "again"})
        assert export(store, csv_config(folder=str(target))).ok
    path = target / "_gateway" / "finance_hub.db"
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        assert conn.execute("SELECT COUNT(*) FROM v_approved_invoices").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM v_invoice_lines").fetchone()[0] == len(gt()["line_items"])
        assert conn.execute("SELECT value FROM _hub_info WHERE key = 'schema_version'").fetchone()[0] == "1"
        assert conn.execute("SELECT checked FROM v_accuracy_by_field WHERE field = 'gl_account'").fetchone()[0]
    finally:
        conn.close()
    import os

    assert not os.access(path, os.W_OK) or os.name == "nt" or os.geteuid() == 0


def test_a_folder_that_cannot_be_written_fails_plainly_and_is_recorded(store, tmp_path):
    blocker = tmp_path / "a-file"
    blocker.write_text("x", encoding="utf-8")
    result = export(store, csv_config(folder=str(blocker / "share")), actor="Jane")
    assert not result.ok and "can't be reached" in result.error
    (event,) = store.events(actions=["hub_export"])
    assert event["actor"] == "Jane" and event["detail"]["ok"] is False
    assert run.last_run(store)["ok"] is False
    assert not export(store, csv_config()).ok  # no folder chosen


def test_one_run_at_a_time(store, tmp_path):
    folder = tmp_path / "data" / "finance_hub"
    with run._run_lock(folder):
        result = export(store, csv_config(), destination=MemoryDestination())
    assert not result.ok and "already under way" in result.error


# --- Option B: OneLake ----------------------------------------------------------------------------------------------


class FakeOneLake(BaseHTTPRequestHandler):
    """Microsoft Entra ID's token endpoint and OneLake's DFS API, on this computer, in memory."""

    files: dict[str, bytes] = {}
    calls: list[tuple[str, str]] = []
    secret = "s3cret"
    page_size = 0  # > 0: a listing is answered this many names at a time, with x-ms-continuation

    def log_message(self, *args):  # quiet
        pass

    def _answer(self, code: int, body: bytes = b"", kind: str = "application/json") -> None:
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> bytes:
        return self.rfile.read(int(self.headers.get("Content-Length") or 0))

    def _authorised(self) -> bool:
        if self.headers.get("Authorization") != "Bearer token-1":
            self._answer(401, b'{"error": {"code": "InvalidAuthenticationInfo"}}')
            return False
        return True

    def do_POST(self):
        form = urllib.parse.parse_qs(self._body().decode())
        self.calls.append(("POST", self.path))
        if form.get("client_secret") != [self.secret] or form.get("scope") != ["https://storage.azure.com/.default"]:
            return self._answer(401, b'{"error": "invalid_client"}')
        self._answer(200, b'{"access_token": "token-1", "expires_in": 3600}')

    def do_PUT(self):
        url = urllib.parse.urlsplit(self.path)
        self.calls.append(("PUT", url.path + ("?" + url.query if url.query else "")))
        if not self._authorised():
            return
        path = urllib.parse.unquote(url.path)
        source = self.headers.get("x-ms-rename-source")
        if source:
            self.files[path] = self.files.pop(urllib.parse.unquote(source))
        else:
            self.files[path] = b""
        self._answer(201)

    def do_PATCH(self):
        url = urllib.parse.urlsplit(self.path)
        query = urllib.parse.parse_qs(url.query)
        self.calls.append(("PATCH", query["action"][0]))
        if not self._authorised():
            return
        body = self._body()
        if query["action"] == ["append"]:
            self.files[urllib.parse.unquote(url.path)] += body
        self._answer(202 if query["action"] == ["append"] else 200)

    def do_GET(self):
        url = urllib.parse.urlsplit(self.path)
        query = urllib.parse.parse_qs(url.query)
        if not self._authorised():
            return
        prefix = "/" + url.path.strip("/") + "/" + query["directory"][0] + "/"
        names = [p[len(prefix) - 1 :] for p in self.files if p.startswith(prefix) and "/" not in p[len(prefix) :]]
        if not names:
            return self._answer(404, b'{"error": {"code": "PathNotFound"}}')
        start = int((query.get("continuation") or ["0"])[0])
        size = self.page_size or len(names)
        page, more = sorted(names)[start : start + size], start + size < len(names)
        body = json.dumps({"paths": [{"name": n, "isDirectory": "false"} for n in page]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        if more:
            self.send_header("x-ms-continuation", str(start + size))
        self.end_headers()
        self.wfile.write(body)

    def do_DELETE(self):
        self.files.pop(urllib.parse.unquote(urllib.parse.urlsplit(self.path).path), None)
        self._answer(200)


@pytest.fixture
def fake_onelake(monkeypatch):
    monkeypatch.setattr(onelake, "ALLOW_TEST_HTTP", True)  # plain http to this computer: the tests' stand-in only
    FakeOneLake.files, FakeOneLake.calls, FakeOneLake.page_size = {}, [], 0
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeOneLake)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def onelake_config(base: str, **kw) -> HubConfig:
    return HubConfig(enabled=True, mode="onelake", format=CSV, onelake_url=f"{base}/ws/db/Files/LandingZone",
                     tenant_id="tenant-1", client_id="app-1", secret=FakeOneLake.secret, authority=base,
                     push_opt_in=True, **kw)  # fmt: skip


def test_the_push_writes_the_same_files_to_onelake(store, tmp_path, fake_onelake, monkeypatch):
    monkeypatch.setenv("AP_ALLOW_INTERNET", "1")
    _approved(store, tmp_path, grand_total=500.0)
    result = export(store, onelake_config(fake_onelake), actor="Jane")
    assert result.ok, result.error
    names = sorted(FakeOneLake.files)
    assert "/ws/db/Files/LandingZone/invoices/00000000000000000001.csv" in names
    assert "/ws/db/Files/LandingZone/invoices/_metadata.json" in names
    assert not [n for n in names if "/_staging/" in n]  # staged, then renamed
    assert ("POST", "/tenant-1/oauth2/v2.0/token") in FakeOneLake.calls
    store.log_event("note", 1, "Jane", {"text": "more"})
    assert export(store, onelake_config(fake_onelake)).ok  # numbering read back from OneLake
    assert "/ws/db/Files/LandingZone/activity/00000000000000000002.csv" in FakeOneLake.files
    assert result.mode == "onelake"


def test_the_push_needs_the_internet_and_an_admins_opt_in(store, fake_onelake, monkeypatch):
    result = export(store, onelake_config(fake_onelake))
    assert not result.ok and "AP_ALLOW_INTERNET" in result.error and FakeOneLake.calls == []
    monkeypatch.setenv("AP_ALLOW_INTERNET", "1")
    off = onelake_config(fake_onelake)
    off.push_opt_in = False
    assert "off" in export(store, off).error and FakeOneLake.calls == []


def test_a_wrong_secret_or_address_is_said_plainly(store, fake_onelake, monkeypatch):
    monkeypatch.setenv("AP_ALLOW_INTERNET", "1")
    wrong = onelake_config(fake_onelake)
    wrong.secret = "nope"
    result = export(store, wrong)
    assert not result.ok and "client secret" in result.error and "nope" not in result.error
    with pytest.raises(HubError, match="https://"):
        onelake.OneLakeDestination("https://evil.example.com/ws/db", "t", "c", "s", opted_in=True)
    with pytest.raises(HubError, match="sign-in address"):
        onelake.OneLakeDestination("https://onelake.dfs.fabric.microsoft.com/ws/db", "t", "c", "s",
                                   authority="https://login.evil.example.com", opted_in=True)  # fmt: skip


def test_the_secret_is_kept_in_the_data_folder_and_never_logged(store, tmp_path):
    changed = run.save_config(store, csv_config(folder=str(tmp_path / "share")), actor="Jane", secret="top-secret")
    assert run.SECRET_ENV in changed
    env = (tmp_path / "data" / ".env").read_text(encoding="utf-8")
    assert "top-secret" in env and run.HubConfig.load(store).secret == "top-secret"
    assert "top-secret" not in json.dumps(store.events(), default=str)
    assert all("secret" not in k for k in HubConfig().stored())


# --- The schedule ---------------------------------------------------------------------------------------------------


def test_the_schedule_runs_every_n_hours_once_turned_on():
    import datetime as dt

    now = dt.datetime(2026, 10, 25, 12, 0)
    on = HubConfig(enabled=True, every_hours=4)
    assert run.due(on, None, now)
    assert not run.due(on, {"at": "2026-10-25T09:00:00"}, now)
    assert run.due(on, {"at": "2026-10-25T08:00:00"}, now)
    assert not run.due(HubConfig(enabled=False), None, now)
    assert not run.due(HubConfig(enabled=True, every_hours=0), None, now)


def test_the_dashboard_runs_the_feed_on_its_schedule(store, tmp_path):
    _approved(store, tmp_path, grand_total=500.0)
    run.save_config(store, csv_config(folder=str(tmp_path / "share"), every_hours=1))
    client = client_for(store)
    schedule = client.app.state.hub_schedule
    schedule.every = 0
    client.get("/help")
    schedule.thread.join(30)
    assert (tmp_path / "share" / "invoices" / "00000000000000000001.csv").is_file()
    assert store.events(actions=["hub_export"])[0]["actor"] == run.SCHEDULE_ACTOR


# --- The command line -----------------------------------------------------------------------------------------------


def test_hub_export_on_the_command_line(store, tmp_path, capsys):
    from ap_coder.ops.cli import main

    _approved(store, tmp_path, grand_total=500.0)
    db = str(store.path)
    assert main(["--db", db, "hub", "export"]) == 1
    assert "off" in capsys.readouterr().err
    assert main(["--db", db, "hub", "export", "--dry-run"]) == 0
    assert "would send" in capsys.readouterr().out
    run.save_config(store, csv_config(folder=str(tmp_path / "share")))
    assert main(["--db", db, "hub", "export"]) == 0
    assert "sent" in capsys.readouterr().out
    assert main(["--db", db, "hub", "export", "--full"]) == 0
    assert "Full rebuild" in capsys.readouterr().out


def test_imports_are_recorded_as_runs(store, tmp_path, monkeypatch):
    from ap_coder.ops.cli import main

    folder = tmp_path / "in"
    folder.mkdir()
    (folder / "x.md").write_text((SAMPLES / "northwind_ON_HST_NW-2026-0912.md").read_text(encoding="utf-8"),
                                 encoding="utf-8")  # fmt: skip
    main(["--db", str(store.path), "process", str(folder), "--out", str(tmp_path / "out")])
    (event,) = store.events(actions=["import_run"])
    assert event["detail"]["source"] == "process" and event["detail"]["files"] == 1


# --- Settings → Finance hub -----------------------------------------------------------------------------------------


def test_settings_finance_hub_saves_runs_and_shows_the_last_run(store, tmp_path):
    _approved(store, tmp_path, grand_total=500.0)
    client = client_for(store)
    page = client.get("/settings/hub").text
    assert "Finance hub" in page and "Off" in page
    share = tmp_path / "share"
    saved = follow(client, post(client, "/settings/hub", {
        "enabled": "on", "mode": "folder", "folder": str(share), "every_hours": "4", "format": "csv",
        "sqlite": "on", "link_base": "https://ap.example.test",
    }))  # fmt: skip
    assert "Saved" in saved and HubConfig.load(store).every_hours == 4
    ran = follow(client, post(client, "/settings/hub/run", {}))
    assert "sent" in ran and (share / "invoices" / "00000000000000000001.csv").is_file()
    assert "Last run" in client.get("/settings/hub").text


def test_settings_finance_hub_refuses_what_it_cannot_use(store):
    client = client_for(store)
    answer = post(client, "/settings/hub", {"enabled": "on", "mode": "folder", "folder": "", "every_hours": "24"})
    assert answer.status_code == 400 and "folder" in answer.json()["error"]
    answer = post(client, "/settings/hub", {"mode": "folder", "folder": "x", "every_hours": "7"})
    assert answer.status_code == 400


def test_the_finance_hub_is_an_admins_and_records_who_ran_it_on_the_server(store, tmp_path):
    from fastapi.testclient import TestClient

    from ap_coder.web import roles
    from ap_coder.web.app import create_app
    from ap_coder.web.identity import SESSION_COOKIE

    from .webclient import web_settings

    for route in ("POST /settings/hub", "POST /settings/hub/run"):
        method, _, path = route.partition(" ")
        assert roles.RULES[(method, path)] == roles.ADMIN
    run.save_config(store, csv_config(folder=str(tmp_path / "share")))
    app = create_app(web_settings(), store, start_reader=False, server=True)
    uid = store.add_user(
        "the.admin", "correct horse battery", role="admin", display_name="Ada Admin", must_change=False
    )
    client = TestClient(app, base_url="https://localhost", client=("10.0.0.5", 50000))
    client.cookies.set(SESSION_COOKIE, store.start_session(uid))
    store.log_event("note", None, "Ada Admin", {"text": "work to send"})  # (account events are not sent, D-105)
    answer = post(client, "/settings/hub/run", {})
    assert answer.status_code == 200, answer.text
    assert store.events(actions=["hub_export"])[0]["actor"] == "Ada Admin"


# --- D-105: the review's findings -----------------------------------------------------------------------------------


def _marks(dest: MemoryDestination, table: str) -> list[tuple[str, str]]:
    found = []
    for name in dest.list(table):
        if name.endswith(".csv"):
            rows = csv.DictReader(io.StringIO(dest.files[f"{table}/{name}"].decode("utf-8")))
            found += [(r[TABLES[table].key[0]], r["__rowMarker__"]) for r in rows]
    return found


def test_a_new_schema_version_at_the_same_destination_still_deletes_rows_gone(store, tmp_path, monkeypatch):
    dest = MemoryDestination()
    _approved(store, tmp_path, grand_total=500.0)
    other = store.add_invoice(tmp_path / "b.pdf", {**gt(), "invoice_number": "B-1"}, {})
    assert export(store, csv_config(), destination=dest).ok
    store.reject_invoice(other, "Jane", "x")
    store.delete_invoice(other, actor="Jane")
    monkeypatch.setattr(run, "HUB_SCHEMA_VERSION", 2)
    result = export(store, csv_config(), destination=dest)
    assert result.ok and result.full and result.tables["invoices"]["deletes"] == 1
    assert (str(other), "2") in _marks(dest, "invoices")


def test_account_and_settings_events_stay_out_of_activity(store):
    for action in ("user_added", "role_changed", "user_activated", "user_deactivated", "backup_restored",
                   "settings_changed", "login", "logout", "login_failed", "login_mfa_failed"):  # fmt: skip
        store.log_event(action, actor="Admin", detail={"username": "ann"})
    store.log_event("note", 1, "Jane", {"text": "kept"})
    assert [r["action"] for r in tables.build(store, "activity", tables.Context())] == ["note"]


def test_a_gateway_copy_that_fails_is_a_warning_not_a_failed_run(store, tmp_path, monkeypatch):
    _approved(store, tmp_path, grand_total=500.0)

    def broken(*args, **kwargs):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(landing, "gateway_database", broken)
    share = tmp_path / "share"
    result = export(store, csv_config(folder=str(share)))
    assert result.ok, result.error
    assert any("Warning" in n and "SQLite" in n for n in result.notes) and "Warning" in result.summary()
    assert any(p.name.endswith(".csv") for p in (share / "invoices").iterdir())
    store.log_event("note", 1, "Jane", {"text": "more"})
    second = export(store, csv_config(folder=str(share)))
    assert second.ok and second.tables["invoices"]["upserts"] == 0  # the first run was recorded as sent


def test_the_onelake_listing_follows_the_continuation(store, tmp_path, fake_onelake, monkeypatch):
    monkeypatch.setenv("AP_ALLOW_INTERNET", "1")
    dest = onelake_config(fake_onelake).destination()
    for n in range(1, 6):
        dest.write(f"activity/{n:020d}.csv", b"x")
    FakeOneLake.page_size = 2
    assert dest.list("activity") == [f"{n:020d}.csv" for n in range(1, 6)]
    assert landing.last_sequence(dest.list("activity")) == 5


def test_the_onelake_push_never_uses_a_proxy_nor_plain_http(monkeypatch):
    monkeypatch.setenv("AP_ALLOW_INTERNET", "1")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example.com:3128")
    dest = onelake.OneLakeDestination("https://onelake.dfs.fabric.microsoft.com/ws/db/Files/LandingZone", "t", "c",
                                      "s", opted_in=True)  # fmt: skip
    assert not [h for h in dest._opener.handlers if isinstance(h, urllib.request.ProxyHandler) and h.proxies]
    with pytest.raises(HubError, match="https://"):
        onelake.OneLakeDestination("http://127.0.0.1:1/ws/db", "t", "c", "s", authority="https://127.0.0.1:1",
                                   opted_in=True)  # fmt: skip
