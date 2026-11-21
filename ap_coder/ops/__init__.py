"""Command line and operations: the commands, the doctor, the offline guard, the demo, check help, the share report,
the server's health for its monitor and the system's log (``health``, ``system_log``).

``cli`` is ``python -m ap_coder``: ``build_parser`` lists the commands, ``main`` runs one; the bigger ones have a
module each (``backup_cmd``, ``chart_cmd``, ``jde_cmd``, ``journal_cmd``). Checks: ``doctor``, ``offline`` (the
offline guard), ``help`` (what each check code means), ``share_report``. The pilot: ``demo`` (the sample invoices and
the made-up company), ``pilot_score``, ``journal_score`` and ``proposed_score`` (against what AP posted). The company
server: ``service`` (``service_windows``, ``service_macos``, ``service_linux``), ``server_update`` and
``update_recovery``, ``safety`` (its data folder), ``health``, ``system_log``. A laptop serving a few colleagues:
``share`` (run end to end by Windows CI only: ``scripts/ci/share_end_to_end.py``); kept awake while it reads:
``awake``.
"""
