# Getting started: running the prototype on your enterprise data

**How we work together.** You run everything on your own computer, offline: every invoice is read
on the laptop (the PDF's text or local OCR, and OvisOCR2 in LM Studio). Invoices, GL accounts, reviews
and the AI's memory stay in your **data folder**
(default `C:\Users\<you>\APCoder`, chosen during install), outside the code, so none of it can
be pushed to GitHub. The dashboard runs at
`http://localhost` and is not reachable from other machines. You only send back two text
reports, and you can read both before sending:

| You paste back | Produced by | Contains | Never contains |
|---|---|---|---|
| **Doctor report** | `python -m ap_coder doctor --online` | pass/fail of setup, package versions, row counts and column *names*, tax setup status | keys, endpoint URLs, codes, account names |
| **Share report** | `python -m ap_coder share-report` | counts, confidence buckets, issue codes, accuracy %, token usage | vendor names, amounts, descriptions, file names, invoice numbers |

Invoices appear in the share report as `doc-01`, `doc-02`, … The file
`share_report_key.csv` in your data folder maps them back to file names and stays on your machine. If I ask
about `doc-07`, you can look it up there.

---

## Step 1: LM Studio with OvisOCR2 (IT, one-off)

Every invoice is read the same way, with nothing to choose. Part of that is **OvisOCR2**, a small
page-reading model (about 1 GB) that reads every page of every invoice as a second reader. It runs in
[LM Studio](https://lmstudio.ai/) on the laptop.

1. **IT installs LM Studio** (0.4.8 or newer) with OvisOCR2 already in it, and optionally a chat model
   (**Qwen 3.5 9B**, Q4_K_M) that suggests GL accounts for lines nothing was learned for. Nothing is
   downloaded on the laptop. The files and sizes are in [PILOT.md](PILOT.md#what-you-need).
2. **Start the server:** open LM Studio, **Developer** tab → **Start server**.
3. **That's all.** When AP Coder starts, it finds OvisOCR2 on its own and runs its self-test by itself (it
   reads a sample invoice whose answers are known): 10 to 20 minutes on a laptop the first time.
   **Settings → Reading** shows its progress. There is no model to pick and nothing to link.

Without LM Studio, AP Coder still works: invoices are read by the PDF's text or OCR, and wait for a
person. A banner on the Process and Review pages says so.

## Step 2: Install (~10 min, one double-click)

AP Coder is an offline enterprise build: the laptop never connects to the internet, not even to set up
(see [PILOT.md](PILOT.md)). IT gives you three things:

- **Python 3.11, 3.12 or 3.13**, already installed. AP Coder uses the Python you have and never installs
  a second one. If there is none, ask IT to install Python 3.12 from python.org, with **"Add python.exe
  to PATH"** ticked on the first screen.
- **The offline bundle**, a ZIP such as `APProcessor-offline-<version>-<date>.zip` (on a USB stick or a
  share). It holds the code, every package (`wheelhouse/`) and the OCR models (`models/`).
- **LM Studio**, with OvisOCR2 (and, optionally, the chat model) already in it (Step 1).

**Get the code.** Unzip the bundle to a folder that is **not** synced by OneDrive, e.g.
`C:\APCoder\app`.

**Install and start: one button.** Open that folder and **double-click `APProcessor.bat`** (macOS:
`APProcessor.command`; the first time right-click → *Open*). That is the only file you need, now and
every day after. The first time (a few minutes) it:

1. finds your Python 3.11–3.13 and makes AP Coder's own environment (`.venv`), once
2. installs the packages from the bundle's `wheelhouse/` folder, then on later starts only ones that
   are missing or too old
3. records the data folder, `C:\Users\<you>\APCoder`: outside the code folder and outside OneDrive.
   The database, invoices, outputs and your settings live there, so **every future version uses the
   same data**
4. copies the OCR models for scanned invoices from the bundle's `models/` folder, and puts one
   **AP Coder** shortcut on the desktop
5. runs the self-check (the ten sample invoices, about 10 seconds) and prints a readiness summary:
   Python, packages, OCR, data folder, LM Studio, the page reader (OvisOCR2: found, and its self-test)
   and the self-check's OK/FAILED
6. opens the dashboard in your browser

Later double-clicks reuse all of it and start in seconds; double-clicking while AP Coder already runs
just opens it in the browser again.

**For developers (online).** On a computer with internet you can work from a git checkout instead of
the bundle. Set `AP_ALLOW_INTERNET=1` in a terminal (`set AP_ALLOW_INTERNET=1`), then run
`APProcessor.bat` from it. Packages then come from PyPI and the OCR models are downloaded;
`install.bat` updates the checkout with git; with no Python, APProcessor.bat offers to install 3.12
with `winget`. Never set it on a pilot laptop.

**The other installer options.** **`install.bat`** (macOS/Linux: `./install.sh`) is the same setup as
`APProcessor.bat` (it runs it), plus a question for the data folder and the desktop shortcut, then a
self-test and the setup check. It asks nothing about reading invoices or cloud services: the offline
build has nothing to set up. (Only a developer computer with `AP_ALLOW_INTERNET=1` is asked for Azure
details.)

**Running either again is always safe.** Both install packages only when something is missing (from
the bundle), and keep your data folder, settings and shortcut. Nothing is duplicated. Options are typed after the name in **`terminal.bat`** (double-click
it, then type e.g. `install.bat --fresh-start`):

| Option | What it does |
|---|---|
| `--fresh-start` | moves the database, invoices and outputs into a dated `backup-…` folder inside the data folder (the `.env` settings are kept) |
| `--yes` | no questions; keeps current answers |
| `--no-update` | developers online only: don't check GitHub for a newer version (the offline build never does) |
| `--data-dir <folder>` | use a different data folder |
| `--no-shortcut` / `--no-start` | no desktop shortcut / don't offer to start the dashboard at the end |
| `--skip-tests` / `--reinstall` | skip the self-test / reinstall AP Coder itself even if unchanged |
| `APProcessor.bat --check` | run the sample-invoice self-check again before starting |

To run AP Coder commands yourself (`doctor`, `share-report`, …), double-click **`terminal.bat`**:
it opens a command prompt with everything ready, e.g. `python -m ap_coder doctor`.

## Step 3: Check the setup → paste the doctor report

In `terminal.bat`:

```bat
python -m ap_coder doctor --online
```

It checks the setup, the reference data and LM Studio: whether OvisOCR2 was found and passed its
self-test (*page reader*), and, with the chat model loaded, one short test call on a tiny made-up invoice
(on this laptop; nothing leaves it). **Paste the whole output into the chat.** Any FAIL line says why.
**Settings → Reading** in the dashboard shows the same, reader by reader.

## Step 4: Try the dashboard with the sample data (no enterprise data)

Start AP Coder from the **AP Coder** desktop shortcut (or `APProcessor.bat`). It opens in your browser;
keep the black window open while you use it and close it to stop. If another program already uses
port 8501, AP Coder picks the next free one.

**Fastest: the demo.** On the welcome screen click **Load demo invoices**. Ten
sample invoices from across Canada arrive as if the AI had read them (eight to review, two already
approved), with a few realistic mistakes to correct, plus sample purchase orders and a vendor list.
Things to try:

- **Review queue** → open *Red River*: the **PO match** shows chairs billed but not yet received,
  and *Use the PO's coding* fixes the desk's GL account in one click.
- Open *Harbourview*: line 4 has no GL account; **Suggested GL accounts** offers one.
- Type `2/10 Net 30` in an invoice's *Payment terms*: the due date and the early-payment discount
  deadline update (a discount shows while its deadline is still ahead).
- *Split a line…* under the line grid divides a shared cost across cost centers.
- On an invoice with something to ask (e.g. *Red River*), open **Ask the vendor** under the checks:
  the email is written for you (English, or French for a Quebec vendor).
- **Find an invoice**: type `northwind 0912` or an amount such as `18,017.85`.
- The **Today** line above the queue lists what is past due, parked for follow-up and ready to export.
- Approve a few, then look at **Exports** (export a batch, then *Approved PDFs*), **Learning &
  accuracy**, **Insights**, **Spend** and **Activity** (*Run the audit* for possible duplicate payments).
- **GL accounts & tax** → **Fixed rules**: add a rule such as *line contains "delivery" → 6800*; open
  an invoice and *Apply the coding rules*.
- **Vendor statements** → pick Northwind and upload `data\sample_statement_northwind.csv`, then open
  *Ask the vendor for the missing invoices*.
- **Month-end** shows what to accrue; **Sales tax** totals the GST/HST and QST to claim back (set
  the dates to include the demo invoices, dated mid-2026); **Help** explains every check.
- *Remove demo invoices* on the **Process invoices** page removes the demo invoices (and any batch
  exported from them), sample POs and the sample vendor list again. The sample GL accounts and tax
  setup stay (replace them when you import yours: tick *Replace my current list*).

**Reading the samples for real:** first click *Remove demo invoices* if you loaded the demo (the same
sample files would otherwise be recognised as already processed).

1. **GL accounts & tax** → *Load sample setup* (skip this if you tried the demo: the sample setup is
   already loaded).
2. Copy the sample PDFs from `samples\` into your invoices folder (**Process invoices** →
   *Open folder* shows it). `samples\README.md` lists what each one shows: HST, GST+PST,
   TPS/TVQ, GST only, a US invoice and a credit note.
3. **Process invoices** → *Process 10 file(s)* (or copy only a few to start).
4. **Review queue** → open each invoice. Change a GL code in the grid and watch the checks and
   the GL distribution update. Approve, and look at **Learning & accuracy**.

These invoices are synthetic, so screenshots of this step are fine to share.

When you're done, close the AP Coder window, double-click **`terminal.bat`** and type
**`install.bat --fresh-start`** so your real setup starts clean: the sample database, memory and invoices move to a backup folder, and your
settings stay.

## Step 5: Your setup, in the dashboard

Open **GL accounts & tax**:

1. **GL accounts (cost codes):** *Import from CSV or Excel*. Pick which column holds the code,
   which holds the description, and (optionally) a category column. You can also type categories
   in afterwards, delete codes you don't want AP to use, and download the list. Include your
   sales-tax receivable accounts (e.g. *GST/HST Recoverable*, *QST Recoverable*).
   Good descriptions matter: the AI matches invoice lines against them.
2. **Cost centers:** optional. Leave empty if you don't code to cost centers.
3. **Sales tax setup:**
   - GST, HST and QST: *Recoverable*, and pick the receivable GL account for each.
   - PST: *add to each expense line's GL*. Alternatively, choose a separate PST expense account.
4. **Coding policy:** plain-English rules, one per line, e.g. "Laptops under $2,500 go to 6010".
   This is your fastest tuning lever.
5. **Fixed rules:** lines that must always go to one account (a vendor, words in the line, or both),
   whatever the AI says. Vendors your team always coded the same way are suggested.

Optional, any time later:

- **Vendor master:** on **Vendors**, import the ERP's vendor list. Exports then carry vendor IDs, and
  an invoice from a vendor that is not in the list is flagged.
- **Past coding:** on **Learning & accuracy**, *Teach from past coding* with last year's posted AP
  lines (vendor, description, GL, cost center) so the AI starts with your history.
- **Purchase orders:** import your open POs (one row per PO line, from the ERP; the *Template*
  button shows the columns). Include the quantity received to get three-way matching. Re-import
  the export regularly; a PO in the file replaces its earlier lines.
- **Exports → Custom layout for your ERP:** if your ERP's import needs its own columns.
- **Exports → ERP invoice register:** the ERP's list of AP invoices (last 12 to 18 months), so a bill
  already entered there is caught, and the duplicate payment audit covers it.
- **Settings → Review:** your name (shown on approvals), the confidence threshold, an **approval
  limit** above which a second person must approve, the days to pay when an invoice shows no
  terms, and exchange rates to CAD if you are billed in other currencies.
- **Settings → Data & backups:** a **second backup folder** (OneDrive or network) for the daily backups.

Then run `python -m ap_coder doctor` in `terminal.bat` (offline is fine) and **paste the output**. From it I can see
row counts, whether every tax type is mapped, and the prompt size, but none of your codes or
names.

## Step 6: Process a test set

Put **20–50 real invoices** in your invoices folder (**Process invoices** → *Open folder*), or
upload them on **Process invoices**. Invoices that came by email: save the email as `.eml` (Outlook
on the web or new Outlook: *Download*) into the folder, or save the PDF attachment itself.
Aim for a representative mix:

- your top vendors by volume, and the ones AP often recodes
- several provinces: Ontario/Atlantic (HST), Quebec (GST + QST, French invoices), BC/SK/MB
  (GST + PST), Alberta/territories (GST only)
- out-of-province vendors (these often don't charge PST/QST, which may require self-assessment)
- multi-page invoices, scans/TIFFs, phone photos (iPhone HEIC too), credit notes, freight lines,
  discounts

Then click *Process*. Each invoice lands in the **Review queue**.

## Step 7: Review in the dashboard (AP team)

For each invoice:

1. Compare the image with the extracted header, lines and tax lines. A field the readers agree on is
   *verified*; one they read differently is marked *Check* and boxed in amber on the page.
2. Read **Checks**. Red items are errors, for example:
   - tax math wrong
   - QST charged on a GST-inclusive amount
   - totals don't add up
   - possible duplicate invoice

   Yellow items are warnings, for example a missing GST/HST number, PST not charged or a rate that
   differs from the official one for the province.
   Each check says what to do; **Help** lists them all.
3. Fix anything wrong in the grids. GL and cost-center cells are dropdowns of your own codes. Lines
   without a GL account get **suggestions**; an invoice quoting a PO shows the **PO match**. When
   the vendor has to fix something, **Ask the vendor** under the checks writes the email.
4. Click **Approve & teach** (Ctrl+Enter). Each field you corrected is recorded for that vendor and
   updates its template; the confirmation says what was learned and how far the vendor is from touchless
   (e.g. *Learned from your 2 corrections (invoice number, due date). Acme Ltd: 14 invoices reviewed, about
   6 more clean invoices to go touchless.*). Invoices nobody needs to look at can be approved together with
   *Approve N clean…* above the queue; those approvals don't count toward touchless.
5. Approved invoices wait in **Exports**: export them as a batch for the ERP (Excel, CSV or your
   custom layout), with the **Approved PDFs** to attach if your ERP keeps invoice images.

Every approved line is stored as a lesson: *confirmed* if the AI was right, *corrected* if you
changed it. The next invoice from that vendor sees those lessons, and **Learning & accuracy**
tracks how often the AI is right. A mistaken lesson can be removed with *Forget*. **Learning &
accuracy → Supplier learning** is the training view, vendor by vendor: how often each field was corrected
and how far each vendor is from touchless.

**Touchless processing** (Settings → Automation) is off by default, so every invoice is approved by a
person. Leave it off for the first weeks and watch *Supplier learning*. When your regular vendors are
close to the bar, a manager turns it on (with a confirmation, recorded in the Activity log). A vendor then
goes touchless by itself once it has at least 20 reviewed invoices, header fields read right at least 99%
of the time with 95% confidence, and the last 10 without a correction: usually about 30 clean invoices.
5% are still audited, and one correction suspends the vendor. Some invoices always go to a person (a
changed bank account or GST/HST number, a possible duplicate, a total above *Largest invoice approved
without a person*, 5,000.00 CAD by default, and others: Settings → Automation lists them). See
[PILOT.md](PILOT.md#touchless-processing-when-to-turn-it-on).

## Step 8: Send me the share report

In `terminal.bat`:

```bat
python -m ap_coder share-report --include-codes
```

**Paste it.** It includes the review statistics and which GL codes get corrected to which
(e.g. `6000 -> 6010 x4`). That is what lets me improve the prompts and checks. Drop
`--include-codes` if account codes may not leave the building; the percentages still come
through.

## Step 9: Iterate

When there are improvements, IT gives you a new offline bundle. Close AP Coder, unzip the new bundle
**over** the same folder (replace files), then double-click `APProcessor.bat`. It installs only the
packages that changed, from the new bundle, and keeps everything else. Your data folder (database,
memory, GL accounts and settings) is not touched by updates: keep it where it is. We repeat until the AI's coding
accuracy on the **Learning & accuracy** page holds at or above **90%**. That is the gate for
Phase 2.

---

### If I need to see a specific problem

Describe it in words, e.g. "doc-07: the freight line was merged into the line above" or "QST
flagged as wrong on a Quebec invoice that looks correct". If your policy allows, paste a
**hand-redacted** snippet of the *Extracted text* panel, with names and numbers changed. I'll
recreate the problem as a synthetic sample in `samples/` and test the fix against it.

### Command cheat sheet

Run these in `terminal.bat` as `python -m ap_coder <command>`.

| Command | What it does |
|---|---|
| `dashboard` | the review app (what the desktop shortcut and `APProcessor.bat` start) |
| `doctor [--online]` | setup check; safe to paste |
| `share-report [--include-codes]` | redacted summary; safe to paste |
| `demo [--remove]` | load or remove the demo invoices |
| `watch [--every 60]` | keep processing new files dropped in the invoices folder; with `--once` it checks once (for Windows Task Scheduler: program `<AP Coder folder>\.venv\Scripts\python.exe`, arguments `-m ap_coder watch --once`, start in the AP Coder folder) |
| `process <files/folders>` | batch processing without the dashboard; results also appear in the review queue. Files already processed are skipped (`--force` to redo), so it is safe to run again after Ctrl+C |
| `labels` / `evaluate` | spreadsheet-based labelling and scoring (an alternative to reviewing in the dashboard) |
| `read-pages [--minutes 240]` | have OvisOCR2 read the invoices waiting for it, then stop (Task Scheduler overnight) |
| `schema` | the exact JSON Schema sent to Azure OpenAI (developers) |

### Two people approving (second approval)

The second approval needs both people to use the same AP Coder database. The simplest set-up is one
AP PC that both sign in to with their own Windows accounts: put the code in a folder both can open
(e.g. `C:\APCoder\app`) and have each person run `install.bat --data-dir C:\APCoder\data` once (in
`terminal.bat`). Each person sets *Your name* in Settings → Review; it is kept per Windows account.
Keep the data folder on a local disk: the database must not live on a network share. To protect it
against a lost or broken PC, set *Also copy each backup to* in Settings → Data & backups to a OneDrive or
network folder: the daily backup copies go there (that is safe; only the live database must stay local).
