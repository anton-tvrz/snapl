# SDD Workflow — Spec-Driven Development

## Overview

snapl follows Spec-Driven Development (SDD), a methodology where every module is fully specified before implementation begins. SDD ensures that design decisions are deliberate, documented, and reviewable before any code is written.

The SDD workflow has five phases:

```
Constitution -> Specify -> Plan -> Tasks -> Implement
```

## Phase 1: Constitution

The **constitution** is the governing document for the entire project. It defines:
- Project purpose and scope
- Architectural principles
- Quality standards
- Technology choices
- Constraints and non-negotiables

Location: `.specify/memory/constitution.md`

The constitution is written once and updated rarely. All specifications must align with it.

**Command:** `/speckit-constitution`

## Phase 2: Specify

A **specification** defines what a module or feature must do, without describing how. It captures:
- **Purpose** — the problem being solved
- **Interface** — public API (ABCs, methods, models)
- **Behavior** — expected inputs, outputs, and edge cases
- **Dependencies** — what the module depends on and what depends on it
- **Constraints** — performance, security, compatibility requirements
- **Acceptance Criteria** — testable conditions for "done"

Location: `.specify/specs/<block>/<module-name>.md`

**Key principle:** The specification should be detailed enough that two independent developers would produce functionally equivalent implementations.

**Command:** `/speckit-specify`

## Phase 3: Plan

The **plan** translates a specification into a concrete implementation strategy:
- File locations for ABC, models, and implementation
- Test file locations and test strategy
- Dependency changes (new third-party packages)
- Integration points with other NAF blocks
- Migration steps (if changing existing code)
- Risk assessment and mitigation

Location: `.specify/specs/<block>/<module-name>-plan.md`

**Command:** `/speckit-plan`

## Phase 4: Tasks

**Tasks** break the plan into atomic, actionable work items:
- Each task is a single, focused unit of work
- Tasks are ordered by dependency (what must be done first)
- Each task has clear completion criteria
- Tasks map to individual commits or small PRs

Location: Generated from the plan, tracked in GitHub issues or task lists.

**Command:** `/speckit-tasks`

## Phase 5: Implement

**Implementation** follows TDD within each task:
1. Write a failing test that defines the expected behavior (RED)
2. Write the minimum code to make the test pass (GREEN)
3. Refactor while keeping tests green (REFACTOR)
4. Run quality checks (lint, format, scan)
5. Commit with a conventional commit message

**Command:** `/speckit-implement`

## Cross-Artifact Consistency

Use `/speckit-analyze` to verify consistency across all SDD artifacts:
- Does the implementation match the specification?
- Does the plan cover all specification requirements?
- Are tasks complete and properly ordered?
- Do tests cover the acceptance criteria?

## SDD Directory Structure

```
.specify/
├── memory/
│   └── constitution.md          # Project constitution (governing document)
├── specs/
│   ├── intent/
│   │   ├── schema-loader.md     # Specification
│   │   └── schema-loader-plan.md # Implementation plan
│   ├── executor/
│   │   ├── gnmi-deploy.md
│   │   └── gnmi-deploy-plan.md
│   └── ...
├── scripts/                     # Automation scripts
└── templates/                   # Spec/plan/task templates
```

## When to Use SDD

| Scenario | Use SDD? | Reason |
|----------|----------|--------|
| New NAF module | Yes | Needs ABC design, interface definition |
| New use case (e.g., WAN) | Yes | Needs specification of how it maps to existing blocks |
| Bug fix | No | Use the fix-bug workflow instead |
| Small refactor | No | Use guided-task workflow |
| New feature within existing module | Depends | Use SDD if it changes the public interface |
| Infrastructure/CI change | No | Use guided-task workflow |

## Relationship to TDD

SDD and TDD are complementary:
- **SDD** defines *what* to build (specification-level)
- **TDD** defines *how* to verify it works (implementation-level)
- The specification's acceptance criteria become the basis for test cases
- TDD operates within the Implement phase of SDD

```
SDD: Constitution -> Specify -> Plan -> Tasks -> Implement
                                                      |
TDD:                                            RED -> GREEN -> REFACTOR
```
