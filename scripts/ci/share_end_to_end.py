"""``python -m ap_coder share`` end to end, the way a laptop's owner and a colleague use it (CI's job "share on
Windows"; D-224). From an AP Coder folder set up with its .venv:

    .venv\\Scripts\\python.exe scripts\\ci\\share_end_to_end.py [--work FOLDER] [--port 8547]

In a new data folder under ``--work`` (a temporary folder by default) holding one invoice to review, as laptop mode
left it: ``share`` makes its self-signed certificate (on Windows with this computer's openssl taken off the PATH, so
it finds Git for Windows' own, as on a laptop) and the first Admin (``--admin-from-stdin``: Windows' password prompt
reads only the keyboard), and serves. Then, over HTTPS by this computer's name (``share.names()``), trusting that
certificate and nothing else: the owner signs in (a new password, the authenticator app set up with the key the page
shows) and adds a colleague on the Users page; the colleague signs in with the temporary password shown, opens the
review queue and approves the invoice. ``share --stop`` stops it; ``share`` starts again on the same data, the
colleague signs in with a code and finds the invoice approved; ``share --stop`` again. Exit 0 when every step went
as it should; on a failure the share's window (its log) is printed. Runs on Linux and macOS too (without the
openssl step). Connects only to this computer.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import signal
import sqlite3
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from ap_coder.bench.day import Client, _json  # noqa: E402 - (after the path: run from a checkout)
from ap_coder.bench.load import Screen  # noqa: E402
from ap_coder.core import authenticator  # noqa: E402
from ap_coder.ops import share  # noqa: E402

WINDOWS = os.name == "nt"
STEM = "northwind_ON_HST_NW-2026-0912"  # a sample invoice that may be approved as it is (tests/conftest.py)
OWNER, OWNER_NAME = "pat.lee", "Pat Lee"
COLLEAGUE, COLLEAGUE_NAME = "sam.wu", "Sam Wu"
FIRST, CHOSEN = "correct horse battery staple 42", "a colleague's own long password 7"
UP_WAIT = 240.0  # seconds a share has to answer (its first start works the reports out first)
STOP_WAIT = 90.0  # seconds ``share --stop`` and the window it stops have to end
SECRET = re.compile(r'data-secret="([A-Z2-7 ]+)"')
INVOICE_LINK = re.compile(r'href="/review/(\d+)[?"]')  # (the queue's rows carry its view after the number)
TEMPORARY = re.compile(r'id="temporary".*?<code class="big">(.*?)</code>', re.S)


class Failed(Exception):
    """A step did not go as it should: said in plain words."""


def step(text: str) -> None:
    print(f"--- {text}", flush=True)


def check(ok: object, text: str) -> None:
    if not ok:
        raise Failed(text)


# --- The laptop -------------------------------------------------------------------------------------------------------


def child_env(data: Path, work: Path, port: int) -> dict[str, str]:
    """The share's environment: this data folder, nothing of the computer's own AP Coder; on Windows no openssl on the
    PATH (a laptop has Git for Windows' in Program Files, not on its PATH)."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("AP_")}
    env.update(AP_PRIVATE_DIR=str(data), AP_USER_SETTINGS=str(work / "user-settings.json"), AP_SERVER_PORT=str(port),
               AP_NO_PAUSE="1", BROWSER="none", PYTHONUTF8="1", PYTHONIOENCODING="utf-8")  # fmt: skip
    if WINDOWS:
        kept = [p for p in env.get("PATH", "").split(os.pathsep) if p and not (Path(p) / "openssl.exe").is_file()]
        env["PATH"] = os.pathsep.join(kept)
    return env


def seed(data: Path) -> int:
    """The data folder as laptop mode left it: the sample GL accounts, one invoice to review, its vendor known. Its
    number."""
    from ap_coder.core.store import Store
    from ap_coder.core.store.samples import load_sample_setup

    data.mkdir(parents=True)
    os.environ["AP_PRIVATE_DIR"] = str(data)
    store = Store(data / "ap_coder.db")
    load_sample_setup(store, cost_centers=False)  # (the dashboard's "Use the sample GL accounts")
    truth = json.loads((ROOT / "samples" / "ground_truth" / f"{STEM}.json").read_text(encoding="utf-8"))
    store.import_vendor_master([{"vendor_name": truth["vendor_name"], "erp_id": "V1"}])
    return store.add_invoice(ROOT / "samples" / f"{STEM}.pdf", truth,
                             {"requires_review": False, "adjusted_confidence": 0.97})  # fmt: skip


def start(env: dict[str, str], log: Path, *extra: str, given: str = "") -> subprocess.Popen:
    """``python -m ap_coder share`` in a window of its own (its output to ``log``), ``given`` typed on its input."""
    out = log.open("a", encoding="utf-8")
    child = subprocess.Popen([sys.executable, "-m", "ap_coder", "share", *extra], cwd=str(ROOT), env=env,
                             stdin=subprocess.PIPE, stdout=out, stderr=subprocess.STDOUT, text=True,
                             start_new_session=not WINDOWS)  # fmt: skip
    out.close()
    child.stdin.write(given)
    child.stdin.close()
    return child


def stop(env: dict[str, str], window: subprocess.Popen) -> None:
    done = subprocess.run([sys.executable, "-m", "ap_coder", "share", "--stop"], cwd=str(ROOT), env=env,
                          capture_output=True, text=True, timeout=STOP_WAIT)  # fmt: skip
    print(done.stdout.strip())
    check(done.returncode == 0 and "Stopped" in done.stdout, f"share --stop ended {done.returncode}: {done.stderr}")
    try:
        code = window.wait(timeout=STOP_WAIT)
    except subprocess.TimeoutExpired:
        raise Failed("the share's window did not end after share --stop") from None
    check(code == 0, f"the share's window ended with {code}, not 0")


def end(window: subprocess.Popen | None) -> None:
    """A share left running by a failure ended, with the server it started."""
    if window is None or window.poll() is not None:
        return
    if WINDOWS:  # its server is tied to it (a job object): it ends with it
        window.kill()
    else:
        os.killpg(window.pid, signal.SIGTERM)
    window.wait(timeout=60)


# --- A browser of a colleague's PC ------------------------------------------------------------------------------------


def browser(base: str, cert: Path) -> Client:
    """A browser that trusts the laptop's certificate (as a colleague's PC does once it is trusted) and nothing else."""
    client = Client(base)
    context = ssl.create_default_context(cafile=str(cert))

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args: object, **kwargs: object) -> None:
            return None

    client.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
                                                urllib.request.HTTPSHandler(context=context), NoRedirect)  # fmt: skip
    return client


def wait_up(client: Client, window: subprocess.Popen, log: Path) -> None:
    deadline = time.monotonic() + UP_WAIT
    while time.monotonic() < deadline:
        check(window.poll() is None, f"share ended ({window.returncode}) before it answered")
        status, body, _ = client.request("GET", "/healthz", timeout=5)
        if status == 200 and _json(body).get("server_mode"):
            return
        time.sleep(1)
    raise Failed(f"share did not answer over HTTPS at {client.base} within {UP_WAIT:g} s")


def post(client: Client, path: str, fields: object) -> dict:
    status, body, _ = client.post_form(path, fields)  # type: ignore[arg-type] - a list of pairs, as a form sends
    answer = _json(body)
    check(status == 200 and answer.get("ok", True), f"POST {path}: {status} {body[:300]!r}")
    return answer


def page(client: Client, path: str) -> str:
    status, body, _ = client.request("GET", path)
    check(status == 200, f"GET {path}: {status} {body[:300]!r}")
    return body.decode("utf-8")


def code_for(secret: str, used: int) -> tuple[str, int]:
    """The authenticator's code now, in a later step than ``used`` (a code is taken once)."""
    while int(time.time() // 30) <= used:
        time.sleep(1)
    now = time.time()
    return authenticator.code(secret, now), int(now // 30)


def first_sign_in(client: Client, username: str, password: str) -> tuple[str, int]:
    """A first sign-in: the password given, one's own chosen, the authenticator app set up with the key the page
    shows. That key, and the step its code was taken in."""
    answer = post(client, "/login", {"username": username, "password": password, "next": "/review"})
    check(str(answer.get("redirect", "")).startswith("/login/new-password"), f"no new password asked: {answer}")
    answer = post(
        client, "/login/new-password", {"current": password, "new": CHOSEN, "again": CHOSEN, "next": "/review"}
    )
    found = SECRET.search(page(client, answer["redirect"]))
    check(found, "the authenticator's page shows no key to type")
    secret = "".join(found.group(1).split())
    code, used = code_for(secret, 0)
    post(client, "/login/authenticator", {"code": code, "next": "/review"})
    check(any("session" in name for name in client.cookies), "no session after the authenticator's code")
    return secret, used


def approve(client: Client, invoice_id: int) -> None:
    """The invoice opened from the queue, checked and approved as the review screen sends it."""
    screen = Screen(invoice_id, page(client, f"/review/{invoice_id}"))
    check(screen.editable, "the review screen is read-only")
    screen.apply(post(client, f"/review/{invoice_id}/check", screen.data("inv")))
    check(screen.allow, "Approve is not open on the review screen")
    url, fields = screen.forms["inv"]["action"], screen.data("inv")
    answer = post(client, url, fields)
    if answer.get("ask_why"):
        answer = post(client, url, [*fields, ("why_done", "1")])
    check(answer.get("redirect"), f"the approval was not made: {answer}")


def status_of(db: Path, invoice_id: int) -> str:
    with sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True) as conn:
        return conn.execute("SELECT status FROM invoices WHERE id = ?", (invoice_id,)).fetchone()[0]


# --- The run ----------------------------------------------------------------------------------------------------------


def run(work: Path, port: int) -> None:
    data, log = work / "data", work / "share.log"
    env = child_env(data, work, port)
    if WINDOWS:
        saved, os.environ["PATH"] = os.environ["PATH"], env["PATH"]
        print(f"openssl, as share finds it on a PATH without one: {share.find_openssl() or '(none)'}")
        os.environ["PATH"] = saved
    invoice_id = seed(data)
    name = share.names()[0]
    base = f"https://{name}:{port}"
    cert = data / share.TLS_DIR / share.CERT_NAME
    step(f"share, the first time (the certificate, the first Admin {OWNER}), then serving at {base}")
    window = start(env, log, "--admin-from-stdin", given=f"{OWNER_NAME}\n{OWNER}\n{FIRST}\n")
    try:
        deadline = time.monotonic() + UP_WAIT
        while not cert.is_file() and window.poll() is None and time.monotonic() < deadline:
            time.sleep(0.5)
        check(cert.is_file(), "share made no certificate")
        owner = browser(base, cert)
        wait_up(owner, window, log)
        said = log.read_text(encoding="utf-8")
        check(base in said, f"share did not tell colleagues {base}")

        step(f"the owner signs in over HTTPS by the name {name} and adds {COLLEAGUE} on the Users page")
        first_sign_in(owner, OWNER, FIRST)
        answer = post(owner, "/admin/users/add", {"username": COLLEAGUE, "name": COLLEAGUE_NAME, "role": "clerk"})
        shown = TEMPORARY.search(page(owner, answer["redirect"]))
        check(shown, "the Users page shows no temporary password")

        step(f"{COLLEAGUE} signs in from another browser, opens the review queue and approves the invoice")
        colleague = browser(base, cert)
        secret, used = first_sign_in(colleague, COLLEAGUE, html.unescape(shown.group(1)))
        queue = page(colleague, "/review")
        check(str(invoice_id) in INVOICE_LINK.findall(queue), f"invoice {invoice_id} is not in the review queue")
        approve(colleague, invoice_id)

        step("share --stop")
        stop(env, window)
        check(status_of(data / "ap_coder.db", invoice_id) == "approved", "the invoice is not approved in the database")

        step("share again: the same data, the colleague signs in with a code")
        window = start(env, log)
        wait_up(colleague, window, log)
        answer = post(colleague, "/login", {"username": COLLEAGUE, "password": CHOSEN, "next": "/review"})
        check(str(answer.get("redirect", "")).startswith("/login/code"), f"no code asked: {answer}")
        post(colleague, "/login/code", {"code": code_for(secret, used)[0], "next": "/review"})
        approved = page(colleague, "/review?tab=approved")
        check(str(invoice_id) in INVOICE_LINK.findall(approved), "the approved invoice is not in the Approved tab")
        step("share --stop")
        stop(env, window)
    except BaseException:
        end(window)
        print(f"--- the share's window ({log}):\n{log.read_text(encoding='utf-8') if log.is_file() else ''}")
        raise


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--work", type=Path, help="a folder for the run's data folder (default: a new temporary folder)")
    p.add_argument("--port", type=int, default=8547)
    a = p.parse_args(argv)
    work = a.work or Path(tempfile.mkdtemp(prefix="share-e2e-"))
    if (work / "data").exists():
        print(f"{work / 'data'} is there already: give a --work folder without one.", file=sys.stderr)
        return 2
    work.mkdir(parents=True, exist_ok=True)
    try:
        run(work.resolve(), a.port)
    except Failed as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1
    print("OK: shared, signed in over HTTPS by name, approved, stopped, started again with the data kept.")
    if a.work is None:
        shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
