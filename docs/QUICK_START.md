# AP Coder: Quick start

For the pilot tester, to keep next to the laptop (to print it: **Help** → **Quick start** → **Print**). Words in
bold are pages and buttons in AP Coder.

## Install (once, about 10 minutes)

- IT gives you the AP Coder ZIP (the offline bundle) and installs LM Studio with its models.
- Unzip the ZIP to a folder that OneDrive does not sync, for example `C:\APCoder\app`.
- Open that folder and double-click `APProcessor.bat`. The first time it sets itself up (a few minutes), puts an
  *AP Coder* shortcut on the desktop and opens the dashboard in your browser.

## Start (every day)

- Open LM Studio and click *Developer* → *Start server*. Without it AP Coder still works, but every invoice waits
  for you and there are no GL suggestions.
- Double-click *AP Coder* on the desktop. A black window opens, then the dashboard in your browser.
- Keep the black window open while you work. Close it to stop AP Coder.

## The first six steps

1. Your name: **Settings** → **Review** → type your name → **Save review settings**. Each approval is
   recorded under it.
2. Your GL accounts: **GL accounts & tax** → **Import GL accounts from CSV or Excel**. To try AP Coder first,
   click **Use the sample GL accounts** instead. Then on the **Sales tax** tab, pick a GL account for each tax.
3. Your first invoices: **Import invoices** → **Choose files** (PDFs, scans, photos or saved emails) →
   **Process N uploaded invoices**.
4. Review: **Review queue** → open an invoice. Compare it with the page and fix what the checks point to: a
   line without a GL account or cost center, an amount marked in amber.
5. Your first approval: **Approve & teach** (or Ctrl+Enter). AP Coder learns from each correction.
6. Your first export: **Exports** → **Export N invoices** → **Download batch N**, then import that file into
   the ERP.

Just looking around? On the welcome page click **Load demo invoices**: ten sample invoices to practise on.
**Remove demo invoices** on **Import invoices** takes them away again.

## Where things are

- The dashboard: http://127.0.0.1:8501, on this computer only.
- Your data (invoices, database, daily backups): the data folder, `C:\Users\<you>\APCoder`. Updates keep it.
- The folder AP Coder reads invoices from: **Import invoices** → **Open folder**.
- What every check means, and what to do about it: **Help**.

## When you are stuck

- Each warning on a page says what to do. **Help** explains every check.
- "LM Studio isn't running": open LM Studio, *Developer* → *Start server*, then **Settings** → **Reading** →
  **Check again**.
- **Approve & teach** is greyed out: the invoice still needs something, often a GL account or a cost center on a
  line. The note next to the button says what.
- The dashboard is closed or won't load: double-click *AP Coder* on the desktop again. It opens the AP Coder that
  is running, or starts it.
- A page says "Something went wrong": go back and try again. If it happens again, report it.

## Reporting a problem

Open **Help** → **Report a problem**. Write what you did, what you expected and what happened, then **Copy** the
setup report and the activity summary into your message. They hold no vendor names, amounts or account names.
Never send the invoices themselves.
