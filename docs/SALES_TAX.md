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

Every line item has its taxes ticked (GST, HST, PST, QST). That is how one invoice mixes taxable and exempt lines:

- **PST on some lines only**: in BC, Saskatchewan and Manitoba some goods and services are exempt from PST but not
  from GST. Untick PST on those lines. Since October 1, 2026, BC PST also applies to some services that were exempt
  (the BC 2026 budget: accounting and bookkeeping, security, non-residential real estate services, and architectural,
  engineering and geoscience services on 30% of the price), and to residential landline and toll-free telephone
  services. A line taxed on part of its price shows as a difference in the math: check it and approve anyway.
- **QST** is worked out on the price before GST (no tax on tax since 2013). AP Coder flags QST worked out on the
  GST-included price.
- **Freight and delivery**: taxed like what is delivered, in general; tick the taxes the vendor charged on its line.
- **US and other foreign vendors**: no Canadian tax unless they are registered. A foreign sales tax is "Other tax":
  not recoverable, it is added to the expense lines.

## The math, line by line

Under the tax lines, the review screen works every sum out again from the lines on screen:

- the lines add up to the subtotal;
- each tax is its rate times the lines ticked with it (a cent of rounding is allowed, or a cent per line when the
  vendor rounded line by line);
- subtotal + taxes = total.

The approve bar says **Math: All adds up**, or lists the differences. A credit note adds up the same way, in the
negative.

## Rates

The rates are in `data/canada_tax_rates.csv`, with the dates they apply from, checked against the CRA:
GST 5%; HST 13% in Ontario, 15% in New Brunswick, Newfoundland and Labrador and Prince Edward Island, and 14% in Nova
Scotia from April 1, 2025 (15% before); BC PST 7%, Saskatchewan PST 6%, Manitoba RST 7%; Quebec QST 9.975%. An
invoice is checked at the rates in force on its date.

## Registration numbers

GST/HST, QST and PST numbers printed on an invoice are compared with the vendor's (the vendor list, else its earlier
approved invoices). A different number is flagged: it is a classic sign of a fake invoice. The GST/HST number must
also pass the CRA check digit.
