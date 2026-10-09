"""Which packages listed in pyproject.toml are missing or older than it asks for, as after an update that
added or raised one. The launcher (scripts/launch.py, behind APProcessor.bat / APProcessor.command) asks
this on every start and installs only those.

    python scripts/check_deps.py                # exit 1 and list what is missing (the main packages)
    python scripts/check_deps.py --extras ocr   # also the OCR add-on

Standard library (plus the ``packaging`` that comes with pip): it runs before anything else is checked.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

try:
    import tomllib
except ImportError:  # Python 3.10: pip carries its own TOML reader
    from pip._vendor import tomli as tomllib  # type: ignore[no-redef]

try:  # packaging comes with pip; pip also carries its own copy
    from packaging.requirements import Requirement
except ImportError:  # pragma: no cover - depends on what is installed
    try:
        from pip._vendor.packaging.requirements import Requirement
    except ImportError:
        Requirement = None


def applies(requirement: str) -> bool:
    """Fallback without ``packaging``: only ``sys_platform == '...'`` markers are understood."""
    marker = requirement.partition(";")[2]
    platform = re.search(r"sys_platform\s*==\s*['\"]([^'\"]+)['\"]", marker)
    return platform is None or platform.group(1) == sys.platform


def requirement_name(requirement: str) -> str:
    """The normalised package name of a requirement string (``Rapidocr_ONNXRuntime>=1`` -> ``rapidocr-onnxruntime``)."""
    name = re.split(r"[<>=!~;\[ (]", requirement.strip(), maxsplit=1)[0]
    return re.sub(r"[-_.]+", "-", name).lower()


def problem(requirement: str) -> str:
    """Why ``requirement`` is not met here, or "" when it is."""
    if Requirement is not None:
        req = Requirement(requirement)
        if req.marker is not None and not req.marker.evaluate():
            return ""
        name = req.name
    else:
        if not applies(requirement):
            return ""
        req = None
        name = requirement_name(requirement)
    try:
        installed = importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return f"Missing: {requirement}"
    if req is not None and req.specifier and not req.specifier.contains(installed, prereleases=True):
        return f"Outdated: {name} {installed} installed, needs {req.specifier}"
    return ""


def requirements(pyproject: Path = ROOT / "pyproject.toml", extras: tuple[str, ...] | list[str] = ()) -> list[str]:
    """The main requirements, plus those of each extra (e.g. ``ocr``), in pyproject order."""
    project = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]
    reqs = list(project["dependencies"])
    for extra in extras:
        reqs += project.get("optional-dependencies", {}).get(extra, [])
    return list(dict.fromkeys(reqs))


def self_problem(root: Path = ROOT) -> str:
    """ "" when ap_coder is installed (editable) from ``root``, else why not. Read from the installed
    package's record, so a copy of the folder on sys.path does not count."""
    found = "Missing: ap_coder (pip install -e .)"
    # Every copy on sys.path: the project folder's own ap_coder.egg-info (left by the editable build) has no
    # install record, the one pip installed has.
    for dist in importlib.metadata.distributions(name="ap-coder"):
        try:
            url = json.loads(dist.read_text("direct_url.json") or "{}").get("url", "")
        except ValueError:
            url = ""
        if not url.startswith("file:"):
            found = "ap_coder is not installed from this folder"
            continue
        where = Path(urllib.request.url2pathname(urllib.parse.urlparse(url).path))
        try:
            if where.resolve() == root.resolve():
                return ""
        except OSError:
            pass
        found = f"ap_coder is installed from {where}, not from this folder"
    return found


def problems(extras: tuple[str, ...] | list[str] = ()) -> list[str]:
    found = [p for p in (problem(r) for r in requirements(extras=extras)) if p]
    return found + [p for p in (self_problem(),) if p]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="List the packages AP Coder needs that are missing or too old.")
    parser.add_argument("--extras", default="", help="also these optional groups, e.g. ocr or ocr,dev")
    args = parser.parse_args(argv)
    found = problems([e for e in args.extras.split(",") if e])
    for line in found:
        print(line)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
