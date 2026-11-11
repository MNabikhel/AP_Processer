# AP Coder: the prototype on one Windows laptop

For one person trying AP Coder on their own Windows 11 laptop, with their department's own JD Edwards exports. It
takes an afternoon. Nothing leaves the laptop: the invoices, the exports and the models all stay on it.

The other guides describe more than a prototype needs (a server, a mailbox, purchase orders). Start here, and open
them only when this page points to one.

## What it does

You drop supplier invoices (PDFs, scans, phone pictures) into AP Coder. For each one it:

1. reads the supplier, the invoice number, the dates, the amounts and every line;
2. codes each line to a JD Edwards account, `business unit.object.subsidiary`, that exists in your chart and can be
   posted to, and suggests the 30-character remark;
3. works out the tax on every line and checks that the invoice adds up;
4. shows you the invoice beside its lines, with a reason for anything it was not sure of;
5. after you approve, puts the voucher on a keying sheet (Excel) to key into JD Edwards, or in a batch file.

It learns from last year's voucher journal before the first invoice, and from every approval after it.

## 1. Install (once, about 20 minutes)

1. **Python 3.11 or newer** (3.12 to 3.14 are tested) from python.org, with *Add python.exe to PATH* ticked.
   Nothing to do if the laptop has one.
2. **LM Studio** (0.4.8 or newer), with two models: a chat model (Gemma 4 12B) and the page reader (OvisOCR2).
   [LM_STUDIO.md](LM_STUDIO.md), "Using Gemma 4 12B", has the download links and the settings. On an 8 GB graphics
   card the two together spill a little into the computer's memory: slightly slower, nothing else.
3. **AP Coder:** unzip it to a folder OneDrive does not sync, for example `C:\APCoder\app`, and double-click
   `APProcessor.bat`. The first time it sets itself up, puts an *AP Coder* shortcut on the desktop and opens the
   dashboard in your browser. From GitHub's ZIP it downloads its packages and reading models once, and says so
   first; from the offline bundle it downloads nothing.

   The first double-click checks what is there: packages already good enough are kept, only missing ones are
   downloaded (about 230 MB). With another AP Coder installed it asks *Update it to ...? [Y/n]* first (No changes
   nothing); an older ZIP sets up nothing. A company network that blocks the downloads: it stops before setting
   anything up and says what to do (another network once, such as a phone's hotspot, or ask IT).

Every day after that: start LM Studio (*Developer* → *Start server*), then double-click *AP Coder*. Keep the black
window open: closing it stops AP Coder.

A new installation shows the short menu ("Simple screens"): **To review**, **Add invoices**, **To key into JDE**,
**Find an invoice**, **What was learned**, **Rules**, and under **Setup**: GL accounts & tax, JD Edwards files,
Vendors, Activity, Settings; then Help. **Settings → About → Screens** (*Everything*) shows every page again.

AP Coder's commands (`python -m ap_coder ...`, in sections 2, 4 and 6) are typed in **`scripts\terminal.bat`**:
double-click it in AP Coder's folder for a command prompt there with AP Coder's own Python. A plain command prompt's
`python` is not AP Coder's and does not have its packages.

A laptop that ran an earlier AP Coder keeps its data folder, and with it the long menu. The first time you open
**To review** after the update, a card says what is new and asks once: **Use the simple screens** or **Keep every
page**. Either way it does not come back.

## 2. Look around with a made-up company first (optional, 5 minutes)

To see the whole path before loading anything of your own, make a second data folder with a made-up company in it:

```
set AP_PRIVATE_DIR=C:\APCoder\try
python -m ap_coder demo --company
python -m ap_coder serve
```

Type these in `scripts\terminal.bat`. `demo --company` loads a made-up chart, suppliers, last year's journal and
seven invoices to review; `serve` opens them in the browser (on the next free port while your own AP Coder is open:
the window says "AP Coder is running at ..."). When you are done, press Ctrl+C in that window, close it (it still
points at the made-up company) and delete `C:\APCoder\try`. Your own data folder (`C:\Users\<you>\APCoder`) is never
touched.

## 3. Load your JD Edwards files (once, about 15 minutes)

**Setup → JD Edwards files** takes five exports, each exactly as JD Edwards gives it: no column to rename, no row to
delete. The top of the page (*What is loaded*) ticks off what is loaded.

| # | File | What it gives AP Coder | From JD Edwards |
|---|---|---|---|
| 1 | Chart of accounts (Excel) | every account string a line may be coded to, and which can be posted to | account master |
| 2 | Business units (Excel) | their names, and which are closed | business unit master |
| 3 | Suppliers (Excel): two exports, loaded one after the other in either order | the supplier master's grid: supplier numbers, names, payment holds; the vendor information list: each supplier's tax ID, currency, payment terms, address and e-mail | supplier master; vendor information list |
| 4 | Last year's coding (CSV) | what each supplier's lines were coded to, and their remarks | the Transaction Journal, saved as CSV |
| 5 | Vouchers already in JD Edwards (Excel) | the duplicate check, and the voucher and batch numbers | supplier ledger inquiry |

The chart (**Load the chart**) and the business units (**Load the business units**) are loaded at once and say what
they read. The other three (**Read the suppliers**, **Read the journal**, **Read the voucher list**) first show what
they found (how many rows, which columns were read as what) with nothing kept yet; the **Load** button under it keeps
them. A newer chart, business unit list or voucher list replaces the older one, a newer supplier list adds the new
suppliers and updates the others, and what you changed by hand is kept (below).

Box 3 takes both supplier exports and tells them apart by their column names. Each row goes to the supplier of its
number, so loading one never removes what only the other gives. The vendor information list lists the suppliers of
every company (about 20,000): it is read in a few seconds, its last row of totals is left out, and the summary says
how many tax IDs are business numbers. A tax ID that is a word (CHARITABLE, GOVERNMENT) or another kind of number
(a US EIN) is kept as written and compared with nothing.

**See what is loaded.** Once a file is loaded, *What is loaded* at the top links to its rows: a table with a search
box, filters, a count and *Download ... as CSV* (codes keep their leading zeros). In the chart, the business units and
the suppliers, **Change** beside a row lets you correct it: an account's description, whether it can be posted to
and its meals tick; a business unit's name and status; a supplier's name, hold, note, tax numbers and terms. What
you change is kept when you load a newer file (the page shows what the file says beside it), and every change is in
**Activity** with who made it. Last year's coding and the vouchers are a record of what happened: you can look and
download, not change; forget the file to take them out.

Under the chart, **The chart's settings** has three boxes worth a look:

- *Accounts never offered*: subsidiaries reserved for something else, so they are never suggested;
- *Words in a name that retire a business unit or a supplier* (for example DO NOT USE);
- *Home business unit*: where a line goes when nothing says otherwise.

Then **Save the settings** under them. Two things the files do not say are offered on the page, each with one click:

- a chart does not say which accounts are meals accounts, so the top of the page has a **Meals accounts** line that
  names the accounts that read like meals; **Mark these ... as meals accounts** ticks them. Until one is ticked, the
  GST/HST of a meal is claimed in full, not by half;
- once the business units are loaded, **Use company ...** under them makes the company they belong to JD Edwards'
  company number, which every voucher and the keying sheet carry.

**Setup → GL accounts & tax → Sales tax accounts** needs nothing for keying by hand: a new installation is already
set the usual way, GST and HST *Recoverable*, PST and QST *Not recoverable: add to each expense line's GL*. The
GST/HST that is recovered goes on the voucher's pay item (tax explanation code V) and JD Edwards posts it itself, so
it needs no GL account here; PST or QST stays in the line's expense. Then set **Settings → Review → Your province**
(where goods are delivered to you): the tax check uses it for an invoice that prints no ship-to or bill-to.

**Bank accounts (optional).** JD Edwards' voucher entry takes a G/L bank account. To have each voucher carry
one, type your bank accounts on **Setup → GL accounts & tax → Bank accounts**: the account as you key it in
JD Edwards, a description, its currency, and a tick on the default of each currency, then **Save bank accounts**.
*Remove* takes one off the list. Where people sign in, an Approver or an Admin does this; a Clerk sees the list and
picks from it. With no bank account entered, nothing changes and nothing asks for one.

## 4. See how it would have coded last year (5 minutes)

Before relying on it, score it against what was really keyed. Both commands are typed in `scripts\terminal.bat` (a
new window, not the made-up company's of section 2), run on this laptop only and change nothing in AP Coder; the
reports go to `pilot_score` in the data folder.

```
python -m ap_coder pilot-score --journal --hold-out 2
```

AP Coder forgets the journal's last 2 months, learns from the months before, codes the last 2 months' lines from
their supplier and remark alone, and compares with the account that was keyed. It says the answer, also the first
paragraph of `journal_score.md`, for example: "Of 1,149 lines keyed in November and December 2024 (785 vouchers), AP
Coder would have coded 227 by itself, all of them the way they were keyed; for another 734 the keyed account was
among its first three suggestions; 188 it would have left to a person without the keyed account among them."

The number to trust it by is *of those coded by itself, how many were right*: it should be close to all of them.

The second command scores real invoices: copy about 50 past PDFs into the invoices folder and process them (section
5, step 1; nobody needs to approve them), then give it JD Edwards' voucher list and journal of the same period:

```
python -m ap_coder pilot-score --proposed --vouchers HEADERS.xlsx --journal R09321.csv
```

Its answer starts "Of 50 invoices read by AP Coder here ...": how many it found in the voucher list and the journal,
and how what it proposed compares with what was keyed; `proposed_score.md` has each voucher. [PILOT.md](PILOT.md),
"How well would it have coded last year?", explains every figure of both.

## 5. A day with it

1. **Add invoices:** **Choose files**, then **Process uploaded invoices**; or copy them into the invoices folder
   (**Open folder** on the page opens it), where the page lists them, then **Process N files**. A page of text takes
   a few seconds; a scan or a phone picture takes longer (the page reader looks at each page).
2. **To review:** each row says in words the first thing to fix. Open one: the invoice is on the left, its lines
   on the right.
   - **Account:** one box a line holds the whole string. A line AP Coder is sure of arrives filled, with why
     ("this supplier's usual account: 14 of 15 lines last year"). A line it is not sure of is left empty with up to
     three accounts offered: pick one, or type a number or a word of the account's name. A string the chart does not
     have is refused, never replaced. The Account box's list has every account kept for the line: for a supplier
     billed to many business units, each unit its lines went to, the one the line or the invoice's ship-to or "Attn:"
     names first. A supplier not in last year's journal is offered the accounts other suppliers' lines like it went to.
   - **Remark:** suggested from the supplier's past remarks, 30 characters at most, yours to change.
   - **Tax:** every line says which tax it carries and how much, each tax its own amount ("read" when the invoice
     prints it on the line). A line taxed differently from the rest is marked. A sum that does not match the invoice
     is marked under the lines and in the findings; it does not stop the approval by itself, so look before
     approving.
   - **Supplier and tax ID** (with the vendor information list loaded): picking a supplier fills its GST/HST
     number under *More details* when the invoice shows none; a number that was read or typed is never replaced.
     A number that belongs to one supplier of the list picks that supplier when the box is empty, and offers it
     with one click (*Use ...*) when another supplier is chosen. A number that differs from the chosen supplier's
     is an error: *Approve anyway* with a reason approves it, and the keying sheet then says on that voucher
     "Tax ID differs from the supplier list: confirm before paying". Where people sign in, such a voucher waits
     for a second approval by an Approver or an Admin before it can be exported. The same nine digits with other
     digits after RT (123456789 RT0002 for RT0001) is another GST/HST account of the same business: a warning
     only, with nothing to fix, no reason to give and no second approval.
   - **Bank account** (once a bank account is entered): shown in the header, set to the one this supplier was
     last paid from, else the default of the invoice's currency; pick another from the list. An invoice in a
     currency no bank account is in gets a warning and is not held.
   - **Approve** (Ctrl+Enter). An invoice whose accounts are all guesses asks for a reason first.
3. **To key into JDE:** with *JD Edwards keying sheet* chosen (a new installation opens on it), **Export N
   invoices** puts the approved invoices on a **keying sheet** and **Download batch N** gives it (Excel): one block a
   voucher, the header fields JD Edwards asks for (supplier number, invoice number, dates, currency, gross amount, tax
   explanation code, tax rate/area, tax amount, taxable amount, and the G/L bank account once bank accounts are
   entered), then one row a line (account, amount, remark). Print it or keep it beside JD Edwards and key from it.
   An invoice that is not ready is listed with why and is not put on the sheet. The same page makes the JD Edwards
   batch files (*JD Edwards E1 (Z-tables)*), for when the JDE team can load one.

What AP Coder leaves to a person, always with a reason: a supplier it does not know, a line it cannot code with
confidence, an invoice whose numbers do not add up, two tax areas on one invoice, a possible duplicate, a document
that may not be an invoice, pages it could not read.

## 6. Backups, and where things are

- Your data (invoices, the database, daily backups) is in the data folder, `C:\Users\<you>\APCoder`. An update of
  AP Coder keeps it.
- A backup is made once a day, at 17:30 while AP Coder is open, or at its next start when it was closed then
  (**Settings → Data & backups** changes the time). That page also makes one (**Back up now**), lists them and
  restores one; a second folder (a network drive, a USB disk) can keep a copy. `python -m ap_coder backup now` makes
  one too.
- A restore clears the second backup folder: set it again on **Settings → Data & backups** after one.
- **Setup → Activity** lists who did what.
- `python -m ap_coder doctor` checks the installation and says what is missing in plain words.

## What a prototype does not do yet

- **Several people.** This page is one person on one laptop. The same laptop can serve a few colleagues with
  sign-in during working hours: `python -m ap_coder share` in `scripts\terminal.bat` checks what is missing, says each
  next step (the certificate, your own Admin account, the firewall rule), then serves until you close its window;
  the day's backup is made at the time set on *Settings* → *Data & backups* (17:30 unless you change it), so leave
  the window open until then or press *Back up now* first. Two steps are not yours alone: the firewall rule needs
  administrator rights, and the Windows licence is IT's to confirm ([SERVER.md](SERVER.md), "From one person's
  laptop to a few people on the same laptop"). Once `share` has made your Admin account, the *AP Coder* shortcut no
  longer opens this data without sign-in: start it with `share` (after an update, double-click `APProcessor.bat`
  once to set it up, then `share`).
- **Purchase orders, statements, month-end and the other pages** are hidden in Simple screens, not removed.
- **Posting into JD Edwards by itself.** Vouchers are keyed from the sheet, or loaded as a batch file by the JDE
  team.
- **Tax advice.** The tax rules follow the Tax setup; have your tax team look at the cases it flags.

Stuck? **Help** in the menu, [QUICK_START.md](QUICK_START.md), "When you are stuck", and
[KNOWN_LIMITS.md](KNOWN_LIMITS.md).
