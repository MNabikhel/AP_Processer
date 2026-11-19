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

import datetime as dt
import io
import random
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal
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
    chart_rows: int = 3_000_000  # (D-267: a group's chart, mostly job cost codes)
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


def unit_rows(scale: Scale) -> list[BuRow]:
    """The business units of ``chart_rows``' chart, without the chart."""
    codes = [c[0] for c in group(scale.companies)]
    units_each = max(1, scale.chart_rows // (len(OBJECTS) * 2 * len(codes)))
    return [BuRow(unit, f"Job {unit}", code, "JB", "") for code in codes for unit in units_of(code, units_each)]


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


# The whole group's F0901 unfiltered (D-272): of each business unit's 26 accounts, one in two postable (blank), the
# others titles, budget, inactive and machine-only, and one with a code JD Edwards' own list does not have.
FULL_EDITS = ("N", "B", "I", "M", "N", "B", "I", "M", "N", "B", "I", "M", "Z")
FULL_HEADER = ("Company", "Business Unit", "Object Account", "Subsidiary", "Account Description", "L D", "P E",
               "Account ID")  # fmt: skip
FULL_UNLISTED = 997  # one business unit in this many is missing from the business units report


def _full_units(rows: int, companies: int) -> tuple[list[str], int]:
    codes = [c[0] for c in group(companies)]
    return codes, max(1, rows // ((len(OBJECTS) * 2 + 1) * len(codes)))


def full_unit_rows(rows: int, companies: int = 20) -> list[BuRow]:
    """The business units report of ``write_full_chart``'s chart: one in ``FULL_UNLISTED`` left out."""
    codes, each = _full_units(rows, companies)
    return [BuRow(unit, f"Job {unit}", code, "JB", "") for code in codes for k, unit in enumerate(units_of(code, each))
            if (k + 1) % FULL_UNLISTED]  # fmt: skip


def write_full_chart(path: Any, rows: int, companies: int = 20, encoding: str = "utf-8") -> int:
    """The group's whole chart (F0901 unfiltered, every company's, every posting edit code) as ReportsNow saves it as
    text: tab-delimited, CRLF, a business unit right-aligned in 12 characters, a quotation mark in some descriptions;
    ``rows`` rows at most (``FULL_EDITS``: about half of the accounts not posted to). How many rows were written."""
    codes, each = _full_units(rows, companies)
    n = 0
    with open(path, "w", encoding=encoding, newline="") as out:
        out.write("\t".join(FULL_HEADER) + "\r\n")
        for code in codes:
            for unit in units_of(code, each):
                lines = [(f"{unit:>12}", "", "", f"Job {unit}", 3, "N")]
                for k, (obj, sub) in enumerate((obj, sub) for obj in OBJECTS for sub in ("", "0100")):
                    said = f'Cost {obj} 1/2" pipe' if k % 5 == 0 else f"Cost {obj} {'labour' if sub else 'material'}"
                    lines.append((f"{unit:>12}", obj, sub, said, 8 if sub else 7, FULL_EDITS[k // 2] if k % 2 else ""))
                for bu, obj, sub, description, lod, edit in lines:
                    if n >= rows:
                        return n
                    out.write(f"{code}\t{bu}\t{obj}\t{sub}\t{description}\t{lod}\t{edit}\t{n + 10_000_000:08d}\r\n")
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


# --- The construction group of the pilot day (``pilot_day``, D-275) ----------------------------------------------
# Seven companies: three Canadian ones in three provinces, two US ones, a USD company whose address says CA and one
# with no country. ``PILOT`` are the companies in the pilot; an invoice of another is held. Every name and number is
# invented here.

CONSTRUCTION = (*GROUP[:4], ("00330", "Ridgeline Drilling Inc.", "USD", "US"), *GROUP[4:])
PILOT = ("00210", "00220", "00230", "00310", "00320")
# Each company's province or state (where its jobs are: the place of supply) and the address it is billed at.
SITES = {
    "00210": ("AB", "410 Elm Street, Edmonton, AB T5J 0N3"),
    "00220": ("BC", "1180 Marine Drive, Kamloops, BC V2C 1A4"),
    "00230": ("ON", "75 Quarry Road, Sudbury, ON P3A 4R7"),
    "00310": ("WA", "2200 Pine Street, Spokane, WA 99201"),
    "00320": ("BC", "90 Front Street, Osoyoos, BC V0H 1V0"),
    "00330": ("MT", "18 Rimrock Road, Billings, MT 59101"),
    "00410": ("", "Harbour Road, Bridgetown, Barbados"),
}
AREAS = {"AB": "AB-GST", "BC": "BC-GSTPST", "ON": "ON-HST"}  # the tax area of a province (the default tax map's)
US_AREA = "USNOTAX"  # the tax area of a voucher of a US supplier
JOBS = ("Kelso Bridge", "Ashcroft Interchange", "Pembina Lift Station", "Tolmie Reservoir", "Oxbow Overpass")
CLOSED_JOBS = 2  # the first jobs of each company: finished, closed in the business units report
ORDERS_EACH = 12  # the open orders of a PO supplier in each company it bills
# A job's or the office's accounts: (object, subsidiary, description, posting edit code). A title (N), a budget (B),
# a machine-only (M) and an inactive (I) account, and codes JD Edwards' own list does not have (Z, Q).
JOB_ACCOUNTS = (("5000", "", "Job costs", "N"), ("5110", "", "Materials", ""),
                ("5110", "0100", "Materials - lumber", ""), ("5110", "0200", 'Materials - pipe 1/2" to 2"', ""),
                ("5120", "", "Concrete and aggregates", ""),
                ("5210", "", "Equipment rental", "L"), ("5230", "", "Fuel and lubricants", "U"),
                ("5310", "", "Subcontractors", ""), ("5410", "", "Small tools and supplies", ""),
                ("5510", "", "Freight in", ""), ("5900", "", "Job cost budget", "B"),
                ("5990", "", "Cost transfers", "M"), ("5995", "", "Job cost clearing", "Z"))  # fmt: skip
OFFICE_ACCOUNTS = (("2110", "", "Accounts payable - trade", ""), ("2420", "", "GST/HST receivable", ""),
                   ("6000", "", "Office expenses", "N"), ("6110", "", "Office supplies", ""),
                   ("6120", "", "Courier and postage", ""), ("6410", "", "Telephone and internet", ""),
                   ("7110", "", "Repairs and maintenance", ""), ("7140", "", "Safety supplies", ""),
                   ("7210", "", "Professional fees", ""), ("7210", "0100", "Professional fees - engineering", ""),
                   ("7210", "0200", "Professional fees - management", ""), ("7310", "", "Software subscriptions", ""),
                   ("7990", "", "Old suspense - do not use", "I"), ("7995", "", "Clearing - to be decided", "Q"),
                   ("5510", "", "Freight in", ""))  # fmt: skip
PAYABLE, TAX_OBJECT = "2110", "2420"
# The suppliers: (number, name as the supplier master has it, province or state, currency, terms code, kind, the
# companies it bills, the object.subsidiary it is coded to). "po": it sells on purchase orders (job costs); "office":
# its invoices go to the company's office, one account; "jobs": to the job the invoice names, no PO.
SUPPLIERS = (
    (4410101, "KELSO READY MIX LTD", "AB", "CAD", "N", "po", ("00210",), "5120"),
    (4410102, "ASHCROFT STEEL SUPPLY LTD", "BC", "CAD", "N", "po", ("00220",), "5110"),
    (4410103, "PEMBINA EQUIPMENT RENTALS LTD", "AB", "CAD", "N", "po", ("00210", "00230"), "5210"),
    (4410104, "TOLMIE FUEL SERVICES LTD", "AB", "CAD", "T", "office", ("00210",), "5230"),
    (4410105, "WAKEFIELD OFFICE PRODUCTS INC", "ON", "CAD", "N", "office", ("00230",), "6110"),
    (4410106, "LARCH BAY COURIER LTD", "AB", "CAD", "N", "office", ("00210", "00220"), "6120"),
    (4410107, "OXBOW SAFETY SUPPLY LTD", "BC", "CAD", "F", "office", ("00220",), "7140"),
    (4410108, "SUNDER TELECOM INC", "ON", "CAD", "N", "office", ("00230",), "6410"),
    (4410109, "GRANITE HILL AGGREGATES LTD", "ON", "CAD", "N", "po", ("00230",), "5120"),
    (4410110, "BIRCH RUN LUMBER LTD", "BC", "CAD", "N", "po", ("00220",), "5110.0100"),
    (4410111, "COLDWATER ENGINEERING LTD", "AB", "CAD", "F", "office", ("00210",), "7210.0100"),
    (4410112, "REDCLIFF HYDRAULICS LTD", "AB", "CAD", "N", "office", ("00210", "00230"), "7110"),
    (4410113, "HARTWELL PIPE & VALVE LTD", "AB", "CAD", "N", "po", ("00210", "00220"), "5110.0200"),
    (4410114, "RIDGE TOOL RENTALS LTD", "BC", "CAD", "N", "office", ("00220",), "5210"),
    (4410115, "PRAIRIE PROMO SUPPLY INC", "AB", "CAD", "N", "office", ("00210",), "6110"),
    (4410116, "NORTHGATE SOFTWARE LTD", "ON", "CAD", "N", "office", ("00210",), "7310"),
    (4410117, "SUMMIT SURVEY & LAYOUT LTD", "AB", "CAD", "N", "jobs", ("00210",), "5310"),
    (4410118, "FRASER VALLEY CONCRETE PUMPING LTD", "BC", "CAD", "N", "jobs", ("00220",), "5310"),
    (4410801, "KELSO READY MIX LTD (DNU)", "AB", "CAD", "N", "retired", ("00210",), "5120"),
    (4410900, "MAPLE RIDGE CONSTRUCTORS LTD", "BC", "CAD", "N", "office", ("00210",), "7210.0200"),
    (4420201, "CASCADE BEARING & DRIVE INC", "WA", "USD", "N", "office", ("00310",), "7110"),
    (4420202, "LONE PINE CALIBRATION LLC", "MT", "USD", "N", "office", ("00310",), "7210"),
    (4420203, "SPOKANE CONCRETE PUMPING LLC", "WA", "USD", "N", "po", ("00310",), "5310"),
    (4420204, "BIG SKY DRILLING SUPPLY INC", "MT", "USD", "N", "office", ("00330",), "5510"),
    (4420205, "OKANOGAN CRANE SERVICE INC", "WA", "USD", "N", "office", ("00320",), "7110"),
    (4420206, "ISLAND MARINE FREIGHT LTD", "", "USD", "N", "office", ("00410",), "5510"),
)
SUPPLIER_CITIES = {"AB": ("Calgary", "T2E 7H1"), "BC": ("Kamloops", "V2C 6B4"), "ON": ("Sudbury", "P3B 1Z3"),
                   "WA": ("Spokane", "99202"), "MT": ("Billings", "59102"), "": ("Bridgetown", "BB11000")}  # fmt: skip
# What a supplier prints for a purchase coded to an object: a few phrasings ({job}: the job's name).
WORDS = {
    "5110": ("Rebar 15M x 6m bundles", "Form ties and snap ties", "Anchor bolts galvanized"),
    "5110.0100": ("SPF 2x6x16 lumber", "Plywood 3/4 in. sheathing", "Pressure treated 4x4x10"),
    "5110.0200": ('PVC pipe 1/2" sch 40', 'Ball valve 2" brass', 'Copper elbow 3/4"'),
    "5120": ("Ready mix concrete 32 MPa", "Crushed gravel 20 mm", "Concrete pump wash-out"),
    "5210": ("Excavator rental weekly", "Skid steer rental daily", "Telehandler rental weekly"),
    "5230": ("Diesel fuel delivered", "Hydraulic oil 20 L pail", "Clear diesel to site tank"),
    "5310": ("Site survey and layout", "Concrete pumping services", "Grade checking crew"),
    "5410": ("Rotary hammer rental", "Laser level rental", "Cut-off saw blades"),
    "5510": ("Freight", "Delivery charge", "Shipping and handling"),
    "6110": ("Copy paper letter size", "Toner cartridge black", "File folders letter"),
    "6120": ("Courier same day downtown", "Courier overnight Calgary", "Courier pickup and delivery"),
    "6410": ("Mobile phone plan monthly", "Office internet service", "Long distance charges"),
    "7110": ("Hydraulic cylinder repair", "Pump seal kit replaced", "Hose assembly and fittings"),
    "7140": ("Hard hats class E", "Safety glasses clear", "Hi-vis vests orange"),
    "7210": ("Calibration of survey equipment", "Load test certification", "Inspection services"),
    "7210.0100": ("Engineering services", "Structural review drawings", "Geotechnical report"),
    "7210.0200": ("Management fee", "Shared services fee", "Project management fee"),
    "7310": ("Estimating software licence", "Project software subscription", "Cloud storage plan"),
}


def unit_code(company: str, number: int) -> str:
    """A business unit of a company: its number's last three digits and the unit's ("2101000", "2102401")."""
    return f"{company[-3:]}{number:04d}"


def office_of(company: str) -> str:
    return unit_code(company, 1000)


def jobs_of(company: str) -> list[str]:
    return [unit_code(company, 2401 + k) for k in range(len(JOBS))]


def open_jobs(company: str) -> list[str]:
    return jobs_of(company)[CLOSED_JOBS:]


def job_name(unit: str) -> str:
    return f"Job {unit[-4:]} {JOBS[int(unit[-2:]) - 1]}"


@dataclass(frozen=True)
class GroupSupplier:
    number: int
    name: str  # as the supplier master has it
    province: str  # or state; "" outside Canada and the US
    currency: str
    terms: str  # the payment terms code
    kind: str  # "po", "office", "jobs", "retired"
    companies: tuple[str, ...]
    code: str  # object.subsidiary

    @property
    def country(self) -> str:
        return "CA" if self.province in AREAS else "US" if self.province else "BB"

    @property
    def tax_id(self) -> str:
        """A business number with its program account (a US supplier's an EIN), passing the check digit."""
        rng = random.Random(f"group-tax-id-{self.number % 1000 if self.kind == 'retired' else self.number}")
        eight = "8" + "".join(rng.choice("0123456789") for _ in range(7))
        from .jde_exports import _check_digit

        number = eight + _check_digit(eight)
        return f"{number}RT0001" if self.country == "CA" else f"{number[:2]}-{number[2:]}" if self.country == "US" \
            else ""  # fmt: skip

    @property
    def tax_area(self) -> str:
        return AREAS.get(self.province, US_AREA)

    @property
    def printed(self) -> str:
        """Its name as its invoices print it: the master's capitals in mixed case, "(DNU)" left out."""
        name = self.name.replace(" (DNU)", "")
        return " ".join(w if w in ("LLC", "&") else w.capitalize() for w in name.split()).replace("Llc", "LLC")

    @property
    def address(self) -> tuple[str, str, str]:
        """(street, city, postal code)."""
        city, postal = SUPPLIER_CITIES[self.province]
        return f"{100 + self.number % 900} Industrial Way", city, postal


@dataclass(frozen=True)
class PastLine:
    account: str  # BusinessUnit.Object.Subsidiary
    amount: Decimal  # in the voucher's currency, a provincial tax inside
    remark: str
    subledger: str = ""
    subledger_type: str = ""
    units: Decimal | None = None


@dataclass(frozen=True)
class PastVoucher:
    document: int
    company: str
    supplier: int
    invoice_number: str
    invoice_date: dt.date
    lines: tuple[PastLine, ...]
    tax: Decimal  # the GST or HST recovered (on the pay item; a G/L line to the company's TAX_OBJECT)
    currency: str
    purchase_order: str = ""
    paid: bool = True

    @property
    def gross(self) -> Decimal:
        return sum((li.amount for li in self.lines), Decimal(0)) + self.tax


@dataclass(frozen=True)
class GroupPoLine:
    order: int
    company: str
    line: Decimal  # 1.000, 2.000...
    supplier: int
    description: str  # 30 characters at most
    ordered: Decimal
    unit_cost: Decimal
    uom: str
    account: str
    subledger: str = ""
    subledger_type: str = ""
    currency: str = "CAD"
    tax_area: str = ""
    order_type: str = "OP"

    @property
    def amount(self) -> Decimal:
        return (self.ordered * self.unit_cost).quantize(Decimal("0.01"))


@dataclass
class GroupBooks:
    """The group's books as of ``on``: its companies, business units, chart, suppliers, past year of vouchers (the
    ledger and the past coding) and open purchase order lines."""

    on: dt.date
    companies: tuple[tuple[str, str, str, str], ...]
    units: list[BuRow]
    accounts: list[ChartRow]
    suppliers: list[GroupSupplier]
    vouchers: list[PastVoucher]
    po_lines: list[GroupPoLine]

    def supplier(self, number: int) -> GroupSupplier:
        return next(s for s in self.suppliers if s.number == number)

    def company_name(self, code: str) -> str:
        return next(name for c, name, _cur, _ctr in self.companies if c == code)

    def currency_of(self, code: str) -> str:
        return next(cur for c, _name, cur, _ctr in self.companies if c == code)

    def orders(self, supplier: int, company: str) -> dict[int, list[GroupPoLine]]:
        out: dict[int, list[GroupPoLine]] = {}
        for li in self.po_lines:
            if li.supplier == supplier and li.company == company:
                out.setdefault(li.order, []).append(li)
        return out


def _money(x: float) -> Decimal:
    return Decimal(str(round(x, 2))).quantize(Decimal("0.01"))


def _chart_of(company: str) -> tuple[list[BuRow], list[ChartRow]]:
    units, rows = [], []
    for unit in (office_of(company), *jobs_of(company)):
        office = unit == office_of(company)
        closed = not office and unit not in open_jobs(company)
        units.append(BuRow(unit, "Head office" if office else job_name(unit), company, "AD" if office else "JB",
                           "N" if closed else ""))  # fmt: skip
        rows.append(ChartRow(unit, "", "", "Head office" if office else job_name(unit), 3, "N", "", company))
        for obj, sub, words, edit in OFFICE_ACCOUNTS if office else JOB_ACCOUNTS:
            rows.append(ChartRow(unit, obj, sub, words, 8 if sub else 7, edit, "", company))
    return units, rows


def provincial_rate(company: str) -> Decimal:
    """The PST a supplier to a job of ``company`` charges (British Columbia's 7%), which stays in the expense."""
    return Decimal("0.07") if SITES[company][0] == "BC" and company in ("00220",) else Decimal(0)


def recovered_rate(company: str) -> Decimal:
    """The GST or HST of a Canadian company's site (none for the others)."""
    return {"AB": Decimal("0.05"), "BC": Decimal("0.05"), "ON": Decimal("0.13")}.get(SITES[company][0], Decimal(0)) \
        if company in ("00210", "00220", "00230") else Decimal(0)  # fmt: skip


def area_of(company: str) -> str:
    """The tax area of a voucher of ``company``: its site's province's, a US one for the others."""
    return AREAS.get(SITES[company][0], US_AREA) if recovered_rate(company) else US_AREA


def _account_for(supplier: GroupSupplier, company: str, unit: str) -> str:
    obj, _, sub = supplier.code.partition(".")
    return f"{unit}.{obj}.{sub}" if sub else f"{unit}.{obj}"


def construction_books(seed: int = 1, on: dt.date | None = None) -> GroupBooks:
    """The construction group's books as of ``on`` (default today): the past year of each supplier's vouchers for
    each company it bills, and ``ORDERS_EACH`` open orders of each PO supplier in each of its companies. The same
    seed and day give the same books."""
    on = on or dt.date.today()
    rng = random.Random(f"construction-group:{seed}")
    units: list[BuRow] = []
    accounts: list[ChartRow] = []
    for company, *_ in CONSTRUCTION:
        u, a = _chart_of(company)
        units += u
        accounts += a
    suppliers = [GroupSupplier(*row) for row in SUPPLIERS]
    vouchers: list[PastVoucher] = []
    billed: Counter[int] = Counter()
    document = 1_500_000
    for s in suppliers:
        for company in s.companies:
            count = 4 if s.kind == "retired" else 10 + rng.randrange(4)
            for n in range(count):
                document += 1
                age = 360 - (n * (330 // count)) - rng.randrange(8) - (400 if s.kind == "retired" else 0)
                day = on - dt.timedelta(days=age)
                if s.kind == "office" or s.kind == "retired":
                    unit = office_of(company) if s.code[0] in "67" else open_jobs(company)[0]
                else:  # a job: the closed ones first, then the open ones
                    jobs = jobs_of(company)
                    unit = jobs[min(len(jobs) - 1, n * len(jobs) // count)]
                if company in ("00310", "00320", "00330", "00410") and s.kind in ("office", "jobs"):
                    unit = office_of(company) if s.code[0] in "67" else unit
                account = _account_for(s, company, unit)
                lines = []
                for k in range(1 + (rng.random() < 0.3 and s.kind != "office")):
                    amount = _money(rng.uniform(120, 4800) * float(1 + provincial_rate(company)))
                    words = WORDS[s.code][(n + k) % 3]
                    remark = f"{words[:18]} {job_name(unit)[4:8] if unit != office_of(company) else 'HO'}"[:30]
                    ledger = ("", "")
                    units_used = None
                    if s.code == "5210":  # (the equipment rented: on an order its own, else mostly one)
                        ledger = ("EQ1042" if s.kind == "office" and n % 5 else f"EQ{1040 + (n + k) % 6}", "E")
                    if s.code == "5230":
                        units_used = Decimal(rng.choice((400, 600, 850, 1200)))
                    lines.append(PastLine(account, amount, remark, *ledger, units_used))
                net = sum((li.amount for li in lines), Decimal(0)) / (1 + provincial_rate(company))
                tax = _money(float(net * recovered_rate(company)))
                po = str(41000 + document % 900) if s.kind == "po" else ""
                billed[s.number] += 1  # (a supplier's numbers run on across its companies)
                vouchers.append(PastVoucher(document, company, s.number,
                                            f"{s.name[:2]}-{20000 + (s.number % 1000) * 37 + billed[s.number]}",
                                            day - dt.timedelta(days=3), tuple(lines), tax, s.currency, po,
                                            paid=age > 40))  # fmt: skip
    po_lines: list[GroupPoLine] = []
    order = 47000
    for s in suppliers:
        if s.kind != "po":
            continue
        for company in s.companies:
            for k in range(ORDERS_EACH):
                order += 7 + rng.randrange(5)
                unit = open_jobs(company)[k % len(open_jobs(company))]
                account = _account_for(s, company, unit)
                count = 2 + rng.randrange(3)
                for n in range(count):
                    words = WORDS[s.code][n % 3].upper()[:30]
                    ledger = (f"EQ{1040 + (k + n) % 6}", "E") if s.code == "5210" else ("", "")
                    qty = Decimal(rng.choice((4, 10, 12, 20, 40, 60)))
                    cost = _money(rng.uniform(8, 260))
                    po_lines.append(GroupPoLine(order, company, Decimal(n + 1), s.number, words, qty, cost,
                                                rng.choice(("EA", "M3", "LF", "DY")), account, *ledger,
                                                currency=s.currency, tax_area=area_of(company)))  # fmt: skip
                if k % 3 != 2:  # a freight line on two orders of three
                    po_lines.append(GroupPoLine(order, company, Decimal(count + 1), s.number, "FREIGHT", Decimal(1),
                                                _money(rng.uniform(40, 180)), "EA",
                                                f"{unit}.5510", currency=s.currency,
                                                tax_area=area_of(company)))  # fmt: skip
    return GroupBooks(on, CONSTRUCTION, units, accounts, suppliers, vouchers, po_lines)


# --- The group's files, as the pilot tester makes them ------------------------------------------------------------


def write_chart_text(path: Any, books: GroupBooks) -> int:
    """The whole chart (F0901 unfiltered, every company's) as ReportsNow saves it as Unicode text: UTF-16 with its
    byte order mark, tab-delimited, CRLF, a business unit right-aligned in 12 characters, a description's quotation
    marks as typed. How many rows."""
    with open(path, "w", encoding="utf-16", newline="") as out:
        out.write("\t".join(FULL_HEADER) + "\r\n")
        for n, row in enumerate(books.accounts):
            out.write(f"{row.company}\t{row.bu:>12}\t{row.object}\t{row.subsidiary}\t{row.description}\t{row.lod}\t"
                      f"{row.posting_edit}\t{20_000_000 + n:08d}\r\n")  # fmt: skip
    return len(books.accounts)


def _wrapped(title: str) -> str:
    """A long title as ReportsNow wraps it in its header cell: a line break at its first space."""
    return title.replace(" ", "\n", 1) if len(title) > 10 and " " in title else title


def _sheet_titles(key: str, how: str) -> list[str]:
    from .jde_exports import BUILD_SHEET

    if key == "companies" and how == "reportsnow":
        return list(TITLES["reportsnow"])
    return [field if how == "fields" else _wrapped(title) if how == "wrapped" else title
            for field, title in BUILD_SHEET[key]]  # fmt: skip


def _supplier_records(books: GroupBooks) -> list[dict[str, Any]]:
    out = []
    for s in books.suppliers:
        street, city, postal = s.address
        mail = "".join(c for c in s.printed.lower() if c.isalpha())[:14]
        out.append({"ABAN8": s.number, "ABALPH": s.name, "ABTAX": s.tax_id, "ABAT1": "V", "A6HDPY": "N",
                    "A6CRRP": s.currency, "A6TRAP": s.terms, "A6TXA2": s.tax_area,
                    "A6EXR2": "V" if s.country == "CA" else "E", "ALADD1": street, "ALCTY1": city,
                    "ALADDS": s.province, "ALADDZ": postal, "ALCTR": s.country,
                    "EAEMAL": f"ar@{mail}.example.com"})  # fmt: skip
    return out


def _ledger_records(books: GroupBooks, half: int) -> list[dict[str, Any]]:
    from .jde_exports import _date, _number

    middle = books.on - dt.timedelta(days=180)
    out = []
    for v in books.vouchers:
        if (v.invoice_date >= middle) + 1 != half:
            continue
        s = books.supplier(v.supplier)
        gl = v.invoice_date + dt.timedelta(days=3)
        out.append({"RPDOC": v.document, "RPDCT": "PV", "RPKCO": v.company, "RPSFX": "001", "RPSFE": 0,
                    "RPAN8": s.number, "RPALPH": s.name, "RPVINV": v.invoice_number, "RPDIVJ": _date(v.invoice_date),
                    "RPDGJ": _date(gl), "RPDDJ": _date(v.invoice_date + dt.timedelta(days=30)),
                    "RPAG": _number(v.gross), "RPAAP": _number(Decimal(0) if v.paid else v.gross), "RPACR": 0.0,
                    "RPCRCD": v.currency, "RPICU": 7000 + v.document % 300, "RPPST": "P" if v.paid else "A",
                    "RPPO": v.purchase_order or None, "RPPDCT": "OP" if v.purchase_order else None,
                    "RPPKCO": v.company if v.purchase_order else None,
                    "RPMCU": v.lines[0].account.split(".")[0].rjust(12)})  # fmt: skip
    return out


def _gl_records(books: GroupBooks, half: int) -> list[dict[str, Any]]:
    """The vouchers' G/L lines, the payable lines left out (as the build sheet asks), the GST or HST line kept."""
    from .jde_exports import _date

    middle = books.on - dt.timedelta(days=180)
    words = {f"{r.bu}.{r.object}{'.' + r.subsidiary if r.subsidiary else ''}": r.description for r in books.accounts}
    out = []
    for v in books.vouchers:
        if (v.invoice_date >= middle) + 1 != half:
            continue
        s = books.supplier(v.supplier)
        lines = [(li.account, li.amount, li.remark, li.subledger, li.subledger_type, li.units) for li in v.lines]
        if v.tax:
            lines.append((f"{office_of(v.company)}.{TAX_OBJECT}", v.tax, "GST/HST", "", "", None))
        for n, (account, amount, remark, ledger, kind, units) in enumerate(lines, 1):
            out.append({"GLDOC": v.document, "GLDCT": "PV", "GLKCO": v.company,
                        "GLDGJ": _date(v.invoice_date + dt.timedelta(days=3)), "GLJELN": float(n), "GLANI": account,
                        "GMDL01": words.get(account, ""), "GLAA": float(amount), "GLEXA": s.name, "GLEXR": remark,
                        "GLAN8": s.number, "ABALPH": s.name, "GLVINV": v.invoice_number,
                        "GLPO": v.purchase_order or None, "GLCRCD": v.currency, "GLSBL": ledger or None,
                        "GLSBLT": kind or None, "GLU": float(units) if units else None})  # fmt: skip
    return out


def _po_records(books: GroupBooks) -> list[dict[str, Any]]:
    from .jde_exports import COST_FORMAT, LINE_FORMAT, _date, _number

    out = []
    for li in books.po_lines:
        unit, obj, *sub = li.account.split(".")
        out.append({"PDDOCO": li.order, "PDDCTO": li.order_type, "PDKCOO": li.company,
                    "PDLNID": (float(li.line), LINE_FORMAT), "PDAN8": li.supplier,
                    "ABALPH": books.supplier(li.supplier).name, "PDDSC1": li.description, "PDLNTY": "J",
                    "PDUOM": li.uom, "PDUORG": _number(li.ordered), "PDUOPN": _number(li.ordered),
                    "PDUREC": _number(li.ordered), "PDPRRC": (float(li.unit_cost), COST_FORMAT),
                    "PDAEXP": _number(li.amount), "PDAOPN": _number(li.amount), "PDANI": li.account,
                    "PDOMCU": unit.rjust(12), "PDOBJ": obj, "PDSUB": sub[0] if sub else None, "PDTX": "Y",
                    "PDEXR1": "V" if li.tax_area != US_AREA else "E", "PDTXA1": li.tax_area, "PDLTTR": "400",
                    "PDNXTR": "510", "PDCRCD": li.currency, "PDTRDJ": _date(books.on - dt.timedelta(days=40)),
                    "PDSBL": li.subledger or None, "PDSBLT": li.subledger_type or None})  # fmt: skip
    return out


# The master workbook's sheets: (title, report, how its columns are titled). The chart is not in it: it is loaded
# from its text file (``write_chart_text``) on the command line, as the guide says of a group's whole chart.
GROUP_SHEETS = (("Companies", "companies", "reportsnow"), ("BUs", "units", "wrapped"),
                ("Suppliers", "supplier_master", "build"), ("Ledger 1", "vouchers", "build"),
                ("Ledger 2", "vouchers", "build"), ("GL lines 1", "gl_lines", "build"),
                ("GL lines 2", "gl_lines", "build"), ("Open POs", "purchase_orders", "wrapped"),
                ("Terms", "payment_terms", "fields"), ("Tax areas", "tax_areas", "build"))  # fmt: skip


def group_workbook_xlsx(books: GroupBooks) -> bytes:
    """Every report of the build sheet but the chart in one workbook, a sheet each (``GROUP_SHEETS``): report 9 as
    ReportsNow titles it by default, the business units and the open PO lines with their long titles wrapped, the
    payment terms by JD Edwards' field names, the others by the build sheet's titles; a sheet of notes last."""
    from .jde_exports import TAX_AREA_ROWS, TERMS_TABLE, _book, _date, _rows_of

    units = [{"MCMCU": u.code.rjust(12), "MCDL01": u.name, "MCCO": u.company, "MCSTYL": u.type,
              "MCPECC": u.status or None, "MCLDM": "3" if u.type == "AD" else "4",
              "MCADDS": SITES[u.company][0] or None} for u in books.units]  # fmt: skip
    terms = [{"PNPTC": code, "PNPTD": words, "PNDCP": pct, "PNDCD": days, "PNNDTP": net}
             for code, words, pct, days, net in TERMS_TABLE]  # fmt: skip
    areas = [{"TATXA1": area, "TADL01": words, **{f"TATA{n}": 9000 + n for n, _r in enumerate(rates, 1)},
              **{f"TATXR{n}": rate for n, rate in enumerate(rates, 1)}, "TAEFDJ": _date(dt.date(2013, 4, 1)),
              "TAEFTJ": _date(dt.date(2040, 12, 31))}
             for area, words, rates in (*TAX_AREA_ROWS, (US_AREA, "US purchases - no tax", (0,)))]  # fmt: skip
    records = {"companies": [dict(zip(("CCCO", "CCNAME", "CCCRCD", "ALCTR"), c, strict=True)) for c in books.companies],
               "units": units, "supplier_master": _supplier_records(books), "purchase_orders": _po_records(books),
               "payment_terms": terms, "tax_areas": areas}  # fmt: skip
    halves = {"Ledger 1": _ledger_records(books, 1), "Ledger 2": _ledger_records(books, 2),
              "GL lines 1": _gl_records(books, 1), "GL lines 2": _gl_records(books, 2)}  # fmt: skip
    sheets = [(title, _sheet_titles(key, how), _rows_of(key, halves.get(title) or records[key]), None)
              for title, key, how in GROUP_SHEETS]  # fmt: skip
    sheets.append(("Notes", ["Note"], [["Queried in ReportsNow from the build sheet; the chart saved as text."]], None))
    return _book(sheets)
