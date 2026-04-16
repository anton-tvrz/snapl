# Intent Model — Business & Operational Intent Skill

## Metadata
- name: intent-model
- triggers: intent, lineage, business intent, operational override, connectivity, firewall rule, compliance
- project: snapl

## Overview

snapl's intent model creates data lineage from business need to device configuration. It is the core of the NAF Intent building block (`snapl_intent`). It has two parts:
1. **Business Intent Bridge** — Object chain from application owner to device config
2. **Operational Intent** — Time-bounded overrides with auto-reversion

## Business Intent Chain

```
ApplicationService -> ServiceEndpoint -> ConnectivityIntent -> InfrastructureBinding -> FirewallRuleSet
```

| Schema Object | Purpose | Key Attributes |
|---------------|---------|----------------|
| **ApplicationService** | Business-level service declaration | name, owner, environment, criticality |
| **ServiceEndpoint** | Protocol/port endpoint within a service | protocol, port, direction |
| **ConnectivityIntent** | Declared need for two endpoints to communicate | status, justification |
| **InfrastructureBinding** | Maps intent to specific infrastructure | binding_type |
| **FirewallRuleSet** | Device-level configuration | rule_name, action, source/dest network |

## Data Lineage Queries

**Forward (provision):** ApplicationService -> ... -> Device Config
**Reverse (audit):** Device Rule -> ... -> Business Owner

Given any device rule, you can trace it back to the business owner who requested it.

## Operational Intent

| Schema Object | Purpose |
|---------------|---------|
| **OperationalOverride** | Time-bounded deviation from as-built intent |
| **OverrideWindow** | Time bounds (start, end, auto_revert) |
| **OverrideAction** | Specific config change (original_state, override_state) |

## Override-Aware Drift Detection

When drift is detected on a device with an active OperationalOverride:
- Expected state = override state (NOT as-built state)
- Query: "Is the device in the state its active override says it should be?"

## Common Mistakes

- Creating device rules without intent lineage (orphaned rules)
- Not checking for active overrides before flagging drift
- Missing the reverse lineage path (audit queries need this)
- Not setting override end_time (overrides should always be time-bounded)
