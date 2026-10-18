"""The AI reviewer (verifier.py): the local chat model checks a whole invoice against its pages, thinking first, and is
asked again until it is sure; an invoice touchless processing would approve waits for it."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from ap_coder import page_reader, page_worker, verifier
from ap_coder.capture.types import CHECK
from ap_coder.pipeline import InvoicePipeline
from ap_coder.store import APPROVED, REVIEW

from .test_local_llm import REAL_FETCH, FakeLMStudio, chat_reply, local_settings  # imported first: the real fetch
from .test_uniform_reading import MODEL, PDF, STEM, _settings, store, touchless  # noqa: F401 - fixtures

SHOWN = {"fields": {f: "" for f in verifier.FIELDS} | {"grand_total": 1150.0, "invoice_number": "HPS-2026-0347"},
         "lines": [{"line_number": 1, "description": "Consulting", "amount": 1000.0}], "taxes": []}  # fmt: skip


def _answer(disputed=None, lines=None, confident=True, fields=None, numbers=(1,)):
    disputed = disputed or {}
    asked = fields or list(verifier.FIELDS)
    return {"fields": [{"field": f, "agree": f not in disputed, "on_page": disputed.get(f), "reason": "x"}
                       for f in asked],
            "lines": [{"line_number": n, "agree": n not in (lines or {}), "reason": (lines or {}).get(n, "")}
                      for n in numbers],
            "confident": confident, "summary": "checked"}  # fmt: skip


class Script:
    """A model answering from a list, keeping what it was asked."""

    def __init__(self, *answers):
        self.answers, self.asked = list(answers), []

    def __call__(self, text, fields, lines):
        self.asked.append((text, fields, lines))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer(fields) if callable(answer) else answer


# --- Asked until it is sure ----------------------------------------------------------------------------------------


def test_it_agrees_at_once():
    found = verifier.review(Script(_answer()), SHOWN)
    assert (found.status, found.rounds, found.fields) == (verifier.AGREED, 1, {})


def test_a_field_it_reads_differently_twice_is_a_disagreement():
    ask = Script(_answer({"grand_total": "1,105.00"}),
                 lambda fields: _answer({"grand_total": "1,105.00"}, fields=fields))  # fmt: skip
    found = verifier.review(ask, SHOWN)
    assert found.status == verifier.DISAGREES and found.rounds == 2
    assert found.fields["grand_total"]["on_page"] == "1,105.00"
    text, fields, lines = ask.asked[1]
    assert fields == ["grand_total"] and lines == []  # the second round: only what it disputed...
    assert "earlier answers" in text and "1,105.00" in text  # ...with its own earlier answer


def test_a_second_look_can_settle_it():
    ask = Script(_answer({"invoice_number": "HPS-2026-0374"}, confident=False),
                 lambda fields: _answer(fields=fields))  # fmt: skip
    found = verifier.review(ask, SHOWN)
    assert (found.status, found.rounds) == (verifier.AGREED, 2)


def test_never_sure_is_unsure_after_the_rounds():
    ask = Script(*[_answer(confident=False)] * 3)
    found = verifier.review(ask, SHOWN, rounds=3)
    assert found.status == verifier.UNSURE and found.rounds == 3 and "3 rounds" in found.why
    assert all(fields == list(verifier.FIELDS) for _, fields, _ in ask.asked)  # nothing disputed: all of it again


def test_the_time_cap_stops_it():
    now = [0.0]

    def ask(text, fields, lines):
        now[0] += 200.0  # 200 s an answer on a slow laptop
        return _answer(confident=False)

    found = verifier.review(ask, SHOWN, rounds=6, seconds=300, clock=lambda: now[0])
    assert found.status == verifier.UNSURE and found.rounds == 2 and "out of time" in found.why


def test_an_answer_it_cannot_give_is_unsure():
    found = verifier.review(Script(RuntimeError("cut off at the token limit")), SHOWN)
    assert found.status == verifier.UNSURE and "cut off" in found.why


def test_an_outage_on_the_first_question_is_not_an_answer():
    from ap_coder.inference import CodingError

    with pytest.raises(CodingError):
        verifier.review(Script(CodingError("The local model isn't answering", temporary=True)), SHOWN)
    # Answered once, then out of time on the second look: that is its verdict (not sure).
    late = verifier.review(Script(_answer(confident=False), CodingError("took longer", temporary=True)), SHOWN)
    assert late.status == verifier.UNSURE and late.rounds == 2 and "took longer" in late.why


def test_an_item_left_unanswered_is_not_confident():
    partial = {"fields": [{"field": "grand_total", "agree": True, "on_page": None, "reason": ""}], "lines": [],
               "confident": True, "summary": ""}  # fmt: skip
    found = verifier.review(Script(partial, partial, partial), SHOWN)
    assert found.status == verifier.UNSURE


def test_agreed_needs_every_item_agreed_in_a_confident_round():
    """Round 1 disputes one field, skips the rest and is not confident; round 2 answers only that field, confident.
    Nothing else was ever confirmed: it is not agreed, and the round that was not confident did not narrow the next."""
    skipped = {"fields": [{"field": "invoice_number", "agree": False, "on_page": "HPS-2026-0374", "reason": "x"}],
               "lines": [], "confident": False, "summary": ""}  # fmt: skip
    only_that = {"fields": [{"field": "invoice_number", "agree": True, "on_page": None, "reason": "ok"}],
                 "lines": [], "confident": True, "summary": ""}  # fmt: skip
    ask = Script(skipped, only_that, only_that)
    found = verifier.review(ask, SHOWN, rounds=3)
    assert found.status != verifier.AGREED
    assert ask.asked[1][1] == list(verifier.FIELDS) and ask.asked[1][2] == [1]  # all of it again
    # Extreme: only the vendor answered, then an empty confident answer.
    vendor_only = {"fields": [{"field": "vendor_name", "agree": True, "on_page": None, "reason": ""}], "lines": [],
                   "confident": False, "summary": ""}  # fmt: skip
    empty = {"fields": [], "lines": [], "confident": True, "summary": ""}
    assert verifier.review(Script(vendor_only, empty, empty), SHOWN, rounds=3).status != verifier.AGREED


def test_a_confident_round_still_narrows_to_what_it_disputed():
    ask = Script(_answer({"grand_total": "1,105.00"}), lambda fields: _answer(fields=fields, numbers=()))
    found = verifier.review(ask, SHOWN)
    assert (found.status, found.rounds) == (verifier.AGREED, 2) and ask.asked[1][1:] == (["grand_total"], [])


def test_every_hold_is_recorded_whatever_the_order():
    from ap_coder import azure_check

    decided = {"state": "autonomous", "auto": True, "audit": False, "reason": "ok"}
    for first, second in ((azure_check.hold, verifier.hold), (verifier.hold, azure_check.hold)):
        held = first(decided, azure_check.Plan(hold=True) if first is azure_check.hold else verifier.Plan(hold=True))
        held = second(held, azure_check.Plan(hold=True) if second is azure_check.hold else verifier.Plan(hold=True))
        assert held["auto"] is False and held.get("azure_hold") and held.get("verifier_hold")
    not_auto = {"auto": False, "reason": "x"}
    assert verifier.hold(azure_check.hold(not_auto, azure_check.Plan(hold=True)), verifier.Plan(hold=True)) == not_auto


def test_a_line_it_disputes_twice():
    ask = Script(_answer(lines={1: "GL 6000 Office Supplies does not fit consulting"}),
                 lambda fields: _answer(lines={1: "still not office supplies"}, fields=fields))  # fmt: skip
    found = verifier.review(ask, SHOWN)
    assert found.status == verifier.DISAGREES and 1 in found.lines


# --- What it means for the invoice ---------------------------------------------------------------------------------


def test_the_issues_say_what_it_read_and_drop_a_field_since_corrected():
    result = {
        "status": verifier.DISAGREES,
        "fields": {"grand_total": {"value": 1150.0, "on_page": "1,105.00", "reason": "the total box"}},
        "lines": {},
    }
    [(severity, code, message)] = verifier.issues(result)
    assert (severity, code) == ("warning", "VERIFIER_DISAGREES") and "1,105.00" in message and "total" in message
    assert verifier.issues(result, {"grand_total": 1150.0}) and not verifier.issues(result, {"grand_total": 1105.0})
    unsure = verifier.issues({"status": verifier.UNSURE, "why": "out of time (5 min)", "fields": {}, "lines": {}})
    assert unsure[0][1] == "VERIFIER_UNSURE" and "out of time" in unsure[0][2]
    assert verifier.issues({"status": verifier.AGREED}) == []


def test_settings_are_kept_within_bounds(tmp_path):
    from ap_coder.store import Store

    s = Store(tmp_path / "a.db")
    assert verifier.load_settings(s) == verifier.ReviewerSettings(True, 3, 5.0)
    verifier.save_settings(s, on=False, rounds=99, minutes=0)
    assert verifier.load_settings(s) == verifier.ReviewerSettings(False, verifier.MAX_ROUNDS, 0.5)


def test_the_queue(tmp_path):
    from ap_coder.store import Store

    s = Store(tmp_path / "a.db")
    assert s.queue_verification(5, "every invoice") and s.queue_verification(6)
    taken = s.next_verification(after_page_reader=False)
    assert taken["invoice_id"] == 5 and s.verification(5)["status"] == "verifying"
    assert not s.queue_verification(5)  # being reviewed: left to finish
    s.finish_verification(5, "done", result={"status": "agreed"}, rounds=1, seconds=40)
    assert s.verification(5)["result"] == {"status": "agreed"} and s.verification_counts()["agreed"] == 1
    assert s.queue_verification(5)  # read again (the page reader): reviewed again, its old result kept until then
    assert s.verification(5)["status"] == "waiting" and s.verification(5)["result"] == {"status": "agreed"}
    s.queue_page_read(6, "new")
    assert s.next_verification(after_page_reader=True)["invoice_id"] == 5  # 6 waits for the page reader
    assert s.next_verification(after_page_reader=True) is None


def test_a_review_that_ends_without_a_result_keeps_the_last_result(tmp_path):
    from ap_coder.store import Store

    s = Store(tmp_path / "a.db")
    s.queue_verification(5)
    s.next_verification(after_page_reader=False)
    s.finish_verification(5, "done", result={"status": "disagreed"}, rounds=2, seconds=30)
    s.queue_verification(5)
    s.next_verification(after_page_reader=False)
    s.finish_verification(5, "failed", error="the chat model stopped")
    assert s.verification(5)["status"] == "failed" and s.verification(5)["result"] == {"status": "disagreed"}


# --- With the chat model: thinking on, the page images, the schema -------------------------------------------------


def test_the_model_is_asked_to_think_with_the_pages(monkeypatch, reference):
    from ap_coder import local_llm
    from ap_coder.imaging import PageImage
    from ap_coder.inference import THINKING_TOKENS

    monkeypatch.setattr(local_llm, "_fetch_json", REAL_FETCH)
    server = FakeLMStudio(["gemma-4-12b"], [{"id": "gemma-4-12b", "type": "vlm", "state": "loaded"}],
                          lambda body: chat_reply(json.dumps(_answer()), model="gemma-4-12b"))  # fmt: skip
    try:
        from ap_coder.inference import InvoiceCoder

        coder = InvoiceCoder(local_settings(server.base_url), reference, provider="local")
        image = PageImage(1, "image/png", b"\x89PNG\r\n\x1a\n" + b"0" * 20)
        found = verifier.review(verifier.ask_model(coder, [image]), SHOWN)
    finally:
        server.close()
    assert found.status == verifier.AGREED
    body = server.chats[-1]
    assert body["chat_template_kwargs"] == {"enable_thinking": True} and body["reasoning_effort"] == "high"
    assert body["max_tokens"] >= THINKING_TOKENS
    content = body["messages"][-1]["content"]
    assert any(part.get("type") == "image_url" for part in content)
    assert "Think it through" in body["messages"][0]["content"]


def test_a_draft_in_its_thinking_is_not_its_answer():
    class Thinker(FakeCoder):
        def _local_create(self, model, messages, schema=None, *, thinking=False, timeout=None):
            draft = "Draft: " + json.dumps(_answer()) + " wait"
            message = SimpleNamespace(content="Hmm, the total looks wrong.", reasoning_content=draft, model_extra={})
            return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="stop")])

    found = verifier.review(verifier.ask_model(Thinker(), []), SHOWN)
    assert found.status == verifier.UNSURE and "no usable answer" in found.why


def test_each_call_is_given_only_the_time_left():
    """The budget (5 minutes an invoice) bounds each call, not only the gap between calls: one call can't run the
    full AP_LLM_TIMEOUT_SECONDS."""
    left = [300.0]
    coder = FakeCoder(_answer(), _answer())
    verifier.ask_model(coder, [], seconds_left=lambda: left[0])("check", list(verifier.FIELDS), [1])
    left[0] = 2.0
    verifier.ask_model(coder, [], seconds_left=lambda: left[0])("check", list(verifier.FIELDS), [1])
    assert coder.timeouts == [300.0, verifier.MIN_CALL_SECONDS]


# --- In line: held from touchless approval until it agrees ---------------------------------------------------------


class FakeCoder:
    """The chat model, answering ``answers`` in turn (agree, or what to dispute)."""

    def __init__(self, *answers, vision=True):
        self.answers = list(answers)
        self.settings = _settings()
        self.profile = SimpleNamespace(supports_vision=vision)
        self.calls, self.timeouts = [], []

    def _prepare_local(self):
        return "gemma-4-12b"

    def _local_create(self, model, messages, schema=None, *, thinking=False, timeout=None):
        import re

        self.calls.append(thinking)
        self.timeouts.append(timeout)
        data = self.answers.pop(0)
        fields = schema["properties"]["fields"]["items"]["properties"]["field"]["enum"]
        data = data(fields) if callable(data) else data
        # Every line it was asked about is answered (agreed, unless disputed).
        asked = messages[-1]["content"][0]["text"].split("For reference")[0]
        numbers = sorted({int(n) for n in re.findall(r'"line_number": (\d+)', asked)})
        disputed = {li["line_number"]: li for li in data["lines"] if not li["agree"]}
        data = {**data, "lines": [disputed.get(n) or {"line_number": n, "agree": True, "reason": ""}
                                  for n in numbers]}  # fmt: skip
        message = SimpleNamespace(content=json.dumps(data), reasoning_content="", model_extra={})
        return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="stop")])


def _read_by_the_page_reader(store, monkeypatch):  # noqa: F811
    transcript = (PDF.parent / f"{STEM}.md").read_text(encoding="utf-8")
    status = page_reader.ReaderStatus(reachable=True, lm_studio=True, model=MODEL, document_reader=True,
                                      state="loaded", candidates=[MODEL])  # fmt: skip
    monkeypatch.setattr(page_reader, "reader_status", lambda settings, use_cache=True: status)
    monkeypatch.setattr(page_reader, "load_reader", lambda settings, model=None: "")
    monkeypatch.setattr(page_reader, "read_document", lambda settings, path, **kw: page_reader.PageReading(
        model=MODEL, pages=[transcript], seconds=[90.0], page_count=1))  # fmt: skip
    return page_worker.read_one(_settings(), store)


def test_touchless_waits_for_the_reviewer_then_it_approves(store, touchless, monkeypatch):  # noqa: F811
    result = InvoicePipeline(_settings(), store.reference_data(), store=store).process(PDF)
    iid = result.invoice_id
    assert store.verification(iid)["status"] == "waiting"
    outcome = _read_by_the_page_reader(store, monkeypatch)
    assert outcome.status == "done" and outcome.updated and not outcome.auto_approved  # held for the reviewer
    inv = store.get_invoice(iid)
    assert inv["status"] == REVIEW and inv["meta"]["capture"].get("verifier_hold")
    assert store.verification(iid)["status"] == "waiting"  # reviewed again, as the page reader left it

    from .test_azure_check import _touchless_vendor

    _touchless_vendor(store, store.get_invoice(iid)["ai_output"])  # touchless still applies, as it stands now
    coder = FakeCoder(_answer())
    done = verifier.verify_one(_settings(), store, coder=coder)
    assert done.status == verifier.AGREED and done.approved and coder.calls == [True]  # asked with thinking on
    assert 0 < coder.timeouts[0] <= 300  # within the reviewer's 5 minutes, not the 600 s of any call
    inv = store.get_invoice(iid)
    assert inv["status"] == APPROVED and "verifier_hold" not in inv["meta"]["capture"]
    assert inv["meta"]["verifier"]["status"] == verifier.AGREED


def _held_for_the_reviewer(store, monkeypatch, **capture):  # noqa: F811
    from .test_azure_check import _touchless_vendor

    iid = InvoicePipeline(_settings(), store.reference_data(), store=store).process(PDF).invoice_id
    _read_by_the_page_reader(store, monkeypatch)
    _touchless_vendor(store, store.get_invoice(iid)["ai_output"])
    if capture:
        notes = {**store.get_invoice(iid)["meta"]["capture"], **capture}
        store.update_after_check(iid, None, None, (), {"capture": notes})
    return iid


def test_the_reviewer_agreeing_does_not_approve_past_a_pending_azure_hold(store, touchless, monkeypatch):  # noqa: F811
    iid = _held_for_the_reviewer(store, monkeypatch, azure_hold=True)
    done = verifier.verify_one(_settings(), store, coder=FakeCoder(_answer()))
    assert done.status == verifier.AGREED and not done.approved
    inv = store.get_invoice(iid)
    assert inv["status"] == REVIEW and inv["meta"]["capture"].get("azure_hold")


def test_a_reopen_during_the_review_keeps_aps_corrections(store, touchless, monkeypatch):  # noqa: F811
    """AP approves with a correction and reopens it while the reviewer is still looking: the reviewer agreeing with
    the old proposal must not approve it over AP's coding."""
    iid = _held_for_the_reviewer(store, monkeypatch)
    proposed = store.get_invoice(iid)["ai_output"]
    corrected = {**proposed, "invoice_number": "CORRECTED-1"}

    def meanwhile(fields):
        store.approve_invoice(iid, corrected, "Mia")
        store.reopen(iid, "Mia", "one more look")
        return _answer(fields=fields)

    done = verifier.verify_one(_settings(), store, coder=FakeCoder(meanwhile))
    assert done.status == verifier.AGREED and not done.approved
    inv = store.get_invoice(iid)
    assert inv["status"] == REVIEW and inv["final_output"]["invoice_number"] == "CORRECTED-1"


def test_a_disagreement_marks_the_field_and_sends_it_to_a_person(store, touchless, monkeypatch):  # noqa: F811
    result = InvoicePipeline(_settings(), store.reference_data(), store=store).process(PDF)
    iid = result.invoice_id
    _read_by_the_page_reader(store, monkeypatch)
    wrong = {"grand_total": "9,999.00"}
    coder = FakeCoder(_answer(wrong), lambda fields: _answer(wrong, fields=fields))
    done = verifier.verify_one(_settings(), store, coder=coder)
    assert done.status == verifier.DISAGREES and not done.approved
    inv = store.get_invoice(iid)
    assert inv["status"] == REVIEW and inv["requires_review"]
    codes = {i["code"] for i in inv["validation"]["issues"]}
    assert "VERIFIER_DISAGREES" in codes
    capture = store.get_capture(iid)
    total = capture["fields"]["grand_total"]
    assert total["status"] == CHECK and "other:9,999.00" in total["sources"]
    assert any("AI reviewer reads 9,999.00" in r for r in total["reasons"])
    # Reviewed again (a new reading): the old issue is replaced, not added to.
    store.queue_verification(iid)
    verifier.verify_one(_settings(), store, coder=FakeCoder(_answer()))
    codes = [i["code"] for i in store.get_invoice(iid)["validation"]["issues"]]
    assert "VERIFIER_DISAGREES" not in codes


def test_no_chat_model_leaves_it_in_line(store, touchless, monkeypatch):  # noqa: F811
    result = InvoicePipeline(_settings(), store.reference_data(), store=store).process(PDF)
    store.finish_page_read(result.invoice_id, "done")
    done = verifier.verify_one(_settings(), store, coder=FakeCoder(vision=False))
    assert done.status == "postponed" and store.verification(result.invoice_id)["status"] == "waiting"
    assert "can't see pages" in store.verification(result.invoice_id)["error"]


class DownCoder(FakeCoder):
    """LM Studio stopped (or one call ran out of time) after the model was found: every call fails as the real
    coder's does."""

    def __init__(self, failure):
        super().__init__()
        self.failure = failure

    def _local_create(self, model, messages, schema=None, *, thinking=False, timeout=None):
        import httpx
        import openai

        from ap_coder.inference import InvoiceCoder

        request = httpx.Request("POST", "http://127.0.0.1:9/v1/chat/completions")
        exc = openai.APITimeoutError(request) if self.failure == "timeout" else openai.APIConnectionError(
            request=request)  # fmt: skip
        raise InvoiceCoder._local_error(self, exc, timeout)


@pytest.mark.parametrize("failure", ["stopped", "timeout"])
def test_the_servers_outage_is_not_the_reviewers_verdict(store, touchless, monkeypatch, failure):  # noqa: F811
    """The chat model not answering the first question is no review: the invoice stays in line, held, with no
    VERIFIER_UNSURE; after MAX_TRIES such outages it leaves the line as failed (put in line again, it starts over)."""
    iid = _held_for_the_reviewer(store, monkeypatch)
    for attempt in range(1, verifier.MAX_TRIES):
        done = verifier.verify_one(_settings(), store, coder=DownCoder(failure))
        row = store.verification(iid)
        assert done.status == "postponed" and row["status"] == "waiting" and row["tries"] == attempt
        inv = store.get_invoice(iid)
        assert inv["meta"]["capture"].get("verifier_hold") and "verifier" not in inv["meta"]
        assert not any(i["code"].startswith("VERIFIER") for i in inv["validation"]["issues"])
    done = verifier.verify_one(_settings(), store, coder=DownCoder(failure))
    assert done.status == "failed" and store.verification(iid)["status"] == "failed"
    assert store.get_invoice(iid)["meta"]["capture"].get("verifier_hold")  # never approved without the review
    assert store.queue_verification(iid) and store.verification(iid)["tries"] == 0


def test_the_reviewer_is_shown_the_math(store):  # noqa: F811
    """The stored proposal carries gl_distribution, which the coding model refuses: the math is still worked out."""
    iid = InvoicePipeline(_settings(), store.reference_data(), store=store).process(PDF).invoice_id
    output = store.get_invoice(iid)["ai_output"]
    assert "gl_distribution" in output
    shown = verifier.proposal(output, None)
    assert shown.get("math") and all(row.startswith(("OK", "WRONG")) for row in shown["math"])
    assert "the math worked out again" in verifier.question(shown, ["grand_total"], [], None)
    assert '"math"' in verifier.question(shown, ["grand_total"], [], None)


def test_with_the_reviewer_off_nothing_waits_for_it(store, touchless, monkeypatch):  # noqa: F811
    verifier.save_settings(store, on=False, rounds=3, minutes=5)
    result = InvoicePipeline(_settings(), store.reference_data(), store=store).process(PDF)
    assert store.verification(result.invoice_id) is None
    assert verifier.verify_one(_settings(), store, coder=FakeCoder()) is None
    assert _read_by_the_page_reader(store, monkeypatch).auto_approved


def test_the_background_thread_reviews_when_nothing_is_left_to_read(store, monkeypatch):  # noqa: F811
    seen = []
    monkeypatch.setattr(verifier, "verify_one", lambda settings, store, **kw: seen.append(kw) or None)
    reader = page_worker.BackgroundReader(_settings, lambda: store)
    monkeypatch.setattr(page_worker, "ready", lambda settings, store: ("", "OvisOCR2 isn't running"))
    assert reader._review_one(_settings(), store) == page_worker.IDLE_SECONDS
    assert seen == [{"after_page_reader": False}]  # OvisOCR2 not running: nothing waits for it
