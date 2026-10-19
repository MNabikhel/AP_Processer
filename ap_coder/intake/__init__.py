"""Invoices coming in: pictures made into PDFs, saved emails unpacked and sorted, and the emails set aside.

This package took the name of the old module ``ap_coder.intake`` (now ``ap_coder.intake.pictures``): for one release
its names are still found here (``from ap_coder.intake import to_pdf``); this forwarding goes in a later release.
"""

_SUBMODULES = {"pictures", "inbox", "mailbox", "msgfile", "triage"}


def __getattr__(name: str):
    if name.startswith("__") or name in _SUBMODULES:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from . import pictures

    try:
        return getattr(pictures, name)
    except AttributeError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
