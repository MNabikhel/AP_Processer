"""The foundations: the SQLite store, settings, data folder, reference data, coding schema, memory, HTML pieces.

``store`` is the database (``Store``, a part per subject: its own map), ``schema`` the coding an invoice gets
(``InvoiceCoding``), ``reference_data`` the GL accounts, cost centers and tax setup it is checked against
(``tax_setup``: the treatments and the rate table). Settings: ``config`` (``Settings``, the .env; ``envfile``) and the
data folder ``paths``. Learning: ``memory`` (past approvals as examples; ``vendor_key``, a name's key). Values as
people mean them: ``keys``, ``parsing``, ``headers`` (column names), ``csvio`` and ``safe`` (files read and written);
a JD Edwards account string ``chart``, its file ``chart_file``. Signing in: ``permissions``, ``authenticator``.
``audit``: what an approval changed; ``ui``: the dashboard's HTML pieces; ``caches``: what each process keeps.
"""
