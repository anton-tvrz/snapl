# Shared Instructions for All Commands

Before starting any task:

1. **Read AGENTS.md** for project context and conventions
2. **Check `dev/guidelines/`** for coding standards relevant to your task
3. **Read the relevant `dev/knowledge/` files** for architecture understanding
4. **Follow the git workflow** in `dev/guidelines/git-workflow.md`

After completing any task:

1. **Run linting:** `uv run invoke lint`
2. **Run formatting:** `uv run invoke format`
3. **Run relevant tests:** `uv run invoke test-unit`
4. **Add a changelog fragment** if the change is user-facing (see `dev/guidelines/changelog.md`)
