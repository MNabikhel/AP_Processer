# AP Coder

An offline accounts-payable app for Canadian invoices, on one laptop: it reads each PDF or picture locally, codes the GL
accounts and cost centers, checks GST/HST/PST/QST and the math, learns from what AP approves, and exports approved
invoices to JD Edwards E1 or Excel/CSV. People review on a local dashboard (FastAPI) at 127.0.0.1.

Code: `ap_coder/` (`core` store and settings, `ai` pipeline and models, `coding` tax and checks, `erp` exports and
reports, `intake` pictures and emails, `ops` command line and doctor, `capture` document reading, `web` the dashboard,
`bench` the accuracy benchmark). `scripts/`: launcher, installer, update, offline bundle. `tests/`: pytest. `docs/`:
guides, the bug log (BUGS.md) and decisions (decisions.md). Search the code to find things; the code is the reference.

## Where things live
- One invoice, start to end: `ai/pipeline.py` (`finalise_coding`); the schema: `core/schema.py`. Reading: the rule
  reader `capture/reader/` (parts: labels, values, spatial, patterns, totals, line_items, entry: `read_fields`); the
  vision model `ai/page_reader.py`; their merged result `ai/reading.py`; the plain text `capture/transcript.py`.
- The database (`Store`): the package `core/store/`, one part per subject (list in its `__init__.py`): `invoices.py`
  (approvals `approve_invoice`/`final_approve`), `_base.py` (schema, `SCHEMA_VERSION`), `accounts.py` (`events`).
  Who the reviewer is: `web/identity.py`.
- Checks (findings with a code): `coding/validation.py` (`validate_coding` gathers them), payment terms
  `coding/terms.py`, tax `coding/tax.py` (`check_taxes`, `place_of_supply`), the Tax check `coding/taxmath.py`, line
  tax codes `coding/line_tax.py`, ITCs and self-assessment `coding/taxreturn.py`, vendors `coding/vendors.py`. Every
  check code needs a `CheckHelp` in `ops/help.py` (`tests/test_help.py` enforces it).
- Exports: `erp/exports.py` (Excel/CSV), `erp/jde.py` (JD Edwards). Reports: the rest of `erp/`.
- The review screen: routes `web/pages/invoice.py`, view `web/invoice_view.py`, `web/templates/invoice.html`,
  `web/static/js/invoice.js`. Other pages: `web/pages/<page>.py` + `web/templates/<page>.html`.
- Tests: `tests/test_<module>.py` for most modules; fixtures `tests/conftest.py` (`reference`, `ground_truth`), web
  helpers `tests/webclient.py` (`client_for`, `add_clean`).

## Rules
- Fully offline at run time: nothing connects to the internet unless `AP_ALLOW_INTERNET=1` (developers, the bundle
  builder). The Azure check is opt-in, off by default.
- No company data in the repo: the data folder (`private/` or `~/APCoder`) and `.env` are git-ignored.
- User-facing wording: "pilot tester" for the person running the pilot, never "manager". Plain English.
- A bug fix starts with a failing test, then the fix, then one line at the end of docs/BUGS.md (format at its top).
  Significant choices go in docs/decisions.md (next D-NN).

## Working here in few steps
Every step re-reads the whole conversation, so fewer, fuller steps cost less:
- Put independent lookups in one command (several `grep -n` / `sed -n A,Bp` joined with `;`), and read the lines
  around a match rather than whole files.
- Make the change, then check once with `python scripts/check.py`; don't wait on the whole suite (CI runs it).

## Run and test
- Set up: `python -m venv .venv`, activate it, `pip install -e ".[ocr,dev]"` (or double-click `APProcessor.bat` / `APProcessor.command`).
- Run: `python -m ap_coder serve` (http://127.0.0.1:8501); `python -m ap_coder doctor`; `python scripts/pilot_check.py`.
- Check a change: `python scripts/check.py` (ruff on the changed files, then their tests; a minute or two).
- Test: `python -m pytest -q` runs all but the slow tests (a few minutes; serially, no xdist). The whole suite with the
  browser tests, `python -m pytest -q -m ""`, takes ~25 minutes: CI runs it on every push, so leave it to CI.
- Lint: `ruff check ap_coder tests scripts` and `ruff format --check ap_coder tests scripts`.
