"""A local model through LM Studio (or Ollama): detection, model picking, structured-output fallback, loose JSON.

A tiny OpenAI-compatible server runs in a thread on 127.0.0.1 (no real network, no real model).
"""

import json
import re
import tempfile
import threading
import time
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from ap_coder.ai import local_llm
from ap_coder.ai.extraction import result_from_text
from ap_coder.ai.inference import CodingError, InvoiceCoder
from ap_coder.ai.local_llm import (
    check_server,
    clip_document,
    parse_json_reply,
    pick_model,
    provider_status,
    read_reply,
    resolve_provider,
)
from ap_coder.core.config import LocalLLMSettings, OpenAISettings, Settings, normalise_base_url
from tests.conftest import FakeOpenAI, make_completion

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
        "auto",  # AP_LLM_PROVIDER is no longer read: every invoice is coded the same way
        "http://127.0.0.1:1234/v1",
        "",
        "off",
        8000,
    )
    assert llm.api_key == "lm-studio"
    monkeypatch.setenv("AP_LLM_PROVIDER", "off")
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

    idle = lm_studio(["gemma-3-27b-it", "qwen/qwen3.5-9b"], [{"id": "qwen/qwen3.5-9b", "type": "vlm", "state": "x"}])
    status = check_server(LocalLLMSettings(base_url=idle.base_url))
    assert status.active and status.model == "qwen/qwen3.5-9b"  # the team's chat model: LM Studio loads it when asked
    other = lm_studio(["qwen2.5-7b-instruct", "llama-3.2-3b-instruct"], [{**V0_LOADED[2], "state": "not-loaded"}])
    status = check_server(LocalLLMSettings(base_url=other.base_url))
    assert status.reachable and not status.active  # never the first on the list: it could be any size (CloseDesk)
    reader_only = lm_studio(["ath-maas_ovisocr2", "text-embedding-nomic-embed-text-v1.5"], [])
    status = check_server(LocalLLMSettings(base_url=reader_only.base_url))
    assert status.reachable and not status.active  # OvisOCR2 reads pages, it never suggests accounts
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
        # LM Studio with nothing loaded (just-in-time loading lists every downloaded model, and asking for one loads
        # it): only the team's chat model, Qwen 3.5 9B, as CloseDesk asks for it; never the first on the list.
        ("", ["a", "b"], [], True, ""),
        ("", ["gemma-3-27b-it", "qwen/qwen3.5-9b", "b"], [], True, "qwen/qwen3.5-9b"),
        ("", ["ath-maas_ovisocr2", "text-embedding-x"], [], True, ""),  # never OvisOCR2 or an embedding model
        ("", ["text-embedding-qwen3-embedding-0.6b", "ovis-ocr2", "qwen3.5-9b-mlx"], [], True, "qwen3.5-9b-mlx"),
        ("", ["qwen3.5-27b", "qwen3.5-4b"], [], True, ""),  # another size is not the one the team runs
        # Gemma 4 12B is the team's chat model too, picked first when both are downloaded and neither is loaded
        ("", ["qwen/qwen3.5-9b", "google/gemma-4-12b", "b"], [], True, "google/gemma-4-12b"),
        ("", ["gemma-4-e4b-it", "gemma-4-26b-a4b-it", "qwen3.5-9b"], [], True, "qwen3.5-9b"),  # other Gemma sizes: no
        ("", ["google/gemma-4-12b", "qwen/qwen3.5-9b"], ["qwen/qwen3.5-9b"], True, "qwen/qwen3.5-9b"),  # loaded wins
        ("", ["ath-maas_ovisocr2", "gemma-4-12b-it-qat"], ["ath-maas_ovisocr2"], True, "gemma-4-12b-it-qat"),
        ("", ["Ovis_OCR2-GGUF"], ["Ovis_OCR2-GGUF"], True, ""),  # a page reader, however it is spelled
        # OvisOCR2 under any name the page reader knows it by (an MLX build, separators): never the chat model
        ("", ["ovis-ocr2", "qwen/qwen3.5-9b"], ["ovis-ocr2"], True, "qwen/qwen3.5-9b"),
        ("", ["ovis_ocr2-mlx", "qwen3.5-9b-mlx"], ["ovis_ocr2-mlx"], True, "qwen3.5-9b-mlx"),
        ("", [], [], False, ""),
    ],
)
def test_pick_model(requested, ids, loaded, lm, expected):
    assert pick_model(requested, ids, loaded, lm_studio=lm) == expected


@pytest.mark.parametrize(
    "model_id",
    [
        "qwen/qwen3.5-9b", "qwen3.5-9b", "qwen3.5-9b-mlx", "qwen3.5-9b@q4_k_m", "lmstudio-community/Qwen3.5-9B-GGUF",
        "mlx-community/Qwen3.5-9B-4bit", "Qwen3.5-9B-Instruct-Q4_K_M.gguf", "qwen3_5-9b", "qwen3.5:9b",
    ],
)  # fmt: skip
def test_recognises_the_team_chat_model_in_every_build(model_id):
    """The ids LM Studio gives Qwen 3.5 9B: by publisher, MLX or GGUF, with a quantization; Ollama's tag."""
    assert local_llm.team_chat_rank(model_id) == 1 and local_llm.is_chat_model(model_id)


@pytest.mark.parametrize(
    "model_id",
    [
        "qwen3.5-4b", "qwen3.5-27b", "qwen3-8b", "qwen2.5-7b-instruct", "text-embedding-qwen3-embedding-0.6b",
        "Qwen3-Embedding-4B-GGUF", "ath-maas_ovisocr2", "qwen3.5-19b",
    ],
)  # fmt: skip
def test_other_models_are_not_the_team_chat_model(model_id):
    assert local_llm.team_chat_rank(model_id) is None


@pytest.mark.parametrize(
    "model_id",
    [
        "google/gemma-4-12b", "gemma-4-12b-it", "gemma-4-12b-it-qat", "lmstudio-community/gemma-4-12B-it-QAT-GGUF",
        "lmstudio-community/gemma-4-12B-it-GGUF", "gemma-4-12B-it-Q4_K_M.gguf", "gemma-4-12b-it-mlx", "gemma4:12b",
        "gemma-4-12b@q4_0",
    ],
)  # fmt: skip
def test_recognises_gemma_4_12b_in_every_build(model_id):
    """The ids LM Studio and Ollama give Gemma 4 12B: it can see pages and is ranked before Qwen 3.5 9B."""
    assert local_llm.team_chat_rank(model_id) == 0 and local_llm.looks_like_vision(model_id)


@pytest.mark.parametrize(
    "model_id", ["gemma-4-e4b-it", "gemma-4-26b-a4b-it", "gemma-4-31b-it", "gemma-3-12b-it", "gemma-4-112b"]
)
def test_other_gemma_models_are_not_the_team_chat_model(model_id):
    assert local_llm.team_chat_rank(model_id) is None


@pytest.mark.parametrize(
    "model_id",
    [
        "ath-maas_ovisocr2", "OvisOCR2-GGUF", "ovis-ocr2", "Ovis_OCR2@q8_0", "mlx-community/OvisOCR2-8bit",
        "text-embedding-qwen3-embedding-0.6b", "qwen3-embedding-0.6b-mlx", "Qwen/Qwen3-Embedding-8B",
        "nomic-embed-text",
    ],
)  # fmt: skip
def test_page_readers_and_embedding_models_never_chat(model_id):
    assert not local_llm.is_chat_model(model_id)


def test_other_servers_guess_vision_from_the_name(lm_studio):
    server = lm_studio(["gemma3:4b"])  # Ollama-style: no LM Studio route
    status = check_server(LocalLLMSettings(base_url=server.base_url))
    assert status.model == "gemma3:4b" and status.vision and not status.lm_studio


# --- Which provider -------------------------------------------------------------------------------------------


def test_every_invoice_is_coded_the_same_way(lm_studio, monkeypatch):
    """Local always in the offline build, whether LM Studio answers or not (it used to be "off" when it didn't, and
    an invoice was coded differently depending on the moment). Azure only with the internet allowed."""
    azure = Settings(openai=OpenAISettings(endpoint="https://x.openai.azure.com/"))
    assert resolve_provider(azure) == "local"  # offline: an Azure endpoint in the .env is never used
    monkeypatch.setenv("AP_ALLOW_INTERNET", "1")
    assert resolve_provider(azure) == "azure" and provider_status(azure).ready
    assert resolve_provider(replace(azure, llm=LocalLLMSettings(provider="local"))) == "local"
    monkeypatch.delenv("AP_ALLOW_INTERNET")
    assert resolve_provider(Settings(llm=LocalLLMSettings(base_url="http://127.0.0.1:9/v1"))) == "local"
    server = lm_studio(["qwen2.5-7b-instruct"], V0_LOADED)
    local = Settings(llm=LocalLLMSettings(base_url=server.base_url))
    assert resolve_provider(local) == "local"
    assert provider_status(local).label == "LM Studio · qwen2.5-7b-instruct"
    assert resolve_provider(replace(local, llm=replace(local.llm, provider="off"))) == "off"


def test_no_model_anywhere(reference, sample_markdown_path):
    settings = Settings()  # auto, no Azure, nothing answering (conftest)
    assert resolve_provider(settings) == "local"
    status = provider_status(settings)
    assert not status.ready and "Not running" in status.detail
    coder = InvoiceCoder(settings, reference)  # building it never waits on a server
    assert coder.wants_images() is False
    with pytest.raises(CodingError, match="isn't answering"):
        coder.code(result_from_text(sample_markdown_path))


def test_a_text_invoice_without_a_model_is_read_by_the_rule_reader(reference, sample_markdown_path, ground_truth):
    """A .md/.txt invoice with no AI model used to fail ("No AI model is set up"): it is read by the same rule reader
    and checks as a PDF, its text taken as exact, and coded from AP's history (none here: left for AP)."""
    from ap_coder.ai.offline_coder import MODEL_NAME
    from ap_coder.ai.pipeline import InvoicePipeline

    result = InvoicePipeline(Settings(), reference).process(sample_markdown_path)
    assert result.ok, result.error
    assert result.coding.model == MODEL_NAME and result.capture is not None
    for name in ("vendor_name", "invoice_number", "invoice_date", "grand_total", "subtotal"):
        assert result.output[name] == ground_truth[name], name
    assert result.capture.layout_source == "text"


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
    server = lm_studio(["ath-maas_ovisocr2"], [{"id": "ath-maas_ovisocr2", "type": "vlm", "state": "loaded"}])
    with pytest.raises(CodingError, match="no model is loaded"):
        InvoiceCoder(local_settings(server.base_url), reference).code(result_from_text(sample_markdown_path))


def test_vision_follows_the_loaded_model(lm_studio, reference):
    v0 = [{"id": "qwen2.5-vl-7b-instruct", "type": "vlm", "state": "loaded"}]
    server = lm_studio(["qwen2.5-vl-7b-instruct"], v0)
    assert InvoiceCoder(local_settings(server.base_url), reference).wants_images() is True
    assert InvoiceCoder(local_settings(server.base_url, vision="off"), reference).wants_images() is False


# --- Doctor ---------------------------------------------------------------------------------------------------


def test_doctor_reports_the_local_model(lm_studio, reference):
    from ap_coder.ops.doctor import PASS, SKIP, run_checks

    server = lm_studio(["qwen2.5-7b-instruct"], V0_LOADED)
    checks = {
        c.area: c for c in run_checks(Settings(llm=LocalLLMSettings(base_url=server.base_url)), lambda: reference)
    }
    assert checks["AI model"].status == PASS and "local" in checks["AI model"].detail
    assert checks["local model"].status == PASS and "qwen2.5-7b-instruct" in checks["local model"].detail
    assert checks["AOAI endpoint"].status == SKIP


def test_doctor_local_dry_run(lm_studio, reference, ground_truth):
    from ap_coder.ops.doctor import PASS, run_checks

    # The dry run asks what the pipeline asks a local model: the accounts of two lines.
    picks = {"lines": [{"line_number": 1, "gl_code": "6000", "cost_center": "", "reason": "supplies"},
                       {"line_number": 2, "gl_code": "6800", "cost_center": "", "reason": "courier"}]}  # fmt: skip
    server = lm_studio(["qwen2.5-7b-instruct"], V0_LOADED, reply=lambda body: chat_reply(json.dumps(picks)))
    checks = {c.area: c for c in run_checks(local_settings(server.base_url), lambda: reference, online=True)}
    assert checks["local dry run"].status == PASS and "line 1 -> 6000" in checks["local dry run"].detail


# --- Settings page --------------------------------------------------------------------------------------------


@pytest.fixture
def no_reading_status(monkeypatch):
    """Settings → Reading with nothing known yet about the readers (OvisOCR2 not found)."""
    from ap_coder.ai import reading

    monkeypatch.setattr(reading, "reading_status", lambda settings, store, use_cache=True: reading.ReadingStatus())


@pytest.fixture
def settings_client(tmp_path, monkeypatch):
    """The dashboard reading its settings from a .env of the test's own (as it does for every page)."""
    from fastapi.testclient import TestClient

    from ap_coder.core.store import Store
    from ap_coder.web.app import create_app

    env = tmp_path / "private" / ".env"
    monkeypatch.setenv("AP_ENV_FILE", str(env))
    app = create_app(None, Store(tmp_path / "private" / "ap_coder.db"), start_reader=False)
    return TestClient(app, base_url="http://localhost"), env


def _words(page: str) -> str:
    import html
    import re

    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", page)))


def test_settings_page_when_lm_studio_is_not_running(settings_client, no_reading_status):
    client, _ = settings_client
    text = _words(client.get("/settings/reading").text)
    assert "IT team installs it" in text and "Start server" in text and "lmstudio.ai" not in text
    response = client.post("/settings/reading/check", headers={"X-AP-Coder": "1"})
    assert response.status_code == 200 and response.json()["ok"]


def test_settings_page_lists_the_models_with_nothing_to_choose(settings_client, lm_studio, monkeypatch,
                                                               no_reading_status):  # fmt: skip
    from ap_coder.core.envfile import read_env

    client, env = settings_client
    server = lm_studio(["qwen2.5-7b-instruct", "llama-3.2-3b-instruct", "text-embedding-nomic"], V0_LOADED)
    for key in ("AP_LLM_PROVIDER", "AP_LLM_MODEL", "AP_LLM_VISION"):
        monkeypatch.setenv(key, "")  # restored after the test
    monkeypatch.setenv("AP_LLM_BASE_URL", server.base_url)
    page = client.get("/settings/reading").text
    assert "IT team installs it" not in _words(page)
    table = page.split("Models in LM Studio", 1)[1].split("</table>", 1)[0]
    models = set(re.findall(r"<td[^>]*>\s*(?:<[^>]+>\s*)*([\w.-]+-instruct|[\w.-]*embed[\w.-]*)", table))
    # what LM Studio has (its own listing); the embedding model only as one AP Coder doesn't use
    assert {"qwen2.5-7b-instruct", "qwen2.5-vl-7b-instruct"} <= models
    for embedding in [m for m in models if "embed" in m]:
        assert "not used (an embedding model)" in table.split(embedding, 1)[1].split("</tr>", 1)[0]
    for gone in ("llm_provider", "llm_model", "llm_vision", "pr_mode", "pr_scope", "pr_model"):
        assert f'name="{gone}"' not in page
    assert not read_env(env)  # looking at Settings writes nothing


def test_with_a_local_model_the_reader_reads_and_the_model_codes_the_leftover_lines(lm_studio, reference):
    """A small model misreads totals and provinces; the local reader does not. So the invoice is read by the
    reader, and the model is only asked for the accounts the approval memory could not give."""
    from ap_coder.ai.pipeline import InvoicePipeline
    from ap_coder.core.store import Store
    from tests.conftest import SAMPLES

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
    from ap_coder.core.schema import province_code

    assert province_code("Vancouver, BC") == "BC" and province_code("British Columbia") == "BC"
    assert province_code("Québec") == "QC" and province_code("Seattle, USA") == "OUTSIDE_CANADA"
    with pytest.raises(ValueError):
        province_code("Narnia")
    with pytest.raises(ValueError):
        province_code("Ontario St, Quebec City")  # two provinces named, neither at the end: not a guess


@pytest.mark.parametrize(
    "written, code",
    [
        ("1234 Rue Ontario Est, Montréal, QC", "QC"),  # a street named after a province is not the province
        ("500 Quebec St, Vancouver, BC", "BC"),
        ("Alberta St, Portland, OR, USA", "OUTSIDE_CANADA"),
        ("Halifax, Nova Scotia, Canada", "NS"),
        ("Toronto ON M5V 2T6", "ON"),
        ("Toronto, ON, CA", "ON"),
        ("Montréal (Québec) H2X 1Y4", "QC"),
        ("PEI", "PE"),
        ("P.E.I.", "PE"),
        ("PQ", "QC"),
        ("Que.", "QC"),
        ("NF", "NL"),
        ("Nfld", "NL"),
        ("Newfoundland & Labrador", "NL"),
        ("Île-du-Prince-Édouard", "PE"),
        ("Boston, MA", "OUTSIDE_CANADA"),
        ("Seattle, WA, USA", "OUTSIDE_CANADA"),
        ("Los Angeles, CA", "OUTSIDE_CANADA"),
        ("Portland, OR 97201", "OUTSIDE_CANADA"),
        ("Boston, Massachusetts", "OUTSIDE_CANADA"),
        # The one province named, not at the end: a city, a region, a tax in brackets.
        ("Quebec City", "QC"),
        ("Québec City", "QC"),
        ("Ontario Region", "ON"),
        ("Nunavut Territory", "NU"),
        ("Ontario, Canada (HST)", "ON"),
        ("Manitoba (RST)", "MB"),
    ],
)
def test_a_province_from_an_address_or_an_old_abbreviation(written, code):
    from ap_coder.core.schema import province_code

    assert province_code(written) == code


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
    from ap_coder.ai.inference import suggest_accounts

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


@pytest.mark.parametrize(
    "answer",
    ['{"lines": [{"line_', json.dumps(PICKS)[:-60]],  # cut in its first line; the first line whole, the second cut
    ids=["in-the-first-line", "after-the-first-line"],
)
def test_a_cut_off_json_answer_says_so(lm_studio, reference, answer):
    """An answer cut off at the token limit is an error that says how to give it more room, never its first lines
    read as a suggestion."""
    server = lm_studio(["qwen3.5-9b"], QWEN35_V0, reply=lambda body: chat_reply(answer, "length"))
    with pytest.raises(CodingError, match="cut off before the JSON was complete.*AP_LLM_MAX_TOKENS"):
        ask_accounts(server, reference)


@pytest.mark.parametrize(
    "content",
    [
        '{"vendor_name": "ACME", "line_items": [{"line_number": 1, "amount": 10.0}, {"line_number": 2, "descr',
        '{"lines": [{"line_number": 1, "gl_code": "6000", "reason": "x"}, {"line_number": 2, "gl_co',
    ],
)
def test_a_reply_cut_off_at_the_token_limit_is_not_read_as_one_of_its_line_items(content):
    from types import SimpleNamespace

    assert parse_json_reply(content) is None  # the first "{" never closes: an object inside it is not the answer
    reply = read_reply(SimpleNamespace(message=SimpleNamespace(content=content), finish_reason="length"), 2048)
    assert reply.data is None and reply.cut_off and "cut off before the JSON was complete" in reply.problem
    closed = read_reply(SimpleNamespace(message=SimpleNamespace(content=content + '"]}'), finish_reason="length"))
    assert closed.data is None and closed.cut_off  # stopped at the limit: not trusted even when it closes


def test_an_answer_without_json_is_an_error_not_an_empty_suggestion(lm_studio, reference):
    server = lm_studio(["qwen3.5-9b"], QWEN35_V0, reply=lambda body: chat_reply("Paper is office supplies."))
    with pytest.raises(CodingError, match="held no JSON"):
        ask_accounts(server, reference)


def test_the_doctor_dry_run_names_the_thinking_problem(lm_studio, reference):
    from ap_coder.ops.doctor import WARN, run_checks

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
    from ap_coder.ai.local_llm import looks_like_vision

    assert looks_like_vision("qwen3.5-9b") and looks_like_vision("qwen3.5:9b") and looks_like_vision("Qwen3.5-2B")
    assert not looks_like_vision("qwen3-8b")
    assert looks_like_vision("gemma-4-12b-it-qat") and looks_like_vision("gemma4:12b")


# --- A good neighbour to CloseDesk (docs/LM_STUDIO.md) --------------------------------------------------------------
# CloseDesk uses the same LM Studio on the same Mac: AP Coder sends the chat model one request at a time, however many
# invoices are processed at once, and never sends the same request again by itself after a timeout or a failure.


def test_chat_requests_are_sent_one_at_a_time(lm_studio, reference):
    lock = threading.Lock()
    running, most = [0], [0]

    def reply(body):
        with lock:
            running[0] += 1
            most[0] = max(most[0], running[0])
        time.sleep(0.15)
        with lock:
            running[0] -= 1
        return chat_reply(json.dumps(PICKS), model="qwen3.5-9b")

    server = lm_studio(["qwen3.5-9b"], QWEN35_V0, reply=reply)
    threads = [threading.Thread(target=ask_accounts, args=(server, reference)) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(server.chats) == 4 and most[0] == 1


def test_a_failed_chat_request_is_not_sent_again_by_itself(lm_studio, reference):
    server = lm_studio(["qwen3.5-9b"], QWEN35_V0, reply=lambda body: (503, {"error": "busy"}))
    with pytest.raises(CodingError):
        ask_accounts(server, reference)
    assert len(server.chats) == 1  # the OpenAI SDK's own retry would have sent it twice
    assert local_llm.build_local_client(LocalLLMSettings()).max_retries == 0


@pytest.mark.parametrize(
    "content",
    [
        '<|channel>thought\nThe line is office paper, so 6100.<channel|>{"gl_code": "6100"}',
        'The line is office paper.<channel|>{"gl_code": "6100"}',  # the template opened the channel itself
        '<|channel>thought\n<channel|>{"gl_code": "6100"}',  # an empty "ghost" channel
    ],
)
def test_gemma_4_thought_channel_is_not_the_answer(content):
    """Gemma 4 writes any thinking in a thought channel, not <think> tags: only what follows it is the answer."""
    assert local_llm.strip_thinking(content) == '{"gl_code": "6100"}'
    assert local_llm.parse_json_reply(content) == {"gl_code": "6100"}


def _choice(content, reasoning="", finish="stop"):
    from types import SimpleNamespace

    return SimpleNamespace(message=SimpleNamespace(content=content, reasoning_content=reasoning), finish_reason=finish)


@pytest.mark.parametrize(
    "content",
    [
        "The total looks wrong to me.",  # prose: no answer
        "",  # nothing written
        '<|channel>thought\nmaybe {"fields": [], "lines": [], "confident": true, "summary": "a"} then',  # unclosed
        '<think>{"fields": [], "lines": [], "confident": true, "summary": "a"}',  # unclosed
    ],
)
def test_a_strict_reply_never_takes_a_draft_from_the_thinking(content):
    """The AI reviewer's answer can approve an invoice: a draft in its reasoning, or in a thought block it never
    closed, is not its answer."""
    draft = 'Draft: {"fields": [], "lines": [], "confident": true, "summary": "draft"} hmm, wait'
    reply = read_reply(_choice(content, draft), strict=True)
    assert reply.data is None and reply.problem
    answer = '{"fields": [], "lines": [], "confident": false, "summary": "x"}'
    assert read_reply(_choice(answer, draft), strict=True).data == json.loads(answer)
    assert read_reply(_choice(f"<think>{draft}</think>{answer}"), strict=True).data == json.loads(answer)


def test_a_strict_reply_is_the_whole_answer_not_json_found_in_prose():
    """A template that opened the thought itself writes a bare "Thinking Process:" it never closes: a draft inside it
    is not the answer, as it isn't inside an unclosed <think>. Strict, the answer is one JSON object or nothing."""
    draft = json.dumps({"fields": [], "lines": [], "confident": True, "summary": "all fine"})
    content = f"Thinking Process:\n1. The header.\n2. A first draft: {draft}\n3. Wait, the total on page 2 differs"
    reply = read_reply(_choice(content), strict=True)
    assert reply.data is None and reply.problem
    assert read_reply(_choice(f"I checked it. {draft}"), strict=True).data is None  # prose around it: not taken
    assert read_reply(_choice(f"Thinking Process: hmm</think>\n{draft}"), strict=True).data == json.loads(draft)
    assert read_reply(_choice(f"```json\n{draft}\n```"), strict=True).data == json.loads(draft)
    assert read_reply(_choice(content)).data is not None  # not strict (coding): read loosely, as before


@pytest.mark.parametrize(
    "reply",
    [
        '{"reason": "model said </think> here", "a": 2}',
        '{"reason": "a <think> b", "a": 2}',
        '<think>first</think>{"reason": "a <think> b", "a": 2}',
        '{"reason": "a <|channel>thought b", "a": 2}',
    ],
)
def test_a_thinking_tag_inside_a_json_string_is_kept(reply):
    assert parse_json_reply(reply)["a"] == 2


def test_a_reply_cut_off_without_the_length_reason_is_not_read_as_its_first_line():
    content = '{"lines": [{"line_number": 1, "gl_code": "6100", "reason": "a"}, {"line_num'
    reply = read_reply(_choice(content))
    assert reply.data is None and "cut off" in reply.problem


def _raised(status, message):
    exc = RuntimeError(message)
    exc.status_code = status
    return exc


class _Raising:
    """A chat client whose every call fails with ``exc``, keeping what it was sent."""

    def __init__(self, exc):
        from types import SimpleNamespace

        self.calls, self.exc = [], exc
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        raise self.exc


def test_a_400_that_is_not_about_the_format_keeps_structured_output(reference):
    """LM Studio couldn't load the model (a 400): that is not the server refusing response_format, so the schema is
    neither dropped for this call nor remembered as refused."""
    settings = local_settings("http://127.0.0.1:9/v1")
    client = _Raising(_raised(400, "Failed to load model 'gemma-4-12b': not enough memory"))
    coder = InvoiceCoder(settings, reference, client=client, provider="local")
    with pytest.raises(CodingError, match="Failed to load model"):
        coder._local_create("gemma-4-12b", [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}])
    assert [c.get("response_format", {}).get("type", "text") for c in client.calls] == ["json_schema"]
    assert local_llm.start_mode(settings.llm.base_url, "gemma-4-12b") == 0
    assert local_llm.rejects_format(_raised(400, "response_format json_schema is not supported"))
    assert local_llm.rejects_format(_raised(422, "Unknown field: response_format"))


class _PromptCoder:
    """Enough of an InvoiceCoder for ``suggest_accounts``: keeps each prompt, codes every line it is asked to 6000."""

    def __init__(self, reference, max_prompt_chars=24_000):
        self.reference, self.prompts = reference, []
        self.settings = local_settings("http://127.0.0.1:9/v1", max_prompt_chars=max_prompt_chars)

    def _prepare_local(self):
        return "qwen3.5-9b"

    def _local_create(self, model, messages, schema=None, **kw):
        self.prompts.append(messages)
        numbers = [int(n) for n in re.findall(r"^(\d+)\. ", messages[-1]["content"], re.M)]
        lines = [{"line_number": n, "gl_code": "6000", "cost_center": "", "reason": "x"} for n in numbers]
        return make_completion(json.dumps({"lines": lines}))


def test_a_long_invoice_is_suggested_within_the_prompt_budget(reference):
    """300 lines with long descriptions and a big chart of accounts: every prompt fits AP_LLM_MAX_PROMPT_CHARS (the
    lines go in batches, descriptions and the chart are clipped); a normal invoice is asked as before, in one call."""
    from ap_coder.ai.inference import SUGGEST_BATCH_LINES, suggest_accounts
    from ap_coder.core.reference_data import ReferenceTable

    lines = [(n, f"Item {n}: " + "replacement filter cartridge, heavy duty " * 12, 10.0 * n) for n in range(1, 301)]
    coder = _PromptCoder(reference)
    picks = suggest_accounts(coder, "Staples", lines, history="AP coded filters to 6000. " * 2000)
    assert all(sum(len(m["content"]) for m in p) <= 24_000 for p in coder.prompts)
    assert all(len(re.findall(r"^\d+\. ", p[-1]["content"], re.M)) <= SUGGEST_BATCH_LINES for p in coder.prompts)
    assert len(coder.prompts) > 1 and len(picks) >= 100  # in batches, not one prompt of every line

    chart = reference.chart_of_accounts
    rows = [*chart.rows, *({**chart.rows[0], chart.key_column: str(70000 + i), "description": "Account " * 20}
                           for i in range(2000))]  # fmt: skip
    big = replace(reference, chart_of_accounts=ReferenceTable(chart.kind, chart.key_column, rows))
    coder = _PromptCoder(big)
    suggest_accounts(coder, "Staples", lines[:2])
    assert sum(len(m["content"]) for m in coder.prompts[0]) <= 24_000

    coder = _PromptCoder(reference)
    assert set(suggest_accounts(coder, "Staples", TWO_LINES)) == {1, 2} and len(coder.prompts) == 1
    assert reference.to_prompt_context() in coder.prompts[0][0]["content"]  # the whole chart, as before


def test_the_reviewers_prompt_is_bounded(reference):
    from ap_coder.ai import verifier

    output = {"vendor_name": "Staples", "grand_total": 100.0,
              "line_items": [{"line_number": n, "description": "toner " * 200, "amount": 1.0,
                              "predicted_gl_code": "6000"} for n in range(1, 301)]}  # fmt: skip
    shown = verifier.proposal(output, None)
    numbers = [li["line_number"] for li in shown["lines"]]
    assert len(verifier.question(shown, list(verifier.FIELDS), numbers[:60], None)) <= 24_000
    asked = []
    found = verifier.review(lambda text, fields, lines: asked.append(text) or {}, shown)
    assert found.status == verifier.UNSURE and not asked and "300 lines" in found.why  # too many to check: a person


@pytest.mark.parametrize(
    "url",
    [
        "http://134744072:1234/v1",  # 8.8.8.8 written as one number
        "http://0x08080808:1234/v1",
        "http://0x8.0x8.0x8.0x8:1234/v1",
        "http://8.8.2056:1234/v1",
        "http://[64:ff9b::808:808]:1234/v1",  # NAT64: 8.8.8.8
        "http://[::ffff:8.8.8.8]:1234/v1",  # IPv4-mapped
    ],
)
def test_an_internet_address_written_another_way_is_still_refused(url, monkeypatch):
    from ap_coder.ops.offline import INTERNET_ENV

    monkeypatch.delenv(INTERNET_ENV, raising=False)
    assert local_llm.refused_address(url)


@pytest.mark.parametrize(
    "url",
    ["http://localhost:1234/v1", "http://127.0.0.1:1234/v1", "http://127.1:1234/v1", "http://[::1]:1234/v1",
     "http://192.168.1.40:1234/v1", "http://10.0.0.5:1234/v1", "http://172.16.3.2:1234/v1", "http://studio-mac:1234/v1",
     "http://studio-mac.local:1234/v1", "http://[::ffff:192.168.1.40]:1234/v1", "http://2130706433:1234/v1"],
)  # fmt: skip
def test_lm_studio_on_this_computer_or_the_lan_is_still_allowed(url, monkeypatch):
    from ap_coder.ops.offline import INTERNET_ENV

    monkeypatch.delenv(INTERNET_ENV, raising=False)
    assert local_llm.refused_address(url) == ""


def test_a_call_given_less_time_than_the_timeout_gets_only_that(reference):
    client = FakeOpenAI(make_completion("{}"))
    settings = local_settings("http://127.0.0.1:9/v1", timeout_seconds=600)
    coder = InvoiceCoder(settings, reference, client=client, provider="local")
    coder._local_create("m", [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}], timeout=120)
    assert client.calls[0]["timeout"] == 120
