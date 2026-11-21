"""Coding checks, all deterministic: what an invoice's coding is checked against before approval.

``validation`` gathers them (``validate_coding``); Canadian sales tax is ``tax`` (rates, place of supply, the checks,
the GL distribution), ``line_tax`` (each line's tax code), ``taxmath`` (the Tax check) and ``taxreturn`` (ITCs and
self-assessment); then ``vendors`` (the vendor master, fraud and duplicate signals), ``po`` (PO matching), ``terms``
(payment terms), ``banks`` (the account an invoice is paid from), ``rules`` (fixed coding rules), ``review`` (the
review screen's grids), ``bulk`` (bulk approval), ``vendor_mail`` (the email to a vendor) and ``manager_mail`` (the
email asking a manager to confirm an invoice's cost allocation). Beneath them: ``money`` (the money and tax
arithmetic they share), ``claim`` (an expense claim's receipts as one voucher) and ``posting_edits`` (what JD Edwards
requires of a line on an account whose posting edit is L or U).
"""
