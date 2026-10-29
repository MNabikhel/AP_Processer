"""Prove a change keeps behaviour: run the same probes on the code at a git ref and on this checkout, and compare
what they produce, byte for byte (D-124). For a refactor, a clean-up or a speed-up: anything that must not change
what AP Coder decides.

    python scripts/same_behaviour.py                       tax, approve and read, against origin/main
    python scripts/same_behaviour.py tax approve --n 5000   these probes, bigger
    python scripts/same_behaviour.py pipeline --ref HEAD~3  against another ref (pipeline: whole files, slower)

Probes (``scripts/probes/<name>.py``, run side by side with each tree first on the path; inputs are made from seeds,
so both runs see the same ones):

    tax       check_taxes and math_check on generated invoices (N, default 20000; ~1 min)
    approve   approvals into new databases, every table dumped (N per database, default 200; ~1 min)
    read      reading generated invoices and two suppliers' streams with AP's approvals (N, default 24; ~3 min)
    pipeline  whole files through the offline pipeline into a database (N, default 20; ~5 min)

Exit code 0 when every probe gives the same output, 1 when one differs (the first difference is shown), 2 when a
probe failed to run. A difference is a change of behaviour: either intended (then say so in the commit) or a bug.
"Same" proves only what the probes' inputs reach (a change on a path none of them takes passes): run the tests too.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROBES = ROOT / "scripts" / "probes"
DEFAULT_N = {"tax": 20000, "approve": 200, "read": 24, "pipeline": 20}
DEFAULT = ("tax", "approve", "read")


def start_probe(name: str, tree: Path, n: int, work: Path) -> subprocess.Popen:
    """The probe running on the code in ``tree`` (both trees run side by side)."""
    env = {**os.environ, "PYTHONPATH": str(tree), "PYTHONHASHSEED": "0"}
    work.parent.mkdir(parents=True, exist_ok=True)
    return subprocess.Popen([sys.executable, str(PROBES / f"{name}.py"), str(n), str(work)], cwd=work.parent, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)  # fmt: skip


def finish(process: subprocess.Popen) -> tuple[bytes, str]:
    """(its output, its error text when it failed)."""
    out, err = process.communicate()
    return (b"", err.decode("utf-8", "replace")[-3000:]) if process.returncode else (out, "")


def first_difference(a: bytes, b: bytes) -> str:
    """Where two outputs first differ: the line, and in it the first key path whose values differ."""
    left, right = a.decode("utf-8", "replace").splitlines(), b.decode("utf-8", "replace").splitlines()
    for i, (x, y) in enumerate(zip(left, right, strict=False)):
        if x != y:
            try:
                return f"line {i + 1}: {_path(json.loads(x), json.loads(y)) or 'differs'}"
            except ValueError:
                return f"line {i + 1}: {x[:200]!r} / {y[:200]!r}"
    return f"{len(left)} lines before, {len(right)} after"


def _path(x: object, y: object, at: str = "") -> str:
    if isinstance(x, dict) and isinstance(y, dict):
        for k in sorted(set(x) | set(y), key=str):
            if x.get(k) != y.get(k):
                return _path(x.get(k), y.get(k), f"{at}/{k}")
        return ""
    if isinstance(x, list) and isinstance(y, list):
        if len(x) != len(y):
            return f"{at}: {len(x)} items before, {len(y)} after"
        for i, (u, v) in enumerate(zip(x, y, strict=True)):
            if u != v:
                return _path(u, v, f"{at}[{i}]")
        return ""
    return f"{at or '/'}: {str(x)[:120]!r} before, {str(y)[:120]!r} after"


def parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    # Checked here, not with ``choices``: argparse checks an empty or a default list against them as one value, and
    # Python versions differ in when (B-1443).
    parser.add_argument("probes", nargs="*", help=f"{', '.join(DEFAULT_N)} (default: {' '.join(DEFAULT)})")
    parser.add_argument("--ref", default="origin/main", help="the code to compare with (default origin/main)")
    parser.add_argument("--n", type=int, default=0, help="how many inputs (each probe has its own default)")
    args = parser.parse_args(argv)
    if unknown := [p for p in args.probes if p not in DEFAULT_N]:
        parser.error(f"no probe named {', '.join(unknown)} (the probes: {', '.join(DEFAULT_N)})")
    args.probes = args.probes or list(DEFAULT)
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse(argv)
    scratch = Path(tempfile.mkdtemp(prefix="ap-same-"))
    before = scratch / "before"
    added = subprocess.run(["git", "worktree", "add", "--detach", str(before), args.ref], cwd=ROOT,
                           capture_output=True, text=True)  # fmt: skip
    if added.returncode:
        print(f"Could not check out {args.ref}: {added.stderr.strip()}")
        return 2
    worst = 0
    try:
        for name in args.probes:
            n = args.n or DEFAULT_N[name]
            t0 = time.monotonic()
            runs = (
                start_probe(name, before, n, scratch / f"{name}-before"),
                start_probe(name, ROOT, n, scratch / f"{name}-after"),
            )
            (old, old_err), (new, new_err) = finish(runs[0]), finish(runs[1])
            took = time.monotonic() - t0
            if old_err or new_err:
                print(f"{name:9s} FAILED TO RUN ({took:.0f} s)\n{old_err or new_err}")
                worst = max(worst, 2)
            elif old == new:
                print(f"{name:9s} same      {len(new.splitlines()):6d} outputs, {len(new):,} bytes ({took:.0f} s)")
            else:
                print(f"{name:9s} DIFFERENT {first_difference(old, new)} ({took:.0f} s)")
                worst = max(worst, 1)
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", str(before)], cwd=ROOT, capture_output=True)
        shutil.rmtree(scratch, ignore_errors=True)
    return worst


if __name__ == "__main__":
    sys.exit(main())
