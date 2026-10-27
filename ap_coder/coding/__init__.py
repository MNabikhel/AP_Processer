"""Coding checks, all deterministic: what an invoice's coding is checked against before approval.

``validation`` gathers them (``validate_coding``); Canadian sales tax is ``tax`` (rates, place of supply, the checks,
the GL distribution), ``line_tax`` (each line's tax code), ``taxmath`` (the Tax check) and ``taxreturn`` (ITCs and
self-assessment); then ``vendors`` (the vendor master, fraud and duplicate signals), ``po`` (PO matching), ``terms``
(payment terms), ``banks`` (the account an invoice is paid from), ``rules`` (fixed coding rules), ``review`` (the
review screen's grids), ``bulk`` (bulk approval) and ``vendor_mail`` (the email to a vendor).
"""
