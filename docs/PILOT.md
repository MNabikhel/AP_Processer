# Pilot guide: AP Coder on one laptop, offline

A short guide for running the pilot on a Windows laptop (a Mac works the same way). For the four-week
plan that turns the pilot into a decision, see [PILOT_PLAN.md](PILOT_PLAN.md).

## What you need

- **Windows 10 or 11** (64-bit), or a Mac with macOS 12 or newer.
- **Python 3.11 or newer** (3.12 recommended) from [python.org](https://www.python.org/downloads/). On the
  first screen of the Windows installer tick **Add python.exe to PATH**. Or, in a terminal:
  `winget install Python.Python.3.12`.
- About **2 GB** free disk space.
- **Optional: LM Studio** with a local model, for the AI coding step on this laptop (see the LM Studio
  section of the [README](../README.md)). AP Coder starts and works without it: every invoice is still
  read on the laptop (PDF text, or local OCR for scans) and each line is coded from what AP approved
  before for that vendor, the vendor master's default account and your fixed coding rules. A line with
  nothing to learn from is left for you to code. With a model running, it also proposes accounts for
  vendors and lines it has not seen yet.
- **No Azure needed.** Azure Document Intelligence and Azure OpenAI stay optional; leave Settings → Azure
  empty for the pilot.

## Install

There are two ways; both end with the same folder and the same double-click.

**A. Online once (simplest).** Download the AP Coder ZIP, unzip it to a folder that is **not** synced by
OneDrive (e.g. `C:\APCoder\app`), and double-click **`APProcessor.bat`** (Mac: `APProcessor.command`;
the first time, right-click → *Open*). The first run takes a few minutes: it creates `.venv`, installs
the packages, picks the data folder and downloads the OCR models. After that the laptop can stay offline.

**B. Fully offline (air-gapped laptop).** On any computer with internet, build the bundle:

```
python scripts/build_offline_bundle.py
```

It writes `dist/APProcessor-offline-<version>-<date>.zip` with the code, a `wheelhouse/` folder (every
package, for Windows 64-bit and the computer that built it, Python 3.11 and 3.12) and the OCR models.
Add `--platform macosx_11_0_arm64` for Apple-silicon Macs; `--dry-run` shows the plan without
downloading. Copy the ZIP to the laptop (USB stick), unzip it, double-click `APProcessor.bat`. Seeing
`wheelhouse/`, the launcher installs from it with `pip --no-index` and never goes online.

The laptop's Python must match one the bundle was built for (3.11 or 3.12 by default).

**Check the install** (a minute, nothing is saved): in the AP Coder folder run
`.venv\Scripts\python.exe scripts\pilot_check.py`. It reads the ten sample invoices on the laptop, with
no AI model and no Azure, plus one as a scan through local OCR, and prints `OK` for each one that matches
its answer. The same check runs on a clean Windows machine in CI for every change, both from the
double-click launcher and from the offline bundle with the internet cut off.

## Daily use

1. Double-click **`APProcessor.bat`**. A black window opens and the dashboard opens in your browser
   (at `http://127.0.0.1:8501`). Keep the window open while you work; close it to stop AP Coder.
2. **Bring in invoices:** drop PDFs, scans or photos in the **invoices folder** (Process invoices →
   *Open folder*), or upload them on the *Process invoices* page.
3. **Review:** open each invoice in the *Review queue*, check the fields the capture marked *check*,
   the GL split and the tax lines, correct what is wrong and **Approve** (or park / reject). Every
   approval teaches AP Coder how that vendor is coded.
4. **Export to JD Edwards E1:** *Exports* → pick the approved invoices → *JD Edwards E1* → download the
   ZIP (F0411Z1 / F0911Z1 CSVs and a README with the loading steps). Load it with R04110ZA, proof mode
   first. Details and the one-time JDE settings: [JDE_E1.md](JDE_E1.md).

## Where your data lives

Everything AP Coder keeps is in one **data folder**, outside the code: by default `C:\Users\<you>\APCoder`
(Mac: `~/APCoder`). It holds the database (`ap_coder.db`: setup, invoices, reviews, the learning memory),
the `invoices` folder, the daily `backups` and exports. The folder is recorded in
`C:\Users\<you>\.ap_coder\settings.json`. Don't put the data folder on OneDrive or a network share: the
open database can be damaged by syncing.

## Backup

AP Coder backs up the database once a day, when it starts, into `backups` in the data folder. For protection against a lost
laptop, set **Settings → Data & backups → Also copy each backup to** to a OneDrive or network folder: the
backup copies are safe to sync (only the live database must stay local). To back up by hand, close AP Coder
and copy the whole data folder.

## Update

Close AP Coder. Unzip the new version **over** the old folder (or into a new folder), then double-click
`APProcessor.bat`. It sees what changed and installs only that (from the new bundle's `wheelhouse/` when
there is one). Your data folder is not touched, so nothing is lost or duplicated.

## Troubleshooting

| What you see | What to do |
|---|---|
| *Python 3.11 or newer is needed* | Install Python 3.12 (above), tick *Add python.exe to PATH*, double-click again. |
| *AP Coder stopped. The message above says why.* | Read the lines above it; most often a package could not install. Online: check the proxy (`set HTTPS_PROXY=http://proxy:8080` in a terminal, then run `APProcessor.bat` from it). Offline: the bundle was built for another Python version. |
| The browser does not open | Open `http://127.0.0.1:8501` yourself (the window shows the exact address; it uses the next free port if 8501 is taken). |
| Scans are read, but with a note about one OCR engine | The PP-OCRv5 models are missing. When online, run `.venv\Scripts\python.exe scripts\fetch_models.py` (Mac: `.venv/bin/python scripts/fetch_models.py`). |
| Something else | In `terminal.bat`, run `python -m ap_coder doctor` and send the report: it lists checks only, never keys, file names or invoice data. |
| Start over | Delete the `.venv` folder and double-click `APProcessor.bat` (your data is kept). |

## What is sent over the network

**Nothing**, in the offline build:

- The dashboard listens only on this laptop (`127.0.0.1`); other computers cannot open it. To share it
  on the office network on purpose, set `AP_DASHBOARD_ADDRESS=0.0.0.0` before starting.
- Streamlit's usage statistics are switched off; fonts and icons come from the app, not the internet.
- OCR runs on the laptop with local models; the optional LM Studio model runs on the laptop too and is
  reached over `localhost`.
- A test (`tests/test_offline.py`) reads an invoice, runs every dashboard page and builds a JD Edwards
  batch with all outside connections blocked, and fails if anything tries to connect.

The only outside connections are the ones you choose: the one-time package and model download of an
online install, and Azure, if someone fills in Azure settings (then invoices go to your organisation's
own Azure resources).
