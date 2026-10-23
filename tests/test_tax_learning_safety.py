"""The safety rules of learned line tax codes (D-80): a learned code may save typing, but it never silently drops a
tax that is due and never removes a reason for a person to look. The scenarios are a CPA review's of D-70."""

from __future__ import annotations

from ap_coder.ai.offline_coder import tax_codes
from ap_coder.coding.line_tax import learned_codes, prefill, tax_code_of, tax_guessed, tax_learned
from ap_coder.coding.taxreturn import self_assessed_base
from ap_coder.core.memory import vendor_key
from ap_coder.core.schema import InvoiceCoding, LineItem, TaxLine

VENDOR = "Coast Supply Ltd"


def _lines(*items: tuple[str, float]) -> list[LineItem]:
    return [LineItem(line_number=n, description=d, quantity=1, unit_price=a, amount=a, predicted_gl_code="6000")
            for n, (d, a) in enumerate(items, 1)]  # fmt: skip


def _tax(kind: str, rate: float, base: float, province: str = "BC") -> TaxLine:
    return TaxLine(tax_type=kind, province="" if kind == "GST" else province, rate=rate, taxable_amount=base,
                   tax_amount=round(base * rate, 2))  # fmt: skip


def _row(invoice: int, description: str, code: str, province: str = "BC", vendor: str = VENDOR, *,
         day: int | None = None, source: str = "approved", line: int = 1) -> dict:  # fmt: skip
    return {"id": invoice * 100 + line, "invoice_id": invoice, "line_number": line, "vendor_key": vendor_key(vendor),
            "vendor_name": vendor, "description": description, "final_tax_code": code, "tax_province": province,
            "tax_source": source, "created_at": f"2026-01-{day or invoice:02d}T10:00:00"}  # fmt: skip


def _learn(lines, rows, vendor: str = VENDOR, province: str = "BC") -> list[str]:
    return learned_codes(lines, rows, vendor, province)


def _self_assessed(lines, taxes, tax_type: str = "PST") -> float:
    coding = InvoiceCoding(vendor_name=VENDOR, invoice_number="1", invoice_date="2026-09-01", currency="CAD",
                           subtotal=sum(li.amount for li in lines), tax_lines=taxes, line_items=lines,
                           tax_total=sum(t.tax_amount for t in taxes),
                           grand_total=sum(li.amount for li in lines) + sum(t.tax_amount for t in taxes),
                           confidence_score=1.0)  # fmt: skip
    doc = coding.model_dump()
    return self_assessed_base(doc, doc["line_items"], tax_type)


BOOTS = [_row(1, "Safety boots steel toe", "GST~PST"), _row(2, "Safety boots steel toe", "GST~PST")]


# --- 1. A vendor that does not collect the PST/QST: a learned exemption of it is not used (B-760) ---------------------


def test_a_learned_pst_exemption_is_not_used_when_the_vendor_does_not_collect_pst():
    """S1c: the vendor stopped charging PST (no PST number): the PST is due on the boots too, as without learning."""
    lines = _lines(("Safety boots steel toe", 200.0), ("Work gloves", 100.0))
    taxes = [_tax("GST", 0.05, 300.0)]
    done = prefill(lines, taxes, vendor_collects=False, learned=["GST~PST", ""]).lines
    assert tax_code_of(done[0]) == "GST" and _self_assessed(done, taxes) == 300.0
    # S9: a printed "G" takes no exemption from the history either.
    printed = prefill(lines, taxes, printed=["G", "G"], vendor_collects=False, learned=["GST~PST", ""]).lines
    assert tax_code_of(printed[0]) == "GST"
    # A vendor that collects it: the learned exemption is used as before.
    assert tax_code_of(prefill(lines, taxes, learned=["GST~PST", ""]).lines[0]) == "GST~PST"


def test_an_out_of_province_vendor_and_a_quebec_vendor_without_a_qst_number_owe_the_tax():
    """S1e: an Alberta vendor shipping to BC; S8c: a Quebec vendor with no QST number charging GST only."""
    lines = _lines(("Safety boots steel toe", 200.0), ("Work gloves", 100.0))
    away = tax_codes(lines, [_tax("GST", 0.05, 300.0)], [], "", "BC", "2026-09-01", supplier_province="AB",
                     pst_number="", feedback=BOOTS, vendor=VENDOR)  # fmt: skip
    assert tax_code_of(away[0]) == "GST"
    paper = [_row(n, "Papier photocopie", "GST~QST", "QC", "Fournitures Laval") for n in (1, 2)]
    qc = tax_codes(_lines(("Papier photocopie", 100.0), ("Toner", 100.0)), [_tax("GST", 0.05, 200.0)], [], "", "QC",
                   "2026-09-01", supplier_province="QC", qst_number="", feedback=paper,
                   vendor="Fournitures Laval")  # fmt: skip
    assert tax_code_of(qc[0]) == "GST"
    with_number = tax_codes(_lines(("Papier photocopie", 100.0), ("Toner", 100.0)), [_tax("GST", 0.05, 200.0)], [],
                            "", "QC", "2026-09-01", supplier_province="QC", qst_number="1234567890TQ0001",
                            feedback=paper, vendor="Fournitures Laval")  # fmt: skip
    assert tax_code_of(with_number[0]) == "GST~QST"


def test_a_learned_exempt_status_is_not_used_when_the_vendor_does_not_collect_pst():
    lines = _lines(("Hard hat", 100.0), ("Vest", 100.0))
    taxes = [_tax("GST", 0.05, 200.0)]
    done = prefill(lines, taxes, vendor_collects=False, learned=["EXEMPT", ""]).lines
    assert not tax_learned(done[0]) and tax_code_of(done[0]) == "GST"


# --- 5. A learned code that keeps a charged tax off every line that could carry it (B-761) ----------------------------


def test_a_learned_code_that_leaves_a_tax_charged_on_no_line_is_dropped():
    """S5: learned "GST" (PST due) on the hard hat, but the vendor charged PST on it this time: not self-assessed on
    top of the PST charged."""
    lines = _lines(("Hard hat", 100.0))
    taxes = [_tax("GST", 0.05, 100.0), _tax("PST", 0.07, 100.0)]
    done = prefill(lines, taxes, learned=["GST"]).lines
    assert tax_code_of(done[0]) == "GST+PST" and not tax_learned(done[0]) and _self_assessed(done, taxes) == 0
    assert tax_guessed(done[0])  # what AP approved before and what the vendor charged now disagree: check it
    # S3b: learned zero-rated, but the GST charged adds up only with it.
    two = prefill(_lines(("Chocolate milk 4L jug", 20.0), ("Bag of ice", 20.0)), [_tax("GST", 0.05, 40.0, "AB")],
                  learned=["ZERO", ""]).lines  # fmt: skip
    assert [tax_code_of(li) for li in two] == ["GST", "GST"]
    # S10b: a credit note reversing PST on the boots.
    credit = prefill(_lines(("Safety boots steel toe", -200.0)), [_tax("GST", 0.05, -200.0), _tax("PST", 0.07, -200.0)],
                     learned=["GST~PST"]).lines  # fmt: skip
    assert tax_code_of(credit[0]) == "GST+PST"


def test_a_learned_code_keeps_the_check_it_note_of_an_ambiguous_solve():
    """S3c: three lines of 20.00 and GST on 40.00: the amounts can't say which two; a learned code makes it look
    unique, and the person is still asked to check it."""
    lines = _lines(("Chocolate milk 4L jug", 20.0), ("Paper cups", 20.0), ("Napkins", 20.0))
    taxes = [_tax("GST", 0.05, 40.0, "AB")]
    assert all(tax_guessed(li) for li in prefill(lines, taxes).lines)
    done = prefill(lines, taxes, learned=["ZERO", "", ""]).lines
    assert tax_code_of(done[0]) == "ZERO" and tax_learned(done[0])
    assert all(tax_guessed(li) for li in done)
    # A unique solve stays unique: the learned exemption takes no note.
    alone = prefill(_lines(("Electrician - labour", 400.0), ("LED panel", 120.0)),
                    [_tax("GST", 0.05, 520.0), _tax("PST", 0.07, 120.0)], learned=["GST~PST", ""]).lines  # fmt: skip
    assert not tax_guessed(alone[0]) and tax_code_of(alone[0]) == "GST~PST"
