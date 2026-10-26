"""The fixes from an end-to-end review (B-1211 on): the UNASSIGNED cost center in the ERP files, the command line's setup
gate, the doctor in server mode, the Exports buttons for Read-only, the Learning page's headline, the hub's activity
and the duplicate-in-JD-Edwards message."""

from __future__ import annotations

from ap_coder.erp import exports, jde

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
