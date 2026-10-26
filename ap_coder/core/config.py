"""Runtime configuration, loaded from environment variables (and an optional .env file)."""

from __future__ import annotations

import io
import ipaddress
import logging
import os
import re
import socket
from dataclasses import dataclass, field, replace
from pathlib import Path

from dotenv import dotenv_values

from . import envfile, paths

log = logging.getLogger(__name__)
DEFAULT_AOAI_API_VERSION = "2024-10-21"  # first GA version with strict Structured Outputs
DEFAULT_LLM_BASE_URL = "http://127.0.0.1:1234/v1"  # LM Studio's local server (Ollama: http://127.0.0.1:11434/v1)
LLM_VISION_MODES = ("auto", "on", "off")
# Settings an older AP Coder had, read no more: every invoice is read the same way now (an old .env that still has
# them works as before, they are simply ignored).
RETIRED_SETTINGS = ("AP_PAGE_READER", "AP_PAGE_READER_SCOPE", "AP_LLM_PROVIDER")
# What a .env never sets: the variables that decide which code a process (or one it starts) runs. The data folder's
# .env can be written by the account AP Coder runs as; on the company server an administrator's commands read it too,
# so a PYTHONPATH= line there would run that account's code as the administrator (D-109).
PROCESS_VARS = ("PATH", "PATHEXT", "COMSPEC", "SYSTEMROOT", "WINDIR", "VIRTUAL_ENV", "__PYVENV_LAUNCHER__")
PROCESS_PREFIXES = ("PYTHON", "PIP_", "LD_", "DYLD_", "UV_")


def process_var(name: str) -> bool:
    """Whether ``name`` is a variable that decides which code runs (never taken from a .env)."""
    upper = name.strip().upper()
    return upper in PROCESS_VARS or upper.startswith(PROCESS_PREFIXES)


def load_env_file(path: Path) -> None:
    """The .env's settings into the environment, those already set winning, and never :func:`process_var` ones."""
    values = dotenv_values(stream=io.StringIO(envfile.read_text(path)), interpolate=False)
    for name, value in values.items():
        if value is not None and not process_var(name) and name not in os.environ:
            os.environ[name] = value


_PLACEHOLDER = re.compile(r"<[^<>]+>")  # "https://<your-resource>.openai.azure.com/" in .env.example


def _env(name: str, default: str | None = None) -> str | None:
    """A setting from the environment; empty, or an example placeholder such as ``<your-resource>``, is unset."""
    value = os.getenv(name)
    if value is None or value.strip() == "" or _PLACEHOLDER.search(value):
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


_NAT64 = ipaddress.ip_network("64:ff9b::/96")  # an IPv4 address on the internet, reached through a NAT64 gateway


def _address(name: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """The address ``name`` is, read the way the resolver reads it: an IPv4 address may also be written as one number
    (134744072), in hex (0x08080808) or with fewer parts (127.1). None for a name."""
    try:
        return ipaddress.ip_address(name)
    except ValueError:
        pass
    parts = name.split(".")
    if not name or not name[0].isdigit() or len(parts) > 4 or set(name) - set("0123456789abcdefx."):
        return None
    numbers = []
    for part in parts:  # as inet_aton reads each part: 0x… hex, 0… octal, else decimal
        try:
            numbers.append(int(part, 16) if part[:2] == "0x" else int(part, 8) if part[:1] == "0" else int(part, 10))
        except ValueError:
            return None
    *head, last = numbers
    if any(n > 255 for n in head) or last >= 256 ** (4 - len(head)):
        return None
    value = last
    for index, n in enumerate(head):
        value += n << (8 * (3 - index))
    return ipaddress.IPv4Address(value)


def _on_this_network(host: str) -> bool:
    """localhost, a bare machine name, a .local/.lan name, or a private or loopback address (however it is written:
    an address in another form is judged as the address it is)."""
    name = host.rsplit("@", 1)[-1]
    name = name[1:].split("]", 1)[0] if name.startswith("[") else name.rsplit(":", 1)[0]
    name = name.lower()
    address = _address(name)
    if address is None:
        if not (name == "localhost" or name.endswith((".local", ".lan", ".home")) or "." not in name):
            return False
        # A name is judged by the addresses it resolves to: "gpu-box" or "x.local" may be an internet address
        # (a DNS search domain, a spoofed name) (B-974). One that doesn't resolve reaches nothing: judged by name.
        if name == "localhost":
            return True
        found = _resolved(name)
        return all(_private(a) for a in found) if found else True
    return _private(address)


def _resolved(name: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    try:
        infos = socket.getaddrinfo(name, None, proto=socket.IPPROTO_TCP)
    except (OSError, UnicodeError):
        return []
    found = []
    for info in infos:
        try:
            found.append(ipaddress.ip_address(str(info[4][0]).split("%", 1)[0]))
        except ValueError:
            continue
    return found


def _private(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(address, ipaddress.IPv6Address):
        if address in _NAT64:
            return False
        if address.ipv4_mapped is not None:
            address = address.ipv4_mapped
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
    """The local OpenAI-compatible server (LM Studio, Ollama) whose chat model suggests GL accounts.

    Every invoice is coded the same way (``local_llm.resolve_provider``): read by the local readers, coded from what
    AP approved before, the chat model suggesting accounts for the lines left over when it answers. ``provider`` is
    not read from the .env any more (AP_LLM_PROVIDER is ignored); it stays for code and tests that set it: ``auto``
    (the default), ``local``, ``azure`` (only with the internet allowed, AP_ALLOW_INTERNET=1) or ``off`` (the chat
    model is never asked). An empty ``model`` means the chat model LM Studio has, found on its own.
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
    # Requests sent to the model server at once (chat and page reader alike, D-111): 1 = strictly one at a time
    # (LM Studio on a laptop is shared with CloseDesk); more for a server that answers several together (LM Studio's
    # parallel requests, llama.cpp's --parallel, vLLM). AP_LLM_PARALLEL; the company server's ``service install
    # --llm-parallel``.
    parallel: int = 1


@dataclass(frozen=True)
class PageReaderSettings:
    """The page reader: OvisOCR2 in LM Studio reads every page of every invoice (digital PDFs, scans and photos), an
    independent reader beside the PDF's text and OCR (see ap_coder/ai/page_reader.py). Nothing to choose: it is always
    on, found in LM Studio on its own and trusted once its self-test passed.

    The fields are developer overrides, not settings for AP: ``model`` ("" = OvisOCR2, found among LM Studio's
    models), ``base_url`` ("" = the chat model's server, AP_LLM_BASE_URL), how long a page may take, and how many
    pages of an invoice are read.
    """

    model: str = ""
    base_url: str = ""
    timeout_seconds: float = 1200.0  # a laptop CPU takes minutes a page, most of it looking before the first word
    max_pages: int = 5
    # Leave out a digital PDF whose own text the rule reader read in full (D-111): AP_PAGE_READER_SKIP_CLEAN, on by
    # default on the company server only; ``skip_sample`` of those are read all the same, so its accuracy stays
    # measured (AP_PAGE_READER_SAMPLE).
    skip_clean: bool = False
    skip_sample: float = 0.1


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
    page_reader: PageReaderSettings = field(default_factory=PageReaderSettings)

    @classmethod
    def from_env(cls, env_file: str | Path | None = None) -> Settings:
        if env_file is None:
            # The data folder's .env (written by the installer), else ./.env, else the project's.
            env_file = paths.env_file()
        # Read here (UTF-8 or Notepad's ANSI), never searched for: without a file, python-dotenv would load the first
        # .env in any folder above AP Coder's code. Values are taken as written: a saved key or password with "$" in
        # it is never expanded like a shell variable.
        if env_file is not None and Path(env_file).is_file():
            load_env_file(Path(env_file))

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
        vision_mode = (_env("AP_LLM_VISION", "auto") or "auto").lower()
        vision_mode = {"true": "on", "yes": "on", "1": "on", "false": "off", "no": "off", "0": "off"}.get(
            vision_mode, vision_mode
        )
        model = _env("AP_LLM_MODEL", "") or ""
        llm = LocalLLMSettings(
            base_url=normalise_base_url(_env("AP_LLM_BASE_URL")),
            model="" if model.lower() == "auto" else model,
            api_key=_env("AP_LLM_API_KEY", "lm-studio"),
            timeout_seconds=_env_float("AP_LLM_TIMEOUT_SECONDS", 600.0),
            max_output_tokens=_env_int("AP_LLM_MAX_TOKENS", 4096),
            vision=vision_mode if vision_mode in LLM_VISION_MODES else "auto",
            max_prompt_chars=_env_int("AP_LLM_MAX_PROMPT_CHARS", 24_000),
            temperature=_env_float("AP_LLM_TEMPERATURE", 0.0),
            parallel=min(32, max(1, _env_int("AP_LLM_PARALLEL", 1) or 1)),
        )
        return cls(document_intelligence=di, openai=oai, engine=engine, llm=llm, page_reader=_page_reader_from_env())

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


def _page_reader_from_env() -> PageReaderSettings:
    """The page reader's developer overrides (AP_PAGE_READER_MODEL, _BASE_URL, _TIMEOUT_SECONDS, _MAX_PAGES). A value
    that isn't a positive number keeps the default, so a typo never stops the app. AP_PAGE_READER and
    AP_PAGE_READER_SCOPE, which an older AP Coder read, are ignored: the page reader is always on, for every invoice."""
    default = PageReaderSettings()
    model = _env("AP_PAGE_READER_MODEL", "") or ""
    base_url = _env("AP_PAGE_READER_BASE_URL")
    timeout = _env_float("AP_PAGE_READER_TIMEOUT_SECONDS", default.timeout_seconds)
    max_pages = _env_int("AP_PAGE_READER_MAX_PAGES", default.max_pages)
    on_server = (os.environ.get("AP_SERVER_MODE") or "").strip().lower() in ("1", "true", "yes", "on")
    sample = _env_float("AP_PAGE_READER_SAMPLE", default.skip_sample)
    return PageReaderSettings(
        skip_clean=_env_bool("AP_PAGE_READER_SKIP_CLEAN", on_server),
        skip_sample=min(1.0, max(0.0, sample)) if sample == sample else default.skip_sample,
        model="" if model.lower() in {"auto", "automatic"} else model,
        base_url=normalise_base_url(base_url) if base_url else "",
        timeout_seconds=timeout if timeout and 0 < timeout < float("inf") else default.timeout_seconds,
        max_pages=max_pages if max_pages and max_pages > 0 else default.max_pages,
    )
