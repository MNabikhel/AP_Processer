"""Build the offline bundle: one ZIP that installs AP Coder on a computer without internet.

Run it on a computer with internet (any OS):

    python scripts/build_offline_bundle.py              # Windows 64-bit + this computer, Python 3.11 to 3.14
    python scripts/build_offline_bundle.py --mac        # also Macs (Apple silicon and Intel)
    python scripts/build_offline_bundle.py --dry-run    # show what it would do; downloads and writes nothing
    python scripts/build_offline_bundle.py --manifest-for FOLDER   # only write FOLDER's bundle_manifest.json

The ZIP (in ``dist/``) holds one ``APProcessor/`` folder:

* the code (the files git tracks, never ``private/`` data or a ``.env``)
* ``wheelhouse/``: every package for ``pip install -e ".[ocr]"`` (and ``.[dev]`` for ``--installer``, ``.[azure]`` for
  the optional Azure check, installed by the launcher when it can), as wheels for each platform and Python version
  asked for, plus pip, setuptools and wheel (where a package of the OCR add-on has no wheel, that target gets the
  rest without it: the launcher runs AP Coder without OCR; one without the Azure packages simply lacks them)
* ``models/``: the PP-OCRv5 OCR models (``scripts/fetch_models.py`` copies them into place) and the table finder's
  model (read where it is)
* ``bundle_manifest.json``: what is inside: the version, when it was built, and every file with its SHA-256
  (``files``). Unzipped over an older version, the launcher removes the files under ap_coder/, scripts/, tests/,
  samples/, wheelhouse/ and models/ that this list does not name (the older version's), and AP Coder's own
  top-level files it no longer ships (scripts/upgrade.py, prune_stale); a newer bundle unzipped
  anywhere knows from ``version`` and ``built`` that it is newer (scripts/upgrade.py)

On the offline computer: unzip, double-click ``APProcessor.bat`` (or ``APProcessor.command``). Seeing
``wheelhouse/``, the launcher installs with ``pip --no-index --find-links wheelhouse``.

This is the one build-time tool that uses the internet (it is how the bundle is made, by IT, online): it sets
the opt-in ``AP_ALLOW_INTERNET=1`` for itself. Everything the bundle runs on the laptop stays offline.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import sysconfig
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from check_deps import requirement_name, tomllib  # noqa: E402  (tomllib, or pip's copy on Python 3.10)

DEFAULT_PLATFORMS = ["win_amd64"]
# --mac: both kinds of Mac, each at the oldest macOS the current packages are built for. Apple silicon: macOS 14
# (onnxruntime, the OCR engine, publishes only macosx_14_0_arm64 wheels). Intel: macOS 13 (onnxruntime 1.23, its
# last Intel release, and opencv); onnxruntime has no Intel-Mac wheel for Python 3.14, so that one is without OCR.
MAC_PLATFORMS = ["macosx_14_0_arm64", "macosx_13_0_x86_64"]
DEFAULT_PYTHONS = ["3.11", "3.12", "3.13", "3.14"]
DEFAULT_EXTRAS = ["ocr", "dev", "azure"]
# Extras downloaded on their own, after the rest: a target where one can't be had still gets everything else (the
# manifest says so). azure: the optional Azure check's packages, so switching it on needs no download.
OPTIONAL_EXTRAS = ("azure",)
BUILD_TOOLS = ["pip", "setuptools>=68", "wheel"]  # an offline editable install builds with setuptools
# Pure-Python packages whose metadata says Python < 3.13 but that work on 3.13 and 3.14 (rapidocr-onnxruntime, the
# second OCR engine: it reads scans right there). Downloaded for every Python version, whatever their marker in
# pyproject says, with pip's --ignore-requires-python (the launcher installs them the same way).
IGNORE_REQUIRES_PYTHON = {"rapidocr-onnxruntime"}
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
    if name == ".ap_coder_launch.lock":
        return True  # the launcher's lock file (scripts/launch.py)
    if name == ".env" or (name.startswith(".env.") and name != ".env.example"):
        return True  # settings with keys
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


# pip, run with the marker values of the target computer (marker_env, as JSON in the first argument).
PIP_FOR_TARGET = ("import json, sys; from pip._vendor.packaging import markers; "
                  "env = {**markers.default_environment(), **json.loads(sys.argv.pop(1))}; "
                  "markers.default_environment = lambda: dict(env); "
                  "from pip._internal.cli.main import main; sys.exit(main())")  # fmt: skip


def marker_env(platform: str, version: str) -> dict[str, str]:
    """The PEP 508 marker values of a computer with this wheel platform and Python version. pip evaluates the
    markers in requirements for the Python that runs it, even with ``--platform`` and ``--python-version``:
    built on Linux, a Windows wheelhouse would lack click's ``colorama; platform_system == "Windows"`` (uvicorn
    needs click), and a Mac one would ask for a package marked ``platform_system != "Darwin"`` that has no Mac wheel."""
    found = re.search(r"(amd64|x86_64|arm64|aarch64|universal2|win32|i686)$", platform)
    machine = found.group(1) if found else ""
    env = {"python_version": version, "python_full_version": f"{version}.0", "implementation_name": "cpython",
           "implementation_version": f"{version}.0", "platform_python_implementation": "CPython"}  # fmt: skip
    if platform.startswith("win"):
        machine = {"amd64": "AMD64", "arm64": "ARM64", "win32": "x86"}.get(machine, machine)
        env.update(sys_platform="win32", platform_system="Windows", os_name="nt")
    elif platform.startswith("macosx"):
        env.update(sys_platform="darwin", platform_system="Darwin", os_name="posix")
    elif "linux" in platform:
        env.update(sys_platform="linux", platform_system="Linux", os_name="posix")
    else:
        return env  # a platform not known here: only the Python version is the target's
    return {**env, "platform_machine": machine, "platform_release": "", "platform_version": ""}


def pip_commands(reqs: list[str], wheelhouse: Path, platforms: list[str], pythons: list[str],
                 current: bool = True, plain: list[str] | None = None,
                 optional: list[str] | None = None) -> list[dict]:  # fmt: skip
    """``pip download`` commands ({"target", "cmd"}): this computer's own Python, then each platform and
    Python version (wheels only: nothing can be compiled on the offline computer). "also": the
    IGNORE_REQUIRES_PYTHON packages (without their marker) with ``--ignore-requires-python``, run after ``cmd``.
    Given ``plain`` (the requirements without the OCR add-on), each also has a "fallback" command, run instead
    when ``cmd`` fails (then without "also": those packages are part of the OCR add-on). Given ``optional`` (the
    OPTIONAL_EXTRAS' requirements), each also has an "optional" command, run after the others: when it fails, that
    target is only without them."""
    args = ["download", "--disable-pip-version-check", "--dest", str(wheelhouse)]
    loose = [r.split(";")[0].strip() for r in reqs if requirement_name(r) in IGNORE_REQUIRES_PYTHON]
    strict = [r for r in reqs if requirement_name(r) not in IGNORE_REQUIRES_PYTHON]
    plain = None if plain is None else [r for r in plain if requirement_name(r) not in IGNORE_REQUIRES_PYTHON]

    def step(target: str, cmd: list[str]) -> dict:
        found = {"target": target, "cmd": [*cmd, *strict]}
        if loose:
            found["also"] = [*cmd, "--ignore-requires-python", *loose]
        if plain is not None and plain != strict:
            found["fallback"] = [*cmd, *plain]
        if optional:
            found["optional"] = [*cmd, *optional]
        return found

    cmds = []
    if current:
        cmds.append(step("this computer", [sys.executable, "-m", "pip", *args, "--prefer-binary"]))
    for platform in platforms:
        for version in pythons:
            pip = [sys.executable, "-c", PIP_FOR_TARGET, json.dumps(marker_env(platform, version))]
            cmds.append(step(f"{platform}, Python {version}",
                             [*pip, *args, "--only-binary=:all:", "--implementation", "cp", "--platform", platform,
                              "--python-version", version]))  # fmt: skip
    return cmds


def plan(args: argparse.Namespace) -> dict:
    """Everything the build will do, without doing it (the --dry-run output)."""
    out_dir = Path(args.out).resolve()
    stage = out_dir / "offline-bundle" / TOP
    reqs = requirements([e for e in args.extras if e not in OPTIONAL_EXTRAS])
    plain = requirements([e for e in args.extras if e != "ocr" and e not in OPTIONAL_EXTRAS])
    extras = project().get("optional-dependencies", {})
    optional = [r for e in args.extras if e in OPTIONAL_EXTRAS for r in extras.get(e, [])]
    from ap_coder.ops import offline

    return {
        "name": f"{TOP}-offline-{project()['version']}-{dt.date.today():%Y%m%d}.zip",
        "zip": str(out_dir / f"{TOP}-offline-{project()['version']}-{dt.date.today():%Y%m%d}.zip"),
        "stage": str(stage),
        "source_files": [p.as_posix() for p in source_files()],
        "requirements": reqs,
        "optional_requirements": optional,
        "targets": (
            ["this computer (" + sysconfig.get_platform() + f", Python {sys.version_info[0]}.{sys.version_info[1]})"]
            if not args.no_current
            else []
        )
        + [f"{p}, Python {v}" for p in args.platform for v in args.python],
        "pip_commands": pip_commands(
            reqs, stage / "wheelhouse", args.platform, args.python, not args.no_current, plain, optional
        ),
        "models": [] if args.no_models else [{"name": m.name, "sha256": m.sha256} for m in offline.bundle_models()],
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
            info.create_system = 3  # Unix, also when built on Windows: else the Mac ignores those permissions
            info.compress_type = zipfile.ZIP_STORED if path.suffix in (".whl", ".onnx") else zipfile.ZIP_DEFLATED
            with open(path, "rb") as f:
                zf.writestr(info, f.read())
    os.replace(tmp, dest)


def file_list(stage: Path) -> dict[str, str]:
    """Every file in the bundle folder ``stage`` (but the manifest itself), by its path, with its SHA-256."""
    from ap_coder.ops.offline import sha256

    return {path.relative_to(stage).as_posix(): sha256(path) for path in sorted(stage.rglob("*"))
            if path.is_file() and path.name != "bundle_manifest.json"}  # fmt: skip


def write_manifest(folder: Path) -> Path:
    """``folder``'s bundle_manifest.json, for a folder made some other way (CI, a hand-made bundle): its version,
    built now, and its files."""
    version = re.search(r'(?m)^version\s*=\s*"([^"]+)"', (folder / "pyproject.toml").read_text(encoding="utf-8"))
    manifest = {"app": "AP Coder", "version": version.group(1) if version else "",
                "built": dt.datetime.now().isoformat(timespec="seconds"), "files": file_list(folder)}  # fmt: skip
    path = folder / "bundle_manifest.json"
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return path


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
    without_ocr: list[str] = []
    without_optional: list[str] = []
    for step in p["pip_commands"]:
        target = step["target"]
        print(f"      pip download ({target}) ...")
        result = subprocess.run(step["cmd"], cwd=ROOT, capture_output=True, text=True)
        if result.returncode != 0 and step.get("fallback"):
            why = [ln for ln in result.stderr.splitlines() if ln.startswith("ERROR")][-2:] or ["(no message)"]
            print("      " + "\n      ".join(why))
            print(f"      ... so {target} gets everything but the OCR add-on (AP Coder runs there without it)")
            result = subprocess.run(step["fallback"], cwd=ROOT, capture_output=True, text=True)
            if result.returncode == 0:
                without_ocr.append(target)
        elif result.returncode == 0 and step.get("also"):
            result = subprocess.run(step["also"], cwd=ROOT, capture_output=True, text=True)
        if result.returncode != 0:
            print(result.stdout[-3000:] + result.stderr[-3000:])
            print(f"pip download failed for {target}: see the messages above.")
            return 1
        if step.get("optional") and subprocess.run(step["optional"], cwd=ROOT, capture_output=True).returncode:
            print(f"      ... {target} is without the optional Azure packages (no wheel for it); the rest is there")
            without_optional.append(target)
    wheels = sorted(f.name for f in (stage / "wheelhouse").iterdir() if f.is_file())

    models: list[dict] = []
    if p["models"]:
        print("[3/4] OCR models")
        import fetch_models

        from ap_coder.ops import offline

        result = fetch_models.fetch(stage / "models", offline.model_dir(), online=True)
        # The table finder's model, from this checkout's models/ folder when it is there, else downloaded.
        finder = (offline.table_finder_file(),)
        table = fetch_models.fetch(stage / "models", ROOT / "models", online=True, models=finder)
        if not result.ok or not table.ok:
            print("Could not get every OCR model; build again when online, or use --no-models.")
            return 1
        models = [{"name": m.name, "sha256": offline.sha256(stage / "models" / m.name)}
                  for m in offline.bundle_models()]  # fmt: skip
    else:
        print("[3/4] OCR models: left out (--no-models)")

    manifest = {
        "app": "AP Coder", "version": project()["version"], "built": dt.datetime.now().isoformat(timespec="seconds"),
        "built_with": f"Python {sys.version.split()[0]} on {sysconfig.get_platform()}",
        "targets": p["targets"], "requirements": p["requirements"], "source_files": len(p["source_files"]),
        "optional_requirements": p["optional_requirements"], "wheels": wheels, "without_ocr": without_ocr,
        "without_azure": without_optional, "models": models,
        "install": "Unzip, then double-click APProcessor.bat (Windows) or APProcessor.command (Mac).",
        "files": file_list(stage),
    }  # fmt: skip
    (stage / "bundle_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    print("[4/4] zip")
    dest = Path(p["zip"])
    _zip(stage, dest)
    size = dest.stat().st_size / 1e6
    print(f"Done: {dest} ({size:,.0f} MB, {len(wheels)} wheels, {len(models)} models)")
    if without_ocr:
        print(f"Without the OCR add-on (a package of it has no wheel there): {'; '.join(without_ocr)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    from ap_coder.ops.offline import allow_internet

    allow_internet()  # building the bundle is the one step that downloads (packages, OCR models), on IT's computer
    parser = argparse.ArgumentParser(description="Build the offline install ZIP (code + wheelhouse + OCR models).")
    parser.add_argument("--out", default=str(ROOT / "dist"), help="output folder (default: dist/)")
    parser.add_argument("--platform", action="append", help=f"wheel platform, repeatable (default {DEFAULT_PLATFORMS})")
    parser.add_argument("--mac", action="store_true", help=f"also Macs: {' and '.join(MAC_PLATFORMS)}")
    parser.add_argument("--python", nargs="+", default=DEFAULT_PYTHONS, help=f"Python versions ({DEFAULT_PYTHONS})")
    parser.add_argument("--extras", nargs="+", default=DEFAULT_EXTRAS, help=f"optional extras ({DEFAULT_EXTRAS})")
    parser.add_argument("--no-current", action="store_true", help="no wheels for this computer's own Python")
    parser.add_argument("--no-models", action="store_true", help="leave out the OCR models")
    parser.add_argument("--dry-run", action="store_true", help="print the plan as JSON; download and write nothing")
    parser.add_argument("--manifest-for", metavar="FOLDER", help="only write FOLDER's bundle_manifest.json")
    args = parser.parse_args(argv)
    if args.manifest_for:
        print(f"Wrote {write_manifest(Path(args.manifest_for).resolve())}")
        return 0
    args.platform = list(dict.fromkeys((args.platform or DEFAULT_PLATFORMS) + (MAC_PLATFORMS if args.mac else [])))
    if args.dry_run:
        p = plan(args)
        print(json.dumps({**p, "source_files": f"{len(p['source_files'])} files"}, indent=2))
        return 0
    return build(args)


if __name__ == "__main__":
    sys.exit(main())
