"""Lessons (lessons.py): AP's corrections remembered per situation; "why" asked when they keep coming back; the
reasons read by the AI and shown to the reviewer."""

from __future__ import annotations

import copy
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from ap_coder import lessons
from ap_coder.config import Settings
from ap_coder.store import APPROVED, Store, load_sample_setup
from ap_coder.web.app import create_app

from .test_web_invoice import PAGE, form_of


def _doc(ground_truth, number):
    d = copy.deepcopy(ground_truth)
    d["invoice_number"] = number
    return d


# --- Per situation ------------------------------------------------------------------------------------------------


def test_what_an_approval_says_about_each_situation(ground_truth):
    proposed = _doc(ground_truth, "ACCT 4471")
    final = _doc(ground_truth, "4471-2026-10")
    final["line_items"][0]["predicted_gl_code"] = "6999"
    rows = {(r["field"], r["pattern"]): r for r in lessons.outcomes(proposed, final)}
    number = rows[("invoice_number", "")]
    assert number["corrected"] and (number["proposed"], number["value"]) == ("ACCT 4471", "4471-2026-10")
    assert not rows[("vendor_name", "")]["corrected"]
    line = next(r for (f, _), r in rows.items() if f == lessons.LINE_GL and r["corrected"])
    assert line["value"] == "6999" and line["pattern"]
    credit = {**final, "grand_total": -100.0}
    assert {r["kind"] for r in lessons.outcomes(proposed, credit)} == {lessons.CREDIT_NOTE}


def test_line_patterns_ignore_numbers_and_dates():
    assert lessons.line_pattern("Mobility services Oct 2026 - 555-0101") == "mobility services oct"
    assert lessons.line_pattern("Mobility services Nov 2026 - 555-0199") == "mobility services nov"
    assert lessons.line_pattern("HP 26X toner cartridges (G, P)") == "hp toner cartridges"


def test_corrections_counted_then_asked_why_then_reset(tmp_path, ground_truth):
    store = Store(tmp_path / "a.db")
    proposed, final = _doc(ground_truth, "ACCT 1"), _doc(ground_truth, "1-2026")
    assert lessons.due_for_why(store, proposed, final) == []  # never corrected before: nothing to ask
    for i in range(2):
        lessons.record(store, i, proposed, final, "Jane")
    asked = lessons.due_for_why(store, proposed, final)  # the third time
    assert [a["field"] for a in asked] == ["invoice_number"] and asked[0]["times"] == 3
    lessons.record(store, 3, proposed, final, "Jane")
    assert store.explain_lesson(asked[0]["key"], "  The invoice number is the account number and the year.  ", "Jane")
    lesson = store.lesson(asked[0]["key"])
    assert lesson["since_why"] == 0 and lesson["corrected"] == 3 and lesson["explained_by"] == "Jane"
    assert lessons.due_for_why(store, proposed, final) == []  # just explained: not asked again yet
    lessons.record(store, 4, proposed, final)
    lessons.record(store, 5, proposed, final)
    assert lessons.due_for_why(store, proposed, final)[0]["explanation"].startswith("The invoice number")  # again
    lessons.record(store, 6, final, final)  # read right this time
    assert store.lesson(asked[0]["key"])["confirmed"] == 1
    store.set_setting(lessons.SETTING, "10")
    assert lessons.due_for_why(store, proposed, final) == []


def test_the_ai_and_the_reviewer_read_the_lessons(tmp_path, ground_truth):
    store = Store(tmp_path / "a.db")
    proposed, final = _doc(ground_truth, "ACCT 1"), _doc(ground_truth, "1-2026")
    final["line_items"][0]["predicted_gl_code"] = "6999"
    lessons.record(store, 1, proposed, final)
    key = lessons.situation(final["vendor_name"], lessons.INVOICE, "invoice_number")
    store.explain_lesson(key, "account number then year", "Jane")
    lines = lessons.prompt_lines(store, final["vendor_name"], lessons.INVOICE, gl_names={"6999": "Telecom"})
    assert lines[0].startswith(f"{final['vendor_name']} invoices: AP corrects the invoice number")
    assert "AP's reason: account number then year" in lines[0]
    assert any("coded to GL 6999 Telecom" in line for line in lines)
    assert lessons.prompt_lines(store, final["vendor_name"], fields="lines") == [
        line for line in lessons.prompt_lines(store, final["vendor_name"]) if "coded to GL" in line
    ]
    store.retire_lesson(store.lesson(key)["id"], "Jane")
    assert not any("invoice number" in line for line in lessons.prompt_lines(store, final["vendor_name"]))
    assert lessons.prompt_lines(store, "Somebody Else Ltd") == []


def test_the_ai_reviewer_is_given_the_lessons(tmp_path, ground_truth, monkeypatch):
    from ap_coder import verifier

    store = Store(tmp_path / "a.db")
    lessons.record(store, 1, _doc(ground_truth, "ACCT 1"), _doc(ground_truth, "1-2026"))
    store.explain_lesson(lessons.situation(ground_truth["vendor_name"], lessons.INVOICE, "invoice_number"),
                         "account number then year")  # fmt: skip
    seen = []
    monkeypatch.setattr(verifier, "review", lambda ask, shown, **kw: seen.append(shown) or verifier.Review(
        verifier.AGREED, {}, {}, 1, 1.0))  # fmt: skip
    from .test_verifier import FakeCoder

    path = tmp_path / "x.pdf"
    from .conftest import SAMPLES

    path.write_bytes((SAMPLES / "northwind_ON_HST_NW-2026-0912.pdf").read_bytes())
    iid = store.add_invoice(path, ground_truth, {})
    store.queue_verification(iid)
    verifier.verify_one(Settings(), store, coder=FakeCoder(), after_page_reader=False)
    assert any("account number then year" in line for line in seen[0]["lessons"])


# --- On the review screen -----------------------------------------------------------------------------------------


@pytest.fixture
def client_store(tmp_path, ground_truth):
    store = Store(tmp_path / "private" / "ap.db")
    load_sample_setup(store)
    from .conftest import SAMPLES

    ids = []
    for n in range(4):
        path = tmp_path / f"inv{n}.pdf"
        path.write_bytes((SAMPLES / "northwind_ON_HST_NW-2026-0912.pdf").read_bytes())
        ids.append(store.add_invoice(path, _doc(ground_truth, f"ACCT {n}"), {}))
    s = Settings()
    app = create_app(replace(s, llm=replace(s.llm, provider="off")), store, start_reader=False)
    return TestClient(app, base_url="http://localhost"), store, ids


def _approve(client, store, iid, **extra):
    data = {**form_of(store, iid, invoice_number=f"{iid}-2026"), **extra}
    return client.post(f"/review/{iid}/approve", data=data, headers=PAGE).json()


def test_the_third_same_correction_asks_why_before_saving(client_store):
    client, store, ids = client_store
    for iid in ids[:2]:
        assert _approve(client, store, iid)["ok"]
    answer = _approve(client, store, ids[2])
    assert answer.get("ask_why") and answer["ask_why"]["fields"][0]["field"] == "invoice_number"
    assert store.get_invoice(ids[2])["status"] != APPROVED  # nothing saved yet
    key = answer["ask_why"]["fields"][0]["key"]
    done = _approve(client, store, ids[2], why_done="1", **{f"why::{key}": "Account number, then the year."})
    assert done["ok"] and store.get_invoice(ids[2])["status"] == APPROVED
    assert "reason kept" in client.get(done["redirect"]).text.lower()
    assert store.lesson(key)["explanation"] == "Account number, then the year." and store.lesson(key)["since_why"] == 0
    page = client.get(f"/review/{ids[3]}").text
    assert "Learned from AP for this vendor" in page and "Account number, then the year." in page
    assert _approve(client, store, ids[3])["ok"]  # just explained: not asked again


def test_skipping_the_question_asks_again_next_time(client_store):
    client, store, ids = client_store
    for iid in ids[:2]:
        _approve(client, store, iid)
    assert _approve(client, store, ids[2], why_done="1")["ok"]  # skipped
    assert _approve(client, store, ids[3]).get("ask_why")


def test_the_lessons_tab(client_store):
    client, store, ids = client_store
    _approve(client, store, ids[0])
    page = client.get("/learning?tab=lessons").text
    assert "Lessons" in page and "Invoice number" in page and "read as ACCT" in page
    lesson = store.list_lessons()[0]
    assert client.post("/learning/lessons/explain", data={"situation": lesson["situation"], "explanation": "x y z"},
                       headers=PAGE).status_code == 200  # fmt: skip
    assert store.lesson(lesson["situation"])["explanation"] == "x y z"
    assert client.post("/learning/lessons/explain", data={"situation": "nope", "explanation": "x"},
                       headers=PAGE).status_code == 404  # fmt: skip
    assert client.post("/learning/lessons/retire", data={"lesson_id": lesson["id"], "retired": "1"},
                       headers=PAGE).status_code == 200  # fmt: skip
    assert store.lesson(lesson["situation"])["retired"] == 1
    assert "Restore" in client.get("/learning?tab=lessons").text


def test_the_question_threshold_in_settings(client_store):
    client, store, _ = client_store
    assert 'name="why_after"' in client.get("/settings/review").text
    data = {"approval_limit": "0", "terms_days": "30", "fx_rates": "", "why_after": "5"}
    assert client.post("/settings/review/approval", data=data, headers=PAGE).status_code == 200
    assert lessons.why_after(store) == 5
    assert client.post("/settings/review/approval", data={**data, "why_after": "99"}, headers=PAGE).status_code == 400
