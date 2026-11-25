"""The help catalog covers every check the engine can raise, and nothing that no longer exists."""

import re

from ap_coder.ops.help import AREAS, CHECKS, help_for
from tests.conftest import ROOT


def codes_raised_in(text: str) -> set[str]:
    """The check codes a module's source raises: ``Issue(WARNING, "CODE", ...)``, ``(ERROR, "CODE", ...)``."""
    codes = set(
        re.findall(r"\b(?:ERROR|WARNING|INFO|\"error\"|\"warning\"|\"info\")\s*,\s*\"([A-Z][A-Z0-9_]+)\"", text)
    )
    # a code raised through a constant: Issue(WARNING, ISSUE_CODE, ...) with ISSUE_CODE = "AZURE_DI_DISAGREES"
    constants = dict(re.findall(r"(?m)^([A-Z][A-Z0-9_]*)\s*=\s*\"([A-Z][A-Z0-9_]+)\"", text))
    for name in re.findall(r"\b(?:ERROR|WARNING|INFO)\s*,\s*([A-Z][A-Z0-9_]*)\s*,", text):
        if name in constants:
            codes.add(constants[name])
    # codes raised as they come, from a named set: elif code in SAID_AS_IS: out.append((ERROR, code, ...))
    for name, members in re.findall(r"(?m)^([A-Z][A-Z0-9_]*)\s*=\s*frozenset\(\{([^}]*)\}\)", text):
        if re.search(rf"\bcode in {name}:\s*\n\s*\S*\(\((?:ERROR|WARNING|INFO), code,", text):
            codes |= set(re.findall(r"\"([A-Z][A-Z0-9_]+)\"", members))
    return codes


def _raised_codes():
    """The codes raised anywhere in the app: ops/help.py, which names them all, left out."""
    help_module = ROOT / "ap_coder" / "ops" / "help.py"
    modules = [p for p in sorted((ROOT / "ap_coder").rglob("*.py")) if p != help_module]
    return set().union(*(codes_raised_in(p.read_text(encoding="utf-8")) for p in modules))


def test_every_check_has_help():
    raised = _raised_codes()
    assert len(raised) > 40  # the pattern still finds the checks
    assert raised - set(CHECKS) == set()


def test_no_help_for_checks_that_do_not_exist():
    assert set(CHECKS) - _raised_codes() == set()


def test_entries_are_complete():
    for code, about in CHECKS.items():
        assert about.area in AREAS, code
        assert about.title and about.meaning.endswith((".", ")")) and about.action.endswith("."), code
    assert help_for("DUPLICATE_INVOICE").area == "Duplicates & fraud"
    assert help_for("NOPE") is None


def test_no_help_says_to_approve_anyway_what_approve_anyway_never_covers():
    """B-3046: the help of JDE_TAX_AREA said to key an invoice taxed for two provinces by hand "and approve it with a
    reason", but every JD Edwards hold keeps Approve closed, with no "Approve anyway" (``invoice_findings.is_hard``;
    ``tests/web/test_hard_cases.py::test_o_lines_for_two_provinces_are_held``)."""
    from ap_coder.web.invoice_findings import HARD_CODES

    told = re.compile(r"\bapprove it\b[^.]*\b(?:anyway|with a reason)\b", re.I)
    hard = [code for code in CHECKS if code in HARD_CODES or code.startswith("JDE_")]
    assert len(hard) > 10 and [code for code in hard if told.search(CHECKS[code].action)] == []


def test_help_page_search_filters_the_catalog():
    from ap_coder.web.pages.help import matching_checks

    found = matching_checks("qst")
    assert 0 < len(found) < len(CHECKS) and "QST_NUMBER_MISSING" in dict(found)
    assert len(matching_checks("", "Purchase orders")) == sum(1 for c in CHECKS.values() if c.area == "Purchase orders")
    assert matching_checks("zzzz-not-a-check") == []


def test_routine_links_to_real_pages():

    from ap_coder.ops.help import ROUTINE
    from ap_coder.web.pages import BY_KEY

    assert {page for _, tasks in ROUTINE for _, page in tasks} <= set(BY_KEY)
