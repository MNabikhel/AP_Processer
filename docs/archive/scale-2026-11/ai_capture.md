# AP Coder: `ap_coder/ai/` and `ap_coder/capture/`, reader's notes (main @ af9925b)

Read only. Every number below was measured in this sandbox (4 cores, `.venv` Python 3.12, RapidOCR PP-OCRv4 only:
the PP-OCRv5 models are missing here, so the second OCR read is the same engine on a "prepared" page,
`layout.py:1046`). The scripts and their outputs are in
`scratchpad/ai_capture_prof/`.
Nothing in the repo was changed.

---------------------------------------------------------------------------------------------------------------------
## 1. Map

### ai/ (about 9.5k lines)
| module | role | main entry points |
|---|---|---|
| `pipeline.py` (877) | one invoice from file to queue | `InvoicePipeline.process` :482 → `_process` :499 → `_read_and_code` :531 → `_code_locally` :596 → `_checks_after_coding` :632 → `_save_and_queue` :678; `finalise_coding` :146 (checks + output; also every reviewer edit); `process_many` :717 (CLI `--workers`, ThreadPoolExecutor); `_local_accounts` :360 (chat model for unassigned lines); `_model_reading` :415 (chat model reads the header from images, page-reader pass only) |
| `offline_coder.py` (675) | the local readers' coding | `read_invoice` :105 (capture `analyze` once, then **again** with template/vendor :124); `code_from_capture` :424 (138 lines); `tax_codes` :605; `local_extraction` :659; `provinces`/`spatial_addresses` |
| `inference.py` (659) | chat-model client | `InvoiceCoder` :113 (builds the JSON schema and system prompt in `__init__`); `_local_create` :337 (CHAT_LOCK slot, response-format and thinking fallbacks); `suggest_accounts` :468 (batches of 40, at most 200 lines, answers kept in `.cache/model_accounts`); `read_header` :631 |
| `local_llm.py` (812) | server detection, slots, JSON parsing | `check_server` :314 (1.5 s timeout, cached 30 s up / 10 s down); `Slots` :401 (in-process Condition plus lock files in `AP_LLM_SLOTS_DIR` across processes, polled every 0.2 s); `CHAT_LOCK` :451; `build_local_client` :478 (max_retries=0); `read_reply`, `parse_json_reply` |
| `page_reader.py` (1408) | OvisOCR2 (vision) | `reader_status` :236; `load_reader` :433 (POST `/api/v1/models/load`, 20,480 context, flash attention); `transcribe` :698 (streaming, greedy, loop detection, one sampled retry); `read_document` :1090 (per-page disk cache by sha256/page/model/prompt); `test_reader` :1292 (self-test on a scanned sample); `READ_LOCK = Slots("page")` :624 |
| `page_worker.py` (973) | the page reader's queue | `BackgroundReader` :711 (one thread per `llm_parallel` slot; also runs the AI reviewer when nothing waits) ; `read_one` :446 → `_read_taken` :516 (99 lines: blank pages, read, **whole pipeline again** with `page_text`, `replace_proposal`, maybe auto-approve); `_postpone_or_fail` :500 (MAX_TRIES 3); `release_interrupted` :697; self-test machinery :824-973 |
| `verifier.py` (694) | AI reviewer | `verify_one` :517 → `_verify` :558 (80 lines); `review` :325 (up to 3 rounds / 5 min, thinking on, 12k tokens); `_fold_in` :654 (may auto-approve a held touchless invoice) |
| `reading.py` (261) | status only | `reading_status` :72: the banner and Settings rows. **Not a reader**: it reports the readers' state (the name is misleading). Called on every render of Process/Review (`web/core.py:587`) |
| `extraction.py` (280) | `ExtractionResult`, Azure DI wrapper (internet only) | |
| `imaging.py` (133) | page images for chat model (≤1 MP) | `render_page_images` :36 |
| `figures.py`, `lessons.py`, `suggest.py` (`suggest_gl` :213, 134 lines), `line_match.py`, `labels.py`, `history.py`, `prompts.py`, `evaluation.py`, `training_export.py`, `azure_check.py` (opt-in, own thread and queue) | | |

### capture/ (about 12k lines)
| module | role |
|---|---|
| `layout.py` (1073) | words and boxes: text layer (`_text_layer_page` :325), OCR (`ocr_image` :519, RapidOCR, `_engine` :412 singletons), OCR disk cache (`ocr_cache` :447 / `_ocr_cached` :486, **key = sha256 of the rendered PNG**, at most 4,000 files :442), `build_layout` :940 (at most `READ_PAGES = 20` pages :45), `second_read_layout` :1052, photo flattening :140 |
| `__init__.py` (561) | `analyze` :471 (91 lines): rule reader (straightened) + template + DI + AI values + **second OCR read** + page-reader transcript + table finder lines + `fuse` (twice when lines change) + text-layer check |
| `reader/` (≈4.3k) | rule reader: `entry.read_fields` :144 → `_read_fields_on` (up to 3 passes for ambiguous dates) → `_read_fields` :565 (151 lines); `labels` (~300 regexes, `_hits_at` :326); `values._value_reading` :239 (105 lines); `spatial._labelled` :416 (85); `patterns._vendor_names` :583 (112); `totals._solve_totals` :47 (100, a nested `itertools.product`); `line_items._read_line_items` :556 (155) |
| `confidence.py` (1022) | `fuse` :528, calibration (`calibration.json`), `run_checks` :169 (114 lines) |
| `supplier.py` (1772) | templates (`learn` :670, 116 lines; `apply_template` :1026), autonomy policy, touchless gates; **its own** `parse_amount` :126, `parse_date` :206, `norm_id` :215, `normalize_value` :248 |
| `workflow.py` (736) | `capture_invoice` :242, `autonomy_decision` :323, `learn_from_approval` :500 (99 lines), `still_touchless` :421 |
| `transcript.py` (463) | the page reader's markdown/HTML as a layout (`layout_from_transcript` :45); `made_up` (pangrams on blank pages) |
| `normalize.py` (685) | amounts (FR/EN, spaces, CR), dates (EN/FR/DE/NL/ES/IT/PT), GST/QST/PST numbers |
| `table_finder.py` (206) | YOLOv8n layout ONNX; per-process in-memory cache of 32 files :160 |
| `tables.py`, `line_items.py`, `line_memory.py`, `locate.py`, `bridge.py`, `types.py` | |

### One invoice, end to end (server mode)
1. **Arrival (a web worker).** An upload is saved to `<data>/invoices/` (`web/pages/process.py:_save_and_start`) → the "process" job.
   **One import job at a time across the whole server** (`web/jobs.py:81` claims it in the DB; `process.py:753` queues
   other uploads **in memory, per worker**). The job reads its files **serially** in one thread (`process.py:600-611`).
2. `InvoicePipeline.process` (`pipeline.py:482`): OCR cache on, one DB session →
   `_as_pdf` (photo → PDF, flattened) → `_check_pdf` → `build_layout` (text layer, or OCR at 200 dpi) → `_upright`
   → `local_extraction` → `learning_rows` (≤50k rows) → `_code_locally`: `read_invoice` (`analyze` ×1, or ×2 when
   the supplier has a template or vendor-master record) → `code_from_capture` (`suggest_gl` per line) →
   **`_local_accounts`: a synchronous chat-model call for every invoice with unassigned lines** (`pipeline.py:628`) →
   coding rules → `finalise_coding` (vendor, duplicates, bank, terms, PO, JDE) → `review_issues` → Azure plan →
   `autonomy_decision` (never touchless before the page reader) → verifier plan → `add_invoice`, `save_capture`,
   queue Azure / verifier / page reader (`queue_new_invoice`, or skipped as a clean digital PDF on the server).
3. **Background process** (`web/server.py:448 BackgroundWork`, one per data folder): `BackgroundReader` threads
   (`llm_parallel` of them). Each round: `release_interrupted` → self-test if due → `read_one`: `ready()`
   (`reader_status`, cached) → `next_page_read` (FIFO, `BEGIN IMMEDIATE`) → `_read_taken`: `load_reader` (an
   uncached listing), `blank_pages` (**`build_layout` again**), `read_document` (≤5 pages; per page `_render` →
   `transcribe` under `READ_LOCK`) → **`InvoicePipeline(...).process(page_text=...)` again** (a new pipeline, coder,
   schema and client per invoice; `_model_reading` = **chat vision call**; `_local_accounts` again, often from the
   cache) → `replace_proposal` → auto-approve when allowed.
4. When `read_one` finds nothing: `_review_one` → `verifier.verify_one` (≤3 thinking rounds, 3 page images).
5. On approval: `learn_from_approval` (`workflow.py:500`) → **`build_layout` again** (template learning).

---------------------------------------------------------------------------------------------------------------------
## 2. Throughput

### Where the time goes (measured, `ai_capture_prof/stages.py`, `prof1.py`)
| invoice | total | breakdown |
|---|---|---|
| digital, 2 pages | 0.13 s (table finder cached) / 0.32 s | rule reader ≈0.1 s; table finder ≈0.2 s |
| scan, 2 pages, arrival | **15.5–16.7 s** | `build_layout` 5.8 s (of which rendering the page PNG 2.2 s); `second_read_layout` 8.8 s (of which `_prepared` deskew + 1.5x + PNG 4.9 s); table finder 0.94 s (0.7 s rendering); rule reader `_read_fields` **0.03 s** |
| same scan, re-read with the OCR disk cache warm (page-reader pass, simulated new process) | **8.45 s** | `_page_png` 2.0 s, `_prepared` 4.7 s, table finder 0.94 s: the cache key is the hash of a freshly rendered PNG, so every re-read re-renders and re-encodes |
| cProfile of the scan (tottime) | 14.6 s | **PNG encoding 6.6 s** (Pillow `ImagingEncoder.encode` 4.2 s, PyMuPDF `fz_write_pixmap_as_png` 2.4 s); ORT `run` 3.7 s; RapidOCR `normalize` 2.0 s (numpy); PNG decode 0.7 s |
| 1-page Quebec scans | 9.2 s / 19.4 s | (`quebec.py`) |
| 200-page digital PDF | 3.1 s | only 20 pages read (`READ_PAGES`), 180 "left out"; RSS 268 MB |
| 200-page scan | ≈155 s (estimate: 20 pages × ~7.7 s) | blocks the server's single import job meanwhile |

Production with both OCR engines installed skips `_prepared` but runs a second model on each page, so expect a
similar order: about 5–8 s of CPU per scanned page on arrival.

How often one scan is read in its life: arrival (`analyze` ×2 when a template exists, so the second-read
preparation runs twice), `blank_pages` (`page_worker.py:393`), the page-reader pass (`build_layout` + `analyze` ×2),
`learn_from_approval` (`workflow.py:576`), and the teach-mode words (`web/document.py:285`, not cached). Each one
renders and PNG-encodes every page again even when the OCR result is cached.

### Model calls per invoice
- Arrival: `suggest_accounts` (chat; one call per 40 unassigned lines) **inside the serial import job**.
- Page-reader pass: OvisOCR2 ≤5 pages (12,288 max tokens a page, streaming); `read_header` (chat vision, 2 images);
  `suggest_accounts` again (a cache hit only when the history and the vendor read are identical).
- AI reviewer: 1–3 rounds, thinking on, up to 12,000 tokens each, 3 images (on the server: touchless candidates only,
  `verifier.py:126`).
- Rough load at 1,250 a day: ~1,250 chat calls on arrival + ~900 vision header calls + ~1,800–2,500 OvisOCR2 pages
  (with `skip_clean` leaving out ~90% of clean digital PDFs) + reviewer rounds. **Nobody has measured the Mac Studio's
  seconds a page / a call.** At 20 s a page and `llm_parallel=1`, the page reader alone needs 10–14 h a day.

### GIL
- Measured (`gil.py`): while a scan is read in one thread, a 5 ms timer thread woke **up to 888 ms late**
  (p99 6 ms). PyMuPDF rendering to PNG holds the GIL about 0.6 s a page. Since the import job runs inside a web
  worker, that worker's other requests stall.
- Three threads reading three scans: 34.5 s vs 41.6 s serial (**17% faster on 4 cores**), with identical results
  (`threads.py`). OCR does not scale with threads; ORT is left at intra-op threads −1 (all cores) by
  `rapidocr_onnxruntime/config.yaml`, while the table finder pins itself to 1 thread (`table_finder.py:70`).
- Pure Python and GIL-bound: the rule reader, `fuse`, `suggest_gl`, the learning-memory rebuild (0.74 s at 50k rows,
  `memory.py`), and `_solve_totals` (worst case **1.32 s**, `solver.py`; it can run up to 3× per `read_fields` because of
  the date-order passes, times each source and each `analyze`).

### Does OCR use processes?
No. Everything is threads: the web workers' import job, `BackgroundReader` helpers, `process_many`. The only
`multiprocessing` is the benchmark (`bench/harness.py:152`). The server's processes are uvicorn web workers plus one
background process (`web/server.py:271-300`), but capture work is never spread across them.

### Queue design
- **Import (first pass): not persistent.** `ImportQueue` lives in memory per worker (`process.py:735-861`), and
  there is one running job server-wide. After a restart, uploaded files sit in the folder as "new" until someone
  presses pick-up or runs `ap_coder watch` (`ops/cli.py:564`). There are no priorities and no fairness: a colleague's
  1,000-file batch is FIFO ahead of yours. The per-user upload limits (200 files, 250 MB, 120 uploads/h,
  `process.py:84-89`) bound abuse, not order. If 1,250 arrive at once, the last is visible in the review queue only
  after `Σ(OCR + chat call)` of all the others, serially (estimate: 3–10 h).
- **Page reader and AI reviewer: persistent** SQLite tables `page_reads` and `verifications` (`core/store/capture.py:750-905`,
  `lessons.py:274-360`), FIFO by `created_at` (`ORDER BY created_at, invoice_id`). A person's "read now" jumps the
  line. Reads interrupted by a dead process are released (`page_worker.py:697`, pid plus start time). A `reading` row
  is **never renewed** while it is read; after `STALE_READING_HOURS = 2` (`_base.py:256`) another reader may take it.
- The reviewer only runs when the page reader has nothing (`page_worker.py:788-796`), so under a backlog it starves,
  and touchless invoices held for it wait.
- The ETA `wait_seconds` (`page_worker.py:909`) ignores `llm_parallel`: it is N× too long with N readers.

### Concurrency limits toward the model server
`llm.parallel` (AP_LLM_PARALLEL, 1–32, default 1) applies to the page slots (`READ_LOCK`) and the chat slots
(`CHAT_LOCK`) **separately**, so up to 2×N requests at once, across the server's processes via lock files
(`local_llm.py:430`). It is one number even when the page reader and the chat model are on different servers.
Status calls (`/v1/models`, `/api/v1/models`) are outside the slots: about 4–6 per page read
(`load_reader` asks uncached twice per invoice, `page_worker.py:535` and `page_reader.py:1149`).

### Timeouts and retries
- Chat: AP_LLM_TIMEOUT_SECONDS **600 s** a call, `max_retries=0` (`local_llm.py:499`); one repair turn
  (`inference.py:121`); the reviewer gives each call its remaining budget, at least 10 s (`verifier.py:369`).
- Page reader: **1,200 s** read timeout between streamed chunks, 10 s connect (`page_reader.py:648`); no total cap a
  page beyond the token limit and loop detection; LOAD_TIMEOUT 300 s; listing 3 s; detect 1.5 s.
- Retries: a temporary page failure is postponed (status `waiting`, `tries+1`), and the thread pauses 60 s
  (`OFF_SECONDS`). After 3 tries the invoice fails until a person re-queues it (`page_worker.py:500`). Load failures
  are remembered for 30 min (`page_reader.py:348`). The reviewer: 3 tries, and only when the very first question fails;
  a failure in a later round becomes UNSURE (`verifier.py:350`).

### Failover to a backup URL
**None.** There is one `AP_LLM_BASE_URL` and an optional `AP_PAGE_READER_BASE_URL` (`page_reader.py:151`,
`core/config.py:321,360`). Settings are re-read when the .env changes (`web/app.py:147 EnvSettings`), so a person can
point at the Mac mini without a restart, but nothing switches on its own. The self-test result is keyed by model
name, not by server (`page_worker.py:132`), so the backup's OvisOCR2 would be trusted without a test of its own.

### Circuit breaking
Only the status cache (`check_server`: down for 10 s) and the 30-minute load-failure memory. When the server
answers `/models` quickly but inference hangs or is overloaded, **every arrival call waits up to 600 s**, inside the
single import job; every page waits up to 1,200 s. Timeouts do not mark the server down.
`DETECT_TIMEOUT = 1.5 s` was tuned for localhost; over the network to a busy Mac Studio it may flap "down", which
idles the readers and shows "LM Studio isn't running" to 300 users.

### An invoice in flight when the model dies
- Arrival `suggest_accounts`: CodingError, so its lines are left for AP and the invoice is saved (`pipeline.py:392`). Fine,
  but it is slow when the model hangs rather than dies.
- Page read: pages already read are kept on disk; the reading is postponed (tries+1), then fails after 3. Ctrl+C or
  shutdown puts it back in line (`page_worker.py:483`).
- `_model_reading` or `_local_accounts` in the page-reader pass: never fatal, but they **block that reader thread** (and the
  reviewer, which shares the thread) up to 600 s per call.
- Reviewer: a first-question failure is postponed; a later round's failure makes it UNSURE (to a person).

---------------------------------------------------------------------------------------------------------------------
## 3. Memory
- Big images: picture decode is capped at 40 MP (`layout.py:80`, `open_image` drafts JPEGs); Pillow's bomb guard is
  worded in `pipeline.py:279`. The page reader reads the whole file into memory and reopens the PDF from bytes for
  every page (`page_reader.py:1119`, `:554`). Uploads are capped at 50 MB.
- In-process caches are bounded: `_SCAN_PNGS` 4 pages, `table_finder._found` 32 files, `_plain` 16k,
  `line_match.keys` 131k, `word_match` 262k, `supplier._printed_*` 3×65k. **Learning memory**: 50k feedback rows plus
  the suggest index ≈ **105 MB per process** (`memsize.py`), rebuilt after every approval. RowsCache keeps 2 lists, so
  ×(8 web workers + background) ≈ 1–2 GB on the server.
- **Disk caches that grow without bound**: `.cache/page_reader/<sha>/` (one JSON a page, never pruned; only
  `timings.jsonl` is trimmed, `page_reader.py:982`), `.cache/model_reading/`, `.cache/model_accounts/`,
  `.cache/extraction/`. At 25k invoices a month that is a few hundred thousand small files a year, all invoice data.
  Only `.cache/ocr` is pruned, to **4,000 files** (`layout.py:442`): at this volume that is about 1.5 days of scans, so a
  page-reader backlog longer than that re-OCRs from scratch, in `blank_pages` too.
- Model clients built per call: `page_reader._client` makes a new OpenAI and httpx client **for every page**
  (`page_reader.py:717`); `InvoicePipeline` (with a new InvoiceCoder, JSON schema, system prompt and client) is built
  **per invoice** in the page worker (`page_worker.py:577`), and the reviewer builds a coder per invoice
  (`verifier.py:551`). No connection reuse and no explicit close (suspected; file descriptors not measured).

---------------------------------------------------------------------------------------------------------------------
## 4. Efficiency hotspots (with evidence)
1. **PNG round-trips**: 6.6 of 14.6 s of a scan's profile. A page is rendered to PNG (`layout.py:874`), decoded again
   for OCR (`:526`), hashed for the cache, re-encoded by `_prepared` (`:1042`) and by `table_finder.page_pictures`
   (`:156`). The cache key needs the PNG, so even a cache hit pays for the render.
2. **Re-reads** of the same scan (5×, listed above), each ≥2 s for 2 pages even with the OCR cache warm; 8.45 s
   through the page-reader pass.
3. **`analyze` twice** per `read_invoice` for known suppliers (`offline_coder.py:124`), so the second OCR read and
   table lines run twice. In-memory caches cover part of it, but `_prepared` is not cached.
4. **Table finder** cached only per process: web worker on arrival, background process again on the page-reader pass.
5. **File hashing**: whole-file sha256 in `add_invoice`, `find_by_hash`, `audit_pick` (×2 via `skip_reason` +
   `autonomy_decision`), `_model_reading`, `page_reader._file_pages`, `table_finder.find_tables`. The Process page
   hashes **every file in the invoices folder on every view** (`process_page` → `folder_files` → `not_processed` →
   `find_by_hash`, `web/pages/process.py:646,679`). Processed files stay in that folder, so the cost grows with every
   invoice ever received. Verified by reading; not timed.
6. **Learning memory**: up to 50k rows re-read (about 0.3 s, docstring `learning.py:38`) and the index rebuilt (0.74 s)
   in each process after **every** approval (`_feedback_stamp`); on a busy server that is nearly every invoice.
7. `reading_status` on every Process/Review render: 4–5 SQL counts, a parse of `timings.jsonl`, `reader_status`
   (`ai/reading.py:72`, `web/core.py:587`).
8. `_solve_totals`: 4×4×6×8×5⁴ combinations; 1.32 s worst case.
9. Repeated settings and database reads per invoice: `reference_data(store)` per page read (`page_worker.py:577`),
   `load_settings(store)` for the verifier, `autonomy_policy()`, `touchless_enabled()`, `supplier_stats` 2–3× in
   `autonomy_decision` / `still_touchless`. Minor next to OCR.

---------------------------------------------------------------------------------------------------------------------
## 5. Cleanliness
- Long functions (≥85 lines): `_read_line_items` 155, `_read_fields` 151, `code_from_capture` 138, `suggest_gl` 134,
  `supplier.learn` 116, `run_checks` 114, `_vendor_names` 112, `_value_reading` 105, `_solve_totals` 100,
  `learn_from_approval` 99, `_read_taken` 99, `analyze` 91, `tables._page_items` 86, `_labelled` 85,
  `_read_document` 83.
- **Duplication**: `capture/supplier.py` has a second amount parser, date parser (EN/FR months only), `norm_id`
  (different meaning from `normalize.norm_id`) and `normalize_value`, next to `capture/normalize.py`. Verified
  divergences (`parsers.py`): `supplier.parse_amount("0.125") == 125.0` (normalize: 0.125); `"$-113.00"` → 113.0 (sign
  lost); `"$.65"`, `"1.3023"`, `"12,345.6789"`, `"TPS 12,50"` → None; `parse_date("24. Januar 2014")` → None. Used in
  template learning (`_matches_key`, which compares absolute values) and `compare_key`, so the impact is low. Also:
  `plain()` exists in 4 variants (`reader/_common.py:16`, `normalize._plain`, `line_items._plain`, `supplier._fold`);
  page rendering in 5 places (`layout._page_png`, `page_reader._render`, `imaging._render_pdf`,
  `table_finder.page_pictures`, `training_export.page_pngs`); a "rows of words" grouping in 3 (`layout.group_lines`,
  `tables._rows`, `supplier._rows`).
- Confusing flows: `reading.py` is a status page, not a reader. "Reading" happens in `offline_coder.read_invoice`
  (capture) on arrival and again in `page_worker._read_taken` via a whole second `InvoicePipeline.process`. Two
  ways of capturing: `_code_locally` keeps its capture in `captured["local"]`, while the Azure path runs
  `capture_invoice` afterwards (`pipeline.py:644-650`). The chat model's three jobs (suggest, header, reviewer) live in
  three modules.
- Dead or near-dead in this offline build: the Azure OpenAI path in `InvoiceCoder.code` (`inference.py:260-307`), Document
  Intelligence in `extraction.DocumentExtractor`, `_cross_read` (`capture/__init__.py:459`, `cross_read=False` everywhere
  but tests/bench), the `pipeline._images` vision attach. All reachable only with AP_ALLOW_INTERNET.
- `test_under_way.__test__ = False` style markers on functions named `test_*` in production code
  (`page_reader.py:1239,1345`, `page_worker.py:846,868,884`).

---------------------------------------------------------------------------------------------------------------------
## 6. Possible bugs
Verified:
- V1. `supplier.parse_amount` misreads `"0.125"` (125.0) and drops the minus of `"$-113.00"` (`supplier.py:126`).
- V2. `wait_seconds` ignores `llm_parallel` (`page_worker.py:930`): the ETA shown is N× too long. (Read, not run.)
- V3. A `reading` row is never renewed (`capture.py:790` is the only `status='reading'` write). A read lasting over 2 h
  (5 pages × up to 2 readings × a 20-minute timeout) can be taken by a second reader thread; both then write the
  proposal. (Read, not reproduced.)
- V4. The OCR cache (4,000 files) is too small for this volume: a backlog longer than about 1.5 days re-OCRs everything
  on the page-reader pass (`layout.py:442`).

Suspected:
- S1. **Arrival stalls behind a hung or overloaded chat model**: 600 s a call, no breaker, in the server's only import
  job (`pipeline.py:628` → `inference.py:352`).
- S2. PyMuPDF used from several threads without a lock (only `page_reader._RENDER_LOCK`). Logged as N-14 ("likely safe
  under the GIL"), but `llm_parallel` helper threads now run `build_layout` concurrently. The threaded OCR test above
  gave identical results.
- S3. `ap_coder watch` and the dashboard import can both process the same new file: there is no shared lock, and
  `file_sha256` has no unique constraint (`invoices.py:66-76`). The result is two rows, flagged as possible duplicates.
- S4. DETECT_TIMEOUT 1.5 s / LISTING 3 s over the LAN to a busy Mac Studio may flap the status to down.
- S5. The self-test is keyed by model name, not server: a backup server is trusted on the primary's test.
- S6. A swapped chat model is never qualified: a model that answers "agree, confident" to everything auto-approves
  held touchless invoices (`verifier.py:675-685`).
- S7. The reviewer starves under a page-reader backlog (shared thread).
- S8. A new httpx client for every page and invoice without close (sockets reclaimed only by GC).

---------------------------------------------------------------------------------------------------------------------
## 7. Test gaps (`tests/ai` 25 files, `tests/capture` 33; 18k lines)
Covered: page reads cut off and the 3-try limit (`test_page_worker.py:721`), interrupted reads released (`:797`),
slots across processes and `llm_parallel` readers (`tests/web/test_server_scale.py:442-470`), the reviewer's own timeout
(`test_verifier.py:575`), short call timeouts (`test_local_llm.py:971`), password PDFs (`tests/intake/test_unusual_files.py`),
HEIC and phone photos (`tests/intake/test_intake.py:58-134`), lots of French and Quebec readers.
Missing:
- failover (none exists), circuit breaking, **a hung model server** (accepts, never answers) on the arrival path;
- any throughput or load test (1,250 a day, mixed scans), and the latency of web requests while an import OCRs
  (the GIL test above is the first);
- a read longer than STALE_READING_HOURS; the reviewer's starvation; the ETA with `llm_parallel`;
- OCR cache eviction under a multi-day backlog; concurrent PyMuPDF/OCR in helper threads;
- 200-page scans (time bound and the import blocking others); the Process page with thousands of files in the folder;
- `watch` and dashboard processing the same file;
- chat-model swap qualification; a parity test of `supplier.parse_amount` against `normalize.parse_amount`.

---------------------------------------------------------------------------------------------------------------------
## 8. Scenarios
- **Model server slow or overloaded by several clients**: no breaker; arrival waits up to 600 s an invoice;
  page reads 1,200 s a chunk; 3 timeouts and an invoice fails until re-queued; status checks may flap.
  `llm_parallel` is per AP Coder only: CloseDesk and others on the same LM Studio are not counted.
- **Model swapped**: the page reader self-tests the new OvisOCR2 key (good) and re-reads pages, with caches keyed by
  model. A chat swap has no qualification; thinking-off support differs (Qwen 3.5 in an older LM Studio thinks for
  minutes and gets THINKING_MESSAGE errors). A non-"ovisocr" document model is never used unless AP_PAGE_READER_MODEL
  is set.
- **Same invoice uploaded twice at once**: the serial import plus `split_new` at start skips the second (safe in the
  dashboard); `watch` alongside is not (S3). The page-reader queue dedupes by invoice_id.
- **Giant 200-page PDF**: 20 pages read locally and 5 by OvisOCR2; `PAGES_NOT_READ` warns and it always goes to a person.
  A scan costs about 2.5 min of OCR **in the server's only import job**, during which everyone else's uploads wait.
- **Encrypted PDF**: a user password is worded plainly (`pipeline.py:294`); owner-only (permissions) PDFs read normally.
- **Phone picture**: made a PDF, EXIF-turned and flattened (`intake/pictures.py:217`); over 40 MP drafted down or
  refused; HEIC needs pillow-heif.
- **French / Quebec**: labels, TPS/TVQ, "1 234,56 $" and French dates handled. On two Quebec samples, digital and
  scanned, everything was right but currency (not printed: default CAD plus a warning) and the payment terms of one
  scan. The OCR model is PP-OCR "ch" (Chinese/English); accents are folded by `plain()`.
- **Queue backing up for days**: FIFO with no priorities (due date, discount terms, touchless-eligible); the OCR cache
  evicts after about 1.5 days, so OCR is redone; the reviewer starves; the ETA is wrong with N readers; the
  `.cache/page_reader` files keep growing.
- **Server restart mid-import**: the in-memory import queue and the job are lost; files wait in the folder until picked up.

---------------------------------------------------------------------------------------------------------------------
## 9. Recommended goal items
| # | item | benefit | size | proof |
|---|---|---|---|---|
| 1 | **Server-day throughput bench**: replay N mixed invoices (50% scans) through arrival + page reader + reviewer against a fake LM Studio with configurable latency, failures and hangs (`bench/speed.py` has a start) | turns guesses into numbers; a guard for every later item | M | `python -m ap_coder.bench speed --server-day --n 1250` reports invoices an hour per stage; target: 1,250 cleared within 8 h at the measured Mac Studio latencies |
| 2 | **Chat calls off the arrival path** (queue them like the page reader), or a short arrival timeout (20–30 s) plus a breaker | arrival no longer waits on the Mac Studio; a hung model cannot stall imports | M | test: a fake server that never answers, 20 invoices imported in under 60 s; bench item 1 |
| 3 | **Failover and circuit breaker**: primary + backup URLs for chat and page reader, health checks, open after N timeouts, half-open retry; the self-test keyed by server+model; network-only addresses (offline rule) | Mac mini takeover without a person; no 10-minute stalls | M | tests with two fake servers: kill the primary mid-read, the read finishes on the backup; `tests/meta` offline rules pass |
| 4 | **OCR and render cache by (file sha, page, engine, dpi)** looked up *before* rendering; OCR from pixmap arrays (no PNG); cache the prepared image and table-finder boxes on disk | scan arrival about 15→9 s; re-read 8.5→<1 s; teach mode instant | M | `scripts/same_behaviour.py` + `bench run --quick` unchanged; timings from `ai_capture_prof/stages.py` and `cached.py` |
| 5 | **Capture in a process pool** (the background process or N capture workers) with ORT threads pinned, instead of the serial import thread in a web worker; persistent import queue in SQLite (resumes after a restart, fair round-robin per uploader, priorities) | uses the 32 vCPUs; no web stalls (0.9 s GIL); 1,250 at once cleared in about an hour | L | bench item 1: arrival ≥600 invoices an hour on 32 vCPU; GIL probe p99 <50 ms during import; restart test resumes the queue |
| 6 | **Queue scheduling fixes**: heartbeat on `reading` rows; reviewer gets its own slot or interleaves (no starvation); priorities (person-asked, touchless-eligible, due date); ETA ÷ `llm_parallel` | same-day clearing is predictable; touchless keeps flowing | S–M | unit tests per rule; bench shows reviewer throughput >0 under a page backlog |
| 7 | **Size and prune the caches** for the volume: OCR cache by age or backlog, not 4,000 files; prune `.cache/page_reader`, `model_reading`, `model_accounts` once an invoice is decided | no re-OCR after a backlog; no unbounded invoice data on disk | S | test: a 3-day backlog makes no second OCR; folder size stays bounded in a 30-day simulation |
| 8 | **Hash once**: carry the file sha through the pipeline; the Process page lists new files without hashing the whole folder (mtime/size index or a processed-files table) | Process page O(new files), not O(all invoices ever) | S | timing with 10k files in the folder: under 200 ms |
| 9 | **Incremental learning memory** (append the new lesson instead of rebuilding 50k rows + index in every process) | about 1 s saved an invoice on a busy server; less memory | M | `same_behaviour.py`; bench `coding --quick` identical; micro-bench 0.74 s → <20 ms |
| 10 | **One normalizer**: `supplier.py` uses `capture/normalize.py` (fix "0.125", the sign of "$-113.00", German dates) | one truth for values; fewer silent mismatches | S–M | a parity test (the `parsers.py` cases); `bench learn --quick` unchanged |
| 11 | Prune `_solve_totals` (early bounds, ≤N combos) | worst case 1.3 s → ms | S | micro-bench `solver.py`; `bench run --quick` unchanged |
| 12 | **Chat-model qualification** when the model changes (a reviewer self-test like OvisOCR2's, a known-wrong invoice it must reject) | a swapped model can't rubber-stamp approvals | M | test: a fake "always agree" model is not trusted |
| 13 | PyMuPDF and HTTP hygiene: one render lock (or one process per renderer), one HTTP client per server reused | removes crash risk (N-14) and the connection churn | S | threaded stress test of 50 PDFs; file-descriptor count flat over 1,000 page reads with a fake server |
