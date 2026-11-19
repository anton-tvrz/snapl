# Data Model: Site Edge — A Segmentation Rule as Intent

**Feature**: 008-site-edge
**Date**: 2026-10-28
**Source**: spec.md and research.md (R3, R4, R6, R9, R14)

## Overview

This feature adds one concept, the access-control list, at every layer it crosses:

1. **Source of Truth schema**: Infrahub kinds that hold the ACL as intent.
2. **Intent contract**: Pydantic models that every block consumes.
3. **Rendered payload**: SR Linux JSON. **Provisional until capture C2.**
4. **Normalized actual state**: what the collect adapter hands the diff.
5. **Drift field map**: what is compared, what counts as configured, and what is protected.

Layers 2, 4 and 5 are vendor-neutral contracts, apart from the path templates, which live in the field map seam. Layers 3 and 4's raw input are vendor seams (Principle VI).

dcfabric is untouched at every layer: its intent has empty ACL lists, its templates are unchanged, and its field map entries are unchanged apart from the mechanical `key_field` → `key_fields` rename (R6).

---

## 1. Source of Truth schema (Infrahub, Batch 3)

New file `packages/intent/snapl_intent/schemas/network_acl.yml`.

### SecurityAccessList

| Attribute / relationship | Kind | Optional | Notes |
| --- | --- | --- | --- |
| `name` | Text | no | e.g. `ot-ingress` |
| `description` | Text | yes | |
| `device` | relationship → `DcimDevice`, cardinality one | no | owning device |
| `entries` | relationship → `SecurityAccessListEntry`, cardinality many, Component | yes | |

Uniqueness: `[device, name__value]`. An ACL name is unique per device, not globally.

### SecurityAccessListEntry

| Attribute / relationship | Kind | Optional | Notes |
| --- | --- | --- | --- |
| `sequence` | Number | no | evaluation order, lowest first |
| `action` | Dropdown: `accept`, `drop` | no | |
| `source_prefix` | IPNetwork | yes | unset = any |
| `destination_prefix` | IPNetwork | yes | unset = any |
| `description` | Text | yes | |
| `access_list` | relationship → `SecurityAccessList`, cardinality one, Parent | no | |

Uniqueness: `[access_list, sequence__value]`.

### InterfacePhysical extension (in the same file)

| Relationship | Peer | Cardinality | Optional | Notes |
| --- | --- | --- | --- | --- |
| `ingress_acl` | `SecurityAccessList` | one | yes | the binding. At most one ingress IPv4 filter per interface. |

Readiness: `SchemaLoader` must wait for both new kinds, and for this relationship, before a seed may run (R5, #87).

---

## 2. Intent contract (`snapl_intent.models`)

### AclEntry (new)

```python
class AclEntry(BaseModel):
    model_config = ConfigDict(frozen=False, extra="forbid")

    id: UUID
    device_id: UUID
    acl_name: str
    sequence: int                      # > 0
    action: Literal["accept", "drop"]
    source_prefix: str | None = None   # CIDR string; None = any
    destination_prefix: str | None = None
    description: str | None = None
```

### AclBinding (new)

```python
class AclBinding(BaseModel):
    """An ACL applied to an interface's traffic in the ingress direction."""

    model_config = ConfigDict(frozen=False, extra="forbid")

    device_id: UUID
    interface: str    # interface name as in Interface.name, e.g. "ethernet-1/3"
    acl_name: str
```

A binding has no Source of Truth id of its own. It is the interface's `ingress_acl` relationship, and the store derives it.

### DesiredState (changed: two fields added)

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| device | Device | | unchanged |
| interfaces | list[Interface] | `[]` | unchanged |
| bgp_sessions | list[BGPSession] | `[]` | unchanged |
| **acl_entries** | list[AclEntry] | `[]` | new |
| **acl_bindings** | list[AclBinding] | `[]` | new |

**Validation rules** (enforced in the renderer, so that a bad intent fails with a named error before any SET, as for #72):

- `(acl_name, sequence)` is unique within a device.
- Every `AclBinding.acl_name` names an ACL that has at least one entry in `acl_entries`.
- Every `AclBinding.interface` names an interface in `interfaces`. A binding needs the subinterface that interface rendering creates.
- Prefixes are valid IPv4 CIDRs. The `0.0.0.0/0` written explicitly in the lab seed is valid and kept as written (R2).

**Store mapping** (`InfrahubIntentStore.get_desired_state`):

- One more child-side query: `SecurityAccessListEntry`, filtered to the matched devices' lists, with relationships prefetched (the #33 pattern).
- Entries are flattened, with `acl_name` taken from the parent list.
- `acl_bindings` comes from each interface node's `ingress_acl` peer name. Interfaces are already queried with `prefetch_relationships=True`.
- IPNetwork attributes are stringified, as `management_ip` is. The live SDK type is confirmed by capture C7.

---

## 3. Rendered payload: provisional SR Linux shape

> **Provisional.** The shape below is recalled from SR Linux documentation for the `acl-filter` model (R1). It has not been observed on this lab. Capture C2 replaces it, and `acl.j2` is written against the capture, not against this block.

```json
{
  "acl": {
    "acl-filter": [
      {
        "name": "ot-ingress",
        "type": "ipv4",
        "entry": [
          {
            "sequence-id": 10,
            "description": "OT to historian",
            "match": {"ipv4": {"source-ip": {"prefix": "10.40.20.0/24"},
                               "destination-ip": {"prefix": "10.50.10.0/24"}}},
            "action": {"accept": {}}
          },
          {
            "sequence-id": 20,
            "description": "deny OT to anything else",
            "match": {"ipv4": {"source-ip": {"prefix": "0.0.0.0/0"},
                               "destination-ip": {"prefix": "0.0.0.0/0"}}},
            "action": {"drop": {}}
          }
        ]
      }
    ],
    "interfaces": {
      "interface": [
        {
          "interface-id": "ethernet-1/3.0",
          "interface-ref": {"interface": "ethernet-1/3", "subinterface": 0},
          "input": {"acl-filter": [{"name": "ot-ingress", "type": "ipv4"}]}
        }
      ]
    }
  }
}
```

The renderer merges this under the existing payload alongside `interface` and `network-instance`. It is still a single SET update at `/`.

---

## 4. Normalized actual state (adapter output)

`normalize_srlinux_state` gains two entity shapes. The keys are path strings, and the values are flat snake_case dicts, the same contract as today (spec 007 Assumptions):

| Key (path) | Value |
| --- | --- |
| `/acl/acl-filter[name=ot-ingress][type=ipv4]/entry[sequence-id=20]` | `{"acl_name": "ot-ingress", "sequence": 20, "action": "drop", "source_prefix": "0.0.0.0/0", "destination_prefix": "0.0.0.0/0", "description": "deny OT to anything else"}` |
| `/acl/interfaces/interface[interface-id=ethernet-1/3.0]` | `{"interface": "ethernet-1/3", "acl_name": "ot-ingress"}` |

Normalization rules:

- **Filter type**: only IPv4 filters are normalized. Other filter types, and any container C1 shows to be control-plane protection, are not emitted, or are emitted under a path that `acl_entry`'s template does not match (R9).
- **Action**: `action` is the name of whichever case is present (`accept` or `drop`). If both are present, which C5 must rule out, both are emitted as `"accept+drop"`. That value never equals intent, so the condition surfaces as drift instead of being hidden.
- **Prefixes**: an absent prefix is `None`.
- **Bindings**: the binding's `acl_name` is the name of the **input** IPv4 filter. Output (egress) filters are not emitted in the first cut (spec Edge Cases).

---

## 5. Drift field map (`ENTITY_FIELD_MAP`)

After R6, every kind declares `key_fields` (a tuple). The existing kinds become one-tuples and are otherwise unchanged.

| Kind | `path_template` (provisional) | `key_fields` | `fields` (compared) | `value_fields` (configured-ness) | `protected` |
| --- | --- | --- | --- | --- | --- |
| `interface` | `/interface[name={name}]` | `("name",)` | unchanged | unchanged | `mgmt0`, `system0` |
| `bgp_session` | `…/neighbor[peer-address={peer_address}]` | `("peer_address",)` | unchanged | unchanged | none |
| **`acl_entry`** | `/acl/acl-filter[name={acl_name}][type=ipv4]/entry[sequence-id={sequence}]` | `("acl_name", "sequence")` | `action`, `source_prefix`, `destination_prefix`, `description` | `action`, `source_prefix`, `destination_prefix` | set from C1 (R9) |
| **`acl_binding`** | `/acl/interfaces/interface[interface-id={interface}.0]` | `("interface",)` | `acl_name` | `acl_name` | set from C1 (R9) |

Notes:

- **Key types**: `sequence` is an `int` in the model and the regex capture is a `str`. The undesired pass compares keys after formatting both sides through the template, so it never compares `int` against `str`.
- **`skip_when_none`**: none for the new kinds. Every field the intent leaves unset is compared strictly. The site_edge seed sets every prefix explicitly, so no lab rule depends on an unset value (R2).
- **The `.0` suffix**: the binding's path template pins subinterface 0, the only subinterface the templates render. A filter bound by hand to another subinterface does not match the template, so it is not reported in the first cut. This limitation is documented with the others.

### Drift items produced (examples)

| Situation | DriftItem(s) |
| --- | --- |
| Entry 20 deleted (User Story 2) | `…/entry[sequence-id=20]/action` desired `drop` actual `None`; likewise `source_prefix`, `destination_prefix`, `description` |
| Entry 20 flipped to accept | `…/entry[sequence-id=20]/action` desired `drop` actual `accept` |
| Accept entry inserted at sequence 5 | `…/entry[sequence-id=5]/action` desired `None` actual `accept`, `undesired=True` (plus its set prefixes) |
| OT binding removed | `/acl/interfaces/interface[interface-id=ethernet-1/3.0]/acl_name` desired `ot-ingress` actual `None` |
| ACL bound to `ethernet-1/2` by hand | `/acl/interfaces/interface[interface-id=ethernet-1/2.0]/acl_name` desired `None` actual `ot-ingress`, `undesired=True` |

---

## 6. Seed data shape (`seed/site_edge/topology.yml`)

The existing sections are used as in dcfabric: organization, location, manufacturer, platform, device_types, autonomous_systems, vrfs, ip_prefixes, devices, interfaces, bgp_peer_groups and bgp_sessions. Addressing follows research R14. One section is new, and interfaces gain one optional key:

```yaml
access_lists:
  - device: site-edge-01
    name: ot-ingress
    description: "OT segment may reach the historian subnet and nothing else across the WAN"
    entries:
      - {sequence: 10, action: accept, source_prefix: "10.40.20.0/24", destination_prefix: "10.50.10.0/24", description: "OT to historian"}
      - {sequence: 20, action: drop,   source_prefix: "0.0.0.0/0",     destination_prefix: "0.0.0.0/0",     description: "deny OT to anything else"}

interfaces:
  - {device: site-edge-01, name: ethernet-1/3, role: access, ip_address: "10.40.20.1/24", description: "OT segment", ingress_acl: ot-ingress}
  # ...
```

`SEED_ORDER` places `access_lists` after `devices` and **before** `interfaces`, because an interface row's `ingress_acl` is resolved by `(device, name)` to an ACL that must already exist. Entries are upserted by `(access_list, sequence)`.
