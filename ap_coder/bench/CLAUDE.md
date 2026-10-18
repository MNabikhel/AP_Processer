# ap_coder/bench: benchmark generator and harnesses

Makes random, reproducible invoices and emails with ground truth, plays them through the app's own code and reports accuracy and speed: capture fields, supplier learning and autonomy, GL coding with no AI model, email triage, seconds per invoice. It also fits capture's confidence table (`ap_coder/capture/calibration.json`). It does not read invoices itself (`ap_coder/capture`) and is not part of the review app, apart from one runtime use of `scan()`. Everything is made up and offline.

**Depends on:** `ap_coder`, `ap_coder/capture`, `ap_coder/web`
**Used by:** `ap_coder`

## Interface
- Run: `python -m ap_coder.bench run --n 300 --seed 1 --scanned 0.3 [--out bench_out] [--workers N] [--regen] [--reader capture|stub|auto]` -> report.json, report.md, failures.jsonl, evidence.jsonl.
- Other commands: `generate`, `calibrate RUN_DIR...`, `learn`, `speed`, `coding`, `triage` (`python -m ap_coder.bench -h`; each writes into its own `--out`, but `calibrate` writes `calibration.json` or `--dest`).
- `run()`, `prepare_cases()`, `fit_calibration()` (harness.py): generate or reuse cases, score, write the calibration table.
- `generate()`, `make_case()`, `make_stream_case()`, `scan()` (generator.py): PDF or PNG cases and `<id>.truth.json`.
- `score_case()`, `summarize()` (scoring.py): a `CaptureResult` against the truth.
- `run_learning()` (learning.py), `run_coding()` (coding.py), `run_triage()` (emails.py), `run_speed()` (speed.py).
- `build_msg()` (msgwriter.py): writes Outlook .msg files; tests use it.
- What ships: the whole folder is in the `ap_coder` package and the offline bundle, but only `scan()` runs in the app (`page_reader.test_pages`, the page reader's test). The rest is for the CLI and tests (tests/test_bench.py, tests/test_bench_speed.py, tests/test_msg_and_triage.py, ...).

## Gotchas and decisions
- Layering slip (scale assessment): `ap_coder.page_reader` imports `bench.generator` (`scan()` in `test_pages`); changing `scan()` changes the page reader's test pages.
- Layering slip (scale assessment): `bench.coding` imports `web.invoice_view` (`suggestions`), so the benchmark depends on the web layer.
- Each case's random seed includes `GENERATOR_VERSION`: bump it and every case changes, and cached cases are regenerated (manifest.json).
- `calibrate` rewrites `calibration.json` from only the runs it is given: pass every run (see its "runs" list), or the measured patterns are lost.
- The harnesses call the app's real code (`capture.analyze`, `offline_coder`, `Store`, `mailbox.unpack`): a benchmark change of numbers usually means an app change.
- The `__main__.py` docstring lists fewer commands than the parser has (`generate`, `triage` are missing).

## Files
- `__init__.py`: package docstring.
- `__main__.py`: the CLI (argparse sub-commands).
- `generator.py`: random invoices, their rendering, scans and truth; supplier streams.
- `content.py`: parties, numbers, taxes, lines, dates and money styles; `wording.py`: English and French labels and titles.
- `harness.py`: the capture run, the stub reader, evaluation, calibration fitting, the report.
- `scoring.py`: per-field scoring and summary metrics.
- `learning.py`: supplier learning and autonomy simulation.
- `coding.py`: GL coding with no AI model, from AP's approvals.
- `emails.py`: email triage benchmark; `msgwriter.py`: Outlook .msg writer.
- `speed.py`: seconds per invoice through the pipeline.
