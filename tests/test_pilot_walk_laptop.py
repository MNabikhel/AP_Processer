"""The fixes from a pilot tester's walk of laptop mode (B-1297 on): a new vendor with JD Edwards on, the learning
figures without the model, a correction after a reopen, the Welcome checklist's JD Edwards line, the JD Edwards
"why not" list, ``watch --once``, emails uploaded, the second approver on Exports, the people on Activity, and
pilot-score's plurals."""

from __future__ import annotations

import json
import re

from ap_coder.core.memory import vendor_key
from ap_coder.core.store import REVIEW
from ap_coder.erp import jde

from .test_web_invoice import PAGE, check
from .test_web_review_signoff import _jde_on, client, demo_db, me, store

__all__ = ["client", "demo_db", "me", "store"]  # the fixtures, shared


def _plain(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


def _cascade(store) -> dict:
    return next(i for i in store.list_invoices(REVIEW) if "Cascade" in (i["vendor_name"] or ""))


# --- 1. A new vendor with JD Edwards on ------------------------------------------------------------------------------


def test_a_new_vendor_with_jd_edwards_on_can_be_given_its_address_number_where_it_is_added(client, store):
    """B-1297: with JD Edwards on, an invoice from a vendor added with "Add new vendor…" could not be approved: "ask the
    pilot tester to add its number", on a laptop where the reader is the pilot tester, without saying where. The
    pop-up said "Its invoices need a person", as if a person could approve them. The pop-up now takes the address
    number, and the check says where it is set."""
    _jde_on(store)
    cascade = _cascade(store)
    page = client.get(f"/review/{cascade['id']}").text
    pop = re.search(r'data-vendor-add>(.*?)</details>', page, re.S).group(1)
    assert 'data-new="an8"' in pop and "Its invoices need a person" not in pop
    assert "Supplier address numbers" in _plain(pop)

    bad = client.post(f"/review/{cascade['id']}/vendor", headers=PAGE,
                      data={"name": cascade["vendor_name"], "an8": "V10104"})  # fmt: skip
    assert bad.status_code == 400 and "digits only" in bad.json()["error"]
    assert not store.get_vendor(vendor_key(cascade["vendor_name"]))  # nothing added when refused

    added = client.post(f"/review/{cascade['id']}/vendor", headers=PAGE,
                        data={"name": cascade["vendor_name"], "an8": "40123"}).json()  # fmt: skip
    assert added["ok"] and "40123" in added["toast"]["text"]
    assert jde.load(store).an8_overrides[vendor_key(cascade["vendor_name"])] == "40123"
    sections = json.dumps(check(client, cascade["id"])["sections"])
    assert "JDE_NO_AN8" not in sections and "VENDOR_ADDED_HERE" in sections


def test_a_vendor_with_no_address_number_is_told_where_to_add_it(client, store):
    """B-1297: the JD Edwards check of a vendor with no AN8 names the Settings section that sets it."""
    _jde_on(store)
    cascade = _cascade(store)
    client.post(f"/review/{cascade['id']}/vendor", headers=PAGE, data={"name": cascade["vendor_name"]})
    raw = json.dumps(check(client, cascade["id"])["sections"], ensure_ascii=False)
    sections = _plain(raw)
    assert "JDE_NO_AN8" in raw
    assert "Settings → JD Edwards E1 → Supplier address numbers" in sections and "ask the pilot tester" not in sections
