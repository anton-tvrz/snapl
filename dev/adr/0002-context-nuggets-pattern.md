# ADR-0002: Context Nuggets Pattern for Developer Documentation

## Status

Accepted

## Date

2026-04-14

## Context

This project uses AI coding agents (Claude Code, GitHub Copilot, etc.) alongside human developers. Both audiences need easy access to project context — architecture decisions, coding standards, step-by-step procedures, and domain knowledge.

Traditional documentation (long READMEs, wikis) doesn't work well for AI agents because:

- Large monolithic docs exceed context windows
- AI agents need focused, task-specific context
- Human developers need different levels of detail than AI agents

The Infrahub project (opsmill/infrahub) pioneered the "Context Nuggets" pattern that solves this by organizing documentation into small, focused files organized by purpose and audience. This pattern was previously adopted in project-network-synapse-quattro and proved effective for both human and AI developers.

## Decision

We adopt the Context Nuggets pattern, organizing developer documentation under `dev/` with these directories:

| Directory         | Purpose                             | Primary Audience |
| ----------------- | ----------------------------------- | ---------------- |
| `dev/adr/`        | Architecture Decision Records       | Human + AI       |
| `dev/commands/`   | Reusable AI agent command workflows | AI agents        |
| `dev/guidelines/` | Coding standards and conventions    | Human + AI       |
| `dev/guides/`     | Step-by-step procedures             | Human + AI       |
| `dev/knowledge/`  | Architecture explanations by domain | Human + AI       |
| `dev/prompts/`    | Prompt templates for thinking tasks | Human            |
| `dev/skills/`     | Domain-specific AI agent skills     | AI agents        |

`AGENTS.md` at the project root serves as the entry point and knowledge map for AI agents. `.claude/commands` and `.claude/skills` symlink to `dev/commands` and `dev/skills`.

## Consequences

### Positive

- AI agents get focused, relevant context for each task
- Human developers have organized, discoverable documentation
- Single source of truth — no duplication between human and AI docs
- Easy to maintain — small files are simpler to update
- Follows proven patterns from production-grade open-source projects
- Ported from quattro with lessons learned, ensuring consistency across projects

### Negative

- More files to maintain than a single README
- Requires discipline to keep docs updated as the project evolves
- New contributors need to understand the directory structure
