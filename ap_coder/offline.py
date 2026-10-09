"""Offline readiness: the local OCR models, and checks that the app keeps everything on this computer.

RapidOCR 3 (the PP-OCRv5 reader) downloads its model files into its own package folder the first time
it is used. A computer without internet cannot do that, so ``scripts/fetch_models.py`` fetches them once
(while online, or from the ``models/`` folder of the offline bundle), and the capture code asks
:func:`ppocrv5_ready` before using that engine: with the models missing it falls back to the other
engine (rapidocr-onnxruntime, whose PP-OCRv4 models come inside its package) instead of trying to
download them.

:func:`offline_checks` adds the matching lines to ``python -m ap_coder doctor``.
"""

from __future__ import annotations

import functools
import hashlib
import importlib.util
import ipaddress
import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

PASS, WARN, FAIL, SKIP = "PASS", "WARN", "FAIL", "SKIP"  # the same words as doctor.py

# The models the PP-OCRv5 engine of capture/layout.py uses (onnxruntime, mobile det + rec, the default
# angle classifier), as RapidOCR 3.4 lists them. Used only when RapidOCR's own list cannot be read.
_PPOCRV5_FALLBACK = (
    ("ch_PP-OCRv5_mobile_det.onnx", "onnx/PP-OCRv5/det/ch_PP-OCRv5_mobile_det.onnx",
     "4d97c44a20d30a81aad087d6a396b08f786c4635742afc391f6621f5c6ae78ae"),
    ("ch_ppocr_mobile_v2.0_cls_infer.onnx", "onnx/PP-OCRv4/cls/ch_ppocr_mobile_v2.0_cls_infer.onnx",
     "e47acedf663230f8863ff1ab0e64dd2d82b838fceb5957146dab185a89d6215c"),
    ("ch_PP-OCRv5_rec_mobile_infer.onnx", "onnx/PP-OCRv5/rec/ch_PP-OCRv5_rec_mobile_infer.onnx",
     "5825fc7ebf84ae7a412be049820b4d86d77620f204a041697b0494669b1742c5"),
)  # fmt: skip
_FALLBACK_BASE = "https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.4.0/"

# Environment variable that lets the dashboard listen on the office network (default: this computer only).
ADDRESS_ENV = "AP_DASHBOARD_ADDRESS"
LOCAL_ADDRESS = "127.0.0.1"


@dataclass(frozen=True)
class ModelFile:
    name: str
    url: str
    sha256: str | None = None


def _package_dir(module: str) -> Path | None:
    """The folder of an installed package, without importing it."""
    try:
        spec = importlib.util.find_spec(module)
    except (ImportError, ValueError):
        return None
    if spec is None or not spec.submodule_search_locations:
        return None
    return Path(next(iter(spec.submodule_search_locations)))


def ppocrv5_installed() -> bool:
    return _package_dir("rapidocr") is not None and _package_dir("onnxruntime") is not None


def ppocrv4_installed() -> bool:
    """rapidocr-onnxruntime, which carries its PP-OCRv4 models inside the package (nothing to download)."""
    folder = _package_dir("rapidocr_onnxruntime")
    return folder is not None and any((folder / "models").glob("*.onnx"))


def model_dir() -> Path | None:
    """Where RapidOCR 3 looks for (and downloads) its models: the ``models`` folder of its package."""
    folder = _package_dir("rapidocr")
    return folder / "models" if folder else None


def ppocrv5_params() -> dict:
    """The settings capture/layout.py gives RapidOCR 3 (a test keeps the two the same)."""
    from rapidocr import ModelType, OCRVersion

    return {"Det.ocr_version": OCRVersion.PPOCRV5, "Rec.ocr_version": OCRVersion.PPOCRV5,
            "Det.model_type": ModelType.MOBILE, "Rec.model_type": ModelType.MOBILE,
            "Global.log_level": "error"}  # fmt: skip


@functools.lru_cache(maxsize=1)
def ppocrv5_files() -> tuple[ModelFile, ...]:
    """The model files RapidOCR 3 needs for the PP-OCRv5 engine, from its own model list when it can be
    read (so a newer RapidOCR is followed), else the list above."""
    try:
        from rapidocr.inference_engine.base import FileInfo, InferSession
        from rapidocr.main import DEFAULT_CFG_PATH
        from rapidocr.utils.parse_parameters import ParseParams

        cfg = ParseParams.update_batch(ParseParams.load(DEFAULT_CFG_PATH), ppocrv5_params())
        files = []
        for task in (cfg.Det, cfg.Cls, cfg.Rec):
            info = InferSession.get_model_url(FileInfo(engine_type=task.engine_type, ocr_version=task.ocr_version,
                                                       task_type=task.task_type, lang_type=task.lang_type,
                                                       model_type=task.model_type))  # fmt: skip
            url = str(info["model_dir"])
            files.append(ModelFile(url.rsplit("/", 1)[-1], url, info.get("SHA256")))
        if files:
            return tuple(files)
    except Exception as exc:  # an older/newer RapidOCR with another layout: use the known list
        log.debug("RapidOCR model list not readable (%s); using the built-in list", exc)
    return tuple(ModelFile(name, _FALLBACK_BASE + path, sha) for name, path, sha in _PPOCRV5_FALLBACK)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def missing_models(folder: Path | None = None, verify: bool = False) -> list[ModelFile]:
    """The PP-OCRv5 model files not in ``folder`` (default: RapidOCR's own); ``verify`` also checks
    each file's SHA-256, so a half-downloaded file counts as missing."""
    folder = folder or model_dir()
    if folder is None:
        return list(ppocrv5_files())
    out = []
    for f in ppocrv5_files():
        path = folder / f.name
        if not path.is_file() or path.stat().st_size == 0 or (verify and f.sha256 and sha256(path) != f.sha256):
            out.append(f)
    return out


_warned = False


def ppocrv5_ready() -> bool:
    """RapidOCR 3 is installed and its PP-OCRv5 models are on disk, so using it needs no download."""
    global _warned
    if not ppocrv5_installed():
        return False
    missing = missing_models()
    if missing and not _warned:
        _warned = True
        log.warning(
            "PP-OCRv5 models missing (%s): scans are read with one OCR engine. "
            "Run 'python scripts/fetch_models.py' once while online.",
            ", ".join(f.name for f in missing),
        )
    return not missing


# --- Dashboard network settings ---------------------------------------------------------------------------------


def dashboard_address() -> str:
    """The address the dashboard listens on: this computer only, unless ``AP_DASHBOARD_ADDRESS`` says
    otherwise (e.g. ``0.0.0.0`` to let colleagues on the office network open it)."""
    return (os.environ.get(ADDRESS_ENV) or "").strip() or LOCAL_ADDRESS


def is_loopback(address: str) -> bool:
    if address.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(address).is_loopback
    except ValueError:
        return False


def streamlit_flags(port: int, address: str | None = None) -> list[str]:
    """The Streamlit settings ``python -m ap_coder dashboard`` starts with: listen locally, no usage
    statistics, no e-mail prompt, assets served from the package (nothing loaded from the internet)."""
    address = address or dashboard_address()
    browser = "localhost" if address in ("0.0.0.0", "::") else address  # the browser opens this address
    return [
        "--server.port", str(port), "--server.address", address, "--browser.serverAddress", browser,
        "--browser.gatherUsageStats", "false", "--client.toolbarMode", "minimal",
        "--server.enableStaticServing", "true", "--server.showEmailPrompt", "false",
    ]  # fmt: skip


def _flag(flags: list[str], name: str) -> str | None:
    return flags[flags.index(name) + 1] if name in flags and flags.index(name) + 1 < len(flags) else None


# --- doctor -------------------------------------------------------------------------------------------------------


def offline_checks() -> list[tuple[str, str, str]]:
    """(area, status, detail) lines for ``doctor``: OCR engines and models, the data folder, and the
    dashboard's network settings. Reads files only; never connects anywhere."""
    out: list[tuple[str, str, str]] = []
    v4, v5 = ppocrv4_installed(), ppocrv5_installed()
    v4_detail = "rapidocr-onnxruntime, models inside the package" if v4 else "not installed: pip install -e .[ocr]"
    out.append(("OCR PP-OCRv4", PASS if v4 else WARN, v4_detail))
    if not v5:
        out.append(("OCR PP-OCRv5", WARN if v4 else SKIP, "RapidOCR 3 not installed: pip install -e .[ocr]"))
    else:
        missing = missing_models()
        out.append(("OCR PP-OCRv5", PASS if not missing else WARN,
                    f"{len(ppocrv5_files())} model files present" if not missing else
                    "models missing (" + ", ".join(f.name for f in missing) + "): scans get one OCR read; "
                    "run python scripts/fetch_models.py once while online"))  # fmt: skip
    if not v4 and not v5:
        out.append(("OCR", WARN, "no local OCR: scanned invoices and photos can't be read here (text PDFs can)"))

    from .paths import private_dir

    folder = private_dir()
    try:
        folder.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=folder, prefix=".write-test-"):
            pass
        status, detail = PASS, "writable"
        if "onedrive" in str(folder).lower():
            status, detail = WARN, "writable, but synced by OneDrive (can corrupt the open database)"
    except OSError as exc:
        status, detail = FAIL, f"not writable ({type(exc).__name__})"
    out.append(("data folder", status, detail))

    flags = streamlit_flags(8501)
    address = _flag(flags, "--server.address") or ""
    stats = _flag(flags, "--browser.gatherUsageStats")
    if not is_loopback(address):
        detail = f"listens on {address} ({ADDRESS_ENV}): others on the network can open it"
        out.append(("dashboard network", WARN, detail))
    elif stats != "false":
        out.append(("dashboard network", FAIL, "Streamlit usage statistics are not switched off"))
    else:
        detail = "this computer only; no usage statistics; fonts and icons served locally"
        out.append(("dashboard network", PASS, detail))

    return out  # the local model's status is reported by doctor itself (local_llm.check_server)
