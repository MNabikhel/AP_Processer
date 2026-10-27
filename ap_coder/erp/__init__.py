"""ERP files and reports, from the approved invoices.

Files: ``exports`` (Excel and CSV; ``export_layout``: a CSV of your own), ``jde`` (JD Edwards batch files; its AIS
API: ``jde_ais``, ``jde_sync``, ``jde_post``; ``jde_worksheet``: the settings worksheet), ``stamp`` (approval-stamped
PDFs) and ``hub`` (the finance hub feed). Reports: ``insights``, ``spend``, ``accruals`` (month-end), ``controls``
(internal audit), ``dupaudit`` (duplicate payments), ``statements`` (vendor statements), ``recurring`` (regular
vendors), ``directory`` and ``search`` (the invoice directory); ``registers`` reads the ERP's invoice register.
"""
