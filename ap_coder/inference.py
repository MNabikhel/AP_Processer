"""Component 2 - Inference & GL coding layer.

Azure OpenAI with strict Structured Outputs, or a local OpenAI-compatible model (LM Studio, Ollama) through
:mod:`ap_coder.local_llm`, which asks for the same schema and falls back to parsing a looser reply.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, replace
from typing import Any

from pydantic import ValidationError

from . import local_llm
from .config import Settings
from .extraction import ExtractionResult
from .imaging import PageImage
from .prompts import VISION_NOTE, build_system_prompt
from .reference_data import ReferenceData
from .schema import SCHEMA_NAME, InvoiceCoding, build_json_schema, response_format

log = logging.getLogger(__name__)

_REASONING_PREFIXES = ("o1", "o3", "o4", "gpt-5")
_TEXT_ONLY_PREFIXES = ("o1-mini", "o3-mini", "gpt-35", "gpt-3.5")


class CodingError(RuntimeError):
    """The model could not produce a valid coding for the invoice."""


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


def build_openai_client(settings: Settings) -> Any:
    from openai import AzureOpenAI

    oai = settings.openai
    if not oai.endpoint:
        raise RuntimeError("AZURE_OPENAI_ENDPOINT is not set")
    auth: dict[str, Any]
    if oai.api_key:
        auth = {"api_key": oai.api_key}
    else:
        from azure.identity import DefaultAzureCredential, get_bearer_token_provider

        auth = {
            "azure_ad_token_provider": get_bearer_token_provider(
                DefaultAzureCredential(), "https://cognitiveservices.azure.com/.default"
            )
        }
    return AzureOpenAI(
        azure_endpoint=oai.endpoint,
        api_version=oai.api_version,
        timeout=oai.timeout_seconds,
        max_retries=oai.max_retries,  # SDK retries 429/5xx with backoff, honouring Retry-After
        **auth,
    )


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
                self._client = local_llm.build_local_client(self.settings.llm)
            elif self.provider == "azure":
                self._client = build_openai_client(self.settings)
            else:
                raise CodingError(local_llm.NO_MODEL_MESSAGE)
        return self._client

    def _prepare_local(self) -> str:
        """The local model to ask, and what it can do (vision), found once per coder."""
        if self._local_model:
            return self._local_model
        llm = self.settings.llm
        status = local_llm.check_server(llm)
        model = llm.model or status.model
        if not model:
            if status.reachable:
                raise CodingError(
                    f"{status.server_title} is running but no model is loaded. Load a small instruct model in "
                    "LM Studio (e.g. Qwen 2.5 7B Instruct), or name one in AP_LLM_MODEL."
                )
            raise CodingError(
                f"The local model isn't answering at {llm.base_url}. In LM Studio: load a model, then "
                "Developer tab → Start server."
            )
        vision = {"on": True, "off": False}.get(llm.vision, status.vision if status.model == model else False)
        self.profile = ModelProfile(name=model.lower(), supports_vision=vision, reasoning=False)
        self._local_model = model
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

    def _local_kwargs(self, model: str, mode: str) -> dict[str, Any]:
        """No Azure-only fields (max_completion_tokens, reasoning_effort): every local server takes these."""
        llm = self.settings.llm
        kwargs: dict[str, Any] = {"model": model, "temperature": llm.temperature}
        if llm.max_output_tokens:
            kwargs["max_tokens"] = llm.max_output_tokens
        if self.settings.openai.seed is not None:
            kwargs["seed"] = self.settings.openai.seed
        if mode == "json_schema":
            kwargs["response_format"] = response_format(self.schema)
        elif mode == "json_object":
            kwargs["response_format"] = {"type": "json_object"}
        return kwargs

    def _with_json_instructions(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        system, *rest = messages
        return [{**system, "content": system["content"] + local_llm.json_instructions(self.schema)}, *rest]

    def _local_error(self, exc: Exception) -> CodingError:
        import openai

        llm = self.settings.llm
        if isinstance(exc, openai.APITimeoutError):
            return CodingError(
                f"The local model took longer than {llm.timeout_seconds:.0f}s. LM Studio may still be loading it; "
                "try again, or allow longer with AP_LLM_TIMEOUT_SECONDS."
            )
        if isinstance(exc, (openai.APIConnectionError, ConnectionError)):
            local_llm.forget_status()
            return CodingError(
                f"The local model isn't answering at {llm.base_url}. In LM Studio: Developer tab → Start server."
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
        base = self.settings.llm.base_url
        messages = self.build_messages(extraction, images, history)
        images_attached = len(images) if images and self.profile.supports_vision else 0
        usage_total: dict[str, int] = {}
        mode = local_llm.start_mode(base, model)
        calls = repairs = 0
        while True:
            calls += 1
            kind = local_llm.RESPONSE_MODES[mode]
            sent = messages if kind == "json_schema" else self._with_json_instructions(messages)
            try:
                response = self.client.chat.completions.create(messages=sent, **self._local_kwargs(model, kind))
            except Exception as exc:  # noqa: BLE001 - every failure becomes a CodingError the reviewer can read
                if local_llm.rejects_format(exc) and mode + 1 < len(local_llm.RESPONSE_MODES):
                    log.info("%s refused response_format=%s (%s); trying the next way", model, kind, exc)
                    mode += 1
                    local_llm.remember_mode(base, model, mode)
                    continue
                raise self._local_error(exc) from exc
            _accumulate_usage(usage_total, getattr(response, "usage", None))
            choice = response.choices[0]
            message = choice.message
            # LM Studio puts a reasoning model's text in reasoning_content when it never got to the answer.
            raw = message.content or getattr(message, "reasoning_content", None) or ""
            data = local_llm.parse_json_reply(raw)
            try:
                if data is None:
                    if choice.finish_reason == "length":
                        raise CodingError(
                            "The model's reply was cut off before it finished; raise AP_LLM_MAX_TOKENS "
                            "(or load a model that doesn't think at length)"
                        )
                    raise ValueError("the reply held no JSON object")
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
