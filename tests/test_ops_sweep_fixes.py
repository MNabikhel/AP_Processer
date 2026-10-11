"""Regression tests for bugs found sweeping the install, command line, exports and data handling. Each test
reproduces the bug as it was found, so it is not made again."""

from __future__ import annotations

from ap_coder import jde

from .test_jde import AN8, ON, _files, _invoice

# --- JD Edwards: dates and field widths ------------------------------------------------------------------------


def test_jde_a_date_e1_cannot_hold_is_left_out_not_a_crash():
    """An invoice dated 1899 (a typo for 1999 / 2026) passed the checks, then the batch's ZIP could not be built
    (julian() raised): the whole batch, every invoice in it, could not be downloaded."""
    settings = jde.JdeSettings()
    for changes in ({"invoice_date": "1899-05-01"}, {"due_date": "0026-05-01"}):
        inv = _invoice(ON, **changes)
        problems = jde.validate([inv], settings, AN8)
        assert problems and "JD Edwards" in " ".join(problems[1]), changes
    good = _invoice(ON, inv_id=2)
    zipped, _, _ = jde.build_zip([good], settings, AN8, batch=1)  # the others still go out
    assert zipped
    credit = _invoice(ON, inv_id=3, due_date="0026-05-01", grand_total=-10.0)  # a credit's due date isn't sent
    assert all("due date" not in p for p in jde.validate([credit], settings, AN8).get(3, []))


def test_jde_text_made_formula_safe_still_fits_its_column():
    """A description starting with "-" (a discount line) gets an apostrophe in front so a spreadsheet does not run
    it: a 30-character VNEXA became 31, and a 25-character VLVINV 26, longer than the Z-table columns."""
    inv = _invoice(ON, invoice_number="=" + "1" * 24)
    inv["final_output"]["gl_distribution"][0]["description"] = "-" + "x" * 40
    files = _files([inv])
    line = files[jde.DIST_FILE][0]
    assert line["VNEXA"].startswith("'-") and len(line["VNEXA"]) == 30
    assert len(line["VNEXR"]) <= 30
    header = files[jde.HEADER_FILE][0]
    assert header["VLVINV"].startswith("'=") and len(header["VLVINV"]) == 25
