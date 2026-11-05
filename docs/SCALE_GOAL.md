# Goal: AP Coder ready for 400 people and 25,000 invoices a month (measured, clean, efficient; 36+ hours)

## Why
AP Coder works today for one person on a laptop, or about 50 people on a small server. Stage 2 needs 300–400 people
at once and about 25,000 invoices a month (1,250 a day, 300,000 a year):
- AP Coder on one Windows VM, 32 vCPU and 64 GB;
- the AI models on a Mac Studio over the company network, with the Mac mini as backup;
- the pilot itself on a Mac mini (macOS).

The codebase digest found what stands in the way: `docs/archive/scale-2026-11/DIGEST.md` and the seven notes beside it
(read on main at af9925b, 5 Nov 2026). Progress and every measurement: `docs/SCALE.md`.
- One web process serves about 1.6 pages a second, and the server stops at 8 processes.
- Some pages and checks redo work for the whole queue, or read every file ever received.
- Imports run one at a time inside a web process.
- The JD Edwards syncs stop at 100,000 rows.
- There's no failover to the backup model machine, and no macOS service.

This goal makes the code ready for that load, proves it with measurements, and leaves it cleaner than it found it.

## Decisions already made (the user, 5 Nov 2026)
- **PR #92: keep the docs fix, drop the release.**
  - In phase 0, on `claude/epic-feynman-r6ns86`, revert the release-labels commit (c847e97). Use a revert commit, no
    force-push.
  - Set `docs/HANDOFF.md`'s release names back to pilot9 and handoff9. Keep the break-even wording in
    `PILOT_PLAN.md` and its D-147 entry.
  - The goal's first PR carries this.
- **Merging:** merge each phase's PR into main with a merge commit once every CI check passes, and report each merge.
- **Job cost coding** is a separate goal later.
- **Releases:** no new release or tag during or at the end of this goal. The package and its handoff9 kit stay
  exactly as sent.

## Done means (each measured, before and after, in `docs/SCALE.md`)
1. **Load: 100 simulated people on this 4-core machine meet the targets.**
   - **The setup:** a 300,000-invoice database, the fake model server, and the real `serve --server` with several
     workers.
   - **The targets:** pages p50 ≤ 300 ms, p95 ≤ 1.5 s, p99 ≤ 3 s; actions (approve, check, park) p95 ≤ 1 s; zero
     errors, zero "database is locked"; memory flat over an hour.
   - **Why 100 here:** it is twice the per-core load of 400 people on the 32-vCPU VM. The VM figure is a projection
     until it is measured on the real server in stage 2.
   - **Stretch:** 200 people here.
2. **Volume:** every page, search, report and export on the 300,000-invoice database (1M+ events, 600k JDE
   register rows):
   - pages under 300 ms for one person;
   - reports and exports under 3 s, or run as a background job with progress;
   - no full-table scans on hot paths, guarded by tests.
3. **Throughput:**
   - **A morning drop:** 1,250 invoices dropped at once (half of them scans) are read and in the review queue within
     the working day on this machine, with web p95 unchanged while it runs.
   - **The model machine misbehaves:** killed, hung, slow or swapped. No read is lost or done twice, and failover to
     the backup takes under a minute.
4. **JD Edwards at volume** (against the AIS stand-in):
   - the nightly sync of 30,000 suppliers and 600,000 vouchers succeeds, and the next night reads only the changes;
   - 500 vouchers post in one run despite slow and late answers;
   - the duplicate check stays right next to a supplier with 20,000 vouchers;
   - fiscal periods are respected.
5. **Operations:**
   - **macOS service:** proven in the macOS CI job (start at boot, back after a crash, stays stopped when stopped).
   - **A readiness check** reports the disk, the certificate, the backup's age, the background process and the queue
     depth.
   - **Updates:** under 5 minutes of downtime, and safe if the power fails halfway.
   - **Backups:** pruned, with a free-space check.
6. **Cleaner:**
   - no SQL outside `core/store`;
   - transactions that nest;
   - the long functions and duplicated helpers named in the notes merged or split;
   - meta tests so none of it comes back;
   - a PostgreSQL seam, so a later move takes weeks, not months.
7. **Bugs:** every bug the digest reproduced is fixed test-first, with its line in `docs/BUGS.md`. There are about 20,
   listed in phase 1.
8. **Nothing worse:** laptop mode unchanged; accuracy benches the same or better; `scripts/same_behaviour.py`
   identical for every refactor; the full suite and CI green on main after every phase.
9. **Docs current:**
   - `SERVER.md`'s capacity tables re-measured;
   - a macOS section;
   - the VM-plus-model-server layout;
   - `HANDOFF.md`, `PILOT_PLAN.md` and `KNOWN_LIMITS.md` matching today's plan (no company names in the repo);
   - one stated test time.

## How every phase runs (the loop, and the bug checks in between)
1. **Plan:** write the phase's items, their proofs, and a pre-mortem in the plan file. The pre-mortem asks "what could
   go wrong, which scenario haven't we thought of": two people at once, a restart halfway, a full disk, a slow model,
   the clock, a big supplier, an old database.
2. **Build:** in `developer` agents, in parallel worktrees where the files don't overlap.
   - Bug fixes are test-first.
   - Significant choices get a D-148+ entry.
   - Leave each file you touch cleaner.
3. **Bug check:** a fresh reviewer agent, which didn't write the code, hunts bugs in the phase's diff. Every finding
   is verified before it is fixed.
4. **Prove:**
   - `python scripts/check.py`, the default tier, and the browser tests for the areas touched;
   - `same_behaviour.py` for refactors;
   - the accuracy benches `--quick`;
   - the load and volume measurements, recorded in `docs/SCALE.md`.
5. **Ship:** one PR per phase from `claude/epic-feynman-r6ns86`. Watch CI to green (fix, never skip). Merge with a
   merge commit, then start the next phase from main.
6. **Checkpoint:**
   - walk the app end to end in server mode as each role, plus a quick laptop-mode walk;
   - re-measure;
   - update the plan with what was learned and any new scenarios;
   - write a short status for the user.

## Phases (about 44 hours of work; the order matters: measure, then fix, then re-measure)
**0. Measure first (≈4 h).**
- Research and write down how to scale on this stack, with sources, as a D- entry:
  - uvicorn on Windows (spawn, no fork, no uvloop);
  - SQLite under many writers (WAL, `busy_timeout`, `synchronous`, checkpoints);
  - process pools on Windows and macOS;
  - when PostgreSQL pays.
- Build `python -m ap_coder.bench load`:
  - a seeded 12k, 100k or 300k database;
  - users across the roles, with authenticator codes;
  - the real multi-worker server;
  - p50/p95/p99 per route;
  - a correctness check after the run;
  - a baseline gate.
- One shared fake model server, replacing the three, with delay, slots, hangs, errors, a model swap and
  down-after.
- A "server day" throughput bench and a volume tier of tests.
- Record the baseline. Keep disk use small: keep only failed tests' temp folders, and add timeouts.

**1. The verified bugs (≈4 h), each test-first:**
- the manager double answer;
- `session_user` waiting 30 s behind a writer;
- a model server named by company DNS refused;
- the stale `host.json` keeping the service down;
- `server-update` trusting `server.json`'s task name;
- two pick-ups storing one file twice;
- the controls report missing older periods;
- the Activity CSV capped at 5,000;
- the queue's view lost and loaded twice;
- edits lost on session expiry;
- server-mode wording;
- the 24-hour clock;
- polls keeping idle sessions alive;
- one big supplier switching the duplicate check off;
- `parse_amount`;
- the missing indexes and `find_vendor`'s scan.

**2. The request path (≈7 h).**
- **Database foundations:**
  - nested transactions (`begin_write`);
  - connections reused, with tuned settings;
  - the `last_seen` write off the request path;
  - one transaction per approval.
- **Pages:**
  - `GET /review` reads stored checks only (re-checks run in the background, and vendor notes no longer invalidate
    them);
  - no folder hashing on `/process`;
  - no second check when an invoice opens;
  - nothing blocking the event loop (with a meta test).
- **Network:**
  - versioned static files cached for good, and gzip;
  - vendor and account lists out of the invoice HTML;
  - the coding-rules options;
  - polling that pauses, backs off and stops on logout, with small job polls;
  - the model-server status probed by the background process.
- **Limits:**
  - paging on every list, and Activity filtered in SQL;
  - per-person limits on heavy endpoints;
  - a sized thread pool;
  - the 8-worker cap replaced by a measured default.
- Re-measure.

**3. The background work (≈7 h).**
- A persistent import queue in the database: it survives restarts, is fair between people, and has priorities.
- Capture in a process pool outside the web workers.
- Chat calls off the arrival path, with short timeouts.
- An OCR and page-image cache looked up before drawing, with no PNG round-trips (a scan from about 15 s to 9 s, a
  re-read under 1 s).
- Hash each file once.
- The learning memory updated, not rebuilt, after each approval.
- Queues with a heartbeat on reads in progress, no reviewer starvation, and a correct wait estimate.
- Caches sized and pruned.
- Pre-rendered page images in one shared cache.
- Re-measure with the server-day bench.

**4. Model machine and JD Edwards (≈6 h).**
- **The model machine:**
  - a primary and a backup URL for each model, with a circuit breaker;
  - a self-test per server;
  - a new chat model qualified before it can approve anything.
- **JDE:**
  - incremental syncs with no page ceiling, in short transactions;
  - one signed-in AIS client per worker;
  - posting that keeps going past a late voucher, with progress (plus a spec for a batch-level orchestration for the
    JDE team);
  - fiscal periods and cut-off;
  - accruals that count unposted vouchers;
  - JDE holds re-checked before posting;
  - Z-table keys unique per installation;
  - `held_vendor` and `account_for` without N+1.
- **Tax:** one Decimal half-up arithmetic, and one tolerance table for the Tax check, the validation checks and the
  splits.

**5. Many people on one queue (≈4 h).**
- "Ann is working on this" on the invoice and the queue, and Next skips invoices someone else has open.
- A stale screen turns read-only once, with "approved by X".
- A revision number, so two tabs can't silently overwrite each other.
- An expired session keeps the work and comes back to the same page.
- Bulk approval as a background job with progress.
- Fetch timeouts.

**6. Operations (≈6 h).**
- **The macOS service:**
  - a LaunchDaemon backend, SIGTERM handling and a stable `current` link;
  - a recipe for the LM Studio daemon;
  - a FileVault note for IT;
  - a macOS CI job.
- **Health and monitoring:** `/readyz`, `doctor --json` and `service status --json`, with Event Log and syslog
  entries for failures.
- **Backups:** a time window, retention, the store on another volume, and free-space checks.
- **`server-update`:**
  - works with either service;
  - prepares before it stops, and runs the self-check once;
  - records an in-progress marker;
  - takes under 5 minutes of downtime, proven by a Windows CI job.
- **Server safety:**
  - maintenance mode during a restore;
  - a check for a network-share data folder;
  - no silent new database;
  - a business time zone;
  - a certificate expiry warning.
- **The bundles:** reproducible, plus a small server-only bundle.

**7. Clean and maintainable (≈4 h).**
- **The PostgreSQL seam:** no SQL or `_conn` outside `core/store`, a small dialect layer and numbered migrations.
- `core` no longer importing upward.
- The long functions split, and the duplicated JS and test helpers merged.
- One amount and date parser.
- Test health:
  - real concurrency tests in place of sleeps;
  - upgrade databases from real past releases, plus a "migrated equals fresh" check;
  - a slow list kept from measured times;
  - CI time limits on every job, and the bundle built once;
  - a load smoke test in CI.

**8. Final proof (≈3 h).**
- The full load test and the server-day run against the baseline.
- End-to-end walks as each role, and laptop mode.
- The full suite and CI.
- The docs updated.
- A before-and-after report for the user.

## Rules that always hold
- **Offline:** fully offline at run time; no new runtime dependency without a D- entry; no company data or company
  names in the repo (the repo is public).
- **Laptop mode stays as it is.** The pilot runs on it until the server is set up.
- **Verify before acting:** every agent's finding is checked in the code before it is acted on, and nothing is
  claimed that wasn't measured.
- **Keep work safe:**
  - commit and push after each item;
  - keep the plan file current, so a summary of the conversation loses nothing;
  - keep the disk under control (about 2 GB free).
- **Don't fall behind:** if an item takes far longer than planned, write down why, and decide to split it, simplify
  it or move it later. Don't stall the goal on one item.

## Not in this goal (on purpose)
- **The PostgreSQL port itself.** The seam is in; the port, or SQL Server, comes when high availability is needed.
- **Multi-company:** a design note only.
- **Single sign-on.**
- **Job cost coding:** its own goal, once the company's job, cost code and cost type structure is known.
- **Releases and tags:** none (see the decisions above).
- **Anything that needs the real hardware or JDE:** the Mac Studio's real speed, the Mac mini burn-in and the DV
  environment. They are measured in stage 0 and stage 2.

## If time is left
- Paging the finance hub's reads and making them truly incremental;
- a spend-workbook job;
- the systemd backend;
- a licence report;
- the accessibility fixes;
- more of the clean-up list in the notes.
