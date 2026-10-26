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
