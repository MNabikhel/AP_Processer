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
  Microsoft's cloud being an exception to the offline rule to record as one; JD Edwards read and write through the 7
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
