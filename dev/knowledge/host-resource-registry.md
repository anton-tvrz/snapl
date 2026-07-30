# Host Resource Registry — ports and subnets snapl owns

> **Rule:** snapl never binds a conventional default port and never uses a
> shared bridge subnet. It owns a dedicated block of each, recorded here.
> If you add a service, take the next free port **from snapl's block** and add
> it to the table below in the same commit.

## Why this exists

snapl shares a developer machine with sibling projects that run
near-identical stacks — Infrahub, Temporal, Neo4j, RabbitMQ, Grafana. They all
default to the same well-known ports, and containerlab labs all default to the
same bridge. Two collisions have already happened:

- **Issue #90** — snapl's lab shared containerlab's default `172.20.20.0/24`
  bridge with `project-network-synapse-quattro`. Docker hands out addresses
  dynamically, so the labs interleaved and one project's SoT address could
  resolve to the other's SR Linux nodes. Same default credentials, plaintext
  gNMI.
- **Issue #107** — snapl's committed default ports (Infrahub 8000, Temporal
  7233) are also quattro's. A schema provision aimed at "localhost:8000"
  reached *quattro's* Infrahub and tried to load snapl's schema into it.
  Infrahub rejected the payload whole, so nothing was damaged — but against an
  empty instance on that port it would have succeeded, and snapl would have
  seeded six devices into a foreign Source of Truth.

Both were near-misses caused by the same mistake: **treating "it responds" as
"it is ours."** A dedicated block makes the collision impossible rather than
unlikely.

## Port registry

snapl owns **18000–18099**. Each service keeps a mnemonic tail from its
conventional port, so the mapping is readable without this table.

| service | snapl port | conventional | env var |
| --- | --- | --- | --- |
| Infrahub API / UI | **18000** | 8000 | `INFRAHUB_PORT` |
| Temporal gRPC | **18033** | 7233 | `TEMPORAL_PORT` |
| Temporal Web UI | **18034** | 8233 | `TEMPORAL_UI_PORT` |
| RabbitMQ management | **18015** | 15672 | `RABBITMQ_MGMT_PORT` |
| Grafana *(reserved, #101)* | **18030** | 3000 | `GRAFANA_PORT` |
| Prefect / task-manager | **18042** | 4200 | `PREFECT_PORT` |
| RabbitMQ | **18072** | 5672 | `RABBITMQ_PORT` |
| Neo4j HTTP | **18074** | 7474 | `NEO4J_HTTP_PORT` |
| Redis | **18079** | 6379 | `REDIS_PORT` |
| Neo4j bolt | **18087** | 7687 | `NEO4J_BOLT_PORT` |
| Prometheus *(reserved, #101)* | **18090** | 9090 | `PROMETHEUS_PORT` |

The two reserved rows are claimed ahead of use: #101 proposes adding Prometheus
and Grafana, and *their* defaults (9090, 3000) are already taken by neighbours
too. Claiming them now avoids relitigating this later.

These are the committed compose defaults — a fresh clone is correct with no
`.env` at all. `development/.env` remains available for further overrides, but
it is no longer load-bearing for collision avoidance.

## Subnet registry

One `/24` per lab, on a dedicated bridge (issue #90):

| subnet | bridge | project / lab |
| --- | --- | --- |
| 172.20.20.0/24 | `clab` (default) | project-network-synapse-quattro — spine-leaf-lab |
| **172.20.21.0/24** | **`clab-snapl`** | **snapl — dcfabric** |

snapl's node pins live in `containerlab/dcfabric.yml` and are seeded as each
device's `lab_node_name` (issue #96). Those two files are one address registry
split across two places; `tests/unit/test_containerlab/test_topology.py` fails
if they diverge.

> On OrbStack all container bridges are mutually routable, so this is address
> isolation, not L3 isolation. The guarantee is "no collision", not "no route".

## Known neighbours

Surveyed 2026-07-29 across `~/Documents/PYPROJECTS`. Not exhaustive and not
snapl's to manage — recorded so the next person choosing a port knows what is
already crowded.

| port | claimed by |
| --- | --- |
| 3000, 9090 | quattro, synapse-3 (Grafana, Prometheus) |
| 3100, 8086, 5514/udp | quattro (Loki, InfluxDB, syslog) |
| 4200 | quattro (Prefect) |
| 5672, 15672 | quattro, synapse-3, synapse, synapse-gcp-base |
| 6379 | quattro, synapse-3 |
| 7233 | quattro, synapse-3, synapse, synapse-gcp-base, NetAutoStack, netautostack_gcp |
| 7474, 7687 | quattro, synapse, synapse-gcp-base |
| 8000 | quattro, synapse-3, synapse-gcp-base, netautostack_gcp |
| 8001 | synapse-gcp-base |
| 8002, 8081 | synapse |
| 8080 | quattro, synapse-3, synapse |
| 8233 | synapse-gcp-base, NetAutoStack, netautostack_gcp |
| 8530, 8531 | quattro, synapse-3 |

**This is why a `+1` offset is not a fix.** snapl's old advice was to offset
Infrahub to 8001 — which is synapse-gcp-base's. Incrementing walks into the
next project; a distinct block does not.

## Checking before you assume

```bash
docker ps --format '{{.Names}}\t{{.Ports}}'          # who is running right now
lsof -nP -iTCP -sTCP:LISTEN | awk 'NR>1{print $9}'   # what is bound on the host
uv run invoke demo.check                             # is *snapl's* stack up and ours
```

`demo.check` and `snapl status` verify identity, not just reachability: an
Infrahub holding devices but lacking snapl's `use_case` attribute is reported
as someone else's, and the end-to-end suite refuses to seed into it.
