# ap_coder/core: the foundations (store, settings, data folder, reference data, schema, memory)

What every other folder builds on: the one SQLite store, the settings (`.env`, the per-user settings file, the
`settings` table), the data folder, the GL / cost center / tax reference data, the coding schema, the learning memory's
vendor key, the spreadsheet-safe and CSV helpers, the audit trail's labels and the dashboard's HTML pieces.
Grouped here from the flat `ap_coder/` (docs/decisions.md D-33): `ap_coder.store` and the other old paths are aliases for one release.

**Depends on:** `ap_coder`, `ap_coder/ai`, `ap_coder/capture`, `ap_coder/coding`
**Used by:** `ap_coder/ai`, `ap_coder/bench`, `ap_coder/capture`, `ap_coder/coding`, `ap_coder/erp`, `ap_coder/ops`, `ap_coder/web`, `ap_coder/web/pages`

## Interface
- `Store()` (store.py): path (default `ap_coder.db` in the data folder) -> the one SQLite store; `session()` shares one
  connection per thread. `load_sample_setup()`: the sample GL, cost centers, tax GL mapping and policy from `data/`.
  `EVENT_ORIGIN` (store.py): a contextvar; set (web/identity.py, a colleague on the network), every event's detail gets `address`: theirs (never `from`, which events use for an old value: B-129).
- Second approval: `second_approval_reason()` (store.py; the total in CAD over `approval_limit`, or a currency with no `fx_rates`
  rate) -> `pending_approval`; `final_approve()` refuses the first approver's name (any case) or login (the latest `approved`
  event's `detail.login`; a colleague's is `network <address>`: web/core.py's login). web/pages/invoice.py and review.py's
  _second repeat the check to show the button: change all three together.
- `from_env()` (config.py, `Settings.from_env`): .env path, else `paths.env_file()` -> `Settings`; `override=False`: the OS environment wins, so a saved value also goes into `os.environ`.
- `load_reference_data()` (reference_data.py): files -> `ReferenceData` (GL, cost centers, tax setup, policy) for prompts and checks.
- `InvoiceCoding` (schema.py): the coding shape (and the JSON schema the models answer in).
- Data it owns: store.py's tables `invoices`, `feedback`, `events`, `settings`, `gl_accounts`, `cost_centers`, `tax_treatments`,
  `vendors`, `export_batches`, `purchase_orders`, `po_lines`, `erp_invoices`, `coding_rules`, `invoice_capture`, `supplier_profiles`,
  `supplier_outcomes`, `reader_outcomes`, `lessons`; queues `page_reads`, `azure_checks`, `verifications`. Status: review, approved, pending_approval, parked, rejected, failed.
- Settings: the `.env` (addresses, keys); per user `~/.ap_coder/settings.json` (`AP_USER_SETTINGS`: reviewer name, data folder, installs;
  `paths.read_user_settings()`); the `settings` table (`get_setting()` / `set_setting()`): `approval_limit`, `fx_rates`, `policy_notes`,
  `default_terms_days`, `our_province`, `site_provinces`, `touchless_processing`, `touchless_limit`, `verifier`, `insights_assumptions`, `jde_e1`,
  `export_layout`, `directory_presets` (JSON [{name, query}]), `directory_default`, `backup_copy_dir` (a restore clears it), `why_after`, `azure_check_*`, `page_reader_test`, `page_reader_figures`. Never edit or reset `schema_version`, `vendor_key_rules`, `backup_copy_status`, `page_reader_testing` (the self-test's cross-process lock), `demo_vendor_master`, `demo_purchase_orders`.

## Gotchas and decisions
- Only store.py opens the database (`erp/directory.py` builds query text); id lists are one JSON parameter (B-11).
  CSV/xlsx files for people go through safe.py (HTML is escaped by Jinja and ui.py).
- A newer version's database is refused; an older one is backed up before migrating. store.py does too much (scale assessment, PR #24).
- Store methods that run `BEGIN IMMEDIATE` (`create_export_batch`, `next_page_read`, `next_azure_check`, `next_verification`,
  `update_after_check`, `replace_proposal`, `delete_invoice(keep_approved=True)`) fail inside an open `session()` write.
- `get_invoice` can write (a moved file is re-pointed, logged `file_found`). `feedback_rows` is cached: feedback rows are only added or deleted, never updated.
- A new event action needs its `audit.ACTIONS` entry (tests/test_audit.py fails without it).
- Changing `memory.vendor_key` means bumping `VENDOR_KEY_RULES` (the store recomputes stored keys); invoice.js has a copy (parity test).
- A stored `ai_output` / `final_output` holds `gl_distribution`: strip it before `InvoiceCoding.model_validate` (extra fields are refused).
- paths.py and envfile.py are standard library only (scripts/ import them before any package is installed); keep `__init__.py` free of imports.

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
- B-15: Don't build the legal-form list from English only in a bilingual market. `tests/test_memory_store.py::test_vendor_key_ignores_french_legal_forms`
- B-30: Don't bind an unbounded id list as separate SQL variables. `tests/test_exports_bulk.py::test_a_batch_of_more_invoices_than_sqlite_takes_variables`
- B-39: Don't classify hosts by their spelling; parse them the way the resolver will. `tests/test_local_llm.py::test_an_internet_address_written_another_way_is_still_refused`
- B-56: Don't check a state in one step and write it in another without a condition on the write. `tests/test_web_finance.py::test_two_undos_of_a_batch_at_once_record_one_undo`
- B-73: Don't resolve a key collision by keeping one row when the other row holds a control. `tests/test_memory_store.py::test_rekeyed_vendor_rows_merge_and_on_hold_wins`
- B-74: Don't decide "thousands separator" without checking the groups are real thousands. `tests/test_safety.py::test_parse_amount_reads_thousands_only_from_real_thousands_groups`
- B-75: Don't keep a hand list of audit actions apart from the actions the store logs. `tests/test_audit.py::test_every_logged_action_has_a_label_and_a_description`
- B-78: Don't read a file people save in Notepad without `utf-8-sig`. `tests/test_reference_data.py::test_coding_policy_saved_with_a_byte_order_mark_keeps_its_comment_line`
- B-129: Don't add a field to every record under a name some records already use: pick a key nothing else writes. `tests/test_network_identity.py::test_a_colleagues_address_never_replaces_what_an_event_itself_records`

## Files
- `__init__.py`: the package docstring only.
- `store.py` (SQLite store), `config.py` (Settings from env), `paths.py` (data folder, user settings), `envfile.py` (.env edits).
- `reference_data.py` (GL, cost centers, tax setup, policy, prompt context), `csvio.py` (Excel CSVs, dates); `schema.py` (JSON schema, InvoiceCoding).
- `safe.py` (formula-safe cells, the shared amount parser), `audit.py` (approval diffs, event labels); `memory.py` (reviewer decisions, vendor key), `ui.py` (HTML pieces).
