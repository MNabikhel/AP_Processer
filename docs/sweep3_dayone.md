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
- **Fix:** one definition (D-38), `insights.corrected()`: a value changed or a line added or removed is a change, a blank filled in is not; `approved_as_coded()` gives the pair Insights and Learning show, both under the label "Approved as coded" with "N of M needed no change"; Activity says "N approvals: X approved as coded, Y corrected" with the same rule; Coding accuracy is written to one decimal on the queue as on Learning.
- **Test:** `tests/test_sweep3_dayone.py::test_insights_learning_activity_and_the_queue_count_approved_as_coded_alike`, `tests/test_sweep3_dayone.py::test_a_change_is_a_value_the_reviewer_changed_or_a_line_added_not_a_blank_filled_in`
- **Lesson:** Don't compute one business figure in three pages with three rules: define it once and import it.

### Improvements
- Review screen, line grid: "Apply to all lines…" gives one GL account and / or cost center to every line that has none (lines already coded keep theirs; POST /review/<id>/fill-lines, like the other grid actions); when every past approval of the vendor used one cost center, it is pre-selected there as a suggestion (`invoice_view.usual_cost_center`; the coder itself is unchanged). `tests/test_sweep3_dayone.py::test_apply_to_all_lines_codes_only_the_lines_that_have_none`, `tests/test_sweep3_dayone.py::test_apply_to_all_lines_refuses_a_code_not_in_the_lists`, `tests/test_sweep3_dayone.py::test_a_vendors_one_usual_cost_center_is_offered_for_its_lines`, `tests/test_web_invoice.py::test_in_a_browser_apply_to_all_lines_fills_the_cost_centers_left_blank`
- Second approval on one laptop: Settings → Review ("Approval and payment"), the waiting invoice's screen and the Second approval tab say it takes another person on another computer login, so one Windows login can't give it; how to test it (a second Windows account on the laptop, or a colleague over the office network); and that Send back returns it to the queue. The control is unchanged. `tests/test_sweep3_dayone.py::test_the_second_approval_says_how_to_give_it_when_one_login_cannot`
- Exports, JD Edwards: an invoice held back for its supplier number (AN8 missing or not a JDE address number) links straight to Settings → JD Edwards E1 → Supplier address numbers (`/settings/jde#an8`), in its "Why not" cell and in the message above the list (`jde.an8_problem`). `tests/test_sweep3_dayone.py::test_an_export_held_back_for_a_supplier_number_links_to_the_address_numbers`
