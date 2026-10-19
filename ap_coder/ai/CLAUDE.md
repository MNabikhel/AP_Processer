# ap_coder/ai: reading and AI (readers, models, second opinions, learning, accuracy, the pipeline)

How an invoice is read and coded: the pipeline (read, code, check, save), the offline coder, the local and Azure
models and their prompts, the page reader (OvisOCR2) and its queue, the readers' status, the second opinions (AI
reviewer, opt-in Azure check), what AP's corrections teach (lessons, ERP history, GL suggestions) and the accuracy tools
(ground-truth workbook, evaluation, training ZIP). Tax and checks stay deterministic (`ap_coder/coding`): the AI only reads what is printed.
Grouped here from the flat `ap_coder/` (docs/decisions.md D-33): `ap_coder.pipeline` and the other old paths are aliases for one release.

**Depends on:** `ap_coder/bench`, `ap_coder/capture`, `ap_coder/coding`, `ap_coder/core`, `ap_coder/erp`, `ap_coder/intake`, `ap_coder/ops`
**Used by:** `ap_coder/bench`, `ap_coder/capture`, `ap_coder/coding`, `ap_coder/core`, `ap_coder/erp`, `ap_coder/intake`, `ap_coder/ops`, `ap_coder/web`, `ap_coder/web/pages`

## Interface
- `InvoicePipeline` `process()` / `process_many()` (pipeline.py): file -> `PipelineResult` (read, code, check, save,
  approve touchless at save); with `page_text`, `invoice_id`, `pages_read`, `save=False`: the re-read after the page reader.
- `finalise_coding()` (pipeline.py): coding -> (output with `gl_distribution`, report); the pipeline, review screen, bulk and demo call it.
- Offline: `read_invoice()` and `code_from_capture()` (offline_coder.py; the chat model only suggests leftover lines
  and reads the header); with Azure, `workflow.capture_invoice()` (capture) and `InvoiceCoder.code()` (inference.py).
- Second opinions: the pipeline calls `prepare()`, `hold()` and `queue()` of verifier.py (AI reviewer, key `verifier`) and azure_check.py
  (own thread, `azure_check.start_background()`). `verify_one()` runs in the page reader's thread when OvisOCR2 has nothing to read or isn't ready.
- Page reader queue (page_worker.py): `queue_new_invoice()`, `read_one()`, `run_queue()`, `start_background()`; reads once the self-test passed (`confirmed()`).
- `reader_status()` (page_reader.py): is OvisOCR2 there and ready; reading.py gathers every reader's status for Settings > Reading.
- Accuracy: `export_labels()` / `load_labels()` (labels.py, the ground-truth workbook) -> `evaluate()` (evaluation.py).

## Gotchas and decisions
- The queues (page reads, Azure checks, verifications) never hold up import: they hold touchless approval, add their
  checks (VERIFIER_*, AZURE_DI_*), and the page reader replaces an untouched proposal.
- A late approval (Azure, AI reviewer) needs every hold cleared (`verifier.HOLDS`) and goes through `still_touchless()` (capture/workflow.py).
- A check that must always reach a person goes in `capture/supplier.ALWAYS_A_PERSON` (else bulk approves an info-level one unseen).
- The model server's address is refused when it is on the internet (`local_llm.refuse_internet`, D-04); Azure only once AP switched it on.
- Path(__file__) here is one folder deeper than before the grouping: azure_check.py's sample invoice is `parent.parent.parent`.

### Bug lessons
From docs/BUGS.md (one line each; the entry has the repro and the fix).
- B-31: Don't let a later round's confidence stand in for items that were never answered. `tests/test_verifier.py::test_agreed_needs_every_item_agreed_in_a_confident_round`
- B-32: Don't make approval gates depend on the order they were applied in. `tests/test_verifier.py::test_every_hold_is_recorded_whatever_the_order`
- B-33: Don't approve from a copy of the row read minutes earlier. `tests/test_verifier.py::test_a_reopen_during_the_review_keeps_aps_corrections`
- B-34: Don't treat a model's scratch work as its decision. `tests/test_local_llm.py::test_a_strict_reply_never_takes_a_draft_from_the_thinking`
- B-35: Don't accept a stream as finished unless the server said so. `tests/test_page_reader.py::test_a_reading_that_ends_without_a_finish_reason_is_not_a_whole_page`
- B-36: Don't check a time budget only between blocking calls. `tests/test_verifier.py::test_each_call_is_given_only_the_time_left`
- B-37: Don't infer a capability from an error that didn't name it. `tests/test_local_llm.py::test_a_400_that_is_not_about_the_format_keeps_structured_output`
- B-38: Don't build a prompt from data without a length budget. `tests/test_local_llm.py::test_a_long_invoice_is_suggested_within_the_prompt_budget`
- B-40: Don't regex-strip markup out of text that might be JSON. `tests/test_local_llm.py::test_a_thinking_tag_inside_a_json_string_is_kept`
- B-41: Don't let a fallback parser undo the guard the main parser enforces. `tests/test_local_llm.py::test_a_reply_cut_off_without_the_length_reason_is_not_read_as_its_first_line`
- B-48: Don't delete what you should fold. `tests/test_evaluation.py::test_accented_names_match_their_unaccented_spelling`
- B-76: Don't cut a date at 10 characters. `tests/test_history.py::test_history_dates_are_read_not_cut_at_ten_characters`
- B-77: Don't round ground truth on the way out. `tests/test_ops_sweep_fixes.py::test_labels_keep_quantities_and_unit_prices_unrounded`
- B-79: Don't store a value you worked out in the field that means "printed on the invoice". `tests/test_tax.py::test_a_worked_out_place_of_supply_follows_the_recoded_lines`
- B-93: Don't treat "nothing compared" as "nothing differs". `tests/test_azure_check.py::test_nothing_compared_is_not_nothing_differs`
- B-94: Don't record the server's outage as the reviewer's verdict. `tests/test_verifier.py::test_the_servers_outage_is_not_the_reviewers_verdict`
- B-95: Don't silence a helper's exception without a log line. `tests/test_verifier.py::test_the_reviewer_is_shown_the_math`
- B-96: Don't let the strict path fall back to scanning prose for JSON. `tests/test_local_llm.py::test_a_strict_reply_is_the_whole_answer_not_json_found_in_prose`
- B-103: Don't assume Unicode decomposition folds every letter. `tests/test_evaluation.py::test_ligatures_and_sharp_s_fold_to_their_letters`
- B-130: Don't trust a contextvar across a thread pool: copy the context into each task. `tests/test_network_identity.py::test_invoices_read_in_parallel_keep_the_colleague_who_started_them`

## Files
- `__init__.py`: the package docstring only.
- Coding: `pipeline.py` (read, code, check, save), `offline_coder.py` (how every invoice is coded offline); `suggest.py` (GL from history), `line_match.py` (line similarity).
- AI models: `inference.py` (Azure coding; the chat model's header read and line suggestions), `local_llm.py` (LM Studio, Ollama); `prompts.py` (whole-invoice coding prompt).
- Readers: `page_reader.py` (OvisOCR2), `page_worker.py` (its queue); `reading.py` (the readers' status), `extraction.py` (Azure Document Intelligence); `imaging.py` (page images), `figures.py` (two readings compared).
- Second opinions: `verifier.py` (AI reviewer), `azure_check.py` (opt-in Azure check). Learning: `lessons.py` (corrections per situation), `history.py` (past ERP coding).
- Accuracy: `labels.py` (ground-truth workbook), `evaluation.py` (accuracy), `training_export.py` (fine-tuning ZIP).
