"""The CI workflow is one GitHub accepts: a file it rejects runs no job at all, so nothing is tested."""

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

WORKFLOWS = sorted((Path(__file__).resolve().parent.parent / ".github" / "workflows").glob("*.yml"))


def _env_blocks(node, where="workflow"):
    """Every ``env:`` mapping in the workflow (top level, jobs, steps), with where it is."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "env" and isinstance(value, dict):
                yield where, value
            else:
                yield from _env_blocks(value, f"{where}.{key}")
    elif isinstance(node, list):
        for number, item in enumerate(node):
            yield from _env_blocks(item, f"{where}[{number}]")


@pytest.mark.parametrize("workflow", WORKFLOWS, ids=lambda p: p.name)
def test_env_names_are_unique_ignoring_case(workflow):
    """GitHub reads env names without case: HTTP_PROXY and http_proxy in one env block reject the whole file."""
    for where, env in _env_blocks(yaml.safe_load(workflow.read_text(encoding="utf-8"))):
        lowered = [name.lower() for name in env]
        duplicates = sorted({name for name in lowered if lowered.count(name) > 1})
        assert not duplicates, f"{workflow.name} {where}: {duplicates}"
