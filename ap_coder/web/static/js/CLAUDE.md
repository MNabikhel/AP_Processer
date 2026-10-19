# ap_coder/web/static/js: the dashboard's scripts (plain ES modules)

Plain JavaScript, no framework, no build step, nothing from the internet: each file is an ES module served at `/static/js/<file>`.
`app.js` runs on every page and sends every POST form; the others add what one page needs. The HTML is drawn on the server (templates/); a script only
sends forms, follows answers and redraws pieces the server returns already escaped. Styles are in the parent folder `ap_coder/web/static`.

**Depends on:** `ap_coder/web/pages` (the URLs and JSON answers it posts to and polls)
**Used by:** `ap_coder/web/templates` (each `<script type="module">`)

## Interface
- The form protocol (app.js): a `submit` listener catches every `<form method="post">` (except `data-upload`), sends it with fetch through `post()` with `X-AP-Coder: 1` (`HEADERS`), and reads the JSON answer:
  `{redirect}` go there (the flash shows on that page); `{toast: {text, tone}}` stay, show a message; `{error}` stay, show it red; `{job, status}` a job started: the page polls `status` (event `apc:job`, or `watchJob()` then reload); `{ask_why}` the server wants a reason first (event `apc:ask-why`, answered by invoice.js, which posts again).
- Server side those are done, message and refuse in web/core.py; `job` and `ask_why` come from pages/process.py, azure.py, settings_reading.py and invoice.py.
- `post()`: (url, FormData) -> the JSON answer, `{ok: false, error}` when the server is down or the answer isn't JSON (`error: ""`, so nothing is shown, when that answer was a 200). `follow()`: acts on an answer, true when leaving the page.
- `pollJob()`: (status url, onUpdate, everyMs = 800) polls until `state` is not "running" (the shape of jobs.py `snapshot`). `toast()`, `messageElement()`, `escapeHtml()`.
- Form attributes app.js reads: `data-confirm`, `data-busy`, `data-upload` (left alone: process.js sends it); also `select[data-autosubmit]`, `input[data-enables]`, `[data-dismiss]`, `details.pop`. `data-job` (in its header comment) is not read: any `{job, status}` answer is followed.
- `InvoiceViewer` (invoice_viewer.js): the page viewer class; events select, assign, teach_cancel, selection, line_hover; boxes are page fractions (0..1).

## Gotchas and decisions
- A script's own POST goes through `post()` (or adds `HEADERS`, as process.js's upload XHR does): without `X-AP-Coder: 1` the server answers 403.
- Text from the server is set as text or through `escapeHtml()`; HTML is only taken from the server's own escaped sections and rows.
- Page scripts load with `?v={{ static_version }}`; app.js in base.html has none, because page scripts import "./app.js" and must get the very same module. invoice_viewer.js is imported the same way. The server
  sends both with `Cache-Control: no-cache`, so a browser checks them again after an update (B-111).
- A form sends once: `data-sending` blocks a double submit until the answer (and is cleared on a back-forward page show).
- `data-busy` swaps the button's HTML and puts it back after: a script must not keep references to elements inside it (B-113).
- `vendorKey()` in invoice.js copies `memory.vendor_key` (ap_coder/core/memory.py): change both (a browser test compares them, B-108).

### Bug lessons
From docs/BUGS.md (one line each; the entry has the repro and the fix).
- B-58: Don't bind Enter on a whole dialog, because it overrides the focused button. `tests/test_web_invoice.py::test_in_a_browser_enter_on_cancel_leaves_the_words_dragged_on_the_page_unused`
- B-60: Don't redraw a section with `innerHTML` without putting back the focus inside it. `tests/test_web_invoice.py::test_in_a_browser_the_approve_anyway_box_keeps_the_focus_when_ticked_with_the_keyboard`
- B-108: Don't re-implement a server normaliser in JavaScript without a parity test. `tests/test_web_invoice.py::test_in_a_browser_the_vendor_key_is_the_servers`
- B-109: Don't let a page-wide shortcut fire inside a modal dialog. `tests/test_web_invoice.py::test_in_a_browser_ctrl_enter_in_the_why_dialog_keeps_the_reason`
- B-110: Don't bind a page-wide submit shortcut without checking which form owns the focus. `tests/test_web_invoice.py::test_in_a_browser_ctrl_enter_in_another_form_never_approves`
- B-114: Don't debounce a save without flushing it when the page is left. `tests/test_web_invoice.py::test_in_a_browser_an_edit_typed_just_before_leaving_is_kept`
- B-115: Don't take over text-editing keys while a text box has the focus. `tests/test_web_invoice.py::test_in_a_browser_alt_arrows_in_a_text_box_stay_on_the_invoice`

## Files
- `app.js`: the form protocol, toasts, job polling, popovers, flash token clean-up; loaded by base.html (every page).
- `invoice.js`: the review screen: live /check on every edit, grids, teach a field, park/approve, keys, reader progress; loaded by invoice.html.
- `invoice_viewer.js`: the page viewer (pages with field boxes, zoom, teach mode); imported by invoice.js.
- `process.js`: Import invoices: pick or drop files, upload with progress, follow the "process" job; loaded by process.html.
- `exports.js`: the ticked count on the Export button and the custom layout's live preview; loaded by exports.html.
- `directory.js`: column order in the Columns popover (drag, arrows); loaded by search.html.
- `accounts.js`: confirm before deleting ticked codes, show a tax's own GL field; loaded by accounts.html.
- `settings.js`: self-test progress, restore confirmation, JDE tables, slider values, old #tab links; loaded by settings.html.
- `help.js`: filters the check list as you type; loaded by help.html.
