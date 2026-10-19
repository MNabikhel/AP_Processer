# Sweep 3: web

#### B-190 · This computer's browser at its own network address was taken for a colleague · medium
- **Where:** `ap_coder/web/identity.py:126` (`Identity.__call__`, `is_this_computer`)
- **Symptom:** with `AP_DASHBOARD_ADDRESS=0.0.0.0`, the person on the computer running AP Coder opens
  `http://<this PC's IP>:8501`, is asked "Who are you?", gives another name and gives the second approval of an
  invoice they approved first on the same computer (D-34 says it must be refused).
- **Cause:** only a loopback client address counted as this computer; a browser on the server PC that opens its LAN
  address connects from that address, so it was "network <ip>", a login different from the OS user's. Also
  `is_this_computer("::ffff:127.0.0.1")` was False.
- **Reproduce:** approve on this computer, then a client at 192.0.2.2 to `http://192.0.2.2:8501` posts `/who`
  "S. Owner" and `/review/{id}/final-approve`.
- **Reproduced:** yes (`STATUS approved Server Owner S. Owner`, event `final_approved` by `network 192.0.2.2`)
- **Fix:** `identity.from_this_computer(scope)`: a client whose address is the one the connection arrived on
  (`scope["server"][0]`) is this computer; `is_this_computer` unwraps IPv4-mapped addresses. `Identity` and
  `pages/who.py` use it (`/shutdown` stays loopback only).
- **Test:** `tests/test_network_identity.py::test_this_computers_browser_at_its_own_network_address_cannot_give_the_second_approval`
- **Lesson:** Don't decide "this computer" from loopback alone: the same machine also reaches itself at its own network address.

#### B-191 · Two bank accounts ending in the same 4 digits were one row of the vendor's bank history · medium
- **Where:** `ap_coder/web/pages/vendors.py:138` (`vendor_detail`, `history`)
- **Symptom:** a vendor's invoices switch to another account at another bank that ends in the same 4 digits: the
  review screen warns VENDOR_BANK_CHANGED, but the vendor's "Bank account and GST/HST number history" shows one row
  (…4567, 2 invoices) and no "Changed" pill.
- **Cause:** the history grouped values by their masked form (`mask_account(a) == mask_account(b)`), not by the
  account (`same_bank_account`, which the check uses).
- **Reproduce:** approve an invoice paid into "Transit 00012, Institution 003, Account 5011234567", add one with
  "Transit 47210, Institution 815, Account 9907754567", open the vendor on /vendors.
- **Reproduced:** yes (`bank history rows: [('…4567', False, '2026-08-01', '2026-09-01', '2')]`)
- **Fix:** grouped with `same_bank_account` (details without any number still go together); rows that mask alike
  also show their bank and branch numbers (`coding/vendors.bank_branch()`, never the account's digits), or "another
  account number" when those are alike too.
- **Test:** `tests/test_web_masterdata.py::test_a_new_bank_account_ending_in_the_same_four_digits_shows_as_changed`
- **Lesson:** Don't group records by their masked display value: compare what they are, then mask for showing.

#### B-192 · Colleagues' unsaved edits were kept by name only · low-medium
- **Where:** `ap_coder/web/pages/invoice.py:93` (`_drafts`)
- **Symptom:** "Mike" on one computer and "mike" on another see each other's unsaved edits on the review screen; a
  colleague who gives a new name on /who loses the edits made under the old one, Start over can't drop them, and bulk
  approval skips that invoice ("it has edits open on its review screen") until AP Coder restarts.
- **Cause:** `person_drafts` was keyed by `name.casefold()` alone, and nothing moved the drafts when the cookie's name changed.
- **Reproduce:** colleagues at 10.0.0.5 "Mike" and 10.0.0.9 "mike"; A posts `/review/{id}/check` with a PO; B opens
  `/review/{id}`. Then "Ana" edits, renames to "Ana Silva" on /who, presses Start over, and this computer bulk-approves.
- **Reproduced:** yes (`Mike B's screen shows Mike A's unsaved edit: True`, `Ana Silva sees her edit: False`,
  `after Start over, draft still held: True`, bulk approval skipped it)
- **Fix:** drafts keyed by (name, case aside, and the colleague's address); `who_save` moves the asker's drafts to
  their new name (`invoice.rename_drafts()`).
- **Test:** `tests/test_network_identity.py::test_two_colleagues_who_give_one_name_on_two_computers_keep_their_own_edits`,
  `tests/test_network_identity.py::test_a_colleague_who_changes_their_name_keeps_their_edits_and_can_start_over`
- **Lesson:** Don't key a person's state by the name they typed alone: a name is neither unique nor fixed.

#### B-193 · A link from another site to the dashboard asked a colleague "Who are you?" again · low
- **Where:** `ap_coder/web/pages/who.py:36` (`who_save`)
- **Symptom:** a colleague who gave their name follows a link to AP Coder from another site (the intranet, a webmail)
  and is asked "Who are you?" again; typing the address works.
- **Cause:** the signed name cookie was `SameSite=Strict`, which the browser leaves out of a navigation started on
  another site.
- **Reproduce:** in Chromium, give a name on `http://<server>:<port>/who`, then click a link to `/review` on a page
  served from another origin.
- **Reproduced:** yes (`link from another site: .../who?next=%2Freview True False`)
- **Fix:** `SameSite=Lax`: the cookie comes with a top-level GET from a link (a GET never changes anything); a POST
  from another site is still refused by LocalOnly (header `X-AP-Coder`, Origin, Sec-Fetch-Site).
- **Test:** `tests/test_network_identity.py::test_a_link_from_another_site_keeps_who_the_colleague_is` (the cookie's
  attributes, and a cross-site POST still 403; the sweep's browser repro used the server's own address, which B-190
  now counts as this computer)
- **Lesson:** Don't make a login cookie Strict when the CSRF defence is already elsewhere: it only breaks incoming links.
