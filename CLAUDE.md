# Claude Code Configuration

See [AGENTS.md](AGENTS.md) for full project context.

## Quick Reference
- Lint: `uv run invoke lint`
- Format: `uv run invoke format`
- Test: `uv run invoke test-unit`
- Start infra: `uv run invoke dev.deps`
- Start worker: `uv run invoke orchestrator.start`
- Branch from `main`, PR to `main`
- Conventional commits (feat:, fix:, docs:, etc.)

## Spec-Driven Development

This project uses GitHub Spec Kit. Before implementing any module:
1. Run `/speckit-specify` to create the specification
2. Run `/speckit-plan` to create the implementation plan
3. Run `/speckit-tasks` to generate tasks
4. Run `/speckit-implement` to execute

Constitution: `.specify/memory/constitution.md`

## Test-Driven Development

This project follows strict TDD. When generating code:
1. **Always produce the test file first.** If asked for a new module, generate `test_<module>.py` before `<module>.py`.
2. Follow Red-Green-Refactor: failing test -> minimum implementation -> refactor.
3. Never create a source file without its corresponding test file.
4. Use pytest fixtures from `tests/conftest.py` for shared test data.
5. Coverage target: >=80% on new code.

This is a hard rule, not a suggestion.

## NAF Building Blocks

Each package under `packages/` maps to one NAF block:
- `intent` — Source of Truth (Infrahub)
- `executor` — Config deployment (gNMI)
- `collector` — Live data retrieval
- `observability` — Drift detection, metrics
- `orchestrator` — Temporal workflows
- `presentation` — CLI interface

## Recent Changes
- 003-collector-gnmi: Added Python 3.12+ + pygnmi>=0.8, grpcio>=1.60, pydantic>=2.5, snapl-intent (workspace dep)
- 002-executor-gnmi: Added Python 3.12+ + pygnmi>=0.8, grpcio>=1.60, jinja2>=3.1, pydantic>=2.5, snapl-intent (workspace dep)
- 001-naf-intent-sot: Added Python 3.12+ + infrahub-sdk[ctl]>=1.0.0, pydantic>=2.5, httpx>=0.25, pyyaml>=6.0

## Active Technologies
- Python 3.12+ + pygnmi>=0.8, grpcio>=1.60, pydantic>=2.5, snapl-intent (workspace dep) (003-collector-gnmi)
- None — Collector is stateless and read-only; no persistence (003-collector-gnmi)
