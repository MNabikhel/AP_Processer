"""Draft an email asking the vendor for what an invoice is missing, from its open checks.

Only checks a vendor can fix are turned into requests (a missing GST/HST number, amounts that don't add
up, a price above the PO...). Internal controls are never mentioned: fraud and duplicate signals that
would tip off a fraudster (bank account or GST/HST number changed, vendor on hold, unusual amount, not in
the vendor master), GL coding, and how the document was read. A changed bank account is confirmed by
phone, never by replying to an email.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

ENGLISH, FRENCH = "en", "fr"
LANGUAGES = {ENGLISH: "English", FRENCH: "Français"}

_TOTALS = {"SUBTOTAL_MISMATCH", "TOTAL_MISMATCH", "LINE_MATH", "TAX_CALC_MISMATCH", "TAX_LINES_TOTAL_MISMATCH",
           "TAX_BASE_MISMATCH"}  # fmt: skip
_TAX_WRONG = {"TAX_RATE_NONSTANDARD", "TAX_REGIME_MISMATCH", "TAX_TYPE_NOT_LEVIED", "TAX_PROVINCE_DIFFERS",
              "QST_ON_GST_INCLUSIVE", "PROVINCIAL_TAX_NOT_CHARGED"}  # fmt: skip
_PO_LINE = {"PO_LINE_NOT_ON_PO", "PO_PRICE_OVER", "PO_QTY_OVER", "PO_NOT_RECEIVED"}
ASKABLE = (
    _TOTALS | _TAX_WRONG | _PO_LINE
    | {"NO_TAX_CHARGED", "GST_HST_NUMBER_MISSING", "GST_HST_NUMBER_FORMAT", "GST_NUMBER_CHECK_DIGIT",
       "QST_NUMBER_MISSING", "QST_NUMBER_FORMAT",
       "PO_UNKNOWN", "PO_CLOSED", "PO_VENDOR_MISMATCH", "PO_OVER_BILLED", "PO_NOT_QUOTED", "MISSING_INVOICE_NUMBER",
       "DUE_BEFORE_INVOICE", "DUPLICATE_INVOICE", "DUPLICATE_IN_ERP", "CREDIT_NOTE_ORIGINAL_UNKNOWN",
       "CREDIT_EXCEEDS_INVOICE"}
)  # fmt: skip

_TEXT = {
    ENGLISH: {
        "subject": "Invoice {number}: information needed before payment",
        "subject_credit": "Credit note {number}: information needed",
        "hello": "Hello,",
        "intro": "We are processing your invoice {number} dated {date} for {total} {currency}{po}. Before we can "
        "pay it, could you please help us with the following:",
        "intro_credit": "We are processing your credit note {number} dated {date} for {total} {currency}{po}. "
        "Before we can apply it, could you please help us with the following:",
        "po": ", purchase order {po}",
        "thanks": "Thank you,",
        "totals": "The amounts on the invoice do not add up{lines}. Please check them and send a corrected invoice.",
        "lines": " (line {lines})",
        "tax_wrong": "The sales tax charged ({taxes}) does not look right for a supply {province}. Please "
        "confirm the taxes, or send a corrected invoice.",
        "no_tax": "No sales tax is charged. Please confirm that the supply is exempt or zero-rated, or send a "
        "corrected invoice with the tax.",
        "gst_number": "The invoice does not show a valid GST/HST registration number. We need it to claim the "
        "tax: please send a corrected invoice that shows it.",
        "qst_number": "The invoice does not show a valid QST registration number. We need it to claim the tax: "
        "please send a corrected invoice that shows it.",
        "po_unknown": "We cannot find purchase order {po}. Please confirm the purchase order number.",
        "po_closed": "Purchase order {po} is closed on our side. Please confirm which order this invoice is for.",
        "po_vendor": "Purchase order {po} was not issued to your company. Please confirm the purchase order number.",
        "po_over": "This invoice takes the total billed on purchase order {po} above the amount ordered. Please "
        "send a list of the invoices billed on this order.",
        "po_missing": "The invoice does not show our purchase order number. Please send it to us.",
        "line_not_on_po": "Line {line} ({item}) is not on purchase order {po}. Please confirm who ordered it.",
        "price_over": "Line {line} ({item}) is billed at {price} each, above the price on purchase order {po}. "
        "Please send a credit note or a corrected invoice, or tell us who agreed to the new price.",
        "qty_over": "Line {line} ({item}) bills more than was ordered on purchase order {po}. Please send a "
        "credit note or a corrected invoice.",
        "not_received": "We have no record yet of receiving line {line} ({item}). Please send the proof of "
        "delivery (packing slip or signed delivery note).",
        "no_number": "The invoice has no invoice number. Please send a copy that shows one.",
        "due_before": "The due date ({due}) is before the invoice date ({date}). Please confirm the payment terms.",
        "duplicate": "We may already have received this invoice. If it is a copy, no action is needed; if it is a "
        "new bill, please send it with its own invoice number.",
        "credit_unknown": "This credit note refers to invoice {original}, which we cannot find. Please send us a copy "
        "of that invoice, or confirm which invoice the credit applies to.",
        "credit_exceeds": "This credit note is for more than invoice {original}. Please confirm the amount, or which "
        "invoices it applies to.",
    },
    FRENCH: {
        "subject": "Facture {number} : renseignements requis avant le paiement",
        "subject_credit": "Note de crédit {number} : renseignements requis",
        "hello": "Bonjour,",
        "intro": "Nous traitons votre facture {number} du {date} au montant de {total} {currency}{po}. Avant de "
        "pouvoir la payer, pourriez-vous nous aider avec les points suivants :",
        "intro_credit": "Nous traitons votre note de crédit {number} du {date} au montant de {total} {currency}{po}. "
        "Avant de pouvoir l'appliquer, pourriez-vous nous aider avec les points suivants :",
        "po": ", bon de commande {po}",
        "thanks": "Merci,",
        "totals": "Les montants de la facture ne concordent pas{lines}. Veuillez les vérifier et nous envoyer une "
        "facture corrigée.",
        "lines": " (ligne {lines})",
        "tax_wrong": "Les taxes facturées ({taxes}) ne semblent pas correspondre à une fourniture effectuée "
        "{province}. Veuillez confirmer les taxes ou nous envoyer une facture corrigée.",
        "no_tax": "Aucune taxe n'est facturée. Veuillez confirmer que la fourniture est exonérée ou détaxée, ou "
        "nous envoyer une facture corrigée avec les taxes.",
        "gst_number": "La facture n'indique pas de numéro d'inscription à la TPS/TVH valide. Nous en avons besoin "
        "pour réclamer la taxe : veuillez nous envoyer une facture corrigée qui l'indique.",
        "qst_number": "La facture n'indique pas de numéro d'inscription à la TVQ valide. Nous en avons besoin pour "
        "réclamer la taxe : veuillez nous envoyer une facture corrigée qui l'indique.",
        "po_unknown": "Nous ne trouvons pas le bon de commande {po}. Veuillez confirmer le numéro du bon de commande.",
        "po_closed": "Le bon de commande {po} est fermé chez nous. Veuillez confirmer à quelle commande cette "
        "facture se rapporte.",
        "po_vendor": "Le bon de commande {po} n'a pas été émis à votre entreprise. Veuillez confirmer le numéro "
        "du bon de commande.",
        "po_over": "Avec cette facture, le total facturé sur le bon de commande {po} dépasse le montant commandé. "
        "Veuillez nous envoyer la liste des factures émises sur cette commande.",
        "po_missing": "La facture n'indique pas notre numéro de bon de commande. Veuillez nous le transmettre.",
        "line_not_on_po": "La ligne {line} ({item}) ne figure pas sur le bon de commande {po}. Veuillez confirmer "
        "qui l'a commandée.",
        "price_over": "La ligne {line} ({item}) est facturée {price} l'unité, au-dessus du prix du bon de commande "
        "{po}. Veuillez nous envoyer une note de crédit ou une facture corrigée, ou nous indiquer qui a accepté "
        "le nouveau prix.",
        "qty_over": "La ligne {line} ({item}) facture plus que la quantité commandée sur le bon de commande {po}. "
        "Veuillez nous envoyer une note de crédit ou une facture corrigée.",
        "not_received": "Nous n'avons pas encore de trace de la réception de la ligne {line} ({item}). Veuillez "
        "nous envoyer la preuve de livraison (bordereau d'expédition ou bon de livraison signé).",
        "no_number": "La facture n'a pas de numéro. Veuillez nous en envoyer une copie numérotée.",
        "due_before": "La date d'échéance ({due}) précède la date de la facture ({date}). Veuillez confirmer les "
        "conditions de paiement.",
        "duplicate": "Il se peut que nous ayons déjà reçu cette facture. S'il s'agit d'une copie, aucune action "
        "n'est requise; s'il s'agit d'une nouvelle facture, veuillez nous l'envoyer avec son propre numéro.",
        "credit_unknown": "Cette note de crédit renvoie à la facture {original}, que nous ne trouvons pas. Veuillez "
        "nous envoyer une copie de cette facture, ou nous confirmer à quelle facture le crédit s'applique.",
        "credit_exceeds": "Cette note de crédit dépasse le montant de la facture {original}. Veuillez confirmer le "
        "montant, ou les factures auxquelles elle s'applique.",
    },
}


# Where a supply is made, as a sentence ends: "for a supply in Ontario", "une fourniture effectuée au Québec".
_WHERE = {
    ENGLISH: {
        "AB": "in Alberta", "BC": "in British Columbia", "MB": "in Manitoba", "NB": "in New Brunswick",
        "NL": "in Newfoundland and Labrador", "NS": "in Nova Scotia", "NT": "in the Northwest Territories",
        "NU": "in Nunavut", "ON": "in Ontario", "PE": "in Prince Edward Island", "QC": "in Quebec",
        "SK": "in Saskatchewan", "YT": "in Yukon", "OUTSIDE_CANADA": "outside Canada",
    },
    FRENCH: {
        "AB": "en Alberta", "BC": "en Colombie-Britannique", "MB": "au Manitoba", "NB": "au Nouveau-Brunswick",
        "NL": "à Terre-Neuve-et-Labrador", "NS": "en Nouvelle-Écosse", "NT": "aux Territoires du Nord-Ouest",
        "NU": "au Nunavut", "ON": "en Ontario", "PE": "à l'Île-du-Prince-Édouard", "QC": "au Québec",
        "SK": "en Saskatchewan", "YT": "au Yukon", "OUTSIDE_CANADA": "hors du Canada",
    },
}  # fmt: skip
_TAX_NAMES = {FRENCH: {"GST": "TPS", "HST": "TVH", "PST": "TVP", "QST": "TVQ", "OTHER": "autre taxe"}}


def _amount(value: float | None, language: str) -> str:
    """1,234.50 in English, 1 234,50 in French."""
    text = f"{float(value or 0):,.2f}"
    return text.replace(",", " ").replace(".", ",") if language == FRENCH else text


def _rate(rate: float, language: str) -> str:
    text = f"{rate * 100:g}"
    return f"{text.replace('.', ',')} %" if language == FRENCH else f"{text}%"


@dataclass
class Draft:
    subject: str
    body: str
    points: list[str]

    def mailto(self, to: str = "") -> str:
        return f"mailto:{quote(to)}?subject={quote(self.subject)}&body={quote(self.body)}"


def _code(issue: Any) -> str:
    return issue["code"] if isinstance(issue, dict) else issue.code


def _line(issue: Any) -> int | None:
    return issue.get("line_number") if isinstance(issue, dict) else issue.line_number


def askable(issues: Iterable[Any]) -> list[Any]:
    """The open checks a vendor can fix (internal controls left out)."""
    return [i for i in issues if _code(i) in ASKABLE]


def suggested_language(coding: dict[str, Any]) -> str:
    """French for a Quebec vendor, else English."""
    return FRENCH if (coding.get("supplier_province") or "") == "QC" else ENGLISH


def _short(text: Any, limit: int = 60) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def points(coding: dict[str, Any], issues: Iterable[Any], language: str = ENGLISH) -> list[str]:
    """One request per problem, in the order a vendor would read the invoice."""
    t = _TEXT[language]
    issues = askable(issues)
    codes = {_code(i) for i in issues}
    po = (coding.get("po_number") or "").strip()  # as printed: what the vendor knows it as
    lines_by_number = {li.get("line_number"): li for li in coding.get("line_items") or []}
    out = []
    if codes & {"DUPLICATE_INVOICE", "DUPLICATE_IN_ERP"}:
        out.append(t["duplicate"])
    if "MISSING_INVOICE_NUMBER" in codes:
        out.append(t["no_number"])
    if codes & _TOTALS:
        numbers = sorted({n for i in issues if _code(i) in _TOTALS and (n := _line(i))})
        lines = t["lines"].format(lines=", ".join(str(n) for n in numbers)) if numbers else ""
        out.append(t["totals"].format(lines=lines))
    if codes & {"GST_HST_NUMBER_MISSING", "GST_HST_NUMBER_FORMAT", "GST_NUMBER_CHECK_DIGIT"}:
        out.append(t["gst_number"])
    if codes & {"QST_NUMBER_MISSING", "QST_NUMBER_FORMAT"}:
        out.append(t["qst_number"])
    if codes & _TAX_WRONG:
        names = _TAX_NAMES.get(language, {})
        taxes = ", ".join(
            f"{names.get(str(tl.get('tax_type')), tl.get('tax_type'))} {_rate(float(tl.get('rate') or 0), language)}"
            for tl in coding.get("tax_lines") or []
        )
        province = coding.get("ship_to_province") or coding.get("supplier_province") or ""
        where = _WHERE[language].get(province, "")
        out.append(t["tax_wrong"].format(taxes=taxes or "—", province=where).replace(" .", ".").replace("  ", " "))
    if "NO_TAX_CHARGED" in codes:
        out.append(t["no_tax"])
    simple = {"PO_UNKNOWN": "po_unknown", "PO_CLOSED": "po_closed", "PO_VENDOR_MISMATCH": "po_vendor",
              "PO_OVER_BILLED": "po_over"}  # fmt: skip
    for code, key in simple.items():
        if code in codes and po:
            out.append(t[key].format(po=po))
    if "PO_NOT_QUOTED" in codes:
        out.append(t["po_missing"])
    keys = {"PO_LINE_NOT_ON_PO": "line_not_on_po", "PO_PRICE_OVER": "price_over", "PO_QTY_OVER": "qty_over",
            "PO_NOT_RECEIVED": "not_received"}  # fmt: skip
    seen = set()
    for i in issues:
        code, n = _code(i), _line(i)
        if code not in keys or not po or (code, n) in seen:
            continue
        seen.add((code, n))
        li = lines_by_number.get(n) or {}
        price = _amount(li.get("unit_price"), language)
        out.append(t[keys[code]].format(line=n or "?", item=_short(li.get("description")), price=price, po=po))
    original = (coding.get("original_invoice_number") or "").strip() or "?"
    if "CREDIT_NOTE_ORIGINAL_UNKNOWN" in codes:
        out.append(t["credit_unknown"].format(original=original))
    if "CREDIT_EXCEEDS_INVOICE" in codes:
        out.append(t["credit_exceeds"].format(original=original))
    if "DUE_BEFORE_INVOICE" in codes:
        out.append(t["due_before"].format(due=coding.get("due_date") or "", date=coding.get("invoice_date") or ""))
    return out


def draft(coding: dict[str, Any], issues: Iterable[Any], language: str = ENGLISH, signature: str = "") -> Draft:
    t = _TEXT[language]
    asks = points(coding, issues, language)
    number = coding.get("invoice_number") or "?"
    po = (coding.get("po_number") or "").strip()
    grand_total = float(coding.get("grand_total") or 0)
    total = _amount(abs(grand_total), language)
    intro = t["intro_credit" if grand_total < 0 else "intro"].format(
        number=number, date=coding.get("invoice_date") or "?", total=total, currency=coding.get("currency") or "",
        po=t["po"].format(po=po) if po else "",
    )  # fmt: skip
    numbered = "\n".join(f"{n}. {text}" for n, text in enumerate(asks, 1))
    body = f"{t['hello']}\n\n{intro}\n\n{numbered}\n\n{t['thanks']}\n{signature}".rstrip() + "\n"
    return Draft(t["subject_credit" if grand_total < 0 else "subject"].format(number=number), body, asks)


_STATEMENT = {
    ENGLISH: {
        "subject": "Your statement of account: invoices we need",
        "intro": "Thank you for your statement of account. Comparing it with our records:",
        "missing": "We have no record of these invoices. Please send us a copy of each:",
        "differs": "These amounts differ from the invoices we hold. Please send a copy of the invoice, or the credit "
        "note that explains the difference:",
        "missing_line": "- {number}{date}: {amount}",
        "differs_line": "- {number}{date}: {amount} on the statement, {ours} on our copy",
        "dated": " dated {date}",
    },
    FRENCH: {
        "subject": "Votre relevé de compte : factures à nous transmettre",
        "intro": "Merci pour votre relevé de compte. En le comparant avec nos dossiers :",
        "missing": "Nous n'avons aucune trace de ces factures. Veuillez nous en envoyer une copie :",
        "differs": "Ces montants diffèrent des factures que nous avons. Veuillez nous envoyer une copie de la facture, "
        "ou la note de crédit qui explique l'écart :",
        "missing_line": "- {number}{date} : {amount}",
        "differs_line": "- {number}{date} : {amount} sur le relevé, {ours} sur notre copie",
        "dated": " du {date}",
    },
}


def statement_request(lines: Iterable[Any], language: str = ENGLISH, signature: str = "") -> Draft | None:
    """An email asking for the invoices missing from AP and explaining the differences, from the
    ``statements.reconcile`` lines; None when there is nothing to ask."""
    from .statements import DIFFERS, NOT_RECEIVED

    t, common = _STATEMENT[language], _TEXT[language]
    lines = list(lines)
    missing = [li for li in lines if li.status == NOT_RECEIVED]
    differs = [li for li in lines if li.status == DIFFERS]
    if not (missing or differs):
        return None

    def dated(li: Any) -> str:
        return t["dated"].format(date=li.date) if li.date else ""

    parts, asks = [t["intro"]], []
    if missing:
        rows = [t["missing_line"].format(number=li.number or "?", date=dated(li),
                                         amount=_amount(li.statement_amount, language)) for li in missing]  # fmt: skip
        parts.append(t["missing"] + "\n" + "\n".join(rows))
        asks += rows
    if differs:
        rows = [
            t["differs_line"].format(
                number=li.number or "?", date=dated(li), amount=_amount(li.statement_amount, language),
                ours=_amount(li.ap_amount, language),
            )
            for li in differs
        ]  # fmt: skip
        parts.append(t["differs"] + "\n" + "\n".join(rows))
        asks += rows
    body = f"{common['hello']}\n\n" + "\n\n".join(parts) + f"\n\n{common['thanks']}\n{signature}".rstrip() + "\n"
    return Draft(t["subject"], body, asks)
