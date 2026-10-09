"""Shared types for invoice capture: words with boxes, readings, field results.

Coordinates are fractions of the page (0..1, origin top-left), so they survive rendering at any
resolution. Pages are numbered from 1.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from typing import Any

FIELDS = (
    "vendor_name",
    "invoice_number",
    "invoice_date",
    "due_date",
    "po_number",
    "currency",
    "gst_hst_registration_number",
    "qst_registration_number",
    "subtotal",
    "gst_amount",
    "hst_amount",
    "pst_amount",
    "qst_amount",
    "tax_total",
    "grand_total",
    "payment_terms",
)
AMOUNT_FIELDS = ("subtotal", "gst_amount", "hst_amount", "pst_amount", "qst_amount", "tax_total", "grand_total")
DATE_FIELDS = ("invoice_date", "due_date")

VERIFIED, LIKELY, CHECK, MISSING = "verified", "likely", "check", "missing"
STATUSES = (VERIFIED, LIKELY, CHECK, MISSING)


@dataclass(frozen=True)
class Box:
    page: int
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    def union(self, other: Box) -> Box:
        return Box(self.page, min(self.x0, other.x0), min(self.y0, other.y0), max(self.x1, other.x1),
                   max(self.y1, other.y1))  # fmt: skip

    def to_list(self) -> list[float]:
        return [self.page, round(self.x0, 5), round(self.y0, 5), round(self.x1, 5), round(self.y1, 5)]

    @classmethod
    def from_list(cls, v: list[float]) -> Box:
        return cls(int(v[0]), float(v[1]), float(v[2]), float(v[3]), float(v[4]))


def union_all(boxes: list[Box]) -> Box | None:
    if not boxes:
        return None
    out = boxes[0]
    for b in boxes[1:]:
        if b.page == out.page:
            out = out.union(b)
    return out


@dataclass
class Word:
    text: str
    box: Box
    conf: float = 1.0
    source: str = "text"  # "text" | "ocr" | "di"


@dataclass
class Line:
    words: list[Word]
    box: Box
    text: str


@dataclass
class PageLayout:
    number: int
    width: float
    height: float
    words: list[Word]
    lines: list[Line]
    rotation: int = 0


@dataclass
class DocLayout:
    pages: list[PageLayout]
    source: str  # "text" | "ocr" | "di" | "mixed"

    def words(self) -> Iterator[Word]:
        for page in self.pages:
            yield from page.words

    def lines(self) -> Iterator[Line]:
        for page in self.pages:
            yield from page.lines

    def text(self) -> str:
        return "\f".join("\n".join(line.text for line in page.lines) for page in self.pages)


@dataclass
class Reading:
    field: str
    value: Any
    raw: str
    boxes: list[Box]
    score: float
    method: str


@dataclass
class FieldResult:
    field: str
    value: Any
    confidence: float
    status: str
    boxes: list[Box] = field(default_factory=list)
    sources: dict[str, Any] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    evidence: str = ""  # the pattern of readers and checks behind the value (calibration key)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["boxes"] = [b.to_list() for b in self.boxes]
        d["confidence"] = round(self.confidence, 4)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> FieldResult:
        return cls(
            field=d["field"], value=d.get("value"), confidence=float(d.get("confidence") or 0),
            status=d.get("status") or MISSING, boxes=[Box.from_list(b) for b in d.get("boxes") or []],
            sources=dict(d.get("sources") or {}), reasons=list(d.get("reasons") or []),
            evidence=d.get("evidence") or "",
        )  # fmt: skip


@dataclass
class LineReading:
    description: str
    quantity: float | None
    unit_price: float | None
    amount: float | None
    boxes: list[Box] = field(default_factory=list)
    score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["boxes"] = [b.to_list() for b in self.boxes]
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> LineReading:
        return cls(d.get("description") or "", d.get("quantity"), d.get("unit_price"), d.get("amount"),
                   [Box.from_list(b) for b in d.get("boxes") or []], float(d.get("score") or 0))  # fmt: skip


@dataclass
class CaptureResult:
    fields: dict[str, FieldResult]
    line_items: list[LineReading] = field(default_factory=list)
    checks: list[dict[str, Any]] = field(default_factory=list)
    layout_source: str = ""
    page_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "fields": {k: v.to_dict() for k, v in self.fields.items()},
            "line_items": [li.to_dict() for li in self.line_items],
            "checks": list(self.checks),
            "layout_source": self.layout_source,
            "page_count": self.page_count,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CaptureResult:
        return cls(
            fields={k: FieldResult.from_dict(v) for k, v in (d.get("fields") or {}).items()},
            line_items=[LineReading.from_dict(x) for x in d.get("line_items") or []],
            checks=list(d.get("checks") or []), layout_source=d.get("layout_source") or "",
            page_count=int(d.get("page_count") or 0),
        )  # fmt: skip

    def status_counts(self) -> dict[str, int]:
        counts = dict.fromkeys(("verified", "likely", "check", "missing"), 0)
        for f in self.fields.values():
            counts[f.status] = counts.get(f.status, 0) + 1
        return counts
