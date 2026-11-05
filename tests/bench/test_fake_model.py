"""The shared stand-in model server (ap_coder/bench/fake_model.py): the routes AP Coder calls, answered as LM Studio
answers them; its delays, slots, hangs, failures, going down and its control over HTTP. Everything on 127.0.0.1."""

import base64
import json
import socket
import threading
import time
import urllib.error
import urllib.request

import pytest

from ap_coder.ai import local_llm, page_reader
from ap_coder.ai.local_llm import parse_lm_studio_listing
from ap_coder.bench.__main__ import main
from ap_coder.bench.fake_model import CHAT, EMBED, READER, FakeModel, kind_of, page_key, seconds_range
from ap_coder.core.config import LocalLLMSettings, PageReaderSettings, Settings

REAL_FETCH = local_llm._fetch_json  # conftest refuses every model server in the tests
DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def ask(url, body=None, timeout=10.0):
    """(status, JSON) of a GET, or of a POST of ``body``."""
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with DIRECT.open(request, timeout=timeout) as answer:
            return answer.status, json.loads(answer.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def chat(fake, model=CHAT, text="Hello", timeout=10.0, **extra):
    body = {"model": model, "messages": [{"role": "user", "content": text}], **extra}
    return ask(fake.base_url + "/chat/completions", body, timeout)


@pytest.fixture
def real_fetch(monkeypatch):
    monkeypatch.setattr(local_llm, "_fetch_json", REAL_FETCH)
    local_llm.forget_status()
    page_reader.forget_status()
    yield
    local_llm.forget_status()
    page_reader.forget_status()


def test_latencies_are_read_as_lo_hi_seconds():
    assert seconds_range("8-20") == (8.0, 20.0) and seconds_range("3") == (3.0, 3.0) and seconds_range(2) == (2.0, 2.0)
    assert seconds_range([1, 4]) == (1.0, 4.0) and seconds_range("0.5 - 1.5") == (0.5, 1.5)
    with pytest.raises(ValueError):
        seconds_range("9-2")


def test_it_lists_its_models_as_each_lm_studio_does_and_ap_coder_picks_the_right_one_for_each_job(real_fetch):
    with FakeModel() as fake:
        status, ids = ask(fake.base_url + "/models")
        assert status == 200 and [m["id"] for m in ids["data"]] == [CHAT, READER, EMBED]
        v0 = parse_lm_studio_listing(ask(fake.url + "/api/v0/models")[1])
        v1 = parse_lm_studio_listing(ask(fake.url + "/api/v1/models")[1])
        for listing in (v0, v1):
            assert {m.key for m in listing.models} == {CHAT, READER} and [e for e, _ in listing.embeddings] == [EMBED]
            assert listing.find(READER).loaded_context == 20480 and listing.find(CHAT).vision
        settings = Settings(llm=LocalLLMSettings(base_url=fake.base_url), page_reader=PageReaderSettings())
        assert local_llm.check_server(settings.llm, use_cache=False).model == CHAT
        assert page_reader.reader_status(settings, use_cache=False).model == READER
        assert ask(fake.url + "/nowhere")[0] == 404
    with FakeModel(api="none", models=["gemma3:4b"]) as ollama:  # no LM Studio route: another server
        assert ask(ollama.url + "/api/v1/models")[0] == 404 and ask(ollama.url + "/api/v0/models")[0] == 404


def test_each_kind_of_question_gets_a_plausible_answer():
    suggest = {"model": CHAT, "messages": [
        {"role": "system", "content": "You code accounts-payable invoice lines to the company's GL accounts."},
        {"role": "user", "content": "Vendor: Staples\n1. Paper | 12.00\n2. Toner | 80.00"}],
        "response_format": {"type": "json_schema", "json_schema": {"schema": {"properties": {"lines": {
            "items": {"properties": {"gl_code": {"enum": ["6100", "6200"]}}}}}}}}}  # fmt: skip
    review = {"model": CHAT, "messages": [
        {"role": "system", "content": "You are a careful accounts-payable reviewer at a Canadian company."},
        {"role": "user", "content": [{"type": "text", "text": 'What was entered, to check:\n{"fields": {"grand_total":'
                                      ' 5}, "lines": [{"line_number": 1}]}\nFor reference'}]}],
        "response_format": {"type": "json_schema", "json_schema": {"schema": {"properties": {
            "confident": {"type": "boolean"}, "fields": {"items": {"properties": {"field": {
                "enum": ["grand_total"]}}}}}}}}}  # fmt: skip
    image = {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(b"png").decode()}}
    header = {"model": CHAT, "messages": [{"role": "system", "content": "You read the header of a supplier's invoice"},
                                          {"role": "user", "content": [image]}],
              "response_format": {"type": "json_schema", "json_schema": {"schema": {"properties": {
                  "vendor_name": {}, "grand_total": {}}}}}}  # fmt: skip
    page = {"model": READER, "messages": [{"role": "user", "content": [image, {"type": "text", "text": "OCR"}]}]}
    assert [kind_of("POST", "/v1/chat/completions", b) for b in (suggest, review, header, page)] == [
        "suggest", "review", "header", "page"]  # fmt: skip
    with FakeModel(header={"vendor_name": "Acme Ltd"}, page_texts={page_key(b"png"): "INVOICE 42"}) as fake:

        def content(body):
            status, answer = ask(fake.base_url + "/chat/completions", body)
            assert status == 200, answer
            return answer["choices"][0]["message"]["content"]

        assert json.loads(content(suggest))["lines"] == [
            {"line_number": 1, "gl_code": "6100", "cost_center": "", "reason": "fake"},
            {"line_number": 2, "gl_code": "6100", "cost_center": "", "reason": "fake"}]  # fmt: skip
        answer = json.loads(content(review))
        assert answer["confident"] and answer["fields"][0]["agree"] and answer["lines"][0]["line_number"] == 1
        assert json.loads(content(header)) == {"vendor_name": "Acme Ltd", "grand_total": None}
        assert content(page) == "INVOICE 42"
        fake.set(reviewer="disagree")
        assert not json.loads(content(review))["fields"][0]["agree"]
        assert fake.stats()["served_by_kind"] == {"suggest": 1, "review": 2, "header": 1, "page": 1}
        assert chat(fake, model="nobody")[0] == 404 and chat(fake, model=EMBED)[0] == 400
        assert ask(fake.base_url + "/embeddings", {"model": EMBED, "input": "x"})[1]["data"][0]["embedding"]


def test_the_page_reader_reads_a_streamed_page_and_loads_its_model_with_the_context_it_needs(real_fetch):
    with FakeModel(models=[{"id": READER, "ctx": 4096}, CHAT]) as fake:
        png = b"\x89PNG a page"
        fake.learn_pages([png], ["INVOICE NW-1\nTotal 105.00"])
        settings = Settings(llm=LocalLLMSettings(base_url=fake.base_url), page_reader=PageReaderSettings())
        assert page_reader.load_reader(settings, READER) == ""  # loaded again with 20,480, the short one unloaded
        instances = ask(fake.url + "/api/v1/models")[1]["models"][0]["loaded_instances"]
        assert [i["config"]["context_length"] for i in instances] == [20480]
        assert page_reader.transcribe(settings, png, model=READER) == "INVOICE NW-1\nTotal 105.00"
        assert fake.stats()["by_kind"]["page"] == 1 and fake.page_answers() == {page_key(png): 1}


def test_an_answer_takes_its_latency_scaled():
    with FakeModel(latency="0.4", time_scale=0.5) as fake:
        started = time.monotonic()
        assert chat(fake)[0] == 200
        assert 0.18 <= time.monotonic() - started < 2.0
        assert fake.stats()["answer_seconds"]["max"] >= 0.18


def test_slots_answer_that_many_at_once_and_the_rest_wait_in_line():
    with FakeModel(latency="0.15", slots=2) as fake:
        threads = [threading.Thread(target=chat, args=(fake,)) for _ in range(4)]
        started = time.monotonic()
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        took = time.monotonic() - started
        s = fake.stats()
        assert s["served"] == 4 and s["max_active"] == 2 and s["max_in_flight"] == 4
        assert 0.27 <= took < 3.0 and s["queue_wait"]["max"] >= 0.12 and s["queue_wait"]["n"] == 4


def _wait_for(fake, done, seconds=10.0):
    """The fake's stats once ``done(stats)`` holds (or after ``seconds``): a request's bookkeeping takes a moment."""
    deadline = time.monotonic() + seconds
    while not done(stats := fake.stats()) and time.monotonic() < deadline:
        time.sleep(0.02)
    return stats


def test_a_hung_request_is_never_answered_and_keeps_its_slot_until_released():
    with FakeModel(hang_rate=1.0) as fake:
        # The client gives up only once the request is hanging: a short timeout raced the request's arrival (CI).
        body = json.dumps({"model": CHAT, "messages": [{"role": "user", "content": "Hello"}]}).encode()
        client = socket.create_connection(("127.0.0.1", fake.port), timeout=10)
        client.sendall(b"POST /v1/chat/completions HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\n"
                       + f"Content-Length: {len(body)}\r\n\r\n".encode() + body)  # fmt: skip
        assert _wait_for(fake, lambda s: s["hanging"] >= 1)["hanging"] == 1
        client.close()
        # Wait until the hang is wholly over: counted as given up, and as hung once no longer hanging.
        s = _wait_for(fake, lambda s: s["hung_gave_up"] >= 1 and s["hanging"] == 0 and s["hung"] >= 1)
        assert s["hung"] == 1 and s["hung_gave_up"] == 1 and s["served"] == 0 and s["hanging"] == 0
        fake.set(hang_rate=0.0)
        results = []
        fake.set(hang_rate=1.0)
        stuck = threading.Thread(target=lambda: results.append(_quietly(chat, fake, timeout=10)))
        stuck.start()
        while fake.stats()["hanging"] < 1:
            time.sleep(0.02)
        fake.set(hang_rate=0.0)
        assert fake.release_hung() == 1  # LM Studio restarted: the connection closed, no answer
        stuck.join(5)
        assert results == ["dropped"] and chat(fake)[0] == 200


def _quietly(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)[0]
    except OSError:
        return "dropped"


def test_a_request_queued_during_a_hang_drill_is_answered_once_the_drill_is_over():
    # Whether a request hangs is decided as it takes its slot, from the rate then, not when it arrived.
    with FakeModel(hang_rate=1.0, slots=1) as fake:
        results = []
        people = [threading.Thread(target=lambda: results.append(_quietly(chat, fake, timeout=10))) for _ in "ab"]
        people[0].start()
        _wait_for(fake, lambda s: s["hanging"] >= 1)
        people[1].start()
        _wait_for(fake, lambda s: s["waiting"] >= 1)  # in line behind the hung one, during the drill
        fake.set(hang_rate=0.0)
        assert fake.release_hung() == 1  # the drill over: LM Studio restarted
        for person in people:
            person.join(5)
        assert results == ["dropped", 200] and fake.stats()["hanging"] == 0


def test_a_failure_rate_answers_errors():
    with FakeModel(fail_rate=1.0) as fake:
        status, answer = chat(fake)
        assert status == 500 and "fake failure" in answer["error"]
        assert fake.stats()["failed"] == 1 and fake.stats()["served"] == 0


def test_down_after_drops_every_request_until_it_is_up_again():
    with FakeModel(down_after=1) as fake:
        assert chat(fake)[0] == 200
        assert _quietly(chat, fake) == "dropped" and _quietly(ask, fake.base_url + "/models") == "dropped"
        assert fake.stats()["down"] and fake.stats()["dropped"] >= 1
        fake.set(down=False)  # counted once: it stays up now
        assert chat(fake)[0] == 200 and chat(fake)[0] == 200


def test_the_control_endpoint_slows_swaps_and_kills_it_while_it_runs():
    with FakeModel() as fake:
        status, answer = ask(fake.url + "/__fake/set", {"latency": "0.15-0.15", "models": ["gemma-4-12b-it", READER]})
        assert status == 200 and answer["settings"]["models"] == ["gemma-4-12b-it", READER]
        assert [m["id"] for m in ask(fake.base_url + "/models")[1]["data"]] == ["gemma-4-12b-it", READER]
        started = time.monotonic()
        assert chat(fake)[0] == 404 and chat(fake, model="gemma-4-12b-it")[0] == 200  # the old model is gone
        assert time.monotonic() - started >= 0.28  # slowed: latency sets the pages' and the chat's
        assert answer["settings"]["page_latency"] == answer["settings"]["chat_latency"] == [0.15, 0.15]
        assert ask(fake.url + "/__fake/set", {"down": True})[0] == 200
        assert _quietly(chat, fake, model="gemma-4-12b-it") == "dropped"
        stats = ask(fake.url + "/__fake/stats")[1]
        assert stats["down"] and stats["by_kind"]["chat"] == 3 and stats["served"] == 1
        assert ask(fake.url + "/__fake/set", {"slots": 0, "down": False})[1]["settings"]["slots"] == 1
        assert ask(fake.url + "/__fake/set", {"what": 1})[0] == 400
        assert [h["outcome"] for h in ask(fake.url + "/__fake/history")[1]["history"]] == [
            "failed", "served", "dropped"]  # fmt: skip


def test_the_command_serves_until_stopped(tmp_path, monkeypatch, capsys):
    started = threading.Event()
    seen = {}

    def serve_forever(self):
        self.start()
        seen["models"] = ask(self.base_url + "/models")[1]
        started.set()
        self.stop()

    monkeypatch.setattr(FakeModel, "serve_forever", serve_forever)
    assert main(["fake-model", "--port", "0", "--latency", "8-20", "--slots", "2", "--embed", ""]) == 0
    assert started.is_set() and [m["id"] for m in seen["models"]["data"]] == [CHAT, READER]
    assert "latency (8.0, 20.0) s" in capsys.readouterr().out
