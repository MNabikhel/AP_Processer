"""A stand-in for LM Studio with the pilot tester's three models, and a check of which one AP Coder uses for what.

    python scripts/ci/fake_lm_studio.py serve [--port 1234] [--chat ID] [--reader ID] [--embed ID] [--api v0|v1|both]
                                              [--log FILE]
    python scripts/ci/fake_lm_studio.py check --app http://127.0.0.1:8597 [--chat ID] [--reader ID] [--embed ID]
                                              [--log FILE]

``serve``: an OpenAI-compatible server on 127.0.0.1, as LM Studio is with three models loaded: the chat model (Qwen
3.5 9B, a vision-language model), the page reader (OvisOCR2) and an embedding model (Qwen3 Embedding). It is the
benches' stand-in model server (ap_coder/bench/fake_model.py, standard library only, imported from this checkout):
``/v1/models`` (every model, as LM Studio lists them), LM Studio 0.3's ``/api/v0/models`` and 0.4's
``/api/v1/models`` in their own shapes, ``/v1/chat/completions`` (each request's model and how many page images it
held are written to ``--log``), ``/v1/embeddings`` and ``/api/v1/models/load``.

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
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # this checkout's ap_coder (CI's Python has no install)
from ap_coder.bench.fake_model import CHAT, EMBED, READER, FakeModel  # noqa: E402


def models(chat: str, reader: str, embed: str) -> list[dict]:
    return [
        {"id": chat, "type": "vlm", "arch": "qwen3_5", "publisher": "qwen", "quant": "Q4_K_M", "ctx": 8192},
        {"id": reader, "type": "vlm", "arch": "qwen3_5", "publisher": "bartowski", "quant": "Q8_0", "ctx": 20480},
        {"id": embed, "type": "embeddings", "arch": "qwen3", "publisher": "qwen", "quant": "Q8_0", "ctx": 32768},
    ]


def serve(port: int, chat: str, reader: str, embed: str, api: str, log: Path | None, reviewer: str = "agree") -> None:
    fake = FakeModel("127.0.0.1", port, models=models(chat, reader, embed), api=api, reviewer=reviewer, log=log,
                     slots=8)  # fmt: skip
    fake.start()
    print(f"Fake LM Studio on 127.0.0.1:{fake.port}: {', '.join((chat, reader, embed))} ({api})", flush=True)
    fake.serve_forever()


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
