# Sweep 3 · day one

A pilot tester's first day with AP Coder (load the sample setup, import the ten sample invoices, review, approve,
export, look around the reports). What they ran into, fixed with a failing test first.

#### B-197 · The review queue called "Ready" an invoice its review screen won't approve · high
- **Where:** `ap_coder/web/pages/review.py:116` (`attention`, used by `review_page` and invoice.py's `navigation`); `ap_coder/coding/review.py:100` (`codes_to_pick`)
- **Symptom:** with the sample setup every uploaded sample invoice whose checks found no error showed a green "Ready" in the queue (and under "Ready" in the filter), yet its review screen said "Pick a cost center for lines 1, 2, 3 … to approve" with Approve disabled.
- **Cause:** the queue's status was the stored `requires_review` flag (an error, or confidence under the threshold); a line without a cost center is only a warning (`CC_UNASSIGNED`), so it never set the flag, while the review screen's Approve bar refuses any line without a GL account or (with cost centers imported) a cost center. Two rules for one question.
- **Reproduce:** demo data; blank the cost centers of a clean invoice's lines; open /review: the row says Ready; /review/<id>/check answers `allow: false`.
- **Reproduced:** yes (`'<li><a class="rq-row ready" href="/review/6" …Agence Créative Mont-Royal inc.'` while its check answered `allow: False`)
- **Fix:** one rule, `codes_to_pick()` in coding/review.py, asked by the review screen's Approve bar (invoice_view.analyse) and by the queue's new `attention()`; the queue's Ready / Needs attention, its filters, the Waiting tile ("N need attention") and the review screen's Previous/Next within a filter all use it. A row that needs attention says why under its pill ("Pick a cost center for lines 1, 2, 3", "1 error to fix", "Confidence under 85%").
- **Test:** `tests/test_sweep3_dayone.py::test_the_queue_never_calls_ready_an_invoice_whose_review_screen_blocks_approval`, `tests/test_sweep3_dayone.py::test_every_invoice_the_queue_calls_ready_can_be_approved_on_its_screen`
- **Lesson:** Don't answer "can this be approved?" with a stored flag in one place and the approval rule in another: ask one shared function.
