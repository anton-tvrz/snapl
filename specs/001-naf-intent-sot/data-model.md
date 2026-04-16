# Data Model: NAF Intent — Source of Truth Integration

**Feature**: 001-naf-intent-sot
**Date**: 2026-04-15
**Source**: Feature spec + clarifications + research

## Entities

### Device

The primary entity representing a network element within a use case.

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| id | UUID | PK, system-assigned | Unique identity; stable across renames |
| name | string | required | Human-readable device name (e.g., `spine-01`) |
| management_address | string | required | Management IP or FQDN |
| role | enum | required; values: spine, leaf, border, edge, hub, spoke | Device function in the topology |
| use_case | string | required; FK to UseCase | Which network scenario this device belongs to |
| platform | string | optional | Device platform (e.g., `nokia-srlinux`) |
| description | string | optional | Human-readable notes |

**Identity rule**: UUID is the primary key. Name is queryable but not unique — two use cases may have a `spine-01`. A device belongs to exactly one use case. Physical devices serving multiple use cases are modeled as separate logical devices.

### Interface

A network interface on a device, carrying intended L2/L3 configuration.

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| id | UUID | PK, system-assigned | Unique identity |
| device_id | UUID | required; FK to Device | Owning device |
| name | string | required | Interface name (e.g., `ethernet-1/1`) |
| description | string | optional | Interface purpose |
| ip_address | string | optional | IPv4/IPv6 address |
| prefix_length | integer | optional; required if ip_address set | Subnet mask length |
| enabled | boolean | required; default true | Admin state |
| speed | string | optional | Link speed (e.g., `100G`) |
| mtu | integer | optional; default 9232 | Maximum transmission unit |
| peer_device | string | optional | Connected peer device name |
| peer_interface | string | optional | Connected peer interface name |

**Identity rule**: UUID is the primary key. Interface name is unique within a device (device_id + name is a natural key).

### BGPSession

A BGP peering session configured on a device.

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| id | UUID | PK, system-assigned | Unique identity |
| device_id | UUID | required; FK to Device | Device hosting this session |
| local_asn | integer | required | Local autonomous system number |
| peer_address | string | required | Peer's IP address |
| peer_asn | integer | required | Peer's autonomous system number |
| peer_group | string | optional | BGP peer group name |
| address_family | enum | required; default ipv4_unicast | AF: ipv4_unicast, ipv6_unicast, evpn |
| export_policy | string | optional | Export route policy name |
| import_policy | string | optional | Import route policy name |
| enabled | boolean | required; default true | Session admin state |

**Identity rule**: UUID is the primary key. (device_id + peer_address) is a natural key.

### UseCase

A network automation scenario grouping devices and their intended configurations.

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| id | string | PK | Short identifier (e.g., `dcfabric`, `clientedge`, `sdwan`, `wan`) |
| name | string | required | Human-readable name (e.g., `Datacenter Fabric`) |
| description | string | optional | Purpose of this use case |
| schema_version | string | required | Version of the Infrahub schema for this use case |

**Identity rule**: String ID is the primary key (simple, readable, used in file paths and queries).

### Schema

Metadata about a provisioned data model in the Source of Truth.

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| use_case | string | PK; FK to UseCase | Which use case this schema belongs to |
| version | string | required | Schema version string |
| entities | list[string] | required | List of entity types defined in this schema |
| source_files | list[string] | required | YAML files that define this schema |
| provisioned_at | datetime | set on provision | When the schema was last provisioned |

**Note**: Schema is a read-only view returned by `get_schema()`. It is not persisted as a separate entity — it is derived from the Infrahub schema registry.

## Relationships

```
UseCase 1──* Device         (a use case has many devices)
Device  1──* Interface      (a device has many interfaces)
Device  1──* BGPSession     (a device has many BGP sessions)
Interface ──? Interface     (peer_device/peer_interface form a logical link)
```

## Validation Rules

### Device
- `name` must be non-empty
- `management_address` must be a valid IPv4/IPv6 address or FQDN
- `role` must be one of the defined enum values
- `use_case` must reference an existing UseCase

### Interface
- `name` must follow platform naming convention (e.g., `ethernet-X/Y` for SR Linux)
- If `ip_address` is set, `prefix_length` is required
- `ip_address` must be valid IPv4 or IPv6

### BGPSession
- `local_asn` and `peer_asn` must be valid ASN range (1–4294967295)
- `peer_address` must be valid IPv4 or IPv6
- `address_family` must be one of the defined enum values

## State Transitions

Devices follow a simple lifecycle within the Intent module:

```
[not exists] --seed/ingest--> [active] --update--> [active] --delete--> [not exists]
```

- **Creation**: Via data ingestion (seed). Device is immediately active.
- **Update**: Via re-ingestion. Existing data is overwritten (upsert pattern).
- **Deletion**: Via `delete_device()`. Requires coordination with Collector (observed state check). On successful coordination, device and all its interfaces/BGP sessions are removed.

There is no "inactive" or "decommissioning" intermediate state in the Intent module. Decommissioning workflow coordination is handled at the Orchestrator level.

## Supporting Entities (Internal Prerequisites)

These entities are required by the Source of Truth before devices can be created. They are included in schema and seed definitions but **not exposed through the IntentStore ABC**. They use Infrahub's built-in schema-library types.

| Entity | Schema-Library Type | Purpose |
|--------|-------------------|---------|
| Organization | OrganizationManufacturer | Lab organization identity |
| Manufacturer | OrganizationManufacturer | Device vendor (Nokia) |
| Platform | DcimPlatform | Device software platform (SR Linux) |
| Device Type | DcimDeviceType | Hardware model (7220 IXR-D2, IXR-D3) |
| Location | LocationSite | Physical/logical site |
| Autonomous System | RoutingAutonomousSystem | BGP ASN |
| IP Prefix | IpamIPPrefix | Network address ranges |
| IP Address | IpamIPAddress | Host addresses |
| VRF | IpamVRF | Virtual routing instance |
| BGP Peer Group | RoutingBGPPeerGroup | BGP session grouping |

**Seed dependency order**: Organization → Location → Manufacturer → Platform → DeviceType → AutonomousSystems → VRFs → IPPrefixes → Devices → IPAddresses → Interfaces → BGPPeerGroups → BGPSessions

## Business Intent Entities (Stubs Only)

Schema definitions are included as stubs for future implementation. No retrieval, seeding, or deletion in this feature.

| Entity | Purpose |
|--------|---------|
| ApplicationService | Business-level service declaration |
| ServiceEndpoint | Service ingress/egress points |
| ConnectivityIntent | Desired connectivity between endpoints |
| InfrastructureBinding | Maps intent to physical infrastructure |
| FirewallRuleSet | Security policy declarations |
| OperationalOverride | Temporary operational exceptions |
| OverrideWindow | Time-bounded override periods |
| OverrideAction | Actions taken during override windows |

## Infrahub Schema Mapping

Domain entities map to Infrahub schema-library types (extended with project-specific attributes where needed):

| Domain Entity | Infrahub Type | Source |
|---------------|--------------|--------|
| Device | DcimDevice (extended) | schema-library + project extensions |
| Interface | InterfacePhysical (extended) | schema-library + project extensions |
| BGPSession | RoutingBGPSession | schema-library routing_bgp extension |

**Schema loading**: 3-batch dependency order:
1. Base (dcim, ipam, location, org)
2. Extensions (routing_bgp, vrf)
3. Project-specific (device/interface customizations, business intent stubs)

UseCase and Schema are not stored as Infrahub nodes — they are derived from the schema registry and file structure.
