# Updating from an older version

Every merge into `main` was a version someone may have installed. All of them are `0.1.0` in their
`pyproject.toml`; then came `0.2.0`, the web app (`python -m ap_coder serve`) instead of the Streamlit
dashboard, and `0.3.0`, the version of the pilot drop. This page says, for each older version, how its README had it installed and started, what the new
version finds on such a computer, and what the update does with it. Each line was checked for real on Linux (old
version installed as its README said, used, left running; the new offline bundle unzipped into a new folder and
over the old one, and double-clicked with no internet): see *How this is checked* at the end.

The update itself is the same for everyone (the full steps are in [PILOT.md](PILOT.md#updating-ap-coder)): unzip
the new offline bundle anywhere (a new folder, or over the old one), then double-click `APProcessor.bat` (Mac,
Linux: `APProcessor.command`). Leaving the old AP Coder open is fine, and even helps: the new version finds an old
one that runs, stops it and cleans its folder up.

The new version has one file to open per computer: `APProcessor.bat` (Windows) or `APProcessor.command` (Mac,
Linux). Older folders also had `install.bat`, `install.sh`, `start.bat`, `start.sh` and `terminal.bat`. When the new
bundle is unzipped over such a folder, those files are removed on its first start. Nothing to do by hand. The
command prompt for AP Coder's commands is now `scripts\terminal.bat`, and the installer's questions are
`APProcessor.bat --installer`.

## Builds of one version

Every `0.2.0` looked the same to the launcher: GitHub's ZIP carried nothing but the version, so a newer ZIP
unzipped over an older one said no *Updated* line, showed the older one's *Self-check OK at setup* and ran no new
self-check (docs/BUGS.md B-542). From `0.3.0` every copy says which build it is:

- **GitHub's ZIP** (and `git archive`): `ap_coder/BUILD` holds `$Format:%h %cI$`, which git fills in with the commit
  and its time (`export-subst` in `.gitattributes`).
- **The offline bundle**: the builder writes the commit it was built from into `ap_coder/BUILD`; the bundle's build
  time stays in `bundle_manifest.json`.
- **A git checkout**: its HEAD. **A folder with neither**: a hash of its code files (not shown as a build).

The launcher's fingerprint of a folder is its `pyproject.toml` and that build, so a newer build of the same version
is an update: *Updated AP Coder from 0.3.0 (build 1a2b3c4, 2026-10-20) to 0.3.0 (build 5e6f7a8, 2026-10-21)*, then
the self-check again. A dashboard still running from before (this folder unzipped over while it ran) says its
build on `/healthz` and is stopped, not opened; a window of 0.3.0 or later then says *A newer AP Coder took over;
you can close this window.* (a 0.2.0 window, the one this update replaces, just ends). An older build unzipped over a
newer one says *Replaced AP Coder X with an older build, Y*. Only a folder certainly older than this one (or another
copy of this very build) is ever cleaned up: builds are ordered by their commit's time in UTC, and a folder of the same
version whose build can't be ordered (a bundle from before the stamp, a copy made by hand) is left alone. *Settings → About*, `python -m ap_coder doctor` and *Help → Report a problem* show the build.

## The older versions

| Version (main after) | How its README installed and started it | Data, `.env`, record | Database |
| --- | --- | --- | --- |
| `81f691e` first commit (Oct 6) | `python -m venv .venv`, `pip install -e ".[dev]"`, `cp .env.example .env`; a command line only (`python -m ap_coder process ... -o output`, Azure needed) | `output/` and `.env` (Azure keys) in the code folder; nothing recorded | none |
| `db95511` PR #1 (Oct 6) | `install.sh` / `install.bat` (asks for the data folder, makes a `.venv`, Windows desktop shortcut *AP Coder* to `start.bat`), then `start.sh` / `start.bat`: `python -m ap_coder dashboard` (Streamlit, port 8501 or the next free one). By hand: `pip install -e ".[dev]"` and `python -m ap_coder dashboard` | installer: `~/APCoder` (recorded as `data_dir` in `~/.ap_coder/settings.json`, with `desktop_shortcut`), `.env` there. By hand: `private/` in the code folder, `.env` in the code folder, nothing recorded | schema 2 |
| `b968b74` PR #2 (Oct 8) | as PR #1 (now with demo invoices, a public demo `streamlit_app.py`) | as PR #1 | schema 13 |
| `4366f35` PR #3 (Oct 9) | the double-click: `APProcessor.bat` / `APProcessor.command` (a shell script: `.venv`, `pip install -e ".[ocr]"` from PyPI or the bundle's `wheelhouse/`, `scripts/first_run.py`, then the Streamlit dashboard); `install.*` / `start.*` still there; Python 3.11+ | `~/APCoder` recorded (`private/` only when it already held data), `.env` there; shortcut by `install.bat` | schema 14 |
| `e2a20cf` PR #4 (Oct 9) | the double-click, now through `scripts/launch.py` (reuses `.venv`, readiness summary, self-check, *AP Coder* shortcut to `APProcessor.bat`) | `~/APCoder` recorded, `.env` there; launcher state in `.venv/ap_coder_launcher.json` | schema 14 |
| `a798f79` PR #5 (Oct 10) | as PR #4; offline by default (packages only from the bundle's `wheelhouse/`, OCR models from its `models/`), launch lock `.ap_coder_launch.lock` | as PR #4 | schema 15 |
| `abbc331` PR #6 (Oct 10) | as PR #5 | as PR #4 | schema 15 |
| `0de8afe` PR #7 (Oct 11) | as PR #5 (`.env.example` without the Azure keys: local reading, LM Studio) | as PR #4 | schema 15 |
| `e1cf61a` PR #8 (Oct 11), the last before the web app | as PR #5 (page reader OvisOCR2: `AP_PAGE_READER` in the `.env`) | as PR #4 | schema 15 |

Every one of them also ran by hand from a checkout (`python -m ap_coder dashboard`, data in `private/`): the
`by-hand` rows below cover that.

## What the update does with each

How the new version finds an old folder: its dashboard is running (on 8501 or one of the next ports: it is
stopped first), the Windows desktop shortcut *AP Coder* points to it, the recorded data folder is its `private/`,
or it sits next to the new folder (beside it, as a Mac unzips a second bundle as *APProcessor 2*, or in the folder
beside, as Windows' *Extract All* makes `APProcessor-offline-<version>-<date>\APProcessor`). Unzipped over it, it
is the new folder itself.

| Old version, how it was installed | The new version unzipped elsewhere (new folder, or beside it) | Unzipped over the old folder |
| --- | --- | --- |
| `81f691e`, command line | Found only next to the new folder: its `.env` moves to `~/APCoder/.env`, its `.venv` goes, the folder stays (its `output/` is yours). Anywhere else nothing points to it: copy its `.env` into `~/APCoder` by hand if the Azure check is to use those keys. | Its `.env` moves to `~/APCoder/.env`; `output/` stays; the old modules, tests and samples go; its `.venv` is reused and the new packages are added. |
| `db95511`, `b968b74`, installer | The running old dashboard is stopped; the database in `~/APCoder` is backed up and brought to schema 15; the demo invoices, which pointed into the old folder's `samples/`, point at a copy in `~/APCoder/invoices/` (or the new version's identical sample); after the self-check the old folder (and its `.venv`) is removed. | The old dashboard is stopped; the old version's files the new one does not ship (`ap_coder/dashboard.py`, `streamlit_app.py`, `.streamlit/`, ...) are removed; the `.venv` is reused (made again when its Python is too old); the database is brought up to date. *Updated AP Coder from 0.1.0 to 0.2.0.* |
| `db95511`, `b968b74`, by hand (data in `private/`) | Its `private/` moves to `~/APCoder` before the new version starts, its `.env` too, its invoices point at their files' new place; then the old folder is removed. | `private/` moves to `~/APCoder`, the `.env` too; the rest as above. |
| `4366f35`, double-click or installer | as the installer rows above | as above |
| `e2a20cf` to `e1cf61a`, double-click | as above; the desktop shortcut *AP Coder* (Windows) now opens the new folder | as above |

In every case: the `.env` keeps its values; a setting no longer used (`AP_PAGE_READER`, `AP_PAGE_READER_SCOPE`,
`AP_LLM_PROVIDER`) is commented out with *# no longer used (AP Coder 0.2)*; the database is copied to
`~/APCoder/backups/ap_coder-<date>-before-update-from-schema-<n>.db` before it is changed; invoices, approvals,
the learning memory, vendors and their notes, coding rules, export batches and settings are all kept, and every
invoice still shows its file and its page picture. `~/.ap_coder/settings.json` then names the new folder as the
only install and the current one. The next double-click starts in seconds and sets nothing up again. Nothing
connects to the internet (ONNX Runtime's own usage reports to Microsoft included: AP Coder switches them off).

## What it cannot do

- **An old folder that is not running, not next to the new one and kept no record** (no Windows desktop
  shortcut, data in `~/APCoder`) is not found. The data is found (it is in `~/APCoder`), but that folder and its
  `.venv` (about 1 GB) stay, and starting it would run the old version on the updated data: a version before the
  web app does not know to hand over to the newest. Unzip the new bundle **over** that folder, or next to it, or
  start the new version while the old one is still open, or delete the old folder by hand.
- **Data kept in `private/` of such a folder** (run by hand) stays there for the same reason: the same remedies.
- **A folder watcher or overnight page reader of a version from before October 19, 2026** (`python -m ap_coder
  watch` or `read-pages`, in a window or a scheduled task) is not found: only the versions since leave a note
  saying they run. Close its window, or end its task in Task Scheduler, before this one update; left running, it
  reads new invoices with the old code and fails them once its folder is cleaned up (they then show as failed, with
  **Retry**). Updates after this one stop it by themselves.
- **Demo invoices, unzipped over PR #2 to #5.** A demo invoice's file is the old version's sample PDF in its
  `samples/` folder. Unzipped elsewhere, the update copies each one into `~/APCoder/invoices/` before the old
  folder goes. Unzipped over the old folder, the new bundle has already replaced them by its own samples (the
  same invoices, one detail changed, e.g. Pacific's GST number): those demo invoices then show the new sample.
  Your own invoices are in the data folder, never in `samples/`, and are not touched. (*Remove demo invoices*, on
  the *Import invoices* page, clears them.)
- **Python 3.10.** PR #1 and #2 ran on Python 3.10; the new version needs 3.11 or newer (3.12 is best). A `.venv`
  made with 3.10 is made again with the newer Python when one is installed; with only 3.10 the double-click says
  so (*Python 3.10 is installed, but AP Coder needs Python 3.11 or newer*).
- **Going back to an older version loses the meals ticks.** A version from before the *Meals & entertainment (50% tax
  credit)* tick on GL accounts (D-90) does not know it: its *Replace the list* import or a save of its GL accounts
  grid clears every tick in the database. Back on this version, tick the meals accounts again (GL accounts & tax →
  GL accounts) before approving meals invoices, or their full tax credit is claimed.

## How this is checked

`scripts/ci/old_versions.py` does it for real: `extract` (the version's files, `git archive`), `install` (as its
README said, packages from PyPI), `start` (its dashboard, left running), `seed` (used with its own code: demo or
sample invoices, one invoice added to the data folder and read by its own pipeline, one approved with a GL account
corrected, a vendor note and coding rule, an export batch, a setting, `.env` values), `unzip` (the new offline
bundle), then `check` (old dashboard stopped, every record kept, every invoice's file there with the same bytes,
`/review/<id>/page/1.png` a PNG, the web pages served, the old folder and `.venv` gone, the record of installs).
`scenario` runs all of it in a scratch home with no internet for the new version; on Linux every connection is
traced (`strace`): none may leave the computer.

- `AP_UPGRADE_TESTS=1 AP_UPGRADE_BUNDLE=<the bundle ZIP> python -m pytest -m upgrade` runs every version, both
  ways, plus a version closed before the update (found beside the new folder), two old windows open (8501 and
  8502), and, with `AP_UPGRADE_PYTHON310` naming one, a `.venv` made with Python 3.10
  (`tests/test_update_from_old_versions.py`; Linux and Mac; a few minutes per version).
- CI's *Update from ...* job runs the first version (unzipped over), PR #1 (run by hand, data in `private/`) and
  PR #8 on Windows, macOS and Linux, through `APProcessor.bat` / `APProcessor.command`, with every proxy pointing
  at a closed port.
- CI's *The pilot tester's Mac* job is one real case: a Mac with only Python 3.14, GitHub's ZIP of PR #8 with an
  older Windows bundle's wheelhouse copied in as `.wheelhouse` (`old_versions.py old-wheelhouse`), whose double-click
  stopped on *Missing packages: azure-ai-documentintelligence, ..., streamlit*. The Mac bundle is then unzipped into
  a new folder and over the old one, offline, with LM Studio's three models standing in on port 1234
  (`scripts/ci/fake_lm_studio.py`: Qwen 3.5 9B, OvisOCR2, a Qwen embedding model): it must start, serve every page,
  load the demo invoices, code GL with Qwen 3.5 9B (never OvisOCR2 nor the embedding model), read pages with
  OvisOCR2, open the running one on a second double-click and start again in seconds, with one install on record
  and one desktop launcher. GitHub's ZIP of the new main with the old `.wheelhouse` must stop with the one sentence
  *This package folder (wheelhouse) was built for Windows, Python 3.11/3.12; this computer is a Mac with Python
  3.14. Use the bundle built for Macs (build it with --mac)*, not a list of packages; its installer too
  (`./APProcessor.command --installer`). And the installer, run over the old folder and in a new folder beside it
  (no answers given, then `--yes`), three times each: the first brings everything up to date (PR #8's leftover
  files removed, its `install.sh` and `start.sh` too, its `.venv` cleaned up, the self-check run), the next two
  say *Nothing to update* in seconds; the double-click then starts without setting anything up.

Last run on Linux (Python 3.12, the 0.2.0 offline bundle built for it, 2026-10-12): all 24 cases of the first
table (each version, each way it was installed, unzipped into a new folder and over it) passed, and so did the
closed-before-the-update, two-windows and Python 3.10 ones. The new version answered 26 to 83 s after the first
double-click (less over the old folder: the `.venv` is reused), 2 to 4 s after the next one, and no connection
left the computer. Getting there took four fixes: ONNX Runtime's usage reports to Microsoft (switched off), AP
Coder from before the launcher (PR #1 to #3) not taken for an install (its data in `private/` left behind, its
folder and `.venv` kept), old dashboards on the next ports (8502...) left running, and old folders closed before the
update never found.
