# Bug log

One line per bug: `B-NNN · what broke · severity · the test that keeps it fixed`. A suspicion that didn't reproduce is
an `N-NN` line. B-01 to B-1441 and the N- notes (to 29 October 2026): [archive/bugs-2026-10.md](archive/bugs-2026-10.md);
look one up with `grep -n "B-1234 " docs/archive/bugs-*.md`.

How to add one: the test first (it fails), then the fix, then a line at the end of this file, numbered after the
last B- line (`grep -o "B-[0-9]*" docs/BUGS.md | tail -1`).

## From B-1442

- **B-1442** · Settings → Reading's Check again and Load never retried a page reader LM Studio had failed to load (not enough memory): the failure was kept for half an hour whatever the person did, since `forget_reader_failures` lost its caller when the Reading tab was simplified · medium · `tests/web/test_reading_pages.py::test_check_again_and_load_ask_lm_studio_to_load_a_model_that_failed_before`
- **B-1443** · `python scripts/same_behaviour.py` with no probes named stopped at once ("invalid choice"): argparse checked the default list of probes against the choices as one value, so only naming the probes worked · low · `tests/meta/test_same_behaviour.py::test_with_no_probes_named_the_default_ones_run`
