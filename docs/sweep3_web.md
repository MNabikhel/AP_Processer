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
