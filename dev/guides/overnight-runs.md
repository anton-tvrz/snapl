# Overnight Runs

An unattended agent works through the active spec's `tasks.md` one PR slice at a time. For each slice it implements the tasks, opens the PR, merges it once CI is green, and starts the next. The maintainer reads the report in the morning.

**Authorization**: the maintainer has authorized overnight runs to merge their own PRs once CI is green (2026-11-07). This covers PRs on `overnight/` branches only.

## Starting a run (maintainer)

Run `/fewer-permission-prompts` once beforehand. A permission prompt stalls the run until morning. Then, in a dedicated terminal in the main checkout:

```bash
caffeinate -i &
claude
/loop Do one overnight pass: follow dev/guides/overnight-runs.md.
```

Start `claude` in the main checkout, not in the worktree. Memory is keyed by that path.

## Guardrails

- **Worktree**: all work happens in `../snapl-overnight`. The main checkout belongs to the maintainer: another session's worker runs from it, and untracked work lives there. The shell's working directory resets between calls, so prefix each command with `cd ../snapl-overnight &&`.
- **Staging**: stage files by path. `git add -A` sweeps in untracked files that are not yours.
- **Lab**: the lab devices, the Docker stack, Temporal schedules and running workers belong to the maintainer. Test servers that your own tests start (`WorkflowEnvironment`) are fine. Tasks marked **Needs the lab** are deferred (see Judgement calls).
- **History**: add commits, and refresh a branch with `gh pr update-branch`. History is append-only: no force-push and no `--admin` merges.
- **TDD**: per `CLAUDE.md`, with each test seen failing for the expected reason before its code exists.

## One pass

Each loop iteration reads the state, runs exactly one branch below, and then schedules its wakeup.

### 1. Read the state

- **Worktree**: if `../snapl-overnight` is missing, run `git worktree add --detach ../snapl-overnight origin/main`, then `uv sync --all-groups` inside it. Then `git -C ../snapl-overnight fetch -q origin`.
- **Open overnight PR**: `gh pr list --state open --json number,headRefName,headRefOid --jq '.[] | select(.headRefName | startswith("overnight/"))'`.
- **Report**: `.snapl/overnight/<UTC date>.md` in the main checkout. The folder is git-ignored. Create the file if it is missing.

Done when you know whether an overnight PR is open and, if so, the state of its CI.

### 2a. An overnight PR is open

- **CI running**: wake in about 8 minutes.
- **CI red**: read the failure (`gh run view <run> --log-failed`), fix it in a new commit, and push. Log the attempt in the report. If the same check goes red for the third time, stop the run (see Stopping).
- **Behind `main`**: `gh pr update-branch <n>`, then wait for CI.
- **Green**: every check passes, or reports `skipping` because of a path filter, and the branch is up to date. Merge with `gh pr merge <n> --squash --delete-branch --match-head-commit <full sha>`. GitHub rejects a short sha. Confirm the PR is `MERGED` and its issue is closed, then log the merge.

Done when the PR is merged, a fix is pushed, or the wakeup is scheduled.

### 2b. No overnight PR is open: build the next slice

1. **Find the slice**: `.specify/feature.json` names the spec. In its `tasks.md`, take the first slice in the "PR slicing" list that still has an unticked task not marked **Needs the lab**. If there is none, write the final report and stop.
2. **Issue**: create it per `dev/guides/tracking-work.md`, with labels and the board. Title it `feat(<block>): spec <NNN> PR <X>, <goal>`, and say it is part of the spec's umbrella issue.
3. **Branch**: `overnight/<NNN>-<x>-<slug>`, from a fresh `origin/main` in the worktree.
4. **Implement** the slice's tasks in order, tests first. Tick each task in `tasks.md` once its tests pass.
5. **Gate** locally on everything CI runs. `invoke lint` is ruff only, and CI also runs mypy and bandit:
   ```bash
   uv run invoke lint && uv run mypy packages/ && uv run bandit -r packages/ -c pyproject.toml -q && uv run pytest tests/unit -q
   ```
6. **Ship**: commit (conventional, ending `(#<issue>)`), push, and open the PR. Model its body on #154:
   - `Closes #<issue>` and `Refs` the umbrella issue
   - What Changed
   - How to Review, listing every deviation from `tasks.md`
   - the TDD checklist
   - the slice's changelog fragment, as `tasks.md` specifies

Done when every task in the slice that doesn't need the lab is ticked, the local gate is green, and the PR is open.

## Judgement calls

- **Lab tasks**: leave them unticked. List them under "Deferred to the lab" in the PR body and the report, and carry on. The slice still merges.
- **Spec against code**: when the code shows a fact in the spec is wrong and the correction is clear, amend the spec in the same PR and list the change under "Spec amendments". When the answer is a product decision, mark the PR as a draft, write the question in the report, and stop.
- **Unexpected state**: for example, uncommitted changes in the worktree you did not make, or an `overnight/` PR whose history you do not recognise. Stop and describe it.

## The report

At every merge, deferral, question or stop, append to `.snapl/overnight/<UTC date>.md`: the time, the PR, what happened, and any deferred lab tasks. The last entry says why the run stopped.

## Stopping

End the loop when:
- every slice is done
- one check has gone red three times
- a product decision is needed
- continuing would cross a guardrail

The report is the last thing written.
