# ap_coder: module index (generated)

Made by `python scripts/check_map.py --modules`; CI fails when it is out of date. For each module of
ap_coder/ and of its folders core, ai, coding, erp, intake, ops (the old paths' aliases aside): its job (its docstring's
first lines) and every module that imports it, so a change can be followed to its callers.
`web/pages/review` is `ap_coder/web/pages/review.py`.

| Module | Job | Imported by |
|---|---|---|
| `__init__.py` | AP Coder: reads vendor invoices on this computer (PDF text, local OCR) and codes them to GL accounts, with... | core/store, web/app, web/core, web/pages/settings, web/server |
| `__main__.py` |  | (entry point) |
| `ai/azure_check.py` | The Azure check: Azure AI Document Intelligence as an optional second opinion, off unless AP turns it on. | ai/pipeline, web/app, web/pages/azure |
| `ai/evaluation.py` | Accuracy evaluation against hand-labelled ground truth. | ops/cli, ops/share_report |
| `ai/extraction.py` | Component 1 - Extraction layer (Azure AI Document Intelligence). | ai/azure_check, ai/inference, ai/offline_coder, ai/page_worker, ai/pipeline, capture/workflow, coding/validation, erp/stamp, intake/mailbox, ops/cli, ops/doctor, web/invoice_view, web/pages/process |
| `ai/figures.py` | Two readings of a page compared figure by figure, as CloseDesk compares its page reader with OCR. | ai/offline_coder, ai/page_worker, capture |
| `ai/history.py` | Teach the AI from the ERP's past AP coding before the pilot starts. | core/store, web/pages/learning |
| `ai/imaging.py` | Page rendering for vision-capable models. | ai/inference, ai/pipeline, ai/training_export, ai/verifier |
| `ai/inference.py` | Component 2 - Inference & GL coding layer. | ai/offline_coder, ai/pipeline, ai/verifier, capture/workflow, ops/doctor |
| `ai/labels.py` | Ground-truth labelling via a spreadsheet the AP team can correct in Excel. | ai/evaluation, ops/cli |
| `ai/lessons.py` | Lessons: what AP corrects, remembered per situation, with AP's own reason, for the AI to read. | ai/pipeline, ai/verifier, capture/workflow, web/pages/invoice, web/pages/learning, web/pages/settings |
| `ai/line_match.py` | How alike two invoice line descriptions are: for coding a line from the lines AP coded before. | ai/suggest |
| `ai/local_llm.py` | Local model (LM Studio, Ollama): finding the server, picking its model, and reading a small model's JSON. | ai/inference, ai/page_reader, ai/reading, ai/verifier, ops/doctor, scripts/launch, web/pages/process, web/pages/settings_reading |
| `ai/offline_coder.py` | Coding an invoice with no AI model: the capture reader's header and lines, coded from memory. | ai/pipeline, bench/coding, capture/workflow |
| `ai/page_reader.py` | The page reader: OvisOCR2 reads every page of every invoice and writes down its text, tables too. | ai/page_worker, ai/reading, ops/cli, ops/doctor, scripts/launch, web/invoice_view, web/pages/process, web/pages/settings_reading |
| `ai/page_worker.py` | The page reader's queue: invoices waiting for OvisOCR2 to read their pages. | ai/azure_check, ai/pipeline, ai/reading, ai/verifier, ops/cli, ops/doctor, scripts/launch, web/app, web/invoice_view, web/pages/settings_reading |
| `ai/pipeline.py` | Unified execution pipeline: extract -> code -> validate -> persist. | ai/page_worker, bench/speed, coding/bulk, ops/cli, ops/demo, scripts/pilot_check, web/invoice_view, web/pages/process |
| `ai/prompts.py` | System prompt for the GL coding model. | ai/inference |
| `ai/reading.py` | How AP Coder reads every invoice, the same way each time, and how each reader is doing right now. | web/core |
| `ai/suggest.py` | GL account suggestions for invoice lines, from what AP approved before (no AI model, no Azure call). | ai/offline_coder, bench/coding, web/invoice_view |
| `ai/training_export.py` | Training data from AP's approvals: each approved invoice's pages and what AP approved, as one local ZIP. | ops/cli, web/pages/learning |
| `ai/verifier.py` | The AI reviewer: the local chat model (Gemma 4, Qwen 3.5) checks a whole invoice against its pages, in the... | ai/azure_check, ai/page_worker, ai/pipeline, ai/reading, capture/workflow, ops/demo, web/invoice_view, web/pages/invoice, web/pages/settings_reading |
| `coding/bulk.py` | Bulk approval of clean invoices. | web/pages/review |
| `coding/po.py` | Purchase orders and PO matching (2-way and, when receipts are known, 3-way). | ai/pipeline, core/store, erp/accruals, erp/search, web/invoice_view, web/pages/purchase_orders |
| `coding/review.py` | Turning the dashboard's edited header fields and grids back into an ``InvoiceCoding``. | web/invoice_view |
| `coding/rules.py` | Fixed coding rules: lines that always go to the same GL account (and cost center), whatever the AI says. | ai/pipeline, bench/coding, core/store, erp/search, web/invoice_view, web/pages/accounts |
| `coding/tax.py` | Canadian sales tax: GST, HST, PST (incl. Manitoba RST) and QST. | ai/offline_coder, ai/pipeline, coding/taxmath, coding/taxreturn, coding/validation, coding/vendor_mail, core/reference_data, core/schema, core/store, erp/export_layout, erp/exports, erp/jde, erp/jde_worksheet, erp/spend, ops/cli, ops/doctor, web/invoice_view, web/pages/accounts, web/pages/invoice, web/pages/process, web/pages/review, web/pages/settings, web/pages/settings_jde, web/pages/vendors |
| `coding/taxmath.py` | The math on an invoice, worked out again from its lines: the review screen shows it under the tax lines... | ai/verifier, web/invoice_view |
| `coding/taxreturn.py` | Sales-tax return support: the GST/HST and QST paid to vendors that can be claimed back for a period. | web/pages/sales_tax |
| `coding/terms.py` | Payment terms: when an invoice is due and whether an early-payment discount is on offer. | ai/pipeline, core/store, erp/insights, web/invoice_view, web/pages/invoice, web/pages/review |
| `coding/validation.py` | Deterministic post-inference controls. | ai/azure_check, ai/pipeline, capture/bridge, web/invoice_view |
| `coding/vendor_mail.py` | Draft an email asking the vendor for what an invoice is missing, from its open checks. | erp/insights, web/invoice_view, web/pages/statements, web/pages/vendors |
| `coding/vendors.py` | Vendor master and payment-fraud / duplicate signals. | ai/pipeline, coding/validation, core/audit, core/store, erp/accruals, erp/dupaudit, erp/exports, erp/jde, erp/search, erp/statements, web/pages/vendors |
| `core/audit.py` | Audit trail: what changed between the AI's suggestion and what was approved, and how to show events. | core/store, erp/controls, web/pages/activity, web/pages/invoice |
| `core/config.py` | Runtime configuration, loaded from environment variables (and an optional .env file). | ai/azure_check, ai/extraction, ai/inference, ai/local_llm, ai/page_reader, ai/page_worker, ai/pipeline, bench/speed, coding/bulk, ops/cli, ops/demo, ops/doctor, scripts/launch, scripts/pilot_check, web/app, web/core, web/invoice_view, web/pages/process, web/pages/settings_reading |
| `core/csvio.py` | CSV reading that copes with files saved by Excel or an ERP export. | coding/tax, core/reference_data, erp/dupaudit, erp/statements, web/files |
| `core/envfile.py` | Reading and updating the ``.env`` settings file in place. | ai/azure_check, core/config, scripts/install, scripts/launch, web/pages/settings, web/pages/settings_reading |
| `core/memory.py` | Learning from reviewer decisions (no model retraining). | ai/azure_check, ai/evaluation, ai/lessons, ai/line_match, ai/offline_coder, ai/pipeline, ai/suggest, ai/verifier, bench/coding, capture/bridge, capture/supplier, coding/po, coding/rules, coding/vendors, core/audit, core/store, erp/accruals, erp/dupaudit, erp/insights, erp/jde, erp/jde_worksheet, web/invoice_view, web/pages/invoice, web/pages/review, web/pages/settings_jde |
| `core/paths.py` | Where AP Coder keeps your data and settings. | ai/azure_check, ai/page_reader, ai/page_worker, ai/pipeline, coding/tax, core/config, core/store, ops/cli, ops/demo, ops/offline, scripts/first_run, scripts/install, scripts/launch, scripts/running, scripts/upgrade, web/app, web/core, web/pages/help, web/pages/settings, web/server |
| `core/reference_data.py` | Enterprise reference data: GL accounts (cost codes), optional cost centers and tax setup. | ai/inference, ai/labels, ai/offline_coder, ai/pipeline, ai/prompts, ai/suggest, bench/coding, bench/speed, coding/bulk, coding/review, coding/validation, core/schema, core/store, erp/spend, ops/cli, ops/doctor, scripts/pilot_check, web/core, web/invoice_view, web/pages/accounts, web/pages/exports, web/pages/invoice, web/pages/learning, web/pages/month_end, web/pages/vendors |
| `core/safe.py` | Make untrusted text safe for the places it ends up: spreadsheets (and amounts read from them). | ai/history, ai/labels, coding/po, coding/taxreturn, erp/accruals, erp/directory, erp/dupaudit, erp/exports, erp/jde_worksheet, erp/registers, erp/search, erp/spend, erp/statements, web/pages/accounts, web/pages/activity, web/pages/search |
| `core/schema.py` | Target output schema. | ai/inference, ai/offline_coder, ai/pipeline, ai/verifier, coding/bulk, coding/po, coding/review, coding/rules, coding/taxmath, coding/validation, coding/vendors, erp/jde, ops/cli, ops/demo, ops/doctor, web/invoice_view |
| `core/store.py` | Local SQLite store behind the dashboard (``private/ap_coder.db``). | ai/azure_check, ai/page_worker, ai/pipeline, ai/training_export, ai/verifier, bench/coding, bench/learning, bench/speed, capture/workflow, coding/bulk, coding/po, coding/taxreturn, coding/vendors, erp/accruals, erp/controls, erp/directory, erp/dupaudit, erp/insights, erp/search, erp/spend, erp/stamp, ops/cli, ops/demo, ops/share_report, scripts/launch, scripts/pilot_check, web/app, web/core, web/identity, web/invoice_view, web/pages/accounts, web/pages/exports, web/pages/invoice, web/pages/learning, web/pages/process, web/pages/purchase_orders, web/pages/review, web/pages/search, web/pages/settings, web/pages/settings_automation, web/pages/settings_jde, web/pages/settings_reading, web/pages/statements, web/pages/vendors, web/server |
| `core/ui.py` | HTML building blocks for the dashboard (styled by web/static/app.css; templates call them as ``ui.*``). | ai/azure_check, capture/workflow, coding/taxmath, web/core, web/invoice_view, web/pages/accounts, web/pages/activity, web/pages/exports, web/pages/insights, web/pages/invoice, web/pages/learning, web/pages/month_end, web/pages/process, web/pages/purchase_orders, web/pages/review, web/pages/sales_tax, web/pages/settings, web/pages/settings_automation, web/pages/settings_jde, web/pages/settings_reading, web/pages/spend, web/pages/vendors |
| `erp/accruals.py` | Month-end accruals: costs incurred by the period end that are not yet in the ERP. | web/pages/month_end |
| `erp/controls.py` | Controls report for internal audit: the exceptions in a period, from the audit trail. | web/pages/activity |
| `erp/directory.py` | The invoice directory: every invoice in a table, filtered column by column, as JD Edwards' "query by example". | web/pages/search |
| `erp/dupaudit.py` | Duplicate payment audit: bills that may have been approved (or posted in the ERP) twice. | web/pages/activity, web/pages/exports |
| `erp/export_layout.py` | A CSV layout of your own, for an ERP whose import expects specific columns. | erp/exports, web/pages/exports |
| `erp/exports.py` | ERP export files for approved invoices. | erp/jde, web/pages/exports, web/pages/search |
| `erp/insights.py` | Insights: the business case (volume, straight-through rate, time saved, Azure cost), operations, workload. | web/pages/insights, web/pages/vendors |
| `erp/jde.py` | JD Edwards EnterpriseOne (E1) batch vouchers: F0411Z1 / F0911Z1 files for R04110ZA. | erp/jde_worksheet, web/pages/exports, web/pages/settings_jde |
| `erp/jde_worksheet.py` | The JD Edwards settings worksheet: one Excel file AP sends the JDE team, and AP Coder reads back. | web/pages/settings_jde |
| `erp/recurring.py` | Recurring vendors: who bills on a regular rhythm, when the next invoice is due, and which are late. | erp/accruals, web/pages/review, web/pages/vendors |
| `erp/registers.py` | The ERP's AP invoice register: invoices entered in the ERP before (or outside) AP Coder. | web/pages/exports |
| `erp/search.py` | The invoice directory's quick search: answer a vendor's "did you get it, and when will it be paid?". | erp/directory, web/pages/search |
| `erp/spend.py` | Spend analysis from approved invoices, and a full data download for Excel / Power BI. | web/pages/spend |
| `erp/stamp.py` | Approval-stamped PDFs: the invoice as received, with an APPROVED stamp on its first page and a coding page... | web/pages/exports, web/pages/invoice, web/pages/search |
| `erp/statements.py` | Vendor statement reconciliation. | ai/history, coding/vendor_mail, erp/dupaudit, erp/registers, web/pages/statements |
| `intake/inbox.py` | The emails triage set aside: what a person still has to look at, and the actions on them. | ops/cli, web/pages/process, web/pages/statements |
| `intake/mailbox.py` | Invoices that arrive by email: take the attachments out of saved emails and sort them (``triage``). | bench/emails, intake/inbox, ops/cli, web/pages/process |
| `intake/msgfile.py` | Outlook's .msg emails: what classic desktop Outlook saves when an email is dragged to a folder or *Save As*'d. | bench/msgwriter, intake/mailbox |
| `intake/pictures.py` | Every invoice is a PDF: a photo or a scan saved as a picture is turned into one when it comes in. | ai/pipeline, capture/layout, intake |
| `intake/triage.py` | Email triage: what each email from the AP mailbox is, and where each of its attachments goes. | bench/emails, erp/jde_worksheet, intake/inbox, intake/mailbox, web/files |
| `ops/cli.py` | Command-line entry point: ``python -m ap_coder <command>``. | __main__, ai/page_worker, scripts/launch |
| `ops/demo.py` | Demo mode: the whole product with the bundled sample invoices, without Azure. | ops/cli, web/pages/process, web/pages/review |
| `ops/doctor.py` | Environment and data readiness checks (``python -m ap_coder doctor``). | ops/cli, scripts/launch, web/pages/settings_reading |
| `ops/help.py` | Plain-English help for every check AP Coder runs: what it means and what to do about it. | web/invoice_view, web/pages/help, web/pages/insights, web/pages/vendors |
| `ops/offline.py` | Offline readiness: the local OCR models, and checks that the app keeps everything on this computer. | ai/extraction, ai/inference, ai/local_llm, ai/pipeline, ai/reading, capture/layout, capture/table_finder, erp/insights, ops/cli, ops/doctor, scripts/build_offline_bundle, scripts/fetch_models, scripts/install, scripts/launch, scripts/running, web/pages/settings_reading, web/server |
| `ops/share_report.py` | Redacted run summary that is safe to paste into a chat or ticket. | ops/cli |
