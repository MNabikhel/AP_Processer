# scripts: setup, start, update, the offline bundle and developer tools

Everything that installs, starts and updates AP Coder from outside the package: the launcher behind the double-click,
the installer, the update over older installs, the offline bundle builder, the OCR model copier, the self-check, the
map checker and the map-reviewer gate. The app itself (`python -m ap_coder serve` and the rest) is in `ap_coder/`; CI-only tools are in `ci/`.
Offline by default: the bundle builder downloads (it sets `AP_ALLOW_INTERNET=1` for itself); the setup downloads from PyPI
only from the code alone (GitHub's ZIP or a checkout, no bundle; `AP_ALLOW_INTERNET=0` stops it) or with `AP_ALLOW_INTERNET=1`.

**Depends on:** `ap_coder/core` (paths, envfile, config, store, reference_data), `ap_coder/ops` (offline, doctor, cli), `ap_coder/ai` (local_llm, page_reader, page_worker, pipeline), `ap_coder/capture`
**Used by:** the root launchers (`APProcessor.bat`, `APProcessor.command` run launch.py), `.github/workflows`, `tests`, `.claude` (the hooks run check_map.py and map_review_gate.py)

## Interface
- `main()` (launch.py): argv -> exit code. The double-click: hand over to a newer install, reuse or make `.venv`,
  `take_over()` a running AP Coder, prune an older version's files, install missing packages from `wheelhouse/`,
  settle the data folder, copy OCR models, record the install, shortcut, readiness summary and self-check, clean up
  older installs once it passed, then `python -m ap_coder serve`. Options: `--installer`, `--yes`, `--check`, `--no-start`; the rest (`--port N`) goes to serve.
- `take_over()` (running.py): this folder and version -> ("open" | "newer" | "clear" | "blocked", port). Finds servers
  by `<data>/.ap_coder_server.json` and `GET /healthz`, stops an older one with `POST /shutdown` + `X-AP-Coder-Token`.
- Update (upgrade.py): `newer_install()`, `hand_over()`, `prune_stale()` (files the manifest no longer lists),
  `settle_data_folder()` (data out of a code folder into `~/APCoder`), `repoint_files()` (invoice paths into an old
  folder), `clean_up()` (old `.venv`, models and folders, after the self-check passed).
- `main()` (install.py): the installer, the same setup plus questions; `--yes`, `--no-update`, `--skip-tests`, `--full-tests`,
  `--data-dir`, `--reinstall`, `--no-shortcut`, `--fresh-start`, `--no-start`.
- `problems()` (check_deps.py): extras -> requirements of pyproject.toml missing or too old, and `ap_coder` itself not installed (exit 1 from the command line).
- `fetch()` (fetch_models.py): copy the PP-OCRv5 models (and the table finder) from the bundle's `models/` (never download
  unless opted in); `--check` exits 1 if any is missing; also `--to`, `--from`, `--offline`, `--table-finder`. `choose_data_dir()` (first_run.py): the data folder already in use, else `~/APCoder`.
- `build()` (build_offline_bundle.py): `dist/APProcessor-offline-*.zip` with code, `wheelhouse/`, `models/` and
  `bundle_manifest.json` (version, built, SHA-256 per file). `--mac`, `--platform`, `--python`, `--no-models`, `--no-current`, `--dry-run`, `--manifest-for FOLDER`.
- `main()` (pilot_check.py): read and code each sample in `samples/` with no model and no Azure (`check()`, one sample),
  compare with `samples/ground_truth/`, then one scan with local OCR when installed (`check_ocr()`); exit 0 when all
  read right. A throwaway database, nothing sent.
- check_map.py: `python scripts/check_map.py` (the whole map), `--changed BASE` (also warns when git can't compare),
  `--modules` (writes `ap_coder/MODULES.md`), `--hook` (Claude Code reminder, never blocks). New files count before `git add`.
- map_review_gate.py (D-35): `--reviewed` (the map-reviewer finished: record every changed file's staged and work-tree
  git blob, from the top folder, in git's folder); `--commit` (hook event on stdin: before a `git commit`, any changed
  non-Markdown file not reviewed as it is exits 2). `commit_dirs()`: the folders a shell command commits in.

## Gotchas and decisions
- launch.py, running.py, upgrade.py, check_deps.py, fetch_models.py, check_map.py, map_review_gate.py and install.py run before any package
  is installed: at the top they import only the standard library (check_deps.py: plus pip's `packaging`) and
  `ap_coder`'s standard-library modules (core/paths, core/envfile, ops/offline); anything else is imported inside a function, after setup.
- Never two versions on one database: a start calls `take_over()` (`run()` in launch.py) before packages, data or models are touched.
- `.venv` is made once and kept (made again only when broken or made with a Python the bundle has no packages for); packages are installed only when missing or too old; the shortcut is rewritten, never added.
- `prune_stale()` removes only files under the manifest's own folders and AP Coder's retired top-level files; the
  data folder is never touched except to move legacy data into it.
- CI greps the sentences these scripts print ("already running", "Self-check OK", "Nothing to update"): reword them
  together with `.github/workflows/ci.yml`.
- The root launchers' quoting and second-instance lock were checked by reading only (docs/BUGS.md N-30): test a change
  to them with a real double-click or the CI jobs.
- The data folder's `.ap_coder_server.json` holds the shutdown token: never print it.

### Bug lessons
From docs/BUGS.md (one line each; the entry has the repro and the fix).
- B-118: Don't let an existence check be satisfied by any match anywhere. `tests/test_check_map.py::test_an_interface_name_must_be_in_the_module_or_file_it_names`
- B-119: Don't parse source text in a way stricter than the interpreter that runs it. `tests/test_check_map.py::test_a_source_file_with_a_byte_order_mark_is_read_like_python_reads_it`
- B-120: Don't swallow a tool's error and report that nothing changed. `tests/test_check_map.py::test_a_base_git_cannot_compare_with_is_said_not_passed_over`
- B-121: Don't take the files git tracks as all the files that exist. `tests/test_check_map.py::test_a_new_module_counts_before_it_is_added_to_git`
- B-122: Don't let a checker crash on input it didn't expect; say what is wrong instead. `tests/test_check_map.py::test_a_new_module_counts_before_it_is_added_to_git`
- B-123: Don't skip a whole folder in a hook without checking who works in it. `tests/test_check_map.py::test_the_hook_reminds_of_an_edit_in_an_agents_worktree_too`

## Files
- Start: `launch.py` (the one-button setup and start), `running.py` (find and stop a running AP Coder), `first_run.py` (data folder and models on first run), `check_deps.py` (missing packages), `fetch_models.py` (OCR models from the bundle).
- Install and update: `install.py` (the installer's questions), `upgrade.py` (update over older installs), `terminal.bat` (a Windows prompt with .venv first on PATH).
- Build and check: `build_offline_bundle.py` (the offline ZIP), `pilot_check.py` (self-check on the samples), `make_sample_pdf.py` (regenerate the sample PDFs and Markdown), `check_map.py` (the CLAUDE.md map checker), `map_review_gate.py` (no commit the map-reviewer hasn't seen).
- `ci/`: CI-only helpers (has its own CLAUDE.md).
