"""The fixes from an end-to-end walk of the company server, the JD Edwards API and the finance hub (B-1197 on): the
batch's dry run, the retry schedule, the secret scrubber, the AIS address, the reasons given, the undo and the release
of a voucher, the hub's links and files, the paused usernames, the dry run's proof and the sync's descriptions."""

from __future__ import annotations

import io
import zipfile

from ap_coder.erp import jde_post
from ap_coder.web.pages import exports

from .test_jde import ON, QC
from .test_jde_post_safety import ais, lost_answer, quick, statuses

__all__ = ["ais", "quick"]  # the fixtures, shared


# --- S1. The batch's DRY_RUN.txt --------------------------------------------------------------------------------


def test_the_dry_run_names_the_vouchers_held_back_for_the_api(tmp_path, ais):
    """B-1197: the batch ZIP's DRY_RUN.txt listed the vouchers held back for the API (JD Edwards has them, or may)
    with no vendor, invoice number or date."""
    store, batch = lost_answer(tmp_path, ais, ON, QC)
    assert statuses(store, batch)[1] == jde_post.UNKNOWN
    with zipfile.ZipFile(io.BytesIO(exports.jde_file(store, batch)[0])) as zf:
        dry = zf.read("DRY_RUN.txt").decode("utf-8")
    final = store.get_invoice(1)["final_output"]
    line = next(text for text in dry.splitlines() if "#1" in text)
    assert final["vendor_name"] in line and final["invoice_number"] in line and final["invoice_date"] in line, line
