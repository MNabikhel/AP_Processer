"""First-run setup after the packages are installed. The launcher (scripts/launch.py, behind APProcessor.bat /
APProcessor.command) uses its ``choose_data_dir``; run on its own, it does both steps below.

* records the data folder (database, invoices, outputs) outside the code, default ``~/APCoder``, so
  unzipping a newer version never loses or duplicates your data; an existing choice is kept, and data an
  older version kept inside its code folder (``private/``) is moved there
* puts the PP-OCRv5 OCR models in place (``scripts/fetch_models.py``: copied from the offline bundle's
  ``models/`` folder, never downloaded unless ``AP_ALLOW_INTERNET=1``); if that fails, OCR still works
  with one engine and this says where they come from

Safe to run again: it changes nothing that is already set up. Never asks questions.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from ap_coder.core import paths  # noqa: E402

DEFAULT_DATA_DIR = paths.default_data_dir()


def choose_data_dir() -> Path:
    """The folder already in use, else ``~/APCoder``; data kept inside a code folder (``private/``, as AP Coder 0.1
    did) is moved to ``~/APCoder`` first, so replacing the code never touches it (``scripts/upgrade.py``)."""
    import upgrade

    return upgrade.settle_data_folder(ROOT, print, default=DEFAULT_DATA_DIR)


def main() -> int:
    data = choose_data_dir()
    data.mkdir(parents=True, exist_ok=True)
    (data / "invoices").mkdir(exist_ok=True)
    print(f"Data folder: {data}")

    import fetch_models

    if fetch_models.main([]) != 0:
        print("(Not needed to start: the dashboard works, and scans are read with one OCR engine.)")
    return 0  # the models are an extra: never stop the setup over them


if __name__ == "__main__":
    sys.exit(main())
