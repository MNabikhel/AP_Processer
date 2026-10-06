# Enterprise AP Invoice Coder Engine (PoC)

Phase 1 proof of concept for automated Accounts Payable invoice coding on Azure. A single
Python pipeline chains:

1. **Extraction (Step 2):** Azure AI Document Intelligence (`prebuilt-layout` or `prebuilt-invoice`)
   turns a raw PDF, TIFF or image invoice into reading-order Markdown with multi-page tables preserved.
2. **Inference & GL coding (Step 3):** Azure OpenAI (`gpt-4o`, `gpt-4o-mini`, or any newer
   deployment) reads that Markdown with a snapshot of the Chart of Accounts, Cost Centers and Tax
   Codes. It returns the target JSON, enforced natively by **Structured Outputs (`strict: true`)**.

Deterministic controls then reconcile the numbers, check every code and decide whether a person
needs to review the invoice.

```
invoice.pdf/.tiff/.png ──► Document Intelligence ──► Markdown + tables + OCR confidence
                              (prebuilt-layout |        (+ invoice field hints)
                               prebuilt-invoice)                 │
                                                                 ▼
 Chart of Accounts ┐                                  Azure OpenAI Chat Completions
 Cost Centers      ├─► system prompt (stable prefix) ─►  response_format = json_schema
 Tax Codes, Policy ┘                                   strict, GL/CC codes as enums
                                     (optional page images in vision mode)
                                                                 │
                                                                 ▼
                                          Pydantic re-validation (+1 repair round-trip)
                                                                 │
                                                                 ▼
                                   Validation: totals reconcile, codes exist, DI cross-check
                                   → adjusted confidence + requires_review flag
                                                                 │
                                                                 ▼
                          <stem>.json (target schema) · <stem>.validation.json · <stem>.extraction.md
```

> **Running this on real enterprise data?** Follow [docs/GETTING_STARTED.md](docs/GETTING_STARTED.md).
> All your data stays in the git-ignored `private/` folder. Only redacted reports (`doctor`, `share-report`)
> are meant to leave your machine.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"          # or: pip install -r requirements.txt
cp .env.example .env              # fill in endpoints/keys, or leave keys empty to use Entra ID (az login)

# Check configuration, reference data and Azure connectivity
python -m ap_coder doctor --online

# Full chain on a real document (a synthetic 2-page sample is included); results go to private/output
python -m ap_coder process samples/contoso_invoice_INV-2026-04471.pdf

# A whole folder, 4 invoices in parallel, prebuilt-invoice model, page images attached
python -m ap_coder process ./inbox --extraction-model prebuilt-invoice --vision --workers 4

# Skip Document Intelligence: feed already-extracted Markdown straight to the LLM
python -m ap_coder process samples/contoso_invoice_INV-2026-04471.md

# Measure accuracy against hand-labelled ground truth (the >90% gate for Phase 2)
python -m ap_coder evaluate --ground-truth samples/ground_truth --show-mismatches
```

Other commands:

| Command | Purpose |
|---|---|
| `doctor [--online]` | Setup, reference-data and connectivity check. Output contains no secrets or URLs. |
| `process <files/dirs…>` | Full extract → code → validate pipeline into `private/output`. Exit code 1 if any invoice failed. |
| `extract <files/dirs…>` | Document Intelligence only; writes `.extraction.md` and raw `.di.json`. |
| `labels [--blind]` | Excel workbook (dropdowns of valid codes, text-typed cells) for the AP team to correct into ground truth. |
| `evaluate [--ground-truth]` | Header, GL and cost-center accuracy against the target (default 0.9). Accepts a JSON folder, `.xlsx` or `.csv`. |
| `share-report [--ground-truth] [--include-codes]` | Redacted aggregate summary: no vendor names, amounts, descriptions or file names. |
| `schema [--tax-rate-field]` | Prints the exact strict JSON Schema sent to Azure OpenAI. |

Reference data is looked up in this order: `AP_REFERENCE_DIR`, then `private/reference/`, then the
bundled samples in `data/`. Individual files can be overridden with `--coa`, `--cost-centers`,
`--tax-codes` and `--policy`. CSV and JSON are both accepted. Common ERP headers (*Main account*,
*GL Account*, *SAKNR*, *Cost Centre*, *KOSTL*, *VAT code*, …) are recognised without renaming,
and tax rates may be written `0.2`, `20` or `20%`. Set an `active` column to `false` to retire a
code without deleting it.

## Output

`private/output/<stem>.json` contains **exactly** the target schema, with no Markdown wrappers and no extra keys:

```json
{
  "vendor_name": "Contoso Cloud Solutions Ltd",
  "invoice_number": "INV-2026-04471",
  "invoice_date": "2026-09-14",
  "currency": "GBP",
  "subtotal": 23745.0,
  "tax_total": 4749.0,
  "grand_total": 28494.0,
  "confidence_score": 0.93,
  "line_items": [
    {
      "line_number": 4,
      "description": "Dell PowerEdge R760 rack server - IT Operations datacentre",
      "quantity": 1,
      "unit_price": 8900.0,
      "amount": 8900.0,
      "predicted_gl_code": "1500",
      "predicted_cost_center": "CC400",
      "reasoning_justification": "Unit cost 8,900 exceeds the 2,500 capitalisation threshold, so Computer Equipment (asset); explicitly for IT Operations; standard VAT 20%."
    }
  ]
}
```

The audit trail goes in sidecar files so it never pollutes the contract:

* `<stem>.validation.json`: extraction stats (pages, tables, mean OCR word confidence), model,
  token usage including cached prompt tokens, repair attempts, every validation issue,
  `adjusted_confidence` and `requires_review`.
* `<stem>.extraction.md`: the Markdown the LLM actually saw.
* `batch_summary.json`: one row per invoice.

## Design decisions

**Structured Outputs, natively enforced.** The schema is hand-written in `ap_coder/schema.py` to
satisfy strict-mode rules: every object has `additionalProperties: false`, and every property is
required. It is sent as `response_format={"type":"json_schema","json_schema":{"strict":true,...}}`.
The response is re-validated with a Pydantic mirror, which checks `YYYY-MM-DD` dates and keeps
confidence in [0, 1]. If a value-level check fails, the model gets one repair round-trip.

**Codes cannot be invented.** When the reference lists fit Structured Outputs enum limits, the
valid GL codes and cost centers are embedded in the schema as `enum`s, plus an `UNASSIGNED`
escape hatch. The model therefore cannot return an account that does not exist. If it is
unsure, it says so, and the invoice goes to review instead of being posted to a wrong account.
Larger charts fall back automatically to free text plus local validation (`AP_CONSTRAIN_CODES`).

**Tax rate.** The target schema has no tax field, so by default the model determines the rate
from the Tax Codes table and states it in `reasoning_justification`. With `--tax-rate-field` (or
`AP_INCLUDE_TAX_RATE=true`), `predicted_tax_rate` is added to every line. The engine then also
checks that rate against the tax table and checks that `Σ amount × rate ≈ tax_total`.

**Model-agnostic and ready for vision.** Deployment names are arbitrary, so capabilities are
inferred from `AZURE_OPENAI_MODEL_NAME`:

* Reasoning models (o-series, gpt-5) do not receive `temperature` or `seed`, and do receive
  `reasoning_effort`.
* Text-only models never receive images.

Vision mode (`--vision`) renders up to `AP_VISION_MAX_PAGES` pages (PyMuPDF for PDFs, Pillow for
multi-frame TIFFs) and sends them with the Markdown. Moving to a newer model is a configuration
change, not a code change.

**Prompt caching friendly.** Instructions plus the reference-data snapshot form an identical
system-message prefix for every invoice, so Azure OpenAI prompt caching discounts it across a
batch. The invoice content comes last.

**Deterministic controls on top of the LLM** (`ap_coder/validation.py`):

| Check | Severity |
|---|---|
| GL / cost center not in reference data | error |
| `Σ line amounts ≠ subtotal` (cent-level, ±1¢ per line rounding) | error |
| `subtotal + tax ≠ grand_total` (±2¢) | error |
| Missing vendor / invoice number / no line items | error |
| `UNASSIGNED` code, `qty × unit_price ≠ amount`, non-sequential lines | warning |
| Mean OCR word confidence < 0.90 | warning |
| Disagreement with `prebuilt-invoice` fields (InvoiceId, date, totals) | warning |

`adjusted_confidence = model confidence × 0.6 per error × 0.9 per warning`. An invoice gets
`requires_review = true` if it has any error, or if its adjusted confidence is below
`AP_REVIEW_THRESHOLD` (default 0.85). This flag is meant to drive the Phase 2 Power Apps review queue.

**Cheap iteration.** Document Intelligence results are cached on disk in `.cache/extraction`,
keyed by file hash and options. Prompt or model experiments can therefore rerun over a corpus
without paying for OCR again.

**Enterprise auth.** If a key is not set, both services use Entra ID through
`DefaultAzureCredential` (managed identity in Azure, `az login` locally). The OpenAI SDK retries
429 and 5xx responses with backoff and honours `Retry-After` (`AZURE_OPENAI_MAX_RETRIES`).

## Project layout

```
ap_coder/
  config.py          env-driven settings (.env supported)
  extraction.py      Component 1 – Document Intelligence → ExtractionResult (+ cache)
  imaging.py         page rendering for vision mode
  reference_data.py  CoA / cost center / tax code loaders + prompt snapshot
  prompts.py         system prompt (extraction + GL coding rules)
  schema.py          strict JSON Schema + Pydantic mirror
  inference.py       Component 2 – Azure OpenAI Structured Outputs, model profiles, repair loop
  validation.py      deterministic controls, adjusted confidence, review flag
  pipeline.py        unified extract → code → validate → persist chain, batch runner
  evaluation.py      accuracy scoring vs ground truth
  labels.py          Excel/CSV labelling workbook export + import
  share_report.py    redacted, paste-safe run summary
  doctor.py          configuration / reference data / connectivity checks
  cli.py             `python -m ap_coder …`
data/                sample Chart of Accounts, cost centers, tax codes, coding policy
samples/             synthetic 2-page invoice (PDF + extracted Markdown) and its ground truth
scripts/             sample PDF generator
docs/                GETTING_STARTED.md – step-by-step guide for running on enterprise data
private/             git-ignored home for your invoices, reference exports, outputs and labels
tests/               offline test suite (Azure clients mocked)
```

Run the tests with `pytest`, and lint with `ruff check . && ruff format --check .`.

## Phase 2 hooks

The pipeline is a pure function from a document path to an output plus a report.
`InvoicePipeline.process()` can therefore be wrapped directly:

* **Ingestion:** a Logic App or Power Automate flow drops attachments in Blob Storage. A Service
  Bus message carries the blob URL, and an Azure Function or Container App worker downloads the
  blob and calls `process()`.
* **Throughput:** Service Bus absorbs month-end spikes. Set worker concurrency
  (`--workers` today) to the AOAI TPM quota. Built-in retries handle 429 responses.
* **Human-in-the-loop:** `<stem>.validation.json` already contains `requires_review`,
  `adjusted_confidence` and line-level issues, ready to write to Dataverse for the Power Apps review
  dashboard before ERP posting.
