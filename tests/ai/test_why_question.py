"""The question after a surprising correction (lessons.surprise, D-218): a person changed an account AP Coder had
coded by itself, or offered first; the next page asks why, once, the approval already made; the answer is that
lesson's reason, or "one-off" keeps the line out of what teaches."""

from __future__ import annotations

import copy
import re

from ap_coder.ai import lessons
from ap_coder.core.reference_data import UNASSIGNED
from ap_coder.core.schema import decided
from ap_coder.core.store import APPROVED, REVIEW, Store
from tests.ai.test_lessons import client_store  # noqa: F401 - the fixture
from tests.web.test_web_invoice import PAGE, form_of


def changed(ground_truth, **lines):
    """The sample invoice as proposed and as approved: ``lines`` {its place from 0: what the approval changes}."""
    proposed, final = copy.deepcopy(ground_truth), copy.deepcopy(ground_truth)
    for place, values in lines.items():
        final["line_items"][int(place.lstrip("n"))].update(values)
    return proposed, final


def test_an_account_ap_coder_coded_by_itself_and_a_person_changed_is_asked_about(tmp_path, ground_truth):
    store = Store(tmp_path / "a.db")
    proposed, final = changed(ground_truth, n0={"predicted_gl_code": "6999"})
    line = proposed["line_items"][0]
    line["decided_by"] = decided("supplier", lines=9, of=10, share=0.9)
    found = lessons.surprise(store, proposed, final)
    assert found["question"] == f"Why 6999 and not {line['predicted_gl_code']} for “{line['description'][:80]}”?"
    assert found["coded"] and found["line"] == line["line_number"]
    assert found["key"] == lessons.situation(ground_truth["vendor_name"], lessons.INVOICE, lessons.LINE_GL,
                                             lessons.line_pattern(line["description"]))  # fmt: skip
    assert [r["code"] for r in found["reasons"]] == [lessons.KIND, lessons.CHANGED, lessons.ONE_OFF]
    assert [r["label"] for r in found["reasons"]] == ["This supplier's lines like this go there",
                                                      "Last year's account no longer applies",
                                                      "One-off: don't learn from it"]  # fmt: skip
    assert lessons.surprise(store, proposed, copy.deepcopy(proposed)) is None  # nothing changed: nothing asked


def test_a_line_left_empty_for_a_person_or_only_guessed_is_never_asked_about(tmp_path, ground_truth):
    store = Store(tmp_path / "a.db")
    proposed, final = changed(ground_truth, n0={"predicted_gl_code": "6999"})
    line = proposed["line_items"][0]
    line.update(predicted_gl_code=UNASSIGNED, candidates=[], decided_by={})
    assert lessons.surprise(store, proposed, final) is None  # left empty: no confusion there
    line["candidates"] = [{"account": "6100", "code": "6100", "source": "words", "reason": "the words match"}]
    assert lessons.surprise(store, proposed, final) is None  # offered from the words alone: not nearly sure
    line.update(predicted_gl_code="6100", candidates=[], decided_by=decided("model"))
    assert lessons.surprise(store, proposed, final) is None  # its own guess, said as one
    added = copy.deepcopy(final)
    added["line_items"].append({**final["line_items"][0], "line_number": 99, "description": "Late fee on account"})
    line.update(predicted_gl_code="6999", decided_by={})
    assert lessons.surprise(store, proposed, added) is None  # a line the person added


def test_an_account_offered_first_from_the_suppliers_own_history_and_not_taken_is_asked_about(tmp_path, ground_truth):
    store = Store(tmp_path / "a.db")
    proposed, final = changed(ground_truth, n0={"predicted_gl_code": "6999"})
    line = proposed["line_items"][0]
    line.update(predicted_gl_code=UNASSIGNED, decided_by={}, candidates=[
        {"account": "6100", "code": "6100", "source": "supplier", "lines": 6, "of": 10, "share": 0.6},
        {"account": "6999", "code": "6999", "source": "supplier", "lines": 4, "of": 10, "share": 0.4}])  # fmt: skip
    found = lessons.surprise(store, proposed, final)
    assert found["question"].startswith("Why 6999 and not 6100 for “") and not found["coded"]
    assert lessons.reason_kept(found, lessons.KIND, "") == (
        "6999, not the 6100 offered first: This supplier's lines like this go there"
    )
    taken = copy.deepcopy(final)
    taken["line_items"][0]["predicted_gl_code"] = "6100"
    assert lessons.surprise(store, proposed, taken) is None  # the first one offered was taken
    line["candidates"][0].update(lines=3, share=0.3)
    assert lessons.surprise(store, proposed, final) is None  # its lines went to several accounts: not nearly sure


def test_one_question_an_invoice_the_line_ap_coder_was_surest_of(tmp_path, ground_truth):
    store = Store(tmp_path / "a.db")
    proposed, final = changed(ground_truth, n0={"predicted_gl_code": "6999"}, n1={"predicted_gl_code": "6998"})
    proposed["line_items"][0]["decided_by"] = decided("supplier", lines=6, of=10, share=0.6)
    proposed["line_items"][1]["decided_by"] = decided("vendor", lines=19, of=20, share=0.95)
    found = lessons.surprise(store, proposed, final)
    assert found["now"] == "6998" and found["line"] == proposed["line_items"][1]["line_number"]
    proposed["line_items"][0]["decided_by"] = {"source": "rule", "rule_id": 3, "rule": "Lunches"}
    found = lessons.surprise(store, proposed, final)
    assert found["now"] == "6999" and found["reasons"][1]["label"] == "The rule does not fit this line"


def test_with_a_chart_a_line_moved_to_the_unit_it_names_offers_that_reason(chart_company):
    """The made-up company's invoices as the pipeline read them: a line left with the supplier's usual account
    offered first, which a person codes to the same account on another business unit, the one the line names."""
    from ap_coder.core import chart

    store = chart_company.store
    reference = store.reference_data()
    pairs = reference.pairs
    for inv in store.list_invoices():
        proposed = store.get_invoice(inv["id"])["ai_output"]
        for place, line in enumerate(proposed["line_items"]):
            first = next(iter(line.get("candidates") or []), {})
            unit, gl = first.get("business_unit"), first.get("code")
            others = [u for u in pairs.bus_of(gl) if u != unit] if unit and gl and not first.get("guess") else []
            if not others or first.get("source") not in lessons.OWN_HISTORY or first.get("share", 1) < lessons.NEARLY:
                continue
            final = copy.deepcopy(proposed)
            name = pairs.name_of(others[0])
            final["line_items"][place].update(predicted_gl_code=gl, predicted_cost_center=others[0],
                                              description=f"{line['description']} - {name}")  # fmt: skip
            found = lessons.surprise(store, proposed, final, reference)
            if found is None or found["line"] != line["line_number"]:
                continue  # (an earlier line of the same kind speaks for it)
            if others[0] not in lessons._units_named(reference, final["line_items"][place]["description"]):
                continue  # (a unit whose name says too little to be told on a line)
            assert found["field"] == lessons.LINE_CC and not found["coded"]
            was, now = chart.join_account(unit, gl), chart.join_account(others[0], gl)
            assert found["question"].startswith(f"Why {now} and not {was} for “")
            assert found["reasons"][1] == {"code": lessons.UNIT, "label": f"The unit named on the line ({name})"}
            assert lessons.surprise(store, proposed, final) is None  # no chart given: a unit alone is no account
            return
    raise AssertionError("the made-up company has no line with an account offered first that another unit has")


# --- On the screen ------------------------------------------------------------------------------------------------


def _recode(rows):
    rows[0]["predicted_gl_code"] = "6200" if rows[0]["predicted_gl_code"] != "6200" else "6100"
    return rows


def _approve(client, store, iid, lines=_recode, **extra):
    data = {**form_of(store, iid, lines), **extra}
    return client.post(f"/review/{iid}/approve", data=data, headers=PAGE).json()


def _asked(page: str) -> re.Match | None:
    return re.search(r'<form method="post" action="(/review/\d+/why)" class="flash-why" data-once>\s*'
                     r'<input type="hidden" name="situation" value="([^"]+)">', page)  # fmt: skip


def test_the_next_page_asks_why_once_and_the_approval_is_not_held(client_store):  # noqa: F811
    client, store, ids = client_store
    was = store.get_invoice(ids[0])["ai_output"]["line_items"][0]
    done = _approve(client, store, ids[0])
    assert done["ok"] and store.get_invoice(ids[0])["status"] == APPROVED  # approved first, asked after
    now = store.get_invoice(ids[0])["final_output"]["line_items"][0]["predicted_gl_code"]
    page = client.get(done["redirect"]).text
    asked = _asked(page)
    assert asked and asked.group(1) == f"/review/{ids[0]}/why"
    assert f"Why {now} and not {was['predicted_gl_code']} for “{was['description']}”?" in page
    labels = ("This supplier&#39;s lines like this go there", "One-off: don&#39;t learn from it", ">Skip<", ">Keep<")
    for label in labels:
        assert label in page
    assert page.count('class="flash-why"') == 1 and 'name="answer"' in page
    assert _asked(client.get(f"/review/{ids[1]}").text) is None  # shown once: gone from the next page
    # An approval that changes no account asks nothing.
    plain = _approve(client, store, ids[1], lines=None)
    assert plain["ok"] and _asked(client.get(plain["redirect"]).text) is None


def test_the_answer_is_kept_with_the_lesson_and_shown_on_what_was_learned(client_store):  # noqa: F811
    client, store, ids = client_store
    url, key = _asked(client.get(_approve(client, store, ids[0])["redirect"]).text).groups()
    nothing = client.post(url, data={"situation": key}, headers=PAGE)
    assert nothing.status_code == 400 and "Skip" in nothing.json()["error"]
    kept = client.post(url, data={"situation": key, "reason": lessons.KIND, "answer": " staff  meals "}, headers=PAGE)
    assert kept.json() == {"ok": True, "toast": {"text": "Thanks: the reason is kept with that lesson (What was "
                                                 "learned, Lessons).", "tone": "ok"}}  # fmt: skip
    lesson = store.lesson(key)
    assert lesson["explanation"] == "This supplier's lines like this go there: staff meals"
    assert lessons.reaches_ai(lesson)  # one correction with its reason: read with the supplier's next invoices
    assert "This supplier&#39;s lines like this go there: staff meals" in client.get("/learning?tab=lessons").text
    typed = client.post(url, data={"situation": key, "answer": "Catering goes to meals"}, headers=PAGE)
    assert typed.json()["ok"] and store.lesson(key)["explanation"] == "Catering goes to meals"
    assert client.post(url, data={"situation": "other", "answer": "x"}, headers=PAGE).status_code == 409
    assert client.post(f"/review/{ids[0]}/undo-approve", headers=PAGE).json()["ok"]
    assert store.get_invoice(ids[0])["status"] == REVIEW
    assert client.post(url, data={"situation": key, "answer": "x"}, headers=PAGE).status_code == 409  # not approved


def test_a_one_off_keeps_the_correction_out_of_what_teaches(client_store):  # noqa: F811
    client, store, ids = client_store
    url, key = _asked(client.get(_approve(client, store, ids[0])["redirect"]).text).groups()
    pattern = store.lesson(key)["pattern"]
    assert store.lesson(key)["corrected"] == 1
    assert [r for r in store.feedback_rows() if lessons.line_pattern(r["description"]) == pattern]
    answer = client.post(url, data={"situation": key, "reason": lessons.ONE_OFF, "answer": "a team lunch"},
                         headers=PAGE)  # fmt: skip
    assert answer.json()["toast"]["text"] == "Kept as a one-off: AP Coder does not learn from that line."
    # As Learning's Forget: the line is out of the coding memory, and its lesson says nothing any more.
    assert not [r for r in store.feedback_rows() if lessons.line_pattern(r["description"]) == pattern]
    assert store.lesson(key)["corrected"] == 0 and not lessons.on_screen(store.lesson(key))
    (event,) = store.events(ids[0], ["lesson_one_off"])
    assert event["detail"]["reason"] == "a team lunch" and event["detail"]["lines"] == 1
    assert "One-off: not learned from" in client.get(f"/review/{ids[0]}").text
    assert store.get_invoice(ids[0])["status"] == APPROVED  # the approval itself stands


def test_the_setting_turns_the_questions_off(client_store):  # noqa: F811
    client, store, ids = client_store
    page = client.get("/settings/review").text
    assert 'name="why_surprise" value="1" checked' in page and "Ask why after a surprising correction" in page
    saved = {"approval_limit": "0", "terms_days": "30", "fx_rates": "", "why_after": "1", "why_shown": "1"}
    assert client.post("/settings/review", data=saved, headers=PAGE).status_code == 200  # the box not ticked
    assert not lessons.asks_why(store) and 'name="why_surprise" value="1">' in client.get("/settings/review").text
    done = _approve(client, store, ids[0], invoice_number="A-1")  # a header correction too: asked before saving at 1
    assert done.get("ok") and _asked(client.get(done["redirect"]).text) is None
    assert client.post("/settings/review", data={**saved, "why_surprise": "1"}, headers=PAGE).status_code == 200
    assert lessons.asks_why(store)
    # On again: the question before saving comes first, and is the invoice's one question.
    held = _approve(client, store, ids[1], invoice_number="A-2")
    assert held.get("ask_why") and store.get_invoice(ids[1])["status"] != APPROVED
    done = _approve(client, store, ids[1], invoice_number="A-2", why_done="1")
    assert done["ok"] and _asked(client.get(done["redirect"]).text) is None


def test_a_bulk_approval_never_asks(client_store, monkeypatch):  # noqa: F811
    client, store, ids = client_store
    asked = []
    monkeypatch.setattr(lessons, "surprise", lambda *a, **k: asked.append(a))
    answer = client.post("/review/bulk-approve", data={"ids": [str(i) for i in ids]}, headers=PAGE)
    assert answer.status_code == 200 and asked == []


def test_in_a_browser_the_question_is_answered_in_one_click_or_skipped(client_store, chromium):  # noqa: F811
    from tests.browser import browser_on, wait_until

    client, store, ids = client_store
    with browser_on(chromium, client.app) as (browser, base):
        page = browser.new_page()
        page.goto(base + _approve(client, store, ids[0])["redirect"])
        key = page.locator(".flash-why input[name=situation]").input_value()
        assert "Why " in page.locator(".flash", has=page.locator(".flash-why")).inner_text()
        page.get_by_role("button", name="This supplier's lines like this go there").click()
        wait_until(page, lambda: page.locator(".flash-why").count() == 0)
        assert page.locator(".flash-why").count() == 0  # answered: the question is gone, the page stays
        assert "the reason is kept" in page.locator("#toasts").inner_text()
        assert store.lesson(key)["explanation"] == "This supplier's lines like this go there"
        assert page.locator("form.flash-undo").count() == 1  # the approval's own message, its Undo still there

        page.goto(base + _approve(client, store, ids[1])["redirect"])
        asked = page.locator(".flash-why")
        key = asked.locator("input[name=situation]").input_value()
        before = store.lesson(key)["explanation"]
        asked.get_by_role("button", name="Skip").click()
        assert page.locator(".flash-why").count() == 0 and store.lesson(key)["explanation"] == before
