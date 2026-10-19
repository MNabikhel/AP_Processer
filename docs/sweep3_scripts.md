# Sweep 3: scripts (launcher, update, installer, bundle)

#### B-167 · An update leaves the older version's `watch` / `read-pages` running, and it fails every new invoice · medium
- **Where:** `scripts/running.py:404` (`take_over`), `ap_coder/ops/cli.py:441` (`cmd_watch`), `scripts/upgrade.py:816` (`clean_up`)
- **Symptom:** after an update, invoices dropped in the invoices folder show as failed ("No module named 'pymupdf'") and the new version never reads them again.
- **Cause:** `take_over` found and stopped only servers (by their server file and `/healthz`); a `python -m ap_coder watch` (or `read-pages`) of the older version left no trace, kept running on the same database, and the clean-up then deleted the `.venv` it ran from. A failed attempt counts as seen, so nothing retried it.
- **Reproduce:** start `python -m ap_coder watch` from version A, update to B in another folder (double-click), drop an invoice in the invoices folder.
- **Reproduced:** yes (`<old>/ap_coder/capture/layout.py ... import pymupdf ... ModuleNotFoundError: No module named 'pymupdf'` / `chinook_AB_GST_CCO-26-10418.pdf: FAILED ModuleNotFoundError`); the new test failed with the old watch still running after `take_over`.
- **Fix:** `watch` and `read-pages` leave `<data>/.ap_coder_worker-<pid>.json` (pid, command, folder, version) while they run (`cli.running_note`); `take_over` reads them (`running.workers`, a note whose process is gone is removed) and stops an older one, or one from another folder, before setup (`stop_worker`: as Ctrl+C, then ended; only a process whose command line is AP Coder's `watch` / `read-pages`). GETTING_STARTED says to point a scheduled task at the new folder after an update unzipped into a new one (no command follows the current version by itself).
- **Test:** `tests/test_running.py::test_an_older_versions_watch_is_stopped_before_the_update`, `tests/test_config_and_cli.py::test_watch_and_read_pages_say_in_the_data_folder_that_they_run`
- **Lesson:** Don't make way for a new version by stopping only the process you can see on a port: every long-running command on the same data must say it runs.

#### B-168 · A second Windows account's double-click opens the first person's dashboard, under their name · medium
- **Where:** `scripts/running.py:164` (`judge`), `scripts/launch.py:1624` (`wait_for_other`), `ap_coder/web/app.py` (`healthz`), `ap_coder/web/server.py` (`write_server_file`)
- **Symptom:** with two Windows accounts sharing one code and data folder (GETTING_STARTED, "Two people approving"), the second person's double-click opens the AP Coder the first person left running, and approves under the first person's name.
- **Cause:** a running server was judged by folder and version only: the same folder and version was "same", so it was opened (or, older, stopped under the other person).
- **Reproduce:** A starts AP Coder; B (another account, same folders) double-clicks APProcessor.bat.
- **Reproduced:** yes (`take_over` returned `("open", port)` for a server whose `/healthz` names another user; `running_dashboard` returned its port).
- **Fix:** the server file and `/healthz` (to this computer only) carry `user`, the computer account running it (`app.os_user`); `judge` says "other" for another account's server (names compared ignoring case; a server that doesn't say is judged as before); `take_over` returns `("other", port)` and the launcher says whose AP Coder is open and to ask them to close it (`someone_elses`), in `run`, `wait_for_other` and `stop_older`. It is never opened nor stopped.
- **Test:** `tests/test_running.py::test_another_accounts_ap_coder_is_never_opened_nor_stopped`, `tests/test_launch.py::test_another_accounts_running_ap_coder_is_named_never_opened`, `tests/test_web_app.py::test_healthz_and_the_server_file_say_which_account_runs_it`
- **Lesson:** Don't identify a running program by its folder and version alone when two accounts can share that folder: record whose it is.

#### B-169 · On a new data folder the readiness summary says the GL account checks all passed · low
- **Where:** `scripts/launch.py:1400` (`setup_check_lines`)
- **Symptom:** a fresh install with no GL accounts imported prints "Setup checks: all 9 passed (GL accounts, tax mapping, coding rules, settings)".
- **Cause:** with nothing imported, the reference data falls back to the bundled sample (`ap_coder/ops/cli.py:reference_dir`), and the doctor's checks pass on it; the summary never said where the GL accounts came from.
- **Reproduce:** a new data folder, double-click: read the summary.
- **Reproduced:** yes (`[('ok', 'Setup checks: all 9 passed (GL accounts, tax mapping, coding rules, settings)')]`)
- **Fix:** when `reference_source` is the bundled sample, a warning line "GL accounts: none imported yet, so the sample ones are used: import yours on GL accounts & tax" comes first, and the summary line says "the sample GL accounts" (CI's `Setup checks: all \d+ passed` still matches).
- **Test:** `tests/test_launch.py::test_the_start_says_no_gl_accounts_were_imported_yet`
- **Lesson:** Don't report a check as passed without saying it ran on stand-in data.

#### B-170 · An update from GitHub's ZIP always keeps the older folder ("files AP Coder didn't put there (CLAUDE.md)") · low
- **Where:** `scripts/upgrade.py:59` (`BUNDLE_TOP`)
- **Symptom:** after updating over a folder made from GitHub's ZIP, the older folder stays: "the folder itself stays because it has files AP Coder didn't put there (CLAUDE.md)".
- **Cause:** a folder with no manifest file list is judged by `BUNDLE_TOP`, which lacked `CLAUDE.md` and `.claude`, both top-level names git tracks (and GitHub's ZIP holds).
- **Reproduce:** GitHub's ZIP of an older version, set up; update from a bundle beside it.
- **Reproduced:** yes (`Kept the older AP Coder folder .../app: the folder itself stays because it has files AP Coder didn't put there (CLAUDE.md)`)
- **Fix:** `CLAUDE.md` and `.claude` in `BUNDLE_TOP` (`.claude` also in `RETIRED_KEPT`: the bundle leaves it out, so it is never pruned); a test checks every top-level name git tracks is in `BUNDLE_TOP` (skipped outside a git checkout).
- **Test:** `tests/test_update.py::test_every_top_level_name_git_tracks_is_one_ap_coder_put_there`, `tests/test_update.py::test_an_older_folder_from_githubs_zip_is_removed_whole`
- **Lesson:** Don't keep a hand list of what a folder holds without a test that compares it with the source of truth.

#### B-171 · `--installer --full-tests` from an offline bundle fails the CI workflow tests · low
- **Where:** `tests/test_ci_workflow.py:44` (`needs_ci`), `scripts/build_offline_bundle.py:70` (`EXCLUDE_DIRS`)
- **Symptom:** the installer's full test run in an unzipped offline bundle reports failures.
- **Cause:** the bundle leaves `.github` out, but two tests read `.github/workflows/ci.yml` unconditionally (FileNotFoundError).
- **Reproduce:** copy the bundle's files (`build_offline_bundle.source_files()`) to a folder; `python -m pytest tests/test_ci_workflow.py` there.
- **Reproduced:** yes (`FileNotFoundError: ... .github/workflows/ci.yml` / `2 failed, 1 passed, 1 skipped`)
- **Fix:** the tests that read ci.yml skip when it isn't there (`needs_ci`); the parametrized one already had nothing to run. The other suites a bundle copy runs (check_map, map_review_gate, launcher scripts, update, doc) were checked to pass there.
- **Test:** `tests/test_ci_workflow.py::test_the_workflow_tests_skip_where_there_is_no_workflow`
- **Lesson:** Don't let a shipped test read a file the shipping step leaves out.

#### B-172 · HOW_IT_RUNS says the setup downloads only with AP_ALLOW_INTERNET=1 · low
- **Where:** `docs/HOW_IT_RUNS.md:380` (the network table), `docs/HOW_IT_RUNS.md:121` (`:installpython`)
- **Symptom:** IT reading the map believes GitHub's ZIP never downloads; it does (PyPI, the OCR models, winget's Python).
- **Cause:** the map predates `scripts/launch.py:plain_download`: from the code alone (no manifest, wheelhouse or models) the setup downloads unless `AP_ALLOW_INTERNET=0`; APProcessor.bat does the same for winget.
- **Reproduce:** read rows 1 to 3 of section 10 and step 6 of "Windows", then `plain_download` / `setup_online` and APProcessor.bat lines 30-34.
- **Reproduced:** yes (`PyPI only with AP_ALLOW_INTERNET=1` against `return [] if setup_online() else None`)
- **Fix:** the intro, step 6, section 5 item 2 and rows 1 to 3 say: from the code alone, or with `AP_ALLOW_INTERNET=1`; never with `AP_ALLOW_INTERNET=0`.
- **Test:** `tests/test_how_it_runs_doc.py::test_the_setups_downloads_from_the_code_alone_are_said`
- **Lesson:** Don't leave a network table behind when a new path to the internet is added.

#### B-173 · `--fresh-start` moves the data folder's models/ (and the guide says "your settings stay") · low
- **Where:** `scripts/install.py:278` (`fresh_start`), `docs/GETTING_STARTED.md` (Step 4)
- **Symptom:** after `--installer --fresh-start`, the table finder IT put in `<data>/models/` (or the setup downloaded there) is gone into `backup-<date>/`, so line items are read without it; and Settings → Review, Automation and Data & backups are back to their defaults though Step 4 promises "your settings stay".
- **Cause:** `fresh_start` kept only `.env`, `.cache` and `backups`; the dashboard's settings live in the database, which moves.
- **Reproduce:** put `yolov8n_layout_general6.onnx` in `<data>/models/`, run `fresh_start`.
- **Reproduced:** yes (`FileNotFoundError: .../models/yolov8n_layout_general6.onnx`)
- **Fix:** `models` is kept; GETTING_STARTED (the option table and Step 4) says the `.env` settings and models stay while what was set in the dashboard's Settings moves with the database (set it again in Step 5). Carrying database settings over was left as a choice for later (it is the sample trial's database).
- **Test:** `tests/test_install.py::test_fresh_start_keeps_the_models_ap_coder_reads`
- **Lesson:** Don't sweep a folder by "everything but a short keep list" without checking what else is read from it.

### Not fixed
- **N-XX** · scripts · B-167 finds a running `watch` / `read-pages` by the note it leaves; one started by a version from before the fix leaves none, so the first update from such a version still can't stop it (finding it would mean scanning every process and telling which data folder it uses). Stop it by hand before that one update (worth a line in WHATS_NEW / UPGRADING when released).
- **N-XX** · scripts · `watch` still treats an invoice that failed for a missing module as seen (no retry). Not changed, as the finding suggested: with the older watcher stopped, nothing fails that way; a retry rule for failures is a design choice.
- **N-XX** · scripts · an older `watch` / `read-pages` run by another computer account (or a scheduled task under one) can't be ended by this account: the start waits a minute ("close the window it runs in, or end its task in Task Scheduler"), then stops. Worker notes don't carry the account (B-168 covers servers only).
- **N-XX** · scripts · two accounts sharing one folder on macOS/Linux: the server file is mode 0600 and the code folder's lock file belongs to the first account, so the second account's start can't read the token or take the lock; it now relies on `/healthz`'s `user` (B-168). Sharing one folder between accounts is documented for Windows only.
