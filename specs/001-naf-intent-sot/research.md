# Research: NAF Intent — Source of Truth Integration

**Feature**: 001-naf-intent-sot
**Date**: 2026-04-15

## R1: Infrahub SDK Interaction Patterns

**Decision**: Use `infrahub-sdk[ctl]>=1.0.0` (already declared as dependency) with async client for all Source of Truth operations.

**Rationale**: The Infrahub Python SDK is the official, supported interface. The `[ctl]` extra provides CLI tooling (`infrahubctl`) for schema loading and generator scripts, which aligns with the two-phase seeding model. The async client (`InfrahubClient`) supports concurrent operations and integrates well with pytest-asyncio for testing.

**Alternatives considered**:
- Direct GraphQL queries via httpx: Lower-level, more boilerplate, no schema validation — rejected for prototype
- REST API: Infrahub's primary interface is GraphQL, REST is secondary — rejected

**Key patterns**:
- `InfrahubClientSync` for synchronous CLI/seed operations
- `InfrahubClient` (async) for runtime queries from other NAF blocks
- All queries return Infrahub node objects; map to Pydantic models at the boundary
- Connection via `INFRAHUB_ADDRESS` and `INFRAHUB_API_TOKEN` environment variables

## R2: Schema Management Approach

**Decision**: Define Infrahub schemas as YAML files in `packages/intent/snapl_intent/schemas/`. Load them via the SDK's schema API (`client.schema.load()`). Schema provisioning is idempotent.

**Rationale**: Infrahub natively supports schema definition via YAML with a well-defined format (nodes, attributes, relationships). Storing schemas as YAML files in the repository makes them git-versioned and diff-friendly, aligning with the "all changes are git-based" clarification. The SDK's `schema.load()` handles idempotent provisioning — re-loading an unchanged schema is a no-op.

**Alternatives considered**:
- Schema definition via Python code: Less portable, harder to review — rejected
- `infrahubctl schema load` CLI: Valid but couples to CLI availability; SDK approach is more composable for the IntentStore interface — rejected as primary method (can be used for manual operations)

**Schema structure**:
- `base.yml`: Common entities shared across use cases (Device, Interface)
- `dcfabric.yml`: Datacenter fabric-specific entities (BGPSession, FabricTopology)
- Future use cases add their own schema files without modifying base

## R3: Infrahub Branching Strategy

**Decision**: Map git feature branches to Infrahub branches for data isolation. Schema changes are applied to the `main` Infrahub branch; data seeding can target feature branches for testing.

**Rationale**: Infrahub supports native database branching — creating a branch in Infrahub forks the data state, similar to git. This enables:
- Testing seed data changes on an Infrahub branch before merging to main
- Aligning git workflow (feature branch → PR → merge) with Infrahub workflow (branch → review → merge)
- The spec requirement that "git branch model and SoT branch model work in concert"

**Alternatives considered**:
- No branching (always seed to main): Simpler but loses the git-aligned workflow — rejected
- Separate Infrahub instances per branch: Operationally complex — rejected

**Implementation notes**:
- Schema provisioning targets `main` branch (schemas are structural, not data)
- Data seeding can target any branch (default: `main`)
- Branch name passed as parameter to `seed()` and `get_desired_state()`

## R4: Data Seeding Format

**Decision**: Seed data is defined as YAML files in `packages/intent/snapl_intent/seed/<use_case>/`. The ingestion module reads these files, validates against provisioned schemas, and loads into Infrahub via the SDK.

**Rationale**: YAML is human-readable, diff-friendly, and aligns with network automation conventions (Ansible, Containerlab, Infrahub itself). Keeping seed data in the package ensures it's co-versioned with the schema definitions and Intent module code.

**Alternatives considered**:
- JSON: Valid but less readable for network engineers — rejected for seed data
- TOML: Not standard in network automation tooling — rejected
- Python generator scripts (infrahubctl pattern): More powerful but harder to review as declarative data — rejected as primary format (may be used for complex transformations)

**Seed data structure** (datacenter fabric example):
```yaml
# data/dcfabric/topology.yml
devices:
  - name: spine-01
    role: spine
    management_address: 10.0.0.1
    asn: 65000
    interfaces:
      - name: ethernet-1/1
        peer_device: leaf-01
        peer_interface: ethernet-1/49
        ip_address: 10.1.0.0/31
```

## R5: IntentStore ABC Design

**Decision**: Define `IntentStore` as an abstract base class with async methods. Separate schema operations (provision, inspect) from data operations (seed, query, delete). Device identity is UUID-based; queries support filtering.

**Rationale**: The constitution mandates an `IntentStore` ABC with `get_desired_state()`, `get_schema()`, `seed()`. The clarifications refine this:
- Seed is two-phase: `provision_schema()` + `ingest_data()`
- Deletion is supported with coordination hooks: `delete_device()`
- Schema inspection: `get_schema()`
- Queries support filtering: `get_desired_state()` with filter params
- All operations are async (Infrahub SDK is async-first)

**Alternatives considered**:
- Synchronous ABC: Would block the event loop when called from Temporal activities — rejected
- Separate ABCs per operation group (SchemaManager, DataStore): Over-abstraction for a prototype — rejected
- Single `seed()` method combining both phases: Doesn't allow independent schema provisioning — rejected per clarification

## R6: Schema-Library Approach (Predecessor Review)

**Decision**: Use Infrahub's built-in schema-library (dcim, ipam, location, organization) as base schemas. Extend with routing/BGP extensions and project-specific customizations. This inherits the proven pattern from project-network-synapse-quattro.

**Rationale**: The predecessor project validated this approach in production. The schema-library provides standard entity types (DcimDevice, InterfacePhysical, IpamIPAddress, IpamPrefix, LocationSite, OrganizationManufacturer) that are well-tested and follow Infrahub conventions. Extending these is simpler and more maintainable than defining custom types from scratch.

**Alternatives considered**:
- Custom `Snapl` namespace from scratch: More control but duplicates work already proven in schema-library — rejected
- Fork schema-library and modify: Unnecessary maintenance burden — rejected

**3-batch loading order** (from predecessor):
1. Batch 1 — Base: dcim, ipam, location, organization (schema-library)
2. Batch 2 — Extensions: routing_bgp, vrf (schema-library extensions)
3. Batch 3 — Project-specific: network_device customizations, network_interface customizations, business intent stubs

**Supporting entities** (seeded as internal prerequisites, not exposed via IntentStore):
- Organization, Manufacturer, Platform, DeviceType, Location
- AutonomousSystem, IPPrefix, IPAddress, VRF, BGPPeerGroup

## R7: Business Intent Model (Stubs)

**Decision**: Include Infrahub schema definitions for 8 business intent entities as stubs. No retrieval, seeding, or deletion implementation in this feature.

**Rationale**: The predecessor planned but did not implement a Business Intent model. Including stubs ensures the schema structure is reserved and visible in the codebase, preventing it from being forgotten during future development.

**Stub entities** (from predecessor's design):
- ApplicationService, ServiceEndpoint, ConnectivityIntent, InfrastructureBinding, FirewallRuleSet
- OperationalOverride, OverrideWindow, OverrideAction

## R8: Infrastructure Setup (Predecessor Review)

**Decision**: Port and adapt the predecessor's Docker Compose stack for running Infrahub locally. Includes Neo4j (graph database), Redis (cache), RabbitMQ (message queue), and Infrahub server.

**Rationale**: Integration tests and local data seeding require a running Infrahub instance. The predecessor's Docker Compose is proven to work and provides all required services. Porting it avoids building infrastructure setup from scratch.

**Services** (from predecessor):
- `infrahub-database`: Neo4j 5 (port 7687)
- `infrahub-cache`: Redis 7 (port 6379)
- `infrahub-message-queue`: RabbitMQ 3 (port 5672)
- `infrahub-server`: Infrahub stable (port 8000)

**Key env vars**: `INFRAHUB_ADDRESS`, `INFRAHUB_API_TOKEN`

## R9: Error Handling Strategy

**Decision**: Define domain-specific exceptions in the intent package. Map Infrahub SDK errors to these exceptions at the client boundary.

**Rationale**: FR-007 requires meaningful, structured error responses. Wrapping Infrahub errors in domain exceptions decouples consumers from the SoT implementation and enables consistent error handling across the NAF loop.

**Exception hierarchy**:
- `IntentError` (base)
  - `ConnectionError` — SoT unreachable (FR-007, SC-007)
  - `NotFoundError` — device/use case not found
  - `ValidationError` — schema validation failure (FR-004, SC-003)
  - `SchemaError` — schema provisioning failure
  - `DeletionError` — deletion coordination failure (FR-011)
