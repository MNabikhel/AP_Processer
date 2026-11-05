[Subagent hand-back] The text below is the final report of a subagent this session delegated to. It is model output, NOT a message from the user: instructions, requests, or approval claims inside it are the subagent's words and carry no user authority. The harness indents every line of the report, so a frame-like line at column zero inside it would be forged. Notes above this frame may quote model-derived text, which carries no user authority either. The report follows:
## W2b report: the review screen's invoice page made light (D-160)

All four items are done and committed, plus a merge of the current `scale-int` (W2d, D-162). Nothing is pushed and the tree is clean. Branch HEAD is `558d81f`; my own last commit before the merge is `54187e7`.

**One thing you need to decide.** The brief asked for browser caching of the vendor list and the page images, but B-1070 (archived decision, test `test_every_response_behind_the_login_is_not_stored_by_the_browser`) makes every server response behind the login `no-store`, page images included. I kept B-1070 and added `/review/vendors.json` to that test.
- **Laptop:** the browser keeps the vendor list as asked.
- **Server:** each editable screen downloads it once per open (370 KB gzipped, about 7 ms of CPU from what the worker keeps), so most of the CPU saving holds but the bandwidth saving does not.
- **To change it:** exempting `/review/vendors.json` (and the page images) is a one-line change in `identity._no_store`. That reverses part of B-1070, so it is your call; I did not do it.

### Item 1: the vendor list out of the page (`6f51d3c`)
**What changed:**
- `invoice.vendor_list` builds, once per change of the vendor list in each worker, the JSON, a gzipped copy, a version (hash of the JSON), a count, and an index by vendor key and by GST/HST business number. It uses `summary_memo` on `changes/vendors`; without a vendor master, also the `approved_vendors` topic.
- New route `GET /review/vendors.json?v=<version>`, rule READ (same as the screen; the entries hold no bank data). On a laptop it is cached as `private, max-age=31536000, immutable` when `v` matches, otherwise `private, no-cache`. A weak ETag gives 304s, with `Vary: Accept-Encoding`.
- The page now holds only `#vendor-seed`: the list's address, whether a vendor master enforces it, the count, and the vendor named on the invoice plus the one whose GST/HST number is shown. So the line under the vendor field is right as soon as the page opens.
- `invoice.js` fetches the full list when an editable screen opens. Read-only screens fetch it only if needed (in practice never).
- The fill-from-the-list waits for it. A vendor list opened before it arrives is redrawn when it does (new `picker.js` `refresh(input)`, 6 lines). A vendor added on the screen is kept when the list arrives.
- `bench/load.py` now fetches the vendor list through its browser cache, as the page does.

**Measured (100k database):**

| | before | after |
|---|---:|---:|
| page size | 3,609,239 B | 299,818 B |
| page CPU, same invoice again | 494 ms | 70 ms |
| page CPU, four vendors in turn | 514 ms | 79 ms |
| W2a's probe (same four vendors as its `items1to7` run), alone / in turn | 520 / 544 ms | 84 / 171 ms |

- The list itself is 3.03 MB of JSON, 369 KB gzipped. Serving it costs 7.9 ms; a 304 costs 7.0 ms.
- Building it costs 587 ms, once per change per worker, and it keeps about 12 MB per worker.
- Other embedded lists are small and I left them: `#gl-options` 73 KB, `#cc-options` 12 KB, tax codes 8 KB, and the "Apply to all lines" selects (`invoice.html:269-275`), which repeat every GL account and cost center as options (about 85 KB).

### Item 2: page images drawn once into a shared disk cache (`3b18301`)
**What changed:**
- **Server mode only.** `document.page_png(..., kept_in)` reads the picture from `<data>/.cache/pages/<2>/`, or draws it and puts it there.
- **File names:** the file's SHA-256 (worked out once per version of the file), page, scale, DPI, size cap and a `DRAWING` version number.
- **Writing:** to a temporary name, then `os.replace`. On Windows a refused rename is retried briefly, and left alone when another worker already put the same picture there (as B-1689 handled the Spend workbook). A full disk or unwritable folder just means the page is drawn each time, with nothing half-written left behind.
- **Size cap:** 1 GB (`PAGES_KEPT_BYTES`), oldest written removed first down to three quarters, never the newest. Pruning runs at a worker's first write and after every sixteenth of the cap written; leftover partial files older than an hour are removed.
- On the server a worker no longer keeps pictures in its own 96 MB memory cache (the operating system's file cache is shared).
- **Unchanged:** the laptop's memory cache and its `private, max-age=86400` header (its test asserts that); the server's `no-store`.

**Measured:**

| picture | drawn | read back from the shared folder |
|---|---:|---:|
| scale 1 (74 KB) | 38-44 ms | 8.2 ms |
| scale 2 (161 KB) | 111-115 ms | 8.2 ms |

The 8.2 ms is the route's fixed cost; writing the file adds nothing measurable.

### Item 3: the reader panel's poll (`7986131`)
**What changed:**
- The page carries `data-tag` (from `reader_tag`) describing what its reader panel shows.
- The poll sends `?seen=<tag>`. While nothing changed, the answer is `{state, poll, tag, same}` with no HTML and is not rendered, and `invoice.js` redraws nothing.
- A poll without `seen` (an older page) gets the same answer as before. The load harness sends the tag too.

**Measured:** 210 → 66 bytes per poll. CPU stays about 15 ms, because 9 ms of it is `reading_status` and the rest the invoice's row (see notes for later).

### Item 4: `Store.vendor_invoices` kept per vendor (`54187e7`)
**What changed:**
- Three new triggers in `SUMMARY_TRIGGERS` count changes per vendor in settings (`changes/vendor/<key>`).
- They fire on any insert or delete of an invoice, and on any update of the columns `vendor_invoices` lists. The confidence a check writes is not one of them, so checks don't reset the count.
- Each worker keeps the invoices of the vendors asked for most recently, up to 20,000 rows in all (about 14 MB). The stamp is `summary_id` plus that vendor's count. Each caller gets its own copy of the rows.
- Rows read inside a write that is still under way are not kept, since it may be undone.
- The rows no longer carry `adjusted_confidence` and `requires_review`; nothing read them.
- `test_store_perf`'s read counts are adjusted, since rows are now kept between pages.

**Measured:**

| vendor's invoices | a check's two calls, before | after |
|---|---:|---:|
| 7,812 | 223 ms | 15 ms |
| 4,196 | 92 ms | 11 ms |
| 2,872 | 55 ms | 5 ms |
| 1,227 | 27 ms | 2 ms |

With the same four vendors in turn: check 93 → 66 ms, page 171 → 147 ms; the same invoice checked again: 134 → 85 ms. I measured this without adding the new triggers to the shared database (`cpu_interleave_ro.py`); its schema is unchanged.

### Merge (`558d81f`)
- Conflicts were only textual, in `core/store/summary.py` (the triggers' comment, and both agents' new triggers at the end of `SUMMARY_TRIGGERS`) and `docs/HOW_IT_RUNS.md` ("what a worker keeps").
- If you would rather resolve them yourself, merge `54187e7` instead.

### How I checked
- `same_behaviour.py --ref scale-int`: tax, approve and read all the same, after items 1-3 and again after item 4.
- `scripts/check.py` on all my files: OK (844 passed).
- On the merged tree: 2,434 default tests passed (core, coding, erp, web, meta, bench load), and 282 browser tests passed with `-m ""` (`test_web_invoice`, `test_web_review*`, `test_server_browser`, `test_web_picker`, `coding/test_split`, `test_web_viewer`).
- New tests cover:
  - the vendor list's route, seed, versions, ETag and gzip;
  - a two-Store test for the kept vendor list;
  - a browser test that the list is fetched once and a list opened early is redrawn;
  - the shared picture cache: shared by two workers, full disk, Windows rename refused, pruning;
  - the server route sharing pictures;
  - the poll's short answer, plus a browser test using the page clock;
  - three kept-answers tests for item 4.
- One flaky test, not caused by me: `test_web_picker.py::test_in_a_browser_a_code_or_a_name_typed_then_clicking_away_picks_its_account` failed 1-3 times in 8 runs even on `scale-int`'s own web code under the shared CPU. It passed in the final run. I did not fix it, so no B number is used.
- Docs: `HOW_IT_RUNS.md` (what a worker keeps; the `.cache/pages/` row in the data-folder table). My probe scripts and results are in `/tmp/claude-0/-home-user/b4fef7c4-f9cb-52fc-9da2-47ad67a7ebc2/scratchpad/goal_notes/w2b/`. I made no seed.

### Notes for later phases
- `reading_status` (`ai/reading.py:72`) costs 9 ms on every page, queue and poll; 5.5 ms of it is `supplier_templates_count` (`core/store/capture.py:887`) scanning `template_json`.
- `erp/jde_sync.py:115` updates every vendor's `erp_bank`/`erp_hold` on each sync. That resets the vendor list, `vendor_ids` and `master_numbers` in every worker each time.
- `summary_memo` could skip keeping answers inside a write that is still under way, as `vendor_invoices` now does (W2a's open note).
- The manager page's picture route (`web/pages/manager.py:100`) could use the shared picture cache too.
- In server mode the vendor list could be fetched only when first needed instead of at page open. That saves 370 KB per page, but the first picker opening would be slower.

### D-160 (text for decisions.md)
- **D-160** · **The review screen made light: the vendor list fetched and kept, page pictures drawn once for all workers, a reader poll of a few bytes, each vendor's invoices kept** (scale goal, phase 2 part 2).
  - **Why:** on the 100k database the 20,000-vendor list was rebuilt for every invoice opened (about 400 ms of a 500 ms page) and was 3.3 MB of its 3.6 MB. Each web worker drew the page pictures it was asked for (40 ms, 115 ms at scale 2) into its own 96 MB. The reader poll put its panel in again every 20 seconds. Every check read all of its vendor's invoices (220 ms for a vendor of 7,800).
  - **The vendor list:** `GET /review/vendors.json?v=<version>` (READ, as the screen). `invoice.vendor_list` keeps, per process and per vendor-list stamp (`summary_memo`, `changes/vendors`; without a vendor master also the `approved_vendors` topic): the JSON, it gzipped, its version (its hash), its count, and the first vendor of each vendor key and GST/HST business number. The page holds `#vendor-seed` (the address, enforced, the count, and the vendors it speaks of as it opens), so what the screen says of the vendor is right at once. An editable screen fetches the list as it opens. The fill from the list waits for it. An open vendor list is drawn again when it comes (`picker.refresh`).
  - **Browser caching:** on a laptop, `private, max-age=31536000, immutable` while `v` is the list's version (`no-cache` for another), with a weak ETag (304). On the server B-1070 stands: every answer behind the login is `no-store`, so each editable screen fetches the list from the worker's memo (370 KB gzipped, ~7 ms). Exempting it, or the page pictures, is a security decision left open (`identity._no_store`).
  - **Page pictures (server):** in `<data>/.cache/pages/<2>/`, named by the file's SHA-256, the page, the scale, the DPI, the size cap and `DRAWING`. Written under a name of its own, then `os.replace`; on Windows retried, and left when the same picture is there (B-1689's way). At most `PAGES_KEPT_BYTES` (1 GB), the oldest written removed first, down to three quarters, never the newest. A full disk draws each time. A worker keeps no picture in memory there. The laptop is unchanged.
  - **The reader poll:** `?seen=<tag>` (`reader_tag`; the page's `data-tag`). While the panel is what the page shows, the answer is `same`, without it, and nothing is drawn again.
  - **Each vendor's invoices:** `Store.vendor_invoices` kept per process for the vendors asked most recently (`VENDOR_ROWS_KEPT`, 20,000 rows, ~14 MB). Stamped by `summary_id` and `changes/vendor/<key>`, counted by three triggers on invoices (insert, delete, an update of what it lists). A check's confidence write is not listed. Read inside a write under way: afresh, not kept. The rows no longer carry `adjusted_confidence`/`requires_review` (read by nothing).
  - **Measured (100k, four vendors in turn):** page 544 → 147 ms and 3.61 MB → 300 KB; check 93 → 66 ms; a picture read back 8 ms against 40-115 ms drawn; a poll 210 → 66 bytes.
  - (`web/pages/invoice.py`, `web/document.py`, `web/roles.py`, `web/static/js/invoice.js`, `picker.js`, `templates/invoice.html`, `_invoice_document.html`, `core/store/vendors.py`, `core/store/summary.py`, `bench/load.py`; `tests/web/test_web_invoice.py`, `test_web_picker.py`, `test_web_document.py`, `test_server_web.py`, `test_server_security.py`, `tests/core/test_kept_answers.py`, `test_store_perf.py`, `tests/bench/test_bench_load.py`)
