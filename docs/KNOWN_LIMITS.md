# Known limits of the laptop prototype

What this version does not do yet, or has not been tried on. Each is a next step, not a surprise in a demo. The plan
that turns them into decisions: [PILOT_PLAN.md](PILOT_PLAN.md).

1. **One laptop, one person at a time.** The central server version (logins, roles, HTTPS, a Windows service:
   [SERVER.md](SERVER.md)) is built and tested but not set up anywhere yet.
2. **Speed without a graphics card.** OvisOCR2 reads a page in a few minutes on a laptop processor, in the
   background; the chat model takes 1 to 6 minutes for an invoice whose lines it must code. A line learned from an
   approval needs no model. A graphics card is many times faster.
3. **Sample data only.** The sample GL accounts (no cost centers), no vendor list, no purchase orders. A company's
   own GL accounts, cost centers, vendor master, tax accounts and POs are imported from CSV or Excel.
4. **JD Edwards E1 not tried against a real E1.** The batch-voucher files (F0411Z1 / F0911Z1 for R04110ZA) and the
   AIS connection are built and tested here, hidden until switched on; E1's own values (company, document type,
   account mapping, tax areas) come from the JDE team through the worksheet ([JDE_E1.md](JDE_E1.md)).
5. **No direct mailbox connection.** Invoices come in by upload, or as saved emails (.eml, .msg) and files that a
   company tool drops in the invoices folder ([MAILBOX_SETUP.md](MAILBOX_SETUP.md)).
6. **Accuracy measured on samples, not on a company's invoices.** Every invoice waits for a person (touchless
   processing is off) until each vendor has a track record.
7. **Coding is a GL account (and cost center) per line.** A line can be split across several accounts, by
   percentage or amount; job cost coding (job, cost code, cost type) is not built.
8. **Scans and photos** are read by local OCR and OvisOCR2; handwriting and very poor scans need a person.
9. **Python 3.11 or newer must be installed** on the laptop (IT installs it); the offline bundle brings everything
   else and never downloads anything.
10. **Not yet reviewed by a company's IT security team.** It runs offline by design: it opens nothing to the
    network and sends nothing out ([HOW_IT_RUNS.md](HOW_IT_RUNS.md)).
