"""Every page of the dashboard, in the order of the left rail (see ``ap_coder/web/__init__.py`` for how to add one).

``PAGES`` is the single list: the rail is drawn from it, and ``create_app`` includes each page's module.
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
    subtitle: str = ""  # the one-line description under the page title
    module: str = ""  # ap_coder.web.pages.<module>, which has a ``router``
    more: bool = False  # folded under "More" in the rail


PAGES: list[Page] = [
    Page("review", "To review", "/review", "inbox", "Invoices waiting for review.", module="review"),
    Page(
        "process",
        "Add invoices",
        "/process",
        "upload_file",
        "Bring invoices in by hand: upload files or saved emails, or pick up new files in the invoices folder.",
        module="process",
    ),
    Page(
        "search",
        "Find an invoice",
        "/search",
        "manage_search",
        "Every invoice in one table: filter any column (vendor, number, PO, dates, amounts, status, GL account, "
        "approver, export batch), sort, choose the columns, save the layout, export what you see.",
        module="search",
    ),
    Page(
        "exports",
        "Exports",
        "/exports",
        "ios_share",
        "Hand approved invoices to the ERP in batches. Each invoice goes out once; any batch can be downloaded again.",
        module="exports",
    ),
    Page(
        "accounts",
        "GL accounts & tax",
        "/accounts",
        "account_tree",
        "The codes the AI may use, how each sales tax posts, and your coding policy.",
        module="accounts",
    ),
    Page(
        "rules",
        "Rules",
        "/rules",
        "rule_settings",
        "Lines that always go to the same account: the rules, every change made to them, and what each one coded.",
        module="rules",
        more=True,
    ),
    Page(
        "manager",
        "Manager confirmations",
        "/manager",
        "how_to_reg",
        "Invoices sent to a manager to confirm their cost allocation: the ones still waiting, and what to do next.",
        module="manager",
        more=True,
    ),
    Page(
        "month_end",
        "Month-end",
        "/month-end",
        "event_available",
        "What to accrue: goods received but not invoiced, invoices not in the ERP yet, and regular bills not arrived.",
        module="month_end",
        more=True,
    ),
    Page(
        "sales_tax",
        "Sales tax",
        "/sales-tax",
        "percent",
        "The GST/HST and QST you paid to vendors and can claim back on your return, from approved invoices.",
        module="sales_tax",
        more=True,
    ),
    Page(
        "statements",
        "Vendor statements",
        "/statements",
        "fact_check",
        "Compare a vendor's statement with the invoices you hold: find missing invoices and amount differences.",
        module="statements",
        more=True,
    ),
    Page(
        "jde_files",
        "JD Edwards files",
        "/jde-files",
        "drive_folder_upload",
        "Load JD Edwards' own exports: the chart of accounts, the business units, the suppliers, the past invoice "
        "coding and the vouchers already entered.",
        module="jde_files",
        more=True,
    ),
    Page(
        "vendors",
        "Vendors",
        "/vendors",
        "storefront",
        "Everyone who has sent an invoice: spend, how well the AI codes them, and your controls.",
        module="vendors",
        more=True,
    ),
    Page(
        "purchase_orders",
        "Purchase orders",
        "/purchase-orders",
        "shopping_cart",
        "Invoices that quote a PO are checked against it: price, quantity ordered and received, and coding.",
        module="purchase_orders",
        more=True,
    ),
    Page(
        "learning",
        "What was learned",
        "/learning",
        "insights",
        "How often the coding is right, and what AP Coder has learned.",
        module="learning",
        more=True,
    ),
    Page(
        "spend",
        "Spend",
        "/spend",
        "donut_small",
        "Where the money goes, by month, GL account, cost center and vendor, from approved invoices.",
        module="spend",
        more=True,
    ),
    Page(
        "insights",
        "Insights",
        "/insights",
        "query_stats",
        "How much goes straight through and the time it saves.",
        module="insights",
        more=True,
    ),
    Page(
        "activity",
        "Activity",
        "/activity",
        "history",
        "Who processed, changed, approved, rejected, deleted or exported what, and when.",
        module="activity",
        more=True,
    ),
    Page(
        "settings",
        "Settings",
        "/settings",
        "settings",
        "Review behaviour, how invoices are read, touchless processing, your data and backups.",
        module="settings",
    ),
    Page(
        "help",
        "Help",
        "/help",
        "help",
        "How AP Coder works, what each check means and what to do about it.",
        module="help",
    ),
]

# Routes without a rail entry: the invoice review screen (/review/{id}); the Azure check's card on Settings → Reading
# (/settings/azure-check); the "Who are you?" page of a colleague on the office network (/who); in server mode
# (D-97), logging in and out and one's own password (/login, /logout, /account/password) and the Users page
# (/admin/users); the Admin panel (/admin, a person's page, Sign-in: D-249; Roles, ``admin_roles``: D-250), its one
# entry drawn by base.html.
# ``jde_loaded``: the rows of what the JD Edwards files page loaded (/jde-files/loaded/..., D-221).
MORE_MODULES: list[str] = ["invoice", "azure", "who", "login", "users", "admin", "admin_roles", "downloads",
                           "jde_loaded"]  # fmt: skip

BY_KEY: dict[str, Page] = {p.key: p for p in PAGES}
PINNED = {"settings", "help"}  # at the rail's foot, always on screen

# Simple screens (Settings → About → "What is shown", ``core.simple_screens``): the menu of one person coding invoices
# to JD Edwards account strings. Its everyday pages in the menu's order (the coding rules among them: anyone changes
# them, D-217), then the pages under "Setup", then Help. Every other page is left out of the menu, not removed: its
# address still opens.
SIMPLE_MAIN: tuple[str, ...] = ("review", "process", "exports", "search", "learning", "rules")
SIMPLE_SETUP: tuple[str, ...] = ("accounts", "jde_files", "vendors", "activity", "settings")
SIMPLE_KEYS = {*SIMPLE_MAIN, *SIMPLE_SETUP, "help"}
# With JD Edwards (Settings → About, ``core.jde_shown``) and in Simple screens, in every menu and page title: JD
# Edwards' own words (a supplier, not a vendor), and the exports are what AP keys into it (``core.page_meta``).
JDE_TITLES: dict[str, str] = {"exports": "To key into JDE", "vendors": "Suppliers",
                              "statements": "Supplier statements"}  # fmt: skip
BANNER_PAGES = {"process", "review"}  # where OvisOCR2's one-line warning shows under the page header
