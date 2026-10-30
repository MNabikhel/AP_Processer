# Bug log

One line per bug: `B-NNN · what broke · severity · the test that keeps it fixed`. A suspicion that didn't reproduce is
an `N-NN` line. B-01 to B-1441 and the N- notes (to 29 October 2026): [archive/bugs-2026-10.md](archive/bugs-2026-10.md);
look one up with `grep -n "B-1234 " docs/archive/bugs-*.md`.

How to add one: the test first (it fails), then the fix, then a line at the end of this file, numbered after the
last B- line (`grep -o "B-[0-9]*" docs/BUGS.md | tail -1`).

## From B-1442

- **B-1442** · Settings → Reading's Check again and Load never retried a page reader LM Studio had failed to load (not enough memory): the failure was kept for half an hour whatever the person did, since `forget_reader_failures` lost its caller when the Reading tab was simplified · medium · `tests/web/test_reading_pages.py::test_check_again_and_load_ask_lm_studio_to_load_a_model_that_failed_before`
- **B-1443** · `python scripts/same_behaviour.py` with no probes named stopped at once ("invalid choice"): argparse checked the default list of probes against the choices as one value, so only naming the probes worked · low · `tests/meta/test_same_behaviour.py::test_with_no_probes_named_the_default_ones_run`
- **B-1444** · A pilot tester looking at an invoice's lines could not find how to split one across GL accounts: *Split a line…* was only under the line table and in a line's right-click menu · medium · `tests/web/test_web_picker.py::test_in_a_browser_each_line_has_a_split_button_that_opens_the_split_for_it`
- **B-1445** · A GL code (or an account's name) typed in an account box, then a click elsewhere, was lost: the box went back to the account it had, so the code typed and the account kept could differ; only Tab and Enter picked. Now a whole code, or words only one account has, is picked with its name, and an exact code comes first in the list · medium · `tests/web/test_web_picker.py::test_in_a_browser_a_code_or_a_name_typed_then_clicking_away_picks_its_account`
- **B-1474** · `server-update` left the company server stopped when a step raised instead of failing (the backup on a full disk, the new folder's Python blocked by antivirus, Ctrl+C during the few-minute set-up): no rollback ran, the service stayed stopped and a half-migrated database stayed; now anything after the stop puts the backup back and starts the old version · high · `tests/ops/test_server_update.py::test_a_step_that_raises_still_puts_the_old_version_back_and_starts_it`
