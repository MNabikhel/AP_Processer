# ap_coder/intake: invoices coming in (pictures to PDF, saved emails, triage, the emails set aside)

How invoices arrive before they are read: a photo or scan saved as a picture is made into a PDF, saved emails (.eml,
Outlook .msg) are unpacked, each email and attachment is sorted (invoice, statement, needs a person), and the emails
set aside wait in the inbox for a person.
Grouped here from the flat `ap_coder/` (docs/decisions.md D-33): `ap_coder.mailbox` and the other old paths are aliases for one release.
The old module `ap_coder/intake.py` is now `pictures.py` (the package took its name).

**Depends on:** `ap_coder/ai`, `ap_coder/capture`
**Used by:** `ap_coder/ai`, `ap_coder/bench`, `ap_coder/capture`, `ap_coder/erp`, `ap_coder/ops`, `ap_coder/web`, `ap_coder/web/pages`

## Interface
- `to_pdf()` (pictures.py): a picture -> the PDF it is stored, read and stamped as (`InvoicePipeline.process` calls it first).
- `mailbox.unpack_folder()` -> `mailbox.unpack()` (.msg via `read_msg()`, msgfile.py; sorted by `classify_email()`, triage.py):
  the email then moves to `emails/`, or `emails/needs a person/`; statements to `statements/`; invoices to the invoices folder.
- `needs_person()` / `mark_done()` (inbox.py): the emails triage set aside, and the actions on them.
- `sheet_unzips_too_big()` (triage.py): every openpyxl reader of an emailed or uploaded workbook calls it first.

## Gotchas and decisions
- Email files are untrusted: cap sizes before parsing (each part, their sum, the walk of a .msg directory).
- `__init__.py` forwards the old `ap_coder.intake` module's names (`from ap_coder.intake import to_pdf`) to pictures.py for
  one release; it never forwards its own submodules' names. Patch `ap_coder.intake.pictures`, not the package.

### Bug lessons
From docs/BUGS.md (one line each; the entry has the repro and the fix).
- B-61: Don't bound each piece and forget the sum. `tests/test_msg_and_triage.py::test_attachments_sharing_one_stream_cannot_fill_the_memory`
- B-62: Don't put a per-call limit on a walk that runs once per node. `tests/test_msg_and_triage.py::test_a_directory_walk_reads_each_entry_once`
- B-63: Don't hand an untrusted zip to a library before checking its unzipped sizes. `tests/test_msg_and_triage.py::test_a_spreadsheet_that_unzips_to_far_more_is_not_opened`
- B-64: Don't size-cap the parts of a file name and then join them uncapped. `tests/test_mailbox.py::test_a_long_chinese_email_and_attachment_name_still_fit_one_file_name`
- B-65: Don't log "done" when the step that makes it done failed. `tests/test_mailbox.py::test_an_email_that_cannot_be_moved_is_logged_once`
- B-71: Don't apply the limit after the costly parse. `tests/test_mailbox.py::test_an_email_of_thousands_of_tiny_parts_is_refused_before_it_is_parsed`
- B-116: Don't make the result of a graph walk depend on the order the nodes are stored in. `tests/test_msg_and_triage.py::test_the_order_of_directory_entries_never_drops_an_attachment`
- B-117: Don't apply a "most recent N" window before the filter that decides what is still open. `tests/test_msg_and_triage.py::test_an_open_email_stays_in_needs_a_person_however_many_were_sorted_after_it`
- B-128: Don't use a RecursionError as a limit: count what makes the code recurse and refuse it before. `tests/test_mailbox.py::test_an_email_forwarded_inside_itself_a_thousand_times_is_refused_before_it_is_parsed`

## Files
- `__init__.py`: the package docstring, and the old module's names forwarded to pictures.py.
- `pictures.py` (images to PDF), `mailbox.py` (unpack emails); `msgfile.py` (.msg reader), `triage.py` (sort emails); `inbox.py` (emails set aside).
