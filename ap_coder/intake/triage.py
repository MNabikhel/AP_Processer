"""Email triage: what each email from the AP mailbox is, and where each of its attachments goes.

Every email is sorted into one of six kinds, from its subject, its text and what its attachments say about
themselves (a PDF's first pages; a spreadsheet's first rows), in English and French:

* invoice / credit note: the attachments go to the review queue, as before;
* statement: a vendor's statement of account goes to the Statements page (``statements/``), not the queue;
* payment reminder: an email chasing payment; a copy of an invoice attached to it still goes to the queue (the
  duplicate check says if AP already has it), the reminder itself waits for a person;
* bank-detail change request: always flagged as possible fraud, whatever else the email holds;
* not for AP: quotes, purchase orders, packing slips, remittance advice, newsletters, out-of-office replies, an
  email saying an invoice is online without attaching it: they wait for a person.

The rule that matters most: **an invoice is never lost.** A document is kept out of the review queue only when it
says plainly what else it is (a statement, a quote, a packing slip ...) and does not call itself an invoice. A
document that says nothing (a photo, a scan without text) goes to the queue, where a person sees it anyway.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from ..capture.normalize import CREDIT_TITLES

INVOICE, CREDIT, STATEMENT, REMINDER, BANK_CHANGE, NOT_FOR_AP = (
    "invoice",
    "credit_note",
    "statement",
    "payment_reminder",
    "bank_change",
    "not_for_ap",
)
LABELS = {
    INVOICE: "Invoice",
    CREDIT: "Credit note",
    STATEMENT: "Statement",
    REMINDER: "Payment reminder",
    BANK_CHANGE: "Bank-detail change (possible fraud)",
    NOT_FOR_AP: "Not for AP",
}
QUEUE_KINDS = (INVOICE, CREDIT)  # documents that go to the review queue
SHEET_EXTENSIONS = (".csv", ".xlsx", ".xls")
TOP_CHARS = 700  # a document's title is near its top: words found there count double
MAX_TEXT = 20_000
MAX_SHEET_PART_BYTES = 20_000_000  # one file inside an .xlsx, unzipped: openpyxl reads some in full
MAX_SHEET_BYTES = 50_000_000  # every file inside it, unzipped


def fold(text: str) -> str:
    """Lower case, accents removed, spaces collapsed: "Relevé  de COMPTE" -> "releve de compte"."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    return re.sub(r"\s+", " ", text).strip()


def _any(*phrases: str) -> re.Pattern[str]:
    return re.compile(r"(?<![a-z])(?:" + "|".join(phrases) + r")(?![a-z])")


# What a document calls itself (folded text).
_INVOICE_TITLE = _any(
    r"(?:tax |commercial |proforma )?invoice",
    r"facture",
    r"bill(?:ing)? (?:no|number|#)",
    r"invoice (?:no|number|#|date)",
    r"no\.? de facture",
    r"numero de facture",
)
_CREDIT_TITLE = _any(*CREDIT_TITLES)  # the same titles capture reads a credit note by
_STATEMENT_TITLE = _any(
    r"statement of account",
    r"account statement",
    r"customer statement",
    r"(?:monthly )?statement",
    r"releve de compte",
    r"etat de compte",
    r"releve mensuel",
    r"releve",
)
_AGING = re.compile(  # aged-balance columns; numbers only where they stand alone (not inside a phone number)
    r"(?<![\w-])(?:aging|ageing|1-30|31-60|61-90|over 90|90\+(?: days)?|> ?90|plus de 90|31 a 60|61 a 90|anciennete)"
    r"(?![\w-])"
)
_UTILITY = _any(
    r"billing period",
    r"meter",
    r"kwh",
    r"m3",
    r"gj",
    r"service address",
    r"usage",
    r"consumption",
    r"periode de facturation",
    r"compteur",
    r"consommation",
    r"adresse de service",
)
# A bill to pay, whatever it calls itself (a utility "billing statement", "your bill"): it goes to the queue.
_BILL = _any(
    r"amount due",
    r"total amount due",
    r"balance due",
    r"please pay",
    r"pay by",
    r"due date",
    r"bill (?:no|number|date)",
    r"billing date",
    r"your (?:\w+ )?bill",
    r"billing statement",
    r"montant (?:du|a payer)",
    r"total a payer",
    r"date d'echeance",
    r"payable (?:avant|le)",
    r"votre facture",
)
_INVOICE_REF = re.compile(r"(?<![a-z])(?:inv(?:oice)?|facture|fact|bill)\b.{0,40}\d")  # one line of a statement
_REMINDER = _any(
    r"payment reminder",
    r"reminder",
    r"past due",
    r"overdue",
    r"second notice",
    r"final notice",
    r"outstanding balance",
    r"outstanding invoices?",
    r"collection notice",
    r"friendly reminder",
    r"rappel(?: de paiement)?",
    r"avis de retard",
    r"en souffrance",
    r"en retard",
    r"solde impaye",
    r"factures? impayees?",
    r"dernier avis",
    r"relance",
)
_BANK = _any(
    r"(?:new|updated?|changed?|change (?:of|in|to)(?: our)?) (?:bank(?:ing)?|eft|ach|wire|remittance|payment) "
    r"(?:details|information|info|account|instructions)",
    r"bank(?:ing)? (?:details|information|account) (?:has|have) changed",
    r"changed? (?:our )?bank(?:s)?",
    r"new account number",
    r"void(?:ed)? cheque",
    r"void(?:ed)? check",
    r"direct deposit form",
    r"(?:nouvelles?|changement de|modification de|mise a jour de)(?:s)? (?:nos |de nos |des )?"
    r"(?:coordonnees|informations) bancaires",
    r"nouveau compte bancaire",
    r"changement de banque",
    r"cheque annule",
    r"specimen de cheque",
)
# Documents that are plainly something else (only when they do not also call themselves an invoice).
_OTHER_TITLE = {
    "a quote": _any(r"quot(?:e|ation)", r"estimate", r"soumission", r"devis", r"proposal"),
    "a purchase order": _any(r"purchase order", r"bon de commande"),
    "a packing slip": _any(
        r"packing (?:slip|list)",
        r"delivery (?:note|slip|receipt)",
        r"bill of lading",
        r"bordereau de (?:livraison|transport)",
        r"bon de livraison",
        r"connaissement",
    ),
    "remittance advice": _any(r"remittance advice", r"payment advice", r"avis de paiement", r"avis de versement"),
    "a tax or insurance form": _any(
        r"w-9",
        r"w-8ben",
        r"certificate of insurance",
        r"attestation d'assurance",
        r"wsib clearance",
        r"wcb clearance",
        r"certificat de conformite",
    ),
}
_NOT_AP_EMAIL = {
    "an automatic reply": _any(
        r"out of (?:the )?office",
        r"automatic reply",
        r"auto(?:matic)?[- ]?reply",
        r"absent du bureau",
        r"reponse automatique",
        r"autoreply",
    ),
    "a newsletter or marketing": _any(
        r"newsletter",
        r"unsubscribe",
        r"webinar",
        r"promotion",
        r"infolettre",
        r"se desabonner",
        r"special offer",
        r"offre speciale",
    ),
    "a payment confirmation": _any(
        r"thank you for your payment", r"payment received", r"merci pour votre paiement", r"paiement recu"
    ),
}
_INVOICE_MENTION = _any(r"invoice", r"facture", r"bill", r"credit note", r"note de credit", r"statement", r"releve")
_LINK = re.compile(r"https?://|www\.|click here|cliquez ici|portal|portail|view (?:it )?online|en ligne")


@dataclass
class Document:
    """An attachment as triage sees it: its file name and the text it holds (empty for a picture or a scan)."""

    name: str
    text: str = ""

    @property
    def sheet(self) -> bool:
        return Path(self.name).suffix.lower() in SHEET_EXTENSIONS


@dataclass
class Decision:
    kind: str
    reason: str


@dataclass
class EmailTriage:
    kind: str
    reasons: list[str] = field(default_factory=list)
    documents: list[tuple[Document, Decision]] = field(default_factory=list)
    possible_fraud: bool = False
    left_out: list[str] = field(default_factory=list)  # attachments not read (a zip, a Word file ...): may be invoices

    @property
    def needs_person(self) -> bool:
        """Someone has to look at this email (not just at its invoices in the review queue)."""
        return (
            self.possible_fraud
            or bool(self.left_out)
            or self.kind in (REMINDER, BANK_CHANGE, NOT_FOR_AP)
            or any(d.kind not in (*QUEUE_KINDS, STATEMENT) for _, d in self.documents)
        )

    @property
    def label(self) -> str:
        return LABELS[self.kind]


def _find(pattern: re.Pattern[str], text: str) -> str:
    m = pattern.search(text)
    return m.group(0) if m else ""


def classify_document(doc: Document, email_text: str = "") -> Decision:
    """Where one attachment goes. Only a plain statement of what it is keeps it out of the review queue."""
    name = fold(Path(doc.name).stem.replace("_", " ").replace("-", " "))
    text = fold(doc.text[:MAX_TEXT])
    top = f"{name} {text[:TOP_CHARS]}"
    if not text and not doc.sheet:  # never kept out of the queue on its file name alone
        return Decision(INVOICE, "no text to read (a picture or a scan): sent to the review queue to be read")
    if doc.sheet:  # a spreadsheet is never an invoice to read; a statement when it looks like one
        if _find(_STATEMENT_TITLE, top) or _find(_STATEMENT_TITLE, fold(email_text)):
            return Decision(STATEMENT, "a spreadsheet sent as a statement")
        return Decision(NOT_FOR_AP, "a spreadsheet that does not say it is a statement")
    credit = _find(_CREDIT_TITLE, top)
    if credit:
        return Decision(CREDIT, f'it says "{credit}"')
    invoice = _find(_INVOICE_TITLE, top)
    statement = _find(_STATEMENT_TITLE, top)
    # A statement of account lists several invoices or ages the balance; a utility bill called a "statement" (usage,
    # a meter, a billing period) is a bill to pay, so it goes to the queue.
    listed = sum(
        1 for line in fold(doc.text[:MAX_TEXT].replace("\n", "\x00")).split("\x00") if _INVOICE_REF.search(line)
    )
    if statement and (listed >= 3 or (listed >= 2 and _find(_AGING, text))) and not _find(_UTILITY, text):
        return Decision(STATEMENT, f'it says "{statement}" and lists {listed or "aged"} invoices')
    if invoice:
        return Decision(INVOICE, f'it says "{invoice}"')
    bank = _find(_BANK, top) or _find(_BANK, text)
    if bank:
        return Decision(BANK_CHANGE, f'it asks to change bank details ("{bank}")')
    bill = _find(_BILL, text) or _find(_UTILITY, text)
    if bill and not any(_find(p, top) for p in _OTHER_TITLE.values()):
        return Decision(INVOICE, f'a bill to pay ("{bill}")')
    for what, pattern in _OTHER_TITLE.items():
        found = _find(pattern, top)
        if found:
            return Decision(NOT_FOR_AP, f'{what} ("{found}"), not an invoice')
    reminder = _find(_REMINDER, top)
    if reminder:
        return Decision(REMINDER, f'a payment reminder ("{reminder}")')
    return Decision(INVOICE, "it does not say what it is: sent to the review queue to be read")


def classify_email(
    subject: str, body: str, documents: list[Document], left_out: list[str] | None = None
) -> EmailTriage:
    """What the email is, each attachment's decision, and whether it may be fraud. ``left_out``: the attachments that
    were not read (not a PDF, a picture or a spreadsheet, or damaged): the email then waits for a person, since one of
    them may be an invoice."""
    words = fold(f"{subject}\n{body}")
    decisions = [(d, classify_document(d, f"{subject}\n{body}")) for d in documents]
    kinds = [dec.kind for _, dec in decisions]
    out = EmailTriage(NOT_FOR_AP, documents=decisions, left_out=list(left_out or []))
    bank = _find(_BANK, words) or next(
        (_find(_BANK, fold(d.text[:MAX_TEXT])) for d in documents if _find(_BANK, fold(d.text[:MAX_TEXT]))), ""
    )
    if bank or BANK_CHANGE in kinds:
        out.possible_fraud = True
        out.reasons.append(
            f'it asks to change bank details ("{bank}"): confirm with the vendor by phone, using the '
            "number on file, never one in the email"
            if bank
            else "an attachment asks to change bank details: confirm with the vendor by phone, using the number on file"
        )
    queued = [k for k in kinds if k in QUEUE_KINDS]
    reminder = _find(_REMINDER, fold(subject)) or _find(_REMINDER, words[:1500])
    if queued:
        out.kind = CREDIT if all(k == CREDIT for k in queued) else INVOICE
        if reminder:
            out.reasons.append(
                f'it also chases payment ("{reminder}"): the duplicate check says if AP already has the invoice'
            )
    elif STATEMENT in kinds:
        out.kind = STATEMENT
    elif out.possible_fraud:
        out.kind = BANK_CHANGE
    elif reminder or REMINDER in kinds:
        out.kind = REMINDER
        out.reasons.append(f'a payment reminder ("{reminder or "reminder attached"}") with no invoice attached')
    else:
        why = next((f'{what} ("{_find(p, words)}")' for what, p in _NOT_AP_EMAIL.items() if _find(p, words)), "")
        if not why and _find(_INVOICE_MENTION, words) and not documents and not out.left_out:
            why = "it mentions an invoice but has none attached" + (
                " (it may be a link to a portal: download it and add it on Import invoices)"
                if _LINK.search(words)
                else ""
            )
        why = why or next((f"{doc.name}: {dec.reason}" for doc, dec in decisions if dec.kind == NOT_FOR_AP), "")
        if why or not out.left_out:
            out.reasons.append(why or "nothing in it for AP")
    for doc, dec in decisions:
        if dec.kind not in (*QUEUE_KINDS, STATEMENT) and f"{doc.name}: {dec.reason}" not in out.reasons:
            out.reasons.append(f"{doc.name}: {dec.reason}")
    if out.left_out:
        more = len(out.left_out) - 5
        listed = "; ".join(out.left_out[:5]) + (f"; and {more} more" if more > 0 else "")
        out.reasons.append(f"left out, not read: {listed}. Open the email: an invoice in it must be added by hand")
    return out


def pdf_text(content: bytes, pages: int = 2) -> str:
    """The text of a PDF's first pages ("" for a scan, or a PDF that cannot be opened)."""
    try:
        import pymupdf

        with pymupdf.open(stream=content, filetype="pdf") as doc:
            return "\n".join(doc[i].get_text() for i in range(min(pages, doc.page_count)))[:MAX_TEXT]
    except Exception:  # noqa: BLE001 - a damaged PDF: the review queue says so when it is read
        return ""


def sheet_unzips_too_big(content: bytes) -> bool:
    """True when an Excel workbook (.xlsx, a zip) holds a file over MAX_SHEET_PART_BYTES once unzipped, or files over
    MAX_SHEET_BYTES together: a few KB can unzip to GB, so every reader of an emailed or uploaded workbook asks this
    before openpyxl or pandas opens it (B-63, W2-01). A file that is not a zip (a CSV, an old .xls) is False: its
    size on disk is what gets read. Only the zip's directory is read here, nothing is unzipped."""
    import io
    import zipfile

    try:
        with zipfile.ZipFile(io.BytesIO(content)) as z:
            sizes = [item.file_size for item in z.infolist()]
    except (zipfile.BadZipFile, ValueError):
        return False
    return max(sizes, default=0) > MAX_SHEET_PART_BYTES or sum(sizes) > MAX_SHEET_BYTES


def sheet_text(name: str, content: bytes, rows: int = 15) -> str:
    """The first rows of a CSV or Excel attachment, as text ("" when it cannot be read)."""
    try:
        if name.lower().endswith(".csv"):
            return content[:8000].decode("utf-8-sig", "replace")
        import io

        import openpyxl

        if sheet_unzips_too_big(content):
            return ""
        wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        ws = wb.worksheets[0]
        out = [" ".join(str(c) for c in row if c is not None) for row in ws.iter_rows(max_row=rows, values_only=True)]
        wb.close()
        return "\n".join(out)
    except Exception:  # noqa: BLE001
        return ""


def document_for(name: str, content: bytes) -> Document:
    suffix = Path(name).suffix.lower()
    if suffix == ".pdf":
        return Document(name, pdf_text(content))
    if suffix in SHEET_EXTENSIONS:
        return Document(name, sheet_text(name, content))
    return Document(name, "")
