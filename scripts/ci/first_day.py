"""The pilot tester's first day as ``docs/PROTOTYPE.md`` says it, step by step (CI's job "first day on Windows";
D-225). From an AP Coder folder set up with its .venv (or a developer's checkout):

    .venv\\Scripts\\python.exe scripts\\ci\\first_day.py [--work FOLDER] [--port 8548] [--start launcher|serve]

On the made-up company only (``bench/jde_company``, its exports ``bench/jde_exports``, its invoices
``bench/jde_invoices``), in a new data folder under ``--work`` (a temporary folder by default), the guide's sections
in their order:

1. the start: what the *AP Coder* shortcut runs (``APProcessor.bat`` -> ``scripts/launch.py``: ``--start launcher``,
   the default where this folder has its .venv), or the server it starts (``--start serve``: ``python -m ap_coder
   serve``, for a developer's checkout without one); the first sign-in (D-243: admin / admin, the tester's own
   password, the authenticator app set up with the key the page shows); the short menu ("Simple screens") the
   guide lists;
2. the made-up company in a second data folder (``set AP_PRIVATE_DIR=...``, ``demo --company``, ``serve``), deleted
   after, the first data folder untouched;
3. the five JD Edwards files loaded on Setup -> JD Edwards files (each shown before it is kept), each
   one's rows to see; the chart's settings, the meals accounts and the company number offered there; Sales tax
   accounts as a new installation sets them; the province (Settings -> Review); a bank account (Bank accounts);
4. ``pilot-score --journal --hold-out 2``; past invoices copied into the invoices folder and processed, then
   ``pilot-score --proposed --vouchers ... --journal ...``;
5. the day's invoices chosen on Add invoices and processed; the review queue; one invoice opened (its Account box
   filled, with why; its remark suggested; its bank account), approved; To key into JDE: Export, Download batch, the
   keying sheet's header fields and its line;
6. ``backup now``, Settings -> Data & backups, Activity, ``doctor``.

The commands are typed where the guide says: on Windows in ``scripts\\terminal.bat`` (its input piped), elsewhere
with AP Coder's Python. Each step prints a line or two of what it saw; a step that does not show what the guide says
ends the run (exit 1) saying what was seen, and AP Coder's window (its log) is printed. Connects only to this
computer: no AI model answers (as with LM Studio not started: the rule reader and last year's coding do the work),
and the setup may download nothing (``AP_ALLOW_INTERNET=0``).
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from ap_coder.bench import jde_exports, jde_invoices  # noqa: E402 - (after the path: run from a checkout)
from ap_coder.bench.day import PAGE, Client, _json  # noqa: E402
from ap_coder.bench.load import Forms, Screen, export_form, form_data, multipart  # noqa: E402
from ap_coder.core import authenticator  # noqa: E402

WINDOWS = os.name == "nt"
UP_WAIT = 300.0  # seconds a start has to answer (a first start works the reports out first)
READ_WAIT = 600.0  # seconds the invoices have to be read and coded
COMMAND_WAIT = 600.0  # seconds a command (demo, pilot-score, backup, doctor) has
PAST = 6  # past invoices for pilot-score --proposed (the guide says about 50 of the tester's own)
APPROVED = "one_line"  # the day's invoice approved: one line from a supplier that always goes to one account
MENU = ("To review", "Add invoices", "To key into JDE", "Find an invoice", "What was learned", "Rules")
SETUP = ("GL accounts & tax", "JD Edwards files", "Vendors", "Activity", "Settings")
# The keying sheet's header fields the guide names (section 5, To key into JDE), as the sheet labels them.
KEYING_FIELDS = ("Supplier number", "Invoice number", "Invoice date", "G/L date", "Currency", "Gross amount",
                 "Tax explanation code", "Tax rate/area", "Tax amount", "Taxable amount",
                 "G/L bank account")  # fmt: skip
USUAL_TAX = {"GST": "recoverable", "HST": "recoverable", "PST": "expense_to_line", "QST": "expense_to_line"}
INVOICE_LINK = re.compile(r'href="/review/(\d+)[?"]')
RUNNING_AT = re.compile(r"AP Coder is running at (http://127\.0\.0\.1:\d+)")
EXIT_MARK = "FIRST-DAY-EXIT="  # what the terminal is asked to echo after a command: its exit code
CHOSEN = "pilot tester first day"  # the tester's own password at the first sign-in
SECRET = re.compile(r'data-secret="([A-Z2-7 ]+)"')  # the authenticator's key, as the page shows it to type


class Failed(Exception):
    """A step did not go as the guide says: said in plain words."""


def step(text: str) -> None:
    print(f"--- {text}", flush=True)


def seen(text: str) -> None:
    print(f"    {text}", flush=True)


def check(ok: object, text: str) -> None:
    if not ok:
        raise Failed(text)


def plain(page: str) -> str:
    """A page's words, as a person reads them: tags out, entities read, spaces as one."""
    page = re.sub(r"<(script|style)\b.*?</\1>", " ", page, flags=re.S)
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", page)).split())


def says(page: str, *words: str) -> None:
    """The page shows each of ``words`` (read as a person reads it)."""
    text = plain(page)
    missing = [w for w in words if w not in text]
    check(not missing, f"the page does not say {missing}; it says: {text[:1500]}")


# --- The laptop -------------------------------------------------------------------------------------------------------


def child_env(data: Path, work: Path) -> dict[str, str]:
    """AP Coder's environment: this data folder, nothing of the computer's own AP Coder, no AI model to reach, no
    download."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("AP_")}
    # Unbuffered: its words reach the log as said (output to a file is block-buffered on Windows).
    env.update(AP_PRIVATE_DIR=str(data), AP_USER_SETTINGS=str(work / "user-settings.json"), AP_NO_PAUSE="1",
               AP_ALLOW_INTERNET="0", BROWSER="none", PYTHONUTF8="1", PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1",
               AP_LLM_BASE_URL="http://127.0.0.1:9/v1", AP_PAGE_READER_BASE_URL="http://127.0.0.1:9/v1")  # fmt: skip
    return env


def python() -> str:
    """AP Coder's Python: the folder's .venv where it has one, else this one (a developer's checkout)."""
    venv = ROOT / ".venv" / ("Scripts/python.exe" if WINDOWS else "bin/python")
    return str(venv) if venv.is_file() else sys.executable


def command(env: dict[str, str], *args: str, typed_first: str = "") -> str:
    """``python -m ap_coder ARGS`` typed where the guide says (on Windows in scripts\\terminal.bat, after
    ``typed_first``): what it printed. Fails on an exit code other than 0."""
    line = subprocess.list2cmdline(["python", "-m", "ap_coder", *args])
    if WINDOWS:
        typed = f"{typed_first}\n" if typed_first else ""
        typed += f"{line}\necho {EXIT_MARK}%errorlevel%\nexit\n"
        done = subprocess.run(["cmd", "/c", str(ROOT / "scripts" / "terminal.bat")], cwd=str(ROOT), env=env,
                              input=typed, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=COMMAND_WAIT)  # fmt: skip
        # The mark is in what the terminal printed (stdout); what AP Coder says on stderr is kept whole.
        mark = re.search(rf"^{EXIT_MARK}(-?\d+)\s*$", done.stdout, re.M)
        code = int(mark.group(1)) if mark else None
        said = (done.stdout[: mark.start()] if mark else done.stdout) + done.stderr
    else:
        done = subprocess.run([python(), "-m", "ap_coder", *args], cwd=str(ROOT), env=env, capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=COMMAND_WAIT)  # fmt: skip
        said, code = done.stdout + done.stderr, done.returncode
    check(code == 0, f"{line} ended {code}: {said.strip()[-3000:]}")
    return said.strip()


def start(how: str, env: dict[str, str], log: Path, *extra: str) -> subprocess.Popen:
    """AP Coder started in a window of its own (``how``: the launcher, or serve), its output to ``log``."""
    if how == "launcher":  # what the AP Coder shortcut runs; the rest of its line goes to the dashboard
        line = ["cmd", "/c", str(ROOT / "APProcessor.bat")] if WINDOWS else [str(ROOT / "APProcessor.command")]
    else:
        line = [python(), "-m", "ap_coder", "serve"]
    out = log.open("a", encoding="utf-8")
    child = subprocess.Popen([*line, *extra], cwd=str(ROOT), env=env, stdin=subprocess.DEVNULL, stdout=out,
                             stderr=subprocess.STDOUT, start_new_session=not WINDOWS)  # fmt: skip
    out.close()
    return child


def wait_up(client: Client, window: subprocess.Popen) -> dict:
    """Until the dashboard answers this computer's browser: what /healthz says."""
    deadline = time.monotonic() + UP_WAIT
    while time.monotonic() < deadline:
        check(window.poll() is None, f"AP Coder ended ({window.returncode}) before it answered")
        status, body, _ = client.request("GET", "/healthz", timeout=5)
        if status == 200 and _json(body).get("app") == "ap-coder":
            return _json(body)
        time.sleep(1)
    raise Failed(f"AP Coder did not answer at {client.base} within {UP_WAIT:g} s")


def address(log: Path, window: subprocess.Popen) -> str:
    """The address the window says AP Coder is running at."""
    deadline = time.monotonic() + UP_WAIT
    while time.monotonic() < deadline:
        found = RUNNING_AT.search(log.read_text(encoding="utf-8", errors="replace") if log.is_file() else "")
        if found:
            return found.group(1)
        check(window.poll() is None, f"AP Coder ended ({window.returncode}) before it said where it runs")
        time.sleep(0.5)
    raise Failed(f"AP Coder did not say where it runs within {UP_WAIT:g} s")


def stop(client: Client, data: Path, window: subprocess.Popen) -> None:
    """The window closed: the server asked to stop as the launcher asks one (its token), the window waited for."""
    if window.poll() is None:
        record = json.loads((data / ".ap_coder_server.json").read_text(encoding="utf-8"))
        client.request("POST", "/shutdown", b"", {**PAGE, "X-AP-Coder-Token": str(record.get("token", ""))})
        try:
            window.wait(timeout=60)
        except subprocess.TimeoutExpired:
            end(window)


def end(window: subprocess.Popen | None) -> None:
    """A window left running (a failure) ended, with what it started."""
    if window is None or window.poll() is not None:
        return
    if WINDOWS:
        subprocess.run(["taskkill", "/PID", str(window.pid), "/T", "/F"], capture_output=True)
    else:
        os.killpg(window.pid, signal.SIGTERM)
    try:
        window.wait(timeout=60)
    except subprocess.TimeoutExpired:
        window.kill()


# --- The browser ------------------------------------------------------------------------------------------------------


def page(client: Client, path: str) -> str:
    status, body, _ = client.request("GET", path)
    check(status == 200, f"GET {path}: {status} {body[:500]!r}")
    return body.decode("utf-8")


def post(client: Client, path: str, fields: object) -> dict:
    status, body, _ = client.post_form(path, fields)  # type: ignore[arg-type] - a list of pairs, as a form sends
    answer = _json(body)
    check(status == 200 and answer.get("ok", True), f"POST {path}: {status} {body[:500]!r}")
    return answer


def send(client: Client, path: str, files: list[Path], field: str = "file") -> dict:
    """Files chosen in a page's box and sent, as the page sends them."""
    body, kind = multipart([], [(field, f.name, f.read_bytes(), "application/octet-stream") for f in files])
    status, answer, _ = client.request("POST", path, body, {**PAGE, "Content-Type": kind}, timeout=600)
    said = _json(answer)
    names = ", ".join(f.name for f in files)
    check(status == 200 and said.get("ok", True), f"POST {path} with {names}: {status} {answer[:500]!r}")
    return said


def forms(page_html: str) -> Forms:
    parsed = Forms()
    parsed.feed(page_html)
    return parsed


def submit(client: Client, page_html: str, action: str, **values: str) -> dict:
    """The page's form that posts to ``action`` sent as a person sends it, the boxes in ``values`` typed in."""
    parsed = forms(page_html)
    owner = next((k for k, f in parsed.forms.items() if f["action"] == action), None)
    check(owner, f"the page has no form that sends to {action}")
    return post(client, action, form_data(parsed.fields, owner, **values))


def card(page_html: str, card_id: str) -> str:
    """One part of a page (its element ``card_id``, to the end of its card), as words."""
    part = page_html.split(f'id="{card_id}"', 1)
    check(len(part) == 2, f"the page has no {card_id}")
    return plain(part[1].split(">", 1)[-1].split("</section>", 1)[0])


def wait_read(client: Client) -> dict:
    """Until the Add invoices page's card says the reading is done: its summary."""
    deadline, snap = time.monotonic() + READ_WAIT, {}
    while time.monotonic() < deadline:
        snap = _json(client.request("GET", "/process/status")[1])
        if snap.get("state") == "done" and snap.get("result"):
            return snap["result"]
        time.sleep(1)
    raise Failed(f"the invoices were not read within {READ_WAIT:g} s: {snap}")


# --- The made-up company ----------------------------------------------------------------------------------------------


def company(work: Path) -> tuple[Path, Path, Path, Any, dict[str, Any]]:
    """The made-up company's five exports (last year's books), the day's invoices (its seven, dated in the days before
    today, as ``demo --company`` makes them) and past invoices of its vouchers, as files: (exports, the day's, past
    ones, the books, the day's invoices by name)."""
    exports, today, past = work / "jde_exports", work / "invoices_today", work / "invoices_past"
    on = dt.date.today()
    truth = jde_exports.write_all(exports, year=on.year - 1)
    day = jde_invoices.named(truth, on)
    jde_invoices.write(today, truth, day)
    jde_invoices.write(past, truth, jde_invoices.from_vouchers(truth, PAST))
    return exports, today, past, truth, {invoice.name: invoice for invoice in day}


# --- The guide, section by section ------------------------------------------------------------------------------------


def first_sign_in(client: Client) -> None:
    """The first sign-in, as the guide has it (D-243): admin / admin, the tester's own password and name, the
    authenticator app set up with the key shown (its code worked out here, as the phone does)."""
    step("1. the first sign-in: admin / admin, a password of one's own, the authenticator app")
    status, _body, _ = client.request("GET", "/review")
    check(status == 303, f"the review queue opened before any sign-in ({status})")
    says(page(client, "/login"), "username admin and the password admin")
    answer = post(client, "/login", {"username": "admin", "password": "admin", "next": "/review"})
    check(str(answer.get("redirect", "")).startswith("/login/first"), f"admin / admin led elsewhere: {answer}")
    name = re.search(r'name="name" value="([^"]*)"', page(client, answer["redirect"]))
    check(name and name.group(1), "the first sign-in offers no name for the approvals")
    answer = post(client, "/login/first", {"name": html.unescape(name.group(1)), "new": CHOSEN, "again": CHOSEN,
                                           "next": "/review"})  # fmt: skip
    found = SECRET.search(page(client, answer["redirect"]))
    check(found, "the authenticator's page shows no key to type")
    secret = "".join(found.group(1).split())
    post(client, "/login/authenticator", {"code": authenticator.code(secret, time.time()), "next": "/review"})
    check(any("session" in cookie for cookie in client.cookies), "no session after the authenticator's code")
    seen(f"signed in as admin ({html.unescape(name.group(1))}), the authenticator app set up")


def section_1(client: Client) -> None:
    first_sign_in(client)
    step("1. the dashboard opens with the short menu (Simple screens)")
    rail = page(client, "/review").split('<aside class="rail"', 1)[-1].split("</aside>", 1)[0]
    labels = [html.unescape(x) for x in re.findall(r'<span class="label">([^<]+)</span>', rail)]
    check(labels[: len(MENU)] == list(MENU), f"the menu is {labels}, not {list(MENU)} first")
    check('id="nav-setup">Setup<' in rail, "the menu has no Setup")
    setup = [html.unescape(x) for x in re.findall(r'<span class="label">([^<]+)</span>',
                                                  rail.split('id="nav-setup"', 1)[1])]  # fmt: skip
    check(setup[: len(SETUP)] == list(SETUP), f"Setup is {setup}, not {list(SETUP)}")
    says(page(client, "/settings/about"), "Screens", "Simple: the short menu", "Everything: every page")
    seen(f"menu: {', '.join(MENU)}; Setup: {', '.join(SETUP)}; Settings -> About -> Screens")


def section_2(work: Path, data: Path) -> None:
    tried = work / "try"
    step(f"2. a made-up company in a second data folder {tried}: demo --company, then serve")
    env = child_env(tried, work)
    before = sorted(p.name for p in data.iterdir())
    said = command(env, "demo", "--company", typed_first=f"set AP_PRIVATE_DIR={tried}")
    check("7 invoices to review" in said, f"demo --company did not say its seven invoices: {said[-1500:]}")
    seen(next(line for line in said.splitlines() if "made-up company is loaded" in line)[:200])
    log = work / "try_serve.log"
    window = start("serve", env, log)
    try:
        client = Client(address(log, window))
        wait_up(client, window)
        queue = page(client, "/review")
        listed = set(INVOICE_LINK.findall(queue))
        check(len(listed) == 7, f"its review queue lists {len(listed)} invoices, not seven")
        check('id="nav-setup"' in queue, "it does not show the short menu")
        seen(f"serve: {client.base}, seven invoices to review")
        stop(client, tried, window)
    except BaseException:
        end(window)
        said = log.read_text(encoding="utf-8", errors="replace") if log.is_file() else ""
        print(f"--- its window ({log}):\n{said}")
        raise
    for _ in range(10):  # "Delete C:\APCoder\try when you are done" (a file just closed may be held a moment)
        shutil.rmtree(tried, ignore_errors=True)
        if not tried.exists():
            break
        time.sleep(1)
    after = sorted(p.name for p in data.iterdir())
    check(after == before, f"the first data folder changed: {before} -> {after}")
    seen("the second folder deleted; the first one untouched")


def section_3(client: Client, exports: Path) -> None:
    step("3. Setup -> JD Edwards files: the five exports, each shown first, then loaded")
    first = page(client, "/jde-files")
    top = card(first, "checklist") if 'id="checklist"' in first else plain(first)
    says(first, "What is loaded", "Chart of accounts", "Business units", "Suppliers", "Last year's coding",
         "Invoices already in JD Edwards", "How to get it", "Download a blank template", "Check the file")  # fmt: skip
    check(top.count("Not yet") >= 5, f"the top of the page does not tick off what is loaded: {top[:600]}")

    for box, name in (("chart", jde_exports.CHART_FILE), ("units", jde_exports.UNITS_FILE),
                      ("suppliers", jde_exports.SUPPLIERS_FILE), ("suppliers", jde_exports.VENDOR_INFO_FILE),
                      ("journal", jde_exports.JOURNAL_FILE), ("vouchers", jde_exports.HEADERS_FILE)):  # fmt: skip
        shown = page(client, send(client, f"/jde-files/{box}/upload", [exports / name])["redirect"])
        summary = card(shown, f"{box}-summary")
        check(name in summary and "not loaded yet" in summary, f"{name} is not shown first: {summary[:500]}")
        part = shown.split(f'id="{box}-summary"')[-1]
        button = re.search(rf'data-load="{box}">.*?</span>(Load [^<]+)</button>', part, re.S)
        check(button, f"the summary of {name} has no Load button: {summary}")
        seen(f"{name}, shown first ({button.group(1)}): {summary.split('not loaded yet.', 1)[-1].strip()[:200]}")
        token = re.search(rf'action="/jde-files/{box}/load".*?name="token" value="([^"]+)"', shown, re.S)
        check(token, f"the summary of {name} has no Load button")
        loaded = page(client, post(client, f"/jde-files/{box}/load", {"token": token.group(1)})["redirect"])
        seen(f"  loaded: {card(loaded, f'{box}-said')[:220]}")
    top = card(loaded, "checklist") if 'id="checklist"' in loaded else plain(loaded)
    check("Not yet" not in top and top.count("See the") == 5, f"the top does not tick off the five: {top[:600]}")
    seen("What is loaded: the five ticked off, each with its rows to see")
    for what in ("chart", "units", "suppliers", "journal", "vouchers"):
        rows = page(client, f"/jde-files/loaded/{what}")
        check('type="search"' in rows, f"the rows of {what} have no search box")
        csv_link = re.search(r'href="([^"]+)"[^>]*download>.*?Download (?:all|the) [\d,]+ \w+.*? as CSV', rows, re.S)
        check(csv_link, f"the rows of {what} have no Download as CSV")
        changes = ">Change</a>" in rows
        check(changes == (what in ("chart", "units", "suppliers")), f"the rows of {what}: Change {changes}")
        if what == "chart":
            status, body, _ = client.request("GET", html.unescape(csv_link.group(1)))
            check(status == 200 and b"0021061010" in body, f"the chart's CSV lost the codes' leading zeros: {status}")
    seen("each one's rows: a search, Download as CSV (leading zeros kept); Change on the chart, units and suppliers")


def section_3_settings(client: Client, truth: Any) -> None:
    step("3. the chart's settings, the meals accounts, the company number")
    company_ = truth.company
    files = page(client, "/jde-files")
    says(files, "The chart's settings", "Accounts never offered",
         "Words in a name that retire a business unit or a supplier", "Home business unit", "Save the settings",
         "as meals accounts")  # fmt: skip
    never = ", ".join(f"*.{sub}" for sub in company_.reserved_subsidiaries)
    answer = submit(client, files, "/jde-files/chart/settings", never_offered=never,
                    home_business_unit=company_.main_business_unit)  # fmt: skip
    seen(card(page(client, answer["redirect"]), "chart-said")[:220])
    files = page(client, "/jde-files")
    meals = plain(files).split("Meals accounts", 1)[-1]
    check("read like meals:" in meals, f"the Meals accounts line names no account: {meals[:600]}")
    named = meals.split("read like meals:", 1)[1].split(" restaurant", 1)[0].strip()
    submit(client, files, "/jde-files/meals")
    seen(f"Meals accounts, ticked with one click: {named}")
    files = page(client, "/jde-files")
    check("/jde-files/company" in files, "the page does not offer the business units' company number")
    says(files, f"Use company {company_.number}")
    answer = submit(client, files, "/jde-files/company")
    seen(card(page(client, answer["redirect"]), "units-said")[:220])

    step("3. Setup -> GL accounts & tax -> Sales tax accounts; the province; Bank accounts")
    tax = page(client, "/accounts?tab=tax")
    says(tax, "Sales tax accounts", "How each sales tax posts")
    chosen = {f.name.split("_", 1)[1]: f.values[0] for f in forms(tax).fields if f.name.startswith("treatment_")}
    check(all(chosen.get(t) == how for t, how in USUAL_TAX.items()), f"Sales tax accounts are set {chosen}")
    seen("Sales tax accounts as a new installation sets them: GST and HST recovered, PST and QST in the expense")
    review = page(client, "/settings/review")
    says(review, "Your province")
    answer = submit(client, review, "/settings/review", our_province=jde_invoices.HOME_PROVINCE)
    says(page(client, answer["redirect"]), "Saved your province")
    seen(f"Settings -> Review -> Your province: {jde_invoices.HOME_PROVINCE}, saved")

    bank = page(client, "/accounts?tab=bank")
    says(bank, "Type each account as you key it in JD Edwards", "Description", "Currency", "Default",
         "Save bank accounts")  # fmt: skip
    parsed = forms(bank)
    owner = next((k for k, f in parsed.forms.items() if f["action"] == "/accounts/bank/save"), None)
    check(owner, "the Bank accounts tab has no grid to type in")
    typed = {"code": company_.bank_account, "name": "Operating account", "currency": "CAD"}
    fields = [(name, typed.pop(name) if name in typed else value)  # the grid's first row typed in
              for name, value in form_data(parsed.fields, owner)]  # fmt: skip
    saved = page(client, post(client, "/accounts/bank/save", [*fields, ("is_default", "0")])["redirect"])
    says(saved, "Saved: 1 bank account changed.", "Remove")
    check(f'value="{company_.bank_account}"' in saved, f"the grid does not show {company_.bank_account}")
    seen(f"Bank accounts: {company_.bank_account}, Operating account, CAD, the default")


def section_4(client: Client, env: dict[str, str], data: Path, exports: Path, past: Path) -> None:
    step("4. python -m ap_coder pilot-score --journal --hold-out 2")
    said = command(env, "pilot-score", "--journal", "--hold-out", "2")
    report = data / "pilot_score" / "journal_score.md"
    check(report.is_file(), f"no {report}: {said[-1500:]}")
    first = next((p.strip() for p in report.read_text(encoding="utf-8").split("\n\n") if p.startswith("Of ")), "")
    check(first and first in " ".join(said.split()), f"the command does not say the report's first paragraph: {said}")
    check(re.match(r"Of [\d,]+ lines keyed in .*, AP Coder would have coded [\d,]+ by itself", first), first)
    seen(first)

    step(f"4. {PAST} past invoices copied into the invoices folder and processed; pilot-score --proposed")
    process = page(client, "/process")
    found = re.search(r'action="/process/open-folder">\s*<button[^>]*title="([^"]+)"', process)
    check(found, "Add invoices does not name the invoices folder (its Open folder button)")
    folder = Path(html.unescape(found.group(1)))
    said = submit(client, process, "/process/open-folder")  # (it makes the folder and opens it in a window)
    seen(f"Open folder: {(said.get('toast') or {}).get('text', said)}")
    for pdf in sorted(past.glob("*.pdf")):
        shutil.copy2(pdf, folder / pdf.name)
    process = page(client, "/process")
    says(process, f"Process {PAST} files")
    submit(client, process, "/process/folder")
    result = wait_read(client)
    check(result.get("ok") == PAST, f"{result.get('ok')} of {PAST} past invoices were read: {result}")
    seen(f"Process {PAST} files: read")
    said = command(env, "pilot-score", "--proposed", "--vouchers", str(exports / jde_exports.HEADERS_FILE),
                   "--journal", str(exports / jde_exports.JOURNAL_FILE))  # fmt: skip
    report = data / "pilot_score" / "proposed_score.md"
    check(report.is_file(), f"no {report}: {said[-1500:]}")
    first = next((line for line in said.splitlines() if line.startswith("Of ")), "")
    check(f"Of {PAST} invoices read by AP Coder here" in first, f"the command does not say how they fared: {said}")
    seen(first[:400])


def section_5(client: Client, work: Path, today: Path, approved: Any, bank: str) -> None:
    chosen = sorted(today.glob("*.pdf"))
    step(f"5. Add invoices: the day's {len(chosen)} invoices chosen (Choose files), then Process uploaded invoices")
    says(page(client, "/process"), "Choose files", "Process uploaded invoices")
    send(client, "/process/upload", chosen, field="files")
    result = wait_read(client)
    check(result.get("ok") == len(chosen), f"{result.get('ok')} of {len(chosen)} invoices were read: {result}")
    seen(" ".join(plain(str(m.get("text", m))) for m in result.get("messages", []))[:300] or str(result))

    step("5. To review: each row says the first thing to fix; one invoice opened")
    queue = page(client, "/review")
    ids = list(dict.fromkeys(INVOICE_LINK.findall(queue)))
    check(len(ids) == len(chosen) + PAST, f"the review queue lists {len(ids)} invoices, not {len(chosen) + PAST}")
    says(queue, "Needs attention", "pick the account")
    number = approved.number
    screens = {int(i): page(client, f"/review/{i}") for i in ids}
    found = [i for i, shown in screens.items() if f'value="{html.escape(number)}"' in shown]
    check(len(found) == 1, f"the invoice {number} ({APPROVED}) is not in the review queue once: {found}")
    invoice_id, shown = found[0], screens[found[0]]
    screen = Screen(invoice_id, shown)
    accounts = [v for f in screen.fields if f.owner == "inv" and f.name == "line_account" for v in f.values if v]
    reasons = [v for f in screen.fields if f.owner == "inv" and f.name == "line_reason" for v in f.values if v]
    keyed_as = approved.lines[0].parts[0].account
    check(accounts == [keyed_as], f"its Account box holds {accounts}, not {keyed_as}")
    says(shown, "this supplier's usual account:", "lines last year")
    remark = re.search(r'data-remark-box value="([^"]*)"', shown)
    check(remark and remark.group(1), "its Remark box suggests nothing")
    check(bank in shown, f"its header does not show the bank account {bank}")
    seen(f"invoice {number}: Account {accounts[0]} ({html.unescape(reasons[0]) if reasons else '?'}); remark "
         f"{html.unescape(remark.group(1))!r}; bank account {bank}")  # fmt: skip

    step(f"5. Approve (invoice {number})")
    check(screen.editable, "the review screen is read-only")
    screen.apply(post(client, f"/review/{invoice_id}/check", screen.data("inv")))
    check(screen.allow, "Approve is not open on the review screen")
    url, fields = screen.forms["inv"]["action"], screen.data("inv")
    answer = post(client, url, fields)
    if answer.get("ask_why"):
        answer = post(client, url, [*fields, ("why_done", "1")])
    check(answer.get("redirect"), f"the approval was not made: {answer}")
    seen("approved")

    step("5. To key into JDE: Export puts it on a keying sheet; Download batch")
    exports = page(client, "/exports")
    says(exports, "JD Edwards keying sheet", "Export 1 invoice")
    form = export_form(exports)
    check(("ids", str(invoice_id)) in form, f"the approved invoice is not ticked to export: {form}")
    check(("fmt", "jde_keying") in form, f"the page does not put it on the keying sheet: {form}")
    made = page(client, post(client, "/exports/create", form)["redirect"])
    link = re.search(r'href="(/exports/batches/(\d+)/download[^"]*)"[^>]*download>', made)
    check(link, "the page offers no Download batch")
    says(made, f"Batch {link.group(2)} created", f"Download batch {link.group(2)}")
    status, sheet, _ = client.request("GET", html.unescape(link.group(1)))
    check(status == 200 and sheet[:2] == b"PK", f"the keying sheet did not download: {status} {sheet[:300]!r}")
    keying = work / "keying_sheet.xlsx"
    keying.write_bytes(sheet)
    keyed(keying, approved, bank)


def keyed(path: Path, invoice: Any, bank: str) -> None:
    """The keying sheet holds the header fields the guide names, then the line (account, amount, remark)."""
    import openpyxl

    book = openpyxl.load_workbook(path, read_only=True)
    rows = [[c for c in row if c is not None] for row in book.worksheets[0].iter_rows(values_only=True)]
    cells = [str(c) for row in rows for c in row]
    for label in KEYING_FIELDS:
        check(label in cells, f"the keying sheet has no {label!r}: {cells[:80]}")
    check(rows[0][:4] == ["Account number", "Account description", "Amount", "Explanation -Remark-"],
          f"the keying sheet's lines are not account, amount, remark: {rows[0]}")  # fmt: skip
    part = invoice.lines[0].parts[0]
    line = next((row for row in rows if row and row[0] == part.account), None)
    check(line and float(line[2]) == float(part.amount) and str(line[3]).strip(), f"its line is {line}")
    check(bank in cells, f"the keying sheet does not carry the bank account {bank}")
    seen(f"{path.name}: {', '.join(KEYING_FIELDS)}; then {line[0]} {line[2]:,.2f} {line[3]!r}")


def section_6(client: Client, env: dict[str, str], data: Path) -> None:
    step("6. backups, Activity, doctor")
    backups = page(client, "/settings/data")
    says(backups, "Back up now", "One is made automatically once a day at 17:30", "Also copy each backup to")
    said = command(env, "backup", "now")
    check("Backed up to" in said, f"backup now did not say where: {said}")
    seen(said.splitlines()[-1][:220])
    says(page(client, "/activity"), "Activity")
    said = command(env, "doctor")
    seen(next((line for line in said.splitlines() if "failure(s)" in line), said[-200:]))
    for line in said.splitlines():
        if line.startswith(("[WARN]", "[FAIL]")):
            seen(" ".join(line.split()))


def run(work: Path, port: int, how: str) -> None:
    data, log = work / "data", work / "ap_coder.log"
    env = child_env(data, work)
    exports, today, past, truth, day = company(work)
    step(f"the made-up company: {len(truth.accounts)} accounts, {len(truth.suppliers)} suppliers, "
         f"{len(truth.vouchers)} vouchers; {len(day)} invoices of the day, {PAST} past ones")  # fmt: skip
    client = Client(f"http://127.0.0.1:{port}")
    step(f"1. the start ({'APProcessor.bat' if how == 'launcher' and WINDOWS else how}) on a new data folder {data}")
    extra = ["--port", str(port)] + ([] if how == "launcher" else ["--no-browser"])
    window = start(how, env, log, *extra)
    try:
        health = wait_up(client, window)
        seen(f"answering at {client.base} (AP Coder {health.get('version', '?')})")
        section_1(client)
        section_2(work, data)
        section_3(client, exports)
        section_3_settings(client, truth)
        section_4(client, env, data, exports, past)
        section_5(client, work, today, day[APPROVED], truth.company.bank_account)
        section_6(client, env, data)
        step("the window closed")
        stop(client, data, window)
        check(window.returncode == 0, f"AP Coder's window ended with {window.returncode}, not 0")
    except BaseException:
        end(window)
        said = log.read_text(encoding="utf-8", errors="replace") if log.is_file() else ""
        print(f"--- AP Coder's window ({log}):\n{said}")
        raise


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--work", type=Path, help="a folder for the run's data folders (default: a new temporary folder)")
    p.add_argument("--port", type=int, default=8548)
    p.add_argument(
        "--start",
        choices=("launcher", "serve"),
        default=None,
        help="how AP Coder starts (default: the launcher where this folder has its .venv, else serve)",
    )
    a = p.parse_args(argv)
    how = a.start or ("launcher" if (ROOT / ".venv").is_dir() else "serve")
    work = a.work or Path(tempfile.mkdtemp(prefix="first-day-"))
    if (work / "data").exists():
        print(f"{work / 'data'} is there already: give a --work folder without one.", file=sys.stderr)
        return 2
    work.mkdir(parents=True, exist_ok=True)
    try:
        run(work.resolve(), a.port, how)
    except Failed as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1
    print("OK: the first day of docs/PROTOTYPE.md went as the guide says.")
    if a.work is None:
        shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
