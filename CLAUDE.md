# AP Coder: the codebase map

AP Coder is an offline accounts-payable app for Canadian invoices, on one laptop. It reads each PDF or picture locally
(PDF text, OCR, an optional OvisOCR2 page reader in LM Studio), codes GL accounts and cost centers, checks the sales tax
and the math, and learns from AP's approvals; people review at 127.0.0.1 and export to JD Edwards E1 or Excel/CSV.

## Sections
- `ap_coder`: the package (version, `python -m ap_coder`, the old paths' aliases) and its index of folders.
- `ap_coder/core`: store, settings, data folder, reference data, schema, memory, HTML pieces. `ap_coder/ai`: the pipeline, readers, models, second opinions, learning, accuracy.
- `ap_coder/coding`: sales tax, checks, vendors, POs, rules, terms, review, bulk approval. `ap_coder/erp`: exports, JD Edwards, registers and the reports.
- `ap_coder/intake`: pictures to PDF, emails unpacked and sorted. `ap_coder/ops`: the command line, doctor, offline guard, demo, help, share report.
- `ap_coder/capture`: document capture (layout, readers, confidence, supplier learning, touchless). `ap_coder/bench`: the benchmark.
- `ap_coder/web`: the dashboard (FastAPI app, shared page helpers, server); `ap_coder/web/pages`: one route module per page.
- `ap_coder/web/templates`, `ap_coder/web/templates/partials`: the Jinja pages; `ap_coder/web/static`, `ap_coder/web/static/js`: stylesheets and scripts.
- `scripts`: launcher, installer, update, offline bundle, OCR models, self-check, map checker and review gate; `scripts/ci`: CI-only helpers.
- `tests`: the pytest suite and its helpers. `data`: shipped tax rates and sample reference data. `docs`: guides, bug log, decisions.
- `.github`, `.github/workflows`: CI. `.claude`, `.claude/agents`: the map hooks and the map-reviewer. `samples`, `private`: data folders.

## How to navigate
Read this file, then the folder's CLAUDE.md, then the code. A folder's **Used by:** line names what to check when its
interface changes; for one function's callers, search the code (`ap_coder/MODULES.md` lists imports only).

## The rule
Any change to a folder's interface, dependencies or files means updating its CLAUDE.md in the same change and
checking every folder in its **Used by:** line. Before committing, run `python scripts/check_map.py` (also CI's
"Codebase map" job; what it checks: scripts/CLAUDE.md) and the map-reviewer agent (`.claude/agents/map-reviewer.md`):
a hook stops an agent's `git commit` of code the reviewer hasn't seen as it is (D-35, `.claude/CLAUDE.md`).
Keep each CLAUDE.md at 50 lines or fewer, bug lessons aside (the checker warns past that): one that grows flags a folder
to split. Every 3 months, a cold-start round (5 questions, maps only) goes in `docs/MAP_CHECKS.md`.

## Standing rules
- Fully offline at run time: nothing goes online unless `AP_ALLOW_INTERNET=1`; the Azure check is opt-in (D-01 to D-07).
- No company data in the repo: the data folder (`private/` or `~/APCoder`) and `.env` are git-ignored.
- User-facing wording says "pilot tester" for the person running the pilot, never "manager". Plain English.
- Each bug: a failing test first, then the fix, a docs/BUGS.md entry, its lesson in the folder's CLAUDE.md. A significant choice: a D-NN in docs/decisions.md.

## Run and test
- Set up: `python -m venv .venv`, activate it, `pip install -e ".[ocr,dev]"` (or double-click `APProcessor.bat` / `APProcessor.command`).
- Run: `python -m ap_coder serve` (dashboard on http://127.0.0.1:8501 or the next free port), `python -m ap_coder doctor`
  (this computer's setup), `python scripts/pilot_check.py` (codes the samples without a model; exit 0 when right).
- Test: `python -m pytest -q tests`, serially (tests/CLAUDE.md). Lint: `ruff check ap_coder tests scripts` and
  `ruff format --check ap_coder tests scripts`. Map: `python scripts/check_map.py`.

## Files
- `README.md`: the overview, setup, screens, commands and design decisions. (The CLAUDE.md files are this map.)
- `pyproject.toml`: package, dependencies and the ocr, azure and dev extras, pytest markers, ruff. `requirements.txt`: the core packages.
- `APProcessor.bat` (Windows) and `APProcessor.command` (Mac, Linux): the double-click; they find Python and run scripts/launch.py.
- `.env.example` (what a .env may hold), `.gitignore` (data folder, .env, builds, wheelhouse, models), `.gitattributes` (CRLF .bat; LF .sh, .command).
- Folders: `ap_coder`, `scripts`, `tests`, `data`, `docs`, `samples`, `private`, `.github`, `.claude` (see Sections).
