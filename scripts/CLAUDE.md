# scripts: setup, start, update, the offline bundle and developer tools

What installs, starts and updates AP Coder from outside the package (launcher, installer, update, offline bundle, OCR
models, self-check), plus the map checker and the review gate; the app itself is in `ap_coder/`, CI-only tools in `ci/`.
Offline by default: the bundle builder downloads (it sets `AP_ALLOW_INTERNET=1` for itself); the setup downloads (PyPI, OCR
models) only from the code alone, with no bundle (`AP_ALLOW_INTERNET=0` stops it), or with `AP_ALLOW_INTERNET=1`.

**Depends on:** `ap_coder/core` (paths, envfile, config, store, reference_data), `ap_coder/ops` (offline, doctor, cli), `ap_coder/ai` (local_llm, page_reader, page_worker, pipeline), `ap_coder/capture`
**Used by:** the root launchers (`APProcessor.bat`, `APProcessor.command` run launch.py), `.github/workflows`, `tests`, `.claude` (the hooks run check_map.py and map_review_gate.py)

## Interface
- `main()` (launch.py): argv -> exit code. The double-click: hand over to a newer install, make or reuse `.venv`, `take_over()`
  a running AP Coder, prune old files, install missing packages from `wheelhouse/`, settle the data folder, copy OCR models,
  shortcut, self-check, clean up older installs, then `python -m ap_coder serve` (`--installer`, `--yes`, `--check`, `--no-start`; the rest goes to serve).
- `take_over()` (running.py): this folder and version -> ("open" | "newer" | "clear" | "other": another computer account's | "blocked", port). Servers by
  `<data>/.ap_coder_server.json` and `GET /healthz`, stopped with `POST /shutdown` + `X-AP-Coder-Token`; an older `watch` / `read-pages` by its `<data>/.ap_coder_worker-<pid>.json` (`workers()`, `stop_worker()`).
- Update (upgrade.py): `newer_install()`, `hand_over()`, `prune_stale()` (files the manifest no longer lists), `settle_data_folder()`
  (data out of a code folder into `~/APCoder`), `repoint_files()`, `clean_up()` (old `.venv`, models, folders, after the self-check).
- `main()` (install.py): the installer, the same setup plus questions (`--yes`: none; `--help` lists the rest).
- `problems()` (check_deps.py): extras -> requirements of pyproject.toml missing or too old, and `ap_coder` itself not installed (exit 1 from the command line).
- `fetch()` (fetch_models.py): copy the PP-OCRv5 models from the bundle's `models/` (never download unless
  opted in; the table finder is read in place, `--table-finder` downloads it online); `--check` exits 1 if any is missing; also `--to`, `--from`, `--offline`. `choose_data_dir()` (first_run.py): the data folder already in use, else `~/APCoder`.
- `build()` (build_offline_bundle.py): `dist/APProcessor-offline-*.zip` with code, `wheelhouse/`, `models/` and
  `bundle_manifest.json` (version, built, SHA-256 per file). `--mac`, `--platform`, `--python`, `--no-models`, `--no-current`, `--dry-run`, `--manifest-for FOLDER`.
- `main()` (pilot_check.py): code each sample in `samples/` with no model (`check()`), compare with `samples/ground_truth/`,
  then one scan with local OCR when installed (`check_ocr()`); exit 0 when all are right. A throwaway database.
- check_map.py: each folder's map size (warns past 50, fails past 80, bug lessons aside), Files index, Depends on / Used by against the imports (`ap_coder/` folders only),
  Interface names, bug lessons and `ap_coder/MODULES.md`; `--changed BASE`, `--modules` (writes MODULES.md), `--hook` (reminds, never blocks).
- map_review_gate.py (D-35): `--reviewed` records every changed non-Markdown file's staged and work-tree blob in git's folder; `--commit`
  exits 2 before a `git commit` while a changed non-Markdown file differs (`_unreviewed()`); `commit_dirs()`: where a command commits (B-131).

## Gotchas and decisions
- launch.py, running.py, upgrade.py, check_deps.py, fetch_models.py, check_map.py, map_review_gate.py and install.py run before any package
  is installed: at the top they import only the standard library (check_deps.py: plus pip's `packaging`) and
  `ap_coder`'s standard-library modules (core/paths, core/envfile, ops/offline); anything else is imported inside a function, after setup.
- Never two versions on one database: a start calls `take_over()` (`run()` in launch.py) before packages, data or models are touched.
- `.venv` is made once and kept (remade only when broken or on a Python the bundle has no packages for); packages are installed only when missing or too old; the shortcut is rewritten, never added.
- `prune_stale()` removes only files under the manifest's own folders and AP Coder's retired top-level files; the
  data folder is never touched except to move legacy data into it.
- CI greps the sentences these scripts print ("already running", "Self-check OK", "Nothing to update"): reword them
  together with `.github/workflows/ci.yml`.
- The root launchers were checked by reading only (docs/BUGS.md N-30): test a change with a real double-click or CI.
- The data folder's `.ap_coder_server.json` holds the shutdown token: never print it.

### Bug lessons
From docs/BUGS.md (one line each; the entry has the repro and the fix).
- B-118: Don't let an existence check be satisfied by any match anywhere. `tests/test_check_map.py::test_an_interface_name_must_be_in_the_module_or_file_it_names`
- B-119: Don't parse source text in a way stricter than the interpreter that runs it. `tests/test_check_map.py::test_a_source_file_with_a_byte_order_mark_is_read_like_python_reads_it`
- B-120: Don't swallow a tool's error and report that nothing changed. `tests/test_check_map.py::test_a_base_git_cannot_compare_with_is_said_not_passed_over`
- B-121: Don't take the files git tracks as all the files that exist. `tests/test_check_map.py::test_a_new_module_counts_before_it_is_added_to_git`
- B-122: Don't let a checker crash on input it didn't expect; say what is wrong instead. `tests/test_check_map.py::test_a_new_module_counts_before_it_is_added_to_git`
- B-123: Don't skip a whole folder in a hook without checking who works in it. `tests/test_check_map.py::test_the_hook_reminds_of_an_edit_in_an_agents_worktree_too`
- B-131: Don't read a command line one way when it may mean two: check the folders of every reading. `tests/test_map_review_gate.py::test_a_windows_path_keeps_its_backslashes`

## Files
- Start: `launch.py` (the one-button setup and start), `running.py` (find and stop a running AP Coder), `first_run.py` (data folder and models on first run), `check_deps.py` (missing packages), `fetch_models.py` (OCR models from the bundle).
- Install and update: `install.py` (the installer's questions), `upgrade.py` (update over older installs), `terminal.bat` (a Windows prompt with .venv first on PATH).
- Build and check: `build_offline_bundle.py` (the offline ZIP), `pilot_check.py` (self-check on the samples), `make_sample_pdf.py` (regenerate the sample PDFs and Markdown), `check_map.py` (the CLAUDE.md map checker), `map_review_gate.py` (no commit the map-reviewer hasn't seen).
- `ci/`: CI-only helpers (has its own CLAUDE.md).
