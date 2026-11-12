"""The fixes from an end-to-end review (B-1211 on): the UNASSIGNED cost center in the ERP files, the command line's
setup gate, the doctor in server mode, the Exports buttons for Read-only, the Learning page's headline and the hub's
activity."""

from __future__ import annotations

import re

from ap_coder.erp import exports, jde, jde_accounts, jde_settings, jde_zfiles
from tests.conftest import copy_database
from tests.erp.test_jde import AN8, ON, _files, _invoice


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
    settings = jde_settings.JdeSettings(amount_mode=jde_settings.AMOUNT_GROSS, default_bu="100")
    assert jde.validate([inv], settings, AN8) == {}
    lines = _files([inv], settings)[jde_zfiles.DIST_FILE]
    assert lines and all(r["VNANI"].startswith("100.") for r in lines), [r["VNANI"] for r in lines]
    assert jde_accounts.account_for("1500", "UNASSIGNED", settings).bu == "100"
    # Without a default business unit the line is held back, never sent with UNASSIGNED.
    assert jde_accounts.account_for("1500", "UNASSIGNED", jde_settings.JdeSettings()) is None


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
    from tests.conftest import SAMPLES

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
    assert "username admin and the password admin, in a browser on this server" in out  # the first sign-in (D-243)
    assert f"setup code {store.first_setup_code()}" in out  # (made by the doctor when the server has none yet)
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
    from tests.web.test_server_role_pages import GOOD
    from tests.webclient import enrol, sign_in, web_settings

    store = copy_database(setup_demo_template, tmp_path / "private" / "ap_coder.db")  # sample setup, then the demo
    batch = store.create_export_batch([i["id"] for i in store.list_invoices("approved")][:1], "xlsx")
    app = create_app(web_settings(), store, start_reader=False, server=True)
    uid = store.add_user("viewer", GOOD, role="readonly")
    with store._conn() as conn:
        conn.execute("UPDATE users SET must_change = 0 WHERE id = ?", (uid,))
    enrol(store, uid)  # (D-146)
    client = TestClient(app, base_url="https://localhost", client=("10.0.0.5", 50000), follow_redirects=False)
    assert sign_in(client, "viewer", GOOD).status_code == 200
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
    from tests.fake_ais import PASSWORD, FakeAis

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


def test_the_doctor_asks_no_gst_account_of_a_laptop_that_keys_the_gst_on_the_pay_item(tmp_path, monkeypatch, capsys):
    """B-2315: on a laptop's new data folder (JD Edwards' keying set up, docs/PROTOTYPE.md) the doctor warned "tax GST
    recoverable but no GL account mapped yet (invoices with it will error)": the GST/HST goes on the voucher's pay item
    and JD Edwards posts it itself, so no GL account is asked for and no invoice errors (B-2259)."""
    from ap_coder.bench import jde_exports
    from ap_coder.erp import jde_settings
    from ap_coder.ops import chart_cmd, cli
    from ap_coder.web.core import first_run

    data = tmp_path / "data"
    data.mkdir()
    db = data / "ap_coder.db"
    store = first_run(db, server=False)
    jde_exports.write_all(tmp_path / "jde")  # the made-up company's chart and business units, loaded as on the page
    chart_cmd.import_files(store, tmp_path / "jde" / jde_exports.CHART_FILE, tmp_path / "jde" / jde_exports.UNITS_FILE)
    monkeypatch.setenv("AP_PRIVATE_DIR", str(data))
    args = ["--env-file", str(tmp_path / "none.env"), "--db", str(db), "doctor"]
    cli.main(args)
    out = capsys.readouterr().out
    for tax in ("GST", "HST"):
        line = next(line for line in out.splitlines() if line.startswith("[") and f"tax {tax} " in line)
        assert line.startswith("[PASS]") and "JD Edwards posts it" in line and "will error" not in line, line

    settings = jde_settings.load(store)  # the tax coded to G/L lines: then the account is needed, as before
    settings.amount_mode = jde_settings.AMOUNT_GL
    jde_settings.save(store, settings)
    cli.main(args)
    assert "[WARN] tax GST" in capsys.readouterr().out


def _server_accounts(store):
    from ap_coder.ops.doctor import store_checks

    return next(c for c in store_checks(store, server=True) if c.area == "server accounts")


def test_the_doctor_fails_a_server_whose_authenticator_key_is_gone(store):
    """B-2525: "server accounts PASS" while every authenticator code is refused: the key that opens them is gone."""
    from ap_coder.core import authenticator
    from ap_coder.ops.doctor import FAIL, PASS
    from tests.webclient import enrol

    uid = store.add_user("boss", "a long admin password 1", role="admin", must_change=False)
    enrol(store, uid)
    assert _server_accounts(store).status == PASS
    (store.path.parent / authenticator.KEY_FILE).unlink()
    line = _server_accounts(store)
    assert line.status == FAIL and authenticator.KEY_FILE in line.detail, line


def test_with_the_key_gone_the_doctor_counts_only_the_active_accounts_with_an_app(store):
    """B-2525: with the key gone, the doctor said "nobody can log in" though an Admin without an app still can (a new
    key is made when they set one up): an account turned off counted, and one active Clerk with an app was all."""
    from ap_coder.core import authenticator
    from ap_coder.ops.doctor import FAIL, PASS, WARN
    from tests.webclient import enrol

    boss = store.add_user("boss", "a long admin password 1", role="admin", must_change=False)
    for name in ("carl", "dana"):
        enrol(store, store.add_user(name, "a long clerk password 1", role="clerk", must_change=False))
    store.deactivate_user(store.find_user("carl")["id"])
    store.deactivate_user(store.find_user("dana")["id"])
    (store.path.parent / authenticator.KEY_FILE).unlink()
    assert _server_accounts(store).status == PASS  # the two with an app are turned off
    with store._conn() as conn:
        conn.execute("UPDATE users SET active = 1 WHERE username = 'dana'")
    line = _server_accounts(store)
    assert line.status == WARN and "dana" in line.detail and "carl" not in line.detail and "boss" not in line.detail
    with store._conn() as conn:  # the Admin's app set up too: no Admin can log in
        conn.execute("UPDATE users SET totp_on = 1 WHERE id = ?", (boss,))
    line = _server_accounts(store)
    assert line.status == FAIL and authenticator.KEY_FILE in line.detail, line


def test_the_doctor_fails_a_server_whose_only_admin_can_no_longer_log_in(store, monkeypatch):
    """B-2525: "server accounts PASS" while the only Admin's temporary password is past its 3 days."""
    import time

    from ap_coder.core.store import users
    from ap_coder.ops.doctor import FAIL, PASS

    boss = store.add_user("boss", "a long admin password 1", role="admin")  # as `users add --admin`: temporary
    store.add_user("carl", "a long clerk password 1", role="clerk", must_change=False)
    with store._conn() as conn:  # signed in before (B-2533: a first Admin's lasts until then)
        conn.execute("UPDATE users SET last_login = '2026-11-01T09:00:00' WHERE id = ?", (boss,))
    assert _server_accounts(store).status == PASS
    later = time.time() + users.TEMPORARY_SECONDS + 60
    monkeypatch.setattr(users.time, "time", lambda: later)
    line = _server_accounts(store)
    assert line.status == FAIL and "python -m ap_coder users reset boss" in line.detail, line


def test_the_web_setup_report_says_what_the_doctor_says_of_the_taxes_jde_posts(tmp_path, monkeypatch, capsys):
    """B-2526: Help → Report a problem's setup report still warned "tax GST ... no GL account mapped yet (invoices with
    it will error)" where `python -m ap_coder doctor` passes it: the page made its own report, without what JD Edwards
    posts itself and without the database's own lines."""
    from ap_coder.bench import jde_exports
    from ap_coder.ops import chart_cmd, cli
    from ap_coder.web.core import first_run
    from ap_coder.web.pages import help as help_page

    data = tmp_path / "data"
    data.mkdir()
    db = data / "ap_coder.db"
    store = first_run(db, server=False)
    jde_exports.write_all(tmp_path / "jde")
    chart_cmd.import_files(store, tmp_path / "jde" / jde_exports.CHART_FILE, tmp_path / "jde" / jde_exports.UNITS_FILE)
    monkeypatch.setenv("AP_PRIVATE_DIR", str(data))
    monkeypatch.setenv("AP_ENV_FILE", str(tmp_path / "none.env"))
    cli.main(["--env-file", str(tmp_path / "none.env"), "--db", str(db), "doctor"])
    doctor = [line for line in capsys.readouterr().out.splitlines() if line.startswith("[")]
    help_page.forget_setup_report()
    report = help_page.setup_report(store)
    gst = next(line for line in report.splitlines() if "tax GST " in line)
    assert "will error" not in gst and "JD Edwards posts it" in gst, gst
    assert "finance hub feed" in report and "reference data source:" in report

    def areas(lines):
        return [re.split(r"\s{2,}", line, maxsplit=1)[0].split("] ", 1)[1] for line in lines if line.startswith("[")]

    assert areas(report.splitlines()) == areas(doctor)  # the same checks, in the same order
