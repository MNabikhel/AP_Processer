"""One JD Edwards account string joined and split in one place (D-209)."""

from __future__ import annotations

import pytest

from ap_coder.core import chart


@pytest.mark.parametrize(
    ("business_unit", "code", "account"),
    [
        ("0880133142", "6215.1170", "0880133142.6215.1170"),
        ("0880133142", "3005", "0880133142.3005"),
        ("03002407", "6275.8464", "03002407.6275.8464"),
        ("0880133142", "3091.ET", "0880133142.3091.ET"),
        ("03007953", "3117.73301299", "03007953.3117.73301299"),
        ("08801", "0019", "08801.0019"),
    ],
)
def test_an_account_joins_and_splits_back(business_unit, code, account):
    assert chart.join_account(business_unit, code) == account
    assert chart.split_account(account) == (business_unit, code)


def test_an_exports_padding_and_blank_cells_are_dropped():
    assert chart.split_account("  0880133142.6215.1170") == ("0880133142", "6215.1170")
    assert chart.split_account("    03002407.6275.8464 ") == ("03002407", "6275.8464")
    assert chart.join_account("       08801", " ") == "08801"
    assert chart.code_of("6215", " ") == "6215"
    assert chart.code_of(" 6215 ", "1170") == "6215.1170"
    assert chart.clean(None) == chart.clean(" ") == chart.clean("nan") == ""


def test_leading_zeros_and_letters_are_kept_as_text():
    assert chart.split_code("0019") == ("0019", "")
    assert chart.split_code("3017.31DTK0") == ("3017", "31DTK0")
    assert chart.join_account("0300674", "0019") == "0300674.0019"


def test_a_text_with_no_dot_is_a_business_unit_and_a_code_alone_joins_to_itself():
    assert chart.split_account("0880133142") == ("0880133142", "")
    assert chart.join_account("", "6215.1170") == "6215.1170"
    assert chart.join_account("0880133142", "") == "0880133142"
    assert chart.split_account("") == ("", "")


def test_a_dot_left_at_the_end_is_not_an_empty_subsidiary():
    assert chart.split_account("0880133142.3005.") == ("0880133142", "3005")
    assert chart.code_of("3005", "") == "3005"
