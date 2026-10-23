# Decisions

The significant choices behind AP Coder, one line each: the choice, why, and where it is written or enforced.
Add the next number when a change makes a new choice (and say so in the pull request); never renumber. A choice
that is reversed keeps its line, marked "replaced by D-NN".

## Running offline

- **D-01** · Fully offline at run time; the one opt-in to the internet is `AP_ALLOW_INTERNET=1` (developers, the bundle
  builder). Why: invoices and company data must never leave the laptop. (`ap_coder/ops/offline.py` `internet_allowed`,
  README "Run it by hand")
- **D-02** · Azure (Document Intelligence check, Azure OpenAI) is optional, off by default, and never needed to install
  or start; its packages are an extra. Why: the pilot runs with no cloud account; Azure is a second opinion, not a
  dependency. (`pyproject.toml` `azure` extra, `ap_coder/ai/azure_check.py`, `tests/test_azure_optional.py`)
- **D-03** · The offline proof: tests run AP Coder under a socket guard that refuses and records any connection not to
  this computer (127.0.0.0/8, ::1, localhost), and CI installs the bundle with every proxy pointed at a closed port.
  Why: "offline" must be shown, not promised. (`tests/offline_guard.py`, `tests/test_offline_guard.py`,
  `.github/workflows/ci.yml`)
- **D-04** · LM Studio is reached only on this computer or the office network; an internet address is refused however it
  is written, unless `AP_ALLOW_INTERNET=1`. Why: a model server URL is the easiest way for data to leave.
  (`ap_coder/ai/local_llm.py` `refused_address`, `ap_coder/core/config.py` `_on_this_network`, docs/BUGS.md B-39)
- **D-05** · An offline bundle (code, `wheelhouse/`, `models/`, `bundle_manifest.json`) installed with `pip --no-index`.
  Why: the pilot laptop has no internet; one ZIP must install everything. (`scripts/build_offline_bundle.py`,
  `scripts/launch.py`)
- **D-06** · No CDN: fonts, icons, styles, scripts and charts (hand-made SVG) all ship in the package. Why: a page that
  loads anything from the internet would not work offline; the offline proof reports any such address.
  (`ap_coder/web/__init__.py`, `ap_coder/web/charts.py`, `tests/offline_flow.py`)
- **D-07** · RapidOCR stays on 3.4.x, and its PP-OCRv5 models are copied from the bundle, never downloaded by RapidOCR.
  Why: the capture confidence table is fitted on its reads; later 3.x releases change the model list. (`pyproject.toml`
  `ocr` extra, `scripts/fetch_models.py`, `ap_coder/ops/offline.py`)

## Data and install

- **D-08** · One data folder outside the code (default `~/APCoder`, recorded in `~/.ap_coder/settings.json`) for the
  database, invoices, outputs and `.env`. Why: unzipping a new version must never lose or duplicate data.
  (`ap_coder/core/paths.py`, `scripts/upgrade.py` `settle_data_folder`)
- **D-09** · SQLite, one file in the data folder; a backup once a day when the dashboard starts (newest 14 kept), an
  optional second backup folder; a newer database is refused and an older one backed up before migrating. Why: a single
  laptop needs no server, and an update must never risk the data. (`ap_coder/core/store/` `auto_backup`, `copy_backup`,
  `_before_migrating`; `tests/test_update_database.py`, docs/WHATS_NEW.md)
- **D-10** · No company data in the repo: `private/` and `.env` are git-ignored, and so are every `.xlsx` and the PDFs
  and TIFFs at the top of the repo. Why: the repo is shared; invoices are confidential. (`.gitignore`,
  `private/README.md`)
- **D-11** · Never two versions on one database: a start finds a running AP Coder (`/healthz`), opens it if it is the
  same version and folder (or newer), stops an older one or one from another folder (`POST /shutdown` with a token).
  Why: older code must never write to a newer database. (`scripts/running.py`, `scripts/launch.py`,
  `ap_coder/web/app.py`)
- **D-12** · One button per computer (`APProcessor.bat`, `APProcessor.command`) that sets up only what is missing; the
  installer is the same setup with questions. Why: the pilot tester double-clicks one file; the older install and start
  scripts are gone and an update removes them. (`scripts/launch.py`, `scripts/install.py`, `scripts/upgrade.py`
  `prune_stale`, docs/WHATS_NEW.md)
- **D-13** · Two people approving share one AP Coder on one PC, the database on a local disk, not a network share. Why:
  an open SQLite database can be damaged on a network share or a synced folder; a shared server version is a Phase 2
  item. (docs/WHATS_NEW.md "Second approval", docs/GETTING_STARTED.md "Two people approving", docs/PILOT.md)

## Dashboard

- **D-14** · The dashboard is a local FastAPI app (uvicorn) on 127.0.0.1, replacing the Streamlit dashboard. Why: faster
  pages, an address per page, nothing from the internet, and hosted the same way as CloseDesk. (`pyproject.toml`,
  `ap_coder/web/`, docs/WHATS_NEW.md "the dashboard is a local web app")
- **D-15** · Only this computer's host names are served; writes need the `X-AP-Coder: 1` header and a same-origin
  request, and every redirect stays local. Why: another web page in the same browser must not drive AP Coder.
  (`ap_coder/web/security.py` `LocalOnly`, docs/BUGS.md N-20, N-21)
- **D-34** · Shared on the office network (`AP_DASHBOARD_ADDRESS=0.0.0.0`), each browser on another computer gives its
  name once on a "Who are you?" page; the name is kept in a cookie the server signs (HMAC-SHA256) with a secret made at
  each start, and stands for that person: approvals and events name them, every event records their address
  (`detail["address"]`, also from a thread pool the request started), their computer's address stands for a login (so a
  second approval from the first approver's computer is refused, whatever name it gives), and their unsaved review edits
  are their own. This computer's browser is never asked. No passwords: the office network and the second-approval rule
  are the controls; a restart asks everyone again. Why: on the network every action was recorded as the server's user,
  so a colleague could not give the second approval (W2-02, docs/BUGS.md N-57). (`ap_coder/web/identity.py`,
  `ap_coder/web/pages/who.py`)
- **D-48** · After Approve, Park or Reject the review screen opens the invoice that followed this one when its screen
  was drawn (`next_id`), while it is still waiting; the queue's order is not frozen otherwise. And *Your province* is a
  getting-started row (recommended) but not one of the steps counted in the ring. Why: the queue is ordered by a
  confidence that the open invoice's own edits refresh (so the queue shows what the screen shows), and the next invoice
  taken from its new place skipped the ones between (B-426); the province makes the sales tax check work on invoices
  without a ship-to, but AP Coder works without it, and counting it would change every count the first-launch pages and
  their tests agree on (B-437). (`ap_coder/web/pages/invoice.py` `next_url`, `ap_coder/web/pages/process.py`
  `province_step`)
- **D-49** · Over the first 50 reviewed lines the review queue and Learning show the coding accuracy with a plain note
  ("GL suggestions improve as you approve", plus "; with the AI model on they start sooner" without a chat model)
  instead of the red distance to the 90% target; after them the distance shows, with the note's first half while there
  is no chat model (B-447); D-39's definition is unchanged. And with LM Studio itself not running, the review screen
  says so once, in the banner, and leaves out the OvisOCR2 and AI reviewer notes that repeated it. Why: on the pilot's
  first day both read as alarms about something nobody could change, and the three notes pushed the invoice below the
  fold on a 1366 x 768 laptop (docs/BUGS.md B-442, B-444). (`ap_coder/web/core.py` `accuracy_note`,
  `ap_coder/web/pages/invoice.py` `lm_studio_off`)
- **D-52** · What the pilot shows (owner's decision, before testing): the menu shows the seven pages a tester uses every
  day (Review queue, Import invoices, Invoice directory, Exports, GL accounts & tax, and Settings and Help pinned above
  the footer); the other nine fold under **More**, which opens by itself on one of its pages and otherwise stays as that
  browser left it (`localStorage`). LM Studio runs for the pilot: it is step 0 of the Quick start and the first row of
  the welcome checklist, with **Check again**. Exports are Excel or CSV: the JD Edwards E1 tab and export format show
  only once Settings → About's *Show JD Edwards settings* is on (a database setting; nothing removed, `/settings/jde`
  still opens and says so). Developer tools (Learning → Readers, the training data export, the developer changelog, the
  Azure check's page offline) show with *Show developer tools* or `AP_DEVELOPER_TOOLS=1`. Settings opens on Review. Why:
  the audit found the menu longer than a laptop screen, and pages a tester never needs (JD Edwards, readers' scorecards)
  in the way of the ones they do. (`ap_coder/web/pages/__init__.py` `more`, `ap_coder/web/core.py` `jde_shown`,
  `developer_tools`; docs/BUGS.md B-502 to B-525)
- **D-56** · The long choices are picked from a searchable list (`static/js/picker.js`, plain JavaScript, loaded on
  every page): a line's GL account and cost center, Split a line and Apply to all, the fixed rules on GL accounts & tax,
  the vendor on Vendor statements; the review screen's vendor and currency take it in free-text mode (what is typed is
  the value; the vendor list replaces the `<datalist>`). It enhances the native `<select>`, which stays in the form,
  hidden under the box, with its name: its value is what is posted, so the server, `LINE_INPUTS` and the checks are
  unchanged, and a pick fires the select's own `input` and `change`. Every word typed must be in the code or the name
  (accents and case aside); while nothing is typed a line's account list starts with *Used for this vendor* (the
  accounts this vendor's approved lines went to, most used first, from the decided lessons only:
  `invoice_view.vendor_accounts`) and *Suggested* (the line's suggestions card). The review screen prints the GL
  accounts and cost centers once, as JSON (`#gl-options`, `#cc-options`); a line's select holds only its own choice and
  *Needs an account*, so 20 lines no longer carry the chart of accounts 20 times. Without JavaScript a line's select
  therefore offers only its choice and *Needs an account*: a progressive enhancement we accept, the review screen
  needing JavaScript for its checks anyway. Short selects (province, tax, sort orders, the words chooser after a drag)
  stay native. The list opens on typing, a click, Alt+↓, F4 or Enter (Space on a box without a choice), never on the
  focus alone: tabbing through a grid of 20 lines stays quiet, and the text shown is selected so typing replaces it. Tab
  and Ctrl/⌘+Enter pick only the choice highlighted from the keyboard once something was typed, never the row the mouse
  rests on (Enter and a click do pick that one); Ctrl/⌘+Enter then goes on to approve with it (B-590, B-595). The vendor
  list ends with *Not in the vendor list: add as a new vendor…* while the name typed is not in it (B-597), and *Used for
  this vendor* follows the vendor field through each check of the edits (B-596). The list follows its box as the page
  scrolls and closes once the box is out of sight (B-593). Why: the pilot tester found the dropdowns lacking: a native
  select only jumps by first letters, and 300 accounts × 20 lines were about 6,000 options in one page.
  (`ap_coder/web/static/js/picker.js`, `ap_coder/web/static/picker.css`, `ap_coder/web/templates/_invoice_lines.html`
  `few`, docs/BUGS.md B-563, B-564)
- **D-57** · The review screen's page viewer works like a document viewer, offline and in plain JavaScript (no PDF.js):
  the page is a PNG drawn on the server, zoomed from 50% to 400% (Fit width and Fit page follow the window; the zoom is
  remembered in the same browser setting as before, so "100" and "150" saved by older versions still load), with the
  Hand to drag it and Rotate to turn it a quarter turn on screen only (per page of the open invoice, never saved or
  sent). The page and its boxes are one sheet turned with a CSS transform, so the boxes and the words to teach from turn
  with it, and the pointer is turned back to page fractions. Zoomed in, the picture is asked for again at `?scale=2` or
  `3` (240 or 360 dpi, capped at 4,800 and 6,000 px; a scan, a picture or a PDF page that is one scanned picture, is
  never drawn beyond its own resolution, B-594) once the screen shows more than 1.25 or 2.5 screen pixels per picture
  pixel, loaded aside and swapped in, and kept when zooming out; the server keeps the pictures drawn up to 96 MB in all,
  the least recently used going first. The tools above the page take one row (zoom, fit, the Hand, Rotate, *Next to
  check*, then the pages; "Page" folds to "1 of 2" beside the form), the colour key and *To check only* sit on the
  *Fields* line (the counts in words stay for a screen reader), and the teach bar puts its buttons on its title's line:
  at 1366 x 768 the page shows 345 px instead of 305 before the screen scrolls, 514 instead of 474 once the card is
  stuck. A tag in the page's corner says when the Hand is on (*Hand: drag to move · H to turn off*) and when the page is
  shown turned (*Turned 90°: view only, nothing is saved*); it never takes the pointer. Space holds the Hand only with
  the focus on the page, or nowhere with the pointer over it, and Page Up / Page Down turn the pages only from the
  Document card: the form's boxes and buttons and the screen's scrolling keep their keys (B-591, B-592). Why: the pilot
  tester found zoom lacking (B-580); a vector renderer in the browser would need PDF.js (a download) and could not show
  scans and photos the same way, while PNGs at three scales cost one request per page zoomed.
  (`ap_coder/web/static/js/invoice_viewer.js`, `ap_coder/web/document.py` `page_png`, `clean_scale`)
- **D-59** · The review screen's line grid is the tighter one (B-479: the captions *GL account*, *Amount*, *Qty*, *Unit
  price* on the first line only, one-line description boxes, the taxes on one line) from 4 lines, no longer from 11: a
  line takes about 105 px instead of 150, so at 1366 x 768 three lines show beside the page where two did. Up to 3 lines
  every line keeps its captions: they all fit anyway, and a short invoice is where a first-time tester reads them. The
  rows and the Tab order are unchanged. Why: the reviewer of the pilot build found only about two lines in view, the
  grid scrolling away from the page being checked. (`ap_coder/web/templates/invoice.html` `compact`,
  `ap_coder/web/static/invoice.css`)
- **D-60** · The review screen has right-click menus (`static/js/ctxmenu.js`, plain JavaScript, one component: at the
  pointer or under the focus with Shift+F10, inside the window, role=menu with the arrow keys, Enter and Esc, every key
  kept in the menu; a right-click inside an invoice detail's box (Paste, the spelling: B-624), inside a line's or tax
  line's box while it is being typed in (it had the focus already: B-628), Shift + right-click and a right-click on
  selected text stay the browser's own). A field (a right-click on its name, its row in the Fields list or its box on
  the page, or Shift+F10 in its box): Clear this value, Not on this invoice, Teach where it is, Show on page, Copy value
  (and Add as a new vendor). A line (a right-click anywhere on it, its boxes too unless one is being typed in, or
  Shift+F10 in it): delete, duplicate, insert below, split, the suggested account, the GL account from the line above,
  clear it, put it on the lines without one (or, after a confirm, on every line). A tax line (the same): recalculate,
  update from the ticked lines, delete. The page: zoom here, fit, rotate, use a word for a field, teach a field, a line
  from a row of words. Every change from a menu, Delete, a grid action and words used from the page can be undone: the
  screen is kept in the browser before it (each detail, the words taught, both grids row by row), the message offers
  Undo and Ctrl+Z does the same (not in a text box of another form; in a box of the review form, only while nothing was
  typed in it since). One change is kept, until the next one or until something is typed. "Not on this invoice" is kept
  in the draft only (`Draft.absent`) and only for fields an invoice may leave out (`invoice_view.ABSENT_FIELDS`: tax
  numbers, PO, terms, due date, bank account, invoice credited): the field stays blank with a note, the GST/HST and QST
  checks say once, as information, what it costs (no input tax credit or refund), "no PO quoted" goes, errors never do.
  The supplier template does not learn "absent": `supplier.learn` keeps where a value is printed, and teaching it a
  field's absence would change what every later invoice of the vendor reads, which is not small or safe. Small things
  with it: a double-clicked plain word fills the detail being typed in, or just left for the page (as teach mode does,
  with its box; never from a field's box, and a verified value or a line item's word only after asking: B-621); Enter
  and Shift+Enter move between the details; P opens Park and X opens Reject (open only); the invoice number has a copy
  button; a possible duplicate links to the other invoice (a new tab); a note is added without leaving the page; the
  Fields list shows an edited field as Edited; the review queue keeps its filter and scroll for the tab
  (sessionStorage). Why: the pilot tester asked for "delete the field" on a right-click, and the actions that took
  several steps (a line's account from the line above, a line between two others) are one click where the reviewer
  already is; Undo makes the quick ones safe to use. (`ap_coder/web/static/js/ctxmenu.js`,
  `ap_coder/web/static/js/invoice.js`, `ap_coder/web/invoice_view.py`, `tests/test_web_ctxmenu.py`)
- **D-63** · The review screen's right-hand column runs: the checks, the **invoice details**, the line items, the PO
  match, the suggestions, the notes (the sales tax and the GL posting preview stay full width under the columns). This
  supersedes B-467's order (lines first, details after them). Why: the pilot tester asked for the details first, and it
  is the order the work is done in: the header (vendor, invoice #, date, tax numbers, amounts) is checked against the
  page first, then the lines are coded. The details stay compact (two columns per group: Supplier, Invoice, Amounts),
  the document card stays sticky beside the lines once they are scrolled to, and Tab, G (the line still to code) and N
  (the next field to check) work as before. (`ap_coder/web/templates/invoice.html`,
  `tests/test_web_pilot_walkthrough.py::test_in_a_browser_at_1366_the_page_and_the_line_grid_show_together`)

## Reading and touchless approval

- **D-16** · One way to read every invoice, nothing to choose: the PDF's text or local OCR, then OvisOCR2 reads every
  page as a second reader; agreement makes a field verified. Why: no single reader is right on every invoice.
  (`ap_coder/ai/reading.py`, `.env.example`, docs/CAPTURE_DESIGN.md)
- **D-17** · Every picture becomes a PDF when it comes in; the picture is kept in `originals/` for the audit trail. Why:
  one format to store, show, stamp and find duplicates of. (`ap_coder/intake/pictures.py`, docs/WHATS_NEW.md)
- **D-18** · The AI reads, code does the arithmetic: tax lines are copied as printed, rates and the GL distribution are
  computed in Python, and GL codes are an `enum` in the schema. Why: a model must not invent amounts or accounts.
  (README "Design decisions", `ap_coder/core/schema.py`, `ap_coder/coding/tax.py`)
- **D-19** · Touchless processing is one company-wide switch, off by default, with one fixed bar every vendor must reach
  by itself, and a 5% audit sample. Why: autonomy is earned per supplier on its own invoices. (docs/CAPTURE_DESIGN.md
  "Touchless processing", `ap_coder/capture/supplier.py`, `ap_coder/capture/workflow.py`)
- **D-20** · Touchless gates: no approval without a person until OvisOCR2 has read it and the AI reviewer (when on)
  agrees with every field and line; the reviewer's and the Azure check's holds both count, whatever the order; a credit
  note always needs a person. Why: no person looks at a touchless invoice, and B-31, B-32 and B-42 each once let one
  past a gate. (docs/BUGS.md B-31, B-32, B-42, `ap_coder/ai/page_worker.py`, `ap_coder/ai/verifier.py`,
  `ap_coder/capture/workflow.py`)
- **D-21** · The AI reviewer never changes a value; it agrees, disagrees or is unsure, and a person decides. It runs
  after OvisOCR2 in the same line. Why: a person decides what is posted, and the two models never load LM Studio at
  once. (docs/WHATS_NEW.md "the AI reviewer", `ap_coder/ai/verifier.py`, `ap_coder/ai/page_worker.py`)
- **D-22** · Capture calibration is not refitted by bug fixes; a fix caps or lowers a confidence without changing
  calibration keys. Why: `calibration.json` is measured on benchmark runs (`python -m ap_coder.bench calibrate`), and a
  refit needs new runs. (docs/BUGS.md B-44, B-47, docs/CAPTURE_DESIGN.md, `ap_coder/capture/calibration.json`)
- **D-43** · A value read from the line just over its label (`label-above`, B-280, B-282, B-288: grids that print labels
  under their values) is a new evidence pattern the benchmark does not generate, so it has no measured record and keeps
  the reader's own score (never verified on its record alone); and on a scan or a photo an amount that no check confirms
  (no total read, nothing adding up with it) is likely at most, whatever its pattern's record (B-285). Why: two OCR
  reads share the page's geometry, so a row put on the wrong label is a mistake both make; the arithmetic is the
  independent witness. Neither changes the calibration keys (D-22). (`ap_coder/capture/reader/` `labels_under_values`,
  `_value_over_label`; `ap_coder/capture/confidence.py` `fuse`)
- **D-44** · An invoice date or number read without a label of its own is a new evidence pattern, weaker than a labelled
  one and never verified on a labelled one's record: the date after the number in a title (`number-dated`: "Rechnung Nr.
  471102 vom 05.03.2018", "Facture n° 562044 du 2 juillet 2015"), an invoice number alone at the far end of a header row
  after a real label (`label-far-right`), a value after a weak label (`weak-label-...`: "Nr.", "Numéro :", the receipt
  labels, which are read only when no other label for the field reads a value), and, with no label at all, a receipt's
  date and time (`date-time`) and a date alone beside the title (`title-dated`), which fusion holds to check however
  many readers see them (`UNLABELLED_CAP`); a date with no label of its own scores at most half the weakest labelled
  date on the page. A date that reads both ways (9/10/2020) with nothing on the page to settle it is shown month first
  when the first page prints a US address and nothing Canadian (no postal code, province or Canadian tax anywhere on
  it), still marked as reading both ways (at most 60%, check); dates printed with points (05.03.2018) are never turned
  month first. The calibration keys stay as they are (D-22). Why: on 135 real invoices and receipts (sweep 4) half the
  dates and two fifths of the invoice numbers were printed so and missed; a missing value costs a person a look, a wrong
  one that looks sure costs a wrong posting. (`ap_coder/capture/reader/` `_dated_number`, `_stamped_dates`,
  `_title_dates`, `_far_right_header`, `_read_fields_on`; `ap_coder/capture/confidence.py` `fuse`; docs/BUGS.md B-353 to
  B-367)
- **D-45** · A reading found by wording the benchmark never prints (terms stated in a footer sentence, a Spanish,
  Italian, German or Dutch label, a PO under "Your reference", a due date in a German sentence: sweep 4, docs/BUGS.md
  B-373 to B-375) is marked `UNMEASURED` (`capture/types.py`): it keeps the reader's own score instead of the measured
  record of its reading's family ("label-inline" and the like), and is checked (0.8 at most) however many OCR reads
  agree, unless a measured reader reads the same value; a check on other fields (the due date = invoice date + the
  terms) lifts it to likely at most, never verified (B-384). Why: those records were measured on the benchmark's own
  wording; a new wording may fail in ways the benchmark never showed, so a person checks it until it is measured. The
  calibration keys stay as they are (D-22). (`ap_coder/capture/reader/` `UNMEASURED_LABELS`, `_terms_sentences`;
  `ap_coder/capture/confidence.py` `fuse`)
- **D-46** · A supplier template reading is evidence of where the value was printed on the vendor's earlier invoices, so
  it counts as a reader only where the template can vouch for it: read by the label it was learned by (or, for a value
  learned with no label, such as the name at the top, at its position), at a spot *settled* by an approval where the
  value was printed there and nowhere else (or clicked there), or by two approvals in a row with the value there; a spot
  that holds another value than the one approved starts over, and a value printed twice is learned at both places so
  that the template's own spots disagree once they differ. Any other template reading (`UNSETTLED` in
  `capture/types.py`: the label not on this page, a spot not settled yet, a new label at an old spot, the template's own
  spots disagreeing) never decides and never counts towards verified: the value chosen is the one the other readers make
  strongest; it adds its agreement, or stays a rival that contests; alone it fills the field at *check*; it has its own
  evidence family, not measured. Learning does not follow the rule reader's choice of place (it would only repeat the
  reader's mistakes). The template agreeing never leaves a value less certain than the other readers alone (a floor
  below verified), and a rival only the template read does not contest an amount the totals add up with. Why: on the
  sweep 4 repeat vendors (47 later invoices) the template replaced right, labelled readings 9 times and marked 12 right
  values down where the layout had changed; a review with generated English supplier streams then found an agreeing,
  unvouched template reading made a wrong date verified and the invoice touchless (B-420 to B-422). Touchless needs
  every field verified, so a reading that only agrees must not be a second reader. The calibration keys stay as they are
  (D-22). (`ap_coder/capture/supplier.py` `learn`, `_apply_variant`, `Variant.settled`; `ap_coder/capture/confidence.py`
  `fuse`, `_vouched_first`, `_totals_that_add_up`; docs/BUGS.md B-387 to B-393, B-420 to B-423)
- **D-47** · What English vendor PDFs taught the reader beyond the benchmark's wording is marked `UNMEASURED` (D-45) or
  capped, so it starts as a person's check: a supplier's name read under a remit-to label on its own line (unless the
  same name is printed elsewhere on the page), a top-of-page name with nothing of a supplier's name about it (scored
  below `WEAK_NAME`), US dollars implied by a US supplier's "$" with nothing Canadian on the page (never printed, so
  *check*), the HST or GST inferred from a page that names one tax and a tax total at its rate, a business number
  printed without its RT program account (its check digit confirms nothing), a letter's date alone (`letter-dated`, held
  to check as other dates without a label); a credit known only by a line saying so in the body is likely at best. Any
  total on a document that takes a deposit, a prepayment or an agency's commission off before the amount to pay, and the
  deposit itself, are marked `TAKEN_OFF` and held to check however well they add up: whether the gross or the net is
  posted is AP's call, not arithmetic. A GST or HST read with no subtotal to tell its rate is likely at most
  (`EITHER_TAX`). Text a PDF draws sideways is read on the page turned upright and the page's `turn` set, as for a scan
  read turned, so the review screen shows the PDF the way it was read. Why: on 48 real English invoices (sweep 4) the
  dangerous readings were confident ones of patterns the benchmark never prints; a missing value costs a look, a wrong
  verified one a wrong posting. The calibration keys stay as they are (D-22). (`ap_coder/capture/reader/`,
  `ap_coder/capture/layout.py` `_text_turn`, `ap_coder/capture/confidence.py`; docs/BUGS.md B-394 to B-418)
- **D-50** · A supplier template learns from each page once: it keeps the pages it learned from (the file's SHA-256, the
  newest 100) with the value of each field it learned from each, and a page approved again (reopened for correction,
  sent back by the second approver, or the same file imported twice) teaches only the values it has not taught before
  (every value of a field it taught is kept, and every value approved is still looked for on the page, B-460), or one a
  reviewer clicked; it does not count as another invoice. The template is not rolled back when an approval is withdrawn
  (a reopen or a send-back withdraws the coding memory, the lessons and the outcomes): undoing one page's place in a
  running record of spots is not possible, and the page's values approved again are the same evidence. Why: approved
  twice, one page made a spot learned unsettled "two approvals in a row" and vouched for, and the template read the
  invoice date as the due date on the next invoice (B-454; D-46 counts approvals in a row).
  (`ap_coder/capture/supplier.py` `learn`, `Template.documents`; `ap_coder/capture/workflow.py` `learn_from_approval`)

## Tax, coding and learning

- **D-23** · Place of supply is the ship-to printed on the invoice, else the province of the cost center's site, else
  our province from Settings, and the vendor's province only last; one rule for the tax checks and the self-assessment.
  Why: that is where Canadian sales tax applies, not where the vendor is. (docs/SALES_TAX.md, `ap_coder/coding/tax.py`
  `supply_source`, `ap_coder/coding/taxreturn.py`, docs/BUGS.md B-13)
- **D-24** · Rates are an effective-dated CSV that finance edits (a copy in the data folder wins); the covering row with
  the latest `effective_from` wins (a province row on a tie). Why: rates change on a date, and file order must not
  decide. (`ap_coder/coding/tax.py`, `data/canada_tax_rates.csv`, docs/BUGS.md B-12)
- **D-25** · Lessons are kept per situation (vendor, document kind, field or kind of line); after 3 corrections of one
  situation (`why_after`, Settings → Review), approving asks "why", and the reason goes to the AI with the lesson. Why:
  a repeated correction is a rule nobody wrote down. (`ap_coder/ai/lessons.py`)
- **D-26** · `vendor_key` rules are versioned (`VENDOR_KEY_RULES`); the store recomputes stored keys when the version
  changes. Why: a new rule (French legal forms) must not split one vendor's history in two. (`ap_coder/core/memory.py`,
  `ap_coder/core/store/`, docs/BUGS.md B-15)
- **D-27** · The invoice directory reads the PO, subtotal, tax and GL accounts from the invoice's coding JSON with
  SQLite's JSON functions instead of adding columns. Why: the coding JSON (the reviewer's when approved, else AP
  Coder's) stays the one source, with no column to keep in step. (`ap_coder/erp/directory.py`)
- **D-51** · An account no approval backs is a guess, not a proposal: a weak match (an account's name, the vendor
  master's default) is never put in a line, with or without the chat model; the chat model's own account is put in,
  marked "A guess, not learned yet: check it" on the review screen, and never approved without a person. What the
  reviewer approves on a guessed line, kept or changed, is their own coding (outcome "coded"): learned from like any
  other line, but neither confirmed nor corrected, so it is left out of coding accuracy, the top corrections and the
  lessons (B-475); a coding rule or the PO coding that replaces a guess is that source's coding, not a guess; Month-end
  accrues an invoice no one approved as "proposed", on its non-guessed accounts only. Why: with LM Studio chosen but not
  running, name matches (Google → Meals) looked learned, left Approve open and raised the accuracy the pilot tester
  would judge AP Coder by (B-463, B-466). (`ap_coder/ai/offline_coder.py` `guessed`, `ap_coder/core/store/`
  `proposed_coding`)
- **D-53** · No cost centers for the pilot: *Use the sample GL accounts* (welcome page, Import invoices, GL accounts &
  tax) and the demo load the sample GL accounts, tax setup and policy without the sample cost centers, so a line needs
  only its GL account (the checks ask for a cost center only when a cost center list exists). A list imported later on
  GL accounts & tax brings cost centers back, unchanged (`load_sample_cost_centers` loads the sample one, for tests).
  The demo's invoices keep the cost centers their ground truth prints, unchecked. Why: the owner's decision for this
  test; one less thing per line. (`ap_coder/core/store/` `load_sample_setup(cost_centers=)`, `ap_coder/ops/demo.py`)
- **D-58** · How GL coding learns from AP, measured with `bench coding` and its new `--realistic` run (2% reviewer
  mistakes, ERP history for 4 vendors in 10, kept chat-model guesses, one account split by quantity); thresholds set on
  seeds 1 to 4, reported on 31 to 34 (30 × 30). Kept: (1) other vendors' lines code a line when at least two of them
  agree with three quarters of the evidence (each vendor's closest line), not only when none disagrees: realistic
  accuracy 92.2% → 93.0%, precision 96.6% → 96.7%, default unchanged; (2) a correction against 5 or more approvals in a
  row of the same line is suggested with the old account beside it, not filled in by any source, until AP confirms it
  once (a slip looks like a change of mind until then; B-603, B-606): realistic wrong accounts 292 → 219, precision
  97.4%, accuracy −0.1 point (default −0.1, the first spare part after the recode); 3 approvals was tried: 199 wrong but
  −0.3 point of accuracy on both runs (−0.2 on tuning seeds 3 and 4, where 5 cost nothing); (3) the history check and
  the chat model's examples follow a correction once it is confirmed by one more decision for the new account, whatever
  its outcome, each line on its own (`memory.current`, B-601, B-602, B-605), not all-time votes: neutral on the
  benchmark (false alarms 20 → 18 default, 49 → 52 realistic, caught 139 → 140), kept because the old count warned for
  about 4 lines per old approval after a recode, which grows with years of ERP history. All together (after the review
  fixes B-602 to B-606): default 96.0% / 99.7% / 96.3% (accuracy / precision / coverage) → 95.9% / 99.7% / 96.2%, 28
  wrong both, history-check false alarms 20 → 9; realistic 92.2% / 96.6% / 95.5% → 92.9% / 97.5% / 95.3%, 292 → 212
  wrong, false alarms 49 → 50. Rejected: following the latest correction at once (no warning right after a recode:
  default false alarms 20 → 0, but realistic catches 139 → 71 and false alarms 49 → 66, since a slip is a correction
  too); "usual account" at 9 in 10 of 5 or more lines (no change on 31 to 34); a kept guess learned only after a second
  decision (realistic accuracy 92.2% → 89.2%, or 90.0% counting a changed guess as a decision, for precision 97.0% and
  96.8%); a line that went back and forth between two accounts left blank (split errors 93 → 46, but accuracy 92.2% →
  91.5%); a cost center per vendor and account (the benchmark does not measure cost centers). Why: a wrong account
  filled in is worse than a blank, but a blank costs AP a click, so only what lowered wrong accounts without losing
  right ones, or for a small, stated cost, was kept. (`ap_coder/ai/suggest.py`, `ap_coder/core/memory.py`,
  `ap_coder/bench/coding.py`, docs/CODING.md)
- **D-61** · The ship-to read from an invoice is the address printed under a delivery label ("Ship to", "Deliver to",
  "Livré à", "Expédié à"), else the one under a customer label ("Bill to", "Sold to", "Client", "Facturé à", "Vendu à"),
  else nothing; never the vendor's own letterhead. With nothing read, D-23 goes on: the cost center's site, then our
  province from Settings, and the tax warnings then say the province was assumed ("your province in Settings: no ship-to
  or bill-to printed"); the review screen says "the invoice's ship-to, else its bill-to", never only "the ship-to". Why:
  the place of supply of goods is where they are delivered and of services (generally) the recipient's business address
  (Excise Tax Act, Schedule IX); a labelled customer address is ours by its label, names the office actually billed (a
  Montréal office of a company set to Ontario), and the chat model is told the same ("use the bill-to if there is no
  ship-to"); the vendor's province says only where the vendor is, so taking it hid every out-of-province charge. A
  bill-to that is not where the goods went is still caught: the tax charged then fits another province and the check
  says so. (`ap_coder/ai/offline_coder.py` `provinces`, `addresses`; `ap_coder/coding/tax.py` `check_taxes`;
  docs/SALES_TAX.md; docs/BUGS.md B-651)
- **D-62** · EOM payment terms count the net days from the end of the invoice's month, however they are written: "Net 30
  EOM", "30 EOM", "30 days EOM", "EOM 30" and "EOM net 30" (or "fin de mois", "end of month" in place of EOM) are due 30
  days after it (an invoice of January 15: March 2), "10 EOM" on the 10th of the next month; "EOM" or "Net EOM" alone at
  the end of the month; "2/10 EOM" keeps its discount by the 10th after month end and the net 20 days later. "Net: 45",
  "Net - 45", "NET-45", "N45" and "45 net" are net 45. Why: that is the common North American reading of EOM terms (net
  N days after the end of the invoice month); reading "30 EOM" as "the end of this month" asked for payment a month
  early. A vendor who means "the end of the next month" prints it (or a due date, which always wins).
  (`ap_coder/coding/terms.py` `_NET`, `_start`; docs/BUGS.md B-656)
- **D-65** · The vendor list's tax numbers fill the invoice details where none is read: when a vendor is set (picked or
  typed on the review screen, and on the server at each check and when the screen opens) each GST/HST, QST and PST
  number the reading left **blank** (not printed; one read and cleared by the reviewer is theirs to type, B-688) takes
  the vendor list's (`vendors.expected_gst`, `qst_number`, `pst_number`), with "From the vendor list — not printed on
  the invoice" under it. A number read from the invoice or typed is never touched, nor a field marked *Not on this
  invoice*; a number filled from the vendor follows the vendor (replaced, or cleared when the new vendor has none), and
  once the reviewer changes or clears it, it is theirs. Undo (the message, Ctrl+Z) puts the numbers back and keeps the
  vendor. The filled fields are kept in the draft (`Draft.from_vendor`) and with the approved coding
  (`tax_numbers_from_vendor_list`, beside it like the place of supply). Such a number is not on the invoice, so: an
  information line says CRA (Revenu Québec for the QST) needs it printed to claim the input tax credit (refund) when the
  invoice charges that tax and its total is $100 or more (`taxreturn.document_threshold`: $100 from 2021-04-21, $30
  before, checked against CRA's documentary requirements, GST/HST Memorandum 8.4; the code was right and now cites its
  source), and the sales tax return lists the claim to check. The checks about one invoice detail highlight it in amber
  with a one-line note (`invoice_view.FIELD_FLAGS`): VENDOR_TAX_NUMBER_CHANGED, GST_NUMBER_CHECK_DIGIT and
  GST_HST_NUMBER_FORMAT the GST/HST number ("Vendor list: 123456789 RT0001", "Fails the CRA check digit"),
  VENDOR_QST_CHANGED and QST_NUMBER_FORMAT the QST number, VENDOR_PST_CHANGED the PST number, VENDOR_BANK_CHANGED the
  vendor's printed bank details, the PAY_FROM checks Pay from; the checks panel still lists them, and each /check answer
  redraws the highlights. The Vendors page warns about a vendor list number that is not in the CRA format or fails its
  check digit, since it is now filled in. Why: the pilot tester asked for the vendor's numbers to come with the vendor,
  but a number the vendor list supplies is not evidence the invoice meets the documentary requirements: filling it must
  never hide that it was not printed, nor overwrite what the page says (a different number printed is the fraud signal).
  (`ap_coder/web/invoice_view.py` `fill_from_vendor`, `settle_header`, `not_printed`, `field_flags`;
  `static/js/invoice.js`; `ap_coder/coding/taxreturn.py`; `ap_coder/coding/vendors.py` `vendor_number_problems`;
  docs/BUGS.md B-680)
- **D-64** · A line's sales tax is said **tax by tax**, as one **tax code** picked in one dropdown per line on the
  review screen. For each tax of the place of supply (D-61) a line is *charged* (`LineItem.taxes_applied`, as always),
  *not due* (`LineItem.tax_exempt`, the taxes it is exempt or zero-rated from; or `tax_status` exempt / zero_rated /
  out_of_scope for the whole line) or *due, not charged* (neither: the vendor did not collect it; a provincial tax is
  then self-assessed, BC PSTA s.49 / Bulletin PST 400, the SK PST, MB RST and QST acts likewise). One status for every
  tax could not say "GST taxable, PST exempt", and "Exempt" hid PST that was due (B-710). Codes: "GST+PST", "GST~PST"
  (PST exempt), "GST" (where PST applies: PST due, not charged), "PST~GST", "EXEMPT", "ZERO", "OUT", "NONE"; labels per
  province ("GST only · PST exempt", "GST only · PST not charged (self-assess)", "PST only · GST exempt", "Exempt from
  both", "Zero-rated", "No tax (out of scope)"; RST in Manitoba; QST in Quebec; HST provinces: HST, Exempt, Zero-rated,
  out of scope, and GST only when GST is charged; AB and the territories: GST, Exempt, Zero-rated, out of scope; any tax
  charged the province does not use, "Foreign tax", and the line's own code). Storage stays backward compatible:
  `tax_exempt` is `None` on an invoice stored before it, read (`line_tax.legacy_exempt`, filled when the coding is
  validated) as the vendor meant it: a tax it charges and marks on other lines and not on this one is not due on it, a
  tax charged on no line is due. The strict JSON schema has `tax_exempt` (an array of the five tax types; the enum
  budget counts it). Pre-filling (`line_tax.prefill`): the tax column printed beside the lines (capture keeps it as
  `LineReading.tax_flags`, not in the description), read with the invoice's legend, else common letters only when their
  tax is charged; else, per tax charged, the lines that make it add up: an exact subset sum in cents, the ways counted
  exactly by meeting in the middle (up to 40 lines); a discount or credit line is never left out (a trade discount
  lowers the taxable price, ETA s.165; s.161 covers only early-payment discounts, B-707); a credit note is solved in the
  size of its amounts (ETA s.232, B-704); one way: its lines, a line left out that does not read as untaxed (deposit,
  gift card, tip, interest, late payment charge, printed exempt; never freight, delivery or an eco fee, which follow the
  goods: CRA P-077R2, BC/SK/MB taxable price, B-711) or that a printed taxable says is taxed marked to check; 2 to 20
  ways among at most 20 lines: the likeliest, the lines in doubt to check; more: every line, nothing picked (B-706). A
  tax charged on other lines and not on this one: not due; a line with nothing charged while tax is charged on others:
  Exempt. For BC, SK and MB PST that inference is a question a person answers before approval (PST_EXEMPTION_TO_CONFIRM,
  asked on the approve bar like a GL account to pick; "confirmed on review" kept in the coding and the audit trail), and
  a vendor out of the province of supply or with no PST number is taken as not having charged it (due, self-assess)
  (B-715). "Unsure" marks every line to check; a difference some choice of lines can make asks which lines carry the
  tax, and "the vendor's tax looks wrong" is said only when none can ("can't be claimed" never of PST) (B-714). GST
  charged where HST applies is HST_NOT_CHARGED (the GST is claimable; you remain liable for the provincial part, ETA
  s.165: ask for a corrected invoice, B-721), not an ITC risk (B-717). QST not charged is said without "self-assess" (a
  registrant buying for its commercial activities generally owes nothing net; the report keeps listing it, B-718).
  Self-assessed PST is booked from the Self-assessment report by journal entry; the JD Edwards dry run warns when an
  invoice's code (B) already has E1 accrue it (B-716). A clerk's code always wins; picking or confirming a code clears
  its check mark, and approving confirms the codes (B-713). The Tax check under the lines (`taxmath.math_check`) checks
  the rate printed (each tax line at its own rate when a tax is printed at two: BC PST 10% on liquor is lawful, B-705),
  names a line only when exactly one line's amount explains a difference, else says the vendor's tax looks wrong
  (B-703), and shows a "self-assess" row for a provincial tax due and not charged; a tax line's taxable follows the
  lines marked unless typed (B-610, B-622), its amount never does (B-700). Findings: TAX_BASE_MISMATCH,
  EXEMPT_LINE_TAXED, LINE_TAX_NOT_CHARGED (once per tax, advice that keeps a due tax in the self-assessment, B-708),
  PROVINCIAL_TAX_NOT_CHARGED line by line (B-709), ITC_AT_RISK (HST where GST applies or GST where HST applies: only tax
  properly payable is claimable, ETA s.169; the booking is not changed). Self-assessment is line by line;
  non-recoverable PST is expensed onto the lines it is charged on; JD Edwards (*match header*) gives a line exempt or
  zero-rated from every tax charged the exempt code; exports print the code's words. Out of scope: prices that include
  the tax (a receipt): said, never converted. Why: the pilot tester asked for each line to say which taxes it carries
  and a check that the tax charged matches the lines; a CPA review asked that the model follow what the law
  distinguishes (charged, exempt, due and self-assessed) and that the checks never present a guess as a reading.
  (`ap_coder/coding/line_tax.py`, `ap_coder/coding/taxmath.py`, `ap_coder/coding/tax.py`,
  `ap_coder/coding/taxreturn.py`, `ap_coder/core/schema.py`, `ap_coder/capture/reader/`, `ap_coder/ai/offline_coder.py`,
  `ap_coder/web/invoice_view.py`, `ap_coder/erp/jde.py`, docs/SALES_TAX.md, docs/BUGS.md B-700 to B-723)

## Exports and intake

- **D-28** · Files for an ERP loader (JDE Z-files, the CSV and Custom CSV exports) are written as they are, without
  spreadsheet quoting; files for people (the xlsx export, search and directory exports, statements, accruals and other
  reports) are neutralised against formulas. Why: a loader puts an apostrophe into the data, so an invoice `'+A-100`
  misses the ERP's supplier + invoice-number duplicate check; people open their files in Excel. (`ap_coder/erp/jde.py`,
  `ap_coder/erp/exports.py`, `ap_coder/erp/export_layout.py`, `ap_coder/core/safe.py`, docs/BUGS.md B-23, B-26, B-92)
- **D-29** · An export batch is rebuilt from approved data: the same invoices and vouchers on every download, with
  today's settings and master data (due date, User ID, vendor IDs, GL names, rates); a JDE batch downloaded again never
  re-runs the checks, and is refused while a setting blocks every export. Why: a second download must load the same
  vouchers, and never a file its own report says cannot load. (`ap_coder/erp/jde.py`, `ap_coder/web/pages/exports.py`,
  docs/BUGS.md B-21)
- **D-30** · AP Coder never connects to the mailbox: another tool saves each email as a file in the invoices folder.
  Why: no mailbox password, token or network access is ever given to it. (docs/MAILBOX_SETUP.md,
  `ap_coder/intake/mailbox.py`)
- **D-40** · The UTF-8 byte-order mark: the JDE Z-files have none; the ERP CSV and every download for people have one;
  the Custom CSV has one unless its layout leaves it out ("Byte-order mark (for Excel)", ticked by default). Why: Excel
  needs the mark to show accents and some Windows import tools to read UTF-8 at all, while a loader that reads plain
  UTF-8 takes it as part of the first header; the Custom CSV is the export shaped for one ERP's loader, so the choice is
  its layout's, with the behaviour before as the default. (`ap_coder/erp/export_layout.py`, docs/BUGS.md N-79, B-224)
- **D-66** · *Pay from* is the company's own bank account an invoice is paid from, picked from a list only: the accounts
  imported or typed on GL accounts & tax → **Bank accounts** (table `bank_accounts`, schema 16: code, name, currency,
  institution, transit, account number, cash GL account (in the chart when one is loaded), default (one per currency),
  in use). Only the last 4 digits of an account number are ever shown (the grid's box stays blank, and there is a
  template to download, never the list). An invoice takes, until the reviewer picks one, this vendor's last Pay from
  account when it is in the invoice's currency, else the currency's default, else its only account in that currency
  (`banks.choose`, in the pipeline and on the review screen); when the currency changes the default follows, and an
  account the reviewer picked for another currency is replaced by it. An account picked in another currency than the
  invoice's is kept with a warning (PAY_FROM_CURRENCY). Once any account is in use, approval needs one
  (PAY_FROM_MISSING, and the approve bar says "Pick the bank account to pay this invoice from", not overridable); with
  none set up nothing asks, and one information line says where to add them. The approved coding stores the account's
  code (`pay_from_account`); the exports show its name and cash GL as they are today (D-29): the Excel and CSV files get
  *Pay from* and *Pay from GL* columns, the custom layout offers both as fields (saved layouts unchanged); for JD
  Edwards, VLGLBA (the G/L bank account, which E1 keeps as a short account ID AP Coder does not know) is a mapped
  setting per Pay from account (Settings → JD Edwards E1), and with no mapping (the default) the files are as before and
  E1 takes the AAI PB bank (unconfirmed, docs/JDE_E1.md). The vendor's banking details printed on the invoice
  (`remit_bank_account`) are no longer an editable "Pay into" field: a read-only line "Bank details printed on the
  invoice: …1234" in the Supplier group, kept unchanged in the coding for VENDOR_BANK_CHANGED, amber when that check
  fires. A getting-started row, "Your bank accounts (optional)", links to the tab; the sample `data/bank_accounts.csv`
  is not loaded by the sample setup. Why: the pilot tester confirmed these are our accounts, chosen from a fixed list; a
  typed account in a payment file is an error or a fraud waiting to happen, and the vendor's printed details are
  evidence for the fraud check, never where AP pays. (`ap_coder/coding/banks.py`, `ap_coder/core/store/`
  `bank_accounts`, `ap_coder/web/pages/accounts.py`, `ap_coder/web/invoice_view.py` `settle_header`,
  `ap_coder/erp/exports.py`, `ap_coder/erp/jde.py` `bank_glba`)

## Working on the code

- **D-31** · Every bug gets a failing test first, then the fix, and an entry in docs/BUGS.md with its lesson; a
  suspicion that cannot be reproduced is logged as N-NN and left. Why: a fix without a test comes back; a blind fix
  breaks something else. (docs/BUGS.md, `CLAUDE.md`)
- **D-32** · The codebase map is checked, not only asked for: `scripts/check_map.py` fails CI on a stale index, a
  Depends on / Used by line that the imports contradict, an interface name that no longer exists or a bug lesson missing
  from its folder; a hook reminds after each edit; the map-reviewer agent checks a change's Used-by folders. Why: a map
  nobody checks goes stale, and a stale map misleads more than none. (`scripts/check_map.py`, `.claude/`,
  `.github/workflows/ci.yml`)
- **D-33** · The flat modules of `ap_coder/` (68, all but `__init__.py` and `__main__.py`) are grouped into six folders
  (`core`, `ai`, `coding`, `erp`, `intake`, `ops`), moves and import edits only; each old path (`ap_coder/store.py`...)
  stays one release as an alias that puts the moved module in `sys.modules` under the old name, so old imports, outside
  scripts and monkeypatching through the old path reach the one real module (`ap_coder.intake`, now a package, forwards
  its old names to `intake/pictures.py`). Why: one folder of 70 modules was too big to map or review (scale assessment,
  PR #24), and an update unzipped over an older install overwrites the flat files with the aliases instead of leaving
  stale copies that shadow the moved code. (`ap_coder/CLAUDE.md`, `ap_coder/MODULES.md`, `scripts/check_map.py`
  `GROUPED`)
- **D-35** · The map-reviewer runs before every commit an agent makes, without being asked: a Claude Code hook records
  what it reviewed when it finishes, and another stops an agent's `git commit` while any changed file that is not
  Markdown differs, staged or on disk, from what was reviewed (every changed file, so `git add -A && git commit` and
  `commit -a` are covered); Markdown edits after the review (the map lines it asks for), a merge, cherry-pick or revert
  being finished, and a person's own terminal pass. Why: in PR #29 it found two bugs the tests had missed (B-129,
  B-130), and a reviewer that must be remembered is skipped. (`scripts/map_review_gate.py`, `.claude/settings.json`)
- **D-36** · CI runs on pushes to `main` and on pull requests, not also on pushes to `claude/**` branches. Why: a PR's
  commits ran every job twice (48 jobs instead of 24) for the same code; a branch with no PR can still be run by hand.
  (`.github/workflows/ci.yml`)
- **D-37** · Each CLAUDE.md is kept at 50 lines or fewer, bug lessons aside: `scripts/check_map.py` warns past 50 (was
  60) and fails past 80. A map is trimmed mainly by cutting words that hold implementation detail or repeat another map;
  a few short bullets may share a line, but joining lines into long ones that save lines and not words is no trim. Why:
  the goal set ~50 lines so a map reads in one go; five maps had grown to 53 to 60, and a trim that only joined lines
  (words down 0 to 3%) was reverted. (`scripts/check_map.py` `WARN_LINES`, docs/MAP_CHECKS.md round 4)
- **D-38** · The codebase map is removed: the CLAUDE.md in every folder, `ap_coder/MODULES.md`, `scripts/check_map.py`,
  the review gate (`scripts/map_review_gate.py` and the `.claude/` hooks), the map-reviewer agent, the CI "Codebase map"
  job and docs/MAP_CHECKS.md. A short root CLAUDE.md keeps the standing rules and the run/test commands. This supersedes
  D-32, D-35 and D-37. Why: on 5 tasks from this repo, each run by a fresh Claude Code session with and without the maps
  (2026-10-19), the maps cost 1.4 to 2.4 times the tokens on four tasks and the same on the fifth, with the same
  correctness on four; the one difference (a new check code) went to the maps. The upkeep (map edits and a review on
  every commit) cost more than it saved. (owner's decision, 2026-10-19)
- **D-39** · "Approved as coded" has one definition and one label on Insights, Learning and Activity: an approval that
  changed nothing the coder proposed. A value the reviewer changed, a line added or removed, is a change; filling in a
  blank the coder left (a cost center or GL account it could not pick, an empty field) is not, since it completes the
  coding rather than correcting it (the audit trail still lists it). Insights, Learning and Activity count the latest
  approval of each invoice still approved or waiting for its second approval, once per invoice (B-212). "Coding
  accuracy" stays a line figure (a line the coder left blank counts as not right: it gave no answer) and is written to
  one decimal wherever it shows. Why: the pilot tester saw 75%, 1 of 3, "4 with reviewer changes" and 88% vs 87.5% for
  the same four approvals. (`ap_coder/erp/insights.py` `corrected`, `approved_as_coded`)
- **D-41** · Line items read without column headings to go by (a table read by its amount column, a receipt's list of
  amounts, a summary of charges) are kept only when they prove themselves: they add up to a sum printed with them, to
  the subtotal, or to the total less the taxes; otherwise none are returned and the invoice is coded as one line for its
  subtotal. Line items read under column headings are kept as before. Why: a wrong line (a tax or a total read as a
  line) is worse than a missing one: it is coded to an account and has to be found and taken out, where a missing line
  is one line a person splits; on 128 real invoices and receipts the guesses gave 234 wrong lines (sweep 4, docs/BUGS.md
  B-341, B-342). (`ap_coder/capture/reader/` `_proven_rows`, `listed_items`, `_charges_summary`)
- **D-42** · A reading the page cannot settle is never verified, however well the totals add up: an amount under a
  "PST/QST" label when the supplier prints neither its QST nor its PST number alone, a subtotal when the totals add up
  with another printed subtotal too (before and after the charges), a supplier's name with a rival name printed above it
  (a store's brand over its operator; unless the vendor master confirms it), terms that ask for a deposit first. The
  reader marks the reading's method (`EITHER_TAX`, `TWO_SUBTOTALS`, `RIVAL_NAME`, `PART_OF_TERMS` in `capture/types.py`)
  and fusion caps it at likely, with the reason; the calibration keys stay as they are (D-22). Why: on 135 real invoices
  (sweep 4) the values verified wrong were ones the arithmetic could not tell from the right one.
  (`ap_coder/capture/reader/`, `ap_coder/capture/confidence.py`, docs/BUGS.md B-321, B-322, B-325, B-328)
- **D-54** · An AP Coder folder is identified by its version and its build: the commit in `ap_coder/BUILD` (`$Format:%h
  %cI$`, `export-subst`), which GitHub's Download ZIP and `git archive` fill in and the offline bundle builder writes; a
  checkout's HEAD; else a hash of the code. The build is part of the launcher's fingerprint (a newer build of one
  version is an update, said and self-checked), a stamped build (a GitHub ZIP, a bundle from 0.3.0 on) is ordered by its
  commit's time in UTC and an older bundle only against another older bundle (so an older ZIP in another folder is older
  and is cleaned up, as an older bundle is; a folder whose build can't be ordered with this one is never cleaned up,
  B-551), `/healthz` says it (a dashboard of an older build is stopped, not opened), and Settings → About, the doctor
  and the problem report show it. The version is 0.3.0 for the pilot drop. Why: every version was 0.2.0 and a ZIP
  carried nothing else, so a newer ZIP unzipped over an older one was invisible (B-542); a version bump on every merge
  would be forgotten, a commit never is. (`ap_coder/core/paths.py` `build_stamp`, `install_identity`; `.gitattributes`;
  `scripts/build_offline_bundle.py` `stamp_build`)
- **D-55** · Python 3.11 is the floor everywhere: `requires-python`, ruff's target, the CI test matrix (3.11 to 3.14),
  the doctor, the launcher and the guides. A `.venv` made with an older Python is made again. Why: the launcher, the
  bundle (3.11 to 3.14) and the guides already said 3.11 while pyproject and CI said 3.10, so CI tested a Python no
  pilot laptop is set up with (B-548). (`pyproject.toml`, `.github/workflows/ci.yml`, `scripts/launch.py` `MINIMUM`)
- **D-67** · The 67 one-line alias files of the old flat paths (D-33) are gone: `_MOVED` in `ap_coder/__init__.py`, a
  finder first on `sys.meta_path`, gives each old name (`ap_coder.store`...) the moved module itself, so old imports,
  outside scripts and patching through an old path still work, and a stale flat file an older install left is never
  imported. Why: the files doubled every listing and search of `ap_coder/` and sent agents to read an alias before the
  real module (token-cost study, October 2026). (`tests/test_moved_modules.py`)
- **D-68** · The default `python -m pytest` leaves out the slow tests (every browser test and those over half a second,
  listed in `tests/slow_tests.txt`; marked `slow` in `tests/conftest.py`): about 3,000 tests in a few minutes instead of
  ~3,600 in 20+. CI, `--full-tests` and anyone before a merge run everything with `-m ""`; `scripts/check.py` runs ruff
  and the tests of the changed files. Why: sessions spent half their turns waiting on, re-running and polling the whole
  suite (token-cost study, October 2026).
- **D-69** · `ap_coder/core/store.py` (4,000 lines) is the package `ap_coder/core/store/`: one mixin per subject,
  assembled into the same `Store`; the package re-exports every name the module had (`tests/fixtures/store_api.txt`,
  `tests/test_store_package.py`). `ap_coder/capture/reader.py` (3,900 lines) is the package `ap_coder/capture/reader/`
  the same way: one part per section, each using only the parts before it, all names re-exported. Why: the store was the
  file agents read most, and a 4,000-line read fills a session's context for every later step.
- **D-70** · A line's tax code is learned from approvals: each approved line keeps its code (`feedback.final_tax_code`,
  schema 17, filled from the approved invoices), and a code the vendor's history agrees on (lines worded alike, at least
  2 approvals of different invoices, never another code) is used before the subset-sum guess, marked "Learned from past
  approvals" on the review screen. Printed codes still win (a printed "G" with the same taxes only takes the exemption
  from the history); a learned code for a tax the invoice does not charge is not used; one contradiction and the line is
  worked out again. The Tax check checks a learned code like any other. Why: the solver can't see an exemption on an
  invoice that charges no PST, and an inferred one waits for a person every time. Measured with `bench coding` (tax code
  precision and coverage). (`coding/line_tax.py` `learned_codes`, `prefill`; `tests/test_tax_learning.py`)
- **D-71** · A withdrawn approval takes back what it taught the vendor's layout: before an approval's `learn()` changes
  the template, the template as it was is kept (`template_snapshots`, keyed by invoice, the last 5 per vendor);
  reopening, rejecting or sending back that invoice puts it back, unless a later approval changed the template since
  (then the later one is kept, and the event's detail says so: putting back an older one would undo that approval too).
  The Learning page's Suppliers tab has "Reset layout" (asks first, logged as `layout_reset`). Why: a box taught on the
  wrong words is authoritative at once, and the next invoice was read with it. Measured with `bench learn
  --wrong-approval N`. (`core/store/invoices.py` `_withdraw_template`, `core/store/capture.py`;
  `tests/test_template_rollback.py`)
- **D-72** · A lesson reaches the AI (prompts, automatic use) only after 2 corrections to the same value (`agreeing`,
  counted over the approvals still standing in `lesson_approvals`) or 1 correction with the clerk's reason. The review
  screen's hints (a person decides) show from the first correction, said as "Corrected once before: …"
  (`lessons.on_screen`); a single correction is still counted and shown on the Lessons tab as "not shown to the AI yet".
  A line's cost center is a lesson of its own (`line_cc`) beside its GL account (`line_gl`). Why: one slip (2% of lines
  in `bench coding --realistic`) was told to the AI at once, and half the line lessons shown were wrong.
  (`ai/lessons.py` `reaches_ai`, `core/store/lessons.py`; `tests/test_lessons.py`)
- **D-73** · The training export's format 2 only adds to format 1: per field `corrected` and `boxes` (taught by click,
  kept with the capture at approval, or found by the supplier's template), each invoice's `split` ("holdout" for one in
  10 by a SHA-256 of its id, so an invoice is on the same side in every export), and `coding.jsonl` (one row per line:
  words, then GL, cost center, tax code). Why: a model tuned on it needs the mistakes, the boxes and a fixed set it
  never trains on, and a reader of format 1 must keep working. (`ai/training_export.py`;
  `tests/test_training_export.py`)
- **D-74** · The Lessons tab measures each lesson: corrections per invoice of its vendor and field before it reached the
  AI (the second agreeing correction, or the first with a reason) and since, over the approvals still standing
  (`lesson_approvals`). One still corrected as often after 3 invoices or more is suggested for retiring, never retired
  on its own. Why: a lesson that doesn't help still costs a line of every prompt, and only a person knows whether the
  vendor changed. (`ai/lessons.py` `effect`, `Store.lesson_history`; `tests/test_lessons.py`)
- **D-75** · Before approval, the review screen asks what an export would refuse, and "Approve anyway" covers only what
  can be right anyway. With JD Edwards the export in use (Settings → About's switch, `erp/jde.in_use`), every reason
  its batch would hold a voucher back (`jde.voucher_findings`: the AN8, a GL account with no BU.Object.Subsidiary, the
  tax area, the currency, the field lengths) is a check, raised in `finalise_coding`, so the review screen, the queue's
  Ready and bulk approval all ask it. Never approved anyway (`invoice_view.HARD_CODES` and every `JDE_` finding): no
  vendor or invoice number, no lines, totals or posting that don't balance, a date that is not one or could be two, a
  currency code not in use, anything JD Edwards would refuse; "Not ready: …" says the first. The other errors (a
  vendor not in the list, a possible duplicate, a tax difference) can be approved anyway with a reason typed beside the
  box, kept with each error in the approval's `open_issues`. Why: approved vouchers were held back at export for what
  the screen could have said (B-737), and the tick cleared a missing invoice number or an unbalanced total. Pilot
  testers who run JD Edwards unconfigured see every invoice "Not ready" until Settings → JD Edwards E1 is filled: that
  is the point. (`ap_coder/erp/jde.py`, `ap_coder/web/invoice_view.py`, `tests/test_web_review_voucher.py`)
- **D-76** · The review screen is laid out for a clerk entering a standard voucher: the reading tools (OvisOCR2's
  queue, the AI reviewer, LM Studio's state) folded under "Reading" in the Document card; the page a little wider than
  the form at 1366, both halves equal on a wide screen; a "JD Edwards voucher" block in E1's order (company, AN8,
  invoice number and date, G/L date, due date and terms, currency, amounts, tax area and code, pay status: what comes
  from the settings marked so, read-only there) and each posting line's E1 account; the line items one row each when
  the card is wide enough (a container query), the three-row card when not; the tax lines and the posting preview
  beside the Tax check; a check about one field said under it, with a one-click fix where there is one (which date,
  the vendor's open PO, a value boxed on the page); amounts with a minus sign on this screen, as its boxes show them.
  The invoice's next page from anywhere is Alt+PgDn / Alt+PgUp: plain PgDn keeps scrolling the form outside the
  Document card (B-592). Undo of an approval is the banner's Undo (`/review/<id>/undo-approve`, as Reopen for
  correction without a reason, only while not exported).
- **D-80** · Safety rules of learned line tax codes (D-70): a learned code may save typing, but never silently drops a
  tax that is due and never removes a reason for a person to look. (1) Only votes from the same place of supply
  (`feedback.tax_province`) and the same collection by the vendor (`tax_uncollected`: out of province, or no PST/QST
  number and none charged): an exemption learned while the vendor collected the tax is not used once it does not
  (B-715 applies: due). (2) An exemption (`~`, Exempt, Zero-rated, Out of scope) is learned only from the same words
  (numbers and units aside); a taxed code from words alike, never across "non"/"not"/"no"; never from a generic
  description ("Misc", "Services"). (3) Votes are a person's decisions: a pick on review counts alone; approvals as
  proposed count from 2 invoices approved on different days; a learned code or a guess kept unchanged, and a bulk
  approval, record no code; approvals stored before `tax_exempt` existed (`legacy`) never vote. One contradiction still
  vetoes. (4) A learned code that leaves a charged tax on no set of the other lines is dropped and the lines worked out
  again, marked to check; a line the amounts alone could not decide on keeps its "check it" note. (5) A learned PST/QST
  exemption says so on the line (TAX_EXEMPTION_LEARNED) and, with TAX_BASE_MISMATCH, EXEMPT_LINE_TAXED and
  TAX_NOT_ALLOCATED, always sends the invoice to a person. Why: a CPA review found self-assessed PST/QST lost, a BC
  exemption used in Manitoba, legacy and bulk approvals teaching exemptions, and learned exemptions going touchless.
  `bench coding`: default 97.7% precision / 96.9% coverage before, 97.8% / 97.1% after; `--realistic` 95.2% / 96.5%
  before, 95.2% / 94.3% after. Not done: the 50% ITC on meals. (`coding/line_tax.py` `learned_codes`, `tax_vote`,
  `uncollected`, `prefill`; `core/store/_base.py` `_fill_tax_codes`; `tests/test_tax_learning_safety.py`,
  `tests/test_tax_learning_votes.py`)
- **D-85** · Template snapshots keep the order of approval (`template_snapshots.seq`, AUTOINCREMENT), not a time to the
  second; the newest 5 per vendor are kept, never the one just written. A withdrawal whose template a later approval
  changed since keeps it (D-71), and the later approval's snapshot now starts from what was there before the withdrawn
  one: withdrawing that later one too takes back both. Known limit: while a later approval stands, what the withdrawn
  one taught stays in the template (a template can't be split by approval); withdraw the later ones too, or "Reset
  layout". Learning re-checks that the invoice is still approved, in the template's transaction and once more after
  learning (`forget_if_withdrawn`). Why: bulk approval writes many snapshots in one second, and a withdrawal must
  never leave a wrong box behind. (`core/store/capture.py` `save_supplier_profile`, `core/store/invoices.py`
  `_withdraw_template`; `tests/test_template_rollback.py`)
- **D-86** · The review screen after its second UI sign-off. (1) Drafts (edits not approved yet) are kept in the store,
  `invoice_drafts` (one row per invoice and person: "" this computer, else a colleague's key), read back when AP Coder
  starts and dropped on approval, rejection, deletion, Start over and a restore; only real edits make one: an account
  or cost center blank either way, a cost center with no list in use, and what the screen fills itself (the default Pay
  from account, the vendor list's tax numbers) are not edits. (2) Confidence is the reading's and the coding's: setup
  findings (`JDE_*`, `PAY_FROM_*`, `validation.SETUP_PREFIXES`) keep Approve closed but take nothing off it. (3) The
  approve bar's "Not ready" is the first blocker in the screen's order (`invoice_view.SCREEN_ORDER`), and the approval
  refuses with the same. (4) A right-click menu leaves out what can't be used rather than greying it; the posting, the
  Tax check and the checks have menus too, and Esc gives the focus back to what had it. (5) Opening an invoice focuses
  the first thing to check without scrolling: the header, the checks and the approval's banner come first. Why: an
  independent UI sign-off found phantom drafts holding back bulk approval, drafts lost on a restart while the screen
  promised them, a 29% confidence that only meant "JD Edwards not set up", and a bar naming the bank account for a
  currency typo. (`web/pages/invoice.py`, `web/invoice_view.py`, `core/store/invoices.py`, `coding/validation.py`,
  `static/js/invoice.js`, `static/js/ctxmenu.js`; `tests/test_web_review_signoff.py`)
- **D-87** · A PO number longer than JD Edwards' PO field (VLPO, 8 characters) no longer blocks approval when the PO
  is sent as a reference: the voucher goes out with VLPO, VLPDCT and VLPKCO blank, the PO kept in AP Coder and in the
  remark (`AP Coder #<id> PO <number>`) when that fits in VLRMK's 30 characters, with a warning on the review screen
  (JDE_PO_LONG, now a warning). With PO-matched vouchers on, the PO is E1's order number the voucher is matched to, so
  a longer one still holds the invoice back. Why: vendor-printed POs like "4500078813" or "MNT-2026-028" are common,
  and blocking made the clerk change what is printed; cutting it to 8 characters would send a wrong reference.
  (`erp/jde.py` `po_not_sent`, `_prepare`, `voucher_rows`, `voucher_findings`; `docs/JDE_E1.md`;
  `tests/test_jde.py::test_a_po_number_longer_than_jde_holds_is_kept_here_and_not_sent`)
- **D-88** · The review queue's "look clean" and Ready are today's checks, not the ones stored when the invoice was
  read (B-815). A stored validation carries `checks_stamp`, a fingerprint of what the checks read that a person
  changes in Settings (`Store.checks_stamp`: the GL accounts, cost centers, tax setup and rate file, our province and
  sites, policy notes, JD Edwards' settings and switch, the days to pay). When it differs from today's, the queue does
  the coding's checks again (`finalise_coding`, as bulk approval does) for the invoices not flagged, and keeps the
  result beside the stored checks (`validation.recheck`: its stamp and findings), so once per change of settings,
  about 5 ms an invoice; the findings made after processing (the Azure check, the AI reviewer, the page's own:
  `bulk.AFTER_PROCESSING`, and any code no check describes) are kept from the stored ones. Bulk approval drops the
  stale stored findings the same way, so what the queue offers is what it approves. Not in the stamp: the passing
  days (duplicates, overdue): bulk approval still checks those at approval (the vendor master and the POs are in it
  since B-842, as a count of their changes). Why:
  re-checking on every page load costs ~5 ms an invoice; re-checking on each settings save would be slow on the save
  and miss changes made elsewhere. (`coding/bulk.py` `issues_today`, `sort_for_bulk`, `bulk_approve`;
  `web/pages/review.py` `attention`; `core/store` `checks_stamp`, `check_stamps`, `save_recheck`)
- **D-89** · Confidence is two numbers: the **Reading** (how sure the header is: supplier, number, dates, amounts, tax
  numbers) and the **Coding** (how sure the lines' GL accounts, cost centers and tax codes are), side by side in the
  review screen's header where the one Confidence bar was. Where one number is needed (the queue's order and its bar,
  the review threshold, bulk approval, `adjusted_confidence` stored on the invoice, the exports and reports), it is the
  lower of the two; `confidence_score` on the coding keeps its name and now means the Reading. (1) The Reading is the
  mean confidence of the values read (`capture.confidence.reading_confidence`), no longer multiplied by the share of
  lines coded. A verified value the supplier's template vouched for gets the supplier's record of that field added as
  evidence (`apply_supplier_record`: (right + 2 × confidence) / (cases + 2), its approvals over the autonomy policy's
  window): 98.5% → 99.3% after two right, 99.96% after forty; a first-time supplier, a value to check or one the
  template did not read keep the readers' own confidence; it never lowers and never changes a status, so nothing goes
  touchless that would not have. (2) The Coding is the mean over the lines of: the account (none, unknown or a tax
  account 0; a guess, the chat model's or a weak match, 0.5), the cost center where they are used (none or unknown 0),
  and, when a tax is charged, the tax code (worked out from several possible sets of lines, an exemption to confirm, or
  none: 0.5), multiplied (`validation.coding_confidence`). (3) A line to code is counted once: GL_UNASSIGNED,
  CC_UNASSIGNED, GL_UNKNOWN, CC_UNKNOWN, GL_IS_TAX_ACCOUNT and PST_EXEMPTION_TO_CONFIRM (`COUNTED_IN_CODING`) keep
  Approve closed, touchless off and the invoice in Needs attention, but take nothing more off (as D-86's setup
  findings); so do the business findings (`BUSINESS_CODES`, `PO_*`, `DUPLICATE_*`: not in the vendor master, on hold,
  bank details changed, a possible duplicate, an unusual amount: every value can be read right and still be one), which
  stay errors or warnings as they were and keep the invoice in Needs attention; findings about the lines' coding
  (HISTORY_CONFLICT, the tax-code mismatches: `ABOUT_CODING`) take their share off the Coding, every other finding off
  the Reading; "Coding … is below the threshold" is not said when a line to code is why. (4) The benches report each
  number against the share actually right by the invoice's position in its supplier's stream (1, 2-5, 6-20, 21+): `bench
  learn` the Reading (with and without the template), `bench coding` the Coding. On `learn --suppliers 20 --invoices
  40`: Reading 98.1% / 99.2% / 99.4% / 99.4% against 100% / 99.5% / 100% / 99.9% read right (without the template a flat
  98.1-98.2%); readiness and touchless unchanged (14 of 20, median 37; 51 of 67). On `coding` (30 × 30): Coding 54.4% /
  86.6% / 98.2% / 98.6% against 50.7% / 85.1% / 96.4% / 98.2% of lines right, within 2 points after the first invoice.
  Replaying one supplier offline (`e2e_r2` stream s7-v006, approving each): Reading 98.3% → 97.8% → 99.7% → 99.7% →
  99.8%, Coding 0% → 100% once its lines are learned. Why: an end-to-end run showed ~23% on invoices read 100% right
  through the template, because one number mixed the reading with the lines still to code and counted those twice; two
  numbers say what to look at. The invoices still to approve that the local readers read before get the Reading of their
  stored capture once, when this version opens the database. (`capture/confidence.py`, `capture/workflow.py`
  `supplier_record`, `ai/offline_coder.py`, `coding/validation.py`, `web/invoice_view.py`, `_invoice_live.html`,
  `bench/learning.py`, `bench/coding.py`, `core/store` `_reading_as_d89`; `tests/test_reading_and_coding.py`)
- **D-90** · Meals and entertainment: 50% of the GST/HST and QST is claimable. Income Tax Act s.67.1 limits meals and
  entertainment to 50%, and Excise Tax Act s.236 claws back half the input tax credits on them; Quebec does the same
  for the QST input tax refunds (the QST Act's counterpart of s.236, and Taxation Act s.421.1 for income tax: verify the
  section with Revenu Québec). PST is never recoverable, so it is untouched. **Quebec, large businesses:** a large
  business may be denied the QST refund on meals and entertainment entirely, not half: check its current status with
  Revenu Québec; AP Coder claims half. **Setting:** a GL account attribute, *Meals & entertainment (50% tax credit)*: a
  tick box on GL accounts & tax → GL accounts, an optional column at import ("Meals", "M&E": yes, x, 1, M&E, 50% mark
  it; blank, no, 0, 100% don't), a `meals` column in a chart-of-accounts file (the sample's 6320 has it), a `meals`
  column in the database (added whatever the schema version, no new SCHEMA_VERSION). **Effect:** on a line coded to such
  an account, each recoverable GST, HST or QST line's share of tax for that line (`tax.tax_shares`: spread as PST is,
  over the lines that carry it, a tip line left out: a voluntary tip is not taxed) is halved; the claim is that half cut
  to the cent, so never more than half (HST 11.37 on a dinner: 5.68 claimed, 5.69 expensed), and the rest moves to the
  line's `non_recoverable_tax` and amount (`meals_tax` on the line and on the tax entry, with `tax_line`, its tax line).
  The tax printed on the invoice is halved, never a rate times the line, so only the tax actually charged on the meal
  is affected. When no line is marked with the tax and a meals line shares it with other unmarked lines (a catered
  lunch and zero-rated groceries), the share is a guess: MEALS_TAX_LINES (a warning) asks which lines carry it (B-868).
  It flows from `gl_distribution` to the Excel/CSV lines (the tax line reads "recoverable, less 50% on meals"), JD
  Edwards (the expense line carries the half; VLSTAM and the recoverable tax E1 posts are the claimable part only,
  VLATXN takes the rest, so gross = taxable + non-taxable + tax and the F0911Z1 lines = gross − claimable tax; one pay
  item per line takes the distribution's own shares, B-867) and the sales-tax report (the claim is the tax less the
  half, tagged "Meals & entertainment: 50% of the tax claimable"; the page and CSV give the amount by currency and the
  law). **JD Edwards cases held back** (errors, never "approve anyway", B-865, B-866): with the **gross amount only**
  (the default) E1 works out the tax itself and would claim the full ITC while the expense line already holds half: the
  GL and the ITC report would disagree, so JDE_MEALS_GROSS asks to switch to sending tax amounts; a **PO-matched**
  voucher gets its G/L lines from the receipts, so the half can't be expensed: JDE_MEALS_PO. Forcing tax amounts for
  those vouchers alone was not chosen: one batch would mix the two amount layouts, which E1 refuses. **To confirm with
  the JDE team:** that R04110ZA accepts a VLSTAM below the tax area's rate times VLATXA (VLATXA is unchanged while
  VLSTAM is halved): the tax-tolerance check (P0022 processing options) and the F0018 tax lines it writes; how VLSTAM is
  split under explanation code C (GST + PST) with a meals line; and whether a separate tax area or explanation code for
  meals (a 50%-recoverable rate area) would suit the company better than tax amounts. An invoice approved before keeps
  what it posted (its stored distribution). **Timing:** in law, the 50% is added back to net tax in the return for the
  last period of the fiscal year (ETA s.236(1)); claiming half as each invoice is approved gives the same net tax for
  the year (a little less claimed earlier) and is common practice: confirm with your accountant. **For the year-end
  accountant:** the ITCs AP Coder reports are already net of the 50% (line 106 as is): there is **no s.236 add-back on
  line 104** for these meals, or it would be clawed back twice. **Where 100% is allowed** (ITA s.67.1(2), followed by
  s.236): meals billed to a client and shown on the bill; a business that sells meals; meals that are a taxable benefit
  to the employee; up to six events a year open to all staff at a place of business (e.g. the holiday party: **count
  them**, the seventh is back to 50%); fundraising events of a registered charity. AP Coder has no per-line switch (it
  would need a new line field, its learning and the exports): set up a second GL account without the tick (e.g. "Meals
  billed to clients", "Staff events") and code those lines to it; the line's note says so. **Out of scope:** long-haul
  truck drivers' meals are 80% (ITA s.67.1(1.1), ETA s.236(1.1)): code them to an account without the tick and adjust
  by hand. Club dues and recreational facilities get no ITC at all (ETA s.170): not this flag either. **Review
  screen:** a line coded to a meals account says "Meals: 50% of the GST/HST is claimable. Billed to a client or a
  staff event? Use the other account." ("GST and QST" in Quebec), following the GL account as it is changed.
  (`coding/tax.py` `tax_shares`, `meals_parts`, `meals_unsure`, `build_gl_distribution`; `core/store/accounts.py`,
  `core/reference_data.py`; `web/pages/accounts.py`; `erp/jde.py` `_prepare`, `voucher_rows`, `_claimed_shares`,
  `voucher_findings`; `coding/taxreturn.py`; `tests/test_meals.py`) **Keeping the ticks:** the flag is usually set
  only in AP Coder, not in the ERP's chart, so an import without a meals column (*Replace the list* too) keeps each
  code's tick (B-890). **Going back to an older version loses the meals ticks; re-tick after:** a version before this
  one drops them on its *Replace the list* import or grid save, and it can't be changed (docs/UPGRADING.md).
- **D-91** · Payment terms → JD Edwards payment terms code (VLPTC). Before, VLPTC was one code for every voucher or
  blank, while the clerk sees the printed terms. Now Settings → JD Edwards E1 has a small table: the terms' meaning →
  the company's code, keyed by `terms.terms_key` (the standard wording of what `parse_terms` reads: "Net 30",
  "2/10 Net 30", "Net 30 EOM", "Due on receipt", "Net 10th prox"; it reads back as the same terms), so "Net 30",
  "30 days" and "N30" are one row and the pilot tester can type any of them; two rows that mean the same, and terms
  AP Coder can't read, are refused. The old single code becomes the default for terms not in the table. The voucher
  gets the table's code, else the default, else blank (E1 takes the supplier's terms). Example rows (Net 30 → N30,
  2/10 Net 30 → 210…) are shown pre-filled and marked as examples to check against P0014, but are **not used until the
  table is saved**: an existing pilot's next batch does not suddenly carry codes nobody checked (an unknown PTC fails
  R04110ZA). The check JDE_TERMS_NO_CODE (a warning, not a blocker; no confidence penalty, as every JDE_ setup finding,
  D-86) fires only **once the table has rows** and no default is set: with no table, a blank VLPTC is the intended
  set-up, not a gap; terms not understood are TERMS_NOT_FOUND's. Only the printed terms are looked up, not the vendor
  master's: E1 has the supplier's own terms already. (`coding/terms.py` `terms_label`, `terms_key`; `erp/jde.py`
  `terms_code`, `terms_unmapped`, `voucher_rows`, `voucher_findings`, `voucher_view`, `dry_run`;
  `web/pages/settings_jde.py` `save_terms`; `docs/JDE_E1.md`; `tests/test_jde_terms.py`) **Later (B-896):**
  JDE_TERMS_NO_CODE is a note (info), not a warning: a blank VLPTC is a benign fallback (E1 takes the supplier's own
  terms), so it holds back no bulk approval; it is still shown. **Credit memos** are sent with a blank VLPTC, as
  their due date is not sent: a terms code can carry a discount (2/10 Net 30) that E1 would work out on the credit.
  The safe choice; **to confirm with the JDE administrator** whether the company's credit memos should carry the
  terms code (R04110ZA, P0014) instead.
- **D-92** · The shadow-pilot kit. (1) **Scoring against JD Edwards**: `python -m ap_coder pilot-score --vouchers
  F0411 [--distribution F0911]` compares what AP Coder approved with what AP posted, from an E1 export the pilot
  tester gets (CSV or Excel; E1's column names, the data dictionary's or plain English through `match_columns`; Julian
  dates; `--implied-decimals`). Invoices are matched by supplier (AN8, else `vendor_key`), `norm_invoice_number` and
  amount, then by supplier and amount, so a number typed differently is still found and reported. It reports each
  header field (the benchmark's comparators), the coding line by line (BU.Obj.Sub through the JDE mapping, else the
  GL against the object; recoverable GST/HST/QST and the AP offset left out) both as approved and as AP Coder proposed
  it, each supplier's first invoice against its later ones, the would-be touchless invoices (the supplier met the bar
  when processed) that AP posted differently, and what has no match; `pilot_score.md`, a CSV of the differences and
  the figures for Insights go to the data folder's `pilot_score`. Why: the pilot's question is whether AP Coder
  agrees with what AP really posts, and the vouchers are the only truth that needs no extra labelling; it runs the
  same on synthetic data now and real exports later. (`ops/pilot_score.py`, `tests/test_pilot_score.py`) (2) **The JDE
  test-import checklist** for the pilot tester's IT is its own section of `docs/JDE_E1.md`: load the Z-file batch in
  a test environment, R04110ZA proof then final with the options to check, what to compare, how to report back;
  what varies by company says "confirm with your JDE administrator". (3) **Getting started in the pilot's order**: GL
  accounts → vendor master (with the AN8s) → JD Edwards settings → the ERP's past coding ("AP Coder learns your coding
  from what you posted before") → first invoices; the Next step card walks through them when JD Edwards is the
  export, and the rows show for everyone (`web/pages/process.py` `pilot_steps`). Measured cold start, first invoice
  from new vendors with no AI model: through the pipeline on generated invoices 0 of 285 lines coded without history,
  285 of 285 with the vendors' past coding imported (exact wording; `tests/test_first_steps_order.py`); in the coding
  benchmark (each vendor's own wording) 35.2% → 91.5% of first-invoice lines (30 vendors, `--history 1.0`). (4) **The
  AI-on paths** (the AI coder end to end, the page reader with its self-test, the AI reviewer) run against a scripted
  fake LM Studio over HTTP (`tests/test_ai_paths.py`); they passed after the round-2 changes with nothing to fix. The
  coding benchmark gained `--ai`: the chat model in LM Studio codes the lines nothing else coded, scored against the
  truth. **Still owed: a real-model accuracy run on the pilot laptop** (LM Studio is on no build computer), with LM
  Studio's server started and its chat model and OvisOCR2 loaded: `python -m ap_coder read-pages --test` (OvisOCR2's
  self-test), then `python -m ap_coder.bench coding --vendors 20 --invoices 5 --ai --out bench_ai` (the chat model's
  precision, first invoice apart; report in `bench_ai/report.md`), and, once AP has posted the pilot's invoices,
  `python -m ap_coder pilot-score` on the JDE export.
