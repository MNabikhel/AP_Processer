"""JD Edwards set up the way of a department that types its own vouchers (D-210; ``docs/PROTOTYPE.md``): its settings
(``prototype_settings``) and a data folder set up with them in one step (``use_prototype``).
"""

from __future__ import annotations

from typing import Any

from ..coding.tax import PROVINCES
from .jde_settings import (
    AMOUNT_TAX,
    EXPORT_FORMAT,
    FORMATS,
    HST_PROVINCES,
    REMARKS_TYPED,
    JdeSettings,
    TaxArea,
    load,
    save,
    show,
)

# The GST-only tax area of the made-up company (``bench.jde_company.TAX_AREA``): a placeholder, as every area name.
PROTOTYPE_GST_AREA = "CDNGST"


def prototype_settings(base: JdeSettings | None = None) -> JdeSettings:
    """JD Edwards set up as a department that types its own vouchers and recovers the GST/HST only works (D-210): what
    a first-run step saves and test fixtures use. ``JdeSettings()`` keeps its own defaults (existing installs).

    * The tax is on the pay item: Amounts = tax amounts, tax explanation code V for every province.
    * A tax rate/area a province, placeholder names an Admin replaces with the company's own (Settings → JD Edwards
      E1 → Province → tax area): ``CDNGST`` (a GST-only area) for the provinces whose federal tax is the GST (AB,
      BC, MB, SK, QC, NT, NU, YT: their PST or QST stays in the expense), ``<province>HST`` for the HST provinces
      (ONHST, NBHST, NLHST, NSHST, PEHST: their rates differ). Outside Canada: none.
    * The remarks are the ones typed by hand (``REMARKS_TYPED``).
    * The account is the line's own string: the business unit is its cost center, the object and subsidiary its GL
      code's (the default rule, B-2085); no mapping row is needed.

    ``base``: settings already saved, whose other values are kept (the company, the supplier numbers, the terms).
    Not settings of JD Edwards, and needed too: JD Edwards shown (``show``) and the QST not recovered (GL accounts &
    tax): ``use_prototype`` does all three. Left as it was: the code for a PST not charged (B, the self-assessed
    code), which on a GST-only area accrues nothing: to settle with the JDE team for a company that buys in BC, SK
    or MB."""
    settings = JdeSettings.from_json(base.to_json()) if base is not None else JdeSettings()
    settings.amount_mode, settings.remarks = AMOUNT_TAX, REMARKS_TYPED
    settings.use_default_rule = settings.bu_from_cost_center = True
    for province in PROVINCES:
        area = f"{province}HST" if province in HST_PROVINCES else PROTOTYPE_GST_AREA
        settings.tax_map[province] = TaxArea(area, "V")
    return settings


def use_prototype(store: Any, actor: str | None = None) -> JdeSettings:
    """Set a data folder up the department's way in one step (``prototype_settings``, over what is saved): its JD
    Edwards settings saved, JD Edwards shown, and the QST set to "not recoverable: add to the expense line" in GL
    accounts & tax (the department claims the GST/HST only). The page that hands vouchers over opens on the keying
    sheet, unless a format was chosen there before (B-2279). Returns the settings saved."""
    from ..core.tax_setup import EXPENSE_TO_LINE

    settings = prototype_settings(load(store))
    save(store, settings, actor)
    show(store, True, actor)
    store.set_tax_treatment("QST", EXPENSE_TO_LINE, "", actor=actor)
    if not store.get_setting(EXPORT_FORMAT):
        store.set_setting(EXPORT_FORMAT, FORMATS[1])  # the keying sheet (``jde_keying.FORMAT``)
    return settings
