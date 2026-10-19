# ap_coder: module index (generated)

Made by `python scripts/check_map.py --modules`; CI fails when it is out of date. For each module of the
flat core: its job (its docstring's first lines) and every module that imports it, so a change can be
followed to its callers. `web/pages/review` is `ap_coder/web/pages/review.py`.

| Module | Job | Imported by |
|---|---|---|
| `__init__.py` | AP Coder: reads vendor invoices on this computer (PDF text, local OCR) and codes them to GL accounts, with... | store, web/app, web/core, web/pages/settings, web/server |
| `__main__.py` |  | (entry point) |
| `accruals.py` | Month-end accruals: costs incurred by the period end that are not yet in the ERP. | web/pages/month_end |
| `audit.py` | Audit trail: what changed between the AI's suggestion and what was approved, and how to show events. | controls, store, web/pages/activity, web/pages/invoice |
| `azure_check.py` | The Azure check: Azure AI Document Intelligence as an optional second opinion, off unless AP turns it on. | pipeline, web/app, web/pages/azure |
| `bulk.py` | Bulk approval of clean invoices. | web/pages/review |
| `cli.py` | Command-line entry point: ``python -m ap_coder <command>``. | __main__, page_worker, scripts/launch |
| `config.py` | Runtime configuration, loaded from environment variables (and an optional .env file). | azure_check, bench/speed, bulk, cli, demo, doctor, extraction, inference, local_llm, page_reader, page_worker, pipeline, scripts/ci/old_versions, scripts/launch, scripts/pilot_check, web/app, web/core, web/invoice_view, web/pages/process, web/pages/settings_reading |
| `controls.py` | Controls report for internal audit: the exceptions in a period, from the audit trail. | web/pages/activity |
| `csvio.py` | CSV reading that copes with files saved by Excel or an ERP export. | dupaudit, reference_data, statements, tax |
| `demo.py` | Demo mode: the whole product with the bundled sample invoices, without Azure. | cli, scripts/ci/old_versions, web/pages/process, web/pages/review |
| `directory.py` | The invoice directory: every invoice in a table, filtered column by column, as JD Edwards' "query by example". | web/pages/search |
| `doctor.py` | Environment and data readiness checks (``python -m ap_coder doctor``). | cli, scripts/launch, web/pages/settings_reading |
| `dupaudit.py` | Duplicate payment audit: bills that may have been approved (or posted in the ERP) twice. | web/pages/activity, web/pages/exports |
| `envfile.py` | Reading and updating the ``.env`` settings file in place. | azure_check, config, scripts/install, scripts/launch, web/pages/settings, web/pages/settings_reading |
| `evaluation.py` | Accuracy evaluation against hand-labelled ground truth. | cli, share_report |
| `export_layout.py` | A CSV layout of your own, for an ERP whose import expects specific columns. | exports, web/pages/exports |
| `exports.py` | ERP export files for approved invoices. | jde, web/pages/exports, web/pages/search |
| `extraction.py` | Component 1 - Extraction layer (Azure AI Document Intelligence). | azure_check, capture/workflow, cli, doctor, inference, mailbox, offline_coder, page_worker, pipeline, stamp, validation, web/invoice_view, web/pages/process |
| `figures.py` | Two readings of a page compared figure by figure, as CloseDesk compares its page reader with OCR. | capture, offline_coder, page_worker |
| `help.py` | Plain-English help for every check AP Coder runs: what it means and what to do about it. | web/invoice_view, web/pages/help, web/pages/insights, web/pages/vendors |
| `history.py` | Teach the AI from the ERP's past AP coding before the pilot starts. | store, web/pages/learning |
| `imaging.py` | Page rendering for vision-capable models. | inference, pipeline, training_export, verifier |
| `inbox.py` | The emails triage set aside: what a person still has to look at, and the actions on them. | cli, web/pages/process, web/pages/statements |
| `inference.py` | Component 2 - Inference & GL coding layer. | capture/workflow, doctor, offline_coder, pipeline, verifier |
| `insights.py` | Insights: the business case (volume, straight-through rate, time saved, Azure cost), operations, workload. | web/pages/insights, web/pages/vendors |
| `intake.py` | Every invoice is a PDF: a photo or a scan saved as a picture is turned into one when it comes in. | capture/layout, pipeline |
| `jde.py` | JD Edwards EnterpriseOne (E1) batch vouchers: F0411Z1 / F0911Z1 files for R04110ZA. | jde_worksheet, web/pages/exports, web/pages/settings_jde |
| `jde_worksheet.py` | The JD Edwards settings worksheet: one Excel file AP sends the JDE team, and AP Coder reads back. | web/pages/settings_jde |
| `labels.py` | Ground-truth labelling via a spreadsheet the AP team can correct in Excel. | cli, evaluation |
| `lessons.py` | Lessons: what AP corrects, remembered per situation, with AP's own reason, for the AI to read. | capture/workflow, pipeline, verifier, web/pages/invoice, web/pages/learning, web/pages/settings |
| `line_match.py` | How alike two invoice line descriptions are: for coding a line from the lines AP coded before. | suggest |
| `local_llm.py` | Local model (LM Studio, Ollama): finding the server, picking its model, and reading a small model's JSON. | doctor, inference, page_reader, reading, scripts/launch, verifier, web/pages/process, web/pages/settings_reading |
| `mailbox.py` | Invoices that arrive by email: take the attachments out of saved emails and sort them (``triage``). | bench/emails, cli, inbox, web/pages/process |
| `memory.py` | Learning from reviewer decisions (no model retraining). | accruals, audit, azure_check, bench/coding, capture/bridge, capture/supplier, dupaudit, evaluation, insights, jde, jde_worksheet, lessons, line_match, offline_coder, pipeline, po, rules, scripts/ci/old_versions, store, suggest, vendors, verifier, web/invoice_view, web/pages/invoice, web/pages/review, web/pages/settings_jde |
| `msgfile.py` | Outlook's .msg emails: what classic desktop Outlook saves when an email is dragged to a folder or *Save As*'d. | bench/msgwriter, mailbox |
| `offline.py` | Offline readiness: the local OCR models, and checks that the app keeps everything on this computer. | capture/layout, capture/table_finder, cli, doctor, extraction, inference, insights, local_llm, pipeline, reading, scripts/build_offline_bundle, scripts/fetch_models, scripts/install, scripts/launch, scripts/running, web/pages/settings_reading, web/server |
| `offline_coder.py` | Coding an invoice with no AI model: the capture reader's header and lines, coded from memory. | bench/coding, capture/workflow, pipeline |
| `page_reader.py` | The page reader: OvisOCR2 reads every page of every invoice and writes down its text, tables too. | cli, doctor, page_worker, reading, scripts/launch, web/invoice_view, web/pages/process, web/pages/settings_reading |
| `page_worker.py` | The page reader's queue: invoices waiting for OvisOCR2 to read their pages. | azure_check, cli, doctor, pipeline, reading, scripts/launch, verifier, web/app, web/invoice_view, web/pages/settings_reading |
| `paths.py` | Where AP Coder keeps your data and settings. | azure_check, cli, config, demo, offline, page_reader, page_worker, pipeline, scripts/ci/old_versions, scripts/first_run, scripts/install, scripts/launch, scripts/running, scripts/upgrade, store, tax, web/app, web/core, web/pages/help, web/pages/settings, web/server |
| `pipeline.py` | Unified execution pipeline: extract -> code -> validate -> persist. | bench/speed, bulk, cli, demo, page_worker, scripts/ci/old_versions, scripts/pilot_check, web/invoice_view, web/pages/process |
| `po.py` | Purchase orders and PO matching (2-way and, when receipts are known, 3-way). | accruals, pipeline, search, store, web/invoice_view, web/pages/purchase_orders |
| `prompts.py` | System prompt for the GL coding model. | inference |
| `reading.py` | How AP Coder reads every invoice, the same way each time, and how each reader is doing right now. | web/core |
| `recurring.py` | Recurring vendors: who bills on a regular rhythm, when the next invoice is due, and which are late. | accruals, web/pages/review, web/pages/vendors |
| `reference_data.py` | Enterprise reference data: GL accounts (cost codes), optional cost centers and tax setup. | bench/coding, bench/speed, bulk, cli, doctor, inference, labels, offline_coder, pipeline, prompts, review, schema, scripts/pilot_check, spend, store, suggest, validation, web/core, web/invoice_view, web/pages/accounts, web/pages/exports, web/pages/invoice, web/pages/learning, web/pages/month_end, web/pages/vendors |
| `registers.py` | The ERP's AP invoice register: invoices entered in the ERP before (or outside) AP Coder. | web/pages/exports |
| `review.py` | Turning the dashboard's edited header fields and grids back into an ``InvoiceCoding``. | web/invoice_view |
| `rules.py` | Fixed coding rules: lines that always go to the same GL account (and cost center), whatever the AI says. | bench/coding, pipeline, scripts/ci/old_versions, search, store, web/invoice_view, web/pages/accounts |
| `safe.py` | Make untrusted text safe for the places it ends up: spreadsheets (and amounts read from them). | accruals, directory, dupaudit, exports, history, jde_worksheet, labels, po, registers, search, spend, statements, taxreturn, web/pages/accounts, web/pages/activity, web/pages/search |
| `schema.py` | Target output schema. | bulk, cli, demo, doctor, inference, jde, offline_coder, pipeline, po, review, rules, scripts/ci/old_versions, taxmath, validation, vendors, verifier, web/invoice_view |
| `search.py` | The invoice directory's quick search: answer a vendor's "did you get it, and when will it be paid?". | directory, web/pages/search |
| `share_report.py` | Redacted run summary that is safe to paste into a chat or ticket. | cli |
| `spend.py` | Spend analysis from approved invoices, and a full data download for Excel / Power BI. | web/pages/spend |
| `stamp.py` | Approval-stamped PDFs: the invoice as received, with an APPROVED stamp on its first page and a coding page... | web/pages/exports, web/pages/invoice, web/pages/search |
| `statements.py` | Vendor statement reconciliation. | dupaudit, history, registers, vendor_mail, web/pages/statements |
| `store.py` | Local SQLite store behind the dashboard (``private/ap_coder.db``). | accruals, azure_check, bench/coding, bench/learning, bench/speed, bulk, capture/workflow, cli, controls, demo, directory, dupaudit, insights, page_worker, pipeline, po, scripts/ci/old_versions, scripts/launch, scripts/pilot_check, search, share_report, spend, stamp, taxreturn, training_export, vendors, verifier, web/app, web/core, web/invoice_view, web/pages/accounts, web/pages/exports, web/pages/invoice, web/pages/learning, web/pages/process, web/pages/purchase_orders, web/pages/review, web/pages/search, web/pages/settings, web/pages/settings_automation, web/pages/settings_jde, web/pages/settings_reading, web/pages/statements, web/pages/vendors, web/server |
| `suggest.py` | GL account suggestions for invoice lines, from what AP approved before (no AI model, no Azure call). | bench/coding, offline_coder, web/invoice_view |
| `tax.py` | Canadian sales tax: GST, HST, PST (incl. Manitoba RST) and QST. | cli, doctor, export_layout, exports, jde, jde_worksheet, offline_coder, pipeline, reference_data, schema, spend, store, taxmath, taxreturn, validation, vendor_mail, web/invoice_view, web/pages/accounts, web/pages/invoice, web/pages/process, web/pages/review, web/pages/settings, web/pages/settings_jde, web/pages/vendors |
| `taxmath.py` | The math on an invoice, worked out again from its lines: the review screen shows it under the tax lines... | verifier, web/invoice_view |
| `taxreturn.py` | Sales-tax return support: the GST/HST and QST paid to vendors that can be claimed back for a period. | web/pages/sales_tax |
| `terms.py` | Payment terms: when an invoice is due and whether an early-payment discount is on offer. | insights, pipeline, store, web/invoice_view, web/pages/invoice, web/pages/review |
| `training_export.py` | Training data from AP's approvals: each approved invoice's pages and what AP approved, as one local ZIP. | cli, web/pages/learning |
| `triage.py` | Email triage: what each email from the AP mailbox is, and where each of its attachments goes. | bench/emails, inbox, jde_worksheet, mailbox, web/files |
| `ui.py` | HTML building blocks for the dashboard (styled by web/static/app.css; templates call them as ``ui.*``). | azure_check, capture/workflow, taxmath, web/core, web/invoice_view, web/pages/accounts, web/pages/activity, web/pages/exports, web/pages/insights, web/pages/invoice, web/pages/learning, web/pages/month_end, web/pages/process, web/pages/purchase_orders, web/pages/review, web/pages/sales_tax, web/pages/settings, web/pages/settings_automation, web/pages/settings_jde, web/pages/settings_reading, web/pages/spend, web/pages/vendors |
| `validation.py` | Deterministic post-inference controls. | azure_check, capture/bridge, pipeline, web/invoice_view |
| `vendor_mail.py` | Draft an email asking the vendor for what an invoice is missing, from its open checks. | insights, web/invoice_view, web/pages/statements, web/pages/vendors |
| `vendors.py` | Vendor master and payment-fraud / duplicate signals. | accruals, audit, dupaudit, exports, jde, pipeline, search, statements, store, web/pages/vendors |
| `verifier.py` | The AI reviewer: the local chat model (Gemma 4, Qwen 3.5) checks a whole invoice against its pages, in the... | azure_check, capture/workflow, demo, page_worker, pipeline, reading, web/invoice_view, web/pages/invoice, web/pages/settings_reading |
