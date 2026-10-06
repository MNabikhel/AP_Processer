# private/ — your enterprise data lives here (git-ignored)

Everything in this folder except this README is ignored by git, so nothing here can be pushed to GitHub.

```
private/
  reference/
    chart_of_accounts.csv   # required: a code column (gl_code / Account / Main account ...) + name/description
    cost_centers.csv        # required: a code column (cost_center / Cost Centre / Department code ...) + name
    tax_codes.csv           # optional: tax_code + rate (0.2, 20 or 20%)
    coding_policy.md        # optional: one coding rule per line, in plain English
  invoices/                 # the PDFs / TIFFs / images you want to test
  output/                   # created by `process`
  labels.xlsx               # created by `labels`, corrected by the AP team
  share_report.md           # created by `share-report`: the only file meant to leave your machine
  share_report_key.csv      # maps doc-01, doc-02 ... back to file names (keep local)
```
