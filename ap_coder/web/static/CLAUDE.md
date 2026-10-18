# ap_coder/web/static: the dashboard's stylesheets

The CSS of the dashboard, served at `/static/<file>` by `create_app` (web/app.py). `app.css` is the "Ledger" look for every page; the other files add what a group of pages needs.
The page scripts are in `js/`. The fonts (Inter, Material Symbols) are in `ap_coder/static`, served at `/fonts`; the logo and icon are in `ap_coder/assets`, at `/assets`.
Nothing here is loaded from the internet, and nothing may be: the app is fully offline.

**Depends on:** `ap_coder/static` (the fonts that app.css's `@font-face` rules point to at /fonts)
**Used by:** `ap_coder/web/templates` (each `<link>`), `ap_coder` (ui.py builds the `.apc-*` classes app.css styles)

## Interface
- Design tokens `--lg-*` on `:root` in app.css (colours, type, radii); the other stylesheets use them, except invoice_viewer.css (below).
  A second `:root` in app.css maps `--rv-*` aliases onto them for invoice.css.
- Class families: `.apc-*` (the HTML of ap_coder/ui.py), `.rq-*` (review queue), `rvw-*`/`inv-*` (invoice.css), `iv-*` (invoice_viewer.css), `st-*` and `hp-*` (Settings, Help: settings.css), `an-*` (analytics.css), `dir-*` (data.css).
- Every `<link>` is `/static/<file>.css?v={{ static_version }}`: the hash of every file under this folder, made once when web/core.py is imported.

## Gotchas and decisions
- Edit a file and the `?v=` changes on the next server start; files without it (js/app.js, js/invoice_viewer.js) are checked again on every use
  (`Cache-Control: no-cache`, web/app.py). A running server keeps the old `?v=`.
- Load a stylesheet with `?v={{ static_version }}` in the template's `head` block, never without it.
- No `@import`, no web fonts or images from a URL: `url()` only points at /fonts or /assets on this server.
- invoice_viewer.css has its own copies of the Ledger colours (`--ink`, `--muted`, `--line`... on `.iv, .iv-tip`: the hover card lives on `<body>`): change a colour there too.
- ap_coder/ui.py repeats the hex values of `--lg-success` / `--lg-warning` / `--lg-danger` / `--lg-accent` (OK, WARN, ERR, BRAND): change a colour in both.

### Bug lessons
From docs/BUGS.md (one line each; the entry has the repro and the fix).
- B-57: Don't anchor a fixed-width popover to one side of its button without keeping it on screen. `tests/test_web_invoice.py::test_in_a_browser_the_popovers_open_inside_a_phone_screen`
- B-59: Don't add dark-mode colours for a few elements on a page that has no dark theme. `tests/test_web_invoice.py::test_in_a_browser_os_dark_mode_leaves_the_light_page_readable`

## Files
- `app.css`: tokens, base layout (rail, header, cards, buttons, forms, flash, popovers), the .apc-* components; loaded by base.html on every page.
- `analytics.css`: filters, charts, small bars; loaded by activity.html, insights.html, learning.html, spend.html.
- `data.css`: tables and imports of the finance and master-data pages; loaded by accounts, exports, month_end, purchase_orders, sales_tax, search, statements, vendors (.html).
- `invoice.css`: the review screen (rvw-*, inv-*); loaded by invoice.html.
- `invoice_viewer.css`: the page viewer of js/invoice_viewer.js (iv-*); loaded by invoice.html.
- `settings.css`: Settings tabs and Help; loaded by settings.html and help.html.
- `js/`: the page scripts (own CLAUDE.md).
