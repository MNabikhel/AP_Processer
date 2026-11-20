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
    "pst_registration_number",
    "subtotal",
    "gst_amount",
    "hst_amount",
    "pst_amount",
    "qst_amount",
    "tax_total",
    "grand_total",
    "payment_terms",
)
AMOUNT_FIELDS = ("subtotal", "gst_amount", "hst_amount", "pst_amount", "qst_amount", "tax_total", "grand_total",
                 "other_charges")  # fmt: skip
# Read and checked, but not a header field of the ERP voucher: freight, delivery or a discount printed
# between the subtotal and the total (so that subtotal + charges + taxes = total).
EXTRA_FIELDS = ("other_charges",)
DATE_FIELDS = ("invoice_date", "due_date")

VERIFIED, LIKELY, CHECK, MISSING = "verified", "likely", "check", "missing"
# Added to a reading's method when it was printed for the customer (the bill-to block, after "Client:"): a
# registration number read only so may be the customer's. The calibration reads the method by its family ("pattern").
BILL_TO_READING = "+bill-to"
# Added to an amount read under a "PST/QST" label the page does not settle (no QST or PST number of the supplier's):
# which of the two taxes it is stays a person's call, however well it adds up.
EITHER_TAX = "+pst-or-qst"
# Added to terms read from a line that asks for a deposit first ("30% d'acompte, solde à 30 j"): the net days read are
# only the balance's terms, never verified.
PART_OF_TERMS = "+with-deposit"
# Added to a supplier's name read from the top of the page when another name there scored nearly as well (a store's
# brand and its operator's legal name): never verified unless the vendor master confirms it.
RIVAL_NAME = "+rival-name"
# Added to a subtotal when the totals add up with another subtotal printed too (before and after the discounts and
# charges): never verified.
TWO_SUBTOTALS = "+two-subtotals"
# Added to a reading found by wording the benchmark never prints (terms in a footer sentence, a German or Spanish
# label, "Your reference" for the PO): the measured record of its reading's family ("label-inline") is not its
# own, so it keeps the reader's own score and is never verified on its own.
UNMEASURED = "+unmeasured"
# Added to a supplier template's reading it cannot vouch for: read where the value was learned with its label not
# found on the page (whatever is printed there now), or at a spot never settled (on every invoice learned from, the
# value was printed somewhere else too: an order date the day of the invoice). It fills a field no other reader read
# (to be checked) or agrees, but is never a reader of its own: not counted towards verified, not first over another
# reader's value, and measured apart from the template's labelled readings (its own family, "template-unsettled").
UNSETTLED = "-unsettled"
# Added to a total read on a document that takes something off before the amount to pay (a deposit or prepayment,
# an agency's commission), and to that amount itself: whether the gross or the net is posted is AP's call, so a person
# checks it, however well it adds up (D-47).
TAKEN_OFF = "+taken-off"
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
    # A scan's tilt: how far the text runs down the page per unit across (both as fractions of the page), from the
    # OCR's own text lines; 0 for a PDF's text. The line items' rows are read straightened (reader.straightened).
    skew: float = 0.0
    # A page scanned turned (sideways, upside down): the clockwise quarter turn (90, 180, 270) that brought the OCR's
    # lines upright. The words are read on the upright page; 0 when it was upright already.
    turn: int = 0
    # A scan's resolution (pixels per inch, the coarser way): from the picture filling a PDF page, else the picture's
    # size taken as a letter page's long side; 0 for a PDF's text or when unknown.
    dpi: float = 0.0


@dataclass
class DocLayout:
    pages: list[PageLayout]
    source: str  # "text" | "ocr" | "di" | "mixed"
    pages_left_out: int = 0  # the file's pages past the pages read (``build_layout`` reads at most ``max_pages``)
    # Of the pages here, the ones kept empty, never read: OCR failed on them, or they are a long file's pages between
    # its first and last pages read (``layout.pages_read``). ``not_read``: their numbers.
    pages_unread_locally: int = 0
    not_read: list[int] = field(default_factory=list)

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
    label: str = ""  # the label printed over the value, apart from it (a totals table of one row's column, B-2831)


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
    label: str = ""  # the label a reader read it under, printed apart from it (``Reading.label``; B-2831)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["boxes"] = [b.to_list() for b in self.boxes]
        d["confidence"] = round(self.confidence, 4)
        if not self.label:  # (most fields: stored as before)
            del d["label"]
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> FieldResult:
        return cls(
            field=d["field"], value=d.get("value"), confidence=float(d.get("confidence") or 0),
            status=d.get("status") or MISSING, boxes=[Box.from_list(b) for b in d.get("boxes") or []],
            sources=dict(d.get("sources") or {}), reasons=list(d.get("reasons") or []),
            evidence=d.get("evidence") or "", label=str(d.get("label") or ""),
        )  # fmt: skip


# Who read a line item (``LineReading.source``), in plain words for the review screen.
LINE_SOURCES = {
    "table": "the table finder",  # the words inside the table the layout model found on the page
    "text table": "the PDF's table",  # the PDF's text, in the columns its ruling lines and gaps draw
    "words": "the words on the page",  # the PDF's text or OCR, the whole page (no table found)
    "listed": "the amounts listed above the subtotal",  # a receipt's items: no headings, adding up to the subtotal
    "vlm": "OvisOCR2",  # the page reader's transcription of the table
    "di": "Azure Document Intelligence",  # the optional cloud reader's Items, when an Admin switched it on (D-245)
}


# What a coded line's reasoning says of a quantity worked out (``LineReading.quantity_from``): QUANTITY_WORKED_OUT.
QUANTITY_NOTE = "the quantity was not read"


@dataclass
class LineReading:
    description: str
    quantity: float | None
    unit_price: float | None
    amount: float | None
    boxes: list[Box] = field(default_factory=list)
    score: float = 0.0
    source: str = ""  # the reader whose row this is (LINE_SOURCES); "" for a reading stored before it was recorded
    agreed: list[str] = field(default_factory=list)  # other readers that read the same row (amount, quantity, words)
    # The text of the line table's tax column on this row, as printed ("G", "GP", "G R", "H", "E", "*", "exempt"): the
    # line's tax code, read with the invoice's legend (``coding.line_tax.prefill``, D-64). "" when there is none.
    tax_flags: str = ""
    # The description as printed, when the one shown is how AP named this line on an earlier invoice of the supplier
    # (``capture.line_memory``, D-120); "" when it is shown as read.
    read_as: str = ""
    # The tax amounts printed on this row under the line table's tax columns, by the tax each heading names ("GST",
    # "HST", "PST", "QST"; "TAX" under one that names none, or two): 0.0 where the cell prints none, or the row's own
    # total proves there is none; None where no cell was read (blank, or not found: never "exempt" by itself). None
    # for the whole when the table has no tax column. ``bridge.printed_taxes`` reads them with the invoice's taxes.
    taxes_read: dict[str, float | None] | None = None
    # How the quantity was worked out when it was not read: the amount at the unit price ("209.60 ÷ 52.40", B-2892),
    # said on the coded line (``QUANTITY_NOTE``) and by an info finding (B-2896); "" when it was read, or is none.
    quantity_from: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["boxes"] = [b.to_list() for b in self.boxes]
        if self.taxes_read is None:  # (most invoices: stored as before)
            del d["taxes_read"]
        if not self.quantity_from:
            del d["quantity_from"]
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> LineReading:
        return cls(d.get("description") or "", d.get("quantity"), d.get("unit_price"), d.get("amount"),
                   [Box.from_list(b) for b in d.get("boxes") or []], float(d.get("score") or 0),
                   str(d.get("source") or ""), [str(s) for s in d.get("agreed") or []],
                   str(d.get("tax_flags") or ""), str(d.get("read_as") or ""),
                   dict(d["taxes_read"]) if isinstance(d.get("taxes_read"), dict) else None,
                   str(d.get("quantity_from") or ""))  # fmt: skip


@dataclass
class CaptureResult:
    fields: dict[str, FieldResult]
    line_items: list[LineReading] = field(default_factory=list)
    checks: list[dict[str, Any]] = field(default_factory=list)
    layout_source: str = ""
    page_count: int = 0  # the file's pages, read or not
    # Of them, the pages the local readers did not read: OCR failed on them, or the file is longer than the pages
    # read (``layout.read_pages``). ``pages_not_read``: their numbers, when the reading named them.
    pages_left_out: int = 0
    credit_note: bool = False  # titled as a credit note: a person applies it, whatever the sign of its total
    pages_not_read: list[int] = field(default_factory=list)
    not_invoice: str = ""  # why this may not be an invoice, in words (``not_invoice.not_an_invoice``); "": it is one
    # Text on it addressed to software, quoted (``instructions.addressed_to_software``, D-259); []: none
    instructions: list[str] = field(default_factory=list)
    # The invoices the file seems to hold, each "4521 on page 1" (``not_invoice.several_invoices``, D-261); []: one
    several_invoices: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "fields": {k: v.to_dict() for k, v in self.fields.items()},
            "line_items": [li.to_dict() for li in self.line_items],
            "checks": list(self.checks),
            "layout_source": self.layout_source,
            "page_count": self.page_count,
            "pages_left_out": self.pages_left_out,
            "credit_note": self.credit_note,
            **({"pages_not_read": list(self.pages_not_read)} if self.pages_not_read else {}),
            **({"not_invoice": self.not_invoice} if self.not_invoice else {}),
            **({"instructions": list(self.instructions)} if self.instructions else {}),
            **({"several_invoices": list(self.several_invoices)} if self.several_invoices else {}),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CaptureResult:
        return cls(
            fields={k: FieldResult.from_dict(v) for k, v in (d.get("fields") or {}).items()},
            line_items=[LineReading.from_dict(x) for x in d.get("line_items") or []],
            checks=list(d.get("checks") or []), layout_source=d.get("layout_source") or "",
            page_count=int(d.get("page_count") or 0), pages_left_out=int(d.get("pages_left_out") or 0),
            credit_note=bool(d.get("credit_note")), pages_not_read=[int(n) for n in d.get("pages_not_read") or []],
            not_invoice=str(d.get("not_invoice") or ""), instructions=[str(s) for s in d.get("instructions") or []],
            several_invoices=[str(s) for s in d.get("several_invoices") or []],
        )  # fmt: skip

    def status_counts(self) -> dict[str, int]:
        counts = dict.fromkeys(("verified", "likely", "check", "missing"), 0)
        for f in self.fields.values():
            counts[f.status] = counts.get(f.status, 0) + 1
        return counts
