"""The AI reviewer: the local chat model (Gemma 4, Qwen 3.5) checks a whole invoice against its pages, in the
background.

Every reader so far reads fields; nothing looked at the invoice as a whole the way an AP clerk does before posting it.
The AI reviewer is given the page images and everything AP Coder proposes (the header, the lines with their GL
accounts, the taxes and totals, the vendor from the vendor list, the math worked out again) and asked, with its
thinking on, whether each of it is right. It answers per field and per line, and whether it is confident.

It is asked again until it is sure: the fields it disputed (or all of it, when it was not confident) go back to it
with its own reasons, up to ``rounds`` times and ``minutes`` in all (Settings → Reading). Then:

* **agreed**: it found nothing wrong and is confident. An invoice touchless processing had held for it is approved.
* **disagrees**: it read a field differently twice in a row, confident: the field is marked Check and the warning
  VERIFIER_DISAGREES says what it read.
* **unsure**: out of rounds or time without being confident: the warning VERIFIER_UNSURE sends it to a person.

Like the Azure check (azure_check.py), an invoice waits in line (``verifications``), is never approved without a
person until the reviewer agrees (with the reviewer on), and the reviewer runs between the page reader's invoices, in
the same background thread, so the two never load LM Studio at once. It never changes a value: a person decides.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import json
import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..coding.line_tax import tax_code_of

log = logging.getLogger(__name__)

SETTING = "verifier"  # Settings → Reading, as JSON: {"on", "rounds", "minutes"}
DEFAULT_ROUNDS, MAX_ROUNDS = 3, 6
DEFAULT_MINUTES, MAX_MINUTES = 5.0, 30.0
MAX_PAGES = 3  # page images sent: the header, the lines, the totals
MAX_TRIES = 3  # reviews the model server cut off before any answer, before an invoice leaves the line as failed
MAX_LINES = 60  # an invoice with more lines than this is not reviewed (it would not fit the model): a person checks it
DESCRIPTION_CHARS = 160  # of each line's description, as it is shown
NAME = "AI reviewer"
READER = "verifier"  # its name among the readers (a field's sources)
AGREED, DISAGREES, UNSURE = "agreed", "disagrees", "unsure"
DISAGREES_CODE, UNSURE_CODE = "VERIFIER_DISAGREES", "VERIFIER_UNSURE"
WAITING_FOR_REVIEWER = f"waiting for the {NAME}: approved once it agrees"
WARNING = "warning"

# What it checks, with the names a clerk knows them by.
FIELDS: dict[str, str] = {
    "vendor_name": "vendor", "invoice_number": "invoice number", "invoice_date": "invoice date",
    "due_date": "due date", "po_number": "PO number", "currency": "currency",
    "gst_hst_registration_number": "GST/HST number", "qst_registration_number": "QST number",
    "pst_registration_number": "PST number", "subtotal": "subtotal", "tax_total": "total tax",
    "grand_total": "total",
}  # fmt: skip
AMOUNTS = ("subtotal", "tax_total", "grand_total")

SYSTEM = (
    "You are a careful accounts-payable reviewer at a Canadian company. Before an invoice is posted to JD Edwards, you "
    "check what was entered for it against the invoice's pages (the images). Think it through step by step before you "
    "answer.\n"
    "For each field you are asked about: look for it on the pages and say whether the value entered is what the "
    "invoice says (agree true or false). A date, an amount or a number printed another way is the same value "
    '("Oct 1, 2026" is 2026-10-01; "$1,234.50" is 1234.5; "123 456 789 RT 0001" is 123456789RT0001). When you '
    "disagree, write in on_page the value as the invoice shows it; when the field is not on the pages, agree only if "
    "the value entered is empty. vendor is the company that issued the invoice, never the customer it is billed or "
    "shipped to.\n"
    "For each line you are asked about: say whether its description and amount are on the pages as entered, and "
    "whether its GL account plausibly fits what was bought (agree false if not, with the reason).\n"
    "Check the arithmetic and Canadian sales tax: the lines add up to the subtotal; each tax is its rate on the lines "
    "it applies to (PST may apply to some lines only; QST is worked out on the price before GST); subtotal plus taxes "
    "equals the total. Sales tax follows where the goods are delivered to the buyer, not where the vendor is.\n"
    "When lessons are given (corrections AP made on this vendor's earlier invoices, with AP's reasons), apply them: "
    "they say how AP reads this vendor's invoices.\n"
    "Set confident true only when you checked every item you were asked about on the pages and are sure of each "
    "answer. Never guess: when you cannot read something, say so in the reason and set confident false. "
    "Answer with JSON only."
)


def schema(fields: list[str]) -> dict[str, Any]:
    """The answer's JSON schema: an entry per field asked about (``fields``) and per line, then confident and a
    summary."""
    field = {"type": "object", "additionalProperties": False, "required": ["field", "agree", "on_page", "reason"],
             "properties": {"field": {"type": "string", "enum": fields or list(FIELDS)}, "agree": {"type": "boolean"},
                            "on_page": {"type": ["string", "null"]}, "reason": {"type": "string"}}}  # fmt: skip
    line = {"type": "object", "additionalProperties": False, "required": ["line_number", "agree", "reason"],
            "properties": {"line_number": {"type": "integer"}, "agree": {"type": "boolean"},
                           "reason": {"type": "string"}}}  # fmt: skip
    return {"type": "object", "additionalProperties": False, "required": ["fields", "lines", "confident", "summary"],
            "properties": {"fields": {"type": "array", "items": field}, "lines": {"type": "array", "items": line},
                           "confident": {"type": "boolean"}, "summary": {"type": "string"}}}  # fmt: skip


# --- Its settings -------------------------------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class ReviewerSettings:
    on: bool = True
    rounds: int = DEFAULT_ROUNDS
    minutes: float = DEFAULT_MINUTES
    # Which invoices it reviews (D-111): "all" (the laptop's default), or "touchless", only those AP Coder could
    # approve without a person (the company server's default: the rest go to a person anyway, and the chat model's
    # time is the server's scarce resource). Either way none is approved without a person until it agrees.
    scope: str = "all"


SCOPES = ("all", "touchless")


def default_scope() -> str:
    from ..ops.offline import server_mode

    return "touchless" if server_mode() else "all"


def load_settings(store: Any) -> ReviewerSettings:
    """Settings → Reading (on by default: 3 rounds, 5 minutes an invoice)."""
    try:
        data = json.loads(store.get_setting(SETTING) or "{}")
    except (ValueError, TypeError, AttributeError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    try:
        rounds = min(MAX_ROUNDS, max(1, int(data.get("rounds", DEFAULT_ROUNDS))))
        minutes = min(MAX_MINUTES, max(0.5, float(data.get("minutes", DEFAULT_MINUTES))))
    except (TypeError, ValueError):
        rounds, minutes = DEFAULT_ROUNDS, DEFAULT_MINUTES
    scope = data.get("scope") if data.get("scope") in SCOPES else default_scope()
    return ReviewerSettings(on=bool(data.get("on", True)), rounds=rounds, minutes=minutes, scope=scope)


def save_settings(store: Any, *, on: bool, rounds: int, minutes: float, actor: str | None = None,
                  scope: str | None = None) -> ReviewerSettings:  # fmt: skip
    cfg = ReviewerSettings(on=bool(on), rounds=min(MAX_ROUNDS, max(1, int(rounds))),
                           minutes=min(MAX_MINUTES, max(0.5, float(minutes))),
                           scope=scope if scope in SCOPES else load_settings(store).scope)  # fmt: skip
    store.set_setting(SETTING, json.dumps(dataclasses.asdict(cfg)), actor=actor)
    return cfg


# --- What it is shown -------------------------------------------------------------------------------------------


def _shown(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 2)
    return value if value not in (None, "") else ""


def proposal(output: dict[str, Any], vendor: dict[str, Any] | None, gl_names: dict[str, str] | None = None
             ) -> dict[str, Any]:  # fmt: skip
    """Everything AP Coder proposes for the invoice, as the reviewer is shown it."""
    gl_names = gl_names or {}
    out: dict[str, Any] = {"fields": {name: _shown(output.get(name)) for name in FIELDS}}
    out["lines"] = [
        {"line_number": li.get("line_number"), "description": str(li.get("description") or "")[:DESCRIPTION_CHARS],
         "quantity": li.get("quantity"), "unit_price": li.get("unit_price"), "amount": _shown(li.get("amount")),
         "taxes": list(li.get("taxes_applied") or []), "tax_code": tax_code_of(li),
         "gl_account": f"{li.get('predicted_gl_code') or ''} {gl_names.get(str(li.get('predicted_gl_code')), '')}"
         .strip()}
        for li in output.get("line_items") or []
    ]  # fmt: skip
    out["taxes"] = [{"tax": t.get("tax_type"), "province": t.get("province") or "", "rate": t.get("rate"),
                     "on": _shown(t.get("taxable_amount")), "amount": _shown(t.get("tax_amount"))}
                    for t in output.get("tax_lines") or []]  # fmt: skip
    # the ship-to printed, else the place of supply worked out from where it is delivered to us (pipeline)
    out["place_of_supply"] = output.get("ship_to_province") or output.get("place_of_supply") or ""
    if vendor:
        out["vendor_list"] = {k: v for k, v in {
            "name": vendor.get("display_name"), "erp_id": vendor.get("erp_id"), "gst_hst": vendor.get("expected_gst"),
            "qst": vendor.get("qst_number"), "pst": vendor.get("pst_number"), "province": vendor.get("province"),
        }.items() if v}  # fmt: skip
    try:
        from ..coding.taxmath import math_check
        from ..core.schema import InvoiceCoding

        # A stored proposal carries its GL distribution too, which InvoiceCoding doesn't take.
        coding = InvoiceCoding.model_validate({k: v for k, v in output.items() if k != "gl_distribution"})
        out["math"] = [f"{'OK' if r['ok'] else 'WRONG'}: {r['what']}: {r['detail']}"
                       for r in math_check(coding)["rows"]]  # fmt: skip
    except Exception as exc:  # noqa: BLE001 - the math is a help, never a reason not to ask
        log.warning("%s: the math is not shown (%s: %s)", NAME, type(exc).__name__, str(exc)[:200])
    return out


def digest(output: dict[str, Any]) -> str:
    """Which proposal a review was of: a new reading (the page reader's) is reviewed again."""
    keep = {k: output.get(k) for k in (*FIELDS, "line_items", "tax_lines", "ship_to_province")}
    return hashlib.sha256(json.dumps(keep, sort_keys=True, default=str).encode()).hexdigest()[:16]


def question(shown: dict[str, Any], fields: list[str], lines: list[int], earlier: dict[str, Any] | None) -> str:
    """The user message's text: what to check (all of it, or what it disputed), and its own earlier answers."""
    ask = {"fields": {f: shown["fields"].get(f, "") for f in fields},
           "lines": [li for li in shown["lines"] if li.get("line_number") in lines]}  # fmt: skip
    context = {k: v for k, v in shown.items() if k not in ("fields", "lines")}
    text = ["Check this invoice against its pages (the images below).",
            "What was entered, to check:", json.dumps(ask, ensure_ascii=False, indent=1),
            "For reference (the taxes, the vendor list's record of the vendor, the math worked out again, and AP's "
            "lessons for this vendor):",
            json.dumps(context, ensure_ascii=False, indent=1)]  # fmt: skip
    if earlier:
        text += ["You checked this invoice before and were not sure of everything. Your earlier answers on these "
                 "items, with your reasons:", json.dumps(earlier, ensure_ascii=False, indent=1),
                 "Look at the pages again, carefully, item by item. Change an answer only if you now see it "
                 "differently; keep it if you are sure."]  # fmt: skip
    return "\n".join(text)


# --- Asking it until it is sure -------------------------------------------------------------------------------


@dataclasses.dataclass
class Review:
    status: str  # agreed | disagrees | unsure
    fields: dict[str, dict[str, Any]]  # disputed: {field: {"value", "on_page", "reason"}}
    lines: dict[int, str]  # disputed lines: {line_number: reason}
    rounds: int
    seconds: float
    summary: str = ""
    why: str = ""  # unsure: out of rounds, out of time, or a reply it couldn't give

    def to_dict(self) -> dict[str, Any]:
        d = dataclasses.asdict(self)
        d["lines"] = {str(k): v for k, v in self.lines.items()}
        return d


def _agreed(data: dict[str, Any], fields: list[str], lines: list[int]) -> tuple[set[str], set[int]]:
    """The fields and lines asked about that one answer says, in so many words, are right (agree true)."""
    got_f = {str(i["field"]) for i in data.get("fields") or []
             if isinstance(i, dict) and i.get("field") in fields and i.get("agree") is True}  # fmt: skip
    got_l: set[int] = set()
    for item in data.get("lines") or []:
        try:
            if item.get("agree") is True and int(item.get("line_number")) in lines:
                got_l.add(int(item.get("line_number")))
        except (TypeError, ValueError, AttributeError):
            continue
    return got_f, got_l


def _parse(data: dict[str, Any], fields: list[str], lines: list[int]) -> tuple[dict[str, dict], dict[int, str], bool,
                                                                                  str]:  # fmt: skip
    """(disputed fields, disputed lines, confident, summary) from one answer. An item asked about but not answered is
    not confirmed: the answer is not confident."""
    answered_f: dict[str, dict] = {}
    for item in data.get("fields") or []:
        if isinstance(item, dict) and item.get("field") in fields:
            answered_f[str(item["field"])] = item
    answered_l: dict[int, dict] = {}
    for item in data.get("lines") or []:
        try:
            n = int(item.get("line_number"))
        except (TypeError, ValueError, AttributeError):
            continue
        if n in lines:
            answered_l[n] = item
    # Only a true that is a boolean (as for "agree"): in json_object or text mode "false" can come back as a string.
    confident = data.get("confident") is True and set(answered_f) >= set(fields) and set(answered_l) >= set(lines)
    disputed = {f: {"on_page": (str(a.get("on_page")) if a.get("on_page") not in (None, "") else ""),
                    "reason": str(a.get("reason") or "")[:300]}
                for f, a in answered_f.items() if a.get("agree") is False}  # fmt: skip
    lines_out = {n: str(a.get("reason") or "")[:300] for n, a in answered_l.items() if a.get("agree") is False}
    return disputed, lines_out, confident, str(data.get("summary") or "")[:400]


def review(ask: Callable[[str, list[str], list[int]], dict[str, Any]], shown: dict[str, Any], *,
           rounds: int = DEFAULT_ROUNDS, seconds: float = DEFAULT_MINUTES * 60,
           clock: Callable[[], float] = time.monotonic) -> Review:  # fmt: skip
    """Ask (``ask(text, fields, lines)`` returns the model's JSON) until it is sure, ``rounds`` times at most and
    ``seconds`` in all. Round 1 is everything; after a confident round the next is what it disputed, with its
    earlier answers; after a round it was not confident of, everything again (what it was unsure of, or never
    answered, is not dropped). A dispute it keeps on a second look while confident is a disagreement; one it drops is
    resolved. Agreed means every field and every line was answered "agree" in a round it was confident of. The model
    server failing on the first question (``temporary``: stopped, no answer in time) is raised: that is no review."""
    start = clock()
    all_fields = list(FIELDS)
    all_lines = [li["line_number"] for li in shown["lines"] if isinstance(li.get("line_number"), int)]
    fields, lines, earlier = all_fields, all_lines, None
    previous: tuple[set[str], set[int]] | None = None
    confirmed_f: set[str] = set()  # answered "agree" in a confident round, and not disputed since
    confirmed_l: set[int] = set()
    summary, why, done = "", "", 0
    disputed: dict[str, dict] = {}
    disputed_lines: dict[int, str] = {}
    if len(all_lines) > MAX_LINES:  # too long to be asked about in one go: never agreed, so not asked
        return Review(UNSURE, {}, {}, 0, 0.0, "", f"{len(all_lines)} lines: more than the {MAX_LINES} it checks")
    while done < rounds:
        if done and clock() - start >= seconds:
            why = f"out of time ({seconds / 60:.0f} min)"
            break
        try:
            data = ask(question(shown, fields, lines, earlier), fields, lines)
        except Exception as exc:  # noqa: BLE001 - a reply it couldn't give counts as not sure
            if not done and getattr(exc, "temporary", False):
                raise  # the server stopped or ran out of time before any answer: no review, the caller postpones it
            why = f"no usable answer ({str(exc)[:160]})"
            done += 1
            break
        done += 1
        got_f, got_l, confident, summary = _parse(data, fields, lines)
        # What it now says about the items asked; items not asked this round keep their earlier answer (none: they
        # were agreed).
        disputed = {f: v for f, v in {**disputed, **got_f}.items() if f in got_f or f not in fields}
        disputed_lines = {n: r for n, r in {**disputed_lines, **got_l}.items() if n in got_l or n not in lines}
        now = (set(disputed), set(disputed_lines))
        if confident:
            agreed_f, agreed_l = _agreed(data, fields, lines)
            confirmed_f |= agreed_f
            confirmed_l |= agreed_l
        confirmed_f -= set(got_f)
        confirmed_l -= set(got_l)
        everything = confirmed_f >= set(all_fields) and confirmed_l >= set(all_lines)
        if confident and not disputed and not disputed_lines and everything:
            return Review(AGREED, {}, {}, done, round(clock() - start, 1), summary)
        if (confident and previous is not None and (now[0] or now[1]) and now[0] <= previous[0]
                and now[1] <= previous[1]):  # fmt: skip
            # It stands by what it disputed, on a second look: a disagreement, for a person to settle.
            return Review(DISAGREES, disputed, disputed_lines, done, round(clock() - start, 1), summary)
        previous = now
        # Only a confident round narrows the next to what it disputed; otherwise all of it is asked again.
        narrow = confident and (disputed or disputed_lines)
        fields = sorted(disputed) if narrow else all_fields
        lines = sorted(disputed_lines) if narrow else all_lines
        earlier = {"fields": {f: {"agree": False, **v} for f, v in disputed.items()},
                   "lines": {str(n): r for n, r in disputed_lines.items()}, "summary": summary,
                   "confident": confident}  # fmt: skip
    if not why:
        why = f"not sure after {done} round{'s' if done != 1 else ''}"
    return Review(UNSURE, disputed, disputed_lines, done, round(clock() - start, 1), summary, why)


MIN_CALL_SECONDS = 10.0  # a call is never given less, however little of the budget is left


def ask_model(coder: Any, images: list[Any], *, seconds_left: Callable[[], float] | None = None
              ) -> Callable[[str, list[str], list[int]], dict[str, Any]]:  # fmt: skip
    """``ask`` for ``review``: one call to the local chat model with the page images, thinking on. ``seconds_left``:
    what is left of the reviewer's time for the invoice, the most each call may take (not AP_LLM_TIMEOUT_SECONDS)."""
    from . import local_llm
    from .inference import CodingError

    model = coder._prepare_local()

    def ask(text: str, fields: list[str], lines: list[int]) -> dict[str, Any]:
        content: list[dict[str, Any]] = [{"type": "text", "text": text}]
        content += [{"type": "image_url", "image_url": {"url": img.to_data_url(), "detail": "high"}} for img in images]
        messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": content}]
        timeout = max(MIN_CALL_SECONDS, float(seconds_left())) if seconds_left is not None else None
        response = coder._local_create(model, messages, schema(fields), thinking=True, timeout=timeout)
        # Strict: a draft in its thinking is not its answer (an answer that can approve an invoice).
        reply = local_llm.read_reply(response.choices[0], response_limit(coder), strict=True)
        if reply.data is None:
            raise CodingError(reply.problem)
        return reply.data

    return ask


def response_limit(coder: Any) -> int:
    from .inference import THINKING_TOKENS

    return max(int(coder.settings.llm.max_output_tokens or 0), THINKING_TOKENS)


# --- What it means for the invoice --------------------------------------------------------------------------------


def issues(result: dict[str, Any] | None, output: dict[str, Any] | None = None) -> list[tuple[str, str, str]]:
    """(severity, code, message) for the review screen from a review's result. With ``output`` (what is on screen
    now): a field it disputed that the reviewer has since changed is no longer reported."""
    if not result:
        return []
    status = result.get("status")
    fields = dict(result.get("fields") or {})
    if output is not None:
        from ..capture.normalize import normalize_value

        fields = {f: v for f, v in fields.items()
                  if normalize_value(f, output.get(f)) == normalize_value(f, v.get("value"))}  # fmt: skip
    lines = result.get("lines") or {}
    parts = [f"{FIELDS.get(f, f)}: it reads {v.get('on_page') or 'something else'} on the page"
             f"{' (' + v['reason'] + ')' if v.get('reason') else ''}" for f, v in fields.items()]  # fmt: skip
    parts += [f"line {n}: {reason or 'not as entered'}" for n, reason in lines.items()]
    if status == DISAGREES and parts:
        return [(WARNING, "VERIFIER_DISAGREES", f"the {NAME} disagrees: " + "; ".join(parts)[:900])]
    if status == UNSURE:
        what = "; ".join(parts) if parts else "it could not confirm the invoice"
        return [
            (WARNING, "VERIFIER_UNSURE", f"the {NAME} is not sure ({result.get('why') or 'not sure'}): {what}"[:900])
        ]
    return []


def mark_capture(capture: Any, result: dict[str, Any]) -> list[str]:
    """The fields it disputed are Check in the capture, with what it read as a reader of its own. Returns them."""
    from ..capture.types import CHECK

    marked = []
    for name, v in (result.get("fields") or {}).items():
        field = (getattr(capture, "fields", None) or {}).get(name)
        if field is None:
            continue
        field.status = CHECK
        seen = str(v.get("on_page") or "")
        field.sources = {**(field.sources or {}), (f"other:{seen}" if seen else READER): READER}
        note = f"{NAME} reads {seen or 'it differently'}" + (f": {v['reason']}" if v.get("reason") else "")
        field.reasons = [*(field.reasons or []), note[:200]]
        marked.append(name)
    return marked


# --- In line, in the background ------------------------------------------------------------------------------------


@dataclasses.dataclass
class Plan:
    """What the reviewer means for an invoice being processed: put in line, and held from touchless approval."""

    queue: bool = False
    hold: bool = False


def prepare(store: Any, autonomy: dict[str, Any] | None = None) -> Plan:
    """Before the touchless decision: with the reviewer on, every invoice is put in line and waits for it; with its
    scope "touchless" (D-111), only one touchless processing passed (``autonomy``), which waits for it; the others
    go to a person anyway (one read again by the page reader is asked again then). Never raises."""
    if store is None:
        return Plan()
    try:
        cfg = load_settings(store)
    except Exception:  # noqa: BLE001
        return Plan()
    wanted = cfg.scope != "touchless" or bool((autonomy or {}).get("auto")) or held(autonomy or {})
    return Plan(queue=cfg.on and wanted, hold=cfg.on)


HOLDS = ("azure_hold", "verifier_hold")  # what an invoice touchless processing passed may still be waiting for


def held(autonomy: dict[str, Any]) -> bool:
    """Touchless processing passed it, but it is held back for a check (the Azure check, the AI reviewer)."""
    return any(autonomy.get(k) for k in HOLDS)


def hold(autonomy: dict[str, Any], plan: Plan) -> dict[str, Any]:
    """The touchless decision, held back until the reviewer agrees: recorded whether or not another check (the
    Azure check) is already holding it, so neither approves it alone."""
    if plan.hold and (autonomy.get("auto") or held(autonomy)):
        reason = WAITING_FOR_REVIEWER if autonomy.get("auto") else f"{autonomy.get('reason')}; {WAITING_FOR_REVIEWER}"
        return {**autonomy, "auto": False, "audit": False, "verifier_hold": True, "reason": reason}
    return autonomy


def queue(store: Any, invoice_id: int | None, plan: Plan, held: bool = False) -> bool:
    if store is None or not invoice_id or not plan.queue:
        return False
    try:
        return bool(store.queue_verification(invoice_id, "held for touchless" if held else "every invoice"))
    except Exception as exc:  # noqa: BLE001 - the queue is an extra
        log.warning("invoice %s: not queued for the %s (%s)", invoice_id, NAME, type(exc).__name__)
        return False


@dataclasses.dataclass
class Outcome:
    invoice_id: int
    status: str  # agreed | disagrees | unsure | failed | skipped | postponed
    message: str = ""
    approved: bool = False
    rounds: int = 0
    seconds: float = 0.0


def verify_one(settings: Any, store: Any, *, coder: Any = None, clock: Callable[[], float] = time.monotonic,
               after_page_reader: bool = True, reader: str = "") -> Outcome | None:  # fmt: skip
    """Review the next invoice in line. None when the reviewer is off or nothing waits. The chat model not
    answering leaves the invoice in line (postponed). ``reader``: who marks it (``page_worker.READER_ID``, or one
    of this process's reader threads with ``llm_parallel``)."""
    if store.restoring():
        return None
    cfg = load_settings(store)
    if not cfg.on:
        return None
    from .page_worker import READER_ID, reading_as

    with reading_as(reader or READER_ID) as mark:
        row = store.next_verification(after_page_reader=after_page_reader, reader=mark)
        if not row:
            return None
        iid = int(row["invoice_id"])
        try:
            return _verify(settings, store, row, cfg, coder, clock)
        except Exception as exc:  # noqa: BLE001 - one invoice must never stop the queue
            log.warning("%s: invoice %s failed (%s)", NAME, iid, exc)
            _end(store, row, "failed", error=f"{type(exc).__name__}: {str(exc)[:300]}")
            return Outcome(iid, "failed", str(exc)[:300])


def _end(store: Any, row: dict[str, Any], status: str, **how: Any) -> bool:
    """End the review ``row`` (from ``next_verification``) as this process took it: one another process has taken
    since is left to that one (B-787)."""
    return store.finish_verification(int(row["invoice_id"]), status, reader=row.get("reader"), **how)


def _coder(settings: Any, store: Any) -> Any:
    from .inference import InvoiceCoder
    from .page_worker import reference_data

    return InvoiceCoder(settings, reference_data(store), provider="local")


def _verify(settings: Any, store: Any, row: dict[str, Any], cfg: ReviewerSettings, coder: Any,
            clock: Callable[[], float]) -> Outcome:  # fmt: skip
    from ..core.memory import vendor_key
    from ..core.store import PARKED, REVIEW
    from .imaging import render_page_images
    from .inference import CodingError

    iid = int(row["invoice_id"])
    inv = store.get_invoice(iid)
    identity = store.row_identity(iid)
    path = Path((inv or {}).get("source_path") or "")
    if inv is None or not path.is_file():
        _end(store, row, "skipped", error="the invoice or its file is gone")
        return Outcome(iid, "skipped", "the invoice or its file is gone")
    if inv.get("status") not in (REVIEW, PARKED):
        _end(store, row, "skipped", error="already dealt with")
        return Outcome(iid, "skipped", "already dealt with")
    output = inv.get("final_output") or inv.get("ai_output") or {}
    if not output:
        _end(store, row, "skipped", error="nothing proposed to review")
        return Outcome(iid, "skipped", "nothing proposed to review")
    try:
        coder = coder or _coder(settings, store)
        model = coder._prepare_local()
        if not coder.profile.supports_vision:
            raise CodingError(f"{model} can't see pages: load a model that can (Gemma 4 12B)")
        images = render_page_images(path, MAX_PAGES)
    except CodingError as exc:  # not loaded, can't see: it waits in line
        _end(store, row, "waiting", error=str(exc)[:300])
        return Outcome(iid, "postponed", str(exc)[:300])
    if not images:
        _end(store, row, "skipped", error="no page to show (a text file)")
        return Outcome(iid, "skipped", "no page to show")
    vendor = store.get_vendor(vendor_key(output.get("vendor_name") or ""))
    try:
        names = {a["code"]: a["description"] for a in store.list_accounts("gl_accounts")}
    except Exception:  # noqa: BLE001
        names = {}
    shown = proposal(output, vendor, names)
    from . import lessons

    learned = lessons.prompt_lines(store, output.get("vendor_name") or "", lessons.kind_of(output), gl_names=names)
    if learned:
        shown["lessons"] = learned
    budget, started = cfg.minutes * 60, clock()
    ask = ask_model(coder, images, seconds_left=lambda: budget - (clock() - started))
    try:
        found = review(ask, shown, rounds=cfg.rounds, seconds=budget, clock=clock)
    except CodingError as exc:
        if not exc.temporary:
            raise
        if store.restoring() or store.row_identity(iid) != identity:
            return Outcome(iid, "skipped", "not applied: a backup was restored meanwhile")
        return _postpone_or_fail(store, row, str(exc)[:300])
    fields = {f: {"value": shown["fields"].get(f), **v} for f, v in found.fields.items()}
    result = {**found.to_dict(), "fields": fields, "model": model, "proposal": digest(output),
              "at": dt.datetime.now().isoformat(timespec="seconds")}  # fmt: skip
    if store.restoring() or store.row_identity(iid) != identity:
        return Outcome(iid, "skipped", "not applied: a backup was restored meanwhile")
    _end(store, row, "done", result=result, rounds=found.rounds, seconds=found.seconds)
    approved = _fold_in(store, inv, result, expect=identity)
    store.log_event("verified", iid, actor="AP Coder", detail={
        "status": found.status, "rounds": found.rounds, "seconds": found.seconds, "fields": sorted(found.fields),
        "lines": sorted(found.lines)})  # fmt: skip
    return Outcome(iid, found.status, found.summary or found.why, approved, found.rounds, found.seconds)


def _postpone_or_fail(store: Any, row: dict[str, Any], problem: str) -> Outcome:
    """The model server stopped, or gave no answer in time, before the reviewer answered at all: that is the server's
    trouble, not the reviewer's verdict. Back in line (still held), or failed once it happened MAX_TRIES times."""
    iid = int(row["invoice_id"])
    tries = int(row.get("tries") or 0) + 1
    if tries >= MAX_TRIES:
        message = (f"the chat model didn't answer {tries} times ({problem}); have it check again from the review "
                   "screen once LM Studio is running with the model loaded")  # fmt: skip
        _end(store, row, "failed", error=message, tries=tries)
        return Outcome(iid, "failed", message)
    message = f"{problem}: reviewed again later (try {tries} of {MAX_TRIES})"
    _end(store, row, "waiting", error=message, tries=tries)
    return Outcome(iid, "postponed", message)


def _fold_in(store: Any, inv: dict[str, Any], result: dict[str, Any], *, expect: Any = None) -> bool:
    """The review into the invoice: its issue, the fields it disputed marked Check, a note; an invoice held for it
    and agreed is approved as touchless processing would have. Returns whether it was approved now."""
    from ..capture.types import CaptureResult
    from ..capture.workflow import AUTONOMOUS_REVIEWER, still_touchless
    from ..core.store import REVIEW

    iid = int(inv["id"])
    found = issues(result)
    capture = None
    stored = store.get_capture(iid)
    if stored:
        try:
            capture = stored if isinstance(stored, CaptureResult) else CaptureResult.from_dict(stored)
            mark_capture(capture, result)
        except Exception:  # noqa: BLE001 - an older capture: the issue is enough
            capture = None
    meta = dict(inv.get("meta") or {})
    notes = dict(meta.get("capture") or {})
    held = bool(notes.pop("verifier_hold", False))
    note = {k: result.get(k) for k in ("status", "rounds", "seconds", "summary", "why", "at", "model")}
    issue = None
    if found:
        severity, code, message = found[0]
        issue = {"severity": severity, "code": code, "message": message, "line_number": None}
    folded = store.update_after_check(iid, capture.to_dict() if capture is not None else None, issue,
                                      (DISAGREES_CODE, UNSURE_CODE), {"verifier": note, "capture": notes},
                                      expect=expect)  # fmt: skip
    untouched = inv.get("status") == REVIEW and not (inv.get("final_output") or inv.get("reviewer") or inv.get("edits"))
    output = inv.get("ai_output")
    if folded and held and untouched and result.get("status") == AGREED and output:
        # Read again first: another check may still hold it, or a person may have approved it with corrections and
        # reopened it while the reviewer looked.
        if still_touchless(store, capture, output, invoice_id=iid):
            try:
                store.approve_invoice(iid, output, AUTONOMOUS_REVIEWER, login="ap-coder")
                return True
            except (KeyError, ValueError) as exc:
                log.info("invoice %s: not approved after the %s (%s)", iid, NAME, exc)
    return False
