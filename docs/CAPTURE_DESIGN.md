# Invoice capture: design

Goal: read invoices accurately enough that a supplier can move to **touchless processing** once AP
Coder has proved it on that supplier's own invoices. Inspired by CloseDesk's page reader (text
layer, OCR with confidence, boxes on the page, agreement between readers).

No single reader is right on every invoice. Accuracy comes from three things together:

1. **Several independent readers** of every header field:
   - the PDF's own text layer (exact) or OCR words with their confidence;
   - a rule reader (labels, patterns and positions, English and French);
   - the supplier's learned template, once AP has confirmed a few invoices;
   - Azure Document Intelligence `prebuilt-invoice` fields, when used;
   - the AI coder's answer, located back on the page.
2. **Checks that cannot be fooled by a misread:**
   - subtotal + taxes = total, and the lines add up to the subtotal;
   - tax = rate × taxable amount, at the official rate for the province;
   - GST/HST and QST numbers have the right format and check digit;
   - the vendor master agrees on the registration number;
   - dates are plausible and the PO exists.
3. **A confidence per field**, calibrated on a benchmark, that decides the route:
   - **verified**: readers agree and the checks pass; safe to post;
   - **likely**: one strong reader with the checks passing; quick look;
   - **check**: disagreement or a failed check; AP must look;
   - **missing**: nothing found.

A supplier earns **autonomy** when AP has confirmed enough of its invoices with almost no
corrections. The bar is a lower confidence bound, not a lucky streak. Autonomous invoices still go
through every check; any failed check, or any field below *verified*, sends the invoice back to a
person. A random audit sample of autonomous invoices keeps measuring accuracy, and one correction
demotes the supplier.

## Package `ap_coder/capture/`

Coordinates are **fractions of the page** (0–1, origin top-left), so they survive rendering at any
DPI. Pages are numbered from 1.

<!-- fmt: off -->
```python
# types.py
@dataclass(frozen=True)
class Box:
    page: int
    x0: float; y0: float; x1: float; y1: float   # 0..1, top-left origin

@dataclass
class Word:
    text: str
    box: Box
    conf: float = 1.0                # 1.0 for the PDF text layer; OCR/DI confidence otherwise
    source: str = "text"             # "text" | "ocr" | "di"

@dataclass
class Line:
    words: list[Word]
    box: Box
    text: str                        # words joined with single spaces

@dataclass
class PageLayout:
    number: int
    width: float; height: float      # points (PDF) or pixels (image)
    words: list[Word]
    lines: list[Line]
    rotation: int = 0

@dataclass
class DocLayout:
    pages: list[PageLayout]
    source: str                      # "text" | "ocr" | "di" | "mixed"
    def words(self) -> Iterator[Word]: ...
    def text(self) -> str: ...       # reading order, lines joined by "\n", pages by "\f"

@dataclass
class Reading:                       # one candidate value from one reader
    field: str
    value: Any                       # normalized: str id, "YYYY-MM-DD", float amount, "CAD"
    raw: str                         # the text as printed
    boxes: list[Box]
    score: float                     # reader's own 0..1 score
    method: str                      # "label-right", "label-below", "pattern", "totals-block",
                                     # "template", "di", "ai-located", ...

@dataclass
class FieldResult:
    field: str
    value: Any
    confidence: float                # calibrated 0..1
    status: str                      # "verified" | "likely" | "check" | "missing"
    boxes: list[Box]
    sources: dict[str, Any]          # reader -> value it read (for the review screen)
    reasons: list[str]               # plain-English: "3 readers agree", "total does not add up"

@dataclass
class LineReading:
    description: str; quantity: float | None; unit_price: float | None; amount: float | None
    boxes: list[Box]                 # row box first
    score: float

@dataclass
class CaptureResult:
    fields: dict[str, FieldResult]
    line_items: list[LineReading]
    checks: list[dict]               # {"code","ok","detail"}
    layout_source: str
    page_count: int
    def to_dict(self) -> dict: ...   # JSON-safe, stored with the invoice
    @classmethod
    def from_dict(cls, d: dict) -> "CaptureResult": ...
```
<!-- fmt: on -->

Header fields (`FIELDS`): `vendor_name`, `invoice_number`, `invoice_date`, `due_date`, `po_number`,
`currency`, `gst_hst_registration_number`, `qst_registration_number`, `subtotal`, `gst_amount`,
`hst_amount`, `pst_amount`, `qst_amount`, `tax_total`, `grand_total`, `payment_terms`.

Modules:
- `normalize.py`: amounts ("1,234.56", "1 234,56 $", "(12.00)", "-12.00", "12.00 CR"), dates
  (ISO, "Oct 7, 2026", "7 octobre 2026", "07/10/2026" with day/month resolution rules), ids
  (case, spaces, dashes), GST/HST BN (`123456789 RT 0001`, with the Luhn check on the 9 digits) and
  QST numbers (`1234567890 TQ 0001`).
- `layout.py`: `build_layout(path, *, di_raw=None, ocr="auto") -> DocLayout`. Uses the text layer
  (PyMuPDF words) when the page has one, OCR (RapidOCR, optional) for scanned pages and images,
  and Document Intelligence words when `di_raw` is given.
- `reader.py`: `read_fields(layout) -> dict[str, list[Reading]]`, candidates best first;
  `read_line_items(layout) -> list[LineReading]`.
- `locate.py`: `locate(layout, field, value) -> list[Reading]`. Finds where a value given by
  another reader (the AI, Document Intelligence) is printed.
- `confidence.py`: `fuse(...) -> dict[str, FieldResult]` and the cross-field checks.
- `supplier.py`: per-supplier templates and statistics (learning and autonomy).
- `__init__.py`: `analyze(path, *, di_raw=None, ai_values=None, template=None, vendor=None) -> CaptureResult`.

## Benchmark `ap_coder/bench/`

A generator of **random invoices with ground truth**: random layouts, label wordings (English and
French), fonts, number and date formats, multi-page tables, credit notes, logos and noise text. Each
is made as a digital PDF and as a "scan": rasterized, slightly rotated, noisy and JPEG-compressed.
`python -m ap_coder.bench run --n 500 --seed 1` reports per field:
- the accuracy of the top reading;
- the precision of fields marked *verified* (target ≥ 99.5%) and how many are verified (coverage);
- calibration: stated confidence against observed accuracy.

### Confidence is measured, not guessed

Every field result carries an **evidence pattern**: which readers found the value, how the rule
reader found it (label to the right, below, in a header grid, top of page...), whether the page was a
text PDF or a scan, and which checks confirmed it (for example
`grand_total|rules|label-right|text|adds-up,confirmed`). `python -m ap_coder.bench calibrate RUN...`
counts, over benchmark runs, how often each pattern was right and writes
`ap_coder/capture/calibration.json`. A field's confidence is then the **lower 95% bound** of its
pattern's measured accuracy (Wilson interval), once the pattern has been seen at least 40 times.
So *verified* (≥ 98.5%) means "this kind of evidence was right at least 98.5% of the time, allowing
for chance": a pattern seen 300 times without a single error qualifies; one seen 100 times does not
yet.

Three rules sit on top, because the benchmark is synthetic and real invoices will surprise it:
- a value found by **one reader only**, with no check confirming it, is capped at 97% (*likely* at
  best): *verified* needs a second reader or a hard check to agree;
- checks that only show a value is *reasonable* (the date is plausible, the due date is after the
  invoice date) confirm nothing; *verified* comes from checks a misread almost never passes: the
  totals add up, the tax is the official rate, the GST/HST number's check digit, the vendor master,
  and the due date = invoice date + the payment terms;
- a date that reads both ways (03/04/2026) and that nothing on the page resolves stays at *check*.
  The order is taken from the document's other dates, a US supplier address, or the terms.

## Accuracy on the benchmark

Measured on invoices the reader was never tuned on (fresh random seeds), with **only the rule
reader and the checks**: no AI, no Document Intelligence, no supplier template. In production those
add independent readers, which is what lifts identifiers and names to *verified*.

BENCH_TABLE

What this means for "99%":
- On digital PDFs the reader is already at 99%+ per field, and no field it marked *verified* was
  wrong. On scans OCR misreads characters (l/I, O/0, accents) and drops spaces; most of the gap is
  there, and Document Intelligence's OCR or the AI's reading of the same page closes it.
- 99% of fields right is not 99% of invoices right: an invoice has ~10 printed fields. That is why
  the route is per field (*verified* fields need no look) and why autonomy is earned **per
  supplier** on its own confirmed invoices, not granted on benchmark numbers.
- The benchmark is a tool, not a guarantee: its layouts are varied but invented. The pilot measures
  the same numbers on real invoices (every approval records which fields AP corrected), and a
  supplier only goes touchless once *its* corrections over its recent invoices show ≥ 99% with
  confidence (lower bound, not a streak).

### Scale

Text-layer capture takes about 20 ms per invoice on one CPU core; OCR of a scanned page about
2-4 s. A million invoices a month is ~23 per minute in a working month: one core for digital
PDFs, a handful of OCR workers (or Document Intelligence) for scans. Capture is stateless per
invoice, so it runs in parallel workers behind a queue; supplier templates and statistics are small
rows keyed by supplier.

### Supplier learning simulation

`python -m ap_coder.bench learn --suppliers 30 --invoices 40 --seed 1 [--scanned 0.0]` simulates
supplier learning: each supplier sends a stream of invoices that look alike (same vendor, layout,
wording and formats; new number, dates, PO, lines and amounts). They are read in order with the
supplier's template, AP approves the true values (outcomes recorded, template learned, as on
approval), and the autonomy policy is applied, switched on as soon as a supplier is ready. The
report gives accuracy by invoice position (against the same invoices read without a template), when
each supplier would reach the policy, and after that the touchless share and its errors.
`--ignore-check CODE` is a what-if: a failed check with that code does not hold an invoice back.

## Review screen

The invoice page with a box over every field, coloured by status (green verified, blue likely, amber
check, red failed check). Click a field in the panel to jump to it on the page. To teach, click a
field and then the words on the page that hold it. Each confirmation trains the supplier's template.

## JDE EnterpriseOne

Approved invoices export as F0411Z1 (voucher) and F0911Z1 (G/L distribution) batch rows for the
R04110Z voucher batch processor: supplier address number, company, BU.Object.Sub accounts, Julian
dates and tax explanation codes, all mapped in settings. A direct connection (AIS/Orchestrator)
comes later and uses the same mapping.
