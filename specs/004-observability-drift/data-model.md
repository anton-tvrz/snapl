# Data Model: NAF Observability — Drift Detection & Audit

**Feature**: 004-observability-drift
**Date**: 2026-05-14
**Source**: Feature spec + research

## Overview

The Observability block does not own a device inventory or configuration store. Its data model describes the **comparison input, drift output, event, and audit record types** that flow across the Observer boundary. Inputs are consumed read-only from `snapl_intent` (`DesiredState`) and `snapl_collector` (`CollectResult`). All Observability-owned models are Pydantic v2 `BaseModel` subclasses with `frozen=True` (immutable by construction).

---

## Input Types (consumed from upstream blocks)

### DesiredState

Imported from `snapl_intent.models`. The intended configuration for a single device.

| Field | Type | Description |
|-------|------|-------------|
| device | Device | Device identity and attributes |
| interfaces | list[Interface] | Intended interface configurations |
| bgp_sessions | list[BGPSession] | Intended BGP peerings |

The Observer reads every field in the per-entity comparison map (see research R1) and never modifies the input.

### CollectResult

Imported from `snapl_collector.models`. The outcome of a single live data retrieval.

| Field | Type | Description |
|-------|------|-------------|
| device_id | UUID | Identity of the collected device |
| device_name | str | Human-readable name |
| success | bool | Whether the gNMI GET succeeded |
| data | dict[str, Any] | Live data keyed by YANG path |
| error | str \| None | Error description if success=False |

The Observer routes failed CollectResults into `DriftReport(status=ERROR)` without invoking the diff function.

---

## Result Types (owned by snapl_observability)

### DriftStatus

```python
class DriftStatus(str, Enum):
    CLEAN = "clean"        # Desired and actual match exactly
    DRIFTED = "drifted"    # One or more discrepancies found
    ERROR = "error"        # Live state could not be obtained
```

### DriftItem

A single detected discrepancy.

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| path | str | required, non-empty | Attribute path identifying the field (e.g., `/interface[name=ethernet-1/1]/mtu`) |
| desired | Any \| None | required (None permitted) | Value from the desired state |
| actual | Any \| None | required (None permitted) | Value observed in the collected data |
| entity_kind | str | required | Originating entity type: `device`, `interface`, or `bgp_session` |

**Invariant**: `desired != actual`. Constructing a `DriftItem` where the two are equal is a programming error (raises `ValueError` in `__init__`).

### DriftReport

The complete drift analysis for one device.

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| device_id | UUID | required | Identity of the analysed device |
| device_name | str | required | Human-readable name |
| status | DriftStatus | required | `clean`, `drifted`, or `error` |
| items | list[DriftItem] | required; empty if status != `drifted` | Per-attribute discrepancies |
| error | str \| None | optional; required if status=`error`, must be None otherwise | Error string from the upstream Collector |
| timestamp | datetime | required; UTC | When the analysis completed |

**Invariants**:
- `status == CLEAN`  ⇒ `items == []` and `error is None`
- `status == DRIFTED` ⇒ `len(items) >= 1` and `error is None`
- `status == ERROR`  ⇒ `items == []` and `error is not None`

### BatchDriftReport

The aggregated outcome of `detect_drift_batch()`.

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| reports | dict[UUID, DriftReport] | required | Per-device DriftReport keyed by device UUID |
| total | int | required | Total number of devices analysed |
| clean | int | required | Count of CLEAN reports |
| drifted | int | required | Count of DRIFTED reports |
| errored | int | required | Count of ERROR reports |

**Invariant**: `clean + drifted + errored == total`.

### EventType

```python
class EventType(str, Enum):
    DRIFT_DETECTED = "drift_detected"   # status == DRIFTED
    STATE_CLEAN = "state_clean"         # status == CLEAN
    DRIFT_ERROR = "drift_error"         # status == ERROR
```

### ObservabilityEvent

A structured notification emitted after every drift check.

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| event_type | EventType | required | Mapped 1:1 from `DriftReport.status` |
| device_id | UUID | required | Affected device |
| device_name | str | required | Human-readable name |
| report | DriftReport | required | The triggering report |
| timestamp | datetime | required; UTC | When the event was constructed |

**Invariant**: `event_type` matches `report.status` per the mapping above. Inconsistent construction raises `ValueError`.

### AuditOperation

```python
class AuditOperation(str, Enum):
    DETECT_DRIFT = "detect_drift"
    EMIT_EVENT = "emit_event"
    LOG_AUDIT = "log_audit"
```

### AuditOutcome

```python
class AuditOutcome(str, Enum):
    SUCCESS = "success"
    FAILURE = "failure"
```

### AuditEntry

An immutable record of one Observability operation.

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| operation | AuditOperation | required | What was done |
| device_id | UUID \| None | optional | Target device, if applicable |
| component | str | required | Caller name (e.g., `"StructuralObserver"`, `"orchestrator.workflow"`) |
| outcome | AuditOutcome | required | `success` or `failure` |
| detail | dict[str, Any] | optional, default `{}` | Free-form context |
| timestamp | datetime | required; UTC | When the entry was created |

**Invariant**: Frozen — Pydantic prevents post-construction mutation. The hosting `AuditLog` returns `list` copies on every query so callers cannot mutate the in-memory list either.

---

## Internal Types (not part of public contract)

### EntityFieldMap (in `structural/diff.py`)

A module-private constant mapping each intent entity kind to the set of fields the platform compares and the YANG path template used to look up the corresponding actual value.

```python
ENTITY_FIELD_MAP = {
    "interface": {
        "fields": ["description", "ip_address", "prefix_length", "enabled", "mtu"],
        "path_template": "/interface[name={name}]",
        "key_field": "name",
    },
    "bgp_session": {
        "fields": ["peer_address", "peer_asn", "peer_group", "enabled"],
        "path_template": "/network-instance[name=default]/protocols/bgp/neighbor[peer-address={peer_address}]",
        "key_field": "peer_address",
    },
    "device": {
        "fields": ["description"],
        "path_template": "/system",
        "key_field": None,
    },
}
```

This map is the single point of vendor coupling in the Observability block. Adding a new entity (e.g., `OSPFNeighbor`) means extending this map; nothing else in the package changes.

---

## Service Types (owned by snapl_observability)

### EventBus

In-process synchronous dispatcher. Not a Pydantic model — a plain class.

| Method | Signature | Behaviour |
|--------|-----------|-----------|
| register | `(handler: Callable[[ObservabilityEvent], None]) -> None` | Append to handler list |
| emit | `(event: ObservabilityEvent) -> None` | Invoke each handler in registration order; isolate exceptions per-handler with `logging.warning` |
| handlers | `property -> tuple[Callable, ...]` | Read-only view of registered handlers |

### AuditLog

In-memory append-only store. Not a Pydantic model — a plain class.

| Method | Signature | Behaviour |
|--------|-----------|-----------|
| append | `(entry: AuditEntry) -> None` | Lock, append, release |
| query_by_device | `(device_id: UUID) -> list[AuditEntry]` | Return chronological list copy filtered by device |
| all | `() -> list[AuditEntry]` | Return chronological list copy of every entry |
| __len__ | `() -> int` | Number of entries |

---

## Entity Relationships

```text
snapl_intent.DesiredState ─┐
                           ├─► Observer.detect_drift() ─► DriftReport
snapl_collector.CollectResult ─┘                                │
                                                                ▼
                                              Observer.emit_event() ─► ObservabilityEvent ─► EventBus.emit() ─► handlers
                                                                │
                                                                ▼
                                              Observer.log_audit() ─► AuditEntry ─► AuditLog.append()
```

**Batch flow**:

```text
list[(DesiredState, CollectResult)] ─► Observer.detect_drift_batch() ─► BatchDriftReport(reports=dict[UUID, DriftReport])
```

---

## State Transitions

The Observability block is **stateful only with respect to the audit log and registered event handlers**. The `Observer` itself holds no per-call state; consecutive calls to `detect_drift()` produce identical outputs given identical inputs.

| Operation | Pre-condition | Post-condition |
|-----------|--------------|----------------|
| `detect_drift(desired, actual)` | both non-None; types as declared | DriftReport returned; AuditEntry appended to AuditLog |
| `detect_drift_batch(pairs)` | pairs is non-empty | BatchDriftReport returned; one AuditEntry appended per pair |
| `emit_event(report)` | report is a valid DriftReport | ObservabilityEvent dispatched to every registered handler; AuditEntry appended |
| `log_audit(entry)` | entry is a valid AuditEntry | Entry appended to AuditLog |
| `EventBus.register(h)` | h is callable | h added to handler list; subsequent emits invoke it |
| `AuditLog.append(e)` | e is an AuditEntry | Length increases by 1; e is queryable by device_id |

There are no concurrency-driven state transitions in this iteration — the `AuditLog` lock makes append atomic but the visible state is the same as serial execution.
