"""The lists the review screen picks from, fetched as JSON, and adding a vendor from it.

* ``GET /review/vendors.json``: the vendor list (``vendor_list``, built once a change of the vendors and kept, D-160;
  ``vendor_options``, ``vendor_seed``: what the page itself holds of it).
* ``GET /review/accounts.json``: with a chart of accounts, the accounts a line may be coded to (``account_list``,
  ``account_seed``).
* ``POST /review/<id>/vendor``: Add new vendor, the printed vendor not in the list.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import re
from copy import copy
from typing import Any, NamedTuple

from fastapi import Form, Request
from fastapi.responses import JSONResponse, Response

from ap_coder.coding.tax import OUTSIDE_CANADA, PROVINCE_NAMES
from ap_coder.coding.vendors import ON_HOLD
from ap_coder.core import chart
from ap_coder.core.memory import vendor_key
from ap_coder.core.reference_data import ReferenceData
from ap_coder.core.store import Store
from ap_coder.erp import jde_settings
from ap_coder.web import account_lines, identity
from ap_coder.web.access import is_admin, may_lift_hold
from ap_coder.web.core import get_store, message, reference_or_none, refuse, reviewer
from ap_coder.web.pages.invoice_router import _in_review, router

# --- The vendor list ----------------------------------------------------------------------------------------------


def vendor_options(store: Store) -> dict[str, Any]:
    """The vendor list the review screen picks from: each vendor's name, its key (memory.vendor_key, which the name
    typed is compared with), ERP ID and tax numbers (the screen says whether the name typed is in it, and which
    vendor a printed GST/HST number belongs to)."""
    choices = store.vendor_choices()
    return {
        "enforced": store.has_vendor_master(),  # with a vendor master, an invoice's vendor must be in the list
        "list": [{"name": v["display_name"], "key": vendor_key(v["display_name"]), "id": v.get("erp_id") or "",
                  "gst": v.get("expected_gst") or "", "qst": v.get("qst_number") or "",
                  "pst": v.get("pst_number") or "", "added": v.get("in_master") == 2}
                 for v in choices if v.get("display_name")],
    }  # fmt: skip


class VendorList(NamedTuple):
    """The vendor list as the review screen fetches it (``GET /review/vendors.json``), kept by ``vendor_list``."""

    body: bytes  # the JSON of ``vendor_options``
    gzipped: bytes
    version: str  # the JSON's hash: the address's ?v=
    count: int
    enforced: bool
    by_key: dict[str, dict[str, Any]]  # the first vendor of each vendor key, and of each GST/HST business number,
    by_number: dict[str, dict[str, Any]]  # as invoice.js finds them in the list


def vendor_list(store: Store) -> VendorList:
    """The vendor list (``vendor_options``) as JSON, gzipped, with its version, its count and an index of it: worked
    out once per change of it in each process (``summary_memo``: ``changes/vendors``; without a vendor master the
    vendors approved before are in it too, the ``approved_vendors`` topic). 20,000 vendors: 3 MB of JSON, 0.4 MB
    gzipped, about 0.8 s to build and 21 MB kept. Shared: read it, don't change it."""
    master = store.has_vendor_master()
    return store.summary_memo("vendor_list", lambda: _vendor_list(store), "" if master else "approved_vendors",
                              changes="vendors", asked=master)  # fmt: skip


def _vendor_list(store: Store) -> VendorList:
    options = vendor_options(store)
    body = json.dumps(options, ensure_ascii=False, separators=(",", ":")).encode()
    by_key: dict[str, dict[str, Any]] = {}
    by_number: dict[str, dict[str, Any]] = {}
    for v in options["list"]:
        by_key.setdefault(v["key"], v)
        if number := business_number(v["gst"]):
            by_number.setdefault(number, v)
    return VendorList(body, gzip.compress(body, mtime=0), hashlib.sha256(body).hexdigest()[:16], len(options["list"]),
                      options["enforced"], by_key, by_number)  # fmt: skip


def business_number(value: Any) -> str:
    """A GST/HST number's business number as invoice.js ``businessNumber`` compares them: its first 9 digits, "" with
    fewer."""
    digits = re.sub(r"[^0-9]", "", str(value or ""))
    return digits[:9] if len(digits) >= 9 else ""


def vendor_seed(store: Store, header: dict[str, Any]) -> dict[str, Any]:
    """What the page holds of the vendor list (``#vendor-seed``; invoice.js fetches the rest): its address, whether a
    vendor master enforces it, its count, and the vendors the screen names as it opens (the invoice's vendor, the one
    whose GST/HST number it shows) and what it was asked about (so that none named is known too: "Not in the vendor
    list" is right at once, and a read-only screen fetches nothing)."""
    kept = vendor_list(store)
    key = vendor_key(header.get("vendor_name") or "")
    number = business_number(header.get("gst_hst_registration_number"))
    named = [kept.by_key.get(key) if key else None, kept.by_number.get(number) if number else None]
    return {"url": f"/review/vendors.json?v={kept.version}", "enforced": kept.enforced, "count": kept.count,
            "list": [v for i, v in enumerate(named) if v and v not in named[:i]],
            "asked": {"vendor": key, "number": number}}  # fmt: skip


@router.get("/review/vendors.json")
def vendors_json(request: Request, v: str = "") -> Response:
    """The vendor list the review screen picks from (``vendor_list``), fetched as an editable screen opens. On a laptop
    the browser keeps it for good while ``v`` is its version (the page says the version it was drawn with), and asks
    again otherwise (its ETag: 304 while it did not change). On the company server every answer behind the login is
    ``no-store`` (B-1070, ``identity._no_store``): nothing of the company's stays in a shared computer's browser."""
    kept = vendor_list(get_store(request))
    tag = f'"{kept.version}"'
    headers = {
        "ETag": f"W/{tag}",
        "Vary": "Accept-Encoding",
        "Cache-Control": "private, max-age=31536000, immutable" if v == kept.version else "private, no-cache",
    }
    asked = request.headers.get("if-none-match", "")
    if any(t.strip().removeprefix("W/") in (tag, "*") for t in asked.split(",")):
        return Response(status_code=304, headers=headers)
    if "gzip" in request.headers.get("accept-encoding", "").lower():
        return Response(kept.gzipped, media_type="application/json", headers={**headers, "Content-Encoding": "gzip"})
    return Response(kept.body, media_type="application/json", headers=headers)


# --- The accounts -------------------------------------------------------------------------------------------------


class AccountList(NamedTuple):
    """The accounts a line may be coded to, as the review screen fetches them (``GET /review/accounts.json``)."""

    body: bytes  # the JSON: {"list": [[account string, the account's name, its business unit's name], ...]}
    gzipped: bytes
    version: str  # the JSON's hash: the address's ?v=
    count: int


def account_list(store: Store, reference: ReferenceData | None) -> AccountList | None:
    """The chart's accounts offered for a line (``AccountPairs.offered``: what may be posted to and nothing holds
    back), each with its name and its business unit's name, as JSON, gzipped, with its version: made once for a
    chart and its settings (``summary_memo``, for the reference data's stamp). None without a chart. 9,900 accounts:
    about 0.6 MB of JSON, 0.15 MB gzipped. Shared: read it, don't change it."""
    if not account_lines.in_use(reference):
        return None
    return store.summary_memo("account_list", lambda: _account_list(reference), "", asked=reference.stamp)


def _account_list(reference: ReferenceData) -> AccountList:
    pairs = reference.pairs
    rows = [[chart.join_account(unit, code), pairs.description(unit, code), pairs.name_of(unit)]
            for unit in pairs.business_units() for code in pairs.accounts_of(unit)]  # fmt: skip
    body = json.dumps({"list": rows}, ensure_ascii=False, separators=(",", ":")).encode()
    return AccountList(body, gzip.compress(body, mtime=0), hashlib.sha256(body).hexdigest()[:16], len(rows))


def account_seed(store: Store, reference: ReferenceData, vendor_name: str) -> dict[str, Any] | None:
    """What the page holds of the account list (``#account-seed``; the list itself is fetched, as the vendor list
    is): its address and count, the business unit a line goes to when nothing says otherwise, and the accounts this
    supplier's lines went to last year, the most used first (the supplier's prior, which the suggestions keep: no
    query of its own). None without a chart."""
    kept = account_list(store, reference)
    if kept is None:
        return None
    return {"url": f"/review/accounts.json?v={kept.version}", "count": kept.count,
            "home": reference.pairs.settings.home_business_unit,
            "supplier": account_lines.supplier_accounts(store, reference, vendor_name)}  # fmt: skip


@router.get("/review/accounts.json")
def accounts_json(request: Request, v: str = "") -> Response:
    """The accounts the review screen's Account boxes pick from (``account_list``), fetched as the screen opens,
    not printed in every page. Kept by the browser as the vendor list is (``vendors_json``, D-160): for good on a
    laptop while ``v`` is its version, ``no-store`` behind the company server's login. An empty list without a
    chart (no page asks for it then)."""
    kept = account_list(get_store(request), reference_or_none(get_store(request)))
    if kept is None:
        return JSONResponse({"list": []}, headers={"Cache-Control": "no-store"})
    tag = f'"{kept.version}"'
    headers = {
        "ETag": f"W/{tag}",
        "Vary": "Accept-Encoding",
        "Cache-Control": "private, max-age=31536000, immutable" if v == kept.version else "private, no-cache",
    }
    asked = request.headers.get("if-none-match", "")
    if any(t.strip().removeprefix("W/") in (tag, "*") for t in asked.split(",")):
        return Response(status_code=304, headers=headers)
    if "gzip" in request.headers.get("accept-encoding", "").lower():
        return Response(kept.gzipped, media_type="application/json", headers={**headers, "Content-Encoding": "gzip"})
    return Response(kept.body, media_type="application/json", headers=headers)


# --- Add new vendor -----------------------------------------------------------------------------------------------

MAX_VENDOR_NAME = 120  # a vendor name typed in Add new vendor (B-1531)


@router.post("/review/{invoice_id:int}/vendor")
def add_vendor(
    request: Request, invoice_id: int, name: str = Form(""), gst: str = Form(""), qst: str = Form(""),
    pst: str = Form(""), province: str = Form(""), an8: str = Form(""),
) -> JSONResponse:  # fmt: skip
    """Add new vendor: a vendor not in the vendor list, added from the review screen. Its invoices need a person's
    "Approve anyway" (VENDOR_ADDED_HERE) until it is set up in JD Edwards and the vendor list is imported again. With
    JD Edwards on, its address number (AN8) can be given here (an Admin's, as in Settings → JD Edwards E1, D-112)."""
    store = get_store(request)
    _in_review(store, invoice_id)
    name = identity.clean_name(name, MAX_VENDOR_NAME)  # no control characters, a vendor name's length (B-1531)
    if province and province not in PROVINCE_NAMES and province != OUTSIDE_CANADA:
        return refuse("Choose a province from the list.")
    an8 = an8.strip()
    if an8 and not is_admin(request):
        return refuse("Only an Admin sets a vendor's JD Edwards address number (Settings → JD Edwards E1).", 403)
    if an8 and (not an8.isdigit() or len(an8) > 8):
        return refuse(f"“{an8}” is not a JD Edwards address number (AN8): digits only, up to 8.")
    before = store.get_vendor(vendor_key(name)) or {}
    if (before.get("status") == ON_HOLD or before.get("erp_hold")) and not may_lift_hold(request):
        held = before.get("display_name") or name  # its numbers replaced, the hold would not catch them (B-1554)
        return refuse(f"{held} is on hold: only an Approver can change its tax numbers. Pick it from the list.", 403)
    try:
        store.add_vendor_here(name, gst=gst, qst=qst, pst=pst, province=province, actor=reviewer(),
                              invoice_id=invoice_id)  # fmt: skip
    except ValueError as exc:
        return refuse(str(exc))
    said = f"Added {name} to the vendor list"
    if an8:
        settings = copy(jde_settings.load(store))
        settings.an8_overrides = {**settings.an8_overrides, vendor_key(name): an8}
        jde_settings.save(store, settings, actor=reviewer())
        said += f", with JD Edwards address number {an8}"
    kept = store.get_vendor(vendor_key(name)) or {}  # a number left blank keeps the one AP gave it (B-2549)
    return message(f"{said}. Set it up in JD Edwards before it is paid: until then a person approves each of its "
                   "invoices with “Approve anyway”.", "ok", icon="person_add", vendor=name, key=vendor_key(name),
                   gst=kept.get("expected_gst") or "", qst=kept.get("qst_number") or "",
                   pst=kept.get("pst_number") or "")  # fmt: skip
