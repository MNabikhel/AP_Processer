"""The invoice review screen in the web dashboard (/review/<id>): the page and its picture, the live checks, Approve &
teach (what it records and learns), park, reject, reopen, the second approval, the grid actions, notes, teaching a
field, OvisOCR2's queue, and the page's protections."""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import replace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ap_coder.config import Settings
from ap_coder.demo import load_demo
from ap_coder.reference_data import UNASSIGNED
from ap_coder.rules import Rule
from ap_coder.store import APPROVED, PARKED, PENDING, REJECTED, REVIEW, Store
from ap_coder.web import invoice_view
from ap_coder.web.app import create_app
from ap_coder.web.invoice_view import Draft

PAGE = {"X-AP-Coder": "1"}


@pytest.fixture(scope="module")
def demo_db(tmp_path_factory):
    """The demo invoices, loaded once (copied for each test: loading reads ten PDFs)."""
    path = tmp_path_factory.mktemp("demo") / "private" / "ap_coder.db"
    load_demo(Store(path), Settings())
    return path


@pytest.fixture
def store(demo_db, tmp_path):
    import shutil

    target = tmp_path / "private" / "ap_coder.db"
    target.parent.mkdir(parents=True)
    shutil.copy(demo_db, target)
    return Store(target)


@pytest.fixture
def client(store):
    s = Settings()
    app = create_app(replace(s, llm=replace(s.llm, provider="off")), store, start_reader=False)
    return TestClient(app, base_url="http://localhost")


@pytest.fixture(autouse=True)
def me(monkeypatch):
    """A fixed reviewer and login (not this computer's)."""
    who = {"name": "Jordan Lee", "login": "jlee"}
    for module in ("ap_coder.web.core", "ap_coder.web.pages.invoice", "ap_coder.web.pages.review"):
        monkeypatch.setattr(f"{module}.reviewer", lambda: who["name"], raising=False)
        monkeypatch.setattr(f"{module}.login", lambda: who["login"], raising=False)
    return who


def form_of(store: Store, invoice_id: int, lines: Any = None, **header: str) -> dict[str, Any]:
    """The review form as the page posts it: the stored coding, with ``header`` fields changed and ``lines`` (a
    function of the rows) applied to the line grid."""
    inv = store.get_invoice(invoice_id)
    draft = Draft.stored(inv["final_output"] or inv["ai_output"])
    rows = [dict(r) for r in draft.lines]
    if lines:
        rows = lines(rows)
    data: dict[str, Any] = {**draft.header, **header, "grids": "1"}
    data["line_key"] = [f"r{i}" for i in range(len(rows))]
    for column, name in invoice_view.LINE_INPUTS.items():
        data[name] = ["" if r.get(column) is None else str(r.get(column)) for r in rows]
    for i, r in enumerate(rows):
        data[f"line_tax_r{i}"] = list(r.get("taxes_applied") or [])
    for column, name in invoice_view.TAX_INPUTS.items():
        data[name] = ["" if t.get(column) is None else str(t.get(column)) for t in draft.taxes]
    return data


def check(client: TestClient, invoice_id: int, data: dict[str, Any] | None = None) -> dict[str, Any]:
    response = client.post(f"/review/{invoice_id}/check", data=data or {}, headers=PAGE)
    assert response.status_code == 200, response.text
    return response.json()


def clean_invoice(client: TestClient, store: Store) -> int:
    """An invoice of the queue that may be approved as it is."""
    for inv in store.list_invoices(REVIEW):
        if check(client, inv["id"])["allow"]:
            return inv["id"]
    raise AssertionError("no clean demo invoice")


def flash_text(client: TestClient, answer: dict[str, Any]) -> str:
    return client.get(answer["redirect"]).text


# --- The page -----------------------------------------------------------------------------------------------------


def test_the_review_screen_shows_the_document_the_form_the_grids_and_the_actions(client, store):
    pending = store.list_invoices(REVIEW)
    inv = store.get_invoice(pending[0]["id"])
    text = client.get(f"/review/{inv['id']}").text
    assert f'Reviewing <b class="n">1</b> of {len(pending)}' in text
    assert 'id="viewer-data"' in text and f"/review/{inv['id']}/page/1.png?v=" in text
    data = re.search(r'<script type="application/json" id="viewer-data">(.*?)</script>', text, re.S).group(1)
    assert '"fields"' in data and '"boxes"' in data
    for name in ("vendor_name", "invoice_number", "grand_total", "supplier_province"):
        assert f'name="{name}"' in text
    assert text.count('name="line_key" value="r') == len(inv["ai_output"]["line_items"])
    for label in (
        "Approve &amp; teach",
        "Park",
        "Reject",
        "Split a line",
        "Teach a field",
        "GL posting preview",
        "Sales tax",
        "Rate and amount check",
        "Notes",
        "Why these codes",
    ):
        assert label in text, label
    assert 'data-nav="next"' in text and 'data-nav="prev"' in text


def test_every_demo_invoice_opens_whatever_its_state(client, store):
    for inv in store.list_invoices():
        assert client.get(f"/review/{inv['id']}").status_code == 200
    assert client.get("/review/99999").status_code == 404


def test_a_page_picture_is_a_png_and_the_words_can_be_taught_from(client, store):
    inv = store.list_invoices(REVIEW)[0]
    response = client.get(f"/review/{inv['id']}/page/1.png")
    assert response.status_code == 200 and response.headers["content-type"] == "image/png"
    assert response.content.startswith(b"\x89PNG\r\n\x1a\n")
    assert client.get(f"/review/{inv['id']}/page/1.png").content == response.content  # cached, the same picture
    assert client.get(f"/review/{inv['id']}/page/40.png").status_code == 404
    words = client.get(f"/review/{inv['id']}/words").json()
    assert words["ok"] and words["words"][0] and {"t", "b"} <= set(words["words"][0][0])


def test_text_from_the_invoice_is_escaped(client, store):
    inv = store.list_invoices(REVIEW)[0]
    evil = "<script>alert(1)</script>"
    answer = check(client, inv["id"], form_of(store, inv["id"], vendor_name=evil))
    assert evil not in answer["sections"]["summary"] and "&lt;script&gt;" in answer["sections"]["summary"]
    text = client.get(f"/review/{inv['id']}").text  # the edit is kept as a draft
    assert evil not in text and "&lt;script&gt;alert(1)&lt;/script&gt;" in text


# --- Checking the edits -------------------------------------------------------------------------------------------


def test_edits_are_checked_kept_and_counted_on_the_approve_bar(client, store):
    inv_id = clean_invoice(client, store)

    def recode(rows):
        rows[0]["predicted_gl_code"] = "6800" if rows[0]["predicted_gl_code"] != "6800" else "6020"
        return rows

    answer = check(client, inv_id, form_of(store, inv_id, recode))
    assert (
        'data-change-count="1"' in answer["sections"]["bar"]
        and "you changed 1 of its codes" in answer["sections"]["bar"]
    )
    assert "6800" in answer["sections"]["posting"] or "6020" in answer["sections"]["posting"]
    # The draft survives leaving the screen; Start over drops it.
    assert 'data-change-count="1"' in client.get(f"/review/{inv_id}").text
    client.post(f"/review/{inv_id}/start-over", headers=PAGE)
    assert 'data-change-count="0"' in client.get(f"/review/{inv_id}").text


def test_a_line_without_a_gl_account_cannot_be_approved(client, store):
    inv_id = clean_invoice(client, store)

    def blank(rows):
        rows[0]["predicted_gl_code"] = UNASSIGNED
        return rows

    data = form_of(store, inv_id, blank)
    answer = check(client, inv_id, data)
    assert not answer["allow"] and "Pick a GL account for line" in answer["sections"]["bar"]
    response = client.post(f"/review/{inv_id}/approve", data=data, headers=PAGE)
    assert response.status_code == 400 and "Pick a GL account" in response.json()["error"]
    assert store.get_invoice(inv_id)["status"] == REVIEW


def test_a_field_that_cannot_be_read_is_shown_instead_of_the_checks(client, store):
    inv_id = store.list_invoices(REVIEW)[0]["id"]
    answer = check(client, inv_id, form_of(store, inv_id, invoice_date="next tuesday", grand_total="lots"))
    assert not answer["valid"] and not answer["allow"]
    assert "Fix this field" in answer["sections"]["checks"] and "summary" not in answer["sections"]


def test_errors_need_approve_anyway(client, store):
    inv_id = clean_invoice(client, store)
    data = form_of(store, inv_id, grand_total="1.00")  # the total no longer adds up: an error
    answer = check(client, inv_id, data)
    assert not answer["allow"] and "Approve anyway, despite" in answer["sections"]["bar"]
    key = re.search(r'name="override" value="([0-9a-f]+)"', answer["sections"]["bar"]).group(1)
    refused = client.post(f"/review/{inv_id}/approve", data=data, headers=PAGE)
    assert refused.status_code == 400 and "Approve anyway" in refused.json()["error"]
    assert check(client, inv_id, {**data, "override": key})["allow"]
    assert client.post(f"/review/{inv_id}/approve", data={**data, "override": key}, headers=PAGE).json()["ok"]
    assert store.get_invoice(inv_id)["status"] in (APPROVED, PENDING)


# --- Approve & teach ----------------------------------------------------------------------------------------------


def test_approve_and_teach_records_feedback_learns_and_opens_the_next_invoice(client, store):
    inv_id = clean_invoice(client, store)
    before = len(store.feedback_rows())
    inv = store.get_invoice(inv_id)
    original = inv["ai_output"]["line_items"][0]["predicted_gl_code"]
    other = "6800" if original != "6800" else "6020"

    def recode(rows):
        rows[0]["predicted_gl_code"] = other
        return rows

    response = client.post(f"/review/{inv_id}/approve", data=form_of(store, inv_id, recode), headers=PAGE)
    answer = response.json()
    assert answer["ok"], answer
    done = store.get_invoice(inv_id)
    assert done["status"] == APPROVED and done["reviewer"] == "Jordan Lee"
    assert done["final_output"]["line_items"][0]["predicted_gl_code"] == other
    rows = [r for r in store.feedback_rows() if r["invoice_id"] == inv_id]
    assert len(store.feedback_rows()) > before and any(r["outcome"] == "corrected" for r in rows)
    assert re.match(r"/review/\d+", answer["redirect"]) and f"/review/{inv_id}?" not in answer["redirect"]
    page = flash_text(client, answer)
    vendor = done["final_output"]["vendor_name"].rstrip(".")
    assert f"Approved {vendor}." in page and ("coded as suggested" in page or "Learned from" in page)
    assert any(e["action"] == "approved" for e in store.events(inv_id))


def test_teaching_a_field_sets_it_and_the_approval_teaches_the_template(client, store):
    inv_id = clean_invoice(client, store)
    taught = client.post(f"/review/{inv_id}/teach", data={"field": "grand_total", "text": "$1,234.50"}, headers=PAGE)
    assert taught.json()["value"] == "1234.50" and "Approving teaches it" in taught.json()["toast"]["text"]
    bad = client.post(f"/review/{inv_id}/teach", data={"field": "invoice_date", "text": "   "}, headers=PAGE)
    assert bad.status_code == 400
    assert client.post(f"/review/{inv_id}/teach", data={"field": "remit_bank_account", "text": "x"},
                       headers=PAGE).status_code == 400  # fmt: skip
    inv = store.get_invoice(inv_id)
    data = form_of(store, inv_id, taught='{"invoice_number": [[1, 0.1, 0.1, 0.3, 0.12]], "nope": [[1, 0, 0, 1, 1]]}')
    assert invoice_view.parse_taught(data["taught"]) == {"invoice_number": [[1, 0.1, 0.1, 0.3, 0.12]]}
    assert client.post(f"/review/{inv_id}/approve", data=data, headers=PAGE).json()["ok"]
    key = store.supplier_key_for(inv["ai_output"]["vendor_name"], inv["ai_output"].get("gst_hst_registration_number"))
    assert (store.get_supplier_profile(key) or {}).get("template")


def test_second_approval_and_send_back(client, store, me):
    store.set_setting("approval_limit", "1")
    inv_id = clean_invoice(client, store)
    answer = client.post(f"/review/{inv_id}/approve", data=form_of(store, inv_id), headers=PAGE).json()
    assert store.get_invoice(inv_id)["status"] == PENDING
    assert "waits for a second approver" in flash_text(client, answer)
    page = client.get(f"/review/{inv_id}").text
    assert "Waiting for a second approval" in page and "You approved it first" in page  # the same person
    assert client.post(f"/review/{inv_id}/final-approve", headers=PAGE).json()["ok"]
    assert store.get_invoice(inv_id)["status"] == PENDING  # refused: the first approver can't give the second
    me.update(name="Sam Rivera", login="srivera")
    assert "You approved it first" not in client.get(f"/review/{inv_id}").text
    client.post(f"/review/{inv_id}/send-back", data={"reason": "wrong cost center"}, headers=PAGE)
    assert store.get_invoice(inv_id)["status"] == REVIEW
    assert "Sent back by Sam Rivera" in client.get(f"/review/{inv_id}").text
    me.update(name="Jordan Lee", login="jlee")
    client.post(f"/review/{inv_id}/approve", data=form_of(store, inv_id), headers=PAGE)
    me.update(name="Sam Rivera", login="srivera")
    client.post(f"/review/{inv_id}/final-approve", headers=PAGE)
    assert (
        store.get_invoice(inv_id)["status"] == APPROVED and store.get_invoice(inv_id)["second_reviewer"] == "Sam Rivera"
    )


def test_an_approved_invoice_can_be_reopened_for_correction_and_its_pdf_downloaded(client, store):
    inv_id = clean_invoice(client, store)
    client.post(f"/review/{inv_id}/approve", data=form_of(store, inv_id), headers=PAGE)
    page = client.get(f"/review/{inv_id}").text
    assert "Reopen for correction" in page and "GL posting" in page and "approved.pdf" in page
    pdf = client.get(f"/review/{inv_id}/approved.pdf")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    assert client.post(f"/review/{inv_id}/correct", data={"reason": " "}, headers=PAGE).status_code == 400
    answer = client.post(f"/review/{inv_id}/correct", data={"reason": "wrong GL"}, headers=PAGE).json()
    assert answer["redirect"].startswith(f"/review/{inv_id}") and store.get_invoice(inv_id)["status"] == REVIEW
    assert "Reopened by Jordan Lee" in client.get(f"/review/{inv_id}").text


@pytest.mark.parametrize("exported", [True, False])
def test_correcting_a_touchless_invoice_suspends_its_vendor_only_when_it_is_reopened(client, store, exported):
    """Reopen for correction on a touchless invoice suspends its vendor. When the reopen is refused (exported
    meanwhile, from a screen opened before), nothing was reopened: the vendor stays touchless."""
    from ap_coder.capture.workflow import AUTONOMOUS_REVIEWER

    inv_id = clean_invoice(client, store)
    client.post(f"/review/{inv_id}/approve", data=form_of(store, inv_id), headers=PAGE)
    final = store.get_invoice(inv_id)["final_output"]
    key = store.supplier_key_for(final["vendor_name"], final.get("gst_hst_registration_number"))
    store.save_supplier_profile(key, display_name=final["vendor_name"])
    with store._conn() as conn:  # as if touchless processing had approved it, its vendor touchless
        conn.execute("UPDATE invoices SET reviewer = ? WHERE id = ?", (AUTONOMOUS_REVIEWER, inv_id))
        conn.execute("UPDATE supplier_profiles SET state = 'autonomous' WHERE key = ?", (key,))
    if exported:
        store.create_export_batch([inv_id], "csv", actor="Sam")
    client.post(f"/review/{inv_id}/correct", data={"reason": "wrong GL"}, headers=PAGE)
    state = store.get_supplier_profile(key)["state"]
    if exported:
        assert store.get_invoice(inv_id)["status"] == APPROVED and state == "autonomous"
        assert not store.events(actions=["autonomy_suspended"])
    else:
        assert store.get_invoice(inv_id)["status"] == REVIEW and state == "suspended"


# --- Park, reject, delete, notes ----------------------------------------------------------------------------------


def test_park_needs_a_reason_and_comes_back(client, store):
    inv_id = store.list_invoices(REVIEW)[0]["id"]
    assert client.post(f"/review/{inv_id}/park", data={"reason": ""}, headers=PAGE).status_code == 400
    yesterday = (dt.date.today() - dt.timedelta(days=1)).isoformat()
    assert client.post(f"/review/{inv_id}/park", data={"reason": "PO", "follow_up": yesterday},
                       headers=PAGE).status_code == 400  # fmt: skip
    follow = (dt.date.today() + dt.timedelta(days=3)).isoformat()
    answer = client.post(f"/review/{inv_id}/park", data={"reason": "buyer to confirm", "follow_up": follow},
                         headers=PAGE).json()  # fmt: skip
    inv = store.get_invoice(inv_id)
    assert inv["status"] == PARKED and inv["parked_reason"] == "buyer to confirm" and inv["follow_up"] == follow
    assert answer["redirect"] != f"/review/{inv_id}" and "parked" in flash_text(client, answer)
    page = client.get(f"/review/{inv_id}").text
    assert "Waiting for: buyer to confirm" in page and "/resume" in page
    assert "buyer to confirm" in page  # in the notes too
    client.post(f"/review/{inv_id}/resume", headers=PAGE)
    assert store.get_invoice(inv_id)["status"] == REVIEW


def test_reject_and_reopen(client, store):
    inv_id = store.list_invoices(REVIEW)[0]["id"]
    answer = client.post(f"/review/{inv_id}/reject", data={"reason": "not our invoice"}, headers=PAGE).json()
    assert store.get_invoice(inv_id)["status"] == REJECTED and "rejected" in flash_text(client, answer)
    assert "/restore" in client.get(f"/review/{inv_id}").text
    client.post(f"/review/{inv_id}/restore", headers=PAGE)
    assert store.get_invoice(inv_id)["status"] == REVIEW


def test_acting_on_an_invoice_someone_else_changed_says_so(client, store):
    inv_id = clean_invoice(client, store)
    data = form_of(store, inv_id)
    store.reject_invoice(inv_id, "Someone else", "duplicate")
    answer = client.post(f"/review/{inv_id}/approve", data=data, headers=PAGE).json()
    assert "changed meanwhile, in another tab or by someone else. It is rejected." in flash_text(client, answer)
    assert store.get_invoice(inv_id)["status"] == REJECTED


def test_notes_and_delete(client, store):
    pending = store.list_invoices(REVIEW)
    inv_id = pending[0]["id"]
    assert client.post(f"/review/{inv_id}/note", data={"text": " "}, headers=PAGE).status_code == 400
    client.post(f"/review/{inv_id}/note", data={"text": "asked Sam about the PO"}, headers=PAGE)
    assert any(e["action"] == "note" for e in store.events(inv_id))
    assert "asked Sam about the PO" in client.get(f"/review/{inv_id}").text
    back = f"/review/{pending[1]['id']}"
    answer = client.post(f"/review/{inv_id}/delete", data={"back": back}, headers=PAGE).json()
    assert answer["redirect"].startswith(back) and store.get_invoice(inv_id) is None


# --- The line grid --------------------------------------------------------------------------------------------------


def test_split_a_line(client, store):
    inv_id = clean_invoice(client, store)
    rows = store.get_invoice(inv_id)["ai_output"]["line_items"]
    data = {**form_of(store, inv_id), "split_line": str(rows[0]["line_number"]), "part_gl": ["6020", "6800"],
            "part_cc": ["", ""], "part_pct": ["60", "40"]}  # fmt: skip
    answer = client.post(f"/review/{inv_id}/split", data=data, headers=PAGE).json()
    assert answer["ok"] and "split in 2" in answer["toast"]["text"]
    assert answer["lines"].count('name="line_key" value="r') == len(rows) + 1 and "(60%)" in answer["lines"]
    page = client.get(f"/review/{inv_id}").text  # kept as the draft
    assert page.count('name="line_key" value="r') == len(rows) + 1
    wrong = client.post(f"/review/{inv_id}/split", data={**data, "part_pct": ["60", "30"]}, headers=PAGE)
    assert wrong.status_code == 400 and "100" in wrong.json()["error"]


def test_the_coding_rules_can_be_applied_to_the_lines(client, store):
    inv_id = clean_invoice(client, store)
    inv = store.get_invoice(inv_id)
    first = inv["ai_output"]["line_items"][0]
    target = "6800" if first["predicted_gl_code"] != "6800" else "6020"
    store.save_coding_rules([Rule(vendor="", contains=first["description"].split()[0], gl_code=target,
                                  cost_center=first.get("predicted_cost_center") or "")])  # fmt: skip
    answer = check(client, inv_id)
    assert "Apply the coding rules to line" in answer["sections"]["checks"]
    applied = client.post(f"/review/{inv_id}/apply-rules", data=form_of(store, inv_id), headers=PAGE).json()
    assert applied["ok"] and f'<option value="{target}" selected>' in applied["lines"]


def test_a_suggested_gl_account_codes_the_line(client, store):
    inv_id = clean_invoice(client, store)

    def unknown(rows):
        rows[0]["predicted_gl_code"] = UNASSIGNED
        rows[0]["description"] = "Software subscriptions renewal, software licences"  # as the account is described
        return rows

    data = form_of(store, inv_id, unknown)
    answer = check(client, inv_id, data)
    found = re.search(r'data-position="(\d+)" data-gl="([^"]+)"', answer["sections"]["suggest"])
    assert found, "a suggestion for the uncoded line"
    position, gl = found.groups()
    used = client.post(f"/review/{inv_id}/suggestion", data={**data, "position": position, "gl": gl},
                       headers=PAGE).json()  # fmt: skip
    assert used["ok"] and f'<option value="{gl}" selected>' in used["lines"]
    bad = client.post(f"/review/{inv_id}/suggestion", data={**data, "position": position, "gl": "NOPE"}, headers=PAGE)
    assert bad.status_code == 400


# --- OvisOCR2 -------------------------------------------------------------------------------------------------------


def test_an_invoice_not_read_by_ovisocr2_can_be_put_in_its_queue(client, store):
    inv_id = store.list_invoices(REVIEW)[0]["id"]
    page = client.get(f"/review/{inv_id}").text
    assert "OvisOCR2 hasn" in page and "Add it to OvisOCR2" in page
    client.post(f"/review/{inv_id}/read-pages", headers=PAGE)
    assert (store.page_read(inv_id) or {}).get("status") == "waiting"
    progress = client.get(f"/review/{inv_id}/reader").json()
    assert progress["state"] == "waiting" and progress["poll"] and "OvisOCR2" in progress["html"]
    assert 'data-poll="' in client.get(f"/review/{inv_id}").text


def test_the_reading_coming_in_keeps_the_reviewers_edits(client, store):
    inv_id = store.list_invoices(REVIEW)[0]["id"]
    check(client, inv_id, form_of(store, inv_id, vendor_name="Typed by me"))
    inv = store.get_invoice(inv_id)
    meta = {**(inv.get("meta") or {}), "page_reader": {"at": "2026-10-11T10:00:00", "agree": ["vendor_name"]}}
    with store._conn() as conn:
        import json

        conn.execute("UPDATE invoices SET meta = ? WHERE id = ?", (json.dumps(meta), inv_id))
    page = client.get(f"/review/{inv_id}").text
    assert "Typed by me" in page and "Start over from its reading" in page and "1 of 1 fields agree" in page


# --- Navigation and protections -----------------------------------------------------------------------------------


def test_previous_and_next_follow_the_queue_as_shown(client, store):
    pending = store.list_invoices(REVIEW)
    by_amount = sorted(pending, key=lambda r: -(r["grand_total"] or 0))
    first = by_amount[0]["id"]
    page = client.get(f"/review/{first}?sort=amount").text
    assert f'href="/review/{by_amount[1]["id"]}?sort=amount" data-nav="next"' in page
    assert f'href="/review/{by_amount[-1]["id"]}?sort=amount" data-nav="prev"' in page
    assert 'href="/review?sort=amount" data-nav="queue"' in page


def test_the_page_keeps_the_dashboards_protections(client, store):
    inv_id = store.list_invoices(REVIEW)[0]["id"]
    for path in ("check", "approve", "park", "reject", "note", "split", "teach", "read-pages", "correct"):
        assert client.post(f"/review/{inv_id}/{path}").status_code == 403  # no X-AP-Coder header
    cross = client.post(f"/review/{inv_id}/reject", headers={**PAGE, "Origin": "https://evil.example"})
    assert cross.status_code == 403 and store.get_invoice(inv_id)["status"] == REVIEW
    assert client.get(f"/review/{inv_id}", headers={"Host": "evil.example"}).status_code == 400
    assert client.get(f"/review/{inv_id}/page/1.png", headers={"Host": "evil.example"}).status_code == 400


def test_form_values_from_the_words_on_the_page():
    assert invoice_view.form_value("grand_total", "$ 1,234.50") == 1234.5
    assert invoice_view.form_value("invoice_date", "October 1, 2026") == "2026-10-01"
    assert invoice_view.form_value("vendor_name", "  Acme \n Ltd ") == "Acme Ltd"
    assert invoice_view.parse_taught("not json") == {} and invoice_view.parse_taught('{"po_number": "x"}') == {}


def test_bulk_approve_leaves_an_invoice_with_open_edits_for_its_review_screen(client, store):
    """An invoice with edits on its review screen, not approved yet, is not bulk-approved as coded (the edits would be
    lost): it is skipped with the reason, and the other clean invoices are approved."""
    edited = clean_invoice(client, store)
    check(client, edited, form_of(store, edited, vendor_name="Edited Vendor Ltd"))
    others = [i["id"] for i in store.list_invoices(REVIEW) if i["id"] != edited]
    answer = client.post("/review/bulk-approve", data={"ids": [edited, *others]}, headers=PAGE).json()
    assert store.get_invoice(edited)["status"] == REVIEW
    assert "edits open on its review screen" in flash_text(client, answer)


@pytest.mark.parametrize(("name", "said"), [("IMG_2001.jpg", "Made into a PDF from the picture"),
                                            ("scan.pdf", "Shown upright: the scan")])  # fmt: skip
def test_the_review_screen_says_where_the_file_as_it_came_in_is_kept(client, store, name, said):
    import json
    import sqlite3

    inv = store.list_invoices(REVIEW)[0]
    meta = {**(store.get_invoice(inv["id"]).get("meta") or {}),
            "original": {"file": name, "kept": f"/x/originals/{name}", "sha256": "0" * 64}}  # fmt: skip
    with sqlite3.connect(store.path) as conn:
        conn.execute("UPDATE invoices SET meta = ? WHERE id = ?", (json.dumps(meta), inv["id"]))
    text = client.get(f"/review/{inv['id']}").text
    assert said in text and f"<b>{name}</b>" in text and "<code>originals</code>" in text
