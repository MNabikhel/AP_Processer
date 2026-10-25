"""AP Coder's feed for the company's finance hub on Microsoft Fabric (D-103; the data dictionary: docs/FINANCE_HUB.md).

* ``tables``: the six versioned tables (invoices, invoice_lines, approvals, activity, accuracy, workflow_runs), built
  from the Store with stable column names and types (``HUB_SCHEMA_VERSION``).
* ``landing``: the files, laid out as Fabric Open Mirroring's landing zone wants them (a folder per table, numbered
  files, ``_metadata.json``), Parquet when pyarrow is installed, else CSV with ``_schema.json``; written whole or not
  at all; and the read-only SQLite copy for the on-premises data gateway. Option A, "Fabric pulls": a shared folder.
* ``onelake``: Option B, "AP Coder pushes" the same files to OneLake (only with the internet allowed and an Admin's
  opt-in).
* ``run``: one run (incremental or full, or a dry run), its settings, what was sent, and the schedule.

Off until an Admin turns it on (Settings → Finance hub). The invoices' PDFs never go: only a link to each invoice's
page in AP Coder.
"""

from .landing import HubError, MemoryDestination
from .run import HubConfig, HubSchedule, Result, export
from .tables import HUB_SCHEMA_VERSION, TABLES

__all__ = ["HUB_SCHEMA_VERSION", "TABLES", "HubConfig", "HubError", "HubSchedule", "MemoryDestination", "Result",
           "export"]  # fmt: skip
