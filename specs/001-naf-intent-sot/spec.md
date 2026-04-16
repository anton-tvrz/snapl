# Feature Specification: NAF Intent — Source of Truth Integration

**Feature Branch**: `001-naf-intent-sot`
**Created**: 2026-04-15
**Status**: Draft
**Input**: User description: "NAF Intent module — Source of Truth integration with Infrahub for multi-use-case network automation"

## Clarifications

### Session 2026-04-15

- Q: Is seeding a day-0 full bootstrap or merely data ingestion into an existing schema? → A: Layered — seeding has two distinct phases: (1) schema provisioning (idempotent, ensures data structures exist) then (2) data ingestion (loads device/config data). Both are part of the Intent module but are separate operations.
- Q: Can a device belong to multiple use cases (e.g., a border device serving both fabric and WAN)? → A: No. Each device belongs to exactly one use case. Shared physical devices are modeled as separate logical devices per use case.
- Q: Can desired state be deleted (e.g., decommissioning a device)? → A: Yes, deletion is supported but must work in conjunction with other NAF modules — particularly observed state from the Collector — to ensure safe decommissioning. Deletion is not an isolated intent operation.
- Q: What uniquely identifies a device? → A: A system-assigned UUID is the primary identity. Device name is a human-readable, queryable attribute but not the key. Devices can be renamed without breaking references.
- Q: What format does seed data come in? → A: Git-based declarative files. Seed data is maintained as structured files in the repository. All intent changes must be git-based. The Source of Truth supports native database branching, so the git branch model and SoT branch model work together. Keep it simple for the prototype.
- Q: Should supporting entities (Organization, Manufacturer, Platform, DeviceType, Location, ASN, IPPrefix, VRF, BGPPeerGroup) be modeled as first-class entities? → A: No. They are internal prerequisites included in schema and seed definitions but not exposed through the IntentStore interface. The ABC exposes only Device, Interface, and BGPSession. Supporting entities are seeded in dependency order as infrastructure data.
- Q: Should schemas be custom from scratch or use Infrahub's built-in schema-library? → A: Use Infrahub's schema-library (dcim, ipam, location, org) as base schemas, then extend with project-specific customizations. Inherits the predecessor's approach.
- Q: Should the Business Intent model (ApplicationService, ServiceEndpoint, ConnectivityIntent, etc.) be in scope? → A: Stub only. Define Infrahub schema definitions for the 8 business intent entities as stubs, but do not implement retrieval or seeding in this feature. Serves as a placeholder for a future feature.
- Q: Should Docker Compose infrastructure for Infrahub be part of this feature? → A: Yes. Port and adapt the predecessor's Docker Compose (Neo4j, Redis, RabbitMQ, Infrahub server) as a deliverable. Required for integration tests and data seeding.
- Q: How should schemas be loaded given dependency ordering requirements? → A: 3-batch strategy from predecessor: Batch 1 (base: dcim, ipam, location, org) → Batch 2 (extensions: routing, BGP, VRF) → Batch 3 (project-specific: custom entities + business intent stubs).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Retrieve Desired Network State (Priority: P1)

A network operator or an automated workflow needs to know the intended configuration for one or more network devices in the datacenter fabric. They query the Intent module, which returns the complete desired state — interfaces, routing parameters, and device roles — as structured data ready for deployment or comparison against the live network.

**Why this priority**: Retrieving desired state is the foundational capability that every downstream component (deployment, drift detection, auditing) depends on. Without it, no other NAF block can function.

**Independent Test**: Can be fully tested by seeding a small dataset, querying for a specific device, and verifying that the returned desired state matches what was seeded. Delivers immediate value as the single integration point for all consumers of intended network state.

**Acceptance Scenarios**:

1. **Given** a datacenter fabric with 4 spine and 8 leaf devices registered in the Source of Truth, **When** an operator queries desired state for a specific leaf device, **Then** the system returns the complete intended configuration for that device including interfaces, BGP peers, and device role.
2. **Given** the same fabric, **When** an automated workflow queries desired state for all spine devices, **Then** the system returns the intended configuration for all 4 spines as a collection of structured records.
3. **Given** a device identifier that does not exist in the Source of Truth, **When** a query is made for that device, **Then** the system returns a clear "not found" error without crashing or returning partial data.

---

### User Story 2 - Seed Network Intent Data (Priority: P2)

A network engineer setting up a new use case (e.g., a datacenter fabric) uses the Intent module to establish the data structures and then populate the initial intended state. This is a two-phase process: first, schema provisioning ensures the required data model exists in the Source of Truth (this step is idempotent and safe to repeat); second, data ingestion loads the actual device inventory, interface assignments, IP addressing, BGP autonomous system numbers, and peering relationships. The system validates ingested data against the provisioned schema before persisting it, ensuring only well-formed intent is stored.

**Why this priority**: Seeding is the entry point for getting data into the system. It must work correctly before desired state retrieval has meaningful data to return. It is second to retrieval because retrieval defines the consumer contract that seeding must satisfy.

**Independent Test**: Can be tested by running schema provisioning for the datacenter fabric use case (verifying the data model is installed), then ingesting a minimal dataset (2 spines, 4 leaves), and confirming all devices and their intended configurations are present and correctly structured in the Source of Truth.

**Acceptance Scenarios**:

1. **Given** a Source of Truth with no schema for the datacenter fabric, **When** a network engineer runs schema provisioning for the datacenter fabric use case, **Then** the data model is installed and the system reports success. Running provisioning again produces no errors or changes (idempotent).
2. **Given** a provisioned schema, **When** the engineer ingests a datacenter fabric dataset (devices, interfaces, BGP configuration), **Then** all entities are persisted and retrievable with correct relationships between them.
3. **Given** a schema that requires certain fields (e.g., device role, management address), **When** ingesting data that omits required fields, **Then** the system rejects the submission with a clear error message identifying which fields are missing.
4. **Given** an already-ingested fabric, **When** data ingestion is run again with updated values, **Then** existing data is updated (not duplicated) and the Source of Truth reflects the latest intent.

---

### User Story 3 - Inspect Data Model Definitions (Priority: P3)

A developer building a new use case or a network engineer troubleshooting data issues needs to understand what fields, types, and relationships are valid for a given use case's desired state. They query the Intent module for the schema definition and receive a structured description of the data model.

**Why this priority**: Schema inspection supports development and debugging workflows. It is less critical for day-to-day automation (which relies on retrieval and seeding) but essential for onboarding new use cases and validating data correctness.

**Independent Test**: Can be tested by querying for the schema of the datacenter fabric use case and verifying the returned definition includes all expected entities, fields, and relationship descriptions.

**Acceptance Scenarios**:

1. **Given** a datacenter fabric use case with defined schema, **When** a developer queries the schema, **Then** the system returns a structured definition listing all entities, their fields, field types, and relationships.
2. **Given** a use case identifier that has no schema registered, **When** a schema query is made, **Then** the system returns a clear error indicating the use case is not configured.

---

### User Story 4 - Support Independent Use Cases (Priority: P4)

The platform supports multiple network automation scenarios (datacenter fabric, client edge, SD-WAN, WAN). Each use case has its own data model and intended state. A network engineer working on the datacenter fabric can manage intent without affecting the client edge configuration, and vice versa.

**Why this priority**: Multi-use-case isolation is an architectural property that enables the platform to grow beyond a single use case. It is lower priority because the primary use case (datacenter fabric) must work end-to-end before multi-use-case scenarios are exercised.

**Independent Test**: Can be tested by seeding two different use cases (e.g., datacenter fabric and client edge), modifying one, and verifying the other remains unchanged.

**Acceptance Scenarios**:

1. **Given** two use cases (datacenter fabric, client edge) each with seeded data, **When** an operator modifies the desired state for a device in the datacenter fabric, **Then** all client edge data remains unchanged and retrievable in its original form.
2. **Given** a query for desired state, **When** the operator specifies a use case filter, **Then** only devices and configurations belonging to that use case are returned.

---

### Edge Cases

- What happens when the Source of Truth is unreachable (network outage, service down)? The system must return a clear connectivity error and not cache or serve stale data without indication.
- What happens when seeding references a device role or interface type not defined in the schema? Validation must catch this before persistence.
- What happens when two operators seed conflicting data for the same device simultaneously? The system must handle concurrent modifications without silent data loss.
- What happens when a physical device serves multiple use cases (e.g., a border device in both fabric and WAN)? Each use case models it as a separate logical device. The system enforces single use case membership per device record.
- What happens when querying desired state for a use case with no seeded data? The system must return an empty result, not an error.
- What happens when deleting a device that still has active observed state in the Collector? The system must coordinate with the Collector before removing intent — deletion without confirming the device is safely decommissioned must be prevented or flagged.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST retrieve the complete desired network state for any registered device, returning all configuration attributes relevant to the device's use case.
- **FR-002**: System MUST support filtering desired state queries by use case, device role, device name, or any combination thereof.
- **FR-003**: System MUST provision the data model (schema) for a use case into the Source of Truth. Schema provisioning MUST be idempotent — repeated runs produce no errors or structural changes. Schemas MUST be loaded in 3 dependency-ordered batches: base schemas (dcim, ipam, location, org) → extension schemas (routing, BGP, VRF) → project-specific schemas (custom entities + business intent stubs).
- **FR-003a**: System MUST ingest initial intended state for a network use case from declarative data files maintained in the git repository. Data ingestion requires a provisioned schema.
- **FR-004**: System MUST validate all data against the applicable schema before persisting to the Source of Truth, rejecting invalid submissions with specific error details.
- **FR-005**: System MUST expose schema definitions that describe valid entities, fields, types, and relationships for each use case.
- **FR-006**: System MUST isolate desired state between use cases — operations on one use case MUST NOT affect another.
- **FR-007**: System MUST return meaningful, structured error responses when the Source of Truth is unreachable, queried data does not exist, or submitted data fails validation.
- **FR-008**: System MUST provide a consistent interface for desired state operations regardless of which use case is being queried, enabling downstream consumers to work with any use case through the same interaction patterns.
- **FR-009**: System MUST support updating existing desired state (re-ingesting) without creating duplicate records.
- **FR-010**: System MUST support the datacenter fabric use case as the primary and fully-tested use case at first delivery.
- **FR-012**: System MUST include stub schema definitions for the Business Intent model (ApplicationService, ServiceEndpoint, ConnectivityIntent, InfrastructureBinding, FirewallRuleSet, OperationalOverride, OverrideWindow, OverrideAction). These schemas are provisioned alongside device-level schemas but have no retrieval, seeding, or deletion implementation in this feature.
- **FR-013**: Feature MUST deliver a local infrastructure stack (Docker Compose) for running the Source of Truth and its dependencies (graph database, cache, message queue). This enables integration testing and local development.
- **FR-011**: System MUST support deletion of a device and its desired state from the Source of Truth. Deletion MUST be coordinated with other NAF modules (specifically the Collector's observed state) to ensure the device is safely decommissioned before intent is removed. The Intent module MUST expose deletion as an operation that other modules can gate or confirm.

### Key Entities

- **Device**: A network element uniquely identified by a system-assigned UUID. Carries human-readable attributes including name, management address, and role (e.g., spine, leaf, border, edge). Name is queryable but not the primary key — devices can be renamed without breaking references. Belongs to exactly one use case. If a physical device serves multiple use cases (e.g., a border device), it is modeled as separate logical devices — one per use case. Carries intended interface and routing configuration.
- **Desired State**: The intended configuration for a device — a structured record comprising interface assignments, IP addressing, routing protocol parameters (e.g., BGP ASN, peer groups), and operational metadata.
- **Use Case**: A network automation scenario (datacenter fabric, client edge, SD-WAN, WAN) that groups related devices and defines the data model governing their desired state.
- **Schema**: The data model definition for a use case — specifies which entities exist, what fields they carry, valid types, and how entities relate to each other.
- **Seed Dataset**: A complete initial dataset for a use case, maintained as declarative files in the git repository, containing all devices, their configurations, and relationships needed to establish the intended state. Seeding is a two-phase process: schema provisioning (idempotent, ensures data model exists) followed by data ingestion (loads records from the declarative files). Data ingestion follows a strict dependency order — supporting entities (organization, manufacturer, platform, device types, locations, autonomous systems, IP prefixes, VRFs, BGP peer groups) are created before devices, interfaces, and BGP sessions. Supporting entities are internal prerequisites, not exposed through the IntentStore interface. All changes to intent are git-based.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Operators can retrieve the complete desired state for any single device within 5 seconds of issuing the query.
- **SC-002**: Seeding a full datacenter fabric dataset (up to 50 devices with interfaces and routing configuration) completes within 2 minutes.
- **SC-003**: 100% of schema-violating data submissions are rejected before persistence, with error messages that identify the specific field and violation.
- **SC-004**: Use case isolation is absolute — automated tests confirm that operations on one use case produce zero side effects on any other use case.
- **SC-005**: The datacenter fabric use case (spine-leaf eBGP) is fully operational at first delivery, with the system architecture validated to support at least one additional use case without structural changes.
- **SC-006**: All downstream consumers (deployment, drift detection, orchestration) can retrieve desired state through a single, consistent interface pattern.
- **SC-007**: When the Source of Truth is unavailable, 100% of operations return a clear error within 10 seconds (no hanging or silent failures).

## Assumptions

- Datacenter fabric (spine-leaf eBGP with Nokia SR Linux) is the primary use case for first delivery. Client edge, SD-WAN, and WAN are architecturally supported but not required to have fully populated data models or seed datasets in the initial release.
- The Intent module owns schema provisioning (idempotent installation of data models into the Source of Truth). Schemas build on the Source of Truth's built-in schema-library (dcim, ipam, location, organization) as base types, extending with project-specific customizations. Schema migration and versioning remain the responsibility of the Source of Truth platform. The Intent module does not own schema migration tooling.
- Network devices are registered in the Source of Truth inventory as part of the seeding process — there is no separate device registration workflow.
- The Intent module is consumed programmatically by other system components (executor, orchestrator, observability). Direct end-user interaction with the Intent module occurs through the Presentation layer, which is out of scope for this feature.
- Nokia SR Linux is the prototyping target. The interface design supports future multi-vendor extension without requiring changes to consuming components.
- All intent changes are git-based. Seed data and intent modifications are maintained as declarative files in the repository. The Source of Truth supports native database branching, enabling the git branch model and SoT branch model to work in concert.
- The project inherits integration patterns and domain knowledge from the predecessor project (project-network-synapse-quattro), including data model concepts for the datacenter fabric use case.
