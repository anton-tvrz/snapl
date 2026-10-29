# snapl

A closed network automation loop (**intent → deploy → verify → drift → reconcile**) running against a real Nokia SR Linux fabric, built as the [Network Automation Forum (NAF)](https://networkautomation.forum/) Framework's six building blocks.

## What it does

The Source of Truth (Infrahub) holds what the network is supposed to look like. snapl renders that intent, pushes it to the devices over gNMI, reads the devices back, and compares the two.

When they disagree, that's drift. snapl reports it, and an operator can reconcile the device back to intent. Every step runs as a durable Temporal workflow and lands in an audit trail that survives restarts.

What this demonstrates, live, on a six-node spine-leaf fabric in Containerlab:

- **Deploy and verify.** `snapl deploy spine-01` renders intent, applies it, then collects the device's state and checks that the change actually took.
- **Drift detection in both directions.** `snapl scan` flags config the device is *missing* and config the device carries that intent *never asked for*: a hand-added interface address or BGP neighbour.
- **Reconcile.** `snapl reconcile` re-applies intent to drifted devices. Remediation is always operator-triggered, never automatic.
- **Durability.** Workflows survive a worker restart. `snapl audit` reads the trail by workflow or by device.

## NAF building blocks

| Block | Package | What it does here |
|-------|---------|-------------------|
| Intent | `snapl-intent` | Infrahub schema, seed data, desired-state models |
| Executor | `snapl-executor` | Jinja2 rendering + gNMI apply and dry-run |
| Collector | `snapl-collector` | gNMI GET of live device state |
| Observability | `snapl-observability` | Structural drift diff, drift events |
| Orchestrator | `snapl-orchestrator` | Temporal workflows: deploy, scan, reconcile; durable audit |
| Presentation | `snapl-presentation` | The `snapl` operator CLI |

## Scope

- **Datacenter fabric (current):** spine-leaf eBGP underlay, 2 spines and 4 leaves.
- **Site edge (next, #117):** a manufacturing site edge with an OT segmentation rule as intent.
- **Roadmap, not started:** SD-WAN, WAN backbone.

Single vendor: Nokia SR Linux. Vendor-specific code is confined to a few named places (gNMI implementations, templates, the drift field map, one collect adapter), so a second vendor has somewhere to go. snapl doesn't claim multi-vendor support until a second vendor runs the full loop.

## Known limitations

- **Removal is not implemented (#65).** Apply is a merge, so config deleted from intent stays on the device. snapl *detects* that undesired config and reports it, but does not remove it. Convergence is true for additions and changes.
- **Self-healing is opt-in, per use case.** `demo.up` creates a report-only drift schedule (every 5 minutes); scheduled repairs happen only after `snapl schedule mode remediate`. A scheduled repair never removes config (#65), and a failed repair is retried every run until an operator switches back to report (#136).
- **No metrics or dashboards (#101).** Drift and audit are visible through the CLI and the Temporal Web UI only.
- **Config-level verification only.** Intent describes configuration. Operational expectations (BGP sessions established, interfaces oper-up) are not yet checked.

## Quick start

Needs Docker (OrbStack on macOS) and uv.

```bash
uv sync --all-groups
cp development/.env.example development/.env
uv run invoke demo.up             # stack + SR Linux fabric + seeded SoT + drift schedule + preflight
uv run invoke orchestrator.start  # the worker, in its own terminal

uv run snapl status
uv run snapl deploy spine-01
uv run snapl scan
uv run snapl schedule show        # the report-only drift schedule demo.up created
```

[`docs/demo-scenarios.md`](docs/demo-scenarios.md) walks through the full demo: deploy, inducing drift by hand, scanning, reconciling, and reading the audit trail.

## How it is built

Two methodologies govern the code:

- **[Spec-Driven Development](https://github.com/github/spec-kit).** Every block and every new capability starts as a spec in [`specs/`](specs/).
- **Test-Driven Development**, with one rule learned the hard way: mocks encode assumptions, so changes at a system boundary also carry evidence from a live lab run.

The governing rules are in the [constitution](.specify/memory/constitution.md); the reasoning behind them is in [`dev/adr/`](dev/adr/). [AGENTS.md](AGENTS.md) is the entry point for AI coding agents.

```bash
uv run invoke lint        # ruff
uv run invoke test-unit   # unit tests, no infrastructure needed
uv run invoke test-e2e    # the demo arc end to end, against a live stack
```

## Lineage

snapl builds on patterns from [project-network-synapse-quattro](https://github.com/anton-tvrz/project-network-synapse-quattro).
