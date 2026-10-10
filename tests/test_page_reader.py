"""The page reader: settings, which model reads pages, loading it in LM Studio, rendering, the streamed reading with
loop detection, the reading cache, the test invoice and the models-in-use rows. No network: LM Studio's answers are
the real ones it gave on 2026-10-09 (OvisOCR2 loaded with a 20,480-token context), served by a fake."""

import json
import sys
import threading
import types
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest
from PIL import Image

from ap_coder import local_llm, page_reader
from ap_coder.capture.types import Reading
from ap_coder.config import PageReaderSettings, Settings
from ap_coder.page_reader import (
    GENERAL,
    NOT_DOWNLOADED,
    OVIS,
    OVIS_PROMPT,
    Blank,
    CutOff,
    PageReaderError,
    Stopped,
    document_reader,
    reader_for,
    reader_name,
)

from .conftest import SAMPLE_STEM, SAMPLES

BASE = "http://127.0.0.1:1234/v1"
OVIS_KEY = "ath-maas_ovisocr2"
SAMPLE_PDF = SAMPLES / f"{SAMPLE_STEM}.pdf"  # two pages: the totals are on the second

# LM Studio 0.4's /api/v1/models and its older /api/v0/models, as they answered here (headless, OvisOCR2 loaded).
REAL_V1 = json.loads(
    '{"models":[{"type":"llm","publisher":"bartowski","key":"ath-maas_ovisocr2","display_name":"ATH MaaS OvisOCR2",'
    '"architecture":"qwen35","quantization":{"name":"Q8_0","bits_per_weight":8},"size_bytes":1018563360,'
    '"params_string":"752M","loaded_instances":[{"id":"ath-maas_ovisocr2","config":{"context_length":20480,'
    '"eval_batch_size":2048,"physical_batch_size":512,"parallel":4,"flash_attention":false,"context_checkpoints":32,'
    '"reasoning_budget_message":"","speculative_draft_mtp":false,"speculative_draft_simple":false,'
    '"speculative_draft_model":"","speculative_draft_max_tokens":3,"speculative_draft_min_tokens":0,'
    '"speculative_draft_min_continue_probability":0,"offload_kv_cache_to_gpu":true}}],"max_context_length":262144,'
    '"format":"gguf","capabilities":{"vision":true,"trained_for_tool_use":true,"reasoning":{"allowed_options":'
    '["off","on"],"default":"on"}},"description":null},{"type":"llm","publisher":"bartowski",'
    '"key":"qwen2.5-7b-instruct","display_name":"Qwen2.5 7B Instruct","architecture":"qwen2","quantization":'
    '{"name":"Q4_K_M","bits_per_weight":4},"size_bytes":4683074240,"params_string":"7B","loaded_instances":[],'
    '"max_context_length":32768,"format":"gguf","capabilities":{"vision":false,"trained_for_tool_use":true},'
    '"description":null},{"type":"embedding","publisher":"nomic-ai","key":"text-embedding-nomic-embed-text-v1.5",'
    '"display_name":"Nomic Embed Text v1.5","quantization":{"name":"Q4_K_M","bits_per_weight":4},'
    '"size_bytes":84106624,"params_string":null,"loaded_instances":[],"max_context_length":2048,"format":"gguf"}]}'
)
REAL_V0 = json.loads(
    '{"data":[{"id":"ath-maas_ovisocr2","object":"model","type":"vlm","publisher":"bartowski","arch":"qwen35",'
    '"compatibility_type":"gguf","quantization":"Q8_0","state":"loaded","max_context_length":262144,'
    '"loaded_context_length":20480,"capabilities":["tool_use"]},{"id":"qwen2.5-7b-instruct","object":"model",'
    '"type":"llm","publisher":"bartowski","arch":"qwen2","compatibility_type":"gguf","quantization":"Q4_K_M",'
    '"state":"not-loaded","max_context_length":32768,"capabilities":["tool_use"]},{"id":'
    '"text-embedding-nomic-embed-text-v1.5","object":"model","type":"embeddings","publisher":"nomic-ai",'
    '"arch":"nomic-bert","compatibility_type":"gguf","quantization":"Q4_K_M","state":"not-loaded",'
    '"max_context_length":2048}],"object":"list"}'
)
REAL_IDS = {"data": [{"id": m["key"], "object": "model"} for m in REAL_V1["models"]], "object": "list"}


def v1_model(key, *, vision=True, loaded=None, max_context=262144):
    """One /api/v1/models entry, shaped like LM Studio's (``loaded``: the context of a loaded instance)."""
    instances = [] if loaded is None else [{"id": key, "config": {"context_length": loaded, "parallel": 4}}]
    return {"type": "llm", "key": key, "loaded_instances": instances, "max_context_length": max_context,
            "capabilities": {"vision": vision, "trained_for_tool_use": True}}  # fmt: skip


def lm_studio(*models):
    """The routes of an LM Studio with these /api/v1/models entries (listed in order)."""
    return {
        "/v1/models": {"data": [{"id": m["key"], "object": "model"} for m in models], "object": "list"},
        "/api/v1/models": {"models": list(models)},
    }


@pytest.fixture(autouse=True)
def fresh_reader(monkeypatch):
    """No status, listing, load failure or thinking-switch refusal carried from one test to the next."""
    monkeypatch.setattr(local_llm, "_thinking_off_by_model", {})
    page_reader.forget_status()
    page_reader.forget_reader_failures()
    yield
    page_reader.forget_status()
    page_reader.forget_reader_failures()


@pytest.fixture
def serve(monkeypatch):
    """Answer the model server's GET routes (by path; other hosts and paths refuse, like a stopped server)."""

    def start(routes, host="127.0.0.1:1234"):
        def fetch(url, api_key, timeout):
            parsed = urlparse(url)
            if parsed.netloc == host and parsed.path in routes:
                return routes[parsed.path]
            raise ConnectionRefusedError(url)

        monkeypatch.setattr(local_llm, "_fetch_json", fetch)
        page_reader.forget_status()

    return start


class Posts(list):
    fail = None  # LM Studio's reason for refusing to load, when it does


@pytest.fixture
def posts(monkeypatch):
    """LM Studio's load / unload routes, recorded. ``posts.fail`` makes the load route refuse with that reason."""
    sent = Posts()

    def post(url, body, api_key, timeout):
        path = urlparse(url).path
        sent.append((path, body))
        if path.endswith("/load") and sent.fail:
            raise PageReaderError(sent.fail)
        return {"status": "loaded"}

    monkeypatch.setattr(page_reader, "_post_json", post)
    return sent


def reader_settings(**reader):
    return Settings(page_reader=PageReaderSettings(**reader))


# --- Settings -------------------------------------------------------------------------------------------------


def _clear_env(monkeypatch):
    import os

    for key in [k for k in os.environ if k.startswith("AP_PAGE_READER")]:
        monkeypatch.delenv(key)


def test_page_reader_settings_default(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    settings = Settings.from_env(tmp_path / "missing.env")
    assert settings.page_reader == PageReaderSettings()
    assert settings.page_reader == Settings().page_reader
    assert (settings.page_reader.mode, settings.page_reader.scope, settings.page_reader.model) == ("auto", "scans", "")
    assert (settings.page_reader.timeout_seconds, settings.page_reader.max_pages) == (1200.0, 5)
    assert settings.with_overrides(vision=True).page_reader == settings.page_reader  # kept by CLI overrides


def test_page_reader_settings_from_env(monkeypatch, tmp_path):
    _clear_env(monkeypatch)
    monkeypatch.setenv("AP_PAGE_READER", "ASK")
    monkeypatch.setenv("AP_PAGE_READER_SCOPE", "all")
    monkeypatch.setenv("AP_PAGE_READER_MODEL", OVIS_KEY)
    monkeypatch.setenv("AP_PAGE_READER_BASE_URL", "127.0.0.1:5678/v1/chat/completions")
    monkeypatch.setenv("AP_PAGE_READER_TIMEOUT_SECONDS", "900,5")
    monkeypatch.setenv("AP_PAGE_READER_MAX_PAGES", "3")
    reader = Settings.from_env(tmp_path / "missing.env").page_reader
    assert reader == PageReaderSettings("ask", "all", OVIS_KEY, "http://127.0.0.1:5678/v1", 900.5, 3)


@pytest.mark.parametrize(
    "env, field, value",
    [
        ({"AP_PAGE_READER": "sometimes"}, "mode", "auto"),
        ({"AP_PAGE_READER": "false"}, "mode", "off"),
        ({"AP_PAGE_READER": "Off"}, "mode", "off"),
        ({"AP_PAGE_READER_SCOPE": "everything"}, "scope", "scans"),
        ({"AP_PAGE_READER_MODEL": "Automatic"}, "model", ""),
        ({"AP_PAGE_READER_BASE_URL": "  "}, "base_url", ""),
        ({"AP_PAGE_READER_TIMEOUT_SECONDS": "ten minutes"}, "timeout_seconds", 1200.0),
        ({"AP_PAGE_READER_TIMEOUT_SECONDS": "-5"}, "timeout_seconds", 1200.0),
        ({"AP_PAGE_READER_TIMEOUT_SECONDS": "0"}, "timeout_seconds", 1200.0),
        ({"AP_PAGE_READER_MAX_PAGES": "0"}, "max_pages", 5),
        ({"AP_PAGE_READER_MAX_PAGES": "two"}, "max_pages", 5),
        ({"AP_PAGE_READER_MAX_PAGES": "-1"}, "max_pages", 5),
    ],
)
def test_page_reader_settings_invalid_values_keep_the_default(monkeypatch, tmp_path, env, field, value):
    _clear_env(monkeypatch)
    for key, text in env.items():
        monkeypatch.setenv(key, text)
    assert getattr(Settings.from_env(tmp_path / "missing.env").page_reader, field) == value


# --- Readers ----------------------------------------------------------------------------------------------------


def test_reader_for_and_document_reader():
    for name in (OVIS_KEY, "OvisOCR2-Q8_0", "bartowski/ATH-MaaS_OvisOCR2-GGUF", "ovis-ocr2"):
        assert reader_for(name) is OVIS and document_reader(name), name
        assert reader_name(name) == "OvisOCR2"
    for name in ("qwen3.5-9b", "gemma-3-12b-it", "llava:13b", "", "ovis2-8b"):
        assert reader_for(name) is GENERAL and not document_reader(name), name
    assert reader_name("qwen3.5-9b") == "qwen3.5-9b"
    assert (OVIS.dpi, OVIS.max_side, OVIS.max_tokens, OVIS.context, OVIS.prompt) == (
        200,
        2048,
        12288,
        20480,
        OVIS_PROMPT,
    )
    assert (GENERAL.dpi, GENERAL.max_side, GENERAL.max_tokens, GENERAL.context) == (150, 1600, 8192, 0)
    assert "markdown table" in GENERAL.prompt and "HTML" in OVIS.prompt


# --- LM Studio's model list -------------------------------------------------------------------------------------


def test_listing_parser_reads_lm_studio_0_4():
    listing = page_reader.parse_lm_studio_listing(REAL_V1)
    assert listing.route == "v1"
    assert [m.key for m in listing.models] == [OVIS_KEY, "qwen2.5-7b-instruct"]  # the embedding model left out
    ovis, qwen = listing.models
    assert ovis.vision and ovis.instances == ((OVIS_KEY, 20480),) and ovis.loaded_context == 20480
    assert ovis.max_context == 262144 and ovis.reasoning == ("off", "on")
    assert not qwen.vision and qwen.instances == () and qwen.loaded_context == 0 and qwen.max_context == 32768
    assert listing.find(OVIS_KEY) is ovis and listing.find("missing") is None


def test_listing_parser_reads_lm_studio_0_3():
    listing = page_reader.parse_lm_studio_listing(REAL_V0)
    assert listing.route == "v0"
    ovis, qwen = listing.models
    assert (ovis.key, ovis.vision, ovis.instances) == (OVIS_KEY, True, ((OVIS_KEY, 20480),))
    assert (qwen.key, qwen.vision, qwen.instances) == ("qwen2.5-7b-instruct", False, ())


@pytest.mark.parametrize("data", [REAL_IDS, {"data": []}, [], None, "LM Studio", {"models": "none"}])
def test_listing_parser_rejects_other_servers(data):
    assert page_reader.parse_lm_studio_listing(data) is None


def test_status_with_lm_studio_as_it_answered(serve):
    serve({"/v1/models": REAL_IDS, "/api/v1/models": REAL_V1})
    status = page_reader.reader_status(Settings())
    assert (status.reachable, status.lm_studio, status.model, status.state) == (True, True, OVIS_KEY, "loaded")
    assert status.document_reader and status.usable and status.context == 20480 and status.note == ""
    assert status.candidates == [OVIS_KEY]
    assert status.base_url == BASE


def test_status_with_lm_studio_0_3(serve):
    serve({"/v1/models": REAL_IDS, "/api/v0/models": REAL_V0})
    status = page_reader.reader_status(Settings())
    assert (status.lm_studio, status.model, status.state, status.context) == (True, OVIS_KEY, "loaded", 20480)


def test_document_readers_come_first(serve):
    serve(lm_studio(v1_model("qwen3.5-9b", loaded=8192), v1_model(OVIS_KEY)))
    status = page_reader.reader_status(Settings())
    assert status.candidates == [OVIS_KEY, "qwen3.5-9b"]
    assert (status.model, status.state, status.document_reader) == (OVIS_KEY, "downloaded", True)
    assert "20,480-token context" in status.note


def test_status_says_a_short_context_is_raised(serve):
    serve(lm_studio(v1_model(OVIS_KEY, loaded=4096)))
    status = page_reader.reader_status(Settings())
    assert (status.state, status.context) == ("loaded", 4096)
    assert "4,096-token context" in status.note and "20,480" in status.note


# --- Which model reads pages ------------------------------------------------------------------------------------


def test_the_chat_model_reads_pages_when_it_can_see(serve):
    serve(lm_studio(v1_model("qwen3.5-9b", loaded=8192), v1_model("qwen2.5-7b-instruct", vision=False)))
    status = page_reader.reader_status(Settings())
    assert (status.model, status.state, status.document_reader) == ("qwen3.5-9b", "loaded", False)
    assert status.candidates == ["qwen3.5-9b"]
    assert NOT_DOWNLOADED in status.note


def test_no_model_reads_pages_when_none_can_see(serve):
    serve(lm_studio(v1_model("qwen2.5-7b-instruct", vision=False, loaded=8192)))
    status = page_reader.reader_status(Settings())
    assert (status.model, status.state, status.usable, status.candidates) == ("", "missing", False, [])
    assert status.note.startswith(NOT_DOWNLOADED)
    with pytest.raises(PageReaderError, match="OvisOCR2 isn't in LM Studio"):
        page_reader.transcribe(Settings(), b"png")


def test_a_named_model_is_used_as_it_is(serve):
    serve(lm_studio(v1_model(OVIS_KEY, loaded=20480), v1_model("qwen3.5-9b")))
    status = page_reader.reader_status(reader_settings(model="qwen3.5-9b"))
    assert (status.model, status.state, status.document_reader) == ("qwen3.5-9b", "downloaded", False)
    missing = page_reader.reader_status(reader_settings(model="gemma-3-12b"))
    assert (missing.model, missing.state, missing.usable) == ("gemma-3-12b", "missing", False)
    assert "doesn't have gemma-3-12b" in missing.note
    blind = page_reader.reader_status(reader_settings(model="qwen2.5-7b-instruct"))
    assert blind.state == "missing"  # not in this listing at all
    serve(lm_studio(v1_model("qwen2.5-7b-instruct", vision=False)))
    blind = page_reader.reader_status(reader_settings(model="qwen2.5-7b-instruct"))
    assert blind.state == "blind" and not blind.usable  # it would read nothing: not ready, never tested or loaded
    assert "can't look at pictures: pick a model that can, e.g. OvisOCR2" in blind.note


def test_turned_off_and_down(serve):
    serve({"/v1/models": REAL_IDS, "/api/v1/models": REAL_V1})
    off = page_reader.reader_status(reader_settings(mode="off"))
    assert (off.state, off.model, off.usable) == ("off", "", False)
    assert off.candidates == [OVIS_KEY]  # still listed, for choosing one before turning it on
    serve({})
    down = page_reader.reader_status(Settings())
    assert (down.reachable, down.state, down.model) == (False, "down", "")
    assert "Nothing answered at http://127.0.0.1:1234/v1" in down.note


def test_another_server_is_judged_by_model_names(serve):
    serve({"/v1/models": {"data": [{"id": "qwen2.5:7b"}, {"id": "llava:13b"}]}})  # Ollama: no LM Studio routes
    status = page_reader.reader_status(Settings())
    assert (status.lm_studio, status.candidates) == (False, ["llava:13b"])
    assert (status.model, status.state) == ("", "missing")  # the chat model (qwen2.5:7b) can't see
    serve({"/v1/models": {"data": [{"id": "llava:13b"}]}})
    status = page_reader.reader_status(Settings())
    assert (status.model, status.state, status.document_reader) == ("llava:13b", "loaded", False)


def test_the_page_reader_can_have_its_own_server(serve):
    serve({"/v1/models": REAL_IDS, "/api/v1/models": REAL_V1}, host="127.0.0.1:5678")
    status = page_reader.reader_status(reader_settings(base_url="http://127.0.0.1:5678/v1"))
    assert (status.model, status.state, status.base_url) == (OVIS_KEY, "loaded", "http://127.0.0.1:5678/v1")
    assert page_reader.reader_status(Settings()).state == "down"  # AP_LLM_BASE_URL's server isn't running


# --- Loading it in LM Studio ------------------------------------------------------------------------------------


def test_load_does_nothing_when_loaded_with_enough_context(serve, posts):
    serve({"/v1/models": REAL_IDS, "/api/v1/models": REAL_V1})
    assert page_reader.load_reader(Settings()) == ""
    assert page_reader.load_reader(Settings(), OVIS_KEY) == ""
    assert posts == []


def test_load_longer_then_unload_the_short_one(serve, posts):
    serve(lm_studio(v1_model(OVIS_KEY, loaded=4096)))
    assert page_reader.load_reader(Settings(), OVIS_KEY) == ""
    assert posts == [
        ("/api/v1/models/load", {"model": OVIS_KEY, "context_length": 20480}),
        ("/api/v1/models/unload", {"instance_id": OVIS_KEY}),
    ]


def test_load_a_downloaded_reader(serve, posts):
    serve(lm_studio(v1_model(OVIS_KEY)))
    assert page_reader.load_reader(Settings()) == ""
    assert posts == [("/api/v1/models/load", {"model": OVIS_KEY, "context_length": 20480})]


def test_a_failed_load_is_remembered(serve, posts):
    serve(lm_studio(v1_model(OVIS_KEY), v1_model("qwen3.5-9b", loaded=8192)))
    posts.fail = "Not enough memory to load the model"
    problem = page_reader.load_reader(Settings(), OVIS_KEY)
    assert problem == (
        "LM Studio couldn't load OvisOCR2 with a 20,480-token context (Not enough memory to load the model)."
    )
    assert page_reader.reader_load_problem(OVIS_KEY) == problem
    assert page_reader.load_reader(Settings(), OVIS_KEY) == problem
    assert len(posts) == 1  # not asked again for half an hour
    status = page_reader.reader_status(Settings())  # meanwhile the chat model, which can see, reads pages
    assert (status.model, status.document_reader) == ("qwen3.5-9b", False)
    assert problem in status.note and "half an hour" in status.note
    page_reader.forget_reader_failures()  # the settings were saved
    posts.fail = None
    assert page_reader.load_reader(Settings(), OVIS_KEY) == ""
    assert len(posts) == 2
    assert page_reader.reader_status(Settings()).model == OVIS_KEY


def test_load_leaves_general_models_and_other_servers_alone(serve, posts):
    serve(lm_studio(v1_model("qwen3.5-9b")))
    assert page_reader.load_reader(Settings(), "qwen3.5-9b") == ""  # no context of its own to load it with
    serve({"/v1/models": {"data": [{"id": OVIS_KEY}]}})  # not LM Studio: nothing to ask
    assert page_reader.load_reader(Settings(), OVIS_KEY) == ""
    serve({"/v1/models": REAL_IDS, "/api/v0/models": {"data": [{**REAL_V0["data"][0], "state": "not-loaded"}]}})
    assert "Context Length 20,480" in page_reader.load_reader(Settings(), OVIS_KEY)  # LM Studio 0.3: by hand
    serve(lm_studio(v1_model("qwen3.5-9b")))
    assert page_reader.load_reader(Settings(), OVIS_KEY) == f"LM Studio doesn't have {OVIS_KEY} downloaded."
    assert posts == []


def test_post_json_gives_lm_studio_reason():
    """The real HTTP layer against a server on this computer: LM Studio's own words when it refuses."""
    answers = {"/api/v1/models/load": (500, {"error": {"message": "Insufficient system resources"}}),
               "/api/v1/models/unload": (200, {"instance_id": OVIS_KEY})}  # fmt: skip
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            seen.append((self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
            status, payload = answers[self.path]
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    root = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with pytest.raises(PageReaderError, match=r"^not enough memory to load it \(Insufficient system resources\)$"):
            page_reader._post_json(root + "/api/v1/models/load", {"model": OVIS_KEY}, "lm-studio", 5)
        assert page_reader._post_json(root + "/api/v1/models/unload", {}, "", 5) == {"instance_id": OVIS_KEY}
    finally:
        server.shutdown()
        server.server_close()
    assert seen[0] == ("/api/v1/models/load", {"model": OVIS_KEY})


# --- Rendering ----------------------------------------------------------------------------------------------------


def _size(png):
    assert png.startswith(b"\x89PNG")
    with Image.open(BytesIO(png)) as img:
        return img.size


def _small_pdf(tmp_path, width=216, height=144, pages=1):
    import pymupdf

    doc = pymupdf.open()
    for n in range(pages):
        doc.new_page(width=width, height=height).insert_text((20, 40), f"Page {n + 1}")
    path = tmp_path / "small.pdf"
    doc.save(path)
    return path


def test_render_pdf_pages(tmp_path):
    pages = page_reader.render_pages(SAMPLE_PDF, OVIS, 5)
    assert len(pages) == 2
    for png in pages:
        width, height = _size(png)
        assert 2047 <= height <= 2048 and abs(width / height - 612 / 792) < 0.002  # the long side capped
    assert len(page_reader.render_pages(SAMPLE_PDF, OVIS, 1)) == 1
    assert _size(page_reader.render_pages(SAMPLE_PDF, GENERAL, 1)[0])[1] in (1599, 1600)
    small = _small_pdf(tmp_path)  # 3 x 2 inches: the DPI decides
    assert _size(page_reader.render_pages(small, OVIS, 5)[0]) == (600, 400)
    assert _size(page_reader.render_pages(small, GENERAL, 5)[0]) == (450, 300)


def _image(tmp_path, name, size, *, mode="RGB", dpi=None, exif=None, color="white", frames=1):
    img = Image.new(mode, size, color)
    options = {"dpi": dpi} if dpi else {}
    if exif:
        options["exif"] = exif
    if frames > 1:
        options.update(save_all=True, append_images=[Image.new(mode, size, color) for _ in range(frames - 1)])
    path = tmp_path / name
    img.save(path, **options)
    return path


def test_render_pictures(tmp_path):
    fine_scan = _image(tmp_path, "fine.png", (1200, 800), dpi=(400, 400))  # 3 x 2 inches at 400 DPI
    assert _size(page_reader.render_pages(fine_scan, GENERAL, 5)[0]) == (450, 300)
    assert _size(page_reader.render_pages(fine_scan, OVIS, 5)[0]) == (600, 400)
    photo = _image(tmp_path, "photo.jpg", (3000, 4000))  # no scan resolution: only the long side is capped
    assert _size(page_reader.render_pages(photo, GENERAL, 5)[0]) == (1200, 1600)
    small = _image(tmp_path, "small.png", (1000, 800), dpi=(72, 72))  # never enlarged
    assert _size(page_reader.render_pages(small, OVIS, 5)[0]) == (1000, 800)
    exif = Image.Exif()
    exif[0x0112] = 6  # taken on its side: turned upright
    sideways = _image(tmp_path, "sideways.jpg", (400, 200), exif=exif)
    assert _size(page_reader.render_pages(sideways, OVIS, 5)[0]) == (200, 400)
    tiff = _image(tmp_path, "three.tif", (300, 400), frames=3)
    assert len(page_reader.render_pages(tiff, OVIS, 2)) == 2 and len(page_reader.render_pages(tiff, OVIS, 5)) == 3
    clear = _image(tmp_path, "clear.png", (20, 20), mode="RGBA", color=(0, 0, 0, 0))  # transparent: white paper
    with Image.open(BytesIO(page_reader.render_pages(clear, OVIS, 1)[0])) as img:
        assert img.convert("RGB").getpixel((5, 5)) == (255, 255, 255)
    text = tmp_path / "notes.txt"
    text.write_text("not a page")
    with pytest.raises(PageReaderError):
        page_reader.render_pages(text, OVIS, 1)


# --- Reading a page -------------------------------------------------------------------------------------------------


class FakeStream:
    """A streamed reply: chunks shaped like the OpenAI SDK's, a record of how many were taken and whether it was
    closed (LM Studio stops writing when it is)."""

    def __init__(self, chunks, fail_after=None):
        self.chunks, self.fail_after, self.taken, self.closed = chunks, fail_after, 0, False

    def __iter__(self):
        for chunk in self.chunks:
            if self.fail_after is not None and self.taken >= self.fail_after:
                raise RuntimeError("Model has unloaded or crashed")
            self.taken += 1
            yield chunk

    def close(self):
        self.closed = True


def chunk(content=None, finish=None, reasoning=None):
    delta = SimpleNamespace(content=content, role="assistant")
    if reasoning:
        delta.reasoning_content = reasoning
    return SimpleNamespace(choices=[SimpleNamespace(index=0, delta=delta, finish_reason=finish)])


def stream(text, finish="stop", **kwargs):
    """``text`` written line by line, then the finish reason."""
    pieces = [line + "\n" for line in text.split("\n")]
    return FakeStream([*(chunk(piece) for piece in pieces), chunk(None, finish)], **kwargs)


class StatusError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status_code = status


class FakeClient:
    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


@pytest.fixture
def client(monkeypatch):
    def install(*replies):
        fake = FakeClient(*replies)
        monkeypatch.setattr(page_reader, "_client", lambda settings: fake)
        return fake

    return install


PAGE = (
    "Northwind IT Solutions Inc.\n\nINVOICE\n\nInvoice No: NW-2026-0912\n\n"
    "<table border=1><tr><td>Subtotal</td><td>$15,945.00</td></tr><tr><td>HST 13% (ON)</td><td>$2,072.85</td></tr>"
    "</table>\n\nThank you for your business."
)
THINKING_OFF = {"chat_template_kwargs": {"enable_thinking": False}, "reasoning_effort": "none"}


def test_transcribe_reads_a_page(client):
    fake = client(stream(PAGE))
    assert page_reader.transcribe(Settings(), b"\x89PNG\r\n\x1a\n page", model=OVIS_KEY) == PAGE
    (call,) = fake.calls
    assert (call["model"], call["stream"], call["temperature"], call["max_tokens"]) == (OVIS_KEY, True, 0.0, 12288)
    assert call["extra_body"] == THINKING_OFF  # OvisOCR2 takes reasoning off, as the coding calls send it
    image, prompt = call["messages"][0]["content"]
    assert image["image_url"]["url"].startswith("data:image/png;base64,iVBORw")  # the PNG, base64
    assert prompt == {"type": "text", "text": OVIS_PROMPT}
    general = client(stream(PAGE))
    page_reader.transcribe(Settings(), b"png", model="qwen3.5-9b")
    assert general.calls[0]["max_tokens"] == 8192 and general.calls[0]["messages"][0]["content"][1]["text"] == (
        GENERAL.prompt
    )


def test_transcribe_stops_a_loop_and_reads_again_with_sampling(client):
    looping = stream("Description  Qty  Amount\n" + "\n".join(["| Item | 1 | 10.00 |"] * 200), finish="length")
    again = "\n".join(f"Line {n}: Dell Latitude 7450 laptop, 32GB RAM  3  1,450.00  4,350.00" for n in range(8))
    fake = client(looping, stream(again))
    assert page_reader.transcribe(Settings(), b"png", model=OVIS_KEY) == again
    assert looping.taken < 40 and looping.closed  # stopped once twenty rows were the same, not at the token limit
    first, second = fake.calls
    assert first["temperature"] == 0.0 and "top_p" not in first
    assert (second["temperature"], second["top_p"]) == (0.7, 0.8)
    assert second["extra_body"] == {**THINKING_OFF, "top_k": 20, "presence_penalty": 1.5}


def test_a_page_that_loops_twice(client):
    loop = "\n".join(["| Item | 1 | 10.00 |"] * 100)
    client(stream("Header\n" + loop, finish="length"), stream("Header\n" + loop, finish="length"))
    with pytest.raises(CutOff, match="repeating itself"):
        page_reader.transcribe(Settings(), b"png", model=OVIS_KEY)
    long_page = "\n".join(f"Line {n}: consulting services, hours and expenses" for n in range(10))
    client(stream(long_page + "\n" + loop, finish="length"), stream("Header\n" + loop, finish="length"))
    kept = page_reader.transcribe(Settings(), b"png", model=OVIS_KEY)
    assert kept == long_page + "\n| Item | 1 | 10.00 |"  # the longer reading, the repeated row kept once


def test_html_row_loop_is_cut(client):
    """OvisOCR2 writes a table on one line: its loop is the same empty row over and over on that line."""
    row = "<tr><td></td><td></td><td></td></tr>"
    looping = FakeStream([chunk("<table border=1><tr><td>Qty</td><td>Amount</td></tr>"), *[chunk(row)] * 200,
                          chunk(None, "length")])  # fmt: skip
    again = PAGE + "\n" + "\n".join(f"Line {n}: services rendered in September" for n in range(6))
    client(looping, stream(again))
    assert page_reader.transcribe(Settings(), b"png", model=OVIS_KEY) == again
    assert looping.taken < 60


def test_transcribe_cut_off_blank_and_thinking(client):
    client(stream("Northwind IT Solutions Inc.", finish="length"))
    with pytest.raises(CutOff, match="12,288-token limit"):
        page_reader.transcribe(Settings(), b"png", model=OVIS_KEY)
    client(FakeStream([chunk(""), chunk(None, "stop")]))
    with pytest.raises(Blank):
        page_reader.transcribe(Settings(), b"png", model=OVIS_KEY)
    client(FakeStream([chunk(None, reasoning="Let me look at the page"), chunk(None, "length")]))
    with pytest.raises(PageReaderError, match="thought until its 12,288-token budget") as raised:
        page_reader.transcribe(Settings(), b"png", model=OVIS_KEY)
    assert not isinstance(raised.value, Blank)
    client(stream("<think>The page shows an invoice.</think>Invoice No: NW-2026-0912"))
    assert page_reader.transcribe(Settings(), b"png", model=OVIS_KEY) == "Invoice No: NW-2026-0912"


def test_thinking_fields_refused_are_dropped(client):
    refused = StatusError(400, "Unrecognized request argument supplied: chat_template_kwargs")
    fake = client(refused, refused, stream(PAGE))
    assert page_reader.transcribe(Settings(), b"png", model=OVIS_KEY) == PAGE
    assert [call.get("extra_body") for call in fake.calls] == [
        THINKING_OFF, {"chat_template_kwargs": {"enable_thinking": False}}, None,
    ]  # fmt: skip
    assert local_llm.thinking_off(BASE, OVIS_KEY) == {}  # remembered for this model on this server


def test_sampling_fields_refused_are_dropped_first(client):
    loop = "\n".join(["| Item | 1 | 10.00 |"] * 100)
    again = "\n".join(f"Line {n}: consulting services, hours and expenses" for n in range(10))
    fake = client(stream(loop, finish="length"), StatusError(422, "unknown field top_k"), stream(again))
    assert page_reader.transcribe(Settings(), b"png", model=OVIS_KEY) == again
    assert fake.calls[2]["extra_body"] == THINKING_OFF and fake.calls[2]["temperature"] == 0.7
    assert local_llm.thinking_off(BASE, OVIS_KEY) == {"extra_body": THINKING_OFF}  # the thinking switch kept


def test_server_failures_and_stopping(client):
    client(ConnectionRefusedError("refused"))
    with pytest.raises(PageReaderError, match="isn't answering at http://127.0.0.1:1234/v1"):
        page_reader.transcribe(Settings(), b"png", model=OVIS_KEY)
    broken = stream(PAGE, fail_after=2)
    client(broken)
    with pytest.raises(PageReaderError, match="Model has unloaded or crashed"):
        page_reader.transcribe(Settings(), b"png", model=OVIS_KEY)
    assert broken.closed
    client(StatusError(400, "The number of tokens to keep from the initial prompt is greater than the context length"))
    with pytest.raises(PageReaderError, match="didn't fit the model's context"):
        page_reader.transcribe(Settings(), b"png", model=OVIS_KEY)
    halted = stream(PAGE)
    client(halted)
    with pytest.raises(Stopped):
        page_reader.transcribe(Settings(), b"png", model=OVIS_KEY, should_stop=lambda: True)
    assert halted.closed and halted.taken == 1


def test_transcribe_without_a_server():
    with pytest.raises(PageReaderError, match="Nothing answered"):
        page_reader.transcribe(Settings(), b"png")


def test_loop_detection_leaves_a_page_alone():
    dots = "Signature " + "." * 200
    assert not page_reader._looping(dots + "\n")  # a dot leader is the page's own
    empty_rows = "| # | Description | Amount |\n|---|---|---|\n" + "|  |  |  |\n" * 12 + "Subtotal 15,945.00\n"
    assert not page_reader._looping(empty_rows)  # a dozen empty item rows: an invoice's own
    assert page_reader.trim_loop(PAGE) == (PAGE, False)
    assert page_reader._looping("|  |  |  |\n" * 31)


# --- Reading a document, and the cache --------------------------------------------------------------------------


@pytest.fixture
def pages_read(monkeypatch):
    """transcribe replaced: page n of what it is shown reads "text of page n"; each call recorded."""
    calls = []

    def fake(settings, png, *, model=None, should_stop=None):
        calls.append((png[:8], model))
        if fake.outcomes:
            outcome = fake.outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        return f"text of page {len(calls)}"

    fake.outcomes = []
    monkeypatch.setattr(page_reader, "transcribe", fake)
    fake.calls = calls
    return fake


def _no_server(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("the model server was asked")

    monkeypatch.setattr(page_reader, "reader_status", refuse)
    monkeypatch.setattr(page_reader, "transcribe", refuse)


def test_read_document_keeps_every_page(monkeypatch, pages_read):
    settings = Settings()
    started = []
    reading = page_reader.read_document(
        settings, SAMPLE_PDF, model=OVIS_KEY, on_page=lambda n, k: started.append((n, k))
    )
    assert (reading.model, reading.pages, reading.cached) == (OVIS_KEY, ["text of page 1", "text of page 2"], False)
    assert reading.complete and reading.page_count == 2 and len(reading.seconds) == 2 and reading.error == ""
    assert started == [(1, 2), (2, 2)]
    assert pages_read.calls == [(b"\x89PNG\r\n\x1a\n", OVIS_KEY)] * 2
    folder = page_reader.cache_dir()
    assert str(folder).startswith(str(page_reader.paths.private_dir()))
    (kept,) = [path for path in folder.iterdir() if path.is_dir()]
    entry = json.loads(sorted(kept.glob("p1-*.json"))[0].read_text(encoding="utf-8"))
    assert entry["model"] == OVIS_KEY and entry["text"] == "text of page 1" and entry["prompt_version"].startswith("1-")
    assert set(entry) == {"model", "prompt_version", "page", "text", "seconds", "at"}

    _no_server(monkeypatch)  # read again: from the cache, the server never asked
    again = page_reader.read_document(settings, SAMPLE_PDF, model=OVIS_KEY)
    assert (again.pages, again.cached, again.complete) == (reading.pages, True, True)
    automatic = page_reader.read_document(settings, SAMPLE_PDF)  # a document reader's reading: no need to ask
    assert (automatic.model, automatic.cached) == (OVIS_KEY, True)
    assert page_reader.cached_reading(settings, SAMPLE_PDF).pages == reading.pages
    assert page_reader.cached_reading(settings, SAMPLE_PDF, model="qwen3.5-9b") is None


def test_only_pages_not_read_yet_are_read(pages_read):
    first = page_reader.read_document(reader_settings(max_pages=1), SAMPLE_PDF, model=OVIS_KEY)
    assert first.pages == ["text of page 1"] and first.complete
    whole = page_reader.read_document(Settings(), SAMPLE_PDF, model=OVIS_KEY)
    assert whole.pages == ["text of page 1", "text of page 2"] and not whole.cached
    assert len(pages_read.calls) == 2  # page 1 came from the cache


def test_blank_and_cut_off_pages(pages_read):
    pages_read.outcomes = ["first page", Blank("nothing on it")]
    reading = page_reader.read_document(Settings(), SAMPLE_PDF, model=OVIS_KEY)
    assert reading.pages == ["first page", ""] and reading.complete  # a blank page is a reading too
    pages_read.outcomes = [CutOff("stopped at the 12,288-token limit"), "second page"]
    other = page_reader.read_document(Settings(), SAMPLE_PDF, model="qwen3.5-9b")
    assert other.pages == ["", "second page"] and not other.complete
    assert other.error == "page 1: stopped at the 12,288-token limit"
    pages_read.outcomes = ["page one again"]
    retried = page_reader.read_document(Settings(), SAMPLE_PDF, model="qwen3.5-9b")  # the cut-off page wasn't kept
    assert retried.pages == ["page one again", "second page"] and retried.complete


def test_a_server_failure_stops_the_reading(pages_read):
    pages_read.outcomes = [PageReaderError("the model server isn't answering at http://127.0.0.1:1234/v1")]
    failed = page_reader.read_document(Settings(), SAMPLE_PDF, model=OVIS_KEY)
    assert failed.pages == [] and failed.error == "page 1: the model server isn't answering at http://127.0.0.1:1234/v1"
    assert len(pages_read.calls) == 1 and not failed.complete  # page 2 wasn't tried: it would fail the same way
    assert page_reader.cached_reading(Settings(), SAMPLE_PDF) is None


def test_reading_stops_when_asked(pages_read):
    stopped = page_reader.read_document(
        Settings(), SAMPLE_PDF, model=OVIS_KEY, should_stop=lambda: len(pages_read.calls) >= 1
    )
    assert stopped.stopped and stopped.pages == ["text of page 1"] and not stopped.complete and not stopped.error
    assert page_reader.read_document(Settings(), SAMPLE_PDF, model=OVIS_KEY, should_stop=lambda: True).pages == []
    pages_read.outcomes = [Stopped("stopped before the page was read")]  # asked part way through page 2
    halted = page_reader.read_document(Settings(), SAMPLE_PDF, model=OVIS_KEY)
    assert halted.stopped and halted.pages == ["text of page 1"] and halted.error == ""


def test_read_document_chooses_its_model(monkeypatch, pages_read):
    general = page_reader.read_document(Settings(), SAMPLE_PDF, model="qwen3.5-9b")
    assert general.model == "qwen3.5-9b"

    def status(model, state="loaded"):
        return page_reader.ReaderStatus(True, True, model, document_reader(model), state)

    monkeypatch.setattr(page_reader, "reader_status", lambda settings: status("", "down"))
    kept = page_reader.read_document(Settings(), SAMPLE_PDF)  # no model can read now: the earlier reading
    assert (kept.model, kept.cached) == ("qwen3.5-9b", True)
    monkeypatch.setattr(page_reader, "reader_status", lambda settings: status("qwen3.5-9b"))
    assert page_reader.read_document(Settings(), SAMPLE_PDF).cached
    monkeypatch.setattr(page_reader, "reader_status", lambda settings: status(OVIS_KEY))
    upgraded = page_reader.read_document(Settings(), SAMPLE_PDF)  # a document reader now: read again by it
    assert (upgraded.model, upgraded.cached) == (OVIS_KEY, False)
    assert page_reader.cached_reading(Settings(), SAMPLE_PDF).model == OVIS_KEY  # preferred from now on


def test_read_document_reports_problems(tmp_path, pages_read):
    text = tmp_path / "notes.txt"
    text.write_text("not a page")
    assert "couldn't open notes.txt" in page_reader.read_document(Settings(), text, model=OVIS_KEY).error
    nothing = page_reader.read_document(Settings(), SAMPLE_PDF)  # no model server
    assert nothing.model == "" and "Nothing answered" in nothing.error and nothing.page_count == 2
    assert pages_read.calls == []


def test_read_document_doesnt_read_when_lm_studio_cant_load_the_reader(serve, posts, pages_read):
    serve(lm_studio(v1_model(OVIS_KEY)))
    posts.fail = "Not enough memory"
    reading = page_reader.read_document(Settings(), SAMPLE_PDF, model=OVIS_KEY)
    assert "couldn't load OvisOCR2" in reading.error and reading.pages == [] and pages_read.calls == []


def test_page_seconds_estimate(pages_read):
    assert page_reader.page_seconds_estimate(Settings()) is None
    page_reader._log_timing(OVIS_KEY, 100)
    page_reader._log_timing(OVIS_KEY, 200)
    page_reader._log_timing("qwen3.5-9b", 600)
    assert page_reader.page_seconds_estimate(Settings(), model=OVIS_KEY) == 150.0
    assert page_reader.page_seconds_estimate(reader_settings(model="qwen3.5-9b")) == 600.0
    assert page_reader.page_seconds_estimate(Settings()) == 300.0
    assert page_reader.page_seconds_estimate(Settings(), model="gemma-3-12b") is None
    page_reader.read_document(Settings(), SAMPLE_PDF, model="gemma-3-12b")
    assert page_reader.page_seconds_estimate(Settings(), model="gemma-3-12b") >= 0  # the reads just made
    assert page_reader.duration(40) == "about 40 seconds" and page_reader.duration(185) == "about 3 minutes"
    assert page_reader.duration(4800) == "about 1 hour 20 minutes"


# --- The test invoice ------------------------------------------------------------------------------------------


def test_the_test_pages_are_a_deterministic_scan():
    pages = page_reader.test_pages(OVIS)
    assert len(pages) == 2  # the totals are on the second page
    assert all(2047 <= _size(png)[1] <= 2048 for png in pages)
    assert page_reader.test_pages(OVIS) == pages


@pytest.fixture
def test_invoice(monkeypatch, pages_read):
    """The test pages stood in for (rendering is tested above), and capture.transcript (built separately) faked:
    it returns what ``found`` holds, as a rule reader's readings."""
    monkeypatch.setattr(page_reader, "test_pages", lambda reader=OVIS: [b"\x89PNG page 1", b"\x89PNG page 2"])
    found = {
        "vendor_name": "Northwind IT Solutions Inc.", "invoice_number": "NW-2026-0912", "invoice_date": "2026-09-14",
        "due_date": "2026-10-14", "po_number": "PO-88213", "gst_hst_registration_number": "123456782 RT0001",
        "subtotal": 15945.0, "hst_amount": 2072.85, "grand_total": 18017.85, "payment_terms": "Net 30",
    }  # fmt: skip
    given = []

    def transcript_fields(pages):
        given.append(list(pages))
        return {name: [Reading(name, value, str(value), [], 0.9, "label")] for name, value in found.items()}

    module = types.ModuleType("ap_coder.capture.transcript")
    module.transcript_fields = transcript_fields
    monkeypatch.setitem(sys.modules, "ap_coder.capture.transcript", module)
    return SimpleNamespace(found=found, given=given)


def test_test_reader_compares_with_the_ground_truth(test_invoice, pages_read):
    result = page_reader.test_reader(Settings(), model=OVIS_KEY)
    assert (result.model, result.ok, result.problem) == (OVIS_KEY, True, "")
    assert (result.fields_right, result.fields_total) == (9, 9)
    assert [row["field"] for row in result.rows] == [
        "vendor_name", "invoice_number", "invoice_date", "due_date", "po_number", "gst_hst_registration_number",
        "subtotal", "tax_total", "grand_total",
    ]  # fmt: skip
    tax = next(row for row in result.rows if row["field"] == "tax_total")
    assert (tax["label"], tax["expected"], tax["read"], tax["match"]) == ("Total tax", 2072.85, 2072.85, True)
    assert test_invoice.given == [["text of page 1", "text of page 2"]] and result.pages == test_invoice.given[0]
    assert result.when and result.seconds >= 0
    # No reading kept (the caller keeps the result), but each page's time is noted, as for any page read, so the
    # next wait shown is this computer's.
    assert [p.name for p in page_reader.cache_dir().iterdir()] == [page_reader.TIMINGS_FILE]
    assert page_reader.page_seconds_estimate(Settings(), model=OVIS_KEY) is not None


def test_test_reader_passes_with_one_field_wrong(test_invoice):
    test_invoice.found["due_date"] = "2026-10-15"
    result = page_reader.test_reader(Settings(), model=OVIS_KEY)
    assert result.ok and (result.fields_right, result.fields_total) == (8, 9)
    assert result.problem == "8 of 9 fields right; Due date was read wrong or not found."


def test_test_reader_fails_on_an_amount_or_too_many_fields(test_invoice):
    test_invoice.found["grand_total"] = 18071.85
    result = page_reader.test_reader(Settings(), model=OVIS_KEY)
    assert not result.ok and "an amount was read wrong or not found (Total)" in result.problem
    test_invoice.found.update(grand_total=18017.85, due_date="2026-10-15", po_number="PO-88218")
    del test_invoice.found["vendor_name"]
    result = page_reader.test_reader(Settings(), model=OVIS_KEY)
    assert not result.ok and result.fields_right == 6
    assert result.problem == "Only 6 of 9 fields right (Supplier, Due date, PO number read wrong or not found)."
    test_invoice.found.pop("hst_amount")
    test_invoice.found["tax_total"] = 2072.85  # a total tax read stands for the tax amounts
    assert next(r for r in page_reader.test_reader(Settings(), model=OVIS_KEY).rows if r["field"] == "tax_total")[
        "match"
    ]


def test_test_reader_reports_a_failed_page(test_invoice, pages_read):
    pages_read.outcomes = ["first page", PageReaderError("no word from the model in 1200 s")]
    result = page_reader.test_reader(Settings(), model=OVIS_KEY)
    assert not result.ok and result.problem == "Page 2 of the test invoice: no word from the model in 1200 s."
    assert result.rows == [] and result.pages == ["first page"]
    nothing = page_reader.test_reader(Settings())  # no model server
    assert not nothing.ok and nothing.model == "" and "Nothing answered" in nothing.problem


# --- Which model does which job ------------------------------------------------------------------------------------


@pytest.fixture
def two_ocr_models(monkeypatch):
    from ap_coder.capture import layout

    monkeypatch.setattr(layout, "ocr_available", lambda: True)
    monkeypatch.setattr(layout, "_engine_name", lambda: "rapidocr")
    monkeypatch.setattr(layout, "second_engine_name", lambda: "ppocrv5")


def test_models_in_use(serve, two_ocr_models):
    serve(lm_studio(v1_model("qwen3.5-9b", loaded=8192), v1_model(OVIS_KEY, loaded=20480)))
    page_reader._log_timing(OVIS_KEY, 185)
    coding, reader, ocr = page_reader.models_in_use(Settings())
    assert [coding["role"], reader["role"], ocr["role"]] == [
        "Suggests GL accounts", "Reads pages (page reader)", "OCR for scans",
    ]  # fmt: skip
    assert all(set(row) == {"role", "model", "state", "status", "note"} for row in (coding, reader, ocr))
    assert (coding["model"], coding["state"], coding["note"]) == ("qwen3.5-9b", "on", "")
    assert (reader["model"], reader["state"], reader["note"]) == ("OvisOCR2", "on", "")
    assert (
        reader["status"]
        == "Loaded. Reads each scan and photo in the background. About 3 minutes a page on this computer."
    )
    assert (ocr["model"], ocr["state"]) == ("PP-OCRv4 + PP-OCRv5", "on")
    asked = page_reader.models_in_use(reader_settings(mode="ask", scope="all"))[1]
    assert asked["status"].startswith("Loaded. Reads pages when AP asks.")
    assert page_reader.models_in_use(reader_settings(mode="off"))[1]["state"] == "off"


def test_models_in_use_with_a_general_model_or_none(serve, two_ocr_models, monkeypatch):
    serve(lm_studio(v1_model("qwen3.5-9b", loaded=8192)))
    reader = page_reader.models_in_use(Settings())[1]
    assert (reader["model"], reader["state"]) == ("qwen3.5-9b", "fallback") and NOT_DOWNLOADED in reader["note"]
    serve(lm_studio(v1_model(OVIS_KEY, loaded=20480)))  # OvisOCR2 alone: never taken for the chat model
    coding = page_reader.models_in_use(Settings())[0]
    assert coding["model"] != OVIS_KEY
    serve({})
    from ap_coder.capture import layout

    monkeypatch.setattr(layout, "ocr_available", lambda: False)
    coding, reader, ocr = page_reader.models_in_use(Settings())
    assert (coding["state"], reader["state"], ocr["state"]) == ("fallback", "off", "off")
    assert "Nothing answered" in reader["note"] and "OCR alone" in reader["status"]
    assert "offline bundle" in ocr["note"]
    coding = page_reader.models_in_use(replace(Settings(), llm=replace(Settings().llm, provider="off")))[0]
    assert coding["state"] == "fallback" and "turned off" in coding["status"]


# --- What LM Studio has, and loading a model ---------------------------------------------------------------------


def test_lm_studio_models_says_what_is_loaded_and_what_each_does(serve, monkeypatch):
    serve(lm_studio(v1_model(OVIS_KEY, loaded=20480), v1_model("qwen3.5-9b", loaded=8192),
                    v1_model("gemma-3-4b", vision=True)))  # fmt: skip
    monkeypatch.setenv("AP_LLM_PROVIDER", "local")
    rows = page_reader.lm_studio_models(Settings.from_env())
    by = {r["model"]: r for r in rows}
    assert by[OVIS_KEY]["loaded"] and by[OVIS_KEY]["context"] == 20480 and by[OVIS_KEY]["document_reader"]
    assert by[OVIS_KEY]["used_for"] == "reads pages"
    assert by["qwen3.5-9b"]["used_for"] == "suggests GL accounts"
    assert not by["gemma-3-4b"]["loaded"] and by["gemma-3-4b"]["used_for"] == ""
    assert rows[-1]["model"] == "gemma-3-4b"  # loaded ones first
    serve({})
    assert page_reader.lm_studio_models(Settings()) is None  # LM Studio not answering


def test_load_a_chat_model_from_settings(serve, posts):
    serve(lm_studio(v1_model("qwen2.5-7b-instruct", vision=False), v1_model(OVIS_KEY)))
    assert page_reader.load_model(Settings(), "qwen2.5-7b-instruct") == ""
    assert posts[-1] == ("/api/v1/models/load", {"model": "qwen2.5-7b-instruct", "context_length": 8192})
    assert page_reader.load_model(Settings(), OVIS_KEY) == ""  # the page reader: with the context a page needs
    assert posts[-1] == ("/api/v1/models/load", {"model": OVIS_KEY, "context_length": 20480})
    assert "doesn't have" in page_reader.load_model(Settings(), "missing-model")


# --- The second review's findings (page reader read for real on a laptop) -----------------------------------------


def test_an_oversized_picture_is_named_plainly(tmp_path, monkeypatch):
    """A 225-megapixel PNG tripped Pillow's guard against decompression bombs and was reported as "it isn't a PDF or a
    picture" (DecompressionBombError underneath)."""
    huge = _image(tmp_path, "huge.png", (1000, 700))
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 200_000)  # 0.7 megapixels is more than twice this
    with pytest.raises(PageReaderError, match=r"^the picture is too large \(0\.7 megapixels\)") as raised:
        page_reader.render_pages(huge, OVIS, 1)
    assert "DecompressionBomb" not in str(raised.value)
    reading = page_reader.read_document(Settings(), huge, model=OVIS_KEY)
    assert reading.error == "couldn't open huge.png: the picture is too large (0.7 megapixels): scan or save it at a " \
        "lower resolution" and not reading.temporary  # fmt: skip


def test_a_large_picture_is_brought_down_to_the_readers_size(tmp_path):
    photo = _image(tmp_path, "large.jpg", (6000, 4500))  # a 27-megapixel photo: decoded small, then resized
    assert _size(page_reader.render_pages(photo, OVIS, 1)[0]) == (2048, 1536)
    scan = _image(tmp_path, "scan.png", (6800, 8800), dpi=(800, 800), mode="L")  # 8.5 x 11 inches at 800 DPI
    width, height = _size(page_reader.render_pages(scan, GENERAL, 1)[0])
    assert (width, height) == (1236, 1600)  # the long side capped (150 DPI would be 1,650)


def test_a_refusal_that_isnt_about_the_thinking_switch_keeps_it(client):
    """LM Studio answers 400 when it can't load the model: that is no refusal of the thinking switch, which was kept
    being sent; and its words are shown whole enough to make sense, not cut at 200 characters mid-word."""
    said = ('Failed to load model "qwen2.5-7b-instruct". Error: Model loading was stopped due to insufficient system '
            "resources. Under the current settings, this model requires approximately 5.62 GB of memory, and "
            "continuing to load it would likely overload your system and cause it to freeze. If you believe this is "
            "a mistake, you can try to change the model loading guardrails in the settings.")  # fmt: skip
    error = StatusError(400, f"Error code: 400 - {{'error': {{'message': '{said}'}}}}")
    error.body = {"message": said}
    fake = client(error)
    with pytest.raises(PageReaderError) as raised:
        page_reader.transcribe(Settings(), b"png", model=OVIS_KEY)
    assert len(fake.calls) == 1  # not asked again without the switch
    assert local_llm.thinking_off(BASE, OVIS_KEY) == {"extra_body": THINKING_OFF}  # still sent next time
    message = str(raised.value)
    assert message.startswith("the model server answered 400: not enough memory to load it (Failed to load model")
    assert message.endswith("requires approximately 5.62 GB of memory, and continuing to load it would likely "
                            "overload your system and cause it to freeze.)")  # fmt: skip
    assert raised.value.temporary  # the server's trouble: the invoice is read again later


def test_gist_keeps_whole_sentences_or_whole_words():
    assert page_reader.gist("Model not found.") == "Model not found."
    long = "word " * 100
    assert page_reader.gist(long).endswith("word…") and len(page_reader.gist(long)) <= 301
    sentences = "First sentence is here. " * 20
    assert page_reader.gist(sentences).endswith("here.") and len(page_reader.gist(sentences)) <= 300
    assert page_reader.gist("Insufficient system resources") == (
        "not enough memory to load it (Insufficient system resources)"
    )


def test_a_server_failure_is_temporary_a_page_problem_is_not(pages_read):
    pages_read.outcomes = [PageReaderError("the model server isn't answering", temporary=True)]
    down = page_reader.read_document(Settings(), SAMPLE_PDF, model=OVIS_KEY)
    assert down.temporary and not down.complete and "isn't answering" in down.error
    pages_read.outcomes = [CutOff("stopped at the 12,288-token limit"), "second page"]
    cut = page_reader.read_document(Settings(), SAMPLE_PDF, model=OVIS_KEY)
    assert not cut.temporary and not cut.complete  # the page's own trouble: reading it again won't help


def test_server_errors_are_temporary(client):
    client(ConnectionRefusedError("refused"))
    with pytest.raises(PageReaderError) as raised:
        page_reader.transcribe(Settings(), b"png", model=OVIS_KEY)
    assert raised.value.temporary
    client(stream(PAGE, fail_after=2))  # the model unloaded part way
    with pytest.raises(PageReaderError) as raised:
        page_reader.transcribe(Settings(), b"png", model=OVIS_KEY)
    assert raised.value.temporary
    client(stream("Northwind IT Solutions Inc.", finish="length"))
    with pytest.raises(CutOff) as raised:
        page_reader.transcribe(Settings(), b"png", model=OVIS_KEY)
    assert not raised.value.temporary


def test_blank_pages_are_not_shown_to_the_model(pages_read):
    """OvisOCR2 wrote "The quick brown fox jumps over the lazy dog." for a blank first page: a page OCR found no
    words on isn't shown to it, and reads as blank."""
    reading = page_reader.read_document(Settings(), SAMPLE_PDF, model=OVIS_KEY, blank_pages={1})
    assert reading.pages == ["", "text of page 1"] and reading.complete  # only page 2 was shown to the model
    assert len(pages_read.calls) == 1
    pages_read.outcomes = ["The quick brown fox jumps over the lazy dog."]
    again = page_reader.read_document(Settings(), SAMPLE_PDF, model="qwen3.5-9b")  # read before it was known blank
    cached = page_reader.read_document(Settings(), SAMPLE_PDF, model="qwen3.5-9b", blank_pages={1})
    assert again.pages[0].startswith("The quick") and cached.pages[0] == "" and cached.cached


def test_the_test_notes_its_page_times(test_invoice, pages_read):
    """Before any page was read here, the wait is a laptop's; the test's own pages then teach the estimate."""
    assert page_reader.page_seconds_estimate(Settings(), model=OVIS_KEY) is None
    page_reader.test_reader(Settings(), model=OVIS_KEY)
    lines = (page_reader.cache_dir() / page_reader.TIMINGS_FILE).read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["model"] for line in lines] == [OVIS_KEY, OVIS_KEY]
