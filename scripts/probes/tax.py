"""Probe ``tax``: ``check_taxes`` and ``math_check`` on N seeded random invoices (messy ones, and a third as most
are), one JSON line each, for ``scripts/same_behaviour.py``.

    PYTHONPATH=<tree> python scripts/probes/tax.py N WORK_DIR > out.jsonl"""

import json
import random
import sys

from ap_coder.coding.tax import TaxRateTable, TaxSetup, TaxTreatment, check_taxes
from ap_coder.coding.taxmath import math_check
from ap_coder.core.schema import InvoiceCoding

PROVINCES = ["AB", "BC", "MB", "NB", "NL", "NS", "NT", "NU", "ON", "PE", "QC", "SK", "YT", "", "OUTSIDE_CANADA"]
TYPES = ["GST", "HST", "PST", "QST", "OTHER"]
RATES = {
    "GST": [0.05, 0.07, 0.0],
    "HST": [0.13, 0.15, 0.14, 0.12],
    "PST": [0.07, 0.06, 0.08, 0.05],
    "QST": [0.09975, 0.095],
    "OTHER": [0.0825, 0.06],
}
STATUSES = ["", "exempt", "zero_rated", "out_of_scope"]
NOTES = [
    "",
    "Tax code learned from past approvals of this vendor.",
    "PST exemption inferred (charged on the other lines, not this one): confirm it.",
    "office supplies",
]
GL = ["5000", "6000", "6100", "7000"]
GST_NUMBERS = ["", "123456782RT0001", "123456789RT0001", "12345678RT0001", "BAD"]
QST_NUMBERS = ["", "1234567890TQ0001", "123TQ1"]


REGIME = {
    "ON": {"HST": 0.13},
    "NB": {"HST": 0.15},
    "NS": {"HST": 0.14},
    "QC": {"GST": 0.05, "QST": 0.09975},
    "BC": {"GST": 0.05, "PST": 0.07},
    "MB": {"GST": 0.05, "PST": 0.07},
    "SK": {"GST": 0.05, "PST": 0.06},
    "AB": {"GST": 0.05},
    "YT": {"GST": 0.05},
}


def clean(rng: random.Random) -> InvoiceCoding:
    """An invoice as most are: the province's taxes at their rates, on the lines that carry them."""
    prov = rng.choice(sorted(REGIME))
    taxes = REGIME[prov]
    lines = []
    for i in range(rng.choice([1, 2, 3, 5])):
        amount = round(rng.uniform(5, 1500), 2)
        exempt = rng.random() < 0.15 and "PST" in taxes
        applied = sorted(t for t in taxes if not (exempt and t == "PST"))
        lines.append(
            {
                "line_number": i + 1,
                "description": "Item",
                "quantity": 1,
                "unit_price": amount,
                "amount": amount,
                "predicted_gl_code": rng.choice(GL),
                "taxes_applied": applied,
                "tax_exempt": ["PST"] if exempt else None,
                "reasoning_justification": rng.choice(NOTES[:2]),
            }
        )
    subtotal = round(sum(li["amount"] for li in lines), 2)
    tax_lines = []
    for t, rate in taxes.items():
        taxable = round(sum(li["amount"] for li in lines if t in li["taxes_applied"]), 2)
        tax_lines.append(
            {
                "tax_type": t,
                "province": prov if t != "GST" else "",
                "rate": rate,
                "taxable_amount": taxable,
                "tax_amount": round(taxable * rate, 2),
            }
        )
    tax_total = round(sum(t["tax_amount"] for t in tax_lines), 2)
    return InvoiceCoding.model_validate(
        {
            "vendor_name": "Vendor",
            "invoice_number": "A-1",
            "invoice_date": f"2026-{rng.randint(1, 12):02d}-15",
            "currency": "CAD",
            "supplier_province": prov,
            "ship_to_province": rng.choice([prov, "", prov]),
            "gst_hst_registration_number": "123456782RT0001",
            "qst_registration_number": "1234567890TQ0001",
            "subtotal": subtotal,
            "tax_lines": tax_lines,
            "tax_total": tax_total,
            "grand_total": round(subtotal + tax_total, 2),
            "confidence_score": 0.9,
            "line_items": lines,
        }
    )


def coding(rng: random.Random) -> InvoiceCoding:
    if rng.random() < 0.35:
        return clean(rng)
    n = rng.choice([0, 1, 1, 2, 3, 4, 6])
    sign = -1 if rng.random() < 0.1 else 1
    lines = []
    for i in range(n):
        amount = sign * round(rng.choice([rng.uniform(1, 2000), rng.choice([10, 100, 250.5, 999.99])]), 2)
        applied = sorted(set(rng.sample(TYPES[:4], rng.choice([0, 1, 1, 2, 2, 3]))))
        lines.append(
            {
                "line_number": i + 1,
                "description": rng.choice(["Widgets", "Freight", "Lunch", "Consulting", "Toner"]),
                "quantity": 1,
                "unit_price": amount,
                "amount": amount,
                "predicted_gl_code": rng.choice(GL),
                "taxes_applied": applied,
                "tax_exempt": rng.choice([None, [], ["PST"], ["QST"], ["PST", "QST"]]),
                "tax_status": rng.choice(STATUSES) if not applied or rng.random() < 0.1 else "",
                "reasoning_justification": rng.choice(NOTES),
            }
        )
    subtotal = (
        round(sum(li["amount"] for li in lines), 2)
        if lines and rng.random() < 0.85
        else round(sign * rng.uniform(0, 3000), 2)
    )
    tax_lines = []
    for t in rng.sample(TYPES, rng.choice([0, 1, 1, 2, 2, 3])):
        rate = rng.choice(RATES[t])
        flagged = [li["amount"] for li in lines if t in li["taxes_applied"]]
        taxable = round(sum(flagged), 2) if flagged and rng.random() < 0.7 else subtotal
        if rng.random() < 0.15:
            taxable = round(taxable * rng.choice([1.05, 0.9, 1.0 + rng.choice(RATES["GST"])]), 2)
        amount = round(taxable * rate, 2)
        r = rng.random()
        if r < 0.15:
            amount = round(amount + rng.choice([0.01, -0.01, 0.03, 1.5, -7.0]), 2)
        elif r < 0.2:
            amount = 0.0
        tax_lines.append(
            {
                "tax_type": t,
                "province": rng.choice(["", "", rng.choice(PROVINCES[:13])]),
                "rate": rate,
                "taxable_amount": taxable,
                "tax_amount": amount,
            }
        )
        if rng.random() < 0.08:  # the same tax printed twice (goods and freight)
            tax_lines.append(
                {**tax_lines[-1], "taxable_amount": round(taxable / 3, 2), "tax_amount": round(taxable / 3 * rate, 2)}
            )
    tax_total = round(sum(t["tax_amount"] for t in tax_lines), 2)
    if rng.random() < 0.1:
        tax_total = round(tax_total + rng.choice([0.05, -1.0, 10.0]), 2)
    grand = round(subtotal + tax_total + (rng.choice([0, 0, 0.01, 5.0]) if rng.random() < 0.2 else 0), 2)
    year = rng.choice([2010, 2012, 2013, 2016, 2019, 2024, 2026, 2027])
    return InvoiceCoding.model_validate(
        {
            "vendor_name": "Vendor",
            "invoice_number": f"INV-{rng.randint(1, 9999)}",
            "invoice_date": f"{year}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}",
            "currency": rng.choice(["CAD", "CAD", "USD", "JPY", ""]),
            "supplier_province": rng.choice(PROVINCES),
            "ship_to_province": rng.choice(PROVINCES),
            "gst_hst_registration_number": rng.choice(GST_NUMBERS),
            "qst_registration_number": rng.choice(QST_NUMBERS),
            "subtotal": subtotal,
            "tax_lines": tax_lines,
            "tax_total": tax_total,
            "grand_total": grand,
            "confidence_score": 0.9,
            "line_items": lines,
        }
    )


def setup(rng: random.Random, rates: TaxRateTable) -> TaxSetup:
    treatments = {}
    for t in TYPES:
        if rng.random() < 0.5:
            kind = rng.choice(["recoverable", "expense_to_line", "expense_separate"])
            treatments[t] = TaxTreatment(t, kind, rng.choice(["", "2100", "9999"]))
    return TaxSetup(
        rates,
        treatments,
        our_province=rng.choice(PROVINCES[:13] + [""]),
        site_provinces={"CC1": "ON"} if rng.random() < 0.2 else {},
        meals_gl_codes=frozenset(rng.sample(GL, rng.choice([0, 0, 1, 2]))),
    )


def main() -> None:
    n = int(sys.argv[1])
    rates = TaxRateTable.load()
    rng = random.Random(20261029)
    for i in range(n):
        c, s = coding(rng), setup(rng, rates)
        known = rng.choice([None, {"5000", "6000", "2100"}])
        findings = [[f.severity, f.code, f.message, f.line_number] for f in check_taxes(c, s, known)]
        out = {"i": i, "findings": findings}
        for which, st in (("math", None), ("math_setup", s)):
            try:
                out[which] = math_check(c, st)
            except Exception as exc:  # the same crash before and after is the same behaviour
                out[which] = f"raised {type(exc).__name__}: {exc}"
        print(json.dumps(out, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
