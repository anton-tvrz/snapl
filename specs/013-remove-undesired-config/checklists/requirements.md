# Specification Quality Checklist: Remove Undesired Config

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-11-15
**Feature**: [spec.md](../spec.md)

## Content Quality

- [ ] No implementation details (languages, frameworks, APIs)
- [x] Focused on user value and business needs
- [x] Written for non-technical stakeholders
- [x] All mandatory sections completed

## Requirement Completeness

- [x] No [NEEDS CLARIFICATION] markers remain
- [x] Requirements are testable and unambiguous
- [x] Success criteria are measurable
- [ ] Success criteria are technology-agnostic (no implementation details)
- [x] All acceptance scenarios are defined
- [x] Edge cases are identified
- [x] Scope is clearly bounded
- [x] Dependencies and assumptions identified

## Feature Readiness

- [x] All functional requirements have clear acceptance criteria
- [x] User scenarios cover primary flows
- [x] Feature meets measurable outcomes defined in Success Criteria
- [ ] No implementation details leak into specification

## Notes

- **gNMI paths are named on purpose.** The spec names gNMI `delete`, Set requests and entity paths. That is deliberate, as in spec 007: the safety of this feature is about which paths are written, and the constitution (VIII.5) and spec 007 FR-011 already state the rule in those terms. The three unchecked "no implementation details" items fail for that reason only.
- **Q1 to Q3 were answered on 2026-11-15: the proposed default for each** (see the spec's Clarifications). The reasons are kept below.
  - **Q1, the entry point. Proposed: a new command.** `snapl remove <device>` previews by default, and `--apply` executes. Reconcile stays a pure merge. It is idempotent and safe to retry (VIII.4), and the scheduler uses it; folding deletes into it would change both.
  - **Q2, the first cut's scope. Proposed: (a).** Whole undesired entities, plus extra addresses on intent-managed interfaces. Fields removed from intent on an entity that intent still manages need the renderer and the diff to agree on "unset means remove", which today they deliberately do not (spec 007 `skip_when_none`, #84).
  - **Q3, the granularity. Proposed: all or nothing per device.** It matches FR-008's single Set request and keeps the confirmed plan equal to the executed one. Picking a subset can come later, as a filter on the plan.
- **Not committed to a branch of its own.** It sits on `ccr-a14a9b05-ogrz9i` and should move to `013-remove-undesired-config` for its PR.
- **`.specify/feature.json` is unchanged.** It still points at `specs/011-dashboard-ui`. Run later Spec Kit steps with `SPECIFY_FEATURE_DIRECTORY=specs/013-remove-undesired-config`.
