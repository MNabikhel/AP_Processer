# ap_coder/web: the dashboard (FastAPI app, shared page helpers, server)

The local web dashboard: `create_app()` builds a FastAPI app with Jinja templates and a little plain JavaScript;
`server.py` runs it on 127.0.0.1 (`--host 0.0.0.0` opens it to the office network). This folder holds what every page
shares: rendering, flash messages, security, jobs, files, the review screen's logic. The routes are in `pages/`; reading,
coding, checks and the database are in the core folders, never here. Fully offline: CSS and scripts come from this
folder, fonts from `ap_coder/static`, the logo from `ap_coder/assets`.

**Depends on:** `ap_coder`, `ap_coder/ai`, `ap_coder/capture`, `ap_coder/coding`, `ap_coder/core`, `ap_coder/intake`, `ap_coder/ops`, `ap_coder/web/pages`
**Used by:** `ap_coder/ops` (cli.py runs server.serve), `ap_coder/bench` (invoice_view.suggestions), `ap_coder/web/pages`

## Interface
- `create_app()` (app.py): (settings=None, store=None, address, start_reader) -> FastAPI. Mounts /static, /fonts, /assets; includes `MORE_MODULES` then every `PAGES` module; adds `/`, `/healthz`, `POST /shutdown`, `GET /jobs/<name>`.
- `serve()` (server.py): (host, port, open_browser, store) -> exit code; writes `<data folder>/.ap_coder_server.json` {pid, port, folder, version, token, started}.
- `render()` (core.py): (request, template, page key, **context) -> HTML with the rail, reviewer, flashes, banner; always `Cache-Control: no-store`.
- `done()` / `message()` / `refuse()` (core.py): a POST's JSON answer: `{ok, redirect}` (a ?flash= token; `local_url()` keeps it on this server), `{ok, toast}`, `{ok: false, error}` (400 by default).
- `get_store()`, `get_settings()`, `jobs()`, `local_url()`, `money()` (core.py); `reviewer()` and `login()`: this computer's user, or a colleague on the network (their name; `network <address>`).
- `Job.start()` (jobs.py): runs work(job) in a thread, False if that job name is running; the page polls `snapshot()` at `GET /jobs/<name>`.
- `Identity`, `person()`, `PERSON`, `COOKIE`, `WHO_PATH`, `cookie_value()`, `name_from_cookie()`, `clean_name()`, `is_this_computer()`,
  `network_login()` (identity.py): on 0.0.0.0, who each other computer's browser is (a cookie signed with `app.state.identity_secret`, made at each start); without one it gets /who only (D-34).
- `LocalOnly` (security.py): middleware; `host_allowed()` and `same_origin()` decide. Header `X-AP-Coder: 1` (PAGE_HEADER), launcher token `X-AP-Coder-Token`.
- `download()`, `receive_sheet()`, `read_upload()`, `sheet_context()` (files.py): downloads and the two-step spreadsheet import (uploads held in memory 1 hour).
  `read_excel()`: every workbook upload or emailed sheet (unzipped sizes checked, "NA" kept as text).
- `page_png()`, `page_sizes()`, `page_words()`, `shape_fields()` (document.py): the review screen's page pictures and words, boxes as page fractions (0..1, top-left).
- `analyse()`, `Draft` (invoice_view.py): the review screen's coding, checks and sections from the reviewer's edits.
- `line_chart()`, `bar_chart()` (charts.py): server-drawn SVG; `render()` (minimd.py): our own help Markdown to safe HTML.
- Data it owns: `app.state` (store, settings_factory, jobs, reader, port, shutdown_token, identity_secret; pages/invoice.py adds the drafts); flash messages and uploads in memory only.

## Gotchas and decisions
- Any method but GET, HEAD, OPTIONS needs `X-AP-Coder: 1`, an Origin (if sent) of this server, no cross- or same-site Sec-Fetch-Site, else 403; `/shutdown` takes the token.
- A Host that is not this computer gets 400 (DNS rebinding). Tests use `base_url="http://localhost"`: "testserver" is refused.
- GET never changes anything. A POST answers JSON (`done()`/`message()`/`refuse()` or its own), never HTML or a redirect;
  `app.py` answers an `HTTPException` in a POST with `{ok: false, error}`.
- Jinja autoescape is on. Never `|safe` or `Markup` text from an invoice; `ui.*` in templates reaches only the `_UI_HTML` builders, which escape.
- `static_version` hashes static/ once at import: restart the server after editing CSS/JS.
- A spreadsheet from a person or an email goes through `files.read_excel`, never a bare `pd.read_excel` (B-105, B-106).
- Settings are read from the .env on every request (`settings_factory`), so a Settings change applies at once.
- Who is asking is a contextvar set by `Identity` (and `store.EVENT_ORIGIN`); a `Job` thread and `process_many`'s pool inherit it (B-130).
- Only "process", "settings-doctor" and "azure-test" are `Job`s; backups, restore, exports and the duplicate audit run in the request.
- invoice_view.py is over 1,100 lines: this folder does too much, see the scale assessment, PR #24.

### Bug lessons
From docs/BUGS.md (one line each; the entry has the repro and the fix).
- B-52: Don't parse money with a bare `float()`; refuse non-finite values in every field, not only in the grid cells. `tests/test_web_invoice.py::test_a_header_amount_that_is_not_a_finite_number_is_refused`
- B-53: Don't run anchored `(...)+$` regexes over input whose length you don't control. `tests/test_web_invoice.py::test_a_new_line_from_a_long_run_of_words_is_read_quickly`
- B-105: Don't put a size guard in one reader of untrusted files and leave the other readers of the same files without it. `tests/test_msg_and_triage.py::test_every_reader_of_an_uploaded_sheet_checks_its_unzipped_sizes`
- B-106: Don't read the same kind of upload with different parser options in three copies of the reader. `tests/test_web_masterdata.py::test_an_excel_cell_reading_na_is_kept_as_typed`
- B-111: Don't leave an unversioned module URL without a revalidation header. `tests/test_web_app.py::test_static_files_are_checked_again_on_every_use`

## Files
- `__init__.py`: package docstring: layout, how to add a page, the rules. `app.py`: create_app, error handlers, /healthz, /shutdown, /jobs.
- `core.py`: templates, render, rail, flash messages, store/settings access, shared helpers. `server.py`: runs uvicorn, writes the server file, opens the browser.
- `security.py`: Host check and POST header/origin check (LocalOnly). `identity.py`: who a colleague on the network is (Identity).
- `jobs.py`: background jobs (Job, Jobs). `files.py`: downloads and uploaded spreadsheets.
- `document.py`: page PNGs, sizes, words and field shapes for the viewer. `invoice_view.py`: the review screen's logic (Draft, analyse, grid actions).
- `charts.py`: inline SVG charts. `minimd.py`: a small, escaping Markdown renderer.
- `pages/`: one route module per page and PAGES, the rail. `static/`: CSS and js/. `templates/`: the Jinja templates (each has its own CLAUDE.md).
