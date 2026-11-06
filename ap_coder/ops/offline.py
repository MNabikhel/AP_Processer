"""Offline readiness: the local OCR models, and checks that the app keeps everything on this computer.

AP Coder is an offline build: nothing it runs connects to the internet unless a developer or the bundle
builder opts in with ``AP_ALLOW_INTERNET=1`` (:func:`internet_allowed`). The setup scripts check it before
any package, model, git or Python download, and the model server's address (``local_llm.refuse_internet``): LM
Studio on this computer or the office network, never a server on the internet. The exceptions are the optional
Azure check (``azure_check``), which contacts its configured endpoint only once AP has switched it on in Settings, and
a named internal system an Admin enables: JD Edwards' AIS Server (``erp/jde_ais.py``, D-104), only its saved address
on the company network.
``tests/offline/test_offline_guard.py`` proves it: every flow runs with any connection but to this computer refused.

RapidOCR 3 runs both OCR models: PP-OCRv4, the first read, whose model files come inside its package, and PP-OCRv5,
the second read, whose files come in the ``models/`` folder of the offline bundle (D-163). It would download a model
file into its own package folder the first time it is used, if the file were not there. AP Coder never lets it:
``scripts/fetch_models.py`` copies the files from the bundle's ``models/`` folder, the capture code asks
:func:`ppocrv4_ready` / :func:`ppocrv5_ready` (every file of that model there, with the right SHA-256) before using
an engine and builds it with those files' paths (:func:`ppocrv4_engine_params`, :func:`ppocrv5_engine_params`), so
RapidOCR's own downloader never runs. With one model's files missing, the other model reads alone.

:func:`offline_checks` adds the matching lines to ``python -m ap_coder doctor``.
"""

from __future__ import annotations

import functools
import hashlib
import importlib.util
import ipaddress
import logging
import os
import socket
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

PASS, WARN, FAIL, SKIP = "PASS", "WARN", "FAIL", "SKIP"  # the same words as doctor.py

# The models the two engines of capture/layout.py use (onnxruntime, mobile det + rec, the default angle classifier), as
# RapidOCR 3.4 lists them. Used only when RapidOCR's own list cannot be read. The PP-OCRv4 ones come inside its package:
# the very files rapidocr-onnxruntime 1.4.4 carried (D-163).
_PPOCRV4_FALLBACK = (
    ("ch_PP-OCRv4_det_infer.onnx", "onnx/PP-OCRv4/det/ch_PP-OCRv4_det_infer.onnx",
     "d2a7720d45a54257208b1e13e36a8479894cb74155a5efe29462512d42f49da9"),
    ("ch_ppocr_mobile_v2.0_cls_infer.onnx", "onnx/PP-OCRv4/cls/ch_ppocr_mobile_v2.0_cls_infer.onnx",
     "e47acedf663230f8863ff1ab0e64dd2d82b838fceb5957146dab185a89d6215c"),
    ("ch_PP-OCRv4_rec_infer.onnx", "onnx/PP-OCRv4/rec/ch_PP-OCRv4_rec_infer.onnx",
     "48fc40f24f6d2a207a2b1091d3437eb3cc3eb6b676dc3ef9c37384005483683b"),
)  # fmt: skip
_PPOCRV5_FALLBACK = (
    ("ch_PP-OCRv5_mobile_det.onnx", "onnx/PP-OCRv5/det/ch_PP-OCRv5_mobile_det.onnx",
     "4d97c44a20d30a81aad087d6a396b08f786c4635742afc391f6621f5c6ae78ae"),
    ("ch_ppocr_mobile_v2.0_cls_infer.onnx", "onnx/PP-OCRv4/cls/ch_ppocr_mobile_v2.0_cls_infer.onnx",
     "e47acedf663230f8863ff1ab0e64dd2d82b838fceb5957146dab185a89d6215c"),
    ("ch_PP-OCRv5_rec_mobile_infer.onnx", "onnx/PP-OCRv5/rec/ch_PP-OCRv5_rec_mobile_infer.onnx",
     "5825fc7ebf84ae7a412be049820b4d86d77620f204a041697b0494669b1742c5"),
)  # fmt: skip
_FALLBACK_BASE = "https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.4.0/"

# The table finder (capture/table_finder.py): RapidAI's YOLOv8n "general6" page layout model (RapidLayout, Apache-2.0),
# 12 MB. Like the OCR models it comes in the offline bundle's models/ folder and is never downloaded by the app.
TABLE_FINDER_NAME = "yolov8n_layout_general6.onnx"
TABLE_FINDER_URL = "https://www.modelscope.cn/models/RapidAI/RapidLayout/resolve/v1.2.0/onnx/360/" + TABLE_FINDER_NAME
TABLE_FINDER_SHA256 = "927b6edcb268e896e6a170f7d78980591b408e04b3908f54d58eb69efd018c95"
TABLE_FINDER_ENV = "AP_TABLE_FINDER_MODEL"  # a path to the model file elsewhere (still checked against its SHA-256)

# The one opt-in to the internet, for developers and the bundle builder (scripts/build_offline_bundle.py):
# AP_ALLOW_INTERNET=1 lets the setup download packages from PyPI, the OCR models, git updates and Python.
INTERNET_ENV = "AP_ALLOW_INTERNET"

# Environment variable that lets the dashboard listen on the office network (default: this computer only).
ADDRESS_ENV = "AP_DASHBOARD_ADDRESS"
LOCAL_ADDRESS = "127.0.0.1"
# Server mode (D-97): AP Coder on a company server, people log in with their own account and role; off on a laptop.
SERVER_ENV = "AP_SERVER_MODE"


def internet_allowed() -> bool:
    """The setup may use the internet: only when ``AP_ALLOW_INTERNET`` is 1 / true / yes / on."""
    return (os.environ.get(INTERNET_ENV) or "").strip().lower() in ("1", "true", "yes", "on")


def allow_internet() -> None:
    """Opt in for this process and the ones it starts (the bundle builder, scripts/build_offline_bundle.py)."""
    os.environ[INTERNET_ENV] = "1"


# Azure Document Intelligence (an optional check of the local reading, switched on in the settings) and Azure
# OpenAI's sign-in: never needed to install or start AP Coder. Their packages are the optional ``azure`` extra
# (pyproject): the offline bundle carries its wheels and the launcher installs them from there when it can, but a
# computer without them runs as before. Only code that is switched on to use Azure needs them (import_azure).
AZURE_UNAVAILABLE = "Azure check is off or its package isn't installed"


def import_azure(module: str):
    """``module`` (e.g. ``azure.identity``), or a plain RuntimeError when the Azure packages are not installed."""
    import importlib

    try:
        return importlib.import_module(module)
    except ImportError:
        raise RuntimeError(
            f"{AZURE_UNAVAILABLE} ({module} is missing). Invoices are still read and coded on this computer; the "
            "offline bundle's wheelhouse carries the Azure packages (double-click APProcessor to install them)."
        ) from None


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
    """RapidOCR 3 and ONNX Runtime, which run both OCR models."""
    return _package_dir("rapidocr") is not None and _package_dir("onnxruntime") is not None


def ppocrv4_installed() -> bool:
    """RapidOCR 3 with its PP-OCRv4 model files, which come inside its package (looked at, not hashed)."""
    return ppocrv5_installed() and not missing_models(models=ppocrv4_files())


def model_dir() -> Path | None:
    """Where RapidOCR 3 looks for (and downloads) its models: the ``models`` folder of its package."""
    folder = _package_dir("rapidocr")
    return folder / "models" if folder else None


def ppocrv4_params() -> dict:
    """The settings capture/layout.py gives RapidOCR 3 for PP-OCRv4: the models and settings rapidocr-onnxruntime 1.4.4
    read with (D-163)."""
    from rapidocr import ModelType, OCRVersion

    return {"Det.ocr_version": OCRVersion.PPOCRV4, "Rec.ocr_version": OCRVersion.PPOCRV4,
            "Det.model_type": ModelType.MOBILE, "Rec.model_type": ModelType.MOBILE,
            "Global.log_level": "error"}  # fmt: skip


def ppocrv5_params() -> dict:
    """The settings capture/layout.py gives RapidOCR 3 for PP-OCRv5 (a test keeps the two the same)."""
    from rapidocr import ModelType, OCRVersion

    return {"Det.ocr_version": OCRVersion.PPOCRV5, "Rec.ocr_version": OCRVersion.PPOCRV5,
            "Det.model_type": ModelType.MOBILE, "Rec.model_type": ModelType.MOBILE,
            "Global.log_level": "error"}  # fmt: skip


@functools.lru_cache(maxsize=1)
def ppocrv4_files() -> tuple[ModelFile, ...]:
    """The model files RapidOCR 3 needs for the PP-OCRv4 engine (as :func:`ppocrv5_files`)."""
    return _model_files(ppocrv4_params, _PPOCRV4_FALLBACK)


@functools.lru_cache(maxsize=1)
def ppocrv5_files() -> tuple[ModelFile, ...]:
    """The model files RapidOCR 3 needs for the PP-OCRv5 engine, from its own model list when it can be
    read (so a newer RapidOCR is followed), else the list above."""
    return _model_files(ppocrv5_params, _PPOCRV5_FALLBACK)


def ocr_files() -> tuple[ModelFile, ...]:
    """Every model file of the two OCR engines, once (they share the angle classifier)."""
    return tuple({f.name: f for f in (*ppocrv4_files(), *ppocrv5_files())}.values())


def _model_files(params: Callable[[], dict], fallback: tuple[tuple[str, str, str], ...]) -> tuple[ModelFile, ...]:
    try:
        from rapidocr.inference_engine.base import FileInfo, InferSession
        from rapidocr.main import DEFAULT_CFG_PATH
        from rapidocr.utils.parse_parameters import ParseParams

        cfg = ParseParams.update_batch(ParseParams.load(DEFAULT_CFG_PATH), params())
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
    return tuple(ModelFile(name, _FALLBACK_BASE + path, sha) for name, path, sha in fallback)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


_checked: dict[tuple[str, int, int], str] = {}  # (path, size, mtime) -> SHA-256: each file version read once


def _sha256_once(path: Path, stat: os.stat_result) -> str:
    key = (str(path), stat.st_size, stat.st_mtime_ns)
    if key not in _checked:
        _checked[key] = sha256(path)
    return _checked[key]


def missing_models(folder: Path | None = None, verify: bool = False,
                   models: tuple[ModelFile, ...] | None = None) -> list[ModelFile]:  # fmt: skip
    """The OCR model files of ``models`` (default: both engines', :func:`ocr_files`) not in ``folder`` (default:
    RapidOCR's own); ``verify`` also checks each file's SHA-256 (once per file version), so a half-copied or damaged
    file counts as missing."""
    folder = folder or model_dir()
    models = ocr_files() if models is None else models
    if folder is None:
        return list(models)
    out = []
    for f in models:
        path = folder / f.name
        try:
            stat = path.stat()
        except OSError:
            out.append(f)
            continue
        if not path.is_file() or stat.st_size == 0 or (verify and f.sha256 and _sha256_once(path, stat) != f.sha256):
            out.append(f)
    return out


def ppocrv4_engine_params() -> dict:
    """:func:`ppocrv4_params` plus the path of each model file on disk (as :func:`ppocrv5_engine_params`)."""
    return _engine_params(ppocrv4_params(), ppocrv4_files())


def ppocrv5_engine_params() -> dict:
    """:func:`ppocrv5_params` plus the path of each model file on disk. RapidOCR downloads a model only when
    it is given no path (or the file at its default path fails the checksum); given the paths, it never does."""
    return _engine_params(ppocrv5_params(), ppocrv5_files())


def _engine_params(params: dict, models: tuple[ModelFile, ...]) -> dict:
    folder = model_dir()
    if folder is not None:
        for task, model in zip(("Det", "Cls", "Rec"), models, strict=False):
            params[f"{task}.model_path"] = str(folder / model.name)
    try:
        threads = int(os.environ.get("AP_OCR_THREADS") or 0)
    except ValueError:
        threads = 0
    if threads > 0:  # a capture process of the company server: its share of the cores, not all of them (D-168)
        params["EngineConfig.onnxruntime.intra_op_num_threads"] = threads
        params["EngineConfig.onnxruntime.inter_op_num_threads"] = 1
    return params


_warned: set[str] = set()  # the models whose missing files were logged (once each)


def ppocrv4_ready() -> bool:
    """RapidOCR 3 is installed and its PP-OCRv4 models (inside its package) are on disk, each with the right size and
    SHA-256, so using it needs no download (as :func:`ppocrv5_ready`)."""
    return _ready("PP-OCRv4", ppocrv4_files())


def ppocrv5_ready() -> bool:
    """RapidOCR 3 is installed and its PP-OCRv5 models are on disk, each with the right size and SHA-256, so
    using it needs no download (a damaged file would make RapidOCR fetch it again: it counts as missing)."""
    return _ready("PP-OCRv5", ppocrv5_files())


def _ready(name: str, models: tuple[ModelFile, ...]) -> bool:
    if not ppocrv5_installed():
        return False
    missing = missing_models(verify=True, models=models)
    if missing and name not in _warned:
        _warned.add(name)
        log.warning(
            "%s models missing or damaged (%s): scans are read with one OCR engine. They come from the "
            "offline bundle's models/ folder: 'python scripts/fetch_models.py' copies them.",
            name,
            ", ".join(f.name for f in missing),
        )
    return not missing


# --- The table finder's model ------------------------------------------------------------------------------------


def table_finder_file() -> ModelFile:
    return ModelFile(TABLE_FINDER_NAME, TABLE_FINDER_URL, TABLE_FINDER_SHA256)


def bundle_models() -> tuple[ModelFile, ...]:
    """Every model file the offline bundle carries in its models/ folder: the OCR models and the table finder."""
    return (*ocr_files(), table_finder_file())


def table_finder_dirs() -> list[Path]:
    """Where the table finder's model is looked for: the offline bundle's models/ folder (unzipped with the app), then
    the data folder's models/ folder."""
    from ..core.paths import PROJECT_DIR, private_dir

    return [PROJECT_DIR / "models", private_dir() / "models"]


def from_bundle() -> bool:
    """Whether this copy was unzipped from the offline bundle: its models/ folder is next to the app (a copy set up
    from the code alone has none, and no table finder model unless one is copied in)."""
    from ..core.paths import PROJECT_DIR

    return (PROJECT_DIR / "models").is_dir()


def table_finder_path() -> Path | None:
    """The table finder's model on this computer, intact (its SHA-256 checked once per file version), or None: the
    path in ``AP_TABLE_FINDER_MODEL``, else the first of :func:`table_finder_dirs` that has it. Never downloaded."""
    from ..core.paths import env_path

    chosen = env_path(TABLE_FINDER_ENV)
    candidates = [chosen] if chosen else [folder / TABLE_FINDER_NAME for folder in table_finder_dirs()]
    for path in candidates:
        try:
            stat = path.stat()
        except OSError:
            continue
        if path.is_file() and stat.st_size and _sha256_once(path, stat) == TABLE_FINDER_SHA256:
            return path
    return None


def onnxruntime_installed() -> bool:
    return _package_dir("onnxruntime") is not None and _package_dir("numpy") is not None


def quiet_onnxruntime() -> None:
    """Switch off ONNX Runtime's usage events (on Windows it writes them to the system's event tracing, which Windows
    may send to Microsoft with its diagnostic data) before the OCR engines and the table finder make their sessions.
    It never connects anywhere itself; this keeps the OCR's usage on this computer too. Never fails."""
    try:
        import onnxruntime

        onnxruntime.disable_telemetry_events()
    except Exception:  # noqa: BLE001 - not installed, or an ONNX Runtime without the switch
        pass


# --- Dashboard network settings ---------------------------------------------------------------------------------


def server_mode() -> bool:
    """AP Coder runs as the company's server (``AP_SERVER_MODE`` 1 / true / yes / on, or ``serve --server``):
    every page needs a login, and what each person may do comes from their role (D-97). Off by default."""
    return (os.environ.get(SERVER_ENV) or "").strip().lower() in ("1", "true", "yes", "on")


def setup_contact(text: str) -> str:
    """``text`` with who to ask about the setup: the pilot tester on a laptop, an Admin on the company server, where
    the setup is an Admin's (D-98, D-102)."""
    if not server_mode():
        return text
    return text.replace("the pilot tester", "an Admin").replace("The pilot tester", "An Admin")


def dashboard_address() -> str:
    """The address the dashboard listens on: this computer only, unless ``AP_DASHBOARD_ADDRESS`` says
    otherwise (e.g. ``0.0.0.0`` to let colleagues on the office network open it)."""
    return (os.environ.get(ADDRESS_ENV) or "").strip() or LOCAL_ADDRESS


DEFAULT_PORT = 8501


def listening(port: int, host: str = "127.0.0.1") -> bool:
    """Something accepts connections on ``port`` here. A short timeout: on Windows a refused connection to a closed
    port is retried for about a second, a listening one answers at once."""
    with socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        try:
            return s.connect_ex((host, port)) == 0
        except OSError:
            return False


def free_port(start: int = DEFAULT_PORT, address: str | None = None, *, windows: bool | None = None,
              answers: Callable[[int], bool] | None = None) -> int:  # fmt: skip
    """The first port from ``start`` the dashboard can listen on at ``address`` (default ``dashboard_address``): a
    bind test, as the server binds (instant, unlike connecting), then nothing must answer there yet (``answers``,
    default ``listening``: on a Mac SO_REUSEADDR lets 127.0.0.1 be bound on a port another program listens on for
    every address). When none of the 50 from ``start`` can be bound (Windows reserves whole blocks of ports for
    Hyper-V and WSL), one the system says is free: never the busy one asked for. Used by the launcher
    (``scripts/launch.py``) and by ``python -m ap_coder serve``."""
    address = address or dashboard_address()
    windows = os.name == "nt" if windows is None else windows
    answers = answers or listening
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    for port in range(start, start + 50):
        with socket.socket(family, socket.SOCK_STREAM) as s:
            if not windows:  # like the dashboard's server: a port left in TIME_WAIT is free
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            elif hasattr(socket, "SO_EXCLUSIVEADDRUSE"):  # Windows lets 127.0.0.1 be bound on a port in use without it
                s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            try:
                s.bind((address, port))
            except OSError:
                continue
        if not answers(port):  # something answering there is never picked (0.3 s at most on Windows)
            return port
    with socket.socket(family, socket.SOCK_STREAM) as s:
        s.bind((address, 0))
        return s.getsockname()[1]


def browser_wanted() -> bool:
    """Whether to open the dashboard in a browser: not with ``BROWSER=none``, nor on a Linux computer with no desktop
    (over SSH, a server: Python's webbrowser would start a text browser such as lynx in the very window AP Coder
    runs in, and wait for it). Windows and Macs always have a desktop."""
    if (os.environ.get("BROWSER") or "").strip().lower() == "none":
        return False
    if sys.platform.startswith("linux"):
        return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    return True


def is_loopback(address: str) -> bool:
    if address.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(address).is_loopback
    except ValueError:
        return False


# --- doctor -------------------------------------------------------------------------------------------------------


def offline_checks() -> list[tuple[str, str, str]]:
    """(area, status, detail) lines for ``doctor``: OCR engines and models, the data folder, and the
    dashboard's network settings. Reads files only; never connects anywhere."""
    out: list[tuple[str, str, str]] = []
    from_bundle = "run APProcessor.bat (it installs from the offline bundle)"
    usable = []
    for area, models in (("OCR PP-OCRv4", ppocrv4_files()), ("OCR PP-OCRv5", ppocrv5_files())):
        if not ppocrv5_installed():
            out.append((area, WARN, f"RapidOCR 3 not installed: {from_bundle}"))
            continue
        missing = missing_models(verify=True, models=models)  # as _ready() checks them (each hashed once per version)
        usable.append(not missing)
        out.append((area, PASS if not missing else WARN,
                    f"{len(models)} model files present" if not missing else
                    "models missing or damaged (" + ", ".join(f.name for f in missing) + "): scans get one OCR read; "
                    "copy them from the offline bundle: python scripts/fetch_models.py"))  # fmt: skip
    if not any(usable):
        out.append(("OCR", WARN, "no local OCR: scanned invoices and photos can't be read here (text PDFs can)"))
    if table_finder_path() is not None and onnxruntime_installed():
        out.append(("table finder", PASS, f"{TABLE_FINDER_NAME} present"))
    else:
        why = "ONNX Runtime not installed" if not onnxruntime_installed() else f"{TABLE_FINDER_NAME} missing"
        out.append(("table finder", WARN, f"{why}: line items are read without it; it comes with the offline "
                                          "bundle's models folder"))  # fmt: skip

    from ..core.paths import private_dir

    folder = private_dir()
    # Which folder (B-1501): a checkout on a computer where AP Coder was installed uses that install's recorded
    # folder; the home folder shortened to ~, so a pasted setup report shows no Windows user name.
    home = Path.home()
    shown = f"~{os.sep}{folder.relative_to(home)}" if folder.is_relative_to(home) and folder != home else str(folder)
    try:
        folder.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=folder, prefix=".write-test-"):
            pass
        status, detail = PASS, f"writable: {shown}"
        if "onedrive" in str(folder).lower():
            status, detail = WARN, f"writable, but synced by OneDrive (can corrupt the open database): {shown}"
    except OSError as exc:
        status, detail = FAIL, f"not writable ({type(exc).__name__}): {shown}"
    out.append(("data folder", status, detail))

    address = dashboard_address()
    if not is_loopback(address if address not in ("", "localhost") else "127.0.0.1"):
        detail = f"listens on {address} ({ADDRESS_ENV}): others on the network can open it"
        out.append(("dashboard network", WARN, detail))
    else:
        detail = "this computer only; no usage statistics; fonts and icons served locally"
        out.append(("dashboard network", PASS, detail))

    return out  # the local model's status is reported by doctor itself (local_llm.check_server)
