"""Unified execution pipeline: extract -> code -> validate -> persist."""

from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .config import Settings
from .extraction import SUPPORTED_EXTENSIONS, TEXT_EXTENSIONS, DocumentExtractor, ExtractionResult
from .imaging import render_page_images
from .inference import CodingResult, InvoiceCoder
from .memory import compare_with_history, format_examples, select_examples
from .reference_data import ReferenceData
from .schema import InvoiceCoding
from .tax import build_gl_distribution
from .validation import ValidationReport, validate_coding

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


def discover_inputs(paths: list[str | Path]) -> list[Path]:
    """Expand directories into supported invoice files (sorted, non-recursive)."""
    found: list[Path] = []
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            found += sorted(c for c in p.iterdir() if c.is_file() and c.suffix.lower() in SUPPORTED_EXTENSIONS)
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
    if feedback is None and store is not None:
        feedback = store.feedback_rows()
    history = compare_with_history(coding, feedback) if feedback else None
    duplicates = (
        store.find_duplicates(coding.vendor_name, coding.invoice_number, exclude_id=exclude_invoice_id)
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
    )
    output = coding.to_output()
    output["gl_distribution"] = build_gl_distribution(coding, reference.tax)
    return output, report


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

    def process(self, path: str | Path) -> PipelineResult:
        path = Path(path)
        result = PipelineResult(source=path)
        try:
            t0 = time.perf_counter()
            result.extraction = self.extractor.extract(path)
            result.timings["extraction"] = time.perf_counter() - t0

            images = None
            if self.settings.engine.vision and path.suffix.lower() not in TEXT_EXTENSIONS:
                images = render_page_images(path, self.settings.engine.vision_max_pages)

            # Reviewer history relevant to this document (immediate learning).
            feedback = self.store.feedback_rows() if self.store is not None else []
            vendor_hint = (result.extraction.invoice_fields.get("VendorName") or {}).get("value")
            examples = select_examples(feedback, result.extraction.content, vendor_hint)
            result.history_examples = len(examples)
            history_text = format_examples(examples, with_cost_center=self.reference.cost_centers is not None)

            t1 = time.perf_counter()
            result.coding = self.coder.code(result.extraction, images, history_text)
            result.timings["inference"] = time.perf_counter() - t1

            result.output, result.report = finalise_coding(
                result.coding.coding,
                self.reference,
                self.settings,
                result.extraction,
                store=self.store,
                feedback=feedback,
            )
        except Exception as exc:  # one bad invoice must not stop a batch
            log.exception("Failed to process %s", path)
            result.error = f"{type(exc).__name__}: {exc}"

        if self.store is not None:
            result.invoice_id = self.store.add_invoice(
                path,
                result.output,
                result.report.to_dict() if result.report else None,
                extraction_md=result.extraction.content if result.extraction else "",
                meta=_meta(result),
                error=result.error,
            )
        return result

    def process_many(self, paths: list[Path], workers: int = 1) -> list[PipelineResult]:
        if workers <= 1 or len(paths) <= 1:
            return [self.process(p) for p in paths]
        with ThreadPoolExecutor(max_workers=workers) as pool:
            return list(pool.map(self.process, paths))


def write_outputs(result: PipelineResult, out_dir: str | Path, save_extraction: bool = True) -> list[Path]:
    """Persist ``<stem>.json`` (target schema only), ``<stem>.validation.json`` and ``<stem>.extraction.md``."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = result.source.stem
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
    return meta
