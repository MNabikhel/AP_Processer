# Bug log

Every bug found by a bug sweep: what went wrong, how to reproduce it, the fix, the test that keeps it fixed, and the lesson
("don't do this again"). B-118 to B-123 and B-131 were bugs in the codebase-map tools, which were removed later (decisions D-38).
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
- **Where:** `ap_coder/core/reference_data.py:63-77` (`_canonical_headers`)
- **Symptom:** a GL file with headers "GL Code" and "Active" is refused with "missing required column 'gl_code'"; an Active=no column is ignored, so retired codes reach the prompt.
- **Cause:** when a header normalised exactly to the standard name, the loop skipped it and kept the original header text.
- **Reproduce:** CSV `GL Code,Description,Active`, then `load_table`.
- **Reproduced:** yes (ValueError "missing required column 'gl_code'").
- **Fix:** that header is now renamed to the standard name too.
- **Test:** `tests/test_reference_data.py::test_header_that_spells_the_standard_name_is_renamed`
- **Lesson:** Don't skip the rename step for the exact standard name.

#### B-02 · Doubled quotes in a CSV cell are misread · medium-low
- **Where:** `ap_coder/core/csvio.py:13-23` (`_dialect`)
- **Symptom:** `"Acme ""Best"" Inc"` is read as `Acme "Best"" Inc"`.
- **Cause:** the sniffer looked at the header line only and guessed doublequote=False.
- **Reproduce:** a CSV with a doubled quote in a quoted cell, read with `read_csv_rows`.
- **Reproduced:** yes (got `'Acme "Best"" Inc"'`).
- **Fix:** only the delimiter is taken from the sniffer; quoting stays Excel's.
- **Test:** `tests/test_real_world.py::test_doubled_quotes_in_a_csv_cell_are_read_as_one_quote`
- **Lesson:** Don't use a sniffed dialect's quoting rules after sniffing one line.

#### B-03 · A re-review without a result erases the last result · medium-low
- **Where:** `ap_coder/core/store.py:2599` (`finish_verification`)
- **Symptom:** a re-review that ends failed, waiting or skipped erases the previous result and its disputed-field issues.
- **Cause:** `CASE WHEN ? IS NOT NULL` was given a Python bool, which is never NULL.
- **Reproduce:** finish "done" with a result, queue again, finish "failed".
- **Reproduced:** yes (the result became None).
- **Fix:** `result_json = COALESCE(?, result_json)`.
- **Test:** `tests/test_verifier.py::test_a_review_that_ends_without_a_result_keeps_the_last_result`
- **Lesson:** Don't pass a Python bool to IS NULL.

#### B-04 · An accented reviewer name never counts approvals today · low
- **Where:** `ap_coder/core/store.py:1203-1225` (`approved_since`)
- **Symptom:** reviewer "Élise Tremblay" always sees 0 approved today.
- **Cause:** SQLite `lower()` only changes ASCII letters, but the name was lowered in Python.
- **Reproduce:** approve as "Élise Tremblay", then `approved_since(today, "élise tremblay")`.
- **Reproduced:** yes (0 instead of 1).
- **Fix:** names are compared in Python with casefold, still in one query.
- **Test:** `tests/test_end_to_end_sweep_fixes.py::test_approved_today_matches_accented_names_without_case`
- **Lesson:** Don't compare non-ASCII text with SQLite `lower()`.

#### B-05 · A re-created database can't take the write lock · low
- **Where:** `ap_coder/core/store.py:478` (`_open`)
- **Symptom:** the first BEGIN IMMEDIATE on a re-created database fails.
- **Cause:** the upgrade step's DELETE opened a transaction that was never committed.
- **Reproduce:** delete the database file, then `delete_invoice(1, keep_approved=True)`.
- **Reproduced:** yes ("cannot start a transaction within a transaction").
- **Fix:** `conn.commit()` after the upgrade step.
- **Test:** `tests/test_store_session.py::test_a_database_made_again_can_take_the_write_lock_at_once`
- **Lesson:** Don't leave a setup step's changes uncommitted on a connection handed to callers.

#### B-06 · Saved settings are expanded or split · low
- **Where:** `ap_coder/core/config.py:204`, `ap_coder/core/envfile.py:58-64`
- **Symptom:** a saved secret `pa$$word ${HOME}` comes back as `pa$$word /root`; a value with a line break splits into extra settings.
- **Cause:** `load_dotenv` expanded variables; `write_env` allowed line breaks.
- **Reproduce:** write the key with `write_env`, then `Settings.from_env`.
- **Reproduced:** yes (got `'pa$$word /root'`; a value with a line break was written).
- **Fix:** the .env is loaded with `interpolate=False`; `write_env` raises ValueError on a line break and writes nothing (see B-08).
- **Test:** `tests/test_envfile_edge_cases.py::test_a_saved_value_with_a_dollar_sign_reads_back_as_typed`, `::test_a_value_with_a_line_break_is_refused`
- **Lesson:** Don't let a settings file expand or split values the user typed.

#### B-07 · A CAD rate can be typed in · minor
- **Where:** `ap_coder/core/store.py:223` (`parse_fx_rates`)
- **Symptom:** `CAD=1.4` is accepted and scales every CAD limit check.
- **Cause:** CAD, the base currency, was not excluded.
- **Reproduce:** `parse_fx_rates("CAD=1.4, USD=1.37")`.
- **Reproduced:** yes (CAD 1.4).
- **Fix:** a typed CAD rate is skipped.
- **Test:** `tests/test_spend.py::test_a_cad_rate_typed_in_is_ignored`
- **Lesson:** Don't let the base currency's rate be overridden.

#### B-08 · A setting typed with a line break gives an error page · low
- **Where:** `ap_coder/core/envfile.py:58` (`one_line`), used by `save_env` in `ap_coder/web/pages/settings.py` and `save_settings` in `ap_coder/ai/azure_check.py`
- **Symptom:** once `write_env` refused line breaks (B-06), a setting sent with a line break gave a 500 from the Settings page.
- **Cause:** the refusal was at the file writer, not where the value is typed.
- **Reproduce:** post a Settings value that holds a line break.
- **Reproduced:** yes (noted by the B-06 fix; found while merging, not in a review report).
- **Fix:** `envfile.one_line` makes the value one line in `save_env` and the Azure settings before it is written.
- **Test:** `tests/test_web_settings.py::test_a_setting_typed_with_a_line_break_is_saved_as_one_line`
- **Lesson:** Refuse bad input where it is typed, not with an error page.

#### B-09 · A NaN total skips the second approver · medium
- **Where:** `ap_coder/core/store.py:1377-1379` (`second_approval_reason`)
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
- **Where:** `ap_coder/core/store.py:54` (`_in_list`), used by every store query that takes a list of ids or keys
- **Symptom:** a query with a long list of ids or keys fails with "too many SQL variables" on older SQLite builds (999 variables).
- **Cause:** each list used one `?` per item.
- **Reproduce:** set the connection's variable limit to 999 and pass more ids than that.
- **Reproduced:** yes (found while merging B-29 and B-30, which fixed two of these queries; not in a review report).
- **Fix:** the lists are bound as one JSON parameter: `column IN (SELECT value FROM json_each(?))`.
- **Test:** `tests/test_exports_bulk.py::test_every_list_query_takes_more_ids_than_sqlite_takes_variables`
- **Lesson:** Don't bind an unbounded list as separate SQL variables.

### Tax and coding rules

#### B-12 · An old rate row left open keeps winning · high
- **Where:** `ap_coder/coding/tax.py:148` (`_in_force`; was `rate_for` at 139)
- **Symptom:** if a new rate row is appended and the old row is left open, the old rate keeps winning.
- **Cause:** `rate_for` returned the first covering row in file order.
- **Reproduce:** add `PST SK 0.07 from 2026-07-01, no end`, then `rate_for("PST","SK",2026-09-01)`.
- **Reproduced:** yes (`0.06 == 0.07` failed).
- **Fix:** the covering row with the latest `effective_from` wins; a province row beats a Canada-wide one on a tie. Overlaps are logged at load. `in_force()` shows one row per tax and province in `to_markdown` and the Tax setup page.
- **Test:** `tests/test_tax.py::test_a_new_rate_row_wins_over_an_old_one_left_open`
- **Lesson:** Don't let file order decide which effective-dated row wins.

#### B-13 · Self-assessment uses its own place of supply · medium
- **Where:** `ap_coder/coding/taxreturn.py:127` (`supply_province`), plus `province()`, `build`, `self_assessment`
- **Symptom:** BC PST is flagged for a BC vendor delivering to our AB site, and missed for a US vendor delivering to our BC site.
- **Cause:** it used ship-to, then the supplier's province, and ignored the site and default provinces in TaxSetup.
- **Reproduce:** set our province to BC and site `CCAB=AB`; approve two invoices with no ship-to.
- **Reproduced:** yes (`[('B-1','BC','PST')] == [('U-1','BC','PST')]` failed).
- **Fix:** `supply_setup` and `supply_province` call `tax.place_of_supply`, the rule of the tax checks. Claims use it for the HST/QST province too.
- **Test:** `tests/test_taxreturn.py::test_self_assessment_uses_the_place_of_supply_of_the_tax_checks`
- **Lesson:** Don't compute place of supply twice with different rules.

#### B-14 · ERP lump-sum lines with qty 0 and price 0 raise PO alarms · medium
- **Where:** `ap_coder/coding/po.py:124`
- **Symptom:** lump-sum PO lines exported as qty 0 / price 0 raise false PO_PRICE_OVER and PO_QTY_OVER.
- **Cause:** `amount_only` was set only when qty and price were blank, not when they were 0.
- **Reproduce:** import qty 0, price 0, total 5,000.
- **Reproduced:** yes (got `(0.0, 0.0, 5000.0, False)`).
- **Fix:** with a non-zero amount, a 0 qty or price counts as blank: qty 1, price = amount, amount_only. An all-zero line stays as it is.
- **Test:** `tests/test_po.py::test_lump_sum_line_exported_with_zero_quantity_and_price_is_amount_only`
- **Lesson:** Don't treat 0 differently from blank for ERP quantities and prices.

#### B-15 · French legal forms are not dropped from vendor keys · medium
- **Where:** `ap_coder/core/memory.py:35,42,94`; `ap_coder/core/store.py:439,460`; `ap_coder/erp/jde.py` (`from_json`)
- **Symptom:** "Les Entreprises ABC Limitée" doesn't match "Les Entreprises ABC"; "X, s.e.c." keys as "x sec".
- **Cause:** the legal-form list was English only.
- **Reproduce:** `vendor_key("Les Entreprises ABC Limitée")`.
- **Reproduced:** yes (`'les entreprises abc limitee'`).
- **Fix:** added `limitee`, `incorporee` and `sec`. Stored keys are recomputed once when `VENDOR_KEY_RULES` changes (now "2"); whoever changes `vendor_key` again must bump it.
- **Test:** `tests/test_memory_store.py::test_vendor_key_ignores_french_legal_forms`, `::test_vendor_keys_stored_by_an_older_version_are_recomputed`
- **Lesson:** Don't build the legal-form list from English only in a bilingual market.

#### B-16 · A QST number without its TQ suffix counts as changed · low
- **Where:** `ap_coder/coding/vendors.py:85`
- **Symptom:** master QST "1234567890" against invoice "1234567890 TQ 0001" raises VENDOR_QST_CHANGED.
- **Cause:** QST numbers were compared as exact strings.
- **Reproduce:** `tax_number_findings` with those two values.
- **Reproduced:** yes (both "same" cases failed).
- **Fix:** `same_qst_number`: when either side lacks TQnnnn, the 10 digits decide; two full numbers must match exactly.
- **Test:** `tests/test_vendors.py::test_qst_numbers_compare_by_their_ten_digits`
- **Lesson:** Don't compare a registration number more strictly than the vendor list stores it.

#### B-17 · "Net 1 month" is read as one day · low-medium
- **Where:** `ap_coder/coding/terms.py:62-87, 109, 129-155, 193-206`
- **Symptom:** "Net 1 month" makes the invoice due the next day.
- **Cause:** the first net-terms pattern read "Net 1 month" as 1 day.
- **Reproduce:** `payment({"invoice_date":"2026-09-10","payment_terms":"Net 1 month",...}).due`.
- **Reproduced:** yes (2026-09-11).
- **Fix:** "Net N month(s)/mois" is read as calendar months, never days (`Terms.net_months`); interest clauses are skipped.
- **Test:** `tests/test_terms.py::test_net_months_are_calendar_months_never_days`
- **Lesson:** Don't read a number before "month" as days.

#### B-18 · "1/2%" is read as 2% · low
- **Where:** `ap_coder/coding/terms.py:36-45`
- **Symptom:** "1/2% 10 net 30" gives a $200 discount on $10,000 instead of $50.
- **Cause:** two discount patterns lacked the lookbehind guard, so they matched "2%" inside "1/2%".
- **Reproduce:** `payment(... "1/2% 10 net 30", total 10000)`.
- **Reproduced:** yes (`(2.0, 200.0)`).
- **Fix:** the `(?<![\d/.,])` guard on both, and "a/b%" is rewritten to its decimal before parsing.
- **Test:** `tests/test_terms.py::test_a_half_percent_discount_is_half_a_percent`
- **Lesson:** Don't let a regex start matching in the middle of a number.

#### B-19 · The GST number threshold is $30, not $100 · low
- **Where:** `ap_coder/coding/taxreturn.py:41-51, 168`; `ap_coder/web/templates/sales_tax.html`
- **Symptom:** an $84 invoice from 2026 with no GST number is flagged as a claim at risk.
- **Cause:** the threshold was a flat $30.
- **Reproduce:** approve an $84 invoice with no number.
- **Reproduced:** yes (it showed NO_GST_NUMBER).
- **Fix:** `document_threshold(date)`: $100 from 2021-01-01, $30 before. QST uses the same amounts; that Quebec followed the federal change is not confirmed against Revenu Québec.
- **Test:** `tests/test_taxreturn.py::test_registration_number_needed_from_100_dollars_since_2021`
- **Lesson:** Don't hard-code a regulatory threshold without the date it applies from.

#### B-20 · A rate written "0.5%" loads as 50% · low
- **Where:** `ap_coder/coding/tax.py:126-132`
- **Symptom:** a rate written "0.5%" loads as 0.5.
- **Cause:** the % sign was stripped first, then `rate >= 1` decided whether to divide by 100.
- **Reproduce:** load a CSV with `PST,BC,0.5%`.
- **Reproduced:** yes (0.5 instead of 0.005).
- **Fix:** text with a % sign is always divided by 100; without it, a value of 1 or more is still read as a percentage.
- **Test:** `tests/test_tax.py::test_a_rate_written_with_a_percent_sign_is_always_a_percentage`
- **Lesson:** Don't throw away the % sign before deciding the unit.

### Exports, ERP and reports

#### B-21 · A JDE batch downloaded again leaves invoices out · high
- **Where:** `ap_coder/web/pages/exports.py:69` (`jde_left_out`, `jde_file`); `ap_coder/erp/jde.py` (`readme`, `build_zip`)
- **Symptom:** downloading an existing JDE batch again gives an empty or short F0411Z1, but the invoices stay marked as in that batch.
- **Cause:** every download re-ran `jde.validate` against today's settings and ERP register; once loaded, a batch's vouchers were duplicates of themselves.
- **Reproduce:** make a JDE batch, import a register holding its invoice, download again.
- **Reproduced:** yes (the vouchers differed; F0911Z1 had only its header row).
- **Fix:** a batch made as JDE is never checked again and always holds all its invoices; today's problems are listed in README.txt and DRY_RUN.txt.
- **Test:** `tests/test_jde.py::test_a_jde_batch_downloads_again_with_the_same_vouchers`
- **Lesson:** Don't re-run gatekeeping checks when re-downloading something already committed.

#### B-22 · The duplicate audit hides an unexported bill already in the ERP · medium
- **Where:** `ap_coder/erp/dupaudit.py:124` (`_same_bill`)
- **Symptom:** an approved invoice not yet exported, but keyed into the ERP by hand, produced no audit pair.
- **Cause:** same vendor and number counted as "its own ERP entry" even when it was never exported.
- **Reproduce:** approve NW-100, put the same vendor, number and amount in the register, run `find`.
- **Reproduced:** yes (no pair came back).
- **Fix:** `return ours.exported and (vendor_key == ... or total == ...)`.
- **Test:** `tests/test_dupaudit.py::test_an_unexported_invoice_already_in_the_erp_is_a_duplicate`
- **Lesson:** Don't treat "same vendor and number" as "same bill" unless we actually sent it.

#### B-23 · A formula in the JDE worksheet · medium
- **Where:** `ap_coder/erp/jde_worksheet.py:350`
- **Symptom:** a vendor name such as `=HYPERLINK(...)` was saved as a live formula in the Suppliers sheet.
- **Cause:** it was the only workbook writer that skipped `safe.neutralise_sheet`.
- **Reproduce:** `build(...)` with that vendor name, then look for formula cells.
- **Reproduced:** yes (a formula in A2).
- **Fix:** every sheet is neutralised before saving; the text is kept word for word.
- **Test:** `tests/test_jde_readiness.py::test_the_worksheet_holds_no_formula_from_a_document`
- **Lesson:** Don't write document text to a workbook without neutralising it.

#### B-24 · The JDE duplicate check misses an earlier export known by GST number · medium
- **Where:** `ap_coder/erp/jde.py:724` (`an8_of`); `ap_coder/core/store.py` (`search_rows`)
- **Symptom:** a second copy of a bill, its name read differently, went through when the first copy was exported earlier and the vendor is known only by its GST/HST number.
- **Cause:** earlier exports carried no GST/HST number, so their AN8 was looked up by name only.
- **Reproduce:** lookup `{"gst:<n>": "10023"}`, two names, the first copy exported.
- **Reproduced:** yes (no "already exported" problem). The same-batch case was already caught.
- **Fix:** `search_rows` also returns `gst_hst_registration_number`; earlier exports get their AN8 by name or GST/HST.
- **Test:** `tests/test_jde.py::test_an_earlier_export_known_only_by_its_gst_number_is_a_duplicate`
- **Lesson:** Don't compare two records with lookups of different strength.

#### B-25 · A payment word in a statement description makes a payment · medium
- **Where:** `ap_coder/erp/statements.py:158`
- **Symptom:** statement lines such as "14/2 NMD90 wire" or "Brass check valves" became payments, and their invoices showed as "Not on statement".
- **Cause:** the Description column maps to `type`, and payment words were searched in that free text.
- **Reproduce:** headers Date, Invoice, Description, Amount, with matching invoices.
- **Reproduced:** yes (the first line came out as a payment).
- **Fix:** unless the column is a real Type header (`TYPE_HEADERS`), a payment word makes a payment only if no invoice has its number. A column picked by hand with another name (e.g. "Kind") is now free text too.
- **Test:** `tests/test_statements.py::test_a_payment_word_in_a_description_does_not_turn_an_invoice_into_a_payment`
- **Lesson:** Don't read meaning into free-text columns.

#### B-26 · Excel apostrophes in the JDE Z-files · low
- **Where:** `ap_coder/erp/jde.py:788` (`_csv`); `docs/JDE_E1.md:60`
- **Symptom:** a 25-character `+AAA…` invoice number became `'+AAA…` cut to 25 characters; "- Labour" became `'- Labour`.
- **Cause:** spreadsheet quoting (`csv_row`) was applied to files meant for a database loader.
- **Reproduce:** a 25-character invoice number `+AAAA…`, then `export_batch`.
- **Reproduced:** yes (the number no longer matched).
- **Fix:** no `csv_row` in the Z-files; text is written as it is, still cut to the column widths. Two older tests that expected the apostrophe were changed.
- **Test:** `tests/test_jde.py::test_z_files_hold_the_text_as_it_is_without_spreadsheet_quoting`
- **Lesson:** Don't apply Excel quoting to files meant for a database.

#### B-27 · A resent copy of an exported bill is accrued · low-medium
- **Where:** `ap_coder/erp/accruals.py:96` (`_not_in_erp`)
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
- **Where:** `ap_coder/erp/directory.py:286`
- **Symptom:** "too many SQL variables" on SQLite builds limited to 32,766 or 999 variables.
- **Cause:** up to 50,000 ids were bound as `id IN (?,…)`.
- **Reproduce:** set the variable limit to 999 and search with 2,000+ hits.
- **Reproduced:** yes, with that low limit (the review could not, on a build that allows 250,000).
- **Fix:** the ids are bound as one JSON list with `json_each(?)`.
- **Test:** `tests/test_directory.py::test_quick_search_with_more_hits_than_sqlite_takes_variables`
- **Lesson:** Don't bind an unbounded id list as separate SQL variables.

#### B-30 · An export batch fails past SQLite's variable limit · medium
- **Where:** `ap_coder/core/store.py:1612` (`create_export_batch`)
- **Symptom:** exporting more than 999 ticked invoices on an older SQLite build fails.
- **Cause:** the same `IN (?,…)` pattern.
- **Reproduce:** low variable limit, 1,500+ ids.
- **Reproduced:** yes ("too many SQL variables"; found while fixing B-29).
- **Fix:** the same `json_each(?)` fix. The other store queries are B-11.
- **Test:** `tests/test_exports_bulk.py::test_a_batch_of_more_invoices_than_sqlite_takes_variables`
- **Lesson:** Don't bind an unbounded id list as separate SQL variables.

### AI and readers

#### B-31 · The AI reviewer agrees after confirming one field · high
- **Where:** `ap_coder/ai/verifier.py:269-306` (`review`)
- **Symptom:** the result is AGREED, and the invoice is approved touchless, after the reviewer has confirmed only one field.
- **Cause:** a round that wasn't confident still narrowed the next round to the disputed items; AGREED only looked at the last round.
- **Reproduce:** round 1 disputes `invoice_number`, skips the rest, not confident; round 2 answers only `invoice_number`, confident.
- **Reproduced:** yes (status `agreed`).
- **Fix:** each item agreed in a confident round is tracked and loses that if disputed later; AGREED needs all of them. Only a confident round narrows the next.
- **Test:** `tests/test_verifier.py::test_agreed_needs_every_item_agreed_in_a_confident_round`, `::test_a_confident_round_still_narrows_to_what_it_disputed`
- **Lesson:** Don't let a later round's confidence stand in for items that were never answered.

#### B-32 · Azure agreeing approves past the AI reviewer · high
- **Where:** `ap_coder/ai/verifier.py:431`, `ap_coder/ai/azure_check.py:453` (`hold`); `verifier.py:581`, `azure_check.py:652` (`_fold_in`); `ap_coder/capture/workflow.py:190-209`
- **Symptom:** Azure agreeing approves an invoice whether the reviewer hasn't run yet or has flagged VERIFIER_DISAGREES.
- **Cause:** `azure_check.hold` turned `auto` off first, so `verifier.hold` never recorded its hold; `still_touchless` ignored stored issues.
- **Reproduce:** apply both holds; then let Azure agree on an invoice held for both, or with a stored VERIFIER_DISAGREES.
- **Reproduced:** yes (`verifier_hold` missing, `approved=True`).
- **Fix:** each hold records itself whatever the order. Both `_fold_in`s call `still_untouched`, which re-reads the row and refuses if a hold is set or a `VERIFIER_*`/`AZURE_DI_*` issue is stored.
- **Test:** `tests/test_verifier.py::test_every_hold_is_recorded_whatever_the_order`, `::test_the_reviewer_agreeing_does_not_approve_past_a_pending_azure_hold`, `tests/test_azure_check.py::test_azure_agreeing_does_not_approve_past_the_ai_reviewer`
- **Lesson:** Don't make approval gates depend on the order they were applied in.

#### B-33 · A reopen during the review loses AP's corrections · high
- **Where:** `ap_coder/ai/verifier.py` and `ap_coder/ai/azure_check.py` (`_fold_in`)
- **Symptom:** AP approves with a correction and reopens during the review; the reviewer agreeing then approves the old proposal over AP's coding.
- **Cause:** "untouched" was judged from the row read before the minutes-long review.
- **Reproduce:** a fake model approves the invoice with a changed `invoice_number`, then reopens it, during its own answer.
- **Reproduced:** yes (`approved=True`, the correction overwritten). The review could not run it.
- **Fix:** `still_untouched` re-reads the row right before approving: still in review, no `final_output`, reviewer or edits, same `ai_output`.
- **Test:** `tests/test_verifier.py::test_a_reopen_during_the_review_keeps_aps_corrections`
- **Lesson:** Don't approve from a copy of the row read minutes earlier.

#### B-34 · A draft in the model's thinking is taken as its answer · medium
- **Where:** `ap_coder/ai/local_llm.py:505` (`read_reply`), used by `verifier.ask_model`
- **Symptom:** a draft JSON in the model's reasoning is taken as the reviewer's answer.
- **Cause:** the fallback `last_json_object(content) or last_json_object(reasoning)`.
- **Reproduce:** prose, empty, or unclosed-thought content, with an all-agree draft in the reasoning.
- **Reproduced:** yes (the draft was returned).
- **Fix:** a `strict=True` option, used by the reviewer: only the answer itself counts.
- **Test:** `tests/test_local_llm.py::test_a_strict_reply_never_takes_a_draft_from_the_thinking`, `tests/test_verifier.py::test_a_draft_in_its_thinking_is_not_its_answer`
- **Lesson:** Don't treat a model's scratch work as its decision.

#### B-35 · A page reading with no finish reason is cached as whole · medium
- **Where:** `ap_coder/ai/page_reader.py:884`
- **Symptom:** a partial page is accepted as complete and cached for good.
- **Cause:** "no finish reason" only counted as a failure when nothing had been written.
- **Reproduce:** a stream of three content chunks with no `finish_reason`.
- **Reproduced:** yes (no error raised).
- **Fix:** a stream that ends with no finish reason and didn't loop raises `PageReaderError(temporary=True)`; nothing is cached and it is read again later.
- **Test:** `tests/test_page_reader.py::test_a_reading_that_ends_without_a_finish_reason_is_not_a_whole_page`
- **Lesson:** Don't accept a stream as finished unless the server said so.

#### B-36 · One reviewer call can outrun its time budget · low-medium
- **Where:** `ap_coder/ai/verifier.py:317` (`ask_model`, `_verify`); `ap_coder/ai/inference.py:333` (`_local_create`)
- **Symptom:** one call can run the full 600 s against a 5-minute budget.
- **Cause:** the budget was only checked between rounds.
- **Reproduce:** a fake coder records the per-call timeout.
- **Reproduced:** yes (timeout `None`, so the 600 s default).
- **Fix:** each call gets `max(10 s, time left)`, passed per request when below `AP_LLM_TIMEOUT_SECONDS`.
- **Test:** `tests/test_verifier.py::test_each_call_is_given_only_the_time_left`, `tests/test_local_llm.py::test_a_call_given_less_time_than_the_timeout_gets_only_that`
- **Lesson:** Don't check a time budget only between blocking calls.

#### B-37 · One unrelated 400 turns off structured output · medium-low
- **Where:** `ap_coder/ai/local_llm.py:386` (`rejects_format`)
- **Symptom:** a 400 "Failed to load model" switches off structured output for that model until restart.
- **Cause:** every 400 or 422 was read as "this server refuses `response_format`".
- **Reproduce:** a fake client that always raises 400 "Failed to load model".
- **Reproduced:** yes (modes tried `json_schema`, `json_object`, `text`).
- **Fix:** a 400, 422 or 500 counts as a format refusal only when the message names `response_format`, `json_schema`, `json schema`, `json_object` or `grammar`.
- **Test:** `tests/test_local_llm.py::test_a_400_that_is_not_about_the_format_keeps_structured_output`
- **Lesson:** Don't infer a capability from an error that didn't name it.

#### B-38 · Prompts have no size budget · medium
- **Where:** `ap_coder/ai/inference.py:444-485` (`suggest_accounts`, `_suggest_batch`); `ap_coder/ai/verifier.py` (`MAX_LINES`, `DESCRIPTION_CHARS`)
- **Symptom:** prompts of about 90k characters (GL suggestions) and about 77k (reviewer) for 300 lines.
- **Cause:** prompts were built with no length budget.
- **Reproduce:** 300 lines, a 2,000-account chart, a long history.
- **Reproduced:** yes (reviewer prompt 81,871 characters).
- **Fix:** GL suggestions go in batches of 40 lines (200 at most), descriptions cut to 160 characters, history to 3,000, the chart to half of `AP_LLM_MAX_PROMPT_CHARS`. The reviewer sends a person any invoice over 60 lines.
- **Test:** `tests/test_local_llm.py::test_a_long_invoice_is_suggested_within_the_prompt_budget`, `::test_the_reviewers_prompt_is_bounded`
- **Lesson:** Don't build a prompt from data without a length budget.

#### B-39 · Internet addresses written another way pass the offline guard · low
- **Where:** `ap_coder/core/config.py:72-97` (`_address`, `_on_this_network`)
- **Symptom:** `134744072`, `0x08080808` and `[64:ff9b::808:808]` (all 8.8.8.8) pass the offline guard.
- **Cause:** hosts were classified by how they are spelled.
- **Reproduce:** call `refused_address` on those URLs.
- **Reproduced:** yes (three of six forms were not refused).
- **Fix:** IPv4 is parsed in every `inet_aton` form by hand; NAT64 is refused; IPv4-mapped addresses are judged by the address they carry. Local and LAN addresses are still allowed.
- **Test:** `tests/test_local_llm.py::test_an_internet_address_written_another_way_is_still_refused`, `::test_lm_studio_on_this_computer_or_the_lan_is_still_allowed`
- **Lesson:** Don't classify hosts by their spelling; parse them the way the resolver will.

#### B-40 · A thinking tag inside a JSON string breaks the parse · low
- **Where:** `ap_coder/ai/local_llm.py:591` (`_whole_object`, used by `parse_json_reply`)
- **Symptom:** `</think>` or `<think>` inside a JSON string gives None or a cut-off string.
- **Cause:** markup was regex-stripped before parsing.
- **Reproduce:** `parse_json_reply('{"reason": "model said </think> here", "a": 2}')`.
- **Reproduced:** yes (None, or a cut-off string).
- **Fix:** the reply is first tried as one whole JSON object, as written or after a leading thought block; the old path is the fallback.
- **Test:** `tests/test_local_llm.py::test_a_thinking_tag_inside_a_json_string_is_kept`
- **Lesson:** Don't regex-strip markup out of text that might be JSON.

#### B-41 · A cut-off reply returns its first line item as the answer · low
- **Where:** `ap_coder/ai/local_llm.py:499-505` (`_unclosed`, `read_reply`)
- **Symptom:** a reply cut off with finish reason "stop" returns line 1's object as the answer.
- **Cause:** the `last_json_object` fallback undid `parse_json_reply`'s guard against inner objects.
- **Reproduce:** `read_reply` on `{"lines": [{…}, {"line_num` with `finish_reason="stop"`.
- **Reproduced:** yes (line 1's object came back).
- **Fix:** an answer whose first object never closes returns no data and the problem "the reply's JSON was cut off before it closed".
- **Test:** `tests/test_local_llm.py::test_a_reply_cut_off_without_the_length_reason_is_not_read_as_its_first_line`
- **Lesson:** Don't let a fallback parser undo the guard the main parser enforces.

### Document capture

#### B-42 · A French "AVOIR" printed positive is approved as an invoice · high
- **Where:** `ap_coder/capture/reader.py:990` (`_CREDIT_TITLE`), `:993` (`credit_titled`); `capture/normalize.py:543` (`CREDIT_TITLES`); `ap_coder/intake/triage.py:71`; `capture/supplier.py:1364`
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
- **Where:** `ap_coder/ai/evaluation.py:27` (`_norm_text`)
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
- **Where:** `ap_coder/core/store.py:1752-1764` (`undo_export_batch`); `ap_coder/web/pages/exports.py:358`
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
- **Where:** `ap_coder/intake/msgfile.py:159` (`stream`)
- **Symptom:** a 1.2 MB .msg held 201 MB in memory.
- **Cause:** each stream was checked against the file size, but the total was never bounded, and many entries can point at the same sectors.
- **Reproduce:** a .msg with one 40 KB attachment and 10 small ones whose data entries all point at the big one; `read_msg`.
- **Reproduced:** yes (no MsgError raised).
- **Fix:** `stream()` keeps a running total and raises MsgError past 2× file size + 64 KB.
- **Test:** `tests/test_msg_and_triage.py::test_attachments_sharing_one_stream_cannot_fill_the_memory`
- **Lesson:** Don't bound each piece and forget the sum.

#### B-62 · The .msg directory walk is quadratic · high
- **Where:** `ap_coder/intake/msgfile.py:101-106, 138` (`_children`)
- **Symptom:** the walk grew with the square of the entry count (8,000 entries: 8 s and 1.1 GB).
- **Cause:** each storage walked its children with its own visited set.
- **Reproduce:** a CFB with 200 storages whose child is the same 200-entry sibling chain.
- **Reproduced:** yes (40,275 visits instead of at most 404).
- **Fix:** one visited set for all storages, each storage added so it is never its own child.
- **Test:** `tests/test_msg_and_triage.py::test_a_directory_walk_reads_each_entry_once`
- **Lesson:** Don't put a per-call limit on a walk that runs once per node.

#### B-63 · A spreadsheet zip bomb is opened · high
- **Where:** `ap_coder/intake/triage.py:363`
- **Symptom:** a small .xlsx unzipped to hundreds of MB inside openpyxl.
- **Cause:** the zip went to openpyxl with no check of its unzipped sizes.
- **Reproduce:** an .xlsx under 100 KB whose `sharedStrings.xml` unzips to 25 MB.
- **Reproduced:** yes (openpyxl was called).
- **Fix:** `zipfile` checks first: one member over 20 MB, or 50 MB in all, returns "" (`MAX_SHEET_PART_BYTES`, `MAX_SHEET_BYTES`).
- **Test:** `tests/test_msg_and_triage.py::test_a_spreadsheet_that_unzips_to_far_more_is_not_opened`
- **Lesson:** Don't hand an untrusted zip to a library before checking its unzipped sizes.

#### B-64 · A long Chinese name sticks an email forever · medium
- **Where:** `ap_coder/intake/mailbox.py:245` (`_joined`, used by `_save`)
- **Symptom:** an email with a long Chinese name and attachment name was never sorted and was retried silently on every check.
- **Cause:** each part of the file name was capped, but the joined name was not (259 bytes, over 255).
- **Reproduce:** a 40-character Chinese email name plus a 56-character Chinese attachment name.
- **Reproduced:** yes (`OSError: [Errno 36] File name too long`).
- **Fix:** the joined name is capped at 200 bytes by trimming the attachment's stem. An OSError while writing is still "try again later" (a read-only folder is a real reason).
- **Test:** `tests/test_mailbox.py::test_a_long_chinese_email_and_attachment_name_still_fit_one_file_name`
- **Lesson:** Don't size-cap the parts of a file name and then join them uncapped.

#### B-65 · An email that can't be moved is logged on every check · medium
- **Where:** `ap_coder/intake/mailbox.py:327-329`; `ap_coder/intake/inbox.py:118`
- **Symptom:** an email held open (e.g. by Outlook) got a new `triage.jsonl` line and audit event on every check.
- **Cause:** `_record` and `log_sorted` ran even when the move failed.
- **Reproduce:** make `Path.replace` raise PermissionError and unpack the same email 3 times.
- **Reproduced:** yes (3 log entries).
- **Fix:** a new `Unpacked.left_in_place` flag skips `_record` and the event; the email is logged once, after it moves.
- **Test:** `tests/test_mailbox.py::test_an_email_that_cannot_be_moved_is_logged_once`
- **Lesson:** Don't log "done" when the step that makes it done failed.

#### B-66 · `watch` watches a different folder from the dashboard's · medium
- **Where:** `ap_coder/ops/cli.py:166, 415`
- **Symptom:** with `AP_DB_PATH` or `--db`, `watch` watched `private/invoices` instead of the dashboard's folder.
- **Cause:** the default folder was worked out separately from the database path.
- **Reproduce:** `AP_DB_PATH=<tmp>/srv/ap_coder.db`, then `watch --once`.
- **Reproduced:** yes (`…/private/invoices != …/srv/invoices`).
- **Fix:** the folder defaults to None and `cmd_watch` uses `store.path.parent / "invoices"`, as the dashboard does.
- **Test:** `tests/test_config_and_cli.py::test_watch_uses_the_dashboard_invoices_folder`
- **Lesson:** Don't work out the same path two ways.

#### B-67 · Doctor passes damaged OCR models · medium
- **Where:** `ap_coder/ops/offline.py:371`
- **Symptom:** doctor said PASS for damaged PP-OCRv5 models that the app itself won't use.
- **Cause:** `missing_models()` was called without `verify=True`.
- **Reproduce:** one model file with the right name and the wrong SHA-256.
- **Reproduced:** yes (PASS instead of WARN).
- **Fix:** `missing_models(verify=True)`; the message says "missing or damaged".
- **Test:** `tests/test_offline.py::test_doctor_checks_the_model_files_as_the_app_does`
- **Lesson:** Don't let the doctor check less than the code it vouches for.

#### B-68 · Doctor treats a missing Pillow as optional · medium
- **Where:** `ap_coder/ops/doctor.py:85`
- **Symptom:** a missing Pillow was a WARN "needed only for --vision", with exit code 0.
- **Cause:** the wording was left behind when Pillow became a core dependency (photos and scans need it).
- **Reproduce:** make `_version("pillow")` return None.
- **Reproduced:** yes (WARN instead of FAIL).
- **Fix:** Pillow is in `_PACKAGES` as a FAIL "(photos and scanned images can't be read)".
- **Test:** `tests/test_config_and_cli.py::test_doctor_fails_without_pillow`
- **Lesson:** Don't leave doctor wording behind when a dependency's role grows.

#### B-69 · A file stamped in the future is never picked up · medium
- **Where:** `ap_coder/ops/cli.py:349`; `ap_coder/intake/mailbox.py:374`
- **Symptom:** a file stamped ahead of the clock was skipped until the clock caught up.
- **Cause:** `now - mtime < SETTLE` is also true when the difference is negative.
- **Reproduce:** set a file's mtime one hour ahead.
- **Reproduced:** yes (the file was not picked up; the .eml case too).
- **Fix:** `abs(now - mtime) < SETTLE_SECONDS`; a file within 5 s ahead still counts as copying.
- **Test:** `tests/test_controls_watch.py::test_a_file_stamped_in_the_future_is_picked_up`, `tests/test_mailbox.py::test_an_email_stamped_in_the_future_is_unpacked`
- **Lesson:** Don't assume a file's timestamp is in the past.

#### B-70 · The CLI exits 0 after printed failures · low
- **Where:** `ap_coder/ops/cli.py:249-284, 297, 317, 332, 386-430, 486, 548-570`
- **Symptom:** `process`, `extract`, `read-pages` and `watch --once` exited 0 after printing failures.
- **Cause:** errors weren't carried through to the exit code.
- **Reproduce:** `process bad.msg good.pdf`; `watch --once` with only a bad email.
- **Reproduced:** yes (exit 0 instead of 1). The review had only read it in the code.
- **Fix:** an unreadable email makes `process` and `extract` return 1; `extract` with nothing to read returns 2; `read-pages` returns 1 on a failed read; `watch --once` returns 1 when everything failed.
- **Test:** `tests/test_config_and_cli.py::test_a_command_that_could_not_read_something_does_not_exit_0`, `tests/test_controls_watch.py::test_watch_once_fails_when_every_email_could_not_be_read`
- **Lesson:** Don't print an error and then return success.

#### B-71 · A big .eml is parsed before the part cap applies · low-medium
- **Where:** `ap_coder/intake/mailbox.py:160`
- **Symptom:** an email of thousands of tiny parts was fully parsed (about 86 µs a part) before the 500-part cap.
- **Cause:** the limit was applied after the costly parse.
- **Reproduce:** `read_eml` on a 6,000-part email.
- **Reproduced:** yes (it was parsed).
- **Fix:** more than `MAX_BOUNDARY_LINES` (5,000) boundary lines raises UnreadableEmail before parsing; the email goes to "could not read".
- **Test:** `tests/test_mailbox.py::test_an_email_of_thousands_of_tiny_parts_is_refused_before_it_is_parsed`
- **Lesson:** Don't apply the limit after the costly parse.

#### B-72 · `watch --once` skips invoices it just took out of emails · low
- **Where:** `ap_coder/ops/cli.py:378-389` (`new_files(..., fresh=...)`)
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

## Sweep 2 (after the codebase map), October 2026

Each section was reviewed again once the codebase map was written, with its CLAUDE.md checked against the code. Each
finding was checked again, given a failing test, and fixed. The sweep-1 fixes were re-checked too, and several entries
below follow up on them (B-04, B-10, B-15, B-25, B-43, B-46, B-48, B-49, B-50, B-62, B-63). Line numbers are those of
the code at the time of the sweep.

| Section | Found | Reproduced and fixed | Not reproduced / left |
|---|---|---|---|
| Store and config | 12 | 6 (B-73 to B-78) | 6 (N-32 to N-37) |
| Tax and coding rules | 9 | 6 (B-79 to B-84) | 3 (N-38 to N-40) |
| Exports, ERP and reports | 14 | 8 (B-85 to B-92) | 6 (N-41 to N-46) |
| AI and readers | 9 | 5 (B-93 to B-97) | 4 (N-47 to N-50) |
| Document capture | 13 | 7 (B-98 to B-104) | 6 (N-51 to N-56) |
| Web routes | 9 | 3 (B-105 to B-107) | 6 (N-57 to N-62; N-57 done since, D-34) |
| Front end | 13 | 8 (B-108 to B-115) | 5 (N-63 to N-67) |
| Intake, CLI and launch | 11 | 3 (B-116, B-117, B-128) | 8 (N-68 to N-75) |
| Codebase map tooling, CI and tests | 10 | 9 (B-118 to B-125, B-127) | 1 (N-76) |
| Repeated lessons made into checks | 1 | 1 (B-126) | 0 |
| Map review of the per-person identity (W2-02) | 2 | 2 (B-129, B-130) | 0 |
| The review gate on Windows CI | 1 | 1 (B-131) | 0 |
| **Total** | **104** | **59** | **45** |

### Store and config

#### B-73 · An on-hold vendor stops being on hold after a vendor-name rules update · medium
- **Where:** `ap_coder/core/store.py:477-488` (`_recompute_vendor_keys`), fix in `_merge_vendor_rows` (:507)
- **Symptom:** after an update to the vendor-name rules, a vendor on hold silently stops being on hold, and its invoices no longer get `VENDOR_ON_HOLD`.
- **Cause:** the B-15 re-key used `UPDATE OR IGNORE`. When the new key was already taken, the on-hold row stayed under its old key, which no invoice produces any more.
- **Reproduce:** vendor rows `acme` (active) and `acme ltee` (on hold), the database marked as on the older rules (`vendor_key_rules='1'`), then open the store again.
- **Reproduced:** yes (rows `[('acme','active'), ('acme ltee','on_hold')]`; an "Acme Ltée" invoice found the active row and no findings).
- **Fix:** when the new key is taken, the two rows are merged: on hold wins if either row is on hold, both notes are kept, a blank field is filled from the other row, `in_master` takes the larger value, and the old row is deleted.
- **Test:** `tests/test_memory_store.py::test_rekeyed_vendor_rows_merge_and_on_hold_wins`
- **Lesson:** Don't resolve a key collision by keeping one row when the other row holds a control.

#### B-74 · French decimals such as "0,125" are read as thousands · medium-low
- **Where:** `ap_coder/core/safe.py:51-93` (`parse_amount`, new `_thousands`)
- **Symptom:** `"0,125"` became 125, `"-0,500"` -500, `"1,2345"` 12345, `"12.50.00"` 125000 and `"01.02.2026"` 1022026. A PO line with unit price `0,125` was stored at 125.
- **Cause:** a comma followed by three digits was always read as a thousands separator, without checking the digit groups.
- **Reproduce:** `parse_amount` on those strings; a PO import with unit price `0,125`, quantity `10 000` and amount `1 250,00`.
- **Reproduced:** yes (`'unit_price': 125.0`).
- **Fix:** a separator is thousands only when the first group is 1-3 digits not starting with 0 and every later group is exactly 3 digits. Otherwise a single comma is the decimal mark, and several separators return None. A single dot stays a decimal point (`"1.234"` is still 1.234).
- **Test:** `tests/test_safety.py::test_parse_amount_reads_thousands_only_from_real_thousands_groups`
- **Lesson:** Don't decide "thousands separator" without checking the groups are real thousands.

#### B-75 · Logged actions missing from the Activity page and the controls report · low-medium
- **Where:** `ap_coder/core/audit.py:30-63` (`ACTIONS`, `describe`); `ap_coder/erp/controls.py:26-32` (`SETUP_ACTIONS`); `ap_coder/core/store.py:3017` (`delete_vendors`); `ap_coder/web/pages/activity.py:27-39`
- **Symptom:** nine logged actions (vendor_added, verified, azure_checked, azure_check_disagrees, lesson_explained, lesson_retired, lesson_restored, directory_preset_saved, directory_preset_deleted) showed as a raw key with no description. The controls report said "None." for setup changes after a vendor was added, the ERP register was cleared, an invoice was deleted, history was imported or a lesson was retired. Deleting vendors logged nothing.
- **Cause:** the hand-kept `ACTIONS` and `SETUP_ACTIONS` lists had drifted from the actions the code logs, and `delete_vendors` had no `_log` call.
- **Reproduce:** log `vendor_added`, then `audit.describe`; build the controls report over a period with those setup events.
- **Reproduced:** yes (`describe` gave ""; the setup section stayed empty). Found by both the store and the exports reviews.
- **Fix:** labels and descriptions for the nine actions; `delete_vendors` takes an optional `actor` and logs `vendors_deleted`; `SETUP_ACTIONS` adds vendor_added, vendors_deleted, erp_register_imported, deleted, history_imported, lesson_retired and lesson_restored; the new actions are in the Activity page's filter groups.
- **Test:** `tests/test_audit.py::test_every_logged_action_has_a_label_and_a_description`, `tests/test_controls_watch.py::test_controls_setup_section_counts_every_setup_change`
- **Lesson:** Don't keep a hand list of audit actions apart from the actions the store logs.

#### B-76 · An ERP history date with a time is cut at 10 characters · low
- **Where:** `ap_coder/ai/history.py:47-64`
- **Symptom:** a history date like `1/25/2026 0:00` was stored cut short, so the same line imported again from an export with ISO dates was not seen as a duplicate and was taught twice.
- **Cause:** `row["date"][:10]`.
- **Reproduce:** import a history line dated with a time, then the same line with an ISO date.
- **Reproduced:** yes (two rows, `['2026-01-05', '1/5/2026 0']`).
- **Fix:** dates go through `statements.read_date` with the file's day/month order (`day_first_order`), as registers.py does. A date that can't be read, or an ambiguous one like `1/5/2026` with nothing else in the file, is kept as typed.
- **Test:** `tests/test_history.py::test_history_dates_are_read_not_cut_at_ten_characters`
- **Lesson:** Don't cut a date at 10 characters.

#### B-77 · The labels workbook rounds quantities and unit prices · low
- **Where:** `ap_coder/ai/labels.py:71-75` (`_fmt_num`)
- **Symptom:** in both xlsx and csv, unit price 0.125 came back as 0.12 and quantity 41.237 as 41.24. No effect on scores yet: `evaluate` scores only `amount`.
- **Cause:** every number was written with `.2f`.
- **Reproduce:** write and read back labels with those values.
- **Reproduced:** yes, for both formats.
- **Fix:** whole numbers are written as before; other numbers are written exactly, with no rounding.
- **Test:** `tests/test_ops_sweep_fixes.py::test_labels_keep_quantities_and_unit_prices_unrounded`
- **Lesson:** Don't round ground truth on the way out.

#### B-78 · A coding policy saved with a byte-order mark turns its comment into a rule · low
- **Where:** `ap_coder/core/store.py:3536`, `ap_coder/core/reference_data.py:208`
- **Symptom:** a `coding_policy.md` saved with a byte-order mark sent its `#` comment line to the AI prompt as a rule.
- **Cause:** both readers opened the file as `utf-8`, so the mark stayed at the start of the first line.
- **Reproduce:** save the policy with a BOM, then read the notes.
- **Reproduced:** yes (`['﻿# Coding policy notes', 'Rule one']`).
- **Fix:** both readers use `utf-8-sig`.
- **Test:** `tests/test_reference_data.py::test_coding_policy_saved_with_a_byte_order_mark_keeps_its_comment_line`
- **Lesson:** Don't read a file people save in Notepad without `utf-8-sig`.

### Tax and coding rules

#### B-79 · A worked-out place of supply is frozen and shown as printed · medium
- **Where:** `ap_coder/ai/pipeline.py:122-125` (`finalise_coding`); `ap_coder/coding/tax.py` (`PLACE_OF_SUPPLY`, `stored_supply`); `ap_coder/core/schema.py` (`InvoiceCoding`); `ap_coder/web/invoice_view.py:394`
- **Symptom:** lines recoded to a cost center in another province kept the old place of supply in the tax checks, the stored output and the self-assessment, and the review screen said it came from "the ship-to on the invoice". Lines recoded from an AB site to a BC site never flagged BC PST.
- **Cause:** with no ship-to printed, `finalise_coding` wrote the province it worked out into `ship_to_province`. The form sent it back, and `supply_source` then took it as printed.
- **Reproduce:** our province AB, site CC400=BC; a sample GST invoice with no ship-to coded to CC400, then recoded to CC100 (and the reverse).
- **Reproduced:** yes (BC kept with `PROVINCIAL_TAX_NOT_CHARGED`; the reverse stayed AB with no warning).
- **Fix:** the worked-out province is stored beside the coding as `place_of_supply`, never in `ship_to_province`, and is worked out again on every check. `InvoiceCoding` drops it when a stored invoice is read, `tax.stored_supply` reads it from a stored invoice, and the review screen says where it came from.
- **Test:** `tests/test_tax.py::test_a_worked_out_place_of_supply_follows_the_recoded_lines`
- **Lesson:** Don't store a value you worked out in the field that means "printed on the invoice".

#### B-80 · Bulk approval approves a new vendor's first invoice unseen · medium
- **Where:** `ap_coder/coding/bulk.py:28` (`clean_candidates`), `:63` (`bulk_approve`)
- **Symptom:** an invoice whose only finding is `VENDOR_NEW` ("never touchless") was listed as clean and bulk-approved.
- **Cause:** bulk.py skipped only errors and warnings. `capture/supplier.ALWAYS_A_PERSON` holds info-level codes too, and bulk.py never read it.
- **Reproduce:** an invoice whose only issue is `('info','VENDOR_NEW')`, then `clean_candidates` and `bulk_approve`.
- **Reproduced:** yes (listed, then `approved`).
- **Fix:** both skip any finding whose code is in `ALWAYS_A_PERSON`, whatever its severity.
- **Test:** `tests/test_exports_bulk.py::test_bulk_approve_never_takes_a_check_that_always_needs_a_person`
- **Lesson:** Don't treat "no warnings" as "safe to approve unseen" when some person-only checks are info-level.

#### B-81 · A vendor's hold is lost when its GST number matches a master vendor · medium-low
- **Where:** `ap_coder/coding/vendors.py:249-251` (`vendor_findings`)
- **Symptom:** a name AP put on hold, not in the ERP vendor master, on an invoice printing a master vendor's GST number, showed only `VENDOR_MATCHED_BY_TAX_NUMBER`. The hold and its note were not shown.
- **Cause:** `master = by_number[0]` replaced the vendor's own row, and only the replaced row was checked for the hold.
- **Reproduce:** put a name on hold, then an invoice under that name with a master vendor's GST number.
- **Reproduced:** yes (`['VENDOR_MATCHED_BY_TAX_NUMBER']`; with an unknown number `['VENDOR_NOT_IN_MASTER', 'VENDOR_ON_HOLD']`).
- **Fix:** the vendor's own row is kept apart, and a hold on it or on the matched master vendor raises `VENDOR_ON_HOLD` with both notes.
- **Test:** `tests/test_vendors.py::test_a_hold_on_the_name_survives_a_match_by_tax_number`
- **Lesson:** Don't reuse one variable for two records when each carries its own controls.

#### B-82 · The math check flags a correct yen invoice · low
- **Where:** `ap_coder/coding/taxmath.py:43-46` (`math_check`)
- **Symptom:** a JPY invoice charging 5% GST on 1,234 prints 62. `check_taxes` passed it, but `math_check` reported "1 difference: GST (0.30)", and the AI reviewer was given that as a math failure.
- **Cause:** `math_check` rounded to cents with a cent of tolerance; `check_taxes` uses `currency_places`.
- **Reproduce:** that invoice through both checks.
- **Reproduced:** yes.
- **Fix:** `math_check` rounds with `tax.currency_places(coding.currency)`, as `check_taxes` does.
- **Test:** `tests/test_math_check.py::test_a_yen_invoice_is_rounded_to_whole_yen_as_the_tax_checks_do`
- **Lesson:** Don't re-implement a check with different rounding rules from the one it mirrors.

#### B-83 · An interest rate in the same clause cancels the early-payment discount · low
- **Where:** `ap_coder/coding/terms.py:138` (`parse_terms`, `_clause`); fix in new `_discount_clause`
- **Symptom:** "2/10 net 30 interest 1.5% per month on overdue accounts" was read with no discount, so `DISCOUNT_AVAILABLE` never came. With " - " before "interest" the discount was found.
- **Cause:** with no punctuation, the whole text was one clause, and "interest" in it vetoed the discount.
- **Reproduce:** `parse_terms` on that text.
- **Reproduced:** yes.
- **Fix:** when another rate follows in the same clause, the text checked for interest words stops at the discount.
- **Test:** `tests/test_terms.py::test_interest_words_of_another_rate_do_not_veto_the_discount`
- **Lesson:** Don't veto a match using words that belong to another clause.

#### B-84 · A tax rate of "nan" loads · low
- **Where:** `ap_coder/coding/tax.py:128` (`TaxRateTable`)
- **Symptom:** a rate of `nan` or `inf` in `canada_tax_rates.csv` loaded (as `rate=nan`) instead of stopping the load like any other non-number.
- **Cause:** `float()` reads "nan" and "inf".
- **Reproduce:** a rate row with `nan`, then load the table.
- **Reproduced:** yes (found by the store review while checking the data map).
- **Fix:** a rate that is not a finite number stops the load with "rate ... is not a number".
- **Test:** `tests/test_tax.py::test_a_rate_that_is_not_a_finite_number_is_refused`
- **Lesson:** Don't take `float()` succeeding as proof that the text is a number.

### Exports, ERP and reports

#### B-85 · The JDE dry run shows "gross 0.00" in tax-amounts mode · medium
- **Where:** `ap_coder/erp/jde.py:987` (`dry_run`, new `_gross_of`)
- **Symptom:** with "Taxable / non-taxable / tax amounts" chosen, DRY_RUN.txt showed "gross 0.00" for an 18,017.85 invoice; for a foreign currency the gross was blank.
- **Cause:** that mode writes no VLAG/VLACR column, so the dry run fell back to VLATXN (only the non-taxable part), and to the domestic column even for a foreign invoice.
- **Reproduce:** `jde.dry_run` with `amount_mode=AMOUNT_TAX` on the ON sample, in CAD and then in USD.
- **Reproduced:** yes (`PV gross 0.00, tax 2,072.85`; USD `gross , tax 2,072.85`).
- **Fix:** the gross is VLAG/VLACR when it is there; otherwise ATXA + ATXN + STAM, from the VLC* columns for a foreign currency.
- **Test:** `tests/test_jde_readiness.py::test_the_dry_run_gross_is_the_whole_invoice_in_every_amount_mode`
- **Lesson:** Don't show a column under a label it doesn't mean.

#### B-86 · A statement's payment line with the invoice's number is "not received" · medium
- **Where:** `ap_coder/erp/statements.py:177`
- **Symptom:** a statement row "1001 / Payment - thank you / -500.00" came out as Not received -500, that is "ask the vendor for a copy".
- **Cause:** since the B-25 fix, a payment word in a free-text column counted only when no invoice had that number. Statements usually print the invoice number on its payment line.
- **Reproduce:** a statement with Invoice, Description and Amount columns: invoice 1001 for 500.00, then that payment row.
- **Reproduced:** yes (`not_received 1001 -500.0`).
- **Fix:** a line with a payment word and an invoice's number is a payment when its sign is the opposite of every invoice with that number; with no amount, only when those invoices are all matched already. "Already matched" alone is not used when there is an amount, so a bill sent twice is still flagged.
- **Test:** `tests/test_statements.py::test_a_payment_line_with_the_invoices_number_is_a_payment`
- **Lesson:** Don't fix one misreading of free text by trusting another field without the sign.

#### B-87 · A JDE batch downloaded again fails on a due date JDE can't hold · medium
- **Where:** `ap_coder/erp/jde.py:584` (`voucher_rows`)
- **Symptom:** after "send due date" was switched on, downloading again a batch with an invoice due 3026-10-14 gave a 500.
- **Cause:** a JDE batch is not re-checked on download (B-21), and `julian()` raises for years outside 1900-2899.
- **Reproduce:** a batch made with send due date off and a 3026-10-14 due date; switch the option on; download again.
- **Reproduced:** yes (`ValueError 3026-10-14 is outside what JD Edwards dates hold`).
- **Fix:** VLDDJ is left blank when the year is out of range. `validate` already lists the date under "check before loading" in the README.
- **Test:** `tests/test_jde.py::test_a_due_date_jde_cannot_hold_leaves_vlddj_blank_in_a_batch_downloaded_again`
- **Lesson:** Don't let a file that skipped re-checking hit code that assumes the checks ran.

#### B-88 · A JDE batch downloads while a setting stops every export · low-medium
- **Where:** `ap_coder/web/pages/exports.py:84` (`download_batch`)
- **Symptom:** after the User ID was cleared, a JDE batch downloaded again with a blank VLEDUS. Its DRY_RUN.txt said "NOTHING CAN LOAD", and README.txt said nothing.
- **Cause:** `settings_problems` was never checked on downloads.
- **Reproduce:** make a batch, clear the User ID, download it again.
- **Reproduced:** yes (200 with the bad file).
- **Fix:** the download answers 409 with each settings problem and "Set it in Settings → JD Edwards E1, then download the batch again.", as downloads already refuse with 404 and 410. The same batch as xlsx or csv still downloads.
- **Test:** `tests/test_jde.py::test_a_jde_batch_is_not_downloaded_while_a_setting_stops_every_export`
- **Lesson:** Don't produce a file that its own report says cannot load.

#### B-89 · Directory filters miss accented vendor names · low-medium
- **Where:** `ap_coder/erp/directory.py:182,184`; `ap_coder/core/store.py` (new `fold_text`, registered as `ap_fold` in `_open`)
- **Symptom:** the filters `éclair`, `eclair` and `=éclair s.e.c.` didn't find vendor "Éclair S.E.C."; only `Éclair` or `ÉCLAIR` did. The docstring said accents were ignored.
- **Cause:** SQLite's `LOWER()` and `LIKE` fold only the letters A to Z.
- **Reproduce:** add vendor "Éclair S.E.C.", then filter Vendor with `éclair`.
- **Reproduced:** yes (`[]`).
- **Fix:** store.py registers `ap_fold` (accents removed, case folded, a quick path for plain ASCII) on every connection. directory.py compares `ap_fold(column)` with the typed text, folded the same way before `*` and `?` are read, so a fullwidth ％ can't become SQL's `%`. About 30 ms per filter over 50,000 rows (3 ms before).
- **Test:** `tests/test_directory.py::test_text_filters_ignore_accents_and_case_beyond_ascii`
- **Lesson:** Don't compare non-ASCII text with SQLite `lower()` (B-04 again).

#### B-90 · JDE worksheet codes such as "01" come back as 1 · low-medium
- **Where:** `ap_coder/erp/jde_worksheet.py:270-281` and its four `table(...)` calls
- **Symptom:** a subsidiary or terms code typed `01` by the JDE team came back as `1` (account `CC400.6010.1`, terms `1`).
- **Cause:** the answer cells were in Excel's General format.
- **Reproduce:** write the number 1 in an answer cell, as Excel saves `01` typed there, and read the worksheet back.
- **Reproduced:** yes, simulated (`CC400.6010.1`, `terms: 1`; the cells were `General`).
- **Fix:** the answer and code columns are Text cells (`"@"`): Questions E-F, Tax areas C-F, Account mapping A, B and D-H, Suppliers B-C.
- **Test:** `tests/test_jde_readiness.py::test_the_worksheet_answer_and_code_cells_are_text`
- **Lesson:** Don't collect codes in cells that Excel will turn into numbers.

#### B-91 · The redacted share report can print a file name · low
- **Where:** `ap_coder/ops/share_report.py:39-43` (`_failure_category`)
- **Symptom:** the redacted report printed e.g. `Acme invoice 4471 truncated.pdf can't be opened as a PDF/output_truncated`.
- **Cause:** when a keyword matched, the text before the first colon was printed without checking it was an exception name, and `plain_error` messages start with the file name.
- **Reproduce:** `_failure_category` on a plain-words error that starts with a file name.
- **Reproduced:** yes.
- **Fix:** that text is printed only when `isidentifier()` holds; otherwise only the category is printed.
- **Test:** `tests/test_working_together.py::test_share_report_failure_category_never_prints_a_file_name`
- **Lesson:** Don't put back raw text in the one branch after redacting it in the others.

#### B-92 · The ERP CSV files get Excel apostrophes · medium
- **Where:** `ap_coder/erp/exports.py:177` (`build_csv`), `ap_coder/erp/export_layout.py:109` (`build`)
- **Symptom:** in the CSV and Custom CSV files meant for an ERP import, invoice `+A-100` became `'+A-100` and `- Labour` became `'- Labour`, so the ERP's supplier + invoice-number duplicate check missed the invoice.
- **Cause:** both files passed every cell through the spreadsheet formula quoting in `safe.py` (`csv_row` / `csv_cell`), although they go to a database loader (the same mistake as B-26 in two more files).
- **Reproduce:** export one invoice numbered `+A-100` with a line `- Labour` as CSV and as Custom CSV.
- **Reproduced:** yes (`'+A-100,'- Labour` in both files).
- **Fix:** both files hold text exactly as typed, like the JDE Z-files; the xlsx export and the files for people (search, directory, statements, accruals, reports) keep their formula protection. Decided by the user; docs/decisions.md D-28 updated.
- **Test:** `tests/test_exports_bulk.py::test_erp_csv_keeps_text_exactly_as_typed`, `tests/test_export_layout.py::test_custom_csv_keeps_text_exactly_as_typed`
- **Lesson:** Don't apply Excel quoting to files meant for a database (B-26 again).

### AI and readers

#### B-93 · Azure comparing nothing approves touchless · high
- **Where:** `ap_coder/ai/azure_check.py:649` (`_fold_in`)
- **Symptom:** an invoice held for the Azure check was approved touchless when Azure compared none of its fields; the summary even said "Azure read none of the fields AP Coder read".
- **Cause:** approval needed only "nothing differs", and an empty comparison has nothing that differs. It happens with the model set to `prebuilt-layout` or `prebuilt-read`, or when Azure returns no documents.
- **Reproduce:** set the Azure model to `prebuilt-layout` and check an invoice held for Azure.
- **Reproduced:** yes (`CheckOutcome(status='done', ..., differ=[], approved=True)`).
- **Fix:** approval also needs at least one field compared. With nothing compared, `azure_hold` stays and a new issue, `AZURE_DI_NOT_COMPARED`, is added (by `prepare()` too when the page reader reads the invoice again). `update_after_check` replaces both Azure issue codes. The new code has a help entry.
- **Test:** `tests/test_azure_check.py::test_nothing_compared_is_not_nothing_differs`
- **Lesson:** Don't treat "nothing compared" as "nothing differs".

#### B-94 · A server outage closes the AI review as "unsure" · medium
- **Where:** `ap_coder/ai/verifier.py:283` (`review`), `:561` (`_postpone_or_fail`), `_verify`; `ap_coder/ai/inference.py` (`CodingError`); `ap_coder/core/store.py` (`verifications.tries`)
- **Symptom:** when LM Studio stopped or a call timed out, the review was finished as done/unsure: the hold was dropped, VERIFIER_UNSURE added, and the invoice never reviewed again. The docstring promised it stays in line.
- **Cause:** every exception from `ask` became an UNSURE result. Since B-36 each call is capped at the time left (5 min by default), which a thinking model with 3 page images on a laptop CPU can pass.
- **Reproduce:** make the coder's `_local_create` raise a connection error, then a timeout, during the review.
- **Reproduced:** yes for a server not answering (`outcome: unsure | queue row: done | issues: ['VERIFIER_UNSURE'] | verifier_hold: None`); the timeout case only by injecting a timeout into `_local_create`.
- **Fix:** `CodingError` gets a `temporary` flag, set for a timeout or a connection failure. `review()` re-raises it on the first question; after a round already answered it is still "unsure". `_verify` puts the invoice back in line with the hold kept and no VERIFIER issue, and after `MAX_TRIES = 3` marks it failed so AP has it checked again. A new `verifications.tries` column (an ALTER for existing databases) is reset by a finished review and by `queue_verification`.
- **Test:** `tests/test_verifier.py::test_the_servers_outage_is_not_the_reviewers_verdict`, `tests/test_verifier.py::test_an_outage_on_the_first_question_is_not_an_answer`
- **Lesson:** Don't record the server's outage as the reviewer's verdict.

#### B-95 · The AI reviewer is never shown the math · medium
- **Where:** `ap_coder/ai/verifier.py:165` (`proposal`)
- **Symptom:** the reviewer's prompt never held "the math worked out again", though `SYSTEM` and `question()` say it does.
- **Cause:** the stored `ai_output` includes `gl_distribution`, which `InvoiceCoding` (`extra="forbid"`) rejects, and the error was swallowed by a bare `pass`. Every other caller strips that key first.
- **Reproduce:** process the sample PDF, then `proposal(<stored ai_output>, None)`.
- **Reproduced:** yes (`math` was None; without that key, 3 math rows).
- **Fix:** `gl_distribution` is stripped before `model_validate`, and the bare `pass` is a `log.warning`.
- **Test:** `tests/test_verifier.py::test_the_reviewer_is_shown_the_math`
- **Lesson:** Don't silence a helper's exception without a log line.

#### B-96 · A draft after "Thinking Process:" is taken as a strict answer · low
- **Where:** `ap_coder/ai/local_llm.py:528` (`read_reply`, strict)
- **Symptom:** with `strict=True` (the reviewer, since B-34), a draft JSON inside an unclosed "Thinking Process:" block was returned as the answer. The same text inside an unclosed `<think>` was refused.
- **Cause:** strict mode still used `parse_json_reply`, which finds the first object anywhere in prose.
- **Reproduce:** `read_reply(..., strict=True)` on "Thinking Process: … {draft} … wait".
- **Reproduced:** yes (the draft dict came back instead of None).
- **Fix:** strict mode accepts only `_whole_object(content)`, still read after a closed or stray-closed thought block and through code fences. JSON inside prose is refused, which gives "unsure", not an approval. Non-strict reading is unchanged.
- **Test:** `tests/test_local_llm.py::test_a_strict_reply_is_the_whole_answer_not_json_found_in_prose`
- **Lesson:** Don't let the strict path fall back to scanning prose for JSON.

#### B-97 · The Azure disagreement has no help entry · low
- **Where:** `ap_coder/ops/help.py` (`CHECKS`); `tests/test_help.py` (`_raised_codes`)
- **Symptom:** `AZURE_DI_DISAGREES` (Azure read a field differently) had no help entry, and `tests/test_help.py` still passed.
- **Cause:** the code is raised through a constant (`Issue(WARNING, ISSUE_CODE, ...)`), and the test's pattern matched only a code written as a string.
- **Reproduce:** look up `AZURE_DI_DISAGREES` in `help.CHECKS`.
- **Reproduced:** yes (no entry). Noted by the B-93 fix, not in a review report.
- **Fix:** a help entry for `AZURE_DI_DISAGREES`; the test pattern now resolves `Issue(LEVEL, CONST, ...)` through the module's `CONST = "CODE"` lines.
- **Test:** `tests/test_help.py::test_every_check_has_help`
- **Lesson:** Don't let a coverage test match only one spelling of the thing it covers.

### Document capture

#### B-98 · A zero balance due keeps a credit note positive · medium
- **Where:** `ap_coder/capture/reader.py:1001` (`credit_printed_positive`)
- **Symptom:** a credit note printed positive that also prints "Balance Due $0.00" or "Amount Due 0.00" was read with positive amounts, all verified (2,000 / 260 / 2,260). The credit-note title still stopped approval without a person.
- **Cause:** since B-46, every amount on a subtotal or total line votes on the sign. A 0.00 fails `v > 0`, and look-alike lines ("Previous Balance", "Total payments") vote too.
- **Reproduce:** add a "Balance Due $0.00" row to a positive CREDIT NOTE.
- **Reproduced:** yes (`credit_printed_positive` True without the line, False with it). The rule before B-46 read it right.
- **Fix:** zero amounts don't vote, and lines matching `DISTRACTORS["grand_total"]` are skipped unless they are also subtotal labels.
- **Test:** `tests/test_capture_edge_cases.py::test_a_zero_or_a_total_lookalike_does_not_vote_on_a_credit_notes_sign`
- **Lesson:** Don't let a zero, or a line that only looks like a total, vote on the document's sign.

#### B-99 · "Payments in US dollars accepted" makes a Canadian invoice USD · low-medium
- **Where:** `ap_coder/capture/normalize.py:460` (`_in_payment_note`, used by `currency_evidence`)
- **Symptom:** "Total Due CAD 1,050.00" with the note "Payments in US dollars accepted" was read as USD 0.8. With an AI coding that said USD, `analyze` gave USD, Likely, 0.997.
- **Cause:** a currency written in words was a statement wherever it appeared; the B-43 payment-note guard covered only codes.
- **Reproduce:** a sample invoice with that total and that note.
- **Reproduced:** yes (`('USD', 0.8)` where `('CAD', 0.8)` was right).
- **Fix:** both kinds of statement are skipped when their own sentence is a payment note. It works by sentence, not line, so "All prices in Canadian dollars. Payments in USD accepted" on one line still reads CAD. The repro now reads CAD and goes to Check.
- **Test:** `tests/test_capture_edge_cases.py::test_a_currency_in_words_in_a_payment_note_is_not_a_statement`
- **Lesson:** Don't guard one spelling of a signal and leave its other spellings unguarded.

#### B-100 · A page OCR could not read counts as read · low-medium
- **Where:** `ap_coder/capture/layout.py:731` (`build_layout`); `capture/types.py` (`DocLayout.pages_unread_locally`); `capture/__init__.py` (`CaptureResult`)
- **Symptom:** when OCR failed on a scanned page, the capture said 2 pages and 0 left out, and no gate fired.
- **Cause:** the `except` kept an empty page and only logged a warning.
- **Reproduce:** make `ocr_image` raise on page 2 of a two-page scan.
- **Reproduced:** yes, only by injecting that failure; no real file was found that makes OCR fail.
- **Fix:** failed pages are counted in `DocLayout.pages_unread_locally`, and `CaptureResult.pages_left_out` includes them (`page_count` is unchanged). The B-50 gate then says "1 of its pages was not read by the local readers".
- **Test:** `tests/test_touchless.py::test_a_page_ocr_could_not_read_counts_as_unread`
- **Lesson:** Don't turn a page you could not read into an empty page you did read.

#### B-101 · Bulk approvals feed the confidence calibration · low-medium
- **Where:** `ap_coder/capture/workflow.py:299` (`learn_from_approval`)
- **Symptom:** an untouched bulk approval stays out of the supplier's touchless record, but its kept values were saved as "right" cases with evidence keys and fed the calibration: the 3 demo invoices added cases to 15 patterns.
- **Cause:** fused reader rows were recorded with evidence whether or not anyone checked them. "Right" there only meant capture agreed with the AI coding, which bulk approval already requires.
- **Reproduce:** bulk-approve the 3 demo invoices and compare `evidence_counts()`.
- **Reproduced:** yes (15 patterns grew).
- **Fix:** on a bulk approval, rows AP kept are saved with empty evidence, which `evidence_counts()` already leaves out. They still count on the Readers tab. A value someone corrected keeps its evidence.
- **Test:** `tests/test_reader_outcomes.py::test_a_bulk_approval_does_not_feed_the_calibration`
- **Lesson:** Don't count an approval nobody looked at as proof a reading was right.

#### B-102 · A grouped amount to four decimals is cut in two · low
- **Where:** `ap_coder/capture/normalize.py:23` (`_AMOUNT`, `_number`, `_ONE_SPACE_GROUP`)
- **Symptom:** "12,345.6789" was read as 12345.678 and a stray 9; "1.234,5678" and "1 234.5678" split the same way.
- **Cause:** the B-49 four-decimal pattern allowed only digits with no thousands separators.
- **Reproduce:** `parse_amount("12,345.6789")`.
- **Reproduced:** yes (12345.678).
- **Fix:** two more patterns, one with comma, dot or apostrophe grouping and one with space grouping; `_number` reads four digits after a new separator as decimals. A quantity next to a four-decimal price still splits by the product check ("2 250.5000 501.00" gives 2, 250.5, 501).
- **Test:** `tests/test_capture_edge_cases.py::test_a_grouped_amount_to_four_decimals_is_one_number`
- **Lesson:** Don't fix one spelling of a number and leave its other spellings broken.

#### B-103 · Œ, æ and ß are deleted, not folded · low
- **Where:** `ap_coder/ai/evaluation.py:28` (`_norm_text`); `ap_coder/capture/normalize.py:172` (`_plain`, used by `norm_name`)
- **Symptom:** "Les Œuvres du Cœur" became "lesuvresducur" in the accuracy report, so a correct name counted as wrong. `norm_name` gave "les uvres du c ur", so VENDOR_NAME_MATCH failed (the safe direction).
- **Cause:** NFKD has no decomposition for œ, æ or ß, so the B-48 fix deleted them with the accents.
- **Reproduce:** both functions on "Les Œuvres du Cœur".
- **Reproduced:** yes, both.
- **Fix:** a small fold table (œ→oe, æ→ae, ß→ss and the capitals) runs before the accents are dropped, in both modules; evaluation.py doesn't import the capture package.
- **Test:** `tests/test_evaluation.py::test_ligatures_and_sharp_s_fold_to_their_letters`, plus an assertion in `tests/test_capture.py::test_ids_and_names_compare_loosely`
- **Lesson:** Don't assume Unicode decomposition folds every letter.

#### B-104 · On Windows a capture test reads the French sample in the wrong encoding · low
- **Where:** `tests/test_capture_edge_cases.py`; `tests/test_jde.py`
- **Symptom:** on Windows, the French quantity test read a fifth line item.
- **Cause:** the test read the French sample with `read_text()`, which is cp1252 on Windows, so the accented "Sous-total" line no longer looked like a total. The app reads the file as UTF-8; only the tests were wrong.
- **Reproduce:** run the test on Windows.
- **Reproduced:** yes, on Windows. Found while running the tests there, not in a review report.
- **Fix:** `encoding="utf-8"` there and in tests/test_jde.py, which reads the JDE guide.
- **Test:** `tests/test_capture_edge_cases.py::test_a_french_quantity_in_one_space_group_is_read_whole`
- **Lesson:** Don't read a text file without naming its encoding.

### Web routes

#### B-105 · An emailed statement spreadsheet can freeze the dashboard · medium-high
- **Where:** `ap_coder/web/files.py:126` (new `read_excel`); `ap_coder/intake/triage.py:351` (`sheet_unzips_too_big`); `ap_coder/web/pages/statements.py:139-150` (`statements_emailed`); `ap_coder/web/pages/settings_jde.py`, `ap_coder/web/pages/learning.py`, `ap_coder/erp/jde_worksheet.py:390`
- **Symptom:** an emailed "Statement of account" .xlsx of 108 KB that unzips to 30 MB froze Reconcile for 11 s and added 141 MB of memory, again on every redraw.
- **Cause:** the B-63 unzipped-size check was only in `triage.sheet_text`. Triage refused the sheet, but the email was still filed as a statement, and the other readers of the same files (`files.read_sheet`, `settings_jde.read_sheet`, `learning.read_sheets`, the JDE worksheet import) passed them to `pd.read_excel` or openpyxl unchecked. `statements_emailed` also read the whole file with no size cap.
- **Reproduce:** such an email sorted into the statements folder, then Reconcile on the Statements page.
- **Reproduced:** yes (11.4 s, 141 MB more); the new test saw all 5 routes open the workbook.
- **Fix:** one guard, `triage.sheet_unzips_too_big()` (triage's limits: 20 MB a part, 50 MB in all), used by `sheet_text` and by one shared reader, `files.read_excel()`, which raises `SheetError(TOO_BIG_UNZIPPED)`. `files.read_sheet`, `settings_jde.read_sheet` and `learning.read_sheets` use it; `jde_worksheet.read` checks the guard itself, since core code can't import from web. `statements_emailed` reads at most `_MAX_BYTES + 1` and refuses more with the upload's message. Side effect: an .xlsx AP line history with a sheet over 20 MB unzipped is now refused too; the message says to save it as CSV UTF-8, which has no unzip limit.
- **Test:** `tests/test_msg_and_triage.py::test_every_reader_of_an_uploaded_sheet_checks_its_unzipped_sizes`, `tests/test_msg_and_triage.py::test_an_emailed_statement_larger_than_an_upload_is_not_read`
- **Lesson:** Don't put a size guard in one reader of untrusted files and leave the other readers of the same files without it.

#### B-106 · Excel imports drop cells that read "NA" · low-medium
- **Where:** `ap_coder/web/files.py:125`; `ap_coder/web/pages/settings_jde.py:171`; `ap_coder/web/pages/learning.py:136`
- **Symptom:** an Excel import dropped cells reading "NA", "NULL", "N/A" or "None", so a cost center "NA" disappeared. The same file as CSV kept them.
- **Cause:** `read_excel` was called without `keep_default_na=False`; the CSV reader has it.
- **Reproduce:** import a cost-center .xlsx with the codes EU, NA and NULL.
- **Reproduced:** yes (`['EU']` from xlsx, `['EU', 'NA', 'NULL']` from csv).
- **Fix:** all three readers go through the shared `files.read_excel` (B-105), which passes `keep_default_na=False, na_filter=False`. One `pd.read_excel` call is left in the web code.
- **Test:** `tests/test_web_masterdata.py::test_an_excel_cell_reading_na_is_kept_as_typed`
- **Lesson:** Don't read the same kind of upload with different parser options in three copies of the reader.

#### B-107 · The vendor detail asks the database once per invoice · low (performance)
- **Where:** `ap_coder/web/pages/vendors.py:87-111` (`ask_vendor`)
- **Symptom:** `GET /vendors?open=<key>` opened one more database connection for each of the vendor's invoices in the queue.
- **Cause:** `store.get_invoice()` was called inside the row loop (B-10 again).
- **Reproduce:** count `sqlite3.connect` calls with 2 and then 12 invoices.
- **Reproduced:** yes (21 and 31 connections).
- **Fix:** one `store.get_invoices()` call for the rows in review or parked.
- **Test:** `tests/test_web_review.py::test_the_vendor_detail_does_not_ask_the_database_once_per_invoice`
- **Lesson:** Don't call the store inside a row loop.

### Front end

#### B-108 · The vendor line under the name disagrees with the server's match · low
- **Where:** `ap_coder/web/static/js/invoice.js:335` (`vendorKey`); `ap_coder/web/pages/invoice.py:355`
- **Symptom:** for names ending in "S.E.N.C.", "Incorporée" or "s.e.c.", or starting with "The", the server's key matched a listed vendor but the page said the name wasn't that vendor. Only the hint was wrong; the server refuses a duplicate vendor.
- **Cause:** the JavaScript copy of `memory.vendor_key` had drifted: legal forms dropped anywhere in the name, a short list of them, no joining of dotted letters (S.E.N.C.), a leading "the" kept, no ligatures.
- **Reproduce:** type "The North IT Solutions Inc." in the vendor box.
- **Reproduced:** yes (the page's keys differed from the server's; the page had no vendor `key` from the server).
- **Fix:** each vendor in the list carries its `key` from `vendor_key`, and the add-vendor answer returns it too. The JavaScript copy, still needed for the name being typed, follows `vendor_key` exactly and is exported for a parity test.
- **Test:** `tests/test_web_invoice.py::test_in_a_browser_the_vendor_key_is_the_servers`
- **Lesson:** Don't re-implement a server normaliser in JavaScript without a parity test.

#### B-109 · Ctrl+Enter in the "why" dialog throws away the reason · low-medium
- **Where:** `ap_coder/web/static/js/invoice.js:773` (the why dialog's keydown)
- **Symptom:** a reason typed in "One question before saving", then Ctrl+Enter: the dialog came back empty and no reason was sent.
- **Cause:** Ctrl+Enter sent the approval again without `why_done`, so the server asked again and the dialog was drawn again blank.
- **Reproduce:** approve while `lessons.due_for_why` asks a question, type a reason, press Ctrl+Enter.
- **Reproduced:** yes, in Chromium with the server's question simulated (2 approve requests, reason never sent).
- **Fix:** inside the dialog Ctrl+Enter does `save.click()`, and the page-wide shortcut ignores dialogs (B-110).
- **Test:** `tests/test_web_invoice.py::test_in_a_browser_ctrl_enter_in_the_why_dialog_keeps_the_reason`
- **Lesson:** Don't let a page-wide shortcut fire inside a modal dialog.

#### B-110 · Ctrl+Enter typed in another form approves the invoice · medium
- **Where:** `ap_coder/web/static/js/invoice.js:807` (`elsewhere`, used by the keydown listener)
- **Symptom:** Ctrl+Enter in Park's "Waiting for" box or in "Add a note" approved the invoice, and the note was never posted.
- **Cause:** the page-wide Ctrl+Enter listener never checked which form the focused box belongs to.
- **Reproduce:** open an invoice that can be approved, type in Park or the note, press Ctrl+Enter.
- **Reproduced:** yes (`['approve', 'check']` sent; status approved).
- **Fix:** Ctrl+Enter does nothing when the focus belongs to another form, a `<dialog>` or a `[role=dialog]`.
- **Test:** `tests/test_web_invoice.py::test_in_a_browser_ctrl_enter_in_another_form_never_approves` (`[park]` and `[note]`)
- **Lesson:** Don't bind a page-wide submit shortcut without checking which form owns the focus.

#### B-111 · After an update, browsers keep running the old app.js · medium
- **Where:** `ap_coder/web/app.py:39` (`CheckedStaticFiles`)
- **Symptom:** after an upgrade, the old `app.js` and `invoice_viewer.js` kept running, even after a reload, while page scripts and CSS changed at once.
- **Cause:** those two files have no `?v=`, and `/static` sent no `Cache-Control`, so Chromium cached them by guesswork.
- **Reproduce:** GET `/static/js/app.js` and look at the headers.
- **Reproduced:** yes (`cache-control` was `None`; the new app.js did not run after the update or a reload).
- **Fix:** a `StaticFiles` subclass adds `Cache-Control: no-cache` to every answer; an unchanged file still answers 304.
- **Test:** `tests/test_web_app.py::test_static_files_are_checked_again_on_every_use`
- **Lesson:** Don't leave an unversioned module URL without a revalidation header.

#### B-112 · Plain Enter in a review field approves the invoice · medium
- **Where:** `ap_coder/web/templates/invoice.html:52`; `ap_coder/web/static/js/app.js` (`submitButton`)
- **Symptom:** typing a PO number and pressing Enter approved at once, with the half-typed value.
- **Cause:** the browser's implicit submission pressed Approve, the form's only default button.
- **Reproduce:** type in the PO number box and press Enter.
- **Reproduced:** yes (`['approve', 'check']` sent; status approved).
- **Fix:** a disabled, hidden submit button is the first child of `form#inv`. `submitButton` in app.js skips hidden buttons, so "Approving…" still lands on the real Approve button.
- **Test:** `tests/test_web_invoice.py::test_in_a_browser_enter_in_a_review_field_never_approves`
- **Lesson:** Don't let a form whose submit button approves be submitted by Enter in a text field.

#### B-113 · A refused export says "The page is refreshed", and the button's count freezes · low
- **Where:** `ap_coder/web/pages/exports.py:313`; `ap_coder/web/static/js/exports.js:13`
- **Symptom:** after the refusal nothing reloaded and the stale rows stayed listed; afterwards the button kept saying "Export 5 invoices" with one ticked.
- **Cause:** `refuse()` never reloads. Separately, the busy state replaces the button's contents, so the `#export-label` that exports.js kept was no longer on the page.
- **Reproduce:** export invoices another tab exported first, then untick a row.
- **Reproduced:** yes (no reload; the count did not follow the ticks).
- **Fix:** the server answers with `done(request, "/exports?fmt=…", message, tone="warn")`, and `update()` looks up `#export-label` each time. The end of `test_the_ready_list_and_a_new_batch`, which expected the old 400, was changed.
- **Test:** `tests/test_web_finance.py::test_an_export_refused_as_exported_meanwhile_refreshes_the_page`, `tests/test_web_invoice.py::test_in_a_browser_a_refused_export_reloads_and_the_count_follows_the_ticks`
- **Lesson:** Don't keep references to elements inside a `data-busy` button.

#### B-114 · An edit typed just before Back is lost · low
- **Where:** `ap_coder/web/static/js/invoice.js:839`
- **Symptom:** an edit typed just before Back or closing the tab was gone on coming back, though the page promises edits are kept as a draft.
- **Cause:** the check that saves the draft waits 450 ms and was flushed only for links, forms and the page's own keys.
- **Reproduce:** type a PO number, press Back within 100 ms, come back.
- **Reproduced:** yes (the PO number was `''`).
- **Fix:** on `pagehide`, a pending check is sent with `fetch(…/check, {keepalive: true})`.
- **Test:** `tests/test_web_invoice.py::test_in_a_browser_an_edit_typed_just_before_leaving_is_kept`
- **Lesson:** Don't debounce a save without flushing it when the page is left.

#### B-115 · Alt+←/→ in a text box jumps to another invoice · low
- **Where:** `ap_coder/web/static/js/invoice.js:813` (`typing`)
- **Symptom:** Alt+←/→ in a text box (Option+arrows on a Mac, which move the cursor by word) went to the previous or next invoice.
- **Cause:** the listener didn't check where the focus was.
- **Reproduce:** click the vendor box and press Alt+←.
- **Reproduced:** yes (`/review/5` went to `/review/7`).
- **Fix:** the shortcut is skipped in text inputs, textareas, selects and editable areas; it still works elsewhere on the page.
- **Test:** `tests/test_web_invoice.py::test_in_a_browser_alt_arrows_in_a_text_box_stay_on_the_invoice`
- **Lesson:** Don't take over text-editing keys while a text box has the focus.

### Intake, CLI and launch

#### B-116 · An invoice in an email forwarded inside a .msg is dropped · medium
- **Where:** `ap_coder/intake/msgfile.py:102-109` (`_children`)
- **Symptom:** an invoice inside an email forwarded in a .msg was silently dropped when the .msg's directory lists the inner storage before its parent, which the file format allows.
- **Cause:** the B-62 fix walked storages in index order with one shared `walked` set. A child storage with a lower index than its parent was already marked seen, so the parent's walk skipped it and everything reached only through it.
- **Reproduce:** a .msg with an attached email (`bench.msgwriter`), its directory entries renumbered in reverse with the root kept at 0, then `read_msg`.
- **Reproduced:** yes (`['INV-1.pdf']`, against `['INV-1.pdf', 'INV-0.pdf']` for the file as written).
- **Fix:** storages are walked as a tree from the root (a stack, each storage pushing the child storages it finds), with one shared `walked` set. B-62's test still passes.
- **Test:** `tests/test_msg_and_triage.py::test_the_order_of_directory_entries_never_drops_an_attachment`
- **Lesson:** Don't make the result of a graph walk depend on the order the nodes are stored in.

#### B-117 · An open fraud email drops off "needs a person" after 500 newer emails · medium
- **Where:** `ap_coder/intake/inbox.py:39-53` (`needs_person`, `entry`); `ap_coder/intake/mailbox.py:337` (`triage_log`)
- **Symptom:** a bank-detail-change (possible fraud) email nobody marked done dropped off "needs a person" once 500 newer emails had been sorted. An old open email could also be shown but not opened or marked done.
- **Cause:** `needs_person()` read `triage_log` with its default limit of 500, applied before the needs-person and not-done filters. `triage_log` also read only the last 4 MB, and `entry()` searched only the newest 5000 entries.
- **Reproduce:** a log with one open fraud entry followed by 500 newer invoice entries, then `inbox.needs_person`.
- **Reproduced:** yes (`['fraud01']` with 499 newer, `[]` with 500).
- **Fix:** `triage_log` takes an optional `keep` filter: with it the whole log is read and `limit` counts only the entries kept. `needs_person` filters first, puts possible fraud first, then cuts to `limit`; `entry()` matches the id through the filter. The dashboard now reads the whole triage log on each load.
- **Test:** `tests/test_msg_and_triage.py::test_an_open_email_stays_in_needs_a_person_however_many_were_sorted_after_it`
- **Lesson:** Don't apply a "most recent N" window before the filter that decides what is still open.

#### B-128 · An email forwarded inside itself a thousand times was read by recursing to Python's limit · low
- **Where:** `ap_coder/intake/mailbox.py` (`read_eml`)
- **Symptom:** CI on Python 3.13 (Ubuntu) failed `test_a_crafted_email_in_the_folder_does_not_break_the_process_page` once with "Failed to process unraisable exception"; the same commit had passed.
- **Cause:** the only guard against deep forwarding was to catch the RecursionError of Python's email parser, which recurses once a forwarded layer. At that depth an object freed by the garbage collector (an event loop's `__del__`) failed too, outside the `try`.
- **Reproduce:** `read_eml()` of an email with 1,000 nested `Content-Type: message/rfc822` layers, with `email.message_from_bytes` patched to fail when called.
- **Reproduced:** yes (the parser was called).
- **Fix:** more than 500 `message/rfc822` headers (the part cap) are refused before parsing; the RecursionError catch stays as a last resort.
- **Test:** `tests/test_mailbox.py::test_an_email_forwarded_inside_itself_a_thousand_times_is_refused_before_it_is_parsed`
- **Lesson:** Don't use a RecursionError as a limit: count what makes the code recurse and refuse it before.

### Codebase map tooling and CI

#### B-118 · Interface names pass when they exist anywhere · medium
- **Where:** `scripts/check_map.py:255-262`
- **Symptom:** the "names in ## Interface exist" check passed `` `nosuchmodule.main()` ``, passed a name whose file was renamed because another file in the folder had a function of that name, and skipped a folder with no code.
- **Cause:** a qualified name passed if the function name existed anywhere in the repository; an unqualified one only had to exist somewhere in the folder; a folder with no .py or .js files skipped the check.
- **Reproduce:** add `` `nosuchmodule.main()` `` to scripts/CLAUDE.md; rename `pilot_check.check()`; add `` `does_not_exist()` `` to .claude/CLAUDE.md; run the checker.
- **Reproduced:** yes (exit 0 each time).
- **Fix:** a name given with its file (x.py) must be in that file; `mod.name()` or `Class.name()` must be in that module or class; in a folder with no code, a name must exist somewhere in the repository.
- **Test:** `tests/test_check_map.py::test_an_interface_name_must_be_in_the_module_or_file_it_names`, `tests/test_check_map.py::test_a_name_in_a_folder_without_code_must_exist_somewhere`
- **Lesson:** Don't let an existence check be satisfied by any match anywhere.

#### B-119 · A source file with a byte-order mark crashes the map checker · low-medium
- **Where:** `scripts/check_map.py:91, 171`
- **Symptom:** the checker crashed with `SyntaxError: invalid non-printable character U+FEFF` on a .py saved with a UTF-8 BOM (Notepad). Python runs such files fine.
- **Cause:** `ast.parse(read_text("utf-8"))` kept the mark.
- **Reproduce:** add a BOM to a .py in the map and run the checker.
- **Reproduced:** yes (traceback).
- **Fix:** a source is parsed from bytes, as Python reads it.
- **Test:** `tests/test_check_map.py::test_a_source_file_with_a_byte_order_mark_is_read_like_python_reads_it`
- **Lesson:** Don't parse source text in a way stricter than the interpreter that runs it.

#### B-120 · `--changed` passes over a base git can't compare with · low
- **Where:** `scripts/check_map.py:273`; `.github/workflows/ci.yml` (the "Check the map" step)
- **Symptom:** a base that doesn't resolve, or a depth-1 clone, silently reported no changed folders, exit 0. A push to main never got the "code changed without its map" warning.
- **Cause:** git's errors were swallowed. On a push to main, CI's base was `origin/main`, which equals HEAD.
- **Reproduce:** `check_map.py --changed no-such-branch`; the same in a shallow clone.
- **Reproduced:** yes (exit 0, nothing said).
- **Fix:** `--changed` warns "git can't compare with <base>". CI compares a push to main with the commit before it (`github.event.before`, else `HEAD~1`).
- **Test:** `tests/test_check_map.py::test_a_base_git_cannot_compare_with_is_said_not_passed_over`
- **Lesson:** Don't swallow a tool's error and report that nothing changed.

#### B-121 · A new file not yet added to git is invisible to the map checker · low
- **Where:** `scripts/check_map.py`
- **Symptom:** a new module not yet `git add`ed passed both the Files index check and `--changed` (2 errors once it was staged). The hook's "then run check_map.py" advice missed exactly this case.
- **Cause:** the checker listed only the files git tracks.
- **Reproduce:** create a new module in a mapped folder without adding it, then run the checker.
- **Reproduced:** yes (exit 0).
- **Fix:** new untracked files that git doesn't ignore count too.
- **Test:** `tests/test_check_map.py::test_a_new_module_counts_before_it_is_added_to_git`
- **Lesson:** Don't take the files git tracks as all the files that exist.

#### B-122 · The map checker crashes on a deleted file, a list as hook input, or no base · low
- **Where:** `scripts/check_map.py`
- **Symptom:** a tracked file deleted but not yet `git rm`ed gave `FileNotFoundError`; `--hook` given a JSON list on stdin gave `AttributeError` (exit 1, still not blocking); `--changed` with no value gave `IndexError`.
- **Cause:** each input was taken as well formed.
- **Reproduce:** each of the three.
- **Reproduced:** yes.
- **Fix:** a deleted file is simply gone from the map (a map that still lists it is an error), the hook ignores input that isn't an object, and `--changed` with no base exits 2.
- **Test:** `tests/test_check_map.py::test_a_new_module_counts_before_it_is_added_to_git` (deleted file), `tests/test_check_map.py::test_the_hook_reminds_of_the_folder_map_and_never_blocks` (a JSON list), `tests/test_check_map.py::test_a_base_git_cannot_compare_with_is_said_not_passed_over` (no base)
- **Lesson:** Don't let a checker crash on input it didn't expect; say what is wrong instead.

#### B-123 · The hook never reminds of an edit in an agent's worktree · low
- **Where:** `scripts/check_map.py:60` (`_skipped`)
- **Symptom:** when the hook runs from the main checkout, edits under `.claude/worktrees/` never got the folder-map reminder.
- **Cause:** `_skipped()` dropped every path under `.claude/worktrees/`.
- **Reproduce:** send the hook an edit of `.claude/worktrees/agent-1/ap_coder/sub/b.py`.
- **Reproduced:** yes (no reminder).
- **Fix:** an edit inside an agent's worktree gets the reminder of its folder in that worktree.
- **Test:** `tests/test_check_map.py::test_the_hook_reminds_of_an_edit_in_an_agents_worktree_too`
- **Lesson:** Don't skip a whole folder in a hook without checking who works in it.

#### B-124 · The browser tests never ran in CI · medium
- **Where:** `tests/conftest.py` (`chromium_path`), `.github/workflows/ci.yml` (no browser job)
- **Symptom:** the 12 tests that open the dashboard in Chromium (B-57 to B-60, B-108 to B-115, and every page asking only this computer) passed in CI by skipping: CI installs no Playwright, and the tests looked for Chromium only under /opt/pw-browsers or `PLAYWRIGHT_BROWSERS_PATH`.
- **Cause:** a test that skips without its tool was the only proof of the front-end fixes, and nothing made CI provide the tool. Found by the map's second accuracy pass.
- **Reproduce:** any CI run before this fix: the `test_in_a_browser_*` tests are reported as skipped.
- **Reproduced:** yes (11 skipped per Tests job).
- **Fix:** a "Browser tests" CI job installs Playwright and its Chromium and runs them with `AP_REQUIRE_BROWSER=1`, where a missing browser fails instead of skipping; one shared `chromium_path()` also finds Playwright's own install.
- **Test:** `tests/test_ci_workflow.py::test_ci_runs_the_browser_tests_and_never_lets_them_skip`
- **Lesson:** Don't let a test that skips without its tool be the only proof: make CI provide the tool and fail without it.


#### B-125 · The phone-screen popover test read the position before the page placed it · low
- **Where:** `tests/test_web_invoice.py` (`test_in_a_browser_the_popovers_open_inside_a_phone_screen`)
- **Symptom:** the test failed on some runs (the Split a line popover at x = -79 on a 390 px screen), then passed on others, with no code change; seen on a loaded machine, on main as well as this branch.
- **Cause:** app.js moves a popover inside the screen on the "toggle" event, which the browser fires a moment after the click; the test read the box right after the click, sometimes before that. The page itself was right: 300 ms later every popover sat 8 px from the edge.
- **Reproduce:** run the test a few times on a busy machine; or read the box right after the click and again 300 ms later (-79 then 8).
- **Reproduced:** yes.
- **Fix:** the test waits (up to 2 s) for the popover to be inside the screen, then checks it; it still fails if the page never places it (checked against app.js without the placing code).
- **Test:** `tests/test_web_invoice.py::test_in_a_browser_the_popovers_open_inside_a_phone_screen`
- **Lesson:** Don't assert on what a page does in an event handler right after the action that queues it: wait for the state, with a timeout.

#### B-126 · A Status filter with many statuses broke the invoice directory · low
- **Where:** `ap_coder/erp/directory.py` (`condition`, the Status column)
- **Symptom:** /search with `status=review,review,...` (1,200 times) failed with "too many SQL variables" on an older SQLite build (999 variables); a misspelt status ("reveiw") matched nothing, without a word.
- **Cause:** one "?" per status typed in the address, with no check that each is a real status or that it is there only once. Found by the new rule in tests/test_code_rules.py, which turns the lesson of B-10, B-29 and B-30 into a check of the whole code.
- **Reproduce:** `d.run(store, d.Query(filters={"status": ",".join(["review"] * 1200)}))` with the `few_sql_variables` fixture.
- **Reproduced:** yes.
- **Fix:** keep only real statuses, each once (so at most six); an unknown one is refused with a message, as the other filters are.
- **Test:** `tests/test_directory.py::test_a_status_filter_with_more_statuses_than_sqlite_takes_variables`; the rule: `tests/test_code_rules.py::test_no_sql_placeholders_are_made_one_per_item_of_a_list`
- **Lesson:** Don't fix a repeated mistake one place at a time: write a check that finds every place, then fix what it finds.

#### B-127 · The browser tests could not start in CI's browser job · medium
- **Where:** `tests/conftest.py` (`chromium_path`)
- **Symptom:** in the new "Browser tests" CI job every browser test errored with "It looks like you are using Playwright Sync API inside the asyncio loop".
- **Cause:** CI has no Chromium under /opt/pw-browsers, so `chromium_path()` asked Playwright for its own one by starting a second `sync_playwright()`, and the tests call it inside their own `with sync_playwright()`. Locally the /opt/pw-browsers path was found first, so the fallback never ran.
- **Reproduce:** point the browser roots at an empty folder, with Playwright's own Chromium installed, and call `chromium_path()` inside `with sync_playwright()`.
- **Reproduced:** yes (the same error as CI).
- **Fix:** the lookup is made once per run (`functools.cache`), the first time outside a running Playwright; the folders searched are a list (`BROWSER_ROOTS`) a test can change.
- **Test:** `tests/test_ci_workflow.py::test_the_browser_is_looked_up_once_not_inside_a_running_playwright`
- **Lesson:** Don't let a fallback be the path only CI takes: make a test run it locally.

#### B-129 · A change made from the office network lost what it changed from · medium
- **Where:** `ap_coder/core/store.py` (`_log`, with `EVENT_ORIGIN` from the W2-02 change before it was merged)
- **Symptom:** a colleague who changed the touchless limit, a supplier's touchless state, or opened an invoice whose file had moved left an event whose "from" was their computer's address instead of the old value; the Activity page showed "10.0.0.5 → 2500".
- **Cause:** the colleague's address was added to every event as `detail["from"]`, a key several events already use for the old value. Found by the map-reviewer agent before the PR.
- **Reproduce:** set `EVENT_ORIGIN` to "192.168.1.20", call `store.set_touchless_limit(1234.0, "Ana")`, read the `settings_changed` event.
- **Reproduced:** yes (`{"keys": ["touchless_limit"], "from": "192.168.1.20", "to": 1234.0}`).
- **Fix:** the address goes under its own key, `detail["address"]`.
- **Test:** `tests/test_network_identity.py::test_a_colleagues_address_never_replaces_what_an_event_itself_records`
- **Lesson:** Don't add a field to every record under a name some records already use: pick a key nothing else writes.

#### B-130 · Invoices a colleague processed in parallel were recorded as nobody's · low
- **Where:** `ap_coder/ai/pipeline.py` (`process_many`)
- **Symptom:** with more than one worker, invoices read by a colleague's "Process" job had no address on their events.
- **Cause:** the job thread copied the request's context, but `ThreadPoolExecutor` threads do not carry contextvars. Found by the map-reviewer agent before the PR.
- **Reproduce:** set `EVENT_ORIGIN`, call `process_many` with three files and `workers=3`, and read `EVENT_ORIGIN` inside `process`.
- **Reproduced:** yes (`[None, None, None]`).
- **Fix:** each file runs in a copy of the caller's context (`contextvars.copy_context().run`).
- **Test:** `tests/test_network_identity.py::test_invoices_read_in_parallel_keep_the_colleague_who_started_them`
- **Lesson:** Don't trust a contextvar across a thread pool: copy the context into each task.

#### B-131 · On Windows, a commit after `cd C:\path` passed the review gate unchecked · low
- **Where:** `scripts/map_review_gate.py` (`commit_dirs`)
- **Symptom:** CI on Windows: the gate's own test `cd <an unquoted Windows temp path> && git commit -am x`, run from another folder, was let through (exit 0, not 2). Git Bash also eats unquoted backslashes, so a real command like it would fail at `cd`; a quoted path was read right.
- **Cause:** the command was split into words the POSIX way, where a backslash escapes the next character, so the folder became `C:Users...repo`, which is no repository; the gate then checked only the session's folder. Found by the new gate's own test on CI's Windows runners, before the PR merged.
- **Reproduce:** `commit_dirs(r"cd C:\Users\me\repo && git commit -am x", Path("/session"))`.
- **Reproduced:** yes (`/session/C:Usersmerepo`).
- **Fix:** the command is read both ways, a backslash as an escape (`my\ repo`) and as a Windows path's, and the folders of both readings are checked. A first fix that only kept the backslashes broke escaped spaces (`git -C my\ repo commit` was no longer seen at all); the map-reviewer found it before the push.
- **Test:** `tests/test_map_review_gate.py::test_a_windows_path_keeps_its_backslashes`
- **Lesson:** Don't read a command line one way when it may mean two: check the folders of every reading.

### Not reproduced or left as they are

- **N-32** · Store · `_env_float` (`config.py:45`) accepts `AP_REVIEW_THRESHOLD=nan` or `-1` from a hand-edited `.env`, so low confidence would never force a review. The Settings page enforces 50-99%; not run end to end; left.
- **N-33** · Store · `add_invoice` deletes earlier failed attempts but not their `page_reads`, `azure_checks`, `verifications` or `invoice_capture` rows (foreign keys are off, so `ON DELETE CASCADE` never fires). Failed invoices are never queued; no effect seen; left.
- **N-34** · Store · `copy_backup` prunes every `ap_coder-*.db` in the second backup folder, so two installs sharing one folder would delete each other's copies. Read only; left.
- **N-35** · Store · a `labels.csv` saved again by a French Excel uses `;` and fails with "row 2 has no document" (`labels._read_rows` doesn't sniff the delimiter). A clear error, not silent; left.
- **N-36** · Store · `envfile.parse_value` unescapes `\\` in single-quoted values, but python-dotenv does not. `write_env` never writes single quotes; left.
- **N-37** · Store · `reference_data.load_table` accepts an inactive GL row followed by an active one with the same code, but refuses the reverse order. Inconsistent, harmless; left.
- **N-38** · Tax · `vendor_mail.Draft.mailto` percent-encodes the "@" (`a%40b.com`), which some mail clients may not decode. No mail client to try it with; left.
- **N-39** · Tax · `po._header_findings` divides the word overlap by the shorter name, so "Acme" against "Acme Fraud Holdings" scores 0.9 and `PO_VENDOR_MISMATCH` never fires. Read in the code only; it may be intended; left.
- **N-40** · Tax · `math_check` allows 0.015 for "Lines = subtotal" while `validate_coding` allows a cent per line. Display only, the same family as N-07; left.
- **N-41** · Exports · MATCH_HEADER and tax-mode balancing. Checked: every sample, both numberings and both amount modes; the pay items always add up to the invoice total.
- **N-42** · Exports · accruals (B-27 fix): a flagged copy with a lower id than its original would be accrued too. Invoices are read in id order; not built; left.
- **N-43** · Exports · a JDE batch downloaded again uses today's settings, FX rates and vendor master, so it can differ from the first download. By design (the docstring says so).
- **N-44** · Exports · one damaged source page that raises inside `_stamp` would fail the whole stamped-PDF ZIP. Not built; left.
- **N-45** · Exports · `Layout.from_json` and `JdeSettings.from_json` crash on wrong types or extra keys. Only a hand-edited database reaches them; left.
- **N-46** · Exports · the duplicate audit ignores currency: CAD 100 and USD 100 with the same number pair up. Looks like a design choice; left.
- **N-47** · AI · `store.queue_verification` refuses while a row is `verifying`, so a page-reader re-read in another process during a review leaves the new proposal unreviewed, showing the old result's issues (the proposal digest is stored but never compared). Needs two processes and real timing; left. The stale `meta.capture` write in `verifier._fold_in` is N-11.
- **N-48** · AI · since B-15, "ABC Sec" and "ABC" share a vendor key. Read in the code only; no real collision shown.
- **N-49** · AI · `mark_capture` overwrites an existing `other:<value>` source and the readers listed there. Unlikely: capture keys hold typed values (`9999.0`), the model writes strings (`9,999.00`); left.
- **N-50** · AI · the reviewer keys answers by line number, so two lines with the same number could be confirmed by one answer. Local readers number lines in sequence, so only the Azure path could reach it; left.
- **N-51** · Capture · at a late approval, `still_touchless` calls `invoice_gates` without the report, `pages_read` or `no_page`, and never re-runs `should_auto_approve`. No path found that approves something wrong: the first decision applied these gates, and B-32 re-reads the reviewer and Azure issues; left.
- **N-52** · Capture · image files over 20 frames skip the B-50 count (`_image_pngs`). Not reachable: the pipeline turns pictures into PDFs first.
- **N-53** · Capture · `evaluation._amount_eq(None, None)` counts an amount blank in both as a miss. Not reachable: the schema and `labels.py` require numbers.
- **N-54** · Capture · `VENDOR_NAME_MATCH` is a plain substring test ("bell" is in "bellwether"). A misuse is caught by the coding and the page disagreeing; not shown to be harmful.
- **N-55** · Capture · a blank-looking page with a hidden text layer is left out of `text_layer_check`. Safe: `made_up("")` counts it as not read, which fires the "pages not read by OvisOCR2" gate.
- **N-56** · Capture · the other places that rebuild a `DocLayout` (`straightened`, the tables-only layout, `second_read_layout`) drop `pages_left_out`. No effect on the gate today, because `analyze` builds the final capture from the original layout; left.
- **N-57** · Web routes · W2-02: the dashboard shared on the network (`AP_DASHBOARD_ADDRESS=0.0.0.0`) records every action as the server's user, so a colleague's second approval is refused and review drafts are shared. The user chose a per-person identity: done in its own PR (D-34, `tests/test_network_identity.py`).
- **N-58** · Web routes · `security.host_allowed` calls `socket.getfqdn()` (a DNS lookup) in the async middleware when listening on 0.0.0.0, so a slow DNS could freeze every request. Needs a real network; not tried.
- **N-59** · Web routes · up to 24 uploads of 50 MB wait in memory (about 1.2 GB), and `process_upload` and `learning.history_upload` read a whole file with no cap. Not measured; left.
- **N-60** · Web routes · `Job._run` catches only `Exception`, so a `BaseException` would leave a job "running" forever. Read in the code only.
- **N-61** · Web routes · `server.write_server_file` writes the token before its `chmod` to 0600, so for a moment it has the default permissions. Read only; the token only allows a shutdown from this computer.
- **N-62** · Web routes · `/review/{id}/read-pages` and `/split` don't check the status. Checked: `page_worker` refuses an invoice not in review, so at worst a read is wasted or a draft unused.
- **N-63** · Front end · XSS. Checked in Chromium: HTML in the vendor, invoice and PO numbers, terms, lines and notes, on 15 pages and in the viewer tooltips; nothing was injected.
- **N-64** · Front end · forms and pages. Checked: all 65 POST forms crawled have a route; every page at 1400 and 390 px wide has no JS errors, no requests leaving the computer and no sideways scroll.
- **N-65** · Front end · a `/check` in flight while approving could save a stale draft again. Checked: the handlers run one after the other; the check sees "approved" and answers 409.
- **N-66** · Front end · a redirect that changes only the `#` part (the form stuck busy), and the OvisOCR2 progress check reloading forever. Checked: every `done()` with a message adds a fresh flash token; the template and `/reader` both use `reader_state`.
- **N-67** · Front end · help.js filtering unlike the server, and the `taught` hidden field encoded twice. Checked: both filters match the same plain substring; `taught` is a dict, so `tojson` gives an object.
- **N-68** · Intake · a photo picked up twice by `watch`. Checked end to end: the PDF left in the folder is found by `find_by_hash`.
- **N-69** · Intake · the B-63 zip check fooled by sizes that lie. Checked: Python's zipfile unpacks at most the declared size.
- **N-70** · Intake · the B-71 boundary count bypassed. Every part needs a `\n--` line; no way around it found.
- **N-71** · Intake · touchless approval of an invoice from a "possible fraud" email: the triage flag never reaches the pipeline, so only the invoice's own `VENDOR_BANK_CHANGED` check can hold it. An end-to-end touchless run was too heavy to try; left.
- **N-72** · Intake · Windows reserved names in `safe_name` (e.g. an uploaded `CON.pdf`). Can't be tried on Linux; left.
- **N-73** · Intake · `cli._inputs` given one email as both a relative and an absolute path: the second unpack fails and the command exits 1. Edge case, not run.
- **N-74** · Intake · `ui.money(NaN)` shows "nan", and `ui.time_ago` would crash on a timestamp with a time zone. Not run; the store writes timestamps without one.
- **N-75** · Intake · `inbox.done_ids()` reads only the last 20,000 "email marked done" events, so past that, emails marked done long ago would come back as open. Noted by the B-117 fix; left.
- **N-76** · Codebase map · Depends on / Used by is worked out only for `ap_coder`, so `scripts` and `tests` are never counted as users of a module, and the Depends on lines of `scripts`, `tests`, `.github` and `.claude` are not checked. By design; the `ap_coder/MODULES.md` "Imported by" column now lists the scripts that import each module.

## Sweep 3 (before the pilot tester starts testing), October 2026

The codebase map was removed first (decisions D-38). Each section was then reviewed by a fresh agent that reproduced every finding by running it; a pilot tester's day-one walk-through of the dashboard was added. Each reproduced bug got a failing test first, then the fix.

| Section | Found | Reproduced and fixed | Not reproduced / left |
|---|---|---|---|
| Store and config | 10 | 9 (B-158 to B-166) | 1 (N-77) |
| Tax and coding rules | 9 | 8 (B-132 to B-138, B-214) | 1 (N-78) |
| Exports, ERP and reports | 12 | 9 (B-150 to B-157, B-208) | 3 (N-79 to N-81) |
| AI and readers | 11 | 9 (B-174 to B-181, B-209) | 2 (N-82 to N-83) |
| Document capture | 12 | 9 (B-182 to B-189, B-205) | 3 (N-84 to N-86) |
| Web routes | 6 | 5 (B-190 to B-194) | 1 (N-87) |
| Intake, email and command line | 14 | 12 (B-140 to B-149, B-206 to B-207) | 2 (N-88 to N-89) |
| Setup, launcher and update | 11 | 7 (B-167 to B-173) | 4 (N-90 to N-93) |
| Day-one walk-through | 13 | 13 (B-197 to B-204, B-210 to B-213, B-215) | 0 |
| **Total** | **98** | **81** | **17** |

### Store and config

#### B-158 · A withdrawn approval kept teaching the AI its lessons · medium
- **Where:** `ap_coder/core/store.py:1238` (`_forget_learning`, `delete_invoice(forget_lessons=True)`, `record_lesson`)
- **Symptom:** an approval with a wrong GL, reopened (or sent back, rejected, deleted with its lessons), still told the AI "lines like “dell latitude laptop gb” are coded to GL 6999"; a correction reopened and approved again counted twice, so the "why?" question came early.
- **Cause:** withdrawing an approval removed its feedback, supplier and reader outcomes but not what it had added to the `lessons` counts and value; nothing recorded which approval added what.
- **Reproduce:** approve an invoice with line 1 changed to 6999 and learn from it, reopen it, approve it as read: `lessons.prompt_lines()` still names GL 6999; approve, reopen, approve the same correction: `corrected` and `since_why` are 2.
- **Reproduced:** yes (`AI is told: ['Northwind IT Solutions Inc.: lines like “dell latitude laptop gb” are coded to GL 6999, 1 time.']` after the reopen, the re-approval and a reject; `one invoice, corrected: 2 since_why: 2`)
- **Fix:** each approval's part of a lesson is kept in a new table `lesson_approvals` (invoice, situation, corrected, confirmed, value, proposed, when); `_withdraw_lessons` takes it back (seen, corrected, confirmed, and the count towards "why" unless a reason was given since; the value falls back to the latest correction still standing) from `_forget_learning` (reopen, send back, reject) and `delete_invoice(forget_lessons=True)`; the same invoice recorded again replaces its earlier part.
- **Test:** `tests/test_sweep3_core.py::test_a_withdrawn_approval_no_longer_teaches_its_lessons`, `tests/test_sweep3_core.py::test_a_reopened_and_reapproved_correction_counts_once`, `tests/test_sweep3_core.py::test_an_invoice_deleted_with_its_lessons_forgets_them`
- **Lesson:** Don't fold an approval into running totals without keeping what it added: withdrawing it must be able to take that back.

#### B-159 · Replacing the GL list with a file whose code column is blank deleted every account · medium
- **Where:** `ap_coder/core/store.py:685` (`import_accounts`), `ap_coder/web/pages/accounts.py` (`accounts_import`)
- **Symptom:** an import with "Replace all" where the wrong (or a blank) column was picked as the code said "Imported: 0 added, 0 updated, 2 skipped" and left no GL account at all; coding then failed ("No GL accounts imported yet").
- **Cause:** `replace_all` deleted the table before looking at the rows, and nothing checked that any row had a code.
- **Reproduce:** `import_accounts("gl_accounts", [{"GL": ""}, {"GL": ""}], "GL", None, replace_all=True)` on the sample setup.
- **Reproduced:** yes (`before: 26`, `{'added': 0, 'updated': 0, 'skipped': 2}`, `after: 0`, `coding now fails: No GL accounts imported yet`)
- **Fix:** a replace whose rows (there are some) have no code at all is refused before anything is deleted, with a plain message; the accounts page shows it (400) instead of "0 added". The editable grid's save (rows with codes only, or none when everything was deleted on purpose) is unchanged.
- **Test:** `tests/test_sweep3_core.py::test_an_import_with_no_usable_code_never_replaces_the_gl_accounts`, `tests/test_sweep3_core.py::test_the_accounts_page_says_why_an_import_with_no_codes_is_refused`
- **Lesson:** Don't delete what an import replaces before checking the import has something to put in its place.

#### B-160 · A semicolon CSV with a comma in a header cell was split on commas · low
- **Where:** `ap_coder/core/csvio.py:16` (`sniff_delimiter`, was `_dialect`), `ap_coder/web/files.py:152` (`read_sheet`)
- **Symptom:** a GL list saved by French-Canadian Excel (semicolons) whose header reads "Compte GL;Description;Catégorie (dépenses, revenus)" came in as two columns, "Compte GL;Description;Catégorie (dépenses" and " revenus)", with whole lines in the first one; the import page offered the same wrong columns.
- **Cause:** the reference-file reader sniffed the delimiter from the header line alone, and pandas' `sep=None` on uploads also sniffs a single line: a comma inside a header cell made comma look like the delimiter.
- **Reproduce:** `read_csv_rows()` on `Compte GL;Description;Catégorie (dépenses, revenus)` / `6000;Fournitures de bureau;Dépenses`.
- **Reproduced:** yes (`{'Compte GL;Description;Catégorie (dépenses': '6000;Fournitures de bureau;Dépenses', ' revenus)': None}`)
- **Fix:** `sniff_delimiter` reads the header and the first 20 rows with Excel's quoting for each of comma, semicolon, tab and pipe, and picks the one that splits the most rows into the header's number of cells (more than one; a comma on a tie or when nothing fits); `read_csv_rows` and the dashboard's upload reader both use it (Excel's quoting is kept, B-02).
- **Test:** `tests/test_sweep3_core.py::test_a_semicolon_csv_with_a_comma_in_its_header_is_split_on_semicolons`, `tests/test_sweep3_core.py::test_a_plain_comma_csv_is_still_read_on_commas`
- **Lesson:** Don't guess a file's delimiter from one line: check that it fits the rows under it too.

#### B-161 · A backup dated in the future stopped the daily automatic backups · low
- **Where:** `ap_coder/core/store.py:3316` (`auto_backup`)
- **Symptom:** after a day the laptop's clock was ahead (a backup named and dated in the future), AP Coder made no automatic backup at start-up again, for as long as that file was the newest by name.
- **Cause:** the newest daily backup's age was compared with 20 hours, and a negative age (made "later" than now) is less than 20 hours; it was always the newest by name, so it was the only one looked at.
- **Reproduce:** a daily backup `ap_coder-20271019-090000.db` with a modified time a year ahead, then `auto_backup()` three times.
- **Reproduced:** yes (`auto backups made: [None, None, None]`)
- **Fix:** each daily backup's age is looked at, and only one made between now and 20 hours ago skips the backup: one dated in the future counts as old.
- **Test:** `tests/test_sweep3_core.py::test_a_backup_dated_in_the_future_does_not_stop_daily_backups`
- **Lesson:** Don't trust a file time to be in the past: a negative age is not a recent one.

#### B-162 · A held supplier could go touchless after the vendor-matching rules changed · low
- **Where:** `ap_coder/core/store.py:497` (`_recompute_vendor_keys`; new `_merge_supplier_profiles`, `_merge_lesson_rows`)
- **Symptom:** with profiles under both "name:energir sec" (held by an AP lead) and "name:energir", the update that recomputes vendor keys left the hold on the old key, which no invoice gives any more: the supplier's profile now read "supervised" and could be made touchless. Two lessons that became the same situation stayed apart, one under a key nothing reads.
- **Cause:** the re-keying used `UPDATE OR IGNORE` for supplier profiles and lessons: where the new key was taken, the old row (with its hold, its counts) was silently left behind, unlike vendor list rows (B-73).
- **Reproduce:** two profiles as above, `set_supplier_state("name:energir sec", "held", ...)`, set `vendor_key_rules` to 1 and open the store again.
- **Reproduced:** yes (`name:energir supervised`, `name:energir sec held`; `lessons-style lookup of 'name:energir': supervised`)
- **Fix:** a profile that collides is folded into the one under the new key, the stricter state winning (held, suspended, supervised, touchless) with its dates and audit rate, blank fields filled from the other; a lesson that collides is folded in with its counts added up (the newer value, the latest reason), and each approval's part (B-158) follows its lesson.
- **Test:** `tests/test_sweep3_core.py::test_a_held_supplier_stays_held_when_its_key_merges_under_new_rules`, `tests/test_sweep3_core.py::test_two_lessons_that_become_one_vendor_add_up`
- **Lesson:** Don't re-key rows with `UPDATE OR IGNORE` alone: a row left behind under a dead key takes its controls and counts with it.

#### B-163 · Amounts written "CA$1,234.50", "US$99.00", "250.00 DR" or with a typeset minus were not read · low
- **Where:** `ap_coder/core/safe.py:48` (`_CURRENCY`, `parse_amount`)
- **Symptom:** ERP-register, statement, PO and history rows with amounts like "CA$1,234.50", "C$5", "US$99.00", "250.00 DR" or "−150.00" (U+2212, as Word and some ERP PDFs write it) were skipped as having no amount.
- **Cause:** the currency pattern removed "$" but left "CA", "C" or "US" in front of the number; "DR" was not known (only "CR"); only the ASCII hyphen was read as a minus.
- **Reproduce:** `parse_amount("CA$1,234.50")`, `parse_amount("250.00 DR")`, `parse_amount("−150.00")`.
- **Reproduced:** yes (`None` for each)
- **Fix:** "CA$", "C$", "US$" and "CDN" are removed with the currency (longest first); U+2212 is read as a minus; a trailing "DR" (a debit) is dropped and leaves the amount positive, the counterpart of "CR" (a credit, negative), as registers and statements show them.
- **Test:** `tests/test_sweep3_core.py::test_parse_amount_reads_currency_prefixes_debits_and_the_minus_sign`
- **Lesson:** Don't strip a currency symbol and leave its country letters: remove the whole prefix as people write it.

#### B-164 · The update's safety copy of a schema-1 database could not be restored · low
- **Where:** `ap_coder/core/store.py:3420` (`_check_backup_file`, `check_backup`)
- **Symptom:** after an update from the first version, restoring `ap_coder-…-before-update-from-schema-1.db` (the copy made to undo the update) was refused: "… is not an AP Coder database".
- **Cause:** the check required a `schema_version` row, which the first version never wrote; the store itself (`_migrate`, `database_schema`) reads a missing row as version 1.
- **Reproduce:** a database with the first version's tables and no `schema_version` row, opened by `Store()` (it makes the safety copy), then `Store.check_backup(safety_copy)`.
- **Reproduced:** yes (`restore refused: ap_coder-20261019-210038-before-update-from-schema-1.db is not an AP Coder database`)
- **Fix:** a missing row is version 1 when the file has the first version's shape (AP Coder's `invoices` and `feedback` tables, `invoices` without the `export_batch` column added in version 3); a newer database whose row was removed is still refused (tests/test_backups.py).
- **Test:** `tests/test_sweep3_core.py::test_the_safety_copy_of_a_schema_1_database_can_be_restored`, `tests/test_sweep3_core.py::test_a_database_without_ap_coders_tables_is_still_refused`
- **Lesson:** Don't make a check stricter than the code it guards: read a missing version the way the migration reads it.

#### B-165 · A GL CSV with Excel's trailing blank rows was refused · low
- **Where:** `ap_coder/core/reference_data.py:161` (`load_table`)
- **Symptom:** a chart of accounts saved from Excel with empty rows under the list (`,` lines) was refused: "row 3 has an empty 'gl_code'".
- **Cause:** every row was checked for a code, including rows with nothing in any cell.
- **Reproduce:** `load_table()` on `GL Code,Description` / `6000,Office` / `6100,Travel` / `,` / `,`.
- **Reproduced:** yes (`ValueError: …gl.csv: row 3 has an empty 'gl_code'`)
- **Fix:** a row with every cell blank is skipped before the checks; a row with a description and no code is still refused, under its row number in the file.
- **Test:** `tests/test_sweep3_core.py::test_a_gl_csv_with_excels_trailing_blank_rows_loads`
- **Lesson:** Don't treat an all-blank row from a spreadsheet as a row with a missing value: skip it first.

#### B-166 · A supplier abroad ("London, UK", "Paris, France") made the coding invalid · low
- **Where:** `ap_coder/core/schema.py:245` (`province_code`, `_province_at_end`)
- **Symptom:** a model that wrote the supplier's place as "London, UK" or "Paris, France" got its answer refused ("province must be a Canadian province code"), using up its one repair turn (or failing the read) for an invoice that simply comes from outside Canada.
- **Cause:** only US states and the USA were known as outside Canada; any other country fell through to the error.
- **Reproduce:** `province_code("London, UK")`, `province_code("Paris, France")`.
- **Reproduced:** yes (`ValueError: province must be a Canadian province code, got 'London, UK'`)
- **Fix:** an address ending with a country Canadian businesses commonly buy from (UK, France, Germany, China, Mexico, Ireland…) is OUTSIDE_CANADA; the province at the end still wins ("London, ON"), and a place nothing identifies is still an error.
- **Test:** `tests/test_sweep3_core.py::test_a_place_abroad_is_outside_canada`, `tests/test_sweep3_core.py::test_canadian_places_named_like_places_abroad_stay_canadian`
- **Lesson:** Don't treat "not a province" as "an error" when the value is a place abroad: outside Canada is an answer.

### Tax and coding rules

#### B-132 · Bulk approval takes a clean credit note · medium
- **Where:** `ap_coder/coding/bulk.py:22-24` (`_needs_a_person`, used by `clean_candidates` and `bulk_approve`)
- **Symptom:** a credit note with no warning (it credits an approved invoice) is listed as clean on the queue and approved by "Approve all clean" with nobody opening it, although Settings → Automation says a credit is always applied by a person.
- **Cause:** bulk approval only looked at the findings' severity and `ALWAYS_A_PERSON`; the credit-note gate lives in `capture/supplier.touchless_gates` (a total below zero, or titled a credit note) and bulk.py never asked it.
- **Reproduce:** approve the Northwind ON sample, then process a negated copy `CN-1` with `original_invoice_number` set (only info findings `CREDIT_NOTE_FOR`), and call `clean_candidates` / `bulk_approve`.
- **Reproduced:** yes (`assert (2 not in [2, 3])`: the credit note and a positive one titled a credit note were both listed and approved)
- **Fix:** `_credit_note()` asks `touchless_gates(grand_total=..., credit_note=<capture's credit_note>)`, the touchless rules' own notion; `clean_candidates` leaves such an invoice out and `bulk_approve` skips it with "it is a credit note: a credit is always applied by a person".
- **Test:** `tests/test_exports_bulk.py::test_bulk_approval_never_takes_a_credit_note`
- **Lesson:** Don't write a second list of what always needs a person: ask the gate the touchless rules use.

#### B-133 · A line on an unlisted home cost center lets another site decide the place of supply · medium-low
- **Where:** `ap_coder/coding/tax.py:311-314` (`supply_source`)
- **Symptom:** we are in Alberta with `CC400=BC` in Settings → Review; an invoice with one line to CC400 and one to the home cost center CC100 is checked (and self-assessed in the tax return) as a BC supply: PST expected on the whole invoice.
- **Cause:** cost centers not in `site_provinces` (and blank ones) were dropped from the set of sites, so the one listed site looked like "every line goes to BC".
- **Reproduce:** `supply_source(coding([(1000, ["GST"], "CC400"), (1000, ["GST"], "CC100")], ...), TaxSetup(our_province="AB", site_provinces={"CC400": "BC"}))`.
- **Reproduced:** yes (`('BC', 'site')`, expected `('AB', 'ours')`)
- **Fix:** an unlisted (or blank) cost center counts as our province; a site's province is used only when every line maps to it. Without `our_province` set, unlisted cost centers still say nothing (as before). taxreturn.py goes through the same `supply_source` (B-13), so the tax return follows.
- **Test:** `tests/test_tax.py::test_a_line_on_an_unlisted_site_is_delivered_to_our_province`
- **Lesson:** Don't drop the values a lookup doesn't list: an unlisted key has a default meaning too.

#### B-134 · A site typed in lower case in Settings is never matched · low
- **Where:** `ap_coder/coding/tax.py:311` (`supply_source`)
- **Symptom:** "cc400=BC" typed in Settings → Review is saved, but lines coded to CC400 are still taxed as delivered to our province.
- **Cause:** `site_provinces` keys were compared with the line's cost center exactly (case and spaces), while the JD Edwards mapping compares cost centers upper-cased.
- **Reproduce:** `supply_source(<one line on CC400>, TaxSetup(our_province="AB", site_provinces={"cc400": "BC"}))`.
- **Reproduced:** yes (`('AB', 'ours')`, expected `('BC', 'site')`)
- **Fix:** both sides are trimmed and upper-cased before the lookup.
- **Test:** `tests/test_tax.py::test_a_site_typed_in_lower_case_is_still_that_cost_center`
- **Lesson:** Don't compare codes a person types more strictly than the rest of the app compares them.

#### B-135 · "End of month" in the interest clause makes Net 30 count from month end · medium-low
- **Where:** `ap_coder/coding/terms.py:141` (`parse_terms`)
- **Symptom:** "Net 30, interest of 2% per month charged at end of month on overdue accounts" on an invoice of 2026-09-10 is due 2026-10-30 instead of 2026-10-10: the due-date sort and the overdue check are 20 days late.
- **Cause:** EOM was any "end of month" anywhere in the terms, including the late-payment clause.
- **Reproduce:** `payment({"invoice_date": "2026-09-10", "payment_terms": "Net 30, interest of 2% per month charged at end of month on overdue accounts", "grand_total": 1130}).due`.
- **Reproduced:** yes (`2026-10-30`, eom=True)
- **Fix:** `_eom()` leaves out an EOM whose clause has interest words before it ("interest ... at end of month", "Overdue accounts are charged ... at the end of the month"); "Net 30 EOM, interest 2% per month" keeps its EOM.
- **Test:** `tests/test_terms.py::test_end_of_month_in_the_interest_clause_does_not_make_the_terms_eom`
- **Lesson:** Don't read the words of the late-payment clause as the payment terms (B-83 again, for EOM).

#### B-136 · "Net 2 weeks", "Net 3 semaines" and "Net 10 prox" are read as days · low
- **Where:** `ap_coder/coding/terms.py:76-79` (`_NET`)
- **Symptom:** "Net 2 weeks" is due 2 days after the invoice, "Net 3 semaines" 3 days, "Net 10 prox" 10 days (instead of the 10th of the next month); "2% 2 weeks net 30" offers a 2-day discount.
- **Cause:** the "net N" pattern only refused months after the number; weeks and prox were never parsed.
- **Reproduce:** `payment({"invoice_date": "2026-09-10", "payment_terms": "Net 2 weeks", "grand_total": 1}).due`.
- **Reproduced:** yes (`2026-09-12` net=2; "Net 3 semaines" `2026-09-13`; "Net 10 prox" `2026-09-20`)
- **Fix:** weeks / semaines count 7 days each (also "Payable within 2 weeks"); "Net N prox" (also "10th prox", "proximo") is due on day N of the month after the invoice's (last day of a shorter month), `Terms.prox_day`; a discount's number before weeks is not its days. A slash discount's "2/10th prox" is not read as prox.
- **Test:** `tests/test_terms.py::test_weeks_and_prox_terms_are_never_read_as_days`
- **Lesson:** Don't take the number after "net" as days without looking at the unit that follows it.

#### B-137 · "Net EOM" with no days is due 30 days after the invoice · low
- **Where:** `ap_coder/coding/terms.py:172` (`parse_terms`, `payment`)
- **Symptom:** "Net EOM" / "Due EOM" on an invoice of 2026-09-10 is due 2026-10-10 (the default days, source "default") instead of 2026-09-30.
- **Cause:** EOM terms with no day count gave no net days, so `payment()` fell back to the default days from the invoice date.
- **Reproduce:** `payment({"invoice_date": "2026-09-10", "payment_terms": "Net EOM", "grand_total": 1})`.
- **Reproduced:** yes (`due=2026-10-10 src=default`)
- **Fix:** EOM terms with no days are net 0 from the end of the invoice's month.
- **Test:** `tests/test_terms.py::test_eom_terms_with_no_days_are_due_at_the_end_of_the_month`
- **Lesson:** Don't fall back to the default when the terms printed say when it is due, only without a number.

#### B-138 · A PO export's "Item" column (an SKU) is taken as the PO line number · low
- **Where:** `ap_coder/coding/po.py:52` (`COLUMN_ALIASES`, `map_columns`)
- **Symptom:** importing a PO file with columns "PO Number, Vendor, Item, Description, ..." guesses "Item" as the line number: SKU 88341 becomes PO line 88341 (and "WID-0042" no number), shown on the PO and its matches.
- **Cause:** "item" and "itemnumber" were aliases of the line number, though in most exports they hold the part number.
- **Reproduce:** `map_columns(["PO Number","Vendor","Item","Description","Qty","Unit Price","Amount"])` then `rows_from_records` with Item "88341".
- **Reproduced:** yes (`'line_number': 'Item'`, row `line_number: 88341`)
- **Fix:** "item" / "itemnumber" are no longer guessed as the line number; lines without a line column are numbered in file order on import, and a person can still pick the column by hand.
- **Test:** `tests/test_po.py::test_an_item_column_is_an_sku_never_the_po_line_number`
- **Lesson:** Don't guess a column from a header that usually means something else.

### Exports, ERP and reports

#### B-150 · A statement's payment line with the invoice's number is taken for the invoice · medium
- **Where:** `ap_coder/erp/statements.py:177` (`reconcile`)
- **Symptom:** a vendor statement whose payment or credit line carries the invoice's number (a Credit column, or a Type of PMT / CHQ) shows "Not received" for it, so AP chases a bill it has; listed newest first, the payment takes the invoice's match ("Amount differs") and the invoice itself shows "Not received".
- **Cause:** only a payment word in a free-text column was checked against the sign; a Debit/Credit line or a Type code the word list did not know (PMT, CHQ) was matched like an invoice, against any document with that number whatever its sign.
- **Reproduce:** statement rows `PMT 1001 -500.00`, `INV 1001 500.00` (or `Credit 500.00` before `Debit 500.00`) against invoice 1001 for 500.00.
- **Reproduced:** yes (`amount_differs 1001 -500.0 1`, `not_received 1001 500.0 None`; with a Type column `not_received 1001 -500.0` and `not_received CHQ 88123`)
- **Fix:** a line whose sign is opposite to every AP Coder document with its number is the payment / credit applied, whatever its words; a line never takes a document of the opposite sign (a credit line still matches a credit note AP holds under that number); pmt, pymt and chq are payment words.
- **Test:** `tests/test_statements.py::test_a_payment_line_carrying_the_invoice_number_never_takes_the_invoice`
- **Lesson:** Don't match a signed line to a document of the opposite sign because the number agrees.

#### B-151 · An invoice approved without a stored distribution is missing from the CSV exports · low-medium
- **Where:** `ap_coder/erp/exports.py:104` (`line_rows`)
- **Symptom:** an approved invoice from an older version (no `gl_distribution` stored) is marked as in the batch, but the CSV, Custom CSV and the xlsx "GL lines" sheet have no row for it; the JD Edwards files of the same batch have its lines.
- **Cause:** `line_rows` read only the stored distribution; `jde._distribution` rebuilt it, but only for jde.py.
- **Reproduce:** `exports.build("csv", [{"id": 5, "final_output": <ground truth without gl_distribution>}], {}, 3, {})`.
- **Reproduced:** yes (`csv rows: 0`, `custom rows: 0`, `jde F0911Z1 rows: 5`)
- **Fix:** `exports.distribution()` returns the stored lines, else builds them again from the coding with the store's tax treatments (passed by web/pages/exports.py as `tax_treatments`, so a recoverable tax line keeps its GL account); jde.py uses the same helper.
- **Test:** `tests/test_exports_bulk.py::test_an_invoice_approved_without_a_stored_distribution_still_has_its_lines_in_the_export`
- **Lesson:** Don't give one reader of a stored record a fallback the others don't share: put it in one helper.

#### B-152 · Quick search hides another vendor's ERP bill with an exported invoice's number and amount · low-medium
- **Where:** `ap_coder/erp/search.py:113` (`find`)
- **Symptom:** an ERP-register row is missing from the quick search whenever any exported invoice, of any vendor, has the same number and amount (or the same amount with the other sign); searching the other vendor's name finds nothing.
- **Cause:** the "its own ERP entry" key was (invoice number, absolute amount): no vendor, no sign.
- **Reproduce:** export Chinook's invoice 1001 for 500.00, import an ERP register row `Other Supplies Ltd, 1001, 500.00`, search "1001" and "other supplies".
- **Reproduced:** yes (`[('AP Coder', 'Chinook Courier & Office Services Ltd.')]` and `[]`)
- **Fix:** the key keeps the amount's sign and the vendor: the same vendor key, or one the start of the other (the ERP's short name, "CHINOOK COURIER", still counts as the same vendor).
- **Test:** `tests/test_search.py::test_another_vendors_erp_bill_with_the_same_number_and_amount_is_still_found`
- **Lesson:** Don't de-duplicate two records on a key that leaves out who they belong to.

#### B-153 · The ERP's document number is guessed as the supplier's invoice number · low-medium
- **Where:** `ap_coder/erp/registers.py:18` (`COLUMN_ALIASES`)
- **Symptom:** an ERP register with both "Document Number" (or "Doc No") and "Supplier Invoice" / "Vendor Invoice" columns is imported with the ERP's own document number as the invoice number, so the duplicate check against the ERP never matches.
- **Cause:** the aliases were tried in order and "documentnumber" / "docno" came before "vendorinvoice" / "supplierinvoice".
- **Reproduce:** `registers.map_columns(["Supplier Name", "Document Number", "Supplier Invoice", "Date", "Amount"])`.
- **Reproduced:** yes (`invoice_number: 'Document Number'`)
- **Fix:** the supplier / vendor invoice aliases (and their "number" / "no" forms) come first; the document number only when there is nothing better.
- **Test:** `tests/test_registers.py::test_the_suppliers_invoice_number_is_chosen_over_the_erps_document_number`
- **Lesson:** Don't order column guesses so that a generic name wins over the specific one.

#### B-154 · A bill entered twice in the ERP collapses into one register row · low-medium
- **Where:** `ap_coder/core/store.py:956` (`import_erp_register`), `ap_coder/erp/dupaudit.py:116` (`bills`)
- **Symptom:** the same bill keyed twice in the ERP (same vendor, number and amount) is one row in AP Coder's ERP register, so the duplicate payment audit can never flag it.
- **Cause:** `erp_invoices` has the primary key (vendor_key, number_key, total) and the import upserts on it: the second entry only overwrote the first one's date.
- **Reproduce:** import `[Acme Ltd A-77 812.50 2026-01-05, Acme Ltd A-77 812.50 2026-02-05]`, run `dupaudit.find`.
- **Reproduced:** yes (`stored 1`, `pairs []`)
- **Fix:** `erp_invoices.copies` (a column added whatever the schema version) counts how many times the file lists the bill, kept at the highest count when an overlapping register is imported again (never added up); the audit pairs such a row with itself as EXACT ("entered 2 times"), and the register import's message names how many bills the ERP holds more than once (`Store.erp_register_repeats()`).
- **Test:** `tests/test_dupaudit.py::test_the_same_bill_entered_twice_in_the_erp_is_a_duplicate`
- **Lesson:** Don't make the primary key of imported records the very thing a duplicate check needs to see twice.

#### B-155 · VLCRR is 1 for a CAD invoice when JD Edwards keeps its books in another currency · low-medium
- **Where:** `ap_coder/erp/jde.py:577` (`voucher_rows`)
- **Symptom:** with the JDE domestic currency set to USD, a CAD invoice's F0411Z1 row has VLCRR `1` (and a EUR invoice its CAD rate), so E1 converts at a wrong rate instead of its own table.
- **Cause:** VLCRR was the invoice currency's rate from Settings → Review, which are CAD per unit, whatever the domestic currency.
- **Reproduce:** `jde.export_batch([<CAD invoice>], JdeSettings(domestic_currency="USD", currencies=["CAD","USD"]), AN8, fx_rates={"CAD": 1.0, "USD": 1.37})`.
- **Reproduced:** yes (`CRRM F CRCD CAD CRR 1`)
- **Fix:** `jde._rate()`: domestic units per unit of the invoice's currency, the rate across CAD (1 / 1.37 = 0.729927; EUR 1.50 / 1.37), and blank (E1's exchange-rate table) without a rate for the domestic currency; docs/JDE_E1.md says so.
- **Test:** `tests/test_jde.py::test_the_exchange_rate_is_in_the_domestic_currency_when_it_is_not_cad`
- **Lesson:** Don't send a rate without saying, and converting to, the currency it is quoted in.

#### B-156 · Custom CSV whole-yen lines don't add up to the invoice total · low
- **Where:** `ap_coder/erp/export_layout.py:97` (`_value`)
- **Symptom:** a JPY invoice whose distribution was spread in cents (10.66 + 10.67 + 10.67 + 1000 + 52) is written 11, 11, 11, 1000, 52: 1,085 against an InvoiceTotal of 1,084; and 2.5 yen is written 2.
- **Cause:** each money cell was formatted on its own with `f"{x:.0f}"` (round half to even), with no balancing per invoice as jde.py's vouchers have.
- **Reproduce:** `exports.build("custom", [_yen_invoice(old_distribution=True)], {}, 1, {})`.
- **Reproduced:** yes (`['11', '11', '11', '1000', '52'] sum 1085 total 1084`; `f"{2.5:.0f}"` is `2`)
- **Fix:** money rounds half away from zero on the decimal value; in a currency without cents, when an invoice's lines balance in cents, the largest line takes the difference to the rounded total (11, 11, 11, 999, 52).
- **Test:** `tests/test_export_layout.py::test_whole_yen_lines_add_up_to_the_invoice_total`
- **Lesson:** Don't round posting lines one by one in a coarser unit without giving the remainder to one of them.

#### B-157 · How the line numbers VLEDLN / VNEDLN load is never asked of the JDE team · low
- **Where:** `ap_coder/erp/jde.py:106` (`UNCONFIRMED`), `ap_coder/erp/jde_worksheet.py:44` (`QUESTIONS`)
- **Symptom:** the Z-files write VLEDLN / VNEDLN as 1, 2, 3; E1 shows EDLN with display decimals, so a direct table load may read line 1 as 0.001, and nothing in the README, the worksheet or docs/JDE_E1.md asks the JDE team about it.
- **Cause:** the unconfirmed list and the open questions covered VNEDLN numbering (sequential or matching) but not how the number itself is stored.
- **Reproduce:** read the README's "To confirm with your JDE team" line and the worksheet's Amounts questions.
- **Reproduced:** yes (no mention of EDLN's decimals)
- **Fix:** an UNCONFIRMED entry (in the README), an Amounts question in the worksheet (free text), and the open-questions bullet in docs/JDE_E1.md (still 8 bullets, so test_jde_readiness keeps its count).
- **Test:** `tests/test_jde_readiness.py::test_how_the_line_numbers_load_is_asked_of_the_jde_team`
- **Lesson:** Don't leave a load assumption out of the questions list because the value looks obvious.

#### B-208 · A register listing a bill once per GL line made every multi-line bill an "entered 3 times" duplicate · medium-low
- **Where:** `ap_coder/core/store.py:1038` (`import_erp_register`), `ap_coder/erp/dupaudit.py:158` (`find`)
- **Symptom:** since B-154, an ERP register exported one row per GL line (the bill's total repeated on each line) or with equal JD Edwards pay items showed every multi-line bill as an EXACT duplicate, "entered 3 times", and the import said the ERP holds those bills more than once.
- **Cause:** `copies` counted every row alike in vendor, number and amount, also rows alike in their date: one bill's lines, not a bill keyed again.
- **Reproduce:** review33/s/t2.py: import Acme Ltd A-77 812.50 2026-01-05 three times, then `erp_register_repeats()` and `dupaudit.find()`.
- **Reproduced:** yes (`repeats 1 [Pair(reason='EXACT', ... copies=3) ...]`)
- **Fix:** `copies` is the number of different dates the file lists the bill with (a row with no date adds none); rows alike in their date too collapse into one bill as before B-154. The register has no ERP document number column to tell two entries on the same day apart, so those still collapse.
- **Test:** `tests/test_dupaudit.py::test_a_register_listing_a_bill_once_per_gl_line_is_not_a_bill_entered_three_times` (and B-154's `test_the_same_bill_entered_twice_in_the_erp_is_a_duplicate`)
- **Lesson:** Don't count identical rows as repeated entries: an export at line level repeats its header on every line.

### AI and readers

#### B-174 · A credit note with unsigned lines under a negative subtotal got an invented charge line · medium
- **Where:** `ap_coder/ai/offline_coder.py:287` (`code_from_capture`)
- **Symptom:** a credit note that prints its lines positive (1,450.00 and 600.00) but its subtotal and total negative (-2,050.00) was coded as two lines owed (+1,450, +600) plus a "Charges not in the line table" line of -4,100, with no check raised.
- **Cause:** the lines read were compared with the subtotal as printed: the difference, minus twice the subtotal, was taken for a charge printed outside the table.
- **Reproduce:** the sample `northwind_ON_HST_CN-2026-0047.md` with the lines' minus signs removed (or a PDF credit note so printed), through the offline pipeline.
- **Reproduced:** yes (`[('Return - Dell Latitude 7450 la', 1450.0), ('Credit - implementation consul', 600.0), ('Charges not in the line table', -4100.0)]`, issues only GL/CC unassigned)
- **Fix:** when the lines sum to minus the (negative) subtotal, they are the credit: their amounts and unit prices take the credit's sign (the quantity keeps its own, as the rule reader codes a credit's line); no charge line is added.
- **Test:** `tests/test_sweep3_ai.py::test_a_credit_note_with_positive_lines_and_negative_totals_codes_the_lines_as_credits`
- **Lesson:** Don't explain a difference of exactly twice a total as something extra: check whether the sign is what differs.

#### B-175 · Past 50,000 lessons, the vendors imported first lost all their learning · medium
- **Where:** `ap_coder/ai/pipeline.py:374` (`InvoicePipeline.process`), `ap_coder/core/store.py:3425` (`learning_rows`)
- **Symptom:** once the learning memory held more than 50,000 rows, every line of a vendor whose history was imported first stayed UNASSIGNED, and the history check (`finalise_coding`) saw none of its past coding.
- **Cause:** the pipeline coded with `store.feedback_rows()`, the newest 50,000 rows: an old vendor's rows were all cut off.
- **Reproduce:** import 6 history rows for one vendor, then 50,000 for others; code an invoice from the first vendor.
- **Reproduced:** yes (`50000 rows read: 50000 acme rows read: 0 -> UNASSIGNED`, against 6100 with 1,000 other rows)
- **Fix:** `Store.learning_rows(vendor)`: the shared capped list, with this vendor's lessons that fell past the cap added after it (the shared list itself while nothing is missing, so the suggestion index is not rebuilt). The pipeline (once the vendor is read) and the review screen's suggestions use it; the cap is `store.FEEDBACK_READ`.
- **Test:** `tests/test_sweep3_ai.py::test_a_vendor_imported_first_keeps_its_learning_past_the_memory_read_cap`
- **Lesson:** Don't cap a shared read newest-first and then look up one key in it: read that key's rows too.

#### B-176 · A banking line quoting the invoice number made every later invoice a changed bank account · medium
- **Where:** `ap_coder/ai/offline_coder.py:196` (`bank_details`)
- **Symptom:** a supplier whose banking line reads "EFT payments: Transit 12345 Inst 001 Account 1234567, quoting invoice NW-2026-0912" got VENDOR_BANK_CHANGED on every invoice after the first approved one.
- **Cause:** everything after the first banking word was kept, the invoice number included, so the digits compared differed on each invoice.
- **Reproduce:** two PDFs from the same supplier with that line and their own invoice numbers; approve the first, read the second.
- **Reproduced:** yes (`bank 2: ... Account 1234567, quoting invoice NW-2026-0987`, `VENDOR_BANK_CHANGED`)
- **Fix:** the details stop at the first word that starts something else (invoice, ref, quoting, date, call, phone...) once 5 digits are in, then at the end of the last labelled number (transit, institution, account..., an IBAN or SWIFT after its label).
- **Test:** `tests/test_sweep3_ai.py::test_a_banking_line_that_quotes_the_invoice_number_keeps_only_the_account`
- **Lesson:** Don't keep "the rest of the line" as an identifier: cut it to the part that identifies.

#### B-177 · A self-test mark left by a crash stayed "under way" forever once its process id was reused · medium
- **Where:** `ap_coder/ai/page_worker.py:681` (`test_under_way`), `:790` (`_mark_test`)
- **Symptom:** after a dashboard died during OvisOCR2's self-test, Settings > Reading showed the test running for good, the page reader read nothing, a new test was refused and nothing went touchless, as long as another program had that process id.
- **Cause:** another process's mark was judged by its process id alone; the random token in READER_ID can't be checked from another process.
- **Reproduce:** set the `page_reader_testing` mark with a start three days ago and the pid of a running `sleep`; call `test_under_way`, `self_test`, `start_test`.
- **Reproduced:** yes (`self_test: ('running', 'OvisOCR2 is reading the test invoice now (page 1 of 2)...')`, `start_test: False`)
- **Fix:** the mark notes when it was last renewed (`updated`, at each page); another process's mark not renewed for `TEST_STALE_HOURS` (1 hour) is not under way.
- **Test:** `tests/test_sweep3_ai.py::test_a_self_test_mark_whose_process_id_another_program_now_has_is_not_under_way`
- **Lesson:** Don't take a live process id as proof the process that wrote a mark is alive: give the mark an age limit.

#### B-178 · An unlisted charge was labelled with part of another amount · low
- **Where:** `ap_coder/ai/offline_coder.py:227` (`_unlisted_charge`)
- **Symptom:** a $50.00 delivery charge printed outside the line table became a line described "1,2".
- **Cause:** the figure "50.00" was looked for as a substring and found inside "1,250.00" first.
- **Reproduce:** a capture with one line of 1,250.00, subtotal 1,300.00, and the page text "...1,250.00\nDelivery charge\n50.00".
- **Reproduced:** yes (`[('Widgets', 1250.0), ('1,2', 50.0)]`)
- **Fix:** the amount is matched as a whole figure (no digit, comma or point before it, no digit after); a figure alone on its line takes the line above as its label; a label needs a letter.
- **Test:** `tests/test_sweep3_ai.py::test_an_unlisted_charge_is_labelled_from_its_own_figure_not_part_of_another`
- **Lesson:** Don't search for a number as a substring: match it as a whole figure.

#### B-179 · A Mac's "._" files and Office lock files in the invoices folder failed as damaged invoices · low
- **Where:** `ap_coder/ai/pipeline.py:80` (`invoice_files`)
- **Symptom:** "._inv.pdf" (a Mac's file details, copied beside "inv.pdf" on a shared or USB drive), other hidden files and "~$" lock files were listed and each failed as a damaged invoice.
- **Cause:** every file with an invoice extension was listed, whatever its name.
- **Reproduce:** a folder with "inv.pdf", "._inv.pdf", ".hidden.pdf", "~$notes.txt": `invoice_files(folder)`.
- **Reproduced:** yes (`['._inv.pdf', '.hidden.pdf', 'inv.pdf', '~$notes.txt']`)
- **Fix:** names starting with "." or "~$" are skipped, like README files.
- **Test:** `tests/test_sweep3_ai.py::test_hidden_files_and_lock_files_in_the_invoices_folder_are_not_invoices`
- **Lesson:** Don't take every file with the right extension for a document: hidden and lock files share it.

#### B-180 · A bug in the checks after coding stopped a whole batch · medium
- **Where:** `ap_coder/ai/pipeline.py:428-462` (`InvoicePipeline.process`)
- **Symptom:** an exception in the capture, review issues, touchless decision or holds escaped `process()`, so `process_many()` (a batch import) stopped at that invoice and the rest were not read.
- **Cause:** only the reading and coding were inside the "one bad invoice must not stop a batch" guard; the checks after it were not.
- **Reproduce:** make `autonomy_decision` raise, then `process_many` two sample invoices.
- **Reproduced:** yes (`RuntimeError: a bug` out of `process_many`)
- **Fix:** those checks run inside a guard: the exception is logged, the invoice is saved for a person (never touchless) and the batch goes on.
- **Test:** `tests/test_sweep3_ai.py::test_a_bug_in_the_touchless_decision_does_not_stop_the_batch`
- **Lesson:** Don't guard only part of the work a batch promises to survive: the guard covers every step of one item.

#### B-181 · The AI reviewer's "confident": "false" written as text counted as confident · low
- **Where:** `ap_coder/ai/verifier.py:248` (`_parse`)
- **Symptom:** in json_object or text response modes, a model answering `"confident": "false"` (a string) was taken as confident, so its agreement could clear the reviewer's hold.
- **Cause:** `bool(data.get("confident"))`: any non-empty string is true.
- **Reproduce:** `verifier._parse({"confident": "false", "fields": [{"field": "vendor_name", "agree": true}], "lines": [{"line_number": 1, "agree": true}]}, ["vendor_name"], [1])`.
- **Reproduced:** yes (confident `True`)
- **Fix:** only a boolean `true` counts, as for each item's "agree".
- **Test:** `tests/test_sweep3_ai.py::test_the_reviewer_saying_confident_false_as_text_is_not_confident`
- **Lesson:** Don't read a model's yes/no with `bool()`: accept only a real boolean.

#### B-209 · Bank details approved before B-176 made the vendor's next invoice a changed bank account · medium-low
- **Where:** `ap_coder/coding/vendors.py:161` (`same_bank_account`), `ap_coder/coding/vendors.py:146` (`cut_bank_details`)
- **Symptom:** a vendor whose banking line runs on past the account ("... Account 1234567. Questions? Call 416-555-1212", "..., ref. invoice 88231") got VENDOR_BANK_CHANGED on its first invoice read after B-176, which holds it out of bulk approval and touchless.
- **Cause:** B-176 cut the details read from new invoices after the account, but the approved invoices compared with kept the whole line they were stored with, so their digits (the phone or reference number included) differed.
- **Reproduce:** review33/s/t7.py, then `same_bank_account(bank_details(old), old)` for the stored lines above.
- **Reproduced:** yes (`False 'EFT: Transit 00012, Institution 003, Account 1234567' | stored: '... Account 1234567. Questions? Call 416-555-1212'`, the same for the wire and "Ref 88231" lines)
- **Fix:** the cutting moved from `offline_coder.bank_details` to `vendors.cut_bank_details` (with `bank_digit_count`); `same_bank_account` also compares both sides cut at the account (each of the "; "-joined details), so a stored long line matches the same account read now; another account number still differs.
- **Test:** `tests/test_sweep3_ai.py::test_bank_details_stored_before_they_were_cut_at_the_account_are_the_same_account`
- **Lesson:** Don't change how a value is read without comparing the values already stored the same way.

### Document capture

#### B-182 · A currency code in an equivalent note wins over the code on the total's label · high
- **Where:** `ap_coder/capture/normalize.py:476` (`currency_evidence`)
- **Symptom:** A US-dollar invoice printing "Total USD" (its amount in the next column) and a note "CAD equivalent: CAD 1,438.50" is read as Canadian dollars, verified.
- **Cause:** Only a code printed on an amount on the same line counted as on the amounts; the layout reads "Total USD" and "1,050.00" as two lines, so the only code "on an amount" was the equivalent note's. Several codes fell back to the first in the list of currencies (CAD), not the first printed.
- **Reproduce:** `currency_evidence("Total USD\n1,050.00\nCAD equivalent: CAD 1,438.50")`; on a page, `analyze()` with the page reader.
- **Reproduced:** yes (`{'currency': ('CAD', 'verified', 0.998)}`)
- **Fix:** A code on the total's label line ("Total USD", "Amount Due CAD", "Montant dû") counts as on the total and comes before codes on other amounts; an equivalent note is an exchange note (not on the invoice's amounts); several codes give the first printed.
- **Test:** `tests/test_sweep3_capture.py::test_the_code_on_the_total_line_is_the_currency_not_one_in_an_equivalent_note`, `tests/test_sweep3_capture.py::test_several_currency_codes_are_given_in_page_order`
- **Lesson:** Don't decide that a code is on an amount from one line of text when the layout can put the label and the amount in two columns.

#### B-183 · "Order ID 77812" read as an Idaho address makes a Calgary supplier's dates month first · high
- **Where:** `ap_coder/capture/reader.py:1419` (`_US_ADDRESS`, `us_address`, `read_fields`)
- **Symptom:** A Calgary supplier's invoice dated 03/04/2026 with "Order ID 77812" near the top is verified as March 4 (and its due date 03/05/2026 as March 5), not left ambiguous or settled by its other dates.
- **Cause:** The US address pattern took any two capitals and five digits ("ID 77812": Idaho), and a US address forced month-first dates before the Canadian postal code was looked at; the order then dropped the ambiguous mark. `offline_coder.provinces()` used the same pattern, so the same line above the letterhead's address made the supplier OUTSIDE_CANADA.
- **Reproduce:** the Acme invoice with "Order ID 77812", Invoice Date 03/04/2026, Due Date 03/05/2026, `analyze(..., page_text=[...])`, today 2026-04-10.
- **Reproduced:** yes (`{'invoice_date': ('2026-03-04', 'verified', 0.997), 'due_date': ('2026-03-05', 'verified', 0.991)}`)
- **Fix:** A state and ZIP code count as an address only after a city, a comma or at the start of a line, never after a label word (Order, Vendor, Store, Job, Ticket...): `us_address()`, used by the reader and `provinces()`. Month first is never forced on a supplier with a Canadian postal code or a valid GST/HST number.
- **Test:** `tests/test_sweep3_capture.py::test_an_order_id_is_not_a_us_address_and_a_calgary_suppliers_date_is_not_read_month_first`, `tests/test_sweep3_capture.py::test_a_label_with_an_id_is_not_a_us_address`, `tests/test_sweep3_capture.py::test_a_canadian_supplier_billing_in_us_dollars_is_not_read_month_first`
- **Lesson:** Don't let a two-letter code and five digits stand for an address: check the shape around it, and the evidence against it.

#### B-184 · "Due Date: Upon Receipt" takes the delivery date printed under it · medium
- **Where:** `ap_coder/capture/reader.py:635` (`_labelled`)
- **Symptom:** An invoice printing "Due Date: Upon Receipt" (or "Due Date: N/A") over "Delivery Date: Sep 20, 2026" has its due date verified as 2026-09-20; its terms are not read.
- **Cause:** When the label's own line held no date, the reader looked right of it and then below it, and took the first date it found there even when that date came after a label of its own ("Delivery Date:"). The page reader's transcription, read by the same rules, agreed. "Upon Receipt" after a due-date label was never read as the terms.
- **Reproduce:** the Acme invoice with "Invoice Date: Sep 14, 2026", "Due Date: Upon Receipt", "Delivery Date: Sep 20, 2026" on three lines, `analyze(..., page_text=[...])`.
- **Reproduced:** yes (`{'due_date': ('2026-09-20', 'verified', 0.99), 'payment_terms': (None, 'missing', 0.0)}`)
- **Fix:** A value right of or below a label is not taken when it comes after another label on its own line (`_after_another_label`: a label shape, a colon, or another field's label before it). A due-date label followed by "Upon Receipt" gives the payment terms "Due on receipt" when no terms label was read.
- **Test:** `tests/test_sweep3_capture.py::test_due_upon_receipt_does_not_take_the_delivery_date_below_it`
- **Lesson:** Don't take a value from a neighbouring line without checking it isn't already another label's value.

#### B-185 · An invoice number printed in groups of three is cut at the first space · medium
- **Where:** `ap_coder/capture/reader.py:328` (`_id_from`)
- **Symptom:** "No de facture 123 456" is read as invoice number 123, verified (the page reader's transcription, read by the same rules, agrees).
- **Cause:** An identifier ended at the first space unless a short prefix of letters came before it ("INV 4471"); digit groups set apart the French way were never joined.
- **Reproduce:** the Acme invoice with "No de facture" and "123 456" in the next column, `analyze(..., page_text=[...])`.
- **Reproduced:** yes (`{'invoice_number': ('123', 'verified', 0.998)}`)
- **Fix:** A number of one to three digits followed only by groups of three digits, each one space apart, with nothing else after it on the line, is read whole ("123 456"; compared without spaces).
- **Test:** `tests/test_sweep3_capture.py::test_an_invoice_number_in_french_digit_groups_is_read_whole`
- **Lesson:** Don't end a number at a space before checking whether the space is part of how the number is printed.

#### B-186 · Text drawn twice for bold is read twice ("Acme Acme Supply Supply") · low
- **Where:** `ap_coder/capture/layout.py:192` (`_text_layer_page`)
- **Symptom:** On a PDF whose writer makes bold by drawing the text twice a hair apart, every bold word is read twice: the supplier is "Acme Acme Supply Supply Ltd. Ltd." and a bold "Invoice Date:" label is not recognised, so the invoice date is missing.
- **Cause:** The text layer's words went into the layout as they were stored; only the text-layer check (`_printed_once` in `capture/__init__.py`) kept a word drawn over itself once, so the readers saw both copies.
- **Reproduce:** the Acme invoice with "Acme Supply Ltd." and "Invoice Date:" each inserted again 0.3 pt to the right, `analyze(..., ocr=False)`.
- **Reproduced:** yes (`{'vendor_name': ('Acme Acme Supply Supply Ltd. Ltd.', 'likely'), 'invoice_date': (None, 'missing')}`)
- **Fix:** The layout drops a text-layer word with the same text as one of the last few words that covers mostly the same spot; the text-layer check now reads the layout's lines as they are (its `_printed_once` moved into the layout).
- **Test:** `tests/test_sweep3_capture.py::test_text_drawn_twice_for_bold_is_read_once`
- **Lesson:** Don't clean the input in one consumer only: clean it where it is built, so every reader sees the same page.

#### B-187 · "HST 13% on $1,000.00" reads the base as the tax · low
- **Where:** `ap_coder/capture/reader.py:487` (`_value_reading`)
- **Symptom:** A tax line "HST 13% on $1,000.00" with 130.00 in the amount column reads the HST as 1,000.00 (both readers agree), so the totals fail and the subtotal, tax and total all go to Check.
- **Cause:** The amount after the label on its own line was taken first, and the base the tax is charged on is an amount on that line.
- **Reproduce:** the Acme invoice with "HST 13% on $1,000.00" and "130.00" in the next column, total 1,130.00, `analyze(..., page_text=[...])`.
- **Reproduced:** yes (`hst_amount 1000.0 check`, `TOTALS_ADD_UP` and `TAX_RATE` failed)
- **Fix:** For a tax or tax total, an amount right after "on" / "sur" is the base, not the tax: it is skipped, and the tax is read further right or in the next column.
- **Test:** `tests/test_sweep3_capture.py::test_the_amount_a_tax_is_charged_on_is_not_the_tax`
- **Lesson:** Don't take the first amount after a tax label without asking whether it is the tax or what the tax is charged on.

#### B-188 · "Modalités : Net 30 jours" is not read as the terms · low
- **Where:** `ap_coder/capture/reader.py:139` (`LABELS["payment_terms"]`)
- **Symptom:** A French invoice printing "Modalités : Net 30 jours" has no payment terms.
- **Cause:** "Modalités" was a terms label only as "Modalités de paiement".
- **Reproduce:** the Acme invoice with "Modalités : Net 30 jours", `analyze(..., page_text=[...])`.
- **Reproduced:** yes (`{'payment_terms': (None, 'missing', 0.0)}`)
- **Fix:** "Modalités" alone labels the terms (strength 0.8, as "Termes"); "Modalités de/du/d'..." (livraison, expédition) does not.
- **Test:** `tests/test_sweep3_capture.py::test_modalites_alone_labels_the_payment_terms`
- **Lesson:** Don't list only the long form of a label when invoices print its short form too.

#### B-189 · Payment terms printed only in a note were not read, so a "payable on receipt" invoice showed as overdue · low
- **Where:** `ap_coder/capture/reader.py` (`read_fields`, payment terms)
- **Symptom:** the Harbourview sample prints "HST charged at 14%, ... Payable on receipt." with no "Terms:" label: terms came out "not found", the due date fell back to 30 days and the invoice showed "Overdue by 50 days".
- **Cause:** payment terms were only read after a terms label (or a due-date label); a terms phrase inside a sentence was never looked at.
- **Reproduce:** read `samples/harbourview_NS_HST_HPS-2026-0347.pdf`: `payment_terms` missing. Found by the pilot tester's day-one walk-through.
- **Reproduced:** yes (`payment_terms` status missing).
- **Fix:** when no labelled terms are found, a "payable/due (up)on receipt" or "payable à réception" phrase anywhere on the page is read as "Due on receipt", at a lower strength than a labelled value.
- **Test:** `tests/test_sweep3_capture.py::test_payable_on_receipt_in_a_note_is_read_as_the_terms`
- **Lesson:** Don't look for a value only beside its label when documents also state it in a sentence.

#### B-205 · Printed discount terms "2/10 Net 30" were read as "1% 0 Net 30" · medium
- **Where:** `ap_coder/capture/reader.py` (`_TERMS`, the discount pattern)
- **Symptom:** an invoice printing "2/10 Net 30" offered a 1% discount within 0 days; "2% 10 days, Net 30" lost its discount (plain Net 30). Found while taking the guide's screenshots.
- **Cause:** the pattern allowed no "/" between the rate and the days, so it started inside "10" ("1", "0"); the word "days" after the discount days broke the match.
- **Reproduce:** `reader._terms("2/10 Net 30")`.
- **Reproduced:** yes (`1% 0 Net 30`).
- **Fix:** a separator is required between the rate and the days ("%", "/", a space or a dot), neither number may start or end inside another, "days"/"jours" may follow the discount days, and "n/30" counts as net.
- **Test:** `tests/test_sweep3_capture.py::test_printed_early_payment_discount_terms_keep_the_discount`
- **Lesson:** Don't let a number pattern start inside another number: anchor both ends.

#### B-214 · The GST/HST registration-number threshold rose to $100 from 2021-01-01, but the law changed on April 21, 2021 · low
- **Where:** `ap_coder/coding/taxreturn.py` (`document_threshold`)
- **Symptom:** an invoice of $30 to $99.99 dated between January 1 and April 20, 2021, with no registration number, was not listed among the claims at risk on the Sales tax page.
- **Cause:** the cutoff date was set to the start of 2021; the Input Tax Credit Information (GST/HST) Regulations were amended by Budget 2021 with effect from April 21, 2021 (Justice Canada's current text; a tax-law summary gives the date). Found while fact-checking the guide.
- **Reproduce:** `taxreturn.document_threshold("2021-04-20")`.
- **Reproduced:** yes (100.0, not 30.0).
- **Fix:** the cutoff is April 21, 2021 (`DOCUMENT_THRESHOLD_FROM`); the Sales tax page and WHATS_NEW say so.
- **Test:** `tests/test_taxreturn.py::test_registration_number_needed_from_100_dollars_since_2021`
- **Lesson:** Don't round a legal effective date to the start of a year: take the date the rule names.

### Web routes

#### B-190 · This computer's browser at its own network address was taken for a colleague · medium
- **Where:** `ap_coder/web/identity.py:126` (`Identity.__call__`, `is_this_computer`)
- **Symptom:** with `AP_DASHBOARD_ADDRESS=0.0.0.0`, the person on the computer running AP Coder opens
  `http://<this PC's IP>:8501`, is asked "Who are you?", gives another name and gives the second approval of an
  invoice they approved first on the same computer (D-34 says it must be refused).
- **Cause:** only a loopback client address counted as this computer; a browser on the server PC that opens its LAN
  address connects from that address, so it was "network <ip>", a login different from the OS user's. Also
  `is_this_computer("::ffff:127.0.0.1")` was False.
- **Reproduce:** approve on this computer, then a client at 192.0.2.2 to `http://192.0.2.2:8501` posts `/who`
  "S. Owner" and `/review/{id}/final-approve`.
- **Reproduced:** yes (`STATUS approved Server Owner S. Owner`, event `final_approved` by `network 192.0.2.2`)
- **Fix:** `identity.from_this_computer(scope)`: a client whose address is the one the connection arrived on
  (`scope["server"][0]`) is this computer; `is_this_computer` unwraps IPv4-mapped addresses. `Identity` and
  `pages/who.py` use it (`/shutdown` stays loopback only).
- **Test:** `tests/test_network_identity.py::test_this_computers_browser_at_its_own_network_address_cannot_give_the_second_approval`
- **Lesson:** Don't decide "this computer" from loopback alone: the same machine also reaches itself at its own network address.

#### B-191 · Two bank accounts ending in the same 4 digits were one row of the vendor's bank history · medium
- **Where:** `ap_coder/web/pages/vendors.py:138` (`vendor_detail`, `history`)
- **Symptom:** a vendor's invoices switch to another account at another bank that ends in the same 4 digits: the
  review screen warns VENDOR_BANK_CHANGED, but the vendor's "Bank account and GST/HST number history" shows one row
  (…4567, 2 invoices) and no "Changed" pill.
- **Cause:** the history grouped values by their masked form (`mask_account(a) == mask_account(b)`), not by the
  account (`same_bank_account`, which the check uses).
- **Reproduce:** approve an invoice paid into "Transit 00012, Institution 003, Account 5011234567", add one with
  "Transit 47210, Institution 815, Account 9907754567", open the vendor on /vendors.
- **Reproduced:** yes (`bank history rows: [('…4567', False, '2026-08-01', '2026-09-01', '2')]`)
- **Fix:** grouped with `same_bank_account` (details without any number still go together); rows that mask alike
  also show their bank and branch numbers (`coding/vendors.bank_branch()`, never the account's digits), or "another
  account number" when those are alike too.
- **Test:** `tests/test_web_masterdata.py::test_a_new_bank_account_ending_in_the_same_four_digits_shows_as_changed`
- **Lesson:** Don't group records by their masked display value: compare what they are, then mask for showing.

#### B-192 · Colleagues' unsaved edits were kept by name only · low-medium
- **Where:** `ap_coder/web/pages/invoice.py:93` (`_drafts`)
- **Symptom:** "Mike" on one computer and "mike" on another see each other's unsaved edits on the review screen; a
  colleague who gives a new name on /who loses the edits made under the old one, Start over can't drop them, and bulk
  approval skips that invoice ("it has edits open on its review screen") until AP Coder restarts.
- **Cause:** `person_drafts` was keyed by `name.casefold()` alone, and nothing moved the drafts when the cookie's name changed.
- **Reproduce:** colleagues at 10.0.0.5 "Mike" and 10.0.0.9 "mike"; A posts `/review/{id}/check` with a PO; B opens
  `/review/{id}`. Then "Ana" edits, renames to "Ana Silva" on /who, presses Start over, and this computer bulk-approves.
- **Reproduced:** yes (`Mike B's screen shows Mike A's unsaved edit: True`, `Ana Silva sees her edit: False`,
  `after Start over, draft still held: True`, bulk approval skipped it)
- **Fix:** drafts keyed by (name, case aside, and the colleague's address); `who_save` moves the asker's drafts to
  their new name (`invoice.rename_drafts()`).
- **Test:** `tests/test_network_identity.py::test_two_colleagues_who_give_one_name_on_two_computers_keep_their_own_edits`,
  `tests/test_network_identity.py::test_a_colleague_who_changes_their_name_keeps_their_edits_and_can_start_over`
- **Lesson:** Don't key a person's state by the name they typed alone: a name is neither unique nor fixed.

#### B-193 · A link from another site to the dashboard asked a colleague "Who are you?" again · low
- **Where:** `ap_coder/web/pages/who.py:36` (`who_save`)
- **Symptom:** a colleague who gave their name follows a link to AP Coder from another site (the intranet, a webmail)
  and is asked "Who are you?" again; typing the address works.
- **Cause:** the signed name cookie was `SameSite=Strict`, which the browser leaves out of a navigation started on
  another site.
- **Reproduce:** in Chromium, give a name on `http://<server>:<port>/who`, then click a link to `/review` on a page
  served from another origin.
- **Reproduced:** yes (`link from another site: .../who?next=%2Freview True False`)
- **Fix:** `SameSite=Lax`: the cookie comes with a top-level GET from a link (a GET never changes anything); a POST
  from another site is still refused by LocalOnly (header `X-AP-Coder`, Origin, Sec-Fetch-Site).
- **Test:** `tests/test_network_identity.py::test_a_link_from_another_site_keeps_who_the_colleague_is` (the cookie's
  attributes, and a cross-site POST still 403; the sweep's browser repro used the server's own address, which B-190
  now counts as this computer)
- **Lesson:** Don't make a login cookie Strict when the CSRF defence is already elsewhere: it only breaks incoming links.

#### B-194 · "Bring back" on the directory said "Back in the review queue." for an invoice rejected meanwhile · low
- **Where:** `ap_coder/web/pages/search.py:217` (`bring_back`)
- **Symptom:** a parked invoice is rejected in another tab (or by a colleague) after the directory was drawn; "Bring
  back" then opens it with "Back in the review queue." though it is rejected.
- **Cause:** the store's refusal (`ValueError: not parked`) was swallowed as "someone else brought it back
  meanwhile", whatever the invoice's state now.
- **Reproduce:** park an invoice, reject it, POST `/search/{id}/unpark`.
- **Reproduced:** yes (`answer: /review/1 | message shown: ['Back in the review queue.'] | status now: rejected`)
- **Fix:** on the refusal, unless the invoice is in the review queue now, answer with `invoice.changed_meanwhile()`
  ("Not done: this invoice changed meanwhile ... It is rejected.").
- **Test:** `tests/test_web_finance.py::test_bring_back_of_an_invoice_rejected_meanwhile_says_it_changed`
- **Lesson:** Don't swallow a store's refusal on the guess that the wanted state was reached: check the state now.

### Intake, email and command line

#### B-140 · An email whose attachments were partly left out was filed as an ordinary invoice · high
- **Where:** `ap_coder/intake/mailbox.py:113` (`_keep`), `ap_coder/intake/triage.py:231` (`EmailTriage.needs_person`)
- **Symptom:** an email with one PDF invoice plus a .zip of more invoices, an attached .eml/.msg file, a .docx, an unreadable part or a small black-and-white TIFF scan read the one PDF into the queue and moved the email to `emails/`; the other invoices were never seen again.
- **Cause:** anything `_keep` could not keep went to `skipped` only, which triage never looked at; an email attached as a file (not as message/rfc822) was "not a PDF or image"; a Group 4 TIFF page (~12 KB) was under `MIN_IMAGE_BYTES` and dropped as a logo.
- **Reproduce:** sweep3/intake/skip_repro.py: an email with INV-1.pdf, more-invoices.zip and forwarded.eml (application/octet-stream) holding INV-3.pdf, then `mailbox.unpack()`.
- **Reproduced:** yes (`saved: ['mail - INV-1.pdf']`, `skipped: ['more-invoices.zip (not a PDF or image)', 'forwarded.eml (not a PDF or image)']`, `needs_person: False`, `email moved to: ['emails/mail.eml']`)
- **Fix:** `Mail.left_out` (everything skipped but logos and digital signatures/contact cards) goes to `classify_email(..., left_out)`; `EmailTriage.needs_person` is true when any was left out, with the reason "left out, not read: ...". Attached .eml/.msg files (in an .eml or a .msg) are opened like forwarded emails, within `MAX_FORWARD_DEPTH` and with their sum bounded (`_Opened`: bytes and boundary lines). A TIFF is never dropped as a logo. Two existing tests whose invoice email also carries terms.docx now expect it in `emails/needs a person/`.
- **Test:** `tests/test_sweep3_intake.py::test_an_email_with_an_attachment_left_out_waits_for_a_person_with_the_reason` (also `test_a_signed_email_or_a_small_logo_does_not_need_a_person`, `test_a_small_black_and_white_tiff_scan_is_read_not_dropped_as_a_logo`, `test_an_outlook_email_attached_to_an_eml_is_opened`, `test_an_eml_file_attached_to_an_outlook_email_is_opened`, `test_emails_attached_as_files_inside_each_other_are_opened_only_so_deep`)
- **Lesson:** Don't let a "skipped" list be a log nobody acts on: what may be an invoice and wasn't read must reach a person.

#### B-141 · A forwarded email sent in base64 was dropped · medium
- **Where:** `ap_coder/intake/mailbox.py:174` (`read_eml.parts`)
- **Symptom:** an email forwarded as an attachment (message/rfc822) with Content-Transfer-Encoding: base64 gave nothing: no invoice, nothing in "left out".
- **Cause:** the parser reads such a part as an email with no headers whose text is the base64; `walk()` then skipped the text because its part was "message".
- **Reproduce:** sweep3/intake/rfc822_b64.py: the same forwarded email as 7bit, base64 and quoted-printable.
- **Reproduced:** yes (`base64 found: [] skipped: []`; 7bit and quoted-printable found `INV-3.pdf`)
- **Fix:** `_in_base64()` / `_base64_email()`: such a part is not descended into; its decoded bytes are opened like an attached .eml (`_open_attached`, same depth and size limits).
- **Test:** `tests/test_sweep3_intake.py::test_a_forwarded_email_sent_in_base64_is_opened`
- **Lesson:** Don't assume the parser decoded a nested part: test every transfer encoding of it.

#### B-142 · A scanned invoice in an Outlook .msg was dropped as a logo · medium
- **Where:** `ap_coder/intake/mailbox.py:153` (`read_msg_mail`), `ap_coder/intake/msgfile.py:231` (`_message`)
- **Symptom:** a 105 KB scanned invoice JPEG in a .msg (an email Outlook received from Gmail) was left out as "a small or inline picture, e.g. a logo"; the same email as .eml kept it.
- **Cause:** any attachment with a Content-ID counted as inline in a .msg, and Outlook keeps the Content-ID Gmail gives every attachment; inline pictures under `MAX_LOGO_BYTES` are dropped.
- **Reproduce:** sweep3/intake/cid_repro.py: the same JPEG with Content-ID `f_lx3abc0` in a .msg and an .eml.
- **Reproduced:** yes (`scan.jpg .msg found: [] skipped: ['scan.jpg (a small or inline picture, e.g. a logo)']`, `.eml found: ['scan.jpg']`)
- **Fix:** `MsgAttachment.inline`: a Content-ID and either PidTagAttachmentHidden (read from the attachment's properties stream) or `cid:<id>` in the message's HTML body; `read_msg_mail` uses it. The bench's msgwriter can write the hidden flag.
- **Test:** `tests/test_sweep3_intake.py::test_a_scanned_invoice_with_a_content_id_in_an_outlook_email_is_kept` (also `test_a_picture_the_outlook_email_shows_in_its_text_is_a_logo`, `test_a_picture_outlook_marks_hidden_is_a_logo`)
- **Lesson:** Don't take a header every message carries as the signal for a rare case: check what the two formats' rules mean, not their field names.

#### B-143 · The doctor crashed on a damaged database file · medium
- **Where:** `ap_coder/ops/cli.py:227` (`cmd_doctor`) -> `ap_coder/ops/cli.py:80` (`reference_source`)
- **Symptom:** `python -m ap_coder doctor` with a damaged `ap_coder.db` printed a `sqlite3.DatabaseError: file is not a database` traceback and no report.
- **Cause:** `run_checks` caught the error while loading the reference data, but `cmd_doctor` then called `reference_source()` again, outside any handling, to print the source line.
- **Reproduce:** write junk to the data folder's ap_coder.db, run `python -m ap_coder doctor` (sweep3/intake/doc.out).
- **Reproduced:** yes (the traceback ending `sqlite3.DatabaseError: file is not a database`)
- **Fix:** `cmd_doctor` catches it and adds a FAIL "database" check naming the file and the backups folder next to it. Also: a WARN "package pillow-heif" check when it is missing (iPhone HEIC photos can't be read).
- **Test:** `tests/test_sweep3_intake.py::test_the_doctor_reports_a_damaged_database_instead_of_crashing` (also `test_the_doctor_warns_when_iphone_photos_cannot_be_read`)
- **Lesson:** Don't call the step a check guards a second time outside the guard.

#### B-144 · An emailed statement left the Statements page after 500 newer emails · low
- **Where:** `ap_coder/intake/inbox.py:60` (`statements`)
- **Symptom:** a statement received by email, not marked done, disappeared from *Statements received by email* once 500 more emails were sorted.
- **Cause:** `triage_log(folder)` returned only the 500 newest entries before the statement filter was applied (what B-117 fixed for `needs_person`).
- **Reproduce:** sweep3/intake/stmt_repro.py: a statement entry followed by 499, then 500, invoice entries.
- **Reproduced:** yes (`499 newer emails -> ['stmt01']`, `500 newer emails -> []`)
- **Fix:** `statements()` passes `keep=` (has a statement document, not marked done), so the whole log is searched and the limit counts only open statements.
- **Test:** `tests/test_sweep3_intake.py::test_an_emailed_statement_stays_listed_however_many_emails_were_sorted_after_it`
- **Lesson:** Don't fix a "most recent N before the filter" bug in one list and leave its sibling list reading the same log.

#### B-145 · "Read as invoice" on a spreadsheet made it disappear · low
- **Where:** `ap_coder/intake/inbox.py:89` (`to_queue`), `ap_coder/web/templates/process.html:51`
- **Symptom:** "Read as invoice" on a .csv/.xlsx set aside as Not for AP moved it into the invoices folder, where it is never read; the button disappeared and the file was in no list.
- **Cause:** `to_queue` moved any attachment; the folder pick-up reads only PDFs and pictures.
- **Reproduce:** sweep3/intake/sheet_queue.py: an email with october.csv, `inbox.to_queue()`, then `invoice_files(folder)`.
- **Reproduced:** yes (`Read as invoice -> m - october.csv`, `invoice_files(folder): []`, `after: docs shown with a button: []`)
- **Fix:** `to_queue` refuses a spreadsheet (ValueError, in plain words: save it as a PDF and add that on Import invoices), the page answers with that message, and `needs_person` marks each document `sheet` so the page offers no button for one.
- **Test:** `tests/test_sweep3_intake.py::test_a_spreadsheet_set_aside_cannot_be_sent_to_the_review_queue` (also `test_the_process_page_offers_no_read_as_invoice_for_a_spreadsheet`)
- **Lesson:** Don't offer an action that hands a file to a step that never reads its kind.

#### B-146 · Removing the demo left the sample setup it loaded · low
- **Where:** `ap_coder/ops/demo.py:132` (`load_demo`), `ap_coder/ops/demo.py:184` (`remove_demo`)
- **Symptom:** after loading the demo into a fresh database and removing it, the sample GL accounts, cost centers, tax treatments and policy stayed: AP Coder looked set up with made-up accounts.
- **Cause:** `load_demo` called `load_sample_setup` without recording it (unlike the POs and vendors, `DEMO_POS_SETTING`, `DEMO_VENDORS_SETTING`), so `remove_demo` could not know.
- **Reproduce:** sweep3/intake/demo_left.py: table counts before, after `load_demo`, after `remove_demo`.
- **Reproduced:** yes (`gl_accounts: before 0 loaded 26 after 26`, `cost_centers: before 0 loaded 10 after 10`, `has_reference after remove: True`)
- **Fix:** `DEMO_SETUP_SETTING` keeps the reference data before and after the sample setup was loaded; `remove_demo` puts each part (GL accounts, cost centers, tax setup, policy) back as it was, unless it changed since the demo loaded it.
- **Test:** `tests/test_sweep3_intake.py::test_removing_the_demo_removes_the_sample_setup_it_loaded` (also `test_removing_the_demo_keeps_the_setup_once_the_user_changed_it`)
- **Lesson:** Don't record only some of what a removable mode adds: everything it loads needs its marker.

#### B-147 · `process` sorted emails without logging them, then failed · low
- **Where:** `ap_coder/ops/cli.py:262` (`_inputs`, `cmd_process`)
- **Symptom:** `python -m ap_coder process <folder>` on a folder holding a bank-detail change email sorted it, wrote no Activity event (watch does: `email_bank_change`), and exited 2 "No supported invoice files found".
- **Cause:** `_inputs` unpacked emails without `inbox.log_sorted`, and `cmd_process` treated "no invoice files" as a usage error even when emails had been sorted.
- **Reproduce:** sweep3/intake/fraud_cli.py: the same email through `process` and `watch --once`.
- **Reproduced:** yes (`process: exit 2; 'email_bank_change' events in the Activity log: 0`; `watch: exit 0; ... 1`)
- **Fix:** `_inputs(paths, store)` logs each sorted email in the store (as watch does) and returns how many were sorted; `process` (and `extract`) say "No invoices to read: N email(s) sorted." and exit 0 (1 if an email could not be read).
- **Test:** `tests/test_sweep3_intake.py::test_process_logs_the_emails_it_sorted_and_does_not_fail_when_there_was_nothing_else`
- **Lesson:** Don't let two commands that do the same step diverge on its side effects: share the step.

#### B-148 · A 200-megapixel phone photo was called damaged · low
- **Where:** `ap_coder/ai/pipeline.py:159` (`plain_error`), via `ap_coder/intake/pictures.py:158` (`image_to_pdf`)
- **Symptom:** a photo from a phone's 200 MP mode failed with "can't be opened as a picture: the file is damaged ... Ask the vendor for a new copy".
- **Cause:** Pillow refuses pictures over ~179 million pixels (DecompressionBombError), which `DAMAGED_PICTURE` worded as a damaged file.
- **Reproduce:** sweep3/intake/big_img.py: a 16320 x 12240 JPEG through `to_pdf()` and `plain_error()`.
- **Reproduced:** yes (`RAISED DecompressionBombError`, `AP sees: IMG_20261019.jpg can't be opened as a picture: the file is damaged or not really a JPG. ...`)
- **Fix:** `plain_error` says the picture is too large (with its megapixels, as the page reader does) and to take it at the phone's normal resolution or scan the page.
- **Test:** `tests/test_sweep3_intake.py::test_a_picture_too_large_to_open_is_said_to_be_too_large_not_damaged`
- **Lesson:** Don't file a safety limit under "damaged": say which limit, and what to do.

#### B-149 · `evaluate` without a labels file printed a traceback · low
- **Where:** `ap_coder/ops/cli.py:590` (`cmd_evaluate`)
- **Symptom:** `python -m ap_coder evaluate` before any labels workbook exists printed a FileNotFoundError traceback.
- **Cause:** `cmd_evaluate` passed the default `labels.xlsx` path straight to `evaluate()`.
- **Reproduce:** `python -m ap_coder evaluate` in a data folder without labels.xlsx.
- **Reproduced:** yes (`FileNotFoundError: [Errno 2] No such file or directory: '.../labels.xlsx'`, in the new test before the fix)
- **Fix:** a one-line message naming the file and `python -m ap_coder labels`, exit 2.
- **Test:** `tests/test_sweep3_intake.py::test_evaluate_without_a_labels_file_says_so_in_one_line`
- **Lesson:** Don't hand a default path to a loader without checking the file is there: a first run has none.

#### B-206 · Exchange's ATT00001.htm, winmail.dat or a calendar invite made every invoice email wait for a person · medium
- **Where:** `ap_coder/intake/mailbox.py:98` (`Mail.left_out`), `ap_coder/intake/mailbox.py:183` (`_not_read`)
- **Symptom:** since B-140, an ordinary invoice email with Exchange's ATT00001.htm / ATT00001.txt footer, Outlook's winmail.dat or a calendar invite went to `emails/needs a person/` with "left out, not read: ATT00001.txt ... an invoice in it must be added by hand".
- **Cause:** `Mail.left_out` counted every skipped part but logos and signatures, also those that never hold an invoice.
- **Reproduce:** review33/s/t1.py: an email with INV1234.pdf plus one of those parts, then `mailbox.unpack()`.
- **Reproduced:** yes (`ATT00001.txt needs_person=True ... reasons=['left out, not read: ATT00001.txt (not a PDF or image). ...']`, the same for .htm, winmail.dat and invite.ics)
- **Fix:** `_not_read` marks a .txt/.htm/.html/.ics part (or an unnamed text/plain, text/html or calendar one) as the email's own text or a calendar invite unless its name mentions an invoice (`triage.mentions_invoice`), and winmail.dat (application/ms-tnef) as Outlook's formatting. `left_out` leaves out the first always and winmail.dat when the email gave a PDF, a picture or a spreadsheet (alone it may hold the attachments). A zip, Word, RTF or XML file, an HTML file named as an invoice, a damaged part and the depth/size limits still make the email wait; spreadsheets keep their own "Not for AP" path.
- **Test:** `tests/test_sweep3_intake.py::test_exchange_footers_outlook_formatting_and_invites_do_not_make_an_invoice_email_wait` (also `test_parts_that_may_hold_an_invoice_still_make_the_email_wait`, `test_an_outlook_winmail_dat_alone_is_left_out_since_it_may_hold_the_attachments`)
- **Lesson:** Don't make "may be an invoice" mean "anything that isn't a PDF": list what can really hold one, or every email waits.

#### B-207 · A small fax scan named .tiff (not .tif) was still dropped as a logo · low
- **Where:** `ap_coder/intake/mailbox.py:174` (`_keep`)
- **Symptom:** a black-and-white fax page under 15 KB attached as fax.tiff or scan.TIFF was left out as "a small or inline picture, e.g. a logo"; the same file named .tif was read.
- **Cause:** B-140's exemption checked `suffix not in (".pdf", ".tif")`, but `_kind` keeps a ".tiff" name's extension as ".tiff".
- **Reproduce:** review33/s/t10.py: `_keep` with fax.tif, fax.tiff and scan.TIFF (5 KB, image/tiff).
- **Reproduced:** yes (`fax.tif found`, `fax.tiff ['fax.tiff (a small or inline picture, e.g. a logo)']`, the same for scan.TIFF)
- **Fix:** the exemption lists ".tiff" too.
- **Test:** `tests/test_sweep3_intake.py::test_a_small_tiff_scan_named_tiff_is_read_not_dropped_as_a_logo`
- **Lesson:** Don't compare against one spelling of an extension when the function before it keeps both.

### Setup, launcher and update

#### B-167 · An update leaves the older version's `watch` / `read-pages` running, and it fails every new invoice · medium
- **Where:** `scripts/running.py:404` (`take_over`), `ap_coder/ops/cli.py:441` (`cmd_watch`), `scripts/upgrade.py:816` (`clean_up`)
- **Symptom:** after an update, invoices dropped in the invoices folder show as failed ("No module named 'pymupdf'") and the new version never reads them again.
- **Cause:** `take_over` found and stopped only servers (by their server file and `/healthz`); a `python -m ap_coder watch` (or `read-pages`) of the older version left no trace, kept running on the same database, and the clean-up then deleted the `.venv` it ran from. A failed attempt counts as seen, so nothing retried it.
- **Reproduce:** start `python -m ap_coder watch` from version A, update to B in another folder (double-click), drop an invoice in the invoices folder.
- **Reproduced:** yes (`<old>/ap_coder/capture/layout.py ... import pymupdf ... ModuleNotFoundError: No module named 'pymupdf'` / `chinook_AB_GST_CCO-26-10418.pdf: FAILED ModuleNotFoundError`); the new test failed with the old watch still running after `take_over`.
- **Fix:** `watch` and `read-pages` leave `<data>/.ap_coder_worker-<pid>.json` (pid, command, folder, version) while they run (`cli.running_note`); `take_over` reads them (`running.workers`, a note whose process is gone is removed) and stops an older one, or one from another folder, before setup (`stop_worker`: as Ctrl+C, then ended; only a process whose command line is AP Coder's `watch` / `read-pages`). GETTING_STARTED says to point a scheduled task at the new folder after an update unzipped into a new one (no command follows the current version by itself).
- **Test:** `tests/test_running.py::test_an_older_versions_watch_is_stopped_before_the_update`, `tests/test_config_and_cli.py::test_watch_and_read_pages_say_in_the_data_folder_that_they_run`
- **Lesson:** Don't make way for a new version by stopping only the process you can see on a port: every long-running command on the same data must say it runs.

#### B-168 · A second Windows account's double-click opens the first person's dashboard, under their name · medium
- **Where:** `scripts/running.py:164` (`judge`), `scripts/launch.py:1624` (`wait_for_other`), `ap_coder/web/app.py` (`healthz`), `ap_coder/web/server.py` (`write_server_file`)
- **Symptom:** with two Windows accounts sharing one code and data folder (GETTING_STARTED, "Two people approving"), the second person's double-click opens the AP Coder the first person left running, and approves under the first person's name.
- **Cause:** a running server was judged by folder and version only: the same folder and version was "same", so it was opened (or, older, stopped under the other person).
- **Reproduce:** A starts AP Coder; B (another account, same folders) double-clicks APProcessor.bat.
- **Reproduced:** yes (`take_over` returned `("open", port)` for a server whose `/healthz` names another user; `running_dashboard` returned its port).
- **Fix:** the server file and `/healthz` (to this computer only) carry `user`, the computer account running it (`app.os_user`); `judge` says "other" for another account's server (names compared ignoring case; a server that doesn't say is judged as before); `take_over` returns `("other", port)` and the launcher says whose AP Coder is open and to ask them to close it (`someone_elses`), in `run`, `wait_for_other` and `stop_older`. It is never opened nor stopped.
- **Test:** `tests/test_running.py::test_another_accounts_ap_coder_is_never_opened_nor_stopped`, `tests/test_launch.py::test_another_accounts_running_ap_coder_is_named_never_opened`, `tests/test_web_app.py::test_healthz_and_the_server_file_say_which_account_runs_it`
- **Lesson:** Don't identify a running program by its folder and version alone when two accounts can share that folder: record whose it is.

#### B-169 · On a new data folder the readiness summary says the GL account checks all passed · low
- **Where:** `scripts/launch.py:1400` (`setup_check_lines`)
- **Symptom:** a fresh install with no GL accounts imported prints "Setup checks: all 9 passed (GL accounts, tax mapping, coding rules, settings)".
- **Cause:** with nothing imported, the reference data falls back to the bundled sample (`ap_coder/ops/cli.py:reference_dir`), and the doctor's checks pass on it; the summary never said where the GL accounts came from.
- **Reproduce:** a new data folder, double-click: read the summary.
- **Reproduced:** yes (`[('ok', 'Setup checks: all 9 passed (GL accounts, tax mapping, coding rules, settings)')]`)
- **Fix:** when `reference_source` is the bundled sample, a warning line "GL accounts: none imported yet, so the sample ones are used: import yours on GL accounts & tax" comes first, and the summary line says "the sample GL accounts" (CI's `Setup checks: all \d+ passed` still matches).
- **Test:** `tests/test_launch.py::test_the_start_says_no_gl_accounts_were_imported_yet`
- **Lesson:** Don't report a check as passed without saying it ran on stand-in data.

#### B-170 · An update from GitHub's ZIP always keeps the older folder ("files AP Coder didn't put there (CLAUDE.md)") · low
- **Where:** `scripts/upgrade.py:59` (`BUNDLE_TOP`)
- **Symptom:** after updating over a folder made from GitHub's ZIP, the older folder stays: "the folder itself stays because it has files AP Coder didn't put there (CLAUDE.md)".
- **Cause:** a folder with no manifest file list is judged by `BUNDLE_TOP`, which lacked `CLAUDE.md` and `.claude`, both top-level names git tracks (and GitHub's ZIP holds).
- **Reproduce:** GitHub's ZIP of an older version, set up; update from a bundle beside it.
- **Reproduced:** yes (`Kept the older AP Coder folder .../app: the folder itself stays because it has files AP Coder didn't put there (CLAUDE.md)`)
- **Fix:** `CLAUDE.md` and `.claude` in `BUNDLE_TOP` (`.claude` also in `RETIRED_KEPT`: the bundle leaves it out, so it is never pruned); a test checks every top-level name git tracks is in `BUNDLE_TOP` (skipped outside a git checkout).
- **Test:** `tests/test_update.py::test_every_top_level_name_git_tracks_is_one_ap_coder_put_there`, `tests/test_update.py::test_an_older_folder_from_githubs_zip_is_removed_whole`
- **Lesson:** Don't keep a hand list of what a folder holds without a test that compares it with the source of truth.

#### B-171 · `--installer --full-tests` from an offline bundle fails the CI workflow tests · low
- **Where:** `tests/test_ci_workflow.py:44` (`needs_ci`), `scripts/build_offline_bundle.py:70` (`EXCLUDE_DIRS`)
- **Symptom:** the installer's full test run in an unzipped offline bundle reports failures.
- **Cause:** the bundle leaves `.github` out, but two tests read `.github/workflows/ci.yml` unconditionally (FileNotFoundError).
- **Reproduce:** copy the bundle's files (`build_offline_bundle.source_files()`) to a folder; `python -m pytest tests/test_ci_workflow.py` there.
- **Reproduced:** yes (`FileNotFoundError: ... .github/workflows/ci.yml` / `2 failed, 1 passed, 1 skipped`)
- **Fix:** the tests that read ci.yml skip when it isn't there (`needs_ci`); the parametrized one already had nothing to run. The other suites a bundle copy runs (check_map, map_review_gate, launcher scripts, update, doc) were checked to pass there.
- **Test:** `tests/test_ci_workflow.py::test_the_workflow_tests_skip_where_there_is_no_workflow`
- **Lesson:** Don't let a shipped test read a file the shipping step leaves out.

#### B-172 · HOW_IT_RUNS says the setup downloads only with AP_ALLOW_INTERNET=1 · low
- **Where:** `docs/HOW_IT_RUNS.md:380` (the network table), `docs/HOW_IT_RUNS.md:121` (`:installpython`)
- **Symptom:** IT reading the map believes GitHub's ZIP never downloads; it does (PyPI, the OCR models, winget's Python).
- **Cause:** the map predates `scripts/launch.py:plain_download`: from the code alone (no manifest, wheelhouse or models) the setup downloads unless `AP_ALLOW_INTERNET=0`; APProcessor.bat does the same for winget.
- **Reproduce:** read rows 1 to 3 of section 10 and step 6 of "Windows", then `plain_download` / `setup_online` and APProcessor.bat lines 30-34.
- **Reproduced:** yes (`PyPI only with AP_ALLOW_INTERNET=1` against `return [] if setup_online() else None`)
- **Fix:** the intro, step 6, section 5 item 2 and rows 1 to 3 say: from the code alone, or with `AP_ALLOW_INTERNET=1`; never with `AP_ALLOW_INTERNET=0`.
- **Test:** `tests/test_how_it_runs_doc.py::test_the_setups_downloads_from_the_code_alone_are_said`
- **Lesson:** Don't leave a network table behind when a new path to the internet is added.

#### B-173 · `--fresh-start` moves the data folder's models/ (and the guide says "your settings stay") · low
- **Where:** `scripts/install.py:278` (`fresh_start`), `docs/GETTING_STARTED.md` (Step 4)
- **Symptom:** after `--installer --fresh-start`, the table finder IT put in `<data>/models/` (or the setup downloaded there) is gone into `backup-<date>/`, so line items are read without it; and Settings → Review, Automation and Data & backups are back to their defaults though Step 4 promises "your settings stay".
- **Cause:** `fresh_start` kept only `.env`, `.cache` and `backups`; the dashboard's settings live in the database, which moves.
- **Reproduce:** put `yolov8n_layout_general6.onnx` in `<data>/models/`, run `fresh_start`.
- **Reproduced:** yes (`FileNotFoundError: .../models/yolov8n_layout_general6.onnx`)
- **Fix:** `models` is kept; GETTING_STARTED (the option table and Step 4) says the `.env` settings and models stay while what was set in the dashboard's Settings moves with the database (set it again in Step 5). Carrying database settings over was left as a choice for later (it is the sample trial's database).
- **Test:** `tests/test_install.py::test_fresh_start_keeps_the_models_ap_coder_reads`
- **Lesson:** Don't sweep a folder by "everything but a short keep list" without checking what else is read from it.

### Day-one walk-through

#### B-197 · The review queue called "Ready" an invoice its review screen won't approve · high
- **Where:** `ap_coder/web/pages/review.py:116` (`attention`, used by `review_page` and invoice.py's `navigation`); `ap_coder/coding/review.py:100` (`codes_to_pick`)
- **Symptom:** with the sample setup every uploaded sample invoice whose checks found no error showed a green "Ready" in the queue (and under "Ready" in the filter), yet its review screen said "Pick a cost center for lines 1, 2, 3 … to approve" with Approve disabled.
- **Cause:** the queue's status was the stored `requires_review` flag (an error, or confidence under the threshold); a line without a cost center is only a warning (`CC_UNASSIGNED`), so it never set the flag, while the review screen's Approve bar refuses any line without a GL account or (with cost centers imported) a cost center. Two rules for one question.
- **Reproduce:** demo data; blank the cost centers of a clean invoice's lines; open /review: the row says Ready; /review/<id>/check answers `allow: false`.
- **Reproduced:** yes (`'<li><a class="rq-row ready" href="/review/6" …Agence Créative Mont-Royal inc.'` while its check answered `allow: False`)
- **Fix:** one rule, `codes_to_pick()` in coding/review.py, asked by the review screen's Approve bar (invoice_view.analyse) and by the queue's new `attention()`; the queue's Ready / Needs attention, its filters, the Waiting tile ("N need attention") and the review screen's Previous/Next within a filter all use it. A row that needs attention says why under its pill ("Pick a cost center for lines 1, 2, 3", "1 error to fix", "Confidence under 85%").
- **Test:** `tests/test_sweep3_dayone.py::test_the_queue_never_calls_ready_an_invoice_whose_review_screen_blocks_approval`, `tests/test_sweep3_dayone.py::test_every_invoice_the_queue_calls_ready_can_be_approved_on_its_screen`
- **Lesson:** Don't answer "can this be approved?" with a stored flag in one place and the approval rule in another: ask one shared function.

#### B-198 · "Not in the vendor list" warned on every new vendor when no vendor master was imported · medium
- **Where:** `ap_coder/web/static/js/invoice.js:402` (`showVendorMatch`)
- **Symptom:** with no vendor master imported (the sample setup), once one invoice was approved every other new vendor showed an amber "Not in the vendor list: pick one, or add it as a new vendor." under the vendor field, asking the pilot tester to fix something that isn't wrong.
- **Cause:** `store.vendor_choices()` falls back to the vendors approved before when there is no vendor master, so the list is not empty, and the script warned whenever the typed name was not in that list, whether or not a vendor master is enforced (`vendors.enforced`).
- **Reproduce:** no vendor master; approve one invoice; open another and type a new vendor's name.
- **Reproduced:** yes (`'gpp_maybe\nNot in the vendor list: pick one, or add it as a new vendor.'`)
- **Fix:** the warning (and "Pick the vendor from the list") only with a vendor master; without one a new vendor gets no note, and a vendor approved before reads "Approved before" (not "In the vendor list").
- **Test:** `tests/test_web_invoice.py::test_in_a_browser_not_in_the_vendor_list_is_said_only_once_a_vendor_master_is_imported`
- **Lesson:** Don't warn against a fallback list as if it were the master list it stands in for.

#### B-199 · Settings → Reading showed the Azure check's form in the offline build · medium
- **Where:** `ap_coder/web/pages/settings_reading.py:339` (`reading_context`, `show_azure_check`); `ap_coder/web/templates/settings_reading.html`
- **Symptom:** on a pilot laptop (no `AP_ALLOW_INTERNET`), Settings → Reading ended with an "Azure check · optional" card asking for an Azure endpoint and key, while docs/PILOT.md tells the pilot tester the offline build never uses cloud services.
- **Cause:** the card was included on the Reading tab unconditionally (only the developer's Azure connection form looked at `internet_allowed()`).
- **Reproduce:** GET /settings/reading without `AP_ALLOW_INTERNET`: `id="azure-check"` is in the page.
- **Reproduced:** yes (`'re-check" id="azure-check" aria-labelledby="azure-check-title">'` in the offline page)
- **Fix:** the card shows only where the internet is allowed, or while the check is still switched on from before (so it can be switched off); /settings/azure-check itself is unchanged.
- **Test:** `tests/test_sweep3_dayone.py::test_the_reading_tab_shows_the_azure_check_only_where_the_internet_is_allowed` (and `tests/test_azure_check.py::test_settings_reading_shows_the_azure_card_and_its_own_page_is_not_taken_for_a_tab`, updated: it asserted the card in the offline build)
- **Lesson:** Don't offer a cloud setting on a screen of the build that promises it uses none.

#### B-200 · Optional parts that are simply not set up showed as red "Not working" / "Needs a look" · low
- **Where:** `ap_coder/ai/reading.py:124` (`_table_finder`, `_gl_model`); `ap_coder/web/pages/settings_reading.py` (`READER_PILL`)
- **Symptom:** a fresh pilot laptop showed "GL coding model · Not working" in red and, on a copy set up from the code alone, "Table finder · Needs a look", for parts that are optional and not set up; the pilot tester reads that as something broken.
- **Cause:** the readers' status had only ok / warn / off / testing, so "optional and not there" fell into off or warn.
- **Reproduce:** `reading_status(Settings(), None)` with no LM Studio: the GL coding model's state is `off`; `_table_finder()` without the model file on a non-bundle copy: `warn`.
- **Reproduced:** yes (`AssertionError: assert ('off' == 'off' and 'optional' == 'off')` once the test expected the new state)
- **Fix:** a new state `optional`, drawn as a grey "Not set up (optional)": the GL coding model when not running or not asked, and the table finder's missing model on a copy that didn't come from the offline bundle (`offline.from_bundle()`: no models/ folder next to the app). From the bundle, a missing model is still "Needs a look".
- **Test:** `tests/test_sweep3_dayone.py::test_optional_readers_not_set_up_are_grey_not_red` (and `tests/test_table_reading.py::test_the_reading_settings_show_the_table_finder`, `tests/test_uniform_reading.py::test_reading_status_with_nothing_running`, updated where the old state was the bug)
- **Lesson:** Don't show "optional and not set up" in the colour of "broken".

#### B-201 · Four pages gave four answers to "how many were approved as coded" · medium
- **Where:** `ap_coder/erp/insights.py:59` (`corrected`, `approved_as_coded`); `ap_coder/web/pages/learning.py` (`coding_context`), `ap_coder/web/pages/activity.py` (`activity_page`), `ap_coder/web/pages/review.py` (`kpis`)
- **Symptom:** after the same four approvals the pilot tester saw Insights "Approved as coded 75%, 3 of 4", Learning "Approved without edits 1/3", Activity "4 with reviewer changes", and Coding accuracy 88% on the queue but 87.5% on Learning.
- **Cause:** three definitions: Insights left out blanks filled in, Learning counted invoices with an empty `edits` list among the approved only (not those waiting for a second approval), Activity counted any change in the audit trail; and the queue rounded the line accuracy to a whole percent where Learning kept one decimal.
- **Reproduce:** demo data; approve four invoices, each with a cost center filled in where the coder left none, one also with a GL account changed; open Insights, Learning, Activity and the queue.
- **Reproduced:** yes (`ImportError: cannot import name 'approved_as_coded'`, and the walk's 75% / 1/3 / "4 with reviewer changes" / 88% vs 87.5%)
- **Fix:** one definition (D-39), `insights.corrected()`: a value changed or a line added or removed is a change, a blank filled in is not; `approved_as_coded()` gives the pair Insights and Learning show, both under the label "Approved as coded" with "N of M needed no change"; Activity says "N approvals: X approved as coded, Y corrected" with the same rule; Coding accuracy is written to one decimal on the queue as on Learning.
- **Test:** `tests/test_sweep3_dayone.py::test_insights_learning_activity_and_the_queue_count_approved_as_coded_alike`, `tests/test_sweep3_dayone.py::test_a_change_is_a_value_the_reviewer_changed_or_a_line_added_not_a_blank_filled_in`
- **Lesson:** Don't compute one business figure in three pages with three rules: define it once and import it.

#### B-202 · With ten files chosen to import, the sixth name ran under the "Process" button · low
- **Where:** `ap_coder/web/static/app.css:594` (`.picked`)
- **Symptom:** Import invoices with the ten sample PDFs chosen: the list was a 180 px scroll box with no gap or edge, so the sixth name was cut in half right against the "Process 10 uploaded invoices" button and looked overlapped (shots/06_process_selected.png); the other four were hidden in the box.
- **Cause:** `.picked` had `max-height: 180px` (about five names) and no margin below it.
- **Reproduce:** /process, choose ten PDFs: the list's box ends at the button's top (630.5 px both) with the sixth row from 609.8 to 641.6 px.
- **Reproduced:** yes (`AssertionError: (5, {'y': 609.75, 'height': 31.84}, {'y': 630.53})`)
- **Fix:** the list shows about a dozen names (`max-height: min(55vh, 440px)`) before it scrolls, with a closing line and a gap above the button.
- **Test:** `tests/test_web_invoice.py::test_in_a_browser_ten_files_chosen_to_import_are_listed_clear_of_the_process_button`
- **Lesson:** Don't give a scroll box no edge and no gap: the row it cuts looks like it slides under the next control.

#### B-203 · The getting-started guide named a button the app doesn't have · low
- **Where:** `docs/GETTING_STARTED.md:158`
- **Symptom:** "Reading the samples for real", step 1 said **GL accounts & tax** → *Load sample setup*; the button there (and on the Import invoices page and the welcome page) says "Use the sample GL accounts".
- **Cause:** the button was renamed and the guide kept the old words.
- **Reproduce:** search the guide for "Load sample setup" and the templates for it.
- **Reproduced:** yes (`'Load sample setup' is contained here: tax** → *Load sample setup* (skip this…`)
- **Fix:** the guide says *Use the sample GL accounts*, as the button does (the button's words read better: they say what is loaded).
- **Test:** `tests/test_sweep3_dayone.py::test_the_getting_started_guide_names_the_sample_button_as_the_app_does`
- **Lesson:** Don't rename a button without searching the guides for its old name.

#### B-204 · A batch just created offered "Download again" for a file never downloaded · low
- **Where:** `ap_coder/web/templates/exports.html:196` (the past batches' download button)
- **Symptom:** after Export, the message said "Download it below", and below the "Batch 2 is ready" card the batch list, with batch 2 picked, offered "Download again".
- **Cause:** the list's button always read "Download again", also for the batch the page had just made (`?made=` picks it in the list too).
- **Reproduce:** approve an invoice, Export it as CSV, look at the page the export leads to.
- **Reproduced:** yes (`assert ('Download it below' in page and 'Download again' not in page)` failed)
- **Fix:** for the batch just made the list's button reads "Download"; a batch picked later in the list still reads "Download again".
- **Test:** `tests/test_sweep3_dayone.py::test_a_batch_just_made_offers_download_not_download_again`
- **Lesson:** Don't say "again" about something the page has no record of happening once.

#### B-213 · "Approved today 2 … 1 approved in all" did not say that one waited for its second approval · low
- **Where:** `ap_coder/web/pages/review.py` (`kpis`)
- **Symptom:** with an approval limit set, the queue's Approved today tile counted the reviewer's first approval of an invoice still waiting for its second, next to "1 approved in all": the two numbers seemed to disagree.
- **Cause:** the tile counts this reviewer's approvals today (first approvals waiting included, by design), but its line under the figure counted only fully approved invoices and never mentioned the waiting ones.
- **Reproduce:** limit 5,000; approve one invoice under it and one over it; open the queue.
- **Reproduced:** yes ("Approved today 2 … 1 approved in all").
- **Fix:** the line now reads "of N today · 1 waiting for a second approval · 1 fully approved in all".
- **Test:** `tests/test_sweep3_dayone.py::test_the_approved_today_tile_says_how_many_wait_for_a_second_approval`
- **Lesson:** Don't put two counts side by side that count different things without saying what each counts.

#### B-215 · The JD Edwards dry run read "Will load: 0 voucher(s), -." when every invoice was held back · low
- **Where:** `ap_coder/erp/jde.py` (`dry_run_text`)
- **Symptom:** the first line of the dry run, shown on Exports and kept in each batch's ZIP, ended in a stray "-" when nothing could load.
- **Cause:** an empty list of totals was shown as "-".
- **Reproduce:** a dry run where every invoice is held back (no supplier AN8). Found by the final walk-through.
- **Reproduced:** yes ("Will load: 0 voucher(s), -. Held back: 2.").
- **Fix:** it now reads "Will load: 0 voucher(s), nothing to load. Held back: 2.".
- **Test:** `tests/test_sweep3_dayone.py::test_a_dry_run_with_nothing_to_load_has_no_stray_dash`
- **Lesson:** Don't stand a placeholder character in for a sentence a person reads.

Improvements made with these fixes:
- Review screen, line grid: "Apply to all lines…" gives one GL account and / or cost center to every line that has none (lines already coded keep theirs; POST /review/<id>/fill-lines, like the other grid actions); when every past approval of the vendor used one cost center, it is pre-selected there as a suggestion (`invoice_view.usual_cost_center`; the coder itself is unchanged). `tests/test_sweep3_dayone.py::test_apply_to_all_lines_codes_only_the_lines_that_have_none`, `tests/test_sweep3_dayone.py::test_apply_to_all_lines_refuses_a_code_not_in_the_lists`, `tests/test_sweep3_dayone.py::test_a_vendors_one_usual_cost_center_is_offered_for_its_lines`, `tests/test_web_invoice.py::test_in_a_browser_apply_to_all_lines_fills_the_cost_centers_left_blank`
- Second approval on one laptop: Settings → Review ("Approval and payment"), the waiting invoice's screen and the Second approval tab say it takes another person on another computer login, so one Windows login can't give it; how to test it (a second Windows account on the laptop, or a colleague over the office network); and that Send back returns it to the queue. The control is unchanged. `tests/test_sweep3_dayone.py::test_the_second_approval_says_how_to_give_it_when_one_login_cannot`
- Exports, JD Edwards: an invoice held back for its supplier number (AN8 missing or not a JDE address number) links straight to Settings → JD Edwards E1 → Supplier address numbers (`/settings/jde#an8`), in its "Why not" cell and in the message above the list (`jde.an8_problem`). `tests/test_sweep3_dayone.py::test_an_export_held_back_for_a_supplier_number_links_to_the_address_numbers`
- First run: the getting-started steps (Import invoices's Setup card and the review queue's welcome page) end with "Your name", a to-do linking to Settings → Review until a name is given (saved there, `AP_REVIEWER`, or a colleague's "Who are you?"), so approvals are not recorded under the computer login. `tests/test_sweep3_dayone.py::test_the_first_steps_ask_for_your_name_until_it_is_given` (and `tests/test_dashboard_sweep_fixes.py::test_getting_started_counts_only_the_steps_that_are_needed`, its counts now 1/6 and 3/6 with the new step)
- Review queue, bulk approval: when Ready invoices are not offered ("N invoices look clean" beside more Ready rows), the banner says why: "· 1 credit note left for a person" (a credit is always applied by a person), "· 2 with a warning left for a person" (`review.left_out`, `bulk.sort_for_bulk`; the candidates are unchanged). `tests/test_sweep3_dayone.py::test_the_bulk_banner_says_why_a_ready_invoice_is_left_out`
- First run: the welcome page's ring ("4/6", with "Process your first invoices") and Import invoices' Setup card ("4 of 5 done", without it) counted different steps; both now show the same steps and count (`process.first_steps`: the setup steps, then the first invoices; the optional chat model not counted), so the Setup card reads "4 of 6 done" and "6 of 6" once invoices are in. `tests/test_sweep3_dayone.py::test_the_welcome_page_and_import_invoices_count_the_same_setup_steps`
- `python -m ap_coder serve --no-browser` no longer prints "(it opens in your browser; …)": it says only "(Ctrl+C here to stop)" (the launcher, which passes --no-browser, opens the browser and says so itself). `tests/test_sweep3_dayone.py::test_serve_with_no_browser_does_not_say_it_opens_in_your_browser`
- Review screen: a message after an action ("Cost center CC400 set on 3 lines…" after Apply to all lines) sat in the bottom-right corner over "Approve & teach"; messages now rise above the sticky approve bar (`app.js` `toast`; other pages keep the corner). `tests/test_web_invoice.py::test_in_a_browser_a_message_after_apply_to_all_lines_leaves_the_approve_bar_clear`

#### B-210 · /review decoded every waiting invoice's coding on each load: 0.23 s became 0.57 s at 5,000 waiting · medium-low
- **Where:** `ap_coder/web/pages/review.py:140` (`attention`, also used by invoice.py's `navigation`), `ap_coder/web/pages/review.py:120` (`_to_fix`)
- **Symptom:** since B-197 every /review load (all tabs: the Waiting tile counts "N need attention" on each) took 0.55 s, Ready 0.59 s and Approved 0.39 s with 5,000 invoices waiting.
- **Cause:** `attention` read and decoded `ai_output`, `final_output` and `validation` (about 4 KB of JSON each) of every waiting invoice on each load, to ask `codes_to_pick` of their lines; the decoding was 0.22 s of its 0.34 s. Reading the line codes with SQLite's JSON functions instead costs as much (0.17 s): the parse is the cost.
- **Reproduce:** review33/s/t3.py (5,000 waiting invoices, 10 lines each; /review timed) and t8.py (`attention` alone).
- **Reproduced:** yes (`/review 0.548`, `/review?show=ready 0.595`, `/review?tab=approved 0.406`, `attention over 5000 pending: 0.341 s`)
- **Fix:** `invoice_columns(..., decode=False)` gives the stored text; `_to_fix` remembers each invoice's answer (lines to code, errors) with the hash of that text and decodes only an invoice new or changed since it was last asked. Same rule, same answers ("Ready means Approve is open"). After: `/review 0.27`, Ready 0.26, Approved 0.18, `attention` 0.04 s (0.24 s the first time after a start).
- **Test:** `tests/test_sweep3_dayone.py::test_the_queue_decodes_each_invoices_coding_once_not_on_every_load` (and B-197's two tests)
- **Lesson:** Don't redo per-row work on every page load for rows that haven't changed: remember it by what it was computed from.

#### B-211 · Import invoices said "ready" of invoices the queue lists as "Needs attention" · medium
- **Where:** `ap_coder/web/pages/process.py:197` (`needs_attention`, used by `process_files`; `process_page`'s Recently processed); `ap_coder/core/ui.py:300` (`recent_row`)
- **Symptom:** the sample setup, LM Studio not running, the ten samples imported: the result list said "Pacific Office Supply Ltd.: ready" (and Harbourview, both Northwinds) and Recently processed showed "In queue", while the review queue showed each as "Needs attention · Pick a cost center for lines 1, 2, 3".
- **Cause:** the import list and the Recently processed pill still used the checks' stored flag (`requires_review`); a line without a cost center is only a warning there. B-197 gave the queue one rule (`review.attention`: Approve open as it stands) but left these two places on the old one.
- **Reproduce:** sample setup; chat model at an address nothing answers (weak matches coded, cost centers blank); upload the ten samples; compare the job's lines with `attention()`.
- **Reproduced:** yes (`AssertionError: (['Pacific Office Supply Ltd.: ready'], 'Pick a cost center for lines 1, 2, 3')`)
- **Fix:** both ask `review.attention()` for an invoice waiting in the queue (the checks' flag only for one approved on its own or not saved), so "ready" means Approve is open everywhere. Insights' "Sent for a closer look: N% of processed invoices" is the checks' flag on every invoice ever processed (approved ones too), a different figure on purpose: it now reads "Flagged by the checks when processed" (also in the pilot report), so it no longer reads as the queue's Needs attention.
- **Test:** `tests/test_web_process.py::test_the_import_list_says_ready_only_for_an_invoice_the_queue_calls_ready`
- **Lesson:** Don't fix "one rule for one question" in the main screen only: search for every other place that answers it.

#### B-212 · Activity counted approvals, not invoices approved: "4 approvals: 4 approved as coded" for 3 · low
- **Where:** `ap_coder/web/pages/activity.py:160` (`activity_page`, the "Invoices approved" tile)
- **Symptom:** three invoices approved, one of them sent back and approved again: Insights and Learning said "Approved as coded … 3 of 3", Activity "Invoices approved 3 · 4 approvals: 4 approved as coded, 0 corrected"; an approval taken back and not given again still counted as an invoice approved.
- **Cause:** B-201 gave Activity the same rule for a change (`insights.corrected`) but applied it to every `approved` event in the trail, where Insights and Learning count each invoice's standing approval once (`insights.latest_approvals`, D-39).
- **Reproduce:** demo data; approve three invoices; reopen one and approve it again, reopen another; open /activity.
- **Reproduced:** yes (`Invoices approved 5 · 6 approvals: 5 approved as coded, 1 corrected` against `approved_as_coded` = 3 of 4)
- **Fix:** the tile is `insights.approved_as_coded()`: "Invoices approved N · X approved as coded, Y corrected", the figures Insights and Learning show. The events themselves are still all listed below.
- **Test:** `tests/test_sweep3_dayone.py::test_activity_counts_each_invoices_standing_approval_once_as_insights_does` (and `test_insights_learning_activity_and_the_queue_count_approved_as_coded_alike`)
- **Lesson:** Don't share the rule but not the population: the same figure needs the same rows as well as the same test.

### Not reproduced or left as they are

- **N-77** · Store and config · B-158 can only take back an approval's part of a lesson recorded from this version on (`lesson_approvals`): an approval made before the update and withdrawn after it still leaves its counts and value in `lessons` (nothing recorded which approval added what; rebuilding the lessons from past approvals would need each approval's proposed and final coding replayed in order, a bigger change than the fix).
- **N-78** · Tax and coding rules · Discount terms written with prox or weeks ("2/10th prox net 30th", "2% 10th prox", "2% 2 weeks net 30") still give no discount (the due date is the default or the prox day): the discount date model has only days from the invoice or month end; rare, left for a design of prox discounts.
- **N-79** · Exports, ERP and reports · The ERP CSV export (`ap_coder/erp/exports.py` `build_csv`) and the Custom CSV (`export_layout.build`) are written with a UTF-8 byte-order mark ("Excel shows accents correctly"), while the JDE Z-files are deliberately without one (docs/JDE_E1.md). D-28 / B-92 decided only the quoting, not the BOM. A loader that reads plain UTF-8 (SQL*Loader, a bulk insert) sees the first header as "﻿Batch"; but some Windows import tools need the BOM to read UTF-8 at all. Which one the pilot's ERP wants is a choice (a setting, or a D-NN), not a bug fix: left as it is.
- **N-80** · Exports, ERP and reports · `dupaudit._same_bill` treats an exported invoice and an ERP-register row with the same number as the same bill when the vendor OR the amount agrees; the audit only compares rows with equal amounts, so another vendor's bill with the same number and amount is never flagged OTHER_VENDOR once ours is exported. That is the documented choice (the ERP's own name for the vendor, `test_an_exported_invoice_is_not_its_own_duplicate_in_the_erp`); quick search (B-152) now asks for the same vendor or the ERP's short name. Aligning the audit is a decision about false alarms, left.
- **N-81** · Exports, ERP and reports · Besides the exports (B-151), `spend.py`, `accruals.py` and `stamp.py` also read only a stored `gl_distribution`, so an invoice approved by an older version has no lines in the spend report, the accrual detail or the stamp's posting table. They could use `exports.distribution()`; outside this finding's scope, left.
- **N-82** · AI and readers · `page_worker.reader_alive` (an invoice's "being read" mark) has the same reused-process-id weakness as the self-test mark (B-177), but there the store's `STALE_READING_HOURS` (2 hours) already puts a stale read back in line, so it only delays; left as is.
- **N-83** · AI and readers · `Store.learning_rows` (B-175) gives an old vendor's invoice a list of its own, so the suggestion index (one slot) is rebuilt when invoices of such vendors alternate in a batch (about 0.3 s at 50,000 lessons); a per-vendor index would avoid it, a bigger change than this fix.
- **N-84** · Document capture · The page reader's transcription is read by the same rule reader as the page (`capture/__init__.py` `_page_reader`, `transcript.py`), so a rule-parsing mistake (B-183, B-184, B-185 here) appears in both readings and fusion counts it as two independent readers agreeing (verified). Making the transcript an independent reader needs its own parsing or a fusion rule that discounts agreement on the same rule path; a design change, not a bug fix.
- **N-85** · Document capture · A day/month order taken only from a real US supplier address (`read_fields`, B-183) still settles 03/04/2026 without the ambiguous mark, as docs/CAPTURE_DESIGN.md describes. Marking it ambiguous (or likely at best) would send every US invoice dated on day 1 to 12 to a person; a policy choice, left. B-183 removed the false addresses and the Canadian suppliers from that path.
- **N-86** · Document capture · An invoice number in groups of three followed by more text on its line ("No de facture 123 456 Date ...") is still cut at the first space (B-185 joins it only when nothing follows); capping its confidence would need a new method name, which changes calibration keys. Left.
- **N-87** · Web routes · B-192 keys a colleague's drafts by (name, address): a colleague whose computer gets a new address
- **N-88** · Intake, email and command line · A photo over Pillow's ~179 MP limit is still refused (B-148 only words it truthfully). Reading it would mean raising `Image.MAX_IMAGE_PIXELS`, which is process-wide and shared by the reading threads, or decoding JPEGs at a reduced size (`draft`); either is a decision about memory on a laptop, not a bug fix.
- **N-89** · Intake, email and command line · A .msg picture's Content-ID referenced only from an RTF body (no HTML stream) counts as inline only when Outlook marked it hidden: the compressed RTF is not decompressed to look for `cid:`.
- **N-90** · Setup, launcher and update · B-167 finds a running `watch` / `read-pages` by the note it leaves; one started by a version from before the fix leaves none, so the first update from such a version still can't stop it (finding it would mean scanning every process and telling which data folder it uses). Stop it by hand before that one update (worth a line in WHATS_NEW / UPGRADING when released).
- **N-91** · Setup, launcher and update · `watch` still treats an invoice that failed for a missing module as seen (no retry). Not changed, as the finding suggested: with the older watcher stopped, nothing fails that way; a retry rule for failures is a design choice.
- **N-92** · Setup, launcher and update · an older `watch` / `read-pages` run by another computer account (or a scheduled task under one) can't be ended by this account: the start waits a minute ("close the window it runs in, or end its task in Task Scheduler"), then stops. Worker notes don't carry the account (B-168 covers servers only).
- **N-93** · Setup, launcher and update · two accounts sharing one folder on macOS/Linux: the server file is mode 0600 and the code folder's lock file belongs to the first account, so the second account's start can't read the token or take the lock; it now relies on `/healthz`'s `user` (B-168). Sharing one folder between accounts is documented for Windows only.

## Sweep 4 (real-world testing), October 2026

### Unusual files

A robustness corpus (about 120 files: public PDF and picture test files from pdf.js and Pillow, and files made from a sample invoice: encrypted, empty, cut off, outlined text, huge pages, 300 pages, forms and attachments, PDF/A, CCITT scans, CMYK and 16-bit pictures, multi-page TIFFs, GIF, WEBP, HEIC, tiny pictures, wrong extensions, Unicode and very long names, read-only files, a file still being written) was run through the pipeline (`process_many` on the invoices folder), the dashboard upload and `python -m ap_coder process`. No file crashed or hung (the slowest took 34 s) and none was lost; the findings below are files read wrongly or refused with a message that did not say what to do. Each test makes its own fixture (`tests/test_unusual_files.py`).

#### B-260 · A PDF named ".jpg" was refused, a picture named ".pdf" could not be stamped, a web page named ".pdf" went to the queue as a blank invoice · medium
- **Where:** `ap_coder/ai/pipeline.py` (`_as_pdf`), `ap_coder/intake/pictures.py` (`content_kind`, `as_its_kind`)
- **Symptom:** a PDF saved as `scan from vendor.jpg` failed with "can't be opened as a picture: the file is damaged or not really a JPG"; a phone's PNG saved as `invoice 77.pdf` was read (PyMuPDF looks at the content) but stayed a PNG named .pdf, so its approval stamp failed ("is no PDF", `stamp.stamped_pdf`); a vendor portal's sign-in page saved as `invoice.pdf` was read as a one-page invoice with nothing on it and went to the queue.
- **Cause:** every reader chose by the file's extension, never by its content.
- **Reproduce:** the sample PDF copied to `pdf_named.jpg`, a PNG to `png_named.pdf`, an HTML page to `vendor_portal_login.pdf`, each through the three ways in.
- **Reproduced:** yes (`pdf_named.jpg can't be opened as a picture…`; `ValueError: is no PDF` from `stamped_pdf`; `vendor_portal_login.pdf: needs attention`, vendor and number empty).
- **Fix:** a file coming in is renamed to the extension its first bytes say (PDF, PNG, JPEG, TIFF, BMP, HEIC) when its name says another, then read as usual (a picture made into a PDF and kept in `originals/`); a web page is refused: "invoice.pdf is a web page saved with a .pdf name, not the invoice itself: open the vendor's email or portal and download the invoice again (as a PDF)." A file that can't be renamed is read under its own name as before.
- **Test:** `tests/test_unusual_files.py::test_a_pdf_named_as_a_photo_is_read_as_the_pdf_it_is`, `::test_a_picture_named_as_a_pdf_is_made_into_a_pdf_and_kept`, `::test_a_web_page_saved_as_a_pdf_is_refused_in_plain_words`
- **Lesson:** Don't trust a file's extension for what it holds: look at its first bytes before choosing a reader.

#### B-261 · A multi-page TIFF with a colour page last failed: "ValueError: unrecognized image mode" · medium
- **Where:** `ap_coder/intake/pictures.py` (`image_to_pdf`)
- **Symptom:** a scan saved as one TIFF, black-and-white pages then an 8-bit colour (palette) page, failed in all three ways in with "ValueError: unrecognized image mode" in the Failed list: a technical message, and the invoice was not read.
- **Cause:** `image_to_pdf` asks the TIFF how many pages it has (`n_frames`), which reads up to the last page; Pillow (12.3) then keeps that page's palette when it goes back to the first, and a black-and-white page with a palette can't be loaded.
- **Reproduce:** a four-page TIFF (Group 4, LZW, JPEG, PackBits; the last a palette page) through `intake.to_pdf`.
- **Reproduced:** yes (`ValueError: unrecognized image mode` from `frame.load()`).
- **Fix:** a page that is not a palette page drops the palette left from another page before it is loaded; all four pages become the PDF.
- **Test:** `tests/test_unusual_files.py::test_a_multi_page_tiff_with_a_palette_page_is_made_into_a_pdf`
- **Lesson:** Don't assume a multi-page picture's pages share one mode: test one whose pages differ.
