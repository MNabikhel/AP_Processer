# Getting started: running the PoC on your enterprise data

**How we work together.** You run everything on your own machine against your own Azure
resources. Your invoices, Chart of Accounts and results stay in the `private/` folder, which git
ignores, so none of it can be pushed to GitHub. You only send back two text reports, and you can
read both before sending:

| You paste back | Produced by | Contains | Never contains |
|---|---|---|---|
| **Doctor report** | `python -m ap_coder doctor --online` | pass/fail of setup, package versions, row counts and column *names* of your reference files | keys, endpoint URLs, codes, account names |
| **Share report** | `python -m ap_coder share-report` | counts, confidence buckets, issue codes, accuracy %, token usage | vendor names, amounts, descriptions, file names, invoice numbers |

Invoices appear in the share report as `doc-01`, `doc-02`, … The file
`private/share_report_key.csv` maps them back to file names and stays on your machine. If I ask
about `doc-07`, you can look it up there.

---

## Step 1: Azure resources (one-off, ~30 min)

In the Azure portal, in the same region/geography as your data-residency requirements:

1. **Document Intelligence** resource, tier **S0**. The free F0 tier only reads the first 2 pages
   of each document, which breaks multi-page invoices.
2. **Azure OpenAI** resource (or Azure AI Foundry project). Deploy **gpt-4o**, model version
   `2024-08-06` or later, which is required for Structured Outputs. A `gpt-4o-mini` deployment
   is useful later for cost comparison.
   - Deployment type: if invoices must stay in a region, use **Standard** (regional) or **Data
     Zone**, not *Global*.
   - Give it at least ~50K tokens/minute of quota so a test batch does not stall.
3. **Access:** either copy the keys from each resource's *Keys and Endpoint* blade, or (preferred
   in enterprises) leave the keys empty and grant your user the roles **Cognitive Services User**
   (Document Intelligence) and **Cognitive Services OpenAI User** (Azure OpenAI), then run
   `az login`.

Your compliance team may want to review Azure OpenAI's data, privacy and abuse-monitoring terms
before real invoices are sent.

## Step 2: Local setup (~10 min)

Requires Python 3.10+ and git.

```bash
git clone https://github.com/MNabikhel/AP_Processer.git
cd AP_Processer
git checkout claude/epic-feynman-r6ns86

python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest -q                            # should end with "passed"

cp .env.example .env                 # Windows: copy .env.example .env
```

Edit `.env` and fill in the two endpoints, the deployment name, and `AZURE_OPENAI_MODEL_NAME`.
Add the keys too, unless you use `az login`.

## Step 3: Check the setup → paste the doctor report

```bash
python -m ap_coder doctor --online
```

`--online` makes one real call to each service, using a tiny made-up invoice (about the cost of
one invoice). **Paste the whole output into the chat.** If anything says FAIL, the line tells
you why, and I can help.

## Step 4: Smoke test with the bundled sample (no enterprise data)

Do this **before** Step 5. The sample is scored against the bundled sample Chart of Accounts.

```bash
python -m ap_coder process samples/contoso_invoice_INV-2026-04471.pdf -o private/sample_output
python -m ap_coder evaluate --predictions private/sample_output --ground-truth samples/ground_truth
```

This is a synthetic invoice, so you may paste anything from this step, including
`private/sample_output/contoso_invoice_INV-2026-04471.json`. It proves the full pipeline works
on your Azure resources. A separate output folder keeps it out of your real test runs.

## Step 5: Your reference data

Export these from your ERP as CSV (or JSON) into `private/reference/`:

| File | Required columns | Notes |
|---|---|---|
| `chart_of_accounts.csv` | a code column + a name column | Add a description/keywords column if you have one: the model matches on words. Only include accounts AP can post to. Optional `active` column (Yes/No). |
| `cost_centers.csv` | a code column + a name column | Department/owner/description columns help. |
| `tax_codes.csv` *(optional)* | `tax_code`, `rate` | Rate may be `0.2`, `20` or `20%`. |
| `coding_policy.md` *(optional, recommended)* | one rule per line, plain English | **This is your main tuning lever.** See `data/coding_policy.md` for examples (capitalisation threshold, prepaid rules, which department pays for what). |

You do not need to rename headers. Common ERP names are recognised automatically: *Account*,
*Main account*, *GL Account*, *SAKNR*, *Cost Centre*, *KOSTL*, *Department code*, *VAT code*, and
others. Save from Excel as **CSV UTF-8**.

Then run `python -m ap_coder doctor` again (offline is fine) and **paste the output**. From it I
can see whether your chart fits the schema enums, how large the prompt is, and whether any
column is missing. I can see none of your actual codes or names.

## Step 6: Pick a test set and run it

Copy **20–50 real invoices** into `private/invoices/`. Aim for a representative mix:

- your top vendors by volume
- multi-page invoices with tables that continue across pages
- scanned or poor-quality images, and TIFFs
- foreign-currency invoices, credit notes, freight/shipping lines, discounts
- a few "hard" ones your AP team often recodes

```bash
python -m ap_coder process private/invoices --workers 4
python -m ap_coder share-report
```

**Paste the share report.** It already tells us a lot without any labelling: failure types,
which validation checks fire, confidence spread, and cost per invoice.

## Step 7: Label the correct answers (AP team, ~2–5 min per invoice)

```bash
python -m ap_coder labels            # creates private/labels.xlsx
```

Give `private/labels.xlsx` to an AP clerk. It has one row per invoice line, pre-filled with the
engine's answers, and dropdowns of your valid GL codes and cost centers. The *Instructions*
sheet explains the rules. In short:

- fix anything wrong
- add missing lines and delete extra ones
- set `reviewed` = **Y** on every row

Invoices with unreviewed rows are skipped. Use `labels --blind` if you'd rather the clerk code
from scratch, without seeing the engine's answers.

Then:

```bash
python -m ap_coder share-report --ground-truth private/labels.xlsx --include-codes
```

**Paste it.** `--include-codes` adds which GL/cost-center codes get confused, for example
`6010 -> 1500 x4`. That is what lets me fix the prompt or suggest policy rules. Drop the flag if
your account codes may not leave the building; you still get all the accuracy percentages.

## Step 8: Iterate

I push improvements to the branch. You then run:

```bash
git pull
python -m ap_coder process private/invoices --workers 4      # OCR is cached: only the LLM step re-runs
python -m ap_coder share-report --ground-truth private/labels.xlsx --include-codes
```

There's no relabelling: the same `labels.xlsx` scores every new run. Meanwhile, you can improve
results yourself by adding rules to `private/reference/coding_policy.md`. The share report
shows whether accuracy moved.

We repeat until header, GL and cost-center accuracy are all **≥ 90%**. That is the gate for
Phase 2.

---

### If I need to see a specific problem

When the numbers aren't enough, describe the problem in words. For example: "doc-07: the
freight line was merged into the line above". If your policy allows, you can also paste a
**hand-redacted** snippet of `private/output/<file>.extraction.md`, with names and numbers
changed. Never paste anything you aren't comfortable sharing. I'll then build a synthetic
replica of the problem into `samples/`, so the fix is tested without your data.

### Command cheat sheet

| Command | What it does |
|---|---|
| `doctor [--online]` | setup check; safe to paste |
| `process <files/folders>` | extract + code + validate → `private/output/` |
| `share-report [--ground-truth private/labels.xlsx] [--include-codes]` | redacted summary; safe to paste |
| `labels [--blind]` | create the correction workbook (won't overwrite without `--force`) |
| `evaluate` | full accuracy JSON; `--show-mismatches` prints invoice data, keep it local |
| `schema` | the exact JSON Schema sent to Azure OpenAI |
