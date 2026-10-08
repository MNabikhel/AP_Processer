"""AP Coder public demo: the review dashboard with made-up invoices, for Streamlit Community Cloud.

Community Cloud runs this file (its default main file). It starts the normal dashboard in public demo
mode (``AP_PUBLIC_DEMO``, see ``ap_coder/webapp/public_demo.py``):

* the database lives in a temporary folder, never in a real data folder, and a restart starts it over;
* the made-up demo invoices are loaded on the first visit, and a banner offers to reset them;
* no ``.env`` and no Azure settings are read, so nothing is ever sent to Azure; processing new invoices
  and settings that write to folders say they are not available in the demo.

Try it on your own computer: ``streamlit run streamlit_app.py``. For real work use ``start.bat`` /
``python -m ap_coder dashboard`` instead (see the README).
"""

from __future__ import annotations

import os
import runpy
import tempfile
from pathlib import Path

DEMO_DIR = Path(os.environ.get("AP_DEMO_DIR") or Path(tempfile.gettempdir()) / "ap_coder_public_demo")

os.environ.update(
    {
        "AP_PUBLIC_DEMO": "1",
        "AP_PRIVATE_DIR": str(DEMO_DIR),
        "AP_DB_PATH": str(DEMO_DIR / "ap_coder.db"),
        "AP_ENV_FILE": str(DEMO_DIR / ".env"),  # not created: no real .env is ever read
        "AP_USER_SETTINGS": str(DEMO_DIR / "user_settings.json"),  # not the real ~/.ap_coder
    }
)
os.environ.setdefault("AP_REVIEWER", "Alex (demo)")
for name in [n for n in os.environ if n.startswith("AZURE_")]:  # never use Azure, even if set on the host
    del os.environ[name]

runpy.run_path(str(Path(__file__).resolve().parent / "ap_coder" / "dashboard.py"), run_name="__main__")
