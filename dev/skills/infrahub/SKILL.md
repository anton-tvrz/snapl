# Infrahub — Source of Truth Skill

## Metadata
- name: infrahub
- triggers: infrahub, schema, GraphQL, SoT, source of truth, seed data
- project: snapl

## What is Infrahub?

Infrahub (by OpsMill) is a graph-native Source of Truth for network infrastructure. It stores schemas, inventory, and intended state with full version control and branching. snapl uses Infrahub as the Source of Truth within the NAF Intent building block (`snapl_intent`).

## SDK Async Client Patterns

Always use the async Infrahub SDK client, never raw HTTP calls:

```python
from infrahub_sdk import InfrahubClient

async def get_client() -> InfrahubClient:
    return await InfrahubClient.init(
        address="http://localhost:8000",
        api_token="<INFRAHUB_API_TOKEN>",  # See .env
    )
```

## Schema YAML Structure

Schemas are defined in `packages/intent/snapl_intent/schemas/`:

```yaml
---
version: "1.0"
nodes:
  - name: ApplicationService
    namespace: Business
    description: "Business-level service declaration"
    attributes:
      - name: name
        kind: Text
        unique: true
      - name: environment
        kind: Dropdown
        choices:
          - name: production
          - name: staging
          - name: development
    relationships:
      - name: endpoints
        peer: BusinessServiceEndpoint
        cardinality: many
        kind: Component
```

## Dependency-Ordered Loading

Schemas must be loaded in dependency order. If schema B references schema A, schema A must be loaded first.

## Seed Data Upsert Pattern (Get-or-Create)

Always use idempotent upsert logic:

```python
obj = await client.get(kind="BusinessApplicationService", name__value="trading-app")
if not obj:
    obj = await client.create(kind="BusinessApplicationService", data={...})
    await obj.save()
```

Seed data lives in `packages/intent/snapl_intent/seed/` organised by use case.

## GraphQL Query Conventions

- Queries live in `packages/intent/snapl_intent/queries/`
- Use Infrahub's auto-generated GraphQL schema
- Namespace prefix on type names: `BusinessApplicationService`
- Always include `id` and `display_label` in query results

## Common Mistakes

- Writing synchronous HTTP calls instead of using the async SDK
- Missing schema dependency ordering (causes load failures)
- Generating invalid GraphQL queries (wrong type names, missing namespace prefix)
- Not using get-or-create for seed data (causes duplicates on re-run)
