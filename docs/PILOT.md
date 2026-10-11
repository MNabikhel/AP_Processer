# Pilot guide: AP Coder on one laptop, offline

A short guide for running the pilot on a Windows laptop (a Mac works the same way). For the four-week
plan that turns the pilot into a decision, see [PILOT_PLAN.md](PILOT_PLAN.md).

## What you need

- **Windows 10 or 11** (64-bit), or a Mac with macOS 12 or newer.
- **Python 3.12 (or 3.11).** Nothing to do if the laptop has one: AP Coder uses it as it is and never
  installs a second Python. The offline bundle has packages for 3.11 and 3.12; 3.13 is used only when neither
  is installed, and needs a bundle built for it. AP Coder never downloads Python: if the laptop has none, IT
  installs Python 3.12 from [python.org](https://www.python.org/downloads/) with **Add python.exe to PATH**
  ticked on the first screen (Mac: the python.org installer).
- About **2 GB** free disk space.
- **LM Studio with OvisOCR2** (IT installs it, 0.4.8 or newer, with the models already in it; nothing is
  downloaded on the laptop). OvisOCR2 is part of how every invoice is read: a small page-reading model that
  reads every page of every invoice (digital PDFs, scans and photos) on its own, as a second reader next to
  the PDF's text or OCR (see [How every invoice is read](../README.md#how-every-invoice-is-read)). IT copies
  the *bartowski* build at **Q8_0** (`ATH-MaaS_OvisOCR2-Q8_0.gguf`, 813 MB, and its 205 MB vision file
  `mmproj-ATH-MaaS_OvisOCR2-f16.gguf`) into LM Studio's models folder under
  `bartowski/ATH-MaaS_OvisOCR2-GGUF`. Leave it unloaded: AP Coder has LM Studio load it, with the context it
  needs, when there is a page to read. **There is nothing to choose or link**: see *Set up LM Studio* below.
  AP Coder still starts without LM Studio: invoices are read by the PDF's text or OCR, and wait for a person.
  - *Speed.* Minutes a page on a laptop CPU, so it reads in the background; invoices are in the review queue
    at once and their fields update when it is done. Measured
    with LM Studio's headless server on a 4-core server CPU, no graphics card, where LM Studio gave
    the model a single thread: 3.5 to 5.7 minutes for a phone photo, 7.6 to 12.4 minutes for a scanned PDF page. On a
    laptop that LM Studio lets use several cores, expect a few minutes a page (CloseDesk measured about 3 on 4
    cores); a graphics card is many times faster. If it seems slow, check *CPU Thread Pool Size* for OvisOCR2
    in LM Studio (My Models, the gear next to it).
  - *Memory.* OvisOCR2 is small (about 1 GB on disk), but reading a page image needs working memory too:
    LM Studio holds up to 4 readings at once by default, each with the 20,480-token context a page image needs,
    and the model's server used about 8 GB while reading. On a 16 GB laptop that also has the 9B chat model
    loaded, set *Max Concurrent Predictions* to 1 for OvisOCR2 in LM Studio.
  - *Overnight instead.* `python -m ap_coder read-pages --minutes 240` reads the queue and stops; schedule
    it with Task Scheduler for a laptop that is busy during the day.
- **Optional: a chat model in LM Studio**, for GL suggestions. Without it every invoice is still read and
  checked the same way, and each line is coded from what AP approved before for that vendor, the vendor
  master's default account and your fixed coding rules. A line with nothing to learn from is left for you to
  code. With a model running, it also proposes accounts for vendors and lines it has not seen yet.

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

  **Qwen 3.5 9B in LM Studio.** IT puts the chat model in LM Studio next to OvisOCR2 (Qwen 3.5 9B, Q4_K_M:
  5.6 GB, plus its 0.9 GB vision file, `mmproj`, from the lmstudio-community repo; or another 7B–9B
  instruct model at Q4_K_M). If no chat model is loaded, load it: in AP Coder, *Settings → Reading → Models
  in LM Studio* → **Load** (or in LM Studio itself). Use **Context Length 8192**: the accounts
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
  - The chat model only suggests accounts: it never reads the invoice. Invoices are read by AP Coder's own
    readers and OvisOCR2.
- **No Azure.** The offline build never uses cloud services and the installer asks no cloud questions.
  Azure exists only for developers with the internet allowed (`AP_ALLOW_INTERNET=1`).

## Install: one button

**`APProcessor.bat`** (Mac: **`APProcessor.command`**) is the only file to click, the first time and
every time after. `install.bat` and `start.bat` are kept for older shortcuts and do the same thing.

AP Coder is an offline enterprise build: the laptop never connects to the internet, not even to set up.
Everything comes in the **offline bundle** IT builds (step B). Unzip it to a folder that is **not** synced
by OneDrive (e.g. `C:\APCoder\app`), and double-click **`APProcessor.bat`** (Mac: `APProcessor.command`;
the first time, right-click → *Open*). The first run takes a few minutes and sets up only what is
missing:

- **Python**: uses the Python already installed, 3.12 or 3.11 first (see *What you need*); never downloads one.
- **`.venv`**: AP Coder's own environment, made once and reused (made again only if it is broken).
- **Packages**: installs only the ones that are missing or too old (all of them the first time), from the
  bundle's `wheelhouse/` folder. One that is not in it is named in the summary (it must come from the
  bundle); AP Coder still starts when its core packages are there (without the OCR add-on scans can't be read).
- **Data folder** (`C:\Users\<you>\APCoder`) and the **OCR models** (copied from the bundle's `models/`): once.
- **Desktop shortcut** *AP Coder*: once, never a second copy (deleted? it is not put back).

A second double-click while the first one is still setting up waits for it and then opens its dashboard;
it never runs a second setup.

Then it checks itself and prints a short summary before the browser opens:

```
AP Coder readiness:
  [OK] Python 3.12.4 (.venv)
  [OK] Packages: all 13 in place
  [OK] OCR for scanned invoices
  [OK] Data folder: C:\Users\you\APCoder
  [OK] LM Studio: model qwen3.5-9b loaded
  [--] Page reader: ath-maas_ovisocr2 found; its self-test runs on its own before it reads invoices
  [OK] Self-check OK: 10 of 10 sample invoices read right, and a scan with local OCR (9 s)
```

The self-check (the ten sample invoices, read on the laptop with no AI model and no Azure) runs on the
first start and after an update; later starts show its last result and take a few seconds. If it fails,
the summary says so plainly and AP Coder still starts. LM Studio is never installed by AP Coder: the
summary only says whether it is running, which chat model is loaded, and whether OvisOCR2 was found and
passed its self-test. Once it has: *[OK] Page reader: ath-maas_ovisocr2 found, self-test passed (reads every
page of every invoice)*. Without LM Studio: *[!!] Page reader: OvisOCR2 isn't running in LM Studio: invoices
are read by OCR only and wait for a person*.

### Set up LM Studio: start the server, nothing to choose

1. **IT installs LM Studio** with OvisOCR2 (and, optionally, the chat model) already in it (see *What you
   need*).
2. **Start the server**: open LM Studio, **Developer** tab → **Start server**. Then start AP Coder.
3. **AP Coder does the rest on its own.** It finds OvisOCR2 in LM Studio and runs its self-test by itself:
   OvisOCR2 reads a scan of a sample invoice whose answers are known, field by field. This takes 10 to 20
   minutes on a laptop the first time (once per model). *Settings → Reading* shows its progress and, after,
   what it read on the test invoice. Until it has passed, invoices are read by OCR and wait for a person; a
   banner on the Process and Review pages says so.

There is nothing to choose and nothing to link: every invoice is read the same way. A failed self-test is
tried again a day later, or at once with *Run the self-test again* in *Settings → Reading*.

**B. Building the offline bundle (IT, once per version).** On any computer with internet, build the bundle
(the one step that downloads; it opts in to the internet for itself):

```
python scripts/build_offline_bundle.py
```

It writes `dist/APProcessor-offline-<version>-<date>.zip` with the code, a `wheelhouse/` folder (every
package, for Windows 64-bit and the computer that built it, Python 3.11 and 3.12) and the OCR models.
Add `--platform macosx_11_0_arm64` for Apple-silicon Macs; `--dry-run` shows the plan without
downloading. Copy the ZIP to the laptop (USB stick), unzip it, double-click `APProcessor.bat`. Seeing
`wheelhouse/`, the launcher installs from it with `pip --no-index` and never goes online.

The laptop's Python must match one the bundle was built for (3.11 or 3.12 by default; add
`--python 3.13` for a laptop that only has 3.13).

**Developers with internet** can set up from a plain checkout instead: `set AP_ALLOW_INTERNET=1` in a
terminal, then run `APProcessor.bat` from it (see *What is sent over the network*).

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
3. **Review (the clerk's job):** open each invoice in the *Review queue*, check the fields marked
   *Check*, the GL split and the tax lines, correct what is wrong and click **Approve & teach** (or park /
   reject). Each corrected field is recorded for that vendor and updates its template; the confirmation
   says what was learned and how far the vendor is from touchless. *Learning & accuracy → Supplier
   learning* shows each vendor's training. Bulk approvals nobody opened don't count toward touchless.
4. **Export to JD Edwards E1:** *Exports* → pick the approved invoices → *JD Edwards E1* → download the
   ZIP (F0411Z1 / F0911Z1 CSVs and a README with the loading steps). Load it with R04110ZA, proof mode
   first. Details and the one-time JDE settings: [JDE_E1.md](JDE_E1.md).

## Touchless processing: when to turn it on

Touchless processing is **off** by default: every invoice is approved by a person. Leave it off for the
first weeks of the pilot. Let the clerks review, correct and *Approve & teach*, and watch **Learning &
accuracy → Supplier learning**: it shows, vendor by vendor, how often a field was corrected and how far
each one is from the bar.

When several of your regular vendors are close to the bar and the corrections have settled, a manager
turns it on in **Settings → Automation** (with a confirmation; recorded in the Activity log with their
name). From then on, a vendor that meets the bar goes touchless by itself:

- at least 20 invoices reviewed by a person;
- header fields read right at least 99% of the time, with 95% confidence, over its last 200 invoices
  (with about 13 header fields an invoice, that usually takes about 30 clean invoices);
- the last 10 with no correction at all.

5% of touchless invoices still go to a person as an audit. One correction, or a touchless invoice
reopened, suspends the vendor until it has a fresh clean streak. *Keep supervised* (Learning & accuracy)
keeps one vendor with a person whatever its record. Some invoices always go to a person: a changed bank
account or GST/HST number, any sign of a duplicate, an unusual amount, a vendor not in the vendor master
or on hold, a credit note, a total above *Largest invoice approved without a person* (5,000.00 CAD by
default, set in Settings → Automation) or above the second-approval limit, and an invoice OvisOCR2 has
not read yet. Settings → Automation also shows the touchless rate and the errors found in touchless
invoices.

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
`APProcessor.bat`. It sees what changed and installs only that (from the new bundle's `wheelhouse/`; it
never checks the internet for updates). Your data folder is not touched, so nothing is lost or duplicated.

## Troubleshooting

| What you see | What to do |
|---|---|
| *AP Coder needs Python 3.11, 3.12 or 3.13 (3.12 is best), and it is not installed* | IT installs Python 3.12 from python.org, with *Add python.exe to PATH* ticked; double-click again. |
| *AP Coder stopped. The message above says what to do.* | The `What to do:` line above it says it; most often a package could not install. *Missing packages: ...*: the folder has no `wheelhouse/` (unzip the full offline bundle over it). *The offline bundle's wheelhouse folder has no ...*: the bundle was built for another Python version. Developers online (`AP_ALLOW_INTERNET=1`): check the proxy (`set HTTPS_PROXY=http://proxy:8080`). |
| *AP Coder is being set up or started in another window* | The first double-click is still setting up: it waits, then opens the dashboard. |
| *Self-check FAILED* in the summary | AP Coder still runs. Run `.venv\Scripts\python.exe scripts\pilot_check.py` in `terminal.bat` and send the output. |
| *AP Coder is already running* | It is: the browser opens on it. Use that window, or close it to stop AP Coder. |
| The browser does not open | Open `http://127.0.0.1:8501` yourself (the window shows the exact address; it uses the next free port if 8501 is taken). |
| Scans are read, but with a note about one OCR engine | The PP-OCRv5 models are missing or damaged. Unzip the full offline bundle (with its `models/` folder) over the AP Coder folder and double-click again, or run `.venv\Scripts\python.exe scripts\fetch_models.py` (Mac: `.venv/bin/python scripts/fetch_models.py`): it copies them from `models/`. |
| A banner: *LM Studio isn't running* or *OvisOCR2 isn't running in LM Studio* | Open LM Studio, **Developer** tab → **Start server**; check that OvisOCR2 is in its models (ask IT if not). Invoices are still read by OCR and wait for a person meanwhile. |
| A banner: *OvisOCR2 is testing itself* | Nothing to do: its self-test runs on its own (10 to 20 minutes the first time). *Settings → Reading* shows its progress. |
| A banner: *OvisOCR2 failed its self-test* | *Settings → Reading* shows what it read on the test invoice. Check that it is the Q8_0 build from IT, then *Run the self-test again*. |
| Something else | In `terminal.bat`, run `python -m ap_coder doctor` and send the report: it lists checks only, never keys, file names or invoice data. |
| Start over | Delete the `.venv` folder and double-click `APProcessor.bat` (your data is kept; it is set up again, once). |

## What is sent over the network

**Nothing**, in the offline build:

- The setup never connects to the internet: packages come only from the bundle's `wheelhouse/`
  (`pip --no-index`), the OCR models only from its `models/` (RapidOCR is given their paths, so its own
  downloader never runs, and a damaged model file counts as missing instead of being fetched again), the
  installer runs no `git fetch` / `git pull`, and APProcessor.bat never installs Python with `winget`.
- The dashboard listens only on this laptop (`127.0.0.1`); other computers cannot open it. To share it
  on the office network on purpose, set `AP_DASHBOARD_ADDRESS=0.0.0.0` before starting.
- Streamlit's usage statistics are switched off; fonts and icons come from the app, not the internet.
- OCR runs on the laptop with local models; OvisOCR2 and the optional chat model run in LM Studio on the
  laptop too and are reached over `localhost`.
- A test (`tests/test_offline.py`) reads an invoice, runs every dashboard page and builds a JD Edwards
  batch with all outside connections blocked, and fails if anything tries to connect.

The only outside connections are the ones someone chooses:

- **`AP_ALLOW_INTERNET=1`**, the one opt-in, for developers and the bundle builder only. With it set in the
  terminal that runs APProcessor.bat / install.bat, missing packages come from PyPI, the OCR models from
  www.modelscope.cn, the installer checks GitHub for updates and APProcessor.bat offers to install
  Python 3.12 with `winget`. `scripts/build_offline_bundle.py` sets it for itself, on IT's computer. Never set
  it on a pilot laptop.
- Azure, only on a developer's computer with `AP_ALLOW_INTERNET=1` and Azure settings filled in (then
  invoices go to your organisation's own Azure resources). The offline build ignores Azure settings.
