"""The review queue says why an invoice needs attention, not how many findings it has (B-2219): one sentence, the
first thing to fix in words. On the made-up company (``chart_company``)."""

from ap_coder.web.pages.review import first_to_fix
from tests.review_page import ReviewPage, queue_reasons
from tests.web.test_chart_company_walk import NEW_SUPPLIER


def test_the_queue_says_the_first_thing_to_fix_in_words(chart_company, chart_company_client):
    said = queue_reasons(chart_company_client)
    ids = {name: invoice_id for name, (_, invoice_id) in chart_company.invoices.items()}
    assert set(said) == set(ids.values())
    # Its lines without an account, counted (D6: why each was left is said on its review screen, cut off here).
    assert said[ids["four_lines"]] == "Pick accounts for 4 lines"
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
    pick = "Pick a GL account for line 4"
    issues = [{"severity": "warning", "code": "NO_TAX_CHARGED", "message": "no tax was charged: check it."},
              {"severity": "warning", "code": "GL_UNASSIGNED", "message": "no GL account yet: pick one",
               "line_number": 4},
              {"severity": "error", "code": "TOTAL_MISMATCH", "message": "subtotal + tax = 105.00 but the total is "
               "150.00"}]  # fmt: skip
    lines = [
        {"line_number": 3, "predicted_gl_code": "7620"},
        {
            "line_number": 4,
            "predicted_gl_code": "UNASSIGNED",
            "reasoning_justification": (
                "This supplier's 9 lines posted in JD Edwards went to 3 accounts: pick the account."
            ),
        },
    ]
    output = {"line_items": lines}
    assert first_to_fix((pick, "", 1, 1, False, ""), issues, output, "") == (
        "Subtotal + tax = 105.00 but the total is 150.00 (and 2 more)"
    )
    assert first_to_fix((pick, "", 0, 1, False, ""), issues[:2], output, "") == (
        "Pick the account for line 4 (and 1 more)"
    )
    two = {"line_items": [{**lines[1], "line_number": 3}, lines[1]]}
    assert first_to_fix((pick, "", 0, 0, False, ""), [], two, "") == "Pick accounts for 2 lines"
    coded = {"line_items": [{"line_number": 4, "predicted_gl_code": "7620"}]}  # a cost center to pick only
    assert first_to_fix((pick, "", 0, 0, False, ""), [], coded, "") == pick
    assert first_to_fix(("", "Pick the account to pay from", 0, 1, False, ""), issues[:1], output, "") == (
        "Pick the account to pay from (and 1 more)"
    )
    assert first_to_fix(("", "", 0, 1, False, ""), issues[:1], output, "") == "No tax was charged: check it"
    assert first_to_fix(("", "", 0, 0, True, ""), [], output, "Confidence under 85%") == "Confidence under 85%"
    # A document that may not be an invoice says so before anything else there is to fix.
    other = {"severity": "warning", "code": "NOT_AN_INVOICE", "message": 'this may not be an invoice: its title is '
             '"Statement of account"'}  # fmt: skip
    assert first_to_fix((pick, "", 1, 2, False, ""), [*issues, other], output, "") == (
        'This may not be an invoice: its title is "Statement of account" (and 3 more)'
    )


def test_a_row_with_no_confidence_says_a_dash_not_a_bar_at_0(chart_company, chart_company_client):
    """D6: an invoice with nothing coded showed an empty bar and "0%" in the queue's Confidence, read as a verdict."""
    text = chart_company_client.get("/review").text

    def row(name: str) -> str:
        at = text.index(f'href="/review/{chart_company.invoices[name][1]}"')
        return text[at : text.index("</li>", at)].split('class="c-conf"', 1)[1]

    assert chart_company.store.get_invoice(chart_company.invoices["four_lines"][1])["adjusted_confidence"] == 0
    assert "<b>—</b>" in row("four_lines") and 'class="bar"' not in row("four_lines")
    assert "%</b>" in row("one_line") and 'class="bar"' in row("one_line")
