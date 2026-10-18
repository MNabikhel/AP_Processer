# ap_coder: the core package (70 flat modules)

The engine of AP Coder: the SQLite store, settings, the reading and coding pipeline, Canadian sales tax, checks,
ERP export files, reports, email intake and the command line. It runs fully offline (Azure is an opt-in exception).
Not here: the document capture readers (`capture/`), the dashboard (`web/`), the accuracy benchmark (`bench/`).

**Depends on:** `ap_coder/capture`, `ap_coder/web`, `ap_coder/bench`
**Used by:** `ap_coder/capture`, `ap_coder/web`, `ap_coder/web/pages`, `ap_coder/bench`

## Interface
- `Store()` (store.py): path (default `ap_coder.db` in the data folder) -> the one SQLite store; `session()` shares one
  connection per thread. `load_sample_setup()`: the sample GL, cost centers, tax GL mapping and policy from `data/`.
- `from_env()` (config.py, `Settings.from_env`): .env path, else `paths.env_file()` -> `Settings`; `override=False`: the OS environment wins, so a saved value also goes into `os.environ`.
- `load_reference_data()` (reference_data.py): files -> `ReferenceData` (GL, cost centers, tax setup, policy) for prompts and checks.
- `InvoicePipeline` `process()` / `process_many()` (pipeline.py): file -> `PipelineResult` (read, code, check, save,
  approve touchless at save); with `page_text`, `invoice_id`, `pages_read`, `save=False`: the re-read after the page reader.
- `finalise_coding()` (pipeline.py): coding -> (output with `gl_distribution`, report); the pipeline, review screen, bulk and demo call it.
- `check_taxes()` (tax.py): coding, `TaxSetup` -> `TaxFinding` list; `build_gl_distribution()`: (coding, `TaxSetup`) ->
  posting lines that add up to the total (tax spread in whole units for a currency without cents, e.g. JPY); `supply_source()`: (province, where it came from), the one place-of-supply rule; `place_of_supply()` gives its province (B-13).
- GST/HST number: `valid_gst_number()`, `gst_check_digit_ok()` (tax.py; taxreturn.py uses them); capture's `normalize.luhn_ok()` repeats the check digit: change both.
- `validate_coding()` (validation.py): coding, reference and the vendor, PO, terms and duplicate findings -> a report.
- Second opinions: the pipeline calls `prepare()`, `hold()` and `queue()` of verifier.py (AI reviewer, key `verifier`) and azure_check.py
  (own thread, `azure_check.start_background()`). `verify_one()` runs in the page reader's thread when OvisOCR2 has nothing to read or isn't ready.
- Page reader queue (page_worker.py): `queue_new_invoice()`, `read_one()`, `run_queue()`, `start_background()`; reads once the self-test passed (`confirmed()`).
- `build()` (exports.py): format, approved invoices, GL names, batch, vendor ids, layout -> (bytes, name, mime);
  `fmt="jde"` raises ValueError: JDE files come from `build_zip()` (jde.py; columns in docs/JDE_E1.md).
- `internet_allowed()` (offline.py): `AP_ALLOW_INTERNET` is 1, true, yes or on; `InvoiceCoding` (schema.py): the coding shape.
- Data it owns: store.py's tables `invoices`, `feedback`, `events`, `settings`, `gl_accounts`, `cost_centers`, `tax_treatments`,
  `vendors`, `export_batches`, `purchase_orders`, `po_lines`, `erp_invoices`, `coding_rules`, `invoice_capture`, `supplier_profiles`,
  `supplier_outcomes`, `reader_outcomes`, `lessons`; queues `page_reads`, `azure_checks`, `verifications`. Status: review, approved, pending_approval, parked, rejected, failed.
- Settings: the `.env` (addresses, keys); per user `~/.ap_coder/settings.json` (`AP_USER_SETTINGS`: reviewer name, data folder, installs;
  `paths.read_user_settings()`); the `settings` table (`get_setting()` / `set_setting()`): `approval_limit`, `fx_rates`, `policy_notes`,
  `default_terms_days`, `our_province`, `site_provinces`, `touchless_processing`, `touchless_limit`, `verifier`, `insights_assumptions`, `jde_e1`,
  `export_layout`, `directory_presets` (JSON [{name, query}]), `directory_default`, `backup_copy_dir` (a restore clears it), `why_after`, `azure_check_*`, `page_reader_test`, `page_reader_figures`. Never edit or reset `schema_version`, `vendor_key_rules`, `backup_copy_status`, `page_reader_testing` (the self-test's cross-process lock), `demo_vendor_master`, `demo_purchase_orders`.

## Flows (who imports each module: `MODULES.md`, generated)
- An invoice: email `mailbox.unpack_folder()` -> `mailbox.unpack()` (.msg via `msgfile.read_msg()`; sorted by `triage.classify_email()`; the email then moves to `emails/`, or
  `emails/needs a person/`; statements to `statements/`) -> invoices folder ->
  `cli.watch_once()` or web `process_files()` -> `InvoicePipeline.process()`: offline, `offline_coder.read_invoice()` and
  `code_from_capture()` (the chat model only suggests leftover lines and reads the header); with Azure, `capture_invoice()`
  and `InvoiceCoder.code()`; then AP's fixed rules (`rules.apply()`, they win over the coder), `check_taxes()`, `validate_coding()`, saved as `review` and queued (or approved touchless).
- Place of supply: `place_of_supply()` (tax.py; printed ship-to, else the cost center's site in `site_provinces` "CC=PROV" lines, else `our_province`,
  vendor last); taxreturn.py uses the same (B-13). Terms: `payment()` (terms.py; printed, else the vendor master's, else `default_terms_days`).
- A check code: raised in tax.py, validation.py, vendors.py (`tax_number_findings()`), po.py or capture; explained in
  help.py `CHECKS` (tests/test_help.py fails on a code with no help); shown by web/invoice_view.py.
- Rules: only store.py opens the database (directory.py builds query text); id lists are one JSON parameter (B-11); the queues never hold up
  import: they hold touchless approval, add their checks (VERIFIER_*, AZURE_DI_*), and the page reader replaces an untouched proposal; tax and
  checks are deterministic, the AI only reads what is printed; email files are untrusted (cap sizes before parsing); a batch re-downloads the
  same invoices with today's settings and master data; CSV/xlsx files for people go through safe.py (HTML is escaped by Jinja and ui.py); loader files (JDE Z-files, CSV, Custom CSV) hold
  text as it is (B-26, B-92); doctor and share_report never print values or amounts.

## Gotchas and decisions
- This folder does too much (scale assessment, PR #24). `__init__.py` sets `ORT_DISABLE_TELEMETRY=1` before any ONNX import. A newer version's database is refused; an older one is backed up before migrating.
- Store methods that run `BEGIN IMMEDIATE` (`create_export_batch`, `next_page_read`, `next_azure_check`, `next_verification`,
  `update_after_check`, `replace_proposal`, `delete_invoice(keep_approved=True)`) fail inside an open `session()` write.
- `get_invoice` can write (a moved file is re-pointed, logged `file_found`). `feedback_rows` is cached: feedback rows are only added or deleted, never updated.
- A new event action needs its `audit.ACTIONS` entry (tests/test_audit.py fails without it).
- Changing `memory.vendor_key` means bumping `VENDOR_KEY_RULES` (the store recomputes stored keys); invoice.js has a copy (parity test).
- A stored `ai_output` / `final_output` holds `gl_distribution`: strip it before `InvoiceCoding.model_validate` (extra fields are refused). A worked-out `place_of_supply` sits beside `ship_to_province`, never in it; the model drops it.
- A check that must always reach a person goes in `capture/supplier.ALWAYS_A_PERSON` (else bulk.py approves an info-level one unseen); one a vendor can fix in `vendor_mail.ASKABLE` (never a fraud or hold code).
- A late approval (Azure, AI reviewer) needs every hold cleared (`verifier.HOLDS`) and goes through `still_touchless()`.

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
- B-73: Don't resolve a key collision by keeping one row when the other row holds a control. `tests/test_memory_store.py::test_rekeyed_vendor_rows_merge_and_on_hold_wins`
- B-74: Don't decide "thousands separator" without checking the groups are real thousands. `tests/test_safety.py::test_parse_amount_reads_thousands_only_from_real_thousands_groups`
- B-75: Don't keep a hand list of audit actions apart from the actions the store logs. `tests/test_audit.py::test_every_logged_action_has_a_label_and_a_description`
- B-76: Don't cut a date at 10 characters. `tests/test_history.py::test_history_dates_are_read_not_cut_at_ten_characters`
- B-77: Don't round ground truth on the way out. `tests/test_ops_sweep_fixes.py::test_labels_keep_quantities_and_unit_prices_unrounded`
- B-78: Don't read a file people save in Notepad without `utf-8-sig`. `tests/test_reference_data.py::test_coding_policy_saved_with_a_byte_order_mark_keeps_its_comment_line`
- B-79: Don't store a value you worked out in the field that means "printed on the invoice". `tests/test_tax.py::test_a_worked_out_place_of_supply_follows_the_recoded_lines`
- B-80: Don't treat "no warnings" as "safe to approve unseen" when some person-only checks are info-level. `tests/test_exports_bulk.py::test_bulk_approve_never_takes_a_check_that_always_needs_a_person`
- B-81: Don't reuse one variable for two records when each carries its own controls. `tests/test_vendors.py::test_a_hold_on_the_name_survives_a_match_by_tax_number`
- B-82: Don't re-implement a check with different rounding rules from the one it mirrors. `tests/test_math_check.py::test_a_yen_invoice_is_rounded_to_whole_yen_as_the_tax_checks_do`
- B-83: Don't veto a match using words that belong to another clause. `tests/test_terms.py::test_interest_words_of_another_rate_do_not_veto_the_discount`
- B-84: Don't take `float()` succeeding as proof that the text is a number. `tests/test_tax.py::test_a_rate_that_is_not_a_finite_number_is_refused`
- B-85: Don't show a column under a label it doesn't mean. `tests/test_jde_readiness.py::test_the_dry_run_gross_is_the_whole_invoice_in_every_amount_mode`
- B-86: Don't fix one misreading of free text by trusting another field without the sign. `tests/test_statements.py::test_a_payment_line_with_the_invoices_number_is_a_payment`
- B-87: Don't let a file that skipped re-checking hit code that assumes the checks ran. `tests/test_jde.py::test_a_due_date_jde_cannot_hold_leaves_vlddj_blank_in_a_batch_downloaded_again`
- B-89: Don't compare non-ASCII text with SQLite `lower()` (B-04 again). `tests/test_directory.py::test_text_filters_ignore_accents_and_case_beyond_ascii`
- B-90: Don't collect codes in cells that Excel will turn into numbers. `tests/test_jde_readiness.py::test_the_worksheet_answer_and_code_cells_are_text`
- B-91: Don't put back raw text in the one branch after redacting it in the others. `tests/test_working_together.py::test_share_report_failure_category_never_prints_a_file_name`
- B-92: Don't apply Excel quoting to files meant for a database (B-26 again). `tests/test_exports_bulk.py::test_erp_csv_keeps_text_exactly_as_typed`
- B-93: Don't treat "nothing compared" as "nothing differs". `tests/test_azure_check.py::test_nothing_compared_is_not_nothing_differs`
- B-94: Don't record the server's outage as the reviewer's verdict. `tests/test_verifier.py::test_the_servers_outage_is_not_the_reviewers_verdict`
- B-95: Don't silence a helper's exception without a log line. `tests/test_verifier.py::test_the_reviewer_is_shown_the_math`
- B-96: Don't let the strict path fall back to scanning prose for JSON. `tests/test_local_llm.py::test_a_strict_reply_is_the_whole_answer_not_json_found_in_prose`
- B-97: Don't let a coverage test match only one spelling of the thing it covers. `tests/test_help.py::test_every_check_has_help`
- B-103: Don't assume Unicode decomposition folds every letter. `tests/test_evaluation.py::test_ligatures_and_sharp_s_fold_to_their_letters`
- B-116: Don't make the result of a graph walk depend on the order the nodes are stored in. `tests/test_msg_and_triage.py::test_the_order_of_directory_entries_never_drops_an_attachment`
- B-117: Don't apply a "most recent N" window before the filter that decides what is still open. `tests/test_msg_and_triage.py::test_an_open_email_stays_in_needs_a_person_however_many_were_sorted_after_it`
- B-126: Don't fix a repeated mistake one place at a time: write a check that finds every place, then fix what it finds. `tests/test_directory.py::test_a_status_filter_with_more_statuses_than_sqlite_takes_variables`

## Files
- Data/config: `store.py` (SQLite store), `config.py` (Settings from env), `paths.py` (data folder, user settings), `envfile.py` (.env edits); `reference_data.py` (GL, cost centers, tax setup, policy, prompt context), `csvio.py` (Excel CSVs, dates); `safe.py` (formula-safe cells, the shared amount parser), `audit.py` (approval diffs, event labels).
- AI models: `inference.py` (Azure coding; the chat model's header read and line suggestions), `local_llm.py` (LM Studio, Ollama); `prompts.py` (whole-invoice coding prompt), `schema.py` (JSON schema, InvoiceCoding).
- Readers: `page_reader.py` (OvisOCR2), `page_worker.py` (its queue); `reading.py` (the readers' status), `extraction.py` (Azure Document Intelligence); `imaging.py` (page images), `figures.py` (two readings compared).
- Second opinions: `verifier.py` (AI reviewer), `azure_check.py` (opt-in Azure check). Learning: `memory.py` (reviewer decisions, vendor key), `lessons.py` (corrections per situation), `history.py` (past ERP coding).
- Coding: `pipeline.py` (read, code, check, save), `offline_coder.py` (how every invoice is coded offline); `suggest.py` (GL from history), `line_match.py` (line similarity). Accuracy: `labels.py` (ground-truth workbook), `evaluation.py` (accuracy), `training_export.py` (fine-tuning ZIP).
- Tax: `tax.py` (rates, checks, GL distribution), `taxmath.py` (invoice math again); `taxreturn.py` (ITC/ITR claims, PST/QST self-assessment), `validation.py` (post-inference checks).
- Coding rules: `vendors.py` (vendor master, fraud signals), `po.py` (PO matching); `rules.py` (fixed rules), `terms.py` (payment terms); `recurring.py` (late recurring vendors), `review.py` (form back to coding, line splits); `bulk.py` (bulk approval), `vendor_mail.py` (request email to a vendor).
- ERP: `exports.py` (xlsx/csv batches), `export_layout.py` (custom CSV); `jde.py` (JDE Z-files), `jde_worksheet.py` (JDE questions workbook); `registers.py` (reads an ERP register's columns; the register is in store.py), `stamp.py` (approved PDFs).
- Reports: `accruals.py` (month-end), `statements.py` (statement reconciliation); `dupaudit.py` (duplicate payments), `controls.py` (audit exceptions); `spend.py` (spend, data download), `insights.py` (business case, operations, vendor workload); `directory.py` (invoice table), `search.py` (quick search).
- Intake: `intake.py` (images to PDF), `mailbox.py` (unpack emails); `msgfile.py` (.msg reader), `triage.py` (sort emails); `inbox.py` (emails set aside).
- Launch and ops: `cli.py` (commands), `__main__.py` (python -m ap_coder), `__init__.py` (version, telemetry off); `doctor.py` (readiness report), `offline.py` (offline guard, OCR models); `demo.py` (sample invoices), `share_report.py` (redacted summary); `help.py` (help per check), `ui.py` (HTML pieces).
- `MODULES.md`: the module index (`python scripts/check_map.py --modules`). `capture/`, `web/`, `bench/`: own CLAUDE.md. `assets/` (icon, logo) and `static/` (fonts, licences): data folders, no CLAUDE.md.
