# Fix Bug Workflow

Follow these steps to diagnose and fix a bug:

## 1. Write a Failing Test That Reproduces the Bug
- Read the bug report or issue description
- Identify the expected vs. actual behavior
- Determine which package is affected (check `packages/`)
- Write a test in `tests/unit/` that demonstrates the bug (expected behaviour vs. actual)
- Run: `uv run invoke test-unit` to confirm the test **fails** — this proves the bug exists

## 2. Fix the Code to Make the Test Pass
- Read the relevant source code in `packages/<block>/snapl_<block>/`
- Check `dev/knowledge/` for architecture context
- Make the minimal change to fix the bug
- Follow coding standards in `dev/guidelines/python.md`
- Run the failing test again — it should now **pass**

## 3. Verify No Regressions
- Run full test suite: `uv run invoke test-unit`
- Run linter: `uv run invoke lint`
- Ensure no other tests were broken by the fix

## 4. Document
- Add changelog fragment: `echo "Fixed <description>" > changelog/<issue>.fixed.md`

## 5. Commit
- Branch: `fix/<short-description>`
- Commit message: `fix: <description> (#<issue>)`
