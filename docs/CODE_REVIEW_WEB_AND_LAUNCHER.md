# Code review: the dashboard and the launcher

A skeptical review and bug hunt of the web app (`ap_coder/web/`) and of everything that installs, starts and
updates AP Coder (`APProcessor.bat` / `.command`, `scripts/`, `ap_coder/core/paths.py`, `offline.py`, `doctor.py`,
`cli.py`). The engine (pipeline, capture, store, exports) and the Mac install scenario were reviewed separately.

Every real bug was reproduced by a failing test first (`tests/test_code_review_fixes.py`), then fixed in its own
commit with the smallest change that fixes it.

## How it was exercised

- **Every page, both widths.** `python -m ap_coder serve` on a throwaway data folder with the demo invoices; a
  Playwright crawl (Chromium) followed every same-origin link from `/review` and `/settings`, 144 URLs, at 1366 px
  and 390 px, and fetched each of the 18 downloads. It found no console errors, no page errors, no 4xx/5xx and no
  horizontal overflow at 390 px.
- **Every control on the review screen.** Each of the 8 demo review screens, at both widths: every visible
  non-submit button, tab, popover and zoom control was clicked, and the keyboard shortcuts were pressed. No JS errors.
- **Every POST route, odd input.** Each POST route of every page module (the Settings tabs too) was sent the
  route's own form fields plus every key a handler reads from `request.form()`. Each field got `""`, `x`, `-1`,
  `1e309`, `99999999999999999999`, `nan`, HTML, `0`, a bad date and `../../etc`, for a real invoice and a missing
  one. That was about 1,000 requests, run in-process so a traceback shows.
- **Every GET route, odd query.** The same values in each route's query parameters (about 3,000 requests).
- **Edge addresses typed by hand.** Long numbers, negative pages, unknown tabs, a NUL in the search box.
- **The launcher tools.** `doctor`, `share-report`, `check_deps.py`, `pilot_check.py`, `fetch_models.py --check`
  and `running.py` were each run with four kinds of data folder: empty; a `.env` with non-numbers, bad URLs and
  undecodable bytes; a truncated settings JSON; and a data folder that does not exist. None gave a traceback.

## Bugs found and fixed

| Commit | Bug | Fix |
|---|---|---|
| A damaged settings file or launcher state never stops a page or a start | `~/.ap_coder/settings.json` or `.venv/ap_coder_launcher.json` holding valid JSON that is not an object (`[]`, `null`), or an install record whose folder is a number: every page and every start stopped on `AttributeError` / `TypeError` | Both read as `{}`, like an unreadable file; bad install entries skipped |
| The stamped PDF of a vendor named beyond Latin-1 downloads | `/review/<id>/approved.pdf` put the vendor's name in a plain `filename=`: "Bœuf Œuvre" answered 500 | Uses `files.download` (ASCII fallback + `filename*=UTF-8''`) like every other download |
| Process invoices never breaks on a file that can't be opened | A scan still being written, a PDF locked by another program or a cloud placeholder in `invoices/`: the Process page and "Process N files" answered 500 | That file is left for the next pick-up, and the page says so |
| Settings: a number too large or not a number is refused plainly | Review threshold and Azure daily cap: `1e309` / `inf` gave 500 (`OverflowError`); `nan` gave a Python message | Plain "must be a number" refusal |
| One free-port function; serve never starts over a running AP Coder | `serve` chose a port by *connecting*, so a port Windows reserves (Hyper-V/WSL) was picked and the start failed. With every port busy it returned a busy one. `serve --port N` while AP Coder ran on N overwrote the running server's file (losing its shutdown token), deleted it, and printed "AP Coder is running at" about the other server | One bind-tested `offline.free_port` for launcher and `serve`; `serve` says the port is in use and starts nothing |
| Reopen for correction and the second approval | Reopening an invoice exported meanwhile (a stale page) was refused, but the supplier's touchless status had already been withdrawn. A second approval by the first approver said "someone else changed this invoice meanwhile" | Checks whether it can be reopened first; says the second approval must come from someone else |
| An invoice number too large to exist is not found | `/review/99999999999999999999` (and `/page/1.png`, `/words`, `/reader`, `/approved.pdf`, `/search/<id>/approved.pdf`, and posted `ids` for bulk approve, forget lessons, delete) reached SQLite: `OverflowError`, 500 Internal Server Error | An app-level handler for SQLite's integer overflow answers the usual not-found page (HTML) or `{"ok": false, "error"}` (POST) |

## Per module

### `web/security.py`: verdict sound

It refuses any Host header that is not this computer or the configured address (DNS rebinding). It refuses any
POST without `X-AP-Coder: 1`, or whose `Origin` / `Sec-Fetch-Site` says another site sent it (CSRF).

- Checked: `[::1]`, `::ffff:127.0.0.1`, a trailing dot, a missing Host header, `Origin: null`, and listening on
  `0.0.0.0` (IP literals and this PC's own name only). Requests with no Host header are allowed; they cannot come
  from a browser.
- Nothing to fix.

### `web/app.py`, `web/server.py`: verdict sound after fixes

`app.py` builds the app: page routers, `/healthz`, a token-guarded `/shutdown` (loopback only, constant-time
compare), `/jobs/<name>`, and the error pages. `server.py` runs uvicorn, writes the server file, and opens the
browser.

- Fixed: the overflow 500, and `serve` over a running AP Coder (both above).
- Left: FastAPI's own 422 answer (`{"detail": [...]}`) has no `error` key, so `app.js` shows nothing for it. Only
  a tampered form can reach it: no form field a person types into is `int`/`float`-typed (the fuzz found 422 only
  for hidden `ids` and `key` fields).

### `web/core.py`: verdict sound

The shared pieces: templates (autoescape on), the rail and its counts, the reviewer's name, flash messages, and the
`done` / `message` / `refuse` answers.

- `local_url` keeps every redirect on this server (`//host`, `/\host`, tab and newline tricks).
- The flash store is pruned on each write (10 minutes), so it stays bounded.
- Nothing to fix.

### `web/jobs.py`: verdict sound

Background jobs (one per name), with a progress log capped at 500 lines.

- The state and result are set together under the lock; an exception becomes the job's error, never a spinner
  that keeps going.
- `/jobs/<name>` uses `known()`, so it never creates a job for a name in the URL.
- Left: if `threading.Thread.start` itself failed (no threads left), the job would stay "running". That is not
  reachable in practice.

### `web/files.py`: verdict sound

Downloads with safe `Content-Disposition`. Uploaded spreadsheets wait in memory under a token: at most 24, an hour
each, 50 MB each.

- Left: up to about 1.2 GB could sit in memory in the worst case. It is bounded, and one person uses the dashboard.

### `web/pages/*` (process, review, invoice, accounts, exports, learning, settings…): verdict sound after fixes

Each page renders through `core.render`; each action answers JSON.

- Fixed: Process invoices on an unopenable file; the Settings numbers; reopen and second approval; the stamped
  PDF name; ids too large.
- Checked:
  - upload names (`safe_file_name`: no folders, no dot names, 80 characters);
  - backup downloads (by listed name only, never a path);
  - every redirect target through `local_url`;
  - drafts (kept per existing invoice: bounded);
  - double submits (`data-sending`).
- Left: `/process/upload` reads each uploaded file whole into memory with no size limit. It is a local
  single-user app and the files are invoices; a limit would need a product decision on what is "too big".

### `web/invoice_view.py`, `document.py`, `charts.py`, `minimd.py`: verdict sound

The review screen's model, the page images and words, the SVG charts, and the small Markdown used by Help.

- Every text is escaped before `Markup`.
- `ui.document_text` passes only a strict table-tag allowlist (no attributes beyond `rowspan` / `colspan`
  digits).
- Nothing to fix.

### `web/templates`, `web/static/js`: verdict sound

Autoescaped templates; plain-module JavaScript.

- Every `innerHTML` is either `escapeHtml(...)` or HTML drawn and escaped by the server.
- No console errors on any page at either width.
- Left: the 422 case above.

### `APProcessor.bat`, `APProcessor.command`: verdict sound

They find Python 3.11 or newer (3.12 first), reuse `.venv` when `launch.py --venv-ok` says so, and run
`scripts/launch.py`.

- Checked:
  - quoted paths with spaces and parentheses;
  - a "command not found" exit code (9009) never taken for a version;
  - Apple's `/usr/bin/python3` stub never run without the developer tools;
  - a broken `.venv` falls back to finding Python.
- Nothing to fix.

### `scripts/launch.py`, `scripts/running.py`: verdict sound after fixes

`launch.py` covers setup, the start lock, update and hand-over, the shortcut, the self-check and running the
dashboard. `running.py` finds and judges a running AP Coder and stops an older one.

- Fixed: a damaged launcher state; one bind-tested free port.
- Checked:
  - a broken or partial `.venv` is remade (and a `.venv` that can't be removed is said);
  - the lock is an OS lock released when the process ends, and on a file system without locks the start carries
    on;
  - `--port` values that are bad or `0`;
  - Ctrl+C ends the server.

### `scripts/upgrade.py`: verdict sound

Update over an older install:

- the record of installs;
- pruning the files the new bundle no longer ships;
- moving 0.1 data out of code folders (resumable, recorded while it runs);
- repointing the database's file paths (backed up first, one transaction);
- removing older folders only when they hold nothing but AP Coder's own files and are not running.

- Checked: interrupted moves (`data_move` resume), a source already gone, name clashes, models fetched into a
  bundle (listed in its manifest, so they never keep a folder).
- Nothing to fix.

### `scripts/install.py`, `check_deps.py`, `fetch_models.py`, `pilot_check.py`, `build_offline_bundle.py`: verdict sound

- No traceback with any of the four damaged data folders.
- `check_deps.py` reports `ap_coder` missing until `pip install -e .` has run; that is by design, because the
  launcher's `.venv` installs it.

### `ap_coder/core/paths.py`, `offline.py`, `doctor.py`, `cli.py`: verdict sound after fixes

The data folder lookup, the settings file and install record, offline guards and the free port, the health check,
and the command line.

- Fixed: a settings file that is not an object; one `free_port`.
- Left: `write_user_settings` is a read-modify-write with a per-process temporary name. Two threads of one process
  writing at the same moment could lose one value. In practice only the Settings page writes from the dashboard,
  one person at a time.
