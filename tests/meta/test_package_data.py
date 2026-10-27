"""Every file the web app reads at run time is listed in pyproject's package data: one left out works from the code
folder but is missing from a pip install (a template not found, a page that errors)."""

import fnmatch
from pathlib import Path

import pytest

tomllib = pytest.importorskip("tomllib")

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "ap_coder"


def _patterns():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return data["tool"]["setuptools"]["package-data"]["ap_coder"]


@pytest.mark.parametrize("folder", ["web/templates", "web/static", "static", "assets"])
def test_every_web_file_is_packaged(folder):
    patterns = _patterns()
    missing = []
    for path in sorted((PACKAGE / folder).rglob("*")):
        if path.is_dir() or "__pycache__" in path.parts:
            continue
        relative = path.relative_to(PACKAGE).as_posix()
        # setuptools globs don't cross folders: "web/static/*" is not "web/static/js/app.js"
        if not any(fnmatch.fnmatch(relative, p) and relative.count("/") == p.count("/") for p in patterns):
            missing.append(relative)
    assert not missing, f"add to [tool.setuptools.package-data] in pyproject.toml: {missing}"
