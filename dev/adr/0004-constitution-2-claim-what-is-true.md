# ADR-0004: Constitution 2.0.0 — Claim Only What Is True

## Status

Accepted

## Date

2026-10-25

## Context

Constitution v1.1.0 was written before most of snapl existed. Three months of building the closed loop against a live SR Linux fabric pulled the project away from it in five ways:

- **Scope.** It advertises four use cases (datacenter fabric, client edge, SD-WAN, WAN). Only `dcfabric` has seed data, templates and a lab topology. `test_edge` is a test fixture.
- **Claims.** The vision promises "production-grade automation patterns" and Principle VI a "driver pattern" so new vendors need no core changes. No driver layer exists. The workflows import SR Linux YANG paths directly (#119), templates are keyed by use case rather than platform, and nothing dispatches on `device.platform`. Merge-only apply (#65) means convergence holds for additions and changes but not removals.
- **Contracts.** Principle V lists 11 ABC methods. The four block ABCs now have 16 (`provision_schema`, `delete_device` and three `*_batch` methods are missing), and `emit_event` takes a `DriftReport`, not an event. A list of signatures in a governing document goes stale with every interface change.
- **Process.**
  - Principle II says no module is implemented without a specification. In practice specs were written for each block and for one risky capability (007, undesired config), and the many fixes since were driven by issues with reproductions. That was the right call, but it broke a "non-negotiable" rule.
  - Principle III treats unit coverage as the quality bar. Of the last 40 commits before this ADR, 23 were `fix:`, and most of those bugs (real Infrahub response shapes, gNMI path keying, async schema registration, MTU on loopbacks) were found on live lab runs, not by the mocked unit suite.
- **Governance.** It requires "review and approval" and "all PRs and code reviews must verify compliance", the language of a team. snapl has one maintainer, and the constitution's main readers are Spec Kit and AI coding agents.

The repository is public and about to be linked from a write-up. Readers will check its claims against the code, so every claim it makes must hold.

## Decision

Rewrite the constitution as **v2.0.0**. Every change below was settled in a structured review on 2026-10-25.

### Vision and scope

- The vision states one demonstrated claim: a closed intent → deploy → verify → drift → reconcile loop on a real SR Linux fabric, built as six NAF blocks.
- "Production-grade" is removed.
- **Datacenter fabric** is the only current use case.
- **Site edge** (#117) is the next one: a manufacturing site with an OT segmentation rule as intent. It replaces the unbuilt "Client Edge" entry.
- SD-WAN and WAN backbone are listed as roadmap, not started.

### Principle I — NAF Alignment

"Every component maps to exactly one NAF block" becomes "every component has one owning block; deviations are recorded in an ADR". Two deviations are recorded here:

1. **Durable audit lives in the Orchestrator.** NAF places "persists historical data" in Observability, and `Observer.log_audit` and a bounded in-memory log live there. The durable `AuditLog` ABC and its SQLite implementation live in `snapl_orchestrator.audit`, because the audit trail is written per workflow step and must survive worker restarts alongside Temporal history.
2. **The SR Linux collect-to-diff adapter lives in the Orchestrator.** `adapters/srlinux.py` translates Collector output into the shape the Observability diff expects. The diff's docstring assigns that translation to "the caller (typically the Orchestrator)". Moving it is not worth doing until a second vendor needs a second adapter.
3. **Presentation reads the Source of Truth directly.** The CLI resolves operator-supplied device names to UUIDs by building an `InfrahubIntentStore` in `snapl_presentation/resolve.py` (spec 006 FR-007), instead of going through a workflow. A Temporal round-trip for a read-only lookup would add latency and a failure mode for no durability benefit. `snapl-presentation` declares only `snapl-orchestrator` and gets `snapl-intent` transitively.

The dependency list is corrected to match the code. Collector and Observability both depend on `snapl-intent`, which v1.1.0 omitted. A rule is added that any package may import another block's models and exceptions, since they are shared data contracts. Presentation already imports `DriftStatus` and `IntentError` this way.

### Principle II — Spec-Driven Development

A specification is required for:

- a new NAF block,
- a new capability, or
- any change that writes to a device or to the Source of Truth in a way the system did not before.

Fixes, hardening and refactors need an issue with a reproduction instead. By this rule, 007 (undesired config) needed a spec and #115 (SoT segregation) did not.

### Principle III — Test-Driven Development

TDD stays: test first, red-green-refactor. What changes is what counts as evidence of correctness:

- **Live evidence is the primary gate for boundary changes.** A PR that touches a system boundary (Infrahub response shapes, gNMI payloads, templates, the drift diff, the collect adapter) states in its body what was run against the live lab and what was observed. CI cannot run the lab, so this is a written obligation, not an automated check.
- **Bugs found live leave a fixture behind.** Every bug found on a live run adds a captured real payload as a test fixture, so the unit suite learns the boundary's true shape.
- **Coverage is a regression net, not a correctness measure.** The 80% floor stays, enforced in CI.

### Principle V — Contract-First Interfaces

Method signatures are removed. The rule stays: each block exposes one ABC plus Pydantic models, and the Orchestrator composes blocks through those ABCs. Each package's `abc.py` is the source of truth for its contract.

### Principle VI — Single Vendor, Named Seams

The multi-vendor ambition is kept as a direction. The claim is gated on proof, and the abstraction waits for the second vendor that would justify it (Principle VII):

- snapl is single-vendor (Nokia SR Linux) today and built to take a second.
- Vendor-specific code lives only in named seams:
  - concrete block implementations (`GnmiExecutor`, `GnmiCollector`),
  - Jinja templates,
  - the drift field map,
  - the collect-to-diff adapter.
- Workflows and models never name a vendor. The two current violations are tracked in #119.
- snapl claims to be multi-vendor only once a second vendor runs the full loop in the lab.

**Open question, deliberately not decided:** how multi-vendor works when it comes. One option is per-vendor adapters and template sets dispatched on `device.platform`. The other is OpenConfig as the shared model, which SR Linux can serve but which would change how snapl talks to its one vendor today. This is decided when a second vendor is chosen, against a real second implementation.

### Principle VIII — Intent-First Correctness

A fifth rule is added, from spec 007:

> **Undesired config.** Configuration present on a device and absent from intent is drift, and it is reported. Removing it is a destructive change: never automatic, always preceded by `dry_run()`, and never applied to the protected set (management and system interfaces).

That removal is not implemented yet (#65) is a limitation, so the README states it and the constitution does not.

### Governance

- The constitution is the standing brief for AI agents and for the maintainer.
- An amendment needs an ADR, a version bump and a PR the maintainer merges.
- "Review and approval" and the compliance-review clause are removed.

### Factual corrections

- Test markers: `pre_deployment` and `post_deployment` were declared but never used. They are removed from the constitution and from `pyproject.toml`.
- CI quality gates run as parallel jobs behind a change filter, not as a sequential pipeline.
- Specs live in `specs/`, not `.specify/specs/`.
- `development/` holds the Docker Compose stack only. There is no monitoring stack (#101).

## Consequences

### Positive

- Every claim in the constitution, README and AGENTS.md can be checked against the code and holds.
- Agents generating specs and plans inherit a scope (dcfabric, then site edge) and the seam rule instead of re-deriving them or extending the multi-vendor fiction.
- The spec trigger in Principle II is something a reviewer can apply, so the rule can be followed instead of routinely and sensibly skipped.
- The live-evidence rule names the practice that actually caught most bugs, and the fixture rule feeds those findings back into the unit suite.

### Negative

- The live-evidence gate depends on discipline. CI cannot enforce it until a lab can run in CI.
- Older specs (001–005) and ADR-0001 still cite four use cases and the v1.1.0 wording of Principle VI. They are left as written, as historical records. Where they conflict with this ADR, this ADR wins.
- #119 must land before Principle VI is true in the code.
