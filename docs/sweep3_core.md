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
