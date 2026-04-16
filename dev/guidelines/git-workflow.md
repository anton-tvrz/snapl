# Git Workflow

## Branch Strategy

| Branch | Purpose                                               | Merges From          |
| ------ | ----------------------------------------------------- | -------------------- |
| `main` | Production-ready code. Protected — no direct commits. | Feature/fix branches |

All feature work branches from `main` and merges back to `main` via Pull Request.

## Branch Naming

| Pattern                  | Use Case                 | Example                        |
| ------------------------ | ------------------------ | ------------------------------ |
| `feature/<description>`  | New features             | `feature/drift-detection`      |
| `fix/<description>`      | Bug fixes                | `fix/gnmi-timeout`             |
| `dev/<description>`      | Infrastructure / tooling | `dev/ci-pipeline-update`       |
| `docs/<description>`     | Documentation only       | `docs/add-runbook`             |
| `refactor/<description>` | Code refactoring         | `refactor/executor-abc`        |

## Commit Conventions

Use **Conventional Commits** format:

```
<type>: <short description>

[optional body]

[optional footer]
```

### Types

| Type       | When to Use                             |
| ---------- | --------------------------------------- |
| `feat`     | New feature or capability               |
| `fix`      | Bug fix                                 |
| `docs`     | Documentation only                      |
| `style`    | Formatting, whitespace (no code change) |
| `refactor` | Code restructuring (no behavior change) |
| `test`     | Adding or updating tests                |
| `chore`    | Build, CI, tooling changes              |
| `perf`     | Performance improvement                 |

### Examples

```bash
feat: add drift detection observer
fix: handle timeout in gNMI collector (#42)
docs: add NAF framework knowledge doc
chore: migrate from black to ruff
test: add unit tests for intent store
```

## Pull Request Process

1. Create branch from `main`
2. Make changes, commit with conventional commit messages
3. Run `uv run invoke check-all` — must pass before opening PR
4. Rebase on `origin/main` to resolve conflicts
5. Push branch and open PR targeting `main`
6. Fill out PR template (Why, What changed, How to review, How to test)
7. CI must pass (lint, tests, security scan)
8. **CodeRabbit will automatically post an AI review** — address actionable comments before requesting human review
9. Request review — at least 1 approval required
10. Address all review comments
11. Merge via **squash merge** (keeps history clean)
12. Delete the feature branch after merge

### PR Checklist — TDD Items

- [ ] Tests written BEFORE implementation (TDD)
- [ ] All new code has corresponding test file
- [ ] Coverage meets 80% threshold on new code
- [ ] Bug fixes include a failing test that reproduces the bug

### Key Rules

- **Keep PRs small** — aim for < 400 lines changed
- **One feature/fix per PR** — don't combine unrelated changes
- **PR title uses Conventional Commits** — this becomes the merge commit message
- **Don't force-push during review** — it destroys comment context

> For comprehensive guidance, see [Pull Request Best Practices](../guides/pull-request-best-practices.md).

## Release Process

Releases are automated via the `Release` workflow (`.github/workflows/release.yml`).

### How to create a release

1. Ensure all features for the release are merged to `main`
2. Go to **Actions -> Release -> Run workflow**
3. Enter the version number (e.g., `0.2.0`)
4. The workflow automatically:
   - Validates all closed issues have changelog fragments
   - Compiles fragments into `CHANGELOG.md` via Towncrier
   - Commits the updated changelog to `main`
   - Creates and pushes a git tag (`v0.2.0`)
   - Creates a GitHub Release with the generated notes
   - Triggers the build-artifacts workflow (Docker + Python packages)

### Completeness validation

Before publishing, the workflow checks that every issue closed since the last release tag has a corresponding `changelog/<issue>.*.md` fragment. Issues labeled `duplicate`, `wontfix`, `question`, `invalid`, or `skip-changelog` are excluded from this check.

If issues are missing fragments, the workflow fails with a list of gaps. Fix by adding the missing fragments or labeling the issues with `skip-changelog`.

### Emergency releases

Use the `skip-validation` checkbox when triggering the workflow to bypass the completeness check. Use sparingly.

### One-time admin setup

The workflow commits directly to `main`. Two things are needed:

1. In **Settings -> Rules -> Rulesets -> "Protect main"** -> Bypass list, add **"Repository admin"** role (set to "Always")
2. Create a Fine-grained PAT (Contents: Read/Write, scoped to this repo) and store it as the **`RELEASE_PAT`** secret in Settings -> Secrets -> Actions

## Issue Lifecycle

Every issue follows this progression:

| Stage           | Action                                                                          |
| --------------- | ------------------------------------------------------------------------------- |
| **Created**     | Assignee set, label set, `## Sub-tasks` and `## Acceptance Criteria` filled out |
| **In Progress** | Branch created matching the `## Branch` field in the issue body                 |
| **In Review**   | PR opened with `Closes #N` in the body — links the PR to the issue              |
| **Done**        | PR merged -> issue auto-closed by GitHub                                        |

As each sub-task is completed, **edit the issue body and tick the checkbox** so progress is visible without reading the commit history.

> For full details, see [Issue Management](./issue-management.md).

## Git Hooks

Local hooks enforce branch protection rules before code leaves your machine.

### Pre-commit hooks (via `pre-commit`)

Installed with `uv run pre-commit install`. Runs on every commit:

- Linting and formatting (ruff)
- Secret detection (detect-secrets, gitleaks)
- **Branch protection** — blocks commits directly to `main`

### Pre-push hook

Blocks `git push` directly to `main`. Install with:

```bash
ln -sf ../../.githooks/pre-push .git/hooks/pre-push
```

The hook is stored in `.githooks/pre-push` (version-controlled). It ensures all changes go through Pull Requests, even if GitHub branch protection is misconfigured.

## Agent-Specific Rules

> **AI agents MUST always use this workflow. No exceptions.**

1. Create a feature branch: `git checkout -b feat/<description> main`
2. Make changes and run `uv run invoke check-all`
3. Commit with Conventional Commits format
4. Push and open a PR: `gh pr create --base main`
5. **Wait for CodeRabbit AI review** — address any flagged issues before requesting human review
6. Wait for CI to pass and PR to be merged
7. **Do NOT push directly to `main`.** Branch protection will reject it.
