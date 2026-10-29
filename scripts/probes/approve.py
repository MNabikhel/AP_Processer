"""Probe ``approve``: N seeded random invoices approved in each of three new databases (no cost centers; cost
centers; an approval limit), every outcome, edit, second approval and refusal among them; what the approvals return
and the invoices, feedback and events tables, for ``scripts/same_behaviour.py``.

    PYTHONPATH=<tree> python scripts/probes/approve.py N WORK_DIR > out.json"""

import copy
import json
import random
import sqlite3
import sys
from pathlib import Path

import ap_coder.core.store._base as base
import ap_coder.core.store.invoices as invoices_mod
from ap_coder.capture.workflow import AUTONOMOUS_REVIEWER
from ap_coder.core.store import Store

TICK = [0]


def fixed_now() -> str:
    TICK[0] += 1
    return f"2026-10-29T{TICK[0] // 3600 % 24:02d}:{TICK[0] // 60 % 60:02d}:{TICK[0] % 60:02d}"


base._now = fixed_now
invoices_mod._now = fixed_now

GL = ["5000", "5100", "6000", "6100", "UNASSIGNED", ""]
CC = ["", "CC10", "CC20", "CC30"]
PROV = ["ON", "QC", "BC", "AB", ""]


def lines(rng: random.Random, n: int) -> list[dict]:
    out = []
    for i in range(n):
        amount = round(rng.uniform(5, 900), 2)
        out.append(
            {
                "line_number": i + 1,
                "description": rng.choice(["Widgets", "Toner", "Freight", "Lunch"]),
                "quantity": 1,
                "unit_price": amount,
                "amount": amount,
                "predicted_gl_code": rng.choice(GL),
                "predicted_cost_center": rng.choice(CC),
                "taxes_applied": rng.choice([[], ["GST"], ["HST"], ["GST", "PST"], ["GST", "QST"]]),
                "tax_exempt": rng.choice([None, ["PST"]]),
                "tax_status": rng.choice(["", "", "exempt"]),
                "reasoning_justification": rng.choice(["", "guessed: closest account", "rule"]),
            }
        )
    return out


def coding(rng: random.Random) -> dict:
    items = lines(rng, rng.choice([0, 1, 2, 3, 5]))
    sub = round(sum(li["amount"] for li in items), 2)
    prov = rng.choice(PROV)
    tax = [
        {
            "tax_type": t,
            "province": prov if t != "GST" else "",
            "rate": r,
            "taxable_amount": sub,
            "tax_amount": round(sub * r, 2),
        }
        for t, r in rng.choice([[("HST", 0.13)], [("GST", 0.05)], [("GST", 0.05), ("QST", 0.09975)], []])
    ]
    total_tax = round(sum(t["tax_amount"] for t in tax), 2)
    return {
        "vendor_name": rng.choice(["Northwind Ltd", "Acme Inc.", "Lakeshore", ""]),
        "invoice_number": f"N-{rng.randint(1, 99999)}",
        "invoice_date": f"2026-0{rng.randint(1, 9)}-1{rng.randint(0, 9)}",
        "po_number": rng.choice(["", "PO-1", "po 22"]),
        "payment_terms": rng.choice(["", "Net 30", "Net 45"]),
        "due_date": rng.choice(["", "2026-12-01"]),
        "currency": rng.choice(["CAD", "USD", "", "eur"]),
        "supplier_province": prov,
        "ship_to_province": rng.choice(PROV),
        "gst_hst_registration_number": rng.choice(["", "123456782RT0001"]),
        "qst_registration_number": "",
        "subtotal": sub,
        "tax_lines": tax,
        "tax_total": total_tax,
        "grand_total": round(sub + total_tax + rng.choice([0, 0, 0, 10000]), 2),
        "confidence_score": 0.9,
        "line_items": items,
    }


def edited(rng: random.Random, ai: dict) -> dict:
    final = copy.deepcopy(ai)
    for li in final["line_items"]:
        r = rng.random()
        if r < 0.3:
            li["predicted_gl_code"] = rng.choice(GL[:4])
        if rng.random() < 0.2:
            li["predicted_cost_center"] = rng.choice(CC)
        if rng.random() < 0.1:
            li["taxes_applied"] = rng.choice([[], ["GST"], ["HST"]])
    if rng.random() < 0.15 and final["line_items"]:
        final["line_items"].pop()
    if rng.random() < 0.1:
        final["line_items"] = final["line_items"] + lines(rng, 1)
        final["line_items"][-1]["line_number"] = len(final["line_items"])
    for f in ("invoice_number", "po_number", "payment_terms", "gst_hst_registration_number"):
        if rng.random() < 0.1:
            final[f] = rng.choice(["X-1", "", "Net 15", "123456782RT0001"])
    if rng.random() < 0.1:
        final["tax_lines"] = []
    if rng.random() < 0.05:
        final["grand_total"] = 99999.0
    return final


def main() -> None:
    n, out = int(sys.argv[1]), Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(1029)
    results = []
    for setup in range(3):  # no cost centers, with cost centers, with an approval limit
        store = Store(out / f"s{setup}.db")
        store.save_accounts("gl_accounts", [{"code": c, "description": c, "category": "expense"} for c in GL[:4]])
        if setup:
            store.save_accounts("cost_centers", [{"code": c, "description": c} for c in CC[1:]])
        if setup == 2:
            store.set_setting("approval_limit", "5000")
            store.set_setting("fx_rates", "USD=1.37")
        for i in range(n):
            ai = coding(rng)
            meta = {}
            if ai["line_items"] and rng.random() < 0.2:
                li = ai["line_items"][0]
                meta["rules_applied"] = [
                    {
                        "line_number": li["line_number"],
                        "gl_from": "5000",
                        "cc_from": "",
                        "gl_to": li["predicted_gl_code"],
                        "cc_to": li.get("predicted_cost_center"),
                    }
                ]
            if ai["line_items"] and rng.random() < 0.15:
                meta["reopened_coding"] = {
                    str(li["line_number"]): [rng.choice(GL[:4]), rng.choice(CC)] for li in ai["line_items"]
                }
            iid = store.add_invoice(out / f"{setup}-{i}.pdf", ai, None, meta=meta or None)
            state = rng.random()
            if state < 0.04:
                store.park_invoice(iid, "Ann", "waiting")
            elif state < 0.07:
                store.reject_invoice(iid, "Ann", "no")
            reviewer = rng.choice(["Ann", "Bob", AUTONOMOUS_REVIEWER])
            final = edited(rng, ai)
            for attempt in range(1 + (rng.random() < 0.05)):  # now and then approved twice
                try:
                    counts = store.approve_invoice(
                        iid,
                        final,
                        reviewer,
                        bulk=rng.random() < 0.2,
                        open_issues=[{"code": "X"}] if rng.random() < 0.1 else None,
                        login=rng.choice(["", "ann"]),
                    )
                    results.append(["ok", setup, i, attempt, counts])
                except Exception as exc:
                    results.append(["raised", setup, i, attempt, type(exc).__name__, str(exc)])
        conn = sqlite3.connect(out / f"s{setup}.db")
        conn.row_factory = sqlite3.Row
        for table, cols in (
            (
                "invoices",
                "id, status, final_output, edits, reviewer, reviewed_at, vendor_name, "
                "vendor_key, invoice_number, invoice_date, grand_total, currency, po_key, "
                "due_date",
            ),
            ("feedback", "*"),
            ("events", "action, invoice_id, actor, detail"),
        ):
            rows = [dict(r) for r in conn.execute(f"SELECT {cols} FROM {table} ORDER BY rowid")]
            results.append([table, setup, rows])
    print(json.dumps(results, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
