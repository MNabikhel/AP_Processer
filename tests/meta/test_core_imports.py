"""``core`` is the base layer (D-192): nothing in ``ap_coder/core`` (the store included) imports from the layers above
it (``coding``, ``capture``, ``ai``, ``erp``, ``ops``, ``web``, ``intake``, ``bench``), at the top of a module or
inside a function, but the imports listed here, each with why. What the store keeps and compares by is in core
(``core/keys.py``, ``core/tax_setup.py``, ``core/memory.py``); what is left is the business rules the store runs
inside its own transactions and the service's own facts the backups need. The list only shrinks: an import no longer
made leaves it (``test_the_list_names_only_imports_still_made``)."""

from __future__ import annotations

import ast
import functools
from pathlib import Path

from tests.conftest import ROOT

UP = ("coding", "capture", "ai", "erp", "ops", "web", "intake", "bench")
# (the file, the module it imports): why it stays
KEPT = {
    ("ap_coder/core/audit.py", "ap_coder.coding.line_tax"): "a line's tax change is said in the tax codes' own words "
        "(code_label, tax_code_of)",
    ("ap_coder/core/audit.py", "ap_coder.ops.help"): "an approval over a check says the check's name from its help "
        "entry, which every check code has (ops/help.py)",
    ("ap_coder/core/schema.py", "ap_coder.coding.line_tax"): "an invoice stored before tax_exempt existed is read as "
        "the vendor meant it (line_tax.fill_exempt, D-64)",
    ("ap_coder/core/store/_base.py", "ap_coder.coding.terms"): "an invoice's due date, kept in its row, is worked out "
        "by the payment terms' parser (terms.payment)",
    ("ap_coder/core/store/_base.py", "ap_coder.ai.offline_coder"): "whether the AI only guessed a line is the coder's "
        "own mark on its reasoning (offline_coder.guessed)",
    ("ap_coder/core/store/_base.py", "ap_coder.coding.line_tax"): "schema 17's step fills each approved line's tax "
        "code with the vote an approval casts (line_tax.tax_vote)",
    ("ap_coder/core/store/backup_files.py", "ap_coder.ops.offline"): "how long backups are kept: the company server's "
        "(server mode) or a laptop's",
    ("ap_coder/core/store/backup_files.py", "ap_coder.ops.service"): "the server told as the doctor tells it, by its "
        "server.json too (service.is_server_here, B-1960)",
    ("ap_coder/core/store/backups.py", "ap_coder.ops.health"): "the nightly backup runs in the night of the business's "
        "time zone, server.json's (D-187)",
    ("ap_coder/core/store/backups.py", "ap_coder.ops.system_log"): "a backup that failed is told to the system's own "
        "log, where IT looks",
    ("ap_coder/core/store/backups.py", "ap_coder.ops.service"): "the second backup folder must be one IT allowed in "
        "server.json (service.folder_refusal, D-109)",
    ("ap_coder/core/store/capture.py", "ap_coder.capture.supplier"): "a supplier's autonomy is moved by its rules "
        "(policy, transitions, stats) in the transaction that records its outcomes",
    ("ap_coder/core/store/capture.py", "ap_coder.capture.workflow"): "an approval withdrawn: the template learned "
        "again from the later ones (workflow.replay_template, D-119)",
    ("ap_coder/core/store/hub.py", "ap_coder.erp.hub.tables"): "the gateway file's tables are the finance hub's own "
        "definitions, read when it is written (erp/hub imports this part, D-189)",
    ("ap_coder/core/store/invoices.py", "ap_coder.coding.line_tax"): "an approval learns each line's tax code vote "
        "in its own transaction (line_tax.tax_vote, D-70)",
    ("ap_coder/core/store/invoices.py", "ap_coder.coding.terms"): "the default payment terms, as the due date's "
        "parser has them (terms.DEFAULT_TERMS_DAYS)",
    ("ap_coder/core/store/lessons.py", "ap_coder.coding.line_tax"): "the corrections listed say a tax code in its own "
        "words (line_tax.code_label)",
    ("ap_coder/core/store/lessons.py", "ap_coder.coding.validation"): "the duplicates checked again are said and "
        "weighed as the checks do (duplicate_message, needs_review, B-1182)",
    ("ap_coder/core/store/old_readings.py", "ap_coder.ai.offline_coder"): "a migration step: the invoices read before "
        "D-89 scored again with the reading's own code, once per database",
    ("ap_coder/core/store/old_readings.py", "ap_coder.capture.confidence"): "a migration step: the old readings' "
        "confidence worked out again (D-89)",
    ("ap_coder/core/store/old_readings.py", "ap_coder.capture.types"): "a migration step: the old readings' stored "
        "fields read back (D-89)",
    ("ap_coder/core/store/old_readings.py", "ap_coder.coding.validation"): "a migration step: the old readings' "
        "confidence and review flag scored again (D-89)",
    ("ap_coder/core/store/purchasing.py", "ap_coder.coding.po"): "month-end's billed lines kept for every process "
        "are read as the PO matching reads them (po.positive_lines)",
    ("ap_coder/core/store/purchasing.py", "ap_coder.coding.rules"): "the fixed coding rules are read back as the "
        "rules that apply them (rules.Rule)",
    ("ap_coder/core/store/samples.py", "ap_coder.coding.po"): "the demo's purchase orders are read with the PO "
        "import's own parser",
    ("ap_coder/core/store/samples.py", "ap_coder.coding.vendors"): "the demo's vendor master is read with the "
        "vendor list import's own parser",
    ("ap_coder/core/store/summary.py", "ap_coder.coding.bulk"): "the summary works out each invoice's facts with the "
        "reports' own code, in the transaction that changes it, so they never disagree (D-110)",
    ("ap_coder/core/store/summary.py", "ap_coder.coding.taxreturn"): "the summary's tax facts are the tax return's "
        "own (D-110)",
    ("ap_coder/core/store/summary.py", "ap_coder.erp.accruals"): "the summary's billing facts are month-end's own "
        "(D-110)",
    ("ap_coder/core/store/summary.py", "ap_coder.erp.insights"): "the summary's insights facts are Insights' own "
        "(D-110)",
    ("ap_coder/core/store/summary.py", "ap_coder.erp.spend"): "the summary's spend facts are the spend report's own "
        "(D-110)",
    ("ap_coder/core/store/suppliers.py", "ap_coder.capture.supplier"): "every supplier's record at once, as its "
        "autonomy rules count it (SupplierStats, FieldStat, D-157)",
}  # fmt: skip


def _upward(source: str, package: str) -> set[str]:
    """The modules of the layers above ``core`` that ``source`` (a module of ``package``) imports, anywhere in it:
    ``from x import y`` names ``x.y`` when that is a module, else ``x``."""
    found = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom):
            base = package.split(".")[: len(package.split(".")) - node.level + 1] if node.level else []
            module = ".".join([*base, *([node.module] if node.module else [])])
            names = [f"{module}.{a.name}" if _is_module(f"{module}.{a.name}") else module for a in node.names]
        elif isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        else:
            continue
        found |= {n for n in names if n.split(".")[:1] == ["ap_coder"] and n.split(".")[1:2] in ([u] for u in UP)}
    return found


def _is_module(name: str) -> bool:
    path = ROOT.joinpath(*name.split("."))
    return path.with_suffix(".py").is_file() or (path / "__init__.py").is_file()


@functools.cache
def _found() -> frozenset[tuple[str, str]]:
    found = set()
    for path in sorted((ROOT / "ap_coder" / "core").rglob("*.py")):
        name = path.relative_to(ROOT).as_posix()
        parts = Path(name).with_suffix("").parts
        package = ".".join(parts[:-1])  # (a package's __init__ is in the package itself)
        found |= {(name, module) for module in _upward(path.read_text(encoding="utf-8-sig"), package)}
    return frozenset(found)


def test_core_imports_nothing_above_it_but_the_listed():
    new = sorted(_found() - set(KEPT))
    assert not new, ("core imports a layer above it: move what it needs down into core, have the caller pass it in, "
                     f"or list it in KEPT with why (D-192): {new}")  # fmt: skip


def test_the_list_names_only_imports_still_made():
    stale = sorted(set(KEPT) - _found())
    assert not stale, f"no longer imported: take them off KEPT (the list only shrinks): {stale}"
    assert all(why.strip() for why in KEPT.values())


def test_the_rule_finds_what_it_should():
    source = """
from __future__ import annotations

from ...coding.po import po_key
from ...coding import tax
from ..memory import vendor_key
from . import dialect
import ap_coder.erp.search
import json


def later():
    from ...ops import system_log
    from ap_coder.ai.page_worker import READER_ID
"""
    assert _upward(source, "ap_coder.core.store") == {
        "ap_coder.coding.po", "ap_coder.coding.tax", "ap_coder.erp.search", "ap_coder.ops.system_log",
        "ap_coder.ai.page_worker",
    }  # fmt: skip
    assert _upward("from ..coding.vendors import mask_account\nfrom .keys import po_key\n", "ap_coder.core") == {
        "ap_coder.coding.vendors"
    }
