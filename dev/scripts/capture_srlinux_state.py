#!/usr/bin/env python
"""Capture real GnmiCollector output from a dcfabric lab node for test fixtures.

Regenerates `tests/unit/test_orchestrator/fixtures/srlinux_spine01_collected.json`
against a running lab (`uv run invoke dev.lab-deploy`). Applies a representative
spine config via the real GnmiExecutor first, so the captured state exercises
configured interfaces and a BGP neighbor, then collects DRIFT_PATHS and prunes
the volumetric, adapter-irrelevant subtrees.

Usage:
    uv run python dev/scripts/capture_srlinux_state.py [--node 172.20.20.3]

The node IP is the containerlab mgmt address of clab-dcfabric-spine-01; check it
with `docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' clab-dcfabric-spine-01`
(or the containerlab deploy output).
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from uuid import uuid4

from snapl_collector.gnmi.collector import GnmiCollector
from snapl_executor.gnmi.executor import GnmiExecutor
from snapl_intent.models import BGPSession, DesiredState, Device, Interface
from snapl_orchestrator.adapters.srlinux import DRIFT_PATHS

_FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "tests"
    / "unit"
    / "test_orchestrator"
    / "fixtures"
    / "srlinux_spine01_collected.json"
)

_NOISE_IFACE = {
    "statistics",
    "traffic-rate",
    "last-change",
    "ifindex",
    "oper-state",
    "forwarding-complex",
    "linecard",
    "forwarding-mode",
    "loopback-mode",
}


def _desired(node: str) -> DesiredState:
    dev_id = uuid4()
    device = Device(
        id=dev_id,
        name="spine-01",
        management_address="10.0.0.1",
        role="spine",
        use_case="dcfabric",
        lab_node_name=node,
    )
    return DesiredState(
        device=device,
        interfaces=[
            Interface(
                id=uuid4(),
                device_id=dev_id,
                name="ethernet-1/1",
                ip_address="10.10.1.0",
                prefix_length=31,
                enabled=True,
            ),
            Interface(
                id=uuid4(),
                device_id=dev_id,
                name="ethernet-1/2",
                ip_address="10.10.1.2",
                prefix_length=31,
                enabled=True,
            ),
        ],
        bgp_sessions=[
            BGPSession(
                id=uuid4(),
                device_id=dev_id,
                local_asn=65001,
                peer_address="10.10.1.1",
                peer_asn=65011,
                peer_group="underlay-ipv4",
                enabled=True,
            ),
        ],
    )


def _prune(data: dict) -> dict:
    out: dict = {}
    icont = data.get("/interface", {})
    ikey = next((k for k in icont if k.endswith("interface")), None)
    if ikey:
        kept = []
        for i in icont[ikey]:
            subs = i.get("subinterface") or []
            has_ip = any((s.get("ipv4") or s.get("srl_nokia-interfaces-nw-instance:ipv4")) for s in subs)
            if not has_ip:
                continue
            o = {
                k: v for k, v in i.items() if k not in _NOISE_IFACE and not k.startswith("srl_nokia-interfaces-vlans:")
            }
            sub_keep = ("index", "admin-state", "ipv4", "srl_nokia-interfaces-nw-instance:ipv4")
            o["subinterface"] = [{kk: vv for kk, vv in s.items() if kk in sub_keep} for s in subs]
            kept.append(o)
        out["/interface"] = {ikey: kept}

    bgp_key = "/network-instance[name=default]/protocols/bgp"
    bgp = data.get(bgp_key, {})
    keep_bgp = {"admin-state", "autonomous-system", "router-id", "group", "neighbor", "afi-safi"}
    b = {k: v for k, v in bgp.items() if k in keep_bgp}
    if "neighbor" in b:
        n_keep = ("peer-address", "peer-as", "peer-group", "admin-state")
        b["neighbor"] = [{k: v for k, v in n.items() if k in n_keep} for n in b["neighbor"]]
    if "group" in b:
        b["group"] = [{k: v for k, v in g.items() if k in ("group-name", "admin-state")} for g in b["group"]]
    out[bgp_key] = b

    sysd = data.get("/system", {})
    name = sysd.get("srl_nokia-system-name:name") or sysd.get("name") or {"host-name": "spine-01"}
    out["/system"] = {"srl_nokia-system-name:name": name}
    return out


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--node", default="172.20.20.3")
    args = ap.parse_args()

    desired = _desired(args.node)
    lab_pw = "NokiaSrl1!"  # documented lab default
    executor = GnmiExecutor(host=args.node, password=lab_pw, insecure=True, timeout=20)
    apply_result = await executor.apply(desired)
    print("apply:", apply_result.success, apply_result.error or "")

    collector = GnmiCollector(host=args.node, password=lab_pw, insecure=True, timeout=20)
    collected = await collector.collect(desired.device, list(DRIFT_PATHS))
    pruned = _prune(collected.data)
    _FIXTURE.write_text(json.dumps(pruned, indent=2) + "\n")
    print("wrote", _FIXTURE)


if __name__ == "__main__":
    asyncio.run(main())
