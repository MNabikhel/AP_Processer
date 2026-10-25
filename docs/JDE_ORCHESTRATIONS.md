# JD Edwards orchestrations for AP Coder (the spec for the JDE team)

AP Coder can talk to JD Edwards EnterpriseOne through the **AIS Server**: it signs in as its own JDE service user and
calls **named orchestrations** with JSON in and JSON out. It never reads or writes JDE tables itself. This page is what
the JDE team builds: seven orchestrations, the service user and its security role, and the answers AP Coder expects.
The decision is D-104 (docs/decisions.md); the batch files of docs/JDE_E1.md stay the fallback.

* **Step 1, read only** (replaces the CSV imports): suppliers, GL accounts, business units, open POs with their
  receipts, and the AP ledger's vouchers (for the duplicate check and "already paid?").
* **Step 2, vouchers**: an approved export posts each voucher through one orchestration: a dry run first (proof
  mode), then a person's approval (FINAL mode); JDE's voucher number, or its error, is kept on the invoice.

Everything AP Coder sends and reads is named in one file, `ap_coder/erp/jde_orchestrations.json`: the AIS paths, each
orchestration's name, its inputs and its outputs. To use other names, copy that file to AP Coder's data folder as
`jde_orchestrations.json` and change only what differs there; AP Coder reads that copy over its own, key by key.

Lines marked **CONFIRM** are what the JDE team must check for their tools release; the full list is at the end.

## The connection

| What | AP Coder uses | CONFIRM |
| --- | --- | --- |
| AIS address | `https://<server>:<port>` saved in Settings → JD Edwards E1 → API connection | the AIS Server's HTTPS address and port |
| Version check | `GET /jderest/v2/defaultconfig` (its `aisVersion`; not required) | that it answers |
| Sign in | `POST /jderest/v2/tokenrequest` with `{"username", "password", "environment", "role", "deviceName"}`; the token is read from `userInfo.token` | the v2 path on your release |
| Each call | `POST /jderest/v3/orchestrator/<name>`, header `jde-AIS-Auth: <token>` and `jde-AIS-Auth-Device` | v3 orchestrator path |
| Basic authentication (option) | `Authorization: Basic …` on each call, with `jde-AIS-Auth-Environment`, `-Role` and `-Device` | that your AIS allows Basic on v3 |
| Expired token | HTTP **444** (AIS's invalid-token status): AP Coder signs in again once | the status your AIS answers |
| Sign out | `POST /jderest/v2/tokenrequest/logout` with `{"token"}` | |
| Dates in inputs and outputs | `YYYY-MM-DD` (`date_format` in the mapping) | the date format your orchestrations take and give |
| Page size | 500 rows (`page_size`) | AIS's maximum rows for data requests (Server Manager) |

* **HTTPS** with the certificate checked: against the computer's trusted certificates, or a company CA file named in
  Settings. AP Coder never follows a redirect, never uses a proxy, and connects to nothing but the one AIS address
  saved; an address that is not on the company network (a public IP address, or a name resolving to one) is refused.
* **Timeouts and retries**: 30 seconds a request (Settings); a read is tried again up to twice when AIS is busy
  (502/503/504) or the connection fails; a voucher's request is never repeated by the client (see *Never twice*).
* **Errors**: AP Coder reads JDE's own messages from the answer and shows them as they are: `userDefinedErrorText`,
  `message` (text, or a form request's `errors` / `warnings` with `CODE`, `TITLE`, `DESC`), an orchestration's
  `Messages`, and `jde__status: ERROR` on a v3 answer. Use **Custom error text** in the orchestrations where JDE's own
  message would not say what is wrong.

## The service user and its role

One JDE user for AP Coder (e.g. `APCODER`), not a person's account, with a password that does not expire (or a
process to change it in AP Coder's settings), and one role (e.g. `APCAPI`) holding only this:

| Security (P00950) | For |
| --- | --- |
| AIS / orchestrator access: the seven orchestrations below (UDO feature security, *Run*) | everything |
| Data request / view access to F0101, F0401, F0030, F0116, F0014 | `APC_Suppliers_Read` |
| F0901, F0006 | `APC_Accounts_Read`, `APC_BusinessUnits_Read` |
| F4301, F4311, F43121 | `APC_OpenPOs_Read` |
| F0411 | `APC_Vouchers_Read`, `APC_Voucher_Status` |
| Route (a): F0411Z1 / F0911Z1 insert and R04110ZA (its version) run; route (b): P0411 / P0411Z1 forms used (add) | `APC_Voucher_Create` |
| Row security limiting the companies and business units to AP's, as for an AP clerk | all |

Nothing else: no table updates outside the Z-tables or the voucher application, no other applications.

## The read orchestrations (step 1)

All five page the same way: AP Coder sends `MaxRows` and the key of the last row it has (`After…`, blank for the first
page) and asks again while a page comes back full. Rows come back sorted by that key. They are read every night (and
on *Sync now*); the vouchers are also read one supplier at a time while an invoice is reviewed.

### APC_Suppliers_Read: suppliers (F0101 / F0401)

| Input | Meaning |
| --- | --- |
| `ChangedSince` | blank: every supplier (AP Coder sends blank today) |
| `AfterAddressNumber`, `MaxRows` | paging |

Output `Suppliers`, one row per supplier (search type V, or the types your company uses for suppliers):

| Output | JDE | AP Coder |
| --- | --- | --- |
| `AddressNumber` | F0101.ABAN8 | the vendor's ERP ID (the voucher's VLAN8) |
| `AlphaName` | F0101.ABALPH | the vendor's name (matched to the invoices' vendor names) |
| `TaxId` | F0101.ABTAX | GST/HST number |
| `AdditionalTaxId` | F0101.ABTX2 | QST number (Quebec suppliers), else PST number. CONFIRM where QST / PST numbers are kept |
| `PaymentTermsCode` | F0401.A6TRAP | the terms, through Settings → JD Edwards E1's terms table when no description |
| `PaymentTermsDescription` | F0014.PNPTD | the terms as words ("Net 30") |
| `HoldPaymentCode` | F0401.A6HDPY | not blank: the vendor is **on hold** in AP Coder |
| `BankTransit` | F0030.AYTNST | with the next, the account JDE pays on ("00012-003 …4567"), for the bank-change check |
| `BankAccountLast4` | the last 4 digits of F0030.AYCBNK | **only the last four digits**: never the whole account |
| `State` | F0116.ALADDS | province |
| `CurrencyCode` | F0401.A6CRRP | (read, not used yet) |

### APC_Accounts_Read: GL accounts (F0901)

Inputs `Company` (blank: all), `AfterAccountId`, `MaxRows`. Output `Accounts`: `AccountId` (GMAID), `BusinessUnit`
(GMMCU), `ObjectAccount` (GMOBJ), `Subsidiary` (GMSUB), `Description` (GMDL01), `PostingEdit` (GMPEC),
`LevelOfDetail` (GMLDA). AP Coder's GL code is the object, or `object.subsidiary`; an account with a subsidiary is
added to Settings → JD Edwards E1's account mapping so the voucher sends it as that object and subsidiary. Only
accounts with a blank posting edit are taken (CONFIRM which posting edit codes take postings). Return the posting
accounts of the levels AP codes to (CONFIRM: level 7 and below?).

### APC_BusinessUnits_Read: business units (F0006)

Inputs `Company`, `AfterBusinessUnit`, `MaxRows`. Output `BusinessUnits`: `BusinessUnit` (MCMCU, trimmed),
`Description` (MCDL01), `Company` (MCCO), `BusinessUnitType` (MCSTYL), `PostingEdit` (MCPECC). They are AP Coder's
cost centers (the voucher's BU with Settings' "BU = the line's cost center").

### APC_OpenPOs_Read: open POs and what was received (F4311 / F43121)

Inputs `OrderType` (OP by default; Settings), `AfterKey`, `MaxRows`. Output `Lines`, one row per open PO line:

| Output | JDE |
| --- | --- |
| `Key` | a sortable unique key: DOCO-DCTO-KCOO-LNID |
| `OrderNumber`, `OrderType`, `OrderCompany`, `LineNumber` | PDDOCO, PDDCTO, PDKCOO, PDLNID (as shown, 1.000) |
| `SupplierNumber`, `SupplierName` | PDAN8, its F0101.ABALPH |
| `Description` | PDDSC1 |
| `QuantityOrdered`, `UnitCost`, `ExtendedAmount` | PDUORG, PDPRRC, PDAEXP (in units, not implied decimals) |
| `QuantityReceived` | the sum of F43121.PRUREC of the line's receipts (match type 1) |
| `ObjectAccount`, `Subsidiary`, `BusinessUnit` | from PDANI |
| `NextStatus` | PDNXTR (999: closed) |

A PO AP Coder had from JDE that is no longer in the answer is closed in AP Coder. AP Coder knows a PO by its number
alone (PDDOCO): CONFIRM that the order numbers of the types read do not overlap.

### APC_Vouchers_Read: the AP ledger (F0411)

Inputs `SupplierNumber` and / or `InvoiceNumber` (either may be blank), `FromInvoiceDate` (the nightly read: the last
730 days, Settings), `AfterKey`, `MaxRows`. Output `Vouchers`, one row per voucher (pay item 001, or the pay items
added up): `Key` (KCO-DCT-DOC-SFX), `DocumentNumber` (RPDOC), `DocumentType` (RPDCT), `DocumentCompany` (RPKCO),
`SupplierNumber` (RPAN8), `SupplierName`, `InvoiceNumber` (RPVINV), `InvoiceDate` (RPDIVJ), `GrossAmount` (RPAG),
`OpenAmount` (RPAAP), `PayStatus` (RPPST: P paid, A approved, H held), `BatchNumber` (RPICU). Voided vouchers are left
out. While an invoice is reviewed AP Coder asks with the supplier's number only, page after page (`AfterKey`), and compares invoice numbers itself
(it ignores dashes, spaces and leading zeros), so the orchestration need not match numbers loosely.

## The voucher (step 2)

### APC_Voucher_Create

AP Coder sends one voucher per call:

| Input | What |
| --- | --- |
| `IdempotencyKey` | `APC-<installation id>-<AP Coder invoice id>-<batch>`, unique per voucher (the installation id: 10 random hex digits per AP Coder data folder, new after a restore; at most 30 characters in all) |
| `PayloadFingerprint` | SHA-256 of the voucher's rows (64 hex digits): keep it with the key and answer it back (below) |
| `ProcessingMode` | `PROOF` (the dry run: check everything, commit nothing) or `FINAL` |
| `PayItems` | array: the voucher's **F0411Z1** rows, column for column as the batch files (docs/JDE_E1.md: VLEDUS, VLEDBT, VLEDTN, VLEDLN, VLDCT, VLCO, VLAN8, VLVINV, VLDIVJ, VLDGJ, VLAG / VLATXA…, VLTXA1, VLEXR1, VLCRCD, VLPTC, VLDDJ, VLPO…, VLRMK) |
| `GLLines` | array: its **F0911Z1** rows (VNEDLN, VNANI or VNMCU / VNOBJ / VNSUB, VNAA / VNACR, VNEXA, VNEXR…) |
| `ReceiptMatches` | array: the PO-match rows (F0411Z1T layout of docs/JDE_E1.md), empty unless PO-matched vouchers are on |

The amounts, dates (Julian CYYDDD) and text are exactly as in the files, with the settings of Settings → JD Edwards
E1 (implied decimals by default, the tax coded to G/L accounts by default, D-93). The batch number (VLEDBT = prefix +
AP Coder batch) and transaction number (VLEDTN = APC + invoice id) are the Z-tables' own keys.

Outputs: `Result` (`VALID` after a proof that passed, `CREATED`, `EXISTS` for a key seen before: with the voucher it
made, or `ERROR`), `DocumentNumber`, `DocumentType`, `DocumentCompany`, `BatchNumber`, `Messages` (JDE's warnings or
errors, CODE / TITLE / DESC), and with EXISTS who that voucher is for: `SupplierNumber` (VLAN8), `InvoiceNumber`
(VLVINV), `GrossAmount` (VLAG, in units) and the `PayloadFingerprint` kept with the key. AP Coder takes EXISTS as
posted only when that record is this voucher (the same fingerprint, else the same supplier, invoice number and gross);
otherwise it stops and asks a person. `ERROR` means nothing was made: AP Coder marks the voucher refused at once. A
failed orchestration (HTTP 500, `jde__status` ERROR, the form request's errors) is looked up by its key first
(`APC_Voucher_Status`) before it is marked refused. Anything else (`CREATED` without a document number, an answer AP
Coder does not know, HTTP 502 / 504, a timeout) is "sent, being checked": looked up by its key, never sent blind.
HTTP 503 is taken as AIS turning the request away before running it (CONFIRM for your load balancer) and retried.

Two ways to build it; the JDE team picks one:

**(a) The Z-tables and R04110ZA**: the same route as the batch files, so the same field mapping and R04110ZA's own
edits, AAIs and processing options.

1. Look for the key (see *Never twice*); found, answer EXISTS with that voucher.
2. Insert the `PayItems` rows into F0411Z1 and the `GLLines` into F0911Z1 (the orchestration's *Database* steps or a
   custom business function), with VLEDSP / VNEDSP 0 and VLEDTC / VNEDTC A.
3. Run R04110ZA (a version for AP Coder) for that batch (VLEDUS + VLEDBT): in **proof** mode for `PROOF`, in **final**
   mode for `FINAL`; its processing option version decides the defaults and the duplicate-invoice edit.
4. Read back the result: VLEDSP = 1 and the voucher's F0411 document number (VLDOC / VLDCT / VLKCO), or the errors
   from the Z-table (P0411Z1 / the work center). For `PROOF`, delete the rows the run left in the Z-tables.
5. Keep the key with the voucher (step 1 of the next call).

**(b) Voucher Entry (P0411) as a form service**: the orchestration fills the Standard Voucher Entry form (W0411A, the
P0411 version AP uses) from the same rows: supplier (VLAN8), invoice number and dates, company, gross, the terms code,
then the G/L distribution grid (W0411K) from `GLLines`, and presses OK; JDE's own edits answer as they would for a
clerk. `PROOF`: fill the forms and press OK only through the edits, then cancel (CONFIRM how your P0411 version can
be checked without committing; otherwise use route (a) for the proof). Store the key in the voucher's remark or a
custom table (step 1 of *Never twice*).

The batch files keep working with either route: the Exports page offers both.

### APC_Voucher_Status

Input `IdempotencyKey`. Outputs `Found` (`1` / `0`: anything else is taken as "can't say") and, found,
`DocumentNumber`, `DocumentType`, `DocumentCompany`, `BatchNumber`, `SupplierNumber`, `InvoiceNumber`, `GrossAmount`,
`PayloadFingerprint`, as for EXISTS. AP Coder asks it before sending any voucher again, so a request whose answer was lost is never posted
twice.

### Never twice

AP Coder sends a voucher again only after a lost answer, and then only after `APC_Voucher_Status` said JDE does not
have its key. JDE keeps one voucher per key all the same: the orchestration looks for the key first (route (a): the
F0411Z1 rows of that VLEDUS / VLEDBT / VLEDTN, which also stops a batch file loaded after the API posted it; route
(b): the key stored with the voucher) and answers EXISTS. JDE's own duplicate-invoice edit (supplier + invoice
number, hard error) is the last word.

## Testing it

AP Coder's tests run every orchestration against a stand-in AIS server (`tests/fake_ais.py`): the token flow and its
expiry, a wrong password, no permission, an orchestration not built, timeouts, AIS busy, JDE's error shape, paging and
the voucher with its key. For the first real trial: Settings → JD Edwards E1 → API connection, on a test environment
(e.g. JPY920 / JDV920), **Test connection**, step 1's **Sync now**, then a dry run of one batch; step 2 on only once the
dry runs agree with what AP expects.

## To confirm (the JDE team)

1. The AIS address, port and tools release; the v2 token request, v2 logout and v3 orchestrator paths.
2. Token or Basic authentication on the v3 orchestrator; the HTTP status for an expired token (444).
3. The date format of the orchestrations' inputs and outputs (YYYY-MM-DD here).
4. AIS's maximum rows for a data request, and the page size AP Coder asks for (500).
5. The service user, its environment (production and test) and role, its password policy, and the security in the
   role table above.
6. The orchestration names, or the renamed ones in a copy of `jde_orchestrations.json`.
7. Which address book search types are suppliers; where the QST and PST numbers are kept (ABTX2?); the bank
   account (F0030) as transit and last four digits only.
8. Which posting edit codes take postings (blank), and which account levels to send.
9. The PO order types to read, and whether order numbers repeat across types or companies.
10. The vouchers left out of `APC_Vouchers_Read` (voided, reversed) and how far back to read.
11. The voucher route, (a) Z-tables and R04110ZA or (b) P0411, its version and processing options (the
    duplicate-invoice edit H), and how the proof mode is done without committing.
12. Where the idempotency key is kept and how `APC_Voucher_Status` finds it.
13. Everything already listed in docs/JDE_E1.md for the batch files (company, document type, amounts, the tax coded
    to G/L accounts with code E, the account mapping, line numbering), which the API sends the same way.
