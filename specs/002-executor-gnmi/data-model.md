# Data Model: NAF Executor — gNMI Config Deployment

**Feature**: 002-executor-gnmi
**Date**: 2026-05-07
**Source**: Feature spec + research

## Overview

The Executor does not own a Source of Truth. Its data model describes the **operation request and result types** that flow across the Executor boundary. All network entity definitions (Device, Interface, BGPSession, DesiredState) are owned by `snapl_intent` and consumed read-only by the Executor.

---

## Input Type (consumed from snapl_intent)

### DesiredState

Imported from `snapl_intent.models`. The Executor's primary input.

| Field | Type | Description |
|-------|------|-------------|
| device | Device | Device identity and attributes (name, role, use_case, management_address) |
| interfaces | list[Interface] | All interfaces with IP addressing |
| bgp_sessions | list[BGPSession] | All BGP peering sessions |

The Executor reads this type but does not modify or persist it.

---

## Result Types (owned by snapl_executor)

### ApplyResult

Returned by `apply()` and `rollback()`.

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| device_id | UUID | required | Identity of the target device |
| device_name | str | required | Human-readable name (from DesiredState) |
| success | bool | required | Whether the gNMI SET succeeded |
| payload | dict | required | The rendered JSON payload that was sent |
| device_response | str \| None | optional | Device's raw response message (success or error) |
| error | str \| None | optional; present if success=False | Error description |
| is_rollback | bool | required; default False | True when this result is from a rollback() call |
| duration_ms | int | required | Wall-clock time of the gNMI operation in milliseconds |

**Invariant**: `success=True` implies `error=None`. `success=False` implies `error` is set.

### DryRunResult

Returned by `dry_run()`.

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| device_id | UUID | required | Identity of the target device |
| device_name | str | required | Human-readable name (from DesiredState) |
| success | bool | required | Whether template rendering succeeded |
| payload | dict \| None | optional; present if success=True | The rendered JSON payload that would be sent |
| render_error | str \| None | optional; present if success=False | Template render error description |

**Invariant**: A `DryRunResult` never represents a committed change. No gNMI connection is made.

### BatchResult

Returned by `apply_batch()`.

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| results | dict[UUID, ApplyResult] | required | Per-device outcome map |
| total | int | required | Total number of devices attempted |
| succeeded | int | required | Number of successful applies |
| failed | int | required | Number of failed applies |

**Derived**: `succeeded + failed == total`. No exception raised for partial failure — failures are captured in `results`.

---

## Internal Types (not part of public contract)

### RenderedConfig

Internal intermediate produced by `ConfigRenderer`. Not exposed at the public ABC boundary.

| Field | Type | Description |
|-------|------|-------------|
| device_id | UUID | Source DesiredState identity |
| payload | dict | YANG-modelled JSON ready for gNMI SET |
| template_name | str | The template file(s) used (for diagnostics) |

---

## Entity Relationships

```
snapl_intent.DesiredState  ──(input)──► GnmiExecutor.apply()
                                               │
                                               ▼
                                        ConfigRenderer.render()
                                               │
                                               ▼
                                        RenderedConfig (internal)
                                               │
                                        asyncio.to_thread()
                                               │
                                               ▼
                                        gNMIclient.set() ──► SR Linux device
                                               │
                                               ▼
                                          ApplyResult  ──(return)──► caller
```

---

## State Transitions

The Executor is **stateless** — it holds no state between calls. The only state it manages is the gRPC connection lifetime, which is scoped to a single `apply()` / `rollback()` / `dry_run()` call (per-call context manager).

| Operation | Pre-condition | Post-condition |
|-----------|--------------|----------------|
| `apply(desired)` | Any (Executor is stateless) | Device running config updated (or ApplyResult.success=False) |
| `rollback(desired)` | Any | Device running config updated to rollback state (or failure) |
| `dry_run(desired)` | Any | No device change; DryRunResult carries rendered payload |
| `apply_batch(states)` | Any | Per-device results in BatchResult; partial success allowed |
