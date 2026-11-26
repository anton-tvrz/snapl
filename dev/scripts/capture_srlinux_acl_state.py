#!/usr/bin/env python
"""Capture SR Linux's /acl state from the site_edge lab for test fixtures (spec 008 T020).

A sibling of capture_srlinux_state.py for research R16's ACL captures. The rule
is configured by hand first, with dev/scripts/site_edge_capture.cli, because
snapl cannot render ACLs until the captures have settled their shape. So this
script never calls the executor and writes nothing to Infrahub. It optionally
makes one hand change over ``sr_cli``, then collects ``/acl`` with the real
GnmiCollector, prunes the per-entry statistics, and writes
tests/unit/test_orchestrator/fixtures/srlinux_v26_3_1_acl_<case>.json.

Usage (lab up: uv run invoke dev.lab-deploy --use-case site_edge):
    uv run python dev/scripts/capture_srlinux_acl_state.py --case baseline
    uv run python dev/scripts/capture_srlinux_acl_state.py --case deny-deleted \\
        --change 'delete / acl acl-filter ot-ingress type ipv4 entry 20'

Record each capture's provenance in the fixtures directory's README.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess  # nosec B404 - runs docker exec against the local lab only
from pathlib import Path
from typing import Any
from uuid import uuid4

CASES = (
    "baseline",
    "applied",
    "deny-deleted",
    "permit-inserted",
    "action-flipped",
    "action-flipped-applied",
    "binding-removed",
    "binding-moved",
)

_FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "unit" / "test_orchestrator" / "fixtures"

# Counters change with every packet, so a fixture keeping them would never be
# reproducible. Nothing the adapter reads lives under them.
_VOLUMETRIC = frozenset({"statistics"})

_LAB_PASSWORD = "NokiaSrl1!"  # documented lab default  # pragma: allowlist secret  # noqa: S105


def fixture_path(case: str) -> Path:
    return _FIXTURES / f"srlinux_v26_3_1_acl_{case}.json"


def prune(data: Any) -> Any:
    """``data`` without any ``statistics`` subtree, at any depth."""
    if isinstance(data, dict):
        return {key: prune(value) for key, value in data.items() if key not in _VOLUMETRIC}
    if isinstance(data, list):
        return [prune(item) for item in data]
    return data


async def capture(
    *,
    node: str,
    container: str,
    change: str | None,
    out: Path,
    collector: Any,
    run: Any = subprocess.run,
) -> None:
    """Make the hand change, if any, then collect /acl from ``node`` and write it to ``out``."""
    from snapl_intent.models import Device  # noqa: PLC0415 - keeps parse_args free of package imports

    if change is not None:
        run(["docker", "exec", container, "sr_cli", "-ec", change], check=True)

    device = Device(
        id=uuid4(),
        name="site-edge-01",
        management_address=node,
        role="edge",
        use_case="site_edge",
        lab_node_name=node,
    )
    collected = await collector.collect(device, ["/acl"])
    out.write_text(json.dumps(prune(collected.data), indent=2) + "\n")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--case", required=True, choices=CASES)
    ap.add_argument("--node", default="172.20.22.11", help="gNMI address (default: site-edge-01's mgmt pin)")
    ap.add_argument(
        "--container", default="clab-site-edge-site-edge-01", help="container the --change runs in (sr_cli)"
    )
    ap.add_argument("--change", default=None, help="one sr_cli -ec line to run before collecting")
    return ap.parse_args(argv)


async def main() -> None:
    from snapl_collector.gnmi.collector import GnmiCollector  # noqa: PLC0415

    args = parse_args()
    out = fixture_path(args.case)
    collector = GnmiCollector(host=args.node, password=_LAB_PASSWORD, insecure=True, timeout=20)
    await capture(node=args.node, container=args.container, change=args.change, out=out, collector=collector)
    print("wrote", out)


if __name__ == "__main__":
    asyncio.run(main())
