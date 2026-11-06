# Volume after phase 3 part 2: one person on 100,000 invoices (6 Nov 2026)

The same seed as phase 3's ([phase3-volume-100k.md](phase3-volume-100k.md)), made again by this code (schema 27):
100,000 invoices, 415,763 events, 421,954 lines of feedback, 200,000 register rows; the 4 cores with nothing else
running, Python 3.12. The bench asks in one process, as a laptop does.

**In short: 167 addresses, 2 over target (phase 3: 3, part 2: 11, the baseline: 43), 0 errors.** Both are the
Activity CSV (6.4-8.0 s), a background job on the server (D-169), so within target 2's "or a job". The JD Edwards
exports page is under target now (0.30 s for Admin, 0.18 s for a Clerk).

**The first request in a process** (the "warm" column) is the first time anything worked these answers out on this
fresh seed: month-end 5.9 s, Spend 3.1 s, Insights 1.6 s, Learning's suppliers 2.6 s. On the server they are worked out
once for every process (D-169, D-175), and a worker that starts later takes them in 1-3 ms. Import invoices' first view
is 0.85-1.05 s on a laptop (it lists the folder there); on the server a view reads the listing kept (D-176).

**50 full scans of big tables** (phase 3: 51): the first look of the answers kept per change, as before; no test
guards them yet.

### Volume benchmark: one person on big data

100,000 invoices, 415,763 events, 421,954 lines of feedback, 200,000 JD Edwards register rows; asked as admin (admin001), clerk (clerk001), the median of 3 after a warm request; 4 cores, Python 3.12.3, SQLite 3.45.1.

**167 addresses, 2 over target, 0 errors (5xx), 50 full scans of big tables.** Targets: a page 300 ms, a report, export or search 3 s.

| address | role | kind | status | median s | warm s | KB | SQL | peak MB | target | |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| `/activity/events.csv` | admin | export | 200 | 7.966 | 7.766 | 62,719 | 371 | 125.6 | 3 | **over** |
| `/activity/events.csv` | clerk | export | 200 | 6.447 | 6.789 | 54,903 | 294 | 110.0 | 3 | **over** |
| `/search/export.xlsx` | clerk | export | 200 | 2.345 | 2.385 | 3,182 | 245 | 16.4 | 3 | ok |
| `/search/export.xlsx` | admin | export | 200 | 2.280 | 2.405 | 3,182 | 243 | 16.4 | 3 | ok |
| `/search/export.csv` | clerk | export | 200 | 1.390 | 1.343 | 10,239 | 228 | 20.6 | 3 | ok |
| `/search/export.csv` | admin | export | 200 | 1.373 | 1.773 | 10,239 | 229 | 20.5 | 3 | ok |
| `/exports/batches/256/approved-pdfs.zip` | clerk | export | 200 | 1.355 | 1.412 | 191 | 15 | 1.7 | 3 | ok |
| `/exports/batches/256/approved-pdfs.zip` | admin | export | 200 | 1.342 | 1.367 | 191 | 15 | 1.7 | 3 | ok |
| `/sales-tax` | clerk | report | 200 | 1.211 | 1.064 | 214 | 25 | 39.3 | 3 | ok |
| `/sales-tax` | admin | report | 200 | 0.933 | 0.942 | 214 | 27 | 39.3 | 3 | ok |
| `/sales-tax/claims.csv` | admin | export | 200 | 0.681 | 0.720 | 4,290 | 14 | 44.9 | 3 | ok |
| `/sales-tax/claims.csv` | clerk | export | 200 | 0.539 | 0.834 | 4,290 | 21 | 44.9 | 3 | ok |
| `/settings/jde/worksheet.xlsx` | admin | export | 200 | 0.472 | 0.812 | 122 | 17 | 13.2 | 3 | ok |
| `/insights/business-case` | admin | report | 200 | 0.464 | 0.440 | 3 | 26 | 13.6 | 3 | ok |
| `/sales-tax/self-assessment.csv` | admin | export | 200 | 0.461 | 0.474 | 0 | 17 | 21.4 | 3 | ok |
| `/insights/business-case` | clerk | report | 200 | 0.454 | 0.455 | 3 | 26 | 13.6 | 3 | ok |
| `/sales-tax/self-assessment.csv` | clerk | export | 200 | 0.410 | 0.417 | 0 | 17 | 21.4 | 3 | ok |
| `/exports?fmt=jde` | admin | page | 200 | 0.298 | 0.172 | 450 | 44 |  | 0.3 | ok |
| `/activity?q=Frontier` | clerk | page | 200 | 0.280 | 0.270 | 131 | 36 |  | 0.3 | ok |
| `/activity?q=Frontier` | admin | page | 200 | 0.279 | 0.273 | 132 | 39 |  | 0.3 | ok |
| `/activity` | clerk | page | 200 | 0.248 | 0.267 | 131 | 35 |  | 0.3 | ok |
| `/learning?tab=coding` | admin | page | 200 | 0.242 | 1.324 | 164 | 38 |  | 0.3 | ok |
| `/learning?tab=coding` | clerk | page | 200 | 0.233 | 0.265 | 122 | 28 |  | 0.3 | ok |
| `/settings/automation` | admin | page | 200 | 0.196 | 0.197 | 15 | 23 |  | 0.3 | ok |
| `/exports?fmt=jde` | clerk | page | 200 | 0.184 | 0.223 | 416 | 42 |  | 0.3 | ok |
| `/review?tab=approved` | clerk | page | 200 | 0.172 | 0.162 | 44 | 45 |  | 0.3 | ok |
| `/review/8/approved.pdf` | clerk | export | 200 | 0.165 | 0.170 | 18 | 12 | 0.2 | 3 | ok |
| `/review?tab=approved` | admin | page | 200 | 0.158 | 0.161 | 44 | 45 |  | 0.3 | ok |
| `/search?q=Frontier` | admin | report | 200 | 0.153 | 0.156 | 160 | 116 | 9.3 | 3 | ok |
| `/activity` | admin | page | 200 | 0.153 | 0.172 | 115 | 36 |  | 0.3 | ok |
| `/search?q=Frontier` | clerk | report | 200 | 0.148 | 0.148 | 160 | 116 | 9.4 | 3 | ok |
| `/review/8/approved.pdf` | admin | export | 200 | 0.147 | 0.168 | 18 | 12 | 0.2 | 3 | ok |
| `/search/8/approved.pdf` | clerk | export | 200 | 0.146 | 0.155 | 18 | 12 | 0.2 | 3 | ok |
| `/search/8/approved.pdf` | admin | export | 200 | 0.145 | 0.190 | 18 | 12 | 0.2 | 3 | ok |
| `/settings/jde` | admin | page | 200 | 0.137 | 0.168 | 42 | 23 |  | 0.3 | ok |
| `/exports/jde/dry-run.txt` | admin | export | 200 | 0.131 | 0.113 | 115 | 19 | 19.1 | 3 | ok |
| `/month-end` | clerk | report | 200 | 0.129 | 0.100 | 1,979 | 75 | 12.5 | 3 | ok |
| `/review?tab=other` | clerk | page | 200 | 0.115 | 0.166 | 988 | 43 |  | 0.3 | ok |
| `/review?tab=other` | admin | page | 200 | 0.112 | 0.305 | 1,419 | 43 |  | 0.3 | ok |
| `/exports/jde/dry-run.txt` | clerk | export | 200 | 0.110 | 0.116 | 115 | 19 | 19.1 | 3 | ok |
| `/review?tab=second` | clerk | page | 200 | 0.101 | 0.069 | 67 | 43 |  | 0.3 | ok |
| `/purchase-orders?status=all` | admin | page | 200 | 0.092 | 0.092 | 150 | 17 |  | 0.3 | ok |
| `/purchase-orders` | clerk | page | 200 | 0.091 | 0.097 | 149 | 17 |  | 0.3 | ok |
| `/purchase-orders?status=all` | clerk | page | 200 | 0.091 | 0.086 | 149 | 17 |  | 0.3 | ok |
| `/purchase-orders` | admin | page | 200 | 0.090 | 0.143 | 150 | 17 |  | 0.3 | ok |
| `/month-end` | admin | report | 200 | 0.088 | 5.859 | 1,980 | 331 | 12.5 | 3 | ok |
| `/month-end/accruals.csv` | clerk | export | 200 | 0.081 | 0.079 | 756 | 110 | 5.0 | 3 | ok |
| `/settings/data` | admin | page | 200 | 0.080 | 0.639 | 23 | 26 |  | 0.3 | ok |
| `/review?sort=amount` | clerk | page | 200 | 0.078 | 0.069 | 84 | 47 |  | 0.3 | ok |
| `/exports/duplicates.csv` | admin | export | 200 | 0.077 | 7.476 | 221 | 191 | 1.6 | 3 | ok |
| `/exports/duplicates.csv` | clerk | export | 200 | 0.076 | 0.078 | 221 | 51 | 1.6 | 3 | ok |
| `/month-end/accruals.csv` | admin | export | 200 | 0.075 | 0.085 | 756 | 110 | 5.0 | 3 | ok |
| `/review?show=attention` | admin | page | 200 | 0.075 | 0.062 | 83 | 46 |  | 0.3 | ok |
| `/learning?tab=suppliers` | admin | page | 200 | 0.070 | 2.617 | 229 | 19 |  | 0.3 | ok |
| `/review?tab=parked` | clerk | page | 200 | 0.070 | 0.075 | 101 | 42 |  | 0.3 | ok |
| `/review?sort=amount` | admin | page | 200 | 0.070 | 0.064 | 84 | 47 |  | 0.3 | ok |
| `/vendors` | admin | page | 200 | 0.067 | 0.916 | 193 | 24 |  | 0.3 | ok |
| `/learning?tab=suppliers` | clerk | page | 200 | 0.066 | 0.374 | 157 | 17 |  | 0.3 | ok |
| `/help/report` | admin | report | 200 | 0.065 | 0.106 | 150 | 141 | 5.8 | 3 | ok |
| `/help/report` | clerk | report | 200 | 0.065 | 0.067 | 146 | 141 | 5.8 | 3 | ok |
| `/review?show=attention` | clerk | page | 200 | 0.065 | 0.063 | 83 | 46 |  | 0.3 | ok |
| `/search?q=FTC6962398` | admin | report | 200 | 0.064 | 0.065 | 30 | 253 | 0.2 | 3 | ok |
| `/vendors?q=Frontier` | admin | page | 200 | 0.063 | 0.064 | 145 | 21 |  | 0.3 | ok |
| `/review` | clerk | page | 200 | 0.063 | 0.082 | 82 | 47 |  | 0.3 | ok |
| `/review?show=ready` | clerk | page | 200 | 0.062 | 0.057 | 10 | 45 |  | 0.3 | ok |
| `/search?q=FTC6962398` | clerk | report | 200 | 0.061 | 0.060 | 30 | 254 | 0.2 | 3 | ok |
| `/review` | admin | page | 200 | 0.060 | 1.003 | 83 | 91 |  | 0.3 | ok |
| `/vendors` | clerk | page | 200 | 0.060 | 0.059 | 192 | 21 |  | 0.3 | ok |
| `/review?show=ready` | admin | page | 200 | 0.060 | 0.056 | 10 | 45 |  | 0.3 | ok |
| `/vendors?q=Frontier` | clerk | page | 200 | 0.058 | 0.058 | 144 | 21 |  | 0.3 | ok |
| `/review?tab=parked` | admin | page | 200 | 0.057 | 0.056 | 101 | 42 |  | 0.3 | ok |
| `/statements` | clerk | report | 200 | 0.057 | 0.064 | 268 | 17 | 6.4 | 3 | ok |
| `/review?tab=second` | admin | page | 200 | 0.057 | 0.060 | 152 | 43 |  | 0.3 | ok |
| `/exports` | admin | page | 200 | 0.057 | 0.343 | 348 | 47 |  | 0.3 | ok |
| `/accounts?tab=rules` | admin | page | 200 | 0.056 | 0.054 | 456 | 20 |  | 0.3 | ok |
| `/review/98499` | clerk | page | 200 | 0.056 | 0.053 | 303 | 104 |  | 0.3 | ok |
| `/review/98499` | admin | page | 200 | 0.050 | 3.264 | 304 | 134 |  | 0.3 | ok |
| `/statements` | admin | report | 200 | 0.049 | 0.670 | 269 | 23 | 6.4 | 3 | ok |
| `/accounts?tab=rules` | clerk | page | 200 | 0.048 | 0.052 | 9 | 20 |  | 0.3 | ok |
| `/accounts?tab=gl` | clerk | page | 200 | 0.047 | 0.044 | 1,184 | 18 |  | 0.3 | ok |
| `/learning?tab=lessons` | admin | page | 200 | 0.038 | 0.278 | 634 | 16 |  | 0.3 | ok |
| `/accounts?tab=gl` | admin | page | 200 | 0.037 | 0.089 | 1,187 | 18 |  | 0.3 | ok |
| `/review/8` | clerk | page | 200 | 0.037 | 0.043 | 21 | 35 |  | 0.3 | ok |
| `/accounts?tab=tax` | admin | page | 200 | 0.035 | 0.050 | 474 | 19 |  | 0.3 | ok |
| `/accounts?tab=tax` | clerk | page | 200 | 0.034 | 0.037 | 473 | 19 |  | 0.3 | ok |
| `/exports` | clerk | page | 200 | 0.029 | 0.242 | 315 | 38 |  | 0.3 | ok |
| `/process` | clerk | page | 200 | 0.029 | 0.848 | 15 | 76 |  | 0.3 | ok |
| `/process` | admin | page | 200 | 0.028 | 1.052 | 16 | 79 |  | 0.3 | ok |
| `/review/97005` | clerk | page | 200 | 0.025 | 0.041 | 26 | 37 |  | 0.3 | ok |
| `/learning?tab=lessons` | clerk | page | 200 | 0.024 | 0.025 | 482 | 14 |  | 0.3 | ok |
| `/review/97068` | clerk | page | 200 | 0.024 | 0.023 | 26 | 36 |  | 0.3 | ok |
| `/review/8` | admin | page | 200 | 0.024 | 0.025 | 21 | 35 |  | 0.3 | ok |
| `/exports/batches/256/download` | clerk | page | 200 | 0.023 | 0.021 | 28 | 18 |  | 0.3 | ok |
| `/accounts/gl_accounts.csv` | admin | export | 200 | 0.023 | 0.028 | 107 | 10 | 1.1 | 3 | ok |
| `/review/97005` | admin | page | 200 | 0.022 | 0.023 | 27 | 37 |  | 0.3 | ok |
| `/accounts?tab=bank` | admin | page | 200 | 0.022 | 0.025 | 474 | 23 |  | 0.3 | ok |
| `/admin/users` | admin | page | 200 | 0.021 | 0.180 | 814 | 15 |  | 0.3 | ok |
| `/accounts?tab=cc` | clerk | page | 200 | 0.021 | 0.018 | 187 | 18 |  | 0.3 | ok |
| `/accounts?tab=bank` | clerk | page | 200 | 0.021 | 0.022 | 195 | 19 |  | 0.3 | ok |
| `/review/97068` | admin | page | 200 | 0.021 | 0.021 | 26 | 36 |  | 0.3 | ok |
| `/accounts?tab=cc` | admin | page | 200 | 0.019 | 0.018 | 190 | 18 |  | 0.3 | ok |
| `/review/vendors.json` | admin | page | 200 | 0.019 | 0.019 | 3,028 | 5 |  | 0.3 | ok |
| `/review/98499/words` | admin | page | 200 | 0.018 | 0.025 | 8 | 4 |  | 0.3 | ok |
| `/accounts/gl_accounts.csv` | clerk | export | 200 | 0.018 | 0.023 | 107 | 10 | 1.1 | 3 | ok |
| `/review/vendors.json` | clerk | page | 200 | 0.017 | 0.020 | 3,028 | 5 |  | 0.3 | ok |
| `/search` | admin | page | 200 | 0.017 | 0.072 | 141 | 25 |  | 0.3 | ok |
| `/exports/batches/256/download` | admin | page | 200 | 0.016 | 0.018 | 28 | 18 |  | 0.3 | ok |
| `/activity/controls-report` | admin | report | 200 | 0.016 | 0.018 | 22 | 20 | 0.2 | 3 | ok |
| `/statements/reconciliation.csv` | clerk | export | 404 | 0.016 | 0.018 | 6 | 15 |  | 3 | ok |
| `/activity/controls-report` | clerk | report | 200 | 0.015 | 0.022 | 22 | 27 | 0.2 | 3 | ok |
| `/search?page=40` | clerk | page | 200 | 0.015 | 0.016 | 152 | 24 |  | 0.3 | ok |
| `/search` | clerk | page | 200 | 0.015 | 0.017 | 141 | 28 |  | 0.3 | ok |
| `/search?page=40` | admin | page | 200 | 0.015 | 0.015 | 152 | 21 |  | 0.3 | ok |
| `/accounts?tab=policy` | clerk | page | 200 | 0.015 | 0.017 | 9 | 17 |  | 0.3 | ok |
| `/statements/reconciliation.csv` | admin | export | 404 | 0.014 | 0.020 | 7 | 15 |  | 3 | ok |
| `/accounts?tab=policy` | admin | page | 200 | 0.014 | 0.015 | 9 | 17 |  | 0.3 | ok |
| `/settings/about` | admin | page | 200 | 0.014 | 0.019 | 10 | 18 |  | 0.3 | ok |
| `/learning/training.zip` | admin | export | 404 | 0.013 | 0.201 | 7 | 16 |  | 3 | ok |
| `/review/98499/words` | clerk | page | 200 | 0.013 | 0.016 | 8 | 4 |  | 0.3 | ok |
| `/spend` | admin | report | 200 | 0.013 | 3.071 | 36 | 103 | 0.5 | 3 | ok |
| `/spend/data.xlsx` | admin | export | 404 | 0.013 | 0.014 | 7 | 15 |  | 3 | ok |
| `/spend/data.xlsx` | clerk | export | 404 | 0.012 | 0.012 | 6 | 15 |  | 3 | ok |
| `/settings/hub` | admin | page | 200 | 0.012 | 0.024 | 14 | 27 |  | 0.3 | ok |
| `/help` | clerk | page | 200 | 0.012 | 0.013 | 124 | 16 |  | 0.3 | ok |
| `/accounts/cost_centers.csv` | clerk | export | 200 | 0.011 | 0.013 | 9 | 10 | 0.3 | 3 | ok |
| `/settings/review` | admin | page | 200 | 0.011 | 0.011 | 12 | 30 |  | 0.3 | ok |
| `/help` | admin | page | 200 | 0.011 | 0.025 | 126 | 16 |  | 0.3 | ok |
| `/settings/reading` | admin | page | 200 | 0.011 | 2.560 | 19 | 31 |  | 0.3 | ok |
| `/settings` | admin | page | 200 | 0.011 | 0.027 | 12 | 30 |  | 0.3 | ok |
| `/accounts/cost_centers.csv` | admin | export | 200 | 0.011 | 0.011 | 9 | 10 | 0.3 | 3 | ok |
| `/spend` | clerk | report | 200 | 0.010 | 0.011 | 36 | 17 | 0.4 | 3 | ok |
| `/accounts/bank/template.csv` | admin | export | 200 | 0.010 | 0.008 | 0 | 8 | 0.1 | 3 | ok |
| `/insights` | admin | report | 200 | 0.009 | 1.554 | 39 | 98 | 0.3 | 3 | ok |
| `/review/98499/reader` | admin | page | 200 | 0.009 | 0.010 | 0 | 12 |  | 0.3 | ok |
| `/insights` | clerk | report | 200 | 0.009 | 0.009 | 39 | 19 | 0.3 | 3 | ok |
| `/account/password` | clerk | page | 200 | 0.009 | 0.009 | 7 | 13 |  | 0.3 | ok |
| `/help/report/setup` | admin | report | 200 | 0.008 | 0.113 | 3 | 375 | 0.1 | 3 | ok |
| `/help/quick-start` | admin | page | 200 | 0.008 | 0.019 | 11 | 11 |  | 0.3 | ok |
| `/help/quick-start` | clerk | page | 200 | 0.008 | 0.011 | 11 | 11 |  | 0.3 | ok |
| `/jobs/process` | clerk | page | 200 | 0.008 | 0.008 | 0 | 4 |  | 0.3 | ok |
| `/login/code` | admin | page | 303 | 0.008 | 0.007 | 0 | 3 |  | 0.3 | ok |
| `/accounts/bank/template.csv` | clerk | export | 200 | 0.008 | 0.007 | 0 | 8 | 0.1 | 3 | ok |
| `/review/98499/page/1.png` | admin | page | 200 | 0.008 | 0.007 | 63 | 4 |  | 0.3 | ok |
| `/learning?tab=readers` | admin | page | 200 | 0.008 | 0.008 | 7 | 12 |  | 0.3 | ok |
| `/learning?tab=readers` | clerk | page | 200 | 0.008 | 0.008 | 7 | 12 |  | 0.3 | ok |
| `/account/password` | admin | page | 200 | 0.007 | 0.013 | 8 | 13 |  | 0.3 | ok |
| `/review/98499/page/1.png` | clerk | page | 200 | 0.007 | 0.008 | 63 | 4 |  | 0.3 | ok |
| `/review/98499/reader` | clerk | page | 200 | 0.007 | 0.007 | 0 | 12 |  | 0.3 | ok |
| `/settings/azure-check` | admin | page | 200 | 0.007 | 0.013 | 7 | 16 |  | 0.3 | ok |
| `/purchase-orders/template.csv` | admin | export | 200 | 0.007 | 0.010 | 0 | 8 | 0.1 | 3 | ok |
| `/purchase-orders/template.csv` | clerk | export | 200 | 0.007 | 0.011 | 0 | 8 | 0.1 | 3 | ok |
| `/login/authenticator` | admin | page | 303 | 0.007 | 0.007 | 0 | 3 |  | 0.3 | ok |
| `/login/new-password` | admin | page | 303 | 0.007 | 0.014 | 0 | 3 |  | 0.3 | ok |
| `/jobs/process` | admin | page | 200 | 0.007 | 0.007 | 0 | 4 |  | 0.3 | ok |
| `/process/status` | admin | page | 200 | 0.007 | 0.007 | 0 | 4 |  | 0.3 | ok |
| `/who` | clerk | page | 303 | 0.006 | 0.007 | 0 | 3 |  | 0.3 | ok |
| `/who` | admin | page | 303 | 0.006 | 0.006 | 0 | 3 |  | 0.3 | ok |
| `/login` | clerk | page | 303 | 0.006 | 0.006 | 0 | 3 |  | 0.3 | ok |
| `/process/status` | clerk | page | 200 | 0.006 | 0.006 | 0 | 4 |  | 0.3 | ok |
| `/login/new-password` | clerk | page | 303 | 0.006 | 0.005 | 0 | 3 |  | 0.3 | ok |
| `/login` | admin | page | 303 | 0.006 | 0.006 | 0 | 3 |  | 0.3 | ok |
| `/login/authenticator` | clerk | page | 303 | 0.006 | 0.006 | 0 | 3 |  | 0.3 | ok |
| `/` | admin | page | 307 | 0.006 | 0.006 | 0 | 2 |  | 0.3 | ok |
| `/healthz` | clerk | page | 200 | 0.006 | 0.007 | 0 | 2 |  | 0.3 | ok |
| `/` | clerk | page | 307 | 0.006 | 0.006 | 0 | 2 |  | 0.3 | ok |
| `/login/code` | clerk | page | 303 | 0.006 | 0.006 | 0 | 3 |  | 0.3 | ok |
| `/healthz` | admin | page | 200 | 0.005 | 0.006 | 0 | 2 |  | 0.3 | ok |

### Full scans of big tables

Each `SCAN` in the plan of a statement a warm request ran, on a table with 12,940 rows or more; `how`: the table itself or an index read whole.

| table | rows | how | routes | statement |
|---|---:|---|---|---|
| invoices | 100,000 | table | `/help/report` | `SELECT id, source_path, status, error, meta, validation FROM invoices WHERE id IN (SELECT id FROM invoices ORDER BY id DESC LIMIT ?) ORDER BY id` |
| invoices | 100,000 | COVERING INDEX invoices_total | `/help/report` | `SELECT COUNT(*) FROM invoices` |
| events | 415,763 | COVERING INDEX events_invoice | `/activity`, `/activity?q=Frontier` | `SELECT COUNT(*) FROM events e WHERE ? = ?` |
| events | 415,763 | COVERING INDEX events_actor | `/activity`, `/activity?q=Frontier` | `SELECT a.actor FROM (SELECT DISTINCT actor FROM events WHERE actor != ?) a WHERE EXISTS (SELECT ? FROM events e WHERE e.actor = a.actor AND ? = ?)` |
| events | 415,763 | COVERING INDEX events_action | `/activity`, `/activity?q=Frontier` | `SELECT COUNT(*) FROM events e WHERE ? = ? AND NOT e.action IN (SELECT value FROM json_each(?))` |
| events | 415,763 | COVERING INDEX events_actor | `/activity`, `/activity?q=Frontier` | `SELECT a.actor FROM (SELECT DISTINCT actor FROM events WHERE actor != ?) a WHERE EXISTS (SELECT ? FROM events e WHERE e.actor = a.actor AND ? = ? AND NOT e.action IN (SELECT value FROM json_each(?)))` |
| feedback | 421,954 | table | `/review/98499`, `/accounts?tab=rules`, `/learning?tab=coding`, `/settings/data`, `/help/report` | `SELECT (SELECT MAX(id) FROM feedback), (SELECT TOTAL(n) FROM feedback_counts), (SELECT created_at \|\| ? \|\| COALESCE(vendor_key, ?) \|\| ? \|\| COALESCE(description, ?) \|\| ? \|\| COALESCE(final_gl, ?) FROM feedback ORDER BY id D` |
| feedback | 421,954 | table | `/review/98499`, `/learning?tab=coding` | `SELECT json_group_array(json_array(CASE typeof(q."id") WHEN ? THEN json_object(?, printf(?, q."id")) WHEN ? THEN q."id" \|\| ? WHEN ? THEN json_object(?, hex(q."id")) ELSE q."id" END, CASE typeof(q."invoice_id") WHEN ? THE` |
| feedback | 421,954 | table | `/learning?tab=coding` | `SELECT * FROM (SELECT f.invoice_id, f.line_number, f.vendor_name, f.description, f.final_tax_code, f.created_at, f.reviewer, i.invoice_number, (SELECT json_extract(c.value, ?) FROM events e, json_each(e.detail, ?) c WHER` |
| feedback | 421,954 | table | `/learning?tab=coding` | `SELECT substr(created_at, ?, ?) day, SUM(outcome = ?) accepted, SUM(outcome = ?) corrected FROM feedback WHERE outcome NOT IN (?, ?, ?) GROUP BY day` |
| feedback | 421,954 | COVERING INDEX feedback_vendor_outcome | `/statements` | `SELECT vendor_key, COUNT(*) lessons, SUM(outcome NOT IN (?, ?, ?)) lines, SUM(outcome = ?) accepted FROM feedback GROUP BY vendor_key` |
| feedback | 421,954 | INDEX feedback_vendor_outcome | `/learning?tab=coding` | `SELECT vendor_name, COUNT(*) lines, SUM(outcome = ?) accepted, SUM(outcome = ?) corrected, MAX(created_at) last_seen FROM feedback WHERE outcome NOT IN (?, ?, ?) GROUP BY vendor_key ORDER BY lines DESC` |
| erp_invoices | 200,000 | table | `/search?q=Frontier` | `SELECT json_group_array(json_array(CASE typeof(q."vendor_key") WHEN ? THEN json_object(?, printf(?, q."vendor_key")) WHEN ? THEN q."vendor_key" \|\| ? WHEN ? THEN json_object(?, hex(q."vendor_key")) ELSE q."vendor_key" END` |
| erp_invoices | 200,000 | table | `/search?q=FTC6962398` | `SELECT json_group_array(json_array(CASE typeof(q."vendor_key") WHEN ? THEN json_object(?, printf(?, q."vendor_key")) WHEN ? THEN q."vendor_key" \|\| ? WHEN ? THEN json_object(?, hex(q."vendor_key")) ELSE q."vendor_key" END` |
| erp_invoices | 200,000 | table | `/exports/duplicates.csv` | `SELECT json_group_array(json_array(CASE typeof(q."vendor_key") WHEN ? THEN json_object(?, printf(?, q."vendor_key")) WHEN ? THEN q."vendor_key" \|\| ? WHEN ? THEN json_object(?, hex(q."vendor_key")) ELSE q."vendor_key" END` |
| erp_invoices | 200,000 | COVERING INDEX erp_invoices_number | `/exports`, `/exports?fmt=jde` | `SELECT COUNT(*) FROM erp_invoices` |
| reader_outcomes | 1,957,734 | INDEX reader_outcomes_reader | `/settings/reading` | `SELECT reader, COUNT(*) fields, SUM(correct) agreed, COUNT(DISTINCT invoice_id) invoices FROM reader_outcomes GROUP BY reader ORDER BY reader` |
| lesson_approvals | 1,945,789 | table | `/learning?tab=coding` | `SELECT a.invoice_id, a.value, a.proposed, a.at, l.vendor_name, l.field, l.pattern, i.invoice_number, i.reviewer, n.corrected next_corrected, n.value next_value, n.invoice_id next_invoice_id, n.at next_at FROM lesson_appr` |
| supplier_outcomes | 1,101,881 | INDEX supplier_outcomes_key_at | `/learning?tab=suppliers` | `WITH inv AS ( SELECT supplier_key, invoice_id, MAX(at) AS at, COUNT(*) AS fields, SUM(correct = ?) AS corrections, json_group_array(DISTINCT COALESCE(NULLIF(source, ?), ?)) AS sources FROM supplier_outcomes WHERE ? GROUP` |
| supplier_outcomes | 1,101,881 | INDEX supplier_outcomes_invoice | `/settings/automation` | `SELECT invoice_id, MIN(correct) AS clean FROM supplier_outcomes WHERE source = ? AND at >= ? GROUP BY invoice_id` |
| line_texts | 421,954 | table | `/learning?tab=coding` | `SELECT t.invoice_id, t.printed, t.description, t.at, i.vendor_name, i.invoice_number, i.reviewer, n.corrected next_corrected, n.description next_value, n.invoice_id next_invoice_id, n.at next_at FROM line_texts t LEFT JO` |
| invoice_summary | 100,000 | table | `/review`, `/month-end` | `SELECT json_group_array(json_array(CASE typeof(q."vendor_key") WHEN ? THEN json_object(?, printf(?, q."vendor_key")) WHEN ? THEN q."vendor_key" \|\| ? WHEN ? THEN json_object(?, hex(q."vendor_key")) ELSE q."vendor_key" END` |
| invoice_summary | 100,000 | table | `/month-end` | `SELECT id, invoice_date, po_key, vendor_key FROM invoice_summary WHERE +status IN (?, ?, ?, ?) AND (+po_key IN (SELECT value FROM json_each(?)) OR (+po_key = ? AND +vendor_key IN (SELECT value FROM json_each(?)))) ORDER ` |
| invoice_summary | 100,000 | table | `/sales-tax`, `/sales-tax/claims.csv` | `SELECT json_group_array(json_array(CASE typeof(q."id") WHEN ? THEN json_object(?, printf(?, q."id")) WHEN ? THEN q."id" \|\| ? WHEN ? THEN json_object(?, hex(q."id")) ELSE q."id" END, CASE typeof(q."status") WHEN ? THEN js` |
| invoice_summary | 100,000 | table | `/vendors` | `SELECT json_group_array(json_array(CASE typeof(q."id") WHEN ? THEN json_object(?, printf(?, q."id")) WHEN ? THEN q."id" \|\| ? WHEN ? THEN json_object(?, hex(q."id")) ELSE q."id" END, CASE typeof(q."status") WHEN ? THEN js` |
| invoice_summary | 100,000 | table | `/learning?tab=coding`, `/activity`, `/activity?q=Frontier` | `SELECT json_group_array(json_array(CASE typeof(q."n") WHEN ? THEN json_object(?, printf(?, q."n")) WHEN ? THEN q."n" \|\| ? WHEN ? THEN json_object(?, hex(q."n")) ELSE q."n" END, CASE typeof(q."clean") WHEN ? THEN json_obj` |
| invoice_summary | 100,000 | table | `/learning?tab=coding`, `/activity`, `/activity?q=Frontier` | `SELECT json_group_array(json_array(CASE typeof(q."id") WHEN ? THEN json_object(?, printf(?, q."id")) WHEN ? THEN q."id" \|\| ? WHEN ? THEN json_object(?, hex(q."id")) ELSE q."id" END, CASE typeof(q."broken") WHEN ? THEN js` |
| invoice_summary | 100,000 | table | `/spend` | `SELECT json_group_array(json_array(CASE typeof(q."id") WHEN ? THEN json_object(?, printf(?, q."id")) WHEN ? THEN q."id" \|\| ? WHEN ? THEN json_object(?, hex(q."id")) ELSE q."id" END)) FROM (SELECT id FROM invoice_summary ` |
| invoice_summary | 100,000 | table | `/insights`, `/insights/business-case` | `SELECT status = ?, COUNT(*), SUM(CASE WHEN requires_review THEN ? ELSE ? END) FROM invoice_summary WHERE broken & ? = ? GROUP BY ?` |
| invoice_summary | 100,000 | table | `/insights`, `/insights/business-case` | `SELECT substr(created_at, ?, ?), COUNT(*) FROM invoice_summary WHERE +status != ? AND broken & ? = ? GROUP BY ?` |
| invoice_summary | 100,000 | table | `/insights`, `/insights/business-case` | `SELECT substr(a_at, ?, ?), a_corrected, a_filled, a_bulk, COUNT(*) FROM invoice_summary WHERE +status IN (?, ?) AND a_at IS NOT NULL AND broken & ? = ? GROUP BY ?, ?, ?, ?` |
| invoice_summary | 100,000 | table | `/insights`, `/insights/business-case` | `SELECT i_seconds FROM invoice_summary WHERE +status != ? AND i_seconds IS NOT NULL AND broken & ? = ? ORDER BY id` |
| invoice_summary | 100,000 | table | `/insights`, `/insights/business-case` | `SELECT json_group_array(json_array(CASE typeof(q."id") WHEN ? THEN json_object(?, printf(?, q."id")) WHEN ? THEN q."id" \|\| ? WHEN ? THEN json_object(?, hex(q."id")) ELSE q."id" END, CASE typeof(q."status") WHEN ? THEN js` |
| invoice_summary | 100,000 | table | `/insights` | `SELECT json_group_array(json_array(CASE typeof(q."id") WHEN ? THEN json_object(?, printf(?, q."id")) WHEN ? THEN q."id" \|\| ? WHEN ? THEN json_object(?, hex(q."id")) ELSE q."id" END, CASE typeof(q."status") WHEN ? THEN js` |
| invoice_summary | 100,000 | COVERING INDEX summary_queue_order | `/review/98499` | `SELECT status, COUNT(*) FROM invoice_summary GROUP BY status` |
| invoice_summary | 100,000 | COVERING INDEX summary_received | `/process` | `SELECT json_group_array(json_array(CASE typeof(q."id") WHEN ? THEN json_object(?, printf(?, q."id")) WHEN ? THEN q."id" \|\| ? WHEN ? THEN json_object(?, hex(q."id")) ELSE q."id" END, CASE typeof(q."file_name") WHEN ? THEN` |
| invoice_summary | 100,000 | COVERING INDEX summary_broken | `/search`, `/search?page=40` | `WITH rows AS ( SELECT id, status, file_name, vendor_name, invoice_number, invoice_date, due_date, currency, grand_total, created_at, reviewed_at, reviewer, second_reviewer, second_reviewed_at, export_batch, parked_reason` |
| invoice_summary | 100,000 | COVERING INDEX summary_received | `/search`, `/search?page=40`, `/search/export.csv`, `/search/export.xlsx` | `SELECT json_group_array(json_array(CASE typeof(q."id") WHEN ? THEN json_object(?, printf(?, q."id")) WHEN ? THEN q."id" \|\| ? WHEN ? THEN json_object(?, hex(q."id")) ELSE q."id" END)) FROM ( WITH rows AS ( SELECT id, stat` |
| invoice_summary | 100,000 | INDEX summary_vendor | `/statements` | `SELECT json_group_array(json_array(CASE typeof(q."vendor_key") WHEN ? THEN json_object(?, printf(?, q."vendor_key")) WHEN ? THEN q."vendor_key" \|\| ? WHEN ? THEN json_object(?, hex(q."vendor_key")) ELSE q."vendor_key" END` |
| invoice_summary | 100,000 | COVERING INDEX summary_queue_order | `/learning?tab=coding`, `/settings/data`, `/help/report` | `SELECT status, COUNT(*) n FROM invoice_summary GROUP BY status` |
| summary_codes | 271,688 | COVERING INDEX summary_codes_code | `/insights`, `/insights/business-case` | `SELECT c.code, SUM(c.n), MIN(c.id), MIN(c.pos) FROM summary_codes c JOIN (SELECT code, MIN(id) first FROM summary_codes GROUP BY code) f ON f.code = c.code AND c.id = f.first GROUP BY c.code` |
| summary_codes | 271,688 | COVERING INDEX summary_codes_code | `/insights`, `/insights/business-case` | `SELECT code, SUM(n) FROM summary_codes GROUP BY code` |
| vendors | 19,008 | table | `/review/98499`, `/review/vendors.json`, `/vendors`, `/vendors?q=Frontier` | `SELECT ? FROM vendors WHERE in_master = ? LIMIT ?` |
| vendors | 19,008 | table | `/review/98499` | `SELECT json_group_array(json_array(CASE typeof(q."vendor_key") WHEN ? THEN json_object(?, printf(?, q."vendor_key")) WHEN ? THEN q."vendor_key" \|\| ? WHEN ? THEN json_object(?, hex(q."vendor_key")) ELSE q."vendor_key" END` |
| vendors | 19,008 | table | `/review/98499` | `SELECT json_group_array(json_array(CASE typeof(q."vendor_key") WHEN ? THEN json_object(?, printf(?, q."vendor_key")) WHEN ? THEN q."vendor_key" \|\| ? WHEN ? THEN json_object(?, hex(q."vendor_key")) ELSE q."vendor_key" END` |
| vendors | 19,008 | table | `/review`, `/insights` | `SELECT vendor_key, terms FROM vendors WHERE terms != ?` |
| vendors | 19,008 | table | `/process` | `SELECT vendor_key, erp_id FROM vendors WHERE in_master = ?` |
| vendors | 19,008 | table | `/exports` | `SELECT vendor_key, erp_id, expected_gst FROM vendors WHERE erp_id != ?` |
| vendors | 19,008 | table | `/settings/jde` | `SELECT * FROM vendors WHERE in_master = ?` |
| lessons | 65,753 | table | `/learning?tab=lessons` | `SELECT lessons.*, (SELECT COUNT(*) FROM lesson_approvals a WHERE a.situation = lessons.situation AND a.corrected = ? AND a.value = lessons.value) + MAX(lessons.corrected - (SELECT COUNT(*) FROM lesson_approvals a WHERE a` |

### Statements one request runs many times

The statements a warm request ran 20 times or more (one per row: an N+1), as admin.

| address | times | statement |
|---|---:|---|
| `/activity/events.csv` | 208 | `SELECT e.*, i.vendor_name AS invoice_vendor, i.invoice_number AS invoice_number FROM events e LEFT JOIN invoices i ON i.id = e.invoice_id WHERE ? = ? AND e.id < ? ORDER BY e.id DESC LIMIT ?` |
| `/activity/events.csv` | 45 | `PRAGMA journal_mode=WAL` |
| `/activity/events.csv` | 33 | `SELECT value FROM settings WHERE key = ?` |
| `/activity/events.csv` | 30 | `SELECT * FROM report_jobs WHERE id = ? AND db = ?` |
| `/month-end` | 139 | `WITH coded AS (SELECT id, COALESCE(NULLIF(final_output, ?), NULLIF(ai_output, ?), ?) AS doc FROM invoices WHERE id IN (SELECT value FROM json_each(?))), read AS (SELECT id, doc, CASE WHEN NOT json_val` |
| `/month-end` | 50 | `SELECT final_gl gl_code, COUNT(*) lines, SUM(outcome = ?) corrected FROM feedback WHERE vendor_key = ? GROUP BY final_gl ORDER BY lines DESC` |
| `/month-end` | 33 | `SELECT ? FROM summary_dirty LIMIT ?` |
| `/month-end` | 22 | `PRAGMA busy_timeout = ?` |
| `/search?q=FTC6962398` | 224 | `-- SELECT pgno FROM ?.? WHERE segid=? AND term<=? ORDER BY term DESC LIMIT ?` |
| `/search/export.xlsx` | 58 | `SELECT ? FROM summary_dirty LIMIT ?` |
| `/search/export.xlsx` | 50 | `SELECT json_group_array(json_array(CASE typeof(q."id") WHEN ? THEN json_object(?, printf(?, q."id")) WHEN ? THEN q."id" \|\| ? WHEN ? THEN json_object(?, hex(q."id")) ELSE q."id" END, CASE typeof(q."sta` |
| `/search/export.xlsx` | 50 | `SELECT id, created_at FROM export_batches WHERE id IN (SELECT value FROM json_each(?))` |
| `/search/export.xlsx` | 23 | `PRAGMA journal_mode=WAL` |
| `/search/export.csv` | 57 | `SELECT ? FROM summary_dirty LIMIT ?` |
| `/search/export.csv` | 50 | `SELECT json_group_array(json_array(CASE typeof(q."id") WHEN ? THEN json_object(?, printf(?, q."id")) WHEN ? THEN q."id" \|\| ? WHEN ? THEN json_object(?, hex(q."id")) ELSE q."id" END, CASE typeof(q."sta` |
| `/search/export.csv` | 50 | `SELECT id, created_at FROM export_batches WHERE id IN (SELECT value FROM json_each(?))` |
| `/exports/duplicates.csv` | 39 | `PRAGMA journal_mode=WAL` |
| `/exports/duplicates.csv` | 25 | `SELECT value FROM settings WHERE key = ?` |
| `/exports/duplicates.csv` | 23 | `SELECT ? FROM summary_dirty LIMIT ?` |
| `/exports/duplicates.csv` | 22 | `SELECT * FROM report_jobs WHERE id = ? AND db = ?` |
| `/search?q=Frontier` | 84 | `-- SELECT pgno FROM ?.? WHERE segid=? AND term<=? ORDER BY term DESC LIMIT ?` |
| `/month-end/accruals.csv` | 50 | `SELECT final_gl gl_code, COUNT(*) lines, SUM(outcome = ?) corrected FROM feedback WHERE vendor_key = ? GROUP BY final_gl ORDER BY lines DESC` |
| `/spend` | 33 | `SELECT ? FROM summary_dirty LIMIT ?` |
| `/insights` | 29 | `SELECT ? FROM summary_dirty LIMIT ?` |
| `/review` | 22 | `SELECT ? FROM summary_dirty LIMIT ?` |

### Not asked

- `/logout`: ends the session
- `/downloads/{job_id:int}`: no job_id in the data folder
- `/process/inbox/{entry_id}/file/{n}`: no email in the inbox
- `/manager`: a Manager's page (no Manager in the seed)
- `/manager/{request_id:int}`: no manager request in the seed
- `/manager/{request_id:int}/page/{number:int}.png`: no manager request in the seed
- `/manager/{request_id:int}/reply`: no manager request in the seed
- `/manager/{request_id:int}/email.eml`: no manager request in the seed
- `/settings/data/backups/{name}`: no backup made (a backup of a big database fills the disk)
- `/settings/reading/self-test`: asks the model server (the load bench's fake one)
