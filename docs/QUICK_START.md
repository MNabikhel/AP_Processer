# AP Coder: Quick start

For the pilot tester on the laptop, and AP staff on the company server (to print: **Help** → **Quick start** →
**Print**). In bold: pages and buttons.

## Install (once, about 10 minutes, on the laptop)

- IT gives you the AP Coder ZIP (the offline bundle) and installs LM Studio with its models.
- Unzip it to a folder OneDrive does not sync, for example `C:\APCoder\app`.
- In that folder, double-click `APProcessor.bat`. The first time it sets itself up (a few minutes), puts an
  *AP Coder* shortcut on the desktop and opens the dashboard.

## Start (every day) on the laptop

- Start LM Studio first: open it and click *Developer* → *Start server*.
- Double-click *AP Coder* on the desktop. A black window opens, then the dashboard.
- Keep the black window open: closing it stops AP Coder.

## The first steps

The welcome page's checklist ticks steps 0 to 3 off, and its *Next step* says what comes next.

0. Start LM Studio: open LM Studio, click *Developer* → *Start server*, then in AP Coder press **Check again** (on
   the welcome page, or **Settings** → **Reading**). Without it every invoice waits for you.
1. Your name and province: **Settings** → **Review** → type your name, choose your province (where invoices are
   delivered to you) → **Save**. Approvals carry your name.
2. Your GL accounts and sales tax: **GL accounts & tax** → open **Import GL accounts from CSV or Excel** → choose
   your file → **Upload a CSV or Excel file** → check the preview → **Import GL accounts**. To try AP Coder first, click **Use the sample GL accounts** instead
   (no cost centers). On the **Sales tax accounts** tab, pick a GL account for GST, HST and QST (PST needs
   none) → **Save tax setup**. Optional: **Bank accounts**. Then the vendor
   master (*Vendors*), the JD Edwards settings and *Teach from past coding*.
3. Your first invoices: **Import invoices** → **Choose files** (PDFs, scans, photos or saved emails) → **Process N uploaded invoices**.
4. Review: **Review queue** → open an invoice. Fix what the checks point to (a line without a GL account, a value
   in amber).
   Set each line's tax code: the Tax check must add up.
   Right-click a field name or line for more; Undo reverts it.
5. Your first approval: **Approve & teach** (or Ctrl+Enter). AP Coder learns from each correction.
6. Your first export: **Exports** → **Export N invoices** → **Download batch N**, then import that file into the
   ERP. The first export is an **Excel workbook**; for a CSV file, click **CSV, one row per GL line** above the list
   first.

Just looking around? **Load demo invoices** on the welcome page adds ten sample invoices; **Remove demo invoices**
on **Import invoices** takes them away.

## On the company server

- Nothing is installed on your PC. Open the address IT gave you and log in with the user name and temporary
  password you were given.
- Choose your own password straight away (*Your password*, under the menu, changes it later).
- LM Studio and the setup (steps 0 to 2) are IT's or an Admin's: start at step 3.
- AP Coder won't load, or says LM Studio isn't running: tell IT.

## Where things are on the laptop

- Pages you use less (Month-end, Vendors...) are under **More** in the menu.
- The dashboard: http://127.0.0.1:8501, on this computer only.
- Your data (invoices, database, daily backups): the data folder, `C:\Users\<you>\APCoder`. Updates keep it.
- The folder AP Coder reads invoices from: **Import invoices** → **Open folder**.

## When you are stuck

- On the laptop, "LM Studio isn't running": open LM Studio, *Developer* → *Start server*, then **Settings** → **Reading** →
  **Check again**.
- **Approve & teach** is greyed out: the invoice still needs something, often a GL account on a line. The note next
  to the button says what; click it (or press G) to go to that line.
- On the laptop, the dashboard is closed or won't load: double-click *AP Coder* on the desktop again.
- A page says "Something went wrong": go back and try again. If it happens again, report it.

## Reporting a problem

Open **Help** → **Report a problem**. Write what you did, what you expected and what happened, then **Copy** the
setup report and the activity summary into your message. They hold no vendor names, amounts or account names.
Never send the invoices themselves.
