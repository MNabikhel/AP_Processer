# ap_coder: the core package (70 flat modules)

The engine of AP Coder: the SQLite store, settings, the reading and coding pipeline, Canadian sales tax, checks,
ERP export files, reports, email intake and the command line. It runs fully offline (Azure is an opt-in exception).
It does not hold the document capture readers (`capture/`), the dashboard routes, templates and scripts (`web/`)
or the accuracy benchmark (`bench/`).

**Depends on:** `ap_coder/capture`, `ap_coder/web`, `ap_coder/bench`
**Used by:** `ap_coder/capture`, `ap_coder/web`, `ap_coder/web/pages`, `ap_coder/bench`

## Interface
- `Store()` (store.py): path (default `ap_coder.db` in the data folder, `paths.default_db_path`) -> the one SQLite store; `session()` shares one connection per thread.
- `load_sample_setup()` (store.py): store -> sample GL accounts, cost centers, tax GL mapping and coding policy from `data/`.
- `from_env()` (config.py, `Settings.from_env`): optional .env path -> `Settings` (Azure, local model, page reader).
- `load_reference_data()` (reference_data.py): files -> `ReferenceData` (GL, cost centers, tax setup) for prompts and checks.
- `InvoicePipeline` `process()` / `process_many()` (pipeline.py): file -> `PipelineResult` (extract, code, validate, save).
- `check_taxes()` (tax.py): coding, `TaxSetup` -> `TaxFinding` list; `build_gl_distribution()`: coding -> GL posting lines.
- GST/HST number: `valid_gst_number()` (format) and `gst_check_digit_ok()` (the CRA check digit) in tax.py, used by
  `check_taxes()` and taxreturn.py; capture has its own `normalize.luhn_ok()` (capture/normalize.py): change both together.
- `validate_coding()` (validation.py): coding, reference -> findings, adjusted confidence, needs-review flag.
- The AI reviewer (verifier.py): the pipeline calls `prepare()`, `hold()` (holds touchless approval) and `queue()`;
  page_worker's `_review_one()` calls `verify_one()` when OvisOCR2 has nothing to read; its settings key is `verifier`
  (JSON on/rounds/minutes). An agreed invoice is approved only through `workflow.still_touchless()` (capture) then `approve_invoice()`.
- The page reader queue (page_worker.py): `queue_new_invoice()`, `run_queue()`, `start_background()`.
- `build()` (exports.py): format, approved invoices -> (bytes, file name, mime); JDE files come from `build_zip()` (jde.py;
  Z-file columns and widths in docs/JDE_E1.md).
- `get_setting()` / `set_setting()` (store.py): the `settings` table, where numbers set on Settings pages live;
  the `.env` holds addresses and keys. Directory layouts: `presets()`, `save_preset()` (directory.py), key `directory_presets`
  (JSON list of {name, query}; `directory_default` names the one that opens first).
- `internet_allowed()` (offline.py): False unless `AP_ALLOW_INTERNET=1`; `InvoiceCoding` (schema.py) is the coding shape.
- Data it owns: store.py's tables `invoices`, `feedback`, `events`, `settings`, `gl_accounts`, `cost_centers`,
  `tax_treatments`, `vendors`, `export_batches`, `purchase_orders`, `po_lines`, `erp_invoices`, `coding_rules`,
  `invoice_capture`, `supplier_profiles`, `supplier_outcomes`, `reader_outcomes`, `lessons`, and the queues
  `page_reads`, `azure_checks`, `verifications`. Invoice status: review, approved, pending_approval, parked, rejected, failed.
- Settings: the `.env` in the data folder (paths.py, envfile.py, read by config.py), and `settings` table keys such as
  `approval_limit`, `fx_rates`, `policy_notes`, `default_terms_days`, `our_province`, `site_provinces`,
  `touchless_processing`, `touchless_limit`, `backup_copy_dir`, `insights_assumptions`, `jde_e1`, `directory_presets`.

## Groups
- Data/config: `store`, `config`, `paths`, `envfile`, `reference_data`, `csvio`, `safe`, `audit`. Used by every
  sub-folder and most core modules. Rule: only store.py opens the database and runs SQL (directory.py only builds its query text).
- AI and readers (incl. learning and accuracy): see Files. Used by `capture`, `web`, `web/pages`, `bench`; core: nearly all
  (`schema.InvoiceCoding`, `memory.vendor_key`). Rule: the local model server may be down; the queues (page reader, AI reviewer, Azure check) run in the background and never hold up import; they only hold touchless approval.
- Tax and coding: used by `web`, `web/pages`, `capture` (validation), `bench` (rules); core: `pipeline`, `store`,
  `exports`, `jde` and most reports. Rule: tax and checks are deterministic; the AI only reads what is printed.
- ERP and reports: used by `web/pages`; core: `jde` uses `exports`, `dupaudit`/`registers` use `statements`.
  Rule: a batch file is rebuilt from approved data, so it must download identical every time; text for people (CSV, xlsx) goes through safe.py, JDE Z-files stay as they are (B-26).
- Intake: used by `web/pages`, `bench` (mailbox, msgfile, triage), `capture` (intake); core: `cli`, `pipeline`. Rule: email files are untrusted: cap sizes before parsing.
- Launch and ops: used by `web`, `web/pages`, `capture` (offline, ui); core: the AI modules (`offline.internet_allowed`), `taxmath`/`azure_check` (`ui`).
  Rule: doctor and share_report never print setting values, vendor names or amounts.

## Gotchas and decisions
- This folder does too much: see the scale assessment, PR #24 (70 modules; store.py alone is about 3500 lines).
- `__init__.py` sets `ORT_DISABLE_TELEMETRY=1` first: keep it before any import of ONNX Runtime.
- A database made by a newer version is refused; an older one is backed up before migrating (store.py).

### Bug lessons
From docs/BUGS.md (one line each; the entry has the repro and the fix).
- B-01: Don't skip the rename step for the exact standard name. `tests/test_reference_data.py::test_header_that_spells_the_standard_name_is_renamed`
- B-02: Don't use a sniffed dialect's quoting rules after sniffing one line. `tests/test_real_world.py::test_doubled_quotes_in_a_csv_cell_are_read_as_one_quote`
- B-03: Don't pass a Python bool to IS NULL. `tests/test_verifier.py::test_a_review_that_ends_without_a_result_keeps_the_last_result`
- B-04: Don't compare non-ASCII text with SQLite `lower()`. `tests/test_end_to_end_sweep_fixes.py::test_approved_today_matches_accented_names_without_case`
- B-05: Don't leave a setup step's changes uncommitted on a connection handed to callers. `tests/test_store_session.py::test_a_database_made_again_can_take_the_write_lock_at_once`
- B-06: Don't let a settings file expand or split values the user typed. `tests/test_envfile_edge_cases.py::test_a_saved_value_with_a_dollar_sign_reads_back_as_typed`
- B-07: Don't let the base currency's rate be overridden. `tests/test_spend.py::test_a_cad_rate_typed_in_is_ignored`
- B-08: Refuse bad input where it is typed, not with an error page. `tests/test_web_settings.py::test_a_setting_typed_with_a_line_break_is_saved_as_one_line`
- B-09: Don't trust a `>` comparison on money that may be NaN. `tests/test_second_approval.py::test_a_total_that_is_not_a_number_needs_the_second_approver`
- B-11: Don't bind an unbounded list as separate SQL variables. `tests/test_exports_bulk.py::test_every_list_query_takes_more_ids_than_sqlite_takes_variables`
- B-12: Don't let file order decide which effective-dated row wins. `tests/test_tax.py::test_a_new_rate_row_wins_over_an_old_one_left_open`
- B-13: Don't compute place of supply twice with different rules. `tests/test_taxreturn.py::test_self_assessment_uses_the_place_of_supply_of_the_tax_checks`
- B-14: Don't treat 0 differently from blank for ERP quantities and prices. `tests/test_po.py::test_lump_sum_line_exported_with_zero_quantity_and_price_is_amount_only`
- B-15: Don't build the legal-form list from English only in a bilingual market. `tests/test_memory_store.py::test_vendor_key_ignores_french_legal_forms`
- B-16: Don't compare a registration number more strictly than the vendor list stores it. `tests/test_vendors.py::test_qst_numbers_compare_by_their_ten_digits`
- B-17: Don't read a number before "month" as days. `tests/test_terms.py::test_net_months_are_calendar_months_never_days`
- B-18: Don't let a regex start matching in the middle of a number. `tests/test_terms.py::test_a_half_percent_discount_is_half_a_percent`
- B-19: Don't hard-code a regulatory threshold without the date it applies from. `tests/test_taxreturn.py::test_registration_number_needed_from_100_dollars_since_2021`
- B-20: Don't throw away the % sign before deciding the unit. `tests/test_tax.py::test_a_rate_written_with_a_percent_sign_is_always_a_percentage`
- B-22: Don't treat "same vendor and number" as "same bill" unless we actually sent it. `tests/test_dupaudit.py::test_an_unexported_invoice_already_in_the_erp_is_a_duplicate`
- B-23: Don't write document text to a workbook without neutralising it. `tests/test_jde_readiness.py::test_the_worksheet_holds_no_formula_from_a_document`
- B-24: Don't compare two records with lookups of different strength. `tests/test_jde.py::test_an_earlier_export_known_only_by_its_gst_number_is_a_duplicate`
- B-25: Don't read meaning into free-text columns. `tests/test_statements.py::test_a_payment_word_in_a_description_does_not_turn_an_invoice_into_a_payment`
- B-26: Don't apply Excel quoting to files meant for a database. `tests/test_jde.py::test_z_files_hold_the_text_as_it_is_without_spreadsheet_quoting`
- B-27: Don't accrue a bill you have already flagged as a duplicate. `tests/test_accruals.py::test_a_resent_copy_of_a_bill_already_counted_is_not_accrued_again`
- B-29: Don't bind an unbounded id list as separate SQL variables. `tests/test_directory.py::test_quick_search_with_more_hits_than_sqlite_takes_variables`
- B-30: Don't bind an unbounded id list as separate SQL variables. `tests/test_exports_bulk.py::test_a_batch_of_more_invoices_than_sqlite_takes_variables`
- B-31: Don't let a later round's confidence stand in for items that were never answered. `tests/test_verifier.py::test_agreed_needs_every_item_agreed_in_a_confident_round`
- B-32: Don't make approval gates depend on the order they were applied in. `tests/test_verifier.py::test_every_hold_is_recorded_whatever_the_order`
- B-33: Don't approve from a copy of the row read minutes earlier. `tests/test_verifier.py::test_a_reopen_during_the_review_keeps_aps_corrections`
- B-34: Don't treat a model's scratch work as its decision. `tests/test_local_llm.py::test_a_strict_reply_never_takes_a_draft_from_the_thinking`
- B-35: Don't accept a stream as finished unless the server said so. `tests/test_page_reader.py::test_a_reading_that_ends_without_a_finish_reason_is_not_a_whole_page`
- B-36: Don't check a time budget only between blocking calls. `tests/test_verifier.py::test_each_call_is_given_only_the_time_left`
- B-37: Don't infer a capability from an error that didn't name it. `tests/test_local_llm.py::test_a_400_that_is_not_about_the_format_keeps_structured_output`
- B-38: Don't build a prompt from data without a length budget. `tests/test_local_llm.py::test_a_long_invoice_is_suggested_within_the_prompt_budget`
- B-39: Don't classify hosts by their spelling; parse them the way the resolver will. `tests/test_local_llm.py::test_an_internet_address_written_another_way_is_still_refused`
- B-40: Don't regex-strip markup out of text that might be JSON. `tests/test_local_llm.py::test_a_thinking_tag_inside_a_json_string_is_kept`
- B-41: Don't let a fallback parser undo the guard the main parser enforces. `tests/test_local_llm.py::test_a_reply_cut_off_without_the_length_reason_is_not_read_as_its_first_line`
- B-48: Don't delete what you should fold. `tests/test_evaluation.py::test_accented_names_match_their_unaccented_spelling`
- B-56: Don't check a state in one step and write it in another without a condition on the write. `tests/test_web_finance.py::test_two_undos_of_a_batch_at_once_record_one_undo`
- B-61: Don't bound each piece and forget the sum. `tests/test_msg_and_triage.py::test_attachments_sharing_one_stream_cannot_fill_the_memory`
- B-62: Don't put a per-call limit on a walk that runs once per node. `tests/test_msg_and_triage.py::test_a_directory_walk_reads_each_entry_once`
- B-63: Don't hand an untrusted zip to a library before checking its unzipped sizes. `tests/test_msg_and_triage.py::test_a_spreadsheet_that_unzips_to_far_more_is_not_opened`
- B-64: Don't size-cap the parts of a file name and then join them uncapped. `tests/test_mailbox.py::test_a_long_chinese_email_and_attachment_name_still_fit_one_file_name`
- B-65: Don't log "done" when the step that makes it done failed. `tests/test_mailbox.py::test_an_email_that_cannot_be_moved_is_logged_once`
- B-66: Don't work out the same path two ways. `tests/test_config_and_cli.py::test_watch_uses_the_dashboard_invoices_folder`
- B-67: Don't let the doctor check less than the code it vouches for. `tests/test_offline.py::test_doctor_checks_the_model_files_as_the_app_does`
- B-68: Don't leave doctor wording behind when a dependency's role grows. `tests/test_config_and_cli.py::test_doctor_fails_without_pillow`
- B-69: Don't assume a file's timestamp is in the past. `tests/test_controls_watch.py::test_a_file_stamped_in_the_future_is_picked_up`
- B-70: Don't print an error and then return success. `tests/test_config_and_cli.py::test_a_command_that_could_not_read_something_does_not_exit_0`
- B-71: Don't apply the limit after the costly parse. `tests/test_mailbox.py::test_an_email_of_thousands_of_tiny_parts_is_refused_before_it_is_parsed`
- B-72: Don't apply the "still copying" check to files your own process just wrote. `tests/test_controls_watch.py::test_watch_once_processes_the_invoices_it_just_took_out_of_emails`

## Files
- Data/config: `store.py` (SQLite store), `config.py` (Settings from env), `paths.py` (data folder), `envfile.py` (.env edits); `reference_data.py` (GL, tax setup), `csvio.py` (Excel CSVs); `safe.py` (formula-safe cells), `audit.py` (approval diffs, events).
- AI models: `inference.py` (GL coding model), `local_llm.py` (LM Studio, Ollama); `prompts.py` (system prompt), `schema.py` (JSON schema, InvoiceCoding).
- Readers: `page_reader.py` (OvisOCR2), `page_worker.py` (its queue); `reading.py` (how invoices are read), `extraction.py` (Azure Document Intelligence); `imaging.py` (page images), `figures.py` (two readings compared).
- Second opinions: `verifier.py` (AI reviewer), `azure_check.py` (opt-in Azure check).
- Learning: `memory.py` (reviewer decisions), `lessons.py` (corrections per situation), `history.py` (past ERP coding).
- Coding: `pipeline.py` (extract, code, validate, save), `offline_coder.py` (coding with no model); `suggest.py` (GL from history), `line_match.py` (line similarity).
- Accuracy: `labels.py` (ground-truth workbook), `evaluation.py` (accuracy); `training_export.py` (fine-tuning ZIP).
- Tax: `tax.py` (rates, checks, GL distribution), `taxmath.py` (invoice math again); `taxreturn.py` (ITC/ITR claims), `validation.py` (post-inference checks).
- Coding rules: `vendors.py` (vendor master, fraud signals), `po.py` (PO matching); `rules.py` (fixed rules), `terms.py` (payment terms); `recurring.py` (late recurring vendors), `review.py` (form back to coding); `bulk.py` (bulk approval), `vendor_mail.py` (request email to a vendor).
- ERP: `exports.py` (xlsx/csv batches), `export_layout.py` (custom CSV); `jde.py` (JDE Z-files), `jde_worksheet.py` (JDE questions workbook); `registers.py` (ERP invoice register), `stamp.py` (approved PDFs).
- Reports: `accruals.py` (month-end), `statements.py` (statement reconciliation); `dupaudit.py` (duplicate payments), `controls.py` (audit exceptions); `spend.py` (spend, data download), `insights.py` (business case); `directory.py` (invoice table), `search.py` (quick search).
- Intake: `intake.py` (images to PDF), `mailbox.py` (unpack emails); `msgfile.py` (.msg reader), `triage.py` (sort emails); `inbox.py` (emails set aside).
- Launch and ops: `cli.py` (commands), `__main__.py` (python -m ap_coder), `__init__.py` (version, telemetry off); `doctor.py` (readiness report), `offline.py` (offline guard, OCR models); `demo.py` (sample invoices), `share_report.py` (redacted summary); `help.py` (help per check), `ui.py` (HTML pieces).
- `capture/`: document capture readers (own CLAUDE.md). `web/`: the dashboard (own CLAUDE.md). `bench/`: accuracy benchmark (own CLAUDE.md).
- `assets/`: icon and logo SVGs. `static/`: Inter and Material Symbols fonts with licences (data folders, no CLAUDE.md).
