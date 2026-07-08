# Tracking Work — Issues & Project Board

## Quick Reference

```bash
# List open issues
gh issue list --repo anton-tvrz/snapl

# Create a new issue
gh issue create --repo anton-tvrz/snapl --title "fix(orchestrator): ..." --label bug --label orchestrator

# Add an issue to the project board (not automatic — do this for every new issue)
gh project item-add 4 --owner anton-tvrz --url https://github.com/anton-tvrz/snapl/issues/<N>
```

## Project Board

Work is tracked on the **snapl** board: https://github.com/users/anton-tvrz/projects/4

It's a user-level GitHub Project (owned by `anton-tvrz`, not repo-scoped) with the default fields plus a single-select `Status`:

| Status | Meaning |
|--------|---------|
| Todo | Not started |
| In Progress | Actively being worked (GitHub also auto-flips this when a linked PR is opened) |
| Done | Closed |

GitHub does **not** auto-add new repo issues to a user-level project. Every new issue must be added explicitly with `gh project item-add` (see Quick Reference) — otherwise it exists in the repo but is invisible on the board.

## Labels

Two label families are in use:

- **NAF block** — `intent`, `executor`, `collector`, `observability`, `orchestrator`, `presentation` (matches `packages/*`)
- **Type** — `bug`, `enhancement`, `documentation`, `infrastructure`, `tests`, `config`, `ci`, `triage`

Apply one NAF-block label (where applicable) plus one or more type labels when filing an issue.

One PR-only label exists besides these: `skip-changelog`. PR validation requires a `changelog/<issue>.<type>.md` fragment on every PR; for changes that don't warrant one (docs-only, CI-only, test-only), add the `skip-changelog` label to the PR instead.

## When to File an Issue vs. Run the SDD Workflow

- **New module or feature** → follow the full SDD flow (`/speckit-specify` → `/speckit-plan` → `/speckit-tasks` → `/speckit-implement`); the spec is the source of truth, an issue is optional bookkeeping around it.
- **Bug, review finding, or small chore surfaced outside the spec workflow** (e.g. a live-run failure, a code review comment, a hardening task) → file a `gh issue create` directly and add it to the board. No spec needed for these.
