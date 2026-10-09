"""A local model through LM Studio (or Ollama): detection, model picking, structured-output fallback, loose JSON.

A tiny OpenAI-compatible server runs in a thread on 127.0.0.1 (no real network, no real model).
"""

import json
import tempfile
import threading
import time
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from ap_coder import local_llm
from ap_coder.config import LocalLLMSettings, OpenAISettings, Settings, normalise_base_url
from ap_coder.extraction import result_from_text
from ap_coder.inference import CodingError, InvoiceCoder
from ap_coder.local_llm import (
    check_server,
    clip_document,
    parse_json_reply,
    pick_model,
    provider_status,
    resolve_provider,
)

from .conftest import FakeOpenAI, make_completion
from .test_dashboard_pages import _ok, _page, db  # noqa: F401 - db: a fresh dashboard database (fixture)

REAL_FETCH = local_llm._fetch_json  # conftest replaces it for every other test


class FakeLMStudio:
    """``/v1/models``, LM Studio's ``/api/v0/models`` (when ``v0`` is given) and ``/v1/chat/completions``.

    ``reply(body)`` returns ``(status, payload)`` for each chat request; every request body is kept in ``chats``."""

    def __init__(self, models, v0=None, reply=None):
        self.models, self.v0, self.reply, self.chats = models, v0, reply, []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, status, payload):
                body = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path == "/v1/models":
                    self._send(200, {"object": "list", "data": [{"id": m, "object": "model"} for m in fake.models]})
                elif self.path == "/api/v0/models" and fake.v0 is not None:
                    self._send(200, {"object": "list", "data": fake.v0})
                else:
                    self._send(404, {"error": "not found"})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                fake.chats.append(body)
                self._send(*fake.reply(body))

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}/v1"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def real_fetch(monkeypatch):
    monkeypatch.setattr(local_llm, "_fetch_json", REAL_FETCH)


@pytest.fixture
def lm_studio(real_fetch):
    servers = []

    def start(models, v0=None, reply=None):
        servers.append(FakeLMStudio(models, v0, reply))
        return servers[-1]

    yield start
    for server in servers:
        server.close()


def chat_reply(content, finish="stop", model="qwen2.5-7b-instruct"):
    return 200, {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "created": 0,
        "model": model,
        "choices": [{"index": 0, "finish_reason": finish, "message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 900, "completion_tokens": 300, "total_tokens": 1200},
    }


def local_settings(base_url, **llm):
    return Settings(llm=LocalLLMSettings(provider="local", base_url=base_url, **llm))


V0_LOADED = [
    {"id": "text-embedding-nomic-embed-text-v1.5", "type": "embeddings", "state": "loaded"},
    {"id": "qwen2.5-vl-7b-instruct", "type": "vlm", "state": "not-loaded"},
    {"id": "qwen2.5-7b-instruct", "type": "llm", "state": "loaded"},
]


# --- Settings -------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("", "http://127.0.0.1:1234/v1"),
        ("http://127.0.0.1:1234/v1/", "http://127.0.0.1:1234/v1"),
        ("http://127.0.0.1:1234/v1/chat/completions", "http://127.0.0.1:1234/v1"),
        ("http://localhost:1234/api/v0/models", "http://localhost:1234/v1"),
        ("127.0.0.1:1234", "http://127.0.0.1:1234/v1"),
        ("192.168.1.20:1234", "http://192.168.1.20:1234/v1"),
        ("http://127.0.0.1:11434", "http://127.0.0.1:11434/v1"),
        ("http://127.0.0.1:11434/api/chat", "http://127.0.0.1:11434/v1"),
        ('"http://127.0.0.1:1234/v1/models"', "http://127.0.0.1:1234/v1"),
        ("https://openrouter.ai/api/v1", "https://openrouter.ai/api/v1"),  # a hosted gateway keeps its path
    ],
)
def test_base_url_is_normalised(given, expected):
    assert normalise_base_url(given) == expected


def test_settings_from_env(monkeypatch, tmp_path):
    for name, value in {
        "AP_LLM_PROVIDER": "LOCAL",
        "AP_LLM_BASE_URL": "http://127.0.0.1:1234/v1/chat/completions",
        "AP_LLM_MODEL": "auto",
        "AP_LLM_VISION": "false",
        "AP_LLM_MAX_PROMPT_CHARS": "8000",
    }.items():
        monkeypatch.setenv(name, value)
    llm = Settings.from_env(tmp_path / "missing.env").llm
    assert (llm.provider, llm.base_url, llm.model, llm.vision, llm.max_prompt_chars) == (
        "local",
        "http://127.0.0.1:1234/v1",
        "",
        "off",
        8000,
    )
    assert llm.api_key == "lm-studio"
    monkeypatch.setenv("AP_LLM_PROVIDER", "something-else")
    assert Settings.from_env(tmp_path / "missing.env").llm.provider == "auto"


# --- Detection ------------------------------------------------------------------------------------------------


def test_detects_lm_studio_and_its_loaded_model(lm_studio):
    server = lm_studio(
        ["text-embedding-nomic-embed-text-v1.5", "qwen2.5-vl-7b-instruct", "qwen2.5-7b-instruct"], V0_LOADED
    )
    status = check_server(LocalLLMSettings(base_url=server.base_url))
    assert status.active and status.lm_studio
    assert status.model == "qwen2.5-7b-instruct"  # the loaded one, not the first listed
    assert status.vision is False
    assert "text-embedding-nomic-embed-text-v1.5" not in status.chat_models
    assert status.describe() == "Connected to LM Studio · qwen2.5-7b-instruct · reads text only"


def test_lm_studio_vision_model_and_nothing_loaded(lm_studio):
    v0 = [{"id": "qwen2.5-vl-7b-instruct", "type": "vlm", "state": "loaded"}]
    server = lm_studio(["qwen2.5-vl-7b-instruct"], v0)
    assert check_server(LocalLLMSettings(base_url=server.base_url)).vision is True

    idle = lm_studio(["qwen2.5-7b-instruct", "llama-3.2-3b-instruct"], [{**V0_LOADED[2], "state": "not-loaded"}])
    status = check_server(LocalLLMSettings(base_url=idle.base_url))
    assert status.reachable and not status.active  # never makes LM Studio load the first model on its list
    assert "no model is loaded" in status.describe()


def test_detection_when_nothing_answers_is_quick_and_cached(real_fetch, monkeypatch):
    llm = LocalLLMSettings(base_url="http://127.0.0.1:9/v1")  # nothing listens on port 9
    started = time.monotonic()
    status = check_server(llm)
    assert not status.reachable and not status.active and status.error
    assert time.monotonic() - started < 2
    calls = []
    monkeypatch.setattr(local_llm, "_fetch_json", lambda *a: calls.append(a))
    assert check_server(llm) is status and not calls  # cached: the page does not ask again on every rerun


@pytest.mark.parametrize(
    ("requested", "ids", "loaded", "lm", "expected"),
    [
        ("my-model", ["a", "b"], [], False, "my-model"),  # AP_LLM_MODEL wins
        ("", ["nomic-embed-text", "llama3.1:8b"], [], False, "llama3.1:8b"),  # Ollama: no embedding model
        ("", ["a", "b"], ["text-embedding-x", "b"], True, "b"),
        ("", ["a", "b"], [], True, ""),  # LM Studio with nothing loaded: none
        ("", [], [], False, ""),
    ],
)
def test_pick_model(requested, ids, loaded, lm, expected):
    assert pick_model(requested, ids, loaded, lm_studio=lm) == expected


def test_other_servers_guess_vision_from_the_name(lm_studio):
    server = lm_studio(["gemma3:4b"])  # Ollama-style: no LM Studio route
    status = check_server(LocalLLMSettings(base_url=server.base_url))
    assert status.model == "gemma3:4b" and status.vision and not status.lm_studio


# --- Which provider -------------------------------------------------------------------------------------------


def test_provider_auto(lm_studio):
    azure = Settings(openai=OpenAISettings(endpoint="https://x.openai.azure.com/"))
    assert resolve_provider(azure) == "azure"
    assert resolve_provider(Settings(llm=LocalLLMSettings(base_url="http://127.0.0.1:9/v1"))) == "off"
    server = lm_studio(["qwen2.5-7b-instruct"], V0_LOADED)
    local = Settings(llm=LocalLLMSettings(base_url=server.base_url))
    assert resolve_provider(local) == "local"
    assert provider_status(local).label == "LM Studio · qwen2.5-7b-instruct"
    assert resolve_provider(replace(local, llm=replace(local.llm, provider="off"))) == "off"
    assert resolve_provider(replace(azure, llm=LocalLLMSettings(provider="local"))) == "local"


def test_no_model_anywhere(reference, sample_markdown_path):
    settings = Settings()  # auto, no Azure, nothing answering (conftest)
    assert resolve_provider(settings) == "off"
    status = provider_status(settings)
    assert not status.ready and "Not running" in status.detail
    coder = InvoiceCoder(settings, reference)  # building it never waits on a server
    assert coder.wants_images() is False
    with pytest.raises(CodingError, match="No AI model is set up"):
        coder.code(result_from_text(sample_markdown_path))


def test_pipeline_without_a_model_records_a_readable_failure(reference, sample_markdown_path):
    from ap_coder.pipeline import InvoicePipeline

    result = InvoicePipeline(Settings(), reference).process(sample_markdown_path)
    assert not result.ok and "No AI model is set up" in result.error
    assert result.extraction is not None  # the text was still read


# --- Reading a small model's reply ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "reply",
    [
        '{"a": 1, "b": {"c": [1, 2]}}',
        '```json\n{"a": 1, "b": {"c": [1, 2]}}\n```',
        '<think>The total is {maybe} 10.</think>\n{"a": 1, "b": {"c": [1, 2]}}',
        'reasoning without an opening tag</think>```json\n{"a": 1, "b": {"c": [1, 2],},}\n```',
        'Here is the coding:\n{"a": 1, "b": {"c": [1, 2]}}\nLet me know if you need anything else. {"x": 2}',
    ],
)
def test_parse_json_reply(reply):
    assert parse_json_reply(reply) == {"a": 1, "b": {"c": [1, 2]}}


def test_parse_json_reply_nothing_there():
    assert parse_json_reply("Sorry, I can't read this invoice.") is None
    assert parse_json_reply('{"cut off": ') is None


def test_clip_document_keeps_start_and_end():
    text = "\n".join(["Vendor: Northwind"] + [f"line {i}" for i in range(5000)] + ["TOTAL DUE 1,234.00"])
    clipped, cut = clip_document(text, 2000)
    assert cut and len(clipped) <= 2000
    assert clipped.startswith("Vendor: Northwind") and clipped.endswith("TOTAL DUE 1,234.00")
    assert "left out to fit the model" in clipped
    assert clip_document("short", 2000) == ("short", False)


# --- Coding with the local model ------------------------------------------------------------------------------


def test_codes_with_lm_studio_json_schema(lm_studio, reference, ground_truth, sample_markdown_path):
    server = lm_studio(["qwen2.5-7b-instruct"], V0_LOADED, reply=lambda body: chat_reply(json.dumps(ground_truth)))
    result = InvoiceCoder(local_settings(server.base_url), reference).code(result_from_text(sample_markdown_path))
    assert result.coding.invoice_number == "NW-2026-0912"
    assert result.attempts == 1 and result.usage["total_tokens"] == 1200
    sent = server.chats[0]
    assert sent["model"] == "qwen2.5-7b-instruct"
    assert sent["response_format"]["type"] == "json_schema"
    assert sent["max_tokens"] == 4096 and "max_completion_tokens" not in sent
    assert sent["reasoning_effort"] == "none"  # never Azure's effort: only the switch that turns thinking off


def test_json_schema_rejected_falls_back(lm_studio, reference, ground_truth, sample_markdown_path):
    def reply(body):
        kind = (body.get("response_format") or {}).get("type")
        if kind in {"json_schema", "json_object"}:
            return 400, {"error": {"message": f"'response_format.type' {kind} is not supported"}}
        return chat_reply("<think>coding it</think>\n```json\n" + json.dumps(ground_truth) + "\n```")

    server = lm_studio(["llama-3.2-3b-instruct"], reply=reply)
    settings = local_settings(server.base_url)
    result = InvoiceCoder(settings, reference).code(result_from_text(sample_markdown_path))
    assert result.coding.vendor_name == ground_truth["vendor_name"]
    assert [(c.get("response_format") or {}).get("type") for c in server.chats] == ["json_schema", "json_object", None]
    assert "## Reply format" in server.chats[-1]["messages"][0]["content"]  # the schema is in the prompt instead
    # The next invoice starts with what worked.
    InvoiceCoder(settings, reference).code(result_from_text(sample_markdown_path))
    assert "response_format" not in server.chats[-1]


def test_invalid_json_gets_one_repair_turn(reference, ground_truth, sample_markdown_path):
    settings = local_settings("http://127.0.0.1:1234/v1", model="qwen2.5-7b-instruct")
    client = FakeOpenAI(make_completion("I think the vendor is Northwind."), make_completion(json.dumps(ground_truth)))
    result = InvoiceCoder(settings, reference, client=client).code(result_from_text(sample_markdown_path))
    assert result.attempts == 2
    assert "failed validation" in client.calls[1]["messages"][-1]["content"]

    client = FakeOpenAI(make_completion("no"), make_completion("still no"))
    with pytest.raises(CodingError, match="failed validation after 2 call"):
        InvoiceCoder(settings, reference, client=client).code(result_from_text(sample_markdown_path))


def test_long_document_is_clipped_for_a_small_context(reference, ground_truth, tmp_path):
    long_invoice = tmp_path / "long.md"
    long_invoice.write_text("Vendor: Northwind\n" + "item line\n" * 20_000 + "TOTAL 99.00\n", encoding="utf-8")
    settings = local_settings("http://127.0.0.1:1234/v1", model="m", max_prompt_chars=3000)
    client = FakeOpenAI(make_completion(json.dumps(ground_truth)))
    InvoiceCoder(settings, reference, client=client).code(result_from_text(long_invoice))
    user = client.calls[0]["messages"][1]["content"]
    assert "TOTAL 99.00" in user and "Vendor: Northwind" in user and len(user) < 5000


def test_server_down_while_coding(real_fetch, reference, sample_markdown_path):
    settings = local_settings("http://127.0.0.1:9/v1", model="qwen2.5-7b-instruct", timeout_seconds=2)
    # A closed port is refused at once on Linux and Mac; Windows retries the connection until the timeout.
    with pytest.raises(CodingError, match="isn't answering|took longer than"):
        InvoiceCoder(settings, reference, max_repair_attempts=0).code(result_from_text(sample_markdown_path))


def test_lm_studio_running_without_a_model(lm_studio, reference, sample_markdown_path):
    server = lm_studio(["qwen2.5-7b-instruct"], [{**V0_LOADED[2], "state": "not-loaded"}])
    with pytest.raises(CodingError, match="no model is loaded"):
        InvoiceCoder(local_settings(server.base_url), reference).code(result_from_text(sample_markdown_path))


def test_vision_follows_the_loaded_model(lm_studio, reference):
    v0 = [{"id": "qwen2.5-vl-7b-instruct", "type": "vlm", "state": "loaded"}]
    server = lm_studio(["qwen2.5-vl-7b-instruct"], v0)
    assert InvoiceCoder(local_settings(server.base_url), reference).wants_images() is True
    assert InvoiceCoder(local_settings(server.base_url, vision="off"), reference).wants_images() is False


# --- Doctor ---------------------------------------------------------------------------------------------------


def test_doctor_reports_the_local_model(lm_studio, reference):
    from ap_coder.doctor import PASS, SKIP, run_checks

    server = lm_studio(["qwen2.5-7b-instruct"], V0_LOADED)
    checks = {
        c.area: c for c in run_checks(Settings(llm=LocalLLMSettings(base_url=server.base_url)), lambda: reference)
    }
    assert checks["AI model"].status == PASS and "local" in checks["AI model"].detail
    assert checks["local model"].status == PASS and "qwen2.5-7b-instruct" in checks["local model"].detail
    assert checks["AOAI endpoint"].status == SKIP


def test_doctor_local_dry_run(lm_studio, reference, ground_truth):
    from ap_coder.doctor import PASS, run_checks

    # The dry run asks what the pipeline asks a local model: the accounts of two lines.
    picks = {"lines": [{"line_number": 1, "gl_code": "6000", "cost_center": "", "reason": "supplies"},
                       {"line_number": 2, "gl_code": "6800", "cost_center": "", "reason": "courier"}]}  # fmt: skip
    server = lm_studio(["qwen2.5-7b-instruct"], V0_LOADED, reply=lambda body: chat_reply(json.dumps(picks)))
    checks = {c.area: c for c in run_checks(local_settings(server.base_url), lambda: reference, online=True)}
    assert checks["local dry run"].status == PASS and "line 1 -> 6000" in checks["local dry run"].detail


# --- Settings page --------------------------------------------------------------------------------------------


def test_settings_page_when_lm_studio_is_not_running(db, monkeypatch):  # noqa: F811
    monkeypatch.setenv("AP_ENV_FILE", str(db.parent / ".env"))
    at = _ok(_page("settings", "page_settings").run())
    page = " ".join(m.value for m in at.markdown)
    assert "Install LM Studio" in page and "Start server" in page
    assert any(b.label == "Test connection" for b in at.button)
    _ok(at.button(key="test_llm").click().run())


def test_settings_page_lists_models_and_saves(db, lm_studio, monkeypatch):  # noqa: F811
    from ap_coder.envfile import read_env

    server = lm_studio(["qwen2.5-7b-instruct", "llama-3.2-3b-instruct", "text-embedding-nomic"], V0_LOADED)
    env = db.parent / ".env"
    monkeypatch.setenv("AP_ENV_FILE", str(env))
    for key in ("AP_LLM_PROVIDER", "AP_LLM_MODEL", "AP_LLM_VISION"):
        monkeypatch.setenv(key, "")  # restored after the test (the page writes os.environ)
    monkeypatch.setenv("AP_LLM_BASE_URL", server.base_url)
    at = _ok(_page("settings", "page_settings").run())
    assert "Install LM Studio" not in " ".join(m.value for m in at.markdown)
    model = at.selectbox(key="llm_model")
    assert model.options[1:] == ["qwen2.5-7b-instruct", "llama-3.2-3b-instruct"]  # no embedding model
    model.select("llama-3.2-3b-instruct")
    at.selectbox(key="llm_provider").select("local")
    submit = next(b for b in at.button if b.proto.is_form_submitter and b.proto.form_id.endswith("ai_model_form"))
    _ok(submit.click().run())
    saved = read_env(env)
    assert saved["AP_LLM_MODEL"] == "llama-3.2-3b-instruct" and saved["AP_LLM_PROVIDER"] == "local"
    assert "AP_LLM_VISION" not in saved and "AP_LLM_BASE_URL" not in saved  # unchanged values are not written


def test_with_a_local_model_the_reader_reads_and_the_model_codes_the_leftover_lines(lm_studio, reference):
    """A small model misreads totals and provinces; the local reader does not. So the invoice is read by the
    reader, and the model is only asked for the accounts the approval memory could not give."""
    from ap_coder.pipeline import InvoicePipeline
    from ap_coder.store import Store

    from .conftest import SAMPLES

    def reply(body):
        user = body["messages"][-1]["content"]
        numbers = [int(line.split(".")[0]) for line in user.splitlines() if line[:1].isdigit()]
        picks = [{"line_number": n, "gl_code": "6400", "cost_center": "", "reason": "marketing"} for n in numbers]
        picks.append({"line_number": 99, "gl_code": "6400", "cost_center": "", "reason": "not a line"})
        picks.append({"line_number": numbers[0], "gl_code": "NOT-A-CODE", "cost_center": "", "reason": "x"})
        return chat_reply(json.dumps({"lines": picks}))

    server = lm_studio(["qwen2.5-7b-instruct"], V0_LOADED, reply=reply)
    with tempfile.TemporaryDirectory() as tmp:
        pipe = InvoicePipeline(local_settings(server.base_url), reference, store=Store(Path(tmp) / "ap.db"))
        result = pipe.process(SAMPLES / "montroyal_QC_TPS_TVQ_ACMR-2026-1187.pdf")
    assert result.ok, result.error
    coding = result.coding.coding
    assert coding.ship_to_province == "QC" and abs(coding.grand_total - 21578.51) < 0.01  # the reader's reading
    assert result.coding.model == "local reader + qwen2.5-7b-instruct"
    by_model = [li for li in coding.line_items if li.reasoning_justification.startswith("Suggested by the local model")]
    assert by_model and {li.predicted_gl_code for li in by_model} == {"6400"}
    # Lines the chart's names already matched keep that account: the model only fills what nothing coded.
    assert all(li.predicted_gl_code != "UNASSIGNED" for li in coding.line_items)
    sent = server.chats[-1]
    assert "Lines (number. description | amount)" in sent["messages"][-1]["content"]
    assert len(json.dumps(sent)) < 20000  # a short call: lines and the chart, not the whole invoice


def test_a_province_as_a_model_writes_it():
    from ap_coder.schema import province_code

    assert province_code("Vancouver, BC") == "BC" and province_code("British Columbia") == "BC"
    assert province_code("Québec") == "QC" and province_code("Seattle, USA") == "OUTSIDE_CANADA"
    with pytest.raises(ValueError):
        province_code("Narnia")


# --- Thinking models (Qwen 3.5 in LM Studio) --------------------------------------------------------------------
# LM Studio may ignore chat_template_kwargs (its bug tracker #1990): the model thinks anyway, the reasoning comes
# back in reasoning_content, and with a token limit the answer can be empty.

QWEN35_V0 = [{"id": "qwen3.5-9b", "type": "vlm", "state": "loaded"}]
TWO_LINES = [(1, "Printer paper, letter size", 420.0), (2, "Courier delivery", 35.0)]
PICKS = {"lines": [{"line_number": 1, "gl_code": "6000", "cost_center": "", "reason": "supplies"},
                   {"line_number": 2, "gl_code": "6800", "cost_center": "", "reason": "courier"}]}  # fmt: skip


def thinking_reply(content, reasoning, finish="stop"):
    status, payload = chat_reply(content, finish, model="qwen3.5-9b")
    payload["choices"][0]["message"]["reasoning_content"] = reasoning
    return status, payload


def ask_accounts(server, reference):
    from ap_coder.inference import suggest_accounts

    return suggest_accounts(InvoiceCoder(local_settings(server.base_url), reference), "Staples", TWO_LINES)


def test_thinking_is_asked_off_and_a_reply_only_in_reasoning_is_read(lm_studio, reference):
    draft = {"lines": [{"line_number": 1, "gl_code": "6400", "cost_center": "", "reason": "first idea"}]}
    reasoning = f"Thinking Process:\n1. Paper... maybe {json.dumps(draft)}\nNo, better:\n{json.dumps(PICKS)}"
    server = lm_studio(["qwen3.5-9b"], QWEN35_V0, reply=lambda body: thinking_reply("", reasoning))
    picks = ask_accounts(server, reference)
    assert {n: gl for n, (gl, _, _) in picks.items()} == {1: "6000", 2: "6800"}  # the final draft, not the first
    sent = server.chats[0]
    assert sent["reasoning_effort"] == "none"  # LM Studio 0.4.8+ turns a Qwen 3.5's thinking off with this
    assert sent["chat_template_kwargs"] == {"enable_thinking": False}  # vLLM, llama.cpp, hosted Qwen
    assert sent["response_format"]["type"] == "json_schema"


def test_an_answer_left_inside_think_tags_is_read(lm_studio, reference):
    content = f"<think>Paper is office supplies, courier is freight.\n{json.dumps(PICKS)}</think>"
    server = lm_studio(["qwen3.5-9b"], QWEN35_V0, reply=lambda body: chat_reply(content, model="qwen3.5-9b"))
    assert set(ask_accounts(server, reference)) == {1, 2}


@pytest.mark.parametrize(
    "content, reasoning",
    [
        ("", "Thinking Process:\n\n1. **Analyze the Request:**\n * Line 1: Printer paper ->"),  # split off
        ("Thinking Process:\n\n1. **Analyze the Request:** {not json", None),  # the template opened <think> itself
        ("<think>\nLine 1 is paper, so", None),
    ],
)
def test_thinking_until_the_token_limit_is_a_clear_error(lm_studio, reference, sample_markdown_path,
                                                         content, reasoning):  # fmt: skip
    server = lm_studio(["qwen3.5-9b"], QWEN35_V0, reply=lambda body: thinking_reply(content, reasoning, "length"))
    with pytest.raises(CodingError, match=r"spent its answer thinking.*4,096.*AP_LLM_MAX_TOKENS.*thinking off"):
        ask_accounts(server, reference)
    calls = len(server.chats)
    with pytest.raises(CodingError, match="spent its answer thinking"):
        InvoiceCoder(local_settings(server.base_url), reference).code(result_from_text(sample_markdown_path))
    assert len(server.chats) == calls + 1  # no repair turn: it would be cut off the same way


def test_a_cut_off_json_answer_says_so(lm_studio, reference):
    server = lm_studio(["qwen3.5-9b"], QWEN35_V0, reply=lambda body: chat_reply('{"lines": [{"line_', "length"))
    with pytest.raises(CodingError, match="cut off before the JSON was complete.*AP_LLM_MAX_TOKENS"):
        ask_accounts(server, reference)


def test_an_answer_without_json_is_an_error_not_an_empty_suggestion(lm_studio, reference):
    server = lm_studio(["qwen3.5-9b"], QWEN35_V0, reply=lambda body: chat_reply("Paper is office supplies."))
    with pytest.raises(CodingError, match="held no JSON"):
        ask_accounts(server, reference)


def test_the_doctor_dry_run_names_the_thinking_problem(lm_studio, reference):
    from ap_coder.doctor import WARN, run_checks

    server = lm_studio(["qwen3.5-9b"], QWEN35_V0, reply=lambda body: thinking_reply("", "Thinking Process:", "length"))
    checks = {c.area: c for c in run_checks(local_settings(server.base_url), lambda: reference, online=True)}
    assert checks["local dry run"].status == WARN and "spent its answer thinking" in checks["local dry run"].detail


def test_a_server_refusing_the_thinking_fields_is_asked_again_with_fewer(lm_studio, reference):
    def reply(body):
        for name in ("reasoning_effort", "chat_template_kwargs"):
            if name in body:
                return 400, {"error": {"message": f"Unrecognized request argument supplied: {name}"}}
        return chat_reply(json.dumps(PICKS), model="qwen3.5-9b")

    server = lm_studio(["qwen3.5-9b"], QWEN35_V0, reply=reply)
    assert set(ask_accounts(server, reference)) == {1, 2}
    sent = [(("reasoning_effort" in c) + ("chat_template_kwargs" in c), c["response_format"]["type"])
            for c in server.chats]  # fmt: skip
    assert sent == [(2, "json_schema"), (1, "json_schema"), (0, "json_schema")]  # the schema is kept
    ask_accounts(server, reference)  # the next call starts with what worked
    assert len(server.chats) == 4 and "chat_template_kwargs" not in server.chats[-1]


def test_qwen35_can_see_pages():
    from ap_coder.local_llm import looks_like_vision

    assert looks_like_vision("qwen3.5-9b") and looks_like_vision("qwen3.5:9b") and looks_like_vision("Qwen3.5-2B")
    assert not looks_like_vision("qwen3-8b")
