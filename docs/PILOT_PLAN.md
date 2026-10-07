# Pilot plan: four weeks to a decision

A suggested way to run the AP Coder pilot so that, after a month, you can show your manager real
numbers from your own invoices. Everything stays on the AP computer; only your organisation's Azure
resources see the documents.

## Before week 1: set up (half a day)

- [ ] Install and connect Azure (GETTING_STARTED.md, steps 1 to 3). *Settings → Azure* has a
      connection test.
- [ ] Try the demo for 15 minutes (*Load demo invoices*), then *Remove demo invoices*.
- [ ] **GL accounts & tax:** import your chart of accounts (and cost centers if you code to them);
      check how each sales tax posts (GST/HST and QST recoverable, PST expensed).
- [ ] **Vendors:** import the vendor list from the ERP (vendor IDs, terms, default GL, blocked).
- [ ] **Purchase orders:** import open POs with quantities received, if you use POs.
- [ ] **Exports:** import the ERP's invoice list for the last 12 to 18 months (catches bills already
      entered), and set up the custom export layout if your ERP needs one.
- [ ] **Learning & accuracy → Teach from past coding:** import last year's posted AP lines, so the AI
      knows how each vendor is coded from the first invoice.
- [ ] **Settings:** your name, the approval limit (if two people approve), the second backup folder
      (OneDrive), and exchange rates if you are billed in USD or EUR.

## Week 1: shadow mode

Process the week's invoices in AP Coder **and** code them the usual way. Do not export yet.

- [ ] 20 to 50 invoices, a representative mix (top vendors, several provinces, a few French invoices,
      credit notes, PO invoices).
- [ ] Review each one; correct what is wrong and approve (the AI learns from every approval).
- [ ] Note anything AP Coder missed or flagged wrongly (Activity keeps the trail).
- [ ] Friday: **Learning & accuracy** (AI accuracy against the 90% target) and **Insights**.

## Week 2: rules and real exports

- [ ] **GL accounts & tax → Fixed rules:** add the suggested rules, and rules for what you corrected
      twice in week 1.
- [ ] Export approved invoices to the ERP from the **Exports** page; attach the **Approved PDFs** if
      your ERP keeps invoice images.
- [ ] Use **Ask the vendor** for missing GST/HST numbers, PO numbers and price differences.
- [ ] Answer vendor calls with **Find an invoice**.

## Week 3: the full cycle

- [ ] Reconcile the statements of your three largest vendors (**Vendor statements**).
- [ ] Turn on the **folder watcher** (`watch`, or Task Scheduler with `--once`) or save invoice emails
      (.eml) into the invoices folder.
- [ ] Look at **Vendors that make work** and ask the worst offenders for better invoices.

## Week 4: close and decide

- [ ] **Month-end:** the accruals schedule for the close.
- [ ] **Sales tax:** the ITCs and ITRs for the period, and the PST/QST to self-assess, checked
      against the GL.
- [ ] **Activity:** run the **duplicate payment audit** and download the **controls report**.
- [ ] **Insights:** edit the assumptions (minutes per invoice, hourly cost, monthly volume) and
      download the **one-page business case** (totals only, safe to share).

## What to bring to the decision

| Measure | Where | Good sign |
| --- | --- | --- |
| AI coding accuracy | Learning & accuracy | at or above 90%, rising week on week |
| Approved as coded | Insights | most invoices need no change |
| Minutes per invoice | Insights (assumptions) | well below today's manual time |
| Azure cost per invoice | Insights | cents, from the real token usage |
| Duplicates stopped, fraud signals | Insights, Activity | any caught is money kept |
| Discounts taken in time | Insights → AP operations | more than before |
| Days to approve | Insights → AP operations | shorter than before |

Phase 2 items to raise if the pilot goes well: a shared server version (several AP people at once,
database on a server), approval routing to budget owners, and a direct ERP connection.
