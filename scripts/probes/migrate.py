"""Probe ``migrate``: the database each older release made (``tests/fixtures/old_schemas/``, D-190), N seeded invoices,
their lessons, vendors, POs, ERP bills, supplier profiles, snapshots and events written into it as that release's
tables had them, then brought up to date by the tree's ``Store``; its schema (in the order SQLite keeps it) and every
table's rows, for ``scripts/same_behaviour.py``.

    PYTHONPATH=<tree> python scripts/probes/migrate.py N WORK_DIR > out.json"""

import json
import random
import sqlite3
import sys
from pathlib import Path

from ap_coder.capture.workflow import AUTONOMOUS_REVIEWER
from ap_coder.core.store import Store

OLD = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "old_schemas"
VENDORS = ["Hydro-Québec", "Smith & Sons Ltd.", "Société ABC S.E.N.C.", "Acme Inc.", "Énergir Limitée", "Lakeshore"]
STATUSES = ["review", "approved", "approved", "parked", "rejected", "pending_approval", "failed"]
GL, CC = ["5000", "6000", "6100", "UNASSIGNED", ""], ["", "CC10", "UNASSIGNED"]


def old_key(name: str) -> str:
    return name.lower()  # as the first version kept it: accents, "&" and legal forms in the key


def coding(rng: random.Random, vendor: str, n: int) -> dict:
    lines = [{"line_number": i + 1, "description": rng.choice(["Toner", "Freight", "Lunch", "Hydro"]),
              "amount": round(rng.uniform(-50, 900), 2), "predicted_gl_code": rng.choice(GL),
              "predicted_cost_center": rng.choice(CC), "taxes_applied": rng.choice([[], ["GST"], ["HST"]])}
             for i in range(rng.choice([0, 1, 2, 3]))]  # fmt: skip
    total = round(sum(li["amount"] for li in lines) * rng.choice([1, 1, -1]), 2)
    return {"vendor_name": vendor, "invoice_number": f"N-{n}", "invoice_date": f"2026-0{rng.randint(1, 9)}-1{n % 10}",
            "po_number": rng.choice(["", "PO-1", "po 22"]), "payment_terms": rng.choice(["", "Net 30", "Net 45"]),
            "due_date": rng.choice(["", "2026-12-01"]), "currency": rng.choice(["cad", "USD", "", "eur"]),
            "grand_total": total, "subtotal": total, "tax_lines": [], "line_items": lines,
            "confidence_score": 0.8, "supplier_province": rng.choice(["ON", "QC", ""])}  # fmt: skip


def insert(conn: sqlite3.Connection, table: str, row: dict) -> None:
    """``row``'s values for the columns ``table`` has; a NOT NULL column with no default and no value gets ''."""
    columns = {r[1]: r for r in conn.execute(f"PRAGMA table_info({table})")}
    if not columns:
        return
    values = {k: v for k, v in row.items() if k in columns}
    for name, (_, _, kind, notnull, default, pk) in columns.items():
        if notnull and default is None and not pk and name not in values:
            values[name] = 0 if "INT" in kind.upper() or "REAL" in kind.upper() else ""
    conn.execute(f"INSERT OR IGNORE INTO {table} ({', '.join(values)}) VALUES ({', '.join('?' for _ in values)})",
                 list(values.values()))  # fmt: skip


def seed(conn: sqlite3.Connection, rng: random.Random, n: int) -> None:
    at = "2026-10-01T09:00:00"
    for i in range(1, n + 1):
        vendor = rng.choice(VENDORS)
        ai = coding(rng, vendor, i)
        status = rng.choice(STATUSES)
        final = {**ai, "currency": rng.choice(["CAD", "usd", ai["currency"]])} if status != "review" else None
        insert(conn, "invoices", {
            "id": i, "source_path": f"/old/invoices/{i}.pdf", "file_name": f"{i}.pdf", "file_sha256": f"{i:064x}",
            "status": status, "vendor_name": vendor, "vendor_key": old_key(vendor),
            "invoice_number": ai["invoice_number"], "invoice_date": ai["invoice_date"], "currency": ai["currency"],
            "grand_total": ai["grand_total"],
            "model_confidence": 0.8, "adjusted_confidence": 0.7, "requires_review": 1, "ai_output": json.dumps(ai),
            "final_output": json.dumps(final) if final else None, "validation": json.dumps({"issues": []}),
            "meta": json.dumps({"inference": {"model": rng.choice(["offline-coder", "chat"])}}), "created_at": at,
            "reviewed_at": at if final else None, "reviewer": "Ann" if final else None,
            "due_date": "2026-11-01" if ai["grand_total"] <= 0 else None,
        })  # fmt: skip
        for li in (final or {}).get("line_items", []):
            reviewer = rng.choice(["Ann", "Bob", AUTONOMOUS_REVIEWER])
            insert(conn, "feedback", {
                "invoice_id": i, "line_number": li["line_number"], "vendor_key": old_key(vendor), "vendor_name": vendor,
                "description": li["description"], "amount": li["amount"], "suggested_gl": rng.choice(GL),
                "final_gl": rng.choice(GL[:3]), "suggested_cc": rng.choice(CC), "final_cc": rng.choice(CC[:2]),
                "outcome": rng.choice(["accepted", "corrected"]), "reviewer": reviewer, "created_at": at,
            })  # fmt: skip
            situation = f"{old_key(vendor)}|line|gl|{li['description'].lower()}"
            insert(conn, "lessons", {"situation": situation, "vendor_key": old_key(vendor), "vendor_name": vendor,
                                     "kind": "line", "field": "gl", "value": li["predicted_gl_code"], "seen": 1,
                                     "created_at": at, "updated_at": at})  # fmt: skip
            insert(conn, "lesson_approvals", {"invoice_id": i, "situation": situation, "at": at})
        if final:
            insert(conn, "events", {"invoice_id": i, "action": "approved", "actor": "Ann", "created_at": at,
                                    "detail": json.dumps({"bulk": rng.random() < 0.3})})  # fmt: skip
        if rng.random() < 0.3:
            insert(conn, "erp_invoices", {"vendor_key": old_key(vendor), "number_key": f"n{i}", "total": 10.0 * i,
                                          "vendor_name": vendor, "invoice_number": f"N-{i}",
                                          "imported_at": at})  # fmt: skip
    for k, vendor in enumerate(VENDORS):
        key = old_key(vendor)
        insert(conn, "vendors", {"vendor_key": key, "display_name": vendor, "notes": "kept", "updated_at": at})
        insert(conn, "purchase_orders", {"po_key": f"po{k}", "po_number": f"PO-{k}", "vendor_name": vendor,
                                         "vendor_key": key, "updated_at": at})  # fmt: skip
        insert(conn, "po_lines", {"po_key": f"po{k}", "line_number": 1, "description": "Toner", "quantity": 2,
                                  "unit_price": 5.0, "amount": 10.0})  # fmt: skip
        state = rng.choice(["supervised", "suspended", "autonomous"])
        insert(conn, "supplier_profiles", {"key": f"name:{key}", "display_name": vendor, "state": state,
                                           "template_json": json.dumps({"n": k}), "updated_at": at})  # fmt: skip
        if state == "suspended":
            insert(conn, "events", {"action": "autonomy_suspended", "actor": "AP Coder", "created_at": at,
                                    "detail": json.dumps({"key": f"name:{key}"})})  # fmt: skip
        insert(conn, "supplier_outcomes", {"supplier_key": f"name:{key}", "invoice_id": 1, "field": "grand_total",
                                           "correct": 1, "at": at})  # fmt: skip
        insert(conn, "template_snapshots", {"invoice_id": k + 1, "supplier_key": f"name:{key}", "at": at,
                                            "before_json": "{}", "after_json": "{}"})  # fmt: skip


def contents(path: Path) -> dict:
    conn = sqlite3.connect(path)
    try:
        made = [list(r) for r in conn.execute("SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY rowid")]
        tables = {}
        for kind, name, _, sql in made:
            if kind != "table" or name.startswith("sqlite_") or (sql or "").upper().startswith("CREATE VIRTUAL"):
                continue
            rows = [list(r) for r in conn.execute(f"SELECT * FROM {name}")]
            if name == "settings":
                rows = [[k, "(new each time)" if k == "summary_id" else v] for k, v in rows]
            tables[name] = sorted(rows, key=lambda r: json.dumps(r, default=repr))
        return {"schema": made, "tables": tables}
    finally:
        conn.close()


def main() -> None:
    n, out = int(sys.argv[1]), Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    results = []
    for k, snapshot in enumerate(sorted(OLD.glob("schema-*.sql"))):
        db = out / snapshot.stem / "ap_coder.db"
        db.parent.mkdir()
        conn = sqlite3.connect(db)
        conn.executescript(snapshot.read_text(encoding="utf-8"))
        seed(conn, random.Random(k), n)
        conn.commit()
        conn.close()
        Store(db)
        results.append({"snapshot": snapshot.name, **contents(db)})
    for result in results:
        print(json.dumps(result, sort_keys=True, default=repr))


if __name__ == "__main__":
    main()
