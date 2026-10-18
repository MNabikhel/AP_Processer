# tests: the test suite (pytest), its helpers and fixtures

About 130 test files for the whole app, the launcher and the update, plus the helpers they share: autouse isolation
in `conftest.py`, a dashboard client, and the offline proof (a network guard and a walk through a running server).
Every bug fix starts here with a failing test (docs/BUGS.md names it). Tests never touch the real data folder, a
real LM Studio or the internet.

**Depends on:** `ap_coder` (and its sub-packages), `scripts`, `scripts/ci`, `data`, `samples`, `docs`, `.github/workflows`
**Used by:** `.github/workflows` (`pytest -q`; `test_offline_guard.py` inside the unzipped bundle); developers

## Interface
- Run: `pip install -e ".[ocr,dev]"` in `.venv`, then `python -m pytest -q tests`, serially. Don't use pytest-xdist: some
  parametrize ids hold PDF bytes made at collection (test_msg_and_triage.py), which differ between processes.
- Markers (pyproject.toml): `slow` and `upgrade`, both only on test_update_from_old_versions.py; they deselect nothing by themselves: it skips unless
  `AP_UPGRADE_TESTS=1` (with `AP_UPGRADE_BUNDLE=dist/APProcessor-offline-...zip`): `python -m pytest -m upgrade -v`.
- conftest.py, autouse for every test: `AP_PRIVATE_DIR` and `AP_USER_SETTINGS` in tmp_path, `AP_ALLOW_INTERNET` unset,
  reading caches cleared, no background page reader or Azure thread, no local model (LM Studio refused).
- conftest.py fixtures: `offline_guard`, `reference` (the sample data in data/), `settings`, `ground_truth`,
  `sample_markdown_path`, `few_sql_variables` (SQLite allowing few variables: a new id-list query is tested with it, B-11); fakes `FakeOpenAI`, `FakeDIClient` and `make_completion()`; constants ROOT, DATA, SAMPLES, SAMPLE_STEM.
- webclient.py: `client_for()` (a FastAPI TestClient on the test's store, no reader thread), `post()` (with the
  `X-AP-Coder: 1` header the page sends), `follow()` (the page an action leads to), `draft_form()`, `gt()`, `add_clean()`, `web_settings()`.
- offline_guard.py: `install()` / `uninstall()` patch the socket module so anything but 127.0.0.0/8, ::1 and localhost
  raises `OfflineViolation` and is recorded in `attempts`; `subprocess_env()` gives a subprocess the guard (via offline_guard_site/) and bait proxy and pip-index settings.
- offline_flow.py: `walk()` drives a real `python -m ap_coder serve` over HTTP like AP would (every page, every sample,
  approval, export, Settings, Help) and reports any address that is not this server; `python -m tests.offline_flow --netns` under `unshare -rn`.

## Gotchas and decisions
- A test that opens the dashboard must not leave threads running: start your own BackgroundReader and stop it.
- A test that needs LM Studio starts a fake one (see test_local_llm.py, scripts/ci/fake_lm_studio.py); never a real one.
- Bug tests keep the name given in docs/BUGS.md: renaming one breaks the log's **Test:** line.
- A lesson logged twice becomes a rule in test_code_rules.py (a check of the whole code), not a third fix.
- test_how_it_runs_doc.py fails when docs/HOW_IT_RUNS.md cites a `file:function` that no longer exists.
- test_package_data.py fails when the web app reads a file not listed in pyproject's package data.
- The browser tests (`test_in_a_browser_...` in test_web_invoice.py and test_offline_guard.py) find Chromium with conftest `chromium_path()` and skip without it; CI's "Browser tests" job sets `AP_REQUIRE_BROWSER=1`, so there they must run.

### Bug lessons
From docs/BUGS.md (one line each; the entry has the repro and the fix).
- B-104: Don't read a text file without naming its encoding. `tests/test_capture_edge_cases.py::test_a_french_quantity_in_one_space_group_is_read_whole`
- B-124: Don't let a test that skips without its tool be the only proof: make CI provide the tool and fail without it. `tests/test_ci_workflow.py::test_ci_runs_the_browser_tests_and_never_lets_them_skip`
- B-125: Don't assert on what a page does in an event handler right after the action that queues it: wait for the state, with a timeout. `tests/test_web_invoice.py::test_in_a_browser_the_popovers_open_inside_a_phone_screen`

## Files
- Helpers: `__init__.py` (makes tests a package), `conftest.py` (isolation and fixtures), `webclient.py` (dashboard client), `offline_guard.py` (network guard), `offline_flow.py` (the offline walk).
- `fixtures/`: data folder (no CLAUDE.md): page_reader/ holds one scanned page (s212-0000) with its Markdown and truth, used by test_transcript.py and test_verifier.py.
- `offline_guard_site/`: data folder (no CLAUDE.md): a sitecustomize.py that installs the guard in subprocesses.
- Store, config and core data: `test_audit.py`, `test_backups.py`, `test_data_layer_fixes.py`, `test_envfile_edge_cases.py`, `test_hardening.py`, `test_memory_store.py`, `test_park_notes.py`, `test_reference_data.py`, `test_safety.py`,
  `test_scale.py`, `test_schema.py`, `test_store_session.py`, `test_store_status_fixes.py`, `test_update_database.py`, `test_working_together.py`, `test_demo.py`, `test_help.py`.
- Capture and benchmark: `test_capture.py`, `test_capture_edge_cases.py`, `test_coarse_scans.py`, `test_line_item_recall.py`, `test_scan_reading_fixes.py`, `test_supplier.py`, `test_table_reading.py`, `test_totals_proven.py`, `test_transcript.py`,
  `test_turned_pages.py`, `test_reader_outcomes.py`, `test_reading_once.py`, `test_samples.py`, `test_evaluation.py`, `test_training_export.py`, `test_bench.py`, `test_bench_speed.py`.
- AI and readers: `test_azure_check.py`, `test_extraction.py`, `test_inference.py`, `test_local_llm.py`, `test_model_reading.py`, `test_page_reader.py`, `test_page_reads.py`,
  `test_page_worker.py`, `test_uniform_reading.py`, `test_verifier.py`, `test_figures.py`, `test_reading_sweep.py`, `test_lessons.py`, `test_history.py`.
- Tax, coding and touchless: `test_tax.py`, `test_taxreturn.py`, `test_terms.py`, `test_validation.py`, `test_math_check.py`, `test_rules.py`, `test_po.py`, `test_split.py`, `test_suggest_recurring.py`, `test_vendors.py`, `test_vendor_master.py`,
  `test_vendor_mail.py`, `test_coding_no_ai.py`, `test_offline_coding.py`, `test_offline_edge_cases.py`, `test_matching_fixes.py`, `test_pipeline.py`, `test_review.py`, `test_real_world.py`, `test_touchless.py`, `test_second_approval.py`, `test_sweep_fixes.py`.
- Exports, ERP and reports: `test_accruals.py`, `test_dupaudit.py`, `test_export_layout.py`, `test_exports_bulk.py`, `test_jde.py`, `test_jde_readiness.py`, `test_registers.py`,
  `test_spend.py`, `test_stamp.py`, `test_statements.py`, `test_directory.py`, `test_search.py`, `test_insights.py`, `test_operations.py`.
- Web dashboard: `test_web_analytics.py`, `test_web_app.py`, `test_web_dashboard_flows.py`, `test_web_document.py`, `test_web_finance.py`, `test_web_help.py`, `test_web_invoice.py`, `test_web_masterdata.py`, `test_web_process.py`, `test_web_review.py`,
  `test_web_robustness.py`, `test_web_settings.py`, `test_ui.py`, `test_reading_pages.py`, `test_touchless_pages.py`, `test_dashboard_sweep_fixes.py`, `test_round2_fixes.py`, `test_end_to_end_sweep_fixes.py`, `test_code_review_fixes.py`.
- Intake, CLI and launch: `test_intake.py`, `test_mailbox.py`, `test_msg_and_triage.py`, `test_config_and_cli.py`, `test_controls_watch.py`, `test_integration_e2e.py`, `test_launch.py`, `test_launch_log.py`,
  `test_launch_platforms.py`, `test_launch_python.py`, `test_launch_update.py`, `test_launcher_scripts.py`, `test_browser_open.py`, `test_running.py`, `test_install.py`, `test_ops_sweep_fixes.py`.
- Offline, packaging and update: `test_offline.py`, `test_offline_guard.py`, `test_network_gates.py`, `test_azure_optional.py`, `test_package_data.py`, `test_ci_workflow.py`, `test_check_map.py` (the codebase map's checker, and the repository's map is clean), `test_code_rules.py` (repeated bug lessons as checks of the whole code: text files name their encoding, no SQL placeholders one per item of a list), `test_how_it_runs_doc.py`, `test_update.py`, `test_update_file_paths.py`, `test_update_from_old_versions.py`.
