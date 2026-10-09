"""Component 1 - Extraction layer (Azure AI Document Intelligence).

Runs ``prebuilt-layout`` or ``prebuilt-invoice`` with Markdown output, which
preserves reading order and renders multi-page tables as HTML ``<table>``
blocks, then condenses the result into an :class:`ExtractionResult` that the
inference layer can consume.
"""

from __future__ import annotations

import hashlib
import json
import logging
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import DocumentIntelligenceSettings

log = logging.getLogger(__name__)

DOCUMENT_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".heif", ".heic"}
TEXT_EXTENSIONS = {".md", ".markdown", ".txt"}
SUPPORTED_EXTENSIONS = DOCUMENT_EXTENSIONS | TEXT_EXTENSIONS

LOW_WORD_CONFIDENCE = 0.80

# prebuilt-invoice header fields worth passing to the LLM as cross-check hints.
_INVOICE_HINT_FIELDS = (
    "VendorName",
    "VendorTaxId",
    "CustomerName",
    "InvoiceId",
    "InvoiceDate",
    "DueDate",
    "PurchaseOrder",
    "PaymentTerm",
    "SubTotal",
    "TotalDiscount",
    "TotalTax",
    "InvoiceTotal",
    "AmountDue",
)


@dataclass
class ExtractionResult:
    source: str
    model_id: str
    content: str
    page_count: int = 0
    table_count: int = 0
    mean_word_confidence: float | None = None
    low_confidence_word_count: int = 0
    invoice_fields: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] | None = None

    def to_prompt_payload(self, max_chars: int | None = None) -> str:
        """Render the extraction as the user-message payload for the LLM."""
        content = self.content
        truncated = False
        if max_chars and len(content) > max_chars:
            content = content[:max_chars]
            truncated = True

        parts = [
            "## Extraction Metadata",
            f"- source_file: {Path(self.source).name}",
            f"- extraction_model: {self.model_id}",
            f"- page_count: {self.page_count}",
            f"- table_count: {self.table_count}",
        ]
        if self.mean_word_confidence is not None:
            parts.append(f"- mean_ocr_word_confidence: {self.mean_word_confidence:.3f}")
            parts.append(f"- low_confidence_word_count: {self.low_confidence_word_count}")
        if truncated:
            parts.append(f"- WARNING: document content truncated to the first {max_chars} characters")

        if self.invoice_fields:
            parts += [
                "",
                "## Pre-extracted Invoice Fields (prebuilt-invoice; verify against the document)",
                "```json",
                json.dumps(self.invoice_fields, indent=2, ensure_ascii=False, default=str),
                "```",
            ]

        parts += ["", "## Document Content (Markdown, reading order)", "<document>", content, "</document>"]
        return "\n".join(parts)


def _field_value(fld: dict[str, Any]) -> Any:
    if "valueCurrency" in fld:
        cur = fld["valueCurrency"]
        return {
            "amount": cur.get("amount"),
            "currency": cur.get("currencyCode") or cur.get("currencySymbol"),
        }
    for key in (
        "valueString",
        "valueDate",
        "valueNumber",
        "valueInteger",
        "valuePhoneNumber",
        "valueCountryRegion",
        "valueBoolean",
    ):
        if key in fld:
            return fld[key]
    return fld.get("content")


def _summarise_invoice_fields(raw: dict[str, Any]) -> dict[str, Any]:
    documents = raw.get("documents") or []
    if not documents:
        return {}
    fields = documents[0].get("fields") or {}
    summary: dict[str, Any] = {}
    for name in _INVOICE_HINT_FIELDS:
        fld = fields.get(name)
        if not fld:
            continue
        summary[name] = {"value": _field_value(fld), "confidence": fld.get("confidence")}
    items = (fields.get("Items") or {}).get("valueArray") or []
    if items:
        summary["ItemCount"] = {"value": len(items), "confidence": None}
    return summary


def result_from_raw(source: str | Path, raw: dict[str, Any]) -> ExtractionResult:
    """Build an :class:`ExtractionResult` from an ``AnalyzeResult.as_dict()`` payload."""
    pages = raw.get("pages") or []
    confidences = [
        w["confidence"] for page in pages for w in (page.get("words") or []) if w.get("confidence") is not None
    ]
    return ExtractionResult(
        source=str(source),
        model_id=raw.get("modelId", "unknown"),
        content=raw.get("content") or "",
        page_count=len(pages),
        table_count=len(raw.get("tables") or []),
        mean_word_confidence=statistics.fmean(confidences) if confidences else None,
        low_confidence_word_count=sum(1 for c in confidences if c < LOW_WORD_CONFIDENCE),
        invoice_fields=_summarise_invoice_fields(raw),
        raw=raw,
    )


def result_from_text(path: str | Path) -> ExtractionResult:
    """Treat a .md/.txt file as already-extracted content (skips Document Intelligence)."""
    path = Path(path)
    data = path.read_bytes()
    try:
        content = data.decode("utf-8-sig")  # -sig: a byte-order mark is not part of the text
    except UnicodeDecodeError:  # saved as "ANSI" by a Windows program
        content = data.decode("cp1252", errors="replace")
    return ExtractionResult(
        source=str(path),
        model_id="pre-extracted",
        content=content,
        page_count=max(1, content.count("<!-- PageBreak -->") + 1),
        table_count=content.count("<table"),
    )


def build_credential(settings: DocumentIntelligenceSettings) -> Any:
    """API key when configured, otherwise Entra ID via DefaultAzureCredential."""
    if settings.api_key:
        from azure.core.credentials import AzureKeyCredential

        return AzureKeyCredential(settings.api_key)
    from azure.identity import DefaultAzureCredential

    return DefaultAzureCredential()


class DocumentExtractor:
    """Thin wrapper around ``DocumentIntelligenceClient`` with an optional on-disk cache.

    The cache is keyed on the file hash plus analysis options so prompt and
    model experiments can be re-run without paying for OCR again.
    """

    def __init__(
        self,
        settings: DocumentIntelligenceSettings,
        client: Any | None = None,
        cache_dir: str | Path | None = None,
    ) -> None:
        self.settings = settings
        self._client = client
        self.cache_dir = Path(cache_dir) if cache_dir else None

    @property
    def client(self) -> Any:
        if self._client is None:
            self._client = self._build_client()
        return self._client

    def _build_client(self) -> Any:
        from azure.ai.documentintelligence import DocumentIntelligenceClient

        if not self.settings.endpoint:
            raise RuntimeError(
                "AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT is not set (pass a .md/.txt file to skip extraction)"
            )
        return DocumentIntelligenceClient(endpoint=self.settings.endpoint, credential=build_credential(self.settings))

    def _cache_path(self, data: bytes) -> Path | None:
        if not self.cache_dir:
            return None
        key = hashlib.sha256(data).hexdigest()
        opts = f"{self.settings.model_id}|{self.settings.pages}|{self.settings.locale}"
        opts_hash = hashlib.sha256(opts.encode()).hexdigest()[:12]
        return self.cache_dir / f"{key}.{opts_hash}.json"

    def extract(self, path: str | Path) -> ExtractionResult:
        path = Path(path)
        suffix = path.suffix.lower()
        if suffix in TEXT_EXTENSIONS:
            return result_from_text(path)
        if suffix not in DOCUMENT_EXTENSIONS:
            raise ValueError(f"Unsupported file type {suffix!r} for {path}")

        data = path.read_bytes()
        cache_path = self._cache_path(data)
        if cache_path and cache_path.exists():
            log.info("Extraction cache hit for %s", path.name)
            return result_from_raw(path, json.loads(cache_path.read_text(encoding="utf-8")))

        raw = self._analyze(data)
        if cache_path:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        return result_from_raw(path, raw)

    def _analyze(self, data: bytes) -> dict[str, Any]:
        from azure.ai.documentintelligence.models import DocumentContentFormat

        log.info("Analyzing document with %s", self.settings.model_id)
        kwargs: dict[str, Any] = {"output_content_format": DocumentContentFormat.MARKDOWN}
        if self.settings.locale:
            kwargs["locale"] = self.settings.locale
        if self.settings.pages:
            kwargs["pages"] = self.settings.pages
        poller = self.client.begin_analyze_document(
            self.settings.model_id,
            body=data,
            content_type="application/octet-stream",
            **kwargs,
        )
        result = poller.result()
        return result.as_dict() if hasattr(result, "as_dict") else dict(result)
