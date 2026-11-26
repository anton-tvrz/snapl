#!/usr/bin/env python
"""Capture a sample-mode gNMI subscription from a dcfabric lab node (spec 016, research R1).

Writes every message the subscription delivers, with the seconds since it
started, to a JSON file. The first message carries ``sync_response``. Used for
the fixtures in ``tests/unit/test_collector/fixtures/telemetry/``.

Usage:
    uv run python dev/scripts/capture_srlinux_telemetry.py --node 172.20.21.13 \\
        --out tests/unit/test_collector/fixtures/telemetry/srlinux_v26_3_1_leaf01_sample.json \\
        --seconds 35 --interval 10 \\
        "/interface[name=ethernet-1/49]/oper-state" "/interface[name=ethernet-1/49]/statistics"

The node is the containerlab address (spine-01 = 172.20.21.11 ... leaf-04 =
172.20.21.16). The password is ``SRLINUX_PASSWORD``, or the lab default.

When the script stops the subscription, pygnmi's reader thread prints a
``StatusCode.CANCELLED`` "Channel closed!" traceback. That is the close, not a failure.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from pygnmi.client import gNMIclient

_LAB_DEFAULT_PASSWORD = "NokiaSrl1!"  # pragma: allowlist secret  # noqa: S105  # documented lab default


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--node", required=True)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--seconds", type=float, default=35.0)
    ap.add_argument("--interval", type=int, default=10, help="sample interval in seconds")
    ap.add_argument("paths", nargs="+")
    args = ap.parse_args()

    subscribe = {
        "subscription": [
            {"path": path, "mode": "sample", "sample_interval": args.interval * 1_000_000_000} for path in args.paths
        ],
        "mode": "stream",
        "encoding": "json_ietf",
    }
    password = os.environ.get("SRLINUX_PASSWORD") or _LAB_DEFAULT_PASSWORD
    messages = []
    start = time.monotonic()
    with gNMIclient(target=(args.node, 57400), username="admin", password=password, insecure=True, timeout=20) as gc:
        for message in gc.subscribe2(subscribe=subscribe):
            messages.append({"t_rel": round(time.monotonic() - start, 3), "msg": message})
            if time.monotonic() - start > args.seconds:
                break
    args.out.write_text(json.dumps(messages, indent=1, default=str) + "\n")
    print(f"{len(messages)} messages to {args.out}")


if __name__ == "__main__":
    main()
