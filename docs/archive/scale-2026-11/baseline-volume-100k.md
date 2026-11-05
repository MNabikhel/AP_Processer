# Volume benchmark: one person on big data

100,000 invoices, 415,762 events, 421,954 lines of feedback, 200,000 JD Edwards register rows; asked as admin (admin001), clerk (clerk001), the median of 3 after a warm request; 4 cores, Python 3.12.13, SQLite 3.53.1.

**165 addresses, 43 over target, 0 errors (5xx), 45 full scans of big tables.** Targets: a page 300 ms, a report, export or search 3 s.

| address | role | kind | status | median s | warm s | KB | SQL | peak MB | target | |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| `/spend/data.xlsx` | admin | export | 200 | 251.251 | 251.251 | 97,373 | 13 |  | 3 | **over** |
| `/spend/data.xlsx` | clerk | export | 200 | 245.279 | 245.279 | 97,373 | 12 |  | 3 | **over** |
| `/month-end` | admin | report | 200 | 201.710 | 201.710 | 2,069 | 10863 |  | 3 | **over** |
| `/month-end/accruals.csv` | admin | export | 200 | 195.209 | 195.209 | 788 | 10858 |  | 3 | **over** |
| `/month-end/accruals.csv` | clerk | export | 200 | 193.577 | 193.577 | 788 | 10858 |  | 3 | **over** |
| `/month-end` | clerk | report | 200 | 190.093 | 190.093 | 2,069 | 10859 |  | 3 | **over** |
| `/help/report` | admin | report | 200 | 22.256 | 22.256 | 27,256 | 100126 |  | 3 | **over** |
| `/activity?q=Frontier` | admin | page | 200 | 19.621 | 19.621 | 67 | 92217 |  | 0.3 | **over** |
| `/activity?q=Frontier` | clerk | page | 200 | 19.567 | 19.567 | 65 | 92217 |  | 0.3 | **over** |
| `/help/report` | clerk | report | 200 | 19.141 | 19.141 | 27,252 | 100125 |  | 3 | **over** |
| `/activity/controls-report` | clerk | report | 200 | 17.322 | 17.322 | 21 | 92210 |  | 3 | **over** |
| `/activity/controls-report` | admin | report | 200 | 17.305 | 17.305 | 21 | 92210 |  | 3 | **over** |
| `/exports/duplicates.csv` | admin | export | 200 | 13.558 | 13.558 | 227 | 12 |  | 3 | **over** |
| `/exports/duplicates.csv` | clerk | export | 200 | 13.534 | 13.534 | 227 | 11 |  | 3 | **over** |
| `/activity` | clerk | page | 200 | 12.397 | 12.397 | 130 | 92217 |  | 0.3 | **over** |
| `/activity` | admin | page | 200 | 12.364 | 12.364 | 115 | 92218 |  | 0.3 | **over** |
| `/search/export.xlsx` | clerk | export | 200 | 8.550 | 7.874 | 3,182 | 19 | 257.7 | 3 | **over** |
| `/learning?tab=suppliers` | clerk | page | 200 | 7.953 | 8.269 | 9,268 | 9038 |  | 0.3 | **over** |
| `/learning?tab=suppliers` | admin | page | 200 | 7.915 | 8.343 | 13,330 | 9038 |  | 0.3 | **over** |
| `/process` | clerk | page | 200 | 7.551 | 7.162 | 15 | 100045 |  | 0.3 | **over** |
| `/search/export.xlsx` | admin | export | 200 | 7.484 | 7.209 | 3,182 | 17 | 257.7 | 3 | **over** |
| `/process` | admin | page | 200 | 6.383 | 6.587 | 16 | 100046 |  | 0.3 | **over** |
| `/spend` | admin | report | 200 | 4.824 | 4.431 | 36 | 19 | 512.0 | 3 | **over** |
| `/spend` | clerk | report | 200 | 4.790 | 4.913 | 36 | 17 | 512.0 | 3 | **over** |
| `/settings/automation` | admin | page | 200 | 4.729 | 5.133 | 15 | 6038 |  | 0.3 | **over** |
| `/exports?fmt=jde` | clerk | page | 200 | 4.319 | 4.031 | 414 | 44 |  | 0.3 | **over** |
| `/exports?fmt=jde` | admin | page | 200 | 4.133 | 4.573 | 449 | 45 |  | 0.3 | **over** |
| `/exports/jde/dry-run.txt` | clerk | export | 200 | 3.790 | 3.752 | 115 | 19 | 323.9 | 3 | **over** |
| `/exports/jde/dry-run.txt` | admin | export | 200 | 3.691 | 3.820 | 115 | 23 | 323.9 | 3 | **over** |
| `/settings/jde/worksheet.xlsx` | admin | export | 200 | 3.451 | 3.429 | 122 | 17 | 30.4 | 3 | **over** |
| `/search?q=FTC6962398` | clerk | report | 200 | 2.725 | 3.223 | 30 | 150 | 120.3 | 3 | ok |
| `/search?q=FTC6962398` | admin | report | 200 | 2.530 | 2.683 | 30 | 148 | 120.3 | 3 | ok |
| `/settings/reading` | admin | page | 200 | 2.425 | 2.441 | 19 | 30 |  | 0.3 | **over** |
| `/sales-tax` | clerk | report | 200 | 1.975 | 1.680 | 11,092 | 29 | 73.3 | 3 | ok |
| `/sales-tax` | admin | report | 200 | 1.939 | 1.692 | 11,093 | 31 | 73.3 | 3 | ok |
| `/search?q=Frontier` | clerk | report | 200 | 1.632 | 1.679 | 1,643 | 14571 | 125.9 | 3 | ok |
| `/search?q=Frontier` | admin | report | 200 | 1.609 | 1.614 | 1,643 | 14570 | 125.9 | 3 | ok |
| `/insights` | admin | report | 200 | 1.377 | 1.248 | 39 | 43 | 94.5 | 3 | ok |
| `/search/export.csv` | clerk | export | 200 | 1.319 | 1.145 | 10,239 | 19 | 145.3 | 3 | ok |
| `/learning?tab=coding` | clerk | page | 200 | 1.282 | 1.223 | 122 | 28 |  | 0.3 | **over** |
| `/insights` | clerk | report | 200 | 1.256 | 1.515 | 39 | 40 | 94.5 | 3 | ok |
| `/learning?tab=coding` | admin | page | 200 | 1.242 | 2.381 | 164 | 34 |  | 0.3 | **over** |
| `/search/export.csv` | admin | export | 200 | 1.012 | 1.177 | 10,239 | 17 | 145.3 | 3 | ok |
| `/settings/data` | admin | page | 200 | 0.777 | 0.962 | 15 | 26 |  | 0.3 | **over** |
| `/sales-tax/claims.csv` | clerk | export | 200 | 0.696 | 0.600 | 4,292 | 14 | 44.9 | 3 | ok |
| `/review/98499` | admin | page | 200 | 0.689 | 3.266 | 3,613 | 689 |  | 0.3 | **over** |
| `/sales-tax/claims.csv` | admin | export | 200 | 0.685 | 0.512 | 4,292 | 14 | 44.9 | 3 | ok |
| `/review/98499` | clerk | page | 200 | 0.626 | 0.587 | 3,612 | 675 |  | 0.3 | **over** |
| `/sales-tax/self-assessment.csv` | clerk | export | 200 | 0.526 | 0.631 | 0 | 17 | 21.4 | 3 | ok |
| `/learning?tab=lessons` | admin | page | 200 | 0.503 | 0.385 | 634 | 15 |  | 0.3 | **over** |
| `/sales-tax/self-assessment.csv` | admin | export | 200 | 0.484 | 0.618 | 0 | 17 | 21.4 | 3 | ok |
| `/insights/business-case` | clerk | report | 200 | 0.409 | 0.405 | 3 | 26 | 13.6 | 3 | ok |
| `/vendors` | admin | page | 200 | 0.408 | 1.259 | 1,765 | 24 |  | 0.3 | **over** |
| `/insights/business-case` | admin | report | 200 | 0.376 | 0.409 | 3 | 26 | 13.6 | 3 | ok |
| `/learning?tab=lessons` | clerk | page | 200 | 0.355 | 0.571 | 482 | 19 |  | 0.3 | **over** |
| `/purchase-orders` | admin | page | 200 | 0.349 | 0.246 | 2,035 | 17 |  | 0.3 | **over** |
| `/review?tab=approved` | clerk | page | 200 | 0.338 | 0.325 | 44 | 40 |  | 0.3 | **over** |
| `/review?tab=approved` | admin | page | 200 | 0.333 | 0.339 | 44 | 40 |  | 0.3 | **over** |
| `/vendors` | clerk | page | 200 | 0.329 | 0.490 | 1,765 | 21 |  | 0.3 | **over** |
| `/exports/batches/254/approved-pdfs.zip` | clerk | export | 200 | 0.262 | 0.315 | 45 | 15 | 0.8 | 3 | ok |
| `/review?show=attention` | clerk | page | 200 | 0.249 | 0.353 | 82 | 41 |  | 0.3 | ok |
| `/exports/batches/254/approved-pdfs.zip` | admin | export | 200 | 0.237 | 0.232 | 45 | 15 | 0.8 | 3 | ok |
| `/vendors?q=Frontier` | clerk | page | 200 | 0.235 | 0.196 | 143 | 21 |  | 0.3 | ok |
| `/review` | admin | page | 200 | 0.233 | 1.285 | 83 | 53 |  | 0.3 | ok |
| `/review?tab=other` | admin | page | 200 | 0.231 | 0.255 | 1,419 | 38 |  | 0.3 | ok |
| `/statements` | clerk | report | 200 | 0.226 | 0.208 | 268 | 17 | 23.4 | 3 | ok |
| `/review?sort=amount` | admin | page | 200 | 0.225 | 0.221 | 83 | 44 |  | 0.3 | ok |
| `/review?sort=amount` | clerk | page | 200 | 0.221 | 0.242 | 83 | 44 |  | 0.3 | ok |
| `/purchase-orders` | clerk | page | 200 | 0.219 | 0.244 | 2,034 | 17 |  | 0.3 | ok |
| `/review?tab=other` | clerk | page | 200 | 0.218 | 0.231 | 988 | 38 |  | 0.3 | ok |
| `/vendors?q=Frontier` | admin | page | 200 | 0.216 | 0.220 | 144 | 21 |  | 0.3 | ok |
| `/review?show=ready` | admin | page | 200 | 0.213 | 0.200 | 10 | 42 |  | 0.3 | ok |
| `/purchase-orders?status=all` | admin | page | 200 | 0.212 | 0.211 | 2,058 | 17 |  | 0.3 | ok |
| `/statements` | admin | report | 200 | 0.211 | 0.645 | 269 | 22 | 23.4 | 3 | ok |
| `/review` | clerk | page | 200 | 0.210 | 0.288 | 82 | 44 |  | 0.3 | ok |
| `/review?show=ready` | clerk | page | 200 | 0.209 | 0.226 | 10 | 42 |  | 0.3 | ok |
| `/review?show=attention` | admin | page | 200 | 0.208 | 0.198 | 83 | 41 |  | 0.3 | ok |
| `/purchase-orders?status=all` | clerk | page | 200 | 0.205 | 0.338 | 2,057 | 17 |  | 0.3 | ok |
| `/review?tab=parked` | clerk | page | 200 | 0.198 | 0.185 | 101 | 37 |  | 0.3 | ok |
| `/review?tab=second` | clerk | page | 200 | 0.188 | 0.228 | 67 | 38 |  | 0.3 | ok |
| `/review?tab=second` | admin | page | 200 | 0.187 | 0.203 | 152 | 38 |  | 0.3 | ok |
| `/review?tab=parked` | admin | page | 200 | 0.177 | 0.176 | 101 | 37 |  | 0.3 | ok |
| `/exports` | admin | page | 200 | 0.123 | 0.638 | 347 | 50 |  | 0.3 | ok |
| `/settings/jde` | admin | page | 200 | 0.117 | 0.157 | 41 | 23 |  | 0.3 | ok |
| `/search/10/approved.pdf` | admin | export | 200 | 0.116 | 0.140 | 15 | 12 | 0.2 | 3 | ok |
| `/search/10/approved.pdf` | clerk | export | 200 | 0.114 | 0.120 | 15 | 12 | 0.2 | 3 | ok |
| `/review/10/approved.pdf` | clerk | export | 200 | 0.113 | 0.113 | 15 | 12 | 0.2 | 3 | ok |
| `/exports` | clerk | page | 200 | 0.112 | 0.164 | 314 | 40 |  | 0.3 | ok |
| `/review/10/approved.pdf` | admin | export | 200 | 0.111 | 0.123 | 15 | 12 | 0.2 | 3 | ok |
| `/search?page=40` | clerk | page | 200 | 0.104 | 0.106 | 151 | 23 |  | 0.3 | ok |
| `/search?page=40` | admin | page | 200 | 0.103 | 0.103 | 152 | 20 |  | 0.3 | ok |
| `/search` | clerk | page | 200 | 0.095 | 0.116 | 141 | 27 |  | 0.3 | ok |
| `/exports/batches/254/download` | clerk | page | 200 | 0.075 | 0.102 | 6 | 18 |  | 0.3 | ok |
| `/review/97068` | clerk | page | 200 | 0.074 | 0.061 | 26 | 41 |  | 0.3 | ok |
| `/exports/batches/254/download` | admin | page | 200 | 0.067 | 0.076 | 6 | 18 |  | 0.3 | ok |
| `/activity/events.csv` | admin | export | 200 | 0.066 | 0.075 | 572 | 10 | 10.5 | 3 | ok |
| `/review/97005` | clerk | page | 200 | 0.063 | 0.074 | 26 | 42 |  | 0.3 | ok |
| `/search` | admin | page | 200 | 0.063 | 0.104 | 141 | 22 |  | 0.3 | ok |
| `/accounts?tab=rules` | admin | page | 200 | 0.058 | 0.057 | 456 | 20 |  | 0.3 | ok |
| `/review/10` | clerk | page | 200 | 0.058 | 0.060 | 20 | 40 |  | 0.3 | ok |
| `/accounts?tab=rules` | clerk | page | 200 | 0.054 | 0.067 | 9 | 20 |  | 0.3 | ok |
| `/activity/events.csv` | clerk | export | 200 | 0.054 | 0.092 | 387 | 10 | 7.1 | 3 | ok |
| `/review/97005` | admin | page | 200 | 0.052 | 0.054 | 27 | 42 |  | 0.3 | ok |
| `/review/10` | admin | page | 200 | 0.051 | 0.080 | 20 | 40 |  | 0.3 | ok |
| `/review/97068` | admin | page | 200 | 0.050 | 0.066 | 26 | 41 |  | 0.3 | ok |
| `/accounts?tab=gl` | clerk | page | 200 | 0.044 | 0.035 | 1,184 | 18 |  | 0.3 | ok |
| `/accounts?tab=tax` | clerk | page | 200 | 0.039 | 0.038 | 473 | 19 |  | 0.3 | ok |
| `/accounts?tab=gl` | admin | page | 200 | 0.030 | 0.074 | 1,186 | 18 |  | 0.3 | ok |
| `/accounts?tab=tax` | admin | page | 200 | 0.027 | 0.037 | 473 | 19 |  | 0.3 | ok |
| `/accounts/gl_accounts.csv` | admin | export | 200 | 0.021 | 0.017 | 107 | 10 | 1.1 | 3 | ok |
| `/admin/users` | admin | page | 200 | 0.018 | 0.038 | 814 | 15 |  | 0.3 | ok |
| `/accounts?tab=bank` | admin | page | 200 | 0.017 | 0.019 | 288 | 23 |  | 0.3 | ok |
| `/accounts?tab=policy` | clerk | page | 200 | 0.017 | 0.018 | 9 | 17 |  | 0.3 | ok |
| `/accounts?tab=cc` | clerk | page | 200 | 0.015 | 0.018 | 187 | 18 |  | 0.3 | ok |
| `/settings/about` | admin | page | 200 | 0.014 | 0.016 | 10 | 18 |  | 0.3 | ok |
| `/accounts?tab=policy` | admin | page | 200 | 0.014 | 0.023 | 9 | 17 |  | 0.3 | ok |
| `/accounts?tab=cc` | admin | page | 200 | 0.014 | 0.015 | 190 | 18 |  | 0.3 | ok |
| `/accounts/gl_accounts.csv` | clerk | export | 200 | 0.013 | 0.016 | 107 | 10 | 1.1 | 3 | ok |
| `/accounts?tab=bank` | clerk | page | 200 | 0.013 | 0.018 | 9 | 19 |  | 0.3 | ok |
| `/review/98499/reader` | clerk | page | 200 | 0.012 | 0.013 | 0 | 12 |  | 0.3 | ok |
| `/review/98499/reader` | admin | page | 200 | 0.012 | 0.012 | 0 | 12 |  | 0.3 | ok |
| `/review/98499/words` | admin | page | 200 | 0.011 | 0.012 | 8 | 4 |  | 0.3 | ok |
| `/review/98499/words` | clerk | page | 200 | 0.011 | 0.012 | 8 | 4 |  | 0.3 | ok |
| `/statements/reconciliation.csv` | clerk | export | 404 | 0.009 | 0.011 | 6 | 15 |  | 3 | ok |
| `/help` | admin | page | 200 | 0.009 | 0.028 | 126 | 20 |  | 0.3 | ok |
| `/statements/reconciliation.csv` | admin | export | 404 | 0.009 | 0.013 | 7 | 15 |  | 3 | ok |
| `/learning/training.zip` | admin | export | 404 | 0.009 | 0.009 | 7 | 16 |  | 3 | ok |
| `/settings` | admin | page | 200 | 0.009 | 0.025 | 12 | 34 |  | 0.3 | ok |
| `/accounts/cost_centers.csv` | admin | export | 200 | 0.009 | 0.009 | 9 | 10 | 0.3 | 3 | ok |
| `/accounts/cost_centers.csv` | clerk | export | 200 | 0.009 | 0.009 | 9 | 10 | 0.3 | 3 | ok |
| `/settings/review` | admin | page | 200 | 0.008 | 0.009 | 12 | 30 |  | 0.3 | ok |
| `/settings/hub` | admin | page | 200 | 0.008 | 0.017 | 13 | 27 |  | 0.3 | ok |
| `/learning?tab=readers` | clerk | page | 200 | 0.008 | 0.008 | 7 | 12 |  | 0.3 | ok |
| `/purchase-orders/template.csv` | admin | export | 200 | 0.007 | 0.008 | 0 | 8 | 0.1 | 3 | ok |
| `/settings/azure-check` | admin | page | 200 | 0.007 | 0.012 | 7 | 16 |  | 0.3 | ok |
| `/help/quick-start` | admin | page | 200 | 0.007 | 0.013 | 11 | 11 |  | 0.3 | ok |
| `/help/quick-start` | clerk | page | 200 | 0.007 | 0.009 | 11 | 15 |  | 0.3 | ok |
| `/help` | clerk | page | 200 | 0.007 | 0.010 | 124 | 16 |  | 0.3 | ok |
| `/account/password` | clerk | page | 200 | 0.006 | 0.007 | 7 | 13 |  | 0.3 | ok |
| `/who` | admin | page | 303 | 0.006 | 0.006 | 0 | 3 |  | 0.3 | ok |
| `/login` | admin | page | 303 | 0.006 | 0.007 | 0 | 3 |  | 0.3 | ok |
| `/process/status` | clerk | page | 200 | 0.006 | 0.008 | 0 | 3 |  | 0.3 | ok |
| `/accounts/bank/template.csv` | admin | export | 200 | 0.006 | 0.008 | 0 | 8 | 0.1 | 3 | ok |
| `/learning?tab=readers` | admin | page | 200 | 0.006 | 0.007 | 7 | 12 |  | 0.3 | ok |
| `/who` | clerk | page | 303 | 0.006 | 0.007 | 0 | 3 |  | 0.3 | ok |
| `/purchase-orders/template.csv` | clerk | export | 200 | 0.006 | 0.007 | 0 | 8 | 0.1 | 3 | ok |
| `/account/password` | admin | page | 200 | 0.006 | 0.010 | 8 | 13 |  | 0.3 | ok |
| `/process/status` | admin | page | 200 | 0.006 | 0.007 | 0 | 3 |  | 0.3 | ok |
| `/review/98499/page/1.png` | clerk | page | 200 | 0.005 | 0.006 | 63 | 4 |  | 0.3 | ok |
| `/login` | clerk | page | 303 | 0.005 | 0.005 | 0 | 3 |  | 0.3 | ok |
| `/login/new-password` | clerk | page | 303 | 0.005 | 0.005 | 0 | 3 |  | 0.3 | ok |
| `/login/code` | clerk | page | 303 | 0.005 | 0.005 | 0 | 3 |  | 0.3 | ok |
| `/accounts/bank/template.csv` | clerk | export | 200 | 0.005 | 0.006 | 0 | 8 | 0.1 | 3 | ok |
| `/review/98499/page/1.png` | admin | page | 200 | 0.005 | 0.032 | 63 | 4 |  | 0.3 | ok |
| `/login/authenticator` | clerk | page | 303 | 0.005 | 0.005 | 0 | 3 |  | 0.3 | ok |
| `/login/new-password` | admin | page | 303 | 0.005 | 0.005 | 0 | 3 |  | 0.3 | ok |
| `/login/authenticator` | admin | page | 303 | 0.005 | 0.005 | 0 | 3 |  | 0.3 | ok |
| `/login/code` | admin | page | 303 | 0.005 | 0.005 | 0 | 3 |  | 0.3 | ok |
| `/help/report/setup` | admin | report | 200 | 0.005 | 0.046 | 3 | 327 | 0.1 | 3 | ok |
| `/` | admin | page | 307 | 0.004 | 0.006 | 0 | 2 |  | 0.3 | ok |
| `/healthz` | admin | page | 200 | 0.004 | 0.005 | 0 | 2 |  | 0.3 | ok |
| `/jobs/process` | admin | page | 200 | 0.003 | 0.004 | 0 | 2 |  | 0.3 | ok |
| `/` | clerk | page | 307 | 0.003 | 0.004 | 0 | 2 |  | 0.3 | ok |
| `/healthz` | clerk | page | 200 | 0.003 | 0.003 | 0 | 2 |  | 0.3 | ok |
| `/jobs/process` | clerk | page | 200 | 0.003 | 0.003 | 0 | 2 |  | 0.3 | ok |

## Full scans of big tables

Each `SCAN` in the plan of a statement a warm request ran, on a table with 12,940 rows or more; `how`: the table itself or an index read whole.

| table | rows | how | routes | statement |
|---|---:|---|---|---|
| invoices | 100,000 | table | `/exports/duplicates.csv` | `SELECT json_group_array(json_array(CASE typeof(q."id") WHEN ? THEN json_object(?, printf(?, q."id")) WHEN ? THEN q."id" \|\| ? WHEN ? THEN json_object(?, hex(q."id")) ELSE q."id" END, CASE typeof(q."status") WHEN ? THEN js` |
| invoices | 100,000 | table | `/spend/data.xlsx` | `SELECT json_group_array(json_array(CASE typeof(q."id") WHEN ? THEN json_object(?, printf(?, q."id")) WHEN ? THEN q."id" \|\| ? WHEN ? THEN json_object(?, hex(q."id")) ELSE q."id" END, CASE typeof(q."status") WHEN ? THEN js` |
| events | 415,762 | table | `/activity`, `/activity?q=Frontier`, `/activity/events.csv`, `/activity/controls-report` | `SELECT e.*, i.vendor_name AS invoice_vendor, i.invoice_number AS invoice_number FROM events e LEFT JOIN invoices i ON i.id = e.invoice_id WHERE ? = ? ORDER BY e.id DESC LIMIT ?` |
| feedback | 421,954 | table | `/review/98499`, `/accounts?tab=rules`, `/learning?tab=coding`, `/settings/data`, `/help/report` | `SELECT (SELECT MAX(id) FROM feedback), (SELECT TOTAL(n) FROM feedback_counts), (SELECT created_at \|\| ? \|\| COALESCE(vendor_key, ?) \|\| ? \|\| COALESCE(description, ?) \|\| ? \|\| COALESCE(final_gl, ?) FROM feedback ORDER BY id D` |
| feedback | 421,954 | table | `/review/98499`, `/learning?tab=coding` | `SELECT json_group_array(json_array(CASE typeof(q."id") WHEN ? THEN json_object(?, printf(?, q."id")) WHEN ? THEN q."id" \|\| ? WHEN ? THEN json_object(?, hex(q."id")) ELSE q."id" END, CASE typeof(q."invoice_id") WHEN ? THE` |
| feedback | 421,954 | table | `/learning?tab=coding` | `SELECT * FROM (SELECT f.invoice_id, f.line_number, f.vendor_name, f.description, f.final_tax_code, f.created_at, f.reviewer, i.invoice_number, (SELECT json_extract(c.value, ?) FROM events e, json_each(e.detail, ?) c WHER` |
| feedback | 421,954 | table | `/learning?tab=coding` | `SELECT substr(created_at, ?, ?) day, SUM(outcome = ?) accepted, SUM(outcome = ?) corrected FROM feedback WHERE outcome NOT IN (?, ?, ?) GROUP BY day` |
| feedback | 421,954 | COVERING INDEX feedback_vendor_outcome | `/statements`, `/vendors`, `/vendors?q=Frontier`, `/settings/jde/worksheet.xlsx` | `SELECT vendor_key, COUNT(*) lessons, SUM(outcome NOT IN (?, ?, ?)) lines, SUM(outcome = ?) accepted FROM feedback GROUP BY vendor_key` |
| feedback | 421,954 | INDEX feedback_vendor | `/learning?tab=coding` | `SELECT vendor_name, COUNT(*) lines, SUM(outcome = ?) accepted, SUM(outcome = ?) corrected, MAX(created_at) last_seen FROM feedback WHERE outcome NOT IN (?, ?, ?) GROUP BY vendor_key ORDER BY lines DESC` |
| erp_invoices | 200,000 | table | `/search?q=Frontier`, `/search?q=FTC6962398`, `/exports?fmt=jde`, `/exports/jde/dry-run.txt`, `/exports/duplicates.csv` | `SELECT vendor_key, vendor_name, invoice_number, number_key, invoice_date, total, copies FROM erp_invoices` |
| erp_invoices | 200,000 | COVERING INDEX sqlite_autoindex_erp_invoices_1 | `/exports`, `/exports?fmt=jde` | `SELECT COUNT(*) FROM erp_invoices` |
| reader_outcomes | 1,957,734 | INDEX reader_outcomes_reader | `/settings/reading` | `SELECT reader, COUNT(*) fields, SUM(correct) agreed, COUNT(DISTINCT invoice_id) invoices FROM reader_outcomes GROUP BY reader ORDER BY reader` |
| lesson_approvals | 1,945,789 | table | `/learning?tab=coding` | `SELECT a.invoice_id, a.value, a.proposed, a.at, l.vendor_name, l.field, l.pattern, i.invoice_number, i.reviewer, n.corrected next_corrected, n.value next_value, n.invoice_id next_invoice_id, n.at next_at FROM lesson_appr` |
| supplier_outcomes | 1,101,881 | INDEX supplier_outcomes_invoice | `/settings/automation` | `SELECT invoice_id, MIN(correct) AS clean FROM supplier_outcomes WHERE source = ? AND at >= ? GROUP BY invoice_id` |
| line_texts | 421,954 | table | `/learning?tab=coding` | `SELECT t.invoice_id, t.printed, t.description, t.at, i.vendor_name, i.invoice_number, i.reviewer, n.corrected next_corrected, n.description next_value, n.invoice_id next_invoice_id, n.at next_at FROM line_texts t LEFT JO` |
| invoice_summary | 100,000 | table | `/review` | `SELECT json_group_array(json_array(CASE typeof(q."vendor_key") WHEN ? THEN json_object(?, printf(?, q."vendor_key")) WHEN ? THEN q."vendor_key" \|\| ? WHEN ? THEN json_object(?, hex(q."vendor_key")) ELSE q."vendor_key" END` |
| invoice_summary | 100,000 | table | `/process` | `SELECT json_group_array(json_array(CASE typeof(q."id") WHEN ? THEN json_object(?, printf(?, q."id")) WHEN ? THEN q."id" \|\| ? WHEN ? THEN json_object(?, hex(q."id")) ELSE q."id" END, CASE typeof(q."file_name") WHEN ? THEN` |
| invoice_summary | 100,000 | table | `/search`, `/search?page=40`, `/search/export.csv`, `/search/export.xlsx` | `WITH rows AS ( SELECT id, status, file_name, vendor_name, invoice_number, invoice_date, due_date, currency, grand_total, created_at, reviewed_at, reviewer, second_reviewer, second_reviewed_at, export_batch, parked_reason` |
| invoice_summary | 100,000 | table | `/exports?fmt=jde`, `/exports/jde/dry-run.txt` | `SELECT json_group_array(json_array(CASE typeof(q."id") WHEN ? THEN json_object(?, printf(?, q."id")) WHEN ? THEN q."id" \|\| ? WHEN ? THEN json_object(?, hex(q."id")) ELSE q."id" END, CASE typeof(q."status") WHEN ? THEN js` |
| invoice_summary | 100,000 | table | `/sales-tax`, `/sales-tax/claims.csv` | `SELECT json_group_array(json_array(CASE typeof(q."id") WHEN ? THEN json_object(?, printf(?, q."id")) WHEN ? THEN q."id" \|\| ? WHEN ? THEN json_object(?, hex(q."id")) ELSE q."id" END, CASE typeof(q."status") WHEN ? THEN js` |
| invoice_summary | 100,000 | table | `/vendors` | `SELECT json_group_array(json_array(CASE typeof(q."id") WHEN ? THEN json_object(?, printf(?, q."id")) WHEN ? THEN q."id" \|\| ? WHEN ? THEN json_object(?, hex(q."id")) ELSE q."id" END, CASE typeof(q."status") WHEN ? THEN js` |
| invoice_summary | 100,000 | table | `/learning?tab=coding`, `/activity`, `/activity?q=Frontier` | `SELECT json_group_array(json_array(CASE typeof(q."id") WHEN ? THEN json_object(?, printf(?, q."id")) WHEN ? THEN q."id" \|\| ? WHEN ? THEN json_object(?, hex(q."id")) ELSE q."id" END, CASE typeof(q."broken") WHEN ? THEN js` |
| invoice_summary | 100,000 | table | `/spend` | `SELECT json_group_array(json_array(CASE typeof(q."id") WHEN ? THEN json_object(?, printf(?, q."id")) WHEN ? THEN q."id" \|\| ? WHEN ? THEN json_object(?, hex(q."id")) ELSE q."id" END)) FROM (SELECT id FROM invoice_summary ` |
| invoice_summary | 100,000 | table | `/insights`, `/insights/business-case` | `SELECT status = ?, COUNT(*), SUM(CASE WHEN requires_review THEN ? ELSE ? END) FROM invoice_summary WHERE broken & ? = ? GROUP BY ?` |
| invoice_summary | 100,000 | table | `/insights`, `/insights/business-case` | `SELECT substr(created_at, ?, ?), COUNT(*) FROM invoice_summary WHERE +status != ? AND broken & ? = ? GROUP BY ?` |
| invoice_summary | 100,000 | table | `/insights`, `/insights/business-case` | `SELECT substr(a_at, ?, ?), a_corrected, a_filled, a_bulk, COUNT(*) FROM invoice_summary WHERE +status IN (?, ?) AND a_at IS NOT NULL AND broken & ? = ? GROUP BY ?, ?, ?, ?` |
| invoice_summary | 100,000 | table | `/insights`, `/insights/business-case` | `SELECT i_seconds FROM invoice_summary WHERE +status != ? AND i_seconds IS NOT NULL AND broken & ? = ? ORDER BY id` |
| invoice_summary | 100,000 | table | `/insights`, `/insights/business-case` | `SELECT json_group_array(json_array(CASE typeof(q."id") WHEN ? THEN json_object(?, printf(?, q."id")) WHEN ? THEN q."id" \|\| ? WHEN ? THEN json_object(?, hex(q."id")) ELSE q."id" END, CASE typeof(q."status") WHEN ? THEN js` |
| invoice_summary | 100,000 | table | `/insights` | `SELECT json_group_array(json_array(CASE typeof(q."id") WHEN ? THEN json_object(?, printf(?, q."id")) WHEN ? THEN q."id" \|\| ? WHEN ? THEN json_object(?, hex(q."id")) ELSE q."id" END, CASE typeof(q."status") WHEN ? THEN js` |
| invoice_summary | 100,000 | table | `/help/report` | `SELECT json_group_array(json_array(CASE typeof(q."id") WHEN ? THEN json_object(?, printf(?, q."id")) WHEN ? THEN q."id" \|\| ? WHEN ? THEN json_object(?, hex(q."id")) ELSE q."id" END, CASE typeof(q."file_name") WHEN ? THEN` |
| invoice_summary | 100,000 | COVERING INDEX summary_edits | `/review/98499` | `SELECT status, COUNT(*) FROM invoice_summary GROUP BY status` |
| invoice_summary | 100,000 | COVERING INDEX summary_broken | `/search`, `/search?page=40`, `/search/export.csv`, `/search/export.xlsx` | `WITH rows AS ( SELECT id, status, file_name, vendor_name, invoice_number, invoice_date, due_date, currency, grand_total, created_at, reviewed_at, reviewer, second_reviewer, second_reviewed_at, export_batch, parked_reason` |
| invoice_summary | 100,000 | INDEX summary_vendor | `/statements` | `SELECT json_group_array(json_array(CASE typeof(q."vendor_key") WHEN ? THEN json_object(?, printf(?, q."vendor_key")) WHEN ? THEN q."vendor_key" \|\| ? WHEN ? THEN json_object(?, hex(q."vendor_key")) ELSE q."vendor_key" END` |
| invoice_summary | 100,000 | COVERING INDEX summary_edits | `/learning?tab=coding`, `/settings/data`, `/help/report` | `SELECT status, COUNT(*) n FROM invoice_summary GROUP BY status` |
| summary_codes | 271,688 | COVERING INDEX summary_codes_code | `/insights`, `/insights/business-case` | `SELECT c.code, SUM(c.n), MIN(c.id), MIN(c.pos) FROM summary_codes c JOIN (SELECT code, MIN(id) first FROM summary_codes GROUP BY code) f ON f.code = c.code AND c.id = f.first GROUP BY c.code` |
| summary_codes | 271,688 | COVERING INDEX summary_codes_code | `/insights`, `/insights/business-case` | `SELECT code, SUM(n) FROM summary_codes GROUP BY code` |
| vendors | 19,008 | table | `/review/98499`, `/review/10`, `/review/97005`, `/review/97068` | `SELECT * FROM vendors WHERE vendor_key = ? OR substr(vendor_key, ?, ?) = ?` |
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
| `/help/report` | 100,000 | `SELECT * FROM invoices WHERE id = ?` |
| `/process` | 100,000 | `SELECT id, status FROM invoices WHERE (file_sha256 = ? OR original_sha256 = ?) ORDER BY id DESC` |
| `/activity` | 92,197 | `SELECT value FROM settings WHERE key = ?` |
| `/activity?q=Frontier` | 92,197 | `SELECT value FROM settings WHERE key = ?` |
| `/activity/controls-report` | 92,197 | `SELECT value FROM settings WHERE key = ?` |
| `/search?q=Frontier` | 7,256 | `SELECT ? FROM summary_dirty LIMIT ?` |
| `/search?q=Frontier` | 7,248 | `SELECT json_group_array(json_array(CASE typeof(q."vendor_name") WHEN ? THEN json_object(?, printf(?, q."vendor_name")) WHEN ? THEN q."vendor_name" \|\| ? WHEN ? THEN json_object(?, hex(q."vendor_name"))` |
| `/search?q=Frontier` | 42 | `-- SELECT pgno FROM ?.? WHERE segid=? AND term<=? ORDER BY term DESC LIMIT ?` |
| `/month-end` | 2,965 | `SELECT * FROM purchase_orders WHERE po_key = ?` |
| `/month-end` | 2,965 | `SELECT * FROM po_lines WHERE po_key = ? ORDER BY line_number` |
| `/month-end` | 2,965 | `SELECT id, status, invoice_number, invoice_date, ai_output, final_output, export_batch FROM invoices WHERE po_key = ? AND status IN (?, ?, ?, ?) ORDER BY id` |
| `/month-end` | 1,889 | `SELECT id, status, invoice_number, invoice_date, ai_output, final_output FROM invoices WHERE vendor_key = ? AND po_key = ? AND status IN (?, ?, ?, ?) ORDER BY id` |
| `/month-end` | 51 | `SELECT final_gl gl_code, COUNT(*) lines, SUM(outcome = ?) corrected FROM feedback WHERE vendor_key = ? GROUP BY final_gl ORDER BY lines DESC` |
| `/month-end/accruals.csv` | 2,965 | `SELECT * FROM purchase_orders WHERE po_key = ?` |
| `/month-end/accruals.csv` | 2,965 | `SELECT * FROM po_lines WHERE po_key = ? ORDER BY line_number` |
| `/month-end/accruals.csv` | 2,965 | `SELECT id, status, invoice_number, invoice_date, ai_output, final_output, export_batch FROM invoices WHERE po_key = ? AND status IN (?, ?, ?, ?) ORDER BY id` |
| `/month-end/accruals.csv` | 1,889 | `SELECT id, status, invoice_number, invoice_date, ai_output, final_output FROM invoices WHERE vendor_key = ? AND po_key = ? AND status IN (?, ?, ?, ?) ORDER BY id` |
| `/month-end/accruals.csv` | 51 | `SELECT final_gl gl_code, COUNT(*) lines, SUM(outcome = ?) corrected FROM feedback WHERE vendor_key = ? GROUP BY final_gl ORDER BY lines DESC` |
| `/learning?tab=suppliers` | 3,008 | `SELECT COUNT(DISTINCT invoice_id) FROM supplier_outcomes WHERE supplier_key = ?` |
| `/learning?tab=suppliers` | 3,008 | `SELECT invoice_id, field, correct, source, at FROM supplier_outcomes WHERE supplier_key = ? AND invoice_id IN (SELECT invoice_id FROM supplier_outcomes WHERE supplier_key = ? GROUP BY invoice_id ORDER` |
| `/learning?tab=suppliers` | 3,008 | `SELECT * FROM supplier_outcomes WHERE supplier_key = ? ORDER BY at DESC, id DESC LIMIT ?` |
| `/settings/automation` | 3,008 | `SELECT COUNT(DISTINCT invoice_id) FROM supplier_outcomes WHERE supplier_key = ?` |
| `/settings/automation` | 3,008 | `SELECT invoice_id, field, correct, source, at FROM supplier_outcomes WHERE supplier_key = ? AND invoice_id IN (SELECT invoice_id FROM supplier_outcomes WHERE supplier_key = ? GROUP BY invoice_id ORDER` |
| `/review/98499` | 391 | `PRAGMA data_version` |
| `/review/98499` | 194 | `SELECT id, status, export_batch, vendor_name, invoice_number, invoice_date, currency, grand_total, adjusted_confidence, requires_review, created_at, reviewed_at, json_extract(COALESCE(NULLIF(NULLIF(fi` |
| `/search?q=FTC6962398` | 112 | `-- SELECT pgno FROM ?.? WHERE segid=? AND term<=? ORDER BY term DESC LIMIT ?` |

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
