"""Corrections the math confirms (D-263): the AI reviewer, asked what is wrong with an invoice that has a problem, and
its corrected values applied only when the invoice's own math then reconciles and each value is printed on its page.

The AI reviewer (verifier.py) only agrees or disagrees. An invoice with a problem a reading can cause (``problems``:
the lines, charges and taxes don't add up to the total, the lines to the subtotal, a tax to its rate, or the supplier
is missing or unsure while a supplier list is loaded) is asked one more question first, with its thinking: what is
wrong, and the values corrected (a charge left out of the lines such as freight, a line's amount, a tax, the subtotal
or the total, the supplier). The corrections are applied (``apply_items``, ``checked``) only when

* every amount in them is printed on the page as money (the words the PDF's text or OCR gave, and the page reader's
  transcription: ``page_text``; with its cents or a currency sign, never the digits of a phone, invoice or GST/HST
  number or a date: ``_printed_money``): the model can't invent a number;
* with all of them applied the invoice's own checks pass: the lines add up to the subtotal, each tax is its rate on
  the lines it is on, subtotal and taxes are the total (``taxmath.math_check``), and ``validate_coding`` finds no
  totals error and no error the invoice did not have;
* a supplier is ONE supplier of the supplier list: by the GST/HST number printed on the page, or by its name printed;
* nothing in them quotes text addressed to software (D-259); an invoice with such text is not asked at all.

Otherwise nothing changes: the review goes on as before, and its warnings say what it read. When they pass, they are
applied to the invoice only while nobody has worked on it (``Store.replace_proposal``: not parked, open or drafted, and
its proposal still the one asked about, ``proposal_version``): each field they changed is marked as the AI reviewer's
(its source, and a reason that starts ``CORRECTED_BY``), and VERIFIER_CORRECTED says what was corrected and why. That
finding always sends the invoice to a person (``supplier.ALWAYS_A_PERSON``): never touchless, never bulk-approved
because of an AI correction. They are a revision of the invoice (``verifier_corrected``): a review screen open on it
(a laptop keeps nothing of who has it open) is out of date, says so, and saves nothing over them. The review that
follows is of the corrected invoice.

The question is asked in the reviewer's thread, between the page reader's invoices (never while it reads), at most
``rounds`` times (Settings → Reading) and within half of the reviewer's minutes, so the review has the rest; a round
whose corrections were refused is asked again with why.
"""

from __future__ import annotations

import copy
import datetime as dt
import json
import logging
import re
from collections.abc import Callable
from typing import Any

from .prompts import DOCUMENT_IS_DATA
from .verifier import CORRECTED_BY, CORRECTED_CODE, MIN_CALL_SECONDS, NAME, READER, proposal, vendor_record

log = logging.getLogger(__name__)

KINDS = ("charge", "line_amount", "tax", "subtotal", "grand_total", "supplier")  # in the order they are applied
TAXES = ("GST", "HST", "PST", "QST")
# The checks that say the amounts as read don't add up (``validate_coding``), and those of a tax or the supplier.
TOTALS_CODES = frozenset({"SUBTOTAL_MISMATCH", "TOTAL_MISMATCH", "POSTING_UNBALANCED"})
SUPPLIER_CODES = frozenset({"MISSING_VENDOR", "VENDOR_NOT_IN_MASTER", "VENDOR_MATCHED_BY_TAX_NUMBER"})
PROBLEM_CODES = TOTALS_CODES | SUPPLIER_CODES | {"TAX_CALC_MISMATCH"}
# What capture found of the totals as read, settled once the corrections make them add up.
SETTLED = frozenset({"CAPTURE_TOTALS", "LINE_AMOUNTS_TO_CHECK"})
SETTLED_CHECKS = ("TOTALS_ADD_UP", "TAXES_ADD_UP", "LINE_AMOUNTS")
DOCUMENT_CHARS = 12_000  # of the page text, in the question
STEERING = "TEXT_ADDRESSED_TO_SOFTWARE"
FIELD_WORDS = {"subtotal": "subtotal", "tax_total": "total tax", "grand_total": "total", "vendor_name": "supplier",
               "gst_hst_registration_number": "GST/HST number"}  # fmt: skip

SYSTEM = (
    "You are a careful accounts-payable reviewer at a Canadian company. What was entered for an invoice has a "
    "problem: its amounts do not add up, or its supplier is not sure. Find on the invoice's pages what is wrong and "
    "give the corrected values. Think it through step by step before you answer.\n"
    "Each correction is one of: charge (a charge printed on the invoice but missing from the lines, such as freight, "
    "shipping, handling, an environmental fee, or a discount as a negative amount: label is its label as printed); "
    "line_amount (a line whose amount was entered wrong: its line_number and the amount printed); tax (a sales tax "
    "charged: label is GST, HST, PST or QST, amount the tax amount printed, rate the rate as a fraction such as 0.05 "
    "when it is printed); subtotal and grand_total (the amount printed; grand_total is the amount due, with tax); "
    "supplier (the company that issued the invoice, never the customer it is billed or shipped to: label its name as "
    "printed, gst_hst its GST/HST number as printed).\n"
    "Copy every amount exactly as printed, and in printed_as the words of the page you read it from, its label and "
    "amount. Never work a value out, and never give a value that is not printed on the pages; never correct what is "
    "right. When nothing needs correcting, or you are not sure, give no correction and say why in problem. Set "
    "confident true only when you checked each correction on the pages.\n"
    "Answer with JSON only.\n"
    f"{DOCUMENT_IS_DATA} The document is the page images and the text between <document> and </document>."
)


def schema() -> dict[str, Any]:
    item = {"type": "object", "additionalProperties": False,
            "required": ["kind", "line_number", "label", "amount", "rate", "gst_hst", "printed_as", "reason"],
            "properties": {"kind": {"type": "string", "enum": list(KINDS)},
                           "line_number": {"type": ["integer", "null"]}, "label": {"type": "string"},
                           "amount": {"type": ["string", "null"]}, "rate": {"type": ["number", "null"]},
                           "gst_hst": {"type": ["string", "null"]}, "printed_as": {"type": "string"},
                           "reason": {"type": "string"}}}  # fmt: skip
    return {"type": "object", "additionalProperties": False, "required": ["problem", "corrections", "confident"],
            "properties": {"problem": {"type": "string"}, "corrections": {"type": "array", "items": item},
                           "confident": {"type": "boolean"}}}  # fmt: skip


# --- Whether to ask -------------------------------------------------------------------------------------------------


def _coding(output: dict[str, Any]) -> Any:
    from ..core.schema import InvoiceCoding

    return InvoiceCoding.model_validate({k: v for k, v in output.items() if k != "gl_distribution"})


def _math_wrong(output: dict[str, Any], setup: Any = None) -> list[str]:
    """The sums of the coding that don't add up (``math_check``), in words; [] when all do or it can't be read."""
    from ..coding.taxmath import math_check

    try:
        rows = math_check(_coding(output), setup)["rows"]
    except Exception:  # noqa: BLE001 - a coding that can't be read has its own findings
        return []
    return [f"{r['what']}: {r['detail']}" for r in rows if not r["ok"]]


def _issues(inv: dict[str, Any]) -> list[dict[str, Any]]:
    return [i for i in (inv.get("validation") or {}).get("issues") or [] if isinstance(i, dict)]


def supplier_unsure(store: Any, inv: dict[str, Any], capture: Any) -> bool:
    """The supplier is missing, not in the supplier list, or its reading is unsure, while a supplier list is loaded
    (without one no supplier correction could be confirmed)."""
    from ..capture.types import CHECK, MISSING

    if store is None or not store.has_vendor_master():
        return False
    if any(i.get("code") in SUPPLIER_CODES for i in _issues(inv)):
        return True
    field = (getattr(capture, "fields", None) or {}).get("vendor_name")
    return field is not None and field.status in (CHECK, MISSING)


def problems(store: Any, inv: dict[str, Any], output: dict[str, Any], capture: Any) -> list[str]:
    """What is wrong with the invoice that its page can correct, in words; [] when nothing (it is not asked)."""
    listed = store.has_vendor_master()
    said = [str(i.get("message") or i.get("code")) for i in _issues(inv)
            if i.get("code") in PROBLEM_CODES and (i.get("code") not in SUPPLIER_CODES or listed)]  # fmt: skip
    said += _math_wrong(output)
    if supplier_unsure(store, inv, capture) and not any(i.get("code") in SUPPLIER_CODES for i in _issues(inv)):
        field = capture.fields.get("vendor_name")
        said.append("the supplier is not sure" + (f": {field.reasons[-1]}" if field.reasons else ""))
    return list(dict.fromkeys(said))


def addressed_to_software(inv: dict[str, Any], capture: Any) -> bool:
    return bool(getattr(capture, "instructions", None)) or any(i.get("code") == STEERING for i in _issues(inv))


def page_text(settings: Any, inv: dict[str, Any]) -> str:
    """The invoice's words as read from its pages: the PDF's text or OCR, kept with it, and the page reader's
    transcription when it has one kept."""
    from .page_reader import kept_pages

    texts = [str(inv.get("extraction_md") or "")]
    if settings is not None and inv.get("source_path"):
        texts += kept_pages(settings, inv["source_path"])
    return "\n\n".join(t for t in texts if t.strip())


# --- Asking -------------------------------------------------------------------------------------------------------


def question(shown: dict[str, Any], wrong: list[str], text: str, earlier: dict[str, Any] | None) -> str:
    entered = {k: v for k, v in shown.items() if k != "pages"}
    parts = ["This invoice has a problem:", *[f"- {w}" for w in wrong],
             "Find on its pages (the images below, and their text) what is wrong, and give the corrected values.",
             "What was entered (with the vendor list's record of the supplier, and the math worked out again):",
             json.dumps(entered, ensure_ascii=False, indent=1),
             "The invoice's text, as read from its pages:",
             f"<document>\n{text[:DOCUMENT_CHARS]}\n</document>"]  # fmt: skip
    if earlier:
        parts += ["You answered before, and your corrections were not applied:",
                  json.dumps(earlier, ensure_ascii=False, indent=1),
                  "Look at the pages again. Give only values printed on them, and no correction when you are not "
                  "sure."]  # fmt: skip
    return "\n".join(parts)


def ask_model(coder: Any, images: list[Any], seconds_left: Callable[[], float]) -> Callable[[str], dict[str, Any]]:
    """One call to the local chat model with the page images, thinking on, each given the time left to it."""
    from . import local_llm
    from .inference import CodingError
    from .verifier import response_limit

    model = coder._prepare_local()

    def ask(text: str) -> dict[str, Any]:
        content: list[dict[str, Any]] = [{"type": "text", "text": text}]
        content += [{"type": "image_url", "image_url": {"url": img.to_data_url(), "detail": "high"}} for img in images]
        messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": content}]
        timeout = max(MIN_CALL_SECONDS, float(seconds_left()))
        response = coder._local_create(model, messages, schema(), thinking=True, timeout=timeout)
        reply = local_llm.read_reply(response.choices[0], response_limit(coder), strict=True)
        if reply.data is None:
            raise CodingError(reply.problem)
        return reply.data

    return ask


# --- Applying them, as far as the page and the math confirm them -------------------------------------------------


def _money(value: Any) -> str:
    try:
        return f"{float(value):,.2f}"
    except (TypeError, ValueError):
        return str(value or "nothing")


def _said(item: dict[str, Any]) -> str:
    label = " ".join(str(item.get("label") or "").split())[:80]
    kind = item.get("kind")
    if kind == "line_amount":
        return f"line {item.get('line_number')} {item.get('amount') or ''}".strip()
    if kind == "supplier":
        return f"supplier {label or item.get('gst_hst') or ''}".strip()
    return f"{label or FIELD_WORDS.get(str(kind), str(kind))} {item.get('amount') or ''}".strip()


def _plain_words(text: str) -> str:
    return " ".join(re.sub(r"[^0-9a-z]+", " ", str(text).casefold()).split())


def _printed_name(name: str, text: str) -> bool:
    """``name`` is printed in ``text`` as whole words, never inside another word ("Ace" in "replacement"); the words of
    a name of several may run together, as a scan reads them ("BIRCH ELECTRICLTD.")."""
    words = _plain_words(name).split()
    return bool(words) and re.search(rf"\b{' ?'.join(map(re.escape, words))}\b", _plain_words(text)) is not None


_CENTS = re.compile(r"\d[.,]\d{2}(?!\d)")
_CURRENCY = re.compile(r"[$€£]|(?<![a-z])(?:cad|cdn|usd|eur|gbp)(?![a-z])", re.I)
_CURRENCY_BEFORE = re.compile(r"(?:[$€£]|(?<![a-z])(?:cad|cdn|usd|eur|gbp)) ?\Z", re.I)  # ("CAD 150", "CA$150")
_CURRENCY_AFTER = re.compile(r" ?(?:[$€£]|(?:cad|cdn|usd|eur|gbp)(?![a-z]))(?! ?\.?\d)", re.I)  # (not the next one's)
_NEXT_ONES_SIGN = re.compile(r"\s*[$€£]\s*\Z")  # (the "$" find_amounts gives "75.00" in "75.00 $150.00")
_JOINED_BEFORE = re.compile(r"(?:[\d#]|\w[-/.])\Z")  # a digit or "#" right before, or a letter or digit and "-" "/" "."
_LETTER_BEFORE = re.compile(r"[^\W\d]\Z")  # (a scan's "Total1,207.50" has its cents; "RT0001" is no amount)
_JOINED_AFTER = re.compile(r"[\w%]| %|[-/.]\d")  # a letter, digit or "%" right after, or a "-", "/" or "." and a digit


def _printed_money(text: str) -> list[float]:
    """Each amount ``text`` prints as money (without its sign): with its cents ("150.00", "1 234,56", "(50.00)") or a
    currency sign or code right beside it ("$150", "150 CAD"). Never the digits of an identifier, a number run into
    letters or other digits ("(604) 555-0150", "INV-000450", "2026-11-02", "V6B 1A1", "RT0001"), nor a rate ("5.00%"):
    a phone, invoice, PO or GST/HST number, a date or a street number is no amount printed."""
    from ..core.parsing import find_amounts

    found = []
    for value, start, end in find_amounts(text):
        if (sign := _NEXT_ONES_SIGN.search(text, start, end)) and re.match(r"\s*\.?\d", text[end : end + 3]):
            end = sign.start()
        shown = text[start:end]
        start, end = start + len(shown) - len(shown.lstrip()), end - len(shown) + len(shown.rstrip())
        cents = _CENTS.search(text, start, end)
        before = _CURRENCY.match(text, start) or _CURRENCY_BEFORE.search(text, max(0, start - 4), start)
        after = _CURRENCY_AFTER.match(text, end)
        lead = start + (text[start : start + 1] in "-−–")  # (a minus run into a word is its hyphen: "INV-450.00")
        if not before and (_JOINED_BEFORE.search(text, max(0, lead - 2), lead)
                           or not cents and _LETTER_BEFORE.search(text, max(0, start - 1), start)):  # fmt: skip
            continue  # run into a word or a number ("RT0001", "555-0150"; run into its currency, "CAD150", is money)
        if not after and _JOINED_AFTER.match(text, end):
            continue  # ("5.00%", "2026-11-02"; "722.93CAD" is money)
        if cents or before or after or _CURRENCY.search(text, start, end):
            found.append(abs(value))
    return found


def _carrying(lines: list[dict[str, Any]], tax: str) -> tuple[list[dict[str, Any]], bool]:
    """(the lines ``tax`` is on, whether none was): a tax no line carries is on every line that is not zero-rated or
    exempt from it (none when each is)."""
    marked = [li for li in lines if tax in (li.get("taxes_applied") or [])]
    if marked:
        return marked, False
    return [li for li in lines if not li.get("tax_status") and tax not in (li.get("tax_exempt") or [])], True


def _which_lines(some: list[dict[str, Any]], lines: list[dict[str, Any]]) -> str:
    """ "every line", "line 2", "lines 1, 2 and 4": ``some`` of the invoice's ``lines``."""
    numbers = [str(li.get("line_number")) for li in some]
    if len(some) == len(lines):
        return "every line"
    return f"line {numbers[0]}" if len(numbers) == 1 else f"lines {', '.join(numbers[:-1])} and {numbers[-1]}"


def _rate(given: Any, amount: float, base: float, lines: int, out: dict[str, Any], setup: Any) -> float | None:
    """The tax's rate: the one given (a rate printed), else a rate in force on the invoice's date that makes the
    amount of ``base`` (GST under an HST label is the GST's 5%: D-262); None when none does."""
    from ..coding.money import half_up, per_line

    if isinstance(given, (int, float)) and not isinstance(given, bool) and 0 < given < 100:
        return float(given) / 100 if given >= 1 else float(given)
    rates = getattr(setup, "rates", None)
    if not base or rates is None:
        return None
    try:
        on = dt.date.fromisoformat(str(out.get("invoice_date") or ""))
    except ValueError:
        on = None
    official = sorted({r.rate for r in rates.in_force(on)})
    return next((r for r in official if abs(half_up(r * base) - amount) <= per_line(lines)), None)


def _supplier(item: dict[str, Any], text: str, store: Any, out: dict[str, Any]) -> tuple[str, str] | str:
    """(the supplier's name in the list, its GST/HST number printed or "") for a supplier correction, or why not."""
    from ..capture.normalize import find_gst_numbers, norm_gst
    from ..capture.workflow import our_name_keys
    from ..core.memory import vendor_key
    from ..core.store.vendor_master import with_number

    if store is None:
        return "no supplier list"
    name = " ".join(str(item.get("label") or "").split())
    number = norm_gst(str(item.get("gst_hst") or ""))
    numbered = ""
    if number:
        if number not in {norm_gst(n) for n, *_ in find_gst_numbers(text)}:
            return f"GST/HST number {item.get('gst_hst')} is not printed on the page"
        one = store.supplier_of_tax_number(number)
        if one is None:
            return f"no one supplier in the supplier list has GST/HST number {item.get('gst_hst')}"
        name = str(one["display_name"])
        numbered = with_number(one) if store.vendors_sharing(name) else ""  # (one of the suppliers of its name)
    elif not _printed_name(name, text):
        return f"{name or 'the supplier'} is not printed on the page"
    else:
        found = store.find_vendor(name)
        if found is None or not found.get("in_master") or store.vendors_sharing(name):
            return f"{name} is not one supplier in the supplier list"
        name = str(found["display_name"])
    if vendor_key(name) in our_name_keys(store):  # (its name as the list has it, never "... #2004": B-2801)
        return f"{name} is your company's own name"
    name = numbered or name
    if vendor_key(name) == vendor_key(out.get("vendor_name") or ""):
        return f"{name} is the supplier already"
    return name, number


def apply_items(output: dict[str, Any], items: list[dict[str, Any]], text: str, *, store: Any, setup: Any,
                supplier_wanted: bool) -> tuple[dict[str, Any], list[str], list[str]]:  # fmt: skip
    """The coding with each correction applied that the page bears out (its amount printed, nothing quoted from text
    addressed to software, a supplier of the list): (the coding, what was corrected, what was not and why). The math
    is ``checked``'s. ``supplier_wanted``: the supplier was in question (else a supplier correction is not taken)."""
    from ..capture.instructions import found_in
    from ..capture.normalize import parse_amount
    from ..coding.money import add_up
    from ..core.reference_data import UNASSIGNED

    out = copy.deepcopy({k: v for k, v in output.items() if k != "gl_distribution"})
    lines: list[dict[str, Any]] = out.setdefault("line_items", [])
    taxes: list[dict[str, Any]] = out.setdefault("tax_lines", [])
    printed = _printed_money(text)
    old_base = {t.get("tax_type"): add_up(li["amount"] for li in lines
                                          if t.get("tax_type") in (li.get("taxes_applied") or []))
                for t in taxes}  # fmt: skip
    said: list[str] = []
    refused: list[str] = []
    lines_changed = taxes_changed = subtotal_given = False
    for item in sorted(items, key=lambda i: KINDS.index(i["kind"])):
        kind, what = item["kind"], _said(item)
        if found_in(" ".join(str(item.get(k) or "") for k in ("label", "printed_as", "amount", "gst_hst"))):
            refused.append(f"{what}: it quotes text addressed to software")
            continue
        if kind == "supplier":
            if not supplier_wanted:
                refused.append(f"{what}: the supplier was not in question")
                continue
            found = _supplier(item, text, store, out)
            if isinstance(found, str):
                refused.append(f"{what}: {found}")
                continue
            name, number = found
            said.append(f"supplier {name} (was {out.get('vendor_name') or 'none'})"
                        + (f", by its GST/HST number {number} in the supplier list" if number else ""))  # fmt: skip
            out["vendor_name"] = name
            if number:
                out["gst_hst_registration_number"] = number
            out.pop("company", None)  # decided again for this supplier: its PO, history (D-267, B-2682)
            out.pop("company_how", None)
            continue
        amount = parse_amount(str(item.get("amount") or ""))
        if amount is None:
            refused.append(f"{what}: no amount")
            continue
        if not any(abs(abs(amount) - v) < 0.005 for v in printed):
            refused.append(f"{what}: {_money(amount)} is not printed on the page")
            continue
        if kind == "charge":
            label = " ".join(str(item.get("label") or "").split())[:80] or "Charge"
            # Taxed as every other line is, when they all carry the same taxes.
            common = set.intersection(*(set(li.get("taxes_applied") or []) for li in lines)) if lines else set()
            number = max((int(li.get("line_number") or 0) for li in lines), default=0) + 1
            center = UNASSIGNED if any(li.get("predicted_cost_center") for li in lines) else ""  # (when they're used)
            lines.append({"line_number": number, "description": label, "quantity": 1.0, "unit_price": amount,
                          "amount": amount, "predicted_gl_code": UNASSIGNED, "predicted_cost_center": center,
                          "taxes_applied": sorted(common), "tax_status": "",
                          "reasoning_justification": f"{CORRECTED_BY}: printed as "
                                                     f"{str(item.get('printed_as'))[:80]}"})  # fmt: skip
            said.append(f"added {label} {_money(amount)} as line {number}")
            lines_changed = True
        elif kind == "line_amount":
            li = next((li for li in lines if str(li.get("line_number")) == str(item.get("line_number"))), None)
            if li is None:
                refused.append(f"{what}: the invoice has no line {item.get('line_number')}")
                continue
            if abs(float(li.get("amount") or 0) - amount) < 0.005:
                continue
            said.append(f"line {li['line_number']} {_money(amount)} (was {_money(li.get('amount'))})")
            li["amount"] = amount
            if abs(float(li.get("quantity") or 0) - 1) < 1e-9:
                li["unit_price"] = amount
            lines_changed = True
        elif kind == "tax":
            tax = str(item.get("label") or "").strip().upper()
            if tax not in TAXES:
                refused.append(f"{what}: not a Canadian sales tax (GST, HST, PST or QST)")
                continue
            same = [t for t in taxes if t.get("tax_type") == tax]
            if len(same) > 1:
                refused.append(f"{what}: the invoice has {tax} at more than one rate")
                continue
            marked, newly = _carrying(lines, tax)
            if not marked:
                refused.append(f"{what}: no line can carry it (each is zero-rated or exempt from {tax})")
                continue
            base = add_up(li["amount"] for li in marked)
            rate = _rate(item.get("rate"), amount, base, len(marked), out, setup)
            if rate is None:
                refused.append(f"{what}: no rate in force makes {_money(amount)} of the {_money(base)} it is on")
                continue
            for li in marked:
                li["taxes_applied"] = sorted({*(li.get("taxes_applied") or []), tax})
            if newly and same:  # (a new tax line says the lines' amount it is on)
                said.append(f"{tax} on {_which_lines(marked, lines)} (no line had it)")
            if same:
                if abs(float(same[0].get("tax_amount") or 0) - amount) < 0.005 and not item.get("rate"):
                    continue
                said.append(f"{tax} {_money(amount)} (was {_money(same[0].get('tax_amount'))})")
                same[0].update(rate=rate, taxable_amount=base, tax_amount=amount)
            else:
                where = "" if tax == "GST" else (out.get("ship_to_province") or out.get("place_of_supply")
                                                 or out.get("supplier_province") or "")  # fmt: skip
                taxes.append({"tax_type": tax, "province": where, "rate": rate, "taxable_amount": base,
                              "tax_amount": amount})  # fmt: skip
                said.append(f"{tax} {_money(amount)} on {_money(base)}")
            taxes_changed = True
        else:  # subtotal, grand_total
            if abs(float(out.get(kind) or 0) - amount) < 0.005:
                continue
            said.append(f"{FIELD_WORDS[kind]} {_money(amount)} (was {_money(out.get(kind))})")
            out[kind] = amount
            subtotal_given = subtotal_given or kind == "subtotal"
    if lines_changed:
        said += _follow_the_lines(out, old_base, subtotal_given)
    if taxes_changed:
        out["tax_total"] = add_up(t["tax_amount"] for t in taxes)
    return out, said, refused


def _follow_the_lines(out: dict[str, Any], old_base: dict[Any, float], subtotal_given: bool) -> list[str]:
    """After lines were added or changed: a tax whose taxable amount was its lines' follows them, and the subtotal
    before tax is the lines' added up unless a correction gave it (its total is printed). Returns what was said."""
    from ..coding.money import add_up

    lines, said = out["line_items"], []
    for t in out["tax_lines"]:
        now = add_up(li["amount"] for li in lines if t.get("tax_type") in (li.get("taxes_applied") or []))
        if abs(float(t.get("taxable_amount") or 0) - old_base.get(t.get("tax_type"), 0.0)) < 0.005:
            t["taxable_amount"] = now
    if not subtotal_given:
        was, out["subtotal"] = out.get("subtotal"), add_up(li["amount"] for li in lines)
        if abs(float(was or 0) - out["subtotal"]) >= 0.005:
            said.append(f"subtotal {_money(out['subtotal'])}, the lines added up (was {_money(was)})")
    return said


def checked(settings: Any, store: Any, inv: dict[str, Any], corrected: dict[str, Any], *,
            supplier: bool = False) -> tuple[Any, Any] | str:  # fmt: skip
    """The corrected coding with its checks (``finalise_coding``'s output and report), or why the math refuses it:
    a sum that does not add up (``math_check``), a totals error left (a supplier's too, once ``supplier`` was
    corrected), or an error the invoice did not have."""
    from ..coding.bulk import _received_on
    from ..coding.taxmath import math_check
    from ..coding.validation import ERROR
    from .extraction import kept_extraction
    from .pipeline import finalise_coding

    try:
        coding = _coding(corrected)
    except ValueError as exc:
        return f"the corrected invoice can't be read ({str(exc)[:120]})"
    reference = store.reference_data()
    output, report = finalise_coding(coding, reference, settings, kept_extraction(inv), store=store,
                                     exclude_invoice_id=int(inv["id"]), received=_received_on(inv))  # fmt: skip
    wrong = [f"{r['what']}: {r['detail']}" for r in math_check(_coding(output), reference.tax)["rows"] if not r["ok"]]
    if wrong:
        return "with them the invoice does not add up (" + "; ".join(wrong)[:300] + ")"
    had = {i.get("code") for i in _issues(inv) if i.get("severity") == ERROR}
    left_out = TOTALS_CODES | (SUPPLIER_CODES if supplier else frozenset())
    left = sorted({i.code for i in report.errors if i.code in left_out or i.code not in had})
    if left:
        return f"with them the checks still find {', '.join(left)}"
    return output, report


def _capture(store: Any, iid: int) -> Any:
    from ..capture.types import CaptureResult

    stored = store.get_capture(iid)
    if not stored:
        return None
    try:
        return stored if isinstance(stored, CaptureResult) else CaptureResult.from_dict(stored)
    except Exception:  # noqa: BLE001 - an older capture: the coding is corrected without it
        return None


def mark(capture: Any, before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    """Each header field the corrections changed, in the capture: its value now the AI reviewer's (its source), the
    readers' own kept beside it ("other:"), Check, with why; capture's checks of the totals as read settled. Returns
    the fields."""
    from ..capture.types import CHECK, MISSING, FieldResult
    from ..capture.workflow import NOT_READERS

    changed = [f for f in FIELD_WORDS if str(before.get(f) or "") != str(after.get(f) or "")]
    for name in changed:
        fr = capture.fields.get(name) or FieldResult(name, None, 0.0, MISSING)
        readers = [r for r in fr.sources if not r.startswith("other:") and r not in NOT_READERS and r != READER]
        kept = {f"other:{fr.value}": ",".join(readers)} if readers and fr.value not in (None, "") else {}
        value = after.get(name)
        fr.sources = {READER: f"{value:.2f}" if isinstance(value, float) else str(value), **kept,
                      **{k: v for k, v in fr.sources.items() if k.startswith("other:")}}  # fmt: skip
        fr.value, fr.status, fr.confidence = value, CHECK, min(fr.confidence or 0.6, 0.6)
        was = before.get(name)
        fr.reasons = [*fr.reasons, f"{CORRECTED_BY} (was {_money(was) if isinstance(was, float) else was or 'none'}):"
                      " printed on the page, and the invoice adds up with it"]  # fmt: skip
        capture.fields[name] = fr
    for check in capture.checks:
        if check.get("code") in SETTLED_CHECKS and not check.get("ok", True):
            check["ok"] = True
            check["detail"] = f"{check.get('detail') or ''}; {CORRECTED_BY}: it adds up now".lstrip("; ")
    return changed


def _store(store: Any, inv: dict[str, Any], output: dict[str, Any], report: Any, said: list[str], problem: str,
           identity: Any, model: str) -> str:  # fmt: skip
    """The corrected coding into the invoice, still untouched, not in use and its proposal still the one ``inv`` had
    when the question was asked: ``said`` as VERIFIER_CORRECTED, the findings made after processing kept (but those
    the corrections settle). The finding's message; "" when it was not applied (a person has it open, or worked on it,
    it was read or checked again, or a backup was restored meanwhile)."""
    from ..coding.bulk import _kept
    from ..coding.validation import INFO, Issue
    from ..core.store.lessons import proposal_version

    iid = int(inv["id"])
    if store.restoring() or store.row_identity(iid) != identity:
        return ""
    message = (f"the {NAME} corrected what was read, and the invoice now adds up as printed: {'; '.join(said)}"
               + (f" (it found: {problem.rstrip('.')})" if problem else "")
               + ". Check these against the page before approving")[:900]  # fmt: skip
    have = {(i.code, i.line_number) for i in report.issues}
    for i in _kept(_issues(inv)):
        if i.get("code") not in SETTLED | {CORRECTED_CODE} and (i.get("code"), i.get("line_number")) not in have:
            report.issues.append(Issue(str(i.get("severity")), str(i.get("code")), str(i.get("message") or ""),
                                       i.get("line_number")))  # fmt: skip
    report.issues.append(Issue(INFO, CORRECTED_CODE, message))
    report.requires_review = True
    capture = _capture(store, iid)
    if capture is not None:
        mark(capture, inv.get("ai_output") or {}, output)
    note = {"applied": said, "problem": problem, "model": model, "at": dt.datetime.now().isoformat(timespec="seconds")}
    updated = store.replace_proposal(iid, output, report, capture, {"verifier_corrections": note}, NAME,
                                     expect=identity, unless_in_use=True, by=NAME, action="verifier_corrected",
                                     version=proposal_version(inv.get("ai_output"), inv.get("validation")))  # fmt: skip
    return message if updated else ""


def correct(settings: Any, store: Any, inv: dict[str, Any], coder: Any, images: list[Any], *, rounds: int,
            seconds_left: Callable[[], float], identity: Any, model: str,
            names: dict[str, str] | None = None) -> dict[str, Any] | None:  # fmt: skip
    """Ask the AI reviewer to correct an invoice with a problem, and apply what the page and the math confirm. What
    was asked and what came of it ({"problems", "applied", "refused", "message", "rounds", ...}); None when nothing
    was asked (no problem, a person's coding on it, parked, open or drafted: never applied then, text addressed to
    software on it, no page text). Never raises."""
    try:
        return _correct(settings, store, inv, coder, images, rounds, seconds_left, identity, model, names)
    except Exception as exc:  # noqa: BLE001 - the corrections are an extra: the review goes on
        log.warning("%s: invoice %s not corrected (%s: %s)", NAME, inv.get("id"), type(exc).__name__, str(exc)[:200])
        return None


def _correct(settings: Any, store: Any, inv: dict[str, Any], coder: Any, images: list[Any], rounds: int,
             seconds_left: Callable[[], float], identity: Any, model: str,
             names: dict[str, str] | None) -> dict[str, Any] | None:  # fmt: skip
    from ..capture.workflow import has_draft
    from ..core.store import REVIEW

    iid = int(inv["id"])
    output = inv.get("ai_output") or {}
    if inv.get("final_output") or inv.get("reviewer") or inv.get("edits"):  # a person's coding: theirs to correct
        return None
    if inv.get("status") != REVIEW or store.open_by(iid) or has_draft(store, iid):  # (``replace_proposal`` refuses)
        return None
    capture = _capture(store, iid)
    wrong = [] if addressed_to_software(inv, capture) else problems(store, inv, output, capture)
    text = page_text(settings, inv) if wrong else ""
    if not wrong or not text.strip():
        return None
    setup = store.reference_data().tax
    supplier_wanted = supplier_unsure(store, inv, capture)
    amounts_wanted = bool(_math_wrong(output, None)) or any(i.get("code") in PROBLEM_CODES - SUPPLIER_CODES
                                                            for i in _issues(inv))  # fmt: skip
    shown = proposal(output, vendor_record(store, output), names)
    outcome: dict[str, Any] = {"problems": wrong, "applied": [], "refused": [], "rounds": 0}
    earlier: dict[str, Any] | None = None
    try:
        ask = ask_model(coder, images, seconds_left)
    except Exception as exc:  # noqa: BLE001 - the chat model isn't there: the review says so
        outcome["why"] = str(exc)[:200]
        return outcome
    for n in range(rounds):
        if n and seconds_left() < MIN_CALL_SECONDS:
            outcome["why"] = "out of time"
            break
        outcome["rounds"] += 1
        try:
            data = ask(question(shown, wrong, text, earlier))
        except Exception as exc:  # noqa: BLE001 - no correction: the review goes on as before
            outcome["why"] = f"no usable answer ({str(exc)[:160]})"
            if getattr(exc, "temporary", False):
                break
            continue
        items = [i for i in data.get("corrections") or [] if isinstance(i, dict) and i.get("kind") in KINDS]
        outcome["problem"] = problem = str(data.get("problem") or "")[:400]
        if data.get("confident") is not True:
            earlier = {"corrections": items, "not applied": ["you were not sure"]}
            continue
        if not items:
            outcome["why"] = "nothing to correct"
            break
        if not amounts_wanted:
            kept = [i for i in items if i["kind"] == "supplier"]
            refused = [f"{_said(i)}: the amounts already add up" for i in items if i["kind"] != "supplier"]
            items = kept
        else:
            refused = []
        corrected, said, not_taken = apply_items(output, items, text, store=store, setup=setup,
                                                 supplier_wanted=supplier_wanted)  # fmt: skip
        refused += not_taken
        by_supplier = corrected.get("vendor_name") != output.get("vendor_name")
        found = (checked(settings, store, inv, corrected, supplier=by_supplier) if said
                 else "nothing in them could be confirmed")  # fmt: skip
        if isinstance(found, str):
            refused.append(found)
        outcome["refused"] = refused
        if not isinstance(found, str):
            was, now = output.get("company") or "", found[0].get("company") or ""
            if was != now:  # (the supplier corrected: its company decided again)
                said.append(f"company {now or 'not decided'} (was {was or 'not decided'}), for the supplier")
            message = _store(store, inv, *found, said, problem, identity, model)
            if message:
                outcome.update(applied=said, message=message)
            else:
                outcome["why"] = "not applied: a person has it open or worked on it, or it changed meanwhile"
            break
        earlier = {"corrections": items, "not applied": refused}
    log.info("%s: invoice %s %s", NAME, iid, f"corrected ({'; '.join(outcome['applied'])})" if outcome["applied"]
             else f"not corrected ({'; '.join(outcome['refused']) or outcome.get('why', '')})")  # fmt: skip
    return outcome
