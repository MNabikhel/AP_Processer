# Invoice capture: design

Goal: read invoices accurately enough that a supplier can move to **touchless processing** once AP
Coder has proved it on that supplier's own invoices. Inspired by CloseDesk's page reader (text
layer, OCR with confidence, boxes on the page, agreement between readers).

No single reader is right on every invoice. Accuracy comes from three things together:

1. **Several independent readers** of every header field:
   - the PDF's own text layer (exact) or OCR words with their confidence;
   - a rule reader (labels, patterns and positions, English and French);
   - the supplier's learned template, once AP has confirmed a few invoices;
   - the **page reader**, OvisOCR2 in LM Studio, which transcribes the page image on its own, on every page
     of every invoice, digital PDFs included (see
     [The page reader](#the-page-reader-a-vision-model-as-a-second-reader) below);
   - the AI coder's answer, located back on the page;
   - Azure Document Intelligence `prebuilt-invoice` fields, only in a developer build with the internet
     allowed (`AP_ALLOW_INTERNET=1`), or afterwards as the optional
     [Azure check](#the-azure-check-an-optional-second-opinion) (off by default).
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
demotes the supplier. See [Touchless processing](#touchless-processing-one-switch-one-bar) for the
company-wide switch and the invoices that always go to a person.

## One way to read every invoice

There is one reading pipeline, with nothing to configure (`ap_coder/ai/reading.py`; Settings → Reading shows
each reader's state):

1. every invoice is a PDF: a photo or a scanned picture is made into one when it comes in (see
   [Pictures become PDFs](#pictures-become-pdfs-and-line-items-read-with-the-table-finder)); the PDF's own text
   layer where the page has one, local OCR (two engines) where it has not (scans, photos, iPhone HEIC); the
   table finder finds the line table on each page;
2. OvisOCR2 reads every page of every invoice in the background, as an independent second reader, once it
   has passed its own self-test;
3. the rule reader, the supplier's template and the business checks compare the readers: a field they
   agree on can be *verified*, one they read differently is *check*;
4. on a digital PDF, the hidden text layer is compared with the page as OvisOCR2 sees it printed
   (`TEXT_LAYER_MATCHES_PAGE`);
5. the line items read from the page are compared row by row with OvisOCR2's reading of the same table
   (`LINE_ITEMS_AGREE`);
6. no invoice is approved without a person before OvisOCR2 has read it.

The settings that used to choose a reading mode (`AP_PAGE_READER`, `AP_PAGE_READER_SCOPE`,
`AP_LLM_PROVIDER`, the model pickers) are gone; an older `.env` that still has them is ignored.

### How this compares with the industry

The design follows what the established AP capture products do, rather than inventing a new method:

- **One pipeline, text layer first.** Use the PDF's text when it has one and OCR otherwise, the same steps
  for every document (ABBYY, Google Document AI).
- **Agreement between independent readers** is the strongest confidence signal; here OCR or the text
  layer, OvisOCR2 and the supplier's template are independent readers.
- **Calibrated confidence per field**, and a document goes touchless only when every field passes
  (Rossum).
- **Business rules can block** an invoice whatever its confidence (Rossum, Stampli), and some invoices
  **always need a person** (Stampli): a changed bank account, a possible duplicate, a large amount.
- **Learning per vendor from corrections** (ABBYY's online learning, Vic.ai): each correction updates the
  vendor's template and its record.
- **A random audit sample** of automated decisions keeps measuring accuracy (AWS Augmented AI).
- **Two numbers to watch:** the touchless rate, and the error rate of touchless invoices. Ardent Partners
  reports an average touchless rate of 32.6%, and about 49% for the best in class.

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
    method: str                      # "label-right", "label-below", "label-above", "pattern", "totals-block",
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
- `reader/` (a package): `entry.py` `read_fields(layout) -> dict[str, list[Reading]]`, candidates best first;
  `line_items.py` `read_line_items(layout) -> list[LineReading]`.
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
  The order is taken from the document's other dates, a US supplier address, or the terms;
- a value found by wording the benchmark never prints (terms stated in a footer sentence, a German
  or Spanish label, a PO under "Your reference", a due date in a sentence) keeps the reader's own
  score, not its reading family's measured record: *check* however many OCR reads agree, *likely* at
  most when a check on other fields confirms it (D-45).

Terms are read in the short form the app counts the due date from ("Net 30", "2% 10 Net 30", "Net 30
EOM", "Net 10 prox", "Net 1 month", "2% 10 days", "Due on receipt"): the reader hands the printed
terms to the app's own terms parser (`coding.terms.parse_terms`) and writes back what it read.

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
invoices with OCR alone: the policy is doing its job, and those suppliers stay supervised until more
confirmed invoices, or a second independent reader (OvisOCR2, see
[Measured with OvisOCR2](#measured-with-ovisocr2)), close the OCR gap.

Every wrong *verified* value the runs turned up while this was built became a fix and a test (a
thousands comma read as ";" by OCR, a table's "Total" column taken for the invoice total, a
customer's GST number glued to "Your GST No.", a template dropping a credit's "CR", a name read
without its legal suffix).

What this means for "99%":
- **Digital PDFs** (most supplier invoices today): 99.3% of whole invoices fully right with no AI and
  no history, 99.9% with nothing wrong left unflagged, 100% once the supplier's template has learned.
- **Scans** are the gap: local OCR misreads characters (l/I, O/0, accents), drops spaces and
  sometimes misses a word at the edge of the page. 93.5% of scanned invoices are fully right on their
  own and 97.5% have nothing wrong left unflagged. OvisOCR2's reading of the same page is the
  independent second reader that closes it (91 of 91 header fields right on 9 real scans, below); until
  it has read an invoice, that invoice stays with a person.
- 99% of fields is not 99% of invoices (an invoice has about ten fields), which is why the numbers
  above are per invoice, why routing is per field, and why autonomy is earned per supplier.
- The benchmark is a tool, not a guarantee: its layouts are varied but invented. The pilot measures
  the same numbers on real invoices (every approval records which fields AP corrected), and a
  supplier only goes touchless once *its* corrections over its recent invoices show >= 99% with
  confidence (lower bound, not a streak).

### Before and after one round of fixes (October 2026)

The code before this round (`33b57d8`) and after it, on the same invoices. Seeds 9101 and 9102 were used to find what
to fix, 9301 and 9701 for later fixes; 9501, 9601, 9702, 9801, 9811, 9821, 9901, 9921 and 9931 had not been looked at
before the runs shown. Scans use OCR only (no page reader); *no silent error* means nothing wrong is shown as *verified* or
*likely*.

| set | invoices | fully right | no silent error |
|---|---:|---:|---:|
| digital, seed 9101 | 300 | 99.7% → 99.7% | 100% → 100% |
| digital, seed 9301 | 300 | 99.0% → **100%** | 100% → 100% |
| digital, seed 9701 | 1,000 | 99.7% → **99.9%** | 99.9% → **100%** |
| digital, seed 9801 (never looked at) | 1,000 | 99.4% → **99.8%** | 100% → 100% |
| digital, seed 9901 (never looked at) | 1,000 | 99.7% → 99.7%, then **99.9%*** | 99.9% → 99.9%, then **100%*** |
| digital, seed 9921 (never looked at, final code) | 1,000 | 99.4% → **99.9%** | 100% → 100% |
| digital, seed 9931 (never looked at, final code) | 2,000 | 99.5% → **99.9%**, then **100%*** | 99.9% → 99.9%, then **100%*** |
| scans, seed 9102 | 100 | 89% → **97%** | 98% → **100%** |
| scans, seed 9301 | 100 | 95% → **99%** | 100% → 100% |
| scans, seed 9501 (never looked at) | 100 | 97% → **98%** | 99% → 99%, then 100%* |
| scans, seed 9601 (never looked at) | 150 | 96.0% → **97.3%** | 99.3% → **100%** |
| scans, seed 9702 (never looked at) | 150 | 92.0% → **94.0%** | 98.0% → 98.7%, then 100%* |
| scans, seed 9811 (never looked at, final code) | 150 | 92.7% → **95.3%** | 98.7% → **100%** |
| scans, seed 9821 (never looked at, final code) | 150 | 95.3% → **96.0%**, then **97.3%*** | 98.0% → 98.0%, then **100%*** |

\* The wrong values shown as likely on seeds 9501 and 9702 were looked at after those runs and fixed (OCR's "IN833.79"
for IN83379, a 0.00 HST taken from a registration-number line, terms that lost their discount to OCR's full-width
comma); every set above now has no silent error. "Never looked at" figures are from before that. The misses seed 9702
showed afterwards (OCR's "Apri1", "Eeb", "2.025" and "Tota1") were fixed too: it now reads 97.3% fully right. On
seed 9901, a utility bill's "Payments received" shown as its supplier (logo initials "PH" had been taken for "Ph:", a
phone line) and a PO read from "Unit 7" were fixed after that run. The misses seeds 9811 and 9921 showed (OCR's
"（9,9758）" rate read as an amount, a logo initial after "LP", a label after a fax number) were fixed after theirs:
they now read 96.7% and 100% fully right. Seeds 9821 and 9931 were run on the code as pushed, before and after this
round; on the old code one scan had a wrong invoice number shown as *verified*, on the new one none. Their wrong values
still shown as likely were then fixed: a date that reads both ways taken as three days after the invoice arrived ("2
March" for 3 February, on both), "2%10,.Net45" losing its discount, a street number under a logo's initials "PO" read as
the PO, and "No. 46" read "4.6" by both OCR engines (now sent to a person). The "then" figures are after those fixes.

Supplier learning (`bench learn`, 30 suppliers × 40 invoices, seeds 77, 78 and 79): once a supplier met the
autonomy policy, 226 of its 345 later invoices went without a person before this round and **295 of 345** after
(66% → 86%), with no wrong field on any of them. The gain is from agreeing readers counting their best-measured
evidence (a value the template read first was only *likely* on fewer measured cases) and from templates no longer
anchoring a field to part of a value printed above it.

Line items found: 97.9% → 100% on new digital invoices, 96.3% → 100% on new scans
([line items](#pictures-become-pdfs-and-line-items-read-with-the-table-finder)). GL accounts filled in with no AI model
that were right: 96.6% → 99.5% ([CODING.md](CODING.md)).

Seconds per invoice through the app's pipeline (median, measured as `bench speed` does on a 4-core test machine with
nothing else running): a digital PDF 0.06 → 0.03 s; one with 30,000 past approvals 3.2 → 0.13 s; a scan's first read
11.3 → 7.0 s (each page's image made once for both OCR engines); a scan read again when the page reader's text arrives 8.1 → 2.2 s.

### Harder pictures than the benchmark's scans (stress test, October 2026)

The benchmark's scans are gentle: 150 to 200 dpi, tilted at most 1.5°. A stress test made the same 36 one-page
invoices (seed 9961) into harder pictures and read them with OCR only (values scored, not boxes):

| picture | values right | invoices fully right | no silent error |
|---|---:|---:|---:|
| phone photo: page on a desk, tilted up to 6°, taken at an angle, uneven light | 90.9% → **100%** | 63.9% → **100%** | 88.9% → **100%** |
| page scanned sideways | 21.4% → **98.1%** | 0% → **86.1%** | 47.2% → **94.4%** |
| page scanned upside down | 23.1% → **98.4%** | 0% → **86.1%** | 36.1% → **97.2%** |
| fax, fine (200 dpi, black and white) | 99.5% | 94.4% | 100% |
| fax, standard (200 × 100 dpi) | 93.7% | 61.1% | 88.9% → 91.7% |
| fax at 100 dpi | 67.0% | 8.3% | 75.0% → **100%** |

- **Phone photos** (`layout.flatten_photo`): a photo of a page on a darker desk is flattened as a scanner app does it.
  The page is found (OpenCV, which comes with the OCR), cut out of the desk and squared up, which undoes its tilt
  and the keystone of a photo taken at an angle. The paper in the light is grown out to the page's sharp edge
  (a watershed from the paper and from the picture's border), so a shadow across the page, as dark as the desk but
  with a soft edge, does not cut the page short (B-300). It happens when the photo is made a PDF (`intake/pictures.py`), so the page
  is read, shown and highlighted flattened; the photo as taken is kept in `originals/`. The desk must show on all
  four sides: a scan (it fills its picture, even one with a dark band across its header), a fax and a page on a
  white desk are left as they are, and a scanner's dark bed keeps the resolution it was scanned at. On the
  benchmark's 600 scans and faxes, none was flattened. A photo reads in about 6 s against 5 s for a scan (it is a
  larger picture).
- **Pages scanned turned** (`layout._upright_turn`): OCR reads each line's words right on a turned page (it turns
  a line to read it), but the lines lie where the turned page has them, so labels and amounts no longer line up.
  When most long lines run down the page, the page was scanned sideways: the OCR's lines are turned a quarter turn,
  whichever way puts the amounts right of their labels (when each label shares a line with its amount, whichever
  puts the totals below the invoice's title and dates). When the lines run across, the letters say which way up
  the page is (B-301): in Latin print, capitals and tall letters rise above the small letters' band far more than
  g, j, p, q, y hang below it, so 8 or more lines on their heads, three times as many as stand upright, turn the
  page; clearly upright letters keep it as it is. Else the amounts standing left of their labels turn it: on the
  benchmark's 1,300 OCR reads of upright scans, no upright page scored below 0 (amounts right of labels count up,
  left count down); a page is turned at −2. The words are read on
  the upright page, at no extra OCR cost. The app then shows the PDF upright (`intake.turn_upright`): only the
  page's rotation changes, never what it prints. The scan as it came is kept in `originals/`, its note naming it
  as a picture's does, so the same scan sent again is found; the copy is marked as turned (not as made from a
  picture), so OCR still reads its pages as shown. A PDF AP Coder made is its own and is turned where it is.
- **Pages scanned askew** (`analyze._read_straight`, `layout.TIGHT_FROM`): the header fields, like the line items,
  are read on the page straightened (each line moved to where it would be on a straight page; the boxes put back
  where the words are printed), so a label and its amount at the other end of a row tilted 4 degrees stay on one
  row (B-302). On a page tilted more than about 1.7 degrees each OCR word's box follows its tilted line, so a long
  line's box does not cover the line under it (B-303).
- **Pages scanned too coarsely** (`analyze`, `COARSE_DPI`): the readings' confidence was measured on scans of
  150 dpi and up. On a page scanned under 140 dpi (the picture filling a PDF page says its resolution, the
  coarser way: a standard fax TIFF of 204 × 98 dpi is 98, and its rows are stretched to the page's shape for OCR,
  as a fax viewer shows them; an image file says it, or its long side is taken as a letter page's), every value
  is read but none is shown as verified or likely: a person checks them. A label OCR misread ("INVO1CE" for
  INVOICE) is never taken for a value.
- **Left as it is**: a standard fax (100 dpi down the page) still read 3 of 36 invoices with a wrong value shown
  as *likely*. On two, the customer's name was taken for the supplier's where the fax garbled "Bill to"; on the
  other, a GST number. No value was shown as *verified* wrongly.

### Scale

Text-layer capture takes about 20 ms per invoice on one CPU core, and a digital PDF goes through the whole
pipeline (read, checked, coded, stored) in about 0.13 s: each file is opened, read and searched for tables once per
invoice, and the database is opened once per invoice (`Store.session`). A scanned page takes two local
OCR reads, several seconds per page per core (about 30 s per scanned invoice per worker on the
4-core test machine with all cores busy). The OCR is kept in the data folder (`.cache/ocr/`, by image hash and engine
version, at most 4,000 files), so when the page reader's text arrives the invoice is read again in about 2 s instead of
13 s. `python -m ap_coder.bench speed` measures both passes. At volume, scans should go to Document Intelligence or a
GPU OCR service. A million invoices a month is ~23 per minute in a working month: one core for digital
PDFs, a handful of OCR workers (or Document Intelligence) for scans. Capture is stateless per
invoice, so it runs in parallel workers behind a queue; supplier templates and statistics are small
rows keyed by supplier.

### Supplier learning simulation

`python -m ap_coder.bench learn --suppliers 30 --invoices 40 --seed 1 [--scanned 0.0]` simulates
supplier learning: each supplier sends a stream of invoices that look alike (same vendor, layout,
wording and formats; new number, dates, PO, lines and amounts). They are read in order with the
supplier's template, AP approves the true values (outcomes recorded, template learned, as on
approval), and the autonomy policy is applied: with touchless processing on, a supplier goes touchless by itself
as soon as it meets the bar (`automatic_transition`, as the app applies it). The
report gives accuracy by invoice position (against the same invoices read without a template), when
each supplier would reach the policy, and after that the touchless share and its errors.
`--ignore-check CODE` is a what-if: a failed check with that code does not hold an invoice back. Each approval goes
through the app's own `learn_from_approval` (outcomes, reader scores, template, lessons, a vendor name corrected), and
the report says how many fields AP corrected were right on the supplier's next invoice ("wrong this time, right next
time") and which invoices would have gone touchless with a wrong field ("eligible but wrong"), by position. Two in
twenty suppliers print a name that reads wrong and no GST/HST number (`misread_name`). `--wrong-approval N` has the
clerk teach the invoice number on the date's words on invoice N, withdrawn before the next invoice (or with
`--withdraw-after K` once K more were approved); `--bulk SHARE` approves that share of invoices in bulk, as proposed.

GL coding without an AI model has its own benchmark, `python -m ap_coder.bench coding`: see
[CODING.md](CODING.md).

## Touchless processing: one switch, one bar

The AP clerk trains each vendor by reviewing its invoices; AP Coder takes over a vendor only once that training
proves it, and the rules are the same for everyone (`capture/supplier.py`, `capture/workflow.py`, Settings →
Automation):

- **One switch** (setting `touchless_processing`, off by default). Off: nothing is approved without a person, whatever
  a vendor's record. On: every vendor that meets the bar goes touchless **by itself**, with no per-vendor click
  (`Store.sync_autonomy`, run when the switch is turned on, after every approval and before every decision). Turning
  it on or off is recorded in the Activity log with who did it.
- **One bar**, fixed standard values (`AutonomyPolicy` defaults; an older `autonomy_policy` setting is ignored): at
  least 20 reviewed invoices, a 99% lower bound (95% confidence) on header-field accuracy over the last 200, the last
  10 without a correction; 5% of touchless invoices are still audited.
- **Demotion**: a correction on an audited invoice, or a touchless invoice reopened, suspends the vendor at once. It
  goes touchless again by itself once it meets the bar with a fresh clean streak of 10 invoices reviewed since the
  suspension (`supplier_profiles.suspended_at`).
- **Keep supervised**: an AP lead can exclude one vendor (Learning & accuracy → Supplier learning; state `held`,
  recorded with who and why), and allow it again later.
- **Always a person** (`touchless_gates`), even for a touchless vendor: a changed bank account or GST/HST number, any
  duplicate signal (`DUPLICATE_INVOICE`, `DUPLICATE_IN_ERP`, `DUPLICATE_OTHER_VENDOR`, `POSSIBLE_DUPLICATE_AMOUNT`), an
  unusual amount (`AMOUNT_UNUSUAL`: over 3× the vendor's median approved total), a vendor on hold or not in an imported
  vendor master, a vendor name with no approved invoice yet (`VENDOR_NEW`) or not in the vendor master though its
  GST/HST number is (`VENDOR_MATCHED_BY_TAX_NUMBER`): a supplier's record is kept by its business number, so another
  name printing it does not inherit it, a purchase-order exception (`PO_UNKNOWN`, `PO_CLOSED`, `PO_VENDOR_MISMATCH`,
  `PO_LINE_NOT_ON_PO`, `PO_PRICE_OVER`, `PO_QTY_OVER`, `PO_NOT_RECEIVED`, `PO_OVER_BILLED`), a credit note, a total over the touchless limit (setting `touchless_limit`, default 5,000.00 CAD; a
  foreign currency is converted at the Settings → Review exchange rates; with no rate for it, it goes to a person), a
  total over the second-approver limit, an invoice the page reader has not read yet (or not every page of it), and a
  text file (.md, .txt), which the page reader cannot look at. On top of these, as before,
  any failed check, any printed header field below *verified*, a line with no GL account or cost center yet (which
  a person could not approve either), or a line whose account only the local chat model chose (the touchless bar
  measures the header fields, not line coding) sends the invoice to a person (`should_auto_approve`,
  `invoice_gates`).
- **How long it takes**: at about 13 header fields an invoice, a 99% lower bound with no error needs about 380
  fields read right (n / (n + 1.96²) ≥ 0.99), so a vendor typically reaches the bar after about 30 to
  40 clean invoices, not 20.
- **Training, visible**: every approval records, field by field, what the clerk corrected; the review screen says what
  was learned ("Learned from your 2 corrections (invoice number, due date). Acme Ltd: 14 invoices reviewed, about 6
  more invoices approved without a correction before this vendor can skip review."), and the Learning page shows each vendor's progress. A bulk approval (nobody
  opened the invoice) shows the template where each field is printed, but vouches for no spot: only a person's
  approval settles one, and only its corrections count towards the bar (D-119).
- **Wrong this time, right next time** (D-119): a vendor name AP corrects (read "N0rthwind Supp1y", approved as
  Northwind Supply) is remembered with that approval (`vendor_aliases`, `Store.vendor_alias`): the next invoice that
  reads so is that vendor's, under its name, with its template, vendor master record, coding memory and lessons; the
  name is *likely* until AP has approved it so twice. Only a name that looks like the one read is remembered (not the
  customer's name read as the vendor's), and not for an invoice whose GST/HST number is another's. Withdrawing an
  approval takes back what it taught even after a later approval changed the template: the template is learned again
  from the later approvals (their pages read again, text layers only; a scan's is kept as it is).
- **Upgrading**: vendors an AP lead turned touchless by hand before keep their state but are not touchless while the
  switch is off. When it is turned on, those that meet the standard bar stay touchless; any that do not go back to
  review until they do.
- **The numbers** (Settings → Automation): vendors touchless, ready and learning; the touchless share of invoices
  processed in the last 30 days; errors a person found in audited touchless invoices, and touchless invoices reopened.

## The page reader: a vision model as a second reader

OCR on a phone photo or a poor scan misreads digits and runs column titles together, and a value only one
reader found can be *likely* at best. CloseDesk (the same owner's Outlook assistant) adds a **page reader**:
a vision language model in LM Studio that transcribes the page image (text, and tables as HTML), compared
figure by figure with OCR. AP Coder does the same, with the same recommended model: **OvisOCR2** (0.85B,
Apache-2.0, a Qwen3.5-0.8B trained to read document pages; `bartowski/ATH-MaaS_OvisOCR2-GGUF`, Q8_0 plus
its `mmproj`, about 1 GB).

- **Reading** (`ap_coder/ai/page_reader.py`): each page is rendered at 200 dpi (long side 2048 px) and sent with
  OvisOCR2's own prompt, greedy. A reply that starts repeating itself is stopped and read once more with
  Qwen's sampling; a reply cut off at the length limit, or a blank page, is an error, never a short reading.
  Readings are cached by file, page, model and prompt, so a page is never read twice.
- **One more independent reader** (`capture/transcript.py`): the transcription is laid out as a page (each
  table's cells as separate lines at their column positions) and read by the same rule reader as OCR's
  words. Each value is located back on the real page for its box. The reader is `vlm`
  (`RELIABILITY["vlm"] = 0.85`), independent of the text layer, OCR and the rules: OCR and the page reader
  agreeing makes two readers (a field can reach *verified*), disagreeing sends it to *check*. Its line items
  are used when they add up to the subtotal and OCR's don't.
- **Figure by figure** (`ap_coder/ai/figures.py`, from CloseDesk): every amount in the transcription is counted
  against OCR's text or the PDF's own. The review screen shows how many figures both read the same and lists
  the ones read two ways ("1,105.00 / 1,150.00"). On a digital PDF the first reading is exact, so the share
  read the same is the page reader's own accuracy, measured on every invoice it reads (Settings → Reading).
- **Found and tested on its own:** AP Coder finds OvisOCR2 in LM Studio by itself (no model to pick). Before it
  reads any invoice, OvisOCR2 reads a scan of a sample invoice whose answers are known
  (``page_worker.self_test_if_due``, on its own: no button to press; 10 to 20 minutes on a laptop the first time).
  Until the model in use has passed, the page reader reads nothing and invoices wait for a person; another model
  needs its own test, and a failed test is tried again after a day (or with *Run the self-test again* in Settings
  → Reading). No general vision model reads pages instead of OvisOCR2. While it isn't ready, a banner on the
  Process and Review pages says so.
- **Every invoice, digital PDFs too:** on a digital PDF the transcription is checked against the PDF's hidden
  text (``TEXT_LAYER_MATCHES_PAGE``, `capture/__init__.py`). Editing the hidden text of a PDF is a known way to
  slip another amount past a system that reads the text alone. The check fails when 8 or more figures were
  compared and fewer than 60% were read the same, or when two or more of the text layer's totals (subtotal,
  taxes, total) are nowhere on the page while OvisOCR2 read at least 90% of the other figures the same. The
  fields of the totals it didn't see are marked *check*; the invoice goes to a person (a warning, never
  touchless). One total read differently is just that field's disagreement, marked *check* by fusion.
- **Never in the way:** reading takes minutes a page on a laptop CPU, so it runs in the background (a thread of
  the dashboard, or `python -m ap_coder read-pages` overnight). An invoice is in the queue at once, read by
  OCR; the reading is folded in only while the invoice is untouched (in review, never approved, no edits), and
  a reviewer's unsaved edits on screen are never replaced. An invoice the page reader hasn't read yet is never
  approved without a person: touchless approval is decided once it has read it.

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

**The uniform pipeline, end to end** (a fresh database, real LM Studio with OvisOCR2 Q8_0, nothing set up or pressed):
`read-pages` found OvisOCR2 and ran its self-test on its own (9 of 9 fields right, 17.7 minutes), then read the queue.
A digital sample PDF (2 pages, 18 minutes): OvisOCR2 read all 24 of its figures as the PDF's text layer has them,
`TEXT_LAYER_MATCHES_PAGE` passed, it agreed on all 11 fields it read, and 11 fields are *verified*. A real scan
(1 page, 8.3 minutes): it agreed with OCR on all 11 fields both read, 10 *verified*; across the scan 27 of 34 figures
were read the same by both, and a field they read differently is marked *Check*. Both stayed in review: touchless
processing is off, and neither vendor has a record yet.

## Pictures become PDFs, and line items read with the table finder

**Every invoice is a PDF** (`ap_coder/intake/pictures.py`). A JPEG, PNG, iPhone HEIC/HEIF, TIFF (every frame), BMP, WEBP or GIF
(its first frame: B-264) that comes in (upload, invoices folder, email attachment, `process`/`watch`) is made into a PDF first
(`InvoicePipeline.process`): one page a picture, upright as its EXIF says, at its resolution (a believable DPI gives
the page its size, else a letter-sized page). A JPEG goes in as it is, turned by the page's `/Rotate`; PNG, TIFF,
BMP, WEBP and GIF losslessly; HEIC (which a PDF can't hold) as a quality-95 JPEG. The invoice is stored, read, shown and stamped as
the PDF; the picture moves to `originals/` beside it, and the PDF's metadata records its name, SHA-256 and where it
was kept (`intake.original_of`). `invoices.original_sha256` keeps the picture's hash, so `Store.find_by_hash` finds
the same photo sent twice (upload, folder or `watch`). OCR of a converted page reads the picture's own pixels
(`layout._scan_png`), not a re-rendering: the text read is the same as before the conversion. A picture stored by an
older version and read again by the page reader stays where it is.

**The table finder** (`capture/table_finder.py`) is RapidAI's YOLOv8n "general6" layout model, as CloseDesk uses
it (12 MB ONNX, Apache-2.0, SHA-256 `927b6edcb268e896e6a170f7d78980591b408e04b3908f54d58eb69efd018c95`). It ships in
the offline bundle's `models/` folder (`offline.bundle_models`, `scripts/build_offline_bundle.py`), is looked for
there or in the data folder's `models/`, checked against its SHA-256, and never downloaded by the app. It looks at
every page (digital PDFs rendered too) and returns the tables on it (score ≥ 0.25, overlapping finds merged).
Without it, line items are read as before and Settings → Reading shows its row as missing.

**Line items, read three ways from the page** (`capture/__init__._page_lines`), best first:
1. the words inside the tables found (`reader.inside_tables`): a note or remittance box beside the rows is left out;
2. the words under the column headings across the page (the reading as before);
3. on a digital PDF, the PDF's own table (`capture/tables.py`, the essentials of CloseDesk's `pdf_layout.py`):
   columns from the lines drawn down the table, or the strips no row writes across, named by their headings; a line
   without an amount under a line item is the rest of its description.

The first that adds up to the subtotal is kept. All of them read the rows on the page straightened: a scan's tilt
is measured from the OCR's own text lines (`PageLayout.skew`), and at 1.4° a row's description sits a whole row
away from its amount. A found table whose headings OCR can't read (bilingual headings run together) is read by its
amount column (`reader._headerless_rows`). Sentences under a table that reach the amount column ("subject to GST 5%")
and a total row whose label OCR lost are no longer read as lines.

**Column headings as invoices print them** (`reader._heading_cells`, `reader.heading_row`):
- a heading printed in two languages is one heading ("Prix unitaire / Unit Price"); two of them set close together are
  two columns ("Price / Taux Ext. Price / Prix total"). Headings OCR runs together are read apart at the "/" and where
  a capital starts a word ("Hr/Qty/QuantitePriceEach/PrixunitaireLineTotal/Montant");
- the longest heading names the column: "Unit Cost" is the price, not a quantity ("Unit"); "Prix total" and "Total
  Price" are the amount;
- words after a heading are more of it ("Price Each", "Qty Shipped"); a second heading for a column already named is
  another column, without a name ("Quantity / Unit / Unit Cost": the unit of measure between the quantity and the
  price);
- a segment with a number in it is no heading ("Unit 9", an address printed level with "Amount Due" at the top of a
  bill), nor is a sentence that starts with a heading's word; of several rows of headings, the one naming the most
  columns is the table's.

**Amounts and rows as OCR reads them.** "1530:00" (the point read as a colon) and "342.18CR" (a credit's mark run into
the amount) are amounts printed as money: such lines were dropped as sentences under the table. "392.99.CR" is a credit,
and a unit price to four decimals ("1.3023" a cubic metre) is 1.3023, never 1,302 and a 3. When OCR loses the quantity's
heading or a digit, the quantity is the number that, times the price, makes the amount; a returned quantity keeps its
sign ("-4 × 45.13 = -180.52"). A subtotal's amount set a hair higher than its label ends the table, and a reading that
ran on into the totals (labels OCR runs together: "Totalbefore.tax", "SalesTaxTotal") is cut at the first row that prints
the sum of the rows above it, when those add up to the subtotal (`capture._to_subtotal`).

Camelot (CloseDesk's other table reader) is not used: it installs from wheels for Python 3.11-3.14 on Windows and
macOS, but it needs `opencv-python-headless`, which can't sit beside the `opencv-python` the OCR engines need (both
are the `cv2` module), and adds about 45 MB a platform to the bundle.

**Lines with no headings to go by prove themselves** (D-41, sweep 4). A table found whose headings can't be read is
read by its amount column (`reader._headerless_rows`) only when the rows add up: to a row under them printing their sum
(whatever its wording, "Positionssumme", "Summe netto": the rows end there), to the subtotal or the total less the
taxes, or to an amount printed elsewhere on the invoice; rows naming a tax or a total in any language are no lines,
and a box that stops short of the rows' last amount on the page is not read at all. A receipt (no headings, no table:
"BATH TOWEL 002215445066 $7.97J") is read by `reader.listed_items` when nothing else adds up: the amounts in the column
right above the subtotal, read upward until they add up to it exactly. A summary of charges (Canada Post: "Subtotal |
GST | HST | PST/QST | Total") gives each service at its amount before tax (`reader._charges_summary`), checked
against the subtotal under its column. Also: "Net price", "Net worth" and "Extension" are headings, a tax's heading
run into the amount's is another column, a row with no words printing the lines' sum ends them, page subtotals and
balances brought forward are no lines, a description wrapped under its row is read whole, and a discount printed
without its minus is taken off when that makes the lines add up. On 128 real invoices and receipts the line count
came out exactly right on 65 (was 40), every line right on 63 (was 29), and wrong lines fell from 234 to 3; on the
150 hard generated invoices, 124 (was 117), 122 (was 112) and 100 to 53 (most of those left on one page OCR read
upside down).

**In tandem with OvisOCR2** (`capture/line_items.py`). Once OvisOCR2 has read the invoice, its table is compared row
by row with the page's readings: the same amount, the same quantity when both read one, and descriptions at least
60% alike (letters, or one's words all in the other: OCR runs words together, a code may be in one only). The first
reading of the page that agrees marks every line *agreed* (`LineReading.agreed`, with `source` saying whose row it
is); none agreeing fails `LINE_ITEMS_AGREE` with the first line read differently. A failed `LINE_ITEMS_AGREE` is a
failed check like any other: the invoice is not approved without a person (`should_auto_approve`; it is not one of
`HEADER_PROVEN_SOFT`). When only one side read line items there is nothing to compare and no check. The review
screen says where the lines came from and whether two readers agree.

**Measured.** The benchmark (no page reader, so the readers of the page alone), on the same invoices before and after,
with the table finder:

| test set | header-field accuracy | verified coverage | invoices fully right | line items found (recall) | line items returned that are right (precision) |
| --- | --- | --- | --- | --- | --- |
| 100 scans (seed 41) | 99.7% → 99.7% | 47.0% → **50.1%** | 96.0% → 96.0% | 89.9% → **98.3%** | 89.3% → **99.3%** |
| 300 digital PDFs (seed 211) | 99.9% → 99.9% | 55.6% → 55.8% | 98.7% → 98.7% | 98.2% → **99.7%** | 96.8% → **99.8%** |

No header field read worse, field by field; *verified* values stayed 100% right (more of them, as lines that add up
confirm the subtotal). The benchmark's line matcher counts a line with no description (which OCR often gave a tilted
scan's rows) as found, so the lines were also scored strictly, amount and description (at least 60% alike) both
right:

| test set | before | after, without the table finder | after, with it | invoices with every line right, no extra line |
| --- | --- | --- | --- | --- |
| 100 scans (seed 41) | 29.1% | 95.2% | **98.3%** | 22 → 89 → **91** |
| 300 digital PDFs (seed 211) | 98.2% | 98.2% | **99.7%** | 219 → 287 → **295** |

Most of the gain on scans is the straightening; the table finder adds the tables whose headings can't be read and
keeps text beside the table out. On the 9 real OvisOCR2 readings above, OCR's own line items went from 31 to 55 of 56 lines
(the benchmark's matcher; it also counts a line with no description, which OCR often gave before); with OvisOCR2,
56 of 56 as before, and `LINE_ITEMS_AGREE` passed on 8 of the 9: on the ninth OCR missed one line's amount (a
misread "9.61"), and the check sent it to a person. The 10 sample invoices, with their text copies standing in for
OvisOCR2, agree on every line. The table finder takes about half a second a page on the (busy) 4-core test machine.

**Headings, amounts and the table's end, measured.** The benchmark as `bench run` runs it (no page reader; the table
finder's model isn't in a development checkout), the same invoices before and after the rules above. The lines missed
on the tuning seeds were sorted by cause to find the rules; the fresh seeds were read only once the rules were done:

| test set | line items found (recall) | lines returned that are right (precision) | header-field accuracy | invoices fully right |
| --- | --- | --- | --- | --- |
| 300 digital PDFs (seed 9101, tuning) | 98.5% → **100.0%** | 99.95% → **100.0%** | 99.97% → 99.97% | 99.7% → 99.7% |
| 300 digital PDFs (seed 9102, tuning) | 97.2% → **100.0%** | 99.8% → **100.0%** | 99.9% → 99.9% | 99.0% → 99.0% |
| 40 scans (seed 9201, tuning) | 93.0% → **99.0%** | 97.2% → **99.3%** | 99.7% → 99.7% | 97.5% → 97.5% |
| 40 scans (seed 9202, tuning) | 94.8% → **99.7%** | 100.0% → 99.7% | 100.0% → 100.0% | 97.5% → 97.5% |
| 300 digital PDFs (seed 9301, fresh) | 97.9% → **100.0%** | 99.8% → **100.0%** | 99.93% → 99.93% | 99.0% → 99.0% |
| 40 scans (seed 9401, fresh) | 96.3% → **100.0%** | 100.0% → 100.0% | 100.0% → 100.0% | 100% → 100% |

What was missed, by cause: bilingual headings ("Price / Taux Ext. Price / Prix total", "Product / Service /
Désignation") left whole tables unread, 31 of the 32 lines missed on seed 9101; an address's "Unit 9" beside "Amount
Due" taken for the heading row of a utility bill; on scans, amounts OCR reads "342.18CR" or "1530:00" dropped as
sentences, and headings OCR runs together. No header field read differently; on scans the subtotal is *verified* more
often, since the lines now add up to it. With the table finder the lines found went from 99.95% to 100% (digital, both
seeds 9101 and 9301; precision 99.8% to 100%) and from 94.0% to 99.0% (scans, seed 9201; 99.7% to 100% on seed 9401).

The quantity and unit price of the lines found are now as printed on every digital line (before, about 1 line in 40
had one missing or wrong), but for a credit note's unit price, which is given with the credit's sign (-45.13 for a
line of -180.52), as before. Still missed on scans: a description OCR misreads beyond recognition ("Safety gasss,clear,
NSI7"; the line is read, with its amount, but the benchmark matches descriptions), an amount OCR doesn't see at the foot
of a page, and a quantity of 1 OCR doesn't see (left blank, read as 1 when the line is posted).

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

## The Azure check: an optional second opinion

`ap_coder/ai/azure_check.py`, Settings → Reading → *Azure check*. **Off by default**, never needed: AP Coder reads every
invoice on this computer. Turned on, with a Document Intelligence endpoint and key, a background thread of the
dashboard sends some invoices to Azure (`prebuilt-invoice`, read with `extraction.DocumentExtractor`, its fields
with capture's DI reader) and compares Azure's fields with the values AP Coder settled on:

- **low-confidence**: every invoice with a header field read but not *verified*, a field needed to go touchless not
  found, or a failed check;
- **spot-check**: a share of the others (5% by default), picked by the file's content like the touchless audit
  sample (salted, so not the same invoices);
- at most the **daily cap** of pages (200 by default) a day; the rest wait for tomorrow.

Invoices are put in line when they are processed (`azure_checks`), and checked after OvisOCR2 has read them when it
is running; a low-confidence invoice that OvisOCR2's reading made fully verified becomes a spot-check candidate.
Processing never waits for Azure. Agreement is recorded ("Azure agrees on 11 of 11 fields"). A field Azure reads
differently is marked *Check* ("Azure Document Intelligence read X"), the invoice gets the review issue
`AZURE_DI_DISAGREES` and can't go touchless; the check is folded in again whenever the page reader reads the invoice
again. An invoice picked for a spot-check is held from touchless approval until Azure has checked it, and approved
then if Azure agrees, nobody touched it and touchless processing still applies (the switch on, the vendor still
touchless, the total within the limits: `workflow.still_touchless`). Azure's reading becomes reader `di` among each field's sources, so
approvals score it on *Learning → Readers* (an invoice already approved is scored at once).

Only the configured endpoint's host is contacted, over HTTPS: a request hook on the Azure client refuses any other
host (polling and redirects included). `AP_ALLOW_INTERNET` stays off. The key is kept in the .env
(`AZURE_DOCUMENT_INTELLIGENCE_KEY`), shown only as its last four characters, and scrubbed from errors. Without the
optional `azure` extra, an endpoint or a key, nothing is queued or sent, and the card says why. *Test connection*
reads the made-up sample invoice and shows the comparison; like the check itself, it sends nothing until the check
is switched on.

## Review screen

The invoice page with a box over every field, coloured by status (green verified, blue likely, amber
check, red failed check). Click a field in the panel to jump to it on the page. To teach, click a
field and then the words on the page that hold it. Each confirmation trains the supplier's template.

It is a page of the local web dashboard, `/review/<id>` (`ap_coder/web/pages/invoice.py`): each page of the
invoice is served as a picture (`/review/<id>/page/<n>.png`, drawn once and cached, `ap_coder/web/document.py`)
with its printed size, and `static/js/invoice_viewer.js` draws the boxes over it from the fields' fractions of the
page. The words to teach from come from `/review/<id>/words`. Every edit is checked as it is made
(`POST /review/<id>/check`: the checks, the posting preview and the approve bar are drawn again) and kept as a
draft until the invoice is approved; while OvisOCR2 has the invoice in its queue the screen follows its progress
(`/review/<id>/reader`) and shows its reading when it arrives, keeping the reviewer's edits.

## JDE EnterpriseOne

Approved invoices export as F0411Z1 (voucher) and F0911Z1 (G/L distribution) batch rows for the
R04110Z voucher batch processor: supplier address number, company, BU.Object.Sub accounts, Julian
dates and tax explanation codes, all mapped in settings. A direct connection (AIS/Orchestrator)
comes later and uses the same mapping.
