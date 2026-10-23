# GL coding without an AI model

30 vendors x 30 invoices (seed 1): 2309 lines, coded as they arrive from what AP approved before (no chat model, no Azure), then approved with the true account.

- **Accuracy** (right account proposed, of all lines): **96.1%**
- **Precision** (proposed accounts that were right): **99.6%** (8 wrong)
- **Coverage** (lines given an account, not left UNASSIGNED): **96.4%**
- Lines left for AP whose right account is among the review screen's suggestions: 54.2%
- The top suggestion, assigned or not: right on 97.6% of lines

## First invoice from a vendor, and later ones

| invoice | lines | accuracy | precision | coverage | wrong | left: right one suggested |
|---|---:|---:|---:|---:|---:|---:|
| first invoice | 71 | 54.9% | 95.1% | 57.8% | 2 | 30.0% |
| later invoices | 2238 | 97.4% | 99.7% | 97.6% | 6 | 67.9% |

## By the vendor's invoice number

| invoice # | lines | accuracy | precision | coverage | wrong | left: right one suggested |
|---|---:|---:|---:|---:|---:|---:|
| 1 | 71 | 54.9% | 95.1% | 57.8% | 2 | 30.0% |
| 2-5 | 309 | 88.7% | 99.3% | 89.3% | 2 | 57.6% |
| 6-10 | 403 | 99.0% | 100.0% | 99.0% | 0 | 50.0% |
| 11-20 | 749 | 98.4% | 99.6% | 98.8% | 3 | 88.9% |
| 21+ | 777 | 99.0% | 99.9% | 99.1% | 1 | 100.0% |

## Has AP coded this item before?

*Vendor, same words*: this vendor, worded the same (case and punctuation aside); *other words*: this vendor, worded differently; *other vendors only*: approved for other vendors, never this one; *never*: a new item.

| seen before | lines | accuracy | precision | coverage | wrong | left: right one suggested |
|---|---:|---:|---:|---:|---:|---:|
| vendor, same words | 1843 | 99.3% | 99.8% | 99.5% | 4 | 100.0% |
| vendor, other words | 335 | 95.5% | 99.4% | 96.1% | 2 | 69.2% |
| other vendors only | 100 | 68.0% | 97.1% | 70.0% | 2 | 36.7% |
| never | 31 | 0.0% | - | 0.0% | 0 | 51.6% |

## Language of the line

| language | lines | accuracy | precision | coverage | wrong | left: right one suggested |
|---|---:|---:|---:|---:|---:|---:|
| en | 1501 | 96.5% | 99.7% | 96.9% | 5 | 63.8% |
| fr | 724 | 95.3% | 99.7% | 95.6% | 2 | 37.5% |
| bi | 84 | 94.0% | 98.8% | 95.2% | 1 | 75.0% |

## Vendor

| vendor | lines | accuracy | precision | coverage | wrong | left: right one suggested |
|---|---:|---:|---:|---:|---:|---:|
| supplier | 1940 | 96.0% | 99.7% | 96.3% | 6 | 54.2% |
| utility | 369 | 96.5% | 99.4% | 97.0% | 2 | 54.5% |

| vendor master | lines | accuracy | precision | coverage | wrong | left: right one suggested |
|---|---:|---:|---:|---:|---:|---:|
| default GL | 737 | 95.7% | 100.0% | 95.7% | 0 | 78.1% |
| no default GL | 1572 | 96.2% | 99.5% | 96.8% | 8 | 39.2% |

## Where the proposed account came from

*vendor*: this vendor's similar lines; *others*: other vendors' similar lines; *usual*: the account all of this vendor's lines went to; *master*: the vendor master's default; *words*: words of the account's name.

| source | lines | precision | wrong |
|---|---:|---:|---:|
| others | 82 | 97.6% | 2 |
| vendor | 2144 | 99.7% | 6 |

AP recoded spare parts (bearings, hose assemblies) to materials halfway through: on the 71 such lines after the change, accuracy 74.7%, precision 89.8% (6 wrong).

The *reviewers coded similar lines ... to GL* warning (HISTORY_CONFLICT), on the 2226 lines that had an account: 1 false alarms on a right account (1 of them after AP recoded spare parts); it caught 0 of the 8 wrong accounts.

## Line tax codes

Each line's tax code (GST and PST in British Columbia: goods carry both, services GST only), as proposed when the invoice is read: *sure* when it is neither worked out from several possible sets of lines nor an exemption waiting for a person to confirm it.

| lines | precision | coverage | accuracy | wrong | learned from past approvals |
|---|---:|---:|---:|---:|---:|
| 2309 | 97.8% | 97.1% | 95.0% | 49 | 1221 |

| invoices | lines | precision | coverage | accuracy | wrong |
|---|---:|---:|---:|---:|---:|
| tax column printed | 704 | 97.2% | 100.0% | 97.2% | 20 |
| no tax column | 1605 | 98.1% | 95.9% | 94.1% | 29 |

Line lessons the AI reads: on 0 of 2309 lines, 0 of them for another account than the line's true one (noise -). Hints on the review screen (from the first correction): on 80 lines, 0 wrong.

## Most frequent wrong accounts

| true | proposed | source | lines |
|---|---:|---:|---:|
| 5000 | 6220 | vendor | 6 |
| 6210 | 6040 | others | 2 |


Time: 61.7 s (26.7 ms per line).
