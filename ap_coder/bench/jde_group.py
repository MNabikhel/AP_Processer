"""A made-up group of JD Edwards companies, Canadian and US (D-267), around Maple Ridge Holdings (``jde_company``):
every name and number is invented here.

``GROUP`` is report 9 (F0010) of six companies: three Canadian (CA and CAD), one US (US and USD), and two that report 9
gets wrong the way the pilot tester's does: a USD company whose address says CA, and one with no country. ``group(n)``
is ``n`` companies, the six first. ``companies_rows`` writes report 9 as the build sheet titles it, as ReportsNow does
by default ("Co", "Name", "Cur Cod", "Country") or by JD Edwards' field names; ``companies_xlsx`` as a workbook.

For the speed measurement (``python -m ap_coder.bench companies``), ``Scale`` says how big the books are and the
generators give the rows each loader takes (``chart_rows``, ``unit_rows``, ``supplier_rows``, ``ledger_rows``,
``journal_csv``, ``po_rows``), every company's together, drawn from ``random.Random(seed)``."""

from __future__ import annotations

import io
import random
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from openpyxl import Workbook

from ..core.chart import BuRow, ChartRow

GROUP = (
    ("00210", "Maple Ridge Holdings Ltd.", "CAD", "CA"),
    ("00220", "Maple Ridge Constructors Ltd.", "CAD", "CA"),
    ("00230", "Fraser Bend Civil Ltd.", "CAD", "CA"),
    ("00310", "Maple Ridge Builders Inc.", "USD", "US"),
    ("00320", "Cascade Line Contractors Inc.", "USD", "CA"),  # a USD company whose address book entry says CA
    ("00410", "Maple Ridge Caribbean Ltd.", "USD", ""),  # no country
)
_PLACES = ("Kelso", "Ashcroft", "Pembina", "Tolmie", "Wakefield", "Larch Bay", "Oxbow", "Sunder", "Kettle Falls",
           "Granite Hill", "Birch Run", "Coldwater", "Redcliff", "Hartwell")  # fmt: skip
TITLES = {
    "build": ("Company", "Company Name", "Currency Code", "Country"),
    "reportsnow": ("Co", "Name", "Cur Cod", "Country"),
    "fields": ("CCCO", "CCNAME", "CCCRCD", "ALCTR"),
}


def group(n: int = len(GROUP)) -> list[tuple[str, str, str, str]]:
    """``n`` companies (code, name, currency, country): the six of ``GROUP``, then made-up ones, two Canadian to one
    US."""
    out = list(GROUP[:n])
    for k in range(len(out), n):
        place = _PLACES[k % len(_PLACES)]
        us = k % 3 == 2
        out.append((f"{500 + k * 10:05d}", f"{place} {'Builders Inc.' if us else 'Constructors Ltd.'} {k:02d}",
                    "USD" if us else "CAD", "US" if us else "CA"))  # fmt: skip
    return out


def companies_rows(companies: list[tuple[str, str, str, str]] | None = None, titles: str = "build") -> list[list[str]]:
    """Report 9 as a sheet: its column names (``TITLES[titles]``), then a row each, and JD Edwards' default company
    00000 (no company's books: never loaded)."""
    rows = [list(TITLES[titles]), ["00000", "Default Company - Do Not Use", "CAD", "CA"]]
    return rows + [list(c) for c in companies or GROUP]


def companies_xlsx(companies: list[tuple[str, str, str, str]] | None = None, titles: str = "build") -> bytes:
    book = Workbook()
    sheet = book.active
    for row in companies_rows(companies, titles):
        sheet.append(row)
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def master_records(companies: list[tuple[str, str, str, str]] | None = None) -> list[dict[str, str]]:
    """Report 9's rows as ``jde_exports.master_workbook_xlsx`` takes them (by JD Edwards' field names)."""
    return [dict(zip(("CCCO", "CCNAME", "CCCRCD", "ALCTR"), c, strict=True)) for c in companies or GROUP]


# --- At scale ----------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Scale:
    """How big the group's books are, all its companies together."""

    companies: int = 20
    chart_rows: int = 50_000
    suppliers: int = 20_000
    ledger_rows: int = 1_000_000
    journal_lines: int = 3_000_000
    po_lines: int = 100_000

    def scaled(self, share: float) -> Scale:
        """The same books at ``share`` of their size (the companies as they are)."""
        return Scale(
            self.companies,
            *(
                max(1, int(getattr(self, f) * share))
                for f in ("chart_rows", "suppliers", "ledger_rows", "journal_lines", "po_lines")
            ),
        )


OBJECTS = ("5110", "5120", "5210", "5230", "6110", "6120", "6410", "7110", "7140", "7210", "7310", "7510", "7710")


def units_of(company: str, count: int) -> list[str]:
    """A company's business units: its number's last three digits and a two-digit department ("2101001")."""
    return [f"{company[-3:]}{k:04d}" for k in range(1, count + 1)]


def chart_rows(scale: Scale) -> tuple[list[ChartRow], list[BuRow]]:
    """The chart (every company's business units, each with ``OBJECTS`` and subsidiaries up to ``chart_rows``) and
    the business units, each with its company."""
    codes = [c[0] for c in group(scale.companies)]
    per_unit = len(OBJECTS) * 2
    units_each = max(1, scale.chart_rows // (per_unit * len(codes)))
    rows, units = [], []
    for code in codes:
        for unit in units_of(code, units_each):
            units.append(BuRow(unit, f"Job {unit}", code, "JB", ""))
            rows.append(ChartRow(unit, "", "", f"Job {unit}", 3, "N", "", code))
            for obj in OBJECTS:
                rows.append(ChartRow(unit, obj, "", f"Cost {obj}", 7, "", "", code))
                rows.append(ChartRow(unit, obj, "0100", f"Cost {obj} labour", 8, "", "", code))
    return rows[: scale.chart_rows], units


CHART_HEADER = ("Company", "Business Unit", "Object Account", "Subsidiary", "Account Description", "L D", "P E")


def write_chart_csv(path: Any, scale: Scale) -> int:
    """The group's chart as one CSV (the build sheet's report 1, every company's), written a row at a time: as
    ``chart_rows`` makes it, ``scale.chart_rows`` rows. How many rows."""
    codes = [c[0] for c in group(scale.companies)]
    units_each = max(1, scale.chart_rows // (len(OBJECTS) * 2 * len(codes)))
    n = 0
    with open(path, "w", encoding="utf-8", newline="") as out:
        out.write(",".join(CHART_HEADER) + "\r\n")
        for code in codes:
            for unit in units_of(code, units_each):
                rows = [(unit, "", "", f"Job {unit}", 3, "N")] + [
                    row for obj in OBJECTS for row in ((unit, obj, "", f"Cost {obj}", 7, ""),
                                                       (unit, obj, "0100", f"Cost {obj} labour", 8, ""))]  # fmt: skip
                for bu, obj, sub, description, lod, edit in rows:
                    if n >= scale.chart_rows:
                        return n
                    out.write(f"{code},{bu},{obj},{sub},{description},{lod},{edit}\r\n")
                    n += 1
    return n


def supplier_rows(scale: Scale) -> list[dict[str, str]]:
    """The suppliers as the vendor master's import takes them: number, name, GST/HST number."""
    return [{"erp_id": str(10000 + k), "vendor_name": f"Supplier {k:05d} {_PLACES[k % len(_PLACES)]} Ltd",
             "gst_number": f"{100000000 + k * 7:09d}RT0001"} for k in range(scale.suppliers)]  # fmt: skip


def _company_of_supplier(k: int, codes: list[str]) -> str:
    """Most suppliers bill one company; one in five bills two (theirs and the next)."""
    return codes[(k + (k % 5 == 0) * random.Random(k).randint(0, 1)) % len(codes)]


def ledger_rows(scale: Scale, seed: int = 1) -> Iterator[dict[str, object]]:
    """JD Edwards' vouchers already entered (``Store.import_erp_vouchers``' rows): the key company-type-document."""
    rng = random.Random(seed)
    codes = [c[0] for c in group(scale.companies)]
    for n in range(scale.ledger_rows):
        k = rng.randrange(scale.suppliers)
        company = _company_of_supplier(k, codes)
        yield {"key": f"{company}-PV-{1_000_000 + n}-001", "vendor_name": f"Supplier {k:05d} {_PLACES[k % 14]} Ltd",
               "invoice_number": f"INV-{n:07d}", "total": round(50 + rng.random() * 5000, 2),
               "invoice_date": f"2025-{1 + n % 12:02d}-{1 + n % 28:02d}", "an8": str(10000 + k),
               "document": str(1_000_000 + n), "batch": str(500 + n // 400), "currency": "CAD"}  # fmt: skip


JOURNAL_HEADER = ("Document Number", "Doc Type", "Doc Co", "G/L Date", "Line Number", "Account Number", "Amount",
                  "Remark", "Supplier Number", "Supplier Name", "Invoice Number", "Curr Code")  # fmt: skip


def journal_csv(scale: Scale, seed: int = 2) -> bytes:
    """The past invoice coding as the build sheet's G/L lines (F0911, the payable lines left out, as
    ``jde_exports.master_workbook_xlsx`` titles them), three lines a voucher on average, every company's."""
    rng = random.Random(seed)
    codes = [c[0] for c in group(scale.companies)]
    units = {code: units_of(code, max(1, scale.chart_rows // (len(OBJECTS) * 2 * len(codes)))) for code in codes}
    out = io.StringIO()
    out.write(",".join(JOURNAL_HEADER) + "\r\n")
    n, doc = 0, 0
    while n < scale.journal_lines:
        doc += 1
        k = rng.randrange(scale.suppliers)
        company = _company_of_supplier(k, codes)
        day = f"2025-{1 + doc % 12:02d}-{1 + doc % 28:02d}"
        for line in range(1, min(rng.randint(1, 5), scale.journal_lines - n) + 1):
            unit, obj = rng.choice(units[company]), OBJECTS[(k + line) % len(OBJECTS)]
            out.write(
                f"{2_000_000 + doc},PV,{company},{day},{line}.0,{unit}.{obj},{round(20 + rng.random() * 900, 2)},"
                f"Job costs {obj},{10000 + k},Supplier {k:05d},J-{doc},CAD\r\n"
            )
            n += 1
    return out.getvalue().encode("utf-8")


def po_rows(scale: Scale, seed: int = 3) -> list[dict[str, object]]:
    """Open PO lines (``po.rows_from_records``' rows), five lines an order; one order number in ten is used again in
    another company, for the same supplier one time in two."""
    rng = random.Random(seed)
    codes = [c[0] for c in group(scale.companies)]
    rows = []
    for n in range(scale.po_lines // 5):
        number = str(40000 + (n if n % 10 else n // 10))
        k = rng.randrange(scale.suppliers)
        company = codes[n % len(codes)]
        unit = units_of(company, 1)[0]
        for line in range(1, 6):
            price = round(5 + rng.random() * 400, 2)
            rows.append({"po_number": number, "vendor_name": "", "supplier_number": str(10000 + k), "order_type": "OP",
                         "order_company": company, "line_number": line, "erp_line": f"{line}.000",
                         "description": f"Material {line} for job {unit}", "quantity": 10.0, "unit_price": price,
                         "amount": round(price * 10, 2), "open_amount": None, "open_quantity": 10.0, "uom": "EA",
                         "received_quantity": None, "gl_code": OBJECTS[line], "cost_center": unit, "status": "open",
                         "tax_flag": "", "tax_code": "", "tax_area": "", "currency": "CAD"})  # fmt: skip
    return rows
