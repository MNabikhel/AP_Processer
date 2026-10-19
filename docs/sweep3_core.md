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
