# ap_coder/web/pages: the dashboard's route modules, one per page

Every URL of the dashboard lives here: each module has an `APIRouter` called `router` that reads the store, calls the core modules and draws a template.
`__init__.py` holds `PAGES`, the one list of pages in rail order; `create_app` (web/app.py) includes each page's module from it.
The shared helpers (render, done/refuse, jobs, files) are in `ap_coder/web`; the business rules (approval, tax, exports) are in `ap_coder/`, not here.

**Depends on:** `ap_coder`, `ap_coder/capture`, `ap_coder/web`
**Used by:** `ap_coder/web`

## Interface
- `PAGES`, `MORE_MODULES`, `SECTIONS`, `BY_KEY`, `BANNER_PAGES` (__init__.py); `page()`: key -> `Page`(key, title, path, icon, section, subtitle, module).
- Each module's `router`; a GET draws `render(request, "<name>.html", "<page key>")`, a POST answers with done, message or refuse from web/core.py (JSON).
- `render_tab()`, `save_env()` (settings.py): draw a Settings tab; write .env changes, apply them to os.environ, log `settings_changed`.
- `reading_context()`, `automation_context()`, `jde_context()`: each Settings tab's template context. `card()` (azure.py): the Azure card, set as the `azure_check_card` template global.
- Shared between pages: `filter_queue()`, `sort_queue()` (review.py, used by invoice.py), `tax_types_mapped()` (process.py, used by accounts.py), `day()` (month_end.py, used by sales_tax.py).
- Jobs started here: "process" (process.py, also review.py's retry; polled at /process/status), "settings-doctor" (settings_reading.py, the developer build's Azure connection test), "azure-test" (azure.py). The self-test runs in page_worker, polled at GET /settings/reading/self-test.

## Gotchas and decisions
- A new page: add `module="<name>"` to its `Page` in `PAGES`; a page without a module gets the placeholder template.
- Routes with no rail entry go in `MORE_MODULES` ("invoice", "azure"), included first so their fixed paths beat `/settings/{tab}`.
- A typed amount: `safe.parse_amount()` (reads French decimals, refuses NaN and infinity); never a bare `float()` (B-52).
- The settings_*.py routers are not in `PAGES`: settings.py includes them into its own router (`_include_tabs`).
- `render()`'s page key highlights the rail: the invoice screen uses "review", the Azure check page "settings".
- Every POST needs the header `X-AP-Coder: 1` (checked by web/security.py before any route runs) and answers JSON, never an HTML page.
- The review screen's unsaved edits live in memory (`app.state.invoice_drafts`, invoice.py): a restart loses them; bulk approval skips those invoices.

### Bug lessons
From docs/BUGS.md (one line each; the entry has the repro and the fix).
- B-10: Don't call the store inside a row loop. `tests/test_web_review.py::test_the_review_and_export_pages_do_not_ask_the_database_once_per_invoice`
- B-21: Don't re-run gatekeeping checks when re-downloading something already committed. `tests/test_jde.py::test_a_jde_batch_downloads_again_with_the_same_vouchers`
- B-28: Don't serve a file for a record that has been undone. `tests/test_web_finance.py::test_an_undone_batch_cannot_be_downloaded`
- B-51: Don't call `int()` on a float parsed from user input without catching OverflowError. `tests/test_web_analytics.py::test_insights_assumptions_refuse_an_endless_invoice_count`
- B-54: Don't let a "not found" from the store escape a route that has already checked the item exists. `tests/test_web_robustness.py::test_approving_an_invoice_deleted_while_it_was_checked_says_so`
- B-55: Don't trust the template to hide a button; check the state in the route. `tests/test_web_review.py::test_retry_reads_again_only_an_invoice_that_failed`

## Files
- `__init__.py`: Page, PAGES (the rail), MORE_MODULES, SECTIONS, BY_KEY, BANNER_PAGES.
- `review.py`: /review (queue, welcome page), /review/bulk-approve, /review/sample-gl, /review/{id}/delete, reopen, retry, unpark, final-approve, send-back; key "review".
- `invoice.py`: /review/{id} (the review screen) and its /check, /approve, /park, /reject, /note, /teach, grid actions (/split, /apply-rules, /po-coding, /suggestion), /page/{n}.png, /words, /reader, /approved.pdf, /vendor, /read-pages, /start-over...; key "review".
- `process.py`: /process, /process/upload, /process/folder, /process/status, /process/open-folder, /process/inbox/..., /demo/load, /demo/remove; key "process".
- `search.py`: /search, /search/export.csv|.xlsx, /search/presets (save), /search/presets/default, /search/presets/delete (directory.py stores them), /search/{id}/approved.pdf, /search/{id}/unpark; key "search".
- `exports.py`: /exports, /exports/create, /exports/batches/{n}/download|approved-pdfs.zip|undo, /exports/layout(/preview), /exports/register/upload|import|clear, /exports/jde/dry-run.txt, /exports/duplicates.csv; key "exports".
- `month_end.py`: /month-end, /month-end/accruals.csv; key "month_end".
- `sales_tax.py`: /sales-tax, /sales-tax/claims.csv, /sales-tax/self-assessment.csv; key "sales_tax".
- `statements.py`: /statements, /statements/upload, /statements/emailed/{entry}/{n}, /statements/reconciliation.csv; key "statements".
- `vendors.py`: /vendors, /vendors/save, /vendors/master/upload|import; key "vendors".
- `purchase_orders.py`: /purchase-orders, its template.csv, upload, import, sample, status, delete; key "purchase_orders".
- `accounts.py`: /accounts, /accounts/{table}/upload|import|save, /accounts/{table}.csv, /accounts/tax, policy, rules, sample; key "accounts".
- `learning.py`: /learning, /learning/lessons/..., /learning/suppliers/..., /learning/memory/forget, /learning/history/..., /learning/training.zip; key "learning".
- `spend.py`: /spend, /spend/data.xlsx; key "spend".
- `insights.py`: /insights, /insights/business-case, /insights/assumptions; key "insights".
- `activity.py`: /activity, /activity/events.csv, /activity/controls-report, /activity/duplicates.csv; key "activity".
- `settings.py`: /settings (the Reading tab), /settings/{tab}, /settings/review (GET the tab via `review_context()`; POST reviewer and threshold; /approval: limit, terms, FX, why_after; /supply: provinces; numbers saved with `set_setting()`), /settings/data/... (open-folder, backup, backup-copy, backups/{name}, restore); key "settings".
- `settings_reading.py`: /settings/reading/... (reviewer, check, self-test, load-model, azure, azure-test); key "settings".
- `settings_automation.py`: /settings/automation/touchless, /settings/automation/limit; key "settings".
- `settings_jde.py`: /settings/jde, its gl-map, tax-map, an8, worksheet(.xlsx); key "settings".
- `azure.py`: /settings/azure-check (page, save) and /settings/azure-check/test; key "settings".
- `help.py`: /help; key "help".
