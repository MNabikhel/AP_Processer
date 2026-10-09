"""The offline path on unusual but real invoices: French labels, freight, a missing subtotal."""

from ap_coder.offline_coder import provinces


def test_a_french_invoice_title_above_the_letterhead_is_not_the_customer_label():
    # "FACTURE" (invoice) is the title of a French invoice, not a "bill to" label: the supplier is the first
    # address and the customer's (after "Vendu à") is the place of supply.
    text = (
        "FACTURE\nFournitures Laval inc.\n100 boul. Saint-Martin, Laval QC H7N 1A1\nVendu à :\nAcme Ltd\n"
        "50 King St, Toronto ON M5V 1A1\n"
    )
    assert provinces(text) == ("QC", "ON")
    assert provinces(text.replace("FACTURE\n", "Fournitures Laval inc.\nFacture no 1234\n", 1)) == ("QC", "ON")


def test_customer_service_or_delivery_date_in_the_letterhead_is_not_the_customer_label():
    text = "Acme Supply\nCustomer service 1-800-555-1212\n1 Main St, Toronto ON M5V 1A1\nBill to:\nHalifax NS B3H 1A1\n"
    assert provinces(text) == ("ON", "NS")
    text = "Date de livraison : 2026-01-02\nFournitures Laval\nLaval QC H7N 1A1\nClient :\nToronto ON M5V 1A1\n"
    assert provinces(text) == ("QC", "ON")


def test_expedie_a_is_the_french_ship_to():
    text = (
        "Fournitures Laval inc.\nLaval QC H7N 1A1\nFacturé à :\nAcme, Toronto ON M5V 1A1\nExpédié à :\n"
        "Acme, Halifax NS B3H 1A1\n"
    )
    assert provinces(text) == ("QC", "NS")
