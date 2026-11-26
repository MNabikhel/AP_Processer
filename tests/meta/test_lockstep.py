"""Lists that must change together. An event that changes an invoice under an open review screen is a revision
(``REVISION_ACTIONS``, D-184), or a screen open on a laptop (which keeps nothing of who has it open) is never told and
its next check saves what it showed over the change: the same mistake twice, B-2805 and B-2985. Each rule has a
self-test showing it finds what it should."""

from __future__ import annotations

import ast
import csv
import functools
import itertools
import re
from collections.abc import Callable, Iterable
from types import SimpleNamespace

from ap_coder.ai.offline_coder import _OFFICIAL
from ap_coder.ai.pipeline import CUT_OFF
from ap_coder.capture import DI_TAX_RATES
from ap_coder.capture.reader.totals import _RATES
from ap_coder.coding.bulk import AFTER_PROCESSING
from ap_coder.coding.line_tax import learned_province
from ap_coder.coding.tax import OUTSIDE_CANADA, PROVINCES
from ap_coder.coding.terms import parse_terms, payment
from ap_coder.coding.validation import currency_not_found
from ap_coder.erp.insights import _may_discount
from tests.conftest import ROOT
from tests.ops.test_help import codes_raised_in

# Events a store function that changes an invoice logs which are not a revision: why a review screen drawn before one
# is still right.
NOT_REVISIONS = {
    "exported": "an approved invoice put in an export batch: its coding and where it stands are as they were",
    "export_undone": "its export batch undone: back in the ready-to-export list, its coding as it was",
    "failed": "a new invoice, one that could not be read: no screen of it was drawn before",
    "file_found": "only where its file is changed (source_path), not its coding or where it stands",
    "processed": "a new invoice: no screen of it was drawn before",
    "proposal_updated": "the page reader's reading (page_worker, without unless_in_use): only while nobody worked on "
    "it, and a screen drawn while it waits to be read is drawn again once it is, the edits kept as a draft (D-86, the "
    "poll in static/js/invoice.js). page_worker.chat_again logs it too, where no screen waits: an open question of "
    "B-2805's kind, for after Monday's test",
}
CHANGES_INVOICES = re.compile(
    r"\b(?:UPDATE|DELETE\s+FROM|(?:INSERT|REPLACE)(?:\s+OR\s+\w+)?\s+INTO)\s+invoices\b", re.I
)


@functools.cache
def _trees(folder: str, word: str = "") -> dict[str, ast.Module]:
    """Each module under ``folder`` (from the repository's root) whose text holds ``word``, parsed."""
    texts = {
        p.relative_to(ROOT).as_posix(): p.read_text(encoding="utf-8") for p in sorted((ROOT / folder).rglob("*.py"))
    }
    return {name: ast.parse(text) for name, text in texts.items() if word in text}


def _called(call: ast.Call) -> str:
    """The name a call calls (``store.replace_proposal(...)``: replace_proposal)."""
    return call.func.attr if isinstance(call.func, ast.Attribute) else getattr(call.func, "id", "")


def _calls(trees: dict[str, ast.Module]) -> list[tuple[str, ast.Call]]:
    return [(name, node) for name, tree in trees.items() for node in ast.walk(tree) if isinstance(node, ast.Call)]


Calls = Callable[
    [str], list[tuple[ast.Call, ast.FunctionDef | None]]
]  # a name's calls, each with the function it is in


def _calls_of(trees: dict[str, ast.Module]) -> Calls:
    """The calls of a name in these modules, each with the function it is in (None: at the top of its module)."""

    def calls(name: str) -> list[tuple[ast.Call, ast.FunctionDef | None]]:
        found = []
        for tree in trees.values():
            inside: dict[ast.Call, ast.FunctionDef] = {}
            for fn in ast.walk(tree):  # (outer functions first: a call in an inner one is the inner one's)
                if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    inside.update((n, fn) for n in ast.walk(fn) if isinstance(n, ast.Call))
            found += [(n, inside.get(n)) for n in ast.walk(tree) if isinstance(n, ast.Call) and _called(n) == name]
        return found

    return calls


def _calls_in_the_app(name: str) -> list[tuple[ast.Call, ast.FunctionDef | None]]:
    """Each call of ``name`` in ap_coder, with the function it is in."""
    return _calls_of(_trees("ap_coder", f"{name}("))(name)


def _literal(module: str, name: str):
    """The value of ``name = <a literal>`` at the top of ``module`` (a path from the root), read from its source as the
    rules read the rest."""
    tree = _trees(module.rsplit("/", 1)[0])[module]
    found = [
        n.value for n in tree.body if isinstance(n, ast.Assign) and any(getattr(t, "id", "") == name for t in n.targets)
    ]
    return ast.literal_eval(found[0])


def documented_order() -> tuple[str, ...]:
    """The JD Edwards modules in the order ``ap_coder/erp/__init__.py``'s docstring lists them (its paragraph that
    starts "JD Edwards E1"; what is in brackets says what a module holds)."""
    doc = ast.get_docstring(_trees("ap_coder/erp")["ap_coder/erp/__init__.py"]) or ""
    listed = next((part for part in doc.split("\n\n") if part.startswith("JD Edwards E1")), "")
    return tuple(re.findall(r"``(jde\w*)``", re.sub(r"\([^()]*\)", "", listed)))


# erp/jde*.py, lowest first, as erp/__init__.py documents them: each imports only the ones before it, at the top or
# inside a function (D-222; test_the_jd_edwards_modules_import_only_the_ones_below_them). The list at the top of
# erp/jde.py is another: jde_match before jde_zfiles, which it imports, and no jde_retainage.
ORDER = documented_order()


def _revisions() -> tuple[str, ...]:
    return _literal("ap_coder/core/store/invoices.py", "REVISION_ACTIONS")


# --- (a) A replace that waits for nobody to have the invoice open is a revision -------------------------------------


def replaces_not_revisions(trees: dict[str, ast.Module], revisions: tuple[str, ...]) -> list[str]:
    """Each ``replace_proposal(..., unless_in_use=True)`` whose ``action=`` is not a revision, or not said."""
    found = []
    for name, call in _calls(trees):
        said = {k.arg: k.value for k in call.keywords}
        guarded = said.get("unless_in_use")
        if _called(call) != "replace_proposal" or guarded is None:
            continue
        if isinstance(guarded, ast.Constant) and guarded.value is False:
            continue
        action = said.get("action")
        if not (isinstance(action, ast.Constant) and action.value in revisions):
            found.append(f"{name}:{call.lineno}: action={ast.unparse(action) if action else '(not said)'}")
    return found


def test_a_replace_while_nobody_has_the_invoice_open_is_a_revision():
    """B-2805, B-2985: the AI reviewer's corrections and Azure's reading again replaced the proposal while nobody had
    the invoice open, as far as the store knew, logged as ``proposal_updated``: a screen open on a laptop showed the
    values read before, and its next check saved them over the change."""
    assert replaces_not_revisions(_trees("ap_coder", "replace_proposal"), _revisions()) == []


def test_the_replace_rule_finds_a_replace_that_is_no_revision():
    source = """
store.replace_proposal(1, out, rep, cap, unless_in_use=True)
store.replace_proposal(2, out, rep, cap, unless_in_use=True, action="proposal_updated")
store.replace_proposal(3, out, rep, cap, unless_in_use=True, action=chosen)
store.replace_proposal(4, out, rep, cap, unless_in_use=True, action="verifier_corrected")
store.replace_proposal(5, out, rep, cap, expect=identity)
store.replace_proposal(6, out, rep, cap, unless_in_use=False)
"""
    found = replaces_not_revisions({"x.py": ast.parse(source)}, ("verifier_corrected",))
    assert found == ["x.py:2: action=(not said)", "x.py:3: action='proposal_updated'", "x.py:4: action=chosen"]


# --- (b) What a store function that changes an invoice logs is a revision, or says why not -------------------------


def _values(node: ast.expr | None, fn: ast.FunctionDef | None, calls_of: Calls, seen: frozenset = frozenset()
            ) -> set[str] | None:  # fmt: skip
    """The strings an argument of ``fn`` can be: a constant, either side of ``a if b else c``, a name assigned them in
    the function, or a parameter: its default and what each call of the function passes it, a name there being the
    caller's. A name met again (``action = ... else action``) adds nothing more. None: not told."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {node.value}
    if isinstance(node, ast.IfExp):
        both = (_values(node.body, fn, calls_of, seen), _values(node.orelse, fn, calls_of, seen))
        return None if None in both else both[0] | both[1]
    if not isinstance(node, ast.Name) or fn is None:
        return None
    if (fn, node.id) in seen:
        return set()
    seen = seen | {(fn, node.id)}
    sources: list[tuple[ast.expr | None, ast.FunctionDef | None]] = []
    for assign in ast.walk(fn):
        if isinstance(assign, ast.Assign) and any(isinstance(t, ast.Name) and t.id == node.id for t in assign.targets):
            sources.append((assign.value, fn))
    args = fn.args
    positional = [*args.posonlyargs, *args.args]
    defaults = dict(
        zip([a.arg for a in positional][len(positional) - len(args.defaults) :], args.defaults, strict=True)
    )
    defaults.update((a.arg, d) for a, d in zip(args.kwonlyargs, args.kw_defaults, strict=True) if d is not None)
    if node.id in defaults:
        sources.append((defaults[node.id], fn))
        sources += [(k.value, caller) for call, caller in calls_of(fn.name) for k in call.keywords if k.arg == node.id]
    elif node.id in {a.arg for a in [*positional, *args.kwonlyargs]}:
        return None  # (a parameter every caller must give: what it is, the callers say)
    if not sources:
        return None
    found = [_values(value, where, calls_of, seen) for value, where in sources]
    return None if None in found else set().union(*found)


def logged_by_invoice_changes(store: dict[str, ast.Module], calls_of: Calls) -> dict:
    """Each ``_log`` call of a store function whose SQL changes the invoices table: the actions it can log (None: not
    told), by where it is."""
    found: dict[str, set | None] = {}
    for name, tree in store.items():
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.FunctionDef):
                continue
            text = " ".join(n.value for n in ast.walk(fn) if isinstance(n, ast.Constant) and isinstance(n.value, str))
            if not CHANGES_INVOICES.search(text):
                continue
            for call in ast.walk(fn):
                if isinstance(call, ast.Call) and _called(call) == "_log":
                    said = {k.arg: k.value for k in call.keywords}
                    action = call.args[1] if len(call.args) > 1 else said.get("action")
                    found[f"{name}:{call.lineno} {fn.name}"] = _values(action, fn, calls_of)
    return found


def unexplained(logged: dict[str, set | None], revisions: tuple[str, ...], explained: dict[str, str]) -> list[str]:
    return [f"{where}: {sorted(actions) if actions is not None else 'which action, not told'}"
            for where, actions in logged.items()
            if actions is None or actions - set(revisions) - set(explained)]  # fmt: skip


def test_an_invoice_changed_in_the_store_is_a_revision_or_says_why_not():
    """Every event logged where the store changes an invoice is a revision (a screen drawn before it is out of date,
    says so, and saves nothing) or in NOT_REVISIONS with why a screen drawn before it is still right."""
    logged = logged_by_invoice_changes(_trees("ap_coder/core/store"), _calls_in_the_app)
    assert len(logged) >= 15  # (the rule reads the store's own: approve, park, reject, delete, replace...)
    assert unexplained(logged, _revisions(), NOT_REVISIONS) == []
    assert not set(NOT_REVISIONS) & set(_revisions())
    every = set().union(*(a for a in logged.values() if a))
    assert set(NOT_REVISIONS) <= every, "a reason for an event no longer logged there"


def test_the_store_rule_finds_an_event_with_no_reason_and_follows_names():
    source = """
class Mixin:
    def park(self, conn, invoice_id):
        conn.execute("UPDATE invoices SET status = 'parked' WHERE id = ?", (invoice_id,))
        self._log(conn, "parked_by_hand", invoice_id)

    def answer(self, conn, invoice_id, yes):
        action = "confirmed" if yes else "sent_back"
        conn.execute(f"UPDATE invoices SET status = {1} WHERE id = ?", (invoice_id,))
        self._log(conn, action, invoice_id)

    def replace(self, conn, invoice_id, *, action="proposal_updated"):
        conn.execute("UPDATE invoices SET ai_output = ? WHERE id = ?", ("{}", invoice_id))
        self._log(conn, action, invoice_id)

    def told(self, conn, invoice_id, what):
        conn.execute("DELETE FROM invoices WHERE id = ?", (invoice_id,))
        self._log(conn, what, invoice_id)

    def vendor(self, conn):
        conn.execute("UPDATE vendors SET hold = 1")
        self._log(conn, "vendor_updated")


store.replace(conn, 1, action="read_again")
"""
    tree = ast.parse(source)
    logged = logged_by_invoice_changes({"s.py": tree}, _calls_of({"s.py": tree}))
    assert logged == {"s.py:5 park": {"parked_by_hand"}, "s.py:10 answer": {"confirmed", "sent_back"},
                      "s.py:14 replace": {"proposal_updated", "read_again"}, "s.py:18 told": None}  # fmt: skip
    found = unexplained(logged, ("sent_back", "confirmed"), {"proposal_updated": "why"})
    assert found == ["s.py:5 park: ['parked_by_hand']", "s.py:14 replace: ['proposal_updated', 'read_again']",
                     "s.py:18 told: which action, not told"]  # fmt: skip


def test_the_store_rule_follows_a_callers_name_in_the_callers_own_function():
    """B-3007: a caller's name for the action (``action=action``, ``action=chosen``) is the caller's. It was looked up
    in the store function, so ``action=action`` went round its default and the caller for good (RecursionError) and
    ``action=chosen`` was not told, on a change that kept what the app does. A name given itself again adds nothing."""
    source = """
class Mixin:
    def replace(self, conn, invoice_id, *, action="proposal_updated"):
        conn.execute("UPDATE invoices SET ai_output = ? WHERE id = ?", ("{}", invoice_id))
        self._log(conn, action, invoice_id)

    def correct(self, conn, invoice_id, corrected, action="noted"):
        action = "verifier_corrected" if corrected else action
        conn.execute("UPDATE invoices SET status = 'review' WHERE id = ?", (invoice_id,))
        self._log(conn, action, invoice_id)


def chat_again(store):
    action = "proposal_updated"
    store.replace(conn, 1, action=action)


def corrections(store):
    chosen = "verifier_corrected"
    store.replace(conn, 2, action=chosen)
"""
    tree = ast.parse(source)
    logged = logged_by_invoice_changes({"s.py": tree}, _calls_of({"s.py": tree}))
    assert logged == {"s.py:5 replace": {"proposal_updated", "verifier_corrected"},
                      "s.py:10 correct": {"verifier_corrected", "noted"}}  # fmt: skip
    told_nothing = ast.parse(
        source.replace('    chosen = "verifier_corrected"\n', "") + "\nstore.replace(c, 3, action=x)\n"
    )
    logged = logged_by_invoice_changes({"s.py": told_nothing}, _calls_of({"s.py": told_nothing}))
    assert logged["s.py:5 replace"] is None  # (a name nobody gives a value: not told)


# --- (c) Each revision has its words on the screen that is out of date ----------------------------------------------


def unworded(words: dict[str, str], revisions: tuple[str, ...]) -> list[str]:
    """A revision with no words (the screen would say only "Changed"), and words for an event that is none."""
    return sorted(set(revisions) ^ set(words))


def test_a_screen_out_of_date_says_what_each_revision_was():
    """``invoice_drafts._CHANGES`` words each revision ("Approved by Ann Lee today at 10:41 a.m."); one left out
    would say "Changed" without a word in the tests."""
    words = _literal("ap_coder/web/pages/invoice_drafts.py", "_CHANGES")
    assert unworded(words, _revisions()) == []


def test_the_words_rule_finds_a_revision_with_no_words():
    assert unworded({"approved": "Approved", "noted": "Noted"}, ("approved", "azure_read_again")) == [
        "azure_read_again",
        "noted",
    ]


# --- (d) The JD Edwards modules import only the ones below them ----------------------------------------------------


def jde_imports(tree: ast.Module) -> set[str]:
    """The ``erp/jde*.py`` modules a module of ``ap_coder/erp`` imports, at the top or inside a function."""
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):  # (relative to ap_coder/erp)
            module = ".".join(p for p in ({1: "ap_coder.erp", 2: "ap_coder"}.get(node.level, ""), node.module) if p)
            names = [module, *(f"{module}.{a.name}" for a in node.names)]
        else:
            continue
        found |= {n.split(".")[2] for n in names if re.match(r"ap_coder\.erp\.jde\w*(\.|$)", n)}
    return found


def out_of_order(imports: dict[str, set[str]], order: tuple[str, ...]) -> list[str]:
    found = [f"{module}: not in ORDER" for module in sorted(imports) if module not in order]
    for module, names in sorted(imports.items()):
        for name in sorted(names):
            if module in order and (name not in order or order.index(name) >= order.index(module)):
                found.append(f"{module} imports {name}")
    return found


def test_the_jd_edwards_modules_import_only_the_ones_below_them():
    """D-222: one subject a module, each importing only the ones before it, so there is no cycle and a function has
    one home (``jde_settings.load``, not ``jde.load``). The order erp/__init__.py documents is one the imports keep, and
    names every module."""
    trees = _trees("ap_coder/erp")
    imports = {name.rsplit("/", 1)[1][:-3]: jde_imports(tree) for name, tree in trees.items() if "/jde" in name}
    assert set(ORDER) <= set(imports)  # (a module gone leaves ORDER)
    assert out_of_order(imports, ORDER) == []


def test_the_order_the_rule_keeps_is_the_one_erp_documents():
    """B-3011: there is one order of the JD Edwards modules, erp/__init__.py's (CLAUDE.md sends an agent there). The
    rule had its own, jde_retainage before jde_remarks where the docstring has them the other way round, so an import
    the docstring allows failed the test and the one it forbids passed. Each module is listed once."""
    order = documented_order()
    assert len(order) == len(set(order)) and ORDER == order


def test_the_order_rule_finds_an_import_from_above_and_a_module_not_placed():
    source = """
from .jde_settings import load
from . import jde_formats
from ..erp.jde_review import voucher_view
from ap_coder.erp.jde_zfiles import voucher_rows
import ap_coder.erp.jde_match


def later():
    from .jde_voucher import _prepare
"""
    tree = ast.parse(source)
    named = {"jde_settings", "jde_formats", "jde_review", "jde_zfiles", "jde_match", "jde_voucher"}
    assert jde_imports(tree) == named
    order = ("jde_formats", "jde_settings", "jde_accounts", "jde_voucher")
    found = out_of_order({"jde_accounts": jde_imports(tree), "jde_formats": set(), "jde_new": set()}, order)
    assert found == ["jde_new: not in ORDER", "jde_accounts imports jde_match", "jde_accounts imports jde_review",
                     "jde_accounts imports jde_voucher", "jde_accounts imports jde_zfiles"]  # fmt: skip


# --- (e) A finding made after the coding's checks is kept when the queue checks again --------------------------------

# The modules whose findings are made after the coding's checks (``finalise_coding``): the readers, the AI reviewer and
# Azure's second reading. Not ai/corrections.py: it reads the coding's own codes (TOTAL_MISMATCH...), it raises none.
AFTER_THE_CHECKS = ("ap_coder/capture/", "ap_coder/ai/verifier.py", "ap_coder/ai/azure_check.py")


def raised_after_the_checks(texts: dict[str, str]) -> set[str]:
    """The codes the modules of ``texts`` (by their path from the root) raise after the coding's checks."""
    return set().union(*(codes_raised_in(text) for name, text in texts.items() if name.startswith(AFTER_THE_CHECKS)))


def test_a_finding_made_after_the_codings_checks_is_kept_when_the_queue_checks_again():
    """The queue works today's checks out again (bulk approval, the second approval) and keeps of the stored findings
    only those in ``bulk.AFTER_PROCESSING`` (``bulk._kept``): a reader's or a reviewer's code left out of it is dropped
    unseen. The file cut off is the pipeline's own (``pipeline.CUT_OFF``), and a currency not printed is raised from
    capture/bridge.py through ``validation.currency_not_found``, which a scan by module misses: both named here."""
    app = sorted((ROOT / "ap_coder").rglob("*.py"))
    raised = raised_after_the_checks({p.relative_to(ROOT).as_posix(): p.read_text(encoding="utf-8") for p in app})
    assert len(raised) > 10  # (the scan still finds them)
    assert (raised | {CUT_OFF, currency_not_found().code}) - AFTER_PROCESSING == set()


def test_the_after_the_checks_rule_finds_a_code_a_reader_raises():
    texts = {
        "ap_coder/capture/reader/new.py": 'issues.append((WARNING, "READ_NEW", "the reader found something new"))',
        "ap_coder/ai/verifier.py": 'Issue(INFO, "VERIFIER_NEW", "the reviewer says something new")',
        "ap_coder/coding/validation.py": 'issues.append(Issue(ERROR, "CODED_NEW", "a check of the coding"))',
    }
    assert raised_after_the_checks(texts) == {"READ_NEW", "VERIFIER_NEW"}


# --- (f) Learned tax codes are looked up by the province they are recorded under -----------------------------------


def supply_rules(tree: ast.Module) -> set[tuple[str, ...]]:
    """The names each ``ship_to_province or ...`` of ``code_from_capture`` tries in turn: where the offline coder takes
    an invoice to be supplied, for its tax lines and the tax codes it learned (``line_tax.learned_codes``)."""
    coder = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "code_from_capture")
    return {tuple(ast.unparse(v) for v in node.values) for node in ast.walk(coder)
            if isinstance(node, ast.BoolOp) and ast.unparse(node.values[0]) == "ship_to_province"}  # fmt: skip


def recorded_elsewhere(rule: tuple[str, ...]) -> list[tuple[str, str, str]]:
    """Each (ship-to, our province, the vendor's) of a province, outside Canada, one in lower case or none where the
    first of ``rule``'s names that is set, as ``line_tax.learned_codes`` compares it, is not
    ``line_tax.learned_province``, the province an approval records the codes under."""
    found = []
    for ship_to, ours, vendor in itertools.product(("", *PROVINCES, OUTSIDE_CANADA, "on"), repeat=3):
        known = {"ship_to_province": ship_to, "ours": ours, "supplier_province": vendor}
        looked_up = next((known[name] for name in rule if known[name]), "").strip().upper()
        if learned_province(SimpleNamespace(ship_to_province=ship_to, supplier_province=vendor), ours) != looked_up:
            found.append((ship_to, ours, vendor))
    return found


def test_learned_tax_codes_are_looked_up_by_the_province_they_are_recorded_under():
    """An approval records each line's tax code under where the invoice was supplied (``line_tax.learned_province``,
    ``feedback.tax_province``), and the offline coder looks the codes up by its own ``ship_to_province or ours or
    supplier_province``, taking only rows of that province (``line_tax.learned_codes``): two copies of one rule.
    Neither has ``tax.supply_source``'s site step (the province of the cost center every line goes to, which the Tax
    check uses): the coder looks them up from what the page and Settings say, and an approval must record them by the
    same rule, a ship-to outside Canada as OUTSIDE_CANADA too (not our province in its place). The chat model's lines
    (``pipeline._model_line_taxes``) are looked up without our province, a question for after the owner's test."""
    rules = supply_rules(_trees("ap_coder/ai")["ap_coder/ai/offline_coder.py"])
    assert rules  # (the coder still writes its rule out)
    assert {rule: recorded_elsewhere(rule)[:3] for rule in rules} == {rule: [] for rule in rules}


def test_the_supply_rule_finds_a_lookup_in_another_order():
    source = "def code_from_capture():\n    f(ship_to_province or ours or supplier_province, a or b)\n"
    assert supply_rules(ast.parse(source)) == {("ship_to_province", "ours", "supplier_province")}
    assert recorded_elsewhere(("ship_to_province", "supplier_province", "ours"))[:1] == [("", "AB", "BC")]


# --- (g) The discount report reads every invoice whose terms give a discount ----------------------------------------

# Terms as invoices print them and as the vendor master says them (JD Edwards' in words): a discount or none, a net or
# none, prox, end of month, a fixed day, a late-payment rate that is no discount, words not understood.
TERMS = ("", "Net 30", "2/10 Net 30", "2% 10 days", "Due on receipt", "As agreed", "Net 30 EOM", "Net 30 prox",
         "2/10th prox net 30th", "2% 10th prox", "1% 10, 25th prox", "Net 15th prox", "Due end of month",
         "Due 2026-12-31", "Net 30, 1.5% 10th prox interest on overdue accounts", "2/10 net 12 months", "fin de mois",
         "1%/15/net 45")  # fmt: skip


def discounts_left_out(may_discount: Callable[[str, str], bool]) -> list[tuple[str, str]]:
    """Each (printed, the vendor's) pair of ``TERMS`` whose discount ``terms.payment`` finds and ``may_discount`` (the
    Insights page's first sift) leaves out."""
    coding = {"invoice_date": "2026-09-15", "grand_total": 100}
    return [(printed, vendor) for printed, vendor in itertools.product(TERMS, repeat=2)
            if payment({**coding, "payment_terms": printed}, vendor_terms=vendor).discount_by is not None
            and not may_discount(printed, vendor)]  # fmt: skip


def test_the_discount_report_reads_every_invoice_whose_terms_give_a_discount():
    """``insights._may_discount`` copies ``terms.payment``'s rule for when the vendor master's terms take the place of
    the printed ones, so that Insights reads only the invoices that may have a discount: one it leaves out is missing
    from the discounts taken and missed, unseen."""
    assert discounts_left_out(_may_discount) == []


def test_the_discount_rule_finds_a_discount_only_the_vendors_terms_give():
    def printed_only(printed: str, vendor: str) -> bool:
        return bool(parse_terms(printed).discount_pct)

    assert ("", "2/10 Net 30") in discounts_left_out(printed_only)


# --- (h) Every tax rate in force is one the readers know ------------------------------------------------------------


def rates_in_force() -> set[tuple[str, float]]:
    """(tax, rate) of each rate of data/canada_tax_rates.csv with no end date."""
    with (ROOT / "data" / "canada_tax_rates.csv").open(encoding="utf-8", newline="") as f:
        return {(row["tax_type"], float(row["rate"])) for row in csv.DictReader(f) if not row["effective_to"]}


def rates_unknown(in_force: set[tuple[str, float]], known: dict[str, dict[str, Iterable[float]]]) -> list[str]:
    """Each rate in force that a list of the rates by tax (``known``: {where: {tax: rates}}) doesn't have."""
    return [f"{where}: {tax} {rate:g}" for where, rates in known.items() for tax, rate in sorted(in_force)
            if rate not in rates.get(tax, ())]  # fmt: skip


def test_every_tax_rate_in_force_is_one_the_readers_know():
    """The rates a Canadian invoice charges are written out three times besides the rate table: the rule reader's
    (``totals._RATES``), Azure's reading's (``capture.DI_TAX_RATES``) and the offline coder's
    (``offline_coder._OFFICIAL``, a rate worked out from rounded amounts snaps to one of them). A rate in force left
    out of one is not known there. ``DI_TAX_RATES`` holds only a rate that names one tax, so a rate two taxes share
    needn't be in it."""
    in_force = rates_in_force()
    shared = {rate for tax, rate in in_force if any(r == rate and t != tax for t, r in in_force)}
    azure: dict[str, list[float]] = {}
    for rate, field in DI_TAX_RATES.items():
        azure.setdefault(field.split("_")[0].upper(), []).append(rate)
    known = {"totals._RATES": {field.split("_")[0].upper(): rates for field, rates in _RATES.items()},
             "offline_coder._OFFICIAL": _OFFICIAL}  # fmt: skip
    assert len(in_force) >= 6
    assert rates_unknown(in_force, known) == []
    assert rates_unknown({(t, r) for t, r in in_force if r not in shared}, {"capture.DI_TAX_RATES": azure}) == []


def test_the_rates_rule_finds_a_new_rate():
    known = {"x": {"GST": (0.05,), "PST": (0.06, 0.07)}}
    assert rates_unknown({("GST", 0.05), ("PST", 0.07), ("PST", 0.09)}, known) == ["x: PST 0.09"]
