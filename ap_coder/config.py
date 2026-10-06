"""Runtime configuration, loaded from environment variables (and an optional .env file)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path

from dotenv import load_dotenv

DEFAULT_AOAI_API_VERSION = "2024-10-21"  # first GA version with strict Structured Outputs


def _env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return value.strip()


def _env_bool(name: str, default: bool) -> bool:
    value = _env(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float | None) -> float | None:
    value = _env(name)
    return float(value) if value is not None else default


def _env_int(name: str, default: int | None) -> int | None:
    value = _env(name)
    return int(value) if value is not None else default


@dataclass(frozen=True)
class DocumentIntelligenceSettings:
    endpoint: str | None = None
    api_key: str | None = None  # None -> Entra ID (DefaultAzureCredential)
    model_id: str = "prebuilt-layout"  # or "prebuilt-invoice"
    locale: str | None = None
    pages: str | None = None  # e.g. "1-3"; None = all pages


@dataclass(frozen=True)
class OpenAISettings:
    endpoint: str | None = None
    api_key: str | None = None  # None -> Entra ID (DefaultAzureCredential)
    api_version: str = DEFAULT_AOAI_API_VERSION
    deployment: str = "gpt-4o"
    # Underlying model name. Deployment names are arbitrary, so capabilities
    # (vision, temperature support, ...) are inferred from this instead.
    model_name: str | None = None
    temperature: float | None = 0.0
    max_output_tokens: int | None = 8000
    seed: int | None = 42
    reasoning_effort: str | None = None  # only sent to reasoning models
    supports_vision: bool | None = None  # override auto-detection
    timeout_seconds: float = 120.0
    max_retries: int = 5


@dataclass(frozen=True)
class EngineSettings:
    vision: bool = False  # attach page images alongside the extracted text
    vision_max_pages: int = 5
    constrain_codes: bool = True  # put valid GL/cost-center codes into the schema as enums
    review_threshold: float = 0.85
    max_document_chars: int = 200_000


@dataclass(frozen=True)
class Settings:
    document_intelligence: DocumentIntelligenceSettings = field(default_factory=DocumentIntelligenceSettings)
    openai: OpenAISettings = field(default_factory=OpenAISettings)
    engine: EngineSettings = field(default_factory=EngineSettings)

    @classmethod
    def from_env(cls, env_file: str | Path | None = None) -> Settings:
        load_dotenv(env_file, override=False)

        di = DocumentIntelligenceSettings(
            endpoint=_env("AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT"),
            api_key=_env("AZURE_DOCUMENT_INTELLIGENCE_KEY"),
            model_id=_env("AZURE_DOCUMENT_INTELLIGENCE_MODEL", "prebuilt-layout"),
            locale=_env("AZURE_DOCUMENT_INTELLIGENCE_LOCALE"),
            pages=_env("AZURE_DOCUMENT_INTELLIGENCE_PAGES"),
        )

        temperature = _env("AZURE_OPENAI_TEMPERATURE", "0")
        vision_override = _env("AZURE_OPENAI_SUPPORTS_VISION")
        oai = OpenAISettings(
            endpoint=_env("AZURE_OPENAI_ENDPOINT"),
            api_key=_env("AZURE_OPENAI_API_KEY"),
            api_version=_env("AZURE_OPENAI_API_VERSION", DEFAULT_AOAI_API_VERSION),
            deployment=_env("AZURE_OPENAI_DEPLOYMENT", "gpt-4o"),
            model_name=_env("AZURE_OPENAI_MODEL_NAME"),
            temperature=None if temperature.lower() == "none" else float(temperature),
            max_output_tokens=_env_int("AZURE_OPENAI_MAX_OUTPUT_TOKENS", 8000),
            seed=_env_int("AZURE_OPENAI_SEED", 42),
            reasoning_effort=_env("AZURE_OPENAI_REASONING_EFFORT"),
            supports_vision=None if vision_override is None else _env_bool("AZURE_OPENAI_SUPPORTS_VISION", False),
            timeout_seconds=_env_float("AZURE_OPENAI_TIMEOUT_SECONDS", 120.0),
            max_retries=_env_int("AZURE_OPENAI_MAX_RETRIES", 5),
        )

        engine = EngineSettings(
            vision=_env_bool("AP_VISION", False),
            vision_max_pages=_env_int("AP_VISION_MAX_PAGES", 5),
            constrain_codes=_env_bool("AP_CONSTRAIN_CODES", True),
            review_threshold=_env_float("AP_REVIEW_THRESHOLD", 0.85),
            max_document_chars=_env_int("AP_MAX_DOCUMENT_CHARS", 200_000),
        )
        return cls(document_intelligence=di, openai=oai, engine=engine)

    def with_overrides(
        self,
        *,
        di_model: str | None = None,
        deployment: str | None = None,
        model_name: str | None = None,
        vision: bool | None = None,
    ) -> Settings:
        """Return a copy with CLI-level overrides applied."""
        di, oai, engine = self.document_intelligence, self.openai, self.engine
        if di_model:
            di = replace(di, model_id=di_model)
        if deployment:
            oai = replace(oai, deployment=deployment)
        if model_name:
            oai = replace(oai, model_name=model_name)
        if vision is not None:
            engine = replace(engine, vision=vision)
        return replace(self, document_intelligence=di, openai=oai, engine=engine)
