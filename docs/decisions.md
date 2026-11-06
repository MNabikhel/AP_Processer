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
- **D-182** · **Posting through the JD Edwards API at 500 vouchers: each voucher its own outcome and the run goes on
  past a late answer; a lost answer sent again only once JD Edwards could have finished it; holds and the open period
  read just before each voucher; Z-table keys unique per data folder and after a restore; accruals follow the postings**
  (scale goal phase 4, target 4; B-1857 to B-1863; changes D-105 where it says otherwise).
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
      reads and lookups. The exports page's job shows done of total, posted, being checked, refused, waiting, held.
    - **Never twice:** a lookup's "not there" for a send whose answer was lost is believed only once the send is
      `resend_minutes` (10) old (`_in_flight`): before, the row stays unknown and locked (B-1858). A refusal that JDE
      answered (its error shape) is looked up and believed at once, as before.
    - **Holds** (`coding.vendors.voucher_hold`): AP Coder's own and JD Edwards' as the last supplier sync left it,
      indexed until the vendor list changes; held, the voucher is queued saying why, the dry run refuses it.
    - **The open period** (`open_from`, `open_to`, `closed_period` in Settings → JD Edwards E1; none read from JDE:
      no orchestration offers it yet): a G/L date before it is moved to its first day (`JDE_GL_DATE_MOVED`, a
      warning on the review screen; the dry run's row says it; the go-ahead's question names it and its event keeps
      `gl_moved`) or held (`JDE_PERIOD_CLOSED`); after it, held. The same G/L date goes in the files. Just before
      each voucher the period is read again (a period closed mid-batch: the rest back to *checked*, dry run again);
      JDE refusing a G/L date as closed (`PERIOD_REFUSED`: PBCO, "closed period"…) holds back the run's others of
      that month and before.
    - **Z-table keys** (`jde.batch_number`, `invoice_site`, `batch_site`): VLEDTN = `APC` + invoice id + `-` + 8
      characters of a digest of the invoice's file and when it was added (the identity a restore uses, D-105);
      VLEDBT = prefix + batch + `-` + up to 4 characters of a digest of the batch (when made, its number, its
      invoices) as room allows in 15. Deterministic: the same each time a voucher is sent or its batch downloaded,
      so the files and the API agree; another invoice or batch under the same number gets others. An upgrade changes
      the keys of what is not sent yet: a dry run passed before it is "changed since its dry run" (dry run again).
    - **Accruals** (`jde_post.not_posted` in `accruals._not_in_erp`): queued, being sent, unknown, held back after its
      go-ahead, or refused (unless the batch's files were downloaded since) is not in the ERP; the kept answer's
      question carries that set, as postings change no invoice.
    - **Lookups, not scans** (B-1863): `jde.account_for` by (GL code, cost center), `held_vendor` by what makes a
      number or account the same.
  - **Not done, for the JDE team** (docs/JDE_ORCHESTRATIONS.md, items 15 to 20): the batch-level orchestration (one
    R04110ZA per batch) and a status lookup of many keys at once; R04110ZA's error IDs for a closed period; whether
    VLPST `A` overrides a supplier's hold; how long a voucher request can still run after AIS gave up (the 10
    minutes); a JDE batch still pending (F0011 ICIST / F0411 RPPOST) is not known to AP Coder, so a voucher posted
    into a pending batch counts as in the ERP.
  - **Measured:** 500 vouchers through the stand-in (each answered after 20 ms, one in ten late past a 0.3 s wait,
    half of them made, one in twenty refused): one go-ahead run of 40 s ends 475 posted and 25 refused, none unknown
    or queued, none made twice (525 sends, 75 lookups); the code before stops at the 8th (late), the other 492 queued.
    `account_for` × 5,000 with a 20,000-row mapping: 12.8 s → 0.04 s; `held_vendor` with 3,000 on hold: 46 → 14 ms.
  - (`erp/{jde_post,jde,accruals}.py`, `coding/vendors.py`, `web/pages/{exports,settings_jde}.py`,
    `web/templates/{exports,settings_jde}.html`, `ops/help.py`, `docs/{JDE_ORCHESTRATIONS,JDE_E1,BUGS}.md`;
    `tests/erp/test_jde_post_runs.py`, `tests/fake_ais.py` and the tests named in B-1857 to B-1863)
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
      discount); `review.split_line` (a part by percent, the last part's remainder).
    - Float `round` kept where no money is worked out or converting would cost more than it changes: rates
      (`round(rate, 6)`, the % shown), confidences, quantities and unit prices at 6 places, the PO match (`po.py`,
      compared within its own tolerance), the reports' running totals (`taxreturn.Report.totals`, `by_type`,
      `meals_totals`: sums of amounts at the cent, the same either way), and the facts kept in the invoices' summary
      (`tax_facts`, `meals_by_tax_line`, `self_assessed_base`: sums of amounts printed to the cent, different only
      for an amount with a third decimal at a tie, while a change there moves `SUMMARY_RULES` and builds every
      database's summary again at its next start, 86 s at 100,000 invoices: left for the next change that moves it).
  - **What counts as equal** (`money`, old → new):
    - nothing: `> 0.004`, `>= 0.005`, `< 0.005`, `<= 0.004` → `NIL` 0.005 (`nil`; the same at the cent);
    - the GL posting against the total: `> 0.005` → `EXACT` 0.005 (the same);
    - subtotal + tax = total: TOTAL_MISMATCH 0.02, the Tax check 0.015 → `CENT` 0.01 (B-1865); the tax lines = the
      tax total: TAX_LINES_TOTAL_MISMATCH 0.02 → `CENT` (B-1865); the lines = the total when prices include the
      tax: 0.011 (validation), 0.015 (the Tax check's hint) → `CENT` (the same at the cent). One sum of amounts
      printed rounded: a vendor who adds before rounding is at most a cent off (two or three taxes included);
    - the lines against the subtotal: SUBTOTAL_MISMATCH max(0.02, 0.01·n), the Tax check 0.015 →
      `per_line(n)` = 0.01·max(1, n) + 0.01, the rule of a tax's base (B-1866; a cent more from two lines). Not the
      Tax check's cent: SUBTOTAL_MISMATCH and TOTAL_MISMATCH are hard (Approve anyway never covers them, D-75), a
      vendor's rounding line by line is no misread, and POSTING_UNBALANCED asks for every cent already;
    - the lines against a tax's base, QST on GST's base, the line that is the difference: 0.01·n + 0.01 (tax,
      taxmath) → `per_line` (the same; the Tax check's two-rate base with no lines 0.01 → 0.02);
    - a tax against its base × rate, once or line by line: `check_taxes` one unit, `tax_band` (a unit × (n/2 + 1))
      a warning not an error; the Tax check 1.5 units, or the band → `TAX_UNITS` one unit for both, the band only
      the finding's severity (B-1867); one tax at two rates: a unit × (k + 0.5) → a unit per tax line (the same);
    - Azure's amount: 0.02 → `READ_AGAIN` (the same); LINE_MATH: max(0.02, 0.5%) → max(`READ_AGAIN`,
      `LINE_MATH_SHARE`) (the same); `line_tax._tax_base`: 0.05 → `PRINTED_BASE` (with the float error of the
      difference); the solver: max(2, n) + 0.5/rate cents half-even → `solver_cents` half up (the same at every rate
      in use, none ends in .5);
    - left apart, outside this item: the review screen's ✓ on a tax line (`web/invoice_view.tax_check`,
      0.01·max(1, n) + 0.01 on its amount, wider than TAX_CALC_MISMATCH), JD Edwards' PO-matched SUBTOTAL_MISMATCH (a
      cent a line, `erp/jde.py`), capture's `amounts_equal` (0.005).
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
  - **Benches** (venv312, old and new): `bench coding --realistic` the same (`lines.jsonl` byte for byte: accuracy
    93.8%, precision 98.2%, tax codes 97.4%, coverage 94.7%); `bench run --quick` the same (`evidence.jsonl` and
    `failures.jsonl` byte for byte: field accuracy 100.0%, verified precision 100.0% at 55.0% coverage, ECE 0.0189).
  - **CPU** (process time over the tax probe's 4,000 invoices, best of 5 × 3, old and new in turn): `check_taxes`
    52.0 → 57.4 µs (+10%), `math_check` 55.4 → 61.6 µs (+11%), `build_gl_distribution` 12.4 → 13.5 µs (+9%); all
    in decimals it was +22%, +40%, +54%. About 6 µs a review screen's checks.
  - **Laptop and server:** the same: functions of the coding and the rates only, no setting or mode.
  - **Left:** JD Edwards' pay items split cents their own way (`erp/jde.py` `_split`: each share cut toward zero, the
    rest on the largest; the GL distribution rounds each half up), J2's file: `money.split` would place them alike.
    The line named as a tax's difference could allow the tax's half unit over the rate, as the solver does.
  - (`coding/money.py`, `coding/{tax,taxmath,validation,line_tax,taxreturn,terms,review}.py`;
    `tests/coding/test_money.py`, `tests/coding/test_line_tax.py`)
