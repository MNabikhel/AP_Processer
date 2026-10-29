# Demo run sheet: AP Coder on the laptop

A 10–15 minute live demo on the pilot tester's Windows laptop, with LM Studio and its models already in it. Everything
runs on the laptop, with no internet. **If anything goes wrong, play the video.** Known limits:
[KNOWN_LIMITS.md](KNOWN_LIMITS.md).

## The evening before (about an hour, mostly waiting)

1. Unzip the offline bundle (`APProcessor-offline-….zip`) into a folder of its own, e.g. `Documents\AP Coder demo`.
2. Open LM Studio: **Developer** tab → **Start server**. Leave the models unloaded: AP Coder loads them itself.
3. Close any AP Coder window, then double-click **`demo.bat`** in the bundle's `scripts` folder. It is AP Coder
   with a data folder of its own (`APCoder-demo` in your user folder), so the demo never mixes with your own
   invoices. The first start takes a few minutes, then the dashboard opens (http://127.0.0.1:8501).
4. **Settings → Reading**: LM Studio *running*, OvisOCR2 *found*, a chat model named. OvisOCR2's self-test runs by
   itself (a few minutes) and must say *passed*.
5. **Settings → Review**: your name, province *Alberta*, **Save**. **GL accounts & tax** → **Use the sample GL
   accounts**.
6. **Import invoices** → **Choose files** → the 10 PDFs in the bundle's `samples` folder (not `samples\demo_day`) →
   **Process**. Wait until *Settings → Reading* says nothing waits for OvisOCR2 (minutes a page on a laptop CPU).
7. **Settings → Data & backups** → **Back up now**: this is the demo's starting point.
8. Rehearse steps 1–6 below once. Then **Settings → Data & backups → Restore a backup…** → the backup from step 7 →
   type RESTORE.
9. Laptop: plugged in, sleep off, notifications off, other apps closed. The video on the desktop.

**On the day, 5 minutes before:** LM Studio → Start server; double-click `scripts\demo.bat`; the Review queue
shows 10 invoices.

## The demo

| # | Click | Say |
|---|---|---|
| 1 | **Review queue** | "This week's invoices from ten vendors across Canada, a US one and a credit note. AP Coder read each one on this laptop, coded the GL accounts and checked the sales tax and the maths. Nothing left the laptop." |
| 2 | Open **Pacific Office Supply, PO-77120** | "The invoice on the left, what it read on the right: each box shows where a value was found. BC: GST 5% and PST 7%. The tax check works out every tax and the total again: *All adds up*. The posting: GST to recoverable, PST expensed with each line." |
| 3 | Lines: toner and paper → **6000**, monitor → **6010** (click the account, type the number, Enter). Optional: a line's **split** button shares it across two accounts, by % or $. Then **Approve & teach**, **What it learned** | "Where it guessed, AP corrects it, as a clerk does today. Approving teaches it this vendor's coding." |
| 4 | **Import invoices** → `samples\demo_day\pacific_BC_GST_PST_PO-77311.pdf` → **Process**, open it | "The same vendor's next invoice. Nobody touched it: toner and paper to 6000, the monitor to 6010, learned from the approval a minute ago. *Ready to approve*." **Approve & teach**. |
| 5 | **Import** `…PO-77120_second_notice.pdf`, open it | "The vendor sends the same invoice again. AP Coder stops it: *possible duplicate payment*. Approving it anyway takes a tick and a reason, kept in the audit trail." |
| 6 | **Exports** → Excel workbook → **Export 2 invoices** → **Download batch 1**, open it | "Approved invoices go out in a batch for the ERP: invoices, GL lines and a GL summary. The JD Edwards export is built too, to set up with IT." |
| 7 | — | "Next: the pilot on real invoices with the AP leads, then PCL's data and JD Edwards, then one central server for the team." |

## If something goes wrong

- **The dashboard doesn't open:** in the browser, go to http://127.0.0.1:8501. Still nothing: double-click
  `scripts\demo.bat` again; its black window says why.
- **"LM Studio isn't running":** LM Studio → Developer → Start server, then *Settings → Reading → Check again*. The
  demo works without it: invoices are read from the PDF text, and the learning needs no model.
- **An import is slow** ("Reading…"): that's the model on the laptop's processor. Carry on with the invoices already
  in the queue.
- **A step does something unexpected:** *Settings → Data & backups* → restore the backup from step 7, or play the video.

After the demo, close the black window. Your own AP Coder (`APProcessor.bat`) and its data are as you left them.
