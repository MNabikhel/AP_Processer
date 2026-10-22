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

## Which lines carry which tax

Line amounts are before tax. Each line has one **tax code**, chosen in its dropdown on the review screen: which taxes
are charged on it, or why none is (decision D-64). The choices follow the place of supply:

| Place of supply | Codes offered |
| --- | --- |
| Ontario, New Brunswick, Newfoundland and Labrador, Nova Scotia, PEI | HST (and GST only, when the vendor charged GST) |
| British Columbia, Saskatchewan | GST + PST, GST only, PST only |
| Manitoba | GST + RST, GST only, RST only |
| Quebec | GST + QST, GST only, QST only |
| Alberta, the territories | GST |
| Always | Exempt, Zero-rated, No tax (out of scope), No tax charged |

A foreign tax charged adds "Foreign tax"; a line's own code is always kept, even when it is unusual there.

- **Exempt**: an exempt supply (residential rent, most financial services, some health and education services): no
  tax, and no input tax credit. **Zero-rated**: taxable at 0% (basic groceries, prescription drugs, exports).
  **No tax (out of scope)**: not a supply at all (a deposit, a government fee, a tip). **No tax charged**: nothing
  says why; a taxable line the vendor did not tax (PST or QST may then have to be self-assessed).
- **How the codes are filled in**: from the codes printed beside the lines ("G", "GP", "G R", "E", "*"), read with
  the invoice's own legend when it prints one ("Tax codes: G = GST 5%, R = Manitoba RST 7%"), otherwise with the
  usual letters (G GST, H HST, P or R PST, Q QST, E exempt, Z zero-rated; "*" or "T" alone: the taxes charged). With
  no codes printed, AP Coder finds which lines make each tax charged add up: the tax ÷ the official rate is what it
  was charged on, and the lines whose amounts make that (to a cent a line) carry it. When several sets of lines
  would do, the likeliest is taken (leaving out freight, delivery or a deposit first) and those lines are marked
  yellow to check. Your choice always wins: AP Coder never changes a code you picked.
- **PST on some lines only**: in BC, Saskatchewan and Manitoba some goods and services are exempt from PST but not
  from GST: mark them "GST only". Since October 1, 2026, BC PST also applies to some services that were exempt
  (the BC 2026 budget: accounting and bookkeeping, security, non-residential real estate services, and
  architectural, engineering and geoscience services on 30% of the price), and to residential landline and toll-free
  telephone services. A line taxed on part of its price shows as a difference: check it and approve anyway.
- **QST** is worked out on the price before GST (no tax on tax since 2013). AP Coder flags QST worked out on the
  GST-included price.
- **Freight and delivery**: taxed like what is delivered, in general; give its line the code the vendor charged.
- **US and other foreign vendors**: no Canadian tax unless they are registered. A foreign sales tax is "Foreign
  tax": not recoverable, it is added to the expense lines.
- **Prices that include the tax** (a receipt): the lines then add up to the total, not the subtotal. AP Coder says
  so; enter the lines before tax. It does not take the tax out of the prices itself.

What the checks say about the codes:

- *Taxable amount doesn't match the lines*: the lines marked with a tax do not make what it was charged on. When one
  line's amount is the difference it is named: "line 4 (Delivery, 285.00) may be exempt from RST".
- *Line marked with a tax that isn't charged*: e.g. lines marked PST on an invoice that charges none: mark them
  exempt or "No tax charged", or self-assess the PST. The province's own "PST not charged" is then not said again.
- *Exempt line seems taxed*: a line marked exempt or zero-rated whose amount the tax charged needs.
- The **Self-assessment** report estimates PST or QST on the lines marked with it (taxable, not charged); with no line
  marked, on the subtotal less the lines marked exempt, zero-rated or out of scope.
- Non-recoverable PST is added to the GL lines that carry it, never to a line marked exempt.

## The Tax check, line by line

Under the line items, the review screen works every tax out again from the lines' codes:

| Tax | Lines marked | Lines × rate = expected | Charged | Difference |
| --- | --- | --- | --- | --- |
| GST | every line | 14,645.00 × 5% = 732.25 | 732.25 | — |
| RST | 3 of 4 lines | 13,445.00 × 7% = 941.15 | 941.15 | — |

The rate is the official one in force on the invoice's date where it is supplied. Then: the lines add up to the
subtotal, and subtotal + all tax = total. A cent of rounding is allowed, or a cent per line when the vendor rounded
line by line. A row that does not add up is yellow ("check"), and so is the tax code of the line it points at. A
tax line's taxable amount follows the lines marked with it (unless you typed it); its amount is what the invoice
charges and never changes by itself. The approve bar says **Math: All adds up**, or lists the differences. A credit
note adds up the same way, in the negative.

## Rates

The rates are in `data/canada_tax_rates.csv`, with the dates they apply from, checked against the CRA:
GST 5%; HST 13% in Ontario, 15% in New Brunswick, Newfoundland and Labrador and Prince Edward Island, and 14% in Nova
Scotia from April 1, 2025 (15% before); BC PST 7%, Saskatchewan PST 6%, Manitoba RST 7%; Quebec QST 9.975%. An
invoice is checked at the rates in force on its date.

## Registration numbers

GST/HST, QST and PST numbers printed on an invoice are compared with the vendor's (the vendor list, else its earlier
approved invoices). A different number is flagged: it is a classic sign of a fake invoice. The GST/HST number must
also pass the CRA check digit.
