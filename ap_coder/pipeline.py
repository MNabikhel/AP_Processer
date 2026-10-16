"""Unified execution pipeline: extract -> code -> validate -> persist."""

from __future__ import annotations

import contextlib
import json
import logging
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import azure_check
from .capture.bridge import review_issues
from .capture.layout import CannotRead
from .capture.workflow import AUTONOMOUS_REVIEWER, autonomy_decision, capture_invoice
from .config import Settings
from .extraction import SUPPORTED_EXTENSIONS, TEXT_EXTENSIONS, DocumentExtractor, ExtractionResult
from .imaging import render_page_images
from .inference import MODEL_SUGGESTED, CodingError, CodingResult, InvoiceCoder
from .memory import compare_with_history, format_examples, select_examples, vendor_key
from .po import po_findings
from .reference_data import ReferenceData
from .rules import apply as apply_rules
from .schema import InvoiceCoding
from .tax import SUPPLY_OURS, SUPPLY_SITE, build_gl_distribution, supply_source
from .terms import payment_findings
from .validation import Issue, ValidationReport, validate_coding
from .vendors import vendor_findings

if TYPE_CHECKING:
    from .store import Store

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
    original: dict[str, Any] | None = None  # the picture this PDF was made from (intake.py): name, kept, sha256

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
    README files are documentation, never invoices.
    """
    files = sorted(
        c
        for c in folder.iterdir()
        if c.is_file() and c.suffix.lower() in SUPPORTED_EXTENSIONS and not c.name.lower().startswith("readme")
    )
    documents = {c.stem.lower() for c in files if c.suffix.lower() not in TEXT_EXTENSIONS}
    return [c for c in files if c.suffix.lower() not in TEXT_EXTENSIONS or c.stem.lower() not in documents]


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
) -> tuple[dict[str, Any], ValidationReport]:
    """Validate a coding and build the output (target schema + GL distribution).

    Used after inference and again by the dashboard whenever a reviewer edits an invoice.
    """
    if not coding.ship_to_province:  # no ship-to printed: where it is delivered to us (tax.supply_source)
        place, source = supply_source(coding, reference.tax)
        if source in (SUPPLY_SITE, SUPPLY_OURS):  # never the vendor's province: it stays a guess
            coding = coding.model_copy(update={"ship_to_province": place})
    if feedback is None and store is not None:
        feedback = store.feedback_rows(vendor_name=coding.vendor_name)  # history check needs this vendor only
    history = compare_with_history(coding, feedback) if feedback else None
    duplicates = (
        store.find_duplicates(
            coding.vendor_name, coding.invoice_number, exclude_id=exclude_invoice_id, grand_total=coding.grand_total
        )
        if store is not None
        else None
    )
    report = validate_coding(
        coding,
        reference,
        extraction,
        review_threshold=settings.engine.review_threshold,
        history=history,
        duplicate_of=duplicates,
        vendor_findings=(vendor_findings(coding, store, exclude_invoice_id) if store is not None else [])
        + payment_findings(
            coding.to_output(),
            store.default_terms_days() if store is not None else 30,
            vendor_terms=store.vendor_terms(vendor_key(coding.vendor_name)) if store is not None else "",
        ),
        po_findings=po_findings(coding, store, exclude_invoice_id) if store is not None else None,
    )
    output = coding.to_output()
    output["gl_distribution"] = build_gl_distribution(coding, reference.tax)
    return output, report


DAMAGED_PICTURE = ("UnidentifiedImageError", "DecompressionBombError")
DAMAGED_PDF = ("FileDataError", "EmptyFileError")


def plain_error(exc: Exception, path: Path) -> str:
    """What AP reads when an invoice can't be processed: a damaged picture or PDF said plainly with what to do (the
    technical detail is in the log), anything else as the error it is."""
    name = type(exc).__name__
    kind = path.suffix.lstrip(".").upper() or "picture"
    if name in DAMAGED_PICTURE or (name == "OSError" and "image file" in str(exc)):
        return (f"{path.name} can't be opened as a picture: the file is damaged or not really a {kind}. Ask the "
                "vendor for a new copy, or scan it again.")  # fmt: skip
    if name in DAMAGED_PDF:
        return (f"{path.name} can't be opened as a PDF: the file is damaged or incomplete. Ask the vendor for a new "
                "copy.")  # fmt: skip
    if path.suffix.lower() == ".pdf" and _needs_password(path):  # PyMuPDF says "document closed or encrypted"
        return f"{path.name} is protected by a password, so it can't be read. Ask the vendor for a copy without one."
    return f"{name}: {exc}"


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
        from .inference import suggest_accounts
        from .reference_data import UNASSIGNED

        coding = result.coding
        # Lines nothing could code. A match on the account's name stays: on the samples a small model
        # (1.5B) picked the right account less often (11 of 41) than the name match did (21 of 41).
        # Each is numbered by its place on the invoice, not its printed line number: those can repeat (a second
        # page that numbers from 1 again), and a suggestion must never land on a line already coded.
        todo = {n: li for n, li in enumerate(coding.line_items, start=1) if li.predicted_gl_code == UNASSIGNED}
        if not todo:
            return
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
            li.reasoning_justification = f"{MODEL_SUGGESTED}: {reason}" if reason else MODEL_SUGGESTED
        model = getattr(self.coder, "_local_model", "") or "local model"
        result.model = f"local reader + {model}"

    def _model_reading(self, path: Path) -> dict[str, Any] | None:
        """The chat model's own reading of the header from the page images (``inference.read_header``), when the model
        loaded in LM Studio can see pages (Qwen 3.5 can). Asked once per file and model: kept in the data folder
        (``.cache/model_reading``). Never fatal: without it the invoice is read as before."""
        import hashlib
        import re

        from .inference import read_header
        from .paths import private_dir

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
        from .offline import internet_allowed

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
        from .capture.layout import ocr_cache

        folder = None
        if self.store is not None:
            from .paths import private_dir

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
        path = Path(path)
        result = PipelineResult(source=path)
        captured: tuple[Any, str, Any] | None = None  # (capture, supplier key, profile) when read without AI
        text_file = path.suffix.lower() in TEXT_EXTENSIONS
        try:
            # A photo or a scanned picture coming in is made a PDF first; one stored as a picture by an older version
            # (read again by the page reader) stays where the database has it.
            path = result.source = _as_pdf(path, result, convert=invoice_id is None)
            t0 = time.perf_counter()
            layout = None
            if self._reads_locally(path):  # offline: the page's text layer, or local OCR for a scan
                from .capture import build_layout
                from .offline_coder import local_extraction

                layout = build_layout(path)
                if invoice_id is None and any(page.turn for page in layout.pages):
                    path = result.source = _upright(path, layout, result)
                result.extraction = local_extraction(path, layout)
            else:
                result.extraction = self.extractor.extract(path)
            result.timings["extraction"] = time.perf_counter() - t0

            # Reviewer history relevant to this document (immediate learning).
            feedback = self.store.feedback_rows() if self.store is not None else []
            vendor_hint = (result.extraction.invoice_fields.get("VendorName") or {}).get("value")
            examples = select_examples(feedback, result.extraction.content, vendor_hint)
            result.history_examples = len(examples)
            history_text = format_examples(examples, with_cost_center=self.reference.cost_centers is not None)

            t1 = time.perf_counter()
            provider = getattr(self.coder, "provider", "")
            if provider in ("off", "local"):
                # The local readers' header, lines and taxes (they read invoices better than a small model), each
                # line coded from what AP approved before; the chat model suggests the lines left over when it
                # answers. A text file is read by the same rule reader, its text taken as exact.
                from .offline_coder import code_from_capture, read_invoice, text_layout

                if layout is None and text_file:
                    layout = text_layout(result.extraction.content)
                # Once the page reader has read the pages (in the background), the chat model reads the header from the
                # page images too, when it can see pages: one more independent reader of every field.
                model_values = self._model_reading(path) if page_text is not None and provider == "local" else None
                captured = read_invoice(path, self.store, layout=layout, page_text=page_text, ai_values=model_values)
                result.coding = code_from_capture(
                    captured[0], self.reference, feedback, self.store, text=result.extraction.content,
                    assign_weak=provider == "local",
                )  # fmt: skip
                if provider == "local":
                    self._local_accounts(result.coding, history_text)
            else:
                result.coding = self.coder.code(result.extraction, self._images(path), history_text)
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
        except CannotRead as exc:  # OCR or the photo add-on isn't installed: said plainly, never an empty reading
            log.warning("%s: %s", path.name, exc)
            result.error = str(exc)
        except Exception as exc:  # one bad invoice must not stop a batch
            log.exception("Failed to process %s", path)
            result.error = plain_error(exc, path)

        key, profile = "", None
        azure = azure_check.Plan()
        if result.output is not None:
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
                for severity, code, message in review_issues(result.capture, result.output.get("currency")):
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

        if self.store is not None and not save and invoice_id:  # read again (the page reader): same invoice
            azure_check.queue(self.store, invoice_id, azure)
        if self.store is not None and save:
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
                if result.autonomy.get("auto") and result.output is not None:
                    self.store.approve_invoice(result.invoice_id, result.output, AUTONOMOUS_REVIEWER, login="ap-coder")
                elif result.output is not None:  # the page reader reads every page of it in the background
                    from .page_worker import queue_new_invoice

                    source = result.capture.layout_source if result.capture is not None else ""
                    queue_new_invoice(self.store, self.settings, result.invoice_id, path, source)
            except Exception as exc:  # a file that cannot be saved (locked, gone) must not stop the batch
                log.exception("Could not save %s", path)
                result.error = result.error or f"not saved: {type(exc).__name__}: {exc}"
        return result

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
            futures = {pool.submit(self.process, p): i for i, p in enumerate(paths)}
            for future in as_completed(futures):
                done(futures[future], future.result())
        except KeyboardInterrupt:
            pool.shutdown(wait=False, cancel_futures=True)
            raise
        pool.shutdown()
        return [r for r in results if r is not None]


def _as_pdf(path: Path, result: PipelineResult, convert: bool = True) -> Path:
    """The PDF to read: a picture (JPEG, PNG, HEIC, TIFF, BMP) is made into one first (``convert``), the picture kept
    in ``originals/`` (``intake.to_pdf``); a PDF made from a picture before says which (``result.original``)."""
    from . import intake

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
    from . import intake

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
