# ADR-0003: Intent-First Correctness Principles

## Status

Accepted

## Date

2026-07-06

## Context

The constitution (v1.0.0) governs *how snapl is built* — SDD, TDD, modularity,
contract-first interfaces — but never states the system's *correctness
semantics*: which system is authoritative for what, and what "correct" means
for the closed NAF loop.

That gap has practical consequences. Resolving issues #30/#31 required an
ad-hoc addressing decision — seed `management_ip` values are intent data
(router-id, loopback) while the gNMI dial target is operational addressing
(`lab_node_name`, dynamic clab management network). The reasoning now lives in
`containerlab/README.md` rather than in the governing document that Spec Kit
consumes when generating future specs. Upcoming work makes the same questions
recur: #32 (collector→observer normalization) must decide what drift *means*,
and any reconcile policy must decide what happens when drift is found.

Sif Baksh's article ["Source of Truth: Intent vs Operational State"](https://sifbaksh.com/blog/source-of-truth-intent-vs-operational-state-network-automation/)
articulates the missing principles against the same NAF building blocks snapl
implements. The core insight: "Source of Truth" conflates two concepts that
must never be merged — **intent** (what the network is supposed to look like)
and **operational state** (what it actually looks like right now). Drift is
the gap between them, and it is daily reality, not an edge case.

snapl already *embodies* most of the article's mechanics (structured intent
behind an API, vendor-neutral models with separate rendering, dry-run
executor, drift detection as a first-class block). What is missing is the
principles being *stated*, so that future specs, plans, and reviews flow from
them instead of re-deriving them.

## Decision

Amend the constitution with a new Core Principle **VIII — Intent-First
Correctness** (version bump 1.0.0 → 1.1.0) containing four rules:

### 1. Authority split

The Source of Truth is authoritative for **intended state**; the live network
is authoritative only for **operational reality**. Device state is never
silently promoted to truth: no automatic reverse-sync of running config into
the SoT. Promoting observed state into intent (e.g. onboarding a brownfield
device) is an explicit, reviewed operation. A manual device change that
"works" is still drift.

Corollary: intent attributes and operational addressing are distinct even
when they look alike — e.g. `management_ip` is intent data consumed by
rendering; the executor/collector dial target is operational addressing
resolved at call time (ADR context: issues #30/#31).

### 2. Drift response is part of drift detection

Every drift-detection path has a **defined response**: `report` (default),
`remediate` (operator-triggered or explicitly automated), or `suppress`
(e.g. maintenance windows). Detecting drift without a defined response is an
incomplete feature. Auto-remediation is never the silent default.

### 3. Intent extends beyond configuration

Intent may declare **operational expectations** — BGP sessions established,
interfaces oper-up, thresholds — not only config. Verification compares
reality against intent; liveness alone never passes verification
("operationally up" ≠ "correct"). Config-structure drift (today's
`StructuralObserver`) is the first tier; operational-state validation is the
intended growth path for the Observability block.

### 4. Executor idempotency

`Executor.apply()` is idempotent: applying the same intent twice yields the
same device state with no additional change. Workflows may safely retry
applies. Destructive or uncertain changes go through `dry_run()` first.

## Consequences

### Positive

- Future specs and plans (Spec Kit reads the constitution) inherit the
  correctness model instead of re-deriving it per feature; #32 and the
  reconcile-policy work start from stated principles.
- Review checklists gain concrete questions: "does this promote device state
  to truth?", "what is the drift response here?", "is this apply idempotent?"
- The intent-vs-operational-addressing decision from #30/#31 is elevated from
  a lab README to a governing principle.

### Negative

- Constitution grows; principle VIII must be kept consistent with future
  ADRs (e.g. if a use case ever *wants* device-led onboarding, it needs an
  explicit reviewed workflow, which is more ceremony than a silent sync).
- Operational-expectation intent (rule 3) is aspirational until the
  Observability block grows beyond structural comparison — stated direction,
  not current capability.
