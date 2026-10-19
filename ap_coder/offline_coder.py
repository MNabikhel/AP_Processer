"""Moved to ap_coder.ai.offline_coder; this alias goes in a later release."""

import importlib
import sys

sys.modules[__name__] = importlib.import_module("ap_coder.ai.offline_coder")
