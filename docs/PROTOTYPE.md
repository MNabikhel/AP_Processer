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

Every day after that: start LM Studio (*Developer* → *Start server*), then double-click *AP Coder*. Keep the black
window open: closing it stops AP Coder.

A new installation shows the short menu ("Simple screens"): **To review**, **Add invoices**, **To key into JDE**,
**Find an invoice**, **What was learned**, and under **Setup**: GL accounts & tax, JD Edwards files, Vendors,
Activity, Settings. **Settings → About → Screens** shows every page again.

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

Run these in the black window's folder, with AP Coder's Python (as `python -m ap_coder doctor`). It loads a made-up
chart, suppliers, last year's journal and seven invoices to review. Delete `C:\APCoder\try` when you are done; your
own data folder (`C:\Users\<you>\APCoder`) is never touched.

## 3. Load your JD Edwards files (once, about 15 minutes)

**Setup → JD Edwards files** takes five exports, each exactly as JD Edwards gives it: no column to rename, no row to
delete. The top of the page ticks off what is loaded.

| # | File | What it gives AP Coder | From JD Edwards |
|---|---|---|---|
| 1 | Chart of accounts (Excel) | every account string a line may be coded to, and which can be posted to | account master |
| 2 | Business units (Excel) | their names, and which are closed | business unit master |
| 3 | Suppliers (Excel) | supplier numbers, names, payment holds | address book and supplier master |
| 4 | Last year's coding (CSV) | what each supplier's lines were coded to, and their remarks | the Transaction Journal, saved as CSV |
| 5 | Vouchers already in JD Edwards (Excel) | the duplicate check, and the voucher and batch numbers | supplier ledger inquiry |

Each upload shows what it found (how many rows, which columns it read as what) before anything is kept. Loading a
newer file later replaces the older one; what you typed in AP Coder is set aside, not lost.

**See what is loaded.** Once a file is loaded, *What is loaded* at the top links to its rows: a table with a search
box, filters, a count and *Download as CSV* (codes keep their leading zeros). In the chart, the business units and
the suppliers, **Change** beside a row lets you correct it: an account's description, whether it can be posted to
and its meals tick; a business unit's name and status; a supplier's name, hold, note, tax numbers and terms. What
you change is kept when you load a newer file (the page shows what the file says beside it), and every change is in
**Activity** with who made it. Last year's coding and the vouchers are a record of what happened: you can look and
download, not change; forget the file to take them out.

Under the chart, **The chart's settings** has three boxes worth a look:

- *Accounts never offered*: subsidiaries reserved for something else, so they are never suggested;
- *Words in a name that retire a business unit or a supplier* (for example DO NOT USE);
- *Home business unit*: where a line goes when nothing says otherwise.

Then **Setup → GL accounts & tax → Sales tax accounts**: check the province and the tax accounts. A new
installation is already set the usual way for keying by hand: the GST/HST that is recovered goes on the voucher's
pay item (tax explanation code V), and PST or QST that is not recovered stays in the line's expense. A chart does
not say which accounts are meals accounts, so the top of the JD Edwards files page has a **Meals accounts** line that
names the accounts that read like meals and ticks them with one click: until one is ticked, the GST/HST of a meal is
claimed in full, not by half.

## 4. See how it would have coded last year (5 minutes)

Before relying on it, score it against what was really keyed. Both commands run on this laptop only and change
nothing in AP Coder; the reports go to `pilot_score` in the data folder.

```
python -m ap_coder pilot-score --journal --hold-out 2
```

AP Coder forgets the journal's last 2 months, learns from the months before, codes the last 2 months' lines from
their supplier and remark alone, and compares with the account that was keyed. The first paragraph of
`journal_score.md` is the answer, for example: "Of 1,149 lines, AP Coder would have coded 227 by itself, all of
them the way they were keyed; for another 734 the keyed account was among its first three suggestions; 188 it would
have left to a person."

The number to trust it by is *of those coded by itself, how many were right*: it should be close to all of them.
[PILOT.md](PILOT.md), "How well would it have coded last year?", explains every figure, and the second command,
which scores real invoices (about 50 past PDFs) against the vouchers keyed for them.

## 5. A day with it

1. **Add invoices:** choose the files, or drop them into the invoices folder the page names, then **Process**.
   A page of text takes a few seconds; a scan or a phone picture takes longer (the page reader looks at each page).
2. **To review:** each row says in words the first thing to fix. Open one: the invoice is on the left, its lines
   on the right.
   - **Account:** one box a line holds the whole string. A line AP Coder is sure of arrives filled, with why
     ("14 of this supplier's 15 lines"). A line it is not sure of is left empty with up to three
     accounts offered: pick one, or type a number or a word of the account's name. A string the chart does not have
     is refused, never replaced.
   - **Remark:** suggested from the supplier's past remarks, 30 characters at most, yours to change.
   - **Tax:** every line says which tax it carries and how much, each tax its own amount ("read" when the invoice
     prints it on the line). A line taxed differently from the rest is marked. A sum that does not match the invoice
     is marked under the lines and in the findings; it does not stop the approval by itself, so look before
     approving.
   - **Approve** (Ctrl+Enter). An invoice whose accounts are all guesses asks for a reason first.
3. **To key into JDE:** **Export** puts the approved invoices on a **keying sheet**: one block a voucher, the
   header fields JD Edwards asks for (supplier number, invoice number, dates, currency, gross amount, tax
   explanation code, tax rate/area, tax amount, taxable amount), then one row a line (account, amount, remark). Print
   it or keep it
   beside JD Edwards and key from it. An invoice that is not ready is listed with why and is not put on the sheet.
   The same page makes the JD Edwards batch files, for when the JDE team can load one.

What AP Coder leaves to a person, always with a reason: a supplier it does not know, a line it cannot code with
confidence, an invoice whose numbers do not add up, two tax areas on one invoice, a possible duplicate, a document
that may not be an invoice, pages it could not read.

## 6. Backups, and where things are

- Your data (invoices, the database, daily backups) is in the data folder, `C:\Users\<you>\APCoder`. An update of
  AP Coder keeps it.
- A backup is made each day AP Coder starts. **Settings → Data & backups** makes one (**Back up now**), lists them
  and restores one; a second folder (a network drive, a USB disk) can keep a copy. `python -m ap_coder backup now`
  does the same.
- **Setup → Activity** lists who did what.
- `python -m ap_coder doctor` checks the installation and says what is missing in plain words.

## What a prototype does not do yet

- **Several people.** This page is one person on one laptop. The same laptop can serve a few colleagues with
  sign-in during working hours: `python -m ap_coder share` in the AP Coder folder checks what is missing, says each
  next step (the certificate, your own Admin account, the firewall rule), then serves until you close its window;
  the day's backup is made at the time set on *Settings* → *Data & backups* (17:30 unless you change it), so leave
  the window open until then or press *Back up now* first. Two steps are not yours alone: the firewall rule needs
  administrator rights, and the Windows licence is IT's to confirm ([SERVER.md](SERVER.md), "From one person's
  laptop to a few people on the same laptop").
- **Purchase orders, statements, month-end and the other pages** are hidden in Simple screens, not removed.
- **Posting into JD Edwards by itself.** Vouchers are keyed from the sheet, or loaded as a batch file by the JDE
  team.
- **Tax advice.** The tax rules follow the Tax setup; have your tax team look at the cases it flags.

Stuck? **Help** in the menu, [QUICK_START.md](QUICK_START.md), "When you are stuck", and
[KNOWN_LIMITS.md](KNOWN_LIMITS.md).
