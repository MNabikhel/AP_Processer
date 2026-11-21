"""The accuracy benchmarks (``python -m ap_coder.bench``): made-up invoices with their truth, read and scored.

``harness`` (capture: ``run``, ``calibrate``), ``learning`` (supplier learning, ``learn``), ``coding`` (GL coding with
no AI model, ``coding``), ``speed`` (seconds per invoice, ``speed``), ``emails`` (email triage, ``triage``); the
invoices come from ``generator`` and ``content``, scored by ``scoring``. For the company server's scale:
``fake_model`` (a stand-in LM Studio with delays, slots and failures, ``fake-model``) and ``day`` (a morning's drop
through a real server, ``day``). For the JD Edwards work: ``jde_exports`` (a made-up company's E1 exports in the
real layouts, written from the truth ``jde_company`` builds out of ``jde_names``), ``jde_group`` (a made-up group
of companies, Canadian and US; their speed: ``companies_speed``), ``jde_po_vouchers`` (the voucher match test's
PO-matched invoices), ``coding_journal`` (GL coding from last year's journal) and ``pilot_day`` (a pilot day end to
end). Beside them: ``adversarial`` (invoices carrying text addressed to software), ``di_compare`` (Azure Document
Intelligence beside AP Coder's readers), ``lines`` (line items wrong once and right the next time), ``wording``
(labels as suppliers print them), ``msgwriter`` (Outlook .msg emails for the tests); and for the server's scale
``seed`` (a big data folder, fast), ``volume`` (one person on big data) and ``load`` (many people at once).
"""
