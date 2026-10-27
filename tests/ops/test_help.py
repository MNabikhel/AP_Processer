"""The help catalog covers every check the engine can raise, and nothing that no longer exists."""

import re

from ap_coder.ops.help import AREAS, CHECKS, help_for
from tests.conftest import ROOT

SOURCES = [
    "coding/validation.py",
    "coding/tax.py",
    "coding/vendors.py",
    "coding/po.py",
    "coding/terms.py",
    "capture/bridge.py",
    "ai/verifier.py",
    "ai/azure_check.py",
    "ai/pipeline.py",
    "coding/banks.py",
    "web/invoice_view.py",
    "erp/jde.py",
    "erp/jde_sync.py",
]


def _raised_codes():
    codes = set()
    for name in SOURCES:
        text = (ROOT / "ap_coder" / name).read_text(encoding="utf-8")
        codes |= set(
            re.findall(r"\b(?:ERROR|WARNING|INFO|\"error\"|\"warning\"|\"info\")\s*,\s*\"([A-Z][A-Z0-9_]+)\"", text)
        )
        # a code raised through a constant: Issue(WARNING, ISSUE_CODE, ...) with ISSUE_CODE = "AZURE_DI_DISAGREES"
        constants = dict(re.findall(r"(?m)^([A-Z][A-Z0-9_]*)\s*=\s*\"([A-Z][A-Z0-9_]+)\"", text))
        for name in re.findall(r"\b(?:ERROR|WARNING|INFO)\s*,\s*([A-Z][A-Z0-9_]*)\s*,", text):
            if name in constants:
                codes.add(constants[name])
    return codes


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
