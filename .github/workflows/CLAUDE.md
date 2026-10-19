# .github/workflows: the CI workflow

`ci.yml` tests every change: lint and the test suite on Linux and Windows, then the real installs a person does
(the launcher, the installer, the offline bundle with the internet cut off, and updates over older versions).
It does not build releases or publish anything. The online jobs set `AP_ALLOW_INTERNET=1` to install from PyPI;
the offline jobs point every proxy at a closed port (127.0.0.1:9) to prove AP Coder runs with no internet.

**Depends on:** `scripts`, `scripts/ci`, `tests`, `ap_coder`, `samples`
**Used by:** GitHub Actions; `tests` (`tests/test_ci_workflow.py` reads `ci.yml`)

## Interface
- Triggers: push to `main`, pull requests, `workflow_dispatch` (a branch without a PR: run it by hand). Each commit of a
  PR runs once (D-36). A newer push cancels the older run.
- Jobs, one line each:
  - `tests`: Ubuntu and Windows x Python 3.10, 3.12, 3.13, 3.14: `pip install -e ".[dev]"`, `pytest -q`.
  - `lint`: `ruff check` and `ruff format --check` once, on Ubuntu (the dev extra's ruff).
  - `browser-tests`: Ubuntu, Playwright and its Chromium installed online, then the `in_a_browser` tests with
    `AP_REQUIRE_BROWSER=1` (a missing browser fails, never skips).
  - `codebase-map`: `python scripts/check_map.py --changed <base>` (a PR's base branch; on a push to main, the commit
    before it): the map's errors fail; a folder whose code changed without its CLAUDE.md is a warning in the log.
  - `windows-installer`: `APProcessor.bat --installer --yes ...` twice (second run reuses .venv and packages), self-test, `/healthz`, `--fresh-start` keeps `.env`.
  - `windows-pilot-launcher`: the double-click on Windows: first setup prints the readiness summary; a second click opens the running one; one shortcut; `fetch_models.py --check`, `doctor`, `pilot_check.py`; a later start within a minute.
  - `windows-offline-bundle`: builds the bundle, unzips it, double-clicks offline (wheelhouse only), `pilot_check.py`, then `tests/test_offline_guard.py`.
  - `macos-offline-bundle`: the same on macOS with Python 3.14 as the only Python.
  - `mac-pilot-update`: the pilot tester's Mac after PR 8 (e1cf61a) with `scripts/ci/fake_lm_studio.py` as LM Studio; the Mac bundle into a new folder, over the old one, via the installer; GitHub's ZIP with an old wheelhouse must stop with one plain sentence.
  - `update-over-older-install`: Ubuntu, macOS, Windows x three old versions (81f691e, db95511, e1cf61a) from `scripts/ci/old_versions.py`: install, use, then the new bundle offline must keep every invoice, setting and `.env` value.

## Gotchas and decisions
- How to read a failure: open the failed step. Shell steps stop at a line `FAILED: <what>`; PowerShell steps
  `throw` a sentence naming what was missing; just above it is the launcher's own output (`launch.out`,
  `launch1.out`, `install1.out`...), printed whole. A red `lint` job is ruff (run `ruff format` locally).
- `update-over-older-install`: `old_versions.py check` prints one line per thing lost or left; the line
  "the update lost or left something" only sums them up. Read the lines above it.
- Background processes do not outlive a step on Windows: the update job is one long step on purpose.
- The checks match the launcher's printed sentences ("already running", "Self-check OK", "reusing .venv"):
  rewording one in `scripts/launch.py` or `scripts/install.py` breaks a job; change both together.
- Env names are case-insensitive on GitHub: the lower-case proxy copies are set in the step's bash script, never in `env:`.

## Files
- `ci.yml`: the one CI workflow (nine jobs, above).
