"""The supplier list's tax IDs on the review screen (D-219): a supplier picked fills the invoice's GST/HST number, a
number read or typed selects or offers its one supplier, and a number that differs from the chosen supplier's is
approved only with a reason, then confirmed by someone else where people sign in, or said on the keying sheet where
one person works alone."""

from __future__ import annotations

import json
import re

import pytest

from ap_coder.core.store import APPROVED, PENDING, TAX_ID_DIFFERS, TAX_ID_NOTE
from ap_coder.web.pages import exports
from tests.stores import sample_setup_store  # noqa: F401  (the tests' store)
from tests.web.test_server_web import account, log_in, server_client
from tests.web.test_web_invoice import check, form_of
from tests.webclient import PAGE, add_clean, client_for, gt

NORTHWIND, NUMBER = gt()["vendor_name"], gt()["gst_hst_registration_number"]  # "123456782 RT0001"
OTHER, OTHER_NUMBER = "Quarry Road Signs Inc.", "704129930RT0001"


@pytest.fixture
def listed(store):
    """The supplier list with tax IDs: the sample's supplier under its business number alone, and another."""
    store.import_vendor_master([
        {"vendor_name": NORTHWIND, "erp_id": "410001", "gst": "123456782"},
        {"vendor_name": OTHER, "erp_id": "410090", "gst": OTHER_NUMBER},
        {"vendor_name": "Tamarack Food Bank Society", "erp_id": "410120", "gst": ""},
    ], by_erp_id=True)  # fmt: skip
    return store


def _anyway(client, invoice_id: int, data: dict) -> dict:
    """The form with "Approve anyway" ticked for the errors showing, and a reason."""
    bar = check(client, invoice_id, data)["sections"]["bar"]
    key = re.search(r'name="override" value="([0-9a-f]+)"', bar).group(1)
    return {**data, "override": key, "override_reason": "confirmed with the supplier by phone", "why_done": "1"}


def test_a_number_that_is_one_suppliers_selects_it_when_no_supplier_is_chosen(listed):
    client = client_for(listed)
    invoice_id = add_clean(listed, vendor_name="")  # the number was read, the name was not
    page = client.get(f"/review/{invoice_id}").text
    assert f'name="vendor_name" value="{NORTHWIND}"' in page  # as the screen opens
    answer = check(client, invoice_id, form_of(listed, invoice_id))
    assert answer["header"]["values"]["vendor_name"] == NORTHWIND
    typed = form_of(listed, invoice_id, gst_hst_registration_number="70412 9930 RT 0001")  # typed: the other's
    assert check(client, invoice_id, typed)["header"]["values"]["vendor_name"] == OTHER
    for number in ("999999998RT0001", "CHARITABLE", ""):  # no supplier's, a word, none: nothing is selected
        answer = check(client, invoice_id, form_of(listed, invoice_id, gst_hst_registration_number=number))
        assert "vendor_name" not in answer["header"]["values"], number


def test_a_number_two_suppliers_share_selects_neither(listed):
    listed.import_vendor_master([{"vendor_name": "Northwind Leasing Corp.", "erp_id": "410002", "gst": NUMBER}],
                                by_erp_id=True)  # fmt: skip
    invoice_id = add_clean(listed, vendor_name="")
    answer = check(client_for(listed), invoice_id, form_of(listed, invoice_id))
    assert "vendor_name" not in answer["header"]["values"] and "vendor_name" not in answer["header"]["help"]


def test_a_number_that_is_another_suppliers_offers_that_supplier_with_one_click(listed):
    client = client_for(listed)
    invoice_id = add_clean(listed)
    assert "vendor_name" not in check(client, invoice_id, form_of(listed, invoice_id))["header"]["help"]  # its own
    answer = check(client, invoice_id, form_of(listed, invoice_id, vendor_name=OTHER))
    offer = answer["header"]["help"]["vendor_name"]
    assert offer["actions"] == [{"label": f"Use {NORTHWIND}", "value": NORTHWIND}]
    assert NUMBER in offer["text"] and "vendor_name" not in answer["header"]["values"]  # offered, never put in
    assert "gst_hst_registration_number" in answer["header"]["flags"]  # and the difference is flagged


def test_picking_a_supplier_fills_an_empty_number_and_never_one_read_or_typed(listed):
    client = client_for(listed)
    blank = add_clean(listed, vendor_name=OTHER, gst_hst_registration_number="")
    answer = check(client, blank, form_of(listed, blank))
    assert answer["header"]["values"]["gst_hst_registration_number"] == OTHER_NUMBER
    assert answer["header"]["from_vendor"]["fields"] == {"gst_hst_registration_number": OTHER_NUMBER}
    # still what was filled: it follows the supplier picked next (and says nothing of who sent the invoice)
    filled = {**form_of(listed, blank, vendor_name=NORTHWIND, gst_hst_registration_number=OTHER_NUMBER),
              "from_vendor": json.dumps(answer["header"]["from_vendor"])}  # fmt: skip
    answer = check(client, blank, filled)
    assert answer["header"]["values"]["gst_hst_registration_number"] == "123456782"
    assert "vendor_name" not in answer["header"]["help"]
    # a supplier whose tax ID is a word has no number to fill
    word = check(client, blank, form_of(listed, blank, vendor_name="Tamarack Food Bank Society"))
    assert "gst_hst_registration_number" not in word["header"]["values"]
    read = add_clean(listed, vendor_name=OTHER)  # the invoice prints Northwind's number
    answer = check(client, read, form_of(listed, read))
    assert "gst_hst_registration_number" not in answer["header"]["values"]
    assert "differs from the vendor list" in answer["sections"]["checks"]


def test_alone_on_a_laptop_the_mismatch_is_approved_with_a_reason_and_said_on_the_keying_sheet(listed):
    client = client_for(listed)
    invoice_id = add_clean(listed, vendor_name=OTHER)
    data = form_of(listed, invoice_id)
    refused = client.post(f"/review/{invoice_id}/approve", data={**data, "why_done": "1"}, headers=PAGE)
    assert refused.status_code == 400 and "Approve anyway" in refused.json()["error"]
    anyway = _anyway(client, invoice_id, data)
    assert "a second approver is needed" not in check(client, invoice_id, anyway)["sections"]["bar"]
    assert client.post(f"/review/{invoice_id}/approve", data=anyway, headers=PAGE).json()["ok"]
    inv = listed.get_invoice(invoice_id)
    assert inv["status"] == APPROVED
    detail = listed.events(invoice_id, ["approved"])[0]["detail"]
    assert detail["tax_id_differs"] == OTHER_NUMBER and "needs_second" not in detail
    over = [i for i in detail["open_issues"] if i["code"] == "VENDOR_TAX_NUMBER_CHANGED"]
    assert over[0]["override_reason"] == "confirmed with the supplier by phone"
    activity = client.get("/activity").text
    assert "Tax ID differs from the supplier list" in activity and "confirm before paying" in activity
    assert exports.tax_id_warnings(listed, [inv]) == {invoice_id: [f"{TAX_ID_NOTE}."]}
    same = add_clean(listed)  # the supplier's own number: nothing to say
    listed.approve_invoice(same, listed.get_invoice(same)["ai_output"], "AP")
    assert exports.tax_id_warnings(listed, [listed.get_invoice(same)]) == {}


def test_where_people_sign_in_the_mismatch_waits_for_someone_else_whatever_the_approval_limit(listed):
    assert listed.approval_limit() == 0  # no second approval is asked for by amount
    invoice_id = add_clean(listed, vendor_name=OTHER)
    account(listed, "ann", "clerk", "Ann Lee")
    account(listed, "bob", "clerk", "Bob Wong")
    account(listed, "cy", "approver", "Cy Tran")
    ann, bob, cy = server_client(listed), server_client(listed), server_client(listed)
    for client, name in ((ann, "ann"), (bob, "bob"), (cy, "cy")):
        log_in(client, name)
    anyway = _anyway(ann, invoice_id, form_of(listed, invoice_id))
    bar = check(ann, invoice_id, anyway)["sections"]["bar"]
    assert "The tax ID differs from the supplier list" in bar and "a second approver is needed" in bar
    assert ann.post(f"/review/{invoice_id}/approve", data=anyway, headers=PAGE).json()["ok"]
    assert listed.get_invoice(invoice_id)["status"] == PENDING
    first = listed.events(invoice_id, ["approved"])[0]["detail"]
    assert first["second_reason"] == TAX_ID_DIFFERS and first["needs_second"] is True
    assert invoice_id not in [i["id"] for i in listed.unexported_approved()]  # not ready for a keying sheet or a file
    shown = {"approval": listed.latest_approval_id(invoice_id)}
    assert bob.post(f"/review/{invoice_id}/final-approve", data=shown, headers=PAGE).status_code == 403  # a Clerk
    assert listed.get_invoice(invoice_id)["status"] == PENDING
    assert cy.post(f"/review/{invoice_id}/final-approve", data=shown, headers=PAGE).status_code == 200
    inv = listed.get_invoice(invoice_id)
    assert inv["status"] == APPROVED and inv["second_reviewer"] == "Cy Tran"
    assert [i["id"] for i in listed.unexported_approved()] == [invoice_id]
    assert exports.tax_id_warnings(listed, [inv]) == {}  # confirmed by a second person
    final = listed.events(invoice_id, ["final_approved"])[0]["detail"]
    assert final["second_reason"] == TAX_ID_DIFFERS
