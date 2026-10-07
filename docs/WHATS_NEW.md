# What's new

Everything below was added after the first version (pull request #1). The fastest way to see it all is
the demo: start AP Coder and click **Load demo invoices** (no Azure needed). The
[Getting started guide](GETTING_STARTED.md#step-4-try-the-dashboard-with-the-sample-data-no-enterprise-data)
has a short tour.

Your existing data is safe. On first start the database upgrades itself (schema version 10), and a
backup of the database is made automatically once a day when AP Coder starts (the newest 14 are kept).

## Safety

Text from invoices and uploaded files can no longer run as a formula when a download is opened in
Excel, cannot turn into a link on screen, and an uploaded file cannot be saved outside the invoices
folder.

## Review faster

- **Every check explains itself.** Each finding on the review screen says what to do about it, and
  the new **Help** page lists every check, in plain English, with a search.
- **GL suggestions.** Lines the AI could not code get up to three one-click GL accounts, from how
  similar lines were coded before (this vendor, then others) and from your account descriptions.
- **Bulk approval.** *Approve N clean…* approves, in one go, the invoices with no error, no warning
  and enough confidence. Each one is re-checked at that moment; anything no longer clean stays for a
  person.
- **Ask the vendor.** When an invoice needs something from the vendor (a missing GST/HST number,
  amounts that don't add up, a price above the PO, proof of delivery), the review screen drafts the
  email in English or French, ready to copy or open in your email program. Internal fraud checks
  are never mentioned.
- **Split a line** across GL accounts or cost centers by percentage (shared costs).
- **Park and notes.** *More → Park* sets aside an invoice waiting for information, with a follow-up
  date (the **Parked** tab); **Notes** on each invoice keep the team informed.
- **Fixed coding rules.** GL accounts & tax → *Fixed rules*: a vendor, words a line contains, or
  both → a GL account (and cost center), whatever the AI says. The review screen shows which lines a
  rule changed; vendors always coded to one account are suggested as rules.
- **Teach from past coding.** Import last year's posted AP lines from the ERP (Learning page) so the AI
  knows how each vendor is coded from the first invoice. Past lines never count in the accuracy
  figures.
- **Due dates.** The AI now reads payment terms and printed due dates (English and French). Queue
  cards show *Overdue*, *Due in 3d* and early-payment discount deadlines (*2% off until Oct 15*), and
  the queue can be sorted by due date.
- **Stays fast** with a year of invoices: tested with 3,000. The queue shows 50 at a time.

## Catch more before it is paid

- **Purchase orders (2- and 3-way match).** Import open POs from the ERP (CSV or Excel, columns
  recognised automatically). An invoice quoting a PO is matched line by line: price above the PO,
  more billed than ordered or received (counting earlier invoices and credit notes), lines not on
  the PO, PO total exceeded, closed POs, POs for another vendor. *Use the PO's coding* applies the
  PO's GL accounts and cost centers in one click.
- **Vendor master from the ERP.** Import the vendor list (CSV or Excel): exports carry each vendor's
  ERP ID, an invoice from a vendor that is not in the list is flagged (by name or GST/HST number),
  the vendor's payment terms set the due date when the invoice shows none, and its default GL
  account is suggested for uncoded lines.
- **Vendor fraud and duplicate signals:** vendor on hold, changed GST/HST number (a classic fake
  invoice sign), a different bank account to pay into than on the vendor's earlier invoices (the most
  common payment fraud; only the last 4 digits are ever shown), amount far above the vendor's usual, same amount under a new invoice number, the
  same bill under another vendor name, a bill already in the ERP (import the ERP's invoice list on the
  Exports page), first invoice from a vendor. The **Vendors** page holds the
  controls (hold, expected GST/HST number, notes).
- **Second approval.** Above an approval limit (Settings → Review), an approved invoice waits for a
  second person (another name and another computer login) before it can be exported, or is sent
  back to the queue with the first approver's corrections kept. For two people to approve, they use
  the same AP Coder, e.g. on a shared AP PC, each signed in to Windows as themselves (the database
  must stay on a local disk, not a network share; a shared server version is a Phase 2 item). See
  [Two people approving](GETTING_STARTED.md#two-people-approving-second-approval) to set it up.

## The rest of the AP cycle

- **Exports:** approved invoices go to the ERP in batches, as an Excel workbook (invoices, GL lines,
  totals by GL), a CSV of GL lines, or a **custom CSV** laid out the way your ERP's import expects
  (columns, headers, fixed text, date format, separators). Each invoice goes out once; a batch can be
  downloaded again or undone if the import failed.
- **Vendor statements:** upload a vendor's statement of account and see what matches, what differs,
  what you never received and what is not on their statement, with the email asking for copies of
  the missing invoices ready to send.
- **Month-end:** the accruals schedule: goods received but not invoiced (from POs), invoices not in
  the ERP yet, and regular bills that have not arrived; totals by GL; CSV.
- **Sales tax:** the GST/HST input tax credits and QST input tax refunds to claim for a period
  (by invoice date, credit notes deducted), by tax and rate, and the claims to check before filing:
  an invoice of $30 or more without a valid registration number, tax in a foreign currency. CSV.
  Also the **PST / QST possibly to self-assess**: approved invoices for a supply in BC, SK, MB or
  Quebec where the vendor charged none, with an estimate at the official rate.
- **Recurring vendors:** the Vendors page lists who bills monthly, quarterly..., their next expected
  invoice, and which are late.
- **Folder watcher:** `python -m ap_coder watch` processes new files dropped in the invoices folder
  (from a scanner, a mail rule or Windows Task Scheduler with `--once`).
- **Invoices by email:** drop a saved email (`.eml`: Outlook on the web or new Outlook → *Download*)
  in the invoices folder, or upload it: its PDF and image attachments are processed (signature logos
  left out) and the email is kept in `invoices/emails`.

## Know what happened

- **Spend:** where the money goes, by month (coloured by GL category), GL account, vendor and cost
  center, net of recoverable tax. *All invoice data (Excel)* gives every invoice, line and GL posting as
  Excel tables for pivot tables or Power BI.

- **Activity:** an audit trail of everything that changes data, with every change a reviewer made
  to the AI's coding; filterable and downloadable.
- **Controls report** (on the Activity page) for internal audit: approvals that overrode an error,
  approvals made while a fraud or PO signal was showing, second approvals, setup changes, approvals
  per person; one HTML page to download.
- **Insights:** how much goes straight through, hours saved, Azure cost per invoice from the real
  token usage, and a monthly projection, with a one-page business case to share (totals only); AP
  operations: queue ageing, days to approve, approvals after the due date, discounts approved in
  time.

## Setup and safety

- **Settings page:** Azure connection with a live test, review behaviour, approval limit, default
  payment days, backups (make, download, restore). Your name is kept per Windows user.
- **Your own tax-rate table:** a `canada_tax_rates.csv` in the data folder overrides the shipped
  rates and survives updates.
- **Demo mode** with sample invoices, purchase orders and a vendor list (plus a sample vendor
  statement in `data/` to upload); removing it leaves your own data untouched.
- **Ten sample invoices** with their correct answers: every Canadian tax regime (HST, GST+PST,
  TPS/TVQ, GST only), a two-page French invoice, a US invoice and a credit note.
- **CI:** every change is tested on Windows and Linux, including the real installer on Windows.
