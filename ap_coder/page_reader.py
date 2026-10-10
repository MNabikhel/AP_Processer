"""The page reader: a model that can see reads a scanned or photographed invoice and writes down its text, tables too.

OCR reads a scan's words but loses its tables, and on a phone photo it misreads digits and runs column titles
together. A vision language model in LM Studio reads the page whole. The recommended one is OvisOCR2 (0.85B,
Apache-2.0, a Qwen3.5-0.8B trained to read document pages): on 29 scanned pages it had never seen, it put 99.6% of
the figures in their right row and invented none, at about 3 minutes a page on a 4-core laptop CPU. Its
transcription is one more independent reader in capture: the rule reader reads it, each value is found back on the
page, and a value OCR and the page reader agree on counts as two readers (see ``capture.transcript``).

- Which model reads pages (``reader_status``): a document reader LM Studio has downloaded (OvisOCR2), else the chat
  model when it can see. LM Studio is asked to load a document reader with the context a page needs
  (``load_reader``), since one it loads on its own gets a context too short for a page.
- Each page is rendered at the reader's resolution (``render_pages``) and read with a streaming chat completion,
  greedy, stopped as soon as the model starts repeating itself and read once more with sampling (``transcribe``).
- Readings are kept on disk by file, page, model and prompt (``read_document``), so a page is never read twice, and
  their timings tell how long a page takes on this computer (``page_seconds_estimate``).
- ``test_reader`` reads a scan of a bundled sample invoice and compares its fields with the sample's ground truth.

Invoices are confidential: a page's text is never logged, only timings and the model.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import io
import json
import logging
import os
import random
import re
import statistics
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from . import local_llm, paths
from .config import _on_this_network

if TYPE_CHECKING:
    from .config import LocalLLMSettings, Settings

log = logging.getLogger(__name__)


# --- How a model is asked to read a page --------------------------------------------------------------------------


@dataclass(frozen=True)
class Reader:
    """How a model is asked to read a page: its prompt, the page's resolution (DPI, and the long side in pixels at
    most), the most tokens its reading may take, and the context LM Studio loads it with (0: as LM Studio has it)."""

    label: str
    prompt: str
    dpi: int
    max_side: int
    max_tokens: int
    context: int


# OvisOCR2's own prompt, which asks for tables in HTML (merged headings and all). 200 DPI with the long side at
# 2,048 pixels (LM Studio shrinks a larger picture to that anyway) gives it about 3,700 image tokens of a letter page;
# with a reading of up to 12,288 tokens it is loaded with a 20,480-token context.
OVIS_PROMPT = (
    "\nExtract all readable content from the image in natural human reading order and output the result as a single "
    'Markdown document. For charts or images, represent them using an HTML image tag: <img src="images/bbox_{left}_'
    '{top}_{right}_{bottom}.jpg" />, where left, top, right, bottom are bounding box coordinates scaled to [0, 1000). '
    "Format formulas as LaTeX. Format tables as HTML: <table>...</table>. Transcribe all other text as standard "
    "Markdown. Preserve the original text without translation or paraphrasing."
)
# A general model that can see (Qwen 3.5, Gemma 3) is told what a faithful transcription is: markdown tables, every
# number exactly as printed, nothing added.
GENERAL_PROMPT = (
    "Transcribe this document page exactly. Return all the text as it appears, top to bottom. "
    "Write every table as a markdown table with its column headings, one table row per printed row, and an empty "
    "cell where the page leaves a cell blank. Copy every number exactly as printed, with its commas, decimals, "
    "currency signs, minus signs and parentheses. Do not use bold or other formatting. Do not calculate, summarize, "
    "or add anything that is not on the page. If something can't be read, write [unreadable]."
)
OVIS = Reader("OvisOCR2, a document reader", OVIS_PROMPT, 200, 2048, 12288, 20480)
# About 150 DPI and 1,600 pixels: a general model pays for every image token; 8,192 tokens is a dense page's
# reading with room to spare. LM Studio keeps the context it was loaded with (0).
GENERAL = Reader("a general model that can see", GENERAL_PROMPT, 150, 1600, 8192, 0)
# Models made for reading document pages, by a word in their name: preferred over a general model when downloaded.
READERS = {"ovisocr": OVIS}


def _plain(model: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (model or "").lower())


def reader_for(model: str) -> Reader:
    """How this model reads pages: a known document reader's own way, else a general model's."""
    plain = _plain(model)
    return next((reader for word, reader in READERS.items() if word in plain), GENERAL)


def document_reader(model: str) -> bool:
    """The model is made for reading document pages (OvisOCR2), and is trusted like one."""
    return reader_for(model) is not GENERAL


def reader_name(model: str) -> str:
    """The page reader as AP knows it: a document reader by its name ("OvisOCR2"), any other model by its id."""
    reader = reader_for(model)
    return reader.label.split(",")[0] if reader is not GENERAL else model


# --- Errors ------------------------------------------------------------------------------------------------------


class PageReaderError(RuntimeError):
    """The page couldn't be read: no model can read pages, the server failed, or the reading wasn't usable."""


class CutOff(PageReaderError):
    """The reading stopped at its length limit part way down the page, or the model got stuck repeating itself."""


class Blank(PageReaderError):
    """The model finished without writing anything: there is nothing on the page to read (the back of a sheet)."""


class Stopped(PageReaderError):
    """``should_stop`` asked to stop while the page was being read."""


class _NothingWritten(PageReaderError):
    """The model wrote nothing, and not because the page is blank (it only thought, or the server stopped)."""


# --- Which model reads pages ----------------------------------------------------------------------------------------


def reader_base_url(settings: Settings) -> str:
    """The page reader's server: AP_PAGE_READER_BASE_URL, else the chat model's (AP_LLM_BASE_URL)."""
    return settings.page_reader.base_url or settings.llm.base_url


def _reader_llm(settings: Settings) -> LocalLLMSettings:
    """``settings.llm`` pointed at the page reader's server; the chat model's own name only on its own server."""
    llm = settings.llm
    base = reader_base_url(settings)
    timeout = settings.page_reader.timeout_seconds
    if base.rstrip("/") == llm.base_url.rstrip("/"):
        return replace(llm, timeout_seconds=timeout)
    return replace(llm, base_url=base, model="", timeout_seconds=timeout)


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

    def find(self, name: str) -> LMModel | None:
        return next((model for model in self.models if model.named(name)), None)


def _int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def parse_lm_studio_listing(data: Any) -> LMListing | None:
    """LM Studio's ``/api/v1/models`` (0.4: ``models``, each with its ``loaded_instances``) or ``/api/v0/models``
    (0.3: ``data``, each with a ``state``) as a listing of its chat and vision models; embedding models are left out.
    None for an answer that isn't LM Studio's (a server answering with a plain list of models)."""
    if not isinstance(data, dict):
        return None
    models: list[LMModel] = []
    if isinstance(data.get("models"), list):
        for item in data["models"]:
            if not isinstance(item, dict) or item.get("type") not in {"llm", "vlm"} or not item.get("key"):
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
                    key=str(item["key"]),
                    vision=caps.get("vision") is True or item.get("type") == "vlm",
                    instances=tuple(instances),
                    max_context=_int(item.get("max_context_length")),
                    reasoning=tuple(str(option) for option in options),
                )  # fmt: skip
            )
        return LMListing("v1", tuple(models))
    items = data.get("data")
    if isinstance(items, list) and any(isinstance(item, dict) and "state" in item for item in items):
        for item in items:
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
        return LMListing("v0", tuple(models))
    return None


LISTING_TIMEOUT = 3.0  # seconds: LM Studio lists its models in milliseconds, even while it reads a page
# LM Studio's listing, kept as long as local_llm keeps the server status it was fetched with (the same object): it
# is asked again when that is, and local_llm.forget_status() (Settings saved, Test connection) refreshes both.
_listings: dict[str, tuple[Any, LMListing | None]] = {}
_listings_lock = threading.Lock()


def _listing_for(settings: Settings, status: Any) -> LMListing | None:
    if not status.reachable or not status.lm_studio:
        return None
    root = local_llm._root(status.base_url)
    with _listings_lock:
        kept = _listings.get(root)
    if kept and kept[0] is status:
        return kept[1]
    listing = None
    for route in ("/api/v1/models", "/api/v0/models"):
        try:
            # Through local_llm, so a server on this computer is asked without a proxy (and tests can stand in).
            data = local_llm._fetch_json(root + route, settings.llm.api_key, LISTING_TIMEOUT)
        except (OSError, ValueError):
            continue
        listing = parse_lm_studio_listing(data)
        if listing is not None:
            break
    with _listings_lock:
        _listings[root] = (status, listing)
    return listing


def lm_studio_listing(settings: Settings, *, use_cache: bool = True) -> LMListing | None:
    """The page reader's server's model list when it is LM Studio, else None (not LM Studio, or not answering)."""
    status = local_llm.check_server(_reader_llm(settings), use_cache=use_cache)
    return _listing_for(settings, status)


def forget_status() -> None:
    """Ask the model server again next time (after the settings are saved, or LM Studio loaded a model)."""
    with _listings_lock:
        _listings.clear()
    local_llm.forget_status()


@dataclass
class ReaderStatus:
    """Which model reads pages now, and why (for the Settings page and the background reader)."""

    reachable: bool
    lm_studio: bool
    model: str  # the model that would read pages now ("" = none)
    document_reader: bool  # it is a document reader (OvisOCR2), trusted as one
    state: str  # "loaded" | "downloaded" | "missing" | "off" | "down"
    candidates: list[str] = field(default_factory=list)  # every model that can see (keys), document readers first
    note: str = ""  # plain-English why, or what to do
    base_url: str = ""
    context: int = 0  # the context the model is loaded with now (LM Studio; 0: not loaded, or not said)

    @property
    def usable(self) -> bool:
        """A model can read pages now (LM Studio loads a downloaded one when asked)."""
        return bool(self.model) and self.state in {"loaded", "downloaded"}


NOT_DOWNLOADED = "OvisOCR2 isn't downloaded in LM Studio: search OvisOCR2, bartowski build, Q8_0, about 1 GB."
TRIES_AGAIN = "AP Coder tries it again in half an hour, or when the settings are saved."


def reader_status(settings: Settings, *, use_cache: bool = True) -> ReaderStatus:
    """The model that reads pages and its state. A model named in the settings (AP_PAGE_READER_MODEL) is used as it
    is ("missing" when the server doesn't have it). Automatic: a document reader LM Studio has downloaded, unless it
    couldn't load it lately; else the chat model when it can see; else none. Asks the server (cached briefly)."""
    base = reader_base_url(settings)
    status = local_llm.check_server(_reader_llm(settings), use_cache=use_cache)
    out = ReaderStatus(status.reachable, False, "", False, "down", base_url=base)
    listing = _listing_for(settings, status)
    out.lm_studio = listing is not None
    if listing is not None:
        seeing = [m for m in listing.models if m.vision]
        seeing.sort(key=lambda m: (not document_reader(m.key), not m.instances))
        out.candidates = [m.key for m in seeing]
    elif status.reachable:  # another server (Ollama, llama.cpp) doesn't say which can see: by name
        seeing = [m for m in status.models if document_reader(m) or local_llm.looks_like_vision(m)]
        if status.vision and status.model and status.model not in seeing:
            seeing.append(status.model)
        out.candidates = sorted(seeing, key=lambda m: not document_reader(m))
    if settings.page_reader.mode == "off":
        out.state, out.note = "off", "The page reader is turned off: scans and photos are read by OCR alone."
        return out
    if not status.reachable:
        out.note = f"Nothing answered at {base}: start LM Studio's server (Developer tab → Start server)."
        return out
    named = settings.page_reader.model.strip()
    if named:
        _use_named(out, named, status, listing)
    else:
        _use_automatic(out, status, listing)
    return out


def _describe(out: ReaderStatus, found: LMModel, reader: Reader) -> None:
    out.state = "loaded" if found.instances else "downloaded"
    out.context = found.loaded_context
    if not reader.context:
        out.note = ""
    elif not found.instances:
        out.note = f"LM Studio loads it with a {reader.context:,}-token context when a page is read."
    elif 0 < found.loaded_context < reader.context:
        out.note = (
            f"Loaded with a {found.loaded_context:,}-token context: AP Coder loads it again with "
            f"{reader.context:,} before reading a page."
        )


def _use_named(out: ReaderStatus, named: str, status: Any, listing: LMListing | None) -> None:
    out.model, out.document_reader = named, document_reader(named)
    if listing is not None:
        found = listing.find(named)
        if found is None:
            out.state = "missing"
            out.note = f"LM Studio doesn't have {named}: download it there, or choose Automatic for the page reader."
            return
        _describe(out, found, reader_for(named))
        if not found.vision:
            out.note = f"LM Studio says {named} can't look at pictures: choose one that can (OvisOCR2), or Automatic."
        return
    if named in status.models:
        out.state = "loaded"
        return
    out.state = "missing"
    out.note = f"The server at {out.base_url} doesn't list {named}: choose a model it has, or Automatic."


def _ready(model: LMModel) -> bool:
    """Loaded with the context its reading needs (or one LM Studio doesn't say)."""
    context = reader_for(model.key).context
    return bool(model.instances) and (not model.loaded_context or model.loaded_context >= context)


def _use_automatic(out: ReaderStatus, status: Any, listing: LMListing | None) -> None:
    failed = ""
    if listing is not None:
        readers = sorted(
            (m for m in listing.models if m.vision and document_reader(m.key)), key=lambda m: not m.instances
        )
        for model in readers:
            problem = reader_load_problem(model.key)
            if problem and not _ready(model):
                failed = failed or problem
                continue
            out.model, out.document_reader = model.key, True
            _describe(out, model, reader_for(model.key))
            return
    else:
        named = next((m for m in status.models if document_reader(m)), "")
        if named:
            out.model, out.document_reader, out.state = named, True, "loaded"
            return
    if status.active and status.vision:  # the chat model can look at pictures: it reads pages
        out.model, out.document_reader = status.model, document_reader(status.model)
        found = listing.find(status.model) if listing is not None else None
        if found is not None:
            _describe(out, found, reader_for(status.model))
        else:
            out.state = "loaded"
        if failed:
            out.note = f"{failed} {TRIES_AGAIN} Meanwhile {status.model} reads pages."
        elif listing is not None:
            out.note = f"{NOT_DOWNLOADED} It reads pages more accurately than {status.model}."
        return
    out.state = "missing"
    if failed:
        out.note = f"{failed} {TRIES_AGAIN}"
    elif listing is not None:
        out.note = f"{NOT_DOWNLOADED} Until then, scans and photos are read by OCR alone."
    else:
        out.note = (
            f"{status.server_title} has no model that can look at pictures: scans and photos are read by OCR alone. "
            "OvisOCR2 in LM Studio reads pages best."
        )


def _reading_model(settings: Settings) -> str:
    status = reader_status(settings)
    if not status.usable:
        raise PageReaderError(status.note or "No model can read pages now.")
    return status.model


# --- Loading it in LM Studio ----------------------------------------------------------------------------------------

LOAD_TIMEOUT = 300.0  # seconds: loading a model from disk; a large general model on a slow disk takes a minute
READER_RETRY_SECONDS = 1800.0
# Models LM Studio couldn't load with the context a page needs: (when, what it said). Not asked again for half an hour,
# or until the settings are saved, so a computer without the memory isn't asked before every page.
_load_failures: dict[str, tuple[float, str]] = {}
_load_lock = threading.Lock()


def _post_json(url: str, body: dict[str, Any], api_key: str, timeout: float) -> Any:
    """POST JSON to LM Studio's own routes (a server on this computer or network: no proxy). Raises
    ``PageReaderError`` with LM Studio's own reason (not enough memory, say) when it refuses."""
    request = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"Authorization": f"Bearer {api_key or 'lm-studio'}", "Content-Type": "application/json"},
    )  # fmt: skip
    local = _on_this_network(urlparse(url).netloc)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({})) if local else urllib.request.build_opener()
    try:
        with opener.open(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        raise PageReaderError(_lm_studio_said(exc)) from exc
    except OSError as exc:
        raise PageReaderError(local_llm._short_error(exc)) from exc
    try:
        return json.loads(raw.decode("utf-8", "replace") or "null")
    except ValueError:
        return None


def _lm_studio_said(exc: urllib.error.HTTPError) -> str:
    """LM Studio's reason for refusing (``{"error": "..."}`` or ``{"error": {"message": "..."}}``), not a bare code."""
    try:
        error = json.loads(exc.read().decode("utf-8", "replace")).get("error")
    except (OSError, ValueError, AttributeError):
        error = None
    said = error.get("message") if isinstance(error, dict) else error
    if isinstance(said, str) and said.strip():
        return " ".join(said.split())[:200]
    return f"HTTP {exc.code}"


def reader_load_problem(model: str) -> str:
    """Why LM Studio couldn't load this page reader lately (asked again after half an hour), or ""."""
    failed = _load_failures.get(model)
    return failed[1] if failed and time.monotonic() - failed[0] < READER_RETRY_SECONDS else ""


def forget_reader_failures() -> None:
    """The settings were saved: a model LM Studio couldn't load is asked again (memory may have been freed)."""
    _load_failures.clear()


def load_reader(settings: Settings, model: str | None = None) -> str:
    """Have LM Studio load the page reader (``model``, else the one ``reader_status`` names) with the context a page
    and its reading need (OvisOCR2: 20,480 tokens) before pages are read: one LM Studio loads on its own when first
    asked gets its default context, too short for a page. Already loaded with at least that context: nothing to do.
    Loaded shorter: loaded again longer, the short instance unloaded once the long one is in. Only LM Studio loads
    models when asked, and a general model (no context of its own) is left as LM Studio has it.

    Returns "" or what went wrong (LM Studio's reason: not enough memory, say). A failure is remembered for half an
    hour so a computer without the memory isn't asked before every page (``forget_reader_failures``)."""
    model = (model or "").strip()
    if not model:
        status = reader_status(settings)
        if not status.model:
            return status.note or "No model can read pages now."
        model = status.model
    reader = reader_for(model)
    if not reader.context:
        return ""
    with _load_lock:
        listing = lm_studio_listing(settings, use_cache=False)
        if listing is None:
            return ""  # not LM Studio (nothing to ask), or not answering (the reading will say so)
        found = listing.find(model)
        if found is None:
            return f"LM Studio doesn't have {model} downloaded."
        if _ready(found):
            return ""
        failed = _load_failures.get(found.key)
        if failed and time.monotonic() - failed[0] < READER_RETRY_SECONDS:
            return failed[1]
        name = reader_name(found.key)
        if listing.route != "v1":  # LM Studio 0.3 has no route to load a model with
            return (
                f"Load {name} in LM Studio with Context Length {reader.context:,} (LM Studio 0.4 does it when asked)."
            )
        root = local_llm._root(reader_base_url(settings))
        api_key = settings.llm.api_key
        started = time.monotonic()
        try:
            _post_json(root + "/api/v1/models/load", {"model": found.key, "context_length": reader.context}, api_key,
                       LOAD_TIMEOUT)  # fmt: skip
        except PageReaderError as exc:
            problem = f"LM Studio couldn't load {name} with a {reader.context:,}-token context ({exc})."
            _load_failures[found.key] = (time.monotonic(), problem)
            log.warning("%s", problem)
            return problem
        finally:
            forget_status()
        _load_failures.pop(found.key, None)
        log.info("LM Studio loaded %s with a %d-token context in %.0f s", found.key, reader.context,
                 time.monotonic() - started)  # fmt: skip
        for instance, _size in found.instances:  # the shorter one, now that the longer one is in
            try:
                _post_json(root + "/api/v1/models/unload", {"instance_id": instance}, api_key, LOAD_TIMEOUT)
            except PageReaderError as exc:
                log.info("LM Studio didn't unload %s (%s)", instance, exc)
        return ""


# --- Looking at a page ------------------------------------------------------------------------------------------------

# PyMuPDF isn't made for two threads at once (a page read in the background while another is shown).
_RENDER_LOCK = threading.Lock()
# A picture records the resolution it was scanned at; a phone photo's 72 is no scan resolution.
_SCAN_DPI = 100


def _is_pdf(name: str, data: bytes) -> bool:
    return Path(name).suffix.lower() == ".pdf" or data[:5] == b"%PDF-"


def _page_count(data: bytes, pdf: bool) -> int:
    """The file's pages: a PDF's, or a picture's frames (a multi-page TIFF)."""
    if pdf:
        import pymupdf

        with _RENDER_LOCK, pymupdf.open(stream=data, filetype="pdf") as doc:
            return doc.page_count
    from PIL import Image

    try:
        with Image.open(io.BytesIO(data)) as img:
            return max(1, int(getattr(img, "n_frames", 1) or 1))
    except Exception as exc:  # Pillow raises several kinds for a file that isn't a picture
        raise PageReaderError("it isn't a PDF or a picture") from exc


def _render(data: bytes, pdf: bool, reader: Reader, numbers: Iterable[int]) -> dict[int, bytes]:
    """The pages ``numbers`` (from 1; those the file has) as PNGs at the reader's resolution."""
    wanted = sorted(set(numbers))
    out: dict[int, bytes] = {}
    if not wanted:
        return out
    if pdf:
        import pymupdf

        with _RENDER_LOCK, pymupdf.open(stream=data, filetype="pdf") as doc:
            for number in wanted:
                if number > doc.page_count:
                    break
                page = doc[number - 1]  # as displayed: its rotation applied
                zoom = min(reader.dpi / 72, reader.max_side / max(page.rect.width, page.rect.height, 1))
                out[number] = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False).tobytes("png")
        return out
    from PIL import Image, ImageOps, ImageSequence

    from .capture.layout import _on_paper

    try:
        img = Image.open(io.BytesIO(data))
    except Exception as exc:  # Pillow raises several kinds for a file that isn't a picture
        raise PageReaderError("it isn't a PDF or a picture") from exc
    with img:
        for number, frame in enumerate(ImageSequence.Iterator(img), start=1):
            if number > wanted[-1]:
                break
            if number not in wanted:
                continue
            dpi = frame.info.get("dpi") or img.info.get("dpi") or (0, 0)
            pic = _on_paper(ImageOps.exif_transpose(frame.copy()))
            scale = reader.max_side / max(pic.width, pic.height, 1)
            try:
                scanned = float(dpi[0])
            except (TypeError, ValueError, IndexError):
                scanned = 0.0
            if scanned >= _SCAN_DPI and scanned > reader.dpi:  # a fine scan: brought to the reader's resolution
                scale = min(scale, reader.dpi / scanned)
            if scale < 1:
                size = (max(1, round(pic.width * scale)), max(1, round(pic.height * scale)))
                pic = pic.resize(size, Image.LANCZOS)
            buf = io.BytesIO()
            pic.save(buf, "PNG")
            out[number] = buf.getvalue()
    return out


def render_pages(path: Path, reader: Reader, max_pages: int) -> list[bytes]:
    """The file's first ``max_pages`` pages as PNGs the reader reads: a PDF's pages rendered at the reader's DPI
    (PyMuPDF), a picture (each frame of a multi-page TIFF) turned upright as its EXIF says and brought down to the
    reader's DPI when it records a finer scan; the long side at most the reader's ``max_side`` either way. Raises
    ``PageReaderError`` for a file that is neither a PDF nor a picture."""
    path = Path(path)
    data = path.read_bytes()
    pages = _render(data, _is_pdf(path.name, data), reader, range(1, max(0, max_pages) + 1))
    return [pages[number] for number in sorted(pages)]


# --- Reading it -------------------------------------------------------------------------------------------------------

# Greedy decoding copies a page most faithfully, but now and then falls into writing one line or cell over and over. A
# page it loops on is read once more with the sampling Qwen recommends for its instruct models, which breaks such
# loops (CloseDesk: a scanned report that looped from its first line had all 162 figures right the second time).
RETRY_SAMPLING = {"temperature": 0.7, "top_p": 0.8, "top_k": 20, "presence_penalty": 1.5}
_EXTRA_SAMPLING = ("top_k", "presence_penalty")  # not OpenAI parameters: sent in the request body as they are
# A looped reading shorter than this (once the loop is cut) stopped near the top of the page.
MIN_LOOPED_TEXT = 200


def _attr(obj: Any, name: str) -> Any:
    """A field of a streamed chunk: an SDK object (fields it doesn't know are in ``model_extra``) or a dict."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(name)
    value = getattr(obj, name, None)
    if value is None:
        extra = getattr(obj, "model_extra", None)
        value = extra.get(name) if isinstance(extra, dict) else None
    return value


def _client(settings: Settings) -> Any:
    """The OpenAI client for the page reader's server. It waits up to AP_PAGE_READER_TIMEOUT_SECONDS for each word
    (the model looks at the page for minutes before the first one) and never retries on its own: a page that failed is
    read again later, not at once (a retry would double a ten-minute wait)."""
    import openai

    client = local_llm.build_local_client(_reader_llm(settings))
    timeout = settings.page_reader.timeout_seconds
    return client.with_options(timeout=openai.Timeout(timeout, connect=10.0), max_retries=0)


def _failure(exc: Exception, settings: Settings) -> str:
    """What went wrong talking to the model server, in a sentence (never the page's text). A stream that fails part
    way raises the HTTP library's own errors (httpx's, or httpx2's under newer OpenAI SDKs): known by their names."""
    import openai

    kinds = {kind.__name__ for kind in type(exc).__mro__}
    if isinstance(exc, (openai.APITimeoutError, TimeoutError)) or "TimeoutException" in kinds:
        return (
            f"no word from the model in {settings.page_reader.timeout_seconds:.0f} s "
            "(AP_PAGE_READER_TIMEOUT_SECONDS allows longer)"
        )
    if isinstance(exc, (openai.APIConnectionError, ConnectionError)) or "TransportError" in kinds:
        return f"the model server isn't answering at {reader_base_url(settings)}"
    if local_llm.context_overflow(exc):
        return "the page didn't fit the model's context: load it in LM Studio with a longer context"
    status = getattr(exc, "status_code", None)
    text = " ".join(str(exc).split())[:200] or type(exc).__name__
    return f"the model server answered {status}: {text}" if status else f"the model server failed: {text}"


def _open_stream(client: Any, base: str, model: str, params: dict[str, Any], sampling: dict[str, Any],
                 settings: Settings) -> Any:  # fmt: skip
    """Start the streaming request, thinking switched off as for coding calls (OvisOCR2 takes reasoning "off"). A
    server that refuses fields it doesn't know gets the request again with fewer: the sampling fields first (the
    greedy reading went through with the thinking switch), then the thinking fields, remembered per model."""
    sampling = dict(sampling)
    while True:
        kwargs = dict(params)
        extra = {**local_llm.thinking_off(base, model).get("extra_body", {}), **sampling}
        if extra:
            kwargs["extra_body"] = extra
        try:
            return client.chat.completions.create(**kwargs)
        except Exception as exc:  # noqa: BLE001 - every failure becomes a PageReaderError the page can show
            if "extra_body" in kwargs and local_llm.refuses_extra_fields(exc):
                if sampling:
                    log.info("%s refused top_k / presence_penalty (%s); sampling without them", model, exc)
                    sampling = {}
                    continue
                if local_llm.refuse_thinking_off(base, model):
                    log.info("%s refused the thinking switch (%s); asking without it", model, exc)
                    continue
            raise PageReaderError(_failure(exc, settings)) from exc


def transcribe(settings: Settings, png: bytes, *, model: str | None = None,
               should_stop: Callable[[], bool] | None = None) -> str:  # fmt: skip
    """The model's reading of one page (a PNG): its text, with tables in HTML (OvisOCR2) or markdown. ``model``: the
    one that reads pages (``reader_status``), asked its own way (``reader_for``).

    A streaming chat completion, greedy (temperature 0), with the reader's token limit; the model may take
    AP_PAGE_READER_TIMEOUT_SECONDS to write each word. When it starts writing one thing over and over, the reading is
    stopped there and the page is read once more with Qwen's sampling. Raises ``CutOff`` (stopped at the length
    limit, or stuck repeating itself twice), ``Blank`` (nothing on the page), ``Stopped`` (``should_stop``) or
    ``PageReaderError`` (the server failed, or no model can read pages)."""
    model = (model or "").strip() or _reading_model(settings)
    reader = reader_for(model)
    data_url = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
    messages = [
        {
            "role": "user",
            "content": [{"type": "image_url", "image_url": {"url": data_url}}, {"type": "text", "text": reader.prompt}],
        }
    ]
    client = _client(settings)
    text, looped = _transcribe_once(settings, client, model, reader, messages, None, should_stop)
    if not looped:
        return text
    log.info("Page reader %s looped on a page; reading it once more with sampling", model)
    try:
        again, looped_again = _transcribe_once(settings, client, model, reader, messages, RETRY_SAMPLING, should_stop)
    except (CutOff, Blank, _NothingWritten):
        again, looped_again = "", True
    best = again if not looped_again else max(text, again, key=len)
    if len(best) < MIN_LOOPED_TEXT:
        raise CutOff("the model got stuck repeating itself near the top of the page")
    return best


def _transcribe_once(settings: Settings, client: Any, model: str, reader: Reader, messages: list[dict[str, Any]],
                     sampling: dict[str, Any] | None,
                     should_stop: Callable[[], bool] | None) -> tuple[str, bool]:  # fmt: skip
    """One reading of the page, stopped early when it loops: (its text without the loop, whether it looped)."""
    params: dict[str, Any] = {"model": model, "messages": messages, "max_tokens": reader.max_tokens,
                              "temperature": 0.0, "stream": True}  # fmt: skip
    extra: dict[str, Any] = {}
    if sampling:
        params["temperature"], params["top_p"] = sampling["temperature"], sampling["top_p"]
        extra = {key: sampling[key] for key in _EXTRA_SAMPLING}
    stream = _open_stream(client, reader_base_url(settings), model, params, extra, settings)
    written: list[str] = []
    finish = ""
    thought = looped = False
    size = checked = 0
    try:
        for chunk in stream:
            if should_stop and should_stop():
                raise Stopped("stopped before the page was read")
            piece = ""
            for choice in _attr(chunk, "choices") or []:
                delta = _attr(choice, "delta")
                thought = thought or bool(_attr(delta, "reasoning_content") or _attr(delta, "reasoning"))
                content = _attr(delta, "content")
                piece += content if isinstance(content, str) else ""
                finish = str(_attr(choice, "finish_reason") or finish)
            if not piece:
                continue
            written.append(piece)
            size += len(piece)
            if "\n" in piece or size - checked >= 400:
                checked = size
                if _looping(_visible("".join(written))):
                    looped = True  # stopped here: the rest would be the same until the token limit
                    break
    except PageReaderError:
        raise
    except Exception as exc:  # noqa: BLE001 - the server failed part way (an error in the stream, a dropped link)
        raise PageReaderError(_failure(exc, settings)) from exc
    finally:
        close = getattr(stream, "close", None)
        if callable(close):
            try:
                close()  # LM Studio stops writing when the reply is closed
            except Exception:  # noqa: BLE001 - closing is a courtesy
                pass
    whole = "".join(written)
    thought = thought or "<think>" in whole.lower() or "</think>" in whole.lower()
    text, _trimmed = trim_loop(local_llm.strip_thinking(whole))
    text = text.strip()
    if not text and not looped:
        if thought:
            raise _NothingWritten(
                f"the model thought until its {reader.max_tokens:,}-token budget ran out and wrote nothing"
                if finish == "length"
                else "the model only thought and wrote nothing"
            )
        if not finish or finish == "length":
            raise _NothingWritten("the model server ended the reading without a word")
        raise Blank("the page has nothing on it to read")
    if finish == "length" and not looped:
        # Half a page would hide the rest of it: the reading is not kept.
        raise CutOff(f"the reading stopped at the {reader.max_tokens:,}-token limit before the end of the page")
    return text, looped


def _visible(text: str) -> str:
    """The reading without a <think> block (a model that thinks despite the switch)."""
    return local_llm.strip_thinking(text) if "think>" in text.lower() else text


# A model reading a page greedily can fall into writing one line over and over (an empty table row) until its token
# limit. A run this long of one line is that; a row of nothing but empty table cells only sooner than an invoice's own
# empty line-item rows (a dozen is common; thirty in a row is the model looping).
LOOP_LINES = 20
LOOP_BLANK_ROWS = 30
_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
# The same few characters over and over at the end of the reading: empty cells written across one line without end
# ("|  |  |  …"). Sixty in a row is more columns than any printed table has. A run of nothing but dots, dashes,
# underscores and spaces is the page's own (a line to sign on, a dot leader), not a loop.
_RUN = re.compile(r"(?!(?:[^\w|<>]|_)*$)(.{1,12}?)\1{59,}$", re.S)
# A longer stretch repeated on one line: OvisOCR2 writes a whole table on one line, so its loop on an empty row is
# "<tr><td></td><td></td></tr>" over and over. Thirty in a row, as for empty markdown rows.
LONG_RUN = (13, 160, LOOP_BLANK_ROWS)


def _repeated_tail(lines: list[str]) -> int:
    """How many of the last lines are the same line."""
    if not lines:
        return 0
    last = lines[-1].strip()
    count = 0
    for line in reversed(lines):
        if line.strip() != last:
            break
        count += 1
    return count


def _blank_row(line: str) -> bool:
    return bool(_TABLE_ROW.match(line)) and not re.sub(r"[|\s:-]", "", line)


def _long_run(text: str) -> tuple[int, int]:
    """Where a run of one stretch of 13 to 160 characters, written thirty times or more, ends the text (start, length
    of the stretch); (-1, 0) when none does. A stretch repeats wherever each character is the one a stretch before
    it, so the run is found wherever the reading stopped within a stretch."""
    shortest, longest, times = LONG_RUN
    tail = text[-longest * (times + 1) :]
    for size in range(shortest, longest + 1):
        span = size * times
        if len(tail) >= span + size and tail[-span:] == tail[-span - size : -size]:
            start = len(text) - span - size
            while start > 0 and text[start - 1] == text[start - 1 + size]:
                start -= 1
            return start, size
    return -1, 0


def _looping(text: str) -> bool:
    """The reading so far ends in a loop (complete lines only, so a line still being written isn't counted)."""
    lines = [line for line in text.split("\n")[:-1] if line.strip()]
    run = _repeated_tail(lines)
    if lines and run >= (LOOP_BLANK_ROWS if _blank_row(lines[-1]) else LOOP_LINES):
        return True
    return bool(_RUN.search(text[-1500:].rstrip())) or _long_run(text.rstrip())[0] >= 0


def trim_loop(text: str) -> tuple[str, bool]:
    """The reading without a line the model repeated at its end (kept once when it says something), or without the
    characters it repeated across its last line; whether anything was cut. Blank lines between repeated lines are
    passed over, as ``_looping`` does."""
    tail = _RUN.search(text[-1500:].rstrip())
    if tail:
        cut = len(text[-1500:].rstrip()) - len(tail.group(0))
        start = text.rstrip()[: len(text.rstrip()) - len(text[-1500:].rstrip()) + cut].rstrip(" |")
        last = start.rsplit("\n", 1)[-1]
        return start + (" |" if last.lstrip().startswith("|") else ""), True  # the table row closed where it stopped
    lines = text.rstrip().split("\n")
    filled = [index for index, line in enumerate(lines) if line.strip()]
    if len(filled) > 1:
        last, before = lines[filled[-1]].strip(), lines[filled[-2]].strip()
        if last != before and before.startswith(last):
            filled = filled[:-1]  # the repeated line, cut off part way when the reply stopped
    kept = [lines[index] for index in filled]
    run = _repeated_tail(kept)
    if not kept or run < (LOOP_BLANK_ROWS if _blank_row(kept[-1]) else LOOP_LINES):
        return _without_long_run(text)
    first_dropped = len(kept) - run + (0 if _blank_row(kept[-1]) else 1)
    end = filled[first_dropped] if first_dropped < len(filled) else len(lines)
    return "\n".join(lines[:end]).rstrip(), True


def _without_long_run(text: str) -> tuple[str, bool]:
    """The reading without one stretch of a line repeated at its end, the stretch kept once (it can be a row the page
    has), from a line or tag end on: the run can begin part way into a row."""
    clean = text.rstrip()
    start, size = _long_run(clean)
    if start < 0:
        return text, False
    ends = [i for i in (clean.find("\n", start, start + size), clean.find(">", start, start + size)) if i >= 0]
    end = min(ends) + 1 + size if ends else start + size
    return clean[:end].rstrip(), True


# --- Readings kept on disk ------------------------------------------------------------------------------------------

# Raised when a reading made before would no longer be made the same way (beyond the prompt and resolution, which
# ``prompt_version`` follows by itself), so every page is read again.
CACHE_VERSION = 1
TIMINGS_FILE = "timings.jsonl"
RECENT_READS = 20  # the estimate is the mean of this computer's latest reads
_cache_lock = threading.Lock()


def cache_dir() -> Path:
    """Where readings are kept: the data folder's ``.cache/page_reader`` (one folder per file, by its SHA-256)."""
    return paths.private_dir() / ".cache" / "page_reader"


def prompt_version(reader: Reader) -> str:
    """The way a cached reading was made: the cache version and a digest of the reader's prompt and resolution, so a
    page is read again when either changes."""
    digest = hashlib.sha256(f"{reader.prompt}|{reader.dpi}|{reader.max_side}|{reader.max_tokens}".encode()).hexdigest()
    return f"{CACHE_VERSION}-{digest[:10]}"


def _cache_path(digest: str, page: int, model: str, version: str) -> Path:
    key = hashlib.sha256(f"{model}\n{version}".encode()).hexdigest()[:16]
    return cache_dir() / digest / f"p{page}-{key}.json"


def _cache_entry(path: Path) -> dict[str, Any] | None:
    try:
        entry = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(entry, dict) or not isinstance(entry.get("text"), str) or not isinstance(entry.get("model"), str):
        return None
    return entry


def _cache_get(digest: str, page: int, model: str) -> dict[str, Any] | None:
    version = prompt_version(reader_for(model))
    entry = _cache_entry(_cache_path(digest, page, model, version))
    if entry is None or entry.get("model") != model or entry.get("prompt_version") != version:
        return None
    return entry


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def _cache_put(digest: str, page: int, model: str, text: str, seconds: float) -> None:
    version = prompt_version(reader_for(model))
    entry = {"model": model, "prompt_version": version, "page": page, "text": text, "seconds": round(seconds, 1),
             "at": _now()}  # fmt: skip
    path = _cache_path(digest, page, model, version)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f"{path.name}.{os.getpid()}-{threading.get_ident()}.tmp")
        temporary.write_text(json.dumps(entry, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, path)
    except OSError as exc:
        log.warning("Page reader: couldn't keep a reading in %s (%s)", cache_dir(), exc)
    _log_timing(model, seconds)


def _log_timing(model: str, seconds: float) -> None:
    """One line per page read (model, seconds, when), for ``page_seconds_estimate``. Kept short: the latest 500."""
    path = cache_dir() / TIMINGS_FILE
    line = json.dumps({"model": model, "seconds": round(seconds, 1), "at": _now()}) + "\n"
    with _cache_lock:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(line)
            if path.stat().st_size > 256_000:
                lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
                path.write_text("".join(lines[-500:]), encoding="utf-8")
        except OSError as exc:
            log.debug("Page reader: couldn't note the timing (%s)", exc)


def page_seconds_estimate(settings: Settings, *, model: str | None = None) -> float | None:
    """How long a page takes on this computer: the mean of the latest page reads by ``model`` (else the model named
    in the settings, else any), or None before the first. Read from the timings kept with the readings; the model
    server isn't asked."""
    model = (model or settings.page_reader.model or "").strip()
    try:
        lines = (cache_dir() / TIMINGS_FILE).read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    seconds = []
    for line in lines:
        try:
            row = json.loads(line)
            if not model or row.get("model") == model:
                seconds.append(float(row["seconds"]))
        except (ValueError, TypeError, KeyError, AttributeError):
            continue
    recent = [value for value in seconds[-RECENT_READS:] if value >= 0]
    return round(statistics.fmean(recent), 1) if recent else None


def duration(seconds: float) -> str:
    """'about 40 seconds', 'about 3 minutes', 'about 1 hour 20 minutes'."""
    if seconds < 90:
        return f"about {max(10, round(seconds / 10) * 10)} seconds"
    minutes = round(seconds / 60)
    if minutes < 60:
        return f"about {minutes} minutes"
    hours, rest = divmod(minutes, 60)
    return f"about {hours} hour{'s' if hours > 1 else ''}" + (f" {rest} minutes" if rest >= 5 else "")


# --- Reading a document ---------------------------------------------------------------------------------------------


@dataclass
class PageReading:
    """The page reader's reading of a file: one text per page, in order (page 1 first)."""

    model: str
    pages: list[str]  # "" for a blank page, or one that couldn't be read (``error`` says which)
    seconds: list[float]  # each page's reading time (as first measured, for a page from the cache)
    cached: bool = False  # every page came from the cache: the model server wasn't asked
    error: str = ""
    stopped: bool = False  # ``should_stop`` asked to stop before every page was read
    page_count: int = 0  # the pages to read: the file's, at most AP_PAGE_READER_MAX_PAGES

    @property
    def complete(self) -> bool:
        """Every page was read (a blank page counts) and nothing failed."""
        return bool(self.pages) and len(self.pages) == self.page_count and not self.error and not self.stopped


def _file_pages(data: bytes, name: str, settings: Settings) -> tuple[str, bool, int]:
    """(SHA-256, is a PDF, pages to read)."""
    pdf = _is_pdf(name, data)
    return hashlib.sha256(data).hexdigest(), pdf, min(_page_count(data, pdf), settings.page_reader.max_pages)


def _cached_reading(digest: str, total: int, model: str | None) -> PageReading | None:
    """Every page (1..``total``) read by ``model`` as kept on disk; with no model, a document reader's reading, else
    the newest one. None when no model has read every page."""
    if total < 1:
        return None
    if model:
        entries = [_cache_get(digest, page, model) for page in range(1, total + 1)]
        if not all(entries):
            return None
        return PageReading(model, [e["text"] for e in entries], [float(e.get("seconds") or 0) for e in entries],
                           cached=True, page_count=total)  # fmt: skip
    by_model: dict[str, dict[int, dict[str, Any]]] = {}
    for path in (cache_dir() / digest).glob("p*.json"):
        entry = _cache_entry(path)
        if entry is None or entry.get("prompt_version") != prompt_version(reader_for(entry["model"])):
            continue
        page = _int(entry.get("page"))
        if 1 <= page <= total:
            by_model.setdefault(entry["model"], {})[page] = entry
    complete = [name for name, pages in by_model.items() if len(pages) == total]
    if not complete:
        return None

    def preference(name: str) -> tuple[bool, str]:
        return document_reader(name), max(str(entry.get("at")) for entry in by_model[name].values())

    best = max(complete, key=preference)
    entries = [by_model[best][page] for page in range(1, total + 1)]
    return PageReading(best, [e["text"] for e in entries], [float(e.get("seconds") or 0) for e in entries],
                       cached=True, page_count=total)  # fmt: skip


def cached_reading(settings: Settings, path: Path, *, model: str | None = None) -> PageReading | None:
    """The page reader's reading of this file as kept on disk, without asking the model server: every page (up to
    AP_PAGE_READER_MAX_PAGES) read by ``model``, else by the model named in the settings, else a document reader's
    reading, else the newest. None when no model has read every page."""
    path = Path(path)
    try:
        digest, _pdf, total = _file_pages(path.read_bytes(), path.name, settings)
    except (OSError, PageReaderError, RuntimeError, ValueError):
        return None
    return _cached_reading(digest, total, (model or settings.page_reader.model or "").strip() or None)


def _loaded(settings: Settings, model: str) -> bool:
    """LM Studio has the model loaded (any context), or the server isn't LM Studio (it serves what it lists)."""
    listing = lm_studio_listing(settings)
    if listing is None:
        return True
    found = listing.find(model)
    return found is not None and bool(found.instances)


def read_document(settings: Settings, path: Path, *, model: str | None = None,
                  on_page: Callable[[int, int], None] | None = None,
                  should_stop: Callable[[], bool] | None = None) -> PageReading:  # fmt: skip
    """The page reader's reading of every page of a PDF or picture (up to AP_PAGE_READER_MAX_PAGES).

    Each page is kept on disk by the file's SHA-256, the page, the model and the prompt version as soon as it is
    read, so a page is never read twice: pages already read come from there without asking the model server (all of
    them: ``cached``). ``model``: else the one in the settings, else a document reader's earlier reading of every page,
    else the model ``reader_status`` names (an earlier reading by any model is used while none can read pages).
    LM Studio is asked to load a document reader with the context it needs (``load_reader``) before the first page.

    ``on_page(n, total)`` is called as page n (from 1) starts. ``should_stop()`` is asked before each page and while a
    page is read; when it says stop, the pages read so far come back with ``stopped``. A page whose reading was cut
    off (``CutOff``) is left empty and named in ``error``, and the rest are read; when the server fails, reading stops
    there (``error``). Never raises for a file or server problem."""
    path = Path(path)
    try:
        data = path.read_bytes()
        digest, pdf, total = _file_pages(data, path.name, settings)
    except Exception as exc:  # noqa: BLE001 - a file PyMuPDF or Pillow can't open is reported, never raised
        return PageReading((model or "").strip(), [], [], error=f"couldn't open {path.name}: {exc}")
    if total < 1:
        return PageReading((model or "").strip(), [], [], error=f"{path.name} has no pages")
    named = (model or settings.page_reader.model or "").strip()
    earlier = _cached_reading(digest, total, named or None)
    if earlier is not None and (named or document_reader(earlier.model)):
        return earlier
    if not named:
        status = reader_status(settings)
        if not status.usable:
            if earlier is not None:
                return earlier  # an earlier reading beats none while no model can read pages
            return PageReading("", [], [], error=status.note or "No model can read pages now.", page_count=total)
        named = status.model
        if earlier is not None and earlier.model == named:
            return earlier
    entries = {page: _cache_get(digest, page, named) for page in range(1, total + 1)}
    reading = PageReading(named, [], [], page_count=total)
    if all(entries.values()):
        reading.pages = [entry["text"] for entry in entries.values() if entry]
        reading.seconds = [float(entry.get("seconds") or 0) for entry in entries.values() if entry]
        reading.cached = True
        return reading
    if should_stop and should_stop():
        reading.stopped = True
        return reading
    problem = load_reader(settings, named)
    if problem:
        log.warning("Page reader: %s", problem)
        if not _loaded(settings, named):  # LM Studio couldn't load it: nothing is counted against the pages
            reading.error = problem
            return reading
    reader = reader_for(named)
    problems: list[str] = []
    for page in range(1, total + 1):
        if on_page:
            on_page(page, total)
        entry = entries[page]
        if entry:
            reading.pages.append(entry["text"])
            reading.seconds.append(float(entry.get("seconds") or 0))
            continue
        if should_stop and should_stop():
            reading.stopped = True
            break
        try:
            png = _render(data, pdf, reader, [page])[page]
        except Exception as exc:  # noqa: BLE001 - a page PyMuPDF or Pillow can't draw is reported, never raised
            problems.append(f"page {page}: couldn't draw it ({exc})")
            break
        started = time.monotonic()
        try:
            text = transcribe(settings, png, model=named, should_stop=should_stop)
        except Blank:
            text = ""  # nothing on the page (the back of a sheet): a reading like any other
        except Stopped:
            reading.stopped = True
            break
        except CutOff as exc:  # this page's reading isn't kept; the next pages may read well
            log.warning("Page reader %s: page %d of %d not read (%s)", named, page, total, exc)
            problems.append(f"page {page}: {exc}")
            reading.pages.append("")
            reading.seconds.append(round(time.monotonic() - started, 1))
            continue
        except PageReaderError as exc:  # the server failed: the next pages would too
            log.warning("Page reader %s: page %d of %d failed (%s)", named, page, total, exc)
            problems.append(f"page {page}: {exc}")
            break
        took = time.monotonic() - started
        _cache_put(digest, page, named, text, took)
        log.info("Page reader %s read page %d of %d in %.0f s (%d characters)", named, page, total, took, len(text))
        reading.pages.append(text)
        reading.seconds.append(round(took, 1))
    reading.error = "; ".join(problems)
    return reading


# --- Testing it ---------------------------------------------------------------------------------------------------

TEST_SAMPLE = paths.PROJECT_DIR / "samples" / "northwind_ON_HST_NW-2026-0912.pdf"
TEST_TRUTH = paths.PROJECT_DIR / "samples" / "ground_truth" / "northwind_ON_HST_NW-2026-0912.json"
TEST_SEED = 7
# Compared with the ground truth when it has them. Tax: the total tax read, else the tax amounts read added up.
TEST_FIELDS = ("vendor_name", "invoice_number", "invoice_date", "due_date", "po_number", "gst_hst_registration_number",
               "qst_registration_number", "subtotal", "tax_total", "grand_total")  # fmt: skip
_TAX_FIELDS = ("gst_amount", "hst_amount", "pst_amount", "qst_amount")
PASS_SHARE = 0.8  # with no amount wrong; every field right passes too


@dataclass
class ReaderTest:
    """How the page reader read the test invoice."""

    model: str
    ok: bool
    seconds: float
    rows: list[dict[str, Any]]  # field, label, expected, read, match
    fields_right: int
    fields_total: int
    when: str
    problem: str = ""
    pages: list[str] = field(default_factory=list)  # its transcription of the test invoice (a sample, not a customer's)


def test_pages(reader: Reader = OVIS) -> list[bytes]:
    """The test invoice's pages as the page reader sees them: samples/northwind_ON_HST_NW-2026-0912.pdf printed and
    scanned by the capture benchmark's scanner (170 DPI, gray, tilted, blurred, noisy, JPEG; the same every time,
    ``random.Random(7)``), each page rendered at the reader's resolution. Both pages: the totals are on the second."""
    from .bench.generator import scan

    scanned, _settings = scan(TEST_SAMPLE.read_bytes(), random.Random(TEST_SEED), as_png=False)
    pages = _render(scanned, True, reader, range(1, 11))
    return [pages[number] for number in sorted(pages)]


test_pages.__test__ = False  # type: ignore[attr-defined]  # not a pytest test


def _first(readings: dict[str, list[Any]], field_name: str) -> Any:
    found = readings.get(field_name) or []
    return found[0].value if found else None


def _compare(truth: dict[str, Any], readings: dict[str, list[Any]]) -> list[dict[str, Any]]:
    """One row per field the ground truth has: what it says, what the page reader's text gave, and whether they are
    the same value (``capture.normalize``: amounts to the cent, dates as dates, ids without spaces and dashes)."""
    from .capture.confidence import LABELS
    from .capture.normalize import amounts_equal, normalize_value

    rows = []
    for name in TEST_FIELDS:
        expected = truth.get(name)
        if expected in (None, ""):
            continue
        read = _first(readings, name)
        if name == "tax_total" and read is None:
            parts = [normalize_value(name, _first(readings, tax)) for tax in _TAX_FIELDS]
            parts = [part for part in parts if isinstance(part, float)]
            read = round(sum(parts), 2) if parts else None
        want, got = normalize_value(name, expected), normalize_value(name, read)
        if isinstance(want, float) and isinstance(got, float):
            match = amounts_equal(want, got)
        else:
            match = want is not None and want == got
        rows.append({"field": name, "label": LABELS.get(name, name), "expected": expected, "read": read,
                     "match": bool(match)})  # fmt: skip
    return rows


def _verdict(rows: list[dict[str, Any]]) -> tuple[bool, str]:
    from .capture.types import AMOUNT_FIELDS

    if not rows:
        return False, "The test invoice's ground truth has no fields to compare."
    wrong = [row for row in rows if not row["match"]]
    if not wrong:
        return True, ""
    right, total = len(rows) - len(wrong), len(rows)
    names = ", ".join(row["label"] for row in wrong)
    amounts = ", ".join(row["label"] for row in wrong if row["field"] in AMOUNT_FIELDS)
    if amounts:
        return False, f"{right} of {total} fields right, but an amount was read wrong or not found ({amounts})."
    if right >= PASS_SHARE * total:
        verb = "was" if len(wrong) == 1 else "were"
        return True, f"{right} of {total} fields right; {names} {verb} read wrong or not found."
    return False, f"Only {right} of {total} fields right ({names} read wrong or not found)."


def test_reader(settings: Settings, *, model: str | None = None,
                on_page: Callable[[int, int], None] | None = None) -> ReaderTest:  # fmt: skip
    """Read the test invoice (``test_pages``) with the page reader, read its transcription for fields
    (``capture.transcript.transcript_fields``) and compare them with the sample's ground truth: supplier, invoice
    number, dates, PO, GST number, subtotal, tax, total. ``ok``: every field right, or at least 80% with no amount
    wrong (``problem`` names the rest). The pages are read afresh each time and nothing is kept: the caller keeps
    the result. ``on_page(n, total)`` as each page starts. Never raises for a server problem."""
    when = _now()
    model = (model or "").strip()
    if not model:
        status = reader_status(settings)
        if not status.usable:
            return ReaderTest("", False, 0.0, [], 0, 0, when, status.note or "No model can read pages now.")
        model = status.model
    try:
        truth = json.loads(TEST_TRUTH.read_text(encoding="utf-8"))
        pngs = test_pages(reader_for(model))
    except (OSError, ValueError) as exc:
        return ReaderTest(model, False, 0.0, [], 0, 0, when, f"The test invoice isn't in this AP Coder ({exc}).")
    problem = load_reader(settings, model)
    if problem and not _loaded(settings, model):
        return ReaderTest(model, False, 0.0, [], 0, 0, when, problem)
    texts: list[str] = []
    started = time.monotonic()
    for number, png in enumerate(pngs, start=1):
        if on_page:
            on_page(number, len(pngs))
        try:
            texts.append(transcribe(settings, png, model=model))
        except Blank:
            texts.append("")
        except PageReaderError as exc:
            seconds = round(time.monotonic() - started, 1)
            return ReaderTest(model, False, seconds, [], 0, 0, when, f"Page {number} of the test invoice: {exc}.",
                              pages=texts)  # fmt: skip
    seconds = round(time.monotonic() - started, 1)
    log.info("Page reader test: %s read %d pages in %.0f s", model, len(texts), seconds)
    try:
        from .capture.transcript import transcript_fields

        rows = _compare(truth, transcript_fields(texts))
    except Exception as exc:  # noqa: BLE001 - a reader bug must not lose the timing
        log.exception("Page reader test: the transcription couldn't be read for fields")
        problem = f"The transcription couldn't be read for fields ({exc})."
        return ReaderTest(model, False, seconds, [], 0, 0, when, problem, pages=texts)
    ok, verdict = _verdict(rows)
    right = sum(1 for row in rows if row["match"])
    return ReaderTest(model, ok, seconds, rows, right, len(rows), when, verdict, pages=texts)


test_reader.__test__ = False  # type: ignore[attr-defined]  # not a pytest test


# --- Which model does which job ---------------------------------------------------------------------------------------


def _coding_row(settings: Settings) -> dict[str, str]:
    row = {"role": "Suggests GL accounts", "model": "", "state": "off", "status": "", "note": ""}
    provider = local_llm.resolve_provider(settings)
    if provider == "azure":
        ready = local_llm.provider_status(settings)
        row.update(model=settings.openai.deployment, state="on" if ready.ready else "off", status=ready.detail)
        return row
    if provider == "local":
        status = local_llm.check_server(settings.llm)
        if status.active:
            status_line = f"Running in {status.server}: suggests accounts for lines AP hasn't coded before."
            row.update(model=status.model, state="on", status=status_line)
            if document_reader(status.model):
                row["note"] = (
                    f"{reader_name(status.model)} is made to read pages, not to suggest accounts: load a chat model "
                    "in LM Studio beside it (Qwen 3.5 9B, say)."
                )
            return row
        row.update(state="fallback", status=f"{status.describe()} Accounts come from what AP approved before.")
        return row
    row["state"] = "fallback"
    if settings.llm.provider == "off":
        row["status"] = "AI coding is turned off: accounts come from what AP approved before."
    else:
        row["status"] = "No AI model: accounts come from what AP approved before."
        row["note"] = "Start LM Studio's server with a chat model (Qwen 3.5 9B) for suggestions on new vendors."
    return row


def _reader_row(settings: Settings) -> dict[str, str]:
    row = {"role": "Reads pages (page reader)", "model": "", "state": "off", "status": "", "note": ""}
    if settings.page_reader.mode == "off":
        row["status"] = "Turned off: scans and photos are read by OCR alone."
        return row
    status = reader_status(settings)
    if not status.usable:
        row.update(model=status.model, status="Scans and photos are read by OCR alone.", note=status.note)
        return row
    row["model"] = reader_name(status.model)
    row["state"] = "on" if status.document_reader else "fallback"
    parts = []
    if status.lm_studio:
        parts.append("Loaded." if status.state == "loaded" else "Downloaded: LM Studio loads it when a page is read.")
    if settings.page_reader.mode == "ask":
        parts.append("Reads pages when AP asks.")
    else:
        what = "every invoice" if settings.page_reader.scope == "all" else "each scan and photo"
        parts.append(f"Reads {what} in the background.")
    seconds = page_seconds_estimate(settings, model=status.model)
    if seconds is not None:
        parts.append(f"{duration(seconds).capitalize()} a page on this computer.")
    row["status"] = " ".join(parts)
    row["note"] = status.note
    if not status.document_reader and not row["note"]:
        row["note"] = "OvisOCR2, made for reading document pages, reads them more accurately: " + NOT_DOWNLOADED
    return row


def _ocr_row() -> dict[str, str]:
    from .capture import layout

    row = {"role": "OCR for scans", "model": "", "state": "off", "status": "", "note": ""}
    if not layout.ocr_available():
        row["status"] = "Not installed: scans and photos can't be read on this computer (text PDFs can)."
        row["note"] = 'Run pip install -e ".[ocr]".'
        return row
    names = {"rapidocr": "PP-OCRv4", "ppocrv5": "PP-OCRv5"}
    first, second = layout._engine_name(), layout.second_engine_name()
    row["state"] = "on"
    if first != second:
        row["model"] = f"{names.get(first, first)} + {names.get(second, second)}"
        row["status"] = "On this computer: each scan is read by two OCR models, in seconds."
    else:
        row["model"] = names.get(first, first)
        row["status"] = "On this computer: each scan is read twice by one OCR model (the second time straightened)."
        row["note"] = "A second OCR model reads independently: run python scripts/fetch_models.py once while online."
    return row


# --- What LM Studio has, and getting OvisOCR2 ---------------------------------------------------------------------

OVIS_DOWNLOAD = ("https://huggingface.co/bartowski/ATH-MaaS_OvisOCR2-GGUF", "Q8_0")  # (Hugging Face link, quantization)
DOWNLOAD_TIMEOUT = 30.0  # seconds: LM Studio answers at once and downloads in the background


def lm_studio_models(settings: Settings, *, use_cache: bool = True) -> list[dict[str, Any]] | None:
    """Every chat and vision model LM Studio has downloaded, for Settings: ``model``, ``loaded`` (with ``context``,
    the tokens it is loaded with; 0 when LM Studio doesn't say), ``vision``, ``document_reader`` and ``used_for``
    (what AP Coder uses it for, "" when nothing). Loaded ones first. None when LM Studio isn't answering (or the
    server isn't LM Studio, which doesn't say what it has)."""
    listing = lm_studio_listing(settings, use_cache=use_cache)
    if listing is None:
        return None
    chat = local_llm.check_server(settings.llm, use_cache=use_cache)
    chat_model = chat.model if chat.active and local_llm.resolve_provider(settings) == "local" else ""
    reader = reader_status(settings, use_cache=use_cache).model if settings.page_reader.mode != "off" else ""
    rows = []
    for model in listing.models:
        jobs = []
        if chat_model and model.named(chat_model):
            jobs.append("suggests GL accounts")
        if reader and model.named(reader):
            jobs.append("reads pages")
        rows.append({
            "model": model.key, "loaded": bool(model.instances), "context": model.loaded_context,
            "vision": model.vision, "document_reader": document_reader(model.key), "used_for": ", ".join(jobs),
        })  # fmt: skip
    return sorted(rows, key=lambda r: (not r["loaded"], not r["used_for"], r["model"]))


CHAT_CONTEXT = 8192  # tokens: the accounts call is short (README: LM Studio settings)


def load_model(settings: Settings, model: str) -> str:
    """Have LM Studio (0.4 or newer) load ``model`` from Settings: a page reader with the context a page needs, any
    other model with the chat context AP Coder uses. Returns "" or what went wrong (LM Studio's reason)."""
    if document_reader(model) or reader_for(model).context:
        return load_reader(settings, model)
    listing = lm_studio_listing(settings, use_cache=False)
    if listing is None:
        return "LM Studio isn't answering: start it, and its server (Developer tab, Start server)."
    found = listing.find(model)
    if found is None:
        return f"LM Studio doesn't have {model} downloaded."
    if found.instances:
        return ""
    if listing.route != "v1":
        return f"Load {model} in LM Studio (this LM Studio can't load a model when asked)."
    root = local_llm._root(reader_base_url(settings))
    try:
        _post_json(root + "/api/v1/models/load", {"model": found.key, "context_length": CHAT_CONTEXT},
                   settings.llm.api_key, LOAD_TIMEOUT)  # fmt: skip
    except PageReaderError as exc:
        return f"LM Studio couldn't load {model} ({exc})."
    finally:
        forget_status()
    return ""


def download_reader(settings: Settings) -> tuple[str, str]:
    """Have LM Studio (0.4 or newer) download OvisOCR2 (bartowski build, Q8_0, about 1 GB). Returns (job id, problem):
    ("", "") when it is already downloaded, (job id, "") while it downloads, ("", why) when it can't."""
    listing = lm_studio_listing(settings, use_cache=False)
    if listing is None:
        return "", "LM Studio isn't answering: start it, and its server (Developer tab, Start server)."
    if any(document_reader(m.key) for m in listing.models):
        return "", ""
    if listing.route != "v1":
        return "", "This LM Studio can't download when asked: search OvisOCR2 in LM Studio and download it there."
    link, quantization = OVIS_DOWNLOAD
    root = local_llm._root(reader_base_url(settings))
    try:
        answer = _post_json(root + "/api/v1/models/download", {"model": link, "quantization": quantization},
                            settings.llm.api_key, DOWNLOAD_TIMEOUT)  # fmt: skip
    except PageReaderError as exc:
        return "", f"LM Studio couldn't start the download ({exc}): search OvisOCR2 in LM Studio and download it there."
    finally:
        forget_status()
    answer = answer if isinstance(answer, dict) else {}
    if answer.get("status") in ("already_downloaded", "completed"):
        return "", ""
    if answer.get("status") == "failed" or not answer.get("job_id"):
        return "", "LM Studio couldn't download OvisOCR2: search OvisOCR2 in LM Studio and download it there."
    return str(answer["job_id"]), ""


def download_progress(settings: Settings, job_id: str) -> dict[str, Any]:
    """How a download LM Studio is doing is going: ``status`` (downloading, paused, completed, failed, or unknown when
    LM Studio doesn't answer), ``done`` and ``total`` bytes, ``seconds_left`` (None when not said)."""
    root = local_llm._root(reader_base_url(settings))
    try:
        data = local_llm._fetch_json(f"{root}/api/v1/models/download/status/{job_id}", settings.llm.api_key,
                                     LISTING_TIMEOUT)  # fmt: skip
    except (OSError, ValueError):
        data = None
    data = data if isinstance(data, dict) else {}
    seconds_left = None
    rate, done, total = (
        data.get("bytes_per_second"),
        _int(data.get("downloaded_bytes")),
        _int(data.get("total_size_bytes")),
    )
    if isinstance(rate, (int, float)) and rate > 0 and total > done:
        seconds_left = (total - done) / rate
    if data.get("status") == "completed":
        forget_status()
    return {"status": str(data.get("status") or "unknown"), "done": done, "total": total, "seconds_left": seconds_left}


def models_in_use(settings: Settings) -> list[dict[str, str]]:
    """One row per job, for Settings: ``role``, ``model`` ("" when none), ``state`` ("on"; "fallback" when something
    less good does the job; "off"), ``status`` (what happens now) and ``note`` (why, or what to do)."""
    return [_coding_row(settings), _reader_row(settings), _ocr_row()]
