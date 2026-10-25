"""A stand-in for a JD Edwards AIS Server, for the tests of the API connection (ap_coder/erp/jde_ais.py, D-104).

It listens on 127.0.0.1 (plain http, or HTTPS with the test-only certificate in tests/fixtures, signed by a test CA) and
answers as AIS does, as far as AP Coder uses it:

* ``GET /jderest/v2/defaultconfig``: the AIS version.
* ``POST /jderest/v2/tokenrequest``: a token for a known user, password and environment (403 otherwise, AIS's error
  shape); the token expires after ``token_seconds``; ``.../logout`` ends it.
* ``POST /jderest/v3/orchestrator/<name>``: the token in ``jde-AIS-Auth`` (an unknown or expired one: HTTP 444, as AIS
  answers), or HTTP Basic; a user without the orchestration in its role: 403; an unknown name: 404. The read
  orchestrations page their rows (``After...`` / ``MaxRows``); the voucher orchestration checks the supplier and each
  account, refuses a second voucher with the same supplier and invoice number (JDE's duplicate edit), keeps one
  voucher per idempotency key (EXISTS and the status lookup say who it is for: supplier, invoice number, gross, the
  fingerprint sent), and answers JDE's errors in its shape (HTTP 500, ``message`` → form request → ``errors``
  with CODE / TITLE / DESC, ``jde__status`` ERROR).
* ``script(name, *steps)``: what the next calls of an orchestration (or "token") do instead: an HTTP status, "slow"
  (answer after ``slow_seconds``, longer than the client waits), "slow-after" (do the work, then answer late: the
  request reached JDE), "drop" (close the connection without an answer).

Every request is recorded in ``calls`` ((path, the orchestration's inputs or {}), the password never kept).
"""

from __future__ import annotations

import base64
import json
import secrets
import ssl
import threading
import time
from collections.abc import Iterable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from ap_coder.erp import jde_ais

# Test-only certificates (a made-up CA, its key not kept, and the server certificate it signed for 127.0.0.1).
FIXTURES = Path(__file__).parent / "fixtures"
CA_FILE = FIXTURES / "TEST_ONLY_ais_ca.pem"
CERT_FILE, KEY_FILE = FIXTURES / "TEST_ONLY_ais_server.cert.pem", FIXTURES / "TEST_ONLY_ais_server.key.pem"
USER, PASSWORD, ENVIRONMENT = "APCODER", "Correct-Horse-9", "JPD920"


def jde_error(form: str, *errors: tuple[str, str]) -> dict[str, Any]:
    """An orchestration's error answer, as AIS sends it: the form request's errors with CODE, TITLE and DESC."""
    return {
        "message": {form: {"errors": [{"CODE": c, "TITLE": t, "DESC": t, "ERRORCONTROL": "1[10]"} for c, t in errors],
                           "warnings": []}},
        "exception": "Exception", "timeStamp": time.strftime("%Y-%m-%d:%H.%M.%S"), "userDefinedErrorText": "",
        "status": "ERROR", "jde__status": "ERROR",
    }  # fmt: skip


def sample_data() -> dict[str, list[dict[str, Any]]]:
    """A small company in JDE, in AP Coder's field names (the mapping's ``fields`` keys)."""
    return {
        "suppliers": [
            {
                "an8": "4001",
                "name": "Acme Office Supplies Ltd.",
                "tax_id": "123456789RT0001",
                "tax_id_2": "",
                "terms_code": "N30",
                "terms": "Net 30",
                "hold_code": "",
                "bank_transit": "00012-003",
                "bank_last4": "4567",
                "province": "ON",
                "currency": "CAD",
            },
            {
                "an8": "4002",
                "name": "Northern Courier Inc.",
                "tax_id": "987654321RT0001",
                "tax_id_2": "1234567890TQ0001",
                "terms_code": "N15",
                "terms": "Net 15",
                "hold_code": "H",
                "bank_transit": "",
                "bank_last4": "",
                "province": "QC",
                "currency": "CAD",
            },
        ],
        "gl_accounts": [
            {
                "account_id": "00012345",
                "business_unit": "CC100",
                "object": "6100",
                "subsidiary": "",
                "description": "Office supplies",
                "posting_edit": "",
                "level": "7",
            },
            {
                "account_id": "00012346",
                "business_unit": "CC100",
                "object": "6200",
                "subsidiary": "",
                "description": "Courier and freight",
                "posting_edit": "",
                "level": "7",
            },
            {
                "account_id": "00012347",
                "business_unit": "CC100",
                "object": "6200",
                "subsidiary": "01",
                "description": "Courier - same day",
                "posting_edit": "",
                "level": "8",
            },
            {
                "account_id": "00012348",
                "business_unit": "CC100",
                "object": "1310",
                "subsidiary": "",
                "description": "GST/HST receivable",
                "posting_edit": "",
                "level": "7",
            },
            {
                "account_id": "00012349",
                "business_unit": "CC100",
                "object": "6999",
                "subsidiary": "",
                "description": "Old account (closed)",
                "posting_edit": "I",
                "level": "7",
            },
        ],
        "business_units": [
            {
                "business_unit": "CC100",
                "description": "Head office",
                "company": "00001",
                "type": "",
                "posting_edit": "",
            },
            {
                "business_unit": "CC200",
                "description": "Montreal branch",
                "company": "00001",
                "type": "",
                "posting_edit": "",
            },
        ],
        "purchase_orders": [
            {
                "key": "4512-OP-00001-1",
                "order_number": "4512",
                "order_type": "OP",
                "order_company": "00001",
                "line_number": "1.000",
                "an8": "4001",
                "supplier_name": "Acme Office Supplies Ltd.",
                "description": "Printer paper",
                "quantity": 10,
                "unit_cost": 45.0,
                "amount": 450.0,
                "received": 10,
                "object": "6100",
                "subsidiary": "",
                "business_unit": "CC100",
                "next_status": "400",
            },
            {
                "key": "4512-OP-00001-2",
                "order_number": "4512",
                "order_type": "OP",
                "order_company": "00001",
                "line_number": "2.000",
                "an8": "4001",
                "supplier_name": "Acme Office Supplies Ltd.",
                "description": "Toner",
                "quantity": 2,
                "unit_cost": 120.0,
                "amount": 240.0,
                "received": 0,
                "object": "6100",
                "subsidiary": "",
                "business_unit": "CC100",
                "next_status": "400",
            },
        ],
        "vouchers": [
            {
                "key": "00001-PV-90001-001",
                "document": "90001",
                "doc_type": "PV",
                "company": "00001",
                "an8": "4001",
                "supplier_name": "Acme Office Supplies Ltd.",
                "invoice_number": "INV-7001",
                "invoice_date": "2026-09-02",
                "gross": 113.0,
                "open_amount": 0.0,
                "pay_status": "P",
                "batch": "88001",
            },
        ],
    }


KEYS = {"suppliers": "an8", "gl_accounts": "account_id", "business_units": "business_unit",
        "purchase_orders": "key", "vouchers": "key"}  # fmt: skip


class FakeAis:
    def __init__(self, *, https: bool = False, token_seconds: float = 3600, basic: bool = True,
                 denied: Iterable[str] = (), slow_seconds: float = 3.0) -> None:  # fmt: skip
        self.mapping = jde_ais.load_mapping()
        self.names = {spec["name"]: key for key, spec in self.mapping["orchestrations"].items()}
        self.data = sample_data()
        self.https, self.token_seconds, self.basic = https, token_seconds, basic
        self.denied = set(denied)  # orchestration keys the user's role does not include
        self.slow_seconds = slow_seconds
        self.tokens: dict[str, float] = {}  # token -> when it expires
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.scripts: dict[str, list[Any]] = {}
        self.by_key: dict[str, dict[str, Any]] = {}  # idempotency key -> the voucher made
        self.next_doc = 10001
        self.lock = threading.Lock()
        self.server: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None

    # -- running
    def __enter__(self) -> FakeAis:
        self.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.stop()

    def start(self) -> str:
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                pass

            def do_GET(self) -> None:  # noqa: N802
                fake._handle(self, "GET")

            def do_POST(self) -> None:  # noqa: N802
                fake._handle(self, "POST")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        if self.https:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(CERT_FILE, KEY_FILE)
            self.server.socket = ctx.wrap_socket(self.server.socket, server_side=True)
        self.thread = threading.Thread(target=self.server.serve_forever, args=(0.02,), daemon=True)
        self.thread.start()
        return self.url

    @property
    def url(self) -> str:
        assert self.server is not None
        return f"{'https' if self.https else 'http'}://127.0.0.1:{self.server.server_address[1]}"

    def stop(self) -> None:
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
            self.server = None

    def script(self, name: str, *steps: Any) -> None:
        """What the next calls of the orchestration ``name`` (its key, e.g. "voucher_create", or "token") do."""
        self.scripts.setdefault(name, []).extend(steps)

    def expire_tokens(self) -> None:
        with self.lock:
            self.tokens = {t: 0.0 for t in self.tokens}

    def settings(self, **changes: Any) -> jde_ais.AisSettings:
        cfg = jde_ais.AisSettings(base_url=self.url, environment=ENVIRONMENT, username=USER, step1=True, step2=True,
                                  timeout=2.0, ca_file=str(CA_FILE) if self.https else "")  # fmt: skip
        for k, v in changes.items():
            setattr(cfg, k, v)
        cfg.password = changes.get("password", PASSWORD)
        return cfg

    def vouchers(self) -> list[dict[str, Any]]:
        return self.data["vouchers"]

    def orchestration_calls(self, key: str) -> list[dict[str, Any]]:
        name = self.mapping["orchestrations"][key]["name"]
        return [inputs for path, inputs in self.calls if path.endswith("/" + name)]

    # -- answering
    def _send(self, h: BaseHTTPRequestHandler, status: int, body: Any) -> None:
        data = json.dumps(body).encode()
        h.send_response(status)
        h.send_header("Content-Type", "application/json")
        h.send_header("Content-Length", str(len(data)))
        h.end_headers()
        h.wfile.write(data)

    def _step(self, key: str) -> Any:
        with self.lock:
            steps = self.scripts.get(key) or []
            return steps.pop(0) if steps else None

    def _handle(self, h: BaseHTTPRequestHandler, method: str) -> None:
        length = int(h.headers.get("Content-Length") or 0)
        raw = h.rfile.read(length) if length else b""
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            return self._send(h, 400, {"message": "Bad request", "status": "ERROR"})
        ais = self.mapping["ais"]
        path = h.path
        if method == "GET" and path == ais["defaultconfig_path"]:
            self.calls.append((path, {}))
            return self._send(h, 200, {"aisVersion": "9.2.8.3", "jasHost": "jas.example.local"})
        if path == ais["token_path"]:
            self.calls.append((path, {k: v for k, v in body.items() if k != "password"}))
            if self._scripted(h, "token"):
                return None
            if (body.get("username") != USER or body.get("password") != PASSWORD
                    or body.get("environment") != ENVIRONMENT):  # fmt: skip
                return self._send(h, 403, {"message": "Authorization Failure: invalid user name, password or "
                                           "environment", "exception": "Exception", "status": "ERROR"})  # fmt: skip
            token = "044" + secrets.token_hex(24)
            with self.lock:
                self.tokens[token] = time.time() + self.token_seconds
            return self._send(h, 200, {"username": USER, "environment": ENVIRONMENT, "role": body.get("role"),
                                       "userInfo": {"token": token, "langPref": "  ", "locale": "en"},
                                       "userAuthorized": False})  # fmt: skip
        if path == ais["logout_path"]:
            self.calls.append((path, {}))
            with self.lock:
                self.tokens.pop(str(body.get("token") or ""), None)
            return self._send(h, 200, {})
        prefix = ais["orchestrator_path"].split("{name}")[0]
        if method == "POST" and path.startswith(prefix):
            name = path[len(prefix) :]
            self.calls.append((path, body))
            if not self._authorised(h):
                return self._send(h, 444, {"message": "Invalid token", "exception": "Exception", "status": "ERROR"})
            key = self.names.get(name)
            if key is None:
                return self._send(h, 404, {"message": f"Orchestration {name} not found", "status": "ERROR"})
            if key in self.denied:
                return self._send(h, 403, {"message": f"User {USER} is not authorized to run {name}",
                                           "status": "ERROR"})  # fmt: skip
            step = self._step(key)
            if self._play(h, step):
                return None
            status, answer = self._run(key, body)
            if step == "slow-after":
                time.sleep(self.slow_seconds)
            return self._send(h, status, answer)
        return self._send(h, 404, {"message": "Not found", "status": "ERROR"})

    def _scripted(self, h: BaseHTTPRequestHandler, key: str) -> bool:
        return self._play(h, self._step(key))

    def _play(self, h: BaseHTTPRequestHandler, step: Any) -> bool:
        if step is None or step == "slow-after":
            return False
        if step == "slow":
            time.sleep(self.slow_seconds)
            self._send(h, 200, {})
            return True
        if step == "drop":
            h.close_connection = True
            h.connection.close()
            return True
        self._send(h, int(step), {"message": f"HTTP {step}", "status": "ERROR"})
        return True

    def _authorised(self, h: BaseHTTPRequestHandler) -> bool:
        token = h.headers.get(self.mapping["ais"]["token_header"])
        if token:
            with self.lock:
                return self.tokens.get(token, 0) > time.time()
        auth = h.headers.get("Authorization") or ""
        if self.basic and auth.startswith("Basic "):
            user, _, password = base64.b64decode(auth[6:]).decode().partition(":")
            return user == USER and password == PASSWORD
        return False

    def _out(self, key: str, row: dict[str, Any]) -> dict[str, Any]:
        fields = self.mapping["orchestrations"][key]["fields"]
        return {fields[k]: v for k, v in row.items() if k in fields}

    def _inputs(self, key: str, body: dict[str, Any]) -> dict[str, Any]:
        names = self.mapping["orchestrations"][key].get("inputs") or {}
        return {ours: body.get(theirs) for ours, theirs in names.items()}

    def _run(self, key: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        spec = self.mapping["orchestrations"][key]
        given = self._inputs(key, body)
        if key in KEYS:
            rows = sorted(self.data[key], key=lambda r: str(r[KEYS[key]]))
            if key == "vouchers":
                if given.get("supplier"):
                    rows = [r for r in rows if r["an8"] == str(given["supplier"])]
                if given.get("invoice"):
                    rows = [r for r in rows if r["invoice_number"] == str(given["invoice"])]
            if given.get("after"):
                rows = [r for r in rows if str(r[KEYS[key]]) > str(given["after"])]
            size = int(given.get("max_rows") or 100)
            out = {spec["rows"]: [self._out(key, r) for r in rows[:size]]}
            return 200, {**out, "jde__status": "SUCCESS", "jde__serverExecutionSeconds": 0.01}
        if key == "voucher_status":
            found = self.by_key.get(str(given.get("key") or ""))
            if not found:
                return 200, self._out(key, {"found": "0"})
            return 200, self._out(key, {"found": "1", **found})
        if key == "voucher_create":
            return self._voucher(spec, given)
        return 404, {"message": "unknown", "status": "ERROR"}

    def _voucher(self, spec: dict[str, Any], given: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        results, modes = spec["results"], spec["modes"]
        key = str(given.get("key") or "")
        items, lines = given.get("pay_items") or [], given.get("gl_lines") or []
        with self.lock:
            if key in self.by_key:
                made = self.by_key[key]
                return 200, self._out("voucher_create", {"result": results["exists"], **made})
        if not key or not items:
            return 500, jde_error("Voucher_Entry", ("0001", "Error: No idempotency key or pay items"))
        head = items[0]
        an8 = str(head.get("VLAN8") or "")
        if an8 not in {s["an8"] for s in self.data["suppliers"]}:
            return 500, jde_error("Voucher_Entry", ("0002", f"Error: Address Number {an8 or 'blank'} Invalid"))
        accounts = {f"{a['business_unit']}.{a['object']}" + (f".{a['subsidiary']}" if a["subsidiary"] else "")
                    for a in self.data["gl_accounts"] if a["posting_edit"] == ""}  # fmt: skip
        for line in lines:
            if line.get("VNANI") and line["VNANI"] not in accounts:
                return 500, jde_error("Voucher_Entry", ("0003", f"Error: Account {line['VNANI']} Invalid"))
        number = str(head.get("VLVINV") or "")
        if any(v["an8"] == an8 and v["invoice_number"] == number for v in self.data["vouchers"]):
            return 500, jde_error("Voucher_Entry", ("0004", f"Error: Duplicate Invoice Number {number}"))
        if str(given.get("mode")) == modes["proof"]:
            return 200, self._out("voucher_create", {"result": results["valid"], "messages": []})
        with self.lock:
            doc = str(self.next_doc)
            self.next_doc += 1
            made = {"document": doc, "doc_type": head.get("VLDCT") or "PV", "company": head.get("VLCO") or "00001",
                    "batch": str(head.get("VLEDBT") or "")}  # fmt: skip
            gross = sum(float(i.get("VLAG") or i.get("VLACR") or 0) for i in items) / 100
            # Who the voucher is for, as the orchestration answers EXISTS and the status lookup (D-105).
            made.update({"an8": an8, "invoice_number": number, "gross": gross})
            if given.get("fingerprint"):
                made["fingerprint"] = given["fingerprint"]
            self.by_key[key] = made
            self.data["vouchers"].append({
                "key": f"{made['company']}-{made['doc_type']}-{doc}-001",
                **{k: made[k] for k in ("document", "doc_type", "company", "batch")}, "an8": an8,
                "supplier_name": next(s["name"] for s in self.data["suppliers"] if s["an8"] == an8),
                "invoice_number": number, "invoice_date": "", "gross": gross, "open_amount": gross, "pay_status": "A",
            })  # fmt: skip
        return 200, self._out(
            "voucher_create",
            {
                "result": results["created"],
                **{k: made[k] for k in ("document", "doc_type", "company", "batch")},
                "messages": [],
            },
        )
