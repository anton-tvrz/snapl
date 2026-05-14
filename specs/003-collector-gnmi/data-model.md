# Data Model: NAF Collector — gNMI Live Data Retrieval

**Feature**: 003-collector-gnmi
**Date**: 2026-05-13
**Source**: Feature spec + research

## Overview

The Collector does not own a device inventory or configuration store. Its data model describes the **operation request and result types** that flow across the Collector boundary. The `Device` descriptor is consumed read-only from `snapl_intent`. Collected data is returned as raw Python dicts — the Collector never transforms or persists it.

---

## Input Type (consumed from snapl_intent)

### Device

Imported from `snapl_intent.models`. The Collector's primary device descriptor.

| Field | Type | Description |
|-------|------|-------------|
| id | UUID | Device identity |
| name | str | Human-readable device name |
| management_address | str | IP or hostname used for gNMI connection |
| role | str | Device role (e.g., "spine", "leaf") |
| use_case | str | Use-case scope (e.g., "dcfabric") |
| platform | str | Device platform (e.g., "nokia-srlinux") |

The Collector reads `management_address` to establish the gNMI connection and `id`/`name` to populate result objects. It does not modify or persist the Device.

---

## Result Types (owned by snapl_collector)

### CollectResult

Returned by `collect()` and `get_running_config()`.

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| device_id | UUID | required | Identity of the target device |
| device_name | str | required | Human-readable name (from Device) |
| success | bool | required | Whether the gNMI GET succeeded |
| data | dict[str, Any] | required; empty dict if success=False | Collected data keyed by YANG path |
| paths | list[str] | required | The paths that were requested |
| error | str \| None | optional; present if success=False | Error description (connectivity/auth/timeout/parse) |
| duration_ms | int | required | Wall-clock time of the gNMI operation in milliseconds |
| timestamp | datetime | required | UTC timestamp when the collection completed |

**Invariant**: `success=True` implies `error=None`. `success=False` implies `error` is set and `data` is empty.

**Empty data invariant**: `data == {}` with `success=True` is valid — the device path exists but has no entries (legitimately empty subtree).

### BatchCollectResult

Returned by `collect_batch()`.

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| results | dict[UUID, CollectResult] | required | Per-device outcome map keyed by device UUID |
| total | int | required | Total number of devices attempted |
| succeeded | int | required | Number of successful collects |
| failed | int | required | Number of failed collects |

**Derived**: `succeeded + failed == total`. No exception raised for partial failure — failures are captured in `results`. The same device appearing twice is a validation error raised before any gNMI connection is attempted.

---

## Internal Types (not part of public contract)

No internal intermediates beyond the `_blocking_get()` method's local response dict. The Collector has no renderer and no template system — it parses pygnmi's raw response into `CollectResult.data` directly.

---

## Entity Relationships

```
snapl_intent.Device  ──(input)──► GnmiCollector.collect()
                                          │
                              paths: list[str]
                                          │
                                    asyncio.to_thread()
                                          │
                                          ▼
                               gNMIclient.get() ──► SR Linux device
                                          │
                               _parse_response()
                                          │
                                          ▼
                                  CollectResult  ──(return)──► caller
```

**Batch flow**:
```
list[Device]  ──(input)──► GnmiCollector.collect_batch()
                                    │
                         asyncio.gather() [concurrent]
                             /    |    \
                         GET  GET  GET  ...
                             \    |    /
                         BatchCollectResult  ──(return)──► caller
```

---

## Data Format Contract

Collected data (`CollectResult.data`) is a `dict[str, Any]` keyed by the normalised YANG path strings from the request:

```python
# For collect(device, paths=["/interface", "/network-instance[name=default]/protocols/bgp/neighbor"])
result.data == {
    "/interface": [
        {"name": "ethernet-1/1", "subinterface": [{"index": 0, "ipv4": {"address": [...]}}]},
        ...
    ],
    "/network-instance[name=default]/protocols/bgp/neighbor": [
        {"peer-address": "10.0.0.1", "peer-as": 65001, "admin-state": "enable"},
        ...
    ]
}

# For get_running_config(device) — root path
result.data == {
    "/": {
        "interface": [...],
        "network-instance": [...],
        "system": {...}
    }
}
```

This structure is directly comparable to the Executor's rendered payloads (which are also `dict[str, Any]` YANG-modelled JSON), satisfying FR-009 and SC-006.

---

## State Transitions

The Collector is **stateless and read-only** — it holds no state between calls and makes no writes to devices.

| Operation | Pre-condition | Post-condition |
|-----------|--------------|----------------|
| `collect(device, paths)` | paths is non-empty | CollectResult with data or error; device unchanged |
| `get_running_config(device)` | Any | CollectResult with full config or error; device unchanged |
| `collect_batch(devices, paths)` | devices is non-empty, no duplicates | BatchCollectResult; all devices unchanged |
