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

import contextlib
import http.client
import json
import logging
import os
import re
import socket
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from ..core.config import COMPANY_DOMAINS_ENV, _address, _on_this_network, judged_by_lookup, pinned_addresses

if TYPE_CHECKING:
    from ..core.config import LocalLLMSettings, Settings

log = logging.getLogger(__name__)

DETECT_TIMEOUT = 1.5  # seconds: a server on this computer answers in milliseconds, or isn't running
STATUS_TTL_SECONDS = 30.0  # a server that answered is asked again after this long
DOWN_TTL_SECONDS = 10.0  # one that didn't is asked again sooner, so starting LM Studio shows up quickly

_cache: dict[str, tuple[float, ModelStatus]] = {}
_lock = threading.Lock()

# Model ids that are not chat models: never picked to code an invoice.
_NOT_CHAT = ("embed", "rerank", "whisper", "tts", "bge-", "e5-", "ovisocr")  # ovisocr: a page reader, not a chat model
_NOT_CHAT_SQUASHED = ("embed", "rerank", "ovisocr")  # also with the punctuation taken out: "ovis-ocr2", "Ovis_OCR2"
# Families that can look at pictures, for servers that don't say (Ollama, llama.cpp). LM Studio says itself.
_VISION_HINTS = (
    "-vl", "vl-", "_vl", "vision", "llava", "pixtral", "gemma-3", "gemma3", "minicpm-v", "moondream", "internvl",
    "smolvlm", "granite-vision", "qwen2.5-omni", "mistral-small-3.1", "mistral-small-3.2", "llama4",
    "qwen3.5", "qwen3_5",  # every Qwen 3.5 size has a vision encoder
    "gemma-4", "gemma4", "gemma_4",  # every Gemma 4 size can see pages too
)  # fmt: skip

LM_STUDIO_STEPS = (
    "Open LM Studio. Your IT team installs it with the models already in it: the chat model (Gemma 4 12B or "
    "Qwen 3.5 9B, 4-bit) and OvisOCR2. If it isn't on this computer, ask IT.",
    "In LM Studio: Developer tab → Start server.",
    "If no model is loaded: Settings → Reading → For IT → Models in LM Studio → pick the chat model → Load.",
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


def refused_address(url: str) -> str:
    """Why this offline build does not contact the model server at ``url`` ("" when it may): one on this computer or
    the office network always may; one on the internet only with the opt-in (AP_ALLOW_INTERNET=1), like every other
    way out (ap_coder/ops/offline.py). Invoices are never sent to an address in the .env by mistake."""
    from ..ops.offline import INTERNET_ENV, internet_allowed

    host = urlparse(url).netloc
    if _on_this_network(host) or internet_allowed():
        return ""
    name = host.rsplit("@", 1)[-1]
    hostname = (urlparse(url).hostname or "").lower()
    # A dotted name, outside .local/.lan/.home and the company's DNS domains: never looked up (a bare or local name, or
    # a company one, is refused for the internet address it has, and has no domain to add: B-1675).
    if _address(hostname) is None and not judged_by_lookup(host):
        return (f"{name} is on another network: this offline build does not contact it. To use a model server named "
                f"in the company's network, add its domain to {COMPANY_DOMAINS_ENV} in the .env file "
                f"({INTERNET_ENV}=1, for developers, allows any server)")  # fmt: skip
    return (f"{name} is not on this computer or the office network: this offline build does not contact it "
            f"({INTERNET_ENV}=1 allows it)")  # fmt: skip


def refuse_internet(url: str) -> bool:
    """Whether ``url`` is a server on this computer or network (True: asked directly, never through a proxy), or on
    the internet and allowed by the opt-in (False). Refused otherwise (:func:`refused_address`), with
    ``ConnectionRefusedError`` before a byte is sent."""
    refused = refused_address(url)
    if refused:
        raise ConnectionRefusedError(refused)
    return _on_this_network(urlparse(url).netloc)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect is an answer, never followed: it could point anywhere (an address on the internet), and urllib
    would send the request there, with its Authorization header (B-973)."""

    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


class _Pinned:
    """An HTTP(S) handler whose connections go to ``addresses`` in turn, the ones the check judged, never to the name
    looked up again; the name stays the Host header's and the certificate's (SNI) (B-1608)."""

    def __init__(self, addresses: list[str]) -> None:
        super().__init__()
        self.addresses = addresses

    def do_open(self, http_class: Any, req: Any, **kwargs: Any) -> Any:
        *others, last = self.addresses

        def connection(host: str, **options: Any) -> http.client.HTTPConnection:
            conn = http_class(host, **options)

            def pinned(where: tuple[str, int], *args: Any, **kw: Any) -> socket.socket:
                for address in others:
                    with contextlib.suppress(OSError):
                        return socket.create_connection((address, where[1]), *args, **kw)
                return socket.create_connection((last, where[1]), *args, **kw)

            conn._create_connection = pinned  # type: ignore[attr-defined]
            return conn

        return super().do_open(connection, req, **kwargs)  # type: ignore[misc]


class _PinnedHTTP(_Pinned, urllib.request.HTTPHandler):
    pass


class _PinnedHTTPS(_Pinned, urllib.request.HTTPSHandler):
    pass


def opener(local: bool, host: str) -> urllib.request.OpenerDirector:
    """The urllib opener for a model server at ``host`` (a URL's netloc): no proxy for one on this computer or network
    (an office proxy would answer for 127.0.0.1, or hang), connected there to the addresses its check judged
    (``pinned_addresses``, which raises OSError for a name no longer on the office network), and no redirect followed
    (``_NoRedirect``)."""
    handlers: list[Any] = [_NoRedirect()]
    if local:
        handlers.append(urllib.request.ProxyHandler({}))
        addresses = pinned_addresses(host)
        if addresses:
            handlers += [_PinnedHTTP(addresses), _PinnedHTTPS(addresses)]
    return urllib.request.build_opener(*handlers)


def _fetch_json(url: str, api_key: str, timeout: float) -> Any:
    """GET a JSON document (raises OSError / ValueError when the server is down or answers something else, and
    PermissionError, an OSError, for a server on the internet in the offline build: :func:`refused_address`)."""
    refused = refused_address(url)
    if refused:
        raise PermissionError(refused)
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {api_key or 'lm-studio'}"})
    host = urlparse(url).netloc
    with opener(_on_this_network(host), host).open(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", "replace"))


def is_chat_model(model_id: str) -> bool:
    """Not an embedding model nor a page reader, however LM Studio spells it ("ovis-ocr2", "Ovis_OCR2@q8_0")."""
    plain = model_id.lower()
    squashed = re.sub(r"[^a-z0-9]", "", plain)
    return not any(word in plain for word in _NOT_CHAT) and not any(word in squashed for word in _NOT_CHAT_SQUASHED)


# The chat models the team runs in LM Studio, beside CloseDesk (docs/LM_STUDIO.md), in the order AP Coder picks one
# that is downloaded but not loaded: Gemma 4 12B (Google; "google/gemma-4-12b", "gemma-4-12b-it-qat",
# "lmstudio-community/gemma-4-12B-it-QAT-GGUF", Ollama's "gemma4:12b"), then Qwen 3.5 9B ("qwen/qwen3.5-9b",
# "qwen3.5-9b-mlx", "lmstudio-community/Qwen3.5-9B-GGUF", "qwen3.5-9b@q4_k_m", "qwen3.5:9b").
_TEAM_CHAT = (
    re.compile(r"gemma[._-]?4(?![0-9.]).*?(?<![0-9.])12b(?![0-9])"),
    re.compile(r"qwen3[._-]?5(?![0-9.]).*?(?<![0-9.])9b(?![0-9])"),
)


def team_chat_rank(model_id: str) -> int | None:
    """Which of the team's chat models ``model_id`` is (0 = Gemma 4 12B, 1 = Qwen 3.5 9B), None for any other."""
    if not is_chat_model(model_id):
        return None
    plain = model_id.lower()
    return next((rank for rank, pattern in enumerate(_TEAM_CHAT) if pattern.search(plain)), None)


def looks_like_vision(model_id: str) -> bool:
    plain = model_id.lower()
    return any(hint in plain for hint in _VISION_HINTS)


def pick_model(requested: str, ids: list[str], loaded: list[str] | None = None, *, lm_studio: bool = False) -> str:
    """The chat model that suggests GL accounts, found on its own: the one a developer named in AP_LLM_MODEL, else
    the chat model loaded now (whichever app loaded it: CloseDesk shares LM Studio), else, with LM Studio (which loads
    a downloaded model when a request names it), the team's chat model when it is downloaded (Gemma 4 12B, else Qwen 3.5
    9B); never the
    first on LM Studio's list, which with just-in-time loading is every model downloaded, of any size (CloseDesk
    doesn't either). Other servers list only what they serve: their first chat model. Never OvisOCR2 (it reads pages,
    it doesn't chat) nor an embedding model."""
    if requested:
        return requested
    chat_loaded = [m for m in loaded or [] if is_chat_model(m)]
    if chat_loaded:
        return chat_loaded[0]
    if lm_studio:
        ranked = [(rank, m) for m in ids if (rank := team_chat_rank(m)) is not None]
        return min(ranked, key=lambda pair: pair[0])[1] if ranked else ""
    return next((m for m in ids if is_chat_model(m)), "")


@dataclass(frozen=True)
class LMModel:
    """A chat or vision model LM Studio has downloaded: its key, whether it can look at pictures, its loaded instances
    (instance id, context length; 0 when LM Studio doesn't say) and the longest context it supports."""

    key: str
    vision: bool
    instances: tuple[tuple[str, int], ...] = ()
    max_context: int = 0
    reasoning: tuple[str, ...] = ()  # LM Studio 0.4.8+: the reasoning options it takes ("off", "on")

    @property
    def loaded_context(self) -> int:
        """The longest context it is loaded with now (0: not loaded, or not said)."""
        return max((size for _id, size in self.instances), default=0)

    def named(self, name: str) -> bool:
        return name == self.key or any(name == instance for instance, _size in self.instances)


@dataclass(frozen=True)
class LMListing:
    """LM Studio's own model list. ``route``: "v1" (LM Studio 0.4, which loads a model when asked) or "v0" (0.3)."""

    route: str
    models: tuple[LMModel, ...]
    embeddings: tuple[tuple[str, bool], ...] = ()  # embedding models (key, loaded): listed in Settings, never used

    def find(self, name: str) -> LMModel | None:
        return next((model for model in self.models if model.named(name)), None)


def _int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def parse_lm_studio_listing(data: Any, *, keyless: bool = False) -> LMListing | None:
    """LM Studio's ``/api/v1/models`` (0.4: ``models``, each with its ``loaded_instances``) or ``/api/v0/models``
    (0.3: ``data``, each with a ``state``) as a listing of its chat and vision models; embedding models are left out.
    None for an answer that isn't LM Studio's (a server answering with a plain list of models). ``keyless``: a 0.4
    model without a key is kept too (key ""), for its loaded instances (``_lm_studio_listing``)."""
    if not isinstance(data, dict):
        return None
    models: list[LMModel] = []
    embeddings: list[tuple[str, bool]] = []
    if isinstance(data.get("models"), list):
        for item in data["models"]:
            if isinstance(item, dict) and item.get("type") == "embedding" and item.get("key"):
                embeddings.append((str(item["key"]), bool(item.get("loaded_instances"))))
            if not isinstance(item, dict) or item.get("type") not in {"llm", "vlm"} or not (item.get("key") or keyless):
                continue
            caps = item.get("capabilities") if isinstance(item.get("capabilities"), dict) else {}
            reasoning = caps.get("reasoning") if isinstance(caps.get("reasoning"), dict) else {}
            options = reasoning.get("allowed_options") if isinstance(reasoning.get("allowed_options"), list) else []
            instances = []
            for instance in item.get("loaded_instances") or []:
                if isinstance(instance, dict) and instance.get("id"):
                    config = instance.get("config") if isinstance(instance.get("config"), dict) else {}
                    instances.append((str(instance["id"]), _int(config.get("context_length"))))
            models.append(
                LMModel(
                    key=str(item.get("key") or ""),
                    vision=caps.get("vision") is True or item.get("type") == "vlm",
                    instances=tuple(instances),
                    max_context=_int(item.get("max_context_length")),
                    reasoning=tuple(str(option) for option in options),
                )  # fmt: skip
            )
        return LMListing("v1", tuple(models), tuple(embeddings))
    items = data.get("data")
    if isinstance(items, list) and any(isinstance(item, dict) and "state" in item for item in items):
        for item in items:
            if isinstance(item, dict) and item.get("type") == "embeddings" and item.get("id"):
                embeddings.append((str(item["id"]), item.get("state") == "loaded"))
            if not isinstance(item, dict) or not item.get("id") or item.get("type") not in {"llm", "vlm"}:
                continue
            loaded = item.get("state") == "loaded"
            context = _int(item.get("loaded_context_length")) if loaded else 0
            models.append(
                LMModel(
                    key=str(item["id"]),
                    vision=item.get("type") == "vlm",
                    instances=((str(item["id"]), context),) if loaded else (),
                    max_context=_int(item.get("max_context_length")),
                )  # fmt: skip
            )
        return LMListing("v0", tuple(models), tuple(embeddings))
    return None


def _lm_studio_listing(root: str, api_key: str, timeout: float) -> tuple[list[str], set[str]] | None:
    """LM Studio's own model list (``parse_lm_studio_listing``): its loaded chat models, and every model that can see.
    None from other servers."""
    for path in ("/api/v1/models", "/api/v0/models"):
        try:
            data = _fetch_json(root + path, api_key, timeout)
        except (OSError, ValueError):
            continue
        listing = parse_lm_studio_listing(data, keyless=True)
        if listing is None:
            continue
        loaded = [instance for model in listing.models for instance, _size in model.instances]
        seeing = {instance for model in listing.models if model.vision for instance, _size in model.instances}
        return loaded, seeing | {model.key for model in listing.models if model.vision and model.key}
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
    except PermissionError as exc:  # the offline build's refusal (refused_address): whole, it says what to set
        status.error = str(exc)
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


def resolve_provider(settings: Settings) -> str:
    """How invoices are coded: ``local`` always in this offline build, whether or not LM Studio answers right now,
    so an invoice is coded the same way every time: the header, lines and taxes from the local readers, GL accounts
    from what AP approved before and the coding rules, and the chat model's suggestion for a line left over when it
    answers (when it doesn't, the line is left for AP, nothing else changes).

    ``azure`` only when the internet is allowed (AP_ALLOW_INTERNET=1) and Azure OpenAI's endpoint is set. ``off``
    only when code or a test sets ``settings.llm.provider`` to it (the same coding, the chat model never asked).
    Nothing is asked of the server."""
    from ..ops.offline import internet_allowed

    provider = settings.llm.provider
    if provider == "off":
        return "off"
    if provider in {"auto", "azure"} and settings.openai.endpoint and internet_allowed():
        return "azure"
    return "local"


@dataclass(frozen=True)
class ProviderStatus:
    provider: str  # azure / local / off
    ready: bool
    label: str  # short, for a setup checklist: "LM Studio · qwen2.5-7b-instruct"
    detail: str  # one plain sentence


def provider_status(settings: Settings) -> ProviderStatus:
    """The GL coding model's state: ``ready`` when the chat model answers (local) or Azure OpenAI is in use. Not ready
    is no failure: invoices are coded the same way, the lines nothing was learned for left for AP."""
    provider = resolve_provider(settings)
    if provider == "azure":
        label = f"Azure OpenAI · {settings.openai.deployment}"
        return ProviderStatus("azure", True, label, "Coding with Azure OpenAI.")
    if provider == "off":
        return ProviderStatus("off", False, "Off", "The chat model isn't asked: accounts come from AP's history.")
    status = check_server(settings.llm)
    if status.active:
        return ProviderStatus("local", True, f"{status.server} · {status.model}", status.describe())
    return ProviderStatus("local", False, "Local model", status.describe())


SLOTS_ENV = "AP_LLM_SLOTS_DIR"  # the company server's folder of slot locks, which all its processes share (D-111)


class Slots:
    """At most ``limit`` requests to the model server at once (``with slots.held(limit):``). ``limit`` is
    the ``llm_parallel`` setting (AP_LLM_PARALLEL, default 1: strictly one at a time, as LM Studio on a laptop is
    shared with CloseDesk). On the company server (AP_LLM_SLOTS_DIR set by its ``serve``) the limit holds across its
    processes too: each request holds one of ``limit`` lock files there (D-111)."""

    def __init__(self, name: str) -> None:
        self.name = name
        self._cond = threading.Condition()
        self._busy = 0

    @contextlib.contextmanager
    def held(self, limit: int = 1, deadline: float | None = None) -> Iterator[None]:
        """``deadline``: a ``time.monotonic()`` by which a slot must be had, else ``TimeoutError`` (an arriving invoice
        waits only so long, D-168); None: as long as it takes."""
        limit = max(1, int(limit or 1))
        with self._cond:
            while self._busy >= limit:
                left = None if deadline is None else deadline - time.monotonic()
                if left is not None and left <= 0:
                    raise TimeoutError(f"no {self.name} slot free in time")
                self._cond.wait(left)
            self._busy += 1
        lock = None
        try:
            lock = self._across_processes(limit, deadline)
            yield
        finally:
            if lock is not None:
                lock.close()
            with self._cond:
                self._busy -= 1
                self._cond.notify_all()

    def _across_processes(self, limit: int, deadline: float | None = None) -> Any:
        folder = (os.environ.get(SLOTS_ENV) or "").strip()
        if not folder:
            return None
        from ..core.paths import try_lock

        while True:
            for number in range(limit):
                try:
                    lock = try_lock(Path(folder) / f"{self.name}-{number + 1}.lock")
                except OSError:
                    return None  # a folder that can't be written: this process's limit only
                if lock is not None:
                    return lock
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError(f"no {self.name} slot free in time")
            time.sleep(0.2)


# The chat requests, however many invoices are processed at once (``--workers``, the dashboard's jobs): one at a time
# by default, since LM Studio on the pilot tester's Mac also answers CloseDesk, and a burst of parallel requests would
# queue CloseDesk's questions behind AP Coder's. The page reader has its own (page_reader.READ_LOCK): it is another
# model, which LM Studio runs beside the chat model.
CHAT_LOCK = Slots("chat")


# --- A backup model server (D-180) -----------------------------------------------------------------------------------
# Each model server, the chat model's (AP_LLM_BASE_URL) and the page reader's (AP_PAGE_READER_BASE_URL, else the chat
# model's), may have a backup on the company network with the same models (AP_LLM_BACKUP_URL,
# AP_PAGE_READER_BACKUP_URL): the Mac mini beside the Mac Studio. Its breaker (``Failover``, one for each server: the
# two models share one when they share a machine and its backup) sends the calls there once FAILOVER_FAILURES calls in
# a row went unanswered by the primary (refused or dropped, a 5xx, no answer within the call's limit, its model list
# not given), and every call still waiting on the primary is dropped then and asked again on the backup. While the
# backup serves, the primary is asked a one-word question every PROBE_SECONDS in the background; it is taken back after
# PROBES_TO_RETURN answers in a row once the calls have been on the backup ``hold`` seconds (HOLD_SECONDS, twice as
# long each time it fails again within FLAP_SECONDS of being taken back, at most HOLD_MOST), so it never flaps. On the
# company server the breaker's state is shared by all its processes (a file in AP_LLM_SLOTS_DIR): what one finds moves
# them all. Without a backup none of this runs.
CHAT, PAGES = "chat", "pages"  # the chat model's server, the page reader's
PRIMARY, BACKUP = "primary", "backup"
FAILOVER_FAILURES = 2
HOLD_SECONDS = 60.0
HOLD_MOST = 1800.0
FLAP_SECONDS = 600.0
PROBE_SECONDS = 5.0
PROBE_TIMEOUT = 10.0
PROBES_TO_RETURN = 3
HANG_SECONDS = 45.0  # with a backup: a page read whose next word is this late on the primary is a hang
WATCH_SECONDS = 0.25  # how often a call under way on the primary looks whether the calls left it
STATE_SECONDS = 0.5  # a process reads the shared state again after this long
SHARED_ENV = "AP_FAILOVER_SHARED"  # 0: each process on its own (to measure what sharing gives, D-180)
KIND_NAMES = {CHAT: "the chat model's server", PAGES: "the page reader's server"}


class ServerLeft(ConnectionError):
    """A call under way on a model server the calls left for its backup: dropped, to be asked again there."""


def server_failure(exc: BaseException) -> str:
    """Why ``exc`` says the model server didn't answer, in a few words ("" when it answered, if only to refuse): no
    answer in time, refused or dropped, or a failure of its own (a 5xx). A call AP Coder dropped itself
    (``ServerLeft``) is no new failure."""
    import openai

    kinds = {kind.__name__ for kind in type(exc).__mro__}
    if isinstance(exc, ServerLeft):
        return ""
    if isinstance(exc, (openai.APITimeoutError, TimeoutError)) or "TimeoutException" in kinds:
        return "no answer in time"
    if isinstance(exc, (openai.APIConnectionError, ConnectionError)) or "TransportError" in kinds:
        return "not answering"
    status = getattr(exc, "status_code", None)
    if isinstance(status, int) and status >= 500:
        return f"failed ({status})"
    return ""


def probe(base_url: str, api_key: str, model: str = "", timeout: float = PROBE_TIMEOUT) -> tuple[bool, str, float]:
    """Ask the model server at ``base_url`` a one-word question (``model``; without one, for its model list): (whether
    it answered, how, in seconds). Any answer counts, a refusal too (a model it doesn't have: it is running); a 5xx,
    no answer within ``timeout`` or no connection doesn't. Only a server this offline build may ask
    (``refused_address``), with no proxy and no redirect, as every request to it."""
    started = time.monotonic()
    refused = refused_address(base_url)
    if refused:
        return False, refused, 0.0
    base = base_url.rstrip("/")
    try:
        if not model:
            _fetch_json(base + "/models", api_key, timeout)
            return True, "listed its models", time.monotonic() - started
        body = {"model": model, "messages": [{"role": "user", "content": "Reply with OK."}], "max_tokens": 1,
                "temperature": 0}  # fmt: skip
        request = urllib.request.Request(
            base + "/chat/completions", data=json.dumps(body).encode("utf-8"), method="POST",
            headers={"Authorization": f"Bearer {api_key or 'lm-studio'}", "Content-Type": "application/json"},
        )  # fmt: skip
        host = urlparse(base).netloc
        with opener(_on_this_network(host), host).open(request, timeout=timeout) as response:
            response.read()
        return True, f"{model} answered", time.monotonic() - started
    except urllib.error.HTTPError as exc:
        return exc.code < 500, f"answered {exc.code}", time.monotonic() - started
    except (OSError, ValueError) as exc:
        return False, _short_error(exc), time.monotonic() - started


def _fresh() -> dict[str, Any]:
    return {"on": PRIMARY, "since": 0.0, "failures": {PRIMARY: 0, BACKUP: 0}, "left": 0.0, "hold": HOLD_SECONDS,
            "returned": 0.0, "good": 0, "probe_at": 0.0, "why": "", "model": "", "probed": "", "moves": 0}  # fmt: skip


class Failover:
    """The breaker of one model server with a backup (``kind``: CHAT or PAGES): where its calls go now
    (``serving``), told how each went (``answered``, ``failed``), and a call under way on the primary dropped once the
    calls left it (``watched``). The state is a dict (``state``; times are ``time.time()``): ``on`` (PRIMARY or
    BACKUP), ``since`` it went there, ``failures`` in a row of each, ``left`` (when the calls last left the primary),
    ``hold``, ``returned`` (when the primary was last taken back), ``good`` (the primary's probes answered in a row),
    ``probe_at``, ``why`` (what moved the calls, in words), ``model`` (the primary's model the probe asks),
    ``probed`` (how its last probe went) and ``moves``."""

    def __init__(self, kind: str, primary: str, backup: str, api_key: str = "") -> None:
        import hashlib

        self.kind, self.primary, self.backup, self.api_key = kind, primary.rstrip("/"), backup.rstrip("/"), api_key
        self.kinds = {kind}  # the models it serves: the chat model's server, the page reader's, or both
        self.name = hashlib.sha256(f"{self.primary}|{self.backup}".encode()).hexdigest()[:12]
        self._lock = threading.Lock()
        self._state = _fresh()
        self._read_at = float("-inf")
        self._prober: threading.Thread | None = None

    @property
    def title(self) -> str:
        return KIND_NAMES[self.kind] if len(self.kinds) == 1 and self.kind in KIND_NAMES else "the model server"

    # --- Its state, shared by the company server's processes

    def _file(self) -> Path | None:
        folder = (os.environ.get(SLOTS_ENV) or "").strip()
        if not folder or (os.environ.get(SHARED_ENV) or "").strip() == "0":
            return None
        return Path(folder) / f"failover-{self.name}.json"

    @staticmethod
    def _read(path: Path) -> dict[str, Any] | None:
        try:
            found = json.loads(path.read_text(encoding="utf-8") or "null")
        except FileNotFoundError:
            return _fresh()
        except (OSError, ValueError):
            return None  # being written by another process: the last one read stands
        return {**_fresh(), **found} if isinstance(found, dict) else None

    def state(self) -> dict[str, Any]:
        """The state now (read again from the shared file at most every STATE_SECONDS)."""
        path = self._file()
        with self._lock:
            if path is not None and time.monotonic() - self._read_at >= STATE_SECONDS:
                found = self._read(path)
                if found is not None:
                    self._state = found
                self._read_at = time.monotonic()
            return json.loads(json.dumps(self._state))

    def _update(self, change: Any) -> dict[str, Any]:
        """``change(state)`` applied to the state as it is now, then kept (in the shared file, under its lock)."""
        path = self._file()
        with self._lock:
            lock = None
            if path is not None:
                from ..core.paths import try_lock

                deadline = time.monotonic() + 2.0
                while lock is None and time.monotonic() < deadline:
                    try:
                        lock = try_lock(path.with_suffix(".lock"))
                    except OSError:
                        break
                    if lock is None:
                        time.sleep(0.01)
                found = self._read(path) if lock is not None else None
                if found is not None:
                    self._state = found
            try:
                change(self._state)
                if lock is not None and path is not None:
                    with contextlib.suppress(OSError):
                        path.write_text(json.dumps(self._state), encoding="utf-8")
                self._read_at = time.monotonic()
            finally:
                if lock is not None:
                    lock.close()  # type: ignore[attr-defined]
            return json.loads(json.dumps(self._state))

    # --- Where the calls go

    def role(self, base_url: str) -> str | None:
        base = (base_url or "").rstrip("/")
        return PRIMARY if base == self.primary else BACKUP if base == self.backup else None

    def serving(self) -> str:
        """The base URL the calls go to now: the primary, or the backup while the breaker has moved them there (its
        probe of the primary then runs in this process too)."""
        if self.state()["on"] == BACKUP:
            self._probe_in_background()
            return self.backup
        return self.primary

    def left_since(self) -> float:
        """When the calls last left the primary for its backup (``time.time()``; 0: never)."""
        return float(self.state()["left"] or 0.0)

    def answered(self, base_url: str, model: str = "") -> None:
        """A call to ``base_url`` was answered (``model``: the model that answered, which the probe then asks)."""
        role = self.role(base_url)
        if role is None:
            return
        s = self.state()
        if not s["failures"].get(role) and not (role == PRIMARY and model and model != s["model"]):
            return

        def change(s: dict[str, Any]) -> None:
            s["failures"][role] = 0
            if role == PRIMARY and model:
                s["model"] = model

        self._update(change)

    def failed(self, base_url: str, why: str) -> None:
        """A call to ``base_url`` went unanswered (``why``, in a few words): the calls go to the backup once the
        primary failed FAILOVER_FAILURES times in a row."""
        role = self.role(base_url)
        if role is None:
            return
        moved: list[str] = []

        def change(s: dict[str, Any]) -> None:
            if role != s["on"]:  # a late failure of a server the calls left already
                return
            s["failures"][role] = int(s["failures"].get(role) or 0) + 1
            if role != PRIMARY or s["failures"][role] < FAILOVER_FAILURES:
                return
            now = time.time()
            again = s["returned"] and now - s["returned"] < FLAP_SECONDS
            s.update(on=BACKUP, since=now, left=now, good=0, probe_at=now + PROBE_SECONDS, moves=s["moves"] + 1,
                     why=f"{s['failures'][role]} calls in a row: {why}",
                     hold=min(HOLD_MOST, s["hold"] * 2) if again else HOLD_SECONDS)  # fmt: skip
            s["failures"] = {PRIMARY: 0, BACKUP: 0}
            moved.append(s["why"])

        self._update(change)
        if moved:
            log.warning("%s at %s didn't answer (%s): its calls go to the backup at %s (D-180)",
                        self.title, self.primary, moved[0], self.backup)  # fmt: skip
            self._probe_in_background()

    # --- A call under way on the primary

    def watched(self, base_url: str, call: Any) -> Any:
        """``call()``'s answer. On the primary the call is made in a thread of its own, and ``ServerLeft`` raised as
        soon as the calls leave the primary (another call's finding, maybe another process's): the call is left to
        end on its own, and what it answers then is closed unread."""
        if self.role(base_url) != PRIMARY:
            return call()
        box: dict[str, Any] = {}
        done, guard = threading.Event(), threading.Lock()

        def run() -> None:
            try:
                value = call()
            except BaseException as exc:  # noqa: BLE001 - handed to the caller as it is
                with guard:
                    box["error"] = exc
            else:
                with guard:
                    dropped = box.get("dropped")
                    box["value"] = value
                if dropped:
                    with contextlib.suppress(Exception):
                        value.close()
            finally:
                done.set()

        threading.Thread(target=run, name=f"ap-coder-{self.kind}-call", daemon=True).start()
        while not done.wait(WATCH_SECONDS):
            if self.state()["on"] != PRIMARY:  # (moved before it began, too: it was sent on the way out)
                with guard:
                    box["dropped"] = "value" not in box and "error" not in box  # (answered just now: taken)
                if box["dropped"]:
                    raise ServerLeft(f"{self.title} at {self.primary} stopped answering: its "
                                     f"backup at {self.backup} is asked instead")  # fmt: skip
                break
        if "error" in box:
            raise box["error"]
        return box["value"]

    # --- Taking the primary back

    def _probe_in_background(self) -> None:
        with self._lock:
            if self._prober is not None and self._prober.is_alive():
                return
            self._prober = threading.Thread(target=self._probe_loop, name=f"ap-coder-{self.kind}-probe", daemon=True)
            self._prober.start()

    def _probe_loop(self) -> None:
        while self.state()["on"] == BACKUP:
            if self._probe_due():
                ok, how, seconds = probe(self.primary, self.api_key, self.state()["model"], PROBE_TIMEOUT)
                self._probed(ok, f"{how} in {seconds:.1f} s" if ok else how)
            time.sleep(min(1.0, PROBE_SECONDS))

    def _probe_due(self) -> bool:
        """Whether this process probes the primary now (one process at a time on the company server)."""
        if time.time() < self.state()["probe_at"]:
            return False
        mine: list[bool] = []

        def change(s: dict[str, Any]) -> None:
            now = time.time()
            if s["on"] == BACKUP and now >= s["probe_at"]:
                s["probe_at"] = now + PROBE_TIMEOUT + PROBE_SECONDS  # (set again once this one is over)
                mine.append(True)

        self._update(change)
        return bool(mine)

    def _probed(self, ok: bool, how: str) -> None:
        back: list[bool] = []

        def change(s: dict[str, Any]) -> None:
            if s["on"] != BACKUP:
                return
            now = time.time()
            s["good"] = s["good"] + 1 if ok else 0
            s["probe_at"], s["probed"] = now + PROBE_SECONDS, how
            failing = s["failures"].get(BACKUP, 0) >= FAILOVER_FAILURES  # the backup fails too: no hold
            if s["good"] >= PROBES_TO_RETURN and (now - s["since"] >= s["hold"] or failing):
                s.update(on=PRIMARY, since=now, returned=now, good=0, why="")
                s["failures"] = {PRIMARY: 0, BACKUP: 0}
                back.append(True)

        self._update(change)
        if back:
            log.warning("%s at %s answers again: its calls go back to it from the backup (D-180)",
                        self.title, self.primary)  # fmt: skip


_FAILOVERS: dict[tuple[Any, ...], Failover] = {}
_FAILOVERS_LOCK = threading.Lock()


def failover(kind: str, primary: str, backup: str, api_key: str = "") -> Failover | None:
    """The breaker of the model server ``primary`` with its ``backup`` (``kind``: the model it is asked for), one in
    each process for each server; None without a backup (or with one that is the primary itself): every call to the
    primary, as before."""
    primary, backup = (primary or "").rstrip("/"), (backup or "").rstrip("/")
    if not backup or backup == primary:
        return None
    key = (os.getpid(), primary, backup)  # one a server: both models share it on one machine
    with _FAILOVERS_LOCK:
        found = _FAILOVERS.get(key)
        if found is None:
            found = _FAILOVERS[key] = Failover(kind, primary, backup, api_key)
        found.kinds.add(kind)
        return found


def forget_failovers() -> None:
    """Every breaker of this process forgotten (tests; a shared state stays where it is kept)."""
    with _FAILOVERS_LOCK:
        _FAILOVERS.clear()


def chat_failover(llm: LocalLLMSettings) -> Failover | None:
    return failover(CHAT, llm.base_url, llm.backup_url, llm.api_key)


def serving_llm(llm: LocalLLMSettings) -> LocalLLMSettings:
    """``llm`` pointed at the chat model's server in use now: its backup while the breaker has moved the calls there,
    else ``llm`` itself (always, without a backup)."""
    found = chat_failover(llm)
    if found is None:
        return llm
    base = found.serving()
    if base == llm.base_url.rstrip("/"):
        return llm
    from dataclasses import replace

    return replace(llm, base_url=base)


NO_MODEL_MESSAGE = (
    "No AI model answers to code this invoice. Start LM Studio's server with a chat model loaded; invoices are still "
    "read and coded from AP's history without it."
)


# --- Talking to it --------------------------------------------------------------------------------------------


def _http_library(client_class: type) -> Any:
    """The HTTP library the OpenAI SDK's client ``client_class`` is built on: httpx, or httpx2 under newer SDKs."""
    import importlib

    base = next(c for c in client_class.__mro__ if c.__name__ == "Client" and c.__module__ != client_class.__module__)
    return importlib.import_module(base.__module__.split(".")[0])


def _refusing_transport(client_class: type, reason: str) -> Any:
    """A transport for the OpenAI SDK's HTTP client (``client_class``) whose every request fails as a connection
    error, before anything is sent."""
    http = _http_library(client_class)

    class Refused(http.BaseTransport):
        def handle_request(self, request: Any) -> Any:
            raise http.ConnectError(reason, request=request)

    return Refused()


def _pinning_transport(client_class: type) -> Any:
    """A transport for the OpenAI SDK's HTTP client (``client_class``) that sends each request to the addresses its
    host's check judged (``pinned_addresses``), never where the name looked up again would lead (B-1608): the URL names
    the address, the Host header and the certificate's check (SNI) keep the name. A name no longer on the office
    network fails as a connection error, before anything is sent."""
    import openai

    http = _http_library(client_class)
    inner = http.HTTPTransport(trust_env=False, limits=openai.DEFAULT_CONNECTION_LIMITS)  # the client's own

    class Pinned(http.BaseTransport):
        def handle_request(self, request: Any) -> Any:
            url = request.url
            try:
                addresses = pinned_addresses(url.netloc.decode("ascii"))
            except OSError as exc:
                raise http.ConnectError(str(exc), request=request) from exc
            extensions = dict(request.extensions)
            if url.scheme == "https":
                extensions["sni_hostname"] = url.raw_host.decode("ascii")
            for number, address in enumerate(addresses, 1):
                pinned = http.Request(request.method, url.copy_with(host=address), headers=request.headers,
                                      stream=request.stream, extensions=extensions)  # fmt: skip
                try:
                    return inner.handle_request(pinned)
                except (http.ConnectError, http.ConnectTimeout):  # nothing was sent: the next address
                    if number == len(addresses):
                        raise
            return inner.handle_request(request)  # an address or localhost: as written

        def close(self) -> None:
            inner.close()

    return Pinned()


_HTTP_CLIENTS: dict[tuple[Any, ...], Any] = {}  # one HTTP client a model server and way to it, in each process
_HTTP_CLIENTS_LOCK = threading.Lock()


def _http_client(key: tuple[Any, ...], make: Any) -> Any:
    """The HTTP client for ``key`` (a server, and how it is reached), made once in this process and shared by every
    request to it from any thread: its connections stay open for the next request. A client made for each page read
    or invoice coded left its connection open until Python's garbage collector came round (B-1760)."""
    key = (os.getpid(), *key)  # (a process forked from this one makes its own)
    with _HTTP_CLIENTS_LOCK:
        client = _HTTP_CLIENTS.get(key)
        if client is None or client.is_closed:
            client = _HTTP_CLIENTS[key] = make()
        return client


def build_local_client(llm: LocalLLMSettings) -> Any:
    from openai import DefaultHttpxClient, OpenAI

    refused = refused_address(llm.base_url)
    host = urlparse(llm.base_url).netloc
    server = _root(llm.base_url)
    # No redirect is followed (the SDK's own client follows them): it could point at the internet (B-973).
    if refused:  # every request fails like a server that is not running (APIConnectionError): nothing is sent
        http_client = _http_client((server, "refused", refused), lambda: DefaultHttpxClient(
            transport=_refusing_transport(DefaultHttpxClient, refused), follow_redirects=False))  # fmt: skip
    elif _on_this_network(host):
        # A server on this computer or network: never through an office proxy (it would answer for 127.0.0.1); one
        # reached by its name, at the addresses its check judged (B-1608).
        judged = judged_by_lookup(host)
        pinned = (lambda: {"transport": _pinning_transport(DefaultHttpxClient)}) if judged else dict
        http_client = _http_client((server, "network", judged), lambda: DefaultHttpxClient(
            trust_env=False, follow_redirects=False, **pinned()))  # fmt: skip
    else:
        http_client = _http_client((server, "internet"), lambda: DefaultHttpxClient(follow_redirects=False))
    return OpenAI(
        base_url=llm.base_url,
        api_key=llm.api_key or "lm-studio",
        timeout=llm.timeout_seconds,
        # Never sent again by the SDK itself: a local server that failed is busy (CloseDesk shares it) or down, not
        # rate limited, and a request sent again after a ten-minute timeout only doubles the wait (CloseDesk doesn't).
        max_retries=0,
        http_client=http_client,
    )


# Structured output, best first. LM Studio takes json_schema; Ollama and llama.cpp take json_object too; anything
# else gets the schema in the prompt and its reply is parsed.
RESPONSE_MODES = ("json_schema", "json_object", "text")
_mode_by_model: dict[str, int] = {}


def start_mode(base_url: str, model: str) -> int:
    return _mode_by_model.get(f"{base_url}|{model}", 0)


def remember_mode(base_url: str, model: str, index: int) -> None:
    _mode_by_model[f"{base_url}|{model}"] = index


FORMAT_WORDS = ("response_format", "json_schema", "json schema", "json_object", "grammar")


def rejects_format(exc: Exception) -> bool:
    """A server refusing ``response_format`` (rather than failing): a 415/501, or a 400/422/500 that names it. Any
    other 400 (LM Studio couldn't load the model, say) is not one: the schema would be dropped, and remembered as
    refused, over an error that has nothing to do with it."""
    status = getattr(exc, "status_code", None)
    text = str(exc).lower()
    if status in {415, 501}:
        return not context_overflow(exc)
    return status in {400, 422, 500} and not context_overflow(exc) and any(w in text for w in FORMAT_WORDS)


# The fields AP sends that a strict server may not know, as its refusal names them: the thinking switch
# (reasoning_effort, chat_template_kwargs.enable_thinking) and the page reader's sampling on a second reading.
THINKING_FIELDS = ("reasoning", "thinking", "chat_template_kwargs")
SAMPLING_FIELDS = ("top_k", "presence_penalty")


def refuses_extra_fields(exc: Exception, names: tuple[str, ...] = THINKING_FIELDS + SAMPLING_FIELDS) -> bool:
    """A strict server refusing a field it doesn't know (the thinking switch), not ``response_format``: a 400 or 422
    whose message names one of ``names``. Any other 400 (LM Studio couldn't load the model, say) is not one: asking
    again without the switch would only hide the real error."""
    if getattr(exc, "status_code", None) not in {400, 422} or context_overflow(exc):
        return False
    text = str(exc).lower()
    if any(word in text for word in ("response_format", "json_schema", "json_object", "grammar")):
        return False
    return any(name in text for name in names)


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


# The AI reviewer (verifier.py) is the one call that asks a model TO think: it checks a whole invoice, and a model that
# reasons first catches more. The same fields, the other way round; a server that refuses them gets fewer.
THINKING_ON: tuple[dict[str, Any], ...] = (
    {"chat_template_kwargs": {"enable_thinking": True}, "reasoning_effort": "high"},
    {"chat_template_kwargs": {"enable_thinking": True}},
    {},
)
_thinking_on_by_model: dict[str, int] = {}


def thinking_on(base_url: str, model: str) -> dict[str, Any]:
    """``extra_body`` asking a thinking model to think (less, or nothing, for a server that refused the fields)."""
    fields = THINKING_ON[_thinking_on_by_model.get(f"{base_url}|{model}", 0)]
    return {"extra_body": dict(fields)} if fields else {}


def refuse_thinking_on(base_url: str, model: str) -> bool:
    """The server refused the thinking fields: send fewer from now on. False when none are left to drop."""
    key = f"{base_url}|{model}"
    level = _thinking_on_by_model.get(key, 0)
    if level + 1 >= len(THINKING_ON):
        return False
    _thinking_on_by_model[key] = level + 1
    return True


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


UNCLOSED_MESSAGE = "the reply's JSON was cut off before it closed"


def _unclosed(answer: str) -> bool:
    """An answer that starts a JSON object and never closes it: cut off, whatever the server said."""
    text = _FENCE_RE.sub("", answer).lstrip()
    return text.startswith("{") and _balanced(text, 0) is None


def read_reply(choice: Any, max_tokens: int | None = None, *, strict: bool = False) -> Reply:
    """The JSON in a local model's reply: in its answer, else in a reply that is all <think>, else in the reasoning
    the server split off (LM Studio's ``reasoning_content``, Ollama's ``reasoning``). A model that thought until the
    token limit gets a message saying so, not a parse error. A reply stopped at the token limit is never read: the
    JSON in it is unfinished, and an inner object that happens to be complete (one line item) is not the answer; nor
    is one in an answer whose first object never closes. ``strict`` (the AI reviewer, whose answer can approve an
    invoice): only the answer itself counts, as one JSON object, never a draft in the reasoning, in a thought block
    or in prose."""
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
    # Strict, the answer is one JSON object (after a closed thought block) or nothing: an object found in prose may
    # be a draft in a thought the template opened and never closed ("Thinking Process: ... {draft} ... wait").
    data = _whole_object(content) if strict else parse_json_reply(content)
    if data is None and _unclosed(answer):
        return Reply(None, raw, UNCLOSED_MESSAGE)
    if data is None and not strict:
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
# Gemma 4 writes its thinking in a thought channel instead: "<|channel>thought ... <channel|>".
_CHANNEL_RE = re.compile(r"<\|channel>thought.*?(?:<channel\|>|\Z)", re.S)
_FENCE_RE = re.compile(r"^\s*```(?:json|JSON)?\s*$", re.M)


def strip_thinking(text: str) -> str:
    """Reasoning models (Qwen3, DeepSeek-R1) put their scratch work in <think> tags, Gemma 4 in a thought channel. A
    template that opens the tag or channel in the prompt leaves only its close in the reply: everything before a
    "</think>" with no "<think>" (a "<channel|>" with no "<|channel>") is reasoning."""
    text = text or ""
    for opening, closing in (("<think>", "</think>"), ("<|channel>", "<channel|>")):
        if closing in text and opening not in text.split(closing, 1)[0]:
            text = text.split(closing, 1)[1]
    return _CHANNEL_RE.sub("", _THINK_RE.sub("", text)).strip()


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


def _whole_object(content: str) -> dict[str, Any] | None:
    """The reply as one JSON object, as it is or after a leading thought block (or the stray close of one the template
    opened), before any markup is taken out of it. None when it is not one."""
    candidates = [content]
    for opening, closing in (("<think>", "</think>"), ("<|channel>", "<channel|>")):
        before, found, after = content.partition(closing)
        if found and (before.lstrip().startswith(opening) or opening not in before):
            candidates.append(after)
    for candidate in candidates:
        text = _FENCE_RE.sub("", candidate).strip()
        if not text.startswith("{"):
            continue
        for attempt in (text, re.sub(r",\s*([}\]])", r"\1", text)):
            try:
                data = json.loads(attempt)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict):
                return data
    return None


def parse_json_reply(content: str | None) -> dict[str, Any] | None:
    """The JSON object in a model's reply. Tolerates <think> blocks, code fences, prose around it, trailing text and
    trailing commas. Only an outermost object counts (the coding is the outermost one): when the first "{" never
    closes, the reply was cut off and an object inside it (a line item) is not the answer, so there is none. A reply
    that is one JSON object (after a leading thought block) is taken as it is: a "<think>" inside one of its strings
    is text, not thinking."""
    whole = _whole_object(content or "")
    if whole is not None:
        return whole
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
