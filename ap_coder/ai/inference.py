"""The chat model: an invoice coded whole (``InvoiceCoder.code``), and the local model's two short jobs.

``InvoiceCoder.code`` asks Azure OpenAI with strict Structured Outputs (when the internet is allowed), or a local
OpenAI-compatible model (LM Studio, Ollama) through :mod:`ap_coder.ai.local_llm`, which asks for the same schema and
falls back to parsing a looser reply (the doctor's dry run). In the app, the local readers code the invoice, and the
local model only suggests the GL account of the lines AP's history doesn't cover (``suggest_accounts``) and reads the
header from the page images as one more reader (``read_header``). ``CodingError`` is every failure, ``temporary``
when it is the model server's.
"""

from __future__ import annotations

import functools
import hashlib
import json
import logging
import math
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from ..core.config import LocalLLMSettings, Settings
from ..core.paths import prune_kept
from ..core.reference_data import ReferenceData
from ..core.schema import SCHEMA_NAME, InvoiceCoding, build_json_schema, response_format
from . import local_llm
from .extraction import ExtractionResult
from .imaging import PageImage
from .prompts import VISION_NOTE, build_system_prompt

log = logging.getLogger(__name__)

# How a line coded by the local chat model alone says so (``pipeline.InvoicePipeline._local_accounts``): a person
# checks it, it is never approved by touchless processing (``capture.workflow.invoice_gates``).
MODEL_SUGGESTED = "Suggested by the local model"
# A line's account chosen by the chat model coding the whole invoice (Azure OpenAI): a guess too (B-894).
CHAT_SUGGESTED = "Suggested by the chat model"
_REASONING_PREFIXES = ("o1", "o3", "o4", "gpt-5")
_TEXT_ONLY_PREFIXES = ("o1-mini", "o3-mini", "gpt-35", "gpt-3.5")


class CodingError(RuntimeError):
    """The model could not produce a valid coding for the invoice. ``temporary``: the model server's trouble (it
    stopped, or gave no answer in time), not the invoice's: asking again later may well work."""

    def __init__(self, message: str = "", *, temporary: bool = False) -> None:
        super().__init__(message)
        self.temporary = temporary


class ChatTimeout(CodingError):
    """The chat model gave no answer in the time it was given (a slot to ask in, then the answer)."""

    def __init__(self, message: str = "") -> None:
        super().__init__(message, temporary=True)


def _timed_out(exc: Exception) -> bool:
    import openai

    return isinstance(exc, openai.APITimeoutError)


@dataclass(frozen=True)
class ModelProfile:
    """Capabilities of the deployed model, so new models can be swapped in by config."""

    name: str
    supports_vision: bool
    reasoning: bool

    @classmethod
    def resolve(cls, model_name: str, supports_vision: bool | None = None) -> ModelProfile:
        name = model_name.lower()
        reasoning = name.startswith(_REASONING_PREFIXES)
        vision = not name.startswith(_TEXT_ONLY_PREFIXES)
        if supports_vision is not None:
            vision = supports_vision
        return cls(name=name, supports_vision=vision, reasoning=reasoning)


@dataclass
class CodingResult:
    coding: InvoiceCoding
    raw_response: str
    model: str
    usage: dict[str, Any] = field(default_factory=dict)
    attempts: int = 1
    images_attached: int = 0
    # The local reader's weak match for each line it left blank: the line's place on the invoice (from 1) -> account
    # (``offline_coder.code_from_capture``). Never put in the line; compared with the chat model's answer.
    hints: dict[int, str] = field(default_factory=dict)
    # With whole account strings (a chart of accounts imported, or a journal loaded): the accounts offered for each
    # line left blank, its place on the invoice (from 1) -> ``suggest.candidates``, the likeliest first. The chat
    # model chooses among them, never from the whole chart (D-212).
    offered: dict[int, list[Any]] = field(default_factory=dict)


def build_openai_client(settings: Settings) -> Any:
    from openai import AzureOpenAI

    oai = settings.openai
    if not oai.endpoint:
        raise RuntimeError("AZURE_OPENAI_ENDPOINT is not set")
    auth: dict[str, Any]
    if oai.api_key:
        auth = {"api_key": oai.api_key}
    else:
        from ..ops.offline import import_azure  # Azure's packages are an optional extra: a plain message without them

        identity = import_azure("azure.identity")
        auth = {
            "azure_ad_token_provider": identity.get_bearer_token_provider(
                identity.DefaultAzureCredential(), "https://cognitiveservices.azure.com/.default"
            )
        }
    return AzureOpenAI(
        azure_endpoint=oai.endpoint,
        api_version=oai.api_version,
        timeout=oai.timeout_seconds,
        max_retries=oai.max_retries,  # SDK retries 429/5xx with backoff, honouring Retry-After
        **auth,
    )


# Room for a model asked to think first (the AI reviewer): its reasoning, then the JSON.
THINKING_TOKENS = 12000


class InvoiceCoder:
    """Turns an :class:`ExtractionResult` into a schema-valid :class:`InvoiceCoding`."""

    def __init__(
        self,
        settings: Settings,
        reference: ReferenceData,
        client: Any | None = None,
        max_repair_attempts: int = 1,
        provider: str | None = None,
    ) -> None:
        self.settings = settings
        self.reference = reference
        self._client = client
        self.max_repair_attempts = max_repair_attempts
        # "azure", "local" or "off". A client handed in is Azure's unless the settings say local (tests, doctor);
        # otherwise it is decided when the first invoice is coded, so building a coder never waits on a server.
        if provider is None and client is not None:
            provider = "local" if settings.llm.provider == "local" else "azure"
        self._provider = provider
        self._local_model = ""
        # The local model's server, its client and its model, kept by server: the chat model's backup has its own
        # (D-180), and a model LM Studio swapped is asked for no more (B-1843).
        self._clients: dict[str, Any] = {}
        self._models: dict[str, tuple[str, ModelProfile]] = {}
        self.profile = ModelProfile.resolve(
            settings.openai.model_name or settings.openai.deployment,
            settings.openai.supports_vision,
        )
        self.schema = build_json_schema(
            reference,
            constrain_codes=settings.engine.constrain_codes,
        )
        self.system_prompt = build_system_prompt(reference)

    @property
    def provider(self) -> str:
        if self._provider is None:
            provider = local_llm.resolve_provider(self.settings)
            if provider == "off":  # asked again next time: LM Studio may be started meanwhile
                return provider
            self._provider = provider
        return self._provider

    @property
    def client(self) -> Any:
        if self._client is None:
            if self.provider == "local":
                return self._local_client(self._llm())
            if self.provider == "azure":
                self._client = build_openai_client(self.settings)
            else:
                raise CodingError(local_llm.NO_MODEL_MESSAGE)
        return self._client

    def _llm(self) -> LocalLLMSettings:
        """The local model's settings, pointed at the server in use: its backup while the breaker moved the calls
        there (D-180)."""
        return local_llm.serving_llm(self.settings.llm)

    def _local_client(self, llm: LocalLLMSettings) -> Any:
        """The client for the local server ``llm`` names (the one handed in, when one was)."""
        if self._client is not None:
            return self._client
        client = self._clients.get(llm.base_url)
        if client is None:
            client = self._clients[llm.base_url] = local_llm.build_local_client(llm)
        return client

    def _prepare_local(self) -> str:
        """The local model to ask on the server in use, and what it can do (vision): found once per server, and again
        when the server lists another one (LM Studio swapped it: the old one would be asked in vain, B-1843)."""
        llm = self._llm()
        status = local_llm.check_server(llm)
        model = llm.model or status.model
        kept = self._models.get(llm.base_url)
        if kept and (not model or model == kept[0]):  # (a server not answering just now: the call will say)
            self._local_model, self.profile = kept
            return kept[0]
        if not model:
            if local_llm.unlisted(local_llm.chat_failover(self.settings.llm), status):  # moved to the backup (D-180)
                return self._prepare_local()
            if status.reachable:
                raise CodingError(
                    f"{status.server_title} is running but no model is loaded. Load Gemma 4 12B (or Qwen "
                    "3.5 9B) in LM Studio (docs/LM_STUDIO.md), or name a model in AP_LLM_MODEL."
                )
            raise CodingError(
                f"The local model isn't answering at {llm.base_url}. In LM Studio: load a model, then "
                "Developer tab → Start server."
            )
        vision = local_llm.sees_pages(llm, status, model)
        self.profile = ModelProfile(name=model.lower(), supports_vision=vision, reasoning=False)
        self._local_model = model
        self._models[llm.base_url] = (model, self.profile)
        return model

    def wants_images(self) -> bool:
        """Whether page images should be rendered for this coder. Azure: AP_VISION. A local model: AP_LLM_VISION
        on, or auto when the loaded model can see pages."""
        if self.provider != "local":
            return self.settings.engine.vision
        try:
            self._prepare_local()
        except CodingError:
            return False
        return self.profile.supports_vision

    def build_messages(
        self,
        extraction: ExtractionResult,
        images: list[PageImage] | None = None,
        history: str = "",
    ) -> list[dict[str, Any]]:
        max_chars = self.settings.engine.max_document_chars
        if self._provider == "local" and self.settings.llm.max_prompt_chars:
            # A small context window: keep the start and the end of a long document, not just the start.
            limit = min(max_chars or self.settings.llm.max_prompt_chars, self.settings.llm.max_prompt_chars)
            content, cut = local_llm.clip_document(extraction.content, limit)
            if cut:
                extraction = replace(extraction, content=content)
            max_chars = None
        payload = extraction.to_prompt_payload(max_chars)
        if history:
            payload = f"{history}\n\n{payload}"
        if images and not self.profile.supports_vision:
            log.warning("Model %s is not vision-capable; ignoring page images", self.profile.name)
            images = None

        if images:
            content: Any = [{"type": "text", "text": f"{payload}\n\n{VISION_NOTE}"}]
            content += [
                {"type": "image_url", "image_url": {"url": img.to_data_url(), "detail": "high"}} for img in images
            ]
        else:
            content = payload
        return [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": content},
        ]

    def _request_kwargs(self) -> dict[str, Any]:
        oai = self.settings.openai
        kwargs: dict[str, Any] = {
            "model": oai.deployment,
            "response_format": response_format(self.schema),
        }
        if oai.max_output_tokens:
            kwargs["max_completion_tokens"] = oai.max_output_tokens
        if self.profile.reasoning:
            if oai.reasoning_effort:
                kwargs["reasoning_effort"] = oai.reasoning_effort
        else:
            if oai.temperature is not None:
                kwargs["temperature"] = oai.temperature
            if oai.seed is not None:
                kwargs["seed"] = oai.seed
        return kwargs

    def code(
        self,
        extraction: ExtractionResult,
        images: list[PageImage] | None = None,
        history: str = "",
    ) -> CodingResult:
        """``history`` is the formatted reviewer history (``memory.format_examples``) for this invoice."""
        provider = self.provider
        if provider == "off":
            raise CodingError(local_llm.NO_MODEL_MESSAGE)
        if provider == "local":
            return self._code_local(extraction, images, history)
        messages = self.build_messages(extraction, images, history)
        images_attached = len(images) if images and self.profile.supports_vision else 0
        usage_total: dict[str, int] = {}
        last_error: Exception | None = None

        for attempt in range(1, self.max_repair_attempts + 2):
            response = self.client.chat.completions.create(messages=messages, **self._request_kwargs())
            _accumulate_usage(usage_total, getattr(response, "usage", None))
            choice = response.choices[0]
            message = choice.message

            if getattr(message, "refusal", None):
                raise CodingError(f"Model refused the request: {message.refusal}")
            if choice.finish_reason == "length":
                raise CodingError(
                    "Model output was truncated (finish_reason=length); raise AZURE_OPENAI_MAX_OUTPUT_TOKENS"
                )
            if choice.finish_reason == "content_filter":
                raise CodingError("Response blocked by the Azure OpenAI content filter")

            raw = message.content or ""
            try:
                coding = InvoiceCoding.model_validate(json.loads(raw))
            except (json.JSONDecodeError, ValidationError) as exc:
                last_error = exc
                log.warning("Attempt %d produced an invalid coding: %s", attempt, exc)
                # Structured Outputs fixes the shape; this repairs value-level
                # problems (bad date format, confidence out of range, ...).
                messages = [
                    *messages,
                    {"role": "assistant", "content": raw},
                    {
                        "role": "user",
                        "content": f"The JSON above failed validation:\n{exc}\nReturn the corrected JSON object.",
                    },
                ]
                continue

            return CodingResult(
                coding=coding,
                raw_response=raw,
                model=getattr(response, "model", None) or self.settings.openai.deployment,
                usage=usage_total,
                attempts=attempt,
                images_attached=images_attached,
            )

        raise CodingError(f"Model output failed validation after {attempt} attempt(s): {last_error}")

    # --- A local model (LM Studio, Ollama) -------------------------------------------------------------------

    def _local_kwargs(self, model: str, mode: str, schema: dict[str, Any] | None = None, *,
                      thinking: bool = False, base: str = "") -> dict[str, Any]:  # fmt: skip
        """No max_completion_tokens: every local server takes max_tokens. Thinking is asked to be off (in the
        fields each kind of server reads; see local_llm.THINKING_OFF), or on for ``thinking`` (the AI reviewer:
        local_llm.THINKING_ON), with room for it (``THINKING_TOKENS``), as the server at ``base`` (default: the chat
        model's) takes it."""
        llm = self.settings.llm
        kwargs: dict[str, Any] = {"model": model, "temperature": llm.temperature}
        if llm.max_output_tokens:
            kwargs["max_tokens"] = llm.max_output_tokens
        if thinking:
            kwargs["max_tokens"] = max(int(llm.max_output_tokens or 0), THINKING_TOKENS)
        if self.settings.openai.seed is not None:
            kwargs["seed"] = self.settings.openai.seed
        if mode == "json_schema":
            kwargs["response_format"] = response_format(schema or self.schema)
        elif mode == "json_object":
            kwargs["response_format"] = {"type": "json_object"}
        kwargs.update((local_llm.thinking_on if thinking else local_llm.thinking_off)(base or llm.base_url, model))
        return kwargs

    def _with_json_instructions(
        self, messages: list[dict[str, Any]], schema: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        system, *rest = messages
        return [{**system, "content": system["content"] + local_llm.json_instructions(schema or self.schema)}, *rest]

    def _local_create(self, model: str, messages: list[dict[str, Any]], schema: dict[str, Any] | None = None, *,
                      thinking: bool = False, timeout: float | None = None,
                      deadline: float | None = None) -> Any:  # fmt: skip
        """One chat call to the local model, in the best way its server takes: the strict schema, else plain JSON
        mode, else the schema in the prompt; with the thinking switch, else without the fields the server refused.
        What worked is remembered for the next call. ``timeout``: the most this call may take, when less than
        AP_LLM_TIMEOUT_SECONDS (the AI reviewer's time for an invoice). ``deadline``: a ``time.monotonic()`` by which
        the call has its answer, the wait for a chat slot included (an arriving invoice's, D-168), else
        ``ChatTimeout``.

        With a backup server (D-180) the call goes to the server in use, the breaker is told how it went, and a call
        whose server the calls left meanwhile (it failed, or another call found it failing) is asked once more on the
        backup, when it has the same model (and the deadline leaves time)."""
        llm = self._llm()
        base = llm.base_url
        breaker = local_llm.chat_failover(self.settings.llm)
        again = breaker is not None
        # The hang limit, or the shorter call limit in play: a call ahead silent past it, the server isn't answering.
        hang = min(local_llm.HANG_SECONDS, float(self.settings.llm.timeout_seconds or local_llm.HANG_SECONDS))
        mode = local_llm.start_mode(base, model)
        while True:
            kind = local_llm.RESPONSE_MODES[mode]
            kwargs = self._local_kwargs(model, kind, schema, thinking=thinking, base=base)
            if timeout is not None and timeout < float(self.settings.llm.timeout_seconds or 0):
                kwargs["timeout"] = timeout
            sent = messages if kind == "json_schema" else self._with_json_instructions(messages, schema)
            cut, since = False, 0.0  # with a backup: its time cut by AP Coder's own line for a chat slot; when sent
            try:
                with local_llm.CHAT_LOCK.held(self.settings.llm.parallel, deadline) as slot:  # llm_parallel at once
                    if deadline is not None:  # what is left once a slot is had
                        left = deadline - time.monotonic()
                        waited = breaker is not None and slot.seconds > local_llm.WAITED_SECONDS
                        cut = waited and slot.behind < hang  # (what held it silent past the limit: the server's wait)
                        if left <= 0 or (waited and left < local_llm.SEND_SECONDS):
                            raise local_llm.SlotTimeout("too little time left once a chat slot was free", slot.behind)
                        kwargs["timeout"] = min(left, float(kwargs.get("timeout") or left))
                    call = functools.partial(self._local_client(llm).chat.completions.create, messages=sent, **kwargs)
                    since = time.monotonic()
                    answer = call() if breaker is None else breaker.watched(base, call)
                if breaker is not None:
                    breaker.answered(base, model)
                return answer
            except TimeoutError as exc:  # no slot in time, or too little time left once one was: never sent
                behind = float(getattr(exc, "behind", 0.0))
                if breaker is not None and behind >= hang:  # the calls holding the slots silent past the hang limit
                    breaker.failed(base, f"{local_llm.NO_ANSWER} to the calls holding the chat slots ({behind:.0f} s)")
                raise ChatTimeout(f"The chat model was busy: no slot to ask in within the time given ({exc}).") from exc
            except Exception as exc:  # noqa: BLE001 - every failure becomes a CodingError the reviewer can read
                if breaker is not None:
                    why = local_llm.server_failure(exc)
                    if why == local_llm.NO_ANSWER and (cut or breaker.behind(base, since, hang)):
                        why = ""  # its time spent in AP Coder's own line, or behind another of its calls: no failure
                    if why:
                        breaker.failed(base, why)
                    if again and (why or isinstance(exc, local_llm.ServerLeft)):
                        again = False
                        other = self._llm()
                        left = None if deadline is None else deadline - time.monotonic()
                        if other.base_url != base and self._model_on(other) == model and (left is None or left > 1):
                            log.info("%s: asked again on the backup at %s (%s)", model, other.base_url, why or exc)
                            llm, base = other, other.base_url
                            mode = local_llm.start_mode(base, model)
                            continue
                if "extra_body" in kwargs and local_llm.refuses_extra_fields(exc):
                    refuse = local_llm.refuse_thinking_on if thinking else local_llm.refuse_thinking_off
                    if refuse(base, model):
                        log.info("%s refused the thinking switch (%s); asking without it", model, exc)
                        continue
                if local_llm.rejects_format(exc) and mode + 1 < len(local_llm.RESPONSE_MODES):
                    log.info("%s refused response_format=%s (%s); trying the next way", model, kind, exc)
                    mode += 1
                    local_llm.remember_mode(base, model, mode)
                    continue
                if deadline is not None and _timed_out(exc):
                    raise ChatTimeout("The chat model gave no answer in the time an arriving invoice waits for it "
                                      f"({kwargs.get('timeout', 0):.0f} s).") from exc  # fmt: skip
                raise self._local_error(exc, kwargs.get("timeout"), base) from exc

    def _model_on(self, llm: LocalLLMSettings) -> str:
        """The model this coder would ask on the server ``llm`` names."""
        kept = self._models.get(llm.base_url)
        return llm.model or (kept[0] if kept else local_llm.check_server(llm).model)

    def _local_error(self, exc: Exception, timeout: float | None = None, base: str = "") -> CodingError:
        import openai

        llm = replace(self.settings.llm, base_url=base) if base else self.settings.llm
        if isinstance(exc, openai.APITimeoutError) and timeout:  # the AI reviewer's own limit, its minutes (B-1525)
            return ChatTimeout(
                f"The chat model took longer than the AI reviewer's time left ({timeout:.0f}s): it may be slow on this "
                "computer. Give the AI reviewer more minutes in Settings → Reading, or turn it off there."
            )
        if isinstance(exc, openai.APITimeoutError):
            return ChatTimeout(
                f"The local model took longer than {timeout or llm.timeout_seconds:.0f}s. LM Studio may still be "
                "loading it; try again, or allow longer with AP_LLM_TIMEOUT_SECONDS."
            )
        if isinstance(exc, (openai.APIConnectionError, ConnectionError)):
            local_llm.forget_status()
            return CodingError(
                f"The local model isn't answering at {llm.base_url}. In LM Studio: Developer tab → Start server.",
                temporary=True,
            )
        if isinstance(exc, openai.NotFoundError):  # the model asked for isn't there any more (swapped): B-1844
            local_llm.forget_status()  # the next call asks the one it has
            return CodingError(
                f"The server at {llm.base_url} no longer has the chat model asked for (LM Studio has another one "
                "now): asked again with the one it has.",
                temporary=True,
            )
        if local_llm.context_overflow(exc):
            return CodingError(
                "The invoice is too long for the model's context window. In LM Studio raise the model's Context "
                "Length (8192 or more), or lower AP_LLM_MAX_PROMPT_CHARS."
            )
        return CodingError(f"The local model failed: {type(exc).__name__}: {str(exc)[:300]}")

    def _code_local(
        self,
        extraction: ExtractionResult,
        images: list[PageImage] | None,
        history: str,
    ) -> CodingResult:
        """Ask for the strict schema first; a server that refuses it gets plain JSON mode, then the schema in the
        prompt. Each reply is parsed loosely and validated with the same model as Azure's, with one repair turn."""
        model = self._prepare_local()
        messages = self.build_messages(extraction, images, history)
        images_attached = len(images) if images and self.profile.supports_vision else 0
        usage_total: dict[str, int] = {}
        calls = repairs = 0
        while True:
            calls += 1
            response = self._local_create(model, messages)
            _accumulate_usage(usage_total, getattr(response, "usage", None))
            choice = response.choices[0]
            reply = local_llm.read_reply(choice, self.settings.llm.max_output_tokens)
            raw, data = reply.raw, reply.data
            try:
                if data is None:
                    if reply.cut_off:
                        raise CodingError(reply.problem)  # a repair turn would be cut off the same way
                    raise ValueError(reply.problem)
                coding = InvoiceCoding.model_validate(data)
            except (ValueError, ValidationError) as exc:
                if repairs >= self.max_repair_attempts:
                    raise CodingError(
                        f"The local model's answer failed validation after {calls} call(s): {exc}"
                    ) from exc
                repairs += 1
                log.warning("Local model reply %d was not a valid coding: %s", calls, exc)
                messages = [
                    *messages,
                    {"role": "assistant", "content": raw[:4000]},
                    {
                        "role": "user",
                        "content": f"That reply failed validation:\n{str(exc)[:2000]}\n"
                        f"Return only the corrected JSON object ({SCHEMA_NAME}), nothing else.",
                    },
                ]
                continue
            return CodingResult(
                coding=coding,
                raw_response=raw,
                model=getattr(response, "model", None) or model,
                usage=usage_total,
                attempts=calls,
                images_attached=images_attached,
            )


def _account_schema(reference: ReferenceData) -> dict[str, Any]:
    gl = reference.line_accounts()
    line: dict[str, Any] = {
        "line_number": {"type": "integer"},
        "gl_code": {"type": "string", "enum": gl},
        "reason": {"type": "string"},
    }
    if reference.cost_centers is not None:
        line["cost_center"] = {"type": "string", "enum": [*reference.cost_centers.codes, ""]}
    item = {"type": "object", "properties": line, "required": list(line), "additionalProperties": False}
    return {
        "type": "object",
        "properties": {"lines": {"type": "array", "items": item}},
        "required": ["lines"],
        "additionalProperties": False,
    }


def suggest_accounts(coder: InvoiceCoder, vendor: str, lines: list[tuple[int, str, float]], history: str = "",
                     kept: Path | None = None, within: float | None = None,
                     offered: dict[int, list[Any]] | None = None,
                     notes: dict[int, str] | None = None) -> dict[int, tuple[str, str, str]]:  # fmt: skip
    """A local model's GL account (and cost center) for each line the approval memory could not code:
    line number -> (gl_code, cost_center, reason). The invoice itself is read by the local reader, which a
    small model is worse at than at choosing an account; this call is short, so it is quick on a laptop.
    Only codes from the chart come back (the schema lists them); anything else is dropped. ``kept``: a folder where
    each answer is kept, so the same question (model, lines, history, chart) is never asked twice: the page reader's
    pass asks what arrival asked (D-131). ``within``: the most it may take, every batch's wait for a chat slot
    included (an arriving invoice on the company server, D-168), else ``ChatTimeout``.

    ``offered`` (with a chart of accounts imported or a journal loaded, D-212): line number -> the accounts offered
    for that line (``suggest.candidates``). The model then chooses among those of each line (``closed_list``), or
    none of them, and is never shown or allowed the whole chart; an answer that is not an account offered for the
    line leaves it for a person. ``notes``: filled with why a line asked about got no account, by line number."""
    if not lines:
        return {}
    notes = {} if notes is None else notes
    deadline = None if within is None else time.monotonic() + within
    model = coder._prepare_local()
    if offered is not None:
        return _suggest_closed(coder, model, vendor, lines, history, kept, deadline, offered, notes)
    for n, _, _ in lines[SUGGEST_MAX_LINES:]:
        notes[n] = f"The chat model is asked about the first {SUGGEST_MAX_LINES} lines of an invoice: not this one"
    schema = _account_schema(coder.reference)
    budget = int(coder.settings.llm.max_prompt_chars or 0)  # AP_LLM_MAX_PROMPT_CHARS: the whole prompt, both messages
    # The chart of accounts in at most half the budget (a real JDE chart is long): its start and its end (the tax
    # rates and the coding policy) are kept. The schema still lists every account.
    context, _ = local_llm.clip_document(coder.reference.to_prompt_context(), budget // 2 if budget else 0)
    system = (
        "You code accounts-payable invoice lines to the company's GL accounts. Pick, for each line, the one expense "
        "account (and cost center, when the list has them) that fits it best, following the coding policy. Answer "
        "with JSON only.\n\n" + context
    )
    if history and budget:
        history, _ = local_llm.clip_document(history, SUGGEST_HISTORY_CHARS)
    valid_gl = set(schema["properties"]["lines"]["items"]["properties"]["gl_code"]["enum"])
    valid_cc = set(coder.reference.cost_centers.codes) if coder.reference.cost_centers is not None else set()
    out: dict[int, tuple[str, str, str]] = {}
    # A long invoice is asked in batches; past SUGGEST_MAX_LINES the lines are left for AP.
    if len(lines) > SUGGEST_MAX_LINES:
        log.info("%d lines to suggest accounts for: the first %d are asked, the rest left for AP", len(lines),
                 SUGGEST_MAX_LINES)  # fmt: skip
    asked = lines[:SUGGEST_MAX_LINES]
    for first in range(0, len(asked), SUGGEST_BATCH_LINES):
        batch = asked[first : first + SUGGEST_BATCH_LINES]
        try:
            out.update(_suggest_batch(coder, model, schema, system, vendor, batch, history, valid_gl, valid_cc,
                                      kept, deadline))  # fmt: skip
        except Exception as exc:  # the batches answered before keep their accounts (B-1342); the rest stay for AP
            if not out:
                raise
            log.info("the local model suggested accounts for %d lines, then failed (%s): the rest left for AP",
                     len(out), exc)  # fmt: skip
            for n, _, _ in asked:
                if n not in out:
                    notes.setdefault(n, f"The chat model stopped answering before this line ({exc})")
            break
    for n, _, _ in asked:
        if n not in out:
            notes.setdefault(n, "The chat model gave no account of the list for this line")
    return out


SUGGEST_BATCH_LINES = 40  # lines in one prompt
SUGGEST_MAX_LINES = 200  # lines asked about in all (five prompts at most); the rest are left for AP
SUGGEST_DESCRIPTION_CHARS = 160  # of each line's description
SUGGEST_HISTORY_CHARS = 3_000  # of the approval history and AP's lessons


def _suggest_batch(coder: InvoiceCoder, model: str, schema: dict[str, Any], system: str, vendor: str,
                   lines: list[tuple[int, str, float]], history: str, valid_gl: set[str], valid_cc: set[str],
                   kept: Path | None = None,
                   deadline: float | None = None) -> dict[int, tuple[str, str, str]]:  # fmt: skip
    """``suggest_accounts`` for one batch of lines: one call, or none when ``kept`` has its answer."""
    listing = "\n".join(f"{n}. {str(desc)[:SUGGEST_DESCRIPTION_CHARS]} | {amount:,.2f}" for n, desc, amount in lines)
    user = f"Vendor: {vendor or 'unknown'}\nLines (number. description | amount):\n{listing}"
    if history:
        user += f"\n\nHow AP coded similar lines before:\n{history}"
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    return _picks(_asked_once(coder, model, messages, schema, kept, deadline), lines, valid_gl, valid_cc)


def _asked_once(coder: InvoiceCoder, model: str, messages: list[dict[str, Any]], schema: dict[str, Any],
                kept: Path | None, deadline: float | None) -> dict[str, Any]:  # fmt: skip
    """The model's answer to one question about accounts, as data: asked, or read from ``kept`` when this very
    question (model, messages, schema) was answered before (D-131). ``CodingError`` when it gives nothing usable."""
    path = None
    if kept is not None:
        digest = hashlib.sha256(json.dumps([model, messages, schema], sort_keys=True).encode()).hexdigest()
        path = kept / f"{digest}.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = None
        if isinstance(data, dict):
            return data
    response = coder._local_create(model, messages, schema, deadline=deadline)
    reply = local_llm.read_reply(response.choices[0], coder.settings.llm.max_output_tokens)
    if reply.data is None:  # the lines stay for AP to code; the reason is logged, and shown by the doctor
        raise CodingError(reply.problem[:1].upper() + reply.problem[1:])
    if path is not None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            part = path.with_suffix(".part")
            part.write_text(json.dumps(reply.data), encoding="utf-8")
            part.replace(path)
        except OSError:
            pass
        else:
            prune_kept(kept, path)  # by age and size (D-171)
    return reply.data


def _picks(data: dict[str, Any], lines: list[tuple[int, str, float]], valid_gl: set[str],
           valid_cc: set[str]) -> dict[int, tuple[str, str, str]]:  # fmt: skip
    """The model's answer for these lines: line number -> (gl_code, cost_center, reason), chart codes only."""
    wanted = {n for n, _, _ in lines}
    out: dict[int, tuple[str, str, str]] = {}
    for row in data.get("lines") or []:
        if not isinstance(row, dict):  # a row that is not an object (a small model's slip): that line stays for AP
            continue
        try:
            n = int(row.get("line_number"))
        except (TypeError, ValueError):
            continue
        gl = str(row.get("gl_code") or "").strip()
        if n in wanted and gl in valid_gl:
            cc = str(row.get("cost_center") or "").strip()
            out[n] = (gl, cc if cc in valid_cc else "", str(row.get("reason") or "").strip()[:300])
    return out


# --- The chat model chooses among the accounts offered for each line (D-212) ----------------------------------------

NONE_OF_THESE = "none of these"
OFFERED_OWN = 8  # of the supplier's own accounts (its coding last year, this vendor's approved lines), the likeliest
OFFERED_OTHERS = 8  # of the others (the words of the accounts' names across the chart, other vendors' lines)
OFFERED_WHY_CHARS = 110  # of what is said of an account
CLOSED_SYSTEM = (
    "You code accounts-payable invoice lines to the company's accounts. Each line comes with the accounts offered for "
    "it: the account string (business unit.object.subsidiary), its name, its business unit, and how the supplier's "
    "lines were coded before. Choose, for each line, the one account offered for that line that fits what was bought, "
    f'copied exactly as written, or "{NONE_OF_THESE}" when none of them fits: never an account that is not offered '
    "for that line. Say why in a few words. Answer with JSON only."
)


def closed_list(found: list[Any]) -> list[Any]:
    """The accounts the chat model is offered for a line, of its ``suggest.candidates``: the first ``OFFERED_OWN`` of
    the supplier's own and the first ``OFFERED_OTHERS`` of the others, in their order."""
    own = [c for c in found if c.source in ("vendor", "supplier")][:OFFERED_OWN]
    others = [c for c in found if c.source not in ("vendor", "supplier")][:OFFERED_OTHERS]
    return own + others


def _unit_name(reference: ReferenceData, unit: str) -> str:
    if reference.pairs is not None:
        return reference.pairs.name_of(unit)
    row = reference.cost_centers.get(unit) if reference.cost_centers is not None and unit else None
    return str((row or {}).get("description") or "")


def _offered_line(reference: ReferenceData, c: Any) -> str:
    """One account as the chat model reads it: its string, its name, its business unit's name and what is known of
    it for this supplier."""
    if c.of:
        said = f"used {c.lines} time{'' if c.lines == 1 else 's'} for this supplier"
        said += f", e.g. “{c.example}”" if c.example else ""
    else:
        said = c.reason
    parts = [c.account, c.name, _unit_name(reference, c.business_unit), said[:OFFERED_WHY_CHARS]]
    return " | ".join(str(p).replace("|", "/").replace("\n", " ") for p in parts)


def _closed_schema(accounts: list[str]) -> dict[str, Any]:
    line = {
        "line_number": {"type": "integer"},
        "account": {"type": "string", "enum": [*accounts, NONE_OF_THESE]},
        "reason": {"type": "string"},
    }
    item = {"type": "object", "properties": line, "required": list(line), "additionalProperties": False}
    return {"type": "object", "properties": {"lines": {"type": "array", "items": item}}, "required": ["lines"],
            "additionalProperties": False}  # fmt: skip


def _closed_question(reference: ReferenceData, vendor: str, batch: list[tuple[int, str, float]],
                     lists: dict[int, list[Any]]) -> tuple[str, list[str]]:  # fmt: skip
    """(the question for a batch of lines, the account strings it offers): each account described once, then each
    line with the strings offered for it."""
    described: dict[str, str] = {}
    listing = []
    for n, desc, amount in batch:
        for c in lists[n]:
            described.setdefault(c.account, _offered_line(reference, c))
        listing.append(f"{n}. {str(desc)[:SUGGEST_DESCRIPTION_CHARS]} | {amount:,.2f} | offered: "
                       + ", ".join(c.account for c in lists[n]))  # fmt: skip
    text = "\n".join([
        f"Vendor: {vendor or 'unknown'}", "Accounts (account | name | business unit | what is known of it):",
        *described.values(), "", "Lines (number. description | amount | the accounts offered for it):", *listing,
    ])  # fmt: skip
    return text, list(described)


def _closed_batches(reference: ReferenceData, vendor: str, lines: list[tuple[int, str, float]],
                    lists: dict[int, list[Any]], room: int) -> list[list[tuple[int, str, float]]]:  # fmt: skip
    """The lines in batches whose question fits ``room`` characters (0: no limit), ``SUGGEST_BATCH_LINES`` lines at
    most each; a line always goes in a batch, alone when its own question is longer."""
    batches: list[list[tuple[int, str, float]]] = []
    batch: list[tuple[int, str, float]] = []
    for line in lines:
        fits = len(batch) < SUGGEST_BATCH_LINES and (
            not room or len(_closed_question(reference, vendor, [*batch, line], lists)[0]) <= room)  # fmt: skip
        if batch and not fits:
            batches.append(batch)
            batch = []
        batch.append(line)
    return [*batches, batch] if batch else batches


def _closed_picks(data: dict[str, Any], batch: list[tuple[int, str, float]], lists: dict[int, list[Any]],
                  notes: dict[int, str]) -> dict[int, tuple[str, str, str]]:  # fmt: skip
    """The model's answer for a batch: line number -> (GL code, business unit, reason), only an account offered for
    that line. Any other answer leaves the line for a person, and ``notes`` says why: none of them, an account that
    was not offered, a code that was offered on another business unit, or no answer for the line."""
    from ..core.chart import split_account

    out: dict[int, tuple[str, str, str]] = {}
    answered: dict[int, tuple[str, str]] = {}
    for row in data.get("lines") or []:
        if not isinstance(row, dict):
            continue
        try:
            n = int(row.get("line_number"))
        except (TypeError, ValueError):
            continue
        if n in lists and n not in answered:
            answered[n] = (str(row.get("account") or "").strip(), str(row.get("reason") or "").strip()[:300])
    for n, _, _ in batch:
        account, reason = answered.get(n, ("", ""))
        chosen = next((c for c in lists[n] if c.account == account), None)
        if chosen is not None:
            out[n] = (chosen.code, chosen.business_unit, reason)
        elif not account:
            notes[n] = "The chat model gave no answer for this line"
        elif account.lower() == NONE_OF_THESE:
            notes[n] = f"The chat model found that none of the accounts offered fits{f' ({reason})' if reason else ''}"
        elif any(c.code == split_account(account)[1] for c in lists[n]):
            notes[n] = (f"The chat model answered {account}: that account was offered, but not on business unit "
                        f"{split_account(account)[0]}")  # fmt: skip
        else:
            notes[n] = f"The chat model answered {account[:60]}, which is not one of the accounts offered"
    return out


def _suggest_closed(coder: InvoiceCoder, model: str, vendor: str, lines: list[tuple[int, str, float]], history: str,
                    kept: Path | None, deadline: float | None, offered: dict[int, list[Any]],
                    notes: dict[int, str]) -> dict[int, tuple[str, str, str]]:  # fmt: skip
    """``suggest_accounts`` when each line has the accounts offered for it: the model chooses among them, in
    batches whose prompt fits the budget."""
    reference = coder.reference
    budget = int(coder.settings.llm.max_prompt_chars or 0)
    policy = "\n".join(f"- {note}" for note in reference.notes)
    system = CLOSED_SYSTEM + (f"\n\nCoding policy:\n{policy[: budget // 4 if budget else None]}" if policy else "")
    if history and budget:
        history, _ = local_llm.clip_document(history, SUGGEST_HISTORY_CHARS)
    for n, _, _ in lines[SUGGEST_MAX_LINES:]:
        notes[n] = f"The chat model is asked about the first {SUGGEST_MAX_LINES} lines of an invoice: not this one"
    lists = {n: closed_list(offered.get(n) or []) for n, _, _ in lines[:SUGGEST_MAX_LINES]}
    asked = [line for line in lines[:SUGGEST_MAX_LINES] if lists[line[0]]]
    for n in lists:
        if not lists[n]:
            notes[n] = "The chat model was not asked: there is no account to offer it for this line"
    room = max(budget - len(system) - len(history) - 200, 2000) if budget else 0
    out: dict[int, tuple[str, str, str]] = {}
    for batch in _closed_batches(reference, vendor, asked, lists, room):
        user, accounts = _closed_question(reference, vendor, batch, lists)
        if history:
            user += f"\n\nHow AP coded similar lines before:\n{history}"
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        try:
            data = _asked_once(coder, model, messages, _closed_schema(accounts), kept, deadline)
        except Exception as exc:  # the batches answered before keep their accounts (B-1342); the rest stay for AP
            if not out:
                raise
            log.info("the local model suggested accounts for %d lines, then failed (%s): the rest left for AP",
                     len(out), exc)  # fmt: skip
            for n, _, _ in asked:
                if n not in out:
                    notes.setdefault(n, f"The chat model stopped answering before this line ({exc})")
            break
        out.update(_closed_picks(data, batch, lists, notes))
    return out


# --- The chat model's own reading of the header, from the page images ------------------------------------------------

HEADER_READ = (
    "vendor_name", "invoice_number", "invoice_date", "due_date", "po_number", "currency",
    "gst_hst_registration_number", "qst_registration_number", "pst_registration_number", "subtotal", "gst_amount",
    "hst_amount", "pst_amount", "qst_amount", "tax_total", "grand_total", "payment_terms",
)  # fmt: skip
_HEADER_AMOUNTS = {"subtotal", "gst_amount", "hst_amount", "pst_amount", "qst_amount", "tax_total", "grand_total"}
HEADER_SYSTEM = (
    "You read the header of a supplier's invoice from its page images, as an accounts-payable clerk copies it into "
    "the ERP. Copy each value as it is printed on the page. Do not work anything out and do not guess: a field that "
    "is not printed is null. vendor_name is the company that issued the invoice (its name at the top or in the "
    "remit-to), never the customer it is billed or shipped to. po_number is the customer's purchase order number, "
    "not the supplier's sales order, quote or account number. Dates as YYYY-MM-DD. Amounts as plain numbers (no "
    "currency sign, no thousands separators), negative on a credit note. gst_hst_registration_number, "
    "qst_registration_number and pst_registration_number (BC PST, Manitoba RST, Saskatchewan PST) are the "
    "supplier's tax registration numbers as printed, not the tax amounts. Answer with JSON only."
)


def _header_schema() -> dict[str, Any]:
    props = {f: {"type": ["number", "null"]} if f in _HEADER_AMOUNTS else {"type": ["string", "null"]}
             for f in HEADER_READ}  # fmt: skip
    return {"type": "object", "properties": props, "required": list(HEADER_READ), "additionalProperties": False}


def clean_header(data: dict[str, Any]) -> dict[str, Any]:
    """The model's answer as capture values: known fields only, amounts as numbers, dates as YYYY-MM-DD, nothing
    empty. A value it wrote in another shape is dropped, never guessed at."""
    import datetime as dt
    import re

    out: dict[str, Any] = {}
    for name in HEADER_READ:
        value = data.get(name)
        if value is None or (isinstance(value, str) and not value.strip()):
            continue
        if name in _HEADER_AMOUNTS:
            if isinstance(value, str):  # as printed: "1 234,56 $", "(12.00)", "12.50 CR", read as the page reads it
                from ..capture.normalize import parse_amount

                value = parse_amount(value)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                continue
            out[name] = round(float(value), 2)
        elif name in ("invoice_date", "due_date"):
            try:
                out[name] = dt.date.fromisoformat(str(value).strip()[:10]).isoformat()
            except ValueError:
                continue
        elif name == "currency":
            code = str(value).strip().upper()
            if re.fullmatch(r"[A-Z]{3}", code):
                out[name] = code
        else:
            out[name] = str(value).strip()[:120]
    return out


def read_header(coder: InvoiceCoder, images: list[PageImage]) -> dict[str, Any]:
    """The local chat model's own reading of the invoice's header from its page images (Gemma 4 and Qwen 3.5 can see
    pages): one more independent reader, never shown what the others found. {} when there are no images or the model
    can't see pages; raises CodingError when the model isn't answering or answers nothing usable."""
    if not images:
        return {}
    model = coder._prepare_local()
    if not coder.profile.supports_vision:
        return {}
    content: list[dict[str, Any]] = [{"type": "text", "text": "Read the header of this invoice."}]
    content += [{"type": "image_url", "image_url": {"url": img.to_data_url(), "detail": "high"}} for img in images]
    messages = [{"role": "system", "content": HEADER_SYSTEM}, {"role": "user", "content": content}]
    response = coder._local_create(model, messages, _header_schema())
    reply = local_llm.read_reply(response.choices[0], coder.settings.llm.max_output_tokens)
    if reply.data is None:
        raise CodingError(reply.problem[:1].upper() + reply.problem[1:])
    return clean_header(reply.data)


def _accumulate_usage(total: dict[str, int], usage: Any) -> None:
    if usage is None:
        return
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = getattr(usage, key, None)
        if isinstance(value, int):
            total[key] = total.get(key, 0) + value
    cached = getattr(getattr(usage, "prompt_tokens_details", None), "cached_tokens", None)
    if isinstance(cached, int):
        total["cached_prompt_tokens"] = total.get("cached_prompt_tokens", 0) + cached
