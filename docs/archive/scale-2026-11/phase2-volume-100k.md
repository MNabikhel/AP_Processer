# Volume after phase 2: one person on 100,000 invoices (5 Nov 2026)

The same seed as the baseline ([baseline-volume-100k.md](baseline-volume-100k.md)): 100,000 invoices, 415,762 events,
421,954 lines of feedback, 200,000 register rows; made again by phase 2's code (schema 24). Measured on the same 4
shared cores while two review agents ran, so the times are on the slow side; statements and sizes don't depend on load.

**Known artefact:** the seed writes its invoices directly, never through a pick-up, so `seen_files` is empty and
`/process` lists all 100,000 files as new (a 17 MB page). On a real upgrade `fill_seen_files` notes the invoices' own
files.

## Before and after (addresses over 0.3 s in either run)

| address | role | before s | after s | before SQL | after SQL | over before -> after |
|---|---|---:|---:|---:|---:|---|
| `/spend/data.xlsx` | admin | 251.2511 | 0.0137 | 13 | 15 | over -> ok |
| `/spend/data.xlsx` | clerk | 245.2785 | 0.0133 | 12 | 15 | over -> ok |
| `/month-end` | admin | 201.7104 | 6.0593 | 10863 | 223 | over -> over |
| `/month-end/accruals.csv` | admin | 195.2095 | 5.922 | 10858 | 213 | over -> over |
| `/month-end/accruals.csv` | clerk | 193.5765 | 5.9389 | 10858 | 213 | over -> over |
| `/month-end` | clerk | 190.0929 | 5.6037 | 10859 | 218 | over -> over |
| `/help/report` | admin | 22.2559 | 0.1026 | 100126 | 127 | over -> ok |
| `/activity?q=Frontier` | admin | 19.621 | 0.3162 | 92217 | 37 | over -> over |
| `/activity?q=Frontier` | clerk | 19.5674 | 0.3168 | 92217 | 34 | over -> over |
| `/help/report` | clerk | 19.1411 | 0.1023 | 100125 | 127 | over -> ok |
| `/activity/controls-report` | clerk | 17.3216 | 0.0929 | 92210 | 18 | over -> ok |
| `/activity/controls-report` | admin | 17.3047 | 0.0155 | 92210 | 18 | over -> ok |
| `/exports/duplicates.csv` | admin | 13.558 | 7.7903 | 12 | 14 | over -> over |
| `/exports/duplicates.csv` | clerk | 13.5341 | 7.2164 | 11 | 12 | over -> over |
| `/activity` | clerk | 12.3973 | 0.265 | 92217 | 33 | over -> ok |
| `/activity` | admin | 12.3645 | 0.1593 | 92218 | 36 | over -> ok |
| `/search/export.xlsx` | clerk | 8.5496 | 9.2253 | 19 | 165 | over -> over |
| `/learning?tab=suppliers` | clerk | 7.9526 | 2.7909 | 9038 | 17 | over -> over |
| `/learning?tab=suppliers` | admin | 7.9149 | 2.8 | 9038 | 17 | over -> over |
| `/process` | clerk | 7.5505 | 1.5331 | 100045 | 46 | over -> over |
| `/search/export.xlsx` | admin | 7.484 | 8.6184 | 17 | 163 | over -> over |
| `/process` | admin | 6.3835 | 1.314 | 100046 | 48 | over -> over |
| `/spend` | admin | 4.8237 | 3.7133 | 19 | 36 | over -> over |
| `/spend` | clerk | 4.7902 | 3.4913 | 17 | 29 | over -> over |
| `/settings/automation` | admin | 4.7285 | 2.6916 | 6038 | 23 | over -> over |
| `/exports?fmt=jde` | clerk | 4.3186 | 1.5068 | 44 | 44 | over -> over |
| `/exports?fmt=jde` | admin | 4.1333 | 1.4352 | 45 | 46 | over -> over |
| `/exports/jde/dry-run.txt` | clerk | 3.7903 | 1.3515 | 19 | 19 | over -> ok |
| `/exports/jde/dry-run.txt` | admin | 3.6912 | 1.2428 | 23 | 19 | over -> ok |
| `/settings/jde/worksheet.xlsx` | admin | 3.4507 | 4.0329 | 17 | 17 | over -> over |
| `/search?q=FTC6962398` | clerk | 2.7246 | 0.0559 | 150 | 254 | ok -> ok |
| `/search?q=FTC6962398` | admin | 2.5298 | 0.065 | 148 | 252 | ok -> ok |
| `/settings/reading` | admin | 2.425 | 2.805 | 30 | 30 | over -> over |
| `/sales-tax` | clerk | 1.9746 | 2.0496 | 29 | 25 | ok -> ok |
| `/sales-tax` | admin | 1.9387 | 1.9032 | 31 | 27 | ok -> ok |
| `/search?q=Frontier` | clerk | 1.6317 | 0.2198 | 14571 | 116 | ok -> ok |
| `/search?q=Frontier` | admin | 1.6087 | 0.2337 | 14570 | 115 | ok -> ok |
| `/insights` | admin | 1.377 | 1.7841 | 43 | 39 | ok -> ok |
| `/search/export.csv` | clerk | 1.3195 | 1.2434 | 19 | 165 | ok -> ok |
| `/learning?tab=coding` | clerk | 1.2824 | 1.4169 | 28 | 28 | over -> over |
| `/insights` | clerk | 1.2558 | 1.9154 | 40 | 36 | ok -> ok |
| `/learning?tab=coding` | admin | 1.2415 | 1.3781 | 34 | 37 | over -> over |
| `/search/export.csv` | admin | 1.0124 | 1.1955 | 17 | 163 | ok -> ok |
| `/settings/data` | admin | 0.7766 | 1.1775 | 26 | 26 | over -> over |
| `/sales-tax/claims.csv` | clerk | 0.6962 | 0.7857 | 14 | 14 | ok -> ok |
| `/review/98499` | admin | 0.6887 | 0.9447 | 689 | 114 | over -> over |
| `/sales-tax/claims.csv` | admin | 0.6852 | 0.7619 | 14 | 14 | ok -> ok |
| `/review/98499` | clerk | 0.6262 | 1.1839 | 675 | 91 | over -> over |
| `/sales-tax/self-assessment.csv` | clerk | 0.526 | 0.6168 | 17 | 17 | ok -> ok |
| `/learning?tab=lessons` | admin | 0.5035 | 0.4614 | 15 | 15 | over -> over |
| `/sales-tax/self-assessment.csv` | admin | 0.4845 | 0.6209 | 17 | 17 | ok -> ok |
| `/insights/business-case` | clerk | 0.4094 | 0.4428 | 26 | 33 | ok -> ok |
| `/vendors` | admin | 0.4079 | 0.5008 | 24 | 24 | over -> over |
| `/insights/business-case` | admin | 0.3761 | 0.4705 | 26 | 26 | ok -> ok |
| `/learning?tab=lessons` | clerk | 0.3552 | 0.5954 | 19 | 15 | over -> over |
| `/purchase-orders` | admin | 0.3491 | 0.3638 | 17 | 17 | over -> over |
| `/review?tab=approved` | clerk | 0.3376 | 0.4595 | 40 | 40 | over -> over |
| `/review?tab=approved` | admin | 0.3328 | 0.2851 | 40 | 40 | over -> ok |
| `/vendors` | clerk | 0.3292 | 0.369 | 21 | 21 | over -> over |
| `/exports/batches/254/approved-pdfs.zip` | clerk | 0.2615 | 0.451 | 15 | 15 | ok -> ok |
| `/exports/batches/254/approved-pdfs.zip` | admin | 0.2375 | 0.353 | 15 | 15 | ok -> ok |
| `/review?sort=amount` | admin | 0.2245 | 0.3077 | 44 | 42 | ok -> over |
| `/purchase-orders` | clerk | 0.2193 | 0.3225 | 17 | 17 | ok -> over |
| `/review?show=ready` | admin | 0.2127 | 0.3075 | 42 | 40 | ok -> over |
| `/statements` | admin | 0.2107 | 0.3059 | 22 | 29 | ok -> ok |
| `/purchase-orders?status=all` | clerk | 0.2054 | 0.3918 | 17 | 17 | ok -> over |
| `/activity/events.csv` | admin | 0.0661 | 11.5953 | 10 | 425 | ok -> over |
| `/activity/events.csv` | clerk | 0.0538 | 10.5116 | 10 | 305 | ok -> over |

before: {'asked': 165, 'over': 43, 'errors': 0, 'scans': 45, 'slowest': '/spend/data.xlsx 251.25 s (admin)'}
after: {'asked': 165, 'over': 38, 'errors': 0, 'scans': 51, 'slowest': '/activity/events.csv 11.60 s (admin)'}

## The run itself

100,000 invoices, 415,762 events, 421,954 lines of feedback, 200,000 JD Edwards register rows; asked as admin
(admin001), clerk (clerk001), the median of 3 after a warm request; 4 cores, Python 3.12.13, SQLite 3.53.1.

**165 addresses, 38 over target, 0 errors (5xx), 51 full scans of big tables.** Targets: a page 300 ms, a report, export
or search 3 s.

| address | role | kind | status | median s | warm s | KB | SQL | peak MB | target | |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| `/activity/events.csv` | admin | export | 200 | 11.595 | 11.595 | 62,719 | 425 |  | 3 | **over** |
| `/activity/events.csv` | clerk | export | 200 | 10.512 | 9.002 | 54,903 | 305 |  | 3 | **over** |
| `/search/export.xlsx` | clerk | export | 200 | 9.225 | 9.169 | 3,182 | 165 | 4.2 | 3 | **over** |
| `/search/export.xlsx` | admin | export | 200 | 8.618 | 8.556 | 3,182 | 163 | 4.2 | 3 | **over** |
| `/exports/duplicates.csv` | admin | export | 200 | 7.790 | 8.449 | 227 | 14 | 261.4 | 3 | **over** |
| `/exports/duplicates.csv` | clerk | export | 200 | 7.216 | 7.565 | 227 | 12 | 261.5 | 3 | **over** |
| `/month-end` | admin | report | 200 | 6.059 | 5.496 | 2,069 | 223 | 123.6 | 3 | **over** |
| `/month-end/accruals.csv` | clerk | export | 200 | 5.939 | 5.996 | 788 | 213 | 123.6 | 3 | **over** |
| `/month-end/accruals.csv` | admin | export | 200 | 5.922 | 6.639 | 788 | 213 | 123.6 | 3 | **over** |
| `/month-end` | clerk | report | 200 | 5.604 | 5.798 | 2,069 | 218 | 123.6 | 3 | **over** |
| `/settings/jde/worksheet.xlsx` | admin | export | 200 | 4.033 | 4.199 | 122 | 17 | 30.5 | 3 | **over** |
| `/spend` | admin | report | 200 | 3.713 | 3.619 | 36 | 36 | 109.2 | 3 | **over** |
| `/spend` | clerk | report | 200 | 3.491 | 3.352 | 36 | 29 | 109.2 | 3 | **over** |
| `/settings/reading` | admin | page | 200 | 2.805 | 2.674 | 19 | 30 |  | 0.3 | **over** |
| `/learning?tab=suppliers` | admin | page | 200 | 2.800 | 2.901 | 225 | 17 |  | 0.3 | **over** |
| `/learning?tab=suppliers` | clerk | page | 200 | 2.791 | 2.906 | 157 | 17 |  | 0.3 | **over** |
| `/settings/automation` | admin | page | 200 | 2.692 | 2.464 | 15 | 23 |  | 0.3 | **over** |
| `/sales-tax` | clerk | report | 200 | 2.050 | 2.099 | 11,092 | 25 | 73.3 | 3 | ok |
| `/insights` | clerk | report | 200 | 1.915 | 1.551 | 39 | 36 | 94.4 | 3 | ok |
| `/sales-tax` | admin | report | 200 | 1.903 | 2.241 | 11,093 | 27 | 73.3 | 3 | ok |
| `/insights` | admin | report | 200 | 1.784 | 2.154 | 39 | 39 | 94.4 | 3 | ok |
| `/process` | clerk | page | 200 | 1.533 | 1.513 | 17,194 | 46 |  | 0.3 | **over** |
| `/exports?fmt=jde` | clerk | page | 200 | 1.507 | 1.490 | 414 | 44 |  | 0.3 | **over** |
| `/exports?fmt=jde` | admin | page | 200 | 1.435 | 1.456 | 449 | 46 |  | 0.3 | **over** |
| `/learning?tab=coding` | clerk | page | 200 | 1.417 | 1.506 | 122 | 28 |  | 0.3 | **over** |
| `/learning?tab=coding` | admin | page | 200 | 1.378 | 2.959 | 164 | 37 |  | 0.3 | **over** |
| `/exports/jde/dry-run.txt` | clerk | export | 200 | 1.351 | 1.313 | 115 | 19 | 24.6 | 3 | ok |
| `/process` | admin | page | 200 | 1.314 | 4.387 | 17,195 | 48 |  | 0.3 | **over** |
| `/search/export.csv` | clerk | export | 200 | 1.243 | 1.304 | 10,239 | 165 | 43.7 | 3 | ok |
| `/exports/jde/dry-run.txt` | admin | export | 200 | 1.243 | 1.325 | 115 | 19 | 24.6 | 3 | ok |
| `/search/export.csv` | admin | export | 200 | 1.196 | 1.109 | 10,239 | 163 | 43.7 | 3 | ok |
| `/review/98499` | clerk | page | 200 | 1.184 | 0.881 | 3,612 | 91 |  | 0.3 | **over** |
| `/settings/data` | admin | page | 200 | 1.177 | 1.155 | 13 | 26 |  | 0.3 | **over** |
| `/review/98499` | admin | page | 200 | 0.945 | 4.081 | 3,613 | 114 |  | 0.3 | **over** |
| `/sales-tax/claims.csv` | clerk | export | 200 | 0.786 | 0.563 | 4,292 | 14 | 44.9 | 3 | ok |
| `/sales-tax/claims.csv` | admin | export | 200 | 0.762 | 0.788 | 4,292 | 14 | 44.9 | 3 | ok |
| `/sales-tax/self-assessment.csv` | admin | export | 200 | 0.621 | 0.675 | 0 | 17 | 21.4 | 3 | ok |
| `/sales-tax/self-assessment.csv` | clerk | export | 200 | 0.617 | 0.889 | 0 | 17 | 21.4 | 3 | ok |
| `/learning?tab=lessons` | clerk | page | 200 | 0.595 | 0.522 | 482 | 15 |  | 0.3 | **over** |
| `/vendors` | admin | page | 200 | 0.501 | 1.373 | 1,765 | 24 |  | 0.3 | **over** |
| `/insights/business-case` | admin | report | 200 | 0.470 | 0.498 | 3 | 26 | 13.6 | 3 | ok |
| `/learning?tab=lessons` | admin | page | 200 | 0.461 | 0.662 | 634 | 15 |  | 0.3 | **over** |
| `/review?tab=approved` | clerk | page | 200 | 0.460 | 0.495 | 44 | 40 |  | 0.3 | **over** |
| `/exports/batches/254/approved-pdfs.zip` | clerk | export | 200 | 0.451 | 0.303 | 45 | 15 | 0.8 | 3 | ok |
| `/insights/business-case` | clerk | report | 200 | 0.443 | 0.485 | 3 | 33 | 13.6 | 3 | ok |
| `/purchase-orders?status=all` | clerk | page | 200 | 0.392 | 0.235 | 2,057 | 17 |  | 0.3 | **over** |
| `/vendors` | clerk | page | 200 | 0.369 | 0.592 | 1,765 | 21 |  | 0.3 | **over** |
| `/purchase-orders` | admin | page | 200 | 0.364 | 0.305 | 2,035 | 17 |  | 0.3 | **over** |
| `/exports/batches/254/approved-pdfs.zip` | admin | export | 200 | 0.353 | 0.310 | 45 | 15 | 0.8 | 3 | ok |
| `/purchase-orders` | clerk | page | 200 | 0.323 | 0.426 | 2,034 | 17 |  | 0.3 | **over** |
| `/activity?q=Frontier` | clerk | page | 200 | 0.317 | 0.273 | 131 | 34 |  | 0.3 | **over** |
| `/activity?q=Frontier` | admin | page | 200 | 0.316 | 0.299 | 132 | 37 |  | 0.3 | **over** |
| `/review?sort=amount` | admin | page | 200 | 0.308 | 0.171 | 84 | 42 |  | 0.3 | **over** |
| `/review?show=ready` | admin | page | 200 | 0.307 | 0.160 | 10 | 40 |  | 0.3 | **over** |
| `/statements` | admin | report | 200 | 0.306 | 0.779 | 269 | 29 | 23.4 | 3 | ok |
| `/vendors?q=Frontier` | admin | page | 200 | 0.292 | 0.259 | 144 | 21 |  | 0.3 | ok |
| `/review?show=attention` | admin | page | 200 | 0.288 | 0.315 | 83 | 41 |  | 0.3 | ok |
| `/review?tab=approved` | admin | page | 200 | 0.285 | 0.599 | 44 | 40 |  | 0.3 | ok |
| `/review?sort=amount` | clerk | page | 200 | 0.278 | 0.394 | 84 | 42 |  | 0.3 | ok |
| `/activity` | clerk | page | 200 | 0.265 | 0.261 | 131 | 33 |  | 0.3 | ok |
| `/purchase-orders?status=all` | admin | page | 200 | 0.264 | 0.258 | 2,058 | 17 |  | 0.3 | ok |
| `/review?tab=other` | admin | page | 200 | 0.256 | 0.318 | 1,419 | 38 |  | 0.3 | ok |
| `/review?tab=other` | clerk | page | 200 | 0.252 | 0.368 | 988 | 38 |  | 0.3 | ok |
| `/statements` | clerk | report | 200 | 0.246 | 0.248 | 268 | 17 | 23.4 | 3 | ok |
| `/review` | admin | page | 200 | 0.243 | 1.350 | 83 | 49 |  | 0.3 | ok |
| `/search?q=Frontier` | admin | report | 200 | 0.234 | 0.276 | 160 | 115 | 9.1 | 3 | ok |
| `/review?tab=parked` | admin | page | 200 | 0.225 | 0.300 | 101 | 37 |  | 0.3 | ok |
| `/vendors?q=Frontier` | clerk | page | 200 | 0.223 | 0.227 | 143 | 21 |  | 0.3 | ok |
| `/search?q=Frontier` | clerk | report | 200 | 0.220 | 0.199 | 159 | 116 | 9.1 | 3 | ok |
| `/search/7/approved.pdf` | clerk | export | 200 | 0.213 | 0.215 | 18 | 12 | 0.2 | 3 | ok |
| `/review?tab=second` | clerk | page | 200 | 0.208 | 0.425 | 67 | 38 |  | 0.3 | ok |
| `/review` | clerk | page | 200 | 0.202 | 0.356 | 82 | 42 |  | 0.3 | ok |
| `/review/7/approved.pdf` | clerk | export | 200 | 0.197 | 0.220 | 18 | 12 | 0.2 | 3 | ok |
| `/review?show=attention` | clerk | page | 200 | 0.196 | 0.332 | 83 | 41 |  | 0.3 | ok |
| `/review?show=ready` | clerk | page | 200 | 0.192 | 0.425 | 10 | 40 |  | 0.3 | ok |
| `/review?tab=parked` | clerk | page | 200 | 0.182 | 0.328 | 101 | 37 |  | 0.3 | ok |
| `/review/7/approved.pdf` | admin | export | 200 | 0.178 | 0.175 | 18 | 12 | 0.2 | 3 | ok |
| `/search/7/approved.pdf` | admin | export | 200 | 0.165 | 0.160 | 18 | 12 | 0.2 | 3 | ok |
| `/activity` | admin | page | 200 | 0.159 | 0.340 | 115 | 36 |  | 0.3 | ok |
| `/review?tab=second` | admin | page | 200 | 0.157 | 0.327 | 152 | 38 |  | 0.3 | ok |
| `/settings/jde` | admin | page | 200 | 0.145 | 0.169 | 41 | 23 |  | 0.3 | ok |
| `/exports` | clerk | page | 200 | 0.139 | 0.171 | 314 | 40 |  | 0.3 | ok |
| `/exports` | admin | page | 200 | 0.137 | 0.788 | 347 | 57 |  | 0.3 | ok |
| `/search?page=40` | admin | page | 200 | 0.125 | 0.117 | 152 | 21 |  | 0.3 | ok |
| `/search?page=40` | clerk | page | 200 | 0.113 | 0.194 | 151 | 24 |  | 0.3 | ok |
| `/help/report` | admin | report | 200 | 0.103 | 0.101 | 151 | 127 | 5.8 | 3 | ok |
| `/help/report` | clerk | report | 200 | 0.102 | 0.111 | 146 | 127 | 5.8 | 3 | ok |
| `/exports/batches/254/download` | admin | page | 200 | 0.097 | 0.103 | 6 | 18 |  | 0.3 | ok |
| `/exports/batches/254/download` | clerk | page | 200 | 0.094 | 0.142 | 6 | 18 |  | 0.3 | ok |
| `/activity/controls-report` | clerk | report | 200 | 0.093 | 0.153 | 21 | 18 | 0.3 | 3 | ok |
| `/search` | admin | page | 200 | 0.088 | 0.127 | 141 | 23 |  | 0.3 | ok |
| `/review/7` | admin | page | 200 | 0.086 | 0.069 | 21 | 34 |  | 0.3 | ok |
| `/review/97005` | admin | page | 200 | 0.083 | 0.090 | 27 | 35 |  | 0.3 | ok |
| `/accounts?tab=rules` | admin | page | 200 | 0.076 | 0.055 | 456 | 20 |  | 0.3 | ok |
| `/search` | clerk | page | 200 | 0.073 | 0.073 | 141 | 28 |  | 0.3 | ok |
| `/search?q=FTC6962398` | admin | report | 200 | 0.065 | 0.075 | 30 | 252 | 0.2 | 3 | ok |
| `/accounts?tab=gl` | admin | page | 200 | 0.061 | 0.094 | 1,186 | 25 |  | 0.3 | ok |
| `/review/97068` | admin | page | 200 | 0.059 | 0.056 | 26 | 34 |  | 0.3 | ok |
| `/search?q=FTC6962398` | clerk | report | 200 | 0.056 | 0.059 | 30 | 254 | 0.2 | 3 | ok |
| `/review/97068` | clerk | page | 200 | 0.055 | 0.062 | 26 | 34 |  | 0.3 | ok |
| `/review/97005` | clerk | page | 200 | 0.054 | 0.050 | 26 | 35 |  | 0.3 | ok |
| `/review/7` | clerk | page | 200 | 0.048 | 0.054 | 20 | 34 |  | 0.3 | ok |
| `/accounts?tab=rules` | clerk | page | 200 | 0.043 | 0.055 | 9 | 20 |  | 0.3 | ok |
| `/accounts?tab=tax` | admin | page | 200 | 0.038 | 0.038 | 473 | 19 |  | 0.3 | ok |
| `/accounts?tab=gl` | clerk | page | 200 | 0.035 | 0.173 | 1,184 | 25 |  | 0.3 | ok |
| `/accounts?tab=tax` | clerk | page | 200 | 0.033 | 0.043 | 473 | 19 |  | 0.3 | ok |
| `/admin/users` | admin | page | 200 | 0.033 | 0.063 | 814 | 15 |  | 0.3 | ok |
| `/accounts?tab=bank` | admin | page | 200 | 0.021 | 0.026 | 288 | 23 |  | 0.3 | ok |
| `/accounts?tab=bank` | clerk | page | 200 | 0.020 | 0.021 | 9 | 19 |  | 0.3 | ok |
| `/accounts?tab=cc` | admin | page | 200 | 0.020 | 0.024 | 190 | 18 |  | 0.3 | ok |
| `/accounts?tab=policy` | clerk | page | 200 | 0.018 | 0.015 | 9 | 17 |  | 0.3 | ok |
| `/accounts/gl_accounts.csv` | admin | export | 200 | 0.018 | 0.021 | 107 | 10 | 1.1 | 3 | ok |
| `/accounts/gl_accounts.csv` | clerk | export | 200 | 0.018 | 0.031 | 107 | 10 | 1.1 | 3 | ok |
| `/accounts?tab=cc` | clerk | page | 200 | 0.017 | 0.017 | 187 | 18 |  | 0.3 | ok |
| `/review/98499/reader` | clerk | page | 200 | 0.016 | 0.017 | 0 | 12 |  | 0.3 | ok |
| `/activity/controls-report` | admin | report | 200 | 0.015 | 0.020 | 21 | 18 | 0.3 | 3 | ok |
| `/settings/about` | admin | page | 200 | 0.015 | 0.023 | 10 | 18 |  | 0.3 | ok |
| `/review/98499/reader` | admin | page | 200 | 0.015 | 0.015 | 0 | 12 |  | 0.3 | ok |
| `/statements/reconciliation.csv` | clerk | export | 404 | 0.014 | 0.015 | 6 | 15 |  | 3 | ok |
| `/learning/training.zip` | admin | export | 404 | 0.014 | 0.015 | 7 | 16 |  | 3 | ok |
| `/accounts?tab=policy` | admin | page | 200 | 0.014 | 0.014 | 9 | 17 |  | 0.3 | ok |
| `/accounts/cost_centers.csv` | admin | export | 200 | 0.014 | 0.014 | 9 | 10 | 0.3 | 3 | ok |
| `/spend/data.xlsx` | admin | export | 404 | 0.014 | 0.015 | 7 | 15 |  | 3 | ok |
| `/spend/data.xlsx` | clerk | export | 404 | 0.013 | 0.015 | 6 | 15 |  | 3 | ok |
| `/help` | clerk | page | 200 | 0.013 | 0.014 | 124 | 16 |  | 0.3 | ok |
| `/review/98499/words` | clerk | page | 200 | 0.012 | 0.013 | 8 | 4 |  | 0.3 | ok |
| `/review/98499/words` | admin | page | 200 | 0.012 | 0.013 | 8 | 4 |  | 0.3 | ok |
| `/statements/reconciliation.csv` | admin | export | 404 | 0.012 | 0.017 | 7 | 15 |  | 3 | ok |
| `/settings/review` | admin | page | 200 | 0.012 | 0.017 | 12 | 30 |  | 0.3 | ok |
| `/settings` | admin | page | 200 | 0.012 | 0.028 | 12 | 30 |  | 0.3 | ok |
| `/accounts/cost_centers.csv` | clerk | export | 200 | 0.012 | 0.015 | 9 | 10 | 0.3 | 3 | ok |
| `/help` | admin | page | 200 | 0.011 | 0.025 | 126 | 16 |  | 0.3 | ok |
| `/help/quick-start` | clerk | page | 200 | 0.011 | 0.011 | 11 | 11 |  | 0.3 | ok |
| `/settings/hub` | admin | page | 200 | 0.010 | 0.024 | 14 | 27 |  | 0.3 | ok |
| `/help/quick-start` | admin | page | 200 | 0.009 | 0.019 | 11 | 11 |  | 0.3 | ok |
| `/learning?tab=readers` | clerk | page | 200 | 0.008 | 0.010 | 7 | 12 |  | 0.3 | ok |
| `/purchase-orders/template.csv` | clerk | export | 200 | 0.008 | 0.009 | 0 | 8 | 0.1 | 3 | ok |
| `/account/password` | clerk | page | 200 | 0.008 | 0.007 | 7 | 13 |  | 0.3 | ok |
| `/accounts/bank/template.csv` | admin | export | 200 | 0.008 | 0.008 | 0 | 8 | 0.1 | 3 | ok |
| `/learning?tab=readers` | admin | page | 200 | 0.008 | 0.010 | 7 | 12 |  | 0.3 | ok |
| `/accounts/bank/template.csv` | clerk | export | 200 | 0.008 | 0.008 | 0 | 8 | 0.1 | 3 | ok |
| `/process/status` | clerk | page | 200 | 0.007 | 0.010 | 0 | 3 |  | 0.3 | ok |
| `/purchase-orders/template.csv` | admin | export | 200 | 0.007 | 0.011 | 0 | 8 | 0.1 | 3 | ok |
| `/review/98499/page/1.png` | admin | page | 200 | 0.007 | 0.041 | 63 | 4 |  | 0.3 | ok |
| `/account/password` | admin | page | 200 | 0.007 | 0.012 | 8 | 13 |  | 0.3 | ok |
| `/settings/azure-check` | admin | page | 200 | 0.007 | 0.011 | 7 | 16 |  | 0.3 | ok |
| `/login` | clerk | page | 303 | 0.007 | 0.006 | 0 | 3 |  | 0.3 | ok |
| `/login/code` | admin | page | 303 | 0.006 | 0.006 | 0 | 3 |  | 0.3 | ok |
| `/jobs/process` | clerk | page | 200 | 0.006 | 0.006 | 0 | 2 |  | 0.3 | ok |
| `/process/status` | admin | page | 200 | 0.006 | 0.007 | 0 | 3 |  | 0.3 | ok |
| `/login/authenticator` | admin | page | 303 | 0.006 | 0.007 | 0 | 3 |  | 0.3 | ok |
| `/help/report/setup` | admin | report | 200 | 0.006 | 0.057 | 3 | 333 | 0.1 | 3 | ok |
| `/login/new-password` | clerk | page | 303 | 0.006 | 0.007 | 0 | 3 |  | 0.3 | ok |
| `/login/new-password` | admin | page | 303 | 0.006 | 0.005 | 0 | 3 |  | 0.3 | ok |
| `/review/98499/page/1.png` | clerk | page | 200 | 0.006 | 0.007 | 63 | 4 |  | 0.3 | ok |
| `/who` | clerk | page | 303 | 0.006 | 0.009 | 0 | 3 |  | 0.3 | ok |
| `/healthz` | clerk | page | 200 | 0.006 | 0.006 | 0 | 2 |  | 0.3 | ok |
| `/who` | admin | page | 303 | 0.006 | 0.007 | 0 | 3 |  | 0.3 | ok |
| `/login` | admin | page | 303 | 0.006 | 0.006 | 0 | 3 |  | 0.3 | ok |
| `/login/authenticator` | clerk | page | 303 | 0.006 | 0.006 | 0 | 3 |  | 0.3 | ok |
| `/` | clerk | page | 307 | 0.006 | 0.006 | 0 | 2 |  | 0.3 | ok |
| `/login/code` | clerk | page | 303 | 0.005 | 0.006 | 0 | 3 |  | 0.3 | ok |
| `/healthz` | admin | page | 200 | 0.005 | 0.006 | 0 | 2 |  | 0.3 | ok |
| `/jobs/process` | admin | page | 200 | 0.005 | 0.006 | 0 | 2 |  | 0.3 | ok |
| `/` | admin | page | 307 | 0.005 | 0.006 | 0 | 2 |  | 0.3 | ok |

## Full scans of big tables

Each `SCAN` in the plan of a statement a warm request ran, on a table with 12,940 rows or more; `how`: the table itself
or an index read whole.

| table | rows | how | routes | statement |
|---|---:|---|---|---|
| invoices | 100,000 | table | `/help/report` | `SELECT id, source_path, status, error, meta, validation FROM invoices WHERE id IN (SELECT id FROM invoices ORDER BY id DESC LIMIT ?) ORDER BY id` |
| invoices | 100,000 | COVERING INDEX invoices_total | `/process` | `SELECT (SELECT value FROM settings WHERE key = ?), COUNT(*), MAX(id) FROM invoices` |
| invoices | 100,000 | COVERING INDEX invoices_total | `/help/report` | `SELECT COUNT(*) FROM invoices` |
| events | 415,762 | COVERING INDEX events_invoice | `/activity`, `/activity?q=Frontier` | `SELECT COUNT(*) FROM events e WHERE ? = ?` |
| events | 415,762 | COVERING INDEX events_actor | `/activity`, `/activity?q=Frontier` | `SELECT a.actor FROM (SELECT DISTINCT actor FROM events WHERE actor != ?) a WHERE EXISTS (SELECT ? FROM events e WHERE e.actor = a.actor AND ? = ?)` |
| events | 415,762 | COVERING INDEX events_action | `/activity`, `/activity?q=Frontier` | `SELECT COUNT(*) FROM events e WHERE ? = ? AND NOT e.action IN (SELECT value FROM json_each(?))` |
| events | 415,762 | COVERING INDEX events_actor | `/activity`, `/activity?q=Frontier` | `SELECT a.actor FROM (SELECT DISTINCT actor FROM events WHERE actor != ?) a WHERE EXISTS (SELECT ? FROM events e WHERE e.actor = a.actor AND ? = ? AND NOT e.action IN (SELECT value FROM json_each(?)))` |
| feedback | 421,954 | table | `/review/98499`, `/accounts?tab=rules`, `/learning?tab=coding`, `/settings/data`, `/help/report` | `SELECT (SELECT MAX(id) FROM feedback), (SELECT TOTAL(n) FROM feedback_counts), (SELECT created_at \|\| ? \|\| COALESCE(vendor_key, ?) \|\| ? \|\| COALESCE(description, ?) \|\| ? \|\| COALESCE(final_gl, ?) FROM feedback ORDER BY id D` |
| feedback | 421,954 | table | `/review/98499`, `/learning?tab=coding` | `SELECT json_group_array(json_array(CASE typeof(q."id") WHEN ? THEN json_object(?, printf(?, q."id")) WHEN ? THEN q."id" \|\| ? WHEN ? THEN json_object(?, hex(q."id")) ELSE q."id" END, CASE typeof(q."invoice_id") WHEN ? THE` |
| feedback | 421,954 | table | `/learning?tab=coding` | `SELECT * FROM (SELECT f.invoice_id, f.line_number, f.vendor_name, f.description, f.final_tax_code, f.created_at, f.reviewer, i.invoice_number, (SELECT json_extract(c.value, ?) FROM events e, json_each(e.detail, ?) c WHER` |
| feedback | 421,954 | table | `/learning?tab=coding` | `SELECT substr(created_at, ?, ?) day, SUM(outcome = ?) accepted, SUM(outcome = ?) corrected FROM feedback WHERE outcome NOT IN (?, ?, ?) GROUP BY day` |
| feedback | 421,954 | COVERING INDEX feedback_vendor_outcome | `/statements`, `/vendors`, `/vendors?q=Frontier`, `/settings/jde/worksheet.xlsx` | `SELECT vendor_key, COUNT(*) lessons, SUM(outcome NOT IN (?, ?, ?)) lines, SUM(outcome = ?) accepted FROM feedback GROUP BY vendor_key` |
| feedback | 421,954 | INDEX feedback_vendor | `/learning?tab=coding` | `SELECT vendor_name, COUNT(*) lines, SUM(outcome = ?) accepted, SUM(outcome = ?) corrected, MAX(created_at) last_seen FROM feedback WHERE outcome NOT IN (?, ?, ?) GROUP BY vendor_key ORDER BY lines DESC` |
| erp_invoices | 200,000 | table | `/search?q=Frontier` | `SELECT json_group_array(json_array(CASE typeof(q."vendor_key") WHEN ? THEN json_object(?, printf(?, q."vendor_key")) WHEN ? THEN q."vendor_key" \|\| ? WHEN ? THEN json_object(?, hex(q."vendor_key")) ELSE q."vendor_key" END` |
| erp_invoices | 200,000 | table | `/search?q=FTC6962398` | `SELECT json_group_array(json_array(CASE typeof(q."vendor_key") WHEN ? THEN json_object(?, printf(?, q."vendor_key")) WHEN ? THEN q."vendor_key" \|\| ? WHEN ? THEN json_object(?, hex(q."vendor_key")) ELSE q."vendor_key" END` |
| erp_invoices | 200,000 | table | `/exports/duplicates.csv` | `SELECT json_group_array(json_array(CASE typeof(q."vendor_key") WHEN ? THEN json_object(?, printf(?, q."vendor_key")) WHEN ? THEN q."vendor_key" \|\| ? WHEN ? THEN json_object(?, hex(q."vendor_key")) ELSE q."vendor_key" END` |
| erp_invoices | 200,000 | COVERING INDEX erp_invoices_number | `/exports`, `/exports?fmt=jde` | `SELECT COUNT(*) FROM erp_invoices` |
| reader_outcomes | 1,957,734 | INDEX reader_outcomes_reader | `/settings/reading` | `SELECT reader, COUNT(*) fields, SUM(correct) agreed, COUNT(DISTINCT invoice_id) invoices FROM reader_outcomes GROUP BY reader ORDER BY reader` |
| lesson_approvals | 1,945,789 | table | `/learning?tab=coding` | `SELECT a.invoice_id, a.value, a.proposed, a.at, l.vendor_name, l.field, l.pattern, i.invoice_number, i.reviewer, n.corrected next_corrected, n.value next_value, n.invoice_id next_invoice_id, n.at next_at FROM lesson_appr` |
| supplier_outcomes | 1,101,881 | INDEX supplier_outcomes_key_at | `/learning?tab=suppliers`, `/settings/automation` | `WITH inv AS ( SELECT supplier_key, invoice_id, MAX(at) AS at, COUNT(*) AS fields, SUM(correct = ?) AS corrections, json_group_array(DISTINCT COALESCE(NULLIF(source, ?), ?)) AS sources FROM supplier_outcomes WHERE ? GROUP` |
| supplier_outcomes | 1,101,881 | INDEX supplier_outcomes_invoice | `/settings/automation` | `SELECT invoice_id, MIN(correct) AS clean FROM supplier_outcomes WHERE source = ? AND at >= ? GROUP BY invoice_id` |
| line_texts | 421,954 | table | `/learning?tab=coding` | `SELECT t.invoice_id, t.printed, t.description, t.at, i.vendor_name, i.invoice_number, i.reviewer, n.corrected next_corrected, n.description next_value, n.invoice_id next_invoice_id, n.at next_at FROM line_texts t LEFT JO` |
| invoice_summary | 100,000 | table | `/review` | `SELECT json_group_array(json_array(CASE typeof(q."vendor_key") WHEN ? THEN json_object(?, printf(?, q."vendor_key")) WHEN ? THEN q."vendor_key" \|\| ? WHEN ? THEN json_object(?, hex(q."vendor_key")) ELSE q."vendor_key" END` |
| invoice_summary | 100,000 | table | `/process` | `SELECT json_group_array(json_array(CASE typeof(q."id") WHEN ? THEN json_object(?, printf(?, q."id")) WHEN ? THEN q."id" \|\| ? WHEN ? THEN json_object(?, hex(q."id")) ELSE q."id" END, CASE typeof(q."file_name") WHEN ? THEN` |
| invoice_summary | 100,000 | table | `/search`, `/search?page=40`, `/search/export.csv`, `/search/export.xlsx` | `WITH rows AS ( SELECT id, status, file_name, vendor_name, invoice_number, invoice_date, due_date, currency, grand_total, created_at, reviewed_at, reviewer, second_reviewer, second_reviewed_at, export_batch, parked_reason` |
| invoice_summary | 100,000 | table | `/sales-tax`, `/sales-tax/claims.csv` | `SELECT json_group_array(json_array(CASE typeof(q."id") WHEN ? THEN json_object(?, printf(?, q."id")) WHEN ? THEN q."id" \|\| ? WHEN ? THEN json_object(?, hex(q."id")) ELSE q."id" END, CASE typeof(q."status") WHEN ? THEN js` |
| invoice_summary | 100,000 | table | `/vendors` | `SELECT json_group_array(json_array(CASE typeof(q."id") WHEN ? THEN json_object(?, printf(?, q."id")) WHEN ? THEN q."id" \|\| ? WHEN ? THEN json_object(?, hex(q."id")) ELSE q."id" END, CASE typeof(q."status") WHEN ? THEN js` |
| invoice_summary | 100,000 | table | `/learning?tab=coding` | `SELECT json_group_array(json_array(CASE typeof(q."id") WHEN ? THEN json_object(?, printf(?, q."id")) WHEN ? THEN q."id" \|\| ? WHEN ? THEN json_object(?, hex(q."id")) ELSE q."id" END, CASE typeof(q."broken") WHEN ? THEN js` |
| invoice_summary | 100,000 | table | `/spend` | `SELECT json_group_array(json_array(CASE typeof(q."id") WHEN ? THEN json_object(?, printf(?, q."id")) WHEN ? THEN q."id" \|\| ? WHEN ? THEN json_object(?, hex(q."id")) ELSE q."id" END)) FROM (SELECT id FROM invoice_summary ` |
| invoice_summary | 100,000 | table | `/insights`, `/insights/business-case` | `SELECT status = ?, COUNT(*), SUM(CASE WHEN requires_review THEN ? ELSE ? END) FROM invoice_summary WHERE broken & ? = ? GROUP BY ?` |
| invoice_summary | 100,000 | table | `/insights`, `/insights/business-case` | `SELECT substr(created_at, ?, ?), COUNT(*) FROM invoice_summary WHERE +status != ? AND broken & ? = ? GROUP BY ?` |
| invoice_summary | 100,000 | table | `/insights`, `/insights/business-case` | `SELECT substr(a_at, ?, ?), a_corrected, a_filled, a_bulk, COUNT(*) FROM invoice_summary WHERE +status IN (?, ?) AND a_at IS NOT NULL AND broken & ? = ? GROUP BY ?, ?, ?, ?` |
| invoice_summary | 100,000 | table | `/insights`, `/insights/business-case` | `SELECT i_seconds FROM invoice_summary WHERE +status != ? AND i_seconds IS NOT NULL AND broken & ? = ? ORDER BY id` |
| invoice_summary | 100,000 | table | `/insights`, `/insights/business-case` | `SELECT json_group_array(json_array(CASE typeof(q."id") WHEN ? THEN json_object(?, printf(?, q."id")) WHEN ? THEN q."id" \|\| ? WHEN ? THEN json_object(?, hex(q."id")) ELSE q."id" END, CASE typeof(q."status") WHEN ? THEN js` |
| invoice_summary | 100,000 | table | `/insights` | `SELECT json_group_array(json_array(CASE typeof(q."id") WHEN ? THEN json_object(?, printf(?, q."id")) WHEN ? THEN q."id" \|\| ? WHEN ? THEN json_object(?, hex(q."id")) ELSE q."id" END, CASE typeof(q."status") WHEN ? THEN js` |
| invoice_summary | 100,000 | table | `/activity`, `/activity?q=Frontier` | `SELECT json_group_array(json_array(CASE typeof(q."n") WHEN ? THEN json_object(?, printf(?, q."n")) WHEN ? THEN q."n" \|\| ? WHEN ? THEN json_object(?, hex(q."n")) ELSE q."n" END, CASE typeof(q."clean") WHEN ? THEN json_obj` |
| invoice_summary | 100,000 | table | `/activity`, `/activity?q=Frontier` | `SELECT json_group_array(json_array(CASE typeof(q."id") WHEN ? THEN json_object(?, printf(?, q."id")) WHEN ? THEN q."id" \|\| ? WHEN ? THEN json_object(?, hex(q."id")) ELSE q."id" END, CASE typeof(q."broken") WHEN ? THEN js` |
| invoice_summary | 100,000 | COVERING INDEX summary_edits | `/review/98499` | `SELECT status, COUNT(*) FROM invoice_summary GROUP BY status` |
| invoice_summary | 100,000 | COVERING INDEX summary_broken | `/search`, `/search?page=40` | `WITH rows AS ( SELECT id, status, file_name, vendor_name, invoice_number, invoice_date, due_date, currency, grand_total, created_at, reviewed_at, reviewer, second_reviewer, second_reviewed_at, export_batch, parked_reason` |
| invoice_summary | 100,000 | INDEX summary_vendor | `/statements` | `SELECT json_group_array(json_array(CASE typeof(q."vendor_key") WHEN ? THEN json_object(?, printf(?, q."vendor_key")) WHEN ? THEN q."vendor_key" \|\| ? WHEN ? THEN json_object(?, hex(q."vendor_key")) ELSE q."vendor_key" END` |
| invoice_summary | 100,000 | COVERING INDEX summary_edits | `/learning?tab=coding`, `/settings/data`, `/help/report` | `SELECT status, COUNT(*) n FROM invoice_summary GROUP BY status` |
| summary_codes | 271,688 | COVERING INDEX summary_codes_code | `/insights`, `/insights/business-case` | `SELECT c.code, SUM(c.n), MIN(c.id), MIN(c.pos) FROM summary_codes c JOIN (SELECT code, MIN(id) first FROM summary_codes GROUP BY code) f ON f.code = c.code AND c.id = f.first GROUP BY c.code` |
| summary_codes | 271,688 | COVERING INDEX summary_codes_code | `/insights`, `/insights/business-case` | `SELECT code, SUM(n) FROM summary_codes GROUP BY code` |
| vendors | 19,008 | table | `/review/98499`, `/vendors`, `/vendors?q=Frontier` | `SELECT ? FROM vendors WHERE in_master = ? LIMIT ?` |
| vendors | 19,008 | table | `/review/98499` | `SELECT json_group_array(json_array(CASE typeof(q."vendor_key") WHEN ? THEN json_object(?, printf(?, q."vendor_key")) WHEN ? THEN q."vendor_key" \|\| ? WHEN ? THEN json_object(?, hex(q."vendor_key")) ELSE q."vendor_key" END` |
| vendors | 19,008 | table | `/review/98499` | `SELECT json_group_array(json_array(CASE typeof(q."vendor_key") WHEN ? THEN json_object(?, printf(?, q."vendor_key")) WHEN ? THEN q."vendor_key" \|\| ? WHEN ? THEN json_object(?, hex(q."vendor_key")) ELSE q."vendor_key" END` |
| vendors | 19,008 | table | `/review`, `/review?show=attention`, `/review?show=ready`, `/review?sort=amount`, `/insights` | `SELECT vendor_key, terms FROM vendors WHERE terms != ?` |
| vendors | 19,008 | table | `/process`, `/settings/jde` | `SELECT * FROM vendors WHERE in_master = ?` |
| vendors | 19,008 | table | `/exports`, `/exports?fmt=jde`, `/exports/jde/dry-run.txt`, `/exports/batches/254/download`, `/settings/jde/worksheet.xlsx` | `SELECT vendor_key, erp_id, expected_gst FROM vendors WHERE erp_id != ?` |
| vendors | 19,008 | table | `/statements`, `/vendors`, `/vendors?q=Frontier`, `/settings/jde/worksheet.xlsx` | `SELECT * FROM vendors` |
| lessons | 65,753 | table | `/learning?tab=lessons` | `SELECT lessons.*, (SELECT COUNT(*) FROM lesson_approvals a WHERE a.situation = lessons.situation AND a.corrected = ? AND a.value = lessons.value) + MAX(lessons.corrected - (SELECT COUNT(*) FROM lesson_approvals a WHERE a` |

## Statements one request runs many times

The statements a warm request ran 20 times or more (one per row: an N+1), as admin.

| address | times | statement |
|---|---:|---|
| `/activity/events.csv` | 211 | `PRAGMA journal_mode=WAL` |
| `/activity/events.csv` | 208 | `SELECT e.*, i.vendor_name AS invoice_vendor, i.invoice_number AS invoice_number FROM events e LEFT JOIN invoices i ON i.id = e.invoice_id WHERE ? = ? AND e.id < ? ORDER BY e.id DESC LIMIT ?` |
| `/search?q=FTC6962398` | 224 | `-- SELECT pgno FROM ?.? WHERE segid=? AND term<=? ORDER BY term DESC LIMIT ?` |
| `/month-end` | 139 | `WITH coded AS (SELECT id, COALESCE(NULLIF(final_output, ?), NULLIF(ai_output, ?), ?) AS doc FROM invoices WHERE id IN (SELECT value FROM json_each(?))), read AS (SELECT id, doc, CASE WHEN NOT json_val` |
| `/month-end` | 51 | `SELECT final_gl gl_code, COUNT(*) lines, SUM(outcome = ?) corrected FROM feedback WHERE vendor_key = ? GROUP BY final_gl ORDER BY lines DESC` |
| `/month-end/accruals.csv` | 139 | `WITH coded AS (SELECT id, COALESCE(NULLIF(final_output, ?), NULLIF(ai_output, ?), ?) AS doc FROM invoices WHERE id IN (SELECT value FROM json_each(?))), read AS (SELECT id, doc, CASE WHEN NOT json_val` |
| `/month-end/accruals.csv` | 51 | `SELECT final_gl gl_code, COUNT(*) lines, SUM(outcome = ?) corrected FROM feedback WHERE vendor_key = ? GROUP BY final_gl ORDER BY lines DESC` |
| `/search/export.csv` | 52 | `SELECT ? FROM summary_dirty LIMIT ?` |
| `/search/export.csv` | 50 | `WITH rows AS ( SELECT id, status, file_name, vendor_name, invoice_number, invoice_date, due_date, currency, grand_total, created_at, reviewed_at, reviewer, second_reviewer, second_reviewed_at, export_` |
| `/search/export.csv` | 50 | `SELECT id, created_at FROM export_batches WHERE id IN (SELECT value FROM json_each(?))` |
| `/search/export.xlsx` | 52 | `SELECT ? FROM summary_dirty LIMIT ?` |
| `/search/export.xlsx` | 50 | `WITH rows AS ( SELECT id, status, file_name, vendor_name, invoice_number, invoice_date, due_date, currency, grand_total, created_at, reviewed_at, reviewer, second_reviewer, second_reviewed_at, export_` |
| `/search/export.xlsx` | 50 | `SELECT id, created_at FROM export_batches WHERE id IN (SELECT value FROM json_each(?))` |
| `/search?q=Frontier` | 84 | `-- SELECT pgno FROM ?.? WHERE segid=? AND term<=? ORDER BY term DESC LIMIT ?` |

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
