"""How AP Coder reads every invoice, the same way each time, and how each reader is doing right now.

There is one way to read an invoice, with nothing to choose:

1. the PDF's own text layer where the page has one, local OCR (two engines) where it has not (scans, photos);
2. the page reader, OvisOCR2 in LM Studio, reads every page of every invoice (digital PDFs too) as an independent
   second reader, found in LM Studio on its own and checked by its own self-test before it is trusted;
3. the rule reader, the supplier's learned template and the business checks (totals, tax, GST/HST number, vendor
   master, purchase order, duplicates, bank account);
4. no invoice is approved without a person before every reader has read it.

``reading_status`` is what the Settings page, the doctor and the banner on every page show.
"""

from __future__ import annotations

import importlib.util
import logging
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

OK, WARN, OFF = "ok", "warn", "off"

PDF_TEXT, OCR, PAGE_READER, RULES, TEMPLATES, CHECKS, GL_MODEL = (
    "PDF text layer", "OCR (two engines)", "Page reader (OvisOCR2)", "Rule reader", "Supplier templates",
    "Business checks", "GL coding model",
)  # fmt: skip


CHECKS_DETAIL = ("Totals add up, tax at the official rate, GST/HST and QST numbers, vendor master, purchase order, "
                 "duplicates and bank account, on every invoice.")  # fmt: skip


@dataclass
class ReaderLine:
    """One row of "How AP Coder reads every invoice"."""

    name: str  # "PDF text layer", "OCR (two engines)", "Page reader (OvisOCR2)", "Rule reader", ...
    state: str  # OK | WARN | OFF
    detail: str  # plain English: what it does, or what is wrong and what to do


@dataclass
class ReadingStatus:
    readers: list[ReaderLine] = field(default_factory=list)
    page_reader_model: str = ""  # LM Studio key of OvisOCR2; "" when LM Studio has none
    page_reader_ready: bool = False  # found, LM Studio answers, and its self-test passed
    self_test: str = "no model"  # "passed" | "failed" | "running" | "pending" | "no model"
    self_test_detail: str = ""  # plain English: when it passed, what failed, how far along it is
    queue: int = 0  # invoices waiting for the page reader
    eta_minutes: float | None = None  # until the queue is read, when there is a measured page time
    figures_agree: float | None = None  # share of figures it read as the PDF's text layer has them (digital PDFs)
    figures_count: int = 0  # figures behind ``figures_agree``
    banner: str = ""  # a one-line warning for every page ("" when all is well)
    lm_studio: bool = False  # LM Studio (or another local model server) answers
    gl_model: str = ""  # the chat model that suggests GL accounts ("" when none answers)

    def reader(self, name: str) -> ReaderLine | None:
        """The row named ``name`` (one of the names above), or None."""
        return next((line for line in self.readers if line.name == name), None)


def reading_status(settings: Any, store: Any, *, use_cache: bool = True) -> ReadingStatus:
    """Each reader's state now. Never raises: a reader that cannot be asked is shown as such. Quick: LM Studio is
    asked through the same short-lived cache as the rest of AP Coder (``use_cache=False`` asks it afresh)."""
    out = ReadingStatus()
    reader_line = _page_reader(settings, store, use_cache, out)
    out.readers = [
        ReaderLine(PDF_TEXT, OK, "Digital PDFs: the PDF's own text, exact, with each word's place on the page."),
        _safe(OCR, _ocr),
        reader_line,
        ReaderLine(RULES, OK, "Reads every field from labels, patterns and positions, in English and French."),
        _safe(TEMPLATES, lambda: _templates(store)),
        ReaderLine(CHECKS, OK, CHECKS_DETAIL),
        _safe(GL_MODEL, lambda: _gl_model(settings, use_cache, out)),
    ]
    _queue(settings, store, out)
    out.banner = _banner(out)
    return out


def _safe(name: str, make: Any) -> ReaderLine:
    try:
        return make()
    except Exception as exc:  # noqa: BLE001 - a status page never fails
        log.debug("reading status: %s not checked (%s)", name, exc)
        return ReaderLine(name, WARN, f"Not checked ({type(exc).__name__}).")


def _ocr() -> ReaderLine:
    """The two local OCR engines (PP-OCRv4 and PP-OCRv5), found without loading them."""
    from . import offline

    v4 = offline.ppocrv4_installed()
    v5 = offline.ppocrv5_installed() and not offline.missing_models()
    photos = "" if importlib.util.find_spec("pillow_heif") else " iPhone photos (HEIC) need the photo add-on too."
    if v4 and v5:
        return ReaderLine(OCR, WARN if photos else OK, "Scans and photos: two OCR models read each page on this "
                                                       f"computer, in seconds.{photos}")  # fmt: skip
    if v4 or v5:
        return ReaderLine(OCR, WARN, "Scans and photos: one OCR model only (the second comes with the offline "
                                     f"bundle's models folder: run APProcessor.bat).{photos}")  # fmt: skip
    return ReaderLine(OCR, OFF, "Not installed: scans and photos can't be read on this computer (digital PDFs can). "
                                "Run APProcessor.bat: it installs OCR from the offline bundle.")  # fmt: skip


def _templates(store: Any) -> ReaderLine:
    count = int(store.supplier_templates_count()) if store is not None else 0
    if not count:
        return ReaderLine(TEMPLATES, OK, "Learned from each supplier's approved invoices: none yet (learned once "
                                         "AP has approved a few of a supplier's invoices).")  # fmt: skip
    noun = "supplier has" if count == 1 else "suppliers have"
    return ReaderLine(TEMPLATES, OK, f"{count:,} {noun} a template learned from approved invoices: where each field "
                                     "is printed on their invoices.")  # fmt: skip


def _page_reader(settings: Any, store: Any, use_cache: bool, out: ReadingStatus) -> ReaderLine:
    """OvisOCR2: found in LM Studio, and its self-test. Fills the page reader's fields of ``out``."""
    try:
        from .page_reader import reader_status
        from .page_worker import self_test

        status = reader_status(settings, use_cache=use_cache)
        out.lm_studio = bool(status.reachable)
        if not status.usable:
            out.self_test, out.self_test_detail = "no model", status.note or "OvisOCR2 isn't running in LM Studio."
            return ReaderLine(PAGE_READER, OFF, f"{out.self_test_detail} Invoices are read by OCR alone and wait for a "
                                                "person.")  # fmt: skip
        out.page_reader_model = status.model
        out.self_test, out.self_test_detail = self_test(store, status.model) if store is not None else (
            "pending", "Not checked: no database.")  # fmt: skip
    except Exception as exc:  # noqa: BLE001 - a status page never fails
        log.debug("reading status: page reader not checked (%s)", exc)
        out.self_test, out.self_test_detail = "pending", f"Not checked ({type(exc).__name__})."
        return ReaderLine(PAGE_READER, WARN, out.self_test_detail)
    out.page_reader_ready = out.self_test == "passed"
    if out.page_reader_ready:
        return ReaderLine(PAGE_READER, OK, f"Reads every page of every invoice as a second reader, in the background. "
                                           f"{out.self_test_detail}")  # fmt: skip
    return ReaderLine(PAGE_READER, WARN, f"{out.self_test_detail} Until it passes, invoices wait for a person.")


def _gl_model(settings: Any, use_cache: bool, out: ReadingStatus) -> ReaderLine:
    """The LM Studio chat model that suggests accounts for lines AP's history doesn't cover (optional)."""
    from .local_llm import check_server, resolve_provider

    provider = resolve_provider(settings)
    if provider == "azure":
        out.gl_model = settings.openai.deployment
        return ReaderLine(GL_MODEL, OK, f"Azure OpenAI ({settings.openai.deployment}): the internet is allowed on "
                                        "this computer (AP_ALLOW_INTERNET).")  # fmt: skip
    if provider == "off":
        return ReaderLine(GL_MODEL, OFF, "Not asked: GL accounts come from AP's history and coding rules.")
    status = check_server(settings.llm, use_cache=use_cache)
    out.lm_studio = out.lm_studio or status.reachable
    if status.active:
        out.gl_model = status.model
        return ReaderLine(GL_MODEL, OK, f"{status.model} in {status.server}: suggests accounts for lines AP's history "
                                        "and rules don't cover.")  # fmt: skip
    return ReaderLine(GL_MODEL, OFF, "Optional, not running: GL accounts come from AP's history and coding rules; "
                                     "a line nothing was learned for is left for AP to code.")  # fmt: skip


def _queue(settings: Any, store: Any, out: ReadingStatus) -> None:
    if store is None:
        return
    try:
        from .page_worker import figure_totals, wait_seconds

        out.queue = int(store.page_reads_waiting())
        seconds = wait_seconds(settings, store, out.page_reader_model) if out.page_reader_model else None
        out.eta_minutes = round(seconds / 60, 1) if seconds is not None else None
        same, seen = figure_totals(store).get("digital", (0, 0))
        out.figures_count = int(seen)
        out.figures_agree = round(same / seen, 4) if seen else None
    except Exception as exc:  # noqa: BLE001 - a status page never fails
        log.debug("reading status: queue not checked (%s)", exc)


def _banner(out: ReadingStatus) -> str:
    """One line for the top of every page while the page reader isn't ready; "" when it is."""
    if out.page_reader_ready:
        return ""
    if out.self_test == "no model":
        if not out.lm_studio:
            return "LM Studio isn't running: invoices are read by OCR only and wait for a person."
        return "OvisOCR2 isn't running in LM Studio: invoices are read by OCR only and wait for a person."
    if out.self_test in ("pending", "running"):
        return "OvisOCR2 is testing itself before it reads invoices: invoices wait for a person meanwhile."
    return "OvisOCR2 failed its self-test: invoices are read by OCR only and wait for a person (Settings → Reading)."
