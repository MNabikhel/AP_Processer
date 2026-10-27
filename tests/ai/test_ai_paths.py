"""The AI-on paths, end to end against a fake LM Studio (D-92): LM Studio is not on the computers that build AP Coder,
so the paths that need it are run here against a scripted OpenAI-compatible server on 127.0.0.1, after the round-2
changes (the store split, the lessons guard, tax-code learning, the JD Edwards findings):

- the AI coder: an invoice read and coded through the pipeline with the chat model suggesting the lines no approval
  taught, approved, then the vendor's next invoice coded from that approval;
- the page reader: its self-test on the test invoice, then a waiting invoice read and folded in, with the chat model
  reading the header from the page images;
- the verifier (the AI reviewer): the invoice checked against its pages, every item agreed.

The fake answers like LM Studio 0.4 with two models loaded: OvisOCR2 (it reads pages, streamed) and a chat model that
can see pages. Each answer is built from the request (the lines it is asked about, the fields in the schema), so the
code under test is the real one, HTTP and all. A real model's accuracy is measured on the pilot laptop (D-92)."""

from __future__ import annotations

import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from ap_coder.ai import local_llm, page_reader, page_worker, verifier
from ap_coder.ai.inference import MODEL_SUGGESTED
from ap_coder.ai.pipeline import InvoicePipeline
from ap_coder.bench.coding import ITEM_GL
from ap_coder.bench.generator import make_stream_case
from ap_coder.core.config import LocalLLMSettings, Settings
from ap_coder.core.store import REVIEW, Store, load_sample_setup
from ap_coder.erp import jde
from ap_coder.web.core import SHOW_JDE
from tests.ai.test_local_llm import REAL_FETCH
from tests.conftest import SAMPLE_STEM, SAMPLES

CHAT, OVIS = "gemma-4-12b-it", "ath-maas_ovisocr2"


def item_of(description: str) -> str:
    """The catalog item a line is, read with more words around its name ("SB-55514 Shipping cartons", "... 3 visit")."""
    items = [k for k in ITEM_GL if k.lower() in description.lower()]
    return max(items, key=len) if items else ""


def gl_for(description: str) -> str:
    return ITEM_GL.get(item_of(description), "6900")


def _pdf_pages(path: Path) -> list[str]:
    import fitz

    with fitz.open(path) as doc:
        return [page.get_text() for page in doc]


class ScriptedLMStudio:
    """LM Studio with OvisOCR2 and a chat model that can see, loaded. ``pages``: what OvisOCR2 writes for the next
    pages it is shown, in order; ``header``: the header the chat model reads off the page images. ``asked`` counts
    the requests by kind (suggest, header, review, page)."""

    def __init__(self) -> None:
        self.pages: list[str] = []
        self.header: dict[str, object] = {}
        self.asked: dict[str, int] = {}
        self.suggested: list[str] = []  # the line descriptions the chat model was asked to code
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, status, payload, content_type="application/json"):
                body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):  # noqa: N802
                if self.path == "/v1/models":
                    self._send(200, {"object": "list", "data": [{"id": m, "object": "model"} for m in (OVIS, CHAT)]})
                elif self.path == "/api/v1/models":
                    self._send(200, {"models": [
                        {"type": "llm", "key": OVIS, "max_context_length": 262144,
                         "loaded_instances": [{"id": OVIS, "config": {"context_length": 20480}}],
                         "capabilities": {"vision": True}},
                        {"type": "llm", "key": CHAT, "max_context_length": 131072,
                         "loaded_instances": [{"id": CHAT, "config": {"context_length": 32768}}],
                         "capabilities": {"vision": True}},
                    ]})  # fmt: skip
                else:
                    self._send(404, {"error": "not found"})

            def do_POST(self):  # noqa: N802
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])) or b"{}")
                if not self.path.endswith("/chat/completions"):
                    self._send(200, {"status": "loaded"})
                    return
                if body.get("model") == OVIS:
                    fake.asked["page"] = fake.asked.get("page", 0) + 1
                    text = fake.pages.pop(0) if fake.pages else ""
                    self._stream(text)
                    return
                self._send(200, fake.chat_reply(body))

            def _stream(self, text):
                chunks = [{"choices": [{"index": 0, "delta": {"role": "assistant", "content": line + "\n"},
                                        "finish_reason": None}]} for line in text.split("\n")]  # fmt: skip
                chunks.append({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
                head = {"id": "p", "object": "chat.completion.chunk", "created": 0, "model": OVIS}
                payload = "".join(f"data: {json.dumps({**head, **c})}\n\n" for c in chunks) + "data: [DONE]\n\n"
                self._send(200, payload.encode(), "text/event-stream")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}/v1"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def chat_reply(self, body: dict) -> dict:
        messages = body.get("messages") or []
        system = str(messages[0].get("content") if messages else "")
        user = messages[-1].get("content") if messages else ""
        text = user if isinstance(user, str) else " ".join(p.get("text", "") for p in user if isinstance(p, dict))
        if system.startswith("You code accounts-payable invoice lines"):
            kind, answer = "suggest", {"lines": []}
            for n, description in re.findall(r"(?m)^(\d+)\. (.+?) \| [-\d,.]+$", text):
                self.suggested.append(description)
                gl = gl_for(description)
                answer["lines"].append({"line_number": int(n), "gl_code": gl, "cost_center": "",
                                        "reason": "the kind of thing bought"})  # fmt: skip
        elif "accounts-payable reviewer" in system:
            kind = "review"
            asked = json.loads(text.split("What was entered, to check:\n", 1)[1].split("\nFor reference", 1)[0])
            answer = {"fields": [{"field": f, "agree": True, "on_page": None, "reason": "as printed"}
                                 for f in asked["fields"]],
                      "lines": [{"line_number": li["line_number"], "agree": True, "reason": "on the page"}
                                for li in asked["lines"]],
                      "confident": True, "summary": "Everything entered is on the pages."}  # fmt: skip
        else:
            kind = "header"
            schema = ((body.get("response_format") or {}).get("json_schema") or {}).get("schema") or {}
            answer = {k: self.header.get(k) for k in (schema.get("properties") or self.header)}
        self.asked[kind] = self.asked.get(kind, 0) + 1
        return {"id": "c1", "object": "chat.completion", "created": 0, "model": CHAT,
                "choices": [{"index": 0, "finish_reason": "stop",
                             "message": {"role": "assistant", "content": json.dumps(answer)}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}  # fmt: skip

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def lm(monkeypatch):
    monkeypatch.setattr(local_llm, "_fetch_json", REAL_FETCH)  # conftest refuses every model server
    local_llm.forget_status()
    page_reader.forget_status()
    fake = ScriptedLMStudio()
    yield fake
    fake.close()
    local_llm.forget_status()
    page_reader.forget_status()


@pytest.fixture
def setup(tmp_path, lm):
    store = Store(tmp_path / "private" / "ap_coder.db")
    load_sample_setup(store, cost_centers=False)
    store.set_setting(SHOW_JDE, "1")  # JD Edwards is the export: its findings are part of every check (D-75)
    jde.save(store, jde.JdeSettings(default_bu="CC700"))
    settings = Settings(llm=LocalLLMSettings(provider="local", base_url=lm.base_url))
    return store, settings


def _truth(case) -> dict:
    return {k: v["value"] for k, v in case.truth["fields"].items()}


def test_the_ai_coder_codes_a_new_vendor_then_learns_from_the_approval(setup, lm, tmp_path):
    store, settings = setup
    pipeline = InvoicePipeline(settings, page_worker.reference_data(store), store=store)
    first = make_stream_case(tmp_path / "cases", 5, 7, 0)
    result = pipeline.process(first.path)
    assert result.error is None and lm.asked.get("suggest") == 1
    lines = result.output["line_items"]
    assert [li["predicted_gl_code"] for li in lines] == [gl_for(t["description"]) for t in first.truth["line_items"]]
    assert all(li["reasoning_justification"].startswith(MODEL_SUGGESTED) for li in lines)
    codes = {i["code"] for i in result.report.to_dict()["issues"]}
    assert "JDE_NO_AN8" in codes  # the JD Edwards findings run on the AI's coding (no address number yet)

    store.approve_invoice(result.invoice_id, store.get_invoice(result.invoice_id)["ai_output"], "Pat Tester")
    taught = store.feedback_rows(vendor_name=_truth(first)["vendor_name"])
    assert len(taught) == len(lines) and all(r["final_tax_code"] for r in taught)  # tax codes learned too

    lm.suggested.clear()
    second = make_stream_case(tmp_path / "cases", 5, 7, 1)
    again = pipeline.process(second.path)
    seen = {item_of(li["description"]) for li in lines}
    repeated = [li for li in again.output["line_items"] if item_of(li["description"]) in seen]
    assert repeated, "the second invoice repeats a line of the first"
    for li in repeated:  # coded from the approval: the chat model is not asked about them
        assert li["predicted_gl_code"] == gl_for(li["description"]) and li["description"] not in lm.suggested
        assert not li["reasoning_justification"].startswith(MODEL_SUGGESTED)


def test_the_page_reader_passes_its_self_test_then_reads_an_invoice_and_the_reviewer_agrees(setup, lm, tmp_path):
    store, settings = setup
    lm.pages = _pdf_pages(SAMPLES / f"{SAMPLE_STEM}.pdf")  # the test invoice, read as printed
    record = page_worker.run_test(settings, store, OVIS)
    assert record is not None and record.get("ok"), record
    assert page_worker.ready(settings, store) == (OVIS, "")

    case = make_stream_case(tmp_path / "cases", 5, 3, 0)
    result = InvoicePipeline(settings, page_worker.reference_data(store), store=store).process(case.path)
    assert result.error is None
    lm.pages = _pdf_pages(case.path)
    lm.header = {"vendor_name": _truth(case)["vendor_name"], "invoice_number": _truth(case)["invoice_number"],
                 "invoice_date": _truth(case)["invoice_date"], "grand_total": _truth(case)["grand_total"]}  # fmt: skip
    outcome = page_worker.read_one(settings, store, invoice_id=result.invoice_id)
    assert outcome is not None and outcome.status == "done" and outcome.updated, outcome
    inv = store.get_invoice(result.invoice_id)
    assert inv["meta"]["page_reader"]["model"] == OVIS and inv["status"] == REVIEW
    assert lm.asked.get("header", 0) >= 1  # the chat model read the header off the pages too
    assert inv["ai_output"]["invoice_number"] == _truth(case)["invoice_number"]

    reviewed = verifier.verify_one(settings, store)
    assert reviewed is not None and reviewed.invoice_id == result.invoice_id, reviewed
    assert reviewed.status == "agreed" and lm.asked.get("review") == 1, reviewed
    assert any(e["action"] == "verified" for e in store.events(result.invoice_id))


def test_the_coding_bench_measures_the_chat_model_with_ai(lm, tmp_path):
    """``python -m ap_coder.bench coding --ai``: the real model's accuracy run on the pilot laptop (D-92), here
    against the fake: the lines nothing else coded are asked, the answers scored against the truth."""
    from ap_coder.bench.coding import ai_coder, run_coding

    coder = ai_coder(Settings(llm=LocalLLMSettings(provider="local", base_url=lm.base_url)))
    report = run_coding(4, 3, 1, tmp_path / "bench", None, coder)
    cm = report["summary"]["chat_model"]
    assert cm["asked"] > 0 and cm["answered"] == cm["asked"] == len(lm.suggested) and lm.asked["suggest"] >= 1
    assert cm["first invoice"]["asked"] > 0 and report["config"]["model"] == CHAT
    assert "## The chat model (--ai)" in (tmp_path / "bench" / "report.md").read_text(encoding="utf-8")


def test_the_coding_bench_says_when_lm_studio_is_not_running(tmp_path, capsys):
    from ap_coder.bench.__main__ import main

    assert main(["coding", "--ai", "--vendors", "2", "--invoices", "1", "--out", str(tmp_path / "b")]) == 1
    assert "--ai needs the chat model running in LM Studio" in capsys.readouterr().err
