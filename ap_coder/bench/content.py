"""What goes on a random invoice: parties, numbers, taxes, and how values are printed.

Everything here draws from a `random.Random` passed in, so a case is reproducible from its seed.
Amounts are `Decimal` rounded half-up to the cent, the way an invoicing system would print them.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")


def money(x: Decimal | float | int | str) -> Decimal:
    return Decimal(str(x)).quantize(CENT, rounding=ROUND_HALF_UP)


# --- registration numbers ---------------------------------------------------------------------


def luhn_check_digit(digits: str) -> int:
    """The Luhn digit that makes `digits + d` valid (the CRA business number uses Luhn on 9 digits)."""
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 0:  # these positions are doubled once the check digit is appended
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return (10 - total % 10) % 10


def luhn_valid(number: str) -> bool:
    digits = [c for c in number if c.isdigit()]
    if len(digits) < 2:
        return False
    s = "".join(digits)
    return luhn_check_digit(s[:-1]) == int(s[-1])


def make_bn(rng: random.Random) -> str:
    """A 9-digit business number that passes the Luhn check (first digit never 0)."""
    body = str(rng.randint(1, 9)) + "".join(str(rng.randint(0, 9)) for _ in range(7))
    return body + str(luhn_check_digit(body))


def make_qst(rng: random.Random) -> str:
    """A 10-digit Quebec QST number. Revenu Quebec's own check digit is not public enough to
    reproduce reliably, so the digits are random; readers should check the format only."""
    return "1" + "".join(str(rng.randint(0, 9)) for _ in range(9))


def format_bn(rng: random.Random, bn: str) -> str:
    ref = "0001" if rng.random() < 0.85 else f"000{rng.randint(2, 3)}"
    return rng.choice(
        [f"{bn} RT {ref}", f"{bn} RT{ref}", f"{bn}RT{ref}", f"{bn[:5]} {bn[5:]} RT {ref}", f"{bn}-RT-{ref}"]
    )


def format_qst(rng: random.Random, qst: str) -> str:
    return rng.choice([f"{qst} TQ 0001", f"{qst}TQ0001", f"{qst} TQ0001", f"{qst}-TQ-0001"])


def reg_value(raw: str) -> str:
    """Normalized registration number: digits and program letters, no spaces or dashes."""
    return "".join(c for c in raw.upper() if c.isalnum())


# --- taxes ------------------------------------------------------------------------------------

# (code, capture field, rate). Rates as of 2026. Nova Scotia's HST fell from 15% to 14% on
# 2025-04-01 (see data/canada_tax_rates.csv); the benchmark uses 14%.
TAX_RULES: dict[str, list[tuple[str, str, Decimal]]] = {
    "ON": [("HST", "hst_amount", Decimal("0.13"))],
    "NS": [("HST", "hst_amount", Decimal("0.14"))],
    "BC": [("GST", "gst_amount", Decimal("0.05")), ("PST", "pst_amount", Decimal("0.07"))],
    "QC": [("GST", "gst_amount", Decimal("0.05")), ("QST", "qst_amount", Decimal("0.09975"))],
    "AB": [("GST", "gst_amount", Decimal("0.05"))],
    "SK": [("GST", "gst_amount", Decimal("0.05")), ("PST", "pst_amount", Decimal("0.06"))],
    "MB": [("GST", "gst_amount", Decimal("0.05")), ("RST", "pst_amount", Decimal("0.07"))],
}


@dataclass
class Tax:
    code: str  # GST, HST, PST, QST, RST, SALES
    field: str  # capture field it fills
    rate: Decimal
    amount: Decimal


def compute_taxes(province: str, taxable: Decimal) -> list[Tax]:
    """Each tax rounded on its own; QST is on the price before GST (the rule since 2013)."""
    return [Tax(code, fld, rate, money(taxable * rate)) for code, fld, rate in TAX_RULES[province]]


def rate_text(rate: Decimal, lang: str) -> str:
    pct = (rate * 100).normalize()
    s = format(pct, "f")
    if lang == "fr":
        return s.replace(".", ",") + " %"
    return s + "%"


# --- places and parties -----------------------------------------------------------------------

CITIES = {
    "ON": [
        ("Toronto", "M"),
        ("Mississauga", "L"),
        ("Ottawa", "K"),
        ("Hamilton", "L"),
        ("London", "N"),
        ("Kitchener", "N"),
        ("Sudbury", "P"),
        ("Barrie", "L"),
        ("Kingston", "K"),
    ],
    "QC": [
        ("Montréal", "H"),
        ("Québec", "G"),
        ("Laval", "H"),
        ("Gatineau", "J"),
        ("Sherbrooke", "J"),
        ("Trois-Rivières", "G"),
        ("Lévis", "G"),
        ("Saint-Hyacinthe", "J"),
    ],
    "BC": [("Vancouver", "V"), ("Surrey", "V"), ("Burnaby", "V"), ("Kelowna", "V"), ("Victoria", "V")],
    "AB": [("Calgary", "T"), ("Edmonton", "T"), ("Red Deer", "T"), ("Lethbridge", "T")],
    "SK": [("Regina", "S"), ("Saskatoon", "S"), ("Moose Jaw", "S")],
    "MB": [("Winnipeg", "R"), ("Brandon", "R"), ("Steinbach", "R")],
    "NS": [("Halifax", "B"), ("Dartmouth", "B"), ("Truro", "B"), ("Sydney", "B")],
}
AREA_CODES = {
    "ON": ["416", "905", "613", "519", "705"],
    "QC": ["514", "450", "418", "819"],
    "BC": ["604", "250"],
    "AB": ["403", "780"],
    "SK": ["306"],
    "MB": ["204"],
    "NS": ["902"],
}
US_CITIES = [
    ("Houston", "TX", "77"),
    ("Columbus", "OH", "43"),
    ("Buffalo", "NY", "14"),
    ("Seattle", "WA", "98"),
    ("Detroit", "MI", "48"),
    ("Chicago", "IL", "60"),
    ("Burlington", "VT", "05"),
    ("Portland", "ME", "04"),
]
US_SALES_TAX = {
    "TX": "0.0825",
    "OH": "0.0575",
    "NY": "0.08",
    "WA": "0.101",
    "MI": "0.06",
    "IL": "0.1025",
    "VT": "0.06",
    "ME": "0.055",
}
STREETS_EN = [
    "King St W",
    "Main St",
    "Industrial Pkwy",
    "Commerce Crt",
    "Dundas St E",
    "Portage Ave",
    "Jasper Ave",
    "Granville St",
    "Barrington St",
    "Albert St",
    "Victoria Ave",
    "Steeles Ave W",
    "Hwy 7",
    "Bay St",
]
STREETS_FR = [
    "rue Sainte-Catherine O",
    "boul. René-Lévesque",
    "rue Principale",
    "boul. des Laurentides",
    "rue Saint-Joseph",
    "ch. de la Côte-de-Liesse",
    "av. du Parc",
    "rue King O",
    "boul. Industriel",
]
POSTAL_LETTERS = "ABCEGHJKLMNPRSTVWXYZ"

EN_NAMES = [
    "Northwind",
    "Maple Ridge",
    "Great Lakes",
    "Polaris",
    "Cedar Point",
    "Bluewater",
    "Summit",
    "Prairie",
    "Granite",
    "Harbourfront",
    "Silver Birch",
    "True North",
    "Red River",
    "Lakeshore",
    "Ironwood",
    "Pinecrest",
    "Atlantic",
    "Rideau",
    "Fraser Valley",
    "Okanagan",
    "Westbrook",
    "Kingsway",
    "Eastgate",
    "Riverside",
    "Aurora",
    "Birchmount",
    "Frontenac",
    "Muskoka",
]
INDUSTRIES = [
    "Industrial Supply",
    "Office Products",
    "Electrical",
    "Mechanical",
    "Janitorial Services",
    "Logistics",
    "Printing",
    "IT Solutions",
    "Safety Equipment",
    "Fasteners",
    "Plumbing & Heating",
    "Hydraulics",
    "Packaging",
    "Lab Supplies",
    "Fleet Services",
    "Landscaping",
    "Consulting Group",
    "Equipment Rentals",
]
EN_SUFFIX = ["Inc.", "Ltd.", "Limited", "Corp.", "Co.", "LP", "Inc.", "Ltd."]
FR_KINDS = [
    "Distributions",
    "Les Entreprises",
    "Groupe",
    "Fournitures",
    "Services techniques",
    "Imprimerie",
    "Transport",
    "Équipements",
    "Électricité",
    "Plomberie",
    "Location",
]
FR_NAMES = [
    "Gagnon",
    "Tremblay",
    "Laval",
    "Beauce",
    "Saint-Laurent",
    "Lévesque",
    "Côté",
    "Boréal",
    "Mauricie",
    "Outaouais",
    "Bouchard",
    "Richelieu",
    "Bélanger",
    "Pelletier",
]
FR_SUFFIX = ["inc.", "ltée", "Inc.", "S.E.N.C."]
US_NAMES = [
    "Lone Star",
    "Pacific Coast",
    "Liberty",
    "Keystone",
    "Buckeye",
    "Granite State",
    "Midwest",
    "Empire",
    "Golden Gate",
    "Cascade",
    "Green Mountain",
]
US_SUFFIX = ["LLC", "Inc.", "Corp.", "Co."]
SMALL_FIRST = ["Bob", "Dave", "Lisa", "Raj", "Marie", "Kevin", "Sandra", "Luc", "Tom", "Nadia"]
SMALL_TRADES = [
    "Snow Removal",
    "Small Engine Repair",
    "Carpentry",
    "Window Cleaning",
    "Bookkeeping",
    "Lawn Care",
    "Courier",
    "Handyman Services",
    "Catering",
]
UTILITIES = ["Hydro", "Power", "Energy", "Gas", "Water & Wastewater", "Telecom"]

# The AP department's own companies: they are the "Bill To" on every invoice, never the vendor.
CUSTOMERS = [
    ("Hartwell Manufacturing Inc.", "ON"),
    ("Groupe Boisvert ltée", "QC"),
    ("Kestrel Foods Ltd.", "BC"),
    ("Northline Energy Corp.", "AB"),
]


@dataclass
class Party:
    name: str
    lines: list[str]  # address lines
    province: str  # Canadian province or US state
    country: str
    phone: str = ""
    fax: str = ""
    email: str = ""
    web: str = ""
    bn: str = ""  # 9 digits
    qst: str = ""  # 10 digits


def postal_code(rng: random.Random, first: str) -> str:
    def letter() -> str:
        return rng.choice(POSTAL_LETTERS)

    d = rng.randint
    return f"{first}{d(0, 9)}{letter()} {d(0, 9)}{letter()}{d(0, 9)}"


def phone(rng: random.Random, area: str) -> str:
    tail = f"{rng.randint(0, 9999):04d}"
    return rng.choice(
        [
            f"({area}) 555-{tail}",
            f"{area}-555-{tail}",
            f"{area}.555.{tail}",
            f"1-{area}-555-{tail}",
            f"+1 {area} 555 {tail}",
        ]
    )


def slug(name: str) -> str:
    keep = "".join(c.lower() for c in name.split(" ")[0] if c.isalpha() and c.isascii())
    return keep or "info"


def make_vendor(rng: random.Random, kind: str, province: str) -> Party:
    """kind: "en" | "fr" | "us" | "small" | "utility"."""
    if kind == "us":
        city, state, zip2 = rng.choice(US_CITIES)
        name = f"{rng.choice(US_NAMES)} {rng.choice(INDUSTRIES)} {rng.choice(US_SUFFIX)}"
        street = f"{rng.randint(10, 9800)} {rng.choice(['Commerce Dr', 'Main St', 'Industrial Blvd', 'Oak Ave'])}"
        lines = [street, f"{rng.choice(['Suite', 'Unit'])} {rng.randint(100, 450)}"] if rng.random() < 0.4 else [street]
        lines.append(f"{city}, {state} {zip2}{rng.randint(100, 999)}")
        area = str(rng.randint(201, 989))
        p = Party(name, lines, state, "US", phone=phone(rng, area))
    else:
        city, first = rng.choice(CITIES[province])
        fr = kind == "fr" or (province == "QC" and rng.random() < 0.6)
        if kind == "small":
            name = f"{rng.choice(SMALL_FIRST)}'s {rng.choice(SMALL_TRADES)}"
        elif kind == "utility":
            name = f"{rng.choice(EN_NAMES)} {rng.choice(UTILITIES)}" + rng.choice([" Inc.", " Corp.", ""])
        elif fr:
            name = f"{rng.choice(FR_KINDS)} {rng.choice(FR_NAMES)} {rng.choice(FR_SUFFIX)}"
        else:
            name = f"{rng.choice(EN_NAMES)} {rng.choice(INDUSTRIES)} {rng.choice(EN_SUFFIX)}"
        street = (
            f"{rng.randint(10, 9800)}, {rng.choice(STREETS_FR)}"
            if fr
            else f"{rng.randint(10, 9800)} {rng.choice(STREETS_EN)}"
        )
        lines = [street]
        if rng.random() < 0.35:
            lines.append(
                rng.choice(["Bureau", "Local"]) + f" {rng.randint(100, 450)}" if fr else f"Unit {rng.randint(1, 40)}"
            )
        sep = rng.choice([" ", "  ", ", "])
        lines.append(
            f"{city} ({province}){sep}{postal_code(rng, first)}"
            if fr
            else f"{city}, {province}{sep}{postal_code(rng, first)}"
        )
        area = rng.choice(AREA_CODES[province])
        p = Party(name, lines, province, "CA", phone=phone(rng, area))
        p.bn = make_bn(rng)
        if province == "QC":
            p.qst = make_qst(rng)
        if rng.random() < 0.4:
            p.fax = phone(rng, area)
    domain = slug(p.name) + rng.choice([".ca", ".com"]) if p.country == "CA" else slug(p.name) + ".com"
    if rng.random() < 0.6:
        p.email = (
            rng.choice(["ar", "billing", "accounts", "info", "factures" if kind == "fr" else "invoices"]) + "@" + domain
        )
    if rng.random() < 0.5:
        p.web = "www." + domain
    return p


def make_customer(rng: random.Random, prov: str) -> Party:
    """One of the AP department's companies, at its site in `prov`."""
    name, _home = rng.choice(CUSTOMERS)
    city, first = rng.choice(CITIES[prov])
    street = f"{rng.randint(10, 9800)} {rng.choice(STREETS_FR if prov == 'QC' else STREETS_EN)}"
    lines = [street]
    if rng.random() < 0.5:
        lines.insert(0, rng.choice(["Attn: Accounts Payable", "Attention: AP Dept.", "c/o Accounts Payable"]))
    lines.append(f"{city}, {prov} {postal_code(rng, first)}")
    p = Party(name, lines, prov, "CA", phone=phone(rng, rng.choice(AREA_CODES[prov])))
    p.bn = make_bn(rng)
    return p


# --- line items -------------------------------------------------------------------------------

# (english, french, unit_en, unit_fr, low, high, decimals of quantity)
GOODS = [
    ("Nitrile gloves, box of 100", "Gants de nitrile, boîte de 100", "box", "boîte", 8, 25, 0),
    ("Copy paper 8.5x11, case of 10", "Papier 8,5x11, caisse de 10", "cs", "caisse", 38, 62, 0),
    ("Hex bolt 3/8-16 x 2 in., zinc", "Boulon hex. 3/8-16 x 2 po, zinc", "ea", "un.", 0.18, 0.95, 0),
    ("LED panel 2x4 ft, 40W", "Panneau DEL 2x4 pi, 40 W", "ea", "un.", 65, 140, 0),
    ("Hydraulic hose assembly 1/2 in.", "Boyau hydraulique 1/2 po", "ea", "un.", 45, 180, 0),
    ("Safety glasses, clear, ANSI Z87", "Lunettes de sécurité, claires", "pr", "paire", 4, 14, 0),
    ("Toner cartridge HP 58X", "Cartouche de toner HP 58X", "ea", "un.", 120, 260, 0),
    ("Pallet wrap 18 in. x 1500 ft", "Film étirable 18 po x 1500 pi", "roll", "rouleau", 18, 34, 0),
    ("Electrician - labour", "Électricien - main-d'oeuvre", "hr", "h", 85, 125, 1),
    ("Technician on site", "Technicien sur place", "hr", "h", 70, 110, 1),
    ("Consulting services - September", "Services-conseils - septembre", "hr", "h", 120, 220, 1),
    ("Monthly maintenance contract", "Contrat d'entretien mensuel", "mo", "mois", 250, 1900, 0),
    ("Forklift rental (weekly)", "Location chariot élévateur (semaine)", "wk", "sem.", 380, 720, 0),
    ("Janitorial service - Unit 4", "Service d'entretien ménager - Local 4", "visit", "visite", 140, 420, 0),
    ("Cable Cat6 plenum, 1000 ft", "Câble Cat6 plénum, 1000 pi", "box", "boîte", 210, 380, 0),
    ("Shipping cartons 18x12x12", "Boîtes d'expédition 18x12x12", "bdl", "paquet", 22, 48, 0),
    ("Disinfectant 4 L", "Désinfectant 4 L", "ea", "un.", 12, 29, 0),
    ("Diesel exhaust fluid 9.46 L", "Liquide d'échappement diesel 9,46 L", "ea", "un.", 14, 26, 0),
    ("Software subscription - 12 users", "Abonnement logiciel - 12 utilisateurs", "mo", "mois", 90, 600, 0),
    ("Printing - brochures 8 pages", "Impression - dépliants 8 pages", "M", "M", 180, 640, 0),
    ("Calibration of pressure gauges", "Étalonnage de manomètres", "ea", "un.", 35, 95, 0),
    ("Bearing 6205-2RS", "Roulement 6205-2RS", "ea", "un.", 6, 21, 0),
    ("Lab beakers 250 mL, pack of 12", "Béchers 250 mL, paquet de 12", "pk", "paquet", 28, 64, 0),
    ("Fleet vehicle inspection", "Inspection de véhicule de flotte", "ea", "un.", 95, 240, 0),
]
UTILITY_LINES = [
    ("Basic monthly charge", "Frais de base mensuels", 18, 45),
    ("Delivery charge", "Frais de livraison", 40, 160),
    ("Regulatory charges", "Frais réglementaires", 6, 30),
    ("Energy charge", "Frais d'énergie", 90, 1600),
    ("Water consumption", "Consommation d'eau", 30, 400),
    ("Wastewater charge", "Frais d'eaux usées", 25, 300),
    ("Long distance & features", "Interurbains et options", 10, 90),
]


@dataclass
class LineItem:
    description: str
    quantity: Decimal | None
    unit: str
    unit_price: Decimal | None
    amount: Decimal
    sku: str = ""
    kind: str = "item"  # "item" | "discount" | "freight"


def make_lines(rng: random.Random, n: int, lang: str, credit: bool = False) -> list[LineItem]:
    out = []
    picks = rng.sample(GOODS, min(n, len(GOODS))) + [rng.choice(GOODS) for _ in range(max(0, n - len(GOODS)))]
    for en, fr, uen, ufr, lo, hi, qdec in picks:
        if qdec:
            qty = Decimal(rng.randint(1, 32)) / 2
        elif hi > 150:
            qty = Decimal(rng.choice([1, 1, 1, 1, 2, 2, 3, 4]))
        else:
            qty = Decimal(rng.choice([1, 1, 1, 2, 2, 3, 4, 5, 6, 10, 12, 20, 24, 25, 50, 100]))
        price = Decimal(str(round(rng.uniform(lo, hi), 2)))
        if price < 1 and rng.random() < 0.5:
            price = Decimal(str(round(rng.uniform(lo, hi), 4))).quantize(Decimal("0.0001"))
        if credit:
            qty = -qty if rng.random() < 0.5 else qty
        amount = money(qty * price)
        if credit and amount > 0:
            amount = -amount
        sku = rng.choice(
            [
                "",
                f"{rng.choice('ABCDEHKMPRST')}{rng.choice('ABCDEHKMPRST')}-{rng.randint(1000, 99999)}",
                f"{rng.randint(100000, 999999)}",
            ]
        )
        out.append(LineItem(fr if lang == "fr" else en, qty, ufr if lang == "fr" else uen, price, amount, sku))
    return out


# --- printed formats --------------------------------------------------------------------------

MONTHS_EN = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]
MONTHS_FR = [
    "janvier",
    "février",
    "mars",
    "avril",
    "mai",
    "juin",
    "juillet",
    "août",
    "septembre",
    "octobre",
    "novembre",
    "décembre",
]
DATE_STYLES_EN = ["iso", "mon_d_y", "month_d_y", "dd/mm/yyyy", "mm/dd/yyyy", "d_mon_y", "dd-mon-yyyy", "yyyy/mm/dd"]
DATE_STYLES_FR = ["iso", "fr_long", "dd/mm/yyyy", "dd-mm-yyyy", "yyyy/mm/dd"]
DATE_STYLES_US = ["mm/dd/yyyy", "mon_d_y", "month_d_y", "iso", "mm-dd-yyyy"]


def format_date(d: date, style: str) -> str:
    m3 = MONTHS_EN[d.month - 1][:3]
    if style == "iso":
        return d.isoformat()
    if style == "mon_d_y":
        return f"{m3} {d.day}, {d.year}"
    if style == "month_d_y":
        return f"{MONTHS_EN[d.month - 1]} {d.day}, {d.year}"
    if style == "dd/mm/yyyy":
        return f"{d.day:02d}/{d.month:02d}/{d.year}"
    if style == "dd-mm-yyyy":
        return f"{d.day:02d}-{d.month:02d}-{d.year}"
    if style == "mm/dd/yyyy":
        return f"{d.month:02d}/{d.day:02d}/{d.year}"
    if style == "mm-dd-yyyy":
        return f"{d.month:02d}-{d.day:02d}-{d.year}"
    if style == "d_mon_y":
        return f"{d.day} {m3} {d.year}"
    if style == "dd-mon-yyyy":
        return f"{d.day:02d}-{m3}-{d.year}"
    if style == "yyyy/mm/dd":
        return f"{d.year}/{d.month:02d}/{d.day:02d}"
    if style == "fr_long":
        day = "1er" if d.day == 1 else str(d.day)
        return f"{day} {MONTHS_FR[d.month - 1]} {d.year}"
    raise ValueError(style)


def random_date(rng: random.Random) -> date:
    return date(2025, 1, 6) + timedelta(days=rng.randint(0, 640))


@dataclass
class MoneyStyle:
    """How this invoice prints amounts. `decimal` "." or ","; `symbol` "", "$" (prefix in English,
    suffix in French); `neg` "minus" | "paren" | "cr"; `code` currency code printed on the grand total."""

    decimal: str = "."
    group: str = ","
    symbol: str = ""
    neg: str = "minus"
    code: str = ""
    code_after: bool = False

    def fmt(self, amount: Decimal, *, symbol: bool | None = None, code: bool = False) -> str:
        neg = amount < 0
        q = abs(money(amount))
        whole, frac = f"{q:.2f}".split(".")
        groups = []
        while len(whole) > 3:
            groups.insert(0, whole[-3:])
            whole = whole[:-3]
        groups.insert(0, whole)
        num = self.group.join(groups) + self.decimal + frac
        sym = self.symbol if symbol is None else ("$" if symbol else "")
        if sym:
            num = f"{num} $" if self.decimal == "," else f"${num}"
        if neg:
            if self.neg == "paren":
                num = f"({num})"
            elif self.neg == "cr":
                num = f"{num} CR"
            else:
                num = f"-{num}"
        if code and self.code:
            num = f"{num} {self.code}" if self.code_after else f"{self.code} {num}"
        return num


def money_style(rng: random.Random, lang: str, currency: str) -> MoneyStyle:
    if lang == "fr" or (lang == "bi" and rng.random() < 0.5):
        st = MoneyStyle(decimal=",", group=rng.choice([" ", " ", ""]), symbol=rng.choice(["$", "$", ""]))
    else:
        st = MoneyStyle(group=rng.choice([",", ",", ",", ""]), symbol=rng.choice(["", "", "$"]))
    st.neg = (
        rng.choice(["minus", "minus", "paren", "cr"]) if st.decimal == "." else rng.choice(["minus", "minus", "paren"])
    )
    if rng.random() < (0.4 if currency == "USD" else 0.12):
        st.code = currency
        st.code_after = rng.random() < 0.4
    return st


def number_text(x: Decimal, decimal: str, places: int | None = None) -> str:
    """Quantities and unit prices: no grouping; trailing zeros kept to `places`."""
    if places is None:
        places = 0 if x == x.to_integral_value() else max(2, -x.normalize().as_tuple().exponent)
    s = f"{x:.{places}f}"
    return s.replace(".", ",") if decimal == "," else s


# --- identifiers ------------------------------------------------------------------------------


def invoice_number(rng: random.Random, credit: bool = False) -> str:
    y = rng.choice(["2025", "2026", "26"])
    n = rng.randint(1, 99999)
    pre = "CN" if credit else rng.choice(["INV", "IN", "F", "FA", "A", "S"])
    styles = [
        f"{pre}-{y}-{n:05d}",
        f"{n:06d}",
        f"{pre}{n:05d}",
        f"{y}/{n:04d}",
        f"{pre}-{n}",
        f"{n}-{rng.choice('ABC')}",
        f"{rng.randint(10, 99)}-{n:05d}",
        f"{pre}{y}{n:04d}",
        f"{rng.randint(1000000, 9999999)}",
    ]
    if credit:
        styles = [f"CN-{n:05d}", f"CM{y}-{n:04d}", f"NC-{n:05d}", f"C{n:06d}", f"{n:06d}-CR"]
    return rng.choice(styles)


def po_number(rng: random.Random) -> str:
    return rng.choice(
        [
            f"PO-{rng.randint(10000, 99999)}",
            f"45000{rng.randint(10000, 99999)}",
            f"P{rng.randint(10, 99)}-{rng.randint(1000, 9999)}",
            f"{rng.randint(100000, 999999)}",
            f"MNT-2026-{rng.randint(1, 999):03d}",
            f"BC-{rng.randint(1000, 99999)}",
        ]
    )


@dataclass
class Terms:
    raw: str
    days: int | None


def payment_terms(rng: random.Random, lang: str) -> Terms:
    days = rng.choice([15, 30, 30, 30, 45, 60, 0])
    if lang == "fr":
        if days == 0:
            return Terms(rng.choice(["Payable sur réception", "Payable à réception"]), 0)
        return Terms(rng.choice([f"Net {days} jours", f"{days} jours net", f"Net {days}"]), days)
    if days == 0:
        return Terms(rng.choice(["Due on receipt", "Payable on receipt", "Upon receipt"]), 0)
    return Terms(
        rng.choice([f"Net {days}", f"Net {days} days", f"NET {days}", f"2% 10, Net {days}", f"{days} days"]), days
    )
