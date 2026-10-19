# AP Coder: the codebase map

AP Coder is an offline accounts-payable app for Canadian invoices, on one laptop. It reads each PDF or picture locally
(the PDF's own text, OCR, and an optional vision page reader, OvisOCR2 in LM Studio), codes the GL accounts and cost
centers, checks GST/HST/PST/QST and the math, and learns from what AP approves. People review on a local dashboard at
127.0.0.1, and approved invoices are exported to JD Edwards E1 (or Excel/CSV). Nothing leaves the laptop.

## Sections
- `ap_coder`: the package (version, `python -m ap_coder`, the old paths' aliases) and its index of folders.
- `ap_coder/core`: store, settings, data folder, reference data, schema, memory, HTML pieces. `ap_coder/ai`: the pipeline, readers, models, second opinions, learning, accuracy.
- `ap_coder/coding`: sales tax, checks, vendors, POs, rules, terms, review, bulk approval. `ap_coder/erp`: exports, JD Edwards, registers and the reports.
- `ap_coder/intake`: pictures to PDF, emails unpacked and sorted. `ap_coder/ops`: the command line, doctor, offline guard, demo, help, share report.
- `ap_coder/capture`: document capture: layout, readers, confidence, supplier learning, touchless decisions.
- `ap_coder/bench`: the accuracy and speed benchmark (made-up invoices and emails, scoring).
- `ap_coder/web`: the dashboard (FastAPI app, shared page helpers, server); `ap_coder/web/pages`: one route module per page.
- `ap_coder/web/templates`, `ap_coder/web/templates/partials`: the Jinja pages; `ap_coder/web/static`, `ap_coder/web/static/js`: stylesheets and scripts.
- `scripts`: launcher, installer, update, offline bundle, OCR models, self-check, map checker and review gate; `scripts/ci`: CI-only helpers.
- `tests`: the pytest suite and its helpers. `data`: shipped tax rates and sample reference data. `docs`: guides, bug log, decisions.
- `.github`, `.github/workflows`: the CI workflow. `.claude`, `.claude/agents`: the map hooks (edit reminder, review gate) and the map-reviewer agent. `samples`, `private`: data folders (sample invoices; the git-ignored data folder).

## How to navigate
Read this file, then the CLAUDE.md of the folder you will change, then the code. Each folder's **Used by:** line
names the folders that import or read it: those are what else to check when its interface changes. The maps stop at
modules: to find who calls a function, search the code (`grep -rn "\.events(" ap_coder scripts`), since `ap_coder/MODULES.md`
lists imports, not an object handed in (intake/inbox.py is given the store).

## The rule
Any change to a folder's interface, dependencies or files means updating its CLAUDE.md in the same change, and
checking every folder in its **Used by:** line. Run `python scripts/check_map.py` before committing (size, Files
index, Depends on / Used by against imports (ap_coder/ only), Interface names, bug lessons, MODULES.md) and the map-reviewer agent
(`.claude/agents/map-reviewer.md`) on the change. CI runs the same checker (the "Codebase map" job). Claude Code
hooks (`.claude/settings.json`) remind you after each edit and stop a `git commit` of code the map-reviewer hasn't
reviewed as it is (Markdown-only edits after its review pass; `scripts/map_review_gate.py`, D-35).
Keep each CLAUDE.md near 50 lines (the checker warns past 60, fails past 80, bug lessons aside): one that grows flags a folder to split.
Every 3 months, a cold-start round (5 questions, maps only) goes in `docs/MAP_CHECKS.md`; the map-reviewer says when.

## Standing rules
- Fully offline at run time: nothing connects to the internet unless `AP_ALLOW_INTERNET=1` (developers, the bundle
  builder); the Azure check is an opt-in, off by default. See docs/decisions.md D-01 to D-07.
- No company data in the repo: the data folder (`private/` or `~/APCoder`) and `.env` are git-ignored.
- User-facing wording says "pilot tester" for the person running the pilot, never "manager". Plain English.
- Each bug gets a failing test first, then the fix, and an entry in docs/BUGS.md (with its lesson in the folder's CLAUDE.md).
- Significant choices go in docs/decisions.md (next D-NN).

## Run and test
- Set up: `python -m venv .venv`, activate it, `pip install -e ".[ocr,dev]"` (or double-click `APProcessor.bat` / `APProcessor.command`).
- Run: `python -m ap_coder serve` (dashboard on http://127.0.0.1:8501, or the next free port; `--port`, `--no-browser`);
  `python -m ap_coder doctor` (settings, reference data and LM Studio on this computer; `--online` adds a dry run on
  the local model and, only with `AP_ALLOW_INTERNET=1`, the Azure checks); `python scripts/pilot_check.py` (reads and codes the samples, no AI model, exit 0 when all right).
- Test: `python -m pytest -q tests` (serially; see tests/CLAUDE.md; CI installs `.[dev]`, so the local-OCR paths run only where `.[ocr]` is installed). Lint: `ruff check ap_coder tests scripts` and `ruff format --check ap_coder tests scripts`.
- Map: `python scripts/check_map.py`.

## Files
- `README.md`: the overview, setup, screens, commands and design decisions. (The CLAUDE.md files are this map.)
- `pyproject.toml`: package, dependencies and the ocr, azure and dev extras, pytest markers, ruff. `requirements.txt`: the core packages.
- `APProcessor.bat` (Windows) and `APProcessor.command` (Mac, Linux): the double-click; they find Python and run scripts/launch.py.
- `.env.example`: the settings a .env may hold (LM Studio address, review threshold, developer-only Azure keys).
- `.gitignore` (private data, .env, builds, wheelhouse, models), `.gitattributes` (.bat kept as committed, with CRLF; LF for .sh and .command).
- Folders: `ap_coder`, `scripts`, `tests`, `data`, `docs`, `samples`, `private`, `.github`, `.claude` (see Sections).
