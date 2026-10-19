# ap_coder/web: the dashboard (FastAPI app, shared page helpers, server)

The local web dashboard: `create_app()` builds a FastAPI app with Jinja templates and a little plain JavaScript, `server.py` runs it on 127.0.0.1 by default (`python -m ap_coder serve`; `--host 0.0.0.0` opens it to the office network).
This folder holds what every page shares: rendering, flash messages, security, jobs, files, the review screen's logic.
The routes themselves are in `pages/`; the reading, coding, checks and the database are in `ap_coder/core`, `ap_coder/ai`, `ap_coder/coding` (store, pipeline, review...), never here.
Fully offline: CSS and scripts are served from this folder, fonts from `ap_coder/static`, the logo from `ap_coder/assets` (Azure is an opt-in exception, in `ap_coder/ai/azure_check.py`).

**Depends on:** `ap_coder`, `ap_coder/ai`, `ap_coder/capture`, `ap_coder/coding`, `ap_coder/core`, `ap_coder/intake`, `ap_coder/ops`, `ap_coder/web/pages`
**Used by:** `ap_coder/ops` (cli.py runs server.serve), `ap_coder/bench` (invoice_view.suggestions), `ap_coder/web/pages`

## Interface
- `create_app()` (app.py): (settings=None, store=None, address, start_reader) -> FastAPI. Mounts /static, /fonts, /assets; includes `MORE_MODULES` then every `PAGES` module; adds `/`, `/healthz`, `POST /shutdown`, `GET /jobs/<name>`.
- `serve()` (server.py): (host, port, open_browser, store) -> exit code; writes `<data folder>/.ap_coder_server.json` {pid, port, folder, version, token, started}.
- `render()` (core.py): (request, template, page key, **context) -> HTML with the rail, reviewer, flashes, banner; always `Cache-Control: no-store`.
- `done()` / `message()` / `refuse()` (core.py): a POST's JSON answer: `{ok, redirect}` (with a ?flash= token), `{ok, toast}`, `{ok: false, error}` (400 by default).
- `get_store()`, `get_settings()`, `jobs()`, `reviewer()`, `local_url()`, `money()` (core.py): what routes use; `templates` is the one Jinja environment.
  `reviewer()` and `login()` are the server process's user: on 0.0.0.0 every browser approves under that one name.
- `Job.start()` (jobs.py): runs work(job) in a thread, False if that job name is running; the page polls `snapshot()` at `GET /jobs/<name>` (the "process" job at /process/status).
- `LocalOnly` (security.py): middleware; `host_allowed()` and `same_origin()` decide. Header `X-AP-Coder: 1` (PAGE_HEADER), launcher token `X-AP-Coder-Token`.
- `download()`, `receive_sheet()`, `read_upload()`, `sheet_context()` (files.py): downloads and the two-step spreadsheet import (uploads wait in memory 1 hour, never on disk;
  pages/learning.py keeps its own, 4 at most). `read_excel()`: every workbook upload or emailed sheet (unzipped sizes checked, "NA" kept as text).
- `page_png()`, `page_sizes()`, `page_words()`, `shape_fields()` (document.py): the review screen's page pictures and words, boxes as page fractions (0..1, top-left).
- `analyse()`, `Draft` (invoice_view.py): the review screen's coding, checks and sections from the reviewer's edits (uses pages/review.py's `confidence_pct`).
- `line_chart()`, `bar_chart()` (charts.py): server-drawn SVG as Markup; `render()` (minimd.py): our own help Markdown to safe HTML.
- Data it owns: `app.state` (store, settings_factory, jobs, reader, azure_checker, port, shutdown_token, server; pages/invoice.py adds invoice_drafts); flash messages (10 min) and uploads in memory only.

## Gotchas and decisions
- Every request but GET, HEAD and OPTIONS needs `X-AP-Coder: 1`, and an Origin (when sent) of this server and no cross-site or same-site
  Sec-Fetch-Site, or `LocalOnly` answers 403; only `/shutdown` takes the token instead of the header.
- A Host header that is not this computer gets 400 (DNS rebinding); listening on 0.0.0.0 also allows an IP or this PC's name. Tests use `base_url="http://localhost"`: "testserver" is refused.
- GET never changes anything. A POST answers JSON (`done()`/`message()`/`refuse()`, or its own JSON for a job start or the review screen's redrawn sections), never an HTML page or a redirect status;
  an `HTTPException` raised in a POST is answered `{ok: false, error}` by `app.py`'s handler.
- Jinja autoescape is on. Never `|safe` or `Markup` text from an invoice; `ui.*` in templates is limited to the `_UI_HTML` builders, which escape.
- `static_version` is a hash of every file under static/, computed once at import: restart the server after editing CSS/JS.
- A spreadsheet from a person or an email is read with `files.read_excel`, never a bare `pd.read_excel` (size guard, "NA" kept; B-105, B-106); the openpyxl readers (`jde_worksheet.read`, triage) call `triage.sheet_unzips_too_big()` first.
- `done()` passes the URL through `local_url()`: a page never redirects off this server.
- Settings are read from the .env on every request (`settings_factory`), so a Settings change applies at once.
- `MORE_MODULES` routers are included before `PAGES`, so `/settings/azure-check` wins over `/settings/{tab}`.
- Only "process", "settings-doctor" and "azure-test" are `Job`s (one per name at a time); backups, restore, export files, the training ZIP and the
  duplicate audit run inside the request.
- invoice_view.py is over 1,100 lines: this folder does too much, see the scale assessment, PR #24.

### Bug lessons
From docs/BUGS.md (one line each; the entry has the repro and the fix).
- B-52: Don't parse money with a bare `float()`; refuse non-finite values in every field, not only in the grid cells. `tests/test_web_invoice.py::test_a_header_amount_that_is_not_a_finite_number_is_refused`
- B-53: Don't run anchored `(...)+$` regexes over input whose length you don't control. `tests/test_web_invoice.py::test_a_new_line_from_a_long_run_of_words_is_read_quickly`
- B-105: Don't put a size guard in one reader of untrusted files and leave the other readers of the same files without it. `tests/test_msg_and_triage.py::test_every_reader_of_an_uploaded_sheet_checks_its_unzipped_sizes`
- B-106: Don't read the same kind of upload with different parser options in three copies of the reader. `tests/test_web_masterdata.py::test_an_excel_cell_reading_na_is_kept_as_typed`
- B-111: Don't leave an unversioned module URL without a revalidation header. `tests/test_web_app.py::test_static_files_are_checked_again_on_every_use`

## Files
- `__init__.py`: package docstring: layout, how to add a page, the rules.
- `app.py`: create_app, error handlers, /healthz, /shutdown, /jobs.
- `core.py`: templates, render, rail, flash messages, store/settings access, shared helpers.
- `security.py`: Host check and POST header/origin check (LocalOnly).
- `server.py`: runs uvicorn, writes the server file, opens the browser.
- `jobs.py`: background jobs (Job, Jobs).
- `files.py`: downloads and uploaded spreadsheets.
- `document.py`: page PNGs, page sizes, words and field shapes for the viewer.
- `invoice_view.py`: the review screen's logic (Draft, analyse, grid actions).
- `charts.py`: inline SVG charts. `minimd.py`: a small, escaping Markdown renderer.
- `pages/`: one route module per page and PAGES, the rail (own CLAUDE.md).
- `static/`: CSS and js/ (own CLAUDE.md).
- `templates/`: the Jinja templates (own CLAUDE.md).
