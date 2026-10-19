# Bug log

Every bug found by a bug sweep: what went wrong, how to reproduce it, the fix, the test that keeps it fixed, and the lesson
("don't do this again"). Each lesson is also a line in the "### Bug lessons" list of the CLAUDE.md of the folder its fix is in.
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
| **Total** | **103** | **58** | **45** |

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
