# Pilot guide: AP Coder on one laptop, offline

A short guide for running the pilot on a Windows laptop (a Mac works the same way). For the four-week
plan that turns the pilot into a decision, see [PILOT_PLAN.md](PILOT_PLAN.md).
To show it to someone, follow [DEMO_RUN_SHEET.md](DEMO_RUN_SHEET.md): `APProcessor-demo.bat` runs AP Coder on a
data folder of its own, so a demo never mixes with the pilot's invoices. What it does not do yet:
[KNOWN_LIMITS.md](KNOWN_LIMITS.md).

## What you need

- **Windows 10 or 11** (64-bit), or a Mac: Apple silicon with macOS 14 or newer, or Intel with macOS 13 or newer.
- **Python 3.11 or newer** (3.12 recommended). Nothing to do if the laptop has one: AP Coder uses it as it
  is and never installs a second Python. When several are installed, the ones the offline bundle has
  packages for come first (3.12, 3.11, 3.13, 3.14, then any newer); a Python the bundle was not built for
  needs a bundle built for it (the launcher says so, with the command). AP Coder never downloads Python: if
  the laptop has none, or only an older one (it then says *Python 3.10 is installed, but AP Coder needs
  Python 3.11 or newer*), IT
  installs Python 3.12 from [python.org](https://www.python.org/downloads/) with **Add python.exe to PATH**
  ticked on the first screen (Mac: the python.org installer).
- About **2 GB** free disk space.
- **LM Studio with OvisOCR2** (IT installs it, 0.4.8 or newer, with the models already in it; nothing is
  downloaded on the laptop). OvisOCR2 is part of how every invoice is read: a small page-reading model that
  reads every page of every invoice (digital PDFs, scans and photos) on its own, as a second reader next to
  the PDF's text or OCR (see [How every invoice is read](REFERENCE.md#how-every-invoice-is-read)). IT copies
  the *bartowski* build at **Q8_0** (`ATH-MaaS_OvisOCR2-Q8_0.gguf`, 813 MB, and its 205 MB vision file
  `mmproj-ATH-MaaS_OvisOCR2-f16.gguf`) into LM Studio's models folder under
  `bartowski/ATH-MaaS_OvisOCR2-GGUF`. Leave it unloaded: AP Coder has LM Studio load it, with the context it
  needs, when there is a page to read. **There is nothing to choose or link**: see *Set up LM Studio* below.
  AP Coder starts without LM Studio, but the pilot runs with it: without OvisOCR2 invoices are read by the PDF's
  text or OCR, and every invoice waits for a person.
  - *Speed.* Minutes a page on a laptop CPU, so it reads in the background; invoices are in the review queue
    at once and their fields update when it is done. Measured
    with LM Studio's headless server on a 4-core server CPU, no graphics card, where LM Studio gave
    the model a single thread: 3.5 to 5.7 minutes for a phone photo, 7.6 to 12.4 minutes for a scanned PDF page. On a
    laptop that LM Studio lets use several cores, expect a few minutes a page (CloseDesk measured about 3 on 4
    cores); a graphics card is many times faster. If it seems slow, check *CPU Thread Pool Size* for OvisOCR2
    in LM Studio (My Models, the gear next to it).
  - *Memory.* OvisOCR2 is small (about 1 GB on disk), and AP Coder loads it with LM Studio's *Flash Attention*
    on: reading a page, its server then used 1.8 GB, and read it in 6 minutes instead of 11 on the same CPU. With
    Flash Attention off it used 8.9 GB and kept it while loaded, more than a 16 GB laptop has room for beside
    Gemma 4 12B; *Max Concurrent Predictions* made no difference. So in LM Studio turn **Flash Attention on for
    OvisOCR2** (My Models, the gear next to it): then a copy loaded by CloseDesk or by hand has it too.
  - *Overnight instead.* `python -m ap_coder read-pages --minutes 240` reads the queue and stops; schedule
    it with Task Scheduler for a laptop that is busy during the day.
- **A chat model in LM Studio** (Gemma 4 12B, else Qwen 3.5 9B; the pilot runs with it): it reviews each invoice
  (the AI reviewer), reads its header from the page images, and suggests GL accounts for new lines.
  Without it every invoice is still read and checked the same way, waits for a person, and each line is
  suggested an account only from what AP approved before for that vendor, the vendor master's default account
  and your fixed coding rules; a line with nothing to learn from is left for you to code. With the model
  running, it also proposes accounts for vendors and lines it has not seen yet.

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

  **The chat model in LM Studio.** IT puts it in LM Studio next to OvisOCR2: **Gemma 4 12B** (Google), QAT Q4_0,
  7.0 GB plus a 0.18 GB vision file, set up as in
  [LM_STUDIO.md](LM_STUDIO.md#using-gemma-4-12b-instead-of-qwen-35-9b-windows-laptop); or Qwen 3.5 9B, Q4_K_M
  (5.6 GB, plus its 0.9 GB vision file, `mmproj`, from the lmstudio-community repo). If no chat model is loaded, load it: in AP Coder, *Settings → Reading → Models
  in LM Studio* → **Load** (or in LM Studio itself). Use **Context Length 16384**, the size CloseDesk
  loads it with, so a Mac running both apps keeps one copy of it ([LM_STUDIO.md](LM_STUDIO.md)). The accounts
  call itself is short (about 1,300 tokens of chart and lines with the sample chart, and about 60 tokens of answer
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
  - The chat model has three jobs: it reads the invoice's header from the page images (one more independent
    reader next to AP Coder's own readers and OvisOCR2), reviews every invoice (the AI reviewer) and suggests
    accounts. It never decides alone: its answers are checked against the other readers and your rules.
- **No Azure needed.** The offline build reads and codes every invoice on the laptop, and the installer asks no
  cloud questions. The optional Azure check is the one exception: it has its own switch (Settings → Reading),
  is off by default and sends nothing unless IT switches it on; it does not need `AP_ALLOW_INTERNET`. Azure as
  the reader or the coder exists only for developers with the internet allowed (`AP_ALLOW_INTERNET=1`).

## Install: one button

**`APProcessor.bat`** (Mac: **`APProcessor.command`**) is the only file to click, the first time and
every time after. There is one such file per computer, and nothing else to choose from. (IT can add
`--installer` to it for the installer's questions: `APProcessor.bat --installer`, typed in `scripts\terminal.bat`.)

AP Coder is an offline enterprise build: the laptop never connects to the internet, not even to set up.
Everything comes in the **offline bundle** IT builds (step B). Unzip it to a folder that is **not** synced
by OneDrive (e.g. `C:\APCoder\app`), and double-click **`APProcessor.bat`** (Mac: `APProcessor.command`;
the first time, right-click → *Open*). The first run takes a few minutes and sets up only what is
missing.

*A ZIP downloaded from the internet* (a browser, Teams, Outlook) is marked as such. Windows: right-click the ZIP →
*Properties* → tick **Unblock** before *Extract All*; otherwise Windows asks *Do you want to run this file?* (or
SmartScreen says *Windows protected your PC*: *More info* → *Run anyway*) until AP Coder has started once, which
takes the mark off its own launchers. Mac: *"APProcessor.command" can't be opened* (or *Apple could not verify*):
right-click it → *Open* → *Open*; on macOS 15 or newer, click *Done*, then **System Settings → Privacy & Security →
Open Anyway**. Or, in Terminal: `xattr -dr com.apple.quarantine ` followed by the AP Coder folder dragged into the
window, then Return. Once started, AP Coder takes the quarantine off its own launchers. What the first run sets up:

- **Python**: uses the Python already installed, 3.11 or newer, 3.12 first (see *What you need*); never
  downloads one.
- **`.venv`**: AP Coder's own environment, made once and reused (made again only if it is broken, or
  made with a Python the bundle has no packages for while one it has is installed).
- **Packages**: installs only the ones that are missing or too old (all of them the first time), from the
  bundle's `wheelhouse/` folder. One that is not in it is named in the summary (it must come from the
  bundle); AP Coder still starts when its core packages are there (without the OCR add-on scans can't be read).
- **Data folder** (`C:\Users\<you>\APCoder`) and the **OCR models** (copied from the bundle's `models/`): once.
- **Desktop shortcut** *AP Coder* (Mac: *AP Coder.command* on the desktop): one, never a second copy, and it
  always opens the current version (an update points it at the new one; deleted? it is not put back).

A second double-click while the first one is still setting up waits for it and then opens its dashboard;
it never runs a second setup.

Then it checks itself and prints a short summary before the browser opens:

```
AP Coder readiness:
  [OK] Python 3.12.4
  [OK] Packages: all 15 in place
  [OK] OCR for scanned invoices
  [OK] Data folder: C:\Users\you\APCoder
  [OK] Chat model: lmstudio-community/gemma-4-12B-it-QAT-GGUF loaded in LM Studio (can see pages)
  [OK] Chat model test passed: accounts from the chart in 4s: line 1 -> 6100, line 2 -> 6400
  [--] Page reader: ath-maas_ovisocr2 found; its self-test runs on its own before it reads invoices
  [OK] Setup checks: all 9 passed (GL accounts, tax mapping, coding rules, settings)
  [OK] Self-check OK: 10 of 10 sample invoices read right, and a scan with local OCR (9 s)
```

The self-check (the ten sample invoices, read on the laptop with no AI model and no Azure) runs on the
first start and after an update; later starts show its last result and take a few seconds. If it fails,
the summary says so plainly and AP Coder still starts. LM Studio is never installed by AP Coder: the
summary only says whether it is running, which chat model is loaded, and whether OvisOCR2 was found and
passed its self-test. Once it has: *[OK] Page reader: ath-maas_ovisocr2 found, self-test passed (reads every
page of every invoice)*. Without LM Studio, one line says so and what to do: *[!!] LM Studio isn't running yet:
until it is, invoices are read by OCR only and wait for a person. To start it: open LM Studio, Developer tab → Start
server; then press Check again in AP Coder's Settings → Reading (no need to restart AP Coder)*.

### Set up LM Studio: start the server, nothing to choose

1. **IT installs LM Studio** with OvisOCR2 and the chat model already in it (see *What you need*). AP Coder
   starts without LM Studio, but the pilot runs with it: without OvisOCR2 every invoice waits for a person, and
   the chat model reviews each invoice.
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
python scripts/build_offline_bundle.py          # Windows laptops
python scripts/build_offline_bundle.py --mac    # Windows laptops and Macs
```

It writes `dist/APProcessor-offline-<version>-<date>.zip` with the code, a `wheelhouse/` folder (every
package, for Windows 64-bit and the computer that built it, Python 3.11, 3.12, 3.13 and 3.14) and the OCR
models: about 0.5 GB. **When a tester has a Mac, add `--mac`**: it adds Apple-silicon Macs (macOS 14 or
later) and Intel Macs (macOS 13 or later), for the same Python versions, and makes the ZIP about 1.5 GB
(`--platform win_amd64 --platform macosx_14_0_arm64`: Windows and Apple silicon only, about 1 GB). The
bundle can be built on any of these computers for all the others. `--dry-run` shows the plan without
downloading. Copy the ZIP to the laptop (USB stick), unzip it, double-click `APProcessor.bat` (Mac:
`APProcessor.command`). Seeing `wheelhouse/`, the launcher installs from it with `pip --no-index` and never
goes online.

> **For IT: what is in the bundle, and where the build downloads from**
>
> - **What the default build holds:** the code (stamped with the commit it was built from: *Settings → About* and
>   the launcher show it, e.g. *0.3.0, build 3dacb6f*), and packages for **Windows 64-bit and for the computer that
>   builds it** (a build on a Mac also holds that Mac's; on Linux, Linux's), for Python 3.11, 3.12, 3.13 and 3.14,
>   with the `ocr` (OCR for scans), `dev` (pytest, for `--installer --full-tests`) and `azure` extras, and the OCR
>   and table-finder models. `--no-current` leaves the building computer's packages out.
> - **Why `azure`:** the packages of the optional *Azure check* (*Settings → Reading → Azure check*, off by
>   default), so turning it on needs no download. It sends nothing unless AP turns it on with your organisation's
>   own Azure endpoint and key. For a pilot without Azure, `--extras ocr dev` leaves them out; nothing else changes.
> - **Where it downloads from** (HTTPS, port 443): `pypi.org` and `files.pythonhosted.org` (the packages) and
>   `www.modelscope.cn` (the models). `--dry-run` prints these hosts and each model's full address, without
>   downloading anything.
> - **Not in the bundle:** LM Studio and its models (OvisOCR2, the chat model). IT installs them on the laptop
>   (*What you need*). Python is not in it either.

The laptop's Python must be one the bundle was built for (3.11 to 3.14 by default; `bundle_manifest.json`
lists them, and the launcher names them when it does not match; `--python 3.12` alone makes a smaller
bundle). One exception: the OCR engine (onnxruntime) is not published for Intel Macs on Python 3.14, so
there AP Coder installs without the OCR add-on (text PDFs are read, scans are not; Python 3.13 on that Mac
has it). The build says so, and `bundle_manifest.json` lists it under `without_ocr`.

**On a computer with internet** you can skip the bundle: unzip GitHub's ZIP (**Code → Download ZIP**) and
double-click `APProcessor.bat`. Like CloseDesk, its setup downloads the packages and OCR models once (and, with no
Python, offers Python 3.12 with `winget`); AP Coder itself stays offline. The first run says what it is doing
while pip works (*Installing packages: 23 of 56 (pandas)...*). A model download that fails (*the connection
dropped*) is tried 3 times, then again the next time AP Coder starts (up to 3 starts a day); scans are read with one
OCR engine meanwhile. `set AP_ALLOW_INTERNET=0` first forbids those downloads. A newer GitHub ZIP of the same
version is an update like any other: each ZIP carries its build (`ap_coder/BUILD`, filled in by GitHub).

**Check the install again** (a few seconds, nothing is saved): `APProcessor.bat --check` runs the
self-check before starting; for every sample's line, run `python scripts\pilot_check.py` in `scripts\terminal.bat`.
The same check runs on a clean Windows machine in CI for every change, from the
double-click (first start, a second double-click while it runs, a later start) and from the offline
bundle with the internet cut off, on Windows and on a Mac that has only Python 3.14.

## Daily use

1. Double-click **`APProcessor.bat`** (or the *AP Coder* desktop shortcut). A black window opens with
   the readiness summary, and the dashboard opens in your browser (at `http://127.0.0.1:8501`). The
   dashboard is a local web app that AP Coder serves itself, on this laptop only (as CloseDesk does):
   nothing is installed in the browser and nothing goes online. Keep the window open while you work;
   close it to stop AP Coder. Double-clicking again while it runs just brings the running dashboard back
   up in the browser; it never starts a second copy. Each page has its own address, so a page or an
   invoice (`/review/<number>`) can be bookmarked or opened in a second tab.
2. **Bring in invoices:** drop PDFs, scans or photos in the **invoices folder** (Import invoices →
   *Open folder*), or upload them on the *Import invoices* page.
3. **Review (the clerk's job):** open each invoice in the *Review queue*, check the fields marked
   *Check*, the GL split and the tax lines, correct what is wrong and click **Approve & teach** (or park /
   reject). Each corrected field is recorded for that vendor and updates its template; the confirmation
   says what was learned and how far the vendor is from touchless. *Learning & accuracy → Supplier
   learning* shows each vendor's training. Bulk approvals nobody opened don't count toward touchless.
4. **Export:** the pilot exports to Excel or CSV: *Exports* → pick the approved invoices → *Excel workbook*
   (the first time) or *CSV, one row per GL line* → download the batch and import it into the ERP. Exports opens
   on the format used last. **JD Edwards E1** (a ZIP of F0411Z1 / F0911Z1 CSVs to load with R04110ZA) is hidden
   for the pilot: *Settings → About → Show JD Edwards settings* shows its settings tab and export format again
   ([JDE_E1.md](JDE_E1.md)). With it on, each vendor needs its address number (AN8) before its invoices can be
   approved (Settings → JD Edwards E1 → *Supplier address numbers*, or the vendor master's ERP ID; *Add new
   vendor…* takes it too), and the export needs a *Default BU* or every account mapped, tax accounts included.

## Touchless processing: when to turn it on

Touchless processing is **off** by default: every invoice is approved by a person. Leave it off for the
first weeks of the pilot. Let the clerks review, correct and *Approve & teach*, and watch **Learning &
accuracy → Supplier learning**: it shows, vendor by vendor, how often a field was corrected and how far
each one is from the bar.

When several of your regular vendors are close to the bar and the corrections have settled, an AP lead
turns it on in **Settings → Automation** (with a confirmation; recorded in the Activity log with their
name). From then on, a vendor that meets the bar goes touchless by itself:

- at least 20 invoices reviewed by a person;
- header fields read right at least 99% of the time, with 95% confidence, since its last correction, and
  at least 98% over its last 200 invoices (with about 13 header fields an invoice, that usually takes about
  20 to 30 clean invoices; a correction AP made early on, once learned, no longer holds it back);
- the last 10 with no correction at all.

5% of touchless invoices still go to a person as an audit. One correction, or a touchless invoice
reopened, suspends the vendor until the invoices since show 99% again (about 21 without a correction). *Keep supervised* (Learning & accuracy)
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

## Updating AP Coder

When IT gives you a new offline bundle, or you download a new GitHub ZIP:

1. Unzip it **anywhere**: a new folder (e.g. `C:\APCoder\app-0.2`) is fine, and so is unzipping it over
   the old folder. AP Coder may still be open.
2. Double-click **`APProcessor.bat`** in the new folder (Mac: `APProcessor.command`).

That is all. The new version takes over:

- **The old one stops.** An AP Coder still running (from the old folder, or the old version in this one) is
  stopped first, so two versions never use your data at once. If it can't be stopped, the window says
  *Close the old AP Coder window, then start again* and waits up to a minute for you to do it.
- **It says what changed**: *Updated AP Coder from 0.2.0 to 0.3.0 (build 3dacb6f, 2026-10-21)*, also between two
  builds of one version (two GitHub ZIPs, or two bundles). It sets up only what is new (from the new bundle's
  `wheelhouse/`, never the internet) and runs the self-check. An older build unzipped over a newer one says
  *Replaced AP Coder X with an older build, Y*. The old window, if it was still open, simply ends when it is 0.2.0
  (this update); from 0.3.0 on it says *A newer AP Coder took over; you can close this window.*
- **Your data is kept.** The data folder (`C:\Users\<you>\APCoder`) is never replaced. Before the new version
  brings the database up to date, it copies it to the `backups` folder
  (`ap_coder-<date>-before-update-from-schema-<n>.db`). Data an older version kept inside its own folder
  (`private\`) and a `.env` left there move into the data folder (when the data folder already has data of
  its own, they go, whole, to `backups\from-<folder>-<date>`: nothing is merged). Settings that are no longer
  used are commented out in the `.env`, with the note *# no longer used (AP Coder 0.2)*.
- **One shortcut, one install.** The *AP Coder* desktop shortcut now opens the new version (on a Mac, an
  *AP Coder.command* on the desktop does). Once the self-check passed, the old version's `.venv`, OCR models
  and folder are removed, and the window says how much space that freed. An old folder with files of your
  own in it is kept, without its `.venv`. An old folder still open in a window is emptied of its `.venv` and
  models, and removed on a later start. Unzipped over the old folder, the files of the old version that the
  new one no longer has are removed (the bundle lists every file it holds); a `wheelhouse` you copied in
  yourself is kept.
- **Stopped halfway?** Closing the window (or a file in use) while the data moves to the data folder never
  leaves AP Coder on half of it: the next start finishes the move first.
- **The old folder by mistake?** Starting an older version (an old shortcut, an old folder) opens the
  newest one instead: older code never opens newer data. (A version from before the web app, 0.1, can't do
  that: the update removes its folder once it found it. [UPGRADING.md](UPGRADING.md) says, for every older
  version, how it was installed, what the update finds and what it does.) A database made by a newer AP Coder is refused in
  plain words, and nothing is changed.

If the new bundle doesn't fit the computer, it stops before installing anything and says which bundle to
use, e.g. *This package folder (wheelhouse) was built for Windows, Python 3.11/3.12; this computer is a Mac
with Python 3.14. Use the bundle built for Macs (build it with --mac): nothing was installed.* When the
bundle has packages for another Python that is installed on the computer, AP Coder uses that Python by
itself (and makes its `.venv` again with it).

## Troubleshooting

| What you see | What to do |
|---|---|
| *AP Coder needs Python 3.11 or newer (3.12 is best), and it is not installed* | IT installs Python 3.12 from python.org, with *Add python.exe to PATH* ticked; double-click again. |
| *Python 3.10 is installed, but AP Coder needs Python 3.11 or newer* | The laptop's Python is too old: IT installs Python 3.12 next to it (the older one is left alone); double-click again. |
| *Note: this offline bundle has packages for Python ..., not for Python 3.14* | The laptop's Python is newer than the bundle: IT builds a bundle for it (the note gives the `build_offline_bundle.py` command) or installs one of the versions listed. |
| Windows: *Do you want to run this file?* or *Windows protected your PC* on every double-click | The ZIP came from the internet. Click *Run* (SmartScreen: *More info* → *Run anyway*) once: AP Coder takes the mark off its own launchers when it starts. Next time, right-click the ZIP → *Properties* → *Unblock* before unzipping. |
| Mac: *"APProcessor.command" can't be opened* / *Apple could not verify ...* / *unidentified developer* | Right-click it → *Open* → *Open*. macOS 15 or newer: click *Done*, then System Settings → Privacy & Security → *Open Anyway*. Or in Terminal: `xattr -dr com.apple.quarantine` and the AP Coder folder (drag it into the window). |
| Mac: *could not be executed because you do not have appropriate access privileges* | The bundle lost the launcher's executable bit (unzipped with another tool, or an older bundle built on Windows). In Terminal: `chmod +x` and drag `APProcessor.command` into the window; or run `bash` followed by the file. |
| *AP Coder can't find its files next to APProcessor.bat* (Mac: *next to this launcher*), or *AP Coder was started from inside its ZIP* | The ZIP was opened without unzipping it, or the launcher was copied out of its folder. Right-click the ZIP → *Extract All* (Mac: double-click the ZIP), then open APProcessor in the extracted folder; use the *AP Coder* shortcut on the desktop rather than a copy of the launcher. Mac: if macOS asked whether Terminal may access Downloads, Desktop or Documents, allow it (System Settings → Privacy & Security → Files and Folders → Terminal). |
| *AP Coder stopped. The message above says what to do.* | The `What to do:` line above it says it; most often a package could not install. *Missing packages: ...*: the folder has no `wheelhouse/` (unzip the full offline bundle over it). *The offline bundle's wheelhouse folder has no ...*: the bundle was built for another Python version. Developers online (`AP_ALLOW_INTERNET=1`): check the proxy (`set HTTPS_PROXY=http://proxy:8080`). |
| *This package folder (wheelhouse) was built for ...: nothing was installed* | The bundle was built for another kind of computer or Python: use the bundle built for this one (the message says which, e.g. `--mac`), or install the Python it names. |
| *Installing the packages stopped: the disk is full* | Free about 1.5 GB on that disk; the message names the older AP Coder's `.venv` folder, which can be deleted (it holds only packages, never your data). Double-click again. |
| *Installing the packages stopped: the AP Coder folder's path is too long for Windows* | Windows allows 260 characters in a path. Unzip the bundle to a short folder such as `C:\APCoder` (not deep in Downloads) and double-click `APProcessor.bat` there; or IT turns on long paths (`LongPathsEnabled`). |
| Linux: *Could not create the .venv folder* ... *sudo apt install python3.12-venv* | Debian and Ubuntu keep Python's venv support in a separate package: install the one the message names, delete the `.venv` folder, run `./APProcessor.command` again. |
| *AP Coder's folder is synced by OneDrive* (or iCloud Drive, Dropbox, Google Drive) / *The data folder is synced by ...* | Files there can be kept online-only, and the laptop is offline. Unzip AP Coder to a folder that is not synced (`C:\APCoder\app`; Mac: a folder in your home folder, not Desktop or Documents when iCloud syncs them) and start it from there; the data folder is chosen with `APProcessor.bat --installer`. |
| Mac: *... is an Intel build running through Rosetta on an Apple silicon Mac* | Install Python 3.12 from python.org (it runs natively), delete the `.venv` folder and open APProcessor.command again. If Terminal is set to *Open using Rosetta* (Finder → Applications → Utilities → Terminal → Get Info), untick it. |
| *Installing the packages stopped: Windows refused a file in .venv because another program has it in use* | Close every AP Coder window, wait a minute (the antivirus checks new files) and double-click again; it carries on where it stopped. If it keeps happening, IT lets the antivirus allow the `.venv` folder. |
| *Your data could not all be moved from ... to ...* | A file of it is open (an old AP Coder window, Excel): close it and double-click again; the move picks up where it stopped. Nothing was lost. |
| *... is still running: stopping it, so this version takes over* | An older AP Coder was open: the new one stops it and carries on. *Close the old AP Coder window, then start again*: it could not be stopped; close that window (it waits a minute for you). |
| *A newer AP Coder ... is in ...: opening that one* | An old shortcut or folder was started: the newest version opens instead. Use its folder (the shortcut already does). |
| *Your database ... was made by a newer AP Coder* | An older version was started on newer data: nothing was changed. Start the newer AP Coder. |
| *AP Coder is being set up or started in another window* | The first double-click is still setting up: it waits, then opens the dashboard. |
| *Self-check FAILED* in the summary | AP Coder still runs. Run `python scripts\pilot_check.py` in `scripts\terminal.bat` and send the output. |
| *AP Coder is already running* | It is: the browser opens on it. Use that window, or close it to stop AP Coder. |
| The browser does not open (*Could not open a web browser here*) | Open `http://127.0.0.1:8501` yourself (the window shows the exact address; it uses the next free port if 8501 is taken). Use `127.0.0.1`, not `localhost`: AP Coder listens on this computer's own address only, so Windows never asks about the firewall. |
| *Port 8501 is in use by another program: AP Coder uses port 8502* | Nothing to do: another program has port 8501, so AP Coder took the next free one; open the address the window shows. |
| Scans are read, but with a note about one OCR engine | The PP-OCRv5 models are missing or damaged. Unzip the full offline bundle (with its `models/` folder) over the AP Coder folder and double-click again, or run `.venv\Scripts\python.exe scripts\fetch_models.py` (Mac: `.venv/bin/python scripts/fetch_models.py`): it copies them from `models/`. |
| A banner: *LM Studio isn't running* or *OvisOCR2 isn't running in LM Studio* | Open LM Studio, **Developer** tab → **Start server**; check that OvisOCR2 is in its models (ask IT if not). Invoices are still read by OCR and wait for a person meanwhile. |
| A banner: *OvisOCR2 is testing itself* | Nothing to do: its self-test runs on its own (10 to 20 minutes the first time). *Settings → Reading* shows its progress. |
| A banner: *OvisOCR2 failed its self-test* | *Settings → Reading* shows what it read on the test invoice. Check that it is the Q8_0 build from IT, then *Run the self-test again*. |
| Something else | In `scripts\terminal.bat`, run `python -m ap_coder doctor` (Mac: `.venv/bin/python -m ap_coder doctor` in Terminal, in AP Coder's folder) and send the report: it lists checks only, never keys, file names or invoice data. |
| Start over | Delete the `.venv` folder and double-click `APProcessor.bat` (your data is kept; it is set up again, once). |

## What is sent over the network

**Nothing**, in the offline build, unless someone switches on one of the exceptions listed at the end of this
section (the Azure check, the finance hub feed, the JD Edwards connection or server mode; all off by default):

- The setup never connects to the internet: packages come only from the bundle's `wheelhouse/`
  (`pip --no-index`), the OCR models only from its `models/` (RapidOCR is given their paths, so its own
  downloader never runs, and a damaged model file counts as missing instead of being fetched again), the
  installer runs no `git fetch` / `git pull`, and APProcessor.bat never installs Python with `winget`.
- The dashboard listens only on this laptop (`127.0.0.1`; or on a company server: [SERVER.md](SERVER.md));
  other computers cannot open it. To share it
  on the office network on purpose, set `AP_DASHBOARD_ADDRESS=0.0.0.0` before starting. A colleague
  opening it from another computer is asked "Who are you?" once: their approvals, rejections and other
  changes are then recorded in their name, with their computer's address, so they can give the second
  approval of an invoice someone else approved first. After AP Coder restarts, they give their name again.
- The dashboard is AP Coder's own small web server (FastAPI): no usage statistics, no outside service;
  fonts, icons and scripts come from the app, not the internet. It answers only requests addressed to this
  laptop and changes nothing for a page that is not its own.
- OCR runs on the laptop with local models; OvisOCR2 and the chat model run in LM Studio on the
  laptop too and are reached over `localhost`.
- A test (`tests/offline/test_offline.py`) reads an invoice, runs every dashboard page and builds a JD Edwards
  batch with all outside connections blocked, and fails if anything tries to connect.

The only outside connections are the ones someone chooses:

- **The setup from GitHub's ZIP** (the code alone, no offline bundle): APProcessor.bat downloads the missing
  packages from PyPI and the OCR models from www.modelscope.cn, once, and offers Python 3.12 with `winget`. The
  dashboard it then starts stays offline. An offline bundle never does this; `AP_ALLOW_INTERNET=0` forbids it.
- **`AP_ALLOW_INTERNET=1`**, the one opt-in, for developers and the bundle builder only. With it set in the
  terminal that runs APProcessor.bat, the installer also checks GitHub for updates, and the app may use a model
  server or Azure on the internet. `scripts/build_offline_bundle.py` sets it for itself, on IT's computer. Never
  set it on a pilot laptop.
- **The Azure check**, its own opt-in switch in Settings → Reading (off by default, no `AP_ALLOW_INTERNET`
  needed): once it is switched on with your organisation's Azure endpoint and key, the invoices AP Coder isn't
  sure of, and a small share of the others (capped per day), are sent to that one Azure resource for a second
  opinion. Azure as the reader or the coder is developer-only, on a computer
  with `AP_ALLOW_INTERNET=1`; the offline build ignores those Azure settings.
- **The finance hub feed** (off by default, Settings → Finance hub): it writes tables to a shared folder you
  choose, or to OneLake only with `AP_ALLOW_INTERNET=1`. See [FINANCE_HUB.md](FINANCE_HUB.md).
- **The JD Edwards AIS connection** (off by default, Settings → JD Edwards E1): it talks to your company's own
  JD Edwards server on the internal network, a named internal system (D-104). See [JDE_E1.md](JDE_E1.md).
- **Server mode** (off by default): the dashboard then listens on the office network, over HTTPS with logins.
  See [SERVER.md](SERVER.md).
