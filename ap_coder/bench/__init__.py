"""The accuracy benchmarks (``python -m ap_coder.bench``): made-up invoices with their truth, read and scored.

``harness`` (capture: ``run``, ``calibrate``), ``learning`` (supplier learning, ``learn``), ``coding`` (GL coding with
no AI model, ``coding``), ``speed`` (seconds per invoice, ``speed``), ``emails`` (email triage, ``triage``); the
invoices come from ``generator`` and ``content``, scored by ``scoring``. For the company server's scale:
``fake_model`` (a stand-in LM Studio with delays, slots and failures, ``fake-model``) and ``day`` (a morning's drop
through a real server, ``day``).
"""
