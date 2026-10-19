# ap_coder/ops: the command line and operations (commands, doctor, offline guard, demo, help, share report)

How AP Coder is run and looked after: `python -m ap_coder <command>`, the readiness report, the offline guard and the
local OCR models, the demo with the sample invoices, the plain-English help for every check and the redacted summary
that is safe to paste into a ticket.
Grouped here from the flat `ap_coder/` (docs/decisions.md D-33): `ap_coder.cli` and the other old paths are aliases for one release.

**Depends on:** `ap_coder/ai`, `ap_coder/capture`, `ap_coder/coding`, `ap_coder/core`, `ap_coder/intake`, `ap_coder/web`
**Used by:** `ap_coder`, `ap_coder/ai`, `ap_coder/capture`, `ap_coder/erp`, `ap_coder/web`, `ap_coder/web/pages`

## Interface
- `main()` (cli.py): argv -> exit code (`__main__.py` and the `ap-coder` script call it); `watch_once()`: the invoices
  folder (and saved emails) once into the pipeline; `serve` runs `web/server.serve()`.
- `internet_allowed()` (offline.py): `AP_ALLOW_INTERNET` is 1, true, yes or on; `ppocrv5_ready()`: every model file there, with the right SHA-256.
- `run_checks()` (doctor.py): settings, reference data and LM Studio -> `Check` list (`python -m ap_coder doctor`; `--online`
  adds a dry run on the local model, and the Azure checks only with `AP_ALLOW_INTERNET=1`).
- `load_demo()` / `remove_demo()` (demo.py): the sample invoices through the whole product, without Azure; what it
  loaded besides (POs, vendors, the sample setup on an empty database) is recorded in a setting and removed with it.
- `help_for()` (help.py): a check code -> its `CheckHelp` from `CHECKS`; `build_share_report()` (share_report.py).

## Gotchas and decisions
- offline.py is standard library at the top: scripts/ import it before any package is installed (keep `__init__.py` empty).
- doctor and share_report never print values or amounts.
- A new check code needs its `CHECKS` entry in help.py (tests/test_help.py fails without it).
- cli.py's `SAMPLE_DATA_DIR` is `data/` at the project root: `Path(__file__)` is one folder deeper since the grouping.

### Bug lessons
From docs/BUGS.md (one line each; the entry has the repro and the fix).
- B-66: Don't work out the same path two ways. `tests/test_config_and_cli.py::test_watch_uses_the_dashboard_invoices_folder`
- B-67: Don't let the doctor check less than the code it vouches for. `tests/test_offline.py::test_doctor_checks_the_model_files_as_the_app_does`
- B-68: Don't leave doctor wording behind when a dependency's role grows. `tests/test_config_and_cli.py::test_doctor_fails_without_pillow`
- B-69: Don't assume a file's timestamp is in the past. `tests/test_controls_watch.py::test_a_file_stamped_in_the_future_is_picked_up`
- B-70: Don't print an error and then return success. `tests/test_config_and_cli.py::test_a_command_that_could_not_read_something_does_not_exit_0`
- B-72: Don't apply the "still copying" check to files your own process just wrote. `tests/test_controls_watch.py::test_watch_once_processes_the_invoices_it_just_took_out_of_emails`
- B-91: Don't put back raw text in the one branch after redacting it in the others. `tests/test_working_together.py::test_share_report_failure_category_never_prints_a_file_name`
- B-97: Don't let a coverage test match only one spelling of the thing it covers. `tests/test_help.py::test_every_check_has_help`

## Files
- `__init__.py`: the package docstring only.
- `cli.py` (commands), `doctor.py` (readiness report), `offline.py` (offline guard, OCR models); `demo.py` (sample invoices), `share_report.py` (redacted summary); `help.py` (help per check).
