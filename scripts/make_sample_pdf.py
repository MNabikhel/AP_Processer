"""Generate the synthetic Canadian sample invoices (PDF + Document-Intelligence-style Markdown).

Usage: python scripts/make_sample_pdf.py
Requires PyMuPDF (pip install pymupdf). Writes samples/<name>.pdf and samples/<name>.md;
the matching answers live in samples/ground_truth/<name>.json.
"""

from __future__ import annotations

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
}  # fmt: skip

_X = [50, 75, 380, 430, 510]


def _table(page: pymupdf.Page, y: float, columns: list[str], rows: list[list[str]]) -> float:
    for label, x in zip(columns, _X, strict=True):
        page.insert_text((x, y), label, fontsize=9, fontname="hebo")
    page.draw_line((45, y + 4), (580, y + 4))
    y += 18
    for row in rows:
        for x, cell in zip(_X, row, strict=True):
            width = 300 if x == 75 else 75
            page.insert_textbox(pymupdf.Rect(x, y - 9, x + width, y + 20), cell, fontsize=8, fontname="helv")
        y += 24
    page.draw_line((45, y - 10), (580, y - 10))
    return y


def _html_table(columns: list[str], rows: list[list[str]]) -> str:
    out = ["<table>", "<tr>" + "".join(f"<th>{c}</th>" for c in columns) + "</tr>"]
    out += ["<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in rows]
    out.append("</table>")
    return "\n".join(out)


def build(name: str, spec: dict) -> None:
    doc = pymupdf.open()
    md: list[str] = []
    n_pages = len(spec["pages"])
    for index, rows in enumerate(spec["pages"]):
        page = doc.new_page(width=612, height=792)
        y = 50.0
        if index == 0:
            for i, line in enumerate(spec["supplier"]):
                page.insert_text((50, y), line, fontsize=14 if i == 0 else 9, fontname="hebo" if i == 0 else "helv")
                y += 22 if i == 0 else 13
            y += 10
            page.insert_text((50, y), spec["title"], fontsize=18, fontname="hebo")
            y += 24
            for line in spec["meta"] + [""] + spec["bill_to"]:
                page.insert_text((50, y), line, fontsize=9, fontname="helv")
                y += 13
            md += [spec["supplier"][0], *spec["supplier"][1:], "", f"# {spec['title']}", "", *spec["meta"], "",
                   *spec["bill_to"], ""]  # fmt: skip
        else:
            header = f"{spec['supplier'][0]} - {spec['meta'][0]} (continued)"
            page.insert_text((50, y), header, fontsize=9, fontname="helv")
            md += ["<!-- PageBreak -->", f'<!-- PageHeader="{header}" -->', ""]
        y = _table(page, y + 12, spec["columns"], rows) + 6
        md += [_html_table(spec["columns"], rows), ""]
        if index == n_pages - 1:
            for line in spec["after_table"]:
                page.insert_text((50, y), line, fontsize=9, fontname="helv")
                y += 14
                md += [line, ""]
            y += 6
            for label, value in spec["totals"]:
                page.insert_text((360, y), label, fontsize=10, fontname="helv")
                page.insert_text((500, y), value, fontsize=10, fontname="hebo")
                y += 16
            md += ["<table>", *[f"<tr><td>{a}</td><td>{b}</td></tr>" for a, b in spec["totals"]], "</table>", ""]
            page.insert_text((50, y + 20), spec["footer"], fontsize=8, fontname="helv")
            md += [spec["footer"], ""]
        footer = f"Page {index + 1} of {n_pages}"
        page.insert_text((270, 760), footer, fontsize=8, fontname="helv")
        md += [f'<!-- PageFooter="{footer}" -->', ""]
    doc.save(SAMPLES / f"{name}.pdf")
    (SAMPLES / f"{name}.md").write_text("\n".join(md), encoding="utf-8")
    print(f"wrote samples/{name}.pdf and .md")


if __name__ == "__main__":
    SAMPLES.mkdir(exist_ok=True)
    for invoice_name, invoice_spec in INVOICES.items():
        build(invoice_name, invoice_spec)
