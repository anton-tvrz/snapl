## Why

<!-- What problem does this solve? Link to issue if applicable. -->

Closes #<issue-number> <!-- REQUIRED: CI will fail without a linked issue -->

## What Changed

<!-- Describe the changes. What behavior is different? -->

-

## How to Review

<!-- Key files to focus on, risky areas, alternatives considered. -->

-

## How to Test

<!-- Runnable commands and expected output. -->

```bash
uv run pytest tests/unit/ -v
```

## Impact & Rollout

- [ ] Backward compatible
- [ ] No new dependencies
- [ ] No config changes required

## TDD Checklist

- [ ] Tests written before implementation (TDD)
- [ ] All new source files have corresponding test files
- [ ] Failing test demonstrated before fix (bug fixes only)
- [ ] Coverage threshold met on new code (>=80%)

## Checklist

- [ ] Tests added/updated
- [ ] Linting passes (`uv run ruff check .`)
- [ ] Changelog fragment added (if user-facing change)
- [ ] Documentation updated (if applicable)
