"""ERP files and reports, from the approved invoices.

Files: ``exports`` (Excel and CSV; ``export_layout``: a CSV of your own), JD Edwards (below), ``stamp``
(approval-stamped PDFs) and ``hub`` (the finance hub feed). Reports: ``insights``, ``spend``, ``accruals``
(month-end), ``controls`` (internal audit), ``dupaudit`` (duplicate payments), ``statements`` (vendor statements),
``recurring`` (regular vendors), ``directory`` and ``search`` (the invoice directory); ``registers`` reads the ERP's
invoice register, ``journal`` its past invoice coding; ``import_specs``: the columns each imported file needs.

JD Edwards E1, one subject a module, each importing only the ones before it, a name imported from its home
(``jde_settings.load``, never through ``jde``): ``jde_formats`` (dates, amounts), ``jde_settings`` (``JdeSettings``,
``load``, ``save``, ``in_use``, the open period), ``jde_prototype`` (``use_prototype``), ``jde_accounts``
(``resolve_an8``, ``account_for``), ``jde_voucher`` (one voucher, ``_prepare``: its tax area and code, terms, its
problems), ``jde_remarks``, ``jde_retainage`` (a progress billing's Retained Amounts), ``jde_zfiles`` (the Z-files'
columns, ``voucher_rows``, ``export_batch``), ``jde_match`` (a PO-matched voucher's rows and taxes), ``jde_review``
(``voucher_findings``, ``voucher_view``), ``jde`` (a batch: ``validate``, why a voucher is held from it;
``build_zip``, ``dry_run``), ``jde_keying`` (the keying sheet), ``jde_worksheet`` (the settings worksheet); the AIS
API: ``jde_ais``, ``jde_post``, ``jde_sync``.
"""
