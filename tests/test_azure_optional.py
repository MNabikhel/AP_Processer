"""Azure Document Intelligence is optional: never needed to install or start AP Coder. Its packages are the ``azure``
extra: the offline bundle carries their wheels and the launcher installs them from there when it can (never failing
when it can't); code switched on to use Azure says so plainly when they are missing, and works when they are there."""

from __future__ import annotations

import dataclasses
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from ap_coder import doctor, extraction, inference
from ap_coder.config import DocumentIntelligenceSettings, Settings
from ap_coder.offline import AZURE_UNAVAILABLE

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


def test_the_offline_bundle_carries_azure_as_an_optional_download():
    """In the bundle by default (switching the check on later needs no download), downloaded on its own: a target
    without a wheel for it still gets everything else."""
    sys.path.insert(0, str(ROOT / "scripts"))
    import build_offline_bundle

    assert "azure" in build_offline_bundle.DEFAULT_EXTRAS and "azure" in build_offline_bundle.OPTIONAL_EXTRAS
    args = SimpleNamespace(out=str(ROOT / "dist"), extras=build_offline_bundle.DEFAULT_EXTRAS, platform=["win_amd64"],
                           python=["3.12"], no_current=True, no_models=True)  # fmt: skip
    plan = build_offline_bundle.plan(args)
    assert not [p for p in AZURE_PACKAGES if p in " ".join(plan["requirements"])]
    assert all(p in " ".join(plan["optional_requirements"]) for p in AZURE_PACKAGES)
    (step,) = plan["pip_commands"]
    assert all(p in " ".join(step["optional"]) for p in AZURE_PACKAGES) and "azure" not in " ".join(step["cmd"])


def _launcher(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("ap_script_launch_azure", ROOT / "scripts" / "launch.py")
    launch = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = launch
    spec.loader.exec_module(launch)
    monkeypatch.setattr(launch, "ROOT", tmp_path)
    monkeypatch.setattr(launch, "fingerprint", lambda: "v2")
    monkeypatch.setattr(launch.check_deps, "problem", lambda r: "Missing" if r.startswith("azure") else "")
    return launch


def test_the_launcher_installs_azure_from_the_wheelhouse_but_never_fails_over_it(tmp_path, monkeypatch, capsys):
    launch = _launcher(tmp_path, monkeypatch)
    calls: list[list[str]] = []
    monkeypatch.setattr(launch, "pip_install", lambda args: calls.append(args) or False)
    state: dict = {}
    assert not launch.ensure_azure(state) and calls == []  # no wheelhouse: nothing (never from the internet)
    (tmp_path / "wheelhouse").mkdir()
    (tmp_path / "wheelhouse" / "azure_ai_documentintelligence-1.0.2-py3-none-any.whl").write_bytes(b"")
    assert not launch.ensure_azure(state) and len(calls) == 1  # failed: said, recorded, AP Coder starts anyway
    assert all(r.startswith("azure") for r in calls[0]) and state["azure_failed"] == "v2"
    assert not launch.ensure_azure(state) and len(calls) == 1  # not tried again for this version
    assert "Not needed" in capsys.readouterr().out
    monkeypatch.setattr(launch, "pip_install", lambda args: calls.append(args) or True)
    state.clear()
    assert launch.ensure_azure(state) and "azure_failed" not in state


def test_doctor_does_not_ask_for_the_azure_packages(no_azure, reference, monkeypatch):
    real_version = doctor._version
    monkeypatch.setattr(doctor, "_version", lambda pkg: None if pkg.startswith("azure") else real_version(pkg))
    assert not [p for p in doctor._PACKAGES if p.startswith("azure")]
    checks = doctor.run_checks(Settings(), lambda: reference)
    assert not [c for c in checks if "azure" in c.area.lower() and c.status == "FAIL"]
    (line,) = [c for c in checks if c.area == "package azure-ai-documentintelligence"]
    assert line.status == "SKIP" and "optional" in line.detail


def _di_settings(key: str = "k" * 32) -> DocumentIntelligenceSettings:
    return DocumentIntelligenceSettings(endpoint="https://di.cognitiveservices.azure.com/", api_key=key)


def test_document_intelligence_without_its_package_says_so_plainly(no_azure, monkeypatch):
    monkeypatch.setenv("AP_ALLOW_INTERNET", "1")  # a developer who opted in, but without the azure extra
    for key in ("k" * 32, None):
        with pytest.raises(RuntimeError, match=AZURE_UNAVAILABLE):
            extraction.build_credential(_di_settings(key))
    with pytest.raises(RuntimeError, match=AZURE_UNAVAILABLE):
        extraction.DocumentExtractor(_di_settings())._build_client()


def test_the_offline_default_still_says_it_reads_on_this_computer(no_azure):
    """Offline (the default): the plain offline message, not an import error."""
    with pytest.raises(RuntimeError, match="not used in this offline build"):
        extraction.DocumentExtractor(_di_settings())._build_client()


def test_azure_openai_sign_in_without_its_package_says_so_plainly(no_azure):
    oai = dataclasses.replace(Settings().openai, endpoint="https://x.openai.azure.com/", api_key=None)
    with pytest.raises(RuntimeError, match=AZURE_UNAVAILABLE):
        inference.build_openai_client(dataclasses.replace(Settings(), openai=oai))
