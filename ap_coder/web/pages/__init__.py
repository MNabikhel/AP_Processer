"""Every page of the dashboard, in the order of the left rail (see ``ap_coder/web/__init__.py`` for how to add one).

``PAGES`` is the single list: the rail is drawn from it, and ``create_app`` includes each page's module. A page with
no ``module`` yet is answered by the placeholder template, so every link in the rail works while pages are built.
``MORE_MODULES`` are modules with routes but no place in the rail (the invoice review screen opens from the queue).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Page:
    key: str  # the page's name in code and templates ("review"); its rail link is highlighted when page == key
    title: str  # the rail label and the page title
    path: str  # where it lives ("/review")
    icon: str  # a Material Symbols name for the rail
    section: str  # the rail group, also the page header's breadcrumb
    subtitle: str = ""  # the one-line description under the page title
    module: str = ""  # ap_coder.web.pages.<module>, which has a ``router``; "" until the page is built


W, C, M, A, S = "Work", "Close & compliance", "Master data", "Analytics", "System"

PAGES: list[Page] = [
    Page("review", "Review queue", "/review", "inbox", W, "Invoices waiting for review.", module="review"),
    Page(
        "process",
        "Import invoices",
        "/process",
        "upload_file",
        W,
        "Bring invoices in by hand: upload files or saved emails, or pick up new files in the invoices folder.",
        module="process",
    ),
    Page(
        "search",
        "Find an invoice",
        "/search",
        "search",
        W,
        "Vendor on the phone? Search by vendor, invoice number, PO or amount: see where it is and when it is due.",
        module="search",
    ),
    Page(
        "exports",
        "Exports",
        "/exports",
        "ios_share",
        C,
        "Hand approved invoices to the ERP in batches. Each invoice goes out once; any batch can be downloaded again.",
        module="exports",
    ),
    Page(
        "month_end",
        "Month-end",
        "/month-end",
        "event_available",
        C,
        "What to accrue: goods received but not invoiced, invoices not in the ERP yet, and regular bills not arrived.",
        module="month_end",
    ),
    Page(
        "sales_tax",
        "Sales tax",
        "/sales-tax",
        "percent",
        C,
        "The GST/HST and QST you paid to vendors and can claim back on your return, from approved invoices.",
        module="sales_tax",
    ),
    Page(
        "statements",
        "Vendor statements",
        "/statements",
        "fact_check",
        C,
        "Compare a vendor's statement with the invoices you hold: find missing invoices and amount differences.",
        module="statements",
    ),
    Page(
        "vendors",
        "Vendors",
        "/vendors",
        "storefront",
        M,
        "Everyone who has sent an invoice: spend, how well the AI codes them, and your controls.",
        module="vendors",
    ),
    Page(
        "purchase_orders",
        "Purchase orders",
        "/purchase-orders",
        "shopping_cart",
        M,
        "Invoices that quote a PO are checked against it: price, quantity ordered and received, and coding.",
        module="purchase_orders",
    ),
    Page(
        "accounts",
        "GL accounts & tax",
        "/accounts",
        "account_tree",
        M,
        "The codes the AI may use, how each sales tax posts, and your rules.",
        module="accounts",
    ),
    Page(
        "learning",
        "Learning & accuracy",
        "/learning",
        "insights",
        A,
        "How often the coding is right, and what AP Coder has learned.",
        module="learning",
    ),
    Page(
        "spend",
        "Spend",
        "/spend",
        "donut_small",
        A,
        "Where the money goes, by month, GL account, cost center and vendor, from approved invoices.",
        module="spend",
    ),
    Page(
        "insights",
        "Insights",
        "/insights",
        "query_stats",
        A,
        "The business case: how much goes straight through, the time it saves and what it costs.",
        module="insights",
    ),
    Page(
        "activity",
        "Activity",
        "/activity",
        "history",
        A,
        "Who processed, changed, approved, rejected, deleted or exported what, and when.",
        module="activity",
    ),
    Page(
        "settings",
        "Settings",
        "/settings",
        "settings",
        S,
        "How invoices are read, touchless processing, review behaviour, JD Edwards E1, your data and backups.",
        module="settings",
    ),
    Page(
        "help",
        "Help",
        "/help",
        "help",
        S,
        "How AP Coder works, what each check means and what to do about it.",
        module="help",
    ),
]

# Routes without a rail entry: the invoice review screen (/review/{id}); the Azure check's card on Settings → Reading
# (/settings/azure-check).
MORE_MODULES: list[str] = ["invoice", "azure"]

SECTIONS: list[str] = list(dict.fromkeys(p.section for p in PAGES))
BY_KEY: dict[str, Page] = {p.key: p for p in PAGES}
BANNER_PAGES = {"process", "review"}  # where OvisOCR2's one-line warning shows under the page header


def page(key: str) -> Page:
    """The page called ``key`` (KeyError for a page that does not exist)."""
    return BY_KEY[key]
