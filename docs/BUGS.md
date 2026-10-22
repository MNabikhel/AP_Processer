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
- **N-78** · Tax and coding rules · Discount terms written with prox or weeks ("2/10th prox net 30th", "2% 10th prox", "2% 2 weeks net 30") still give no discount (the due date is the default or the prox day): the discount date model has only days from the invoice or month end; rare, left for a design of prox discounts. Fixed in Sweep 4 (B-216).
- **N-79** · Exports, ERP and reports · The ERP CSV export (`ap_coder/erp/exports.py` `build_csv`) and the Custom CSV (`export_layout.build`) are written with a UTF-8 byte-order mark ("Excel shows accents correctly"), while the JDE Z-files are deliberately without one (docs/JDE_E1.md). D-28 / B-92 decided only the quoting, not the BOM. A loader that reads plain UTF-8 (SQL*Loader, a bulk insert) sees the first header as "﻿Batch"; but some Windows import tools need the BOM to read UTF-8 at all. Which one the pilot's ERP wants is a choice (a setting, or a D-NN), not a bug fix: left as it is. Fixed in Sweep 4 (B-224).
- **N-80** · Exports, ERP and reports · `dupaudit._same_bill` treats an exported invoice and an ERP-register row with the same number as the same bill when the vendor OR the amount agrees; the audit only compares rows with equal amounts, so another vendor's bill with the same number and amount is never flagged OTHER_VENDOR once ours is exported. That is the documented choice (the ERP's own name for the vendor, `test_an_exported_invoice_is_not_its_own_duplicate_in_the_erp`); quick search (B-152) now asks for the same vendor or the ERP's short name. Aligning the audit is a decision about false alarms, left.
- **N-81** · Exports, ERP and reports · Besides the exports (B-151), `spend.py`, `accruals.py` and `stamp.py` also read only a stored `gl_distribution`, so an invoice approved by an older version has no lines in the spend report, the accrual detail or the stamp's posting table. They could use `exports.distribution()`; outside this finding's scope, left. Fixed in Sweep 4 (B-217).
- **N-82** · AI and readers · `page_worker.reader_alive` (an invoice's "being read" mark) has the same reused-process-id weakness as the self-test mark (B-177), but there the store's `STALE_READING_HOURS` (2 hours) already puts a stale read back in line, so it only delays; left as is. Fixed in Sweep 4 (B-218).
- **N-83** · AI and readers · `Store.learning_rows` (B-175) gives an old vendor's invoice a list of its own, so the suggestion index (one slot) is rebuilt when invoices of such vendors alternate in a batch (about 0.3 s at 50,000 lessons); a per-vendor index would avoid it, a bigger change than this fix. Fixed in Sweep 4 (B-219).
- **N-84** · Document capture · The page reader's transcription is read by the same rule reader as the page (`capture/__init__.py` `_page_reader`, `transcript.py`), so a rule-parsing mistake (B-183, B-184, B-185 here) appears in both readings and fusion counts it as two independent readers agreeing (verified). Making the transcript an independent reader needs its own parsing or a fusion rule that discounts agreement on the same rule path; a design change, not a bug fix.
- **N-85** · Document capture · A day/month order taken only from a real US supplier address (`read_fields`, B-183) still settles 03/04/2026 without the ambiguous mark, as docs/CAPTURE_DESIGN.md describes. Marking it ambiguous (or likely at best) would send every US invoice dated on day 1 to 12 to a person; a policy choice, left. B-183 removed the false addresses and the Canadian suppliers from that path.
- **N-86** · Document capture · An invoice number in groups of three followed by more text on its line ("No de facture 123 456 Date ...") is still cut at the first space (B-185 joins it only when nothing follows); capping its confidence would need a new method name, which changes calibration keys. Left. Fixed in Sweep 4 (B-220).
- **N-87** · Web routes · B-192 keys a colleague's drafts by (name, address): a colleague whose computer gets a new address loses them (and bulk approval skips those invoices) until a restart. Fixed in Sweep 4 (B-221).
- **N-88** · Intake, email and command line · A photo over Pillow's ~179 MP limit is still refused (B-148 only words it truthfully). Reading it would mean raising `Image.MAX_IMAGE_PIXELS`, which is process-wide and shared by the reading threads, or decoding JPEGs at a reduced size (`draft`); either is a decision about memory on a laptop, not a bug fix. Fixed in Sweep 4 (B-222).
- **N-89** · Intake, email and command line · A .msg picture's Content-ID referenced only from an RTF body (no HTML stream) counts as inline only when Outlook marked it hidden: the compressed RTF is not decompressed to look for `cid:`. Fixed in Sweep 4 (B-223).
- **N-90** · Setup, launcher and update · B-167 finds a running `watch` / `read-pages` by the note it leaves; one started by a version from before the fix leaves none, so the first update from such a version still can't stop it (finding it would mean scanning every process and telling which data folder it uses). Stop it by hand before that one update (worth a line in WHATS_NEW / UPGRADING when released).
- **N-91** · Setup, launcher and update · `watch` still treats an invoice that failed for a missing module as seen (no retry). Not changed, as the finding suggested: with the older watcher stopped, nothing fails that way; a retry rule for failures is a design choice.
- **N-92** · Setup, launcher and update · an older `watch` / `read-pages` run by another computer account (or a scheduled task under one) can't be ended by this account: the start waits a minute ("close the window it runs in, or end its task in Task Scheduler"), then stops. Worker notes don't carry the account (B-168 covers servers only).
- **N-93** · Setup, launcher and update · two accounts sharing one folder on macOS/Linux: the server file is mode 0600 and the code folder's lock file belongs to the first account, so the second account's start can't read the token or take the lock; it now relies on `/healthz`'s `user` (B-168). Sharing one folder between accounts is documented for Windows only.

## Sweep 4 (real-world testing), October 2026

The 17 findings Sweep 3 left (N-77 to N-93) were looked at again with real-world use in mind: each one a pilot tester or AP user could hit, and that could be fixed without changing how the app is used, got a failing test first, then the fix. 9 were fixed (B-216 to B-224); 8 are left, each with its reason under *Looked at again and left*.

### Logged issues fixed

#### B-216 · Discount terms written with prox or weeks gave no discount · low-medium
- **Where:** `ap_coder/coding/terms.py:135` (`_prox_discount`, `parse_terms`, `payment`)
- **Symptom:** "2/10th prox net 30th", "2% 10th prox" and "2% 2 weeks net 30" printed on an invoice showed no early-payment discount (no "2% off until ...", no DISCOUNT_AVAILABLE), so AP could miss it; "2/10th prox net 30th" was not even read as terms (the default 30 days).
- **Cause:** the discount model counted only days from the invoice date or the month end; the weeks pattern was kept out of the discount on purpose (B-136) so 2 weeks was never 2 days, but nothing turned it into 14.
- **Reproduce:** `payment({"invoice_date": "2026-09-10", "payment_terms": "2/10th prox net 30th", "grand_total": 113, "subtotal": 100})`, and the same with "2% 10th prox" and "2% 2 weeks net 30".
- **Reproduced:** yes (`discount_pct=None, discount_by=None`, due 2026-10-10 for each)
- **Fix:** weeks are counted as days before the terms are read ("2% 2 weeks" is "2% 14 days"); a discount by a day of the next month ("2/10th prox", "2% 10 prox") is kept in a new `Terms.discount_prox_day` and runs to that day of the next month; the net is a later prox day ("net 25th prox", or "net 30th" after a prox discount), net days from the invoice ("net 30"), or, when none is printed, the discount's own day (as before). A late-payment rate is still never a discount.
- **Test:** `tests/test_terms.py::test_discount_terms_in_prox_or_weeks_give_the_discount`, `tests/test_terms.py::test_a_prox_late_payment_rate_is_still_no_discount`
- **Lesson:** Don't leave a unit out of a pattern only to stop a misreading: convert it, so the reading is right as well as not wrong.

#### B-217 · An invoice approved by an older version had no lines in the spend report, the accruals or the stamp · low
- **Where:** `ap_coder/erp/spend.py:34` (`lines`, `workbook`), `ap_coder/erp/accruals.py:97` (`_not_in_erp`), `ap_coder/erp/stamp.py:112` (`_coding_pages`, `stamped_pdf`, `batch_zip`)
- **Symptom:** an approved invoice with no stored `gl_distribution` (approved before the distribution was stored) was missing from the spend report and the full data workbook's "Posting" sheet, accrued nothing while approved but not exported, and its approved PDF showed an empty posting table with a 0.00 total; the CSV and JD Edwards files of the same invoice had its lines (B-151).
- **Cause:** B-151 gave the exports a fallback (`exports.distribution()`); these three read only the stored `gl_distribution`.
- **Reproduce:** approve the Northwind ground truth (no `gl_distribution`) in a store with the sample setup, then `spend.lines()`, `accruals.build(store, 2026-12-31)` and `stamp.stamped_pdf(...)`.
- **Reproduced:** yes (`spend.lines: []`, no NOT_IN_ERP accrual, no posting lines on the coding page)
- **Fix:** all three use `exports.distribution()` with the store's tax treatments (`stamped_pdf` / `batch_zip` take them from the invoice page, quick search and the batch ZIP), so the lines are built again from the coding with the tax setup's accounts.
- **Test:** `tests/test_sweep4.py::test_an_invoice_approved_without_a_stored_distribution_is_in_the_spend_report`, `tests/test_sweep4.py::test_an_invoice_approved_without_a_stored_distribution_is_in_the_accrual_detail`, `tests/test_sweep4.py::test_an_invoice_approved_without_a_stored_distribution_has_its_posting_on_the_stamp`
- **Lesson:** When a fallback goes into one helper, move every reader of the stored field onto it at the same time.

#### B-218 · An invoice whose reader died stayed "being read" for two hours once its process id was reused · low
- **Where:** `ap_coder/ai/page_worker.py:52` (`_process_started`, `READER_ID`, `reader_alive`)
- **Symptom:** after a dashboard (or `read-pages`) was killed while OvisOCR2 read an invoice, the invoice showed "being read" and the page reader skipped it for up to two hours when another program had since taken that process id (Windows reuses process ids often).
- **Cause:** another process's "being read" mark was judged by its process id alone (the same weakness B-177 fixed for the self-test mark); only `STALE_READING_HOURS` put it back in line.
- **Reproduce:** mark an invoice as being read with the pid of a running `sleep` and any token, then `release_interrupted(store)`.
- **Reproduced:** yes (`released: 0`, the invoice still `reading`)
- **Fix:** `READER_ID` also notes when its process started (Linux `/proc/<pid>/stat`, Windows `GetProcessTimes`, macOS `ps -o lstart`); a mark whose process id now has another start is not alive, so the invoice is read again at once. A mark from an older version (no start noted), or a start that can't be told, is judged by the process id as before.
- **Test:** `tests/test_sweep4.py::test_a_reading_mark_whose_process_id_another_program_now_has_goes_back_in_line`
- **Lesson:** A process id names a process only together with its start time: note both in a mark another process checks.

#### B-219 · Past 50,000 lessons, invoices of an old vendor and of others in turn rebuilt the suggestion index each time · low
- **Where:** `ap_coder/core/memory.py:149` (`RowsCache`), used by `ap_coder/ai/suggest.py` (`_memory`) and `memory.aggregate`
- **Symptom:** in a batch where invoices of a vendor whose history was imported first alternate with other vendors' invoices, each invoice waited for the GL suggestion index to be built again (about 0.3 s at 50,000 lessons, more on a slow laptop).
- **Cause:** B-175 gives an old vendor's invoice a list of its own (`Store.learning_rows`); the index cache kept one list only, so each change of list rebuilt it.
- **Reproduce:** 1 history row for Northwind, then 30 for other vendors, the read cap at 20; `suggest_gl` for Northwind, another vendor, Northwind, another, Northwind with `store.learning_rows(vendor)`.
- **Reproduced:** yes (`builds: 5`)
- **Fix:** `RowsCache` keeps the latest two lists (the shared one and the old vendor's), the most recent first; a list seen again is found by the same row objects or by what they say, as before. The second index is the only memory added (about 40 MB at 50,000 lessons).
- **Test:** `tests/test_sweep4.py::test_invoices_of_an_old_vendor_and_others_in_turn_share_the_suggestion_indexes`
- **Lesson:** When a fix gives some callers their own variant of a shared input, give the cache in front of it room for that variant.

#### B-220 · An invoice number in groups of three was still cut when more text followed it on its line · low-medium
- **Where:** `ap_coder/capture/reader.py:331` (`_id_from`, `_DIGIT_GROUPS`)
- **Symptom:** "No de facture 123 456 Page 1 de 1" (or "... 123 456 Date ...", common when a scan merges the columns of a French-Canadian invoice header) was read as invoice number 123, verified.
- **Cause:** B-185 joined the digit groups only when nothing followed them on the line.
- **Reproduce:** `_id_from(" 123 456 Date 2026-09-14", 0)`; the Acme test invoice with "No de facture 123 456 Page 1 de 1" on one line.
- **Reproduced:** yes (`'123'`)
- **Fix:** the groups of three are joined when the line ends after them or a word follows them; another figure after them ("123 456 2026-09-14") or a group that is not three digits still leaves the first number alone, as before. The method name and confidence are unchanged (no calibration key moves): the join is as sure as B-185's.
- **Test:** `tests/test_sweep4.py::test_an_invoice_number_in_digit_groups_with_more_text_after_it_is_read_whole`
- **Lesson:** When a rule needs "nothing after it" to be safe, ask what may safely follow instead: a word is not part of a number.

#### B-221 · A colleague whose computer got a new address lost their unsaved edits, and bulk approval skipped the invoice · low-medium
- **Where:** `ap_coder/web/pages/invoice.py:99` (`_draft_key`, `rename_drafts`), `ap_coder/web/identity.py:88` (`cookie_value`, `from_cookie`, `Person`), `ap_coder/web/pages/who.py:29` (`who_save`)
- **Symptom:** a colleague on the office network edits an invoice, then their laptop moves from Wi-Fi to cable (or the network renews its address): the review screen no longer shows their edits, Start over can't drop them, and bulk approval skips that invoice ("it has edits open on its review screen") until AP Coder restarts.
- **Cause:** B-192 kept each colleague's drafts by (name, address), and the address is the network's to change.
- **Reproduce:** colleague "Ana" at 10.0.0.6 posts `/review/{id}/check` with a PO; the same browser (same cookie) opens `/review/{id}` from 10.0.0.16, then presses Start over.
- **Reproduced:** yes (the PO is not on her screen; the draft is still held after Start over)
- **Fix:** the signed name cookie also carries an id of the browser, made when it first gives its name and kept when it gives a new one; drafts are kept by (name, case aside, browser id), so two browsers that give one name still keep their own edits (B-192) and a new address changes nothing. A cookie without an id (none can outlive a restart, the secret is new each start) is still kept by address.
- **Test:** `tests/test_network_identity.py::test_a_colleague_whose_computer_gets_a_new_address_keeps_their_edits_and_can_start_over`
- **Lesson:** Don't key a person's state by their network address: give the browser an id of its own.

#### B-222 · A photo from a phone's 200 MP mode was refused instead of read · low
- **Where:** `ap_coder/intake/pictures.py:149` (`_within_pixel_limit`, `image_to_pdf`)
- **Symptom:** an invoice photographed in a phone's 200-megapixel mode failed with "the picture is too large (200 megapixels) ..." (B-148): AP had to ask for it again or retake it.
- **Cause:** Pillow refuses a picture over twice its limit (about 179 million pixels) before decoding it; raising `Image.MAX_IMAGE_PIXELS` would lift the guard for every reading thread.
- **Reproduce:** an 800 x 600 JPEG with `Image.MAX_IMAGE_PIXELS` at 40,000 (480,000 pixels: over twice the limit, as 200 MP is over 179 MP) through `to_pdf()`.
- **Reproduced:** yes (`DecompressionBombError: Image size (480000 pixels) exceeds limit of 80000 pixels`)
- **Fix:** a JPEG over twice the limit is opened from its header alone and decoded at the smallest reduction JPEG offers (half, a quarter or an eighth: a 200 MP photo at half, 50 MP, less memory than a 179 MP photo Pillow accepts today) that brings it under the limit, then re-encoded as a high-quality JPEG with its EXIF and its resolution scaled, so the page keeps its size. The photo is kept in `originals/` as it came. The guard is not touched; a PNG, TIFF or HEIC that large, or a JPEG too large even at an eighth, is still refused in plain words.
- **Test:** `tests/test_sweep4.py::test_a_jpeg_photo_too_large_to_open_is_read_at_a_smaller_size`
- **Lesson:** A safety limit on decoded size can often be met by decoding smaller, not by refusing or by lifting the limit.

#### B-223 · A signature picture an Outlook email showed only in its rich text went to the queue as an invoice · low
- **Where:** `ap_coder/intake/msgfile.py:215` (`decompress_rtf`, `_rtf`, `_message`), `ap_coder/bench/msgwriter.py` (`Message.rtf`)
- **Symptom:** a .msg with no HTML stream (an HTML email Outlook or Exchange kept as compressed RTF, with the HTML inside it) showing a signature logo by its Content-ID: the logo, not marked hidden, was taken for a scanned invoice and put in the queue.
- **Cause:** B-142 counts a picture with a Content-ID as inline only when it is hidden or its `cid:` is in the HTML body; the compressed RTF body was never decompressed to look there.
- **Reproduce:** `build_msg(Message(rtf=r'{\rtf1\ansi\fromhtml1 {\*\htmltag84 <img src="cid:image001.jpg@01DA">}...}', attachments=[image001.jpg with that Content-ID, scan.jpg]))` through `read_msg_mail()`.
- **Reproduced:** yes (`found: ['image001.jpg', 'scan.jpg']`)
- **Fix:** `decompress_rtf` reads PidTagRtfCompressed (MS-OXRTFCP, "LZFu" or stored as it is, bounded at 20 MB; standard library only, as the rest of the .msg reader); a picture whose `cid:` is not in the HTML is looked for in the RTF too (read once per message, only then). A damaged RTF body counts as none. The test writer (`bench/msgwriter.py`) can now write an RTF body.
- **Test:** `tests/test_sweep4.py::test_a_picture_an_outlook_email_shows_only_in_its_rich_text_is_a_logo`, `tests/test_sweep4.py::test_compressed_rtf_is_read_as_outlook_writes_it`
- **Lesson:** When a rule looks for a link in "the text", look in every form the text is kept in.

#### B-224 · The custom CSV for an ERP's loader always began with a byte-order mark · low
- **Where:** `ap_coder/erp/export_layout.py:137` (`build`, `Layout.byte_order_mark`), `ap_coder/web/pages/exports.py:157` (`layout_from_form`), `ap_coder/web/templates/exports.html`
- **Symptom:** an ERP loader that reads plain UTF-8 (SQL*Loader, a bulk insert) given the Custom CSV took its first header as "Vendor" with an invisible character in front of it (U+FEFF) and refused the file or the column; nothing in the layout could change that, though the layout is the one export made "for the ERP's loader".
- **Cause:** `export_layout.build` always encoded with `utf-8-sig`; D-28 / B-92 decided the quoting only, and some Windows tools need the mark, so neither choice is wrong for every ERP.
- **Reproduce:** `exports.build("custom", [invoice], ..., layout)` with any layout: the file starts with `EF BB BF`.
- **Reproduced:** yes (`data[:3] == b'\xef\xbb\xbf'` for every layout)
- **Fix:** the layout has a "Byte-order mark (for Excel)" box beside "Header row", ticked by default and for every layout saved before, so nothing changes until AP unticks it for a loader that reads the first header wrong. The ERP CSV and the other downloads for Excel keep the mark; the JD Edwards files keep none (docs/JDE_E1.md).
- **Test:** `tests/test_export_layout.py::test_the_custom_csv_can_leave_out_the_byte_order_mark_for_a_database_loader`, `tests/test_web_finance.py::test_the_custom_layout_keeps_or_leaves_out_the_byte_order_mark`
- **Lesson:** When neither of two encodings is right for every reader, make it a choice of the layout that writes for that reader, with today's behaviour as the default.

### Looked at again and left

- **N-77** · A lesson an approval made before B-158 added still can't be taken back: nothing recorded which approval added what, and rebuilding the lessons would replay every past approval in order.
- **N-80** · The duplicate audit keeps treating an exported invoice and an ERP row with its number and amount as one bill whatever the ERP calls the vendor ("BELL CANADA" for "Bell Mobility Inc."). Search's rule (B-152: the same vendor or the ERP's short name) would flag every exported invoice whose vendor the ERP names otherwise; a different vendor's bill with the same number and the same amount to the cent is far rarer than that false alarm.
- **N-84** · The page reader's transcript read by the same rules as the page: making it an independent reader is a design change of fusion, not a fix.
- **N-85** · A US supplier's day/month order: marking it ambiguous would send every US invoice dated on day 1 to 12 to a person; a policy choice.
- **N-90** · A `watch` / `read-pages` of a version before B-167 leaves no note, so the first update from it can't find it without scanning every process. docs/UPGRADING.md (*What it cannot do*) now says to close it before that one update, and that what it fails shows with **Retry**.
- **N-91** · An invoice that failed (for a missing module or anything else) is not read again by `watch` on its own: it shows as failed in the review queue with **Retry**, and an automatic retry rule would fail it again at every check while the install is broken. Since B-167 nothing fails that way after an update.
- **N-92** · An older `watch` run by another computer account can't be ended by this one (Windows lets only that account or an administrator end it); the start already says to close its window or end its task in Task Scheduler.
- **N-93** · Two accounts sharing one folder on macOS/Linux: the file permissions are the system's; sharing one folder between accounts is documented for Windows only.

### First launch

A fresh copy of the code (`git archive`), its own home folder and `AP_ALLOW_INTERNET=0`, started with
`scripts/launch.py` as a double-click does, then the first hour in Chromium: welcome, name, GL accounts, the first
invoices, the first approval, the first export and Help, read as a pilot tester with no developer knowledge would.

#### B-240 · The AP Coder window filled with technical lines once a scan was read · medium
- **Where:** `ap_coder/ops/cli.py:745` (`main`: `logging.basicConfig(level=WARNING)`)
- **Symptom:** the black window the tester keeps open printed `2026-10-20 03:31:57,765 INFO ap_coder.ai.pipeline: local model could not suggest accounts (The local model isn't answering at http://127.0.0.1:1234/v1 …); left for AP` for every invoice processed, and `INFO ap_coder.ai.page_worker: AI reviewer: invoice 1 postponed (0 round(s), 0 s)` every minute, though `serve` runs without `-v`.
- **Cause:** importing rapidocr (the OCR engine) sets the root logger to INFO; the window's handler had no level of its own, so the root logger's level was the only filter.
- **Reproduce:** `logging.basicConfig(level=logging.WARNING)`, then `import rapidocr`: `logging.getLogger().level` is 20; or start AP Coder and process an invoice without LM Studio.
- **Reproduced:** yes (the lines above in the launcher's window; `before 30 … after rapidocr 20`)
- **Fix:** `configure_logging()` sets the level on the window's own handler as well, so a library changing the root logger no longer changes what the window shows; `-v` still shows everything.
- **Test:** `tests/test_sweep4_first_launch.py::test_the_window_shows_warnings_only_even_after_the_ocr_engine_turns_the_root_logger_to_info`
- **Lesson:** Don't let the root logger's level be the only filter of what a person sees: a library may change it.

#### B-241 · A page that failed showed the bare text "Internal Server Error" · medium
- **Where:** `ap_coder/web/app.py:168` (only `HTTPException` had a handler)
- **Symptom:** any error a page did not expect (a locked database, a damaged file) answered with a white page reading `Internal Server Error`: no menu, no way back, nothing on what to do; a form sent from a page said only "That didn't work (500)."; the window printed a traceback.
- **Cause:** the dashboard drew its own page for "not found" only; everything else fell through to Starlette's default answer.
- **Reproduce:** a route that raises `RuntimeError("database is locked")`; GET it with `Accept: text/html`.
- **Reproduced:** yes (`500 text/plain; charset=utf-8 'Internal Server Error'`)
- **Fix:** `web/errors.py`: the dashboard's own error page, with the menu, "What to do: go back and try again; if it happens again, Help → Report a problem", and "Details for IT" folded away (the error in one line, and where the full details are). The traceback goes to `~/.ap_coder/logs/dashboard-errors.log`, the window says one line. A form gets `{"ok": false, "error": …}` with the same advice; when the page frame itself can't be drawn (it reads the database), a plain page says the same.
- **Test:** `tests/test_sweep4_first_launch.py::test_a_page_that_fails_says_what_to_do_in_the_dashboard_with_the_details_folded_away`, `…::test_a_page_that_fails_still_answers_when_the_page_frame_itself_fails`
- **Lesson:** Don't leave the unexpected error to the framework's default: it is the one page a person most needs help on.

#### B-242 · A second copy that could not set itself up stopped the AP Coder that worked · medium
- **Where:** `scripts/launch.py:1840` (`run`: `running.take_over` before `ensure_packages`)
- **Symptom:** with AP Coder open, a second folder double-clicked by mistake (GitHub's ZIP, or a bundle unzipped without its `wheelhouse` folder) printed "AP Coder 0.2.0 from … is still running (port 8501): stopping it, so this version takes over. Stopped it.", then "✗ Missing packages: openpyxl, openai, pandas, … What to do: Unzip the full offline bundle …": the tester was left with no AP Coder at all.
- **Cause:** the start makes way for itself (stops an older or other folder's AP Coder) before it installs packages; when nothing can be installed (offline, no wheelhouse) it can only fail after that.
- **Reproduce:** start AP Coder from one folder; extract the code alone into another folder and double-click its launcher with `AP_ALLOW_INTERNET=0`.
- **Reproduced:** yes (the lines above, in the walk-through; the first dashboard stopped answering)
- **Fix:** `cannot_set_up()` asks first whether the core packages are missing with nowhere to install them from; if so the start stops there, before anything running is touched, and says "The AP Coder already open was left running." when one is open. A copy that can set itself up takes over as before.
- **Test:** `tests/test_sweep4_first_launch.py::test_a_copy_that_cannot_set_itself_up_leaves_the_running_ap_coder_alone`, `…::test_a_copy_that_can_set_itself_up_still_takes_over`
- **Lesson:** Don't stop what works before knowing the replacement can start.

#### B-243 · Help sent emails "on Process invoices", a page the menu doesn't have · low
- **Where:** `ap_coder/ops/help.py:496` (`FAQ`, "Our invoices arrive by email…")
- **Symptom:** Help → Questions said the emails that need a person wait "under Emails that need a person on Process invoices"; the menu and the page say **Import invoices**. The same answer sent IT to `docs/MAILBOX_SETUP.md`, a path in the code folder.
- **Cause:** the page was renamed and the answer kept the old name (as B-203's guide did with a button).
- **Reproduce:** open /help, Questions, the email question; compare with the menu.
- **Reproduced:** yes (`… waits under Emails that need a person on Process invoices.`)
- **Fix:** the answer says "on Import invoices", and "the mailbox setup guide that comes with AP Coder" instead of the path.
- **Test:** `tests/test_sweep4_first_launch.py::test_help_names_the_pages_by_the_names_the_menu_gives_them`
- **Lesson:** Don't rename a page without searching Help's own texts for its old name, not only the guides.

#### B-244 · A dashboard that stopped on its own left "The message above says what to do" with no message · medium
- **Where:** `scripts/launch.py:1804` (`start_dashboard`: returns the server's exit code and says nothing)
- **Symptom:** when the dashboard stopped on its own (it crashed, or something ended it), the window printed nothing more and closed with code 241 (Linux) or, on Windows, `APProcessor.bat` said "AP Coder stopped. The message above says what to do." under the dashboard's last normal line: a dead end.
- **Cause:** a server exit was treated as the normal end of a start; only setup failures said what to do.
- **Reproduce:** start AP Coder, then end its `python -m ap_coder serve` process (in the walk-through: `pkill -f "ap_coder serve"`).
- **Reproduced:** yes (the window's last lines were "AP Coder is running at http://127.0.0.1:8501 …" then `[exited with code 241]`)
- **Fix:** a server that ends with an error code adds "✗ AP Coder stopped on its own (code N); the lines above may say why. What to do: double-click AP Coder again. If it happens again, use Help → Report a problem, and send IT the log …". A stop asked for (an update taking over, Ctrl+C) says nothing, as before.
- **Test:** `tests/test_sweep4_first_launch.py::test_a_dashboard_that_stops_on_its_own_says_so_and_what_to_do`
- **Lesson:** Don't let a promise ("the message above says what to do") depend on every path having printed one.

### Unusual files

A robustness corpus (about 120 files: public PDF and picture test files from pdf.js and Pillow, and files made from a sample invoice: encrypted, empty, cut off, outlined text, huge pages, 300 pages, forms and attachments, PDF/A, CCITT scans, CMYK and 16-bit pictures, multi-page TIFFs, GIF, WEBP, HEIC, tiny pictures, wrong extensions, Unicode and very long names, read-only files, a file still being written) was run through the pipeline (`process_many` on the invoices folder), the dashboard upload and `python -m ap_coder process`. No file crashed, none showed a traceback on the review screen and none was lost; a text PDF took 1 to 4 s and a one-page scan or photo 15 to 25 s (55 to 90 s on the second run, the computer then shared by other test runs at a load of 78 on 4 cores); only long scans ran for minutes (N-94). The findings below are files read wrongly or refused with a message that did not say what to do. Each test makes its own fixture (`tests/test_unusual_files.py`).

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

#### B-262 · A PDF with no pages went to the review queue as a blank invoice · medium-low
- **Where:** `ap_coder/ai/pipeline.py` (`_check_pdf`, called by `_process`)
- **Symptom:** a PDF with no page in it (a program that saved before writing a page; pdf.js's `bug1020226.pdf`) was "read and coded" and waited in the queue as "needs attention" with an empty vendor and number and no page to show (its page image 404).
- **Cause:** nothing checked the page count: the readers read zero pages and the coder made an invoice of nothing.
- **Reproduce:** a hand-written PDF whose page tree is empty (`/Count 0`), through the three ways in.
- **Reproduced:** yes (`zero_pages.pdf: needs attention`, vendor and invoice number empty, `/review/1/page/1.png` 404).
- **Fix:** a PDF with no pages is refused before it is read: "statement.pdf has no pages in it: the file is empty or was not saved completely. Ask the vendor for a new copy." (Failed / rejected; the file stays in the folder.) A password-protected PDF is still worded as such (a test now guards that message, and that a PDF with only an owner password is opened with the empty user password).
- **Test:** `tests/test_unusual_files.py::test_a_pdf_with_no_pages_is_refused_in_plain_words_not_queued_blank`, `::test_a_password_protected_pdf_says_so_and_one_with_only_an_owner_password_is_read`
- **Lesson:** Don't let "read nothing" pass as "read an invoice with nothing on it": check there is something to read first.

#### B-263 · A text invoice saved as "Unicode" by Notepad was read as "ÿþI N V O I C E", and a binary file named .txt went to the queue as an invoice of garbage · medium-low
- **Where:** `ap_coder/ai/extraction.py` (`result_from_text`)
- **Symptom:** a .txt invoice saved by Notepad as "Unicode" (UTF-16) went to the queue with the vendor "ÿþI\x00N\x00V\x00…" and nothing else read; a binary file with a .txt name went to the queue as "needs attention", its vendor a run of control characters shown in the import list.
- **Cause:** a text file was decoded as UTF-8, else Windows-1252 with replacement: UTF-16 became every other character a NUL, and anything at all was taken as text.
- **Reproduce:** the invoice text saved as UTF-16 (with its byte-order mark) and 4 KB of random bytes, each as a .txt, through the three ways in.
- **Reproduced:** yes (`vendor=ÿþI N V O I C E`, `binary_garbage.txt … needs attention`).
- **Fix:** a text file starting with a UTF-16 byte-order mark (either order) is decoded as UTF-16; one that still holds a NUL or more than 5% control characters is refused: "scan.txt isn't a text file: it holds something else under a .txt name. Ask the vendor for the invoice as a PDF."
- **Test:** `tests/test_unusual_files.py::test_a_text_invoice_saved_as_unicode_is_read_as_its_text`, `::test_a_txt_file_that_is_not_text_is_refused_in_plain_words`
- **Lesson:** Don't decode with `errors="replace"` and call the result text: check it looks like text before reading it.

#### B-264 · A WEBP or GIF invoice was refused at upload as if it were not a photo, and passed over in silence in the invoices folder · medium-low
- **Where:** `ap_coder/capture/layout.py` (`IMAGE_EXTENSIONS`), `ap_coder/ai/extraction.py` (`DOCUMENT_EXTENSIONS`), `ap_coder/intake/pictures.py` (`image_to_pdf`)
- **Symptom:** a photo saved from a chat app or a browser as `.webp` (or a `.gif`) was refused on upload with "photo.webp was not read: AP Coder reads PDFs, scans, photos and saved emails", though it is a photo; dropped in the invoices folder it was never listed, read or mentioned, and `python -m ap_coder process` said "No supported invoice files found."
- **Cause:** the picture types were a fixed list without WEBP and GIF, although Pillow (already used for every picture) reads both.
- **Reproduce:** a WEBP and an animated GIF of a page, through the three ways in.
- **Reproduced:** yes (the upload message above; the folder's file list and `invoice_files` without them; exit 2 from `process`).
- **Fix:** WEBP and GIF are pictures like the others: made into a PDF on arrival (losslessly), the picture kept in `originals/`. An animated one gives its first frame, the page it shows, not a page per frame. JPEG 2000, AVIF and Photoshop files are still refused at upload in plain words (rare for invoices).
- **Test:** `tests/test_unusual_files.py::test_a_webp_or_gif_picture_in_the_invoices_folder_is_read_as_a_pdf`, `::test_the_upload_takes_a_webp_photo`
- **Lesson:** Don't keep a list of accepted types narrower than what the reader behind it reads: the person sees only "not read".

#### B-265 · "Process the folder" read a PDF the scanner was still writing: half an invoice stored, the whole one read again later as another · medium
- **Where:** `ap_coder/web/pages/process.py` (`process_folder`, `still_being_saved`), `ap_coder/intake/pictures.py` (`looks_incomplete`)
- **Symptom:** a scan still being saved into the invoices folder (its first 4 KB written) when the pilot tester clicked "Process the folder" was read as it stood: PyMuPDF repaired what was there, and it went to the queue with the vendor read but no total ("subtotal + tax = 14050.00 but grand_total = 0.00"). Once the scanner finished, the complete file (another SHA-256) was offered and read again: two invoices for one.
- **Cause:** the folder pick-up left out only files it could not open; on macOS (and for many Windows scanners) a file being written can be opened, and the `watch` command's 5-second settle time was not used there.
- **Reproduce:** half of a sample PDF in the invoices folder, `POST /process/folder`; then the whole file.
- **Reproduced:** yes (`still_being_written.pdf: needs attention`, `total=0.0`, in all three ways in).
- **Fix:** a file changed in the last minute whose end is missing (a PDF without "%%EOF", a JPEG without its end marker, a PNG without its end chunk, or an empty file) is left for next time with the message already used for a locked file: "1 file could not be opened (still being saved, or open in another program): picked up next time." A complete file is read at once, however new. (A file that is still cut off after a minute is read, with a warning: B-266.)
- **Test:** `tests/test_unusual_files.py::test_the_folder_pick_up_leaves_a_pdf_still_being_written_for_next_time`
- **Lesson:** Don't take "it opens" for "it is finished": check the file's end is there before reading one that has just changed.

#### B-266 · A PDF cut off in a download went to the queue with nothing to say part of it was missing · medium-low
- **Where:** `ap_coder/ai/pipeline.py` (`_cut_off`, `_process`), `ap_coder/capture/supplier.py` (`ALWAYS_A_PERSON`)
- **Symptom:** the first 40% of an invoice PDF (a browser download that stopped, an email attachment cut short) was read as an invoice: PyMuPDF repairs what is there, so the header and some lines were read but not the total, and the review screen showed only "subtotal + tax = 4350.00 but grand_total = 0.00". Nothing said the file itself was incomplete, so the pilot tester would correct the figures from a page that is not all there; a cut-off file whose figures happened to add up could have been approved without a person.
- **Cause:** nothing looked at whether the file ended as a PDF ends.
- **Reproduce:** the sample PDF cut at 40% (and at its last 300 bytes), through the three ways in.
- **Reproduced:** yes (`truncated_40pct.pdf … total=0.0`, no word of the file; PyMuPDF's `is_repaired` true, no "%%EOF" in the last kilobyte).
- **Fix:** a PDF without "%%EOF" in its last kilobyte is still read and shown, with a warning on the review screen (and its entry in Help, "File cut off"): "download.pdf looks cut off (its end is missing: it was still downloading or being saved when it came in), so part of the invoice may be missing. Compare it with the vendor's copy, or ask the vendor for a new one." It is never approved without a person ("the file looks cut off", listed in Settings → Automation). Across the pdf.js test files and the ten samples no complete PDF gets the warning.
- **Test:** `tests/test_unusual_files.py::test_a_pdf_cut_off_is_read_with_a_warning_and_never_approved_without_a_person`
- **Lesson:** Don't let a reader's silent repair stand for a whole file: when a file had to be repaired, say so to the person.

#### B-267 · A 300-page PDF was read from its first 20 pages with nothing on the review screen to say so · medium-low
- **Where:** `ap_coder/capture/bridge.py` (`review_issues`), `ap_coder/capture/layout.py` (`READ_PAGES`)
- **Symptom:** a 300-page file (a statement with the invoice on page 140, several invoices scanned as one file) was read from its first 20 pages only, as designed, but the review screen said nothing of it: the fields, lines and totals looked like a reading of the whole file. `CaptureResult.pages_left_out` (280) kept it from touchless approval, but that reason is never shown on the review screen.
- **Cause:** the pages left out were passed to the touchless gates only, not to the review report.
- **Reproduce:** the sample's two pages repeated to 300 (text) and a 300-page scan whose pages all differ, through the three ways in.
- **Reproduced:** yes (`capture.page_count` 300, `pages_left_out` 280; no mention on `/review/1`).
- **Fix:** a warning on the review screen: "only 20 of its 300 pages were read (a file's first 20 pages are read, and a page OCR can't read is left out): check the other pages yourself before approving", with its entry in Help ("Pages not read"). Time stays bounded by the 20 pages read: 2 s for the 300 text pages; for scans each page read costs an OCR (the 250-page fax took 285 s here, with the computer shared by several test runs at a load of 40 on 4 cores; see N-94).
- **Test:** `tests/test_unusual_files.py::test_a_300_page_pdf_says_on_the_review_screen_that_only_its_first_pages_were_read`
- **Lesson:** Don't keep a limit silent: what isn't read must be said where the person checks what was.

#### B-268 · A file in the invoices folder of a kind AP Coder doesn't read was never mentioned · low
- **Where:** `ap_coder/ai/pipeline.py` (`other_files`), `ap_coder/web/pages/process.py` (`process_folder`, `process_page`, `not_read_here`), `ap_coder/web/templates/process.html`, `ap_coder/ops/cli.py` (`_inputs`)
- **Symptom:** a JPEG 2000 scan (`.jp2`), an AVIF photo or a Photoshop file dropped in the invoices folder was not listed on Import invoices, not read and not mentioned anywhere: to the pilot tester the invoice was lost. `python -m ap_coder process <folder>` said only "No supported invoice files found."
- **Cause:** the folder is listed through the readable kinds only; anything else was filtered out without a word (the upload, by contrast, says "was not read").
- **Reproduce:** `scan.jp2` and `layout.psd` beside a PDF in the invoices folder; open Import invoices, process the folder; `process` on a folder holding only `scan.jp2`.
- **Reproduced:** yes (only the PDF listed and read; no message).
- **Fix:** Import invoices says under the folder "Not read from this folder: layout.psd, scan.jp2. Save them as PDF (or JPEG) to have them read."; processing the folder adds "2 files in the invoices folder are not a kind AP Coder reads (PDFs, scans, photos and saved emails): save them as PDF (or JPEG) to have them read." with their names; `process` prints "Left out, not a kind AP Coder reads (save as PDF or JPEG): scan.jp2". Hidden files, README files, Office lock files, `desktop.ini` / `Thumbs.db` and downloads still arriving (`.part`, `.crdownload`, `.tmp`…) are not mentioned.
- **Test:** `tests/test_unusual_files.py::test_the_invoices_folder_says_which_files_it_does_not_read`, `::test_process_on_a_folder_names_the_files_it_leaves_out`
- **Lesson:** Don't filter what a person put somewhere without telling them: a file passed over in silence looks lost.

#### B-269 · An upload of a photo kind AP Coder doesn't read said "AP Coder reads … photos" of a photo · low
- **Where:** `ap_coder/web/pages/process.py` (`_save_and_start`)
- **Symptom:** uploading an AVIF photo (a recent phone's or browser's format) or a JPEG 2000 scan gave "photo.avif was not read: AP Coder reads PDFs, scans, photos and saved emails (.eml, .msg)": it is a photo, so the message contradicts itself and doesn't say what to do.
- **Cause:** the message named the kinds read in general words, not the picture types.
- **Reproduce:** upload `photo.avif` (and `invoice.jp2`).
- **Reproduced:** yes (the message above).
- **Fix:** "photo.avif was not read: AP Coder reads PDFs, pictures (JPEG, PNG, HEIC, TIFF, BMP, WEBP, GIF) and saved emails (.eml, .msg). Save it as PDF or JPEG to have it read."
- **Test:** `tests/test_unusual_files.py::test_an_upload_of_a_picture_kind_not_read_says_which_kinds_are`
- **Lesson:** Don't describe what is accepted in words the refused file also fits: name the types, and what to do.

#### Unusual files left as they are

- **N-94** · A long scan takes minutes: a 300-page scan whose pages all differ took 435 s through the pipeline and 462 s by upload, a 250-page fax TIFF 285 s and 415 s (at a load of 40 on 4 cores, several test runs sharing the computer; a one-page scan took 15 to 25 s under the same load); run again at a load of 78, the 300-page scan passed the harness's 600 s limit, and a 4-page TIFF took 173 to 218 s. Both OCR reads (`build_layout`, then `second_read_layout` in `capture_invoice`) read up to 20 pages each, so it is bounded, and the job shows "Reading and coding … (1/1)" meanwhile, never stuck; but it gives no progress per page. Reading the second OCR pass only on the pages that hold the header and totals, or a per-page progress line, are design choices about reading, not a fix: left. B-267 now says on the review screen that only 20 pages were read.
- **N-95** · A page far larger than the invoice on it (the invoice drawn on a 200-inch page, or in the corner of a sheet 30 times its size) is read in 2 to 9 s and the vendor found, but not the total (`grand_total` 0.00, "subtotal + tax = 14050.00 but grand_total = 0.00" on the review screen, so it is never missed). The reading rules work in fractions of the page; rare for an invoice, a reading question rather than a file one: left.
- **N-96** · Every picture made from the same 200-dpi render of the sample (CMYK JPEG and TIFF, 16-bit grey and 48-bit colour PNG and TIFF, Group 3 fax, Group 4 CCITT in a PDF, HEIC, BMP, 32-bit BMP, WEBP, outlined text with no text layer) is read alike: vendor and subtotal found, total not, with the same "subtotal + tax" warning. The formats are handled the same way; the total is an OCR reading matter for the reading sweep, not these files.
- **N-97** · A read-only file could not be tested as such here: the tests run as root, which writes to read-only files. The code paths that write next to an invoice (`to_pdf` moving a picture to `originals/`, `as_its_kind` renaming a misnamed file, `turn_upright`) each catch the `OSError` and read the file where it is.

### Reading: line items

Measured on a corpus of 128 real invoices and receipts with known line counts and 150 hard generated Canadian invoices
(long tables, no ruled lines, landscape, photos, poor scans), scored line by line: count exact, each line's amount,
the lines adding up, and how alike the descriptions read.

#### B-340 · A long table's page subtotal and "Balance brought forward" rows were read as line items · medium
- **Where:** `ap_coder/capture/reader.py` (`_CARRIED`, used by `_read_line_items`, `_headerless_rows` and `tables.py`)
- **Symptom:** a 77-line invoice over three pages came back with 78 lines, one of them "Balance brought forward 13,447.80"; another had "Page subtotal 17,525.34" and two "Balance brought forward" rows as lines. The lines no longer added up to the subtotal, and the coder balanced them with a large negative "charge".
- **Cause:** the rows a long table prints between its pages were known only as "Brought forward", "Balance forward", "Carried forward" and "Page total"; "Balance brought forward", "Balance carried forward", "Page subtotal", "Sous-total de la page", "Montant reporté" and OCR's "Balancebrought forward" read as lines.
- **Reproduce:** a table with a "Balance brought forward 1,234.56" row above its lines, or "Page subtotal 427.74" under them.
- **Reproduced:** yes (the row read as a line with no quantity).
- **Fix:** those wordings (and German "Übertrag", "Total de la page") are rows that sum the lines, never lines. On the hard corpus's four digital long tables that had them: 9 wrong lines → 0, every line right on 4 of 4 (was 0).
- **Test:** `tests/test_line_items_sweep4.py::test_page_subtotals_and_balances_carried_between_pages_are_no_lines`
- **Lesson:** A table's running sums come in many wordings; list the phrase families (brought/carried, page subtotal) in both languages, not the one seen first.

#### B-341 · A table whose headings the reader can't read gave its totals and taxes as line items · high
- **Where:** `ap_coder/capture/reader.py` (`_headerless_rows`, `_proven_rows`, `_read_line_items`, `read_line_items`), `ap_coder/capture/__init__.py` (`_page_lines` passes the line targets)
- **Symptom:** on 34 of the 47 real digital invoices (German, Dutch and French invoices, a Hetzner bill), the lines came back with the invoice's totals block read as lines: "Positionssumme 473,00", "Steuerbetrag 56,87", "Bruttosumme 529,87", "Umsatzsteuer (S) 19,25", "Netto 87,39". 164 wrong lines over the 47 invoices; a 2-line invoice coded as 10 lines.
- **Cause:** a table the table finder found whose column headings can't be read is read by its amount column (`_headerless_rows`, made for bilingual headings OCR runs together), down to a subtotal label the reader knows. Headings and totals in other wordings ("Beschreibung … Nettobetrag", "Positionssumme", "Summe netto") were neither; the totals' block, a table of its own, was read in full; and nothing checked that the rows add up.
- **Reproduce:** a table with headings "Bezeichnung / Menge / Einzelpreis / Betrag", two lines, then "Umsatzsteuer 7 % 19,25 … Rechnungsbetrag netto 473,00 … Bruttosumme 529,87" inside the table found.
- **Reproduced:** yes (the two lines, the two taxes and two totals).
- **Fix:** rows read by their amounts are a guess, kept only when they add up: to a row under them that prints their sum (whatever its label: the rows end there), to the totals' row they end at, to the invoice's subtotal or total less taxes, or (two lines or more) to an amount printed elsewhere on the invoice, together with the rows read on the invoice's other pages; else none (a wrong line is worse than a missing one). A row whose words name a tax or a total in any of the invoice languages ("…steuer", "…summe", "netto", "total", "USt."), or a zero with no quantity, is no line, and a total's label before the rows is skipped. Real digital invoices: wrong lines 164 → 2 (both on an invoice whose truth gives no amounts), line count exactly right 13 → 18 of 47, every line right 12 → 17; lines found 83 → 60 (partial readings that didn't add up are no longer returned).
- **Test:** `tests/test_line_items_sweep4.py::test_rows_read_by_their_amounts_end_at_the_row_that_sums_them`, `::test_rows_read_by_their_amounts_are_kept_only_when_they_add_up`
- **Lesson:** A fallback reader that guesses must prove its guess: here the proof (the lines add up to a printed sum) is on the page, so look for it before returning anything.

#### B-342 · A receipt's items were never read: no column headings, no line items · medium
- **Where:** `ap_coder/capture/reader.py` (`listed_items`, `_listed_lines`), `ap_coder/capture/__init__.py` (`_page_lines`), `ap_coder/capture/types.py` (`LINE_SOURCES["listed"]`)
- **Symptom:** a Walmart receipt of 15 items (subtotal $84.85, read right) came back with no line items, as did 23 of the 47 receipts in the real corpus (most of them Canadian WildReceipt photos) and the short bills that print no column headings; the invoice was coded as one line for the subtotal.
- **Cause:** every line-item reader starts from a row of column headings (Description, Qty, Amount) or a table the table finder found and could read by its amounts; a till receipt prints neither: just a name, a product code and an amount with its tax flag ("$7.97J"), item after item, down to SUBTOTAL.
- **Reproduce:** a receipt with "GR MN 6PK 085430100956 $12.97", "BATH TOWEL … $7.97J", "OPP BT … $4.00 J", "COUPON 1.00-", then "SUBTOTAL $27.94", "GST 5% $1.40", "TOTAL $29.34".
- **Reproduced:** yes (no lines).
- **Fix:** when no reading of the page adds up to the subtotal, the amounts in the column right above the subtotal (or total) label, each with the words left of it on its row (product codes left out), are read upward from the label until they add up exactly to the subtotal or the total less the taxes (a coupon taken off too). Kept only when they do; never when one of them names a tax or a total ("Montant HT", "TVA", "Remise totale"), nor a single amount with no words (the subtotal printed twice). The review screen names them "the amounts listed above the subtotal". On the 38 real receipts that were not read right: line count exactly right 2 → 12, every line right 0 → 11, lines found 6 → 29, wrong lines 11 → 1 (with B-341).
- **Test:** `tests/test_line_items_sweep4.py::test_a_receipts_items_are_the_amounts_above_its_subtotal_that_add_up_to_it`, `::test_a_bills_totals_above_its_total_are_not_listed_items`
- **Lesson:** A receipt has no table, but it has a proof: its items add up to its subtotal. Read the column and keep it only when the proof holds.

#### B-343 · "Net price / Net worth / VAT [%] / Gross worth" tables: every line's amount read from the gross or the VAT column · medium
- **Where:** `ap_coder/capture/reader.py` (`_HEAD`, `_heading_name`, `_heading_cells`, `_read_line_items`)
- **Symptom:** on 8 real invoices with the columns "No. Description | Qty | UM | Net price | Net worth | VAT [%] | Gross worth", the right number of lines came back with every amount wrong: the gross amount (with VAT), or 10.00 from the "10%" VAT cell, so the lines never added up to the subtotal; under the lines a summary row ("10% 44 364,64 4 436,46 48 801,10") was read as one more line.
- **Cause:** "Net worth" and "Net price" were not headings, nor "No. Description" (the row number's heading before it), so the table was read by its amount column, the last one (gross). Where OCR ran "Net worth VAT[%]" into one segment, "VAT[%]" was taken as more words of the amount's heading, and of the cells under the heading the last one, the VAT rate, was taken as the amount. A row with no words under the lines was a line unless its own amount was their sum.
- **Reproduce:** the table above, with "Net price Net worth VAT[%]" as one segment.
- **Reproduced:** yes (amounts 1,490.55 and 308.55 for 1,355.05 and 280.50).
- **Fix:** "Net price" is the unit price, "Net worth", "Net amount" and "Extension" the amount; a row number's heading ("No.", "#", "Pos.") before a heading is no part of it; a tax's heading ("VAT", "Tax", "TPS" …) after another heading is another column; of the cells under the amount's heading, the nearest to it is the amount; a row with no words that prints the sum of the rows above it among its figures ends the lines. The 8 such real invoices: every line right 0 → 7 (the 8th has one line, its amount misread by OCR).
- **Test:** `tests/test_line_items_sweep4.py::test_net_price_and_net_worth_headings_name_the_price_and_the_amount`, `::test_a_summary_row_with_no_words_under_the_lines_is_no_line`
- **Lesson:** When a table has both a net and a gross column, the one that adds up to the subtotal is the line's amount; the headings that say so are worth knowing by name.

#### B-344 · A description wrapped onto a second line under its row was cut to its first line · low
- **Where:** `ap_coder/capture/reader.py` (`_wrapped`, `_read_line_items`)
- **Symptom:** "Marble Beautiful White Coffee Table Top Malachite Inlay Art Home Decor H5372" (three lines in the description column) was coded as "MarbleBeautifulWhiteCoffee": the account suggestion and the reviewer saw a third of it.
- **Cause:** the reader under the column headings takes the rows that have an amount; a row without one was skipped, though `tables.py` (the PDF's own table) already joined such rows to the line above.
- **Reproduce:** a line item whose description wraps onto two more lines in the description column.
- **Reproduced:** yes.
- **Fix:** a row with no amount right under a line item (within 1.2 text heights), whose words all sit in the description's column (from its heading, else the page's left, to the next heading) and are neither an amount printed as money nor a total's label, is the rest of that line's description. Real corpus, descriptions of the lines found: 0.80 → 0.85 alike (with B-343).
- **Test:** `tests/test_line_items_sweep4.py::test_a_description_wrapped_onto_the_lines_under_its_row_is_read_whole`
- **Lesson:** Two readers of the same table should share their row rules: the wrapped-description rule existed in one of them only.

#### B-345 · A page scanned more than 4 degrees off was not straightened: its table read a row off, or not at all · medium
- **Where:** `ap_coder/capture/layout.py` (`_skew`, `MAX_TILT`)
- **Symptom:** on a three-page scanned long table fed 4.9 degrees askew, the column headings of page 1 were not found ("Description" sat five rows above "Line Total") and only 30 of its 57 lines came back; a landscape scan at 5 degrees gave no lines at all.
- **Cause:** the page's tilt (`PageLayout.skew`, used only to straighten the rows of the line table) was taken as zero past a slope of 0.07 (4 degrees), there to tell a tilt from a page turned a quarter; a scanner fed by hand or a phone held askew easily passes 4 degrees, and at 5 degrees a row's description sits more than three rows from its amount across a letter page.
- **Reproduce:** OCR text lines sloping 5 degrees: `_skew` gave 0.0.
- **Reproduced:** yes.
- **Fix:** a slope up to 0.15 (about 8.5 degrees) is a tilt, straightened; a page turned a quarter (text running down it) is still not. Hard corpus scans: the 57-line table 30 → 56 lines right; the two landscape scans 0 → 3 of 3 and 0 → 5 of 6.
- **Test:** `tests/test_line_items_sweep4.py::test_a_scan_tilted_five_degrees_is_straightened`
- **Lesson:** A threshold set from the scans at hand (under 4 degrees) is not the world's: give a margin to what a person will feed the scanner.

#### B-346 · A discount line printed without its minus was added to the lines instead of taken off · low
- **Where:** `ap_coder/capture/__init__.py` (`_discounts_taken_off`, `_page_lines`)
- **Symptom:** "Promotional discount 15%  600.51" (the minus lost by OCR, or the discount printed in a column of its own) was coded as a charge of +600.51: the lines came to 1,201.02 more than the subtotal and the invoice failed `LINES_ADD_UP`.
- **Cause:** a line's amount is read as printed; nothing used the subtotal to settle the sign of a discount's line.
- **Reproduce:** lines 100.00, 50.00 and "Discount 5% 7.50" under a subtotal of 142.50.
- **Reproduced:** yes (+7.50).
- **Fix:** when the lines don't add up to the subtotal but do with each discount's line (discount, escompte, rabais, remise, rebate, coupon; no quantity) taken off, it is taken off. Hard corpus: the 10-line scan with a discount now right on every line.
- **Test:** `tests/test_line_items_sweep4.py::test_a_discount_printed_without_its_sign_is_taken_off_when_that_adds_up`
- **Lesson:** The subtotal is the proof of every line's sign as well as its amount.

#### B-347 · Header text read off an upside-down page came back as line items with phone and customer numbers for amounts · medium
- **Where:** `ap_coder/capture/reader.py` (`_read_line_items`)
- **Symptom:** a credit note scanned upside down gave six "lines" such as "Cust.ID: Billed To 774,320.00" and "#O T:1-899-555-4176 -9,692.00".
- **Cause:** a row's amount is any number under the amount's heading; the rule that drops numbers not printed as money ("12", "5%") only applied when at least two lines' amounts were printed as money, so a reading with none at all kept every row.
- **Reproduce:** under the column headings, "Cust. ID: Billed To 774320" and "Ordered: www.liberty.com -9692".
- **Reproduced:** yes (two lines).
- **Fix:** a reading in which not one amount is printed as money (two decimals or a currency sign) is no line table, unless its rows add up to the subtotal or the total less taxes. Hard corpus: that credit note's wrong lines 6 → 0 (it is coded as one line for its subtotal).
- **Test:** `tests/test_line_items_sweep4.py::test_a_table_with_no_amount_printed_as_money_is_no_line_table`
- **Lesson:** A rule that needs a majority must also say what happens when there is no majority at all.

#### B-348 · Canada Post's "Summary of charges" gave no line items · medium
- **Where:** `ap_coder/capture/reader.py` (`_charges_summary`, `_read_line_items`)
- **Symptom:** a Canada Post invoice (English or French: "Summary of charges / Sommaire des frais", Parcels, Commercial/Smartmail Marketing, Specialized services, Shipments, Adjustments) came back with no line items: coded as one line for the whole bill, though each service goes to its own account.
- **Cause:** its table heads its columns "Subtotal | GST | HST | PST/QST | Total" and leaves the services' column without a heading; the reader looks for a description or quantity heading beside an amount heading, and "Subtotal" and "Total" are the invoice's totals' labels, not column headings.
- **Reproduce:** the table above (five services, the subtotal 1,673.01 under the Subtotal column on the last row).
- **Reproduced:** yes (no lines).
- **Fix:** a heading row that holds a subtotal's label, a tax's (GST, HST, PST, QST, TPS, TVH, TVQ) and a total's is a summary of charges: each row's words are its description and its amount is the one under "Subtotal" (before tax), down to the row with no words or labelled "Total"; kept only when they add up to the amount that row prints under "Subtotal". The three Canada Post invoices in the real corpus (two digital, one scan): 0 → 5 of 5 lines right each.
- **Test:** `tests/test_line_items_sweep4.py::test_a_summary_of_charges_reads_each_service_before_tax`
- **Lesson:** The suppliers a Canadian AP desk sees every month (Canada Post, the utilities) deserve their own table shapes, checked by the sum they print.

#### B-349 · A table found cut short of its last column was read by a tax's column, and "proven" by the tax's total · medium
- **Where:** `ap_coder/capture/reader.py` (`_headerless_rows`, `read_line_items`)
- **Symptom:** a scanned Canada Post invoice came back with three lines of 3.60, 60.00 and 8.41: its GST amounts, whose sum (72.01) the invoice prints as its GST.
- **Cause:** the table finder's box stopped before the Total column; the rows read by their amounts (B-341) took the last amount inside the box (the GST's) and found their sum printed on the page.
- **Reproduce:** rows "Colis 72,05 3,60 … 75,65", "Envois 168,13 8,41 … 176,54", the box ending after the second column, "12,01" printed under them.
- **Reproduced:** yes (lines 3.60 and 8.41).
- **Fix:** rows read by their amounts are none when the page prints another amount right of a row's last one in the box: the box cut the table, and its last column is not theirs.
- **Test:** `tests/test_line_items_sweep4.py::test_rows_read_by_their_amounts_in_a_table_found_cut_short_are_no_lines`
- **Lesson:** A proof is only as good as what it is checked against: amounts that add up to *some* printed figure need to be the right column first.

#### B-350 · A balance brought forward whose label OCR lost cut off the rest of a long table · medium
- **Where:** `ap_coder/capture/reader.py` (`_read_line_items`)
- **Symptom:** on two scanned three-page tables, the lines of page 3 were missing (58 lines read as 52; 69 as 21) once a row with no words printing the sum of the rows above ended the lines (B-343); before that rule, the same row came back as a line of 38,547.58 with no description.
- **Cause:** at the top of the next page the "Balance brought forward" row had lost its label to OCR (the words on a tilted line of their own): a row with no words printing the lines' sum, which B-343 took for the summary under the table and ended the lines at.
- **Reproduce:** two lines, a row "1,635.55" with no words, then a third line.
- **Reproduced:** yes (the third line dropped).
- **Fix:** such a row is dropped, not taken as the end of the lines; the tax's and total's rows that follow it ("GST (5%)") are no lines either. Hard corpus: those two tables 52 → 58 of 58 and 21 → 68 of 69 lines right.
- **Test:** `tests/test_line_items_sweep4.py::test_a_summary_row_with_no_words_under_the_lines_is_no_line`
- **Lesson:** "This row sums the lines" says what the row is, not where the table ends: drop the row, let the labels end the table.

#### B-351 · "Montant HT", "Remise totale" and "TVA" under the lines were read as three more lines · medium
- **Where:** `ap_coder/capture/reader.py` (`_read_line_items`, `_TAX_ROW`)
- **Symptom:** a French invoice of 5 lines came back with 8: "Montant HT 21 242,65", "Remise totale 1 021,06", "TVA 4 248,53"; a scanned Indian invoice had two lines of 0.00 with no words.
- **Cause:** the table ends at a subtotal label the reader knows; "Montant HT" (amount before tax) is not one of them, and a row with no words and no amount was kept as a line.
- **Reproduce:** two lines, then "Montant HT 1,635.55", "Remise totale 81.78", "TVA 310.75" in the amount column; a row "0.00" with no words.
- **Reproduced:** yes.
- **Fix:** a row with no quantity that names a total in any wording (HT, TTC, total, Summe, netto …) and prints the sum of two or more lines above it ends the lines; a row with no words, no quantity and no amount is no line. Real corpus: wrong lines on those two invoices 5 → 0.
- **Test:** `tests/test_line_items_sweep4.py::test_a_subtotal_row_under_a_label_the_reader_doesnt_know_ends_the_lines`
- **Lesson:** The sum is the label: a row that prints the lines' sum and calls itself a total of any kind is the subtotal, whatever the wording.

#### B-352 · An amount OCR ran together with the VAT rate beside it was read as the rate · medium
- **Where:** `ap_coder/capture/reader.py` (`_heading_cells`, `_under`, `_read_line_items`)
- **Symptom:** a UK invoice's lines of 100.00 and 50.00 came back as 20.00 and 20.00 (the VAT rate), with the bank details as a third line.
- **Cause:** OCR ran the headings "Amount VAT" and each row's "100.00 20.00" into one segment; "VAT" was taken as more words of the amount's heading (whose box then covered both columns), and of the amounts in a cell the last one was taken.
- **Reproduce:** headings "Description | Qty | Amount VAT", rows "Wood Pallet 1 100.00 20.00" and "Steel rods 2 50.00 20.00".
- **Reproduced:** yes (20.00 and 20.00).
- **Fix:** a tax's heading word after a heading is a column of its own even when that heading is the segment's only other one; of two or more amounts printed as money in one cell reaching past the heading, the one nearest the heading's middle is the amount. Real corpus: that invoice 0 → 2 of 2 lines right, 4 wrong lines → 0.
- **Test:** `tests/test_line_items_sweep4.py::test_an_amount_run_together_with_the_vat_rate_beside_it_is_read_under_its_heading`
- **Lesson:** OCR joins neighbours; when a cell holds two amounts, the heading's position says which one is the column's.

### Reading: real-world vendors and totals

#### B-320 · A summary table's first row read as the invoice's subtotal, GST and total, verified · high
- **Where:** `ap_coder/capture/reader.py` (`_labelled`, new `_totals_heading_row`, `_column_below`, `_column_foot`)
- **Symptom:** Canada Post's invoice (English and French, and a scan of the French one) prints its totals as a table: "Subtotal | GST | HST | PST/QST | Total" over one row per kind of charge (Parcels, Marketing mail, ...) and an unlabelled row of column totals. The first row was read as the invoice's: subtotal 72.05, GST 3.60, total 75.65 (the invoice: 1,720.18, 72.01, 1,936.72), all verified because 72.05 + 3.60 = 75.65; in English subtotal and total 97.98 for 1,673.01.
- **Cause:** A label with no value beside it took the first amount under it ("label-below", made for a header grid's one row of values), and the totals solver verified the first row because a row adds up as well as the invoice does.
- **Reproduce:** a PDF with "Subtotal | GST | HST | PST/QST | Total" over four rows of charges and a row of column totals; `analyze()`.
- **Reproduced:** yes (`subtotal 72.05 verified ('label-below+adds-up', 'confirmed by totals add up, tax rate')`)
- **Fix:** three or more total labels side by side with nothing after them are a summary table's headings: each one reads its column (the words under the heading, past a watermark), and its value is the column's foot when the foot is the sum of the rows above it (or the only row); a column with no such foot gives nothing.
- **Test:** `tests/test_real_vendors_totals.py::test_a_summary_tables_column_totals_are_the_invoice_totals_not_its_first_row`, `tests/test_real_vendors_totals.py::test_a_column_whose_foot_is_not_its_sum_gives_no_total`
- **Lesson:** Don't take the first figure under a label as its value when the label heads a column of figures: a section of the invoice adds up as well as the whole.

#### B-321 · An amount under "PST/QST" ("TVP/TVQ") always read as PST · medium
- **Where:** `ap_coder/capture/reader.py` (`_hits_at`, new `_settle_pst_or_qst`), `ap_coder/capture/confidence.py` (`fuse`), `ap_coder/capture/types.py` (`EITHER_TAX`)
- **Symptom:** Canada Post's French invoice charges its 136,47 under "TVP/TVQ" and prints its QST number ("TVQ 1006393299"); it was read as PST (verified once the column totals of B-320 added up).
- **Cause:** The label "PST/QST" matched the PST label and stopped there; which of the two taxes a combined column holds was never asked.
- **Reproduce:** the summary table of B-320 with its QST in the "PST/QST" column and "Registration nos.: GST 119321495; QST 1006393299" printed.
- **Reproduced:** yes (`pst_amount 9.98 likely`, `qst_amount` missing, for the first row; 29.93 expected as QST)
- **Fix:** "PST/QST" and "TVP/TVQ" propose both taxes. The supplier's QST number printed and no PST number makes it the QST (and the other way round); otherwise the solver picks by the rate and the value is never verified ("printed under a PST/QST label: which of the two it is, the page does not say").
- **Test:** `tests/test_real_vendors_totals.py::test_a_pst_or_qst_column_is_the_qst_when_the_supplier_prints_its_qst_number`, `tests/test_real_vendors_totals.py::test_a_pst_or_qst_column_the_page_does_not_settle_is_never_verified`
- **Lesson:** Don't settle a label that names two taxes by the first one it names: ask the page which one the supplier charges, and say so when it does not.

#### B-322 · A heading, title, city, staff line or placeholder read as the supplier's name · medium
- **Where:** `ap_coder/capture/reader.py` (`_vendor_names`, new `_not_a_name`)
- **Symptom:** On the real invoices the supplier was "Summary of charges" and "Sommaire des frais" (Canada Post), "SAMPLE INVOICE WITH REQUIRED INFORMATION" (York University's sample), "Invoicing Requirements" and "Company Name" (form placeholders), "VAUGHAN ON", "Montreal,Quebec", "MONTREAL", "Toronto", "Server: Bhomik", "SEE BACK", "Madrid, Espana", "EUR", "2. Vendor's FULL LEGAL NAME, remit" (receipts and samples).
- **Cause:** Any short line at the top of the first page was a name unless it started with a known label word; the tallest line scored best, and a document's title or a section's heading is usually the tallest.
- **Reproduce:** a PDF with "Summary of charges" (or "VAUGHAN ON", "Server: Bhomik", "Company Name") over the supplier's name; `read_fields()["vendor_name"][0]`.
- **Reproduced:** yes (`'Contact us'`, `'VAUGHAN ON'`, `'Verkäufer'` read as the supplier)
- **Fix:** never a name: a line with a document or section word anywhere (invoice, facture, receipt, statement, summary, sommaire, details, charges, frais, contact, Rechnung...) unless it carries a legal form; a form's placeholder; a receipt's staff, table or slogan line; a field label with its value ("Guest Name: Sanjay"); a city or province alone, a place with its province or country; a currency code; a numbered note; a sentence (more than eight words, or four and more starting in lower case or ending with a period). A legal form OCR glued to the name ("CONTOSOLTD.") counts as one. When the top of the page has two names scored alike, in either order (a store's brand "CANADIAN TIRE 235" and its operator "Gestions Daniel Picard Inc", the receipt's header printed twice), the one chosen is never verified unless the vendor master confirms it.
- **Test:** `tests/test_real_vendors_totals.py::test_a_city_a_staff_line_or_a_placeholder_is_not_the_supplier`, `tests/test_real_vendors_totals.py::test_a_heading_or_title_is_not_the_supplier_but_the_name_with_its_registration_numbers_is`, `tests/test_real_vendors_totals.py::test_a_stores_brand_and_its_operators_name_leave_the_supplier_unverified`
- **Lesson:** Don't take the biggest text at the top for the supplier's name: a title and a heading are as big, and a receipt's top holds the city, the till and the server.

#### B-323 · The supplier's name, printed where the page says whose it is, was not read · medium
- **Where:** `ap_coder/capture/reader.py` (new `_seller_named`, `_under_seller_label`, `_vendor_names`; `_COMPANY`)
- **Symptom:** Canada Post's name is only in its logo (a picture) and in "Canada Post registration nos.: HST/GST 119321495; QST 1006393299" ("Numéros d'enregistrement de Postes Canada TVH/TPS ..."); Hetzner's in its sender's line over the envelope's window ("Hetzner Online GmbH • Industriestr. 25 • 91710 Gunzenhausen"), so the customer under it was read; Factur-X samples print the name under "Verkäufer" or "Rechnungsersteller" ("Verkäufer" itself was read); York University's sample under "From:". None of these was read.
- **Cause:** Only a "Remit to" / "Payable to" line on the first page and the lines at its top were looked at; the legal forms GmbH, AG, B.V., S.A., SARL, SDN BHD were not known.
- **Reproduce:** the PDFs of the tests below; `read_fields()["vendor_name"][0]`.
- **Reproduced:** yes (`'Contact us'` for "Northern Parcel registration nos.: ...", `'Verkäufer'` for the name under it)
- **Fix:** read, best first: the name with the registration numbers (English and French), the name after or under a seller's label (From, Seller, Vendor, Supplier, Verkäufer, Lieferant, Rechnungsersteller, Fournisseur, Vendeur...; past the seller's own fields such as "Nummer : 12345676", and "Name:" gives the value beside it), the first part of a sender's line when it is a company's name (the address block under it is then the customer's and counts less), and "payable to <company>" in the payment notes of page 2. Legal forms of Germany, the Netherlands, France, Spain, Italy and Malaysia count as company names.
- **Test:** `tests/test_real_vendors_totals.py::test_a_heading_or_title_is_not_the_supplier_but_the_name_with_its_registration_numbers_is`, `tests/test_real_vendors_totals.py::test_the_name_under_a_sellers_label_is_the_supplier`
- **Lesson:** Don't look for the supplier's name only where it is usually printed: look where the page says whose name it is.

#### B-324 · The total tax the totals add up with was not the one kept · medium
- **Where:** `ap_coder/capture/reader.py` (`_solve_totals`), `ap_coder/capture/confidence.py` (`_totals_that_add_up`)
- **Symptom:** A Factur-X invoice (473,00 + Steuerbetrag 56,87 = 529,87, under a VAT breakdown 19,25 / 37,62) had its total tax read as 19,25 and its right subtotal and total failed ("subtotal 473.00 + tax 19.25 ≠ total 529.87"). A katanaml sample with "VAT 88,92" (no GST/HST) had its total swapped for its subtotal: 889,20 for 978,12.
- **Cause:** The rule reader's solver tried the total tax in its sums but never put the one that added up first (only the subtotal, total, separate taxes and charges); fusion's re-balancing left the total tax out of the sum altogether, so "subtotal = total" balanced.
- **Reproduce:** the PDFs of the tests below; `analyze()`.
- **Reproduced:** yes (`{'subtotal': 473.0, 'grand_total': 529.87}` with the total tax 19.25 failed; `grand_total 889.2`)
- **Fix:** the solver ranks the total tax it added up with first; fusion counts the total tax in the sum when no separate tax is read, never with one, and does not drop it to make the subtotal equal the total.
- **Test:** `tests/test_real_vendors_totals.py::test_the_total_tax_the_totals_add_up_with_is_the_one_read`, `tests/test_real_vendors_totals.py::test_a_summary_rows_total_is_its_last_figure`
- **Lesson:** Don't let a value take part in a sum without letting the sum choose it: every part that makes a total balance is chosen by it.

#### B-325 · A tax's row in cells gave its base; two VAT rates, or a charge and a discount, could not be added · medium
- **Where:** `ap_coder/capture/reader.py` (`_labelled`, new `_rightmost_tax`, `_summed`; `_solve_totals`)
- **Symptom:** "Umsatzsteuer (S) | 275,00 | 7 | 19,25": the tax read as 275,00 (its base). "Steuerbasisbetrag USt. 7% ... 19,25" and "... 19% ... 37,62" with no total of them: no total tax. "Gesamtbetrag der Zuschläge 5,80" and "der Abschläge -9,07": the totals could only add up with the net amount after them, so the subtotal was verified as 177,49 for 180,76; on the "Rabatte" sample "abzgl. Sondernachlass 10% von 147,30 -14,73" and "zzgl. Versandkosten 5,80 7" were taken for product rows, and 193,83 (after them) was verified for 202,76. "USt. %" (a column of rates) read 19 as the tax.
- **Cause:** right of a label, the first figure was taken; the solver tried one total tax and one charge at a time; a label followed by "%" only was read like a tax.
- **Reproduce:** the PDFs of the tests below; `analyze()`.
- **Reproduced:** yes (`tax_total` missing for two rates; `subtotal 177.49 verified` on the Factur-X sample)
- **Fix:** on a tax's (or a total's, or a charge's) row of figures, the last one printed as money is its value, the first one a runner-up; two total taxes on rows of their own, or two charges (a discount among them), are also tried as their sum; "zzgl." / "abzgl." ("plus" / "less") lines are a totals block's, never a product's; a tax label with only "%" after it heads a column of rates and is passed over. When the totals add up with two printed subtotals (before and after the charges), the one chosen is never verified: which one is the subtotal is AP's convention.
- **Test:** `tests/test_real_vendors_totals.py::test_the_total_tax_the_totals_add_up_with_is_the_one_read`, `tests/test_real_vendors_totals.py::test_vat_at_two_rates_with_no_total_of_it_is_their_sum`, `tests/test_real_vendors_totals.py::test_a_discount_and_freight_between_two_subtotals_are_read_and_the_subtotal_is_not_verified`
- **Lesson:** Don't read a row of figures as one value: the base, the rate and the tax are all on it, and the tax comes last.

#### B-326 · A totals row printed across the page was not read · medium
- **Where:** `ap_coder/capture/reader.py` (`_labelled`, new `_far_right`)
- **Symptom:** March of Dimes' sample prints "Subtotal" at the left margin and "$3,050" at the right one: subtotal, HST and total all missing on a clean digital invoice.
- **Cause:** a value right of a label is looked for within half the page's width only.
- **Reproduce:** a PDF with "Subtotal" at x 20 and "$3,050" at x 520 (same for HST, Total); `analyze()`.
- **Reproduced:** yes (`subtotal`, `hst_amount`, `grand_total` missing)
- **Fix:** past half the width (up to 85%), a totals label takes the amount on its row when the row holds only figures and is level with the label to a quarter of a line (a tilted scan otherwise pairs it with the next row's amount).
- **Test:** `tests/test_real_vendors_totals.py::test_a_totals_row_printed_across_the_page_is_read`
- **Lesson:** Don't bound a label's reach by a fixed distance alone: what is between the label and the value says more.

#### B-327 · Totals labels of real invoices not known (German, Dutch, Italian, Spanish, French; "Invoice Subtotal", "VAT") · low
- **Where:** `ap_coder/capture/reader.py` (`MORE_LABELS`, `DISTRACTORS`, `_DISCOUNT`, new `_negative_totals`)
- **Symptom:** 29 German Factur-X / ZUGFeRD invoices had no total ("Bruttosumme", "Rechnungssumme Brutto", "Zahlbetrag", "Gesamtbetrag"), subtotal ("Rechnungssumme ohne USt.", "Positionssumme", "Netto") or tax ("USt.", "MwSt", "Steuerbetrag"); Dutch ("Subtotaal", "Totaal", "BTW"), Italian ("Imponibile", "Imposta IVA", "Totale"), French ("Total HT", "TVA", "Total TTC") likewise; "Net worth / VAT / Gross worth" (katanaml); Winnipeg's "Invoice Subtotal" was missing, and its 1020.58 read as the invoice number. A credit note's discount printed as a positive figure was subtracted.
- **Cause:** the labels were English and Canadian French only; "Invoice Subtotal" matched the invoice number's weak "invoice" label.
- **Reproduce:** the PDFs of the test below; `analyze()`.
- **Reproduced:** yes (all three totals missing on the German, Dutch and French fixtures)
- **Fix:** the labels above (and Rechnungsnummer, Rechnungsdatum, Fälligkeit, factuurnummer, fattura n., fecha de factura...), with their distractors (USt-IdNr., BTW-nummer, TVA intracommunautaire, Totaal excl.); "Invoice Subtotal" is no invoice number; a discount on a credit printed with negative totals is added to the credit; a tax label followed by a note and no money ("VAT 0% - export of goods outside the EU") gives no tax.
- **Test:** `tests/test_real_vendors_totals.py::test_totals_labelled_in_german_dutch_and_french_are_read`, `tests/test_real_vendors_totals.py::test_a_note_about_vat_is_no_tax_amount`
- **Lesson:** Don't assume the labels of the invoices you have seen: suppliers abroad bill Canadian companies in their own words.

#### B-328 · Terms that ask for a deposit first verified as "Net 30" · low
- **Where:** `ap_coder/capture/reader.py` (`_value_reading`), `ap_coder/capture/confidence.py` (`fuse`), `ap_coder/capture/types.py` (`PART_OF_TERMS`)
- **Symptom:** "Conditions de paiement : 30% d'acompte, solde à 30 j" (FNFE samples) was verified as "Net 30" (the due date 30 days after confirmed it); 30% of the amount is due at once.
- **Cause:** the terms pattern reads the days and nothing else on the line.
- **Reproduce:** `analyze()` on an invoice with those terms and a due date 30 days after its date.
- **Reproduced:** yes (`payment_terms 'Net 30' verified`)
- **Fix:** terms on a line that asks for a deposit (acompte, deposit, down payment, advance payment, Anzahlung) are never verified: "the terms ask for a deposit first: these days are only the balance's".
- **Test:** `tests/test_real_vendors_totals.py::test_terms_with_a_deposit_first_are_never_verified`
- **Lesson:** Don't summarise terms by the part you can parse when the rest of the line changes what is due.

#### B-329 · A receipt's tax lines "TPS (824373609RT0001) $3.82" and "13%HST 14.69" not read · low
- **Where:** `ap_coder/capture/reader.py` (`_labelled`, `_BRACKETED_REG`, `MORE_LABELS`)
- **Symptom:** Quebec restaurant receipts (WildReceipt) print the supplier's number in brackets after the tax's label and the amount further right: GST 3.82 and QST 7.61 were missing. Ontario receipts print the rate before the label ("13%HST", "15.00%HST:"): the HST was missing.
- **Cause:** a tax label followed by a registration number was taken for the number's label (right, for "GST/HST Reg. No.: 988130969 RT0003"); tax labels had to start the line.
- **Reproduce:** the receipts above; `read_fields()["gst_amount"]`, `["hst_amount"]`.
- **Reproduced:** yes (both missing on the real receipts' OCR)
- **Fix:** a registration number in brackets right after the tax's label leaves the amount right of it to the tax; a rate may come before a tax's label.
- **Test:** `tests/test_real_vendors_totals.py::test_a_receipts_tax_lines_with_the_number_in_brackets_or_the_rate_first_are_read`
- **Lesson:** Don't give a whole line to the number printed in it: a receipt squeezes the label, the number and the amount onto one row.

### Reading: layouts and formats

Found by reading a hard corpus of 150 synthetic Canadian invoices with exact truth (labels under values, odd date formats, near-identical numbers, totals at the top, deposits, credits printed positive); each fix has a small generated fixture in `tests/test_sweep4_layouts.py` (the corpus is not in the repo). Figures are the corpus's 50 digital invoices: fields right / invoices fully right.

#### B-280 · A grid that prints each label under its value is read one row off · high
- **Where:** `ap_coder/capture/reader.py` (`_labelled`, new `labels_under_values`)
- **Symptom:** A header grid printing values with their small labels UNDER them ("5874088" over "Invoice Number", the next row's "5874082" over "PO #"): the invoice number was the next row's value (the PO, one digit off, or a business number "782311542 RT 0001"), and the invoice date, due date, PO and terms were missing. 0 of 8 such invoices fully right.
- **Cause:** A label's value was looked for on its line, to its right, then below it, never above: the value below a label in such a grid is the next label's.
- **Reproduce:** the grid of `test_a_grid_that_prints_each_label_under_its_value`; `analyze()`.
- **Reproduced:** yes (`invoice_number 5874082`, invoice date, due date and PO missing)
- **Fix:** A page whose bare labels mostly have a value of theirs just over them (closer than any under them) prints labels under values: a label's value is the line just over it (method `label-above`, not measured by the benchmark, so never verified on its own record), and a line with a label of its own under it is never read as the value of the label above it. Grids of labels over values are read as before.
- **Test:** `tests/test_sweep4_layouts.py::test_a_grid_that_prints_each_label_under_its_value`, `::test_a_grid_of_labels_over_values_is_still_read_below`
- **Hard corpus (digital):** 93.9% / 80.0% → 99.4% / 96.0%; labels_under 66.7% / 0% → 100% / 100%; no silent error 90% → 100%.
- **Lesson:** Don't assume a label reads left to right and top to bottom: which side of a value its label sits on is the page's convention, and the gaps show it.

#### B-281 · Compact dates with a month name ("12APR26", "Oct-15-26") are not read · medium
- **Where:** `ap_coder/capture/normalize.py` (`parse_dates`), `ap_coder/capture/supplier.py` (`date_candidates`)
- **Symptom:** "Billing Date 12APR26", "Please pay by 27APR26", "Date 24NOV25": the invoice and due dates missing.
- **Cause:** Dates with a month name needed a four-digit year or spaces and dashes around the month ("15-Oct-26"); a day, month and two-digit year run together, or a month, day and year joined by dashes, matched no pattern. The supplier template's date reader needed a four-digit year too.
- **Reproduce:** `parse_date("12APR26")`, `parse_date("Oct-15-26")`.
- **Reproduced:** yes (`None`, `None`)
- **Fix:** Two more patterns: day, month and two-digit year run together ("15OCT26") and month-day-year by dashes ("Oct-15-26", "Sept-05-2026"), never inside a longer number ("INV-12MAR26" stays an invoice number). The template's reader takes a two-digit year printed last.
- **Test:** `tests/test_sweep4_layouts.py::test_compact_dates_with_a_month_name`
- **Hard corpus (digital):** 99.4% / 96.0% → 100% / 100%; date_odd 95.4% / 71.4% → 100% / 100%.
- **Lesson:** Don't let a date pattern assume the spaces and the century are printed: supplier systems print what fits in seven characters.

#### B-282 · A quote number under a label printed under its value is verified as the invoice number · high
- **Where:** `ap_coder/capture/reader.py` (`_labelled`, new `_value_over_label`)
- **Symptom:** A speckled scan of a bilingual labels-under grid: "Q-2026-625" (the quote number, printed over "Quote # / Soumission") verified as the invoice number; the invoice's own "075037" sat just over "Inv # / No de facture". The same on a photographed credit note: "SO-42032" verified for "CN-63312".
- **Cause:** The scan's tilt runs the grid's other labels into one line, so B-280 does not see a page of labels under values; the label's value was then looked for below it, where the next label's value is. Both OCR reads agree on the words, so the wrong value was verified.
- **Reproduce:** the grid of `test_one_label_under_its_value_with_a_quote_number_under_the_next`; `analyze(..., page_text=...)`.
- **Reproduced:** yes (the business number or the quote number read, with the page reader agreeing)
- **Fix:** One label is read from the line just over it (`label-above`) when that line reads as its value and the line under it reads as one too but sits right over a label of its own (any label: "Quote #", "Account #", a distractor) and farther from this label than the line over it.
- **Test:** `tests/test_sweep4_layouts.py::test_one_label_under_its_value_with_a_quote_number_under_the_next`
- **Hard corpus:** h-0024 and h-0061 no longer verified wrong (both now right).
- **Lesson:** Don't decide a layout only page-wide: a scan breaks the rows that would show it, and each label can still tell by its own gaps.

#### B-283 · A business number ("782311542 RT 0001") read as the invoice number · medium
- **Where:** `ap_coder/capture/reader.py` (`_value_reading`)
- **Symptom:** "GST/HST No. 123456782 RT0001" gave the invoice number 123456782 (its "No." matches the invoice number's weakest label), and "782311542 RT 0001" under a grid's "Invoice #" was read as the number.
- **Cause:** The number reader took the nine digits of a business number as an identifier; only amounts checked for registration numbers.
- **Reproduce:** `_value_reading("invoice_number", <line "782311542 RT 0001">, 0, 1.0, "x")`.
- **Reproduced:** yes (`782311542`)
- **Fix:** An invoice or PO number that starts a GST/HST business number on its line is not read.
- **Test:** `tests/test_sweep4_layouts.py::test_a_business_number_is_not_an_invoice_number`
- **Lesson:** Don't let the weakest label ("No.") read whatever number follows it: rule out the numbers that have a shape of their own first.

#### B-284 · A credit printed positive whose title was lost is read and verified as a bill · high
- **Where:** `ap_coder/capture/reader.py` (`credit_titled`, used by `credit_printed_positive`), `_vendor_names`
- **Symptom:** A faxed BC credit memo printed with positive amounts: subtotal 3,667.00, GST 183.35 and the total 4,107.04 verified positive (a credit read as a bill to pay), and the customer "Groupe Boisvert ltée" verified as the supplier. An upside-down credit (h-0044) the same: HST and "Total Credit / Montant crédité" verified positive.
- **Cause:** A credit printed positive was known only by its title at the top of page 1, which the fax and the turned page lost; the total's own label, "CREDIT TOTAL", was not used. The customer's name was taken for the supplier's because its "Bill To" label was unreadable, though "Attn: Accounts Payable" was printed under it.
- **Reproduce:** the page of `test_a_credit_whose_title_is_lost_is_known_by_its_credit_total`; `analyze()`.
- **Reproduced:** yes (`1000.0, 50.0, 1050.0`, vendor "Groupe Boisvert ltee")
- **Fix:** A document whose largest amount is on a credit total's label ("CREDIT TOTAL", "Total Credit", "Amount credited", "Montant crédité"; not "credit applied") is a credit, as a title would say; an invoice's smaller "Credit total" line does not make it one. A name printed just over "Attn: ..." / "c/o ..." / "Accounts Payable" is the customer's, never the supplier's.
- **Test:** `tests/test_sweep4_layouts.py::test_a_credit_whose_title_is_lost_is_known_by_its_credit_total`, `::test_an_invoices_credit_line_does_not_make_it_a_credit`
- **Lesson:** Don't hang a sign on one line of the page: a credit says so in its title and on its total, and a scan may keep only one.

#### B-285 · On a photo, a subtotal nothing confirms is verified · high
- **Where:** `ap_coder/capture/confidence.py` (`fuse`)
- **Symptom:** A keystoned EUR photo: the shipping line's 171,03 read on the "Sub Total" label (the tilt ran the rows together), no total read, nothing adding up, and the subtotal verified at 0.9886.
- **Cause:** Both OCR reads count as independent readers, and the evidence pattern's measured record alone reached *verified*; but the two reads share the page's geometry, so a row put on the wrong label is a mistake they share.
- **Reproduce:** `fuse()` with the same subtotal from `rules` and `ocr2`, `layout_source="ocr"`, no other field.
- **Reproduced:** yes (`verified`)
- **Fix:** On a scan or a photo, an amount no check confirms (no total read, no sum adding up with it) is likely at most (`SINGLE_READER_CAP`), with the reason "no total or sum on the page confirms this amount". Amounts the totals or lines confirm are verified as before.
- **Test:** `tests/test_sweep4_layouts.py::test_an_amount_nothing_confirms_on_a_scan_is_not_verified`
- **Lesson:** Don't count two OCR engines reading the same misplaced row as two witnesses to an amount: the arithmetic is the independent witness.

#### B-286 · On a tilted scan, a value right of its label is read from the next row · high
- **Where:** `ap_coder/capture/reader.py` (`read_fields`)
- **Symptom:** A scan tilted about 2.5 degrees: the credit note's number read from the next column's "Applies to Invoice" value; on two-column headers, the value beside a label read from the row under or over it (a near-identical number one digit off).
- **Cause:** The line items were read on the page straightened (`straightened`), the header fields on the page as tilted: two inches right of a label, the text sits a row lower.
- **Reproduce:** `read_fields()` on a layout tilted 0.045 with "B/L No." / "Invoice Number" rows and their values at x 0.44 (`_tilted_scan` in the test).
- **Reproduced:** yes (`1803062`, the B/L number)
- **Fix:** A tilted scan's header is read straightened like its line items; each reading's box is given back on the page as scanned (`_on_tilted_page`). A page whose tilt is not measured (`skew` 0) is read as before.
- **Test:** `tests/test_sweep4_layouts.py::test_a_tilted_scans_label_is_read_level_with_its_value`
- **Lesson:** Don't straighten half the page: every reading that compares positions needs the same geometry.

#### B-287 · Labels under values: the value to the right of a label is the next label's · medium
- **Where:** `ap_coder/capture/reader.py` (`_labelled`, `_over_its_own_label`)
- **Symptom:** On a labels-under page with rows a little off, "A-26-54597" (over "Applies to Invoice") level with "Credit Note #" was read as the credit's number, before the value over the label was looked at.
- **Cause:** B-280 reads the value over a label only after the line's right side; a value with a label of its own under it is that label's, and a distractor label ("Applies to Invoice", "Quote #") did not count as one.
- **Reproduce:** the rows of `test_labels_under_values_the_value_to_the_right_is_the_next_labels`; `read_fields()`.
- **Reproduced:** yes (`A-26-54597`)
- **Fix:** On a labels-under page, a value printed right over a label of its own (any label, wanted or a distractor) is never read as the value of a label beside or over it.
- **Test:** `tests/test_sweep4_layouts.py::test_labels_under_values_the_value_to_the_right_is_the_next_labels`
- **Lesson:** Don't let "to the right" outrank the page's own convention: on a labels-under page, a value belongs to the label under it.

#### B-288 · Dates printed over labels that a scan runs into one line are missed · medium
- **Where:** `ap_coder/capture/reader.py` (`_value_over_label`, `_value_reading`)
- **Symptom:** A speckled bilingual scan printing "4 Mar 2026" and "3 Apr 2026" over "Billing Date / Date de facturation" and "Payment Due / Payable avant le", the two labels read as one line: both dates missing; "Net30days" (the terms run together) offered as an invoice number.
- **Cause:** B-282 reads a label's value from over it only when a value under it belongs to another label; here nothing under the label reads as a date at all. An identifier was any token with a digit in it.
- **Reproduce:** the grid of `test_one_label_under_its_value_with_a_quote_number_under_the_next`, read without the page reader.
- **Reproduced:** yes (invoice and due dates missing)
- **Fix:** When nothing under a label reads as its value, the line just over it does, unless that line is another label's value (a label of its own just over it). "Net30days" / "Net-45" is never an invoice or PO number.
- **Test:** `tests/test_sweep4_layouts.py::test_one_label_under_its_value_with_a_quote_number_under_the_next`, `::test_a_business_number_is_not_an_invoice_number`
- **Lesson:** Don't make one side of a label the fallback for nothing: with no value under it, the value over it is the only one there is.

#### B-289 · An invoice number OCR read twice beside its title is read as the two run together · low
- **Where:** `ap_coder/capture/reader.py` (`_glued_title_numbers`)
- **Symptom:** A tilted US scan: "TAX INVOICE82-78605 82-78605" (OCR glued the title to the number and read the number again) gave the invoice number "82-7860582-78605", ranked over the right "82-78605".
- **Cause:** The glued-title reading took the whole line with its spaces removed.
- **Reproduce:** `_glued_title_numbers()` on an OCR line "TAX INVOICE82-78605 82-78605".
- **Reproduced:** yes (`82-7860582-78605`)
- **Fix:** A last token that repeats the end of the token before it is OCR's second reading of the number, and is dropped first.
- **Test:** `tests/test_sweep4_layouts.py::test_a_title_glued_to_its_number_read_twice_by_ocr`
- **Lesson:** Don't join every OCR token on a line: a number read twice is still one number.

### Reading: photos and poor scans

Measured on a corpus of 150 hard synthetic invoices with exact answers (26 phone photos of a page on a desk, 74 poor
scans: 22 scans, 16 faxes, 12 speckled, 12 upside down, 12 faint), read with OCR only (no page reader, no AI), the
table finder's model on. None of the corpus is in the repo: each test makes its own picture, degraded the same way.

#### B-300 · A shadow across a photographed page cut the page short at the shadow: its totals were lost · high
- **Where:** `ap_coder/capture/layout.py` (`flatten_photo`, new `_page_outline`)
- **Symptom:** of 26 desk photos, 9 were flattened to part of the page: a hand's, a phone's or a lamp's shadow over
  the right side or the bottom made that part as dark as the desk, and the page was cut off there (the totals column
  gone: subtotal, taxes and total *missing*), or squared up on a wrong four-cornered outline (h-0086, h-0127: the page
  warped). Photos read 75.4% of their fields right, 46% of invoices fully right.
- **Cause:** the page was told from the desk by one brightness threshold (Otsu) over the whole photo: paper in shadow
  falls below it.
- **Fix:** the paper in the light is only the seed. The photo is flooded from that seed and from the picture's border
  at once (OpenCV's watershed): the two floods meet where the colour changes sharply, the page's edge, and not at a
  shadow's soft edge. The checks that tell a desk photo from a scan are unchanged (the page must not touch the
  picture's edge, the desk must be darker than the paper): on the corpus's 93 scanned, faxed and speckled pictures,
  none is flattened, as before. It costs a few milliseconds.
- **Measured:** photos 75.4% → 96.2% of fields right, 46.2% → 80.8% fully right, no silent error 80.8% → 92.3%
  (all 26 pages now whole). What is left on photos is the rule reader's (labels under values, credits printed
  positive).
- **Test:** `tests/test_photo_scan_quality.py::test_a_shadow_across_the_page_does_not_cut_the_page_short`
- **Lesson:** Don't find an object by its brightness when the light is uneven: find its edge.

#### B-301 · A page scanned upside down was read upside down when too few amounts stood beside their labels · high
- **Where:** `ap_coder/capture/layout.py` (`_upright_turn`, new `_letters_upright`)
- **Symptom:** of 12 invoices scanned upside down (14 pages turned), 7 pages were read as they lay: a credit note whose
  amounts read "$326.64CR" (h-0064: nothing found but the currency), a 3-page credit (h-0044: the vendor read as
  "Monthly maintenance contract", the totals' signs lost, *verified*) and a 2-page long table whose first page alone
  was turned (h-0123: the vendor read as "Continued.", *likely*).
- **Cause:** a page was turned only when at least two amounts stood left of their labels (`_label_side` ≤ −2). An
  amount ending in "CR", or a label sharing its line with its amount, does not count, so a page with few such rows
  scored −1 or 0 and was left as it was.
- **Fix:** the letters say which way up a page is. In each OCR line of five characters or more, Latin capitals,
  figures and tall letters rise above the small letters' band far more than g, j, p, q, y hang below it; on a page
  on its head it is the other way round. A page is turned when at least 8 lines stand on their heads and three
  times as many as stand upright (a line of capitals alone says nothing); a page whose letters clearly stand upright
  is never turned; otherwise the labels' side decides, as before. Measured on the corpus's 66 upright scanned pages
  (scans, faxes, speckled, faint) and 26 photos: at worst 7 lines stood on their heads against 14 upright; the 14
  turned pages: at least 18 on their heads, at most a third as many upright. A few milliseconds a page.
- **Measured:** upside down 83.5% → 95.0% of fields right, 58.3% → 75.0% fully right, no silent error 75.0% →
  100%. bench (60, seed 3, half scans) 99.8%, verified 100%.
- **Test:** `tests/test_photo_scan_quality.py::test_a_page_scanned_upside_down_is_known_by_its_letters`
- **Lesson:** Don't infer a page's orientation from the layout alone (it varies from invoice to invoice): the
  print's own shape says it on every line.

#### B-302 · On a scan fed askew, an amount at the right edge was read with the label of the row above · high
- **Where:** `ap_coder/capture/__init__.py` (new `_read_straight`, used by `analyze` for the rule reader on the
  first and the second OCR read); `ap_coder/capture/layout.py` (`_skew`)
- **Symptom:** of 22 scans tilted 2 to 5 degrees, 11 had a value wrong or missing. On a landscape page tilted 4.2
  degrees (h-0085) the subtotal was read as 99.52 (the GST), the GST as 139.33, the PST as 2,229.33 (the total); on
  others the invoice number took the PO's line, the due date the invoice date's. No silent error on 77.3% of scans.
- **Cause:** the header fields were read on the page as tilted: a label and its amount at the other end of the row
  lie a row apart on a page tilted 4 degrees. Only the line items were read straightened (`reader.straightened`),
  and a tilt over 4 degrees was not measured at all (taken for a page turned).
- **Fix:** the rule reader reads a tilted scan straightened too: each line (words set close together on one
  baseline) is moved up or down as one piece to where it would be on a straight page, the lines are formed again,
  and each reading's boxes are put back where the words are printed, so the highlight still falls on the scan as
  shown. A line is moved whole, not word by word: moved word by word, a title's words came apart ("Liberty" lost
  from "Liberty Consulting Group Co."). A tilt of up to 8 degrees is measured (it was 4; `MAX_TILT`, as B-345 also found).
- **Measured:** scans 85.8% → 93.8% of fields right, 50.0% → 72.7% fully right, no silent error 77.3% → 95.5%;
  faxes, speckled, faint and photos unchanged; bench (60, seed 3, half scans) 99.8%, verified 100%. Left: on a 5
  degree scan OCR itself joins two neighbouring lines' words ("1880 Industrial Pkwy Kingsway Mechanical Limited"),
  so the vendor was taken from a salesperson's name (*likely*, h-0008).
- **Test:** `tests/test_photo_scan_quality.py::test_a_scan_fed_askew_reads_each_label_with_its_own_amount`
- **Lesson:** Don't straighten only part of the reading: every reader that pairs things across a row needs it.

#### B-303 · On a page scanned at 5 degrees, a long line's box covered the line under it: the two were read as one · medium
- **Where:** `ap_coder/capture/layout.py` (`ocr_image`, `_split_ocr_line`, new `_edges`, `TIGHT_FROM`);
  `ap_coder/capture/__init__.py` (`_read_straight`)
- **Symptom:** after B-302, a scan tilted 5 degrees (h-0008) still read its vendor as "K. Singh" (*likely*, a
  salesperson): its name in large print and the address under it were one line, "1880 Industrial Pkwy Kingsway
  Mechanical Limited"; on others a due date or the terms were missed for the same reason.
- **Cause:** an OCR line's words were given the box around the whole line. On a tilted page that box is as tall as
  the print plus the tilt over the line's length (a 600-pixel line at 5 degrees: 50 pixels more), and it reached
  over the next line down.
- **Fix:** on a page tilted more than about 1.7 degrees, each word's box follows the OCR line's top and bottom edges
  where the word stands (the line's four corners), so it is as high as the print there. The reader then moves each
  word on its own to the straightened page (B-302 moves whole lines, right while a word's box was its line's). Pages
  tilted less, as the benchmark's scans (at most 1.5 degrees), are read as before.
- **Measured:** scans 93.8% → 95.1% of fields right, 72.7% → 81.8% fully right, no silent error 95.5% → 100%; other
  pictures unchanged; bench (60, seed 3, half scans) 99.8%, verified 100%. On such a page the lines as formed on the
  tilted page (not read by the rule reader) may now break a long line in two; the rule reader and the line items read
  the page straightened.
- **Test:** `tests/test_photo_scan_quality.py::test_on_a_steeply_tilted_scan_two_lines_close_together_stay_apart`
- **Lesson:** Don't give a part the box of its whole when the whole is tilted: the box grows with the tilt.

#### Left as they are (photos and poor scans)

- **N-98** · Softening a black-and-white page before OCR (a 3 × 3 blur: a fax's strokes are jagged and broken, and the
  OCR models were trained on grey print) made the first engine read 87.0% of the values printed on 16 faxes against
  83.9%, but no more fields came out right; softened for both engines, both read a fax's "Solutions" as
  "Solutlons" and agreed on it (a vendor name shown as *verified* wrongly). Not done. Faxes stay at 80.5% of fields
  right, 25% fully right: most of what is left is the rule reader's (labels under their values, credits printed
  positive) and OCR's broken characters on 100-dpi rows.
- **N-99** · On a real photo of a German invoice tilted 3 degrees (`drv_de_hetzner_photo`), the logo "HETZNER" and
  the small "ONLINE" under it were one line only because their boxes happened to overlap; read straightened
  (B-303) they are two, and the vendor became the customer's name (*likely*). A logo in two OCR lines is a vendor
  reading question (the logo's parts are not joined), left for the rule reader.
- **N-100** · A page turned upside down is now read upright by its letters (B-301), also the real
  `drv_ca_yorku_upsidedown` (York University's annotated sample invoice, turned): upright, its side panel's
  "2. Vendor's FULL LEGAL NAME" is read as the vendor (*check*) and the invoice number is not found, which the
  page read as it lay happened to get right. Reading an annotated explainer is not an invoice question: left.
- **N-101** · Speckled scans (12) read 85.6% of fields right, as before: a median filter removed specks but also
  thin strokes (75.3% of printed values read, against 79.0%), and a blur did nothing. Left.

### Reading: dates and invoice numbers

Measured on a corpus of 135 public sample invoices and receipts with known answers (German ZUGFeRD samples, French,
Dutch and English invoices, scanned and photographed receipts), read with OCR only where there is no text layer, the
table finder's model on. None of the corpus is in the repo: each test makes its own page. Most of what was missing
was printed in ways the labels did not know: a German invoice's title that carries its number and date ("Rechnung
Nr. 471102 vom 05.03.2018"), "Facture n° 562044 du 2 juillet 2015", "Beleg-Nr | : RK21012345", other languages'
month names, a receipt's "Receipt Number" and its unlabelled date and time ("6/10/2019 13:59", which OCR often runs
together: "27Mar201818:46"), a header row printed across the page ("N° Facture ........ INV-81250"), a date alone beside
the title ("Beitragsrechnung | 24. Januar 2014"), a page number run into the label. Those are read now. What is read
without a label of its own (`number-dated`, `date-time`, `title-dated`, `label-far-right`) is weaker than a labelled
value, so a labelled date or number comes first, and is measured apart: never verified on a labelled value's record.
A date with no label at all (`date-time`, `title-dated`) is held to *check* however many readers see it, and a stamp
along the page's top edge (a fax's "SEP-13-2025 11:26 FROM: ...", a printer's) is not taken for the invoice's date.
**Measured:** real corpus, invoice date right 57 → 109 of 120 (wrong 2 → 0, missing 61 → 11), invoice number right
54 → 89 of 107 (wrong 8 → 0, missing 45 → 18), none verified wrong; the other fields unchanged. Hard corpus (150) 96.9%
of fields right as before, verified precision 100%; bench (60, seed 3, half scans) 99.8%, verified 100%. What is
still missing is mostly OCR's (a slash or a letter lost: "Date demission:332024", "Recelpt No.", "Bjll#"), labels
run into a heading row ("Termini di Pagamento Data Documento Numero Documento"), a restaurant's "Check: 37820" (a
check number is also how a cheque paid is printed: not read as the invoice's number), and an insurer's policy number
taken as the invoice number by the sample's own answers.

#### B-353 · A credit note's own number was not read: the invoice it credits was taken for it · medium
- **Where:** `ap_coder/capture/reader.py` (`MORE_LABELS["invoice_number"]`, `DISTRACTORS["invoice_number"]`, `_hits_at`)
- **Symptom:** a French credit note ("Avoir AV-2017-0005", with "Facture liée FA-2017-0010") came back with the
  invoice number FA-2017-0010, *likely*: posted, the credit would carry the number of the invoice it credits. Its scan,
  where OCR glued the title to its number ("AvoirAV-2017-0005"), gave FA-2017-0010 too (*check*).
- **Cause:** "Avoir" (a credit note, in France) was no label; "Facture liée" ("linked invoice") read as "Facture",
  the invoice number's weakest label. A label glued to the letters of its value was never a label.
- **Reproduce:** a PDF with "Avoir AV-2017-0005" as its title and "Facture liée | FA-2017-0010" in its header grid.
- **Reproduced:** yes (`invoice_number FA-2017-0010`)
- **Fix:** "Avoir" followed by a number, and "N° d'avoir", are a credit note's number labels (as "Note de crédit
  051527-CR" is); "Facture liée / associée / initiale / concernée / corrigée / de référence" and "Ursprungsbeleg" /
  "Bezugsrechnung" are the credited invoice, not this one's number. A label OCR glued to its number is read when the
  number starts with capitals and a digit follows ("AvoirAV-2017-0005").
- **Test:** `tests/test_dates_numbers_sweep4.py::test_a_credit_notes_own_number_not_the_invoice_it_credits`
- **Lesson:** A credit note names two documents; the one it is, and the one it credits. Know the words for both.

#### B-354 · A US customer's invoice dated 9/10/2020 was shown as October 9 · low
- **Where:** `ap_coder/capture/reader.py` (`_read_fields_on`)
- **Symptom:** a statement to a customer in Walnut, WY ("Date: 9/10/2020", nothing Canadian on it) and a synthetic
  invoice between two US addresses ("Date of issue: 05/11/2021") came back as 2020-10-09 and 2021-11-05 (*check*):
  the person checking was shown the wrong day.
- **Cause:** with no date on the page over 12 and no due date or terms to settle the order, a date that reads both
  ways was shown day first. The supplier's US address turns the order to month first, but only when the reader takes
  that address for the supplier's (outside the customer's block), and "WY, 98432" (a comma before the ZIP code) was no
  US address at all.
- **Reproduce:** a PDF with "Date: 9/10/2020", "Sold To ... Walnut, WY, 98432", no GST number, no postal code.
- **Reproduced:** yes (`invoice_date 2020-10-09 check`)
- **Fix:** when nothing settles the order and the first page prints a US address anywhere (and nothing Canadian),
  the date is shown month first, still marked as reading both ways: confidence at most 60%, a person checks it.
- **Test:** `tests/test_dates_numbers_sweep4.py::test_a_us_customers_invoice_shows_an_unsettled_date_month_first_for_a_person_to_check`
- **Lesson:** When a value must be guessed, guess with what the page says; and keep it marked a guess.

#### B-355 · A German supplier's 05.03.2018 was read as May 3: "DE 80333" read as a Delaware ZIP code · medium
- **Where:** `ap_coder/capture/reader.py` (`_read_fields_on`, new `_dotted_dates`)
- **Symptom:** a German invoice ("Lieferant GmbH, DE 80333 München", "Rechnungsdatum: 05.03.2018") came back dated
  2018-05-03 with no mark that the date reads both ways (so not held to *check* for that). On the real corpus it
  showed once German dates were read (this section): two invoices dated 2018-03-05 and 2018-08-06 came back May 3
  and June 8.
- **Cause:** a supplier with a US address prints dates month first; "DE 80333 München" (the country code before a
  German postcode) matched a US state and ZIP code, and the same followed for an invoice in US dollars.
- **Reproduce:** a PDF with "DE 80333 München" at the top and "Rechnungsdatum: 05.03.2018".
- **Reproduced:** yes (`invoice_date 2018-05-03`)
- **Fix:** a page whose numeric dates are mostly printed with points (05.03.2018) is not turned to month first by a
  US address or US dollars: the US does not print dates so, Europe does, day first.
- **Test:** `tests/test_dates_numbers_sweep4.py::test_a_date_printed_with_points_is_not_turned_month_first_by_a_german_postcode`
- **Lesson:** A country guessed from an address needs a second sign; the way the page prints its dates is one.

#### B-356 · A receipt's "02/15/10 11:10AM" was dated 1011-02-15 · low
- **Where:** `ap_coder/capture/normalize.py` (`_make`, `parse_dates`)
- **Symptom:** a scanned receipt's date and time, which OCR read without the space ("02/15/1011:10AM"), came back as
  the invoice date 1011-02-15 (*check*).
- **Cause:** a four-digit year was taken whatever it was.
- **Reproduce:** `parse_date("02/15/1011:10AM")`
- **Reproduced:** yes (`1011-02-15`)
- **Fix:** a year outside 1900-2099 is no date; a four-digit "year" run into a time ("1011:10") is the two-digit
  year and the hour.
- **Test:** `tests/test_dates_numbers_sweep4.py::test_a_year_run_into_the_time_is_not_read_as_the_year`
- **Lesson:** Bound what a value can be: no invoice is dated a thousand years ago.

#### B-357 · An invoice number OCR ran into the next word was read with it: "1128461Cashier" · low
- **Where:** `ap_coder/capture/reader.py` (`_value_reading`, new `_unglue_next_word`)
- **Symptom:** a scanned receipt's "INV No.: 1128461 Cashier: Thandar", which OCR read without the space, came back
  with the invoice number "1128461Cashier" (*check*).
- **Cause:** a number is read up to the next space; OCR left none.
- **Reproduce:** an OCR line "INV No.:1128461Cashier:Thandar".
- **Reproduced:** yes (`invoice_number 1128461Cashier`)
- **Fix:** on OCR's words, a number followed by a capitalised word of four letters or more ends before the word.
- **Test:** `tests/test_dates_numbers_sweep4.py::test_an_invoice_number_run_into_the_next_word_is_cut_at_the_word`
- **Lesson:** OCR's lost spaces join a value to its neighbour on either side; undo both.

#### B-358 · A receipt's item number and description were read as the invoice number: "15.Dumplings" · low
- **Where:** `ap_coder/capture/reader.py` (`_value_reading`)
- **Symptom:** a restaurant receipt's first item, "15.Dumplings au porc et nappa", printed a few lines under the
  guest's "#1", came back as the invoice number "15.Dumplings" (*check*).
- **Cause:** the value under a label is read as a number when it has a digit; an item's number run into its name
  ("15." then "Dumplings") has one.
- **Reproduce:** OCR lines "#1", "2015-08-23 20:45:57 Waiter", "15.Dumplings au porc et nappa | $7.95", close
  together; or "Bill No." over "15.Dumplings au porc et nappa".
- **Reproduced:** yes (`invoice_number 15.Dumplings label-below`)
- **Fix:** a short number with a point or bracket run into a word ("15.Dumplings", "3)Coffee") is no invoice or PO
  number.
- **Test:** `tests/test_dates_numbers_sweep4.py::test_a_receipts_item_number_is_not_its_number`
- **Lesson:** A number that starts a line of words is the line's number, not the document's.

#### B-359 · A label whose own value is no number took the value of a line under it: a postal code · low
- **Where:** `ap_coder/capture/reader.py` (`_labelled`, new `_OWN_VALUE`)
- **Symptom:** a receipt's address "GROUND FLOOR, NO.4 &6," gave the invoice number 47500 (*check*): the postal
  code two lines down ("47500 SUBANG JAYA").
- **Cause:** "No." followed by a value on its own line ("4", too short to be a number) was treated as a label with
  nothing after it, whose value is looked for to the right and below.
- **Reproduce:** OCR lines "GROUND FLOOR, NO.4 &6,", "JALAN SS 15/4B,", "47500 SUBANG JAYA, SELANGOR".
- **Reproduced:** yes (`invoice_number 47500 label-below`)
- **Fix:** an invoice-number or PO label followed on its line by a value of its own that is not a number gives
  nothing; its value is not looked for elsewhere.
- **Test:** `tests/test_dates_numbers_sweep4.py::test_a_label_whose_value_is_printed_after_it_does_not_take_the_next_lines`
- **Lesson:** A label that has a value beside it, however bad, does not own the next line's.

#### B-360 · "InvoiceINV/2023/03/0008" not read: the customer's reference was taken for the invoice number · medium
- **Where:** `ap_coder/capture/reader.py` (`_GLUED_TITLE`, `_GLUED_NUMBER`)
- **Symptom:** a scan of an invoice titled "Invoice INV/2023/03/0008" came back with the invoice number CUSTREF123
  (*check*), read under "Reference:" (the customer's reference).
- **Cause:** the second OCR read the title glued to its number ("InvoiceINV/2023/03/0008"); a glued title was read
  only when its number had a hyphen or none after its letters, not a slash. Only the first read had the number, under
  the weak "Invoice" label, and the two reads agreed on the reference.
- **Reproduce:** OCR lines "InvoiceINV/2023/03/0008", "Reference:" over "CUSTREF123".
- **Reproduced:** yes (`invoice_number CUSTREF123`)
- **Fix:** a number glued to its title may have a slash after its letters ("INV/2023/03/0008"),
  and a label glued to such a number is read too.
- **Test:** `tests/test_dates_numbers_sweep4.py::test_a_title_glued_to_a_number_with_slashes_beats_a_customer_reference`
- **Lesson:** Numbers are written with slashes as often as with hyphens; a pattern for one is a pattern for both.

An independent review of the work above, on ordinary Canadian invoice layouts (one-page PDFs: a supplier in Toronto,
Subtotal / HST / Total), found the new readings wrong where an invoice prints a second date or number they were not
made for: B-361 to B-367. Each was fixed with its test first.

#### B-361 · "Receipt Date" and "Receipt No." outranked the invoice's own date and number, likely · high
- **Where:** `ap_coder/capture/reader.py` (new `WEAK_LABELS`, `_LabelHit.fallback`, `_read_fields`)
- **Symptom:** an invoice with "Date: 2026-10-01" and "Receipt Date: 2026-10-05" came back dated 2026-10-05; one
  titled "INVOICE / No. 20417" with a payment's "Receipt No: 556677" lower down came back with the number 556677.
  Both *likely*.
- **Cause:** the receipt labels added for receipts (B-353's section) were as strong as an invoice's own labels, and
  their values were measured on the record of "label-inline", a pattern right every time on the benchmark.
- **Reproduce:** those two PDFs, `analyze(ocr=False)`.
- **Reproduced:** yes (`invoice_date 2026-10-05 likely`, `invoice_number 556677 likely`)
- **Fix:** "Receipt Date", "Receipt No./#/ID" and "Folio No." are fallback labels: read only when no other label for
  the field reads a value on the document. On a Canadian invoice "Receipt Date" is the day the goods came in.
- **Test:** `tests/test_dates_numbers_sweep4.py::test_a_receipt_date_is_not_the_invoice_date_when_the_invoice_prints_its_date`, `tests/test_dates_numbers_sweep4.py::test_a_receipt_number_is_not_the_invoice_number_beside_the_invoices_own`
- **Lesson:** A label made for one kind of document means something else on another; let it speak only when nothing
  else does.

#### B-362 · A customer's "Numéro : C-4471" was read as the invoice number, likely · high
- **Where:** `ap_coder/capture/reader.py` (`WEAK_LABELS`, new `_customer_line`, `_read_fields`)
- **Symptom:** a Quebec "FACTURE" with "Client / Numéro : C-4471" came back with the invoice number C-4471, *likely*.
- **Cause:** "Numéro :" (added for a Spanish invoice's "NUMERO:") fired under the customer's heading, and a value
  read after any label borrowed the measured record of "label-inline", so a weak label read as sure as a strong one.
- **Reproduce:** a PDF with "FACTURE", "Client" over "Numéro : C-4471", "Date : 2026-10-01".
- **Reproduced:** yes (`invoice_number C-4471 likely`)
- **Fix:** the weak labels ("Nr.", "Numéro :", "Factuur", "Fecha", the receipt and folio labels) give values of their
  own method ("weak-label-..."), with no measured record: on a text PDF they are *check*. An invoice-number weak label
  in the customer's block (a bill-to region, or a client / customer / compte / account heading just above it) is no
  label.
- **Test:** `tests/test_dates_numbers_sweep4.py::test_a_customers_numero_is_not_the_invoice_number`, `tests/test_dates_numbers_sweep4.py::test_a_weak_label_is_never_likely_on_a_real_labels_record`
- **Lesson:** A new label must not inherit an old label's record; measure it apart (D-22, D-44).

#### B-363 · A bare "INVOICE" title read a GST number or a customer number across the page as the invoice number · medium
- **Where:** `ap_coder/capture/reader.py` (`_far_right_header`)
- **Symptom:** "INVOICE" at the left margin with "R123456789" (a GST number) or "C-10045" alone at the right, and
  "No. 20417" under the title: the invoice number came back R123456789 or C-10045 (*check*).
- **Cause:** the far-right reading took any label with nothing after it, the title's weak "Invoice" too.
- **Reproduce:** those PDFs.
- **Reproduced:** yes (`invoice_number R123456789 check`)
- **Fix:** only a real label (strength 0.55 and up, not a weak one) reads across the page; a value shaped like a
  GST/HST, business or QST number is never an invoice or PO number there; and only the invoice number is read so (a
  date or PO number alone across the page may be the due date or an account number).
- **Test:** `tests/test_dates_numbers_sweep4.py::test_a_bare_title_does_not_read_a_value_across_the_page`, `tests/test_dates_numbers_sweep4.py::test_a_header_row_printed_across_the_page_reads_its_value_at_the_right_margin`
- **Lesson:** A title is no label; reading far from a label needs a label that names its field.

#### B-364 · A date with no label of its own outranked the labelled "Date: 03/04/2026" · medium
- **Where:** `ap_coder/capture/reader.py` (`_read_fields`, `_stamped_dates`, new `_NOT_ISSUED`)
- **Symptom:** a service call's "Time in: 2026-03-02 08:15" or a "Payment received 2026-04-15 14:22" line was the
  invoice date over "Date: 03/04/2026"; a credit note's body "Invoice # 20417 dated 2026-08-15" over its "Date:
  2026-10-01"; with no labelled date, "Completed 2026-03-02 16:15" was taken (*check*).
- **Cause:** an unlabelled reading (0.45, 0.5) scored above a labelled date that reads both ways (0.33).
- **Reproduce:** those PDFs.
- **Reproduced:** yes (`invoice_date 2026-03-02`, `2026-04-15`, `2026-08-15`)
- **Fix:** a date read with no label of its own (`number-dated`, `date-time`, `title-dated`) scores at most half the
  weakest labelled date on the document; a dated line that says what else happened (received, paid, payment,
  completed, time in / out, arrival, call, delivered, shipped, period, ending, from / to ...) is no stamp of the
  invoice.
- **Test:** `tests/test_dates_numbers_sweep4.py::test_an_unlabelled_date_never_outranks_a_labelled_one`
- **Lesson:** A fallback must lose to every labelled value, however weak; rank it so, not by its own score.

#### B-365 · "Invoice Due Date | Nov 1, 2026" was read as the invoice date beside the title · medium
- **Where:** `ap_coder/capture/reader.py` (`_DOC_TITLE`, `_title_dates`)
- **Symptom:** the due date came back as the invoice date (*check*); "Invoice Period Ending | Sep 30, 2026" likewise.
- **Cause:** any line starting with "Invoice" was taken for the title.
- **Reproduce:** a PDF with "Invoice Due Date" and "Nov 1, 2026" on one row, "Invoice # 20417" under it.
- **Reproduced:** yes (`invoice_date 2026-11-01`)
- **Fix:** a title is not followed by due, period, total, amount, date, balance, number, ref or to; and its line
  says nothing of another event (B-364).
- **Test:** `tests/test_dates_numbers_sweep4.py::test_a_label_starting_with_invoice_is_not_the_title`
- **Lesson:** "Invoice" starts a dozen labels; a title is the word alone.

#### B-366 · A Canadian invoice shipping to the US had its 03/04/2026 shown month first · low
- **Where:** `ap_coder/capture/reader.py` (`_read_fields_on`, new `_us_page`)
- **Symptom:** a Toronto supplier whose address is in the footer, shipping to Buffalo, NY (and one remitting to a
  Pittsburgh PO box), came back dated 2026-03-04 (*check*) for "Date: 03/04/2026", which it means as April 3.
- **Cause:** B-354's rule counted only the supplier's block (top of the page) and a valid GST number as Canadian.
- **Reproduce:** that PDF.
- **Reproduced:** yes (`invoice_date 2026-03-04 check`)
- **Fix:** a Canadian postal code or province, or a Canadian tax (GST, HST, PST, QST, TPS, TVQ) anywhere on the page
  is something Canadian; and "CA 90210" alone is no US address (CA may be Canada).
- **Test:** `tests/test_dates_numbers_sweep4.py::test_a_canadian_page_with_a_us_address_keeps_its_date_order`
- **Lesson:** Ask the whole page where it is from, not the corner the reader takes for the supplier's.

#### B-367 · Dates pulled out of references, and "Marco" read as March · low
- **Where:** `ap_coder/capture/normalize.py` (`parse_dates`, `MONTHS`)
- **Symptom:** "Ref 45/13/12/2026" gave 2026-12-13, and "Marco 12, 2026" (a name) gave 2026-03-12.
- **Cause:** after a run of numbers that is no date, the next date was looked for one character on, inside the same
  run (made for "REG#19-21/05/2018"); and the Portuguese "março" (accents off: "marco") was a month word.
- **Reproduce:** `parse_date("Ref 45/13/12/2026")`, `parse_date("Marco 12, 2026")`
- **Reproduced:** yes (`2026-12-13`, `2026-03-12`)
- **Fix:** a date is looked for again only from the next word; "marco" is March only in "12 de março de 2020".
- **Test:** `tests/test_dates_numbers_sweep4.py::test_a_date_after_a_failed_one_is_read_and_a_time_may_be_the_next_piece_of_the_row`, `tests/test_dates_numbers_sweep4.py::test_a_name_like_a_month_is_not_a_month`
- **Lesson:** A pattern loosened for one OCR slip finds dates in every reference; loosen it only where words part.

#### B-368 · On a scan, a weak label's value was likely when the two OCR reads agreed · medium
- **Where:** `ap_coder/capture/confidence.py` (`fuse`, new `WEAK_LABEL_METHOD`)
- **Symptom:** a scanned receipt's "Receipt No: 556677" came back *likely* (0.97), "Receipt Date: 2026-10-05"
  *likely* (0.98) and "Numéro : 4471" *likely* (0.91), against D-44 (B-362 made them check on text PDFs only).
- **Cause:** B-362 gave the weak labels' values a method of their own with no measured record, so their confidence was
  the readers' combined score; two OCR reads agreeing lift that past "likely".
- **Reproduce:** a scan layout read by "rules" and "ocr2" alike, `fuse(..., layout_source="ocr")`.
- **Reproduced:** yes (`556677 likely 0.97`)
- **Fix:** a value read only after weak labels (`weak-...`) is held to check however many readers agree, with the
  reason: the word also names other numbers or dates.
- **Test:** `tests/test_dates_numbers_sweep4.py::test_a_weak_label_stays_checked_however_many_readers_agree`
- **Lesson:** Two readers agreeing confirms what is printed, not what it is; a cap on meaning must not depend on the
  number of readers.

#### B-369 · A work order's time line or a report's footer stamp was read as the invoice date · low
- **Where:** `ap_coder/capture/reader.py` (`_NOT_ISSUED`, `_stamped_dates`)
- **Symptom:** "2026-03-01 09:12 Labour - J. Smith" (a technician's time on a work order) and "Generated 2026-10-20
  14:33 by SAP" at the foot of the page came back as the invoice date (*check*), on an invoice that prints no date
  label.
- **Cause:** a date with a time, unlabelled, is a receipt's stamp; a stamp printed for another reason was not told
  apart unless it said received, paid, delivered and the like (B-364), and only the page's top edge was skipped.
- **Reproduce:** OCR lines "Invoice # 20417" and "2026-03-01 09:12 Labour - J. Smith".
- **Reproduced:** yes (`invoice_date 2026-03-01 date-time`)
- **Fix:** labour (main-d'oeuvre), generated (généré), created (créé), run, report (rapport), log (journal) and printed
  (imprimé) mark a line as another event's; a stamp along the page's bottom edge is a printer's or a report's, like
  one along its top.
- **Test:** `tests/test_dates_numbers_sweep4.py::test_a_work_log_or_report_stamp_is_not_the_invoice_date`
- **Lesson:** An unlabelled date is the issue date only when nothing on its line says what else it dates.

#### Left as they are (dates and invoice numbers)

- **N-102** · A Canadian-looking supplier (a ".ca" address, "Ltée" or "Inc.") with no postal code, province or
  Canadian tax on the page, whose page prints a US address: an unsettled date (03/04/2026) is shown month first
  (B-354), still *check*. Those signs are weak (US companies use them too) and the date is checked either way: left.
- **N-103** · "Invoice # 20417" (the invoice a credit note credits, printed with no "Re:" or "Original") beside
  "Credit Memo # CM-1029" gives 20417, *likely*. Already so before this work: "Invoice #" is a stronger label than a
  credit memo's, and nothing on that line says it is the credited invoice. Telling them apart needs the document's
  title weighed against the labels; left for the credit-note work.
- **N-104** · "Account No: 55123   Invoice No: 20417" on one line gives no invoice number. Already so before this work:
  the line comes as one run of words, and a label after another label's value on its line is read only when it is a
  bilingual one ("Issued / Date de facturation"). Reading any strong label there risks a total's or a note's words: left.
- **N-105** · "Invoice Date: Oct 1/26" (month, day and a two-digit year after a slash) is not read. Read as a date,
  "Mar 3/4" or "Unit Oct 1/26" would give years like 2004 from a fraction; left until a real invoice shows the form.
- **N-106** · A US supplier (WA 98101 in its letterhead) with an ambiguous date gets it month first and *likely*,
  through the older supplier-address rule (`_us_vendor`, before this work), not B-354's (which is checked). A US
  supplier prints month first nearly always; left as it was.

### Reading: terms, PO numbers and due dates

Found on the 135 real invoices and receipts of sweep 4 (Canada Post, York University's sample, Azure's and invoice2data's
samples, French Factur-X and German ZUGFeRD invoices): payment terms were right on 4 of 36 that print them, PO numbers
on 5 of 21, due dates on 32 of 47. Each fix has a small generated fixture in `tests/test_terms_po_sweep4.py` (the
corpus is not in the repo). B- numbers start at B-372 and N- numbers at N-121, apart from the numbers another sweep
4 change takes at the same time.

Measured after B-372 to B-375 (rule reader and OCR, table finder on; right / wrong / missing / printed but not in the
corpus's answers, wrong and verified): terms 4/0/32/2/0 → 13/10/13/4/0, the 10 "wrong" all the app's short form of
a German sentence the corpus writes out whole (N-121), so 23 of 36 read as the app reads them; PO numbers 5/0/16/1/0
→ 19/0/2/3/0; due dates 31/0/16/0/0 → 43/0/4/3/0; every other field unchanged. Hard corpus (150) and the benchmark
(200, seed 77, 60% scans) as before: no value wrong and verified.

#### B-372 · Terms whose meaning the short form dropped: "Net 30 EOM" read as "Net 30", "Net 2 weeks" as "Net 2" · high
- **Where:** `ap_coder/capture/reader.py` (`_terms`, new `_terms_form`, `_TERMS_WORDS`); `ap_coder/capture/confidence.py`
  (`run_checks`, new `_due_by_terms`)
- **Symptom:** The rule reader wrote the terms in a short form of the first number it found: "Net 30 EOM" and
  "30 jours fin de mois" became "Net 30", "Net 10th prox" "Net 10", "Net 1 month" "Net 1", "Net 2 weeks" "Net 2", a
  discount's days "2% 10 days" (or "2% discount if paid within 10 days") "Net 10", and "Net 45, 1% 15 days" lost its
  discount. The app counts the due date from the value read when none is printed: 15 to 30 days off, or one day
  for "Net 1". A due date printed with "Net 30 EOM" failed the reader's own check (invoice date + 30 days) and was
  marked down.
- **Cause:** `_terms` kept one number per pattern ("net N", "N days"); the app's own terms parser
  (`coding.terms.parse_terms`) reads end of month, prox, months, weeks, discounts and late-payment clauses, but the
  reader never asked it. The due-date check took the number after "net" too.
- **Reproduce:** `_terms("Net 30 EOM")`; a PDF with "Terms Net 30 EOM" and "Due Date" 45 days after the invoice date.
- **Reproduced:** yes (`Net 30`; the due date's reason "invoice date + 30 days ≠ due date")
- **Fix:** the reader puts the printed terms in the words `parse_terms` reads (OCR's specks and glued words, and
  other languages' days, discount and "at once": B-373), lets it read them, and writes what it read back in a short
  form it reads the same way ("Net 30 EOM", "Net 10 prox", "Net 1 month", "Net 14", "2% 10 days", "1% 15 Net 45",
  "Due on receipt"); the due-date check counts the due date the way the app does.
- **Test:** `tests/test_terms_po_sweep4.py::test_terms_are_read_in_the_form_the_app_counts_the_due_date_from`,
  `tests/test_terms_po_sweep4.py::test_eom_terms_on_an_invoice_are_kept_and_checked_against_the_due_date`
- **Lesson:** Don't keep two readers of the same text: the one that reads less hands the other a different value.

#### B-373 · Terms printed as a sentence, or under a Spanish, Italian, German or Dutch label, were not read · medium
- **Where:** `ap_coder/capture/reader.py` (`_terms`, new `_TERMS_WORDS`, `UNMEASURED_LABELS`, `_terms_sentences`);
  `ap_coder/capture/types.py` (new `UNMEASURED`); `ap_coder/capture/confidence.py` (`fuse`)
- **Symptom:** Canada Post prints its terms with no label, in the remittance slip: "15 days from date of invoice. Late
  payment fees at 18% per annum..." (French: "15 jours suivant la date de la facture."); York University's sample
  "Please remit payment upon receipt."; German invoices "Zahlungsbedingungen 14 Tage netto", "Zahlbar innerhalb 30
  Tagen netto bis 04.07.2018, 3% Skonto innerhalb 10 Tagen bis 15.06.2018", "Die Rechnung ist sofort fällig";
  Spanish "CONDICIÓN DE PAGO: 30 DÍAS FECHA FACTURA"; Italian "Termini di Pagamento" over "50 GG DF". All missing:
  the terms were right on 4 of the 36 invoices that print them.
- **Cause:** terms were read only after an English or French label ("Terms", "Conditions de paiement"), or from a
  note saying "payable on receipt"; days were only "days", "jours" or "j".
- **Reproduce:** a PDF with "15 days from date of invoice." in its footer and no terms label; `analyze()`.
- **Reproduced:** yes (`payment_terms` missing)
- **Fix:** labels "Condición de pago", "Termini/Condizioni di pagamento", "Zahlungsbedingungen/-ziel/-konditionen",
  "Betalingsvoorwaarden/-termijn"; days also "días", "giorni"/"gg", "Tage(n)", "dagen", a discount also "Skonto"
  (with its days before or after its rate), "zahlbar sofort"/"sofort fällig"/"paiement immédiat" due on receipt, and
  "DFFM"/"fine mese" end of month; days OCR glued to the next word ("15jourssuivant la datedelafacture") are
  read as days. With no labelled terms, a sentence that states them: one that starts with the
  days and counts them from the invoice's date, one that says the payment is due or payable within them, "remit
  payment upon receipt", German "zahlbar innerhalb", "Tage netto", "Skonto", "sofort fällig"; two sentences that
  state different terms give none. Days in a sentence about something else ("discrepancies must be communicated
  within 90 days of the invoice date", returns, objections, interest) are not terms. All of this is wording the
  benchmark never prints, so each reading is marked `UNMEASURED`: it keeps its own score (a sentence 0.6) and is
  never verified (D-45; on scans capped at check unless a check on other fields confirms it, B-384).
- **Test:** `tests/test_terms_po_sweep4.py::test_terms_printed_in_other_words_are_read_for_a_person_to_check`,
  `tests/test_terms_po_sweep4.py::test_days_in_a_note_that_is_not_the_terms_are_not_read_as_terms`
- **Lesson:** Don't wait for a label the supplier never prints: the terms are a sentence anyone can recognise, as
  long as the sentence is about paying.

#### B-374 · The customer's order number under "Your reference", a German label or mid-line was not read · medium
- **Where:** `ap_coder/capture/reader.py` (`UNMEASURED_LABELS`, new `_po_mentions`, `_po_as_your_reference`,
  `_after_another_label`)
- **Symptom:** PO numbers were right on 5 of the 21 real invoices that print one. French and Spanish Factur-X
  invoices give it as "Votre référence PO445" / "Your Reference COMPRA0832"; German ones as "Bestellung:
  B123456789", "Bestell-Nr : B123456789" (the colon set in the value's cell) or in a sentence, "Rechnung gemäß
  Bestellung Nr. 2013-471331 vom 01.03.2013"; York University's sample (a scan) after the customer's name, which OCR
  glued to it: "York UniversityPurchaseOrderNumber:PO12345". All missing.
- **Cause:** a PO label was looked for only at the start of a line or after a separator, in English and French; a
  value whose cell starts with a colon was taken for the value of a label on its own line ("Delivery Date: ...").
- **Reproduce:** a PDF with "Your Reference" beside "COMPRA0832", or "York University Purchase Order Number:
  PO12345" on one line; `analyze()`.
- **Reproduced:** yes (`po_number` missing)
- **Fix:** German labels "Bestellung", "Bestell-Nr", "Bestellnummer" (not "Bestellung vom", its date); with no PO
  label, a PO label with its number word inside a line on the first page ("Purchase Order Number:", "P.O. No.",
  "Bestellung Nr.", "Bon de commande n°"), then "Your reference" / "Votre référence"; either gives nothing when two
  different numbers are printed so (each line item's own order). A value cell holding only the colon of the label on
  its left is that label's value (for the PO, the due date and the terms). Each reading is marked `UNMEASURED`
  (D-45): check at best likely, never verified. "Your reference" is never read when a PO label is printed, so it does
  not cast doubt on the PO either. A "Purchase Order Number:" with blanks after it gives nothing, as before.
- **Test:** `tests/test_terms_po_sweep4.py::test_the_customers_order_number_under_other_labels_is_read_for_a_person_to_check`,
  `tests/test_terms_po_sweep4.py::test_no_order_number_is_made_up_from_a_label_without_one`,
  `tests/test_terms_po_sweep4.py::test_a_po_label_wins_over_your_reference`
- **Lesson:** Don't look for a label only where a line starts: OCR glues a label to the words before it, and a
  sentence names the order as clearly as a label does.

#### B-375 · Due dates under "Invoice Due Date", Dutch and German labels, or in a sentence were not read · medium
- **Where:** `ap_coder/capture/reader.py` (`UNMEASURED_LABELS`, new `_due_sentences`)
- **Symptom:** due dates were right on 32 of the 47 real invoices that print one. Missing: Azure's samples' grid
  heading "Invoice Due Date" (over "6/24/2017"), Amazon's "TOTAL AMOUNT DUE ON August 3 , 2014", Dutch "Vervaldag:
  03.04.2020", German "Fälligkeitstag: 01.04.2014", "Zahlungsziel 21.05.14", and the German sentences "Bitte
  überweisen Sie bis zum 09.06.2014" and "Zahlbar innerhalb 30 Tagen netto bis 04.04.2013, 3% Skonto innerhalb 10
  Tagen bis 15.03.2013".
- **Cause:** a due-date label was looked for at the start of a line ("Due Date", "Payment Due", "Fällig"), so
  "Invoice Due Date" and "Total amount due on" were not one, and a date was never read from a sentence.
- **Reproduce:** a PDF with "Invoice Number | Invoice Due Date" over "88120 | 10/15/2026"; `analyze()`.
- **Reproduced:** yes (`due_date` missing)
- **Fix:** labels "Invoice Due Date", "(Your) payment is due by/on", "(Total) amount/balance due on/by",
  "Vervaldag", "Fälligkeitstag", "Zahlungsziel" (only a date is read under it: it may give the terms instead); with
  no due-date label, a date right after "netto bis", "ohne Abzug bis", "überweisen Sie ... bis zum", "payment is due
  by", "please pay by/before", "payable avant le" (the net amount's date, never a discount's "Skonto ... bis"); two
  sentences that state different dates give none. Each reading is marked `UNMEASURED` (D-45; on scans capped at check unless a check on other fields confirms it, B-384). Where the terms are
  read too, the due-date check (B-372) can confirm both.
- **Test:** `tests/test_terms_po_sweep4.py::test_due_dates_under_other_labels_or_in_a_sentence_are_read_for_a_person_to_check`,
  `tests/test_terms_po_sweep4.py::test_a_date_in_a_note_that_is_not_about_the_due_date_is_not_read_as_it`
- **Lesson:** Don't take a label's words only as a line's first words: "Invoice Due Date" and "Total amount due on"
  carry the due date's label inside them.

#### B-376 · Terms read from the whole line: "Net 30 days. Statements are issued at the end of the month." as Net 30 EOM, "Due on receipt  Ship Via: UPS 2 Day" as Net 2 · high
- **Where:** `ap_coder/capture/reader.py` (`_terms`, new `_terms_phrase`, `_TERMS_NEXT_LABEL`, `_EOM_ATTACHED`, `_AT_ONCE`)
- **Symptom:** found by a review of B-372: the terms parser was handed everything after the terms label, so a month's
  end said of something else made the terms EOM ("Net 30 days. Statements are issued at the end of the month.",
  "Net 30   Statement date: end of month": *verified* Net 30 EOM on an invoice of Oct 31 due Nov 30), and days
  printed after due-on-receipt terms became their net days ("Due on receipt. Delivery within 2 weeks." Net 14,
  "Due on receipt  Ship Via: UPS 2 Day" and "Prepaid  Freight: 2 Day" Net 2, "Upon receipt   Lead time: 2 weeks"
  Net 14; "Due on receipt, 5% discount if paid in 7 days" 5% 7 days). The reader before B-372 got these right by
  taking the first pattern.
- **Cause:** B-372 parsed the whole rest of the line as one terms text.
- **Reproduce:** `_terms("Net 30 days. Statements are issued at the end of the month.")`; `_terms("Due on receipt  Ship
  Via: UPS 2 Day")`.
- **Reproduced:** yes (`Net 30 EOM`, `Net 2`)
- **Fix:** only the terms phrase is parsed: the text up to a sentence's end, a wide gap or another label ("Ship Via:",
  "Lead time", "Freight", "Delivery", "Statement", "FOB", "Rep", "Due Date", any "Word:" or "Two Words:"), with a
  discount stated in the next sentence; end of month counts only when said of the net days ("Net 30 EOM", "30 days
  end of month", "Net 30 days from end of month", "within 30 days of month end", "2/10 EOM", "Net EOM"); due on
  receipt (or COD, cash, immediate) with no net days printed stays due on receipt, whatever discount or days follow;
  prepaid with no net days gives no terms.
- **Test:** `tests/test_terms_po_sweep4.py::test_only_the_terms_phrase_is_read_as_the_terms`,
  `tests/test_terms_po_sweep4.py::test_a_terms_label_line_on_an_invoice_reads_only_its_terms`
- **Lesson:** Don't hand a parser more text than the value: it finds meaning in whatever it is given.

#### B-377 · A discount with net months or a net day of the next month: "1% 15 days, net 1 month" read as "1% 15 Net 1" · high
- **Where:** `ap_coder/capture/reader.py` (`_terms`, new `_RATE_THEN_MONTHS`, `_FOLLOWING`, `_AFTER_NET_NUMBER`)
- **Symptom:** "1% 15 days, net 1 month" and "2% 10 net 1 month" read as due 1 day after the invoice; "2% 10 days, Net
  10th prox" and "2/10 Net 25 prox" as Net 10 and Net 25 days (the 10th or 25th of the next month printed); "Net 15th
  following" as Net 15.
- **Cause:** OCR's run-together discount ("2%10,.Net45") was rewritten before parsing as "rate days net N" even when N
  was months or a day of the month; and the app's terms parser reads "2% 10 net 1 month" as 1 net day, and has no
  form for a discount in days with a net day of the next month.
- **Reproduce:** `_terms("1% 15 days, net 1 month")`, `_terms("2% 10 days, Net 10th prox")`.
- **Reproduced:** yes (`1% 15 Net 1`, `2% 10 Net 10` in the review's branch run)
- **Fix:** the run-together rewrite leaves a number followed by months, weeks, prox or st/nd/rd/th alone; a discount
  before net months is written "2% 10 days, Net 1 month"; "15th following (month)" is "15 prox"; a discount in days
  with prox net days is read "Net 10 prox" (the due date kept, the discount for a person to add: the app cannot hold
  both).
- **Test:** `tests/test_terms_po_sweep4.py::test_only_the_terms_phrase_is_read_as_the_terms`
- **Lesson:** Don't rewrite a number before you know its unit.

#### B-378 · A PO after "½", "™" or "…" on its line was cut short: "Bolts ½ in. per PO # 4500123456" read as "00123456" · high
- **Where:** `ap_coder/capture/reader.py` (new `plain_with_offsets`; `_po_mentions`, `_po_as_your_reference`,
  `_terms_sentences`, `_due_sentences`, `_label_hits`)
- **Symptom:** the new mid-line readers found their words in the line's plain text (accents and ligatures taken apart)
  and read the value from the line as printed at the same offset: "½" is three characters in plain text, so the PO
  lost its first two digits. The older label reader had the same slip after a label behind a separator ("Fittings ½
  in., PO # 4500123456" read 500123456).
- **Cause:** offsets in `plain(line.text)` used on `line.text`; `plain()` can lengthen the text.
- **Reproduce:** a PDF line "Bolts ½ in. per PO # 4500123456"; `analyze()`.
- **Reproduced:** yes (`po_number 00123456`)
- **Fix:** `plain_with_offsets` gives each plain character's place in the printed text; the readers map their offsets
  back before reading the value, and `_label_hits` maps its labels' ends the same way when the plain text is longer.
- **Test:** `tests/test_terms_po_sweep4.py::test_a_po_after_a_character_written_as_several_is_read_whole`,
  `tests/test_terms_po_sweep4.py::test_a_label_after_a_character_written_as_several_reads_its_whole_value`,
  `tests/test_terms_po_sweep4.py::test_a_terms_sentence_after_a_ligature_is_read`
- **Lesson:** Don't read from one string at an offset found in another, unless the two are the same length.

#### B-379 · Due-date sentences and labels took dates that are not the due date · medium
- **Where:** `ap_coder/capture/reader.py` (`_due_sentences`, new `_NOT_DUE`, `_value_reading`, `UNMEASURED_LABELS`)
- **Symptom:** "Amount due on account as of 2026-10-01: 0.00", "Amount due by October 25, 2026 with discount" (the
  discount's deadline), "Payment is due by the 15th. Next service 2026-12-01." (the next sentence's date), "Balance
  due on delivery October 30, 2026", "Balance due on or after 2026-11-14 is subject to 2% interest", and (an older
  label) "Please pay by October 25, 2026 to take the 2% discount" each gave a due date.
- **Cause:** the date was the first one within 30 characters, wherever the sentence ended and whatever it was said of.
- **Reproduce:** a PDF with "Payment is due by the 15th. Next service 2026-12-01." in its footer; `analyze()`.
- **Reproduced:** yes (`due_date 2026-12-01`)
- **Fix:** a due date is read only in the sentence of its words, right after them; a sentence that speaks of a
  balance as of a day, an account, a discount, a delivery, interest, "after", "through", "until", a quote or a
  service gives none; "Amount/Balance due on" is no label when delivery, account, "as of", a discount or "after"
  follows on its line; a due-date label's date is not read across a sentence's end or with a discount of its own.
- **Test:** `tests/test_terms_po_sweep4.py::test_a_date_that_is_not_the_due_date_in_a_due_sentence_is_not_read`,
  `tests/test_terms_po_sweep4.py::test_a_balance_due_on_delivery_label_is_not_a_due_date`
- **Lesson:** Don't take the nearest date: take the date the sentence is about.

#### B-380 · Terms sentences about a warranty, claims, returns or a holdback read as terms; a sentence's month end and discount lost · medium
- **Where:** `ap_coder/capture/reader.py` (`_terms_sentences`, new `_NOT_TERMS`, `_LATE_WORDS`, `_DISCOUNT_SENTENCE`)
- **Symptom:** "90 days from date of invoice: warranty on all workmanship." read as Net 90, "5 days after invoice: no
  claims ..." Net 5, "30 days from the invoice date for returns" Net 30, "Holdback of 10% is payable within 60 days of
  substantial completion" Net 60, "30 days from date of invoice, interest at 2% per month is charged" Net 30,
  "Payable within 10 days of statement date" Net 10; and "Payment is due within 30 days of month end." lost its end
  of month, "Payment due within 30 days. 2% discount if paid within 10 days." its discount.
- **Cause:** the sentence patterns looked only at the words around the days; "month end" is not a word the app's
  parser knows; each sentence was read alone.
- **Reproduce:** a PDF with "90 days from date of invoice: warranty on all workmanship." in its footer; `analyze()`.
- **Reproduced:** yes (`payment_terms Net 90`)
- **Fix:** a sentence that speaks of a warranty, guarantee, returns, claims, a holdback, completion, a dispute,
  discrepancies, restocking, a statement or validity is not the terms, nor one that starts with the days and speaks
  of interest or late charges; "month end" is read as EOM when said of the net days (B-376); a discount stated in the
  sentence before or after joins the terms; a deposit in the sentence marks the terms as the balance's (B-328).
- **Test:** `tests/test_terms_po_sweep4.py::test_a_days_sentence_about_warranty_claims_returns_or_holdback_is_not_the_terms`,
  `tests/test_terms_po_sweep4.py::test_a_terms_sentence_keeps_its_end_of_month_and_its_discount`
- **Lesson:** Don't recognise terms by their shape alone: a warranty is counted in days from the invoice date too.

#### B-381 · "Your ref: call 416-555-1212" read as the PO · low
- **Where:** `ap_coder/capture/reader.py` (new `_a_plain_po`; `_po_mentions`, `_po_as_your_reference`)
- **Symptom:** the customer's reference read as the PO was "call 416-555-1212".
- **Cause:** an identifier may join a short word to the number after it ("PO 4471"); the phone check saw the word too.
- **Reproduce:** a PDF line "Your ref: call 416-555-1212"; `analyze()`.
- **Reproduced:** yes (`po_number call 416-555-1212`)
- **Fix:** a PO read without a PO label is one token and not shaped like a phone number.
- **Test:** `tests/test_terms_po_sweep4.py::test_your_reference_that_is_a_phone_number_or_words_is_no_po`
- **Lesson:** Don't accept a value with words in it where a number is expected.

#### B-382 · EOM or prox terms matching the due date confirmed the invoice date · low
- **Where:** `ap_coder/capture/confidence.py` (`run_checks`, new `_counts_from_month`)
- **Symptom:** "Net 30 EOM" with a due date of Nov 30 confirmed an invoice date of Oct 3 as well as Oct 15: the check
  marked the invoice date confirmed (B-372 made it count EOM and prox terms).
- **Cause:** the due-date check confirmed all three fields; end-of-month and prox terms give the same due date from
  any day of the invoice's month.
- **Reproduce:** "Terms Net 30 EOM", "Due Date 2026-10-30" on an invoice of 2026-09-15; the invoice date's reasons.
- **Reproduced:** yes (`confirmed by due matches terms` on the invoice date)
- **Fix:** with EOM or prox terms the check confirms the due date and the terms only.
- **Test:** `tests/test_terms_po_sweep4.py::test_eom_terms_matching_the_due_date_do_not_confirm_the_invoice_date`
- **Lesson:** Don't let a check confirm a field it cannot tell apart from its neighbours.

#### B-383 · On scans, terms OCR ran together with the next label were read with that label's days: "Net30Freight:2Day" as Net 2 · high
- **Where:** `ap_coder/capture/reader.py` (`_terms_phrase`, new `_UNGLUE`, `_NET_ORDINAL_DAYS`, `_TERMS_NEXT_LABEL`,
  `_TERMS_WORDS`)
- **Symptom:** found by a second review on scans of B-376's cases: OCR joins a row's words, so the terms phrase had no
  space to end at. "Terms:Net30Freight:2Day" and "Net30ShipVia:UPS2Day" read as Net 2, "Net30Lead time2weeks" as Net
  14, "Net45Rep:12" as nothing, "Payablesurréception" and "Net30th" as nothing; "2%10 days,Net10thprox" and "Net 1Oth
  prox" (O for a zero) as 2% 10 Net 10 and 2% 10 Net 1; "EOM   Net 30" in one run as Due EOM.
- **Cause:** B-376 ended the phrase at a space before the next label; OCR leaves none. The "sur"/"à réception" words
  had to stand apart; an ordinal net ("Net 30th") was read only before "prox"; a wide gap ended the phrase even when
  the net days followed it.
- **Reproduce:** `_terms("Net30Freight:2Day")`; a scanned invoice with "Terms: Net 30 Freight: 2 Day" on one row.
- **Reproduced:** yes (`Net 2`)
- **Fix:** figures and letters run together are set apart before the phrase is cut ("Net 30 Freight: 2 Day", an
  ordinal's "th" kept with its number and set apart from "prox"), an O between figures or before "th" is a zero,
  "sur"/"des" glued to "réception" are read, "Net 30th" with no prox, month or following is Net 30, and a wide gap
  followed by the net days ("EOM   Net 30") does not end the phrase.
- **Test:** `tests/test_terms_po_sweep4.py::test_terms_ocr_ran_together_are_read_as_printed`
- **Lesson:** Don't rely on spaces in OCR text: a scan's row is one run of words.

#### B-384 · On scans, a value read only through unmeasured wording reached likely: two OCR reads counted as two readers · high
- **Where:** `ap_coder/capture/confidence.py` (`fuse`, new `UNMEASURED_CAP`)
- **Symptom:** "Your Reference: ACCT-1015" (a customer's account) read as the PO, *likely* 0.97, on a scan: the rule
  reader read it on both OCR passes, and two readers agreeing lifted it over the 0.85 of likely; B-373 to B-375's
  readings were meant to stay *check*.
- **Cause:** D-45's cap took the readers' own combined score, and two reads of the same pixels combine as two readers.
- **Reproduce:** `fuse({"rules": po, "ocr2": po}, ...)` with a "label-right+unmeasured" reading; its status.
- **Reproduced:** yes (`likely`, 0.97)
- **Fix:** a value read only through unmeasured wording is capped at 0.8 (*check*) however many OCR reads agree, as a
  value after a weak label (B-368); only a check on other fields (the due date = invoice date + the terms) lifts it,
  to likely at most, and then its reason says it was confirmed instead of "a person checks it".
- **Test:** `tests/test_terms_po_sweep4.py::test_two_ocr_reads_of_unmeasured_wording_stay_checked`,
  `tests/test_terms_po_sweep4.py::test_unmeasured_terms_confirmed_by_the_due_date_may_be_likely`
- **Lesson:** Don't count two reads of one page as two witnesses.

#### B-385 · "Please quote PO number 4500 and invoice ... with payment" read 4500 as the PO · low
- **Where:** `ap_coder/capture/reader.py` (`_po_mentions`, new `_ASKS_FOR_PO`)
- **Symptom:** an instruction to quote the PO with the payment gave the PO "4500" (*check*).
- **Cause:** a PO label mid-line was read wherever it stood.
- **Reproduce:** a PDF with that sentence and no PO label; `analyze()`.
- **Reproduced:** yes (`po_number 4500`)
- **Fix:** a PO label after "quote", "reference", "indicate", "include", "mention", "show" (or French "rappeler",
  "indiquer") is an instruction, not a PO.
- **Test:** `tests/test_terms_po_sweep4.py::test_please_quote_the_po_number_is_an_instruction_not_a_po`
- **Lesson:** Don't read a label inside a sentence that asks for the value.

#### B-386 · In a tight header grid the PO label took "Net 30" from the terms column, likely · low
- **Where:** `ap_coder/capture/reader.py` (`_value_reading`)
- **Symptom:** "Customer ID | Terms | Ship Via | PO# | Due Date" with columns as wide as their text plus 6 to 10
  points: the PO read "Net 30" (*likely*); found by the re-review, as it was before this sweep.
- **Cause:** "Net" and "30" make an identifier (a short word and a number); only "Net30days" run together was refused.
- **Reproduce:** the grid of `test_terms_in_the_next_column_of_a_tight_grid_are_no_po`; `analyze()`.
- **Reproduced:** yes (`po_number Net 30 likely`)
- **Fix:** "Net 30", "Net 30 days" is never a PO (only for the PO, leaving the invoice number's reading as it is).
- **Test:** `tests/test_terms_po_sweep4.py::test_terms_in_the_next_column_of_a_tight_grid_are_no_po`
- **Lesson:** Don't let terms pass as an identifier because they hold a number.

#### Left as they are (terms, PO numbers and due dates)

- **N-121** · The corpus writes German terms as the whole printed sentence, dates included ("Zahlbar innerhalb 30
  Tagen netto bis 04.04.2013, 3% Skonto innerhalb 10 Tagen bis 15.03.2013"); the reader gives the app's short form
  ("3% 10 Net 30", "2% 14 days", "Due on receipt" for "Zahlbar sofort rein netto" and "sofort fällig"). The app reads
  both the same way, but the corpus scorer compares the numbers in the text, so 10 such readings count as wrong
  there (all *check* or *likely*, none verified). Left: the short form is what the due date is counted from.
- **N-122** · York University's sample invoice as a PDF (`ca_yorku_sample`) is a picture of the invoice on a page
  whose side panel is text: only the text is read, so its PO and terms are not seen (the photo of it, read with
  OCR, reads both now). Reading the picture inside a text page is a layout question, left.
- **N-123** · Insurance notices and direct debits (ZUGFeRD "Beitragsrechnung", "wird am 20.03.2014 von Ihrem Konto
  ... eingezogen", Hetzner's "Bankeinzug"): their "terms" are a paragraph on how the amount is collected and the
  due date the day of the debit. Not read; a debit date is not a due date AP pays by.
- **N-124** · Printed but left out of the corpus's answers, and read: "30% d'acompte, solde à 30 j" as Net 30
  (marked as the balance's terms, B-328), "Paiement immédiat" as due on receipt, "Bestellung Nr. 2014-471331" and
  "Bestell-Nr: B610061523500010" as the PO, the net date of a German terms sentence as the due date. The scorer
  counts them as spurious; they are on the page.
- **N-125** · The same "Bestell-Nr", "Bestellung:" and colon-in-the-value's-cell layouts give the invoice number and
  date on the same invoices ("Rechnungs-Nr : 471102", "Leistungs-Datum : 05.08.2013"); B-374 lets a cell holding the
  label's colon be read only for the PO, due date and terms, so as not to change how the invoice number and date are
  read in this change. The same rule likely helps them.
- **N-126** · A discount in days with net days on a day of the next month ("2% 10 days, Net 10th prox") is read as
  "Net 10 prox": the due date is kept and the early-payment discount dropped, since the app's terms form cannot
  hold both (B-377). A person adds the discount if it is wanted. Left until the terms form can.

### Reading: English vendor PDFs

Found on 48 real English invoices that suppliers make as PDFs (sweep 4): Canadian cloud, consulting and staffing
firms' invoices to a federal agency, a university's sample invoices, utility bills, US TV stations' advertising
invoices to agencies, and a few templates. Each fix has a small generated fixture in `tests/test_en_pdf_sweep4.py`
(the corpus is not in the repo). B- numbers start at B-394 and N- numbers at N-130.

#### B-394 · An invoice date run into the next label, or printed with soft hyphens, was not read · medium
- **Where:** `ap_coder/capture/normalize.py` (`_NUMERIC`, `parse_dates`, `infer_day_first`)
- **Symptom:** "Invoice Date: 08-01-2016Contract ID: 1272709" (the PDF's text has no space before the next label) was
  not read, so a spot's air date on the schedule page (06/27/16) became the invoice date; "Invoice Date: 10­31­2016",
  printed with soft hyphens (U+00AD) between its parts, was missing.
- **Cause:** a numeric date had to end on a word boundary, and only "-", "/" and "." separated its parts.
- **Reproduce:** `parse_dates("08-01-2016Contract ID: 1272709")`, `parse_dates("10­31­2016")`.
- **Reproduced:** yes (`[]` for both)
- **Fix:** a four-digit year may run into a word of letters; a soft hyphen is read as a hyphen (same length, so the
  date's place on the line holds).
- **Test:** `tests/test_en_pdf_sweep4.py::test_a_date_run_into_the_next_label_or_printed_with_soft_hyphens_is_read`,
  `tests/test_en_pdf_sweep4.py::test_the_invoice_date_run_into_the_next_label_is_not_lost_to_a_date_in_the_schedule`
- **Lesson:** Don't expect a PDF's text to put spaces where the page shows a gap: the next cell's text may follow
  the value with nothing between them.

#### B-395 · A credit memo that says so only in its body read as an invoice, its amounts verified positive · high
- **Where:** `ap_coder/capture/reader.py` (`credit_titled`, new `_CREDIT_STATED`)
- **Symptom:** A university's sample credit memo titled "EXERCISE 9", numbered CR4597, with "DO NOT PAY – THIS IS A
  CREDIT MEMO" across its body and its amounts printed without a sign: subtotal 23.99, HST 3.12, tax total 3.12 and
  total 27.11 were all verified, where AP posts -23.99, -3.12 and -27.11. The same exercise titled "Exercise 4 –
  CREDIT MEMO" was read negative.
- **Cause:** a document was a credit only when a line in the top third of its first page started with a credit
  title, or its total was labelled a credit.
- **Reproduce:** a PDF titled "EXERCISE 9" with "DO NOT PAY - THIS IS A CREDIT MEMO" in its body; `analyze()`.
- **Reproduced:** yes (`grand_total 27.11 verified`)
- **Fix:** a page that says "this is a credit memo" (or "credit note", "credit memorandum") anywhere on its first
  page is a credit, as one titled so.
- **Test:** `tests/test_en_pdf_sweep4.py::test_a_credit_memo_that_says_so_only_in_its_body_is_read_as_a_credit`
- **Lesson:** Don't look for what a document is only in its title: a sample or a form may say it in a sentence.

#### B-396 · The amount due after a prepayment read as the subtotal printed again, verified · high
- **Where:** `ap_coder/capture/reader.py` (`LABELS["grand_total"]`, `LABELS["other_charges"]`, `_DISCOUNT`, new
  `_PREPAYMENT`, `PREPAID`, `_solve_totals`)
- **Symptom:** Cox Media's invoice prints "Sub Total $611.00 | Posted Pre-Payments ($519.35) | Total This Invoice
  $91.65", and its spot schedule on page 3 ends "Grand Total 24 $611.00". The total was 611.00, verified ("totals add
  up": the subtotal with no tax); the 91.65 to pay was "another reading".
- **Cause:** "Total This Invoice" was only the weak label "Total", the schedule's "Grand Total" on the last page
  ranked first, and nothing read the prepayment, so the subtotal equal to a total was taken as proof.
- **Reproduce:** a PDF with the three lines above and a second page with "Grand Total 24 $611.00"; `analyze()`.
- **Reproduced:** yes (`grand_total 611.0 verified`)
- **Fix:** "Total This Invoice" and "Net amount (balance) due" are total labels; a prepayment or deposit taken off
  ("Posted Pre-Payments", "Less deposit", "Deposit applied") is read as an amount taken off the total, like a
  discount, offered to the solver only when no tax is read (a deposit may come off before or after the tax). A total
  that adds up only through one is marked `UNMEASURED`: likely at best. A subtotal and a total read from one printed
  amount no longer add up with each other (B-397).
- **Test:** `tests/test_en_pdf_sweep4.py::test_the_amount_due_after_a_prepayment_is_the_total_and_not_verified_on_the_subtotal`
- **Lesson:** Don't take a total equal to the subtotal as proof when a line between them takes something off.

#### B-397 · An advertising invoice's gross read as its total and the net after the agency's commission as its subtotal · high
- **Where:** `ap_coder/capture/reader.py` (new `_net_of_commission`, `_COMMISSION`, `_GROSS`, `_NET`, `_LabelHit.unmeasured`,
  `_read_fields`, `_solve_totals`, `LABELS["tax_total"]`)
- **Symptom:** TV stations bill agencies "Gross Total $17,000.00 | Commission ($2,550.00) | Net Total $14,450.00"
  (or "Agency Commission", "Rep. Firm Commission", "Less: Agency Fee", "Net Amount Due"): the total was read as
  17,000.00 and the subtotal as 14,450.00 on Sinclair's, WPXI's and WDTV's invoices, and Comcast's had no total at
  all. KHON's prints the gross at the foot of page 6 and "Agency Commission | Net Amount Due 29,962.50 | HI General
  Excise Tax 1,411.83 | Amount Due 31,374.33" on page 7: its tax was not read.
- **Cause:** "Gross Total" is a total's label, "Net Total" a subtotal's (and kept off the total by a distractor);
  nothing read the commission between them. "General Excise Tax" was no tax label.
- **Reproduce:** a PDF with the gross, commission and net lines above; `analyze()`.
- **Reproduced:** yes (`grand_total 17000.0`, `subtotal 14450.0`)
- **Fix:** on a document that takes a commission off (a commission line with an amount), the gross is neither the
  total nor the subtotal, and a net amount printed under the commission is also read as the total (`UNMEASURED`:
  likely at best); it stays a subtotal only when a total is printed under it (a tax charged on the net). One printed
  amount is never both the subtotal and the total. "General Excise Tax" (Hawaii's) is a tax total's label.
- **Test:** `tests/test_en_pdf_sweep4.py::test_an_agency_commission_is_taken_off_the_gross_the_net_is_the_total`,
  `tests/test_en_pdf_sweep4.py::test_a_tax_charged_on_the_net_after_the_commission_still_adds_up`
- **Lesson:** Don't read "Gross" and "Net" by their words alone: what is taken off between them says which one is
  paid.

#### B-398 · The name under a "Remit to" label on its own line was not read; the label's translation was · medium
- **Where:** `ap_coder/capture/reader.py` (`_REMIT`, new `_TRAILING_LABEL`, `_under_remit_label`, `_vendor_names`)
- **Symptom:** "PLEASE REMIT TO:" over "TEKSYSTEMS CANADA CORP": the customer printed lower, "CANADA BORDER SERVICES
  AGENCY (CLOUD)", was read as the supplier (likely); "Please Remit To/S'il vous plaît remettre à:" gave
  "/S'il vous plaît remettre à"; "REMIT TO Sinclair Broadcast G Advertiser" kept the next column's label;
  "Remit Address:", "Send Payment To:" and "Mail to: Comcast Spotlight" (at the foot) were not remit labels, so KHON's
  "Advertiser" and Comcast's "AGENCY" (box labels) were read as the supplier.
- **Cause:** only a name after the remit label on its line was read, whatever it was.
- **Reproduce:** the PDFs of the tests below; `analyze()`.
- **Reproduced:** yes (`'CANADA BORDER SERVICES AGENCY (CLOUD)'`, `"/S'il vous plait remettre a"`)
- **Fix:** a remit label with no name after it (or only its translation after a slash) reads the name beside it or
  on the line under it; "Remit Address", "Send Payment To", "Mail (payments) to" are remit labels; a box's label
  run onto the end of the name ("Advertiser", "Agency", "Customer"...) is dropped. A name read under the label is
  marked `UNMEASURED` (a person checks it) unless the same name is printed elsewhere on the page; a remit name that
  shares its first word with a company's name at the top ("Contoso Billing", "CONTOSO LTD.") ranks below it.
- **Test:** `tests/test_en_pdf_sweep4.py::test_the_name_under_a_remit_to_label_is_the_supplier_not_the_customer_above_it`,
  `tests/test_en_pdf_sweep4.py::test_a_bilingual_remit_label_is_no_name_and_a_column_label_run_onto_a_name_is_dropped`,
  `tests/test_en_pdf_sweep4.py::test_the_payee_beside_a_mail_to_label_at_the_foot_is_the_supplier`
- **Lesson:** Don't read a label's value only after it on its line: a label printed alone has its value under it.

#### B-399 · A subject line, a salutation, a box's label, a month, opening hours or a tagline read as the supplier, likely · medium
- **Where:** `ap_coder/capture/reader.py` (`_not_a_name`, new `_NOT_A_PARTY`, `_over_address`, `WEAK_NAME`;
  `_vendor_names`, `_seller_named`)
- **Symptom:** read as the supplier, all likely: "Subject: Ongoing Planning Services – Municipality of Argyle" (a
  letter-style invoice), "Closed Saturday, Sunday, and Stat. Holidays" (a water bill), "Dec" (a usage chart's month),
  "Advertiser" and "AGENCY" (box labels), "A quick overview of your bill." (a utility bill's tagline), "International
  Piping Company" (the tagline under "McWilliam Piping", printed larger); "Implementation Services" (a line item,
  "Implementation Services - CA - GC", taken for an envelope's sender line "Name • Street • Town").
- **Cause:** a line carrying a company word ("Services", "Company") was a name whatever else it was; a sentence
  needed a last word of five letters; any top-of-page reading was likely on the benchmark's record of the method,
  however little it scored; a sender's line needed no address in it.
- **Reproduce:** the PDFs of the tests below; `analyze()["vendor_name"]`.
- **Reproduced:** yes (`'Subject: Ongoing Planning Services - Municipality of Argyle' likely`, `'Dec' likely`)
- **Fix:** never a name: a subject or "Re:" line, a salutation or an addressee ("Dear", "Mr."), a box's or column's
  label printed alone, a month, opening hours, a sentence of four words and more ending with a period, a label with
  its amount on its row ("Previous Balance 111.54"), the town under a street address. A name over its street address
  counted a little more (taken out again, B-419). A sender's line needs an address after the name.
  (A rule taking the line printed far larger than the rest for the letterhead was taken out again, B-416: the
  larger line is as often a customer's or a project's name; McWilliam's tagline is read as before, likely.) A top-of-page name scoring below `WEAK_NAME`
  (no company form, not larger, low on the page) is marked `UNMEASURED`: a person checks it.
- **Test:** `tests/test_en_pdf_sweep4.py::test_lines_that_name_no_supplier_are_not_read_as_its_name`,
  `tests/test_en_pdf_sweep4.py::test_a_large_line_over_the_supplier_is_not_taken_for_its_letterhead`,
  `tests/test_en_pdf_sweep4.py::test_a_line_item_with_dashes_is_not_an_envelope_s_sender_line`
- **Lesson:** Don't let one word make a name: "Services" is in a subject line and a line item as often as in a
  supplier's name.

#### B-400 · A name ending in a province's two letters taken for a place: "KHON", "Johnson" · medium
- **Where:** `ap_coder/capture/reader.py` (`_PLACE`)
- **Symptom:** KHON (a TV station) under its "Remit Address:" label was never read as a name; "Johnson", "Allison",
  "Hudson" printed alone would not be either.
- **Cause:** a place was "a few words, then a province's code", with no word boundary before the code: "kh" + "on"
  (Ontario) was "Toronto ON".
- **Reproduce:** `_not_a_name("KHON")`.
- **Reproduced:** yes (`True`)
- **Fix:** the province's code starts a word.
- **Test:** `tests/test_en_pdf_sweep4.py::test_a_name_ending_in_a_province_s_letters_is_still_a_name`
- **Lesson:** Don't match a short code without a word boundary before it: two letters end many names.

#### B-401 · "Total HST", "TOTAL HST 13%" and "Total HST/Montant TVH Total" were not read as the HST · medium
- **Where:** `ap_coder/capture/reader.py` (`LABELS["hst_amount"]`, `LABELS["gst_amount"]`, `DISTRACTORS["grand_total"]`,
  `_labelled`)
- **Symptom:** Distill Mobile's "TOTAL HST 13% 36,322.00" gave no HST and no tax total; Modis's "Total HST/Montant TVH
  Total | 871072369RT: | 3,105.60" gave neither, and its total was only *check*.
- **Cause:** the HST label allowed a province's code before it, not "Total"; "total" matched the grand total's label
  instead. The French half after the slash ("Montant TVH Total") counted as a note about the tax (three words), which
  has no amount.
- **Reproduce:** the PDFs of the test below; `analyze()["hst_amount"]`.
- **Reproduced:** yes (missing)
- **Fix:** "Total" may start a GST or HST label (and "Total GST/HST/PST/QST..." is no grand total); a label's
  translation after a slash is no note.
- **Test:** `tests/test_en_pdf_sweep4.py::test_total_hst_with_its_rate_and_a_bilingual_total_hst_label_are_the_hst`
- **Lesson:** Don't stop a label's pattern at the words the benchmark prints before it.

#### B-402 · A tax code printed on every line item crowded the invoice's HST out of the solver · medium
- **Where:** `ap_coder/capture/reader.py` (`_read_fields`)
- **Symptom:** GC Strategies prints "Services | qty | rate | HST ON | 35,844.00" on 18 rows over two pages and
  "HST (ON) @ 13% 83,098.34" in its totals and its tax summary: the HST was missing on all three of its invoices.
- **Cause:** every "HST ON" cell was an HST label with the line's amount beside it, scored like the real one; the
  solver tries the four best of each tax, all of them line amounts.
- **Reproduce:** the PDF of the test below; `analyze()["hst_amount"]`.
- **Reproduced:** yes (missing)
- **Fix:** a tax label printed with the same words on three rows or more of a page is a tax code's column, not a
  tax: none of those rows is read for the tax.
- **Test:** `tests/test_en_pdf_sweep4.py::test_a_tax_code_column_does_not_crowd_out_the_hst_in_the_totals`
- **Lesson:** Don't let the same label repeated down a column count as many readings of one field.

#### B-403 · A tax printed only as "Tax (13%)" or "Total Taxes" was not read as the HST the page names · low
- **Where:** `ap_coder/capture/reader.py` (new `_tax_named_by_page`, `_TAX_WORDS`)
- **Symptom:** Distill Mobile's "Tax (13%) $26,000.00" with "HST Number: 835582081" in its notes, and WSP's "Total
  Taxes : $1,255.50" over a tax table headed "HST-14015 0269 | 15.000 %": the tax total was read, the HST not (the
  ERP's tax line needs which tax it is).
- **Cause:** a tax's kind was read only from its own label.
- **Reproduce:** the PDF of the test below; `analyze()["hst_amount"]`.
- **Reproduced:** yes (missing)
- **Fix:** with no GST, HST, PST or QST read and a tax total that is 13 to 15% of the subtotal, the HST when the page
  names the HST (5%: the GST when it names the GST) and no provincial tax. Marked `UNMEASURED`: likely at best, when
  the totals add up. A US invoice's "Tax (8%)" names no Canadian tax and is left as a tax total.
- **Test:** `tests/test_en_pdf_sweep4.py::test_a_tax_printed_as_tax_13_percent_is_the_hst_the_page_names`
- **Lesson:** Don't look for what a figure is only beside it: the page may say it once, elsewhere.

#### B-404 · A business number printed alone after "GST/HST Registration No." or "HST #" was not read · low
- **Where:** `ap_coder/capture/reader.py` (new `_bare_business_numbers`, `_GST_REG_LABEL`, `_registration_numbers`);
  `ap_coder/capture/confidence.py` (`run_checks`)
- **Symptom:** "GST/HST Registration No.:" over "121628366", "HST (ON) Registration No.: 713892313", "HST Number:
  835582081", "HST # 108162333": twelve invoices print the nine digits without "RT0001", and none was read.
- **Cause:** only the whole number (nine digits, RT and four digits) was a GST/HST number.
- **Reproduce:** the PDFs of the test below; `analyze()["gst_hst_registration_number"]`.
- **Reproduced:** yes (missing)
- **Fix:** nine digits after a GST/HST number's label (on its line, beside it or under it) are read as the number
  ("label-bn", `UNMEASURED`), when no whole number is printed; a customer's ("Client GST/HST No.") is not. Its check
  digit is checked: a valid one makes it likely, an invalid one (the university's made-up samples) leaves it *check*.
- **Test:** `tests/test_en_pdf_sweep4.py::test_a_business_number_after_its_gst_hst_label_is_read_alone`
- **Lesson:** Don't wait for a number's full form when its label says what it is.

#### B-405 · A US supplier's dollars were left with no currency (coded CAD) · medium
- **Where:** `ap_coder/capture/reader.py` (new `_us_supplier`, `_PROVINCE_NAME`, `_read_fields`, `_US_ADDRESS`);
  `ap_coder/capture/bridge.py` (`currency_read`)
- **Symptom:** 16 US invoices that print only "$" (TV stations, utilities, templates) had no currency read; the
  pipeline codes a missing currency as CAD.
- **Cause:** only a printed code, sign or statement ("USD", "US$", "All amounts in US dollars") gave a currency. "DC"
  (Washington) was no US state in the address pattern.
- **Reproduce:** a PDF from "Bayside Media LLC, Atlanta, GA 30348" with "$1,000.00" amounts; `analyze()["currency"]`.
- **Reproduced:** yes (missing)
- **Fix:** amounts in "$" on a document whose first page prints a US address outside the customer's block and
  nothing Canadian outside it (no GST/HST/PST/QST word or number anywhere, no postal code or province) are US dollars,
  marked `UNMEASURED`: *check* (a Canadian supplier's "$" is Canadian dollars, and the page does not say it). The
  offline coder already assumed US dollars for such a supplier; the review keeps its "Currency not printed" warning
  (`capture.bridge.currency_read` does not count this reading as printed).
- **Test:** `tests/test_en_pdf_sweep4.py::test_dollars_from_a_us_supplier_are_us_dollars_never_more_than_checked`,
  `tests/test_transcript.py::test_offline_coding_assumes_a_currency_by_the_suppliers_address` (now expects the
  reading at *check*)
- **Lesson:** Don't default a currency the page implies: say what it implies and let a person confirm it.

#### B-406 · Text drawn sideways on a portrait page was read word by word down the page · high
- **Where:** `ap_coder/capture/layout.py` (new `_text_turn`, `_turn_box`, `_text_layer_page`)
- **Symptom:** Microsoft's three landscape invoices are set on portrait pages with their text drawn running up the
  page (the page itself not rotated): the reader got columns of single words ("date", "invoice", "from", "days") and
  read the supplier as "days", the invoice number as "30", the PO as "1950" (from the street address, likely), the
  tax total as the subtotal 540,600.00 (likely), and missed the dates, the HST and the total.
- **Cause:** the text layer's words were placed as drawn; rows of a sideways page are columns of words.
- **Reproduce:** a PDF page with its lines inserted with `rotate=90`; `build_layout()` and `analyze()`.
- **Reproduced:** yes (`turn 0`, invoice number missing)
- **Fix:** when most of a page's characters are drawn in one other direction (up, down, upside down), the words are
  read on the page turned that quarter turn and the page's `turn` says so: the pipeline shows the PDF turned the same
  way (as for scans read turned), so each value is highlighted where it is read.
- **Test:** `tests/test_en_pdf_sweep4.py::test_a_page_whose_text_runs_up_the_page_is_read_turned`
- **Lesson:** Don't assume a PDF's text runs the way its page is set: check the direction the text is drawn in.

#### B-407 · Labels on Microsoft's and others' invoices not read: "Document Date", "Bill to PO Number", "Total Sale", "REMIT TO BANK" · medium
- **Where:** `ap_coder/capture/reader.py` (`LABELS`, `_REMIT_WHERE`, `_ACCOUNT_NAME`, `_under_remit_label`, `_vendor_names`)
- **Symptom:** once read the right way up (B-406): the invoice date ("Document Date:"), the PO ("Bill to PO Number:")
  and the subtotal ("Total Sale") were missing, the PO was the line number "000010" under a "PO No" column heading,
  and the supplier was "BANK" ("REMIT TO BANK") or "A/C Name: MICROSOFT CANADA INC".
- **Cause:** none of these labels was known; "Remit to" took the next word whatever it was.
- **Reproduce:** the PDFs of the tests below.
- **Reproduced:** yes (`vendor_name 'BANK'`)
- **Fix:** "Document Date" is an invoice date's label (beside "Document No", read as the number before), "Bill to PO
  Number" a PO's, "Total Sale(s)" a subtotal's; "remit to bank / address / account / the following" names nobody,
  and an account's name ("A/C Name:", "Account Name:") under it, or at the top, is read without its label.
- **Test:** `tests/test_en_pdf_sweep4.py::test_a_page_whose_text_runs_up_the_page_is_read_turned`,
  `tests/test_en_pdf_sweep4.py::test_remit_to_bank_is_no_name_the_account_s_name_under_it_is`
- **Lesson:** Don't take the word after "Remit to" for a payee: it may say where, not to whom.

#### B-408 · A GST/HST amount with no subtotal read could be verified as the GST; "QST/PST 0.00" verified as the QST · high
- **Where:** `ap_coder/capture/reader.py` (`_solve_totals`, `_QST_OR_PST`, `_read_fields`); `ap_coder/capture/confidence.py` (`fuse`)
- **Symptom:** Microsoft's support invoice (read turned, B-406, before "Total Sale" was a label) gave "GST/HST
  16,874.00" as the GST, verified (it is the HST, 13%), and "QST/PST 0.00" as a verified QST.
- **Cause:** a "GST/HST" label proposes both taxes and the solver picks by the rate on the subtotal; with no
  subtotal nothing tells them apart, and the first one won. "QST/PST" (QST first) was not known as a label for
  either of two taxes, as "PST/QST" is (B-321).
- **Reproduce:** a PDF with "GST/HST 16,874.00", "QST/PST 0.00" and "Total Amount 146,674.00" and no subtotal.
- **Reproduced:** no on main (found on this branch's turned pages, before "Total Sale" was read); the test guards it.
- **Fix:** a GST or HST read with no subtotal, from an amount proposed for both, is marked `EITHER_TAX` (likely at
  best: "printed under a label naming two taxes"); "QST/PST" is marked so as "PST/QST" is.
- **Test:** `tests/test_en_pdf_sweep4.py::test_a_gst_hst_amount_with_no_subtotal_to_tell_its_rate_is_not_verified_as_either_tax`
- **Lesson:** Don't settle which of two taxes a figure is when the figure that tells them apart was not read.

#### B-409 · Totals' rows: the hours read as the subtotal, an exchange rate or a date's year as the total · high
- **Where:** `ap_coder/capture/reader.py` (`_RIGHTMOST`, `_value_reading`, new `_FX_RATE`)
- **Symptom:** TEKsystems' "SUBTOTAL: | 23.00 | 23,115.00" (the hours' column, then the amount's) gave the subtotal
  23.00; AWS's request for funds "Total Amount in CAD (1 USD = 1.235 CAD) | CAD 4,000,000" gave the total 1,235.00,
  and with the rate left out "AMOUNT DUE ON | April 1, 2022 | CAD 4,000,000" gave 2,022.00 (likely).
- **Cause:** a subtotal took the first figure to the right of its label (taxes and totals take the rightmost); an
  amount's reader read every number on the label's line, a rate's and a date's included.
- **Reproduce:** the PDFs of the tests below.
- **Reproduced:** yes (`subtotal 23.0`, `grand_total 1235.0`)
- **Fix:** a subtotal's row of figures is read like a total's (the rightmost figure printed as money, the first one
  kept as a weaker reading); a rate written "1 USD = 1.235 CAD" and a date written with its month's name are blanked
  before the amounts are read.
- **Test:** `tests/test_en_pdf_sweep4.py::test_a_subtotal_row_with_the_hours_first_is_read_from_its_amount_column`,
  `tests/test_en_pdf_sweep4.py::test_an_exchange_rate_or_a_date_s_year_on_the_total_s_row_is_no_amount`
- **Lesson:** Don't read every number on a total's line as money: a rate and a year are numbers too.

#### B-410 · A PO run into the next word after a slash: "474192803B/Contract", likely · medium
- **Where:** `ap_coder/capture/reader.py` (`_value_reading`, new `_GLUED_WORD`)
- **Symptom:** SADA's "PO#474192803B/Contract" was read as the PO "474192803B/Contract", likely.
- **Cause:** a slash is part of an ID ("2021/0042"), so the next label's word after it was kept.
- **Reproduce:** a PDF with "Customer PO #" over "PO#474192803B/Contract".
- **Reproduced:** yes (`'474192803B/Contract'`)
- **Fix:** a capitalised word of four letters and more after a slash, ending an ID with digits, is cut off.
- **Test:** `tests/test_en_pdf_sweep4.py::test_a_po_run_into_the_next_word_after_a_slash_is_cut_there`
- **Lesson:** Don't keep a word in an ID because a separator joins it: IDs end in digits and codes, not in words.

#### B-411 · Dates: a letter's date alone was not read; a date in another sentence over "DUE DATE" was the due date · medium
- **Where:** `ap_coder/capture/reader.py` (new `_letter_dates`, `_in_a_sentence`, `_labelled`, `_value_over_label`);
  `ap_coder/capture/confidence.py` (`UNLABELLED_METHODS`)
- **Symptom:** BDO's letter-style invoice prints "June 30, 2021" alone under its letterhead: no invoice date. A utility
  bill prints "Mailed 11/14/22 for Account #" over its "AMOUNT DUE | DUE DATE" box: the due date was 2022-11-14 (it
  is 12/12/22 in the box).
- **Cause:** a date was read only after a label or beside a title; a label printed under its value took the date
  from the line over it whatever that line said.
- **Reproduce:** the PDFs of the tests below.
- **Reproduced:** yes (invoice date missing; due date 2022-11-14)
- **Fix:** with no date labelled, the one line of the first page's top that is a date written out and nothing else is
  the invoice date ("letter-dated": *check*, as other dates without a label). A date over its label is not taken from
  a line that starts with words before it.
- **Test:** `tests/test_en_pdf_sweep4.py::test_a_letter_s_date_alone_under_the_letterhead_is_the_invoice_date_checked`,
  `tests/test_en_pdf_sweep4.py::test_a_date_in_another_sentence_over_a_due_date_label_is_not_the_due_date`
- **Lesson:** Don't take any date over a label as its value: the line over it may be a sentence about another date.

#### B-412 · Found on the hard corpus after B-396 and B-404: a balance after a deposit read as the total, a misread whole business number read as nine digits, likely · high
- **Where:** `ap_coder/capture/reader.py` (`_solve_totals`, `_bare_business_numbers`)
- **Symptom:** two generated invoices that print "Total 2,344.90 | Deposit received | Balance due 1,758.67" gave the
  balance as the total (likely; the corpus's total is the invoice's, 2,344.90); "GST/HST Reg. No. 232672980-RT-0001"
  read badly on a scan gave "232672980" (likely; the number is the whole one).
- **Cause:** B-396 let a deposit taken off make any later total add up; B-404 read nine digits after the label
  whatever followed them.
- **Reproduce:** the PDFs of the tests below.
- **Reproduced:** yes (`grand_total 1758.67`; `['232672980']` read)
- **Fix:** a deposit is taken off only when no total on the subtotal's page equals the subtotal (Cox's "Total This
  Invoice" is the only total on its page); nine digits followed by an "R" or a "T" are the whole number misread, not
  a business number printed alone.
- **Test:** `tests/test_en_pdf_sweep4.py::test_a_deposit_under_a_printed_total_leaves_the_total_as_printed`,
  `tests/test_en_pdf_sweep4.py::test_a_whole_business_number_misread_is_not_read_as_its_nine_digits`
- **Lesson:** Don't let a new way for the totals to add up outrank the plain one the page prints.

#### B-413 · A sentence about credit memos made an ordinary invoice a credit, its amounts verified negative · high
- **Where:** `ap_coder/capture/reader.py` (`_CREDIT_STATED`, `credit_titled`, new `credit_stated`, `_credit_titled`,
  `_read_fields`)
- **Symptom:** found by the review of this sweep (generated invoices): "If this document is a credit memo, the amount
  will be applied to your account.", "Where this is a credit note, no payment is required.", "Unless this is a credit
  memo, payment is due ...", "If this is a credit memo, do not pay." in a footer: the whole invoice read negative,
  verified (main read it right).
- **Cause:** B-395 took "this is a credit memo" anywhere in a line, a condition included.
- **Reproduce:** a plain invoice with one of the footers above; `analyze()["grand_total"]`.
- **Reproduced:** yes (`-621.5 verified`)
- **Fix:** the statement fills its line ("THIS IS A CREDIT MEMO", "DO NOT PAY – THIS IS A CREDIT MEMO", with "no
  payment required" after it at most); a document that is a credit only by such a line has its amounts marked
  `UNMEASURED` (likely at best: the title does not say it).
- **Test:** `tests/test_en_pdf_sweep4.py::test_a_sentence_about_credit_memos_does_not_make_an_invoice_a_credit`
- **Lesson:** Don't read a statement out of a sentence: "if this is X" says the opposite of "this is X" as often.

#### B-414 · A customer's block lower down outranked the supplier because the supplier's address starts with "Suite" · high
- **Where:** `ap_coder/capture/reader.py` (`_STREET_START`, `_vendor_names`)
- **Symptom:** the review's invoices with the supplier's "Suite 200, 123 King Street West" (or "Unit 4 - 88 Industrial
  Rd") under its name and an unlabelled customer block below ("Acme Industries Inc." over "45 Queen Street" or "PO Box
  455", where a window envelope shows it): the customer was the supplier, likely (main: the supplier).
- **Cause:** B-399's "name over its street address" bonus knew only addresses starting with a number or a PO box, and
  gave it to any line.
- **Reproduce:** the PDFs of the test below.
- **Reproduced:** yes (`'Acme Industries Inc.'`)
- **Fix:** an address may start with "Suite", "Unit", "Ste", "Apt", "Bureau", "Floor" or "Room" and a number; the bonus
  goes only to the first name on the page (no other candidate above it).
- **Test:** `tests/test_en_pdf_sweep4.py::test_an_unlabelled_customer_block_lower_down_is_not_the_supplier`
- **Lesson:** Don't reward a pattern the customer's block has as well: the address under a name is everyone's.

#### B-415 · Sideways text in a margin turned an upright page · high
- **Where:** `ap_coder/capture/layout.py` (`_text_turn`)
- **Symptom:** the review's invoices with four lines of 5.5 pt terms (or two form-number strips) printed up the right
  margin: the page was read turned (B-406), the supplier became "No. ON Northwind", the invoice date the due date
  (likely), and most fields were lost.
- **Cause:** a page turned when 60% of its characters ran sideways; the small print had more characters than the
  invoice.
- **Reproduce:** the PDF of the test below; `build_layout().pages[0].turn`.
- **Reproduced:** yes (`90`)
- **Fix:** 80% of the characters sideways, and the sideways lines covering at least an eighth of the page across their
  run (Microsoft's pages: 16 to 54%; a margin's strips: 2 to 8%).
- **Test:** `tests/test_en_pdf_sweep4.py::test_sideways_text_in_the_margin_does_not_turn_an_upright_page`
- **Lesson:** Don't decide a page's orientation by counting characters alone: small print has many of them.

#### B-416 · Totals: "Total Sale" under the tax read as the subtotal, verified; arithmetic blanked as a rate; "TOTAL HST INCL." lost · high
- **Where:** `ap_coder/capture/reader.py` (`_solve_totals`, `_read_fields`, `_TOTAL_SALE`, `_FX_RATE`, `DISTRACTORS`,
  `_vendor_names`)
- **Symptom:** the review's invoices: "HST 13% 71.50 | Total Sale 621.50 | Balance Due 621.50" gave the subtotal 621.50,
  verified, and no HST (main does the same with "Net Amount ... Amount Due"); "HST 13% of 550.00 = 71.50" lost the HST
  and "Total: 10 x 62.15 = 621.50" read 10.00 (B-409's rate blanked any "a = b"); "TOTAL HST INCL. 621.50" lost the
  total (B-401's exclusion); a large customer, project or promotion line over the supplier won as its letterhead
  (B-399); the supplier's name was lost when "Amount Due: $621.50" sat across its row.
- **Cause:** the solver let a subtotal equal to the total add up with no tax while a tax was printed; "Total Sale" was a
  subtotal's label wherever it stood; the other causes as said.
- **Reproduce:** the PDFs of the tests below.
- **Reproduced:** yes (`subtotal 621.5 verified`, `hst_amount` missing; `grand_total 10.0`; vendor missing)
- **Fix:** a subtotal equal to the total with no tax adds up only when no tax is read at all; "Total Sale(s)" is a
  subtotal only over a tax (not "incl."); only a rate for one unit ("1 USD = 1.235 CAD", "USD 1 = CAD 1.35") is blanked;
  "Total HST incl." is a total, not the HST; the letterhead rule is taken out; a label's amount across the page is
  not the name's row's amount (only the next cell, and not after another label).
- **Test:** `tests/test_en_pdf_sweep4.py::test_total_sale_under_the_tax_is_the_total_and_the_hst_is_kept`,
  `tests/test_en_pdf_sweep4.py::test_arithmetic_on_a_totals_line_is_not_an_exchange_rate`,
  `tests/test_en_pdf_sweep4.py::test_total_hst_incl_is_the_invoice_total`,
  `tests/test_en_pdf_sweep4.py::test_a_large_line_over_the_supplier_is_not_taken_for_its_letterhead`,
  `tests/test_en_pdf_sweep4.py::test_the_supplier_s_name_with_an_amount_due_label_across_its_row_is_still_read`
- **Lesson:** Don't let one printed figure prove itself: a subtotal equal to the total is no evidence beside a tax.

#### B-417 · Currency, tax and number rules of this sweep that read other parties: lockbox USD, excise tax, a purchaser's HST number, invoice numbers cut · medium
- **Where:** `ap_coder/capture/reader.py` (`_us_supplier`, `_CA_CITY`, `_LOCKBOX`, `LABELS["tax_total"]`, `_OTHER_PARTY`,
  `_bare_business_numbers`, `_value_reading`); `ap_coder/capture/normalize.py` (`currency_evidence`);
  `ap_coder/capture/confidence.py` (`run_checks`, `WEAK_CHECKS`)
- **Symptom:** the review's invoices: a Canadian supplier with a US lockbox, or one printing "CA$", read USD (*check*);
  "Excise Tax 40.00" on a GST-only fuel invoice read as a tax total; "Purchaser's HST #", "Exempt purchaser HST #",
  "Carrier HST #", "Subcontractor HST # 876543216" read as the supplier's number, likely (the check digit lifted it
  past `UNMEASURED`); "1234/Revised" and "INV-55/Final" cut to "1234" and "INV-55".
- **Cause:** B-405 counted any US address and knew no "CA$"; B-397 made "excise tax" a tax label; B-404 knew only
  customers as other parties, and its check digit counted as a confirmation; B-410 cut every ID.
- **Reproduce:** the PDFs of the tests below.
- **Reproduced:** yes
- **Fix:** "CA$", "C$", "CAD", "CDN" anywhere, a Canadian city outside the customer's block, or the US address only on a
  remit-to or lockbox line: no USD ("CA$" is Canadian dollars in `currency_evidence`); only Hawaii's "General Excise
  Tax" is a tax label; a purchaser's, buyer's, carrier's, subcontractor's, recipient's, agent's or broker's number is
  not read, and the nine digits' check digit is a weak check (`BN_CHECK_DIGIT`: a failure marks it down, a pass does not
  lift it, so it stays *check*); only a PO is cut at "/Word".
- **Test:** `tests/test_en_pdf_sweep4.py::test_a_canadian_supplier_banking_in_the_us_is_not_billing_in_us_dollars`,
  `tests/test_en_pdf_sweep4.py::test_an_excise_tax_in_a_fuel_price_is_no_sales_tax`,
  `tests/test_en_pdf_sweep4.py::test_another_party_s_business_number_is_not_the_supplier_s`,
  `tests/test_en_pdf_sweep4.py::test_an_invoice_number_ending_in_a_word_after_a_slash_is_kept_whole`
- **Lesson:** Don't read a number or an address as the supplier's because of its shape: ask whose the page says it is.

#### B-418 · Totals with a deposit or an agency's commission came out likely or verified, where D-47 says a person checks them · medium
- **Where:** `ap_coder/capture/types.py` (new `TAKEN_OFF`); `ap_coder/capture/reader.py` (`PREPAID`, `_solve_totals`,
  `_net_of_commission`, `_read_fields`); `ap_coder/capture/confidence.py` (`fuse`)
- **Symptom:** the review's taxed invoices with "Deposit received (200.00) | Balance Due 421.50": the total likely and
  the deposit verified (the totals check adds them up); the net after a commission ("Net Amount Due") likely.
- **Cause:** `UNMEASURED` is lifted to likely by a check on other fields, and the deposit read as a charge was not marked
  at all when the solver did not use it.
- **Reproduce:** the PDFs of the tests below.
- **Reproduced:** yes (`grand_total likely`, `other_charges verified`)
- **Fix:** a deposit or prepayment read, and every total on a document that prints one, and a total on a document that
  takes a commission off the gross (or the net under the commission), are marked `TAKEN_OFF`: at most 0.8 (*check*),
  "something is taken off before the amount to pay: a person checks it". Whether the gross or the net is posted is
  AP's call.
- **Test:** `tests/test_en_pdf_sweep4.py::test_a_deposit_on_a_taxed_invoice_leaves_the_total_for_a_person_to_check`,
  `tests/test_en_pdf_sweep4.py::test_the_net_after_an_agency_commission_is_checked`
- **Lesson:** Don't let a check on other fields lift a reading whose question is not arithmetic: which amount to post.

#### B-419 · A customer's block level with a right-aligned supplier was read as the supplier, likely · high
- **Where:** `ap_coder/capture/reader.py` (`_vendor_names`)
- **Symptom:** the re-review's sweep of 27 layouts: the supplier right-aligned at the top right ("Northwind Supply
  Ltd." over "123 King Street West"), the customer's block at the top left at about the same height ("Acme Industries
  Inc." over "45 Queen Street"): 7 layouts gave the customer at likely, where main gave the supplier.
- **Cause:** B-399's "name over its street address" bonus (B-414 kept it for the first name on the page) needed the left
  edges lined up, which a right-aligned supplier's lines are not, and "first" was judged by the bottom of the boxes.
- **Reproduce:** the PDFs of the test below.
- **Reproduced:** yes (`'Acme Industries Inc.' likely`)
- **Fix:** the bonus is taken out: a supplier's and a customer's names both sit over their addresses, so it never told
  them apart. (The town under a street address is still no name, B-399.) Distill Mobile's name, which it lifted to
  likely, is *check* again.
- **Test:** `tests/test_en_pdf_sweep4.py::test_a_customer_block_level_with_a_right_aligned_supplier_is_not_the_supplier`
- **Lesson:** Don't add a bonus for something both parties' blocks have.

#### Left as they are (English vendor PDFs)

- **N-130** · A zero printed under "QST/PST" on Microsoft's invoices ("QST/PST 0.00") is read as a QST of 0.00
  (likely, `EITHER_TAX`); the corpus leaves it out, so it counts as spurious. It is what the page prints.
- **N-131** · Payment terms printed as a phrase ("Accounts are due when rendered", "30 Net payment terms", "Pay upon
  receival", "6 month contract") stay missing on 9 of the 36 invoices that print terms: not in this change's scope.
- **N-132** · Where the supplier's name is only in a logo (Owen Sound's water bill, NorthWestern Energy's and Old
  Dominion Power's bills, WSP's letter), the name read is a customer's or a label at *check*, or none; reading a logo
  is not done.
- **N-133** · A media invoice's agency commission is not read as an amount of its own (no field for it); the total is
  the net, as the agency pays it, and the gross is not kept as the subtotal.
### Learning from approvals

Found by reading the later invoices of the 15 repeat vendors among the sweep 4 real invoices (47 invoices after a
vendor's first, 370 printed fields) twice: with the template learned from the vendor's earlier invoices as AP approved
them, and without one. Learning helped (fields right 291 → 297) but replaced a correct, labelled reading with a wrong
one 9 times and marked 12 right values down (verified to likely, likely to check), all where the vendor's layout had
changed. Each fix has a small generated fixture in `tests/test_learning_sweep4.py` (the corpus is not in the repo).

Measured after B-387 to B-393 and B-420 to B-423 (B-420 to B-423 came from reviews with generated English supplier streams): fields
right with learning 297 → 303 of 370 (291 without); on the English invoices among them 72 → 73 of 100 (71 without),
none wrong and shown likely or verified; wrong and shown likely or verified 5 → 6 of 370 (6 without: the value the
rule reader alone shows so); fields worse with learning than without 21 → 4 (N-127, N-128, N-134).
The reviews' 20 streams (a label renamed, an order date printed where the invoice date was, two layouts alternating,
two divisions, a value printed twice, a stub's statement number): no value wrong and verified on any but `freight`
(N-135, the same on the main branch), where the main branch verified a wrong invoice date on 7 invoices in each of two
(`relayout`, `relayout2`); invoices with a wrong value proposed (at check) 16 against the main branch's 32 over 544
invoices, `freight` aside. Learning benchmark (12 suppliers × 30, seed 5): accuracy 100% as before, none wrong among the invoices
that would go touchless (29/57/61/58% before, 25/48/55/58% after: a spot learned from a value printed twice waits for
two approvals). Benchmark (60, seed 3, 50% scans) and the hard corpus (150) unchanged: no template there (99.8% fields,
verified precision 100%; hard corpus 96.9% fields, no silent error 100%).

#### B-387 · A template reading taken without its label replaced a value the page's own label gave · high
- **Where:** `ap_coder/capture/supplier.py` (`_apply_variant`, `apply_template`); `ap_coder/capture/types.py` (new
  `UNSETTLED`); `ap_coder/capture/confidence.py` (`fuse`, `_Group.strength`, new `_vouched_first`,
  `_totals_that_add_up`)
- **Symptom:** When the label a field was learned by is not on the page, the template reads whatever is printed where
  the value used to be. That reading counted as a full template reading (the template is trusted above the rule
  reader) and beat the rule reader's value: a receipt's "Receipt" as the supplier, 299.97 against a receipt's verified
  total of 2,516.28 (marked down to likely). A subtotal filled so also made a coincidental sum add up with the rule
  reader's runner-up tax ("-7,90 + 0,03 - 0,92 = -8,79", the printed tax is -1,12), shown likely.
- **Cause:** `apply_template` marked readings found by their label and readings found by position alike
  (`method="template"`); only the score differed, and fusion weighs the reader, not how it read.
- **Reproduce:** a template learned with "Invoice No:"; the next invoice prints "Ref" instead; fuse with a rule reading
  of another number.
- **Reproduced:** yes (the template's number won)
- **Fix:** a reading the template cannot vouch for, taken at the learned spot without the label it was learned by
  (or, since B-388, at a spot never settled), is marked `template-unsettled` (`UNSETTLED`, an evidence family of its
  own). It never decides: the value chosen is the one the other readers make strongest, and it only adds its
  agreement to it (not as a reader of its own: B-420) or stays a rival that contests it; read by nothing else, it
  fills the field at *check*. It never makes the totals add up by changing another field. A value learned with no
  label at all (a name at the top of the page) is read by its position, as before.
- **Test:** `tests/test_learning_sweep4.py::test_a_reading_at_the_learned_spot_without_its_label_does_not_beat_a_labelled_reading`,
  `tests/test_learning_sweep4.py::test_a_template_reading_without_its_label_does_not_re_decide_the_other_totals`
- **Lesson:** Don't weigh a reader by its name alone: how it read (by its label, or where the value used to be) is
  the evidence.

#### B-388 · A value printed twice was learned at the wrong label · high
- **Where:** `ap_coder/capture/supplier.py` (`learn`, `_best_span`, `_update_variants`, `Variant.settled`)
- **Symptom:** On a vendor's first invoices the delivery date was the invoice date: the template learned the invoice
  date at "Liefer- und Leistungsdatum:" and read the delivery date as the invoice date once they differed (2018-06-03
  for 2018-06-05). A credit note dated and due the same day printed the date once: the due date was learned at "Date
  de facture" and the next invoice's date was read as its due date (2017-11-05 by the template over the verified
  2017-12-05). A credit note without tax taught the total at "Total HT" (and a partial invoice at "Zahlbetrag"): the
  template's rival marked the next invoice's verified total down.
- **Cause:** `learn` looked for the approved value on the page and, of several places, took the one near where it was
  learned before, then by any label, then the first (the last for amounts): not the field's label, and the same words
  for two fields.
- **Reproduce:** two invoices with "Delivery Date:" and "Invoice Date:" both 05.06.2018 learned; a third with another
  delivery date.
- **Reproduced:** yes (`2018-06-03`)
- **Fix:** the template does not guess which place is the field's. A value printed in several places is learned at
  its two likeliest ones. A spot is vouched for after one approval where the value was printed there and nowhere else
  (on words no other confirmed field's value is printed on; a reviewer's click counts too) or two approvals in a row
  with the value there (the invoice number on the header and the stub, alike every time). A spot that holds another
  value than the one approved starts over (a *miss*), and offers nothing until vouched for again; when the template's
  own vouched spots read different values on a page, it vouches for none. Until then what it reads is `UNSETTLED`
  (B-387): it agrees or contests, never decides. Templates stored before start unvouched. A label holding a number of
  three digits or more ("TAX INVOICE #1677326") is no anchor. (The first version of this fix learned where the rule
  reader reads the value; it copied the reader's mistakes, B-421, and was taken out.)
- **Test:** `tests/test_learning_sweep4.py::test_a_date_printed_twice_does_not_settle_where_it_was_learned`,
  `tests/test_learning_sweep4.py::test_one_printed_date_for_two_fields_settles_neither`,
  `tests/test_learning_sweep4.py::test_a_value_printed_twice_alike_is_vouched_for_after_two_approvals`
- **Lesson:** Don't trust a spot learned from a value printed twice: only an approval where the two differ shows
  which one is the field's.

#### B-389 · The supplier's name printed again was learned as the name's label · medium
- **Where:** `ap_coder/capture/supplier.py` (`learn`, new `_name_again`)
- **Symptom:** "CONTOSOLTD." (the logo, glued by OCR) over "CONTOSO" in the address block: the template learned the
  name with the logo as its label, and on the next invoices read the words under the logo as the supplier ("Calle de",
  "Rua de", "Francesco"; read by its label, so trusted over the rule reader).
- **Cause:** any words left of or above a value can be its anchor; for the supplier's name they were the name itself.
- **Reproduce:** learn "CONTOSO LTD." from a page printing "CONTOSOLTD." over "CONTOSO".
- **Reproduced:** yes (an anchor "CONTOSOLTD.")
- **Fix:** an anchor that holds the supplier's name is not kept: the name is learned by its position (B-387 applies).
- **Test:** `tests/test_learning_sweep4.py::test_the_suppliers_name_printed_again_is_not_a_label`
- **Lesson:** Don't take a second printing of a value for its label.

#### B-390 · A tax rate was read as the tax amount · medium
- **Where:** `ap_coder/capture/supplier.py` (`_apply_variant`)
- **Symptom:** "Sales Tax 3%   $311.25": the template read 3.00 as the total tax where the amount was learned (this
  vendor's statements print the rate there), and the totals check failed.
- **Cause:** an amount field took any text that parses as a number.
- **Reproduce:** learn "Sales Tax $10.00"; apply to "Sales Tax 3% $311.25".
- **Reproduced:** yes (`3.0`)
- **Fix:** a value with a % sign is a rate, never an amount.
- **Test:** `tests/test_learning_sweep4.py::test_a_tax_rate_is_not_read_as_the_tax_amount`
- **Lesson:** Don't let "it parses as a number" stand for "it is an amount".

#### B-391 · The learned day/month order was applied to dates printed another way · medium
- **Where:** `ap_coder/capture/supplier.py` (`_apply_variant`)
- **Symptom:** Day first was learned from a till receipt's "10-06-19"; another receipt of the vendor printed
  "6/10/2019" (month first) and the template read it as 2019-10-06, agreeing with the rule reader's unsure reading.
- **Cause:** the variant's date order applied to every ambiguous date it read; with no order learned, an ambiguous date
  came back unparsed.
- **Reproduce:** learn 2019-06-10 from "10-06-19"; apply to "6/10/2019".
- **Reproduced:** yes (`2019-10-06`)
- **Fix:** the learned order settles a date that reads both ways only when it is printed in the form it was learned
  from ("07-06-19"); otherwise the template does not read it and the page's other evidence decides.
- **Test:** `tests/test_learning_sweep4.py::test_the_learned_date_order_holds_only_for_dates_printed_the_same_way`
- **Lesson:** Don't carry an order learned from one form of a date to another form.

#### B-392 · Runner-ups that added up replaced amounts two readers chose first · high
- **Where:** `ap_coder/capture/confidence.py` (`_totals_that_add_up`)
- **Symptom:** subtotal 100,00 and total 110,00, each the first choice of both the rule reader and the template; the
  IVA row (8,00) was a runner-up of each for both, and "8 = 8" added up: both were replaced by 8.00.
- **Cause:** when the first choices do not add up, any combination of the top two candidates that does was taken,
  whatever the readers' agreement on the values it replaced.
- **Reproduce:** fuse the readings above.
- **Reproduced:** yes (`8.0, 8.0`)
- **Fix:** a value no reader chose first does not replace one two readers chose first.
- **Test:** `tests/test_learning_sweep4.py::test_runner_ups_that_add_up_do_not_replace_amounts_two_readers_chose`
- **Lesson:** Don't let a sum that balances by itself (subtotal = total) outvote two readers.

#### B-393 · The template agreeing, or offering a rival, made a right value less certain · low
- **Where:** `ap_coder/capture/confidence.py` (`fuse`)
- **Symptom:** A supplier's name the rule reader read with a rival name under it was likely; with the template agreeing
  it was *check* (5 vendors' names). A total that adds up was verified; with the template's rival (its old spot) it
  was likely.
- **Cause:** the evidence pattern with the template is not measured yet where the one without it is, so the agreeing
  reader fell back to the raw score; and the template's rival counted as a contest though the totals settled it.
- **Reproduce:** fuse the rule reader's name with a rival and the template's agreeing reading.
- **Reproduced:** yes (check)
- **Fix:** the template agreeing never leaves a value less certain than the other readers alone would (as for the page
  reader; this floor stays below verified); a rival only the template read does not contest an amount the totals add
  up with (the reason names it).
- **Test:** `tests/test_learning_sweep4.py::test_the_template_agreeing_never_makes_a_value_less_certain`,
  `tests/test_learning_sweep4.py::test_a_template_rival_does_not_mark_down_an_amount_the_totals_confirm`
- **Lesson:** Don't let one more agreeing reader cost confidence.

#### B-420 · A template reading it could not vouch for counted as a second reader: a wrong date verified · high
- **Where:** `ap_coder/capture/confidence.py` (`fuse`, `_Group.strength`, `_vouched_first`); `ap_coder/capture/types.py`
  (`UNSETTLED`)
- **Symptom:** Found by a review of B-387 with a generated supplier stream (English invoices): the supplier renames
  "Invoice Date:" to "Billed on:" and adds an order "Date:" row under it. The template, its label gone, read the true
  date first and the order date next; the rule reader read the order date. The invoice date was the order date,
  *verified*, and invoices 22 to 29 went touchless (before B-387: check, corrected by AP, and learned again).
- **Cause:** B-387 dropped the template's first choice (it disagreed) and kept its runner-up, which agreed with the
  rule reader's first choice: two readers agreeing, and the evidence pattern read "+unanchored" by its family
  ("template") and took the labelled template's measured record (0.998).
- **Reproduce:** fuse rules [Y 0.6] with the template's [X 0.62, Y 0.55], both without their label.
- **Reproduced:** yes (Y verified, 0.998; X not mentioned)
- **Fix:** such a reading has its own evidence family (`template-unsettled`, not measured), is never counted among the
  independent readers verified needs nor in the evidence pattern, and is not dropped: the template's first choice
  stays a rival and contests the value chosen. The B-393 floor stays below verified. A total tax only the template read
  there gives way to the sum of the taxes read on the page.
- **Test:** `tests/test_learning_sweep4.py::test_a_reading_the_template_cannot_vouch_for_is_never_an_independent_reader`,
  `tests/test_learning_sweep4.py::test_the_template_agreeing_is_never_enough_for_verified`,
  `tests/test_learning_sweep4.py::test_a_renamed_label_does_not_make_the_order_date_a_verified_invoice_date`,
  `tests/test_learning_sweep4.py::test_a_total_tax_only_the_template_read_gives_way_to_the_taxes_on_the_page`
- **Lesson:** Don't let a reading that only agrees count as a reader: agreement from a reader that cannot vouch for
  its spot is no second opinion.

#### B-421 · Learning where the rule reader reads copied the reader's mistake · high
- **Where:** `ap_coder/capture/supplier.py` (`learn`: `_read_where` taken out)
- **Symptom:** Found by the same review: the invoice date printed only beside the title, an order "Date:" row the same
  day on the first invoice. The rule reader read the order date as the invoice date; B-388 learned the invoice date
  there; on the next invoices the rule reader and the template agreed on the order date: verified, touchless (before:
  the template learned the title's date and contested).
- **Cause:** a template that learns where the rule reader reads is not independent of it: it repeats its choice of
  label, mistakes included.
- **Reproduce:** the stream in the test (4 invoices).
- **Reproduced:** yes (2026-01-09 verified, printed 2026-01-12)
- **Fix:** learning no longer follows the rule reader; a spot learned from a value printed twice is unsettled until an
  approval shows it (B-388).
- **Test:** `tests/test_learning_sweep4.py::test_a_date_learned_where_the_reader_read_another_date_is_not_a_second_reader`
- **Lesson:** Don't teach one reader from another and then count them as two.

#### B-422 · A new label printed at an old spot kept the old spot's record · high
- **Where:** `ap_coder/capture/supplier.py` (`_update_variants`)
- **Symptom:** The supplier's new layout prints an order "Date:" where "Invoice Date:" was, and the invoice date beside
  the title; on its first invoice the two were the same day. From the next invoice the order date was the invoice
  date, verified and touchless (before this branch as well).
- **Cause:** the approval matched the old variant by its position and replaced its label; the variant kept the
  confirmations of the old label.
- **Reproduce:** the stream in the test (4 invoices, then the new layout).
- **Reproduced:** yes (the order date verified)
- **Fix:** a new label at a spot, the old one gone from the page, is a new spot with no record (two labels near one
  value, "Billing Date" beside and "Invoice #" over it, are one spot). (The first fix reset the spot's record in place;
  a vendor alternating two layouts then never kept one: B-423.)
- **Test:** `tests/test_learning_sweep4.py::test_a_new_label_at_the_old_spot_starts_unsettled`
- **Lesson:** Don't carry a spot's record across a change of its label.

#### B-423 · A vendor alternating two layouts: the order date verified as the invoice date · high
- **Where:** `ap_coder/capture/supplier.py` (`_update_variants`, `learn`, new `_vouch_once`, `Variant.twin`)
- **Symptom:** Found by a re-review (English supplier stream): even invoices print "Invoice Date:", odd ones "Billed
  on:" at the same spot and an order "Date:" row under it, the order date the invoice date on invoices 1 and 3. On
  invoice 5 the invoice date was the order date, *verified*, and the invoice eligible for touchless (before this
  branch: check); with the two dates equal on every odd invoice up to 21, invoices 23 to 29 went touchless wrong.
- **Cause:** the true spot was matched by its position and relabelled every invoice, so B-422 reset its record each
  time; the "Date:" spot, learned as the other place of a date printed twice, was never missed on the even layout (no
  "Date:" there) and its two coincidences made it the only spot vouched for: it agreed with the rule reader's wrong
  date.
- **Reproduce:** the stream in the test (8 invoices).
- **Reproduced:** yes (the order date verified on invoice 5)
- **Fix:** another label at a spot is another layout's spot with its own record (both labels printed by the value
  still make one spot), so B-422's reset is no longer needed. A place learned from a value printed twice (a *twin*)
  that only approvals in a row vouch for counts only when all the field's twin places are read on the page; when the
  spots it vouches for disagree, those settled by a printed-alone approval decide if they agree, else none.
- **Test:** `tests/test_learning_sweep4.py::test_a_vendor_alternating_two_layouts_never_verifies_the_order_date`,
  `tests/test_learning_sweep4.py::test_a_template_stored_before_the_spots_were_counted_loads_and_recovers`,
  `tests/test_learning_sweep4.py::test_after_a_layout_change_the_settled_new_spot_shows_the_right_date`
- **Lesson:** Don't let one witness stand when its partner has left the page: two places that coincided vouch only
  together.

#### Left as they are (learning from approvals)

- **N-127** · `i2d_qualityhosting`: the template fills the subtotal 34.73 (as the corpus has it); the totals check then
  fails (34.73 + tax 3.89 ≠ 34.73: the printed subtotal is the gross) and marks the total down from likely to check.
  The template read what is printed; the check is right to ask.
- **N-128** · A German terms sentence ("Skontovereinbarung: 2% bei Zahlung innerhalb 10 Tagen nach Rechnungsdatum"):
  the template reads the part it learned ("10 Tagen nach Rechnungsdatum", at check) where the rule reader writes the
  short form ("2% 10 days", also check). Left with B-372's terms work.
- **N-134** · Since B-420 a template reading at a spot it does not vouch for no longer decides, so it no longer corrects
  the rule reader there either: in the review's stream where the order date is the invoice date 4 times in 10, the
  order date is shown (at *check*) on 1 of 30 invoices where the template's title spot held the right date. On the real
  invoices the same rule leaves 2 right values at likely where they were verified (a due date and a total contested by
  the template's unvouched rival). Left: a person corrects a value at check; a wrong value verified is not corrected.
- **N-135** · The review's `freight` stream (merchandise, freight, then "Subtotal" of both): the reader takes the printed
  "Subtotal" (merchandise + freight), AP codes the merchandise as the subtotal and the freight apart; every invoice is
  "wrong and verified" on the main branch and here alike. A coding convention, not a reading or learning question.

### Pilot walkthrough (UI)

The dashboard walked through as the pilot tester will meet it, before the first real invoices: a new data folder, no
LM Studio (no OvisOCR2, no chat model), the name, the GL accounts (the sample ones, and a small CSV imported), the
sales tax mapping, 15 real English vendor PDFs (a credit memo, US invoices, a two-page Microsoft invoice, HST
invoices), corrections, a field taught by clicking, approvals with Ctrl+Enter, JD Edwards and Excel exports,
Learning, Help, Settings, the keyboard alone, a 1280 px window, and bad, empty and repeated files. Screenshots at
1366 x 768 and 1920 x 1080, the browser's console watched (no errors). Pages answered in 0.05 to 0.3 s (Help → Report
a problem: 3 s the first time, B-440); 15 PDFs were read and coded in 6 s. 26 fixed (B-424 to B-449), each with a
failing test first, all in `tests/test_web_pilot_walkthrough.py`. B-438 to B-444 were first logged as left (N-136 to
N-141), then fixed in a second round; B-445 to B-449 are what an independent review of the first two rounds found.
None is left.

#### B-424 · Settings → Review: the threshold's label broke onto three lines · low
- **Where:** `ap_coder/web/templates/settings_review.html` (the *Needs attention* threshold)
- **Symptom:** the label read "Send to" / "*Needs attention*" / "when the AI's confidence is below", one piece per
  line, on the page the welcome screen sends the tester to first (*Add your name*).
- **Cause:** a `.field` label is a flex column; its text and the `<i>` inside it were separate items of it.
- **Reproduce:** open Settings → Review.
- **Reproduced:** yes
- **Fix:** the sentence is one `<span>`. No other `.field` label mixes text and inline tags.
- **Test:** `tests/test_web_pilot_walkthrough.py::test_the_needs_attention_threshold_label_is_one_sentence`
- **Lesson:** Don't put loose text and inline tags in a flex container: each becomes its own box.

#### B-425 · The Shortcuts list was half hidden behind the Document card · low-medium
- **Where:** `ap_coder/web/static/invoice.css` (`.inv-top`)
- **Symptom:** on a 1366 x 768 laptop, *Shortcuts* on the review screen opened under the Document card: "Next field
  to check" and "Stop teaching a field" were covered.
- **Cause:** the header fades in (an opacity animation, so its own stacking context at level 0); the Document card
  is sticky with `z-index: 2`, so it painted over everything the header opened, whatever the list's own z-index.
- **Reproduce:** open an invoice at 1366 x 768, click *Shortcuts*.
- **Reproduced:** yes (screenshot `17_shortcuts_1366.png` of the walk)
- **Fix:** the review screen's header is positioned with `z-index: 25` (above the card, below the approve bar's
  popovers, which open upwards from the bottom).
- **Test:** `tests/test_web_pilot_walkthrough.py::test_in_a_browser_the_shortcuts_list_is_not_hidden_behind_the_document`
- **Lesson:** Don't trust a popover's z-index alone: it only counts inside its ancestors' stacking context.

#### B-426 · After Approve, Park or Reject the next invoice skipped the ones in between · medium-high
- **Where:** `ap_coder/web/pages/invoice.py` (`navigation`, `next_url`, `approve`, `park`, `reject`),
  `ap_coder/web/templates/invoice.html`
- **Symptom:** approving the 4th invoice of 15 ("Reviewing 4 of 15") opened the 8th ("Reviewing 8 of 14"): the
  invoices 5 to 7 were passed over, and a tester working down the queue with Ctrl+Enter would not know.
- **Cause:** each edit refreshes the invoice's confidence in the queue (so the queue shows what the screen shows);
  the queue is ordered by that confidence, so the open invoice moved down while it was edited (27% → 33%, past three
  others), and "the next one" was taken after its new place.
- **Reproduce:** open an invoice that is not last, pick its GL account and cost center, approve.
- **Reproduced:** yes
- **Fix:** the screen carries the invoice that followed it when it was drawn (`next_id`, none for the last one); the
  action opens that one while it is still waiting, else the queue's next as before.
- **Test:** `tests/test_web_pilot_walkthrough.py::test_after_an_action_the_next_invoice_is_the_one_that_followed_on_screen`,
  `tests/test_web_pilot_walkthrough.py::test_approve_also_opens_the_invoice_that_followed_on_screen`,
  `tests/test_web_pilot_walkthrough.py::test_the_last_invoice_offers_no_next_one_to_wrap_to`
- **Lesson:** Don't recompute "what comes next" from an order the user's own edits change: remember what they saw.

#### B-427 · On a laptop the boxes' colours had no key · medium
- **Where:** `ap_coder/web/static/invoice_viewer.css` (the narrow layout of the viewer)
- **Symptom:** at 1366 x 768 (and 1280) the page showed green, blue, amber and red boxes but not the key under the
  toolbar (Verified, Likely, Check, Failed check, Line item): it showed only on screens wide enough for the fields
  list beside the page (about 1400 px and up).
- **Cause:** the viewer's narrow layout hid the legend with the field count (`display: none`), though the same rule
  block also set its compact padding.
- **Reproduce:** open an invoice at 1366 x 768.
- **Reproduced:** yes
- **Fix:** the key stays in the narrow layout (one short line); only the count is hidden.
- **Test:** `tests/test_web_pilot_walkthrough.py::test_in_a_browser_the_colour_key_shows_on_a_laptop`
- **Lesson:** Don't hide the key to a colour code to save a line: the colours mean nothing without it.

#### B-428 · Park, Reject and Approve jumped to the left while a line needed an account · low
- **Where:** `ap_coder/web/static/invoice.css` (`.inv-actionbar-right`)
- **Symptom:** at 1366 px the approve bar wrapped under "Pick a GL account for line 1 and a cost center for line 1 to
  approve" and its buttons moved to the left edge; once picked, they jumped back to the right, under the mouse.
- **Cause:** `justify-content: space-between` puts a wrapped last item at the start of its line.
- **Reproduce:** open an invoice with a line without an account at 1366 x 768.
- **Reproduced:** yes (`16_invoice_credit_1366.png` against `18_credit_coded_1366.png`)
- **Fix:** the buttons' group has `margin-left: auto`: right-aligned on its own line too.
- **Test:** `tests/test_web_pilot_walkthrough.py::test_in_a_browser_approve_stays_at_the_right_while_a_line_needs_an_account`
- **Lesson:** Don't let the main button move when a hint appears beside it.

#### B-429 · Check messages named fields by their code names · low
- **Where:** `ap_coder/web/invoice_view.py` (`readable`, `checks_details`)
- **Symptom:** "Subtotal + tax = 62.00 but grand_total = 0.00", "Line 1: quantity x unit_price = 20.00 but amount =
  40.00" and "… but tax_total = …" under the checks and in *Details and what to do*.
- **Cause:** the messages come from `coding/validation.py` and `coding/tax.py`, written with the code's field names;
  the review screen showed them as they are (the details without `readable` at all).
- **Reproduce:** open `us_arizona_example.pdf` (total read as 0.00).
- **Reproduced:** yes
- **Fix:** `readable` says them as the form does ("the total", "unit price", "quantity × unit price", "the tax
  total"), and the details go through it too. The stored messages (exports, logs, tests) are unchanged.
- **Test:** `tests/test_web_pilot_walkthrough.py::test_check_messages_name_fields_in_plain_english`,
  `tests/test_web_pilot_walkthrough.py::test_the_details_use_the_same_plain_words`
- **Lesson:** Don't show an internal name to a clerk: translate it where the page says it.

#### B-430 · "1 invoice read and coded. They're waiting in the review queue." · low
- **Where:** `ap_coder/web/pages/process.py` (`process_files`)
- **Symptom:** the message after processing one invoice said "They're waiting".
- **Fix:** "It's waiting" for one.
- **Reproduced:** yes (a bad, an empty and a repeated file with one good one)
- **Test:** `tests/test_web_pilot_walkthrough.py::test_one_invoice_read_is_waiting_not_they_are`
- **Lesson:** Don't write a plural verb after a count: it can be 1.

#### B-431 · "5 GL accounts, 1 categories" · low
- **Where:** `ap_coder/web/pages/accounts.py` (the GL accounts tile)
- **Symptom:** after importing a small chart with one category, the tile said "1 categories".
- **Fix:** `ui.plural` (category, categories).
- **Reproduced:** yes
- **Test:** `tests/test_web_pilot_walkthrough.py::test_one_category_is_not_one_categories`
- **Lesson:** Same as B-430.

#### B-432 · Learning showed "UNASSIGNED" struck through as the account corrected · low
- **Where:** `ap_coder/web/templates/learning.html`, `ap_coder/web/core.py` (template global `unassigned`)
- **Symptom:** "Pat Tester corrected Line 2 ~~UNASSIGNED~~ → 6110", in the feed, the top corrections and the memory
  table: the code's word for a line with no account, shown as if it were a GL account.
- **Cause:** without a chat model every line starts with no account; the pages printed the stored value.
- **Reproduce:** approve an invoice after picking an account for a line without one, open Learning & accuracy.
- **Reproduced:** yes
- **Fix:** shown as *no account*.
- **Test:** `tests/test_web_pilot_walkthrough.py::test_a_line_picked_where_nothing_was_suggested_is_not_shown_as_unassigned`
- **Lesson:** Same as B-429.

#### B-433 · The keyboard focus was almost invisible on buttons and links · medium
- **Where:** `ap_coder/web/static/app.css` (`:focus-visible`), `ap_coder/web/static/invoice.css`
- **Symptom:** tabbing through the review screen, a focused button or link had only a faint glow (a 3 px ring of
  the accent at 18%, about 1.3:1 against the page): the tester could not see where the keyboard was. Text boxes
  were fine (their border turns blue).
- **Cause:** the focus style was the text boxes' soft halo, used for everything.
- **Reproduce:** open an invoice and press Tab.
- **Reproduced:** yes
- **Fix:** a 2 px solid ring in the accent colour (5:1 on white) for everything but text boxes, as the page viewer
  already had; the queue's rows draw it inside, the tax and language choices likewise.
- **Test:** `tests/test_web_pilot_walkthrough.py::test_in_a_browser_the_keyboard_focus_is_easy_to_see`
- **Lesson:** Don't use a decorative halo as the focus mark: it must stand out at 3:1 (WCAG 2.4.7, 1.4.11).

#### B-434 · "Pick a GL account for line 1…" said what to do but not where · medium
- **Where:** `ap_coder/web/templates/_invoice_live.html` (`bar`), `ap_coder/web/static/js/invoice.js` (`goToPick`),
  `ap_coder/web/templates/invoice.html` (Shortcuts)
- **Symptom:** on a 1366 x 768 laptop the line grid is two screens below the top; with the keyboard the line's GL
  account was 57 presses of Tab away. The hint in the approve bar was a label.
- **Fix:** the hint is a button that scrolls to the first line still without a GL account or cost center and puts
  the focus in it; G does the same (listed under *Shortcuts*). The approve bar redraws it after each edit.
- **Reproduced:** yes (57 Tabs counted)
- **Test:** `tests/test_web_pilot_walkthrough.py::test_the_pick_hint_is_a_button_to_the_line_that_needs_it`,
  `tests/test_web_pilot_walkthrough.py::test_in_a_browser_approve_stays_at_the_right_while_a_line_needs_an_account`
- **Lesson:** Don't stop at saying what is missing: take the person there.

#### B-435 · The form's field labels said "97%" or "check", never Verified or Likely · medium
- **Where:** `ap_coder/web/invoice_view.py` (`capture_confidence`, `CAPTURE_WORDS`, `CAPTURE_HELP`),
  `ap_coder/web/templates/invoice.html`
- **Symptom:** beside each label: "✓ 99%" (verified), "✓ 97%" (likely), "! 51%" (check) and "⊗ check" (a failed
  check): the words of the page's key (Verified, Likely, Check, Failed check) appeared nowhere in the form, and a
  red "check" read like a green tick's opposite only by its icon. The tooltip said only "How sure AP Coder is of what
  it read".
- **Fix:** "Verified", "Likely 97%", "Check 51%", "Failed check", each with a tooltip saying what it means (as the
  key's tooltips do) and how sure, in figures. The amounts' boxes stay in line when a label wraps under its status.
- **Reproduced:** yes
- **Test:** `tests/test_web_pilot_walkthrough.py::test_each_field_says_how_sure_it_is_in_words`
- **Lesson:** Don't make a person learn two codes for one thing: use the key's own words everywhere.

#### B-436 · On a wide screen the queue cut the reason an invoice needs attention · low
- **Where:** `ap_coder/web/static/app.css` (`.rq-row`)
- **Symptom:** at 1920 px the vendor column took about 750 px while the status column kept 112 px: "Pick a GL account
  for line 1 and a cos…" (two lines, cut).
- **Fix:** the status column takes a share of the extra width (`minmax(112px, 1fr)` beside the vendor's `1.4fr`): at
  1920 px the whole reason shows; at 1366 px both columns keep about what they had.
- **Reproduced:** yes (`15_review_queue_1920.png`)
- **Test:** `tests/test_web_pilot_walkthrough.py::test_in_a_browser_the_queue_gives_the_reason_room_on_a_wide_screen`
- **Lesson:** Don't give all the spare width to one column: give it where text is being cut.

#### B-437 · Nothing asked where invoices are delivered: "Province of supply unknown" on most invoices · medium
- **Where:** `ap_coder/web/pages/process.py` (`setup_steps`, `province_step`, `first_steps`, `STEP_LINKS`),
  `docs/QUICK_START.md`, `docs/GETTING_STARTED.md`
- **Symptom:** after the welcome page's steps, invoices whose ship-to wasn't read said "Province of supply unknown;
  tax regime cannot be verified", and their sales tax went unchecked (4 of the 13 Canadian PDFs on the walk). The
  province that fixes it (Settings → Review, *Your province*) was mentioned by no step or guide.
  With it set (Ontario), `ca_uoft_ex3_gst.pdf` was checked: "GST charged but Ontario normally charges HST".
- **Fix:** *Your province* is a getting-started row on the welcome page and Import invoices, linking to Settings →
  Review, "recommended" until set and then the province's name; it is not counted in the ring (the steps needed stay
  as they were). The Quick start's first step names it.
- **Reproduced:** yes
- **Test:** `tests/test_web_pilot_walkthrough.py::test_getting_started_asks_for_your_province_without_counting_it`
- **Lesson:** Don't leave a setting the checks depend on out of the first steps.

#### B-438 · "about 34 more clean invoices to go touchless": jargon after every approval · low-medium
- **Where:** `ap_coder/capture/supplier.py` (`to_go_text`), `ap_coder/capture/workflow.py` (`supplier_progress`):
  wording only; `tests/test_touchless.py`, `tests/test_touchless_pages.py`, `tests/test_supplier.py` kept in step
- **Symptom:** (was N-137) the message after each approval ended "about 34 more clean invoices to go touchless";
  *touchless* is explained only in Settings → Automation, which the pilot tester has no reason to open.
- **Fix:** "about 34 more invoices approved without a correction before this vendor can skip review"; "too many
  corrections in its recent invoices to skip review yet"; "approved often enough without a correction to skip
  review"; a vendor that meets the bar while the switch is off: "…, once touchless processing is turned on
  (Settings → Automation)", where the name of the setting is needed to find it. The rule behind the figures is
  unchanged. The same words on Learning → Supplier learning and in the guides.
- **Reproduced:** yes (every approval of the walk)
- **Test:** `tests/test_web_pilot_walkthrough.py::test_the_way_to_skipping_review_is_said_in_plain_words`
- **Lesson:** Don't name a feature in a message to someone who has not met it: say what it does.

#### B-439 · A notes file uploaded by mistake became an invoice from "hello" · low
- **Where:** `ap_coder/web/pages/process.py` (`looks_like_an_invoice_text`, `_save_and_start`; the message in
  `tests/test_unusual_files.py` kept in step),
  `ap_coder/web/templates/process.html`, `ap_coder/web/static/js/process.js`
- **Symptom:** (was N-139) the upload takes `.txt` (invoices saved as text, on purpose) but its hint named only PDFs,
  scans, photos and saved emails; a `notes.txt` reading "hello" went to the review queue as an invoice from
  "hello", 0.00 CAD.
- **Fix:** the hint and the messages name text files; a text file with no amount in it (nothing like 125.00) is not
  read: "notes.txt was not read: a text file is read as an invoice only when it has amounts in it (like 125.00), and
  this one has none." (The simpler of the two safe choices: one pattern, no guess at what an invoice says.)
- **Reproduced:** yes
- **Test:** `tests/test_web_pilot_walkthrough.py::test_a_text_file_without_amounts_is_not_read_as_an_invoice`
- **Lesson:** Don't accept a file type quietly: say it is taken, and turn away what cannot be one.

#### B-440 · Report a problem took 3 s the first time with nothing on the screen · low
- **Where:** `ap_coder/web/static/js/app.js` (`a[data-busy]`), `ap_coder/web/templates/help.html`, `error.html`,
  `settings_about.html`
- **Symptom:** (was N-140) the first open of Help → Report a problem took about 3 s (loading the OCR package to
  report on it is about 1 s of it, once per run; 0.05 s afterwards), and the page just sat there.
- **Fix:** a link to a slow page can say so: each *Report a problem* link reads "Gathering the reports…" (dimmed,
  `aria-busy`) until the page comes, and reads as before when the browser's Back returns to it. The 3 s were left:
  the report loads the OCR package to say whether it works, which is its job.
- **Reproduced:** yes (3.0 s measured)
- **Test:** `tests/test_web_pilot_walkthrough.py::test_in_a_browser_report_a_problem_shows_it_is_working`
- **Lesson:** Don't leave a click without an answer for more than a second.

#### B-441 · A US invoice's header said "Province unknown", its Place of supply "Outside Canada" · low
- **Where:** `ap_coder/web/invoice_view.py` (`summary`)
- **Symptom:** (was N-141) `us_arizona_example.pdf` and the demo's Cascade Cloudworks invoice.
- **Cause:** the place of supply is worked out without the ship-to "Outside Canada" (no Canadian tax to place); the
  form said it from the ship-to (`supply_line`), the header did not.
- **Fix:** the header says "Outside Canada" when the ship-to is outside Canada, as the form does.
- **Reproduced:** yes
- **Test:** `tests/test_web_pilot_walkthrough.py::test_a_foreign_invoice_says_outside_canada_in_its_header`
- **Lesson:** Don't work the same fact out twice for one screen.

#### B-442 · On the first day the coding accuracy read "28.6%, −61.4 pts vs 90% target" in red · medium
- **Where:** `ap_coder/web/core.py` (`accuracy_note`, `EARLY_LINES`), `ap_coder/web/pages/review.py` (`kpis`),
  `ap_coder/web/pages/learning.py` (`coding_context`), `ap_coder/web/templates/review.html`,
  `ap_coder/web/static/app.css`
- **Symptom:** (was N-136) without a chat model every line starts with no account and counts as not right (D-39), so
  the first approvals put a red "−61.4 pts" on the review queue and Learning, though nothing wrong was suggested.
- **Fix:** D-39's figure is kept; while it says little (no chat model, or fewer than 50 lines reviewed) the note under
  it is plain and not red: "GL suggestions improve as you approve; with the AI model on they start sooner" (the
  second half only without the model). From 50 lines with the model on, the comparison with the 90% target is shown
  as before.
- **Reproduced:** yes
- **Test:** `tests/test_web_pilot_walkthrough.py::test_early_coding_accuracy_is_a_plain_note_not_a_red_figure`
- **Lesson:** Don't score a measure against its target before it can be fair: say what moves it.

#### B-443 · "Line 2: no GL account yet: pick one" in the checks didn't take you to line 2 · medium
- **Where:** `ap_coder/web/invoice_view.py` (`grouped_issues`, `GOTO_BOX`, `checks_summary`),
  `ap_coder/web/templates/_invoice_live.html` (`status_block`), `ap_coder/web/static/js/invoice.js` (`goToLine`)
- **Symptom:** the findings about a line's GL account or cost center were text, the line two screens down (B-434 made
  the approve bar's hint a button; the same findings in the checks were not).
- **Fix:** each is a button (underlined) to that line's GL account or cost center box, by its line number; "Lines 1,
  3: …" goes to the first. Other findings stay text.
- **Reproduced:** yes
- **Test:** `tests/test_web_pilot_walkthrough.py::test_each_line_without_an_account_in_the_checks_goes_to_its_line`,
  `tests/test_web_pilot_walkthrough.py::test_in_a_browser_a_line_in_the_checks_takes_you_to_it`
- **Lesson:** Same as B-434.

#### B-444 · With LM Studio off the review screen said so three times, the invoice below the fold · medium
- **Where:** `ap_coder/web/pages/invoice.py` (`LM_STUDIO_OFF`, `lm_studio_off`, `folded_reader`, `folded_reviewer`,
  `invoice_page`, `reader_progress`); `tests/test_web_invoice.py` (two tests kept in step)
- **Symptom:** (was N-138) the banner ("LM Studio isn't running…"), then under the document title "OvisOCR2 isn't
  running: see Settings → Reading. It can't be approved without a person until OvisOCR2 has read it." and "The AI
  reviewer checks this invoice against its pages when the chat model is free (after OvisOCR2)…": at 1366 x 768 the
  invoice's picture started 583 px down, behind the approve bar.
- **Fix:** on an invoice being reviewed, while LM Studio itself isn't running, the banner says it once: "LM Studio
  isn't running: a person approves it; OvisOCR2 and the AI reviewer check it once LM Studio runs." (with its *Open
  Settings → Reading* link), and the two notes are left out (also when the OvisOCR2 line is drawn again while it
  waits). Other states (OvisOCR2 missing, testing itself, failed; a read cut off) keep their notes. The picture now
  starts at 508 px, its top in view.
- **Reproduced:** yes
- **Test:** `tests/test_web_pilot_walkthrough.py::test_with_lm_studio_off_the_review_screen_says_it_once`,
  `tests/test_web_pilot_walkthrough.py::test_in_a_browser_with_lm_studio_off_the_invoice_shows_above_the_fold`
- **Lesson:** Don't say one fact in three places on one screen: each copy pushes the work down.

#### B-445 · B-439 turned away real text invoices: Notepad's Unicode files and whole-dollar amounts · high
- **Where:** `ap_coder/web/pages/process.py` (`text_of`, `_AMOUNT`, `looks_like_an_invoice_text`)
- **Symptom:** found by an independent review of this sweep: a text invoice saved by Notepad as "Unicode" (UTF-16,
  read on purpose by `extraction.result_from_text`) and one printing whole dollars ("Subtotal: $100 / GST: $5 /
  Total: $105", "Total 1,250") were refused at upload as having no amounts.
- **Cause:** the check ran a pattern for "digits, a point, two digits" on the raw bytes: UTF-16 puts a zero byte
  between every character, and whole dollars have no cents.
- **Reproduce:** the review's `repro_txt.py` (both refused, 0 queued).
- **Reproduced:** yes
- **Fix:** the bytes are decoded as `result_from_text` decodes them (UTF-16 by its mark, UTF-8, else cp1252), and an
  amount is a number with cents, a number after a currency sign, or a number after a word that names an amount
  (total, subtotal, amount, balance, due, tax, GST, HST, PST, QST, TPS, TVQ, TVH, montant). The message says "a
  text file (.txt, .md)". The check is the upload's only: a text file a person copies into the invoices folder is
  read as before (it was put there as an invoice; the folder pick-up lists what it cannot read).
- **Test:** `tests/test_web_pilot_walkthrough.py::test_a_real_text_invoice_is_still_read` (UTF-16, whole dollars,
  thousands, ANSI), `tests/test_web_pilot_walkthrough.py::test_a_markdown_file_without_amounts_is_refused_as_a_text_file`
- **Lesson:** Don't test a file's content as bytes when the reader decodes it: check what the reader will see.

#### B-446 · The walkthrough tests left "Pat Tester" as the reviewer for the rest of the run · medium (tests only)
- **Where:** `tests/test_web_pilot_walkthrough.py` (the `me` fixture, the imports)
- **Symptom:** `pytest tests/test_web_pilot_walkthrough.py tests/test_web_robustness.py::test_approving_in_a_second_tab_says_plainly_it_was_approved_meanwhile`
  failed: the second test's approval was recorded under "Pat Tester".
- **Cause:** the fixture patched `web.core.reviewer` before the page modules were first imported; they then copied
  the patched function, and monkeypatch, restoring what it had found there, kept it as theirs.
- **Fix:** the page modules are imported at the top of the file, before any patch.
- **Reproduced:** yes
- **Test:** the pair above, run in that order; the web tests run in two orders (walkthrough first and last).
- **Lesson:** Don't patch a name in a module whose importers haven't imported it yet: they copy the patch.

#### B-447 · Without a chat model B-442's note replaced the coding accuracy's comparison for good · low
- **Where:** `ap_coder/web/core.py` (`accuracy_note`), `ap_coder/web/pages/review.py`, `ap_coder/web/pages/learning.py`
- **Symptom:** found by the review: with no chat model the distance to the 90% target never showed again, however
  many lines were reviewed.
- **Fix:** the first 50 reviewed lines (`EARLY_LINES`) get the plain note in every case; after them the distance
  shows, followed by "GL suggestions improve as you approve" while there is no chat model (D-49).
- **Reproduced:** yes
- **Test:** `tests/test_web_pilot_walkthrough.py::test_the_accuracy_note_gives_way_to_the_figure_after_the_first_lines`
- **Lesson:** Don't make "early on" depend on a setting alone: give it an end.

#### B-448 · Supplier learning and Automation still said "go touchless" and "clean in a row" · low
- **Where:** `ap_coder/capture/supplier.py` (`describe`, `plain_rules`, `policy_gaps`, `autonomy_status`,
  `automatic_transition`: wording only), `ap_coder/web/templates/learning.html`,
  `ap_coder/web/templates/settings_automation.html`, `ap_coder/web/pages/settings_automation.py`,
  `ap_coder/web/pages/learning.py`; `tests/test_supplier.py`, `tests/test_touchless_pages.py`,
  `tests/test_web_settings.py` kept in step
- **Symptom:** found by the review: after B-438 the approval message said "…before this vendor can skip review",
  while Learning → Supplier learning said "6 more clean in a row needed", "about 32 more clean invoices for a 99%
  accuracy bound", "Goes touchless on its next invoice", "a person found an error in a touchless invoice", and a
  suspended vendor's line could end "…to skip review yet again.".
- **Fix:** the same words throughout: "N more in a row without a correction needed", "about N more invoices without
  a correction to show 99% accuracy", "Skips review from its next invoice", "Skips review since …", "an invoice that
  skipped review"; "again" only after a number. *Touchless processing* stays the name of the setting (Settings →
  Automation), and the tiles there read "Vendors skipping review", "Skipped review, last 30 days", "Errors found
  after skipping review". The rules are unchanged.
- **Reproduced:** yes
- **Test:** `tests/test_web_pilot_walkthrough.py::test_supplier_learning_and_automation_use_the_same_plain_words`
- **Lesson:** Don't change a term in one message: change it everywhere the same person reads it.

#### B-449 · "Gathering the reports…" stayed for good when the page never came · low
- **Where:** `ap_coder/web/static/js/app.js` (`a[data-busy]`, `resetSlowLink`)
- **Symptom:** found by the review: with AP Coder stopped, the link kept saying it was gathering the reports.
- **Fix:** after 15 s without the page the link reads as before (and on Back, as before).
- **Reproduced:** yes
- **Test:** `tests/test_web_pilot_walkthrough.py::test_in_a_browser_a_slow_link_reads_as_before_when_the_page_does_not_come`
- **Lesson:** Don't show "working…" without an end: a request can fail silently.

### Pilot path (backend)

The path the pilot tester takes with real English vendor PDFs, from the import to the export, read and then run end to
end (48 English vendor PDFs processed, reviewed and approved through the dashboard's routes, exported as JD Edwards,
Excel, CSV and custom CSV): each suspicion was reproduced before it was called a bug.

#### B-453 · A backup that could not be written stopped the dashboard from starting · medium
- **Where:** `ap_coder/core/store.py` (`backup_to`), `ap_coder/web/pages/settings.py` (`restore_backup`)
- **Symptom:** with the disk full (or the backups folder failing to write), the daily backup made as the dashboard
  starts raised `sqlite3.OperationalError: disk I/O error` and the dashboard did not open at all; the half-written
  copy was left in `backups/`, listed to restore, and taken for today's backup, so the next start made none. A restore
  whose safety copy failed showed an error page.
- **Cause:** the dashboard and the Settings page catch `OSError` for a backup that fails ("a backup problem must never
  stop the dashboard from opening"); SQLite's errors are not `OSError`, and the copy was written under its final name.
- **Reproduce:** start the dashboard with the process's file size limit below the database's size (a full disk).
- **Reproduced:** yes (the dashboard did not start; `ap_coder-<date>.db` left behind)
- **Fix:** the copy is written as `<name>.part` and renamed once complete; any failure removes it and raises `OSError`
  with SQLite's reason, so every caller's existing handling applies; a restore whose safety copy fails says so. A
  `.part` a crash left behind is removed by the next backup once it is a day old (the review's follow-up).
- **Test:** `tests/test_pilot_path_backend.py::test_a_backup_that_cannot_be_written_never_stops_the_dashboard_nor_counts_as_made`,
  `tests/test_pilot_path_backend.py::test_a_part_backup_left_by_a_crash_is_removed_when_backups_run`
- **Lesson:** Don't name a file for what it will be before it is: a copy that fails half-way must not look made.

#### B-454 · An invoice approved again taught the supplier's template a second time · medium
- **Where:** `ap_coder/capture/supplier.py` (`learn`, `Template.documents`), `ap_coder/capture/workflow.py`
  (`learn_from_approval`)
- **Symptom:** an invoice approved, reopened for correction (or sent back by the second approver) and approved again
  counted as two invoices in its supplier's template: a spot learned unsettled from it (one printed date approved as
  both the invoice date and the due date, a value printed twice) had "two approvals in a row" from that one page and
  was vouched for. On the supplier's next invoice the template read the invoice date as the due date, and the fused
  due date became the template's 2017-11-05 instead of the labelled 2017-12-05 (*check* either way). The template's
  invoice count went up by two.
- **Cause:** a reopen or a send-back withdraws the coding memory, lessons and outcomes (B-14 and later), but the
  template is a running record with no way to take one page back out, and nothing told it the page was seen before.
- **Reproduce:** approve, reopen, approve the same PDF (dated and due the same day), then apply the template to an
  invoice printing a due date.
- **Reproduced:** yes (the due date spot vouched; fused due date 2017-11-05)
- **Fix:** the template keeps the pages it learned from (the file's SHA-256, the newest 100) and the value of each field
  learned from each: approved again, a page teaches only a value approved differently since (a correction: the spot of
  the value approved first is a miss as before) or one the reviewer clicked; it no longer counts as another invoice.
- **Test:** `tests/test_pilot_path_backend.py::test_an_invoice_approved_again_teaches_the_template_once`,
  `tests/test_pilot_path_backend.py::test_a_value_corrected_on_an_invoice_approved_again_is_still_learned`
- **Lesson:** Don't count a second look at the same page as a second witness.

#### B-455 · An invoice of about 90 lines or more could not be checked or approved · high
- **Where:** `ap_coder/web/pages/invoice.py` (`_posted`), `ap_coder/web/pages/exports.py` (`create_batch`),
  `ap_coder/web/core.py` (new `posted_form`)
- **Symptom:** a long invoice (a distributor's parts list, a phone bill: 150 lines) opened on the review screen, but
  every check, line action (split, apply rules, fill) and Approve answered "Too many fields. Maximum number of fields
  is 1000.": it could not be approved at all. The Exports page refused a batch of more than about 1,000 ticked
  invoices the same way.
- **Cause:** the screen posts some ten fields per line (number, description, amount, GL, cost center, quantity,
  price, reason, key, ticked taxes); Starlette's form parser refuses more than 1,000 fields by default.
- **Reproduce:** a 150-line invoice in the queue, posted as the review page posts it.
- **Reproduced:** yes (2,159 named fields on the page for 150 lines)
- **Fix:** the review screen's form and the Exports page's ticked invoices are read with a limit of 200,000 fields.
- **Test:** `tests/test_pilot_path_backend.py::test_an_invoice_of_a_hundred_and_fifty_lines_can_be_checked_and_approved`
- **Lesson:** Don't leave a framework's default limit standing on a form whose size grows with the data.

#### B-456 · The review queue's search missed vendors written with accents · low
- **Where:** `ap_coder/web/pages/review.py` (`filter_queue`, `sort_queue`)
- **Symptom:** typing "quebec" or "energir" in the review queue's search found no "Hydro-Québec" or "Énergir" (only
  the accented spelling did), and the review screen's previous / next followed the same filter; "Vendor A–Z" put
  "Énergir" after "Zenith Supply".
- **Cause:** the search and the order compared lower-cased text; the folding the rest of AP Coder uses for names
  (`store.fold_text`, B-04) was not used here.
- **Reproduce:** `filter_queue([{"vendor_name": "Hydro-Québec"}], "quebec")`.
- **Reproduced:** yes (`[]`)
- **Fix:** both fold accents and case as the vendor and invoice filters elsewhere do.
- **Test:** `tests/test_pilot_path_backend.py::test_the_queue_search_and_vendor_order_read_accents_as_people_do`
- **Lesson:** Don't compare names with `lower()`: fold them the way the rest of the app does.

#### B-457 · The JD Edwards pay item sent a PO company without its PO number · low
- **Where:** `ap_coder/erp/jde.py` (`voucher_rows`), docs/JDE_E1.md
- **Symptom:** with *Send the PO number as a reference* off, an invoice quoting a PO went out with VLPO and VLPDCT
  blank but VLPKCO (the PO company) filled: a PO key with no PO, unlike the mapping in docs/JDE_E1.md.
- **Cause:** VLPO and VLPDCT followed the setting; VLPKCO only asked whether the invoice printed a PO.
- **Reproduce:** `jde.export_batch([the Northwind invoice], JdeSettings(send_po_reference=False), ...)`.
- **Reproduced:** yes (`VLPKCO = 00001`)
- **Fix:** the three PO columns go out together, when the PO number is sent (as a reference, or PO-matched).
- **Test:** `tests/test_pilot_path_backend.py::test_the_po_company_goes_out_only_with_the_po_number`
- **Lesson:** Columns that make one key are filled by one condition.

#### B-458 · What capture found about the page never showed on the review screen · medium
- **Where:** `ap_coder/web/invoice_view.py` (`analyse`), `ap_coder/capture/bridge.py` (`review_issues`)
- **Symptom:** "only 20 of its 300 pages were read: check the other pages yourself before approving" (B-267), "the
  currency isn't printed clearly: taken as USD, check it", "as printed, the totals don't add up", "check on the page:
  …", "line items read differently" were kept with the invoice when it was read (they flagged it in the queue and
  kept it from bulk approval), but the review screen, where the person decides, showed none of them: on the real
  English PDFs, `us_gcp_mcwilliam` was flagged for its currency and its screen never said why.
- **Cause:** the review screen checks the coding again as it is edited (`finalise_coding`), and these findings come
  from what capture read, not from the coding; only the AI reviewer's findings were added back. B-267's test checked
  the pipeline's report, not the screen.
- **Reproduce:** an invoice whose stored capture left 280 of 300 pages out; open it, check it.
- **Reproduced:** yes (neither the page nor the check shows it)
- **Fix:** the review screen adds the findings about the page from the stored capture to its checks (warnings and
  notes: Approve stays open), worked out again for the screen's currency and date. "Check on the page: …" is left
  out there: it is on nearly every invoice, and its fields are highlighted in amber (the review's follow-up).
- **Test:** `tests/test_pilot_path_backend.py::test_what_capture_found_about_the_page_is_on_the_review_screen`,
  `tests/test_pilot_path_backend.py::test_the_review_screen_leaves_the_fields_to_check_to_their_highlight`
- **Lesson:** Don't test a message for the screen anywhere but on the screen.

#### B-459 · An invoice with no date found was dated the day it was read, without a word · medium
- **Where:** `ap_coder/capture/bridge.py` (`review_issues`), `ap_coder/ai/pipeline.py`, `ap_coder/ops/help.py`
- **Symptom:** an invoice whose date no reader found got the day it was read as its invoice date (so its due date,
  G/L period and sales tax period), shown on the review screen like a date read from the page (no "how sure" mark,
  no check); 4 of the 48 English PDFs (`ca_ubc_google` was not even flagged: Ready in the queue, offered for bulk
  approval once its cost center is known), 2 of them with a due date years before that "invoice date".
- **Cause:** the offline coder puts in today's date ("capture already flags the date as missing"), but capture's
  findings name only the fields to check, never a missing one.
- **Reproduce:** process a PDF that prints no date.
- **Reproduced:** yes (invoice date today, no issue, requires_review false)
- **Fix:** a warning, INVOICE_DATE_NOT_FOUND ("no invoice date was found on the page: <date> is assumed (the day it
  was read); put in the invoice's date…"), with its Help entry; it is kept with the invoice (which keeps it from
  bulk approval) and shown on the review screen until the reviewer puts in another date, shows the date on the page,
  or approves it with that date (reopened or sent back, it is no longer said: the date was confirmed).
- **Test:** `tests/test_pilot_path_backend.py::test_an_invoice_with_no_date_found_says_the_date_was_assumed`
- **Lesson:** Don't fill a missing value with a default without saying so where the person decides.

#### B-460 · B-454's fix settled a corrected value printed on another field's words · high
- **Where:** `ap_coder/capture/supplier.py` (`learn`)
- **Symptom:** found by the independent review of this branch. A page prints only "Date: 05/11/2017"; approved with
  the due date 2017-12-05 (not printed), reopened, the due date corrected to 2017-11-05 (due on receipt) and approved
  again: the due date was learned at the invoice date's words as *settled*, and on the next invoice the template
  read the invoice date as the due date, vouched for (on the main branch: unsettled). And a page approved with one
  value, then another, then the first again counted twice for the first.
- **Cause:** B-454 left the values learned already out of the page's search, so a corrected value sharing their
  words looked printed alone; and each page remembered only the last value of each field.
- **Reproduce:** the review's probes (`p454.py`, `p454b.py`), as in the tests.
- **Reproduced:** yes (due date read at the invoice date's spot, not unsettled; a spot counted 2 for one page)
- **Fix:** every value approved is looked for on the page (those learned already included), and only the ones not
  learned from this page before are learned; the page keeps every value of each field it taught.
- **Test:** `tests/test_pilot_path_backend.py::test_a_value_corrected_onto_another_fields_words_is_not_settled_there`,
  `tests/test_pilot_path_backend.py::test_a_page_approved_with_a_value_then_another_then_the_first_again_counts_it_once`
- **Lesson:** When a fix skips work, keep what the skipped work told the rest.

#### B-461 · B-455 left other long forms at Starlette's 1,000 fields · medium
- **Where:** `ap_coder/web/pages/accounts.py`, `review.py` (`review_bulk_approve`), `learning.py` (`memory_forget`),
  and every other page reading a posted form (`exports`, `purchase_orders`, `vendors`, `settings_jde`, `insights`)
- **Symptom:** found by the independent review. Saving a GL list of 400 accounts on GL accounts & tax (three fields an
  account: a JD Edwards chart of 334 or more) answered "Too many fields"; so did bulk approval of 1,200 invoices and
  forgetting 1,200 lessons.
- **Cause:** B-455 used `posted_form` on the review screen and Exports only; FastAPI's `Form` lists use the default.
- **Reproduce:** the review's probe (`p455.py`).
- **Reproduced:** yes (400 "Too many fields")
- **Fix:** every page reading a posted form reads it with `posted_form`; bulk approval and forgetting lessons read
  their ticked ids from it.
- **Test:** `tests/test_pilot_path_backend.py::test_long_lists_are_saved_on_every_page_that_posts_them`
- **Lesson:** A limit raised in one place: look for every place that has it.

#### B-462 · A value put back on a page approved again left the corrected-to spot vouched for · high
- **Where:** `ap_coder/capture/supplier.py` (`learn`)
- **Symptom:** found by the second review of this branch. A page prints "Invoice Date" and "Order Date" 2026-03-05 and
  "Ship Date" 2026-03-07; approved with 2026-03-05, reopened and changed to 2026-03-07, then reopened and put back to
  2026-03-05: on the next invoice the template read the ship date as the invoice date, vouched for (0.855; on the main
  branch nothing was vouched).
- **Cause:** B-460 has the page remember every value it taught a field; the value put back counted as taught and was
  skipped whole, so the misses were not looked for and the ship date's spot, settled by the correction, kept its mark.
- **Reproduce:** the review's probe (`p454b.py`), as in the test.
- **Reproduced:** yes (the ship date read, not unsettled)
- **Fix:** every approved value is checked for misses (a spot holding another value starts over), the ones taught by
  the page before included; only learning them again is skipped.
- **Test:** `tests/test_pilot_path_backend.py::test_a_value_put_back_on_a_page_approved_again_still_misses_the_spot_corrected_away`
- **Lesson:** Skip the counting, never the checking.

#### Looked at and left (pilot path, backend)

Run as well, and found to work: approving twice (a double click: the second is "changed meanwhile"), the second
approval and its send-back with a corrected field (learning withdrawn and learned once again, the export holds the
correction), a credit note through the checks, the approval limit and every export, splits of negative and tiny
amounts (exact to the cent), the JD Edwards files of all 47 approved English PDFs in every mode (pay items add up to
the invoice; a 150-line invoice included), an invoice whose file was deleted after import (review, approval,
approved PDF and exports all work and say the file is gone), a missing GL list (the review screen says to import it),
formula-looking vendor names in the Excel export (stored as text), and the import job reading 48 PDFs while invoices
were approved, parked and exported and a backup was made every 2 seconds (no lock, no lost write).

- **N-165** · A PDF renamed in the invoices folder after it was imported is not found again (`paths.find_moved_file`
  looks for the same name, then checks the SHA-256): its review screen says the file is no longer where it was and
  shows no page; approval and the exports still work. Finding it by content would read every file in the folder.
- **N-166** · `python -m ap_coder process <folder>` sets upright, in place, a PDF whose text is drawn sideways (D-47)
  and keeps the file as it came in an `originals/` folder beside it, also in a folder outside the data folder (it
  changed 3 files of the test corpus here, put back since). The dashboard copies what it reads into its own invoices
  folder first; the command line is for developers.
- **N-167** · Two approvals of the same supplier at the same moment (two computers on the office network) each read,
  update and save the supplier's template: one of the two lessons can be lost. Not reproduced (one pilot tester).
- **N-168** · A GL account the coder left blank and AP filled in counts as a correction for the lessons, so the
  "why" question comes after 3 invoices of a vendor imported together before any was approved (GCstrategies on the
  English PDFs), where D-39 counts filling a blank as no change. The lesson is also what tells the AI the account,
  so whether a filled blank asks "why" is a design question: left.
- **N-169** · The approval limit typed with a thousands separator ("5,000") is refused ("must be numbers"): said, not
  saved wrong.
- **N-170** · The review queue's "Amount: high to low" compares totals in different currencies as they are.
- **N-171** · Capture's findings (B-458, B-459) are added after the confidence is worked out: a warning about the page
  keeps an invoice from bulk approval, but does not by itself flag it in the queue
  (Ready means Approve is open, which a warning does not close).

### Before testing: review screen and coding

A product review before the pilot tester's first week, on the set-up they will use: LM Studio chosen as the chat
model (and often not running yet), English vendor PDFs, no cost centers, Excel/CSV exports. Fifteen of the English
PDFs imported into a scratch data folder with the sample GL accounts and screenshot at 1366×768.

#### B-463 · With LM Studio chosen but not running, an account's name was put in the line as if learned · high
- **Where:** `ap_coder/ai/pipeline.py` (`_process`, `_local_accounts`), `ap_coder/ai/offline_coder.py`
  (`code_from_capture`), `ap_coder/core/store.py` (`approve_invoice`), `ap_coder/web/invoice_view.py`
- **Symptom:** Google → 6320 Meals, AWS → 6110 Consulting, an IT consultancy → 6100 Legal, a $281k Microsoft bill →
  1500 Computer Equipment: an account whose name shares a word with the line was filled in the GL dropdown, looked
  like a learned code, left Approve open, and counted as "coded as suggested" in coding accuracy; Month-end accrued
  on it. README and `offline_coder`'s own docstring say such a weak match is left blank and only suggested.
- **Cause:** the pipeline coded with `assign_weak=provider == "local"`, and the provider is always "local" in this
  offline build, whether or not LM Studio answers; the chat model was then asked only about lines with no
  suggestion at all. The approval counted any account the AI put in a line as its proposal.
- **Reproduce:** sample GL accounts, LM Studio at an address nothing answers, import `ca_ubc_google.pdf`,
  `ca_oc_aws_10002100.pdf`: lines coded `WEAK_MATCH`, Approve open, "all as suggested".
- **Reproduced:** yes (9 of 15 invoices had a weak match put in a line)
- **Fix:** a weak match is never put in a line: it stays blank with "Not sure enough to code it (maybe 6110 …)" and
  the review screen's suggestion, and is kept as a hint (`CodingResult.hints`). The chat model, when it answers, is
  asked about these lines too; its account says whether the account's name agrees or pointed elsewhere. An account
  no approval backs (the model's, or an older version's weak match: `offline_coder.guessed`) is marked on the review
  screen ("A guess, not learned yet: check it"), the approve bar says "1 guessed account to check" instead of "all
  as suggested", and when the reviewer keeps it, it counts as the reviewer's coding (`store.proposed_coding`: as if
  left blank), never as "coded as suggested" in accuracy or the lessons (D-51).
- **Test:** `tests/test_trust_review.py::test_with_lm_studio_chosen_but_not_answering_a_weak_match_is_left_blank`,
  `::test_the_chat_models_answer_is_a_guess_and_says_how_it_compares_with_the_weak_match`,
  `::test_a_kept_guess_is_the_reviewers_coding_not_coded_as_suggested`,
  `::test_the_review_screen_marks_a_guess_and_does_not_call_it_as_suggested`
- **Lesson:** "Is the model configured?" is not "did the model answer?": decide per invoice, from what happened.

#### B-466 · Month-end accrued guesses, mixed CAD and USD rows, and said "already in the ERP" with nothing imported · medium
- **Where:** `ap_coder/erp/accruals.py`, `ap_coder/web/pages/month_end.py`, `ap_coder/web/templates/month_end.html`
- **Symptom:** invoices nobody had approved were accrued on their weak-match accounts with nothing to say so; the
  by-account table listed one account twice (once in CAD, once in USD); an invoice dated 2012 was accrued without a
  word; with no invoice and no register at all the page said "Everything incurred … is already in the ERP".
- **Cause:** the accrual read any account in the coding; `by_gl` keyed on (account, currency); no date check; one
  empty-state sentence for every case.
- **Reproduce:** the 15 PDFs above, Month-end at the default period end; a fresh data folder's Month-end.
- **Reproduced:** yes
- **Fix:** an invoice in review or parked is accrued as "proposed: not approved yet" (a "Proposed" tag on screen,
  the note in the CSV), on the accounts it was really given (a guessed account counts as none: "Needs an account");
  the by-account table has one row per account and a column per currency; an invoice dated more than a year before
  the period end was left out and listed (since B-474: accrued, flagged "Check date");
  the empty state says what is true (nothing imported yet, or everything dated by then exported).
- **Test:** `tests/test_trust_review.py::test_month_end_accrues_an_unapproved_invoice_as_proposed_and_never_on_a_guess`,
  `::test_month_end_lists_an_invoice_dated_over_a_year_back_instead_of_accruing_it`,
  `::test_month_end_groups_an_account_in_two_currencies_on_one_row`, `::test_month_end_with_nothing_imported_says_so`
- **Lesson:** An empty state is a claim too: check it against an empty database.

#### B-464 · The checks spoke the code's language: "invoice_number is empty", VENDOR_NEW, UNASSIGNED · medium
- **Where:** `ap_coder/coding/validation.py`, `ap_coder/coding/tax.py`, `ap_coder/core/ui.py` (`check`),
  `ap_coder/ai/lessons.py`, `ap_coder/web/templates/learning.html`, `ap_coder/core/audit.py`,
  `ap_coder/capture/line_items.py` (`sources_line`)
- **Symptom:** the review screen said "Invoice_number is empty", "Vendor_name is empty", "GL code '9999' is not in
  the GL accounts", "line items sum to … but subtotal = …", "prebuilt-invoice InvoiceTotal = …, model grand_total =
  …"; "Details and what to do" showed VENDOR_NEW, CC_UNASSIGNED beside each title; Learning → Lessons said "read as
  UNASSIGNED" and Activity "UNASSIGNED → 6110"; the document card said "Line items read by one reader only (1 from
  the PDF's table): not yet compared with a second reader."
- **Cause:** the messages were written for developers; `readable()` only replaced a few field names.
- **Reproduce:** the 15 PDFs; an invoice with no number; Details and what to do.
- **Reproduced:** yes
- **Fix:** plain sentences that say what to do ("No invoice number: type it from the page", "GL account 9999 is not
  in your GL accounts: pick one from the list", "the lines add up to … but the subtotal is …", "Azure read the total
  as …, AP Coder as …", "the scan is hard to read (62% sure of its words): check the values against the page"); a
  check's code is its hover (`title`) and stays in the problem report; a blank account reads "left blank" / "blank";
  the one-reader note is gone (nothing to act on; the queue says when OvisOCR2 has not read an invoice).
- **Test:** `tests/test_trust_review.py::test_the_checks_say_what_is_missing_in_plain_words_and_keep_their_codes_for_a_hover`,
  `::test_a_lesson_on_a_blank_account_reads_left_blank_not_unassigned`; `tests/test_table_reading.py` (the note)
- **Lesson:** Read every message as the pilot tester would: a field name with an underscore is a bug.

#### B-465 · What kept Approve closed was "Worth a look", not counted in "N must be fixed" · medium
- **Where:** `ap_coder/web/invoice_view.py` (`checks_summary`, `checks_details`), `ap_coder/web/templates/_invoice_live.html`
- **Symptom:** "2 must be fixed · 3 to look at", with "Line 1: no GL account yet: pick one" among the three to look
  at, though it is what disabled Approve; Details called it "Worth a look".
- **Cause:** the summary grouped by severity, and a line without an account is a warning (it lowers the confidence
  less than an error), while the approve bar blocks on it.
- **Reproduce:** BDO's invoice in the 15 PDFs.
- **Reproduced:** yes
- **Fix:** three groups, each under its heading: "Before you can approve (N)" (an account or cost center to pick:
  each a link to its box, "Press G to go to the first one"), "Must be fixed (N)", "Check before approving (N)"
  (the warnings: an assumed invoice date, INVOICE_DATE_NOT_FOUND, sits there plainly). Details uses the same words.
- **Test:** `tests/test_trust_review.py::test_what_keeps_approve_closed_is_its_own_group_before_the_errors_and_warnings`
- **Lesson:** Group by what the person can do next, not by the code's severity.

#### B-467 · At 1366 × 768 the review screen showed 150–200 px of the invoice and the lines 1,200 px down · high
- **Where:** `ap_coder/web/templates/invoice.html`, `_invoice_lines.html`, `ap_coder/web/static/invoice.css`,
  `invoice_viewer.css`, `js/invoice_viewer.js`, `js/invoice.js`, `ap_coder/web/pages/invoice.py`
- **Symptom:** breadcrumb, a navigation row, the LM Studio banner and the summary pushed the document card down;
  in it the field list, a toolbar on two rows and the legend left 150–200 px of the page on screen; the line grid
  came after the invoice details, about 1,200 px down, and Qty / Unit price scrolled sideways.
- **Cause:** a layout made for a wide screen; the banner (B-444) stood in for two notes but cost a row.
- **Reproduce:** any invoice of the 15 PDFs at 1366 × 768 (`shots/before/review_*_vp.png`).
- **Reproduced:** yes
- **Fix:** one row at the top (Queue · Reviewing 3 of 15 · a short "LM Studio isn't running" note · Shortcuts ·
  Previous · Next), no banner on this page (the queue has it); the document and the column beside it half and half,
  the document sticky and taller (the viewer is the window less 190 px); the viewer's legend is in its toolbar, as
  swatches with counts; the line grid sits right under the checks, each line on two rows (description, which wraps,
  and taxes; then GL account, cost center, qty, unit price, amount), nothing scrolls sideways; the invoice details
  follow. Ctrl+Enter, G, N, Alt+arrows work as before; Enter in a description adds no line break.
- **Test:** `tests/test_web_pilot_walkthrough.py::test_in_a_browser_at_1366_the_page_and_the_line_grid_show_together`,
  `::test_with_lm_studio_off_the_review_screen_says_it_once`
- **Lesson:** Check the screen at the pilot tester's resolution, not the developer's.

#### B-468 · Demo invoices mixed into the real numbers · medium
- **Where:** `ap_coder/web/pages/process.py`, `ap_coder/web/templates/process.html`
- **Symptom:** after trying the demo and importing real invoices, "Demo reviewer" was among Activity's people, the
  demo's lessons counted in coding accuracy and the demo's vendors were listed with the real ones.
- **Cause:** nothing asked to remove the demo before the first real import (the demo card sits lower on the page).
- **Reproduce:** load the demo, import a PDF: no word about the demo.
- **Reproduced:** yes
- **Fix:** the simpler safe one: while the demo is loaded and no invoice of your own is, Import invoices says
  "Remove the demo invoices before you import your own" with the button that removes them (as the demo card does).
  Nothing is removed without the click.
- **Test:** `tests/test_trust_review.py::test_the_import_page_asks_to_remove_the_demo_before_the_first_real_invoice`
- **Lesson:** Practice data needs a clear way out at the moment real data comes in.

#### B-469 · "Overdue 5042 days", and an invoice dated years back said nothing · medium
- **Where:** `ap_coder/coding/terms.py` (`describe`, `payment_findings`), `ap_coder/web/pages/review.py` (`due_note`),
  `ap_coder/ops/help.py`
- **Symptom:** the queue and the review screen counted days for a bill due in 2012; an invoice dated 2017 (a misread
  date, or a very late bill) raised no warning.
- **Cause:** days were the only unit; no check on the invoice date's age.
- **Reproduce:** `ca_uoft_ex3_gst.pdf` (dated 2017) among the 15 PDFs.
- **Reproduced:** yes
- **Fix:** past 60 days, "Overdue since Dec 2012" (queue, review screen, the overdue note); a new warning,
  OLD_INVOICE_DATE ("dated 2017-10-05, more than a year ago: check the date on the page"), in "Check before
  approving", with its Help entry; Month-end flags such invoices (B-466, B-474). Since B-476 it is information only.
- **Test:** `tests/test_trust_review.py::test_a_long_overdue_bill_says_since_when_and_an_old_invoice_date_is_flagged`
- **Lesson:** A number nobody can picture ("5042 days") is a sign the value itself needs a check.

#### B-470 · The Excel export's dates were text · low
- **Where:** `ap_coder/erp/exports.py` (`_sheet`)
- **Symptom:** Invoice date, Due date and Approved at were text cells: Excel would not sort or filter them as dates.
- **Cause:** the ISO strings were written as they are.
- **Reproduce:** export the approved invoices as Excel, open the Invoices sheet.
- **Reproduced:** yes
- **Fix:** in the workbook they are dates (yyyy-mm-dd; Approved at with the time); the CSV keeps YYYY-MM-DD text.
- **Test:** `tests/test_trust_review.py::test_the_excel_export_writes_real_dates`
- **Lesson:** A spreadsheet is for people: give it typed values.

#### B-471 · The queue showed the file's name as the vendor, and a doubtful name as read · medium
- **Where:** `ap_coder/web/pages/review.py` (`queue_rows`), `ap_coder/web/templates/review.html`,
  `ap_coder/core/store.py` (`vendor_name_checks`), `ap_coder/web/templates/vendors.html`
- **Symptom:** an invoice with no vendor name read was listed under its file name ("scan_0042.pdf") as the vendor;
  "SOMETHING JOHN" (read with 32%) looked like any vendor; Vendors listed it with the real ones.
- **Cause:** `vendor_name or file_name`; the capture's confidence was not looked at.
- **Reproduce:** `ca_owensound_water.pdf` among the 15 PDFs.
- **Reproduced:** yes
- **Fix:** "No vendor name" in place of a missing one, a name the readers marked to check in italics with "?" and
  "Vendor name: check it" under it (the file's name on hover); on Vendors, a vendor with no approved invoice and no
  record is tagged "Not approved yet". No vendor record is created at import (only on approval, an import of the
  vendor list, or "Add new vendor"): checked, nothing to change there.
- **Test:** `tests/test_trust_review.py::test_the_queue_says_no_vendor_name_and_marks_a_name_to_check`
- **Lesson:** A fallback value must not look like the real one.

#### B-472 · "Every header field was read right" after approving an invoice nobody corrected · low
- **Where:** `ap_coder/capture/workflow.py` (`taught_message`)
- **Symptom:** approving an invoice whose vendor was wrong but not edited said every header field was read right.
- **Cause:** "no correction" was worded as "all correct".
- **Reproduce:** approve an untouched invoice.
- **Reproduced:** yes
- **Fix:** "You changed no header field."
- **Test:** `tests/test_touchless.py`, `tests/test_touchless_pages.py` (the message)
- **Lesson:** Say what is known (nothing changed), not what is hoped (all right).

#### B-473 · "Teaches from 1 line · all as suggested" on a line with no GL account yet · medium
- **Where:** `ap_coder/web/invoice_view.py` (`learn_text`), `ap_coder/web/templates/_invoice_live.html` (the approve bar)
- **Symptom:** BDO's only line had no GL account yet (nothing proposed, Approve closed) and the approve bar said
  "Teaches from 1 line · all as suggested"; once the reviewer picked the account it still said "all as suggested".
- **Cause:** the bar had two words only: "you changed N of its codes", else "all as suggested"; a line still to code,
  or filled by the reviewer where nothing was proposed, fell under "all as suggested" (filling a blank is no change,
  D-39, but it is not a suggestion kept either).
- **Reproduce:** `ca_oc_bdo.pdf` among the 15 PDFs (`shots/after/review_4_vp.png`).
- **Reproduced:** yes
- **Fix:** the bar says what approving teaches, line by line, as the approval counts it: "1 line to code" (no GL
  account, or no cost center where they are used: nothing taught from it yet), then "Teaches from N lines:" with
  "1 you coded" (nothing proposed, or only a guess, and the reviewer picked the account), "1 you changed", "1 as
  suggested" (an account proposed and kept), "1 guessed account to check" (B-463).
- **Test:** `tests/test_trust_review.py::test_the_approve_bar_says_lines_to_code_coded_by_you_or_kept_as_suggested`;
  `tests/test_dashboard_sweep_fixes.py`, `tests/test_web_invoice.py` (the bar's words)
- **Lesson:** A default phrase ("all as suggested") must be earned by the data, not reached by falling through.

#### B-474 · Month-end listed an invoice with no date as "invoice dated None", and left old ones out silently · medium
- **Where:** `ap_coder/erp/accruals.py` (`_not_in_erp`, `undated`), `ap_coder/web/pages/month_end.py`,
  `ap_coder/web/templates/month_end.html`
- **Symptom:** found by the review of this branch. An invoice with no date went to B-466's "dated more than a year
  before" list as "invoice dated None"; and an invoice dated more than a year back was left out of the accruals and
  the CSV, though AP may well need to accrue it.
- **Cause:** a missing date compared as "" (before every date); B-466 chose to exclude old invoices.
- **Reproduce:** the review's `check2.py` (dates "", None, this month, last year).
- **Reproduced:** yes
- **Fix:** an invoice with no date is listed apart, "No invoice date: can't be accrued until it has one"; one dated
  more than a year before the period end is accrued, flagged "Check date" on screen and "dated over a year ago:
  check" in the CSV, with how many and how much at the top of the page: AP decides.
- **Test:** `tests/test_trust_review.py::test_month_end_flags_an_invoice_dated_over_a_year_back_and_lists_one_with_no_date`
- **Lesson:** Test a date check with no date at all.

#### B-475 · A coding rule over a guess, or the reviewer's own account, still read as the model's guess · high
- **Where:** `ap_coder/coding/rules.py` (`apply`), `ap_coder/core/store.py` (`approve_invoice`, `line_guessed`,
  `metrics`), `ap_coder/web/invoice_view.py` (`grid`, `learn_text`), `ap_coder/ai/lessons.py` (`outcomes`),
  `ap_coder/core/audit.py`, `ap_coder/web/pages/invoice.py` (the approval message), `ap_coder/web/templates/learning.html`
- **Symptom:** found by the review of this branch. A fixed coding rule applied after the chat model kept the model's
  reasoning: the line was marked a guess, the bar said "1 guessed account to check", and the approval recorded the
  model's guess as the AI's proposal, corrected (6010 → 6000). The same after "Use PO coding". An account the
  reviewer picked was marked a guess again whenever the server drew the grid (the hidden reasoning is the model's;
  only the page's script took the mark away). A kept guess counted as corrected (D-51 at first), a changed guess was a
  lesson "read as 6010", and the audit trail said "all as the AI suggested".
- **Cause:** "a guess" was read from the line's reasoning text, which the rule and the screen do not change.
- **Reproduce:** the review's `rule.py`; pick another account on a guessed line, then add a line.
- **Reproduced:** yes
- **Fix:** a rule that codes a line says so in its reasoning ("Coded by the coding rule …") and records whether it
  replaced a guess (`from_guess`); a row is marked only while its account is still the one the AI guessed for its line
  (compared with the invoice as read, not by the text). What the reviewer approves on a guessed line is their own
  coding: a new outcome, "coded", learned from but out of coding accuracy, the top corrections, the vendor accuracy and
  the lessons; the approval says "1 you coded", the audit trail "coded by the reviewer (the AI only guessed)", the bar
  "1 by a coding rule" for a rule's line (D-51 updated).
- **Test:** `tests/test_trust_review.py::test_a_coding_rule_over_a_guess_is_the_rules_coding_not_a_guess_and_not_a_correction`,
  `::test_an_account_the_reviewer_picked_over_a_guess_is_never_marked_again`,
  `::test_the_approval_message_says_you_coded_for_a_kept_guess`,
  `::test_a_kept_guess_is_the_reviewers_coding_not_coded_as_suggested`
- **Lesson:** Decide a state from the values (the account the AI guessed, the account now), not from a label that
  other steps don't update.

#### B-476 · OLD_INVOICE_DATE held invoices back as a warning, and fired on credit notes · medium
- **Where:** `ap_coder/coding/terms.py` (`payment_findings`), `ap_coder/ai/pipeline.py` (`finalise_coding`),
  `ap_coder/web/invoice_view.py`, `ap_coder/coding/bulk.py`, `ap_coder/ops/help.py`
- **Symptom:** found by the review of this branch. B-469's check was a warning (it lowered the confidence, kept
  invoices from bulk approval and touchless) measured against today, so an invoice in the queue for a while became
  "old", and a credit note (often old on purpose) was flagged.
- **Cause:** a new check given a warning's weight.
- **Reproduce:** an invoice dated 13 months back; a credit note of the same date.
- **Reproduced:** yes
- **Fix:** information only ("Good to know"), measured from the day AP Coder read the invoice (`received`, its import
  day on the review screen and in bulk approval), not raised for a credit note; Help says so.
- **Test:** `tests/test_trust_review.py::test_an_old_invoice_date_is_a_note_measured_from_the_day_it_came_in_and_not_for_a_credit_note`,
  `::test_a_long_overdue_bill_says_since_when_and_an_old_invoice_date_is_flagged`
- **Lesson:** Give a new check the weight of what it costs the person, not of what it found.

#### B-477 · Bulk approval said why an invoice was left with the check's code ("VENDOR_ON_HOLD: …") · low
- **Where:** `ap_coder/coding/bulk.py`
- **Symptom:** found by the review of this branch: the "not approved" details read "VENDOR_BANK_CHANGED: the bank
  account to pay into changed".
- **Cause:** `f"{code}: {message}"`.
- **Reproduce:** bulk-approve an invoice with a stored warning that needs a person.
- **Reproduced:** yes
- **Fix:** the message alone, capitalised (the check's Help title when it has none); the code stays in the problem
  report.
- **Test:** `tests/test_trust_review.py::test_bulk_approval_says_why_an_invoice_was_left_without_the_check_code`
- **Lesson:** B-464 again: search for every place that prints a code.

#### B-478 · The line grid's Tab order crossed its rows, the page view sat under the approve bar, amounts were cut · medium
- **Where:** `ap_coder/web/templates/_invoice_lines.html`, `ap_coder/web/templates/invoice.html`,
  `ap_coder/web/static/invoice.css`, `ap_coder/web/static/js/invoice_viewer.js`
- **Symptom:** found by the review of this branch. In B-467's two-row lines Tab went description → amount → GL →
  taxes → qty → unit price, up and down across the rows; in Fit mode at 1366 × 768 the bottom 80–170 px of the page
  view was under the approve bar; "-199.98" was cut in its box and the GL account's name had little room.
- **Cause:** the cells kept the old table order; the viewer's height was the window less a fixed 190 px.
- **Reproduce:** the review's `keys.py` and `shots.py` at 1366 × 768.
- **Reproduced:** yes
- **Fix:** three rows a line, the cells in the order they show and Tab follows: # · description · delete; GL account
  (wide: no cost centers in the pilot) · amount; taxes · qty · unit price. The viewer ends above the approve bar
  wherever the Document card is (it grows as the card rises to its sticky place). On a screen 1700 px wide or more
  the document gets three fifths of the width.
- **Test:** `tests/test_web_pilot_walkthrough.py::test_in_a_browser_tab_follows_the_line_grid_as_it_shows_and_the_page_view_ends_above_the_bar`
- **Lesson:** A visual reorder in CSS needs the DOM order to match, or the keyboard tells a different story.

#### B-479 · A replaced guess was "no changes", and the lines AP coded were missing from "Lines reviewed" · medium
- **Where:** `ap_coder/core/store.py` (`approve_invoice`, `metrics`), `ap_coder/web/pages/learning.py`,
  `ap_coder/web/templates/invoice.html`, `ap_coder/web/static/invoice.css`
- **Symptom:** found by the re-review of this branch. A reviewer who put another account in place of the model's
  guess was recorded as no edit: the approved screen said there were no changes to the suggested coding and it counted
  as approved without edits, while the audit showed "line 1 GL: 6000 → 1500". "Lessons learned" (queue) and "Lines
  reviewed" (Learning) left out the lines AP coded over a guess (B-475), though AP Coder learns from them. A 25-line
  invoice's grid was about 4,600 px tall at 1366 × 768.
- **Cause:** B-475 skipped guessed lines when counting the reviewer's changes, and took the "coded" lines out of the
  reviewed total along with the accuracy.
- **Reproduce:** approve an invoice after picking another account on a guessed line; the re-review's `add25.py`.
- **Reproduced:** yes
- **Fix:** a guess kept is no edit, another account in its place is; `lines_reviewed` counts every line AP reviewed
  (the coded ones too, `lines_coded`), the accuracy only what the AI proposed (`lines_measured`). An invoice of more
  than 10 lines gets a tighter grid (captions on the first line only, one-line description boxes, the taxes on one
  line): the same rows and Tab order, about 3,200 px for 25 lines.
- **Test:** `tests/test_trust_review.py::test_replacing_a_guessed_account_is_an_edit_and_coded_lines_count_as_reviewed`
- **Lesson:** Leaving a line out of one figure (accuracy) is not leaving it out of all of them.

#### Looked at and left (review screen and coding)

- **N-172** · After Approve, the address bar was said to stay on the approved invoice's address. Not reproduced:
  the answer's address is opened (`location.assign`) and `location.href` is the next invoice's (`/review/8` after
  approving `/review/14`, five runs); Playwright's `page.url` read straight after the click can still show the old
  one, which is likely what was seen. Nothing changed.
- **N-173** · Vendors still lists a name read off an invoice not approved yet ("Inc."), tagged "Not approved yet":
  hiding it would hide its bank-account and GST/HST history (a fraud signal) and "Ask the vendor" for its invoices
  in the queue.
- **N-174** · Fixed (B-473).
- **N-175** · B-468 asks before the first real import; a demo kept after real invoices are imported still counts in
  the figures until removed (excluding it everywhere is a larger change across Activity, Learning, Insights and
  Vendors).
- **N-176** · With the chat model answering, its account for a line with a weak match is kept even when the name
  match pointed elsewhere (marked as a guess either way); on the samples the name match was right more often than a
  small model (21 vs 11 of 41), so which one to show first, when they disagree, is worth measuring on the pilot's
  invoices.
### Before testing: navigation, settings and help

A product audit of the dashboard as the pilot tester first meets it (1366 x 768 and 1366 x 650 laptop screens, a fresh
install and the demo), and the fresh-install review that followed. The owner decided four things with it: LM Studio
runs for the pilot (step 0), the menu shows only the seven everyday pages, exports are Excel or CSV only, and the pilot
codes no cost centers (D-52, D-53). Tests: `tests/test_before_testing.py` unless named.

#### B-502 · Settings and Help were below the window on a laptop · medium
- **Where:** `ap_coder/web/templates/base.html`, `ap_coder/web/static/app.css` (`.rail`), `ap_coder/web/pages/__init__.py`
- **Symptom:** at 1366 x 768 the left menu (16 pages in five sections) scrolled under its sticky footer: Settings and
  Help could not be seen without scrolling the menu.
- **Cause:** the whole rail scrolled, with the footer stuck over its end.
- **Reproduced:** yes (audit screenshot; Playwright at 1366 x 768 and 1366 x 650)
- **Fix:** the menu shows the seven everyday pages; the nine others fold under **More** (open on one of its pages,
  otherwise as the browser left it); Settings and Help are pinned above the footer, outside the part that scrolls.
- **Test:** `test_the_menu_shows_the_seven_core_pages_and_folds_the_rest_under_more`,
  `test_in_a_browser_settings_and_help_are_on_screen_on_a_laptop` (768 and 650), `test_in_a_browser_the_more_fold_is_remembered`
- **Lesson:** Check a menu at the smallest screen it is used on, with every fold open.

#### B-503 · The getting-started counter counted rows it didn't show · medium
- **Where:** `ap_coder/web/pages/process.py` (`setup_steps`, `first_steps`), `templates/_setup_steps.html`
- **Symptom:** the welcome page listed 8 or 9 steps but counted "x/6": the province showed green but was never
  counted, the optional chat model was listed but not counted.
- **Fix:** every row shown is counted, in the Quick start's order: LM Studio running, reading invoices, your name,
  your province, GL accounts, sales tax accounts, your first invoices. The chat model is part of the LM Studio row.
- **Test:** `test_the_welcome_checklist_counts_exactly_the_rows_it_shows_with_lm_studio_first`,
  `test_import_invoices_counts_the_same_rows`, `test_the_province_counts_once_given`
- **Lesson:** A counter counts what the list shows, nothing hidden and nothing extra.

#### B-504 · Without LM Studio the checklist said "!" forever, with no way to check again · medium
- **Where:** `ap_coder/web/pages/process.py` (`lm_studio_step`), `ap_coder/web/pages/settings_reading.py` (`check_again`)
- **Symptom:** the OvisOCR2 row stayed "!" with "see Settings → Reading"; nothing on the welcome page said how to start
  LM Studio or let the tester check again.
- **Fix:** "LM Studio running" is the first row; until it runs, the card says how to start it and has **Check again**,
  which comes back to the page it was pressed on (a page of this dashboard only).
- **Test:** `test_check_again_from_the_welcome_page_comes_back_to_it`, `tests/test_web_review.py::test_the_setup_step_names_the_self_tests_state`

#### B-505 · The Quick start said what the app doesn't do · medium
- **Where:** `docs/QUICK_START.md`
- **Symptom:** it said there are no GL suggestions without LM Studio (lines AP approved before are still suggested);
  LM Studio was not a step; step 2 named a button the page doesn't have, sent the tester to the sidebar's Sales tax page
  instead of the GL page's tab, and left out **Save tax setup**; step 6 didn't say which export format is used.
- **Fix:** step 0 is starting LM Studio (with **Check again**, not "start AP Coder again"), what is missing without it
  said as it is; step 2 follows the page's own words (PST needs no account: it is added to each line); step 6 says
  the first export is an Excel workbook and how to choose CSV.
- **Test:** `test_the_guides_match_the_app`, `tests/test_sweep4_first_launch.py::test_the_quick_start_names_only_buttons_and_pages_the_dashboard_has`

#### B-506 · Settings opened on its most technical tab · low
- **Where:** `ap_coder/web/pages/settings.py`, `templates/settings_reading.html`
- **Symptom:** Settings opened on Reading: nine readers, model tables, URLs and the AI reviewer's rounds and minutes.
- **Fix:** Settings opens on Review. Reading starts with what the tester acts on (LM Studio running or not, with Check
  again, OvisOCR2 and the chat model found), the self-test and the AI reviewer's switch; the rest is under **For IT**.
- **Test:** `test_settings_opens_on_review_and_hides_jd_edwards_until_asked`, `test_reading_says_whether_lm_studio_runs_and_folds_the_rest_for_it`

#### B-507 · Three forms and three Saves on Settings → Review, and a message about the wrong thing · medium
- **Where:** `ap_coder/web/pages/settings.py` (`save_review`, `saved_text`), `templates/settings_review.html`
- **Symptom:** a change in one card was lost by saving another; saving a name said "Saved. Invoices read from now on
  use it."; "Ask why after the same correction (times)" was unclear.
- **Fix:** one form, one **Save**; the confidence slider, the GL list rule and the "ask for the reason" count are under
  **Advanced**, reworded; the message names what was saved and says "invoices read from now on" only of what applies to
  them. The old addresses still work.
- **Test:** `test_the_review_tab_is_one_form_with_one_save_and_the_risky_settings_folded`, `test_saving_your_name_says_your_name_was_saved`

#### B-508 · Touchless processing was the page's main button, and its bar was told three ways · medium
- **Where:** `templates/settings_automation.html`, `ap_coder/web/pages/settings_automation.py` (`typical_to_go`),
  `ap_coder/web/pages/learning.py` (`suppliers_context`)
- **Symptom:** "Turn on touchless processing…" was the blue button though the pilot leaves it off; the guides said
  "20, the last 10 clean" and "about 30", the vendors 31, 34, 38, 62; Supplier learning said how far each vendor was
  twice, with "accuracy at least 75.7%".
- **Fix:** a plain button and "Leave it off during the pilot."; the bar's rules and, once, what they mean in practice
  (about 30 to 40 invoices without a correction, worked out from the bar); each vendor says its own number once.
- **Test:** `test_touchless_is_not_the_pages_main_button_and_says_to_leave_it_off`, `test_supplier_learning_says_how_many_more_invoices_once`

#### B-509 · "N invoices waiting for OvisOCR2" counted invoices already approved · low
- **Where:** `ap_coder/core/store.py` (`page_reads_waiting`), `ap_coder/ai/reading.py` (`_queue`)
- **Fix:** the count is of the invoices still to approve; the reader still reads the others (to score itself).
- **Test:** `test_the_reading_queue_counts_only_invoices_still_to_approve`

#### B-510 · Without LM Studio the AI reviewer and OvisOCR2 looked broken · low
- **Where:** `ap_coder/ai/reading.py`, `ap_coder/web/pages/settings_reading.py` (`READER_PILL`)
- **Symptom:** amber "Needs a look" and red "Not working" before LM Studio was even started.
- **Fix:** "Waiting for LM Studio", with what to do: start it, then Check again.
- **Test:** `test_without_lm_studio_the_ai_reviewer_says_to_start_it_and_check_again`

#### B-511 · A developer tool on the tester's Learning page · low
- **Where:** `ap_coder/web/pages/learning.py`, `ap_coder/web/core.py` (`developer_tools`)
- **Symptom:** Learning → Readers showed evidence patterns, the benchmark, a command line and "Download training data
  (ZIP)" of the supplier invoices.
- **Fix:** shown with Settings → About's "Show developer tools" (or `AP_DEVELOPER_TOOLS=1`); the download answers 404
  otherwise.
- **Test:** `test_the_readers_tab_and_the_training_data_are_developer_tools`

#### B-512 · Three different things called "lessons" · low
- **Where:** `ap_coder/web/pages/review.py` (`kpis`), `templates/learning.html`, `templates/settings_data.html`, `templates/vendors.html`
- **Symptom:** "Lessons learned 13" (lines reviewed), the Lessons tab's "2" (repeated corrections), "Memory: every lesson".
- **Fix:** the lines AP confirmed or corrected are the **coding memory** everywhere; **lessons** are only the
  corrections AP keeps making, with AP's reason (their own tab).
- **Test:** `test_the_queue_figures_are_plain_and_the_coding_memory_has_one_name`

#### B-513 · The duplicate payment audit was on two pages · low
- **Fix:** it is on Exports, next to the ERP register it also checks; Activity links there. (`/activity/duplicates.csv` stays.)
- **Test:** `test_the_duplicate_payment_audit_is_in_one_place`

#### B-514 · Help was 7,600 px of codes, pointed at hidden settings, and showed a developer changelog · medium
- **Where:** `ap_coder/web/pages/help.py`, `templates/help.html`, `ap_coder/ops/help.py` (`TO_DO`, `CLOUD_CHECKS`,
  `QUICK_START`, `WHATS_NEW`), `static/js/help.js`
- **Symptom:** 83 checks in one table with their codes; the Azure checks said "Settings → Reading", which hides Azure in
  this build; "What's new" listed PRs, schema versions and environment variables; its Getting started order differed
  from the welcome page and the Quick start.
- **Fix:** the checks are grouped by what the tester does about them, folded (opened by a search), without codes
  (still searchable and linkable); the Azure checks only where the internet is allowed or the check is on; a short
  "What's new for testers" (the changelog with the developer tools); Getting started in the Quick start's order; the
  folders under For IT.
- **Test:** `test_help_groups_the_checks_by_what_to_do_folded_without_codes`, `tests/test_web_help.py`

#### B-515 · Insights showed a cloud cost in an offline product, and figures from two approvals · low
- **Where:** `ap_coder/web/pages/insights.py`, `templates/insights.html`
- **Fix:** no cloud cost, prices or net of it offline (only where the internet is allowed); the figures show from 20
  approvals, with an empty state saying so before.
- **Test:** `test_insights_has_no_cloud_cost_offline_and_waits_for_twenty_approvals`

#### B-516 · The setup report and the Azure check's page assumed a cloud build · low
- **Where:** `ap_coder/ops/doctor.py` (`for_this_build`), `ap_coder/web/pages/azure.py`, `ap_coder/ops/cli.py`, `docs/GETTING_STARTED.md`
- **Symptom:** the doctor listed Azure packages and endpoints, the prompt size and "schema enum"; `/settings/azure-check`
  showed the Azure form; the guide offered `schema` ("sent to Azure OpenAI").
- **Fix:** the offline build's report leaves the cloud lines out and says "codes limited to the list"; the Azure page
  says it isn't part of this build unless the internet is allowed, the check is on, or the developer tools are; the
  `schema` command is a developer's, out of the tester's guide.
- **Test:** `test_the_offline_doctor_report_has_no_cloud_lines`, `test_the_azure_check_page_says_it_is_not_part_of_the_offline_build`

#### B-517 · "Approved today 4 · of 28 today · 6 fully approved in all" · low
- **Where:** `ap_coder/web/pages/review.py` (`kpis`, the text only)
- **Fix:** "4 · 24 still to review · 6 approved in total" (and the second approvals waiting, when any).
- **Test:** `test_the_queue_figures_are_plain_and_the_coding_memory_has_one_name`, `tests/test_sweep3_dayone.py`

#### B-518 · The guides' launcher window wasn't the real one · low
- **Where:** `README.md` (PILOT.md's quotes and the launcher's own LM Studio line: the launcher's change, merged
  alongside)
- **Symptom:** "[OK]" where the window shows ✓ and !; "then start AP Coder again" for LM Studio.
- **Fix:** README's window as it shows; the guides say: start LM Studio, then press Check again in Settings →
  Reading, with no need to restart AP Coder (the launcher's wording).
- **Test:** `test_the_guides_match_the_app`

#### B-519 · Vendors said "Ask the vendor above" where there was none · low
- **Fix:** "open a vendor in the list above, and its Ask the vendor section drafts the email".
- **Test:** `test_vendors_say_where_ask_the_vendor_is`

#### B-520 · Help → Report a problem took about 4 s to open · low
- **Where:** `ap_coder/web/pages/help.py` (`report_setup`, `setup_report`), `static/js/help.js`
- **Cause:** the setup report (the doctor, which looks at the OCR engines) was made while the page was drawn.
- **Fix:** the page opens at once; the setup report is fetched after it (Copy waits for it) and kept 5 minutes.
- **Test:** `test_report_a_problem_opens_at_once_and_fetches_the_setup_report`,
  `tests/test_web_pilot_walkthrough.py::test_in_a_browser_report_a_problem_shows_it_is_working`

#### B-521 · Full folder paths on the tester's pages · low
- **Where:** `templates/settings_about.html`, `templates/help.html`, `templates/help_report.html`
- **Fix:** under **For IT** folds; About's update note says AP Coder may stay open (the update stops it, PILOT.md).
- **Test:** `test_about_folds_the_full_paths_for_it`

#### B-522 · "Who are you?" was a bare line · low
- **Where:** `templates/who.html`, `templates/settings_review.html`
- **Fix:** a page that says what the name is for and where to change it; Settings → Review links to it.
- **Test:** `test_who_is_a_proper_page_linking_to_settings`

#### B-523 · "Sales tax" was two different places · low
- **Where:** `ap_coder/web/pages/accounts.py` (`TABS`), `templates/accounts.html`
- **Fix:** the GL page's tab is **Sales tax accounts**; it says GST, HST and QST need an account and PST none.
- **Test:** `test_the_accounts_page_names_its_tax_tab_apart_from_the_sales_tax_page`

#### B-524 · Exports forgot the format chosen · low
- **Where:** `ap_coder/web/pages/exports.py` (`last_format`, `shown_formats`)
- **Fix:** Exports opens on the format of the last batch made (Excel at first); JD Edwards E1 only once shown (D-52).
- **Test:** `test_exports_open_on_the_last_format_used`

#### B-525 · The guides disagreed with each other · low
- **Where:** `docs/GETTING_STARTED.md`, `docs/PILOT.md` ("No Azure"), `templates/settings_about.html`
- **Symptom:** About said to close AP Coder before updating, PILOT that it may stay open (it may: the update stops it);
  "never connects, not even to set up" where a GitHub ZIP downloads its packages and models once; PILOT's "No Azure"
  where the Azure check exists, off by default.
- **Fix:** each said as it is: the update may start with AP Coder open; a GitHub ZIP downloads once; the Azure check
  is opt-in and sends nothing unless switched on. (Python 3.11 or newer, 3.12 recommended, everywhere: the launcher's
  change, which also aligns `pyproject.toml`.)
- **Test:** `test_about_folds_the_full_paths_for_it`, `test_the_guides_match_the_app`

An independent review of the branch walked the pilot path end to end and found what follows. Tests:
`tests/test_before_testing_review.py`.

#### B-526 · An update hid JD Edwards from a company already using it · high
- **Where:** `ap_coder/web/core.py` (`jde_shown`), `ap_coder/web/pages/exports.py` (`last_format`)
- **Symptom:** after the update the JD Edwards E1 tab and export format were gone, with no word, for a database with
  JD Edwards settings and batches: *Show JD Edwards settings* starts off.
- **Fix:** a database with JD Edwards settings saved or a JD Edwards batch made has the switch turned on, once and
  stored (switched off by hand it stays off); Exports keeps a JD Edwards format used last. docs/JDE_E1.md and
  PILOT_PLAN.md name the switch.
- **Test:** `test_a_jd_edwards_user_keeps_the_tab_and_the_format_after_the_update`,
  `test_a_new_install_has_jd_edwards_hidden_and_nothing_stored`
- **Lesson:** Hiding a feature for new users must not take it from those already using it.

#### B-527 · Saving Settings → About stored the developer variable as if it were ticked · medium
- **Where:** `ap_coder/web/pages/settings.py` (`save_shown`), `templates/settings_about.html`, `ap_coder/web/core.py`
- **Symptom:** with `AP_DEVELOPER_TOOLS` set, the page posted developer=1 and saved it in the data folder, so the tools
  stayed on after the variable went; the page and `developer_tools()` read the variable two ways (`0` was "on" for
  one of them); every save logged both switches.
- **Fix:** the variable is read one way (1, true, yes on; 0, false, no, nothing off); when it is set the switch shows
  "on (set by AP_DEVELOPER_TOOLS)" and nothing is stored; only a switch that changed is saved and logged.
- **Test:** `test_the_developer_env_var_is_read_one_way`, `test_the_env_var_is_shown_as_on_and_never_stored`,
  `test_save_shown_logs_only_what_changed`

#### B-528 · The demo kept cost centers the tester could not see · medium
- **Where:** `ap_coder/ops/demo.py` (`_without_cost_centers`, Harbourview's mistake), `templates/_invoice_lines.html`
- **Symptom:** with no cost center list, the demo invoices still carried CC200, CC400 from their ground truth: an
  approved one kept them, the CSV export wrote them, the coding memory learned them, and a hidden `line_cc` posted them
  back; Harbourview's "mistake to correct" was a cost center, invisible.
- **Fix:** without a cost center list the demo loads its invoices without cost centers; Harbourview's mistake is a GL
  account (6110 for a legal review); no hidden cost center is posted when there is no list.
- **Test:** `test_the_demo_has_no_cost_center_anywhere`, `test_harbourviews_demo_mistake_is_a_gl_account`,
  `test_no_hidden_cost_center_is_posted_without_a_list`
- **Lesson:** A field hidden from the screen must be gone from the data too, or it travels unseen.

#### B-529 · The welcome page said "Add your name" before LM Studio, and "Start server" whatever was missing · medium
- **Where:** `ap_coder/web/pages/process.py` (`lm_studio_step`, `lm_studio_help`, `next_step`), `templates/_next_step.html`
- **Symptom:** the Next step card asked for the name while step 0 was LM Studio, and Check again was below the fold
  at 1366 x 650; with LM Studio running but without OvisOCR2 or a chat model the box still said "Developer tab → Start
  server"; LM Studio with no chat model was ticked done, though the pilot runs with it.
- **Fix:** while LM Studio isn't ready, it is the Next step, at the top, with what to do for its state (start it, add
  OvisOCR2, load the chat model) and Check again; the row is done only with OvisOCR2 ready and the chat model loaded.
- **Test:** `test_the_lm_studio_step_needs_ovisocr2_and_the_chat_model`, `test_the_next_step_is_lm_studio_with_check_again_on_a_laptop`,
  `test_the_running_but_empty_lm_studio_box_says_what_is_missing`

#### B-530 · "Sales tax" had three names, and the name step said "recorded in it" · low
- **Fix:** "Sales tax accounts" for the checklist row, the next step and the tab; "GST, HST and QST each need a GL
  account (PST needs none)" everywhere; "each approval is recorded under it".
- **Test:** `test_one_name_for_the_sales_tax_accounts_and_recorded_under_it`

#### B-531 · The Quick start and the screens disagreed · low
- **Where:** `docs/QUICK_START.md`, `ap_coder/ops/help.py` (`QUICK_START`), `templates/help.html`
- **Symptom:** "Start (every day)" didn't say to start LM Studio; step 2 stopped at Upload, though a preview and a
  second **Import GL accounts** follow; Help's Getting started had six steps numbered differently and claimed to be
  the welcome page's.
- **Fix:** start LM Studio first every day; step 2 as the page goes; Help lists the Quick start's seven steps, 0 to 6,
  by the same names, and says the welcome checklist ticks steps 0 to 3.
- **Test:** `test_help_and_the_quick_start_number_the_same_steps`

#### B-532 · "Coding memory" was two different numbers · low
- **Where:** `ap_coder/web/pages/settings.py` (`data_context`), `templates/settings_data.html`, `ap_coder/web/pages/review.py` (`kpis`)
- **Symptom:** the queue counted AP's reviewed lines, Settings → Data every row of the memory (the ERP history
  included, cut at 50,000); the queue tile's note was cut off at 1366.
- **Fix:** one number, AP's reviewed lines, with the ERP history counted apart ("plus N lines from ERP history"); a
  short note.
- **Test:** `test_the_coding_memory_is_one_number_with_erp_history_apart`

#### B-533 · Smaller points from the review · low
- Insights counts fully approved invoices towards its 20 (a second approval waiting does not count yet).
- Help, filtered by area or a search, links back to all the checks.
- A failed self-test shows what OvisOCR2 read on Settings → Reading itself, not folded under For IT.
- Report a problem's setup report is made again after Check again or a saved setting.
- **Test:** `tests/test_before_testing.py` (Insights, Help), `tests/test_reading_pages.py`

#### B-534 · A purchase order's cost center was still compared with no cost center list · medium
- **Where:** `ap_coder/coding/po.py` (`match_invoice`), `templates/_invoice_live.html`, `templates/learning.html`
- **Symptom:** the sample POs carry cost centers (CC400, CC700); with no cost center list, demo invoices 1, 5, 9 and 10
  got "PO-88213 line 1 is coded to GL 6010 · CC400" and a "coding" problem on the PO match, and *Use the PO's
  coding* would have put a cost center the tester cannot see on the line. The GL posting preview and the coding
  memory table showed an empty Cost center column.
- **Cause:** the PO check compared the PO line's cost center with the line's (empty) one whatever the setup.
- **Fix:** without a cost center list a PO's cost centers are not read: not compared, not said, not copied to a line;
  the two tables show the Cost center column only when a row has one. The CSV export keeps its Cost center column
  (empty): it is the ERP import's fixed layout, and a column that comes and goes would break an import mapping (the
  custom layout can leave it out).
- **Test:** `tests/test_before_testing_review.py::test_without_a_cost_center_list_a_po_compares_no_cost_center`,
  `test_without_a_cost_center_list_no_empty_cost_center_column`
### Before testing: install, launcher and updates

A fresh install of main (3dacb6f) the way the pilot tester does it, on Linux standing in for Windows: GitHub's ZIP
(`git archive`) unzipped into a new home folder and double-clicked, four times from cold, then a newer ZIP unzipped
into a new folder and over the old one. Then each fix below was run the same way again (a newer ZIP of the same
version unzipped over a running one: the old window said a newer AP Coder took over, the new one said *Updated AP
Coder from 0.3.0 (build c384fc5, 2026-10-21) to 0.3.0 (build 87db17f, 2026-10-21)* and ran the self-check).

#### B-542 · A newer GitHub ZIP of the same version could not be told from the older one · high
- **Where:** `ap_coder/core/paths.py` (`install_identity`, `describe`), `scripts/launch.py` (`previous_install`,
  `run`), `scripts/running.py` (`judge`), `ap_coder/web/app.py` (`/healthz`), `ap_coder/web/pages/settings.py`
  (About), `ap_coder/ops/doctor.py`
- **Symptom:** unzipped over the old folder, a newer ZIP said no *Updated* line, showed the older one's *Self-check
  OK at setup* and ran no new self-check; Settings → About said only "0.2.0". Unzipped over a folder whose dashboard
  was still running, the new start opened the old dashboard ("already running").
- **Cause:** every version was 0.2.0, a ZIP carries no build information, and the install fingerprint was a hash of
  `pyproject.toml`, which most changes leave alone; the running dashboard was judged by folder and version only.
- **Reproduce:** `git archive` two commits of main, unzip the older one and double-click it, unzip the newer one over
  it and double-click again.
- **Reproduced:** yes (run2.log of the audit: *Self-check OK at setup (2026-10-21 20:14)*, no update said)
- **Fix:** `ap_coder/BUILD` holds `$Format:%h %cI$`, marked `export-subst` in `.gitattributes`: GitHub's Download
  ZIP and `git archive` write the commit and its time into it; the offline bundle builder writes it for its copy; a
  checkout uses `git log -1`; a folder with neither, a hash of its code (`paths.build_stamp`). The fingerprint is the
  pyproject plus that build, a ZIP's build time is its commit's (so an older ZIP in another folder is older), and the
  launcher's state records the commit: *Updated AP Coder from 0.3.0 (build c384fc5, 2026-10-21) to 0.3.0 (build
  87db17f, 2026-10-21)*, then the self-check. `/healthz` says the build, so a dashboard from before this folder was
  unzipped over is stopped, not opened. Settings → About, the doctor and Help → Report a problem say the build. The
  version is 0.3.0 for this pilot drop (D-54).
- **Test:** `tests/test_launch_build.py::test_a_newer_zip_unzipped_over_the_older_one_is_an_update`,
  `::test_a_stamped_zip_says_its_commit_and_time`, `::test_two_zips_of_one_version_are_two_builds`,
  `::test_a_dashboard_of_this_folder_from_before_its_code_changed_is_older`, `::test_the_dashboard_says_its_build`,
  `::test_settings_about_and_the_doctor_say_the_build`, `::test_the_bundle_builder_stamps_its_copy`; CI
  `windows-github-zip`
- **Lesson:** An identity built from a version number that nobody bumps is no identity: take it from what changes
  with every build, the commit, and put it where every way of getting the code carries it.

#### B-543 · The first run said nothing for about three of its first four minutes · medium
- **Where:** `scripts/launch.py` (`ensure_venv`, `pip_install`, `PipProgress`, `heartbeat`)
- **Symptom:** making `.venv` (30 s on the audit's run), pip installing 56 packages (135 s) and a 22 s gap after it
  showed no line: a pilot tester would think it hung and close the window.
- **Cause:** pip's own lines (one a package) are kept for the log, and nothing replaced them in the window; `python -m
  venv` was run without a word.
- **Reproduce:** GitHub's ZIP, a fresh home folder, double-click; time the window's lines.
- **Reproduced:** yes (run1.log of the audit). The 22 s gap did not come back on any of the later runs, cold or warm
  (3 s between pip's last line and the next step; nothing between the steps after it took more than 0.4 s): it was
  pip itself finishing on a cold cache, and the heartbeat now covers it.
- **Fix:** *Making AP Coder's own Python environment (the .venv folder; under a minute)...* before `venv`; while pip
  runs, every 12 s without a line the window says how far it is, from pip's lines and the `.dist-info` folders that
  appear: *Working out which packages are needed: 12 so far (pandas)...*, *Getting packages: 29 of 55
  (opencv_python)...*, *Installing packages: 41 of 56 (onnxruntime)...*. The steps after pip get the same watch
  (`settle_and_check`): 12 s without a line says *Still working: getting the OCR models...* (or the checks, the
  self-check, the clean-up).
- **Test:** `tests/test_launch_build.py::test_pip_progress_counts_what_it_found_fetched_and_installed`,
  `::test_pip_progress_from_the_wheelhouse`, `::test_pip_says_how_far_it_is_while_it_is_quiet`,
  `::test_making_the_venv_is_said`, `::test_a_quiet_step_after_pip_says_it_is_still_working`
- **Lesson:** Moving noise to the log is half the job: whatever took its place in the window must still say that
  something is happening.

#### B-544 · A model download that lost its connection showed urllib's error and waited a day · medium
- **Where:** `scripts/fetch_models.py` (`download`, `fetch`, `plain_reason`, `setup`), `scripts/launch.py`
  (`ensure_models`, `models_due`, `readiness`, `one_engine`)
- **Symptom:** 2 of 4 cold installs lost the connection to www.modelscope.cn. The window said *could not get
  ch_PP-OCRv5_rec_mobile_infer.onnx: URLError: <urlopen error [Errno 104] Connection reset by peer>*, *Run this again
  when the download works* (run what?), *Tried again tomorrow*, and the readiness line said the PP-OCRv5 models *come
  from the offline bundle's models folder*, of a GitHub ZIP that has none.
- **Cause:** one try per file; the launcher ran `fetch_models.main`, written for a person at the command line; the
  failure was recorded with the day in it; the readiness line had one wording for both sources.
- **Reproduce:** a first run from GitHub's ZIP on a flaky connection (or a proxy that resets every connection).
- **Reproduced:** yes (old_run1.log in the audit's up/ and up2/)
- **Fix:** each download is tried 3 times (after 3 s and 10 s; a 404 is not tried again); the window says *Couldn't
  get X: the connection dropped, tried 3 times.* and *Couldn't download the OCR models (the connection dropped). AP
  Coder will try again next time it starts; scans are still read with one engine meanwhile.*, the error itself goes
  to the log; the next start tries again (twice a file, 20 s each), up to 3 starts a day, then *tomorrow*; the
  readiness line says *one engine for now: the PP-OCRv5 models couldn't be downloaded yet; AP Coder tries again when
  it starts* from the code alone, and the bundle wording only for a bundle.
- **Test:** `tests/test_launch.py::test_a_failed_model_download_is_tried_again_and_said_plainly`,
  `::test_plain_reasons_for_a_failed_download`, `tests/test_launch_build.py::test_the_ocr_line_says_where_the_models_come_from`
- **Lesson:** A download over someone else's network fails now and then: try again before saying so, say it in the
  words of the person reading, and say what happens next.

#### B-545 · The window of an older AP Coder just ended when a newer one took over · low
- **Where:** `scripts/running.py` (`take_over`, `note_takeover`, `taken_over`), `scripts/launch.py`
  (`start_dashboard`, `main`, `keep_open`)
- **Symptom:** the old window closed (Windows) or ended with nothing said, or with *AP Coder stopped on its own*;
  the pilot tester could not tell an update from a crash.
- **Cause:** the old start only saw its server end; nothing told it why.
- **Reproduce:** start AP Coder, unzip a newer build (over it or elsewhere), double-click the new one.
- **Reproduced:** yes
- **Fix:** before stopping it, the new start notes in the settings file which port it stops; the old window, seeing
  its dashboard end with that note fresh, says *A newer AP Coder took over; you can close this window.*, frees the
  launch lock (the new start waits for it), and waits for Enter instead of vanishing (not with `AP_NO_PAUSE=1` or no
  one at the window). Only windows of 0.3.0 and later can say it (N-252).
- **Test:** `tests/test_launch_build.py::test_the_old_window_says_a_newer_ap_coder_took_over`,
  `::test_an_old_note_or_another_port_is_not_this_window`, `::test_the_window_frees_the_lock_before_it_waits`
- **Lesson:** When one process ends another on purpose, leave a note the other can read: the reason is the message.

#### B-546 · The first run's counts and GL line did not add up · low
- **Where:** `scripts/launch.py` (`_ensure_packages`, `pip_install`, `models_said`, `setup_check_lines`),
  `scripts/fetch_models.py` (`came_with_package`)
- **Symptom:** *Installing AP Coder's packages (15 missing)* then *Installed 56 packages*; *OCR models ready: 1
  already there, 2 downloaded* on a brand-new computer; *Table finder: ... downloaded into <the whole data folder
  path>/models.*; *GL accounts: none imported yet, so the sample ones are used* next to *Setup checks: all 9 passed
  (the sample GL accounts, ...)* while the GL page showed 0 accounts.
- **Cause:** pip installs what the 15 need too; the "already there" model is RapidOCR's angle classifier, inside its
  package; the table finder line came from the command-line tool; the sample chart's checks were counted as passes
  of this setup.
- **Reproduce:** a first run from GitHub's ZIP.
- **Reproduced:** yes (run1.log of the audit)
- **Fix:** *Installing AP Coder's packages: 15 missing, with the packages they need in turn* and *Installed 56
  packages (the 15 missing and the ones they need)*; *OCR models ready: 2 downloaded, and the table finder's; 1 came
  with the OCR package.* (no path); *· GL accounts: none imported yet (import yours in GL accounts & tax, or use the
  sample ones)* as a note, and *Setup checks: all 7 passed (tax mapping, coding rules, settings)* without the
  sample's GL accounts and cost centers.
- **Test:** `tests/test_launch.py::test_the_start_says_no_gl_accounts_were_imported_yet`,
  `tests/test_launch.py::test_the_model_count_says_where_each_came_from`
- **Lesson:** Two numbers on two lines are read together: say how they relate, or the reader decides something went
  wrong.

#### B-547 · The LM Studio line said to start AP Coder again · low
- **Where:** `scripts/launch.py` (`lm_studio_line`)
- **Symptom:** *To use it: open LM Studio, Developer tab → Start server, then start AP Coder again*; the pilot runs
  LM Studio, and the dashboard picks it up without a restart.
- **Cause:** written before Settings → Reading had *Check again*.
- **Reproduce:** start AP Coder with LM Studio closed.
- **Reproduced:** yes
- **Fix:** *LM Studio isn't running yet: until it is, invoices are read by OCR only and wait for a person. To start
  it: open LM Studio, Developer tab → Start server; then press Check again in AP Coder's Settings → Reading (no need
  to restart AP Coder)*.
- **Test:** `tests/test_launch_build.py::test_lm_studio_not_running_says_how_to_start_it_and_to_check_again`
- **Lesson:** Advice in a start-up message ages with the app: check it against what the dashboard can do now.

#### B-548 · pyproject allowed Python 3.10 while everything else said 3.11 · low
- **Where:** `pyproject.toml` (`requires-python`, ruff `target-version`), `.github/workflows/ci.yml` (tests matrix),
  `ap_coder/ops/doctor.py` (`_python_check`), `scripts/launch.py` (`venv_fits`, `bootstrap`)
- **Symptom:** `requires-python = ">=3.10"` and CI tested 3.10, while APProcessor.bat / .command, the launcher, the
  bundle (3.11 to 3.14), the doctor and the guides said 3.11 or newer.
- **Cause:** the floor was raised in the launcher and the guides, not in the package or CI.
- **Reproduce:** compare them.
- **Reproduced:** yes
- **Fix:** 3.11 everywhere: `requires-python = ">=3.11"`, ruff `py311`, CI tests 3.11, 3.12, 3.13 and 3.14, the doctor
  fails a 3.10. A `.venv` made with 3.10 by AP Coder 0.1 (PR #1 and #2 allowed it) is made again with the newer Python
  (it could no longer install 0.3), instead of being reused.
- **Test:** `tests/test_launch_build.py::test_one_python_version_everywhere`,
  `::test_a_venv_made_with_an_older_python_is_made_again`, `tests/test_config_and_cli.py::test_doctor_python_range_matches_the_launcher`
- **Lesson:** A version floor lives in several places: change the package metadata and CI with the launcher, or the
  one that is not changed is the one that is tested.

#### B-549 · No CI job ran GitHub's ZIP the way the pilot tester does · medium
- **Where:** `.github/workflows/ci.yml` (`windows-github-zip`)
- **Symptom:** every Windows job set `AP_ALLOW_INTERNET=1` or used the offline bundle, from a plain folder, with no
  Mark of the Web; none updated one ZIP with a newer one. B-542 and B-544 passed CI.
- **Cause:** the jobs were written for the offline bundle, the path the pilot was first meant to use.
- **Reproduce:** read the workflow.
- **Reproduced:** yes
- **Fix:** a Windows job makes two `git archive` ZIPs (this commit and an empty commit on top), marks them as
  downloaded, `Expand-Archive`s the older into `Pilot tester Ré\` (a space and an accent), double-clicks
  `APProcessor.bat` with no `AP_ALLOW_INTERNET` until the dashboard answers (checking the first run's lines and the
  build on `/healthz`), leaves an invoice and a `.env` line in the data folder, unzips the newer ZIP over it while it
  runs and double-clicks again: *Updated AP Coder from ... (build old ...) to ... (build new ...)*, the self-check,
  the old window's *A newer AP Coder took over*, the data kept, Settings → About showing the new build.
- **Test:** `tests/test_ci_workflow.py::test_ci_runs_github_zip_as_the_pilot_tester_does`
- **Lesson:** Test the path the user takes, not the one the plan said they would take.

#### B-550 · The bundle builder's dry run did not say where it downloads from · low
- **Where:** `scripts/build_offline_bundle.py` (`plan`, `download_hosts`, `main`)
- **Symptom:** IT behind a firewall that lets only named hosts through had no list: `--dry-run` printed pip commands
  and model names, not the addresses.
- **Cause:** the plan kept each model's checksum, not its URL.
- **Reproduce:** `python scripts/build_offline_bundle.py --dry-run`
- **Reproduced:** yes
- **Fix:** the plan has each model's `url`, `download_hosts` (`pypi.org`, `files.pythonhosted.org`,
  `www.modelscope.cn`) and what is not in the bundle (LM Studio and its models); stderr says the same in three lines
  (stdout stays the JSON plan). PILOT.md §B has a *For IT* box: what the default build holds, why it carries the
  `azure` extra, what it leaves out.
- **Test:** `tests/test_launch_build.py::test_the_dry_run_names_every_address_the_build_downloads_from`
- **Lesson:** A build that downloads needs a list of where from, for whoever runs the firewall.

#### Found by the review of these fixes

#### B-551 · The clean-up could delete a newer offline bundle's folder · high
- **Where:** `ap_coder/core/paths.py` (`install_identity`, `compare`, `is_older`), `scripts/upgrade.py`
  (`replaced_by`, `clean_up`), `scripts/launch.py` (`older_installs`)
- **Symptom:** a GitHub ZIP started after a newer offline bundle of the same version, in Canada, took the bundle for
  the older one: after its self-check the clean-up removed the bundle's `.venv` and then its whole folder.
- **Cause:** B-542 ordered a ZIP by its commit's time (UTC) and a bundle by its manifest's `built` (IT's local time,
  no zone), compared as text: 17:30 in Toronto sorted before 20:00 UTC. And the clean-up took every install on record
  that was not newer, so a build that could not be ordered at all counted as older.
- **Reproduce:** ZIP X stamped 2026-10-21T20:00Z, bundle Y stamped 21:00Z and built 17:30 local; start X.
- **Reproduced:** yes (`is_older(Y, X)` was true)
- **Fix:** a stamped build (a ZIP, or a bundle from 0.3.0 on: its builder writes the stamp) is ordered by its commit's
  time in UTC; a bundle from before the stamp by its build time, and only against another such bundle. `compare`
  answers older, newer, same (one commit, or the same code) or unknown; the clean-up takes only a folder certainly
  older or another copy of this very build, and `clean_up` itself refuses any other (*Kept X: it can't be told
  whether it is older than this AP Coder, so nothing in it was touched*). Never delete when in doubt.
- **Test:** `tests/test_launch_build.py::test_a_newer_bundle_is_never_taken_for_an_older_one_across_time_zones`,
  `::test_a_build_that_cant_be_ordered_is_never_cleaned_up`
- **Lesson:** Two times compare only when they are on one clock: a local time without its zone is not comparable
  with UTC, and a comparison that can't be made must say so rather than answer.

#### B-552 · Python 3.10 stopped with AttributeError before it was told it is too old · low
- **Where:** `ap_coder/core/paths.py` (`_utc`)
- **Symptom:** `python scripts/launch.py` under 3.10: *AttributeError: module 'datetime' has no attribute 'UTC'*.
- **Cause:** `datetime.UTC` is new in 3.11 (ruff's py311 target suggests it); the launcher's first phase runs on any
  Python to say which one it needs.
- **Reproduce:** `python3.10 -c "from ap_coder.core import paths; paths.install_identity()"`
- **Reproduced:** yes
- **Fix:** `datetime.timezone.utc` (with a note for ruff).
- **Test:** `tests/test_launch_build.py::test_build_times_need_no_python_3_11`
- **Lesson:** Code that exists to tell an old Python it is too old must itself run on that Python.

#### B-553 · An older build over a newer one said "Updated", and a copied folder "from 0.3.0 to 0.3.0" · low
- **Where:** `scripts/launch.py` (`update_line`)
- **Symptom:** *Updated AP Coder from 0.3.0 (build new) to 0.3.0 (build old)*; *Updated AP Coder from 0.3.0 to 0.3.0.*
- **Cause:** any change of fingerprint was said as an update.
- **Reproduce:** unzip an older ZIP over a newer one; change a file of a folder with no build stamp.
- **Reproduced:** yes
- **Fix:** *Replaced AP Coder X with an older build, Y.* (it still starts and runs the self-check: going back is
  sometimes what IT wants, and a database a newer AP Coder changed is refused anyway); *AP Coder's files in this
  folder changed since it was last set up: checking it again.* when nothing names the two.
- **Test:** `tests/test_launch_build.py::test_an_older_build_unzipped_over_a_newer_one_is_said_as_such`
- **Lesson:** Say the direction of a change, not only that there was one.

#### B-554 · A network that dropped the models' site silently held a first setup for about 10 minutes · medium
- **Where:** `scripts/fetch_models.py` (`fetch`, `setup`, `unreached`), `scripts/launch.py` (`ensure_models`)
- **Symptom:** each of 4 model files waited out 3 tries of 60 s; every later start about 2 minutes more; the window
  said only that each could not be got.
- **Cause:** every file was tried on its own, whatever had happened to the one before.
- **Reproduce:** a firewall that drops www.modelscope.cn without answering.
- **Reproduced:** yes (in the review)
- **Fix:** a site not reached at all (no answer in time, no such name, refused) is asked for nothing more in that
  run; the first setup waits 30 s an answer (3 tries), a later start 10 s (1 try); the window says once *AP Coder
  couldn't reach www.modelscope.cn (a firewall may block it): ask IT, or use the offline bundle.* (measured: 104 s
  for a first setup, 11 s for a later start). The *Still working* line comes less and less often when it would say
  the same again.
- **Test:** `tests/test_launch.py::test_a_site_that_never_answers_is_asked_once_and_said_once`
- **Lesson:** When a server does not answer, the next request to it will not either: give up on the server, not on
  each file.

#### B-555 · The pip progress line could stay on one package's name · low
- **Where:** `scripts/launch.py` (`PipProgress.status`)
- **Symptom:** *Installing packages: 40 of 56 (Odd.Name)...* to the end, when a `.dist-info` was spelled otherwise.
- **Cause:** the line named the first package not yet found installed.
- **Reproduce:** a package list where one name never matches its `.dist-info` folder.
- **Reproduced:** yes
- **Fix:** it names the package after the last one found installed (pip installs in its list's order).
- **Test:** `tests/test_launch_build.py::test_pip_progress_never_sticks_on_a_name_it_cant_match`
- **Lesson:** Progress worked out from outside must move on when one item can't be matched.

#### B-556 · A 3.10 .venv was remade before the old dashboard using it was stopped · low
- **Where:** `scripts/launch.py` (`bootstrap`, `make_way_for_venv`)
- **Symptom:** an old AP Coder window still open from that .venv: Windows refused to delete it (*Could not remove the
  .venv folder*).
- **Cause:** the first phase remakes .venv; old dashboards were stopped only in the second.
- **Reproduce:** a 0.1 install made with Python 3.10, left running; unzip 0.3.0 over it and double-click.
- **Reproduced:** yes (in the review, on its order of steps)
- **Fix:** before .venv is made again, the older AP Coder running is stopped (as the second phase does).
- **Test:** `tests/test_launch_build.py::test_an_older_venv_is_made_again_only_after_old_dashboards_stopped`
- **Lesson:** Stop what uses a folder before deleting the folder.

#### B-557 · The new CI job could fail wrongly, and checked the Mark of the Web only on the way in · low
- **Where:** `.github/workflows/ci.yml` (`windows-github-zip`, `windows-pilot-launcher`)
- **Symptom:** a first run whose only failed download was the table finder's (*Couldn't download the table finder's
  model*) failed the job; the Mark of the Web was put on the files but never checked as taken off APProcessor.bat;
  the later start's check for *Fetching the OCR models* matched nothing the launcher says.
- **Cause:** the expected lines were written before the final wording.
- **Reproduce:** read the workflow against the launcher's lines.
- **Reproduced:** yes
- **Fix:** the job accepts either download message (and the unreachable one), checks APProcessor.bat has lost its
  Zone.Identifier while the other files keep it; the later start checks for *Downloading the OCR models*. PILOT and
  UPGRADING say the 0.2.0 window just ends on this update (only 0.3.0 and later say a newer one took over).
- **Test:** `tests/test_ci_workflow.py`
- **Lesson:** A CI check for a line is only as good as the line's current wording: grep for what the code says.

#### B-558 · A data folder inside an old GitHub ZIP folder was deleted with it · high
- **Where:** `scripts/upgrade.py` (`removable`, `holds_data`, `data_folders`, `user_files`)
- **Symptom:** with the data folder recorded as `OLD/data/mine`, `OLD/data` or `OLD/samples/mydata` (database and
  invoices in it), the clean-up after an update said *Removed the older AP Coder folder ...* and the data was gone.
- **Cause:** for a folder without a bundle manifest, `user_files` counts `data/` and `samples/` as AP Coder's own and
  exempts a `.db` there; nothing asked whether a data folder lay inside the folder being removed.
- **Reproduce:** an old GitHub ZIP folder set up, `data_dir` recorded inside its `data/`, `clean_up` from a new folder.
- **Reproduced:** yes (in the review, and `tests/test_update_cleanup_data.py` before the fix)
- **Fix:** a folder is kept whole (*... because it holds your data folder (X)*) when this start's data folder, the
  data folder now, the one on record or the database's folder (`AP_DB_PATH`) is inside it and still holds files, or
  when a database (`.db`, `.sqlite`, their journals) or an `invoices` folder is anywhere in it (its `.venv` and caches
  aside). A clean-up never deletes a database file.
- **Test:** `tests/test_update_cleanup_data.py::test_a_data_folder_inside_the_old_folder_is_never_deleted`,
  `::test_a_database_or_invoices_anywhere_in_the_old_folder_keep_it`
- **Lesson:** Before deleting a folder, ask whether the person's data is in it, wherever the rules say data belongs:
  data ends up where people put it.

#### B-559 · A recorded folder that was no longer an AP Coder install lost its .venv and models · medium
- **Where:** `scripts/upgrade.py` (`clean_up`)
- **Symptom:** a folder on record whose `ap_coder/` was gone still had its `.venv` and `models/` removed (*removed its
  .venv and models; the folder itself stays ...*); and when the data folder was the old folder itself, its `models/`
  (where the table finder's model is kept) went.
- **Cause:** the partial clean-up ran for any folder `removable` refused, install or not.
- **Reproduce:** record an install, delete its `ap_coder/`, start a newer one.
- **Reproduced:** yes
- **Fix:** only an AP Coder install is touched; any other recorded folder is said (*Kept X: it is no longer an AP Coder
  folder, so nothing in it was touched*) and taken off the record. A folder that is the data folder keeps its
  `models/`; a link inside one is never followed.
- **Test:** `tests/test_update_cleanup_data.py::test_a_recorded_folder_that_is_no_longer_an_install_is_not_touched`,
  `::test_the_data_folders_models_are_kept_when_the_data_folder_is_the_old_folder`
- **Lesson:** A record says what a folder was, not what it is: check again before acting on it.

#### B-560 · A link to an old folder was renamed and said to be "mostly removed" at every start · low
- **Where:** `scripts/upgrade.py` (`clean_up`, `is_link`)
- **Symptom:** a symlinked old folder: the link became `<link>.removing-<date>`, the window said *most of it was
  removed; the rest is removed on a later start*, again at later starts; nothing behind it was deleted.
- **Cause:** `remove_folder` renames first; renaming a link moves only the link.
- **Reproduce:** record a symlink to an older install as an install, start a newer one.
- **Reproduced:** yes
- **Fix:** links (and Windows junctions) are never renamed, followed or emptied: *Skipped X: it is a link to another
  folder, so nothing was removed*, and they are taken off the record.
- **Test:** `tests/test_update_cleanup_data.py::test_a_link_to_an_old_folder_is_skipped_not_renamed`
- **Lesson:** Clean-up code that walks or renames must decide what to do with links before it touches anything.

#### B-561 · The queue held the coding accuracy against the target too early, counting kept guesses · low
- **Where:** `ap_coder/web/pages/review.py` (`kpis`)
- **Symptom:** after 50 lines approved, mostly kept guesses, the queue showed the distance to the 90% target while
  the Learning page still said *GL suggestions improve as you approve*.
- **Cause:** the queue passed `lines_reviewed` (guesses included) to `accuracy_note`; the accuracy itself counts only
  measured lines (B-475), as the Learning page does.
- **Reproduce:** metrics with 400 lines reviewed, 10 measured; open the queue.
- **Reproduced:** yes
- **Fix:** the queue passes `lines_measured`.
- **Test:** `tests/test_trust_review.py::test_queue_accuracy_note_counts_measured_lines_not_guesses`
- **Lesson:** Two screens showing one figure must take it from the same count.

#### Looked at and left (install, launcher and updates)

- **N-251** · A GitHub ZIP unzipped over an older one leaves the files the newer one no longer has (the offline
  bundle's manifest lists its files, so a bundle removes them: `upgrade.prune_stale`; a ZIP has no list). A file
  left in `ap_coder/` could still be imported; none is today. A ZIP could carry a file list, but GitHub builds it.
- **N-252** · A window of AP Coder 0.2.0 or older, still open when 0.3.0 takes over, still just ends: the message
  comes from the old window's own code (B-545).
- **N-253** · The `windows-github-zip` CI job (B-549) was written without a Windows computer to run it on; its first
  runs may need a fix of the job itself (the steps it checks were run on Linux with the same ZIPs).
