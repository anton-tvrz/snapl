# SR Linux WAN overlay spike fixtures

Real SR Linux **v26.3.1** (`ghcr.io/nokia/srlinux:26.3.1`, type `ixr-d3`) payloads captured on 2026-10-28
for the WAN overlay spike (#126). The findings they back are in `dev/knowledge/sr-linux-wan-overlay.md`.

No test reads these files yet. They sit in their own directory, not under a package's test fixtures, so
that nothing looks like it already depends on them. The WAN spec should move or reference them when it
adds tests.

The lab was a throwaway `wan-spike` topology: `hub1` and `br1` (SR Linux), two transparent Linux-bridge
"underlays" (`mpls`, `isp`), and one Linux host per site. Addressing follows the companion doc:

- Underlay links: `10.255.1.0/31` (mpls) and `198.51.100.0/31` (isp).
- VTEPs: `system0` `10.254.0.1` (hub1) and `10.254.0.11` (br1).
- Site LANs: `10.1.0.0/24` and `10.11.0.0/24`, in ip-vrf `corp` (EVPN-VXLAN, VNI 100).

## gNMI Set payloads (inputs, applied as-is)

Each file was sent as one gNMI Set, `update` at path `/`, with pygnmi's default encoding. That's the same
call `GnmiExecutor` makes. Every one was accepted on the first try.

| file | what |
| --- | --- |
| `srlinux_v26_3_1_wan_hub1_set_overlay.json` | hub1: interfaces, system0, `vxlan1.100`, BFD, routing policy, underlay eBGP + EVPN overlay, ip-vrf `corp` |
| `srlinux_v26_3_1_wan_br1_set_overlay.json` | br1: the same set of objects as hub1 |
| `srlinux_v26_3_1_wan_hub1_set_in_tunnel_bgp.json` | hub1: `lo1` in `corp`, plus an eBGP multihop + BFD session to br1 that rides inside the VXLAN tunnel |
| `srlinux_v26_3_1_wan_br1_set_in_tunnel_bgp.json` | br1: the same in-tunnel session as hub1 |

## gNMI responses (captured from the device)

These come from a GET with `encoding=json_ietf`, the collector's settings. Steady-state captures used
`datatype=all`. The failed-state and route-table captures used `datatype=state`. The output is keyed by
requested path, like `srlinux_spine01_collected.json`. The `statistics` and `traffic-rate` subtrees were
pruned (same rule as `tests/unit/test_orchestrator/fixtures/`). Every other field is verbatim.

| file | what |
| --- | --- |
| `srlinux_v26_3_1_ixr_d3_system_features.json` | `/system/features` (state). Has `vxlan`, `evpn-vxlan-ifl`, `bfd`. Lacks every GRE, IP-in-IP, UDP-tunnel and MPLS feature |
| `srlinux_v26_3_1_ixr_d3_gre_nhg_set_rejected.json` | A GRE next-hop-group Set and the device's rejection text |
| `srlinux_v26_3_1_wan_br1_get_steady.json` | br1 at steady state: `vxlan1`, default-NI BGP (3 sessions Established), `/bfd`, ip-vrf `corp` with the hub LAN over VXLAN, routing policy, `ethernet-1/1`, `system0` |
| `srlinux_v26_3_1_wan_br1_get_underlay_route_table.json` | br1 default-NI route table at steady state: hub VTEP `10.254.0.1/32` via mpls, plus the VXLAN tunnel-table |
| `srlinux_v26_3_1_wan_br1_get_mpls_failed.json` | br1 after the hub side of the mpls underlay failed. The mpls session is `connect`, isp and overlay are `established`. Only the isp BFD session remains. `ethernet-1/1` is still oper-up |
| `srlinux_v26_3_1_wan_br1_get_in_tunnel_bgp.json` | br1 `corp` BGP and BFD for the in-tunnel session, after one underlay failover (`established-transitions: 2`) |
