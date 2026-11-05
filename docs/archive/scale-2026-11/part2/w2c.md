[Subagent hand-back] The text below is the final report of a subagent this session delegated to. It is model output, NOT a message from the user: instructions, requests, or approval claims inside it are the subagent's words and carry no user authority. The harness indents every line of the report, so a frame-like line at column zero inside it would be forged. Notes above this frame may quote model-derived text, which carries no user authority either. The report follows:
## W2c report: static files, compression, polls, worker balance (D-161; B-1720)

All five items are done, in 4 commits on top of `52e2031` (scale-int at the time I reset). Nothing is pushed.

`scale-int` has moved since (W2d merged, `8194441`). `git merge-tree` of my HEAD with it shows no conflicts, and the merged tree has no plain static link and no `static_version` left.

| commit | item |
|---|---|
| `dfe29f8` | 1 and 2 (code): static files from memory, kept addresses, `core.static_url` |
| `8437f34` | 4: polls, fetch deadlines, B-1720 |
| `3c7946d` | 5: `server.HandBack`, `bench load --keep-alive`, docs (HOW_IT_RUNS.md; SERVER.md for item 3) |
| `6a1e1a2` | 2 (templates): the one mechanical commit, kept last |

All load numbers are from my 3,000-invoice seed, now deleted. Two configurations:
- **"sat"**: 60 people, think 4 s, 2 workers, 180 s.
- **"light"**: 50 people, think 10 s, 4 workers, 180 s.

The 4 cores were shared with other agents (load average up to 13 at times), so compare only runs made back to back. Run reports and probe scripts are in `/tmp/claude-0/-home-user/b4fef7c4-f9cb-52fc-9da2-47ad67a7ebc2/scratchpad/goal_notes/w2c/` (`compare.py` prints any set of runs side by side).

### Items 1 and 2: static files from memory, at kept addresses
**What changed:**
- `core.read_static` reads the styles, scripts, fonts and logo once at start.
- `app.StaticFromMemory` answers a GET or HEAD of one on the event loop. It sits after the Host check, with the security headers and the access log, and before the session, the errors page and the backup check. No thread, no session.
- Headers, ETag, Last-Modified and 304s are the same as `StaticFiles`. A test compares every file in laptop and server mode for GET, HEAD, Range, If-None-Match (weak and lists), If-Modified-Since, POST and 404.
- What memory doesn't answer (a Range, a POST, a missing file) still goes to `StaticFiles`.

**Kept addresses:**
- `core.static_url('/static/app.css')` gives `/static/<build>/app.css`, sent with `public, max-age=31536000, immutable`. The kept stylesheet names the fonts at `/fonts/<build>/`.
- **I used a hash of the files' contents as the build, not the commit.** In a checkout the commit doesn't change with an uncommitted edit, so an edited script would be cached for a year. An update that changes no static file keeps everyone's cached copies.
- An address with another build in it (a page drawn before an update) gets the current file with `no-cache`. Plain addresses behave as before. HTML stays `no-store`.
- A test fails on any template that links a plain address: a page script loaded at a plain address would be a second app.js module.

**Measured (sat):**

| | before | memory only | with kept addresses |
|---|---:|---:|---:|
| requests | 22,834 | 26,344 | 6,045 (+27,506 from browser caches) |
| /static p95 (server p95) | 1.82 s (1.67 s) | 130 ms (4 ms) | 67 ms |
| /fonts, /assets p95 | 1.79 s, 1.96 s | 61 ms, 106 ms | 25 ms, 52 ms |
| pages p50 / p95 | 3.25 s / 49.4 s | 466 ms / 6.10 s | 269 ms / 1.67 s |
| actions p95; over 30 s | 50.8 s; 745 | 12.3 s; 0 | 2.05 s; 0 |

Server CPU per request through real uvicorn:

| request | before | after |
|---|---:|---:|
| 304 | 0.90 ms | 0.46 ms |
| app.css (49 KB) | 1.48 ms | 0.41 ms |
| app.js | 1.59 ms | 0.35 ms |
| 373 KB icon font | 2.72 ms | 0.52 ms |

**Left:** a file edited while `serve` runs is served as it was at start; a developer restarts (written in HOW_IT_RUNS.md).

### Item 3: compression, measured and left off
No code; the reasoning is in SERVER.md and D-161.

gzip CPU per MB of HTML/JSON on the 100k database (GETs only):

| level | ms per MB |
|---:|---:|
| 1 | 4.4–6.6 |
| 4 | 7.3–11.7 |
| 6 | 14–18 |
| 9 (Starlette's default) | 60–75 |

- **Invoice page** (3.6 MB, 565–630 ms of route CPU): level 1 costs 16–24 ms and gives 0.53 MB. Level 9 costs 241–271 ms.
- **Queue** (85 KB): 0.5 ms at level 1, to 9 KB.

On a gigabit LAN, 3.6 MB takes about 29 ms on the wire, which costs the server nothing. Level 1 would cost 4–5 core-s a minute at 100 people (about 3% of the 4-core budget) and save no time anyone sees. So compression is off. People on a slow link (VPN) can have IIS in front compress `text/html` and `application/json` at its lowest level. Streams and files are never compressed. Worth revisiting only if the vendor list leaves the invoice page.

### Item 4: polls (B-1720)
- **One loop for all polls:** `app.js` `poll()`, used by `pollJob` (import, Spend workbook, self-test) and by the review screen's reading poll.
  - Nothing is sent while the tab is hidden; it looks at once when the tab comes back.
  - The wait grows ×1.5 while the answer stays the same (or doesn't come), up to 3× the base interval (at least 5 s for a job). A change goes back to the base interval.
  - A 401, or a GET redirected to /login, stops it. The job pages then reload into the login and come back after it.
- **B-1720:** after the session ended, a job page kept polling every 0.8 s, and the server drew the login page each time.
- **Polls ask for less:** `watchJob` asks `/jobs/<name>?only=done,note`.
- **Fetch deadlines** (`fetchWithin`, AbortController): 30 s for a look; 5 min for `post()` and the help page's setup report. On timeout the existing "didn't answer" wording shows.
- **Tests:** a browser test for B-1720 and one for the hidden-tab pause and backoff (both fail on the old app.js), and a test for `only=`.

**Merge note for W2b:** I made three small edits to `invoice.js`: an extra import line (line 25), the `loadWords` fetch (about line 929), and the reading-poll block (about lines 1288–1315).

**Left:**
- `/process/status` still sends all of the job's lines each poll (up to 500, about 57 KB), because process.js shows them all (`web/pages/process.py:1023`).
- The upload XHR has no deadline (`process.js:96`); an upload can take minutes.

### Item 5: balance between workers
**Finding:** the workers share one socket, and the first free event loop takes each new connection. That is true on Linux (epoll) and on Windows (uvicorn's supervisor shares the socket and each worker runs the selector loop; see D-161). What keeps people on one worker is the connection staying open. The harness hid this: its people's httpx connections close after 5 s unused. I added `bench load --keep-alive S` (default stays 5, so earlier baselines stay comparable); 300 behaves like a browser.

**Measured with browser-like people (`--keep-alive 300`):**
- **Light:** one worker did 54% of the work (0.32 cores against 0.07–0.11 for the others).
- **Sat, today's 75 s against HandBack, three pairs run back to back:**

| | 75 s keep-alive | HandBack |
|---|---|---|
| pages p95 | 5.2 / 24.0 / 33.3 s | 4.1 / 19.6 / 16.6 s |
| actions p95 | 10.8 / 35.6 / 45.0 s | 7.3 / 26.1 / 22.5 s |
| requests over 30 s | 0 / 147 / 343 | 0 / 74 / 9 |

- **5 s server keep-alive:** 12 requests got no answer in sat and 1 in light (B-1295's race again), with pages p95 9.9 s. Refused.

**Choice:** keep the 75 s keep-alive and add `server.HandBack`. While requests wait for one of a worker's threads, its answers carry `Connection: close`, so the browser's next request opens a new connection that a free worker takes. Nothing is lost because the close is announced in the answer. A new HTTPS connection costs the worker that takes it 1.2 ms (0.4 ms resumed).

With the harness's default people (5 s), two pairs showed no difference beyond the noise (pages p95 1.42 / 1.19 s without HandBack, 1.16 / 1.66 s with). Its tests use asyncio directly and real uvicorn over a raw socket.

### Checks
- `python scripts/check.py` on the template commit: OK (464 passed).
- Default `tests/web tests/meta tests/offline tests/bench tests/launcher`: 1433 passed, 1 skipped.
- `-m ""` on every browser test file plus `tests/offline`, `test_web_finance` and `test_web_app`: 641 passed, 1 skipped.
- `-m ""` on `test_server_capacity` and `test_server_scale` (real multi-worker servers): 66 passed.
- ruff and format clean on all changed files.
- `same_behaviour.py` not run: the addresses and headers change by design; the file-by-file header comparison test covers the "nothing else changed" part.
- Seed and code copies deleted.

### For later phases
- **100k re-measure:** use `--keep-alive 300` (`bench/load.py:867`); the default 5 s makes results look better than real browsers get.
- **Expired-session polls** (`web/identity.py:332`): a GET poll without a session still makes the server draw the whole login page once before the script stops. The poll routes could answer 401 JSON instead.
- **Access log** (`web/security.py:376`): it still writes each line synchronously on the event loop.
- **W2b merge:** if a new `<script>`/`<link>` arrives with `?v={{ static_version }}`, the template test fails; convert it to `{{ static_url('/static/...') }}`. New fetches or polls in invoice.js should use `fetchWithin` / `poll`.

### D-161 text
- **D-161** · **Static files from memory at kept addresses, no compression, polls that wait, and a busy web worker that hands its connections back** (B-1720; the phase 2 load run's 16-57 s static waits and its 0.34-1.0 cores workers).
  - **Static files from memory:** `core.read_static` reads the styles, scripts, fonts and logo once when AP Coder starts. `app.StaticFromMemory` answers a GET or HEAD of one on the event loop: after the Host check, with the security headers and the access log, before the session, the errors page and the backup check.
    - The same status, headers and 304s as Starlette's `StaticFiles` (its own ETag and Last-Modified, so a browser's copy stays current across the change), laptop and server; a test compares every file, both modes.
    - `StaticFiles` still answers what memory doesn't: a Range, a POST, a file that isn't there.
    - Why: a static file costs 1-2.7 ms, but `StaticFiles` reads it on one of the worker's 4 threads (D-153), behind 1-2 s pages. Server CPU through uvicorn: a 304 0.90 → 0.46 ms, app.css 1.48 → 0.41 ms, the 373 KB icon font 2.72 → 0.52 ms; no thread.
    - A file edited while AP Coder runs is answered as it was at the start (a developer restarts `serve`).
  - **Kept addresses:** the pages link each file at `/static/<build>/...` (`/fonts/<build>/...`, `/assets/<build>/...`) through `core.static_url`, with `Cache-Control: public, max-age=31536000, immutable`; the kept stylesheet names the fonts at theirs.
    - The build is a hash of the files' contents, not the commit: a checkout's commit doesn't change with an edit not yet committed (the edited script would be kept a year), and an update that changes no file keeps everyone's copies.
    - Another build's address (a page drawn before an update) answers the file as it is now, `no-cache`. The plain addresses answer as before (`/static` `no-cache`, `/fonts` and `/assets` without Cache-Control). Pages stay `no-store`.
    - Every template uses `static_url` (a test fails on a plain address: a page script loaded at its plain address would be a second app.js module).
  - **Measured** (bench load on a 3,000-invoice seed, 60 people, think 4 s, 2 web workers, 180 s; the 4 cores shared with other agents):

    | | before | from memory | kept addresses too |
    |---|---:|---:|---:|
    | requests | 22,834 | 26,344 | 6,045 (27,506 from the browsers' caches) |
    | /static p95 (server p95) | 1.82 s (1.67 s) | 130 ms (4 ms) | 67 ms (938, first loads only) |
    | /fonts, /assets p95 | 1.79 s, 1.96 s | 61 ms, 106 ms | 25 ms, 52 ms |
    | pages p50, p95 | 3.25 s, 49.4 s | 466 ms, 6.10 s | 269 ms, 1.67 s |
    | actions p95; over 30 s | 50.8 s; 745 | 12.3 s; 0 | 2.05 s; 0 |

  - **No compression** (laptop and server).
    - gzip's CPU per MB of AP Coder's HTML and JSON (100k database): level 1 4.4-6.6 ms, level 4 7.3-11.7, level 6 14-18, level 9 (Starlette's `GZipMiddleware` default) 60-75.
    - The invoice page (3.6 MB, 565-630 ms of CPU): level 1 16-24 ms for 0.53 MB, level 6 51-65 ms for 0.42 MB, level 9 241-271 ms. The queue (85 KB): level 1 0.5 ms for 9 KB; search (159 KB) 0.5 ms; the vendors page (1.8 MB) 5 ms.
    - On the office's gigabit network 3.6 MB take about 29 ms, which cost the server nothing; level 1 would cost 4-5 core-s a minute at 100 people (3% of the 4-core budget, cpu.md) and save no time anyone sees.
    - Where it matters (a VPN at 20 Mb/s: 1.4 s against 0.2 s), IIS in front compresses `text/html` and `application/json` only, at its lowest level (SERVER.md). Never a stream or file (CSV, xlsx, PDF, PNG, ZIP).
    - Static files are not compressed either: each browser fetches them once per build.
    - Revisit if the review screen's vendor list leaves the page (then about 0.3 MB: 1.5 ms at level 1).
  - **Polls** (`app.js` `poll`, used by `pollJob` and the review screen's reading):
    - Nothing while the tab is hidden; a look at once when it is back.
    - After an answer the same as the last (or none), half as long again, up to three times the pace (a job's at least 5 s); a change starts again at the pace.
    - A look answered by the login (a 401, or a GET sent on to /login) stops it (B-1720); the job pages then go to the login and come back after it.
    - `watchJob` asks `/jobs/<name>?only=done,note`: the state, done and note, not the job's 500 lines.
    - Every fetch has a deadline (`fetchWithin`, AbortController): a look 30 s, an action (`post`) and the setup report 5 minutes; past it, the existing "didn't answer" wording.
  - **The workers' balance:** a busy worker hands its connections back (`server.HandBack`): while requests wait for one of its threads, its answers say `Connection: close`. `KEEP_ALIVE_SECONDS` stays 75.
    - How a connection finds a worker: the workers listen on one socket. On Linux each worker's epoll wakes for a new connection, and the first to `accept()` takes it; uvicorn sets no `SO_REUSEPORT`.
    - On Windows uvicorn's supervisor gives each spawned worker its copy of the listening socket (`socket.share` / `fromshare`, listening before any worker starts: B-1711). Each worker runs the selector event loop (uvicorn's choice under its supervisor), so every worker's `select()` wakes and the first to accept takes it (B-1441 saw two woken for one). The same race on both systems.
    - A free worker's loop usually wins it: a busy one waits for the GIL behind its threads. What kept people on a busy worker is the browser keeping its connections (minutes: Chrome up to 5, Firefox about 2) and the server 75 s.
    - The harness's people closed theirs after 5 s unused (httpx's default), which hid it. `bench load --keep-alive 300` keeps them as a browser does; the default stays 5.
    - Measured with people keeping connections 300 s:
      - 50 people, think 10 s, 4 workers: one worker did 54% of the work (0.32 cores against 0.07-0.11).
      - Saturated (60 people, think 4 s, 2 workers), three pairs run back to back: pages p95 5.2 / 24.0 / 33.3 s with 75 s, 4.1 / 19.6 / 16.6 s with HandBack; actions p95 10.8 / 35.6 / 45.0 s against 7.3 / 26.1 / 22.5 s; over 30 s 0 / 147 / 343 against 0 / 74 / 9. The workers' gap 0.14-0.31 cores against 0.08-0.13. (The machine's load average was 6-13 in pairs 2 and 3: compare within a pair.)
      - With the harness's default people (reconnecting after 5 s unused) HandBack changes nothing beyond the noise: two pairs, pages p95 1.42 / 1.19 s without, 1.16 / 1.66 s with; actions 1.97 / 1.22 s, 1.10 / 1.86 s.
    - A shorter keep-alive was refused: at 5 s, 12 requests got no answer in the saturated run and 1 in the light one (B-1295's race: an idle connection closed as the browser sent on it); pages p95 9.9 s.
    - Closing whenever every thread is taken (not only with requests waiting): worse in its one run (15.5 s, on a loaded machine); not pursued.
    - `Connection: close` comes in the answer: the browser opens a new connection for its next request, nothing is lost, and IIS ARR in front does the same. A new HTTPS connection costs the worker that takes it 1.2 ms (0.4 ms resumed).
  - Left: the import's poll still sends its lines (up to 500, 57 KB; process.js shows them all); an upload (XHR) has no deadline (it may take minutes). Re-measure the 100k load with `--keep-alive 300`.
  - (`web/core.py`, `web/app.py`, `web/server.py`, `web/templates/*`, `web/static/js/{app,invoice,help}.js`, `bench/load.py`; `tests/web/test_web_app.py`, `test_server_browser.py`, `test_server_capacity.py`)
