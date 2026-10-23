# JD Edwards EnterpriseOne (E1) export

AP Coder can hand approved invoices to JD Edwards E1 (9.1 / 9.2) as **batch vouchers**: the files that
E1's Voucher Batch Processor (**R04110ZA**, or the older R04110Z) reads from its two interface ("Z") tables:

| File | Z-table | One row per |
| --- | --- | --- |
| `F0411Z1.csv` | F0411Z1 (Voucher Transactions - Batch File) | voucher pay item |
| `F0911Z1.csv` | F0911Z1 (Journal Entry Transactions - Batch File) | G/L distribution line |
| `F0411Z1T.csv` | *(not a standard table: see "PO-matched vouchers")* | PO match line |

The code is in `ap_coder/erp/jde.py` (pure logic), the settings in `ap_coder/web/pages/settings_jde.py`
(Settings → **JD Edwards E1**) and the export on the Exports page (ERP format **JD Edwards E1**).

## Using it

The pilot exports to Excel and CSV, so the JD Edwards E1 tab and export format are hidden until **Settings → About
→ Show JD Edwards settings** is ticked (D-52). A database already set up for JD Edwards (its settings saved, or a JD
Edwards batch made) has it turned on by itself when it is updated: nothing is taken away.

1. **Settings → JD Edwards E1** (once *Show JD Edwards settings* is on): company, user ID, batch prefix, document type, the amount and account
   formats, the GL → BU.Object.Subsidiary mapping (a table, or import a CSV / Excel file), the province →
   tax area table and, if used, PO-matched vouchers. Go through the *Confirm with your JDE team* checklist.

   Quicker: **Download the worksheet** there and send it to the JDE team (`jde_worksheet.py`). It is one Excel
   file with every question below, what AP Coder uses now and a yellow column for each answer, plus sheets for
   the tax areas, the account mapping (every GL account already listed) and each vendor's address number.
   **Import the JDE team's answers** back on the same page: AP Coder applies them, lists what changed, and lists
   any answer it could not use (a value of the wrong form is reported, never guessed). Answers to questions that
   are not settings (the P0400047 version, the duplicate edit, how the AAIs post tax ...) are kept with the
   settings and shown on the page.
2. **Supplier numbers**: each vendor's JDE address number (AN8) comes from the vendor master's ERP ID
   (Vendors page → import the vendor master from E1). A vendor whose ERP ID is not its address number can
   be given one in Settings → JD Edwards E1 → *Supplier address numbers*.
3. **Exports**: pick the invoices, choose *JD Edwards E1* (offered once *Show JD Edwards settings* is on), and
   export. Invoices that fail the checks
   (below) are listed with the reasons and are **left out of the batch**; they stay in *Ready to export*.
   Before exporting, **Dry run** (on Exports, with *JD Edwards E1* chosen) downloads exactly what a batch of
   the ready invoices would load, without making one (`jde.dry_run`): for each voucher its supplier (AN8), tax
   area and code, each F0411Z1 pay item (document type, gross and tax, the Julian dates), each F0911Z1 line
   (account, amount, text) and the proof that it balances (distribution = gross - the tax E1 posts); for each
   invoice held back, every reason. Send it to the JDE team with the first test batch.
4. Download the ZIP: `F0411Z1.csv`, `F0911Z1.csv` (and `F0411Z1T.csv` for PO-matched vouchers), a
   `README.txt` with the batch number, counts, totals and the loading steps, and `DRY_RUN.txt` (the dry run of
   that batch: what each row is, and what was left out and why):
   1. Load each CSV into its Z-table (database import tool, or a table conversion). The header row holds
      the table's column names.
   2. Run **R04110ZA in proof mode** for the batch (VLEDBT) and user (VLEDUS); read the report and the
      errors in the batch voucher revisions (P0411Z1 / Work Center).
   3. Run **R04110ZA in final mode**; post the voucher batch as usual.
5. If the load fails: Exports → Past batches → **Undo this batch**, fix the cause, export again. Each invoice
   keeps its transaction number (VLEDTN = `APC` + AP Coder invoice number) and gets a new batch number, so the
   Z-table keys never clash. A batch can be downloaded again at any time; it is rebuilt from the approved data
   with the current JD Edwards settings.

## How the mapping works

### Formats

* **Dates** are E1 "Julian" dates **CYYDDD**: C = century (0 = 1900s, 1 = 2000s), YY = year, DDD = day of
  the year. 2026-10-09 → `126282`; 2024-12-31 → `124366`; 1999-12-31 → `099365` (written `99365`).
* **Amounts** use **implied decimals** by default ($5.50 → `550`; -$2,316.50 → `-231650`), rounded half away
  from zero. Setting *Amount format* switches to a decimal point (`5.50`). Currencies without decimals (JPY,
  KRW, …) are written in whole units. *Your JDE team must confirm which their load expects.*
* **Text** is cut to the column size (VLVINV 25, VLRMK / VNEXA / VNEXR 30), on one line, and written as it is:
  the Z-files are for a database loader, not Excel, so there is no spreadsheet apostrophe (it would be loaded
  into the table and the invoice number would no longer match). Do not open them in Excel to edit them. Files
  are UTF-8 without a byte-order mark.

### Gross amount or tax amounts (never both)

E1 rejects a pay item that has both a gross amount and taxable / tax amounts that disagree, so the
setting *Amounts* chooses one:

* **Gross only** (default): VLAG (or VLACR in a foreign currency). E1 calculates the tax from the tax area
  and explanation code.
* **Taxable / non-taxable / tax**: VLATXA (taxable), VLATXN (non-taxable = gross - taxable - tax) and
  VLSTAM (all Canadian tax charged); VLAG is blank (VLCTXA / VLCTXN / VLCTAM in a foreign currency).
  E1 keeps the tax printed on the invoice.

### Tax areas and explanation codes

The place of supply (the ship-to; with none printed, the province of the cost center the lines are coded to or your province from Settings → Review; only then the supplier province) picks the tax area (VLTXA1) and explanation
code (VLEXR1). The defaults are editable; **the tax area names are placeholders** for the customer's own
F4008 tax areas:

| Province | Tax area | Code | Distribution (F0911Z1) |
| --- | --- | --- | --- |
| ON, NB, NL, NS, PE (HST) | `ON-HST`, `NS-HST`, … | V | gross - HST |
| QC (GST + QST) | `QC-GSTQST` | V | gross - GST - QST |
| BC, SK, MB (GST + seller-charged PST) | `BC-GSTPST`, … | C | gross - GST (the PST stays in the expense lines) |
| AB, NT, NU, YT (GST) | `AB-GST`, … | V | gross - GST |
| BC, SK, MB with no PST charged | same area | B (self-assessed PST) | gross - GST; E1 accrues the PST |
| No tax charged | same area | E (exempt) | gross |
| Outside Canada | blank | blank | gross (a US sales tax is part of the expense lines) |

A line marked **Exempt** or **Zero-rated** (its tax code, D-64) says so line by line in *match header* mode: its pay
item carries the exempt code (Settings → JD Edwards, default E) in VLEXR1, with no tax on it, and so does its G/L
line's VNEXR1 when the distribution's tax columns are on. The other lines keep the invoice's code. With one pay item
(*sequential*), the invoice's code is used for all of it, as before.

**Self-assessed PST** (a PST due and not charged by the vendor) is not in the voucher: book it from AP Coder's
**Self-assessment** report (Month-end) by journal entry. An invoice whose explanation code already has E1 accrue the
use tax (B, the self-assessed code) must not be booked again from that report, or the PST is accrued twice: the dry
run says so on such a voucher, and once exported the Self-assessment report marks it "JD Edwards accrues this one
(code B): don't book it again from this report".

### Why the distribution balances (and when it does not)

The explanation code decides which part of the tax E1 posts by itself (through the tax AAIs) and which
part must already be in the G/L distribution:

* **V** (VAT): all the tax is recoverable and posted by E1 → distribution = gross - tax.
* **C** (VAT + sales tax): GST/HST posted by E1, PST expensed → distribution = gross - GST/HST, PST inside.
* **B** (VAT + use tax): GST/HST posted by E1, the PST accrued by E1 → distribution = gross - GST/HST.
* **E** / blank: no tax handling → distribution = gross.

AP Coder's own GL distribution has the same structure: recoverable taxes (Tax setup → *Recoverable*) are
separate lines, non-recoverable taxes are added to the expense lines or to their own expense line. The
F0911Z1 lines are **AP Coder's distribution without the recoverable-tax lines**, and the check is

    sum(F0911Z1 amounts) = gross - (tax charged of the types the code recovers)

A difference means AP Coder's Tax setup and the E1 code disagree (e.g. PST set up as recoverable but the
province uses code C): E1 would post the PST twice or not at all, so the invoice is left out with an
*unbalanced* message until one of them is fixed. We check against the gross minus the tax the code
recovers, rather than against the gross itself, because under V / C / B E1 adds the recoverable tax line
itself; balancing to the gross would post that tax twice.

### Accounts: GL code → BU.Object.Subsidiary

Each distribution line's GL code (and cost center) is looked up in the mapping table:

1. a row with the same GL code **and** cost center;
2. else a row with the GL code and a blank cost center;
3. else the **default rule** (if on): BU = the line's cost center (or the *Default BU* when the line has none,
   or always when "BU = the line's cost center" is off), Object = the GL code, Subsidiary blank.

A row with a blank BU or object is completed by the default rule, so a mapping can change just the object.
A line that finds no account is *unmapped*: the invoice is left out. With *Account* = one field, AP Coder
writes VNAM = `2` and VNANI = `BU.OBJ.SUB` (`CC400.6010`, `100.6010.01`); with separate columns it writes
VNMCU, VNOBJ, VNSUB. A CSV / Excel mapping is imported with the columns *GL code, Cost center, BU, Object,
Subsidiary* (and optionally *Subledger, Subledger type*).

### Line numbering

Oracle's notes say the F0911Z1 line number (VNEDLN) must match the F0411Z1 line (VLEDLN); most loads
number the G/L lines 1, 2, 3 under pay item 1. The setting *Line numbering* offers both:

* **Sequential** (default): one pay item (VLEDLN = 1) and the G/L lines numbered 1, 2, 3, ….
* **Match header**: one pay item **per G/L line**, with VLEDLN = VNEDLN = 1, 2, 3, …. Each pay item's gross
  is its line plus its share of the recoverable tax (split by net amount, to the cent; the pay items add up
  to the invoice). Use it when the E1 edits insist on matching numbers.

### Currencies

The domestic currency (default CAD) gives VLCRRM = `D`; any other currency gives VLCRRM = `F`, VLCRCD = the
currency and the amounts in the foreign columns (VLACR, and VNACR on the lines; VLAG / VNAA blank: E1
converts them). VLCRR is the rate from Settings → Review → *Exchange rates* when there is one, else blank
(E1 then uses its exchange-rate table for the G/L date). Those rates are CAD per unit: with a domestic currency
other than CAD, VLCRR is the rate across CAD (USD books, a CAD invoice at USD 1.37: 0.729927), and blank without
a rate for the domestic currency. A currency not in *Currencies set up in E1* blocks the invoice.

### Credit notes

Credit notes are vouchers with negative amounts (VLAG, VNAA). Their due date is the invoice date. An
optional *Credit note document type* replaces VLDCT / VNDCT for them.

### PO-matched vouchers

With *PO-matched vouchers* on, an invoice with a PO number is written as a pay item **without F0911Z1
lines**, to be matched to the PO receipts in E1 (the match supplies the accounts). The F0411Z1 rows carry
VLPO, VLPDCT (`OP`) and VLPKCO, plus the configurable **extra columns** (default `VLATFLG`, `VLPSTE`, blank
values) for the customer's matching automation. A third file, `F0411Z1T.csv`, lists the invoice lines to
match. **Both the extra columns and the match file are unconfirmed**: E1's standard Z-file upload does not
match receipts by itself, so the JDE team must say how they want PO invoices (a custom match program, P4314
by hand, or an orchestration; see below). Invoices without a PO still get their G/L lines.

### PO numbers longer than 8 characters

VLPO holds 8 characters, and vendors often print longer POs (`4500078813`, `MNT-2026-028`). Sent as a
reference (*Send the PO number as a reference*, not PO-matched), such a PO does not hold the invoice back
and the clerk does not change what is printed: the voucher goes out with VLPO, VLPDCT and VLPKCO blank, the
PO is kept in AP Coder (the invoice, its exports and reports), and VLRMK reads `AP Coder #<id> PO <number>`
when that fits in its 30 characters. The review screen warns: "JD Edwards' PO field holds 8 characters: this
PO number is kept in AP Coder and not sent". With *PO-matched vouchers* on, the PO is E1's order number the
voucher is matched to, so one longer than 8 still holds the invoice back (D-87).

## Checks before export (`jde.validate`)

Blocking problems, per invoice:

* no supplier address number (AN8), or one that is not digits (up to 8);
* invoice number missing or longer than 25 characters; with *PO-matched vouchers* on, a PO number longer
  than 8 (E1's order number can't be one; see *PO numbers longer than 8 characters*);
* no invoice date;
* a distribution line whose GL code (+ cost center) has no JDE account, or an account too long (BU 12,
  object 6, subsidiary 8);
* the GL distribution does not add up to the invoice total, or is *unbalanced* for the tax code (above);
* currency not set up; province unknown while Canadian tax was charged; a tax code AP Coder cannot balance;
* **duplicates**: the same supplier (address number, or vendor name) and invoice number (compared the way
  people mean them: `INV-00123` = `#123`) twice in the batch, already in a live export batch, or in the
  imported ERP invoice register (Exports → *Invoices already in the ERP*). A credit note does not duplicate
  the invoice it reverses. E1's own duplicate-invoice edit (processing option: H = error, I = warning)
  remains the last word.

Settings problems (company not 5 digits, user ID missing or over 10 characters, batch prefix over 9, a
document type that is not 2 characters, a domestic currency that is not a 3-letter code) stop the whole export.

## F0411Z1: voucher pay items

| Column | Value | Notes |
| --- | --- | --- |
| VLEDUS | User ID (setting, ≤10) | key |
| VLEDBT | Batch prefix + AP Coder batch number (≤15) | key; one per export batch |
| VLEDTN | `APC` + AP Coder invoice number (≤22) | key; unique per invoice |
| VLEDLN | 1 (or 1, 2, 3 … in *match header* mode) | key |
| VLEDSP | `0` | not yet processed |
| VLEDTC | `A` | add |
| VLEDTR | `V` | voucher |
| VLDCT | Document type (default `PV`; credit note type if set) | blank lets E1 default it |
| VLCO | Company, zero-padded to 5 | |
| VLMCU | Header business unit (setting) | optional |
| VLAN8 | Supplier address number | vendor master ERP ID / override |
| VLVINV | Invoice number (≤25) | |
| VLDIVJ | Invoice date (Julian) | |
| VLDGJ | G/L date: invoice date (default) or the export day | |
| VLDSVJ | Tax date = invoice date | |
| VLAG | Gross amount | *gross* mode, domestic |
| VLATXA | Taxable amount | *tax amounts* mode, domestic |
| VLATXN | Non-taxable amount | *tax amounts* mode, domestic |
| VLSTAM | Tax amount (all Canadian tax charged) | *tax amounts* mode, domestic |
| VLTXA1 | Tax area | province table |
| VLEXR1 | Tax explanation code (V, C, B, E) | province table / self-assessed / exempt |
| VLCRRM | `D` domestic, `F` foreign | |
| VLCRCD | Currency code | |
| VLCRR | Exchange rate | only when set in AP Coder |
| VLACR | Foreign gross amount | *gross* mode, foreign |
| VLCTXA | Foreign taxable amount | *tax amounts* mode, foreign |
| VLCTXN | Foreign non-taxable amount | *tax amounts* mode, foreign |
| VLCTAM | Foreign tax amount | *tax amounts* mode, foreign |
| VLPTC | Payment terms code: the printed terms' code in the payment terms table, else the default code (blank on a credit memo) | blank: the supplier's terms (D-91) |
| VLDDJ | Due date (Julian): printed or worked out; the invoice date for a credit | setting can leave it blank |
| VLPST | Pay status (default `A`) | |
| VLPO | PO number (≤8) | as a reference, or for PO-matched vouchers; blank when the PO is longer (D-87) |
| VLPDCT | PO document type (default `OP`) | with VLPO |
| VLPKCO | PO company (default: the company) | with VLPO |
| VLRMK | `AP Coder #<id>`, plus `PO <number>` for a PO too long for VLPO when it fits in 30 characters | remark |
| VLGLBA | G/L bank account of the invoice's *Pay from* account | **unconfirmed**; only when mapped (below) |
| *extra* | e.g. VLATFLG, VLPSTE | **unconfirmed**; only in PO-matched mode |

**VLGLBA (G/L bank account).** Each approved invoice records *Pay from*, the company bank account that pays it
(GL accounts & tax → Bank accounts, D-66). E1 keeps the bank account of a voucher in GLBA as its *short account ID*,
which AP Coder does not know, so the column is a mapped setting: Settings → JD Edwards E1 → *G/L bank account (VLGLBA)
per Pay from account*, one box for each bank account in use (the code's case does not matter). With every box blank
(the default) F0411Z1 has no VLGLBA column and E1 takes the bank from the AAI PB, as before. Once any box is filled, a
voucher whose account has none gets a blank VLGLBA, and the dry run and the batch's README.txt list it to check before
loading (B-684). The bank account's own cash GL is *not* used as a default: it maps to BU.Object.Subsidiary (as the
distribution lines are written), which is not the short account ID GLBA holds, and a wrong bank is worse than E1's
default. Confirm with the JDE team whether R04110ZA keeps a VLGLBA it is given.

**VLPTC (payment terms).** Settings → JD Edwards E1 → *Payment terms → JD Edwards code* maps the terms printed on an
invoice to the company's payment terms code (P0014 / F0014, up to 3 characters), by what the terms mean, as AP Coder
reads them (`coding/terms.py`): "Net 30", "30 days", "N30" and "Payable dans les 30 jours" are all *Net 30*;
"2/10 Net 30" and "2% 10 days, net 30" are *2/10 Net 30*; EOM, prox and "Due on receipt" have rows of their own. A
second field gives a default code for terms not in the table. The voucher gets the table's code, else the default,
else a blank VLPTC (E1 takes the supplier's own terms from the supplier master, as before). The table opens with
example rows (Net 30 → N30, 2/10 Net 30 → 210…) that are *not used until the pilot tester saves the table*: check
each against your JD Edwards codes first. Once the table has rows and no default is set, an invoice whose terms
have no code gets a note on the review screen (JDE_TERMS_NO_CODE: "Payment terms 2/10 Net 45 have no JD Edwards
code…"), which holds nothing back, bulk approval included; terms AP Coder can't read are said by TERMS_NOT_FOUND
instead. A credit memo is sent with a blank VLPTC, as its due date is not sent: a code can carry a discount (2/10
Net 30) that E1 would work out on the credit. Confirm with the JDE administrator whether your credit memos should
carry a terms code instead. The review screen's voucher
block and the note under *Payment terms* show the code sent, and the dry run lists each voucher's
`payment terms Net 30 -> N30`. Whether VLDDJ (the due date, sent by default) or VLPTC wins when both are given is
up to R04110ZA's processing options: confirm it with the JDE team.

## F0911Z1: G/L distribution lines

| Column | Value | Notes |
| --- | --- | --- |
| VNEDUS, VNEDBT, VNEDTN | as the pay item | keys |
| VNEDLN | 1, 2, 3 … | see *Line numbering* (**unconfirmed**) |
| VNEDSP, VNEDTC, VNEDTR | `0`, `A`, `V` | as the pay item |
| VNCO | Company | |
| VNDGJ | G/L date (Julian) | as the pay item |
| VNDCT | Document type | as the pay item |
| VNAM | `2` | account format BU.Obj.Sub (one-field mode) |
| VNANI | `BU.OBJ.SUB` | one-field mode |
| VNMCU, VNOBJ, VNSUB | BU, object, subsidiary | separate-columns mode |
| VNSBL, VNSBLT | Subledger, subledger type | optional, from the mapping row |
| VNLT | `AA` | actual amounts ledger |
| VNAA | Amount | domestic |
| VNCRCD | Currency code | |
| VNACR | Foreign amount | foreign currency |
| VNEXA | Line description (≤30) | |
| VNEXR | Invoice number (≤30) | remark |
| VNTXA1, VNEXR1 | Tax area and code | optional (setting) |

## F0411Z1T: PO match lines (unconfirmed)

EDUS, EDBT, EDTN, EDLN (the pay item), DOCO (PO number), DCTO (PO type), KCOO (PO company), LNID (the
invoice's line number, *not* necessarily the PO line; E1 stores line 1 as 1000), LITM (blank: AP Coder
does not know the item number), DSC1 (description), UORG (quantity) and PRRC (unit price) written with a
decimal point, AEXP (amount, in the amount format) and CRCD (currency).

## Test import checklist (for the pilot tester's IT)

For the shadow pilot (D-92) the batches go to a **JDE test environment first** (a CRP or test environment with a
copy of production's setup), never to production. One page for the person at IT who loads them; tick each box and
send the answers in the last step back to the pilot tester. Where E1 is set up its own way at your company, your
JDE administrator's word wins over this page.

**Before the first batch**

- [ ] The test environment has the company, the suppliers (address numbers, AN8) and the accounts (BU.Object.Sub)
      the batch uses. A supplier or account missing there fails the load, not AP Coder.
- [ ] The pilot tester has filled Settings → JD Edwards E1 with your answers (or imported your worksheet).
- [ ] You have the batch's ZIP from AP Coder (Exports → *JD Edwards E1*): `F0411Z1.csv`, `F0911Z1.csv`,
      `README.txt` (batch number VLEDBT, user VLEDUS, voucher count and totals) and `DRY_RUN.txt` (each voucher's
      pay items and G/L lines, and why any invoice was left out).

**1. Load the Z-file batch**

- [ ] Load `F0411Z1.csv` into **F0411Z1** (voucher pay items) and `F0911Z1.csv` into **F0911Z1** (G/L lines) of the
      test environment's business data, with a table conversion or your database's import tool. The header row
      holds the tables' column names; each row is new (VLEDTC/VNEDTC = A), unprocessed (VLEDSP/VNEDSP = 0).
- [ ] Amounts and line numbers as the README says: implied decimals (5.50 written 550) or a decimal point, and the
      line numbers VLEDLN/VNEDLN as written (a direct table load may need 1000 for line 1; confirm with your JDE
      administrator). Business units may need right-justifying in 12 characters on a direct database load.
- [ ] Count the rows loaded: they must equal the README's counts for F0411Z1 and F0911Z1.

**2. Run the Voucher Batch Processor, R04110ZA**

- [ ] Run **R04110ZA** for the batch's user and batch number (data selection on VLEDUS and VLEDBT), with a version
      copied for the pilot (for example from ZJDE0001; confirm with your JDE administrator which version to use).
- [ ] Its processing options to check before running (the tab names differ between releases: confirm with your JDE
      administrator):
      - **Mode: proof first.** Run in proof mode, read the report, then run the same version in final mode.
      - **The G/L date**: AP Coder sends VLDGJ/VNDGJ (the invoice date, or the export day, per Settings → JD
        Edwards E1); check the version keeps the date it is given, and that the test environment's period for that
        date is open.
      - **Tax**: whether the version (and the Voucher Master Business Function version it calls, P0400047) works the
        tax out again from the tax area and explanation code (VLTXA1, VLEXR1) or keeps the amounts sent. AP Coder
        sends the gross only, or the taxable and tax amounts, never both (see "Gross amount or tax amounts").
      - **Duplicate invoice check**: the P0400047 version's duplicate-invoice edit (a warning or an error), so a
        voucher already entered by AP in the test environment is caught.
      - **Batch approval and purge**: whether the batch is created approved or pending, and whether processed
        Z-rows are purged (keep them for the first tests: you compare them below).
- [ ] Final mode marks each Z-row processed (VLEDSP = 1). A row left at 0 has an error.

**3. Compare after the run**

- [ ] **Vouchers created**: as many as the README says, one per AP Coder invoice; each voucher's supplier (AN8) and
      invoice number (VINV) as in `DRY_RUN.txt`.
- [ ] **The batch total**: the voucher batch's total in Review Vouchers / batch review (P0011) equals the README's
      total for each currency.
- [ ] **The tax**: each voucher's tax amount, tax area and explanation code against `DRY_RUN.txt`; a difference of
      a cent or more means E1 worked the tax out differently: say which vouchers.
- [ ] **The account distribution**: each voucher's G/L lines (account BU.Obj.Sub and amount) against `F0911Z1.csv`;
      recoverable GST/HST/QST is posted by E1 from its tax rules (AAIs), so those lines are E1's own.
- [ ] **Error messages**: every message of the R04110ZA report and of the Employee Work Center (P01131) for the
      batch, and any row still unprocessed in Batch Voucher Revisions (P0411Z1).

**4. Report back to us**

Send the pilot tester (who passes it on; no supplier names or amounts needed for us):

- the batch number, the R04110ZA version and mode, and whether proof and final ran clean;
- vouchers created against the README's count; the batch total against the README's; the vouchers whose tax or
  account distribution differed, by AP Coder number (VLEDTN = APC + the AP Coder invoice number);
- each error message as E1 wrote it (the text, not a screenshot of the whole screen), with its voucher;
- your answers to anything above marked "confirm with your JDE administrator".

Then, for the shadow pilot's score, export from the test environment (or from production for the invoices AP
entered by hand) the vouchers of the same days: **F0411** with RPAN8, RPVINV, RPDIVJ, RPAG, RPSTAM, RPCRCD, RPDDJ,
RPDOC, RPDCT, RPKCO, RPSFX (RPTXA1 and RPEXR1 if you can; RPACR and RPCTAM when there are foreign-currency vouchers)
and **F0911** with GLDOC, GLDCT, GLKCO, GLANI (or GLMCU, GLOBJ, GLSUB), GLAA, GLEXA, GLLT (GLACR for foreign
currency). Keep RPDOC and RPSFX: without them a voucher entered twice, or a credit memo with the invoice's number,
can only be told apart by its date and sign, and the report says so. Without RPACR and RPCTAM a foreign voucher's
gross and tax are in CAD and are not compared. CSV or Excel (a large export as CSV: an Excel file over 20 MB once
unzipped is not opened); E1's Julian dates (0 for no date) and implied decimals are read as they are (add
`--implied-decimals` for amounts without a decimal point). The pilot tester runs on the pilot laptop:

```
python -m ap_coder pilot-score --vouchers F0411.xlsx --distribution F0911.xlsx
```

It writes `pilot_score.md` and `pilot_score_differences.csv` into the data folder's `pilot_score` folder; these
stay on the laptop. Insights shows the headline figures.

## Open questions for the JDE team

Every question here is in the worksheet (Settings → JD Edwards E1 → *Download the worksheet*, with *Show JD Edwards
settings* on in Settings → About); a test checks that each one stays covered.

* Company numbers, the business units for the header (VLMCU) and lines, and the PO company.
* Document type for imported vouchers (PV or a custom one), credit notes, and the **P0400047 version** run by
  R04110ZA (its processing options: tax recalculation, duplicate-invoice edit H/I, default G/L date, …).
* Tax areas and codes per province, and how the AAIs post GST, HST, QST and PST (the C and B codes in particular).
* Implied decimals or decimal points; gross only or taxable / tax; VNEDLN numbering; whether the line numbers
  VLEDLN / VNEDLN, written 1, 2, 3, load as written (E1 shows EDLN with display decimals: a direct table load
  may read 1 as 0.001 and need 1000, a table conversion does not).
* Account mapping and subledgers; whether BUs must be right-justified (E1 stores MCU right-aligned in 12
  characters; a database load may need the padding, a table conversion does it).
* Batch numbering and the user R04110ZA is run for; whether a batch should be limited in size.
* The PO path: matched in E1 by hand, by a custom program reading the match file, or through an orchestration;
  the automation columns of the pay item.
* Exchange rates: E1's table (VLCRR blank) or AP Coder's.

## Later: direct integration

The files are the first step. Three ways to skip them, in order of preference for volume:

1. **Insert into the Z-tables, then R04110ZA on a schedule** (recommended for high volume). AP Coder (or a
   small service next to it) inserts the same rows straight into F0411Z1 / F0911Z1 through a database account
   limited to those tables, and a scheduled R04110ZA job (proof, then final, e.g. every hour) creates the
   vouchers. The mapping and checks in `jde.py` stay the same; only the transport changes. Errors come back
   through the Z-tables (VLEDSP stays 0) and the job report.
2. **AIS / Orchestrator**: one REST call per invoice to an E1 orchestration, e.g. the delivered
   **JDE_ORCH_04_Add_Supplier_Invoice** (Accounts Payable orchestrations), or a **custom orchestration** that
   calls the voucher entry form (P0411 / P051111) or R04110ZA. Real-time answers (voucher number or the error)
   for each invoice; best for moderate volume and PO-matched invoices, since an orchestration can drive the
   receipts match (P4314).
3. **Business services (BSSV)**: the published business service **AccountsPayableManager (JP040000)**,
   operation **processVoucher**, a SOAP web service that creates a voucher with its G/L distribution. Fine
   where BSSV is already deployed; Oracle's direction is AIS / Orchestrator.
