# ap_coder/web/templates: the dashboard's Jinja templates

The HTML of every page, drawn on the server by `render()` in web/core.py (one Jinja environment, `templates`, autoescape on).
Each page template extends `base.html`, which draws the rail, the header, the messages and loads `app.css` and `js/app.js`.
The data comes from the route in `ap_coder/web/pages`; the HTML builders called as `ui.*` are in ap_coder/core/ui.py; styles and scripts are in `ap_coder/web/static`.

**Depends on:** `ap_coder/web` (core.py's globals: ui, static_version, version; filters money, plural, time_ago, initials, num, pct), `ap_coder/web/static`
**Used by:** `ap_coder/web/pages` (each route renders one), `ap_coder/web` (app.py renders error.html and placeholder.html)

## Interface
- base.html blocks: `title`, `head` (a page's CSS), `header`, `heading`, `subtitle`, `aside`, `content`, `scripts` (a page's script). Context from `render()`: page, meta, title, rail, reviewer, flashes, banner, settings...
- Macros other templates import: `_sheet.html` (upload_form, preview, columns), `_tiles.html` (tile_row), `_invoice_live.html` (conf, head, summary, status_block, checks, amounts, reasons, suggest, po, taxcheck, posting_table, posting, bar, reader), `_invoice_lines.html` (line_rows, tax_rows...).
- pages/invoice.py also calls `_invoice_live.html` and `_invoice_lines.html` macros alone (`.module`) to answer /check, the grid actions and /reader with HTML pieces.

## Gotchas and decisions
- Autoescape is on: never `|safe` or `Markup` text from an invoice or a file. `ui.*` (only the `_UI_HTML` names in core.py) and the charts escape their input.
- A change is a `<form method="post">`; app.js sends it with `X-AP-Coder: 1`. Add `data-confirm="..."` to ask first. A GET form only filters or sorts.
  Except: invoice.js posts /check, /teach, /vendor and the grid actions itself, and exports.js the layout preview.
- Nothing from the internet: CSS and scripts come from /static, fonts from /fonts, the icon from /assets. The Azure URLs in settings_azure.html and partials/azure_check.html are placeholder text.
- Load a page's CSS or script with `?v={{ static_version }}`; only js/app.js and js/invoice_viewer.js have none (imported as "./app.js" and
  "./invoice_viewer.js", the same URL); every static file is served `Cache-Control: no-cache`, so a browser checks them again (B-111).
- A new page template needs its `Page` in pages/__init__.py PAGES, so `render()` finds its title and rail entry.
- Settings tabs are `settings_<tab>.html`, included by settings.html from the tab key: a new tab needs its file, an entry in pages/settings.py TABS, its context in `settings_tab()`, and static/js/settings.js TABS (old #tab links).
- sales_tax.html says "$100 or more ($30 before 2021)": keep it in step with taxreturn.document_threshold (B-19, tests/test_taxreturn.py::test_registration_number_needed_from_100_dollars_since_2021).

### Bug lessons
From docs/BUGS.md (one line each; the entry has the repro and the fix).
- B-112: Don't let a form whose submit button approves be submitted by Enter in a text field. `tests/test_web_invoice.py::test_in_a_browser_enter_in_a_review_field_never_approves`

## Files
- `base.html`: the layout every page extends (rail, header, flashes, banner, toasts, app.js).
- `error.html`: 404 and other errors for a browser (app.py). `placeholder.html`: a page with no module yet (app.py).
- Work: `review.html`, `review_welcome.html` (GET /review before any invoice or GL accounts; pages/review.py); `invoice.html` (GET /review/{id}, pages/invoice.py); `process.html` (/process); `search.html` (/search).
- Close and compliance: `exports.html` (/exports), `month_end.html` (/month-end), `sales_tax.html` (/sales-tax), `statements.html` (/statements).
- Master data: `vendors.html` (/vendors), `purchase_orders.html` (/purchase-orders), `accounts.html` (/accounts).
- Analytics: `learning.html` (/learning), `spend.html` (/spend), `insights.html` (/insights), `activity.html` (/activity).
- System: `help.html` (/help); `settings.html` (/settings, /settings/{tab}, pages/settings.py render_tab), `azure_check_page.html` (/settings/azure-check, pages/azure.py).
- Settings tabs, included by settings.html: `settings_reading.html` (includes `settings_azure.html`, the developer-only cloud form, and partials/azure_check.html), `settings_automation.html`, `settings_review.html`, `settings_jde.html`, `settings_data.html`, `settings_about.html`.
- Partials and macros (the _*.html files): `_sheet.html` (spreadsheet import steps; accounts, exports, purchase_orders, statements, vendors), `_tiles.html` (KPI row), `_demo_card.html` (demo invoices; process, review_welcome).
- Review screen pieces: `_invoice_document.html` (page viewer card), `_invoice_notes.html` (notes card), `_invoice_live.html` (sections redrawn on each edit), `_invoice_lines.html` (line and tax grids).
- `partials/`: the Azure check card (own CLAUDE.md).
