# Pilot guide: AP Coder on one laptop, offline

A short guide for running the pilot on a Windows laptop (a Mac works the same way). For the four-week
plan that turns the pilot into a decision, see [PILOT_PLAN.md](PILOT_PLAN.md).

## What you need

- **Windows 10 or 11** (64-bit), or a Mac with macOS 12 or newer.
- **Python 3.11, 3.12 or 3.13.** Nothing to do if the laptop has one: AP Coder uses it as it is and never
  installs a second Python. If it has none, the first double-click offers to install Python 3.12 for you
  (Windows, with `winget`: answer **Y**). Without `winget` or internet, install it from
  [python.org](https://www.python.org/downloads/) and tick **Add python.exe to PATH** on the first screen.
  Mac: install Python 3.12 from python.org (or `brew install python@3.12`).
- About **2 GB** free disk space.
- **Optional: LM Studio** with a local model, for the AI coding step on this laptop (see the LM Studio
  section of the [README](../README.md)). AP Coder starts and works without it: every invoice is still
  read on the laptop (PDF text, or local OCR for scans) and each line is coded from what AP approved
  before for that vendor, the vendor master's default account and your fixed coding rules. A line with
  nothing to learn from is left for you to code. With a model running, it also proposes accounts for
  vendors and lines it has not seen yet.

  **Which model, and what to expect** (measured on the ten samples with nothing approved yet, through the
  same OpenAI-compatible server API LM Studio uses): the invoice is always read by AP Coder's own reader,
  which got every header and line right; the model is asked only for the accounts. A 7B instruct model
  (e.g. Qwen 2.5 7B Instruct, Q4_K_M) picked the right account for 26 of 41 lines on its own; used the way
  AP Coder uses it, after the account-name match, 27 of 41 were right before any approval. A 1.5B model did
  worse than the name match (11 of 41), so load a 7B–8B model if the laptop can. Checked in LM Studio
  itself (Qwen 2.5 7B Instruct Q4_K_M, 8k context): on a 4-core laptop CPU without a graphics card an invoice
  that needed the model took 1 to 6 minutes (the first call of the day is the slowest); invoices whose lines
  were all coded without it took no model time at all. A graphics card is many times faster. If the model is
  slower than 10 minutes (`AP_LLM_TIMEOUT_SECONDS`), the invoice is still read and checked; its uncoded lines
  wait for AP. Each approval teaches AP Coder that vendor's accounts, which then come before the model.

  **Qwen 3.5 9B in LM Studio.** In LM Studio search for *Qwen3.5 9B* and download the Q4_K_M file (5.6 GB,
  plus its 0.9 GB vision file, `mmproj`, in the same lmstudio-community repo). Load it with **Context Length 8192**: the accounts
  call is short (about 1,300 tokens of chart and lines with the sample chart, and about 60 tokens of answer
  per line), and Qwen's own advice to keep 128K applies to thinking, which AP Coder turns off.
  - *Thinking.* Qwen 3.5 thinks before it answers unless told not to (LM Studio lists its reasoning
    setting as on/off, default on). On a laptop CPU the thinking alone takes minutes. AP Coder sends
    `reasoning_effort: "none"` with every request, which LM Studio 0.4.8 and newer applies: checked in
    LM Studio with Qwen 3.5 2B, the reply came back with 0 reasoning tokens. The other switch Qwen
    documents, `chat_template_kwargs: {"enable_thinking": false}`, is also sent for servers that read it,
    but LM Studio ignores it (its bug tracker #1990; checked with the 2B: all 100 of 100 tokens went to
    thinking and the answer was empty). Qwen 3.5 has no `/no_think` prompt switch. So **use LM Studio 0.4.8 or newer**; on an
    older one, turn the model's thinking off in LM Studio. If thinking still runs, the doctor's *local dry
    run* and the log say *"The model spent its answer thinking and never wrote the JSON"* instead of coding
    nothing silently.
  - *Max tokens.* Leave `AP_LLM_MAX_TOKENS` at 4096: with thinking off a two-line answer was 123 tokens.
    Raising it does not cure thinking on a CPU (with thinking on, a 4096-token call ran past 10 minutes and
    `AP_LLM_TIMEOUT_SECONDS`); turning thinking off does.
  - *Speed measured.* `python -m ap_coder doctor --online` with Qwen 3.5 2B on a busy 4-core CPU, no
    graphics card: 100 s for the two-line dry run (69 s reading the prompt, 30 s writing the answer). The 9B
    is several times larger, so expect it to be slower on the same laptop; a graphics card is much faster.
  - The doctor shows *can see pages: yes* for it (every Qwen 3.5 size has a vision encoder). Invoices are
    read by AP Coder's own reader either way.
- **No Azure needed.** Azure Document Intelligence and Azure OpenAI stay optional; leave Settings → Azure
  empty for the pilot.

## Install: one button

**`APProcessor.bat`** (Mac: **`APProcessor.command`**) is the only file to click, the first time and
every time after. `install.bat` and `start.bat` are kept for older shortcuts and do the same thing.

**A. Online once (simplest).** Download the AP Coder ZIP, unzip it to a folder that is **not** synced by
OneDrive (e.g. `C:\APCoder\app`), and double-click **`APProcessor.bat`** (Mac: `APProcessor.command`;
the first time, right-click → *Open*). The first run takes a few minutes and sets up only what is
missing:

- **Python**: uses the Python 3.11–3.13 already installed; only if there is none, offers to install
  Python 3.12 (see *What you need*).
- **`.venv`**: AP Coder's own environment, made once and reused (made again only if it is broken).
- **Packages**: installs only the ones that are missing or too old (all of them the first time).
- **Data folder** (`C:\Users\<you>\APCoder`) and the **OCR models**: once.
- **Desktop shortcut** *AP Coder*: once, never a second copy (deleted? it is not put back).

Then it checks itself and prints a short summary before the browser opens:

```
AP Coder readiness:
  [OK] Python 3.12.4 (.venv)
  [OK] Packages: all 13 in place
  [OK] OCR for scanned invoices
  [OK] Data folder: C:\Users\you\APCoder
  [--] LM Studio: not running (optional: invoices are still read and coded without it)
  [OK] Self-check OK: 10 of 10 sample invoices read right, and a scan with local OCR (9 s)
```

The self-check (the ten sample invoices, read on the laptop with no AI model and no Azure) runs on the
first start and after an update; later starts show its last result and take a few seconds. If it fails,
the summary says so plainly and AP Coder still starts. LM Studio is never installed by AP Coder: the
summary only says whether it is running and which model is loaded (*LM Studio: model qwen3.5-9b loaded*).
After the first run the laptop can stay offline.

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

**Check the install again** (a few seconds, nothing is saved): `APProcessor.bat --check` runs the
self-check before starting; for every sample's line, run `.venv\Scripts\python.exe scripts\pilot_check.py`
in the AP Coder folder. The same check runs on a clean Windows machine in CI for every change, from the
double-click (first start, a second double-click while it runs, a later start) and from the offline
bundle with the internet cut off.

## Daily use

1. Double-click **`APProcessor.bat`** (or the *AP Coder* desktop shortcut). A black window opens with
   the readiness summary, and the dashboard opens in your browser (at `http://127.0.0.1:8501`). Keep
   the window open while you work; close it to stop AP Coder. Double-clicking again while it runs just
   brings the running dashboard back up in the browser; it never starts a second copy.
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
| *AP Coder needs Python 3.11, 3.12 or 3.13, and it is not installed* | Install Python 3.12 from python.org, tick *Add python.exe to PATH*, double-click again. |
| *AP Coder stopped. The message above says what to do.* | The `What to do:` line above it says it; most often a package could not install. Online: check the proxy (`set HTTPS_PROXY=http://proxy:8080` in a terminal, then run `APProcessor.bat` from it). Offline: the bundle was built for another Python version. |
| *Self-check FAILED* in the summary | AP Coder still runs. Run `.venv\Scripts\python.exe scripts\pilot_check.py` in `terminal.bat` and send the output. |
| *AP Coder is already running* | It is: the browser opens on it. Use that window, or close it to stop AP Coder. |
| The browser does not open | Open `http://127.0.0.1:8501` yourself (the window shows the exact address; it uses the next free port if 8501 is taken). |
| Scans are read, but with a note about one OCR engine | The PP-OCRv5 models are missing. When online, run `.venv\Scripts\python.exe scripts\fetch_models.py` (Mac: `.venv/bin/python scripts/fetch_models.py`). |
| Something else | In `terminal.bat`, run `python -m ap_coder doctor` and send the report: it lists checks only, never keys, file names or invoice data. |
| Start over | Delete the `.venv` folder and double-click `APProcessor.bat` (your data is kept; it is set up again, once). |

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
