# Handoff: AP Coder to the company's own AI coding agent

**Read this first, then `CLAUDE.md`.** This file is the brief for an AI coding agent (Claude Code or similar) that
takes over AP Coder inside the company it was built for (PCL), to connect it to the company's own systems. The
handoff is **one way**: whoever built AP Coder up to here cannot be asked anything. Everything known is in this
repository: this file, `CLAUDE.md`, `docs/` (guides, decisions, bug log, their archives) and 1,450+ commit messages.
When this file and the code disagree, the code is right; fix this file.

From the handoff on, **your copy is the main copy**. Nothing goes back to where it came from.

Contents: 1 getting the code in · 2 what AP Coder is · 3 run and test · 4 how the code is organised · 5 what is
real and what is sample data · 6 the connection points, one by one · 7 the company's information to gather ·
8 the recommended order of work · 9 house rules · 10 known limits and open questions · 11 glossary.

---

## 1. Getting the code in

The handoff is four files. `docs/HANDOFF.md` inside the code is the master copy of this file; the loose
`HANDOFF.md` beside the others is the same file, for reading before anything is unpacked. Work in the folder that
holds the four files, and check them first: `sha256sum -c SHA256SUMS.txt` (Windows: `Get-FileHash <file> -Algorithm
SHA256`, compared by eye). The tag list `.github/release-tags.txt` in this copy stops at the release before this
one: a tag list can't name the commit it is in, so the lines for this handoff's tags were added in the next commit
of the original repository. The tags themselves are in the bundle.

| File | What it is | Use it when |
|---|---|---|
| `AP_Coder.bundle` | A git bundle: `main` with its whole history and the tags `v0.3.0-pilot1`, `v0.3.0-pilot2` and `v0.3.0-pilot3` (the laptop releases; pilot3 is the newest) and `v0.3.0-handoff3` (this handoff: pilot3 and these docs, the same commit; `v0.3.0-handoff1` and `-handoff2` were earlier ones) | Git is available: keeps every commit message, which explains why the code is as it is |
| `AP_Coder_source.zip` | The same files as `main`, no history | Only file transfers are allowed, or git isn't |
| `HANDOFF.md` | This file | Read first |
| `SHA256SUMS.txt` | The SHA-256 of the three files above | Check the transfer |

**Route A, the git bundle (preferred).** In the folder with the four files:

```bash
git clone -b main AP_Coder.bundle AP_Processer   # a normal repository (cloning checks the bundle); origin = the bundle
cd AP_Processer
git bundle verify ../AP_Coder.bundle             # "The bundle records a complete history" (works only inside a repository)
git tag                                          # v0.3.0-handoff1 to -handoff3, v0.3.0-pilot1 to -pilot3
git log --oneline | head                         # the latest commits
# Make the company's Git server the origin (an empty repository created there first):
git remote set-url origin <the company's repository URL>
git push -u origin main --tags
```

**Route B, the plain zip.** In the folder with the four files:

```bash
unzip AP_Coder_source.zip                        # makes AP_Processer/
cd AP_Processer
cat ap_coder/BUILD                               # must read $Format:%h %cI$ (git fills it in for each ZIP made later)
git config user.name "<you>" && git config user.email "<you@company>"   # a fresh machine has none
git init -b main && git add -A && git commit -m "AP Coder at the handoff (v0.3.0-handoff3), no history"
git remote add origin <the company's repository URL> && git push -u origin main
```

`ap_coder/BUILD` is the build stamp: `git archive` and GitHub's ZIP write the commit into it, and the updater uses it
to tell builds apart (B-542). It must stay the placeholder in the repository; if it holds a commit and a date, put
the placeholder back before the first commit: `printf '$Format:%%h %%cI$\n' > ap_coder/BUILD`.
The decisions and bugs are written in full in `docs/` (and `docs/archive/`), so the reasons survive route B; the
commit messages don't. On route B, `doctor` and `/healthz` report the new commit, not the handoff's: the zip's
checksum (`SHA256SUMS.txt`) is what proves the files are the release's. Drop `.github/workflows/release.yml` (its
tag list names commits a route B repository doesn't have).

**Python and packages.** Python 3.11 to 3.14 (3.12 recommended). With a package index reachable:
`python -m venv .venv`, activate it, `pip install -e ".[ocr,dev]"`. `pyproject.toml` asks only for minimum versions;
the exact set this release was tested with is `docs/tested-packages.txt` (`pip install -c docs/tested-packages.txt
-e ".[ocr,dev]"` when a newer package breaks something).
With no internet, use a wheelhouse. The person who handed this over may also have given you the laptop release's
offline install zip (`APProcessor-offline-0.3.0-20261030.zip`, the release `v0.3.0-pilot3`; Windows, Python
3.12–3.14): its `wheelhouse/` folder installs everything on Windows with `pip install --no-index --find-links <that
wheelhouse> -e ".[ocr,dev]"` (its wheels are Windows ones: a Linux agent or CI runner needs PyPI or a mirror).
Without it, build one on any computer with internet: `AP_ALLOW_INTERNET=1 python scripts/build_offline_bundle.py`
(Windows, Python 3.11–3.14, plus that computer; `--platform` and `--python` choose others; `--dry-run` lists every
address it downloads from, for the firewall). The same command makes the offline install zip for the pilot laptop
(step 2 of section 8; `docs/PILOT.md`).

Then prove the copy works before changing anything (section 3): the default test tier passes,
`python scripts/pilot_check.py` says 10 of 10 sample invoices read right, `python -m ap_coder serve --port 8501
--no-browser` answers on http://127.0.0.1:8501/healthz. `python -m ap_coder doctor` then shows six warnings that are
expected on a fresh copy (the OCR and table-finder models not copied yet, LM Studio not running (two), and the
sample setup); its "data folder" line names the folder it uses (see section 5). The OCR models come with the offline
bundle's `models/` folder (`python scripts/fetch_models.py` copies them from there), or, with internet,
`AP_ALLOW_INTERNET=1 python scripts/fetch_models.py` (add `--table-finder` for the table model).

---

## 2. What AP Coder is

An accounts-payable app for Canadian invoices that runs **offline, on one laptop** (or on one company server for a
team). For each vendor invoice (PDF, scan, photo or saved email) it:

1. **reads** it on this computer: the PDF's text or local OCR, a rule reader, and a small vision model (OvisOCR2 in
   LM Studio) as a second reader; fields are cross-checked and each gets a confidence;
2. **codes** each line to a GL account (and cost center): first from what AP approved before for that vendor, then
   fixed rules and the vendor master's default, then a local chat model in LM Studio for lines nothing was learned
   for;
3. **checks** it: GST/HST/PST/QST by province and place of supply, the maths, duplicates (in AP Coder and in the
   ERP's register), the vendor master, PO matching, bank-detail changes, approval limits;
4. waits for a person on the **review screen** (the invoice beside what was read; correct, split a line, approve);
   **approving teaches it** that vendor's coding, so the next invoice arrives coded;
5. **exports** approved invoices in batches: Excel/CSV, a custom layout, or JD Edwards E1 batch vouchers
   (F0411Z1/F0911Z1), and, built but off, posts them through JD Edwards' AIS API.

Nothing connects to the internet at run time. It is a FastAPI web app on 127.0.0.1 (a server mode adds logins,
roles and HTTPS). Data lives in one SQLite database in a data folder outside the code. It is at pilot stage: built
and tested on synthetic and public sample invoices, not yet on the company's invoices or systems.

Status at the handoff (`v0.3.0-handoff3`: the laptop release `v0.3.0-pilot3` of 30 October 2026, the same commit,
with these handoff docs; the build stamp `doctor` and `/healthz` report is that commit's). Each pilot release was
checked by CI on Windows with the real LM Studio before it was tagged (`.github/workflows/release.yml`, D-129, D-130):

| Part | State |
|---|---|
| Laptop pilot | Ready. A demo run sheet (`docs/DEMO_RUN_SHEET.md`) and an offline install zip for Windows |
| Company server | Built, security-reviewed, tested (HTTPS, logins, four roles, Windows service); not installed anywhere |
| JD Edwards E1 | Batch-voucher export built and tested; AIS API built against a stand-in; never run against a real E1 |
| Finance Hub | Feed to Microsoft Fabric built (off by default); not tried against a real workspace |
| Mailbox | Saved emails dropped in a folder; no direct mailbox connection by design |

---

## 3. Run and test

```bash
python -m ap_coder serve          # the dashboard on http://127.0.0.1:8501 or the next free port (--port N, --no-browser)
python -m ap_coder doctor         # what is set up and what is missing
python scripts/pilot_check.py     # reads the 10 sample invoices, compares with samples/ground_truth (no AI needed)
python scripts/check.py           # after a change: ruff on the changed files, then their tests (a minute or two)
python -m pytest -q               # the default test tier: all but the slow tests (about 4,000, 6 minutes)
python -m pytest -q -m ""         # everything, browser tests included (about 15 minutes; needs Playwright + Chromium)
ruff check ap_coder tests scripts && ruff format --check ap_coder tests scripts
```

- Tests run **serially** (no xdist). They never need the internet, LM Studio or Azure: stand-ins are in `tests/`
  (`fake_ais.py` for JD Edwards' AIS, `offline_guard.py`) and `scripts/ci/fake_lm_studio.py` (LM Studio).
- Test folders mirror the code: `tests/<core|coding|capture|ai|erp|intake|ops|web|bench>/`, plus `launcher/`
  (scripts/), `offline/` and `meta/` (CI, docs and code rules). Fixtures: `tests/conftest.py`; helpers
  `tests/webclient.py`, `tests/pdfs.py`, `tests/browser.py`.
- `tests/ops/test_demo_day.py` is the whole demo, import to Excel export: a good first test to read.
- Refactors are proven to change nothing with `python scripts/same_behaviour.py` (it compares probes on
  `origin/main` and your checkout; point `origin` at the company's repository first).
- Accuracy benchmarks: `python -m ap_coder.bench run|learn|lines|speed|coding|triage [--quick]`.

**CI** is mainly one GitHub Actions file, `.github/workflows/ci.yml` (on push to main and on pull requests): the
tests on Linux (Python 3.11–3.14) and Windows (3.11–3.14, the slow ones in 3 shards), lint and `pip-audit`, browser
tests (Chromium), the installer and the double-click launcher on Windows, GitHub's ZIP on Windows, the offline
bundle on Windows and macOS with the internet cut off (installed with an LM Studio stand-in, then the demo story),
updates from three older versions on Linux, macOS and Windows. A second file, `.github/workflows/release.yml`, runs
only when it or `.github/release-tags.txt` changes: the release's offline zip on Windows with the real LM Studio
(models on the CPU: 60 to 90 minutes, once per pilot release: a later run reuses a passed check, D-130), then the
release tags (D-129). On another CI system (Azure DevOps, GitLab...), port at
least: the Linux and Windows tests, lint, and the Windows offline-bundle job (the closest thing to the pilot
tester's laptop). Three kinds of job need the repository's history: "Update from ..." (`git archive` of three old commits,
`scripts/ci/old_versions.py`), "The pilot tester's Mac ... updated from PR 8" (fetch depth 0) and "GitHub's ZIP on
Windows" (it makes its own commit and archives it: it needs no history); with route B, drop the first two. CI needs
Linux and Windows runners (macOS for the Mac jobs) and these hosts: `pypi.org` and `files.pythonhosted.org`
(packages, `pip-audit`'s advisories), Playwright's Chromium download (its CDN; for the browser tests:
`pip install playwright` then `python -m playwright install --with-deps chromium`, not in the dev extra), and the OCR
models' site (`www.modelscope.cn`; `build_offline_bundle.py --dry-run` lists them all). `release.yml` also needs
`lmstudio.ai` and `huggingface.co` (LM Studio and its models), the `gh` command, GitHub's artifacts API and a token
that may push tags and create releases. **Before the first push of the whole history, switch Actions off (or delete
`release.yml`)**: the first push runs every workflow, and `release.yml` would spend 60 to 90 minutes checking an old
release on Windows and create GitHub releases for its tags. Switch `ci.yml` on first; `release.yml` only once the
company cuts its own releases. The default test tier skips 6 tests on a fresh copy, as expected: the Azure extra (2),
pyarrow (2), the table-finder model (1) and one Windows-only test.

---

## 4. How the code is organised

About 78,000 lines of Python in `ap_coder/` (plus 13,700 of HTML/JS/CSS), 7,800 in `scripts/`, 68,000 in `tests/`
(237 test files, about 3,500 test functions before parametrisation). `CLAUDE.md` has the map; in short:

| Package | Lines | What |
|---|---|---|
| `ap_coder/core` | 10,900 | The database (`core/store/`, one part per subject; schema in `_base.py`), settings, paths, imports |
| `ap_coder/capture` | 11,800 | Reading a document: layout, the rule reader (`capture/reader/`), normalising amounts and dates, supplier learning |
| `ap_coder/ai` | 9,400 | One invoice start to end (`ai/pipeline.py`), LM Studio (`local_llm.py`), the page reader, the AI reviewer, the optional Azure check |
| `ap_coder/coding` | 5,900 | Checks: tax (`tax.py`, `taxmath.py`, `line_tax.py`, `taxreturn.py`), vendors, POs, terms, rules, banks, the line grid's edits (`review.py`: splitting) |
| `ap_coder/erp` | 9,400 | Exports (`exports.py`), JD Edwards (`jde.py`, `jde_ais.py`, `jde_sync.py`, `jde_post.py`, `jde_worksheet.py`), reports, the Finance Hub (`erp/hub/`) |
| `ap_coder/intake` | 1,900 | Emails (.eml/.msg), triage, pictures to PDF, the "needs a person" inbox |
| `ap_coder/ops` | 6,200 | The command line (`python -m ap_coder ...`), doctor, Help's check catalogue, server service and update, users |
| `ap_coder/web` | 15,900 | The dashboard: one module a page in `web/pages/`, templates, static JS/CSS; roles in `web/roles.py` |
| `ap_coder/bench` | 6,900 | The accuracy benchmarks and their invoice generators |
| `scripts/` | 7,800 | The launcher (`launch.py`), installer, updater, offline bundle builder, CI helpers |

Starting points: `ai/pipeline.py` (`finalise_coding`), `core/schema.py`, `web/pages/invoice.py` (the review screen),
`coding/validation.py` (`validate_coding` gathers every check), `ops/help.py` (every check code's plain-English help).

---

## 5. What is real and what is sample data

- **In the repository (synthetic, safe to share):** `data/` (sample chart of accounts, cost centers, tax GL
  mapping, Canadian tax rates, vendor master, POs, bank accounts, coding policy) and `samples/` (10 synthetic invoices
  addressed to "Fabrikam Canada Ltd." with their answers in `samples/ground_truth/`, and `samples/demo_day/`).
  The made-up email domain `pcl-made-up.example` (in `bench/emails.py` and one test) is not a real address.
- **Never in the repository:** the company's data. It lives in the **data folder** (`core/paths.py` `private_dir`):
  `AP_PRIVATE_DIR` if set, else the folder the launcher or installer recorded in `~/.ap_coder/settings.json`
  (`~/APCoder` on a pilot laptop), else `private/` in the checkout (git-ignored): a developer checkout installed with
  pip uses `private/` only when no install was ever made on that computer. **On a computer where the launcher or
  installer ran (the pilot laptop), a checkout uses that install's data folder, which may hold the company's real
  invoices**: set `AP_PRIVATE_DIR=<checkout>/private` there before `serve`, `doctor`, `demo` or `process`, and check
  the folder `doctor`'s "data folder" line names. It holds `ap_coder.db`, `invoices/`, `emails/`, `backups/`, `logs/`,
  and a `.env` with any secrets (AIS password, Azure keys, the Finance Hub's client secret). `.gitignore` keeps
  `private/`, `.env*` and `*.xlsx` out of git. Keep it so: real invoices, vendor lists, GL charts or JDE values
  committed to git are a leak.
- **What real data replaces:** the sample GL accounts, tax mapping, vendor master, POs and bank accounts are imported
  from the company's CSV or Excel files in the dashboard (section 6), or synced from JD Edwards (6.2). A company copy
  of `canada_tax_rates.csv` or `jde_orchestrations.json` in the data folder overrides the bundled one.

---

## 6. The connection points

Each says what it does now, how it's switched on, what it reads, what the company must supply, and where it lives.
Imports match column headers loosely (`core/headers.py` `match_columns`: case, accents and punctuation ignored, many
aliases each). Every import and setting page is Admin-only in server mode (`web/roles.py`).

### 6.1 JD Edwards E1 vouchers through batch files

- **What:** `erp/jde.py` builds a ZIP with `F0411Z1.csv` (pay items), `F0911Z1.csv` (G/L lines), `F0411Z1T.csv` (PO
  match lines, only for PO-matched vouchers), a README and `DRY_RUN.txt`, for E1's Voucher Batch Processor
  (R04110ZA). Keys: VLEDUS = the user setting, VLEDBT = prefix + batch number, VLEDTN = `APC` + invoice id. Julian
  dates; implied decimals by default; amount modes `gl` (default: AP Coder's own distribution including tax),
  `gross` and `tax`. Findings (JDE_NO_AN8, JDE_NO_ACCOUNT, JDE_UNBALANCED, JDE_TAX_AREA ...) show on the review screen
  before approval.
- **Switched on:** hidden on a laptop until Settings → About → *Show JD Edwards settings*; shown from the start in
  server mode. Then Settings → JD Edwards E1, and the Exports page's format *JD Edwards E1*. `GET
  /exports/jde/dry-run.txt` shows what would be sent without making anything.
- **Account mapping:** GL code (+ cost center) → BU.Object.Subsidiary (+ subledger), imported from CSV/Excel
  (headers: gl_code, cost_center, bu, obj, sub, sbl, sblt); else a default rule (BU = the line's cost center or a
  default BU, object = the GL code).
- **The company supplies** (the **JDE worksheet** asks all of it): Settings → JD Edwards E1 → **Download the
  worksheet** gives one Excel file with 37 questions, a yellow answer column, and sheets for tax areas, the account
  mapping (every GL account listed) and each supplier's address number (AN8). The JDE team fills it in; **Import the
  JDE team's answers** on the same page applies them and lists any it could not use. The questions: company, BUs, document types
  (voucher, credit note), the P0400047 version and whether it recalculates tax, the duplicate-invoice edit, batch
  prefix and user, amount mode, decimals, line numbering, account format, how the AAIs post each tax, the self-assessed
  and exempt tax codes, G/L date, due date, terms code, pay status, the PO path, currencies.
  **Not in the worksheet:** payment terms → VLPTC codes (Settings → JD Edwards E1 → terms table), VLGLBA per bank
  account, and everything for the API (6.2).
- **Not confirmed with a real E1** (`jde.UNCONFIRMED`): the PO extra columns and the whole F0411Z1T layout (not a
  standard Oracle table), VNEDLN numbering, whether line numbers load as 1 or 1000, the tax area names, blank VLCRR,
  VLGLBA. The worksheet also asks (`info:` questions) whether code E with no tax area posts lines as sent and whether
  BUs are right-justified. `docs/JDE_E1.md` has the open questions.
- **Prove it:** the shadow pilot scores AP Coder's vouchers against what AP posted in E1:
  `python -m ap_coder pilot-score --vouchers F0411.xlsx [--distribution F0911.xlsx]` (writes `pilot_score/`).
- **Code and tests:** `erp/jde.py`, `erp/jde_worksheet.py`, `web/pages/settings_jde.py`, `web/pages/exports.py`;
  `tests/erp/test_jde*.py`, `tests/ops/test_laptop_walk_jde.py`, `tests/ops/test_pilot_score_jde.py`. Guide:
  `docs/JDE_E1.md`.

### 6.2 JD Edwards AIS API (read sync and voucher posting)

- **What:** `erp/jde_ais.py` (the client), `jde_sync.py` (step 1: read suppliers, accounts, business units, open
  POs, past vouchers, nightly and on demand), `jde_post.py` (step 2: post vouchers, dry run first, one at a time,
  idempotent by key `APC-<installation>-<invoice>-<batch>`, retried 5/15/60 minutes then hourly, looked up before any
  resend). Token or Basic auth over HTTPS to **one saved internal address** only (D-104: private, loopback or
  link-local addresses; no proxy, no redirects).
- **Switched on:** off by default. Settings → JD Edwards E1 → API connection: two switches, read (step 1) and post
  (step 2). Command line: `python -m ap_coder jde test|sync|status [--retry]`.
- **The company supplies:** **seven orchestrations the JDE team builds** (specified in `docs/JDE_ORCHESTRATIONS.md`):
  `APC_Suppliers_Read`, `APC_Accounts_Read`, `APC_BusinessUnits_Read`, `APC_OpenPOs_Read`, `APC_Vouchers_Read`,
  `APC_Voucher_Create` (PROOF and FINAL modes), `APC_Voucher_Status`; the AIS address, environment (a test one first),
  a service user and role with the table and program security listed there, and the 13 "To confirm" points in that
  file. The password goes in the data folder's `.env` (`JDE_AIS_PASSWORD`), never in git.
- **Not done:** never run against a real AIS; the orchestrations don't exist yet; `ChangedSince` is always blank
  (full read each time).
- **Code and tests:** the three modules, `web/pages/settings_jde.py`, `web/pages/exports.py` (the `jde-api` routes);
  `tests/erp/test_jde_ais*.py`, `test_jde_sync*.py`, `test_jde_post*.py`, `test_jde_api_web.py`; stand-in
  `tests/fake_ais.py`.

### 6.3 Vendor master

- **What:** the vendor list on the review screen and its checks: VENDOR_NOT_IN_MASTER, VENDOR_ON_HOLD, tax numbers
  changed, VENDOR_BANK_CHANGED (the printed bank account differs from this vendor's approved invoices: possible fraud),
  duplicates in the ERP.
- **Import:** Vendors page → upload CSV/Excel (Admin). Only the name is required; columns: vendor id (JDE's AN8),
  name, GST/HST number, QST, PST, province, terms, status/hold, default GL. Or synced from JD Edwards (6.2).
- **The company supplies:** E1's supplier export (AN8, alpha name, tax IDs, terms, hold code, province).
- **Code and tests:** `core/store/vendors.py`, `coding/vendors.py`; `tests/core/test_vendor_master.py`,
  `tests/coding/test_vendors.py`.

### 6.4 GL accounts, cost centers, coding policy and rules

- **What:** the codes the AI may use (`AP_CONSTRAIN_CODES`), a plain-English coding policy added to the model's
  prompt, fixed rules (vendor and/or words → GL and cost center), and an optional import of past ERP coding to teach
  the memory before the first approval.
- **Import:** GL accounts & tax page → upload CSV/Excel (code, description, optional category and a *meals* column
  for the 50% tax credit accounts); cost centers the same way. The pilot runs without cost centers (D-53): with them,
  each line needs one. Learning & accuracy → *Teach from past coding* (vendor, description, gl_code, cost_center).
- **The company supplies:** the posting accounts AP uses (JDE object/subsidiary), the BUs as cost centers if lines
  are coded to them, which accounts are meals and entertainment, the coding policy's rules.
- **A line can be split** across several GL accounts (cost centers) by percentage or amount (D-126). **Job cost coding
  (job, cost code, cost type) is not built**; if AP codes to jobs, design it as a new line dimension mapped to E1's
  account (BU.Object.Subsidiary or subledger), learned like the GL account.
- **Code and tests:** `core/store/accounts.py`, `core/reference_data.py`, `coding/rules.py`;
  `tests/core/test_reference_data.py`, `tests/coding/test_rules.py`, `tests/coding/test_split.py`.

### 6.5 Tax setup

- **What:** GST/HST/PST/QST by province and place of supply (ship-to, else the cost center's or "your" province,
  else the supplier's), each tax `recoverable` (to its GL), `expense_to_line` or `expense_separate`; meals at 50%
  (D-90); self-assessment and input-tax-credit reports (Sales tax page).
- **Files:** `tax_gl_mapping.csv` (`tax_type,treatment,gl_code`), `canada_tax_rates.csv` (dated rows; a copy in the
  data folder overrides the bundled one). In the dashboard: GL accounts & tax → How each sales tax posts.
- **The company supplies:** the GST/HST and QST receivable accounts, how PST is treated, and in E1 the tax areas
  and AAIs (the worksheet's *Tax areas* sheet).
- **Code and tests:** `coding/tax.py`, `taxmath.py`, `line_tax.py`, `taxreturn.py`; `tests/coding/test_tax*.py`,
  `tests/erp/test_jde_tax_lines.py`. Guide: `docs/SALES_TAX.md`.

### 6.6 Purchase orders and the ERP invoice register

- **POs:** Purchase orders page → import (po_number and description required; vendor, line, quantity, unit price,
  amount, received quantity, GL, cost center, status). 2-way matching, 3-way when receipts are known (tolerances in
  `coding/po.py`). Or synced from E1 (`APC_OpenPOs_Read`).
- **ERP invoice register** (what is already in E1, for duplicates): Exports → *Invoices already in the ERP* → import
  (vendor, invoice number, total required). Or synced (`APC_Vouchers_Read`).
- **Not confirmed:** E1's own receipt matching (P4314, F0411Z1T) for PO-matched vouchers.

### 6.7 Bank accounts ("Pay from")

- Which company bank account pays an invoice: GL accounts & tax → Bank accounts → import (code, name, currency,
  institution, transit, account number, cash GL, default per currency). In the exports; in JD Edwards as VLGLBA only
  when mapped. The company supplies the codes, cash GLs and JDE short account IDs. `coding/banks.py`.

### 6.8 Mailbox intake

- **AP Coder never connects to a mailbox** (by design: no mailbox credentials in it). A company tool saves each
  email whole (.eml or .msg) into the data folder's `invoices/`; AP Coder sorts them (invoice, credit note,
  statement, payment reminder, bank-change request (flagged), not for AP), reads the invoices and sets the rest aside
  for a person. Picked up by *Import invoices → Process* or `python -m ap_coder watch [--every 60]` (a scheduled
  task). The recommended flow (Power Automate on the shared mailbox → a synced library → the folder) is in
  `docs/MAILBOX_SETUP.md`; it has not been tried here.
- **Code and tests:** `intake/`; `tests/intake/`.

### 6.9 Finance Hub feed (Microsoft Fabric)

- **What:** six tables (invoices, invoice_lines, approvals, activity, accuracy, workflow_runs; dictionary in
  `docs/FINANCE_HUB.md`) written incrementally. Option A (default): files in a folder laid out as a Fabric Open
  Mirroring landing zone (Parquet if `pyarrow` is installed, else CSV), for Fabric to pull. Option B: AP Coder pushes
  to OneLake (needs `AP_ALLOW_INTERNET=1`, an Entra app's client credentials, the Admin's opt-in). No PDFs, bank
  details or sign-in events leave.
- **Switched on:** off by default. Settings → Finance hub (Admin); `python -m ap_coder hub export [--full]
  [--dry-run]`. **The company supplies:** the share path (with write access for the service account) or the OneLake
  landing-zone URL and app registration; AP Coder's URL for links; the schedule.
- **Not done:** never tried against a real Fabric workspace; `pyarrow` is not a dependency (add it if Parquet is
  wanted); times are written without a time zone.

### 6.10 Server mode (one AP Coder for the team)

- **What:** `AP_SERVER_MODE=1`: logins (AP Coder's own accounts; no SSO yet), four roles (Read-only, Clerk,
  Approver, Admin, enforced per route in `web/roles.py`), HTTPS with the company's certificate, an allowed-hosts list,
  a Windows scheduled-task service (`python -m ap_coder service install ...`), `server-update <bundle.zip> --sha256`
  with rollback. All in `docs/SERVER.md`, step by step for IT.
- **IT supplies:** Windows Server 2019–2025, 8–16 cores, 32–64 GB RAM, an NVIDIA GPU with 12 GB+ for LM Studio,
  60–200 GB SSD, Python 3.12, a service account, a DNS name and a certificate for it, a firewall rule for the port,
  LM Studio installed headless with the models.
- **Not done:** never installed anywhere; not reviewed by the company's security team; no Entra ID sign-in.

### 6.11 LM Studio and the models

- **Chat model** (Gemma 4 12B, else Qwen 3.5 9B): GL suggestions for lines nothing was learned for, a header read
  from the page images, the AI reviewer. **Page reader:** OvisOCR2 (bartowski's GGUF, Q8_0), reads every page in the
  background after passing a self-test. Both through LM Studio's OpenAI-compatible server at
  `AP_LLM_BASE_URL` (default `http://127.0.0.1:1234/v1`); refused if it isn't on this computer or the office
  network. Chosen as `ai/local_llm.py` `pick_model` says. Setup: `docs/LM_STUDIO.md`.
- **Optional Azure Document Intelligence check:** off by default; contacts only its configured endpoint.

### 6.12 Excel/CSV and custom exports

- Excel (sheets Invoices, GL lines, By GL account), CSV (one row per GL line), and a **custom layout** (any columns,
  date format, delimiter) for an ERP import that isn't JD Edwards. `erp/exports.py`, `erp/export_layout.py`.

---

## 7. The company's information to gather

Nothing below is in the repository, and nothing of it may be committed. Collect it inside the company, keep it in
the data folder or the company's own document store, and import it through the dashboard. The people are named
nowhere in the code: ask whoever handed this over to you for the pilot tester, the AP lead who decides, the JDE /
CNC contact, IT's contact for the laptop and the server, and who owns the company's repository and CI.

| What | From | Goes to |
|---|---|---|
| Chart of accounts AP posts to; cost centers / BUs | Finance / JDE team | GL accounts & tax → import (6.4) |
| Tax receivable accounts; PST treatment | Tax / Finance | GL accounts & tax → How each sales tax posts (6.5) |
| Vendor master (AN8, name, tax IDs, terms, hold) | E1 supplier export | Vendors → import (6.3) |
| Open POs with receipts (if PO-matched) | E1 / Procurement | Purchase orders → import (6.6) |
| Invoices already in E1 (the last 12 to 18 months) | E1 voucher export | Exports → Invoices already in the ERP (6.6) |
| Last year's posted AP lines (vendor, description, GL, cost center) | JDE team / AP | Learning & accuracy → Teach from past coding |
| Exchange rates (for invoices in other currencies; the approval limit uses them) | Treasury / AP lead | Settings → Review |
| A second backup folder (a network share) | IT | Settings → Data → *Also copy each backup to* |
| Bank accounts that pay AP | Treasury | GL accounts & tax → Bank accounts (6.7) |
| The JDE worksheet's answers (37 questions, tax areas, account mapping, AN8s) | JDE team | Settings → JD Edwards E1 → Import the answers (6.1) |
| Payment terms → E1 terms codes | JDE team | Settings → JD Edwards E1 → terms table |
| AIS address, environment, service user and role; the seven orchestrations | JDE / CNC team | Settings → API connection (6.2) |
| How emails reach the invoices folder | IT / M365 admin | `docs/MAILBOX_SETUP.md` (6.8) |
| Finance Hub landing zone or share | Data team | Settings → Finance hub (6.9) |
| Server, certificate, service account, DNS name | IT | `docs/SERVER.md` (6.10) |
| Coding policy; meals accounts; approval limit | AP lead | GL accounts & tax → Coding policy; Settings → Review |
| A sample of real invoices with what AP coded | AP | The shadow pilot (`docs/PILOT_PLAN.md`) and `pilot-score` |
| E1's vouchers for the pilot period (F0411, and F0911 for the distribution; the columns are in `docs/JDE_E1.md`) | JDE team | `python -m ap_coder pilot-score` (step 2's "done when") |
| LM Studio and the models (a chat model: Gemma 4 12B or Qwen 3.5 9B; OvisOCR2's GGUF and vision file) | IT, from LM Studio's model catalogue | `docs/LM_STUDIO.md`, `docs/PILOT.md` "What you need" |

---

## 8. The recommended order of work

Each step ends with something a person can check. Don't start the next while the last one's tests are red.

1. **Land the code.** Import it (section 1), run the default test tier and `pilot_check.py`, get CI running in the
   company's system (section 3: the runners and hosts it needs). Replace the README's CI badge and links, which point
   at the original GitHub repository. *Done when* the tests are green on the company's CI and the dashboard opens.
2. **The laptop pilot on the company's master data, no connections yet.** Import the GL accounts, tax accounts, vendor
   master, bank accounts (section 7) on the pilot tester's laptop (the offline install zip; `docs/PILOT.md`). Also
   import the vendors' AN8s and the JDE account mapping sheet (or set a Default BU) from the worksheet: `pilot-score`
   matches suppliers by AN8 and compares accounts as BU.Object.Subsidiary; nothing is sent to E1. Run the four-week
   shadow pilot of `docs/PILOT_PLAN.md` without its ERP and mailbox items (week 2's "Export approved invoices to the
   ERP" makes a file that is not loaded; week 3's mailbox watcher and JDE worksheet are steps 3 and 4 here): AP keeps
   working as today, AP Coder reads the same invoices, `pilot-score` compares. *Done when* the pilot score is written
   up and the AP lead decides whether to go on to live use (the four weeks and the decision are in
   `docs/PILOT_PLAN.md`).
3. **JD Edwards through batch files.** Send the JDE worksheet; import the answers; make dry runs; load a batch into
   E1's **test** environment through R04110ZA; compare with what AP would have keyed. Resolve every point of
   `jde.UNCONFIRMED`. *Done when* a test batch posts in E1 exactly as AP would have.
4. **Mailbox intake.** IT builds the flow of `docs/MAILBOX_SETUP.md`; schedule `python -m ap_coder watch`. *Done
   when* a week of AP's mailbox lands in the review queue with nothing lost.
5. **The server.** IT provisions it (`docs/SERVER.md`); install the service, the first Admin, the users; LM Studio
   headless on the GPU; the security team's review. *Done when* the AP team works from it and the laptop is retired.
6. **The JD Edwards API.** The JDE team builds the seven orchestrations in a test environment; step 1 (read) first
   (it replaces the CSV imports of 2), then step 2 (post) with dry runs, then production. *Done when* vouchers post
   through the API with the batch files kept as the fallback.
7. **The Finance Hub.** Point the feed at the landing zone; the data team builds the reports. *Done when* the hub
   shows AP Coder's invoices and accuracy.

Likely company-specific work to weigh along the way: job cost coding (6.4), single sign-on with Entra ID (6.10),
the company's own approval workflow, and any invoice layouts the shadow pilot shows it reads badly (each one: a test
first, then the fix, see section 9).

---

## 9. House rules (they apply to you too)

From `CLAUDE.md`, with why:

- **Offline at run time.** Nothing connects to the internet unless `AP_ALLOW_INTERNET=1` (developers, the bundle
  builder). Internal systems (JD Edwards' AIS) only at their saved address on the company network (D-104). A new
  connection gets the same treatment: off by default, an Admin switches it on, one named address, a test in
  `tests/offline/` that it reaches nothing else.
- **No company data in the repository.** It lives in the data folder; keep `.gitignore` as it is.
- **A bug fix starts with a failing test**, then the fix, then one line at the end of `docs/BUGS.md` (format at its
  top; the next number is after the last `B-` line). A suspicion that didn't reproduce is an `N-` line.
- **Significant choices go in `docs/decisions.md`**, one entry each (next `D-` number, lines of 120 characters at
  most), and are named in the pull request. Never renumber; a reversed decision is marked "replaced by D-NN". Read
  the decisions before changing something that looks odd: most odd things have a reason there (full texts of D-01 to
  D-124 in `docs/archive/decisions-2026-10.md`).
- **Plain English in what people read** (the dashboard, guides): "pilot tester" for the person running the pilot.
- **Every check code needs its help** in `ops/help.py` (`tests/ops/test_help.py` enforces it); **every web route
  needs a role rule** in `web/roles.py` (`tests/web/test_roles.py` fails until it has one).
- **Tests stay green and are never skipped to get green.** Run `python scripts/check.py` after each change; CI runs
  everything.
- One file to open per computer at the app folder's top (`APProcessor.bat`, `APProcessor.command`); a new top-level
  file must be added to `scripts/upgrade.py` `BUNDLE_TOP` (a test checks).

---

## 10. Known limits and open questions

`docs/KNOWN_LIMITS.md` (ten points) and, for JD Edwards, the open questions in `docs/JDE_E1.md` and the "To confirm"
list in `docs/JDE_ORCHESTRATIONS.md`. In short: one laptop until the server is set up; minutes a page without a
graphics card; sample data until the company's is imported; JD Edwards never tried against a real E1; no direct
mailbox connection; accuracy measured on samples only; no job cost coding; Python installed by IT; no security
review yet.

---

## 11. Glossary

| Term | Meaning |
|---|---|
| AN8 | JD Edwards' address number: the supplier's key in E1 |
| AIS | JD Edwards' Application Interface Services: its REST API, which runs orchestrations |
| BU.Object.Subsidiary | An E1 account: business unit, object account, subsidiary |
| R04110ZA | E1's Voucher Batch Processor, which loads F0411Z1/F0911Z1 into vouchers |
| ITC / ITR | Input tax credit (GST/HST) / input tax refund (QST): tax AP gets back |
| Place of supply | The province whose sales tax applies (where the goods or services are delivered) |
| OvisOCR2 | The small vision model that reads every page, in LM Studio |
| Touchless | An invoice approved without a person, for a vendor with a track record (off by default) |
| Shadow pilot | AP works as today while AP Coder reads the same invoices; the two are compared |
| Data folder | Where the company's data lives, outside the code (`~/APCoder` on a laptop, `private/` in a checkout) |
| CloseDesk | The original builder's Outlook assistant, where the page reader idea and `ai/figures.py` came from; not part of AP Coder, nothing to install |
| Pilot tester | The person running the pilot on the laptop |
