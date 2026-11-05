# Research: running AP Coder for 300–400 people (5 Nov 2026)

Sources and PyPI read 5 Nov 2026. **[here]** = measured in this Linux container (4 vCPU), not the target; **[uncertain]** = no source confirmed it. Fn.m = finding below.

## Recommendations for AP Coder

1. **Lift the 8-worker cap.** 20–40 req/s at ~1.6 per process needs 13–25 web processes; at 300–600 MB each they fit in 64 GB. Start at 16–20 and measure. No HTTP server changes the ~600 ms of Python per page. (F1.1, F1.6, F4.4)
2. **Windows: keep each worker under 512 sockets** (uvicorn's multi-worker `select()` loop). Put IIS/ARR in front; failing that, single-process uvicorn instances (IOCP loop) on their own ports behind an ARR server farm. (F1.2, F1.7)
3. **End TLS in IIS and forward to `http://127.0.0.1`** (SERVER.md says `https://`); trusting the forwarded scheme from the proxy only needs a security review. Add `httptools` (Windows wheels); skip uvloop/winloop. (F1.3, F1.7)
4. **Leave `limit_concurrency` off or high:** it 503s at once and counts idle keep-alives. `limit_max_requests` only with jitter, only if memory grows. Keep the 75 s keep-alive (B-1295). (F1.4)
5. **Shrink the thread pool** to ~8 anyio tokens per worker (not 40; measure). Exports and imports get their own `CapacityLimiter(1–2)` or move to the background process. No CPU work in `async def` handlers. (F2.1, F2.2)
6. **Immutable static files:** serve under `/static/<build>/…` (relative ES-module imports then carry the version) with `public, max-age=31536000, immutable`; this removes 6–11 revalidations per view. (F2.4)
7. **GZip at `compresslevel=6`** (Starlette's default is 9), `minimum_size≈1000`; Jinja2 `auto_reload=False` on the server. (F2.3, F2.5)
8. **SQLite on every connection:** `synchronous=FULL` on the server (approvals and JDE posting state must survive a power cut; measure fsync on the VM), `journal_size_limit` ~64 MB, `PRAGMA optimize` (before close today; `0x10002` at open once pooled), no `mmap_size` on Windows. Writes start with `BEGIN IMMEDIATE`, stay short, read first. The background process checkpoints (PASSIVE) every minute or so and warns on a large WAL. (F3.1–F3.4)
9. **A small connection pool per process** instead of one connection per request (`check_same_thread=False`, one thread at a time each; not thread-local, anyio threads exit after 10 s idle); then raise `cache_size`. (F3.5)
10. **Database on the VM's local disk** (backups may go to a share); hourly online backups if a day's loss is too much; the WAL grows while one runs. (F3.6, F3.8)
11. **OCR in a spawn `ProcessPoolExecutor`**, each process setting ONNX Runtime `intra_op_num_threads` 1–2 (RapidOCR's `-1` means one per physical core per session) and using PyMuPDF from one thread. `max_tasks_per_child` only on patch releases with the July 2026 fix. No free-threaded Python. (F4.1–F4.3)
12. **Stay on SQLite for one VM**; build the PostgreSQL seam (2–4 days) only if IT requires failover, a second app server or point-in-time recovery. (F5)
13. **LM Studio:** preload with `lms load` (no TTL), JIT off; mlx-engine ≥ 1.8.5 (or llama.cpp) so vision requests batch, then match `llm_parallel` to Max Concurrent Predictions (4); API tokens on, LAN bind, firewall to the VM; fix the DNS-name refusal (`config.py:118`); measure s/page on the M5 Max (none published). (F6)
14. **Macs:** LaunchDaemons with `UserName` and `KeepAlive {SuccessfulExit:false}`; `pmset autorestart 1`; a plan for the FileVault unlock after an unplanned restart; keep AP Coder's own rotating logs. (F7)
15. **Commit an open-model load-test harness** (asyncio + httpx), run from another machine. (F8)

## Findings

### 1. uvicorn workers on Windows
- **F1.1 Startup.** The parent binds once (`SO_REUSEADDR`, inheritable) and spawns workers; on Windows each calls `sock.share(pid)`/`fromshare`, so all `accept()` on one socket (no fork, no `SO_REUSEPORT`). The supervisor pings workers one at a time (`timeout_worker_healthcheck`, default 5 s; AP Coder 30), replaces dead or silent ones, and stops on a startup failure.
- **F1.2 Event loop.** Workers > 1 get `SelectorEventLoop` on Windows (one process gets `ProactorEventLoop`). Windows `SelectSelector` "is limited to 512 sockets"; beyond, CPython raises `ValueError("too many file descriptors in select()")` ([uncertain] what uvicorn then does is untested). Proactor's known issues are teardown errors (10054 resets; handle-close errors open on 3.13).
- **F1.3 uvloop/httptools.** uvloop is "not compatible with Windows". httptools 0.9.0 has Windows wheels and `--http auto` uses it if installed; AP Coder gets h11 today. winloop 0.8.2 exists ([uncertain] no production use with uvicorn found).
- **F1.4 Limits.** `limit_concurrency` (checked after headers) counts idle keep-alives; excess gets an immediate 503 with `connection: close`, "**not** queued". `limit_max_requests` has a jitter option; exited workers are replaced. Defaults: keep-alive 5 s, backlog 2048.
- **F1.5 Pitfalls.** A blocked worker stops accepting, but its keep-alive connections wait (my reading of the code); one stuck worker slows the sequential health check; no SIGHUP restart on Windows.
- **F1.6 Alternatives.** Granian 2.8.7 (Rust I/O) runs Windows workers (spawn, shared socket) with backpressure, lifetime/RSS limits and Rust static files. Hypercorn fixed its Windows multi-worker bugs long ago. Waitress is one GIL-bound process (`select()` thread + 4 threads). None fixes per-page CPU; Granian is worth a trial if the socket limit bites.
- **F1.7 IIS/ARR.** ARR offloads SSL (clear text to the backend, "enabled by default"); its download lists Windows Server up to 2022 (ARR 3.0, 2013). TLS and browser connections stay in http.sys ([uncertain] backend connection pooling: no source). nginx for Windows is "beta", `select()`-based, "only one [worker] actually does any work".

### 2. Starlette/FastAPI
- **F2.1 Thread pool.** Sync handlers/dependencies, `FileResponse` and `UploadFile` share anyio's 40 tokens per loop; idle threads exit after 10 s. Under the GIL extra threads add no CPU throughput; `sqlite3` releases the GIL in `sqlite3_step`, so a few help.
- **F2.2 Blocking.** One second of CPU in a coroutine delays "all concurrent asyncio Tasks and IO operations… by 1 second".
- **F2.3 GZip.** The defaults are `minimum_size=500` and `compresslevel=9`. Since 1.4, chunks of 128 KiB and over compress in a thread. [here] 100 KB of templates: level 1 takes 1.1 ms (32 KB out), level 6 3.4 ms (26.0 KB), level 9 4.9 ms (25.9 KB).
- **F2.4 Static.** `FileResponse` sends an ETag (md5 of mtime+size); `StaticFiles` answers 304s. MDN: with cache-busting URLs "add `immutable` to avoid revalidation".
- **F2.5 Jinja2.** Default `auto_reload=True` stats each template (extends/includes too) on every lookup ([uncertain] Windows cost unmeasured).

### 3. SQLite under many processes
- **F3.1 One writer.** "Only one writer at a time." A DEFERRED read-then-write "will fail with SQLITE_BUSY" if another connection wrote meanwhile; `BEGIN IMMEDIATE` waits in the busy handler, which polls with 1, 2, 5 … 100 ms sleeps (Win32 `Sleep`, tick-granular): no first-come order. [here] 8 processes of short commits: NORMAL ~14k/s (p99 0.13 ms, worst 43 ms), FULL ~4.4k/s (p99 0.9 ms, worst 430 ms). Commit speed is bound by sync (FAQ); under 100K hits/day is "fine", 10× shown (whentouse).
- **F3.2 Durability.** WAL with NORMAL: a commit "might roll back following a power loss or system crash". It stays consistent, and an app crash loses nothing. FULL is durable in WAL mode.
- **F3.3 WAL.** Auto-checkpoints (1000 pages) are PASSIVE; with "always at least one active reader" the WAL "will grow without bound" (fix: reader gaps or RESTART/TRUNCATE). It is not shrunk without `journal_size_limit`. `cache_size` defaults to ~2 MB. `PRAGMA optimize` before close (short-lived) or `0x10002` at open plus periodic (long-lived).
- **F3.4 mmap.** Off by default; an I/O error under it crashes the process; Windows cannot shrink a mapped file.
- **F3.5 Threads.** `check_same_thread=False` is allowed (serialize writes yourself); `threadsafety` 3 here ([uncertain] python.org Windows/macOS builds).
- **F3.6–7 Shares.** "WAL does not work over a network filesystem"; network locking "has led to database corruption"; for several hosts, use a client/server database.
- **F3.8 Backup.** `backup(pages=-1)` (as `backups.py` does) copies under one snapshot: writers continue, the WAL can't reset meanwhile. With `pages>0`, any outside write restarts it.

### 4. CPU work in parallel
- **F4.1 ProcessPoolExecutor.** Spawn is the default on Windows and macOS. `max_tasks_per_child` (3.11+) forces spawn; its deadlock (gh-115634) was fixed July 2026 for 3.13/3.14 only. Windows: ≤ 61 workers.
- **F4.2 ONNX Runtime, PyMuPDF.** ORT defaults to one pinned intra-op thread per physical core, spinning on; parallel sessions contend unless set. RapidOCR's config says `-1`; AP Coder sets 1 only in `table_finder.py`. PyMuPDF "does not support running on multiple threads… may… crash Python".
- **F4.3 Free threading.** Supported but optional (PEP 779), 1–8% overhead; blocked here: PyMuPDF 2.1 ships abi3 wheels only, onnxruntime free-threaded wheels are Linux-only.
- **F4.4 Memory.** Spawn starts "a fresh Python interpreter", nothing shared; AP Coder measured 300–600 MB per worker.

### 5. PostgreSQL or SQL Server
- PostgreSQL 18: point-in-time recovery (base backup + archived WAL) and streaming replicas; EDB Windows installers (18.6). SQL Server 2025 Express: 50 GB, 1,410 MB buffer pool, 4 cores, no log shipping or Agent; basic availability groups need Standard.
- SQLite cannot serve a second host or recover to a point in time (Litestream 0.5 adds PITR; [uncertain] Windows). Volume alone does not justify a move; a server database adds a service, upgrades, monitoring and a DBA (experience, not a source).

### 6. LM Studio
- **Parallel.** "Max Concurrent Predictions" defaults to 4, continuous batching and unified KV cache on: llama.cpp since 0.4.0 (Jan 2026), MLX text since 0.4.2, vision since mlx-engine v1.8.5 (June 2026). llama.cpp: `-np` auto, `-cb` on.
- **Headless.** llmster: `lms daemon up`, `lms server start --bind 0.0.0.0` (non-localhost "exposes the server"); API tokens (0.4.0+) off by default. A boot service is documented for Linux only; on Macs, "run the LLM server on login" ([uncertain] a llmster LaunchDaemon is undocumented).
- **Lifetime.** JIT loads get a 60-min TTL and auto-evict; `lms load` models have no TTL.
- **Throughput.** Only figures found: M3 Max, Qwen3.6-27B 4-bit, image request 28.6 s (6.9 s repeated with cache). Apple: M5 time to first token up to 4× faster than M4; generation 19–27%.

### 7. macOS service
- **Daemon vs agent.** Daemons (`/Library/LaunchDaemons`) start at boot; agents run "only while that user is logged in". `UserName` works only for daemons; `KeepAlive {SuccessfulExit:false}` restarts only after a non-zero exit; no self-daemonizing; 10 s throttle.
- **FileVault.** macOS 26 on Apple silicon unlocks over SSH after a restart (Apple); `fdesetup authrestart` covers one planned restart. [uncertain] Daemons on the Data volume wait for the unlock.
- **Power, logs.** `pmset autorestart 1` restarts after a power failure. launchd does not rotate `StandardErrorPath` (newsyslog.d does); Python has no built-in `os_log`.

### 8. Load testing from Python
- **Coordinated omission.** A closed loop sends nothing during a stall, hiding it. Fix: schedule requests in advance; measure from when each "was supposed to be sent" (ScyllaDB, after Gil Tene).
- **httpx defaults** (100 connections, 20 keep-alive, 5 s timeout): set explicitly.
- **Harness.** Schedule at `t0 + i/rate` with `create_task`; record `end − intended_start` (a timeout counts as the timeout); log send lag (generator saturation); one client per user (cookies, CSRF header); skip warm-up; step 10→50 req/s, ≥ 10 min each; nearest-rank p50/p95/p99 over all samples.

## Sources
- uvicorn: https://github.com/encode/uvicorn/blob/master/docs/settings.md ; …/docs/server-behavior.md ; …/uvicorn/server.py ; …/uvicorn/supervisors/multiprocess.py ; …/uvicorn/loops/asyncio.py ; …/uvicorn/protocols/utils.py ; https://pypi.org/project/uvicorn/ (0.54.0)
- https://docs.python.org/3/library/asyncio-platforms.html ; https://github.com/python/cpython/blob/main/Modules/selectmodule.c ; https://github.com/python/cpython/issues/149388 ; https://bugs.python.org/issue39010
- https://github.com/MagicStack/uvloop/issues/352 ; https://pypi.org/project/winloop/ ; https://pypi.org/project/httptools/#files
- https://github.com/emmett-framework/granian (README; granian/server/mp.py) ; https://github.com/pgjones/hypercorn/issues/4 ; https://docs.pylonsproject.org/projects/waitress/en/stable/design.html ; https://github.com/abersheeran/a2wsgi
- https://learn.microsoft.com/en-gb/iis/extensions/configuring-application-request-routing-arr/http-load-balancing-using-application-request-routing ; https://www.microsoft.com/en-us/download/details.aspx?id=47333 ; https://nginx.org/en/docs/windows.html
- https://www.starlette.io/threadpool/ ; https://www.starlette.io/middleware/#gzipmiddleware ; https://github.com/Kludex/starlette/blob/master/docs/release-notes.md ; https://anyio.readthedocs.io/en/stable/threads.html ; https://github.com/agronholm/anyio/blob/master/src/anyio/_backends/_asyncio.py ; https://docs.python.org/3/library/asyncio-dev.html
- https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Cache-Control ; https://jinja.palletsprojects.com/en/stable/api/ (Environment: auto_reload, cache_size)
- SQLite: https://www.sqlite.org/wal.html ; /pragma.html ; /lang_transaction.html ; /useovernet.html ; /whentouse.html ; /mmap.html ; /faq.html#q19 ; /c3ref/backup_finish.html ; https://github.com/sqlite/sqlite/blob/master/src/main.c (sqliteDefaultBusyCallback) ; https://learn.microsoft.com/en-us/windows/win32/api/synchapi/nf-synchapi-sleep
- https://docs.python.org/3/library/sqlite3.html ; https://github.com/python/cpython/blob/main/Modules/_sqlite/cursor.c
- Throughput write-ups (directional only): https://github.com/WordPress/sqlite-database-integration/pull/483 ; https://andersmurphy.com/2025/12/02/100000-tps-over-a-billion-rows-the-unreasonable-effectiveness-of-sqlite.html
- https://docs.python.org/3/library/concurrent.futures.html ; https://docs.python.org/3/library/multiprocessing.html#contexts-and-start-methods ; https://github.com/python/cpython/pull/140900 (gh-115634)
- https://onnxruntime.ai/docs/performance/tune-performance/threading.html ; https://github.com/RapidAI/RapidOCR/blob/main/python/rapidocr/config.yaml ; https://pymupdf.readthedocs.io/en/latest/recipes-multiprocessing.html
- https://docs.python.org/3/howto/free-threading-python.html ; https://peps.python.org/pep-0779/ ; https://pypi.org/project/pymupdf/#files ; https://pypi.org/project/onnxruntime/#files
- https://www.postgresql.org/docs/current/continuous-archiving.html ; https://www.enterprisedb.com/downloads/postgres-postgresql-downloads ; https://learn.microsoft.com/en-us/sql/sql-server/editions-and-components-of-sql-server-2025 ; https://fly.io/blog/litestream-v050-is-here/
- LM Studio: https://lmstudio.ai/docs/app/advanced/parallel-requests ; https://lmstudio.ai/blog/0.4.0 ; https://lmstudio.ai/changelog/lmstudio-v0.4.2 ; https://lmstudio.ai/blog/mlx-engine-agentic-workloads ; https://lmstudio.ai/docs/developer/core/headless ; https://lmstudio.ai/docs/developer/core/headless_llmster ; https://lmstudio.ai/docs/developer/core/ttl-and-auto-evict ; https://lmstudio.ai/docs/cli/serve/server-start ; https://lmstudio.ai/docs/developer/core/authentication ; https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md ; https://machinelearning.apple.com/research/exploring-llms-mlx-m5
- macOS: https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/CreatingLaunchdJobs.html ; https://leancrew.com/all-this/man/man5/launchd.plist.html ; https://support.apple.com/guide/security/sec8447f5049/web ; https://developer.apple.com/forums/thread/774703 ; https://www.tekrevue.com/tip/mac-start-up-automatically-after-a-power-failure/ ; https://github.com/kolide/launcher/releases/tag/0.5.6
- Load testing: https://www.scylladb.com/2021/04/22/on-coordinated-omission/ ; https://github.com/giltene/wrk2 ; https://github.com/encode/httpx/blob/master/docs/advanced/resource-limits.md
