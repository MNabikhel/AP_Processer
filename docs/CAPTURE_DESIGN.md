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
   - the AI coder's answer, located back on the page;
   - the **page reader**, a vision model that transcribes the page image on its own (optional; see
     [The page reader](#the-page-reader-a-vision-model-as-a-second-reader) below).
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

**One invoice at a time, no history** (`bench run`; each invoice is given a received date 1-25 days
after its invoice date, as the pipeline knows the day it processes an invoice):

| test set (fresh seed) | invoices | field accuracy | invoices fully right | no silent error | *verified*: share of fields | *verified*: right | calibration error |
|---|---:|---:|---:|---:|---:|---:|---:|
| digital PDFs | 1,000 | 100.0% | **99.2%** | 99.8% | 54.9% | 100% | 0.019 |
| scans (150-200 dpi, tilted, noisy, JPEG; two local OCR engines) | 200 | 99.6% | 95.5% | 98.0% | 44.2% | 100% | 0.037 |

*No silent error*: every value on the invoice is right, or the one that is not is marked *check* or
*missing* for a person; nothing wrong is shown as *verified* or *likely*. On the digital set 99.4%
of fields come out *verified* or *likely*, right 99.99% of the time.

**Learning each supplier** (`bench learn`: every supplier sends invoices that look alike; AP
approves each one, the template learns, the autonomy policy is applied):

| suppliers × invoices (fresh seed) | 1st invoice | later invoices | reached autonomy | touchless after | touchless with a wrong field |
|---|---:|---:|---:|---:|---:|
| 40 × 40 digital | 100.0% | 100.0% | 21 of 40 (median: invoice 37) | 52 of 109 (48%) | **0** |
| 8 × 25 scanned | 96.2% | 99.0-100% | 0 of 8 | - | - |

No simulated invoice that met the touchless bar (every printed field *verified*, every check passed)
has had a wrong field, in any run. No scanned-only supplier reaches the 99% bound within 25
invoices: the policy is doing its job, and those suppliers stay supervised until more confirmed
invoices, or a second independent reader (Document Intelligence or the AI), close the OCR gap.

Every wrong *verified* value the runs turned up while this was built became a fix and a test (a
thousands comma read as ";" by OCR, a table's "Total" column taken for the invoice total, a
customer's GST number glued to "Your GST No.", a template dropping a credit's "CR", a name read
without its legal suffix).

What this means for "99%":
- **Digital PDFs** (most supplier invoices today): 99.3% of whole invoices fully right with no AI and
  no history, 99.9% with nothing wrong left unflagged, 100% once the supplier's template has learned.
- **Scans** are the gap: local OCR misreads characters (l/I, O/0, accents), drops spaces and
  sometimes misses a word at the edge of the page. 93.5% of scanned invoices are fully right on their
  own and 97.5% have nothing wrong left unflagged. Document Intelligence's OCR or the AI's reading of
  the same page is the independent second reader that closes it; until then scanned suppliers stay
  supervised.
- 99% of fields is not 99% of invoices (an invoice has about ten fields), which is why the numbers
  above are per invoice, why routing is per field, and why autonomy is earned per supplier.
- The benchmark is a tool, not a guarantee: its layouts are varied but invented. The pilot measures
  the same numbers on real invoices (every approval records which fields AP corrected), and a
  supplier only goes touchless once *its* corrections over its recent invoices show >= 99% with
  confidence (lower bound, not a streak).

### Scale

Text-layer capture takes about 20 ms per invoice on one CPU core. A scanned page takes two local
OCR reads, several seconds per page per core (about 30 s per scanned invoice per worker on the
4-core test machine with all cores busy); at volume, scans should go to Document Intelligence or a
GPU OCR service. A million invoices a month is ~23 per minute in a working month: one core for digital
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

## The page reader: a vision model as a second reader

OCR on a phone photo or a poor scan misreads digits and runs column titles together, and a value only one
reader found can be *likely* at best. CloseDesk (the same owner's Outlook assistant) adds a **page reader**:
a vision language model in LM Studio that transcribes the page image (text, and tables as HTML), compared
figure by figure with OCR. AP Coder does the same, with the same recommended model: **OvisOCR2** (0.85B,
Apache-2.0, a Qwen3.5-0.8B trained to read document pages; `bartowski/ATH-MaaS_OvisOCR2-GGUF`, Q8_0 plus
its `mmproj`, about 1 GB).

- **Reading** (`ap_coder/page_reader.py`): each page is rendered at 200 dpi (long side 2048 px) and sent with
  OvisOCR2's own prompt, greedy. A reply that starts repeating itself is stopped and read once more with
  Qwen's sampling; a reply cut off at the length limit, or a blank page, is an error, never a short reading.
  Readings are cached by file, page, model and prompt, so a page is never read twice.
- **One more independent reader** (`capture/transcript.py`): the transcription is laid out as a page (each
  table's cells as separate lines at their column positions) and read by the same rule reader as OCR's
  words. Each value is located back on the real page for its box. The reader is `vlm`
  (`RELIABILITY["vlm"] = 0.85`), independent of the text layer, OCR and the rules: OCR and the page reader
  agreeing makes two readers (a field can reach *verified*), disagreeing sends it to *check*. Its line items
  are used when they add up to the subtotal and OCR's don't.
- **Figure by figure** (`ap_coder/figures.py`, from CloseDesk): every amount in the transcription is counted
  against OCR's text or the PDF's own. The review screen shows how many figures both read the same and lists
  the ones read two ways ("1,105.00 / 1,150.00"). On a digital PDF the first reading is exact, so the share
  read the same is the page reader's own accuracy, measured on every invoice it reads (Settings → Page reader).
- **Linked before it is trusted:** *Settings → Page reader → Test the page reader* reads a scan of a sample
  invoice whose answers are known and shows each field. Until the model in use has passed, the page reader
  reads nothing; another model needs its own test.
- **Never in the way:** reading takes minutes a page on a laptop CPU, so it runs in the background (a thread of
  the dashboard, or `python -m ap_coder read-pages` overnight). An invoice is in the queue at once, read by
  OCR; the reading is folded in only while the invoice is untouched (in review, never approved, no edits), and
  a reviewer's unsaved edits on screen are never replaced.

### Measured with OvisOCR2

Real OvisOCR2 readings (LM Studio, Q8_0) of 9 scanned benchmark invoices with known answers, from seeds never
used to tune the readers (s212-0000, and s39 and s40 cases, among them the ones OCR got wrong or missed):

| | OCR only | OCR + page reader |
| --- | --- | --- |
| Header fields right | 87 of 91 | **91 of 91** |
| Fields verified | 41 | **81** |
| Line items right | 31 of 56 | **56 of 56** |
| Wrong values shown as verified | 0 | 0 |

It read the invoice number OCR made "p.0", the due date, PO and HST OCR missed, and the line tables OCR could not
put in rows. These readings also found two fusion bugs, fixed: a PO two readers wrote differently ("BC-15.332" /
"BC-15332") was verified (now: never verified, the plainer form shown), and a field a third reader agreed on could
lose confidence (now: the pattern without the page reader is the floor until patterns with it are measured).
Four phone photos (no known answers) were checked by hand: currency, dates and totals verified where both read
them; the one total both misread (a year in "Incoterm® 2010") stays marked Check.

The benchmark without the page reader is unchanged by this work: 300 digital invoices (seed 211) 99.8% field
accuracy, 100.0% of verified values right (coverage 55.6%); 400 scans (seed 39) 99.7%, 100.0% (coverage 45.8%),
99.8% with no silent error.

Speed: 7.6 to 12.4 minutes a scanned page, 3.5 to 5.7 minutes a photo, on a 4-core server CPU where LM Studio
gave the model one thread; a laptop letting it use its cores is several times faster (CloseDesk: about 3 minutes
on 4 cores).

## Learning from approvals: each reader's record, local calibration, training data

Every approval is the ground truth for that invoice. When it is approved, each reader's raw value for each
field is scored against what AP approved (`reader_outcomes`), along with the fused value and its evidence key:

- **Readers scorecard** (*Learning & accuracy → Readers*): fields compared and agreed per reader (OCR, OCR's
  second engine, rules, template, page reader, AI), per field, and how often *verified* values were right.
- **Local calibration:** the benchmark gives each evidence key (which readers agreed, which checks passed) a
  measured accuracy. Once AP's approvals hold enough rows for a key, the bound comes from the benchmark and
  the approvals together, so the confidence labels follow your own invoices, not only synthetic ones.
- **Training data** (*Export training data*, or `python -m ap_coder export-training`): a local ZIP of the
  approved invoices: page images, the approved header fields, tax lines and line items, which readers agreed,
  and the same as chat fine-tuning rows. It is the data set to fine-tune a vision model on your own invoices.

## Review screen

The invoice page with a box over every field, coloured by status (green verified, blue likely, amber
check, red failed check). Click a field in the panel to jump to it on the page. To teach, click a
field and then the words on the page that hold it. Each confirmation trains the supplier's template.

## JDE EnterpriseOne

Approved invoices export as F0411Z1 (voucher) and F0911Z1 (G/L distribution) batch rows for the
R04110Z voucher batch processor: supplier address number, company, BU.Object.Sub accounts, Julian
dates and tax explanation codes, all mapped in settings. A direct connection (AIS/Orchestrator)
comes later and uses the same mapping.
