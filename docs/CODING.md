# GL coding without an AI model

When no chat model is loaded in LM Studio and Azure is off, AP Coder still codes every invoice line. It uses
what AP approved before, the fixed coding rules, the vendor master and the names of your GL accounts. This page
says how a line is coded, how well it works, and how to measure it again.

## How a line is coded

Each line gets suggestions from five sources (`ap_coder/suggest.py`):

- **this vendor's lines**: how AP coded similar lines from the same vendor;
- **other vendors' lines**: how AP coded similar lines from other vendors;
- **the vendor's usual account**: every line AP coded for this vendor went to one account;
- **the vendor master's default account** for this vendor;
- **account names**: words the line shares with an account's description.

A line gets an account only when the evidence is strong:

- a line of this vendor at least 40% alike, and no line of this vendor nearly as alike (or coded more recently)
  that went to another account;
- a line of another vendor at least 60% alike, and no other vendor's similar line coded to another account;
- the vendor's usual account, once AP has coded at least 4 different lines of it, all to that account.

The vendor master's default account and account names are never used on their own. On the benchmark the default
was right about two times in three (it is wrong for a vendor that sells several kinds of things), and an account
name four times in five. The line is left UNASSIGNED and the review screen shows the suggestions under *Suggested
GL accounts*: one click picks one. When two accounts both have strong evidence, this vendor's own lines win over
its usual account, which wins over other vendors' lines; at the same level, a person decides. Fixed coding rules
still apply after this, as before.

With the local chat model loaded, a weak suggestion is still used, as before (on the sample invoices an account
name match beat a small model), and the model is asked only about lines with no suggestion at all.

### Lines worded differently

Suppliers print the same thing in many ways: "Nitrile gloves, box of 100", "GLOVES, NITRILE", "Nit. glove LG",
"Ntrl gloves". Lines are compared word by word (`ap_coder/line_match.py`):

- case, accents, punctuation, numbers, units and packaging ("box of 100", "pouces") and month names do not count;
- singular and plural are one word;
- a shortened word matches the word it shortens: cut short ("Hydr." for "hydraulic") or without its vowels
  ("Brg" for "bearing");
- a word spelt nearly the same matches in part ("hydraulique" and "hydraulic");
- between two vendors, an item code that is on only one of the lines ("KC31973") is no difference: each vendor
  has its own codes. A code on both lines ("Cat6", "2RS") counts;
- a word AP coded to several accounts ("premium", "charge") counts for less than a word that always went to one
  account.

There is no dictionary from French to English: "gants" and "gloves" are not matched. A dictionary would have to
be written for this catalog of items, and would only measure itself on the benchmark.

### AP changes its mind

When AP codes a line to another account than before, the latest decision is the one that counts for that line.
A line of the same vendor that is as alike and was coded more recently replaces older ones. And when AP recently
moved similar lines away from an account (for any vendor), that account is not filled in by itself any more: it
may be out of date. It is still suggested.

## The benchmark

```
python -m ap_coder.bench coding --vendors 30 --invoices 30 --seed 1 [--out bench_coding]
```

It simulates one company, the same for a given seed (`ap_coder/bench/coding.py`):

- **Vendors.** About one in seven is a utility (hydro, gas, water, phone). The others each sell the items of one
  trade (office, safety, industrial, electrical, fleet...), so vendors of a trade overlap, plus a few other
  items. The items are the benchmark's line catalog (`content.GOODS`, `content.UTILITY_LINES`). Each item's
  true account, picked from `data/chart_of_accounts.csv`, is in `ITEM_GL`. The same words on a utility bill go
  to Utilities (6210) from a hydro and to Telecommunications (6040) from a phone company.
- **Wording.** Each vendor prints each item its own way: words shortened, the name turned round ("Gloves,
  nitrile"), the size dropped or a size or grade added, singular or plural, an item code, upper case. One line in
  seven is worded differently again. A quarter of the vendors print French, and one in five prints either
  language, or both on one line.
- **Vendor master.** About one supplier in five, and half the utilities, have a default GL account: the account
  most of their lines go to.
- **Time.** Each vendor starts at some point in the first half of the period and sends its invoices from then
  on. Halfway through, AP starts coding spare parts (bearings, hose assemblies) to materials instead of repairs.

Each invoice goes through the code the app runs: `offline_coder.code_from_capture` with `Store.feedback_rows()`,
the reference data in `data/` and the vendor master, then the fixed coding rules. The review screen's suggestions
are recorded for the lines left without an account. Then AP approves the true account of every line
(`Store.approve_invoice`), which is what the next invoice learns from.

The report (`report.md`, `report.json`, and every line in `lines.jsonl`) gives:

- **accuracy**: the right account filled in, out of all lines;
- **precision**: of the accounts filled in, the share that were right;
- **coverage**: the share of lines given an account rather than left UNASSIGNED;
- the same split by the first invoice from a vendor or later ones, by whether AP coded the item before (for this
  vendor in the same words, in other words, only for other vendors, never), by language, by vendor, and by where
  the account came from.

A 30 × 30 run takes about two minutes on a laptop.

## Before and after

*Before*: the best suggestion was filled in whatever its strength (commit 33b57d8). *After*: this page. Two
company shapes: 30 vendors × 30 invoices, and 100 vendors × 8 invoices (many vendors, few invoices each: more
first invoices and more items seen only at other vendors).

**Seeds the thresholds were set on** (1 and 2):

| run | lines | accuracy | precision | coverage | wrong |
|---|---:|---:|---:|---:|---:|
| 30 × 30, seed 1 | 2,309 | 93.2% → **96.3%** | 97.4% → **99.6%** | 95.6% → **96.6%** | 57 → 8 |
| 30 × 30, seed 2 | 2,234 | 93.2% → **96.2%** | 98.1% → **99.7%** | 95.0% → **96.5%** | 41 → 6 |
| 100 × 8, seed 1 | 1,987 | 86.6% → **91.8%** | 95.0% → **99.7%** | 91.1% → **92.2%** | 91 → 6 |
| 100 × 8, seed 2 | 2,049 | 88.6% → **93.5%** | 96.0% → **99.4%** | 92.3% → **94.1%** | 75 → 12 |

**Fresh seeds, never used while tuning** (31 to 34):

| run | lines | accuracy | precision | coverage | wrong |
|---|---:|---:|---:|---:|---:|
| 30 × 30, seed 31 | 2,191 | 94.7% → **96.4%** | 98.4% → **99.7%** | 96.2% → **96.6%** | 34 → 6 |
| 30 × 30, seed 32 | 2,273 | 92.1% → **95.8%** | 96.2% → **99.5%** | 95.7% → **96.3%** | 82 → 11 |
| 100 × 8, seed 33 | 1,952 | 88.4% → **92.4%** | 96.0% → **99.5%** | 92.1% → **92.9%** | 71 → 9 |
| 100 × 8, seed 34 | 2,022 | 86.5% → **92.0%** | 95.4% → **99.3%** | 90.7% → **92.6%** | 85 → 13 |

The four fresh runs together (8,438 lines), split:

| lines | count | accuracy | precision | coverage | wrong |
|---|---:|---:|---:|---:|---:|
| all | 8,438 | 90.6% → **94.3%** | 96.6% → **99.5%** | 93.8% → **94.7%** | 272 → 39 |
| first invoice from a vendor | 648 | 56.3% → **60.0%** | 86.3% → **97.2%** | 65.3% → **61.7%** | 58 → 11 |
| later invoices | 7,790 | 93.4% → **97.1%** | 97.1% → **99.6%** | 96.2% → **97.5%** | 214 → 28 |
| this vendor, same words | 5,905 | 98.5% → **99.2%** | 98.5% → **99.7%** | 100.0% → **99.5%** | 90 → 18 |
| this vendor, other words | 1,360 | 82.2% → **95.4%** | 93.7% → **99.5%** | 87.7% → **95.9%** | 75 → 6 |
| other vendors only | 1,049 | 64.0% → **76.0%** | 87.5% → **98.3%** | 73.1% → **77.3%** | 96 → 14 |
| never coded before | 124 | 30.6% → **0.0%** | 77.6% → **0.0%** | 39.5% → **0.8%** | 11 → 1 |
| English | 5,115 | 91.8% → **95.0%** | 96.8% → **99.6%** | 94.9% → **95.4%** | 156 → 20 |
| French | 3,050 | 88.9% → **93.5%** | 96.3% → **99.4%** | 92.3% → **94.1%** | 104 → 17 |
| both on one line | 273 | 85.3% → **87.5%** | 95.1% → **99.2%** | 89.7% → **88.3%** | 12 → 2 |
| spare parts after AP recoded them | 336 | 55.1% → **69.3%** | 58.2% → **90.0%** | 94.6% → **77.1%** | 133 → 26 |

What changed most:

- **Lines worded differently** from this vendor's earlier ones: 82% → 95% right. Before, "Energy charges" did not
  match "Energy charge", nor "Cons. service - August" "Cons. service - September".
- **Precision** went from about 96.6% to 99.5%: one wrong account in 30 filled in became one in 200.
  Most of the old errors came from the vendor master's default (wrong for a vendor that sells several kinds of
  things), from AP's old decisions after it changed its mind, and from account names ("Delivery charge" on a
  hydro bill is not Postage & Courier).
- **First invoices** from a vendor are filled in a little less often (the default account and account names are
  only suggested now), but right 97% of the time instead of 86%.
- **Items never coded before** are left for AP. Before, about 40% got an account from its name, three in four
  right. Now the review screen suggests it.

On the 10 sample invoices in `samples/` with no history, the account names alone used to fill in 25 of 41
lines, 21 of them right. Now none is filled in, and the right account is among the suggestions for 24 lines.

## Speed with a long history

Coding reads the whole learning memory for every invoice: up to 50,000 rows, with years of ERP history
imported. It used to compare every line with every past decision. Now the history is grouped and indexed by
word once (`suggest._memory`, `memory.aggregate`), and kept while the same rows come back. Words and
descriptions are normalised once (`memory._normalise`, `memory.tokens`, `line_match.keys` are cached). Seconds
per invoice through the pipeline, no AI model, the 10 sample PDFs, on the 4-core test machine while other jobs
ran:

| history rows | before | after | after, with `Store.feedback_rows` cached |
|---:|---:|---:|---:|
| 0 | 0.05 | 0.06 | 0.06 |
| 5,000 | 0.72 | 0.19 | 0.07 |
| 30,000 | 3.6 | 0.58 | 0.09 |

The last column keeps the rows read from the database while nothing was approved (one batch of invoices).
After an approval, the first invoice reads the rows and builds the index again: about 0.7 s with 30,000 rows.

## Limits

- The vendors, wording and accounts of the benchmark are invented. The thresholds were set on seeds 1 and 2 and
  checked on seeds 31 to 34. The real measure is the pilot: *Learning & accuracy* shows how often what AP Coder
  proposed was kept.
- Most of the remaining wrong accounts are the first line of an item after AP changed how it codes it, from a
  vendor whose own history still says the old account. Nothing in the history can know yet.
- A new item, or an item another vendor sold in the other language, has no suggestion from history. AP codes it
  once; the vendor's next invoice has it.
