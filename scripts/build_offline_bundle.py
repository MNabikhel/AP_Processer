"""Build the offline bundle: one ZIP that installs AP Coder on a computer without internet.

Run it on a computer with internet (any OS):

    python scripts/build_offline_bundle.py              # Windows 64-bit + this computer, Python 3.11 and 3.12
    python scripts/build_offline_bundle.py --dry-run    # show what it would do; downloads and writes nothing
    python scripts/build_offline_bundle.py --platform macosx_11_0_arm64   # add Apple silicon Macs

The ZIP (in ``dist/``) holds one ``APProcessor/`` folder:

* the code (the files git tracks, never ``private/`` data or a ``.env``)
* ``wheelhouse/``: every package for ``pip install -e ".[ocr]"`` (and ``.[dev]`` for install.bat), as
  wheels for each platform and Python version asked for, plus pip, setuptools and wheel
* ``models/``: the PP-OCRv5 OCR models (``scripts/fetch_models.py`` copies them into place)
* ``bundle_manifest.json``: what is inside, with the models' SHA-256

On the offline computer: unzip, double-click ``APProcessor.bat`` (or ``APProcessor.command``). Seeing
``wheelhouse/``, the launcher installs with ``pip --no-index --find-links wheelhouse``.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import sysconfig
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from check_deps import tomllib  # noqa: E402  (tomllib, or pip's copy on Python 3.10)

DEFAULT_PLATFORMS = ["win_amd64"]
DEFAULT_PYTHONS = ["3.11", "3.12"]
DEFAULT_EXTRAS = ["ocr", "dev"]
BUILD_TOOLS = ["pip", "setuptools>=68", "wheel"]  # an offline editable install builds with setuptools
TOP = "APProcessor"

# Never in the bundle: data, settings, environments and build output.
EXCLUDE_DIRS = {".git", ".venv", "venv", "wheelhouse", "models", "dist", "build", "__pycache__", ".pytest_cache",
                ".ruff_cache", ".cache", "output", "bench_out", ".claude", ".github"}  # fmt: skip
EXECUTABLE = (".command", ".sh")
DATA_SUFFIXES = (".db", ".sqlite", ".sqlite3", ".xlsx", ".xlsm", ".xls")


def project() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]


def requirements(extras: list[str]) -> list[str]:
    proj = project()
    reqs = list(proj["dependencies"])
    for extra in extras:
        reqs += proj.get("optional-dependencies", {}).get(extra, [])
    return list(dict.fromkeys(reqs + BUILD_TOOLS))


def excluded(rel: Path) -> bool:
    parts = rel.parts
    if any(p in EXCLUDE_DIRS or p.endswith(".egg-info") for p in parts[:-1]):
        return True
    if parts[0] == "private" and rel.as_posix() != "private/README.md":
        return True  # enterprise data, never shipped
    name = parts[-1]
    if name == ".env" or (name.startswith(".env.") and name != ".env.example"):
        return True  # settings with keys
    if rel.as_posix() == ".streamlit/secrets.toml":
        return True
    lower = name.lower()
    if lower.endswith(DATA_SUFFIXES) or ".db-" in lower or ".sqlite" in lower:
        return True  # a database or a workbook left in the code folder (as .gitignore, also without git)
    if len(parts) == 1 and lower.endswith((".pdf", ".tif", ".tiff")):
        return True  # an invoice dropped next to the code (the samples are in samples/)
    return name.endswith((".pyc", ".pyo")) or name == ".DS_Store"


def source_files() -> list[Path]:
    """The code: tracked and new (not git-ignored) files when this is a git checkout, else every file
    outside the excluded folders. Paths relative to the project folder."""
    found: list[Path] = []
    git = shutil.which("git")
    if (ROOT / ".git").exists() and git:
        out = subprocess.run([git, "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=ROOT,
                             capture_output=True, text=True)  # fmt: skip
        if out.returncode == 0:
            found = [Path(p) for p in out.stdout.split("\0") if p]
    if not found:
        found = [p.relative_to(ROOT) for p in ROOT.rglob("*") if p.is_file()]
    return sorted({p for p in found if (ROOT / p).is_file() and not excluded(p)})


def pip_commands(reqs: list[str], wheelhouse: Path, platforms: list[str], pythons: list[str],
                 current: bool = True) -> list[dict]:  # fmt: skip
    """``pip download`` commands ({"target", "cmd"}): this computer's own Python, then each platform and
    Python version (wheels only: nothing can be compiled on the offline computer)."""
    base = [sys.executable, "-m", "pip", "download", "--disable-pip-version-check", "--dest", str(wheelhouse)]
    cmds = []
    if current:
        cmds.append({"target": "this computer", "cmd": [*base, "--prefer-binary", *reqs]})
    for platform in platforms:
        for version in pythons:
            cmd = [*base, "--only-binary=:all:", "--implementation", "cp", "--platform", platform,
                   "--python-version", version, *reqs]  # fmt: skip
            cmds.append({"target": f"{platform}, Python {version}", "cmd": cmd})
    return cmds


def plan(args: argparse.Namespace) -> dict:
    """Everything the build will do, without doing it (the --dry-run output)."""
    out_dir = Path(args.out).resolve()
    stage = out_dir / "offline-bundle" / TOP
    reqs = requirements(args.extras)
    from ap_coder import offline

    return {
        "name": f"{TOP}-offline-{project()['version']}-{dt.date.today():%Y%m%d}.zip",
        "zip": str(out_dir / f"{TOP}-offline-{project()['version']}-{dt.date.today():%Y%m%d}.zip"),
        "stage": str(stage),
        "source_files": [p.as_posix() for p in source_files()],
        "requirements": reqs,
        "targets": (
            ["this computer (" + sysconfig.get_platform() + f", Python {sys.version_info[0]}.{sys.version_info[1]})"]
            if not args.no_current
            else []
        )
        + [f"{p}, Python {v}" for p in args.platform for v in args.python],
        "pip_commands": pip_commands(reqs, stage / "wheelhouse", args.platform, args.python, not args.no_current),
        "models": [] if args.no_models else [{"name": m.name, "sha256": m.sha256} for m in offline.ppocrv5_files()],
    }


def _zip(stage: Path, dest: Path) -> None:
    tmp = dest.with_suffix(".zip.part")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(stage.rglob("*")):
            if not path.is_file():
                continue
            arc = Path(TOP) / path.relative_to(stage)
            info = zipfile.ZipInfo.from_file(path, arc.as_posix())
            mode = 0o755 if path.suffix in EXECUTABLE else 0o644
            info.external_attr = (0o100000 | mode) << 16  # keeps the launchers executable on macOS
            info.compress_type = zipfile.ZIP_STORED if path.suffix in (".whl", ".onnx") else zipfile.ZIP_DEFLATED
            with open(path, "rb") as f:
                zf.writestr(info, f.read())
    os.replace(tmp, dest)


def build(args: argparse.Namespace) -> int:
    p = plan(args)
    stage = Path(p["stage"])
    print(f"Building {p['name']}")
    if stage.exists():  # the code is copied fresh; downloaded wheels are kept and reused
        for item in stage.iterdir():
            if item.name != "wheelhouse":
                shutil.rmtree(item) if item.is_dir() else item.unlink()
    stage.mkdir(parents=True, exist_ok=True)

    print(f"[1/4] code: {len(p['source_files'])} files")
    for rel in p["source_files"]:
        dest = stage / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / rel, dest)

    print(f"[2/4] wheelhouse: {', '.join(p['targets'])}")
    (stage / "wheelhouse").mkdir(exist_ok=True)
    for step in p["pip_commands"]:
        target = step["target"]
        print(f"      pip download ({target}) ...")
        result = subprocess.run(step["cmd"], cwd=ROOT, capture_output=True, text=True)
        if result.returncode != 0:
            print(result.stdout[-3000:] + result.stderr[-3000:])
            print(f"pip download failed for {target}: see the messages above.")
            return 1
    wheels = sorted(f.name for f in (stage / "wheelhouse").iterdir() if f.is_file())

    models: list[dict] = []
    if p["models"]:
        print("[3/4] OCR models")
        import fetch_models

        from ap_coder import offline

        result = fetch_models.fetch(stage / "models", offline.model_dir(), online=True)
        if not result.ok:
            print("Could not get every OCR model; build again when online, or use --no-models.")
            return 1
        models = [{"name": m.name, "sha256": offline.sha256(stage / "models" / m.name)}
                  for m in offline.ppocrv5_files()]  # fmt: skip
    else:
        print("[3/4] OCR models: left out (--no-models)")

    manifest = {
        "app": "AP Coder", "version": project()["version"], "built": dt.datetime.now().isoformat(timespec="seconds"),
        "built_with": f"Python {sys.version.split()[0]} on {sysconfig.get_platform()}",
        "targets": p["targets"], "requirements": p["requirements"], "source_files": len(p["source_files"]),
        "wheels": wheels, "models": models,
        "install": "Unzip, then double-click APProcessor.bat (Windows) or APProcessor.command (Mac).",
    }  # fmt: skip
    (stage / "bundle_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    print("[4/4] zip")
    dest = Path(p["zip"])
    _zip(stage, dest)
    size = dest.stat().st_size / 1e6
    print(f"Done: {dest} ({size:,.0f} MB, {len(wheels)} wheels, {len(models)} models)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the offline install ZIP (code + wheelhouse + OCR models).")
    parser.add_argument("--out", default=str(ROOT / "dist"), help="output folder (default: dist/)")
    parser.add_argument("--platform", action="append", help=f"wheel platform, repeatable (default {DEFAULT_PLATFORMS})")
    parser.add_argument("--python", nargs="+", default=DEFAULT_PYTHONS, help=f"Python versions ({DEFAULT_PYTHONS})")
    parser.add_argument("--extras", nargs="+", default=DEFAULT_EXTRAS, help=f"optional extras ({DEFAULT_EXTRAS})")
    parser.add_argument("--no-current", action="store_true", help="no wheels for this computer's own Python")
    parser.add_argument("--no-models", action="store_true", help="leave out the OCR models")
    parser.add_argument("--dry-run", action="store_true", help="print the plan as JSON; download and write nothing")
    args = parser.parse_args(argv)
    args.platform = args.platform or list(DEFAULT_PLATFORMS)
    if args.dry_run:
        p = plan(args)
        print(json.dumps({**p, "source_files": f"{len(p['source_files'])} files"}, indent=2))
        return 0
    return build(args)


if __name__ == "__main__":
    sys.exit(main())
