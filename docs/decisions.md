# Decisions

The significant choices behind AP Coder, one line each: the choice, why, and where it is written or enforced.
Add the next number when a change makes a new choice (and say so in the pull request); never renumber. A choice
that is reversed keeps its line, marked "replaced by D-NN".

## Running offline

- **D-01** · Fully offline at run time; the one opt-in to the internet is `AP_ALLOW_INTERNET=1` (developers, the bundle builder). Why: invoices and company data must never leave the laptop. (`ap_coder/offline.py` `internet_allowed`, README "Run it by hand")
- **D-02** · Azure (Document Intelligence check, Azure OpenAI) is optional, off by default, and never needed to install or start; its packages are an extra. Why: the pilot runs with no cloud account; Azure is a second opinion, not a dependency. (`pyproject.toml` `azure` extra, `ap_coder/azure_check.py`, `tests/test_azure_optional.py`)
- **D-03** · The offline proof: tests run AP Coder under a socket guard that refuses and records any connection not to this computer (127.0.0.0/8, ::1, localhost), and CI installs the bundle with every proxy pointed at a closed port. Why: "offline" must be shown, not promised. (`tests/offline_guard.py`, `tests/test_offline_guard.py`, `.github/workflows/ci.yml`)
- **D-04** · LM Studio is reached only on this computer or the office network; an internet address is refused however it is written, unless `AP_ALLOW_INTERNET=1`. Why: a model server URL is the easiest way for data to leave. (`ap_coder/local_llm.py` `refused_address`, `ap_coder/config.py` `_on_this_network`, docs/BUGS.md B-39)
- **D-05** · An offline bundle (code, `wheelhouse/`, `models/`, `bundle_manifest.json`) installed with `pip --no-index`. Why: the pilot laptop has no internet; one ZIP must install everything. (`scripts/build_offline_bundle.py`, `scripts/launch.py`)
- **D-06** · No CDN: fonts, icons, styles, scripts and charts (hand-made SVG) all ship in the package. Why: a page that loads anything from the internet would not work offline; the offline proof reports any such address. (`ap_coder/web/__init__.py`, `ap_coder/web/charts.py`, `tests/offline_flow.py`)
- **D-07** · RapidOCR stays on 3.4.x, and its PP-OCRv5 models are copied from the bundle, never downloaded by RapidOCR. Why: the capture confidence table is fitted on its reads; later 3.x releases change the model list. (`pyproject.toml` `ocr` extra, `scripts/fetch_models.py`, `ap_coder/offline.py`)

## Data and install

- **D-08** · One data folder outside the code (default `~/APCoder`, recorded in `~/.ap_coder/settings.json`) for the database, invoices, outputs and `.env`. Why: unzipping a new version must never lose or duplicate data. (`ap_coder/paths.py`, `scripts/upgrade.py` `settle_data_folder`)
- **D-09** · SQLite, one file in the data folder; a backup once a day when the dashboard starts (newest 14 kept), an optional second backup folder; a newer database is refused and an older one backed up before migrating. Why: a single laptop needs no server, and an update must never risk the data. (`ap_coder/store.py` `auto_backup`, `copy_backup`, `_before_migrating`; `tests/test_update_database.py`, docs/WHATS_NEW.md)
- **D-10** · No company data in the repo: `private/` and `.env` are git-ignored, and so are every `.xlsx` and the PDFs and TIFFs at the top of the repo. Why: the repo is shared; invoices are confidential. (`.gitignore`, `private/README.md`)
- **D-11** · Never two versions on one database: a start finds a running AP Coder (`/healthz`), opens it if it is the same version and folder (or newer), stops an older one or one from another folder (`POST /shutdown` with a token). Why: older code must never write to a newer database. (`scripts/running.py`, `scripts/launch.py`, `ap_coder/web/app.py`)
- **D-12** · One button per computer (`APProcessor.bat`, `APProcessor.command`) that sets up only what is missing; the installer is the same setup with questions. Why: the pilot tester double-clicks one file; the older install and start scripts are gone and an update removes them. (`scripts/launch.py`, `scripts/install.py`, `scripts/upgrade.py` `prune_stale`, docs/WHATS_NEW.md)
- **D-13** · Two people approving share one AP Coder on one PC, the database on a local disk, not a network share. Why: an open SQLite database can be damaged on a network share or a synced folder; a shared server version is a Phase 2 item. (docs/WHATS_NEW.md "Second approval", docs/GETTING_STARTED.md "Two people approving", docs/PILOT.md)

## Dashboard

- **D-14** · The dashboard is a local FastAPI app (uvicorn) on 127.0.0.1, replacing the Streamlit dashboard. Why: faster pages, an address per page, nothing from the internet, and hosted the same way as CloseDesk. (`pyproject.toml`, `ap_coder/web/`, docs/WHATS_NEW.md "the dashboard is a local web app")
- **D-15** · Only this computer's host names are served; writes need the `X-AP-Coder: 1` header and a same-origin request, and every redirect stays local. Why: another web page in the same browser must not drive AP Coder. (`ap_coder/web/security.py` `LocalOnly`, docs/BUGS.md N-20, N-21)

## Reading and touchless approval

- **D-16** · One way to read every invoice, nothing to choose: the PDF's text or local OCR, then OvisOCR2 reads every page as a second reader; agreement makes a field verified. Why: no single reader is right on every invoice. (`ap_coder/reading.py`, `.env.example`, docs/CAPTURE_DESIGN.md)
- **D-17** · Every picture becomes a PDF when it comes in; the picture is kept in `originals/` for the audit trail. Why: one format to store, show, stamp and find duplicates of. (`ap_coder/intake.py`, docs/WHATS_NEW.md)
- **D-18** · The AI reads, code does the arithmetic: tax lines are copied as printed, rates and the GL distribution are computed in Python, and GL codes are an `enum` in the schema. Why: a model must not invent amounts or accounts. (README "Design decisions", `ap_coder/schema.py`, `ap_coder/tax.py`)
- **D-19** · Touchless processing is one company-wide switch, off by default, with one fixed bar every vendor must reach by itself, and a 5% audit sample. Why: autonomy is earned per supplier on its own invoices. (docs/CAPTURE_DESIGN.md "Touchless processing", `ap_coder/capture/supplier.py`, `ap_coder/capture/workflow.py`)
- **D-20** · Touchless gates: no approval without a person until OvisOCR2 has read it and the AI reviewer (when on) agrees with every field and line; the reviewer's and the Azure check's holds both count, whatever the order; a credit note always needs a person. Why: no person looks at a touchless invoice, and B-31, B-32 and B-42 each once let one past a gate. (docs/BUGS.md B-31, B-32, B-42, `ap_coder/page_worker.py`, `ap_coder/verifier.py`, `ap_coder/capture/workflow.py`)
- **D-21** · The AI reviewer never changes a value; it agrees, disagrees or is unsure, and a person decides. It runs after OvisOCR2 in the same line. Why: a person decides what is posted, and the two models never load LM Studio at once. (docs/WHATS_NEW.md "the AI reviewer", `ap_coder/verifier.py`, `ap_coder/page_worker.py`)
- **D-22** · Capture calibration is not refitted by bug fixes; a fix caps or lowers a confidence without changing calibration keys. Why: `calibration.json` is measured on benchmark runs (`python -m ap_coder.bench calibrate`), and a refit needs new runs. (docs/BUGS.md B-44, B-47, docs/CAPTURE_DESIGN.md, `ap_coder/capture/calibration.json`)

## Tax, coding and learning

- **D-23** · Place of supply is the ship-to printed on the invoice, else the province of the cost center's site, else our province from Settings, and the vendor's province only last; one rule for the tax checks and the self-assessment. Why: that is where Canadian sales tax applies, not where the vendor is. (docs/SALES_TAX.md, `ap_coder/tax.py` `supply_source`, `ap_coder/taxreturn.py`, docs/BUGS.md B-13)
- **D-24** · Rates are an effective-dated CSV that finance edits (a copy in the data folder wins); the covering row with the latest `effective_from` wins (a province row on a tie). Why: rates change on a date, and file order must not decide. (`ap_coder/tax.py`, `data/canada_tax_rates.csv`, docs/BUGS.md B-12)
- **D-25** · Lessons are kept per situation (vendor, document kind, field or kind of line); after 3 corrections of one situation (`why_after`, Settings → Review), approving asks "why", and the reason goes to the AI with the lesson. Why: a repeated correction is a rule nobody wrote down. (`ap_coder/lessons.py`)
- **D-26** · `vendor_key` rules are versioned (`VENDOR_KEY_RULES`); the store recomputes stored keys when the version changes. Why: a new rule (French legal forms) must not split one vendor's history in two. (`ap_coder/memory.py`, `ap_coder/store.py`, docs/BUGS.md B-15)
- **D-27** · The invoice directory reads the PO, subtotal, tax and GL accounts from the invoice's coding JSON with SQLite's JSON functions instead of adding columns. Why: the coding JSON (the reviewer's when approved, else AP Coder's) stays the one source, with no column to keep in step. (`ap_coder/directory.py`)

## Exports and intake

- **D-28** · Files for an ERP loader (JDE Z-files, the CSV and Custom CSV exports) are written as they are, without spreadsheet quoting; files for people (the xlsx export, search and directory exports, statements, accruals and other reports) are neutralised against formulas. Why: a loader puts an apostrophe into the data, so an invoice `'+A-100` misses the ERP's supplier + invoice-number duplicate check; people open their files in Excel. (`ap_coder/jde.py`, `ap_coder/exports.py`, `ap_coder/export_layout.py`, `ap_coder/safe.py`, docs/BUGS.md B-23, B-26, B-92)
- **D-29** · An export batch is rebuilt from approved data: the same invoices and vouchers on every download, with today's settings and master data (due date, User ID, vendor IDs, GL names, rates); a JDE batch downloaded again never re-runs the checks, and is refused while a setting blocks every export. Why: a second download must load the same vouchers, and never a file its own report says cannot load. (`ap_coder/jde.py`, `ap_coder/web/pages/exports.py`, docs/BUGS.md B-21)
- **D-30** · AP Coder never connects to the mailbox: another tool saves each email as a file in the invoices folder. Why: no mailbox password, token or network access is ever given to it. (docs/MAILBOX_SETUP.md, `ap_coder/mailbox.py`)

## Working on the code

- **D-31** · Every bug gets a failing test first, then the fix, and an entry in docs/BUGS.md with its lesson; a suspicion that cannot be reproduced is logged as N-NN and left. Why: a fix without a test comes back; a blind fix breaks something else. (docs/BUGS.md, `CLAUDE.md`)
- **D-32** · The codebase map is checked, not only asked for: `scripts/check_map.py` fails CI on a stale index, a Depends on / Used by line that the imports contradict, an interface name that no longer exists or a bug lesson missing from its folder; a hook reminds after each edit; the map-reviewer agent checks a change's Used-by folders. Why: a map nobody checks goes stale, and a stale map misleads more than none. (`scripts/check_map.py`, `.claude/`, `.github/workflows/ci.yml`)
