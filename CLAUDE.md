# Claude Code Configuration

See [AGENTS.md](AGENTS.md) for full project context.

## Quick Reference
- Lint: `uv run invoke lint`
- Format: `uv run invoke format`
- Test: `uv run invoke test-unit`
- Live tests (need the lab): `uv run invoke test-e2e`, `uv run invoke test-integration`. Each run is recorded under `.snapl/test-runs/`
- Start infra: `uv run invoke dev.deps`
- Start worker: `uv run invoke orchestrator.start`
- Branch from `main`, PR to `main`
- Conventional commits (feat:, fix:, docs:, etc.)
- Issues + project board: see `dev/guides/tracking-work.md` (board: https://github.com/users/anton-tvrz/projects/4)

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
- `observability` — Drift detection, audit (metrics are recorded by `orchestrator` and served by `presentation`, spec 012)
- `orchestrator` — Temporal workflows
- `presentation` — CLI interface

## Recent Changes
- 011-dashboard-ui: the designed dashboard UI in `packages/presentation/ui/` (React 19, Vite 8, TypeScript 5.9, Tailwind 4, Recharts; Vitest and Playwright), built by `invoke dashboard.build` into the package, served at `/` with the plain page at `/plain`; `snapl dashboard --archive` and `--snapshot`; see ADR-0005 Decision 2 and `dev/skills/dashboard/`
- 012-measured-metrics: Prometheus v3.10.0 in Compose (data in `.snapl/prometheus`, kept by `demo.reset`), worker metrics via temporalio's runtime on 18091 (no new Python deps), dashboard `/api/v1/metrics/*` and `/metrics`, `invoke history.archive`/`history.purge`; see ADR-0005 Decision 3
- 010-dashboard-read-api: `snapl dashboard`, a local read-only page and JSON API (`/api/v1/*`); fastapi>=0.115, uvicorn>=0.32, httpx>=0.28 in snapl-presentation; see ADR-0005
- 004-observability-drift: Added Python 3.12+ + pydantic>=2.5, snapl-intent (workspace dep), snapl-collector (workspace dep)
- 003-collector-gnmi: Added Python 3.12+ + pygnmi>=0.8, grpcio>=1.60, pydantic>=2.5, snapl-intent (workspace dep)
- 002-executor-gnmi: Added Python 3.12+ + pygnmi>=0.8, grpcio>=1.60, jinja2>=3.1, pydantic>=2.5, snapl-intent (workspace dep)

## Active Technologies
- Python 3.12+ + pydantic>=2.5, snapl-intent (workspace dep), snapl-collector (workspace dep) (004-observability-drift)
- In-memory `AuditLog` for this iteration; durable persistence is out of scope and deferred to the Orchestrator (004-observability-drift)
