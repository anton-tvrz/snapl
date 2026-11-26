# dcfabric Containerlab topology

The lab the snapl quickstarts and integration tests run against: a 2-spine /
4-leaf Nokia SR Linux fabric whose node names, device types, and cabling match
the SoT seed data in `packages/intent/snapl_intent/seed/dcfabric/topology.yml`.

```text
            spine-01 (IXR-D3)        spine-02 (IXR-D3)
            e1-1..e1-4               e1-1..e1-4
              │ │ │ │                  │ │ │ │
   ┌──────────┘ │ │ └─────────┐   ┌────┘ │ │ └─────────┐
   │        ┌───┘ └───┐       │   │   ┌──┘ └──┐        │
 e1-49    e1-49     e1-49   e1-49 e1-50     e1-50    e1-50/e1-50
 leaf-01  leaf-02   leaf-03 leaf-04  (each leaf: e1-49 → spine-01,
 (IXR-D2, one uplink to each spine)   e1-50 → spine-02)
```

## Deploy / destroy

No native containerlab install is needed — the invoke tasks run containerlab
Docker-outside-of-Docker via `ghcr.io/srl-labs/clab`:

```bash
uv run invoke dev.lab-deploy
uv run invoke dev.lab-destroy
```

If you have the containerlab binary installed, the direct equivalent is:

```bash
cd containerlab && sudo containerlab deploy -t dcfabric.yml
cd containerlab && sudo containerlab destroy -t dcfabric.yml
```

## Access and credentials

| What | Value |
| --- | --- |
| Hostnames | `clab-dcfabric-spine-01` … `clab-dcfabric-leaf-04` (added to `/etc/hosts` by containerlab) |
| CLI | `docker exec -it clab-dcfabric-spine-01 sr_cli` |
| Username / password | `admin` / `NokiaSrl1!` (SR Linux default) |
| gNMI | port `57400`, **plaintext** (no TLS) |

gNMI is deliberately insecure: the snapl executor/collector connect with
`insecure=True`, so `srlinux-insecure-gnmi.partial.cfg` strips containerlab's
generated TLS profile from the `mgmt` grpc-server. Lab use only — don't reuse
this config anywhere reachable.

## Device addressing: how snapl dials the lab (#30/#31, #90, #96)

The seed `management_ip` values (`10.0.0.x/24`) are **intent data only** —
router-id, loopback address, documentation. They deliberately do *not* match
the containerlab management network, and pinning them there would collide with
docker's `10.0.0.1` gateway default.

The dial target is `lab_node_name`: the executor and collector resolve
`device.lab_node_name` first, falling back to `device.management_address` and
then to the configured host. Each dcfabric device is seeded with its **static
management address** from `dcfabric.yml`:

| node | dial target | node | dial target |
| --- | --- | --- | --- |
| spine-01 | `172.20.21.11` | leaf-02 | `172.20.21.14` |
| spine-02 | `172.20.21.12` | leaf-03 | `172.20.21.15` |
| leaf-01 | `172.20.21.13` | leaf-04 | `172.20.21.16` |

These are addresses rather than `clab-dcfabric-<name>` hostnames because
containerlab writes those hostnames into the Linux VM's `/etc/hosts`, not
macOS's — so a host-side worker cannot resolve them, and every apply and
collect fails until the SoT is hand-patched (Issue #96). The pins are stable
across redeploys (Issue #90) and OrbStack routes `172.20.21.0/24` directly.

> `dcfabric.yml` and the seed's `lab_node_name` values are one address
> registry split across two files. Change both together and re-seed;
> `tests/unit/test_containerlab/test_topology.py` fails if they diverge.

## Running the integration tests against the lab

```bash
SRLINUX_PASSWORD='NokiaSrl1!' uv run pytest tests/integration/ -m integration  # pragma: allowlist secret
```

Host, port, and username default to `clab-dcfabric-spine-01`, `57400`, and
`admin`; override with `SRLINUX_HOST` / `SRLINUX_PORT` / `SRLINUX_USERNAME`.
Tests skip automatically when no SR Linux node is reachable.

### macOS (OrbStack)

`SRLINUX_HOST` is only the *fallback* dial target — SoT-driven runs use the
seeded addresses above and need nothing special. It matters for the integration
tests, which default to the `clab-dcfabric-spine-01` hostname. Containerlab
writes those host entries inside the Linux VM, not into macOS's `/etc/hosts`,
so from the Mac point it at the node's address instead:

```bash
SRLINUX_HOST=172.20.21.11 \
  SRLINUX_PASSWORD='NokiaSrl1!' uv run pytest tests/integration/ -m integration  # pragma: allowlist secret
```

Or use OrbStack's container DNS with gRPC's OS resolver (its default c-ares
resolver can't see `.local` names):

```bash
GRPC_DNS_RESOLVER=native SRLINUX_HOST=clab-dcfabric-spine-01.orb.local \
  SRLINUX_PASSWORD='NokiaSrl1!' uv run pytest tests/integration/ -m integration  # pragma: allowlist secret
```

## Quirks worth knowing

- `dev.lab-deploy` strips the TLS profile from each node's `mgmt` grpc-server
  after deploy: containerlab's partial-startup-config mechanism does not
  reliably apply it in the dockerized setup, so the task enforces it (it's a
  no-op when already plaintext). If you deploy with a native containerlab
  binary and gNMI answers TLS instead of plaintext, run the same strip
  manually:
  `docker exec clab-dcfabric-<node> sr_cli -ec 'delete / system grpc-server mgmt tls-profile'`

## The site_edge lab (spec 008)

A second lab, `containerlab/site_edge.yml`, for the site_edge use case. It runs
beside dcfabric on its own bridge, `clab-snapl-site-edge` on `172.20.22.0/24`:

```text
 office-host ── e1-2 ┐                       ┌ e1-2 ── historian   (10.50.10.0/24, allowed)
                     site-edge-01 ── e1-1 ── carrier-a
     ot-host ── e1-3 ┘   (WAN, eBGP)         └ e1-3 ── wan-host    (10.50.99.0/24)
```

| node | kind / type | mgmt pin | role |
| --- | --- | --- | --- |
| `site-edge-01` | SR Linux `ixr-d2` | `172.20.22.11` | site edge: office on e1-2, OT on e1-3, WAN on e1-1 |
| `carrier-a` | SR Linux `ixr-d2` | `172.20.22.21` | carrier, standing in for the underlay |
| `ot-host` | linux | `172.20.22.31` | `10.40.20.10/24`, via `10.40.20.1` |
| `office-host` | linux | `172.20.22.32` | `10.40.10.10/24`, via `10.40.10.1` |
| `historian` | linux | `172.20.22.33` | `10.50.10.10/24`, via `10.50.10.1` |
| `wan-host` | linux | `172.20.22.34` | `10.50.99.10/24`, via `10.50.99.1` |

The routers use the same pinned SR Linux image as dcfabric. The hosts run
`ghcr.io/srl-labs/network-multitool`, pinned by digest. They are traffic
endpoints, not intent devices: containerlab sets their address and default
route, and snapl never configures them.

```bash
uv run invoke dev.lab-deploy --use-case site_edge
uv run invoke dev.lab-destroy --use-case site_edge
uv run invoke demo.up --use-case site_edge   # needs the site_edge seed (spec 008 PR F)
```

`dev.lab-deploy` strips gNMI TLS on the two routers only. The capture tooling
for spec 008 is `dev/scripts/site_edge_capture.cli` and
`dev/scripts/capture_srlinux_acl_state.py`.
