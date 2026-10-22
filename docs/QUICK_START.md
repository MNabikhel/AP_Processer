# AP Coder: Quick start

For the pilot tester, to keep next to the laptop (to print it: **Help** → **Quick start** → **Print**). Words in
bold are pages and buttons in AP Coder.

## Install (once, about 10 minutes)

- IT gives you the AP Coder ZIP (the offline bundle) and installs LM Studio with its models (OvisOCR2 and the chat
  model).
- Unzip the ZIP to a folder that OneDrive does not sync, for example `C:\APCoder\app`.
- Open that folder and double-click `APProcessor.bat`. The first time it sets itself up (a few minutes), puts an
  *AP Coder* shortcut on the desktop and opens the dashboard in your browser.

## Start (every day)

- Start LM Studio first: open it and click *Developer* → *Start server*.
- Double-click *AP Coder* on the desktop. A black window opens, then the dashboard in your browser.
- Keep the black window open while you work. Close it to stop AP Coder.

## The first steps

The welcome page's checklist ticks steps 0 to 3 off as you do them, and its *Next step* says what comes next.

0. Start LM Studio: open LM Studio, click *Developer* → *Start server*, then in AP Coder press **Check again** (on
   the welcome page, or **Settings** → **Reading**). Without it every invoice waits for you, and GL accounts are
   suggested only from what AP approved before.
1. Your name and province: **Settings** → **Review** → type your name, choose your province (where invoices are
   delivered to you) → **Save**. Each approval is recorded under your name; the sales tax check uses the province
   when an invoice prints no ship-to.
2. Your GL accounts and sales tax: **GL accounts & tax** → open **Import GL accounts from CSV or Excel** → choose
   your file → **Upload a CSV or Excel file** → check the preview and the columns → **Import GL accounts**. To try AP Coder first, click **Use the sample GL accounts** instead
   (no cost centers). Then on the **Sales tax accounts** tab, pick a GL account for GST, HST and QST (PST needs
   none) → **Save tax setup**.
3. Your first invoices: **Import invoices** → **Choose files** (PDFs, scans, photos, saved emails or invoices saved
   as text) → **Process N uploaded invoices**.
4. Review: **Review queue** → open an invoice. Compare it with the page and fix what the checks point to: a line
   without a GL account, an amount marked in amber. Beside each value: Verified, Likely, Check or Failed check.
   Right-click a field or a line for more; Undo reverts it.
5. Your first approval: **Approve & teach** (or Ctrl+Enter). AP Coder learns from each correction.
6. Your first export: **Exports** → **Export N invoices** → **Download batch N**, then import that file into the
   ERP. The first export is an **Excel workbook**; for a CSV file, click **CSV, one row per GL line** above the list
   first. Exports opens on the format you used last.

Just looking around? On the welcome page click **Load demo invoices**: ten sample invoices to practise on.
**Remove demo invoices** on **Import invoices** takes them away again.

## Where things are

- Pages you use less (Month-end, Vendors...) are under **More** in the menu.
- The dashboard: http://127.0.0.1:8501, on this computer only.
- Your data (invoices, database, daily backups): the data folder, `C:\Users\<you>\APCoder`. Updates keep it.
- The folder AP Coder reads invoices from: **Import invoices** → **Open folder**.
- What every check means, and what to do about it: **Help**.

## When you are stuck

- Each warning on a page says what to do. **Help** explains every check.
- "LM Studio isn't running": open LM Studio, *Developer* → *Start server*, then **Settings** → **Reading** →
  **Check again**.
- **Approve & teach** is greyed out: the invoice still needs something, often a GL account on a line. The note next
  to the button says what; click it (or press G) to go to that line.
- The dashboard is closed or won't load: double-click *AP Coder* on the desktop again. It opens the AP Coder that
  is running, or starts it.
- A page says "Something went wrong": go back and try again. If it happens again, report it.

## Reporting a problem

Open **Help** → **Report a problem**. Write what you did, what you expected and what happened, then **Copy** the
setup report and the activity summary into your message. They hold no vendor names, amounts or account names.
Never send the invoices themselves.
