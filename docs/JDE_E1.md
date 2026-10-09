# JD Edwards EnterpriseOne (E1) export

AP Coder can hand approved invoices to JD Edwards E1 (9.1 / 9.2) as **batch vouchers**: the files that
E1's Voucher Batch Processor (**R04110ZA**, or the older R04110Z) reads from its two interface ("Z") tables:

| File | Z-table | One row per |
| --- | --- | --- |
| `F0411Z1.csv` | F0411Z1 (Voucher Transactions - Batch File) | voucher pay item |
| `F0911Z1.csv` | F0911Z1 (Journal Entry Transactions - Batch File) | G/L distribution line |
| `F0411Z1T.csv` | *(not a standard table: see "PO-matched vouchers")* | PO match line |

The code is in `ap_coder/jde.py` (pure logic), the settings in `ap_coder/webapp/jde_settings.py`
(Settings → **JD Edwards E1**) and the export on the Exports page (ERP format **JD Edwards E1**).

## Using it

1. **Settings → JD Edwards E1**: company, user ID, batch prefix, document type, the amount and account
   formats, the GL → BU.Object.Subsidiary mapping (a table, or import a CSV / Excel file), the province →
   tax area table and, if used, PO-matched vouchers. Go through the *Confirm with your JDE team* checklist.
2. **Supplier numbers**: each vendor's JDE address number (AN8) comes from the vendor master's ERP ID
   (Vendors page → import the vendor master from E1). A vendor whose ERP ID is not its address number can
   be given one in Settings → JD Edwards E1 → *Supplier address numbers*.
3. **Exports**: pick the invoices, choose *JD Edwards E1*, and export. Invoices that fail the checks
   (below) are listed with the reasons and are **left out of the batch**; they stay in *Ready to export*.
4. Download the ZIP: `F0411Z1.csv`, `F0911Z1.csv` (and `F0411Z1T.csv` for PO-matched vouchers) and a
   `README.txt` with the batch number, counts, totals and the loading steps:
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
* **Text** is cut to the column size (VLVINV 25, VLRMK / VNEXA / VNEXR 30), on one line; anything a
  spreadsheet could run as a formula is prefixed with an apostrophe (`safe.csv_row`). Files are UTF-8
  without a byte-order mark.

### Gross amount or tax amounts (never both)

E1 rejects a pay item that has both a gross amount and taxable / tax amounts that disagree, so the
setting *Amounts* chooses one:

* **Gross only** (default): VLAG (or VLACR in a foreign currency). E1 calculates the tax from the tax area
  and explanation code.
* **Taxable / non-taxable / tax**: VLATXA (taxable), VLATXN (non-taxable = gross - taxable - tax) and
  VLSTAM (all Canadian tax charged); VLAG is blank (VLCTXA / VLCTXN / VLCTAM in a foreign currency).
  E1 keeps the tax printed on the invoice.

### Tax areas and explanation codes

The province of supply (ship-to, else supplier province) picks the tax area (VLTXA1) and explanation
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
(E1 then uses its exchange-rate table for the G/L date). A currency not in *Currencies set up in E1* blocks
the invoice.

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

## Checks before export (`jde.validate`)

Blocking problems, per invoice:

* no supplier address number (AN8), or one that is not digits (up to 8);
* invoice number missing or longer than 25 characters; PO number longer than 8;
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
| VLPTC | Payment terms code (setting) | blank: the supplier's terms |
| VLDDJ | Due date (Julian): printed or worked out; the invoice date for a credit | setting can leave it blank |
| VLPST | Pay status (default `A`) | |
| VLPO | PO number (≤8) | as a reference, or for PO-matched vouchers |
| VLPDCT | PO document type (default `OP`) | when there is a PO |
| VLPKCO | PO company (default: the company) | when there is a PO |
| VLRMK | `AP Coder #<id>` | remark |
| *extra* | e.g. VLATFLG, VLPSTE | **unconfirmed**; only in PO-matched mode |

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

## Open questions for the JDE team

* Company numbers, the business units for the header (VLMCU) and lines, and the PO company.
* Document type for imported vouchers (PV or a custom one), credit notes, and the **P0400047 version** run by
  R04110ZA (its processing options: tax recalculation, duplicate-invoice edit H/I, default G/L date, …).
* Tax areas and codes per province, and how the AAIs post GST, HST, QST and PST (the C and B codes in particular).
* Implied decimals or decimal points; gross only or taxable / tax; VNEDLN numbering.
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
