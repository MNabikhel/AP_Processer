"""Plain-English help for every check AP Coder runs: what it means and what to do about it.

Shown next to each finding on the review screen and as a searchable list on the Help page. A test
makes sure every code the engine can raise has an entry here.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CheckHelp:
    area: str
    title: str
    meaning: str
    action: str


FRAUD, TOTALS, CODING, TAX, PO, READING = (
    "Duplicates & fraud", "Totals", "GL coding", "Sales tax", "Purchase orders", "Reading the document",
)  # fmt: skip
AREAS = (FRAUD, PO, TOTALS, CODING, TAX, READING)

CHECKS: dict[str, CheckHelp] = {
    # --- Duplicates & fraud ---------------------------------------------------------------------------
    "DUPLICATE_INVOICE": CheckHelp(
        FRAUD, "Possible duplicate",
        "Another invoice from the same vendor has the same invoice number (ignoring dashes, leading zeros and "
        "prefixes such as INV).",
        "Open the other invoice. If it is the same bill, reject this one so it is not paid twice.",
    ),
    "POSSIBLE_DUPLICATE_AMOUNT": CheckHelp(
        FRAUD, "Same amount, different number",
        "The vendor billed exactly the same total shortly before, under another invoice number. A re-sent "
        "invoice with a new number is a common way to get paid twice.",
        "Compare the two invoices. Approve only if they are genuinely separate purchases.",
    ),
    "VENDOR_ON_HOLD": CheckHelp(
        FRAUD, "Vendor on hold",
        "AP put this vendor on hold on the Vendors page (the note there says why).",
        "Do not approve until the hold is lifted on the Vendors page, or reject the invoice.",
    ),
    "VENDOR_TAX_NUMBER_CHANGED": CheckHelp(
        FRAUD, "GST/HST number changed",
        "The vendor's GST/HST number differs from earlier approved invoices or from the number recorded on the "
        "Vendors page. Fake invoices often imitate a real supplier with different details.",
        "Call the vendor on a number you already have (not the one on this invoice) to confirm before paying.",
    ),
    "AMOUNT_UNUSUAL": CheckHelp(
        FRAUD, "Unusually large",
        "The total is far above what this vendor usually bills.",
        "Confirm the purchase with whoever ordered it.",
    ),
    "VENDOR_NEW": CheckHelp(
        FRAUD, "First invoice from this vendor",
        "No earlier invoice from this vendor is in AP Coder. Information only.",
        "Make sure the vendor is set up in the ERP and is a supplier you expect.",
    ),
    # --- Purchase orders ---------------------------------------------------------------------------------
    "PO_UNKNOWN": CheckHelp(
        PO, "PO not found",
        "The invoice quotes a purchase order that is not in the Purchase orders list.",
        "Check the PO number for typos, or import the latest open POs from the ERP.",
    ),
    "PO_CLOSED": CheckHelp(
        PO, "PO is closed",
        "The purchase order quoted on the invoice has been closed.",
        "Ask the buyer whether the PO should be reopened or the invoice belongs to another PO.",
    ),
    "PO_VENDOR_MISMATCH": CheckHelp(
        PO, "PO is for another vendor",
        "The quoted purchase order was raised for a different vendor.",
        "Check the PO number on the invoice; a wrong PO is a common reason for misallocated spend.",
    ),
    "PO_LINE_NOT_ON_PO": CheckHelp(
        PO, "Line not on the PO",
        "Nothing on the purchase order resembles this invoice line.",
        "Confirm the charge was agreed (e.g. extra freight or a rush fee) before approving.",
    ),
    "PO_PRICE_OVER": CheckHelp(
        PO, "Price above the PO",
        "The unit price is higher than the price on the purchase order (beyond 2% or 50 cents).",
        "Ask the buyer or the vendor about the increase; short-pay or request a credit if it was not agreed.",
    ),
    "PO_QTY_OVER": CheckHelp(
        PO, "More than ordered",
        "Counting earlier invoices on this PO, more has now been billed than was ordered.",
        "Look at the earlier invoices listed on the review screen: this may be a duplicate or an over-shipment.",
    ),
    "PO_NOT_RECEIVED": CheckHelp(
        PO, "Not received yet",
        "More has been billed than the ERP shows as received (three-way match).",
        "Check with receiving. Hold the invoice until the goods arrive, or approve only what was received.",
    ),
    "PO_OVER_BILLED": CheckHelp(
        PO, "PO total exceeded",
        "Invoices on this PO now add up to more than the PO total (beyond 2%).",
        "Ask the buyer for a PO change, or dispute the extra amount with the vendor.",
    ),
    "PO_CODING_DIFFERS": CheckHelp(
        PO, "PO is coded differently",
        "The purchase order line carries a GL account or cost center different from the invoice line.",
        "The PO coding is usually right: use the 'Use the PO's coding' button under the PO match.",
    ),
    "PO_NOT_QUOTED": CheckHelp(
        PO, "No PO on the invoice",
        "The invoice quotes no PO number but this vendor has open purchase orders.",
        "If the invoice belongs to one of them, type the PO number in the PO # field to match it.",
    ),
    "PO_MATCHED": CheckHelp(
        PO, "Matches the PO",
        "Every line matched the purchase order within price and quantity tolerances.",
        "Nothing to do.",
    ),
    # --- Totals --------------------------------------------------------------------------------------------
    "SUBTOTAL_MISMATCH": CheckHelp(
        TOTALS, "Lines don't add up",
        "The line amounts do not add up to the subtotal printed on the invoice.",
        "Compare the lines with the document: a line was probably missed, duplicated or misread.",
    ),
    "TOTAL_MISMATCH": CheckHelp(
        TOTALS, "Total doesn't add up",
        "Subtotal plus tax does not equal the invoice total.",
        "Check the subtotal, tax lines and total against the document.",
    ),
    "POSTING_UNBALANCED": CheckHelp(
        TOTALS, "Posting doesn't balance",
        "The GL posting would not equal the amount payable, so the ERP entry would not balance.",
        "Fix the line or tax amount the message points to before approving.",
    ),
    "LINE_MATH": CheckHelp(
        TOTALS, "Quantity × price ≠ amount",
        "On this line, quantity times unit price differs from the amount (by more than rounding).",
        "Correct the quantity, unit price or amount to match the document.",
    ),
    "LINE_NUMBERING": CheckHelp(
        TOTALS, "Line numbers out of order",
        "Line numbers are not 1, 2, 3… which can mean a line was skipped.",
        "Make sure every line of the document is present.",
    ),
    "NO_LINE_ITEMS": CheckHelp(
        TOTALS, "No lines",
        "No line items were read from the document.",
        "Add the lines by hand, or reject the file if it is not an invoice.",
    ),
    "MISSING_VENDOR": CheckHelp(TOTALS, "No vendor", "The vendor name is empty.", "Type the vendor name."),
    "MISSING_INVOICE_NUMBER": CheckHelp(
        TOTALS, "No invoice number", "The invoice number is empty, so duplicates cannot be detected.",
        "Type the invoice number from the document.",
    ),
    "CURRENCY_FORMAT": CheckHelp(
        TOTALS, "Unusual currency", "The currency is not a three-letter code such as CAD or USD.",
        "Correct the currency.",
    ),
    # --- GL coding -------------------------------------------------------------------------------------
    "GL_UNASSIGNED": CheckHelp(
        CODING, "No GL account",
        "The AI could not decide on a GL account for this line.",
        "Pick the GL account in the line table; approving teaches the AI for next time.",
    ),
    "GL_UNKNOWN": CheckHelp(
        CODING, "GL account not in your list",
        "The GL account is not in your GL accounts (it may have been removed or mistyped).",
        "Pick an account from the list, or add the account on the GL accounts & tax page.",
    ),
    "GL_IS_TAX_ACCOUNT": CheckHelp(
        CODING, "Expense coded to a tax account",
        "An expense line is coded to a sales-tax GL account. Tax is posted from the tax lines, not the lines.",
        "Pick the expense or asset account for this line.",
    ),
    "CC_UNASSIGNED": CheckHelp(
        CODING, "No cost center", "The AI could not decide on a cost center for this line.",
        "Pick the cost center in the line table.",
    ),
    "CC_UNKNOWN": CheckHelp(
        CODING, "Cost center not in your list", "The cost center is not in your cost centers.",
        "Pick a cost center from the list, or add it on the GL accounts & tax page.",
    ),
    "HISTORY_CONFLICT": CheckHelp(
        CODING, "Coded differently before",
        "Reviewers coded similar lines from this vendor to another GL account in the past.",
        "If the past coding is right, change the GL account; if this purchase is different, approve as is.",
    ),
    # --- Sales tax ------------------------------------------------------------------------------------
    "TAX_CALC_MISMATCH": CheckHelp(
        TAX, "Tax miscalculated",
        "Taxable amount × rate does not give the tax amount charged.",
        "Check the tax line against the document; if the vendor miscalculated, ask for a corrected invoice.",
    ),
    "TAX_LINES_TOTAL_MISMATCH": CheckHelp(
        TAX, "Tax lines don't add up", "The tax lines do not add up to the tax total.",
        "Check each tax line and the tax total against the document.",
    ),
    "TAX_RATE_NONSTANDARD": CheckHelp(
        TAX, "Unusual tax rate",
        "The rate differs from the official rate for that province on the invoice date.",
        "Confirm the rate on the document; a wrong rate means the vendor charged the wrong tax.",
    ),
    "TAX_TYPE_NOT_LEVIED": CheckHelp(
        TAX, "Tax that doesn't exist there",
        "The invoice charges a tax that the province does not levy (e.g. PST in Alberta).",
        "Ask the vendor for a corrected invoice; the tax cannot be recovered.",
    ),
    "TAX_PROVINCE_DIFFERS": CheckHelp(
        TAX, "Tax of another province",
        "A provincial tax belongs to a province other than the place of supply.",
        "Check the ship-to address. The tax should follow where the goods or services are delivered.",
    ),
    "TAX_PROVINCE_UNKNOWN": CheckHelp(
        TAX, "Tax province unknown", "The province of a tax line is not known, so its rate cannot be checked.",
        "Set the province on the tax line.",
    ),
    "PROVINCE_UNKNOWN": CheckHelp(
        TAX, "Place of supply unknown",
        "The province where goods are delivered or services performed is unknown, so the tax cannot be checked.",
        "Set the place of supply in the invoice details.",
    ),
    "TAX_REGIME_MISMATCH": CheckHelp(
        TAX, "Wrong taxes for the province",
        "The taxes charged are not the ones that province uses (e.g. GST + PST charged in an HST province).",
        "Check the place of supply; if it is right, ask the vendor for a corrected invoice.",
    ),
    "NO_TAX_CHARGED": CheckHelp(
        TAX, "No tax charged",
        "No sales tax is charged on a Canadian supply.",
        "Confirm the items are exempt or zero-rated (e.g. basic groceries, some services); otherwise query it.",
    ),
    "PROVINCIAL_TAX_NOT_CHARGED": CheckHelp(
        TAX, "Provincial tax not charged",
        "GST was charged but not the provincial sales tax of the place of supply.",
        "If the item is taxable, PST may have to be self-assessed and remitted; check with tax.",
    ),
    "TAX_NOT_ALLOCATED": CheckHelp(
        TAX, "Tax on no line", "A tax is charged but no line is marked as subject to it.",
        "Tick the tax in the Taxes column of the lines it applies to.",
    ),
    "TAX_BASE_MISMATCH": CheckHelp(
        TAX, "Taxable amount doesn't match the lines",
        "The lines marked with a tax do not add up to that tax's taxable amount.",
        "Fix the Taxes column on the lines so the right lines carry the tax.",
    ),
    "QST_ON_GST_INCLUSIVE": CheckHelp(
        TAX, "QST calculated on GST",
        "QST was calculated on an amount that includes GST. Since 2013 QST is calculated on the price "
        "before GST.",
        "Ask the vendor for a corrected invoice.",
    ),
    "TAX_NON_CANADIAN": CheckHelp(
        TAX, "Foreign tax",
        "A non-Canadian tax (e.g. US sales tax) was charged. It cannot be recovered and is added to the cost of "
        "the lines.",
        "Confirm it should have been charged; a US vendor may not need to charge it on an export.",
    ),
    "GST_HST_NUMBER_MISSING": CheckHelp(
        TAX, "No GST/HST number",
        "GST/HST is charged but the vendor's registration number was not found. Without it the input tax "
        "credit can be denied in an audit.",
        "Type the number from the document, or ask the vendor for it.",
    ),
    "GST_HST_NUMBER_FORMAT": CheckHelp(
        TAX, "GST/HST number looks wrong",
        "The GST/HST number is not in the format 123456789 RT 0001.",
        "Check it against the document; you can verify numbers on the CRA GST/HST registry.",
    ),
    "QST_NUMBER_MISSING": CheckHelp(
        TAX, "No QST number",
        "QST is charged but the vendor's QST number was not found (needed to claim the refund).",
        "Type the number from the document, or ask the vendor for it.",
    ),
    "QST_NUMBER_FORMAT": CheckHelp(
        TAX, "QST number looks wrong", "The QST number is not in the format 1234567890 TQ 0001.",
        "Check it against the document; you can verify numbers on Revenu Québec's registry.",
    ),
    "TAX_GL_UNMAPPED": CheckHelp(
        TAX, "Tax has no GL account",
        "A tax is charged that has no GL account set up, so it cannot be posted.",
        "Set the tax's treatment and GL account on the GL accounts & tax page.",
    ),
    "TAX_GL_UNKNOWN": CheckHelp(
        TAX, "Tax GL account missing",
        "The GL account set for this tax is not in your GL accounts.",
        "Fix the tax setup on the GL accounts & tax page.",
    ),
    # --- Reading the document -------------------------------------------------------------------------
    "LOW_OCR_CONFIDENCE": CheckHelp(
        READING, "Hard to read",
        "The scan was hard to read (blurry, skewed or faint), so numbers may be misread.",
        "Compare the amounts with the document carefully.",
    ),
    "DI_INVOICE_ID_DIFFERS": CheckHelp(
        READING, "Invoice number read differently",
        "Azure's invoice model and the AI read different invoice numbers.",
        "Check the invoice number against the document.",
    ),
    "DI_DATE_DIFFERS": CheckHelp(
        READING, "Date read differently", "Azure's invoice model and the AI read different invoice dates.",
        "Check the date (watch for day/month order).",
    ),
    "DI_AMOUNT_DIFFERS": CheckHelp(
        READING, "Amount read differently", "Azure's invoice model and the AI read a different amount.",
        "Check the subtotal, tax and total against the document.",
    ),
}  # fmt: skip


def help_for(code: str) -> CheckHelp | None:
    return CHECKS.get(code)


FAQ: list[tuple[str, str]] = [
    (
        "Does any invoice or GL data leave this computer?",
        "Only to your organisation's own Azure resources: the document goes to Azure Document Intelligence and "
        "the text to your Azure OpenAI deployment, both in your tenant. The database, the invoices, what the AI "
        "learns and the exports stay in the private folder on this computer.",
    ),
    (
        "How does the AI learn?",
        "Each approval records, per line, whether you kept or changed the AI's GL account and cost center. Next "
        "time an invoice from the same vendor comes in, the closest past decisions are shown to the AI as "
        "examples, and a line coded differently from before is flagged. The Learning & accuracy page shows the "
        "trend; a wrong lesson can be forgotten there.",
    ),
    (
        "What is the difference between errors, warnings and 'good to know'?",
        "Errors must be fixed (or explicitly overridden) before approving. Warnings deserve a look and send the "
        "invoice to the top of the queue. 'Good to know' notes are information only and never block anything.",
    ),
    (
        "Which invoices can be approved in bulk?",
        "Only those with no error, no warning and an AI confidence above the review threshold, re-checked at "
        "the moment you click. Everything else is skipped with the reason and still needs a person.",
    ),
    (
        "How do I get approved invoices into the ERP?",
        "On the Exports page, pick the approved invoices and export them as a batch (Excel or CSV). Each "
        "invoice goes out once. If the ERP import fails, undo the batch and export again.",
    ),
    (
        "How does PO matching work?",
        "Import your open POs (one row per PO line) on the Purchase orders page. When an invoice quotes a PO "
        "number, each line is paired with the PO line it bills for and price, quantity ordered and, if the file "
        "has it, quantity received are compared, counting earlier invoices on the same PO. Re-import the ERP "
        "export regularly to keep received quantities current.",
    ),
    (
        "I made a mistake. Can I undo it?",
        "Approvals record what was learned; you can forget lessons on the Learning page. Export batches can be "
        "undone on the Exports page. For anything else, Settings → Data & backups restores the database to a "
        "backup (one is made automatically each day the dashboard is opened, and before any restore).",
    ),
    (
        "Who did what?",
        "The Activity page lists every action with who did it and when: processing, approvals with each change "
        "made to the AI's coding, rejections, exports, setup changes and backups. It can be downloaded as CSV.",
    ),
    (
        "The connection to Azure fails.",
        "Open Settings → Azure and click 'Run the test'. It checks each setting and says which one is wrong "
        "(endpoint, key, deployment name or network). Keys are stored in the .env file on this computer only.",
    ),
]

QUICK_START: list[tuple[str, str]] = [
    ("Set up your accounts", "Import your GL accounts (and cost centers) and say how each sales tax is posted."),
    ("Add purchase orders (optional)", "Import open POs to check invoices against what was ordered and received."),
    ("Process invoices", "Drop PDFs or photos on the Process page. Each is read, coded and checked."),
    ("Review", "Work through the queue: fix what the checks point to, then Approve & teach (Ctrl+Enter)."),
    ("Export", "Send approved invoices to the ERP in batches from the Exports page."),
]
