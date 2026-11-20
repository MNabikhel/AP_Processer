"""Lists that must change together. An event that changes an invoice under an open review screen is a revision
(``REVISION_ACTIONS``, D-184), or a screen open on a laptop (which keeps nothing of who has it open) is never told and
its next check saves what it showed over the change: the same mistake twice, B-2805 and B-2985. Each rule has a
self-test showing it finds what it should."""

from __future__ import annotations

import ast
import functools
import re

from tests.conftest import ROOT

# Events a store function that changes an invoice logs which are not a revision: why a review screen drawn before one
# is still right.
NOT_REVISIONS = {
    "exported": "an approved invoice put in an export batch: its coding and where it stands are as they were",
    "export_undone": "its export batch undone: back in the ready-to-export list, its coding as it was",
    "failed": "a new invoice, one that could not be read: no screen of it was drawn before",
    "file_found": "only where its file is changed (source_path), not its coding or where it stands",
    "processed": "a new invoice: no screen of it was drawn before",
    "proposal_updated": "the page worker's replaces, without unless_in_use: only while nobody worked on it, and a "
    "screen drawn while it waits to be read is drawn again once it is, the edits kept as a draft (D-86, the poll in "
    "static/js/invoice.js)",
}
# erp/jde*.py, lowest first: each imports only the ones before it, at the top or inside a function (D-222; the list
# at the top of erp/jde.py). After jde.py, the keying sheet and the AIS API's modules.
ORDER = ("jde_formats", "jde_settings", "jde_prototype", "jde_accounts", "jde_voucher", "jde_retainage", "jde_remarks",
         "jde_zfiles", "jde_match", "jde_review", "jde", "jde_keying", "jde_worksheet", "jde_ais", "jde_post",
         "jde_sync")  # fmt: skip
CHANGES_INVOICES = re.compile(
    r"\b(?:UPDATE|DELETE\s+FROM|(?:INSERT|REPLACE)(?:\s+OR\s+\w+)?\s+INTO)\s+invoices\b", re.I
)


@functools.cache
def _trees(folder: str) -> dict[str, ast.Module]:
    """Each module under ``folder`` (from the repository's root), parsed."""
    paths = sorted((ROOT / folder).rglob("*.py"))
    return {p.relative_to(ROOT).as_posix(): ast.parse(p.read_text(encoding="utf-8")) for p in paths}


def _called(call: ast.Call) -> str:
    """The name a call calls (``store.replace_proposal(...)``: replace_proposal)."""
    return call.func.attr if isinstance(call.func, ast.Attribute) else getattr(call.func, "id", "")


def _calls(trees: dict[str, ast.Module]) -> list[tuple[str, ast.Call]]:
    return [(name, node) for name, tree in trees.items() for node in ast.walk(tree) if isinstance(node, ast.Call)]


def _revisions() -> tuple[str, ...]:
    from ap_coder.core.store.invoices import REVISION_ACTIONS

    return REVISION_ACTIONS


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
    assert replaces_not_revisions(_trees("ap_coder"), _revisions()) == []


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


def _values(node: ast.expr | None, fn: ast.FunctionDef, calls: list[tuple[str, ast.Call]]) -> set[str] | None:
    """The strings an argument can be: a constant, either side of ``a if b else c``, a name assigned them in the
    function, or a parameter: its default and what each call of the function passes it. None: not told."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {node.value}
    if isinstance(node, ast.IfExp):
        both = (_values(node.body, fn, calls), _values(node.orelse, fn, calls))
        return None if None in both else both[0] | both[1]
    if not isinstance(node, ast.Name):
        return None
    sources: list[ast.expr | None] = []
    for assign in ast.walk(fn):
        if isinstance(assign, ast.Assign) and any(isinstance(t, ast.Name) and t.id == node.id for t in assign.targets):
            sources.append(assign.value)
    args = fn.args
    positional = [*args.posonlyargs, *args.args]
    defaults = dict(
        zip([a.arg for a in positional][len(positional) - len(args.defaults) :], args.defaults, strict=True)
    )
    defaults.update((a.arg, d) for a, d in zip(args.kwonlyargs, args.kw_defaults, strict=True) if d is not None)
    if node.id in defaults:
        sources.append(defaults[node.id])
        sources += [k.value for _, call in calls if _called(call) == fn.name for k in call.keywords if k.arg == node.id]
    elif node.id in {a.arg for a in [*positional, *args.kwonlyargs]}:
        return None  # (a parameter every caller must give: what it is, the callers say)
    if not sources:
        return None
    found = [_values(s, fn, calls) for s in sources]
    return None if None in found else set().union(*found)


def logged_by_invoice_changes(store: dict[str, ast.Module], everything: dict[str, ast.Module]) -> dict[str, set | None]:
    """Each ``_log`` call of a store function whose SQL changes the invoices table: the actions it can log (None: not
    told), by where it is."""
    calls = _calls(everything)
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
                    found[f"{name}:{call.lineno} {fn.name}"] = _values(action, fn, calls)
    return found


def unexplained(logged: dict[str, set | None], revisions: tuple[str, ...], explained: dict[str, str]) -> list[str]:
    return [f"{where}: {sorted(actions) if actions is not None else 'which action, not told'}"
            for where, actions in logged.items()
            if actions is None or actions - set(revisions) - set(explained)]  # fmt: skip


def test_an_invoice_changed_in_the_store_is_a_revision_or_says_why_not():
    """Every event logged where the store changes an invoice is a revision (a screen drawn before it is out of date,
    says so, and saves nothing) or in NOT_REVISIONS with why a screen drawn before it is still right."""
    logged = logged_by_invoice_changes(_trees("ap_coder/core/store"), _trees("ap_coder"))
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
    logged = logged_by_invoice_changes({"s.py": tree}, {"s.py": tree})
    assert logged == {"s.py:5 park": {"parked_by_hand"}, "s.py:10 answer": {"confirmed", "sent_back"},
                      "s.py:14 replace": {"proposal_updated", "read_again"}, "s.py:18 told": None}  # fmt: skip
    found = unexplained(logged, ("sent_back", "confirmed"), {"proposal_updated": "why"})
    assert found == ["s.py:5 park: ['parked_by_hand']", "s.py:14 replace: ['proposal_updated', 'read_again']",
                     "s.py:18 told: which action, not told"]  # fmt: skip


# --- (c) Each revision has its words on the screen that is out of date ----------------------------------------------


def unworded(words: dict[str, str], revisions: tuple[str, ...]) -> list[str]:
    """A revision with no words (the screen would say only "Changed"), and words for an event that is none."""
    return sorted(set(revisions) ^ set(words))


def test_a_screen_out_of_date_says_what_each_revision_was():
    """``invoice_drafts._CHANGES`` words each revision ("Approved by Ann Lee today at 10:41 a.m."); one left out
    would say "Changed" without a word in the tests."""
    from ap_coder.web.pages.invoice_drafts import _CHANGES

    assert unworded(_CHANGES, _revisions()) == []


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
    one home (``jde_settings.load``, not ``jde.load``)."""
    trees = _trees("ap_coder/erp")
    imports = {name.rsplit("/", 1)[1][:-3]: jde_imports(tree) for name, tree in trees.items() if "/jde" in name}
    assert set(ORDER) <= set(imports)  # (a module gone leaves ORDER)
    assert out_of_order(imports, ORDER) == []


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
