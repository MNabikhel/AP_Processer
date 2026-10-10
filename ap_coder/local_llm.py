"""Local model (LM Studio, Ollama): finding the server, picking its model, and reading a small model's JSON.

Everything here is written so the dashboard never waits on a server that isn't running and a small model on a
laptop still produces a usable coding:

- the server is asked for its models with a short timeout, and the answer (or the silence) is cached briefly;
- the model is the chat model loaded in LM Studio, never an embedding model, and never "the first on the list"
  (with just-in-time loading LM Studio lists every downloaded model, and asking for one loads it);
- structured JSON output is requested when the server supports it, and loose replies (code fences,
  <think> blocks, chat around the JSON, trailing commas) are still parsed.

Standard library only for detection, so a check costs no imports and works with any installed OpenAI SDK.
"""

from __future__ import annotations

import json
import re
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from .config import _on_this_network

if TYPE_CHECKING:
    from .config import LocalLLMSettings, Settings

DETECT_TIMEOUT = 1.5  # seconds: a server on this computer answers in milliseconds, or isn't running
STATUS_TTL_SECONDS = 30.0  # a server that answered is asked again after this long
DOWN_TTL_SECONDS = 10.0  # one that didn't is asked again sooner, so starting LM Studio shows up quickly

_cache: dict[str, tuple[float, ModelStatus]] = {}
_lock = threading.Lock()

# Model ids that are not chat models: never picked to code an invoice.
_NOT_CHAT = ("embed", "rerank", "whisper", "tts", "bge-", "e5-", "ovisocr")  # ovisocr: a page reader, not a chat model
# Families that can look at pictures, for servers that don't say (Ollama, llama.cpp). LM Studio says itself.
_VISION_HINTS = (
    "-vl", "vl-", "_vl", "vision", "llava", "pixtral", "gemma-3", "gemma3", "minicpm-v", "moondream", "internvl",
    "smolvlm", "granite-vision", "qwen2.5-omni", "mistral-small-3.1", "mistral-small-3.2", "llama4",
    "qwen3.5", "qwen3_5",  # every Qwen 3.5 size has a vision encoder
)  # fmt: skip

LM_STUDIO_STEPS = (
    "Install LM Studio from lmstudio.ai.",
    "Download and load a small instruct model, e.g. Qwen 3.5 9B or Qwen 2.5 7B Instruct (any 3B–9B instruct "
    "model), with Context Length 8192. LM Studio 0.4.8 or newer, so a thinking model can be told not to think.",
    "Developer tab → Start server.",
)


@dataclass
class ModelStatus:
    base_url: str
    reachable: bool = False
    model: str = ""  # the model that would code an invoice ("" = none loaded)
    models: list[str] = field(default_factory=list)  # every id /v1/models listed
    chat_models: list[str] = field(default_factory=list)  # the ones that can answer (no embedding models)
    loaded: list[str] = field(default_factory=list)  # LM Studio's loaded chat models
    vision: bool = False  # the model can look at a page image
    lm_studio: bool = False  # LM Studio's own route answered (it says which models are loaded and which can see)
    pinned: bool = False  # AP_LLM_MODEL names the model
    error: str = ""

    @property
    def active(self) -> bool:
        return self.reachable and bool(self.model)

    @property
    def server(self) -> str:
        if self.lm_studio:
            return "LM Studio"
        return "Ollama" if ":11434" in self.base_url else "the local server"

    @property
    def server_title(self) -> str:
        """``server`` at the start of a sentence."""
        return self.server[:1].upper() + self.server[1:]

    def describe(self) -> str:
        if self.active:
            sees = "can see pages" if self.vision else "reads text only"
            return f"Connected to {self.server} · {self.model} · {sees}"
        if self.reachable:
            return f"{self.server_title} is running but no model is loaded. Load one in LM Studio."
        return f"Not running: nothing answered at {self.base_url}."


def _root(base_url: str) -> str:
    base = base_url.rstrip("/")
    return base[: -len("/v1")] if base.endswith("/v1") else base


def _fetch_json(url: str, api_key: str, timeout: float) -> Any:
    """GET a JSON document (raises OSError / ValueError when the server is down or answers something else)."""
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {api_key or 'lm-studio'}"})
    # A server on this computer or network: no proxy (an office proxy would answer for 127.0.0.1, or hang).
    local = _on_this_network(urlparse(url).netloc)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({})) if local else urllib.request.build_opener()
    with opener.open(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", "replace"))


def is_chat_model(model_id: str) -> bool:
    plain = model_id.lower()
    return not any(word in plain for word in _NOT_CHAT)


def looks_like_vision(model_id: str) -> bool:
    plain = model_id.lower()
    return any(hint in plain for hint in _VISION_HINTS)


def pick_model(requested: str, ids: list[str], loaded: list[str] | None = None, *, lm_studio: bool = False) -> str:
    """The model to ask: the one in AP_LLM_MODEL, else the chat model loaded now. With nothing loaded, LM Studio
    lists every downloaded model and loads whichever a request names, so the first on its list would be loaded
    though nobody chose it: none is picked, and Settings says to load one. Other servers list only what they serve."""
    if requested:
        return requested
    chat_loaded = [m for m in loaded or [] if is_chat_model(m)]
    if chat_loaded:
        return chat_loaded[0]
    if lm_studio:
        return ""
    return next((m for m in ids if is_chat_model(m)), "")


def _lm_studio_listing(root: str, api_key: str, timeout: float) -> tuple[list[str], set[str]] | None:
    """LM Studio's own model list: its loaded chat models, and every model that can see. None from other servers.

    LM Studio 0.4 answers ``/api/v1/models`` (``models`` with ``loaded_instances``), 0.3 ``/api/v0/models``
    (``data`` with a ``state`` on each)."""
    for path in ("/api/v1/models", "/api/v0/models"):
        try:
            data = _fetch_json(root + path, api_key, timeout)
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        loaded: list[str] = []
        seeing: set[str] = set()
        if isinstance(data.get("models"), list):
            for item in data["models"]:
                if not isinstance(item, dict) or item.get("type") not in {"llm", "vlm"}:
                    continue
                caps = item.get("capabilities") if isinstance(item.get("capabilities"), dict) else {}
                sees = caps.get("vision") is True or item.get("type") == "vlm"
                instances = [
                    str(i["id"]) for i in item.get("loaded_instances") or [] if isinstance(i, dict) and i.get("id")
                ]
                loaded += instances
                if sees:
                    seeing.update(instances)
                    if item.get("key"):
                        seeing.add(str(item["key"]))
            return loaded, seeing
        items = data.get("data")
        if isinstance(items, list) and any(isinstance(i, dict) and "state" in i for i in items):
            for item in items:
                if not isinstance(item, dict) or not item.get("id") or item.get("type") not in {"llm", "vlm"}:
                    continue
                if item.get("type") == "vlm":
                    seeing.add(str(item["id"]))
                if item.get("state") == "loaded":
                    loaded.append(str(item["id"]))
            return loaded, seeing
        # A server answering this route with a plain list of models isn't LM Studio.
    return None


def check_server(llm: LocalLLMSettings, *, timeout: float = DETECT_TIMEOUT, use_cache: bool = True) -> ModelStatus:
    """Ask the local server which models it has. Cached briefly (and quick when nothing answers) so pages stay fast."""
    base = llm.base_url.rstrip("/")
    key = f"{base}|{llm.model}"
    now = time.monotonic()
    with _lock:
        cached = _cache.get(key)
    if use_cache and cached:
        when, status = cached
        if now - when < (STATUS_TTL_SECONDS if status.reachable else DOWN_TTL_SECONDS):
            return status
    status = ModelStatus(base_url=base, pinned=bool(llm.model))
    try:
        payload = _fetch_json(base + "/models", llm.api_key, timeout)
        listed = payload.get("data", []) if isinstance(payload, dict) else []
        ids = [str(item["id"]) for item in listed if isinstance(item, dict) and item.get("id")]
        status.reachable = True
        status.models = ids
        status.chat_models = [m for m in ids if is_chat_model(m)]
        listing = _lm_studio_listing(_root(base), llm.api_key, timeout)
        seeing: set[str] = set()
        if listing is not None:
            status.lm_studio = True
            status.loaded, seeing = listing
        status.model = pick_model(llm.model, ids, status.loaded, lm_studio=status.lm_studio)
        status.vision = status.model in seeing if status.lm_studio else looks_like_vision(status.model)
    except (OSError, ValueError, AttributeError, TypeError) as exc:
        status.error = _short_error(exc)
    with _lock:
        _cache[key] = (time.monotonic(), status)
    return status


def forget_status() -> None:
    """Drop cached answers (after Settings saves a new address, or the Test connection button)."""
    with _lock:
        _cache.clear()


# --- Which AI codes the invoices ------------------------------------------------------------------------------


def resolve_provider(settings: Settings, *, probe: bool = True) -> str:
    """``azure``, ``local`` or ``off``. Auto: Azure OpenAI when its endpoint is set, else a local model when one
    answers (``probe``), else none."""
    provider = settings.llm.provider
    if provider in {"azure", "local", "off"}:
        return provider
    if settings.openai.endpoint:
        return "azure"
    if probe and check_server(settings.llm).active:
        return "local"
    return "off"


@dataclass(frozen=True)
class ProviderStatus:
    provider: str  # azure / local / off
    ready: bool
    label: str  # short, for a setup checklist: "LM Studio · qwen2.5-7b-instruct"
    detail: str  # one plain sentence


def provider_status(settings: Settings) -> ProviderStatus:
    provider = resolve_provider(settings)
    if provider == "azure":
        ready = bool(settings.openai.endpoint)
        return ProviderStatus(
            "azure", ready, f"Azure OpenAI · {settings.openai.deployment}" if ready else "Azure OpenAI",
            "Coding with Azure OpenAI." if ready else "AP_LLM_PROVIDER=azure but the Azure OpenAI endpoint is not set.",
        )  # fmt: skip
    if provider == "local":
        status = check_server(settings.llm)
        if status.active:
            return ProviderStatus("local", True, f"{status.server} · {status.model}", status.describe())
        return ProviderStatus("local", False, "Local model", status.describe())
    if settings.llm.provider == "off":
        return ProviderStatus("off", False, "Off", "AI coding is turned off (AP_LLM_PROVIDER=off).")
    status = check_server(settings.llm)
    return ProviderStatus("off", False, "No AI model", status.describe())


NO_MODEL_MESSAGE = (
    "No AI model is set up to code invoices. Start LM Studio's server with a model loaded (Settings → AI model), "
    "or connect Azure OpenAI."
)


# --- Talking to it --------------------------------------------------------------------------------------------


def build_local_client(llm: LocalLLMSettings) -> Any:
    from openai import DefaultHttpxClient, OpenAI

    options: dict[str, Any] = {}
    if _on_this_network(urlparse(llm.base_url).netloc):
        # A server on this computer or network: never through an office proxy (it would answer for 127.0.0.1).
        options["http_client"] = DefaultHttpxClient(trust_env=False)
    return OpenAI(
        base_url=llm.base_url,
        api_key=llm.api_key or "lm-studio",
        timeout=llm.timeout_seconds,
        max_retries=1,  # a local server that failed once is busy or down, not rate limited
        **options,
    )


# Structured output, best first. LM Studio takes json_schema; Ollama and llama.cpp take json_object too; anything
# else gets the schema in the prompt and its reply is parsed.
RESPONSE_MODES = ("json_schema", "json_object", "text")
_mode_by_model: dict[str, int] = {}


def start_mode(base_url: str, model: str) -> int:
    return _mode_by_model.get(f"{base_url}|{model}", 0)


def remember_mode(base_url: str, model: str, index: int) -> None:
    _mode_by_model[f"{base_url}|{model}"] = index


def rejects_format(exc: Exception) -> bool:
    """A server refusing ``response_format`` (rather than failing): a 400/415/422/501, or a 500 that names it."""
    status = getattr(exc, "status_code", None)
    text = str(exc).lower()
    if status in {400, 415, 422, 501}:
        return not context_overflow(exc)
    return status == 500 and ("response_format" in text or "json_schema" in text or "grammar" in text)


def refuses_extra_fields(exc: Exception) -> bool:
    """A strict server refusing a field it doesn't know (the thinking switch), not ``response_format``."""
    if getattr(exc, "status_code", None) not in {400, 422} or context_overflow(exc):
        return False
    text = str(exc).lower()
    return not any(word in text for word in ("response_format", "json_schema", "json_object", "grammar"))


# --- Thinking models --------------------------------------------------------------------------------------------
# Hybrid thinking models (Qwen 3, Qwen 3.5) think before they answer unless told not to, and on a laptop CPU the
# thinking alone can take minutes. The fields below are what servers document for "don't": reasoning_effort
# "none" (LM Studio 0.4.8+ turns a Qwen 3.5's thinking off with it) and the chat template's switch (vLLM, SGLang,
# llama.cpp's server, hosted Qwen; LM Studio ignores it, its bug tracker #1990; Qwen 3.5 has no /no_think prompt
# switch). Servers that don't know them ignore them; one that refuses them gets the request again with fewer.
# Thinking may still happen (an older LM Studio), so the JSON is also looked for in the reasoning, and a reply
# that thought until it ran out of tokens says so.
THINKING_OFF: tuple[dict[str, Any], ...] = (
    {"chat_template_kwargs": {"enable_thinking": False}, "reasoning_effort": "none"},
    {"chat_template_kwargs": {"enable_thinking": False}},  # vLLM takes no "none" effort, but takes this
    {},
)
_thinking_off_by_model: dict[str, int] = {}

THINKING_MESSAGE = (
    "The model spent its answer thinking and never wrote the JSON{limit}. Raise AP_LLM_MAX_TOKENS, or turn "
    "thinking off for this model in LM Studio (its Thinking setting), or load a model that doesn't think."
)
CUT_OFF_MESSAGE = "The model's reply was cut off before the JSON was complete{limit}; raise AP_LLM_MAX_TOKENS."


def thinking_off(base_url: str, model: str) -> dict[str, Any]:
    """``extra_body`` for the OpenAI SDK: ask a thinking model not to think (less, or nothing, for a server that
    refused the fields)."""
    fields = THINKING_OFF[_thinking_off_by_model.get(f"{base_url}|{model}", 0)]
    return {"extra_body": dict(fields)} if fields else {}


def refuse_thinking_off(base_url: str, model: str) -> bool:
    """The server refused the thinking fields: send fewer from now on. False when none are left to drop."""
    key = f"{base_url}|{model}"
    level = _thinking_off_by_model.get(key, 0)
    if level + 1 >= len(THINKING_OFF):
        return False
    _thinking_off_by_model[key] = level + 1
    return True


def _field(obj: Any, name: str) -> Any:
    value = getattr(obj, name, None)
    if value is None:
        extra = getattr(obj, "model_extra", None)  # an SDK message keeps fields it doesn't know here
        value = extra.get(name) if isinstance(extra, dict) else None
    return value


@dataclass
class Reply:
    data: dict[str, Any] | None  # the JSON object, None when there is none
    raw: str  # what the model wrote (its answer, else its reasoning)
    problem: str = ""  # why there is no JSON, in words a reviewer can act on
    cut_off: bool = False  # thought or wrote until the token limit: asking again would end the same way


def read_reply(choice: Any, max_tokens: int | None = None) -> Reply:
    """The JSON in a local model's reply: in its answer, else in a reply that is all <think>, else in the reasoning
    the server split off (LM Studio's ``reasoning_content``, Ollama's ``reasoning``). A model that thought until the
    token limit gets a message saying so, not a parse error. A reply stopped at the token limit is never read: the
    JSON in it is unfinished, and an inner object that happens to be complete (one line item) is not the answer."""
    message = getattr(choice, "message", None)
    content = str(_field(message, "content") or "")
    reasoning = str(_field(message, "reasoning_content") or _field(message, "reasoning") or "")
    raw = content if content.strip() else reasoning
    answer = strip_thinking(content)
    # Thinking: split off by the server, in <think> tags, or (a template that opens the tag itself, cut off before
    # closing it) a bare "Thinking Process:" in the answer.
    thought = bool(reasoning.strip()) or answer != content.strip() or content.lstrip().startswith("Thinking")
    limit = f" (it stopped at the {max_tokens:,}-token limit)" if max_tokens else ""
    if getattr(choice, "finish_reason", None) == "length":
        started_json = answer.lstrip().startswith(("{", "```"))
        problem = (CUT_OFF_MESSAGE if started_json or not thought else THINKING_MESSAGE).format(limit=limit)
        return Reply(None, raw, problem, cut_off=True)
    data = parse_json_reply(content)
    if data is None:
        data = last_json_object(content) or last_json_object(reasoning)
    if data is not None:
        return Reply(data, raw)
    if thought and not answer:
        return Reply(None, raw, THINKING_MESSAGE.format(limit=""), cut_off=True)
    return Reply(None, raw, "the reply held no JSON object")


def context_overflow(exc: Exception) -> bool:
    text = str(exc).lower()
    return "context" in text and any(word in text for word in ("length", "overflow", "tokens", "exceed", "window"))


def json_instructions(schema: dict[str, Any]) -> str:
    """For a server without structured output: the schema in words a small model follows."""
    return (
        "\n\n## Reply format\nReply with ONE JSON object and nothing else: no code fences, no explanation. "
        "It must match this JSON Schema exactly (every property present, no others):\n"
        + json.dumps(schema, separators=(",", ":"), ensure_ascii=False)
    )


_THINK_RE = re.compile(r"<think>.*?(?:</think>|\Z)", re.S | re.I)
_FENCE_RE = re.compile(r"^\s*```(?:json|JSON)?\s*$", re.M)


def strip_thinking(text: str) -> str:
    """Reasoning models (Qwen3, DeepSeek-R1) put their scratch work in <think> tags. A template that opens the tag in
    the prompt leaves only its close in the reply: everything before a "</think>" with no "<think>" is reasoning."""
    text = text or ""
    if "</think>" in text and "<think>" not in text.split("</think>", 1)[0]:
        text = text.split("</think>", 1)[1]
    return _THINK_RE.sub("", text).strip()


def _balanced(text: str, start: int) -> str | None:
    depth, in_string, escaped = 0, False, False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


def parse_json_reply(content: str | None) -> dict[str, Any] | None:
    """The JSON object in a model's reply. Tolerates <think> blocks, code fences, prose around it, trailing text and
    trailing commas. Only an outermost object counts (the coding is the outermost one): when the first "{" never
    closes, the reply was cut off and an object inside it (a line item) is not the answer, so there is none."""
    text = _FENCE_RE.sub("", strip_thinking(content or ""))
    start = text.find("{")
    while start != -1:
        chunk = _balanced(text, start)
        if chunk is None:
            return None  # everything after is inside this unfinished object
        for candidate in (chunk, re.sub(r",\s*([}\]])", r"\1", chunk)):
            try:
                data = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict):
                return data
        start = text.find("{", start + len(chunk))  # past it: braces in prose, not the objects inside a broken one
    return None


def last_json_object(text: str | None) -> dict[str, Any] | None:
    """The last complete JSON object in reasoning (the model's final draft, not the first idea), or None."""
    text = _FENCE_RE.sub("", text or "")
    found: dict[str, Any] | None = None
    start = text.find("{")
    while start != -1:
        chunk = _balanced(text, start)
        data = parse_json_reply(chunk) if chunk else None
        if data is not None:
            found = data
        # Past a closed object, valid or broken: its inner ones are not answers. An unclosed "{" may be prose.
        start = text.find("{", start + (len(chunk) if chunk else 1))
    return found


def clip_document(text: str, limit: int) -> tuple[str, bool]:
    """Fit a document into ``limit`` characters for a small context window. The start (vendor, invoice number, the
    first lines) and the end (totals, tax, payment details) matter most, so both are kept, cut at line ends."""
    if not limit or len(text) <= limit:
        return text, False
    marker = "\n\n[… {n:,} characters of the middle left out to fit the model …]\n\n"
    room = max(limit - len(marker) - 10, 0)
    head_room, tail_room = int(room * 0.7), room - int(room * 0.7)
    head = text[:head_room]
    head = head[: head.rfind("\n")] if "\n" in head[head_room // 2 :] else head
    tail = text[len(text) - tail_room :] if tail_room else ""
    if "\n" in tail[: len(tail) // 2]:
        tail = tail[tail.find("\n") + 1 :]
    left_out = len(text) - len(head) - len(tail)
    return head + marker.format(n=left_out) + tail, True


def _short_error(exc: Exception) -> str:
    reason = getattr(exc, "reason", None)
    text = str(reason or exc) or exc.__class__.__name__
    return text.splitlines()[0][:160]
