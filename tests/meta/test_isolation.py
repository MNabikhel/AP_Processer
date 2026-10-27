"""Each test starts from the environment the run started with, whatever the test before it left (tests/conftest.py)."""

import os

LEFT = "AP_TEST_LEFT_BEHIND"


def test_a_variable_set_by_hand_or_by_a_settings_file_is_left_behind():
    """Settings.from_env(path) writes a .env file's variables into os.environ: a test's AP_REVIEWER stayed for the
    tests after it, which then saw a name given (B-1410)."""
    os.environ[LEFT] = "1"


def test_the_next_test_starts_without_it():
    assert LEFT not in os.environ
