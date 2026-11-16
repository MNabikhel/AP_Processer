"""A stand-in for an Azure AI Document Intelligence resource, for the tests of the optional reader
(ap_coder/ai/di_http.py, D-245). Nothing here reaches Azure.

It listens on 127.0.0.1 over HTTPS, with a test-only certificate for ``*.cognitiveservices.azure.com`` (signed by a
made-up CA whose key was not kept, ``tests/fixtures/TEST_ONLY_di_*``): the client connects to the Azure host's name as
it would in use, and ``connect_to`` sends its socket here instead. It answers as Document Intelligence does at API
version 2024-11-30, as far as AP Coder uses it:

* ``POST /documentintelligence/documentModels/prebuilt-invoice:analyze?api-version=2024-11-30``: the key in
  ``Ocp-Apim-Subscription-Key`` (401 otherwise, Azure's error shape), the file as ``base64Source``; 202 with an
  ``Operation-Location`` on the same host;
* ``GET`` that address: ``running`` ``running_polls`` times, then ``succeeded`` with the answer;
* ``DELETE`` it: 204, and the reading is gone (a later GET is 404);
* ``GET /documentintelligence/documentModels/prebuilt-invoice``: the model's description.

The answer: the made-up recorded one (``tests/fixtures/di/<sample>.json``) of the sample whose bytes were sent, else
``answer``. ``script(step, *what)``: what the next requests of ``step`` ("analyze", "poll", "delete", "model") do
instead: an HTTP status (429 and 503 with ``Retry-After``), "drop" (close the connection without an answer),
"failed" (the reading failed), "elsewhere" (an ``Operation-Location`` on another host), "port" (one on another
port), "slow" (wait ``slow_seconds`` before answering). ``region``: sent as ``x-ms-region`` when set. Every request
is recorded in ``calls`` as (method, path, query, whether the right key came); the key itself is never kept.
"""

from __future__ import annotations

import base64
import hashlib
import json
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

FIXTURES = Path(__file__).parent / "fixtures"
CA_FILE = FIXTURES / "TEST_ONLY_di_ca.pem"
CERT_FILE, KEY_FILE = FIXTURES / "TEST_ONLY_di_server.cert.pem", FIXTURES / "TEST_ONLY_di_server.key.pem"
RECORDED = FIXTURES / "di"
SAMPLES = Path(__file__).resolve().parents[1] / "samples"
HOST = "ap-test.cognitiveservices.azure.com"
ENDPOINT = f"https://{HOST}/"
KEY = "S3cretKeyForTests0123456789abcdWXYZ"
API = "2024-11-30"
PREFIX = "/documentintelligence/documentModels/prebuilt-invoice"


def recorded(stem: str) -> dict[str, Any]:
    """The made-up recorded answer for the sample ``stem``."""
    return json.loads((RECORDED / f"{stem}.json").read_text(encoding="utf-8"))


def _by_content() -> dict[str, dict[str, Any]]:
    out = {}
    for path in sorted(RECORDED.glob("*.json")):
        pdf = SAMPLES / f"{path.stem}.pdf"
        if pdf.is_file():
            out[hashlib.sha256(pdf.read_bytes()).hexdigest()] = json.loads(path.read_text(encoding="utf-8"))
    return out


class FakeDi:
    def __init__(self, *, key: str = KEY, running_polls: int = 1, region: str = "", slow_seconds: float = 3.0) -> None:
        self.key, self.running_polls, self.region, self.slow_seconds = key, running_polls, region, slow_seconds
        self.answers = _by_content()
        self.answer: dict[str, Any] = {"apiVersion": API, "modelId": "prebuilt-invoice", "pages": [],
                                       "documents": []}  # fmt: skip
        self.calls: list[tuple[str, str, dict[str, str], bool]] = []
        self.scripts: dict[str, list[Any]] = {}
        self.results: dict[str, dict[str, Any]] = {}  # result id -> {polls, answer, failed}
        self.deleted: list[str] = []
        self.lock = threading.Lock()
        self.server: ThreadingHTTPServer | None = None

    def __enter__(self) -> FakeDi:
        self.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.stop()

    def start(self) -> None:
        import ssl

        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                pass

            def do_GET(self) -> None:  # noqa: N802
                fake._handle(self, "GET")

            def do_POST(self) -> None:  # noqa: N802
                fake._handle(self, "POST")

            def do_DELETE(self) -> None:  # noqa: N802
                fake._handle(self, "DELETE")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(CERT_FILE, KEY_FILE)
        self.server.socket = ctx.wrap_socket(self.server.socket, server_side=True)
        threading.Thread(target=self.server.serve_forever, args=(0.02,), daemon=True).start()

    def stop(self) -> None:
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
            self.server = None

    @property
    def port(self) -> int:
        assert self.server is not None
        return int(self.server.server_address[1])

    def connect_to(self, host: str, port: int) -> tuple[str, int]:
        """Where the client's socket for (host, 443) goes: here."""
        return ("127.0.0.1", self.port)

    def script(self, step: str, *what: Any) -> None:
        self.scripts.setdefault(step, []).extend(what)

    def steps(self, method: str) -> list[str]:
        """The calls of one method, in order ("POST", "GET", "DELETE")."""
        return [path for m, path, _, _ in self.calls if m == method]

    # -- answering
    def _next(self, step: str) -> Any:
        with self.lock:
            queue = self.scripts.get(step) or []
            return queue.pop(0) if queue else None

    def _handle(self, req: BaseHTTPRequestHandler, method: str) -> None:
        parts = urlsplit(req.path)
        query = {k: v[0] for k, v in parse_qs(parts.query).items()}
        right_key = req.headers.get("Ocp-Apim-Subscription-Key") == self.key
        length = int(req.headers.get("Content-Length") or 0)
        body = req.rfile.read(length) if length else b""
        with self.lock:
            self.calls.append((method, parts.path, query, right_key))
        step = ("analyze" if method == "POST" else "delete" if method == "DELETE"
                else "model" if parts.path == PREFIX else "poll")  # fmt: skip
        scripted = self._next(step)
        if scripted == "drop":
            req.close_connection = True
            req.connection.close()
            return
        if scripted == "slow":
            time.sleep(self.slow_seconds)
        if isinstance(scripted, int):
            return self._send(req, scripted, {"error": {"code": str(scripted), "message": "scripted"}},
                              {"Retry-After": "1"})  # fmt: skip
        if not right_key:
            return self._send(req, 401, {"error": {"code": "401", "message": "Access denied due to invalid "
                                                   "subscription key or wrong API endpoint."}})  # fmt: skip
        if query.get("api-version") != API:
            return self._send(req, 404, {"error": {"code": "404", "message": "Resource not found"}})
        if step == "analyze":
            return self._analyze(req, parts.path, body, scripted)
        if step == "model":
            return self._send(req, 200, {"modelId": "prebuilt-invoice", "apiVersion": API, "description":
                                         "Extract key information from invoices."})  # fmt: skip
        result_id = parts.path.rsplit("/", 1)[-1]
        with self.lock:
            result = self.results.get(result_id)
            if result is not None and step == "delete":
                self.deleted.append(result_id)
                del self.results[result_id]
        if result is None:
            return self._send(req, 404, {"error": {"code": "NotFound", "message": "Resource not found."}})
        if step == "delete":
            return self._send(req, 204, None)
        if scripted == "failed" or result["failed"]:
            failed = {"code": "InvalidContent", "message": "The file is corrupted."}
            return self._send(req, 200, {"status": "failed", "error": failed})
        result["polls"] += 1
        if result["polls"] <= self.running_polls:
            return self._send(req, 200, {"status": "running"}, {"Retry-After": "0"})
        return self._send(req, 200, {"status": "succeeded", "createdDateTime": "2026-11-16T10:00:00Z",
                                     "analyzeResult": result["answer"]})  # fmt: skip

    def _analyze(self, req: BaseHTTPRequestHandler, path: str, body: bytes, scripted: Any) -> None:
        if path != f"{PREFIX}:analyze":
            return self._send(req, 404, {"error": {"code": "404", "message": "Resource not found"}})
        try:
            data = base64.b64decode(json.loads(body)["base64Source"])
        except (ValueError, KeyError, TypeError):
            return self._send(req, 400, {"error": {"code": "InvalidRequest", "message": "Invalid request."}})
        answer = self.answers.get(hashlib.sha256(data).hexdigest(), self.answer)
        result_id = str(uuid.uuid4())
        with self.lock:
            self.results[result_id] = {"polls": 0, "answer": answer, "failed": scripted == "failed"}
        host = {"elsewhere": "elsewhere.example.com", "port": f"{HOST}:8443"}.get(scripted, HOST)
        location = f"https://{host}{PREFIX}/analyzeResults/{result_id}?api-version={API}"
        self._send(req, 202, None, {"Operation-Location": location, "Retry-After": "0"})

    def _send(self, req: BaseHTTPRequestHandler, status: int, body: Any, headers: dict[str, str] | None = None) -> None:
        data = json.dumps(body).encode() if body is not None else b""
        req.send_response(status)
        for name, value in (headers or {}).items():
            req.send_header(name, value)
        if self.region:
            req.send_header("x-ms-region", self.region)
        if data:
            req.send_header("Content-Type", "application/json")
        req.send_header("Content-Length", str(len(data)))
        req.end_headers()
        if data:
            req.wfile.write(data)
