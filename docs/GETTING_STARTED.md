# Getting started: running the prototype on your enterprise data

**How we work together.** You run everything on your own computer against your own Azure
resources. Invoices, GL accounts, reviews and the AI's memory stay in your **data folder**
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

## Step 1: Azure resources (one-off, ~30 min)

In the Azure portal, in a Canadian region if invoices must stay in Canada:

1. **Document Intelligence** resource, tier **S0**. The free F0 tier only reads the first 2 pages
   of each document, which breaks multi-page invoices.
2. **Azure OpenAI** resource (or Azure AI Foundry project). Deploy **gpt-4o**, model version
   `2024-08-06` or later, which is required for Structured Outputs. A `gpt-4o-mini` deployment
   is useful later for cost comparison.
   - Deployment type: for data residency use **Standard** (regional) or **Data Zone**, not *Global*.
   - Give it at least ~50K tokens/minute of quota so a batch does not stall.
3. **Access:** copy the keys from each resource's *Keys and Endpoint* blade, or (preferred in
   enterprises) leave the keys empty and grant your user **Cognitive Services User** (Document
   Intelligence) and **Cognitive Services OpenAI User** (Azure OpenAI), then run `az login`.

Your compliance team may want to review Azure OpenAI's data, privacy and abuse-monitoring terms
before real invoices are sent.

## Step 2: Install (~10 min, one double-click)

You need **Python 3.10 or newer** (3.12 recommended) and, ideally, **git**. If either is missing,
install it once from a terminal (or from python.org / git-scm.com):

```bat
winget install Python.Python.3.12
winget install Git.Git
```

When installing Python from python.org, tick **"Add python.exe to PATH"** on the first screen.

**Get the code** into your Downloads folder, either way:

- **With git (recommended: updates are one click).** Open *Command Prompt* and run:

  ```bat
  cd %USERPROFILE%\Downloads
  git clone --branch claude/epic-feynman-r6ns86 https://github.com/MNabikhel/AP_Processer.git
  ```

- **Without git.** On GitHub, open the `claude/epic-feynman-r6ns86` branch, *Code → Download ZIP*,
  and extract it to `Downloads\AP_Processer`. For a later version, extract the new ZIP **over the
  same folder** (replace files) so nothing is duplicated.

**Install:** open `Downloads\AP_Processer` and **double-click `install.bat`** (macOS/Linux:
`./install.sh`). It:

1. finds a suitable Python and creates a private environment (`.venv`) for AP Coder
2. installs the packages (a few minutes the first time)
3. asks where to keep your data. The default is `C:\Users\<you>\APCoder`, outside the code
   folder and outside OneDrive. The database, invoices, outputs and your Azure keys live there, so
   **every future version uses the same data**
4. asks for your Azure details (endpoints, keys, deployment name, your name). Press Enter to
   keep a value shown in brackets. Leave the keys empty if you use `az login`
5. offers a desktop shortcut **AP Coder**
6. runs a self-test and the setup check, and offers to start the dashboard

**Running it again is always safe.** It updates the code in place (with git), reinstalls
packages only when they changed, and keeps your data folder, Azure settings and shortcut. Nothing
is duplicated. Useful options (e.g. `install.bat --fresh-start`):

| Option | What it does |
|---|---|
| `--fresh-start` | moves the database, invoices and outputs into a dated `backup-…` folder inside the data folder (Azure settings are kept) |
| `--yes` | no questions; keeps current answers |
| `--no-update` | don't check GitHub for a newer version |
| `--data-dir <folder>` | use a different data folder |

To run AP Coder commands yourself (`doctor`, `share-report`, …), double-click **`terminal.bat`**:
it opens a command prompt with everything ready, e.g. `python -m ap_coder doctor`.

## Step 3: Check the setup → paste the doctor report

At the end of the install, answer **y** to *Test the connection to Azure now?*. Or, in
`terminal.bat`:

```bat
python -m ap_coder doctor --online
```

`--online` makes one real call to each service with a tiny made-up invoice (about the cost of one
invoice). **Paste the whole output into the chat.** Any FAIL line says why. To fix a setting, run
`install.bat` again and answer **y** to *Change any Azure settings?*.

## Step 4: Try the dashboard with the sample data (no enterprise data)

Start AP Coder from the **AP Coder** desktop shortcut (or `start.bat`). It opens in your browser;
keep the black window open while you use it and close it to stop. If another program already uses
port 8501, AP Coder picks the next free one.

1. **GL accounts & tax** → *Load sample setup*.
2. Copy the sample PDFs from `samples\` into your invoices folder (**Process invoices** →
   *Open folder* shows it). `samples\README.md` lists what each one shows: HST, GST+PST,
   TPS/TVQ, GST only, a US invoice and a credit note.
3. **Process invoices** → *Process 10 file(s)* (or copy only a few to start).
4. **Review queue** → open each invoice. Change a GL code in the grid and watch the checks and
   the GL distribution update. Approve, and look at **Learning & accuracy**.

These invoices are synthetic, so screenshots of this step are fine to share.

When you're done, close the AP Coder window and run **`install.bat --fresh-start`** so your real
setup starts clean: the sample database, memory and invoices move to a backup folder, and your
Azure settings stay.

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

Then run `python -m ap_coder doctor` in `terminal.bat` (offline is fine) and **paste the output**. From it I can see
row counts, whether every tax type is mapped, and the prompt size, but none of your codes or
names.

## Step 6: Process a test set

Put **20–50 real invoices** in your invoices folder (**Process invoices** → *Open folder*), or
upload them on **Process invoices**.
Aim for a representative mix:

- your top vendors by volume, and the ones AP often recodes
- several provinces: Ontario/Atlantic (HST), Quebec (GST + QST, French invoices), BC/SK/MB
  (GST + PST), Alberta/territories (GST only)
- out-of-province vendors (these often don't charge PST/QST, which may require self-assessment)
- multi-page invoices, scans/TIFFs, credit notes, freight lines, discounts

Then click *Process*. Each invoice lands in the **Review queue**.

## Step 7: Review in the dashboard (AP team)

For each invoice:

1. Compare the image with the extracted header, lines and tax lines.
2. Read **Checks**. Red items are errors, for example:
   - tax math wrong
   - wrong rate for the province
   - QST charged on a GST-inclusive amount
   - totals don't add up
   - possible duplicate invoice

   Yellow items are warnings, for example a missing GST/HST number or PST not charged.
3. Fix anything wrong in the grids. GL and cost-center cells are dropdowns of your own codes.
4. Click **Approve & teach the AI**.

Every approved line is stored as a lesson: *confirmed* if the AI was right, *corrected* if you
changed it. The next invoice from that vendor sees those lessons, and **Learning & accuracy**
tracks how often the AI is right. A mistaken lesson can be removed with *Forget*.

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

When I push improvements, close AP Coder and **double-click `install.bat` again** (without git:
extract the new ZIP over the same folder first). It pulls the new version, updates packages if
needed and keeps everything else. Your database, memory, GL accounts and Azure settings are
untouched by updates. We repeat until the AI's coding
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
| `dashboard` | the review app (what the desktop shortcut and `start.bat` run) |
| `doctor [--online]` | setup check; safe to paste |
| `share-report [--include-codes]` | redacted summary; safe to paste |
| `process <files/folders>` | batch processing without the dashboard; results also appear in the review queue. Files already processed are skipped (`--force` to redo), so it is safe to run again after Ctrl+C |
| `labels` / `evaluate` | spreadsheet-based labelling and scoring (an alternative to reviewing in the dashboard) |
| `schema` | the exact JSON Schema sent to Azure OpenAI |
