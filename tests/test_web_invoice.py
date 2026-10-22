"""The invoice review screen in the web dashboard (/review/<id>): the page and its picture, the live checks, Approve &
teach (what it records and learns), park, reject, reopen, the second approval, the grid actions, notes, teaching a
field, OvisOCR2's queue, and the page's protections."""

from __future__ import annotations

import contextlib
import datetime as dt
import re
from dataclasses import replace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ap_coder.coding.rules import Rule
from ap_coder.core.config import Settings
from ap_coder.core.reference_data import UNASSIGNED
from ap_coder.core.store import APPROVED, PARKED, PENDING, REJECTED, REVIEW, Store, load_sample_cost_centers
from ap_coder.ops.demo import load_demo
from ap_coder.web import invoice_view
from ap_coder.web.app import create_app
from ap_coder.web.invoice_view import Draft

from .conftest import chromium_path

PAGE = {"X-AP-Coder": "1"}


@pytest.fixture(scope="module")
def demo_db(tmp_path_factory):
    """The demo invoices, loaded once (copied for each test: loading reads ten PDFs)."""
    path = tmp_path_factory.mktemp("demo") / "private" / "ap_coder.db"
    load_sample_cost_centers(Store(path))  # the demo has none (D-53): the line grid's cost centers are tested here
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


def test_an_invoice_of_more_than_three_lines_gets_the_tighter_line_grid(client, store):
    """D-59: each line of the grid took about 150 px, so two lines showed at 1366 x 768; from four lines the grid is
    the tighter one (captions on the first line only), about 105 px a line."""
    import copy
    import json

    for inv in store.list_invoices(REVIEW):
        lines = len(store.get_invoice(inv["id"])["ai_output"]["line_items"])
        table = re.search(r'<table class="([^"]*)" id="lines">', client.get(f"/review/{inv['id']}").text).group(1)
        assert ("compact" in table.split()) == (lines > 3), (inv["id"], lines, table)
    inv = store.get_invoice(store.list_invoices(REVIEW)[0]["id"])
    output = copy.deepcopy(inv["ai_output"])
    for count, compact in ((3, False), (4, True)):
        output["line_items"] = [{**copy.deepcopy(output["line_items"][0]), "line_number": i + 1} for i in range(count)]
        with store._conn() as conn:
            conn.execute("UPDATE invoices SET ai_output = ? WHERE id = ?", (json.dumps(output), inv["id"]))
        table = re.search(r'<table class="([^"]*)" id="lines">', client.get(f"/review/{inv['id']}").text).group(1)
        assert ("compact" in table.split()) == compact, count


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
    assert 'data-change-count="1"' in answer["sections"]["bar"] and "1 you changed" in answer["sections"]["bar"]
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


@pytest.mark.parametrize("typed", ["nan", "inf", "-1e309"])
def test_a_header_amount_that_is_not_a_finite_number_is_refused(client, store, typed):
    """A total posted as "nan" was taken as a number: only the total check flagged it, "Approve anyway" cleared
    that, and a NaN total is never over the second-approval limit. It must read as an amount that can't be read."""
    store.set_setting("approval_limit", "1")
    inv_id = clean_invoice(client, store)
    data = form_of(store, inv_id, grand_total=typed)
    answer = check(client, inv_id, data)
    assert not answer["valid"] and not answer["allow"] and "Fix this field" in answer["sections"]["checks"]
    key = re.search(r'name="override" value="([0-9a-f]+)"', answer["sections"].get("bar", ""))
    data = {**data, "override": key.group(1) if key else "", "why_done": "1"}
    client.post(f"/review/{inv_id}/approve", data=data, headers=PAGE)
    assert store.get_invoice(inv_id)["status"] == REVIEW


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
    # LM Studio isn't running here: the banner says OvisOCR2 reads it once it runs (B-444), the button stays
    assert "OvisOCR2 and the AI reviewer check it once LM Studio runs" in page and "Add it to OvisOCR2" in page
    client.post(f"/review/{inv_id}/read-pages", headers=PAGE)
    assert (store.page_read(inv_id) or {}).get("status") == "waiting"
    progress = client.get(f"/review/{inv_id}/reader").json()
    assert progress["state"] == "waiting" and progress["poll"]
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


# --- The vendor, from the vendor list ------------------------------------------------------------------------------


def test_the_vendor_is_picked_from_the_vendor_list_or_added_as_a_new_vendor(client, store):
    import json

    cascade = next(i for i in store.list_invoices(REVIEW) if "Cascade" in (i["vendor_name"] or ""))
    page = client.get(f"/review/{cascade['id']}").text
    # picked from the vendor list (#vendor-data) with the picker, typed freely (D-56)
    assert re.search(r'id="vendor-name"[^>]*data-picker="free"', page) and "Add new vendor" in page
    data = json.loads(re.search(r'<script type="application/json" id="vendor-data">(.*?)</script>', page, re.S)
                      .group(1))  # fmt: skip
    assert data["enforced"] and any(v["id"] == "V10023" for v in data["list"])
    assert not any("Cascade" in v["name"] for v in data["list"])  # the demo vendor not in the vendor master
    answer = check(client, cascade["id"])
    assert not answer["allow"] and "VENDOR_NOT_IN_MASTER" in json.dumps(answer["sections"])

    assert client.post(f"/review/{cascade['id']}/vendor", data={"name": "Cascade"}).status_code == 403  # no header
    bad = client.post(f"/review/{cascade['id']}/vendor", data={"name": "X", "province": "ZZ"}, headers=PAGE)
    assert bad.status_code == 400
    added = client.post(
        f"/review/{cascade['id']}/vendor",
        headers=PAGE,
        data={"name": cascade["vendor_name"], "gst": "", "province": "OUTSIDE_CANADA"},
    ).json()
    assert added["ok"] and added["vendor"] == cascade["vendor_name"]
    again = client.post(f"/review/{cascade['id']}/vendor", data={"name": "Northwind IT Solutions Inc."}, headers=PAGE)
    assert again.status_code == 400 and "already in the vendor list" in again.json()["error"]
    sections = json.dumps(check(client, cascade["id"])["sections"])
    assert "VENDOR_ADDED_HERE" in sections and "VENDOR_NOT_IN_MASTER" not in sections
    assert any(
        v["added"]
        for v in json.loads(
            re.search(r'id="vendor-data">(.*?)</script>', client.get(f"/review/{cascade['id']}").text, re.S).group(1)
        )["list"]
    )


# --- Words picked on the page by dragging a box --------------------------------------------------------------------


@pytest.mark.parametrize(
    "field, text, value",
    [
        ("line_description", "  HP 26X   toner ", "HP 26X toner"),
        ("line_amount", "$1,316.00", 1316.0),
        ("line_quantity", "10", 10),
        ("line_quantity", "2.5", 2.5),
        ("line_unit_price", "329.00", 329.0),
        (
            "line_new",
            'Dell 27" monitor P2725H 4 329.00 1,316.00',
            {"description": 'Dell 27" monitor P2725H', "amount": 1316.0},
        ),
        ("line_new", "Freight 45.00", {"description": "Freight", "amount": 45.0}),
        (
            "line_new",
            "3 Copy paper, letter (G, P) 10 52.00 520.00",
            {"description": "Copy paper, letter (G, P)", "amount": 520.0},
        ),
        ("line_new", "2026 Annual licence 1,200.00", {"description": "2026 Annual licence", "amount": 1200.0}),
        ("line_new", "no amount here", None),
        ("line_amount", "toner", None),
    ],
)
def test_words_picked_for_a_line_cell(field, text, value):
    assert invoice_view.line_value(field, text) == value


def test_dragged_words_fill_a_line_or_make_a_new_one(client, store):
    inv = store.list_invoices(REVIEW)[0]
    answer = client.post(f"/review/{inv['id']}/teach", data={"field": "line_amount", "text": "1,316.00"},
                         headers=PAGE).json()  # fmt: skip
    assert answer["ok"] and answer["value"] == "1316.00" and answer["field"] == "line_amount"
    answer = client.post(f"/review/{inv['id']}/teach", data={"field": "line_new", "text": "Freight 45.00"},
                         headers=PAGE).json()  # fmt: skip
    assert answer["value"] == {"description": "Freight", "amount": 45.0}
    bad = client.post(f"/review/{inv['id']}/teach", data={"field": "line_quantity", "text": "toner"}, headers=PAGE)
    assert bad.status_code == 400 and "quantity" in bad.json()["error"]
    bad = client.post(f"/review/{inv['id']}/teach", data={"field": "line_gl", "text": "6000"}, headers=PAGE)
    assert bad.status_code == 400  # the GL account is chosen, never read off the page


def test_a_new_line_from_a_long_run_of_words_is_read_quickly(client, store):
    """Stripping the numbers before a new line's amount took time growing with the square of the words picked
    (8,000 numbers: over 3 seconds of the server's time)."""
    import time

    inv = store.list_invoices(REVIEW)[0]
    text = "Item" + " 1" * 8000 + " x 12.50"
    started = time.perf_counter()
    answer = client.post(f"/review/{inv['id']}/teach", data={"field": "line_new", "text": text}, headers=PAGE).json()
    assert time.perf_counter() - started < 1.5
    assert answer["value"]["amount"] == 12.5 and answer["value"]["description"].endswith("1 1 x")
    assert invoice_view.line_value("line_new", "Item" + " 1" * 8000 + " 12.50") == {"description": "Item",
                                                                                   "amount": 12.5}  # fmt: skip


def test_the_viewer_knows_each_lines_amount_to_light_its_row(client, store):
    import json

    pacific = next(i for i in store.list_invoices(REVIEW) if "Pacific" in (i["vendor_name"] or ""))
    page = client.get(f"/review/{pacific['id']}").text
    data = json.loads(re.search(r'<script type="application/json" id="viewer-data">(.*?)</script>', page, re.S)
                      .group(1))  # fmt: skip
    amounts = [line["amount"] for line in data["line_items"]]
    assert amounts == [890.0, 1316.0, 520.0] and all(line["boxes"] for line in data["line_items"])
    assert "drag a box around" in page


def test_the_math_line_by_line_and_on_the_approve_bar(client, store):
    pacific = next(i for i in store.list_invoices(REVIEW) if "Pacific" in (i["vendor_name"] or ""))
    sections = check(client, pacific["id"])["sections"]
    assert "Math, line by line" in sections["taxcheck"] and "All adds up" in sections["taxcheck"]
    assert "Math: All adds up" in sections["bar"]
    # PST ticked off the first line: the PST charged is no longer 7% of the lines marked.
    data = form_of(store, pacific["id"])
    data["line_tax_r0"] = ["GST"]
    sections = check(client, pacific["id"], data)["sections"]
    assert "Math: 1 difference: PST" in sections["bar"] and "off by 62.30" in sections["taxcheck"]


@pytest.mark.parametrize(
    ("field", "text", "value"),
    [
        ("invoice_number", "#: PO-77120", "PO-77120"),
        ("po_number", "No. 4471", "4471"),
        ("invoice_number", "NW-2026-0912", "NW-2026-0912"),
        ("vendor_name", "#1 Supplies", "#1 Supplies"),
    ],
)
def test_a_numbers_label_marks_picked_with_it_are_dropped(field, text, value):
    assert invoice_view.form_value(field, text) == value


# --- The AI reviewer -----------------------------------------------------------------------------------------------


def test_the_ai_reviewer_on_the_review_screen(client, store):
    import json

    from ap_coder.ai import verifier

    inv = store.list_invoices(REVIEW)[0]
    page = client.get(f"/review/{inv['id']}").text
    # every demo invoice waits for it; with LM Studio off here, the banner says so (B-444)
    assert "OvisOCR2 and the AI reviewer check it once LM Studio runs" in page
    output = store.get_invoice(inv["id"])["ai_output"]
    disputed = {"grand_total": {"value": output["grand_total"], "on_page": "9,999.00", "reason": "the total box"}}
    store.next_verification(after_page_reader=False)
    store.finish_verification(inv["id"], "done", rounds=2, seconds=95.0, result={
        "status": verifier.DISAGREES, "fields": disputed, "lines": {}, "rounds": 2, "seconds": 95.0,
        "summary": "The total printed is 9,999.00."})  # fmt: skip
    page = client.get(f"/review/{inv['id']}").text
    assert "The AI reviewer reads total differently (2 rounds, 95 s)" in page
    assert "The total printed is 9,999.00." in page and "check it again" in page
    sections = json.dumps(check(client, inv["id"])["sections"])
    assert "VERIFIER_DISAGREES" in sections and "9,999.00" in sections
    # The reviewer corrects the total to what is printed: the reviewer's finding is settled.
    data = form_of(store, inv["id"], grand_total="9999.00")
    assert "VERIFIER_DISAGREES" not in json.dumps(check(client, inv["id"], data)["sections"])

    assert client.post(f"/review/{inv['id']}/review-again").status_code == 403  # no header: not from the page
    answer = client.post(f"/review/{inv['id']}/review-again", headers=PAGE).json()
    assert answer["ok"] and store.verification(inv["id"])["status"] == "waiting"
    verifier.save_settings(store, on=False, rounds=3, minutes=5)
    assert client.post(f"/review/{inv['id']}/review-again", headers=PAGE).status_code == 400
    assert "The AI reviewer checks" not in client.get(f"/review/{inv['id']}").text  # off: nothing said


# --- In a browser (Chromium, when Playwright is installed) ---------------------------------------------------------


def _chromium() -> str | None:
    return chromium_path()


@pytest.fixture
def browser(store):
    """The dashboard served on this computer (a free port) and a Chromium to open it: (browser, base URL)."""
    if _chromium() is None:
        pytest.skip("Playwright with Chromium isn't installed")
    import socket
    import threading
    import time

    import uvicorn
    from playwright.sync_api import sync_playwright

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    s = Settings()
    app = create_app(replace(s, llm=replace(s.llm, provider="off")), store, start_reader=False)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.05)
    try:
        with sync_playwright() as p:
            chromium = p.chromium.launch(executable_path=_chromium(), args=["--no-proxy-server"])
            yield chromium, f"http://127.0.0.1:{port}"
            chromium.close()
    finally:
        server.should_exit = True
        thread.join(5)


def _open_review(browser, store, vendor, **context):
    chromium, base = browser
    inv = next(i for i in store.list_invoices(REVIEW) if vendor in (i["vendor_name"] or ""))
    page = chromium.new_page(**context)
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{base}/review/{inv['id']}")
    page.wait_for_selector(".iv-sheet:not(.loading)")
    return page, errors


def _drag_across_the_top(page):
    """A box dragged with the mouse across the top of the page (the vendor's name, on the Cascade invoice)."""
    page.locator(".iv-sheet").scroll_into_view_if_needed()
    sheet = page.locator(".iv-sheet").bounding_box()
    page.mouse.move(sheet["x"] + sheet["width"] * 0.05, sheet["y"] + sheet["height"] * 0.02)
    page.mouse.down()
    page.mouse.move(sheet["x"] + sheet["width"] * 0.6, sheet["y"] + sheet["height"] * 0.08, steps=8)
    page.mouse.up()
    page.wait_for_selector(".inv-chooser")


def test_in_a_browser_the_popovers_open_inside_a_phone_screen(browser, store):
    """390 px wide: Park, Reject, Split a line and Apply to all lines open where they can be read and their buttons
    reached, not past the left edge of the screen (where nothing scrolls to them)."""
    from playwright.sync_api import TimeoutError as PlaywrightTimeout

    page, errors = _open_review(browser, store, "Cascade", viewport={"width": 390, "height": 844})
    for summary in (".inv-actionbar summary[title^='Set it aside']", ".inv-actionbar summary[title^='Not ours']",
                    "[data-split] > summary", "[data-fill] > summary"):  # fmt: skip
        page.locator(summary).scroll_into_view_if_needed()
        page.click(summary)
        # app.js places it on "toggle", which the browser fires a moment after the click: wait for that, then check
        # where it is (B-125).
        with contextlib.suppress(PlaywrightTimeout):
            page.wait_for_function(
                "() => { const r = document.querySelector('details[open] > .pop-body').getBoundingClientRect();"
                " return r.left >= 0 && r.right <= 390; }",
                timeout=2000,
            )
        box = page.locator("details[open] > .pop-body").bounding_box()
        assert box["x"] >= 0 and box["x"] + box["width"] <= 390, (summary, box)
        page.keyboard.press("Escape")
    assert errors == []


def test_in_a_browser_enter_on_cancel_leaves_the_words_dragged_on_the_page_unused(browser, store):
    """Words dragged across on the page, then Cancel pressed with the keyboard: the field keeps its value."""
    page, errors = _open_review(browser, store, "Cascade", viewport={"width": 1400, "height": 1000})
    taught: list[str] = []
    page.on("request", lambda r: r.url.endswith("/teach") and taught.append(r.url))
    before = page.input_value("[form=inv][name=po_number]")
    _drag_across_the_top(page)
    page.select_option(".inv-chooser select", "po_number")
    page.locator(".inv-chooser button", has_text="Cancel").focus()
    page.keyboard.press("Enter")
    page.wait_for_timeout(500)
    assert page.locator(".inv-chooser").count() == 0
    assert taught == [] and page.input_value("[form=inv][name=po_number]") == before
    # Enter in the list of choices still uses the words.
    _drag_across_the_top(page)
    page.select_option(".inv-chooser select", "po_number")
    page.locator(".inv-chooser select").press("Enter")
    page.wait_for_function("document.querySelector('[form=inv][name=po_number]').value.includes('Cascade')")
    assert len(taught) == 1 and errors == []


def test_in_a_browser_the_approve_anyway_box_keeps_the_focus_when_ticked_with_the_keyboard(browser, store):
    """Ticking "Approve anyway" redraws the approve bar: the focus stays on the box, not back at the top of the page."""
    page, errors = _open_review(browser, store, "Cascade", viewport={"width": 1400, "height": 1000})
    page.wait_for_selector("[name=override]")
    page.wait_for_timeout(800)  # the check made on opening the page
    page.locator("[name=override]").focus()
    with page.expect_response(lambda r: r.url.endswith("/check")):
        page.keyboard.press("Space")
    page.wait_for_timeout(300)
    assert page.is_checked("[name=override]")
    assert page.evaluate("document.activeElement.name") == "override" and errors == []


def test_in_a_browser_os_dark_mode_leaves_the_light_page_readable(browser, store):
    """The dashboard has one (light) look: a computer set to dark mode gets the same colours, not pale text meant for
    a dark background on the white page."""
    looks = {}
    for scheme in ("light", "dark"):
        page, errors = _open_review(browser, store, "Cascade", viewport={"width": 1400, "height": 1000},
                                    color_scheme=scheme)  # fmt: skip
        page.wait_for_selector(".inv-vendor-match span")
        page.wait_for_selector(".rvw-math")
        looks[scheme] = page.evaluate("""() => ['.inv-vendor-match', '.rvw-math', '.inv-math .ok, .inv-math .bad']
            .map(s => document.querySelector(s)).filter(Boolean)
            .map(e => [e.className, getComputedStyle(e).color, getComputedStyle(e).backgroundColor])""")
        assert errors == []
        page.close()
    assert len(looks["light"]) >= 2 and looks["dark"] == looks["light"]


def _open(browser, invoice_id, path=""):
    """A page on ``/review/<invoice_id>`` (or ``path``) once its opening check has answered; ``page.posts`` lists the
    last part of each POST's address from then on."""
    chromium, base = browser
    page = chromium.new_page(viewport={"width": 1400, "height": 1000})
    page.base = base
    page.errors = []
    page.on("pageerror", lambda e: page.errors.append(str(e)))
    page.goto(base + (path or f"/review/{invoice_id}"))
    page.wait_for_load_state("networkidle")
    page.posts = []
    page.on("request", lambda r: r.method == "POST" and page.posts.append(r.url.rsplit("/", 1)[-1]))
    return page


@pytest.mark.parametrize("where", ["park", "note"])
def test_in_a_browser_ctrl_enter_in_another_form_never_approves(browser, client, store, where):
    """Ctrl+Enter approves from the review form only: typed in Park's "Waiting for" box or in "Add a note", it is not
    taken for an approval, and the invoice stays in the queue."""
    invoice_id = clean_invoice(client, store)
    page = _open(browser, invoice_id)
    assert page.is_enabled("[data-approve]")
    if where == "park":
        page.click(".inv-actionbar summary[title^='Set it aside']")
        page.fill(".inv-actionbar details[open] input[name=reason]", "buyer to confirm the price")
    else:
        page.click(".inv-notes summary")
        page.fill(".inv-note-form input[name=text]", "asked Sam about the PO")
    page.keyboard.press("Control+Enter")
    page.wait_for_timeout(1200)
    assert "approve" not in page.posts and store.get_invoice(invoice_id)["status"] != APPROVED
    assert page.errors == []


def test_in_a_browser_ctrl_enter_in_the_why_dialog_keeps_the_reason(browser, client, store, monkeypatch):
    """Ctrl+Enter in "One question before saving" is its "Keep the reason and approve": the reason typed is sent."""
    from ap_coder.ai import lessons

    asked = [{"key": "k1", "field": "invoice_number", "label": "invoice number", "pattern": "", "proposed": "A1",
              "value": "A2", "times": 3, "explanation": ""}]  # fmt: skip
    monkeypatch.setattr(lessons, "due_for_why", lambda store, proposed, final: asked)
    invoice_id = clean_invoice(client, store)
    page = _open(browser, invoice_id)
    bodies: list[bytes] = []
    page.on("request", lambda r: r.url.endswith("/approve") and bodies.append(r.post_data_buffer or b""))
    page.click("[data-approve]")
    page.wait_for_selector("dialog.inv-why textarea")
    page.fill("dialog.inv-why textarea", "On these statements the number is the account number")
    page.keyboard.press("Control+Enter")
    page.wait_for_url(lambda url: f"/review/{invoice_id}" not in url, timeout=10000)
    assert len(bodies) == 2 and b"account number" in bodies[1] and b"why_done" in bodies[1]
    assert store.get_invoice(invoice_id)["status"] != REVIEW


def test_in_a_browser_enter_in_a_review_field_never_approves(browser, client, store):
    """Plain Enter in a text box of the review form is no approval (only the button and Ctrl+Enter approve)."""
    invoice_id = clean_invoice(client, store)
    page = _open(browser, invoice_id)
    page.click("[form=inv][name=po_number]")
    page.keyboard.type("4500123")
    page.keyboard.press("Enter")
    page.wait_for_timeout(1200)
    assert "approve" not in page.posts and store.get_invoice(invoice_id)["status"] == REVIEW
    page.keyboard.press("Control+Enter")  # in a field of the review form, Ctrl+Enter approves
    page.wait_for_url(lambda url: f"/review/{invoice_id}" not in url, timeout=10000)
    assert store.get_invoice(invoice_id)["status"] != REVIEW and page.errors == []


def test_in_a_browser_an_edit_typed_just_before_leaving_is_kept(browser, store):
    """An edit typed within the check's wait before the page is left another way (Back, closing the tab) is still
    kept as a draft."""
    invoice_id = store.list_invoices(REVIEW)[0]["id"]
    page = _open(browser, invoice_id, "/review")
    page.goto(f"{page.base}/review/{invoice_id}")
    page.wait_for_load_state("networkidle")
    page.fill("[form=inv][name=po_number]", "PO-NEW-1")
    page.wait_for_timeout(100)  # less than invoice.js's 450 ms
    page.go_back()
    page.wait_for_timeout(800)
    page.goto(f"{page.base}/review/{invoice_id}")
    page.wait_for_load_state("networkidle")
    assert page.input_value("[form=inv][name=po_number]") == "PO-NEW-1"


def test_in_a_browser_alt_arrows_in_a_text_box_stay_on_the_invoice(browser, store):
    """Alt+←/→ in a text box moves the caret word by word (Option+arrows on a Mac); outside one they go to the
    previous / next invoice."""
    invoice_id = store.list_invoices(REVIEW)[1]["id"]
    page = _open(browser, invoice_id)
    page.click("#vendor-name")
    page.keyboard.press("End")
    page.keyboard.press("Alt+ArrowLeft")
    page.wait_for_timeout(1000)
    assert page.url.endswith(f"/review/{invoice_id}")
    page.evaluate("() => document.activeElement.blur()")
    page.keyboard.press("Alt+ArrowLeft")
    page.wait_for_url(lambda url: not url.endswith(f"/review/{invoice_id}"), timeout=10000)


VENDOR_SPELLINGS = [
    "Agence Créative Mont-Royal S.E.N.C.", "Agence Créative Mont-Royal Incorporée", "Agence Créative Mont-Royal s.e.c.",
    "The Northwind IT Solutions Inc.", "Northwind IT Solutions", "Mueller GmbH & Co. KG", "Œuvres Lætitia Ltée",
    "Straße & Søn A.B.C. Corp.", "the", "The Inc", "Hydro-Québec", "  Café   Ünique,, S.A.R.L. ", "J.B. Hunt Co",
    "Ł.Ø.ß", "İstanbul Ltd", "3M Canada Company", "a.b", "X.Y.Z.Inc", "Ⅷ Ventures LLC", "ＡＢＣ Ltd", "and and",
    "Bell Canada & Co. Ltd. Inc.", "Société d'Énergie S.E.N.C.R.L.", "Ça va s.a.", "éa.b.c", "_a.b.", "",
]  # fmt: skip


def test_in_a_browser_the_vendor_key_is_the_servers(browser, client, store):
    """The page gets each listed vendor's key from the server (memory.vendor_key), and keys the name typed with
    invoice.js's copy of it, which gives the same keys: the line under the vendor agrees with the server's match."""
    import json

    from ap_coder.core.memory import vendor_key

    invoice_id = next(i["id"] for i in store.list_invoices(REVIEW) if "Agence" in (i["vendor_name"] or ""))
    text = client.get(f"/review/{invoice_id}").text
    data = json.loads(re.search(r'id="vendor-data">(.*?)</script>', text, re.S).group(1))
    assert data["list"] and all(v["key"] == vendor_key(v["name"]) for v in data["list"])
    page = _open(browser, invoice_id)
    keys = page.evaluate(
        """async (names) => {
             const { vendorKey } = await import(document.querySelector('script[src*="/invoice.js"]').src);
             return names.map(vendorKey);
           }""",
        VENDOR_SPELLINGS,
    )
    assert keys == [vendor_key(n) for n in VENDOR_SPELLINGS]
    listed = {vendor_key(v["name"]) for v in data["list"]}
    for name in VENDOR_SPELLINGS[:4]:
        assert vendor_key(name) in listed, name
        page.fill("#vendor-name", name)
        assert "In the vendor list" in page.inner_text("[data-vendor-match]"), name
    assert page.errors == []


def test_in_a_browser_a_refused_export_reloads_and_the_count_follows_the_ticks(browser, store):
    """An export refused because another tab exported the same invoices meanwhile refreshes the page, as it says; and
    after a refusal that stays on the page, the Export button's count still follows the ticks."""
    for inv in store.list_invoices(REVIEW)[:3]:
        store.approve_invoice(inv["id"], store.get_invoice(inv["id"])["ai_output"], "Jordan Lee")
    ready = store.unexported_approved()
    assert len(ready) >= 3
    page = _open(browser, None, "/exports?fmt=csv")
    # refused and staying on the page: nothing ticked
    page.evaluate("() => document.querySelectorAll('input.export-pick').forEach((p) => (p.checked = false))")
    page.evaluate("() => { const b = document.getElementById('export-go'); b.disabled = false; b.click(); }")
    page.wait_for_timeout(800)
    assert "create" in page.posts
    page.locator("input.export-pick:not(:disabled)").first.check()
    assert page.inner_text("#export-go").split("\n")[-1].strip() == "Export 1 invoice"
    # another tab exports them meanwhile
    store.create_export_batch([r["id"] for r in ready], "csv", actor="Other tab")
    page.evaluate("() => (window.__before = true)")
    page.click("#export-go")
    page.wait_for_function("() => window.__before !== true", timeout=10000)
    page.wait_for_load_state("networkidle")
    assert "exported meanwhile" in page.inner_text("body")
    assert page.locator("input.export-pick").count() == 0 and page.errors == []


def test_in_a_browser_apply_to_all_lines_fills_the_cost_centers_left_blank(browser, client, store):
    """Apply to all lines: one cost center picked, every line without one gets it, and Approve opens."""
    import json

    invoice_id = clean_invoice(client, store)
    output = store.get_invoice(invoice_id)["ai_output"]
    for li in output["line_items"]:
        li["predicted_cost_center"] = UNASSIGNED
    with store._conn() as conn:
        conn.execute("UPDATE invoices SET ai_output = ? WHERE id = ?", (json.dumps(output), invoice_id))
    client.app.state.invoice_drafts.clear()
    cc = store.reference_data().cost_centers.codes[0]
    page = _open(browser, invoice_id)
    assert "Pick a cost center" in page.inner_text(".inv-actionbar")
    page.click("[data-fill] > summary")
    page.select_option("[data-fill-cc]", cc)
    page.click("[data-fill-go]")
    page.wait_for_function("() => !document.querySelector('.inv-actionbar').innerText.includes('Pick a cost center')")
    values = page.eval_on_selector_all('[data-grid="lines"] select[name="line_cc"]', "els => els.map((e) => e.value)")
    assert values == [cc] * len(output["line_items"]) and "fill-lines" in page.posts and page.errors == []


def test_in_a_browser_a_message_after_apply_to_all_lines_leaves_the_approve_bar_clear(browser, client, store):
    """The toast "Cost center … set on 3 lines" sat in the corner over "Approve & teach": it now rises above the
    sticky approve bar (at the window's foot, as the pilot tester sees it)."""
    import json

    invoice_id = clean_invoice(client, store)
    output = store.get_invoice(invoice_id)["ai_output"]
    for li in output["line_items"]:
        li["predicted_cost_center"] = UNASSIGNED
    with store._conn() as conn:
        conn.execute("UPDATE invoices SET ai_output = ? WHERE id = ?", (json.dumps(output), invoice_id))
    client.app.state.invoice_drafts.clear()
    page = _open(browser, invoice_id)
    page.set_viewport_size({"width": 1366, "height": 860})
    page.click("[data-fill] > summary")
    page.select_option("[data-fill-cc]", store.reference_data().cost_centers.codes[0])
    page.click("[data-fill-go]")
    page.wait_for_selector("#toasts .flash")
    toast = page.locator("#toasts .flash").last.bounding_box()
    bar = page.locator(".inv-actionbar").bounding_box()
    assert toast["y"] + toast["height"] <= bar["y"], (toast, bar)  # wholly above the bar, so nothing in it is covered
    assert page.errors == []


def test_in_a_browser_not_in_the_vendor_list_is_said_only_once_a_vendor_master_is_imported(browser, store):
    """Without a vendor master, the vendors to pick from are the ones approved before: a new vendor is no mistake,
    so the screen doesn't ask to pick one or add it."""
    first, second = store.list_invoices(REVIEW)[:2]
    no_number = "[form=inv][name=gst_hst_registration_number]"  # a printed number names a vendor of the list
    with_master = _open(browser, second["id"])
    with_master.fill(no_number, "")
    with_master.fill("#vendor-name", "Brand New Vendor Ltd")
    assert "Not in the vendor list" in with_master.inner_text("[data-vendor-match]")

    with store._conn() as conn:  # no vendor master: only the vendors approved before
        conn.execute("UPDATE vendors SET in_master = 0 WHERE in_master = 1")
    store.approve_invoice(first["id"], store.get_invoice(first["id"])["ai_output"], "Jordan Lee")
    assert not store.has_vendor_master() and store.vendor_choices()
    page = _open(browser, second["id"])
    page.fill(no_number, "")
    page.fill("#vendor-name", "Brand New Vendor Ltd")
    page.wait_for_timeout(200)
    shown = page.inner_text("[data-vendor-match]")
    assert "Not in the vendor list" not in shown and "Pick the vendor" not in shown
    page.fill("#vendor-name", first["vendor_name"])
    assert "Approved before" in page.inner_text("[data-vendor-match]")
    assert page.errors == []


def test_in_a_browser_ten_files_chosen_to_import_are_listed_clear_of_the_process_button(browser, store, tmp_path):
    """Import invoices with ten files chosen: every name can be read, none under the "Process" button."""
    files = []
    for n in range(10):
        files.append(tmp_path / f"vendor_{n:02d}_invoice_2026-{n:04d}.pdf")
        files[-1].write_bytes(b"%PDF-1.4 " + bytes(n))
    page = _open(browser, None, "/process")
    page.set_input_files("#upload-files", [str(f) for f in files])
    page.wait_for_selector("#picked li")
    button = page.locator("#upload-go").bounding_box()
    names = page.locator("#picked li")
    assert names.count() == 10
    for i in range(10):
        box = names.nth(i).bounding_box()
        assert box["y"] + box["height"] <= button["y"] + 0.5, (i, box, button)
    assert page.errors == []
