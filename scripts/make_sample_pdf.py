"""Generate the synthetic Canadian sample invoices (PDF + Document-Intelligence-style Markdown).

Usage: python scripts/make_sample_pdf.py [name ...]
Without names every sample is (re)generated. Requires PyMuPDF (pip install pymupdf).
Writes samples/<name>.pdf and samples/<name>.md; the matching answers live in
samples/ground_truth/<name>.json.

Optional keys in an invoice spec (the defaults reproduce the original three samples):
  x / widths    column positions and widths of the line-item table
  layout        "split" prints the invoice details on the right, beside the bill-to block
  accent        RGB colour (0-1) of a band across the top and of the title
  totals_x      x positions of the totals labels and values
  continued     suffix of the header on continuation pages
  page_label    page footer, formatted with {n} and {total}
"""

from __future__ import annotations

import sys
from pathlib import Path

import pymupdf

SAMPLES = Path(__file__).resolve().parent.parent / "samples"

INVOICES = {
    "northwind_ON_HST_NW-2026-0912": {
        "supplier": ["Northwind IT Solutions Inc.", "200 Bay Street, Suite 1500, Toronto, ON M5J 2J2",
                     "GST/HST Reg. No: 123456789 RT0001"],
        "title": "INVOICE",
        "meta": ["Invoice No: NW-2026-0912", "Invoice Date: September 14, 2026", "Due Date: October 14, 2026",
                 "PO Number: PO-88213"],
        "bill_to": ["Bill To / Ship To: Fabrikam Canada Ltd., Attn: IT Operations",
                    "5800 Explorer Drive, Mississauga, ON L4W 5K9"],
        "columns": ["#", "Description", "Qty", "Unit Price", "Amount"],
        "pages": [
            [
                ["1", "Dell Latitude 7450 laptop, 32GB RAM", "3", "1,450.00", "4,350.00"],
                ["2", "Dell PowerEdge R760 rack server - datacentre", "1", "8,900.00", "8,900.00"],
                ["3", "Microsoft 365 E3 licences - monthly", "25", "32.00", "800.00"],
                ["", "Carried forward", "", "", "14,050.00"],
            ],
            [
                ["", "Brought forward", "", "", "14,050.00"],
                ["4", "Implementation consulting - endpoint rollout (hours)", "12", "150.00", "1,800.00"],
            ],
        ],
        "after_table": ["Delivery & handling: $95.00"],
        "totals": [("Subtotal", "$15,945.00"), ("HST 13% (ON)", "$2,072.85"), ("Total Due (CAD)", "$18,017.85")],
        "footer": "Payment terms: Net 30. Thank you for your business.",
    },
    "laurentides_QC_TPS_TVQ_SIL-4471": {
        "supplier": ["Services Informatiques Laurentides Inc.", "1250 boul. René-Lévesque O., Montréal (QC) H3B 4W8",
                     "TPS/GST : 987654321 RT0001    TVQ/QST : 1234567890 TQ0001"],
        "title": "FACTURE / INVOICE",
        "meta": ["Facture no : SIL-4471", "Date : 2026-10-01", "Échéance : 2026-10-31"],
        "bill_to": ["Facturer à : Fabrikam Canada Ltée, Attn: Facilities / Installations",
                    "1000 rue De La Gauchetière O., Montréal (QC) H3B 4W5"],
        "columns": ["#", "Description", "Qté", "Prix unitaire", "Montant"],
        "pages": [
            [
                ["1", "Entretien ménager des bureaux - octobre 2026", "1", "1 200,00", "1 200,00"],
                ["2", "Fournitures de bureau - papier et cartouches de toner", "1", "340,00", "340,00"],
                ["3", "Réparation du système CVC (chauffage/climatisation)", "1", "860,00", "860,00"],
            ]
        ],
        "after_table": [],
        "totals": [("Sous-total", "2 400,00 $"), ("TPS 5 %", "120,00 $"), ("TVQ 9,975 %", "239,40 $"),
                   ("Total", "2 759,40 $")],
        "footer": "Payable dans les 30 jours.",
    },
    "pacific_BC_GST_PST_PO-77120": {
        "supplier": ["Pacific Office Supply Ltd.", "880 West Georgia Street, Vancouver, BC V6C 2W6",
                     "GST No. 555666777 RT0001    PST No. PST-1234-5678"],
        "title": "INVOICE",
        "meta": ["Invoice #: PO-77120", "Date: 2026-10-05", "Terms: Net 15"],
        "bill_to": ["Ship To: Fabrikam Canada Ltd. - Vancouver office, Requested by: IT Operations",
                    "1055 Dunsmuir Street, Vancouver, BC V7X 1L3"],
        "columns": ["#", "Description", "Qty", "Unit Price", "Amount"],
        "pages": [
            [
                ["1", "HP 26X toner cartridges (G, P)", "10", "89.00", "890.00"],
                ["2", "Dell 27\" monitor P2725H (G, P)", "4", "329.00", "1,316.00"],
                ["3", "Copy paper, letter, case of 10 reams (G, P)", "10", "52.00", "520.00"],
            ]
        ],
        "after_table": ["Tax codes: G = GST 5%, P = BC PST 7%"],
        "totals": [("Subtotal", "$2,726.00"), ("GST 5%", "$136.30"), ("PST 7%", "$190.82"),
                   ("Total CAD", "$3,053.12")],
        "footer": "Thank you!",
    },
    # Alberta: GST only (no provincial sales tax), mixed GL accounts and cost centers incl. inbound freight.
    "chinook_AB_GST_CCO-26-10418": {
        "supplier": ["Chinook Courier & Office Services Ltd.", "3220 - 12 Street NE, Calgary, AB T2E 7S9",
                     "GST Registration: 823145967 RT0001"],
        "title": "INVOICE",
        "layout": "split",
        "accent": (0.80, 0.33, 0.10),
        "meta": ["Invoice Number: CCO-26-10418", "Invoice Date: September 30, 2026", "Due Date: October 30, 2026",
                 "Account: FAB-CGY-01"],
        "bill_to": ["Bill To: Fabrikam Canada Ltd. - Calgary office", "Attn: Finance",
                    "855 2 Street SW, Suite 1200, Calgary, AB T2P 4J8"],
        "columns": ["#", "Description", "Qty", "Rate", "Amount"],
        "pages": [
            [
                ["1", "Same-day document courier - downtown Calgary runs (September)", "18", "24.50", "441.00"],
                ["2", "Inbound LTL freight - raw materials for Plant 1 (PO-90155), Edmonton to Calgary", "1",
                 "385.00", "385.00"],
                ["3", "Secure document shredding - 64-gallon bins", "6", "45.00", "270.00"],
                ["4", "Printing & binding - Q4 sales brochures", "500", "1.30", "650.00"],
            ]
        ],
        "after_table": ["All services subject to GST 5%. Alberta has no provincial sales tax."],
        "totals": [("Subtotal", "$1,746.00"), ("GST 5%", "$87.30"), ("Amount Due (CAD)", "$1,833.30")],
        "footer": "Remit by EFT within 30 days. Thank you for shipping with Chinook.",
    },
    # Manitoba: GST + RST 7% (recorded as PST MB); the design service is RST-exempt, so taxes differ per line.
    "redriver_MB_GST_RST_RRO-55821": {
        "supplier": ["Red River Office Interiors Ltd.", "1450 Waverley Street, Winnipeg, MB R3T 0P7",
                     "GST No. 741852963 RT0001    MB RST No. 160482-7"],
        "title": "SALES INVOICE",
        "accent": (0.55, 0.10, 0.15),
        "meta": ["Invoice: RRO-55821", "Date: 2026-08-21", "Customer PO: PO-90377", "Terms: Net 30"],
        "bill_to": ["Sold To / Ship To: Fabrikam Canada Ltd. - Winnipeg branch fit-out, Attn: Facilities",
                    "201 Portage Avenue, 18th Floor, Winnipeg, MB R3B 3K6"],
        "columns": ["#", "Description", "Qty", "Unit Price", "Tax", "Amount"],
        "x": [50, 75, 345, 390, 460, 510],
        "widths": [25, 265, 45, 70, 50, 75],
        "pages": [
            [
                ["1", "Height-adjustable sit-stand desk, 60 x 30 in", "8", "1,150.00", "G R", "9,200.00"],
                ["2", "Ergonomic task chair, mesh back", "8", "495.00", "G R", "3,960.00"],
                ["3", "Space planning & design consultation (hours)", "10", "120.00", "G", "1,200.00"],
                ["4", "Delivery charge", "1", "285.00", "G R", "285.00"],
            ]
        ],
        "after_table": ["Tax codes: G = GST 5%, R = Manitoba RST 7%. Design services are not subject to RST."],
        "totals": [("Subtotal", "$14,645.00"), ("GST 5%", "$732.25"), ("RST 7% (MB)", "$941.15"),
                   ("Invoice Total", "$16,318.40")],
        "footer": "Thank you for choosing Red River Office Interiors.",
    },
    # Nova Scotia: HST at the reduced 14% rate in force since 2025-04-01; legal fees go to CC800 by policy.
    "harbourview_NS_HST_HPS-2026-0347": {
        "supplier": ["Harbourview Professional Services LLP", "1801 Hollis Street, Suite 900, Halifax, NS B3J 3N4",
                     "HST Registration No. 356201847 RT0001"],
        "title": "INVOICE - PROFESSIONAL SERVICES",
        "layout": "split",
        "accent": (0.10, 0.25, 0.45),
        "meta": ["Invoice No.: HPS-2026-0347", "Date: July 31, 2026", "Engagement: FAB-2026-ADV",
                 "Period: July 1-31, 2026"],
        "bill_to": ["Client: Fabrikam Canada Ltd. - Atlantic office", "Attn: Controller",
                    "1959 Upper Water Street, Suite 1300", "Halifax, NS B3J 3N2"],
        "columns": ["#", "Service", "Hours", "Rate", "Fees"],
        "pages": [
            [
                ["1", "Year-end audit preparation and corporate tax advisory", "22", "210.00", "4,620.00"],
                ["2", "Process consulting - accounts payable workflow review", "16", "185.00", "2,960.00"],
                ["3", "Legal review of supplier contracts (requested by Finance)", "6", "325.00", "1,950.00"],
                ["4", "Disbursements - courier and document delivery", "1", "84.50", "84.50"],
            ]
        ],
        "after_table": [],
        "totals": [("Total fees and disbursements", "$9,614.50"), ("HST 14% (NS)", "$1,346.03"),
                   ("Total Due (CAD)", "$10,960.53")],
        "totals_x": (290, 500),
        "footer": "HST charged at 14%, the Nova Scotia rate effective April 1, 2025. Payable on receipt.",
    },
    # US vendor: USD, US sales tax (tax type OTHER), no GST/HST number, shipped to a US office.
    "cascade_US_SalesTax_INV-30981": {
        "supplier": ["Cascade Cloudworks, Inc.", "1201 Third Avenue, Suite 2200, Seattle, WA 98101, USA",
                     "WA UBI No. 604 118 352"],
        "title": "INVOICE",
        "accent": (0.05, 0.45, 0.40),
        "meta": ["Invoice #: INV-30981", "Invoice Date: Oct 1, 2026", "Due Date: Oct 31, 2026", "Currency: USD"],
        "bill_to": ["Bill To: Fabrikam Canada Ltd. - Accounts Payable",
                    "5800 Explorer Drive, Mississauga, ON L4W 5K9, Canada",
                    "Ship To: Fabrikam Canada Ltd. - Seattle project office, Attn: IT Operations",
                    "2001 Western Avenue, Suite 300, Seattle, WA 98121, USA"],
        "columns": ["#", "Item", "Qty", "Unit Price", "Amount"],
        "pages": [
            [
                ["1", "Observability Platform - Team plan, per seat, October 2026", "15", "49.00", "735.00"],
                ["2", "Premium Support - annual, Oct 2026 to Sep 2027 (prepaid)", "1", "3,600.00", "3,600.00"],
                ["3", "Edge gateway appliance CG-200", "2", "1,240.00", "2,480.00"],
                ["4", "Shipping - UPS Ground", "1", "45.00", "45.00"],
            ]
        ],
        "after_table": ["All prices in US dollars. Canadian GST/HST not charged."],
        "totals": [("Subtotal", "$6,860.00"), ("WA Sales Tax 10.35% (Seattle)", "$710.01"),
                   ("Total (USD)", "$7,570.01")],
        "totals_x": (300, 500),
        "footer": "Pay by ACH or wire within 30 days. Late payments accrue 1.5% interest per month.",
    },
    # Credit note (negative amounts) reversing part of northwind_ON_HST_NW-2026-0912.
    "northwind_ON_HST_CN-2026-0047": {
        "supplier": ["Northwind IT Solutions Inc.", "200 Bay Street, Suite 1500, Toronto, ON M5J 2J2",
                     "GST/HST Reg. No: 123456789 RT0001"],
        "title": "CREDIT NOTE",
        "meta": ["Credit Note No: CN-2026-0047", "Credit Date: September 28, 2026",
                 "Original Invoice: NW-2026-0912 (September 14, 2026)", "PO Number: PO-88213"],
        "bill_to": ["Bill To / Ship To: Fabrikam Canada Ltd., Attn: IT Operations",
                    "5800 Explorer Drive, Mississauga, ON L4W 5K9"],
        "columns": ["#", "Description", "Qty", "Unit Price", "Amount"],
        "pages": [
            [
                ["1", "Return - Dell Latitude 7450 laptop, 32GB RAM (RMA 55120)", "-1", "1,450.00", "-1,450.00"],
                ["2", "Credit - implementation consulting hours not delivered", "-4", "150.00", "-600.00"],
            ]
        ],
        "after_table": ["Reason: one laptop returned unopened; 4 of 12 consulting hours cancelled."],
        "totals": [("Subtotal", "-$2,050.00"), ("HST 13% (ON)", "-$266.50"), ("Total Credit (CAD)", "-$2,316.50")],
        "footer": "This credit will be applied against your next payment. Thank you for your business.",
    },
    # French-language Quebec invoice over two pages: TPS/TVQ, "1 234,56 $" amounts, several cost centers.
    "montroyal_QC_TPS_TVQ_ACMR-2026-1187": {
        "supplier": ["Agence Créative Mont-Royal inc.", "4200 boul. Saint-Laurent, bureau 600, Montréal (QC) H2W 2R2",
                     "No TPS : 812345678 RT0001    No TVQ : 1218765432 TQ0001"],
        "title": "FACTURE",
        "meta": ["Facture no : ACMR-2026-1187", "Date de facturation : 22 septembre 2026",
                 "Échéance : 22 octobre 2026", "Bon de commande : BC-4410"],
        "bill_to": ["Facturer à : Fabrikam Canada Ltée, Service du marketing",
                    "1000 rue De La Gauchetière O., bureau 2400, Montréal (QC) H3B 4W5"],
        "columns": ["#", "Description", "Qté", "Prix unitaire", "Montant"],
        "continued": "(suite)",
        "page_label": "Page {n} de {total}",
        "pages": [
            [
                ["1", "Conception graphique - brochure produits automne (heures)", "24", "95,00", "2 280,00"],
                ["2", "Impression de brochures, 2 500 exemplaires", "2 500", "0,84", "2 100,00"],
                ["3", "Gestion de campagne publicitaire numérique - octobre 2026", "1", "3 500,00", "3 500,00"],
                ["4", "Traduction et révision FR/EN - site Web (mots)", "6 000", "0,22", "1 320,00"],
                ["", "À reporter", "", "", "9 200,00"],
            ],
            [
                ["", "Report", "", "", "9 200,00"],
                ["5", "Kiosque - Salon industriel de Montréal, location et montage", "1", "4 750,00", "4 750,00"],
                ["6", "Traiteur - cocktail clients au salon (60 personnes)", "60", "42,50", "2 550,00"],
                ["7", "Atelier de formation - image de marque, équipe des ventes (8 participants)", "8", "275,00",
                 "2 200,00"],
                ["8", "Messagerie - livraison du matériel promotionnel", "1", "68,00", "68,00"],
            ],
        ],
        "after_table": ["Taxes : TPS 5 % et TVQ 9,975 % sur le sous-total (TVQ calculée sur le montant avant TPS)."],
        "totals": [("Sous-total", "18 768,00 $"), ("TPS 5 %", "938,40 $"), ("TVQ 9,975 %", "1 872,11 $"),
                   ("Total à payer", "21 578,51 $")],
        "footer": "Payable dans les 30 jours. Merci de votre confiance!",
    },
    # Saskatchewan: GST + PST 6%, prepaid licence and capital-threshold hardware.
    "prairie_SK_GST_PST_PNS-104882": {
        "supplier": ["Prairie Network Supply Ltd.", "1150 8th Avenue, Regina, SK S4R 1C9",
                     "GST# 609274185 RT0001    SK PST# 4471920"],
        "title": "INVOICE",
        "accent": (0.75, 0.60, 0.05),
        "meta": ["Invoice No.: PNS-104882", "Invoice Date: 2026-06-12", "Order Ref: PO-89904", "Terms: Net 30"],
        "bill_to": ["Ship To: Fabrikam Canada Ltd. - Regina sales office, Attn: IT Operations",
                    "1874 Scarth Street, Suite 700, Regina, SK S4P 4B3"],
        "columns": ["Line", "Description", "Qty", "Price", "Ext. Price"],
        "pages": [
            [
                ["1", "Cisco Meraki MR46 wireless access point", "6", "1,180.00", "7,080.00"],
                ["2", "Cat6 patch cables and mounting hardware (lot)", "1", "425.00", "425.00"],
                ["3", "On-site network installation labour (hours)", "14", "95.00", "1,330.00"],
                ["4", "Meraki Enterprise licence, 1 year, prepaid (per access point)", "6", "210.00", "1,260.00"],
            ]
        ],
        "after_table": ["All items subject to GST 5% and Saskatchewan PST 6%."],
        "totals": [("Subtotal", "$10,095.00"), ("GST 5%", "$504.75"), ("PST 6% (SK)", "$605.70"),
                   ("TOTAL CAD", "$11,205.45")],
        "footer": "Thank you for your business. Overdue accounts are charged 2% interest per month.",
    },
}  # fmt: skip

_X = [50, 75, 380, 430, 510]
_WIDTHS = [300 if x == 75 else 75 for x in _X]


def _table(page: pymupdf.Page, y: float, columns: list[str], rows: list[list[str]], xs: list[int], widths: list[int]):
    for label, x in zip(columns, xs, strict=True):
        page.insert_text((x, y), label, fontsize=9, fontname="hebo")
    page.draw_line((45, y + 4), (580, y + 4))
    y += 18
    for row in rows:
        for x, width, cell in zip(xs, widths, row, strict=True):
            spare = page.insert_textbox(pymupdf.Rect(x, y - 9, x + width, y + 20), cell, fontsize=8, fontname="helv")
            if spare < 0:
                raise ValueError(f"cell {cell!r} does not fit in a {width}pt column")
        y += 24
    page.draw_line((45, y - 10), (580, y - 10))
    return y


def _html_table(columns: list[str], rows: list[list[str]]) -> str:
    out = ["<table>", "<tr>" + "".join(f"<th>{c}</th>" for c in columns) + "</tr>"]
    out += ["<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in rows]
    out.append("</table>")
    return "\n".join(out)


def _lines(page: pymupdf.Page, x: float, y: float, lines: list[str]) -> float:
    for line in lines:
        page.insert_text((x, y), line, fontsize=9, fontname="helv")
        y += 13
    return y


def build(name: str, spec: dict) -> None:
    doc = pymupdf.open()
    md: list[str] = []
    n_pages = len(spec["pages"])
    xs, widths = spec.get("x", _X), spec.get("widths", _WIDTHS)
    label_x, value_x = spec.get("totals_x", (360, 500))
    accent = spec.get("accent")
    for index, rows in enumerate(spec["pages"]):
        page = doc.new_page(width=612, height=792)
        if accent:
            page.draw_rect(pymupdf.Rect(0, 0, 612, 12), color=accent, fill=accent)
        y = 50.0
        if index == 0:
            for i, line in enumerate(spec["supplier"]):
                page.insert_text((50, y), line, fontsize=14 if i == 0 else 9, fontname="hebo" if i == 0 else "helv")
                y += 22 if i == 0 else 13
            y += 10
            title_colour = {"color": accent} if accent else {}
            page.insert_text((50, y), spec["title"], fontsize=18, fontname="hebo", **title_colour)
            y += 24
            if spec.get("layout") == "split":
                y = max(_lines(page, 50, y, spec["bill_to"]), _lines(page, 360, y, spec["meta"]))
            else:
                y = _lines(page, 50, y, spec["meta"] + [""] + spec["bill_to"])
            md += [spec["supplier"][0], *spec["supplier"][1:], "", f"# {spec['title']}", "", *spec["meta"], "",
                   *spec["bill_to"], ""]  # fmt: skip
        else:
            header = f"{spec['supplier'][0]} - {spec['meta'][0]} {spec.get('continued', '(continued)')}"
            page.insert_text((50, y), header, fontsize=9, fontname="helv")
            md += ["<!-- PageBreak -->", f'<!-- PageHeader="{header}" -->', ""]
        y = _table(page, y + 12, spec["columns"], rows, xs, widths) + 6
        md += [_html_table(spec["columns"], rows), ""]
        if index == n_pages - 1:
            for line in spec["after_table"]:
                page.insert_text((50, y), line, fontsize=9, fontname="helv")
                y += 14
                md += [line, ""]
            y += 6
            for label, value in spec["totals"]:
                if label_x + pymupdf.get_text_length(label, fontname="helv", fontsize=10) > value_x - 5:
                    raise ValueError(f"totals label {label!r} overlaps its value; set totals_x")
                page.insert_text((label_x, y), label, fontsize=10, fontname="helv")
                page.insert_text((value_x, y), value, fontsize=10, fontname="hebo")
                y += 16
            md += ["<table>", *[f"<tr><td>{a}</td><td>{b}</td></tr>" for a, b in spec["totals"]], "</table>", ""]
            page.insert_text((50, y + 20), spec["footer"], fontsize=8, fontname="helv")
            md += [spec["footer"], ""]
        footer = spec.get("page_label", "Page {n} of {total}").format(n=index + 1, total=n_pages)
        page.insert_text((270, 760), footer, fontsize=8, fontname="helv")
        md += [f'<!-- PageFooter="{footer}" -->', ""]
    doc.save(SAMPLES / f"{name}.pdf")
    (SAMPLES / f"{name}.md").write_text("\n".join(md), encoding="utf-8")
    print(f"wrote samples/{name}.pdf and .md")


if __name__ == "__main__":
    unknown = [n for n in sys.argv[1:] if n not in INVOICES]
    if unknown:
        sys.exit(f"unknown sample(s): {', '.join(unknown)}; choose from {', '.join(INVOICES)}")
    SAMPLES.mkdir(exist_ok=True)
    for invoice_name in sys.argv[1:] or INVOICES:
        build(invoice_name, INVOICES[invoice_name])
