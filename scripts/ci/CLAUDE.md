# scripts/ci: helpers for the CI jobs that install AP Coder for real

Two command-line tools the CI workflow runs: a stand-in for LM Studio, and a tool that installs and uses older AP Coder
versions to test the update over them. They are never shipped to run on a pilot laptop and never imported by the app.
The setup and update code they exercise is in `scripts/` (launch.py, upgrade.py, running.py).

**Depends on:** `ap_coder` (old_versions.py `seed` imports it, run inside the old version's own .venv), the root launchers (`scenario` double-clicks the new version)
**Used by:** `.github/workflows` (mac-pilot-update, update-over-older-install), `tests` (test_update_from_old_versions.py runs old_versions.py; test_offline_guard.py lists both as the tools allowed to go online or listen)

## Interface
- fake_lm_studio.py `serve [--port 1234] [--chat ID] [--reader ID] [--embed ID] [--api v0|v1|both] [--reviewer MODE] [--log FILE]`:
  an OpenAI-compatible server on 127.0.0.1 with the pilot tester's three models (chat Qwen 3.5 9B, page reader OvisOCR2,
  a Qwen3 embedding model). Answers `/v1/models`, LM Studio 0.3's `/api/v0/models` and 0.4's `/api/v1/models` (`--api`
  picks which), `/v1/chat/completions`, `/v1/embeddings`, `/api/v1/models/load`. Each request's model and page-image count go to `--log`.
- Its `--reviewer` modes, for the AI reviewer's requests: `agree` (default: agrees with every field and line), `disagree`
  (reads the grand total as 9,999.00, every time), `unsure` (never confident). See `reviewer_answer()`.
- fake_lm_studio.py `check --app URL [--log FILE]`: Settings > Reading names the chat model for GL coding and OvisOCR2 as
  page reader, the embedding model as not used; the log shows no request to the embedding model and none without a page
  image to OvisOCR2. Exit 1 with one line per problem; `check()` returns the list.
- old_versions.py `list | extract SHA FOLDER [--bundle] | old-wheelhouse FOLDER | install FOLDER --how HOW | start ... |
  seed FOLDER --how HOW --out BEFORE.json | unzip BUNDLE FOLDER | check BEFORE.json --new FOLDER --port N | scenario SHA ...`.
  HOW is `cli`, `by-hand`, `installer` or `launcher`; `VERSIONS` lists every old version (docs/UPGRADING.md explains each).
- `seed()` uses the old version with its own code (demo invoices, one approved with a GL fix, a vendor taught, an
  export batch, a setting, `.env` values); `check()` compares the new version's data, pages and files with BEFORE.json.

## Gotchas and decisions
- old_versions.py's `seed` imports keep the flat paths (`ap_coder.store`, `ap_coder.pipeline`...): they run inside an older
  version's own code, which has no `ap_coder/core` and the other folders (D-33). Never move them to the new paths.
- Both are standard library only at the top (`check` runs on any Python 3.10+); old_versions.py `install` and `start` download from
  PyPI (CI runs them with `AP_ALLOW_INTERNET=1`, the old launchers' opt-in); everything after the new bundle is unzipped runs offline.
- `scenario --strace` (Linux) records every connection: none may leave this computer.
- The model ids (`CHAT`, `READER`, `EMBED`) are LM Studio's real keys: AP Coder picks models by them, so keep them exact.
- Adding an old version: add it to `VERSIONS`, to docs/UPGRADING.md and to the matrix in `.github/workflows/ci.yml`.

## Files
- `fake_lm_studio.py`: a stand-in LM Studio (serve) and a check of which model AP Coder used for what (check).
- `old_versions.py`: older AP Coder versions installed, used and updated, then checked (the update tests).
