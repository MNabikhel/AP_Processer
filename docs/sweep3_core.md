# Sweep 3: ap_coder/core

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
