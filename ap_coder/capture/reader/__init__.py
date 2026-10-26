"""Rule reader: finds invoice header fields from labels, patterns and positions on the page.

For every field it returns candidate readings, best first, each with the box of the words it read.
It knows English and French labels, glued OCR text ("InvoiceNo:"), label-then-value on one line,
value to the right of the label, value below it (also header grids), and it avoids the usual traps
(order and ship dates, PO Box, customer numbers, previous balances, registration numbers next to tax
amounts). Totals are chosen together: the subtotal, taxes and total that add up win.

Its parts (D-69), each using only those before it: ``_common`` (shared words and helpers), ``labels``, ``values``
(the value beside or under a label), ``spatial``, ``patterns`` (fields read from their shape), ``totals`` (the solver),
``line_items``, ``entry`` (``read_fields``). Re-exported here: the names used outside the package (D-115); any other
is imported from its part.
"""

from ._common import (  # noqa: F401
    _LABEL_SHAPE,
    _table_header_line,
    plain,
    us_address,
)
from .entry import (  # noqa: F401
    _glued_title_numbers,
    read_fields,
)
from .labels import (  # noqa: F401
    _LABELS,
    _PERCENT,
    _combined_tax_fields,
    _distracted,
)
from .line_items import (  # noqa: F401
    _CARRIED,
    _CREDIT_TITLE,
    _NUMBER_CELL,
    _THREE_PLACES,
    STRAIGHTEN_FROM,
    _heading_cells,
    _on_tilted_page,
    credit_printed_positive,
    credit_titled,
    heading_row,
    inside_tables,
    listed_items,
    read_line_items,
    spaced_whole,
    straightened,
    tax_code_column,
    tax_flag_cell,
)
from .patterns import (  # noqa: F401
    _CONTACT,
    _CUSTOMER_WORD,
    _bare_business_numbers,
    _in_bill_to,
    _not_a_name,
    _without_logo_initials,
)
from .values import (  # noqa: F401
    _STREET,
    _TERMS,
    _amounts_in,
    _id_from,
    _label_hits,
    _ocr_name_fix,
    _span_words,
    _terms,
    _value_reading,
)
