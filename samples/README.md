# Sample invoices

Synthetic invoices addressed to Fabrikam Canada Ltd. (the buyer in every sample). None of the
vendors, people or registration numbers are real, so the files are safe to share and demo.

Each sample has three files with the same name:

- `<name>.pdf`: the invoice as a supplier would send it.
- `<name>.md`: the same invoice as Azure Document Intelligence markdown (HTML tables for line items
  and totals, page breaks and page footers as comments). Use it to run the pipeline without Azure
  Document Intelligence.
- `ground_truth/<name>.json`: the correct coded output (`InvoiceCoding`): GL code, cost center and
  taxes per line, tax lines and totals. Use it for evaluation (`ap-coder evaluate`) and regression
  tests (`tests/test_samples.py` checks every sample).

| Sample | Province | Taxes | What it shows |
| --- | --- | --- | --- |
| `northwind_ON_HST_NW-2026-0912` | ON | HST 13% | Two pages with carried-forward rows; capitalisation threshold (laptops 6010, server 1500); delivery charge printed below the table |
| `northwind_ON_HST_CN-2026-0047` | ON | HST 13% (negative) | Credit note `CN-2026-0047` reversing part of NW-2026-0912: negative quantities, amounts, tax and total |
| `laurentides_QC_TPS_TVQ_SIL-4471` | QC | GST 5% + QST 9.975% | Bilingual invoice, French number format (`2 759,40 $`), QST registration number |
| `montroyal_QC_TPS_TVQ_ACMR-2026-1187` | QC | GST 5% + QST 9.975% | French-only, two pages (`À reporter` / `Report`); marketing, client entertainment (CC500) and training coded to the attendees' department |
| `pacific_BC_GST_PST_PO-77120` | BC | GST 5% + PST 7% | Tax codes printed in the descriptions; PST added to the cost of each line |
| `chinook_AB_GST_CCO-26-10418` | AB | GST 5% | GST-only province (no "provincial tax missing" warning); inbound freight on raw materials to 5100 / CC600; four cost centers on one invoice |
| `redriver_MB_GST_RST_RRO-55821` | MB | GST 5% + RST 7% (PST MB) | Tax column per line: the design service is RST-exempt, so `taxes_applied` differs per line and RST is spread only over the taxed lines; furniture capitalised to 1510 |
| `harbourview_NS_HST_HPS-2026-0347` | NS | HST 14% | Nova Scotia's reduced rate effective 2025-04-01; legal fees go to CC800 whatever department asked |
| `prairie_SK_GST_PST_PNS-104882` | SK | GST 5% + PST 6% | Hardware below the capitalisation threshold; 12-month licence paid upfront to 1550 Prepaid |
| `cascade_US_SalesTax_INV-30981` | Outside Canada (WA, USA) | US sales tax 10.35% (`OTHER`) | USD invoice, no GST/HST number; raises the `TAX_NON_CANADIAN` warning and expenses the tax into the lines; prepaid support to 1550 |

With the sample GL and tax setup only (`data/`, no purchase orders or vendor list), every sample passes the controls with no errors. Only the US
invoice raises a warning (`TAX_NON_CANADIAN`).

## Regenerating the files

```
python scripts/make_sample_pdf.py            # all samples
python scripts/make_sample_pdf.py <name> ... # only these
```

The invoice content lives in `INVOICES` in `scripts/make_sample_pdf.py`. To add a sample, add an entry
there, generate it, then write `ground_truth/<name>.json` by hand. The new sample is picked up by
`tests/test_samples.py` automatically. Regenerating writes a new PDF file ID and creation date, so
the PDF bytes change even when the content does not.
