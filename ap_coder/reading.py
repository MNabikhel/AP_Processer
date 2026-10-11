"""How AP Coder reads every invoice, the same way each time, and how each reader is doing right now.

There is one way to read an invoice, with nothing to choose:

1. the PDF's own text layer where the page has one, local OCR (two engines) where it has not (scans, photos);
2. the page reader, OvisOCR2 in LM Studio, reads every page of every invoice (digital PDFs too) as an independent
   second reader, found in LM Studio on its own and checked by its own self-test before it is trusted;
3. the rule reader, the supplier's learned template and the business checks (totals, tax, GST/HST number, vendor
   master, purchase order, duplicates, bank account);
4. no invoice is approved without a person before every reader has read it.

``reading_status`` is what the Settings page, the doctor and the banner on every page show.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

OK, WARN, OFF = "ok", "warn", "off"


@dataclass
class ReaderLine:
    """One row of "How AP Coder reads every invoice"."""

    name: str  # "PDF text layer", "OCR (two engines)", "Page reader (OvisOCR2)", "Rule reader", ...
    state: str  # OK | WARN | OFF
    detail: str  # plain English: what it does, or what is wrong and what to do


@dataclass
class ReadingStatus:
    readers: list[ReaderLine] = field(default_factory=list)
    page_reader_model: str = ""  # LM Studio key of OvisOCR2; "" when LM Studio has none
    page_reader_ready: bool = False  # found, LM Studio answers, and its self-test passed
    self_test: str = "no model"  # "passed" | "failed" | "running" | "pending" | "no model"
    self_test_detail: str = ""  # plain English: when it passed, what failed, how far along it is
    queue: int = 0  # invoices waiting for the page reader
    eta_minutes: float | None = None  # until the queue is read, when there is a measured page time
    figures_agree: float | None = None  # share of figures it read as the PDF's text layer has them (digital PDFs)
    figures_count: int = 0  # figures behind ``figures_agree``
    banner: str = ""  # a one-line warning for every page ("" when all is well)


def reading_status(settings: Any, store: Any, *, use_cache: bool = True) -> ReadingStatus:
    """Each reader's state now. Never raises: a reader that cannot be asked is shown as such."""
    raise NotImplementedError  # filled in with the uniform pipeline
