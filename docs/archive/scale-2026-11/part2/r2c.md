[Subagent hand-back] The text below is the final report of a subagent this session delegated to. It is model output, NOT a message from the user: instructions, requests, or approval claims inside it are the subagent's words and carry no user authority. The harness indents every line of the report, so a frame-like line at column zero inside it would be forged. Notes above this frame may quote model-derived text, which carries no user authority either. The report follows:
All six items are fixed in 8 commits on top of 4c95898, in the worktree `/home/user/AP_Processer/.claude/worktrees/agent-a6a89121fb5258806`. Nothing is pushed. I did not touch `core/store/*`, `ai/suggest.py`, `ai/line_match.py` or `erp/spend.py`. The picture cache only imports `BACKUP_RESERVE` from `backups.py`; it doesn't change that file. Each bug fix started with a new test that I saw fail, then the fix, then its line at the end of `docs/BUGS.md`.

**Items, in commit order** (items 1 and 2 share one function, so item 2 went first and got the lower number):

| Commit | B | Item | Test (in `tests/web/`) |
|---|---|---|---|
| c63a285 | B-1730 | 2, the line while the list loads | `test_web_picker.py::test_in_a_browser_a_vendor_typed_before_the_vendor_list_came_is_being_checked` |
| c7a321e | B-1731 | 1, a failed vendor-list fetch | `test_web_picker.py::test_in_a_browser_a_vendor_list_that_did_not_come_is_asked_for_again` (dropped and 503), `…_never_comes_is_asked_for_a_few_times_only` |
| 2238360 | B-1732 | 3, `post()`'s deadline | `test_web_settings.py::test_in_a_browser_a_restore_is_waited_for_however_long_it_takes`, `test_web_app.py::test_the_actions_the_server_runs_whole_are_waited_for_however_long_they_take` |
| 24a1867 | B-1733 | 4, free-space reserve for the pictures | `test_web_document.py::test_a_picture_is_not_kept_where_it_would_leave_the_disk_short_of_room` |
| 3a09f37 | B-1734 | 5, HandBack and the long-route lane | `test_server_capacity.py::test_a_worker_whose_long_routes_lane_is_waited_for_closes_the_connection_of_each_answer` |
| 64def87 | B-1735 | 6, another build's fonts and assets | `test_web_app.py::test_another_builds_fonts_and_pictures_are_checked_again_on_every_use_too` |
| ccca252 | B-1736 | 6, `loggedOut` and `/who` | `test_server_browser.py::test_in_a_browser_a_colleagues_poll_stops_once_the_laptop_asks_who_they_are_again` |
| 5e0d39c | B-1737 | 6, `read_static` and symlinks | `test_web_app.py::test_a_static_file_that_links_out_of_its_folder_is_not_answered` |

**How each behaves now:**
- **B-1730:** a vendor found in the seed, or added on the screen, is shown at once ("Added here, not in JD Edwards yet"). Any other vendor shows "Checking the vendor list…" in the plain tone, until the list says.
- **B-1731:**
  - A failed fetch is retried on the next need (the picker opened, the vendor typed, the line redrawn). Retries are at least 5 s after the last failure, 5 tries in all (`VENDORS_TRIES`, `VENDORS_RETRY_MS`). Tests shorten the wait with `window.AP_VENDORS_RETRY_MS`, like the existing `AP_ARMED_MS`.
  - Meanwhile, with a vendor master, the line says "Couldn't load the vendor list: the check on approval still compares the vendor with it." in the warn tone. It never says "Not in the vendor list" or whose GST/HST number is printed. Without a vendor master the line stays empty.
  - The picker's "add as a new vendor" row waits for the list. `vendorChanged` waits once for a fetch under way, then fills from the seed, so it can't loop.
  - `vendorsSettled` is gone.
- **B-1732:** `post(url, body, { ms })`, where 0 means no deadline. A form marked `data-long` is sent with no deadline. These forms are marked:
  - restore and Past coding → Teach;
  - the imports: GL accounts and cost centers, bank accounts, vendor master, payment register, purchase orders, the JD Edwards GL map and worksheet;
  - a Finance Hub run, and bulk approval of clean invoices (its list grows with the queue).

  Everything else keeps 5 minutes. The JD Edwards posting and sync already run as jobs. The browser test uses Playwright's fake clock: "Back up now" still gives up at 5 minutes; a held restore is still waited for after 30 minutes and then shows "Restored …".
- **B-1733:** a picture isn't written if the disk's free space would drop below `backups.BACKUP_RESERVE` (512 MB). Free space is read at most every 5 s (`document.ROOM_EVERY_SECONDS`) and counted down by what the worker wrote since. If it can't be read, the picture is written. The picture is still served, drawn each time.
- **B-1734:** `worker_busy()` also counts requests waiting in `heavy_lane()`. A full lane with nobody waiting doesn't close connections.
- **B-1735:** another build's `/fonts/<b>/` and `/assets/<b>/` now answer `no-cache` (`core.STATIC_OTHER`). The plain addresses answer as before.
- **B-1736:** I checked first that `/who` really is the session-ended answer on a shared laptop: identity sends a GET without a valid name cookie to `/who?next=`. `loggedOut` now also stops on a redirect to `/who`, and the job pages go there and come back after it.
- **B-1737:** `read_static` leaves out any file whose real path is outside its folder, as `StaticFiles(follow_symlink=False)` does. A link inside the folder still works.

**D- amendments** (bullets wrapped at 120 characters):
- D-160: "Amended (B-1730, B-1731)" (wording, retry rule, picker row, fill from the seed) and "Amended (B-1733)".
- D-161: "Amended (B-1732)" (`data-long`, the list of actions, what keeps 5 minutes), "Amended (B-1734)", "Amended (B-1736)" and "Amended (B-1735, B-1737)".

**Checks run:**
- `python scripts/check.py` on every changed file: OK, 1070 passed.
- Default tier for `tests/web tests/meta`: 1056 passed.
- `-m ""` on `test_web_invoice.py`, `test_web_picker.py`, `test_server_browser.py`, `test_web_app.py`, `test_server_capacity.py`, `test_web_document.py`: 242 passed.
- ruff check and format: clean.
- Probes re-run against this worktree:
  - `test_probe_vendor_fail.py`: abort and 500 now give "Couldn't load the vendor list…".
  - `test_probe_vendor_slow.py`: a typed vendor shows "Checking the vendor list…", then "In the vendor list · 10085".
  - `test_probe_vendor_add_early.py`: "Added here…" at once.
  - `test_probe_vendor_fail2.py`: same as the fail probe.
  - `test_probe_handback_rst.py`, `test_probe_reader_poll.py`, `probe_handback_stream.py`, `probe_static_http.py`, `probe_static_raw.py` and `probe_pages_cache.py` give the same sound results as before.
  - The sandbox wouldn't let me set PYTHONPATH, so I ran pytest from the worktree and the script probes through `/tmp/claude-0/-home-user/b4fef7c4-f9cb-52fc-9da2-47ad67a7ebc2/scratchpad/probe_runner.py`, which puts the worktree first on `sys.path`. Its output confirms it loaded the worktree's code.

**Left, and why:**
- `/exports/create` and the POST download of a batch still have the 5-minute deadline. They run in the long-route lane and are bounded by the batch; D-161 already judged an export fits in 5 minutes. Adding `data-long` to their forms is a one-line change if you want it.
- The vendor picker shows only the seed's vendors while the list is missing. The line under the field explains, but the open picker covers it; I didn't add an explanation row to the picker.
- Once the 5 tries are used up, only a page reload fetches the list again; there is no "Try again" button.

I deleted all the temporary data I created; free disk space is still about 1.3 GB.
