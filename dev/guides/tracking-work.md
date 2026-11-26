# Tracking Work — Issues & Project Board

## Quick Reference

```bash
# List open issues
gh issue list --repo anton-tvrz/snapl

# Create a new issue
gh issue create --repo anton-tvrz/snapl --title "fix(orchestrator): ..." --label bug --label orchestrator

# Only if an issue is missing from the board (see "Adding issues to the board")
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

### Adding issues to the board

The board's built-in **Auto-add to project** workflow adds snapl's issues and PRs as they are opened. It is configured on the board under **⋯ → Workflows → Auto-add to project**:
- repository: `anton-tvrz/snapl`
- filter: `is:issue,pr is:open`

The **Item closed** workflow moves closed items to Done.

Auto-add only acts on items created or updated after it was turned on. An older issue that is missing from the board needs adding by hand: from the issue's sidebar (**Projects → snapl**), or with `gh project item-add` (see Quick Reference). That command needs a token with the `project` scope (`gh auth refresh -s project`).

If an issue you just filed doesn't appear on the board within a minute, check that the workflow is still on before adding it by hand. A free account gets one auto-add workflow per project.

## Labels

Two label families are in use:

- **NAF block** — `intent`, `executor`, `collector`, `observability`, `orchestrator`, `presentation` (matches `packages/*`)
- **Type** — `bug`, `enhancement`, `documentation`, `infrastructure`, `tests`, `config`, `ci`, `triage`

Apply one NAF-block label (where applicable) plus one or more type labels when filing an issue.

One PR-only label exists besides these: `skip-changelog`. PR validation requires a `changelog/<issue>.<type>.md` fragment on every PR; for changes that don't warrant one (docs-only, CI-only, test-only), add the `skip-changelog` label to the PR instead.

## When to File an Issue vs. Run the SDD Workflow

- **New module or feature** → follow the full SDD flow (`/speckit-specify` → `/speckit-plan` → `/speckit-tasks` → `/speckit-implement`); the spec is the source of truth. Every PR still needs an issue to close, because PR validation requires `Closes #N`. Give the spec PR and each implementation PR its own sub-issue under the feature's umbrella issue, as #148 (spec 010) and #150 (spec 010 PR A) do under #101, so the umbrella stays open until the last PR lands.
- **Bug, review finding, or small chore surfaced outside the spec workflow** (e.g. a live-run failure, a code review comment, a hardening task) → file a `gh issue create` directly. Auto-add puts it on the board. No spec needed for these.
