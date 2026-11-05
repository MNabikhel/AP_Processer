# Tests, benches and load testing: notes for the 36-hour goal

Repo /home/user/AP_Processer at main af9925b. Read only. Measured on this 4-core box, which six other readers were
also using, so the timings run slow.

## 0. The `ap_coder/bench/coding.py` changes: not mine

The file was changed at 01:12 and again at 01:26:08 UTC (stat). Each time a second `next_time` that calls the
removed `_ratio` was added at coding.py:780-830. I made no edit and ran no Write, `same_behaviour.py`, git stash,
git apply or checkout. Everything I ran was cat, sed, grep, wc, git log, git status, git diff, `pytest --collect-only`,
small pytest runs, `scripts/ci/perf_timing.py` and `bench speed`/`bench coding` (output in the scratchpad). My
`bench coding --quick` at about 01:28 ran *after* the change and failed with `NameError: _ratio`. That run and later
ones only refreshed `ap_coder/bench/__pycache__`. Other processes were running at the time: `.../scratchpad/ai_capture_prof/threads.py`
and `v10_idle.py` (another reader's). `git stash list` holds `stash@{0}: stale index from PR 44 state` (it could be
the source if someone applies it). The guards work: ruff finds 5 errors (F811/F821), and the default-tier test
`tests/ai/test_coding_no_ai.py::test_the_coding_benchmark_scores_line_tax_codes_and_lessons` fails.

## 1. Map

### Folders (collected with `-m ""` / default tier)
| folder | files | lines | all tests | default tier | measured s (test_times.json, full run) |
|---|---:|---:|---:|---:|---:|
| web | 50 | 19,352 | 1,243 | 855 | 199.6 |
| capture | 33 | 9,411 | 747 | 728 | 32.7 |
| coding | 30 | 7,410 | 723 | 711 | 20.7 |
| ai | 27 | 8,836 | 633 | 541 | 68.1 |
| erp | 30 | 6,298 | 381 | 339 | 35.9 |
| launcher | 16 | 5,673 | 359 | 304 | 49.5 |
| core | 24 | 4,142 | 288 | 275 | 19.0 |
| ops | 22 | 4,383 | 216 | 208 | 20.0 |
| intake | 7 | 1,961 | 125 | 117 | 9.9 |
| meta | 12 | 695 | 113 | 111 | 5.6 |
| offline | 4 | 1,487 | 64 | 49 | 41.4 |
| bench | 4 | 488 | 26 | 18 | 13.3 |
| **total** | | ~73k | **4,918** | **4,256** (662 deselected) | 515.7 |

Shared helpers at the top of tests/: conftest.py (502 lines), webclient.py (117), pdfs.py (36), scripts.py (19),
browser.py (61), fake_ais.py (467), offline_guard.py (190), offline_flow.py (634), summary_reference.py (612; the
pre-D-110 report code kept as an oracle for tests/erp/test_invoice_summary.py). Data in tests/fixtures/: test-only TLS
certs, page_reader/, and store_api.txt (279 names). There are 177 browser tests (`in_a_browser` in the name).

### Markers and tiers
- `pyproject.toml` `[tool.pytest.ini_options]`: `addopts = ["-m", "not slow"]`. The markers are `slow` and `upgrade`.
- `slow` is applied **by hand-kept list**: tests/slow_tests.txt (363 entries, "over half a second"), plus any test
  with `in_a_browser` in its name (tests/conftest.py:65-76). Slow entries by folder: web 132, ai 86, erp 40,
  launcher 25, capture 19, core 13, coding 11, offline 10, bench 8, intake 8, ops 7, meta 2.
  tests/meta/test_check_script.py:43 checks that every listed test still exists. **Nothing checks that a new test
  over 0.5 s gets listed** (see 5).
- `upgrade`: tests/launcher/test_update_from_old_versions.py runs only with `AP_UPGRADE_TESTS=1` (real old versions from PyPI).
- `--shard K/N` (conftest.py:61-101): whole files are spread by tests/test_times.json (228 files, measured 27 Oct)
  for the Windows CI split.
- Browser tests: the module-scoped `chromium` fixture (conftest.py:488). They skip without Playwright unless
  `AP_REQUIRE_BROWSER=1` (CI browser job).
- `collect_ignore_glob = ["test_*.py"]` at the top level (conftest.py:53, B-1411): stray files from older ZIPs never run.

### Fixtures (tests/conftest.py)
- Five autouse fixtures on every test:
  - `isolated_private_dir` (:151) sets `AP_PRIVATE_DIR` and `AP_USER_SETTINGS` to tmp, drops `AP_ALLOW_INTERNET`,
    and restores os.environ.
  - `fresh_reading_memory` (:165) clears module caches.
  - `no_dashboard_page_reader` (:178) stubs `page_worker.start_background` and `azure_check.start_background`.
  - `no_local_model` (:191) makes `local_llm._fetch_json` raise ConnectionRefused.
  - At module level, `sqlite3.connect` is patched to `PRAGMA synchronous=OFF` (:17-33) in the test process only.
- `store` (:202) is a new `Store(tmp_path/private/ap_coder.db)`. `client` (:211) is `client_for(store)`
  (webclient.py:28: `create_app(..., start_reader=False)` + TestClient on http://localhost, laptop mode).
- Session templates (:241-286): `demo_template`, `setup_demo_template` and `cost_centers_demo_template` are each
  built once (load_demo reads, codes and captures 10 PDFs, ~1.5 s). `copy_database` / `fill_from` (:289-313) copy one
  with `sqlite3.backup`, a few ms per test.
- Others: `offline_guard`, `reference`, `settings`, `ground_truth`, `sample_markdown_path`, `few_sql_variables`
  (999-variable limit), `FakeOpenAI` and `FakeDIClient` classes, `next_second()` (:109, waits for the clock's
  second to change).
- webclient.py: `PAGE` header, `post`, `enrol`, `authenticator_code` (works out the current TOTP code), `sign_in`
  (password then TOTP), `follow`, `draft_form`, `gt`, `known_vendors`, `add_clean`.
- Fakes: tests/fake_ais.py is a real HTTP(S) AIS server with scripted failures ("slow", "slow-after", "drop", status
  codes). The fake LM Studio exists three times: scripts/ci/fake_lm_studio.py (CLI, CI jobs),
  tests/ai/test_page_reader.py:78-110,440 and tests/ai/test_local_llm.py:69-84.

### How a test database is made
`Store(path)` creates the schema (`core/store/_base.py`, SCHEMA_VERSION 19 at :36, WAL :941, timeout=30 :419/:938).
Measured: new Store 18-28 ms, `load_sample_setup` 13-21 ms, `create_app` 5-26 ms, first `/review` 50 ms (450 ms
cold). Demo-backed tests copy a session template. No fixture is heavy. Note that 36 files define their own `store`
fixture and 11 their own `demo_db` alias (see 6).

### How long it takes
- CI (run 41778283520 on main, 4 Nov):
  - Ubuntu 3.12 full suite: **438.95 s, 4,689 passed / 229 skipped**.
  - Windows 3.11 default tier: **648.6 s (10.8 min)**.
  - Windows 3.12 shard 3/3: 417 s.
  - Browser job: ~6.7 min. Whole workflow: 13-15.5 min.
- Docs disagree:
  - CLAUDE.md says the default tier takes "a few minutes".
  - docs/HANDOFF.md:132 says "about 4,200, 6 to 9 minutes".
  - CLAUDE.md says the whole suite with browsers takes "~15 minutes". CI does it in 7.3 min on Ubuntu (with the
    browser tests in a separate job).
- Measured here: core + coding + erp default tier is 1,323 tests in 67 s, so the whole default tier is about 5-7 min
  on this box. Collection takes 23 s cold and 2.3 s warm.
- Serial only (no pytest-xdist; HANDOFF.md:137).
- **Disk:** pytest keeps the last 3 runs' tmp folders. My 1,323-test run left **332 MB** in /tmp/pytest-of-root, so a
  default-tier run leaves about 1 GB. On this box, with under 1 GB free, one default run can fill the disk. I removed
  only my own runs' folders.

## 2. Coverage by area

Well covered:
- Tax and checks (coding/: 723 tests).
- Capture (747), on generated PDFs (tests/pdfs.py `text_pdf`).
- Roles: tests/web/test_roles.py:70-115 checks that every route has a rule and runs every route for every role, but
  against a stub handler (`Probe`), not the real one.
- Security and hardening: test_server_security, test_server_hardening, test_server_https, test_server_two_step.
- JDE AIS failure modes (fake_ais, see below).
- Offline guarantees: tests/offline/, including a netns run.
- Install, update and launch: launcher/, plus big CI jobs.
- N+1 guards:
  - tests/web/test_web_review.py:256-341: the connections a page opens don't grow with rows.
  - tests/core/test_store_perf.py (12 tests): the plan uses an index (`EXPLAIN QUERY PLAN`), caches read once.
  - tests/web/test_web_app.py:40: one connection a request.

Thin, as asked:
- **Concurrency.** Only about 22 files start threads, and most races are *simulated* by monkeypatching a hook to run
  the second actor inside the first (tests/core/test_store_races.py:27-55 uses `purchasing._now` plus
  `time.sleep(0.5)`). Real parallel tests:
  - tests/web/test_server_security.py:220: 30 threads guessing passwords, behind a Barrier.
  - tests/web/test_web_finance.py:264: two undos at once.
  - tests/web/test_server_scale.py: `Slots` across processes :442-456; BackgroundReader with llm_parallel=3
    :470-503; Jobs across two `Jobs` objects :120-146.
  - tests/web/test_network_identity.py:182.
  - tests/erp/test_jde_post_safety.py (voucher being sent).

  Nothing approves, edits drafts (`/check`) and bulk-approves the same invoices from several threads or processes
  against one SQLite file. Nothing covers WAL plus the 30 s busy timeout under many writers, or B-1293's
  "database is locked" path. B-1293 is tested only with a fake `Locked` connection (test_server_scale.py:612-640).
- **Server mode with many users.** One real multi-process test: test_server_scale.py:227-283 starts
  `serve --server --workers 2`, checks /healthz, the access logs and the background lock, then stops. Every other
  multi-user test runs in one process with TestClient and calls one after another. The "two workers" tests
  (:110-213) are two app objects in one process. No test logs in tens of users and drives real pages over HTTP.
- **Failure modes.**
  - Model down: the autouse refusal (conftest.py:140-148). Also covered: cut-off answers
    (test_local_llm.py:679-843), the read cut off by the server (test_page_worker.py:721), "isn't answering"
    errors, and the LAN model address being allowed (test_local_llm.py:964).
  - Missing: a model server that is slow (tens of seconds) or **accepts and never answers**, a model swapped
    mid-run, the Mac Studio rebooting during a batch, LM Studio's concurrency limit below `llm_parallel`, and
    latency or jitter over a network.
  - AIS down: covered well (test_jde_sync.py:102,158; test_jde_ais.py:113-134; test_jde_post_safety.py:139,308;
    test_jde_api_web.py:94).
  - Disk full: `OSError(28)` is raised by monkeypatch in 7 files. One real limit: test_pilot_path_backend.py:25-42
    (RLIMIT_FSIZE, backup). Missing: a full disk during a database write in a request (SQLITE_FULL), and during WAL
    checkpoint or invoice-file copy at intake.
  - Corrupt database: only conftest's guard; no test opens a damaged DB through the app.
- **Migrations.**
  - tests/core/test_update_database.py checks the backup before an update, a too-new database being refused, and
    the version-check cost. Older schemas are *simulated* by dropping tables or columns from a fresh DB and setting
    `schema_version`: test_supplier.py:479 (13), test_reader_outcomes.py:82 (14), test_invoice_summary.py:297 (18),
    plus 6 `ALTER TABLE ... DROP COLUMN` tests.
  - Real old versions: the `upgrade` tests and the CI job update-over-older-install (scripts/ci/old_versions.py:60-69)
    only reach **schema ≤15 (PR #8)**.
  - Every pilot release (v0.3.0-pilot1..9) is schema 19, but columns were added without a version bump: for example
    users' `totp_*` via `ADDED_COLUMNS` (core/store/users.py:257-263, D-146, pilot8). No test opens a real
    pilot1-pilot8 database.
  - No test checks that "a migrated database equals a fresh one" (tables, columns, indexes, triggers).
- **Large data.** **No test has 100k invoices.**
  - The biggest invoice counts: tests/core/test_scale.py has 105-120 (all slow-marked); the N+1 test has 36.
  - 100k *events*: tests/erp/test_invoice_summary.py:345. 20k audit picks: test_supplier.py:462. 6,000-row
    histories: test_coding_no_ai.py:178.
  - scripts/ci/perf_timing.py builds 3,000 by default but is a manual stopwatch, run by no test or CI job.
  - The D-111 load test used 12,000 invoices. At 25,000 a month, a year is about 300k invoices and millions of
    events and feedback rows.

## 3. The load-test story

What exists:
- The "100 people on 4 cores" test is documented in **D-111** (docs/archive/decisions-2026-10.md:1319-1375; part 7
  "Measured" at :1365-1369) and docs/SERVER.md:50-60. Its setup: 12,000 invoices, logged-in people clicking every
  10 s on average through the queue, invoices, page images, approvals, search and exports, on a 4-core machine
  shared with another load. Results, **all from before "the data-layer work of the same release"**:
  - 1 worker: 25 people p50 6.9 s / p95 39 s; 50 people p50 45 s (22 of 119 requests over 120 s); 100 people 0.6 req/s.
  - 4 workers: 25 people p50 0.38 s / p95 1.5 s (2.1 req/s); 50 people p50 0.99 s / p95 5.9 s (2.9 req/s);
    100 people p50 6.9 s / p95 22 s (4.3 req/s, cores saturated).
  - Memory 2-5 GB. A stop took 32 s with 1 worker and 27 s with 4.
- It found real bugs: B-1271 (port check behind a proxy), B-1293 (session last-use "database is locked" leading to
  500s), B-1294 (approval re-OCR'd inside the request, 50-150 s under load), B-1295 (5 s keep-alive drops, now 75 s),
  B-1296 (stop order) (docs/archive/bugs-2026-10.md:1141-1148).
- **The harness was never committed.** There is no script, no Locust file and no bench subcommand. `git log --all`
  shows no deleted load file. It was never re-run after the data-layer work, so SERVER.md's tables are stale.
- What is in the repo:
  - scripts/ci/perf_timing.py: an in-process stopwatch. Measured now at 2,100 invoices: `/review` 0.118 s,
    `/review/{id}` 0.050 s, 50 `find_by_hash` 0.050 s, **bulk approve 100 = 3.1 s on one connection**.
  - tests/web/test_server_scale.py (the mechanics of workers, jobs, slots and stopping).
  - The N+1 guards.
  - tests/offline_flow.py: a stdlib HTTP walk through every page of a running `serve`, a good seed for user flows.
  - scripts/ci/fake_lm_studio.py: a ThreadingHTTPServer stand-in with no latency or concurrency option.
- Capacity facts that matter:
  - D-111: one process serves about 1.6 pages/s (GIL).
  - The worker count is capped at 8 (`ap_coder/web/server.py:223`, `max(1, min(cpu_count, 8))`).
  - At 400 people and one click every 10-20 s, the offered load is 20-40 req/s, plus pollers: job status every
    0.8 s (`static/js/app.js:109`), the reader panel every 20 s (`invoice.js:1281`), and `/review/{id}/check` 450 ms
    after each edit, which writes a draft row (`invoice.js:164-168`, `pages/invoice.py:154,228,770`).
  - At the 2025 figures, 8 workers is about 13 req/s, so either page cost drops 2-3x or the cap and the
    architecture change. The load test is what decides.
  - Login: scrypt N=2^14 (users.py:62) is 45 ms a hash, so a 400-person login storm costs about 18 CPU-seconds.
    It also needs TOTP (webclient.py:44-80 shows how to work out the codes).

What a proper harness should be (`python -m ap_coder.bench load`, stdlib plus httpx, already a dev dependency; not
Locust, which brings gevent and conflicts with the offline bundle and "no new dependencies"):
1. **Seed**: a builder like perf_timing's `build`, but with bulk SQL for volume. Sizes 12k, 100k and 300k invoices,
   each with a file, events and feedback history, vendors and vendor master, and an approval limit. Users in a role
   mix (≈70% clerk, 20% approver, 5% admin, 5% read-only) enrolled with known TOTP secrets.
2. **Fake model server on another address**: extend fake_lm_studio.py with `--latency 8-20 s a page`, `--slots N`
   (answers N at once, queues the rest, like a GPU), `--fail-rate`, `--hang-rate` (accept, never answer) and
   `--down-after`. Bind it to a non-loopback address (or add `tc netem` delay) so "over the network" is real.
3. **Server under test**: `python -m ap_coder serve --server --workers W --insecure-http` as a subprocess (as in
   test_server_scale.py:227), with its background process reading for real.
4. **Virtual users** (asyncio + httpx, or threads): each logs in (password then TOTP), then loops weighted scenarios
   with a think time (lognormal around 10 s for stress, 20 s nominal):
   - browse the queue (filters, pages);
   - open an invoice (`/review/{id}`, 1-3 `page/N.png`, `words`);
   - edit it (3-6 `/check`);
   - approve, park or reject;
   - approvers do second approvals;
   - search and export CSV/XLSX;
   - Insights, Spend and Month-end (heavy);
   - an occasional upload plus `/process/status` polling;
   - bulk-approve 50;
   - JDE batch create.
5. **Measure** for each route template: count, p50/p95/p99/max, status codes, and timeouts over 30 s. Also
   throughput; each process's CPU and RSS over time (/proc or `ps`); "database is locked" lines in the logs; the
   page-reader backlog (`page_reads_waiting`); and how long a stop takes.
6. **Check correctness after the run**:
   - no invoice approved or exported twice;
   - one approval event per approved invoice;
   - drafts not lost;
   - the summary table equal to a recomputation (tests/summary_reference.py);
   - the hourly upload allowance and the login lockout not tripped by honest users.
7. **Output** report.json and report.md as the other benches do, plus `--baseline FILE --max-regression 1.5`, which
   exits non-zero.
8. **CI smoke size**: 30 users × 90 s on 2 workers with the fake model. It fails on any 5xx, any "database is
   locked", any request over 30 s, or p95 over 2× baseline. The full size runs by hand on the target server.

**Targets that fit 300-400 users** (proposed; measured on the target hardware with a 300k-invoice database):
- At 400 users with a 10 s think time (≈40 req/s offered) and 20 s nominal:
  - page GETs: p50 ≤ 300 ms, p95 ≤ 1.5 s, p99 ≤ 3 s;
  - actions (approve, `/check`, park): p95 ≤ 1 s;
  - search and export: p95 ≤ 3 s;
  - bulk approve of 50: ≤ 5 s, and other people's writes wait ≤ 1 s meanwhile.
- 0 5xx, 0 "database is locked", errors < 0.1%.
- Memory per worker flat over 1 hour (no leak).
- Login storm: 400 logins in 5 min with p95 ≤ 2 s.
- The background reader keeps up with 25k invoices a month: about 1,200 a day and 1,800 pages, so at 15 s a page
  and llm_parallel 2 that is about 4 GPU-hours, and the backlog must drain within the day.
- A stop completes in ≤ 30 s under load.

## 4. Benches (ap_coder/bench/, 6,890 lines; CLI `__main__.py`, QUICK sizes at :21-28)
| bench | what it measures | time (docs / QUICK) | parallel |
|---|---|---|---|
| `run` (harness.py) | capture accuracy on generated invoices (fields, *verified* precision/coverage, no silent error, ECE) → report.json/md, failures.jsonl, evidence.jsonl | `--n 60` ~2.5 min; quick 24 ~1 min | spawn Pool (harness.py:152) |
| `generate` | the invoices plus truth only (generator.py, 1,663 lines; content.py, wording.py) | — | Pool |
| `calibrate` | fits capture/calibration.json from runs' evidence | seconds | — |
| `learn` (learning.py) | supplier learning in order: accuracy by invoice number, autonomy reached, touchless errors; wrong-approval and withdrawal what-ifs | 20×50 at 0.1 scans ~40 min; quick ~20 s | one supplier per process, **own DB each** |
| `lines` (lines.py) | line items right next time (D-120), GL following | 6×8 at 1.0 scans ~6 min; quick ~80 s | per supplier |
| `speed` (speed.py) | seconds per invoice through `InvoicePipeline.process`, on arrival and in the page-reader pass, with `--history N` | quick ~30 s. Measured: digital, 30k history: 0.20 s arrival, 0.12 s reader pass (n=6) | **sequential, one process** |
| `coding` (coding.py) | GL coding with no AI: accuracy, precision, coverage, first vs later invoice, tax codes, lessons; `--realistic`, `--ai` (real LM Studio) | quick ~5 s | single |
| `triage` (emails.py, msgwriter.py) | email sorting: invoices lost, kind and route right, bank-detail changes flagged | quick ~10 s | single |

- Bench tests: tests/bench/ (26 tests, 8 slow) plus tests/ai/test_coding_no_ai.py:286-305 (slow).
- They check that each bench runs, is deterministic, and gets the shape right. Only a weak floor is asserted
  (`precision >= 0.9`, test_coding_no_ai.py:297). **No accuracy baseline is gated in CI**: the numbers live in
  docs/CAPTURE_DESIGN.md:222-240,318-324,544 and docs/CODING.md and are re-measured by hand.
- No bench job in CI. `scripts/same_behaviour.py` (D-124) proves byte-identical behaviour with probes
  (tax/approve/read/pipeline) and is not run in CI.
- **Gaps for scale.** No throughput bench: `speed` times one invoice at a time in one process. Missing:
  - no N-worker intake (1,000 invoices end to end through `/process/upload` plus the background reader);
  - no page-reader throughput against a model with latency and slots;
  - no database-volume bench (store and page timings at 100k/300k);
  - no web load bench (section 3);
  - no backup or restore time at volume (a year is about 12 GB of files at 0.5 MB each, SERVER.md:47);
  - no memory or leak bench.

  `learn` and `lines` give each supplier its own database, so they never measure shared-database contention.

## 5. Test-suite health
- **Slowest** (CI Ubuntu `--durations=15`):
  - offline_guard serve 4.8 s / 4.3 s and in-process 2.8 s / 2.5 s;
  - test_page_reader deterministic scan 3.5 s;
  - modern ciphers 3.3 s;
  - launch_build old window 3.0 s, quiet step 2.4 s, older build 2.3 s;
  - supplier_repeat hash 3.0 s;
  - HTTPS start 2.3 s;
  - generator deterministic 2.3 s;
  - ai_paths self-test 2.25 s;
  - several workers 2.2 s;
  - template_rollback learning bench 2.1 s.

  Slowest files (test_times.json): offline_guard 35 s, local_llm 17 s, server_security 15 s, launch_update 14 s,
  server_hardening 14 s, web_analytics 13 s.
- **The slow list drifts.** Over 0.5 s here but *not* in slow_tests.txt (they run in the default tier):
  - test_server_scale.py: 6 tests (2.4, 1.3, 1.2, 1.06, 1.03, 0.9 s);
  - test_invoice_summary::test_the_year_has_every_state (setup 1.78 s);
  - test_jde_post_safety::test_the_queue_never_picks_up_a_voucher_being_sent (1.6 s);
  - test_users::test_thirty_failures... (1.4 s);
  - test_stamp iphone (1.0 s);
  - test_second_approval currency ×3 (1.0 s);
  - test_tax_rows_and_vendors setup (1.0 s);
  - test_exports_bulk setup (0.93 s);
  - test_insights...hundred_thousand (0.72 s).

  This box was loaded, so some may be under 0.5 s on CI, but nothing automates the list.
  The Windows default tier takes 10.8 min.
- **Duplication (tests):**
  - same names in several files: `test_pages_draw_on_an_empty_database`, `..._busy_database`,
    `test_empty_pages_say_so`, `test_a_post_without_the_page_header_is_refused`, `test_off_sends_nothing`,
    `test_settings_from_env` (2 each);
  - `test_web_review_signoff.py` / `signoff2.py`, `test_navigation_settings_help.py` / `_review.py` and
    `test_dashboard_round2.py` are sweep-shaped files.
- **Flaky patterns:**
  - 44 `time.sleep` in tests. Timing-dependent ones:
    - test_store_races.py:45 (sleep 0.5 to "let Bob run");
    - test_server_scale.py:132 (sleep 0.2, then assert another worker sees it running);
    - test_server_scale.py:499 (sleep 1.0, then assert 3 readers ran at once);
    - test_web_picker.py:481,492 (0.6 s route delays);
    - test_launch_build.py:382,388.
  - Ports: 4 copies of `free_port` (bind to 0, close, reuse: a small race; browser.py:19, test_server_https.py:30,
    test_server_scale.py:17, offline_flow.py:495). URL strings with 1234/8501 are text only.
  - Time: 53 uses of `date.today` / `datetime.now` / `time.time` in 19 files. `next_second()` is used 6 times.
    Most date logic injects `today=` (good). There are 15 hard-coded Nov-Dec 2026 dates; the ones I checked are
    relative or injected.
  - No per-test timeout (no pytest-timeout or `faulthandler_timeout`), and the CI Tests job has no
    `timeout-minutes`, so a hang costs up to 6 h of runner time.
- **Heavy fixtures:** none. The demo templates are built once a session. The real cost is the tmp left behind:
  about 1 GB for a default run, kept for 3 runs (pytest's default retention).
- **Implementation-shaped tests:**
  - tests/fixtures/store_api.txt plus tests/core/test_store_package.py:11 pin 279 old Store names (a compat shim
    that blocks slimming the Store API);
  - tests/meta/test_moved_modules.py pins old import paths;
  - 97 `monkeypatch.setattr` of private names (`local_llm._fetch_json` 11, `layout._ocr_cached` 7, `users._clock` 5...)
    and 54 private imports;
  - **81 files write SQL straight into SQLite** (`UPDATE invoices` 52 times, `UPDATE settings` 18, `UPDATE users` 15),
    which binds the tests to the SQLite schema (any move to another database or a schema split breaks them);
  - connection-count tests (good perf guards, but they count `sqlite3.connect`, not statements);
  - tests/meta/test_ci_workflow.py asserts the CI YAML's text.
- **Dead or rarely run:**
  - no dead slow-list entries (enforced);
  - 229 skips on Linux CI (platform and optional extras);
  - `upgrade` tests only in their CI job;
  - tests/ops/test_demo_day.py's live mode needs `AP_DEMO_DAY_URL`;
  - stray `if __name__ == "__main__": pytest.main` in the middle of test_server_scale.py:608 (tests follow it).

## 6. Helpers duplicated across test files
- `_gt` (ground truth loader): **13 copies**, the same as `tests.webclient.gt`. `SAMPLES / "ground_truth"` is read
  in 80 places.
- `store` fixture: **36** local definitions. 14 run `load_sample_setup`, 8 run `copy_database(demo_db, ...)`, and
  some use other paths (`ap.db`, `data/`). A `setup_store` and a `demo_store` fixture in conftest would replace most.
- `demo_db` alias: **11** files (each `return demo_template` or `cost_centers_demo_template`).
- `PAGE = {"X-AP-Coder": "1"}`: redefined in **21** files (webclient has it).
- `create_app(` built locally in **48** files.
- The server-mode helpers `account`, `app_for`, `client_of` and `log_in` live in *test file*
  tests/web/test_server_security.py:46-62 and are imported by 18 files. `form_of`, `check`, `clean_invoice` and
  `flash_text` live in tests/web/test_web_invoice.py:51-88 (41 imports). Helpers are also imported from test_jde
  (14), test_web_review_signoff (14), test_jde_gl_mode (8) and test_jde_post (7). **64 files import from other test
  files.** These belong in tests/serverclient.py and tests/invoiceform.py.
- `Clock` fixture: 3 copies (test_users.py:18, test_server_security.py:27, test_server_hardening.py:305).
- Fake LM Studio: 3 implementations (see 1).
- `follow`: webclient and test_web_finance.py:47.
- `free_port`: 4 copies. uvicorn-in-a-thread is built in 5 files.
- Frequent private helper names: `_invoice` 13, `_approve` 12, `_read` 11, `_codes` 9, `_coding` 7, `_capture` 7.

## 7. Scenarios worth testing
1. Two approvers final-approving the same invoice at once, in two processes (not simulated). Also a clerk editing
   (`/check` drafts) while another approves, and bulk-approve of 100 running while 50 people save drafts (the bulk
   path ran 3.1 s on one connection here).
2. 400 sessions: session last-use writes (B-1293) under real contention, and a login storm at 08:30 with TOTP.
   Behind the company proxy, does one honest person's typos lock the shared proxy address for everyone? The
   30-failures-from-one-address rule (test_users.py) plus `--proxy` (test_server_https.py:158-219).
3. Two people uploading at once: one import at a time across workers (D-111 (3)). Do 20 people's uploads queue
   fairly, does `/process/status` polling every 0.8 s by 50 people hurt, and does the hourly allowance hold per
   person?
4. The model server on the Mac Studio:
   - slow (60 s+), accepting but never answering, or a 503 storm;
   - `llm_parallel` set higher than LM Studio's *Max concurrent predictions*;
   - the Mac asleep or rebooting mid-batch, a different model loaded mid-day, DNS for its name failing;
   - the background reader restarting (5 s doubling to 60 s) while 1,000 reads wait.
   Reads must not be lost or duplicated, and the reader self-test must not run twice.
5. Morning backlog: 1,500 invoices dropped in one go, the page-reader queue drains in order, and the web stays
   within its p95 while the background process uses its cores.
6. A 300k-invoice database (one year) and 900k (three):
   - the queue, search, Insights, Spend, Month-end, exports and vendor detail;
   - the summary triggers' write cost;
   - the FTS rebuild time;
   - the time of the daily backup, of a restore while people work, and of a WAL checkpoint under load;
   - VACUUM.
7. A disk filling during a real write (SQLITE_FULL in a request, WAL growth), and during the daily backup with 12 GB
   of files.
8. Upgrades: a real database from each pilot release (pilot1, pilot5, pilot8, handoff9) and from schemas 15-18,
   opened by HEAD. Checks: every page renders, an approval works, the schema matches a fresh database's, and a
   restore of the pre-update backup works.
9. Mixed versions during a server update: a worker on the old code and one on the new during `ops/server_update.py`.
   Also a restore under way while the workers serve (`<db>.restoring`).
10. Clock problems: a worker's clock skewed vs another's (leases at 30 s, TOTP steps), DST changes, month end at
    midnight with people still approving.
11. Memory: a worker over 8 hours (draft caches, `_waiting`, reference-data caches, JDE answers kept 5 min); the
    caches' invalidation stamps read from the database at 40 req/s.
12. Every GET route under each role on the *real* handler (not `Probe`) against a busy database: no 500s.

## 8. Recommended goal items
| # | item | benefit | size | proof |
|---|---|---|---|---|
| 1 | **Load harness** `python -m ap_coder.bench load` (section 3: seeding, fake model with latency and slots, multi-worker server, role-mixed TOTP users, p50/p95/p99 by route, correctness check after the run, baseline gate) | the 300-400 user claim measured, not guessed; finds B-1293-style bugs | L | 400 users × 15 min report against the targets; SERVER.md tables re-measured |
| 2 | **CI load smoke** (30 users × 90 s, 2 workers, fake model) in the Ubuntu job | every change checked for 5xx, locking and >2× p95 regressions | M | the job fails on a planted slow query or a planted lock |
| 3 | **Volume tier**: a session template of 100k invoices (bulk SQL) plus `@pytest.mark.scale` tests: time and statement bounds for the hot pages, and `EXPLAIN QUERY PLAN` has no full `SCAN invoices/events` on hot paths | the 25k/month × years data path stays fast | M | tier passes in < 2 min; a dropped index makes it fail |
| 4 | **Real concurrency tests**: N threads or processes on one database for approve/final-approve, drafts plus bulk approve, export batches, sessions; replace the sleep-based simulations (test_store_races.py:45) | races caught by tests, not by people | M | run 50× in a loop with no flake; a planted missing `BEGIN IMMEDIATE` fails |
| 5 | **Golden upgrade databases** from tagged releases (a script builds them; checked in small or built in CI) plus a "migrated equals fresh" schema test (tables, columns, indexes, triggers) | safe schema work at scale; covers the column-only migrations | M | test fails when a column is added without a migration |
| 6 | **Fake model server with failure modes** (one shared module replacing the 3 fakes: latency, slots, hang, 5xx, model swap, down-after) and tests of the reader and reviewer against it | the Mac-Studio-over-the-network risks covered | M | new tests: a hung model never blocks the web; reads resume with no duplicates |
| 7 | **Faster feedback**: make the suite xdist-safe and run `-n auto` (or `--shard` locally); `tmp_path_retention_policy = "failed"`; `faulthandler_timeout` plus a CI `timeout-minutes` | default tier from 5-10 min to ~2-3 min; no disk fill; hangs end in minutes | S-M | default tier time before and after; a run leaves < 50 MB of tmp |
| 8 | **Auto-maintained slow tier**: mark slow from measured durations (a check fails when a default-tier test takes > 0.5 s on CI, or regenerate slow_tests.txt and test_times.json from `--junitxml`) | the default tier stays fast | S | meta test plus a CI step |
| 9 | **Test helper clean-up**: tests/serverclient.py (`account`, `app_for`, `client_of`, `log_in`, `Clock`), tests/invoiceform.py (`form_of`, `check`, ...), conftest `setup_store` / `demo_store` / `gt`, one `free_port`; remove the 64 test-to-test imports, 13 `_gt`, 21 `PAGE` and 11 `demo_db` copies | less code and easier refactors | M | `same_behaviour.py` plus the full suite green; test LOC down; `grep "from tests\.[a-z]*\.test_"` returns 0 |
| 10 | **Store test seams**: helpers for the 81 files that `UPDATE` tables directly (for example `store.force_status`, a `fixtures.set_column`) | the database layer can change (split, pool, other engine) without rewriting tests | M | the count of raw SQL in tests falls; the suite stays green |
| 11 | **Bench regression gate**: commit baseline JSONs for `run --quick`, `learn --quick`, `coding --quick`, `triage --quick`, `speed`; a CI job compares (accuracy within ε, speed within 1.5×) | accuracy and speed regressions caught on every PR | S-M | a planted reader bug fails the gate |
| 12 | **Throughput bench**: `speed --workers N` / an intake bench (1,000 invoices through upload plus the background reader with the fake model) reporting invoices/hour and backlog drain | sizes cores and GPU for 25k/month | M | report on the target server; SERVER.md estimates replaced by measurements |
| 13 | Real-handler route sweep: every GET for every role on a busy database (extend tests/web/test_roles.py) | no 500s on any page, any role | S | the test lists 0 failures |
| 14 | Docs: one stated time for the default tier (CLAUDE.md "a few minutes" vs HANDOFF "6 to 9" vs Windows 10.8 min); SERVER.md capacity tables marked as pre-data-layer until re-measured | fewer wrong expectations | S | doc diff |
