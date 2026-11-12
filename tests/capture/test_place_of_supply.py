"""The place of supply read off side-by-side address blocks (B-920), and an inferred tax line following a place of
supply changed on the review screen (B-935)."""

from ap_coder.ai.offline_coder import provinces
from ap_coder.capture.types import Box, DocLayout, Line, PageLayout, Word


def _layout(rows):
    """A page from (x0, y0, text) rows, as a PDF's text layer gives them (fractions of the page)."""
    lines = []
    for x0, y0, text in rows:
        box = Box(1, x0, y0, x0 + 0.006 * len(text), y0 + 0.014)
        lines.append(Line([Word(text, box)], box, text))
    page = PageLayout(1, 612.0, 792.0, [w for ln in lines for w in ln.words], lines)
    return DocLayout([page], "text"), "\n".join(ln.text for ln in lines)


# s11-0001 of the benchmark: the vendor in Ontario, the customer and its ship-to in Saskatchewan, the two address
# columns read in turn so the province comes 8 lines after its label.
SIDE_BY_SIDE = [
    (0.34, 0.12, "Great Lakes Mechanical Inc."), (0.45, 0.155, "645 King St W"),
    (0.43, 0.17, "Toronto, ON M5V 2T6"), (0.44, 0.216, "INVOICE"),
    (0.09, 0.246, "Customer:"), (0.51, 0.246, "SHIP TO"),
    (0.09, 0.261, "Kestrel Foods Ltd."), (0.51, 0.261, "Kestrel Foods Ltd."),
    (0.09, 0.276, "Attn: Accounts Payable"), (0.51, 0.276, "Receiving Dock 3"),
    (0.09, 0.291, "5609 Industrial Pkwy"), (0.51, 0.291, "5609 Industrial Pkwy"),
    (0.09, 0.307, "Regina, SK S4P 3Y2"), (0.51, 0.307, "Saskatoon, SK S6H 6L0"),
    (0.10, 0.344, "Invoice Date"), (0.26, 0.344, "Invoice #"),
]  # fmt: skip


def test_side_by_side_blocks_give_each_label_its_own_address():
    layout, text = _layout(SIDE_BY_SIDE)
    assert provinces(text) == ("ON", "")  # the line window alone: lost (before)
    assert provinces(text, layout=layout) == ("ON", "SK")


def test_the_ship_to_column_is_the_place_of_supply_not_the_bill_to():
    rows = [r if r[2] != "Saskatoon, SK S6H 6L0" else (0.51, 0.307, "Winnipeg, MB R3C 4T3") for r in SIDE_BY_SIDE]
    rows = [r if r[2] != "Regina, SK S4P 3Y2" else (0.09, 0.307, "Toronto, ON M4W 1A8") for r in rows]
    layout, text = _layout(rows)
    assert provinces(text, layout=layout)[1] == "MB"


def test_an_address_right_of_its_label():
    layout, text = _layout([
        (0.05, 0.05, "Prairie Supply Co."), (0.05, 0.065, "Calgary, AB T2P 1J9"),
        (0.05, 0.20, "Ship to:"), (0.20, 0.20, "Kestrel Foods, Plant 2"), (0.20, 0.215, "Halifax, NS B3H 1A1"),
        (0.60, 0.20, "Invoice #"), (0.75, 0.20, "A-1"),
    ])  # fmt: skip
    assert provinces(text, layout=layout) == ("AB", "NS")


def test_a_remit_to_block_under_a_customer_label_is_never_ours():
    layout, text = _layout([
        (0.05, 0.05, "Prairie Supply Co."), (0.05, 0.065, "Calgary, AB T2P 1J9"),
        (0.05, 0.20, "Bill to:"), (0.05, 0.215, "Kestrel Foods"),
        (0.05, 0.25, "Remit to:"), (0.05, 0.265, "PO Box 9, Calgary, AB T2P 0A1"),
    ])  # fmt: skip
    assert provinces(text, layout=layout)[1] == ""


def test_service_address_is_a_ship_to():
    text = "Fraser Valley IT\nKelowna, BC V1Y 1A1\nBilled To:\nService Address\nHartwell\nHartwell\n"
    text += "Lethbridge, AB T9M 2Y3\nLethbridge, AB T9M 2Y3\n"
    assert provinces(text) == ("BC", "AB")
    assert provinces("Vendor\nKelowna, BC V1Y 1A1\nService address:\nPlant 2\nRegina, SK S4P 3Y2\n") == ("BC", "SK")


def test_a_province_spelled_out_or_abbreviated_with_dots_is_read():
    from ap_coder.coding.tax import OUTSIDE_CANADA

    for supplier, code in [
        ("77 Marine Dr, Burnaby, British Columbia V5H 1A1", "BC"), ("Toronto, Ontario M5V 2T6", "ON"),
        ("Québec (Québec) G1V 2M2", "QC"), ("Montreal, Quebec", "QC"), ("Vancouver, B.C.", "BC"),
        ("Regina, Sask.", "SK"), ("Montréal, Québec, Canada", "QC"), ("1200 Main St, Buffalo, New York 14202",
        OUTSIDE_CANADA), ("1 Infinite Loop, Cupertino, California 95014", OUTSIDE_CANADA),
    ]:  # fmt: skip
        text = f"Coastal Supply Ltd.\n{supplier}\nShip to:\nMadeup Constructors\nVancouver, British Columbia V6B 1A1\n"
        assert provinces(text) == (code, "BC"), supplier
    # A bare code still needs its postal code, and a province named in a sentence is no address.
    assert provinces("Coastal Supply Ltd.\nVancouver, BC\nProudly serving British Columbia\n") == ("", "")


def test_a_word_or_a_tagline_or_a_line_item_naming_a_province_is_no_address():
    """B-2493 read a province named with no postal code too widely: "Labour (man)" as Manitoba, a tagline over the
    letterhead ("Proudly serving Ottawa, Gatineau, Quebec") over the address with its postal code, a line item ("Hotel
    stay - Montreal, Quebec") as the supplier's."""
    from ap_coder.ai.offline_coder import _province_of, addresses

    for text in ["Labour (man)", "Smith, Ont", "Delivery to site, Que", "Paper, Man", "Thanks, Labrador"]:
        assert _province_of(text) == "", text
    for text, code in [
        ("Hours, Man.", "MB"),
        ("Toronto, Ont.", "ON"),
        ("Regina, Sask.", "SK"),
        ("Montreal, Quebec", "QC"),
    ]:
        assert _province_of(text) == code, text
    ship = "Ship to:\nFoo\nOttawa, ON K1A 0B1\n"
    assert (
        addresses(
            f"Acme Supply Ltd.\nProudly serving Ottawa, Gatineau, Quebec\n123 Bank St, Ottawa, ON K1P 1J9\n{ship}"
        )[0]
        == "ON"
    )
    assert addresses(f"Okanagan Fruit Ltd.\nPremium fruit, B.C.\n123 Main St, Calgary, AB T2P 1J9\n{ship}")[0] == "AB"
    assert addresses("Acme Supply Ltd.\n123 Bank St\nOttawa ON\nDescription\nHotel stay - Montreal, Quebec\n")[0] == ""
    # A letterhead naming its province, no postal code, is still read.
    assert addresses("Coastal Supply Ltd.\n77 Marine Dr, Burnaby, British Columbia\nINVOICE\n")[0] == "BC"


def test_a_line_item_naming_a_province_under_a_ship_to_label_is_not_the_ship_to():
    """B-2493: a province only named on a line item ("Hotel stay - Montreal, Quebec") under a "Ship to" label whose
    own address gave none was taken as the ship-to, so the place of supply; a ship-to only named still is."""
    from ap_coder.ai.offline_coder import addresses

    letterhead = "Acme Supply Ltd.\n123 Bank St, Ottawa, ON K1P 1J9\n"
    text = f"{letterhead}Ship to:\nAcme site\nDescription\nHotel stay - Montreal, Quebec\n"
    assert addresses(text)[1] == ""
    assert (
        addresses("Acme Supply Ltd.\n123 Bank St, Ottawa, ON K1P 1J9\nShip to:\nSite 4\nMontreal, Quebec\n")[1] == "QC"
    )


def test_an_inferred_tax_line_follows_the_place_of_supply_changed():
    from ap_coder.web.invoice_form import Draft

    base = Draft(
        header={"ship_to_province": "", "invoice_date": "2026-05-01"},
        lines=[],
        taxes=[{"tax_type": "GST", "province": "", "rate_pct": 5.0, "taxable_amount": 100.0, "tax_amount": 5.0},
               {"tax_type": "PST", "province": "ON", "rate_pct": 7.0, "taxable_amount": 100.0, "tax_amount": 7.0},
               {"tax_type": "HST", "province": "ON", "rate_pct": 13.0, "taxable_amount": 100.0, "tax_amount": 13.0}],
    )  # fmt: skip

    class Form(dict):
        def getlist(self, name):
            return []

    changed = Draft.from_form(Form(ship_to_province="MB", invoice_date="2026-05-01"), base)
    assert [t["province"] for t in changed.taxes] == ["", "MB", "ON"]  # RST 7% in Manitoba; HST is not levied there
    same = Draft.from_form(Form(ship_to_province="", invoice_date="2026-05-01"), base)
    assert [t["province"] for t in same.taxes] == ["", "ON", "ON"]  # nothing changed: left as it is


def test_a_ship_to_printed_after_the_items_is_still_read():
    """B-2493: once the line items' heading was read, a ship-to printed under them and only naming its province
    ("Ship to: Site 4, Montreal, Quebec" after the table) was no longer read, and the place of supply fell back to
    the supplier's province: each label starts its own block again."""
    from ap_coder.ai.offline_coder import addresses

    text = (
        "Acme Supply Ltd.\n123 Bank St, Ottawa, ON K1P 1J9\nDescription Qty Amount\nWidgets 2 10.00\nTotal 10.00\n"
        "Ship to:\nSite 4\nMontreal, Quebec\n"
    )
    assert addresses(text)[1] == "QC"
