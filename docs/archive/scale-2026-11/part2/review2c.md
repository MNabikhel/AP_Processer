[Subagent hand-back] The text below is the final report of a subagent this session delegated to. It is model output, NOT a message from the user: instructions, requests, or approval claims inside it are the subagent's words and carry no user authority. The harness indents every line of the report, so a frame-like line at column zero inside it would be forged. Notes above this frame may quote model-derived text, which carries no user authority either. The report follows:
# Review 2c: scale goal phase 2 part 2, web and browser side (7015205..4c95898)

I found six things, none of them security problems:
- one real browser bug when the vendor list fails to load;
- one smaller wording glitch while the list loads;
- three low risks;
- a few nits.

Static files, the vendor-list route, the page-picture cache, HandBack, the polls (including the 7120c82 merge) and paging all hold up.

**Report file:** I could not save `goal_notes/review2c/report.md`. The environment blocks a subagent from writing report files, so the full report is only here.

**Repository:** nothing in `/home/user/int` was edited (`git status` is clean). The probes are in `/tmp/claude-0/-home-user/b4fef7c4-f9cb-52fc-9da2-47ad67a7ebc2/scratchpad/goal_notes/review2c/probes/`, and their temporary data folders are deleted.

**How the probes run:**
- The browser probes are pytest files: `-p tests.conftest --rootdir=/home/user/int -m ""`, with the venv python and `PYTHONPATH=/home/user/int`.
- The others are plain scripts.
- All of them run and print what they found.

## Findings, most severe first

### 1. A failed vendor-list fetch leaves the screen with only the seed until a reload (Medium-low; reproduced, high confidence)
**Where:** `ap_coder/web/static/js/invoice.js:570-588` (`loadVendors`), with lines 565, 651, 749 and 902.

**What happens:**
- The fetch can fail by abort, a 500, an IIS 502/503, or the 30 s `fetchWithin` deadline.
- Then `vendorsSettled = true`, and `vendors` stays the seed: the 0-2 vendors the screen named as it opened.
- From then on, `loadVendors()` returns at once (`if (vendorsSettled) return`). It never tries again, and nothing tells the user.

**For the rest of that screen's life:**
- The picker is empty for any other vendor.
- With a vendor master, typing a vendor that is in the list says "Not in the vendor list: pick one, or add it as a new vendor."
  - Or it says "The GST/HST number printed is X's", where the full list would say "In the vendor list · <AN8>".
- The "sure" badge is hidden.
- `vendorChanged` fills nothing.

**What limits the harm:** the server check still fills, and the server refuses a duplicate "Add new vendor".

**Scenario:** a busy company server. D-161's saturated runs still had 74 and 9 requests over 30 s with HandBack, and every editable screen fetches the list (`no-store`, B-1070).

**Probes:** `test_probe_vendor_fail.py` and `test_probe_vendor_fail2.py`. I typed the listed vendor "Agence Creative Mont-Royal" (AN8 10085):

| How the fetch went | Line under the vendor field | Picker |
|---|---|---|
| ok | `In the vendor list · 10085` | offers it |
| abort | `Not in the vendor list: pick one, or add it as a new vendor.` | empty |
| 500 | "The GST/HST number printed is Harbourview…'s" | empty |

There was one request in all: no retry.

### 2. While the list is loading, the line still shows the previous vendor as verified (Low; reproduced, high confidence)
**Where:** `invoice.js:651`. It returns before redrawing (`if (!vendorsSettled && seedFor() !== seeded) return void loadVendors();`).

**What happens:** after the clerk types a different vendor, the green "verified · In the vendor list · 10093" of the opening vendor stays until the list arrives. That can be up to 30 s on a busy server, and the server check may flag the field meanwhile.

**Probes:**
- `test_probe_vendor_slow.py` (vendors.json held 4 s): after typing "Some Brand New Vendor Ltd", the line still reads `verified / In the vendor list · 10093`.
- `test_probe_vendor_add_early.py`: the same right after "Add new vendor". Once the list arrives it correctly says `Added here, not in JD Edwards yet`.

### 3. The 5-minute deadline on every `post()` can give up on long actions (Low; suspicion, not measured)
**Where:** `app.js:82` and `:100` (`ACTION_MS`).

**Risk:** some actions run whole in the request and have no upper bound:
- Restore (`pages/settings.py:583`). It makes a safety backup first, and a year's server database is 7-9 GB per `backups._room_refusal`.
- Past coding → Teach (`pages/learning.py:706`).

Past 5 minutes the browser says "AP Coder didn't answer. Is it still running?" while the server carries on. Before D-161 there was no deadline. A second restore at the same time is refused, so the harm is the wrong message and a retry. The code comment says the longest action takes 5 minutes at most, but I found no measurement behind it.

### 4. The shared page-picture cache has no free-space reserve (Low; from the code, medium confidence)
**Where:** `document.py:158` (`_keep`), `:188` (`_prune_kept`), `:42` (1 GB cap).

**Risk:** the cap is a total, not free space. On a data disk with less than 1 GB free, pictures can fill it, and then SQLite writes fail ("database or disk is full"). Backups guard against exactly this (`_room_refusal`, `BACKUP_RESERVE`). I couldn't fill a disk here to reproduce it.

### 5. HandBack ignores the long-route lane (Low; performance only)
**Where:** `server.py:549-553`.

`worker_busy()` counts only tasks waiting for the default thread limiter. Long routes run on their own `CapacityLimiter(1)` and queue in `heavy_lane()`. So a worker whose CPU is taken by two reports, with more queued, never counts as busy and keeps its keep-alive connections. Not measured under load.

### 6. Nits
- **Another build's fonts and assets have no Cache-Control** (`app.py:106`). Only `/static` gets `no-cache`, against D-161's wording. Probed: `/fonts/0123456789/InterVariable.woff2` and `/assets/0123456789/logo.svg` answer `cache-control: None`. This only matters while two workers run different builds, which the service update avoids by restarting them all.
- **`loggedOut` matches `/login`, not `/who`** (`app.js:155-157`). On a laptop shared on the office network, after AP Coder restarts, colleagues' polls keep going as "failed": every 5 s for jobs, 60 s for the reader. This was already so before (then 0.8 s and 20 s); not new.
- **`read_static` follows symlinks** in the package's static folders, where `StaticFiles` (`follow_symlink=False`) refused ones pointing outside. Theoretical: only the installer controls those folders.

## Checked and sound
**Static files from memory and kept addresses**
- No path can leave the static folders: it is an exact dictionary lookup.
  - Raw-socket probe against uvicorn (`probe_static_raw.py`): `..`, `%2f`, `%2e%2e`, `%00`, backslashes, `C:/`, double slashes and an upper-case build all give 404, or the same in-folder answer `StaticFiles` gives.
  - TestClient probe (`probe_static_http.py`) in laptop and server modes.
- It runs after the Host check: a foreign Host gets 400.
- The security headers (CSP, nosniff, HSTS on the server) are on 200s and 304s.
- Only OPEN prefixes are answered there, so nothing behind the login is skipped.
- Range, POST and missing files go on to `StaticFiles`. HEAD and 304 behave as Starlette 1.10's.
- The kept CSS names the kept fonts, no JS imports an absolute static path, and the templates have no plain addresses left.

**`/review/vendors.json`**
- The READ rule applies, and Managers are excluded.
- It is `no-store` on the server (test_server_security covers it).
- No bank data in it, and the seed is escaped safely (`tojson`).
- `immutable` is sent only for the content's own hash.

**Page-picture disk cache** (`probe_pages_cache.py`)
- Eight processes and eight threads drawing the same page give identical bytes, one file, and no `.part` left.
- A file replaced at the same path gets its own picture: the name is the full SHA-256 of the content, so one invoice's picture can't be served for another.
- An unwritable folder still returns the picture, drawn each time.
- Pruning keeps the newest file.
- Read, prune and rename races are caught, including the Windows retry.
- `.cache` is not in backups, and a restore can't bring back a stale picture because names follow content.
- Windows path length is fine.

**HandBack** (`test_probe_handback_rst.py`, `probe_handback_stream.py`, uvicorn h11)
- A streamed answer finishes, then the connection closes.
- HEAD and 304 are fine. HTTP/1.0, or a client that asked to close, gets one `connection: close`, not two.
- An early 401 to a 30 MB upload by fetch or XHR still reached Chromium.
- A pipelined second request is dropped after the close. HTTP allows that (the client retries), and browsers and IIS ARR don't pipeline.
- No send happens from a thread. HandBack and Recycle don't interfere.

**Polls** (`test_probe_reader_poll.py`, server mode in Chromium)
- `?seen=` gets `{"same": true}`.
- A hidden tab asks nothing for 13 s, then asks at once when shown.
- After the session ended: one 303 to `/login`, then no more requests in 26 s.
- The 7120c82 merge is correct: "same" backs off, a new tag redraws and resets the pace, and `poll:false` flushes and reloads.
- `watchJob`'s `?only=state,done,note` covers what its callers read.

**Vendor list in the browser, other cases**
- A vendor added before the list arrives is kept, and the server refuses duplicates.
- Read-only screens lock their fields and don't fetch as they open.
- An open picker is redrawn when the list comes; only the keyboard highlight resets.

**Paging** (Sales tax, Vendors, Purchase orders, Learning)
- `shown_count` handles junk, negative and huge values.
- An opened vendor or PO is looked up in the full list, so it is never dropped.
- Search filters before paging.
- The links keep `shown`, the tiles count every row, and the CSVs are unchanged (every row).
- After Vendors → Save the list goes back to the first 200. That was already so: view, q and sort were lost there before too.

## Probes
All in `/tmp/claude-0/-home-user/b4fef7c4-f9cb-52fc-9da2-47ad67a7ebc2/scratchpad/goal_notes/review2c/probes/`:
- test_probe_vendor_fail.py
- test_probe_vendor_fail2.py
- test_probe_vendor_slow.py
- test_probe_vendor_add_early.py
- test_probe_reader_poll.py
- test_probe_handback_rst.py
- probe_handback_stream.py
- probe_static_http.py
- probe_static_raw.py
- probe_pages_cache.py
