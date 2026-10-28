# SR Linux WAN Overlay: Which Tunnel Works (spike for #126)

> **Answer.** SR Linux 26.3.1 can't build a GRE or IP-in-IP tunnel interface on any platform. On the
> 7220 IXR-D3 type snapl runs, the only overlay it can build is **EVPN-VXLAN**. The tunnel source is
> always `system0`, so a hub/branch pair gets **one** tunnel, not one per underlay. Path choice moves into
> the underlay: one eBGP + BFD session per underlay carries the remote VTEP's `/32`, and route preference
> picks the underlay. In the lab this config went on in one gNMI Set per node, and BGP failed over to the
> second underlay when the first one died. BFD in the container image ran at 1 s whatever was configured,
> so the measured loss window was **2.25–3.0 s**. That is above the companion doc's 2000 ms target.

Spike date: 2026-10-28. Image: `ghcr.io/nokia/srlinux:26.3.1`, node type `ixr-d3` (7220 IXR-D3), the
same pin as dcfabric. containerlab ran dockerized (`ghcr.io/srl-labs/clab:latest`) on macOS/OrbStack.
Raw payloads are in `tests/fixtures/srlinux_wan_overlay/`.

## The questions

1. Which tunnel encapsulations can SR Linux build point-to-point between two routers over an IP underlay?
2. For the ones that work: can eBGP run over the tunnel, with BFD? Does BGP fail over when an underlay dies?
3. Can all of it be configured through gNMI Set, snapl's Executor path?
4. Does `containerlab tools netem` work on SR Linux interfaces, for the T-05 brownout?
5. Which overlay should snapl use, and what does that change in `docs/wan-prototype-companion.md`?

## 1. Tunnel encapsulations: what the image ships

Sources:
- The YANG under `/opt/srlinux/models` in the image: 548 modules, copied out with `docker create` + `docker cp`.
- The `/system/features` state leaf-list on a live IXR-D3 node, which lists what that platform actually
  enables.
- Nokia's SR Linux docs.

| encapsulation | in the YANG as | on the IXR-D3 container | usable for a routed p2p tunnel with BGP+BFD? |
| --- | --- | --- | --- |
| **GRE** | Not an interface. Only (a) a static next-hop-group encap (`next-hop-groups/group/nexthop/encapsulate-header gre` + `gre/{source-ip,destination-ip}`), (b) PBR `encapsulate-gre` / `decapsulate-gre`, (c) `ip-tunnel-decapsulation` groups, (d) GRE for mirror destinations | Features `static-next-hop-group-gre-tunnel`, `policy-based-forwarding-{en,de}capsulate-gre` and `ip-tunnel-decap-groups` are **absent**. A gNMI Set is **rejected**. Only `mirror-dest-remote-tunnel-encap-l2ogre` is present, and that is for traffic mirroring, not forwarding | **No**, on any platform |
| **IP-in-IP** | Only gRIBI-programmed AFT entries (state) and a `system-resource-ip-in-ip-tunnels` feature. No config model | Absent | **No** |
| **VXLAN** | `tunnel-interface vxlan<N>` / `vxlan-interface <idx>`, type `bridged` (mac-vrf) or `routed` (ip-vrf) | `vxlan`, `evpn`, `evpn-vxlan-ifl` and `evpn-vxlan-mac-vrf` are **present** | **Yes**, EVPN only (see below) |
| MPLS, SR-MPLS, LDP, EVPN-MPLS | `srl_nokia-mpls`, LDP, ISIS-SR modules | `mpls` and `evpn-mpls` are absent | No (7250/7730-class only) |
| SRv6 | `srl_nokia-srv6*` | `isis-srv6` is absent | No |
| MPLS-in-UDP / UDP tunnel | `static-nhg-udp-tunnel`, `udp-tunnel` | Absent | No |
| WireGuard, IPsec | Nothing. `ipsec` appears only as an ACL protocol match | — | No |

Why GRE is out on any platform, not just on this one:

- **It is not an interface.** The interface name pattern (`srl_nokia-common:interface-all`) allows only
  `ethernet-*`, `lag*`, `irb*`, `lo*`, `system0`, `mgmt*`, `lif-*`, `vhn-*`, `enp*` and `sync0-*`. There
  is no `gre*`/`tun*`. A GRE next-hop is a one-way encap action on a static route, so there is nothing
  with an IP address for a BGP session to bind to.
- **BFD is explicitly forbidden on it.** From `srl_nokia-next-hop-groups.yang`:
  `must "not(../../encapsulate-header = 'gre')" { error-message "BFD not supported for next-hops with GRE encapsulation"; }`
- **Platforms.** Nokia's 26.3 *Network-instances* guide restricts GRE tunnel next-hops to "7250 IXR-6,
  7250 IXR-10, 7250 IXR-6e, 7250 IXR-10e, and 7250 IXR-18e only". It also restricts GRE encaps to the
  default network-instance.

Live rejection on br1, sent with the same `set(update=[("/", payload)])` call the Executor makes. Full
request in `srlinux_v26_3_1_ixr_d3_gre_nhg_set_rejected.json`:

```text
Schema '/network-instance/next-hop-groups/group/nexthop' has no local leaf with the name
'encapsulate-header'. Options are [index, ip-address, admin-state, resolve]
```

Two VXLAN constraints shape everything that follows:

- **EVPN only.** VXLAN destinations under
  `tunnel-interface/vxlan-interface/bridge-table/{unicast,multicast}-destinations` are all
  `config false`. You can't configure a static remote VTEP. Nokia's EVPN-VXLAN guide says the same:
  "Configuration of VXLAN on SR Linux is tied to EVPN", and the egress VTEP "is determined by EVPN".
- **One source VTEP per box.** `vxlan-interface/egress/source-ip` is an enum with a single value,
  `use-system-ipv4-address`. The guide: "the data path supports one source tunnel IP address for all
  VXLAN interfaces" and "The source IP used in the vxlan-interfaces is the IPv4 address of subinterface
  system0.0 in the default network-instance". The 26.3.1 YANG enum above says the same today. So
  hub and branch have exactly one VTEP pair between them. The routed underlay picks which underlay
  carries it, not the tunnel.

## 2. Lab: EVPN-VXLAN over two underlays

Throwaway lab, created and destroyed in the spike: `name: wan-spike`, mgmt network `clab-wan-spike`
on `172.20.30.0/24`. It is not committed: it isn't a snapl lab, and committing it would suggest one exists.

```text
 h-br1 ── e1-3 [br1 AS65011] e1-1 ── mpls (linux bridge) ── e1-1 [hub1 AS65000] e1-3 ── h-hub
          10.11.0.0/24      e1-2 ── isp  (linux bridge) ── e1-2       10.1.0.0/24
```

```yaml
nodes:
  hub1: { kind: nokia_srlinux, type: ixr-d3 }   # image ghcr.io/nokia/srlinux:26.3.1
  br1:  { kind: nokia_srlinux, type: ixr-d3 }
  mpls: { kind: linux, exec: [ip link add br0 type bridge, ip link set br0 up,
                              ip link set eth1 master br0, ip link set eth2 master br0] }
  isp:  { ...same as mpls... }
  h-hub / h-br1: { kind: linux }                # wbitt/network-multitool, 10.x.0.10/24
links: [br1:e1-1–mpls:eth1, hub1:e1-1–mpls:eth2, br1:e1-2–isp:eth1, hub1:e1-2–isp:eth2,
        br1:e1-3–h-br1:eth1, hub1:e1-3–h-hub:eth1]
```

The underlays are transparent L2 bridges on purpose. Failing the bridge's *hub-side* port leaves br1's
`ethernet-1/1` oper-up, so br1 has nothing but BFD to notice the failure. A direct veth would drop both
ends at once and BFD would never be exercised.

Design, per node:

- **Underlay** (default network-instance): one eBGP session per underlay link, each with BFD
  (`failure-detection enable-bfd true`, `fast-failover true`).
  - Export policy: only `system0` `/32`s (prefix-set `10.254.0.0/24` mask `32..32`).
  - Import policy: sets `local-preference` 200 on mpls and 50 on isp, so mpls is primary.
- **Overlay**: an iBGP EVPN session between the `system0` addresses (`local-as 65500`, `peer-as 65500`,
  `transport local-address <system0>`).
- **Service**: ip-vrf `corp` holds the LAN interface plus `vxlan1.100` (type `routed`, VNI 100), with
  `bgp-evpn bgp-instance 1 { vxlan-interface vxlan1.100; evi 100 }` and explicit route targets
  `target:65500:100`. The RTs are explicit because auto-derived RTs use the global AS, which differs
  per node. The LANs are advertised as EVPN route type 5 (RFC 9136).

### It works: state after one gNMI Set per node

```text
br1# show network-instance default protocols bgp neighbor
| default | 10.254.0.1   | overlay       | S  | 65500 | established | evpn         | [1/1/1] |
| default | 10.255.1.0   | underlay-mpls | SB | 65000 | established | ipv4-unicast | [1/1/1] |
| default | 198.51.100.0 | underlay-isp  | SB | 65000 | established | ipv4-unicast | [2/0/2] |

br1# show network-instance corp ipv4 route
10.1.0.0/24    bgp-evpn  170  >  10.254.0.1(tunnel:vxlan, vni:100)
10.11.0.0/24   local     0    >  10.11.0.1(ethernet-1/3.0)

br1# show network-instance default ipv4 route   (hub VTEP)
10.254.0.1/32  bgp       170  >  10.255.1.0(route:local)          # via mpls
```

On the wire (tcpdump on the mpls bridge, h-br1 pinging h-hub):

```text
10.254.0.11.62443 > 10.254.0.1.4789: VXLAN, flags [I] (0x08), vni 100
  IP 10.11.0.10 > 10.1.0.10: ICMP echo request, id 60257, seq 2, length 64
10.254.0.1.59763 > 10.254.0.11.4789: VXLAN, flags [I] (0x08), vni 100
  IP 10.1.0.10 > 10.11.0.10: ICMP echo reply, id 60257, seq 2, length 64
```

### BFD runs at 1 s in the container, whatever you configure

`desired-minimum-transmit-interval 100000` / `required-minimum-receive 100000` (100 ms, in µs) are
accepted. The negotiated session runs at 1 s anyway. Each node advertises a 1 s receive interval
despite its own 100 ms config:

```text
br1# info from state bfd network-instance default peer 16385
    session-state UP
    remote-minimum-receive-interval 1000000
    active-transmit-interval 1000000
    active-receive-interval 1000000
    remote-multiplier 3
```

Bouncing BFD on both ends didn't change it. The YANG carries a `must '. >= 1000000'` ("Minimum value is
1000000 on this platform", "On vSRL instances this must be set to a value of 1000000 or greater"), but
that `must` is gated on the `vsrl` feature, which isn't in this node's feature list. So the 1 s floor
was **observed but not root-caused**. Treat it as a property of the container image. Detection time in
the lab is 3 × 1 s, and `detection-multiplier` can't go below 3.

### Failover (T-03 shape): BGP moves to isp, the overlay session stays up

The test: a 100 pps ICMP stream from h-br1 to h-hub (`ping -i 0.01 -D -O`, ~89 pps achieved). Then
`ip link set eth2 down` on the mpls bridge, which kills the hub side and leaves br1's port up.

| run | lost packets | loss window (last reply → first reply) | notes |
| --- | --- | --- | --- |
| 1 | 259 / 4500 | **3.00 s** (751.218 → 754.219) | No in-tunnel BGP configured |
| 2 | 198 / 3000 | **2.25 s** (239.43 → 241.68) | With the in-tunnel eBGP+BFD session below |

The window sits between 2 and 3 s depending on BFD phase, which is 3 × 1 s detection. After the failure
(`srlinux_v26_3_1_wan_br1_get_mpls_failed.json`):

```text
| default | 10.254.0.1   | overlay       | S  | 65500 | established | 0d:0h:7m:0s | evpn |   # never dropped
| default | 10.255.1.0   | underlay-mpls | SB | 65000 | connect     | -           |      |
| default | 198.51.100.0 | underlay-isp  | SB | 65000 | established | 0d:0h:7m:5s |      |
10.254.0.1/32  bgp  170  >  198.51.100.0(route:local)                                   # now via isp
ethernet-1/1 is up                                                                      # only BFD saw it
```

The EVPN overlay session (multihop between loopbacks) **didn't flap** in either run. Only the underlay
next-hop to the hub VTEP changed.

### Recovery (T-04 shape): zero loss, reverts to primary

After `ip link set eth2 up`, a 4500-packet stream lost **0** packets. Traffic went back to mpls once the
mpls session re-established, which is one path change. The wait is set by BGP `connect-retry`, default
**120 s**. On run 1 the session came back ~34 s after the link did, depending on where the retry timer
was. Set `timers connect-retry` low in intent if T-04 times recovery.

### eBGP + BFD *inside* the tunnel: works, but races the underlay

To answer Q2 literally, each node got a loopback `lo1` in `corp` (172.16.0.0/32, 172.16.0.1/32). The
EVPN type-5 routes carry the loopbacks over VXLAN, and an eBGP multihop session runs between them
(65000 ↔ 65011, `multihop maximum-hops 3`, BFD on `lo1.0`, import/export reject-all, so it is a
liveness session only):

```text
br1# show network-instance corp protocols bgp neighbor
| corp | 172.16.0.0 | in-tunnel | SB | 65000 | established | ipv4-unicast | [0/0/0] |
br1# info from state bfd network-instance corp peer *
        local-address 172.16.0.1   remote-address 172.16.0.0   session-state UP
        active-transmit-interval 1000000
```

During failover run 2 this session **dropped and re-established once**: `established-transitions` went
to 2, with `last-notification Cease / Connection Collision Resolution`, about 2 s after the failure.
In-tunnel BFD and underlay BFD have the same 3 s detection, so the inner session races the underlay
reconvergence. An in-overlay liveness session only makes sense with timers clearly slower than the
underlay's.

## 3. gNMI Set: all of it, one transaction per node

Each node got **two** gNMI Sets: the base overlay, then the in-tunnel session. Each was a single
`gc.set(update=[("/", payload)])` with pygnmi's default encoding, which is exactly what `GnmiExecutor`
sends. Unprefixed JSON was accepted, augmented containers (`bgp-evpn`, `bgp-vpn`) included. The
`network-instance corp` → `vxlan1.100` reference resolved because the `tunnel-interface` was in the same
commit. Payloads: `srlinux_v26_3_1_wan_{hub1,br1}_set_{overlay,in_tunnel_bgp}.json`.

Done outside gNMI, on purpose or for diagnosis:
- One `sr_cli` BFD admin-state bounce while investigating the 1 s floor. It restored the same config.
- Link failures (`ip link` on the bridge containers).
- netem.

Config paths the WAN templates will need. None of them exist in today's `dcfabric` templates, which
render only `/interface` and default-NI BGP neighbors:

| purpose | path |
| --- | --- |
| VTEP address | `/interface[name=system0]/subinterface[index=0]/ipv4/address[ip-prefix=…/32]` + `/network-instance[name=default]/interface[name=system0.0]` |
| VXLAN tunnel | `/tunnel-interface[name=vxlan1]/vxlan-interface[index=100]/{type=routed, ingress/vni}` |
| BFD per underlay | `/bfd/subinterface[id=ethernet-1/1.0]/{admin-state, desired-minimum-transmit-interval, required-minimum-receive, detection-multiplier}` (µs) |
| Underlay preference | `/routing-policy/prefix-set[name]/prefix[ip-prefix][mask-length-range]`, `/routing-policy/policy[name]/statement[name]/{match/prefix/prefix-set, action/policy-result, action/bgp/local-preference/{operation=set, value}}`, `default-action/policy-result` |
| Underlay eBGP + BFD | `/network-instance[name=default]/protocols/bgp/group[group-name]/{import-policy, export-policy, failure-detection/{enable-bfd, fast-failover}, afi-safi[afi-safi-name]}` |
| EVPN overlay | `…/bgp/afi-safi[afi-safi-name=evpn]`, `…/bgp/group[group-name=overlay]/{peer-as, local-as/as-number, transport/local-address}` |
| Service VRF | `/network-instance[name=corp]/{type=ip-vrf, interface[name], vxlan-interface[name=vxlan1.100]}`, `…/protocols/bgp-evpn/bgp-instance[id=1]/{vxlan-interface, evi}`, `…/protocols/bgp-vpn/bgp-instance[id=1]/route-target/{export-rt, import-rt}` |
| In-tunnel session | `/network-instance[name=corp]/protocols/bgp/group[group-name]/multihop/{admin-state, maximum-hops}`, `/bfd/subinterface[id=lo1.0]` |

State paths a WAN collector or verifier would read, all captured in the fixtures:

- `/network-instance[name=default]/protocols/bgp/neighbor` (`session-state`, `established-transitions`)
- `/bfd/network-instance[name=default]/peer` (`session-state`, `active-transmit-interval`, `local-diagnostic-code`)
- `/network-instance[name=default]/route-table/ipv4-unicast/route[ipv4-prefix=<hub VTEP>/32]` (which underlay is active)
- `/network-instance[name=corp]/route-table` (VXLAN next-hops)
- `/tunnel-interface[name=vxlan1]`
- `/system/features`

GET `json_ietf` returns module-prefixed keys and identities, for example
`srl_nokia-ip-route-tables:ipv4-unicast` and `"type": "srl_nokia-network-instance:ip-vrf"`, as the
existing collector fixtures already show.

## 4. netem works on SR Linux interfaces

```text
$ containerlab tools netem set -n clab-wan-spike-br1 -i e1-1 --delay 250ms --loss 5
WARN failed to load sch_netem kernel module (expected on OrbStack machines)
│ e1-1 │ 250ms │ 0s │ 5.00% │ 0 │ 0.00% │

$ docker exec clab-wan-spike-h-br1 ping -i 0.05 -c 200 -q 10.1.0.10      # traffic on mpls
200 packets transmitted, 193 received, 3.5% packet loss
rtt min/avg/max/mdev = 250.153/250.911/263.255/0.954 ms
```

The warning is harmless. The impairment applies and `netem show` lists it. Use the SR Linux interface
name in its Linux form (`e1-1`, not `ethernet-1/1`). netem shapes **egress only**, so 250 ms on br1's
`e1-1` adds 250 ms RTT and drops in the br1→hub direction only. Put it on both ends, or on the bridge,
for a symmetric brownout.

The **T-05 gap reproduced live.** Under 250 ms / 5 % both underlay sessions and both BFD sessions stayed
up, and the hub VTEP route stayed on mpls. Plain BGP didn't move traffic off the degraded path.
`netem reset` restored ~0.2 ms RTT with 0 % loss.

## 5. Recommendation

**Use EVPN-VXLAN in an ip-vrf (EVPN route type 5) as the WAN overlay, with one eBGP + BFD session per
underlay carrying the `system0` VTEP addresses.** It is the only overlay SR Linux can build on the
platform snapl pins. It is gNMI-configurable through the existing Executor call, and in the lab it
failed over and back correctly. The overlay session survived the failover.

Changes to `docs/wan-prototype-companion.md` (for the spec, not made in this spike):

| companion doc says | becomes |
| --- | --- |
| "each branch builds two tunnels to hub1, one over each underlay. Each tunnel has its own eBGP session with BFD" | Each branch has **one** EVPN-VXLAN overlay to hub1 (VTEP = `system0`) and **two underlay eBGP+BFD sessions**, one per underlay. The underlay route to the hub VTEP `/32` decides which underlay carries the tunnel |
| `overlay.tunnel: gre` | `overlay.tunnel: evpn-vxlan` (VNI, EVI, RT become intent) |
| Tunnel endpoints `10.254.0.0/24` | Same range, now the `system0` VTEP addresses |
| Overlay tunnel interfaces `172.16.0.0/24` as /31s | No tunnel interfaces exist. Drop the range, or keep it for optional in-VRF loopbacks |
| `underlays.mpls.preference: primary` | Rendered as an import policy setting local-preference per underlay group |
| T-01 "2 BGP sessions Established per branch … traceroute takes the primary tunnel" | Still 2 underlay sessions per branch, plus 1 EVPN session. Traceroute can't see the underlay inside VXLAN, so check "on primary" with the hub-VTEP route's next-hop (gNMI state) |
| T-03 `failover_max_loss_ms: 2000`, BFD 100 ms × 3 | The container image runs BFD at 1 s, so lab loss is **2.25–3.0 s**. Either set the lab threshold to about 3500 ms and say why, or accept T-03 failing honestly. 100 ms BFD can't be shown in this lab |
| T-04 recovery | Works with 0 loss. Put a short `connect-retry` in intent so recovery time is predictable |
| T-05 brownout via netem | Works as written. Phase-2 steering lowers local-pref on the **VTEP route** learned over the browned-out underlay, which moves *all* overlay traffic to that hub, not one tunnel or application |
| Provider "clouds" as FRR routers | Keep them as Linux bridges (single-vendor, gives edge-to-hub BFD across each underlay) or make them SR Linux routers. If they route, BFD only covers the edge's local link and remote failures arrive as BGP withdrawals, which is slower |

The honest trade-off against GRE:
- **Lost:** per-underlay tunnels with their own routing adjacency. You can't run "BGP over tunnel A and
  tunnel B" and steer between two tunnels. Steering happens one layer down, on the route to the remote
  VTEP. That route is per hub, not per application or per tunnel.
- **Gained:** a standard, vendor-documented overlay (RFC 9136) with a single control-plane session
  that rides out underlay failures.

## Not verified

- **GRE forwarding on a 7250 IXR type.** It wasn't tried. It can't carry BGP or BFD by design (no
  interface, BFD forbidden by YANG), and it isn't snapl's platform.
- **The cause of the 1 s BFD floor.** Is it image-wide, or specific to the `ixr-d3` type? Other types
  were not tried.
- **Two branches.** Only 1 hub + 1 branch was built. Branch-to-branch via the hub needs either hub
  re-advertisement of type-5 routes or a hub route-reflector. Both untested.
- **MTU.** VXLAN adds 50 bytes. Only 84-byte pings were sent. Large-packet behaviour and SR Linux
  `ip-mtu` on the underlay are untested.
- **Throughput.** containerlab's SR Linux page says the unlicensed container datapath is limited to
  1000 PPS. The 100 pps test stream is well under that, but bulk-traffic tests would hit it. Not measured.
- **Recovery timing** as a distribution. One run each, and the 34 s figure depends on the connect-retry
  phase.
- **Encryption** (companion phase 2: WireGuard/IPsec). SR Linux has no YANG for either, so phase-2
  encryption would need something outside SR Linux. Open risk for the spec.
- **Multihop BFD for the EVPN overlay session itself.** Not configured. Only the in-VRF session was tested.

## References

- Nokia SR Linux 26.3 *Network-instances* (GRE tunnel next-hop groups, IP tunnel decapsulation groups):
  <https://documentation.nokia.com/srlinux/26-3/books/config-basics/network-instances.html>
- Nokia SR Linux 22.3 *EVPN-VXLAN guide*, VXLAN tied to EVPN, system0 source VTEP. Quoted from this
  page; the 26.3.1 YANG agrees:
  <https://documentation.nokia.com/srlinux/22-3/SR_Linux_Book_Files/EVPN-VXLAN_Guide/services-vxlan.html>
- containerlab SR Linux kind (license and datapath limits): <https://containerlab.dev/manual/kinds/srl/>
- YANG in `ghcr.io/nokia/srlinux:26.3.1`:
  - `srl_nokia/models/network-instance/srl_nokia-next-hop-groups.yang`
  - `srl_nokia/models/tunnel/srl_nokia-tunnel-interfaces.yang`
  - `srl_nokia/models/bfd/srl_nokia-bfd.yang`
  - `srl_nokia/models/common/srl_nokia-{common,features}.yang`
