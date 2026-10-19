"""Email triage benchmark: made-up emails of every kind AP's mailbox gets, sorted by AP Coder and scored.

    python -m ap_coder.bench triage --n 300 --seed 1 --out bench_triage

Each email is built from a scenario (an invoice, a credit note, a utility bill called a "statement", a vendor
statement as a PDF or a spreadsheet, a payment reminder with or without the invoice, a bank-detail change, a quote, a
purchase order, a packing slip, remittance advice, an out-of-office reply, a newsletter, a portal link ...), in
English or French, saved as .eml or as Outlook .msg, then unpacked with ``mailbox.unpack`` exactly as the dashboard
does. Scored: the email's kind, where each attachment went (review queue, statements, a person), every bank-detail
change flagged, and above all **invoices lost**: an invoice or credit note that did not reach the review queue.

The invoices are the benchmark's own made-up invoices (``generator``), some scanned (no text to read); the other
documents are drawn here. Everything is made up: no real company, person or bank.
"""

from __future__ import annotations

import json
import random
import shutil
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from ..intake import mailbox, triage
from .generator import Renderer, _pymupdf, make_invoice, scan
from .msgwriter import Attachment, Message, build_msg

QUEUE, STATEMENTS, PERSON = "queue", "statements", "person"
INVOICE_ARCHES = (
    "classic_left",
    "vendor_right",
    "centered_logo",
    "header_grid",
    "boxed_fields",
    "totals_left",
    "table_footer",
    "quebec_fr",
    "bilingual",
    "us_vendor",
    "multipage",
    "remittance_stub",
    "minimal_courier",
)

VENDORS = [
    "Northwind Supplies",
    "Prairie Steel Ltd.",
    "Fournitures Laurentides inc.",
    "Bluewater Electric",
    "Rocky Ridge Rentals",
    "Atlas Concrete",
    "Distribution Boréal",
    "Summit Safety Co.",
    "Cascade Plumbing",
    "Groupe Saint-Laurent",
    "Maple Lane Lumber",
    "Polar Crane Services",
]


@dataclass
class Doc:
    name: str
    data: bytes
    mime: str
    route: str  # where it must go


@dataclass
class Scenario:
    name: str
    lang: str
    subject: str
    body: str
    sender: str
    docs: list[Doc]
    kind: str  # the email's expected kind (triage)
    fraud: bool = False
    extras: list[Doc] = field(default_factory=list)  # logos etc.: must be skipped, never routed


# --- documents ----------------------------------------------------------------------------------------------------


def _pdf(lines: list[tuple[str, float, bool]]) -> bytes:
    """A one-page PDF with the given lines of text (text, size, bold)."""
    pymupdf = _pymupdf()
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    y = 60.0
    for text, size, bold in lines:
        page.insert_text((54, y), text, fontsize=size, fontname="hebo" if bold else "helv")
        y += size * 1.6
        if y > 760:
            break
    out = doc.tobytes()
    doc.close()
    return out


def _money(rng: random.Random) -> str:
    return f"{rng.uniform(80, 25_000):,.2f}"


def _invoice(rng: random.Random, arch: str, scanned: bool) -> tuple[bytes, str]:
    pdf, _truth = Renderer(make_invoice(rng, arch), rng).render()
    if scanned:
        as_png = rng.random() < 0.4
        data, _ = scan(pdf, rng, as_png)
        return data, ".png" if as_png else ".pdf"
    return pdf, ".pdf"


def _statement_pdf(rng: random.Random, vendor: str, lang: str, aged: bool) -> bytes:
    fr = lang == "fr"
    title = rng.choice(
        ["RELEVÉ DE COMPTE", "État de compte", "Relevé mensuel"]
        if fr
        else ["STATEMENT OF ACCOUNT", "Customer Statement", "Account Statement", "Monthly Statement"]
    )
    lines = [
        (vendor, 14, True),
        (title, 18, True),
        (("Date du relevé" if fr else "Statement date") + ": 2026-09-30", 10, False),
        (("Client" if fr else "Customer") + ": Northern Ridge Builders (made-up)", 10, False),
        ("", 10, False),
    ]
    lines.append(
        (
            "Date      Document          Montant      Solde"
            if fr
            else "Date      Document          Amount      Balance",
            10,
            True,
        )
    )
    for _ in range(rng.randint(3, 9)):
        word = rng.choice(["Facture", "Fact."] if fr else ["Invoice", "INV", "Inv."])
        lines.append(
            (
                f"2026-0{rng.randint(6, 9)}-{rng.randint(10, 28)}  {word} {rng.randint(10000, 99999)}   "
                f"{_money(rng)}   {_money(rng)}",
                10,
                False,
            )
        )
    if aged:
        lines += [
            ("", 10, False),
            ("Courant   31-60   61-90   Plus de 90" if fr else "Current   31-60   61-90   Over 90", 10, True),
            (f"{_money(rng)}   {_money(rng)}   0.00   0.00", 10, False),
        ]
    lines.append((("Solde à payer" if fr else "Balance due") + f": {_money(rng)}", 12, True))
    return _pdf(lines)


def _statement_sheet(rng: random.Random, lang: str, xlsx: bool) -> tuple[bytes, str]:
    fr = lang == "fr"
    head = ["Date", "Facture" if fr else "Invoice", "Montant" if fr else "Amount", "Solde" if fr else "Balance"]
    rows = [
        [
            f"2026-0{rng.randint(6, 9)}-{rng.randint(10, 28)}",
            f"INV-{rng.randint(1000, 9999)}",
            round(rng.uniform(100, 9000), 2),
            round(rng.uniform(0, 9000), 2),
        ]
        for _ in range(rng.randint(3, 12))
    ]
    if xlsx:
        import io

        import openpyxl

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["Relevé de compte" if fr else "Statement of account"])
        ws.append(head)
        for r in rows:
            ws.append(r)
        buf = io.BytesIO()
        wb.save(buf)
        return buf.getvalue(), ".xlsx"
    text = "\n".join([",".join(head)] + [",".join(str(c) for c in r) for r in rows])
    return text.encode(), ".csv"


_OTHER_DOCS = {
    "quote": (["QUOTATION", "Quote", "Estimate"], ["SOUMISSION", "Devis"]),
    "purchase_order": (["PURCHASE ORDER"], ["BON DE COMMANDE"]),
    "packing_slip": (
        ["PACKING SLIP", "Delivery Note", "Bill of Lading"],
        ["BON DE LIVRAISON", "Bordereau de livraison"],
    ),
    "remittance": (["REMITTANCE ADVICE", "Payment Advice"], ["AVIS DE PAIEMENT"]),
    "w9": (["Form W-9", "Certificate of Insurance"], ["Attestation d'assurance"]),
}


def _other_pdf(rng: random.Random, kind: str, vendor: str, lang: str) -> bytes:
    en, fr = _OTHER_DOCS[kind]
    title = rng.choice(fr if lang == "fr" else en)
    lines = [
        (vendor, 14, True),
        (title, 18, True),
        (f"No. {rng.randint(1000, 99999)}", 10, False),
        ("Date: 2026-09-" + str(rng.randint(10, 28)), 10, False),
        ("", 10, False),
    ]
    for _ in range(rng.randint(2, 6)):
        lines.append(
            (
                f"{rng.choice(['Rebar 15M', 'Plywood 3/4', 'Safety vests', 'Crane rental', 'Concrete 32MPa'])}"
                f"   {rng.randint(1, 40)}   {_money(rng)}",
                10,
                False,
            )
        )
    if kind == "quote":
        lines.append(("Valide 30 jours" if lang == "fr" else "Valid for 30 days", 10, False))
    return _pdf(lines)


def _bank_letter(rng: random.Random, vendor: str, lang: str) -> bytes:
    if lang == "fr":
        lines = [
            (vendor, 14, True),
            ("Avis de changement de coordonnées bancaires", 16, True),
            ("Veuillez noter nos nouvelles coordonnées bancaires pour tous les paiements futurs.", 10, False),
            (
                f"Institution 00{rng.randint(1, 9)}  Transit {rng.randint(10000, 99999)}  "
                f"Compte {rng.randint(1000000, 9999999)}",
                10,
                False,
            ),
        ]
    else:
        lines = [
            (vendor, 14, True),
            (
                rng.choice(["Change of Banking Information", "Updated EFT details", "Notice: New Bank Account"]),
                16,
                True,
            ),
            ("Please update your records with our new banking information for all future payments.", 10, False),
            (
                f"Institution 00{rng.randint(1, 9)}  Transit {rng.randint(10000, 99999)}  "
                f"Account {rng.randint(1000000, 9999999)}",
                10,
                False,
            ),
        ]
    return _pdf(lines)


def _reminder_pdf(rng: random.Random, vendor: str, lang: str) -> bytes:
    title = rng.choice(
        ["AVIS DE RETARD", "Rappel de paiement"] if lang == "fr" else ["PAYMENT REMINDER", "Past Due Notice"]
    )
    return _pdf(
        [
            (vendor, 14, True),
            (title, 18, True),
            (("Montant en souffrance" if lang == "fr" else "Amount overdue") + f": {_money(rng)}", 11, False),
        ]
    )


def _logo(rng: random.Random) -> Doc:
    return Doc("image001.png", b"\x89PNG\r\n\x1a\n" + rng.randbytes(rng.randint(2000, 12000)), "image/png", "")


# --- emails -------------------------------------------------------------------------------------------------------

_SUBJECTS: dict[str, dict[str, list[str]]] = {
    "invoice": {
        "en": [
            "Invoice {n}",
            "INV {n} from {v}",
            "Your invoice from {v}",
            "Billing documents",
            "Invoice attached",
            "{v} - invoice #{n}",
            "Docs",
            "Fwd: invoice",
            "Scan from copier",
            "",
        ],
        "fr": [
            "Facture {n}",
            "Votre facture de {v}",
            "Facture no {n} ci-jointe",
            "Documents de facturation",
            "Tr : facture",
        ],
    },
    "credit": {
        "en": ["Credit note {n}", "Credit memo for invoice {n}", "Credit issued"],
        "fr": ["Note de crédit {n}", "Avoir sur facture {n}"],
    },
    "statement": {
        "en": ["Statement", "{v} statement of account", "Monthly statement - September", "Account statement"],
        "fr": ["Relevé de compte", "État de compte - septembre", "Relevé mensuel {v}"],
    },
    "reminder": {
        "en": [
            "Payment reminder",
            "PAST DUE - invoice {n}",
            "Second notice: outstanding balance",
            "Friendly reminder from {v}",
            "Overdue account",
        ],
        "fr": ["Rappel de paiement", "Facture {n} en souffrance", "Dernier avis - solde impayé", "Relance facture {n}"],
    },
    "bank": {
        "en": [
            "Change of banking information",
            "Updated bank details",
            "Important: new bank account",
            "Please update our EFT details",
            "Vendor banking update",
        ],
        "fr": [
            "Changement de coordonnées bancaires",
            "Nouvelles coordonnées bancaires",
            "Mise à jour de nos informations bancaires",
        ],
    },
}
_BODIES: dict[str, dict[str, list[str]]] = {
    "invoice": {
        "en": [
            "Please find attached our invoice.",
            "Hi, invoice attached. Thanks!",
            "See attached.",
            "Attached is invoice {n} for work completed at your site.",
            "",
        ],
        "fr": ["Veuillez trouver ci-joint notre facture.", "Bonjour, voici la facture {n}. Merci.", "Ci-joint."],
    },
    "credit": {
        "en": ["Please find attached a credit note against invoice {n}."],
        "fr": ["Veuillez trouver ci-jointe une note de crédit sur la facture {n}."],
    },
    "statement": {
        "en": ["Attached is your statement of account as of month end.", "Please review the attached statement."],
        "fr": ["Vous trouverez ci-joint votre relevé de compte.", "Veuillez consulter l'état de compte ci-joint."],
    },
    "reminder": {
        "en": [
            "Our records show invoice {n} is past due. Please arrange payment.",
            "This is a friendly reminder that your account has an outstanding balance of ${m}.",
            "Invoice {n} is now 45 days overdue.",
        ],
        "fr": [
            "Selon nos dossiers, la facture {n} est en souffrance. Merci de procéder au paiement.",
            "Ceci est un rappel : votre compte présente un solde impayé de {m} $.",
        ],
    },
    "bank": {
        "en": [
            "Please note our bank details have changed. Use the new account below for all future payments.",
            "We have changed banks. Kindly update your records with our new banking information.",
            "Effective immediately, please send all payments to our new account number 4471-882.",
        ],
        "fr": [
            "Veuillez noter nos nouvelles coordonnées bancaires pour tous les paiements futurs.",
            "Nous avons procédé à un changement de banque. Merci de mettre à jour vos dossiers.",
        ],
    },
}
_NOT_AP: dict[str, list[tuple[str, str]]] = {
    "en": [
        ("Automatic reply: Invoice 4471", "I am out of the office until Monday with limited access to email."),
        ("Out of Office", "Thank you for your email. I am currently out of the office."),
        ("Thank you for your payment", "We have received your payment of $1,240.00. Thank you!"),
        ("October newsletter", "New products this month! Click here to unsubscribe."),
        ("Webinar: safety on site", "Join our free webinar. Unsubscribe at any time."),
        (
            "Your invoice is ready",
            "Your invoice {n} is available online. Click here to view it in our customer portal.",
        ),
        ("Lunch on Friday?", "Are we still on for lunch?"),
    ],
    "fr": [
        ("Réponse automatique : facture", "Je suis absent du bureau jusqu'à lundi."),
        ("Merci pour votre paiement", "Nous avons bien reçu votre paiement. Merci!"),
        ("Infolettre d'octobre", "Nouveautés ce mois-ci. Pour vous désabonner, cliquez ici."),
        ("Votre facture est disponible", "Votre facture {n} est disponible en ligne dans notre portail client."),
    ],
}


def _pick(rng: random.Random, table: dict[str, list[str]], lang: str, **kw: Any) -> str:
    return rng.choice(table[lang]).format(**kw)


def scenario(rng: random.Random, index: int) -> Scenario:
    """One made-up email, drawn by ``index`` from a rotation of kinds (so every kind is in every run)."""
    kinds = [
        "invoice",
        "invoice",
        "invoice_scan",
        "invoice_multi",
        "credit",
        "utility",
        "statement_pdf",
        "statement_sheet",
        "reminder_only",
        "reminder_with_invoice",
        "reminder_letter",
        "bank_only",
        "bank_letter",
        "bank_with_invoice",
        "quote",
        "purchase_order",
        "packing_slip",
        "remittance",
        "not_ap_text",
        "invoice_and_packing_slip",
    ]
    name = kinds[index % len(kinds)]
    lang = "fr" if rng.random() < 0.3 else "en"
    v = rng.choice(VENDORS)
    n = f"{rng.choice(['INV-', '', 'F', 'A-'])}{rng.randint(1000, 99999)}"
    sender = f"ar@{v.split()[0].lower()}.example"
    kw = {"v": v, "n": n, "m": _money(rng)}
    arch = rng.choice(INVOICE_ARCHES if lang == "en" else ("quebec_fr", "bilingual", "classic_left"))

    count = iter(range(1, 100))

    def inv_doc(scanned: bool = False, a: str = arch) -> Doc:
        data, ext = _invoice(rng, a, scanned)
        return Doc(
            f"{rng.choice(['Invoice', 'Facture', 'INV', 'scan', 'document'])}_{n}_{next(count)}{ext}",
            data,
            "image/png" if ext == ".png" else "application/pdf",
            QUEUE,
        )

    s = Scenario(
        name,
        lang,
        _pick(rng, _SUBJECTS["invoice"], lang, **kw),
        _pick(rng, _BODIES["invoice"], lang, **kw),
        sender,
        [],
        triage.INVOICE,
    )
    if name in ("invoice", "invoice_scan"):
        s.docs = [inv_doc(scanned=name == "invoice_scan")]
    elif name == "invoice_multi":
        s.docs = [inv_doc(), inv_doc(scanned=rng.random() < 0.5)]
    elif name == "credit":
        s.subject, s.body = _pick(rng, _SUBJECTS["credit"], lang, **kw), _pick(rng, _BODIES["credit"], lang, **kw)
        s.docs, s.kind = [inv_doc(a="credit_note")], triage.CREDIT
    elif name == "utility":
        s.subject = rng.choice(["Your bill is ready", "Monthly statement - electricity", "Votre facture d'électricité"])
        s.docs = [inv_doc(a="utility_bill")]
    elif name == "statement_pdf":
        s.subject, s.body = _pick(rng, _SUBJECTS["statement"], lang, **kw), _pick(rng, _BODIES["statement"], lang, **kw)
        s.docs = [
            Doc(
                f"{rng.choice(['Statement', 'Releve', 'SOA'])}_{v.split()[0]}.pdf",
                _statement_pdf(rng, v, lang, aged=rng.random() < 0.6),
                "application/pdf",
                STATEMENTS,
            )
        ]
        s.kind = triage.STATEMENT
    elif name == "statement_sheet":
        s.subject, s.body = _pick(rng, _SUBJECTS["statement"], lang, **kw), _pick(rng, _BODIES["statement"], lang, **kw)
        data, ext = _statement_sheet(rng, lang, xlsx=rng.random() < 0.5)
        s.docs = [Doc(f"statement_{v.split()[0]}{ext}", data, "application/octet-stream", STATEMENTS)]
        s.kind = triage.STATEMENT
    elif name in ("reminder_only", "reminder_with_invoice", "reminder_letter"):
        s.subject, s.body = _pick(rng, _SUBJECTS["reminder"], lang, **kw), _pick(rng, _BODIES["reminder"], lang, **kw)
        s.kind = triage.REMINDER
        if name == "reminder_with_invoice":  # a copy of the invoice: to the queue (the duplicate check says the rest)
            s.docs, s.kind = [inv_doc()], triage.INVOICE
        elif name == "reminder_letter":
            s.docs = [Doc("reminder.pdf", _reminder_pdf(rng, v, lang), "application/pdf", PERSON)]
    elif name in ("bank_only", "bank_letter", "bank_with_invoice"):
        s.subject, s.body = _pick(rng, _SUBJECTS["bank"], lang, **kw), _pick(rng, _BODIES["bank"], lang, **kw)
        s.kind, s.fraud = triage.BANK_CHANGE, True
        if name == "bank_letter":
            s.docs = [Doc("banking_update.pdf", _bank_letter(rng, v, lang), "application/pdf", PERSON)]
        elif name == "bank_with_invoice":  # the invoice is read; the email is flagged
            s.subject = _pick(rng, _SUBJECTS["invoice"], lang, **kw) or "Invoice"
            s.docs, s.kind = [inv_doc()], triage.INVOICE
    elif name in ("quote", "purchase_order", "packing_slip", "remittance"):
        s.subject = {
            "quote": "Quote for your project",
            "purchase_order": "PO copy",
            "packing_slip": "Delivery documents",
            "remittance": "Remittance",
        }[name]
        s.body = "Please see attached." if lang == "en" else "Voir pièce jointe."
        s.docs = [Doc(f"{name}.pdf", _other_pdf(rng, name, v, lang), "application/pdf", PERSON)]
        s.kind = triage.NOT_FOR_AP
    elif name == "invoice_and_packing_slip":  # the invoice is read, the packing slip waits for a person
        s.docs = [
            inv_doc(),
            Doc("packing_slip.pdf", _other_pdf(rng, "packing_slip", v, lang), "application/pdf", PERSON),
        ]
    else:  # not_ap_text
        subj, body = rng.choice(_NOT_AP[lang])
        s.subject, s.body, s.kind = subj.format(**kw), body.format(**kw), triage.NOT_FOR_AP
    if rng.random() < 0.35:
        s.extras.append(_logo(rng))  # a signature logo: never a document
    return s


def write_email(s: Scenario, path: Path, as_msg: bool) -> Path:
    if as_msg:
        msg = Message(
            subject=s.subject,
            sender_name=s.sender.split("@")[0],
            sender_email=s.sender,
            body=s.body,
            attachments=[
                Attachment(d.name, d.data, d.mime, "logo@sig" if d in s.extras else "") for d in [*s.docs, *s.extras]
            ],
        )
        path = path.with_suffix(".msg")
        path.write_bytes(build_msg(msg))
        return path
    m = EmailMessage()
    m["Subject"], m["From"], m["To"] = s.subject, s.sender, "ap@pcl-made-up.example"
    m.set_content(s.body or " ")
    for d in s.docs:
        main, sub = d.mime.split("/")
        m.add_attachment(d.data, maintype=main, subtype=sub, filename=d.name)
    for d in s.extras:
        m.add_attachment(
            d.data, maintype="image", subtype="png", filename=d.name, cid="<logo@sig>", disposition="inline"
        )
    path = path.with_suffix(".eml")
    path.write_bytes(m.as_bytes())
    return path


def _route(folder: Path, p: Path) -> str:
    if p.parent == folder:
        return QUEUE
    if p.parent.name == mailbox.STATEMENTS_FOLDER:
        return STATEMENTS
    return PERSON


def run_triage(n: int = 300, seed: int = 1, out: str | Path = "bench_triage", msg_share: float = 0.4) -> dict[str, Any]:
    """Make ``n`` emails, unpack them as the dashboard does, score the sorting; writes report.json and report.md."""
    out = Path(out)
    inbox = out / "inbox"
    shutil.rmtree(inbox, ignore_errors=True)
    inbox.mkdir(parents=True)
    rng = random.Random(f"triage:{seed}")
    rows, failures = [], []
    t0 = time.time()
    for i in range(n):
        s = scenario(random.Random(f"triage:{seed}:{i}"), i)
        fmt = "msg" if rng.random() < msg_share else "eml"
        path = write_email(s, inbox / f"mail-{i:04d}", fmt == "msg")
        result = mailbox.unpack(path, inbox)
        got_routes = [_route(inbox, p) for p in [*result.saved, *result.statements, *result.held]]
        placed = {
            p.name.split(" - ", 1)[-1]: _route(inbox, p) for p in [*result.saved, *result.statements, *result.held]
        }
        t = result.triage
        lost = [d.name for d in s.docs if d.route == QUEUE and placed.get(d.name) != QUEUE]
        wrong_routes = [d.name for d in s.docs if placed.get(d.name) != d.route]
        extra_routed = [d.name for d in s.extras if d.name in placed]
        row = {
            "i": i,
            "scenario": s.name,
            "lang": s.lang,
            "format": fmt,
            "expected": s.kind,
            "got": t.kind if t else "unreadable",
            "fraud_expected": s.fraud,
            "fraud_got": bool(t and t.possible_fraud),
            "documents": len(s.docs),
            "routes_right": len(s.docs) - len(wrong_routes),
            "lost": lost,
            "into_queue_wrongly": [d.name for d in s.docs if d.route != QUEUE and placed.get(d.name) == QUEUE],
            "logo_routed": extra_routed,
            "routes": got_routes,
        }
        rows.append(row)
        if row["expected"] != row["got"] or wrong_routes or row["fraud_expected"] != row["fraud_got"] or extra_routed:
            failures.append({**row, "subject": s.subject, "reasons": t.reasons if t else [result.error]})
    summary = _summary(rows)
    summary["seconds_per_email"] = round((time.time() - t0) / max(n, 1), 3)
    report = {"config": {"n": n, "seed": seed, "msg_share": msg_share}, "summary": summary}
    (out / "report.json").write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    with (out / "failures.jsonl").open("w", encoding="utf-8") as fh:
        for f in failures:
            fh.write(json.dumps(f, ensure_ascii=False) + "\n")
    (out / "report.md").write_text(_markdown(report), encoding="utf-8")
    return report


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def block(rs: list[dict[str, Any]]) -> dict[str, Any]:
        docs = sum(r["documents"] for r in rs)
        fraud = [r for r in rs if r["fraud_expected"]]
        return {
            "emails": len(rs),
            "kind_right": sum(r["expected"] == r["got"] for r in rs) / max(len(rs), 1),
            "documents": docs,
            "routes_right": sum(r["routes_right"] for r in rs) / docs if docs else None,
            "invoices_lost": sum(len(r["lost"]) for r in rs),
            "into_queue_wrongly": sum(len(r["into_queue_wrongly"]) for r in rs),
            "fraud_flagged": f"{sum(r['fraud_got'] for r in fraud)} of {len(fraud)}",
            "fraud_false_alarms": sum(r["fraud_got"] and not r["fraud_expected"] for r in rs),
            "logos_routed": sum(len(r["logo_routed"]) for r in rs),
        }

    by: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for r in rows:
        by["scenario"][r["scenario"]].append(r)
        by["language"][r["lang"]].append(r)
        by["format"][r["format"]].append(r)
    confusion = Counter(f"{r['expected']} -> {r['got']}" for r in rows if r["expected"] != r["got"])
    return {
        "overall": block(rows),
        **{k: {name: block(rs) for name, rs in sorted(v.items())} for k, v in by.items()},
        "confusion": dict(confusion),
    }


def _markdown(report: dict[str, Any]) -> str:
    s, cfg = report["summary"], report["config"]
    pc = lambda v: "-" if v is None else f"{100 * v:.1f}%"  # noqa: E731
    o = s["overall"]
    lines = [
        "# Email triage benchmark",
        "",
        f"{cfg['n']} made-up emails (seed {cfg['seed']}, {round(100 * cfg['msg_share'])}% as Outlook .msg), unpacked "
        "with `mailbox.unpack` as the dashboard does.",
        "",
        "- **Invoices lost** (an invoice or credit note that did not reach the review queue): "
        f"**{o['invoices_lost']}**",
        f"- Email kind right: {pc(o['kind_right'])}; attachments sent to the right place: {pc(o['routes_right'])} "
        f"of {o['documents']}",
        f"- Bank-detail changes flagged as possible fraud: {o['fraud_flagged']} "
        f"(false alarms: {o['fraud_false_alarms']})",
        f"- Non-invoices sent to the review queue: {o['into_queue_wrongly']}; "
        f"signature logos routed: {o['logos_routed']}",
        f"- {s['seconds_per_email']} s per email",
        "",
        "| scenario | emails | kind right | routes right | invoices lost | into queue wrongly | fraud flagged |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for group in ("scenario", "language", "format"):
        for name, b in s[group].items():
            lines.append(
                f"| {group}: {name} | {b['emails']} | {pc(b['kind_right'])} | {pc(b['routes_right'])} | "
                f"{b['invoices_lost']} | {b['into_queue_wrongly']} | {b['fraud_flagged']} |"
            )
    if s["confusion"]:
        lines += ["", "Wrong kinds: " + "; ".join(f"{k} ({v})" for k, v in s["confusion"].items())]
    return "\n".join(lines) + "\n"
