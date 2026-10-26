"""``python -m ap_coder jde test|sync|status``: the JD Edwards API connection from the console (D-104).

* ``test``: AIS's version, a sign-in and a logout (only once an Admin switched a step on in Settings).
* ``sync``: step 1 now: suppliers, GL accounts, business units, open POs and vouchers, with the report (``--only``
  some of them). For a scheduled task, as the dashboard's nightly sync does while it runs.
* ``status``: the connection, the last test and sync, and the vouchers waiting for JD Edwards; ``--retry`` sends them.
"""

from __future__ import annotations

import argparse
from typing import Any

from ..core.store import Store
from ..erp import jde_ais, jde_post, jde_sync


def add_parser(sub: Any) -> None:
    p = sub.add_parser("jde", help="The JD Edwards API connection (AIS): test, sync, status")
    p.add_argument("action", choices=["test", "sync", "status"])
    p.add_argument("--only", nargs="+", choices=list(jde_sync.PARTS), help="sync: only these parts")
    p.add_argument("--retry", action="store_true", help="status: send the vouchers waiting for JD Edwards now")
    p.add_argument(
        "--allow-mass-close",
        action="store_true",
        help="sync: close the POs JD Edwards no longer lists even when that is most or all of them",
    )


def main(args: argparse.Namespace) -> int:
    store = Store(args.db)
    actor = "the console (jde)"
    cfg = jde_ais.load(store)
    if args.action == "test":
        found = jde_ais.connection_test(store, actor=actor)
        print(found.get("summary") if found.get("ok") else f"Not connected: {found.get('error')}")
        return 0 if found.get("ok") else 1
    if args.action == "sync":
        try:
            report = jde_sync.sync(
                store,
                parts=tuple(args.only or jde_sync.PARTS),
                actor=actor,
                allow_mass_close=bool(getattr(args, "allow_mass_close", False)),
            )
        except jde_ais.AisError as exc:
            print(exc)
            return 1
        for line in jde_sync.report_lines(report, shown=50):
            print(line)
        return 0 if report["ok"] else 1
    _, note = jde_ais.status(cfg)
    print(f"JD Edwards API: {note}")
    if cfg.base_url:
        print(f"AIS address: {cfg.base_url} · environment {cfg.environment or '(none)'} · user "
              f"{cfg.username or '(none)'} · password {'saved' if cfg.password else 'not saved'}")  # fmt: skip
    tested = jde_ais.last_test(store)
    if tested:
        print(f"Last test {str(tested.get('at') or '')[:16]}: {tested.get('summary') or tested.get('error')}")
    synced = jde_sync.state(store)
    if synced.get("tried"):
        print(
            f"Last sync {synced['tried'][:16]}" + ("" if synced.get("at") == synced["tried"] else " (did not finish)")
        )
    waiting = jde_post.waiting(store)
    print(f"Vouchers waiting for JD Edwards: {len(waiting)}")
    for p in waiting:
        print(f"  AP Coder #{p['invoice_id']} (batch {p['batch']}): {jde_post.LABELS[p['status']]}"
              + (f" - {p['error']}" if p["error"] else ""))  # fmt: skip
    if args.retry and waiting:
        try:
            result = jde_post.retry_queue(store, actor=actor)
        except jde_ais.AisError as exc:
            print(exc)
            return 1
        print(f"Sent: {result['posted']} posted, {result['failed']} refused, {result['queued'] + result['unknown']} "
              "still waiting." + (f" {result['error']}" if result["error"] else ""))  # fmt: skip
        return 0 if not (result["queued"] or result["unknown"] or result["failed"]) else 1
    return 0
