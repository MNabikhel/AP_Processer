"""Runtime configuration, loaded from environment variables (and an optional .env file)."""

from __future__ import annotations

import ipaddress
import logging
import os
import re
from dataclasses import dataclass, field, replace
from pathlib import Path

from dotenv import load_dotenv

from . import paths

log = logging.getLogger(__name__)
DEFAULT_AOAI_API_VERSION = "2024-10-21"  # first GA version with strict Structured Outputs
DEFAULT_LLM_BASE_URL = "http://127.0.0.1:1234/v1"  # LM Studio's local server (Ollama: http://127.0.0.1:11434/v1)
LLM_PROVIDERS = ("auto", "local", "azure", "off")
LLM_VISION_MODES = ("auto", "on", "off")


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
    """A number from the .env; "0,85" (a decimal comma) reads as 0.85, and a typo keeps the default
    instead of stopping every command, doctor included."""
    value = _env(name)
    if value is None:
        return default
    try:
        return float(value.replace(",", "."))
    except ValueError:
        log.warning("%s=%r is not a number; using %s", name, value, default)
        return default


def _env_int(name: str, default: int | None) -> int | None:
    value = _env(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        log.warning("%s=%r is not a whole number; using %s", name, value, default)
        return default


def _on_this_network(host: str) -> bool:
    """localhost, a bare machine name, a .local/.lan name, or a private or loopback address."""
    name = host.rsplit("@", 1)[-1]
    name = name[1:].split("]", 1)[0] if name.startswith("[") else name.rsplit(":", 1)[0]
    name = name.lower()
    if name == "localhost" or name.endswith((".local", ".lan", ".home")) or "." not in name:
        return True
    try:
        address = ipaddress.ip_address(name)
    except ValueError:
        return False
    return address.is_private or address.is_loopback or address.is_link_local or address.is_unspecified


def normalise_base_url(value: str | None) -> str:
    """Accept any URL LM Studio or Ollama shows (…/v1/chat/completions, …/api/v0/models, host:port) as the /v1 base."""
    text = str(value or "").strip().strip("\"'").rstrip("/")
    if not text:
        return DEFAULT_LLM_BASE_URL
    if "://" not in text:
        text = "http://" + text
    scheme, rest = text.split("://", 1)
    host, _, path = rest.partition("/")
    path = "/" + path if path else ""
    path = re.sub(r"/(chat/completions|completions|responses|models|embeddings)$", "", path)
    # Hosted gateways (OpenRouter) really live under /api/v1; only a server on this machine or network is
    # LM Studio's own /api/vN address or Ollama's /api.
    if _on_this_network(host):
        path = re.sub(r"^/api(/v\d+)?(/chat|/models|/tags|/generate)?$", "", path)
    return f"{scheme.lower()}://{host}{path or '/v1'}"


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
class LocalLLMSettings:
    """Which AI codes invoices, and the local OpenAI-compatible server (LM Studio, Ollama) when it is a local one.

    ``provider``: ``auto`` (Azure OpenAI when its endpoint is set, else a local model when one answers, else none),
    ``local``, ``azure`` or ``off``. An empty ``model`` means the chat model loaded in LM Studio.
    """

    provider: str = "auto"
    base_url: str = DEFAULT_LLM_BASE_URL
    model: str = ""
    api_key: str = "lm-studio"  # LM Studio and Ollama accept any key
    timeout_seconds: float = 600.0  # a laptop without a graphics card: the first call took 6.5 min with a 7B model
    max_output_tokens: int = 4096
    vision: str = "auto"  # auto = when the loaded model can see pages; on / off to force it
    # Document text sent to a local model, so a small context window is not overrun (start and end are kept).
    max_prompt_chars: int = 24_000
    temperature: float = 0.0


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
    llm: LocalLLMSettings = field(default_factory=LocalLLMSettings)

    @classmethod
    def from_env(cls, env_file: str | Path | None = None) -> Settings:
        if env_file is None:
            # The data folder's .env (written by the installer), else ./.env, else the project's.
            env_file = paths.env_file()
        load_dotenv(env_file, override=False, encoding="utf-8-sig")  # -sig: Notepad's byte-order mark

        di = DocumentIntelligenceSettings(
            endpoint=_env("AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT"),
            api_key=_env("AZURE_DOCUMENT_INTELLIGENCE_KEY"),
            model_id=_env("AZURE_DOCUMENT_INTELLIGENCE_MODEL", "prebuilt-layout"),
            locale=_env("AZURE_DOCUMENT_INTELLIGENCE_LOCALE"),
            pages=_env("AZURE_DOCUMENT_INTELLIGENCE_PAGES"),
        )

        temperature = _env("AZURE_OPENAI_TEMPERATURE", "0") or "0"
        vision_override = _env("AZURE_OPENAI_SUPPORTS_VISION")
        oai = OpenAISettings(
            endpoint=_env("AZURE_OPENAI_ENDPOINT"),
            api_key=_env("AZURE_OPENAI_API_KEY"),
            api_version=_env("AZURE_OPENAI_API_VERSION", DEFAULT_AOAI_API_VERSION),
            deployment=_env("AZURE_OPENAI_DEPLOYMENT", "gpt-4o"),
            model_name=_env("AZURE_OPENAI_MODEL_NAME"),
            temperature=None if temperature.lower() == "none" else _env_float("AZURE_OPENAI_TEMPERATURE", 0.0),
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
        provider = (_env("AP_LLM_PROVIDER", "auto") or "auto").lower()
        vision_mode = (_env("AP_LLM_VISION", "auto") or "auto").lower()
        vision_mode = {"true": "on", "yes": "on", "1": "on", "false": "off", "no": "off", "0": "off"}.get(
            vision_mode, vision_mode
        )
        model = _env("AP_LLM_MODEL", "") or ""
        llm = LocalLLMSettings(
            provider=provider if provider in LLM_PROVIDERS else "auto",
            base_url=normalise_base_url(_env("AP_LLM_BASE_URL")),
            model="" if model.lower() == "auto" else model,
            api_key=_env("AP_LLM_API_KEY", "lm-studio"),
            timeout_seconds=_env_float("AP_LLM_TIMEOUT_SECONDS", 600.0),
            max_output_tokens=_env_int("AP_LLM_MAX_TOKENS", 4096),
            vision=vision_mode if vision_mode in LLM_VISION_MODES else "auto",
            max_prompt_chars=_env_int("AP_LLM_MAX_PROMPT_CHARS", 24_000),
            temperature=_env_float("AP_LLM_TEMPERATURE", 0.0),
        )
        return cls(document_intelligence=di, openai=oai, engine=engine, llm=llm)

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
