"""The help catalog covers every check the engine can raise, and nothing that no longer exists."""

import re

from ap_coder.help import AREAS, CHECKS, help_for

from .conftest import ROOT

SOURCES = ["validation.py", "tax.py", "vendors.py", "po.py", "terms.py", "capture/bridge.py"]


def _raised_codes():
    codes = set()
    for name in SOURCES:
        text = (ROOT / "ap_coder" / name).read_text(encoding="utf-8")
        codes |= set(
            re.findall(r"\b(?:ERROR|WARNING|INFO|\"error\"|\"warning\"|\"info\")\s*,\s*\"([A-Z][A-Z0-9_]+)\"", text)
        )
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
    from ap_coder.webapp.help import checks_table

    html, count = checks_table("qst")
    assert 0 < count < len(CHECKS) and "QST_NUMBER_MISSING" in html
    assert checks_table("", "Purchase orders")[1] == sum(1 for c in CHECKS.values() if c.area == "Purchase orders")
    assert checks_table("zzzz-not-a-check") == ("", 0)


def test_routine_links_to_real_pages():
    import re

    from ap_coder.help import ROUTINE

    from .conftest import ROOT

    pages = set(re.findall(r'"(\w+)": st\.Page\(', (ROOT / "ap_coder" / "dashboard.py").read_text()))
    assert {page for _, tasks in ROUTINE for _, page in tasks} <= pages
