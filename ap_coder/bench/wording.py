"""Label wordings, English and French, as suppliers actually print them."""

from __future__ import annotations

import random

LABELS: dict[str, dict[str, list[str]]] = {
    "invoice_number": {
        "en": [
            "Invoice No.",
            "Invoice #",
            "Invoice Number",
            "Inv. No.",
            "Document No.",
            "Bill No.",
            "Invoice No",
            "INVOICE #",
            "Inv #",
            "Invoice",
        ],
        "fr": ["N° de facture", "No de facture", "Facture n°", "Numéro de facture", "No facture", "N° facture"],
    },
    "credit_number": {
        "en": ["Credit Note No.", "Credit Memo #", "Credit Note #", "Document No.", "Credit No."],
        "fr": ["N° de note de crédit", "Note de crédit n°", "No de note de crédit"],
    },
    "bill_number": {
        "en": ["Bill Number", "Statement No.", "Invoice Number", "Bill No."],
        "fr": ["N° de facture", "Numéro de relevé"],
    },
    "invoice_date": {
        "en": ["Date", "Invoice Date", "Date of issue", "Billing Date", "Inv. Date", "Issued", "DATE"],
        "fr": ["Date", "Date de facture", "Date de facturation", "Date d'émission"],
    },
    "statement_date": {
        "en": ["Statement Date", "Bill Date", "Billing Date"],
        "fr": ["Date du relevé", "Date de facturation"],
    },
    "credit_date": {"en": ["Credit Date", "Date", "Date of issue"], "fr": ["Date", "Date d'émission"]},
    "due_date": {
        "en": ["Due Date", "Payment Due", "Due", "Pay By", "Date Due", "Please pay by"],
        "fr": ["Date d'échéance", "Échéance", "Payable avant le", "Date limite de paiement"],
    },
    "po_number": {
        "en": [
            "PO",
            "P.O. #",
            "PO Number",
            "Purchase Order",
            "Your Order",
            "Customer PO",
            "PO #",
            "Cust. P.O.",
            "Your PO No.",
            "Purchase Order No.",
        ],
        "fr": ["Bon de commande", "N° de commande", "Votre commande", "No bon de commande", "B.C."],
    },
    "payment_terms": {
        "en": ["Terms", "Payment Terms", "Terms of Payment"],
        "fr": ["Conditions", "Modalités de paiement", "Conditions de paiement"],
    },
    "currency": {"en": ["Currency", "Currency Code"], "fr": ["Devise", "Monnaie"]},
    "subtotal": {
        "en": ["Subtotal", "Sub-total", "Net amount", "Sub Total", "SUBTOTAL", "Total before tax"],
        "fr": ["Sous-total", "Montant net", "Total avant taxes"],
    },
    "grand_total": {
        "en": [
            "Total",
            "Invoice Total",
            "Amount Due",
            "Total Due",
            "Balance Due",
            "TOTAL",
            "Total (incl. tax)",
            "Grand Total",
        ],
        "fr": ["Total", "Montant total", "Total à payer", "Montant dû", "TOTAL"],
    },
    "credit_total": {
        "en": ["Total Credit", "Credit Total", "Total", "Amount Credited"],
        "fr": ["Total du crédit", "Montant crédité", "Total"],
    },
    "amount_due": {
        "en": ["Amount Due", "Total Amount Due", "Please Pay", "Balance Due"],
        "fr": ["Montant dû", "Total à payer", "Veuillez payer"],
    },
    "tax_total": {
        "en": ["Total Tax", "Tax Total", "Total Taxes", "Sales Tax Total"],
        "fr": ["Total des taxes", "Taxes"],
    },
    "GST": {"en": ["GST", "GST/HST", "G.S.T."], "fr": ["TPS", "T.P.S."]},
    "HST": {"en": ["HST", "GST/HST", "H.S.T."], "fr": ["TVH", "TPS/TVH"]},
    "PST": {"en": ["PST", "BC PST", "P.S.T."], "fr": ["TVP"]},
    "RST": {"en": ["RST", "PST", "MB RST"], "fr": ["TVD"]},
    "QST": {"en": ["QST", "Q.S.T."], "fr": ["TVQ", "T.V.Q."]},
    "SALES": {"en": ["Sales Tax", "Tax", "State Sales Tax"], "fr": ["Taxe de vente"]},
    "bn": {
        "en": [
            "GST/HST Reg. No.",
            "Business Number",
            "BN",
            "GST #",
            "GST/HST #",
            "HST Reg #",
            "GST Registration No.",
            "GST/HST Registration",
            "HST No.",
        ],
        "fr": ["TPS/TVH n°", "N° TPS", "N° TPS/TVH", "No TPS", "Numéro d'entreprise", "TPS"],
    },
    "qst": {"en": ["QST No.", "QST Reg. No.", "QST #"], "fr": ["QST/TVQ n°", "N° TVQ", "TVQ #", "No TVQ", "TVQ"]},
    # Distractors
    "customer_account": {
        "en": ["Account No.", "Customer No.", "Cust. ID", "Account #", "Customer Account", "Client ID"],
        "fr": ["Client n°", "N° de client", "Numéro de compte", "No client"],
    },
    "order_date": {"en": ["Order Date", "Ordered", "PO Date"], "fr": ["Date de commande"]},
    "ship_date": {
        "en": ["Ship Date", "Shipped", "Delivery Date", "Date Shipped"],
        "fr": ["Date d'expédition", "Date de livraison"],
    },
    "quote_number": {"en": ["Quote #", "Quotation No.", "Estimate #"], "fr": ["Soumission n°", "N° de soumission"]},
    "sales_order": {
        "en": ["Sales Order", "Our Order No.", "Our Ref.", "Order No.", "Packing Slip #"],
        "fr": ["Notre commande", "Notre référence", "Bon de livraison"],
    },
    "sales_rep": {"en": ["Sales Rep", "Salesperson", "Rep"], "fr": ["Représentant", "Vendeur"]},
    "ship_via": {"en": ["Ship Via", "Carrier", "FOB"], "fr": ["Transporteur", "Expédié par"]},
    "page": {"en": ["Page"], "fr": ["Page"]},
    "original_invoice": {
        "en": ["Original Invoice", "Applies to Invoice", "Ref. Invoice #", "Against Invoice"],
        "fr": ["Facture d'origine", "Réf. facture", "Facture originale n°"],
    },
    "reason": {"en": ["Reason", "Credit reason"], "fr": ["Motif", "Raison"]},
    "customer_gst": {"en": ["Customer GST #", "Your GST No.", "Client BN"], "fr": ["TPS du client", "Votre n° TPS"]},
    "tax_id_us": {"en": ["Tax ID", "Federal EIN", "EIN"], "fr": ["Tax ID"]},
    "freight": {
        "en": ["Freight", "Shipping & Handling", "Delivery", "Shipping"],
        "fr": ["Transport", "Frais de livraison", "Livraison"],
    },
    "discount": {"en": ["Discount", "Volume discount", "Promotional discount"], "fr": ["Escompte", "Rabais"]},
    "previous_balance": {
        "en": ["Previous Balance", "Balance from last bill", "Previous bill amount"],
        "fr": ["Solde précédent"],
    },
    "payment_received": {
        "en": ["Payment Received - Thank you", "Payments received", "Payment(s) - thank you"],
        "fr": ["Paiement reçu - merci"],
    },
    "balance_forward": {"en": ["Balance Forward", "Outstanding balance"], "fr": ["Solde reporté"]},
    "current_charges": {
        "en": ["Total current charges", "Current charges", "New charges"],
        "fr": ["Total des frais courants", "Nouveaux frais"],
    },
    "billing_period": {
        "en": ["Billing Period", "Service Period", "Period"],
        "fr": ["Période de facturation", "Période"],
    },
    "amount_paid": {"en": ["Amount Paid", "Payments/Credits", "Paid"], "fr": ["Montant payé", "Paiements"]},
    "bill_to": {
        "en": ["Bill To", "Sold To", "Invoice To", "Customer", "BILL TO", "Billed To"],
        "fr": ["Facturer à", "Vendu à", "Client"],
    },
    "ship_to": {
        "en": ["Ship To", "Deliver To", "SHIP TO", "Service Address"],
        "fr": ["Expédier à", "Livrer à", "Adresse de livraison"],
    },
    "description": {
        "en": ["Description", "Item Description", "Details", "Product / Service"],
        "fr": ["Description", "Désignation", "Article"],
    },
    "sku": {"en": ["Item", "SKU", "Part #", "Code", "Item No."], "fr": ["Code", "Article", "N° produit"]},
    "qty": {"en": ["Qty", "Quantity", "QTY", "Qty Shipped", "Hrs/Qty"], "fr": ["Qté", "Quantité"]},
    "unit": {"en": ["Unit", "U/M", "UOM"], "fr": ["Unité", "U.M."]},
    "unit_price": {
        "en": ["Unit Price", "Price", "Rate", "Unit Cost", "Price Each"],
        "fr": ["Prix unitaire", "Prix unit.", "Taux"],
    },
    "amount": {
        "en": ["Amount", "Total", "Ext. Price", "Line Total", "Extended"],
        "fr": ["Montant", "Total", "Prix total"],
    },
    "phone": {"en": ["Tel", "Phone", "T", "Ph"], "fr": ["Tél.", "Téléphone", "T"]},
    "fax": {"en": ["Fax", "F"], "fr": ["Téléc.", "Fax"]},
}

TITLES = {
    "invoice": {
        "en": ["INVOICE", "Invoice", "TAX INVOICE", "SALES INVOICE"],
        "fr": ["FACTURE", "Facture"],
        "bi": ["INVOICE / FACTURE", "FACTURE / INVOICE", "Invoice - Facture"],
    },
    "credit": {
        "en": ["CREDIT NOTE", "CREDIT MEMO", "Credit Note"],
        "fr": ["NOTE DE CRÉDIT", "Note de crédit"],
        "bi": ["CREDIT NOTE / NOTE DE CRÉDIT"],
    },
    "utility": {
        "en": ["Your Bill", "STATEMENT", "Billing Statement", "Your Electricity Bill"],
        "fr": ["Votre facture", "RELEVÉ"],
        "bi": ["Your Bill / Votre facture"],
    },
}

PHRASES = {
    "thanks": {
        "en": ["Thank you for your business!", "Thank you!", "We appreciate your business."],
        "fr": ["Merci de votre confiance!", "Merci!", "Nous vous remercions de votre confiance."],
    },
    "continued": {"en": ["Continued on next page", "Continued..."], "fr": ["Suite à la page suivante", "Suite..."]},
    "remit": {
        "en": [
            "REMITTANCE ADVICE - Please detach and return this portion with your payment",
            "Please return this stub with your payment",
            "PAYMENT STUB - detach and mail with cheque",
        ],
        "fr": [
            "TALON DE REMISE - Veuillez détacher et retourner avec votre paiement",
            "Veuillez retourner cette partie avec votre paiement",
        ],
    },
    "payable": {
        "en": ["Make cheques payable to", "Remit to", "Please make payment to"],
        "fr": ["Libeller le chèque à l'ordre de", "Payer à"],
    },
    "interest": {
        "en": [
            "Overdue accounts are subject to interest of 2% per month (26.82% per annum).",
            "A 1.5% monthly service charge (19.56% per annum) applies to overdue balances.",
        ],
        "fr": ["Intérêts de 2 % par mois (26,82 % par année) sur les comptes en souffrance."],
    },
    "enclosed": {"en": ["Amount Enclosed"], "fr": ["Montant joint"]},
    "amounts_in": {
        "en": ["All amounts in", "Amounts shown in", "Prices in"],
        "fr": ["Montants en", "Tous les montants sont en"],
    },
}


def label(rng: random.Random, key: str, lang: str) -> str:
    """One wording for `key`; bilingual prints "English / French"."""
    opts = LABELS[key]
    if lang == "bi":
        en, fr = rng.choice(opts["en"]), rng.choice(opts["fr"])
        return en if en == fr else f"{en} / {fr}"
    return rng.choice(opts["fr" if lang == "fr" else "en"])


def phrase(rng: random.Random, key: str, lang: str) -> str:
    opts = PHRASES[key]
    return rng.choice(opts["fr" if lang == "fr" else "en"])


def title(rng: random.Random, kind: str, lang: str) -> str:
    return rng.choice(TITLES[kind][lang])


def with_sep(text: str, sep: str) -> str:
    """Label separators: ":" ("Invoice No.:"), French spacing (" :"), "#" ("Invoice #"), or none."""
    if sep == ":":
        return text + ":"
    if sep == " :":
        return text + " :"
    if sep == "#":
        low = text.lower()
        if "#" in text or "no" in low.split() or "n°" in low or "no." in low or "number" in low or "numéro" in low:
            return text
        return text + " #"
    return text
