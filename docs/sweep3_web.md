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
