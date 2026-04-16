# snapl

NAF-aligned network automation prototype with Spec-Driven Development.

## Overview

snapl implements the [Network Automation Forum (NAF)](https://networkautomation.forum/) Framework's six building blocks as independent, composable Python packages. It demonstrates production-grade automation patterns across multiple network use cases using Nokia SR Linux.

### NAF Building Blocks

| Block | Package | Description |
|-------|---------|-------------|
| Intent | `snapl-intent` | Source of Truth (Infrahub), desired state models, schemas |
| Executor | `snapl-executor` | Config deployment via gNMI, Jinja2 templates |
| Collector | `snapl-collector` | Live network data retrieval |
| Observability | `snapl-observability` | Drift detection, metrics, audit logging |
| Orchestrator | `snapl-orchestrator` | Temporal workflow coordination |
| Presentation | `snapl-presentation` | CLI / API interface |

### NAF Feedback Loop

```
Intent (desired state)
  -> Executor (deploy config)
    -> Collector (verify state)
      -> Observability (detect drift)
        -> Orchestrator (coordinate response)
          -> back to Executor
```

### Use Cases

- **Datacenter Fabric** — Spine-leaf eBGP underlay (primary)
- **Client Edge** — Customer-facing edge provisioning
- **SD-WAN** — Hub/spoke overlay tunnels
- **WAN** — Backbone traffic engineering

## Quick Start

```bash
# Install dependencies
uv sync --all-groups

# Run quality checks
uv run invoke lint
uv run invoke format

# Run tests
uv run invoke test-unit

# View all tasks
uv run invoke --list
```

## Development

This project uses two complementary methodologies:

- **[Spec-Driven Development](https://github.com/github/spec-kit)** — Constitution -> Specify -> Plan -> Tasks -> Implement
- **Test-Driven Development** — Red-Green-Refactor, 80%+ coverage

See [AGENTS.md](AGENTS.md) for full project context and [CLAUDE.md](CLAUDE.md) for Claude Code quick reference.

## Lineage

snapl builds on patterns from [project-network-synapse-quattro](https://github.com/anton-tvrz/project-network-synapse-quattro).
