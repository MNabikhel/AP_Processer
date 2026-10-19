# Sweep 3: intake (emails, pictures, the inbox, the demo, the command line)

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

### Not fixed
- **N-XX** · intake · A photo over Pillow's ~179 MP limit is still refused (B-148 only words it truthfully). Reading it would mean raising `Image.MAX_IMAGE_PIXELS`, which is process-wide and shared by the reading threads, or decoding JPEGs at a reduced size (`draft`); either is a decision about memory on a laptop, not a bug fix.
- **N-XX** · intake · A .msg picture's Content-ID referenced only from an RTF body (no HTML stream) counts as inline only when Outlook marked it hidden: the compressed RTF is not decompressed to look for `cid:`.
