# The finance hub feed (Microsoft Fabric)

AP Coder can feed the company's finance hub on Microsoft Fabric with six tables about its invoices. This page is the
data dictionary: each table and column, its meaning and type, and how the tables relate. The design decision is
D-103 in [decisions.md](decisions.md).

The feed is **off** until an Admin turns it on in **Settings → Finance hub**. The invoices' PDFs never leave AP
Coder: each invoice carries a link to its page in AP Coder instead (`invoices.invoice_url`).

`HUB_SCHEMA_VERSION = 1`. Names and types never change within a version; a change is a new version, written in
`_hub.json` and in each table's `_schema.json`. A new version is sent as a full rebuild.

## How the data travels

The data team has two options; the tables and files are the same in both.

**Option A, "Fabric pulls"** (the default once the feed is on). AP Coder writes to a folder an Admin chooses, a
network share Fabric reads, laid out as Fabric Open Mirroring's landing zone:

```
<folder>/
  _hub.json                       the schema version, the format, the last run, every table's key and columns
  invoices/
    _metadata.json                {"keyColumns": ["invoice_id"]}
    _schema.json                  the columns with their types and meaning
    00000000000000000001.parquet  what changed in each run, numbered in order (.csv without pyarrow)
    00000000000000000002.parquet
  invoice_lines/  approvals/  activity/  accuracy/  workflow_runs/
  _gateway/finance_hub.db         optional: a read-only SQLite copy of every table, with views, for the
                                  on-premises data gateway
```

**Option B, "AP Coder pushes"**: the same files, written to a OneLake folder (for example a mirrored database's
landing zone, `https://onelake.dfs.fabric.microsoft.com/<workspace>/<item>/Files/LandingZone`) through the ADLS Gen2
DFS API, signed in with an Entra ID app registration (tenant ID, client ID, client secret: client credentials, scope
`https://storage.azure.com/.default`). The app registration needs write access to the workspace (Contributor).
Nothing is sent unless the internet is allowed for AP Coder (`AP_ALLOW_INTERNET=1`) **and** the Admin ticked "Send
the files to OneLake". The client secret is kept in the data folder's `.env` (`AP_HUB_CLIENT_SECRET`).

**Each data file** holds the rows that changed since the previous run, with the column `__rowMarker__` last: `4`
(upsert: insert the row, or replace the one with the same key) or `2` (delete the row with that key; only the key
columns are filled). Files are written under `_staging/` and then renamed into place, so a half-written file is never
seen.

**Formats.** Parquet (snappy) when the `pyarrow` package is installed; otherwise CSV (UTF-8, a header row, comma,
`"` quotes, empty for null), and `_metadata.json` says so (`"fileFormat": "DelimitedText"`). Types in Parquet:
int64, decimal(18,2), float64, date32, timestamp (milliseconds), boolean, string. In CSV: dates `YYYY-MM-DD`, timestamps
`YYYY-MM-DDTHH:MM:SS`, booleans `true`/`false`, money with two decimals.

**Times** are the local time of the computer or server running AP Coder, without a time zone, as AP Coder records
them.

## When it runs

- On its schedule (every hour, 4 or 12 hours, once a day, or only by hand) while the dashboard runs.
- **Run now** on Settings → Finance hub (with "Full rebuild" to send every row again).
- From the command line: `python -m ap_coder hub export [--full] [--dry-run]`, and `python -m ap_coder hub status`.
  A dashboard nobody opens runs nothing on its own, so on a server a scheduled task running `hub export` is the sure
  way.

**What is sent.** `activity` is sent by its watermark (the last event id sent: events are only ever added). The other
tables are compared row by row with what was sent before (a fingerprint per key, kept in the data folder's
`finance_hub/state.db`), so a change made anywhere (an approval, an export, a park) and an invoice deleted (a delete
row) both go out. A full rebuild sends every row again as upserts. A new folder, format or schema version, or a
database put back from a backup, is sent as a full rebuild by itself. A run that fails records nothing as sent, so
the next run sends it again. Each run that sends something, or fails, is in the Activity log (`hub_export`, with the
person who ran it) and in `workflow_runs`.

## How the tables relate

```
invoices (invoice_id) ──┬── invoice_lines (invoice_id, line_position)
                        ├── approvals     (approval_id = activity.event_id; invoice_id)
                        ├── activity      (event_id; invoice_id, when about an invoice)
                        └── accuracy      (accuracy_id; invoice_id)
workflow_runs (run_id): import-<event> and hub_export-<event> are activity events; erp_export-<batch> is
                        invoices.export_batch
```

- `invoice_lines.invoice_id`, `approvals.invoice_id`, `accuracy.invoice_id` and `activity.invoice_id` are
  `invoices.invoice_id`. An invoice deleted in AP Coder is deleted from `invoices` and `invoice_lines`; its approvals
  and its events stay (the audit trail keeps them).
- `approvals.approval_id` is the `activity.event_id` of the approval.
- `invoices.export_batch` is the number in `workflow_runs.run_id` `erp_export-<batch>`.
- `invoices.vendor_key` and `accuracy.vendor_key` name the same supplier; `invoices.vendor_erp_id` is its JD Edwards
  address number when the vendor master has it.

## What is left out

- The invoice files (PDFs, pictures, emails): only `invoice_url`.
- A vendor's bank details (in an event's detail they are shown as `(hidden)`), and the company's own bank accounts.
- Sign-in events (logins, failed logins, passwords, unlocked addresses): the server's security record.
- Settings, accounts and passwords (also their events in `activity`: user_added, role_changed, user_activated,
  user_deactivated, backup_restored, settings_changed, tax_setup_changed, policy_changed), the learning memory's
  lessons, imported ERP history.
- The computer address a change was made from: it is not in `activity`'s details.

## The tables

### `invoices`

One row per invoice in AP Coder, whatever its status. The amounts and coding are the approved version once approved, else what AP Coder proposed.

Key: `invoice_id`. Changes: compared row by row with what was sent (a fingerprint per key).

| Column | Type | Meaning |
|---|---|---|
| `invoice_id` | int64 | AP Coder's number for the invoice (the AP Coder # on screen). Key. |
| `status` | string | review, parked, pending_approval (waiting for a second approval), approved, rejected or failed (the file could not be read). |
| `coding_source` | string | approved: the values below are what AP approved; proposed: what AP Coder read and coded, not approved yet. |
| `vendor_name` | string | The supplier's name. |
| `vendor_key` | string | The name as AP Coder matches vendors (no case, accents or legal form): the same supplier always has the same key. |
| `vendor_erp_id` | string | The supplier's number in the ERP vendor master (JD Edwards address number), when known. |
| `invoice_number` | string | The supplier's invoice number, as printed. |
| `invoice_date` | date | The invoice date. |
| `due_date` | date | When it is due, from the terms (none for a credit note). |
| `po_number` | string | The purchase order it quotes. |
| `payment_terms` | string | The payment terms, as printed. |
| `currency` | string | ISO currency code (CAD, USD...). |
| `subtotal` | decimal(18,2) | Total before tax. |
| `gst_amount` | decimal(18,2) | GST charged (5%). |
| `hst_amount` | decimal(18,2) | HST charged. |
| `pst_amount` | decimal(18,2) | Provincial sales tax charged (BC PST, Manitoba RST, Saskatchewan PST). |
| `qst_amount` | decimal(18,2) | Quebec sales tax charged. |
| `other_tax_amount` | decimal(18,2) | Any other tax charged (a non-Canadian tax). |
| `tax_total` | decimal(18,2) | All tax charged. |
| `grand_total` | decimal(18,2) | Amount due, tax included (below zero for a credit note). |
| `is_credit_note` | boolean | A credit note (a total below zero). |
| `original_invoice_number` | string | For a credit note: the invoice it credits. |
| `supplier_province` | string | Province of the supplier's address (OUTSIDE_CANADA for a foreign one). |
| `ship_to_province` | string | Province delivered to (the place of supply), when printed. |
| `line_count` | int64 | How many lines (rows of invoice_lines). |
| `confidence` | float64 | AP Coder's confidence in its reading and coding, 0 to 1. |
| `needs_review` | boolean | AP Coder asked for a person to check it. |
| `source_file` | string | The file's name (the PDF or picture itself is not in the hub). |
| `source_channel` | string | file (a PDF or file brought in), picture (a photo or scan made into a PDF), demo (a sample invoice). |
| `received_at` | timestamp | When AP Coder read it. |
| `first_approved_by` | string | Who gave the (first) approval. |
| `first_approved_at` | timestamp | When. |
| `second_approved_by` | string | Who gave the second approval (over the approval limit). |
| `second_approved_at` | timestamp | When. |
| `touchless` | boolean | Approved by AP Coder without a person (touchless processing). |
| `parked_reason` | string | Why it is parked, while parked. |
| `export_batch` | int64 | The ERP export batch it went out in (none until exported, or once undone). |
| `exported_at` | timestamp | When that batch was made. |
| `jde_voucher` | string | The JD Edwards voucher (document number) once posted. |
| `jde_doc_type` | string | Its document type (PV...). |
| `jde_company` | string | Its document company. |
| `jde_posted_at` | timestamp | When it was posted. |
| `invoice_url` | string | A link to the invoice's page in AP Coder (the PDF stays there): Settings → Finance hub's address, by default `http://127.0.0.1:8501` on a laptop and the server's own address in server mode. |

### `invoice_lines`

One row per line of each invoice, coded: the approved coding once approved, else AP Coder's proposal.

Key: `invoice_id, line_position`. Changes: compared row by row with what was sent (a fingerprint per key).

| Column | Type | Meaning |
|---|---|---|
| `invoice_id` | int64 | The invoice (invoices.invoice_id). Key, with line_position. |
| `line_position` | int64 | The line's place on the invoice, 1 to line_count. Key, with invoice_id. |
| `line_number` | int64 | The line number as AP Coder read it (two lines may share one). |
| `description` | string | The line's description, as printed. |
| `quantity` | float64 | Quantity billed. |
| `unit_price` | float64 | Price per unit, before tax. |
| `amount` | decimal(18,2) | The line's amount, before tax. |
| `gl_account` | string | The GL account it is coded to (none while unassigned). |
| `cost_center` | string | The cost center it is coded to. |
| `tax_code` | string | The line's tax code as AP Coder shows it: the taxes charged (GST, GST+PST...), EXEMPT, ZERO or OUT, and what it is exempt from. |
| `taxes_applied` | string | The taxes charged on the line, joined with +. |
| `coding_source` | string | approved or proposed, as on invoices. |

### `approvals`

One row per approval given: the first (by a clerk or AP Coder on its own) and the second (over the approval limit). An approval later withdrawn (the invoice reopened or sent back) stays, marked withdrawn.

Key: `approval_id`. Changes: compared row by row with what was sent (a fingerprint per key).

| Column | Type | Meaning |
|---|---|---|
| `approval_id` | int64 | The approval's event in the audit trail (activity.event_id). Key. |
| `invoice_id` | int64 | The invoice approved. |
| `step` | string | first or second. |
| `approver_name` | string | The name on the approval. |
| `approver_account` | string | The account that approved (the username, on the company server; the computer login on a laptop). |
| `approver_role` | string | The account's role on the company server (clerk, approver, admin...). |
| `approved_at` | timestamp | When. |
| `amount` | decimal(18,2) | The invoice total approved. |
| `currency` | string | Its currency. |
| `needs_second` | boolean | A first approval that called for a second one. |
| `second_reason` | string | Why a second approval was needed. |
| `bulk` | boolean | Approved in bulk from the review queue. |
| `touchless` | boolean | Approved by AP Coder without a person. |
| `withdrawn` | boolean | The approval was withdrawn since (the invoice reopened or sent back). |
| `withdrawn_at` | timestamp | When. |

### `activity`

The audit trail: every event AP Coder records (read, approved, exported...), oldest first. Sign-in, account and settings events (`tax_setup_changed` and `policy_changed` too) are left out, and so is the computer address in an event's detail.

Key: `event_id`. Changes: only ever added; sent by the last event id sent (the watermark).

| Column | Type | Meaning |
|---|---|---|
| `event_id` | int64 | The event's number, in the order recorded. Key. |
| `invoice_id` | int64 | The invoice it is about, if any. |
| `action` | string | What happened (processed, approved, final_approved, exported, parked, rejected, import_run, hub_export...). |
| `actor` | string | Who did it (a person, or AP Coder). |
| `occurred_at` | timestamp | When. |
| `detail_json` | string | What AP Coder recorded with it, as JSON (a vendor's bank details hidden, no computer address). |

### `accuracy`

For each approved invoice, field by field, what AP Coder proposed against what AP approved: the header fields (as the supplier learning measures them) and each line's GL account and cost center (as the learning memory records them).

Key: `accuracy_id`. Changes: compared row by row with what was sent (a fingerprint per key).

| Column | Type | Meaning |
|---|---|---|
| `accuracy_id` | string | header:<invoice>:<field>:<source>, or line:<lesson>:<field>. Key. |
| `invoice_id` | int64 | The invoice. |
| `vendor_key` | string | Its supplier (invoices.vendor_key). |
| `line_number` | int64 | The line, for a line's field (none for a header field). |
| `field` | string | The field: invoice_number, invoice_date, grand_total..., or gl_account, cost_center. |
| `proposed` | string | What AP Coder proposed. |
| `approved` | string | What AP approved. |
| `correct` | boolean | AP Coder had it right. None when it proposed nothing, or nobody looked (a bulk approval): left out of the accuracy, as on Learning & accuracy. |
| `source` | string | review (checked by a person), audit (a touchless invoice checked by a person), computed (worked out from the invoice for one approved without that record), lines (a line's coding). |
| `recorded_at` | timestamp | When it was recorded (the approval). |

### `workflow_runs`

One row per run: each import of invoices, each ERP export batch, each finance hub feed.

Key: `run_id`. Changes: compared row by row with what was sent (a fingerprint per key).

| Column | Type | Meaning |
|---|---|---|
| `run_id` | string | import-<event>, erp_export-<batch> or hub_export-<event>. Key. |
| `kind` | string | import, erp_export or hub_export. |
| `source` | string | For an import: dashboard, process or watch; for an export: its format; for the hub: folder or onelake. |
| `actor` | string | Who ran it (or AP Coder, on its schedule). |
| `started_at` | timestamp | When it started. |
| `finished_at` | timestamp | When it finished. |
| `seconds` | float64 | How long it took. |
| `items` | int64 | Files read (import), invoices (export) or rows sent (hub). |
| `succeeded` | int64 | Read and coded (import), exported (export), rows written (hub). |
| `failed` | int64 | Files that could not be read (import). |
| `skipped` | int64 | Files already in AP Coder (import). |
| `total_amount` | decimal(18,2) | The batch's total (export); empty for a batch that mixes currencies. |
| `status` | string | ok, failed (some or all failed), undone (an export batch undone). |
| `errors` | string | The errors, one per line (the first 20). |

## The gateway's SQLite file

`_gateway/finance_hub.db` (Option A, unless unticked) is rebuilt whole at each run that sends something, and made
read-only. It holds the six tables above with the same columns (booleans as 0/1, money and numbers as REAL, dates and
times as text), `_hub_info` (`schema_version`, `built_at`) and three views:

| View | What it shows |
|---|---|
| `v_invoice_lines` | Each line with its invoice's vendor, ERP vendor number, invoice number, date, currency and status. |
| `v_approved_invoices` | The approved invoices. |
| `v_accuracy_by_field` | For each field: how many were checked, how many AP Coder had right, and the rate. |

Open it read-only (`file:finance_hub.db?mode=ro`). When the gateway holds it open while a run replaces it (Windows
refuses then), the tables still go out, the run says the copy was not replaced, and the next run tries again.
