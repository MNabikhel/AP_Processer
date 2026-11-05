# AP Coder: ops/, intake/, scripts/, CI, dependencies (reader 5 of 7)

Repo `/home/user/AP_Processer` at `af9925b`. Nothing in the repo was changed. The reproductions are scratch scripts in
`scratchpad/` (`repro_host.py`, `repro_task_name.py`,
`repro_double_pickup.py`, `measure_backup.py`). Run each with `.venv/bin/python -I <file>`.

Sizes: `ap_coder/ops` 6,503 lines, `ap_coder/intake` 1,902, `scripts/*.py` 6,073 plus `scripts/ci` 1,280 and
`scripts/probes` 612, `ci.yml` 971, `release.yml` 267.

---------------------------------------------------------------------------------------------------------------------

## 1. Map

### ap_coder/ops
| Module | Lines | What it does |
|---|---|---|
| `cli.py` | 1140 | `python -m ap_coder <cmd>`. `build_parser` (136-316) defines 20 commands; `main` (1098-1136) is the dispatch table. It holds the bodies of `serve` (`cmd_serve` 347-394), `doctor`, `process`, `watch` (`new_files` 517, `still_copying` 539, `watch_once` 564, `_watch` 650), `read-pages`, `extract`, `labels`, `evaluate`, `share-report`, `export-training`, `pilot-score`, `demo`, `users` (963-1026), `hub` and `schema`. `running_note` (621-642) writes `.ap_coder_worker-<pid>.json` so the launcher can find watch and read-pages processes. Admin commands (`server-update`, `backup`, and `service` other than `run`) read no `.env` (1104-1107, D-109). |
| `service.py` | 934 | The Windows "service" (D-100). It is a Task Scheduler task, not an SCM service. Covers server.json settings (`settings` 192-215, `ENV_SETTINGS` 58-60), `run.cmd` (299-314), the task XML (329-371), the service host `run_host` (615-675), install/start/stop/remove/status (681-797), the doctor's server lines (823-861) and argparse (864-934). Standard library only. |
| `server_update.py` | 686 | `server-update <bundle.zip> --sha256` and `--back` (D-100, D-101, D-109, D-143). It checks the bundle (94-131), unzips it beside the current folder (143-152), copies the DB without migrating it (158-171), puts back a backup while keeping the accounts (174-199), wraps the Windows service steps in `Server` (232-298), refuses an untrusted folder (309-332), checks the SHA-256 (335-357), and does the update (`_update` 408-546) and the rollback (`go_back` 552-654). |
| `backup_cmd.py` | 134 | `backup now|list|check|restore [--from]` for IT (D-143). |
| `doctor.py` | 503 | `doctor`: packages, Python, OCR, data folder, server lines, LM Studio, page reader, reference data and tax mapping (`run_checks` 113-286). `store_checks` (289-347) covers accounts, JDE and the hub. Secrets are scrubbed (52-74). |
| `offline.py` | 437 | Offline guard (`internet_allowed`), OCR model presence and SHA-256 (`missing_models`, `ppocrv5_ready`), table-finder model, ONNX telemetry off, `server_mode()`, `free_port` (334-360), `listening`, and the doctor's offline lines (386-437). |
| `help.py` | 1060 | 122 `CheckHelp` entries, FAQ, routine, quick start, server variants, what's new. Content only. |
| `pilot_score.py` | 969 | `pilot-score`: compares the approved invoices with an E1 F0411/F0911 export. `score` is 145 lines. |
| `share_report.py` | 264 | A redacted summary of `process` outputs (pre-dashboard workflow). |
| `demo.py` | 249 | Loads and removes the demo invoices. |
| `jde_cmd.py` | 79 | `jde test|sync|status [--retry]`. |

### ap_coder/intake
| Module | Lines | What it does |
|---|---|---|
| `mailbox.py` | 521 | Unpacks `.eml`/`.msg` dropped in the invoices folder. Attachments go next to the email, statements to `statements/`, the rest to `emails/needs a person/`. The email moves to `emails/` and one line is appended to `emails/triage.jsonl` (`unpack` 434-471, `unpack_folder` 507-521, `triage_log` 474-498). It is heavily bounded against hostile input (sizes, depth, parts). |
| `msgfile.py` | 365 | A standard-library reader for MS-CFB/MS-OXMSG (.msg), bounded. |
| `triage.py` | 408 | Sorts each email into invoice, credit, statement, reminder, bank_change or not_for_ap (EN and FR). "An invoice is never lost." |
| `inbox.py` | 132 | Turns the triage log into the dashboard's lists (`needs_person`, `statements`) and actions (`to_queue`, `mark_done`, `log_sorted`). |
| `pictures.py` | 474 | Turns a picture into a PDF (EXIF upright, HEIC re-encoded, multi-page TIFF up to 200 frames, phone photo flattened). The original moves to `originals/`. |

### scripts/
| Script | Lines | What it does |
|---|---|---|
| `launch.py` | 2393 | The double-click launcher (laptop). Two phases (system Python, then .venv). Lock `.ap_coder_launch.lock` (1895-1925), take-over of older running copies (`running.take_over`), pip from the wheelhouse, data folder settle, OCR models, desktop shortcut (1515-1537), readiness summary, self-check (1785-1805), clean-up of older installs, start of `serve` (2033-2089). Its log is `~/.ap_coder/logs/launch.log`, kept once past 1 MB (181-237). It has 150+ functions. |
| `running.py` | 483 | Finds running AP Coders (server file, `/healthz`, port owner, 0.1 Streamlit signature, worker notes) and stops older ones (`take_over` 442-483). |
| `upgrade.py` | 956 | Laptop update over older installs: record of installs, `prune_stale`, data-folder move, repointing DB file paths, `clean_up`. `server_update` borrows its `rmtree`, `base_python` and `is_install`. |
| `install.py` | 541 | `--installer`: the launcher's setup plus questions (data dir, Azure, shortcut, self-test). |
| `build_offline_bundle.py` | 410 | Builds the offline ZIP (code, `wheelhouse/` per platform and Python, `models/`, `bundle_manifest.json`, `.sha256`). No constraints file (see 5). |
| `fetch_models.py` | 285 | Copies or checks the OCR models (downloads only with `AP_ALLOW_INTERNET=1`). |
| `check_deps.py` | 149 | Which pyproject requirements are missing or too old (standard library plus pip's packaging). |
| `first_run.py` | 50 | Only `choose_data_dir` is used (launch.py:1276). `main()` alone duplicates launcher steps (see 6). |
| `pilot_check.py` | 105 | Self-check: reads the samples with no LLM and no Azure into a temporary DB and compares with ground truth, plus one OCR scan. |
| `check.py` | 136 | Dev quick check: ruff on changed files, then the tests mapped to them. |
| `same_behaviour.py` | 131 | D-124 harness: git worktree of a ref, runs `scripts/probes/{tax,approve,read,pipeline}.py` on both trees and compares byte for byte. |
| `make_sample_pdf.py` | 396 | Dev tool that regenerates `samples/`. |
| `ci/old_versions.py` | 824 | Installs, seeds and checks old versions for the update jobs. |
| `ci/fake_lm_studio.py` | 260 | An LM Studio stand-in for CI. |
| `ci/perf_timing.py` | 132 | Stopwatch for heavy pages on a 3,000-invoice DB. |
| `ci/release_key.py` | 64 | D-130 release-check key. |
| `terminal.bat`, `demo.bat` | 18, 20 | A prompt with .venv on PATH, and a demo with its own data folder. |
| `APProcessor.bat` / `.command` | 146 / 165 | Find a Python 3.11 or newer (winget offer only from GitHub's ZIP), then run `scripts/launch.py`. The `.command` re-execs bash 3.2 and skips Apple's python stub. |

---------------------------------------------------------------------------------------------------------------------

## 2. The service story

### 2.1 How the Windows service works today (service.py)
- **A scheduled task, not an SCM service** (docstring 1-22, D-100). The reason: `sc.exe create python` dies after 30 s, and
  a ctypes service host fails from a venv's redirector `python.exe`.
- **Task XML** (`task_xml` 329-371):
  - triggers: `BootTrigger` with a 1-minute delay, and a `TimeTrigger` repeating every 5 minutes for ever;
  - `MultipleInstancesPolicy=IgnoreNew`, `ExecutionTimeLimit=PT0S`, `RestartOnFailure` 1 min × 999,
    `RunLevel=LeastPrivilege`;
  - principal: `APCoderService` with LogonType Password (schtasks asks for the password, `create_command` 374-380), or a
    built-in SID (`BUILT_IN` 62-65).
- **`run.cmd`** (299-314) sets `AP_PRIVATE_DIR` and `AP_SERVER_MODE=1`, `cd`s into the code folder and runs
  `.venv\Scripts\python.exe -m ap_coder service run`. `server-update` rewrites only this file to switch folders
  (`switch_to` server_update.py:370-374), so the task and its stored password never change.
- **Service host** (`run_host` 615-675):
  1. Exits at once if the `stopped` flag exists (623-624) or if `host.json` names a live PID (625-628).
  2. Writes `service/host.json` and logs to `logs/service.log`. The log is a `RotatingFileHandler` of 5 MB × 5
     (553-566).
  3. Starts `serve --server --no-browser --host --port [--certfile --keyfile] [--proxy] --workers N` (252-263). N is
     `server.json` workers, else `min(cpu, 8)` (`web/server.py:221-223`).
  4. Pipes the child's stdout and stderr into service.log (657-658).
  5. Restarts the dashboard with backoff of 5 s doubling to 300 s, reset after 600 s of steady running (66, 663-670).
  6. Re-reads the settings at each start.
  7. On Windows it puts the child in a kill-on-close job object (572-603).
- **stop** (691-707) writes the `stopped` flag, then `POST /shutdown` with the token from `.ap_coder_server.json`, waits
  45 s, then `taskkill /T /F` only if the image is this folder's Python (`runs_python_of` 459-474, B-1500). After that
  it runs `schtasks /end` and kills the host. **start** (681-688) removes the flag and runs `schtasks /run`.
- **status** (776-797) shows the task state from `schtasks /query /v /fo LIST`, `host.json`, `/healthz` over
  127.0.0.1 without a proxy (certificate not verified: `_local` 496-507, `dashboard_health` 514-525), and the stop flag.
- **Off Windows** (`main` 890-897) only `service run` and `install --dry-run` work. Everything else prints "The
  service is for Windows (Task Scheduler)". `doctor_checks` says SKIP (849-851).
- **Not Windows-specific**: `run_host` already runs anywhere. A macOS or Linux supervisor can call
  `python -m ap_coder service run` and get restart-with-backoff and a rotating service.log for free.

### 2.2 What a macOS launchd service needs (new work, size M)
- **Plist location.** Use `/Library/LaunchDaemons/ca.<company>.apcoder.plist` (root:wheel 644), a LaunchDaemon with
  `UserName`. A LaunchAgent needs a GUI login, and the Mac mini must serve after reboot with nobody logged in. Keys:
  - `Label`;
  - `ProgramArguments` = [`<folder>/.venv/bin/python`, `-m`, `ap_coder`, `service`, `run`];
  - `WorkingDirectory` = the code folder;
  - `EnvironmentVariables` = `AP_PRIVATE_DIR`, `AP_SERVER_MODE=1`, `PYTHONUNBUFFERED=1`, `LANG=en_US.UTF-8`;
  - `UserName`/`GroupName` = a dedicated hidden user (`_apcoder`, made with `sysadminctl`/`dscl`; no login shell, home =
    data folder);
  - `RunAtLoad` true;
  - `KeepAlive` = `{SuccessfulExit: false}`. `run_host` returns 0 when stopped on purpose, so launchd must not restart
    it then; a crash exits non-zero and is restarted;
  - `ThrottleInterval` 10, `ExitTimeOut` 60 (more than the 25 s grace plus the background's 15 s);
  - `StandardOutPath`/`StandardErrorPath` = `<data>/logs/launchd.out|err`, for host crashes before its logger starts.
    Nothing rotates these files. Keep them tiny, or add `newsyslog.d` rules.
  - `SoftResourceLimits` `NumberOfFiles` 4096. The default of 256 is low for 8 uvicorn workers plus SQLite plus
    sockets.
- **The `run.cmd` indirection.** A plist names the folder directly, so `server-update` would rewrite the plist (needs
  root) or point at a stable symlink `/usr/local/apcoder/current` → `APProcessor-<ver>`. The symlink is simpler: swap
  it atomically and kickstart.
- **Commands.**
  - install: `launchctl bootstrap system <plist>`
  - start: remove the flag, then `launchctl kickstart -k system/<label>`
  - stop: write the flag, `/shutdown`, then `launchctl kill TERM system/<label>`
  - remove: `launchctl bootout system/<label>`
  - status: `launchctl print system/<label>`, which gives state, pid and last exit code.
- **Graceful stop.** Install a SIGTERM handler in `run_host`. Today Python's default SIGTERM kills the host at once:
  no `finally`, `host.json` is left behind (see 7.1), and the child gets only launchd's process-group kill.
  The handler should set the stop intent, `POST /shutdown` or SIGTERM the child, and wait. Also put the child in its
  own process group or use launchd's group kill on purpose. `tie_to_host` has no POSIX equivalent (on Linux use
  `prctl(PR_SET_PDEATHSIG)`; on macOS rely on launchd's process-group kill).
- **`process_image` is Windows-only** (435-456 returns "" elsewhere), so `runs_python_of` is always False off Windows.
  As a result `service stop` would never end a stuck dashboard or host on macOS. Implement it with
  `libproc.proc_pidpath` (ctypes) on macOS and `/proc/<pid>/exe` on Linux.
- **LM Studio headless.** Use `lms`/llmster as a second LaunchDaemon with `UserName` = the account whose
  `~/.lmstudio/models` holds the models (as in the release.yml use of `lms daemon up`, `lms server start --port 1234`,
  `lms load <model> --context-length 16384`). LM Studio's own "run on login" is a login item: useless at boot without
  a login. Things to test: that Metal works from a daemon context with no GUI session (it is expected to, but not
  verified here), and that LM Studio's auto-update is off. Order: `run_host` does not need LM Studio up. The app
  degrades: invoices wait and a banner shows.
- **Power and boot settings** (IT's runbook, plus a doctor check):
  - `sudo pmset -a sleep 0 disksleep 0 autorestart 1 womp 1`, and "Start up automatically after a power failure";
  - automatic macOS updates off or scheduled;
  - FileVault (see 9);
  - firewall: allow incoming for the venv's real python binary. A venv's python is a symlink to the framework binary,
    so the macOS app firewall prompt and TCC grants attach to that real binary.
- **TCC.** A daemon writing to `~/Documents`, `~/Desktop`, `~/Downloads` or a network volume (the second backup copy
  on an SMB share) is blocked unless the real python binary has Full Disk Access or Network Volumes access. Keep the
  data folder outside those places (`/Users/Shared/APCoderData` or `/opt/apcoder/data`).
- **server-update on macOS is not usable today.** `Server.installed()` returns False off Windows
  (server_update.py:241-245), so it does not stop a running dashboard. It would migrate the DB while a launchd-kept
  old dashboard may restart onto it. `switch_to` writes a `run.cmd` nobody reads. The final message says "service
  install", which refuses on macOS. A launchd backend is needed for `Server.installed/stop/start/up` too.
- **Testability.** GitHub macOS runners allow `sudo launchctl bootstrap system`, so a CI job can install the plist,
  check `/healthz`, kill -9 the dashboard and see it come back, `service stop` and check it stays down, and simulate a
  reboot by booting the job out and back in.

### 2.3 Linux systemd: worth adding?
Not needed for the pilot (macOS) or for stage 2 (Windows). Still worth a size-S item for two reasons. A unit file is
about 15 lines (`ExecStart=<venv>/bin/python -m ap_coder service run`, `Restart=on-failure`, `User=apcoder`,
`KillMode=mixed`, `TimeoutStopSec=60`, `Environment=AP_PRIVATE_DIR=...`). Ubuntu CI runners can then test the service
host end to end (start, crash-restart, stop, stale record) on every push, which catches host regressions for all
three OSs. Implement it as a third backend behind the same `install/start/stop/status` interface as the launchd one.

### 2.4 Health checks
- `/healthz` (`web/app.py:303-323`) is liveness only. It always says `{"ok": true}`; to this computer it also gives
  version, build, folder, pid, port and user. It does not touch the DB, the background process, the disk, LM Studio or
  the backups. A load balancer or monitor would see "ok" with the DB locked, the disk full or the background dead.
- `service status` gives the task state, host pid and `/healthz`. There is no JSON output, no backup age, no disk.
- `doctor` is the deep check (packages, OCR, data folder writable, server HTTPS files readable, names, task, accounts,
  JDE, hub, LM Studio). There is no disk-free check, no certificate expiry, no last-backup age or status (it exists in
  the DB as `backup_status`, backups.py:37), no DB integrity check, no page-reader queue depth, no clock check. There
  is also no `--json`. Verified: `grep -rn "disk_usage|notAfter|integrity_check|quick_check" ap_coder` finds nothing.
- Proposal: `/readyz` (or `/healthz?deep=1`, local or token only) and `doctor --json`. Return 200 or 503 with checks:
  - DB `SELECT 1` within 1 s;
  - the background process's heartbeat age (it already loops every 30 s, server.py:518-520: write a heartbeat row or
    file);
  - last backup age under 26 h and `backup_status` empty;
  - free disk above a threshold (data volume and backup volume);
  - certificate days left (`ssl._ssl._test_decode_cert(path)["notAfter"]`, standard library);
  - LM Studio reachable and the page-reader queue length (`store.page_reads_waiting()`).

### 2.5 Log rotation
- `logs/service.log`: 5 MB × 5 (host, single writer). Fine.
- `logs/access.log`: 10 MB × 9, one file per worker (`web/security.py:300-305`). At 400 users this is about 100 B a
  line and 0.5-2 M requests a day, so 50-200 MB a day: 90 MB of history is only hours. Make the size configurable,
  or rotate by time with `TimedRotatingFileHandler` at midnight and keep 30-90 days for audit.
- `~/.ap_coder/logs/launch.log`: rotated once at 1 MB (launch.py:181-209). Fine.
- Unrotated or unbounded:
  - launchd's or systemd's stdout files (new);
  - `emails/triage.jsonl`, which only grows (mailbox.py:428-431) and is read whole (see 3.6);
  - `service/history` in server.json, capped at 20 (server_update.py:54). Fine.
  - the page-reader cache `.cache/page_reader/<digest>/p<page>-*.json` (`ai/page_reader.py:931-965`), never pruned;
  - the Azure extraction cache, never pruned (Azure only).
  Caches are not in backups.

### 2.6 Monitoring hooks for IT (IT): none exist today
- No Windows Event Log entries. SCOM and most RMM tools watch the Event Log. Without pywin32,
  `eventcreate /L APPLICATION /SO "AP Coder" /T ERROR /ID 100 /D "..."` works, or ctypes `ReportEventW`. Hook these
  events: the dashboard restart loop (run_host 665), a backup failed, the second copy failed, the disk low, a
  certificate expiring, an update or rollback result.
- macOS: `logging.handlers.SysLogHandler(address="/var/run/syslog")`, or `logger -t apcoder`, into the unified log.
- No metrics endpoint. Options are a Prometheus text `/metrics` (local or token only): requests and latency per route
  from the AccessLog middleware, queue depths, page reads waiting, last backup age, DB size, worker restarts. Or a
  JSON status file written every 30 s by the background process, which any agent can read.
- No outbound alerting, by design (offline). The SMTP relay inside the company network would fit the D-104 "named
  internal system" pattern if wanted.

---------------------------------------------------------------------------------------------------------------------

## 3. Running at scale (stage 2: 32 vCPU / 64 GB Windows VM, 300-400 people, about 25,000 invoices a month, about 300k a year)

### 3.1 How serve picks workers and threads
- **Service.** `serve_command` passes `--workers s.workers or default_workers()` = `min(os.cpu_count(), 8)`
  (service.py:261-263, web/server.py:221-223). On the 32-vCPU VM that is 8 web workers. `settings()` caps workers at
  64 (service.py:208-212).
- **`serve --server` by hand** defaults to 1 worker unless `--workers` or `AP_SERVER_WORKERS` (cli.py:387-392). The
  help text says so. A launchd or systemd unit calling `serve` directly instead of `service run` gets 1 worker.
- **Multi-worker** (`_serve_workers` web/server.py:279-319): uvicorn's `Multiprocess` supervisor with spawn and one
  shared socket, `timeout_worker_healthcheck=30`. One `serve --background-work` process takes the OS lock
  `.ap_coder_background.lock` (server.py:491-530) for the page reader, AI reviewer, Azure, JDE, daily backup and hub.
  `llm_parallel` is shared across processes through slot files (`AP_LLM_SLOTS` env).
- **Threads.** Each worker uses anyio's default threadpool (40 threads) for the sync `def` handlers. There is no
  `limit_concurrency` and no backlog setting (grep finds none).
- **Measured** (docs/SERVER.md §1): 12k invoices on 4 cores, 4 workers, 100 people at once gave a median of 6.9 s and
  a p95 of 22 s with the CPU saturated. D-111 sized the design for about 500 accounts, about 50 at once, and
  5-10k invoices a month. Stage 2 is 2.5-5× that on volume and up to 8× on users. There is no load-test script in the
  repo (grep "locust|load test" finds only the doc). `scripts/ci/perf_timing.py` builds only 3,000 invoices.
- **Several uvicorn workers?** Yes, that already works and is tested (`tests/web/test_server_scale.py:227`). The
  limit is SQLite's single writer across 8 processes plus the background process. More workers than 8 will mostly
  queue on the DB write lock. Raising the cap to 16 for 32 vCPU is a cheap experiment once a load test exists.

### 3.2 Backup duration and size at about 300k invoices a year
Facts from the code:
- **Daily backup** = a full SQLite online copy (`backup_to` backups.py:55-85, `source.backup(target)` in one step),
  then `PRAGMA journal_mode=DELETE`, then a full SHA-256 of the copy (`snapshot` → `_sha_size`, backup_files.py:383).
  Then a files snapshot: it walks `invoices/`, `emails/` and `reference/` (`data_files` 236-246), stat()s every file,
  re-hashes only changed ones (size and mtime), copies new content to `backups/files/<2>/<sha>`, and writes
  `<backup>.files.json` listing every file (337-388).
- **Retention.** 14 daily copies are kept (`drop_old` 107-117). Labelled backups (`manual`, `before-restore`,
  `before-update-<v>`, `before-update-from-schema-<n>` made by the Store's own migration, and `before-going-back`) are
  never pruned (`DAILY` regex backups.py:36).
- **Timing.** Once a day, at service start and then at least 20 h later, on a background thread (`DailyBackup`
  app.py:74-114, `auto_backup` min_hours=20). The time of day drifts with restarts. There is no backup window, so a
  30 GB copy can land at 10:00 with 400 users.

Measurement (`measure_backup.py`, 400 synthetic invoices without OCR or page-read data):
- DB size: 8.5 KB per invoice, a lower bound.
- Backup time with nothing changed: 0.19 s.
- `.files.json` size: about 300 bytes per file.

Extrapolated (estimates):
| | per year at 300k invoices |
|---|---|
| Invoice files (docs: "about 0.5 MB each") | ~150 GB in `invoices/`, plus `originals/`, emails and attachments |
| Backup file store (`backups/files`, content-addressed, same volume) | ~150 GB more: a second copy on the same disk, so no protection against disk loss unless the second folder is set |
| DB | 3-30 GB (8.5 KB a row is a floor; real rows carry OCR text, page reads, events, learning) |
| 14 daily DB copies | 14 × DB = 40-400 GB |
| `.files.json` per backup | ~90-120 MB each (300-400k files) × 14+ |
| Labelled DB copies | 2 per update (`before-update-<v>` from server-update step 3 and `before-update-from-schema-<n>` from the migration), never pruned: 12 updates a year × 2 × DB = 24 × DB |
| Daily backup, nothing new | DB copy plus SHA-256 at about 300-500 MB/s, i.e. 1-2 min for 10 GB and 3-6 min for 30 GB, plus a stat of 400k files and the JSON (about 1-3 min) |
| First backup on a new server, or the first copy to the share | every file: 150 GB at 100-200 MB/s is 15-25 min locally and longer to an SMB share |

docs/SERVER.md §1 says "60 GB free, better 200 GB". That is enough only for the pilot. Stage 2 needs about 1 TB on
the data volume for the first year, retention rules for labelled backups, and the second backup folder on another
volume or server. CRA requires keeping books for 6 years, so invoices cannot simply be pruned.

### 3.3 Backups while running
- These are safe while running: the SQLite online backup with WAL keeps writers going, and the backup runs in the
  background process. `exclusive()` (backup_files.py:78-104) prevents a backup, a restore and an update put-back from
  running at once across processes (B-1559).
- One long read transaction during a 30 GB copy keeps the WAL from checkpointing, so the `-wal` file grows for those
  minutes. Acceptable.
- The I/O contention at business hours is the issue (3.2). Add a "backup at 02:00" setting and run the
  `.files.json` work incrementally.
- The server's own backup tool (Veeam, Windows Server Backup) must not copy the live `ap_coder.db` (documented,
  SERVER.md §9). A VSS-aware hook (pre-freeze `backup now`) would make IT's tooling work with AP Coder.

### 3.4 Update and rollback with 400 people logged in
Sequence (`_update` 408-546):
1. stop: `/shutdown`, a 25 s grace, up to 45 s wait;
2. `backup_now`: a full DB copy plus the files snapshot;
3. unzip the 1-2 GB bundle (after `private_copy` already copied the whole ZIP into `%TEMP%`, server_update.py:202-207);
4. `set_up`: the new folder's `launch.py --no-start --yes`. This makes a .venv and installs every wheel, a few
   minutes. It **also runs the self-check itself**, because `first=True` in a new folder (launch.py:2220-2226);
5. `migrate`: `Store(db)` makes its own `before-update-from-schema` full DB copy first (backup_files.py:391-397);
6. `self_check` again (server_update.py:287-298, timeout 900 s);
7. start, waiting up to 180 s.

Estimated downtime at stage-2 size: 10-25 min. That is 2 or 3 full DB copies, venv creation and pip, two self-checks
and the migration.

Sessions are in the DB, so a successful update keeps people logged in. In-flight requests get 25 s and are then cut.
There is no notice to users before the stop: no maintenance banner exists (grep "maintenance" finds nothing). A
rollback (`put_back`) logs everyone out (D-101).

Easy wins:
- Do the unzip, `set_up` and first self-check **before** stopping the service. They don't touch the live DB. This
  needs a launcher flag that skips `running.take_over`, which would otherwise judge the running service "other"
  (running.py:454-457) and refuse.
- Drop one of the two self-checks.
- Skip the duplicate pre-migration copy when server-update already made one.
- Pre-flight free-space check: the ZIP copy, unzip, venv and 2 × DB must fit before step 1.
- A scheduled maintenance banner ("AP Coder restarts at 18:00 for an update").

Together these bring downtime to about the backup plus the migration plus the start (2-6 min).

Rollback (`back()` 470-491 and `go_back`) is careful: covers B-1474, B-1565, Ctrl+C and full disks. But see 7.3: a
power loss mid-update leaves the service stopped with no record of the half-done update.

### 3.5 The intake inbox with hundreds of emails a day
How emails arrive (docs/MAILBOX_SETUP.md): Power Automate saves `.eml` to SharePoint, OneDrive syncs it to a library,
and a scheduled `robocopy /MOV` every 5 min moves it into `invoices/`. AP Coder never touches the mailbox. Pick-up
happens either by a person (Import → Process, `POST /process/folder`, web/pages/process.py:932-955) or by a separate
scheduled `watch --once` that IT must create by hand. The service does not pick up the folder on its own. The
background process does not either (server.py:491-530).

- **Polling.** `watch` checks every `--every` seconds (default 60, cli.py:209). Each check:
  1. `unpack_folder` reads each email whose mtime is more than 5 s old (mailbox.py:507-521);
  2. `new_files` lists the **whole flat invoices folder** and, for **every** file, reads it fully and SHA-256s it,
     then calls `store.find_by_hash`, which **reads and hashes the file again** (cli.py:526-533,
     store/invoices.py:122-131).
  Processed invoices are never moved out of `invoices/`. So each check reads every invoice ever received, twice. At
  2,000 invoices of 0.5 MB that is 2 GB read a minute; at 300k it is 300 GB a minute, which is impossible.
  `POST /process/folder` is worse: `not_processed` (one read and hash) plus `split_new` (two more) on every file, in
  the request (process.py:171-181, 629-640, 937-941).
  **This is the biggest scale bug in my area.** Fix: keep a pick-up ledger (path, size, mtime) → sha, or move
  processed files into `invoices/YYYY/MM/`, and look only at files new since the last check.
- **Flat folder.** `invoices/` will hold 300k+ files plus `originals/`. Windows Explorer becomes unusable,
  `iterdir`/`stat` slow down, and `data_files` walks it at each backup. Use dated subfolders.
- **Locking.** No lock between pick-ups. Between `watch` and `watch`, Task Scheduler's IgnoreNew helps only for the
  same task. Between `watch --once` and the dashboard button there is nothing. Verified with `repro_double_pickup.py`:
  two pick-ups that look before either stores the invoice both take it, and the DB stores 2 rows with the same
  `file_sha256` (no UNIQUE constraint; `_base.py` has none on `file_sha256`). The race window is the whole batch
  (hash every file first, then OCR each for seconds to minutes). The second copy then shows as a possible duplicate,
  which is noise for AP. Fix: an OS lock (`paths.try_lock`, already used for the background process) around a
  pick-up, and a unique index or check on `file_sha256` at insert.
- **Two processes and one email.** Both read the email. Attachments are deduplicated by name and content (`_save`
  392-403). The second `_move_to` fails because the file is gone, so the email is "left in place" and not logged
  (mailbox.py:364-367, 463-466). The result is harmless: one triage line and duplicate attachments collapse.
- **Duplicates across emails.** The same invoice sent twice is caught by content hash. A re-sent invoice with
  different bytes is caught by `find_duplicates` on vendor and number.
- **Settling.** `SETTLE_SECONDS=5` on mtime. Suspected: a copier that sets the source's old mtime on the target
  (robocopy copies timestamps by default) can defeat the settle check while the file is still being written. On
  Windows the open handle usually blocks the move, which narrows the window. A truncated `.eml` could still yield a
  truncated attachment saved and processed, then the full one later as `_1`. Low likelihood. Robocopy into a staging
  folder plus a rename avoids it.
- **The triage log is read whole.** `inbox.needs_person` reads the whole log on every Import-page view
  (`triage_log(folder, 1_000_000, keep=...)`, inbox.py:45, mailbox.py:479-485). `entry()`/`file_of()` do the same for
  each action. At 300-500 emails a day of about 1-2 KB a line, that is 100-300 MB a year read per page view. Move the
  triage log into the DB with an index on "open".

---------------------------------------------------------------------------------------------------------------------

## 4. CI

### 4.1 Jobs, with estimated durations (no run logs read; estimates from the steps and the comments)
| Job (ci.yml line) | Cells | Est. wall time each | Notes |
|---|---|---|---|
| `tests` (17-50) | 12: Ubuntu 3.11-3.14 whole suite; Windows 3.12+ocr ×3 shards; Windows 3.14 ×3 shards; Windows 3.11 and 3.13 fast tier | Ubuntu ~6-8 min ("about 5 minutes" plus install); Windows shards ~10-15; fast tier ~10 | pip cache on |
| `lint` (52-73) | 1 | ~3 min | ruff, plus pip-audit of `.[ocr,azure]` |
| `browser-tests` (75-95) | 1 | ~8-10 min | Playwright plus Chromium `--with-deps` downloaded every run |
| `windows-installer` (97-168) | 1 | ~8-12 min | Installs from PyPI, pilot_check, serve, `/healthz`, fresh start |
| `windows-pilot-launcher` (170-281) | 1 | ~12-18 min | First double-click (online), second, later, doctor, pilot_check |
| `windows-github-zip` (283-403) | 1, timeout 30 | ~15-25 min | Two setups from GitHub's ZIP, an update over a running copy |
| `windows-offline-bundle` (405-531) | 1 | ~15-20 min | **Builds a bundle**; offline install, demo.bat, offline guard plus demo day |
| `macos-offline-bundle` (533-628) | 1 | ~10-15 min | **Builds a bundle** |
| `mac-pilot-update` (630-844) | 1, timeout 25 | ~20-25 min | **Builds a bundle**; 5 scenarios |
| `update-over-older-install` (846-972) | 9 (3 OS × 3 old versions), timeout 30 | ~10-25 min | **Each cell builds its own bundle** (883) |

That is 29 jobs a push or PR. Wall time is about 25-30 min (the longest Windows and Mac jobs). Billed time is roughly
75 min Ubuntu, 200+ min Windows and 75 min macOS. On private repos macOS bills 10× and Windows 2×, so macOS and
Windows dominate.

`release.yml`: `checked` (~2 min) → `pilot-laptop` (60-90 min, timeout 350: LM Studio and the models on CPU) → `tags`
(~2 min). It runs only when the workflow or the tag list changes, and reuses a passed check by key (D-130).

### 4.2 Duplication
- **12 offline-bundle builds a run** (windows-offline 1, macos-offline 1, mac-pilot-update 1, update-over-older 9).
  One build job per target OS, uploading an artifact that the others download, saves about 9 builds.
- **Four Windows first-time setups** (installer, pilot-launcher, github-zip, offline-bundle), each running pip,
  pilot_check and the dashboard start. The installer and pilot-launcher jobs overlap most.
- `pilot_check.py` runs in 4 jobs. A bundle job also runs the test suite's offline guard.
- Windows 3.12 and 3.14 both run the whole suite in 3 shards (6 cells). The "fast tier" on 3.11 and 3.13 adds 2 more.
  The whole suite on 3.12 plus the fast tier on 3.14 would save about 3 Windows cells. Keep 3.14 whole if the pilot
  Mac runs 3.14 (it does: mac jobs).
- No `actions/cache` for the bundle's wheelhouse (`build_offline_bundle.py` reuses wheels only within its own stage
  folder) or for Playwright's browser.

### 4.3 Could be faster
- Build the bundles once, as artifacts.
- Cache the wheelhouse keyed on `hashFiles('pyproject.toml')` plus the date.
- Cache Playwright.
- Run the Windows unit-test shards only on 3.12 (and fast tier elsewhere).
- Use path filters: docs-only PRs skip the launcher, bundle and update jobs.
- Only 3 of 13 jobs set `timeout-minutes` (292, 640, 859). The others default to 360 min, so a hung Windows launcher
  burns 6 h. Add timeouts everywhere (and a meta test).

### 4.4 Missing
- **A service test that runs the service.** `tests/ops/test_service.py:296-311` registers and removes the task on the
  Windows runner with `--no-start` only. Nothing starts the task, checks `/healthz`, kills the dashboard to see the
  host restart it, checks that `service stop` stays stopped across a "reboot" (end and run), or checks the stale-record
  case. Size S-M on Windows; also needed for launchd (macOS runner) and systemd (Ubuntu).
- **server-update end to end on Windows** with a real bundle: install the service from bundle N-1, run
  `server-update` with bundle N and `--sha256`, then `--back`, checking the data and the users. Today all of it is
  unit-tested with a fake `Server` (`tests/ops/test_server_update.py`). Size M.
- **A load test.** The SERVER.md §1 numbers were measured once, by hand. Add a script (locust-free: asyncio plus httpx,
  already in dev) for N logged-in users with a mix of pages, on a generated 300k-invoice DB. Run it in a scheduled
  workflow (weekly, not per PR) and fail if p95 regresses. Size M.
- **Scale tests of intake and backup** at 100k files (new_files, snapshot, triage log). Today only 3k invoices
  (perf_timing).
- **macOS service test**: none, since there is no service yet.
- **PostgreSQL**: the app is SQLite only (D-09). There is no abstraction, so a PostgreSQL test is not applicable until
  a decision is made. SQLite has been measured only to 12k invoices and 100 users. Stage 2 is the point to decide (a
  load test first).
- No dependabot or renovate, no CodeQL or secret scanning, no SBOM or licence report (HANDOFF.md:542 lists the licence
  report as to do).

---------------------------------------------------------------------------------------------------------------------

## 5. Dependencies (pyproject.toml)
- **Only floors, almost no ceilings.**
  - `pandas>=2.1` (tested 3.1.0), `openai>=1.68` (tested 3.41.0), `pillow>=13`, `pymupdf>=1.24` (tested 2.0),
    `pydantic>=2.6`;
  - ceilings exist only on `fastapi<1`, `uvicorn<1`, `jinja2<4`, `python-multipart<1`, `rapidocr<3.5`.
  `docs/tested-packages.txt` (69 pins, 29 Oct 2026) is a constraints file, but **neither CI nor
  `build_offline_bundle.py` uses it**. So every bundle build resolves "latest that day". Two builds of the same commit
  can differ, and the bundle that passed CI is not necessarily the one IT builds. Fix: build with
  `-c docs/tested-packages.txt`, or generate a per-target lock with hashes (`pip download --require-hashes`), and have
  a scheduled CI job try the unpinned latest so that drift is caught early. Size S-M.
- **Risky.**
  - **PyMuPDF (`pymupdf`, `pymupdf4llm`, `pymupdf_layout`) is AGPL-3.0 or commercial.** That needs legal review before
    serving 300-400 staff over the network (AGPL §13).
  - `openai` 1.x → 3.x was a major-version span accepted by the floor.
  - `opencv-python` (non-headless, pulled by rapidocr): about 40-60 MB a platform, with GUI libraries. Linux needs
    libGL. `opencv-python-headless` would do if rapidocr allows it.
  - `onnxruntime` has no Intel-Mac wheel for 3.14 (noted in the builder).
  - `rapidocr-onnxruntime` is installed past its `python_requires` (launch.py:1114-1133, `IGNORE_REQUIRES_PYTHON`).
    That is fragile.
  - `certifi>=2026.10.19` is pinned up for an advisory. Good.
  - pip-audit runs in CI. Good.
- **Heavy optional parts.** OCR (`rapidocr`, `onnxruntime`, `opencv`, `shapely`, `pyclipper`, `numpy`), roughly
  100+ MB a platform and Python. `azure` (identity plus DI) is small. `dev` (pytest, ruff, httpx) is shipped in every
  bundle because the offline guard tests run from it.
- **Offline bundle size** (estimated, not measured here): default targets are win_amd64 plus the build machine, ×
  4 Pythons, × `ocr`, `dev` and `azure`. Wheels are roughly 120-150 MB a Python and platform, so about 0.5-0.6 GB for
  Windows alone. `--mac` adds 2 platforms × 4 Pythons, about 1-1.5 GB. The release zip (release.yml:103) is 4
  Pythons. SERVER.md §1 says 2-3 GB installed per version, with two versions kept. **A server profile** (one Python,
  one platform, no `dev`) would be about 150-200 MB and unzip in seconds, which shortens update downtime (3.4).

---------------------------------------------------------------------------------------------------------------------

## 6. Cleanliness

### 6.1 Long functions (lines)
- `cli.build_parser` 181 (cli.py:136)
- `doctor.run_checks` 174 (doctor.py:113)
- `pilot_score.score` 145 (594)
- `server_update._update` 139 (408)
- `pilot_score.render_markdown` 107 (783)
- `share_report.build_share_report` 106 (64)
- `server_update.go_back` 103 (552)
- `pilot_score.posted_vouchers` 86 (196)
- `build_offline_bundle.build` 85 (291)
- `install.main` 84 (454)
- `launch._ensure_packages` 82 (1189)
- `mailbox.read_eml` 80 (273)
- `upgrade.clean_up` 75 (882)
- `launch.settle_and_check` 74 (2185)
- `msgfile.__init__` 68
- `cli.cmd_users` 64
- `upgrade._repoint_database` 61
- `service.run_host` 61
- `cli.cmd_process` 61

`scripts/launch.py` is one 2,393-line module.

### 6.2 Duplication between scripts/ and ops/ (and web/)
- **Is a PID alive?** Three versions with different semantics:
  - `service.process_alive` (service.py:397-421) returns False when OpenProcess is denied;
  - `ai/page_worker._pid_alive` (649-) returns True on access denied;
  - `running.worker_running` (running.py:215) checks the command line.
- **Ending a process:** `service.terminate` (477-485) and `running.terminate` (385-393).
- **A local HTTP call without a proxy:** `service._local` (496-507) and `running._open`/`health` (86-100).
- **`SERVER_FILE = ".ap_coder_server.json"`** is defined 3 times (service.py:56, web/server.py:42, running.py:49) plus
  `backup_cmd.py:25`.
- **Stopping through the token:** `service.dashboard_stopped` and `running.ask_to_stop`/`stop`.
- **Self-check runner and its output parsing:** `launch.self_check` (1785-1805) and `server_update.Server.self_check`
  (287-298). Both run during one update (3.4).
- **The venv's Python:** `service.folder_python` and `upgrade.venv_python`/`base_python`.
- **Folder pick-up logic, twice:** `cli.new_files`/`still_copying` and `web/pages/process.not_processed`/`split_new`/
  `still_being_saved`. `SETTLE_SECONDS` is defined twice (cli.py:514, mailbox.py:47). One `intake/pickup.py` with a
  lock would serve both.

### 6.3 Dead or legacy
- `scripts/first_run.py:main` is unused by the launcher; only `choose_data_dir` is used.
- CLI commands from the pre-dashboard and Azure era:
  - `extract` needs Azure DI, so it is dead in the offline build;
  - `schema` (Azure OpenAI schema);
  - `labels`, `evaluate` and `share-report` (they work on `process -o` outputs, not on the DB);
  - `process --extraction-model/--deployment/--model-name/--vision`.
  Check with the bench and docs owners before removing them.
- AP Coder 0.1 (Streamlit) support: `running.LEGACY_SIGNATURE`, `legacy_dashboard`, streamlit process detection, and
  `upgrade.adopt_legacy`/`legacy_installs`. Needed only for the oldest pilot laptops, and dead weight on a fresh
  server.

---------------------------------------------------------------------------------------------------------------------

## 7. Possible bugs

### 7.1 VERIFIED: a stale `host.json` blocks the service host when its PID is reused
`run_host` returns 0 without starting anything when `host.json` names any live process (service.py:625-628). It
checks only `process_alive`, not `runs_python_of`.

`host.json` is removed only in `finally` (671-674). That never runs when the host is killed:
- Task Scheduler's `/end` or a shutdown uses TerminateProcess;
- launchd and systemd send SIGTERM, and Python's default disposition kills without `finally`.
So after a reboot or power loss the record is always stale.

`repro_host.py` writes `host.json` with pid 1 (always alive). Result: `run_host returned 0 ; dashboard start
attempts: 0` (and `runs_python_of(1) = False`).

On Windows a reused PID after reboot is possible. On macOS and Linux, boot-time PIDs are low and sequential, so reuse
is likely. Every 5-minute retry then exits too, and the service stays down until that process ends.
`tests/ops/test_service.py:277-282` codifies the current behaviour.

Fix: replace the PID record check with an OS lock (`paths.try_lock(service_dir/host.lock)`, which the background
process already uses, web/server.py:507), or also require `runs_python_of` plus a creation time later than
`started`. The same applies to `.ap_coder_server.json`: a stale one with a reused PID makes `backup restore` refuse
(backup_cmd.py:37-45) and makes `dashboard_stopped` wait 45 s then report failure, which blocks `server-update`
("The service did not stop").

### 7.2 VERIFIED: server-update takes the task name from server.json (against B-1500)
`service.main` never takes `account`, `task_name` or `folder` from server.json (service.py:914-918, B-1500).
`server_update.Server` does: it uses `service.settings(data)` (server_update.py:238, 415, 555). So the administrator's
`schtasks /query`, `/end` and `/run` name whatever task the service account wrote. `repro_task_name.py` with
`task_name="\Microsoft\Windows\Defrag\ScheduledDefrag"` printed:
- `schtasks /query /tn \Microsoft\Windows\Defrag\ScheduledDefrag`
- `/end ...`
- `/run ...`

The folder is protected (`wrong_folder`); the task is not. Low-to-medium severity: it triggers another task with its
own principal. Fix: build `Server`'s settings with `task_name=TASK_NAME` (or an `--task-name` argument) as
`service.main` does. Add a test like test_service.py:192.

### 7.3 VERIFIED BY READING: a power loss mid-update leaves the service stopped with no trace
`service.stop` writes the `stopped` flag (service.py:693-697). `_update` records `history` only at the end or in
`back()`. After a crash between steps 2 and 8, the task starts the host after reboot, the host sees `stopped` and
exits (623-624), so nothing runs. `service status` says "Stopped on purpose". If the crash came after `migrate` and
before `switch_to`, the old code would face a migrated DB. Fix: write `update_in_progress` (step, backup, new folder)
to server.json at start and clear it at the end. `status`, `doctor` and `server-update` should report it and offer to
resume or roll back.

### 7.4 VERIFIED: two folder pick-ups store one file twice
See 3.5 and `repro_double_pickup.py` ("picked up by each: 1 1 → invoices stored: 2, same file hash: True").

### 7.5 VERIFIED BY READING: pick-up and dashboard "Process" cost grows with every invoice ever received
See 3.5 (cli.py:526-533, process.py:937-941, invoices.py:125).

### 7.6 Suspected
- **`backup_cmd._running` on Windows** uses `service.process_alive`, which returns False when OpenProcess is denied
  (service.py:403-405). If IT runs `backup restore` from a non-elevated account other than the service's, it may
  not see the running service and restore under it. It also ignores `watch` and `read-pages` notes
  (`.ap_coder_worker-*.json`), so a scheduled `watch` keeps writing during a console restore.
- **`server-update` drops the admin a desktop shortcut on the server.** `set_up` runs `launch.py`, whose
  `ensure_shortcut` (launch.py:1515-1537) has no `server_mode()` guard. The admin's desktop then gets
  "AP Coder.lnk" → APProcessor.bat, the very double-click SERVER.md §6 forbids. (It is a second copy only if the
  admin's recorded data folder is the server's; otherwise it is a stray laptop install with its own DB.)
- **Double self-check per update and two full DB copies per update** (3.4). Not wrong, just slow and disk-hungry.
  The labelled copies are never pruned (backups.py:36, 158-160).
- **Unbounded caches.** The page-reader cache (`ai/page_reader.py:931-965`) has one directory per invoice and is
  never pruned, so at 300k invoices that is 300k directories. The Azure extraction cache is never pruned
  (`ai/extraction.py:230-255`).
- **No business time zone.** About 95 `datetime.now()`/`date.today()` calls in `ap_coder`. A data-centre VM on UTC
  shifts "today" (due dates, ageing, backup names) by 4-8 h relative to Canadian offices. No `zoneinfo` setting
  exists.
- **The robocopy mtime against `SETTLE_SECONDS`** (3.5).
- **`service install` while running.** It calls `stop`, then `start` only if not `--no-start`. With `--no-start` the
  `stopped` flag stays, so the service does not come back after a reboot. This is intended, but `status` is the only
  place that says so.

---------------------------------------------------------------------------------------------------------------------

## 8. Test gaps

**tests/ops**
- `test_service.py`:
  - host behaviour uses a fake popen only;
  - nothing covers a stale or reused PID (7.1);
  - no real task start;
  - nothing covers a `service run` subprocess with a real `serve` restarting after a kill;
  - nothing covers a SIGTERM to the host.
- `test_server_update.py` uses a fake `Server` throughout. Missing:
  - task_name from server.json (7.2);
  - the non-Windows path, where `installed()` is False (macOS);
  - a crash mid-update (7.3);
  - a free-space pre-flight.
- `test_backup_cmd.py`: no restore refusal while a `watch` runs; no large-backup timing.
- Doctor: nothing for disk, certificate expiry or backup age, because those features don't exist.

**tests/launcher** (5,673 lines, thorough for the laptop): no test that `ensure_shortcut` stays off in server mode;
`set_up` in server mode, which `server-update` depends on, is not covered as a unit.

**tests/intake**: no concurrency test (two pick-ups or two unpacks), no large-folder or big-triage-log performance
test, no truncated-email-then-complete test.

**tests/offline**: the offline guard covers flows. No guard test for the service host (`service run`) or `server-update`
paths. Both make only local calls (`_local` with `ProxyHandler({})`), but nothing asserts it.

**tests/meta**: `test_ci_workflow.py` doesn't require `timeout-minutes` on jobs or check bundle-artifact reuse.
Nothing ties `docs/tested-packages.txt` to CI or the bundle.

---------------------------------------------------------------------------------------------------------------------

## 9. Scenarios people may not have thought of
- **The Mac mini reboots after a macOS update.**
  - With FileVault on, an Apple-silicon Mac stops at the FileVault unlock screen after an unattended reboot; launchd
    daemons don't start until someone types a password at the console. Options: FileVault off (a security decision),
    `fdesetup authrestart` for planned restarts, and automatic updates set to download only.
  - After any reboot `host.json` is stale (7.1).
  - LM Studio's daemon must also be a LaunchDaemon (2.2).
  - `pmset autorestart 1` covers power cuts.
- **LM Studio not started.** The app degrades: invoices wait, a banner shows, and doctor warns
  (doctor.py:359-404). Nobody in IT is told. The Mac Studio is a single model URL (`AP_LLM_BASE_URL`,
  core/config.py:321): there is no failover to the backup Mac mini, so switching is a manual `.env` edit. Pages
  pile up in the page-reader queue; there is no queue-depth alert.
- **The disk fills with PDFs.** See 3.2: about 300 GB a year of files plus backup copies, plus never-pruned labelled
  DB copies, on the same volume. There is no free-space check anywhere. Failures surface as
  `backup_status=failed` (shown on Settings only) or as an upload error. SQLite on a full disk returns
  `SQLITE_FULL`: writes fail and the dashboard limps on. Needs a doctor check, a readiness check, an Event Log entry,
  and a "refuse uploads below X GB" guard.
- **The certificate expires.** Nothing checks `notAfter`. uvicorn loads the PEMs at start only, so a renewed
  certificate needs a restart, and an expired one teaches 400 people to click through warnings. Warn at 30, 14 and 7
  days.
- **The clock.** TOTP sign-in needs the server's clock (SERVER.md §12 mentions `w32tm`). Session idle and absolute
  limits shift with clock jumps. Backups are named by local time (a DST fall-back hour is handled by `-2` names). No
  business time zone (7.6). TLS validity depends on the client clocks.
- **The service account's permissions.**
  - Windows: "Log on as a batch job" (SERVER.md §12), Modify on the data folder, read on the PEMs, Modify on the
    backup share (a domain account needed for SMB);
  - LM Studio is per user, so the same account or a second task;
  - macOS: TCC for network volumes, a hidden user with no login, and the data folder outside protected folders.
  - The data folder is writable by the service account. B-1500 and D-109 harden most of what follows from that;
    7.2 is the remaining hole.
- **Running two copies by mistake.**
  - A second service host is prevented by IgnoreNew and the host record (but see 7.1).
  - A second `serve` on the same port refuses (port_refusal).
  - A double-click of APProcessor.bat by the admin sees the service as "other" only when the admin's recorded data
    folder is the server's. Otherwise it starts a separate laptop-mode AP Coder with its own DB on port 8502, and
    staff may be sent there. A desktop shortcut planted by server-update makes this more likely (7.6).
  - Two `watch` tasks, or watch plus the button, duplicate invoices (7.4).
  - SQLite on a network share would risk corruption. Doctor warns only about OneDrive (offline.py:423-424); add a
    UNC or network-drive check.
- **An update failing halfway.** The rollback for exceptions and Ctrl+C is good. A power loss leaves the service
  stopped (7.3). A full disk in `%TEMP%` during `private_copy` or unzip is caught, but there is no pre-flight.
  Two DB copies plus a 1-2 GB ZIP copy plus unzip plus venv need several GB plus 2× the DB. A rollback's `put_back`
  needs space too. Antivirus scanning the new `.venv` makes `set_up` slow, and the 900 s self-check timeout may fire.
- Also:
  - CRA retention: 6 years of invoices means no pruning of `invoices/`, only archival tiers.
  - The `stopped` flag is never visible on the dashboard.
  - IT's VM snapshot or backup tools copy a live SQLite file (documented as not to be used).

---------------------------------------------------------------------------------------------------------------------

## 10. Recommended goal items (benefit · size · proof)

1. **A macOS launchd service backend** (`service install|start|stop|status|remove` on Darwin, plus an LM Studio
   daemon recipe in docs).
   - What: plist as in 2.2, SIGTERM handling in `run_host`, `process_image` for macOS, a stable `current` symlink.
   - Benefit: the pilot runs as an always-on service.
   - Size: M.
   - Proof: a macOS CI job that bootstraps the daemon, gets 200 on `/healthz`, kills -9 the dashboard and sees it back
     within 15 s, runs `service stop` and sees it stay down after a bootout and bootstrap, then `service start`.
2. **Fix the stale host record (7.1)** with an OS lock instead of a PID check.
   - Benefit: the service always comes back after a reboot.
   - Size: S.
   - Proof: a new test where `host.json` names a live foreign PID and the host starts; BUGS.md line.
3. **server-update ignores server.json's task name (7.2)**.
   - Size: S.
   - Proof: a test like test_service.py:192 for `Server`; BUGS.md line.
4. **Make server-update service-agnostic (Windows task and launchd)** and **cut downtime**.
   - What: prepare (unzip, set_up, self-check) before stop; one self-check; no duplicate pre-migration copy; a
     free-space pre-flight; an `update_in_progress` marker with resume and rollback (7.3).
   - Benefit: an update with 400 people goes from about 15 min down to about 3 min, and is safe on the Mac.
   - Size: M-L.
   - Proof: a Windows CI job doing a real `service install` from bundle N-1, `server-update` to bundle N, and `--back`,
     timing the stopped window (assert under X minutes on the runner); a unit test killing the process between each
     step.
5. **Scalable intake pick-up.**
   - What: a ledger or dated subfolders so a check touches only new files; one shared `intake/pickup.py` used by
     `watch` and the dashboard, under an OS lock; a unique check on `file_sha256`; the triage log moved into the DB.
     Optionally an `--inbox` pick-up in the background process so IT doesn't need a separate `watch` task.
   - Size: M.
   - Proof: a test with 50k files where a check reads fewer than 100 files; the double-pickup repro as a test (one
     row); Import page time flat at a 100k-line triage history.
6. **Health, readiness and monitoring for IT.**
   - What: `/readyz` and `doctor --json` (DB, background heartbeat, backup age and status, disk free, certificate
     days, LM Studio and queue depth, clock offset); `service status --json`; Event Log (Windows) and syslog (macOS)
     entries for restart loops, backup failure, low disk, certificate expiry and update result.
   - Size: M.
   - Proof: tests per check; a CI step that fills a fake disk threshold and sees 503 plus an event.
7. **Backups at stage-2 size.**
   - What: a configurable backup window (e.g. 02:00); retention for labelled copies (keep the last N
     before-update); the backup store on another volume; incremental `.files.json` (or the file list in SQLite).
   - Size: M.
   - Proof: a generated 300k-file data folder where a nightly backup with nothing changed finishes in under N minutes
     (record it), and disk use is flat across 20 simulated updates.
8. **Reproducible bundles and a server profile.**
   - What: build with the tested constraints and hashes; a `--server` profile (one Python, one platform, no dev)
     about 200 MB; a scheduled "latest deps" CI job; a licence report with a flag on PyMuPDF AGPL.
   - Size: S-M.
   - Proof: two builds a day apart give the same wheel set; bundle size printed and asserted.
9. **A load and scale test harness.**
   - What: generate a 300k-invoice DB; N simulated logged-in users (asyncio plus httpx) on the 8- or 16-worker server.
   - Benefit: decides whether SQLite holds for 400 people (D-09/D-111), or whether PostgreSQL work is needed.
   - Size: M.
   - Proof: a recorded p50/p95 table in SERVER.md replacing the 12k/100-user estimate; a scheduled workflow that fails
     on regression.
10. **CI hygiene.**
    - What: `timeout-minutes` on every job (plus a meta test); build bundles once as artifacts; cache the wheelhouse
      and Playwright; drop redundant Windows cells; path filters for docs-only PRs; a real Windows task start/stop
      test.
    - Size: S.
    - Proof: about 40% fewer billed Windows and macOS minutes on a PR (compare run summaries); no job without a
      timeout.
11. **Linux systemd backend** (optional), as a test bed for the service host on every push.
    - Size: S.
    - Proof: an Ubuntu CI job with the same start/kill/stop/stale-record checks.
12. **Business time zone and certificate expiry in doctor.**
    - What: `AP_TIMEZONE` used where "today" matters; notAfter warnings.
    - Size: S-M.
    - Proof: tests with a UTC server clock and a Toronto business zone, and a certificate expiring in 10 days giving
      WARN.
