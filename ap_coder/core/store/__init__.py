"""Local SQLite store behind the dashboard (``private/ap_coder.db``).

Holds everything the prototype needs to run without any enterprise data
leaving the machine:

* GL accounts ("cost codes") and optional cost centers, imported by the user
  with their own choice of code / description / category columns.
* Tax setup: how each tax type (GST, HST, PST, QST) is posted and to which GL.
* Coding policy notes.
* Processed invoices with the AI output, the reviewer's final version and the
  validation report.
* Feedback: one row per approved invoice line, recording whether the reviewer
  accepted or corrected the AI's coding. This is the learning memory.

One class, ``Store``, built from a part per subject (D-69): ``_base`` (the schema, migrations, connections, shared
helpers), ``accounts`` (GL accounts, cost centers, audit trail, bank accounts, tax setup), ``invoices`` (saving,
approving, the ERP's register, the second approval), ``purchasing`` (POs, export batches, coding rules), ``capture``
(supplier learning, touchless, reader scores, the page-reader and Azure queues), ``lessons`` (lessons and the AI
reviewer's queue), ``vendors``, ``backups``, ``learning`` (the feedback memory, metrics), ``ephemeral`` (messages
and uploads kept for a short while), ``samples``. A test that patches a module-level name (``_now``, ``json``...)
patches it in the part that uses it.
"""

from ._base import (  # noqa: F401  (every name the old module had)
    _ACTIVE_IN,
    _AZURE_IN_LINE,
    _FEEDBACK,
    _FEEDBACK_LOCK,
    _FX_RATE,
    _HEADER_FIELDS,
    _IN_LINE,
    _LOOK_AGAIN_SECONDS,
    _NOT_FOUND,
    _RESTORING,
    _RESTORING_LOCK,
    _SCHEMA,
    _VERIFY_IN_LINE,
    ACCEPTED,
    ACCOUNT_TABLES,
    ACTIVE_STATUSES,
    APPROVAL_CONTROLS,
    APPROVED,
    AZURE_CHECKING,
    AZURE_DONE,
    AZURE_FAILED,
    AZURE_SKIPPED,
    AZURE_WAITING,
    CODED,
    CORRECTED,
    DEFAULT_TERMS_DAYS,
    DEFAULT_TOUCHLESS_LIMIT,
    DEFAULT_TREATMENTS,
    EVENT_ORIGIN,
    FAILED,
    FEEDBACK_READ,
    OPEN,
    PAGE_DONE,
    PAGE_FAILED,
    PAGE_READING,
    PAGE_SKIPPED,
    PAGE_WAITING,
    PARKED,
    PENDING,
    PROJECT_DIR,
    REJECTED,
    REVIEW,
    SCHEMA_VERSION,
    STALE_READING_HOURS,
    TAX_TYPES,
    TOUCHLESS_LIMIT_SETTING,
    TOUCHLESS_SETTING,
    TREATMENTS,
    UNASSIGNED,
    VENDOR_KEY_RULES,
    VERIFY_DONE,
    VERIFY_FAILED,
    VERIFY_RUNNING,
    VERIFY_SKIPPED,
    VERIFY_WAITING,
    Any,
    ApprovedAgain,
    Callable,
    DatabaseTooNew,
    Iterator,
    Path,
    ReferenceData,
    ReferenceTable,
    Rule,
    StoreCore,
    TaxRateTable,
    TaxSetup,
    TaxTreatment,
    _clean_code,
    _due,
    _Held,
    _held_connections,
    _in_list,
    _iso_week,
    _norm_number,
    _now,
    _same_row,
    _sessions,
    _stale_before,
    _tax_signature,
    as_read,
    coded_blanks,
    contextmanager,
    contextvars,
    database_schema,
    default_db_path,
    diff_coding,
    dt,
    find_moved_file,
    fold_text,
    hashlib,
    json,
    line_guessed,
    math,
    norm_invoice_number,
    nothing_proposed,
    pair_lines,
    parse_fx_rates,
    parse_policy_notes,
    payment,
    po_key,
    private_dir,
    proposed_coding,
    re,
    refresh_vendor_key,
    shutil,
    sqlite3,
    suggestion_kept,
    threading,
    time,
    tiny_fx_rates,
    unicodedata,
    unread_fx_text,
    vendor_key,
)
from .accounts import AccountsMixin
from .backups import BackupsMixin
from .capture import CaptureMixin
from .ephemeral import EphemeralMixin
from .invoices import InvoicesMixin
from .learning import LearningMixin
from .lessons import LessonsMixin
from .purchasing import PurchasingMixin
from .vendors import VendorsMixin


class Store(
    StoreCore,
    AccountsMixin,
    InvoicesMixin,
    PurchasingMixin,
    CaptureMixin,
    LessonsMixin,
    VendorsMixin,
    BackupsMixin,
    LearningMixin,
    EphemeralMixin,
):
    """The local SQLite database: one class, its parts in this folder by subject (see the module list above)."""


from .samples import (  # noqa: E402,F401
    SAMPLE_DATA_DIR,
    load_sample_cost_centers,
    load_sample_purchase_orders,
    load_sample_setup,
    load_sample_vendor_master,
)
