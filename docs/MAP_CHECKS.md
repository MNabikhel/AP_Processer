# Map checks: how the codebase map was verified

A record of the checks on the codebase map (each folder's CLAUDE.md, `ap_coder/MODULES.md`, `docs/decisions.md`)
in stages 2 to 4. A **cold-start round** gives a fresh agent only the maps and 5 "where is X / what breaks if I
change Y" questions, then grades its answers against the code. An **accuracy pass** has an independent agent check
the maps' statements against the code. Times are from the session transcript (UTC). Paths are main after stage 5.

## Cold-start round 1 (2026-10-17, map-draft; landed in stage 2, PR #25)
Questions summarised from the agent prompt; the agent listed 13 gaps, all closed:
1. Where the GST/HST check digit is enforced and what else to check. Partly right: it found capture's check, not
   `valid_gst_number()` / `gst_check_digit_ok()` in `ap_coder/coding/tax.py`. Fix: the core map names them and
   says capture's `normalize.luhn_ok()` repeats the check digit, so both change together.
2. How an imported invoice reaches the AI reviewer, and what stops approval before it agrees. Mostly right. Fix:
   the maps name the pipeline's `verifier.hold()`, `page_worker._review_one()` calling `verify_one()`, key `verifier`.
3. Where the invoice directory's saved layouts live and which URL saves one. Wrong module (it guessed the store
   and `search.py`). Fix: `presets()` / `save_preset()` in `ap_coder/erp/directory.py`, key `directory_presets`,
   their JSON shape and each `/search/presets` URL are now in the maps.
4. Where the JD Edwards Z-files are written and how their text is quoted. Fully right.
5. A new number setting on Settings → Review. Mostly right. Fix: the maps say settings table or `.env`, and name
   `review_context()`, `set_setting()` and the shared amount parser.
`scripts/check_map.py` also learned to accept a folder-qualified name such as `capture.luhn_ok()`.

## Cold-start round 2 (2026-10-17, enforce-draft; landed in stage 3, PR #26)
Questions: payment terms ("2% 10 net 30") and where the due date is used; an email attachment's path to the queue
and the limits against a malicious email; the files and tests for a new check code; where the place of supply is
decided and what must agree; where to look when the queue is slow with 5,000 invoices. All five were answered;
the transcript records no per-question verdict. The recurring gap: the 70 flat core modules had no module-level
"who uses this", and entry points (`terms.payment()`, `tax.place_of_supply()`, `mailbox.unpack()`) were missing.
Fix: `check_map.py --modules` writes `ap_coder/MODULES.md` (each module's job and importers; CI fails when stale),
and the core map got Flows lines (intake, place of supply, terms order, check codes), each checked in the code.

## Cold-start round 3 (2026-10-17, sweep2-draft; landed in stage 4, PR #27)
Questions: a BC invoice approved with no PST (where the place of supply is decided, its inputs, what reuses it); a
check code for changed bank details that must never be approved touchless or in bulk (files, lists, the help
test); an emailed .msg's steps to the queue and the size limits; what to check if `store.get_invoices()` changes;
why a JDE batch downloads differently from last week. Its answers held up against the code. Gaps, all fixed:
- two place-of-supply names: `supply_source()` gives (province, where it came from), `place_of_supply()` the province;
- a finding that always needs a person goes in `ALWAYS_A_PERSON` (`ap_coder/capture/supplier.py`), which
  `touchless_gates()` and bulk approval read;
- D-29 said "identical every time"; now: the same invoices and vouchers, with today's settings and master data;
- docs/CLAUDE.md gave the bug log's old range; the email folders (`emails/`, `emails/needs a person/`, `statements/`).
Question 4 still needed a code search for one method's callers: the map stays at module level on purpose.

## Accuracy passes
- **Pass 1** (2026-10-17, stage 2, PR #25): three agents checked every line of their maps (capture, bench,
  scripts, CI; web, root, tests, docs; core, data, all 31 decisions) and corrected about 15, 24 and 30 statements
  (about 70). No total of claims checked is recorded. They also caught `check_map.py --help` running the whole
  check, lint errors in the checker, and a company name in a guide; all fixed.
- **Sweep-2 map review** (2026-10-17, stage 4, PR #27): the 8 sweep-2 section reviews flagged map lines; one
  agent checked each against the current code and corrected 8 CLAUDE.md files, skipping about 11 made moot by
  sweep-2 fixes. Three wrong docstring sentences were fixed in a follow-up commit. No claim count is recorded.
- **Final independent pass** (2026-10-17, stage 4, PR #27): **366 claims** checked (19 CLAUDE.md files, 12
  MODULES.md rows, 12 decisions), **10 wrong**, none breaking code: the `doctor --online` wording; tax rounding
  (whole units only without cents); "also taxreturn.py" for the GST functions; missing settings keys; where
  `unpack()` lives; a missing flow step (AP's fixed rules, `rules.apply()`); what safe.py covers; the openpyxl
  readers' size guard; when browser tests skip; the data-folder order. Plus 4 missing items (capture's `types.py`
  shadows the standard library, the `few_sql_variables` fixture, the `slow` marker, CI installing `.[dev]` only).
  All fixed. One finding went past wording: the browser tests never ran in CI (B-124); a "Browser tests" job now
  runs them with `AP_REQUIRE_BROWSER=1`. Its prompt and B-124 call it the "second" pass; its commit says "pass 3".

## Final review of stage 4 (PR #27, merged 2026-10-19 UTC)
- Bug sweep 2: 8 fresh-context agents reviewed each section again and checked its CLAUDE.md against the code.
  docs/BUGS.md "Sweep 2": B-73 to B-128 fixed (a failing test first), N-32 to N-76 not reproduced or left, with
  the reason; each lesson in its folder's CLAUDE.md. B-127 and B-128 came from the PR's first CI run.
- Repeated lessons became whole-code checks (`tests/test_code_rules.py`); one found B-126.
- The final pass and round 3 above were the last check of the maps: 0 checker errors, one warning (core map at
  76 lines, resolved by stage 5's grouping).

## Cold-start round 4 (2026-10-19, after PR #30; the first 3-monthly round, run early)
A fresh agent read only the CLAUDE.md files, MODULES.md, decisions.md and BUGS.md; each answer was graded in the code.
1. A second approval refused on the office network: right on D-34 (a colleague's address stands for their login) and
   the routes; it could not name the function. It is `Store.final_approve()`: another name (any case) and another
   login than the first `approved` event's `detail.login`. Fix: a core map line.
2. A new plain-CSV ERP export: right (erp/exports.py `build()`, D-28 text as typed, the tests to copy); it could not
   find the list of formats. Fix: the erp map names `FORMATS` and the Exports page's labels.
3. Where the review gate stops a commit, and renaming the map-reviewer: right (the settings.json matcher must follow
   the `name:`). Fix: the scripts map names `_unreviewed()`, the comparison.
4. A required argument on `Store.events()`: guessed most callers and named the right tests, but missed
   ai/training_export.py and `final_approve()` itself. As in round 3, the maps stay at module level; the root map
   now says so and how to search (MODULES.md lists imports, not a store handed in, as intake/inbox.py gets it).
5. Whose GST number on a scan, and a bad check digit: right on capture (bill-to reading capped below verified,
   B-44; `GST_NUMBER_VALID`). Missing: coding's code. Fix: the coding map names `GST_NUMBER_CHECK_DIGIT` (with
   `_MISSING`, `_FORMAT`, all askable of the vendor) and taxreturn.py's claim at risk.
To keep the root map at 60 lines, two of its lines were shortened (the checker's list, the cold-start rule).

## Next checks
- A cold-start round every 3 months (root CLAUDE.md): add it here as round 4, 5... with its 5 questions, the verdicts
  and the fixes. The map-reviewer says when the last one is older than that.

## Not recoverable
- Round 1 and 2 question wording is summarised, not quoted. Round 2 has no per-question verdict; pass 1 and the
  sweep-2 review have no claim totals.
