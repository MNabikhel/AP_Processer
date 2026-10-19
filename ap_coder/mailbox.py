"""Moved to ap_coder.intake.mailbox; this alias goes in a later release."""

import importlib
import sys

sys.modules[__name__] = importlib.import_module("ap_coder.intake.mailbox")
