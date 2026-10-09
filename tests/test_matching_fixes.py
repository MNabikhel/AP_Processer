"""Regression tests for defects in coding rules, PO matching, statements and labels."""

import pytest

from ap_coder.rules import Rule


@pytest.mark.parametrize(
    "contains, description, matches",
    [
        ("rent", "Current transformer, 200A", False),  # not inside another word
        ("fee", "Coffee service", False),
        ("oil", "Aluminium foil rolls", False),
        ("rent", "Office rent - October", True),
        ("monitor", "Monitors 27 in", True),  # a plural still matches
        ("freight", "Freight-in (Purolator)", True),
    ],
)
def test_rule_words_match_at_the_start_of_a_word(contains, description, matches):
    assert Rule("", contains, "6100").matches("Any vendor", description) is matches
