# Bug log

Every bug found by a bug sweep: what went wrong, how to reproduce it, the fix, the test that keeps it fixed, and the lesson
("don't do this again"). Each lesson is also a line under Gotchas in the CLAUDE.md of the folder it belongs to.
A suspicion that could not be reproduced is logged as "not reproduced" and left as it is, never fixed blindly.

How to add one: next number, its section, all fields; the test first (it fails), then the fix.

## Sweep 1 (before the codebase map), October 2026

Each section was reviewed, then each finding was checked again, given a failing test, and fixed. Line numbers are those
of the code at the time of the sweep.

| Section | Found | Reproduced and fixed | Not reproduced / left |
|---|---|---|---|
| Store and config | 16 | 11 (B-01 to B-11) | 5 (N-01 to N-05) |
| Tax and coding rules | 11 | 9 (B-12 to B-20) | 2 (N-06, N-07) |
| Exports, ERP and reports | 12 | 10 (B-21 to B-30) | 2 (N-08, N-09) |
| AI and readers | 16 | 11 (B-31 to B-41) | 5 (N-10 to N-14) |
| Document capture | 13 | 9 (B-42 to B-50) | 4 (N-15 to N-18) |
| Web routes | 14 | 6 (B-51 to B-56) | 8 (N-19 to N-26) |
| Front end | 4 | 4 (B-57 to B-60) | 0 |
| Intake, CLI and launch | 17 | 12 (B-61 to B-72) | 5 (N-27 to N-31) |
| **Total** | **103** | **72** | **31** |

### Store and config

#### B-01 · A header that spells the standard name is not renamed · medium
- **Where:** `ap_coder/reference_data.py:63-77` (`_canonical_headers`)
- **Symptom:** a GL file with headers "GL Code" and "Active" is refused with "missing required column 'gl_code'"; an Active=no column is ignored, so retired codes reach the prompt.
- **Cause:** when a header normalised exactly to the standard name, the loop skipped it and kept the original header text.
- **Reproduce:** CSV `GL Code,Description,Active`, then `load_table`.
- **Reproduced:** yes (ValueError "missing required column 'gl_code'").
- **Fix:** that header is now renamed to the standard name too.
- **Test:** `tests/test_reference_data.py::test_header_that_spells_the_standard_name_is_renamed`
- **Lesson:** Don't skip the rename step for the exact standard name.

#### B-02 · Doubled quotes in a CSV cell are misread · medium-low
- **Where:** `ap_coder/csvio.py:13-23` (`_dialect`)
- **Symptom:** `"Acme ""Best"" Inc"` is read as `Acme "Best"" Inc"`.
- **Cause:** the sniffer looked at the header line only and guessed doublequote=False.
- **Reproduce:** a CSV with a doubled quote in a quoted cell, read with `read_csv_rows`.
- **Reproduced:** yes (got `'Acme "Best"" Inc"'`).
- **Fix:** only the delimiter is taken from the sniffer; quoting stays Excel's.
- **Test:** `tests/test_real_world.py::test_doubled_quotes_in_a_csv_cell_are_read_as_one_quote`
- **Lesson:** Don't use a sniffed dialect's quoting rules after sniffing one line.

#### B-03 · A re-review without a result erases the last result · medium-low
- **Where:** `ap_coder/store.py:2599` (`finish_verification`)
- **Symptom:** a re-review that ends failed, waiting or skipped erases the previous result and its disputed-field issues.
- **Cause:** `CASE WHEN ? IS NOT NULL` was given a Python bool, which is never NULL.
- **Reproduce:** finish "done" with a result, queue again, finish "failed".
- **Reproduced:** yes (the result became None).
- **Fix:** `result_json = COALESCE(?, result_json)`.
- **Test:** `tests/test_verifier.py::test_a_review_that_ends_without_a_result_keeps_the_last_result`
- **Lesson:** Don't pass a Python bool to IS NULL.

#### B-04 · An accented reviewer name never counts approvals today · low
- **Where:** `ap_coder/store.py:1203-1225` (`approved_since`)
- **Symptom:** reviewer "Élise Tremblay" always sees 0 approved today.
- **Cause:** SQLite `lower()` only changes ASCII letters, but the name was lowered in Python.
- **Reproduce:** approve as "Élise Tremblay", then `approved_since(today, "élise tremblay")`.
- **Reproduced:** yes (0 instead of 1).
- **Fix:** names are compared in Python with casefold, still in one query.
- **Test:** `tests/test_end_to_end_sweep_fixes.py::test_approved_today_matches_accented_names_without_case`
- **Lesson:** Don't compare non-ASCII text with SQLite `lower()`.

#### B-05 · A re-created database can't take the write lock · low
- **Where:** `ap_coder/store.py:478` (`_open`)
- **Symptom:** the first BEGIN IMMEDIATE on a re-created database fails.
- **Cause:** the upgrade step's DELETE opened a transaction that was never committed.
- **Reproduce:** delete the database file, then `delete_invoice(1, keep_approved=True)`.
- **Reproduced:** yes ("cannot start a transaction within a transaction").
- **Fix:** `conn.commit()` after the upgrade step.
- **Test:** `tests/test_store_session.py::test_a_database_made_again_can_take_the_write_lock_at_once`
- **Lesson:** Don't leave a setup step's changes uncommitted on a connection handed to callers.

#### B-06 · Saved settings are expanded or split · low
- **Where:** `ap_coder/config.py:204`, `ap_coder/envfile.py:58-64`
- **Symptom:** a saved secret `pa$$word ${HOME}` comes back as `pa$$word /root`; a value with a line break splits into extra settings.
- **Cause:** `load_dotenv` expanded variables; `write_env` allowed line breaks.
- **Reproduce:** write the key with `write_env`, then `Settings.from_env`.
- **Reproduced:** yes (got `'pa$$word /root'`; a value with a line break was written).
- **Fix:** the .env is loaded with `interpolate=False`; `write_env` raises ValueError on a line break and writes nothing (see B-08).
- **Test:** `tests/test_envfile_edge_cases.py::test_a_saved_value_with_a_dollar_sign_reads_back_as_typed`, `::test_a_value_with_a_line_break_is_refused`
- **Lesson:** Don't let a settings file expand or split values the user typed.

#### B-07 · A CAD rate can be typed in · minor
- **Where:** `ap_coder/store.py:223` (`parse_fx_rates`)
- **Symptom:** `CAD=1.4` is accepted and scales every CAD limit check.
- **Cause:** CAD, the base currency, was not excluded.
- **Reproduce:** `parse_fx_rates("CAD=1.4, USD=1.37")`.
- **Reproduced:** yes (CAD 1.4).
- **Fix:** a typed CAD rate is skipped.
- **Test:** `tests/test_spend.py::test_a_cad_rate_typed_in_is_ignored`
- **Lesson:** Don't let the base currency's rate be overridden.

#### B-08 · A setting typed with a line break gives an error page · low
- **Where:** `ap_coder/envfile.py:58` (`one_line`), used by `save_env` in `ap_coder/web/pages/settings.py` and `save_settings` in `ap_coder/azure_check.py`
- **Symptom:** once `write_env` refused line breaks (B-06), a setting sent with a line break gave a 500 from the Settings page.
- **Cause:** the refusal was at the file writer, not where the value is typed.
- **Reproduce:** post a Settings value that holds a line break.
- **Reproduced:** yes (noted by the B-06 fix; found while merging, not in a review report).
- **Fix:** `envfile.one_line` makes the value one line in `save_env` and the Azure settings before it is written.
- **Test:** `tests/test_web_settings.py::test_a_setting_typed_with_a_line_break_is_saved_as_one_line`
- **Lesson:** Refuse bad input where it is typed, not with an error page.

#### B-09 · A NaN total skips the second approver · medium
- **Where:** `ap_coder/store.py:1377-1379` (`second_approval_reason`)
- **Symptom:** a NaN total returns "" and the invoice is approved with no second approver.
- **Cause:** NaN compares False with everything, so it is never "over" the limit.
- **Reproduce:** `second_approval_reason({"grand_total": nan})` with a 10000 limit.
- **Reproduced:** yes (for float NaN and the "nan" string; inf already gave the limit message).
- **Fix:** a total that is not a finite number counts as over the limit. The web half is B-52.
- **Test:** `tests/test_second_approval.py::test_a_total_that_is_not_a_number_needs_the_second_approver`
- **Lesson:** Don't trust a `>` comparison on money that may be NaN.

#### B-10 · One database connection per row on the review and export pages · medium (performance)
- **Where:** `ap_coder/web/pages/review.py:121` and `:265`; `ap_coder/web/pages/exports.py` (JDE check, left out, batch file, problem rows, layout preview, dry run, approved PDFs)
- **Symptom:** `/review`, `/review?tab=second` and `/exports?fmt=jde` open more connections as invoices are added.
- **Cause:** the pages called `vendor_terms`, `events` and `get_invoice` inside row loops; each store call opens a connection.
- **Reproduce:** count `sqlite3.connect` calls for each page with 2 and then 12 invoices of each kind.
- **Reproduced:** yes ([27, 24, 28] connections grew to [37, 34, 49]).
- **Fix:** the queue reads all vendor terms once (`all_vendor_terms`), the second-approval tab uses `latest_events`, the export pages use `get_invoices`.
- **Test:** `tests/test_web_review.py::test_the_review_and_export_pages_do_not_ask_the_database_once_per_invoice`
- **Lesson:** Don't call the store inside a row loop.

#### B-11 · List queries fail past SQLite's variable limit · medium
- **Where:** `ap_coder/store.py:54` (`_in_list`), used by every store query that takes a list of ids or keys
- **Symptom:** a query with a long list of ids or keys fails with "too many SQL variables" on older SQLite builds (999 variables).
- **Cause:** each list used one `?` per item.
- **Reproduce:** set the connection's variable limit to 999 and pass more ids than that.
- **Reproduced:** yes (found while merging B-29 and B-30, which fixed two of these queries; not in a review report).
- **Fix:** the lists are bound as one JSON parameter: `column IN (SELECT value FROM json_each(?))`.
- **Test:** `tests/test_exports_bulk.py::test_every_list_query_takes_more_ids_than_sqlite_takes_variables`
- **Lesson:** Don't bind an unbounded list as separate SQL variables.

### Tax and coding rules

#### B-12 · An old rate row left open keeps winning · high
- **Where:** `ap_coder/tax.py:148` (`_in_force`; was `rate_for` at 139)
- **Symptom:** if a new rate row is appended and the old row is left open, the old rate keeps winning.
- **Cause:** `rate_for` returned the first covering row in file order.
- **Reproduce:** add `PST SK 0.07 from 2026-07-01, no end`, then `rate_for("PST","SK",2026-09-01)`.
- **Reproduced:** yes (`0.06 == 0.07` failed).
- **Fix:** the covering row with the latest `effective_from` wins; a province row beats a Canada-wide one on a tie. Overlaps are logged at load. `in_force()` shows one row per tax and province in `to_markdown` and the Tax setup page.
- **Test:** `tests/test_tax.py::test_a_new_rate_row_wins_over_an_old_one_left_open`
- **Lesson:** Don't let file order decide which effective-dated row wins.

#### B-13 · Self-assessment uses its own place of supply · medium
- **Where:** `ap_coder/taxreturn.py:127` (`supply_province`), plus `province()`, `build`, `self_assessment`
- **Symptom:** BC PST is flagged for a BC vendor delivering to our AB site, and missed for a US vendor delivering to our BC site.
- **Cause:** it used ship-to, then the supplier's province, and ignored the site and default provinces in TaxSetup.
- **Reproduce:** set our province to BC and site `CCAB=AB`; approve two invoices with no ship-to.
- **Reproduced:** yes (`[('B-1','BC','PST')] == [('U-1','BC','PST')]` failed).
- **Fix:** `supply_setup` and `supply_province` call `tax.place_of_supply`, the rule of the tax checks. Claims use it for the HST/QST province too.
- **Test:** `tests/test_taxreturn.py::test_self_assessment_uses_the_place_of_supply_of_the_tax_checks`
- **Lesson:** Don't compute place of supply twice with different rules.

#### B-14 · ERP lump-sum lines with qty 0 and price 0 raise PO alarms · medium
- **Where:** `ap_coder/po.py:124`
- **Symptom:** lump-sum PO lines exported as qty 0 / price 0 raise false PO_PRICE_OVER and PO_QTY_OVER.
- **Cause:** `amount_only` was set only when qty and price were blank, not when they were 0.
- **Reproduce:** import qty 0, price 0, total 5,000.
- **Reproduced:** yes (got `(0.0, 0.0, 5000.0, False)`).
- **Fix:** with a non-zero amount, a 0 qty or price counts as blank: qty 1, price = amount, amount_only. An all-zero line stays as it is.
- **Test:** `tests/test_po.py::test_lump_sum_line_exported_with_zero_quantity_and_price_is_amount_only`
- **Lesson:** Don't treat 0 differently from blank for ERP quantities and prices.

#### B-15 · French legal forms are not dropped from vendor keys · medium
- **Where:** `ap_coder/memory.py:35,42,94`; `ap_coder/store.py:439,460`; `ap_coder/jde.py` (`from_json`)
- **Symptom:** "Les Entreprises ABC Limitée" doesn't match "Les Entreprises ABC"; "X, s.e.c." keys as "x sec".
- **Cause:** the legal-form list was English only.
- **Reproduce:** `vendor_key("Les Entreprises ABC Limitée")`.
- **Reproduced:** yes (`'les entreprises abc limitee'`).
- **Fix:** added `limitee`, `incorporee` and `sec`. Stored keys are recomputed once when `VENDOR_KEY_RULES` changes (now "2"); whoever changes `vendor_key` again must bump it.
- **Test:** `tests/test_memory_store.py::test_vendor_key_ignores_french_legal_forms`, `::test_vendor_keys_stored_by_an_older_version_are_recomputed`
- **Lesson:** Don't build the legal-form list from English only in a bilingual market.

#### B-16 · A QST number without its TQ suffix counts as changed · low
- **Where:** `ap_coder/vendors.py:85`
- **Symptom:** master QST "1234567890" against invoice "1234567890 TQ 0001" raises VENDOR_QST_CHANGED.
- **Cause:** QST numbers were compared as exact strings.
- **Reproduce:** `tax_number_findings` with those two values.
- **Reproduced:** yes (both "same" cases failed).
- **Fix:** `same_qst_number`: when either side lacks TQnnnn, the 10 digits decide; two full numbers must match exactly.
- **Test:** `tests/test_vendors.py::test_qst_numbers_compare_by_their_ten_digits`
- **Lesson:** Don't compare a registration number more strictly than the vendor list stores it.

#### B-17 · "Net 1 month" is read as one day · low-medium
- **Where:** `ap_coder/terms.py:62-87, 109, 129-155, 193-206`
- **Symptom:** "Net 1 month" makes the invoice due the next day.
- **Cause:** the first net-terms pattern read "Net 1 month" as 1 day.
- **Reproduce:** `payment({"invoice_date":"2026-09-10","payment_terms":"Net 1 month",...}).due`.
- **Reproduced:** yes (2026-09-11).
- **Fix:** "Net N month(s)/mois" is read as calendar months, never days (`Terms.net_months`); interest clauses are skipped.
- **Test:** `tests/test_terms.py::test_net_months_are_calendar_months_never_days`
- **Lesson:** Don't read a number before "month" as days.

#### B-18 · "1/2%" is read as 2% · low
- **Where:** `ap_coder/terms.py:36-45`
- **Symptom:** "1/2% 10 net 30" gives a $200 discount on $10,000 instead of $50.
- **Cause:** two discount patterns lacked the lookbehind guard, so they matched "2%" inside "1/2%".
- **Reproduce:** `payment(... "1/2% 10 net 30", total 10000)`.
- **Reproduced:** yes (`(2.0, 200.0)`).
- **Fix:** the `(?<![\d/.,])` guard on both, and "a/b%" is rewritten to its decimal before parsing.
- **Test:** `tests/test_terms.py::test_a_half_percent_discount_is_half_a_percent`
- **Lesson:** Don't let a regex start matching in the middle of a number.

#### B-19 · The GST number threshold is $30, not $100 · low
- **Where:** `ap_coder/taxreturn.py:41-51, 168`; `ap_coder/web/templates/sales_tax.html`
- **Symptom:** an $84 invoice from 2026 with no GST number is flagged as a claim at risk.
- **Cause:** the threshold was a flat $30.
- **Reproduce:** approve an $84 invoice with no number.
- **Reproduced:** yes (it showed NO_GST_NUMBER).
- **Fix:** `document_threshold(date)`: $100 from 2021-01-01, $30 before. QST uses the same amounts; that Quebec followed the federal change is not confirmed against Revenu Québec.
- **Test:** `tests/test_taxreturn.py::test_registration_number_needed_from_100_dollars_since_2021`
- **Lesson:** Don't hard-code a regulatory threshold without the date it applies from.

#### B-20 · A rate written "0.5%" loads as 50% · low
- **Where:** `ap_coder/tax.py:126-132`
- **Symptom:** a rate written "0.5%" loads as 0.5.
- **Cause:** the % sign was stripped first, then `rate >= 1` decided whether to divide by 100.
- **Reproduce:** load a CSV with `PST,BC,0.5%`.
- **Reproduced:** yes (0.5 instead of 0.005).
- **Fix:** text with a % sign is always divided by 100; without it, a value of 1 or more is still read as a percentage.
- **Test:** `tests/test_tax.py::test_a_rate_written_with_a_percent_sign_is_always_a_percentage`
- **Lesson:** Don't throw away the % sign before deciding the unit.

### Exports, ERP and reports

#### B-21 · A JDE batch downloaded again leaves invoices out · high
- **Where:** `ap_coder/web/pages/exports.py:69` (`jde_left_out`, `jde_file`); `ap_coder/jde.py` (`readme`, `build_zip`)
- **Symptom:** downloading an existing JDE batch again gives an empty or short F0411Z1, but the invoices stay marked as in that batch.
- **Cause:** every download re-ran `jde.validate` against today's settings and ERP register; once loaded, a batch's vouchers were duplicates of themselves.
- **Reproduce:** make a JDE batch, import a register holding its invoice, download again.
- **Reproduced:** yes (the vouchers differed; F0911Z1 had only its header row).
- **Fix:** a batch made as JDE is never checked again and always holds all its invoices; today's problems are listed in README.txt and DRY_RUN.txt.
- **Test:** `tests/test_jde.py::test_a_jde_batch_downloads_again_with_the_same_vouchers`
- **Lesson:** Don't re-run gatekeeping checks when re-downloading something already committed.

#### B-22 · The duplicate audit hides an unexported bill already in the ERP · medium
- **Where:** `ap_coder/dupaudit.py:124` (`_same_bill`)
- **Symptom:** an approved invoice not yet exported, but keyed into the ERP by hand, produced no audit pair.
- **Cause:** same vendor and number counted as "its own ERP entry" even when it was never exported.
- **Reproduce:** approve NW-100, put the same vendor, number and amount in the register, run `find`.
- **Reproduced:** yes (no pair came back).
- **Fix:** `return ours.exported and (vendor_key == ... or total == ...)`.
- **Test:** `tests/test_dupaudit.py::test_an_unexported_invoice_already_in_the_erp_is_a_duplicate`
- **Lesson:** Don't treat "same vendor and number" as "same bill" unless we actually sent it.

#### B-23 · A formula in the JDE worksheet · medium
- **Where:** `ap_coder/jde_worksheet.py:350`
- **Symptom:** a vendor name such as `=HYPERLINK(...)` was saved as a live formula in the Suppliers sheet.
- **Cause:** it was the only workbook writer that skipped `safe.neutralise_sheet`.
- **Reproduce:** `build(...)` with that vendor name, then look for formula cells.
- **Reproduced:** yes (a formula in A2).
- **Fix:** every sheet is neutralised before saving; the text is kept word for word.
- **Test:** `tests/test_jde_readiness.py::test_the_worksheet_holds_no_formula_from_a_document`
- **Lesson:** Don't write document text to a workbook without neutralising it.

#### B-24 · The JDE duplicate check misses an earlier export known by GST number · medium
- **Where:** `ap_coder/jde.py:724` (`an8_of`); `ap_coder/store.py` (`search_rows`)
- **Symptom:** a second copy of a bill, its name read differently, went through when the first copy was exported earlier and the vendor is known only by its GST/HST number.
- **Cause:** earlier exports carried no GST/HST number, so their AN8 was looked up by name only.
- **Reproduce:** lookup `{"gst:<n>": "10023"}`, two names, the first copy exported.
- **Reproduced:** yes (no "already exported" problem). The same-batch case was already caught.
- **Fix:** `search_rows` also returns `gst_hst_registration_number`; earlier exports get their AN8 by name or GST/HST.
- **Test:** `tests/test_jde.py::test_an_earlier_export_known_only_by_its_gst_number_is_a_duplicate`
- **Lesson:** Don't compare two records with lookups of different strength.

#### B-25 · A payment word in a statement description makes a payment · medium
- **Where:** `ap_coder/statements.py:158`
- **Symptom:** statement lines such as "14/2 NMD90 wire" or "Brass check valves" became payments, and their invoices showed as "Not on statement".
- **Cause:** the Description column maps to `type`, and payment words were searched in that free text.
- **Reproduce:** headers Date, Invoice, Description, Amount, with matching invoices.
- **Reproduced:** yes (the first line came out as a payment).
- **Fix:** unless the column is a real Type header (`TYPE_HEADERS`), a payment word makes a payment only if no invoice has its number. A column picked by hand with another name (e.g. "Kind") is now free text too.
- **Test:** `tests/test_statements.py::test_a_payment_word_in_a_description_does_not_turn_an_invoice_into_a_payment`
- **Lesson:** Don't read meaning into free-text columns.

#### B-26 · Excel apostrophes in the JDE Z-files · low
- **Where:** `ap_coder/jde.py:788` (`_csv`); `docs/JDE_E1.md:60`
- **Symptom:** a 25-character `+AAA…` invoice number became `'+AAA…` cut to 25 characters; "- Labour" became `'- Labour`.
- **Cause:** spreadsheet quoting (`csv_row`) was applied to files meant for a database loader.
- **Reproduce:** a 25-character invoice number `+AAAA…`, then `export_batch`.
- **Reproduced:** yes (the number no longer matched).
- **Fix:** no `csv_row` in the Z-files; text is written as it is, still cut to the column widths. Two older tests that expected the apostrophe were changed.
- **Test:** `tests/test_jde.py::test_z_files_hold_the_text_as_it_is_without_spreadsheet_quoting`
- **Lesson:** Don't apply Excel quoting to files meant for a database.

#### B-27 · A resent copy of an exported bill is accrued · low-medium
- **Where:** `ap_coder/accruals.py:96` (`_not_in_erp`)
- **Symptom:** a resent copy in review, flagged DUPLICATE_INVOICE, was accrued, which overstates month-end.
- **Cause:** nothing checked the duplicate flags.
- **Reproduce:** export NW-2026-0912, add the copy flagged DUPLICATE_INVOICE, run `build`.
- **Reproduced:** yes (the copy was accrued).
- **Fix:** skipped: DUPLICATE_IN_ERP, and DUPLICATE_INVOICE when the same vendor, number and sign is already exported or accrued.
- **Test:** `tests/test_accruals.py::test_a_resent_copy_of_a_bill_already_counted_is_not_accrued_again`
- **Lesson:** Don't accrue a bill you have already flagged as a duplicate.

#### B-28 · An undone batch can still be downloaded · low
- **Where:** `ap_coder/web/pages/exports.py:337` (`live_batch`)
- **Symptom:** the download and the approved-PDFs ZIP worked by URL for an undone batch and gave an empty file.
- **Cause:** only "no such batch" was checked, not `undone_at`.
- **Reproduce:** make a batch, undo it, then GET the download URL.
- **Reproduced:** yes (200 instead of 410). The review had only read it in the code.
- **Fix:** both downloads answer 410 "Batch N was undone: its invoices can be exported again."
- **Test:** `tests/test_web_finance.py::test_an_undone_batch_cannot_be_downloaded`
- **Lesson:** Don't serve a file for a record that has been undone.

#### B-29 · Directory quick search fails past SQLite's variable limit · medium
- **Where:** `ap_coder/directory.py:286`
- **Symptom:** "too many SQL variables" on SQLite builds limited to 32,766 or 999 variables.
- **Cause:** up to 50,000 ids were bound as `id IN (?,…)`.
- **Reproduce:** set the variable limit to 999 and search with 2,000+ hits.
- **Reproduced:** yes, with that low limit (the review could not, on a build that allows 250,000).
- **Fix:** the ids are bound as one JSON list with `json_each(?)`.
- **Test:** `tests/test_directory.py::test_quick_search_with_more_hits_than_sqlite_takes_variables`
- **Lesson:** Don't bind an unbounded id list as separate SQL variables.

#### B-30 · An export batch fails past SQLite's variable limit · medium
- **Where:** `ap_coder/store.py:1612` (`create_export_batch`)
- **Symptom:** exporting more than 999 ticked invoices on an older SQLite build fails.
- **Cause:** the same `IN (?,…)` pattern.
- **Reproduce:** low variable limit, 1,500+ ids.
- **Reproduced:** yes ("too many SQL variables"; found while fixing B-29).
- **Fix:** the same `json_each(?)` fix. The other store queries are B-11.
- **Test:** `tests/test_exports_bulk.py::test_a_batch_of_more_invoices_than_sqlite_takes_variables`
- **Lesson:** Don't bind an unbounded id list as separate SQL variables.

### AI and readers

#### B-31 · The AI reviewer agrees after confirming one field · high
- **Where:** `ap_coder/verifier.py:269-306` (`review`)
- **Symptom:** the result is AGREED, and the invoice is approved touchless, after the reviewer has confirmed only one field.
- **Cause:** a round that wasn't confident still narrowed the next round to the disputed items; AGREED only looked at the last round.
- **Reproduce:** round 1 disputes `invoice_number`, skips the rest, not confident; round 2 answers only `invoice_number`, confident.
- **Reproduced:** yes (status `agreed`).
- **Fix:** each item agreed in a confident round is tracked and loses that if disputed later; AGREED needs all of them. Only a confident round narrows the next.
- **Test:** `tests/test_verifier.py::test_agreed_needs_every_item_agreed_in_a_confident_round`, `::test_a_confident_round_still_narrows_to_what_it_disputed`
- **Lesson:** Don't let a later round's confidence stand in for items that were never answered.

#### B-32 · Azure agreeing approves past the AI reviewer · high
- **Where:** `ap_coder/verifier.py:431`, `ap_coder/azure_check.py:453` (`hold`); `verifier.py:581`, `azure_check.py:652` (`_fold_in`); `ap_coder/capture/workflow.py:190-209`
- **Symptom:** Azure agreeing approves an invoice whether the reviewer hasn't run yet or has flagged VERIFIER_DISAGREES.
- **Cause:** `azure_check.hold` turned `auto` off first, so `verifier.hold` never recorded its hold; `still_touchless` ignored stored issues.
- **Reproduce:** apply both holds; then let Azure agree on an invoice held for both, or with a stored VERIFIER_DISAGREES.
- **Reproduced:** yes (`verifier_hold` missing, `approved=True`).
- **Fix:** each hold records itself whatever the order. Both `_fold_in`s call `still_untouched`, which re-reads the row and refuses if a hold is set or a `VERIFIER_*`/`AZURE_DI_*` issue is stored.
- **Test:** `tests/test_verifier.py::test_every_hold_is_recorded_whatever_the_order`, `::test_the_reviewer_agreeing_does_not_approve_past_a_pending_azure_hold`, `tests/test_azure_check.py::test_azure_agreeing_does_not_approve_past_the_ai_reviewer`
- **Lesson:** Don't make approval gates depend on the order they were applied in.

#### B-33 · A reopen during the review loses AP's corrections · high
- **Where:** `ap_coder/verifier.py` and `ap_coder/azure_check.py` (`_fold_in`)
- **Symptom:** AP approves with a correction and reopens during the review; the reviewer agreeing then approves the old proposal over AP's coding.
- **Cause:** "untouched" was judged from the row read before the minutes-long review.
- **Reproduce:** a fake model approves the invoice with a changed `invoice_number`, then reopens it, during its own answer.
- **Reproduced:** yes (`approved=True`, the correction overwritten). The review could not run it.
- **Fix:** `still_untouched` re-reads the row right before approving: still in review, no `final_output`, reviewer or edits, same `ai_output`.
- **Test:** `tests/test_verifier.py::test_a_reopen_during_the_review_keeps_aps_corrections`
- **Lesson:** Don't approve from a copy of the row read minutes earlier.

#### B-34 · A draft in the model's thinking is taken as its answer · medium
- **Where:** `ap_coder/local_llm.py:505` (`read_reply`), used by `verifier.ask_model`
- **Symptom:** a draft JSON in the model's reasoning is taken as the reviewer's answer.
- **Cause:** the fallback `last_json_object(content) or last_json_object(reasoning)`.
- **Reproduce:** prose, empty, or unclosed-thought content, with an all-agree draft in the reasoning.
- **Reproduced:** yes (the draft was returned).
- **Fix:** a `strict=True` option, used by the reviewer: only the answer itself counts.
- **Test:** `tests/test_local_llm.py::test_a_strict_reply_never_takes_a_draft_from_the_thinking`, `tests/test_verifier.py::test_a_draft_in_its_thinking_is_not_its_answer`
- **Lesson:** Don't treat a model's scratch work as its decision.

#### B-35 · A page reading with no finish reason is cached as whole · medium
- **Where:** `ap_coder/page_reader.py:884`
- **Symptom:** a partial page is accepted as complete and cached for good.
- **Cause:** "no finish reason" only counted as a failure when nothing had been written.
- **Reproduce:** a stream of three content chunks with no `finish_reason`.
- **Reproduced:** yes (no error raised).
- **Fix:** a stream that ends with no finish reason and didn't loop raises `PageReaderError(temporary=True)`; nothing is cached and it is read again later.
- **Test:** `tests/test_page_reader.py::test_a_reading_that_ends_without_a_finish_reason_is_not_a_whole_page`
- **Lesson:** Don't accept a stream as finished unless the server said so.

#### B-36 · One reviewer call can outrun its time budget · low-medium
- **Where:** `ap_coder/verifier.py:317` (`ask_model`, `_verify`); `ap_coder/inference.py:333` (`_local_create`)
- **Symptom:** one call can run the full 600 s against a 5-minute budget.
- **Cause:** the budget was only checked between rounds.
- **Reproduce:** a fake coder records the per-call timeout.
- **Reproduced:** yes (timeout `None`, so the 600 s default).
- **Fix:** each call gets `max(10 s, time left)`, passed per request when below `AP_LLM_TIMEOUT_SECONDS`.
- **Test:** `tests/test_verifier.py::test_each_call_is_given_only_the_time_left`, `tests/test_local_llm.py::test_a_call_given_less_time_than_the_timeout_gets_only_that`
- **Lesson:** Don't check a time budget only between blocking calls.

#### B-37 · One unrelated 400 turns off structured output · medium-low
- **Where:** `ap_coder/local_llm.py:386` (`rejects_format`)
- **Symptom:** a 400 "Failed to load model" switches off structured output for that model until restart.
- **Cause:** every 400 or 422 was read as "this server refuses `response_format`".
- **Reproduce:** a fake client that always raises 400 "Failed to load model".
- **Reproduced:** yes (modes tried `json_schema`, `json_object`, `text`).
- **Fix:** a 400, 422 or 500 counts as a format refusal only when the message names `response_format`, `json_schema`, `json schema`, `json_object` or `grammar`.
- **Test:** `tests/test_local_llm.py::test_a_400_that_is_not_about_the_format_keeps_structured_output`
- **Lesson:** Don't infer a capability from an error that didn't name it.

#### B-38 · Prompts have no size budget · medium
- **Where:** `ap_coder/inference.py:444-485` (`suggest_accounts`, `_suggest_batch`); `ap_coder/verifier.py` (`MAX_LINES`, `DESCRIPTION_CHARS`)
- **Symptom:** prompts of about 90k characters (GL suggestions) and about 77k (reviewer) for 300 lines.
- **Cause:** prompts were built with no length budget.
- **Reproduce:** 300 lines, a 2,000-account chart, a long history.
- **Reproduced:** yes (reviewer prompt 81,871 characters).
- **Fix:** GL suggestions go in batches of 40 lines (200 at most), descriptions cut to 160 characters, history to 3,000, the chart to half of `AP_LLM_MAX_PROMPT_CHARS`. The reviewer sends a person any invoice over 60 lines.
- **Test:** `tests/test_local_llm.py::test_a_long_invoice_is_suggested_within_the_prompt_budget`, `::test_the_reviewers_prompt_is_bounded`
- **Lesson:** Don't build a prompt from data without a length budget.

#### B-39 · Internet addresses written another way pass the offline guard · low
- **Where:** `ap_coder/config.py:72-97` (`_address`, `_on_this_network`)
- **Symptom:** `134744072`, `0x08080808` and `[64:ff9b::808:808]` (all 8.8.8.8) pass the offline guard.
- **Cause:** hosts were classified by how they are spelled.
- **Reproduce:** call `refused_address` on those URLs.
- **Reproduced:** yes (three of six forms were not refused).
- **Fix:** IPv4 is parsed in every `inet_aton` form by hand; NAT64 is refused; IPv4-mapped addresses are judged by the address they carry. Local and LAN addresses are still allowed.
- **Test:** `tests/test_local_llm.py::test_an_internet_address_written_another_way_is_still_refused`, `::test_lm_studio_on_this_computer_or_the_lan_is_still_allowed`
- **Lesson:** Don't classify hosts by their spelling; parse them the way the resolver will.

#### B-40 · A thinking tag inside a JSON string breaks the parse · low
- **Where:** `ap_coder/local_llm.py:591` (`_whole_object`, used by `parse_json_reply`)
- **Symptom:** `</think>` or `<think>` inside a JSON string gives None or a cut-off string.
- **Cause:** markup was regex-stripped before parsing.
- **Reproduce:** `parse_json_reply('{"reason": "model said </think> here", "a": 2}')`.
- **Reproduced:** yes (None, or a cut-off string).
- **Fix:** the reply is first tried as one whole JSON object, as written or after a leading thought block; the old path is the fallback.
- **Test:** `tests/test_local_llm.py::test_a_thinking_tag_inside_a_json_string_is_kept`
- **Lesson:** Don't regex-strip markup out of text that might be JSON.

#### B-41 · A cut-off reply returns its first line item as the answer · low
- **Where:** `ap_coder/local_llm.py:499-505` (`_unclosed`, `read_reply`)
- **Symptom:** a reply cut off with finish reason "stop" returns line 1's object as the answer.
- **Cause:** the `last_json_object` fallback undid `parse_json_reply`'s guard against inner objects.
- **Reproduce:** `read_reply` on `{"lines": [{…}, {"line_num` with `finish_reason="stop"`.
- **Reproduced:** yes (line 1's object came back).
- **Fix:** an answer whose first object never closes returns no data and the problem "the reply's JSON was cut off before it closed".
- **Test:** `tests/test_local_llm.py::test_a_reply_cut_off_without_the_length_reason_is_not_read_as_its_first_line`
- **Lesson:** Don't let a fallback parser undo the guard the main parser enforces.

### Document capture

#### B-42 · A French "AVOIR" printed positive is approved as an invoice · high
- **Where:** `ap_coder/capture/reader.py:990` (`_CREDIT_TITLE`), `:993` (`credit_titled`); `capture/normalize.py:543` (`CREDIT_TITLES`); `ap_coder/triage.py:71`; `capture/supplier.py:1364`
- **Symptom:** a credit note titled "AVOIR", "Facture d'avoir" or "Credit Invoice" with positive amounts was approved without a person.
- **Cause:** capture's credit titles were fewer than triage's, and the only credit gate was "total < 0".
- **Reproduce:** a French sample header with `# AVOIR` and "Total à créditer 2 414,48 $", through the in-memory harness.
- **Reproduced:** yes (total verified, no gates, auto-approved).
- **Fix:** one shared `CREDIT_TITLES` list for triage and capture; a new `CaptureResult.credit_note` flag makes the touchless gate say "it is a credit note" whatever the sign. Captures saved before the fix are not flagged until read again.
- **Test:** `tests/test_capture_edge_cases.py::test_a_french_avoir_printed_positive_is_a_credit_and_needs_a_person`, `tests/test_touchless.py::test_a_document_titled_as_a_credit_needs_a_person_whatever_its_total`, `tests/test_msg_and_triage.py::test_triage_and_capture_know_the_same_credit_note_titles`
- **Lesson:** Don't let the sign of one number be the only thing that recognises a credit note.

#### B-43 · "Payments in USD accepted" makes a Canadian invoice USD · high
- **Where:** `ap_coder/capture/normalize.py:433` (`_WORDS_STATEMENT`), `:439` (`_PAYMENT_NOTE`), `currency_evidence`
- **Symptom:** an invoice saying "All prices in Canadian dollars. Payments in USD accepted" was verified as USD and approved without a person.
- **Cause:** a currency stated in words was not a statement, so a lone "USD" anywhere won at 0.8.
- **Reproduce:** a sample invoice with "(CAD)" removed from the total label and that footer added, through the harness.
- **Reproduced:** yes (USD verified, auto-approved).
- **Fix:** "in Canadian/US dollars" and "dollars canadiens/américains" are statements; a code only on a payment line is a 0.5 guess. Other lone codes keep 0.8.
- **Test:** `tests/test_capture_edge_cases.py::test_canadian_dollars_stated_in_words_win_over_a_code_in_a_payment_note`
- **Lesson:** Don't let a code mentioned anywhere on the page outrank an explicit statement in words.

#### B-44 · The customer's GST number is verified as the supplier's · medium
- **Where:** `ap_coder/capture/reader.py:674` (`_registration_numbers`); `capture/confidence.py:484`; `capture/types.py` (`BILL_TO_READING`)
- **Symptom:** the customer's GST number in the "Client:" block was verified (0.999) as the supplier's.
- **Cause:** passing GST_NUMBER_VALID (a format and check-digit test) lifted a reading already marked down for the bill-to block.
- **Reproduce:** a sample invoice with the supplier's HST line removed and a GST number added after "Attn: Controller".
- **Reproduced:** yes (verified 0.999, auto-approved).
- **Fix:** a GST, QST or PST number read only in the bill-to block is capped at 0.97, unless VENDOR_GST_MATCH passes. Calibration keys did not change.
- **Test:** `tests/test_capture_edge_cases.py::test_a_registration_number_read_only_in_the_bill_to_block_is_not_verified`
- **Lesson:** Don't treat a format check as proof of whose number it is.

#### B-45 · French quantities "2 500" and "6 000" are read as 2 and 6 · medium
- **Where:** `ap_coder/capture/reader.py:1138` (`spaced_whole`, `_qty_times_price`); `capture/tables.py:262` (`_qty_and_price`)
- **Symptom:** "2 500" at 0,84 = 2 100,00 is read as qty 2; "6 000" as 6.
- **Cause:** `find_amounts` splits a whole number with one space group in two, and neither quantity reader tried the joined value.
- **Reproduce:** `_qty_and_price("2 500", "0,84", 2100.0)`.
- **Reproduced:** yes (`(2.0, 0.84, 2100.0)`).
- **Fix:** both readers also offer the whole value and keep the one where quantity × price = amount.
- **Test:** `tests/test_capture_edge_cases.py::test_a_french_quantity_in_one_space_group_is_read_whole`
- **Lesson:** Don't split a number until you have checked the reading that keeps it whole.

#### B-46 · One negative adjustment line keeps a credit note positive · low
- **Where:** `ap_coder/capture/reader.py:1001` (`credit_printed_positive`)
- **Symptom:** a CREDIT NOTE printed positive with one negative adjustment line was read with every sign positive.
- **Cause:** one negative amount anywhere meant "the signs are printed".
- **Reproduce:** a positive CREDIT NOTE with a row "Restocking fee -50.00".
- **Reproduced:** yes (subtotal, HST and total +2000, +260, +2260).
- **Fix:** the subtotal's and total's own signs decide; the old rule applies only when neither is found.
- **Test:** `tests/test_capture_edge_cases.py::test_a_credit_note_printed_positive_with_one_negative_adjustment_is_still_a_credit`
- **Lesson:** Don't let one adjustment line decide how the whole document's signs are read.

#### B-47 · A failed check still shows its measured confidence · low
- **Where:** `ap_coder/capture/confidence.py:446`
- **Symptom:** a total that failed TOTALS_ADD_UP still showed confidence 0.918.
- **Cause:** the ×0.3 mark-down was overwritten by `calibrate()`, whose key does not record the failed check.
- **Reproduce:** a sample invoice with its total changed by $30.
- **Reproduced:** yes (`grand_total check 0.918`).
- **Fix:** after calibrating, a field that failed a check takes `min(conf, raw)`. Calibration was not refitted.
- **Test:** `tests/test_capture_edge_cases.py::test_a_field_that_failed_a_check_does_not_show_its_measured_confidence`
- **Lesson:** Don't apply a penalty and then overwrite it.

#### B-48 · The accuracy report deletes accents · low
- **Where:** `ap_coder/evaluation.py:27` (`_norm_text`)
- **Symptom:** accented names did not match their unaccented spelling in the accuracy report.
- **Cause:** accented letters were deleted instead of folded to plain letters.
- **Reproduce:** `_norm_text("Équipements Saint-Laurent Ltée")`.
- **Reproduced:** yes (`quipementssaintlaurentlte`).
- **Fix:** NFKD first, then the combining marks are dropped.
- **Test:** `tests/test_evaluation.py::test_accented_names_match_their_unaccented_spelling`
- **Lesson:** Don't delete what you should fold.

#### B-49 · A four-decimal amount over 999 is cut in two · low
- **Where:** `ap_coder/capture/normalize.py:26` (`_AMOUNT`, `_number`)
- **Symptom:** `parse_amount("1234.5678")` gave 1234.56, and `find_amounts` added a stray 78.
- **Cause:** the four-decimal pattern allowed only 1 to 3 digits before the point.
- **Reproduce:** `parse_amount("1234.5678")`.
- **Reproduced:** yes.
- **Fix:** `\d{1,3}` changed to `\d{1,6}` in both places.
- **Test:** `tests/test_capture_edge_cases.py::test_a_price_to_four_decimals_with_four_digits_before_the_point_is_one_number`
- **Lesson:** Don't cut a number off in the middle of its decimals.

#### B-50 · Pages past 20 are dropped without being counted · medium
- **Where:** `ap_coder/capture/layout.py:707`; `capture/types.py` (`pages_left_out`, `page_count`); `capture/__init__.py`; `capture/supplier.py:1379`; `capture/workflow.py` (`invoice_gates`)
- **Symptom:** a 25-page PDF gave `page_count == 20`; with `AP_PAGE_READER_MAX_PAGES` at 25 or more, no gate fired though pages 21 to 25 were never read locally.
- **Cause:** `build_layout` stops at 20 pages without recording it, and `page_count` was `len(layout.pages)`.
- **Reproduce:** build a 25-page PDF, run `analyze(..., ocr=False)`.
- **Reproduced:** yes (20, and an unread count of 0). The review could not build the file.
- **Fix:** the pages left out are recorded, `page_count` is the file's real count, and a new gate says "N of its pages were not read by the local readers". PDFs only.
- **Test:** `tests/test_touchless.py::test_pages_past_the_local_readers_limit_need_a_person`
- **Lesson:** Don't drop pages past a limit without counting them.

### Web routes

#### B-51 · An endless number on the Insights assumptions gives a 500 · low
- **Where:** `ap_coder/web/pages/insights.py:176`
- **Symptom:** `POST /insights/assumptions` with `monthly_volume=1e309` or `inf` returned 500 instead of 400.
- **Cause:** `int(float("inf"))` raises OverflowError; only ValueError was caught.
- **Reproduce:** post all the assumption fields with the page header and `monthly_volume=1e309`.
- **Reproduced:** yes (OverflowError at insights.py:175).
- **Fix:** catch `(ValueError, OverflowError)` and refuse with "enter a number".
- **Test:** `tests/test_web_analytics.py::test_insights_assumptions_refuse_an_endless_invoice_count`
- **Lesson:** Don't call `int()` on a float parsed from user input without catching OverflowError.

#### B-52 · A NaN header amount gets past the approval limit · medium
- **Where:** `ap_coder/web/invoice_view.py:305-317` (`header_values`)
- **Symptom:** a header amount of `nan`, `inf` or `-1e309` was accepted; with `nan` the invoice was approved with no second approver despite a 1.00 limit.
- **Cause:** `to_number` is a bare `float()`, and `header_values` had no finite check.
- **Reproduce:** set `approval_limit` to 1, post `/check` with `grand_total=nan`, then `/approve` with the override key and `why_done=1`.
- **Reproduced:** yes (status `approved`; `pending_approval` with `inf`).
- **Fix:** a non-finite value is refused as "“nan” is not an amount", so the field must be fixed. The store half is B-09.
- **Test:** `tests/test_web_invoice.py::test_a_header_amount_that_is_not_a_finite_number_is_refused`
- **Lesson:** Don't parse money with a bare `float()`; refuse non-finite values in every field, not only in the grid cells.

#### B-53 · Teaching a new line from a long run of words is slow · low
- **Where:** `ap_coder/web/invoice_view.py:239-245` (`line_value`, `line_new`)
- **Symptom:** a new line from 8,000 numbers took 3.6 s, growing with the square of the length.
- **Cause:** the anchored `re.sub(r"(\s+[-$]?\d[\d,.]*)+$", ...)` backtracks from every space.
- **Reproduce:** `POST /review/<id>/teach` with `field=line_new` and `text="Item" + " 1"*8000 + " x 12.50"`.
- **Reproduced:** yes (3.6 s).
- **Fix:** trailing number words are dropped one by one from the end; same result, linear time.
- **Test:** `tests/test_web_invoice.py::test_a_new_line_from_a_long_run_of_words_is_read_quickly`
- **Lesson:** Don't run anchored `(...)+$` regexes over input whose length you don't control.

#### B-54 · Approving an invoice deleted meanwhile gives a 500 · low
- **Where:** `ap_coder/web/pages/invoice.py:521`
- **Symptom:** approving an invoice deleted after the route read it gave a 500.
- **Cause:** `store.approve_invoice` raises KeyError; the route only caught ValueError.
- **Reproduce:** make `invoice_view.analyse` delete the invoice, then post `/approve`.
- **Reproduced:** yes (`KeyError: 5`).
- **Fix:** catch `(KeyError, ValueError)`; the page says "this invoice was deleted meanwhile" and goes back to the queue.
- **Test:** `tests/test_web_robustness.py::test_approving_an_invoice_deleted_while_it_was_checked_says_so`
- **Lesson:** Don't let a "not found" from the store escape a route that has already checked the item exists.

#### B-55 · Retry reads again an invoice in any status · low
- **Where:** `ap_coder/web/pages/review.py:412`
- **Symptom:** `POST /review/<id>/retry` read the file again for any status, which adds a duplicate review row.
- **Cause:** nothing in the route checked the status.
- **Reproduce:** post `/retry` on an invoice in review, rejected or approved.
- **Reproduced:** yes (200 instead of 409).
- **Fix:** any status other than FAILED gets 409 "Retry reads again only an invoice that could not be read: reload the page."
- **Test:** `tests/test_web_review.py::test_retry_reads_again_only_an_invoice_that_failed`
- **Lesson:** Don't trust the template to hide a button; check the state in the route.

#### B-56 · Two undos of one batch record two undos · low
- **Where:** `ap_coder/store.py:1752-1764` (`undo_export_batch`); `ap_coder/web/pages/exports.py:358`
- **Symptom:** two undos at the same moment both passed; the second wrote an `export_undone` event with 0 invoices and replaced `undone_by`.
- **Cause:** the route's check and the store's write were separate steps.
- **Reproduce:** undo a batch, make `store.export_batches` return the listing from before, post the undo again.
- **Reproduced:** yes (200 instead of 400). The review had only read it in the code.
- **Fix:** the store claims the batch with `UPDATE ... WHERE id=? AND undone_at IS NULL` and raises ValueError if no row changes; the route refuses with 400.
- **Test:** `tests/test_web_finance.py::test_two_undos_of_a_batch_at_once_record_one_undo`
- **Lesson:** Don't check a state in one step and write it in another without a condition on the write.

### Front end

#### B-57 · Popovers open off a phone screen · medium
- **Where:** `ap_coder/web/static/app.css:590` (`.pop-body`); fix in `ap_coder/web/static/js/app.js` (a `toggle` listener)
- **Symptom:** at 390px, popovers open past the left edge; Park's submit button can't be reached by touch.
- **Cause:** `.pop-body` is lined up with its button's right edge and is `min(460px, 86vw)` wide.
- **Reproduce:** 390px viewport, `/review/<id>`, open Park.
- **Reproduced:** yes (Park at x=-118; 37 of 64 popovers off-screen).
- **Fix:** a capture-phase `toggle` listener moves an open popover back inside the screen with an 8px margin (0 of 64 off-screen).
- **Test:** `tests/test_web_invoice.py::test_in_a_browser_the_popovers_open_inside_a_phone_screen`
- **Lesson:** Don't anchor a fixed-width popover to one side of its button without keeping it on screen.

#### B-58 · Enter on Cancel uses the dragged words anyway · medium
- **Where:** `ap_coder/web/static/js/invoice.js:643-646` (chooser keydown)
- **Symptom:** after drag-selecting words, Enter on "Cancel" fills the chosen field anyway (the PO number became the vendor name).
- **Cause:** the chooser's keydown called `use.click()` on any Enter, whatever had the focus.
- **Reproduce:** drag across the top of a sample invoice, pick "PO number", Tab to Cancel, press Enter.
- **Reproduced:** yes (one `/teach` POST; `po_number` changed).
- **Fix:** Enter uses the words only when the focus is in the list of choices; a button's own Enter presses that button.
- **Test:** `tests/test_web_invoice.py::test_in_a_browser_enter_on_cancel_leaves_the_words_dragged_on_the_page_unused`
- **Lesson:** Don't bind Enter on a whole dialog, because it overrides the focused button.

#### B-59 · OS dark mode makes some review texts unreadable · medium
- **Where:** `ap_coder/web/static/invoice.css:229` and `:255`
- **Symptom:** in dark mode, the vendor-match line and the math texts can't be read on the review screen.
- **Cause:** the dashboard has only a light look, but these rules switched a few texts to pastel dark-theme colours on the white page.
- **Reproduce:** Chromium with `color_scheme="dark"`, open `/review/<id>`.
- **Reproduced:** yes (contrast 1.40:1 and 1.44:1).
- **Fix:** removed the two `prefers-color-scheme: dark` blocks.
- **Test:** `tests/test_web_invoice.py::test_in_a_browser_os_dark_mode_leaves_the_light_page_readable`
- **Lesson:** Don't add dark-mode colours for a few elements on a page that has no dark theme.

#### B-60 · Ticking "Approve anyway" by keyboard loses the focus · low
- **Where:** `ap_coder/web/static/js/invoice.js:58` (`applySections`)
- **Symptom:** ticking "Approve anyway" (or any control in a redrawn section) with the keyboard drops the focus to `<body>`.
- **Cause:** the section is replaced through `innerHTML` after each check.
- **Reproduce:** focus the checkbox, press Space, wait for the check.
- **Reproduced:** yes (`activeElement` was BODY).
- **Fix:** the focused control's name and value are remembered and it gets the focus back after the redraw.
- **Test:** `tests/test_web_invoice.py::test_in_a_browser_the_approve_anyway_box_keeps_the_focus_when_ticked_with_the_keyboard`
- **Lesson:** Don't redraw a section with `innerHTML` without putting back the focus inside it.

### Intake, CLI and launch

#### B-61 · A small .msg can fill the memory · high
- **Where:** `ap_coder/msgfile.py:159` (`stream`)
- **Symptom:** a 1.2 MB .msg held 201 MB in memory.
- **Cause:** each stream was checked against the file size, but the total was never bounded, and many entries can point at the same sectors.
- **Reproduce:** a .msg with one 40 KB attachment and 10 small ones whose data entries all point at the big one; `read_msg`.
- **Reproduced:** yes (no MsgError raised).
- **Fix:** `stream()` keeps a running total and raises MsgError past 2× file size + 64 KB.
- **Test:** `tests/test_msg_and_triage.py::test_attachments_sharing_one_stream_cannot_fill_the_memory`
- **Lesson:** Don't bound each piece and forget the sum.

#### B-62 · The .msg directory walk is quadratic · high
- **Where:** `ap_coder/msgfile.py:101-106, 138` (`_children`)
- **Symptom:** the walk grew with the square of the entry count (8,000 entries: 8 s and 1.1 GB).
- **Cause:** each storage walked its children with its own visited set.
- **Reproduce:** a CFB with 200 storages whose child is the same 200-entry sibling chain.
- **Reproduced:** yes (40,275 visits instead of at most 404).
- **Fix:** one visited set for all storages, each storage added so it is never its own child.
- **Test:** `tests/test_msg_and_triage.py::test_a_directory_walk_reads_each_entry_once`
- **Lesson:** Don't put a per-call limit on a walk that runs once per node.

#### B-63 · A spreadsheet zip bomb is opened · high
- **Where:** `ap_coder/triage.py:363`
- **Symptom:** a small .xlsx unzipped to hundreds of MB inside openpyxl.
- **Cause:** the zip went to openpyxl with no check of its unzipped sizes.
- **Reproduce:** an .xlsx under 100 KB whose `sharedStrings.xml` unzips to 25 MB.
- **Reproduced:** yes (openpyxl was called).
- **Fix:** `zipfile` checks first: one member over 20 MB, or 50 MB in all, returns "" (`MAX_SHEET_PART_BYTES`, `MAX_SHEET_BYTES`).
- **Test:** `tests/test_msg_and_triage.py::test_a_spreadsheet_that_unzips_to_far_more_is_not_opened`
- **Lesson:** Don't hand an untrusted zip to a library before checking its unzipped sizes.

#### B-64 · A long Chinese name sticks an email forever · medium
- **Where:** `ap_coder/mailbox.py:245` (`_joined`, used by `_save`)
- **Symptom:** an email with a long Chinese name and attachment name was never sorted and was retried silently on every check.
- **Cause:** each part of the file name was capped, but the joined name was not (259 bytes, over 255).
- **Reproduce:** a 40-character Chinese email name plus a 56-character Chinese attachment name.
- **Reproduced:** yes (`OSError: [Errno 36] File name too long`).
- **Fix:** the joined name is capped at 200 bytes by trimming the attachment's stem. An OSError while writing is still "try again later" (a read-only folder is a real reason).
- **Test:** `tests/test_mailbox.py::test_a_long_chinese_email_and_attachment_name_still_fit_one_file_name`
- **Lesson:** Don't size-cap the parts of a file name and then join them uncapped.

#### B-65 · An email that can't be moved is logged on every check · medium
- **Where:** `ap_coder/mailbox.py:327-329`; `ap_coder/inbox.py:118`
- **Symptom:** an email held open (e.g. by Outlook) got a new `triage.jsonl` line and audit event on every check.
- **Cause:** `_record` and `log_sorted` ran even when the move failed.
- **Reproduce:** make `Path.replace` raise PermissionError and unpack the same email 3 times.
- **Reproduced:** yes (3 log entries).
- **Fix:** a new `Unpacked.left_in_place` flag skips `_record` and the event; the email is logged once, after it moves.
- **Test:** `tests/test_mailbox.py::test_an_email_that_cannot_be_moved_is_logged_once`
- **Lesson:** Don't log "done" when the step that makes it done failed.

#### B-66 · `watch` watches a different folder from the dashboard's · medium
- **Where:** `ap_coder/cli.py:166, 415`
- **Symptom:** with `AP_DB_PATH` or `--db`, `watch` watched `private/invoices` instead of the dashboard's folder.
- **Cause:** the default folder was worked out separately from the database path.
- **Reproduce:** `AP_DB_PATH=<tmp>/srv/ap_coder.db`, then `watch --once`.
- **Reproduced:** yes (`…/private/invoices != …/srv/invoices`).
- **Fix:** the folder defaults to None and `cmd_watch` uses `store.path.parent / "invoices"`, as the dashboard does.
- **Test:** `tests/test_config_and_cli.py::test_watch_uses_the_dashboard_invoices_folder`
- **Lesson:** Don't work out the same path two ways.

#### B-67 · Doctor passes damaged OCR models · medium
- **Where:** `ap_coder/offline.py:371`
- **Symptom:** doctor said PASS for damaged PP-OCRv5 models that the app itself won't use.
- **Cause:** `missing_models()` was called without `verify=True`.
- **Reproduce:** one model file with the right name and the wrong SHA-256.
- **Reproduced:** yes (PASS instead of WARN).
- **Fix:** `missing_models(verify=True)`; the message says "missing or damaged".
- **Test:** `tests/test_offline.py::test_doctor_checks_the_model_files_as_the_app_does`
- **Lesson:** Don't let the doctor check less than the code it vouches for.

#### B-68 · Doctor treats a missing Pillow as optional · medium
- **Where:** `ap_coder/doctor.py:85`
- **Symptom:** a missing Pillow was a WARN "needed only for --vision", with exit code 0.
- **Cause:** the wording was left behind when Pillow became a core dependency (photos and scans need it).
- **Reproduce:** make `_version("pillow")` return None.
- **Reproduced:** yes (WARN instead of FAIL).
- **Fix:** Pillow is in `_PACKAGES` as a FAIL "(photos and scanned images can't be read)".
- **Test:** `tests/test_config_and_cli.py::test_doctor_fails_without_pillow`
- **Lesson:** Don't leave doctor wording behind when a dependency's role grows.

#### B-69 · A file stamped in the future is never picked up · medium
- **Where:** `ap_coder/cli.py:349`; `ap_coder/mailbox.py:374`
- **Symptom:** a file stamped ahead of the clock was skipped until the clock caught up.
- **Cause:** `now - mtime < SETTLE` is also true when the difference is negative.
- **Reproduce:** set a file's mtime one hour ahead.
- **Reproduced:** yes (the file was not picked up; the .eml case too).
- **Fix:** `abs(now - mtime) < SETTLE_SECONDS`; a file within 5 s ahead still counts as copying.
- **Test:** `tests/test_controls_watch.py::test_a_file_stamped_in_the_future_is_picked_up`, `tests/test_mailbox.py::test_an_email_stamped_in_the_future_is_unpacked`
- **Lesson:** Don't assume a file's timestamp is in the past.

#### B-70 · The CLI exits 0 after printed failures · low
- **Where:** `ap_coder/cli.py:249-284, 297, 317, 332, 386-430, 486, 548-570`
- **Symptom:** `process`, `extract`, `read-pages` and `watch --once` exited 0 after printing failures.
- **Cause:** errors weren't carried through to the exit code.
- **Reproduce:** `process bad.msg good.pdf`; `watch --once` with only a bad email.
- **Reproduced:** yes (exit 0 instead of 1). The review had only read it in the code.
- **Fix:** an unreadable email makes `process` and `extract` return 1; `extract` with nothing to read returns 2; `read-pages` returns 1 on a failed read; `watch --once` returns 1 when everything failed.
- **Test:** `tests/test_config_and_cli.py::test_a_command_that_could_not_read_something_does_not_exit_0`, `tests/test_controls_watch.py::test_watch_once_fails_when_every_email_could_not_be_read`
- **Lesson:** Don't print an error and then return success.

#### B-71 · A big .eml is parsed before the part cap applies · low-medium
- **Where:** `ap_coder/mailbox.py:160`
- **Symptom:** an email of thousands of tiny parts was fully parsed (about 86 µs a part) before the 500-part cap.
- **Cause:** the limit was applied after the costly parse.
- **Reproduce:** `read_eml` on a 6,000-part email.
- **Reproduced:** yes (it was parsed).
- **Fix:** more than `MAX_BOUNDARY_LINES` (5,000) boundary lines raises UnreadableEmail before parsing; the email goes to "could not read".
- **Test:** `tests/test_mailbox.py::test_an_email_of_thousands_of_tiny_parts_is_refused_before_it_is_parsed`
- **Lesson:** Don't apply the limit after the costly parse.

#### B-72 · `watch --once` skips invoices it just took out of emails · low
- **Where:** `ap_coder/cli.py:378-389` (`new_files(..., fresh=...)`)
- **Symptom:** `watch --once` skipped the invoices it had just taken out of emails, so they waited a full scheduler cycle.
- **Cause:** the "still copying" check applied to files AP Coder had just written itself.
- **Reproduce:** an old .eml with one PDF, then `watch --once`.
- **Reproduced:** yes (nothing processed). The review had only read it in the code.
- **Fix:** `watch_once` passes `mail.saved` as `fresh`, which skips the copying check for those files only.
- **Test:** `tests/test_controls_watch.py::test_watch_once_processes_the_invoices_it_just_took_out_of_emails`
- **Lesson:** Don't apply the "still copying" check to files your own process just wrote.

### Not reproduced or left as they are

- **N-01** · Store · the backup's `-wal` file check. Not reproduced by the review; left.
- **N-02** · Store · a history cost centre stored as "100.0". Not reproduced; left.
- **N-03** · Store · a race in `write_user_settings`. Not reproduced; left.
- **N-04** · Store · nested `_conn` calls. Not reproduced; left.
- **N-05** · Store · the audit comparing 100 with 100.0. Not reproduced; left.
- **N-06** · Tax / web routes · `review.to_number("12,50")` gives 1250. Confirmed unreachable by both fixers: every amount box is `type="number"`, which a browser always sends with a dot. Only a hand-made POST can send it, and the screen would show 1,250.00. If a text input is ever added, use `safe.parse_amount`.
- **N-07** · Tax · `taxmath.math_check` and `check_taxes` may differ by 1-2 cents. Notes only; not investigated.
- **N-08** · Exports · two people exporting two copies of one bill at once could both pass `jde_check` before `create_export_batch` locks. Not tried (needs a real database and concurrency); left.
- **N-09** · Exports · French statement column aliases such as "crédit" can never match, because accents are stripped first. Read in the code only; columns can be picked by hand; left.
- **N-10** · AI · a long page read can pass `STALE_READING_HOURS` (2 h) while still running, and another process could take the same invoice. Needs a database and real timing; left.
- **N-11** · AI · lost update of `meta.capture`: the Azure thread and the page reader each write it from a stale copy, so one can bring back a hold the other removed. Explicitly not fixed; the re-read before approval (B-33) limits it to the safe direction (a hold brought back only stops an approval).
- **N-12** · AI · the reviewer sees only `MAX_PAGES = 3` page images but is asked about every field and line. A design concern, not a bug to reproduce.
- **N-13** · AI · waiting for `CHAT_LOCK` has no timeout. By design.
- **N-14** · AI · PyMuPDF is used without `_RENDER_LOCK` in `imaging.py`, `capture/layout.py` and `web/document.py`. Likely safe under the GIL; no failure shown.
- **N-15** · Capture · `_totals_that_add_up` leaves out `tax_total`, so it could swap in a wrong subtotal on a sales-tax-only invoice. No wrong swap seen; left.
- **N-16** · Capture · an ambiguous numeric date read by a supplier template with no learned date order comes back as raw text. Not built; at worst Likely, never Verified.
- **N-17** · Capture · the OCR colon fix turns times into amounts ("10:30" as 10.30) in `find_amounts`. No line found where it changes a chosen value.
- **N-18** · Capture · notes only: "Payable on receipt" in prose without a label is not read (the field stays empty), and some ground-truth files leave blank fields the invoice prints.
- **N-19** · Web routes · `/insights/assumptions` strips commas (`insights.py:173`), a silent ×100 for "12,50". The box is `type="number"`, so only a hand-made POST reaches it; left.
- **N-20** · Web routes · CSRF. Checked: `LocalOnly` refuses writes without `X-AP-Coder: 1` and a same-origin check; no GET changes state.
- **N-21** · Web routes · open redirect. Checked: every redirect goes through `local_url`.
- **N-22** · Web routes · path traversal in backups, inbox files, uploads and page pictures. Checked: all bounded.
- **N-23** · Web routes · races on approve, park, reject, send back and reopen. Checked: each is one conditional UPDATE or BEGIN IMMEDIATE. (The batch-undo race was real: B-56.)
- **N-24** · Web routes · XSS. Checked: autoescape on, `innerHTML` takes escaped HTML; `ui.pill` doesn't escape `tone`, but every tone is a constant.
- **N-25** · Web routes · reaching the internet. Checked: none at runtime; `/settings/azure-check` is the documented opt-in, off by default.
- **N-26** · Web routes · odd ids. Checked: `:int` converters, and ids over 2^63 give a 404.
- **N-27** · Intake · a half-copied file with an old mtime could be picked up, since `new_files` never checks size stability. Needs a real Windows or Mac copy; left.
- **N-28** · Intake · the dashboard and `watch` sorting the same email at once log a duplicate entry. Needs real concurrency; the missing shared lock is already in `docs/HOW_IT_RUNS.md`.
- **N-29** · Intake · doctor package checks read metadata only, so a broken install still shows PASS. Needs a broken install; left.
- **N-30** · Intake · launchers (`APProcessor.bat`, `APProcessor.command`, `scripts/launch.py`, `scripts/running.py`). Read only, not run; quoting and the second-instance lock look right.
- **N-31** · Intake · reaching the internet. Checked: every way out is behind `internet_allowed()` or the documented setup download.
