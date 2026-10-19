# data: the shipped tax rates and sample reference data

The Canadian sales-tax rate table, and sample reference data (GL accounts, cost centers, tax GL mapping, coding
policy, vendor master, purchase orders, a vendor statement) for the demo, the tests and the command line.
All of it is made up except the official rates. A company's own data never goes here: it lives in the data folder
outside the code (`AP_PRIVATE_DIR`, else the folder recorded in `~/.ap_coder/settings.json` (the launcher records `~/APCoder` by
default), else the git-ignored `private/` of a checkout, else `./private`; `paths.private_dir`), and is imported in the dashboard.

**Depends on:** none
**Used by:** `ap_coder/coding` (tax.py), `ap_coder/core` (store.py), `ap_coder/ops` (cli.py), `ap_coder/web/pages` (accounts, purchase orders, review), `ap_coder/bench`, `scripts` (pilot_check.py), `scripts/ci` (old_versions.py), `tests`

## Interface
- `canada_tax_rates.csv`: `tax_type,province,rate,effective_from,effective_to,notes`. Read by `TaxRateTable.load` in
  `ap_coder/coding/tax.py` (via `rates_path`): a `canada_tax_rates.csv` in the data folder wins over this one.
  `province` empty = Canada-wide (GST); `effective_to` empty = still in force; `rate` 0.05, 5 or "5%" (5%).
- `chart_of_accounts.csv` (`gl_code,description,category`), `cost_centers.csv` (`cost_center,description,category`),
  `tax_gl_mapping.csv` (`tax_type,treatment,gl_code`; treatment `recoverable`, `expense_to_line` or `expense_separate`),
  `coding_policy.md` (each non-blank line is a rule, leading `-`, `*` and spaces dropped; `#` lines ignored): loaded into the store by `load_sample_setup` (store.py;
  from demo.py and the Accounts and Review pages), read by `cli.reference_dir` as the last fallback, by
  `scripts/pilot_check.py`, `bench/coding.py`, `bench/speed.py` and the `reference` fixture of `tests/conftest.py`.
- `vendor_master.csv` (`vendor_id,vendor_name,gst_hst_number,payment_terms,status,default_gl`): `load_sample_vendor_master`
  (store.py, from demo.py). `purchase_orders.csv` (one row per PO line: `po_number,vendor_name,line_number,...,status`):
  `load_sample_purchase_orders` (store.py, from demo.py and the Purchase orders page). Both match the sample invoices.
- `sample_statement_northwind.csv` (`Date,Document,Type,Amount,Balance`): a vendor statement to upload by hand on the
  Statements page; read by `tests/test_statements.py`.

## Gotchas and decisions
- Finance edits `canada_tax_rates.csv` (in practice a copy in the data folder, which survives updates): a new rate is a
  new row with its `effective_from`; the old row may be closed or left open. The covering row with the latest
  `effective_from` wins, a province row beats a Canada-wide one on a tie, and overlaps are logged at load. Don't let
  file order decide which effective-dated row wins (B-12, tests/test_tax.py::test_a_new_rate_row_wins_over_an_old_one_left_open).
- A rate written with "%" is always a percentage ("0.5%" is half a percent); without it, 1 or more means percent (B-20).
- An unknown `tax_type`, a rate that is not a finite number (`nan`, `inf` included) or an unreadable date stops the load with the file and row number.
- Changing a sample file changes test results and the demo: the samples in `samples/` and their answers in
  `samples/ground_truth/` assume these GL codes, vendors and POs (`scripts/pilot_check.py` compares them).
- Keep the sample files UTF-8 (the sample loaders in store.py read `utf-8-sig` only; the rate table falls back to Windows-1252).

## Files
- `canada_tax_rates.csv`: GST, HST, PST/RST and QST rates by province with effective dates (CRA-checked).
- Sample reference data: `chart_of_accounts.csv` (26 GL accounts), `cost_centers.csv` (10), `tax_gl_mapping.csv` (tax to GL), `coding_policy.md` (coding rules in words).
- Sample master data: `vendor_master.csv` (8 vendors), `purchase_orders.csv` (PO lines), `sample_statement_northwind.csv` (a vendor statement).
