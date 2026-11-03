# Pilot plan: connect first, then a live pilot

The plan in two stages (D-145). **Stage 0** connects AP Coder to the company's systems: its inbox, its
sign-in and JD Edwards. **Stage 1** is the pilot: the pilot tester and 3 to 4 AP clerks process real
invoices end to end, and after four measured weeks a 5-part gate decides whether it goes on. Dates move
with the approvals: the build is not what holds it up. If approvals slip, the pilot moves later.

A person approves every invoice throughout: **touchless processing stays off** for the pilot and until
the company's AI team has done its model-risk review (D-144).

## Stage 0: connect to the company

Each item ends with something a person can check. The company's IT and the JDE team sign stage 0 off.

- [ ] **The code goes into the company's own repository** ([HANDOFF.md](HANDOFF.md) section 1), and its tests
      run green there.
- [ ] **The pilot computer:** for the pilot's handful of people, a laptop with an NVIDIA graphics card with 16 GB
      of VRAM or more and 32 GB of memory ([SERVER.md](SERVER.md) section 1), docked and plugged in, sleep off, in a
      locked spot (or a desktop), given a burn-in week and set up in
      server mode ([SERVER.md](SERVER.md): Windows 11 Pro runs it), with LM Studio, OvisOCR2 and the chat model on it
      (section 8). The pilot tester and the clerks log in from their browsers, each with their own account
      (section 10).
- [ ] **The AP inbox:** a Power Automate flow saves the AP mailbox's emails into AP Coder's invoices folder
      ([MAILBOX_SETUP.md](MAILBOX_SETUP.md)). AP Coder never holds the mailbox's password. Check *Emails that
      need a person* on Import invoices each day.
- [ ] **Set up AP Coder's logins and authenticator codes for the pilot team** (D-146): an Admin adds the pilot
      tester and each clerk on *Users*; each signs in with the temporary password, chooses their own password and
      sets up an authenticator app on their phone (Microsoft Authenticator, Google Authenticator or any other), then
      signs in with a code from it every time ([SERVER.md](SERVER.md) section 10). Single sign-on with the
      company's accounts (Entra ID) is set aside for now ([HANDOFF.md](HANDOFF.md) 6.10).
- [ ] **JD Edwards read and write** through the 7 orchestrations of [JDE_ORCHESTRATIONS.md](JDE_ORCHESTRATIONS.md),
      built by the JDE team and tested in E1's test environment (PY). *Read* brings in the suppliers, accounts,
      business units, open POs and past vouchers. *Write* means AP Coder creates **pending** voucher batches: under
      E1's batch approval (*Manager Approval of Input*), AP approves each batch and posts it in E1, so a person
      always makes the final check in E1. Fill in the JDE worksheet first ([JDE_E1.md](JDE_E1.md)).
- [ ] **Job costing** (job, cost code, cost type on a line): built in AP Coder, about 3 to 4 weeks of work.
- [ ] **The company's master data:** the GL accounts and cost centers, how each sales tax posts, the vendor list
      with each vendor's address number (AN8), open POs, bank accounts, and last year's posted AP lines
      (*Learning & accuracy → Teach from past coding*), so each vendor is coded from its first invoice. Imported on
      the pilot computer as soon as it is set up, before job costing ([HANDOFF.md](HANDOFF.md) section 8, step 2).
- [ ] **A security review** by the company's IT security team (to OWASP ASVS Level 2), and **a penetration test if
      IT requires one.** They are needed before AP Coder gets *production* write access only: read access and the
      work in PY go ahead meanwhile. If IT wants a penetration test, book it early: firms need weeks of notice.

## Stage 1: the pilot

The pilot tester and 3 to 4 clerks, on real invoices from the AP inbox.

### Before the parallel week

- [ ] **One rule decides which invoices AP Coder owns**, so each invoice is entered in one system only. Write it
      down and give it to every clerk.
- [ ] **Time today's work:** the AP touch time an invoice, timed before the pilot (the gate's baseline).
- [ ] **Rollback steps** and what triggers them are written down before going live: how AP goes back to keying in
      E1, and what happens to the batches AP Coder made.
- [ ] Leave **Settings → Automation** (touchless processing) off (D-144).

### The parallel week

- [ ] AP Coder writes **only into PY**; AP keeps posting in production as today.
- [ ] Score the week with `python -m ap_coder pilot-score` against what AP posted in production (F0411, and F0911
      for the distribution: the JDE team exports them; the columns are in [JDE_E1.md](JDE_E1.md)).
- [ ] Fix what the score shows before going live.

### Live: four measured weeks

- [ ] For the invoices it owns, AP Coder creates pending voucher batches in E1; AP approves and posts each batch
      in E1. Nobody keys them twice.
- [ ] Reconcile AP Coder's vouchers before the first payment run that pays them, and check the daily control
      totals tie.
- [ ] Each week the pilot tester blind re-codes about 50 invoices (codes them without seeing AP Coder's coding),
      for the gate's missed errors.
- [ ] Each Friday: **Learning & accuracy** (the coding as proposed) and **Insights**.
- [ ] Mid-way: an interim report.
- [ ] After week 4: the gate, scored on live weeks 2 to 4, then the go/no-go.

Exports to Excel or CSV stay on the **Exports** page for anyone who doesn't use JD Edwards.

## The go/no-go gate

It is scored on live weeks 2–4, on at least 800 invoices from at least 80 vendors, with no vendor over 5% of the
sample:

1. **Coding:** at least 90% of repeat-vendor lines and at least 85% of all lines right, as AP Coder proposed them
   before review. A right line has the GL account and cost center, plus job, cost code and cost type on job lines.
   Also: at least 97% of vouchers posted exactly as AP Coder created them, and errors that both reviews missed at 2%
   or less (the pilot tester blind re-codes about 50 invoices a week).
2. **First-time match:** at least 60% of invoices need no change; the target is 70%.
3. **Time:** AP touch time is at least 2 minutes an invoice below a baseline timed before the pilot, and the lower
   bound of that saving stays above break-even.
4. **Exceptions:** 15% or less (the market average is 18.4%, Ardent); technical failures 2% or less.
5. **Controls (any failure stops the pilot):** zero duplicate vouchers, zero vouchers to the wrong supplier, daily
   control totals that tie, every batch approved in E1 before posting.

## What to bring to the decision

| Measure | Where | Good sign |
| --- | --- | --- |
| Coding as proposed, fields and vouchers as posted | `pilot-score` report (`pilot_score.md`), Insights | the gate's 1 met |
| Approved as coded | Insights | at least 60% need no change (the gate's 2) |
| Minutes per invoice | Insights (assumptions), the timed baseline | at least 2 minutes saved (the gate's 3) |
| Exceptions and technical failures | counted by the pilot tester (the review queue, Activity) | within the gate's 4 |
| Duplicates stopped, fraud signals | Insights, Activity | any caught is money kept; no duplicate voucher |
| Controls | Activity → controls report, E1's batch approvals | every batch approved in E1 before posting |
| Discounts taken in time | Insights → AP operations | more than before |
| Days to approve | Insights → AP operations | shorter than before |

If the pilot goes well, the next stage takes AP Coder to more of AP's districts, on a company server that also hosts
the Finance AI Hub (finance's local-model and other workflows); the whole company comes after that.
