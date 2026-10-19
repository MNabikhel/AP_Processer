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

### Not fixed
- **N-XX** · ai · `page_worker.reader_alive` (an invoice's "being read" mark) has the same reused-process-id weakness as the self-test mark (B-177), but there the store's `STALE_READING_HOURS` (2 hours) already puts a stale read back in line, so it only delays; left as is.
- **N-XX** · ai · `Store.learning_rows` (B-175) gives an old vendor's invoice a list of its own, so the suggestion index (one slot) is rebuilt when invoices of such vendors alternate in a batch (about 0.3 s at 50,000 lessons); a per-vendor index would avoid it, a bigger change than this fix.
