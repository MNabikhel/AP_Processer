"""The fixes from an end-to-end walk of AP Coder on a laptop (B-1180 on): the demo with JD Edwards on, a rejected
invoice reopened, a duplicate pair after a reject, the vendors that make work, the email import counts, who to ask about
JD Edwards, the CSV exports, the approval limit, the review screen, the new vendor form and the setup report."""

from __future__ import annotations

import re

from ap_coder.core.store import REVIEW
from ap_coder.erp import jde

from .test_web_review_signoff import _jde_on, browser, client, demo_db, me, store

__all__ = ["browser", "client", "demo_db", "me", "store"]  # the fixtures, shared


def _plain(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


# --- L1. The demo with JD Edwards on -------------------------------------------------------------------------------


def test_the_demo_vendors_have_jd_edwards_address_numbers(client, store):
    """B-1180: ticking "Show JD Edwards settings" blocked every demo invoice: "supplier number V10104 is not an address
    number". The demo vendor master's numbers are JD Edwards address numbers; About says the checks come with it."""
    _jde_on(store)
    settings = jde.load(store)
    for inv in store.list_invoices(REVIEW):
        output = store.get_invoice(inv["id"])["ai_output"]
        codes = [f[1] for f in jde.voucher_findings(output, settings, jde.resolve_an8(output, store.vendor_ids(),
                                                                                       settings))]  # fmt: skip
        assert "JDE_BAD_AN8" not in codes, inv["vendor_name"]
    about = _plain(client.get("/settings/about").text)
    assert "adds the JD Edwards checks to every invoice" in about
