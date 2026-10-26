"""The fixes from an end-to-end review (B-1211 on): the UNASSIGNED cost center in the ERP files, the command line's
setup gate, the doctor in server mode, the Exports buttons for Read-only, the Learning page's headline and the hub's
activity."""

from __future__ import annotations

from ap_coder.erp import exports, jde

from .conftest import copy_database
from .test_jde import AN8, ON, _files, _invoice


def _unassigned_cost_centers(stem=ON):
    inv = _invoice(stem)
    for li in inv["final_output"]["line_items"]:
        li["predicted_cost_center"] = "UNASSIGNED"
    for e in inv["final_output"]["gl_distribution"]:
        if e.get("cost_center"):
            e["cost_center"] = "UNASSIGNED"
    return inv


def test_an_unassigned_cost_center_is_blank_in_every_erp_file():
    """B-1211: the literal "UNASSIGNED" went into the CSV/Excel/Custom files' Cost center column and into JD Edwards as
    the business unit (UNASSIGNED.1500), even with a default business unit set."""
    inv = _unassigned_cost_centers()
    rows = exports.line_rows([inv])
    assert rows and {r["cost_center"] for r in rows} == {""}
    settings = jde.JdeSettings(amount_mode=jde.AMOUNT_GROSS, default_bu="100")
    assert jde.validate([inv], settings, AN8) == {}
    lines = _files([inv], settings)[jde.DIST_FILE]
    assert lines and all(r["VNANI"].startswith("100.") for r in lines), [r["VNANI"] for r in lines]
    assert jde.account_for("1500", "UNASSIGNED", settings).bu == "100"
    # Without a default business unit the line is held back, never sent with UNASSIGNED.
    assert jde.account_for("1500", "UNASSIGNED", jde.JdeSettings()) is None


def _cli_args(tmp_path, *args):
    return ["--env-file", str(tmp_path / "none.env"), "--db", str(tmp_path / "a.db"), *args]


def test_process_and_watch_refuse_until_the_gl_accounts_are_imported(tmp_path, monkeypatch, capsys):
    """B-1212: `process` and `watch --once` read and queued invoices on a fresh data folder, coded against the bundled
    sample GL accounts, while the dashboard refuses until the GL accounts are imported; the queued invoices then could
    not be checked ("Import your GL accounts first")."""
    import os
    import shutil

    from ap_coder.core.store import Store, load_sample_setup
    from ap_coder.ops import cli

    from .conftest import SAMPLES

    for key in [k for k in os.environ if k.startswith(("AZURE_", "AP_LLM_"))]:
        monkeypatch.delenv(key)
    folder = tmp_path / "in"
    folder.mkdir()
    shutil.copy(SAMPLES / f"{ON}.md", folder / "a.md")
    os.utime(folder / "a.md", (1_700_000_000, 1_700_000_000))
    out = ["-o", str(tmp_path / "out"), "--cache-dir", ""]
    assert cli.main(_cli_args(tmp_path, "process", str(folder), *out)) == 2
    assert "import your GL accounts" in capsys.readouterr().err
    assert cli.main(_cli_args(tmp_path, "watch", str(folder), "--once", "--cache-dir", "")) == 1
    assert "import your GL accounts" in capsys.readouterr().err
    assert Store(tmp_path / "a.db").list_invoices() == []
    load_sample_setup(Store(tmp_path / "a.db"))  # the sample setup (the demo) is a setup: it reads
    assert cli.main(_cli_args(tmp_path, "process", str(folder), *out)) == 0
    assert len(Store(tmp_path / "a.db").list_invoices()) == 1


def test_the_doctor_checks_the_server_accounts_the_jd_edwards_api_and_the_hub_feed(tmp_path, monkeypatch, capsys):
    """B-1213: with AP_SERVER_MODE=1 and no accounts the doctor said 0 failures (nobody can log in), and it had no line
    for the JD Edwards API (on, password, last test, vouchers waiting) nor the finance hub feed (on, folder, last
    run)."""
    import json

    from ap_coder.core.store import Store
    from ap_coder.erp import jde_ais
    from ap_coder.ops import cli, offline

    data = tmp_path / "data"
    data.mkdir()
    db = data / "ap_coder.db"
    store = Store(db)
    monkeypatch.setenv("AP_PRIVATE_DIR", str(data))
    monkeypatch.setenv(offline.SERVER_ENV, "1")
    monkeypatch.delenv(jde_ais.PASSWORD_ENV, raising=False)
    args = ["--env-file", str(tmp_path / "none.env"), "--db", str(db), "doctor"]
    assert cli.main(args) == 1
    out = capsys.readouterr().out
    assert "[FAIL] server accounts" in out and "python -m ap_coder users add --admin <name>" in out
    assert "[SKIP] JD Edwards API" in out and "[SKIP] finance hub feed" in out

    store.add_user("jsmith", "a long enough password 1", role="admin")
    store.set_setting(jde_ais.SETTING_KEY, json.dumps({"base_url": "https://jde.company.local:9300", "step2": True}))
    store.set_setting("hub_enabled", "on")
    store.set_setting("hub_folder", str(tmp_path / "nowhere"))
    assert cli.main(args) == 1
    out = capsys.readouterr().out
    assert "[PASS] server accounts" in out and "1 account" in out
    assert "[FAIL] JD Edwards API" in out and "No password saved" in out
    assert "JD Edwards API test" in out and "never tested" in out
    assert "[PASS] JD Edwards API queue" in out and "nothing waiting" in out
    assert "finance hub feed" in out and "nowhere" in out and "never run" in out


def test_read_only_is_not_offered_the_export_batch_downloads(tmp_path, setup_demo_template):
    """B-1214: the Exports page showed "Download again" and "Approved PDFs (ZIP)" to Read-only: both are refused
    (403)."""
    from fastapi.testclient import TestClient

    from ap_coder.web.app import create_app

    from .test_server_role_pages import GOOD
    from .webclient import PAGE, web_settings

    store = copy_database(setup_demo_template, tmp_path / "private" / "ap_coder.db")  # sample setup, then the demo
    batch = store.create_export_batch([i["id"] for i in store.list_invoices("approved")][:1], "xlsx")
    app = create_app(web_settings(), store, start_reader=False, server=True)
    uid = store.add_user("viewer", GOOD, role="readonly")
    with store._conn() as conn:
        conn.execute("UPDATE users SET must_change = 0 WHERE id = ?", (uid,))
    client = TestClient(app, base_url="https://localhost", client=("10.0.0.5", 50000), follow_redirects=False)
    assert client.post("/login", data={"username": "viewer", "password": GOOD}, headers=PAGE).status_code in (200, 303)
    page = client.get(f"/exports?batch={batch}", headers={"Accept": "text/html"}).text
    assert f"/exports/batches/{batch}/download" not in page and "approved-pdfs.zip" not in page
    assert client.get(f"/exports/batches/{batch}/download").status_code == 403


def test_the_learning_headline_says_which_approvals_only_had_blanks_filled_in(tmp_path, ground_truth):
    """B-1215: Learning said "Approved as coded 100%, 2 of 2 needed no change" when AP had filled in every GL account
    the AI left UNASSIGNED on one of them (the same page lists those lines as "coded by AP"). Its line says how many
    only had blanks filled in by AP, which are not in the figure (D-112)."""
    import copy
    import re

    from ap_coder.core.store import Store
    from ap_coder.erp import insights
    from ap_coder.web.pages.learning import coding_context

    store = Store(tmp_path / "ap.db")
    a = store.add_invoice(tmp_path / "a.pdf", ground_truth, {"requires_review": True})
    blank = copy.deepcopy({**ground_truth, "invoice_number": "X-2"})
    for li in blank["line_items"]:
        li["predicted_gl_code"] = "UNASSIGNED"
    b = store.add_invoice(tmp_path / "b.pdf", blank, {"requires_review": True})
    store.approve_invoice(a, ground_truth, "jane")
    store.approve_invoice(b, {**ground_truth, "invoice_number": "X-2"}, "jane")
    assert insights.approved_as_coded(store) == (1, 2)  # D-112: filling in a blank is neither as coded nor corrected
    tiles = " ".join(str(t) for t in coding_context(store, "")["tiles"])
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", tiles))
    assert "2 of 2 needed no change" not in text
    assert "1 of 2 needed no change, 1 only had blanks filled in by AP" in text
    page = insights.compute(store)
    assert (page["approved_clean"], page["approved_filled"]) == (1, 1)


def test_the_hub_leaves_out_ap_s_reasons_and_tests_and_sample_setups_say_who(tmp_path, monkeypatch):
    """B-1216: the finance hub's `activity` sent AP's reason for a lesson (lesson_explained) in detail_json, although
    FINANCE_HUB.md leaves the learning memory's lessons out; jde_api_tested and the sample setup's accounts_imported
    were recorded without who did it."""
    import json

    from ap_coder.core.store import Store, load_sample_setup
    from ap_coder.erp import jde_ais
    from ap_coder.erp.hub import tables

    from .fake_ais import PASSWORD, FakeAis

    store = Store(tmp_path / "ap_coder.db")
    store.log_event("lesson_explained", actor="jane", detail={"situation": "acme|gl", "reason": "Rent goes to 6100"})
    (row,) = [r for r in tables.activity(store, tables.Context()) if r["action"] == "lesson_explained"]
    assert row["actor"] == "jane" and "Rent goes to 6100" not in row["detail_json"]
    assert "acme|gl" not in row["detail_json"]

    load_sample_setup(store, cost_centers=False, actor="jane")
    assert {e["actor"] for e in store.events(actions=["accounts_imported"])} == {"jane"}
    with FakeAis() as ais:
        store.set_setting(jde_ais.SETTING_KEY, ais.settings().to_json())
        monkeypatch.setenv(jde_ais.PASSWORD_ENV, PASSWORD)
        assert jde_ais.connection_test(store, actor="jane")["ok"]
    (tested,) = store.events(actions=["jde_api_tested"])
    assert tested["actor"] == "jane", json.dumps(tested)
