# Changelog Management

We use **Towncrier** to manage the changelog. Each user-facing change gets a small fragment file.

## Creating a Fragment

```bash
# Format: changelog/<issue-number>.<type>.md
# If no issue number, use a short identifier: changelog/+<short-id>.<type>.md

echo "Added drift detection observer" > changelog/42.added.md
echo "Fixed gNMI collector timeout handling" > changelog/43.fixed.md
echo "Removed deprecated deploy stub" > changelog/+remove-stub.removed.md
```

## Fragment Types

| Type | Directory | Description |
|------|-----------|-------------|
| `added` | `changelog/*.added.md` | New features |
| `changed` | `changelog/*.changed.md` | Changes to existing features |
| `deprecated` | `changelog/*.deprecated.md` | Features marked for removal |
| `removed` | `changelog/*.removed.md` | Features removed |
| `fixed` | `changelog/*.fixed.md` | Bug fixes |
| `security` | `changelog/*.security.md` | Security-related changes |

## Building the Changelog

```bash
# Preview what the changelog will look like
uv run towncrier build --draft

# Build and update CHANGELOG.md (removes fragment files)
uv run towncrier build --version 0.2.0
```

## When to Add a Fragment

- Any change that affects users (new features, bug fixes, API changes)
- Any breaking change
- Security fixes

## When NOT to Add a Fragment

- Internal refactoring with no user-visible change
- CI/CD updates
- Documentation-only changes
- Test-only changes

## PR Enforcement

CI automatically blocks PRs that don't include a changelog fragment. If your PR doesn't need one (CI-only, docs-only, test-only, or internal refactoring), ask a maintainer to add the `skip-changelog` label to skip the check.

The check runs on every PR to `main`. It is automatically skipped for:

- Dependabot PRs
- PRs with the `skip-changelog` label
