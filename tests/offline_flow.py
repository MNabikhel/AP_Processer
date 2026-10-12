"""The offline proof's walk through a running AP Coder, over HTTP on 127.0.0.1, as AP would use it: every page and
static file the pages name, the setup, every sample invoice (text PDFs, a scan, a photo, an emailed invoice), approval,
an export and its download, the Settings tabs, their checks and Help. Used by tests/test_offline_guard.py, against a
real ``python -m ap_coder serve`` under the network guard and inside a Linux network namespace with no network at
all (``python -m tests.offline_flow --netns``, run under ``unshare -rn``).

It also collects every address a page refers to (links, scripts, styles, images, fonts, form actions, CSS ``url()``):
anything that is not this server is reported, as a page that loads or links to the internet would be.

Standard library only (and PyMuPDF, which AP Coder needs anyway, to make the scan and the photo).
"""

from __future__ import annotations

import argparse
import email.message
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "samples"
PAGE = {"X-AP-Coder": "1"}
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}
NEVER_GET = ("/shutdown",)
MAX_PAGES = 600
MAX_VARIANTS = 4  # the same path with different queries: a few are enough
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # this computer: never through a proxy


# --- HTTP ---------------------------------------------------------------------------------------------------------


@dataclass
class Answer:
    status: int
    type: str
    body: bytes

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", "replace")

    def json(self) -> Any:
        return json.loads(self.text)


def request(
    base: str,
    method: str,
    path: str,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 120.0,
) -> Answer:
    url = path if path.startswith("http") else base + path
    sent = dict(headers or {})
    if method != "GET":
        sent = {**PAGE, "Origin": base, **sent}
    req = urllib.request.Request(url, data=data, method=method, headers=sent)
    try:
        with _OPENER.open(req, timeout=timeout) as response:
            return Answer(response.status, response.headers.get("Content-Type", ""), response.read())
    except urllib.error.HTTPError as exc:
        return Answer(exc.code, exc.headers.get("Content-Type", ""), exc.read())


def post_form(base: str, path: str, fields: list[tuple[str, str]] | dict[str, str] | None = None) -> Answer:
    pairs = list(fields.items()) if isinstance(fields, dict) else list(fields or [])
    body = urllib.parse.urlencode(pairs).encode()
    return request(base, "POST", path, body, {"Content-Type": "application/x-www-form-urlencoded"})


def post_files(base: str, path: str, files: list[Path], name: str = "files") -> Answer:
    boundary = uuid.uuid4().hex
    parts = []
    for f in files:
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{f.name}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n".encode()
            + f.read_bytes()
            + b"\r\n"
        )
    body = b"".join(parts) + f"--{boundary}--\r\n".encode()
    return request(base, "POST", path, body, {"Content-Type": f"multipart/form-data; boundary={boundary}"})


# --- What a page refers to ----------------------------------------------------------------------------------------

_CSS_URL = re.compile(r"url\(\s*['\"]?([^'\")]+)['\"]?\s*\)")
_CSS_IMPORT = re.compile(r"@import\s+['\"]([^'\"]+)['\"]")
_REF_ATTRS = {"href", "src", "action", "formaction", "poster", "data", "background", "srcset", "content"}


class _Refs(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.refs: list[tuple[str, str]] = []  # (tag.attr, value)
        self._style = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._style = tag == "style"
        attributes = dict(attrs)
        for name, value in attrs:
            if value is None:
                continue
            if name == "style":
                self.refs += [(f"{tag}.style", u) for u in _CSS_URL.findall(value)]
            if name not in _REF_ATTRS:
                continue
            if name == "content" and attributes.get("http-equiv", "").lower() != "refresh":
                continue
            if name == "content":
                value = value.split("url=", 1)[-1]
            if name == "srcset":
                self.refs += [
                    (f"{tag}.srcset", part.strip().split(" ")[0]) for part in value.split(",") if part.strip()
                ]
            else:
                self.refs.append((f"{tag}.{name}", value.strip()))

    def handle_endtag(self, tag: str) -> None:
        self._style = False

    def handle_data(self, data: str) -> None:
        if self._style:
            self.refs += [("style", u) for u in _CSS_URL.findall(data) + _CSS_IMPORT.findall(data)]


def references(answer: Answer) -> list[tuple[str, str]]:
    if "text/html" in answer.type:
        parser = _Refs()
        parser.feed(answer.text)
        return parser.refs
    if "text/css" in answer.type:
        return [("css", u) for u in _CSS_URL.findall(answer.text) + _CSS_IMPORT.findall(answer.text)]
    if "javascript" in answer.type:  # a script's own absolute addresses (fetch, import, a URL in a string)
        return [("js", m) for m in _JS_URL.findall(answer.text)]
    return []


_JS_URL = re.compile(r"""["'`]((?:https?:|wss?:)?//[A-Za-z0-9.-]+[^"'`\s]*)["'`]""")


def external(ref: str) -> bool:
    """A reference that leaves this server: another host (http, https, protocol-relative, ws) — never a mailto:/tel:
    link a person clicks (those open a program on this computer), nor a fragment, data: or javascript:."""
    ref = ref.strip()
    if not ref or ref.startswith(("#", "data:", "javascript:", "mailto:", "tel:", "blob:", "about:")):
        return False
    parts = urllib.parse.urlsplit(ref)
    if parts.scheme and parts.scheme not in ("http", "https", "ws", "wss", "ftp"):
        return parts.scheme not in ("mailto", "tel", "data", "javascript", "blob", "about")
    if not parts.netloc:
        return False
    if ref.startswith("//www.w3.org/") or parts.hostname == "www.w3.org" and parts.path.startswith("/2000/svg"):
        return False  # the SVG namespace: a name, never fetched
    return (parts.hostname or "") not in LOCAL_HOSTS


def local_url(url: str) -> bool:
    """An address on this computer (or one that is no address at all: data:, blob:, about:)."""
    parts = urllib.parse.urlsplit(url)
    return parts.scheme in ("data", "blob", "about") or (parts.hostname or "") in LOCAL_HOSTS


@dataclass
class Report:
    fetched: dict[str, int] = field(default_factory=dict)  # path -> status
    pages: list[str] = field(default_factory=list)  # the paths that answered an HTML page (200)
    external: list[str] = field(default_factory=list)  # "page: tag.attr -> url"
    errors: list[str] = field(default_factory=list)
    processed: dict[str, int] = field(default_factory=dict)  # ok, failed, pdfs, images
    steps: list[str] = field(default_factory=list)

    def failed(self) -> list[str]:
        return self.errors + [f"external reference: {e}" for e in self.external]

    def as_json(self) -> dict[str, Any]:
        return {
            "fetched": self.fetched,
            "pages": self.pages,
            "external": self.external,
            "errors": self.errors,
            "steps": self.steps,
            "processed": self.processed,
        }


def crawl(base: str, report: Report, start: tuple[str, ...] = ("/",)) -> None:
    """Every page reachable from ``start`` by a GET link (and every static file they name), once each."""
    todo = list(start)
    variants: dict[str, int] = {}
    while todo and len(report.fetched) < MAX_PAGES:
        path = todo.pop(0)
        if path in report.fetched:
            continue
        bare = urllib.parse.urlsplit(path).path
        variants[bare] = variants.get(bare, 0) + 1
        if variants[bare] > MAX_VARIANTS and bare != path:
            continue
        answer = request(base, "GET", path)
        report.fetched[path] = answer.status
        if answer.status == 200 and "text/html" in answer.type:
            report.pages.append(path)
        if answer.status >= 500:
            report.errors.append(f"GET {path}: {answer.status} {answer.text[:300]}")
        for where, ref in references(answer):
            if external(ref):
                report.external.append(f"{path}: {where} -> {ref}")
                continue
            target = urllib.parse.urljoin(base + path, ref)
            parts = urllib.parse.urlsplit(target)
            if (parts.hostname or "") not in LOCAL_HOSTS or parts.scheme not in ("http", "https"):
                continue
            if where.endswith(".action") or where.endswith(".formaction"):
                continue  # a form's address: posted, not fetched
            local = parts.path + (f"?{parts.query}" if parts.query else "")
            if local.startswith(NEVER_GET) or local in report.fetched:
                continue
            todo.append(local)


# --- Invoices to read ---------------------------------------------------------------------------------------------


def sample_pdfs() -> list[Path]:
    return sorted(SAMPLES.glob("*.pdf"))


def scan_and_photo(folder: Path) -> list[Path]:
    """A scanned invoice (a PDF of page pictures, no text), a phone photo (JPEG, tilted, greyed) and a PNG, made from a
    sample: read by OCR when it is installed (else they wait for a person, which is also offline)."""
    import fitz  # PyMuPDF

    source = SAMPLES / "pacific_BC_GST_PST_PO-77120.pdf"
    out = []
    with fitz.open(source) as doc:
        page = doc[0]
        pix = page.get_pixmap(dpi=200)
        scan = fitz.open()
        new = scan.new_page(width=page.rect.width, height=page.rect.height)
        new.insert_image(new.rect, pixmap=pix)
        scan.save(folder / "scanned_pacific.pdf")
        scan.close()
        out.append(folder / "scanned_pacific.pdf")
        photo = fitz.Pixmap(fitz.csGRAY, page.get_pixmap(matrix=fitz.Matrix(2, 2).prerotate(2)))
        photo.save(folder / "photo_pacific.jpg", jpg_quality=80)
        out.append(folder / "photo_pacific.jpg")
        page.get_pixmap(dpi=150).save(folder / "photo_northwind.png")
        out.append(folder / "photo_northwind.png")
    return out


def emailed_invoice(folder: Path) -> Path:
    """An .eml with an HTML body that points at the internet (a tracking pixel, a remote stylesheet) and a sample PDF
    attached: AP Coder reads the attachment and must never fetch what the body names."""
    msg = email.message.EmailMessage()
    msg["From"] = "billing@vendor.example"
    msg["To"] = "ap@company.example"
    msg["Subject"] = "Invoice CCO-26-10418"
    msg.set_content("Please find our invoice attached.")
    msg.add_alternative(
        '<html><head><link rel="stylesheet" href="https://203.0.113.7/mail.css"></head><body>'
        '<p>Invoice attached.</p><img src="http://203.0.113.7/pixel.gif"></body></html>',
        subtype="html",
    )
    pdf = SAMPLES / "chinook_AB_GST_CCO-26-10418.pdf"
    msg.add_attachment(pdf.read_bytes(), maintype="application", subtype="pdf", filename=pdf.name)
    path = folder / "vendor_mail.eml"
    path.write_bytes(bytes(msg))
    return path


# --- The walk -----------------------------------------------------------------------------------------------------


def _ok(report: Report, what: str, answer: Answer, *, allow_refusal: bool = False) -> Any:
    report.steps.append(f"{what}: {answer.status}")
    if answer.status >= 500 or (answer.status >= 400 and not allow_refusal):
        report.errors.append(f"{what}: {answer.status} {answer.text[:300]}")
        return None
    try:
        return answer.json()
    except ValueError:
        return None


def wait_for_job(base: str, report: Report, path: str = "/process/status", timeout: float = 600.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    snap: dict[str, Any] = {}
    while time.monotonic() < deadline:
        snap = request(base, "GET", path).json()
        if snap.get("state") in ("done", "failed", "idle", None) and snap.get("state") != "running":
            return snap
        time.sleep(0.5)
    report.errors.append(f"{path} still running after {timeout:.0f} s: {snap}")
    return snap


def invoice_ids(html: str) -> list[int]:
    return sorted({int(i) for i in re.findall(r'href="/review/(\d+)"', html)})


def walk(base: str, work: Path, *, invoices_folder: Path | None = None) -> Report:
    """The whole walk; problems are collected in the report (nothing raises for a page that failed)."""
    report = Report()
    health = request(base, "GET", "/healthz")
    _ok(report, "healthz", health)
    crawl(base, report)  # the empty dashboard first: setup cards, empty states

    _ok(report, "sample setup", post_form(base, "/accounts/sample"), allow_refusal=True)
    made = work / "made"
    made.mkdir(parents=True, exist_ok=True)
    extra: list[Path] = []
    try:
        extra = scan_and_photo(made)
    except Exception as exc:  # noqa: BLE001 - without PyMuPDF the scan and photo are left out, and said
        report.steps.append(f"scan and photo not made: {exc}")
    uploads = sample_pdfs() + [p for p in extra if p.exists()]
    started = _ok(report, f"upload {len(uploads)} invoices", post_files(base, "/process/upload", uploads))
    if started:
        snap = wait_for_job(base, report)
        result = snap.get("result") or {}
        report.steps.append(f"processed: {result.get('label')} (ok {result.get('ok')}, failed {result.get('failed')})")
        report.processed = {
            "ok": int(result.get("ok") or 0),
            "failed": int(result.get("failed") or 0),
            "pdfs": len(sample_pdfs()),
            "images": len(uploads) - len(sample_pdfs()),
        }
        if snap.get("state") != "done":
            report.errors.append(f"processing ended {snap.get('state')}: {snap}")

    if invoices_folder is not None:  # the invoices folder: an emailed invoice
        invoices_folder.mkdir(parents=True, exist_ok=True)
        shutil.copy2(emailed_invoice(made), invoices_folder / "vendor_mail.eml")
        if _ok(report, "process the invoices folder", post_form(base, "/process/folder"), allow_refusal=True):
            wait_for_job(base, report)

    _ok(report, "load the demo", post_form(base, "/demo/load", {"back": "/process"}), allow_refusal=True)

    queue = request(base, "GET", "/review").text
    ids = invoice_ids(queue)
    report.steps.append(f"review queue: {len(ids)} invoices")
    if ids:
        _ok(
            report,
            "bulk approve",
            post_form(base, "/review/bulk-approve", [("ids", str(i)) for i in ids]),
            allow_refusal=True,
        )
    exports_page = request(base, "GET", "/exports").text
    ready = sorted(
        {int(i) for i in re.findall(r'name="ids"[^>]*value="(\d+)"', exports_page)}
        | {int(i) for i in re.findall(r'value="(\d+)"[^>]*name="ids"', exports_page)}
    )
    report.steps.append(f"ready to export: {len(ready)}")
    for fmt in ("xlsx", "csv"):
        if not ready:
            report.errors.append("nothing approved to export")
            break
        answer = _ok(
            report,
            f"export {fmt}",
            post_form(base, "/exports/create", [("fmt", fmt)] + [("ids", str(i)) for i in ready]),
        )
        if answer and answer.get("redirect"):
            batch = re.search(r"batch=(\d+)", answer["redirect"])
            if batch:
                got = request(base, "GET", f"/exports/batches/{batch.group(1)}/download?fmt={fmt}")
                _ok(report, f"download batch {batch.group(1)}", got)
                if got.status == 200 and len(got.body) < 100:
                    report.errors.append(f"batch {batch.group(1)} download is {len(got.body)} bytes")
            ready = []  # exported: the next format has nothing left, and that is fine
            break

    # Settings: the checks a person can click (no Azure endpoint: the check says why and sends nothing)
    for path, fields in (
        ("/settings/reading/check", {}),
        ("/settings/reading/self-test", {}),
        ("/settings/reading/azure-test", {}),
    ):
        _ok(report, f"POST {path}", post_form(base, path, fields), allow_refusal=True)
    crawl(base, report, start=("/", "/help", "/settings", "/review", "/exports", "/search", "/learning"))
    return report


# --- A fake LM Studio on this computer --------------------------------------------------------------------------


class FakeLMStudio:
    """An OpenAI-compatible server on 127.0.0.1 with one chat model, as LM Studio is with a model loaded. Every
    request path is kept in ``seen``."""

    def __init__(self) -> None:
        self.seen: list[str] = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                pass

            def _send(self, status: int, payload: Any) -> None:
                body = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:  # noqa: N802
                fake.seen.append(f"GET {self.path}")
                if self.path == "/v1/models":
                    self._send(200, {"object": "list", "data": [{"id": "qwen2.5-7b-instruct", "object": "model"}]})
                elif self.path == "/api/v0/models":
                    self._send(
                        200,
                        {
                            "object": "list",
                            "data": [
                                {
                                    "id": "qwen2.5-7b-instruct",
                                    "type": "llm",
                                    "state": "loaded",
                                    "max_context_length": 32768,
                                }
                            ],
                        },
                    )
                else:
                    self._send(404, {"error": "not found"})

            def do_POST(self) -> None:  # noqa: N802
                fake.seen.append(f"POST {self.path}")
                length = int(self.headers.get("Content-Length") or 0)
                self.rfile.read(length)
                if self.path.endswith("/chat/completions"):
                    content = json.dumps({"lines": []})
                    self._send(
                        200,
                        {
                            "id": "c1",
                            "object": "chat.completion",
                            "created": 0,
                            "model": "qwen2.5-7b-instruct",
                            "choices": [
                                {
                                    "index": 0,
                                    "finish_reason": "stop",
                                    "message": {"role": "assistant", "content": content},
                                }
                            ],
                            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                        },
                    )
                else:
                    self._send(404, {"error": "not found"})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}/v1"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


# --- A real server ------------------------------------------------------------------------------------------------


def free_port() -> int:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Server:
    """``python -m ap_coder serve`` in a subprocess, on a data folder of its own; ``announced`` is True once it printed
    that it runs (it checks its own /healthz first, which must not go through a proxy)."""

    def __init__(self, data: Path, env: dict[str, str], python: str = sys.executable, timeout: float = 90.0) -> None:
        self.port = free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.data = data
        env = {
            **env,
            "AP_PRIVATE_DIR": str(data),
            "AP_USER_SETTINGS": str(data / "user_settings.json"),
            "PYTHONUNBUFFERED": "1",
        }
        self.output: list[str] = []
        self.announced = threading.Event()
        self.proc = subprocess.Popen(
            [python, "-m", "ap_coder", "serve", "--host", "127.0.0.1", "--port", str(self.port), "--no-browser"],
            cwd=ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
        )
        threading.Thread(target=self._read, daemon=True).start()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and self.proc.poll() is None:
            try:
                if request(self.base, "GET", "/healthz", timeout=2).status == 200:
                    break
            except OSError:
                time.sleep(0.3)
        else:
            self.stop()
            raise RuntimeError("the server did not start:\n" + "\n".join(self.output[-40:]))
        self.announced.wait(15)

    def _read(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            self.output.append(line.rstrip())
            if "AP Coder is running at" in line:
                self.announced.set()

    def token(self) -> str:
        try:
            return json.loads((self.data / ".ap_coder_server.json").read_text(encoding="utf-8"))["token"]
        except (OSError, ValueError, KeyError):
            return ""

    def stop(self) -> None:
        if self.proc.poll() is None:
            try:
                request(
                    self.base,
                    "POST",
                    "/shutdown",
                    b"{}",
                    {"X-AP-Coder-Token": self.token(), "Content-Type": "application/json"},
                    timeout=5,
                )
                self.proc.wait(20)
            except (OSError, subprocess.TimeoutExpired):
                self.proc.kill()
                self.proc.wait(10)


def run_walk(env: dict[str, str], work: Path, *, lm_studio: bool) -> dict[str, Any]:
    """A server on a fresh data folder, walked through; with ``lm_studio`` a fake LM Studio answers on 127.0.0.1."""
    fake = FakeLMStudio() if lm_studio else None
    env = dict(env)
    if fake is not None:
        env["AP_LLM_BASE_URL"] = fake.base_url
    else:
        env["AP_LLM_BASE_URL"] = f"http://127.0.0.1:{free_port()}/v1"  # nothing listens there: LM Studio is off
    data = work / ("data-lm" if lm_studio else "data")
    server = Server(data, env)
    try:
        report = walk(server.base, work, invoices_folder=data / "invoices")
    finally:
        server.stop()
        if fake is not None:
            fake.close()
    out = report.as_json()
    out["announced"] = server.announced.is_set()
    out["server_output"] = server.output[-60:]
    out["lm_studio_seen"] = fake.seen if fake is not None else []
    out["failed"] = report.failed()
    return out


# --- Inside a network namespace ------------------------------------------------------------------------------------


def loopback_up() -> None:
    """Bring ``lo`` up in a new network namespace (it starts down), without the ip command: SIOCSIFFLAGS."""
    import fcntl
    import socket
    import struct

    siocgifflags, siocsifflags, iff_up = 0x8913, 0x8914, 0x1
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        ifreq = struct.pack("16sH14x", b"lo", 0)
        flags = struct.unpack("16sH14x", fcntl.ioctl(s.fileno(), siocgifflags, ifreq))[1]
        fcntl.ioctl(s.fileno(), siocsifflags, struct.pack("16sH14x", b"lo", flags | iff_up))


def interfaces() -> list[str]:
    import socket

    return [name for _, name in socket.if_nameindex()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--netns", action="store_true", help="bring lo up first (inside unshare -rn)")
    parser.add_argument("--lm-studio", action="store_true", help="with a fake LM Studio on 127.0.0.1")
    parser.add_argument("--work", help="a folder for the data (default: a new temporary one)")
    args = parser.parse_args(argv)
    if args.netns:
        loopback_up()
    work = Path(args.work) if args.work else Path(tempfile.mkdtemp(prefix="ap-offline-"))
    env = {k: v for k, v in os.environ.items() if k != "AP_ALLOW_INTERNET"}
    out = run_walk(env, work, lm_studio=args.lm_studio)
    out["interfaces"] = interfaces()
    print(json.dumps(out))
    return 1 if out["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
