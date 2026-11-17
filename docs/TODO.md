# To do

The owner's list of what is still to do on AP Coder, newest last. Remove an item once it is done (its bug or
decision number then says what was done).

- **Tip lines told from purchases with a tip word.** `coding/tax.py` `is_tip` calls any line with a tip word a tip
  ("Excel tips workshop", "Pipette tips for lab", "Tip fee at landfill", "Thank you cards"), so such a purchase gets no
  share of the printed tax. Word rules tried on 2026-11-12/13 (B-2540) each lost real tips ("Dinner tip for Mike",
  "Three-course dinner tip", "Cooking class instructor tip"). A better way: decide by the invoice, not the words: a tip
  only on a meals, hotel, taxi or personal-service invoice (by the vendor's usual account or the meals GL), or only a
  line whose amount is a share of the subtotal.
