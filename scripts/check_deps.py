"""Exit 1 when a package listed in pyproject.toml is missing or older than it asks for, as after an update
that added or raised one. The launchers (APProcessor.bat / APProcessor.command) run this before starting
and reinstall when it fails.

Standard library (plus the ``packaging`` that comes with pip): it runs before anything else is checked.
"""

from __future__ import annotations

import importlib.metadata
import re
import sys
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
        name = re.split(r"[<>=!~;\[ ]", requirement, maxsplit=1)[0]
    try:
        installed = importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return f"Missing: {requirement}"
    if req is not None and req.specifier and not req.specifier.contains(installed, prereleases=True):
        return f"Outdated: {name} {installed} installed, needs {req.specifier}"
    return ""


def requirements(pyproject: Path = ROOT / "pyproject.toml") -> list[str]:
    project = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]
    return list(project["dependencies"])


def main() -> int:
    for requirement in requirements():
        found = problem(requirement)
        if found:
            print(found)
            return 1
    try:  # an editable install of this folder (not a stale copy elsewhere)
        import ap_coder

        if Path(ap_coder.__file__).resolve().parent.parent != ROOT:
            print(f"ap_coder is installed from {Path(ap_coder.__file__).parent}, not from this folder")
            return 1
    except ImportError:
        print("Missing: ap_coder (pip install -e .)")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
