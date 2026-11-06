# Volume after phase 3: one person on 100,000 invoices (6 Nov 2026)

The same seed as phase 2 part 2 ([phase2b-volume-100k.md](phase2b-volume-100k.md)), made again by this code (schema
27): 100,000 invoices, 415,763 events, 421,954 lines of feedback, 200,000 register rows. Measured on the 4 cores with
nothing else running, Python 3.12. The bench asks in one process, as a laptop does: on the company server the long
downloads are background jobs instead (D-169), and the web worker's share of each is 7-46 ms.

**In short: 167 addresses, 3 over target (part 2: 11, part 1: 38, the baseline: 43), 0 errors.** Search to Excel 12 s
→ 2.5 s (the workbook written directly, D-169), search to CSV 1.8 → 1.4-1.7 s. Still over: the Activity CSV, 6.5-7.7 s
(a background job on the server, so within target 2's "or a job"), and the JD Edwards exports page for Admin, 0.33 s
(0.20 s for a Clerk).

**The first request in a process** (the "warm" column) is still slow for the pages that keep an answer per process:
month-end 5.6 s, the duplicates CSV 7.1 s, Spend 2.9 s, Insights 1.5 s, Import invoices 1.2-1.4 s, Vendors 0.8 s, Learning
→ Coding accuracy 1.3 s. Each of the server's web workers pays them once, and again after the changes they follow: the
start of the load test's tail (phase 3 part 2: the answers shared by every process, or worked out as a worker starts).

**51 full scans of big tables** (part 2: 45), listed below with their routes. Those on the queue and an invoice's
page are the first look of the answers kept per change (D-159: the learning memory built from the lessons, the vendor
list, the queue's counts and the vendors' terms), worked out once after a change, not for each request; the others are
counts over an index, Activity's filters, Settings → Data and Report a problem.


100,000 invoices, 415,763 events, 421,954 lines of feedback, 200,000 JD Edwards register rows; asked as admin (admin001), clerk (clerk001), the median of 3 after a warm request; 4 cores, Python 3.12.3, SQLite 3.45.1.

**167 addresses, 3 over target, 0 errors (5xx), 51 full scans of big tables.** Targets: a page 300 ms, a report, export or search 3 s.

| address | role | kind | status | median s | warm s | KB | SQL | peak MB | target | |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| `/activity/events.csv` | admin | export | 200 | 7.707 | 7.558 | 62,719 | 371 | 125.6 | 3 | **over** |
| `/activity/events.csv` | clerk | export | 200 | 6.519 | 6.720 | 54,903 | 299 | 110.0 | 3 | **over** |
| `/search/export.xlsx` | clerk | export | 200 | 2.581 | 2.359 | 3,182 | 245 | 16.4 | 3 | ok |
| `/search/export.xlsx` | admin | export | 200 | 2.538 | 2.411 | 3,182 | 243 | 16.4 | 3 | ok |
| `/search/export.csv` | admin | export | 200 | 1.682 | 1.537 | 10,239 | 226 | 20.6 | 3 | ok |
| `/exports/batches/256/approved-pdfs.zip` | admin | export | 200 | 1.474 | 1.428 | 191 | 15 | 1.7 | 3 | ok |
| `/exports/batches/256/approved-pdfs.zip` | clerk | export | 200 | 1.380 | 1.310 | 191 | 15 | 1.7 | 3 | ok |
| `/search/export.csv` | clerk | export | 200 | 1.357 | 1.402 | 10,239 | 228 | 20.5 | 3 | ok |
| `/sales-tax` | clerk | report | 200 | 1.172 | 1.085 | 214 | 25 | 39.3 | 3 | ok |
| `/sales-tax` | admin | report | 200 | 1.002 | 0.958 | 214 | 27 | 39.3 | 3 | ok |
| `/sales-tax/claims.csv` | admin | export | 200 | 0.716 | 0.756 | 4,290 | 14 | 44.9 | 3 | ok |
| `/sales-tax/claims.csv` | clerk | export | 200 | 0.544 | 0.835 | 4,290 | 14 | 44.9 | 3 | ok |
| `/settings/jde/worksheet.xlsx` | admin | export | 200 | 0.503 | 0.705 | 122 | 17 | 12.9 | 3 | ok |
| `/insights/business-case` | clerk | report | 200 | 0.463 | 0.448 | 3 | 26 | 13.6 | 3 | ok |
| `/insights/business-case` | admin | report | 200 | 0.435 | 0.464 | 3 | 26 | 13.6 | 3 | ok |
| `/sales-tax/self-assessment.csv` | clerk | export | 200 | 0.398 | 0.379 | 0 | 17 | 21.4 | 3 | ok |
| `/sales-tax/self-assessment.csv` | admin | export | 200 | 0.390 | 0.353 | 0 | 17 | 21.4 | 3 | ok |
| `/exports?fmt=jde` | admin | page | 200 | 0.333 | 0.192 | 450 | 44 |  | 0.3 | **over** |
| `/activity?q=Frontier` | clerk | page | 200 | 0.297 | 0.296 | 131 | 36 |  | 0.3 | ok |
| `/activity` | clerk | page | 200 | 0.262 | 0.269 | 131 | 35 |  | 0.3 | ok |
| `/activity?q=Frontier` | admin | page | 200 | 0.258 | 0.258 | 132 | 39 |  | 0.3 | ok |
| `/search?q=Frontier` | clerk | report | 200 | 0.255 | 0.189 | 160 | 116 | 9.2 | 3 | ok |
| `/month-end` | admin | report | 200 | 0.251 | 5.564 | 1,980 | 295 | 12.5 | 3 | ok |
| `/month-end` | clerk | report | 200 | 0.239 | 0.258 | 1,979 | 75 | 12.4 | 3 | ok |
| `/learning?tab=coding` | clerk | page | 200 | 0.229 | 0.253 | 122 | 28 |  | 0.3 | ok |
| `/learning?tab=coding` | admin | page | 200 | 0.222 | 1.292 | 164 | 38 |  | 0.3 | ok |
| `/exports?fmt=jde` | clerk | page | 200 | 0.202 | 0.194 | 416 | 42 |  | 0.3 | ok |
| `/settings/automation` | admin | page | 200 | 0.192 | 0.184 | 15 | 23 |  | 0.3 | ok |
| `/review?tab=approved` | admin | page | 200 | 0.188 | 0.204 | 44 | 45 |  | 0.3 | ok |
| `/search?q=Frontier` | admin | report | 200 | 0.186 | 0.206 | 160 | 116 | 9.2 | 3 | ok |
| `/search/7/approved.pdf` | clerk | export | 200 | 0.181 | 0.165 | 18 | 12 | 0.2 | 3 | ok |
| `/review/7/approved.pdf` | clerk | export | 200 | 0.179 | 0.159 | 18 | 12 | 0.2 | 3 | ok |
| `/exports/jde/dry-run.txt` | admin | export | 200 | 0.175 | 0.123 | 115 | 19 | 19.1 | 3 | ok |
| `/review/7/approved.pdf` | admin | export | 200 | 0.171 | 0.171 | 18 | 12 | 0.2 | 3 | ok |
| `/review?tab=approved` | clerk | page | 200 | 0.165 | 0.165 | 44 | 45 |  | 0.3 | ok |
| `/search/7/approved.pdf` | admin | export | 200 | 0.159 | 0.172 | 18 | 12 | 0.2 | 3 | ok |
| `/activity` | admin | page | 200 | 0.142 | 0.172 | 115 | 36 |  | 0.3 | ok |
| `/exports/jde/dry-run.txt` | clerk | export | 200 | 0.136 | 0.118 | 115 | 19 | 19.1 | 3 | ok |
| `/settings/jde` | admin | page | 200 | 0.119 | 0.144 | 42 | 23 |  | 0.3 | ok |
| `/review?tab=other` | admin | page | 200 | 0.109 | 0.271 | 1,419 | 43 |  | 0.3 | ok |
| `/review?tab=other` | clerk | page | 200 | 0.104 | 0.110 | 988 | 43 |  | 0.3 | ok |
| `/purchase-orders?status=all` | clerk | page | 200 | 0.092 | 0.100 | 149 | 17 |  | 0.3 | ok |
| `/purchase-orders?status=all` | admin | page | 200 | 0.086 | 0.089 | 150 | 17 |  | 0.3 | ok |
| `/purchase-orders` | clerk | page | 200 | 0.084 | 0.085 | 149 | 17 |  | 0.3 | ok |
| `/purchase-orders` | admin | page | 200 | 0.084 | 0.107 | 150 | 17 |  | 0.3 | ok |
| `/review` | clerk | page | 200 | 0.083 | 0.075 | 82 | 47 |  | 0.3 | ok |
| `/review?sort=amount` | admin | page | 200 | 0.082 | 0.086 | 84 | 47 |  | 0.3 | ok |
| `/exports/duplicates.csv` | admin | export | 200 | 0.080 | 7.078 | 221 | 168 | 1.6 | 3 | ok |
| `/month-end/accruals.csv` | clerk | export | 200 | 0.079 | 0.076 | 756 | 110 | 5.0 | 3 | ok |
| `/month-end/accruals.csv` | admin | export | 200 | 0.076 | 0.078 | 756 | 110 | 5.0 | 3 | ok |
| `/review?sort=amount` | clerk | page | 200 | 0.073 | 0.071 | 84 | 47 |  | 0.3 | ok |
| `/exports/duplicates.csv` | clerk | export | 200 | 0.072 | 0.076 | 221 | 50 | 1.6 | 3 | ok |
| `/review?tab=parked` | admin | page | 200 | 0.071 | 0.062 | 101 | 42 |  | 0.3 | ok |
| `/settings/data` | admin | page | 200 | 0.071 | 0.104 | 21 | 26 |  | 0.3 | ok |
| `/review` | admin | page | 200 | 0.070 | 1.015 | 83 | 68 |  | 0.3 | ok |
| `/learning?tab=suppliers` | clerk | page | 200 | 0.070 | 0.093 | 157 | 17 |  | 0.3 | ok |
| `/search?q=FTC6962398` | clerk | report | 200 | 0.069 | 0.084 | 30 | 254 | 0.2 | 3 | ok |
| `/learning?tab=suppliers` | admin | page | 200 | 0.067 | 2.627 | 229 | 19 |  | 0.3 | ok |
| `/review?tab=second` | admin | page | 200 | 0.066 | 0.069 | 152 | 43 |  | 0.3 | ok |
| `/review?show=attention` | admin | page | 200 | 0.066 | 0.071 | 83 | 46 |  | 0.3 | ok |
| `/review?show=ready` | admin | page | 200 | 0.063 | 0.076 | 10 | 45 |  | 0.3 | ok |
| `/review?show=attention` | clerk | page | 200 | 0.063 | 0.069 | 83 | 46 |  | 0.3 | ok |
| `/search?q=FTC6962398` | admin | report | 200 | 0.063 | 0.063 | 30 | 253 | 0.2 | 3 | ok |
| `/review?tab=parked` | clerk | page | 200 | 0.062 | 0.060 | 101 | 42 |  | 0.3 | ok |
| `/review?show=ready` | clerk | page | 200 | 0.062 | 0.057 | 10 | 45 |  | 0.3 | ok |
| `/review?tab=second` | clerk | page | 200 | 0.062 | 0.071 | 67 | 43 |  | 0.3 | ok |
| `/help/report` | clerk | report | 200 | 0.061 | 0.064 | 146 | 139 | 5.8 | 3 | ok |
| `/help/report` | admin | report | 200 | 0.059 | 0.321 | 150 | 139 | 5.8 | 3 | ok |
| `/review/98499` | clerk | page | 200 | 0.058 | 0.058 | 303 | 98 |  | 0.3 | ok |
| `/accounts?tab=rules` | admin | page | 200 | 0.057 | 0.060 | 456 | 20 |  | 0.3 | ok |
| `/review/98499` | admin | page | 200 | 0.056 | 3.332 | 304 | 127 |  | 0.3 | ok |
| `/process` | admin | page | 200 | 0.056 | 1.202 | 16 | 50 |  | 0.3 | ok |
| `/vendors` | admin | page | 200 | 0.053 | 0.827 | 193 | 24 |  | 0.3 | ok |
| `/vendors` | clerk | page | 200 | 0.053 | 0.057 | 192 | 21 |  | 0.3 | ok |
| `/vendors?q=Frontier` | clerk | page | 200 | 0.051 | 0.056 | 144 | 21 |  | 0.3 | ok |
| `/vendors?q=Frontier` | admin | page | 200 | 0.049 | 0.051 | 145 | 21 |  | 0.3 | ok |
| `/statements` | admin | report | 200 | 0.047 | 0.601 | 269 | 23 | 6.4 | 3 | ok |
| `/accounts?tab=gl` | clerk | page | 200 | 0.045 | 0.041 | 1,184 | 18 |  | 0.3 | ok |
| `/statements` | clerk | report | 200 | 0.044 | 0.050 | 268 | 17 | 6.4 | 3 | ok |
| `/accounts?tab=rules` | clerk | page | 200 | 0.043 | 0.054 | 9 | 20 |  | 0.3 | ok |
| `/accounts?tab=tax` | clerk | page | 200 | 0.042 | 0.034 | 473 | 19 |  | 0.3 | ok |
| `/exports` | admin | page | 200 | 0.040 | 0.270 | 348 | 47 |  | 0.3 | ok |
| `/accounts?tab=gl` | admin | page | 200 | 0.038 | 0.120 | 1,187 | 18 |  | 0.3 | ok |
| `/process` | clerk | page | 200 | 0.034 | 1.370 | 15 | 48 |  | 0.3 | ok |
| `/accounts?tab=tax` | admin | page | 200 | 0.033 | 0.033 | 474 | 19 |  | 0.3 | ok |
| `/exports` | clerk | page | 200 | 0.033 | 0.268 | 315 | 38 |  | 0.3 | ok |
| `/accounts?tab=bank` | clerk | page | 200 | 0.029 | 0.031 | 195 | 19 |  | 0.3 | ok |
| `/review/97005` | admin | page | 200 | 0.028 | 0.028 | 27 | 37 |  | 0.3 | ok |
| `/review/97005` | clerk | page | 200 | 0.027 | 0.022 | 26 | 37 |  | 0.3 | ok |
| `/learning?tab=lessons` | admin | page | 200 | 0.027 | 0.494 | 634 | 16 |  | 0.3 | ok |
| `/review/97068` | admin | page | 200 | 0.025 | 0.029 | 26 | 36 |  | 0.3 | ok |
| `/learning?tab=lessons` | clerk | page | 200 | 0.025 | 0.308 | 482 | 14 |  | 0.3 | ok |
| `/admin/users` | admin | page | 200 | 0.023 | 0.177 | 814 | 15 |  | 0.3 | ok |
| `/accounts?tab=bank` | admin | page | 200 | 0.022 | 0.027 | 474 | 23 |  | 0.3 | ok |
| `/review/7` | admin | page | 200 | 0.022 | 0.026 | 21 | 36 |  | 0.3 | ok |
| `/review/97068` | clerk | page | 200 | 0.022 | 0.024 | 26 | 36 |  | 0.3 | ok |
| `/review/7` | clerk | page | 200 | 0.021 | 0.023 | 20 | 36 |  | 0.3 | ok |
| `/exports/batches/256/download` | clerk | page | 200 | 0.020 | 0.020 | 28 | 18 |  | 0.3 | ok |
| `/accounts?tab=cc` | clerk | page | 200 | 0.019 | 0.022 | 187 | 18 |  | 0.3 | ok |
| `/accounts?tab=cc` | admin | page | 200 | 0.019 | 0.018 | 190 | 18 |  | 0.3 | ok |
| `/search` | admin | page | 200 | 0.018 | 0.081 | 141 | 25 |  | 0.3 | ok |
| `/accounts/gl_accounts.csv` | admin | export | 200 | 0.017 | 0.061 | 107 | 10 | 1.1 | 3 | ok |
| `/accounts/gl_accounts.csv` | clerk | export | 200 | 0.017 | 0.018 | 107 | 10 | 1.1 | 3 | ok |
| `/search?page=40` | clerk | page | 200 | 0.016 | 0.016 | 152 | 24 |  | 0.3 | ok |
| `/search?page=40` | admin | page | 200 | 0.016 | 0.017 | 152 | 21 |  | 0.3 | ok |
| `/exports/batches/256/download` | admin | page | 200 | 0.016 | 0.019 | 28 | 18 |  | 0.3 | ok |
| `/accounts?tab=policy` | admin | page | 200 | 0.016 | 0.015 | 9 | 17 |  | 0.3 | ok |
| `/search` | clerk | page | 200 | 0.016 | 0.016 | 141 | 28 |  | 0.3 | ok |
| `/review/vendors.json` | admin | page | 200 | 0.015 | 0.017 | 3,028 | 5 |  | 0.3 | ok |
| `/settings/reading` | admin | page | 200 | 0.014 | 2.512 | 19 | 31 |  | 0.3 | ok |
| `/accounts?tab=policy` | clerk | page | 200 | 0.014 | 0.016 | 9 | 17 |  | 0.3 | ok |
| `/settings/about` | admin | page | 200 | 0.013 | 0.019 | 10 | 18 |  | 0.3 | ok |
| `/activity/controls-report` | clerk | report | 200 | 0.013 | 0.016 | 22 | 27 | 0.2 | 3 | ok |
| `/review/98499/words` | admin | page | 200 | 0.013 | 0.018 | 8 | 4 |  | 0.3 | ok |
| `/activity/controls-report` | admin | report | 200 | 0.013 | 0.016 | 22 | 27 | 0.2 | 3 | ok |
| `/spend` | admin | report | 200 | 0.013 | 2.850 | 36 | 49 | 0.5 | 3 | ok |
| `/review/vendors.json` | clerk | page | 200 | 0.012 | 0.014 | 3,028 | 5 |  | 0.3 | ok |
| `/statements/reconciliation.csv` | admin | export | 404 | 0.012 | 0.018 | 7 | 15 |  | 3 | ok |
| `/learning/training.zip` | admin | export | 404 | 0.012 | 0.332 | 7 | 16 |  | 3 | ok |
| `/review/98499/words` | clerk | page | 200 | 0.012 | 0.016 | 8 | 4 |  | 0.3 | ok |
| `/statements/reconciliation.csv` | clerk | export | 404 | 0.012 | 0.014 | 6 | 15 |  | 3 | ok |
| `/help` | clerk | page | 200 | 0.012 | 0.012 | 124 | 16 |  | 0.3 | ok |
| `/accounts/cost_centers.csv` | admin | export | 200 | 0.011 | 0.012 | 9 | 10 | 0.3 | 3 | ok |
| `/spend/data.xlsx` | clerk | export | 404 | 0.011 | 0.012 | 6 | 15 |  | 3 | ok |
| `/spend/data.xlsx` | admin | export | 404 | 0.011 | 0.012 | 7 | 15 |  | 3 | ok |
| `/help` | admin | page | 200 | 0.011 | 0.028 | 126 | 16 |  | 0.3 | ok |
| `/accounts/cost_centers.csv` | clerk | export | 200 | 0.010 | 0.011 | 9 | 10 | 0.3 | 3 | ok |
| `/spend` | clerk | report | 200 | 0.010 | 0.011 | 36 | 17 | 0.4 | 3 | ok |
| `/settings/review` | admin | page | 200 | 0.010 | 0.010 | 12 | 30 |  | 0.3 | ok |
| `/settings/hub` | admin | page | 200 | 0.010 | 0.021 | 14 | 27 |  | 0.3 | ok |
| `/settings` | admin | page | 200 | 0.009 | 0.023 | 12 | 30 |  | 0.3 | ok |
| `/account/password` | clerk | page | 200 | 0.009 | 0.007 | 7 | 13 |  | 0.3 | ok |
| `/insights` | clerk | report | 200 | 0.009 | 0.011 | 39 | 19 | 0.3 | 3 | ok |
| `/accounts/bank/template.csv` | clerk | export | 200 | 0.009 | 0.009 | 0 | 8 | 0.1 | 3 | ok |
| `/help/quick-start` | clerk | page | 200 | 0.009 | 0.009 | 11 | 11 |  | 0.3 | ok |
| `/process/status` | admin | page | 200 | 0.009 | 0.011 | 0 | 4 |  | 0.3 | ok |
| `/help/quick-start` | admin | page | 200 | 0.009 | 0.017 | 11 | 11 |  | 0.3 | ok |
| `/insights` | admin | report | 200 | 0.008 | 1.482 | 39 | 44 | 0.3 | 3 | ok |
| `/accounts/bank/template.csv` | admin | export | 200 | 0.008 | 0.008 | 0 | 8 | 0.1 | 3 | ok |
| `/purchase-orders/template.csv` | admin | export | 200 | 0.008 | 0.010 | 0 | 8 | 0.1 | 3 | ok |
| `/account/password` | admin | page | 200 | 0.008 | 0.012 | 8 | 13 |  | 0.3 | ok |
| `/settings/azure-check` | admin | page | 200 | 0.008 | 0.014 | 7 | 16 |  | 0.3 | ok |
| `/review/98499/reader` | clerk | page | 200 | 0.008 | 0.008 | 0 | 12 |  | 0.3 | ok |
| `/healthz` | admin | page | 200 | 0.008 | 0.009 | 0 | 2 |  | 0.3 | ok |
| `/learning?tab=readers` | clerk | page | 200 | 0.008 | 0.010 | 7 | 12 |  | 0.3 | ok |
| `/login/authenticator` | admin | page | 303 | 0.007 | 0.007 | 0 | 3 |  | 0.3 | ok |
| `/learning?tab=readers` | admin | page | 200 | 0.007 | 0.007 | 7 | 12 |  | 0.3 | ok |
| `/help/report/setup` | admin | report | 200 | 0.007 | 0.972 | 3 | 369 | 0.1 | 3 | ok |
| `/` | admin | page | 307 | 0.007 | 0.007 | 0 | 2 |  | 0.3 | ok |
| `/login/code` | admin | page | 303 | 0.007 | 0.007 | 0 | 3 |  | 0.3 | ok |
| `/review/98499/reader` | admin | page | 200 | 0.007 | 0.009 | 0 | 12 |  | 0.3 | ok |
| `/review/98499/page/1.png` | admin | page | 200 | 0.007 | 0.008 | 63 | 4 |  | 0.3 | ok |
| `/purchase-orders/template.csv` | clerk | export | 200 | 0.007 | 0.009 | 0 | 8 | 0.1 | 3 | ok |
| `/jobs/process` | clerk | page | 200 | 0.007 | 0.008 | 0 | 4 |  | 0.3 | ok |
| `/jobs/process` | admin | page | 200 | 0.007 | 0.009 | 0 | 4 |  | 0.3 | ok |
| `/login/new-password` | admin | page | 303 | 0.006 | 0.007 | 0 | 3 |  | 0.3 | ok |
| `/review/98499/page/1.png` | clerk | page | 200 | 0.006 | 0.008 | 63 | 4 |  | 0.3 | ok |
| `/` | clerk | page | 307 | 0.006 | 0.007 | 0 | 2 |  | 0.3 | ok |
| `/who` | admin | page | 303 | 0.006 | 0.007 | 0 | 3 |  | 0.3 | ok |
| `/login` | admin | page | 303 | 0.006 | 0.007 | 0 | 3 |  | 0.3 | ok |
| `/process/status` | clerk | page | 200 | 0.006 | 0.010 | 0 | 4 |  | 0.3 | ok |
| `/healthz` | clerk | page | 200 | 0.006 | 0.006 | 0 | 2 |  | 0.3 | ok |
| `/login/new-password` | clerk | page | 303 | 0.006 | 0.006 | 0 | 3 |  | 0.3 | ok |
| `/who` | clerk | page | 303 | 0.005 | 0.007 | 0 | 3 |  | 0.3 | ok |
| `/login` | clerk | page | 303 | 0.005 | 0.005 | 0 | 3 |  | 0.3 | ok |
| `/login/authenticator` | clerk | page | 303 | 0.005 | 0.006 | 0 | 3 |  | 0.3 | ok |
| `/login/code` | clerk | page | 303 | 0.005 | 0.005 | 0 | 3 |  | 0.3 | ok |

## Full scans of big tables

Each `SCAN` in the plan of a statement a warm request ran, on a table with 12,940 rows or more; `how`: the table itself or an index read whole.

| table | rows | how | routes | statement |
|---|---:|---|---|---|
| invoices | 100,000 | table | `/help/report` | `SELECT id, source_path, status, error, meta, validation FROM invoices WHERE id IN (SELECT id FROM invoices ORDER BY id DESC LIMIT ?) ORDER BY id` |
| invoices | 100,000 | COVERING INDEX invoices_total | `/process` | `SELECT (SELECT value FROM settings WHERE key = ?), COUNT(*), MAX(id), (SELECT MAX(id) FROM import_batches) FROM invoices` |
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
| invoice_summary | 100,000 | table | `/review` | `SELECT json_group_array(json_array(CASE typeof(q."vendor_key") WHEN ? THEN json_object(?, printf(?, q."vendor_key")) WHEN ? THEN q."vendor_key" \|\| ? WHEN ? THEN json_object(?, hex(q."vendor_key")) ELSE q."vendor_key" END` |
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

## Statements one request runs many times

The statements a warm request ran 20 times or more (one per row: an N+1), as admin.

| address | times | statement |
|---|---:|---|
| `/activity/events.csv` | 208 | `SELECT e.*, i.vendor_name AS invoice_vendor, i.invoice_number AS invoice_number FROM events e LEFT JOIN invoices i ON i.id = e.invoice_id WHERE ? = ? AND e.id < ? ORDER BY e.id DESC LIMIT ?` |
| `/activity/events.csv` | 45 | `PRAGMA journal_mode=WAL` |
| `/activity/events.csv` | 33 | `SELECT value FROM settings WHERE key = ?` |
| `/activity/events.csv` | 30 | `SELECT * FROM report_jobs WHERE id = ? AND db = ?` |
| `/month-end` | 139 | `WITH coded AS (SELECT id, COALESCE(NULLIF(final_output, ?), NULLIF(ai_output, ?), ?) AS doc FROM invoices WHERE id IN (SELECT value FROM json_each(?))), read AS (SELECT id, doc, CASE WHEN NOT json_val` |
| `/month-end` | 50 | `SELECT final_gl gl_code, COUNT(*) lines, SUM(outcome = ?) corrected FROM feedback WHERE vendor_key = ? GROUP BY final_gl ORDER BY lines DESC` |
| `/month-end` | 25 | `SELECT ? FROM summary_dirty LIMIT ?` |
| `/search?q=FTC6962398` | 224 | `-- SELECT pgno FROM ?.? WHERE segid=? AND term<=? ORDER BY term DESC LIMIT ?` |
| `/search/export.xlsx` | 58 | `SELECT ? FROM summary_dirty LIMIT ?` |
| `/search/export.xlsx` | 50 | `SELECT json_group_array(json_array(CASE typeof(q."id") WHEN ? THEN json_object(?, printf(?, q."id")) WHEN ? THEN q."id" \|\| ? WHEN ? THEN json_object(?, hex(q."id")) ELSE q."id" END, CASE typeof(q."sta` |
| `/search/export.xlsx` | 50 | `SELECT id, created_at FROM export_batches WHERE id IN (SELECT value FROM json_each(?))` |
| `/search/export.xlsx` | 23 | `PRAGMA journal_mode=WAL` |
| `/search/export.csv` | 57 | `SELECT ? FROM summary_dirty LIMIT ?` |
| `/search/export.csv` | 50 | `SELECT json_group_array(json_array(CASE typeof(q."id") WHEN ? THEN json_object(?, printf(?, q."id")) WHEN ? THEN q."id" \|\| ? WHEN ? THEN json_object(?, hex(q."id")) ELSE q."id" END, CASE typeof(q."sta` |
| `/search/export.csv` | 50 | `SELECT id, created_at FROM export_batches WHERE id IN (SELECT value FROM json_each(?))` |
| `/exports/duplicates.csv` | 36 | `PRAGMA journal_mode=WAL` |
| `/exports/duplicates.csv` | 25 | `SELECT value FROM settings WHERE key = ?` |
| `/exports/duplicates.csv` | 22 | `SELECT * FROM report_jobs WHERE id = ? AND db = ?` |
| `/search?q=Frontier` | 84 | `-- SELECT pgno FROM ?.? WHERE segid=? AND term<=? ORDER BY term DESC LIMIT ?` |
| `/month-end/accruals.csv` | 50 | `SELECT final_gl gl_code, COUNT(*) lines, SUM(outcome = ?) corrected FROM feedback WHERE vendor_key = ? GROUP BY final_gl ORDER BY lines DESC` |
| `/spend` | 21 | `SELECT ? FROM summary_dirty LIMIT ?` |

## Not asked

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
