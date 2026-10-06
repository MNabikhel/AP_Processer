"""Component 2 - Inference & GL coding layer (Azure OpenAI Structured Outputs)."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from .config import Settings
from .extraction import ExtractionResult
from .imaging import PageImage
from .prompts import VISION_NOTE, build_system_prompt
from .reference_data import ReferenceData
from .schema import InvoiceCoding, build_json_schema, response_format

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
    ) -> None:
        self.settings = settings
        self.reference = reference
        self._client = client
        self.max_repair_attempts = max_repair_attempts
        self.profile = ModelProfile.resolve(
            settings.openai.model_name or settings.openai.deployment,
            settings.openai.supports_vision,
        )
        self.schema = build_json_schema(
            reference,
            constrain_codes=settings.engine.constrain_codes,
            include_tax_rate=settings.engine.include_tax_rate,
        )
        self.system_prompt = build_system_prompt(reference, include_tax_rate=settings.engine.include_tax_rate)

    @property
    def client(self) -> Any:
        if self._client is None:
            self._client = build_openai_client(self.settings)
        return self._client

    def build_messages(
        self, extraction: ExtractionResult, images: list[PageImage] | None = None
    ) -> list[dict[str, Any]]:
        payload = extraction.to_prompt_payload(self.settings.engine.max_document_chars)
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

    def code(self, extraction: ExtractionResult, images: list[PageImage] | None = None) -> CodingResult:
        messages = self.build_messages(extraction, images)
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
