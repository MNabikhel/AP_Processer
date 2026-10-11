"""Azure isn't part of the offline build yet: its packages are an optional extra (``.[azure]``), never in the default
install or the offline bundle, and every place that would use them says so plainly when they are not installed."""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

import pytest

from ap_coder import doctor, extraction, inference
from ap_coder.config import DocumentIntelligenceSettings, Settings
from ap_coder.offline import AZURE_NOT_IN_THIS_BUILD

ROOT = Path(__file__).resolve().parent.parent
AZURE_PACKAGES = ("azure-ai-documentintelligence", "azure-identity")


def _project() -> dict:
    sys.path.insert(0, str(ROOT / "scripts"))
    from check_deps import tomllib

    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]


@pytest.fixture
def no_azure(monkeypatch):
    """This computer has no Azure packages (the offline bundle installs none): every ``import azure...`` fails."""
    for name in [n for n in sys.modules if n == "azure" or n.startswith("azure.")] + [
        "azure",
        "azure.core",
        "azure.core.credentials",
        "azure.identity",
        "azure.ai",
        "azure.ai.documentintelligence",
        "azure.ai.documentintelligence.models",
    ]:
        monkeypatch.setitem(sys.modules, name, None)


def test_azure_is_an_optional_extra_not_a_core_package():
    project = _project()
    core = " ".join(project["dependencies"])
    assert not [p for p in AZURE_PACKAGES if p in core]
    extra = " ".join(project["optional-dependencies"]["azure"])
    assert all(p in extra for p in AZURE_PACKAGES)
    assert "openai" in core  # the LM Studio client stays
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert not [p for p in AZURE_PACKAGES if p in requirements]


def test_the_offline_bundle_leaves_azure_out():
    sys.path.insert(0, str(ROOT / "scripts"))
    import build_offline_bundle

    assert "azure" not in build_offline_bundle.DEFAULT_EXTRAS
    wanted = " ".join(build_offline_bundle.requirements(build_offline_bundle.DEFAULT_EXTRAS))
    assert not [p for p in AZURE_PACKAGES if p in wanted]


def test_doctor_does_not_ask_for_the_azure_packages(no_azure, reference):
    assert not [p for p in doctor._PACKAGES if p.startswith("azure")]
    checks = doctor.run_checks(Settings(), lambda: reference)
    assert not [c for c in checks if "azure" in c.area.lower() and c.status == "FAIL"]


def _di_settings(key: str = "k" * 32) -> DocumentIntelligenceSettings:
    return DocumentIntelligenceSettings(endpoint="https://di.cognitiveservices.azure.com/", api_key=key)


def test_document_intelligence_without_its_package_says_so_plainly(no_azure, monkeypatch):
    monkeypatch.setenv("AP_ALLOW_INTERNET", "1")  # a developer who opted in, but without the azure extra
    for key in ("k" * 32, None):
        with pytest.raises(RuntimeError, match=AZURE_NOT_IN_THIS_BUILD):
            extraction.build_credential(_di_settings(key))
    with pytest.raises(RuntimeError, match=AZURE_NOT_IN_THIS_BUILD):
        extraction.DocumentExtractor(_di_settings())._build_client()


def test_the_offline_build_still_says_it_reads_on_this_computer(no_azure):
    """Offline (the default): the plain offline message, not an import error."""
    with pytest.raises(RuntimeError, match="not used in this offline build"):
        extraction.DocumentExtractor(_di_settings())._build_client()


def test_azure_openai_sign_in_without_its_package_says_so_plainly(no_azure):
    oai = dataclasses.replace(Settings().openai, endpoint="https://x.openai.azure.com/", api_key=None)
    with pytest.raises(RuntimeError, match=AZURE_NOT_IN_THIS_BUILD):
        inference.build_openai_client(dataclasses.replace(Settings(), openai=oai))
