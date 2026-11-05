"""One stand-in model server for the benches, the tests and CI: LM Studio as AP Coder sees it, with the delays, the
GPU slots and the failures of a real model machine (``python -m ap_coder.bench fake-model``).

It answers every route AP Coder calls: OpenAI's ``/v1/models``, ``/v1/chat/completions`` (whole or streamed) and
``/v1/embeddings``, and LM Studio's own ``/api/v0/models`` (0.3), ``/api/v1/models`` (0.4) and
``/api/v1/models/load`` / ``unload``. A chat request is answered as its kind asks (``kind_of``): a page shown to
OvisOCR2 gets a transcription (``page_texts``, by the SHA-256 of the image, else ``page_text``); the GL question one
account a line from its schema; the header question the fields of its schema (``header``); the AI reviewer's
question agreement, disagreement or doubt (``reviewer``).

How the model machine behaves, given when it starts or changed while it runs (``set``, or ``POST /__fake/set`` with
the same names in JSON):

- ``latency``: the seconds an answer takes, (lo, hi), uniform (it sets both of these); ``page_latency`` and
  ``chat_latency`` for each kind;
- ``slots``: the requests answered at once (LM Studio's concurrent predictions, one GPU for every model); the rest
  wait in line, first come first served;
- ``fail_rate``: the share answered 500; ``hang_rate``: the share that never answer (they keep their slot until
  ``release_hung``, the client gives up, or the server goes down); both taken as a request gets its slot;
- ``down``: every request is dropped unanswered, the connection closed (a killed server); ``down_after``: down once
  that many answers were given (counted once);
- ``models``: what LM Studio lists (ids, or dicts as ``model_spec`` takes); changed while it runs, a swapped model;
- ``time_scale``: every wait multiplied by it, so a day's bench runs in minutes.

Listing and loading take no slot (LM Studio lists its models in milliseconds while it reads a page). ``stats()``
(``GET /__fake/stats``): the requests by kind, served, failed, hung and dropped, most at once, the waits for a slot.
Standard library only: CI runs it with a bare Python (``scripts/ci/fake_lm_studio.py``)."""

from __future__ import annotations

import argparse
import base64
import contextlib
import hashlib
import json
import random
import re
import select
import socket
import statistics
import sys
import threading
import time
from collections import Counter
from collections.abc import Callable, Iterable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

CHAT = "qwen/qwen3.5-9b"  # LM Studio's key for Qwen 3.5 9B (GGUF, Q4_K_M)
READER = "ath-maas_ovisocr2"  # bartowski's OvisOCR2 build, as LM Studio names it
EMBED = "text-embedding-qwen3-embedding-0.6b"  # LM Studio's key for Qwen3 Embedding 0.6B
MODELS = (CHAT, READER, EMBED)
INFERENCE = ("page", "suggest", "header", "review", "chat", "embeddings")  # the kinds that take a slot
REVIEWERS = ("agree", "disagree", "unsure")
# What OvisOCR2 writes for a page it was given no text for: words, but no figure or name another reader would differ
# on (a page that reads as blank would be left out, ``capture.transcript.made_up``).
PAGE_TEXT = "Page 1\n\nThank you for your business.\nPlease keep this copy for your records."
CONTROL = "/__fake/"
_SETTABLE = ("latency", "page_latency", "chat_latency", "load_latency", "slots", "fail_rate", "hang_rate",
             "down_after", "down", "models", "reviewer", "header", "page_text", "page_texts", "time_scale")  # fmt: skip


def seconds_range(value: Any) -> tuple[float, float]:
    """A latency as (lo, hi) seconds from "8-20", "10", 10, [8, 20] or (8, 20)."""
    if isinstance(value, str):
        parts = [p for p in re.split(r"\s*-\s*|\s*,\s*", value.strip()) if p]
        value = [float(p) for p in parts] or [0.0]
    if isinstance(value, (int, float)):
        value = [value]
    lo, hi = (float(value[0]), float(value[-1])) if len(value) else (0.0, 0.0)
    if lo < 0 or hi < lo:
        raise ValueError(f"a latency is lo-hi seconds, 0 <= lo <= hi: {value}")
    return lo, hi


def model_spec(model: str | dict[str, Any]) -> dict[str, Any]:
    """A model as LM Studio lists it: ``id``; ``type`` (vlm, llm or embeddings: by its name when not given); ``ctx``,
    the context it is loaded with (0: downloaded, not loaded); ``max_context``; ``vision``; ``reasoning`` (it takes
    the thinking switch); ``publisher``, ``arch``, ``quant``."""
    found = {"id": model} if isinstance(model, str) else dict(model)
    key = str(found["id"])
    squashed = re.sub(r"[^a-z0-9]", "", key.lower())
    embed, reader = "embed" in squashed, "ovisocr" in squashed
    kind = str(found.get("type") or ("embeddings" if embed else "vlm"))
    defaults = {"type": kind, "ctx": 32768 if embed else 20480 if reader else 8192, "max_context": 262144,
                "vision": kind == "vlm", "reasoning": kind != "embeddings",
                "publisher": key.split("/", 1)[0] if "/" in key else "lmstudio-community",
                "arch": "qwen3" if embed else "qwen3_5", "quant": "Q8_0" if embed or reader else "Q4_K_M"}  # fmt: skip
    return {**defaults, **found, "id": key}


def document_reader(model: str) -> bool:
    return "ovisocr" in re.sub(r"[^a-z0-9]", "", model.lower())


def _summary(values: Iterable[float]) -> dict[str, float | int | None]:
    ordered = sorted(values)
    if not ordered:
        return {"n": 0, "mean": None, "p50": None, "p95": None, "max": None}
    pick = lambda q: ordered[min(len(ordered) - 1, int(q * len(ordered)))]  # noqa: E731
    return {"n": len(ordered), "mean": round(statistics.fmean(ordered), 3), "p50": round(pick(0.5), 3),
            "p95": round(pick(0.95), 3), "max": round(ordered[-1], 3)}  # fmt: skip


# --- What a request is, and what it is answered ------------------------------------------------------------------


def _enum(node: Any, key: str) -> list[Any]:
    """The enum of the first ``key`` property anywhere in a JSON schema ([] when none)."""
    if isinstance(node, dict):
        found = node.get("properties", {}).get(key) if isinstance(node.get("properties"), dict) else None
        if isinstance(found, dict) and found.get("enum"):
            return list(found["enum"])
        return next((e for e in (_enum(v, key) for v in node.values()) if e), [])
    if isinstance(node, list):
        return next((e for e in (_enum(v, key) for v in node) if e), [])
    return []


def _schema(body: dict[str, Any]) -> dict[str, Any]:
    found = ((body.get("response_format") or {}).get("json_schema") or {}).get("schema")
    return found if isinstance(found, dict) else {}


def _texts(body: dict[str, Any], role: str | None = None) -> list[str]:
    """The text of each message (of ``role``), its text parts joined."""
    out = []
    for message in body.get("messages") or []:
        if not isinstance(message, dict) or (role and message.get("role") != role):
            continue
        content = message.get("content")
        if isinstance(content, list):
            content = " ".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text")
        out.append(content if isinstance(content, str) else "")
    return out


def images_of(body: dict[str, Any]) -> list[str]:
    """The SHA-256 of each image a chat request shows (a data URL's bytes; any other URL's text)."""
    out = []
    for message in body.get("messages") or []:
        parts = message.get("content") if isinstance(message, dict) else None
        for part in parts if isinstance(parts, list) else []:
            if not isinstance(part, dict) or part.get("type") != "image_url":
                continue
            url = str((part.get("image_url") or {}).get("url") or "")
            data = url.encode()
            if url.startswith("data:") and "," in url:
                with contextlib.suppress(ValueError):
                    data = base64.b64decode(url.split(",", 1)[1])
            out.append(hashlib.sha256(data).hexdigest())
    return out


def page_key(image: bytes) -> str:
    """The key ``page_texts`` takes for a page image (a PNG as AP Coder sends it)."""
    return hashlib.sha256(image).hexdigest()


def kind_of(method: str, path: str, body: dict[str, Any]) -> str:
    """models, load, unload, embeddings, page (a page shown to the page reader), suggest (GL accounts for lines),
    header (the chat model reading the header off the pages), review (the AI reviewer), chat (anything else), other."""
    if method == "GET":
        return "models" if path in ("/v1/models", "/api/v0/models", "/api/v1/models") else "other"
    if path == "/api/v1/models/load":
        return "load"
    if path == "/api/v1/models/unload":
        return "unload"
    if path == "/v1/embeddings":
        return "embeddings"
    if path != "/v1/chat/completions":
        return "other"
    system = " ".join(_texts(body, "system"))
    parts = [p for m in body.get("messages") or [] if isinstance(m, dict) and isinstance(m.get("content"), list)
             for p in m["content"]]  # fmt: skip
    images = any(isinstance(p, dict) and p.get("type") == "image_url" for p in parts)
    if "confident" in (_schema(body).get("properties") or {}) or "accounts-payable reviewer" in system:
        return "review"
    if _enum(body.get("response_format"), "gl_code") or system.startswith("You code accounts-payable invoice lines"):
        return "suggest"
    if images and (document_reader(str(body.get("model", ""))) or not system):
        return "page"
    return "header" if images and system.startswith("You read the header of") else "chat"


def account_lines(body: dict[str, Any]) -> list[dict[str, Any]]:
    """A GL question (its schema lists the chart's accounts) answered as a model would: the first account for each
    numbered line of the question ("3. Shipping cartons | 12.50"). No lines when the schema lists no account."""
    codes = _enum(body.get("response_format"), "gl_code")
    texts = _texts(body, "user")
    numbers = re.findall(r"^(\d+)\. ", texts[-1] if texts else "", re.M)
    if not codes:
        return []
    return [{"line_number": int(n), "gl_code": codes[0], "cost_center": "", "reason": "fake"} for n in numbers]


def reviewer_answer(body: dict[str, Any], mode: str) -> dict[str, Any]:
    """The AI reviewer's question answered as ``mode`` says: "agree" with everything, "disagree" with the total (it
    reads 9,999.00, every time) or "unsure" (never confident). The fields and lines are those asked about."""
    asked = (_texts(body, "user") or [""])[-1].split("For reference")[0]
    fields = _enum(body.get("response_format"), "field")
    if not fields and "What was entered, to check:" in asked:
        with contextlib.suppress(ValueError):
            fields = list(json.loads(asked.split("What was entered, to check:", 1)[1]).get("fields") or {})
    numbers = sorted({int(n) for n in re.findall(r'"line_number": (\d+)', asked)})
    wrong = mode == "disagree"
    return {"fields": [{"field": f, "agree": not (wrong and f == "grand_total"),
                        "on_page": "9,999.00" if wrong and f == "grand_total" else None, "reason": "fake"}
                       for f in fields],
            "lines": [{"line_number": n, "agree": True, "reason": ""} for n in numbers],
            "confident": mode != "unsure", "summary": f"fake reviewer: {mode}"}  # fmt: skip


# --- The server -------------------------------------------------------------------------------------------------


class _Dropped(Exception):
    """The request is dropped unanswered (the server went down, or stopped)."""


class FakeModel:
    """A fake LM Studio on ``host:port`` (port 0: a free one), started by ``start`` (or ``with``), stopped by
    ``stop``. ``url`` is its root, ``base_url`` the ``/v1`` address AP Coder is given (AP_LLM_BASE_URL).

    For tests: ``api`` (v0, v1, both or none: LM Studio's own model lists answered), ``routes`` (GET paths answered
    with these payloads as they are), ``on_request(method, path, body)`` (an answer of its own, (status, payload),
    or None for the usual one; asked after the wait), ``record`` (every request kept in ``requests``) and ``log`` (a
    JSON line a request, as CI's check reads it). A subclass may answer differently: ``answer_page``,
    ``answer_suggest``, ``answer_header``, ``answer_review``, ``answer_chat``."""

    def __init__(self, host: str = "127.0.0.1", port: int = 0, *, latency: Any = (0.0, 0.0),
                 page_latency: Any = None, chat_latency: Any = None, load_latency: Any = (0.0, 0.0), slots: int = 1,
                 fail_rate: float = 0.0, hang_rate: float = 0.0, down_after: int | None = None,
                 models: Iterable[str | dict[str, Any]] = MODELS, seed: int = 0, time_scale: float = 1.0,
                 api: str = "both", reviewer: str = "agree", page_text: str = PAGE_TEXT,
                 page_texts: dict[str, str] | None = None, header: dict[str, Any] | None = None,
                 routes: dict[str, Any] | None = None,
                 on_request: Callable[[str, str, dict[str, Any]], tuple[int, Any] | None] | None = None,
                 record: bool = False, log: str | Path | None = None) -> None:  # fmt: skip
        if api not in ("v0", "v1", "both", "none"):
            raise ValueError(f"api is v0, v1, both or none: {api}")
        self.host, self._port = host, port
        self.api, self.routes, self.on_request = api, dict(routes or {}), on_request
        self.record, self.log = record, Path(log) if log else None
        self.requests: list[dict[str, Any]] = []
        self._rng = random.Random(seed)
        self._cond = threading.Condition()
        self._log_lock = threading.Lock()
        self._state: dict[str, Any] = {}
        self._specs: list[dict[str, Any]] = []
        self._instances: dict[str, list[tuple[str, int]]] = {}
        self._loads = 0
        self._page_texts: dict[str, str] = {}
        self.set(latency=latency, page_latency=latency if page_latency is None else page_latency,
                 chat_latency=latency if chat_latency is None else chat_latency, load_latency=load_latency,
                 slots=slots, fail_rate=fail_rate, hang_rate=hang_rate, down_after=down_after, down=False,
                 models=list(models), reviewer=reviewer, header=dict(header or {}), page_text=page_text,
                 page_texts=dict(page_texts or {}), time_scale=time_scale)  # fmt: skip
        self._stopping = False
        self._release = 0  # bumped by release_hung: the requests hung before it end
        self._line: list[int] = []  # the tickets waiting for a slot, in order
        self._ticket = 0
        self._active = self._in_flight = self._max_active = self._max_in_flight = 0
        self._counts: Counter[str] = Counter()
        self._served: Counter[str] = Counter()
        self._by_model: Counter[str] = Counter()
        self._outcomes: Counter[str] = Counter()
        self._pages: Counter[str] = Counter()  # page image → times answered
        self._waits: dict[str, list[float]] = {}
        self._answer_seconds: list[float] = []
        self._hung_seconds: list[float] = []
        self._hung_gave_up = 0
        self._history: list[dict[str, Any]] = []
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._started = time.monotonic()

    # --- Starting and stopping

    @property
    def port(self) -> int:
        return self._server.server_address[1] if self._server else self._port

    @property
    def url(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"http://{host}:{self.port}"

    @property
    def base_url(self) -> str:
        return self.url + "/v1"

    def start(self) -> FakeModel:
        if self._server is not None:
            return self
        fake, family = self, socket.AF_INET6 if ":" in self.host else socket.AF_INET

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                pass

            def do_GET(self) -> None:  # noqa: N802
                fake._handle(self, "GET")

            def do_POST(self) -> None:  # noqa: N802
                fake._handle(self, "POST")

        class Server(ThreadingHTTPServer):
            daemon_threads = True
            address_family = family
            request_queue_size = 128  # a morning's burst of connections waits to be taken, not refused

        self._server = Server((self.host, self._port), Handler)
        self._started = time.monotonic()
        self._thread = threading.Thread(target=self._server.serve_forever, kwargs={"poll_interval": 0.05},
                                        name="fake-model", daemon=True)  # fmt: skip
        self._thread.start()
        return self

    def stop(self) -> None:
        with self._cond:
            self._stopping = True
            self._cond.notify_all()
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(5)
            self._thread = None

    def __enter__(self) -> FakeModel:
        return self.start()

    def __exit__(self, *exc: Any) -> None:
        self.stop()

    def serve_forever(self) -> None:
        """Start, and answer until Ctrl+C."""
        self.start()
        try:
            while self._server is not None:
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()

    # --- Changing how it behaves

    def set(self, **changes: Any) -> dict[str, Any]:
        """Change how it behaves (the names in the module's text); the settings after the change."""
        unknown = sorted(set(changes) - set(_SETTABLE))
        if unknown:
            raise ValueError(f"not a setting of the fake model server: {', '.join(unknown)}")
        clean: dict[str, Any] = {}
        for name, value in changes.items():
            if name.endswith("latency"):
                clean[name] = seconds_range(value)
            elif name == "slots":
                clean[name] = max(1, int(value))
            elif name in ("fail_rate", "hang_rate"):
                clean[name] = min(1.0, max(0.0, float(value)))
            elif name == "down_after":
                clean[name] = None if value is None else max(0, int(value))
            elif name == "time_scale":
                clean[name] = max(0.0, float(value))
            elif name == "reviewer":
                if value not in REVIEWERS:
                    raise ValueError(f"reviewer is one of {', '.join(REVIEWERS)}: {value}")
                clean[name] = value
            elif name == "models":
                clean[name] = [model_spec(m) for m in value]
            elif name == "page_texts":
                clean[name] = {str(k): str(v) for k, v in dict(value).items()}
            elif name == "down":
                clean[name] = bool(value)
            else:
                clean[name] = value
        if "latency" in clean:  # both kinds' answers, unless named apart in the same change
            clean.setdefault("page_latency", clean["latency"])
            clean.setdefault("chat_latency", clean["latency"])
        with self._cond:
            if "models" in clean:
                self._specs = clean.pop("models")
                self._instances = {m["id"]: [(m["id"], int(m["ctx"]))] if m["ctx"] else [] for m in self._specs}
            if "page_texts" in clean:
                self._page_texts.update(clean.pop("page_texts"))
            self._state.update(clean)
            self._cond.notify_all()
            return self.settings()

    def settings(self) -> dict[str, Any]:
        with self._cond:
            out = {k: v for k, v in self._state.items() if k not in ("header",)}
            out["models"] = [m["id"] for m in self._specs]
            out["page_texts"] = len(self._page_texts)
            return out

    def release_hung(self) -> int:
        """End every request hung now (unanswered, the connection closed): LM Studio restarted. How many."""
        with self._cond:
            self._release += 1
            hung = sum(1 for h in self._history if h["outcome"] == "hanging")
            self._cond.notify_all()
            return hung

    def learn_pages(self, images: Iterable[bytes], texts: Iterable[str]) -> None:
        """What OvisOCR2 writes for these page images (PNGs as AP Coder renders them)."""
        self.set(page_texts={page_key(image): text for image, text in zip(images, texts, strict=True)})

    # --- What it saw

    def stats(self) -> dict[str, Any]:
        with self._cond:
            return {
                "url": self.url, "uptime": round(time.monotonic() - self._started, 3),
                "time_scale": self._state["time_scale"], "slots": self._state["slots"],
                "models": [m["id"] for m in self._specs], "down": self._state["down"],
                "requests": sum(self._counts.values()), "by_kind": dict(self._counts),
                "served": sum(self._served.values()), "served_by_kind": dict(self._served),
                "served_by_model": dict(self._by_model), "failed": self._outcomes["failed"],
                "hung": self._outcomes["hung"] + self._outcomes["hanging"], "hanging": self._outcomes["hanging"],
                "hung_gave_up": self._hung_gave_up, "dropped": self._outcomes["dropped"],
                "active": self._active, "waiting": len(self._line), "max_active": self._max_active,
                "max_in_flight": self._max_in_flight,
                "queue_wait": _summary(w for waits in self._waits.values() for w in waits),
                "queue_wait_by_kind": {k: _summary(v) for k, v in self._waits.items()},
                "answer_seconds": _summary(self._answer_seconds), "hung_seconds": _summary(self._hung_seconds),
                "pages_answered": len(self._pages),
                "pages_answered_again": sum(1 for n in self._pages.values() if n > 1),
            }  # fmt: skip

    def history(self) -> list[dict[str, Any]]:
        """Every request that takes a slot, and every one dropped while the server was down: t (seconds since it
        started), kind, model, image (the first one's key), wait (for a slot), seconds (in all), outcome (served,
        failed, hung, hanging, dropped)."""
        with self._cond:
            return [dict(h) for h in self._history]

    def page_answers(self) -> dict[str, int]:
        """How many times each page image (``page_key``) was answered."""
        with self._cond:
            return dict(self._pages)

    # --- Answering

    def _handle(self, h: BaseHTTPRequestHandler, method: str) -> None:
        path = h.path.split("?", 1)[0]
        body: dict[str, Any] = {}
        if method == "POST":
            try:
                raw = h.rfile.read(int(h.headers.get("Content-Length") or 0))
                found = json.loads(raw or b"{}")
                body = found if isinstance(found, dict) else {}
            except (ValueError, OSError):
                body = {}
        if path.startswith(CONTROL):
            self._control(h, method, path, body)
            return
        kind = kind_of(method, path, body)
        images = images_of(body) if path == "/v1/chat/completions" else []
        self._note(method, path, kind, body, images)
        try:
            with self._cond:
                if self._state["down"]:  # a killed server: nothing is answered, not even the model list
                    self._outcomes["dropped"] += 1
                    self._history.append({"t": round(time.monotonic() - self._started, 3), "kind": kind,
                                          "model": str(body.get("model") or ""), "image": images[0] if images
                                          else "", "wait": 0.0, "seconds": 0.0, "outcome": "dropped"})  # fmt: skip
                    raise _Dropped
            if kind in INFERENCE:
                self._infer(h, method, path, kind, body, images)
                return
            if kind == "load":
                self._pause(self._latency("load"))
            status, payload = self._answer_other(method, path, body)
            self._send(h, status, payload)
        except _Dropped:
            self._drop(h)

    def _note(self, method: str, path: str, kind: str, body: dict[str, Any], images: list[str]) -> None:
        model = str(body.get("model") or "")
        with self._cond:
            self._counts[kind] += 1
            if self.record:
                self.requests.append({"method": method, "path": path, "kind": kind, "model": model,
                                      "images": len(images), "body": body})  # fmt: skip
        if self.log is not None:
            entry = {"t": round(time.time(), 3), "method": method, "path": path, "kind": kind, "model": model,
                     "images": len(images)}  # fmt: skip
            with self._log_lock, self.log.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")

    def _latency(self, kind: str) -> float:
        name = {"page": "page_latency", "load": "load_latency"}.get(kind, "chat_latency")
        with self._cond:
            lo, hi = self._state[name]
            return self._rng.uniform(lo, hi) * self._state["time_scale"]

    def _pause(self, seconds: float) -> None:
        """Wait ``seconds``; _Dropped when the server goes down or stops meanwhile."""
        deadline = time.monotonic() + seconds
        with self._cond:
            while True:
                if self._state["down"] or self._stopping:
                    raise _Dropped
                left = deadline - time.monotonic()
                if left <= 0:
                    return
                self._cond.wait(left)

    def _infer(self, h: BaseHTTPRequestHandler, method: str, path: str, kind: str, body: dict[str, Any],
               images: list[str]) -> None:  # fmt: skip
        arrived = time.monotonic()
        entry = {"t": round(arrived - self._started, 3), "kind": kind, "model": str(body.get("model", "")),
                 "image": images[0] if images else "", "wait": 0.0, "seconds": 0.0, "outcome": "dropped"}  # fmt: skip
        with self._cond:
            self._history.append(entry)
            self._in_flight += 1
            self._max_in_flight = max(self._max_in_flight, self._in_flight)
            self._ticket += 1
            ticket = self._ticket
            self._line.append(ticket)
            try:
                while self._line[0] != ticket or self._active >= self._state["slots"]:
                    if self._state["down"] or self._stopping:
                        raise _Dropped
                    self._cond.wait(0.5)
            except _Dropped:
                self._line.remove(ticket)
                self._in_flight -= 1
                self._outcomes["dropped"] += 1
                self._cond.notify_all()
                raise
            self._line.pop(0)
            # Decided as it takes its slot, from the rates now: one in line when a hang drill ends is answered.
            hang = self._rng.random() < self._state["hang_rate"]
            fail = self._rng.random() < self._state["fail_rate"]
            self._active += 1
            self._max_active = max(self._max_active, self._active)
            entry["wait"] = round(time.monotonic() - arrived, 3)
            self._waits.setdefault(kind, []).append(entry["wait"])
            release = self._release
            self._cond.notify_all()
        outcome, counted = "dropped", False
        try:
            if hang:
                outcome = self._hang(h, entry, release)
                return
            self._pause(self._latency(kind))
            answer = self.on_request(method, path, body) if self.on_request else None
            if answer is None and fail:
                answer = 500, {"error": "The model stopped while it was answering (a fake failure)."}
            if answer is None:
                answer = self._answer_chat(kind, body, images)
            status, payload = answer
            if self._state["down"] or self._stopping:
                raise _Dropped
            outcome = "served" if status < 400 else "failed"
            self._finish(entry, kind, images, arrived, outcome)  # counted before it is sent: a caller sees it
            counted = True
            if status == 200 and body.get("stream") and isinstance(payload, dict) and payload.get("choices"):
                self._stream(h, payload)
            else:
                self._send(h, status, payload)
        except _Dropped:
            self._drop(h)
        finally:
            if not counted:
                self._finish(entry, kind, images, arrived, outcome)

    def _finish(self, entry: dict[str, Any], kind: str, images: list[str], arrived: float, outcome: str) -> None:
        """A request's end counted, its slot freed; down when ``down_after`` answers were given."""
        with self._cond:
            self._active -= 1
            self._in_flight -= 1
            entry["seconds"] = round(time.monotonic() - arrived, 3)
            entry["outcome"] = outcome if entry["outcome"] != "hanging" else "hung"
            self._outcomes[entry["outcome"]] += 1
            if outcome in ("served", "failed"):
                self._answer_seconds.append(entry["seconds"])
            if outcome == "served":
                self._served[kind] += 1
                self._by_model[entry["model"]] += 1
                if kind == "page" and images:
                    self._pages[images[0]] += 1
                after = self._state["down_after"]
                if after is not None and sum(self._served.values()) >= after:
                    self._state["down"], self._state["down_after"] = True, None
            self._cond.notify_all()

    def _hang(self, h: BaseHTTPRequestHandler, entry: dict[str, Any], release: int) -> str:
        """Never answer: until release_hung, the server going down or stopping, or the client giving up."""
        started = time.monotonic()
        with self._cond:
            entry["outcome"] = "hanging"
            self._outcomes["hanging"] += 1
        gave_up = False
        try:
            while True:
                with self._cond:
                    if self._release != release or self._state["down"] or self._stopping:
                        break
                readable, _, _ = select.select([h.connection], [], [], 0.25)
                if readable:
                    try:
                        gave_up = not h.connection.recv(1, socket.MSG_PEEK)
                    except OSError:
                        gave_up = True
                    if gave_up:
                        break
        finally:
            with self._cond:
                self._outcomes["hanging"] -= 1
                self._hung_seconds.append(round(time.monotonic() - started, 3))
                self._hung_gave_up += int(gave_up)
                entry["gave_up"] = gave_up
        self._drop(h)
        return "hung"

    def _answer_chat(self, kind: str, body: dict[str, Any], images: list[str]) -> tuple[int, Any]:
        model = str(body.get("model", ""))
        if kind == "embeddings":
            return 200, {"object": "list", "model": model,
                         "data": [{"object": "embedding", "index": 0, "embedding": [0.0] * 8}]}  # fmt: skip
        with self._cond:
            spec = next((m for m in self._specs if model == m["id"] or any(
                model == i for i, _ in self._instances.get(m["id"], []))), None)  # fmt: skip
        if spec is None:
            return 404, {"error": f"Model '{model}' not found"}
        if spec["type"] == "embeddings":
            return 400, {"error": f"'{model}' is not a llm"}
        answer: Any = {
            "page": lambda: self.answer_page(body, images),
            "suggest": lambda: self.answer_suggest(body),
            "header": lambda: self.answer_header(body),
            "review": lambda: self.answer_review(body),
        }.get(kind, lambda: self.answer_chat(body))()
        text = answer if isinstance(answer, str) else json.dumps(answer)
        return 200, {"id": "chatcmpl-fake", "object": "chat.completion", "created": int(time.time()), "model": model,
                     "choices": [{"index": 0, "finish_reason": "stop",
                                  "message": {"role": "assistant", "content": text}}],
                     "usage": {"prompt_tokens": 1, "completion_tokens": max(1, len(text) // 4),
                               "total_tokens": 1 + max(1, len(text) // 4)}}  # fmt: skip

    def answer_page(self, body: dict[str, Any], images: list[str]) -> str:
        with self._cond:
            return self._page_texts.get(images[0] if images else "", self._state["page_text"])

    def answer_suggest(self, body: dict[str, Any]) -> Any:
        return {"lines": account_lines(body)}

    def answer_header(self, body: dict[str, Any]) -> Any:
        header = self._state["header"]
        return {k: header.get(k) for k in (_schema(body).get("properties") or header)}

    def answer_review(self, body: dict[str, Any]) -> Any:
        return reviewer_answer(body, self._state["reviewer"])

    def answer_chat(self, body: dict[str, Any]) -> Any:
        return {} if body.get("response_format") else "OK"

    def _answer_other(self, method: str, path: str, body: dict[str, Any]) -> tuple[int, Any]:
        answer = self.on_request(method, path, body) if self.on_request else None
        if answer is not None:
            return answer
        if method == "GET" and path in self.routes:
            return 200, self.routes[path]
        with self._cond:
            specs = [dict(m, instances=list(self._instances.get(m["id"], []))) for m in self._specs]
        if method == "GET" and path == "/v1/models":
            data = [{"id": m["id"], "object": "model", "owned_by": "organization_owner"} for m in specs]
            return 200, {"object": "list", "data": data}
        if method == "GET" and path == "/api/v0/models" and self.api in ("v0", "both"):
            return 200, {"object": "list", "data": [_v0(m) for m in specs]}
        if method == "GET" and path == "/api/v1/models" and self.api in ("v1", "both"):
            return 200, {"models": [_v1(m) for m in specs]}
        if method == "POST" and path == "/api/v1/models/load" and self.api in ("v1", "both"):
            return self._load(body)
        if method == "POST" and path == "/api/v1/models/unload" and self.api in ("v1", "both"):
            return self._unload(body)
        return 404, {"error": f"Unexpected endpoint or method. ({method} {path})"}

    def _load(self, body: dict[str, Any]) -> tuple[int, Any]:
        key = str(body.get("model", ""))
        with self._cond:
            if not any(m["id"] == key for m in self._specs):
                return 404, {"error": f"Model '{key}' not found"}
            self._loads += 1
            context = int(body.get("context_length") or 4096)
            instance = key if not self._instances[key] else f"{key}:{self._loads + 1}"
            self._instances[key].append((instance, context))
        return 200, {"type": "llm", "instance_id": instance, "status": "loaded",
                     "load_config": {"context_length": context}}  # fmt: skip

    def _unload(self, body: dict[str, Any]) -> tuple[int, Any]:
        instance = str(body.get("instance_id", ""))
        with self._cond:
            for key, instances in self._instances.items():
                if any(i == instance for i, _ in instances):
                    self._instances[key] = [(i, c) for i, c in instances if i != instance]
                    return 200, {"instance_id": instance}
        return 404, {"error": f"No loaded instance '{instance}'"}

    def _control(self, h: BaseHTTPRequestHandler, method: str, path: str, body: dict[str, Any]) -> None:
        try:
            if method == "GET" and path == CONTROL + "stats":
                self._send(h, 200, self.stats())
            elif method == "GET" and path == CONTROL + "history":
                self._send(h, 200, {"history": self.history()})
            elif method == "POST" and path == CONTROL + "set":
                released = self.release_hung() if body.pop("release_hung", False) else 0
                self._send(h, 200, {"ok": True, "released": released, "settings": self.set(**body)})
            elif method == "POST" and path == CONTROL + "release":
                self._send(h, 200, {"ok": True, "released": self.release_hung()})
            else:
                self._send(h, 404, {"error": f"No such control: {method} {path}"})
        except (ValueError, TypeError) as exc:
            self._send(h, 400, {"error": str(exc)})

    # --- The wire

    @staticmethod
    def _send(h: BaseHTTPRequestHandler, status: int, payload: Any) -> None:
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        with contextlib.suppress(OSError):
            h.send_response(status)
            h.send_header("Content-Type", "application/json")
            h.send_header("Content-Length", str(len(body)))
            h.end_headers()
            h.wfile.write(body)

    @staticmethod
    def _stream(h: BaseHTTPRequestHandler, payload: dict[str, Any]) -> None:
        """A chat completion as LM Studio streams it: a chunk a line, then the end."""
        text = str(payload["choices"][0]["message"]["content"])
        head = {"id": payload["id"], "object": "chat.completion.chunk", "created": payload["created"],
                "model": payload["model"]}  # fmt: skip
        deltas = [{"role": "assistant", "content": line + "\n"} if not n else {"content": line + "\n"}
                  for n, line in enumerate(text.split("\n"))]  # fmt: skip
        chunks = [{**head, "choices": [{"index": 0, "delta": d, "finish_reason": None}]} for d in deltas]
        chunks.append({**head, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
        with contextlib.suppress(OSError):
            h.send_response(200)
            h.send_header("Content-Type", "text/event-stream")
            h.end_headers()
            h.wfile.write("".join(f"data: {json.dumps(c)}\n\n" for c in chunks).encode() + b"data: [DONE]\n\n")

    @staticmethod
    def _drop(h: BaseHTTPRequestHandler) -> None:
        """No answer: the connection closed, as a killed or restarted server leaves it."""
        h.close_connection = True
        with contextlib.suppress(OSError):
            h.connection.shutdown(socket.SHUT_RDWR)


def _v0(m: dict[str, Any]) -> dict[str, Any]:
    """LM Studio 0.3's /api/v0/models entry."""
    loaded = m["instances"]
    found = {"id": m["id"], "object": "model", "type": m["type"], "publisher": m["publisher"], "arch": m["arch"],
             "compatibility_type": "gguf", "quantization": m["quant"], "state": "loaded" if loaded else "not-loaded",
             "max_context_length": m["max_context"]}  # fmt: skip
    if loaded:
        found["loaded_context_length"] = max(c for _, c in loaded)
    return found if m["type"] == "embeddings" else {**found, "capabilities": ["tool_use"]}


def _v1(m: dict[str, Any]) -> dict[str, Any]:
    """LM Studio 0.4's /api/v1/models entry."""
    found = {"type": "embedding" if m["type"] == "embeddings" else "llm", "publisher": m["publisher"],
             "key": m["id"], "display_name": m["id"], "architecture": m["arch"],
             "quantization": {"name": m["quant"], "bits_per_weight": 4}, "max_context_length": m["max_context"],
             "format": "gguf",
             "loaded_instances": [{"id": i, "config": {"context_length": c}} for i, c in m["instances"]]}  # fmt: skip
    if m["type"] != "embeddings":
        found["capabilities"] = {"vision": bool(m["vision"]), "trained_for_tool_use": True}
        if m["reasoning"]:
            found["capabilities"]["reasoning"] = {"allowed_options": ["off", "on"], "default": "on"}
    return found


# --- The command ------------------------------------------------------------------------------------------------


def add_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument("--host", default="127.0.0.1", help="the address it listens on (default 127.0.0.1)")
    p.add_argument("--port", type=int, default=1234, help="its port (default 1234, LM Studio's; 0: a free one)")
    p.add_argument("--latency", default="0", help="seconds an answer takes: 8-20 (uniform) or 10 (default 0)")
    p.add_argument("--page-latency", default=None, help="seconds a page takes OvisOCR2 (default --latency)")
    p.add_argument("--chat-latency", default=None, help="seconds a chat answer takes (default --latency)")
    p.add_argument("--slots", type=int, default=1, help="requests answered at once, the rest wait (default 1)")
    p.add_argument("--fail-rate", type=float, default=0.0, help="share of answers that are errors (500)")
    p.add_argument("--hang-rate", type=float, default=0.0, help="share of requests never answered")
    p.add_argument("--down-after", type=int, default=None, help="down (connections dropped) after this many answers")
    p.add_argument("--chat", default=CHAT, help=f"the chat model's key (default {CHAT})")
    p.add_argument("--reader", default=READER, help=f"the page reader's key (default {READER}; '' for none)")
    p.add_argument("--embed", default=EMBED, help="an embedding model listed beside them ('' for none)")
    p.add_argument(
        "--api",
        default="both",
        choices=["v0", "v1", "both", "none"],
        help="LM Studio's own model lists answered: 0.3's (v0), 0.4's (v1), both, or none (Ollama)",
    )
    p.add_argument("--reviewer", default="agree", choices=REVIEWERS, help="how the AI reviewer is answered")
    p.add_argument("--time-scale", type=float, default=1.0, help="every wait multiplied by this (default 1)")
    p.add_argument("--seed", type=int, default=0, help="random seed of the latencies and failures")
    p.add_argument("--log", type=Path, default=None, help="a JSON line for each request")


def from_arguments(a: argparse.Namespace, models: Iterable[str | dict[str, Any]] | None = None) -> FakeModel:
    return FakeModel(a.host, a.port, latency=a.latency, page_latency=a.page_latency, chat_latency=a.chat_latency,
                     slots=a.slots, fail_rate=a.fail_rate, hang_rate=a.hang_rate, down_after=a.down_after,
                     models=models or [m for m in (a.chat, a.reader, a.embed) if m], seed=a.seed,
                     time_scale=a.time_scale, api=a.api, reviewer=a.reviewer, log=a.log)  # fmt: skip


def run_command(a: argparse.Namespace) -> int:
    """``python -m ap_coder.bench fake-model``: serve until Ctrl+C, then print what it saw."""
    fake = from_arguments(a).start()
    s = fake.settings()
    print(f"Fake model server on {fake.base_url}: {', '.join(s['models'])}; latency {s['latency']} s (pages "
          f"{s['page_latency']}, chat {s['chat_latency']}), {s['slots']} slot(s), x{s['time_scale']} time. "
          f"Control: POST {fake.url}{CONTROL}set, GET {fake.url}{CONTROL}stats", flush=True)  # fmt: skip
    try:
        fake.serve_forever()
    finally:
        print(json.dumps(fake.stats(), indent=1), file=sys.stderr)
    return 0
