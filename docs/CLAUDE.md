# docs: guides, design notes, reviews, the bug log and the decisions

The written documentation: guides for the pilot tester and IT, design notes for developers, the code reviews, the bug
log and the list of decisions. No code lives here. The README at the root is the overview; each folder's CLAUDE.md
is the map of its code. When a doc and the code disagree, the code wins and the doc is fixed.

**Depends on:** none
**Used by:** `scripts` (check_map.py reads BUGS.md), `ap_coder/web/pages` (help.py shows WHATS_NEW.md on the Help page), `tests` (test_how_it_runs_doc.py checks HOW_IT_RUNS.md, test_jde.py and test_jde_readiness.py read JDE_E1.md), `.github/workflows` (comments point to UPGRADING.md), README links

## Interface
- `decisions.md`: D-01 to D-32, one line per significant choice, its reason and its source file. Add the next number.
- `BUGS.md`: every bug a sweep found: B-NN (where, symptom, cause, repro, fix, test, lesson) and N-NN (not reproduced, left).
- `HOW_IT_RUNS.md` cites code as `file:function`; `tests/test_how_it_runs_doc.py` fails when a cited name no longer exists.
- `WHATS_NEW.md` is rendered in the app (Help page): keep its Markdown to what `web/minimd.py` renders.

## Gotchas and decisions
- Each bug gets a failing test first, then the fix, and a B-NN entry in `BUGS.md` with its lesson; the lesson is also
  a line in the `### Bug lessons` list of the folder of the first path on its **Where:** line. check_map.py reads
  `BUGS.md` (`#### B-NN` headings, the **Where:** line): keep that format. A suspicion not reproduced is an N-NN line, never fixed blindly.
- A significant choice (what you would have to explain to the next developer) gets a D-NN line in `decisions.md`.
- `JDE_E1.md`'s open questions are the rows of the JDE worksheet (`ap_coder/jde_worksheet.py`); test_jde_readiness.py
  checks that they match. B-26 also changed `JDE_E1.md` (Z-files hold text as it is).
- User-facing wording: "pilot tester", never another title for that person; plain English; no company data.
- `screenshots/` is a data folder (no CLAUDE.md): the PNGs the README shows.

## Files
- Decisions and bugs: `decisions.md` (D-NN: each significant choice and why), `BUGS.md` (the bug log, sweep 1: B-01 to B-72, N-01 to N-31).
- For the pilot: `GETTING_STARTED.md` (running the prototype on your data, step by step), `PILOT.md` (pilot guide on one laptop), `PILOT_PLAN.md` (four weeks to a decision).
- For IT: `HOW_IT_RUNS.md` (double-click to dashboard: Python, packages, update, files written, every network place), `UPGRADING.md` (each older version and what the update does), `LM_STUDIO.md` (the models, limits shared with CloseDesk), `MAILBOX_SETUP.md` (getting emails into the invoices folder).
- For finance and AP: `SALES_TAX.md` (place of supply, taxes per line, the math, rates), `JDE_E1.md` (JD Edwards E1 Z-file export and open questions), `CODING.md` (GL coding without an AI model).
- Design and reviews: `CAPTURE_DESIGN.md` (invoice capture, readers, confidence, touchless), `CODE_REVIEW.md` (review of the core engine), `CODE_REVIEW_WEB_AND_LAUNCHER.md` (review of the dashboard and the launcher).
- `WHATS_NEW.md`: release notes, newest first (also shown on the Help page).
- `screenshots/`: dashboard screenshots used by the README (data folder).
