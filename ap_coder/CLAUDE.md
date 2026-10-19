# ap_coder: the package (six core folders, capture, the dashboard, the benchmark)

The engine of AP Coder, fully offline (Azure is an opt-in exception). The top level holds only the package itself
(version, telemetry off, `python -m ap_coder`) and, for one release, the old flat modules' paths as aliases. The code
is in its folders; each has its own CLAUDE.md. Who imports each module: `MODULES.md` (generated).

**Depends on:** `ap_coder/ops`
**Used by:** `ap_coder/core`, `ap_coder/web`, `ap_coder/web/pages`

## Folders
- `core/`: the SQLite store, settings (`.env`, user settings), data folder, reference data, coding schema, learning memory's vendor key, safe cells, CSVs, audit labels, HTML pieces.
- `ai/`: the pipeline (read, code, check, save), offline coder, local and Azure models, page reader and its queue, AI reviewer, Azure check, lessons, history, suggestions, accuracy.
- `coding/`: Canadian sales tax, validation, vendor master, PO matching, fixed rules, payment terms, review form, bulk approval, vendor request emails.
- `erp/`: export files, JD Edwards Z-files and worksheet, ERP register, stamped PDFs; accruals, statements, duplicate audit, controls, spend, insights, recurring vendors, directory, quick search.
- `intake/`: pictures made into PDFs, saved emails unpacked and sorted, the emails set aside.
- `ops/`: the command line, doctor, offline guard and OCR models, demo, check help, share report.
- `capture/` (document capture), `web/` (the dashboard), `bench/` (the accuracy benchmark).

## Interface
- `__version__` (`__init__.py`, `_version()`): the version in pyproject.toml next to this folder, else the installed metadata.
- `python -m ap_coder <command>` (`__main__.py`): `ops/cli.py`'s `main`; the `ap-coder` script is `ap_coder.ops.cli:main`.
- Old paths (`ap_coder.store`, `ap_coder/tax.py`...): each is an alias, `sys.modules[__name__]` set to the moved module,
  so `import`, `from ... import` and monkeypatching through the old path all reach the one real module. They go in a later release (D-33).

## Flows
- An invoice: email `mailbox.unpack_folder()` -> `mailbox.unpack()` (intake) -> invoices folder -> `cli.watch_once()` (ops) or web
  `process_files()` -> `InvoicePipeline.process()` (ai): `offline_coder.read_invoice()`, `offline_coder.code_from_capture()`; then
  `rules.apply()`, `tax.check_taxes()`, `validation.validate_coding()` (coding), saved by the store (core) as `review` and queued (or approved touchless).
- Approved -> `exports.build()` or `jde.build_zip()` (erp). A check code: raised in coding, ai or capture; explained in `ops/help.py`; shown by web/invoice_view.py.

## Gotchas and decisions
- `__init__.py` sets `ORT_DISABLE_TELEMETRY=1` before any ONNX import, and imports nothing else: scripts/ import `ap_coder.core.paths`,
  `ap_coder.core.envfile` and `ap_coder.ops.offline` before any package is installed. Keep the six folders' `__init__.py` empty for the same reason.
- An alias module is never edited: change the module it names. A new module goes in its folder, never at the top.
- `intake` is now a package, so `ap_coder/intake.py` has no alias file: `intake/__init__.py` forwards its old names to `intake/pictures.py`.
- An update unzipped over an older version overwrites the flat modules with the aliases; `ap_coder/intake.py` is pruned
  (`scripts/upgrade.py` `prune_stale`), and until then the package `intake/` wins over it.
- scripts/ci/old_versions.py keeps the flat imports on purpose: its seed runs inside an older version's own code.

## Files
- `__init__.py` (version, telemetry off), `__main__.py` (python -m ap_coder). `MODULES.md`: the module index (`python scripts/check_map.py --modules`).
- `core/`, `ai/`, `coding/`, `erp/`, `intake/`, `ops/`, `capture/`, `web/`, `bench/`: own CLAUDE.md. `assets/` (icon, logo) and `static/` (fonts, licences): data folders, no CLAUDE.md.
- Aliases of the old paths, one release: `accruals.py` `audit.py` `azure_check.py` `bulk.py` `cli.py` `config.py` `controls.py` `csvio.py`
  `demo.py` `directory.py` `doctor.py` `dupaudit.py` `envfile.py` `evaluation.py` `export_layout.py` `exports.py` `extraction.py` `figures.py`
  `help.py` `history.py` `imaging.py` `inbox.py` `inference.py` `insights.py` `jde.py` `jde_worksheet.py` `labels.py` `lessons.py` `line_match.py`
  `local_llm.py` `mailbox.py` `memory.py` `msgfile.py` `offline.py` `offline_coder.py` `page_reader.py` `page_worker.py` `paths.py` `pipeline.py`
  `po.py` `prompts.py` `reading.py` `recurring.py` `reference_data.py` `registers.py` `review.py` `rules.py` `safe.py` `schema.py` `search.py`
  `share_report.py` `spend.py` `stamp.py` `statements.py` `store.py` `suggest.py` `tax.py` `taxmath.py` `taxreturn.py` `terms.py`
  `training_export.py` `triage.py` `ui.py` `validation.py` `vendor_mail.py` `vendors.py` `verifier.py`.
