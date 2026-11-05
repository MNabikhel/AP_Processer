# AP Coder web front-end: notes for the 36-hour goal

Reader: web front-end (templates, static JS/CSS, how each page's handler feeds them). Code at `main` af9925b.
Paths are relative to `/home/user/AP_Processer/ap_coder/web/` unless they start with `tests/`, `docs/` or `ap_coder/`.
"Verified" means I read the code path end to end, or ran it (TestClient on the demo data: 10 invoices, 8 vendors,
25 GL accounts). "Suspected" means it follows from the code but I did not reproduce it.

Measurement scripts (throwaway, not in the repo): `scratchpad/probe_pages.py`,
`probe_check.py`, `probe_prof2.py`, `probe_sizes.py`, `fe_scripts/probe_nav.py`, `fe_scripts/deadcss.py`, `fe_scripts/dupcss.py`
(all under that scratchpad folder; run with `.venv/bin/python -I`).

---

## 0. Inventory

- Templates: 48 files, 5,549 lines. 27 extend `base.html`; `login.html` and `login_step.html` are standalone; the rest
  are partials or macros (`_invoice_live.html`, `_invoice_lines.html`, `_invoice_document.html`, `_invoice_notes.html`,
  `_second_approval.html`, `_sheet.html`, `_tiles.html`, `_next_step.html`, `_setup_steps.html`, `_demo_card.html`,
  and the `settings_*.html` tab bodies).
- JS: 13 ES modules, 6,155 lines, 274 KB, plain JS with no framework and no build step. The big ones are `invoice.js`
  (2,621 lines, 117 KB), `invoice_viewer.js` (1,739 lines, 77 KB), `picker.js` (688 lines, 28 KB), `ctxmenu.js` (372),
  `app.js` (292), `process.js` (142).
- CSS: 8 files, 2,234 lines, 152 KB: `invoice.css` 52 KB, `app.css` 49 KB, `invoice_viewer.css` 26 KB, `data.css`
  10 KB, `settings.css` 9 KB, `picker.css` 4 KB, `analytics.css` 3.5 KB, `ctxmenu.css` 1.8 KB.
- **No vendored libraries.** There is no pdf.js: pages are drawn to PNG on the server (`document.py`, `page_png`; 120
  dpi at scale 1, up to 360 dpi at scale 3). Charts are server-side SVG (`charts.py`). The QR code is server-side
  (`qrcodegen.py`).
- Fonts (`ap_coder/static/`): `MaterialSymbolsRounded.woff2` **373 KB** (the full icon font, `font-display: block`,
  app.css:424) and `InterVariable.woff2` 48 KB.

## 1. Map: page, template, JS, what it does

Every page loads `app.css`, `picker.css`, `nav_more.js` (classic script), `app.js` and `picker.js` (modules) from
base.html:9-10, 38 and 112-113. Every page also gets the rail with its count badges (`core.py` `nav_counts`, about
line 489), `approved_today` and `demo_count`. The handler is `pages/<module>.py`.

| Path | Template | Page JS / CSS | What it shows | Data the handler passes |
|---|---|---|---|---|
| `/review` | review.html | queue.js; - | Review queue with tabs: To review, Second approval, Parked, Approved (100 newest), Failed/rejected. KPIs, "Today" pills, bulk approve, search, sort | review.py:377-457: every REVIEW/PARKED/PENDING invoice, `issues_today` and `attention` over the whole pending queue, `queue_rows(shown[:limit])` (limit 50, +50 per "Show more", max 5,000), bulk candidates (all) |
| `/review/{id}` | invoice.html + `_invoice_live/_lines/_document/_notes/_second_approval` | invoice.js (imports ctxmenu.js, invoice_viewer.js, picker.js); invoice.css, invoice_viewer.css, ctxmenu.css | The workspace: page viewer, checks, header fields, line grid with GL/CC pickers, tax lines, GL posting preview, PO, suggestions, notes, approve bar. Otherwise a read-only state card (pending/parked/approved/rejected/failed/nogl) | invoice.py:517-582: inv, nav (loads the **whole** review queue for Prev/Next, 274-301), all events, the draft, `view.analyse(...)`, grid (chart of accounts, cost centers, tax-code labels), the **whole vendor list with tax numbers** (`vendor_options`, 600-611), viewer data (pages, fields, boxes) |
| `/process` | process.html | process.js | Import: upload with progress, folder pick-up, emailed inbox, the global "process" job card | process.py:694+ |
| `/search` | search.html | directory.js; data.css | Invoice directory: per-column filters, sort, choosable columns, presets, CSV/XLSX export | search.py:99-128: `directory.run` (SQL, **100 per page**, erp/directory.py:39) |
| `/exports` | exports.html | exports.js; data.css | Ready-to-export list with checkboxes, batches, custom layout and live preview, JDE API | exports.py:237+: **all** unexported approved, **all** batches |
| `/accounts` | accounts.html | accounts.js; data.css | Chart of accounts, tax treatments, coding rules, bank accounts (editable tables with `version` tokens) | accounts.py:178-207 |
| `/manager`, `/manager/{id}` | manager.html | - | Manager confirmations (D-138) | manager.py:67-93 |
| `/month-end` | month_end.html | -; data.css | Accruals | month_end.py:51 |
| `/sales-tax` | sales_tax.html | -; data.css | ITCs/ITRs, self-assessment | sales_tax.py:49 |
| `/statements` | statements.html | -; data.css | Vendor statement reconciliation | statements.py:76 |
| `/vendors` | vendors.html | -; data.css | **All** vendors with spend and controls; a vendor's detail | vendors.py:203-267 |
| `/purchase-orders` | purchase_orders.html | -; data.css | **All** POs (filtered in Python) | purchase_orders.py:170-217 |
| `/learning` | learning.html | -; analytics.css | Accuracy, lessons, memory (capped: 50/300/12) | learning.py:505 |
| `/spend`, `/insights`, `/activity` | spend/insights/activity.html | -; analytics.css | Analytics. Activity reads the **newest 5,000 events** (activity.py:51) | |
| `/settings/*` | settings.html + settings_*.html | settings.js; settings.css | Settings tabs; self-test progress poll | settings.py:121-126 |
| `/help`, `/help/report`, `/help/quick-start` | help*.html | help.js; settings.css | Help (122 KB page), client-side check filter, report a problem | help.py |
| `/login`, `/login/*`, `/logout`, `/account/password`, `/admin/users`, `/who` | login.html, login_step.html, logout.html, account_password.html, admin_users.html, who.html | app.js only | Server-mode accounts (D-97, D-146) | login.py, users.py, who.py |

## 2. Network behaviour

### 2.1 Polling (verified by reading)

| Where | Interval | Endpoint | Stops when hidden? | On errors |
|---|---|---|---|---|
| `app.js:109-124` `pollJob` (all job cards) | 800 ms fixed | `/jobs/<name>`, `/process/status` | **No** (no `visibilitychange` anywhere in static/js) | `catch` gives `snap = null`, so it **loops forever every 800 ms**, with no backoff |
| `process.js:80-88` `follow_job` | 800 ms, then loops again while `waiting` | `/process/status` (one global "process" job for the whole company) | No | as above; reloads the page when done (`location.reload()`) |
| `settings.js:13-20` self-test | `data-every` (5 s) | self-test status | No | as above; reloads |
| `invoice.js:1279-1298` OvisOCR2 reader | max(5, data-poll) s; the server sets 20 s (invoice.py:81) | `/review/{id}/reader` (returns server-rendered HTML) | No | swallows the error and polls again forever; `location.reload()` when the reading ends |
| `exports.js:31-46` layout preview | 350 ms debounce on input | POST `/exports/layout/preview` | n/a | ignores stale answers (good) |
| `invoice.js:110-143, 164-169, 197-212` **edit check** | debounce 450 ms on typing, 200 ms on change, 0 ms on select or checkbox | POST `/review/{id}/check` with the **whole form** | n/a | one in flight at most (`running` plus `queued`), which is good |

- **Session expiry plus polling (verified by reading):** a GET without a session gets a 303 to `/login?next=...`
  (identity.py:327-329) whatever `Accept` says. `fetch` follows the redirect, receives the login page's HTML, and
  `response.json()` throws, so `pollJob` spins every 800 ms forever, and each spin renders the login page on the server.
  The same happens on `/process/status`. The reader poll does the same every 20 s.
- **Polls keep sessions alive (suspected).** Every poll is an authenticated request that refreshes `last_seen`
  (users.py, `SEEN_EVERY` 60 s), so an unattended tab that is polling never reaches the 30-minute idle timeout
  (users.py:69).
- Everyone watching `/process` polls the one global job and reloads together when it ends.

### 2.2 Fetches on each page load

- **Invoice screen (verified by running):**
  - GET HTML: about 100 KB on demo data.
  - About 11 static revalidations: 5 CSS and 6 JS, all `no-cache`.
  - Page PNG: 84 KB on a demo page at scale 1, 280 KB at scale 3. document.py:36 says "a scan at scale 3 alone can be
    10-15 MB".
  - **An opening POST `/check`** (invoice.js:2621, `check().then(focusFirstToCheck)`). It repeats the analysis the
    GET has just done, and writes to the database (`refresh_confidence`, `_keep_draft`). Measured: the first check on
    a freshly opened invoice took 167-580 ms, against 20-98 ms for the GET; `save_invoice_draft` alone took 53 ms
    (probe_prof2.py). Later checks took 16-21 ms each on demo data.
  - `/review/{id}/words` is fetched only on teach or drag (invoice.js:920-927, about 7 KB).
- **Every other page:** HTML plus about 6 static revalidations.
- **Queue double load (verified):**
  - Queue rows link to a bare `/review/{id}` (review.html:87).
  - So the invoice's "Queue" link is a bare `/review`.
  - On arrival, queue.js:50-52 does `location.replace('/review' + mark.search)` whenever the view had a filter, sort
    or search. That is two full renders of the heaviest page.
- After Approve: the server answers `{redirect: next}` and the next invoice's GET runs, and that GET loads the whole
  queue again (`navigation`).

### 2.3 Page weight and caching

- **Static files are cache-busted but still revalidated on every view.**
  - `?v={{ static_version }}` is a hash of every static file (core.py:102-108).
  - But `/static` is served by `CheckedStaticFiles` with `Cache-Control: no-cache` (app.py:62-71), because `app.js`,
    `picker.js`, `ctxmenu.js` and `invoice_viewer.js` are imported by relative URL without `?v=` (base.html:107-113,
    invoice.js:21-24).
  - So each view costs 6-11 conditional requests answered 304.
  - Each one passes the whole middleware stack, and in server mode the access log writes a line synchronously on the
    event loop for each (security.py:402).
  - At 400 people clicking every 10 s, that is about 40 page requests a second plus roughly 250-450 revalidations a
    second.
- `/fonts` and `/assets` use plain `StaticFiles` (app.py:280-281): no Cache-Control, so the browser falls back to
  heuristic caching on Last-Modified.
- **No compression.** Nothing in `ap_coder/web` adds gzip (grep for GZip/compress finds nothing). HTML, JSON and the
  static files go out raw unless IIS or another proxy in front compresses them.
- First load of the invoice screen, uncompressed:
  - About 385 KB of CSS and JS.
  - 421 KB of fonts.
  - The HTML, then about 100 KB per later view on demo data.
- **Page images are never cached in server mode (verified by reading).**
  - `page_picture` sets `private, max-age=86400` (invoice.py:709), and the URL carries `?v=<file version>` so that it
    can be cached.
  - But in server mode `Identity` wraps every non-OPEN response in `_no_store` (identity.py:239-247, 361), which
    replaces it with `no-store`. The comment there says this is on purpose ("the invoices' page images too").
  - So every open, and every zoom past 125% or 250%, downloads the PNG again.
  - The server keeps a 96 MB picture cache per worker process (document.py:36), so with 8 workers there are 8 caches.
- HTML pages are `no-store` (core.py:617-619), on purpose, so Back shows the current state.

### 2.4 Large inline data, invoice screen (measured on demo data, then projected)

| `<script type="application/json">` | Demo size | Per item | At 3,000 vendors / 1,500 GL / 300 CC |
|---|---|---|---|
| `#vendor-data` (invoice.html:169): every vendor with GST/QST/PST and ERP id | 1.3 KB / 8 | 165 B | **about 500 KB** |
| `#gl-options` (invoice.html:236) | 1.2 KB / 25 | 49 B | about 75 KB |
| the same chart again as `<option>`s in "Apply to all lines" (invoice.html:271) | 25 options | about 60 B | about 90 KB |
| `#cc-options` plus the fill-cc `<option>`s (invoice.html:237, 275) | 0.6 KB / 11 | 56 B | about 35 KB |
| `#tax-code-labels` (invoice.html:286): identical for every invoice | 7.8 KB / 147 | 54 B | 7.8 KB |
| `#viewer-data` (`_invoice_document.html:52`): fields, boxes, line boxes | 6 KB | | grows with lines |

- Projected: **about 0.7-0.8 MB of HTML per invoice view**, rebuilt on each GET.
  - `vendor_options` runs `vendor_key()` (a fold) over every vendor on each request (invoice.py:600-611).
  - The page is `no-store`, so the browser never reuses it.
- **Coding rules (accounts.html:284-297):** every rule row prints the **whole chart of accounts** as `<option>`s, and
  every one of those selects becomes a Picker.
  - 200 rules × 1,500 accounts is about 300,000 `<option>` elements, about 16 MB of HTML (an estimate; the template
    is verified).
  - The line grid avoids this with `data-options` (D-56, `_invoice_lines.html:8-14`). The rules table does not.

### 2.5 Pagination: what renders everything

| List | Behaviour |
|---|---|
| Queue, To review | Draws 50, "Show N more" reloads with +50 (review.py:69, 404-411). The **server still computes issues and attention for every pending invoice** on each load, and again on each invoice open (`navigation`) |
| Queue: Failed/rejected, Parked, Second approval | **All**, unbounded (review.py:445-455, review.html:143-222) |
| Queue: Approved | 100 newest (review.py:70) |
| Bulk-approve popover | **All** clean candidates, as a table plus one hidden input per id (review.html:59-72); approved in **one synchronous request** (review.py:526-569) |
| Invoice directory | SQL, 100 a page, Prev/Next (search.html:179-184); export up to 50,000 rows synchronously |
| Exports | **All** ready invoices with checkboxes; all batches (exports.html:38, 175, 191) |
| Activity | Reads the **newest 5,000 events**, then filters in Python (activity.py:51, 147). The timeline shows 200 and the table 1,000. **At about 1,250 invoices a day and several events each, 5,000 events is less than a day**, so search, the person filter and the "this week" tile (activity.py:196) silently cover only that window (verified by reading) |
| Vendors, POs, month-end accruals, Users (400 people × 5 forms each, no search), Process inbox files | **All** |

## 3. Scale-related UI

### 3.1 Two people on one invoice (verified by reading, plus the existing tests)

- **No locking, no claim, no presence.**
  - Drafts are per person (invoice.py:86-139). Both people edit their own drafts.
  - Approve does a compare-and-set on status (`UPDATE ... WHERE id = ? AND status = ?`, core/store/invoices.py:690-693).
  - The loser gets "Not done: this invoice changed meanwhile, in another tab or by someone else. It is approved by X."
    (invoice.py:329-339; tested in tests/web/test_web_robustness.py:61).
- **Wasted work, not corruption.**
  - B learns that A approved the invoice only when B's next edit check gets a 409 (`NOT_IN_REVIEW`, invoice.py:243-251).
  - That arrives as a toast, "This invoice is no longer in the review queue: reload the page.", **repeated on every
    later edit**. The screen stays editable.
- **Collisions are likely at scale.** Prev/Next and "the next one after approve" follow the same queue order for
  everyone (`navigation`, invoice.py:274-301; sort "priority"), so clerks working the top of the queue land on the same
  invoices.
- **The presence data already exists.**
  - `invoice_drafts(invoice_id, person, draft, updated_at)` (core/store/invoices.py:529-567) knows who has edits on
    which invoice, and since when.
  - Only bulk approval uses it (`unsaved_edits`, review.py:550). The queue shows only your own drafts
    (`draft_reasons`).
- **One person in two tabs on the same invoice: silent lost update (verified by logic).** Each check posts the whole
  form and replaces the person's draft (`view.Draft.from_form`), so the tab that checks last wins. There is no draft
  revision.
- Optimistic `version` tokens exist on Accounts' tables (accounts.html:67, 170, 235) and Settings, Review
  (settings_review.html:4). They are **absent from** vendors/save, the Settings JD Edwards tables (GL map, tax map,
  terms, AN8), PO status and automation: last write wins there (suspected).

### 3.2 Search and filters

- Queue: `q` matches vendor, invoice number and file name, folded for case and accents (review.py:80). Filters are
  show (all, attention, ready) and five sorts. There is **no filter** by vendor, amount, date, province, currency,
  "mine" or "assigned".
- Directory: per-column filters, SQL, paginated. This is the good one.
- Activity: only within the newest 5,000 events (see above). Vendors and POs: substring or regex over everything in
  Python. Help: client-side filter.
- **Verified bug: the queue's view is lost when an invoice opens.**
  - `NAV_PARAMS = ("show", "q", "sort")` is "carried along so Previous / Next follow it" (invoice.py:82), but the
    queue's rows never carry it (review.html:87).
  - Measured (probe_nav.py): on `/review?sort=amount` the top row opens as "Reviewing **6** of 8", and Next goes to
    `/review/10` (priority order) instead of `/review/5` (next by amount).
  - `tests/web/test_web_invoice.py:500` tests the invoice URL **with** `?sort=amount`; nothing tests the link the queue
    actually gives.

### 3.3 Who is working on what

- Nothing live. Activity shows it after the fact. The rail foot shows "N approved today" for yourself only.
  Second approval shows who approved first.

## 4. Efficiency

- **Per edit:**
  - The whole form (about 87 fields and 2 KB on a 6-line invoice; "thousands of fields" on long ones, B-455) goes up.
  - The server answers with **all 10 live sections as HTML** (22-31 KB measured, invoice.py:471-477), even the ones
    that did not change.
  - `applySections` replaces the `innerHTML` of each section (invoice.js:68-97).
  - The body-wide `MutationObserver` in picker.js:683-688 then runs `enhance()` (a `querySelectorAll`) on every added
    node.
  - The server side runs `view.analyse` again and may write the draft.
  - **In multi-worker server mode** `_holder` makes every request reload and JSON-parse **every draft of every person**
    (`store.invoice_drafts()`, invoice.py:101-128), so each keystroke check costs O(all drafts).
- **Listeners:** invoice.js adds 32 document or window listeners: 6 keydown, 6 click, 4 input, 3 change. The viewer,
  ctxmenu, picker and app.js add 17 more. Each is registered once per page load, so nothing leaks. But keyboard
  handling is spread over about 10 keydown handlers that coordinate through `defaultPrevented` and listener order (many
  B-numbers about keys came from this).
- **PDF viewer memory:**
  - One `<img>`. Sharper pictures are preloaded with `new Image()` and swapped in (invoice_viewer.js:1062-1084).
  - It is built once and never destroyed, which is fine because every navigation is a full page load.
  - Three ResizeObservers and window listeners live for the life of the page.
  - No leak found. The cost is the scale-3 images: 360 dpi PNGs, 10-15 MB for scans by the code's own comment
    (document.py:36), decoded to about 50 MB RGBA. PNG is a poor format for scans; JPEG or WebP would be 5-10 times
    smaller.
- `#viewer-data` is parsed twice (invoice.js:41 and 988-994). This is minor.
- Pickers are built eagerly: 2 per line on load (GL and CC, picker.js:187-233), plus one per `data-picker` select on
  Accounts (see 2.4). `render()` caps the list at 400 drawn items (`MOST`, picker.js:42), which is good.

## 5. Cleanliness

**Giant functions** (brace-counted with fe_scripts):

| Function | Location | Lines |
|---|---|---|
| `InvoiceViewer.build` | invoice_viewer.js:230 | **202** |
| `Picker.render` | picker.js:337 | 109 |
| `chooseFor` | invoice.js:1090 | 106 |
| `InvoiceViewer.renderPanel` | invoice_viewer.js:549 | 97 |
| `ctxmenu build` | ctxmenu.js:79 | 63 |
| `Picker.key` | picker.js:515 | 61 |
| `renderPage` | invoice_viewer.js:742 | 59 |
| `update` | invoice_viewer.js:435 | 58 |
| `tipContent` | invoice_viewer.js:667 | 54 |

- `invoice.js` is one 2,621-line module with about 150 top-level functions and state in module globals (`timer`,
  `running`, `edited`, `undoState`, `vendorAccounts`, ...).

**Dead JS (verified):** `InvoiceViewer` still carries its Streamlit-era "Python" protocol.
- `update(args)` handles `args.selected` and `args.teach` with echo suppression: `ECHO_MS`, `sentSelects`,
  `argSelected`, `argTeach` (invoice_viewer.js:42, 214-217, 469-487).
- `emit` stamps `seq`/`lastSeq` (1623-1629).
- `reportHeight() {}` is empty and called about 12 times (1633).
- The server sends only `pages`, `fields`, `line_items` and `height` (invoice.py:365-378), and `update` is called
  once (invoice.js:41).

**Dead CSS:** a token-level scan found nothing clearly dead. The 16 "unreferenced" classes are composed at run time
(`t-${tone}`, `s0`-`s5`, `n1`-`n3`, `cols-1`). The visually-hidden rule is copied three times under two names:
`.sr-only` in analytics.css:4 and data.css:89, `.rvw-sr` in invoice.css:23. `.sr-only` is missing from app.css, so it
works only on pages that load those sheets.

**Inline scripts and styles:** no executable inline scripts (CSP-enforced in server mode, security.py:189-196; tested
in tests/web/test_server_https.py and test_server_ux.py:301). No inline handlers. 10 `style="..."` across templates,
all data-driven bar widths. Clean.

**Inconsistent patterns:**
- Four ways to send a request:
  - `app.js` `submitForm` (fetch, data-busy, double-send guard);
  - `process.js` XHR upload with its own error text (process.js:103);
  - `invoice.js` `sendNote` with its own guard (invoice.js:2326-2360);
  - `exports.js` preview.
- Three polling loops: `pollJob`, the reader `setTimeout` loop, and `follow_job` wrapping `pollJob`.
- Four "is the user typing?" helpers with different rules:
  - invoice.js:1313 `typing`;
  - invoice_viewer.js `isTyping` (excludes only checkbox);
  - app.js:287 inline;
  - picker's.
- Two hyperscript helpers: invoice.js:1216 `el` and invoice_viewer.js:83 `h`.
- `data-add-row` means different things in settings.js:39 and invoice.js:260.
- Times: the client's "Draft saved 16:05" uses a 24-hour clock (invoice.js:159-162), while the server says "today at
  4:05 p.m." (invoice.py:584-597).

**Template duplication that should be macros or a base:**
- The second-approval block (Final approval plus Send back) appears in review.html:207-219 and invoice.html:415-427.
- The delete form appears 4 times (invoice.html:367, 460, 473; review.html:164).
- `{% for k, v in query.items() %}<input type=hidden>` appears 3 times in invoice.html (79, 331, 359).
- The flash markup is built in 4 places: base.html:89-101, login.html, login_step.html, and app.js:29-54
  `messageElement`.
- login.html and login_step.html duplicate the whole `<head>` and flash block, so they want a `_bare.html` base.
- The literal 50 in review.html:108 duplicates `QUEUE_PAGE`.

## 6. Accessibility and usability at a glance

- Good:
  - a skip link;
  - `lang`;
  - labels are mostly implicit `<label>` wrappers, plus 160 `aria-label`s;
  - grid cells say "Line 3 GL account";
  - the picker is a combobox/listbox with `aria-activedescendant`;
  - aria-live regions for toasts, checks and the tax check;
  - the viewer's statuses carry glyphs, not colour alone;
  - prefers-reduced-motion is honoured (app.css, ctxmenu.css, invoice_viewer.css);
  - keyboard shortcuts are documented on screen.
- Issues:
  - **Queue rows** are `<a aria-label="Review invoice from {vendor}">` (review.html:87). The aria-label replaces the
    row's content, so a screen reader hears no amount, date, status or confidence. The column header is `aria-hidden`
    and the list is a `<ul>`, not a table.
  - **Single-key shortcuts** (P, X, G, N, H, R, +, -, 0, 9) have no off switch (WCAG 2.1.4, suspected). They are
    ignored while typing in a box, which limits the risk.
  - "Show more" reloads the page at the top, so the keyboard or scroll position is lost (unlike Back, which queue.js
    restores).

## 7. Security in the front-end

- **XSS: verified safe by reading.**
  - Jinja autoescape is on (core.py:119).
  - `|safe` is used once, on a literal (invoice.html:376).
  - The `Markup` builders in core/ui.py, charts.py and minimd.py escape their text. The only raw interpolations are
    class names and sizes from code.
  - JS `innerHTML` uses (app.js:35, 44; exports.js:38; process.js:23, 62) go through `escapeHtml`.
  - invoice.js:75, 316, 1291 and 2350 insert server-rendered, autoescaped HTML.
  - invoice.js:266, 1201 and 1460 use the page's own `<template>`.
  - invoice.js:1414/1479 re-insert an `outerHTML` taken from the DOM.
  - The viewer sets text only.
  - Browser storage holds only UI preferences: zoom, the field list folded, the More menu, and the queue's search
    string.
- **CSRF: verified good.**
  - Every POST needs `X-AP-Coder: 1` (which a cross-site form cannot send without a CORS preflight that is never
    granted).
  - Origin and Sec-Fetch-Site must be same-origin (security.py:120-130, 176-182).
  - The session cookie is `__Host-`, SameSite=strict (identity.py:56; login.py).
  - There is no token to leak. The cost: forms do not work without JS (by design).
- **Hidden buttons versus server checks: verified that the server enforces.** Templates hide with `can()` (core.py
  `can`), but every route has a role rule enforced in the `Identity` middleware (`roles.need_for`, identity.py:316-353;
  tests/web/test_roles.py fails a route without one). Per-object checks are in the handlers: manager requests are the
  manager's own (manager.py:106-115), and undo-approve is your own approval unless you are an Admin
  (invoice.py:951-954). Client-only guards are UX, not security:
  - the Delete "Yes" checkbox (invoice.html:369; the server does not require it);
  - typing RESTORE (the server checks again).
- The CSP is sent only in server mode (security.py:212-216). Fine for a laptop.
- `post()` saves attachments under the server's filename (app.js:80-89). Browsers sanitise download names, so this is
  fine.

## 8. Possible bugs

| # | What | Status | Where |
|---|---|---|---|
| 1 | The queue's sort, filter and search are lost when an invoice opens. "Reviewing 6 of 8" and Next follow priority order, not the view, and the same goes for the next invoice after Approve | **Verified (ran it)** | review.html:87 row href; invoice.py:82, 274-301 |
| 2 | The queue loads twice when you come back from an invoice opened in a filtered or sorted view (bare Queue link, then `location.replace`) | **Verified (reading; follows from 1)** | queue.js:48-56; invoice.html:18 |
| 3 | `pollJob` loops forever every 800 ms when answers are not JSON (expired session leads to the login HTML) or the server is down, rendering the login page on each spin | **Verified (reading)** | app.js:109-124; identity.py:327-329 |
| 4 | Session ends mid-edit: each later edit toasts "You are not logged in..." (edits after that point are not saved as drafts). Approve then sends you to `/login` with **no `next`**, and after logging in you land on the queue, not the invoice | **Verified (reading)** | identity.py:330; app.js:98-106; invoice.js:122-127 |
| 5 | Network-failure text is laptop wording on the server: "Is it still running? Start it again with the AP Coder shortcut." | **Verified** | app.js:77; process.js:103 |
| 6 | The client's "Draft saved 0:37" uses a 24-hour clock: B-997's symptom, fixed server-side only | **Verified (reading)** | invoice.js:159-162 vs invoice.py:584-597 |
| 7 | One person, two tabs, same invoice: the tab that checks last silently overwrites the other's edits | **Verified (logic)** | invoice.js:110-143; invoice.py:780-789 |
| 8 | After someone else approves, every edit on the stale screen toasts "reload the page" again; the screen stays editable | **Verified (reading)** | invoice.py:243-251; invoice.js:126 |
| 9 | `pagehide` keepalive POST: browsers refuse keepalive bodies over 64 KB, so on a long invoice (about 250 or more lines) the last edit before closing the tab is lost silently | Suspected | invoice.js:1382-1387 |
| 10 | No fetch timeouts. A link or Alt+arrow clicked while a check is in flight waits for it (`flush`). On a server answering in 22 s the page feels frozen, with no way out but reload | Verified (reading) | invoice.js:248-255, 1374-1379; app.js:72-95 |
| 11 | Bulk approve of hundreds runs in one synchronous request with no progress; a proxy timeout (IIS ARR defaults) can cut it while it goes on server-side | Suspected | review.py:526-569 |
| 12 | Activity's search, person list and "this week" tile cover only the newest 5,000 events, about a day at the target volume | **Verified (reading)** | activity.py:51, 147, 196 |
| 13 | Back after form-restoring navigations: the browser may restore field values into the re-fetched invoice page, and the opening check then saves them as the draft | Suspected (worth one browser test) | invoice.js:2621 |

## 9. Test gaps

- About 187 browser tests (Playwright plus Chromium, marked slow; tests/browser.py; run in CI's browser job). Most are
  in test_web_ctxmenu (24), test_web_picker (20), test_web_viewer (19), test_web_invoice (19), test_web_review_signoff
  (13+11), test_web_pilot_walkthrough (11) and test_web_review_voucher (9).
- One slow-network test exists: test_web_picker.py:484 routes `/check` slow or failed. Double send is covered for
  notes (test_web_ctxmenu.py:750, B-625). A CSP-in-a-browser test exists (test_server_ux.py:301).
- Missing:
  - **Two browser contexts as two accounts on one invoice** (presence, the stale screen, the next-invoice collision).
    The robustness tests do this with TestClient only.
  - **Two tabs, one person** (lost update).
  - **Session expiry in a browser** (401 on check, then approve, then login, then back to the invoice, edits kept).
  - **Polling:** stops when the tab is hidden, backs off on 5xx, stops on a login redirect. Nothing tests polling
    behaviour in a browser; test_web_settings.py:195 checks only that the page polls.
  - **The queue's row links carry the view** (bug 1), and Back from an invoice makes one navigation (bug 2).
  - **Page weight and request budgets:** HTML size per invoice view with a realistic vendor master and chart, number
    of requests per view, Cache-Control on static files.
  - The `pagehide` keepalive on a long invoice; Back after Approve or Park with form restoration.
  - Accessibility: there is no automated a11y check (an axe-like run would need a vendored script, offline).
  - Load: the load test in docs/SERVER.md:50-59 ("clicking every 10 s through queue, invoices, pages, approvals, search
    and exports") is not in the repo. Whether it modelled the opening check, the per-edit checks, the static
    revalidations and the polls is unknown (suspected not).

## 10. Scenarios people may not have thought of

- **Slow network or a 22 s server:**
  - Every edit's check queues behind the one in flight, which is fine.
  - Every link and shortcut waits for `flush()`.
  - Approve shows "Approving..." with no timeout.
  - If the answer is lost after the server approved, a retry gives "changed meanwhile", which is right but alarming.
- **Session expiry:** see bugs 3 and 4. Also, polls keep an unattended tab logged in up to the 12-hour absolute limit.
  There is no "you will be logged out in 5 minutes" warning.
- **Back after approving:** pages are `no-store`, so Back re-fetches and shows "Approved", with Undo through the flash
  and Ctrl+Z (good). The bfcache case is handled by app.js:189-195. Form restoration is untested (bug 13).
- **Double submits:** guarded by `form.dataset.sending` (app.js:160), disabled buttons, the note guard, and the
  compare-and-set on the server. Good. Bulk approve of a set that overlaps with a colleague's bulk approve is
  re-checked by the server.
- **Many tabs:**
  - Drafts are per person, not per tab (bug 7).
  - sessionStorage is per tab, while localStorage zoom and folding are shared (fine).
  - Several `/process` tabs each poll the same global job and each reload when it ends.
- **The queue left open for an hour** is stale: no refresh, no "N new invoices" note, and rail counts are frozen. A
  clerk clicks rows already taken.
- **Clerks working the top of the queue collide** on the same invoices (same order for everyone, no claim).
- **A company-sized vendor master or chart** makes every invoice view about 0.8 MB of uncompressed HTML, and the rules
  table on Accounts becomes megabytes (see 2.4).
- **A large scan zoomed past 250%** fetches a 10-15 MB PNG, again on every open in server mode.
- **A timed-out bulk approve or 50,000-row XLSX export** behind a reverse proxy.

## 11. Recommended goal items

Sizes: S is up to about 3 hours, M is half a day to a day, L is several days.

1. **Long-lived caching for versioned static files.** (S-M)
   - What: serve them under a versioned path (`/static/<static_version>/...`) so relative module imports carry the
     version, with `Cache-Control: public, max-age=31536000, immutable`; give `/fonts` and `/assets` the same.
   - Benefit: removes 6-11 requests per page view, about 85% of request count at 400 people, and the access-log line
     for each.
   - Prove it: a Playwright test that counts requests on the second invoice open (0 static), and a TestClient test of
     the header. Before and after: access.log lines per page view.
2. **Response compression** (Starlette `GZipMiddleware`, minimum size about 1 KB, or document IIS dynamic compression).
   (S)
   - Benefit: invoice HTML about 100 KB down to about 15 KB, check answers about 25 KB down to about 4 KB.
   - Prove it: a `Content-Encoding: gzip` test, and measured bytes.
3. **Fix the queue's view on invoice links (bug 1) and the double load (bug 2).** (S)
   - What: rows link to `/review/{id}?{show,q,sort}`, so the Queue link already holds the view and queue.js only
     scrolls.
   - Prove it: a new failing test first (GET `/review?sort=amount`, rows carry `?sort=amount`, opening the top row
     shows "1 of N" and Next is the next by amount), then the fix, then a BUGS.md line. Playwright: Alt+↑ makes one
     navigation.
4. **Drop the opening `/check`.** (M)
   - What: the GET already ran `view.analyse`; have it do the `refresh_confidence` and draft bookkeeping, or make the
     opening check read-only and conditional.
   - Benefit: halves server work per invoice open and removes a DB write per view.
   - Prove it: a Playwright count of POST `/check` on load (0); `scripts/same_behaviour.py`; the queue still shows the
     screen's confidence (existing B-996 and B-997 tests).
5. **Polling hygiene.** (S)
   - What: one shared poller that pauses on `document.hidden` and resumes on `visibilitychange`; backs off
     exponentially on errors (cap about 30 s); stops and says "Your session ended: log in" on a 401 or a
     redirect-to-login (`response.redirected` or a non-JSON content-type); is used by pollJob, follow_job and the
     reader.
   - Prove it: Playwright with `page.route` answering 500 or HTML (requests in 10 s bounded) and an emulated hidden
     page (0 requests).
6. **Session expiry that keeps the work.** (M)
   - What: on a 401 from check or approve, stay on the page, show a "Log in again" panel (login in a new tab, or inline),
     then retry; redirect to `/login?next=<this page>`; warn about 5 minutes before idle expiry.
   - Prove it: a browser test that ends the session in the store, edits, logs in, finds the edits kept, and approves.
7. **Presence and collision avoidance.** (M; with claiming, L)
   - What: show "Ann has edits on this invoice (3 min ago)" on the invoice screen and on queue rows, from
     `invoice_drafts` (person, updated_at), plus a heartbeat piggybacked on `/check`; Prev/Next and "next after approve"
     skip invoices someone else is working on; optionally "Take it" or assigned-to.
   - Prove it: two Playwright contexts with two accounts; B sees A's note, and B's Next skips A's invoice.
8. **Stale-screen handling.** (S)
   - What: when a check answers 409, turn the screen read-only with "Approved by X 2 min ago: open the next invoice"
     (once, not a toast per edit).
   - Prove it: a two-context browser test.
9. **A draft revision number** (an optimistic token in the form, refused when older). (S-M)
   - Benefit: no silent lost update between tabs (bug 7).
   - Prove it: a two-page, same-context browser test.
10. **Take reference lists out of the invoice HTML.** (M)
    - What: serve vendors, GL, CC and tax-code labels as cacheable JSON keyed by version (`?v=` of the vendor master
      and chart, `private, max-age`, ETag), or a server-side vendor search past N vendors; make "Apply to all lines"
      use the picker's `data-options` instead of a second full `<option>` list.
    - Prove it: a fixture with 3,000 vendors and 1,500 accounts; invoice HTML from about 0.8 MB to under 120 KB; GET
      time before and after.
11. **Coding-rules table: one shared `data-options` list instead of the chart per row.** (S)
    - Prove it: HTML size with 200 rules × 1,500 accounts (megabytes down to about 150 KB); the existing accounts
      tests pass.
12. **Bound every list.** (M)
    - What: paginate or cap Failed/rejected, Parked, Second approval, Vendors, POs, Exports-ready (with a server-side
      "select all N"), Users (search); make Activity filter in SQL by q, person and date instead of the newest 5,000.
    - Prove it: a 25k-invoice, 200k-event fixture with page size and time bounded, and Activity finding a 3-month-old
      event.
13. **Page images.** (S-M)
    - What: JPEG or WebP for scans; in server mode allow `private, max-age` for the `?v=` page images (still never in
      a shared cache), or record the decision to keep `no-store`.
    - Prove it: bytes per invoice view, and a scale-3 scan under 2 MB.
14. **Bulk approve as a background job with progress.** (M)
    - Prove it: a 300-candidate test returns at once with a job URL, and the page shows progress.
15. **Timeouts and server-mode wording.** (S)
    - What: an `AbortController` in `post()` (about 60 s) with a clear message; "The AP Coder server didn't answer: try
      again; if it keeps happening, tell IT" in server mode.
    - Prove it: a browser test with `route.abort`, and a wording test for each mode.
16. **Small verified fixes.** (S each, each test-first with a BUGS.md line)
    - the 12-hour clock in `clock()` (bug 6);
    - the queue rows' aria-label (accessibility);
    - a single-key shortcut switch;
    - `pagehide` above 64 KB (send on `visibilitychange: hidden` too, or only the changed fields).
17. **Clean-up with no behaviour change.** (M; prove each with `scripts/same_behaviour.py` and the browser tests)
    - remove the viewer's Streamlit-era protocol (`ECHO_MS`, `sentSelects`, `argSelected`/`argTeach`, `seq`, the
      empty `reportHeight`);
    - one `isTyping`, one hyperscript helper, one request path, one poller;
    - macros for second approval, delete and hidden-query;
    - a `_bare.html` base for the login pages;
    - one `.sr-only` in app.css;
    - split invoice.js into modules (checks, grids, vendor, keys, undo).
18. **A load-test scenario faithful to the front-end, checked in.** (M)
    - What: per invoice, GET, the opening check, 1-3 PNGs, N checks at a realistic typing cadence, static
      revalidations, approve and the next GET; plus queue and directory browsing and the polls; measured at 100, 200
      and 400 people.
    - Benefit: the baseline every other item is proved against (p50 and p95 per route, requests per second, CPU).
