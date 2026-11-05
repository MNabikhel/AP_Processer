# Volume after phase 2 part 2: one person on 100,000 invoices (5 Nov 2026)

The same seed as before ([phase2-volume-100k.md](phase2-volume-100k.md)), made again by this code (schema 25, a CAD and
a USD bank account in use: B-1739): 100,000 invoices, 415,765 events, 421,954 lines of feedback, 200,000 register rows.
Measured on the 4 cores with nothing else running, Python 3.12.

**In short: 167 addresses, 11 over target (part 1: 38, the baseline: 43), 0 errors.** Month-end 6 s → 0.4 s (its first
request in a process 8.6 s), Import invoices 1.3-1.5 s → 0.05 s, Spend 3.5 s → 0.02 s, Settings → Reading 2.8 s →
0.02 s, Supplier learning 2.8 s → 0.12 s, the duplicates CSV 7.2-7.8 s → 0.03 s (its first request in a process 11
s), the JD Edwards exports page 1.4 s → 0.3-0.5 s, Learning → Coding accuracy → 0.34 s, an invoice 0.9-1.2 s → 0.1 s.

**Still over:** the Activity CSV (12-16 s, streamed as it is read: the download starts at once), search to Excel
(12 s, almost all openpyxl writing 450,000 cells), the duplicates CSV's first request in a process (11 s), Activity and
its search (0.2-0.6 s), the JD Edwards exports page for Admin (0.5 s), Learning → Coding accuracy (0.34-0.43 s).

**Against main, like for like** (main's own seed and this one, the same addresses, one after the other): Activity, its
search, the Activity CSV and search to Excel the same (within 10%: the part 1 figures came from a run with other work on
the machine); the duplicates CSV 9.2 s → 0.025 s, the JD Edwards exports page 1.4 s → 0.33-0.40 s, Learning → Coding
accuracy 1.6-2.0 s → 0.34 s; search to CSV 1.51 s → 1.80 s (B-1735: the directory read in one step costs about 15% more
alone, and takes a 50,000-row export under three busy threads from 132 s to 0.65 s).

## Before and after (addresses over target in any run; "first request": the first in a process)

| address | role | baseline s | part 1 s | part 2 s | first request s | target s | now |
|---|---|---:|---:|---:|---:|---:|---|
| `/spend/data.xlsx` | admin | 251.251 | 0.014 | 0.023 | 0.023 | 3 | ok |
| `/spend/data.xlsx` | clerk | 245.279 | 0.013 | 0.018 | 0.019 | 3 | ok |
| `/month-end` | admin | 201.710 | 6.059 | 0.419 | 8.554 | 3 | ok |
| `/month-end/accruals.csv` | admin | 195.209 | 5.922 | 0.055 | 0.055 | 3 | ok |
| `/month-end/accruals.csv` | clerk | 193.577 | 5.939 | 0.059 | 0.061 | 3 | ok |
| `/month-end` | clerk | 190.093 | 5.604 | 0.390 | 0.383 | 3 | ok |
| `/help/report` | admin | 22.256 | 0.103 | 0.109 | 0.117 | 3 | ok |
| `/activity?q=Frontier` | admin | 19.621 | 0.316 | 0.614 | 0.732 | 0.3 | over |
| `/activity?q=Frontier` | clerk | 19.567 | 0.317 | 0.463 | 0.586 | 0.3 | over |
| `/help/report` | clerk | 19.141 | 0.102 | 0.103 | 0.110 | 3 | ok |
| `/activity/controls-report` | clerk | 17.322 | 0.093 | 0.018 | 0.030 | 3 | ok |
| `/activity/controls-report` | admin | 17.305 | 0.015 | 0.024 | 0.032 | 3 | ok |
| `/activity/events.csv` | admin | 0.066 | 11.595 | 16.294 | 16.294 | 3 | over |
| `/exports/duplicates.csv` | admin | 13.558 | 7.790 | 10.956 | 10.956 | 3 | over |
| `/exports/duplicates.csv` | clerk | 13.534 | 7.216 | 0.025 | 0.028 | 3 | ok |
| `/search/export.xlsx` | clerk | 8.550 | 9.225 | 12.591 | 12.591 | 3 | over |
| `/activity` | clerk | 12.397 | 0.265 | 0.401 | 0.409 | 0.3 | over |
| `/activity` | admin | 12.364 | 0.159 | 0.219 | 0.242 | 0.3 | ok |
| `/activity/events.csv` | clerk | 0.054 | 10.512 | 12.346 | 12.346 | 3 | over |
| `/search/export.xlsx` | admin | 7.484 | 8.618 | 12.238 | 12.238 | 3 | over |
| `/learning?tab=suppliers` | clerk | 7.953 | 2.791 | 0.133 | 0.150 | 0.3 | ok |
| `/learning?tab=suppliers` | admin | 7.915 | 2.800 | 0.122 | 4.453 | 0.3 | ok |
| `/process` | clerk | 7.551 | 1.533 | 0.056 | 1.845 | 0.3 | ok |
| `/process` | admin | 6.383 | 1.314 | 0.049 | 1.936 | 0.3 | ok |
| `/spend` | admin | 4.824 | 3.713 | 0.020 | 5.016 | 3 | ok |
| `/spend` | clerk | 4.790 | 3.491 | 0.018 | 0.018 | 3 | ok |
| `/settings/automation` | admin | 4.729 | 2.692 | 0.298 | 0.341 | 0.3 | ok |
| `/exports?fmt=jde` | clerk | 4.319 | 1.507 | 0.299 | 0.701 | 0.3 | ok |
| `/exports?fmt=jde` | admin | 4.133 | 1.435 | 0.502 | 0.354 | 0.3 | over |
| `/settings/jde/worksheet.xlsx` | admin | 3.451 | 4.033 | 1.030 | 0.900 | 3 | ok |
| `/exports/jde/dry-run.txt` | clerk | 3.790 | 1.351 | 0.194 | 0.208 | 3 | ok |
| `/exports/jde/dry-run.txt` | admin | 3.691 | 1.243 | 0.186 | 0.391 | 3 | ok |
| `/settings/reading` | admin | 2.425 | 2.805 | 0.019 | 3.658 | 0.3 | ok |
| `/search?q=FTC6962398` | clerk | 2.725 | 0.056 | 0.089 | 0.090 | 3 | ok |
| `/search?q=FTC6962398` | admin | 2.530 | 0.065 | 0.083 | 0.086 | 3 | ok |
| `/sales-tax` | clerk | 1.975 | 2.050 | 1.832 | 1.585 | 3 | ok |
| `/search/export.csv` | clerk | 1.319 | 1.243 | 2.012 | 2.627 | 3 | ok |
| `/sales-tax` | admin | 1.939 | 1.903 | 1.705 | 1.750 | 3 | ok |
| `/insights` | clerk | 1.256 | 1.915 | 0.022 | 0.015 | 3 | ok |
| `/search/export.csv` | admin | 1.012 | 1.196 | 1.900 | 2.047 | 3 | ok |
| `/insights` | admin | 1.377 | 1.784 | 0.015 | 2.378 | 3 | ok |
| `/search?q=Frontier` | clerk | 1.632 | 0.220 | 0.376 | 0.324 | 3 | ok |
| `/search?q=Frontier` | admin | 1.609 | 0.234 | 0.274 | 0.283 | 3 | ok |
| `/learning?tab=coding` | clerk | 1.282 | 1.417 | 0.430 | 0.427 | 0.3 | over |
| `/learning?tab=coding` | admin | 1.242 | 1.378 | 0.395 | 1.912 | 0.3 | over |
| `/sales-tax/claims.csv` | admin | 0.685 | 0.762 | 1.238 | 0.877 | 3 | ok |
| `/sales-tax/claims.csv` | clerk | 0.696 | 0.786 | 1.230 | 1.458 | 3 | ok |
| `/review/98499` | clerk | 0.626 | 1.184 | 0.089 | 0.091 | 0.3 | ok |
| `/settings/data` | admin | 0.777 | 1.177 | 0.106 | 0.168 | 0.3 | ok |
| `/review/98499` | admin | 0.689 | 0.945 | 0.097 | 5.194 | 0.3 | ok |
| `/insights/business-case` | admin | 0.376 | 0.470 | 0.811 | 0.777 | 3 | ok |
| `/insights/business-case` | clerk | 0.409 | 0.443 | 0.754 | 0.747 | 3 | ok |
| `/sales-tax/self-assessment.csv` | clerk | 0.526 | 0.617 | 0.679 | 0.628 | 3 | ok |
| `/sales-tax/self-assessment.csv` | admin | 0.484 | 0.621 | 0.622 | 0.796 | 3 | ok |
| `/learning?tab=lessons` | clerk | 0.355 | 0.595 | 0.040 | 0.046 | 0.3 | ok |
| `/learning?tab=lessons` | admin | 0.503 | 0.461 | 0.045 | 0.490 | 0.3 | ok |
| `/vendors` | admin | 0.408 | 0.501 | 0.105 | 1.443 | 0.3 | ok |
| `/review?tab=approved` | clerk | 0.338 | 0.460 | 0.262 | 0.260 | 0.3 | ok |
| `/exports/batches/254/approved-pdfs.zip` | clerk | 0.262 | 0.451 |  |  | 0.3 |  |
| `/purchase-orders?status=all` | clerk | 0.205 | 0.392 | 0.146 | 0.146 | 0.3 | ok |
| `/vendors` | clerk | 0.329 | 0.369 | 0.115 | 0.101 | 0.3 | ok |
| `/purchase-orders` | admin | 0.349 | 0.364 | 0.137 | 0.185 | 0.3 | ok |
| `/exports/batches/254/approved-pdfs.zip` | admin | 0.237 | 0.353 |  |  | 0.3 |  |
| `/review?tab=approved` | admin | 0.333 | 0.285 | 0.260 | 0.268 | 0.3 | ok |
| `/purchase-orders` | clerk | 0.219 | 0.323 | 0.209 | 0.165 | 0.3 | ok |
| `/review?sort=amount` | admin | 0.225 | 0.308 | 0.138 | 0.133 | 0.3 | ok |
| `/review?show=ready` | admin | 0.213 | 0.307 | 0.114 | 0.167 | 0.3 | ok |
| `/statements` | admin | 0.211 | 0.306 | 0.134 | 1.085 | 3 | ok |

## This run in full

**167 addresses, 11 over target, 0 errors (5xx), 51 full scans of big tables.** Targets: a page 300 ms, a report, export or search 3 s.

| address | role | kind | status | median s | warm s | KB | SQL | peak MB | target | |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| `/activity/events.csv` | admin | export | 200 | 16.294 | 16.294 | 62,718 | 425 |  | 3 | **over** |
| `/search/export.xlsx` | clerk | export | 200 | 12.591 | 12.591 | 3,182 | 165 |  | 3 | **over** |
| `/activity/events.csv` | clerk | export | 200 | 12.346 | 12.346 | 54,902 | 305 |  | 3 | **over** |
| `/search/export.xlsx` | admin | export | 200 | 12.238 | 12.238 | 3,182 | 163 |  | 3 | **over** |
| `/exports/duplicates.csv` | admin | export | 200 | 10.956 | 10.956 | 221 | 16 |  | 3 | **over** |
| `/search/export.csv` | clerk | export | 200 | 2.012 | 2.627 | 10,239 | 165 | 43.7 | 3 | ok |
| `/search/export.csv` | admin | export | 200 | 1.900 | 2.047 | 10,239 | 163 | 43.7 | 3 | ok |
| `/sales-tax` | clerk | report | 200 | 1.832 | 1.585 | 213 | 25 | 39.1 | 3 | ok |
| `/sales-tax` | admin | report | 200 | 1.705 | 1.750 | 213 | 27 | 39.0 | 3 | ok |
| `/sales-tax/claims.csv` | admin | export | 200 | 1.238 | 0.877 | 4,272 | 14 | 44.7 | 3 | ok |
| `/sales-tax/claims.csv` | clerk | export | 200 | 1.230 | 1.458 | 4,272 | 14 | 44.7 | 3 | ok |
| `/settings/jde/worksheet.xlsx` | admin | export | 200 | 1.030 | 0.900 | 122 | 17 | 12.9 | 3 | ok |
| `/insights/business-case` | admin | report | 200 | 0.811 | 0.777 | 3 | 26 | 13.6 | 3 | ok |
| `/insights/business-case` | clerk | report | 200 | 0.754 | 0.747 | 3 | 26 | 13.6 | 3 | ok |
| `/sales-tax/self-assessment.csv` | clerk | export | 200 | 0.679 | 0.628 | 0 | 17 | 21.3 | 3 | ok |
| `/sales-tax/self-assessment.csv` | admin | export | 200 | 0.622 | 0.796 | 0 | 17 | 21.3 | 3 | ok |
| `/activity?q=Frontier` | admin | page | 200 | 0.614 | 0.732 | 132 | 39 |  | 0.3 | **over** |
| `/exports?fmt=jde` | admin | page | 200 | 0.502 | 0.354 | 449 | 44 |  | 0.3 | **over** |
| `/activity?q=Frontier` | clerk | page | 200 | 0.463 | 0.586 | 131 | 36 |  | 0.3 | **over** |
| `/learning?tab=coding` | clerk | page | 200 | 0.430 | 0.427 | 122 | 28 |  | 0.3 | **over** |
| `/month-end` | admin | report | 200 | 0.419 | 8.554 | 1,977 | 230 | 12.4 | 3 | ok |
| `/activity` | clerk | page | 200 | 0.401 | 0.409 | 131 | 35 |  | 0.3 | **over** |
| `/search/9/approved.pdf` | clerk | export | 200 | 0.400 | 0.373 | 18 | 12 | 0.2 | 3 | ok |
| `/learning?tab=coding` | admin | page | 200 | 0.395 | 1.912 | 164 | 38 |  | 0.3 | **over** |
| `/month-end` | clerk | report | 200 | 0.390 | 0.383 | 1,977 | 74 | 12.4 | 3 | ok |
| `/search?q=Frontier` | clerk | report | 200 | 0.376 | 0.324 | 160 | 116 | 9.2 | 3 | ok |
| `/review/9/approved.pdf` | admin | export | 200 | 0.338 | 0.358 | 18 | 12 | 0.2 | 3 | ok |
| `/search/9/approved.pdf` | admin | export | 200 | 0.308 | 0.319 | 18 | 12 | 0.2 | 3 | ok |
| `/exports/batches/257/approved-pdfs.zip` | clerk | export | 200 | 0.306 | 0.275 | 27 | 15 | 0.7 | 3 | ok |
| `/review/9/approved.pdf` | clerk | export | 200 | 0.299 | 0.306 | 18 | 12 | 0.2 | 3 | ok |
| `/exports?fmt=jde` | clerk | page | 200 | 0.299 | 0.701 | 415 | 42 |  | 0.3 | ok |
| `/settings/automation` | admin | page | 200 | 0.298 | 0.341 | 15 | 23 |  | 0.3 | ok |
| `/exports/batches/257/approved-pdfs.zip` | admin | export | 200 | 0.283 | 0.277 | 27 | 15 | 0.7 | 3 | ok |
| `/search?q=Frontier` | admin | report | 200 | 0.274 | 0.283 | 160 | 116 | 9.2 | 3 | ok |
| `/settings/jde` | admin | page | 200 | 0.272 | 0.256 | 42 | 23 |  | 0.3 | ok |
| `/review?tab=approved` | clerk | page | 200 | 0.262 | 0.260 | 44 | 45 |  | 0.3 | ok |
| `/review?tab=approved` | admin | page | 200 | 0.260 | 0.268 | 44 | 45 |  | 0.3 | ok |
| `/activity` | admin | page | 200 | 0.219 | 0.242 | 115 | 36 |  | 0.3 | ok |
| `/purchase-orders` | clerk | page | 200 | 0.209 | 0.165 | 149 | 17 |  | 0.3 | ok |
| `/review?tab=other` | clerk | page | 200 | 0.201 | 0.191 | 988 | 43 |  | 0.3 | ok |
| `/exports/jde/dry-run.txt` | clerk | export | 200 | 0.194 | 0.208 | 115 | 19 | 19.0 | 3 | ok |
| `/exports/jde/dry-run.txt` | admin | export | 200 | 0.186 | 0.391 | 115 | 19 | 19.0 | 3 | ok |
| `/review?tab=other` | admin | page | 200 | 0.176 | 0.180 | 1,419 | 43 |  | 0.3 | ok |
| `/purchase-orders?status=all` | admin | page | 200 | 0.161 | 0.137 | 150 | 17 |  | 0.3 | ok |
| `/purchase-orders?status=all` | clerk | page | 200 | 0.146 | 0.146 | 149 | 17 |  | 0.3 | ok |
| `/review` | admin | page | 200 | 0.142 | 1.770 | 83 | 68 |  | 0.3 | ok |
| `/review` | clerk | page | 200 | 0.139 | 0.155 | 82 | 47 |  | 0.3 | ok |
| `/review?sort=amount` | admin | page | 200 | 0.138 | 0.133 | 84 | 47 |  | 0.3 | ok |
| `/purchase-orders` | admin | page | 200 | 0.137 | 0.185 | 150 | 17 |  | 0.3 | ok |
| `/accounts?tab=rules` | clerk | page | 200 | 0.135 | 0.137 | 9 | 20 |  | 0.3 | ok |
| `/statements` | admin | report | 200 | 0.134 | 1.085 | 269 | 23 | 6.4 | 3 | ok |
| `/learning?tab=suppliers` | clerk | page | 200 | 0.133 | 0.150 | 157 | 17 |  | 0.3 | ok |
| `/review?tab=parked` | clerk | page | 200 | 0.129 | 0.115 | 101 | 42 |  | 0.3 | ok |
| `/review?tab=second` | clerk | page | 200 | 0.123 | 0.127 | 67 | 43 |  | 0.3 | ok |
| `/review?show=attention` | clerk | page | 200 | 0.123 | 0.127 | 83 | 46 |  | 0.3 | ok |
| `/learning?tab=suppliers` | admin | page | 200 | 0.122 | 4.453 | 229 | 19 |  | 0.3 | ok |
| `/review?tab=second` | admin | page | 200 | 0.117 | 0.115 | 152 | 43 |  | 0.3 | ok |
| `/review?sort=amount` | clerk | page | 200 | 0.116 | 0.117 | 84 | 47 |  | 0.3 | ok |
| `/review?show=attention` | admin | page | 200 | 0.115 | 0.359 | 83 | 46 |  | 0.3 | ok |
| `/vendors` | clerk | page | 200 | 0.115 | 0.101 | 192 | 21 |  | 0.3 | ok |
| `/review?show=ready` | admin | page | 200 | 0.114 | 0.167 | 10 | 45 |  | 0.3 | ok |
| `/review?tab=parked` | admin | page | 200 | 0.111 | 0.114 | 101 | 42 |  | 0.3 | ok |
| `/help/report` | admin | report | 200 | 0.109 | 0.117 | 150 | 131 | 5.8 | 3 | ok |
| `/review?show=ready` | clerk | page | 200 | 0.108 | 0.111 | 10 | 45 |  | 0.3 | ok |
| `/vendors?q=Frontier` | clerk | page | 200 | 0.106 | 0.106 | 143 | 21 |  | 0.3 | ok |
| `/settings/data` | admin | page | 200 | 0.106 | 0.168 | 18 | 26 |  | 0.3 | ok |
| `/vendors` | admin | page | 200 | 0.105 | 1.443 | 192 | 24 |  | 0.3 | ok |
| `/help/report` | clerk | report | 200 | 0.103 | 0.110 | 146 | 131 | 5.8 | 3 | ok |
| `/accounts?tab=rules` | admin | page | 200 | 0.101 | 0.121 | 456 | 20 |  | 0.3 | ok |
| `/vendors?q=Frontier` | admin | page | 200 | 0.098 | 0.107 | 144 | 21 |  | 0.3 | ok |
| `/review/98499` | admin | page | 200 | 0.097 | 5.194 | 304 | 127 |  | 0.3 | ok |
| `/statements` | clerk | report | 200 | 0.092 | 0.095 | 268 | 17 | 6.4 | 3 | ok |
| `/review/98499` | clerk | page | 200 | 0.089 | 0.091 | 303 | 98 |  | 0.3 | ok |
| `/search?q=FTC6962398` | clerk | report | 200 | 0.089 | 0.090 | 30 | 254 | 0.2 | 3 | ok |
| `/search?q=FTC6962398` | admin | report | 200 | 0.083 | 0.086 | 30 | 253 | 0.2 | 3 | ok |
| `/accounts?tab=gl` | admin | page | 200 | 0.071 | 0.138 | 1,187 | 18 |  | 0.3 | ok |
| `/accounts?tab=gl` | clerk | page | 200 | 0.063 | 0.109 | 1,184 | 18 |  | 0.3 | ok |
| `/month-end/accruals.csv` | clerk | export | 200 | 0.059 | 0.061 | 755 | 69 | 5.0 | 3 | ok |
| `/accounts?tab=tax` | clerk | page | 200 | 0.058 | 0.056 | 473 | 19 |  | 0.3 | ok |
| `/process` | clerk | page | 200 | 0.056 | 1.845 | 15 | 47 |  | 0.3 | ok |
| `/accounts?tab=tax` | admin | page | 200 | 0.055 | 0.054 | 474 | 19 |  | 0.3 | ok |
| `/month-end/accruals.csv` | admin | export | 200 | 0.055 | 0.055 | 755 | 69 | 5.0 | 3 | ok |
| `/exports` | admin | page | 200 | 0.053 | 0.613 | 348 | 47 |  | 0.3 | ok |
| `/exports` | clerk | page | 200 | 0.050 | 0.054 | 315 | 38 |  | 0.3 | ok |
| `/process` | admin | page | 200 | 0.049 | 1.936 | 16 | 49 |  | 0.3 | ok |
| `/review/97005` | admin | page | 200 | 0.049 | 0.050 | 27 | 37 |  | 0.3 | ok |
| `/review/97068` | admin | page | 200 | 0.049 | 0.044 | 26 | 36 |  | 0.3 | ok |
| `/learning?tab=lessons` | admin | page | 200 | 0.045 | 0.490 | 634 | 16 |  | 0.3 | ok |
| `/review/97068` | clerk | page | 200 | 0.045 | 0.044 | 26 | 36 |  | 0.3 | ok |
| `/review/9` | clerk | page | 200 | 0.044 | 0.042 | 22 | 35 |  | 0.3 | ok |
| `/review/9` | admin | page | 200 | 0.043 | 0.047 | 22 | 35 |  | 0.3 | ok |
| `/accounts?tab=bank` | admin | page | 200 | 0.042 | 0.042 | 474 | 23 |  | 0.3 | ok |
| `/review/97005` | clerk | page | 200 | 0.042 | 0.046 | 26 | 37 |  | 0.3 | ok |
| `/learning?tab=lessons` | clerk | page | 200 | 0.040 | 0.046 | 482 | 14 |  | 0.3 | ok |
| `/accounts?tab=cc` | admin | page | 200 | 0.037 | 0.042 | 190 | 18 |  | 0.3 | ok |
| `/admin/users` | admin | page | 200 | 0.036 | 0.077 | 814 | 15 |  | 0.3 | ok |
| `/accounts?tab=policy` | clerk | page | 200 | 0.036 | 0.036 | 9 | 17 |  | 0.3 | ok |
| `/accounts?tab=cc` | clerk | page | 200 | 0.032 | 0.031 | 187 | 18 |  | 0.3 | ok |
| `/accounts?tab=bank` | clerk | page | 200 | 0.031 | 0.048 | 195 | 19 |  | 0.3 | ok |
| `/accounts/gl_accounts.csv` | admin | export | 200 | 0.031 | 0.032 | 107 | 10 | 1.1 | 3 | ok |
| `/search?page=40` | clerk | page | 200 | 0.028 | 0.028 | 152 | 24 |  | 0.3 | ok |
| `/settings/about` | admin | page | 200 | 0.027 | 0.037 | 10 | 18 |  | 0.3 | ok |
| `/accounts?tab=policy` | admin | page | 200 | 0.026 | 0.027 | 9 | 17 |  | 0.3 | ok |
| `/search` | clerk | page | 200 | 0.026 | 0.028 | 141 | 28 |  | 0.3 | ok |
| `/exports/duplicates.csv` | clerk | export | 200 | 0.025 | 0.028 | 221 | 11 | 1.6 | 3 | ok |
| `/statements/reconciliation.csv` | admin | export | 404 | 0.025 | 0.032 | 7 | 15 |  | 3 | ok |
| `/search?page=40` | admin | page | 200 | 0.024 | 0.024 | 152 | 21 |  | 0.3 | ok |
| `/activity/controls-report` | admin | report | 200 | 0.024 | 0.032 | 22 | 20 | 0.2 | 3 | ok |
| `/review/98499/words` | admin | page | 200 | 0.023 | 0.028 | 8 | 4 |  | 0.3 | ok |
| `/search` | admin | page | 200 | 0.023 | 0.104 | 141 | 25 |  | 0.3 | ok |
| `/review/vendors.json` | admin | page | 200 | 0.023 | 0.025 | 3,028 | 5 |  | 0.3 | ok |
| `/spend/data.xlsx` | admin | export | 404 | 0.023 | 0.023 | 7 | 15 |  | 3 | ok |
| `/accounts/gl_accounts.csv` | clerk | export | 200 | 0.023 | 0.026 | 107 | 10 | 1.1 | 3 | ok |
| `/review/98499/words` | clerk | page | 200 | 0.022 | 0.020 | 8 | 4 |  | 0.3 | ok |
| `/insights` | clerk | report | 200 | 0.022 | 0.015 | 39 | 19 | 0.3 | 3 | ok |
| `/statements/reconciliation.csv` | clerk | export | 404 | 0.022 | 0.023 | 6 | 15 |  | 3 | ok |
| `/exports/batches/257/download` | admin | page | 200 | 0.020 | 0.024 | 4 | 18 |  | 0.3 | ok |
| `/exports/batches/257/download` | clerk | page | 200 | 0.020 | 0.021 | 4 | 18 |  | 0.3 | ok |
| `/spend` | admin | report | 200 | 0.020 | 5.016 | 36 | 49 | 0.5 | 3 | ok |
| `/help` | admin | page | 200 | 0.020 | 0.605 | 126 | 16 |  | 0.3 | ok |
| `/settings/reading` | admin | page | 200 | 0.019 | 3.658 | 19 | 31 |  | 0.3 | ok |
| `/help` | clerk | page | 200 | 0.019 | 0.021 | 124 | 16 |  | 0.3 | ok |
| `/spend/data.xlsx` | clerk | export | 404 | 0.018 | 0.019 | 6 | 15 |  | 3 | ok |
| `/activity/controls-report` | clerk | report | 200 | 0.018 | 0.030 | 22 | 20 | 0.2 | 3 | ok |
| `/accounts/cost_centers.csv` | admin | export | 200 | 0.018 | 0.019 | 9 | 10 | 0.3 | 3 | ok |
| `/spend` | clerk | report | 200 | 0.018 | 0.018 | 36 | 17 | 0.4 | 3 | ok |
| `/learning/training.zip` | admin | export | 404 | 0.018 | 0.027 | 7 | 16 |  | 3 | ok |
| `/review/vendors.json` | clerk | page | 200 | 0.018 | 0.018 | 3,028 | 5 |  | 0.3 | ok |
| `/settings` | admin | page | 200 | 0.018 | 0.039 | 12 | 30 |  | 0.3 | ok |
| `/help/quick-start` | admin | page | 200 | 0.017 | 0.027 | 11 | 11 |  | 0.3 | ok |
| `/settings/review` | admin | page | 200 | 0.016 | 0.019 | 12 | 30 |  | 0.3 | ok |
| `/settings/hub` | admin | page | 200 | 0.016 | 0.030 | 14 | 27 |  | 0.3 | ok |
| `/insights` | admin | report | 200 | 0.015 | 2.378 | 39 | 44 | 0.3 | 3 | ok |
| `/accounts/bank/template.csv` | clerk | export | 200 | 0.015 | 0.013 | 0 | 8 | 0.1 | 3 | ok |
| `/purchase-orders/template.csv` | admin | export | 200 | 0.014 | 0.014 | 0 | 8 | 0.1 | 3 | ok |
| `/login/authenticator` | clerk | page | 303 | 0.014 | 0.013 | 0 | 3 |  | 0.3 | ok |
| `/accounts/cost_centers.csv` | clerk | export | 200 | 0.014 | 0.018 | 9 | 10 | 0.3 | 3 | ok |
| `/review/98499/reader` | admin | page | 200 | 0.013 | 0.013 | 0 | 12 |  | 0.3 | ok |
| `/settings/azure-check` | admin | page | 200 | 0.013 | 0.019 | 7 | 16 |  | 0.3 | ok |
| `/login` | clerk | page | 303 | 0.013 | 0.013 | 0 | 3 |  | 0.3 | ok |
| `/login/new-password` | clerk | page | 303 | 0.013 | 0.013 | 0 | 3 |  | 0.3 | ok |
| `/account/password` | admin | page | 200 | 0.013 | 0.018 | 8 | 13 |  | 0.3 | ok |
| `/purchase-orders/template.csv` | clerk | export | 200 | 0.013 | 0.014 | 0 | 8 | 0.1 | 3 | ok |
| `/process/status` | admin | page | 200 | 0.013 | 0.014 | 0 | 3 |  | 0.3 | ok |
| `/review/98499/reader` | clerk | page | 200 | 0.013 | 0.013 | 0 | 12 |  | 0.3 | ok |
| `/who` | clerk | page | 303 | 0.013 | 0.013 | 0 | 3 |  | 0.3 | ok |
| `/` | admin | page | 307 | 0.012 | 0.013 | 0 | 2 |  | 0.3 | ok |
| `/accounts/bank/template.csv` | admin | export | 200 | 0.012 | 0.013 | 0 | 8 | 0.1 | 3 | ok |
| `/account/password` | clerk | page | 200 | 0.012 | 0.014 | 7 | 13 |  | 0.3 | ok |
| `/learning?tab=readers` | admin | page | 200 | 0.012 | 0.014 | 7 | 12 |  | 0.3 | ok |
| `/help/report/setup` | admin | report | 200 | 0.012 | 0.171 | 3 | 345 | 0.1 | 3 | ok |
| `/login/code` | admin | page | 303 | 0.012 | 0.011 | 0 | 3 |  | 0.3 | ok |
| `/login/code` | clerk | page | 303 | 0.012 | 0.014 | 0 | 3 |  | 0.3 | ok |
| `/help/quick-start` | clerk | page | 200 | 0.012 | 0.013 | 11 | 11 |  | 0.3 | ok |
| `/review/98499/page/1.png` | admin | page | 200 | 0.011 | 0.014 | 63 | 4 |  | 0.3 | ok |
| `/jobs/process` | admin | page | 200 | 0.011 | 0.012 | 0 | 2 |  | 0.3 | ok |
| `/learning?tab=readers` | clerk | page | 200 | 0.011 | 0.011 | 7 | 12 |  | 0.3 | ok |
| `/process/status` | clerk | page | 200 | 0.011 | 0.012 | 0 | 3 |  | 0.3 | ok |
| `/login/authenticator` | admin | page | 303 | 0.011 | 0.011 | 0 | 3 |  | 0.3 | ok |
| `/healthz` | admin | page | 200 | 0.011 | 0.013 | 0 | 2 |  | 0.3 | ok |
| `/who` | admin | page | 303 | 0.011 | 0.012 | 0 | 3 |  | 0.3 | ok |
| `/login/new-password` | admin | page | 303 | 0.010 | 0.011 | 0 | 3 |  | 0.3 | ok |
| `/jobs/process` | clerk | page | 200 | 0.010 | 0.008 | 0 | 2 |  | 0.3 | ok |
| `/review/98499/page/1.png` | clerk | page | 200 | 0.010 | 0.012 | 63 | 4 |  | 0.3 | ok |
| `/login` | admin | page | 303 | 0.010 | 0.011 | 0 | 3 |  | 0.3 | ok |
| `/` | clerk | page | 307 | 0.008 | 0.010 | 0 | 2 |  | 0.3 | ok |
| `/healthz` | clerk | page | 200 | 0.008 | 0.009 | 0 | 2 |  | 0.3 | ok |

## Full scans of big tables

Each `SCAN` in the plan of a statement a warm request ran, on a table with 12,940 rows or more; `how`: the table itself or an index read whole.

| table | rows | how | routes | statement |
|---|---:|---|---|---|
| invoices | 100,000 | table | `/help/report` | `SELECT id, source_path, status, error, meta, validation FROM invoices WHERE id IN (SELECT id FROM invoices ORDER BY id DESC LIMIT ?) ORDER BY id` |
| invoices | 100,000 | COVERING INDEX invoices_total | `/process` | `SELECT (SELECT value FROM settings WHERE key = ?), COUNT(*), MAX(id) FROM invoices` |
| invoices | 100,000 | COVERING INDEX invoices_total | `/help/report` | `SELECT COUNT(*) FROM invoices` |
| events | 415,765 | COVERING INDEX events_invoice | `/activity`, `/activity?q=Frontier` | `SELECT COUNT(*) FROM events e WHERE ? = ?` |
| events | 415,765 | COVERING INDEX events_actor | `/activity`, `/activity?q=Frontier` | `SELECT a.actor FROM (SELECT DISTINCT actor FROM events WHERE actor != ?) a WHERE EXISTS (SELECT ? FROM events e WHERE e.actor = a.actor AND ? = ?)` |
| events | 415,765 | COVERING INDEX events_action | `/activity`, `/activity?q=Frontier` | `SELECT COUNT(*) FROM events e WHERE ? = ? AND NOT e.action IN (SELECT value FROM json_each(?))` |
| events | 415,765 | COVERING INDEX events_actor | `/activity`, `/activity?q=Frontier` | `SELECT a.actor FROM (SELECT DISTINCT actor FROM events WHERE actor != ?) a WHERE EXISTS (SELECT ? FROM events e WHERE e.actor = a.actor AND ? = ? AND NOT e.action IN (SELECT value FROM json_each(?)))` |
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
| `/activity/events.csv` | 211 | `PRAGMA journal_mode=WAL` |
| `/activity/events.csv` | 208 | `SELECT e.*, i.vendor_name AS invoice_vendor, i.invoice_number AS invoice_number FROM events e LEFT JOIN invoices i ON i.id = e.invoice_id WHERE ? = ? AND e.id < ? ORDER BY e.id DESC LIMIT ?` |
| `/search?q=FTC6962398` | 224 | `-- SELECT pgno FROM ?.? WHERE segid=? AND term<=? ORDER BY term DESC LIMIT ?` |
| `/month-end` | 139 | `WITH coded AS (SELECT id, COALESCE(NULLIF(final_output, ?), NULLIF(ai_output, ?), ?) AS doc FROM invoices WHERE id IN (SELECT value FROM json_each(?))), read AS (SELECT id, doc, CASE WHEN NOT json_val` |
| `/month-end` | 49 | `SELECT final_gl gl_code, COUNT(*) lines, SUM(outcome = ?) corrected FROM feedback WHERE vendor_key = ? GROUP BY final_gl ORDER BY lines DESC` |
| `/search/export.csv` | 52 | `SELECT ? FROM summary_dirty LIMIT ?` |
| `/search/export.csv` | 50 | `SELECT json_group_array(json_array(CASE typeof(q."id") WHEN ? THEN json_object(?, printf(?, q."id")) WHEN ? THEN q."id" \|\| ? WHEN ? THEN json_object(?, hex(q."id")) ELSE q."id" END, CASE typeof(q."sta` |
| `/search/export.csv` | 50 | `SELECT id, created_at FROM export_batches WHERE id IN (SELECT value FROM json_each(?))` |
| `/search/export.xlsx` | 52 | `SELECT ? FROM summary_dirty LIMIT ?` |
| `/search/export.xlsx` | 50 | `SELECT json_group_array(json_array(CASE typeof(q."id") WHEN ? THEN json_object(?, printf(?, q."id")) WHEN ? THEN q."id" \|\| ? WHEN ? THEN json_object(?, hex(q."id")) ELSE q."id" END, CASE typeof(q."sta` |
| `/search/export.xlsx` | 50 | `SELECT id, created_at FROM export_batches WHERE id IN (SELECT value FROM json_each(?))` |
| `/search?q=Frontier` | 84 | `-- SELECT pgno FROM ?.? WHERE segid=? AND term<=? ORDER BY term DESC LIMIT ?` |
| `/month-end/accruals.csv` | 49 | `SELECT final_gl gl_code, COUNT(*) lines, SUM(outcome = ?) corrected FROM feedback WHERE vendor_key = ? GROUP BY final_gl ORDER BY lines DESC` |
| `/spend` | 21 | `SELECT ? FROM summary_dirty LIMIT ?` |

## Not asked

- `/logout`: ends the session
- `/process/inbox/{entry_id}/file/{n}`: no email in the inbox
- `/manager`: a Manager's page (no Manager in the seed)
- `/manager/{request_id:int}`: no manager request in the seed
- `/manager/{request_id:int}/page/{number:int}.png`: no manager request in the seed
- `/manager/{request_id:int}/reply`: no manager request in the seed
- `/manager/{request_id:int}/email.eml`: no manager request in the seed
- `/settings/data/backups/{name}`: no backup made (a backup of a big database fills the disk)
- `/settings/reading/self-test`: asks the model server (the load bench's fake one)
