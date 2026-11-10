# Decisions

The significant choices behind AP Coder. D-01 to D-124, in full (the choice, why, and where it is written or
enforced): [archive/decisions-2026-10.md](archive/decisions-2026-10.md); look one up with
`grep -n -A8 "D-34\b" docs/archive/decisions-*.md`. Below, one line each. D-77 to D-79, D-81 to D-84, D-113
and D-114 were never used (not lost).

A new choice: the next number (`grep -o "D-[0-9]*" docs/decisions.md | sort -t- -k2 -n | tail -1`), written in full
at the end of the last section here (lines at most 120 characters); say so in the pull request. Never renumber. A
choice that is reversed keeps its line, marked "replaced by D-NN".

## Running offline

- **D-01** · Fully offline at run time
- **D-02** · Azure (Document Intelligence check, Azure OpenAI) is optional, off by default, and never needed to…
- **D-03** · The offline proof
- **D-04** · LM Studio is reached only on this computer or the office network
- **D-05** · An offline bundle (code, `wheelhouse/`, `models/`, `bundle_manifest.json`) installed with `pip --no-index`
- **D-06** · No CDN
- **D-07** · RapidOCR stays on 3.4.x, and its PP-OCRv5 models are copied from the bundle, never downloaded by RapidOCR

## Data and install

- **D-08** · One data folder outside the code (default `~/APCoder`, recorded in `~/.ap_coder/settings.json`) for the…
- **D-09** · SQLite, one file in the data folder
- **D-10** · No company data in the repo
- **D-11** · Never two versions on one database
- **D-12** · One button per computer (`APProcessor.bat`, `APProcessor.command`) that sets up only what is missing
- **D-13** · Two people approving share one AP Coder on one PC, the database on a local disk, not a network share

## Dashboard

- **D-14** · The dashboard is a local FastAPI app (uvicorn) on 127.0.0.1, replacing the Streamlit dashboard
- **D-15** · Only this computer's host names are served
- **D-34** · Shared on the office network (`AP_DASHBOARD_ADDRESS=0.0.0.0`), each browser on another computer gives its…
- **D-48** · After Approve, Park or Reject the review screen opens the invoice that followed this one when its screen…
- **D-49** · Over the first 50 reviewed lines the review queue and Learning show the coding accuracy with a plain note…
- **D-52** · What the pilot shows (owner's decision, before testing)
- **D-56** · The long choices are picked from a searchable list (`static/js/picker.js`, plain JavaScript, loaded on…
- **D-57** · The review screen's page viewer works like a document viewer, offline and in plain JavaScript (no PDF.js)
- **D-59** · The review screen's line grid is the tighter one (B-479
- **D-60** · The review screen has right-click menus (`static/js/ctxmenu.js`, plain JavaScript, one component
- **D-63** · The review screen's right-hand column runs

## Reading and touchless approval

- **D-16** · One way to read every invoice, nothing to choose
- **D-17** · Every picture becomes a PDF when it comes in
- **D-18** · The AI reads, code does the arithmetic
- **D-19** · Touchless processing is one company-wide switch, off by default, with one fixed bar every vendor must…
- **D-20** · Touchless gates
- **D-21** · The AI reviewer never changes a value
- **D-22** · Capture calibration is not refitted by bug fixes
- **D-43** · A value read from the line just over its label (`label-above`, B-280, B-282, B-288
- **D-44** · An invoice date or number read without a label of its own is a new evidence pattern, weaker than a…
- **D-45** · A reading found by wording the benchmark never prints (terms stated in a footer sentence, a Spanish…
- **D-46** · A supplier template reading is evidence of where the value was printed on the vendor's earlier invoices…
- **D-47** · What English vendor PDFs taught the reader beyond the benchmark's wording is marked `UNMEASURED` (D-45)…
- **D-50** · A supplier template learns from each page once
- **D-117** · AP Coder asks LM Studio for what a 16 GB laptop holds, measured on the real models (LM Studio's headless…

## Tax, coding and learning

- **D-23** · Place of supply is the ship-to printed on the invoice, else the province of the cost center's site, else…
- **D-24** · Rates are an effective-dated CSV that finance edits (a copy in the data folder wins)
- **D-25** · Lessons are kept per situation (vendor, document kind, field or kind of line)
- **D-26** · `vendor_key` rules are versioned (`VENDOR_KEY_RULES`)
- **D-27** · The invoice directory reads the PO, subtotal, tax and GL accounts from the invoice's coding JSON with…
- **D-51** · An account no approval backs is a guess, not a proposal
- **D-53** · No cost centers for the laptop pilot (the pilot of D-145 codes them)
- **D-58** · How GL coding learns from AP, measured with `bench coding` and its new `--realistic` run (2% reviewer…
- **D-61** · The ship-to read from an invoice is the address printed under a delivery label ("Ship to", "Deliver to"…
- **D-62** · EOM payment terms count the net days from the end of the invoice's month, however they are written
- **D-65** · The vendor list's tax numbers fill the invoice details where none is read
- **D-64** · A line's sales tax is said tax by tax, as one tax code picked in one dropdown per line on the review screen

## Exports and intake

- **D-28** · Files for an ERP loader (JDE Z-files, the CSV and Custom CSV exports) are written as they are, without…
- **D-29** · An export batch is rebuilt from approved data
- **D-30** · AP Coder never connects to the mailbox
- **D-40** · The UTF-8 byte-order mark
- **D-66** · *Pay from* is the company's own bank account an invoice is paid from, picked from a list only

## Working on the code

- **D-31** · Every bug gets a failing test first, then the fix, and an entry in docs/BUGS.md with its lesson
- **D-32** · The codebase map is checked, not only asked for
- **D-33** · The flat modules of `ap_coder/` (68, all but `__init__.py` and `__main__.py`) are grouped into six…
- **D-35** · The map-reviewer runs before every commit an agent makes, without being asked
- **D-36** · CI runs on pushes to `main` and on pull requests, not also on pushes to `claude/` branches
- **D-37** · Each CLAUDE.md is kept at 50 lines or fewer, bug lessons aside
- **D-38** · The codebase map is removed
- **D-39** · "Approved as coded" has one definition and one label on Insights, Learning and Activity
- **D-41** · Line items read without column headings to go by (a table read by its amount column, a receipt's list of…
- **D-42** · A reading the page cannot settle is never verified, however well the totals add up
- **D-54** · An AP Coder folder is identified by its version and its build
- **D-55** · Python 3.11 is the floor everywhere
- **D-67** · The 67 one-line alias files of the old flat paths (D-33) are gone
- **D-68** · The default `python -m pytest` leaves out the slow tests (every browser test and those over half a…
- **D-69** · `ap_coder/core/store.py` (4,000 lines) is the package `ap_coder/core/store/`
- **D-70** · A line's tax code is learned from approvals
- **D-71** · A withdrawn approval takes back what it taught the vendor's layout
- **D-72** · A lesson reaches the AI (prompts, automatic use) only after 2 corrections to the same value (`agreeing`…
- **D-73** · The training export's format 2 only adds to format 1
- **D-74** · The Lessons tab measures each lesson
- **D-75** · Before approval, the review screen asks what an export would refuse, and "Approve anyway" covers only…
- **D-76** · The review screen is laid out for a clerk entering a standard voucher
- **D-80** · Safety rules of learned line tax codes (D-70)
- **D-85** · Template snapshots keep the order of approval (`template_snapshots.seq`, AUTOINCREMENT), not a time to…
- **D-86** · The review screen after its second UI sign-off
- **D-87** · A PO number longer than JD Edwards' PO field (VLPO, 8 characters) no longer blocks approval when the PO…
- **D-88** · The review queue's "look clean" and Ready are today's checks, not the ones stored when the invoice was…
- **D-89** · Confidence is two numbers
- **D-90** · Meals and entertainment
- **D-91** · Payment terms → JD Edwards payment terms code (VLPTC)
- **D-92** · The shadow-pilot kit (as the plan, replaced by D-145; `pilot-score` kept)
- **D-93** · JD Edwards
- **D-94** · First-time setup is the one exception to "nothing goes online"
- **D-95** · Touchless never approves what bulk approval would refuse
- **D-96** · The review screen's keys from the box it focuses, and drafts in the queue (fourth UI sweep)
- **D-97** · Server mode
- **D-98** · Roles in server mode
- **D-100** · The company server runs AP Coder as a Windows scheduled task, and updates with a way back
- **D-99** · The company server's network side and pages (with D-97's switch)
- **D-101** · Server security review (changes D-97, D-99 and D-100 where they say otherwise); its "a Clerk may
  release a vendor's hold": replaced by D-142
- **D-102** · What each role sees on the company server, and how the server speaks (with D-97 to D-99)
- **D-103** · The finance hub feed (Microsoft Fabric)
- **D-104** · JD Edwards through its AIS Server (the API connection), and the offline rule's named internal systems
- **D-105** · Voucher posting safety (changes D-104 where it says otherwise)
- **D-106** · Coding work runs as the `developer` agent (`.claude/agents/developer.md`)
- **D-107** · Fixes of an end-to-end walk (B-1180 to B-1210), with the choices they made
- **D-108** · Fixes of an end-to-end review (B-1211 to B-1217), with the choices they made
- **D-109** · Second security review of server mode (B-1231 to B-1243
- **D-110** · The invoices' summary
- **D-111** · The company server's runtime for ~500 accounts (~50 at once) and 5,000 to 10,000 invoices a month…
- **D-112** · A pilot tester's walk of laptop mode (B-1297 to B-1306), with the choices it made (changes D-39 and…
- **D-115** · The rule reader's package (`ap_coder/capture/reader/`) re-exports only the names used outside it (42 of…
- **D-116** · The tests live in folders that mirror `ap_coder/` (`tests/core/`, `tests/coding/`, `tests/web/`..., plus…
- **D-118** · One rule for "AP changed its mind", written once (`memory.latest_decision`) and used by the GL…
- **D-119** · Learning on the reading side, "wrong this time, right next time"
- **D-120** · A supplier's line descriptions as AP approves them, known again on its next invoice
- **D-121** · Header lessons reach the reader without the chat model
- **D-122** · Suppliers reach hands-free sooner, on an honest bar
- **D-123** · The five longest functions in named steps, no behaviour change
- **D-124** · Fewer connections a page; a before/after harness for refactors (scripts/same_behaviour.py)
- **D-125** · The bug log and the decisions are short files; their past is archived. `docs/BUGS.md` holds the bugs
  from B-1442 and `docs/decisions.md` one line per decision, each choice made from now on in full; B-01 to B-1441
  (287 KB) and D-01 to D-124 in full (170 KB) are in `docs/archive/`, which `.ignore` keeps out of ripgrep and the
  Grep tool's searches (an agent's search for a word in the code no longer lands in 450 KB of history; a number is
  looked up there by name). Why: both files were read whole, searched and appended to on most changes. Benches take
  `--quick` (`bench/__main__.py` `QUICK`) for a check in a minute or two. `tests/meta/test_agent_friendly.py` keeps
  the last numbers findable in the short files.
- **D-126** · A line is split across GL accounts (cost centers) by percentage or by dollar amount, from a split
  button on every line (beside Delete; left out of Tab's way, B-478) as well as *Split a line…* under the table
  and the line's right-click menu. By amount, the parts are the amounts typed, to the cent, each of the line's sign
  (minus on a credit note), and must add up to the line; the description says "(500.00 of 890.00)". Why: the pilot
  tester asked for a line's cost shared across GL accounts and did not find the split under the table; invoices are
  often shared out in dollars, and a percentage of 56.18% to get 500.00 is error-prone. Where: `coding/review.py`
  `split_line(by=)`, `web/pages/invoice.py` `split`, the popover in `web/templates/invoice.html`,
  `static/js/invoice.js`; tests `tests/coding/test_split.py`, `tests/web/test_web_picker.py`.
- **D-127** · The demo release: a demo runs AP Coder on a data folder of its own, `scripts\demo.bat`
  (`AP_PRIVATE_DIR=%USERPROFILE%\APCoder-demo`, never recorded: the usual double-click keeps the pilot tester's
  data; in `scripts\` as the app folder's top holds one file to open per computer), with the ten samples imported
  and two more invoices in `samples/demo_day/` (made by `scripts/make_sample_pdf.py`, left out of the self-check):
  Pacific Office Supply's next invoice, coded from the approval of PO-77120, and PO-77120 sent again, stopped as a
  duplicate. `docs/DEMO_RUN_SHEET.md` is the one page to follow, `docs/KNOWN_LIMITS.md` what it does not do yet.
  CI proves it on the offline bundle on Windows with the internet cut off: installed with LM Studio (a stand-in)
  running and the right model for each job, `scripts\demo.bat` keeping its own folder, and
  `tests/ops/test_demo_day.py` (import to Excel export). Why: the pilot tester's laptop may hold their own AP Coder
  data, where the samples would be skipped as already in AP Coder; a rehearsal is put back with a backup restore.
  The version stays 0.3.0; the release is the git tag `v0.3.0-pilot1` and its bundle, told apart by its build stamp.
- **D-128** · The handoff to the company's own AI coding agent is one way and complete: `docs/HANDOFF.md` is its
  brief (getting the code in, run and test, the code's map, sample vs real data, each connection point with what the
  company supplies, the order of work, the house rules), written so that nothing need be asked back. It travels
  as a git bundle (`main` with its whole history and the tags) and as a plain zip of `main`, with HANDOFF.md beside
  them and their SHA-256. The company's copy becomes the main one. No company data travels: the company's values
  are gathered inside the company (the JDE worksheet) and imported into the data folder. `CLAUDE.md` and the README
  point to HANDOFF.md first.
- **D-129** · A release is tagged by CI once its laptop is checked with the real LM Studio: `.github/release-tags.txt`
  lists the tags (tag, commit, title), and `.github/workflows/release.yml` builds the pilot release's offline install
  zip from its commit, installs it on Windows with LM Studio running headless (llmster) with the pilot laptop's
  models on the CPU, double-clicks `scripts\demo.bat` with the internet cut off and runs the run sheet against the
  running dashboard: `tests/ops/test_demo_day.py` with `AP_DEMO_DAY_URL` waits for OvisOCR2 to pass its self-test and
  checks that the chat model coded every sample invoice. On main it then creates each missing tag and its GitHub
  release, the checked zip attached to the pilot's. Why: a stand-in proved the install, not the real models on
  Windows, and only CI may push tags here. Slow, so it runs only when the workflow or the tag list changes.
- **D-130** · A pilot release is checked on the laptop once, not once per run: the Release workflow's first job
  keys what its check checks (`scripts/ci/release_key.py`: the newest pilot release's tag and commit, the check
  job's runner, time limit, environment and steps, and `tests/ops/test_demo_day.py`) and looks for a passed run
  with the same key whose zip is still kept (14 days). Found, the check is skipped and the tags job attaches that
  run's zip. A release's pull request runs the check; its merge to main reuses it (it ran twice, 60 to 90 minutes
  each, on the CPU). A release keeps the zip it was first given (no `--clobber`): its checksum never changes after
  it is published. Why not key on the files alone: the zip is built from the pilot's commit, so a release reuses
  only a check of that same commit.
- **D-131** · The local model's account suggestions are kept in the data folder (`.cache/model_accounts`), one file
  per exact question (model, lines, history and lessons, chart), so the page reader's pass never asks what arrival
  asked: with a graphics card every invoice was read again and coded twice, the same answer at temperature 0, and a
  second request that failed (the model busy or unloaded beside OvisOCR2) blanked the accounts the invoice had. A
  question that changed (an approval or a lesson since, other lines read off the page) is asked again, so what AP
  taught applies. Only the app's pipeline keeps them: the doctor's dry run and the coding bench still ask the model.
- **D-132** · A currency changed from the one read off the invoice can't take it under the second-approval limit: the
  total is held to the limit in both currencies, each at its CAD rate (the strictest in force since it came in,
  B-970), and above either it waits for a second approver; a currency read with no CAD rate needs one, as an invoice
  in it always does. The approve bar says why before approving ("The currency was changed from USD, and in USD it is
  over the approval limit of 20,000.00 CAD: a second approver is needed"), as do the first approval's message, the
  Second approval tab and the audit trail, which keeps the change itself as it keeps every edit. Why both, not the
  one read alone: a reading can be wrong and a person must be able to correct it, but a correction that lowers the
  amount in CAD is what a second pair of eyes is for. One rule for the laptop and the server (`Store.approval_reason`,
  called by `approve_invoice`, bulk approval included). An invoice whose currency was never changed is decided as
  before.
- **D-133** · With JD Edwards' G/L date the day a batch is exported (Settings > JD Edwards), month-end counts an
  invoice as in the ERP at a period end only if the batch that took it was made on or before that day
  (`export_batches.created_at`); one exported later is accrued for that period, its note "exported after the month
  end" in the CSV and a pill on the page (`erp/accruals.py`, `late_after`). Why: JD Edwards has such an invoice in
  the next month, so counted as in the ERP its cost was in neither month. With the G/L date the invoice date (the
  default) a voucher exported later still lands in its own month, so an exported invoice stays in the ERP.
- **D-134** · A JD Edwards batch goes to JDE one way: its ZIP loaded, or its vouchers posted through the API. Each
  download of a batch's ZIP is recorded (`jde_file_downloaded`: who, when; Activity, and the batch's API part says
  it). Posting that batch through the API afterwards is refused unless the person ticks that the file was not loaded
  into JDE and says what was checked, kept in the `jde_api_approved` event (the pattern of "Approve anyway" with a
  reason): every post once a download is recorded asks, as any later post may send a voucher that file holds. The
  vouchers waiting in the queue at the download (JDE never reached, so in the file) go back to *Dry run passed*: the
  queue would have posted them a second time unasked, and a go-ahead under way at the download sends none it had not
  sent yet (`_claim`: none approved before the last download). A batch posted through the API has its ZIP's page
  warn that those vouchers are in JDE already (the ZIP leaves them out; a copy downloaded before holds them). A batch
  only ever sent one way posts as before. Why not refuse the second way outright: the files are the fallback while
  JDE's API is down, and AP may download a ZIP only to look at it. The record is in the database: a restore from
  before the download forgets it, as it forgets the batch's other events. (`ap_coder/erp/jde_post.py`
  `file_downloaded`, `downloads`, `post`, `_claim`; `ap_coder/web/pages/exports.py`; `tests/erp/test_jde_post.py`,
  `test_jde_api_web.py`)
- **D-135** · Two defences against another page of the company's domain acting in a person's browser (the security
  audit of October 2026). (1) A batch's JD Edwards ZIP is downloaded by a POST from the Exports page (its button
  sends a form; `app.js` saves an answer that is a file under its name), which LocalOnly checks (the page's header,
  the same origin): that download is recorded and puts the vouchers waiting in the queue back to a go-ahead (D-134),
  so an `<img>` or a link on another page must not do it. A GET of the ZIP is refused (405), not served unrecorded:
  a ZIP out without the record is the double posting D-134 prevents, as the queue would still send the vouchers it
  holds. The other formats stay GET links (they change nothing). (2) Over HTTPS the session cookie is
  `__Host-ap_session` (Secure, Path=/, no Domain), which a browser takes only from this host, so another host of the
  domain can't plant one beside it to block a login (two session cookies are still no login, D-101); `ap_session`
  only over `--insecure-http`, where the prefix isn't allowed. The update logs everyone out once (the old cookie is
  not read). With them, the vouch rule (D-109) follows a chain: an account added or reset within `VOUCH_DAYS` by
  an account the first approver added or reset in that time can't give the second approval either.
  (`ap_coder/web/pages/exports.py` `download_batch`, `static/js/app.js` `post`, `web/identity.py` `session_cookie`,
  `web/pages/login.py`, `core/store/users.py` `vouched_for`; `tests/erp/test_jde_api_web.py`,
  `tests/web/test_server_security.py`, `test_server_hardening.py`)
- **D-136** · The administrator's `service install|start|stop|status|remove` never take the task's account, its name
  or the code folder from `server.json` (B-1500): the command line's, else `APCoderService`, `AP Coder` and the folder
  the command runs from, as `server-update` trusts only that folder (D-109). Why: the data folder is the service
  account's to write, and these three say what the task runs, as whom, and which task `schtasks` changes; a built-in
  account (SYSTEM) is so used only when `--account` names it. Who chose another name or account gives it each time.
  `service stop` ends a process that host.json or .ap_coder_server.json names only when it runs the folder's Python
  (its `.venv`'s, or the `home` Python of its `pyvenv.cfg`, which a .venv's python.exe starts); any other is left to
  the dashboard's own /shutdown and `schtasks /end`. (`ap_coder/ops/service.py` `main`, `runs_python_of`;
  `docs/SERVER.md` step 6; `tests/ops/test_service.py`)
- **D-137** · The demo invoices are offered only while there are no invoices of your own (or to remove a demo already
  loaded). Loading them beside your own invoices added the ten samples again (a second Pacific PO-77120 reads as a
  duplicate payment) and mixed made-up invoices into your numbers, which B-468 already guarded against for the
  order of the cards. The demo's own copy of AP Coder (`scripts/demo.bat`, its own data folder) is where to show it.
  (`ap_coder/web/pages/process.py` `demo_offered`; `tests/web/test_web_process.py`)
- **D-138** · A manager confirms an invoice's cost allocation when AP asks: **Send to manager** (under Park) on the
  review screen, AP's choice invoice by invoice (never automatic). The lines on screen go with the request (each line's
  GL account, cost center and amount, `manager_requests.lines`), and the invoice is **parked** waiting for that manager,
  not a new status: everything that already treats a parked invoice right (out of the queue and bulk approval, still
  counted for POs, duplicates and the month end, in the directory, brought back by Un-park) stays right, and an older AP
  Coder opening the database sees a parked invoice. Either answer, **Confirmed** or **Sent back** with what to change,
  puts it back in the review queue; AP then approves as before: the approval limits and the second approval are
  unchanged, and an approval never waits on a manager (AP decides). The screen says who answered and when, and warns
  when the lines are no longer the ones confirmed (GL account, cost center, amount; a retyped description doesn't
  count). Two ways to answer, as asked: on the **laptop**, AP downloads a ready-made email (`.eml` with `X-Unsent: 1`,
  so Outlook opens it as a draft to send; the PDF attached, the lines, the manager's own cost centers marked) and
  records the reply (who, when, what they said; the reply email kept in the data folder's `emails/manager/`); on the
  **server**, a **Manager** account (a role outside the Read-only to Admin ladder: it opens only `/manager` pages, its
  own requests, never another manager's or any other invoice) answers on screen. An Admin sets a Manager's cost centers;
  on the laptop each cost center remembers its manager's name and email. Every step is in the Activity log
  (`sent_to_manager`, `manager_confirmed`, `manager_sent_back`, `manager_request_withdrawn`). No schema bump: a table
  and three columns, added whatever the version. (`core/store/managers.py`, `coding/manager_mail.py`,
  `web/pages/manager.py`, `web/roles.py` `MANAGE`; `tests/core/test_managers.py`, `tests/web/test_manager.py`)
- **D-139** · The review screen's PO match says what is **left to expense** on each PO line and on the whole PO after
  this invoice: the line's amount less what the other live invoices bill on it and this one; when the PO import gives
  an **Open amount** (JD Edwards' AOPN), that amount less this invoice and the ones not exported yet (the ERP counted
  the exported ones). Over is shown amber, "over by ...", never an error: as asked, it is not strict, so no new check
  and no change to the PO warnings, and an invoice with no PO or over the amount is approved as before. The open
  amount is as of the last PO import: between an export and the next import it reads high by what was just exported
  (said in the notes line). (`coding/po.py` `LineLeft`; `tests/coding/test_po.py`)
- **D-140** · The invoice directory says **who acts next** and what, beside where the invoice stands, on by default:
  AP (to review, to export, to process again), the manager it waits for, a second approver (not the first), or no
  one (in the ERP, rejected). It is worked out from the status and the parked reason, so it needs nothing stored.
  (`erp/search.py` `next_to_act`; `tests/erp/test_search.py`)
- **D-141** · Changed vendor details are approved only with a reason: a bank account printed for payment that differs
  from the vendor's approved invoices (`VENDOR_BANK_CHANGED`) or from the one JD Edwards pays (`VENDOR_BANK_NOT_JDE`),
  and a GST/HST, QST or PST number that differs (`VENDOR_TAX_NUMBER_CHANGED`, `VENDOR_QST_CHANGED`,
  `VENDOR_PST_CHANGED`) are errors now, not warnings: "Approve anyway" with how the change was confirmed, kept with
  the approval in the audit trail and the controls report's "Approved despite an error" (the pilot tester's call,
  2 November 2026: "stuff like this should be logged"). A vendor on hold is never approved anyway (`HARD_CODES`,
  B-1544): the hold is lifted first, or the invoice rejected. As errors, the changed tax numbers take an error's
  share off the Reading. (`coding/vendors.py`, `web/invoice_view.py` `HARD_CODES`;
  `tests/web/test_web_review_voucher.py`)
- **D-142** · On the company server only an **Approver or an Admin takes a vendor off hold** (On hold to Active on
  the Vendors page); a Clerk is refused ("Only an Approver can take a vendor off hold.", the Active choice disabled
  for them, with that sentence). Replaces D-101's "a Clerk may release a vendor's hold"; D-101's other point left for
  later (a Clerk may reopen another person's approval) stands. Why: since D-141 a vendor on hold is never approved
  anyway, and a Clerk who could lift the hold could then approve its invoices under the limit alone (B-1548; the
  user's call). A Clerk still puts a vendor on hold and edits its notes and tax numbers, so the route stays a Clerk's
  and only the lift is checked. A Manager (D-138) never reaches the Vendors page. On a laptop (no accounts) nothing
  changes. The lift is in the audit trail as before (`vendor_updated`, "made active", with who). The other ways a
  hold ends are an Admin's (the vendor master import, the JD Edwards sync's `erp_hold`, removing the demo) or keep it
  (vendors merged, a vendor added on the review screen). The same rule on the review screen (B-1549): a held
  vendor's invoice coded to another vendor (an existing one, or one added there under a name a letter off) is
  `VENDOR_CHANGED_FROM_HELD`, an error an Approver or an Admin approves anyway with a reason; a Clerk's approval is
  refused. The invoice is the held vendor's by its vendor as read, or by a GST/HST number or bank account (read, or
  on screen) that is the held vendor's (given it, or on its approved invoices), so a near-identical new vendor
  doesn't dodge it. Server only: the laptop is unchanged. A Clerk is not offered "Approve anyway" for it: the
  approve bar says "Only an Approver can approve it." and why. Bulk approval on the server leaves such an invoice
  for a person and says why (B-1551: one read under another vendor's name, asking to be paid to a held vendor's
  bank account, was bulk-approved), whoever runs it: it gives no reason. The audit trail's approval says who and
  from which vendor (its `changes`: vendor before and after, and the reason). While a vendor is on hold (here or in
  JD Edwards), what the hold is matched by is an Approver's to change too (B-1554): a Clerk can't change its GST/HST,
  QST or PST number on the Vendors page (shown read-only) nor replace them with *Add new vendor…* under its name;
  its name and its bank accounts (on its approved invoices, and JD Edwards' `erp_bank`, B-1555) no Clerk edits.
  The notes and the province stay a Clerk's. (`web/access.py` `may_lift_hold`,
  `web/pages/vendors.py`, `web/templates/vendors.html`, `coding/vendors.py` `moved_off_hold`, `coding/bulk.py`,
  `web/invoice_view.py` `_findings` and `analyse`, `web/pages/invoice.py` `_approve`;
  `tests/web/test_web_masterdata.py`, `tests/web/test_web_review_voucher.py`, `tests/coding/test_vendors.py`)
- **D-143** · Every backup is a whole state of AP Coder, not the database only (the pilot tester, 3 November 2026: "the
  pdf should also be backed up... so we can easily restore previous states"). Beside each `ap_coder-<stamp>.db`, its
  list of files `ap_coder-<stamp>.files.json` (each file's place, size, time and SHA-256, and the database's own); the
  files themselves once per content in `backups/files/<2 digits>/<sha256>`. In it: the whole `invoices/` folder
  (PDFs, pictures, `originals/`, `deleted/`, `statements/`, saved emails and their attachments), `emails/` (managers'
  replies), `reference/`, the data folder's rate table and JD Edwards mapping, the postings ledger (merged on a
  restore, never replaced: D-105) and an invoice's file kept elsewhere (back in `invoices/restored/`). Not in it: the
  `.env` (passwords; a backup must not choose where AP Coder connects, as D-109), `server.json` (this server's
  install), the JD Edwards installation id (a restore makes a new one), caches, logs, exports and models (made again
  or bundled). Content-addressed, not a mirror: a daily backup copies only new or changed files (one unchanged since
  the last list, by size and time, is not read again), a file deleted or changed since stays for every kept backup
  that lists it, and pruning drops a file only when no kept list names it and it is a day old (a backup may be being
  made). The second backup folder has the same layout (the list goes last), so it restores alone on a new computer.
  A restore makes the listed places as they were: each file copied with its SHA-256 checked (missing or damaged ones
  taken from the second folder, else said), what is there and was not then moved to `backups/set-aside-<stamp>/`,
  never deleted; the accounts and sessions rules stay (D-101). Settings → Data & backups shows each backup's contents,
  its second copy and a quick check (sizes); `python -m ap_coder backup now|list|check|restore [--from <folder>]`
  for IT (`check` reads every file). A backup of an older version (no list) restores its database only, and says so.
  Only a complete backup (with its list) is the day's, and one without never pushes a complete one out; one backup,
  restore or update put-back at a time, by an OS lock in `backups/` shared by the server's processes (B-1557 to
  B-1559).
  (`core/store/backup_files.py`, `core/store/backups.py`, `ops/backup_cmd.py`, `ops/server_update.py` `put_back`;
  `tests/core/test_backups.py`, `tests/ops/test_backup_cmd.py`, `tests/ops/test_server_update.py`)
- **D-144** · **Touchless processing stays off** until the company's AI team has done a model-risk review of it
  (invoices written to steer the AI reviewer included): an invoice approved without a person, on a model's say,
  waits for that review (the user's call). PILOT_PLAN.md's week-3 "Touchless, if ready" item is removed; the plan
  itself is to be rewritten once the pilot's design is settled. Nothing in the code changes: touchless is off by
  default (Settings → Automation). (`docs/PILOT_PLAN.md`)
- **D-145** · **Connect first, then a live pilot** (the user's decision; the plan was a shadow pilot, D-92, on a laptop:
  its `pilot-score` is kept). **Stage 0** connects AP Coder to the company: the code into the company's repository; the
  pilot computer in server mode (a laptop with an NVIDIA card for the pilot's handful of people, or a desktop; Windows
  11 Pro, SERVER.md section 1); the AP inbox through a Power Automate flow (MAILBOX_SETUP.md; AP Coder never holds
  mailbox credentials); single sign-on planned with IT (Entra ID or the company's AD) and built, Entra's sign-in through
  Microsoft's cloud being an exception to the offline rule to record as one (replaced by D-146: AP Coder's own logins
  with authenticator codes, single sign-on set aside); JD Edwards read and write through the 7
  orchestrations of JDE_ORCHESTRATIONS.md, tested in E1's test environment (PY); job costing built; the company's IT
  security review (OWASP ASVS Level 2), and a penetration test if IT requires one, conditions for production write
  access only. **The pilot**: the pilot tester and 3 to 4 clerks; one rule says which invoices AP Coder owns; a parallel
  week writing only to PY, scored with `pilot-score` against production; then live, AP Coder creating *pending* voucher
  batches that AP approves and posts in E1 (Manager Approval of Input), so E1's batch approval keeps a person's final
  check; rollback steps written before going live; vouchers reconciled before the first payment run; the 5-part gate of
  PILOT_PLAN.md, word for word, scored on live weeks 2 to 4; touchless off (D-144). Excel/CSV stays for anyone without
  JD Edwards. Why: the user's call ("integrate the AP inbox, single sign in, and have read and write set up with JDE/E1,
  and then the actual pilot once all integrations are ready... with a small AP team"). Docs only: nothing in the code
  changes. (`docs/PILOT_PLAN.md`, `docs/HANDOFF.md` sections 6, 8, 10, `docs/KNOWN_LIMITS.md`, `README.md`,
  `docs/PILOT.md`)
- **D-146** · **AP Coder's own logins, with a code from an authenticator app at every login** (the user's decision:
  "discard [Entra ID single sign-on] for now and have our own login and authentication with Google Authenticator
  instead"; D-145's single sign-on replaced). In server mode every account signs in in two steps: the password, then
  the 6-digit code of any authenticator app (Microsoft Authenticator, Google Authenticator...): TOTP (RFC 6238,
  HMAC-SHA1, 30 seconds) from the standard library, offline. **First login**: the Admin's temporary password, the
  person's own password, then the app set up from a QR code of its `otpauth://` address (issuer "AP Coder", account
  the username; the key also as text), confirmed with a code; an account from before sets it up at its next login.
  **Each code**: a step either side of the server's clock taken; never a step at or before the last one used (no
  replay); a wrong one counts toward the lockout as a wrong password does, and a right password alone no longer
  restarts the count (only a whole login does, so guesses at codes can't be reset by logging in again); success,
  failure and pauses in the audit trail. **Half way** (the password right, the code to come) is no session: its own
  cookie, kept by its SHA-256 for 15 minutes in the short-lived table, opening only its next step's page (OPEN
  routes, each checking the step); the session is made once the code is right, under a new token; a session of an
  account without its app set up is no login (so sessions from before end). **Recovery**: an Admin's *Reset
  authenticator* on Users, which resets the password too (a phone lost or stolen may go with it: a temporary one
  shown once, as for a new account; audited, logs the person out, not one's own); `users reset-2fa <user>` at the
  console for the last Admin, printing the temporary password once (the user's call).
  No recovery codes: a printed secret to lose or leave on a desk, while for a handful of people an Admin or the
  console resets an app in a minute. **The secret**: 20 bytes from `secrets`, kept sealed (HMAC-SHA256 as the stream
  and the tag, the temporary passwords' sealing of D-111, now shared) with this server's key, `authenticator.key` in
  the data folder (made at the first set-up, never in the database): a copy of the database alone gives no codes
  away. **Backups** (D-143): the key is in every whole-state backup (the share is IT's to protect, the user's call);
  a restore puts it back only where there is none
  (a new computer, whose accounts come from the backup), never over the one here, which the accounts a restore keeps
  on the server (D-101) are sealed with, and never sets it aside; so codes work after a restore here and on a new
  server. A key lost while its accounts are kept: each code refused in words, an Admin resets each app. **The QR
  code**: Project Nayuki's QR Code generator (MIT, one pure-Python file, standard library only), vendored unchanged
  as `web/qrcodegen.py` (1.8.0, the wheel's SHA-256 of it checked; left out of ruff), drawn as an SVG on the server:
  no new dependency to bundle, no JavaScript. The laptop is unchanged. Entra ID sign-in remains a possible later
  option (HANDOFF.md 6.10). (`core/authenticator.py`, `core/store/users.py`, `core/store/backup_files.py`,
  `web/pages/login.py`, `web/templates/login_step.html`, `web/identity.py`, `web/roles.py`, `web/pages/users.py`,
  `ops/cli.py`; `tests/core/test_authenticator.py`, `tests/core/test_users.py`, `tests/core/test_backups.py`,
  `tests/web/test_server_two_step.py`, `tests/web/test_server_browser.py`, `tests/ops/test_users_cli.py`)
- **D-147** · **The developer builds the AP inbox's Power Automate flow in stage 0, and IT gives the flow's account
  access to the AP shared mailbox** (the user's decision, 3 November 2026; the plan had IT building it). The flow is
  the one of MAILBOX_SETUP.md, unchanged: the whole email as .eml into a synced library, moved into AP Coder's
  invoices folder by a scheduled task; AP Coder still never holds the mailbox's password. IT can own the flow instead
  if it prefers. The model register (each local model's publisher and licence) is listed for the AI team before the
  security review. The gate of PILOT_PLAN.md is worded as the proposal's copies (after AP's review in AP Coder; the
  break-even of its base case, named without a figure so the two copies can't drift; approved in JD Edwards).
  Docs only: nothing in the code changes. (`docs/HANDOFF.md` sections 7
  and 8, `docs/MAILBOX_SETUP.md`, `docs/PILOT_PLAN.md`)
- **D-148** · **How AP Coder scales to 300–400 people on one server** (the scale goal, docs/SCALE_GOAL.md; the research
  with its sources: docs/archive/scale-2026-11/research.md, 5 November 2026). (1) **More processes, cheaper pages,
  not another web server**: one process serves about 1.6 pages a second (Python, the GIL), so 20–40 requests a second
  needs 13–25 web workers (300–600 MB each, which fits 64 GB) *and* pages that cost less; no ASGI server (granian,
  hypercorn, waitress) removes the Python per page. The cap of 8 workers goes, for a default measured in the load
  test. (2) **Windows**: with several workers uvicorn runs the selector loop (`uvicorn/loops/asyncio.py`:
  `ProactorEventLoop` only without subprocesses, read in 0.54.0), whose `select()` holds at most 512 sockets a worker:
  TLS ends in IIS (ARR) in front, which forwards to `http://127.0.0.1`, and the workers stay well under 512
  connections each. (3) **SQLite stays** for one server: WAL, `synchronous=FULL` (an approval or a JD Edwards post must
  survive a power cut), writes begun with `BEGIN IMMEDIATE` and kept short, a small connection pool per process,
  `journal_size_limit`, `PRAGMA optimize`, no `mmap_size` on Windows, the database on the server's own disk. A
  PostgreSQL seam is built (docs/SCALE_GOAL.md phase 7); the port waits until failover, a second app server or
  point-in-time recovery is needed. (4) **The work nobody waits on leaves the web workers**: OCR and page rendering in
  a spawned process pool (ONNX Runtime 1–2 threads each, PyMuPDF from one thread per process); no free-threaded Python
  (PyMuPDF and onnxruntime have no wheels for it on Windows). (5) **The model machine** (LM Studio): models preloaded
  with `lms load`, a vision-batching engine, `llm_parallel` matched to its concurrent predictions, the API token on, a
  firewall that admits the AP Coder server only. (6) **Measured, not guessed**: `python -m ap_coder.bench load` and
  `bench volume` / `bench day`; the numbers in docs/SCALE.md. Docs only so far: the code changes follow, phase by phase.
- **D-149** · **A model server named in the company's DNS** (B-1605; changes D-104's rule on model servers where it
  says otherwise). Stage 2 reaches the models on another machine by its company name (`macstudio.corp…`), and the
  offline rule refused every dotted name that wasn't `.local`, `.lan` or `.home` unless internet access was on. Now
  a name under a domain listed in `AP_COMPANY_DOMAINS` (the `.env`, comma-separated) is judged as a bare name is
  (B-974): allowed while every address it resolves to is private or loopback, refused if one is public; the lookup
  is kept 30 s. Other dotted names are never looked up at all, because the offline proof counts every lookup
  (`tests/offline/test_offline_guard.py`): the company network is named by an Admin, not guessed, as JD Edwards' one
  saved address is (D-104). The refusal says to add the domain to `AP_COMPANY_DOMAINS` instead of turning on internet
  access, and keeps that whole sentence (`check_server` cuts other errors to 160 characters). (`core/config.py`,
  `ai/local_llm.py`; `tests/web/test_platform_security.py`, `tests/ai/test_local_llm.py`)
  - **Amended (B-1608, B-1675):** such a name is connected to at the very addresses its check judged, never through
    a second lookup.
    - **Which names:** bare names, .local, .lan and .home, and names under `AP_COMPANY_DOMAINS`.
    - **How:** `config.pinned_addresses` feeds urllib's connection and the OpenAI client's transport. The name stays
      in the Host header and in the certificate check (SNI).
    - **The 30 s lookup** decides refusals and gives those addresses; it never stands in for them.
    - **A name that resolves to nothing** is still judged by its name (no proxy) but isn't connected to.
    - **The advice** to add a domain to `AP_COMPANY_DOMAINS` goes only to a dotted name outside .local, .lan, .home
      and those domains. (`core/config.py`, `ai/local_llm.py`, `ai/page_reader.py`; `tests/ai/test_local_llm.py`)
- **D-150** · **Background polls are not activity; schema 20** (B-1579, B-1580, B-1581, B-1584; changes D-109's idle
  rule where it says otherwise). A tab left open logged out at 30 idle minutes only if nothing polled: the reader
  panel and job status renewed the session for as long as the tab stayed open (up to the 12-hour limit). The routes
  in `web/identity.POLLS` (`/process/status`, `/jobs/*`, `/review/N/reader`, `/settings/reading/self-test`) look the
  session up with `session_user(renew=False)`. Any new route a page polls on a timer goes in that list. The
  `last_seen` write and the delete of an expired session use a 0.2 s wait and are left to a later request when the
  database is busy (`users._briefly`): a page never waits behind another writer just to note that someone is still
  there. The delete of a session refused for its account (turned off, no authenticator app) keeps the normal wait,
  so an old session can't come back once the app is set up. Schema 20 adds six indexes: `invoices(export_batch)`,
  `feedback(invoice_id)`, `events(action, created_at)`, `sessions(user_id)`, `ephemeral(expires_at)` and
  `vendors(erp_id)`; on a 12,000-invoice database they take 0.09 s. (`web/identity.py`, `core/store/users.py`,
  `core/store/_base.py`; `tests/core/test_users.py`, `tests/web/test_server_web.py`, `tests/core/test_store_perf.py`)
- **D-151** · **One pick-up at a time, and the service host is told by its lock** (B-1585 to B-1590).
  - **Pick-up:** a scheduled `watch`, the dashboard's Process button (with uploads and retry) and `ap_coder process
    <files>` take one OS lock in the data folder (`mailbox.pickup_lock`). Each re-checks `find_by_hash` while it
    holds the lock, so a file is stored once.
  - **When it's busy:** a watch skips its check and says so. The dashboard and the command wait
    (`mailbox.wait_for_pickup`), because a person asked.
  - **The service host:** it holds `service\host.lock` while it runs. `service status`, `install` and `stop` judge a
    running host by that lock, never by the process number in `host.json`, which may belong to another process after
    a reboot. The host writes `host.json` straight after taking the lock, so the record is the running host's.
  - **`server-update`:** it uses the fixed task name ("AP Coder", or `--task-name`), never `server.json`'s, as the
    service commands do (B-1500).
  - (`ops/service.py`, `ops/server_update.py`, `intake/mailbox.py`, `ops/cli.py`, `web/pages/process.py`;
    `tests/ops/test_service.py`, `tests/ops/test_server_update.py`, `tests/ops/test_controls_watch.py`,
    `tests/web/test_web_process.py`)
- **D-152** · **Work typed after a session ended comes back** (B-1594). A 401 on any POST sends the person to
  `/login?next=<this page>`. Before that, the review screen keeps the form in the browser tab's sessionStorage
  together with the username. Back on that invoice as the same person, it becomes their draft; anyone else who opens
  that invoice in the tab drops it without seeing it. Nothing is kept on the server for a session that has ended.
  (`web/static/js/invoice.js`, `web/static/js/app.js`, `web/pages/login.py`; `tests/web/test_server_browser.py`)
- **D-153** · **What one web worker of the company server runs at once, and when it is started afresh** (B-1610;
  changes D-111's default of one worker per core, at most 8, and settles D-148's "a default measured in the load
  test").
  - **Threads:** in server mode the lifespan caps anyio's thread pool at 4 (`app.WORKER_THREADS`;
    `AP_WORKER_THREADS`, server.json `worker_threads`, 1-40).
    - **Why:** a request under way holds what it read (about 110 MB), and one process works on one at a time (the
      GIL).
    - **Measured** on a 3k seed, 30 people, 3 workers: worker peak RSS 1.48-1.63 GB → 0.53-0.60 GB, and 2,332 →
      7,451 requests in 5 minutes. With 8 threads: 0.68-0.76 GB and 3,188.
    - **A laptop** keeps anyio's 40.
  - **The long routes' lane:** `web/core.py HEAVY_ROUTES` run 2 at a time a worker, on threads beside the pool's
    (`core.run_heavy`, a limiter per event loop).
    - **Which:** reports, exports, Report a problem, backups and restore, and the buttons that wait on LM Studio or
      JD Edwards.
    - **A long wait:** one waiting 30 s gets a 503 "busy, try again in a minute".
    - **An async handler** of a long route calls `run_heavy` itself (`POST /exports/create`, which ran on the event
      loop: B-1610).
  - **No `limit_concurrency`:**
    - it counts idle keep-alive connections: with a limit of 20 and 25 idle, every request got a 503 with no load
      (measured);
    - it refuses instead of queueing;
    - it doesn't bound the sockets a worker accepts.
  - **A worker started afresh** (`server.Recycle`):
    - **When:** past its share of the memory (at least 2 GB; `AP_WORKER_MAX_MB`), or after 50,000 requests plus up to
      a quarter (`AP_WORKER_MAX_REQUESTS`), once no job, backup, waiting import or long route of its own runs.
    - **How:** it stops accepting, uvicorn stops it gracefully 1 s later, and its supervisor starts another. One at a
      time (`.ap_coder_worker_restart.lock`).
    - **Not uvicorn's `limit_max_requests`:** its supervisor asks every worker "alive?" over a pipe whose both ends it
      holds, so it waited 30 s on a worker whose process was closing and replaced none meanwhile. `_Supervisor`
      leaves the ending worker alone (`.ap_coder_worker_restart.pid`).
    - **Logins are in the database:** nobody is signed out.
  - **Default workers** (`server.default_workers`): one per core but an eighth, and no more than fit at 1.5 GB each
    in the memory left once a quarter (at least 4 GB) is kept for the system, the background process and the
    database.
    - 32 vCPU / 64 GB: 28. 4 cores / 16 GB: 4.
    - `--workers` and server.json still decide when set.
  - (`web/app.py`, `web/core.py`, `web/server.py`, `web/jobs.py`, `web/pages/exports.py`, `web/templates/error.html`,
    `ops/service.py`; `tests/web/test_server_capacity.py`, `tests/web/test_server_scale.py`)
  - **Amended (B-1682 to B-1686):** a worker started afresh gives its requests under way up to an hour
    (`server.RECYCLE_GRACE_SECONDS`), not uvicorn's 25 s. What a request started after Recycle looked (a job, a backup,
    imports waiting: `app.own_work_running`) is waited for in its lifespan once its connections are closed. A stop of
    the service still ends it at `join_all`'s deadline (GRACEFUL_SECONDS + 5). `.ap_coder_worker_restart.pid` is
    believed ten minutes at most (`LEAVING_SECONDS`), and is dropped by the supervisor once that worker has ended and at
    the server's start. uvicorn 0.52 on (pyproject, requirements.txt). A worker due whose uvicorn server isn't found
    says so once.
- **D-154** · **The learning memory read once and refreshed, a vendor's lessons worked out once, an invoice opened in
  one request** (B-1620 to B-1622).
  - **The whole memory** (`Store.feedback_rows()`): one page reads it at a time in each process; the others asking
    meanwhile wait and share the list.
    - When lessons were only added since, only those (`id >` the newest kept) are read and merged in the memory's
      order.
    - It is read whole again when a lesson was removed, the newest kept is gone or changed (a restore), or more were
      added than the memory holds.
    - The stamp and the rows are read in one read transaction.
  - **One vendor's lessons** (`Store.vendor_lessons`): the history check's patterns, the GL accounts its lines went
    to, and its cost centers.
    - Worked out once from 8 columns, and kept for the latest 32 vendors until one of that vendor's lessons is added
      or removed.
    - Patterns are kept, not rows: the biggest vendor's rows were 120 MB.
  - **Past the memory's cap** (`learning_rows`): only the vendor's lessons ranked after the memory's last are read,
    kept for the latest two vendors.
  - **One request to open an invoice:**
    - the GET does what the screen's opening check did (the queue's confidence, the draft and its blocker), for a
      person who may edit;
    - invoice.js no longer checks on opening;
    - a check writes after all its reads.
  - **Fewer statements:** the held vendors' check reads their approved numbers in one query; the page reads the
    manager context, the events and the readers' state once each.
  - **Measured** on a 3,000-invoice seed with one vendor at 50,622 lessons: the big vendor's open went from 3.1 s, 442
    statements and 167 MB to 0.19 s, 91 and 9 MB.
  - (`core/store/learning.py`, `core/memory.py`, `ai/pipeline.py`, `web/invoice_view.py`, `web/pages/invoice.py`,
    `web/pages/review.py`, `web/core.py`, `web/static/js/invoice.js`, `coding/vendors.py`, `core/store/vendors.py`,
    `bench/load.py`)
  - **Amended (B-1687, B-1694):** the open's bookkeeping (confidence, the draft's blocker) runs in `Store.briefly`: 200
    ms at most behind another writer, else it is left to the next open or check. A draft is written before it is noted
    as kept. The page draws the draft with what its check fills in (a date as YYYY-MM-DD); the draft keeps what was
    typed.
- **D-155** · **The review queue from the invoices' summary, checked again in the background; the import page reads no
  file** (B-1630 to B-1634; changes D-88 where it says the queue re-checks in the page).
  - **What the queue reads** of an invoice in review is kept in its summary row (`bulk.queue_facts`, summary rules 2):
    - the stamps of its stored checks and of its queue re-check;
    - their findings, as (severity, code);
    - what its lines still need, without and with cost centers;
    - what its opening Pay from account is chosen from.
  - **Keeping it current:** the summary's triggers recompute that row on every write of an invoice, from any
    connection. GET /review reads only these columns, plus the coding of the rows it draws.
  - **The re-check** after a change of the checks stamp is done by `bulk.QueueRecheck` (`start_background_work`),
    looking every 5 s.
    - It takes the stale invoices 50 at a time, each batch in one write.
    - One process at a time: the `ephemeral` lease 'queue-recheck', taken over after 120 s without renewal.
    - A coding that can't be checked again is kept with `recheck.issues` null.
  - **On the company server** the queue never re-checks in the page, and says how many invoices still show their
    earlier checks. Bulk approval re-checks each invoice at approval. **On the laptop** the page still re-checks
    before drawing.
  - **The checks stamp** says whether a JD Edwards batch exists, not how many. A note on a vendor that is neither on
    hold nor added from the review screen is not a vendor master change.
  - **The import page:**
    - each pick-up keeps what it read of each file in `seen_files` (place, size, mtime_ns, SHA-256;
      `core/store/folder.py`);
    - a view lists the files whose size and time the pick-up hasn't read, or whose content no invoice has, in one
      query, reading none;
    - it is kept per process until the folder, a pick-up's notes or the invoices change.
  - (`coding/bulk.py`, `web/pages/review.py`, `core/store/summary.py`, `core/store/ephemeral.py`,
    `core/store/accounts.py`, `core/store/vendors.py`, `core/store/folder.py`, `web/pages/process.py`,
    `ai/pipeline.py`, `ops/cli.py`, `web/app.py`)
  - **Amended (B-1692, B-1693, B-1695):** the re-check ends between two batches when AP Coder stops
    (`recheck_queue(stopping=)`) and lets go of its lease; the stop waits up to 5 s for it (`RECHECK_STOP_SECONDS`), and
    a laptop's start drops a `queue-recheck` lease left behind. The import page's list is also worked out again after a
    minute (`process.LISTED_SECONDS`: a file written over in place changes no stamp). Files are listed and noted at the
    invoices folder's resolved place.
- **D-156** · **The big reports summed and picked by SQLite; the Spend workbook made in the background on the server;
  schema 24.**
  - **What SQLite now picks or sums:**
    - the controls report's one-person check: the period's approved invoices that may be over the limit;
    - the report's events: their details only where SQLite can't tell they left nothing open;
    - month-end's received-not-invoiced: POs, lines and invoices in three reads;
    - the Spend page's sums;
    - the JD Edwards duplicate checks: only the exported invoices and register rows with the batch's numbers;
    - Activity: counts and people off indexes.
  - **What stays in Python:** what is rounded, ordered, converted or decided. The old code is kept in
    `tests/reports_reference.py` and compared on seeded stores.
  - **The Spend data workbook** is written in openpyxl's write-only mode. On the company server a Clerk or above makes
    it in the background (`POST /spend/data`, job `spend-data`, one at a time), and the download is the last one
    made. On a laptop it is made on download.
  - **Schema 24** adds `events_created (created_at, action)`, `events_actor (actor)` and `erp_invoices_number
    (number_key)`.
  - **Not done:**
    - month-end at 100k is about 3-5 s;
    - an Activity search for a rare word decodes every event.
  - **Amended (B-1688 to B-1691, B-1697):** on the server each data workbook is made under a name of its own
    (`ap_coder_data.<time>.xlsx`); the newest is downloaded and older ones are removed once they can be (Windows
    replaces no file being downloaded). A job that fails says why on Spend. A restore removes `<data>/.cache/downloads`.
    The Spend figures are read in one read transaction (`Store.summary_snapshot`).
- **D-157** · **Search and the smaller pages read in SQL what they show; schema 22.**
  - **Quick search:**
    - `search.condition` is the quick search as one SQL condition over the summary's columns: exactly what `_score`
      finds.
    - The directory counts, sorts and pages it with its filters.
    - Over 50,000 matches: all are counted, and the directory's sort picks the export's 50,000.
  - **ERP register:** `erp_invoices.plain_name` and `raw_number` are filled at import, and for an older register by a
    migration that checks for the columns.
    - A search reads only the rows it may match.
    - Its "is it ours?" check is one query.
    - The ERP hits are shown 50 at a time ("Show more").
  - **Exports:** CSV and Excel read 1,000 rows at a time, and the workbook is write-only (`exports.streamed_sheet`).
  - **Duplicate audit:** only pairs that can be one, in the order every pair was compared before. SQLite 3.38+ reads
    the five fields with `->`.
  - **Supplier learning and Automation:** every vendor's `SupplierStats` from one query (`Store.all_supplier_stats`,
    `core/store/suppliers.py`). Supplier learning draws 50 vendors at a time.
  - **Report a problem:** the latest 500 invoices (`help.REPORT_INVOICES`), and it says so; `share-report` still
    covers every invoice.
  - **Import invoices:** checks the folder's files in one query.
  - (`erp/search.py`, `erp/directory.py`, `erp/exports.py`, `erp/dupaudit.py`,
    `core/store/{_base,invoices,summary,suppliers}.py`, `capture/supplier.py`, `ops/share_report.py`,
    `web/pages/{search,learning,settings_automation,help,process}.py`)
- **D-158** · **An async handler only awaits; its work runs on a threadpool thread** (B-1660).
  - **Why:** a uvicorn worker runs every `async def` on its one event loop, so database or file work there holds up
    every other request of that worker.
  - **The rule:** an async handler in `ap_coder/web` (pages and middlewares) only awaits the form (`posted_form`) or
    an upload's body. Then `return await run_in_threadpool(_work, request, form)`, where `_work` is a plain function
    marked `@one_connection`, as the review screen's Check does.
  - **No await needed:** the handler is a plain `def`. FastAPI runs it on the threadpool, and `page_router` gives it
    one connection (`manager.record`).
  - **Also on a thread:** `files.receive_sheet` reads and keeps an uploaded sheet, and the error pages are drawn
    there too.
  - **Long imports** (ERP register, vendor master, ERP history) still run whole inside their request, now on a thread.
  - **A code rule** (`tests/meta/test_code_rules.py`) walks every `async def` with `ast`. Outside an `await` it
    forbids `get_store`/`current_store`, store methods, functions given the store, `render`, `open`, and path reads
    or writes. What `run_in_threadpool` is given is exempt, and the lifespan is left out.
  - **Still listed for now:** the review screen's grid actions (`invoice.py`) and the upload check (`process.py`),
    until they are converted at the phase 2 integration.
  - (`web/pages/accounts.py`, `exports.py`, `insights.py`, `learning.py`, `manager.py`, `purchase_orders.py`,
    `vendors.py`, `settings.py`, `settings_hub.py`, `settings_jde.py`, `web/files.py`, `web/app.py`, `web/errors.py`;
    `tests/meta/test_code_rules.py`, `tests/web/test_server_scale.py`)
  - **Amended (B-1698):** nothing is still listed. The review screen's grid actions read the form on the event loop and
    run `@one_connection` functions on the threadpool, and `process_upload` is a plain `def`. The code rule also counts
    a message kept for the next page (`done(request, url, "…")`, `keep_flash`, `with_flash`) as a write.
- **D-159** · **What a web worker keeps between requests, and how it knows it still holds** (phase 2 part 2's CPU:
  `docs/archive/scale-2026-11/cpu.md` items 1-3, 5, 7 and 10).
  - **Why:** the check and the invoice page redid, on every request, work whose answer changes a few times a day (the
    vendor master as a dict of 20,000 vendors, twice; the held vendors' approved numbers, twice; the GL suggestions'
    index of 50,000 lessons, again for each vendor in turn; the queue's checks and list): 82% of the CPU asked at 100
    people.
  - **The rule:** a kept answer holds only while a stamp, read from the database in the same request and before the
    work, says nothing it depends on changed. It does not matter which process changed it (another worker, the
    background process, plain SQL). Never a timer, never "this process wrote it".
    - `Store.summary_memo(name, compute, topic="all", events="", changes="", asked=None)` (`core/store/summary.py`)
      reads the stamp: the database's id (`summary_id`, new after a restore or a summary built again); with `topic`,
      that summary topic's generation (`summary_generation/<topic>`; "" reads none, and leaves the summary as it is);
      with `events`, the count of the events of that action; with `changes`, a count the triggers keep
      (`changes/<kind>`); and `asked`, what the answer is for (a list's invoices, the vendors asked), compared with ==.
    - The latest answer of each name is kept, 64 at most a process. One page works an answer out; the others asking
      the same meanwhile (the name and `asked`) wait for it (`MEMO_WAIT`, then work it out themselves). Another ask of
      the name works its own out alongside (B-1722: three Month-end periods took 0.5 + 1.0 + 1.5 s, now 0.5 s each);
      the locks no page holds are let go past `COMPUTING_KEPT` (256). The answer is shared: read it, don't change it.
    - **Only what is committed is kept.** `conn.in_transaction` is read with the stamp. Inside a write under way the
      stamp counts that write's own changes and the answer reads them, and the write may yet be undone: a later
      change, by anyone, would bring the count back to the same number. There the answer is worked out afresh, neither
      taken from what is kept nor kept, and without waiting for the compute lock (the page holding it may need the
      write's lock). So is one asked in a read transaction (`summary_snapshot`), or by a call that brought the
      summary up to date (the next call keeps it).
  - **Counts kept by triggers** (in `summary.SUMMARY_TRIGGERS`): `_COUNT` adds one to `settings('changes/<kind>')`
    AFTER each INSERT, UPDATE and DELETE, in the same transaction as the change, however it is made:
    - `vendors` (`vendors`): the vendor master's ERP IDs, its GST/HST numbers, the vendor list;
    - `approved` (`invoices`): an invoice approved or no longer approved, or its `vendor_key`, `final_output` or
      `ai_output` changed while approved; one added or deleted approved. An export changes nothing, nor a confidence;
    - `suppliers` (`supplier_outcomes`), `readers` (`reader_outcomes`), `purchase_orders` (`purchase_orders`,
      `po_lines`), `register` (`erp_invoices`), `lessons` (`feedback`), `situations` (`lessons`, `lesson_approvals`);
    - each vendor's `changes/vendor/<key>`: an invoice of its added or deleted, or a column `vendor_invoices` lists
      changed (`status, export_batch, vendor_key, vendor_name, invoice_number, invoice_date, currency, grand_total,
      created_at, reviewed_at, final_output, ai_output`; not a check's confidence). An update counts OLD and NEW, so
      a move between vendors counts for both.
    - **Cost:** about 5-9 µs a row changed, whatever the trigger's body. 200,000 register rows: 1.2 → 2.8 s inserted
      alone, 4.7 → 6.5 s through `import_erp_register` (a rare import, on a thread). Most writes change one row.
  - **Triggers whose SQL changes:** at every start `make_triggers(conn, missing=True)` (from `_summary_schema`, in the
    same transaction) makes those missing and drops and makes again any whose stored SQL differs from
    `SUMMARY_TRIGGERS`: a later version may change a column list without building the summary again.
    - A backup's check (`backups._check_backup_file`) accepts a trigger whose name is in `SUMMARY_TRIGGERS`, whatever
      its SQL, and still refuses any other trigger and every view.
    - The restore makes ours again in its private copy (`_own_triggers`, after the read-only check, before
      `source.backup(target)`), so the SQL a backup gave them never runs, not even on another worker's change while
      the restore goes on. A backup where one of ours can't be made (its table missing) is refused.
    - The copy is the database for minutes before its migration (the data folder's files are copied first), the
      workers answering. So it also gets the counts' triggers whose tables it has (`make_triggers(counts=True)`; a
      backup made before D-159 has none) and a new `summary_id`: a change meanwhile is counted, and no answer kept
      before is taken when a count comes back to its number. The migration gives it another `summary_id` (B-1720).
    - **Going back:** a version before this one refuses the backups made by this one (they hold triggers it doesn't
      know), and keeps those triggers in its database. `server-update --back` restores the pre-update backup, made by
      the older version (`docs/SERVER.md` §11, `docs/UPGRADING.md`).
  - **Two summary topics** (`summary.TOPICS`; no column changes, so no summary is built again):
    - `queue`: what the queue reads of an invoice in review (`QUEUE_READ`, the columns `bulk.queue_checks` reads). One
      the summary could not work out (`broken`) is read again from the invoice, so each change to it counts.
    - `listed`: the queue's invoices (review, parked, pending) as `list_invoices` lists them (`LISTED_COLUMNS`, its
      sort columns and the confidence included).
  - **What is kept** (its stamp; what it holds at 100k, by tracemalloc):
    - **The vendor master's ERP IDs** (`vendor_ids`): `changes/vendors`; 6.4 MB. Shared: no caller changes it.
    - **The master's GST/HST numbers** (`master_by_tax_number`, for an invoice not found by name): `changes/vendors`;
      6.8 MB. Only the vendors found are read, and `same_tax_number` decides on them.
    - **The held vendors' approved numbers** (`approved_numbers`): `changes/approved` and the vendors asked; 0.07 MB
      for 193 held vendors. When read, it seeks the vendors' index (`+status`): 218 → 19 ms. Each caller gets lists
      of its own.
    - **The queue's checks** (`bulk.queue_checks`): `queue`, the checks stamp, laptop or server, the invoices asked;
      15 MB for 2,740 invoices. Fewer than `KEEP_FROM` (50) invoices are worked out each time. Each caller gets rows
      and lists of its own; the findings in them are read-only (`MappingProxyType`, made so in `_today`): a copy of
      each of the 34,000 findings took 18 ms a call, the rows and lists 2.5 ms.
    - **The queue's lists** (`list_invoices` of review, parked, pending): `listed`; 2.8-3.1 MB each. Each caller gets
      copies. The invoice page's Previous and Next read the review one (`navigation`).
    - **The GL suggestions' index:**
      - the shared memory's index is built once per change of the lessons (`suggest._MEMORY`: the same list while
        D-154's stamps hold);
      - a vendor's older lessons past the cap (`learning_rows` returns `memory.WithPast`: the shared rows, then those)
        extend it (`suggest._with_past`, `_extended`, `Index.extended`): exactly what a whole build gives
        (`tests/ai/test_coding_no_ai.py`, many vendors in turn), in 12-150 ms (1,400 to 43,000 older lessons) instead
        of 0.2-0.37 s; 0.33-0.46 MB each, 11.7 MB for 32 (the bench's 9,830 patterns of 167 words);
      - kept for 32 vendors and 60,000 of their lessons per process (`PAST_KEPT`, `PAST_ROWS_KEPT`), the latest used
        always; the store keeps those lessons the same way (`_PAST_MEMORY`, 2 vendors before): 1.3 KB a lesson, 78 MB
        at 60,000. A memory extended from the shared rows lets go of every one extended from others: each holds all
        of its shared rows' memory (61.6 MB at 50,000 lessons), and 32 vendors asked between approvals kept 681 MB
        (B-1719; 45.6 MB now).
    - **A line's suggestions** (`_Memory.said`): with the memory and the reference data they came from; 1,024 per
      memory, the latest used last, about 2.4 KB each. Callers get copies.
  - **Measured** (the 100k seed read-only, an `immutable=1` URI; one process, CPU, medians of 20, the checks and the
    pages of four vendors in turn; JD Edwards in use, as in cpu.md, which asks `vendor_ids` twice a check):
    - the check 1,000 → 55 ms (743 → 56 without JD Edwards);
    - the invoice page 1,717 → 762 ms; its vendor list (D-160) is most of what is left;
    - `/review` 325 → 131 ms (105 alone): the 50 rows drawn 31 ms, `all_vendor_terms` 23 of them, `attention` 19, the
      template 14, `queue_checks` 10 (the checks stamp 4);
    - the page's Previous and Next 48 → 7 ms (3 alone).
    - (The first build, lost, measured the check 1,244 → 96 ms, the page 1,811 → 544, `/review` 247 → 61 and Previous
      and Next 26 → 1, on another seed.)
  - **Not done:**
    - a vendor's invoices (`vendor_invoices`) and the vendor list in the page: D-160's;
    - the memory's whole index once per worker after each approval (0.27 s), then each vendor's extension again;
    - the queue's answers refreshed row by row after a park: a park moves `queue` and `listed`, and the next `/review`
      works them out whole (98 ms and 35-44 ms);
    - the rest of `/review`: `all_vendor_terms` (the terms of 20,000 vendors read on each page), `attention`;
    - the extended index's size in its cap: 0.33-0.46 MB a vendor here, but a big vocabulary could make it 2.6-4.4 MB
      (review 2b's probe); past 1 MB a vendor, count each entry's size in the cap.
  - (`core/store/summary.py`, `core/store/backups.py`, `vendors.py`, `learning.py`, `invoices.py`, `core/memory.py`,
    `coding/bulk.py`, `coding/vendors.py`, `ai/suggest.py`, `ai/line_match.py`, `docs/HOW_IT_RUNS.md`;
    `tests/core/test_kept_answers.py`, `tests/core/test_backups.py`, `tests/erp/test_invoice_summary.py`,
    `tests/ai/test_coding_no_ai.py`, `tests/ops/test_real_world_findings.py`)
- **D-160** · **The review screen made light: the vendor list fetched and kept, page pictures drawn once for all
  workers, a reader poll of a few bytes, each vendor's invoices kept** (scale goal, phase 2 part 2; rebuilt from
  `docs/archive/scale-2026-11/part2/`, its reviews' findings designed in).
  - **Why:** on the 100k database the 19,000-vendor list was built again for every invoice opened and was 3.3 MB of
    its 3.6 MB. Each web worker drew the page pictures it was asked for (35 ms, 125 ms at scale 2) into its own 96 MB.
    The reader poll put its panel in again every 20 seconds. Every check read all of its vendor's invoices (430 ms
    for a vendor of 7,812).
  - **The vendor list:** `GET /review/vendors.json?v=<version>` (READ, as the screen; no bank details in it).
    `invoice.vendor_list` keeps, per process and per vendor-list stamp (`summary_memo`, `changes/vendors`; without a
    vendor master also the `approved_vendors` topic): the JSON, it gzipped, its version (its hash), its count, and the
    first vendor of each vendor key and GST/HST business number. The page holds `#vendor-seed`: the address, whether
    a master enforces it, the count, the vendors it names as it opens (the invoice's, the one whose GST/HST number it
    shows) and what it looked up, so a vendor it doesn't name is known not to be in the list. An editable screen
    fetches the list as it opens (`app.fetchWithin`: 30 s); a read-only one doesn't. An open vendor list is drawn again
    when it comes (`picker.refresh`); a vendor added on the screen is kept when it comes.
    - **Before it comes** (review 2c item 2) the line under the vendor field says only what the seed knows: a vendor in
      it, or one added here. Any other vendor is "Checking the vendor list…", in the plain tone.
    - **A fetch that failed** (review 2c item 1: dropped, a 500, a 502/503, the deadline) is asked for again at the
      next need (the list opened, the vendor typed, the line drawn again), at least 5 s after the last failure, 5 times
      in all. Meanwhile, with a vendor master, the line says "Couldn't load the vendor list: the check on approval
      still compares the vendor with it." It never says "Not in the vendor list", nor whose GST/HST number is printed;
      without a master it says nothing. The list's "add as a new vendor" row waits for it; the fill from the list
      waits once for a fetch under way, then fills from what is there.
  - **Browser caching:** on a laptop, `private, max-age=31536000, immutable` while `v` is the list's version
    (`no-cache` for another), with a weak ETag (304) and `Vary: Accept-Encoding`.
    - **On the server B-1070 stands (decided):** every answer behind the login is `no-store`, the vendor list and the
      page pictures included, so nothing of the company's stays in a shared computer's browser cache after logout.
      Each editable screen fetches the list from the worker's memo: 350 KB gzipped and about 14 ms of CPU, on the
      company network. Exempting them is one line in `identity._no_store`, a security decision, not a speed one.
  - **Page pictures (server):** in `<data>/.cache/pages/<2>/`, named by the file's SHA-256, the page, the scale, the
    DPI, the size cap and `DRAWING`. Written under a name of its own, then `os.replace`; on Windows retried, and left
    when the same picture is there (B-1689's way). At most `PAGES_KEPT_BYTES` (1 GB), the oldest written removed first,
    down to three quarters, never the newest; looked at as a worker writes its first picture there and after each
    sixteenth of the cap. A full disk or a folder that can't be written draws each time. A worker keeps no picture in
    memory there. The laptop is unchanged.
    - **Room on the disk** (review 2c item 4): the cap counts the pictures, not the disk. No picture is written where
      the disk's free space would fall under `backups.BACKUP_RESERVE` (512 MB), so the database and its backups keep
      their room; the free space is read every 5 s at most (`ROOM_EVERY_SECONDS`), counted down by what was written
      since. The picture is still served, drawn each time.
  - **The reader poll:** the page's `data-tag` (`reader_tag`, what its panel shows); the poll (D-161's `app.poll`,
    whose address may be a function, asked at each look) sends `?seen=<tag>`. While the panel is still that, the answer
    is `{state, poll, tag, same}`, without it, and nothing is drawn again (the same answer each time: `poll` waits
    longer).
  - **Each vendor's invoices:** `Store.vendor_invoices` kept per process for the vendors asked most recently, its own
    cache (not `summary_memo`): at most `VENDOR_ROWS_KEPT` (50,000 rows: about 1 KB a row, 50 MB) and
    `VENDOR_KEYS_KEPT` (4,096 vendors: review 2b item 4, each name typed is asked for, most with no invoices), with a
    running row total. One vendor is kept up to half the cap, so it never empties it of the others (B-1721); one of
    more rows is read once in a page's session instead (`_session_memo`: the check's two calls). Stamped by
    `summary_id` and `changes/vendor/<key>` (D-159's triggers; a check's confidence is not listed). Read inside a write
    under way: afresh, not kept. Each caller gets rows of its own. The rows no longer carry
    `adjusted_confidence`/`requires_review` (read by nothing).
    - **The cap** (B-1721, measured on the 300,000-invoice seed, read-only): its biggest vendor has 23,174 invoices,
      kept in 23.9 MB (1,031 bytes a row; 924 for one of 12,611), read in 1.2 s on a busy machine, each caller's copy
      21 ms. Its check's two calls: 2 × 1.2 s each check under the 20,000 cap (never kept), about 45 ms once kept.
  - **Measured** (100k database, read-only; one process's CPU, medians; before: 7d13f0b, where A1, A2, W2c and W2d
    are merged; after: D-160 on it):
    - the page: 3.64 MB → 332 KB; the same invoice again 2,391 → 1,302 ms, four vendors in turn 1,724 → 870 ms.
      Without the "Learned" hints (`_lesson_hints`, 1.1 s for the biggest vendor, below): 1,220 → 180 ms and
      1,444 → 638 ms. (On the A1 base alone, d5c8013: 2,454 → 1,405 ms, and 1,416 → 429 ms without the hints.)
    - the vendor list: 19,008 vendors, 3.03 MB of JSON, 351 KB gzipped, served in 16 ms (a 304: 6 ms); built in about
      0.8 s once per change in each worker, about 21 MB kept; the seed 354 bytes.
    - a check's two calls (`vendor_findings`, `expected_tax_numbers`): 422 → 35 ms for a vendor of 7,812, 162 → 34 ms
      (4,196), 107 → 18 ms (2,872), 48 → 7 ms (1,227).
    - a picture: drawn 35-42 ms (scale 1, 73 KB) and 122 ms (scale 2, 156 KB); read back from the shared folder 0.1 ms
      (the route's own cost aside). A poll: 268 → 64 bytes, its CPU unchanged (17 ms; the first build found 9 ms of
      it in `reading_status`).
  - **Not done:** the "Learned" hints' `lessons_for` counts each lesson's approvals with two correlated subqueries
    (1.9 M rows of `lesson_approvals` on the 100k database: 1.1 s for a vendor of 30 lessons); `reading_status` costs
    9 ms on every page, queue and poll; a JD Edwards sync rewrites every vendor's `erp_bank`/`erp_hold`, which resets
    the vendor list in every worker (phase 4); the pilot tester's page pictures (`pages/manager.py`) could use the
    shared folder; once the 5 tries are used, only a reload fetches the vendor list again.
  - (`web/pages/invoice.py`, `web/document.py`, `web/roles.py`, `web/static/js/invoice.js`, `picker.js`, `app.js`,
    `templates/invoice.html`, `_invoice_document.html`, `core/store/vendors.py`, `bench/load.py`;
    `tests/web/test_web_invoice.py`, `test_web_picker.py`, `test_web_document.py`, `test_server_web.py`,
    `test_server_security.py`, `test_roles.py`, `tests/core/test_kept_answers.py`, `test_store_perf.py`,
    `tests/bench/test_bench_load.py`)
- **D-161** · **Static files from memory at kept addresses, no compression, polls that wait, and a busy web worker
  that hands its connections back** (B-1712; the phase 2 load run's 16-57 s static waits and its workers at 0.34 to
  1.0 cores).
  - **Static files from memory:** `core.read_static` reads the styles, scripts, fonts and logo once when AP Coder
    starts. `app.StaticFromMemory` answers a GET or HEAD of one on the event loop: after the Host check, with the
    security headers and the access log, before the session, the errors page and the backup check.
    - At its plain address, the same status, headers and 304s as Starlette's `StaticFiles` (its ETag and
      Last-Modified, so a browser's copy stays good across the change), laptop and server; a test compares every
      file, both modes, for GET, HEAD, Range, If-None-Match, If-Modified-Since, POST and a file that isn't there.
    - `StaticFiles` still answers what memory doesn't: a POST, a file that isn't there, a Range (at the file's plain
      address). A file whose real path is outside its folder (a link out of it) is left out, as `StaticFiles` refuses
      it (`follow_symlink=False`).
    - Why: a static file costs about a millisecond, but `StaticFiles` read it on one of the worker's 4 threads
      (D-153), behind pages of 1-2 s. Server CPU a request through uvicorn (server mode, four runs of 3,000 each):
      a 304 0.85-1.03 → 0.41-0.47 ms, app.css 1.49-2.52 → 0.49-0.50 ms, app.js 1.46-2.52 → 0.45-0.46 ms, the 373 KB
      icon font 3.26-4.38 → 0.50-0.56 ms; and no thread.
    - A file edited while AP Coder runs is answered as it was at the start (a developer restarts `serve`).
  - **Kept addresses:** the pages link each file at `/static/<build>/...` (`/fonts/<build>/...`,
    `/assets/<build>/...`) through `core.static_url`, with `Cache-Control: public, max-age=31536000, immutable`; the
    kept stylesheet names the fonts at theirs (its own ETag).
    - The build is a hash of the files' contents, not the commit: a checkout's commit doesn't change with an edit not
      yet committed (the edited script would be kept a year), and an update that changes no file keeps everyone's
      copies.
    - Another build's address (a page drawn before an update) answers the file as it is now with `no-cache`, for
      `/static`, `/fonts` and `/assets` alike: never kept a year under an address that isn't its own. The plain
      addresses answer as before (`/static` `no-cache`, `/fonts` and `/assets` without Cache-Control). Pages stay
      `no-store`.
    - Every template uses `static_url` (a test fails on a plain address: a page script loaded at its plain address
      would be a second app.js module beside the page's own).
  - **Measured** (bench load, a 3,000-invoice seed, 60 people, think 4 s, 2 web workers, 180 s; the 4 cores shared
    with other agents).
    - The first build (lost before it was pushed, rebuilt as written here), before → from memory → kept addresses
      too: requests 22,834 → 26,344 → 6,045 (27,506 more from the browsers' caches); `/static` p95 1.82 s → 130 ms →
      67 ms; `/fonts` and `/assets` p95 1.79 and 1.96 s → 25 and 52 ms; pages p50 3.25 s → 466 ms → 269 ms, p95
      49.4 s → 6.10 s → 1.67 s; actions p95 50.8 s → 12.3 s → 2.05 s; requests over 30 s 745 → 0 → 0.
    - The rebuild, all of D-161 against main, on a machine busier than the first build's (other agents' tests on its
      cores), saturated both times: the people's work done +63% (requests other than static files 1,319 → 2,147;
      invoices opened 140 → 252, checks 170 → 356), the workers at 1.11 and 0.33 → 1.07 and 1.02 cores; `/static` p95
      7.51 s → 212 ms, `/fonts` 13.8 s → 59 ms; the pages stayed past what the people asked (p95 64 → 62 s).
    - The rebuild, lighter (30 people, think 10 s): the same work in 5,193 → 1,681 requests (5,696 more from the
      caches); `/static` p95 493 → 70 ms; pages p50 792 → 709 ms, p95 3.34 → 3.19 s; actions p95 1.95 → 1.56 s.
  - **No compression** (laptop and server).
    - gzip's CPU a MB of AP Coder's HTML and JSON (the first build, on the 100k database): level 1 4.4-6.6 ms, level
      4 7.3-11.7, level 6 14-18, level 9 (Starlette's `GZipMiddleware` default) 60-75.
    - The review screen (3.6 MB there, 565-630 ms of CPU): level 1 16-24 ms for 0.53 MB, level 9 241-271 ms. The
      queue (85 KB): level 1 0.5 ms for 9 KB.
    - On the office's gigabit network 3.6 MB take about 29 ms, which cost the server nothing; level 1 would cost 4-5
      core-s a minute at 100 people (3% of the 4-core budget) and save no time anyone sees.
    - Where it matters (a VPN at 20 Mb/s: 1.4 s against 0.2 s), IIS in front compresses `text/html` and
      `application/json` only, at its lowest level (SERVER.md); never a download (CSV, xlsx, PDF, PNG, ZIP). Static
      files are not compressed either: each browser fetches them once a build. Revisit if the review screen's
      vendor list leaves the page.
  - **Polls** (`app.js` `poll`, used by `pollJob` and the review screen's reading):
    - Nothing while the tab is hidden; a look at once when it is shown again.
    - After an answer the same as the last (or none), the wait is half as long again, up to three times the pace (a
      job's at least 5 s); a change starts again at the pace.
    - A look answered by the login stops it (B-1712): a 401, or a GET sent on to `/login`, or to `/who` (a laptop
      shared on the office network asks a colleague's name again once AP Coder started again: a new key makes the
      cookie no good). The job pages then go there and come back after it; the review screen stays as it is, and
      its next check goes to the login with the edits kept (B-1594).
    - `watchJob` asks `/jobs/<name>?only=done,note`: the state, done and note, not the job's 500 lines.
  - **Deadlines** (`fetchWithin`, AbortController): a look 30 s, an action (`post`), the setup report and the words
    on the page (a scan's pages may be read again, B-1715) 5 minutes; past it, the existing "didn't answer" wording.
    `post(url, body, {ms})` takes 0 for none.
    - A form marked `data-long` is sent with none: the actions the server runs whole in the request, however long
      they take. Restore (a safety backup first, of a database of gigabytes), Past coding → Teach, the imports (GL
      accounts and cost centers, bank accounts, vendor master, payment register, purchase orders, the JD Edwards GL
      map and worksheet), a Finance Hub run, the bulk approval of clean invoices (its list grows with the queue)
      and the page reader's model loaded (up to 5 minutes a try, B-1717). The JD Edwards posting and sync run as
      jobs already; an export batch keeps its 5 minutes.
  - **The workers' balance:** a busy worker hands its connections back (`server.HandBack`): while requests wait for
    one of its threads, or for its long routes' lane (`core.run_heavy`: two reports taking its CPU, more waiting),
    its answers say `Connection: close`. A pool or a lane full with nobody waiting is not busy. `KEEP_ALIVE_SECONDS`
    stays 75.
    - How a connection finds a worker: the workers listen on one socket, and the first to `accept()` takes it, on
      Linux (epoll; uvicorn sets no `SO_REUSEPORT`) and on Windows (uvicorn's supervisor shares the listening socket,
      B-1711, and each worker runs the selector loop, so every worker's `select()` wakes: B-1441 saw two woken for
      one). A free worker's loop usually wins it: a busy one waits for the GIL behind its threads.
    - What kept people on a busy worker is the browser keeping its connections (minutes: Chrome up to 5, Firefox
      about 2) and the server 75 s. The harness's people closed theirs after 5 s unused (httpx's default), which hid
      it: `bench load --keep-alive 300` keeps them as a browser does (the default stays 5, so earlier runs compare).
    - Measured in the first build with people keeping connections 300 s: 50 people, think 10 s, 4 workers: one
      worker did 54% of the work. Saturated (60 people, think 4 s, 2 workers), three pairs back to back: pages p95
      5.2 / 24.0 / 33.3 s with 75 s alone, 4.1 / 19.6 / 16.6 s with HandBack; requests over 30 s 0 / 147 / 343
      against 0 / 74 / 9. With the default people (5 s) no difference beyond the noise.
    - The rebuild, people keeping connections 300 s (30 people, think 10 s, 2 workers), two pairs, the second in the
      other order: the workers at 0.40 / 0.90 and 0.48 / 0.81 cores without HandBack, 0.67 / 0.65 and 0.64 / 0.64
      with it; pages p95 4.68 and 19.7 s without, 6.41 and 5.44 s with (the machine's load average 4-6 from other
      agents' tests: the balance is the steady result, the latencies are noise).
    - The rebuild's saturated pair (above) at the default 5 s: 1.11 / 0.33 cores before, 1.07 / 1.02 after.
    - A shorter keep-alive was refused: at 5 s, 12 requests got no answer in the saturated run (B-1295's race: an
      idle connection closed as the browser sent on it).
    - `Connection: close` comes in the answer: it arrives whole, the browser opens a new connection for its next
      request, nothing is lost, and IIS ARR in front does the same. A new HTTPS connection costs the worker that
      takes it 1.2 ms (0.4 ms resumed).
  - **Not done:** the import's poll still sends its lines (up to 500, 57 KB; process.js shows them all); an upload
    (XHR) has no deadline (it may take minutes); a poll without a session still draws the login page once before
    it stops (`identity.py`); the access log writes on the event loop (`security.py`).
  - (`web/core.py`, `web/app.py`, `web/server.py`, `web/templates/*`, `web/static/js/{app,invoice,help}.js`,
    `bench/load.py`; `tests/web/test_web_app.py`, `test_server_browser.py`, `test_server_capacity.py`,
    `test_web_settings.py`)
- **D-162** · **The reports and pages still slow at 100,000 invoices: kept per change, listed a part at a time, or read
  without waste** (phase 2 part 2).
  - **Kept in each process** (`Store.summary_memo`, D-159's rule: a stamp read from the database before the work), on
    the counts D-159's triggers keep (`SUMMARY_TRIGGERS`, `changes/<kind>`: any insert, update or delete of the
    table, on any connection):
    - "suppliers" (`supplier_outcomes`): every vendor's supplier learning record (`all_supplier_stats` without its
      fields, asked the vendors) and the drawn vendors' field figures (`supplier_fields`): Learning → Supplier
      learning, Settings → Automation;
    - "readers" (`reader_outcomes`): the readers' scorecard: Settings → Reading, Learning → Readers;
    - "purchase_orders" (`purchase_orders`, `po_lines`), with the summary's generation: month-end's received, not
      invoiced PO lines, per period end (`accruals._received_not_invoiced`);
    - "register" (`erp_invoices`), with the summary's generation: the duplicate payment audit's pairs
      (`dupaudit.find`: its page and its CSV);
    - "lessons" (`feedback`): each vendor's lesson counts (`vendor_summaries`: Vendors, Statements, the JDE
      worksheet);
    - "situations" (`lessons`, `lesson_approvals`): the Lessons tab's rows (`learning.lesson_rows`);
    - the summary's generation alone: the Spend page's figures and currencies, per period, currency, GL categories
      and rates (copies; `spend.figures`, `spend_currencies`; `read_figures` reads them now).
    - **What a trigger costs:** about 37% more time on a 200,000-row import (A1's measurement in D-159: 4.7 → 6.5 s
      through `import_erp_register`, a rare import, on a thread); most writes change one row.
  - **Listed a part at a time, with Show more** (how many in the address, `web.core.shown_count`, which Supplier
    learning's count uses too; the CSVs, tiles and search keep every row; with every row asked, each page is the one
    it was): Sales tax's claims and self-assessments (500 each: `rows`, `self_rows`), Vendors (200: `shown`, kept
    when one is opened), Purchase orders (200: `shown`). On the laptop too: a list under these sizes shows as before.
  - **Read without waste:**
    - `jde.resolve_an8` reads the vendor IDs without copying them for each invoice or vendor;
    - Learning → Coding accuracy counts the approvals with SQLite (`insights.approval_counts`, as Activity);
    - Settings → Data counts the invoices folder with `os.scandir` (0 for one it can't list, B-1723);
    - `vendor_summaries` reads only the listed vendors' rows of the vendor list;
    - `clean_fields_needed` (a pure search, asked twice a vendor) is cached.
  - **What stays true:** a kept answer is what reading again gives (a two-Store test each,
    `tests/core/test_kept_answers.py`, each failing without its count). Memory kept a worker at 100,000 invoices
    (`tracemalloc`): the supplier records 28 MB (47 MB at the peak of working them out, which each request took
    before); the rest under 2 MB each (the audit's pairs 1.7, the Lessons tab 1.1, the lesson counts 0.9, Spend 0.6,
    month-end 0.2, the field figures 0.1; the scorecard and the currencies a few KB).
  - **Under load:** the summary's generation changes with every invoice written, so month-end, the audit and Spend are
    worked out again after each write. They help one person and pages asked again between writes, not 100 people.
  - **Not done:**
    - `/search/export.xlsx` (9-10 s at 50,000 rows): 70% of it is openpyxl writing 450,000 cells. A job per person,
      as the Spend workbook, or a lighter writer.
    - `/activity/events.csv` (11 s, 416,000 events): streamed as it is read (B-1597), so the download starts at once.
    - Month-end worked out afresh: 8-9 s (69,000 codings read and decoded). A summary table of each invoice's
      PO-billing lines (summary rules 3) would make it about 1.5 s.
    - Insights (1.8-2 s, within a report's 3 s); `/process` (a seed artefact: no `seen_files`).
  - (`core/store/{suppliers,capture,vendors}.py`, `erp/{accruals,dupaudit,spend,jde}.py`, `capture/supplier.py`,
    `web/core.py`, `web/pages/{learning,settings,sales_tax,vendors,purchase_orders}.py` and their templates;
    `tests/core/test_kept_answers.py`)
- **D-163** · **RapidOCR 3 runs PP-OCRv4 too, read exactly as rapidocr-onnxruntime read it, on every Python; that
  package is gone.** The first read, which the rule reader and the confidence table are tuned on, was
  rapidocr-onnxruntime 1.4.4, published for Python up to 3.12: on 3.13 and 3.14 scans were read by PP-OCRv5 alone (its
  second read the same model on an enlarged page), unless the launcher's install past its metadata worked.
  - **What 1.4.4 ran:** PP-OCRv4 mobile det and rec (`ch_PP-OCRv4_det_infer.onnx`, `ch_PP-OCRv4_rec_infer.onnx`, its
    characters in the model) and the v2.0 angle classifier (`ch_ppocr_mobile_v2.0_cls_infer.onnx`), on ONNX Runtime.
    Pages over 2,000 pixels a side shrunk; det with the smaller side made 736, threshold 0.3, box threshold 0.5, unclip
    ratio 1.6, dilation, the fast score, boxes under 4 pixels dropped; the angle classifier on (0.9); rec at 48 x 320
    in batches of 6; lines scoring under 0.5 dropped.
  - **RapidOCR 3.4.5** carries the same three files in its package (the same SHA-256), and its default settings are
    those. The pre- and post-processing, the crops and the ONNX Runtime session options are the same code. Two steps
    differ; `capture/layout.py` (`_read_as_rapidocr_onnxruntime`) takes them as 1.4.4 did:
    - the boxes' order: 1.4.4 sorts them by their top left corner and swaps neighbours under 10 pixels apart; 3.4
      groups them into lines first, which orders some differently, and the order sets which lines are recognised in
      one batch (`_boxes_in_order`);
    - a line's score: 3.4 rounds each character's probability, and their mean, to 5 decimals (`_decode_lines`).
    - 3.4 also drops a line whose text is blank; 1.4.4 never gave one in the lines measured below.
  - **The proof** (Python 3.12 with both installed, ONNX Runtime 1.31.1):
    - **The OCR:** the 229 page images the app gives the first read (78 bench scans' pages, 73 of them straightened and
      enlarged for a second read, 26 supplier-stream pages, 4 photos on a desk, 24 pages turned 90, 180 or 270 degrees
      and their 24 enlarged copies): all 15,335 lines the same boxes, texts, scores and order as 1.4.4's, compared
      whole. 1.4.4 again on 30 of them: the same. Without the two steps (14 images), every score differed (by up to
      1e-5) and 7 had lines in another order.
    - **The reading:** `scripts/same_behaviour.py --ref 62d2590`: tax, approve and read the same.
    - **The benches** (`--quick --scanned 1`): `run`, 24 scans, the same before and after (field accuracy 100.0%,
      verified precision 100.0% at 50.7% coverage, every invoice right, no silent error, ECE 0.0226; its failures and
      evidence the same bytes). `lines`, every invoice a scan (B-1724): descriptions right 43.1% before and after;
      the 17 AP corrected all right the next time, GL right 100%.
  - **On Python 3.13 and 3.14** a venv made with `pip install -e ".[ocr]"` had no 1.4.4 (the launcher's had it). Now it
    reads exactly as 3.12 does: the read probe's 60 readings are the same bytes as 3.12's before, and the benches' cases
    the same. Against its PP-OCRv5-alone reading: `run` field accuracy 98.7 → 100.0%, invoices all right 87.5 → 100%,
    no silent error 95.8 → 100%, ECE 0.0387 → 0.0226, verified coverage 52.4 → 50.7%; `lines` descriptions right on
    the first reading 87.5 → 43.1%: 30 of the 33 lost differ only in spaces (PP-OCRv4 runs a scan's words together,
    "Palletwrap18in.x1500ft"), each right after AP's one correction. Taking line descriptions from the PP-OCRv5 read
    is not done here: the reader and its confidence table are tuned on PP-OCRv4's reading.
  - **The bundle:** the rapidocr-onnxruntime wheel (14,915,192 bytes, one for every target) goes; the PP-OCRv4 det and
    rec (15,603,475 bytes, stored as the ZIP stores every model) join `models/` as PP-OCRv5's are, so
    `scripts/fetch_models.py` puts back a damaged copy and `--check` checks them: 0.7 MB more. Left out, the bundle
    would be 14.9 MB smaller (RapidOCR's wheel has them).
  - **Names:** the engines are "ppocrv4" (was "rapidocr") and "ppocrv5". The app's OCR cache keys a reading by
    RapidOCR's version, so each page kept is read once more; the bench's `AP_OCR_CACHE` keeps its names.
  - **Gone:** `ensure_second_ocr` (the launcher installed 1.4.4 on 3.13+ with `--ignore-requires-python`), and the
    bundle builder's downloads past a package's metadata (`IGNORE_REQUIRES_PYTHON`).
  - (`capture/layout.py`, `ops/offline.py`, `ai/reading.py`, `scripts/launch.py`, `scripts/fetch_models.py`,
    `scripts/build_offline_bundle.py`, `scripts/check_deps.py`, `scripts/first_run.py`, `pyproject.toml`,
    `docs/HOW_IT_RUNS.md`, `docs/tested-packages.txt`; `tests/offline/test_offline.py`, `tests/launcher/test_launch.py`,
    `tests/capture/test_reading_once.py`)
- **D-164** · **An answer a page only shows may settle for a few seconds after a change** (phase 2 part 2, measured
  under load).
  - **Why:** the first load run of part 2 (100 people on 100,000 invoices, part 1's settings) did twice part 1's work
    (5,200 pages and actions against 2,600) on the same saturated 4 cores. Its profile (py-spy, two minutes of the
    four workers): a third of their CPU was `summary_memo` working answers out again. Every approval, park or
    check moves the summary's generations and counts, so the queue's checks, the day's figures, month-end and the
    reports were worked out again at nearly every request; D-162 said so ("they help one person, not 100 people").
  - **What:** `summary_memo(..., settle=seconds)`. Once what an answer depends on changed, the answer kept is still
    taken while it was worked out less than `settle` seconds ago, for the same question (`asked`) on the same
    database (`summary_id`: never past a restore). Inside a write under way it is worked out afresh, as before.
    Under a stream of changes it is worked out at most every `settle` seconds in each process, and is at most that
    old.
  - **Where it may be used:** only for what a page shows and nobody acts on unchecked: counts, figures, reports, the
    reading status. Never for what a check, an approval, an export or a posting decides with (the vendor IDs, the
    approved numbers, a vendor's invoices, the checks an approval runs): those follow every change, as before.
  - (`core/store/summary.py`; `tests/core/test_kept_answers.py`)
- **D-165** · **The review queue, the invoice page and every page's reading status made cheap under a stream of
  writes** (phase 2 part 2, B-1730 to B-1736).
  - **Why:** in the first load run's profile (100 people on 100,000 invoices, 4 workers, D-164), `GET /review` took
    27% of the workers' CPU, the invoice page 9% and the check 5.4%. Of `/review`: the queue's checks 8.8% (worked
    out again after nearly every write), today's pills 6.9% (the late regular invoices), the reading status 6% (on
    every page: the page reader's wait 4.3%), every vendor's terms 1.7%.
  - **Each cause, and what now** (each exact, but the one pill):
    - **The queue's findings** (B-1730): each time the queue's checks were worked out again, every finding of 2,654
      invoices was decoded and made read-only one by one (133 ms), though they share 211 texts. `bulk._found` decodes
      each text once, kept for every process (`_FOUND`, 1.5 MB here; past `FOUND_KEPT`, 4,096 texts, all let go):
      shared, read-only, each invoice's list its own. The answer kept is 6.8 MB instead of 19.8.
    - **The queue's order** (B-1731): a check writes the confidence it worked out, which moves its invoice in the
      queue, and the queue's checks were kept for the invoices asked in their order. They are kept for the invoices
      asked in any order (their answer is by invoice), so a check no longer works them out again; an approval, a
      park or a rejection still does (the `queue` topic and the invoices asked move).
    - **The late regular invoices** (B-1732): every vendor's invoice dates (98,720 rows, 0.95 s) after each upload or
      rejection (the summary's `dates`). The queue only shows this pill, so it settles (`summary_memo(...,
      settle=LATE_SETTLE)`, 60 s, `web/pages/review.py`). Sixty seconds and not a few: a vendor is late by days (its
      cadence of a week to a quarter, plus at least five days' grace), so a minute behind changes nothing anyone
      does, and 0.95 s every few seconds in each worker would still be most of the pill's cost. The Vendors page and
      Month-end ask `recurring.detected` itself, exact as before.
    - **The vendors' terms** (B-1733): the queue's 50 rows read the terms of every vendor (19,008: 27 ms) on each
      visit; kept on `changes/vendors` (2.7 MB).
    - **The reading status** (B-1734, B-1736): the page reader's wait sorted all 99,692 finished reads for the latest
      50 (44 ms a page), the templates' count read all 3,008 templates (14.7 MB: 6 to 13 ms), the open queue listed
      every invoice in review. Two indexes (`page_reads_done` by status and time, the partial
      `supplier_profiles_template`) and an `EXISTS` make them 0.02, 0.1 and 0.02 ms. The indexes are in `_SCHEMA`,
      made at every start where missing (about 0.1 s at 100,000 invoices), with no new schema version: an index
      changes no data, and an older AP Coder opens such a database as before.
    - **The directory's exports** (B-1735): their ids and rows were read one row at a time, the worker's lock on
      Python handed back and forth at each row (50,000 ids: 0.2 s alone, 132 s beside three busy requests). Read in
      one step (`fetch_rows`): 0.65 s so (0.24 s alone, 15% more than row by row).
  - **Never settled:** the queue's checks and lists (bulk approval offers its invoices from them, and an invoice just
    approved leaves the queue at once), the check, a vendor's invoices and the vendor IDs: they follow every change.
    Bulk approval checks each invoice again anyway (`bulk_approve`).
  - **Measured** (`p2c_queue/probe.py` in the session's scratchpad: the app on a copy of the 100,000-invoice database,
    the queue as on the server, the bench's stand-in model as page reader with a page time measured; CPU of one
    request, medians of 16; "after a check": a confidence written first, "after a park": an invoice parked or put
    back first):

    | request | alone | after a check | after a park |
    |---|---:|---:|---:|
    | `GET /review` | 159 → 83 ms | 545 → 137 ms | 597 → 236 ms |
    | `GET /review/{id}` | 136 → 73 ms | 184 → 120 ms | 193 → 134 ms |

    The check (46 to 68 ms, before and after alike, within the shared machine's noise) draws no reading status and
    asks none of these.
  - **Not done** (other files' owners):
    - `list_invoices` of the queue's lists (`listed`, 48 ms) after every check's confidence: `/review` and the invoice
      page's Previous and Next. A topic without the confidence, the confidences read apart and sorted, would keep the
      rows (`core/store/summary.py`, `invoices.py`).
    - The queue's facts read again after a park or an approval (`bulk._queue_rows`, 57 ms; the findings texts are 36
      of them, JSON within JSON): the summary could keep each findings text once (`summary.py`). Read with each text
      once by a window over them was slower (65 ms: SQLite sorts the texts).
    - `status_counts` after each change of status (16 ms, every page's rail): a count, it may settle (`summary.py`).
    - The queue's Pay from (`last_pay_froms`): 197 ms at each `/review` once any bank account is in use (none in the
      load bench's seed): an index by vendor and approval time, or kept on a stamp (`accounts.py`, `summary.py`).
    - The search page's first page sorts all 100,000 rows (`directory_ids`, 81 ms): an index for its default sort
      (`summary.py`'s schema).
    - The invoice page and the check: a vendor's invoices (`vendors.py`), its lessons (`learning.py`), the lines'
      similarity search. `_to_fix` (15 ms) and the queue's template (14 ms) each `/review`.
  - (`coding/bulk.py`, `web/pages/review.py`, `core/store/{_base,capture,summary}.py`, `docs/HOW_IT_RUNS.md`;
    `tests/coding/test_bulk.py`, `tests/core/test_kept_answers.py`, `tests/core/test_store_perf.py`,
    `tests/erp/test_directory.py`)
- **D-166** · **Import invoices lists the folder once by name; month-end works out only the POs whose billing changed;
  the reports a page only shows settle** (phase 2 part 3, B-1725; measured as thread CPU on the 100,000-invoice
  database, alone and right after a change made elsewhere, against e889dc4).
  - **Import invoices (GET /process):** D-162's "a seed artefact" was not one. The seed notes its files as an upgrade
    does (`fill_seen_files`: each invoice's own file, its place trusted), and a real installation after an update, a
    restore (the files put back at their own times, `seen_files` as backed up) or with 100,000 files is in that state.
    Each list worked out again (the folder, a pick-up's notes or the invoices changed, or a minute on, in each worker)
    took 3.7 s: `invoice_files`, `emails_in` and `other_files` each made a Path of every file. And `not_read_here`
    listed the folder again at every view (0.6 s).
    - Now one `os.scandir` by name gives the three lists (`process.folder_names`: the same names in the same order, a
      test against the three); each file is looked at by its place as a string; the names not read are kept with
      the list.
    - 1.46 s afresh (the names 0.4, each file's size and time 0.4, the one query 0.6); nothing on a kept view.
    - Its setup steps read every vendor of the vendor master, each column (20,000: 0.23 s), at every view, to count
      them and those with an address number: now two columns, the counts kept until the vendor list or the numbers
      set in Settings → JD Edwards change. The steps 0.25 s → 45 ms.
  - **Month-end, received not invoiced:** every approval moved its stamp, so each visit decoded 69,000 codings (6.5 s).
    - Each received PO's accruals are kept in the process (`accruals._PARTS`) with a digest of what they came from:
      the database's `summary_id`, the PO and its lines, its billing invoices (which ones, their dates and PO, read
      from the summary at each ask) and those invoices' vendors' change counts (`changes/vendor/<key>`, D-159).
    - After a change, only the POs whose billing moved have their invoices decoded. Nothing read in a write under way
      is kept, nor a PO billed by an invoice with no vendor key. Each answer is what working every PO out afresh
      gives: a test after each kind of change, a restore and a write undone.
    - At 100,000 invoices: 0.66 s when no PO's billing changed; 1.5 s after a change to the vendor with the most
      billing invoices (1,156); 6.1 s the first time in a worker.
    - The invoices not in the ERP yet (0.9 s at every visit) are kept per change. What is asked: the period end, the
      G/L date's cutoff, the batches made after it and the tax setup; the POs' change count is part of what the
      received lines are asked for. A change to any of these is another question, worked out at once.
  - **Settled under a stream of changes (D-164):** only what a page shows, never what a check, an approval, an export
    batch or a posting decides with. Each module's `SETTLE_SECONDS`:
    - the recurring vendors (the queue's pill, Vendors, Month-end), 60 s: 0.83 s → 2 ms after a change;
    - Spend's figures and currencies, 30 s: 2.2 s → 3 ms;
    - Insights (`insights.shown`, `shown_operations`), kept per change, 60 s: 1.76 s at every visit → 6 ms;
    - the Month-end page's schedule, 30 s: 8.9 s → 0.1 s after a change (the CSV, booked from, settles nothing:
      10.1 → 2.7 s).
    - **The lengths:** a settled request costs nothing, and a worker works an answer out at most once a `settle`.
      The pill is asked at each view of the queue, the busiest page: 0.8 s every 60 s is 1.3% of a core a worker
      (8% at 10 s), and a regular invoice is late by days. A change of one's own may show on a report up to that
      long after. What is downloaded (the accruals CSV, the business case) is worked out afresh.
  - **Exports:** what an undone batch held (`exported_totals`, every export event decoded: 0.54 s, read in every
    worker after each batch made) is read only when an undone batch is listed: the others show their own invoices'
    totals (`batch_totals`, kept per change, not settled, so a batch just made shows its amounts).
  - **B-1725:** `postings.ensure` takes a column another connection added meanwhile as there. It stays at each
    connection (45 µs): putting the table in the schema would not remove a concurrent add (a laptop's dashboard and
    a command line migrating at once), and needs no schema change.
  - **Not done:**
    - `/search/export.xlsx`, 11.9 s at 50,000 rows: openpyxl writing the cells. A job a person waits on moves that
      CPU, it doesn't cut it; a lighter writer is `erp/exports.streamed_sheet`'s.
    - `/search/export.csv`, 1.8 s at 50,000 rows: the directory's reads and cells, none of it waste in the page.
    - Sales tax, 1.25 s at every visit (`taxreturn.build`, `self_assessment`): within a report's 3 s. A kept answer
      would follow the tax rates file in the data folder, the sites and JD Edwards' settings, which
      `coding/taxreturn.py` reads: it belongs there.
    - Month-end's first visit in a worker (8.5 s) and its 0.66 s kept path: D-162's summary table of each invoice's
      PO-billing lines (`core/store/summary.py`).
  - (`web/pages/process.py`, `erp/accruals.py`, `erp/recurring.py`, `erp/spend.py`, `erp/insights.py`,
    `core/store/{purchasing,postings,vendors}.py`, `web/pages/{month_end,exports,insights}.py`;
    `tests/web/test_web_process.py`, `tests/erp/test_accruals.py`, `tests/erp/test_insights.py`,
    `tests/erp/test_suggest_recurring.py`, `tests/core/test_kept_answers.py`, `tests/core/test_store_races.py`)
- **D-167** · **The review queue's last store costs under a stream of writes** (phase 2 part 2, B-1737 to B-1741:
  D-165's "Not done" of the store).
  - **Why:** after D-165, with 100 people checking, approving and parking, `/review` and the invoice page still read
    again what a write moved even when they only show it, and once a bank account is in use (every real
    installation; the load bench's seed had none) each `/review` ranked a year of approvals for the Pay from.
  - **Each, and what now** (all exact, but the counts shown):
    - **The queue's lists** (B-1737): the `listed` topic no longer covers the confidence (`CONFIDENCE`: what a check
      writes). The rows are kept by id; each ask reads the confidences with the queue's order (`QUEUE_ORDER`, the
      same `ORDER BY`) off a covering index (`summary_queue_order`) and gives each row its own. An invoice listed or
      gone between the two reads (another worker's write) has the list read afresh. Not kept apart on a topic of
      their own: under 100 people a check comes between nearly every two asks, so it would rarely hold, and it saves
      about 8 ms only when nothing changed (a list asked with nothing changed costs 10 ms instead of 1.3).
    - **The counts by status** (B-1738): `status_counts(settled=True)` settles for `COUNTS_SETTLE` (5 s): every
      page's rail (`web/core.py:nav_counts`) and the queue's tabs. Five seconds: a count a person reads, at most one
      approval behind. Exact still: the approval's "your queue is clear" (it then refreshes what the rail takes), the
      first start's page (shown while `has_invoices` finds none; it offers the demo by them) and the demo's load.
    - **The Pay from** (B-1739): each waiting vendor's latest is sought on a partial index (`summary_pay_from`:
      vendor, approval time, id and account, of the approved or pending invoices with an account, `PAID_FROM`), one
      seek a vendor, read in one step. Not kept on a stamp: each approval would move it. The seed sets up a CAD and
      a USD account (`seed.BANK_ACCOUNTS`) before its templates are read, so each clone is paid from its currency's,
      as approved.
    - **The search page's first page** (B-1740): an index in the directory's default order (`summary_received`:
      `directory.order_by` of "received", newest first, its expressions as they are). Another sort, direction or
      filter sorts as before; the test reads the plan, so a change to `order_by` is seen.
    - **The queue's facts** (B-1741): `Store.queue_rows` reads the facts without the findings texts, then each text
      once with the ids that have it (grouped), in one read transaction (`summary_snapshot`). Not kept once in the
      summary (a table of texts): that is a summary column changed, so `SUMMARY_RULES` moves and every database's
      summary is built again at its next start (86 s at 100,000 invoices), for about 13 ms more.
  - **The indexes** are in `SUMMARY_SCHEMA`, made at every start where missing (0.1 to 0.15 s each at 100,000
    invoices), with no new schema version (as D-165): an index changes no data, and an older AP Coder opens the
    database as before. Each write of a summary row keeps them up (the partial one only for an approved invoice
    with an account).
  - **Measured** (`p2c_store/probe.py` in the session's scratchpad, D-165's probe on a copy of the 100,000-invoice
    database with three bank accounts in use; CPU of one request, medians of 16; "after a check": a confidence
    written first, "after a park": an invoice parked or put back first):

    | request | alone | after a check | after a park |
    |---|---:|---:|---:|
    | `GET /review` | 390 → 129 ms | 462 → 121 ms | 560 → 233 ms |
    | `GET /review/{id}` | 90 → 88 ms | 124 → 91 ms | 134 → 130 ms |
    | `POST /review/{id}/check` | 54 → 47 ms | 51 → 46 ms | 50 → 48 ms |

    Each read on a request's one connection (`p2c_store/store_parts.py`): the queue's lists after a check 52 → 10
    ms, Previous and Next's 50 → 9; the counts after a park 7.4 → 0.2 (settled); the Pay from of 1,028 vendors 281
    → 6; the queue's facts 59 → 39; the search page's first page 83 → 5. The check (within noise) asks none of these.
  - **Not done** (other files'):
    - `/review` after a park: the queue's checks worked out again (72 ms, the facts 39 of them), the lists' rows
      read again (48 ms); after a check and a park alike, `attention` (54 ms: with an account in use the Pay from
      choice of each of 2,654 invoices, `banks.opening_choice` and `blocking`, about 25 ms, and `_to_fix`, 15 ms;
      `web/pages/review.py`), the 50 rows and the template.
    - The invoice page after a park: the review list's rows read again for Previous and Next (48 ms). The page and
      the check: `analyse` (`finalise_coding`), `find_duplicates` (each of the vendor's invoice numbers normalized
      in Python, about 1,270 a call, while the summary's `number_key` holds them normalized), `vendor_findings`.
  - (`core/store/{summary,invoices,accounts}.py`, `coding/bulk.py`, `web/core.py`, `web/pages/review.py`,
    `bench/seed.py`, `docs/HOW_IT_RUNS.md`; `tests/core/test_kept_answers.py`, `tests/core/test_store_perf.py`,
    `tests/bench/test_seed_volume.py`)
- **D-168** · **Imports out of the web workers: a persistent import queue, capture processes of their own on the
  company server, the arrival's chat call bounded, each file hashed once; schema 26** (phase 3 α; changes D-99's
  imports queued in memory and D-111's one import job server-wide).
  - **Why:** on the server an import ran as one job server-wide, in a thread of a web worker: files read one after
    another, the uploads queued meanwhile in that worker's memory (lost at a restart), the chat model asked on each
    arrival for up to 600 s, and OCR holding that worker's Python lock for up to 0.9 s, which slowed every page it
    served. A colleague's 1,000 files went before your 3.
  - **The queue** (`core/store/imports.py`; `import_batches`, `import_files`, `import_turns`):
    - **In:** an upload (its SHA-256 from the bytes uploaded), the Process button's pick-up and a scheduled `watch`
      (only the files whose size and time the pick-up hasn't noted are read, `intake.imports.pick_up`), `ap_coder
      process` (a batch of its own, `--force` reads stored files again), a Retry. One transaction a batch; a file an
      invoice was made from (a failed one too for the folder's pick-ups) or a repeat is named among its skipped.
    - **Taken** one at a time in BEGIN IMMEDIATE (`claim_import`): a person's batch before a watch's, then the
      uploader served least recently (round-robin, `import_turns`), then the oldest. Never a file whose content
      another reader is reading; a file stored meanwhile is skipped as it is taken.
    - **Leases:** 120 s (`IMPORT_LEASE_SECONDS`), renewed every 20 s by the reader's heartbeat (the files under way
      only, B-1767), as times of the database's clock (`julianday('now')`) compared there. A file whose reader
      stopped is taken again once its lease ran out; the second time (`IMPORT_TRIES`) it is failed with the reason
      (`STOPPED_TWICE`) as an invoice in Failed / rejected, which Retry reads again. One whose invoice its reader stored
      before it stopped is done with that invoice, whatever its try (B-1769). A stop (the server, Ctrl+C, a laptop's
      dashboard: B-1766) puts the file back, its try not counted.
    - **A restart:** what waits is taken up again: by the capture processes, a laptop's dashboard at its start
      (`process.resume`), or the next `watch`.
    - **A full disk:** nothing is taken while the data folder's disk has less than `backups.BACKUP_RESERVE` (512 MB)
      free; the files stay in the folder, in line, and the Process page says why. With room again they are read: the
      readers look again every 5 s (B-1764).
    - **The Process page** shows the latest run (`process.import_status`, the job's snapshot as before): the batches
      that came in while the queue had work. Each batch is recorded in the audit trail once its last file is done
      (`record_import_run`, D-103). Kept 7 days (`IMPORT_KEEP_DAYS`), the latest run whatever its age. A file that
      could not be read just then (the GL accounts open in Excel) waits in line with the reason, which the card says
      (`release_imports(error=)`, B-1764). The Process button waits 3 s at most for another pick-up (B-1771).
  - **Who reads** (`intake.imports.Reader`):
    - **The company server** (`serve --server`, several web workers): `server.capture_processes()` capture processes
      (`serve --capture-work`, `server.run_capture`) beside the background process, started and stopped with it
      (`server.Together`: asked at once, 15 s in all), started again when one stops. As many as the cores less two,
      and as fit at `CAPTURE_MB` (700 MB; 660-680 measured) in the memory the web workers leave less 2 GB: 4 cores and
      16 GB 2, 32 cores and 64 GB 20; `AP_CAPTURE_PROCESSES` sets it. Each a fresh Python (`subprocess`, so spawn on
      every system), its models loaded once for its life, its ONNX Runtime, OpenCV and BLAS threads its share of the
      cores less two (`AP_OCR_THREADS`, `server.capture_env`: 1 each on 4 cores), its log `logs/capture-<n>.log`. A
      stop gives the file under way 12 s, then puts it back. No web worker reads an invoice.
    - **A laptop** and `--workers 1`: the dashboard's own job "process", in its own process, as before; after a pause
      or an error it looks again every 5 s while files wait, as the capture processes do (D-172, B-1764).
    - **`watch`** reads what waits, unless a capture process runs (its `capture:` lease), then it only puts files in
      line; **`process`** reads its own batch (its `reader:` lease; anyone's once it stopped).
  - **The arrival's chat call** (the choice): **a short timeout with a circuit breaker**, not a queue of its own.
    - **How:** on the company server an arriving invoice waits at most 20 s for the chat model's accounts, the wait
      for a chat slot included (`pipeline.ARRIVAL_SECONDS`, `AP_LLM_ARRIVAL_SECONDS`; `inference.ChatTimeout`,
      `Slots.held(deadline=)`). After two arrivals in a row with no answer in time (`BREAKER_FAILURES`) arrivals
      don't ask for 2 minutes (`BREAKER_SECONDS`), then one asks: an answer in time closes it. One per process. The
      invoice goes in with the local coding, those lines left for AP; it is never left out of the page reader's pass
      (D-111's `skip_clean`), which asks again with the full AP_LLM_TIMEOUT_SECONDS. One no page read asks again for
      (a text file; one in line while the page reader can't read) is asked again by the background process's page
      reader (D-172, B-1765).
    - **Why not a queue:** an answer in time codes exactly as before (the same question, the same kept answers,
      D-131), while a queued call would bring every invoice in without the model's accounts and need a writer that
      changes a proposal someone may be reviewing already. The page reader's pass is that second chance already
      (where it never comes, D-172 gives one, with the page reader's writer).
    - **A laptop:** no limit (0), as before: its LM Studio is the pilot tester's own, and laptop mode stays as it is.
  - **Hash once:** the SHA-256 read as the file comes in goes with it (`InvoicePipeline.process(sha256=)`,
    `add_invoice(sha256=)`); a file whose size or time changed since is hashed again; the page reader's pass's
    header reading takes the invoice's stored hash. The folder pick-up and `watch` no longer hash every file of the
    folder at each press or check.
  - **One rule** for a folder's files (`intake.files.folder_names`): `pipeline.invoice_files`, `pipeline.other_files`,
    `mailbox.emails_in` and the import page's list are its parts (D-166 repeated them; B-1758's hidden emails kept).
  - **Measured** (4 cores shared with two other agents' tests; `python -m ap_coder.bench day --n 100 --scanned 0.5`,
    the fake model 8-20 s a page, 2 slots, time ×0.05; before: fd5d166 with B-1758):

    | | before | after |
    |---|---:|---:|
    | all 100 in the review queue after | 8.9 min | 5.9 min |
    | arrival, invoices an hour | 673 | 1,022 |
    | page reader drained after | 9.1 min | 6.2 min |
    | web workers' CPU (the busiest) | 1,067 s, 1.93 cores (1,057 s) | 9 s, 0.02 cores (3.5 s) |
    | capture processes' CPU | | 600 s, 1.58 cores (2 processes) |
    | `GET /review` p95 idle / during the drop | 18 / 204 ms | 20 / 130 ms |
    | failures | 0 | 0 |

    At 1,250 invoices (after; the base wasn't run, about 2 h here at 673 an hour): all in the review queue after
    78.2 min (959 an hour), the page reader drained after 78.3 min, the AI reviewer all 1,250, 0 failures; the web
    workers 158 s of CPU in all (0.03 cores), the capture processes 1.57 cores, 495 MB each; `GET /review` p95 15 ms
    idle, 219 ms during the drop (a queue grown to 1,250 invoices, the 4 cores full with the capture processes, the
    background process and two other agents' tests). At the model machine's full latency (an estimate): drained
    after 8.4 h, the model machine's 1,250 a day with 2 slots being the limit now.

    GIL probe (`p3_alpha/gil_probe.py` in the session's scratchpad: a 5 ms timer thread in a web worker while 20
    scans are imported; how late it woke): p50 0.08 → 0.08 ms, p95 3.0 → 0.24 ms, p99 11.0 → 3.8 ms, the longest
    818 → 100 ms, wake-ups over 50 ms 67 → 7 (of about 40,000; the 4 cores were shared, so a late wake-up after is
    the machine's, not the import's); the import 262 → 209 s. `scripts/same_behaviour.py --ref
    claude/epic-feynman-r6ns86 pipeline read approve` (Python 3.12, both OCR models): the same.
  - **No new dependency.**
  - **Not done:**
    - `server.json` has no `capture_processes` key (`ops/service.py`): `AP_CAPTURE_PROCESSES` in the .env sets it.
    - The day bench's estimate at full latency still adds the arrival's chat answers one after another, as when one
      import read serially: with N capture processes and the 20 s limit it overstates the arrival.
    - A reader that loses its lease while still reading (no heartbeat for 2 minutes: the database held by another
      writer that long) could have its file read again by another; the claim then finds it stored only if the first
      saved it already.
  - (`core/store/{imports,_base,folder,invoices}.py`, `intake/{files,imports,mailbox}.py`, `ai/pipeline.py`,
    `ai/inference.py`, `ai/local_llm.py`, `web/pages/{process,review}.py`, `web/server.py`, `web/app.py`,
    `ops/cli.py`, `ops/offline.py`, `bench/day.py`, `web/templates/process.html`, `web/static/js/process.js`;
    `tests/core/test_imports.py`, `tests/intake/test_imports.py`, `tests/ai/test_pipeline_arrival.py`,
    `tests/web/test_server_capture.py`, `tests/web/test_web_process.py`, `tests/web/test_server_ux.py`,
    `tests/web/test_server_capacity.py`, `tests/ops/test_controls_watch.py`, `tests/coding/test_terms_po_vendors.py`,
    `tests/ai/test_pipeline.py`)
- **D-169** · **The long downloads made by the server's background process; the big answers worked out once for
  every process; search to Excel written without openpyxl's cells; schema 27** (scale goal phase 3, item δ).
  - **Why:** at 25 people the web workers were half busy, yet a page's p95 was 4.45 s
    (`docs/archive/scale-2026-11/phase2b-load-100-people.md`): Python runs one thread of a process at a time, and a
    long request (search to Excel 9-12 s, the Activity CSV 7-16 s, the duplicates CSV's first 11-15 s) held its
    worker's while the light pages beside it waited. And the answers kept in a worker's memory (`summary_memo`) were
    worked out again in each worker: month-end's first visit 5-8.6 s, the duplicate audit 7-15 s.
  - **The downloads** (`web/pages/downloads.py`; the jobs in `report_jobs`, `core/store/reports.py`): on the company
    server search to Excel and to CSV, the Activity CSV, the duplicate audit's CSV and the accruals CSV (`KINDS`).
    - The page's link (`a[data-download-job]`, `app.js`) asks its own address with the page's header: the web worker
      reads what the file is of (the filters; the person's default layout; the events they may see) and answers with
      the job, at once. The link says how far it got ("Making the file: 12,000 of 50,000 invoices written", or how
      many files wait before it), then the file downloads from its own address with `job=<id>`, so the access log and
      Activity's "Downloaded" name it as before.
    - Asked without that header (a bookmark, a script, the load bench), the worker waits for the job holding no thread
      (an async handler that looks at the job every 0.05 s, then up to every 0.25 s), then sends the file as before.
    - The background process makes them (`Runner`, started by `app.start_background_work`: in `serve
      --background-work`, or in the one process of `--workers 1`), 2 at a time, oldest first, each written beside the
      others under a name of its own and renamed once whole. A server of one process whose background work was not
      started (a first test, `bench volume`) makes them on a thread of its own, which ends once none waits; after a
      restart, the page following a job the process before left, or its file asked for, starts it again (B-1777).
    - Each job is its owner's: another person's is "No such download". A person may download what the route's own
      role rule lets them (Read-only too: the file is theirs, nothing else changes); `GET /downloads/<id>` (how far
      it got) is READ and the owner's. The same file asked again while it waits is the same job, from two web
      workers at once too (one write transaction, and a unique index of the jobs waiting or being made). At most 3
      waiting or being made a person (a 429 says so). A file is kept an hour, the 5 latest of each person (`sweep`).
      The sweep also removes a half-written file 60 s after its maker last noted how far it got (each note keeps the
      file's time new, B-1773), and any file no job names once it is an hour old (its process killed before the job
      was recorded made; one Windows refused to remove while it was sent: B-1775). A file whose job was not recorded
      made is removed by its maker at once (B-1774, B-1776).
    - **A restart:** a background process that stops (an update, the service restarted) puts the job under way back
      in line, and the next one makes it; one killed is seen stopped after 60 s without a note of its progress and its
      jobs are taken again, twice in all, then fail with why; its late end is refused (the file is not the job's). A
      job an older AP Coder left fails ("AP Coder was updated…": what its filters mean may have changed).
    - **A full disk:** under `backups.BACKUP_RESERVE` free before it starts (D-160's rule), or full while it writes
      (ENOSPC, SQLite's "disk is full"), the job ends with why; nothing half written is left.
    - **A restore** empties `.cache/downloads` (D-156's amendment, `made/` with it), and the jobs of the database
      before are no one's (another `summary_id`): none is made, none is shown, the next sweep forgets them.
    - The routes left the long routes' lane (`core.HEAVY_ROUTES`): their worker only waits. On a laptop each is made
      as it is asked for, as before (the Activity CSV sent as it is read, B-1597).
    - **Not made so:** the Sales tax CSVs (0.4-0.8 s, within a report's 3 s); the Spend workbook stays a `web/jobs.py`
      job in the worker that started it (D-156; `web/pages/spend.py` is another's).
  - **Worked out once for every process** (`Store.shared_answer`, the table `report_answers`): an answer with the
    stamp it was worked out under, read before the work as `summary_memo` reads its own (D-159): the database's
    `summary_id`, the summary's generation, the counts of changes the triggers keep, and what it is for (`asked`, by a
    digest of its `repr`). A process with nothing kept takes it while the stamp read now is the same.
    - **JSON only**, never pickle (what is read back is data, never code); each answer gives its own format (`version`,
      with AP Coder's version): one an older AP Coder wrote, another format, or one that is not JSON is worked out
      again. At most 16 MB (`ANSWER_MOST_BYTES`): a bigger one is kept in its process only. The 8 latest of each name.
    - One process works it out: a lease in `ephemeral` (`answer/<name>/<asked>`, 5 minutes at most); one asking the
      same meanwhile waits up to 30 s for it, then works it out itself. One thread of a process at a time (a lock).
    - Each process keeps its own copy (32 at most): asked again, nothing is read from the database but the stamp.
    - `settle` (D-164) holds across processes by when the work began (`reports.wall`): once what it depends on
      changed, an answer begun less than `settle` seconds ago on the same database is taken, in whichever process it
      was made, so it is never older than that (two settles stacked would have made it twice as old).
    - In a write under way (or a read transaction) it is worked out afresh, neither read nor kept, as `summary_memo`.
      Its write waits at most a moment for another writer (`briefly`); a busy or full database just doesn't keep it.
    - **Used for:** the duplicate audit's pairs (its page and CSV; `changes/register` and the generation, as D-162);
      month-end's received, not invoiced lines and the invoices not in the ERP yet (what they were asked by, as
      D-166; the page's 30 s settle now across processes); and each received PO's accruals by the digest of what they
      come from (D-166's `_PARTS`, kept for all as `received_parts`): a process with none kept decodes none of the
      billing invoices another one decoded, and after a change only those of the POs whose billing changed.
    - **Month-end:** this, not D-162's summary table of each invoice's PO-billing lines: that is a summary column, so
      `SUMMARY_RULES` moves and every database's summary is built again at its next start (86 s at 100,000 invoices,
      D-167), and the 0.5 s kept path (the POs' lines, the billing invoices' rows) stays either way.
    - **Not shared:** the recurring vendors (0.7 s a process at most every 60 s): the queue's late pill settles it
      again on top (`web/pages/review.py`), so a shared settled answer could be shown twice as old as D-165 says; the
      vendor list (`web/pages/invoice.py`, 0.8 s a worker a change); Sales tax (D-166: `coding/taxreturn.py`'s).
  - **Search to Excel** is written by `exports.sheet_workbook`: the workbook's parts written as text a row at a
    time, the cells as openpyxl writes them (numbers "%.16g", dates as Excel's serial days, text inline with its
    spaces kept, `#N/A` and the like as errors, a formula-looking text quoted), the same formats, header, widths,
    frozen header and filter. What it holds is what the whole sheet held in memory (`exports._sheet`) holds, cell by
    cell, read back and compared (`tests/erp/test_directory.py`, with numbers past 2^53, spaces only, 40,000
    characters, dates before 1900-03-01). `streamed_sheet` (D-157) is gone. On a laptop too.
  - **Schema 27:** `report_answers` and `report_jobs`, made by `_SCHEMA` at every start. Another change takes 26; its
    migration comes before this one at the integration. Going back: an older version refuses a database of schema 27
    (`DatabaseTooNew`), and `server-update --back` restores the pre-update backup.
  - **Measured** (`p3_delta/` in the session's scratchpad: a copy of the 100,000-invoice database at schema 27,
    against fd5d166 on the seed itself; the machine shared with two other agents' work, its load 3 to 9, so each
    figure beside the one it is compared with):
    - **A web worker's part** of each download on the server, asked by the page: 8-80 ms (7-46 ms of CPU). Before, it
      made the whole file: search to Excel 8.6 s, to CSV 1.1 s, the Activity CSV 8.6 s, the duplicates CSV 6.8 s
      the first time in each worker (`bench volume`, admin).
    - **The background process's:** search to Excel 8.6-9.2 → 2.2-2.3 s (8.2-8.5 → 2.2 s of CPU: the writer), to CSV
      1.1-1.3 s, the Activity CSV 5.4-5.7 s, the duplicates CSV 0.02 s (the pairs shared), the accruals CSV 0.8 s the
      first time, 0.04 s after.
    - **The first time in a process** (a fresh Python each, the month-end page's answers and the audit's pairs):
      before, 5.0-6.2 s and 6.7-7.2 s in every process; now 5.0 s and 6.4 s in the server's first, then 0.8 s (the
      recurring vendors, not shared) and 0.01 s in each other.
    - **`bench volume --roles admin --only` the changed routes** (one process; asked without the page's header, so
      the time to the file, made beside the request on a thread of its own): search to Excel 8.57 → 2.26-2.58 s,
      to CSV 1.12 → 1.59-1.79 s (the file written, then sent; a look at the job every 0.25 s at most), the
      Activity CSV 8.56 → 7.54-7.75 s, the accruals CSV 0.05 → 0.16 s, the duplicates CSV's and month-end's first
      6.8 and 5.4 s → 7.0-7.4 and 5.4-5.7 s (the server's first: the same work), Exports and month-end after it the
      same.
  - **Not done:**
    - The load bench asks for the downloads without the page's header (`bench/load.py` `export_search`): its worker
      then waits holding no thread while the background process makes the file.
    - The Activity CSV (5.4 s in the background, each event decoded for its words), the Spend workbook made in the
      background process, the recurring vendors and the vendor list shared.
    - The background process warming the shared answers after a change (the first ask after it works them out).
  - (`core/store/{reports,_base,__init__}.py`, `erp/{accruals,dupaudit,directory,exports}.py`,
    `web/pages/{downloads,search,activity,exports,month_end,__init__}.py`, `web/{app,core,files,roles}.py`,
    `web/static/js/app.js`, the four pages' templates, `docs/HOW_IT_RUNS.md`, `docs/SERVER.md`;
    `tests/core/test_reports.py`, `tests/core/test_kept_answers.py`, `tests/web/test_downloads.py`,
    `tests/erp/test_directory.py`, `tests/erp/test_accruals.py`)
- **D-170** · **A scanned page is drawn and OCR'd once, whatever reads it and in whichever process; OCR reads the
  page's pixels, with no PNG between; PyMuPDF one thread at a time; one HTTP client a model server; the totals
  solver pruned. What is read is the same, byte for byte** (phase 3, item β; B-1760).
  - **Why** (measured again on fd5d166, Python 3.12, both OCR engines and the table finder): a scan was read up to
    five times in its life (arrival, the page reader's blank pages and its pass, the approval's template learning,
    teach mode's words) and each time every page was drawn and made a PNG again (0.4 to 1 s a page), because the
    readings kept in `.cache/ocr` were named by the hash of that PNG: the page reader's pass on a 2-page scan took
    3.2 s with every reading kept, teach mode 1.9 s. The table finder's tables were kept only in the process that
    found them. PyMuPDF was used from several threads with no lock (N-14). Each page read made a new HTTP client
    (B-1760). The totals solver tried up to 480,000 combinations (1.3 s).
  - **The readings kept by what was asked** (`capture/layout.py`): a page for OCR is a `PageToRead`, named by the
    file's SHA-256 (worked out once per version of the file, before it is opened), the page and how it is drawn
    (`OCR_DPI`, `OCR_MAX_SIDE`, straightened and enlarged for a second read by the same engine), and drawn only when
    its reading isn't kept. A reading is kept under the hash of that, the engine with its settings and its model
    files' SHA-256, the libraries' versions (RapidOCR, ONNX Runtime, PyMuPDF, Pillow, pillow-heif, NumPy, OpenCV) and
    `PAGE_DRAWING` (`kept_name`). Kept with it: the picture's size, the resolution it states and, when the page's turn
    needed them, its letters' count (``_letters_upright``), so a reading read back never draws the page. The turn,
    the tilt and the words are worked out from the reading each time, as before: a change to the reader needs no
    reading again. `tests/capture/test_reading_cache.py` pins the code that draws a page, runs OCR and says what is
    kept (`KEPT_CODE`): a change to it fails until `PAGE_DRAWING` is one more.
    - The table finder's tables are kept there too (by the file, the pages, the model's SHA-256 and its settings).
    - The folder is `.cache/ocr`, shared by every process of the data folder: a reading is written under a name of
      its process and thread, then renamed; at most 512 MB (was 4,000 files, about a day and a half of a busy
      server's scans: two weeks now), the oldest written first, down to three quarters; a half-written one left by a
      stopped process goes after an hour. The readings named the old way are never read again and go as the folder
      is pruned: each page kept is read once more.
    - `AP_OCR_CACHE` (a developer's cache for bench iterations) is named the same way now; D-163's names go, so a
      cache made before is read once more.
    - Not kept on disk: the pictures themselves (the readings are what is read back). In memory, the last pages
      drawn (at most 32 MB, `PICTURES_KEPT_BYTES`) for the second engine, as the last 4 PNGs were.
  - **Pixels, no PNG** (`_page_picture`, `_scan_picture`, `_image_pages`, `_prepared`, `table_finder.page_pictures`):
    PyMuPDF's pixmap into a Pillow picture, a picture's frame as it is, the second read's straightened page as it is
    made. OCR is given the same pixels, in the same mode, as the PNG gave it, and the resolution the PNG stated
    (PyMuPDF writes the pixmap's own, 96 dpi: read off a one-pixel PNG once, `_png_dpi`; a page small enough states
    it, as before). Proven per kind of page in the tests: a scanned PDF page (letter, a 4 x 6 in. slip, tabloid), a
    PDF made from a picture (PNG, JPEG, grey, palette), a photo and a 3-page TIFF, PNG files with transparency,
    palette and grey, the straightened second read of each, and the table finder's pictures.
  - **PyMuPDF one thread at a time** (`layout.PDF_LOCK`, `opened_pdf`): every call on a document in `capture/` and
    `web/document.py` (the page pictures, teach mode's words, the page sizes) holds one lock; OCR runs outside it.
    PyMuPDF holds Python's own lock in its calls (another thread got 2% of the time during a page's PNG), so nothing
    waits longer than before. Fifty PDFs (scans and digital, one to three pages) read, rendered for the table finder
    and the viewer and listed for teach mode, eight threads at once, each the same as alone. N-14 did not reproduce
    before: the lock makes it so whatever PyMuPDF's next version does.
  - **One HTTP client a model server** (`local_llm.build_local_client`, B-1760): shared by every request of a
    process to that server and way of reaching it (refused, on the network and how, the internet), a forked process
    making its own: 1,000 page reads and codings against a server keeping its connections open, 2,000 more open
    files before, none now.
  - **The totals solver** (`reader/totals.py`): with a total tax or a deposit only the combination with no tax is
    tried (the others were all skipped one by one); otherwise only the taxes that make the totals add up (a range of
    the combinations sorted by their sum: the sum grows with it) and those whose most possible score, worked out
    with each tax's rate, beats the best so far. One that doesn't add up never scores 3.0 more, so the subtotals that
    balance are all still found. The same answers: 5,000 random readings of every kind (added up or not, a deposit,
    a total tax, GST and HST from one amount, no subtotal) the same readings, scores and methods as the solver kept
    in the test (`_solve_totals_as_before`), 300 in the test itself. No cap on the combinations: a cap could change
    an answer. Worst case (every amount field full) 1.33 s → 12 ms.
  - **The proof that reading is the same:** `scripts/same_behaviour.py --ref claude/epic-feynman-r6ns86 read
    pipeline` (Python 3.12, both engines, the table finder on): read 60 outputs and pipeline the same bytes. `python -m
    ap_coder.bench run --quick --scanned 1` and `lines --quick --scanned 1` before and after: the same evidence,
    failures, reports and cases (field accuracy 100.0%, ECE 0.0226; descriptions right 43.1%), only the timings
    differ.
  - **Measured** (`p3_beta/stages.py` in the session's scratchpad: a 2-page bench scan, each step in a new process,
    the arrival with the models loaded, before and after one after the other 4 to 6 times, medians; other agents'
    work loaded the 4 cores 2 to 8 times over, so the arrival's times spread from 12 to 43 s):

    | a 2-page scan | both OCR engines | one engine (the second read straightened, enlarged) |
    |---|---:|---:|
    | arrival | 28.6 → 28.5 s | 35.6 → 28.9 s |
    | the page reader's pass (its blank pages, then the whole pipeline again) | 3.16 → 0.69 s | 6.77 → 0.67 s |
    | of it, the reading (`analyze`) | 2.55 → 0.27 s | 6.04 → 0.22 s |
    | teach mode's words | 1.80 → 0.41 s | |
    | the approval's template learning (`build_layout`) | 1.88 → 0.43 s | |

    The arrival with both engines is the OCR: drawing the pages for it took 1.6 s of 11 s on a quiet moment (PyMuPDF's
    PNG 1.43 s, read back 0.18 s), now 0.26 s, within the load's spread here. With one engine, the straightened page
    is no longer made a PNG and read back (that column with the cores loaded 5 to 7 times over). Teach mode's 0.41 s
    is importing RapidOCR in a new process: in a running one, the same invoice's words again take 0.01 s (0.16 s
    before, its last 4 pages' PNGs in memory). What is read is the same in every row (each step's digest, before and
    after).

  - **Not done, and why:**
    - The OCR itself is the arrival's cost and stays: a reading the same needs the same engines on the same pixels.
    - `ai/page_reader.py` keeps its own `_RENDER_LOCK`, and `ai/imaging.py`, `ai/pipeline.py`, `intake/`,
      `erp/stamp.py` call PyMuPDF without `PDF_LOCK`: not this item's files (one line each to share it).
    - The table finder's model is loaded in each process (0.6 s); its tables are no longer looked for again.
    - Found, then changed (B-1761, B-1778): a page drawn for OCR states the 96 dpi PyMuPDF gives its pixmaps, so a
      small page with neither text nor a picture (text drawn as outlines, a 4 x 6 in. slip) was said to be scanned
      at about 96 dpi, coarse, and each field went to a person. A PDF page's resolution is now its scan's: the
      picture covering at least a quarter of it, from 100 px on its short side (a 4 x 6 in. slip at 72 dpi, a
      thumbnail made a PDF: coarse, as before); a page without one has none.
  - (`capture/layout.py`, `capture/table_finder.py`, `capture/reader/totals.py`, `web/document.py`,
    `ai/local_llm.py`, `docs/HOW_IT_RUNS.md`, `docs/CAPTURE_DESIGN.md`; `tests/capture/test_reading_cache.py`,
    `tests/capture/test_totals_solver.py`, `tests/capture/test_reading_once.py`, `tests/ai/test_local_llm.py`)
- **D-171** · **The page reader's and the AI reviewer's queues renewed while read, taken in order, the reviewer not
  starved on the server; an import stored only by the reader holding it; the model's kept answers pruned; the
  learning memory updated, not built again, after an approval; a vendor's extended index kept within bytes** (phase
  3, item γ; B-1762, B-1763).
  - **The queues** (`core/store/page_queue.py`: the page reader's queue moved out of `capture.py` as it was, then
    changed; the reviewer's in `lessons.py`):
    - **A heartbeat:** a read (`page_reads`, "reading") or a review ("verifying") under way is renewed every
      `READING_RENEW_SECONDS` (60 s) by a thread of its reader (`page_worker.renewed`, `renew_page_read`,
      `renew_verification`, only while it is still that reader's) and is in line again once not renewed for
      `READING_STALE_SECONDS` (5 minutes; it was `STALE_READING_HOURS`, 2 hours, the row never renewed), on the
      database's clock (`renewed`, D-172, B-1770). A reader whose process is gone is still put back at once
      (`release_interrupted`). One whose read or review another took meanwhile ends nothing of it and folds nothing
      in (B-1770). The Azure check keeps its 2 hours (not renewed: a check taken again would send its pages to Azure
      twice).
    - **The order:** a person's first (the page reader: `requested_by`, the invoice's "Add it to OvisOCR2's queue" or
      `read-pages --invoice`; the reviewer: "asked by <name>"), then an invoice touchless processing waits on (a
      touchless supplier's new invoice, reason `TOUCHLESS_NEXT`, from the arrival's `WAITING_FOR_PAGE_READER`; the
      reviewer's "held for touchless"), then the earliest due date (none last: a credit note), then the oldest. One
      SQL order (`_ORDER`, `_REVIEW_ORDER`, on the invoice's `due_date`); where an invoice is in line
      (`page_reads_ahead`) counts by the same order (row values). Before, the oldest first: a person's request
      went to the back of the line.
    - **The AI reviewer's turn** (the choice: interleaved, not a thread of its own): on the company server each
      reader thread gives the reviewer the next turn after each invoice it read, when it has one ready
      (`BackgroundReader._round`), and goes on reading at once after it, even when the chat model didn't answer (the
      minute's pause is for a reader with nothing left to read); a reader thread still asks the model server one
      thing at a time, so `llm_parallel` stays what the model machine is asked at once. A thread of its own would
      ask N + 1 at once, beside the arrival's chat calls. On a laptop it reviews once nothing is left to read, as
      before: one LM Studio there may hold one model at a time, and switching OvisOCR2 and the chat model after each
      invoice would load one each time.
    - **The wait** (`wait_seconds`): the rounds of the `llm_parallel` readers, the queue's (its count over the
      readers, rounded up) or an invoice's (those ahead over the readers, then its own); one reader as before
      (B-1763).
    - **Measured** (`python -m ap_coder.bench day --n 100 --scanned 0.5`, the fake model 8-20 s a page, 2 slots,
      time ×0.05; before: aad1b61 with this bench's new figures; the 4 cores shared with other agents' work):

      | | before | after |
      |---|---:|---:|
      | reviews done while the page reader had invoices in line | 23 of 100 | 94 of 100 |
      | an invoice's wait for its review once read, median / max | 21.5 / 44 s | 1.0 / 17 s |
      | the first review after the drop | 49 s | 24 s |
      | the longest stretch reads waited and no review ended | 77 s | 30 s |
      | all in the review queue / the page reader drained after | 5.3 / 5.4 min | 5.2 / 5.4 min |
      | failures | 0 | 0 |

  - **An import stored once** (B-1762, D-168's "not done"): the import queue's reader sets `IMPORT_CLAIM` (the file
    and its reader) around the pipeline, and `add_invoice` notes the invoice on that file in its own transaction
    (`ImportsMixin._claim_noted`), only while the file is still that reader's; else `ClaimLost`, nothing of it is
    stored (its event neither) and the pipeline says so. A capture process stalled past its 120 s lease (a long OCR
    holding Python's lock, a paused VM) whose file another took stores nothing, whichever stores first.
  - **The model's answers kept, pruned** (`paths.prune_kept`): `.cache/page_reader` (a folder a file),
    `.cache/model_reading` and `.cache/model_accounts` were never pruned (a few hundred thousand files a year at the
    server's volume, all invoice data). Each answer kept prunes its folder, at a process's first and then at most
    hourly: an answer written more than `KEPT_DAYS` (30) ago goes, then the oldest past `KEPT_MAX_BYTES` (512 MB, about
    3 months of a busy server's), a half-written one an hour old, a folder left empty; the page reader's timings stay.
    By age, not as an invoice is decided: an answer is asked for again within days (the arrival, the page reader's
    pass, a reading again), a decided invoice's only if it is read again, which then costs one model call; age needs
    no database.
  - **The learning memory updated, not built again** (`suggest._updated_memory`, `memory._updated_aggregate`,
    `Index.updated`, `RowsCache.get(update=)`): after an approval the store's memory is the new lessons in front of
    the rows kept, the oldest past the cap dropped (D-154's `_with_added`); every process then built the GL
    suggestions' index and the examples' patterns again from all of them. Now the memory kept is updated: only the
    lines of the lessons added and dropped are worked out again; the patterns in a build's order, ranks in the same
    order (a rank is a row's place less the memory's `offset`: those kept stay as they were), the index renumbered,
    its words in the order a build meets them, so `similar` finds the same lines in the same order and the ties
    break alike. Exactly what a build gives: `tests/ai/test_coding_no_ai.py` compares everything both hold over 80
    approvals of random lessons past a cap, and each of four sabotages of the update fails it. More than
    `UPDATE_MOST` (1,000) lessons at once, or any other change (lessons forgotten, the memory read again, a history
    import's rows in the middle), builds it as before. A slot's fingerprint is worked out once compared (it was at
    each build: ~20 ms at 50,000 lessons).
    - **Measured** (`p3_gamma/memory_after_approval.py`: 50,000 of the 100k seed's lessons, five approvals, the
      machine quiet): the index after an approval 0.15-0.22 s → 6-8 ms, the patterns 0.17-0.18 s → 3 ms, in every
      web worker, capture process and the background process; held 4.7 → 10.9 MB and 1.9 → 4.9 MB (each line's
      decisions, kept for the updates). `bench coding --quick` 2.1 → 1.8 s.
  - **A web worker's memory** (`p3_gamma/worker_memory.py` on a copy of the 100k seed: each per-process cache worked
    out in turn, under tracemalloc, what it holds after a gc; then the queue, 20 invoices twice and the reports in
    one server-mode worker; before aad1b61, after this):

    | what a web worker keeps | held (traced) | bounded by |
    |---|---:|---|
    | the learning memory, 50,000 lessons (`_FEEDBACK`) | 64.1 MB | `FEEDBACK_READ` |
    | the GL suggestions' index of it (`suggest._MEMORY`, 2 slots) | 4.7 → 10.9 MB | 2 slots |
    | the examples' patterns (`memory._AGGREGATED`, 2 slots) | 1.9 → 4.9 MB | 2 slots |
    | 32 vendors' lessons worked out (`_VENDOR_LESSONS`) | 0.2 MB | `VENDOR_LESSONS_KEPT` |
    | 26 vendors' older lessons (`_PAST_MEMORY`) and extended indexes (`suggest._PAST`) | 80.1 MB | 60,000 rows; bytes |
    | an approval: the memory, its index and patterns again | +6.9 MB, 1.40 s → +3.6 MB, 0.08 s | |
    | the app's modules (server mode) | 49 MB | |
    | the review queue: the vendors' invoice dates (24 MB) and the recurring vendors' (19 MB) | 66 MB | one each |
    | 20 invoices: the worker's vendors' older lessons (45 MB), their extended indexes (27 MB) | 81 MB | as above |
    | the reports (insights, month-end, vendors, purchase orders) | 20 MB | |
    | in all at the end, traced / RSS | 380 → 386 MB / 1,491 → 1,593 MB | |

    - **What holds it:** Python's objects are about a quarter of the RSS (the RSS of three runs of this probe ended
      1,392 to 1,593 MB: the allocator's, not a difference between the code before and after). The kept rows
      dominate them (the learning memory and the vendors' older lessons: 1.3 KB a lesson, 64 + 77 MB); the rest of
      the RSS is what the requests' peaks freed and the allocator keeps (D-153: 4 threads a worker, about 110 MB
      each a request in flight; one invoice's first opening after the memory changed read a vendor's older lessons
      again: 43,000 rows, a 50 MB JSON text in SQLite and its rows decoded).
    - **SQLite:** a connection a request (its session), closed after it, with SQLite's default page cache (2 MB,
      `cache_size` -2000); at most a worker's 4 threads at once: about 8 MB. Not what holds it.
    - **Bounded now:** a vendor's extended index (`_own_bytes`: the lists and dicts `_extended` copied, of the
      shared memory's size: 0.35 MB a vendor on the seed, whose 9,850 lines have only 167 words; with a real
      vocabulary, its words' dicts too, several MB) within `PAST_BYTES_KEPT` (32 MB) besides its 32 vendors and
      60,000 rows, the latest always; none for a vendor whose older lessons add nothing (the shared index itself,
      B-1781). On the seed it never bites (26 vendors kept: 9 MB).
    - **Load** (`bench load --users 25 --duration 300 --ramp 30 --think 10 --workers 4` on the 100k seed, run once,
      after all of phase 3: α, β, δ and this): pages p50 148 ms, p95 1.37 s; actions p95 254 ms; 0 errors, 0 over
      30 s; the web workers 125 → 684-782 MB in 5 minutes, 0.09-0.24 cores each; the background process 213 → 454 MB.
      Phase 2 part 2's same run (`knee_25`, before phase 3): pages p50 300 ms, p95 4.45 s, actions p95 1.37 s, the
      workers 125 → 787-916 MB at 0.4-0.5 cores. The 100-person run's 1.25-1.4 GB in 10 minutes was 4 workers
      saturated (D-153's requests in flight), not a cache that grows.
  - **Follow-ups of phase 3's other parts:**
    - PyMuPDF holds `layout.PDF_LOCK` outside `capture/` too: the page reader's pages (its `_RENDER_LOCK` gone), the
      AI reviewer's page images (`imaging`), the pipeline's look at a PDF, a picture made a PDF and a scan turned, an
      email's PDF text, the approved invoice's stamp (a picture is decoded outside the lock).
      `tests/capture/test_reading_cache.py` holds every `pymupdf.open` there to it.
    - `server.json` has `capture_processes` (1-64; 0 or none: `server.capture_processes()`'s own), passed to the
      dashboard as `AP_CAPTURE_PROCESSES` (the .env's still wins, as for the other keys).
    - The day bench's arrival at full latency waits for each chat answer side by side across the capture processes,
      each at most `pipeline.ARRIVAL_SECONDS` (it added them one after another, uncut): 100 invoices on aad1b61, 29.0
      min the old way, 16.4 min this way (two runs). It also says whether the reviewer kept up (the rows above).
  - **The proof that nothing else changed:** `scripts/same_behaviour.py --ref claude/epic-feynman-r6ns86 tax approve
    read pipeline` (Python 3.12, both OCR engines): the same, all four (20,000 tax, 1 approve, 60 read and 1
    pipeline outputs). `bench coding --quick` and `bench learn --quick`: the same bytes but the timings.
  - **Not done:**
    - After each approval the memory's last lesson moves (past the cap): every vendor past it reads its older lessons
      again (`_past_memory`'s stamp holds the memory's last row), up to 43,000 rows on the seed in each process; they
      could be updated as the memory is.
    - The vendors' invoice dates and the recurring vendors' memos (43 MB a worker at 100,000 invoices) grow with the
      database: shared across processes (`shared_answer`) or in SQL, they would be one copy.
    - A reader thread with nothing in line looks again after 30 s (`IDLE_SECONDS`): during a drop that trickles in,
      the page reader may wait that long for a new invoice.
    - The reviewer's turn is per reader thread: with `llm_parallel` 1 on the server the page reader reads every
      other turn under a backlog of reviews.
  - (`core/store/{page_queue,capture,lessons,imports,invoices,_base,__init__}.py`, `core/{memory,paths}.py`,
    `ai/{page_worker,verifier,pipeline,suggest,line_match,inference,page_reader,imaging}.py`, `intake/{imports,
    pictures,triage}.py`, `erp/stamp.py`, `ops/service.py`, `bench/day.py`, `docs/HOW_IT_RUNS.md`, `docs/SERVER.md`;
    `tests/capture/test_page_reads.py`, `tests/ai/test_page_worker.py`, `tests/ai/test_verifier.py`,
    `tests/ai/test_uniform_reading.py`, `tests/ai/test_coding_no_ai.py`, `tests/intake/test_imports.py`,
    `tests/core/test_paths.py`, `tests/capture/test_reading_cache.py`, `tests/web/test_server_capacity.py`,
    `tests/bench/test_day.py`)
- **D-172** · **The review of phase 3's imports and queues: a second chance for an arrival no page read asks again
  for; the page reader's and the reviewer's queues stale on the database's clock and fenced; a laptop's import that
  looks again after a pause or an error** (B-1764 to B-1772; changes D-168 and D-171 where they say so).
  - **Why:** a fresh review of phase 3 found the second chance D-168 counted on (the page reader's pass) missing for a
    text invoice and while OvisOCR2 can't read; a read under way taken by a second reader as the clocks go forward or
    a VM is paused, then folded in twice; and a laptop's import that stopped for good after an error or a pause.
  - **The second chance** (the choice: a turn of the background process's page reader, not a queue of its own): an
    arrival whose chat call was put off is marked (`meta.chat_deferred`). When the page reader has nothing to read it
    takes the oldest such invoice no page read asks again for (`Store.take_chat_deferred`: none done nor in line;
    those in line too while the page reader can't read), its mark cleared as it is taken (asked again once, by one
    reader), and codes it again with no arrival limit (`page_worker.chat_again`), before the reviewer's turn. The
    result goes into its proposal only while nobody has worked on it (`replace_proposal`, the page reader's own writer:
    its guards answer D-168's worry of a writer under a reviewer), approved as touchless processing would have. A
    laptop never puts the call off (no limit), so it never has one.
    - **Why not a thread of its own:** it would ask the model machine one more thing at once than `llm_parallel`
      (D-171's reason), for what is rare: a text invoice, or a server whose OvisOCR2 is not there.
  - **The queues' clock:** staleness was compared on `updated_at`, the local time: as the clocks go forward an hour,
    every read and review under way looked an hour old until its next renewal (up to a minute). `page_reads` and
    `verifications` have `renewed` (`julianday('now')`, the database's clock, as the import queue's leases), set as
    they are taken and renewed, looked for at each start as their `reader` and `tries` are (the schema stays 27). A
    row from before has none: it is compared on its local time, as before (`_base._STALE`).
  - **Fenced:** `finish_page_read(reader=)` ends only that reader's read (as `finish_verification` does, B-787). The
    page reader checks the read is still its own after reading and before folding it in (a renewal: five minutes
    more), and stops reading once its heartbeat finds it gone; the reviewer folds in only a review it ended.
  - **A laptop's import** looks again every 5 s while files wait after a pause (no room, a restore) or an error (the
    GL accounts open in Excel, the database busy), as a capture process does, and the card says why a file waits (its
    reason on its row, so the server's card says it too). Before D-168 the card said "Processing stopped" and the
    files were listed again for the Process button; now they are in line, so the job tries again on its own and the
    card keeps following it. Its stop puts the file under way back, its try not counted, and takes no more.
  - **Not done:**
    - The second chance reads the invoice again in full (a scan's OCR from its cache) in the background process: on a
      server whose OvisOCR2 is never there, each arrival put off is read twice.
    - It comes only when the page reader has nothing to read: under a backlog of pages a text invoice waits for it.
  - (`core/store/{imports,page_queue,lessons,_base}.py`, `intake/{imports,mailbox}.py`, `ai/{pipeline,page_worker,
    verifier}.py`, `web/pages/process.py`, `web/app.py`, `bench/day.py`; `tests/core/test_imports.py`,
    `tests/intake/test_imports.py`, `tests/web/test_web_process.py`, `tests/capture/test_page_reads.py`,
    `tests/ai/test_page_worker.py`, `tests/ai/test_verifier.py`, `tests/ai/test_pipeline_arrival.py`,
    `tests/bench/test_day.py`)
- **D-173** · **A vendor's older lessons carried forward when an approval moves the learning memory, and the GL
  suggestions' memory extended by the lessons that moved; the vendor's lessons counted once a page; the accounts'
  words once a chart** (phase 3 part 2, item (b) of SCALE.md's phase 3; D-171's first "not done").
  - **Why:** at 300,000 invoices under 100 people, `learning_rows` / `_past_memory` were 15.8% of the busiest web
    workers' CPU and the suggestions' extension (`_with_past`, `_extended`) 4.6%. Past the memory's cap
    (`FEEDBACK_READ`, 50,000), `_past_memory`'s stamp holds the memory's last lesson, so every approval (its lessons in
    front, the oldest out) changed every vendor's stamp: the next ask of each vendor read all its older lessons again
    (up to 50,000 rows; the biggest seeded vendor has 59,000 lessons at 100k, 179,000 at 300k), in every process, and
    `_extended` went over them and the 50,000 shared rows again.
  - **The store** (`learning._carried`): in the memory's order (decisions, then history, each newest first) the rows
    past its last are a tail of the vendor's. When the last moved forward and the vendor only gained decisions since
    (its count up by exactly its lessons above the newest kept, that one still the same lesson, none of them history),
    the vendor's lessons between the new last and the old one are read (`vendor_key` and an id range, off its index),
    put in front of those kept, and of its newest `FEEDBACK_READ` as many of the oldest dropped as it gained: what the
    full read gives. A decision added past the new last (more at once than the memory holds) is read with them. The
    list kept is returned as it is when nothing moved. **Read whole** on anything else: a lesson of the vendor removed
    or moved to another, history of it imported, the memory's last moved back (a lesson forgotten), a restore (its
    newest kept gone, or another lesson by that id). The vendor's own stamp stays as it was (count, newest id, newest
    lesson); the memory's last is in it, as before.
  - **The suggestions** (`suggest._Older`, `_grouped`, `_carried`, `_extended`): each vendor's older lessons by line,
    with each line's pattern as last made, kept in `_OLDER` (`PAST_KEPT` vendors, `PAST_ROWS_KEPT` lessons, the latest
    used last). It holds none of the shared rows' memory, so it outlives a change of them: B-1719's rule for `_PAST`
    (the extended memories) is as it was. When the store's list is the one kept with lessons in front and the oldest
    dropped (`memory.added_in_front`, `UPDATE_MOST` at most), only those lessons' lines are gone over. A line's pattern
    is kept while its decisions are: its older ones (not among the lines changed) and its shared ones (the same list
    in `_Memory.lines`: D-171's update keeps the list of a line it doesn't change, and a memory built again has new
    ones). A line only in the older lessons is ranked again as the shared rows change (its rank is its place after
    them). The shared rows' decisions of a line come from `base.lines`, not a pass over the 50,000 rows. Exactly what
    building it of all the rows gives.
  - **Once a page:** a check read the stamp of the vendor's lessons three or four times (the history check, the
    accounts and cost centers used for it, its older lessons), each a COUNT of all of them (8 ms at 179,000): read once
    a page now while the database does not change (`_lessons_stamp`, `_session_memo`: `total_changes` and
    `data_version`).
  - **Once a chart:** every line suggested went over the words of every account (1,500 on the seed: 12,000 `_stems`
    for a check of 8 lines once the memory's answers are new, after each approval). `_account_stems` works them out
    once per chart (`Store.reference_data` keeps it, shared and never changed, until the accounts change; the latest
    four charts). The same suggestions: only the stems were ever compared.
  - **The proof:**
    - `tests/core/test_memory_store.py`: 300 random steps (approvals, history imported, invoices approved again,
      lessons forgotten, moved to another vendor, changed by an upgrade, more than the memory holds at once, a
      database restored), some vendors asked each time: what `learning_rows` gives is what reading all of the vendor's
      lessons gives, carried forward 791 times and read whole 159; the edges each on their own (the last moving from
      the history to the decisions, history imported past the memory, a restore whose next lessons take the lost ids,
      a decision added past the new last); the stamp read once a page. Nine sabotages of `_carried`, each caught.
    - `tests/ai/test_coding_no_ai.py`: 120 random approvals past a cap (and invoices approved again, lessons
      forgotten, history imported), some vendors asked each time: the extended memory is what building it of all the
      rows gives, everything it holds, and the same suggestions; nine sabotages of the carry, each caught. `_held`
      compares the index's word weights as a dict (read by word; `Index.updated`, D-171, keeps no build's order there,
      as `_as_built` already allowed).
    - On the 20k seed (below), 8 approvals: 57 vendor memories, each the full read and a build of all its rows, the
      same suggestions for its lines; 49 carried forward in the store and in the suggestions.
    - `scripts/same_behaviour.py --ref 1c73b77 tax approve pipeline` (Python 3.12): the same, all three (20,000 tax,
      1 approve, 1 pipeline outputs). `bench coding --realistic`: the same lines, byte for byte.
  - **Measured** (a seed of 20,000 invoices, 74,556 lessons: the memory's last moves with every approval; CPU,
    `time.process_time()`, medians of 10 rounds, each an approval of another vendor, then the check and the page of
    the biggest vendor and a small one; the 4 cores shared with other work). "Heavy": the biggest vendor given 40,000
    older lessons more (42,854 past the memory, as the biggest has at 300,000 invoices), measured last, with all of
    this; the others before them, without the stamp read once a page (a millisecond at 10,686 lessons):

    | CPU (heavy in brackets) | before | after |
    |---|---:|---:|
    | after an approval: a check, the biggest vendor (3,500 older lessons; 42,854) | 249 (1,025) ms | 108 (106) ms |
    | after an approval: its page | 99 (111) ms | 91 (101) ms |
    | after an approval: a check, a small vendor (57 older lessons) | 63 (62) ms | 43 (42) ms |
    | a check of the biggest again, nothing changed | 39 (47) ms | 37 (39) ms |

    The vendor's older lessons read in all, the first 26 requests: 358 → 20 ms; `_extended` 109 → 9 ms. What is left of
    a check after an approval is the suggestions worked out for the new memory (the memory changed) and D-171's
    update. `_OLDER` holds 0.9 MB for the heavy vendor (its rows' lists and its lines' shared decisions).
  - **Not done:**
    - A line's suggestions are worked out again after every approval (`_Memory.said` is the memory's): about 60 ms for
      8 lines; carrying them forward needs a rule for which lines an approval's patterns can change (a word, or one
      like it, in common), not taken here.
    - A vendor's own approval works out its lessons whole again in each process (`vendor_lessons`: its newest
      50,000, 360 ms heavy): carrying them forward needs its rows kept (D-154 keeps only what they say).
    - `metrics()` counts every lesson four times after each approval (the Learning page, Settings): 1.29 million at
      300k.
    - The arrival's examples and history check (`select_examples`, `compare_with_history` in `ai/pipeline.py`)
      aggregate a vendor's whole `learning_rows` past the cap afresh (not the shared list: `memory.aggregate` can't
      update it), 50,000 rows and more an arrival, in the capture processes.
    - Outside these files: the invoice page's Previous and Next (`navigation`, `list_invoices` of the review queue,
      the `listed` summary) are worked out whole after every approval: most of what is left of the page's 91 ms.
  - (`core/store/learning.py`, `ai/suggest.py`, `docs/HOW_IT_RUNS.md`; `tests/core/test_memory_store.py`,
    `tests/ai/test_coding_no_ai.py`)
- **D-174** · **A vendor's invoices brought up to date from a log of what changed, not read whole; the check reads
  them read-only; the vendors on hold and their approved numbers kept on their own changes** (scale goal, phase 3
  part 2, item (c); B-1790; changes D-160's stamp of `vendor_invoices` and D-159's of `approved_numbers`).
  - **Why (verified):** at 300k invoices and 100 people the check's `vendor_findings` was 10.6% of the busiest web
    workers' CPU, `_vendor_invoices` 9.3% of it. D-160 kept each vendor's invoices in each worker, stamped by the
    vendor's count of changes: any change to one of its invoices read all of them again, in every worker. A row is
    about 13 KB (the two codings 7.4 KB, the checks, the reading, the metadata), and `created_at`, `reviewed_at` and
    `export_batch` come after them, so each row's overflow pages are walked. On the 20k seed's biggest vendor (1,577
    invoices): the read 37 ms, without the JSON 13 ms, the columns before the documents 4.6 ms, the ids 0.8 ms. With
    that vendor made as big as the 300k seed's (19,088), the check's vendor path took 477 ms after a change.
  - **The choice: a change log, not a narrow table.** A narrow table of what the check reads, kept by triggers, would
    parse both codings in the trigger at each write of one, and every worker would still read all of a big vendor's
    rows after each change (as the columns before the documents read: 4.6 ms for 1,577, so about 55 ms for 19,000);
    the log costs a row of three columns per change, and a worker reads again only the invoices changed (about 1 ms
    for the vendor of 19,088).
    - `vendor_changes (seq INTEGER PRIMARY KEY, vendor_key, invoice_id)` (`_base.VENDOR_CHANGES`), written by the
      triggers that count each vendor's changes (`changes_vendor_invoice_*`, D-159: an insert, a delete, an update of
      a column `vendor_invoices` lists; a move logged for both vendors), in the same statements: every way an invoice
      changes is logged, whoever changes it (another worker, the background process, plain SQL): an arrival, an
      approval, a park, a rejection, a delete, an export's batch and its undo, a vendor renamed or merged
      (`_recompute_vendor_keys`), a restore's migration. A check's confidence is not listed, so not logged.
    - In those triggers, not one of its own, and with no index by vendor: measured raw, without the store (20,000
      invoices inserted, their batch set, 10,000 parked): 0.84, 0.28, 0.25 s without the log; a trigger of its own
      and an index 0.96, 0.36, 0.30; in the counts' triggers with the index 0.85, 0.34, 0.30, without it (pruned as
      below) 0.82, 0.32, 0.26. A worker reads the changes since its own off the primary key: 5 µs, 0.6 ms with 20,000
      changes of other vendors since (the most).
    - **Pruned in the same triggers:** each change removes the one `VENDOR_CHANGES_KEPT` (20,000) before it, a seek
      (not measurable above). The log never empties, so `seq` never goes back.
  - **The read:** `vendor_invoices` reads in one statement (one snapshot) the `summary_id`, the log's first and last
    `seq`, and this vendor's invoices logged since the `seq` its rows hold. Rows of another `summary_id` (a restore, a
    summary built again), or from before the log's first change (pruned since), are read whole; otherwise only those
    invoices are read again and put in their place (found by bisection: no sort of 23,000 rows), kept with the last
    `seq` read. Inside a write under way they are read afresh and not kept, as before: an undone write's place in
    the log is the next write's. Proven equal to a fresh read by random writes of two workers (each its own cache)
    and plain SQL, writes undone and restores among them, with a log of 8 changes and of 1,000; mutations of the
    triggers (the batch not logged, a move not logged for the new vendor) and of the read (pruning not seen, a
    deleted row left) each fail it (`tests/core/test_vendor_changes.py`).
  - **Old databases, restores:** the log is made with those triggers (`summary.make_triggers`), at every start and in
    a backup's copy being restored, which gets the counts' triggers its tables allow (B-1720): a copy of a backup
    made before the log gets the log first (otherwise every invoice write while its files were copied failed, "no
    such table"). The schema stays 27.
  - **The check reads the rows kept** (`vendor_invoices(key, shared=True)`), each read-only (`MappingProxyType`,
    40 bytes a row more): a copy of each, twice a check, was 21 ms for 23,000 rows (D-160). Other callers still
    get copies of their own. A vendor grown past half the rows a worker keeps lets go of what was kept (B-1790).
  - **Also in the check's vendor path:**
    - `approved_numbers` (the approved numbers of the vendors on hold, twice a check): stamped by those vendors' own
      counts (`changes/vendor/<key>`, read first), not `changes/approved`, which every approval of any vendor moves (a
      few a second under 100 people; 19 ms each at 100k); each pair of numbers once (`held_vendor` asks if any is).
    - `held_vendors`: kept until the vendor list changes (`changes/vendors`), not read out of 20,000 vendors each
      check (about 3 ms).
    - `vendor_findings` looks at each bank account its approved invoices carry once, not once an invoice.
  - **Measured** (one process's CPU, `time.process_time`, medians; the check's vendor path, `vendor_findings`,
    `expected_tax_numbers` and `moved_off_hold` in a page's session, right after another Store parked or unparked one
    of the vendor's invoices; before: 1c73b77, the same seed):
    - the vendor of 1,577 invoices: 52.4 → 5.5 ms (nothing changed since: 9.9 → 5.3 ms);
    - the vendor of 19,088: 477 → 18.7 ms (41.3 → 17.5 ms); the whole check's coding (`finalise_coding` with the
      store, as the review screen's check runs it): 529 → 78 ms (96 → 79 ms);
    - bulk writes: 200,000 ERP register rows imported 3.4-3.5 → 3.5-3.7 s of CPU (no change: the log is on the
      invoices); an export batch of 10,000 invoices and its undo 5.6-5.8 s either way (the summary's work).
  - **Not done:**
    - What is left of the check's vendor path is its loops over the vendor's invoices (the invoice checked left out,
      the totals in its currency and their median, the same-amount look): about 17 ms at 19,000. Facts kept per
      vendor would need the invoice checked taken out of them.
    - `vendor_invoice_dates` (recurring invoices for the queue's pills and month-end, 4.1% at 300k) is still read
      whole after any change of the summary's `dates` topic: phase 3 part 2's (a), with `erp/recurring.py`.
    - The vendor master's answers (`vendor_ids`, `master_by_tax_number`, the vendor list) are still worked out whole
      after any change of the vendor list (a few a day; a JD Edwards sync rewrites every vendor: D-160's open item).
  - (`core/store/vendors.py`, `core/store/_base.py`, `core/store/summary.py`, `coding/vendors.py`,
    `docs/HOW_IT_RUNS.md`; `tests/core/test_vendor_changes.py`, `tests/core/test_kept_answers.py`)
- **D-175** · **What the pages only show, worked out once for every process: Spend, the recurring vendors, Insights;
  month-end's and the duplicate audit's first time; no warming as a worker starts** (scale goal phase 3 part 2, P3;
  B-1797 to B-1802).
  - **Why:** in the profile of the busiest web workers with 100 people on 300,000 invoices (phase 3), Spend took
    17.3% of their CPU: its figures, kept by each worker for itself, were worked out again by each one every 30 s under
    the stream of approvals (19 visits in 10 minutes, nearly each one working them out again). Then the recurring
    vendors 5.6% (4.4% of it the queue's late pill), the quick search 2.4%, Insights 2.2%. And at 100,000 invoices the
    first request in a process was slow: month-end 5.6 s, the duplicates CSV 7.1 s, though both were shared (D-169),
    Spend 2.9 s, Insights 1.5 s.
  - **Shared, or kept up to date as things change: measured** (a 20,000-invoice seed of this code):
    - **Spend:** 0.47 s worked out for its default view (SQLite's four sums 0.16 s; each line in another currency
      converted to CAD in Python as before, 0.3 s). Kept up to date would need which invoices changed since (the
      summary keeps no list of them: `summary_dirty` is emptied as it is worked out) and sums of each month added up
      after, in another order than SQLite's: not the same figures to the last bit. Shared.
    - **The recurring vendors:** 0.1 s worked out (the invoice dates 0.07 s, `detect` 0.03 s). Kept up to date by each
      vendor's count of changes (`changes/vendor/<key>`: 2,649 read in 4 ms) would work out only the vendors that
      changed; but shared, the whole is worked out once a minute for every process (about 2 s at 300,000 invoices: 3%
      of one core, against about 1% kept up to date), for no second way of working them out. Shared.
    - **Insights:** seven sums of the summary, and every active invoice's dates: shared.
  - **What:**
    - `spend.figures` and `spend_currencies` (`FIGURES_FORMAT`), `recurring.detected` (`FORMAT`) and
      `insights.shown`, `shown_operations` (`SHOWN_FORMAT`) go through `Store.shared_answer`, each with its JSON form
      and back as it was (Spend's `total_by` rows tuples, the recurring vendors' dates, Insights' assumptions and
      tuples). The settles are as before (Spend 30 s, the recurring vendors and Insights 60 s), counted from when the
      work began in whichever process did it (D-169): never older than that. The recurring vendors are one name
      asked by the day (or Month-end's period end): at most 8 of them kept in the database, not a row a day for good.
    - **The queue's late pill** reads the recurring vendors with `LATE_SETTLE`, with no answer of its own settled on
      top of theirs (it could say what was true up to two minutes before: B-1798).
    - **A process waiting** for the answer another is working out takes one kept under the stamp it read as it asked,
      or a later one (B-1802): on one database the counts only go up (a restore gives another `summary_id`). Only an
      answer read from the database, or another thread's of this process; never this process's own older one.
    - Month-end looks its GL accounts up by code (B-1800); the quick search's ERP hits work each vendor's name out
      once (B-1801).
  - **Month-end's and the duplicate audit's first time** (5.6 and 7.1 s at 100,000 invoices): the volume run asks in
    one process on a seed no answer was kept in yet, so its first ask is the first anywhere, the same work as before;
    D-169 measured 0.8 s and 0.01 s in each other process. Those 0.8 s were month-end's recurring vendors, kept by
    each process for itself (now shared: 1-3 ms), and each visit's GL look-ups (0.25 s of 0.4 at 20,000 invoices,
    B-1800). And under a stream of changes a worker waiting for another's answer worked it out again itself (B-1802).
    What is left is the answer worked out after a change (month-end 1.1 s at 20,000: its invoices not in the ERP
    decode every one of them).
  - **No warming as a worker starts:** a worker that starts takes each of these from the database in 1-3 ms (Spend's
    page 48 ms in all, its answers 3 ms, against 0.47 s worked out); after a change the first ask works it out once
    for all, the others waiting for it. Working them out as a worker starts would do that same work again at every
    restart, while nobody asks, and under a stream of changes they are stale within their settle anyway. The rest of
    a fresh worker's first request (0.15-0.2 s at 20,000 invoices: Jinja compiling the page's template, the first
    session) is every page's, not these answers'. It doesn't pay, so a worker's start (the service's health check, the
    laptop) is as it was.
  - **Measured** (the 20,000-invoice seed; CPU by `time.process_time`; the machine shared with three other agents):
    - **A page in a second process after a change in a first**, the first ask then the second (the first ask of a
      process includes 0.15-0.2 s of its own start): Spend 0.59 / 0.01 s before and 0.57 / 0.01 s now where it is
      worked out, 0.60 → 0.23 s in each other process; Insights 0.45 → 0.39, and 0.44 → 0.19 in each other;
      Month-end 1.59 / 0.45 → 1.16 / 0.18 s, and 0.73 / 0.38 → 0.37 / 0.14 s in each other; the queue 0.81 → 0.71 s
      and 0.84 → 0.63 s; the duplicates CSV 1.10 → 1.05 s, 0.14 → 0.13 s in each other (shared before).
    - **Four web workers and a change a second** (150 s; each worker the queue every 2 s and a report every 8 s:
      Spend, Insights, Month-end, Vendors): the workers' CPU 48.3 → 37.8 s (49.4 → 38.3 in a first run), Spend 5.4 →
      2.7 s, Insights 2.1 → 1.0 s, Month-end 12.5 → 7.4 s, the queue 24.8 → 23.0 s, Vendors the same (3.5 / 3.7 s:
      its vendors' summaries are each worker's).
    - **The same pages:** 18 addresses (Spend's views, Insights, Month-end and its CSV, the queue's tabs, the search,
      Vendors, the audit and its CSV), each asked twice, byte for byte as before, both as worked out and as another
      process takes them; `scripts/same_behaviour.py --ref 1c73b77 tax approve`: same.
  - **Not done:**
    - Month-end's answer after a change (its invoices not in the ERP decoded whole: per-invoice parts as D-169's POs
      would need each invoice's own count of changes, and its checks' `validation` is not in `changes/vendor/<key>`).
    - The quick search's word looked for in every ERP register row (`instr` over the register: an index of its own
      would be a schema change).
  - (`core/store/reports.py`, `erp/{spend,recurring,insights,search}.py`, `web/pages/{review,month_end}.py`,
    `docs/HOW_IT_RUNS.md`; `tests/core/test_kept_answers.py`, `tests/erp/{test_insights,test_suggest_recurring,
    test_search}.py`, `tests/web/test_web_finance.py`)
- **D-176** · **Import invoices on the company server: the invoices folder listed once for every process and kept in
  the database; a view shows it, the files put in line or read since left out at once; a file saved in the folder
  shows within a minute** (phase 3 part 2; changes D-166's list kept in each process, on the server only).
  - **Why:** at 300,000 invoices the busiest web workers spent 16.9% of their CPU on Import invoices (py-spy:
    `folder_files` 16.4%, `unseen_files` 5.6%, `folder_names` 3.7%). The invoices folder keeps every file ever
    received, and D-166's list, kept in each process, was worked out again in each worker after any invoice, pick-up
    note or import batch anywhere (`files_stamp`), a change of the folder, or a minute on: under load, at nearly every
    view, 2.75 s of CPU each (the names 0.8 s, each file's size and time 1.0 s, the one query 0.8 s).
  - **The listing** (`core/store/folder.py` `folder_listings`, made by its own start-up, `fill_seen_files`, whatever
    the version: no schema change): one row a folder, its resolved place, when the listing began and the folder's
    time read before it, and what it found as JSON: the invoice files new then (name, size, time), the saved emails,
    the files of a kind not read, how long it took (`intake.imports.list_folder`, `Listing.keep`).
  - **Who lists:** a pick-up lists the folder anyway (the Process button, a scheduled watch: `pick_up` keeps its
    listing once it has read the new files, each file read with the size and time it was read at, as it goes in line,
    B-1814; the Process button keeps it again without the emails it unpacked, and takes the files of a kind not read
    from it, no second listing). A page view on the server that finds the listing due starts a thread of its own
    process that lists it (`process.list_for_all`): one at a time across the processes (a lease `folder-listing:
    <place>` of 60 s, renewed every 15 s by a thread of its own whatever the listing is doing, a renewal the database
    refuses tried again at the next, B-1817: a slow network folder is listed once), and one thread a process; a
    thread that can't be started lets go of both, for the next view due (B-1816). A pick-up and a page's thread may
    list at once (the pick-up holds the pick-up lock, not the lease): whichever is kept last stands, both the folder
    as it was, the next one due catching up.
  - **Due:** once the folder's time changed (a file added, removed or renamed) and the listing ended 20 s ago
    (`SHARED_REFRESH`), or 45 s ago whatever changed (`SHARED_MOST`: a scan written over another of its name, an
    invoice deleted whose file is new again). Counted from the listing's end, so a folder that takes minutes to list
    is not listed back to back.
  - **A view** does one look at the folder's time, one read of the listing, and `unseen_files` of its new files only:
    a file put in line or read since, by any process, is no longer offered at once; one the pick-up read with a time
    later than both the listing's look at it and the listing's start was written again since, and counts as read
    (`since`, B-1814: a page's thread that looked before a pick-up, kept last; one written over by an older copy, its
    time earlier, is still offered). It waits (5 s at most, `SHARED_WAIT`) only when it started the listing itself
    and the one kept is older than 45 s, or there is none: a stopped process's lease (up to 60 s) or a slow folder
    never makes every view wait on another's listing. A listing shown older than 45 s says when it was made, as the
    screens say a time (`ui.day_and_time`, B-1815): "As the invoices folder was today at 10:41 a.m.: files saved
    since show once it is listed again", "… was Nov 3 at 1:19 p.m. …" one made another day; none yet: "The invoices
    folder is being listed", never "No new files". A listing that failed is kept as such, with why, so each
    process's page says it (B-1804); the Process button says it too (B-1806). The card states the delay: "New files
    are listed here within a minute".
  - **The laptop** (one process) as before: each view lists when the folder, a pick-up's notes or the invoices
    changed, or a minute on, through the same `list_folder`; it keeps nothing for other processes.
  - **Cheaper, everywhere:** `folder_names` 0.79 → 0.30 s at 300,000 files (a name's suffix and stem without
    `os.path.splitext`; the text-copy rule only when there are text files; a plain sort where `normcase` changes
    nothing): the same three lists as before for about 1,600 names of every case, dots and script, checked against
    the old code. `unseen_files(listed, folder=)` of a whole folder's listing (1,000 files or more) reads what the
    pick-up read in that folder in one range of `seen_files` (0.81 → 0.58 s), unless it read there more than twice
    the files listed (files removed since): then one by one, as before.
  - **One rule:** `pipeline.invoice_files` and `other_files` already are parts of `intake.files.folder_names` (D-168);
    nothing is repeated in `ai/pipeline` any more.
  - **The load bench's search to Excel and CSV** (B-1805) go as the page's link sends them on the server (D-169): the
    job asked for with the page's header, followed as `app.js` follows it (at once, then 0.5 s, half as long again
    while nothing changes, 5 s at most), then the file downloaded; the report's format is the same, with the file's
    own route (`GET /search/export.xlsx?job={id}`) beside the asking one and `GET /downloads/{id}`. A job that failed
    or is gone, or a file not there once made, is counted "search exports not made" and fails the gate, as the plain
    address's 500 did (B-1813); one still being made when the person stops waiting (3 minutes, the run's end) does
    not.
  - **Measured** (process CPU, 4 cores shared with three other agents; a seed of 20,000 invoices with 280,000 empty
    files added to its invoices folder, each noted as read, 300,000 in all):

    | | before (1c73b77) | after |
    |---|---:|---:|
    | a server view after an invoice or a pick-up elsewhere | 2,748 ms | 3.0 ms |
    | a server view, nothing changed | 2.3 ms | 1.3 ms |
    | the folder listed (names, sizes and times, what is new) | 2,750 ms, each worker | 1,940 ms, one of them |
    | `folder_names` | 790 ms | 296 ms |
    | `unseen_files` of the 300,000 | 831 ms | 584 ms |
    | a laptop view after a change | 2,747 ms | 1,984 ms |

  - **Not done:**
    - The listing runs on a thread of a web worker (one at a time for the whole server: about 2 s of CPU at 300,000
      files, at most every 20 s while the folder changes and someone looks). The background process could make it
      instead, with a hook in `app.start_background_work` (not this work's file).
    - Each file's size and time is still looked at (1.0 s of the listing's 1.9 s): it is what shows a file written
      over in place. On Windows the listing itself has them (`DirEntry.stat`, no look at each file over a network
      share), not used: what the pick-up notes comes from `os.stat`, and the two must agree.
    - `seen_files` keeps the rows of files removed from the folder for ever; past twice the files listed, the
      folder's listing is looked up one by one again.
    - A view looks at none of the files listed: one removed shows until the next listing (within the minute).
  - (`core/store/folder.py`, `intake/{files,imports}.py`, `web/pages/process.py`, `web/templates/process.html`,
    `bench/load.py`; `tests/web/test_web_process_listing.py`, `tests/web/test_web_process.py`,
    `tests/intake/test_files.py`, `tests/intake/test_imports.py`, `tests/core/test_imports.py`,
    `tests/bench/test_bench_load.py`)
- **D-177** · **Month-end's billing invoices' lines kept for every process by vendor and brought up to date from the
  vendors' change log; a line matched by its description alone when no price can change it; the POs and their
  billing read in one moment** (scale goal, phase 3 part 3, Q1; B-1818; changes D-166's and D-169's received POs).
  - **Why (verified):** at 300,000 invoices and 100 people, month-end was 22% of the busiest web workers' CPU,
    `_received_not_invoiced` 18.2% of it: `billing_codings` 9.3% (the SQL 4.5%, the JSON 4.1%), `positive_lines`
    4.0%, `_accrued`'s `billed_on` 3.4%. Each received PO's accruals are kept by a digest holding the counts of
    changes of its billing invoices' vendors (`_made_of`), so a change to any invoice of a vendor put every PO of the
    vendor in `todo`, and every billing invoice of those POs (those quoting it, and every invoice of the vendor on
    no PO: 23,000 for the biggest at 300,000) was decoded and its lines read again; nothing was kept per invoice.
    Under load many vendors change between two computes: at 300,000, tens of thousands of invoices each time.
  - **The choice: in the database, not each process** (measured on a 20,000-invoice seed of this code):
    - What month-end reads of an invoice (its coding's date, its lines billing a quantity) is about 450 bytes held
      in Python, 300 as JSON. Billing invoices are 70% of all (13,918 at 20,000, about 210,000 at 300,000: 95 MB in
      each process), and the vendors with invoices in the review queue hold 81% of them.
    - Simulated at 300,000 (the seed's vendors' billing invoices x15, changes drawn by the queue's share), with 60
      changes between two computes: keeping none decodes 75,420 invoices a compute; a process keeping at most 50,000
      (23 MB, its vendors asked longest ago going first) 74,935: it thrashes; 100,000 (45 MB) 9,736; 150,000 (68 MB)
      1,198, most billing invoices in every worker. With 10 changes: 30,723, 14,223, 1,709, 464.
    - A vendor's row read back from the database costs 4.6 us an invoice (2.8 the JSON), against 34 us to decode
      its coding again; nothing held in any worker, no first decode in each.
  - **What:**
    - `report_answers`, a row a vendor (`purchasing.BILLING_ANSWER` and its key, `BILLING_FORMAT`): each billing
      invoice's [id, date, lines], stamped (`summary_id`, the log's last change, the vendor's count of changes) as
      `vendor_invoices`' rows are (D-174). `Store.billing_lines` reads it with the log in the same moment: a row of
      another `summary_id` (a restore), from before the log's first change (pruned since), or whose vendor's count
      moved by more than the log says (an older AP Coder, B-1811) is decoded whole; otherwise only the invoices the
      log names since. A row is written again once `BILLING_REWRITE` (32) of its invoices, and a sixteenth of them,
      were decoded since: until then each read decodes those again (the biggest vendor's row is 7 MB at 300,000).
      Not kept: an invoice with no vendor key (no log follows it), a coding not read so (not an object, its lines
      not lines: read as before at each use, failing where it did) and one with a NaN. Inside a write under way
      nothing kept is used or kept.
    - **One moment** (`Store.billing_moment`, B-1818): the received POs, the invoices billing them (the summary
      brought up to date first), the log, the vendors' rows and the codings decoded, in one read
      (`learning.one_snapshot`); the rows to keep are written once it is over, never in it. The answer's stamps are
      as before (`changes/purchase_orders`, the summary's generation; each PO's digest with its vendors' counts): a
      change of a vendor's invoice still works its POs out again, from the lines kept. A vendor's invoices on no PO
      are digested once for all its POs.
    - **Matching** (`coding/po.billed_on`, `_price_decides`): a PO line's price, when it is an invoice line's, can
      change what the line matches only when that PO line resembles it, at the same price, at least as much as the
      most alike does at any price and enough to match (`MATCH_THRESHOLD`). With none of them a line matches
      nothing; with only the most alike, itself enough, that one: by its description alone (most lines); otherwise
      only those PO lines' price bands are looked at. The same quantities to the last bit as matching each line in
      turn (`billed_by_line`): random POs and lines with prices at each edge of a band, then with how alike they are
      drawn from values that tie exactly at the same price (0.25 + 0.2 = 0.45); three mutations caught, two that only
      look at more bands than needed survive (the same matches).
  - **Proven** (`tests/erp/test_accruals.py`): random changes by two workers and plain SQL, with a log of 8 changes
    and of 1,000 (an invoice added, approved with other lines, its lines, date or coding's date changed, moved to
    another vendor, to none or to another PO, deleted, exported and undone, parked, rejected and reopened; the POs
    imported again; an older AP Coder's change, logged nowhere; a write undone with month-end asked inside it; a
    restore), each worker asking at either period end: the accruals CSV and the page byte for byte what working them
    out afresh gives (nothing kept in any process or the database). Each rule of `billing_lines` mutated (the
    count, the pruned log, the `summary_id`, the changed invoices left in, kept rows used or written in a write
    under way), a PO's digest without its vendor's invoices on no PO or kept with an invoice of no vendor: each fails
    it; the moment left out fails B-1818's test (another worker's approval while month-end reads). After a change of
    one of a vendor's invoices, another process with nothing of its own decodes that invoice alone, and the vendor's
    row is written again once enough were decoded since.
  - **Measured** (CPU of the second process, `time.process_time`, medians of 5; the first process parked, edited
    (a line's quantity, plain SQL) or approved (a line's words changed) an invoice of the biggest vendor; a
    20,000-invoice seed of its own, 4 cores shared with two other agents; before: 88d82e0, each pair run together).
    Received, not invoiced, in seconds; month-end in all in brackets (the rest is its invoices not in the ERP yet):

    | one change of the vendor's invoices | park | edit | approve |
    |---|---:|---:|---:|
    | its 1,560 billing invoices, before | 0.36 (1.04) | 0.38 (1.04) | 0.35 (0.97) |
    | after | 0.22 (0.80) | 0.24 (0.82) | 0.25 (0.84) |
    | made as big as 300,000's biggest (17,889 billing invoices), before | 0.96 (1.45) | 0.96 (1.48) | 0.93 (1.56) |
    | after | 0.39 (0.97) | 0.38 (0.92) | 0.40 (1.02) |

    60 changes of any vendors' invoices between two computes: 0.47 (1.08) before, 0.28 (0.84) after. The first
    compute on the database, every vendor's row written: 1.10 (1.90) before, 1.16 (1.97) after.

    What is left after a change at 20,000 is mostly the same at every compute: every received PO read (2,965), the
    summary's 13,918 billing rows and their digests, the shared parts read and written again; at the vendor of
    17,889, its row read back (60 ms) and its lines matched to its PO again.

  - **Not done:**
    - A compute still reads every received PO (3,000: 50 ms), the summary's billing rows (all of them: about 0.2 s
      at 300,000), their digests, and matches each PO it works out again to all its billing lines. A PO's billed
      quantities kept and updated would sum them in another order than a fresh compute: not the same to the last bit.
    - The first compute on a database writes every vendor's row (about 63 MB at 300,000, once).
    - The other half of month-end after a change, its invoices not in the ERP yet, still decodes each of them
      (D-175's open item: their checks' `validation` is in no vendor's count).
  - (`core/store/purchasing.py`, `erp/accruals.py`, `coding/po.py`, `docs/HOW_IT_RUNS.md`;
    `tests/erp/test_accruals.py`)
- **D-178** · **A vendor's older lessons read by line, kept in the database for every process and carried forward;
  their rows read only when an invoice's reading asks for them** (scale goal, phase 3 part 3, Q2; changes D-173's
  `_OLDER` and what `learning_rows` reads at once).
  - **Why:** at 300,000 invoices `_past_read` was 12.9% of the web workers' CPU. A probe of 400 checks of the review
    queue's invoices (no writes): 380 read the vendor's lessons past the memory's cap whole, 8,200 rows and about
    115 ms of CPU each. Each process kept 32 vendors' and 60,000 of their rows (22 vendors there) while the queue's
    invoices came from 1,041; most vendors have thousands past the memory (`FEEDBACK_READ` 50,000 of 1.29 million).
    D-173's carry forward held for the vendors kept, and most were not. A check only needs them for the GL
    suggestions (`invoice_view.suggestions`), which read of them each line's decisions, not the rows.
  - **The candidates, measured** (one vendor of a 20,000-invoice seed with 800,000 older lessons added for 100
    vendors with invoices in review, half on their own few lines, half with 2,500 lines each; CPU by
    `time.process_time`, 11,500 / 8,908 rows):
    - as before (`SELECT *`, a dict a row): 195 / 127 ms, SQLite alone 120 / 83 ms (it sorts all the vendor's rows);
    - (a) only the columns the consumers read: 106 / 76 ms; still a cost a row, and rows of a byte cap still miss;
    - (b) the rows kept for every process as JSON: decoded in 32 / 24 ms, but 5.7 / 4.3 MB a vendor to write again
      at each change (the biggest vendors change with nearly every approval);
    - (b) the same lessons by line: 1 KB / 0.19 MB of JSON, decoded in 0.0 / 2.9 ms (11 ms into objects with 5,160
      runs). Taken;
    - (c) a read of only the lines an invoice's lines can match: not built. The index's word weights (one over the
      accounts each word's lines go to) and the vendor's usual account count every line, so leaving out the lines
      that can't match changes the suggestions of those that can.
  - **What** (`core/memory.py` `PastLines`, `core/store/learning.py` `_past_lines`, `ai/suggest.py` `_extended`):
    - `PastLines`: the lessons past the memory by line (vendor key, description normalised), each line's runs of
      lessons in a row on one account, newest first, each with its newest's number, description, cost center and
      whether it changed the account (`changed_account`); whatever the chart (the suggestions take the runs on its
      accounts). And each lesson's line in their order (an `array` of 4 bytes a lesson), which says whose oldest goes
      when the vendor's newest `FEEDBACK_READ` drop one. `carried(front, keep)` is `of(front + rows)[:keep]` going
      over those only, the lines it doesn't change keeping their runs (the same tuples).
    - `Store._past_lines`: read with the stamp of `_past_memory` (the vendor's lessons stamp, the memory's last, in
      one snapshot with the whole memory's stamp, B-1809's lesson: read across a change, used once and not kept).
      Kept in each process (`_base._PAST_LINES`, `PAST_LINES_KEPT` 4,096 vendors and `PAST_LINES_BYTES` 64 MB by
      `PastLines.size`, counting each run's words: 0.87-1.01 of tracemalloc, B-1841), else taken from the database's
      copy (`past_lines`: the stamp, with this version and `FEEDBACK_READ`, and the lines as JSON; never pickle; AP
      Coder's version rarely moves, so a change to what a copy means, its runs or how a line is normalised, must move
      `PAST_LINES_FORMAT` in `core/store/learning.py`), carried forward from either by
      D-173's rule (`_moved`, shared with the rows: only the lessons the memory let go are read, six columns of them),
      read whole only when neither can be (a lesson of the vendor removed or moved, history of it imported, the memory's
      last moved back, a restore whose lessons took other ids). The copy is written again once a process carried it
      `PAST_WRITE_AFTER` (64) lessons on, or read them whole; never inside a write under way, and only if the database
      is free within `BRIEF_WAIT_MS` (`_briefly_or`). One that can't be read, or doesn't add up, is worked out again.
    - `WithPast` is no longer a list: the rows past the cap are read (`_past_memory`, as before) only when it is read
      as one (an arrival's examples, history check and tax codes), never by the suggestions, which read `older`.
    - `suggest._extended` goes over the lines of `PastLines`, a line's pattern made again only when its runs or its
      decisions in the shared rows are other objects than when it was made (`_OLDER`: the patterns made, by vendor);
      D-173's `_Older`, `_grouped` and `_carried` go.
    - What changes lessons in place clears the copies with them: the upgrades that recompute vendor keys and clean
      the memory (`_base._migrate`), Forget history and a lesson forgotten on the Learning page (the vendor's).
      Schema 27 still: the table is made by `_SCHEMA` (an older version leaves it be; a backup has it, consistent
      with its lessons, so a restore carries it forward).
  - **Measured** (the seed above; 300 checks drawn from the 100 vendors, their mean; the 4 cores shared):

    | CPU per check (mean) | before | after |
    |---|---:|---:|
    | a vendor on few lines, nothing approved meanwhile | 160 ms | 14 ms (39 in the first process) |
    | a vendor on 2,500 lines | 225 ms | 52 ms (84) |
    | under a stream, an approval every third check: few lines | 174 ms | 21 ms |
    | the same: 2,500 lines | 245 ms | 73 ms |
    | an approval | 20.6 ms | 19.3 ms |

    The probe of the 300k seed (`learning_rows` of 400 invoices of the review queue, here from its 957 vendors, a
    process that keeps nothing yet): read whole 354 times (1.59 million rows) → never (147 times where no process
    had asked before: 1,211 lessons, the copies made), about 69 → 3.4 ms of CPU an ask.
    Under the stream: 248 of 300 checks read the rows whole before; after, 90 took the lines from the database's
    copy (14.5 ms mean, the 2,500-line vendors' JSON), one wrote it again, none read the rows. What is left on the
    2,500-line vendors is the extended memory's patterns made once in a process (64 ms cold, 14 ms warm). A web
    worker after 200 checks of the 100 vendors (tracemalloc, beyond the shared memory): 155 → 149 MB, holding 62
    vendors' lines and 32 vendors' patterns instead of 7 vendors' rows. The real seeds' vendors are on few lines (41
    descriptions in all on the 20,000-invoice seed), the first case.
  - **The proof:**
    - `tests/core/test_memory_store.py`: two processes on one database (each its own caches), 250 random steps
      (approvals, some vendors past the cap of their own, invoices approved again, lessons forgotten by the Learning
      page and by plain SQL, history imported, an upgrade, more than the memory holds at once, a restore), vendors
      asked in either: the lines are what reading the lessons whole gives, the rows too when read, and the GL
      suggestions those of a memory built of all the rows (480 asks, 151 from the copy, 206 copies written). D-173's
      300 steps, its edges and B-1809's race now check the lines too; a copy of another version or memory size, or
      one that can't be read or doesn't add up, or no table, is worked out again. Twelve sabotages (a drop, a number
      off, a run's newest kept, runs merged across accounts, a flag lost in the JSON, a copy's stamp not compared, one
      of another memory size, the lean read without `suggested_gl`, the race kept, three of `_extended`'s reuse),
      each caught.
    - On the seed above, 400 invoices of the queue (300 of the 100 vendors): every line's suggestions, and on 100 of
      them the examples chosen and the history check, the same byte for byte as before.
    - `scripts/same_behaviour.py --ref 88d82e0 approve pipeline` (Python 3.12): the same. `bench coding
      --realistic`: the same lines, byte for byte (the reports but their time).
  - **Not done:**
    - A capture process reading an invoice of a vendor past the cap reads its lines too (from the copy, or whole once
      for every process), besides its rows as before.
    - Two processes finding no copy they can carry forward each read the lessons whole (no lease between them).
    - Reopening, rejecting or deleting an invoice forgetting its lessons (`core/store/invoices.py`) leaves the
      vendor's copy until its next ask writes it again (it is never used: its stamp is another).
  - (`core/memory.py`, `core/store/{learning,_base}.py`, `ai/suggest.py`, `docs/HOW_IT_RUNS.md`;
    `tests/core/test_memory_store.py`, `tests/ai/test_coding_no_ai.py`)
- **D-179** · **What the pages only show at 300,000 invoices: Sales tax and Learning worked out once for every process
  and settled; a settled answer's askers take the one before while another works it out; Previous and Next read the
  queue's ids; the duplicate check and the parked list off the summary** (scale goal phase 3 part 3, Q3; B-1832 to
  B-1837).
  - **Why:** the profile of the two busiest web workers with 100 people on 300,000 invoices (phase 3 part 2,
    [phase3b-load-100-people.md](archive/scale-2026-11/phase3b-load-100-people.md)) had, beside month-end's accruals
    and a vendor's older lessons (other work): an invoice's page 16.8% and its check 15.7% (`list_invoices` 5.1%,
    `vendor_findings` 4.6%, `find_duplicates` 3.3%), the queue 14.8%, Sales tax 6.3%, Learning 5.9%; and at
    100,000 Spend's and month-end's p95 (4.7 s, 7.5 s) were the others asking waiting for the one working them out.
  - **What:**
    - **The duplicate check** seeks the invoice number as it compares (the summary's `number_key`, its index), not
      every invoice of the vendor read and compared in Python (B-1832).
    - **Previous and Next** in the queue's own order (no search, sort or view of its own: the queue's links) read
      its ids alone off `summary_queue_order` (`Store.listed_ids`), not the whole list again after each approval
      (B-1833).
    - **A settled answer** (`shared_answer(..., settle=)`, D-164): while another thread or process works it out
      again, the others asking take the answer before, this database's latest (in the process, or the database's
      when later), instead of waiting up to `ANSWER_WAIT`; with none, or an exact answer, they wait as before
      (B-1834). Its age is then at most the settle and the time the other takes to work it out: D-164's "at most
      that old" now reads "at most that old, but for the work under way". Month-end's settled answers too. Only an
      answer whose work began less than the settle and `ANSWER_WAIT` ago is shown so, and only while another thread
      holds the work or another process its hold: a database too busy to give the hold is not another process
      working it out (B-1838).
    - **Sales tax:** what the page shows (totals, counts, the rows listed: `sales_tax.shown`) is one shared answer
      for the dates, the view and its rows, settling 30 s as Spend's; `taxreturn.setup_key` (the tax setup, the rate
      table, our provinces, JD Edwards' settings and batches) is part of what it is asked for, so a change of one
      shows at once. The CSVs, filed from, are worked out afresh, as month-end's (B-1835). Kept whole, the quarter's
      claims would pass `ANSWER_MOST_BYTES` at 300,000 invoices and hold 100,000 claims in each worker: what the page
      shows is a few hundred rows.
    - **Learning:** the Coding accuracy tab's metrics, approval counts and corrections are shared answers settling
      60 s as Insights' (`changes/lessons`, `changes/situations` and the summary's generation), with the count of the
      page's own changes (`lessons_forgotten`, `history_imported`) as what they are asked for: a lesson forgotten or
      taught there shows at once. The latest lessons and the memory list are read at each visit, as before (B-1836).
    - **The parked list** (the queue's follow-up pill and Parked tab) is read off the summary, not the invoices'
      whole rows, whose reason and follow-up day come last (B-1837).
  - **Kept exact, or not done (measured at 20,000 invoices, scaled by the vendor sizes at 300,000):**
    - **A check's vendor history** (`vendor_invoices`, 3.6% at 300,000): each vendor's rows are kept and brought up
      to date from the vendors' change log (D-174), but the queue's 1,041 vendors hold about 237,000 invoices and a
      worker keeps 50,000 (`VENDOR_ROWS_KEPT`): most checks read their vendor again, the big ones' thousands of rows
      (an expected few thousand a check). Reading them is 9 ms of plain columns and 23 ms of `json_extract` of their
      five numbers from each coding for the biggest vendor at 20,000 (1,577 rows); the same rows off the summary take
      3.9 ms. The numbers kept in the summary (the GST/HST number is, `gst_number`; QST, PST, the bank account and the
      original invoice number are not) would make that read 8 times cheaper: a summary rule and schema change, its
      rebuild at the upgrade, in `core/store/{summary,vendors}.py` (not this work's files).
    - **The recurring vendors** (the queue's late pill, 3.9%): worked out once a minute for every process (about 2 s
      at 300,000); kept up to date by each vendor's count of changes (D-175's estimate: a third of it) is
      `erp/recurring.py`'s, not this work's. Their askers now take the answer before while it is worked out (above).
    - **The queue's lists** (`list_invoices` of the three queues, 45 ms at 20,000 after each approval or park): the
      queue's size, not the database's, sets it; brought up to date by the invoices changed would need a change log of
      the summary.
    - **Sales tax kept up to date** rather than settled: under 100 people the queue's approvals of invoices dated in
      the default quarter change it all day, and the summary keeps no list of the invoices changed since (D-175).
    - In an invoice's analysis the rest that grows with the database is the learning memory's (`learning_rows`,
      `suggest_gl`: other work).
  - **Measured** (CPU by `time.process_time`, the 4 cores shared with two other agents; a 20,000-invoice seed of this
    code; a change made in another process before each ask):

    | | before (88d82e0) | after |
    |---|---:|---:|
    | an invoice's page after a park elsewhere (20 invoices, each its first) | 86.5 ms | 43.8 ms |
    | its check | 32.2 ms | 31.4 ms |
    | the duplicate check, the biggest vendor (1,577 invoices) | 6.3 ms | 1.1 ms |
    | Sales tax after an approval elsewhere, within the settle | 113 ms | 17 ms |
    | Sales tax with nothing changed | 119 ms | 15 ms |
    | Learning after an approval elsewhere, within the settle | 57 ms | 17 ms |
    | a second worker asking Spend 0.1 s after a first, past the settle (wall) | 0.53 s | 0.02 s |
    | month-end's two workers so (wall) | 1.09 / 1.05 s | 0.56 / 0.69 s |
    | the parked list, 146 invoices, on a request's own connection | 3.6 ms | 2.1 ms |

    The same bodies, byte for byte, from the code before and now on the same seed: 14 Sales tax pages and CSVs, 6
    Learning pages, 8 queue pages (its tabs and views), 12 invoice pages (the queue's own view and five others),
    and 20 invoice pages and checks in a row; `scripts/same_behaviour.py --ref 88d82e0 tax approve`: same.
  - (`core/store/{invoices,reports}.py`, `coding/taxreturn.py`, `web/pages/{invoice,sales_tax,learning}.py`,
    `web/templates/sales_tax.html`, `docs/HOW_IT_RUNS.md`; `tests/core/test_store_perf.py`,
    `tests/core/test_kept_answers.py`, `tests/web/test_web_finance.py`, `tests/web/test_web_analytics.py`)
- **D-180** · **A backup for each model server: one breaker a server, shared by the company server's processes; a
  call under way on a failed primary asked again on the backup at once; a chat model other than the first trusted
  for touchless approval only once it read the self-test's invoice right** (phase 4, M1; SCALE.md's target 3: "the
  model machine killed, hung, slow or swapped: nothing lost or doubled, and failover to the backup in under a
  minute"; B-1843, B-1844).
  - **Why:** the planned layout puts the models on a Mac Studio with a Mac mini as its backup. AP Coder had one
    address a model server (`AP_LLM_BASE_URL`, `AP_PAGE_READER_BASE_URL`) and nothing moved to another on its own: a
    dead Mac Studio stopped the page reader and the AI reviewer until a person changed the .env, and a hung one held
    each page read up to its 20-minute word limit and each review its minutes. A chat model swapped in LM Studio was
    asked for by its old name by every capture process until it restarted (B-1843), a review asked of it ended unsure
    (B-1844), and the new one was then used, its reviews approving held invoices, with no test of its own.
  - **The settings:** `AP_LLM_BACKUP_URL` (the chat model's backup, and the page reader's when it shares that server)
    and `AP_PAGE_READER_BACKUP_URL` (a page reader on a server of its own, `AP_PAGE_READER_BASE_URL`): the .env,
    *Settings → Reading → For IT → Backup model servers* (Admin; refused for an address off the company network by
    the offline rule's own checks, `refused_address`, as the main address, and for the main server itself), or
    server.json's `llm_backup_url` / `page_reader_backup_url` (given to the dashboard as its environment, which wins
    over the .env: the form then shows it and leaves it). The service host reads the .env into its own environment
    first, so it lets server.json's win over a value the .env put there (`service.OVER_ENV_FILE`, B-1873); the
    machine's environment still wins over both. The service's other settings keep their order, a .env's winning over
    server.json, which `service install` writes in full (its `llm_parallel` 1 would hide the .env's): SERVER.md says
    so. Empty: none, and nothing below runs: no breaker, no thread, nothing kept, every call to the one server as
    before (laptop mode; `same_behaviour.py` the same).
  - **The breaker** (`local_llm.Failover`), one a server: the chat model and the page reader share one when they share
    a machine and its backup (the planned layout); a page reader on a server of its own has its own. One a model
    (the first try) left the page reader on a killed Mac Studio: it saw the model list fail, read nothing, and so
    never counted a failure while the chat calls had long moved.
    - **Moves the calls** after `FAILOVER_FAILURES` (2) calls in a row the primary didn't answer: refused or dropped,
      a 5xx, no answer within the call's limit (the arrival's 20 s, the reviewer's time left, AP_LLM_TIMEOUT_SECONDS;
      a page read's next word within `HANG_SECONDS`, 45 s, on a primary with a backup, instead of its 20 minutes), or
      its model list not given when asked afresh (a status kept 10 s is one failure, `local_llm.unlisted`: asked by
      `page_reader.reader_status`, and by `InvoiceCoder._prepare_local` when it finds no model, so a chat model on a
      server of its own moves too, B-1869). A 4xx is an answer (a model it doesn't have, a field refused): a swap
      moves nothing. A slow answer within its limit is no failure: the backup is the smaller machine.
    - **Only the server's own time counts:** an arrival that waited longer than `WAITED_SECONDS` (1 s) for its chat
      slot (another of AP Coder's calls held it) was given what was left of its 20 s: no answer then isn't counted,
      and with less than `SEND_SECONDS` (5 s) left it isn't sent at all, put off as an arrival out of time (B-1870).
      A call with no answer in time while another of this process's calls to that server was under way since before
      it was sent may have waited in the server's own line (one GPU for both models): no hang (`Failover.behind`,
      B-1871).
    - **But the minute holds** (the lead's ruling, SCALE.md's target): a call ahead stands for AP Coder's own line
      only while it has been under way less than the hang limit (`HANG_SECONDS`, 45 s, or the shorter call limit in
      play, AP_LLM_TIMEOUT_SECONDS). Silent past it, the server isn't answering it, and a timeout behind it counts.
      The same for a slot: `Slots` notes since when each one is held (this process's in memory, the other processes'
      by the time set on the slot's lock file when it is taken), and an arrival whose slot was held by a call silent
      past the hang limit counts, sent or not (`SlotTimeout.behind`, `SlotWait.behind`). A page read waits the whole
      hang limit itself, so whatever it waited behind was silent past it: every page read's hang counts. So a hang
      that starts while two reviews hold the chat slots is found by the page reads (and the arrivals) once the reviews
      are 45 s silent: 46 s at the app's limits (`test_failover.py`'s review-held hang, the limits scaled 1:20, the
      reviewer's 5 minutes kept), where before the cap it waited for the reviews' own 5 minutes. The
      price: a primary only busy with a call silent past 45 s (a review of minutes on a server that answers one
      thing at a time) is taken for hung, and the calls move (`busy_primary_moves.py` of the review moves again); a
      call ahead within the limit (an arrival behind a review a few seconds old) still moves nothing. A read cut off
      at the hang limit still counts a try of its own.
    - **Drops the calls under way on the primary** (`Failover.watched`: the call in a thread of its own, its caller
      looking at the breaker every 0.25 s): `ServerLeft`, and the call is asked once more on the backup when that has
      the same model (`InvoiceCoder._local_create`, the arrival only within its deadline; `page_reader.transcribe`).
      An answer that came just as the calls moved is taken (the second kill drill read a page twice before). A read
      or review that fails all the same because the calls moved goes back in line at once, its try not counted
      (`page_worker._postpone_or_fail(moved)`, `verifier._postpone_or_fail(moved)`), and a reader thread pausing
      after a failed read wakes when they move.
    - **Takes the primary back** after `PROBES_TO_RETURN` (3) answers in a row to a one-word question asked every
      `PROBE_SECONDS` (5 s; `local_llm.probe`: one of its models, no slot taken, `PROBE_TIMEOUT` 10 s, one process at a
      time), once the calls have been on the backup `hold` seconds: `HOLD_SECONDS` (60 s), twice as long each time it
      fails again within `FLAP_SECONDS` (10 minutes) of being taken back, at most 30 minutes; at once when the backup
      fails too: two of its calls, or its model list not given while the calls are on it (both down, nothing else is
      asked of it: B-1872). Calls under way on the backup end there.
    - **Shared** by the company server's processes, as the minute needs (measured below): a small JSON file in
      `AP_LLM_SLOTS_DIR` (the slot locks' folder `serve --server` gives them all), written under its lock
      (`paths.try_lock`), read at most every 0.5 s (an answer reads it afresh, so another process's failure a moment
      before is reset: B-1876); times on the server's clock (one machine). Not the database: the
      model calls have no store at hand (`local_llm` opens none), and it is no row's state. `AP_FAILOVER_SHARED=0`
      gives each process its own (to measure). On a laptop, each process its own.
    - **D-168's arrival breaker** counts for the server in use (`ArrivalBreaker.asks(server)`): its two timeouts in a
      row move the calls first, and it counts afresh on the backup, so arrivals stop asking only when neither answers.
  - **Which one answers, and how** (item 4): `page_reader.server_checks` lists each model's server and its backup: the
    model it would use there, then (Check again, the doctor) a one-word question, timed. *Settings → Reading*: a card
    "Model servers" (in use or standing by, how each answered at the last Check again, where the breaker sends the
    calls and why); `python -m ap_coder doctor`: a line for each server and one for the breaker. Run from a console
    on the company server it takes the server's own (`doctor.as_the_server`): server.json's backups and the state its
    processes share, in the data folder's `llm` folder, where `serve --server` keeps it (B-1874). Without a backup:
    neither, as before.
  - **A swapped model** (item 5): the page reader's self-test was already its model's (`confirmed(store, model)`):
    another OvisOCR2 build reads nothing until it passed, on whichever server it is; the backup's with the same key is
    trusted on the primary's test (the same model file, LM Studio's key says). The chat model:
    `page_worker.CHAT_TEST_KEY` notes the first chat model AP Coder runs with (the one it was qualified with); any
    other (swapped in LM Studio, or the backup's own) reads the page reader's test invoice's header from the same
    scanned pages (`inference.read_header`, compared by `page_reader._verdict`) before it decides anything
    touchless: `chat_test_if_due`, in the first reader's round as `self_test_if_due`. Meanwhile the AI reviewer waits
    (`verify_one` takes nothing), its agreement approves nothing (`_fold_in`), nor does a reading (`_read_taken`,
    `chat_again`), nor the Azure check's agreement on an invoice whose header it read (the page reader's pass keeps
    that model in the invoice's `meta.page_reader.chat_model`, `page_worker.header_untrusted`; with the AI reviewer
    off the pass holds the invoice for Azure and asked nothing: B-1877). The trust asked is that of the model that
    read the header, not of the one in use when it decides (LM Studio may swap it between): `_read_taken` and
    `chat_again` ask `PipelineResult.header_model`'s (`chat_again` reads no header: its accounts go to a person
    anyway), and the reviewer's agreement needs both its own model and that one trusted (B-1878). The Reading tab's
    reviewer card says why
    ("The AI reviewer waits: the chat model is now …"); the test is audited (`chat_model_tested`). This holds on a
    laptop too: a swapped model is as risky there. One that
    can't see pages (`page_worker.chat_sees_pages`) decides nothing touchless: it reads no header, reviews nothing
    (the reviewer says it needs one that can) and its accounts always go to a person; it is neither kept back nor
    tested (it could never pass, and was tested again, audited, every hour: B-1875).
  - **Measured** (`python -m ap_coder.bench day --backup --drill …`: a second stand-in model server as the backup,
    the same models and latencies, the drill on the primary; 40 invoices, half scans, the model 8-20 s a page × 0.05,
    2 slots, 2 web workers, 2 capture processes; the 4 cores shared with three other agents' work; real seconds, the
    app's limits unscaled but AP_LLM_TIMEOUT_SECONDS 30 s and the page reader's 60 s):

    | drill on the primary | to the backup (pages / GL / reviews) | back | lost | read twice | stored twice |
    |---|---|---:|---:|---:|---:|
    | kill, 60 s | 0.9 s (3.3 / 2.2 / 0.9 s) | 14 s | 0 | 0 | 0 |
    | hang, 60 s | 22.7 s (23.5 / 22.7 / 26.6 s) | 28 s | 0 | 0 | 0 |
    | slow, answers ×100 (40-100 s), 90 s | 29.2 s (29.2 / 34.0 / 31.0 s) | 49 s | 0 | 1 | 0 |
    | swap (another chat model) | none: a 404 is an answer | — | 0 | 0 | 0 |
    | hang, each process its own breaker | 49.1 s (49.1 / 50.3 / 49.6 s) | 51 s | 0 | 0 | 0 |

    To the backup: its first answer after the drill began (the hang's 2 s after the first request found hung, the
    unshared hang's 16 s); back: the primary's first answer to a call asked after the drill ended; lost: reads failed
    or never read; read twice: pages the two model servers answered twice; stored twice: readings or reviews folded
    in twice. 0 failures, 0 imports failed and every review done in every run.

    - **Sharing** halves a hang's failover (49 → 23 s) and its return (51 → 28 s): alone, each process finds it
      by its own calls (the capture processes two arrivals each, 40 s; the background process its page reads' 45 s,
      or its reviews' minutes had it no page read under way); shared, the first two timeouts anywhere move them all.
      The arrivals during the drop: 3.7 min alone, 2.6 min shared.
    - **The slow primary:** a call dropped when the calls moved is left to end on its own: on a hung primary it never
      does, on a slow one it may still be answered within its limit, to nobody (the one page answered twice, its
      reading stored once). Its return waits for those calls to leave the primary's slots: the probes, asked behind
      them, go unanswered until then (49 s; three runs: 0, 1 and 1 page answered twice, 0 stored twice). The
      stand-in model server now drops an answer whose client gave up (LM Studio stops), so it no longer counts
      those as answered.
    - **The swap:** nothing moves (a model not found is an answer); after it, 6 → 3 requests asked for the old chat
      model and 2 → 0 of 40 reviews unsure with B-1844 (the status asked again at once); the new model answered
      after 2.5 s and was trusted after 7.6 s (its self-test: 9 of 9 fields, 3.1 s), the AI reviewer waiting meanwhile;
      all 40 agreed.
    - The first runs found three of this change's gaps, fixed before the rows above: the page reader on its own
      breaker never moved off a killed primary (one breaker a server, the model list counted); an answer that came
      as the calls moved was asked again (taken now); two of 40 reviews unsure after the swap (B-1844).
    - **Nothing else changed:** `scripts/same_behaviour.py --ref 5aa6014 read pipeline` (Python 3.12, both OCR
      models): the same (60 read outputs, 1 pipeline output).
    - **Measured again after the review's fixes** (B-1869 to B-1878; the same runs, one machine shared with other
      agents' work): kill 1.1 s to the backup (3.8 / 4.7 / 1.1 s), back 14 s; hang 30.6 and 30.8 s (31.6 / 31.6 /
      30.6 s), back 37 and 36 s, the code before on the same machine that day 21 s and 25 s; slow ×100 for 90 s
      30.9 s (33.6 / 32.1 / 32.6 s), back 20 s; swap: nothing moved, 1 request for the old model after it, the new
      one answered after 2 s and trusted after 4 s. Every run: 0 lost, 0 read twice, 0 stored twice, 0 failures,
      40 of 40 reviews done. `same_behaviour.py --ref cfb45d6 read pipeline`: the same (60 read outputs, 1 pipeline
      output).
    - **With the minute's cap**, the AI reviewer running (`--reviewer all`): hang 31.6 s to the backup (32.7 / 31.6 /
      32.0 s), back 37 s, with the bench's scaled limits (its reviews give up at 30 s, the cap there too: no gain to
      show); at the app's own limits (`--no-scale-timeouts`, a review's call up to its 5 minutes) 20.9 s (21.9 /
      21.8 / 20.9 s), back 27 s: one review hung there, and two arrivals that had their slots at once moved it. Two
      reviews holding both slots is the test's case: 46 s at the app's limits, 300 s before the cap. 0 lost, 0 read
      twice, 0 stored twice, 0 failures in both.
  - **Not done:**
    - A call dropped on the primary is not cancelled there: a slow primary finishes it for nobody (above).
    - The capture processes take a backup address saved on the Reading page at their next start (their pipeline is
      made once, `intake.imports.Reader`), as they take a new main address; the page reader and the reviewer at once.
    - The backup's own models (another chat model, another OvisOCR2 key) are tested when the calls first go there,
      not before: the AI reviewer, or the page reader, waits for that test then.
    - The chat model's self-test reads the header: a reviewer that agrees with a wrong total (the digest's item 12)
      is not tested.
    - A hung chat model on a server of its own is found by the arrivals' 20 s, or else by a call's own limit (the
      reviewer's minutes) while nothing arrives.
    - At full latency the drills are estimates of the bench's own: the limits (20 s, 45 s, the probes) are real time
      and the model's answers ×0.05, so a hang is found sooner relative to the work than on the Mac Studio.
  - (`ai/{local_llm,inference,page_reader,page_worker,verifier,pipeline,azure_check}.py`, `core/{config,audit}.py`,
    `ops/{doctor,service,cli}.py`, `web/pages/{settings_reading,activity}.py`, `web/roles.py`,
    `web/templates/settings_reading.html`, `bench/{day,fake_model}.py`, `docs/{REFERENCE,SERVER,HOW_IT_RUNS}.md`;
    `tests/ai/test_failover.py`, `tests/ai/test_page_reader.py`, `tests/web/test_reading_pages.py`,
    `tests/ops/test_service.py`, `tests/bench/test_day.py`)
- **D-181** · **The JD Edwards sync at the company's size: what changed each night and everything once a week, each
  page written as it comes in a transaction of its own and resumed where it stopped, JD Edwards' vouchers kept by
  their key in the register, one connection kept open** (scale goal phase 4, target 4's sync half; B-1850 to B-1856).
  - **Why:** the digest ([coding_erp.md](archive/scale-2026-11/coding_erp.md) §2b, §5 R7 and R9), checked in the code:
    every part read whole every night (no ChangedSince sent; the vouchers of 730 days), at most 200 pages a part
    (100,000 rows: 600,000 vouchers failed every night), nothing kept of where a read stopped, a new TCP and TLS
    connection for each request, each part written in one transaction after its last page, the PO sync three or four
    queries a PO, voided vouchers never removed, a renamed supplier's vouchers kept under its old name for good, a
    split PO line's number cut. Not reproduced: "no index on `vendors.erp_id`, 52 s for 30,000 suppliers": the index
    has been there since B-1581 (schema 20); 30,000 suppliers took 1.9 s, the write lock 0.44 to 0.53 s at a time.
  - **What:**
    - **What changed, each night.** Each part keeps in the sync's state (`parts`) when its last good read began, when
      it was last read whole, and what it reads (its scope: the company, the PO order type, the vouchers' days). The
      next asks `ChangedSince` = that day less one (the two servers' clocks, a time zone); the vouchers: what changed
      in the `voucher_days` window, then every voucher of the last 31 days (`RECENT_DAYS`). A part is read whole once
      a week (`FULL_DAYS`), the first time, when its scope changes and when asked (`jde sync --full`, Settings' *Read
      everything again*); the business units always (a short list). The mapping file's GL, PO and voucher reads take
      `ChangedSince` (the suppliers' had it); an orchestration that ignores it still works, everything every night.
    - **What only a whole read can say.** A PO no longer listed is closed only after a whole read (a changed one
      merges its lines by number, the changed lines alone or the whole PO: both work; a PO's lines here are its open
      ones, as a whole read lists them: a closed line, 999, leaves while another is open, and the last one closed
      closes the PO). A voucher leaves the register when a complete read of its dates no longer lists it: those of
      the last 31 days each night, the rest at the week's whole read, which also removes those without a date (none,
      or one that can't be read) it no longer lists; a read's first day is left alone, whether JDE's FromInvoiceDate
      includes it or not. Either refused when most or all at once (a wrong answer: a filter, row security), as
      D-105's mass close, with `--allow-mass-close`. Nothing is removed before a read's last page: marked, not
      staged and swapped (each voucher read is real and in the register as it comes; a half-read register is never
      short of one).
    - **A read is complete** when its last page is empty or shorter than the longest before it (what AIS gives:
      its own maximum rows may be below the page size). So the reads that remove or close (the vouchers' sweeping
      reads, the POs' whole read) ask again after a page as long as the longest so far (`pages(to_the_end=True)`:
      one request more for a read of one page), and one that ends otherwise (its last row without its key: nothing
      to ask the next page after) removes and closes nothing, said in the report. Kept with the read's progress.
    - **Every page, as it comes.** `AisClient.pages` yields each page with the key to ask the next after; no ceiling
      (a page ending on the key asked after raises, never a loop); `rows` keeps its `max_pages` for the live check.
      Each page's rows, where the read is (`jde_ais_sync_progress`: the reads planned, the step, the last key written,
      the report so far, each read's stamp) and the lease are one transaction; a stopped read goes on after that key
      (a whole read left is taken whatever is due: it reads the changes too; begun less than 3 days ago). A PO's lines
      are held to the next page so each PO is written whole. One database connection for a part (closing the last
      one writes the log into the database: 40 ms a page). One sync at a time (lease `jde_sync`, renewed each page,
      15 minutes): another is refused at once, nothing saved; one that can't renew it (it waited on JDE longer and
      another took over) stops there, the page undone (renewed last in its transaction: the write lock is taken at
      the page's first write, as before; renewed first, the lock waits went from 0.05 to 1.7 s), the sync's state
      left to the other. A voucher keeps the newest read that listed it (`MAX(seen)`), so an older read writing late
      never lets a newer one's sweep remove it.
    - **The register** (`core/store/register.py`, moved out of `invoices.py`, 994 lines): JD Edwards' vouchers by
      their key (`erp_vouchers`, made at every start, no schema version: an older AP Coder leaves it be) under their
      bill (`erp_invoices`, what every check reads); a bill's copies are its vouchers (each by its key: one invoice
      entered twice in JDE is two, whatever their dates; a file import still counts a bill's dates); a voucher whose
      supplier was renamed or gross corrected moves to its new bill, the old going with its last voucher; each bill
      keeps its supplier's number (`an8`), and `in_erp` finds a bill by the vendor's ERP ID too, so a renamed
      supplier's vouchers are found before the week's read moves them. Its dates are read in the mapping's
      `date_format`, as the dates sent. Cleared or replaced (Exports), the register keeps when
      (`erp_register_cleared`), and the vouchers' next read after that is whole, one under way then not resumed.
    - **Suppliers a page at a time:** `import_vendor_master(by_erp_id=True)` takes the vendors already there as
      earlier rows of the same file: a row goes to the vendor of its name holding its AN8 ("Acme #4002", B-930), and
      another AN8's row of a vendor's name is merged into it or is the second vendor, as in one file.
    - **POs:** two queries a page for the POs before and after (`purchase_orders_by_key`), not three or four a PO;
      R9: a whole LNID as it is (1.000 → 1), a split line's in JDE's own thousandths (1.500 → 1500), so no two lines
      of a PO share a number and none after it is renumbered (`coding/po.py` matches whole numbers: kept so).
    - **GL accounts:** an object.subsidiary's description is its first account's in a whole read; in a changes read
      the changed account's (another business unit's may differ: the week's whole read puts the first back). The
      account mapping rows are saved with their page; one `settings_changed` logged for the part.
    - **Events:** one import event a part (`vendors_imported`, `accounts_imported`, `pos_imported`,
      `erp_register_imported`), as its CSV import logs, not one a page; none on a night that read nothing.
    - **One connection kept open** (`jde_ais`): a client keeps up to 4 open (one for each thread asking at once:
      the live check's client is shared), TLS as before; one AIS closed meanwhile is seen before it is used (readable
      while no answer is awaited), and one unused for 30 seconds (`IDLE_SECONDS`) is not used again (a firewall's idle
      timeout or a VPN reconnecting drops it silently, no FIN: a request sent into it would wait its whole timeout);
      a read that finds it closed as it is sent is asked again once on a new one, a voucher's never (it is
      "unknown", looked up by its key, as before); after a timeout or any failure the connection is closed. One
      signed-in client per worker for the live check (B-1603), one per sync. An orchestration that gives the same
      page again (`PageRepeated`) fails the sync's read, and is the live check's question too wide, as too many
      pages: asked by the invoice number, logged, the check left on.
  - **Measured** on the stand-in (`tests/erp/test_jde_sync_volume.py`, 4 cores shared with three other agents; the
    wait is a tiny write's from another connection every 20 ms, for the lock alone):

    | | took | longest wait for the write lock | read |
    |---|---:|---:|---|
    | first sync: 30,000 suppliers, 600,000 vouchers (1,260 pages) | 48.7 s | 0.11 s | everything |
    | next night: 50 suppliers changed, 1,000 vouchers new, 50 voided | 5.2 s | 0.04 s | 50 + 28,568 vouchers |
    | the week's whole read, AIS down at the vouchers' page 600 | 26.9 s | 0.05 s | 600 pages |
    | the next sync, going on from there | 24.7 s | 0.05 s | 602 pages (the 20 older removed) |

    The next night read 50 suppliers and 28,568 vouchers (the 1,000 new and the last 31 days'), and removed the 30
    voided this month; the 20 older went at the whole read. The register equalled the stand-in's after each.
    Before: 600,000 vouchers failed at page 200 every night. 30,000 suppliers: 1.9 s, a write held 0.44 to 0.53 s;
    now 2.4 s, 0.04 s. 30,000 open PO lines (10,000 POs): 22.3 s the first sync, 37.6 s the next; now 1.2 s, 1.5 s.
    After its review (B-1893 to B-1904), the same four: 50.3 s, 4.7 s, 27.5 s and 25.0 s, the longest wait for the
    write lock 0.06, 0.03, 0.04 and 0.13 s, the same reads, 30 then 20 removed, the register right after each.
  - **To confirm with the JDE team** (docs/JDE_ORCHESTRATIONS.md, items 3, 4, 21 and 22): the update dates
    `ChangedSince` covers on each read (a supplier's bank account, a receipt, a voucher paid or voided), the PO read
    listing the lines closed since, each read's order by its key kept stable, every row given its key, whether
    `FromInvoiceDate` includes its day, the output dates in `date_format`, AIS's maximum rows not below the page size
    (else each read that removes nothing stops at its first page), and AIS (and a load balancer) keeping a
    connection open, and how long unused. Left: a voucher entered with an invoice date older than the window is
    never read (as before); the vendor's key is its name, so a renamed supplier is a second vendor in AP Coder (as
    before: only its register is found by number); a bill a file import put in the register, or a sync before this
    version (no voucher key kept for it), is never removed by the sync unless JDE lists it again: to start clean,
    clear the register (Exports), and the next sync reads the vouchers whole; a connection dropped silently within
    30 seconds of its last use still costs the request its timeout.
  - **Amended** after its review (B-1893 to B-1904): the register cleared or replaced read whole again; dates read
    in `date_format`; undated vouchers swept by the whole read; a read's first day left alone; a PO's lines its open
    ones; a read complete only on an empty or shorter page; the lease checked each page, `MAX(seen)`; a bill's
    copies its vouchers; a connection unused 30 seconds not used again; `PageRepeated` for the live check.
  - **Amended** (7 Nov 2026, phase 8 part 2): another writer beside the sync (an approval during the nightly read)
    waits as long as the sync's write under way, and longer when SQLite's busy timeout, retrying after 1, 2, 5, 10
    ms... then every 100 ms, misses the moments between its short writes (each 7-40 ms here): its single longest
    wait swings run to run (0.1-1.4 s here and on CI) while its p99 stays 0.02-0.08 s. The volume test asserts the
    p99 under 0.5 s and the longest under 3 s in each of the four syncs (it asserted the longest under 0.5 s, which
    held by chance). Leaving the lock free between the writes (pausing as long as each held it) was tried: 0.034 s
    at most on Linux, but on Windows, where each write holds longer, the volume run took 1,957 s for 785 s and still
    waited 1.4 s, so it was taken out. Left: an approval during the nightly sync can wait about a second, rarely.
    Committing the sync's writes without waiting for the disk (`synchronous` NORMAL on its connection) was tried
    too, under the app's own FULL: a writer's p99 wait beside the sync 54-80 → 14-24 ms, but each approval's own
    commit then writes the sync's log out (+1-2 ms at the median) and takes the disk's stalls itself: 2.36 s once,
    against at most 0.63 s for approvals without it. It was not kept.
  - (`erp/jde_sync.py`, `erp/jde_ais.py`, `erp/jde_orchestrations.json`, `core/store/{register,invoices,vendors,
    purchasing,_base,__init__}.py`, `ops/jde_cmd.py`, `web/pages/settings_jde.py`, `web/templates/settings_jde.html`,
    `docs/JDE_ORCHESTRATIONS.md`, `docs/JDE_E1.md`; `tests/fake_ais.py`, `tests/erp/test_jde_sync_scale.py`,
    `tests/erp/test_jde_sync_volume.py`, `tests/erp/test_jde_ais.py`, `tests/erp/test_jde_sync{,_safety}.py`,
    `tests/erp/test_jde_api_web.py`, `tests/core/test_store_perf.py`)
- **D-182** · **Posting through the JD Edwards API at 500 vouchers: each voucher its own outcome and the run goes on
  past a late answer; a lost answer sent again only once JD Edwards could have finished it; holds and the open period
  read just before each voucher; Z-table keys unique per data folder and after a restore; accruals follow the postings**
  (scale goal phase 4, target 4; B-1857 to B-1863, and the review's B-1880 to B-1891; changes D-105 where it says
  otherwise).
  - **Why:** one orchestration call per voucher, R04110ZA in each, against a 30-second timeout: the first late answer
    closed the client and queued the rest, so a batch went out one voucher per five minutes (500 ≈ 40 hours). Holds
    were not looked at after approval (R3), there was no fiscal period (R5), VLEDBT / VLEDTN collided across data
    folders and after a restore (R6), and month end counted an API voucher not posted as in the ERP (R4).
  - **What:**
    - **A run** (`jde_post._run`, `_Run`): the lookups first (each row JDE has or may have, by its key), then the
      sends one after another, then the run's late ones once their answer is `LOOK_AFTER` (30 s) old. A late answer
      (the request reached JDE: a timeout, the connection cut, 502 / 504, an answer not read) is *unknown* and the
      run goes on; JDE not reached (refused connection, 503 after the retries, the sign-in or the permission refused)
      or `LATE_STOP` (3) late answers in a row stop it, the rest queued saying why; so does the run's time
      (`post_minutes`, 60). A voucher's request waits `voucher_timeout` (120 s), the connection's 30 s stays for the
      reads and lookups. The exports page's job shows done of total (one left as it changed meanwhile is done too,
      B-1891), posted, being checked, refused, waiting, held.
      A run cut short: each voucher of a go-ahead has when its run is over at the latest as its next try
      (`_run_over`: the run's time, the last send it began, a margin), and one not sent by then (AP Coder stopped)
      is the queue's (`waiting`); stopped by an error, the run queues the rest at once. A dry run since the go-ahead
      takes that off: a new go-ahead sends it (B-1886).
    - **Never twice:** a lookup's "not there" for a send whose answer was lost is believed only `resend_minutes` (10)
      after the answer was lost (`lost_at`; a send whose end is not known, from its start and the longest it could
      take), never sooner than a voucher's wait and `RESEND_MARGIN` (a minute): Settings refuses less, and a run takes
      the larger (`JdeSettings.resend_seconds`, B-1880). Before, the row stays unknown and locked (B-1858). A row
      *sending* is someone's live send, never looked up nor sent by another, for the longest a send can take
      (`send_seconds`: each try a voucher's wait, AIS's 503 retries and their pauses, the sign-in, twice over for a
      token expired meanwhile; 15 minutes at least, B-1881). Both waits are kept and compared in UTC (`sending_at`,
      `lost_at` with their offset; an older row's local time read as local), which the clocks going forward don't
      move (B-1887). A refusal that JDE answered (its error shape) is looked up and believed at once, as before; only
      a form error with JD Edwards' code is that shape (`jde_ais.jde_refused`): AIS's own failure in JSON (its
      orchestration timeout: HTTP 500, a message) is an answer lost, unknown (B-1890; the one change to `jde_ais.py`,
      J1's file, is that classification).
    - **Holds** (`coding.vendors.voucher_hold`): AP Coder's own and JD Edwards' as the last supplier sync left it,
      indexed until the vendor list changes; held, the voucher is queued saying why, the dry run refuses it.
    - **The open period** (`open_from`, `open_to`, `closed_period` in Settings → JD Edwards E1; none read from JDE:
      no orchestration offers it yet): a G/L date before it is moved to its first day (`JDE_GL_DATE_MOVED`, a
      warning on the review screen; the dry run's row says it; the go-ahead's question names it and its event keeps
      `gl_moved`) or held (`JDE_PERIOD_CLOSED`); after it, held. The same G/L date goes in the files. Just before
      each voucher the period is read again (a period closed mid-batch: the rest back to *checked*, dry run again);
      JDE refusing a G/L date as closed (`PERIOD_REFUSED`: PBCO, "closed period"…) holds back the run's others of
      that month and before. Month end: with the open period starting after the period end and closed periods
      moved, a batch made after the period end is late (`accruals.late_after`, as with the export day as the G/L
      date: its invoices of the period go with a G/L date after it, so they are accrued, B-1883).
    - **Z-table keys** (`jde.batch_number`, `invoice_site`, `batch_site`): VLEDTN = `APC` + invoice id + `-` + 8
      characters of a digest of the invoice's file and when it was added (the identity a restore uses, D-105);
      VLEDBT = prefix + batch + `-` + up to 4 characters of a digest of the batch (when made, its number, its
      invoices) as room allows in 15. Deterministic: the same each time a voucher is sent or its batch downloaded,
      so the files and the API agree; another invoice or batch under the same number gets others. An upgrade changes
      the keys of what is not sent yet: a dry run passed before it is "changed since its dry run" (dry run again),
      and a voucher approved and waiting in the queue that JD Edwards hasn't (or any changed since its go-ahead) is
      held back for a new dry run, its go-ahead kept for month end, *Not sent: dry run again* on the batch's panel,
      not *Refused by JD Edwards* (B-1888).
    - **Accruals** (`jde_post.not_posted` in `accruals._not_in_erp`): queued, being sent, unknown, held back after its
      go-ahead, or refused, by JDE or by a dry run since its go-ahead (B-1885), unless the batch's files were
      downloaded since, is not in the ERP; the kept answer's question carries that set, as postings change no
      invoice. A download of the batch's files takes the go-ahead off every voucher they hold (not locked: queued,
      held back, refused), so one loaded from them is never counted as not in the ERP afterwards, a dry run again or
      not (B-1882).
    - **Lookups, not scans** (B-1863): `jde.account_for` by (GL code, cost center), `held_vendor` by what makes a
      number or account the same.
    - **One rule for a tax's shares** (B-1889): a pay item per line (`MATCH_HEADER`), the tax not on G/L lines, takes
      its share of each recoverable tax and of the taxable amount by `money.split`'s rule (each half up, what is
      left over on the largest), the shares the lines have (`tax.tax_shares`), where `jde._split` truncated each and
      put the rest on the largest. Changed outputs, the totals and the G/L lines the same: two lines of 10.10 with
      GST 1.01, VLSTAM 51 / 50 → 50 / 51; of the 19 sample vouchers in that layout (tax and gross amounts), the
      two of Quebec (GST and QST each split): SIL-4471 VLSTAM 17971 / 5091 / 12878 → 17969 / 5092 / 12879,
      ACMR-2026-1187 lines 2, 3 and 5 31447 / 52412 / 71133 → 31448 / 52413 / 71131 (VLAG moves by the same cents
      with gross amounts); in whole yen, 3 over two equal lines 2 / 1 → 1 / 2. Neither `scripts/same_behaviour.py`'s
      tax nor its approve probe makes pay items.
  - **Not done, for the JDE team** (docs/JDE_ORCHESTRATIONS.md, items 15 to 20): the batch-level orchestration (one
    R04110ZA per batch) and a status lookup of many keys at once; R04110ZA's error IDs for a closed period; whether
    VLPST `A` overrides a supplier's hold; how long a voucher request can still run after AIS gave up (the 10
    minutes); a JDE batch still pending (F0011 ICIST / F0411 RPPOST) is not known to AP Coder, so a voucher posted
    into a pending batch counts as in the ERP.
  - **Measured:** 500 vouchers through the stand-in (each answered after 20 ms, one in ten late past a 0.3 s wait,
    half of them made, one in twenty refused): one go-ahead run of 40 s ends 475 posted and 25 refused, none unknown
    or queued, none made twice (525 sends, 75 lookups); the code before stops at the 8th (late), the other 492 queued.
    With the resend wait counted from the loss (B-1880) the run of 38 s ends 474 posted, 25 refused and 1 being
    checked (lost within the 3 s of its end), which the queue 3.5 s after posts: the same 475, 525 sends, 75 lookups.
    The queue building a batch once (B-1884): 500 waiting 84 s → 0.2 s before the first send.
    `account_for` × 5,000 with a 20,000-row mapping: 12.8 s → 0.04 s; `held_vendor` with 3,000 on hold: 46 → 14 ms.
  - (`erp/{jde_post,jde,accruals}.py`, `coding/vendors.py`, `web/pages/{exports,settings_jde}.py`,
    `web/templates/{exports,settings_jde}.html`, `ops/help.py`, `docs/{JDE_ORCHESTRATIONS,JDE_E1,BUGS}.md`;
    `tests/erp/test_jde_post_runs.py`, `tests/fake_ais.py` and the tests named in B-1857 to B-1863; the review's
    fixes also `erp/jde_ais.py` (`jde_refused` only), `core/store/postings.py` (`lost_at`), `coding/tax.py` (a
    docstring) and the tests named in B-1880 to B-1891)
- **D-183** · **One money arithmetic, half up on the decimals printed, and one table of what counts as equal** (scale
  goal, phase 4, T1; R10 and G9 of `docs/archive/scale-2026-11/coding_erp.md`; B-1864 to B-1868).
  - **Why:** Python's `round` on a float is half-even on its binary value: 2.50 × 5% gave 0.12 and 0.30 × 5% gave
    0.01, where the vendor and JD Edwards (`erp.jde.jde_amount`, Decimal half up) give 0.13 and 0.02. Of the bases
    0.01 to 2,000.00, 1.1% at 5%, 1.5% at 6% and 3.5% at 15% came out a cent short. The checks' cent of tolerance
    hid it; it landed in money: the self-assessment estimate AP books from, the Tax check's amount to self-assess,
    the early-payment discount, a line split by percent, the PST spread onto the lines. And what counts as equal was
    written in each check apart: the review screen's Tax check and the findings said different things of one sum
    (N-07, N-40, the notes' verified 0.02 total).
  - **What** (`coding/money.py`): `half_up`, `times` (an amount × a rate or a share), `add_up`, `split` (each share
    half up, what is left over on the largest weight: `tax._allocate`'s rule, now on decimals) and `in_cents`, on
    `Decimal(str(float))` with ROUND_HALF_UP (half away from zero: a credit note rounds as the invoice it reverses),
    to the cent or to whole units (`places` 0, the yen). The quick way gives the same answer: a float more than
    0.4999 of a unit from a tie, under 1e11 units (a billion dollars, its error after a product or an `fsum` under
    1e-5 of a unit), is rounded as a float; a tie or what could be one in decimals
    (`test_the_quick_way_is_the_decimals_answer_always`: ties, third decimals, credits, yen, 1e12, the sign of a
    nothing).
    - Used by `tax.check_taxes`, `build_gl_distribution`, `tax_shares`, `meals_parts`; `taxmath.math_check`;
      `validation` (the totals, the posting, `off_text`, `prices_include_tax`); `line_tax` (the solver's cents, its
      tolerance `money.solver_cents` (was `line_tax.tolerance_cents`), `_tax_base`, `can_explain`, prefill's target);
      `taxreturn.self_assessment` (the base, and the estimate in the currency's units, B-1868); `terms.payment` (the
      discount); `review.split_line` (a part by percent, the last part's remainder); `erp/accruals` (received, not
      invoiced: 3 × 2.675 was 8.02, now 8.03, B-1912).
    - Float `round` kept where no money is worked out or converting would cost more than it changes: rates
      (`round(rate, 6)`, the % shown), confidences, quantities and unit prices at 6 places, the PO match (`po.py`,
      compared within its own tolerance), the reports' running totals (`taxreturn.Report.totals`, `by_type`,
      `meals_totals`: sums of amounts at the cent, the same either way), and the facts kept in the invoices' summary
      (`tax_facts`, `meals_by_tax_line`, `self_assessed_base`: sums of amounts printed to the cent, different only
      for an amount with a third decimal at a tie, while a change there moves `SUMMARY_RULES` and builds every
      database's summary again at its next start, 86 s at 100,000 invoices: left for the next change that moves it).
  - **What counts as equal** (`money`, old → new):
    - the float error of a difference (`within`): a billionth beyond the tolerance → a billionth and 2e-15 of the
      larger amount (B-1906, the review): two amounts printed to the cent a cent apart differ in floats by a cent and
      up to 2e-9 from 2^23 dollars (8,388,608) on, so CENT refused the cent it is for (131 of 300 generated invoices
      of 8 to 60 million newly TOTAL_MISMATCH, hard). Not each difference rounded to the cent, which would also move
      the half cents, each by the float it lands on: a total read 113.005 is within EXACT of a posting of 113.01 and
      of 113.00, and would be a cent from the one (POSTING_UNBALANCED) and none from the other; LINE_MATH's 0.5%
      would move by up to half a cent. The float error is a part of the amounts' size: at most 2.5 units in the last
      place of the larger (an amount, a sum of two, the subtraction);
    - nothing: `> 0.004`, `>= 0.005`, `< 0.005`, `<= 0.004` → `NIL` 0.005 (`nil`; the same at the cent);
    - the GL posting against the total: `> 0.005` → `EXACT` 0.005 (the same);
    - subtotal + tax = total: TOTAL_MISMATCH 0.02, the Tax check 0.015 → `CENT` 0.01 (B-1865); the tax lines = the
      tax total: TAX_LINES_TOTAL_MISMATCH 0.02 → `CENT` (B-1865); the lines = the total when prices include the
      tax: 0.011 (validation), 0.015 (the Tax check's hint) → `CENT` (the same at the cent). One sum of amounts
      printed rounded: a vendor who adds before rounding is at most a cent off (two or three taxes included);
    - and a total paid in cash, rounded to the nickel (B-1907, the review): TOTAL_MISMATCH and the Tax check's
      total row take a total that is a multiple of 5 cents, at most `CASH_ROUNDING` 0.02 from subtotal + tax (the
      nickel nearest it), when a line is that rounding (`cash_rounded`). Why this and not 0.02 again ("nothing
      worse", and what the review screen means), counted with the review's `runner.py`/`compare.py`, cb08074
      against this: of 300 generated cash receipts, their rounding entered as a line so that the posting balances,
      117 were blocked at a cent, none now (none on cb08074). Of 300 invoices with one amount read a cent or two
      off, 57 are blocked that cb08074 passed, and stay so: the subtotal (30) or the tax total (27) read 2 cents off,
      the lines and the posting right. That is a header amount misread, not a good invoice's coding: TOTAL_MISMATCH
      names the sum, one field puts it right, and the Tax check now shows the same ✕ (cb08074 showed the subtotal's
      beside no finding, the tax total's not at all). 0.02 again would pass those 57 and bring back B-1865's ✕
      beside no finding. The rounding line is asked for: without it a subtotal read 2 cents off whose total is a
      multiple of 5 cents (one in five) would pass, and a cash receipt without its rounding line can't post anyway
      (POSTING_UNBALANCED, hard on cb08074 too). Any currency with cents, as cb08074 passed any 2 cents;
    - the Tax check's total row adds the tax total, the sum TOTAL_MISMATCH adds (it added the tax lines: a tax total
      read 2 cents off was a hard TOTAL_MISMATCH beside an all-✓ Tax check, 27 of the 300), and is ✕ too when the
      tax lines are not the tax total (TAX_LINES_TOTAL_MISMATCH's `CENT`), its hint saying which sum is off and its
      difference the total less subtotal + the tax lines, as before (a tax line read 2 cents off keeps its ✕ there:
      6 of the 300 have none on their tax row, the tax read within a cent of its lines' taxes added up) (B-1911);
    - the lines against the subtotal: SUBTOTAL_MISMATCH max(0.02, 0.01·n), the Tax check 0.015 →
      `per_line(n)` = 0.01·max(1, n) + 0.01, the rule of a tax's base (B-1866; a cent more from two lines). Not the
      Tax check's cent: SUBTOTAL_MISMATCH and TOTAL_MISMATCH are hard (Approve anyway never covers them, D-75), a
      vendor's rounding line by line is no misread, and POSTING_UNBALANCED asks for every cent already. The Tax
      check's lines row asks for every cent too (`EXACT`) when the posting can't balance (the lines and the tax lines,
      each rounded as posted, are not the total: POSTING_UNBALANCED), so that it points at a line read off: with a
      cent a line, a line read 2 cents off was all ✓ beside the block (29 of the 300; a cent off, 32, all ✓ on
      cb08074 too, now ✕ as well) (B-1910). SUBTOTAL_MISMATCH keeps `per_line`;
    - the lines against a tax's base, QST on GST's base, the line that is the difference: 0.01·n + 0.01 (tax,
      taxmath) → `per_line` (the same; the Tax check's two-rate base with no lines 0.01 → 0.02);
    - a tax against its base × rate, once or line by line: `check_taxes` one unit, `tax_band` (a unit × (n/2 + 1))
      a warning not an error; the Tax check 1.5 units, or the band → `TAX_UNITS` one unit for both, the band only
      the finding's severity (B-1867); one tax at two rates: a unit × (k + 0.5) → a unit per tax line (the same);
      the review screen's tick on a tax line (`web/invoice_view.tax_check`, its taxable × rate rounded to the cent
      within 0.01·max(1, n) + 0.01) → the finding's rule, `tax_right` on `tax.tax_line_ways` (once on the taxable,
      line by line when it is its tax's only line), in the currency's units (B-1908, B-1909, the review): the Tax
      check said "GST WRONG 0.02" beside "GST ✓" on 229 of 400 generated invoices (60 on cb08074), none now, and a
      right tax in whole yen is ✓;
    - Azure's amount: 0.02 → `READ_AGAIN` (the same); LINE_MATH: max(0.02, 0.5%) → max(`READ_AGAIN`,
      `LINE_MATH_SHARE`) (the same); `line_tax._tax_base`: 0.05 → `PRINTED_BASE` (with the float error of the
      difference); the solver: max(2, n) + 0.5/rate cents half-even → `solver_cents` half up (the same at every rate
      in use, none ends in .5);
    - left apart, outside this item: JD Edwards' PO-matched SUBTOTAL_MISMATCH (a cent a line, `erp/jde.py`),
      capture's `amounts_equal` (0.005).
  - **The intended output changes** (`scripts/same_behaviour.py --ref 5aa6014 tax approve pipeline`; every line of
    the tax probe compared, and split by running this code with the old float arithmetic patched in):
    - tax: 505 of 20,000 outputs. The tolerances alone, 44: a tax row of the Tax check ✓ within the band, now ✕
      with its difference and why (B-1867); 43 beside a TAX_CALC_MISMATCH or TAX_BASE_MISMATCH of that tax, 1 (yen,
      QST marked and not charged) beside LINE_TAX_NOT_CHARGED. Half up alone, 463, each with at least a tie rounded
      the other way (none without): a tax row's expected a cent more or less, its detail, and its difference and
      why when it doesn't add up, 337 invoices, and 0.5 yen now 1, 92; the amount to self-assess, 12; the line the
      Tax check names as the difference, 3 lost and 3 gained (the base gap is the tax's gap over the rate: half a
      cent of it is 6 cents at 8%, beyond the 2-line tolerance either way); a row turned right (#8134, yen line by
      line) and one wrong (#19340); `check_taxes`: 24 messages with the expected tax half up (in 1 the line it named
      gone, as above), TAX_CALC_MISMATCH added 3 (one error, two warnings: the probe's tax was Python's round ± a
      cent, now 2 cents from half up) and removed 2 (yen), one error now a warning (#3905: 3 cents off → 2). 2
      invoices change both ways;
    - approve: the same; pipeline: 1 invoice of 20 files: SK PST 665.13 spread over 10 lines, 236.25 × 6% =
      14.175 now 14.18 (14.17), the largest line its own 233.02 (233.03), in its output, AI output and final output.
    - The review's fixes (B-1906 to B-1913; `--ref cfb45d6 tax approve pipeline`, every line of the tax probe
      compared): tax, 1,355 of 20,000 outputs, all in the Tax check's total row (with and without the rates set
      up): where the probe's tax total is not its tax lines (5 cents, a dollar or ten apart), the row adds the tax
      total (B-1911), so its tax, computed and detail, and a hint naming the tax lines; in 74 the difference shown
      too (✕ before and after). No row turned right or wrong, no finding, mark or text changed: the probe reaches no
      amount from 2^23 dollars on, no subtotal a cent or two off its lines, no cash rounding. Approve and pipeline:
      the same. The review's 16 scenarios × 300 (`runner.py`/`compare.py`, cb08074 against this), newly blocked:
      none in 15 (cash receipts 117 at cfb45d6, 8-to-60-million invoices 131 TOTAL_MISMATCH at cfb45d6, now none);
      one amount read off, 57, the header misreads chosen above (at cfb45d6 the same 57). The review screen's two
      tax verdicts at odds (`screen_agree.py`, 400 invoices): 60 on cb08074, 229 at cfb45d6, none.
  - **Benches** (venv312, old and new): `bench coding --realistic` the same (`lines.jsonl` byte for byte: accuracy
    93.8%, precision 98.2%, tax codes 97.4%, coverage 94.7%); `bench run --quick` the same (`evidence.jsonl` and
    `failures.jsonl` byte for byte: field accuracy 100.0%, verified precision 100.0% at 55.0% coverage, ECE 0.0189).
    The review's fixes, cfb45d6 against them: the same two benches the same, byte for byte, the same figures.
  - **CPU** (process time over the tax probe's 4,000 invoices, best of 5 × 3, old and new in turn): `check_taxes`
    52.0 → 57.4 µs (+10%), `math_check` 55.4 → 61.6 µs (+11%), `build_gl_distribution` 12.4 → 13.5 µs (+9%); all
    in decimals it was +22%, +40%, +54%. About 6 µs a review screen's checks. The review's fixes (the tax probe's
    first 2,000 invoices, best of 7 × 6 runs, cfb45d6 and this in turn, on a machine shared with other test runs,
    ±4 µs): `check_taxes` 56.7 → 56.2 µs, `math_check` 61.0 → 63.4 µs; a tax that doesn't add up on 4,000 lines
    (the review's `cpu.py`): `check_taxes` 6.9 s → 11 ms (B-1913).
  - **Laptop and server:** the same: functions of the coding and the rates only, no setting or mode.
  - **Left:** JD Edwards' pay items split cents their own way (`erp/jde.py` `_split`: each share cut toward zero, the
    rest on the largest; the GL distribution rounds each half up), J2's file: `money.split` would place them alike.
    The line named as a tax's difference could allow the tax's half unit over the rate, as the solver does.
    After the review, as on cb08074: a total, or a tax line, read a cent off is POSTING_UNBALANCED beside an all-✓
    Tax check (65 of its 300 misreads; within a cent is right for TOTAL_MISMATCH and TAX_CALC_MISMATCH, and the
    posting asks for every cent): the total row could ask for every cent when the posting can't balance, as the
    lines row now does. `tests/reports_reference.py` (the old reports' code) still accrues with `round`.
  - (`coding/money.py`, `coding/{tax,taxmath,validation,line_tax,taxreturn,terms,review}.py`,
    `web/invoice_view.py` (`tax_check`), `erp/accruals.py`; `tests/coding/test_money.py`,
    `tests/coding/test_line_tax.py`, `tests/coding/test_tax.py`, `tests/erp/test_accruals.py`)
- **D-184** · **Many people on one review queue: who else has an invoice open, Previous / Next past those, a screen out
  of date said once, and two revisions on every check** (scale goal, phase 5, S1; `docs/archive/scale-2026-11/
  web_frontend.md` §3.1, its bugs 7 and 8 and ideas 7 to 9, `web_backend.md` §9.1 and idea 13; B-1915 to B-1917).
  - **Who has it open** (server mode only; `core/store/ephemeral.py` `keep_open`, `open_by`):
    - one row of the `ephemeral` table per invoice and person: kind `open:<id>`, the person's draft key, the name
      shown; written as the screen is drawn (in the page's own brief transaction, with its draft) and renewed by the
      screen's poll (`GET /review/<id>/reader`, every 30 s, a minute apart at most while nothing changes);
    - written only when its last write is 45 s old or more (`OPEN_RENEW_SECONDS`): a read, then one upsert, else
      nothing; gone 120 s after it (`OPEN_SECONDS`): a tab closed, or hidden (the poll pauses);
    - its times are the database's clock (`julianday('now')`, as B-1770), in seconds since 1970 as the table's
      other rows keep theirs: the expired ones go with any of them;
    - only a role that may edit the invoice counts (a Read-only person looking is not working on it); never oneself;
      the display name, an email's part before its @;
    - said on the screen ("Ann Lee is working on this invoice too.", its poll says it again) and on the queue row
      ("Ann Lee has it open");
    - why not from the drafts (`invoice_drafts`): a draft lasts until the approval, days, and someone who opened an
      invoice without editing it yet is working on it too.
  - **Previous / Next** pass over the ones someone else has open, the screen saying so ("Next skips 2 that Ann Lee
    and Bo Chen have open."); every other one open, none is skipped. The invoice an action goes to next
    (`next_url`) is never one someone else has open, the screen's `next_id` included when it was opened since:
    worked out while the invoice is still in the queue (once it left it, none is passed over), Send to the manager's
    too (B-1939).
    - A tab left is not said: an invoice opened and passed with Next stays "open" up to 2 minutes (no write when
      leaving; the expiry says it).
  - **The invoice's revision** (`Store.coding_revision`): the id of its last event that moved it in or out of the
    queue or stored its coding (`REVISION_ACTIONS`: approved, its second approval, sent back, reopened (an approval
    undone too), parked, brought back, rejected, deleted, and the manager's three), as the second approval's `shown`
    is its last approval's id. The screen's form carries it (`rev`).
    - Why events, not a counter column: each of those changes logs its event in its own transaction already, so no
      path that moves an invoice can leave the revision behind; no schema 28, no upgrade, no backup; and the event
      says who did it and when. A note, a reading, the AI reviewer's verdict move no revision: a reading coming in
      keeps its own way (the panel draws the page again, the edits kept).
  - **The draft's revision**: the person's draft's edits in 12 letters (blake2b), "0" for none; the same edits keep
    it (two tabs typing the same thing lose nothing). It is in the approve bar (`draft_rev`), drawn again by each
    check, so the screen sends back the revision its last check answered (and the load bench's browsers follow it as
    a browser does); a line action answers it (`draft_rev`).
    - The screen's own id (`screen`, made with the page) is kept with each draft it writes: its poll, check or
      approval sent while its own check was under way carries the revision from before it, and is no other tab's.
      A draft its own check dropped (its edits put back as stored, Undo after a line action) names that screen for
      2 minutes (`UNDONE_SECONDS`, an `ephemeral` row "draft-dropped:<id>" of the person), so the same holds then
      (B-1937); and Approve or Send to the manager clicked while a check is under way waits for its answer.
    - A check (a line action, Send to the manager) writes the draft only while its screen is still at it, read again
      in the write's own transaction (`save_invoice_draft`'s `expect`, BEGIN IMMEDIATE): of two tabs whose checks
      overlap, the second to write is refused as out of date, never writing over the first (B-1938).
    - The page's first revision allows the one before it too ("<new> <old>"): the page keeps its draft once drawn,
      unless another writer holds the database (left to the next check).
  - **Out of date**: behind either revision, or the invoice gone from the queue, a check (409, `stale`), a line
    action (409) or the poll says once what was done, by whom and when (`ui.day_and_time`: "Approved by Ann Lee
    today at 10:41 a.m.", "Edited in another tab or window today at 10:41 a.m.", "Deleted by …"); the screen turns
    read-only (its boxes read-only, the approve bar gone, nothing more checked or sent) with what was typed still in
    its boxes to copy, and "Open it as it is now" opens the invoice as it is now in a new tab, beside them.
    - An action from it (approve, send to the manager) is refused as its check is (409, `stale`), while the
      invoice is in the queue: the screen turns read-only, what was typed still in its boxes (B-1936: the redirect
      of "changed meanwhile" was followed, the page drawn again from the other tab's draft, the edit typed just before
      Approve lost); once it left the queue (approved, parked, deleted…), "changed meanwhile" (`changed_meanwhile`).
    - A form posted without the revisions (not the screen's) is checked as before: "no longer in the review queue".
      The screen's revisions are looked at first, a line action's as a check's (B-1940).
  - **Laptop**: one person: nobody else is shown and nothing of it is written; the revisions and the out-of-date
    screen hold between its tabs (the screen's poll asks every 30 to 60 s there too).
  - **Measured** (`bench load --users 100 --duration 300 --workers 4`, a 20,000-invoice seed of 3,000 queued and
    400 accounts (`bench seed --invoices 20000 --queue 3000 --users 400 --history 0.3 --seed 7`, made again for
    each run), before (6152f8a) and after; write transactions counted from a connection of their own (PRAGMA
    data_version every 2 ms: a lower bound), and the writes of who has it open exactly (each write's row seen):
    - who has it open: 413 writes in the 300 s, **1.38 a second** (0.014 a person); write transactions seen 13.2 →
      14.2 a second (+0.97: most of those writes share the page's own transaction with its draft and confidence;
      the run also made 1,004 checks, 904 before);
    - the screen's poll: 0 → 55 (p50 11 ms, p95 44 ms through uvicorn; the bench's people seldom keep a screen 30 s);
      invoices opened 681 → 706, p50 66 → 76 ms, p95 295 → 303 ms; pages p95 477 → 425 ms; 5xx, "database is
      locked", tracebacks: none either time;
    - a day's use, a screen open all the time: a write when an invoice opens and one a minute while it stays open
      (the poll at 30, 75, 135 s…, written at 45 s or more): about 1.7 a second at 100 people and 7 at 400, plus
      the invoices opened, each a small upsert of one row.
  - **Left**: a tab left could drop its row at once (`pagehide`, a write more per invoice opened); "Take it" or
    assigning invoices to people; the queue row's note is as of the queue's drawing (no poll there); the load
    bench's browsers poll every `data-poll` seconds without the page's backing off (a little more than a browser).
  - (`core/store/ephemeral.py`, `core/store/invoices.py` (`coding_revision`, `last_change`, `invoice_draft`),
    `web/pages/invoice.py`, `web/pages/review.py` (`queue_rows`), `web/static/js/invoice.js`,
    `web/templates/{invoice,review,_invoice_document,_invoice_live}.html`; `tests/web/test_web_invoice_together.py`,
    `tests/core/test_store_ephemeral.py`)
- **D-185** · **Bulk approval as a background job; a session that ended keeps the page; every request of the pages'
  scripts has a deadline** (scale goal, phase 5, S2; items 6, 11 and 15 of `docs/archive/scale-2026-11/web_frontend.md`;
  B-1925 to B-1927; after its review, B-1928 to B-1934).
  - **Why:** a person approving 200 clean invoices held a web worker's thread for the whole run (6.6 s at 200 on this
    machine, one request with no progress, cut by a proxy's timeout while it went on behind); a check, an approval or a
    poll answered by the login sent the page to the login (the review screen kept its edits in the tab, B-1594; any
    other page was lost); and one request had no deadline (the upload, B-1926), the data-long actions none either.
  - **The job** (`core/store/bulk_jobs.py`, table `bulk_jobs` by `_SCHEMA`, no schema version of its own, as
    `past_lines`): the invoices asked, each with its `row_identity` and its stamp as the page listed them, who asked
    (their name and login, and the person as `identity.kept_person` keeps them), what was approved and what was left
    with why. The stamp (`Store.bulk_stamps`) is the id of the invoice's last event that moved it in or out of the
    queue or stored its coding (`coding_revision`'s, D-184) or read it again (`proposal_updated`); the queue's form
    carries each one's as it drew it (a form without them: as they are when it is posted, B-1928).
    - One open (waiting or being approved) a person (a unique index and one write transaction: four web workers asking
      at once get one job); asked again meanwhile, the page of the one under way says so.
    - Done by the company server's background process (`jobs.BulkRunner`, started by `app.start_background_work`, two
      at a time, oldest first), as D-169's downloads; on a laptop, or a server of one process without its background
      work, by a thread of the process asked (`jobs.approve_here`), which ends once none waits.
    - Each invoice (`bulk.approve_in_job`): no longer the invoice listed (another `row_identity`, or its stamp moved:
      approved and undone, reopened, parked and brought back, a manager's answer, read again) → "changed meanwhile",
      looked at again inside the approval's transaction, so coding stored after the job checked it is never written
      over (B-1928); not clean by today's checks → why, exactly as before (`bulk._still_clean`, split out of
      `bulk_approve`, which the tests and the bench still use); open on someone else's review screen now (the server's
      presence, `Store.open_by`, D-184), nothing of theirs checked yet → "Ann Lee has it open on the review screen",
      theirs to approve (B-1934; one only the person asking has open is approved); else approved as coded. The
      approval and the job's record of it are one write transaction (`Store.bulk_step`, BEGIN IMMEDIATE), taken only
      while the job is still that runner's, and in it the review screens' drafts are looked at (someone's edits kept →
      "it has edits open on its review screen"; the queue's request read them once at its start, now each invoice's
      own moment); the approval's own compare-and-set (`approve_invoice`, `WHERE status = 'review'`) refuses one acted
      on meanwhile. Nothing in that lock reads a whole list (whether there are cost centers is one row looked for,
      B-1933). So an invoice is approved at most once, and counted once, however the job is stopped or taken again.
  - **A restart:** a background process stopping puts the job back in line between two invoices (its try not
    counted), and so does a laptop's dashboard closing (`jobs.stop_here`, waited for up to 5 s as the server's,
    B-1930); at its next start, or a laptop's (`jobs.resume_bulk`), jobs left being approved are put back too (one
    background process a data folder, D-111). Its progress is noted at each invoice's start and at its approval
    (`renew_bulk_job`, `bulk_step`); one whose process stopped noting it for 30 s is taken again by another
    (`claim_bulk_job`), which goes on from the next invoice. So an invoice's learning taking long never lets another
    take it (B-1931); one invoice whose own check and approval take over 30 s still does (as a process that hung
    would). Three stops end it, saying so ("AP Coder stopped 3 times…: the ones still in the queue need a person");
    one of another format (an update changed it) ends the same way; one of the database before a restore is no
    one's. What was done is kept and shown either way.
    - None is taken while a backup is restored (`Store.restoring`, as the page reader, the Azure check, the imports
      and the JD Edwards sync wait). A database error noting a job's end or putting it back (locked past the 30 s
      wait, a restore's moment without the table, a full disk) is logged, never the end of the thread: the job is
      ended once it is taken again (30 s on), and the laptop's thread, however it ends, no longer marks the database
      as approved by it, so the next one asked starts another (B-1929).
    - What is not done again: an invoice's learning (`learn_from_approval`) follows its approval's commit, as the
      review screen's Approve does, so a process that dies in between (killed, the power off: an orderly stop waits
      for it) loses that one invoice's learning, the invoice approved and counted once. The job taken again does not
      learn it again: learning is several writes, not one transaction, and not safe to make twice (it counts what was
      kept and corrected, and teaches the template); it reads the invoice's pages (seconds for a scan), so it is never
      made inside the approval's write lock either.
  - **As the person who asked:** the job runs inside `identity.acting_as(kept)` (`person()`, the events' actor and
    address as their request's), their name and login on each approval and its learning: whether their session goes
    on or not. On the server it stops (with why) once their account may no longer approve (turned off, or a role below
    Clerk): an Admin's change applies to the invoices not approved yet.
  - **The request:** `POST /review/bulk-approve` starts it and waits up to `BULK_WAIT` (3 s, as Process waits at most
    3 s, D-172) holding no thread (an async loop on an event set by the thread of this process, or, with several web
    workers, a look at the job every 0.05 to 0.25 s). Done by then: the same answer as before (the queue, "Approved N
    invoices", the ones not approved and why), so the tests, the bench and a small approval are unchanged. Not yet: to
    `/review/bulk`. The form is no longer data-long.
  - **The page** (`/review/bulk`, `review_bulk.html`): "Approving: 37 of 200 approved, 2 need a look", followed by
    `app.js`'s `data-job-status` from `/review/bulk/status` (an automatic look: it keeps no session going); once done,
    what was done and each invoice left, linked while it is still in the queue. Safe to leave: the queue says "Your
    bulk approval is under way: …, See how far it got", then once "…is done: …" until the page is seen. Its routes are
    Clerk's, and a person's job is theirs alone.
  - **The session ended** (server mode; and a laptop shared on the office network that asks who it is again): every
    request goes through `fetchWithin`, which sees the login's answer (a 401, or a look sent on to /login or /who) and
    says so once, on the page, which stays as it is: "Your session ended: sign in again; your edits are kept" (the
    last part only when the review screen kept them in the tab), with "Sign in again" to `/login?next=<this page>`.
    The review screen keeps its edits in the tab (B-1594's `apc:session-ended`) at every refused check and as the
    link is followed, then puts them back as the person's draft after the login. `post` answers such a 401 (the
    middleware's, marked `session_ended`) with nothing to follow or show; a login step's own 401 is followed as it is
    (B-1925). Polls stop (B-1712) without reloading the page; the download link and the upload keep the page too.
    - What the server keeps: each check of the review screen saves its coding as the person's draft (`invoice_drafts`,
      D-86), so everything up to the last check answered before the session ended is on the server; Approve sends the
      whole form. Edits after it are only in the tab (sessionStorage) until the login: a tab closed meanwhile loses
      them. Changed meanwhile (D-184), the screen drawn after the login puts them in its boxes (the tab keeps the
      boxes as Undo does, `snapshot`), then turns read-only with them there to copy (B-1941). Elsewhere nothing is
      kept: a settings form, a note typed, a file picked for upload stays on the page while it is open, but following
      "Sign in again" leaves it (sign in in another tab to keep it).
  - **Deadlines:** `fetchWithin` is the pages' one fetch (`tests/meta/test_code_rules.py`: no bare `fetch(` outside
    it, nor `window["fetch"]`, `navigator.sendBeacon`, an `EventSource` or a `WebSocket`, and an XMLHttpRequest only
    with an abort or a timeout of its own near it, B-1942); the review screen's last check sent as the page closes
    goes through it.
    A look 30 s, an action 5 minutes (the Process POST answers within 3 s, D-172; the bulk approval within
    `BULK_WAIT`), a data-long action (a restore, a big import) 2 hours (`LONG_MS`, it had none). The upload: 30 s
    without its files moving on, or without the answer once they are all sent (B-1926). Long downloads are jobs
    (D-169).
  - **Measured** (`scripts/ci/perf_timing.py --invoices 600 --bulk 200`, which now waits for the job's end): before,
    6.6 s in the request, one thread held; now the answer after 3 s holding none, the 200 approved in 6.3 s by the
    job's thread (6 connections in all). `scripts/same_behaviour.py --ref 6152f8a approve`: same. After its review
    (each invoice's stamp read, the job renewed at each invoice): 200 in 6.2 s, `/review` 1.32 s (6.5 s and 1.31 s
    just before, on the same machine).
  - **Left:** the queue still lists and posts every candidate (a cap and "select all N" are item 12); the page doesn't
    warn before the idle limit; the job's progress is a line, not a bar.
  - (`core/store/bulk_jobs.py`, `core/store/invoices.py` (B-1933), `coding/bulk.py`, `web/jobs.py`,
    `web/pages/review.py` (the bulk route and its page), `web/identity.py`, `web/pages/login.py`, `web/app.py`,
    `web/roles.py`, `web/static/js/{app,process,settings}.js`, one call of `invoice.js`, `review.html`,
    `review_bulk.html`; `tests/core/test_bulk_jobs.py`, `tests/web/test_web_review_bulk.py`,
    `tests/web/test_server_browser.py`, `tests/meta/test_code_rules.py`)
- **D-186** · **The company server's service on a Mac: a LaunchDaemon that runs the service host from a `current`
  link, started again by launchd only when it did not end on purpose; `ops/service.py` split by platform** (scale
  goal, phase 6, O1: the pilot runs on a Mac mini, its models on a Mac Studio).
  - **Why:** AP Coder's service knew only Windows' scheduled task (`service` refused anywhere else), so on the
    pilot's Mac nothing started AP Coder at boot, brought it back after a crash, or kept it stopped once stopped.
  - **What:** `service.py` keeps what both platforms share (the settings, the service host, the dashboard's own
    stop, `status`, the doctor's lines) and hands install/start/stop/remove/status to this computer's backend:
    `service_windows.py` (the task and run.cmd, moved as they were; their names still found in `service`, its
    `__getattr__`) or `service_macos.py`. The daemon is `/Library/LaunchDaemons/<label>.plist` (`com.apcoder.server`
    for the default task name; a name like a label as it is; any other made into one), written with `plistlib`,
    root's and 0644: `UserName` the service's account (`_apcoder`; root refused unless `--allow-root`), `RunAtLoad`,
    `KeepAlive {SuccessfulExit: false}`, `AbandonProcessGroup false` (launchd's default, written down: a killed
    host's dashboard goes with it), `ThrottleInterval` 10, `ExitTimeOut` 60 (more than the host's 45 s for its
    dashboard), `NumberOfFiles` 8192 (launchd's 256 is too few for the web workers), its output in
    `<data>/logs/launchd.out.log` and `.err.log`. It runs `<the code folders' parent>/current/.venv/bin/python -m
    ap_coder service run`, `current` a relative link to the version's folder. launchctl: `bootstrap system`,
    `bootout system/<label>`, `kickstart -k`, `print` (its top level read), `enable` before a start.
  - **The host's exit code is the contract with KeepAlive:** 0 only when stopped on purpose: the `stopped` file, or
    SIGTERM / SIGINT (`StopSignals`, off Windows: the dashboard asked to stop, ended STOP_WAIT, 45 s, later if it
    won't, then exit 0); "start me again", `service.again()`: `HOST_AGAIN` (75) off Windows, so launchd starts it
    again 10 s later, 0 on Windows as before (its task comes back every 5 minutes), when another host holds the lock
    or when the folder to run has changed (an update's recovery at the host's start, D-188, returns `again()`: the
    host started by the `current` link's new target is the right one); a crash or a kill is started again. An update
    cut short that only root can finish (the link to switch is root's, D-188) ends with 0 too: left stopped, its log
    saying to run `sudo server-update`, never started again every 10 s (B-1956, B-1957).
    `service stop` is the flag, `/shutdown`, then `bootout`: it stays stopped after a reboot (the host launchd starts
    at load ends at once). `service start` bootstraps it, or `kickstart -k` when it is loaded.
  - **server-update:** `switch_to` asks the backend (`service.switch_folder`): Windows rewrites run.cmd as before;
    the Mac points `current` at the new folder by one rename (`os.symlink` to a temporary name, then `os.replace`)
    while the service is stopped. `Server.installed` asks the backend too (the plist there). The plist never changes
    with a version. Neither does Windows' task: it starts in run.cmd's folder (the data folder's `service`); it started
    in the folder it was installed from, so once server-update's "two versions back" was deleted Task Scheduler could
    not start it (error 267). A task an older version registered is still so until `service install` again:
    server-update says so instead of "delete it" (it reads the task's XML `service install` saved), and SERVER.md
    section 11 (B-1961).
  - **Through a power cut:** what the service starts from is written whole or not at all (`service.write_whole`: a
    temporary file, fsync, `os.replace`, the folder fsynced): server.json, run.cmd (written in place before, a cut
    could leave it empty and no host would start to recover an update) and the plist; the `current` link's folder is
    fsynced after its rename.
  - **Root on a Mac:** sudo leaves the environment out, so the plist (root's) gives the other service commands the
    data folder when `AP_PRIVATE_DIR` is not given, and `install` refuses with neither. A file root's sudo makes in
    the data folder is root's (a Windows folder gives its rights to what is made in it): `install` and `start` hand
    root's own folders and single-name files back to the daemon's account (`hand_back`: `os.fwalk`, `O_NOFOLLOW`,
    `fchown` on the open file; only in a data folder that is the account's). `host_running` no longer makes
    host.lock (made by root, the host could not take it). Every Mac service command but `install --dry-run` asks for
    sudo.
  - **`status --json`**, both platforms: installed, running, pid, started, version, folder, address, answering, the
    `/healthz` answer, the dashboard's process, stopped on purpose, where the settings and log are, and the
    backend's own (the task and its state; the label, plist, loaded, launchd's pid and last exit code).
  - **Proof:** `tests/ops/test_service_macos.py` (the plist read back, launchctl's commands against a stand-in, the
    refusals, the data folder found in the plist, the link switched under a reader 400 times, SIGTERM and SIGINT
    sent to a real host and dashboard process (kind, stubborn, crashing), the exit codes, the hand-back never through
    a link or a hard link, the CI script's flow against a stand-in Mac); `tests/ops/test_service.py` as it was, but
    the second host's exit code checked on both platforms, and `status --json` on Windows. On GitHub's macOS runner
    the CI job `macos-service` (`scripts/ci/macos_service.py`) does it for real: install, `kill -9` of the host
    (launchd brings it back, the old dashboard gone), `service start` while it runs (`kickstart -k`, which signal it
    sends is not documented: only the restart is checked), `launchctl bootout` of the running daemon (launchd's
    SIGTERM, as at the Mac's shutdown: the host's log says so, its dashboard is gone, it stays stopped), `service stop`
    (still stopped 60 s later), start, remove (the plist gone).
  - **Not chosen:** a LaunchAgent (needs someone logged in); `KeepAlive true` (a deliberate exit 0 would be started
    again, so every stop would need a bootout, and a reboot would start it); the plist rewritten at each update (a
    reload each time); the link in the data folder (the service's account can write there, and could point it at
    other code); `launchctl load/unload` (deprecated); a daemon library (a new dependency); `SMAppService` (an app
    bundle, signed).
  - **Left:** launchd's own end of a killed host's processes (SIGTERM to its group) is relied on, and measured by
    the CI job only; LM Studio's daemon on the Mac Studio is a recipe for IT in SERVER.md, not code; `process_image`
    is Windows' only, so on a Mac `service stop` leaves a dashboard that won't stop to launchd's bootout; `doctor`'s
    and `users`' runs as root are not refused (SERVER.md runs them as the account).
- **D-187** · **The company server's health for IT: `GET /readyz` for a monitor, `doctor --json`, failures in the
  system's log, the certificate's end read from its PEM, daily backups kept 14 days, 8 weeks and 12 months, and a
  business time zone** (scale phase 6, O2: 400 people, 25,000 invoices a month on one server).
  - **Why:** IT had `/healthz`, which says the dashboard answers and nothing more: a background process stuck, a
    disk filling, a certificate expiring, a model server on its backup were seen when staff called. Fourteen daily
    backups are a fortnight: a mistake found at the month end could not be undone. A virtual machine left on UTC
    started the 1-5 a.m. backup at 8 p.m. in Toronto, with people still at work.
  - **`/readyz`** (`ops/health.py` `readiness`, the route in `web/app.py`): 200 when ready, 503 when not, a JSON body
    of checks, each with its status, and the problems in words (docs/SERVER.md section 12 gives the shape). **503
    only when AP Coder can't do its work**: the database unreadable, the data folder's disk under `BACKUP_RESERVE`
    (nothing read, no backup), the certificate expired, the background process's heartbeat 2 minutes old, no
    capture process. A backup failed or old, no second folder, a model server on its backup, the clock's zone: a
    warning (200, `"status": "warn"`): a load balancer in front of the only server must not take it out for those;
    they reach IT through the system's log and the body. **Cheap:** counts through the queues' status indexes (the
    oldest waiting by its id or its index), a heartbeat row, the backups folder's listing, `disk_usage`, the
    certificate file; each process keeps its answer 5 s (`Kept`: callers wait for one, then share it); the second
    backup folder (a share gone can take a minute) is looked at on a thread of its own, waited 2 s, kept 60 s.
    **Nothing secret or named:** no path, token, model server's address, person or vendor; a backup's failure gives
    its time, not its reason (which may name an invoice's file).
  - **Who it answers** (its rule in `web/roles.py` is `OPEN`: the route checks, as `/shutdown` does): on the server
    its console (a loopback address with no `--proxy`, as `pages/login.py` `from_console`), and the monitors
    server.json names: `monitor_token` (24 characters or more, sent as `X-AP-Coder-Monitor` or `Authorization:
    Bearer`, compared in constant time) or `monitor_addresses` (addresses or networks, for a probe that can't send a
    header). Others get a 403 that says how. Not chosen: the allowed hosts (`AP_ALLOWED_HOSTS` are the names staff
    type, a Host header any client sets), a token in the query string (proxies log it), an Admin's session (a
    monitor has no account nor an authenticator app), open to all (the rule asked for the console and the server's
    settings). On a laptop as `/healthz`: this computer everything, another computer only `{"ready", "app"}`
    (`identity.OPEN_PATHS`: no name asked first).
  - **The heartbeat:** `Store.beat("background")`, an `ephemeral` row the background process renews every 30 s
    (`server.run_background`), read with `heartbeat_age`. Not a lease: `take_lease` refuses a new owner until the
    old one runs out, so a restarted process would look dead for that long. With one process (a laptop,
    `--workers 1`) the work runs in the dashboard's own: nothing to check.
  - **The certificate's end:** a DER walk of the PEM to the validity's notAfter (UTCTime and GeneralizedTime, the
    version optional; the earliest of the file's certificates, the CA's included). Not chosen: `cryptography` (not a
    dependency), `ssl._ssl._test_decode_cert` (private), a loopback handshake (`getpeercert()` gives a dict only when
    it verified the certificate, which needs the company's CA; its binary form is the DER again). A warning from 30
    days before (doctor, `/readyz`, an Admin's banner on every page, `core.admin_notice`), an error once expired
    (the banner red, 503). The server's own process looks every 6 hours for the system's log (`watch_certificate`).
  - **The system's log** (`ops/system_log.py`, server mode only): Windows' Application log through `eventcreate`
    (part of Windows; source "AP Coder", which an administrator makes once: SERVER.md section 6; event numbers 101
    backup, 102 full disk, 103 a process stopped, 104 certificate, 105 a model server's backup), syslog on macOS and
    Linux (`SysLogHandler` on `/var/run/syslog`, `/dev/log`; nothing without one). The same kind and message (or
    key) at most once an hour per process; it never raises. Told by: a backup failed or refused (`_backup_now`), a
    copy to the second folder failed, the import queue paused on a full disk, the server's supervisor seeing a
    background or capture process stop, a breaker moving its calls (`Failover.failed`), and `/readyz` finding a
    failure. Not chosen: pywin32 (a dependency), PowerShell's `Write-EventLog` (gone from PowerShell 7).
  - **Retention** (`backup_files.Retention`, `kept_dailies`): the newest 14 dailies, the newest 14 complete ones
    (B-1558), and of the complete ones the newest of each of the last 8 ISO weeks and 12 months that have one (as
    restic's `--keep-weekly`: a server off for months keeps its history, where calendar windows would drop it); the
    newest complete one is never dropped; a labelled one is never among the dailies; the second folder the same
    (`copy_backup`). On the company server only (`retention`): a laptop keeps its 14, as before (laptop mode
    unchanged; a laptop's disk is small). About 32 copies of the database on the server's disk: SERVER.md says so.
    The server is told as the doctor tells it (`service.is_server_here`: server mode, or server.json in the data
    folder), so a `backup now` or a restore run from IT's console without AP_SERVER_MODE keeps the server's history
    too (it pruned the second folder to 14, B-1960).
  - **The second folder on the database's disk:** its `st_dev` against the data folder's (the volume serial number
    on Windows, a share's own), a warning in doctor, `/readyz` and Settings → Data & backups; no second folder at
    all, a warning too. On the server only: a laptop's OneDrive folder is on its own disk and synced away.
  - **The business time zone:** server.json's `time_zone`, an IANA name, read with `zoneinfo`. Its database on
    Windows is the `tzdata` package, which pandas requires there (`tzdata; sys_platform == "win32"`, and
    python-dateutil): in the venv and in the offline bundle's wheelhouse (pip resolves it for each platform), so no
    new dependency and no fallback; a name it can't find is said. The machine's clock is compared by its UTC offset
    now and in mid-January and mid-July (Toronto and New York agree; Regina and Winnipeg don't). Only the daily
    backup's hours follow it (`backups._business_hour`); "today" elsewhere is the machine's clock (the places are
    listed in the phase's report): the server's time zone is set in Windows.
  - (`ops/health.py`, `ops/system_log.py`, `ops/doctor.py` `as_json` `store_checks`, `ops/cli.py`, `web/app.py`,
    `web/roles.py`, `web/identity.py`, `web/server.py`, `web/core.py`, `web/pages/settings.py`,
    `core/store/{backups,backup_files,ephemeral,imports,page_queue}.py`, `ai/local_llm.py`, `intake/imports.py`;
    `tests/ops/test_health.py`, `tests/ops/test_system_log.py`, `tests/web/test_readyz.py`,
    `tests/core/test_backups.py`)
- **D-188** · **server-update prepares while the old version serves and survives a power cut; a restore closes the
  server to others; the server's data folder on its own disk with its database; the same bundle for the same
  commit** (scale goal, phase 6, O3; B-1955).
  - **Why:** the update stopped the service first, then unzipped, set up the new folder from its wheelhouse (minutes on
    Windows), migrated, ran the self-check and started: 400 people closed out for all of it, and a migration that
    failed was found with the service down. A power cut part way left the `stopped` file (the service stayed down after
    the reboot) and maybe a half-migrated database, with nothing saying where it was. A restore let every other worker
    draw pages from a database being replaced. SQLite's write-ahead log is damaged on SMB/NFS shares. A database file
    deleted by mistake was replaced by an empty one at the next start, which the daily backups then copied for two
    weeks. Two builds of one commit gave different ZIPs (file times, the build time in the manifest).
  - **The update** (`ops/server_update.py`): unzip, set up, a copy of the database (SQLite's backup, consistent while
    the old version writes) migrated by the new version in `service/update-check` (timed, removed after), and the
    self-check, all before the stop, after a check of the disk's room for a copy (the new folder's launcher runs with
    `--no-start`, which leaves the AP Coder of another folder serving, B-1944). Then stop, the safety copy, the real
    migration, switch, start, healthy. The downtime (stop to healthy) and each step are printed and kept in
    server.json's `history[-1]["times"]` (`prepare_s`, `check_migration_s`, `self_check_s`, `stop_s`,
    `safety_copy_s`, `migrate_s`, `start_s`, `downtime_s`). A failure before the stop changes nothing ("not updated");
    after it, as before (the backup put back, the old folder started). The new version's migration skips the Store's
    own pre-migration copy (`MIGRATE`: `backup_files.before_update` made a no-op in that process): the safety copy was
    made a moment before with the service stopped, and a second copy of a year's 7-9 GB would add minutes. The safety
    copy is `safety_copy` (local; it was `backup_now`), and the second backup folder's copy is made after the service
    answers, by the new version (`Server.second_copy`): a copy to a share is no longer in the downtime. `Server`'s
    interface is kept (one method added); `switch_to` and the platform code are untouched.
  - **The marker** (`ops/update_recovery.py`): `service/update.json` {from, to, from_folder, to_folder, database,
    label, step, started, pid, safety_copy}, written whole at each step (a `.part` file, fsync, `os.replace`, the
    folder fsynced off Windows), removed once the update is recorded. `service/update.lock` (`paths.try_lock`) is held
    by server-update throughout, so a marker with a free lock is one left behind; a second server-update is refused.
    The next `server-update` (or `--back`), and `service run` before it looks at the `stopped` file, act on it, by
    step: `prepare`, `stop`, `safety-copy` → the old version starts (a cut copy's `.part` removed, the update's own
    `stopped` file removed; nothing stopped or started after `prepare`); `migrate`, `going-back` → the safety copy put
    back (`put_back`), the old version starts; `switch`, `start` → finished (the new version starts, recorded with
    `previous_folder` and `pre_update_backup`, so `--back` works); server-update goes back when the new version does
    not answer, and the service host goes back at `switch` when it still runs the old folder. A host whose folder is not
    the one to run rewrites run.cmd and exits: the task's 5-minute trigger starts the right one (a Mac's host can't:
    below). server-update acts on
    a marker only when both folders are beside its own (the data folder is the service account's to write, D-109),
    with the database it was given, a safety copy only from `backups`; it then stops ("run server-update again").
    Each step is proven by a test that cuts the update there (a `BaseException`, so no rollback code runs) and then
    recovers both ways (`tests/ops/test_update_recovery.py`).
  - **The service host's part** (B-1956, B-1957, B-1958): it switches nothing when the rule's folder is the one it
    runs (only server.json's `folder` is written), and asks before it acts whether it may switch at all
    (`service.can_switch`: the folder it would write, not the platform). Once the safety copy is put back, the marker
    says so (`put_back`): never put back twice, and only going back is left. On a Mac the host runs as `_apcoder`,
    and the `current` link and its folder are root's, so by step:

    | cut short at | the host runs (the link's) | it does |
    |---|---|---|
    | `prepare`, `stop`, `safety-copy` | the old folder (moved at the switch only) | the old version starts as it was |
    | `migrate` | the old folder | the safety copy put back once; the old version starts |
    | `switch` | the old folder / the new (moved before the cut) | put back once, old starts / finished, new starts |
    | `start` | the new folder | finished: the new version starts |
    | `going-back` | the old folder (moved back) / the new | put back once, old starts / **nothing changed**, exit 0 |

    "Nothing changed": no put back under the new version, the marker kept, exit 0 (launchd leaves it stopped), the log
    saying to run `sudo server-update`, which goes back. Windows' host there rewrites run.cmd and exits (`again()`):
    its task starts the old folder. **Can't tell** (`update.lock` won't open: root's, made by a `sudo server-update`,
    `--dry-run` too, before this, and no `service start` since to hand it back): nothing changed, said in the log, and
    the host goes on as without a marker: at `prepare` the old version serves on (it was serving), after it the
    update's own `stopped` file keeps it stopped (exit 0) for server-update. A put back the account may not make (a
    backups folder root's sudo made: `PermissionError`): exit 0 too, not 75 every 10 s. Any other failure: `again()`
    while the database may be half put back or the folder is wrong (tried again), else AP Coder starts and the rest
    is done at the next start. `service install` makes `update.lock` the account's (`hand_over`), and a lock or a
    marker root's sudo makes is the service folder owner's from the start (`_owned`: on the file just made with
    `O_EXCL`, never through a link). server-update going back after its own recovery (the new version did not
    answer) writes a `going-back` marker first. Proven on Linux with the host really run as another account (uid
    65534, the code folders' parent root's), each step; the macOS CI job cuts one short at the safety copy for real.
  - **server.json on Windows** (B-1962): Windows refuses a rename over a file another process has open without
    delete sharing (as Python opens files; an antivirus): `write_whole` tries it again for 1.6 s in all
    (`service.REPLACE_WAITS`); a monitor's `/readyz` reads server.json's monitor settings again only once the file
    changes (its inode, time and size: `health.monitor_config`); and an update done and answering whose record still
    can't be written says so, returns 1 and keeps its marker (step `start`), which the next server-update or service
    start records (`previous_folder`, so `--back` works), never a traceback with nothing said.
  - **A restore** (`core/store/backups.py`, `web/maintenance.py`): its marker `<database>.restoring` (beside the
    database, never in it) is {pid, started, backup}; on the server an ASGI middleware outside the session answers
    503 (a plain page that refreshes itself, JSON for fetches, `Retry-After: 60`) while it is there and its process
    lives, on every web worker; `/healthz`, `/readyz`, static files and the restore's own request (under way before the
    marker) still answer. One stat a request; the marker read on a thread. The marker goes on success and failure;
    one a crash left is removed as `serve` starts, said in the log, unless a console restore still holds the backups'
    lock (`clear_left_restore`). The laptop is unchanged (its background workers still wait, as before).
  - **The data folder** (`ops/safety.py`, from `serve` and the doctor): a UNC path (both slashes, `\\?\UNC\`), a
    mapped drive (`GetDriveTypeW` = DRIVE_REMOTE) or a mount of smbfs/afpfs/nfs/nfs4/cifs/smb3/webdav/davfs/sshfs
    (`/proc/mounts`; the Mac's `mount` output) refuses the server's start; a laptop gets a warning, as for OneDrive.
    9p is warned, never refused, on the server too (B-1963): it is WSL2's /mnt/c (the Windows disk of the same
    computer) and a virtual machine's folder from its host, sometimes its root. SQLite's warning is about locks and
    shared memory across computers; here every AP Coder process is in the one machine, whose kernel keeps them, so only
    a program outside it (on the host, or WSL's Windows side) opening the same database would break them, and the
    folder is slower. Refusing it stopped a pilot on WSL or such a machine from starting at all.
    On the server, a missing database with signs it existed (backups, server-update's record, its `-wal`/`-shm`,
    `authenticator.key`, `logs/access.log`, invoice files; not server.json alone, which `service install` writes
    before the first start, nor the service log, written before the dashboard starts) refuses the start; `serve
    --server --new-database` makes the new one on purpose (at once, so the service starts on it).
  - **The bundle** (`scripts/build_offline_bundle.py`): every entry dated SOURCE_DATE_EPOCH, else the commit's time,
    sorted by name, 0644/0755 and Unix-made; the manifest's `built` that time (UTC) and its files sorted by name. The
    same bytes need the same wheels: pip resolves what PyPI has on the day (a constraints file would pin them), and a
    stage's wheelhouse reused from an earlier build keeps its old wheels; text files are as the checkout has them
    (CRLF on Windows). A smaller server-only bundle was not added: the flags give it (`--python <the server's>
    --no-current`), the self-check needs the samples and the OCR models, and the wheels for one Python and one
    platform are most of what is left.
  - **Proven in CI:** one job, `windows-server-update` (windows-latest, 60 minutes): the bundle built once, set up as
    the server on 20,000 seeded invoices, installed as the service (SYSTEM, plain HTTP), then `server-update --force`
    with the same bundle, offline; over 300 s down fails (`scripts/ci/server_update_downtime.py`, its verdict tested on
    Linux against what an update records). Measured here (Linux, 4 cores, the dashboard with 2 workers as a process,
    `server_update_downtime.py local`, 20,000 invoices, a 669 MB database): prepared in 16.7 s (the copy migrated in
    2.2 s, the self-check 13.9 s) while serving; down 11.4 s (stop 1.0, safety copy 6.7, migration 0.2, start 3.4).
  - **Alternatives:** migrating in place with the old version still running (two versions on one database); a safety
    copy before the stop (writes in between lost on a rollback); a marker in the database (the file being replaced or
    migrated); the 503 from a flag in the database or each worker's memory (other processes can't see it); refusing
    a share by `os.statvfs` (it can't tell a share).
  - **Left:** `service.write_runner` writes run.cmd in place (not a `.part` renamed), so a power cut in that write
    could leave it empty: O1 owns the platform code. `--back` writes no marker (a power cut during it leaves the
    `stopped` file, as before).
  - (`ops/server_update.py`, `ops/update_recovery.py`, `ops/service.py` `run_host` (3 lines), `ops/safety.py`,
    `ops/cli.py`, `ops/doctor.py`, `core/store/backups.py`, `web/maintenance.py`, `web/app.py`,
    `scripts/build_offline_bundle.py`, `scripts/ci/server_update_downtime.py`, `.github/workflows/ci.yml`;
    `tests/ops/test_server_update.py`, `tests/ops/test_update_recovery.py`, `tests/ops/test_safety.py`,
    `tests/ops/test_server_update_downtime.py`, `tests/web/test_maintenance.py`,
    `tests/offline/test_bundle_reproducible.py`)
- **D-189** · **No SQL runs outside `ap_coder/core/store`: each statement is a Store method or, for a database file a
  Store must not open, a module function of the store; a meta test keeps it so** (scale goal, phase 7, C1).
  - **Why:** the move to PostgreSQL needs every statement in one place. 55 lines in 6 files ran SQL outside the store
    (`erp/jde_post.py` 15, `erp/jde_sync.py` 12, `erp/hub/run.py` 9, `erp/hub/landing.py` 7, `ops/server_update.py`
    8, `ops/share_report.py` 4), most through `store._conn()`.
  - **What moved where:**
    - JD Edwards posting (`erp/jde_post.py`) → `core/store/postings.py` `PostingsMixin`, a new part of `Store`:
      `jde_postings`, `jde_posting`, `keep_jde_postings` (the copy in the ledger file), `put_jde_posting`,
      `claim_jde_posting`, `jde_documents`, `jde_not_posted`, `jde_locked`, `undo_jde_batch`, `forget_jde_batch`,
      `jde_downloads`, `jde_waiting`, `set_jde_voucher` (the invoice's `meta.jde_voucher`). The rows' statuses
      (`CHECKED` … `UNKNOWN`, `IN_FLIGHT`, `SENDABLE`, `FILE_DOWNLOADED`) are kept with the table and imported by
      `jde_post`, which keeps the state machine, its clock (`_now`, `_stamp`: passed in, so the tests' patches still
      hold) and `_put` / `_claim` (thin, called by the tests).
    - JD Edwards sync (`erp/jde_sync.py`) → `vendors.py` `master_vendors_of` and `set_erp_bank_and_hold`;
      `purchasing.py` `po_keys_written_since` and `open_po_numbers`; its `with store._conn():` (and `with
      store.session(), store._conn():`) holding several calls → `Store.transaction()`, a new part `transaction.py`
      (`session()` then `_conn()`: inside a session, as the sync always is, exactly what it replaces).
    - Finance hub (`erp/hub/run.py`, `landing.py`) → `core/store/hub.py`: `HubState` (its own `finance_hub/state.db`)
      and `write_gateway_database` with `GATEWAY_VIEWS` (the gateway's copy; it reads the hub's table list from
      `erp/hub/tables.py` when called, since `erp/hub` imports this part). `landing.gateway_database` keeps the
      temporary folder and its name (a test breaks it there).
    - server-update (`ops/server_update.py`) → module functions of `core/store/backups.py` that open the file
      themselves, read-only where it was, never a Store (it would migrate a newer version's database, the copy being
      migrated, a safety copy): `copy_database`, `one_file` (no write-ahead log), `accounts_in`,
      `carry_accounts_into`, `setting_in`. `server_update` imports them by name (`server_update.copy_database` is what
      its tests break).
    - The redacted run summary (`ops/share_report.py`, which opens a Store) → `learning.py` `invoice_count` and
      `run_summary_rows`.
  - **Transactions kept, never split or merged:** each `with store._conn():` around several calls is one
    `with store.transaction():` around the same calls: the sync's page (its rows, the progress setting and the lease,
    last: lost, all of it undone), B-1918's 250 vouchers at a time each with the lease, the POs closed 500 at a time,
    the part's progress and state at its end. Each moved statement runs in the transaction it ran in: one method per
    former `with` block, joining the caller's transaction where it did (the vendor reads inside the sync's page);
    the undo's check stays inside `undo_export_batch`'s `BEGIN IMMEDIATE` (`undo_jde_batch` passes it as the guard);
    `HubState` is one transaction a run, committed only by a run that worked; the accounts carried over a restored
    backup, one transaction. One difference no one sees: `put_jde_posting`'s new row takes the `updated_at` its
    update writes (two readings of the clock a moment apart before).
  - **The rule** (`tests/meta/test_sql_in_store.py`, by the syntax tree: comments and strings don't count): no
    `.execute(`, `.executemany(`, `.executescript(` or `.cursor(` call, no `sqlite3.connect` and no `._conn` in
    `ap_coder/` outside `core/store`, but its allowlist: the benchmark's tools, which work below the store on purpose
    (`bench/seed.py` writes 300,000 invoices straight in SQLite; `load.py`'s audit and `volume.py`'s statement tracer
    and row counts read the database they measure; `day.py`'s probe reads it read-only while the server runs;
    `coding.py` backdates a replayed day's feedback), each with its reason. The list only shrinks: a listed file
    with no SQL left fails the test. On 537708d it fails (the 55 lines); after, it passes.
  - **Left:** SQL text written outside the store and run by its generic readers (`summary_query`, `summary_fetch`,
    `summary_rows`, `search_rows`): `erp/insights.py`, `spend.py`, `controls.py`, `accruals.py`, `directory.py`,
    `search.py`, `dupaudit.py`, `jde.py` and `web/pages/process.py`. The statements run in the store, but their text
    (whole queries, or WHERE clauses) is not in it yet: the next step for the move (the rule checks what runs).
  - (`core/store/{postings,transaction,hub,backups,vendors,purchasing,learning,__init__}.py`, `erp/jde_post.py`,
    `erp/jde_sync.py`, `erp/hub/run.py`, `erp/hub/landing.py`, `ops/server_update.py`, `ops/share_report.py`;
    `tests/meta/test_sql_in_store.py`)
- **D-190** · **A PostgreSQL seam: the store's migrations as numbered steps, SQLite's own SQL behind names, and the
  database each release made brought up to date and compared with a new one** (scale goal, phase 7, C3; B-1975).
  - **Why:** a move off SQLite (for high availability) had no map. `_migrate` was one 200-line run of version tests
    and column looks; the SQLite-only constructs were spread over 40 files, uncounted; the migrations were tested on
    faked old schemas only (the real ones reached schema 15, and columns came at 19 without a version of their own).
  - **The steps** (`core/store/migrations.py`): `STEPS`, `(number, step)` in the order `_migrate` ran them. A
    numbered step runs on a database older than its number (`MIGRATIONS = {2: _v2_vendor_keys, ..., 26:
    _v26_indexes}`); a step numbered `EVERY_TIME` runs whatever the version, where it ran before (`_po_key_index`
    right after 4, seventeen more after 26). `_migrate` is the loop. The same statements in the same order: the new
    `migrate` probe of `same_behaviour.py` (22 old databases seeded with invoices, lessons, vendors, POs, ERP bills,
    profiles, snapshots and events) gives the same schema, in SQLite's own order, and the same rows as 537708d, but
    for B-1975's index. Its constants moved with it (`_INDEXES_*`, `_SNAPSHOTS_SCHEMA`, `FEEDBACK_CLEANUP`);
    `_fill_tax_codes` and `_recompute_vendor_keys` stay on StoreCore. `_base.py` went from 997 lines to 740.
  - **Migrated equals fresh** (`tests/core/test_migrations.py`): `scripts/old_schemas.py` runs each release's own
    code (`git archive` of its commit, or its code unpacked: PR #3 to #8 are older than this clone's history) to
    make a new database, kept as SQL in `tests/fixtures/old_schemas/` (22 files, one per distinct database: PR #1's
    schema 2 to PR #99's 27, the pilot's and hand-over's tags among them, and 20 to 26, which no release had). Each is
    opened by this version and compared, by name, with a new database: each table's columns (type, NOT NULL,
    default, key), foreign keys and AUTOINCREMENT, each index's columns, uniqueness and WHERE, each trigger and view.
    One difference is left, listed with why it does no harm (`KNOWN`: the AI reviewer's queue's columns in another
    order); one put right must come off the list. The check found B-1975.
  - **The dialect** (`core/store/dialect.py`): SQLite only, each name saying PostgreSQL's form: `PARAM`, `in_list`,
    `json_get`, `json_valid`, `upsert`, `insert_or_replace`, `insert_or_ignore`, `update_or_ignore`, `NOW_DAYS`,
    `NOW_SECONDS`, `group_concat`, `like`, `nocase`, `ROWID`, `settle`, `begin_write`, `busy_timeout`,
    `set_busy_timeout`, `data_version`, `table_names`, `columns`, `add_column`. The shared helpers use them, so each
    has one place: `_in_list`, `_STALE`, `ephemeral._DB_NOW`, `database_schema`, `briefly`, `_session_memo`, `_open`,
    the store's opening, the migrations and the vendor keys' recount; `begin_write` at the seven sites that already
    joined a transaction under way (the same behaviour). No other query was rewritten. Not converted: the 29 sites
    with a bare `BEGIN IMMEDIATE`, which fail inside a transaction (the digest's R4); through `begin_write` they would
    join it, a change of behaviour left to the nested-transactions item. `strftime` was dropped from the plan's
    list: the SQL uses none.
  - **The inventory** (`scripts/sql_inventory.py`, docs/POSTGRES.md): each construct counted in the text of the
    string literals that read as SQL (the syntax tree, docstrings left out) and the driver's in the code (calls,
    attributes): about 1,560 placeholders and some 340 uses of SQLite's own SQL, 80% in `core/store`, the JSON
    functions the most (about 130). The port planned at about 17 days, 19 with the clean-ups before it.
  - **The guard** (`tests/meta/test_dialect.py`): for the 15 constructs the dialect covers, the uses outside
    `dialect.py` may not rise above today's (`ALLOWED`). A total per construct, not per file: SQL moved between files
    (phase 7's SQL out of `erp/` and `ops/`) doesn't trip it; a fall doesn't fail it (the numbers are lowered by hand),
    so work in parallel isn't stopped by it. The whole-code scan takes about a second: in the slow list, run by CI.
  - **Alternatives:** a dict of numbered steps alone (the steps that run whatever the version would have moved,
    and the order of the schema's rows with them); the every-time steps made numbered ones (a database at 27 lacking
    a column would no longer get it); a dialect object handed round with the connection (nothing to choose between
    yet); old schemas written from each commit's `_SCHEMA` (not what the release made: its `_migrate` added columns);
    a per-file or exact allowance (every move or removal a failure).
  - **Left:** the 29 bare `BEGIN IMMEDIATE`; the parts' own `julianday('now')` (8) and the other counted constructs;
    the port itself (POSTGRES.md's plan).
  - (`ap_coder/core/store/migrations.py`, `dialect.py`, `_base.py`, `ephemeral.py`, `purchasing.py`, `invoices.py`,
    `reports.py`, `bulk_jobs.py`; `scripts/old_schemas.py`, `scripts/sql_inventory.py`, `scripts/probes/migrate.py`,
    `scripts/same_behaviour.py`; `tests/core/test_migrations.py`, `tests/core/test_dialect.py`,
    `tests/meta/test_dialect.py`, `tests/fixtures/old_schemas/`; docs/POSTGRES.md)
- **D-191** · **The longest functions split, and a limit on a function's length; one amount parser and one date
  parser, each caller's rules as its parameters; tests that wait for what they need, a slow list from measured times,
  shared test helpers, a time limit on every CI job and a load smoke job** (scale goal, phase 7, C4: clean and
  maintainable).
  - **Why:** the digest's cleanliness and test-health findings (docs/archive/scale-2026-11/DIGEST.md, sections 1 and
    7): functions of 150 to 270 lines nobody could hold in mind; amounts and dates parsed in four places that a fix
    had to find one by one; sleeps where a test waited for a thread (too short on a busy runner, too long on a quick
    one); a slow list that had drifted; helpers copied into dozens of test files; CI jobs a hang could hold for six
    hours.
  - **Long functions:** split into parts named for what they do, their lines moved as they were. `core/audit.py`
    `describe` 198 lines to 9 (ten parts, one subject each, tried in the chain's order); `web/app.py` `create_app`
    241 to 30 (the lifespan, the state, the middleware, the pages, the status routes, the error pages);
    `coding/line_tax.py` `_prefill` 165 to 56 (its docstring's steps 1-2, 3 and 4, and the codes written);
    `coding/validation.py` `validate_coding` 161 to 62 (header, lines, totals, history); `erp/jde.py` `_prepare` 176
    to 40 (header, tax area, tax amounts, the distribution and its entries; `_Voucher.add`); `ops/cli.py`
    `build_parser` 186 to 49 and `ops/doctor.py` `run_checks` 184 to 33 (by their groups of commands and of checks).
    `describe` was compared with the old one on 88,800 generated events, the parser by its whole tree of commands
    and options. **The limit** (`tests/meta/test_function_length.py`): 120 lines, from the measured lengths (5,144
    functions; 38 over 100, 19 over 120, 6 over 150 after the splits). The 19 over it are listed at their length and
    may only shrink; one under the limit (or gone) comes off the list. `StoreCore._migrate` (220) is among them: C3
    rewrites it, and its entry goes with it.
  - **One amount parser, one date parser** (`core/parsing.py`; in core, since the imports use them and core imports
    nothing of capture): `parse_amount(value, printed=, within=)` and `parse_date(text, printed=, within=,
    day_first=)`: printed (a document's text, or OCR's) or typed (a spreadsheet's cell, a CSV); the first value in a
    line or the whole text one value; how a day/month that reads both ways is read. The grammars were moved, not
    rewritten: printed and typed text disagree on what some values mean ("1.234" is 1,234 on a French invoice and
    1.234 in a cell; "250.00 DR" is a register's debit; "03/04/2026" is read in the document's order of dates and
    refused in an import), so each keeps its own, and a field's amount is the readers' grammar with the whole text
    one amount. The callers keep their names and say their rules: `normalize.parse_amount` and `parse_date`
    (printed, within), `supplier.parse_amount` and `parse_date` (printed, a field, its learned order),
    `safe.parse_amount` (typed), `csvio.parse_date` (typed, and why not: ambiguous or not a date); the English and
    French month names are one table. **Proven:** on 20,000 generated amounts, 20,023 values (numbers, NaN, True,
    texts) and 20,000 dates (numeric, ISO, wordy in eight languages, glued, OCR's slips, near misses), the parsers at
    537708d and the new one agreed on all 260,046 readings, on Python 3.11, 3.12 and 3.13. The old parsers are gone:
    `tests/core/test_parsing.py` keeps the digests of what they read, with round-trip properties of each grammar
    and the rules that differ.
  - **Tests:** `tests/conftest.py` `until(condition, seconds)`, one bounded wait for a thread or process (the
    copies in test_failover.py and test_web_review_bulk.py go). The sleeps that waited for something to happen wait
    for it now (120 `time.sleep` in tests/ to 109): another web worker seeing a job, three readers at once over two
    rounds (test_server_scale), a thread asking for an answer finding the other process's hold (three in
    test_kept_answers), the holder of the one chat slot at the server (test_failover), the background loops having
    looked three times (test_page_worker, test_bulk). Those left let time pass on purpose: a lease, a hold or a
    resend wait running out, a stand-in's latency, a check that nothing happens meanwhile, a subprocess's script.
    **The slow list** from measured times (`--junitxml` of the default run: 562 s here, 5,081 tests): the 35 tests
    over 2 s that weren't listed (108 s) join it, as 33 entries: the default run 496 s after (5,040 tests). The older
    entries (over half a second) stay: timed again on this machine, shared with other agents' runs, they would have
    measured the load.
    **Shared helpers:** `tests/stores.py` holds the `store` fixtures files wrote again (the sample setup; a copy of
    the demo; of the cost centers' demo; each where the dashboard keeps its database): a file imports the one it
    starts from, and pytest takes a fixture imported into a test file as the file's own (24 copies go; 23 that differ
    stay: another path, the page reader's test, accounts, an env var, the setup-then-demo template).
    `tests/conftest.py` `demo_db` (6 go; 4 that are the cost centers' demo and test_offline.py's path stay);
    `tests/webclient.py` `PAGE` (19 go; offline_flow.py's stays: standard library only) and `gt`, now with changes
    (12 `_gt` go, imported as `_gt`; test_controls_watch.py's takes an invoice number). `tmp_path_retention_policy =
    "failed"` was set already (with `faulthandler_timeout`).
  - **CI:** a `timeout-minutes` on the seven jobs that had none (Tests 45, Lint 10, Browser tests 30, the Windows
    installer and pilot launcher 15 each, the two offline bundles 25 each: about twice their time in a passing run),
    and `tests/meta/test_ci_workflow.py` keeps one on every job (60 at most). **`load-smoke`:** ten people for a
    minute on two web workers over 2,000 invoices `bench load` seeds itself, its gate failing the job (a 5xx, an
    answer over 30 s or none, "database is locked", an invoice approved or exported twice, the summary off): 2
    minutes here (seed 45 s; 313 requests, p95 178 ms). **The bundle built once a run and shared** was not done: the
    jobs that build one want different bundles (Windows' Python 3.12 with the OCR models; the Mac's 3.14 for arm64;
    `mac-pilot-update`'s `--no-current`, built as elsewhere on purpose; `--no-models` on each OS for the update
    tests), and the one job building the same as `windows-offline-bundle` is `windows-server-update`, left as it is;
    an artifact would also hold both back behind a build job.
  - **Alternatives:** one grammar for printed and typed amounts with a switch for each difference (exact agreement
    needed a dozen, `float()`'s own forms among them); the old parsers kept beside the new one as the test's oracle
    (a second copy to keep); every test's `store` parameter renamed to a shared fixture's name (hundreds of lines for
    nothing a reader gains); the bundle as an artifact (above).
  - **Left:** the 18 functions over the limit besides `_migrate` (the benches' `main` 269, `simulate` 203 and
    `_simulate` 167; the rule reader's `_read_line_items` 155 and `_read_fields` 151; `jde.voucher_rows` 148...); a
    supplier template still reads dates in English and French only ("24. Januar 2014" is no field date: the
    digest's divergence, kept as it was); the slow list's older entries timed again on a quiet machine.
  - (`core/parsing.py`, `core/safe.py`, `core/csvio.py`, `core/audit.py`, `capture/normalize.py`,
    `capture/supplier.py`, `capture/reader/line_items.py`, `web/app.py`, `coding/line_tax.py`,
    `coding/validation.py`, `erp/jde.py`, `ops/cli.py`, `ops/doctor.py`, `.github/workflows/ci.yml`,
    `scripts/check.py`, `pyproject.toml`; `tests/core/test_parsing.py`, `tests/core/parsing_inputs.py`,
    `tests/meta/test_function_length.py`, `tests/meta/test_ci_workflow.py`, `tests/stores.py`, `tests/conftest.py`,
    `tests/webclient.py`, `tests/slow_tests.txt` and the test files whose copies went)
- **D-192** · **Store transactions nest, and `core` is the base layer: a write joins the transaction under way, the
  few that must commit on their own refuse in words; what the store keeps and compares by moves down into core, the
  rest of core's imports from above are listed with why** (scale goal, phase 7, C2; B-1995, B-1996).
  - **Why:** 29 writes began with a bare `BEGIN IMMEDIATE` and failed inside `Store.transaction()` or another write
    with SQLite's "cannot start a transaction within a transaction" (the digest's R4): an approval's 25 to 30
    commits could never become one. A `Store()` built inside a transaction committed it (R3). And `core` imported
    the layers above it 76 times (53 file and module pairs), so the base layer could not be read, tested or moved to
    another database apart from them.
  - **Nesting:** 21 sites take the lock with `dialect.begin_write` (BEGIN IMMEDIATE, or join the transaction under
    way): `add_user`, `set_role`, `deactivate_user`, `activate_user`, `change_password` (the new password),
    `reset_password`, `unlock_username`, `unlock_address`, `authenticator_secret`, `update_after_check`,
    `_recheck_duplicates`, `replace_proposal`, `create_export_batch`, `add_rules_applied`, `answer_manager_request`,
    `delete_invoice` (`keep_approved`), `final_approve`, `save_supplier_profile` (a snapshot), `forget_if_withdrawn`,
    `queue_imports`, `finish_import`. Called alone, each is what it was (`begin_write` is BEGIN IMMEDIATE when no
    transaction is open, which a bare one needed); inside one, it is kept with its commit and undone with its
    rollback. 8 keep their own, through `StoreCore._begin_alone` (`NestedWrite`, a RuntimeError in words, inside any
    transaction or write, before anything is done): the four queues' claims (`next_page_read`, `next_verification`,
    `next_azure_check`, `claim_import`: committed before the work starts, their heartbeat on another thread's
    connection must find them, and the write lock is never held through minutes of reading) and a login's tries
    (`authenticate` twice, `check_code`, `change_password`'s current password typed wrong: kept whatever comes next,
    D-101; a rollback would undo the count of the pause). `StoreCore.__init__` inside a transaction under way makes
    its schema on a connection of its own (`_conn(own=True)`): `executescript` commits first; nothing is due there
    (the caller's Store made it current), so it takes no lock. `tests/meta/test_nested_writes.py`: no bare `BEGIN
    IMMEDIATE` outside `dialect.py` (by the syntax tree, docstrings and comments left out; it finds the 29 on
    f468b0f), and `ALONE` lists the 8 with why (the list only shrinks).
  - **core below the rest:** moved down, under the names they had (their modules import them back, the same objects):
    `core/keys.py`, new (`po_key`; `norm_invoice_number`, `norm_tax_number`, `same_tax_number`, `BUSINESS_NUMBER`,
    `master_vendor_id`, `mask_account`, `IN_ERP`/`ADDED_HERE` from `coding/vendors.py`; `rules._plain` as `plain`;
    `search_keys`, `register_keys`, `_raw` as `raw_number` from `erp/search.py`; `supplier_key`; `line_pattern` and
    `LINE_GL`/`LINE_CC`/`LINE_FIELDS` from `ai/lessons.py`); `core/tax_setup.py`, new (the tax setup and the rate
    table: `TaxRate`, `TaxRateTable`, `TaxTreatment`, `TaxSetup`, `load_tax_mapping`, `rates_path`, and the names of
    the taxes, treatments and provinces the schema uses, from `coding/tax.py`, which keeps the checks); `core/memory.py`
    (`HISTORY`, `AUTONOMOUS_REVIEWER`); `core/paths.py` (`page_worker._pid_alive` as `pid_alive`); the bank accounts'
    columns into the part that writes them (`accounts.BANK_COLUMNS`, `banks.COLUMNS` imports it). 21 pairs and 34
    statements gone: `core/schema.py` and `core/reference_data.py` no longer import `coding` at load. The one test
    that patched `coding.tax.rates_path` to steer the store patches `core.tax_setup.rates_path`, where the store
    asks now. `tests/meta/test_core_imports.py`: `KEPT`, 32 pairs (file, module) with why; a new one fails, one no
    longer made must come off.
  - **What stays, and why:** business rules the store runs inside its own transactions, where a move would split
    them from the layer that owns them: the summary's facts (D-110: `bulk`, `taxreturn`, `accruals`, `insights`,
    `spend`), the tax vote an approval learns and schema 17's step (`line_tax`), the due date (`terms`), the
    duplicates checked again (`validation`), a supplier's autonomy and its template replayed (`capture.supplier`,
    `capture.workflow`), month-end's billed lines (`po.positive_lines`), the coding rules read back (`rules.Rule`), a
    guess's mark (`offline_coder.guessed`); a migration step (`old_readings`, D-89); the service's facts the backups
    need (`ops`: server mode and server.json, the business time zone, IT's allowed folders, the system log); words of
    the layers above (`audit`: C4 changes it in parallel, so only its import lines were touched; `lessons`'
    `code_label`); the demo's files read with the imports' parsers (`samples`); the hub's table definitions (D-189);
    an invoice stored before `tax_exempt` (`schema`, D-64).
  - **Alternatives:** a registry the upper layers fill at import (the summary or an approval worked out without it
    when nothing imported them first: wrong rows kept); every claim joining too (the heartbeat on another connection
    would not see it); the kept ones left bare and listed (SQLite's error again); skipping the schema in a nested
    `Store()` (a table with no version of its own, `bulk_jobs`, would be missed on an older database).
  - **Left:** the 32 pairs (a design change each: facts handed to the store, or the summary as a plug-in). A write
    called inside a read-only snapshot (`summary_snapshot`, `one_snapshot`) joins its deferred transaction: it takes
    the write lock at its first write, and fails with SQLite's "database is locked" when another process wrote since
    the snapshot began (it failed every time before).
  - (`ap_coder/core/store/{_base,users,lessons,purchasing,managers,invoices,page_queue,capture,imports,summary,
    transaction,accounts,backups,folder,learning,migrations,register,samples,vendors,__init__}.py`, `core/keys.py`,
    `core/tax_setup.py`, `core/memory.py`, `core/paths.py`, `core/schema.py`, `core/reference_data.py`, `core/audit.py`
    (an import line), `coding/{po,vendors,rules,tax,banks}.py`, `erp/search.py`, `capture/{supplier,workflow}.py`,
    `ai/{lessons,history,page_worker}.py`; `tests/core/test_store_transaction.py`, `tests/meta/test_nested_writes.py`,
    `tests/meta/test_core_imports.py`, `tests/core/test_store_perf.py`)
  - **After its review (B-1965, B-1966):** a Store() made inside this thread's write is not started up again when the
    database is current (its start-up's summary refresh would wait for the lock the thread holds), and refused
    (NestedWrite) when it isn't; the read snapshots roll back on an exception, so a write inside one is undone with it.
    The `_conn(own=)` flag went with it.
- **D-193** · **The review queue's lists and facts kept by invoice and brought up to date from the summary's log of
  the queue's changes; the Approved tab read off an index; the queue's reasons work each Pay from choice out once**
  (scale goal, phase 8, E1; changes how D-159 and D-167 keep the queue's lists and checks).
  - **Why:** with 100 people on 300,000 invoices, `/review` was 25% of the busiest web workers' CPU
    (`docs/archive/scale-2026-11/phase3c-load-100-people.md`). In its samples: the lists' rows read whole again
    (`list_invoices`' kept answer, 15% of the page) and their confidences and order read as rows at every ask (10%),
    the queue's checks worked out whole again (12%) and copied twice for each caller (5%), `attention` (19%), the
    Approved tab sorting a year of approvals (7%). Each check, approval or park moved the `listed` and `queue` topics
    (D-159), so every worker read the queue's 2,700 invoices again at its next `/review`. (The phase's list also named
    `summary_rows`: in that profile it is the Vendors page's workload, Insights, Sales tax and the quick search, not
    the queue.)
  - **A log the summary writes** (`queue_changes (seq INTEGER PRIMARY KEY, id)`, `_base.QUEUE_CHANGES`): each
    invoice whose queue row changed (`summary.queue_logged`: its row in the lists, `LISTED_COLUMNS` with the
    confidence a check writes; its facts in review, the `queue` topic, one the summary could not work out at each
    change; or its coming into the queues or leaving them), written by `_summarize` in the same transaction as the
    change. Every change of an invoice is worked into the summary there (plain SQL too: `summary_dirty`, worked out by
    the next read), so the log holds them all. The latest `QUEUE_CHANGES_KEPT` (20,000) are kept, pruned in the same
    write; the log never empties. `summary_logged` counts the refreshes that kept it, beside `summary_generation/all`.
    - Not triggers: a trigger on `invoices` or the summary can't tell whether the queue's row changed (the summary
      deletes and inserts each row), and a trigger of a new name makes every older version refuse this version's
      backups (D-159's check). Not `shared_answer` (D-169): every other process would still decode the whole answer,
      2,700 rows, after each change.
  - **The read** (`core/store/review_queue.py`, `_kept`): in one read transaction, one statement reads the database's
    `summary_id`, the refreshes made without the log (the generation less `summary_logged`: an older AP Coder on the
    same database, B-1811's case), the log's first and last change, and the invoices logged since the one kept. Only
    those are read again; all, when the database is another (a restore, a summary built again), the log no longer
    holds every change since, or an older AP Coder worked the summary out meanwhile. The caller's reads inside are of
    the same moment. Inside a write under way, or a read transaction, it is read afresh and not kept (D-159's rule).
    - **The lists** (`queue_listed`, through `list_invoices`): the rows of the three queues kept as one; each ask reads
      the ids of the asked lists in the queue's order off `summary_queue_order` (`json_group_array`: 0.8 ms, the
      confidences as rows 3.9 ms) and gives copies of the rows. A check's confidence is in the log: D-167 kept it out of
      the `listed` topic because a check comes between nearly every two asks; logged, a check reads one row again.
    - **The facts** (`Store.queue_facts`): what the queue reads of every invoice in review. `bulk.queue_checks` takes
      those it is asked (from `KEEP_FROM` as before) and works their findings out at each call (`_today`, decoded once
      in `_FOUND`); each caller gets one copy, not two.
    - The topics `listed` and `queue` move as before: an older AP Coder reading the same database follows them.
  - **The queue's reasons** (`review.attention`): the findings counted in one pass (`_to_fix`); each Pay from asked
    (the account, approved before, the currency, the vendor's last one) checked once a call (`opening_choice`,
    `blocking`, pure); each Pay from text decoded once (`_PAYS`, 16,384 texts, then all let go).
  - **The Approved tab** (`recently_approved`): read off the index `summary_recent`, in its order, not sorted out of
    every approval at each visit.
  - **No schema version:** a table and an index, made at every start where missing (`make_triggers` makes the log with
    the vendors' log, so a backup's copy being restored gets it before another worker writes there, D-174;
    `SUMMARY_SCHEMA` the index); an older AP Coder leaves both alone. `queue_rows`, `stale_checks` and
    `recently_approved` moved from `summary.py` into `review_queue.py` (996 lines left).
  - **Measured** (the bench's seed of 30,000 invoices, 2,491 in review from 968 vendors, 116 parked, 93 waiting for a
    second approval, two bank accounts in use; one request's CPU by `time.process_time`, medians of 12, the two codes
    in turn on the same database, the write made by another process before each request; d417b98 before;
    `e1/probe.py` in the session's scratchpad):

    | request | alone | after a check | after a park | after an approval |
    |---|---:|---:|---:|---:|
    | `GET /review` | 52-66 → 45 ms | 54-70 → 48-51 ms | 106-156 → 48-51 ms | 111-155 → 44-56 ms |
    | `GET /review?tab=approved` | 93 → 40 ms | | 136 → 41-44 ms | |
    | `GET /review/{id}` | | 34-36 → 32-37 ms | 32-36 → 31-34 ms | |
    | `POST /review/{id}/check` (noise) | 23 → 21 ms | 20 → 22 ms | 20 → 22 ms | |

    - On one connection after a park: `list_invoices` 30 → 2.4 ms, `queue_checks` 30 → 6.5 ms (the checks stamp is
      2 ms of it), `attention` 23 → 13 ms; the Approved tab's rows 40 → 2.4 ms (27,000 approved).
    - The writes: a check's confidence 3.75 → 4.04 ms (the log's row), a park and its unpark 8.1 → 8.2 ms, 3,000
      invoices worked into the summary 1.53 → 1.40 s (each one's topic keys made once, not once a topic).
    - At 300,000 the queue's size sets these (about 2,700 in review), not the database's, but for the Approved tab's
      sort (ten times the approvals: about 0.4 s a visit before). By the phase 3 part 3 profile's samples, the queue's
      25% of the busiest workers' CPU comes to about 12%: what is left is `attention`, the order read, the copies,
      the template and the 50 rows.
  - **Proven the same:** random writes by two workers (each its own rows kept), plain SQL and an older AP Coder (an
    invoice added, checked, parked, approved, rejected, reopened, deleted, exported; its findings, coding, vendor or
    status changed; the settings its checks are made against, the vendor master, a bank account; a write undone, a
    restore), with a log of 4 changes and of 20,000: every list, the queue's checks (of the queue, part of it, every
    invoice) and every reason equal a fresh computation (`tests/core/test_queue_changes.py`). Seven mutations (the
    confidence not logged, pruning not seen, a row gone left kept, the older AP Coder or a restore not seen, kept
    inside a write, no log in a backup's copy) each fail it. `scripts/same_behaviour.py --ref d417b98`: approve,
    pipeline and tax the same; migrate differs by the table, its index and the count alone (22 databases, compared
    with them set aside). The queue's pages byte for byte: a worker of each code side by side on the seed, 9 views of
    the queue, Previous and Next and bulk approval's candidates drawn after each of 120 writes by a third process: 1,220
    files, none differs (the ages "3 minutes ago" masked: the two read the clock a moment apart; `e1/identity.py`).
    The base code passes the same property test, adapted to its kept answers: none was wrong, only worked out again.
  - **Not done:** the findings counted at each visit (5 ms: the queue's 30,000 findings); the checks stamp (2 ms: the
    GL accounts and cost centers joined and hashed at each ask, `accounts.py`); `last_pay_froms` (2.5 ms, a seek a
    vendor: an approved invoice's change is not in the queue's log); the template and the 50 rows' codings.
  - (`core/store/{review_queue,summary,_base,invoices,__init__}.py`, `coding/bulk.py`, `web/pages/review.py`,
    `docs/HOW_IT_RUNS.md`; `tests/core/test_queue_changes.py`, `tests/core/test_kept_answers.py`)
- **D-194** · **Month-end's billing lines kept by invoice, not by vendor; the quick search reads the ERP register
  through an FTS5 index the register's writes keep; the Vendors page's workload worked out once for every process**
  (scale goal, phase 8, E2; changes D-177; every output the same).
  - **Why (phase 3 part 3's profile at 300,000 invoices, 100 people):** month-end's vendors' rows 15% of the web
    workers' CPU (`billing_lines` 10.3%, `keep_answer` 4.9%: each vendor's billing lines one row of `report_answers`,
    read whole and written whole again, the biggest 7 MB, once a sixteenth of it was decoded since, each read
    decoding those again until then); the quick search 4.6% (`erp_hits`: SQLite's `instr` over each of the
    register's 600,000 rows for a word); the Vendors page's workload 4.2% (worked out by each web worker itself).
  - **Month-end, by invoice:** `billing_facts` (a vendor's invoice and what month-end reads of it, [date, lines] as
    JSON) under the vendor's stamp in `billing_vendors` (`kept`: the database's `summary_id`, this AP Coder's version
    and `BILLING_FORMAT`; `logged`: the last change of the vendors' change log its rows are as of; `counted`: the
    vendor's count of changes then), both made by `_SCHEMA` at every start (no version of their own, as `past_lines`).
    `billing_lines` reads the stamps, the counts and the log in the same moment as the invoices (B-1812): a stamp
    holds when kept by this database and version and the count has not moved, or when the log still holds every
    change since and the count moved by as many as the log names (B-1811); each row of its invoices is then taken
    but those the log names, else none is. After the read a vendor's rows are written in one write, only while its
    stamp is still the one read (a slower process never writes over a later one): the invoices changed since removed
    (every one when the stamp did not hold), those decoded written (JSON made then, a vendor at a time), the stamp
    moved on. A change rewrites its own invoice's row and the vendor's stamp, not the vendor's row; nothing waits for
    a sixteenth (`BILLING_REWRITE` gone). The rows D-177 kept (`report_answers` `billing/<key>`, about 63 MB at
    300,000) go at the next start, looked for first (a start with none takes no lock: B-1996's test found the plain
    delete waiting 30 s for it).
  - **The quick search, through the register's index:** `register_search`, FTS5's trigram index of each register row's
    `plain_name` and `raw_number` by its rowid, where `summary.fts_available()` (as the invoices' summary's since
    D-110). FTS5 is part of SQLite, no dependency: CPython's own builds have it (Windows: `PCbuild/sqlite3.vcxproj`
    defines `SQLITE_ENABLE_FTS5`, with SQLite 3.38 or newer since Python 3.11.0; macOS's installer too; the trigram
    tokenizer is SQLite 3.34+), and so do the Pythons here (3.11, 3.12, 3.13 on SQLite 3.45.1 and 3.53.1); a Python
    without it reads the register as before. Kept by the register's own writes, in their transaction (a file imported,
    added or in place of the register; cleared; JD Edwards' vouchers, B-1918's chunks, and the sweep): the rows added
    are those above the largest rowid before the write (SQLite gives a new row the largest plus one), a bill removed
    leaves the index, a register cleared makes it again. In step while the setting `register_search_step` is the
    register's count of changes (`changes/register`, which every version's triggers keep): a write that did not keep it
    (an older AP Coder, plain SQL, a computer without FTS5, SQLite's largest rowid taken) leaves it behind, and the
    search reads the register itself until the next start builds it again (a step at every start, after those that write
    the register). `erp_hits` gives the store the same condition twice: as before, and narrowed by MATCH for each word
    and each number whose every way to match is a text of 3 characters or more (not an amount), the exact `instr` kept
    after it; `erp_register_rows` reads the second while the index is in step, checked in the same moment. The same rows
    in the same order (`ORDER BY rowid`), each scored as before (`_score`).
  - **The Vendors page's workload:** `vendor_workload` through `shared_answer` (topic `workload`, `WORKLOAD_FORMAT`),
    as D-175 did for Spend: worked out by the first process asking after a change, taken by the others; each caller
    gets its own copy of each row.
  - **Measured** (a seed of 30,000 invoices and 60,000 register rows, `bench seed --history 0.1`; process CPU):
    - Month-end after each of 1,200 changes to the biggest vendor's invoices (each another one) by another process,
      the vendor made as big as 300,000's (17,889 billing invoices): `billing_lines` median 127.8 → 108.6 ms (mean
      132.7 → 112.4), received-not-invoiced 0.405 → 0.387 s; its writes: the 7 MB row (92 ms, the write lock held
      through it) every 1,118 decodes → at most 0.5 ms a change. The 1,200 answers the same, digest for digest. At the
      seed's own size (2,333 billing invoices) the same within the noise (0.215 → 0.230 s, `billing_lines` 13.3 →
      14.6 ms): reading a vendor's rows costs what its whole row did (95 ms of JSON for 17,889 invoices); what this
      saves grows with the vendor: the writes and the re-decoding (at 300,000 up to 1,118 codings a read).
    - The register's SQL alone, at 600,000 rows (the seed's x10): median 73.6 → 25.2 ms over 72 searches (2 slower:
      words in a fifth of the rows, "contractors" 93 → 138 ms); `erp_hits` median 193 → 113 ms, a vendor's word 226 →
      169 ms, a number or a rare word about 100 → 5 ms; at 60,000 rows 19.5 → 15.8 ms. 77 searches' hits the same.
      The index: 3.5 s to build at 600,000 rows (once, the first start), about 119 MB (80 the index, 31 its copy of
      the texts, 7 the sizes). The register's writes it adds to, at 600,000 rows: a JD Edwards page of 250 new
      vouchers 8.7 → 9.7 ms, the same page seen again 7.8 → 7.6 ms, a file of 1,000 rows 20.9 → 21.9 ms.
    - The Vendors page after a change, two workers: the first 0.31 s (works the workload out), the second 0.289 →
      0.148 s; the workload's own work 0.11 s at 30,000 invoices; the pages byte for byte, in either code (the
      rail's counts taken as they are).
  - **What is left:** month-end still reads all of a changed vendor's rows at each work-out (the JSON); a common
    word's hits (a vendor word in 100,000 register rows: 0.9 s) are each still read and scored in Python: counting
    and paging them in SQL is the next step.
  - **Alternatives:** triggers on `erp_invoices` keeping the index (whoever writes; but a backup carrying triggers
    not the summary's is refused, so an older AP Coder could not restore a newer backup, and a computer without FTS5
    could not write the register at all); a words table (a word is matched anywhere in a name: trigrams anyway);
    FTS5 with external content (31 MB less, but out of step it fails its deletes) or contentless with deletes (SQLite
    3.43+: not on every Python AP Coder supports); month-end's rows held in each process (95 MB each at 300,000,
    D-177); one write for every vendor's rows after a read (the write lock held through the biggest's).
  - **After its review (B-2039 to B-2041):** the index made again (a start, a clear) takes every row, rowid 0 and
    below too; a search text with a NUL in it is matched exactly (FTS5 reads its MATCH text up to a NUL: the Search
    page failed); each word's condition on the register is one level, as on the summary (from 499 words SQLite's
    depth limit). The review's probes found month-end's kept rows equal to a fresh computation over 2,350 random
    changes in two Pythons and with two processes, and the indexed search equal to the exact one over 1,375 searches
    (non-ASCII letters, ligatures, accents, FTS5's own characters).
  - (`ap_coder/core/store/{purchasing,register,migrations,_base}.py`, `erp/search.py`, `erp/insights.py`;
    `tests/erp/test_accruals.py`, `tests/erp/test_invoice_summary.py`, `tests/erp/test_search.py`,
    `tests/core/test_kept_answers.py`)
- **D-195** · **What fills a web worker, each per-process cache within its bytes (1 GB for a web worker in all), two
  malloc arenas, and a memory slope for an hour's run** (scale goal, phase 8, E3; B-2025 to B-2027).
  - **Why:** target 1 includes "memory flat for an hour" with 100 people on 300,000 invoices, and each web worker
    reached 1.65-1.77 GB at 300,000 against `WORKER_MB` 1,500. Most caches were bounded by entries (64 answers, 32
    vendors, 131,072 descriptions) whatever an entry held, a few by nothing at all, and nothing said what a worker
    holds in all.
  - **Measured** (a 60,000-invoice seed, `bench seed --invoices 60000`; one worker in a process of its own, driven by
    `bench load`'s people, 20 for 180 s, its background work in another; then each cache walked object by object,
    `sys.getsizeof` over what the gc says it refers to, each object counted once, in the order below; the lru caches
    by `tracemalloc`, filled with a big company's lines):

    | what a web worker keeps | at 60,000, measured | at 300,000, projected | its bound now |
    |---|---:|---:|---|
    | the learning memory (`_base._FEEDBACK`) | 66.6 MB | 67 MB | `Bounded` 96 MB |
    | the GL suggestions' index (`suggest._MEMORY`) | 9.5 MB | 10 (54 *) MB | 104 MB, 2 KB a lesson |
    | the examples' patterns (`memory._AGGREGATED`) | 18.9 → 9.5 MB | 19 (24 *) MB | 28 MB, 512 B a lesson |
    | vendors' older lessons (`_base._PAST_MEMORY`) | 0.04 MB | 78 MB | `Bounded` 80 MB |
    | their extended indexes (`suggest._PAST`) | 17.1 MB | 48 MB | PAST_BYTES_KEPT |
    | their lines' patterns (`suggest._OLDER`) | 0.7 MB | 12 MB | PAST_ROWS_KEPT |
    | older lessons by line (`_base._PAST_LINES`) | 0.1 MB | 64 MB | PAST_LINES_BYTES |
    | vendors' lessons (`_base._VENDOR_LESSONS`) | 0.2 MB | 2 MB | `Bounded` 8 MB |
    | the pages' answers (`summary._MEMO`) | 36.9-43.5 MB | 60 MB | `Bounded` 112 MB |
    | the reports' answers (`reports._KEPT`) | 2.8 MB | 15 MB | `Bounded` 32 MB |
    | vendors' invoices (`vendors._VENDOR_ROWS`) | 8.9-14.3 MB | 53 MB | `Bounded` 64 MB |
    | JD Edwards' live check (`jde_sync._CACHE`) | (not set up) | grew all day | `Bounded` 32 MB (B-2025) |
    | invoices whose file is gone (`_base._NOT_FOUND`) | none | grew for ever | 50,000: 24 MB (B-2026) |
    | 29 pure functions' caches (`lru_cache`) | 18 MB ** | 400 * MB at the old sizes | maxsize: 249 MB |
    | the rest (reference data, findings texts, accruals...) | 6 MB | 20 MB | 62 MB |
    | **in all** | **about 180 MB** | **450 (750 *) MB** | **1,017 MB** |

    \* with a big company's vocabulary: every lesson's description its own (the seed has 41). At 60,000: the learning
    memory is 50,000 lessons and Learning's 2,000; the extended indexes 32 vendors', older lessons 6 and 51 vendors';
    the pages' answers 16 (the vendor list 15.6 MB, master numbers 6.7, vendor ids 6.3, the queue's checks 4.4 and
    lists 3.1, vendor totals 2.9, terms 2.7); vendors' invoices 51-53 vendors'. \*\* the walk can't see an
    `lru_cache`'s answers: its entries at the size measured of one (the walk: 170.5 MB in all before, 172.9 after).
    The worker's RSS 714 → 695 MB.
    - **The rest of the RSS** is not the caches': of 714 MB, 76 MB are the libraries, 321 MB pymalloc's arenas (242 MB
      of objects in them, 45 MB free blocks), and about 250 MB glibc's: each thread could make a malloc arena of its
      own (8 a core), each keeping what a request's peak freed there; `malloc_trim` gave back only 34 MB.
  - **Each cache within its bytes** (`core/caches.py`):
    - `size_of`: what a value holds, about: `sys.getsizeof` of it and of what it refers to (by the gc: a dict's keys
      too, a mapping proxy's dict), each object once; a container of more than 32 items by 32 of them spread over it,
      the first paying for what they share. 20,000 rows in about a millisecond (the whole walk: 0.3 s), within 5%;
      values shared by many items make it count more (the queue's checks: 14.7 for 4.4 MB), rarely less.
    - `Bounded`: a cache's dict that sizes each entry as it is kept (`size_of`, or the owner's), lets go of the ones
      used longest ago (kept or read with `get`) while over its bytes, the latest always (alone when bigger), and
      keeps the owner's own bounds and locks. On `summary_memo`'s answers (112 MB), the reports' (32), the learning
      memory (96), vendors' older lessons (80) and lessons (8), vendors' invoices (64), JD Edwards' live check (32).
    - `RowsCache(most, row_bytes)`: the memory's index and patterns keep the latest always and another only while
      both fit, counted a lesson at a time (2 KB and 512 B, measured with every description its own): `size_of` on an
      index takes 100 ms, an approval's update of it 8.
    - The pure functions' caches, sized by their bytes: the descriptions' words 131,072 → 65,536 (`keys`,
      `_token_set_short`: the memory holds 50,000 lessons at most), word pairs 262,144 → 131,072, PO description
      pairs 65,536 → 32,768, printed spans 65,536 → 16,384, the suppliers' counts 65,536 → 16,384. A miss costs
      microseconds (a description's words, a pair compared), the answers are the same.
    - `CACHES`: every per-process cache, "module:name", with the bytes it holds at most and how; 1,017 MB for a web
      worker (not a laptop's page pictures, kept on disk on the server, nor a scan's), within `WORKER_CACHES_MOST`
      (1 GB; 1,050 MB with D-193's two, below) and so within `WORKER_MB` with room for the requests under way.
    - `tests/meta/test_caches.py`: what a module keeps at its top level (an empty dict, set or list it fills, a
      container or cache made by a call, an `lru_cache`) is in `CACHES` or in its `STATE` (locks, threads, flags: what
      bounds them); a `Bounded` or `RowsCache` declared with its own bytes; the web worker's sum within 1 GB.
      `tests/core/test_caches.py`: each `Bounded` filled with 300,000 invoices' answers (8,000 rows, 6 MB each) to twice
      its bytes, `summary_memo` and `shared_answer` through their own code (the answer used kept), each pure
      function's cache full of a big company's lines, the index and patterns of 10,000 such lessons.
  - **Two malloc arenas** (`web/server.py fewer_malloc_arenas`): in server mode on Linux the server's process
    (`mallopt`) and those it starts (`MALLOC_ARENA_MAX`: the workers, the background and capture processes) keep two;
    one set by IT is left. The same probe on the code before the bounds: 714 → 536 MB, pages p50/p95 0.098/0.80 →
    0.095/0.75 s. glibc's alone: on Windows and macOS nothing is set or called (`sys.platform`), and their workers
    keep what they kept. Windows' C runtime allocates from the one heap of the process, with no arena a thread, and
    macOS's malloc gives its free pages back by itself; there pymalloc's own arenas (the same on all three) and the
    caches' bounds are what count. A Linux without glibc (musl: no `mallopt`) is left as it is too.
  - **Measured after:** the probe's worker 695 MB (the bounds don't bind at 60,000: its caches 172.9 MB). `bench load
    --users 20 --duration 180 --workers 1` (one process, its background work in it): 972 → 606 MB (peak 991 → 606),
    pages p50 151 → 118 ms, p95 0.85 → 1.15 s, actions p95 565 → 439 ms (one run each, the machine shared: the
    routes' p95 move both ways).
  - **An hour's flatness:** `bench load --sample-every 10` samples each server process's RSS every 10 s, and the report
    says its slope (MB a 10 minutes, least squares) over the run and over its second half (`rss_slope`), once its
    caches have filled; `--max-slope MB` fails a run over it. 15 minutes on the 60,000 seed (`--users 20 --duration
    900 --ramp 30 --workers 2 --sample-every 10`), twice with all of this (RSS MB first → last (peak); MB a 10
    minutes over the run, **over its second half**):

    | process | first run | its slope | second run | its slope |
    |---|---|---|---|---|
    | web worker 1 | 250 → 561 (610) | +92.8, **+17.6** | 132 → 556 (556) | +93.3, **+25.0** |
    | web worker 2 | 132 → 537 (537) | +92.8, **+9.7** | 171 → 570 (570) | +93.9, **+16.9** |
    | the background process | 118 → 148 (154) | +9.5, **-3.8** | 120 → 148 (148) | +9.9, **+4.7** |
    | the two capture processes | 134 → 318, 134 → 315 | **+1.9, +1.3** | 134 → 317, 134 → 315 | **+1.2, +2.1** |

    The workers are at about 500 MB after 2 minutes, then grow 30 to 60 MB in the 13 that follow, more and more
    slowly (the second run's worker 2: 1.9 MB in its last 5 minutes): at 60,000 the caches are far from their bounds,
    still filling with each vendor asked for. At +25 MB a 10 minutes an hour adds 150 MB, within `WORKER_MB`; phase
    8's hour at 300,000 says whether the second half is flat once they are full. Pages p50 93 and 80 ms, p95 535 and
    683 ms, actions p95 106 and 191 ms, no 5xx. The second run, started 6 minutes after the machine restarted, while
    another agent's tests filled the shared cores, had 5 requests over 30 s in its first minute (the server's own 8 to
    45 s, as each worker first built its caches); two 180 s runs straight after, the database warm and then let go
    from the disk's cache, had none (the worst 10 s's p95 443 ms and 1.40 s).
  - **Alternatives:** one shared budget for all caches (one bigger answer evicting another cache's; harder to reason
    about); bounding by entries alone (what an entry holds grows with the database); measuring each value whole (0.3
    s for 20,000 rows); `malloc_trim` after requests (34 MB at the end of the run: the arenas' memory is fragmented,
    not free at their top); `PYTHONMALLOC=malloc` (not tried: pymalloc's arenas held what is alive).
  - **Left:** the requests' own peaks (4 threads, about 110 MB each at 100,000, D-153; more at 300,000) are what
    remains of 1.65-1.77 GB: the queue's lists and month-end's rows (phase 8's other items) shrink them. Not measured
    at 300,000 here (the 300,000 seed is another run's). The lru caches are bounded by entries of a measured size,
    not by their bytes (an lru_cache can't be sized from Python). A few caches live in objects, not modules
    (`app.state._needs`, 4,096 entries; a session's memo, one request): not in `CACHES`.
  - (`ap_coder/core/caches.py`, `core/memory.py`, `core/store/{_base,summary,reports,learning,vendors,invoices}.py`,
    `ai/{suggest,line_match}.py`, `coding/po.py`, `capture/supplier.py`, `erp/jde_sync.py`, `ops/system_log.py`,
    `web/server.py`, `bench/load.py`; `tests/core/test_caches.py`, `tests/meta/test_caches.py`,
    `tests/erp/test_jde_sync.py`, `tests/ops/test_system_log.py`, `tests/web/test_server_capacity.py`,
    `tests/bench/test_bench_load.py`, `tests/core/test_kept_answers.py`)
  - **With D-193 (merged beside it):** the review queue's two caches are listed too: its lists and facts kept by
    invoice (`review_queue._KEPT`, 16 MB: two databases', about 1.1 KB an invoice in the queue, 5.4 MB for the
    100,000-invoice seed's 3,036 listed and 2,122 in review; `KEPT_MOST` 16 -> 4, a company server has one) and the
    Pay from texts decoded (`review._PAYS`, 6 MB full). A web worker's caches come to 1,039 MB; `WORKER_CACHES_MOST`
    is 1,050 MB, 0.7 of `WORKER_MB` (the test's limit).
- **D-196** · **server-update's safety copy is made while the old version serves and brought up to date once it is
  stopped, with only what was written since: the downtime no longer grows with the database** (scale goal, phase 8,
  E4; B-2035).
  - **Why:** on GitHub's Windows runner (`windows-server-update`, 20,000 invoices, 662 MB) the update was down 38.8 s,
    33.5 s of it the safety copy, a whole copy made with the service stopped (D-188): about 50 s per GB there, so a
    year's 10 GB (25,000 invoices a month at 34 MB per 1,000) would be down about 8 minutes, past the 5 of target 5.
  - **Made ahead** (`core/store/backups.py` `CopyAhead`, from `server_update.copy_ahead`, step 6, after the
    self-check, the marker still at `prepare`): with the backups' lock held (`backup_files.exclusive`: no backup or
    restore meanwhile), the write lock is taken for a moment (`BEGIN IMMEDIATE`), a `wal_checkpoint(PASSIVE)` that
    wrote the whole log into the file is waited for (tried again every 0.2 s for `HOLD_WAIT`, 60 s), and a read
    transaction begun then. While it lasts SQLite writes nothing into the database file: a checkpoint stops at the
    oldest reader's snapshot (sqlite.org/wal.html: it "must stop when it reaches a page in the WAL that is past the
    end mark of any current reader"; in wal.c a backfill needs the exclusive `WAL_READ_LOCK(0)` this reader holds), and
    this reader's snapshot is the file itself, so what the old version writes stays in the log. The file is then
    copied as it is, flushed to the disk; SQLite's lock-byte page (from byte 2**30, in a database over 1 GiB) is
    written as zeros and never read: it holds no data, and on Windows bytes of it are locked by whoever opens the
    database, so a read of them fails. The data folder's files are listed and the new ones kept then too
    (`backup_files.snapshot(..., database=db)`: the invoices' files named by the live database, since even a
    read-only open of the copy would make its `-wal` and `-shm`). The cost: while the copy is made (a minute per GB
    on a slow disk) the log grows by what the old version writes, never written into the file before the stop (tens
    of MB in minutes of a busy day; the room check keeps `BACKUP_RESERVE`, 512 MB, to spare), and no backup or restore
    runs (the daily one waits for its next check).
  - **Brought up to date** (`safety_copy(db, label, ahead)`, step `safety-copy`, the service stopped): the file's size
    and time checked as they were, its `-wal` copied beside the copy, the hold let go, and the copy opened: SQLite's
    own recovery reads that log, `wal_checkpoint(TRUNCATE)` writes it in, `journal_mode=DELETE` makes it one file. A
    database file and its log are what SQLite reads as the database, so the copy is the database as it was the
    moment the service stopped (a frame cut short at the log's end is dropped by the recovery, as after a crash). Then
    renamed, the backups folder flushed off Windows (`_flush_folder`: the copy is on the disk before the migration),
    the files listed again (only what changed since step 6 is read), and the database's SHA-256 written in the list
    once the new version answers (`backup_files.hash_database`; until then `check(deep=True)` compares the size). Its
    time is what was written while the copy was made, not the database's size.
  - **D-188's guarantee and the power cuts:** unchanged, and no step is new. The safety copy is whole and on the disk
    before the migration, as before. A cut while the copy is made ahead is `prepare` (rule `old`: the old version
    serves on its own database, only ever read; what is left in backups, the copy, its `-wal`/`-shm`, its list, is
    removed by `_drop_cut_copy`); while it is brought up to date, `safety-copy` (`old`: the same); in the migration,
    `migrate` (`back`: the copy put back holds what the old version did while it was made ahead). Each is cut for real
    (a `BaseException` inside the copy, inside the bringing up to date, in the migration) and dealt with by
    server-update and by the service's start, the old version's last work kept each time
    (`tests/ops/test_update_recovery.py::test_an_update_cut_short_in_its_safety_copy_made_ahead_loses_nothing`). The
    file is held still while another connection writes 300 pages and checkpoints, PASSIVE and TRUNCATE
    (`tests/core/test_backups.py::test_a_copy_made_ahead_holds_what_is_written_until_it_is_finished`, which fails
    without the read transaction), and the safety copy has what was done between the copy and the stop, after an
    update, a rollback and `--back` (`tests/ops/test_server_update.py`).
  - **When it can't be held still** (a report that reads in one transaction for over a minute, a database not in WAL
    mode) the update says so and makes the copy whole once stopped, D-188's way; also when the file's size or time
    changed meanwhile after all (never a copy of a file that moved). A copy ahead that fails (a full disk) stops
    nothing: "not updated".
  - **Measured** (Linux, 4 cores, `server_update_downtime.py local`, before → after; 20,000 invoices, 669 MB, on the
    disk; 120,000 invoices, 2.45 GB, made by duplicating a seed's invoices, events and feedback, in RAM since the
    shared disk was short of room): 669 MB down 11.9 s → 5.9 s, the safety copy 6.2 s → 2.0 s (6.2 s ahead, while
    serving); 2.45 GB down 15.9 s → 6.0 s, the safety copy 10.4 s → 1.9 s (8.3 s ahead). What is left at the stop is
    the data folder's files listed again, about 1.8 s per 20,000 invoice files there (`_elsewhere` now resolves each
    invoice's path once, not once per folder with the folders again each time: 2.1 → 1.75 s; same result). The
    Windows job records `copy_ahead_s` too.
  - **Alternatives:** the old database file kept as the safety copy (a rename is instant) and a copy of it migrated
    (that copy is the same whole copy in the downtime); SQLite's backup API while serving, then again once stopped
    (it copies every page each time, or starts again when the database changes); a snapshot held at the stop and
    copied while the new version serves (less downtime still, but until that copy is made a power cut leaves no way
    back unless nothing opens the database first, and a rollback has no copy to put back); `VACUUM INTO` (a whole
    copy); SQLite's session extension (not in Python's sqlite3); no copy when the schema is unchanged (the new
    version still writes, and `--back` would have no database of the stop); a hard link (one file: the new version's
    checkpoints write into it); comparing pages after the stop (reads the whole database).
  - **After its review (B-2036, B-2037):** the copy reads the database through one handle, opened after the hold and
    closed only after it: on Linux and macOS closing any handle on a file lets go of every lock the process holds on
    it (POSIX), the hold's own too, so another process could write the log into the file held still, and with
    SQLite 3.45 (Ubuntu 24.04's) the old version's last connection then deleted a log with its last writes in it,
    lost from the database and the safety copy (40 of 41 rows in the review's probe). A copy that can't be brought up
    to date (made whole at the stop) is said. An update that goes back, and the recovery that puts the copy back, read
    in its SHA-256: the old version's `backup check` took a list without one for a damaged copy.
  - **Left:** the files listed at the stop are one walk with two stats a file (more on Windows); `--back`'s own safety
    copy is still whole (the update's downtime is the target); the Windows job's number with this is its next run's.
  - (`ap_coder/core/store/backups.py`, `core/store/backup_files.py`, `ops/server_update.py`, `ops/update_recovery.py`,
    `scripts/ci/server_update_downtime.py`, `docs/SERVER.md` section 11; `tests/core/test_backups.py`,
    `tests/ops/test_server_update.py`, `tests/ops/test_update_recovery.py`)
- **D-197** · **A settle that grows with the answer's own work: ten times as long as it lasted, 15 minutes at most;
  the Vendors page's workload and totals settle too** (scale goal, phase 8).
  - **Why:** phase 8's load test, 100 simulated people on 300,000 invoices (4 web workers, 4 cores), gave pages p95
    1.78 s, p99 6.76 s and actions p95 1.10 s over an hour (targets 1.5 s, 3 s, 1 s). In a web worker's profile 46% of
    its CPU was `shared_answer` working answers out again: Spend's figures 27%, the Vendors page 20% (its workload, no
    settle; its totals through `summary_memo`, none either), then Sales tax, Insights, month-end, Learning. D-164's
    settle is a fixed number of seconds: Spend's figures took about 20 s at 300,000 against their 30 s, so with a
    change every second some worker worked them out almost all the time, and its other requests waited behind its
    GIL (server p95s: month-end 23 s, Spend 20 s, Vendors 8.4 s, Sales tax 7.8 s, Insights 6.3 s).
  - **What:** each kept answer records how long its work lasted, and the settle it is held to is the longer of the
    caller's `settle` and `SETTLE_SHARE` (10) times that (`core/store/reports.py` `settle_for`): 20 s of work is
    taken for 200 s after a change, so working it out again costs about a tenth of a core at most, however big the
    database. On a laptop's database the work takes milliseconds and nothing changes.
    - `shared_answer`: the work is timed by `reports.wall`, the clock `made` is in, around `compute`, and travels with
      the answer to the other processes: `report_answers.lasted` (REAL seconds, added by a migration step with no
      version of its own, `_answer_work`), written by `keep_answer`, read by `kept_answer`. Both `holds` and `before`
      (B-1834, B-1838: the answer before, shown while another works it out, never older than its settle and
      `ANSWER_WAIT`) use the grown settle; one of another database (`summary_id`: a restore) or another format is
      never taken, as before; the stamps, holds and waits (B-1807, B-1914, B-1919) are as they were.
    - `summary_memo`: the same, by `monotonic` within its process (each kept answer of `_MEMO` holds its work).
    - **The cap,** `SETTLE_MOST`: 15 minutes. A work made long once (a cold cache after a start, a clock moved
      forward meanwhile) holds its answer that long after a change at most; the next work, shorter, sets its own.
    - **Not known:** an answer kept before this (no `lasted`: the same AP Coder version before the change) settles
      for the caller's settle alone; so does one whose clock went back during the work. An exact answer (no
      `settle`) is never settled, whatever its work.
  - **What settles,** only what a page shows and nothing a check, an approval, an export or a posting decides with:
    once for every process (`shared_answer`), Spend's figures and currencies (30 s), month-end's received-not-invoiced
    lines and invoices not in the ERP on its page (30 s; its CSV exact), Sales tax (30 s), Insights' figures and
    operations (60 s), Learning's (60 s), the recurring vendors (60 s, the queue's late pill too) and, new, the Vendors
    page's workload (`insights.vendor_workload`, by its own work alone: `OWN_WORK`, B-2044); in each process
    (`summary_memo`), the rail's counts (`status_counts(settled=True)`, 5 s) and, new, the Vendors page's counts,
    spend and dates by vendor (`vendor_summaries(settled=True)`, `VENDORS_SETTLE` = `OWN_WORK`: ten times their work,
    no floor, so a laptop's are exact). Exact: the duplicate audit's pairs, month-end's CSV,
    the vendor lists of Statements and the JD Edwards worksheet, a vendor's controls (`/vendors/save`), and every
    other kept answer.
  - **The Vendors page** only shows its workload and totals; a vendor's hold, numbers and notes are read afresh from
    the vendor list at each visit. Two workers give the same page byte for byte when nothing changed since they worked
    it out (the workload is one answer for every process; the totals are each worker's, so while changes come one
    may be up to its settle behind the other). A vendor opened (`?open=`) that is not in the settled totals (its
    first invoice came after them: a link from another worker's page) is looked up in the exact ones.
  - **Measured** (a 60,000-invoice seed, `bench seed --invoices 60000`; one web worker, a TestClient asking one page a
    second, Spend, Vendors and Insights in turn, while another process approved an invoice every second for 3
    minutes; the first visit of each, cold, not counted; before: 2263422):

    | one web worker, 3 minutes | before | after |
    |---|---:|---:|
    | its CPU | 52.8 s (29% of a core) | 15.4 s (8.6%) |
    | Vendors page, p50 / p95 | 0.70 / 0.80 s | 0.056 / 0.19 s |
    | its totals / workload worked out | 60 / 60 times (15.9 / 21.2 s) | 2 / 2 times (0.5 / 0.7 s) |
    | Spend's figures worked out (1.4 s each) | 5 times | 5 times |
    | with Spend's work made 20 s long, as at 300,000: CPU | 152 s in 186 s (82%) | 9.3 s in 180 s (5%) |
    | ... Spend worked out, its p95; requests served | 6 times, 21.5 s; 166 of 180 | none, 0.16 s; 180 |

    At 60,000 Spend's work (1.4 s) and Insights' (0.2-0.4 s) are under a tenth of their settle: they settle as
    before. The 20 s row adds a CPU loop of 20 s to each of Spend's works (the probe's own, in both versions): its
    answer of 21.4 s settles for 214 s, so the 3 minutes after the first never work it out again.
  - **Alternatives:** a longer fixed settle for each (Spend 300 s: on a small database every change would show
    minutes late for nothing); the big answers worked out by the background process on a timer (every period anyone
    might ask, looked at or not); the work's CPU time (`thread_time`) rather than its wall time (under load the wall
    time is longer, so the settle grows most when the workers are busiest, as it should); the work kept in the
    answer's stamp or value instead of a column (the stamp is compared whole and read by SQL at a restore's clean-up,
    and a value from before could not be told apart); the Vendors totals shared by every process (every vendor's row
    through JSON in each worker; each keeps its own, as D-162).
  - **After its review (B-2043 to B-2045):** an answer's work time is its work alone (not the wait for another's
    work before it); the Vendors page settles by its own work only (it was exact before: a laptop's person saw their
    own change a minute late); a restore's copy gets `report_answers.lasted` before it goes in (a page asked while an
    older backup was restored failed). A session's memo (B-2042) holds 64 MB, so a vendor of up to about 70,000
    invoices is read once in a check (300,000 invoices' biggest: 23,000).
  - **Left:** the Vendors totals are each worker's own: 4 workers each work them out at most once a settle (0.24 s
    at 60,000). An older AP Coder of the same version updating a kept answer (during an update, both running) leaves
    the `lasted` of the one before. Month-end's page shows three parts each settled by its own work (lines received
    and not invoiced, invoices not in the ERP, the recurring vendors): at 300,000 invoices one may be a few minutes
    behind another, so an invoice that moved between two parts meanwhile shows in both or neither for that long (it
    was a minute at most before); its CSV, what is booked, is worked out exactly.
  - (`ap_coder/core/store/reports.py`, `core/store/summary.py`, `core/store/migrations.py`, `core/store/vendors.py`,
    `erp/insights.py`, `web/pages/vendors.py`, `docs/HOW_IT_RUNS.md`; `tests/core/test_kept_answers.py`)
- **D-198** · **A settled answer worked out again by the background process, never by a request that has one to
  show** (scale goal, target 1's p99).
  - **Why:** phase 8's hour of 100 people on 300,000 invoices (4 web workers, 4 cores) met every target but p99: 5.50 s
    for 3 s. Once a report's settled answer (D-164, D-197) no longer held, the web worker of the first person to open
    the page worked it out (server p95: Spend 11.4 s, Month-end 8.7 s, Vendors 6.0 s, Insights 6.0 s, Sales tax
    5.8 s), and that worker's other requests waited behind its one running thread (the queue p99 3.6 s, the invoice
    4.5 s, the check 3.4 s). On a database never served, and after an update that changes an answer's format, the
    first minutes worked every answer out at once in the web workers.
  - **What:** on the company server a settled answer is worked out again by the background process; a request that
    has one to show takes it at once.
    - `Store.shared_answer(..., again=)`: what the answer is worked out again from (JSON: dates as ISO text), given by
      each caller of a settled answer. With it, `settle` and `Store.background_answers` (set by `create_app` in server
      mode; a laptop's is off, and nothing of this runs there), once an answer of this database (`summary_id`) and
      format no longer holds (changed, past its settle) and the background process's refresher runs (its `ephemeral`
      lease `answers/refresher`, 30 s, renewed every 5 s), the request takes the latest kept, its own memory's or the
      database's, whose work began less than `STALE_MOST` ago (2 x `SETTLE_MOST`: 30 minutes), and leaves a want
      (`reports.py` `_meanwhile`): one row of `answer_wants` per name and question (`asked_key`), with the arguments,
      the database's `summary_id`, when it was last asked and when it was first wanted: one want however many ask,
      its place in line kept. A request with nothing to show (the first ask, another format after an update, only
      another database's after a restore), or past the cap, or with the refresher down (its lease run out), works it
      out, or waits for another's work under way, exactly as before (B-1834, B-1838, B-1914, B-1919 unchanged).
    - **The refresher** (`web/refresh.py` `Refresher`, started by `app.start_background_work` on the server, beside
      the downloads and the bulk approvals: the background process, or the one process of `--workers 1`): one thread,
      one refresher a database (its lease), the oldest want first, one at a time, worked out through `ANSWERS` (each
      answer by its name: its own caller, the arguments read back, so its version, format, stamp and settle are the
      caller's; inside `reports.refreshing()` it is worked out there, not wanted again) under the answer's own hold
      (`answer/<name>/<key>`: never two processes on one answer, B-1807), kept for every process (`keep_answer`), the
      want dropped. A want that fails (its arguments not read back, an answer AP Coder no longer has, its work
      raising) is logged, waits again behind the others and is dropped, row and all, after `WANT_TRIES` (3); a
      database too busy to drop one stops nothing (it is taken again, held by then).
    - **At its start** the refresher works out again every answer of this database asked in the last day
      (`ASKED_WITHIN`), the latest asked first: `asked_at` is written with each want and, as an answer is asked, at
      most once an hour a process (`ASKED_NOTE`: no write at every page); the rows of another database (before a
      restore) are forgotten then and by the downloads' sweep. After a restart or an update the answers people were
      looking at are worked out before they ask them again.
    - **Worked out again this way:** Spend's figures and currencies (30 s, `erp/spend.py`), Insights' figures and
      operations (60 s) and the Vendors page's workload (by its own work, `erp/insights.py`), Sales tax (30 s),
      Learning's three (corrections, metrics, approvals: 60 s, `learning.SETTLED`), the recurring vendors (60 s: the
      queue's late pill, Vendors, Month-end), month-end's received-not-invoiced lines and invoices not in the ERP on
      its page (30 s; `accruals.received_not_invoiced` and `accruals.not_in_erp`, split out of `build`), and the
      Vendors page's totals, moved from each worker's `summary_memo` onto `shared_answer` (`vendor-totals-1`): one
      answer for every process. **Exact, as before:** the duplicate audit's pairs, month-end's CSV (its lines and
      recurring vendors asked with no settle), the vendor lists of Statements and the JD Edwards worksheet, a vendor's
      controls and a vendor opened that the settled totals don't have (`vendor_summaries()`), the rail's counts (each
      process's, 5 s), and everything a check, an approval, an export or a posting decides with.
    - **How far behind:** a page's figures may be behind a change by its settle (`settle_for`: the caller's, or ten
      times the answer's work, 15 minutes at most) plus the refresher's line (one answer at a time: under a stream of
      changes about one round of the answers being asked), never more than `STALE_MOST` (30 minutes: past it the
      request works it out), never another database's or another format's (B-1834's and B-1838's rules).
    - **The table:** `answer_wants`, made by `_SCHEMA` (no version of its own), and in a restore's copy of an older
      backup before it goes in (`backups._own_triggers`, as B-2045 did for `report_answers.lasted`): a page asked
      while the data folder's files are copied reads it.
  - **Measured** (the 60,000-invoice seed, `bench seed --invoices 60000`; one web worker: a process asking Spend,
    Vendors, Month-end and Insights in turn, one a second, and the queue every half second on another thread, while
    another process approved an invoice every second; after, with the background process's refresher; the first
    visit of each and the queue's checks done again not counted; before: a875aae):

    | one web worker, 3 minutes | before | after |
    |---|---:|---:|
    | Spend, p95 / max | 1.70 / 1.77 s | 0.024 / 0.030 s |
    | Vendors, p50 / p95 | 0.70 / 1.07 s | 0.070 / 0.10 s |
    | Month-end, p95 / max | 1.46 / 1.77 s | 0.25 / 0.29 s |
    | Insights, p95 / max | 0.86 / 0.90 s | 0.019 / 0.020 s |
    | the queue beside them, p95 / p99 / max | 0.34 / 0.55 / 0.72 s | 0.21 / 0.26 / 0.28 s |
    | the web worker's CPU; its memory at most | 79 s (44%); 595 MB | 38 s (21%); 369 MB |

    | with Spend's and month-end's work made 11 s long, as at 300,000 (4 minutes) | before | after |
    |---|---:|---:|
    | Spend / Month-end, at most | 12.0 / 12.1 s | 0.018 / 0.35 s |
    | the queue beside them, p95 / p99 / max | 0.55 / 3.55 / 4.41 s | 0.21 / 0.26 / 0.34 s |
    | the web worker's CPU | 117 s (49%) | 52 s (22%) |

    The refresher worked out 149 answers in its 4.3 minutes (59 s of work, about a quarter of a core; the Vendors
    page's totals and workload most often, settled for ten times their 0.4-0.5 s), 139 with the 11 s works (115 s);
    its process held 350 MB at most, one answer's work at a time. `bench load` (30 people, 2 minutes, 4 web workers,
    the seed never served before): pages p50 67 ms, p95 747 ms, p99 2.20 s, actions p95 122 ms, 0 5xx, 0 "database
    is locked", the correctness checks the same; the background process 222 → 456 MB (150 MB at 300,000 before:
    it now holds the reports' work, as a web worker did). `same_behaviour` approve and pipeline: the same.
  - **Alternatives:** the reports worked out by the background process on a timer (every period anyone might ask,
    looked at or not: this works out only what was asked, as it is asked); a process of its own for the refresher
    (its own running thread, away from the bulk approvals and the downloads: another Python process of 300 MB, and
    its start, stop and log, for a thread's work; left to measure at 300,000); a request with nothing kept waiting
    for the background process (as long a wait, and the first ask of a question never was the background's); each
    ask noted in the database (a write at every page: once an hour a process instead); a function for each answer
    from its arguments beside its caller (two places to keep its format, stamp and settle the same: `ANSWERS` calls
    the caller).
  - **Left:** the refresher shares the background process's one running thread with the bulk approvals, the downloads
    and the queue's re-check: while many answers wait (the start after an update at 300,000), those are slower
    meanwhile. An answer asked with today's date (Insights' operations, the recurring vendors, and Spend's default view,
    which ends today) is worked out by the first request of a new day, and at a start yesterday's are worked out again
    for nothing (`ANSWERS_KEPT` of a name at most). A database never served still works each answer out once, at its
    first ask. The background process's memory at 300,000 and the hour's p99 are to be measured again with it.
  - **After its review (B-2046 to B-2048):** a worker shows its own copy only when it is the one the database keeps
    (an answer too big to keep for every process, or holding NaN, is worked out where it is asked, as before); an
    answer asked with arguments JSON can't hold is worked out where it is asked, said in the log (it failed the page
    on the server); a laptop's Vendors page keeps its settled totals in the process as before, writing nothing.
  - (`ap_coder/core/store/reports.py`, `core/store/_base.py`, `core/store/backups.py`, `core/store/vendors.py`,
    `erp/spend.py`, `erp/insights.py`, `erp/recurring.py`, `erp/accruals.py`, `web/pages/sales_tax.py`,
    `web/pages/learning.py`, `web/refresh.py`, `web/app.py`, `web/server.py`, `docs/HOW_IT_RUNS.md`, `docs/SERVER.md`;
    `tests/web/test_refresh.py`, `tests/core/test_kept_answers.py`, `tests/web/test_downloads.py`)
- **D-199** · **A PO's invoices read for what its match uses, each billed line matched once** (scale goal, targets 1
  and 2).
  - **Why:** the hour of 100 people on 300,000 invoices after D-198 met every target, but its 4 requests over 30 s
    were all on invoices of the biggest vendor (23,174 invoices), on its PO billed by 3,381 of them: each open and
    check matches the invoice against its PO (`coding/po.match_invoice`), which read every other invoice on the PO
    with its whole coding (`Store.po_invoices`: 6-7 KB of JSON each, 1.5 s) and matched each of their lines to the
    PO's lines twice (0.25 s): 1.75 s for one person, 4-15 s at the server under load when several people had that
    vendor's invoices open (`po_invoices` 10.9% of a worker's CPU at 30 minutes).
  - **What:** `po_invoices` has SQLite read out each invoice's `subtotal`, `invoice_date` and `line_items`
    (`purchasing.PO_CODING`; `coding` holds only those), the rest of the coding not decoded, as month-end's
    `billing_codings` does (D-177); a coding that is not an object of valid JSON is read by Python as before (failing
    as it did). `po_lines_billed(..., matched=)` keeps each (description, unit price)'s PO line for the calls of one
    PO (the match's quantities and amounts, the PO page's), so a line repeated across the PO's invoices is matched
    once. For the 3,381-invoice PO: `po_invoices` 1.49 → 0.19 s, the billed lines 0.25 → 0.11 s (15,820 distinct
    lines in the seed; a real standing PO repeats more).
  - **Same:** what the match and the PO page read: the invoices, their subtotals, dates and statuses, what each PO
    line was billed (quantities and amounts, with and without credits), against the whole codings decoded
    (`tests/coding/test_po.py`, 80 invoices of random lines, prices as numbers, text and none, credits, codings of
    AP Coder's and approved, `''`, `null`, not JSON).
  - **Alternatives:** the PO's billed lines kept by invoice in each process and refreshed from the vendors' change log
    (D-174; 0.3 s → near 0, but an invoice joins or leaves a PO by its coding, under any vendor: a log of its own to
    get right), or kept in the database for every process as month-end's (D-194); left until measured to need it.
  - (`ap_coder/core/store/purchasing.py`, `coding/po.py`, `web/pages/purchase_orders.py`; `tests/coding/test_po.py`)
- **D-200** · **The background process works each report's default view out before anyone asks, and keeps the views
  people open warm** (scale goal, target 1's first minutes, and each morning).
  - **Why:** D-198's refresher, at its start, works out again the settled answers asked in the last day. On a
    database never served (a fresh install, a company's database moved onto the server) or not asked anything for a
    day (a server started again after a quiet day or a weekend, an update among them), nothing was asked, so the first
    person to open each report worked it out in their web worker. At 300,000 invoices with 100 people that start
    measured Month-end's first build of its lines by invoice (D-194, `billing_facts`) 12-50 s (four asks at once,
    three waiting for the first), Spend 15.8 s, Sales tax 9-11 s, Learning 6-12 s, Vendors 7 s, and the other
    requests of those workers waited behind that work: p99 5.78 s and 7 requests over 30 s in the first 10 minutes
    (the hour after: p99 2.02 s, none over 30 s). And on a server that keeps running, a report asked in the evening
    and changed in the night (JD Edwards' nightly sync, the intake) was over `STALE_MOST` old at 08:00, so the first
    person's web worker worked it out again each morning (B-2055); a view asked with today's date (Spend, Insights'
    operations, the recurring vendors) is another question each day, so it was never kept for the morning.
  - **What:** at each start (and again for another database: a restore while it runs, B-2053), after the answers
    asked in the last day (D-198, `want_again`: how many is said in the log; a want left waiting from before is
    worked out but not counted, B-2054), the refresher works out each settled answer's default view, what its page
    asks opened with nothing in its address, today (`web/refresh.py` `DEFAULTS`, beside `ANSWERS`), in this order:
    Month-end's three first, the biggest (the lines received and not invoiced, the invoices not in the ERP, the
    recurring vendors for its period end), then the recurring vendors for today (the queue's late pill, the Vendors
    page), Spend's currencies and figures, Sales tax, Learning's three, the Vendors page's totals and workload,
    Insights' two (a start that found asks worked none out before, B-2057).
    - Each goes through `ANSWERS` with its arguments as a want keeps them (JSON), inside `reports.refreshing()`
      (`ahead`), so it is worked out by its caller and kept for every process under the very question the page asks
      (`reports.refreshing()` gives the questions its caller asked); one that is kept and holds still is taken, not
      worked out. One at a time, a want waiting taken before the next; one whose work, or its arguments', fails is
      logged and the others go on; the refresher stopped stops them after the one under way; logged once ("the
      reports' default views worked out at the refresher's start: N").
    - **Kept warm** (`_Warm`, B-2052, B-2055): from then on, while no want waits, it looks every `WARM_LOOK` (60 s)
      at each page's default view (asked anew each time: today's) and at the latest question asked of each settled
      answer within `ASKED_WITHIN` on this database (`Store.asked_lately`: `answer_wants`, its arguments there, the
      one already a default view's once). One whose answer is `WARM_AGAIN` old (`STALE_MOST / 2`, 15 minutes;
      `Store.answer_made`), or has none kept, is asked again: a cheap take while it holds, worked out again once
      something changed, so one younger than `STALE_MOST` is there to show when the first person comes. The default
      views while anything was asked within `ASKED_WITHIN`, or for that long after the start (a server nobody uses
      keeps nothing warm after a day). One that failed, or whose answer could not be kept for every process (NaN in
      it, over `ANSWER_MOST_BYTES`: nothing to age, it was due at every look), is not asked again before the next
      start (B-2056).
    - The arguments are picked by the code the page runs, moved into functions both call: `web/pages/month_end.py`
      `asked` (the period end and the settle), `web/pages/spend.py` `asked` (the currencies offered, the one shown,
      and the dates, currency, categories and rates `spend.figures` is asked with) with `spend.default_period`,
      `web/pages/sales_tax.py` `asked` (the period, the list and its rows), `insights.WORKLOAD_MIN_INVOICES`, and
      today for Insights' operations and the recurring vendors. `NO_DEFAULT` lists an answer with no default view
      and why (none now): an answer of `ANSWERS` in neither fails a test.
    - **B-2050**, found on the way: with several web workers the background process (`ap_coder background`) has a
      Store of its own that no `create_app` sets, so its refresher worked the Vendors page's totals out in its own
      memory only (B-2048's laptop way): nothing kept for the web workers, and nothing of this warm-up's either.
      `start_background_work` makes the server's background Store the company server's (`background_answers`).
  - **Same:** a laptop (no background process, so no refresher: none of this runs); the answers asked in the last
    day worked out first at a start (D-198); what each page shows and asks (its choices moved into the functions, not
    changed: a test opens each page and finds it asks exactly the questions and arguments warmed, the clock past
    every settle, so only a stamp that holds is taken).
  - **Measured** (a 20,000-invoice seed of its own, `bench seed --invoices 20000 --workers 2`, never served; a web
    worker process with nothing in its memory opening each page in turn with nothing in its address, its refresher's
    hold taken; after a first start without the warm-up, and after the background process's warm-up; two rounds,
    the second's figures, the first's within 10%):

    | first view, in the web worker: wall / its CPU | without | after the warm-up |
    |---|---:|---:|
    | Month-end | 1.68 / 1.67 s | 0.18 / 0.18 s |
    | Spend | 0.37 / 0.35 s | 0.037 / 0.037 s |
    | Sales tax | 0.11 / 0.11 s | 0.030 / 0.029 s |
    | Insights | 0.21 / 0.20 s | 0.021 / 0.021 s |
    | Vendors | 0.24 / 0.24 s | 0.099 / 0.099 s |
    | Learning (the coding tab) | 0.28 / 0.27 s | 0.24 / 0.24 s |
    | the queue (its recurring vendors 0.09 s) | 0.42 / 0.68 s | 0.34 / 0.69 s |
    | the seven pages, the web worker's CPU | 3.52 s | 1.29 s |

    Without, the web worker worked out all 14 answers; after the warm-up, none. The warm-up in the background
    process: 3.97 s (2.64 s of CPU), month-end's received lines 1.28 s of it, its invoices not in the ERP 0.50 s,
    Spend's figures 0.27 s. At 300,000 invoices the work moved is the first start's 12-50 s, 15.8 s, 9-11 s, 6-12 s
    and 7 s above: done by the background process at its start, one answer at a time, instead of in the first
    person's web worker. The first 10 minutes at 300,000 are to be measured again with it.

    Kept warm (the same seed, the seven pages asked by a web worker first: 14 views, 13 asked lately, the background
    process's CPU): a pass when nothing changed, each due (a cheap take), 99 ms (the Vendors page's totals 42 ms: the
    vendor list read around them; Sales tax 12 ms), at most one a minute after a quiet quarter of an hour, so 0.17% of
    a core; half an hour of one approval a minute, nobody asking: 3.77 s (0.21% of a core), 24 answers worked out
    (each changed one twice, at its 15 minutes), against 0.41 s for the approvals themselves.
  - **Alternatives:** every period anyone might ask worked out at each start (D-198's alternative; this is one view
    a page, and the latest question asked of each answer); only a first start's default views until someone asks
    them (the first review: a report asked in the evening went cold by the morning, and a view of today's date is a
    new question each day); every question asked within the day kept warm (up to `ANSWERS_KEPT` of each, eight times
    the work under a stream of changes); the default arguments written out in `refresh.py` (a page that changes its
    default would warm another question unseen: the page's own function instead, and a test that opens each page);
    the pages themselves asked through a test client in the background process (all their other work too).
  - **Left:** a view with an address of its own (another period, currency, tab) is kept warm only while it is the
    latest asked of its answer. The background process's share of the machine's cores in its first minutes grows by
    the warm-up (one answer at a time, a few minutes at 300,000), and under a stream of changes by each view kept
    warm that nobody asks (asked, D-198 keeps it young) worked out again every `WARM_AGAIN` (at 300,000 up to the
    start's minute or so of work every 15 minutes). After a quiet quarter of an hour, a change within the minute
    (`WARM_LOOK`) before the first person comes can still have that person's web worker work the answer out. A
    default view whose question changed (today's date, a PO's change, a JD Edwards posting: in month-end's question)
    is asked under the new one once the one before is `WARM_AGAIN` old; a question whose stamp parts moved since it was
    asked (Learning's lessons) is a cheap take at each look.
  - (`ap_coder/web/refresh.py`, `core/store/reports.py`, `web/pages/month_end.py`, `web/pages/spend.py`,
    `web/pages/sales_tax.py`, `erp/insights.py`, `web/app.py`, `docs/HOW_IT_RUNS.md`, `docs/SERVER.md`;
    `tests/web/test_refresh.py`)
- **D-201** · **A report asked before its first answer is worked out says so at once, and opens by itself once
  ready** (scale goal, target 1's first minutes).
  - **Why:** at a first start D-200's warm-up works each report out, Month-end first (12-50 s at 300,000 invoices).
    A page asking an answer the database has none of yet, while another process held its work, waited for it in the
    request (up to `ANSWER_WAIT_MOST`, 180 s): four Month-end asks waited 10.7-49 s, and each wait held what its web
    worker serves others with (a report one of the long routes' 2 places, `HEAVY_AT_ONCE`; the queue, waiting for its
    late pill's recurring vendors, one of the 4 threads), so the others waited behind: the queue 46.7 s and an
    invoice 36 s at the client, p99 3.59 s, 7 requests over 30 s, all in the first 100 s.
  - **What:** on the company server (`background_answers`), a page's GET (`web/core.py` `one_connection` sets
    `reports.asked_by_a_page`) asking a settled answer of the kind the refresher works out (`shared_answer`'s
    `again` and `settle`, whether the refresher runs or not) with nothing kept to show, while another thread or
    process works it out (the warm-up, or another web worker's request), waits for it `ANSWER_BRIEF` (2 s) at most,
    for the thread here and for the other's hold together; the database is looked at once more at the end, however
    short the wait (`_waited`: one kept and let go of just after the ask looked is taken, B-2058), and a thread here
    that holds the answer's lock may only be reading the one kept, which is taken when it holds (B-2059). Then
    `reports.Working` is raised and one handler (`web/app.py` `being_worked_out`) answers, with
    `X-AP-Coder-Working: 1` (`WORKING_HEADER`), so it is told apart from a page served (`bench load` counts it apart
    and asks again as the browser does, B-2062):
    - a browser's page (GET, Accept text/html): the page in the app's layout, under the report's own title, "This
      report is being worked out" (AP Coder is working it out just now: after a start on a big database, a minute or
      so; `templates/working.html`), with `<meta http-equiv="refresh" content="5">` (`WORKING_AGAIN`): it asks again
      by itself until the report shows, with no script, which the Content-Security-Policy allows (a browser test
      checks it). Status 200, never kept by the browser (`render`'s no-store): it is the page asked for, saying where
      things stand; a 5xx body can be swapped for a reverse proxy's own error page (IIS's custom errors, nginx's
      `proxy_intercept_errors`), losing the refresh, and a load balancer's passive checks count 5xx, so a first
      start's hundred people could have it take the workers out. An ask again that waited too long for a place in
      the long routes gets the "Busy" page, which draws itself again too (B-2061);
    - any other ask (JSON, a file, a script): 503, `Retry-After: 5`, no-store, `{"ok": false, "error": "This report
      is being worked out just now ...", "working": true}`: a script must know it has no answer.
    - The queue (everyone's first page) shows the recurring vendors only as its late pill: it asks them waiting 0 s
      (`asked_by_a_page(0.0)`) and, while they are being worked out, opens without the pill (`today_pills`).
    - Views that are never replaced (`reports.as_anywhere`, B-2060): a vendor's details, the vendor master's import
      and Teach from past coding wait for their answers as before; the vendor list and the coding accuracy may say
      they are being worked out. A job's thread, and each file a run reads on one, never takes a page's brief wait
      along from the request it copies its context from (`reports.anywhere_in`, B-2063).
  - **Same:** a laptop (no `background_answers`: its pages work their answers out where asked; a test); an action's
    POST, a background job, the refresher, a write under way (worked out afresh) and an answer without `again` or
    `settle` (the CSVs booked from, the duplicate audit) wait as before (a test); an answer with one to show is
    shown at once as before (D-198, D-179); one nobody works out is worked out in the request as before (the first
    ask before the warm-up gets to it).
  - **Measured** (a 20,000-invoice seed of its own, `bench seed --invoices 20000 --workers 2`, a fresh copy never
    served for each run; the server as the service runs it, 2 web workers and the background process; a process of
    its own holding Month-end's first answer and the queue's recurring vendors for 60 s, the warm-up's own
    `refresh.ahead` with their work slowed, as at 300,000; 8 Admins logged in, then asks at once, timed at the
    client; the code before, 5983127, against D-201):
    - **28 asks in the first seconds** (6 Month-end, 6 queue, 6 invoices, 2 each of Vendors, Learning, Spend,
      Insights, Sales tax): before, median 56.3 s, max 58.2 s, 19 over 10 s, 7 "busy" 503s (Spend, Insights,
      Month-end, Sales tax: 30 s in the long routes' line), the queue 56.2-56.7 s; with D-201, median 3.9 s, max
      6.5 s, none over 10 s, no 503, the queue 1.0-1.2 s (no late pill), Month-end and Vendors (the recurring
      vendors) "being worked out". The 28 share 4 cores at a cold start: the invoices took 3.4-4.1 s with D-201,
      2.5-2.8 s before, when most of the others slept.
    - **The workers warmed** on the queue and invoices, then 4 Month-end, 4 queue and 6 invoices at once: before,
      Month-end 53.2-53.6 s, the queue 51.3-51.4 s, the invoices 0.11-0.58 s; with D-201, Month-end 2.08-2.32 s
      "being worked out" and one 4.04 s (a worker's third: its 2 long routes' places each held for the 2 s wait),
      the queue 0.19-0.56 s, the invoices 0.15-0.69 s.
    - Once the work was kept, the next asks showed the reports (Month-end 1.0-1.3 s, the queue 0.5-1.2 s, its pill
      back), before and with D-201 alike.
  - **Alternatives:** the app's own poll (a script asking a status address, then drawing the page: a route, its role
    rule and a script, for what the meta refresh does without them); the page as a 503 (as a restore's): above; a
    longer brief wait (each ask holds its place that long, and a page asks again every 5 s: at most 40% of a place
    a person waiting); the request leaving the work to the refresher and answering at once whenever nothing is
    kept, also when nobody holds the work (no request would ever work a report out on the server: a bigger change,
    measured on its own, left); waiting without a thread (the pages' handlers are plain ones, on threads).
  - **Left:** while the work goes on, each ask again holds its long routes' place (or thread) for up to the 2 s;
    many people on reports in a first start's first minute line up behind each other in a worker (the 4.04 s above).
    A report whose first ask comes before the warm-up reaches it is still worked out in that request. The views
    never replaced (a vendor's details, the imports) wait for the warm-up's work as before D-201, holding their
    thread: rare in a first start's first minutes. The 300,000 first start to be measured again with it.
  - Reviewed by a fresh reviewer: B-2058 to B-2063 fixed test-first (above).
  - (`ap_coder/core/store/reports.py`, `web/core.py`, `web/app.py`, `web/jobs.py`, `ai/pipeline.py`,
    `web/templates/working.html`, `web/templates/error.html`, `web/pages/review.py`, `web/pages/vendors.py`,
    `web/pages/learning.py`, `bench/load.py`, `docs/HOW_IT_RUNS.md`, `docs/SERVER.md`; `tests/web/test_refresh.py`,
    `tests/bench/test_bench_load.py`)
- **D-202** · **A big PO's invoices kept in each process with what their lines bill, brought up to date from the
  vendors' change log** (scale goal, target 1's first minutes; D-199's alternative, measured to need it).
  - **Why:** at 300,000 invoices and 100 people the biggest vendor's standing PO is billed by 3,414 invoices. Every
    open of one of them (`GET /review/{id}`: `analyse` runs the check's `po_findings` and the screen's `po_section`)
    and every check matched it against the PO (`po.match_invoice`): every other invoice on the PO read
    (`Store.po_invoices`, D-199) and each of their lines matched to the PO's (`billed_by_line`): about 0.3 s of CPU
    each, twice an open. Under load, sharing a worker's GIL, these were the slow requests of a first 10 minutes (3-6 s
    at the server), setting the pages' p99 there (3.6-4.4 s for 3 s).
  - **What:** `po.po_billing(store, key, po)` gives the PO's invoices in review or approved (`Store.po_invoices`'
    rows, in order) and what each bills on the PO's lines (`PoBilling.billed`: `billed_by_line`'s sums); the match
    (`match_invoice`, its left to expense) and the PO page (`po_detail`) take them from it. A PO quoted by
    `PO_KEPT_FROM` (1,000; 100 before its review, below) invoices or more has them kept in each process (`po._KEPT`,
    a `Bounded` of `PO_KEPT_MOST`, 24 MB, in `CACHES`), by database and PO: the rows, and each row's lines as
    matched, (the PO line, the quantity,
    the amount) of each matched line, against the PO's lines as matched (each one's description, price and number:
    when they differ, every row is matched again, none read). At each ask `Store.po_invoices_since` reads, in one
    moment (B-1812's rule), the database's `summary_id`, the change log's first and last change, which invoices quote
    the PO (any status, off its index: a PO key changed alone, by plain SQL, is no change the log follows; 0.7 ms for
    3,424), and the invoices to read again: every one when nothing is kept, or the `summary_id` is another (a
    restore, a summary built again), or a change since is gone from the log; else those not kept, those the log names
    since (any vendor's), those of a vendor whose count of changes moved by more than the log says (B-1811's rule:
    an older AP Coder's triggers count and log nothing), and those with no vendor (no log follows them). Those read
    are matched (`_billed`), the texts of their lines, keys and values, made one object each, those the PO's kept
    rows hold (`_share_texts` and the kept `texts`: 31 → 21 MB for that PO), and the PO kept with the counts of its
    invoices' vendors; when nothing of it changed but the log moved (nearly every ask under load) only its stamp moves
    (its size not measured again). The open's or check's own invoice is left out as before (`exclude_invoice_id`). A
    PO of fewer invoices is read at each ask as before, and the matches of its invoices' lines are kept in each
    process (`po._MATCHED`, a `Bounded` of `MATCHED_MOST`, 6 MB: `po_lines_billed`'s `matched` map by database and
    PO, while the PO's lines are those matched), so its lines are matched once, not at each ask.
  - **Same, by construction and by test:** the sums are added up in the order `billed_by_line` adds them (row by
    row, line by line), so they are the same to the last digit; an invoice whose lines can't be worked out so (a
    coding not an object, lines not a list, a quantity not a number) has none kept, and any sum over it is worked out
    by `billed_by_line` as before, failing where it did; a coding not JSON fails in the read as before. A line's match
    kept is what matching it again gives (D-199: a function of its description, its price and the PO's lines). Every
    ask inside a write under way (which may yet be undone, D-159) reads and matches afresh as before, nothing kept nor
    taken. `tests/coding/test_po.py`: random writes by two workers (each its own caches) and
    plain SQL in every way an invoice on a PO or the PO changes (added, approved onto it or off it, rejected,
    reopened, parked, deleted, exported and undone, its lines and its approved coding changed, moved to another
    vendor, its vendor merged, its PO key changed alone, left with no vendor, a coding the match can't read, a write
    undone, a restore, the PO's lines and open amounts), each worker asking between them, with a log of 1,000 and of
    8 changes, the PO kept whole or read at each ask; every answer of the match, its checks, the review screen's PO
    section and the PO page equal to a fresh read's to the last digit (its `repr`) or the same error; quantities and
    amounts that add up differently in another order. Targeted: only the invoices changed are read and matched, none
    after a change elsewhere, all matched and none read after a PO line changed; a few invoices not kept; an older AP
    Coder's change, after a whole read and after an invoice read again; one moment (a change racing the read, of an
    invoice read again and of one taken as kept); a write undone; a restore; a kept PO counted at its bytes, and
    again once it changed; an invoice read again alone sharing the kept rows' texts; a PO read at each ask matching
    its invoices' lines once while its lines stay, each worker its own. Mutations of the read and the keeping (each
    rule above left out, the sums in another order, an invoice not worked out taken, which invoices quote the PO taken
    from what was kept, the size counted 0, the texts or keys not shared, the matches kept whatever the PO's lines or
    not at all: 19) each fail them, but one: the log's pruning not seen, which the count rule catches as well (each
    change it logs is counted with it). Before the review's tests, three more passed: the read not in one moment (the
    race then touched only invoices taken as kept), the stamp counting only the vendors of the invoices just read (an
    older AP Coder was tried only after a whole read), and the size counted 0. On the
    seed below, the 5 biggest POs' 194 answers (the match, its checks, the PO section with and without the invoice
    left out, the PO page) the same digest in either code; `scripts/same_behaviour.py` tax, approve, read and pipeline
    the same (none of their inputs has a PO loaded: the probes don't reach this path).
  - **Measured** (an 80,000-invoice seed, `bench seed --invoices 80000 --history 0`, its biggest vendor's PO grown to
    the 300,000 seed's: 3,414 invoices of 8 lines, 2,441 more of the vendor's invoices quoting it; process CPU,
    medians of 7, each in a page's session; the code before, f1dcb10, against D-202, Python 3.12):
    - nothing changed since: the open's PO section 305 → 5.3 ms, the check's coding (`finalise_coding` with the
      store) 321 → 13.1 ms, the PO page 301 → 5.4 ms; an open or a check runs both: about 626 → 18 ms;
    - the first ask after another process changed another invoice on the PO (a line's quantity): 271 → 10.4, 278 →
      21.5, 264 → 10.8 ms; after a change to the invoice itself: 256 → 9.1, 263 → 21.4, 257 → 9.6 ms;
    - the first ask in a process (every invoice read and matched, then kept): 262-338 → 330-392 ms, once (the texts
      made one 30-50 ms, the size measured 3-4 ms); then 5.4-6.7 ms;
    - kept: 23.8 MB for that PO in each process (`size_of`; 23.9 MB traced), 33.8 MB before the texts were shared.
  - **Within the worker's budget:** `WORKER_CACHES_MOST` (1,050 MB) had 11 MB left. The PO description pairs'
    `similarity` cache goes from 32,768 to 8,192 pairs (26 → 7 MB): it was sized for each open matching every line of
    a big PO's invoices again (D-195); now a kept PO's lines are matched once a process, and so are those of a PO read
    at each ask (`_MATCHED`, since the review), and what is left (the invoice's own lines, month-end's descriptions
    once a PO is worked out again) is far less. 1,039 − 26 + 7 + 24 + 6 = 1,050 MB.
  - **Alternatives:** kept in the database for every process, as month-end's billing lines (D-194): one copy, but
    each ask would decode the PO's rows again (65-70 ms of JSON for 3,414, measured) or build them from a compact
    form (90-150 ms); keeping only the sums' parts and building the rows at each ask: the same, as the match's
    `other_invoices` and the PO page's rows carry each invoice's lines and must stay as they were; a log of its own of
    which invoices quote a PO (D-199's worry): the PO's index says it at each ask for 0.7 ms; each PO line's values in
    order, summed with `functools.reduce` (0.5 ms against 1.2 ms a sum): not worth its upkeep.
  - **After its review** (a fresh reviewer, probes on the 300,000 seed opened read-only; no wrong answer found):
    - **Which POs are kept:** at 300,000 invoices 35 POs have 100 invoices or more; kept whole they held 63 MB, the
      biggest alone 22.5 MB, so in 30 MB the others' asks let it go (its first-ask hit rate about 74%, 25% had it
      grown to 30 MB) and each miss built it again (288 ms against the old read's 205). `PO_KEPT_FROM` 100 → 1,000:
      only a PO whose read is slow is kept whole (at 300,000 one: 926 invoices are read in 37-39 ms), within 24 MB.
      Measured on the 300,000 seed (read-only; CPU of an ask's billing, `po_billing` and its two sums): the biggest
      PO (3,382 invoices in review or approved) 246-290 ms before, 4.4-5.2 ms kept, 302-379 ms to build once a
      process; kept 22.5 MB; an ask mix of 100,000 drawn by each PO's invoices: the biggest's hit rate 100% after its
      first, the matches' 79% (34 POs' maps, 7.5 MB in all, in 6 MB), a first ask of a PO of 100 invoices or more
      81.7 ms before → 12.2 ms (the reviewer's own times: 66.6 ms before, 33.4 with the first design, 17.4 now).
    - **Its texts drifted:** they were shared only among the invoices read together, so a PO brought up to date an
      invoice at a time grew (22.5 → 30.2 MB once each of its 3,382 was read again alone); the kept rows' texts are
      kept with them and the keys shared too: 22.5 → 22.5 MB.
    - **The pairs' cache cut** slowed POs read at each ask whose invoices' descriptions vary (a ticket, a week): two
      such POs of 99 invoices asked in turn 7.3 → 57.6 ms an ask, four 11.7 → 93.8 (32,768 → 8,192 pairs). Their
      lines' matches kept (`_MATCHED`): two 4.6 ms, four 8.9, eight 18.7 (44.0 and 108.4 before, at either size).
    - The tests and the mutations' account above (the review's: one moment, an older AP Coder after an invoice read
      again, the size counted).
  - **Left:** each process builds its own at its first ask of such a PO (about 0.3 s once, as many threads as ask at
    once at a cold start each building); a PO whose invoices hold more than 24 MB is kept alone, as every `Bounded`
    keeps its latest, and several POs of 1,000 invoices or more over 24 MB in all let each other go (each then costly
    to read: the bound, not the rule). Kept rows are every caller's (`PoBilling`'s docstring: read them, don't change
    them), as `summary_memo`'s values. The 300,000 seed's first 10 minutes under load to be measured again with it.
  - (`ap_coder/coding/po.py`, `core/store/purchasing.py`, `core/caches.py`, `web/pages/purchase_orders.py`,
    `docs/HOW_IT_RUNS.md`; `tests/coding/test_po.py`)
- **D-203** · **Not kept: each web worker of the company server warmed as it starts** (scale goal, target 1's first
  minutes; tried and measured, 8 Nov 2026).
  - **Why it was tried:** at 300,000 invoices, on a database never served with 100 people from the first second, the
    pages' p99 over the first 10 minutes was 2.24 and 3.18 s (D-202; 3 s the target), set by the first 1-2 minutes: each
    web worker's first opens of an invoice built that worker's memories in the request (3-6 s at the server), and Sales
    tax and Learning were asked before the background process's warm-up reached them (3.6-4.7 s).
  - **What was tried** (never merged): a thread in each web worker that, once the worker serves, builds what a first
    open and Learning read (the learning memory, the vendor list, the vendors' ERP IDs, what the held vendors were paid
    with, the reading status, Learning's latest lessons, the review queue's biggest vendors' lessons and invoices), each
    step through the page's own function and after the worker's requests under way (2 s at most); a vendor's invoices
    read by one thread at a time (64 striped locks, then one read per vendor handed to those waiting); and three orders
    of the warm-up's default views (D-200).
  - **Measured** (4 cores, 100 people for 600 s, each run on a fresh seed of its own, nothing else running): a cold
    worker's first open of the queue's first invoice 6.33 → 0.61-0.71 s and its first Learning 3.16 → 0.17 s at 300,000.
    The first 10 minutes as a whole were not better (pages p95, p99):
    - without it (D-202): 898 and 977 ms, 2.24 and 3.18 s;
    - the quick views first, Month-end last: 661 and 755 ms, 2.19 and 2.93 s, but a Month-end page asked before the
      warm-up reached it was worked out in its request (12-50 s at 300,000, the review's finding);
    - a page's ask of a view still to come taken first: 808 ms, 3.22 s (Month-end last); 725 and 877 ms, 2.51 and 6.29 s
      (Month-end first: the views asked during Month-end's first build waited behind it, 90 pages said "being worked
      out");
    - Month-end first, as without it (the version to keep): 1.13 and 1.32 s, 3.45 and 6.30 s. Its misses came 6-8
      minutes in (10-second windows' p95 7.6-9.1 s) while the server's own slowest request took 5.4 s: requests waited
      before a worker's thread took them. Not traced further.
  - **Decision:** not kept. It makes a cold worker's first open fast, but with the warm-up's order the database needs
    (Month-end first), the first 10 minutes' p95 and p99 were worse than without it in both runs. Kept from the work:
    B-2064 (Month-end's invoices not in the ERP read in one moment, found by its load runs) and B-2065 (each web
    worker's own log, found by its review). The first 10 minutes at 300,000 stay at the edge (SCALE.md row 1); a next
    try starts from a profile of those minutes 6 to 8.
- **D-204** · **The company server works its reports out before it first answers, and each web worker its memories
  before it serves** (scale goal, target 1's last edge; 8 Nov 2026).
  - **Why:** at 300,000 invoices a database's first 10 minutes with 100 people had the pages' p99 at 2.24 and 3.18 s
    (3 s the target). Its slowest 1%: each web worker's first opens of an invoice and first Learning visit, which built
    that worker's memories in the request (3-6 s: the learning memory of 50,000 lessons, the vendor list, the vendors'
    ERP IDs, the queue's vendors' lessons and invoices; Learning's latest lessons 2.2 s), and the reports asked before
    the background process's refresher had worked their default views out (D-200: about a minute under load, Month-end's
    first build 12-50 s). D-203 built the memories on a thread once the worker served and made the whole worse: the
    work competed with the requests. The user chose to do it before the server answers.
  - **What:**
    - **The reports, before the port opens, on a database that never had them** (`web/server.py:prepare_reports`,
      `web/refresh.py:prepare`): in `_serve_workers`, before `listening_socket` (connections are refused meanwhile,
      none held), each of `DEFAULTS` is worked out through `ahead`, as the refresher works it out, on a Store with
      `background_answers` (B-2050: the Vendors page's totals kept for every process too), in `DEFAULTS`' order
      (Month-end's three first). A view that fails, or whose arguments can't be found, is logged and the next goes on.
    - **The rule: only a database that never had them** (`refresh.served_before`, `Store.answered_before`): no answer
      of any of `DEFAULTS`' names kept by any AP Coder, and no Month-end billing facts (D-194). One that had them (a
      restart; an update; one view's answer gone while the others are kept) is served at once, as before D-204, its
      views worked out while it serves by the refresher (D-198, D-200; a report asked before says it is being worked
      out, D-201), and the window says so in one line; no process is started. Why not each view with none kept for its
      question (the first version, which asked each answer's caller for its key without working it out): every update
      keeps none of the version before's answers (an answer's version is AP Coder's own, `reports._version`, and so is
      Month-end's billing facts', `purchasing`'s `kept`), so every update worked them all out before it answered: at
      300,000 its downtime would have been about 3.5 to 4 minutes instead of about 16 s (D-188's target 5). Nor each
      name with none kept: simpler is one look, and a database served before has each name (the refresher works each
      default view out at every start).
    - **In a process of its own** (`server._prepare_reports`, started with multiprocessing's "spawn", as uvicorn's web
      workers, on Windows and Linux alike): the server's own process (uvicorn's supervisor, which lives as long as the
      server) did the work at first and its allocator kept what it peaked at (114 MB with the server's imports, 235-241
      MB after the work at 60,000 invoices); now it waits for that process and keeps 118 MB (20,000 invoices). Its
      bounds: `refresh.PREPARE_MOST`, 200 s, looked at before each view, the view under way finished, then
      `server.PREPARE_ENDS`, 15 s more, after which the process is ended. Month-end's three, by far the longest (92-121
      s together at 300,000), come first, so the view under way at 200 s is a quick one (about 10 s at most at 300,000,
      estimated: the Vendors page's totals 7 s there, B-2050). A process that fails (exit code 1, its warning in the
      window with its time) or is ended is no reason not to serve: the window says so and the refresher works out the
      rest after the start.
    - **Said in the window** (and `service.log`): "Working out the reports' default views before the dashboard answers
      (this database never had them), 200 seconds at most..." then how many of the 14 were worked out, in how long (or
      that its process stopped or was ended); on a database that had them, that they are worked out while it serves.
      Printed, as the server's other lines at a start: the window shows warnings and up only (`cli.configure_logging`,
      B-240; uvicorn at "warning"), so an INFO line would reach nobody.
    - **Each web worker's memories, before it serves, at every start** (`server.warm_worker`, in `worker_app`,
      uvicorn's factory): the templates of an invoice and of the review queue (`WARM_TEMPLATES`, with every template
      they extend, include or import: 0.43 s a first open compiled at 60,000 invoices), the learning memory, the GL
      suggestions' index of it (`suggest_gl` asked of one line: 0.32 s), the vendor list, the vendors' ERP IDs, what the
      held vendors were paid with, the reading status, Learning's latest lessons, then the lessons and invoices of the
      biggest vendors of the review queue's first 50 invoices (8 at most, their invoices within half of
      `VENDOR_ROWS_KEPT`): D-203's `warm_steps` and those two, each through the page's own function, run in turn with
      no thread and no request to yield to. `WARM_MOST` (25 s, looked at before each step) bounds it; uvicorn's pong
      thread answers the supervisor's "alive?" meanwhile. The 30 s uvicorn gives a new worker to serve
      (`timeout_worker_healthcheck`) is `restart_all`'s alone, on SIGHUP, which AP Coder never sends: a worker that
      ended (`Recycle`, D-153) is replaced with no such wait. A step that fails is logged and the next goes on; a worker
      asked to stop stops building. Said in its own `logs/web-<n>.log` (B-2065). The other workers serve meanwhile (a
      worker started afresh by `Recycle`); at a start none does for those seconds, and connections wait in the port's
      queue. After an update too: about 13 s more of downtime at 300,000, and the first opens after it are fast.
    - **The waits for a server started:** server-update's for the new version (`UP_WAIT`, before it goes back to the
      old one) 180 → 300 s, `bench load`'s 180 → 300 s (`START_WAIT`), `bench day`'s 120 → 300 s, CI's update job's
      300 s made a constant (`UP_WAIT`); `test_every_wait_for_a_server_started_covers_what_it_does_before_it_answers`
      holds each at `PREPARE_MOST + PREPARE_ENDS + WARM_MOST` + 30 s or more (270 s), and even an update of a database
      that never had the reports within D-188's 5 minutes (200 + 15 + 25 + 15 for the processes' starts and the step
      under way + 40 for server-update's own stop, copy and update).
  - **What stays the same:** a laptop and the server in one process (`--workers 1`) prepare and build nothing ahead;
    the refresher's start, its wants and its views kept warm are as they were (on a database just prepared it takes
    the answers kept, a cheap take); a restart or an update works no report out before it answers, so the first
    minutes after an update are as before D-204 (the reports worked out while it serves, a report asked meanwhile
    shown as being worked out, D-201), but for the workers starting with their memories built; what a page shows.
  - **Tests:** `tests/web/test_refresh.py` (on a database that never had them each view worked out once and nothing
    more, kept for every process: a web worker's pages work none out; a database with this version's answers, only an
    earlier version's, one view's answer gone, only Month-end's billing facts: served before; only another report's
    answers: not; the bound, said so; a failing view and arguments not found logged, the next going on),
    `tests/web/test_server_capacity.py` (prepared before the port is bound, only by `_serve_workers`: `serve` with one
    process and a laptop prepare nothing; in another process, none of the work in this one; a database that had them
    (an earlier version's answers) served at once, no process started, one line said; the process failing or ended at
    its bound, the window saying so and the server answering; `worker_app` builds each memory once before it returns,
    the templates compiled and the GL index built, and an invoice and the queue opened right after read, compile and
    build none again; only a server's web worker builds them; the queue's biggest vendors within their bounds; the
    bound, a stop, a failing step and steps not found; the real two-worker server: its window's line, each
    `web-<n>.log`'s, it serves, and started again it answers at once, the workers' memories built again; the waits).
  - **Measured** (4 cores shared with another seed's build, Python 3.12, a 60,000-invoice seed of my own,
    `bench seed --invoices 60000`, 2 web workers; the first version, which looked at each view's question):
    - the reports before the port opens, on the database never served: 7.9 s in the server; 12.5 s timed view by view
      with the other build busier, Month-end's received POs 6.2 s of it, the next longest Spend's figures 1.3 s and
      Learning's corrections 1.1 s; with Month-end's billing lines kept already (D-194), 5.3 s;
    - each web worker's memories: 2.9-3.1 s (22 steps; the learning memory 1.0 s, the vendor list 0.5 s, Learning's
      latest lessons 0.4 s, the reading status 0.2 s, the biggest vendor's lessons 0.4 s);
    - the server answered 13.0 s after its start on the database never served, 5.0 s started again, where the code
      before took 1.6 s;
    - first opens (the access log's server time, the queue's first four invoices, then Learning): before, each
      worker's first open 2.44 and 2.29 s, Learning 0.47 s; with the memories built 0.51-0.59 s, then 0.06-0.14 s,
      Learning 0.09-0.18 s. What a first open still built (a profile): Jinja's templates, 0.43 s, and the GL
      suggestions' index, 0.32 s: since, two steps of the workers' own;
    - a process doing the same as the server's own (uvicorn's supervisor), after the reports: 235-241 MB, where the
      server's imports alone take 114 MB (hence their own process).
  - **At 300,000 invoices** (the lead's runs: a seed never served each time, 100 people for 10 minutes from its first
    start, 4 web workers, 4 cores):
    - 6696496 (the reports' bound 120 s, each worker's 15 s, in the server's own process): "3 of the 3 with none kept
      (14 in all), in 121.3 s; past 120 s, 11 left to the background process": Month-end's three alone 121.3 s (their
      billing facts, D-194, built from nothing); each worker stopped at 15 s with 8-12 of its steps (4 workers building
      at once). Pages p50 115 ms, p95 686 ms, p99 2.91 s, actions p95 347 ms, no error, none over 30 s; the slow ones
      left in the first 2 minutes were the quick reports the bound left out (Sales tax p95 3.44 s, Insights, Vendors).
      Hence the bounds 200 s and 25 s, the reports' own process and the two steps more;
    - 24391d1 (all of those): "14 of the 14 ... in 92.3 s"; each worker 17 of its memories in about 13 s. Pages p50
      132 ms, p95 1.06 s, p99 2.49 s (2.24-3.18 s before D-204: the target met), actions p95 508 ms, no error, none
      over 30 s.
    - **After an update** (estimated from those): no report worked out before it answers (the rule), so its downtime
      is as before D-204 (about 16 s on CI's Windows runner at 20,000 invoices) and each worker's memories, about 13 s
      at 300,000; its first minutes are as before D-204, the workers warm.
  - **After the rule** (a 20,000-invoice seed of my own, 2 web workers, a first start): the reports 3.6 s in their own
    process, the server answering 9.4 s after its start, its own process 118 MB; each worker 2.5-2.7 s for 24 steps;
    a first open after them 0.25 s in a worker's process, half of it importing the PDF library (`capture.layout`,
    once a process), then 0.07 s.
  - **Where each half applies** (the review's point): in production a database is mostly first served empty, its
    invoices brought in through the dashboard's own pages, and a laptop pilot's database counts as served once its
    review queue or a report was opened: the reports' half mostly covers a database built without being served
    (`ap_coder process`, a bulk load) and the bench's seed. The workers' half applies at every start, an update's too.
  - **The 5 minutes, tightened** (an update of a database that never had the reports, the only one that works them
    out first): no view is begun past 200 s, and Month-end's three, by far the longest, come first, so the one under
    way then is a quick one (about 10 s at 300,000, estimated); `PREPARE_ENDS` (15 s) is its time to finish, then its
    process is ended (SIGTERM ends it at once; 5 s more, then a kill, for one that doesn't); the workers begin no step
    past 25 s (each step 1-3 s at 300,000). The start is then within about 260 s, inside server-update's `UP_WAIT`
    (300 s: a start that overran it would roll the update back), and the downtime, with server-update's own 16-40 s,
    within the 5 minutes; the reports only near 200 s far above 300,000 invoices (92-121 s there).
  - **After its review** (a fresh reviewer, 5a40c17..f6ce662): B-2067 (a worker started afresh: no other one stopped
    until its replacement serves, `WARMING_NOTE`), B-2068 (a SIGTERM while the reports are worked out ends their
    process too; that process stops once its server is gone), B-2069 (the server file written before the reports),
    B-2070 (the day bench counts "database is locked" on no INFO line), B-2071 (an error looking at the reports or
    starting their process: said, and the server answers). Ctrl+C ends the reports' process quietly.
  - **Left:** a database that had the reports and was updated waits for the refresher for its first minute or two,
    as before D-204 (D-201 says "being worked out"); a database restored from a backup keeps its answers, of another
    database, until they are swept, and counts as served. A start's workers serve nothing for their seconds of
    building, all at once. The PDF library's import (0.12 s) is still a worker's first open's.
  - (`ap_coder/web/server.py`, `web/refresh.py`, `core/store/reports.py`, `ops/server_update.py`, `bench/load.py`,
    `bench/day.py`, `scripts/ci/server_update_downtime.py`, `docs/HOW_IT_RUNS.md`, `docs/SERVER.md`;
    `tests/web/test_refresh.py`, `tests/web/test_server_capacity.py`)
- **D-205** · **The OCR models have a mirror, huggingface.co, tried only when www.modelscope.cn fails a download, and
  kept only with their pinned SHA-256** (B-2072; approved by the user, 9 Nov 2026).
  - **Why:** since 8 Nov 2026 about 22:30 UTC, www.modelscope.cn serves RapidOCR's OCR models at about 20 KB/s
    (measured from a developer's machine and in GitHub Actions; 70 KB/s in a 25-second sample on 9 Nov): the 16.6 MB
    `ch_PP-OCRv5_rec_mobile_infer.onnx` arrived cut off and failed its pinned SHA-256 three times, and every bundle
    build (`scripts/build_offline_bundle.py`), the Windows installer and each first setup from GitHub's ZIP (the
    launcher, D-94) failed: there was no other source.
  - **The mirror:** `https://huggingface.co/pitapo/rapidocr/resolve/main/` plus the same path as under modelscope's
    RapidOCR `resolve/v3.4.0/` (e.g. `onnx/PP-OCRv5/rec/ch_PP-OCRv5_rec_mobile_infer.onnx`). All five pinned OCR files
    there (`ops/offline.py` `_PPOCRV4_FALLBACK`, `_PPOCRV5_FALLBACK`) were checked on 9 Nov 2026: byte for byte the
    same, every SHA-256 the pinned one (16.6 MB in 1.6 s). It is a third party's copy: only the pinned SHA-256s make it
    safe (a different file is refused, the model then reported missing), and nothing promises it stays up.
  - **What:** `ModelFile.mirror` (`offline.mirror_url`), looked up by the pinned SHA-256, so a model RapidOCR's own
    list names (the same five today) gets it only if it is one of those files; a newer model has none.
    `fetch_models.fetch` tries the site as before (its tries, timeouts, `.part` file and SHA-256 check), then on any
    failure (no answer, cut off, far too slow, the wrong file) the mirror the same way, and says which host a file came
    from ("Got ... from its mirror, huggingface.co, checked against its SHA-256."). A host that never answers is asked
    for nothing more in that run (B-554), the mirror too. The launcher's notice and its "couldn't reach" / "Couldn't
    download" lines name both hosts. The table finder (RapidLayout) has no mirror: no copy of it was verified.
  - **Far too slow means "try elsewhere"** (`fetch_models.SLOW_AFTER`, `SLOW_LIMIT`): CI's `Installer on Windows`
    job was cancelled at its own time limit while modelscope trickled the PP-OCRv5 models (the 4.7 MB detector from
    22:29:58, the 16.6 MB recogniser from 22:34:16, cancelled 22:44:02), the mirror never asked, since a trickle is no
    error. While another source is left for a file (the site of an OCR model, which has the mirror), after the file's
    first 30 s one that would take more than 3 minutes in all at its average rate so far is given up on: its `.part`
    deleted, not tried again there, and the mirror tried. 3 minutes lets the largest model, 16.6 MB, come at 92 KB/s
    or more (a slow office link's 1 MB/s brings it in 17 s, a tenth of the bound), while the detector at CI's 18 KB/s
    (4.3 minutes) and the recogniser at 20 KB/s (14 minutes) move on after 30 s; 5 minutes would have let the
    detector's 4.3 minutes through. A host far too slow did answer, so it is still asked for the next file (not
    "couldn't reach"). The last source left for a file (the mirror; the site of a file with no mirror, as the table
    finder, which CI's bundle builds did get from the trickling modelscope that day) is waited out as before, with its
    timeouts: giving up there would leave the file nowhere to come from. The floor needs the file's length, which both
    hosts send (`Content-Length`, after their redirects); without it a download is waited out as before.
  - **Fallback only, never at run time:** www.modelscope.cn is always asked first. The app never downloads a model
    (D-163); only `scripts/fetch_models.py` does, at the first setup from GitHub's ZIP (the launcher's opt-in for that
    step) or the bundle build (`AP_ALLOW_INTERNET=1`). The offline guards allow the mirror's address in
    `ops/offline.py` only and prove that without the opt-in neither host is asked.
  - **What IT must allow** for a bundle build or a first setup from GitHub's ZIP: `huggingface.co` (HTTPS, port 443)
    beside `pypi.org`, `files.pythonhosted.org` and `www.modelscope.cn`; `build_offline_bundle.py --dry-run` lists
    them, each model's mirror address too. Without it nothing changes while modelscope works.
  - **Left:** a slow site costs each missing OCR model its first 30 s before the mirror, and a mirror as slow is waited
    out (a slow link at both hosts takes as long as before, plus those 30 s). The table finder has no mirror (none at
    huggingface.co: `pitapo/rapidocr`, `RapidAI/RapidLayout` and `SWHL/RapidLayout` answer 404/401), so a trickling
    modelscope is still waited out for it. A file that came cut off is not asked of modelscope again before the mirror.
  - (`ap_coder/ops/offline.py`, `scripts/fetch_models.py`, `scripts/launch.py`, `scripts/build_offline_bundle.py`,
    `docs/HOW_IT_RUNS.md`, `docs/HANDOFF.md`, `docs/PILOT.md`, `docs/REFERENCE.md`; `tests/launcher/test_launch.py`,
    `tests/launcher/test_launch_build.py`, `tests/offline/test_offline.py`, `tests/offline/test_offline_guard.py`)
  - **Amended (B-2077, 9 Nov 2026): the table finder has a mirror too**, the model author's own release on GitHub,
    `https://github.com/RapidAI/RapidLayout/releases/download/v0.0.0/yolov8n_layout_general6.onnx`
    (`offline.TABLE_FINDER_MIRROR`, on `table_finder_file()`): 12,242,304 bytes, byte for byte the pinned file (its
    SHA-256 the pinned `927b6edc…8c95`, checked 9 Nov 2026; 12 MB in about a second, the file itself sent from
    `release-assets.githubusercontent.com` after the redirect). **Why:** with no other source it was waited out at
    www.modelscope.cn, and that day modelscope didn't answer for it at all (3 tries, WinError 10060): CI's bundle build
    stopped ("Could not get every OCR model") and the pilot launcher's first start on Windows said it couldn't reach
    www.modelscope.cn, so its next start set the models up again. It is tried the same way as the OCR models' mirror:
    only after modelscope fails it (no answer, cut off, the wrong file, or far too slow: the speed floor now applies
    to it, a source being left), kept only with its pinned SHA-256, and the mirror, the last source, is waited out. The
    author's own release, at an address that names its version, is a safer copy than a third party's; the SHA-256 is
    still what makes it safe. The launcher's lines name it ("www.modelscope.cn or its mirror, github.com"; both
    mirrors: "its mirrors, huggingface.co and github.com"; on its own: "the table finder's mirror, github.com").
    **What IT must allow** now includes `github.com` (and `release-assets.githubusercontent.com`, where it sends the
    file), asked only when modelscope fails the table finder; a first setup from GitHub's ZIP already reached
    github.com for the code. Offline at run time is unchanged: only `scripts/fetch_models.py` downloads, with the
    opt-in. (`ap_coder/ops/offline.py`, `scripts/fetch_models.py`, `scripts/launch.py`,
    `scripts/build_offline_bundle.py`, `docs/HOW_IT_RUNS.md`, `docs/HANDOFF.md`, `docs/PILOT.md`, `docs/REFERENCE.md`;
    `tests/launcher/test_launch.py`, `tests/launcher/test_launch_build.py`, `tests/offline/test_offline.py`,
    `tests/offline/test_offline_guard.py`)
- **D-206** · **Exports, Activity and Settings → Automation answer for one person at 300,000 invoices in under 300 ms:
  Exports' JD Edwards check and Activity's approvals and controls report kept until what they come from changes,
  the events counted and their people found off indexes, the touchless audits off an index of their own** (scale
  goal, target 2; 9 Nov 2026).
  - **Why:** `bench volume` on the 300,000-invoice seed (final code of phase 8 part 7, 64046fc), median of 3 warm
    requests, Admin / Clerk: `/exports` 0.88 / 0.89 s and `/exports?fmt=jde` 0.79 / 0.82 s (0.57-0.72 s measured
    again here), `/activity` 0.51 / 0.73 s, `/activity?q=Birchwood` 0.53 / 0.71 s, `/settings/automation` 0.35 s;
    300 ms the target. Profiled: Exports checked its 1,122 invoices ready to export for JD Edwards at every visit
    (each read and decoded twice: 0.55 s; its SQL 70 ms); Activity counted 1.26 million events by looking at each
    one's action (0.1-0.18 s, twice for a Clerk), read every event's name off their index for the Person list (60-80
    ms), counted the approvals in the summary (0.22 s) and built this month's controls report (0.2 s); Automation
    looked for the last 30 days' audits among 3.4 million supplier outcomes (none of them audits: 0.28 s).
  - **What:**
    - **Exports' check** (`web/pages/exports.py:ready_problems`): the rows of the invoices that can't go
      (`problem_rows`, now of the invoices the check read, not read again) are a `shared_answer` (D-169) for the
      invoices asked, JD Edwards' settings (their text, which the work checks by: B-2075), the day and the tax-rate
      file's stamp (`asked`; `Store.rates_stamp`, as `checks_stamp` compares it: the amount sent gross reads the
      rates, B-2074), under the summary's generation, `changes/register` and `changes/vendors`: what the check reads
      (the invoices, the exported ones and the register by number, the vendor master's ERP IDs; the open period by
      today). Exact, no settle: which invoices can be ticked is shown as before, and making a batch checks its
      invoices afresh (`_create_batch`, `jde_check`). The settings' own problems (`settings_problems`) are read at
      each visit as before. 0.7 MB kept for the 1,122 ready at 300,000 invoices (in each process that shows it,
      and in `report_answers`).
    - **Activity's approvals** (`insights.approval_counts`): a `summary_memo` on the generation (any invoice or
      approval changed), exact. **Its controls report** (`activity.controls_report`): only the counts its tiles show
      (`TILE_COUNTS`, and `same_person`'s or None without the duties; the template draws them as it drew each list's
      length), a few hundred bytes whatever the period (B-2073: the whole report of a year was 153 MB at 15,000
      invoices, past `summary_memo`'s 112 MB), a `summary_memo` on the generation and, asked, the period, the duties
      and `Store.events_stamp` (the events' count and newest id: events are only ever added; a deletion moves the
      count). None of the counts is of the approvals over the limit, so the limit and the rates are not asked. The
      download (`/activity/controls-report`) is built whole and afresh as before.
    - **Every event but the hidden ones** (`Store.count_events` with `hidden` alone): all the events counted
      (SQLite's count of a table decodes no row), less those of the hidden actions off `events_action` (an event's
      action is never NULL): 15 ms for 1.26 million, was 0.1-0.18 s. **The Person list** (`Store.event_actors`): each
      name after the one before off `events_actor` (a recursive query, one seek a name: 330 names in 2 ms, was 57
      ms); `actor > ''` gives the names `actor != ''` gave (a TEXT column holds text, bytes or NULL; no text is below
      '', bytes sort after text), each kept as before when one of its events is not hidden.
    - **The audits** (`Store.touchless_summary`): a partial index of the audits alone, `supplier_outcomes_audit (at,
      invoice_id, correct) WHERE source = 'audit'`, made at the start (`_SCHEMA`: a database made before gets it at
      its next start, one read of the outcomes), read for the window and grouped after (`GROUP BY +invoice_id`:
      without statistics SQLite would rather walk the invoices' index in order). Small, as the audits are a sample of
      the touchless invoices; the reviews' outcomes never enter it.
    - **A period's events** (`Store.find_events` with `since` or `until`: the controls report, Activity's tiles of a
      period; B-2076): they come off an index by day, not in their order, so each batch of 2,000 asked for all of the
      period's again, joined to each invoice's whole row, and sorted: the work grew with the square of the period's
      events (a year's controls report, 596,000 events, 313 s at 300,000 invoices; 90 days 20 s). Their ids are read
      in one go off that index (8 bytes each kept), then the events a batch at a time by id, each invoice's vendor and
      number off the invoices' summary, which keeps them as they are (`summary.MIRROR`), brought up to date first:
      the same events in the same order (`tests/reports_reference.py`). A year's events read in 3.9 s, the whole
      report built in 15 s (one-person approvals over the limit 8.4 s, the duties 2.3 s).
    - **A long period's controls report**, still over a report's 3 s (a year's 15 s: the approvals one person gave
      over the limit are found by reading every approved invoice's coding, 8.4 s, and no summary column is provably
      the total and currency that check reads): on the company server it is a download its background process makes
      (`downloads.KINDS`, D-169), as the Activity CSV: the link (`data-download-job`) says how far it got and the file
      downloads once made; asked without the page's header (a bookmark), it is waited for. A laptop makes it as it is
      asked for, as before.
    - **Activity's tiles of a long period**: never the approvals over the limit (`controls.build(..., over=False)`:
      no tile shows them), and on the company server a month at most (`activity.TILES_MOST_DAYS`, 31 days, the first
      and last included): a year's tiles still took 6.5 s there, and the events recorded all day keep the kept counts
      for moments only. A longer period on the server says "The tiles count a month at most on the server" and offers
      the report's download. Chosen over D-201's "being worked out" for a kept answer: the counts follow every event,
      so it would be worked out again at nearly every visit (and a page's first ask works it out itself), where a
      month's are 0.4 s at 300,000 invoices. A laptop counts any period, as before.
  - **Measured** (`bench volume --only` on the seed, median of 3 warm requests, Admin / Clerk, before → after):
    `/exports` 0.574 / 0.724 → 0.046 / 0.048 s, `/exports?fmt=jde` 0.706 / 0.589 → 0.045 / 0.048 s, `/activity`
    0.523 / 0.719 → 0.017 / 0.057 s, `/activity?q=Birchwood` 0.605 / 0.699 → 0.056 / 0.079 s,
    `/settings/automation` 0.360 → 0.071 s: 9 addresses over target → 0. The pages are the same, byte for byte
    (`tests/bench/test_seed_volume.py`: against the code before, `tests/reports_reference.py`, nothing kept, after
    13 changes by another Store or plain SQL). A year (2025-11-09 to 2026-11-09, the code of bb40bf7 → after B-2076
    and the two above): `/activity/controls-report?start=…&end=…` 316.7 / 317.0 → 16.3 / 15.9 s (now the server's
    background job, which the bench waits for), `/activity?start=…&end=…` 322.3 / 322.0 → 0.018 / 0.062 s (its tiles
    not counted on the server); 30 days, `/activity?start=2026-10-10&end=2026-11-09`: 0.020 / 0.073 s (its first
    look, the counts worked out, 0.39 / 0.48 s).
  - **Left:** the first look after a change still does the work: Exports' check after any invoice, vendor or
    register change (0.43-0.50 s here, once for every process), Activity's approvals after any invoice change
    (0.47-0.60 s, and 0.24-0.28 s for the next look: the look that brought the summary up to date keeps nothing,
    D-159) and its controls report after any event (0.27-0.29 s). Under a stream of changes these pages cost what
    they did: nothing there settles. `SELECT COUNT(*) FROM events` still reads the table's pages (4 ms at 1.26
    million, as the register's count does, 3 ms at 600,000). An event changed in place by plain SQL (AP Coder never
    changes one) is not seen by the kept controls report until another event is recorded. A year's controls report
    is still 15 s of the background process's work, most of it the approvals over the limit (each approved invoice's
    coding read); a laptop's, made in the request, as long for as many invoices.
  - (`ap_coder/web/pages/exports.py`, `web/pages/activity.py`, `web/templates/activity.html`, `web/pages/downloads.py`,
    `web/core.py`, `erp/controls.py`, `core/store/accounts.py`, `core/store/capture.py`, `core/store/_base.py`,
    `bench/volume.py`, `docs/HOW_IT_RUNS.md`, `docs/SERVER.md`; `tests/bench/test_seed_volume.py`,
    `tests/core/test_kept_answers.py`, `tests/core/test_store_perf.py`, `tests/reports_reference.py`,
    `tests/web/test_web_analytics.py`, `tests/web/test_downloads.py`)
- **D-207** · **The pilot's hosting: one machine that does everything, recommended a Linux workstation with an
  NVIDIA graphics card** (Phase 1, up to 10 people; 9 Nov 2026). Its pilot computer replaces D-145's (a laptop or
  desktop on Windows 11 Pro) and the Mac mini with a Mac Studio planned since (D-186).
  - **The choice** (`docs/SERVER.md` section 1, `docs/PILOT_PLAN.md`): one machine runs AP Coder and both AI models
    (OvisOCR2 and the chat model, in LM Studio), each as it is. **Recommended:** a Linux workstation with an NVIDIA
    graphics card of 16 GB or more, on Ubuntu LTS, AP Coder as a Linux service (SERVER.md section 15). **The
    alternatives:** a Mac mini with memory enough for AP Coder and both models together (32 GB; SERVER.md section
    14); a Windows Server virtual machine with a graphics card in a cloud region in the company's country (nothing to
    buy; a site-to-site link to the company network, and the cloud's quota for a graphics card asked for first). The
    split layout, a small Windows Server virtual machine for AP Coder (4 vCPUs, 16 GB) plus a model machine on the
    network (SERVER.md section 8, point 5), stays possible but is no longer the recommendation.
  - **Why the Linux workstation:** the cheapest on enterprise-grade hardware. No server licences or user licences on
    Linux; the models in the graphics card's own memory, so the machine's memory is AP Coder's; its disk encrypted
    and unlocked by the machine's TPM at boot, so it starts again by itself after a power cut; IT's standard Linux
    tooling and SSH. **Why not the others first:** a Mac with FileVault waits to be unlocked after every restart, a
    power cut at night included, until someone does it (at its screen, or over SSH on Apple silicon with macOS 26 or
    later: Apple's Platform Security guide), so the Mac mini is an alternative, not the recommendation; Windows 11
    Pro's licence (below) rules out a Windows PC as the server, and Windows Server brings its licences and user
    licences, which put the split layout below the Linux workstation; the cloud virtual machine needs nothing bought
    but a quota and a site-to-site link to wait for.
  - **Memory on one machine:** AP Coder's own sizing (`default_workers`, `capture_processes` in
    `ap_coder/web/server.py`) counts the whole machine as its own: on a 12-core 32 GB Mac 11 web workers and 8
    capture processes, on a 20-core 32 GB workstation 16 and 8 (worked out with the functions, 9 Nov 2026). The pilot
    sets `--workers 4` and `capture_processes` 2 to 3 (`server.json`), the configuration measured (below). On a Mac
    the models share its memory (about 12 GB for both, estimated; AP Coder about 3 GB; macOS about 4 GB: 32 GB); with
    a graphics card they sit in its memory.
  - **Measured** (`.github/workflows/pilot-size.yml`, 9 Nov 2026, GitHub's Windows Server runner: 4 vCPUs, 16 GB, no
    graphics card): 10 simulated people for 10 minutes on 20,000 invoices, 4 web workers, the benches' stand-in model:
    pages p50 223 ms, p95 740 ms, p99 1.56 s; approvals and checks p95 156 ms; 0 errors, 0 "database is locked"; 2.0 GB
    of memory for all of AP Coder's processes at the end of the run, 2.5 GB with each one's peak (web workers 0.25-0.29
    GB each, the background process 0.6 GB). A day's 60 invoices (half scans) dropped at once, the real OCR on those 4
    vCPUs: all 60 in the review queue in 4.1 minutes; with the stand-in model at a graphics card's assumed 8-20 s a
    page, two at once, every page read and reviewed 30 minutes after the drop. Without a graphics card (the release
    check of `v0.3.0-pilot9`, 4 Nov 2026, the same kind of runner, the real models in LM Studio: OvisOCR2 Q8_0 and Qwen
    3.5 9B Q4_K_M): a page read in 4.4 to 5.5 minutes, a chat answer 1.8 to 8.6 minutes, the ten sample invoices 76
    minutes. So a pilot reading 50-60 invoices a day needs a graphics card (about 16 GB) or Apple silicon with enough
    memory on the machine that runs the models; its speed is estimated until the burn-in measures it. SERVER.md's
    minimum (8 cores, 32 GB) stays for 25 people or more, with LM Studio on the same server.
  - **Two corrections.** *Windows 11 Pro:* SERVER.md said it "allows at most 20 devices" for the server mode. AP Coder
    runs its server mode there, but Microsoft's Windows 11 licence (OEM terms of April 2024, sections 2(c)(v) and
    2(d)(iii)) bars using it "as server software or to operate the device as a server", except that up to 20 devices
    may use its file, print, Internet information, connection-sharing and telephony services. Whether serving AP Coder
    to the team is allowed is the company's licensing decision; the safe choice for anyone but its own user is Windows
    Server. *LM Studio's log:* its server log (`%USERPROFILE%\.lmstudio\server-logs`, the folder the release workflow
    copies; `~/.lmstudio/server-logs` on macOS and Linux) keeps each model answer in full and the start and end of
    each request (invoice text, vendor names, amounts), so the model machine holds invoice content: an encrypted disk
    (BitLocker, FileVault), the server's physical security, and the logs deleted on a schedule (LM Studio's docs name
    no setting for how long it keeps them, checked 9 Nov 2026; a Task Scheduler and a launchd example in SERVER.md).
    LM Studio's system requirements don't list Windows Server; the release check runs its headless server (llmster)
    on GitHub's Windows Server machines for each pilot release, on the processor.
  - **Also:** the rollout's steps are "Phase 0" (connect to the company), "Phase 1" (the pilot) and "Phase 2" (300 to
    400 people, the company server) in the current docs, D-145's "stages" left as written; the pilot's team is up to
    10 people (was the pilot tester and 3 to 4 clerks); the gate's sample is unchanged. From the review of these docs:
    LM Studio started again when it stops (SERVER.md section 8, point 8, a Task Scheduler watch; section 14, a
    LaunchDaemon with `KeepAlive`) and its runtimes' automatic update turned off on a server meant to stay offline;
    on a Mac, FileVault's unlock named (someone must do it, over SSH too on macOS 26 with Apple silicon) and a backup
    share mounted at boot by root (`_apcoder` has no password); MAILBOX_SETUP's last hop for a Linux or Mac server
    (the emails to a Windows share, mounted at boot, moved by a systemd timer or a LaunchDaemon).
  - **Left:** the pilot machine's real speed (Phase 0's burn-in); no measurement with a graphics card; the TPM unlock
    with the NVIDIA driver on the workstation itself (its burn-in: a power cut, then the card working); the Mac's
    share and watchdog recipes not tried on a Mac here; the licensing of Windows 11 Pro the company's call; LM Studio
    on Windows Server tried only on the processor.
  - (`docs/SERVER.md`, `docs/PILOT_PLAN.md`, `docs/HANDOFF.md`, `docs/KNOWN_LIMITS.md`, `docs/LM_STUDIO.md`,
    `docs/PILOT.md`, `docs/SCALE.md`, `docs/SCALE_GOAL.md`, `docs/MAILBOX_SETUP.md`, `docs/DEMO_RUN_SHEET.md`,
    `docs/WHATS_NEW.md` (its introduction), `README.md`, `ap_coder/bench/day.py`; comments in
    `tests/web/test_platform_security.py`, `tests/web/test_server_capacity.py`, `tests/web/test_server_scale.py`)
- **D-208** · **The company server's service on Linux: a systemd unit that runs the service host from the `current`
  link, started again by systemd only when it did not end on purpose** (the pilot's recommended machine, an Ubuntu
  24.04 LTS workstation with an NVIDIA card; 9 Nov 2026).
  - **Why:** `service` knew Windows' task and the Mac's LaunchDaemon, and refused on Linux ("for Windows and macOS"),
    where server-update fell back to Windows' run.cmd, which nothing runs there. With the pilot on a Linux machine,
    nothing would have started AP Coder at boot, brought it back after a crash, or kept it stopped once stopped.
  - **What:** `ops/service_linux.py`, the third backend, built as the Mac's, whose shared parts it uses
    (`service_macos`: root, the account's ids, `hand_over` and `hand_back`, the `current` link); `service.py` gains a
    `LINUX` branch in `backend()` and `doctor_checks`. The unit is `/etc/systemd/system/<name>.service` (`apcoder` for
    the default task name, any other `apcoder-<name>`: never a unit of the system's), written whole (`write_whole`,
    0644): `User=` the service's account, `WorkingDirectory` and `ExecStart` through `<the code folders'
    parent>/current`, `Environment` `AP_PRIVATE_DIR`, `AP_SERVER_MODE=1`, `PYTHONUNBUFFERED=1` (the plist's),
    `Restart=on-failure`, `RestartSec=10`, `StartLimitIntervalSec=0` (never given up on, as launchd does),
    `KillMode=mixed` with `KillSignal=SIGTERM` (SIGTERM to the host alone, which ends its dashboard; what is left once
    it has ended is killed: the plist's `AbandonProcessGroup false`), `TimeoutStopSec=60` (more than the host's 45 s),
    `LimitNOFILE=8192`, `After=` and `Wants=network-online.target`, `WantedBy=multi-user.target`. Its output goes to
    the journal (`journalctl -u`), not to files in the data folder (systemd would open them as root). Its values are
    quoted as systemd reads them back (a space, `%`, `$`, `"`, `\`). The account, `apcoder`, is made by `install`
    when this computer has none, the default only (`useradd --system --user-group --no-create-home --home-dir
    /nonexistent --shell /usr/sbin/nologin`; an account named with `--account` must exist; root refused unless
    `--allow-root`). `install`: the account, the data folder handed over before server.json is written in it (so one
    made now is the account's), the link, the unit, `daemon-reload`, `enable`, then `restart`; `start`: `enable` (a
    unit disabled by hand starts at boot again) and `restart`; `remove`: stop, `disable`, the unit deleted,
    `daemon-reload` (the data, the link and the account kept); `status`: `systemctl show` (LoadState, ActiveState,
    SubState, MainPID, NRestarts, ExecMainStatus, Result, UnitFileState) and the host's own health. Every command but
    `install --dry-run` needs sudo and a Linux that systemd runs (`/run/systemd/system`): said plainly.
  - **Semantics, the Mac's:** the host's exit code is the contract with `Restart=on-failure` as with KeepAlive: 0 only
    when stopped on purpose (left stopped), `again()` (75) to be started again 10 s later, a crash or a kill started
    again. `service stop` (the `stopped` file, `/shutdown`, `systemctl stop`) **stays stopped across a reboot**, as on
    a Mac and Windows, without being asked: the unit stays enabled, and the host systemd starts at boot ends at once
    with 0. Disabling the unit on stop was not chosen: a stop would then mean one thing on Linux and another elsewhere,
    and `service start` would have to enable it again. A `systemctl stop` of IT's own stops it until the next boot
    (systemd's way). An update cut short that only root can finish ends with 0 and
    stays stopped (D-188, B-1957).
  - **server-update and its recovery:** nothing new. `switch_folder` points the `current` link (the Mac's
    `point_current`); `can_switch` asks the link's folder (root's `/opt/apcoder`: the host can't, so it leaves the
    switch to sudo server-update); `update_recovery._owned` hands root's marker to the service folder's owner as on a
    Mac. The tests of server-update and its recovery that model Windows' run.cmd on Linux now say so (`LINUX` off in
    their fixtures, as `test_service.py` had `MACOS`).
  - **Proof:** `tests/ops/test_service_linux.py` (the unit read back, and by `systemd-analyze verify` where it is
    installed; systemctl and useradd answered by a stand-in; the refusals; the data folder found in the unit; the
    hand-back; `status --json`; a power cut before the unit's rename; the link switched by server-update; the CI
    script's flow against a stand-in Ubuntu, which found its test unit left behind when install's own checks failed).
    By hand on Ubuntu 24.04 (systemd not running there): `install` made `apcoder` and a data folder of its own;
    the unit's command, environment and account (`HOME=/nonexistent`, a root-owned code folder through `current`)
    answered `/healthz` in 3 s, both processes `apcoder`'s; SIGTERM ended its dashboard, then the host with 0; the
    `stopped` file at a start ended it at once with 0; a second host ended with 75. On GitHub's Ubuntu runner, the CI
    job `linux-service` (`scripts/ci/linux_service.py`), AP Coder in `/opt/apcoder`, root's, with Ubuntu's own Python
    as SERVER.md section 15 sets it up: install as `apcoder` (made by it), `systemd-analyze verify`, enabled, the
    host and dashboard `apcoder`'s, `kill -9` of the host (systemd brings it back, the old dashboard gone), `service
    start` while it runs, `systemctl stop` (SIGTERM in the host's log, stays stopped), `service stop` (still stopped
    60 s later, and after a `systemctl start` as at boot, its last exit code 0), start, an update cut short dealt with
    at the unit's next start, remove (the unit gone, systemd's LoadState not-found).
  - **SERVER.md section 15**, for IT: Ubuntu Server 24.04 LTS, the NVIDIA driver from `ubuntu-drivers --gpgpu`, what
    AP Coder and LM Studio need from Ubuntu, the account, the bundle or the repository, `--workers 4` and 2 capture
    processes for the pilot (the defaults on 20 cores and 32 GB, 16 and 8, are for a bigger load), ufw and SSH, LM
    Studio's headless server as a unit of its own with `Restart=always` and a watchdog (its `/v1/models` asked every
    30 s, three misses end it; LM Studio's own example watches nothing) and its logs deleted nightly by a timer,
    encryption (LUKS, its key sealed in the TPM with Clevis: Ubuntu's own TPM-backed encryption does not support the
    NVIDIA driver's kernel modules in 24.04), the AP inbox (MAILBOX_SETUP.md) and backups to a share mounted at boot.
  - **Left:** the CI job has not run yet (it first runs with this change); the TPM unlock with Clevis and the NVIDIA
    driver is not tried on the pilot's machine (its burn-in proves it: a power cut, back by itself, `nvidia-smi`
    shows the card); `clevis-initramfs` 20 puts no `tss` account in the initramfs, which one guide says the TPM's
    tools need (not confirmed); LM Studio's units are checked by `systemd-analyze` and the watchdog's loop was run
    against a stand-in, never with LM Studio itself; a Linux offline bundle is built on Ubuntu itself (its "this
    computer" packages): the builder offers no Linux `--platform`.
  - (`ap_coder/ops/service_linux.py`, `ap_coder/ops/service.py`, `scripts/ci/linux_service.py`,
    `.github/workflows/ci.yml`, `docs/SERVER.md`; `tests/ops/test_service_linux.py`, `tests/ops/test_service.py`,
    `tests/ops/test_server_update.py`)
- **D-209** · **A line is coded to one JD Edwards account string, `business unit.object.subsidiary`, kept as its two
  codes: the cost center is the business unit and the GL code is `object.subsidiary`; which pairs exist and may be
  posted to comes from the chart of accounts as JD Edwards exports it** (a head-office overhead department's prototype;
  10 Nov 2026).
  - **Why:** The department types one string into JD Edwards for each line ("0880133142.7340.2210"). An account exists
    on some business units and not on others, and only some may be posted to (the chart's posting edit code, the
    business unit's status). AP Coder knew a list of GL codes and a list of cost centers, each checked alone: a line
    coded to an account of another business unit, to a closed one, or to a header account had no finding. The chart is
    far too long for a model's list of choices or one drop-down (about 14,700 rows on 134 business units; about 720
    strings used in a year, on 77 business units).
  - **What:** The two fields stay (`predicted_cost_center`, `predicted_gl_code`), as the JD Edwards sync already writes
    them. `core/chart.py` alone joins and splits the string (`join_account`, `split_account`, `code_of`, `split_code`).
    Two tables hold the chart's rows and the business units as exported; the GL account and cost center lists are worked
    out from them on import, so every reader of those lists is unchanged. `ReferenceData` gets an optional `pairs`: with
    it, a line's pair is checked (not in the chart, not postable, a business unit that is not active, a reserved
    subsidiary), suggestions and the model choose among a short list of valid strings for each line (never the whole
    chart), and the review screen's picker shows the suggested accounts, the vendor's, and the business unit's before
    the rest. The JD Edwards files split a code at its dot with no mapping row. An install with no chart loaded has no
    `pairs` and behaves as before.
  - **Not chosen:** One field holding the whole string (the learning index is by account: the same expense on two
    business units would become two accounts, and a vendor's usual account and usual business unit are two separate
    habits); a third field for the subsidiary (about 60 files for nothing the pair does not say: object and subsidiary
    are never chosen apart); a mapping row for each account (it translates, it does not say which pairs exist).
  - **Room for job costing:** the same three parts under other names (job, cost type, cost code): lists and labels,
    since one module owns the string. A subledger for each line would be a new field.
  - **Built in steps:** this entry is written with the first (`core/chart.py`); the tables, checks, importers, short
    lists, picker and exports follow, each with its tests. The audit behind it measured today's behaviour on files made
    up in the exports' layouts.
  - (`ap_coder/core/chart.py`; `tests/core/test_chart.py`)
- **D-210** · **Under tax explanation code V the pay item of a JD Edwards voucher carries only the taxes the Tax
  setup recovers, with no new switch; and a keying sheet for vouchers typed by hand** (a head-office overhead
  department's prototype; 10 Nov 2026).
  - **Why:** The department claims the GST/HST only. It types the tax on the voucher's pay item (code V, a GST-only
    tax area, the taxable amount and the GST) and leaves a provincial tax inside the expense line; JD Edwards makes
    the GST line and the payable line itself. AP Coder took code V to recover every tax charged: a Quebec invoice
    coded with the QST added to the expense line was held back as unbalanced (B-2086), so no setting could say what
    the department does. It also types its vouchers, so it needs a sheet to read, not the Z-files.
  - **What, the tax:** the invoice's stored G/L distribution already says which taxes are recovered (a recoverable
    tax line of their own) and which are in the expense. Under code V a tax charged that the distribution does not
    recover is left off the pay item (`jde._tax_amounts`, `_Voucher.expensed`): the taxable amount and the tax
    amount are the recovered taxes' alone, the other tax stays in the G/L lines, and the voucher balances as before
    (gross = distribution + the tax JD Edwards posts). In the batch files it is in the non-taxable amount (VLATXN =
    gross - taxable - tax: the three amounts must add up to the gross, which is not sent). With the gross amount
    only, JD Edwards would work the GST out of an amount that holds the QST: held back, with that reason
    (JDE_GROSS_TAX).
  - **No switch:** every voucher this changes was held back before, so nothing an install sends today moves: where
    the Tax setup recovers each tax the code posts, the amounts are the same to the cent. Codes C and B are
    untouched: there the provincial tax is the tax area's sales tax and stays in the tax amount, and a Quebec company
    that prefers code C with an area holding the QST as a sales tax keeps that. What is lost is one catch: a tax set
    to "not recoverable" by mistake under code V used to be held as unbalanced, and now goes out with the tax left
    off the pay item, on a tax area that may hold it (JD Edwards' own tax tolerance is then the check). The other
    way round is still held: a tax the Tax setup recovers under a code that expenses it.
  - **Open, for the JDE team:** on a voucher with a provincial tax left in the expense, is that tax entered as the pay
    item's non-taxable amount (what the batch files send), or is the taxable amount left at the base with nothing
    else typed? The keying sheet shows gross, taxable and tax, which is the same either way.
  - **What, the keying sheet** (`erp/jde_keying.py`; the Exports page's format "JD Edwards keying sheet"): for a
    batch's invoices, a block for each voucher in the screen's order (the header and pay item fields, then its G/L
    lines with the account string as typed, the account's description, the amount and the remark, then a check line:
    lines + recovered tax = gross, then in words what a person would otherwise work out), made of the same voucher
    as the batch files (`jde._prepare`), so the two cannot disagree. Codes are text cells, amounts numbers, dates
    dates; it prints upright with the column names on each page, a voucher that fits a page never cut.
  - **Changed after its review (B-2147 to B-2152):** a keying batch first took every ticked invoice and listed those
    not ready on its second sheet; it now holds only invoices that pass the JD Edwards checks, as a JD Edwards batch
    does (the others stay in Ready to export, listed with why before the batch is made), keeps its vouchers once
    they are back in the ERP register, and its invoices read "On keying sheet 12", not "Exported". The sheet "Not
    ready" remains for a batch made in another format and for vouchers JD Edwards has through the API. The CSV twin
    is gone: a spreadsheet reads its codes as numbers and no house convention keeps them text. A GST/HST left in
    the expense under code V is warned of (JDE_TAX_NOT_RECOVERED), and a voucher with no tax left on its pay item
    goes with the exempt code. Two federal rates on one invoice (lines supplied in two provinces) are two pay
    items: the voucher is held (JDE_TAX_AREA) with both spelled out, until AP Coder makes two pay items itself.
  - **Its settings in one step:** `jde.prototype_settings()` is the department's way (tax amounts on the pay item,
    code V and a placeholder tax rate/area for each province, the remarks as typed, the default account rule) and
    `jde.use_prototype(store)` saves it, shows JD Edwards (`jde.show`) and sets the QST to "add to the expense
    line". `JdeSettings()` keeps its defaults (the tax coded to G/L lines, JD Edwards hidden on a laptop): existing
    installs do not move. The review screen reads what is keyed from `jde.voucher_view` (`keyed`, `not_ready`).
  - **Remarks:** `jde.line_remark` and `jde.voucher_remark` alone say a G/L line's remark (today its description cut
    at 30 characters) and the pay item's (the first line's): the keying sheet always shows them, and one setting
    (Remarks) chooses what the batch files carry: AP Coder's invoice number in VLRMK and the invoice number in VNEXR
    (the default: what was sent before), or the pay item's remark in VLRMK and each line's in VNEXR, as typed by hand.
  - **Not chosen:** a setting "the pay item's tax is only the taxes you recover" (the Tax setup already says it, and
    two places to say it can disagree); the same rule under codes C and B (it would take the PST out of the tax
    amount of every code C voucher sent today); a keying sheet built from the Excel export's rows (it knows no tax
    area, explanation code or account string).
  - **Left:** the worksheet for the JDE team has no question yet for the open one above, nor for the remarks. (The
    Sales tax page now counts a keying-sheet batch typed with code B as one whose PST JD Edwards accrues: B-2153.)
  - (`ap_coder/erp/jde.py`, `ap_coder/erp/jde_keying.py`, `ap_coder/erp/exports.py`,
    `ap_coder/web/pages/exports.py`, `ap_coder/web/pages/settings_jde.py`, `docs/JDE_E1.md`;
    `tests/erp/test_jde.py`, `tests/erp/test_jde_keying.py`, `tests/web/test_web_settings.py`)
- **D-211** · **A data folder that serves a team with sign-in is never started in laptop mode by accident: the
  launcher and `serve` refuse it, the server running or stopped, and laptop mode again on the same data is a named
  option, `serve --without-sign-in`** (one laptop that first runs AP Coder for one person, then serves a few AP
  processors from the same data folder; 10 Nov 2026).
  - **Why:** The launcher asked "is AP Coder already running here?" over plain HTTP only (`scripts/running.py`), so
    beside a server on HTTPS a double-click on `APProcessor.bat`, or on the desktop shortcut left from the laptop's
    first weeks, saw nothing, took the next free port and started a second AP Coder in laptop mode: no sign-in, on
    the team's database. `python -m ap_coder serve` by hand did the same. Asking over HTTPS too would not be enough:
    a server stopped for the night answers on no port, and the same double-click would then open the team's data to
    whoever sits at the laptop. The guide only said "don't double-click".
  - **What tells a team's data folder** (`ops/safety.py:team_server`), any one of four:
    1. a server-mode AP Coder answers for it now: the port its server file (`.ap_coder_server.json`) names, else
       server.json's, asked on this computer over HTTPS (the certificate not checked: it is self-signed or names the
       server, not 127.0.0.1) and over HTTP (a first test with `--insecure-http`); its `/healthz` now says
       `server_mode`. This holds before the first account and without server.json (a server started by hand);
    2. its database holds sign-in accounts, one row in `users` or more, turned off or not
       (`core/store/users.py:sign_in_accounts`: read without a Store, which would migrate it, and leaving no `-wal`
       file beside a database closed cleanly);
    3. the sign-in codes' key, `authenticator.key`, is in the folder: someone set up their authenticator app. It is
       what the launcher's first phase in a new AP Coder folder has, where it runs on the standard library alone
       and can't load the store yet (its second phase, the packages installed, and `serve` read the accounts);
    4. its `server.json` is there (what `service install` writes, and the doctor already reads as "this is the
       server": `service.is_server_here`): the server set up and not yet given its first account.
    The last three do not depend on anything answering: that is the point.
  - **What a laptop-mode start does** (the launcher before it sets anything up or stops anything, `serve` before it
    opens the database; a start in server mode is the server's own and is not asked, except that the launcher never
    starts a second one beside a running server in either mode):
    - *the server runs*: "AP Coder is serving your team from this computer (sign in at https://…): it was not started
      a second time", and the browser opens at that address (the first name of server.json's `allowed_hosts`, else
      this computer's own name, which the certificate should carry); exit code 0 from the launcher, 2 from `serve`;
    - *the server is stopped*: "AP Coder on this computer is set up to serve your team, with sign-in (N sign-in
      accounts in its database …), and its server is stopped now: it was not started without sign-in", with how to
      start it and the option below; nothing is opened in the browser (nothing answers there); exit code 1 / 2;
    - *a laptop's own data folder* (no account, no server.json, no server answering): as before, nothing more said.
  - **The way back, on purpose:** `python -m ap_coder serve --without-sign-in` (`APProcessor.bat --without-sign-in`:
    the launcher passes it on) opens the same data in laptop mode again, with a warning each time; its help says
    what it does. The accounts stay in the database, unused, and the next `serve --server` uses them again: a
    database still moves from laptop mode to server mode and back, as before. It is needed at every such start, not
    once: nothing is written that a later double-click could inherit. While the server runs the option changes
    nothing (two AP Coders never use one database).
  - **Not chosen:** only asking the running server over HTTPS (a stopped server leaves the way in open); the accounts
    alone (a server set up and waiting for its first Admin would still start in laptop mode; a database that can't
    be read would say nothing) or server.json alone (a server started by hand has none); a note in the data folder
    that remembers "laptop mode again" (one deliberate step would then open every later double-click); refusing in
    `server.serve` itself (the tests and the bench call it with their own stores; the command is the one way a
    person starts it); making the launcher's own running check speak HTTPS (it also stops older versions and opens
    `http://127.0.0.1`: a laptop-mode AP Coder given a certificate is still not found by it, as before).
  - **Left:** on a laptop whose data folder has a server.json from a trial of `service install` (`service remove`
    keeps the file), the start is refused until the file is deleted or the option used: the message names the file.
    `--without-sign-in` leaves no line in the Activity log (a new action needs its name in the controls report).
    Not run on Windows 11 itself: the tests run the launcher's and `serve`'s own code against a real server on HTTPS
    with a self-signed certificate on Linux, and CI on Windows Server.
  - (`ap_coder/ops/safety.py`, `ap_coder/core/store/users.py`, `ap_coder/ops/cli.py`, `ap_coder/ops/service.py`,
    `ap_coder/web/app.py`, `ap_coder/web/server.py`, `scripts/launch.py`, `scripts/running.py`, `docs/SERVER.md`;
    `tests/ops/test_safety.py`, `tests/launcher/test_launch.py`, `tests/web/test_server_https.py`)
- **D-212** · **A line's account is suggested from how the supplier was coded last year, as whole account strings
  that may be posted to; a line is coded with no one picking only on strong evidence, and keeps the accounts offered
  and what decided it** (a head-office overhead department's prototype; 10 Nov 2026).
  - **Why:** All AP Coder knew of a supplier was the lines approved in AP Coder. A department starts with a year of
    coding in JD Edwards' Transaction Journal, in its own 30-character remarks ("courier charges 4410 Alder"), where
    an invoice prints the supplier's words ("Ground shipment waybill 77812"). Taught as past coding, with the remark
    as the description, the journal gave: lines coded with no one picking that were wrong four times in five (the
    object.subsidiary without a business unit, or the last line of a split), nothing for a supplier with 346 lines on
    one account under one remark, and the first suggestion right for 6% of lines (the table below). The chat model
    was shown 1.5% of a chart of 13,000 accounts and allowed all of them. Nothing on a line said what had decided it.
  - **What, the supplier prior** (`ai/supplier_prior.py`): `prior_for(store, vendor)` counts, for each account string,
    the supplier's lines, vouchers, share, latest G/L date and commonest remark shapes (months, quarters, years and
    numbers of five digits or more taken out), the share of its vouchers with one coding line and the business units
    each object.subsidiary was used on. The supplier is found by its JD Edwards number when the vendor list gives one
    (a number the journal does not have is a new supplier, never looked up by name); else by the name the journal
    prints, cut at 26 characters: a shorter journal name is whole and must read as the vendor's (legal forms aside), a
    26-character one may start the vendor's name, and two numbers that fit a cut name are not told apart (neither is
    taken, and the line says so). Two numbers under one whole name are one vendor in the vendor list, which merges
    them: counted together. A journal line counts 1, a line approved in AP Coder as proposed 3, a line a person coded
    or changed 5: one correction outweighs four lines of history. `rank(prior, line, reference)` orders the accounts
    by the log of the share plus 3 x the likeness to the remarks AP wrote for the account, 4 x the likeness to the
    lines AP approved to it here, 2 x the likeness to the account's name, each `Candidate` with its reason in plain
    words and counts ("9 of this supplier's 15 lines last year; 4 of its 4 “custom” lines").
  - **What, one list** (`ai/suggest.py` `candidates`): every source in one ordered list of account strings: this
    vendor's approved lines like it, the supplier's coding, other vendors' lines, the vendor master's default, the
    words of the accounts' names. Each pair is completed (`complete_pair`: the past line's business unit, the only
    one of the supplier's that has the account, the only one of the chart that has it, then a guess: the supplier's
    most used, else the home business unit) and must be offered by `AccountPairs`: never a reserved subsidiary, a
    closed or DO NOT USE business unit, a title account. A past line on a business unit since closed gives its
    account on another, saying why, or nothing. `suggest_gl` returns the first of these when a chart is imported or a
    journal loaded. For a line left for a person, the supplier's accounts on a business unit the line names come
    first, saying so (`supplier_prior.units_named`, B-2253: the line has every word of the unit's name that at most
    3 units' names have, "Smartphone device balance - IT - Data Centre"); this orders the list and codes nothing.
    On the made-up company's fifteen-line invoice the account AP keys is first on 15 lines of 15 (it was on 1, and
    among the first three on 4).
  - **What, deciding alone** (`ai/offline_coder.py`): a line is coded with no one picking by this vendor's own
    approved lines as before, or when one account has 90% of the supplier's lines (by weight) of 3 lines or more and
    the last line a person approved here did not go elsewhere (`SURE_SHARE`, `SURE_LINES`). For both, a line a
    person approved here to another account counts only for a new line alike to it (`Prior.sure(line)`, B-2252): a
    lunch coded by hand on a courier's invoice is evidence about the next lunch, not about the waybills, which are
    coded alone as before; it still ranks the accounts offered. Never on a guessed
    business unit, never from another vendor's line, and not when the same words went to several business units
    (who ordered decides: `UNIT_SURE_LINES` approvals on one unit settle it) or to several accounts of one voucher of
    the past coding. Nor does the supplier's one account code a line that plainly says another kind of thing
    (B-2228, `suggest._unless_another_kind`): one that reads as a meal while nothing of the account does, that is
    `ANOTHER_ALIKE` (0.5) to the name of another account offered, or that says the name of another account of the
    chart, offered or not (B-2251, `supplier_prior.reads_like`: every word of one of the things the name lists,
    "laptop" for "Laptops & Desktops", or nothing but words of the name, "Consulting services" for "Management
    Consulting", one of them a word at most 3 names have), and in each case is not alike to AP's remarks
    for the account, the lines approved to it here or its name. It is left with "This supplier's lines usually go to
    X, but this line reads like Y: check it" and both offered; a supplier's own words that name no account ("waybill
    77812", and "Fuel surcharge" beside "Fleet Fuel": one word of two is not the name) are still coded, and so is a
    line no account is named for ("Ergonomic office chair", "Late payment interest" where the chart has neither):
    words cannot tell those. The journal bench's one-account lines: 77% coded alone, 88 of 88 right before B-2251;
    76%, 87 of 87 after ("Paper cups" from a coffee supplier reads like "Printer Toner & Paper"); all lines 14%, 117
    of 117 and 116 of 116.
    A line left says why ("This supplier's 20 lines last year went to 3 accounts: 60% / 25% / 15%",
    "A new supplier: not in last year's journal", "The supplier's usual account … is no longer in the chart (business
    unit … is no longer active)") and keeps up to 5 `candidates`. Every line with an account keeps `decided_by`
    (`core/schema.decided`): `source` rule, vendor, supplier, others, words, model or person, with the rule's id, the
    counts, what it replaced. A fixed rule records itself also when it only agrees; the review screen records a
    person's pick and which candidate it was (`coding/review.decided_on_screen`). Both keys are left out of the
    stored coding when empty, so invoices stored before read and are stored unchanged.
  - **What, the chat model** (`ai/inference.py`): for the lines left it chooses among the accounts offered for each
    line: the supplier's first 8 and the first 8 others, each with its string, name, business unit's name and "used N
    times for this supplier, e.g. …", or "none of these". The schema lists those strings (under 2,000 characters
    where the whole chart was 320,000). An answer that is not offered for the line, or an offered code on another
    business unit, leaves the line for a person and the line says so, as it says when the model gave no answer. Its
    pick is a guess (`decided_by.source` model). With no chart and no journal the model is asked as before.
  - **Measured** (`python -m ap_coder.bench coding --journal`: the made-up year, 14,000 chart rows, 5,500 vouchers,
    seed 1; ten months loaded, the last two held out as invoices in the suppliers' wording; 809 lines of 266 trade
    suppliers; no AI model; right = object.subsidiary and business unit both). Before = the journal's lines taught
    as past coding, the supplier named by its number:

    | lines | before: coded alone / right | first / in 3 | after: coded alone / right | first / in 3 |
    |---|---:|---:|---:|---:|
    | one account (114) | 68% / 21% | 15% / 15% | 77% / 100% | 100% / 100% |
    | several, by what was bought (237) | 13% / 68% | 9% / 9% | 0% / - | 44% / 78% |
    | several, by who ordered (434) | 32% / 9% | 3% / 3% | 5% / 100% | 42% / 72% |
    | never seen (24) | 0% / - | 0% / 0% | 0% / - | 12% / 12% |
    | all (809) | 30% / 20% | 6% / 6% | 13% / 100% | 50% / 76% |

    First candidate's object.subsidiary right 37% → 67%, business unit right 8% → 62%. With AP approving each
    held-out invoice as it comes (`--approve`): 24% coded alone, 94% of those right (186 of 198; 10 of the 12 wrong
    are a billback or another department's line worded as an earlier one), first 52%, in the first three 78%.
    The bars: at 90% of 3 lines, 109 coded and 0 wrong; at 80%, 117 and 3; at 2 lines, 160 and 19; at 5 lines, 98
    and 0: 90% and 3 kept. The ranking's weights are the audit's; the wording was written to share few words with
    the remarks, so on the several-account suppliers the words add about a dozen points to the share alone.
    Speed: 1.5 ms a line on a supplier's first invoice, 0.9 ms after (the slowest 34 ms); a prior is 4.6 KB at the
    median, kept in 6 MB (`core/caches.py`; the 950 suppliers of the year are 5 MB).
  - **Not chosen:** The journal pressed into the approval memory (one remark on the accounts of a split read as a
    change of mind; identical remarks counted once); the whole chart in the model's schema; a third field for the
    account string (D-209: the two codes stay); the vendor's "usual account" rule on journal lines (it counts
    different descriptions, and gives no business unit).
  - **Left:** With no chart and no journal nothing is suggested or decided differently (the coding bench's lines are
    the same, one by one); there the fixes of the usual account's missing cost center, of a line coded from one
    other vendor's, and of a split in past coding do not apply. The reasoning model's own choice among the accounts
    offered is not measured: no model runs in CI (the true account is among the first three for 76% of lines, which
    bounds what it can add from that list). Two suppliers whose names read alike share one vendor, so their coding is
    pooled (the made-up company has many: 113 of 607). A vendor past the 50,000-lesson cap has its units by line
    read from each line's latest decision only. The rules' history, and `decided_by` copied to the lessons, are for
    the stage that searches by them.
  - (`ap_coder/ai/supplier_prior.py`, `ap_coder/ai/suggest.py`, `ap_coder/ai/offline_coder.py`,
    `ap_coder/ai/inference.py`, `ap_coder/ai/pipeline.py`, `ap_coder/coding/rules.py`, `ap_coder/coding/review.py`,
    `ap_coder/core/schema.py`, `ap_coder/core/store/prior.py`, `ap_coder/web/invoice_view.py`,
    `ap_coder/bench/coding_journal.py`, `ap_coder/bench/jde_names.py`; `tests/ai/test_supplier_prior.py`,
    `tests/ai/test_candidates.py`, `tests/ai/test_closed_list.py`, `tests/bench/test_coding_journal.py`)
- **D-213** · **Each G/L line has the remark AP keys with it in JD Edwards: suggested from the supplier's usual
  one, edited by a person, learned on approval, and sent on the keying sheet and in the batch files** (the same
  department's prototype; 10 Nov 2026).
  - **Why:** AP types a remark of 30 characters at most with every G/L line, in its own short words ("KT
    May.cellphone", "coffee supplies 3-1-204"). AP Coder stored none: the sheet and the batch carried the invoice
    line's description cut at 30, mid-word.
  - **What:** `LineItem.remark` and `remark_from` ("supplier", "invoice", "account", "person"), left out of the
    stored coding when empty and never asked of the model. `coding/remark.py` `suggest_remark(line, invoice, prior,
    account, reference)` gives the text and why: (a) the supplier's commonest remark shape for that account
    (`supplier_prior`), in its latest wording, with the month, quarter, year and "INV" number of this invoice put
    back and the office it ends with kept as written; (b) with no remark for the account, the supplier's one shape
    when it is on more than half of its lines (three at least); (c) else the invoice line's own words shortened at
    a word end, else the account's name. `fit` never returns more than 30 characters and says when it shortened.
  - **Filled** in `finalise_coding` (`fill_remarks`), so on every read and every check of the review screen: a
    line's suggestion follows its account; a remark a person typed (`remark_from` "person") is never replaced. The
    form's field is `line_remark`, one a line (`invoice_view.LINE_REMARK`): posted, it is a person's once it is not
    what the line had; not posted, each line keeps its remark. Blank asks for the suggestion again.
  - **Only where remarks are used:** a chart of accounts is imported, or JD Edwards is in use with its remarks as
    typed. Any other install stores its invoices as before (`same_behaviour.py`: the same).
  - **Too long:** a typed remark is kept whole; over 30 characters is the check `REMARK_LONG`, which says what would
    be keyed: an error where the remarks go to JD Edwards as typed, a warning elsewhere. The exports always fit it.
  - **Learned:** `feedback.final_remark` and `feedback.remark_from` (added to an older database at its next start).
    The supplier prior counts them with the journal's: a remark a person typed 5, one a person approved as
    suggested 3, one nobody looked at 1. The accounts are ranked by the journal's remarks only, as before.
  - **Measured** (`bench coding --journal --quick`, 198 held-out lines, each on its true account): the suggestion
    has the department's shape for 11% and is its very remark for 6%. The made-up department picks its wording
    among several and its office at random for every voucher, so this is a floor; nothing was tuned to it.
  - **Left:** the review screen's box and its counter (another build). The period is the month a line names, else
    the invoice date's: the schema has no service period of the header. An unlabelled long number in a remark is
    taken out (it was that voucher's); "INV 123" with fewer than five digits is left as written.
  - (`ap_coder/coding/remark.py`, `ap_coder/core/schema.py`, `ap_coder/ai/supplier_prior.py`,
    `ap_coder/ai/pipeline.py`, `ap_coder/coding/review.py`, `ap_coder/coding/validation.py`,
    `ap_coder/web/invoice_view.py`, `ap_coder/erp/jde.py`, `ap_coder/core/store/`, `ap_coder/ops/help.py`,
    `ap_coder/bench/coding_journal.py`; `tests/coding/test_remark.py`, `tests/web/test_web_remark.py`)
- **D-214** · **The review screen's first version for an install with a chart of accounts: one row a line (the
  whole account string in one box, amount, remark, tax with its amount), the reason under it, in a frame that fits
  the window it is given** (the same department's prototype; stage 1 of the screens plan; 10 Nov 2026).
  - **Why:** With a chart loaded a line was two pickers (GL account, cost center) three rows tall under the whole
    invoice header, the first line some 1,700 px down; nothing said which tax a line carried or how much, why a line
    was coded so, or what was offered for a line left for a person. The department keys a JD Edwards account string
    (`business unit.object.subsidiary`) and a remark for each line and wants to confirm each line's tax.
  - **What:** Only where a chart is imported (`account_lines.in_use`); an install without one keeps its screen,
    row and tests. A line's Account box is a text box (the picker's free kind) holding the whole string: the server
    splits what is posted (`account_lines.posted`) and the chart's four checks say what is wrong on the line; a
    string the chart does not have stays in the box and keeps Approve closed, never replaced by the account before.
    Under the boxes: the account's name, its business unit and why (`decided_by` in plain words), or "To fix" with
    the line's reason and its `candidates` as buttons. Each line's taxes come from `taxmath.invoice_taxes`, once a
    draw: each tax its own amount, "read" when printed on the line, a mark when it is not the invoice's usual tax,
    the amount keyed under Amount when a tax stays in the line, one sentence a tax under the lines. The Remark box
    (D-213) shows the suggestion with a dashed edge until visited and posts it blank, so it follows the account;
    its count is under it and past 30 nothing is cut. The account list is fetched once
    (`GET /review/accounts.json`, kept by the browser as the vendor list is) and searched in the page
    (`account_search.js`): by any part of the number with or without dots, words, a business unit's name; 50 rows
    drawn. The frame: two panes from 1200 px (400 and 690 px at the least), stacked under it, the menu behind a
    button below 1820 px, a compact header with the rest under More details, "Keyed on the pay item" in one line
    from `jde.voucher_view`. Keys: Ctrl+Enter approves (twice when something is to check), F8 the next thing to fix.
  - **Measured:** the sweep (`tests/web/test_screens_fluid.py`: 12 widths from 1000 to 2560 px x 3 heights, a 17 px
    scrollbar, three invoices) finds no sideways scroll and nothing outside its card; the four-line invoice's first
    line is at 433 px in a 1280 x 700 window (about 1,690 before). Page weight with the made-up chart (146 accounts
    offered, a 10 KB list): one line 97 → 85 KB, four lines 117 → 108 KB, fifteen lines 182 → 182 KB (its rows now
    carry each line's candidates and remark); the page no longer grows with the chart.
  - **Not chosen:** rows as `<div>` blocks, container queries, a drag handle between the panes (the plan's second
    version: the table row is already a grid, and media queries pass the sweep); a hidden select behind the Account
    box (it puts the earlier choice back when what is typed names nothing).
  - **Left (stage 2):** the no-chart row in the new columns; compact rows and a filter for long invoices (a long
    invoice shows the accounts offered for the line in hand only); one pane at a time under 1200 px; the supplier
    change; pay items as groups for two tax areas (held with its sentence today); editing what is keyed on the pay
    item; Ctrl+D copying the remark; the first press on the Approve button (not Ctrl+Enter) approves at once;
    "Supplier" for "Vendor" everywhere; the findings strip rebuilt (it is the checks card, compacted).
  - (`ap_coder/web/account_lines.py`, `ap_coder/web/invoice_view.py`, `ap_coder/web/pages/invoice.py`,
    `ap_coder/web/roles.py`, `ap_coder/web/templates/invoice.html`, `_invoice_lines.html`, `_invoice_live.html`,
    `ap_coder/web/static/invoice.css`, `picker.css`, `js/invoice.js`, `js/picker.js`, `js/account_search.js`;
    `tests/web/test_account_lines.py`, `tests/web/test_account_picker.py`, `tests/web/test_screens_fluid.py`,
    `tests/review_page.py`)
- **D-215** · **A line's amount is read from one column of the line table, and lines that do not add up are left
  for a person, never balanced.** After B-2106 a row's own arithmetic could pick any figure of the row as its
  amount, and a line "Charges not in the line table" of whatever was missing hid a wrong reading (B-2197 to B-2203).
  - **Decision:** The amount's column is chosen once for the table: by its heading (`_amount_heading`: after a
    discount column only the headings right of it; never one that says the tax is in it; one that says before tax
    over a plain one only beside a tax column or a total with tax, else the plain one; of equals the first), then by
    where the numbers are printed (`_columns_under`: a heading has the column of numbers printed under it, else
    headings and columns are paired in the order they come across the page, never a heading with the numbers
    printed wholly to its left; a heading over words, a tax code's, has none). The numbers may change the COLUMN, not
    a row's figure: a column of money that makes the amount read with the tax on every row that prints one is the
    amount before tax; another column headed as an amount that adds up to the invoice's subtotal is the amount.
    A charge outside the table is a line only when it is read from the page (the freight beside the subtotal, an
    amount under its own label). Lines left not adding up stay as read: SUBTOTAL_MISMATCH closes Approve, and
    LINE_AMOUNTS_TO_CHECK says whether the amounts include the tax or which column to check. A tax cell not found
    is "not read"; a column says only about the tax it names; the tax amounts read are kept on the line
    (`taxes_read`), a "Tax" column on an invoice that charges two taxes excepted (it names neither).
    A row's discount that explains its amount makes the line's unit price the price after it, so quantity × unit
    price is the amount (B-2269). A row of the supplier's account ("Previous balance", "Payment received", "Balance
    forward" above the first line) is no line: every reading leaves it out and ACCOUNT_ROWS_LEFT_OUT names it
    (B-2270).
  - **Not chosen:** Working out the amount before tax as total less tax on a "Tax | Total" table (nothing on the
    page proves it line by line: a person enters it); pairing a heading with the nearest numbers (the column before
    ends nearer a left-aligned heading than its own); a partial tax code for a line when only one of two taxes has
    a column (`line_tax.prefill` takes whole codes: the amounts decide, as before the tax columns were read).
  - **Where:** `ap_coder/capture/reader/line_items.py` (`_amount_heading`, `_Columns`, `_columns_under`),
    `ap_coder/capture/bridge.py` (`printed_taxes`), `ap_coder/ai/offline_coder.py` (`_charges_outside`,
    `_lines_to_check`), `ap_coder/ops/help.py`; `tests/capture/test_line_table_columns.py`.
- **D-216** · **Several files can be ONE voucher: a person ticks them on the Add invoices page and says whether they
  are the pages of one invoice or the receipts of one expense claim; they are joined into one document, and can be
  made separate invoices again while in review** (stage 2; 10 Nov 2026).
  - **Why:** every file became its own invoice. An expense claim comes as several phone pictures of receipts, and a
    scanner or a phone saves an invoice's pages one by one: each was read as an invoice of its own, with its own
    supplier and total.
  - **What:** on the Add invoices page, the files chosen for upload (two or more: "These files are" one voucher) or
    ticked in the invoices folder's list are joined in the order shown, which arrows change (`intake/joined.py`
    `join`): a picture becomes its pages as one coming in alone does, a PDF gives its pages. The one PDF
    ("IMG_0412 and 2 more.pdf") goes in the import queue like any file; the files move to `originals/` as they came,
    and `joined_files` (by `_SCHEMA`, no schema version) keeps which pages are whose, by the document's SHA-256.
    **Pages of one invoice:** nothing else changes: the document is read by the normal path, the 60-page limit and
    its finding included. **Receipts of one claim:** `InvoicePipeline._read_claim` reads each receipt's pages on
    their own as an invoice is, and `coding/claim.py` makes one voucher of them: the receipts' lines in order, named
    with their merchant and coded as each receipt was; a receipt whose lines do not add up to its total gives one
    line (merchant and date, its amount before tax); each line carries the tax its own receipt charged
    (`LineItem.taxes_read`, from that receipt's own `invoice_taxes`, so a tip takes none); the totals and the tax
    lines are the receipts' added up. A receipt in another currency than most, or whose total could not be read,
    adds nothing: a 0.00 line and the finding CLAIM_RECEIPT_NOT_ADDED naming the file and its page. With as many
    receipts in each of two currencies the claim is put in CAD (else the first by its letters), never the first
    file's, the finding names the receipts of both, and the currency is the person's to set (B-2285). Once the
    person types the receipt's amount on its line the finding goes and the claim's subtotal and total take it,
    while they are still the ones without it (`claim.totals_follow`, B-2286). The supplier
    and the invoice number are left empty, and MISSING_VENDOR and MISSING_INVOICE_NUMBER say why in a claim's words
    (the employee being repaid; a reference of your own), on the first reading and on every check after it
    (`finalise_coding`). The review screen lists the files ("page 3 is from IMG_0412.jpg") over the one document,
    and "Make these separate invoices again" (`POST /review/{id}/separate`, a Clerk's) removes the voucher, puts
    the files back in the invoices folder and reads each on its own. A file that is one of a voucher's files counts
    as in AP Coder wherever a file's content is asked (the upload's and the folder's skip, the folder's list), and
    joining it into another voucher is refused with where it is ("page 2 of invoice #14"): from the moment it is
    joined, its voucher read or still in line ("page 2 of IMG_0411 and 1 more.pdf, a voucher waiting to be read"),
    the join and the queueing being one step under the pick-up's lock (B-2283). A file read as an invoice of its
    own and still in review is taken into the voucher instead (that invoice is removed; one approved or rejected
    is refused as before): what a separation did is undone the same way.
    **Two tax areas (B-2288):** JD Edwards takes one tax area a pay item and AP Coder makes one pay item (D-210),
    so a claim with the GST on some receipts and Ontario's HST on another is held by JDE_TAX_AREA. Each receipt's
    tax area is kept as read (`joined_files.tax`), and the finding says what to do in a claim's words, naming the
    files of each: make them separate invoices again, then choose each tax area's receipts again on Add invoices
    as a claim of their own. Receipts already read on their own that show two tax areas are refused at the join,
    with the same words. Two pay items on one voucher are not made (left).
    **The audit trail (B-2284):** the events `joined` (who, which files, as what, the invoices taken in; the
    voucher's own once it is read: `Store.joined_read`) and `separated` (who, which files), on Activity and in
    the invoice's history. **Nothing left in `originals/` (B-2289):** a separation also removes the copy kept of
    the document as joined (pages set upright since); a deleted voucher's files go to `invoices/deleted` with its
    document, as a deleted invoice's file does, and are no voucher's files any more. A rejected voucher keeps
    them (it can be reopened and separated). A backup holds the whole invoices folder, `originals/` included.
  - **Measured:** three made-up phone pictures (fuel with GST, a lunch with GST and a tip, a room with HST) as one
    claim: 5 lines, 273.00 + 27.80 = 300.80, GST 3.00 / 1.20 / 0.20 / none on the tip, HST 23.40 on the room, no
    tax or total finding, read in 7 to 10 s (4 cores, OCR on the processor). Two scanned pages joined give the same
    header, lines, totals and findings as the same two pages scanned as one PDF.
  - **Not chosen:** a group in the import queue read file by file and merged afterwards (a second kind of queue
    row, and nothing to show as one document); the link kept on the invoice's own row (it is found by the
    document's SHA-256, so a failed read, a retry or pages set upright need nothing more); a supplier guessed from
    the first merchant; a capture of the whole claim (each receipt has its own: a claim is never approved without
    a person, and its fields are not boxed on the page).
  - **Left:** two pay items on one voucher (a claim of two tax areas is two vouchers); a claim of one receipt (a
    receipt alone in its tax area stays an invoice of its own, the employee chosen as its supplier by hand); a
    claim cover sheet; per-employee settings; adding a file to a voucher already read; a claim's
    receipt photographed sideways is read but shown as it was taken; a tip whose amount OCR reads before its
    label is not split out (the receipt is then one line for its amount before tax).
  - (`ap_coder/intake/joined.py`, `ap_coder/coding/claim.py`, `ap_coder/core/store/joined.py`,
    `ap_coder/ai/pipeline.py`, `ap_coder/web/pages/process.py`, `review.py`, `invoice.py`,
    `ap_coder/web/templates/process.html`, `_invoice_document.html`, `ap_coder/web/static/js/process.js`;
    `tests/intake/test_joined.py`, `tests/coding/test_claim.py`, `tests/web/test_web_joined.py`)

- **D-217** · **The coding rules are anyone's to change, on a page of their own that keeps every change, undoes any
  of them and says what each rule coded** (the same department's prototype, stage 2; 10 Nov 2026).
  - **Why:** The rules were a tab of *GL accounts & tax* that only an Admin could change on the server. The
    department wants anyone who reviews invoices to change them, and to see how the rules worked over time: who
    changed what, and which invoices a rule coded. Saving the rules also rewrote every rule as new (B-2231).
  - **What:** `/rules` (*Coding rules* under More; **Rules** in the Simple menu, one click). On the server
    `GET /rules`, `POST /rules`, `/rules/add` and `/rules/undo` are a Clerk's and above (`web/roles.py`); a
    Read-only account does not open the page (as *GL accounts & tax*). A laptop is as before: on the office
    network a colleague's browser still does not change the rules (`identity.SETUP_CHANGES_HERE_ONLY`).
    `/accounts?tab=rules` goes to the page.
  - **A rule's number is its own** (`coding_rules.id`, which every database has had since schema 11: nothing to
    migrate). A save changes a rule in place, a number is never given to another rule, and an undone removal has
    its number again. A rule has no name of its own: it is named by what it matches (`Rule.label`, as `decided_by`
    already kept it), so "renaming" it is changing its vendor or words.
  - **History:** `coding_rule_history` (by `_SCHEMA`, no schema version): rule, when, who, the rule before and
    after as JSON. The page lists it newest first in plain words (`rules.change_words`), each with **Undo**: the
    rule goes back to what it was before that entry, and the undo is an entry (so it can be undone). When the rule
    is not as the entry left it, the page says "changed again since", names the newer change in the question it
    asks, and only then overwrites (`overwrite=1`; without it the store refuses, `CHANGED_SINCE`). An entry whose
    rule is already as it was before it has no Undo. An undo that would make a rule say the same as another is
    refused (`TWICE`, B-2294; said on the page): two rules alike are one too many, and the next save dropped one
    unasked. Not merged: which of the two numbers stays is a person's choice.
  - **Several people, one list (B-2292):** a save writes the whole list, so a save made on older rules removed what
    another person had just added. The page posts what it was drawn from (`version`: the history's last entry,
    `Store.rules_seen`) with a save and with an undo; the store compares it inside its write (`_unseen`), so two
    saves at the same moment cannot both pass. Changed since: nothing is written (`RulesChanged`), and the person
    is sent back to the page (`?kept=`, kept an hour), told who changed what, with the rules as they are now and
    their own changes still in the form (`rules.form_again`: a row left as their page showed it follows the newer
    change, one they changed, cleared or added stays theirs). A request that names no page is refused (409).
    *Add rule* reads and writes on one stamp.
  - **What a rule coded:** a line's `decided_by` already kept the rule's number and its words (D-212); nothing was
    added to the line. `summary_rules` (invoice, rule, lines, and what the rule said: its words as the line kept
    them, the line's own account and cost center), filled with the invoices' summary from the coding as approved,
    else as coded, gives each rule's "N lines on M invoices" off a covering index, and the invoice directory's
    `?rule=<number>` filter (`rules.rule_filter`). A number outlives what its rule says (B-2293): the count is of
    the lines that say what the rule says now (its words, its GL account, its cost center when it has one); the
    lines it coded while it said something else are said apart, "K lines on J invoices before it was changed
    (when; it was ...)", from the history's last change, with their own link (`?rule=<number>&was=1`), and the
    search says which it shows. Nothing is dated: a line says what coded it. `SUMMARY_RULES` is not raised (no
    rebuild of every summary for a table that starts empty): an invoice is counted once it is coded or changed
    from the first start of this version, the day kept as the setting `rule_use_since` and said on the page.
    Nothing is guessed for the invoices before. (The table as this stage first made it, without what the rule
    said, is dropped and made again empty at the next start: `_summary_schema`.)
  - **With a chart of accounts** the rule's account is one box holding `business unit.object.subsidiary`, read as
    the review screen's Account box is (`account_lines.posted`; joined and split only by `core/chart.py`). A rule
    added or changed is refused, and nothing saved, when the pair is not in the chart, takes no postings or is on a
    business unit that is not active, in the line check's own words (`validation.account_problem`), or names no
    business unit. An account never offered (reserved) is allowed: the rule is a person's decision, and the line's
    check still warns. A rule saved before the chart, left as it is, does not stop the others being saved: its row
    says what is wrong. An undo to such a rule is refused with the same words.
  - **Left:** the Account box has no search list (the review screen's is tied to its own script): the string is
    typed or pasted; the history shows its newest 100 entries.
  - (`ap_coder/coding/rules.py`, `ap_coder/coding/validation.py`, `ap_coder/core/store/rules.py`, `summary.py`,
    `migrations.py`, `_base.py`, `ap_coder/erp/directory.py`, `ap_coder/web/pages/rules.py`, `search.py`,
    `accounts.py`, `ap_coder/web/roles.py`, `identity.py`, `ap_coder/web/templates/rules.html`, `search.html`;
    `tests/core/test_rules_store.py`, `tests/web/test_web_rules.py`)

- **D-218** · **The last pieces of one laptop serving a few AP processors: a backup at a set time each day, one
  command to start sharing with sign-in, and a "why?" asked after a surprising correction** (10 Nov 2026).
  - **A backup at a set time.** The daily backup was made when AP Coder started, then not again for 20 hours: on a
    laptop started each morning the day's work was in no backup until the next morning. *Settings* → *Data &
    backups* now has *Back up every day at* (`backup_at`, kept with the other backup settings; 17:30 until one is
    saved, so an install that never saw the page gets the end-of-day backup too; saved empty, "off": no set time,
    the 20-hour rule as before). With a time, the day's backup is the one made since the clock last showed it
    (`backups.backup_due`): made as the clock passes it while AP Coder runs, and at the next check when it did not
    (the start's, as before), with why kept and shown ("made at start: AP Coder was not running at 17:30", "made
    late: this computer was asleep or busy at 17:30"). The clock is the business time zone's where `server.json`
    has one. Who looks at the clock: `app.DailyBackup`, every half minute, in the process that does the background
    work (the laptop's own; on the server its background process, never a web worker), and it reads the time again
    once a minute, so a time saved on the page is followed with no restart. The free-space check, the second folder
    and the retention are `backup_now`'s and `drop_old`'s, unchanged.
    - *A time changed while AP Coder runs (B-2295):* one moved to a time the clock already passed today is followed
      at the watch's next read, within a minute (it waited for the hourly check, with the reason "asleep or busy"):
      a new time that the clock showed more lately than the one before it has come (`DailyBackup._time_came`), and
      `auto_backup` makes the day's backup when none was made since. The page saves the time through
      `Store.save_backup_at`, which keeps when (`backup_at_changed`), so the reason says what happened: "made after
      the time was changed to 14:00 at 15:00". A time moved later also makes one backup at the change, as the rule
      (none since that time last passed) always did. The watch's first read counts from when the process began: a
      start half a minute before the time does not wait a day for it.
    - *On a company server (B-2296):* the page shows no time that nobody chose. With IT's hours it says them and
      where they are set (`AP_BACKUP_HOURS`, the `.env`) in place of the box; without them and with no time saved,
      the box is empty and the page says that 17:30, AP Coder's own time, applies until one is saved, and that it
      is in working hours. What applies is unchanged: the 17:30 stays for a shared laptop that never saw the page.
    - *Not chosen:* a time that also governs a server whose IT set `AP_BACKUP_HOURS`: those hours were chosen on
      purpose (B-1671), so they decide, and the page says so in place of the time. A Windows scheduled task for
      the backup: it needs AP Coder closed or a second process on the database, and administrator rights.
  - **`python -m ap_coder share`** (`ops/share.py`), the short way through docs/SERVER.md section 16. It checks,
    in order: an AP Coder already running on this data (shared: says the address; the owner's own window: asks to
    close it); the certificate (`server.json`'s, else the data folder's `tls` files, else the guide's self-signed
    one made with this computer's `openssl`, as section 3 already allowed by hand); the first Admin (asked for at
    the console: the name on the owner's approvals, a username, a password); then writes `server.json`, allows the
    second backup folder chosen in laptop mode (`allowed_folders`, D-109: from the console, by the person who chose
    it, and only while the data has no account yet; later `--allow-folder`, which also works while AP Coder is being
    shared, the list being read each time a folder is saved, B-2297), says the firewall rule and the address,
    and runs `serve --server` as the service's host does (`serve_command`, `serve_env`), in its window, with 2 web
    workers unless `server.json` says. `share --stop` asks that server to stop (`dashboard_stopped`).
    - *Never:* a start without sign-in or over plain HTTP (`insecure_http` in `server.json` is ignored here); a
      change to Windows' firewall or anything else that needs administrator rights (the rule is looked for with
      `netsh ... show rule` and its command printed); `server.json` before the first Admin exists (a person who
      stops at the password prompt still has a laptop that opens with a double-click). After it, D-211 holds: the
      accounts and `server.json` mark the folder, and a plain start refuses it.
    - *What could not be made simpler, and is said so:* the certificate without `openssl` (Windows writes no PEM
      files; no certificate library is a dependency, and none was added for this); the firewall rule; the Windows
      licence; a laptop asleep answers nobody. Not tried on Windows 11 (section 16 lists what to check there).
  - **The "why?" after a surprising correction.** There was one question already, before saving: the same
    correction of one situation made `why_after` times (D-72's lessons, `lessons.due_for_why`), its answer the
    lesson's reason. Added (`lessons.surprise`): after an approval, never holding it, the next page asks once "Why
    7340 and not 7420 for “Working lunch”?" when the person changed an account AP Coder expected: one it had coded
    with no one picking (not a guess: `decided_by` "model", or a reason that says so), or the first account offered
    on a line it left, when that came from this supplier's own history ("vendor", "supplier") with at least half
    of its lines. Of several, the one it was surest of (a rule's, then a coded line by its share, then an offered
    one): one question an invoice. Never for a line left with nothing offered, or offered from words alone, or a
    line the person added; never in a bulk approval (it does not go through the review screen); not after the
    question before saving. The message carries a short box, Skip, and one-click reasons built from what the line
    kept, no model asked: "This supplier's lines like this go there", what may have changed by where the account
    came from ("Last year's account no longer applies", "The rule does not fit this line"...), or "The unit named
    on the line" when the line names the business unit chosen; and "One-off: don't learn from it".
    - *Where the answer goes:* `explain_lesson` on that line's lesson, so What was learned → Lessons shows it and
      one correction with its reason reaches the AI (D-72). For an account only offered, the lesson holds no
      correction: its reason is listed all the same (`list_lessons`) and says both accounts, and is not sent to the
      AI (the coding memory already learned the account chosen).
    - *One-off:* the existing way a line is kept out of what teaches is Learning → Coding memory → Forget
      (`delete_feedback`, which withdraws what the line taught its lessons, B-1422). `forget_one_off` uses it for
      the line the question named, no other (a line like it on the same invoice stays; the question's lesson
      loses this approval's correction all the same), and writes `lesson_one_off` in the invoice's history. No
      second path: a one-off therefore leaves no row on the Lessons tab; the invoice's history and the Activity
      page say it.
    - *Whose answer it is (B-2291):* the answer belongs to the question that was asked. The approval keeps it
      (`why_questions`, by `_SCHEMA`: the invoice, the lesson, the line, the person as the approval recorded them,
      `core.login`); it goes with the approval's learning (a reopen, a rejection). `POST /review/{id}/why` takes an
      answer only from that person, for that question, once, while the setting is on and the approved invoice
      still asks it; another person gets 403 (an Admin too: Forget on Learning is the Admin's way), an answer to
      no question, a second answer or the setting off 409, in plain words, and nothing changes. The form's
      `situation` is only compared. On a laptop the one person is the computer's own login, as before.
    - *The setting:* Settings → Review, "Ask why after a surprising correction" (`why_surprise`), on until turned
      off. Off turns both questions off: "never asked" would not be true with the older question still asking.
  - (`ap_coder/core/store/backups.py`, `ap_coder/web/app.py`, `ap_coder/web/pages/settings.py`,
    `settings_data.html`; `ap_coder/ops/share.py`, `ap_coder/ops/cli.py`; `ap_coder/ai/lessons.py`,
    `ap_coder/web/pages/invoice.py`, `ap_coder/core/store/learning.py`, `lessons.py`, `base.html`, `js/app.js`;
    `tests/core/test_backup_time.py`, `tests/ops/test_share.py`, `tests/ai/test_why_question.py`)
- **D-221** · **What JD Edwards' files loaded is seen row by row and changed by hand; the demo invoices live in
  Settings; an older data folder is asked once which screens to show.** Three things the prototype's tester asked
  for.
  - *Seeing what is loaded.* "What is loaded" on JD Edwards files only said loaded or not yet. Each of the five
    (chart of accounts, business units, suppliers, last year's coding, vouchers already in JD Edwards) now opens
    `/jde-files/loaded/<list>`: a table with a search (each word somewhere in the row), exact filters on the
    columns with few values, a count, pages, and a CSV of everything the search keeps. A chart is 15,000 rows and
    a register up to 600,000, so no list is read whole: the count and the page are SQLite's (`LIMIT`, `OFFSET`),
    the CSV reads on from the last row it gave, 2,000 at a time, in the order of the list's own key
    (`core/store/loaded.py`). Codes are given as the text the table keeps (leading zeros). The suppliers show
    whichever columns their table has, under a known label or the column's name in words, so a version that keeps
    more of the master row shows it; a bank account is never among them (D-160).
  - *Who:* whoever opens JD Edwards files sees the lists and downloads them (server mode: a Clerk and above, as
    the page itself). Changing a row is an Admin's (a laptop: the one person).
  - *What is changed by hand:* a chart row's description, whether it may be posted to (its posting edit code made
    blank, or the file's own code again, `I` when the file's let it be posted to) and its GL account's meals tick;
    a business unit's name and status; a supplier's own fields (name, AP's own hold, note, tax numbers, terms and
    the rest), not its number nor JD Edwards' hold payment code, which are JD Edwards' to give. Last year's coding
    and the vouchers are a record of what happened: looked at, and forgotten whole by the buttons there were.
  - *Kept across the next load* (`core/store/by_hand.py`, the setting `hand_edits`, as B-2090's `chart_set_aside`
    keeps what was typed on a code that left the lists): each change is kept beside what the file said. The
    chart's and the business units' imports put it back on the rows they read before they compare and write, so
    the same file loaded again changes nothing and says which rows keep a change; a row the file no longer has
    keeps its change set aside. The supplier importer replaces what its file gives, and another piece of work was
    rewriting it, so it is not touched: `Store.import_vendor_master` (the one method written on `Store` itself)
    calls it and then puts back each field the import replaced, in one transaction, whoever imports. A field
    changed elsewhere since (the Vendors page) is no longer that change and is let go; so is one the file now
    agrees with, or one the person types back to the file's value. Rejected: an overlay applied at every read (the
    vendor list is read in dozens of places), triggers (a save on the Vendors page would be undone by one).
    One limit, said on the screen: a supplier renamed in JD Edwards arrives as a new row, and what was changed on
    the old name stays with the old name.
  - *Recorded:* each change is one event, `changed_by_hand` (Activity → Setup): who, the row, each field before →
    after; what a load put back is in the load's own event (chart, business units) or one of its own (suppliers).
  - *The demo invoices* were offered on the welcome page and on Import invoices, where a person setting up their
    own company met ten made-up invoices first. They are loaded and removed in Settings → Data & backups only,
    which says how many are loaded; the routes and their roles are unchanged (an Admin's in server mode), and so
    is `python -m ap_coder demo`. Import invoices still warns while the demo is loaded and none of your own
    invoices are (B-468), and points to Settings.
  - *An older data folder* keeps showing every page (`first_run` leaves an existing database as it is), so after an
    update the person saw the old menu and wondered whether it had worked. A laptop's database on which the
    Screens choice was never saved shows a card on the queue page, once: what is new, "Use the simple screens" or
    "Keep every page" (`POST /settings/about/screens`), and where to change it later. Either answer is saved, as
    is Everything saved on Settings → About. A new data folder starts Simple and the company server's screens are
    an Admin's: neither is asked.
  - (`ap_coder/core/store/loaded.py`, `by_hand.py`, `chart.py`, `__init__.py`, `dialect.py`;
    `ap_coder/web/pages/jde_loaded.py`, `jde_loaded.html`, `jde_files.html`, `web/roles.py`, `core/audit.py`,
    `ops/chart_cmd.py`; `_demo_card.html`, `settings_data.html`, `web/pages/process.py`; `_screens_card.html`,
    `web/core.py`, `web/pages/settings.py`, `review.py`; `tests/core/test_by_hand.py`,
    `tests/web/test_web_jde_loaded.py`, `test_web_process.py`, `test_simple_screens.py`)
