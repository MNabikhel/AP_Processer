# AP Coder

An offline accounts-payable app for Canadian invoices, on one laptop: it reads each PDF or picture locally, codes the GL
accounts and cost centers, checks GST/HST/PST/QST and the math, learns from what AP approves, and exports approved
invoices to JD Edwards E1 or Excel/CSV. People review on a local dashboard (FastAPI) at 127.0.0.1.

Code: `ap_coder/` (`core` store and settings, `ai` pipeline and models, `coding` tax and checks, `erp` exports and
reports, `intake` pictures and emails, `ops` command line and doctor, `capture` document reading, `web` the dashboard,
`bench` the accuracy benchmark). `scripts/`: launcher, installer, update, offline bundle. `tests/`: pytest. `docs/`:
guides, the bug log (BUGS.md) and decisions (decisions.md). Search the code to find things; the code is the reference.

## Rules
- Fully offline at run time: nothing connects to the internet unless `AP_ALLOW_INTERNET=1` (developers, the bundle
  builder). The Azure check is opt-in, off by default.
- No company data in the repo: the data folder (`private/` or `~/APCoder`) and `.env` are git-ignored.
- User-facing wording: "pilot tester" for the person running the pilot, never "manager". Plain English.
- A bug fix starts with a failing test, then the fix, then an entry in docs/BUGS.md. Significant choices go in
  docs/decisions.md (next D-NN).
- Old flat module paths (`ap_coder/store.py` and the like) are aliases of the moved modules: edit the real module.

## Run and test
- Set up: `python -m venv .venv`, activate it, `pip install -e ".[ocr,dev]"` (or double-click `APProcessor.bat` / `APProcessor.command`).
- Run: `python -m ap_coder serve` (http://127.0.0.1:8501); `python -m ap_coder doctor`; `python scripts/pilot_check.py`.
- Test: `python -m pytest -q tests` (serially, no xdist). Run the tests of what you changed first; the full suite takes ~9 minutes.
- Lint: `ruff check ap_coder tests scripts` and `ruff format --check ap_coder tests scripts`.
