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

#### B-198 · "Not in the vendor list" warned on every new vendor when no vendor master was imported · medium
- **Where:** `ap_coder/web/static/js/invoice.js:402` (`showVendorMatch`)
- **Symptom:** with no vendor master imported (the sample setup), once one invoice was approved every other new vendor showed an amber "Not in the vendor list: pick one, or add it as a new vendor." under the vendor field, asking the pilot tester to fix something that isn't wrong.
- **Cause:** `store.vendor_choices()` falls back to the vendors approved before when there is no vendor master, so the list is not empty, and the script warned whenever the typed name was not in that list, whether or not a vendor master is enforced (`vendors.enforced`).
- **Reproduce:** no vendor master; approve one invoice; open another and type a new vendor's name.
- **Reproduced:** yes (`'gpp_maybe\nNot in the vendor list: pick one, or add it as a new vendor.'`)
- **Fix:** the warning (and "Pick the vendor from the list") only with a vendor master; without one a new vendor gets no note, and a vendor approved before reads "Approved before" (not "In the vendor list").
- **Test:** `tests/test_web_invoice.py::test_in_a_browser_not_in_the_vendor_list_is_said_only_once_a_vendor_master_is_imported`
- **Lesson:** Don't warn against a fallback list as if it were the master list it stands in for.

### Improvements
- Review screen, line grid: "Apply to all lines…" gives one GL account and / or cost center to every line that has none (lines already coded keep theirs; POST /review/<id>/fill-lines, like the other grid actions); when every past approval of the vendor used one cost center, it is pre-selected there as a suggestion (`invoice_view.usual_cost_center`; the coder itself is unchanged). `tests/test_sweep3_dayone.py::test_apply_to_all_lines_codes_only_the_lines_that_have_none`, `tests/test_sweep3_dayone.py::test_apply_to_all_lines_refuses_a_code_not_in_the_lists`, `tests/test_sweep3_dayone.py::test_a_vendors_one_usual_cost_center_is_offered_for_its_lines`, `tests/test_web_invoice.py::test_in_a_browser_apply_to_all_lines_fills_the_cost_centers_left_blank`
- Second approval on one laptop: Settings → Review ("Approval and payment"), the waiting invoice's screen and the Second approval tab say it takes another person on another computer login, so one Windows login can't give it; how to test it (a second Windows account on the laptop, or a colleague over the office network); and that Send back returns it to the queue. The control is unchanged. `tests/test_sweep3_dayone.py::test_the_second_approval_says_how_to_give_it_when_one_login_cannot`
- Exports, JD Edwards: an invoice held back for its supplier number (AN8 missing or not a JDE address number) links straight to Settings → JD Edwards E1 → Supplier address numbers (`/settings/jde#an8`), in its "Why not" cell and in the message above the list (`jde.an8_problem`). `tests/test_sweep3_dayone.py::test_an_export_held_back_for_a_supplier_number_links_to_the_address_numbers`
