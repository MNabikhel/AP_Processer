"""Sample data for the demo and the tests."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ...coding.po import po_key
from ..memory import vendor_key

if TYPE_CHECKING:
    from . import Store


SAMPLE_DATA_DIR = Path(__file__).resolve().parent.parent.parent.parent / "data"


def load_sample_setup(
    store: Store, data_dir: Path = SAMPLE_DATA_DIR, cost_centers: bool = True, actor: str | None = None
) -> None:
    """Load the bundled sample GL accounts, tax mapping and policy (for trying things out), and with ``cost_centers``
    the sample cost centers. The dashboard's "Use the sample GL accounts" and the demo leave the cost centers out: the
    pilot codes each line to a GL account only (D-53); a cost center list can still be imported later."""
    import csv

    tables = [("gl_accounts", "chart_of_accounts.csv", "gl_code")]
    if cost_centers:
        tables.append(("cost_centers", "cost_centers.csv", "cost_center"))
    for table, filename, code_col in tables:
        with (data_dir / filename).open(encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.DictReader(fh))
        meals = "meals" if table == "gl_accounts" and rows and "meals" in rows[0] else None  # D-90
        store.import_accounts(table, rows, code_col, "description", "category", meals_column=meals, actor=actor)
    with (data_dir / "tax_gl_mapping.csv").open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            store.set_tax_treatment(row["tax_type"], row["treatment"], row.get("gl_code") or "", actor=actor)
    store.set_setting("policy_notes", (data_dir / "coding_policy.md").read_text(encoding="utf-8-sig"), actor=actor)


def load_sample_cost_centers(store: Store, data_dir: Path = SAMPLE_DATA_DIR) -> None:
    """Load the bundled sample cost centers on their own: the list a company that codes to cost centers imports
    later (the sample setup leaves them out, D-53)."""
    import csv

    with (data_dir / "cost_centers.csv").open(encoding="utf-8-sig", newline="") as fh:
        store.import_accounts("cost_centers", list(csv.DictReader(fh)), "cost_center", "description", "category")


def load_sample_vendor_master(store: Store, data_dir: Path = SAMPLE_DATA_DIR, only_new: bool = False) -> list[str]:
    """Load the bundled sample vendor master (the demo vendors but one). Returns the vendor keys (with
    ``only_new``: only those added, existing vendor records being left as they are)."""
    import csv

    from ...coding.vendors import master_columns, master_rows

    with (data_dir / "vendor_master.csv").open(encoding="utf-8-sig", newline="") as fh:
        records = list(csv.DictReader(fh))
    rows = master_rows(records, master_columns(list(records[0]) if records else []))
    result = store.import_vendor_master(rows, only_new=only_new)
    return sorted(result["inserted"]) if only_new else sorted({vendor_key(r["vendor_name"]) for r in rows})


def load_sample_purchase_orders(store: Store, data_dir: Path = SAMPLE_DATA_DIR) -> list[str]:
    """Load the bundled sample purchase orders (they match the sample invoices). Returns the PO keys."""
    import csv

    from ...coding.po import map_columns, rows_from_records

    with (data_dir / "purchase_orders.csv").open(encoding="utf-8-sig", newline="") as fh:
        records = list(csv.DictReader(fh))
    rows, _ = rows_from_records(records, map_columns(list(records[0]) if records else []))
    store.import_purchase_orders(rows)
    return sorted({po_key(r["po_number"]) for r in rows})
