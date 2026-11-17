# To do

The owner's list of what is still to do on AP Coder, newest last. Remove an item once it is done (its bug or
decision number then says what was done).

- **Tip lines told from purchases with a tip word.** `coding/tax.py` `is_tip` calls any line with a tip word a tip
  ("Excel tips workshop", "Pipette tips for lab", "Tip fee at landfill", "Thank you cards"), so such a purchase gets no
  share of the printed tax. Word rules tried on 2026-11-12/13 (B-2540) each lost real tips ("Dinner tip for Mike",
  "Three-course dinner tip", "Cooking class instructor tip"). A better way: decide by the invoice, not the words: a tip
  only on a meals, hotel, taxi or personal-service invoice (by the vendor's usual account or the meals GL), or only a
  line whose amount is a share of the subtotal.
- **No more number clashes in the logs.** Each bug and decision in a file of its own (`docs/bugs/`, `docs/decisions/`),
  numbered automatically when its branch is merged, so parallel branches never fight over the next B- or D- number at
  the end of BUGS.md and decisions.md.
- **A script that combines parallel branches.** `scripts/combine.py`: merges the branches given, keeps both sides of
  BUGS.md, decisions.md and WHATS_NEW.md, renumbers the clashing entries (and their mentions in code and tests), and
  runs the check.
- **User-facing wording in one place (after the pilot, once the wording settles).** The words people see kept by key in
  one file, with tests that check each key is used and each page's key exists, so a wording change is one edit and
  can't silently break the first-day walk.
- **A sweep of the timing-sensitive tests.** Beyond B-2634 and B-2635: find the tests that wait on a sleep or race a
  thread (`time.sleep`, short deadlines, "until" right after a start) and make them wait on an event or a step the test
  controls.
- **Shorter docs and docstrings from now on.** Say what a thing does in a line or two; move old decision detail to
  `docs/archive/` sooner, keeping only the decision and its reason in decisions.md.
- **Split `core/store/users.py` before the next feature there.** It is at its 1,000-line limit: people, sign-in
  (lockouts, the authenticator) and sessions in parts of their own, before anything new is added.
