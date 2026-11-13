# To do

The owner's list of what is still to do on AP Coder, newest last. Remove an item once it is done (its bug or
decision number then says what was done).

- **Purchase orders have no currency.** A PO's lines are kept with no currency (`purchase_orders` and `po_lines` in
  `core/store/_base.py`), so the month-end accrual of a USD order's lines received and not yet invoiced is labelled
  CAD (`erp/accruals.py` `_accrued`: an `Accrual` is CAD unless told otherwise) and added to the CAD totals. To do:
  a currency column in the PO import (`erp/import_specs.py`, `core/store/purchasing.py`; CAD when the file has
  none), a schema migration (`core/store/_base.py` `SCHEMA_VERSION`), and the accrual taking the PO's currency.
- **Tip lines told from purchases with a tip word.** `coding/tax.py` `is_tip` calls any line with a tip word a tip
  ("Excel tips workshop", "Pipette tips for lab", "Tip fee at landfill", "Thank you cards"), so such a purchase gets no
  share of the printed tax. Word rules tried on 2026-11-12/13 (B-2540) each lost real tips ("Dinner tip for Mike",
  "Three-course dinner tip", "Cooking class instructor tip"). A better way: decide by the invoice, not the words: a tip
  only on a meals, hotel, taxi or personal-service invoice (by the vendor's usual account or the meals GL), or only a
  line whose amount is a share of the subtotal.
