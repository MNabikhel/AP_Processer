"""Put the PP-OCRv5 OCR models where RapidOCR 3 looks for them, so scans are read with two OCR engines.

AP Coder is an offline build: this copies the models from the ``models/`` folder of the offline bundle and
never downloads them. RapidOCR 3 itself is never allowed to download them either (``ap_coder/offline.py``).
Safe to run again: files already there (and intact) are kept.

    python scripts/fetch_models.py            # copy what is missing from the bundle's models/ folder
    python scripts/fetch_models.py --check    # only report; exit 1 if something is missing
    python scripts/fetch_models.py --to DIR   # put them in DIR instead (the offline bundle uses this)

Downloading from the internet (www.modelscope.cn) happens only with the developers' / bundle builder's
opt-in, ``AP_ALLOW_INTERNET=1`` (scripts/build_offline_bundle.py sets it itself).

rapidocr-onnxruntime (the PP-OCRv4 engine) carries its models inside its package: nothing to copy.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ap_coder import offline  # noqa: E402

BUNDLE_MODELS = ROOT / "models"  # the offline bundle's copy (scripts/build_offline_bundle.py)
TIMEOUT = 60


@dataclass
class Result:
    kept: list[str] = field(default_factory=list)
    copied: list[str] = field(default_factory=list)
    downloaded: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failed


def intact(path: Path, model: offline.ModelFile) -> bool:
    if not path.is_file() or path.stat().st_size == 0:
        return False
    return not model.sha256 or offline.sha256(path) == model.sha256


def download(url: str, dest: Path) -> None:
    """Fetch ``url`` into ``dest`` (through HTTPS_PROXY when set, as urllib does). Only with the opt-in."""
    if not offline.internet_allowed():
        raise PermissionError(f"offline build: no download without {offline.INTERNET_ENV}=1")
    with urllib.request.urlopen(url, timeout=TIMEOUT) as response, open(dest, "wb") as out:  # noqa: S310
        shutil.copyfileobj(response, out, 1 << 20)


def fetch(target: Path, source: Path | None = None, *, online: bool | None = None,
          get: Callable[[str, Path], None] = download, say: Callable[[str], None] = print) -> Result:  # fmt: skip
    """Make ``target`` hold every PP-OCRv5 model file, intact: keep what is there, copy from ``source``
    (a folder of model files) what it has, and download the rest only when ``online`` (default: only with
    the ``AP_ALLOW_INTERNET=1`` opt-in)."""
    online = offline.internet_allowed() if online is None else online
    result = Result()
    target.mkdir(parents=True, exist_ok=True)
    for model in offline.ppocrv5_files():
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
            say(f"  could not get {model.name}: {type(exc).__name__}: {exc}")
            result.failed.append(model.name)
        finally:
            part.unlink(missing_ok=True)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Put the PP-OCRv5 OCR models in place, from the offline bundle.")
    parser.add_argument("--check", action="store_true", help="only report what is missing")
    parser.add_argument("--to", help="put the models in this folder (default: RapidOCR's own models folder)")
    parser.add_argument("--from", dest="source", help=f"copy from this folder (default: {BUNDLE_MODELS})")
    parser.add_argument("--offline", action="store_true", help="never download, even with AP_ALLOW_INTERNET=1")
    args = parser.parse_args(argv)

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
        print("Scans are still read, by one OCR engine. Run this again when the download works.")
    else:
        where = f"{source} does not have them intact" if source else f"there is no {BUNDLE_MODELS} folder"
        print(f"This is an offline build: the OCR models come only from the offline bundle, and {where}.")
        print("Scans are still read, by one OCR engine. Unzip the full offline bundle over this folder, then run this "
              f"again (developers online: {offline.INTERNET_ENV}=1 downloads them).")  # fmt: skip
    return 1


if __name__ == "__main__":
    sys.exit(main())
