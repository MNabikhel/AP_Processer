# To do

The owner's list of what is still to do on AP Coder, newest last. Remove an item once it is done (its bug or
decision number then says what was done).

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
- **The vendor an invoice is filed under, asked one way everywhere (after Monday: a bug fix).** Two suppliers whose
  names read the same are two vendors, the second kept as "<name> #<supplier number>". Processing looks the payment
  terms up under the vendor the invoice is filed under (`Store.vendor_key_for`, `ai/pipeline.py`); the review screen
  (`web/invoice_view.py`, `web/pages/invoice.py`) and the queue's due note (`web/pages/review.py`) under the name's
  key (`memory.vendor_key`). Measured with the made-up company's SILVERTIP SOFTWARE INC. (supplier 35436, Net 30)
  and a twin SILVERTIP SOFTWARE LTD. (35437, Net 60): for an invoice from the Ltd., `vendor_key` gives "silvertip
  software" and Net 30, `vendor_key_for` with its GST/HST number "silvertip software #35437" and Net 60. Seven
  per-vendor lookups in `web/` and `coding/` use the name's key (terms, open POs, `get_vendor`). Start with a failing
  test that the screen, the queue, processing and bulk approval agree on terms, due date, open POs and Pay from for
  two vendors of one name and for a name AP corrected (D-119); then send those lookups through `vendor_key_for`
  (not every `vendor_key` call: matching names rightly uses a name's key).
