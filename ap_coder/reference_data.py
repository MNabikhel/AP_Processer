"""Moved to ap_coder.core.reference_data; this alias goes in a later release."""

import importlib
import sys

sys.modules[__name__] = importlib.import_module("ap_coder.core.reference_data")
