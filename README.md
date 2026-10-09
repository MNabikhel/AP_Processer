# AP Invoice Coder (prototype)

## Run the offline pilot

Everything runs on one laptop: no Azure, no cloud, nothing sent out.

1. **Install Python 3.11+** from [python.org](https://www.python.org/downloads/) (tick *Add python.exe to PATH*).
2. **Unzip AP Coder** to a folder that OneDrive does not sync, and double-click **`APProcessor.bat`**
   (Mac: `APProcessor.command`). The first run sets itself up in a few minutes, including the OCR models
   for scans, then opens the dashboard. An air-gapped laptop installs from the offline bundle instead.
3. **Optional local AI:** in [LM Studio](https://lmstudio.ai/), load a 3B–8B instruct model and start the
   server (Developer tab → **Start server**). AP Coder finds it on its own (**Settings → AI model**).

Without a model, every invoice is still read on the laptop (PDF text, or OCR for scans), checked, and each
line is coded from what AP approved before for that vendor. With a model, it also proposes accounts for
vendors it hasn't seen. The full pilot guide is [docs/PILOT.md](docs/PILOT.md).

## Try the live demo

[![Open the live demo](https://static.streamlit.io/badges/streamlit_badge_black_white.svg)](https://ap-coder-demo.streamlit.app)

**[ap-coder-demo.streamlit.app](https://ap-coder-demo.streamlit.app)** opens the review dashboard in
your browser with ten made-up invoices: no company data, no Azure, nothing to install. Review, correct
and approve invoices, then look at Insights, Sales tax and Activity. *Reset demo* starts it over.

![Review queue with today's numbers](docs/screenshots/review-queue.png)

| | |
|---|---|
| ![An invoice in review: checks, GL split and the approve bar](docs/screenshots/invoice-review.png) | ![Insights: the business case at 500 invoices a month](docs/screenshots/insights.png) |
| ![Sales tax: GST/HST and QST to claim back](docs/screenshots/sales-tax.png) | ![Activity: the duplicate payment audit and the audit trail](docs/screenshots/activity.png) |

To run the same demo on your computer: `pip install -r requirements.txt`, then
`streamlit run streamlit_app.py`. How the hosted demo is set up: [docs/DEMO.md](docs/DEMO.md).

## What it does

An Accounts Payable invoice coding prototype on Azure, built for Canadian AP:

1. **Extraction:** Azure AI Document Intelligence (`prebuilt-layout` or `prebuilt-invoice`) turns
   a PDF, TIFF or image invoice into reading-order Markdown, keeping multi-page tables intact.
2. **Coding:** Azure OpenAI (`gpt-4o`, `gpt-4o-mini` or any newer deployment) reads the
   Markdown and returns structured output, enforced by **Structured Outputs (`strict: true`)**:
   - the invoice header and every line item
   - every sales tax charged (**GST, HST, PST, QST**)
   - a GL account and optional cost center for each line
3. **Controls:** deterministic checks, done in code rather than by the AI:
   - totals reconcile
   - tax amount = taxable amount × rate, at the official rate for the province and date
   - the right tax regime for the province
   - QST is charged on the pre-GST amount
   - supplier registration numbers are present
   - possible duplicate invoices (also under another vendor name, or already in the ERP's invoice
     register), and vendor fraud signals (vendor not in the ERP's vendor master, on hold, changed
     GST/HST number or bank account, unusual amount, same amount under another number)
   - credit notes: the invoice they credit is found, and a credit larger than it is flagged
   - purchase order match: price, quantity ordered and received, lines not on the PO, PO total
   - payment terms and due dates, early-payment discounts
   - agreement with past reviewer decisions, and fixed coding rules set by AP
4. **GL distribution:** posting lines that add up to the grand total:
   - recoverable GST/HST and QST go to their own receivable accounts
   - non-recoverable PST is added pro rata to the expense lines it applies to
5. **Review dashboard and learning:** a local web app where AP reviews and approves each invoice.
   Every approved line is remembered as *confirmed* or *corrected* and shown to the AI on the next
   invoice from that vendor. Accuracy is tracked against the 90% target.
6. **The rest of the AP cycle, locally:** invoices taken out of saved emails, *Find an invoice* for
   vendor calls, emails to vendors drafted for you, second approval above a limit, export batches
   for the ERP with approved (stamped) PDFs, vendor statement reconciliation, month-end accruals, the
   sales-tax claim and PST/QST self-assessment, spend analysis, a duplicate payment audit, an audit
   trail with a controls report, and a business case from the pilot's own numbers.

> **Just want to look around?** Install, start the dashboard and click *Load demo invoices*: ten
> sample invoices from across Canada, sample purchase orders and a sample vendor list, with a few
> realistic AI mistakes to correct (a sample vendor statement to upload is in `data/`). No Azure needed.

```
invoice ─► Document Intelligence ─► Markdown + tables + OCR confidence
                                              │
GL accounts, cost centers, tax rates, policy ─┤   past approvals for this vendor
                                              ▼   (learning memory) ──────────┐
                              Azure OpenAI, strict JSON schema ◄──────────────┘
                              (only your GL codes allowed; tax lines per type)
                                              │
                                              ▼
                     Checks: totals · tax math · province rates · QST base · registration #s
                             duplicates · history conflicts → confidence + review flag
                                              │
                                              ▼
                     GL distribution (tax GLs, PST into expense lines) ─► Review dashboard
                                                                               │ approve
                                                                               ▼
                                                     learning memory + accuracy tracking
```

> **Running this on real data?** Follow [docs/GETTING_STARTED.md](docs/GETTING_STARTED.md), then the
> four-week [pilot plan](docs/PILOT_PLAN.md). Everything
> stays in a local data folder outside the code (default `~/APCoder`), and the dashboard only listens on
> `localhost`. Only redacted reports (`doctor`, `share-report`) are meant to leave your machine.

## Quick start

**Windows:** unzip, double-click **`APProcessor.bat`**. **Mac:** double-click `APProcessor.command`
(Linux: `./APProcessor.command`). The first run sets itself up (a few minutes, Python 3.11+ needed), then
the dashboard opens in your browser; later runs start straight away. It works offline, and with a
`wheelhouse/` folder (the offline bundle) it even installs offline: see the pilot guide,
[docs/PILOT.md](docs/PILOT.md).

The full installer is still there: `install.bat` / `./install.sh` also updates a git checkout, asks for
Azure settings and adds a desktop shortcut; `start.bat` / `./start.sh` do the same as `APProcessor`. It is
safe to run again (see [GETTING_STARTED](docs/GETTING_STARTED.md#step-2-install-10-min-one-double-click)).

By hand:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env              # endpoints/keys, or leave keys empty to use Entra ID (az login)

python -m ap_coder doctor --online   # check configuration and Azure connectivity
python -m ap_coder dashboard         # review app on http://127.0.0.1:8501 (or the next free port)
```

In the dashboard:
1. **GL accounts & tax** → *Load sample setup*, or import your own accounts (or click *Load demo
   invoices* to skip ahead).
2. **Process invoices** → upload the PDFs in `samples/`.
3. **Review queue** → review and approve. **Help** explains every check.

### Run offline with LM Studio

The coding step can use a model on your own computer instead of Azure OpenAI. Nothing is sent out.

1. Install [LM Studio](https://lmstudio.ai/).
2. Download and load a small *instruct* model: Qwen 2.5 7B Instruct, Qwen 3 8B, Llama 3.1 8B, or any
   3B–8B instruct model. One that can see (an eye icon in LM Studio, e.g. Qwen 2.5 VL 7B) is also shown
   the page images.
3. **Developer** tab → **Start server**.

AP Coder finds it on its own: `AP_LLM_PROVIDER=auto` (the default) uses Azure OpenAI when its endpoint
is set, else the model loaded in LM Studio when the server answers, else no AI. **Settings → AI model**
shows what it found (*Connected to LM Studio · qwen2.5-7b-instruct · can see pages: no*), has a
**Test connection** button, and picks the server address and model. `python -m ap_coder doctor --online`
codes one made-up invoice with it.

| Setting | Default | |
| --- | --- | --- |
| `AP_LLM_PROVIDER` | `auto` | `local`, `azure` or `off` to choose |
| `AP_LLM_BASE_URL` | `http://127.0.0.1:1234/v1` | any address LM Studio shows; Ollama: `http://127.0.0.1:11434/v1` |
| `AP_LLM_MODEL` | empty | empty = the chat model loaded in LM Studio (never an embedding model) |
| `AP_LLM_VISION` | `auto` | `on` / `off`: show the model page images |
| `AP_LLM_MAX_PROMPT_CHARS` | `24000` | invoice text sent; a longer one keeps its start and end |
| `AP_LLM_TIMEOUT_SECONDS`, `AP_LLM_MAX_TOKENS` | `600`, `4096` | a laptop without a graphics card is slow |

The model is asked for the same strict JSON schema as Azure (LM Studio supports it). A server that
refuses it gets plain JSON mode, then the schema in the prompt; replies wrapped in code fences or
`<think>` blocks are still read, and an invalid answer gets one repair turn. Every check after coding is
the same as with Azure. With no model at all, the rest of AP Coder still works; invoices just aren't
coded by AI. Reading the invoice itself (Document Intelligence, or reading text files) is a separate step.

## Dashboard

| Section | Page | What it does |
|---|---|---|
| Work | **Review queue** | Invoice image beside the editable header (incl. PO #, payment terms, due date, bank account to pay into); live **Checks**, each with what to do; the line grid (GL account and cost center dropdowns of your codes); tax lines with a tax-check table; the **PO match**; **GL suggestions** for lines the AI could not code; *Split a line* across GL accounts / cost centers; a GL posting preview; **Ask the vendor** drafts the email for what the invoice is missing (English or French). *Approve & teach*, *Reject*, *Delete*. The queue shows due dates and discount deadlines, can be sorted by due date, and clean invoices can be approved in bulk. A **Second approval** tab holds invoices over the approval limit; *Park* sets aside an invoice waiting for information (**Parked** tab); **Notes** keep the team informed. |
| Work | **Find an invoice** | For a vendor on the phone: search by vendor, invoice or PO number, or amount; see where each invoice is (in review, parked and why, approved by whom, exported in which batch, rejected) and when it is due; the ERP register too. |
| Work | **Process invoices** | Upload files or saved emails (`.eml`, attachments taken out), or process new files dropped into the data folder's `invoices/` (or run `watch`). |
| Work | **Exports** | Approved invoices go to the ERP in batches (Excel, CSV, or a **custom CSV layout** matching your ERP's import); each invoice once; any batch can be downloaded again or undone; **approved PDFs** (stamp and coding page) per batch. Import the ERP's invoice register to catch bills already entered there. |
| Work | **Vendor statements** | Upload a vendor's statement of account: matched, amount differs, not received, not on the statement; the email asking for the missing invoices. |
| Work | **Month-end** | The accruals schedule: received not invoiced (from POs), invoices not in the ERP yet, expected recurring invoices; by GL; CSV. |
| Work | **Sales tax** | GST/HST (ITCs) and QST (ITRs) to claim back for a period, by tax and rate, with the claims to check before filing (no valid registration number on the invoice, foreign currency); PST / QST possibly to self-assess; CSV. |
| Insight | **Insights** | Straight-through rate, hours saved, Azure cost per invoice, a monthly projection; AP operations (queue ageing, days to approve, discounts approved in time); a one-page business case to download. |
| Insight | **Spend** | Spend by month (by GL category), top GL accounts, vendors and cost centers, net of recoverable tax; **all invoice data as Excel** (invoices, lines, GL posting) for pivot tables or Power BI. |
| Insight | **Learning & accuracy** | AI accuracy against the 90% target, weekly trend, per-vendor accuracy, most common corrections, and the memory itself (*Forget* a bad lesson). *Teach from past coding* imports last year's AP lines from the ERP. |
| Insight | **Vendors** | Import the **vendor master** from the ERP (vendor IDs in exports, unknown vendors flagged, vendor terms, default GL); spend, AI accuracy and controls per vendor (hold, expected GST/HST number, notes); recurring vendors and late invoices. |
| Insight | **Activity** | The audit trail (who did what, with every change to the AI's coding), filterable, CSV; the **controls report** for internal audit; the **duplicate payment audit** (number typos, same bill under two vendor names, same amount days apart, also against the ERP register). |
| Setup | **GL accounts & tax** | Import GL accounts (cost codes) from CSV/Excel by choosing the **code**, **description** and **category** columns; edit, categorise, delete. Optional cost centers. Tax treatments and GLs. Coding policy. **Fixed rules** (vendor and/or words → GL account and cost center), with rules suggested from past coding. |
| Setup | **Purchase orders** | Import open POs (one row per line, received quantities optional); what has been invoiced against each; close, reopen, delete. |
| Setup | **Settings** | Azure connection (with a connection test), your name (per Windows user), review threshold, page images for the AI (vision), only-my-GL-codes, approval limit, default payment days, backups and restore (with an optional second backup folder, e.g. OneDrive). |
| Setup | **Help** | Quick start, every check explained, questions, shortcuts. |

## Canadian sales tax

Rates live in [`data/canada_tax_rates.csv`](data/canada_tax_rates.csv) with effective dates. For
example, Nova Scotia HST was 15% until 2025-03-31 and 14% from 2025-04-01. When a rate changes,
put a copy of that file in your data folder (`canada_tax_rates.csv`) and edit the copy: it is used
instead of the shipped one and survives updates.

| Check | Severity |
|---|---|
| Tax amount ≠ taxable amount × rate (±1¢ per line of vendor rounding) | error |
| Tax type not levied in that province (e.g. PST in Ontario) | error |
| QST calculated on the GST-inclusive amount | error |
| Tax lines don't add up to the tax total | error |
| Tax type charged but no GL mapped in Tax setup | error |
| Rate differs from the official rate for the province and invoice date | warning |
| Wrong regime for the place of supply (e.g. GST only in an HST province) | warning |
| PST/QST not charged where expected (self-assessment may be required) | warning |
| Lines marked as taxable don't add up to the taxable amount | warning |
| GST/HST or QST registration number missing or malformed (needed for ITC/ITR) | warning |

Posting treatment per tax type is configured in the dashboard:
- *Recoverable* posts to its own GL account. This is the default for GST, HST and QST.
- *Add to each expense line's GL* allocates the tax pro rata across the lines it applies to. This is
  the default for PST.
- *Separate expense GL* posts it to one account of your choice.

## Learning from reviewers

This is a memory of your team's decisions. No model is retrained.

- **On approval**, each line is stored with the AI's suggestion, the final coding, the reviewer,
  and whether the reviewer *confirmed* or *corrected* it.
- **On the next invoice**, the vendor is recognised in the document. That vendor's past decisions,
  plus similar lines from other vendors, are added to the prompt; corrections are marked as
  explicit overrides. A correction therefore takes effect immediately.
- **Reinforcement:** if the AI agrees with an established pattern for that vendor, the dashboard
  says so. If it disagrees, the line is flagged `HISTORY_CONFLICT`.
- **Accuracy** (lines confirmed ÷ lines reviewed) is tracked overall, weekly and per vendor.

## Commands

| Command | Purpose |
|---|---|
| `dashboard [--port]` | The review app (localhost only). |
| `demo [--remove]` | Load (or remove) the demo invoices and sample POs; no Azure needed. |
| `watch [folder] [--every 60] [--once]` | Keep processing new files dropped in the invoices folder (scanner, mail rule, Task Scheduler). |
| `doctor [--online]` | Setup, reference-data, tax-mapping and connectivity check. No secrets or URLs in the output. |
| `process <files/dirs…>` | Batch pipeline. Results go to `<data folder>/output` **and** the dashboard queue (`--no-db` to skip). Uses the learning memory. Skips files already processed (`--force` to redo). |
| `share-report [--include-codes]` | Redacted summary of the dashboard database (or an output folder): no vendor names, amounts, descriptions or file names. |
| `extract <files/dirs…>` | Document Intelligence only; writes `.extraction.md` and raw `.di.json`. |
| `labels` / `evaluate` | Spreadsheet-based ground truth and scoring, as an alternative to dashboard review. |
| `schema` | Prints the exact strict JSON Schema sent to Azure OpenAI. |

Reference data comes from the dashboard database once GL accounts are imported. For command-line
use without the dashboard, CSV files are looked up in this order: `AP_REFERENCE_DIR`, then
`<data folder>/reference/`, then the samples in `data/`. Individual files can be given with `--coa`,
`--cost-centers`, `--tax-mapping` and `--policy`; with the dashboard database, the last three apply on top
of it (`''` leaves one out).

**Data folder.** The database, invoices, outputs and the `.env` live in one folder: the
`AP_PRIVATE_DIR` environment variable if set, else the folder chosen in the installer (recorded in
`~/.ap_coder/settings.json`), else `private/` inside the project. The `.env` is read from the data
folder first, then the current folder, then the project folder.

CSV files may be saved as "CSV UTF-8" or plain "CSV" from Excel; both encodings are read.

## Output

Each invoice produces the original target fields plus the Canadian tax fields, and a computed
`gl_distribution` (abbreviated here; this is the BC sample):

```json
{
  "vendor_name": "Pacific Office Supply Ltd.",
  "invoice_number": "PO-77120",
  "invoice_date": "2026-10-05",
  "currency": "CAD",
  "supplier_province": "BC",
  "ship_to_province": "BC",
  "gst_hst_registration_number": "555666777 RT0001",
  "qst_registration_number": "",
  "subtotal": 2726.0,
  "tax_lines": [
    {"tax_type": "GST", "province": "", "rate": 0.05, "taxable_amount": 2726.0, "tax_amount": 136.3},
    {"tax_type": "PST", "province": "BC", "rate": 0.07, "taxable_amount": 2726.0, "tax_amount": 190.82}
  ],
  "tax_total": 327.12,
  "grand_total": 3053.12,
  "confidence_score": 0.93,
  "line_items": [
    {
      "line_number": 2,
      "description": "Dell 27\" monitor P2725H (G, P)",
      "quantity": 4, "unit_price": 329.0, "amount": 1316.0,
      "predicted_gl_code": "6010",
      "predicted_cost_center": "CC400",
      "taxes_applied": ["GST", "PST"],
      "reasoning_justification": "Monitors below the capitalisation threshold; GST 5% + BC PST 7%."
    }
  ],
  "gl_distribution": [
    {"kind": "expense", "line_number": 2, "gl_code": "6010", "cost_center": "CC400",
     "net_amount": 1316.0, "non_recoverable_tax": 92.12, "amount": 1408.12, "description": "Dell 27\" monitor P2725H (G, P)"},
    {"kind": "tax", "line_number": null, "gl_code": "2310", "cost_center": "",
     "net_amount": 0.0, "non_recoverable_tax": 0.0, "amount": 136.3, "description": "GST 5% (recoverable)"}
  ]
}
```

## Design decisions

- **Structured Outputs, natively enforced.** The schema (`ap_coder/schema.py`) follows strict-mode
  rules: closed objects, all properties required. A Pydantic mirror re-validates values (dates,
  provinces, tax types, rate ranges). If that fails, the model gets one repair round-trip.
- **Codes cannot be invented.** Your GL codes (excluding the tax accounts) and cost centers are
  embedded in the schema as `enum`s, plus an `UNASSIGNED` escape hatch. Very large charts fall
  back to free text plus local validation (`AP_CONSTRAIN_CODES`).
- **The AI reads; code does the arithmetic.** The model copies tax lines exactly as printed.
  Rates, regimes and amounts are verified in Python, and the GL distribution is computed in
  Python. An invoice whose posting does not equal the amount payable to the cent is flagged
  (`POSTING_UNBALANCED`) before it can be approved without an override.
- **Model-agnostic and ready for vision.** Capabilities are inferred from
  `AZURE_OPENAI_MODEL_NAME`:
  - reasoning models get `reasoning_effort` instead of `temperature`/`seed`
  - `--vision` sends page images to models that can read them
- **Prompt-cache friendly.** Instructions and reference data form a stable system prompt. History
  and the document go in the user message.
- **Cheap iteration.** Document Intelligence results are cached by file hash, so re-runs only pay
  for the LLM.
- **Enterprise auth.** Without keys, both services use Entra ID (`DefaultAzureCredential`). The
  SDK retries 429 and 5xx responses with backoff.

## Invoice capture and supplier autonomy

Next to the AI coder, AP Coder reads every invoice itself and shows *where* each value is printed
(design and benchmark: [docs/CAPTURE_DESIGN.md](docs/CAPTURE_DESIGN.md)):

- **Several readers per field:** the PDF's text layer or OCR (RapidOCR, local: `pip install -e .[ocr]`), a rule reader
  (labels in English and French, header grids, totals blocks), the supplier's learned template, and
  the AI's and Document Intelligence's answers located back on the page.
- **Checks a misread cannot pass:** subtotal + charges + taxes = total, official tax rates, GST/HST
  check digit, vendor master, due date = invoice date + terms.
- **A confidence per field, measured on a benchmark** of thousands of random invoices with known
  answers (`python -m ap_coder.bench run`), shown as *verified*, *likely*, *check* or *missing*.
- **The review screen** shows the invoice page with every field boxed in its status colour. Click
  a field to find it on the page; *Teach a field* lets AP click the words that hold a value, and
  the supplier's template learns it on approval.
- **Autonomy per supplier:** once a supplier's own confirmed invoices show at least 99% field
  accuracy (lower confidence bound, at least 20 invoices, the last 10 clean), a manager can switch it
  to touchless. Its invoices are then approved without a person only when every printed field is
  *verified* and every check passes; 5% are still audited, and one correction suspends it
  (*Learning & accuracy → Supplier learning*).
- **JD Edwards EnterpriseOne:** approved invoices export as F0411Z1/F0911Z1 Z-file batches for
  R04110ZA ([docs/JDE_E1.md](docs/JDE_E1.md)).

## Project layout

```
ap_coder/
  extraction.py      Document Intelligence → ExtractionResult (+ cache)
  inference.py       Azure OpenAI Structured Outputs, model profiles, repair loop
  local_llm.py       LM Studio / Ollama: finding the server and model, reading a small model's JSON
  prompts.py         system prompt (extraction, Canadian tax, GL coding, learning rules)
  schema.py          strict JSON Schema + Pydantic mirror
  tax.py             Canadian rates, tax checks, GL distribution
  memory.py          learning memory: example selection, history comparison
  store.py           local SQLite: accounts, tax setup, invoices, feedback, POs, vendors, exports, audit trail
  validation.py      deterministic controls, adjusted confidence, review flag
  vendors.py         vendor master, fraud and duplicate signals
  po.py              purchase orders: import, 2- and 3-way matching
  terms.py           payment terms, due dates, early-payment discounts
  suggest.py         GL suggestions for uncoded lines;  recurring.py: recurring vendors
  statements.py      vendor statement reconciliation;  accruals.py: month-end accruals
  exports.py · bulk.py · insights.py · controls.py · audit.py · help.py · demo.py
  capture/           invoice capture: layout (text/OCR), rule reader, locate, confidence + checks, supplier
                     templates and autonomy, calibration.json (measured confidence)
  bench/             random invoices with ground truth: accuracy benchmark, calibration, supplier simulation
  jde.py             JD Edwards E1 F0411Z1/F0911Z1 export
  pipeline.py        extract → code → validate → capture → store
  dashboard.py       Streamlit app shell;  webapp/: one module per page;  review.py: grid edits → InvoiceCoding
  doctor.py · share_report.py · labels.py · evaluation.py · reference_data.py · cli.py
data/                sample GL accounts, cost centers, tax rates and mapping, coding policy, POs, a statement
samples/             10 synthetic invoices (ON, QC, BC, AB, MB, NS, SK, US, a credit note) + ground truth
scripts/             sample invoice generator
docs/                GETTING_STARTED.md (step by step on enterprise data), WHATS_NEW.md, PILOT_PLAN.md,
                     DEMO.md (the public web demo), screenshots/
streamlit_app.py     the public web demo: the dashboard with made-up invoices (.streamlit/ and static/ go with it)
private/             git-ignored: default data folder when the installer is not used
tests/               offline test suite (Azure clients mocked)
```

Run the tests with `pytest`; lint with `ruff check . && ruff format --check .`.

## Phase 2 hooks

- **Ingestion:** Logic Apps / Power Automate → Blob Storage → a Service Bus message → a worker
  calling `InvoicePipeline.process()`. Locally, `watch` already processes a shared folder.
- **Throughput:** Service Bus absorbs month-end spikes. Set worker concurrency to the Azure OpenAI
  tokens-per-minute quota.
- **Human-in-the-loop at scale:** the store's invoice, feedback and metrics tables map directly to
  Dataverse, either for a Power Apps version of the review screen or for hosting this dashboard
  behind Entra ID sign-in.
