"""Lessons (lessons.py): AP's corrections remembered per situation; "why" asked when they keep coming back; the
reasons read by the AI and shown to the reviewer."""

from __future__ import annotations

import copy
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from ap_coder.ai import lessons
from ap_coder.core.config import Settings
from ap_coder.core.store import APPROVED, Store, load_sample_setup
from ap_coder.web.app import create_app
from tests.web.test_web_invoice import PAGE, form_of


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
    lessons.record(store, 2, proposed, final)  # the same correction twice: the line lesson is shown
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
    from ap_coder.ai import verifier

    store = Store(tmp_path / "a.db")
    lessons.record(store, 1, _doc(ground_truth, "ACCT 1"), _doc(ground_truth, "1-2026"))
    store.explain_lesson(lessons.situation(ground_truth["vendor_name"], lessons.INVOICE, "invoice_number"),
                         "account number then year")  # fmt: skip
    seen = []
    monkeypatch.setattr(verifier, "review", lambda ask, shown, **kw: seen.append(shown) or verifier.Review(
        verifier.AGREED, {}, {}, 1, 1.0))  # fmt: skip
    from tests.ai.test_verifier import FakeCoder

    path = tmp_path / "x.pdf"
    from tests.conftest import SAMPLES

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
    from tests.conftest import SAMPLES

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
    assert client.post("/settings/review", data=data, headers=PAGE).status_code == 200
    assert lessons.why_after(store) == 5
    assert client.post("/settings/review", data={**data, "why_after": "99"}, headers=PAGE).status_code == 400


def test_a_lesson_reaches_the_ai_after_two_agreeing_corrections_or_one_with_a_reason(tmp_path, ground_truth):
    """A single correction may be a slip: it is counted, but the AI and the review screen's hints wait for a second
    correction to the same value, or for the clerk's reason (D-72)."""
    store = Store(tmp_path / "a.db")
    vendor = ground_truth["vendor_name"]
    proposed = _doc(ground_truth, "ACCT 1")

    def corrected(n: int, number: str) -> None:
        lessons.record(store, n, proposed, _doc(ground_truth, number))

    def shown() -> list[str]:
        return [line for line in lessons.prompt_lines(store, vendor) if "invoice number" in line]

    key = lessons.situation(vendor, lessons.INVOICE, "invoice_number")
    corrected(1, "1-2026")
    assert store.lesson(key)["corrected"] == 1 and not lessons.reaches_ai(store.lesson(key)) and shown() == []
    # The reviewer sees it at once (a person decides), said as a single correction.
    screen = lessons.prompt_lines(store, vendor, screen=True)
    assert any(line.startswith(lessons.SEEN_ONCE) and "invoice number" in line for line in screen)
    corrected(2, "2-2026")  # a second correction, to another value: the two don't agree
    assert store.lesson(key)["agreeing"] == 1 and shown() == []
    corrected(3, "2-2026")  # the same value again: two agree
    assert store.lesson(key)["agreeing"] == 2 and shown()
    with store._conn() as conn:
        store._withdraw_lessons(conn, 3)
    assert shown() == []  # withdrawn: one correction to that value is left
    store.explain_lesson(key, "account number then year", "Jane")
    assert shown() and "AP's reason" in shown()[0]  # one correction and the clerk's reason
    assert not any(line.startswith(lessons.SEEN_ONCE) for line in lessons.prompt_lines(store, vendor, screen=True))


def test_cost_center_corrections_are_lessons_too(tmp_path, ground_truth):
    """A line's cost center is a lesson of its own (``line_cc``), beside its GL account: corrected, confirmed, and
    once it agrees twice, read by the AI as "go to cost center"."""
    store = Store(tmp_path / "a.db")
    proposed = _doc(ground_truth, "1")
    proposed["line_items"][0]["predicted_cost_center"] = "CC100"
    final = copy.deepcopy(proposed)
    final["line_items"][0]["predicted_cost_center"] = "CC200"
    rows = [r for r in lessons.outcomes(proposed, final) if r["field"] == lessons.LINE_CC]
    first = next(r for r in rows if r["corrected"])
    assert (first["proposed"], first["value"]) == ("CC100", "CC200") and first["pattern"]
    gl = [r for r in lessons.outcomes(proposed, final) if r["field"] == lessons.LINE_GL and r["corrected"]]
    assert gl == []  # the GL account was kept: no GL correction
    for n in (1, 2):
        lessons.record(store, n, proposed, final)
    lines = lessons.prompt_lines(store, final["vendor_name"], fields="lines")
    assert any("go to cost center CC200" in line for line in lines)
    blank = copy.deepcopy(proposed)
    blank["line_items"][0]["predicted_cost_center"] = "UNASSIGNED"
    filled = [r for r in lessons.outcomes(blank, final) if r["field"] == lessons.LINE_CC and r["value"] == "CC200"]
    assert filled and not filled[0]["corrected"]  # filled in where none was proposed: no correction


def test_lesson_effectiveness_before_and_after_and_a_suggestion_never_a_retirement(tmp_path, ground_truth):
    """On the Lessons tab, each lesson's corrections per invoice of that vendor and field before it reached the AI
    and after; one still corrected as often after a few invoices is suggested for retiring, never retired (D-74)."""
    store = Store(tmp_path / "a.db")
    vendor = ground_truth["vendor_name"]
    proposed = _doc(ground_truth, "ACCT 1")
    key = lessons.situation(vendor, lessons.INVOICE, "invoice_number")
    for n in (1, 2):  # corrected twice the same way: it reaches the AI after the second
        lessons.record(store, n, proposed, _doc(ground_truth, "1-2026"))
    effect = lessons.effect(store.lesson_history([key])[key], store.lesson(key))
    assert effect["before"] == {"invoices": 2, "corrected": 2, "rate": 1.0} and effect["after"]["invoices"] == 0
    assert effect["verdict"] == lessons.TOO_EARLY
    history = store.lesson_history([key])[key]
    right = [{**history[0], "corrected": 0, "invoice_id": n} for n in (3, 4, 5)]  # read right since
    effect = lessons.effect(history + right, store.lesson(key))
    assert effect["after"] == {"invoices": 3, "corrected": 0, "rate": 0.0} and effect["verdict"] == lessons.WORKING
    for n in (6, 7, 8):  # corrected again and again since: the lesson does not help
        lessons.record(store, n, proposed, _doc(ground_truth, "1-2026"))
    effect = lessons.effect(store.lesson_history([key])[key], store.lesson(key))
    assert effect["after"]["rate"] == 1.0 and effect["verdict"] == lessons.NOT_WORKING
    assert not store.lesson(key)["retired"]  # suggested, never retired on its own
    rows = {r["situation"]: r for r in __import__("ap_coder.web.pages.learning", fromlist=["x"]).lessons_context(
        store)["lessons"]}  # fmt: skip
    assert rows[key]["effect"]["verdict"] == lessons.NOT_WORKING


def test_corrections_from_before_each_approval_was_kept_still_count(tmp_path, ground_truth):
    """A lesson corrected twice before ``lesson_approvals`` existed, and once since, counted as corrected once: the
    upgrade took it away from the AI (B-785). The corrections from before still count."""
    store = Store(tmp_path / "a.db")
    vendor = ground_truth["vendor_name"]
    proposed = _doc(ground_truth, "ACCT 1")
    key = lessons.situation(vendor, lessons.INVOICE, "invoice_number")
    for n in (1, 2):
        lessons.record(store, n, proposed, _doc(ground_truth, "2-2026"))
    with store._conn() as conn:
        conn.execute("DELETE FROM lesson_approvals")  # as an older version kept them: the counts only
    assert store.lesson(key)["agreeing"] == 2 and lessons.reaches_ai(store.lesson(key))
    lessons.record(store, 3, proposed, _doc(ground_truth, "2-2026"))
    assert store.lesson(key)["agreeing"] == 3 and lessons.reaches_ai(store.lesson(key))
    with store._conn() as conn:
        store._withdraw_lessons(conn, 3)
    assert store.lesson(key)["agreeing"] == 2  # the one since withdrawn: the two from before are left


# --- Bulk approvals and confirmations (B-955) ----------------------------------------------------------------------

_BULK_OUT = {"vendor_name": "Acme Ltd", "invoice_number": "A-1", "invoice_date": "2026-10-01", "currency": "CAD",
             "subtotal": 100.0, "tax_total": 0.0, "grand_total": 100.0, "tax_lines": [],
             "line_items": [{"line_number": 1, "description": "Mobility services", "quantity": 1, "unit_price": 100,
                             "amount": 100.0, "predicted_gl_code": "6000", "predicted_cost_center": "",
                             "reasoning_justification": "memory"}]}  # fmt: skip


def _bulk_line(gl):
    out = copy.deepcopy(_BULK_OUT)
    out["line_items"][0]["predicted_gl_code"] = gl
    return out


def _approve_and_learn(st, tmp_path, n, proposed, final, actor, bulk=False):
    from ap_coder.capture.workflow import learn_from_approval

    f = tmp_path / f"{actor}{n}.txt"
    f.write_text(f"{actor} {n}")
    iid = st.add_invoice(f, proposed, {"issues": []})
    st.approve_invoice(iid, final, actor, bulk=bulk)
    learn_from_approval(st, iid, final, actor=actor, bulk=bulk)
    return iid


def test_an_untouched_bulk_approval_is_not_seen_by_the_lessons(tmp_path):
    """Nobody opened an invoice approved in bulk: it is no confirmation of a lesson, nor an invoice the lesson was
    seen on (B-955)."""
    st = Store(tmp_path / "ap.db")
    for n in range(2):  # two people corrected the invoice number the same way: the lesson reaches the AI
        _approve_and_learn(st, tmp_path, n, {**_BULK_OUT, "invoice_number": f"A1{n}"},
                           {**_BULK_OUT, "invoice_number": f"A-1{n}"}, "alice")  # fmt: skip
    key = lessons.situation("Acme Ltd", "invoice", "invoice_number")
    before = st.lesson(key)
    for n in range(3):
        out = {**_BULK_OUT, "invoice_number": f"B{n}"}
        _approve_and_learn(st, tmp_path, n, out, out, "bob", bulk=True)
    assert st.lesson(key)["seen"] == before["seen"]


def test_bulk_approvals_do_not_make_a_lesson_look_working(tmp_path):
    st = Store(tmp_path / "ap.db")
    for n in range(2):  # AP corrects 6000 -> 6100 twice: the lesson reaches the AI
        _approve_and_learn(st, tmp_path, n, _bulk_line("6000"), _bulk_line("6100"), "alice")
    for n in range(3):  # three invoices nobody opened, bulk approved as proposed
        _approve_and_learn(st, tmp_path, n, _bulk_line("6100"), _bulk_line("6100"), "bob", bulk=True)
    key = lessons.situation("Acme Ltd", "invoice", lessons.LINE_GL, "mobility services")
    assert lessons.effect(st.lesson_history([key])[key], st.lesson(key))["verdict"] == lessons.TOO_EARLY


def test_a_correction_in_a_bulk_approval_still_counts(tmp_path):
    """An earlier approver's correction on a reopened invoice, approved in bulk: still a correction (B-955)."""
    st = Store(tmp_path / "ap.db")
    _approve_and_learn(st, tmp_path, 0, _bulk_line("6000"), _bulk_line("6100"), "bob", bulk=True)
    key = lessons.situation("Acme Ltd", "invoice", lessons.LINE_GL, "mobility services")
    assert st.lesson(key)["corrected"] == 1


def test_a_blank_line_coded_to_another_account_does_not_confirm_the_lesson(tmp_path):
    st = Store(tmp_path / "ap.db")
    for n in range(2):  # "lines like 'mobility services' are coded to 6100"
        _approve_and_learn(st, tmp_path, n, _bulk_line("6000"), _bulk_line("6100"), "alice")
    key = lessons.situation("Acme Ltd", "invoice", lessons.LINE_GL, "mobility services")
    before = st.lesson(key)
    # The line left blank; AP codes it to 6200, not the lesson's account: no confirmation.
    _approve_and_learn(st, tmp_path, 3, _bulk_line("UNASSIGNED"), _bulk_line("6200"), "alice")
    assert st.lesson(key)["confirmed"] == before["confirmed"]
    # Coded to the lesson's own account: that confirms it.
    _approve_and_learn(st, tmp_path, 4, _bulk_line("6100"), _bulk_line("6100"), "alice")
    assert st.lesson(key)["confirmed"] == before["confirmed"] + 1


def test_a_line_bulk_approved_unchanged_is_left_out_of_the_accuracy(tmp_path):
    from ap_coder.core.memory import ACCEPTED, BULK

    st = Store(tmp_path / "ap.db")
    f = tmp_path / "x.txt"
    f.write_text("b")
    iid = st.add_invoice(f, _bulk_line("6100"), {"issues": []})
    assert st.approve_invoice(iid, _bulk_line("6100"), "bob", bulk=True)[ACCEPTED] == 0
    m = st.metrics()
    assert m["lines_accepted"] == 0 and m["line_accuracy"] is None and m["lines_bulk"] == 1
    assert st.feedback_rows()[0]["outcome"] == BULK
