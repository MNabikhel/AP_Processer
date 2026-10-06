# private/: your enterprise data lives here (git-ignored)

Everything in this folder except this README is ignored by git, so nothing here can be pushed to GitHub.

```
private/
  ap_coder.db              # dashboard database: GL accounts, tax setup, invoices, reviews, the AI's memory
  invoices/                # PDFs / TIFFs / images to process (uploads land here too)
  output/                  # created by the `process` command (JSON per invoice)
  .cache/                  # Document Intelligence results, so re-runs don't pay for OCR again
  share_report.md          # created by `share-report`: the only file meant to leave your machine
  share_report_key.csv     # maps doc-01, doc-02 ... back to file names (keep local)
  reference/               # optional: CSV files for command-line use instead of the dashboard
```

To start over, close the dashboard and delete `ap_coder.db`.
