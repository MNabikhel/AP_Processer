# ap_coder/web: the dashboard (FastAPI app, shared page helpers, server)

The local web dashboard: `create_app()` builds a FastAPI app with Jinja templates and a little plain JavaScript, `server.py` runs it on 127.0.0.1 by default (`python -m ap_coder serve`; `--host 0.0.0.0` opens it to the office network).
This folder holds what every page shares: rendering, flash messages, security, jobs, files, the review screen's logic.
The routes themselves are in `pages/`; the reading, coding, checks and the database are in `ap_coder/` (store, pipeline, review...), never here.
Fully offline: CSS and scripts are served from this folder, fonts from `ap_coder/static`, the logo from `ap_coder/assets` (Azure is an opt-in exception, in `ap_coder/azure_check.py`).

**Depends on:** `ap_coder`, `ap_coder/capture`, `ap_coder/web/pages`
**Used by:** `ap_coder` (cli.py runs server.serve), `ap_coder/bench` (invoice_view.suggestions), `ap_coder/web/pages`

## Interface
- `create_app()` (app.py): (settings=None, store=None, address, start_reader) -> FastAPI. Mounts /static, /fonts, /assets; includes `MORE_MODULES` then every `PAGES` module; adds `/`, `/healthz`, `POST /shutdown`, `GET /jobs/<name>`.
- `serve()` (server.py): (host, port, open_browser, store) -> exit code; writes `<data folder>/.ap_coder_server.json` {pid, port, folder, version, token, started}.
- `render()` (core.py): (request, template, page key, **context) -> HTML with the rail, reviewer, flashes, banner; always `Cache-Control: no-store`.
- `done()` / `message()` / `refuse()` (core.py): a POST's JSON answer: `{ok, redirect}` (with a ?flash= token), `{ok, toast}`, `{ok: false, error}` (400 by default).
- `get_store()`, `get_settings()`, `jobs()`, `reviewer()`, `local_url()`, `money()` (core.py): what routes use; `templates` is the one Jinja environment.
- `Job.start()` (jobs.py): runs work(job) in a thread, False if that job name is running; the page polls `snapshot()` at `GET /jobs/<name>`.
- `LocalOnly` (security.py): middleware; `host_allowed()` and `same_origin()` decide. Header `X-AP-Coder: 1` (PAGE_HEADER), launcher token `X-AP-Coder-Token`.
- `download()`, `receive_sheet()`, `read_upload()`, `sheet_context()` (files.py): downloads and the two-step spreadsheet import (uploads wait in memory 1 hour, never on disk).
- `page_png()`, `page_sizes()`, `page_words()`, `shape_fields()` (document.py): the review screen's page pictures and words, boxes as page fractions (0..1, top-left).
- `analyse()`, `Draft` (invoice_view.py): the review screen's coding, checks and sections from the reviewer's edits; no web code.
- `line_chart()`, `bar_chart()` (charts.py): server-drawn SVG as Markup; `render()` (minimd.py): our own help Markdown to safe HTML.
- Data it owns: `app.state` (store, settings_factory, jobs, reader, azure_checker, port, shutdown_token, server; pages/invoice.py adds invoice_drafts); flash messages (10 min) and uploads in memory only.

## Gotchas and decisions
- Every non-GET request needs `X-AP-Coder: 1` and a same-origin Origin, or `LocalOnly` answers 403; only `/shutdown` takes the token instead.
- A Host header that is not this computer gets 400 (DNS rebinding); listening on 0.0.0.0 also allows an IP or this PC's name. Tests use `base_url="http://localhost"`: "testserver" is refused.
- GET never changes anything. A POST answers JSON (`done()`/`message()`/`refuse()`, or its own JSON for a job start or the review screen's redrawn sections), never an HTML page or a redirect status.
- Jinja autoescape is on. Never `|safe` or `Markup` text from an invoice; `ui.*` in templates is limited to the `_UI_HTML` builders, which escape.
- `static_version` is a hash of every file under static/, computed once at import: restart the server after editing CSS/JS.
- `done()` passes the URL through `local_url()`: a page never redirects off this server.
- Settings are read from the .env on every request (`settings_factory`), so a Settings change applies at once.
- `MORE_MODULES` routers are included before `PAGES`, so `/settings/azure-check` wins over `/settings/{tab}`.
- Work over a second or two runs as a `Job`; one job per name at a time.
- invoice_view.py is over 1,100 lines: this folder does too much, see the scale assessment, PR #24.

### Bug lessons
From docs/BUGS.md (one line each; the entry has the repro and the fix).
- B-52: Don't parse money with a bare `float()`; refuse non-finite values in every field, not only in the grid cells. `tests/test_web_invoice.py::test_a_header_amount_that_is_not_a_finite_number_is_refused`
- B-53: Don't run anchored `(...)+$` regexes over input whose length you don't control. `tests/test_web_invoice.py::test_a_new_line_from_a_long_run_of_words_is_read_quickly`

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
