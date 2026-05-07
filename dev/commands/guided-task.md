# Guided Task Workflow

A general-purpose workflow for implementing any task.

## 1. Identify the Test File
- Read the task description carefully
- Identify which packages and files will be affected
- Check `dev/knowledge/` for relevant architecture docs
- Determine the correct test file location (see `AGENTS.md` Workspace Architecture)

## 2. Write Failing Tests
- Create the test file in `tests/unit/` or `tests/integration/`
- Write tests that define the expected behaviour of the new code
- Run tests to confirm they **fail** (RED): `uv run pytest tests/unit/test_<module>.py -x`
- If tests pass without implementation, the tests are not testing the right thing

## 3. Implement
- Write the minimum code to make all tests pass (GREEN)
- Follow `dev/guidelines/python.md` coding standards
- Keep commits small and focused

## 4. Verify Green
- Run the test file: `uv run pytest tests/unit/test_<module>.py -v`
- All tests must pass
- Run full suite to check for regressions: `uv run invoke test-unit`

## 5. Refactor
- Improve code while keeping tests green
- Extract shared logic, add type hints, simplify
- Re-run tests after each refactor step

## 6. Quality Checks
- `uv run invoke format` — Format code
- `uv run invoke lint` — Lint code
- `uv run invoke scan` — Security scan (if applicable)

## 7. Document
- Update docstrings for new/modified functions
- Add changelog fragment if user-facing
- Update `dev/knowledge/` docs if architecture changed

## 8. Commit
- Use conventional commit messages
- Reference issue numbers where applicable
