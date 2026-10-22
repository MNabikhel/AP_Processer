# Sales tax: what AP Coder checks, and why

AP Coder checks the Canadian sales tax on every invoice: which taxes apply, at what rate, on which lines, and that it
all adds up. This page explains the rules it follows, so you can tell when to trust a flag and when to override it.
It is a guide, not tax advice: when in doubt, ask your tax team.

## Place of supply: where the goods are delivered to you

The province whose tax applies is the **place of supply**:

- **Goods** are supplied where they are delivered to you (or made available to you): CRA GST/HST Memorandum 3-3-3,
  *Place of supply in a province: tangible personal property*. When the vendor ships them to you by carrier, the
  destination decides. **Where the vendor is, or where it ships from, does not.** A vendor in Ontario shipping from
  British Columbia to your site in British Columbia charges GST and BC PST, not Ontario HST. Shipped to your site in
  Alberta: GST only.
- **Services** are generally supplied where you are (your business address for the service): Memorandum 3-3-6.
- The BC, Saskatchewan and Manitoba PST acts follow the same idea: PST applies to goods delivered to you in the
  province.
- An exception: goods **picked up** at the vendor's counter are delivered there, so its province's tax applies.

How AP Coder decides, in this order:

1. The ship-to printed on the invoice; with no ship-to, the address printed under "Bill to", "Sold to", "Client" or
   "Facturé à" (yours, as the customer); or the province you set on the review screen. Never the vendor's own
   address (decision D-61).
2. With neither printed: the province of the cost center the lines are coded to (Settings → Review, "Sites in other
   provinces, by cost center", e.g. `CC400=BC`), when every line goes to one site.
3. Else your province (Settings → Review, "Your province").
4. Only when none is known: the vendor's province, shown on the review screen as a guess to confirm.

## Which lines carry which tax, tax by tax

Line amounts are before tax. For each tax of the place of supply, a line is in one of three states (decision D-64):

- **charged**: the vendor charged it on the line;
- **not due**: the line is exempt or zero-rated from it (a BC bookkeeping service is "GST only · PST exempt"), or
  the whole line carries no tax (Exempt, Zero-rated, out of scope);
- **due, not charged**: neither. The tax is owed and the vendor did not collect it: an out-of-province or unregistered
  vendor, or a line it missed. A provincial tax is then **self-assessed** by you (BC PSTA s.49, Bulletin PST 400; the
  Saskatchewan PST and Manitoba RST acts and the QST Act likewise).

Each line has one **tax code**, chosen in its dropdown on the review screen, that says these states in plain words:

| Place of supply | Codes offered |
| --- | --- |
| British Columbia, Saskatchewan | GST + PST · GST only · PST exempt · GST only · PST not charged (self-assess) · PST only · GST exempt · Exempt from both · Zero-rated · No tax (out of scope) |
| Manitoba | the same with RST |
| Quebec | the same with QST (QST mostly follows the GST's exemptions) |
| Ontario, New Brunswick, Newfoundland and Labrador, Nova Scotia, PEI | HST · Exempt · Zero-rated · No tax (out of scope) (and GST only, when the vendor charged GST) |
| Alberta, the territories | GST · Exempt · Zero-rated · No tax (out of scope) |

A foreign tax charged adds "Foreign tax"; a line's own code is always kept, even when it is unusual there.

- **Exempt**: an exempt supply (residential rent, most financial services, some health and education services): no
  tax, and no input tax credit. **Zero-rated**: taxable at 0% (basic groceries, prescription drugs, exports).
  **No tax (out of scope)**: not a supply at all (a deposit, a government fee, a tip).
- **How the codes are filled in**: from the codes printed beside the lines ("G", "GP", "G R", "E", "*"), read with
  the invoice's own legend when it prints one ("Tax codes: G = GST 5%, R = Manitoba RST 7%"), otherwise with the
  usual letters (G GST, H HST, P or R PST, Q QST, E exempt, Z zero-rated; "*" or "T" alone: the taxes charged). With
  no codes printed, AP Coder finds which lines make each tax charged add up: the tax ÷ its rate is what it was
  charged on, and the lines whose amounts make that (to a cent a line) carry it; the ways are counted exactly.
  - A discount or credit line always carries the tax with the others: a trade discount lowers the taxable price (ETA
    s.165; only an early-payment discount does not, s.161).
  - A line left out that does not read as untaxed (a deposit, a gift card, a tip, interest, a late payment charge,
    or printed exempt) is marked yellow to check, and so is one a printed taxable amount says is taxed. Freight,
    delivery and environmental fees are never assumed untaxed: charged by the vendor they follow the goods (CRA
    P-077R2; part of the taxable price for BC, SK and MB PST).
  - Several ways: the likeliest is taken and the lines in doubt marked to check. More than 20 ways (or several among
    more than 20 lines): the amounts can't say, so every line carries the tax and the Tax check shows the difference.
  - A tax the vendor charges on other lines and not on this one is "not due" on it (it collects the tax and chose
    not to); a tax charged on no line is "due, not charged". Invoices approved before the per-tax states are read the
    same way.
  - Your choice always wins: AP Coder never changes a code you picked. Picking a code, or "Confirm the tax code" in
    the line's menu, clears the yellow mark; approving confirms the codes.
- **Since October 1, 2026**, BC PST also applies to some services that were exempt (the BC 2026 budget: accounting
  and bookkeeping, security, non-residential real estate services, and architectural, engineering and geoscience
  services on 30% of the price), and to residential landline and toll-free telephone services. A line taxed on part
  of its price shows as a difference: check it and approve anyway.
- **QST** is worked out on the price before GST (no tax on tax since 2013). AP Coder flags QST worked out on the
  GST-included price.
- **US and other foreign vendors**: no Canadian tax unless they are registered. A foreign sales tax is "Foreign
  tax": not recoverable, it is added to the expense lines.
- **Prices that include the tax** (a receipt): the lines then add up to the total, not the subtotal. AP Coder says
  so; enter the lines before tax. It does not take the tax out of the prices itself.

What the checks say about the codes:

- *Taxable amount doesn't match the lines*: the lines marked with a tax do not make what it was charged on. A line is
  named only when its amount is the difference: "line 4 (Delivery, 285.00) may be exempt from RST". Otherwise the
  vendor's tax is said to look wrong: ask for a corrected invoice (tax charged in error is not claimable).
- *Line marked with a tax that isn't charged*: once per tax, the lines listed. If they are taxable, choose "GST only ·
  PST not charged (self-assess)" and self-assess the PST; if they are exempt, "GST only · PST exempt". Never
  "Exempt" for a taxable line: it would take the PST out of the self-assessment.
- *Provincial tax not charged*: the lines the PST or QST is due on and not charged (this vendor charges it elsewhere
  or not), with their total: self-assess it.
- *Exempt line seems taxed*: a line marked exempt or zero-rated (from that tax) whose amount the tax charged needs.
- *Input tax credit at risk*: HST charged where GST applies, or GST alone where HST applies. Only the tax properly
  payable is claimable (ETA s.169); the booking is not changed by AP Coder.
- Every check says the same of a credit note as of its invoice (a credit reverses it line for line, ETA s.232).
- The **Self-assessment** report works line by line: the lines a PST or QST is due on and not charged, at the official
  rate.
- Non-recoverable PST is added to the GL lines it is charged on, never to a line not due it.

## The Tax check, line by line

Under the line items, the review screen works every tax out again from the lines' codes:

| Tax | Lines marked | Lines × rate = expected | Charged | Difference |
| --- | --- | --- | --- | --- |
| GST | every line | 14,645.00 × 5% = 732.25 | 732.25 | — |
| RST | 3 of 4 lines | 13,445.00 × 7% = 941.15 | 941.15 | — |

The rate checked is the one printed (BC PST is 10% on liquor; the same tax printed at two rates is checked tax line
by tax line); whether it is the official rate in force on the invoice's date is the rate check's. A provincial tax due
and not charged shows as a "self-assess" row with what is owed at the official rate. Then: the lines add up to the
subtotal, and subtotal + all tax = total. A cent of rounding is allowed, or a cent per line when the vendor rounded
line by line. A row that does not add up is yellow ("check"), and so is the tax code of the line it points at. A tax
line's taxable amount follows the lines marked with it (unless you typed it); its amount is what the invoice charges
and never changes by itself. The approve bar says **Math: All adds up**, or lists the differences.

## Rates

The rates are in `data/canada_tax_rates.csv`, with the dates they apply from, checked against the CRA:
GST 5%; HST 13% in Ontario, 15% in New Brunswick, Newfoundland and Labrador and Prince Edward Island, and 14% in Nova
Scotia from April 1, 2025 (15% before); BC PST 7%, Saskatchewan PST 6%, Manitoba RST 7%; Quebec QST 9.975%. An
invoice is checked at the rates in force on its date.

## Registration numbers

GST/HST, QST and PST numbers printed on an invoice are compared with the vendor's (the vendor list, else its earlier
approved invoices). A different number is flagged: it is a classic sign of a fake invoice. The GST/HST number must
also pass the CRA check digit.
