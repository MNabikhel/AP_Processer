"""Put the PP-OCRv5 OCR models where RapidOCR 3 looks for them, so scans are read with two OCR engines, and check
the table finder's model (``capture/table_finder.py``), which AP Coder reads from the bundle's ``models/`` folder.

AP Coder is an offline build: this copies the models from the ``models/`` folder of the offline bundle and
never downloads them. RapidOCR 3 itself is never allowed to download them either (``ap_coder/ops/offline.py``).
Safe to run again: files already there (and intact) are kept.

    python scripts/fetch_models.py            # copy what is missing from the bundle's models/ folder
    python scripts/fetch_models.py --check    # only report; exit 1 if something is missing
    python scripts/fetch_models.py --to DIR   # put them in DIR instead (the offline bundle uses this)
    python scripts/fetch_models.py --table-finder  # developers online: also the table finder, into models/

Downloading from the internet (www.modelscope.cn) happens only with the developers' / bundle builder's
opt-in, ``AP_ALLOW_INTERNET=1`` (scripts/build_offline_bundle.py sets it itself).

rapidocr-onnxruntime (the PP-OCRv4 engine) carries its models inside its package: nothing to copy.
"""

from __future__ import annotations

import argparse
import functools
import os
import shutil
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ap_coder.ops import offline  # noqa: E402

BUNDLE_MODELS = ROOT / "models"  # the offline bundle's copy (scripts/build_offline_bundle.py)
TIMEOUT = 60
# A download that fails on the way (2 of 4 first setups from GitHub's ZIP: "Connection reset by peer") is tried again
# after these waits (seconds): 3 tries in all. A refusal by the server (404, 403) is not tried again.
RETRY_WAITS = (3.0, 10.0)
sleep = time.sleep  # (tests wait for nothing)


@dataclass
class Result:
    kept: list[str] = field(default_factory=list)
    copied: list[str] = field(default_factory=list)
    downloaded: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)  # why each download failed, in plain words (plain_reason)

    @property
    def ok(self) -> bool:
        return not self.failed

    def add(self, other: Result) -> Result:
        for name in ("kept", "copied", "downloaded", "failed", "reasons"):
            getattr(self, name).extend(getattr(other, name))
        return self


def plain_reason(exc: BaseException) -> str:
    """Why a download failed, in a few plain words ("the connection dropped"): urllib's own message
    ("URLError: <urlopen error [Errno 104] Connection reset by peer>") means nothing to the pilot tester."""
    text = f"{exc} {getattr(exc, 'reason', '')}".lower()
    if isinstance(exc, urllib.error.HTTPError):
        return f"the download site answered {exc.code}"
    if isinstance(exc, ConnectionResetError) or "reset by peer" in text or "10054" in text or "errno 104" in text:
        return "the connection dropped"
    if isinstance(exc, TimeoutError) or "timed out" in text:
        return "the connection timed out"
    if any(s in text for s in ("getaddrinfo", "name or service", "nodename nor servname", "no address", "errno -3",
                               "temporary failure in name resolution", "11001")):  # fmt: skip
        return "this computer couldn't find the download site (no internet?)"
    if "refused" in text or "unreachable" in text or "10061" in text:
        return "this computer couldn't reach the download site"
    if "checksum" in text:
        return "the file that came was damaged"
    if "certificate" in text or "ssl" in text:
        return "the download site's certificate couldn't be checked (a company proxy?)"
    return "the download didn't finish"


def _retry(exc: BaseException) -> bool:
    """Worth trying again: the connection, not the server's answer (a 5xx is tried again, a 404 is not)."""
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code >= 500 or exc.code == 429
    return isinstance(exc, (OSError, ValueError, TimeoutError)) and not isinstance(exc, PermissionError)


def intact(path: Path, model: offline.ModelFile) -> bool:
    if not path.is_file() or path.stat().st_size == 0:
        return False
    return not model.sha256 or offline.sha256(path) == model.sha256


def download(url: str, dest: Path, waits: tuple[float, ...] | None = None, timeout: float | None = None) -> None:
    """Fetch ``url`` into ``dest`` (through HTTPS_PROXY when set, as urllib does), tried again after each of
    ``waits`` (default RETRY_WAITS) when the connection fails. Only with the opt-in."""
    if not offline.internet_allowed():
        raise PermissionError(f"offline build: no download without {offline.INTERNET_ENV}=1")
    waits = RETRY_WAITS if waits is None else waits
    for attempt in range(len(waits) + 1):
        try:
            with urllib.request.urlopen(url, timeout=timeout or TIMEOUT) as response, open(dest, "wb") as out:  # noqa: S310
                shutil.copyfileobj(response, out, 1 << 20)
            return
        except Exception as exc:  # noqa: BLE001 - tried again, then raised as it came
            if attempt >= len(waits) or not _retry(exc):
                raise
            sleep(waits[attempt])


def fetch(target: Path, source: Path | None = None, *, online: bool | None = None,
          get: Callable[[str, Path], None] | None = None, say: Callable[[str], None] = print,
          models: tuple[offline.ModelFile, ...] | None = None, log: Callable[[str], None] | None = None,
          waits: tuple[float, ...] | None = None, timeout: float | None = None) -> Result:  # fmt: skip
    """Make ``target`` hold every model file of ``models`` (default: the PP-OCRv5 ones), intact: keep what is there,
    copy from ``source`` (a folder of model files) what it has, and download the rest only when ``online`` (default:
    only with the ``AP_ALLOW_INTERNET=1`` opt-in). ``log``: where the error itself goes (the launcher's log), the
    window getting only the plain reason. ``waits`` and ``timeout``: the download's (:func:`download`)."""
    online = offline.internet_allowed() if online is None else online
    tries = len(RETRY_WAITS if waits is None else waits) + 1 if get is None else 1
    get = get or functools.partial(download, waits=waits, timeout=timeout)
    result = Result()
    target.mkdir(parents=True, exist_ok=True)
    for model in offline.ppocrv5_files() if models is None else models:
        dest = target / model.name
        if intact(dest, model):
            result.kept.append(model.name)
            continue
        part = dest.with_name(dest.name + ".part")
        try:
            if source is not None and intact(source / model.name, model):
                shutil.copyfile(source / model.name, part)
                how = result.copied
            elif online:
                say(f"Downloading {model.name} ...")
                get(model.url, part)
                how = result.downloaded
            else:
                result.failed.append(model.name)
                continue
            if not intact(part, model):
                raise ValueError("the file does not match its checksum")
            os.replace(part, dest)
            how.append(model.name)
        except Exception as exc:  # noqa: BLE001 - report each file and carry on
            reason = plain_reason(exc)
            tried = f", tried {tries} times" if online and tries > 1 and _retry(exc) else ""
            if log is None:  # (run on its own: a developer, who reads the error too)
                say(f"  Couldn't get {model.name}: {reason}{tried} ({type(exc).__name__}: {exc}).")
            else:
                say(f"  Couldn't get {model.name}: {reason}{tried}.")
                log(f"  ({model.name}: {type(exc).__name__}: {exc})")
            result.failed.append(model.name)
            result.reasons.append(reason)
        finally:
            part.unlink(missing_ok=True)
    return result


def setup(*, online: bool, table_finder: bool, first: bool, say: Callable[[str], None],
          log: Callable[[str], None] | None = None) -> Result:  # fmt: skip
    """The launcher's step (scripts/launch.py, ensure_models): the PP-OCRv5 models into RapidOCR's folder, copied from
    the offline bundle's models/ folder, or downloaded when ``online``; with ``table_finder``, the table finder's
    model too (online only, into the data folder's models/ folder). A first setup tries each download 3 times with a
    minute's patience; a later start (trying again after a failed one) twice, more briefly."""
    waits, timeout = (RETRY_WAITS, TIMEOUT) if first else (RETRY_WAITS[:1], 20.0)
    result = Result()
    target = offline.model_dir()
    if target is not None:
        source = BUNDLE_MODELS if BUNDLE_MODELS.is_dir() else None
        result.add(fetch(target, source, online=online, say=say, log=log, waits=waits, timeout=timeout))
    if table_finder and online and offline.table_finder_path() is None:
        folder = offline.table_finder_dirs()[-1]  # the data folder's (a models/ folder in the code is a bundle's)
        result.add(fetch(folder, None, online=True, say=say, log=log, waits=waits, timeout=timeout,
                         models=(offline.table_finder_file(),)))  # fmt: skip
    return result


def came_with_package(name: str) -> bool:
    """The model file ``name`` is part of RapidOCR's own package (its angle classifier is): nothing to download."""
    import importlib.metadata

    try:
        return any(Path(str(f)).name == name for f in importlib.metadata.files("rapidocr") or [])
    except importlib.metadata.PackageNotFoundError:
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Put the PP-OCRv5 OCR models in place, from the offline bundle.")
    parser.add_argument("--check", action="store_true", help="only report what is missing")
    parser.add_argument("--to", help="put the models in this folder (default: RapidOCR's own models folder)")
    parser.add_argument("--from", dest="source", help=f"copy from this folder (default: {BUNDLE_MODELS})")
    parser.add_argument("--offline", action="store_true", help="never download, even with AP_ALLOW_INTERNET=1")
    parser.add_argument("--table-finder", action="store_true",
                        help="also download the table finder (online only)")  # fmt: skip
    args = parser.parse_args(argv)

    table_finder(args)  # reported only: without it line items are read as before
    target = Path(args.to) if args.to else offline.model_dir()
    if target is None:
        print("RapidOCR 3 is not installed: no OCR models to put in place (scans use rapidocr-onnxruntime if present).")
        return 0
    if args.check:
        missing = offline.missing_models(target, verify=True)
        for model in missing:
            print(f"Missing: {model.name}")
        print("OCR models: all present." if not missing else f"OCR models: {len(missing)} missing in {target}")
        return 1 if missing else 0

    source = Path(args.source) if args.source else (BUNDLE_MODELS if BUNDLE_MODELS.is_dir() else None)
    online = offline.internet_allowed() and not args.offline
    result = fetch(target, source, online=online)
    parts = [f"{len(result.kept)} already there"]
    if result.copied:
        parts.append(f"{len(result.copied)} copied from {source}")
    if result.downloaded:
        parts.append(f"{len(result.downloaded)} downloaded")
    if result.ok:
        print("OCR models ready: " + ", ".join(parts) + ".")
        return 0
    print(f"OCR models: {len(result.failed)} missing ({', '.join(result.failed)}).")
    if online:
        print("Scans are still read, by one OCR engine. Run python scripts/fetch_models.py again when the download "
              "works.")  # fmt: skip
    else:
        where = f"{source} does not have them intact" if source else f"there is no {BUNDLE_MODELS} folder"
        print(f"This is an offline build: the OCR models come only from the offline bundle, and {where}.")
        print("Scans are still read, by one OCR engine. Unzip the full offline bundle over this folder, then run this "
              f"again (developers online: {offline.INTERNET_ENV}=1 downloads them).")  # fmt: skip
    return 1


def table_finder(args: argparse.Namespace) -> bool:
    """The table finder's model: AP Coder reads it from the bundle's models/ folder (or the data folder's), so there
    is nothing to copy: it is reported, and downloaded only when asked (``--table-finder``, online: the launcher's
    setup from the code alone, or a developer with the opt-in), into the data folder's models/ folder (a models/
    folder in the code would make it look like an offline bundle). True when it is in place, intact."""
    name = offline.TABLE_FINDER_NAME
    if offline.table_finder_path() is not None:
        print(f"Table finder: {name} present.")
        return True
    online = args.table_finder and offline.internet_allowed() and not args.offline and not args.check
    if online:
        folder = offline.table_finder_dirs()[-1]
        result = fetch(folder, None, online=True, models=(offline.table_finder_file(),))
        if result.ok:
            print(f"Table finder: {name} downloaded into {folder}.")
            return True
    print(f"Table finder: {name} missing (it comes with the offline bundle's models folder): line items are read "
          "without it.")  # fmt: skip
    return False


if __name__ == "__main__":
    sys.exit(main())
