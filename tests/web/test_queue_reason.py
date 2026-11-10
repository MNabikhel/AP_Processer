"""The review queue says why an invoice needs attention, not how many findings it has (B-2219): one sentence, the
first thing to fix in words. On the made-up company (``chart_company``)."""

from ap_coder.web.pages.review import first_to_fix
from tests.review_page import ReviewPage, queue_reasons
from tests.web.test_chart_company_walk import NEW_SUPPLIER, SEVERAL


def test_the_queue_says_the_first_thing_to_fix_in_words(chart_company, chart_company_client):
    said = queue_reasons(chart_company_client)
    ids = {name: invoice_id for name, (_, invoice_id) in chart_company.invoices.items()}
    assert set(said) == set(ids.values())
    # Why its first line without an account was left, as the line itself says it.
    several = SEVERAL.format(lines=3, accounts=3, shares="33% / 33% / 33%").rstrip(".")
    assert said[ids["four_lines"]] == f"Line 1: {several[0].lower()}{several[1:]}"
    # The first error's own sentence; what else there is to fix is counted after it, not instead of it.
    page = ReviewPage(chart_company_client, ids["usd_new_supplier"])
    assert page.lines[0].reason == NEW_SUPPLIER
    assert said[ids["usd_new_supplier"]] == ("This vendor is not in the vendor list imported from the ERP: pick it "
                                             "from the list, or add it as a new vendor (and 4 more)")  # fmt: skip
    # Nothing to fix: the row says Ready and no more.
    assert said[ids["one_line"]] == said[ids["quebec"]] == ""
    for reason in said.values():
        assert "to look at" not in reason and "to fix" not in reason  # never a count alone


def test_the_first_thing_to_fix_in_order():
    """An error first; else the first line left; else the Pay from account; else the first warning; else the count."""
    issues = [{"severity": "warning", "code": "NO_TAX_CHARGED", "message": "no tax was charged: check it."},
              {"severity": "warning", "code": "GL_UNASSIGNED", "message": "no GL account yet: pick one",
               "line_number": 4},
              {"severity": "error", "code": "TOTAL_MISMATCH", "message": "subtotal + tax = 105.00 but the total is "
               "150.00"}]  # fmt: skip
    lines = [{"line_number": 3, "predicted_gl_code": "7620"},
             {"line_number": 4, "predicted_gl_code": "UNASSIGNED",
              "reasoning_justification": "This supplier's 9 lines last year went to 3 accounts: pick the account."}]
    output = {"line_items": lines}
    pick = "Pick a GL account for line 4"
    assert first_to_fix((pick, "", 1, 1, False), issues, output, "") == (
        "Subtotal + tax = 105.00 but the total is 150.00 (and 2 more)")
    assert first_to_fix((pick, "", 0, 1, False), issues[:2], output, "") == (
        "Line 4: this supplier's 9 lines last year went to 3 accounts: pick the account (and 1 more)")
    assert first_to_fix((pick, "", 0, 0, False), [], {"line_items": [{"line_number": 4}]}, "") == pick
    assert first_to_fix(("", "Pick the account to pay from", 0, 1, False), issues[:1], output, "") == (
        "Pick the account to pay from (and 1 more)")
    assert first_to_fix(("", "", 0, 1, False), issues[:1], output, "") == "No tax was charged: check it"
    assert first_to_fix(("", "", 0, 0, True), [], output, "Confidence under 85%") == "Confidence under 85%"
