"""A stand-in for LM Studio with the pilot tester's three models, and a check of which one AP Coder uses for what.

    python scripts/ci/fake_lm_studio.py serve [--port 1234] [--chat ID] [--reader ID] [--embed ID] [--api v0|v1|both]
                                              [--log FILE]
    python scripts/ci/fake_lm_studio.py check --app http://127.0.0.1:8597 [--chat ID] [--reader ID] [--embed ID]
                                              [--log FILE]

``serve``: an OpenAI-compatible server on 127.0.0.1, as LM Studio is with three models loaded: the chat model (Qwen
3.5 9B, a vision-language model), the page reader (OvisOCR2) and an embedding model (Qwen3 Embedding). It answers
``/v1/models`` (every model, as LM Studio lists them), LM Studio 0.3's ``/api/v0/models`` and 0.4's ``/api/v1/models``
in their own shapes, ``/v1/chat/completions`` (a plain answer; each request's model and how many page images it held
are written to ``--log``), ``/v1/embeddings`` and ``/api/v1/models/load``.

``check``: AP Coder's Settings > Reading (the running web app) names the chat model for GL coding and OvisOCR2 as the
page reader, lists the embedding model as not used; and (``--log``) no request ever asked the embedding model, and
none without a page image asked OvisOCR2 (GL coding is never sent to the page reader).

Standard library only.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

CHAT = "qwen/qwen3.5-9b"  # LM Studio's key for Qwen 3.5 9B (GGUF, Q4_K_M)
READER = "ath-maas_ovisocr2"  # bartowski's OvisOCR2 build, as LM Studio names it
EMBED = "text-embedding-qwen3-embedding-0.6b"  # LM Studio's key for Qwen3 Embedding 0.6B


def models(chat: str, reader: str, embed: str) -> list[dict]:
    return [
        {"id": chat, "type": "vlm", "arch": "qwen3_5", "publisher": "qwen", "quant": "Q4_K_M", "ctx": 8192},
        {"id": reader, "type": "vlm", "arch": "qwen3_5", "publisher": "bartowski", "quant": "Q8_0", "ctx": 20480},
        {"id": embed, "type": "embeddings", "arch": "qwen3", "publisher": "qwen", "quant": "Q8_0", "ctx": 32768},
    ]


def _enum(node: object, key: str) -> list:
    """The enum of the first ``key`` property anywhere in a JSON schema ([] when none)."""
    if isinstance(node, dict):
        found = node.get("properties", {}).get(key) if isinstance(node.get("properties"), dict) else None
        if isinstance(found, dict) and found.get("enum"):
            return list(found["enum"])
        return next((e for e in (_enum(v, key) for v in node.values()) if e), [])
    if isinstance(node, list):
        return next((e for e in (_enum(v, key) for v in node) if e), [])
    return []


def account_lines(request: dict) -> list[dict]:
    """A GL question (its schema lists the chart's accounts) answered as a model would: the first account for each
    numbered line of the question. Anything else gets no lines."""
    codes = _enum(request.get("response_format"), "gl_code")
    texts = [m.get("content") for m in request.get("messages", []) if m.get("role") == "user"]
    numbers = re.findall(r"^(\d+)\. ", texts[-1] if texts and isinstance(texts[-1], str) else "", re.M)
    if not codes:
        return []
    return [{"line_number": int(n), "gl_code": codes[0], "cost_center": "", "reason": "fake"} for n in numbers]


def reviewer_answer(request: dict, mode: str) -> dict | None:
    """An AI reviewer's request (its schema has "confident") answered as ``mode`` says: "agree" with everything,
    "disagree" with the total (it reads 9,999.00, every time), or "unsure" (never confident). None for any other."""
    schema = (request.get("response_format") or {}).get("json_schema", {}).get("schema") or {}
    if "confident" not in (schema.get("properties") or {}):
        return None
    fields = _enum(schema, "field")
    texts = [p.get("text", "") for m in request.get("messages", []) if isinstance(m.get("content"), list)
             for p in m["content"] if isinstance(p, dict) and p.get("type") == "text"]  # fmt: skip
    asked = texts[-1].split("For reference")[0] if texts else ""
    numbers = sorted({int(n) for n in re.findall(r'"line_number": (\d+)', asked)})
    wrong = mode == "disagree"
    return {"fields": [{"field": f, "agree": not (wrong and f == "grand_total"),
                        "on_page": "9,999.00" if wrong and f == "grand_total" else None, "reason": "fake"}
                       for f in fields],
            "lines": [{"line_number": n, "agree": True, "reason": ""} for n in numbers],
            "confident": mode != "unsure", "summary": f"fake reviewer: {mode}"}  # fmt: skip


def serve(port: int, chat: str, reader: str, embed: str, api: str, log: Path | None, reviewer: str = "agree") -> None:
    listed = models(chat, reader, embed)

    def write(entry: dict) -> None:
        if log is not None:
            with log.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"t": round(time.time(), 3), **entry}) + "\n")

    def v0(m: dict) -> dict:
        found = {"id": m["id"], "object": "model", "type": m["type"], "publisher": m["publisher"], "arch": m["arch"],
                 "compatibility_type": "gguf", "quantization": m["quant"], "state": "loaded",
                 "max_context_length": 262144, "loaded_context_length": m["ctx"]}  # fmt: skip
        return found if m["type"] == "embeddings" else {**found, "capabilities": ["tool_use"]}

    def v1(m: dict) -> dict:
        found = {"type": "embedding" if m["type"] == "embeddings" else "llm", "publisher": m["publisher"],
                 "key": m["id"], "display_name": m["id"], "architecture": m["arch"],
                 "quantization": {"name": m["quant"], "bits_per_weight": 4}, "max_context_length": 262144,
                 "format": "gguf",
                 "loaded_instances": [{"id": m["id"], "config": {"context_length": m["ctx"]}}]}  # fmt: skip
        if m["type"] != "embeddings":
            found["capabilities"] = {"vision": True, "trained_for_tool_use": True,
                                     "reasoning": {"allowed_options": ["off", "on"], "default": "on"}}  # fmt: skip
        return found

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: object) -> None:
            pass

        def send(self, code: int, payload: object) -> None:
            body = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            write({"method": "GET", "path": self.path})
            if self.path == "/v1/models":
                data = [{"id": m["id"], "object": "model", "owned_by": "organization_owner"} for m in listed]
                self.send(200, {"object": "list", "data": data})
            elif self.path == "/api/v0/models" and api in ("v0", "both"):
                self.send(200, {"object": "list", "data": [v0(m) for m in listed]})
            elif self.path == "/api/v1/models" and api in ("v1", "both"):
                self.send(200, {"models": [v1(m) for m in listed]})
            else:
                self.send(404, {"error": f"Unexpected endpoint or method. (GET {self.path})"})

        def do_POST(self) -> None:  # noqa: N802
            try:
                request = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            except ValueError:
                request = {}
            model = str(request.get("model", ""))
            parts = [part for message in request.get("messages", []) if isinstance(message.get("content"), list)
                     for part in message["content"]]  # fmt: skip
            images = sum(1 for part in parts if isinstance(part, dict) and part.get("type") == "image_url")
            write({"method": "POST", "path": self.path, "model": model, "images": images})
            kinds = {m["id"]: m["type"] for m in listed}
            if self.path == "/api/v1/models/load":
                self.send(200, {"type": "llm", "instance_id": model, "status": "loaded"})
            elif self.path == "/v1/embeddings":
                self.send(
                    200, {"object": "list", "data": [{"object": "embedding", "index": 0, "embedding": [0.0] * 8}]}
                )
            elif self.path != "/v1/chat/completions":
                self.send(404, {"error": f"Unexpected endpoint or method. (POST {self.path})"})
            elif model not in kinds:
                self.send(404, {"error": f"Model '{model}' not found"})
            elif kinds[model] == "embeddings":
                self.send(400, {"error": f"'{model}' is not a llm"})
            else:
                review = reviewer_answer(request, reviewer)
                if review is not None:
                    text = json.dumps(review)
                else:
                    text = "INVOICE\nTotal 0.00" if images else json.dumps({"lines": account_lines(request)})
                if request.get("stream"):
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.end_headers()
                    for delta, end in (({"role": "assistant", "content": text}, None), ({}, "stop")):
                        chunk = {"id": "c", "object": "chat.completion.chunk", "created": 0, "model": model,
                                 "choices": [{"index": 0, "delta": delta, "finish_reason": end}]}  # fmt: skip
                        self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                    self.wfile.write(b"data: [DONE]\n\n")
                    return
                self.send(200, {"id": "c", "object": "chat.completion", "created": 0, "model": model,
                                "choices": [{"index": 0, "finish_reason": "stop",
                                             "message": {"role": "assistant", "content": text}}],
                                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})  # fmt: skip

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Fake LM Studio on 127.0.0.1:{port}: {', '.join(m['id'] for m in listed)} ({api})", flush=True)
    server.serve_forever()


def _page(app: str, path: str) -> str:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(app.rstrip("/") + path, timeout=60) as response:
        text = re.sub(r"<[^>]+>", " ", response.read().decode("utf-8", "replace"))
    return re.sub(r"\s+", " ", html.unescape(text))


def check(app: str, chat: str, reader: str, embed: str, log: Path | None) -> list[str]:
    """What is wrong in AP Coder's choice of models ([] when nothing)."""
    problems = []
    text = _page(app, "/settings/reading")
    table = text.split("Models in LM Studio", 1)[-1]
    expected = {
        f"GL coding model: {chat}": re.search(rf"GL coding model \S* ?Working {re.escape(chat)} in LM Studio", text),
        f"page reader: {reader}": f"Found in LM Studio {reader}" in text,
        f"table: {chat} suggests GL accounts": re.search(
            rf"{re.escape(chat)} yes[^|]{{0,60}}?suggests GL accounts", table
        ),
        f"table: {reader} reads pages": re.search(
            rf"{re.escape(reader)} yes[^|]{{0,60}}?document reader reads pages", table
        ),
        f"table: {embed} not used": re.search(
            rf"{re.escape(embed)} yes[^|]{{0,60}}?not used \(an embedding model\)", table
        ),
    }
    for what, found in expected.items():
        print(f"{'ok ' if found else 'NO '} Settings > Reading: {what}")
        if not found:
            problems.append(what)
    for name in (embed, reader):  # the wrong model for GL coding never shows there
        if re.search(rf"GL coding model \S* ?Working {re.escape(name)} ", text):
            problems.append(f"GL coding model is {name}")
    if log is not None and log.exists():
        asked = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]
        chats = [a for a in asked if a.get("path") == "/v1/chat/completions"]
        print(f"    chat requests: {sorted({(a['model'], bool(a['images'])) for a in chats})}")
        if any(a["model"] == embed for a in chats):
            problems.append("the embedding model was asked to chat")
        if any(a["model"] == reader and not a["images"] for a in chats):
            problems.append("OvisOCR2 was asked without a page (GL coding sent to the page reader)")
        if any(a["model"] not in (chat, reader) for a in chats):
            problems.append(f"a model it doesn't have was asked: {sorted({a['model'] for a in chats})}")
    print("Model choice checked: all right." if not problems else f"Model choice WRONG: {problems}")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("serve", "check"):
        p = sub.add_parser(name)
        p.add_argument("--chat", default=CHAT)
        p.add_argument("--reader", default=READER)
        p.add_argument("--embed", default=EMBED)
        p.add_argument("--log", type=Path)
        if name == "serve":
            p.add_argument("--port", type=int, default=1234)
            p.add_argument("--api", default="both", choices=["v0", "v1", "both"])
            p.add_argument(
                "--reviewer",
                default="agree",
                choices=["agree", "disagree", "unsure"],
                help="how the chat model answers the AI reviewer's questions",
            )
        else:
            p.add_argument("--app", required=True, help="AP Coder's address (http://127.0.0.1:8597)")
    args = parser.parse_args(argv)
    if args.command == "serve":
        serve(args.port, args.chat, args.reader, args.embed, args.api, args.log, args.reviewer)
        return 0
    return 1 if check(args.app, args.chat, args.reader, args.embed, args.log) else 0


if __name__ == "__main__":
    sys.exit(main())
