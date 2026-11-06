"""A backup model server (D-180): the breaker of each model server (the chat model's, the page reader's) sends the
calls to the backup after two calls in a row the primary didn't answer, drops the calls still waiting on it, probes
it in the background and takes it back only after it answered well for a while. Two stand-in model servers
(``bench/fake_model.py``) on 127.0.0.1 play the Mac Studio and the Mac mini. Without a backup nothing changes."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from dataclasses import replace

import pytest

from ap_coder.ai import local_llm, page_reader, page_worker, pipeline, verifier
from ap_coder.ai.inference import ChatTimeout, CodingError, InvoiceCoder, suggest_accounts
from ap_coder.ai.local_llm import BACKUP, PRIMARY
from ap_coder.ai.pipeline import ArrivalBreaker, InvoicePipeline
from ap_coder.bench.fake_model import READER, FakeModel, page_key
from ap_coder.core.config import Settings
from ap_coder.core.paths import PROJECT_DIR
from ap_coder.core.store import Store, load_sample_setup
from ap_coder.core.store.folder import read_file
from ap_coder.intake.imports import Reader
from tests.conftest import SAMPLES

REAL_FETCH = local_llm._fetch_json  # conftest replaces it for every test: these ask fake model servers
CHAT = "gemma-4-12b-it"
SWAPPED = "qwen/qwen3.5-9b"
LINES = [(1, "Printer paper, 10 boxes", 40.0)]
STEM = "harbourview_NS_HST_HPS-2026-0347"


@pytest.fixture(autouse=True)
def real_fetch(monkeypatch):
    monkeypatch.setattr(local_llm, "_fetch_json", REAL_FETCH)
    monkeypatch.setattr(pipeline, "ARRIVAL_BREAKER", ArrivalBreaker())
    local_llm.forget_status()
    local_llm.forget_failovers()
    yield
    local_llm.forget_status()
    local_llm.forget_failovers()


@pytest.fixture
def quick(monkeypatch):
    """The breaker's times made short: a second's hold, a probe every 0.1 s."""
    monkeypatch.setattr(local_llm, "HOLD_SECONDS", 1.0)
    monkeypatch.setattr(local_llm, "PROBE_SECONDS", 0.1)
    monkeypatch.setattr(local_llm, "PROBE_TIMEOUT", 1.0)


@pytest.fixture
def two():
    """The primary and its backup, the same models on both."""
    with FakeModel(models=[CHAT, READER], slots=4) as primary, FakeModel(models=[CHAT, READER], slots=4) as backup:
        yield primary, backup


def _settings(base_url: str, backup_url: str = "", **llm) -> Settings:
    s = Settings()
    return replace(s, llm=replace(s.llm, provider="local", base_url=base_url, backup_url=backup_url, **llm))


def _models_asked(fake: FakeModel, kind: str = "suggest") -> list[str]:
    return [h["model"] for h in fake.history() if h["kind"] == kind]


def _served(fake: FakeModel, kind: str) -> int:
    return sum(1 for h in fake.history() if h["kind"] == kind and h["outcome"] == "served")


def _until(condition, seconds: float = 10.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.02)
    return condition()


# --- B-1843 ---------------------------------------------------------------------------------------------------------


def test_a_chat_model_lm_studio_swapped_is_asked_for_no_more(reference):
    """B-1843: a coder kept the chat model it found first for its whole life, so once LM Studio had another one (the
    model swapped), a capture process asked for the old one in vain until it was restarted: every arriving invoice's
    lines were left for AP. It asks the server's model again (its status is kept 30 s)."""
    with FakeModel(models=[CHAT, READER]) as fake:
        coder = InvoiceCoder(_settings(fake.base_url), reference, provider="local")
        assert suggest_accounts(coder, "Acme Supplies", LINES)
        fake.set(models=[SWAPPED, READER])
        local_llm.forget_status()  # (what 30 s does)
        picks = suggest_accounts(coder, "Acme Supplies", LINES)
        asked = _models_asked(fake)
    assert picks and asked == [CHAT, SWAPPED], asked


# --- Without a backup -------------------------------------------------------------------------------------------------


def test_without_a_backup_nothing_changes(tmp_path, monkeypatch, reference):
    """A laptop, or a server with one model machine: no breaker, no probe, nothing kept; every call to the one
    server, as before."""
    monkeypatch.setenv(local_llm.SLOTS_ENV, str(tmp_path / "llm"))
    with FakeModel(models=[CHAT, READER]) as fake:
        settings = _settings(fake.base_url)
        assert local_llm.chat_failover(settings.llm) is None and page_reader.reader_failover(settings) is None
        assert local_llm.serving_llm(settings.llm) is settings.llm
        assert page_reader.reader_base_url(settings) == fake.base_url
        assert local_llm.failover(local_llm.CHAT, fake.base_url, fake.base_url + "/") is None  # itself: no backup
        coder = InvoiceCoder(settings, reference, provider="local")
        assert suggest_accounts(coder, "Acme Supplies", LINES)
        fake.set(down=True)
        for _ in range(3):
            with pytest.raises(CodingError):
                suggest_accounts(coder, "Acme Supplies", LINES)
        ours = [t.name for t in threading.enumerate() if t.name.startswith(("ap-coder-chat", "ap-coder-pages"))]
        assert ours == []  # no probe, no call watched
    assert not list((tmp_path / "llm").glob("failover-*"))  # (the slots' own lock files are there, as before)


def test_the_backup_addresses_are_read_from_the_env(monkeypatch, tmp_path):
    monkeypatch.setenv("AP_LLM_BASE_URL", "studio.local:1234")
    monkeypatch.setenv("AP_LLM_BACKUP_URL", "http://macmini.local:1234/v1/chat/completions")
    settings = Settings.from_env(tmp_path / "missing.env")
    assert settings.llm.backup_url == "http://macmini.local:1234/v1"
    assert page_reader.reader_servers(settings) == ("http://studio.local:1234/v1", "http://macmini.local:1234/v1")
    monkeypatch.setenv("AP_PAGE_READER_BASE_URL", "studio.local:8081")  # a page reader of its own: no backup...
    assert page_reader.reader_servers(Settings.from_env(tmp_path / "missing.env"))[1] == ""
    monkeypatch.setenv("AP_PAGE_READER_BACKUP_URL", "macmini.local:8081")  # ...unless it is given one
    assert page_reader.reader_servers(Settings.from_env(tmp_path / "missing.env")) == (
        "http://studio.local:8081/v1", "http://macmini.local:8081/v1")  # fmt: skip
    monkeypatch.setenv("AP_LLM_BACKUP_URL", " ")
    assert Settings.from_env(tmp_path / "missing.env").llm.backup_url == ""


# --- The breaker ------------------------------------------------------------------------------------------------------


def test_the_chat_calls_go_to_the_backup_once_the_primary_is_killed(two, reference):
    """Killed: the first call that finds it gone fails (the invoice's lines left for AP, as before), the second moves
    the calls and is answered by the backup at once; every call after goes there."""
    primary, backup = two
    coder = InvoiceCoder(_settings(primary.base_url, backup.base_url), reference, provider="local")
    assert suggest_accounts(coder, "Acme Supplies", LINES)
    primary.set(down=True)
    killed = time.monotonic()
    with pytest.raises(CodingError):
        suggest_accounts(coder, "Acme Supplies", LINES)
    assert suggest_accounts(coder, "Acme Supplies", LINES)  # moved, and asked again on the backup
    took = time.monotonic() - killed
    for _ in range(3):
        assert suggest_accounts(coder, "Acme Supplies", LINES)
    breaker = local_llm.chat_failover(coder.settings.llm)
    assert breaker.state()["on"] == BACKUP and "not answering" in breaker.state()["why"]
    assert _served(primary, "suggest") == 1 and _served(backup, "suggest") == 4 and took < 5, took


def test_a_call_waiting_on_a_hung_primary_is_asked_again_on_the_backup_at_once(two, reference):
    """Hung: a call under way (the AI reviewer's, minutes long) is dropped as soon as the calls leave the primary,
    another call's finding, and asked again on the backup: not after its own ten minutes."""
    primary, backup = two
    settings = _settings(primary.base_url, backup.base_url, timeout_seconds=600)
    coder = InvoiceCoder(settings, reference, provider="local")
    coder._prepare_local()
    primary.set(hang_rate=1.0)
    answer: dict = {}

    def ask() -> None:
        started = time.monotonic()
        answer["picks"] = suggest_accounts(coder, "Acme Supplies", LINES)
        answer["seconds"] = time.monotonic() - started

    asking = threading.Thread(target=ask)
    asking.start()
    assert _until(lambda: primary.stats()["hanging"] == 1)
    breaker = local_llm.chat_failover(settings.llm)
    for _ in range(local_llm.FAILOVER_FAILURES):  # two calls found it hung (another process's, say)
        breaker.failed(primary.base_url, "no answer in time")
    asking.join(10)
    primary.release_hung()
    assert answer.get("picks") and answer["seconds"] < 5, answer
    assert _served(backup, "suggest") == 1 and _served(primary, "suggest") == 0


def test_an_arrival_that_waited_for_its_chat_slot_is_no_failure_of_the_server(two, reference, monkeypatch):
    """B-1870: an arrival that spent most of its 20 s waiting for AP Coder's own chat slot (another call held it) was
    sent with what was left, and the healthy primary's usual answer time past it was counted as its failure: two
    such moved every call to the backup. Only the server's own time counts: an arrival that waited for its slot
    isn't counted when it gets no answer, and one left too little time isn't sent at all."""
    primary, backup = two
    primary.set(chat_latency=(0.6, 0.6))
    monkeypatch.setattr(local_llm, "WAITED_SECONDS", 0.1)  # (1 s and 5 s in the app, the arrival's 20 s scaled)
    monkeypatch.setattr(local_llm, "SEND_SECONDS", 0.25)
    coder = InvoiceCoder(_settings(primary.base_url, backup.base_url, parallel=1), reference, provider="local")
    model = coder._prepare_local()
    messages = [{"role": "system", "content": "x"}, {"role": "user", "content": "y"}]
    coder._local_create(model, messages, {"type": "object"})  # (its client made, its way of asking found)

    def arrival(within: float) -> str:  # behind a call that holds the one chat slot for 0.6 s
        holder = threading.Thread(target=lambda: coder._local_create(model, messages, {"type": "object"}))
        holder.start()
        time.sleep(0.05)
        try:
            coder._local_create(model, messages, {"type": "object"}, deadline=time.monotonic() + within)
            return "answered"
        except ChatTimeout as exc:
            return str(exc)
        finally:
            holder.join()

    sent = len(primary.history())
    assert "no answer in the time an arriving invoice waits" in arrival(0.9)  # sent with 0.35 s: no answer then
    assert len(primary.history()) == sent + 2
    assert "busy" in arrival(0.7)  # 0.15 s left once it had its slot: never sent
    assert len(primary.history()) == sent + 3
    breaker = local_llm.chat_failover(coder.settings.llm)
    assert breaker.state()["on"] == PRIMARY and breaker.state()["failures"][PRIMARY] == 0
    assert not backup.history()


def test_the_primary_is_taken_back_only_after_it_answered_well_for_a_while(two, quick):
    """Hysteresis: the primary, probed in the background, is taken back after three answers in a row and the hold
    (a second here); one that fails again soon after is kept away twice as long."""
    primary, backup = two
    breaker = local_llm.failover(local_llm.CHAT, primary.base_url, backup.base_url)
    breaker.answered(primary.base_url, CHAT)  # (the model the probe asks)
    primary.set(down=True)
    moved = time.monotonic()
    breaker.failed(primary.base_url, "not answering")
    assert breaker.serving() == primary.base_url  # one failure: not yet
    breaker.failed(primary.base_url, "not answering")
    assert breaker.serving() == backup.base_url
    time.sleep(0.5)
    assert breaker.serving() == backup.base_url and breaker.state()["good"] == 0  # its probes go unanswered
    primary.set(down=False)
    assert _until(lambda: breaker.serving() == primary.base_url, 10)
    back = time.monotonic()
    assert back - moved >= local_llm.HOLD_SECONDS
    probes = [h for h in primary.history() if h["kind"] == "chat" and h["outcome"] == "served"]
    assert len(probes) >= local_llm.PROBES_TO_RETURN and all(h["model"] == CHAT for h in probes)
    for _ in range(local_llm.FAILOVER_FAILURES):  # fails again at once: twice the hold
        breaker.failed(primary.base_url, "not answering")
    assert breaker.serving() == backup.base_url and breaker.state()["hold"] == 2 * local_llm.HOLD_SECONDS
    assert _until(lambda: breaker.serving() == primary.base_url, 10)


def test_with_both_servers_down_the_primary_is_taken_back_as_soon_as_it_answers(two, quick, monkeypatch, reference):
    """B-1872: both servers down, then the primary back: the calls stayed on the dead backup for the whole hold
    (60 s, up to 30 minutes when it flapped), since a backup that doesn't list its models was never counted as
    failing, and nothing else is asked of it then. Its model list not given counts on the backup too (the page
    reader's and the chat model's), and the primary is taken back once it answered its probes."""
    primary, backup = two
    monkeypatch.setattr(local_llm, "HOLD_SECONDS", 60.0)
    settings = _settings(primary.base_url, backup.base_url)
    assert page_reader.reader_status(settings).usable
    breaker = page_reader.reader_failover(settings)
    primary.set(down=True)
    backup.set(down=True)
    for _ in range(local_llm.FAILOVER_FAILURES):  # what ready() asks each round, a status 10 s old each time
        local_llm.forget_status()
        page_reader.reader_status(settings)
    assert breaker.state()["on"] == BACKUP and breaker.state()["failures"][BACKUP] == 1  # (the list just asked)
    local_llm.forget_status()
    with pytest.raises(CodingError):  # the chat model's coder asks the backup's list too
        InvoiceCoder(settings, reference, provider="local")._prepare_local()
    assert breaker.state()["failures"][BACKUP] == local_llm.FAILOVER_FAILURES
    primary.set(down=False)
    assert _until(lambda: breaker.serving() == primary.base_url, 5)


def test_the_breaker_is_shared_by_the_servers_processes(two, tmp_path, monkeypatch):
    """On the company server what one process finds moves every process's calls: its state is kept in the slots
    folder (AP_LLM_SLOTS_DIR, which ``serve --server`` sets for all its processes)."""
    primary, backup = two
    monkeypatch.setenv(local_llm.SLOTS_ENV, str(tmp_path / "llm"))
    code = ("import sys; from ap_coder.ai import local_llm\n"
            "b = local_llm.failover(local_llm.CHAT, sys.argv[1], sys.argv[2])\n"
            "for _ in range(local_llm.FAILOVER_FAILURES): b.failed(sys.argv[1], 'not answering')\n")  # fmt: skip
    here = local_llm.failover(local_llm.CHAT, primary.base_url, backup.base_url)
    assert here.serving() == primary.base_url
    started = time.time()
    subprocess.run([sys.executable, "-c", code, primary.base_url, backup.base_url], check=True, timeout=60,
                   cwd=PROJECT_DIR, env=dict(os.environ))  # fmt: skip
    assert _until(lambda: here.serving() == backup.base_url, 2 * local_llm.STATE_SECONDS + 1)
    assert here.left_since() >= started
    kept = json.loads(next((tmp_path / "llm").glob("failover-*.json")).read_text(encoding="utf-8"))
    assert kept["on"] == BACKUP and kept["moves"] == 1


def test_a_failure_of_a_server_already_left_or_a_refusal_moves_nothing(two):
    primary, backup = two
    breaker = local_llm.failover(local_llm.PAGES, primary.base_url, backup.base_url)
    breaker.failed(primary.base_url, "not answering")
    breaker.answered(primary.base_url)  # an answer in between: two in a row are needed
    breaker.failed(primary.base_url, "not answering")
    assert breaker.serving() == primary.base_url
    breaker.failed(backup.base_url, "not answering")  # the backup isn't in use: says nothing
    breaker.failed("http://127.0.0.1:9/v1", "not answering")  # nor another server
    assert breaker.state()["failures"] == {PRIMARY: 1, BACKUP: 0}

    class Refused(Exception):
        status_code = 404

    assert local_llm.server_failure(Refused()) == "" and local_llm.server_failure(ConnectionRefusedError()) != ""
    assert local_llm.server_failure(local_llm.ServerLeft("dropped by us")) == ""


# --- The page reader ------------------------------------------------------------------------------------------------


def test_a_page_read_on_a_hung_primary_waits_at_most_the_hang_limit_then_the_backup_reads_it(two, monkeypatch):
    """With a backup, a page read whose next word is HANG_SECONDS late on the primary is a hang (not its twenty
    minutes): the second one moves the calls, and that page is read on the backup at once."""
    primary, backup = two
    monkeypatch.setattr(local_llm, "HANG_SECONDS", 1.0)
    settings = _settings(primary.base_url, backup.base_url)
    primary.set(hang_rate=1.0)
    started = time.monotonic()
    with pytest.raises(page_reader.PageReaderError) as first:
        page_reader.transcribe(settings, b"page one", model=READER)
    assert first.value.failed and first.value.temporary and "no word from the model in 1 s" in str(first.value)
    text = page_reader.transcribe(settings, b"page two", model=READER)  # the second hang moves it
    took = time.monotonic() - started
    primary.release_hung()
    assert text and took < 10, took
    assert page_reader.reader_base_url(settings) == backup.base_url
    assert backup.page_answers() == {page_key(b"page two"): 1} and not primary.page_answers()
    # One machine with both models, one backup: one breaker. The chat model's calls moved with the pages'.
    assert local_llm.chat_failover(settings.llm) is page_reader.reader_failover(settings)
    assert local_llm.serving_llm(settings.llm).base_url == backup.base_url


def test_a_page_read_waiting_behind_another_of_ap_coders_calls_is_no_hang(reference, monkeypatch):
    """B-1871: a healthy primary, only busy (its one slot taken by a long chat call of AP Coder's: a review with
    thinking on takes minutes): two page reads that waited in the model server's own line longer than HANG_SECONDS
    before their first word were counted as hangs, and the calls left it, the review under way there dropped and
    asked again from the start on the backup. A call is no hang while another of this process's calls to that
    server has been under way since before it was sent; with none, a hang moves the calls as before."""
    monkeypatch.setattr(local_llm, "HANG_SECONDS", 0.5)
    with FakeModel(models=[CHAT, READER], slots=1, chat_latency=(2.0, 2.0), page_latency=(0.05, 0.05)) as primary, \
            FakeModel(models=[CHAT, READER]) as backup:  # fmt: skip
        settings = _settings(primary.base_url, backup.base_url, timeout_seconds=600)
        coder = InvoiceCoder(settings, reference, provider="local")
        model = coder._prepare_local()
        messages = [{"role": "system", "content": "x"}, {"role": "user", "content": "review"}]
        review = threading.Thread(target=lambda: coder._local_create(model, messages, {"type": "object"}))
        review.start()
        assert _until(lambda: primary.stats()["active"] == 1)
        for n in range(local_llm.FAILOVER_FAILURES):
            with pytest.raises(page_reader.PageReaderError, match="no word from the model"):
                page_reader.transcribe(settings, f"page {n}".encode(), model=READER)
        review.join(10)
        breaker = page_reader.reader_failover(settings)
        assert breaker.state()["on"] == PRIMARY and breaker.state()["failures"][PRIMARY] == 0
        assert _served(primary, "chat") == 1 and not backup.history()
        primary.set(hang_rate=1.0)  # hung, and nothing else of AP Coder's under way there: the second moves them
        with pytest.raises(page_reader.PageReaderError):
            page_reader.transcribe(settings, b"page 2", model=READER)
        assert page_reader.transcribe(settings, b"page 3", model=READER)
        primary.release_hung()
        assert breaker.state()["on"] == BACKUP and backup.page_answers() == {page_key(b"page 3"): 1}


def test_a_page_reader_on_a_server_of_its_own_has_a_breaker_of_its_own(two):
    primary, backup = two
    s = _settings(primary.base_url, backup.base_url)
    with FakeModel(models=[READER]) as reader, FakeModel(models=[READER]) as spare:
        settings = replace(s, page_reader=replace(s.page_reader, base_url=reader.base_url, backup_url=spare.base_url))
        pages, chat = page_reader.reader_failover(settings), local_llm.chat_failover(settings.llm)
        assert pages is not chat and (pages.primary, pages.backup) == (reader.base_url, spare.base_url)
        for _ in range(local_llm.FAILOVER_FAILURES):
            pages.failed(reader.base_url, "not answering")
        assert page_reader.reader_base_url(settings) == spare.base_url
        assert local_llm.serving_llm(settings.llm).base_url == primary.base_url


def test_a_primary_that_gives_no_model_list_moves_the_page_reader_to_its_backup(two):
    """Killed, the page reader's server doesn't list its models, so nothing is read there and no read fails: each
    list asked afresh that doesn't come is a failure too (a list kept 10 s is one), and the reader reads on the
    backup."""
    primary, backup = two
    settings = _settings(primary.base_url, backup.base_url)
    assert page_reader.reader_status(settings).model == READER
    primary.set(down=True)
    local_llm.forget_status()
    page_reader.reader_status(settings)
    page_reader.reader_status(settings)  # the same status, kept: no second failure
    assert page_reader.reader_base_url(settings) == primary.base_url
    local_llm.forget_status()
    status = page_reader.reader_status(settings)  # asked afresh: the second
    assert status.base_url == backup.base_url and status.model == READER and status.usable


def test_a_chat_model_on_a_server_of_its_own_moves_to_its_backup_when_its_model_list_doesnt_come(reference):
    """B-1869: the chat model's server killed, with the page reader on another machine: each review (a coder of its
    own) found no model in the dead server's list and stopped before any call was sent, so its breaker counted
    nothing, never moved, and the backup with the same model was never asked. A list asked afresh that doesn't come
    is a failure of the chat model's server too."""
    with FakeModel(models=[CHAT]) as chat, FakeModel(models=[CHAT]) as spare, FakeModel(models=[READER]) as pages:
        s = _settings(chat.base_url, spare.base_url)
        settings = replace(s, page_reader=replace(s.page_reader, base_url=pages.base_url))
        assert InvoiceCoder(settings, reference, provider="local")._prepare_local() == CHAT
        chat.set(down=True)
        local_llm.forget_status()
        with pytest.raises(CodingError):  # the first: the invoice's lines left for AP, as before
            suggest_accounts(InvoiceCoder(settings, reference, provider="local"), "Acme Supplies", LINES)
        with pytest.raises(CodingError):  # the same status, kept: no second failure
            suggest_accounts(InvoiceCoder(settings, reference, provider="local"), "Acme Supplies", LINES)
        local_llm.forget_status()  # (what DOWN_TTL_SECONDS does)
        assert suggest_accounts(InvoiceCoder(settings, reference, provider="local"), "Acme Supplies", LINES)
        assert local_llm.chat_failover(settings.llm).state()["on"] == BACKUP
        assert _served(spare, "suggest") == 1 and page_reader.reader_base_url(settings) == pages.base_url


@pytest.fixture
def store(tmp_path):
    store = Store(tmp_path / "ap.db")
    load_sample_setup(store)
    page_worker.save_test(store, {"model": READER, "ok": True, "fields_right": 9, "fields_total": 9})
    return store


def _off(primary: FakeModel, backup: FakeModel) -> Settings:
    """The page reader on ``primary`` with ``backup``, the chat model not asked."""
    s = _settings(primary.base_url, backup.base_url)
    return replace(s, llm=replace(s.llm, provider="off"))


def _invoice(store: Store, settings: Settings) -> int:
    result = InvoicePipeline(settings, store.reference_data(), store=store).process(SAMPLES / f"{STEM}.pdf")
    assert result.ok and store.page_read(result.invoice_id)["status"] == "waiting", result.error
    return int(result.invoice_id)


def test_a_read_in_flight_when_the_primary_dies_is_read_on_the_backup_and_stored_once(two, store):
    """The page read under way when the calls leave the primary (another reader found it hung) is dropped and read
    again on the backup at once: done, no try counted, its reading folded in once."""
    primary, backup = two
    settings = _off(primary, backup)
    iid = _invoice(store, settings)
    primary.set(hang_rate=1.0)
    outcome: dict = {}
    reading = threading.Thread(target=lambda: outcome.update(read=page_worker.read_one(settings, store)))
    reading.start()
    assert _until(lambda: primary.stats()["hanging"] >= 1, 30)
    breaker = page_reader.reader_failover(settings)
    for _ in range(local_llm.FAILOVER_FAILURES):
        breaker.failed(primary.base_url, "no answer in time")
    reading.join(30)
    primary.release_hung()
    read = outcome["read"]
    assert read is not None and read.status == "done", read
    assert store.page_read(iid)["status"] == "done" and not store.page_read(iid).get("tries")
    assert sum(backup.page_answers().values()) >= 1 and not primary.page_answers()
    assert page_worker.read_one(settings, store) is None  # nothing left in line: read once


def test_a_read_cut_off_by_a_move_goes_back_in_line_at_once_its_try_not_counted(two, store):
    iid = _invoice(store, _off(*two))
    row = store.next_page_read(reader="r")
    moved = page_worker._postpone_or_fail(store, row, READER, 0, 1.0, "the model server isn't answering", True)
    assert moved.status == "postponed" and moved.again and "backup" in moved.message
    assert store.page_read(iid)["status"] == "waiting" and not store.page_read(iid).get("tries")
    row = store.next_page_read(reader="r")
    counted = page_worker._postpone_or_fail(store, row, READER, 0, 1.0, "the model server isn't answering")
    assert not counted.again and store.page_read(iid)["tries"] == 1


# --- The arrival's chat call (D-168's breaker) ----------------------------------------------------------------------


def _text_invoices(store: Store, n: int) -> list:
    """``n`` text invoices (the samples' text copies, each made distinct), each with lines that ask the chat model."""
    folder = store.path.parent / "invoices"
    folder.mkdir(exist_ok=True)
    texts = sorted(p for p in SAMPLES.glob("*.md") if p.name != "README.md")
    read = []
    for k in range(n):
        path = folder / f"{k:02d}-{texts[k % len(texts)].name}"
        path.write_text(texts[k % len(texts)].read_text(encoding="utf-8") + f"\n\nCopy {k}\n", encoding="utf-8")
        read.append(read_file(path))
    return read


def test_arrivals_ask_the_backup_once_the_primary_hangs_not_nobody(two, tmp_path, monkeypatch):
    """D-168's breaker stops asking after two arrivals in a row went unanswered: with a backup those two move the
    calls instead, and every arrival after asks the backup, which answers."""
    primary, backup = two
    monkeypatch.setenv("AP_PRIVATE_DIR", str(tmp_path / "private"))
    monkeypatch.setenv(pipeline.ARRIVAL_ENV, "2")
    store = Store(tmp_path / "private" / "ap_coder.db")
    load_sample_setup(store)
    store.queue_imports(_text_invoices(store, 6), source="upload", person=True)
    primary.set(hang_rate=1.0)
    read = Reader(store, _settings(primary.base_url, backup.base_url, timeout_seconds=600), "r").read_all()
    primary.release_hung()
    assert read == 6 and len(store.list_invoices()) == 6
    assert primary.stats()["hung"] == local_llm.FAILOVER_FAILURES and _served(backup, "suggest") == 4


# --- A swapped chat model (item 5) ----------------------------------------------------------------------------------


def _truth_header() -> dict:
    truth = json.loads(page_reader.TEST_TRUTH.read_text(encoding="utf-8"))
    fields = ("vendor_name", "invoice_number", "invoice_date", "due_date", "po_number", "gst_hst_registration_number",
              "subtotal", "tax_total", "grand_total")  # fmt: skip
    return {k: truth.get(k) for k in fields}


def test_a_swapped_chat_model_decides_nothing_touchless_until_it_passes_its_self_test(store):
    """The chat model AP Coder first ran with is trusted; one LM Studio swapped in isn't, until it read the page
    reader's test invoice right: the AI reviewer waits meanwhile, and the person sees why."""
    vision = [{"id": CHAT, "vision": True}, {"id": SWAPPED, "vision": True}, READER]
    with FakeModel(models=[vision[0], READER]) as fake:
        settings = _settings(fake.base_url)
        assert page_worker.chat_untrusted(settings, store) == ""  # the first: qualified by its use
        assert page_worker.chat_test_if_due(settings, store) is None
        fake.set(models=[vision[1], READER])
        local_llm.forget_status()
        why = page_worker.chat_untrusted(settings, store)
        assert SWAPPED in why and CHAT in why and "self-test" in why
        verifier.save_settings(store, on=True, rounds=1, minutes=1.0)
        taken = []
        store.next_verification = lambda **kw: taken.append(kw)  # nothing is taken from its line
        assert verifier.verify_one(settings, store) is None and not taken
        failed = page_worker.chat_test_if_due(settings, store)  # the fake reads nothing on the test pages
        assert failed is not None and not failed["ok"] and failed["model"] == SWAPPED
        assert "failed its self-test" in page_worker.chat_untrusted(settings, store)
        assert page_worker.chat_test_if_due(settings, store) is None  # not again at once
        fake.set(headers={page_key(png): _truth_header() for png in page_reader.test_pages(page_reader.OVIS)})
        tests = page_worker.chat_tests(store)
        tests["models"].pop(SWAPPED)
        store.set_setting(page_worker.CHAT_TEST_KEY, json.dumps(tests))
        passed = page_worker.chat_test_if_due(settings, store)
        assert passed["ok"], passed
        assert page_worker.chat_untrusted(settings, store) == ""
    assert "chat_model_tested" in [e["action"] for e in store.events()]


def test_the_first_chat_model_is_the_one_ap_coder_was_qualified_with(store):
    assert page_worker.chat_trust(store, "") == ""  # no chat model: nothing it decides
    assert page_worker.chat_trust(store, CHAT) == ""  # the first one asked here
    assert page_worker.chat_trust(store, SWAPPED) and page_worker.chat_trust(store, CHAT) == ""
    assert page_worker.chat_tests(store)["first"] == CHAT


def _read_touchless(store: Store, settings: Settings, monkeypatch) -> page_worker.ReadOutcome:
    """The page reader's pass over a new invoice touchless processing passes (its gates taken as passed), the AI
    reviewer off: what the pass decides."""
    from ap_coder.capture.supplier import AUTONOMOUS

    _invoice(store, replace(settings, llm=replace(settings.llm, provider="off")))
    verifier.save_settings(store, on=False, rounds=1, minutes=1.0)
    monkeypatch.setattr(pipeline, "autonomy_decision", lambda *a, **k: {
        "state": AUTONOMOUS, "auto": True, "audit": False, "reason": "every header field verified"})  # fmt: skip
    text = (SAMPLES / f"{STEM}.md").read_text(encoding="utf-8")
    status = page_reader.ReaderStatus(reachable=True, lm_studio=True, model=READER, document_reader=True,
                                      state="loaded", candidates=[READER])  # fmt: skip
    monkeypatch.setattr(page_reader, "reader_status", lambda settings, use_cache=True: status)
    monkeypatch.setattr(page_reader, "load_reader", lambda settings, model=None: "")
    monkeypatch.setattr(page_reader, "read_document", lambda settings, path, **kw: page_reader.PageReading(
        model=READER, pages=[text], seconds=[1.0], page_count=1))  # fmt: skip
    outcome = page_worker.read_one(settings, store)
    assert outcome is not None and outcome.status == "done" and outcome.updated, outcome
    return outcome


def test_a_chat_model_that_cant_see_pages_is_not_tested_nor_kept_from_touchless(store, monkeypatch):
    """B-1875: the chat model swapped for one that can't see pages (a laptop too) could never pass the self-test,
    which reads pages; so after every reading nothing was approved without a person for as long as it was loaded,
    and the test ran again, audited, every hour. Such a model reads no header and its accounts always go to a
    person: it decides nothing touchless, so it is neither gated nor tested."""
    text_only = {"id": "qwen/qwen3-8b", "type": "llm", "vision": False}
    with FakeModel(models=[{"id": CHAT, "vision": True}, READER]) as fake:
        settings = _settings(fake.base_url)
        assert page_worker.chat_untrusted(settings, store) == ""  # the first: the one it was qualified with
        fake.set(models=[text_only, READER])
        local_llm.forget_status()
        assert page_worker.chat_untrusted(settings, store) == ""
        assert page_worker.chat_test_if_due(settings, store) is None
        assert page_worker.chat_tests(store)["first"] == CHAT
        assert _read_touchless(store, settings, monkeypatch).auto_approved
    assert "chat_model_tested" not in [e["action"] for e in store.events()]


# --- The doctor (item 4) --------------------------------------------------------------------------------------------


def test_the_doctor_says_which_model_server_answers_and_how(two):
    """Each server, primary and backup, asked a one-word question and timed; which one the calls go to; where the
    breaker sends them. Without a backup, no such rows (as before)."""
    from ap_coder.ops import doctor

    primary, backup = two
    settings = _settings(primary.base_url, backup.base_url)
    rows = doctor._server_checks(settings, "local")
    areas = [area for area, _status, _detail in rows]
    servers = [f"{kind} server ({role})" for kind in ("chat model", "page reader") for role in ("primary", "backup")]
    assert areas == [*servers, "model server failover"], areas  # (one machine, one backup: one breaker)
    assert all(status == doctor.PASS for _area, status, _detail in rows)
    assert f"{primary.base_url}: {CHAT} answered in" in rows[0][2] and "the calls go to it now" in rows[0][2]
    assert f"{backup.base_url}: {READER} answered in" in rows[3][2] and "the calls go to it now" not in rows[3][2]
    backup.set(down=True)
    rows = doctor._server_checks(settings, "local")
    assert rows[1][1] == doctor.WARN and "answered in" not in rows[1][2]  # (how it failed, in its words)
    assert doctor._server_checks(_settings(primary.base_url), "local") == []


def test_the_doctor_from_a_console_on_the_server_sees_its_breakers_and_server_json(two, tmp_path, monkeypatch):
    """B-1874: ``python -m ap_coder doctor`` from IT's console on the company server had a breaker of its own, new
    and in memory (only ``serve --server`` sets AP_LLM_SLOTS_DIR), so it said the calls go to the primary while the
    server's processes had moved them; and a backup given only in server.json wasn't seen at all. It reads the
    server's shared state and its server.json as the dashboard does."""
    import argparse

    from ap_coder.ops import cli, doctor, service

    primary, backup = two
    data = tmp_path / "private"  # (the data folder, AP_PRIVATE_DIR)
    data.mkdir(exist_ok=True)
    service.write_config(data, llm_backup_url=backup.base_url)  # the console's .env names no backup
    monkeypatch.setenv(local_llm.SLOTS_ENV, "")
    monkeypatch.delenv(local_llm.SLOTS_ENV)  # the console: not set (and none left behind)
    code = ("import sys; from ap_coder.ai import local_llm\n"
            "b = local_llm.failover(local_llm.CHAT, sys.argv[1], sys.argv[2])\n"
            "for _ in range(local_llm.FAILOVER_FAILURES): b.failed(sys.argv[1], 'not answering')\n")  # fmt: skip
    subprocess.run([sys.executable, "-c", code, primary.base_url, backup.base_url], check=True, timeout=60,
                   cwd=PROJECT_DIR, env={**os.environ, local_llm.SLOTS_ENV: str(data / "llm")})  # fmt: skip
    seen: dict = {}

    def checks(settings, *args, **kwargs):  # the doctor's model server rows, with the settings it runs with
        rows = doctor._server_checks(settings, "local")
        seen["rows"] = {area: (status, detail) for area, status, detail in rows}
        return [doctor.Check(*row) for row in rows] or [doctor.Check("model servers", doctor.SKIP, "no backup")]

    monkeypatch.setattr(cli, "run_checks", checks)
    monkeypatch.setattr(doctor, "store_checks", lambda store, server: [])
    cli.cmd_doctor(argparse.Namespace(online=False, coa=None, db=str(tmp_path / "ap.db")), _settings(primary.base_url))
    rows = seen["rows"]
    assert "the calls go to it now" in rows["chat model server (backup)"][1]
    assert "the calls go to it now" not in rows["chat model server (primary)"][1]
    assert rows["model server failover"][0] == doctor.WARN
    assert "The calls go to the backup " + backup.base_url in rows["model server failover"][1]


def test_a_review_asked_of_a_chat_model_lm_studio_no_longer_has_waits_in_line_not_unsure(store):
    """B-1844: a review asked of the chat model LM Studio had a moment ago (swapped since, its status kept 30 s)
    got a 404 on its first question and ended unsure, its invoice sent to a person: a model the server no longer
    has is the server's change, not the reviewer's verdict. The review waits in line, and the next asks the new
    model."""
    with FakeModel(models=[{"id": CHAT, "vision": True}, READER]) as fake:
        settings = _settings(fake.base_url)
        iid = _invoice(store, replace(settings, llm=replace(settings.llm, provider="off")))
        verifier.save_settings(store, on=True, rounds=1, minutes=1.0)
        store.queue_verification(iid, verifier.QUEUED[0])
        assert page_worker.chat_untrusted(settings, store) == ""  # (its status kept: CHAT, the first)
        fake.set(models=[{"id": SWAPPED, "vision": True}, READER])
        outcome = verifier.verify_one(settings, store, after_page_reader=False)
        assert outcome is not None and outcome.status == "postponed", outcome
        assert store.verification(iid)["status"] == "waiting"
        assert local_llm.check_server(settings.llm).model == SWAPPED  # asked again at once


def test_a_reader_thread_pausing_after_a_failed_read_wakes_when_the_calls_move(two, store):
    """The first read that found the primary gone waits its minute; once the calls move it reads on at once."""
    primary, backup = two
    settings = _settings(primary.base_url, backup.base_url)
    worker = page_worker.BackgroundReader(lambda: settings, lambda: store)
    woke: list[float] = []
    started = time.monotonic()
    pausing = threading.Thread(target=lambda: (worker._pause(page_worker.OFF_SECONDS, settings),
                                               woke.append(time.monotonic() - started)))  # fmt: skip
    pausing.start()
    time.sleep(0.3)
    breaker = page_reader.reader_failover(settings)
    for _ in range(local_llm.FAILOVER_FAILURES):
        breaker.failed(primary.base_url, "not answering")
    pausing.join(5)
    assert woke and woke[0] < 3, woke
