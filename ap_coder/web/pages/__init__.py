"""Every page of the dashboard, in the order of the left rail (see ``ap_coder/web/__init__.py`` for how to add one).

``PAGES`` is the single list: the rail is drawn from it, and ``create_app`` includes each page's module. A page with
no ``module`` yet is answered by the placeholder template, so every link in the rail works while pages are built.
``MORE_MODULES`` are modules with routes but no place in the rail (the invoice review screen opens from the queue).

The rail shows the pages a pilot tester uses every day (``more=False``) at the top, Settings and Help pinned at its
foot, and the other pages folded under "More" (D-52).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Page:
    key: str  # the page's name in code and templates ("review"); its rail link is highlighted when page == key
    title: str  # the rail label and the page title
    path: str  # where it lives ("/review")
    icon: str  # a Material Symbols name for the rail
    section: str  # the page header's breadcrumb
    subtitle: str = ""  # the one-line description under the page title
    module: str = ""  # ap_coder.web.pages.<module>, which has a ``router``; "" until the page is built
    more: bool = False  # folded under "More" in the rail


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
        "Invoice directory",
        "/search",
        "manage_search",
        W,
        "Every invoice in one table: filter any column (vendor, number, PO, dates, amounts, status, GL account, "
        "approver, export batch), sort, choose the columns, save the layout, export what you see.",
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
        "accounts",
        "GL accounts & tax",
        "/accounts",
        "account_tree",
        M,
        "The codes the AI may use, how each sales tax posts, and your rules.",
        module="accounts",
    ),
    Page(
        "month_end",
        "Month-end",
        "/month-end",
        "event_available",
        C,
        "What to accrue: goods received but not invoiced, invoices not in the ERP yet, and regular bills not arrived.",
        module="month_end",
        more=True,
    ),
    Page(
        "sales_tax",
        "Sales tax",
        "/sales-tax",
        "percent",
        C,
        "The GST/HST and QST you paid to vendors and can claim back on your return, from approved invoices.",
        module="sales_tax",
        more=True,
    ),
    Page(
        "statements",
        "Vendor statements",
        "/statements",
        "fact_check",
        C,
        "Compare a vendor's statement with the invoices you hold: find missing invoices and amount differences.",
        module="statements",
        more=True,
    ),
    Page(
        "vendors",
        "Vendors",
        "/vendors",
        "storefront",
        M,
        "Everyone who has sent an invoice: spend, how well the AI codes them, and your controls.",
        module="vendors",
        more=True,
    ),
    Page(
        "purchase_orders",
        "Purchase orders",
        "/purchase-orders",
        "shopping_cart",
        M,
        "Invoices that quote a PO are checked against it: price, quantity ordered and received, and coding.",
        module="purchase_orders",
        more=True,
    ),
    Page(
        "learning",
        "Learning & accuracy",
        "/learning",
        "insights",
        A,
        "How often the coding is right, and what AP Coder has learned.",
        module="learning",
        more=True,
    ),
    Page(
        "spend",
        "Spend",
        "/spend",
        "donut_small",
        A,
        "Where the money goes, by month, GL account, cost center and vendor, from approved invoices.",
        module="spend",
        more=True,
    ),
    Page(
        "insights",
        "Insights",
        "/insights",
        "query_stats",
        A,
        "How much goes straight through and the time it saves.",
        module="insights",
        more=True,
    ),
    Page(
        "activity",
        "Activity",
        "/activity",
        "history",
        A,
        "Who processed, changed, approved, rejected, deleted or exported what, and when.",
        module="activity",
        more=True,
    ),
    Page(
        "settings",
        "Settings",
        "/settings",
        "settings",
        S,
        "Review behaviour, how invoices are read, touchless processing, your data and backups.",
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
# (/settings/azure-check); the "Who are you?" page of a colleague on the office network (/who); in server mode
# (D-97), logging in and out and one's own password (/login, /logout, /account/password) and the Users page
# (/admin/users).
MORE_MODULES: list[str] = ["invoice", "azure", "who", "login", "users"]

SECTIONS: list[str] = list(dict.fromkeys(p.section for p in PAGES))
BY_KEY: dict[str, Page] = {p.key: p for p in PAGES}
PINNED = {"settings", "help"}  # at the rail's foot, always on screen
BANNER_PAGES = {"process", "review"}  # where OvisOCR2's one-line warning shows under the page header


def page(key: str) -> Page:
    """The page called ``key`` (KeyError for a page that does not exist)."""
    return BY_KEY[key]
