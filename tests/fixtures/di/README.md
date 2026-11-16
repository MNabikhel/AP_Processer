# Made-up Document Intelligence answers

Each `<sample>.json` is the `analyzeResult` Azure AI Document Intelligence's `prebuilt-invoice` model gives at API
version 2024-11-30, written by hand for the made-up sample invoice of the same name in `samples/` (its values from
`samples/ground_truth/`, each field's box where the PDF prints it, in inches). None came from Azure, and none is a
company's invoice. The pages' words, lines and spans are left out to keep the files small: AP Coder reads only the
fields.

Deliberate differences from the samples' ground truth, for the tests:

- `laurentides_QC_TPS_TVQ_SIL-4471`: `VendorTaxId` is the QST number (it routes to `qst_registration_number`); no
  `TotalTax`, the GST and QST only in `TaxDetails` (5 % and 9,975 %).
- `pacific_BC_GST_PST_PO-77120`: no `TotalTax`; GST 5% and PST 7% in `TaxDetails`.
- `cascade_US_SalesTax_INV-30981`: in US dollars, a 10.35% sales tax no Canadian rate matches.

`tests/fake_di.py` answers with them, and `tests/ai/test_di_http.py`, `tests/capture/test_di_reader.py` read them.
