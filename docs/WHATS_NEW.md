# What's new

Everything below was added after the first version (pull request #1). The fastest way to see it all is
the demo: start AP Coder and click **Load demo invoices** (no Azure needed). The
[Getting started guide](GETTING_STARTED.md#step-4-try-the-dashboard-with-the-sample-data-no-enterprise-data)
has a short tour, and the [pilot plan](PILOT_PLAN.md) suggests four weeks to a decision.

Your existing data is safe. On first start the database upgrades itself (schema version 15), and a
backup of the database is made automatically once a day when AP Coder starts (the newest 14 are kept).

## This release: the dashboard is a local web app

**For everyone**

- **The dashboard is AP Coder's own web app now**, hosted the same way as CloseDesk: a small server on this
  computer (`http://127.0.0.1:8501`) that opens in your usual browser. Every page is there, in the same places:
  Process, the Review queue and each invoice's review screen, Find an invoice, Exports, Month-end, Sales tax,
  Vendor statements, Vendors, Purchase orders, GL accounts & tax, Learning & accuracy, Spend, Insights,
  Activity, Settings (with the optional Azure check card) and Help. Pages open faster, each has its own address
  (bookmark a page, or open an invoice in a second tab), and nothing is loaded from the internet.
- **Updates take over by themselves.** Double-clicking a new version stops the running dashboard cleanly (an
  older AP Coder 0.1 dashboard too), moves your data if it has to, and opens the new one.
- The Streamlit dashboard and the public web demo built on it are retired: AP Coder no longer installs
  Streamlit.
- **Better GL coding with no chat model.** A line is matched with what AP approved before even when it is worded
  differently (shortened words, plurals, word order, French or English), other vendors' lines for the same item
  count when they agree, a vendor's usual account is learned, and the latest decision wins when AP changes how it
  codes something. An account is filled in only when the evidence is strong; a weak match (an account's name,
  the vendor master's default) is shown under *Suggested GL accounts* instead. On the new benchmark more lines
  get an account and 99.5% of them are right (95-98% before). Coding stays fast with years of ERP history.
  Details: [CODING.md](CODING.md).
- **The math is proven on every invoice.** Subtotal (or, when none is printed, the lines) plus charges and taxes
  must make the total, and the tax rates are checked; approval without a person now also needs the totals proven.
- **Qwen checks the page too.** When the chat model in LM Studio can see pages (Qwen 3.5), it reads each
  invoice's header from the page image in the page reader's pass, as one more independent reader: a field it
  reads differently goes to a person. Still offline, once per file, in the background.
- **More invoice lines found** (100% on the benchmark's new digital invoices and scans, from 96-98%): column
  headings in two languages or run together, amounts as OCR reads them, and where the table ends.
- **More invoices go through without a person** once their supplier has earned it: on the benchmark, 86% of
  those suppliers' later invoices instead of 66%, still with no wrong field. Nothing changes about who qualifies.
- **Scans and PDFs read better**: a partnership's name ("... LP"), "ltée", labels in header grids (also two
  bilingual labels printed as one line), specks OCR reads as periods, "Payable on receipt", French "1er octobre",
  "PO -20203" and "GST/HST @58" as OCR prints them, and a name whose accented first letter one OCR engine missed;
  also a product called "Shipping cartons" (no longer taken for the freight), "2%10,.Net45", "Net·30", OCR's 号 for
  a percent sign, a street number after a logo's initials "PO", and a date that reads both ways no longer taken as
  dated after the day it arrived.
- **Phone photos of an invoice read like scans.** A photo of a page lying on a desk is flattened the way a scanner
  app does it (the page cut out and squared up, its tilt and the angle it was taken at undone) when it comes in; the
  photo as taken is kept in `originals/`. On a stress test, every value of 36 photographed invoices was read right
  (91% of the values before, with some wrong ones shown as likely).
- **Pages scanned sideways or upside down are read upright and shown upright.** Before, almost nothing on such a
  page was read right (about a fifth of the values), and some wrong totals were shown as verified; now 98% are.
  The scan as it came is kept in `originals/`.
- **A page scanned too coarsely is checked by a person.** Below about 140 dpi (an old fax at 100 dpi), nothing is
  shown as verified or likely: every value is read and waits for a person to confirm it. A fax at 200 dpi reads
  like a scan.
- **Faster**: a digital PDF is read, checked and coded in about half the time, a scan in about 7 s instead of
  11 s and again in about 2 s instead of 8 s when the page reader's text arrives (its OCR is kept in the data
  folder), and coding stays quick with years of ERP history (0.13 s instead of 3.2 s with 30,000 past lines).
- **One file to open per computer:** `APProcessor.bat` on Windows, `APProcessor.command` on a Mac. The old
  `install.bat`, `install.sh`, `start.bat` and `start.sh` are gone, and so is the top-level `terminal.bat`.
  An update removes them from an older folder by itself.

**For IT**

- `python -m ap_coder serve` runs the dashboard (`dashboard` is the same command); `GET /healthz` says which AP
  Coder answers, and the launcher stops it with `POST /shutdown` and a token kept in the data folder.
- The installer's questions (data folder, Azure settings, shortcut) are `APProcessor.bat --installer` (Mac:
  `./APProcessor.command --installer`), with the same options as before (`--yes`, `--fresh-start`, ...). The
  command prompt for AP Coder's commands is `scripts\terminal.bat`.

## Earlier: pictures become PDFs, and line items read with a table finder

**For AP clerks**

- **Every invoice is a PDF.** A photo or a scanned picture (JPEG, PNG, iPhone HEIC, TIFF with every page,
  BMP) that is uploaded, dropped in the invoices folder or attached to an email is made into a PDF before it
  is read: turned upright, at the resolution it was taken, a JPEG kept as it is. The invoice is stored, shown
  and stamped as that PDF; the picture is kept as it came in, in an `originals` folder beside it (the review
  screen says so). The same photo sent twice is still found as the same invoice.
- **Line items from the table on the page.** A small layout model, the *table finder*, finds the line
  table on each page (scans, photos and digital PDFs alike), and the line items are read from the words
  inside it: a note or a remittance box printed beside the rows no longer runs into the descriptions. A
  tilted scan's rows are straightened before they are read, and a table whose column headings OCR can't
  read is read by its amount column. On the benchmark, line items read right (amount and description) went
  from 29% to 98% on scans and from 98.2% to 99.7% on digital PDFs, with header fields read exactly as well as
  before ([details](CAPTURE_DESIGN.md#pictures-become-pdfs-and-line-items-read-with-the-table-finder)).
- **Line items checked by two readers** (`LINE_ITEMS_AGREE`). Once OvisOCR2 has read the invoice, its
  reading of the table is compared row by row with the page's (amount, quantity and description). Lines
  both read the same are marked so; when they differ, the invoice says which line and goes to a person.
  The review screen says where each line came from.

**For IT**

- The table finder's model (`yolov8n_layout_general6.onnx`, 12 MB, Apache-2.0) comes in the offline
  bundle's `models/` folder with its SHA-256, like the OCR models; AP Coder never downloads it. Without it,
  line items are read as before and *Settings → Reading* shows the *Table finder* row as missing.

## Earlier: one way to read every invoice, and touchless processing

**For AP clerks**

- **Every invoice is read the same way.** A digital PDF, a scan or a phone photo (iPhone HEIC too): the
  PDF's own text or OCR, then OvisOCR2 reads every page again as a second reader. A field the readers agree
  on is *verified*; one they read differently is marked *Check*.
- **A new check: hidden text differs from the page** (`TEXT_LAYER_MATCHES_PAGE`). On a digital PDF, the
  text a computer reads is compared with what is printed. An edited PDF that says another total than its
  page shows goes to a person.
- **No invoice is approved without a person before OvisOCR2 has read it.** A banner on the Process and
  Review pages says when OvisOCR2 isn't running or is still testing itself.
- **Approve & teach says what it learned**, e.g. *Learned from your 2 corrections (invoice number, due
  date). Acme Ltd: 14 invoices reviewed, about 6 more clean invoices to go touchless.* Each corrected
  field is recorded for that vendor and updates its template.

**For AP leads**

- **Touchless processing: one switch** in the new *Settings → Automation* tab, off by default, turned on
  with a confirmation (recorded in the Activity log). With it on, a vendor goes touchless by itself once it
  meets one fixed bar: at least 20 reviewed invoices, header fields at least 99% right with 95%
  confidence over its last 200, and the last 10 without a correction (usually about 30 clean invoices).
  5% of touchless invoices are still audited; one correction, or a touchless invoice reopened, suspends
  the vendor until it has a fresh clean streak.
- **Always a person:** a changed bank account or GST/HST number, any sign of a duplicate, an unusual
  amount, a vendor not in the vendor master or on hold, a credit note, a total above *Largest invoice
  approved without a person* (5,000.00 CAD by default) or above the second-approval limit, and an invoice
  OvisOCR2 has not read yet.
- **Learning & accuracy → Supplier learning** is now the training view, vendor by vendor: what was
  corrected, how far each vendor is from touchless, and *Keep supervised* for a vendor that should always
  go to a person. Bulk approvals nobody opened don't count toward the bar.
- *Settings → Automation* shows the touchless rate of the last 30 days and the errors found in touchless
  invoices. Our advice: leave it off for the first weeks of the pilot and watch *Supplier learning*.

**For IT**

- **Nothing to configure.** IT installs LM Studio with OvisOCR2 (and, optionally, the chat model) and
  starts its server. AP Coder finds OvisOCR2 on its own and runs its self-test by itself (10 to 20 minutes
  on a laptop the first time). There is no *Test the page reader* step any more; *Run the self-test again*
  is in *Settings → Reading*.
- **Settings tabs are now** Reading, Automation, Review, JD Edwards E1, Data & backups and About. *Settings
  → Reading* replaces the *AI model* and *Page reader* tabs: how every invoice is read, each reader's
  state, OvisOCR2's self-test and queue, and the models in LM Studio with **Load**.
- **Settings removed:** `AP_PAGE_READER`, `AP_PAGE_READER_SCOPE`, `AP_LLM_PROVIDER`, `AP_VISION`,
  `AP_LLM_VISION` and the model pickers. An older `.env` that still has them does no harm.
- **Azure is for developers only** (`AP_ALLOW_INTERNET=1`). The offline installer asks no cloud questions.
- **Any Python 3.11 or newer.** APProcessor.bat and APProcessor.command no longer stop at 3.13: a Mac with
  only Python 3.14 (as `python3` or `python`) is used. The offline bundle's versions still come first (3.12,
  3.11, 3.13, 3.14). A Python that is too old is named (*Python 3.10 is installed, but AP Coder needs Python
  3.11 or newer*) instead of *not installed*; a Python the bundle was not built for gets the command that
  builds one for it.
- The database upgrades itself on first start; your data is kept.

## Highlights

- **New: the page reader, a second reader for every invoice.** A small page-reading model (OvisOCR2 in LM
  Studio, about 1 GB) reads every page of every invoice on its own in the background. Where it and the
  PDF's text or OCR agree, a field can be verified; where they differ, it is marked Check, and every figure
  on the page is compared between the two. It is found and self-tested on its own (*Settings → Reading*). Every approval
  now scores each reader (*Learning & accuracy → Readers*), tunes the confidence labels to your own
  invoices, and can be exported as training data. See [CAPTURE_DESIGN.md](CAPTURE_DESIGN.md#the-page-reader-a-vision-model-as-a-second-reader).
- **New: invoice capture with the highlighted page.** Every field boxed on the invoice in its
  confidence colour, teach-by-click, a measured confidence per field, and **supplier autonomy**:
  with touchless processing on, suppliers whose invoices AP has confirmed at 99%+ go touchless, with an
  audit sample. See
  [CAPTURE_DESIGN.md](CAPTURE_DESIGN.md) for how it works and the benchmark results.
- **New: JD Edwards E1 export** (F0411Z1/F0911Z1 Z-files), see [JDE_E1.md](JDE_E1.md).

- **Catch it before it is paid:** purchase-order matching, vendor master checks, duplicate and
  bank-account-change signals, credit notes matched to their invoice, second approval above a limit.
- **Less typing:** fixed coding rules, GL suggestions, bulk approval, *Ask the vendor* emails written
  for you, invoices taken straight out of saved emails.
- **The rest of the cycle:** exports in your ERP's layout with approved PDFs, vendor statements,
  month-end accruals, the sales-tax claim (ITCs/ITRs) and PST/QST self-assessment.
- **Know where you stand:** *Find an invoice*, the *Today* line, spend analysis, the duplicate
  payment audit, the controls report and the business case.
- **A public demo on the web:** the dashboard with made-up invoices, nothing to install, for anyone
  with the link (retired with the Streamlit dashboard: load the demo invoices on your own dashboard instead).

## Safety

Text from invoices and uploaded files can no longer run as a formula when a download is opened in
Excel, cannot turn into a link on screen, and an uploaded file cannot be saved outside the invoices
folder.

## Review faster

- **Every check explains itself.** Each finding on the review screen says what to do about it, and
  the new **Help** page lists every check, in plain English, with a search.
- **GL suggestions.** Lines the AI could not code get up to three one-click GL accounts, from how
  similar lines were coded before (this vendor, then others) and from your account descriptions.
- **Bulk approval.** *Approve N clean…* approves, in one go, the invoices with no error, no warning
  and enough confidence. Each one is re-checked at that moment; anything no longer clean stays for a
  person.
- **Ask the vendor.** When an invoice needs something from the vendor (a missing GST/HST number,
  amounts that don't add up, a price above the PO, proof of delivery), the review screen drafts the
  email in English or French, ready to copy or open in your email program. Internal fraud checks
  are never mentioned.
- **Split a line** across GL accounts or cost centers by percentage (shared costs).
- **Reopen for correction.** An approved invoice not exported yet (or one rejected by mistake) goes
  back to the queue with its coding kept; what it taught is withdrawn until it is approved again.
- **Park and notes.** *More → Park* sets aside an invoice waiting for information, with a follow-up
  date (the **Parked** tab); **Notes** on each invoice keep the team informed.
- **Fixed coding rules.** GL accounts & tax → *Fixed rules*: a vendor, words a line contains, or
  both → a GL account (and cost center), whatever the AI says. The review screen shows which lines a
  rule changed; vendors always coded to one account are suggested as rules.
- **Teach from past coding.** Import last year's posted AP lines from the ERP (Learning page) so the AI
  knows how each vendor is coded from the first invoice. Past lines never count in the accuracy
  figures.
- **Due dates.** The AI now reads payment terms and printed due dates (English and French). Queue
  cards show *Overdue*, *Due in 3d* and early-payment discount deadlines (*2% off until Oct 15*), and
  the queue can be sorted by due date.
- **Find an invoice** (for a vendor on the phone): one search box for vendor, invoice or PO number
  (however it is written) and amount; each match says where the invoice is and when it is due.
- **Today** line above the queue: past due, due soon, parked to follow up, waiting for your second
  approval, ready to export, regular invoices that are late.
- **Stays fast** with a year of invoices: tested with 3,000. The queue shows 50 at a time.

## Catch more before it is paid

- **Credit notes matched.** The AI reads which invoice a credit note credits; the review screen
  names that invoice (in AP Coder or the ERP register), or says it was not found, and warns when the
  credit is larger than the invoice.
- **Purchase orders (2- and 3-way match).** Import open POs from the ERP (CSV or Excel, columns
  recognised automatically). An invoice quoting a PO is matched line by line: price above the PO,
  more billed than ordered or received (counting earlier invoices and credit notes), lines not on
  the PO, PO total exceeded, closed POs, POs for another vendor. *Use the PO's coding* applies the
  PO's GL accounts and cost centers in one click.
- **Vendor master from the ERP.** Import the vendor list (CSV or Excel): exports carry each vendor's
  ERP ID, an invoice from a vendor that is not in the list is flagged (by name or GST/HST number),
  the vendor's payment terms set the due date when the invoice shows none, and its default GL
  account is suggested for uncoded lines.
- **Vendor fraud and duplicate signals:** vendor on hold, changed GST/HST number (a classic fake
  invoice sign), a different bank account to pay into than on the vendor's earlier invoices (the most
  common payment fraud; only the last 4 digits are ever shown), amount far above the vendor's usual,
  same amount under a new invoice number, the same bill under another vendor name, a bill already in
  the ERP (import the ERP's invoice list on the Exports page), first invoice from a vendor. The
  **Vendors** page holds the controls (hold, expected GST/HST number, notes).
- **Second approval.** Above an approval limit (Settings → Review), an approved invoice waits for a
  second person (another name and another computer login) before it can be exported, or is sent
  back to the queue with the first approver's corrections kept. For two people to approve, they use
  the same AP Coder, e.g. on a shared AP PC, each signed in to Windows as themselves (the database
  must stay on a local disk, not a network share; a shared server version is a Phase 2 item). See
  [Two people approving](GETTING_STARTED.md#two-people-approving-second-approval) to set it up.

## The rest of the AP cycle

- **Exports:** approved invoices go to the ERP in batches, as an Excel workbook (invoices, GL lines,
  totals by GL), a CSV of GL lines, or a **custom CSV** laid out the way your ERP's import expects
  (columns, headers, fixed text, date format, separators). Each invoice goes out once; a batch can be
  downloaded again or undone if the import failed. **Approved PDFs**: each invoice with an APPROVED
  stamp (who, when, batch) and a coding page, to attach in the ERP: per invoice, or a ZIP per batch.
- **Vendor statements:** upload a vendor's statement of account and see what matches, what differs,
  what you never received and what is not on their statement, with the email asking for copies of
  the missing invoices ready to send.
- **Month-end:** the accruals schedule: goods received but not invoiced (from POs), invoices not in
  the ERP yet, and regular bills that have not arrived; totals by GL; CSV.
- **Sales tax:** the GST/HST input tax credits and QST input tax refunds to claim for a period
  (by invoice date, credit notes deducted), by tax and rate, and the claims to check before filing:
  an invoice of $30 or more without a valid registration number, tax in a foreign currency. CSV.
  Also the **PST / QST possibly to self-assess**: approved invoices for a supply in BC, SK, MB or
  Quebec where the vendor charged none, with an estimate at the official rate.
- **Vendors that make work** (Vendors page): how often each vendor's invoices arrive with something
  only the vendor can fix, the usual problems, and how often AP corrects the coding.
- **Recurring vendors:** the Vendors page lists who bills monthly, quarterly..., their next expected
  invoice, and which are late.
- **Folder watcher:** `python -m ap_coder watch` processes new files dropped in the invoices folder
  (from a scanner, a mail rule or Windows Task Scheduler with `--once`).
- **Invoices by email:** drop a saved email (`.eml`: Outlook on the web or new Outlook → *Download*)
  in the invoices folder, or upload it: its PDF and image attachments are processed (signature logos
  left out) and the email is kept in `invoices/emails`.

## Know what happened

- **Spend:** where the money goes, by month (coloured by GL category), GL account, vendor and cost
  center, net of recoverable tax. *All invoice data (Excel)* gives every invoice, line and GL posting as
  Excel tables for pivot tables or Power BI.
- **Activity:** an audit trail of everything that changes data, with every change a reviewer made
  to the AI's coding; filterable and downloadable.
- **Duplicate payment audit** (Activity page): bills that may have been approved or posted twice,
  across AP Coder and the ERP invoice register: invoice numbers one keystroke apart, the same bill
  under two vendor names, the same amount days apart. CSV.
- **Controls report** (on the Activity page) for internal audit: approvals that overrode an error,
  approvals made while a fraud or PO signal was showing, second approvals, setup changes, approvals
  per person; one HTML page to download.
- **Insights:** how much goes straight through, hours saved, Azure cost per invoice from the real
  token usage, a monthly projection and the duplicate invoices stopped, with a one-page business case
  to share (totals only); AP operations: queue ageing, days to approve, approvals after the due date,
  discounts approved in time.

## Setup and safety

- **Settings page:** how invoices are read, review behaviour, approval limit, default
  payment days, backups (make, download, restore, and a **second backup folder** such as OneDrive so
  a lost computer does not lose the database), **exchange rates to CAD** for estimates (Spend can show
  every currency in CAD; Sales tax estimates foreign-currency claims). Your name is kept per Windows user.
- **Your own tax-rate table:** a `canada_tax_rates.csv` in the data folder overrides the shipped
  rates and survives updates.
- **Demo mode** with sample invoices, purchase orders and a vendor list (plus a sample vendor
  statement in `data/` to upload); removing it leaves your own data untouched.
- **Ten sample invoices** with their correct answers: every Canadian tax regime (HST, GST+PST,
  TPS/TVQ, GST only), a two-page French invoice, a US invoice and a credit note.
- **CI:** every change is tested on Windows and Linux, including the real installer on Windows.
- **Offline setup fixes:** the installer no longer writes the example Azure endpoints into your `.env`,
  and an `.env` that still has them (`<your-resource>`) is read as "no Azure", so nothing tries to reach
  Azure. The doctor checks every package the app needs (without `pymupdf` no PDF can be read), says to
  run `APProcessor.bat` (the offline bundle) instead of `pip install`, shows the Python range the launcher
  uses (3.11 to 3.13), and skips the local model when AI coding is turned off. `terminal.bat` (now
  `scripts\terminal.bat`) still works after the AP Coder folder is moved. The setup steps now say that IT installs LM Studio and its models.
