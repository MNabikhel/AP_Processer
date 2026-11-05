# AP Coder `ap_coder/coding/` and `ap_coder/erp/`: notes for the 36-hour goal

Reader: coding + erp. Checkout: `main` at af9925b. Read only: nothing in the repo was changed. Every file of both
packages was read (coding 6,191 lines; erp 9,523 lines). Probes (scripts in
`scratchpad/probe/p1.py` … `p6.py`) ran against throwaway
stores in the scratchpad, since deleted. "Verified" means reproduced by one of them; "read" means certain from the
code; "suspected" depends on JDE or on data we don't have.

Target: about 25,000 invoices a month (1,250 a day), about 300,000 a year, millions of lines; 300 to 400 people on
one Windows VM (32 vCPU, 64 GB); web workers default to one per core, at most 8 (`web/server.py:221-223`); one
SQLite database in WAL mode, busy timeout 30 s (`core/store/_base.py:938-941`), one write transaction per
`with store._conn()` (`_base.py:949-975`).

---------------------------------------------------------------------------------------------------------------------

## 1. Map

### coding/ (the checks, the tax, the review grid, bulk approval)

| Module | Lines | Main functions |
| --- | --- | --- |
| `validation.py` | 417 | `validate_coding` :103 (159 lines: header, lines, totals, posting balance, `check_taxes`, history, Azure cross-check), `score` :264, `needs_review` :282, `coding_confidence` :325, tolerances `_CENT_TOLERANCE` :85, `_rounding_tolerance` :92 |
| `tax.py` | 1050 | `TaxRateTable` :118 (effective-dated, `_in_force` :155), `TaxSetup` :212, `supply_source`/`place_of_supply` :305/:334, `check_taxes` :387 (10 steps :425-783), `build_gl_distribution` :906, `_allocate` :889 (float split), meals `half_down`/`tax_shares`/`meals_parts` :998-1036 |
| `taxmath.py` | 243 | `math_check` :47 (the review screen's Tax check: lines row :79, tax row :94, two rates :141, why :159, self-assess rows :204, total row :235) |
| `line_tax.py` | 1088 | line tax codes (`tax_state` :136, `code_for` :163, `parse_code` :179, `options` :265), printed-code reading (`parse_legend` :376, `read_flags` :408), subset-sum solver (`subset_solutions` :519 meet in the middle with numpy, `solve` :556, MAX_SOLVER_LINES 40 :458), `prefill` :638 / `_prefill` :685 (167 lines), learning (`tax_vote`, `learned_codes` :1017) |
| `taxreturn.py` | 528 | ITC/ITR `build` :199 (from `invoice_summary`, D-110), `tax_facts` :246, `charged_in_error` :311, `self_assessment` :397, `self_assessed_base` :455, CSVs |
| `terms.py` | 433 | `parse_terms` :209 (lru_cache 4096), `terms_label`/`terms_key` :264/:288 (the JDE terms table key), `payment` :313, `payment_findings` :394 |
| `vendors.py` | 634 | `norm_invoice_number` :64, `same_tax_number` :79, bank compare :131-198, `vendor_findings` :334 (131 lines), `held_vendor` :491, `moved_off_hold` :516, `_jde_live_findings` :534, `_jde_bank_findings` :551, master import `master_rows` :607 |
| `review.py` | 221 | `coding_from_inputs` :30, `codes_to_pick` :108, `split_line` :151 |
| `bulk.py` | 281 | `issues_today` :109 (re-checks stale invoices), `sort_for_bulk` :163, `bulk_approve` :211 (sequential `finalise_coding` per invoice :263) |
| `manager_mail.py` | 103 | `request_email` :87 (an .eml draft for a cost-center manager, D-138) |
| also: `po.py` 515 (PO match, `match_invoice` :284, `billed_by_line` :493), `banks.py` 207 (Pay from), `rules.py` 142, `vendor_mail.py` 321 | | |

### erp/ (exports, JD Edwards, the hub, reports)

| Module | Lines | Main functions |
| --- | --- | --- |
| `exports.py` | 329 | `build` :302 (xlsx/csv/custom), `distribution` :130, `line_rows` :151, `entry_lines` :191 |
| `jde.py` | 1856 | settings `JdeSettings` :205 / `from_json` :247; formats `julian` :348, `jde_amount` :360, `_units` :369, `_split` :378; `resolve_an8` :408, `account_for` :421; `_prepare` :708 (170 lines, every voucher check), `voucher_rows` :935 (147 lines), `validate` :1241, `duplicate_problems` :1154, `prior_records` :1139; review screen `voucher_findings` :1284, `voucher_view` :1357; files `export_batch` :1471, `readme` :1496, `build_zip` :1585; `dry_run` :1647 |
| `jde_ais.py` | 802 | `AisSettings` :138, `save` :325, address checks :232-322, `AisClient` :474 (`_once` :537 a new TCP/TLS connection per request, `_request` :590 retries, `login` :614, `orchestration` :686 444 once, `rows` :718 paging), `connection_test` :779 |
| `jde_sync.py` | 483 | `sync_suppliers` :81, `sync_gl_accounts` :136, `sync_business_units` :166, `sync_purchase_orders` :193, `sync_vouchers` :252, `sync` :267, `due` :332, `live_findings` :376, `tick`/`Background` :441/:460 |
| `jde_post.py` | 787 | `idem_key` :82, `_payloads` :133, `_put` :197 / `_claim` :223 (compare-and-set), `dry_run` :457, `_deliver` :586, `_failed_send` :617, `_answered` :644, `post` :679, `_run` :714, `retry_queue` :742, `undo_batch` :320, `release` :291 |
| `jde_worksheet.py` | 502 | `build` :278, `read` :409 (the JDE team's answers back into the settings) |
| `hub/` | 1770 | `run.export` :300, `_run` :348, `State` (state.db fingerprints), `HubSchedule` :501; `tables.py` six builders :408-651; `landing.py` files, Parquet/CSV, gateway SQLite :313; `onelake.py` push |
| reports | | `accruals.build` :263, `controls.build` :64, `dupaudit.find` :139, `search.find` :199, `spend.parts`/`workbook` :76/:196, `insights.compute` :201, `statements.reconcile` :130, `stamp.batch_zip` :219, `directory.run` :333, `recurring.detected` :102, `export_layout.build` :145, `registers.rows_from_records` :40 |

What works well, to keep: the posting state machine with compare-and-set (`jde_post._claim` :223, `rev`), the
ledger outside the database and a new installation id after a restore (`core/store/postings.py:1-14`), batch
creation under `BEGIN IMMEDIATE` (`core/store/purchasing.py:226-268`), the invoice summary (D-110) that the tax
return, accruals, insights, directory and quick search candidates use, and a large set of JDE safety tests.

---------------------------------------------------------------------------------------------------------------------

## 2. Scale

### 2a. Reports and exports that load everything or scan whole tables (a year: 300k invoices, millions of lines)

| Where | What it loads | A year of data | Evidence |
| --- | --- | --- | --- |
| `erp/hub/run.py:361` `store.hub_invoice_rows()` (`core/store/hub.py:20-37`) | every invoice with `ai_output`, `final_output`, `meta` JSON decoded, on **every run, incremental too** | 300k invoices of tens of KB of decoded JSON each: many GB in the background process | read |
| `hub/run.py:372` `build(store, "activity", ctx, after_id=0)` | every event since id 0, decoded, then filtered by the watermark | millions of events each run | read |
| `hub/run.py:380-383` | sha256 fingerprint of every row of every table, plus every fingerprint sent (`State.sent`) | ~3M+ line rows a year | read |
| `hub/run.py:431` `_gateway` / `landing.gateway_database` :313 | the whole SQLite copy for the gateway, rebuilt each run | whole tables | read |
| `hub/tables.py:553-607` `accuracy` | `outcome_rows` worked out again for every approved invoice without a record, plus every feedback row | | read |
| `erp/controls.py:68` | `store.events(limit=1_000_000)`, newest first, JSON decoded, **filtered by date in Python** | beyond 1M events an older period is silently empty (see §5) | verified (`p5.py`, cap scaled down) |
| `controls.py:79` | `invoice_columns(("id","reviewed_at","second_reviewer","final_output"), APPROVED)`: every approved invoice's coding | 300k JSON parses | read |
| `erp/dupaudit.py:107-126` `bills` | every invoice's `final_output` plus the whole ERP register | 300k + 600k rows | read |
| `dupaudit.py:162-178` | all pairs within each (vendor, amount) group, three loops, `_days` parses both dates for each pair | one vendor with 2,000 same-amount bills: **4.8 s** alone | verified (`p1.py`) |
| `erp/search.py:209-213` | the quick search scores **every ERP register row** in Python on every search, then one summary query per hit (`_ours` :190) | 100k rows: **1.1 s**; 600k: ~6.6 s per search | verified (`p1.py`) |
| `erp/jde.py:1139-1144` `prior_records` | `search_rows(exported=True)` (every exported invoice) + `erp_register()` (the whole register), for `jde_check` (making a JDE batch, `web/pages/exports.py:147-153`), `jde_left_out` :106 and the dry run text :418 | ~300k + 600k dicts per click | read |
| `erp/spend.py:196-284` `workbook` ("all data" download) | every invoice's `ai_output` + `final_output`, openpyxl **not write-only**, Invoices / Lines / Posting sheets | millions of rows: past Excel's 1,048,576 rows a sheet and tens of GB of openpyxl cells | read |
| `erp/accruals.py:76-88` `_received_not_invoiced` | for every open PO with receipts: `purchase_order` + `po_invoices` (N+1) + `vendor_invoices_without_po` (full coding JSON of the vendor's invoices) + `billed_by_line` (similarity of every line to every PO line) | thousands of synced JDE POs | read |
| `coding/taxreturn.py:429, 450-452` | with JD Edwards in use, one `invoice_columns` query and JSON parse per self-assessment row (`doc()` is evaluated before `accrues_use_tax` checks the mode) | thousands per quarter | read |
| `erp/exports.py` `build` | one batch, in memory | fine at batch sizes (hundreds) | read |
| Good: `taxreturn.build` :199, `accruals._not_in_erp` :171, `insights`, `directory`, `recurring`, `search._candidates` use the invoice summary | | | read |

### 2b. JDE sync: full every night, a hard 100,000-row ceiling

* Every part is a **full read** every night: `sync_suppliers` calls `client.rows("suppliers")` with no `changed_since`
  (`jde_sync.py:84`) although the mapping has the input (`jde_orchestrations.json`, docs/JDE_ORCHESTRATIONS.md:71
  "AP Coder sends blank today"); vouchers are read for the last `voucher_days` = 730 days every night
  (`jde_sync.py:252-260`, `jde_ais.py:154`).
* Paging: `AisClient.rows` :718-735, 500 rows a page (`page_size`), `max_pages=200`: **more than 100,000 rows raises
  AisError** and the part fails. Verified (`p1.py`): "APC_Vouchers_Read gave more than 200 pages". At 25k invoices a
  month, 730 days of F0411 is ~600k vouchers: **the nightly voucher sync fails every night at target volume**, and so
  does an F0901 of more than 100k accounts (the GL read returns every BU.Object.Sub row and dedupes in Python,
  `jde_sync.py:142-149`).
* No checkpoint or resume: a failure at page 150 starts again next night (`sync` :267-302); `due` retries hourly
  (:332-345).
* Time: one HTTP request per page, each a new TCP + TLS connection (`jde_ais.py:521-535, 578-579`). Suppliers 30k = 60
  pages, open PO lines 20-50k = 40-100 pages, vouchers 1,200 pages if the cap were raised: tens of minutes to hours
  depending on AIS (a few seconds per data request is common).
* Writing it: `sync_suppliers` updates `erp_bank`/`erp_hold` **one row at a time by `erp_id`, and `vendors` has no
  index on `erp_id`** (`jde_sync.py:111-114`, table `core/store/_base.py:139-141`): measured 1.7 ms a row at 30k
  vendors, **~52 s for 30k, inside one write transaction** (`p4.py`). During "Sync now" in the day every other writer
  (approvals of 300 people) waits, and fails after the 30 s busy timeout (suspected: same transaction model as
  `_base.py:949-975`).
* `sync_purchase_orders` reads each PO's lines before and after (`_po_lines` :182, :214-217) and every synced open PO
  again (:220): 3-4 queries a PO.
* Vouchers are only ever upserted (`import_erp_register` `core/store/invoices.py:172-215`, `replace_all=False`): a
  voucher voided in JDE stays in the register forever (the read leaves voided vouchers out, it does not delete).

### 2c. Posting batches of hundreds of invoices

* One orchestration call per voucher, sequential (`jde_post._run` :714-739), in a job thread of a web worker
  (`web/pages/exports.py:597-650`, a single progress step "Posting the vouchers…").
* Route (a) of the spec runs **R04110ZA for each call** (docs/JDE_ORCHESTRATIONS.md:165-175): a UBE per voucher in
  proof (the dry run sends every voucher in PROOF too, `jde_post.py:488-499`) and again in final. A UBE often takes
  tens of seconds; the default request timeout is 30 s (`jde_ais.py:67`).
* **One late answer stops the whole run**: any AisError that is not JDE's own refusal (a TIMEOUT included) closes the
  client and queues the rest (`jde_post.py:730-734`). Verified (`p6.py`): two vouchers, the first answered late →
  `{'posted': 0, 'unknown': 1, 'queued': 1}`. The queue then runs every 5 minutes (`jde_sync.Background` every=300
  :464, `RETRY_MINUTES` :64), looks each row up first, sends one, and stops again at the next late answer: if
  R04110ZA routinely exceeds the timeout, **about one voucher per 5 minutes: 500 vouchers ≈ 40 hours**.
* Each R04110ZA run makes its own JDE batch (ICU): **one pending JDE batch per voucher for AP to approve and post**
  (suspected: depends on the orchestration; the spec says "for that batch (VLEDUS + VLEDBT)", and every call sends
  the same EDBT, so a later call's R04110ZA would also pick up any Z-rows a previous call left).
* Before any send, `payloads()` prepares the whole batch (`jde_post.py:133-157`), again in `post` :691 and per batch in
  `retry_queue` :751. With a large account mapping that preparation is slow (§3).

### 2d. AIS timeouts, retries, idempotency

* Reads: retried twice on connection failures, timeouts and 502/503/504 (`jde_ais.py:590-611`, pause 1 s then 2 s,
  `time.sleep` in the request thread).
* Voucher create: `idempotent=False`; a request that may have reached JDE is never repeated by the client (timeout →
  `sent=True` :573-574; 502/504 → sent :603-607; 503 retried as refused before processing). The row goes UNKNOWN,
  is looked up by its key (`APC_Voucher_Status`) before anything else, and is sent again only when JDE says it has
  none (`_deliver` :592-601, `_lookup` :574-583). Well tested (`tests/erp/test_jde_post_safety.py`).
* A double post is still possible only if the orchestration loses its memory of the key: route (a) keys on the
  F0411Z1 rows of VLEDUS/VLEDBT/VLEDTN (docs :198-203). If the Z-tables are purged (R0411Z1P) or the key isn't kept
  with the voucher, a resend after a purge would post twice, stopped only by JDE's duplicate-invoice edit if set to
  H (suspected; acceptance test needed with the JDE team).
* Token: an expired token (HTTP 444) is asked again once per call (`jde_ais.py:690-701`). Tested for a read only
  (`tests/erp/test_jde_ais.py:50-57`). If the AIS release answers 401/403 instead, `_failure` gives DENIED, the voucher
  is queued as never processed (`_failed_send` :633-638) and the run stops for 5 minutes (read).

### 2e. Several people exporting or bulk-approving at once

* Export batches: `create_export_batch` takes the write lock before reading (`BEGIN IMMEDIATE`,
  `core/store/purchasing.py:231`) and marks each invoice only if `export_batch IS NULL`: never one invoice in two
  batches; batch numbers are the table's AUTOINCREMENT id, unique. Read (sound).
* Posting the same batch twice at once: the go-ahead is a compare-and-set on `rev` (`jde_post.py:706-709`) and the
  claim another (`_claim` :223-240); the queue skips rows being sent (`_fresh_sending` :377). Read (sound), threaded
  tests exist for undo vs post (`tests/erp/test_jde_post_safety.py:258-260`).
* Bulk approval: sequential, `approve_invoice` refuses an invoice someone else acted on (`bulk.py:272-277`, tested
  `tests/erp/test_exports_bulk.py:99`). Cost per invoice is the whole `finalise_coding` (`ai/pipeline.py:146-224`):
  feedback history, duplicates, `vendor_ids()` + `jde.load()` (:203-204), `vendor_findings` with the live JDE call,
  PO match, and on the server `moved_off_hold` (`bulk.py:259`). With 3,000 held vendors `held_vendor` alone is 2.8 s
  an invoice (§3).
* `issues_today` (`bulk.py:123-154`) re-runs `finalise_coding` for every queued invoice whose checks were made under
  other settings, the first time the queue is opened after any settings change ("about 5 ms an invoice", :115):
  realistic cost is far higher with JDE on (live AIS call per new supplier, `vendor_ids()` 160 ms), so a queue of
  thousands stalls the page once per settings change.

### 2f. Checks that query history per invoice

* Duplicate invoice: `find_duplicates` (`core/store/invoices.py:133-160`) reads every invoice of the vendor (indexed
  `invoices_vendor`) and compares normalised numbers in Python: fine per vendor; big vendors (thousands a year) read
  thousands of rows per check.
* `duplicates_elsewhere` (:283-298): by `grand_total` (indexed `invoices_total`): common amounts ($100.00) return
  many rows.
* ERP register `in_erp` (:265-281): indexed by (vendor_key, number_key). Keyed by vendor **name**, not AN8.
* `vendor_invoices` (`core/store/vendors.py:408-440`): 5 `json_extract` over each of the vendor's invoices'
  `final_output`, memoised per session only.
* `vendor_findings` loads the **whole vendor master** for a vendor not found by name (`coding/vendors.py:356`,
  `master_vendors()` 149 ms at 30k, `p2.py`).
* `learned_codes` (`line_tax.py:1017`) compares each line with every feedback row of the vendor (similarity), and
  `po.match_invoice` runs `po_lines_billed` (every line × every PO line, SequenceMatcher) three times over every other
  invoice on the PO (`po.py:306-307`, `_left_to_expense` :375): blanket POs with hundreds of invoices get slow (read).

---------------------------------------------------------------------------------------------------------------------

## 3. Efficiency hotspots (with evidence)

1. **`held_vendor`: N+1 over every held vendor, on every review screen render on the server.** `coding/vendors.py:503-512`
   calls `store.vendor_invoices(key)` for each held vendor before even checking the GST/bank; run twice per render
   (`moved_off_hold` :521-531, `web/invoice_view.py:1698-1699`) and per invoice in bulk (`bulk.py:259`). Measured
   **2.8 s with 3,000 held vendors** (`p2.py`). The JDE supplier sync marks every vendor with a hold code
   (`jde_sync.py:103-114`); inactive suppliers usually carry one.
2. **`jde.account_for` scans the account mapping list per line** (`jde.py:427-428`), and `sync_gl_accounts` adds a
   mapping row for every subsidiary account in F0901 (:153-161). Measured: **5,000 lookups (a 500-invoice batch of
   10 lines) with a 20k mapping: 17.4 s** (`p1.py`). Paid at every review edit (`voucher_findings`), batch creation,
   dry run, post, retry and ZIP download.
3. **`jde.load()` parses the whole JDE settings JSON on every call** (`jde.py:315-316`; `from_json` :247-295 runs
   `refresh_vendor_key` on every AN8 override, :283). 20k mapping + 10k overrides = 2.4 MB, **50 ms** (`p1.py`);
   called per `finalise_coding`, per live check, per page.
4. **`store.vendor_ids()` rebuilt per call**: 30k vendors → 90k keys, **160 ms** (`p2.py`), called per
   `finalise_coding` (`ai/pipeline.py:204`), per live check (`jde_sync.py:385`), per payload build.
5. **Live JDE check**: a new AIS sign-in, the read and a logout for each supplier not cached (3 HTTP requests, `p2.py`),
   every page of the supplier's vouchers **with no date limit** (`jde_sync.py:366-373`, up to 40 pages of 500, 5 s
   each), per worker process cache (`_CACHE` :354). Hundreds of AIS sessions an hour from 300 people.
6. **Quick search scans the ERP register** (1.1 s / 100k rows, §2a); **dupaudit pairs** (4.8 s for one vendor, §2a).
7. **Supplier sync UPDATE loop without an `erp_id` index** (~52 s for 30k, one write transaction, §2b).
8. **The hub reads, decodes and fingerprints everything on every run** (§2a), in the server's background process,
   beside the page reader and the JDE queue (`web/server.py:510-520`).
9. **Subset-sum solver memory**: 40 lines → two 2^20-element int64 array pairs (~32 MB) per tax per invoice
   (`line_tax.py:507-545`); fine at 1,250 a day, noticeable with 8 workers re-checking queues.
10. **One AIS connection per request** (`jde_ais.py:521-579`): TLS handshake per page and per voucher; minor next to JDE
    time, cheap to fix with keep-alive.

---------------------------------------------------------------------------------------------------------------------

## 4. Cleanliness

**Long functions** (lines): `jde._prepare` 170 (`jde.py:708`), `line_tax._prefill` 167 (:685), `validate_coding` 159
(`validation.py:103`), `jde.voucher_rows` 147 (`jde.py:935`, three numbering modes inline), `vendor_findings` 131
(`vendors.py:334`), `insights.compute` 127, `jde.dry_run` 117 (:1647), `jde_worksheet.build` 101 / `read` 94,
`hub/run._run` 92, `spend.workbook` 90, `statements.reconcile` 87, `jde.readme` 87.

**`jde.py` is 1,856 lines with eight jobs**: settings and their JSON, Z-file formats, supplier and account lookup, one
voucher's checks, its rows in three layouts, batch checks and duplicates, the review screen's findings and header view,
the files/README/ZIP, the dry run text, the mapping import. Natural seams: settings, voucher (prepare + rows), files.

**The tax logic is worked out in several places, with different rounding and tolerances:**

* expected tax per tax line: `tax._tax_line_amount` (`tax.py:450-490`: `round(taxable*rate, places)`, within one unit
  ok, warning within `unit*(n/2+1)`) and `taxmath._tax_row` (`taxmath.py:127-131`: ok within 1.5 units or
  `unit*(flagged/2+1)`); the "which line is the difference" logic twice (`tax.py:470-489` and `taxmath.py:159-201`).
* lines vs subtotal / subtotal + tax vs total: `validation.py:199-208` (max(0.02, 0.01·n) and 0.02) vs
  `taxmath.py:84, 239` (< 0.015). Verified (`p3.py`): a 0.02 gap passes `TOTAL_MISMATCH` and fails the Tax check
  (`POSTING_UNBALANCED` still catches it, so this is two checks disagreeing, not a pass).
* the regime of a province: `tax.REGIME`/`regime_on` (:66, :283) vs `jde._area_types` (`jde.py:643-652`) and
  `line_tax._regime` (:227).
* self-assessed base: `taxreturn.self_assessed_base` (:455-473) vs `taxmath._self_assess_rows` (:204-232) vs
  `tax._provincial_tax_due` (:661-680).
* splitting cents: `tax._allocate` (floats, Python `round`, :889-903) vs `jde._split` (integer cents, :378-389) vs
  `jde._claimed_shares` (:1084-1112, `tax_shares` again). Verified (`p3.py`): 0.05 over two equal lines →
  `_allocate` [0.02, 0.03], `_split` [0.03, 0.02]: the GL distribution and the JDE pay items put the odd cent on
  different lines (totals agree).
* PST spread onto tip lines in `build_gl_distribution` (`tax.py:932-936`) but not in `tax_shares` (:1017-1018).

**Boundaries coding/ ↔ erp/** are two-way: `coding/vendors.py:534-548` calls `erp.jde_sync.live_findings` (an AIS call
from a vendor check), `coding/taxreturn.py:376-394` imports `erp.jde`; `erp/jde.py` imports `coding.line_tax`,
`coding.tax`, `coding.terms`, `coding.vendors`, and `erp.exports._vendor_id` (a private name, `jde.py:411`);
`erp/accruals.py` uses `coding.po`; `erp/jde_sync.py` uses `coding.po` and `coding.vendors`. Function-level imports
hide the cycle.

**Dead or test-only code**: `bulk.clean_candidates` (`bulk.py:157`) has no caller in the app (20 in tests); small
helpers repeated: `_date` ×5 (`vendors.py:201`, `terms.py:293`, `recurring.py:50`, `dupaudit.py:95`, `jde.py:392`),
`_text` ×4, `_money` ×4, `_norm` ×2, `_plain` ×3 with three different meanings (`rules.py:23` text fold, `bulk.py:41`
message, `jde.py:1127` number).

**Read but unused data**: the business unit's company (`company` field, mapping) is dropped by
`sync_business_units` (`jde_sync.py:170-175`); the PO's `order_company`/`order_type` are dropped and POs are keyed by
number alone (`po.po_key` `po.py:82-86`, `jde_sync.py:203-213`); the supplier's currency is read, "not used yet".

---------------------------------------------------------------------------------------------------------------------

## 5. Correctness risks in the money paths

| # | Risk | Where | Status |
| --- | --- | --- | --- |
| R1 | **Duplicate payments not flagged at scale**: the nightly voucher sync fails past 100k rows (600k at target), so the register fallback goes stale; the live check reads a supplier's whole history, and one supplier with more than 40 pages (20,000 vouchers) makes the check answer JDE_NOT_REACHED **for every supplier for a minute** (`jde_sync.py:393-400`). | `jde_ais.py:718-735`, `jde_sync.py:252-260, 366-403` | **verified** (`p1.py` cap; `p5.py`: after a 20,001-voucher supplier, a small supplier's real duplicate came back JDE_NOT_REACHED) |
| R2 | **Controls report silently incomplete**: newest 1,000,000 events only, then filtered by date; an older period shows "None" for overrides and setup changes. Sibling of B-1251 (insights' 100,000 cap). | `controls.py:68` | **verified** (`p5.py`, cap scaled to 50: the March setup change vanished) |
| R3 | **A vendor put on hold in JDE after approval is still posted**: neither `jde.validate`/`_prepare` nor `jde_post.dry_run` re-checks holds; and every voucher is sent with the configured pay status, `A` by default (`JdeSettings.pay_status` `jde.py:223`, `VLPST` :967), which may override the supplier's hold default in R04110ZA. | `jde.py:708-877, 967` | read (no re-check); suspected (VLPST overriding the hold: confirm with the JDE team) |
| R4 | **Accruals understate at month end when the API is used**: an invoice in an export batch counts as "in the ERP" (`accruals._not_in_erp` `accruals.py:177-179`) even when its voucher is queued, unknown, refused or the JDE batch is still pending. | `accruals.py:171-229` | read |
| R5 | **No fiscal period logic anywhere** (`grep -i "fiscal\|period close\|PBCO"`: nothing). G/L date = invoice date by default (`jde.py:948, 961`): October invoices processed in November after the FY close (31 Oct) are refused by R04110ZA, or land in the prior year if it is still open. **The pilot's DV with FY2026 data**: today is 2026-11-05, so every invoice dated from 1 Nov 2026 has a G/L date in FY2027, which the DV may not have open. | `jde.py:106-107, 948` | read; the DV part suspected (check the DV's date pattern and current period) |
| R6 | **Z-table keys collide across installations and after a restore**: the idempotency key carries the installation id, but VLEDBT = prefix + batch id and VLEDTN = "APC" + invoice id do not (`jde._keys` `jde.py:898-906`). Two data folders posting to one JDE (pilot laptop + server), or a restored database handing out batch numbers again, give the same EDUS/EDBT/EDTN; route (a) looks the key up by those rows (docs :198-203) and answers EXISTS for the other voucher (AP Coder then fails it as "not this invoice", locked for a person), and a Z-file load of the batch hits duplicate keys. Tested only for the idempotency key (`test_a_second_laptop_never_makes_the_first_ones_key`). | `jde.py:898-906` | read |
| R7 | **Voided vouchers stay in the register** forever (`import_erp_register` upserts only): false DUPLICATE_IN_ERP for a bill voided and re-entered. And the register is keyed by vendor **name** (`invoices.py:183-186`): a supplier renamed in JDE no longer matches its own old vouchers. | `jde_sync.py:252-260`, `core/store/invoices.py:172-215` | read |
| R8 | **Multi-company not supported**: one VLCO for every voucher (`JdeSettings.company` `jde.py:206`), one header/default BU, one tax setup, one account mapping; the GL read with company blank merges every company's object.sub into one list (first description wins, `jde_sync.py:147`); POs keyed by number only (same DOCO in two companies collide, `po.py:82-86`). | `jde.py:205-241`, `jde_sync.py:136-179, 193-213` | read |
| R9 | **PO line numbers**: `int(float(line_number))` (`jde_sync.py:199-202`): JDE lines 1.000 and 1.500 (split lines, LNID) both become 1. | `jde_sync.py:199-202` | suspected (depends on how the orchestration writes LNID; the spec says "as shown, 1.000") |
| R10 | **Tax rounding**: Python `round()` on floats is half-even on binary values: 2.50 × 5% → 0.12 (half-up 0.13), 0.30 × 5% → 0.01 (0.02) (`p3.py`). In the checks the one-cent tolerance absorbs it; it lands in money in the self-assessment estimate AP books from (`taxreturn.py:431` `round(base * rate, 2)`), `taxmath` self-assess rows (:220) and the discount amount (`terms.py:360, 363`). The JDE files use Decimal half-up (`jde.py:360-375`): consistent there. | listed | verified (the rounding); impact cent-level |
| R11 | **Currency**: VLCRR comes from AP Coder's own rates in Settings → Review (`jde._rate` :909-915, `store.fx_rates()`), typed by hand and possibly stale, overriding E1's exchange-rate table; listed as unconfirmed (`jde.py:145`). Foreign tax in the ITC report is only flagged, not converted (`taxreturn.py:234-235`). | `jde.py:909-915, 957, 963` | read |
| R12 | **Credit notes**: handled consistently: sign kept in duplicates (`jde.py:1171-1176`, `in_erp`, `live_findings` :407-416), due date = invoice date and no terms code (`jde.py:518-519, 534-535`), credit document type (:918-921), `_split` exact for negatives (truncation toward zero, remainder to the largest). No defect found. | | read (+ `p3.py` split of negatives) |
| R13 | **Batch numbering when two people export at once**: safe (`BEGIN IMMEDIATE`, re-check `export_batch IS NULL`). No defect; only R6 (numbers reused after a restore / across installations). | `purchasing.py:226-268` | read |
| R14 | Hub lock: a run longer than 2 h (`LOCK_STALE_SECONDS` `hub/run.py:54`, :240-242) has its lock deleted by the next process, so two runs overlap; with a year of data a full rebuild could exceed 2 h. | `hub/run.py:235-262` | suspected |

---------------------------------------------------------------------------------------------------------------------

## 6. Test gaps (`tests/coding/`, `tests/erp/`)

Strong today: 31 coding test files and 31 erp test files; JDE posting safety is thorough (EXISTS for another voucher,
lost answers, 502/504, a stop mid-send, undo during the queue, restore, file downloaded), sync safety (mass close,
holds, the live check over pages), AIS client (paging, expired token on a read, busy, timeouts, TLS, address rules).

Missing:

* **Volume**: nothing exercises the paging cap (`max_pages`), a sync of 100k+ rows, a batch of hundreds of vouchers,
  a 20k account mapping, thousands of held vendors, a register of 600k rows, a million events. `tests/erp/test_invoice_summary.py`
  checks the summary's equivalence on a small "year" fixture, and one cap test (B-1251, `:345`); there is no
  performance budget for any report or check.
* **Token expiry during a batch post** (444 on `voucher_create`), and AIS answering 401/403 for an expired token.
* **A slow voucher in a batch** (the run stopping; `p6.py` shows it).
* **The live check with a supplier beyond 40 pages** (R1; `p5.py` is the reproduction).
* **Voided vouchers** and supplier renames in the register (R7).
* **Two installations / a restore and the Z-table keys** (R6).
* **Holds placed in JDE after approval** (R3); **fiscal periods / G/L date in a closed period** (R5): none exist.
* **Multi-company**: none (R8).
* **Accruals with queued or refused API vouchers** at month end (R4).
* **Concurrent sync and approvals** (write-lock duration of the supplier sync).
* The controls report's event cap (R2; insights has `test_insights_counts_every_approval_past_a_hundred_thousand`,
  controls has nothing alike: `tests/ops/test_controls_watch.py:20-70` uses a handful of events).

---------------------------------------------------------------------------------------------------------------------

## 7. Scenarios people may not have thought of

* **AIS down for a day.** Reviews carry on (live check degrades to INFO JDE_NOT_REACHED and the last sync's register,
  `jde_sync.py:431-435`); posting queues every voucher never sent, retried every 5 then 15 minutes forever
  (`_later` :554-559); the nightly sync fails and is tried hourly. Gaps: if the sync had been failing anyway (R1),
  duplicate checks rest on a stale register; at month end the queued vouchers are not accrued (R4); after the outage
  the queue drains at one voucher per call, stopping at the first late answer (§2c); the batch ZIP is the fallback
  but, once downloaded, the queue stops and waits for a person's word (D-134, `jde_post.py:349-357`).
* **A vendor changed in JDE after coding.** The AN8 is resolved again at each export, dry run and post from the vendor
  master by name or GST/HST number (`jde.resolve_an8` :408-418), so a rename or merge is picked up after the sync
  (or the invoice is held with JDE_NO_AN8 if the name no longer matches and the GST/HST number isn't there). A hold
  or a bank change in JDE after approval is not re-checked before posting (R3); `VENDOR_BANK_NOT_JDE` runs only on
  the review screen (`vendors.py:551-566`).
* **Fiscal year rollover (31 Oct).** No period awareness (R5): a wave of R04110ZA refusals in the first days of
  November for October invoices, each FAILED one at a time with JDE's words; the only lever is the global G/L date
  switch (invoice date / export day, `jde.py:106-107`), which then moves every invoice. The accruals "late batch"
  rule follows that switch (`accruals.late_after` :162-168). For the pilot: FY2026 data in DV, today in FY2027.
* **Period close.** AP Coder doesn't know the open period, doesn't read back whether a pending JDE batch was
  approved and posted, and counts exported-but-unposted vouchers as in the ERP (R4). A cut-off date (no G/L date
  before X) is not settable.
* **A batch half posted.** Handled per voucher (posted / unknown / queued / failed), the ZIP leaves out what JDE has or
  may have (`left_out_of_files` :257-269), undo refused while any is locked (:320-334). Operationally: in JDE that is
  one pending batch per voucher (§2c); in AP Coder the Exports page has no per-voucher progress during the run (one
  job step).
* **The token expiring mid-batch.** 444 → sign in again once per call (fine); 401/403 instead → the run stops and the
  rest wait 5 minutes (read, untested for vouchers).
* **Duplicate invoice numbers across vendors.** Handled: duplicates are by vendor (key or AN8) and number with the
  sign; same number + total under another vendor is a warning (`DUPLICATE_OTHER_VENDOR`). The live check for a vendor
  without an AN8 asks JDE by invoice number across all suppliers (`jde_sync.py:373`): a common number ("1001") can
  hit the 40-page cap and switch the live check off for a minute (R1).
* **Many companies in one JDE (the company districts).** Not supported today (R8): one company per AP Coder database, so
  either one installation per company (each with its own vendor master, mapping and batch numbers, and then R6) or
  per-invoice company derived from the BU, which needs batches split per company.
* Also: **two AP Coder data folders against one JDE DV** (a pilot tester's laptop and the server) collide on Z-table
  keys (R6); **a database restore** reuses batch and invoice numbers in EDBT/EDTN (R6); **a big blanket PO** with
  hundreds of invoices slows its review screen (§2f); **"Sync now" at 10 am** holds the write lock ~1 minute (§2b).

---------------------------------------------------------------------------------------------------------------------

## 8. Recommended goal items

Sizes: S ≈ up to half a day, M ≈ a day, L ≈ two days or more. Order is my priority for a pilot on JDE DV.

| # | Item | Benefit | Size | Proof it worked |
| --- | --- | --- | --- | --- |
| G1 | **Syncs that finish at target volume**: incremental reads (suppliers with `changed_since`; vouchers since the last good sync minus an overlap, a full 730-day reconcile weekly that also drops voided vouchers); import page by page instead of collecting all rows; no 200-page ceiling for the nightly reads (a time budget instead); an index on `vendors(erp_id)` and the hold/bank update as one `executemany` in short transactions. | The duplicate check and the vendor master stay current; no daily failure; no minute-long write lock. | M–L | A FakeAis scale test (slow mark) with 30k suppliers, 150k accounts, 600k vouchers: the nightly sync succeeds; the second night's orchestration calls count only changed rows; no write transaction over 1 s (time `with store._conn()`); voided voucher removed from the register. |
| G2 | **Live duplicate check that holds up**: ask JDE by supplier and a date window (or supplier + number), keep one signed-in AIS client per worker (re-used token, logout at stop), never mark JDE "down" for everyone on a "too many pages" answer, register fallback by AN8 as well as name. | Duplicate payments caught for big suppliers; far fewer AIS sessions. | M | `p5.py` turned into a regression test (small supplier's duplicate found after a 20k-voucher supplier); token requests over 100 live checks ≤ 2. This is a bug fix: BUGS.md line. |
| G3 | **Posting throughput**: a separate, longer voucher timeout; keep going after a TIMEOUT/unknown voucher (stop only when JDE is unreachable before sending), per-voucher progress in the job; spec and support a batch-level orchestration (one R04110ZA run, one pending JDE batch per AP Coder batch) with the per-voucher route kept. | A 500-voucher batch posts in one run instead of ~40 hours; AP approves one JDE batch, not 500. | M (client side) + L (orchestration spec with the JDE team) | FakeAis with 500 vouchers, 2 s latency, every 20th answered late: one `post` run ends with 475 posted / 25 unknown, the queue resolves the 25 without a second voucher; `tests/erp/test_jde_post_safety.py` unchanged and green. |
| G4 | **Fiscal periods and cut-off**: read the company's open period (F0010/F0008, a read orchestration or Settings), a check before the dry run/post (G/L date in a closed period: a new check code with its `ops/help.py` entry), an optional rule "invoice date, else the first day of the open period"; accruals count batch vouchers not POSTED through the API as not in the ERP; read back the JDE batch status. | No November wave of refusals; month-end accruals right with the API; the DV pilot works in FY2027. | M | Tests with a fake calendar (October closed, an Oct 28 invoice posted Nov 10: held with plain words or dated per the rule); an accruals test with a queued voucher at month end. |
| G5 | **Review screen and bulk cost**: cache `vendor_ids()` and `jde.load()` per data version; index the account mapping by (GL, cost center) in `account_for`; `held_vendor` as one or two queries (GST/bank of held vendors' approved invoices from the summary) instead of N+1. | A review edit and a bulk approval cost milliseconds, not seconds, for 300 people on 8 workers. | S–M | Query count (sqlite `set_trace_callback`) of `moved_off_hold` with 3,000 held vendors ≤ 3; `finalise_coding` with 30k vendors and a 20k mapping under 100 ms; `python scripts/same_behaviour.py` equal. |
| G6 | **Reports without whole-table scans**: controls by SQL date range (bug fix, sibling of B-1251); dupaudit from the summary, a sliding date window instead of all pairs; the quick search's register part by index (number key, vendor words); `prior_records` as lookups of the batch's own numbers; the "all data" workbook write-only with a date range. | Audit, search, batch creation and the data download work on a year of data. | M | A synthetic "year" bench (300k invoices, 1M+ events, 600k register rows): each report under a set time and memory (`tracemalloc`); results equal to today's on the small fixtures (`tests/erp/test_invoice_summary.py`, `same_behaviour.py`). |
| G7 | **Finance hub incremental for real**: read only invoices changed since the watermark (events since the watermark name them), activity from the watermark, the gateway copy updated in place; a lock that can't go stale under a running run. | The hub runs every hour on a year of data in seconds, not GBs. | M | On the year bench, a second run reads < 1% of rows and sends the same files as a full run would for the same changes (existing hub tests green). |
| G8 | **Hold, bank and Z-key safety before posting**: re-check JDE holds (latest sync) in the dry run and before the go-ahead; confirm VLPST with the JDE team (blank to let JDE default the hold?); put the installation (or a site code) into VLEDBT/VLEDTN so two data folders and a restore never collide. | No voucher for a held supplier; no Z-table collisions in a shared DV. | S | Tests: a vendor held in JDE after approval is refused by the dry run; two data folders give different EDBT/EDTN. |
| G9 | **One tax arithmetic**: one Decimal half-up helper and one tolerance table for `check_taxes`, `math_check`, the self-assessment and the splits (`_allocate`/`_split`). | The review screen's Tax check and the findings never disagree; cents placed the same in the GL lines and the pay items. | S–M | A property test over generated invoices (credits, yen, meals): the three agree; the bench `coding`/`run --quick` unchanged; `same_behaviour.py`. |
| G10 | **Multi-company** (design + first step): company per invoice from its BU (MCCO, already read), batches split per company, PO keys with company and type. | the company districts in one JDE. | L | FakeAis with two companies: vouchers carry the right VLCO; the same PO number in two companies stays two POs. Likely beyond 36 hours: design note (D-NN) plus the BU → company step. |
| G11 | **Missing failure-mode tests**: 444 and 401/403 during `voucher_create`, the paging cap, a sync interrupted mid-part, a slow voucher mid-batch. | Locks in G1–G3. | S | New tests fail on today's code where behaviour changes, pass after. |

Suggested 36-hour cut: G1, G2, G3 (client side), G4, G5, G8, G11, plus the controls fix from G6. Defer G7, G9, G10
(design note for G10).
