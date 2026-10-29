"""One invoice from its file to the review queue: read, code, check, decide, save.

* ``InvoicePipeline.process``: a PDF, picture or text file read on this computer (the PDF's text layer or local OCR,
  read by capture with the supplier's template), its lines coded from AP's approvals (``offline_coder``) and the chat
  model for those left over, the checks, the touchless decision, then saved to the review queue and put in line for
  the page reader and the AI reviewer. ``process_many`` runs a batch.
* ``finalise_coding``: the checks and the output (target schema, GL distribution) of a coding, again each time a
  reviewer edits the invoice.
* ``invoice_files``, ``discover_inputs``, ``other_files``: which files in a folder are invoices; ``write_outputs``:
  the command line's JSON files; ``plain_error``: what AP reads when a file can't be read.
"""

from __future__ import annotations

import contextlib
import contextvars
import datetime as dt
import json
import logging
import re
import time
from collections.abc import Callable, Collection
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..capture.bridge import review_issues
from ..capture.layout import CannotRead
from ..capture.workflow import AUTONOMOUS_REVIEWER, autonomy_decision, capture_invoice
from ..coding import banks
from ..coding.po import po_findings
from ..coding.rules import apply as apply_rules
from ..coding.tax import PLACE_OF_SUPPLY, SUPPLY_OURS, SUPPLY_SITE, build_gl_distribution, supply_source
from ..coding.terms import payment_findings
from ..coding.validation import WARNING, Issue, ValidationReport, validate_coding
from ..coding.vendors import vendor_findings
from ..core.config import Settings
from ..core.memory import compare_with_history, format_examples, select_examples, vendor_key
from ..core.reference_data import ReferenceData
from ..core.schema import InvoiceCoding
from . import azure_check, verifier
from .extraction import SUPPORTED_EXTENSIONS, TEXT_EXTENSIONS, DocumentExtractor, ExtractionResult
from .imaging import render_page_images
from .inference import CHAT_SUGGESTED, MODEL_SUGGESTED, CodingError, CodingResult, InvoiceCoder

if TYPE_CHECKING:
    from ..core.store import Store

log = logging.getLogger(__name__)


@dataclass
class PipelineResult:
    source: Path
    output: dict[str, Any] | None = None
    report: ValidationReport | None = None
    extraction: ExtractionResult | None = None
    coding: CodingResult | None = None
    error: str | None = None
    timings: dict[str, float] = field(default_factory=dict)
    history_examples: int = 0
    invoice_id: int | None = None  # row id when saved to the dashboard store
    rules_applied: list[dict[str, Any]] = field(default_factory=list)  # lines a fixed coding rule changed
    capture: Any = None  # capture.CaptureResult: every header field located, with its confidence
    autonomy: dict[str, Any] = field(default_factory=dict)  # supplier state and whether it went touchless
    original: dict[str, Any] | None = None  # the picture this PDF was made from (intake/pictures): name, kept, sha256

    @property
    def ok(self) -> bool:
        return self.error is None and self.output is not None

    def summary(self) -> dict[str, Any]:
        row: dict[str, Any] = {"file": self.source.name, "status": "ok" if self.ok else "failed"}
        if self.error:
            row["error"] = self.error
        if self.report:
            row.update(
                model_confidence=self.report.model_confidence,
                adjusted_confidence=self.report.adjusted_confidence,
                reading_confidence=self.report.reading_confidence,
                coding_confidence=self.report.coding_confidence,
                requires_review=self.report.requires_review,
                errors=len(self.report.errors),
                warnings=len(self.report.warnings),
            )
        if self.output:
            row["line_items"] = len(self.output["line_items"])
            row["grand_total"] = self.output["grand_total"]
            row["currency"] = self.output["currency"]
        row["seconds"] = round(sum(self.timings.values()), 2)
        return row


def invoice_files(folder: Path) -> list[Path]:
    """Supported invoice files in a folder (sorted, non-recursive).

    A .md/.txt file is skipped when a PDF or image with the same name sits next to it
    (it is a text copy of the same invoice, e.g. the bundled samples), so nothing is coded twice.
    README files are documentation, never invoices; nor are hidden files (a Mac's "._inv.pdf" beside "inv.pdf" on a
    shared or USB drive) or an Office lock file ("~$...").
    """
    files = sorted(
        c
        for c in folder.iterdir()
        if c.is_file()
        and c.suffix.lower() in SUPPORTED_EXTENSIONS
        and not c.name.lower().startswith(("readme", ".", "~$"))
    )
    documents = {c.stem.lower() for c in files if c.suffix.lower() not in TEXT_EXTENSIONS}
    return [c for c in files if c.suffix.lower() not in TEXT_EXTENSIONS or c.stem.lower() not in documents]


NOT_A_FILE_TO_MENTION = {"desktop.ini", "thumbs.db"}  # Windows' own, in any folder
STILL_ARRIVING = {".part", ".partial", ".crdownload", ".download", ".tmp"}  # a copy or a download not finished


def other_files(folder: Path, also: Collection[str] = ()) -> list[Path]:
    """The files in ``folder`` (not its subfolders) that are not a kind AP Coder reads, nor one of the extensions
    ``also`` (saved emails): said to the person, never passed over in silence. Hidden files, README files, Office
    lock files, Windows' own files and downloads still arriving are not mentioned."""
    return sorted(
        c
        for c in folder.iterdir()
        if c.is_file()
        and c.suffix.lower() not in SUPPORTED_EXTENSIONS | set(also) | STILL_ARRIVING
        and c.name.lower() not in NOT_A_FILE_TO_MENTION
        and not c.name.lower().startswith(("readme", ".", "~$"))
    )


def discover_inputs(paths: list[str | Path]) -> list[Path]:
    """Expand directories into supported invoice files (sorted, non-recursive)."""
    found: list[Path] = []
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            found += invoice_files(p)
        elif p.is_file():
            found.append(p)
        else:
            raise FileNotFoundError(p)
    return found


def finalise_coding(
    coding: InvoiceCoding,
    reference: ReferenceData,
    settings: Settings,
    extraction: ExtractionResult | None = None,
    store: Store | None = None,
    exclude_invoice_id: int | None = None,
    feedback: list[dict[str, Any]] | None = None,
    received: dt.date | None = None,
) -> tuple[dict[str, Any], ValidationReport]:
    """Validate a coding and build the output (target schema + GL distribution).

    Used after inference and again by the dashboard whenever a reviewer edits an invoice. ``received``: the day the
    invoice was read (an invoice in the queue: its import day), default today.
    """
    # the vendor's key: of two vendors whose names read alike, the one with this GST/HST number (B-1021)
    key = store.vendor_key_for(coding.vendor_name, coding.gst_hst_registration_number) if store is not None else None
    if feedback is None and store is not None:
        feedback = store.feedback_rows(vendor_name=coding.vendor_name, key=key)  # history check: this vendor only
    history = compare_with_history(coding, feedback, key) if feedback else None
    duplicates = (
        store.find_duplicates(
            coding.vendor_name,
            coding.invoice_number,
            exclude_id=exclude_invoice_id,
            grand_total=coding.grand_total,
            key=key,
        )
        if store is not None
        else None
    )
    # The bank account it is paid from (ours, D-66): the vendor's last one, else the currency's default, when none is
    # chosen yet; checked once our accounts are set up.
    bank_findings: list[tuple[str, str, str]] = []
    if store is not None:
        accounts = store.bank_accounts()
        if accounts and not coding.pay_from_account:
            coding.pay_from_account = banks.choose(
                accounts, coding.currency, store.last_pay_from(vendor_key(coding.vendor_name))
            )
        bank_findings = banks.findings(coding.pay_from_account, coding.currency, accounts)
    output = coding.to_output()
    if not coding.ship_to_province:  # no ship-to printed: where it is delivered to us (tax.supply_source)
        place, source = supply_source(coding, reference.tax)
        # Stored beside the printed ship-to, never in it, so that recoding the lines moves it (worked out again
        # each time); never the vendor's province: it stays a guess.
        if source in (SUPPLY_SITE, SUPPLY_OURS):
            output[PLACE_OF_SUPPLY] = place
    output["gl_distribution"] = build_gl_distribution(coding, reference.tax)
    # JD Edwards in use: what its batch would hold back, asked before approval (D-75).
    erp_findings: list[tuple[str, str, str, int | None]] = []
    if store is not None:
        from ..erp import jde

        if jde.in_use(store):
            js = jde.load(store)
            erp_findings = jde.voucher_findings(output, js, jde.resolve_an8(output, store.vendor_ids(), js))
    report = validate_coding(
        coding,
        reference,
        extraction,
        review_threshold=settings.engine.review_threshold,
        history=history,
        duplicate_of=duplicates,
        vendor_findings=(vendor_findings(coding, store, exclude_invoice_id) if store is not None else [])
        + bank_findings
        + payment_findings(
            coding.to_output(),
            store.default_terms_days() if store is not None else 30,
            vendor_terms=store.vendor_terms(vendor_key(coding.vendor_name)) if store is not None else "",
            received=received,
        ),
        po_findings=po_findings(coding, store, exclude_invoice_id) if store is not None else None,
        erp_findings=erp_findings,
    )
    return output, report


def _mark_chat_accounts(coding: InvoiceCoding) -> None:
    """Each line's account the chat model chose, coding the whole invoice (Azure OpenAI), marked a guess
    (``CHAT_SUGGESTED``), as the local model's are: its ``confidence_score`` is about the header (D-89), so without
    the mark the Coding took every known account as sure (B-894)."""
    from ..core.reference_data import UNASSIGNED
    from .offline_coder import unsure

    for li in coding.line_items:
        if li.predicted_gl_code in ("", UNASSIGNED) or unsure(li.reasoning_justification):
            continue
        reason = str(li.reasoning_justification or "").strip()
        li.reasoning_justification = f"{CHAT_SUGGESTED}: {reason}" if reason else CHAT_SUGGESTED


def _model_line_taxes(coding: InvoiceCoding, text: str, feedback: list[dict[str, Any]] | None = None) -> None:
    """The chat model's lines with their taxes worked out when it said nothing of them (no line has a tax or a
    status) and a tax is charged: the codes printed on the page are not there to read, so the lines that make each
    tax add up (``line_tax.prefill``, D-64). What the model said is kept."""
    from ..coding.line_tax import charged_types, fill_exempt, model_gave_no_taxes
    from .offline_coder import tax_codes

    charged = charged_types(coding.tax_lines)
    if (
        coding.line_items
        and not model_gave_no_taxes(coding.line_items)
        and not any(li.tax_exempt or li.tax_status for li in coding.line_items)
    ):  # its taxes, and nothing of what is exempt: read as the vendor meant it (a tax charged on others is exempt)
        coding.line_items = fill_exempt([li.model_copy(update={"tax_exempt": None}) for li in coding.line_items],
                                        coding.tax_lines)  # fmt: skip
    if not coding.line_items or not model_gave_no_taxes(coding.line_items) or not charged:
        return
    province = coding.ship_to_province or coding.supplier_province
    coding.line_items = tax_codes(list(coding.line_items), list(coding.tax_lines), [], text, province,
                                  coding.invoice_date, supplier_province=coding.supplier_province,
                                  pst_number=coding.pst_registration_number, feedback=feedback,
                                  vendor=coding.vendor_name, qst_number=coding.qst_registration_number)  # fmt: skip


DAMAGED_PICTURE = ("UnidentifiedImageError",)
DAMAGED_PDF = ("FileDataError", "EmptyFileError")


def plain_error(exc: Exception, path: Path) -> str:
    """What AP reads when an invoice can't be processed: a damaged picture or PDF said plainly with what to do (the
    technical detail is in the log), anything else as the error it is."""
    name = type(exc).__name__
    kind = path.suffix.lstrip(".").upper() or "picture"
    if name == "DecompressionBombError":  # Pillow's guard: over ~179 million pixels (a phone's 200 MP mode)
        pixels = re.search(r"\((\d+) pixels\)", str(exc))
        megapixels = int(pixels.group(1)) / 1e6 if pixels else 0.0
        size = f" ({megapixels:,.{0 if megapixels >= 10 else 1}f} megapixels)" if pixels else ""
        return (f"{path.name} is too large a picture{size} to be opened safely. Take the photo at the phone's normal "
                "resolution (12 megapixels is plenty), or scan the page.")  # fmt: skip
    if name in DAMAGED_PICTURE or (name == "OSError" and "image file" in str(exc)):
        return (f"{path.name} can't be opened as a picture: the file is damaged or not really a {kind}. Ask the "
                "vendor for a new copy, or scan it again.")  # fmt: skip
    if name in DAMAGED_PDF:
        return (f"{path.name} can't be opened as a PDF: the file is damaged or incomplete. Ask the vendor for a new "
                "copy.")  # fmt: skip
    if path.suffix.lower() == ".pdf" and _needs_password(path):  # PyMuPDF says "document closed or encrypted"
        return f"{path.name} is protected by a password, so it can't be read. Ask the vendor for a copy without one."
    return f"{name}: {exc}"


NO_PAGES = "{name} has no pages in it: the file is empty or was not saved completely. Ask the vendor for a new copy."


def _check_pdf(path: Path) -> None:
    """Raises ``CannotRead`` for a PDF with no pages (read, it would go to the queue as a blank invoice). A PDF that
    can't be opened is left to the readers, whose error is worded by ``plain_error``."""
    if path.suffix.lower() != ".pdf":
        return
    try:
        import pymupdf

        with pymupdf.open(path) as doc:
            pages = None if doc.needs_pass else doc.page_count  # a password: said as such (``plain_error``)
    except Exception:  # noqa: BLE001 - damaged, or not a PDF: said by the reader that fails on it
        return
    if pages == 0:
        raise CannotRead(NO_PAGES.format(name=path.name))


CUT_OFF = "FILE_CUT_OFF"
CUT_OFF_TEXT = ("{name} looks cut off (its end is missing: it was still downloading or being saved when it came in), "
                "so part of the invoice may be missing. Compare it with the vendor's copy, or ask the vendor for a new "
                "one.")  # fmt: skip


def _cut_off(path: Path) -> bool:
    """A PDF whose end is missing (``intake.looks_incomplete``): PyMuPDF reads what is there, maybe part of it."""
    from ..intake.pictures import looks_incomplete

    try:
        return path.suffix.lower() == ".pdf" and looks_incomplete(path)
    except OSError:
        return False


def _needs_password(path: Path) -> bool:
    try:
        import pymupdf

        with pymupdf.open(path) as doc:
            return bool(doc.needs_pass)
    except Exception:  # noqa: BLE001 - only asked to word an error: a file that can't be opened says so otherwise
        return False


class InvoicePipeline:
    def __init__(
        self,
        settings: Settings,
        reference: ReferenceData,
        extractor: DocumentExtractor | None = None,
        coder: InvoiceCoder | None = None,
        cache_dir: str | Path | None = None,
        store: Store | None = None,
    ) -> None:
        self.settings = settings
        self.reference = reference
        self.extractor = extractor or DocumentExtractor(settings.document_intelligence, cache_dir=cache_dir)
        self.coder = coder or InvoiceCoder(settings, reference)
        self.store = store

    def _local_accounts(self, result: CodingResult, history: str) -> None:
        """The local model's account for each line the approval memory has not taught. Never fatal: a model
        that is slow or wrong leaves the line as it was for AP to code."""
        from ..core.reference_data import UNASSIGNED
        from .inference import suggest_accounts

        coding = result.coding
        # Lines nothing could code, weak matches included (an account's name: never put in a line on its own, B-463).
        # Each is numbered by its place on the invoice, not its printed line number: those can repeat (a second
        # page that numbers from 1 again), and a suggestion must never land on a line already coded. A line the model
        # gives no account stays blank, its weak match shown as a suggestion on the review screen.
        todo = {n: li for n, li in enumerate(coding.line_items, start=1) if li.predicted_gl_code == UNASSIGNED}
        hints = getattr(result, "hints", None) or {}
        if not todo:
            return
        from .lessons import prompt_lines

        learned = prompt_lines(
            getattr(self, "store", None), coding.vendor_name, fields="lines"
        )  # AP's corrections, with its reasons
        if learned:
            history = "\n".join([history, "AP's lessons for this vendor:", *learned]).strip()
        try:
            picks = suggest_accounts(
                self.coder, coding.vendor_name, [(n, li.description, li.amount) for n, li in todo.items()], history
            )
        except CodingError as exc:  # the chat model isn't running (it is optional): the lines stay for AP
            log.info("local model could not suggest accounts (%s); left for AP", exc)
            return
        except Exception as exc:  # the model is an extra here: the invoice is already read and checked
            log.warning("local model could not suggest accounts (%s); left for AP", exc)
            return
        for n, (gl, cc, reason) in picks.items():
            li = todo.get(n)
            if li is None:
                continue
            li.predicted_gl_code = gl
            if cc:
                li.predicted_cost_center = cc
            why = f"{MODEL_SUGGESTED}: {reason}" if reason else MODEL_SUGGESTED
            hint = hints.get(n)
            if hint == gl:
                why += " (the account's name matches the line too)"
            elif hint:
                why += f" (the account's name pointed to {hint} instead)"
            li.reasoning_justification = why
        model = getattr(self.coder, "_local_model", "") or "local model"
        result.model = f"local reader + {model}"

    def _model_reading(self, path: Path) -> dict[str, Any] | None:
        """The chat model's own reading of the header from the page images (``inference.read_header``), when the model
        loaded in LM Studio can see pages (Qwen 3.5 can). Asked once per file and model: kept in the data folder
        (``.cache/model_reading``). Never fatal: without it the invoice is read as before."""
        import hashlib

        from ..core.paths import private_dir
        from .inference import read_header

        try:
            model = self.coder._prepare_local()
            if not self.coder.profile.supports_vision:
                return None
        except Exception as exc:  # the chat model is optional: not answering, the invoice is read without it
            log.info("%s: the chat model can't read the page (%s)", path.name, exc)
            return None
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            return None
        slug = re.sub(r"[^a-z0-9._-]+", "-", model.lower()).strip("-")[:60]
        kept = private_dir() / ".cache" / "model_reading" / f"{digest}-{slug}-v1.json"
        try:
            values = json.loads(kept.read_text(encoding="utf-8"))
            if isinstance(values, dict):
                return values
        except (OSError, ValueError):
            pass
        try:
            values = read_header(self.coder, render_page_images(path, 2))  # the header, and the totals on page 2
        except Exception as exc:  # an extra reader: never fatal
            log.warning("%s: the chat model could not read the header (%s)", path.name, exc)
            return None
        try:
            kept.parent.mkdir(parents=True, exist_ok=True)
            part = kept.with_suffix(".part")
            part.write_text(json.dumps(values), encoding="utf-8")
            part.replace(kept)
        except OSError:
            pass
        return values

    def _reads_locally(self, path: Path) -> bool:
        """Read the file on this computer: no Document Intelligence client handed in, and its endpoint unset or the
        internet not allowed (this offline build). Text files are read as they are either way."""
        from ..ops.offline import internet_allowed

        ex = self.extractor
        return (
            path.suffix.lower() not in TEXT_EXTENSIONS
            and isinstance(ex, DocumentExtractor)
            and ex._client is None
            and (not ex.settings.endpoint or not internet_allowed())
        )

    def _images(self, path: Path) -> list[Any] | None:
        """Page images for a coder that looks at them (Azure OpenAI with AP_VISION, a client handed in): rendered only
        then, never for the local readers, which read the page's text."""
        wants_images = getattr(self.coder, "wants_images", lambda: self.settings.engine.vision)
        if path.suffix.lower() in TEXT_EXTENSIONS or not wants_images():
            return None
        try:
            return render_page_images(path, self.settings.engine.vision_max_pages)
        except Exception as exc:  # vision is an extra; the text extraction is enough to code
            log.warning("%s: could not render page images (%s); coding from text only", path.name, exc)
            return None

    def process(
        self, path: str | Path, page_text: list[str] | None = None, save: bool = True, invoice_id: int | None = None,
        pages_read: int | None = None,
    ) -> PipelineResult:  # fmt: skip
        """``_process``, with each scanned page's OCR kept in the data folder (``.cache/ocr``) for an app pipeline (one
        with a store): an invoice is read on arrival, with its supplier's template, and again once the page reader has
        read it, and its pages are OCR'd once. Its fifty-odd calls on the database share one connection."""
        from ..capture.layout import ocr_cache

        folder = None
        if self.store is not None:
            from ..core.paths import private_dir

            folder = private_dir() / ".cache" / "ocr"
        with ocr_cache(folder), self.store.session() if self.store is not None else contextlib.nullcontext():
            return self._process(path, page_text, save, invoice_id, pages_read)

    def _process(
        self, path: str | Path, page_text: list[str] | None = None, save: bool = True, invoice_id: int | None = None,
        pages_read: int | None = None,
    ) -> PipelineResult:  # fmt: skip
        """Read, code and check one invoice, and save it to the review queue (``save``). ``page_text``: the page
        reader's reading of each page (OvisOCR2), read as one more independent reader. ``invoice_id``: the invoice is
        already in the store as this row (read again by the page reader), so the checks against other invoices
        (duplicate, vendor history, purchase order) leave it out instead of finding it against itself.
        ``pages_read``: the pages the page reader really read (a page it wrote nothing real for is in ``page_text``,
        blank, but was not read); all of ``page_text`` when not given.

        Every file is read the same way: a PDF's text layer where its page has one, local OCR (two engines) for a scan
        or a photo, a text file as it is; the same readers and checks, then coded from AP's history. A document the
        page reader hasn't read yet (``page_text`` None) is never approved without a person: it is queued for the
        page reader, which decides that once it has read it."""
        result = PipelineResult(source=Path(path))
        text_file = result.source.suffix.lower() in TEXT_EXTENSIONS
        # (capture, supplier key, profile) when read without AI: kept even if a later step of the reading fails
        captured: dict[str, tuple[Any, str, Any]] = {}
        try:
            self._read_and_code(result, page_text, invoice_id, text_file, captured)
        except CannotRead as exc:  # no OCR or photo add-on, no pages, a web page: said plainly, never an empty reading
            log.warning("%s: %s", result.source.name, exc)
            result.error = str(exc)
        except Exception as exc:  # one bad invoice must not stop a batch
            log.exception("Failed to process %s", result.source)
            result.error = plain_error(exc, result.source)
        azure, reviewer_plan = self._checks_after_coding(result, captured.get("local"), page_text, invoice_id,
                                                         pages_read, text_file)  # fmt: skip
        self._save_and_queue(result, save, invoice_id, azure, reviewer_plan)
        return result

    def _read_and_code(self, result: PipelineResult, page_text: list[str] | None, invoice_id: int | None,
                       text_file: bool, captured: dict[str, tuple[Any, str, Any]]) -> None:  # fmt: skip
        """Read the invoice (``result.extraction``), code it (``result.coding``, AP's fixed rules applied) and check
        it (``result.output``, ``result.report``). ``captured["local"]``: the capture (with its supplier key and
        profile) when the local readers read it (the chat model's coding is read against the page afterwards)."""
        path = result.source
        # A photo or a scanned picture coming in is made a PDF first; one stored as a picture by an older version
        # (read again by the page reader) stays where the database has it.
        path = result.source = _as_pdf(path, result, convert=invoice_id is None)
        _check_pdf(path)
        t0 = time.perf_counter()
        layout = None
        if self._reads_locally(path):  # offline: the page's text layer, or local OCR for a scan
            from ..capture import build_layout
            from .offline_coder import local_extraction

            layout = build_layout(path)
            if invoice_id is None and any(page.turn for page in layout.pages):
                path = result.source = _upright(path, layout, result)
            result.extraction = local_extraction(path, layout)
        else:
            result.extraction = self.extractor.extract(path)
        result.timings["extraction"] = time.perf_counter() - t0

        # Reviewer history relevant to this document (immediate learning).
        vendor_hint = (result.extraction.invoice_fields.get("VendorName") or {}).get("value")
        feedback = self.store.learning_rows(vendor_hint) if self.store is not None else []
        examples = select_examples(feedback, result.extraction.content, vendor_hint)
        result.history_examples = len(examples)
        history_text = format_examples(examples, with_cost_center=self.reference.cost_centers is not None)

        t1 = time.perf_counter()
        provider = getattr(self.coder, "provider", "")
        if provider in ("off", "local"):
            feedback = self._code_locally(result, path, layout, page_text, feedback, history_text, text_file,
                                          captured)  # fmt: skip
        else:
            result.coding = self.coder.code(result.extraction, self._images(path), history_text)
            _mark_chat_accounts(result.coding.coding)
            _model_line_taxes(result.coding.coding, result.extraction.content if result.extraction else "",
                              feedback)  # fmt: skip
        result.timings["inference"] = time.perf_counter() - t1
        if self.store is not None:  # fixed coding rules set by AP win over the AI
            coded, result.rules_applied = apply_rules(result.coding.coding, self.store.coding_rules())
            result.coding.coding = coded

        result.output, result.report = finalise_coding(
            result.coding.coding,
            self.reference,
            self.settings,
            result.extraction,
            store=self.store,
            exclude_invoice_id=invoice_id,
            feedback=feedback,
        )
        if _cut_off(path):  # what is there is read and shown; the person is told part may be missing
            result.report.issues.append(Issue(WARNING, CUT_OFF, CUT_OFF_TEXT.format(name=path.name)))

    def _code_locally(self, result: PipelineResult, path: Path, layout: Any, page_text: list[str] | None,
                      feedback: list[Any], history_text: str, text_file: bool,
                      captured: dict[str, tuple[Any, str, Any]]) -> list[Any]:  # fmt: skip
        """The local readers' header, lines and taxes (they read invoices better than a small model), each line coded
        from what AP approved before; the chat model suggests the lines left over when it answers. A text file is read
        by the same rule reader, its text taken as exact. The capture goes in ``captured``; returns the vendor's
        feedback."""
        from .offline_coder import code_from_capture, read_invoice, text_layout

        provider = getattr(self.coder, "provider", "")
        if layout is None and text_file:
            layout = text_layout(result.extraction.content)
        # Once the page reader has read the pages (in the background), the chat model reads the header from the page
        # images too, when it can see pages: one more independent reader of every field.
        model_values = self._model_reading(path) if page_text is not None and provider == "local" else None
        local = captured["local"] = read_invoice(path, self.store, layout=layout, page_text=page_text,
                                                 ai_values=model_values)  # fmt: skip
        vendor = local[0].fields.get("vendor_name")
        if vendor is not None and vendor.value:
            # The vendor is known now: its lessons, past the cap too, and its examples for the chat model, even when
            # its name is not on the page as AP's history has it ("&" for "and", B-600).
            if self.store is not None:
                feedback = self.store.learning_rows(str(vendor.value))
            examples = select_examples(feedback, result.extraction.content, str(vendor.value))
            result.history_examples = len(examples)
            history_text = format_examples(examples, with_cost_center=self.reference.cost_centers is not None)
        # A weak match (an account's name) is never put in the line: it is left blank, suggested on the review
        # screen, and the chat model is asked about it when it answers (B-463).
        result.coding = code_from_capture(
            local[0], self.reference, feedback, self.store, text=result.extraction.content, layout=layout
        )  # fmt: skip
        if provider == "local":
            self._local_accounts(result.coding, history_text)
        return feedback

    def _checks_after_coding(self, result: PipelineResult, captured: tuple[Any, str, Any] | None,
                             page_text: list[str] | None, invoice_id: int | None, pages_read: int | None,
                             text_file: bool) -> tuple[Any, Any]:  # fmt: skip
        """The capture against the coding, the review issues it raises, and whether the invoice may skip review
        (``result.autonomy``), held for the Azure check and the AI reviewer. Returns their plans."""
        path = result.source
        azure = azure_check.Plan()
        reviewer_plan = verifier.Plan()
        if result.output is None:
            return azure, reviewer_plan
        try:
            t2 = time.perf_counter()
            if captured is not None:  # already read (no AI model): the capture is the proposal itself
                result.capture, key, profile = captured
            else:
                result.capture, key, profile = capture_invoice(
                    path, result.output, store=self.store, page_text=page_text,
                    di_raw=result.extraction.raw if result.extraction else None,
                )  # fmt: skip
            result.timings["capture"] = time.perf_counter() - t2
            if result.capture is not None and result.report is not None:
                for severity, code, message in review_issues(
                    result.capture, result.output.get("currency"), assumed_date=result.output.get("invoice_date")
                ):
                    result.report.issues.append(Issue(severity, code, message))
                # The optional Azure check (off unless turned on): a check already done is folded in again (read
                # again by the page reader); otherwise whether it goes in line, and waits for it.
                azure = azure_check.prepare(self.store, self.settings, invoice_id, result.capture, result.report, path)
                # Not read by the page reader yet: it will be (queued below), and decides then.
                awaiting = page_text is None and not text_file
                read = None if page_text is None else len(page_text)  # it stops at AP_PAGE_READER_MAX_PAGES
                if read is not None and pages_read is not None:
                    read = min(read, pages_read)
                result.autonomy = autonomy_decision(self.store, key, profile, result.capture, result.report, path,
                                                    output=result.output, awaiting_page_reader=awaiting,
                                                    pages_read=read)  # fmt: skip
                result.autonomy = azure_check.hold(result.autonomy, azure)
                # The AI reviewer (on by default): every invoice goes in line, and none is approved without a person
                # until it agrees.
                reviewer_plan = verifier.prepare(self.store, result.autonomy)
                result.autonomy = verifier.hold(result.autonomy, reviewer_plan)
        except Exception:  # one bad invoice must not stop a batch: saved for a person, never touchless
            log.exception("Checks after coding failed for %s", path)
            result.autonomy = {}
        return azure, reviewer_plan

    def _save_and_queue(self, result: PipelineResult, save: bool, invoice_id: int | None, azure: Any,
                        reviewer_plan: Any) -> None:  # fmt: skip
        """Saved to the review queue (``save``) with its capture; queued for the Azure check, the AI reviewer and the
        page reader; approved by AP Coder on its own when its supplier is touchless and nothing holds it back."""
        if self.store is None:
            return
        path = result.source
        if not save and invoice_id:  # read again (the page reader): same invoice
            azure_check.queue(self.store, invoice_id, azure)
            verifier.queue(self.store, invoice_id, reviewer_plan, held=bool(result.autonomy.get("verifier_hold")))
        if not save:
            return
        try:
            result.invoice_id = self.store.add_invoice(
                path,
                result.output,
                result.report.to_dict() if result.report else None,
                extraction_md=result.extraction.content if result.extraction else "",
                meta=_meta(result),
                error=result.error,
                original_sha256=(result.original or {}).get("sha256"),
            )
            if result.capture is not None:
                self.store.save_capture(result.invoice_id, result.capture.to_dict())
            azure_check.queue(self.store, result.invoice_id, azure)
            verifier.queue(self.store, result.invoice_id, reviewer_plan,
                           held=bool(result.autonomy.get("verifier_hold")))  # fmt: skip
            if result.autonomy.get("auto") and result.output is not None:
                self.store.approve_invoice(result.invoice_id, result.output, AUTONOMOUS_REVIEWER, login="ap-coder")
            elif result.output is not None:  # the page reader reads every page of it in the background
                from .page_worker import queue_new_invoice, skip_new_invoice, skip_reason

                why = skip_reason(self.settings, result.capture, result.autonomy, path)  # (server: D-111)
                if not why or not skip_new_invoice(self.store, result.invoice_id, result.capture.page_count, why):
                    queue_new_invoice(self.store, result.invoice_id, path)
        except Exception as exc:  # a file that cannot be saved (locked, gone) must not stop the batch
            log.exception("Could not save %s", path)
            result.error = result.error or f"not saved: {type(exc).__name__}: {exc}"

    def process_many(
        self,
        paths: list[Path],
        workers: int = 1,
        on_result: Callable[[int, PipelineResult], None] | None = None,
    ) -> list[PipelineResult]:
        """Process several invoices; results come back in input order.

        ``on_result(index, result)`` is called as each one finishes, so outputs can be saved
        straight away (an interrupted batch keeps everything finished so far).
        """
        results: list[PipelineResult | None] = [None] * len(paths)

        def done(i: int, result: PipelineResult) -> None:
            results[i] = result
            if on_result is not None:
                on_result(i, result)

        if workers <= 1 or len(paths) <= 1:
            for i, p in enumerate(paths):
                done(i, self.process(p))
            return [r for r in results if r is not None]
        pool = ThreadPoolExecutor(max_workers=workers)
        try:
            # each file in a copy of the caller's context: who started the run, for the events (B-130)
            futures = {pool.submit(contextvars.copy_context().run, self.process, p): i for i, p in enumerate(paths)}
            for future in as_completed(futures):
                done(futures[future], future.result())
        except KeyboardInterrupt:
            pool.shutdown(wait=False, cancel_futures=True)
            raise
        pool.shutdown()
        return [r for r in results if r is not None]


def _as_pdf(path: Path, result: PipelineResult, convert: bool = True) -> Path:
    """The PDF to read. A file coming in (``convert``) named as another kind is renamed to what it is
    (``intake.as_its_kind``), and a picture (JPEG, PNG, HEIC, TIFF, BMP, WEBP, GIF) is made into one, the picture
    kept in ``originals/`` (``intake.to_pdf``); a PDF made from a picture before says which (``result.original``)."""
    from ..intake import pictures as intake

    if convert and path.suffix.lower() not in TEXT_EXTENSIONS:  # a PDF named .jpg, a PNG named .pdf: what it is
        path = result.source = intake.as_its_kind(path)
    if intake.is_image(path) and convert:
        converted = intake.to_pdf(path)
        result.original = {"file": path.name, "kept": str(converted.original), "sha256": converted.sha256,
                           "pages": converted.pages}  # fmt: skip
        log.info("%s: made into %s (the picture is kept in %s)", path.name, converted.pdf.name, converted.original)
        return converted.pdf
    if path.suffix.lower() == ".pdf":
        note = intake.original_of(path)
        if note:
            kept = path.parent / str(note.get("kept") or "")
            result.original = {"file": note.get("original"), "kept": str(kept), "sha256": note.get("sha256")}
    return path


def _upright(path: Path, layout: Any, result: PipelineResult) -> Path:
    """A PDF with pages scanned sideways or upside down, shown upright (``intake.turn_upright``): the words OCR read
    on the upright pages are where the review screen shows them. Left as it is when it can't be written."""
    from ..intake import pictures as intake

    turns = {page.number: page.turn for page in layout.pages if page.turn}
    try:
        turned = intake.turn_upright(path, turns)
    except Exception as exc:  # a locked or damaged file: read all the same, shown as it was scanned
        log.warning("%s: pages scanned turned not set upright (%s)", path.name, exc)
        return path
    for page in layout.pages:
        page.turn = 0  # the page now shows as it was read
    if turned is not None:
        result.original = {"file": path.name, "kept": str(turned.original), "sha256": turned.sha256,
                           "pages": turned.pages}  # fmt: skip
    log.info("%s: pages %s were scanned turned and are now shown upright", path.name, sorted(turns))
    return path


def output_stems(paths: list[Path]) -> list[str]:
    """One output name per input. Same-named files from different folders (e.g. two vendors'
    ``Invoice.pdf``) get ``_2``, ``_3``... so their results don't overwrite each other."""
    taken = {p.stem.lower() for p in paths}  # a file's own name stays its own (c/Invoice_2.pdf keeps Invoice_2)
    first: set[str] = set()
    stems = []
    for p in paths:
        key = p.stem.lower()
        if key not in first:
            first.add(key)
            stems.append(p.stem)
            continue
        n = 2
        while f"{key}_{n}" in taken:
            n += 1
        taken.add(f"{key}_{n}")
        stems.append(f"{p.stem}_{n}")
    return stems


def write_outputs(
    result: PipelineResult, out_dir: str | Path, save_extraction: bool = True, stem: str | None = None
) -> list[Path]:
    """Persist ``<stem>.json`` (target schema only), ``<stem>.validation.json`` and ``<stem>.extraction.md``."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = stem or result.source.stem
    written: list[Path] = []

    if result.output is not None:
        p = out_dir / f"{stem}.json"
        p.write_text(json.dumps(result.output, indent=2, ensure_ascii=False), encoding="utf-8")
        written.append(p)

    meta = _meta(result)
    if result.report:
        meta["validation"] = result.report.to_dict()

    p = out_dir / f"{stem}.validation.json"
    p.write_text(json.dumps(meta, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    written.append(p)

    if save_extraction and result.extraction and result.extraction.model_id != "pre-extracted":
        p = out_dir / f"{stem}.extraction.md"
        p.write_text(result.extraction.content, encoding="utf-8")
        written.append(p)
    return written


def _meta(result: PipelineResult) -> dict[str, Any]:
    meta: dict[str, Any] = {
        "source": str(result.source),
        "status": "ok" if result.ok else "failed",
        "error": result.error,
        "timings_seconds": {k: round(v, 3) for k, v in result.timings.items()},
    }
    if result.rules_applied:
        meta["rules_applied"] = result.rules_applied
    if result.extraction:
        meta["extraction"] = {
            "model_id": result.extraction.model_id,
            "page_count": result.extraction.page_count,
            "table_count": result.extraction.table_count,
            "mean_word_confidence": result.extraction.mean_word_confidence,
            "low_confidence_word_count": result.extraction.low_confidence_word_count,
            "invoice_fields": result.extraction.invoice_fields,
        }
    if result.coding:
        meta["inference"] = {
            "model": result.coding.model,
            "attempts": result.coding.attempts,
            "images_attached": result.coding.images_attached,
            "usage": result.coding.usage,
        }
    meta["history_examples"] = result.history_examples
    if result.original:
        meta["original"] = result.original
    if result.capture is not None:
        meta["capture"] = {
            "status_counts": result.capture.status_counts(),
            "layout": result.capture.layout_source,
            **{k: v for k, v in result.autonomy.items() if v not in ("", None)},
        }
    return meta
