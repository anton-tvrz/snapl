# Issue Management

This document defines how GitHub issues are created, managed, and closed throughout the development lifecycle.

## Issue Anatomy

Every issue must have the following fields populated before work begins:

| Field                   | Required       | Notes                                                                        |
| ----------------------- | -------------- | ---------------------------------------------------------------------------- |
| **Assignee**            | Yes            | The person / agent responsible for delivering this issue                     |
| **Label**               | Yes            | At minimum one of: `enhancement`, `bug`, `task`, `security`, `observability` |
| **Milestone**           | Recommended    | Group issues into delivery milestones (e.g. v0.1.0, v0.2.0)                 |
| **Branch**              | Yes            | The branch name to be created for this work (in issue body)                  |
| **Sub-tasks**           | Yes            | Checkbox list of concrete steps in the `## Sub-tasks` section                |
| **Acceptance Criteria** | Yes            | Definition of done in `## Acceptance Criteria` section                       |

## Issue Body Template

All issues should follow this structure:

```markdown
## Description

<1-3 sentences describing the problem or feature>

## Sub-tasks

- [ ] Step 1
- [ ] Step 2
- [ ] Step 3

## Acceptance Criteria

- [ ] Criterion 1 (testable, observable)
- [ ] Criterion 2

## Branch

`feature/<short-description>`
```

> Use `feature/` for product features, `fix/` for bugs, `dev/` for infrastructure/tooling, `docs/` for documentation.

## Issue Lifecycle

```text
Created -> Assigned -> In Progress -> In Review -> Done
```

| Stage           | What happens                                                                         |
| --------------- | ------------------------------------------------------------------------------------ |
| **Created**     | Issue body filled out, assignee set, label set, milestone set                        |
| **In Progress** | Branch created (`git checkout -b <branch> main`), sub-tasks checked as work lands |
| **In Review**   | PR opened with `Closes #N` in the body — links PR to issue on GitHub                 |
| **Done**        | PR merged -> issue **auto-closed** by GitHub via `Closes #N`                         |

## Bug Management Lifecycle

Bugs require strict tracking to ensure they are diagnosed, assigned, and fixed properly.

1. **Submission**: All bugs must be submitted using the `Bug Report` issue template (`.github/ISSUE_TEMPLATE/bug_report.yml`).
2. **Triage**: A GitHub action will automatically tag new bug reports with the `triage` label.
3. **Investigation**: A maintainer will investigate the issue. If valid, the `triage` label will be swapped for the `bug` label, an assignee will be set, and Sub-tasks will be defined.
4. **Resolution**: The assigned developer opens a PR using the `fix/...` branch naming convention.
5. **Closure**: The PR description MUST use the exact keyword `Closes #<bug_issue_number>`. Our PR linting automation will enforce this keyword to ensure the bug is not left open after merge.

## Linking a PR to an Issue

In the PR body, always include:

```text
Closes #<issue-number>
```

This causes GitHub to:

- Show the linked PR on the issue sidebar
- Auto-close the issue when the PR merges to the target branch

## Tracking Sub-task Progress

As you commit work that completes a sub-task, edit the issue body and tick the checkbox:

```markdown
- [x] Step 1 <- done
- [ ] Step 2 <- still pending
```

This gives anyone viewing the issue a real-time progress picture without needing to read commit history.

## Automated Guardrails

The **Issue Automation** workflow (`.github/workflows/issue-automation.yml`) enforces the rule that issues must only be closed via PR merge:

- Triggers whenever an issue is closed.
- Checks for any open (unmerged) PRs that reference the issue with `Closes #N` / `Fixes #N` / `Resolves #N`.
- If an unmerged PR is found, the workflow **reopens the issue** and leaves a comment explaining why.

This prevents accidental or premature manual closures.

## Agent Rules

> **AI agents MUST follow these rules when working on issues.**

1. **Never start work without an assignee** — add yourself or `anton-tvrz` if no human is assigned.
2. **Always create a branch** matching the `## Branch` field in the issue body.
3. **Check off sub-tasks** in the issue body as each one is completed.
4. **Always open a PR with `Closes #N`** to link work back to the issue.
5. **Never close an issue manually** — let the PR merge trigger auto-close.
6. **Update `## Acceptance Criteria`** if scope changes during implementation; don't silently deviate.
